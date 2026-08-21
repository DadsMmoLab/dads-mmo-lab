"""Shared subprocess streaming wrapper.

Provides line-by-line stdout streaming so the UI can display install/build/
container output without buffering the whole run. Stderr is drained
concurrently on a background thread — this exists purely to avoid pipe-buffer
deadlock (a chatty stderr filling its OS pipe buffer while nobody reads it),
not to interleave stderr into the stream in real time: stderr lines are
yielded only after the process exits and stdout is exhausted. See `stream()`'s
docstring for the exact ordering.
"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import threading
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path

from yulon.log import get_logger

logger = get_logger(__name__)

# How long to wait for a terminated/killed child and its stderr-reader thread
# to actually finish, when a `stream()` generator is abandoned early.
_SHUTDOWN_TIMEOUT_SECONDS = 5.0


def _cwd_arg(cwd: Path | None) -> str | None:
    """Convert an optional Path working dir to the str `subprocess` expects."""
    return str(cwd) if cwd is not None else None


def stream(command: list[str], cwd: Path | None = None) -> Iterator[str]:
    """Run a command, yielding stdout lines live and stderr lines at the end.

    Stdout lines are yielded one at a time as they arrive. Stderr is **not**
    interleaved in real time — it is drained on a background thread purely to
    prevent a full pipe buffer from deadlocking the child, then yielded in one
    block after stdout is exhausted and the process has exited. Each yielded
    line has its trailing newline removed.

    If the caller abandons the generator before it's exhausted (e.g. `break`s
    out of a `for line in stream(...):` loop, or the generator is garbage
    collected), the child process is terminated (escalating to `kill()` after
    a timeout) and the stderr-reader thread is joined before the generator
    finishes unwinding — the child and thread are never silently orphaned.

    Args:
        command: The argv list to execute (no shell interpolation).
        cwd: Optional working directory for the child process.

    Yields:
        Each output line (all of stdout, in order, then any stderr) as a
        string without a trailing newline.

    Raises:
        subprocess.CalledProcessError: If the command exits non-zero (only
            raised if the generator is fully exhausted normally).
        OSError: If `command`'s executable cannot be found/started (propagates
            directly from `subprocess.Popen`).
    """
    logger.debug(f"stream() called: command={command} cwd={cwd}")
    proc = subprocess.Popen(
        command,
        cwd=_cwd_arg(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stderr_lines: list[str] = []

    def _drain_stderr() -> None:
        assert proc.stderr is not None
        stderr_lines.extend(line.rstrip("\n") for line in proc.stderr)

    reader = threading.Thread(target=_drain_stderr, daemon=True)
    reader.start()

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            yield line.rstrip("\n")

        reader.join()
        proc.wait()
        yield from stderr_lines

        if proc.returncode:
            raise subprocess.CalledProcessError(proc.returncode, command)
    finally:
        # Runs on normal completion (all no-ops below, since the process has
        # already exited and the reader thread has already finished) AND on
        # early abandonment via GeneratorExit — where it does the real work of
        # not leaking a running child process or a stuck reader thread.
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        reader.join(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()


def run(
    command: list[str],
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command to completion and return the completed process.

    Captures output (text mode, UTF-8) rather than streaming it. Use `stream()`
    when line-by-line output is needed; use this for fire-and-collect calls.

    Args:
        command: The argv list to execute (no shell interpolation).
        cwd: Optional working directory for the child process.
        env: Complete environment for the child, or None to inherit this
            process's. Callers that only want to *add* a variable should copy
            `os.environ` and extend it — this replaces the environment wholesale,
            exactly like `subprocess.run`. It exists so a secret can be handed
            over the environment instead of argv (`/proc/<pid>/cmdline` is
            world-readable on Linux; `environ` is not), and so a child can be
            told not to prompt.

    Returns:
        The completed process (stdout/stderr available as strings). Does not
        raise on non-zero exit — inspect `returncode`.
    """
    logger.debug(f"run() called: command={command} cwd={cwd}")
    return subprocess.run(
        command,
        cwd=_cwd_arg(cwd),
        env=dict(env) if env is not None else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


# A prompt-answering callback for `interact()`: gets each output line (and each
# quiet partial line, i.e. a prompt with no trailing newline) with ANSI colour
# codes stripped, returns the text to send to stdin (without newline) or None.
Responder = Callable[[str], str | None]

_ANSI = re.compile(r"\[[0-9;?]*[ -/]*[@-~]")

# How long a partial line must sit unchanged before it is treated as a prompt.
_PROMPT_QUIET_SECONDS = 0.3


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences (the install scripts colour everything)."""
    return _ANSI.sub("", text)


def interact(
    command: list[str],
    cwd: Path | None = None,
    *,
    respond: Responder,
    env: Mapping[str, str] | None = None,
    quiet_seconds: float = _PROMPT_QUIET_SECONDS,
    cancel: threading.Event | None = None,
) -> Iterator[str]:
    """Run an interactive command, yielding its output live and answering its prompts.

    `cancel` is checked every loop turn: setting it interrupts even a child
    sitting on a prompt no rule answers, which otherwise blocks forever
    (review finding, 2026-08-21) — the child is then terminated as usual.

    Stdout and stderr are merged and read in chunks, so a prompt that does not
    end in a newline (`read -p`, `echo -n ...; read`) still surfaces: once a
    partial line has been quiet for `quiet_seconds`, `respond()` is asked about
    it. `respond()` also sees every complete line. Whenever it returns a
    string, that string plus a newline is written to the child's stdin. Lines
    are yielded raw (with colour codes); `respond()` receives them stripped.

    Raises `subprocess.CalledProcessError` on non-zero exit, like `stream()`.
    If the generator is abandoned early the child is terminated (then killed)
    and its reader thread joined, exactly like `stream()`.
    """
    logger.debug(f"interact() called: command={command} cwd={cwd}")
    proc = subprocess.Popen(
        command,
        cwd=_cwd_arg(cwd),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=dict(env) if env is not None else None,
        bufsize=0,
    )
    assert proc.stdout is not None and proc.stdin is not None
    chunks: queue.Queue[bytes | None] = queue.Queue()
    out_fd = proc.stdout.fileno()

    def _pump() -> None:
        try:
            while True:
                data = os.read(out_fd, 4096)
                if not data:
                    break
                chunks.put(data)
        except OSError:
            pass
        finally:
            chunks.put(None)

    reader = threading.Thread(target=_pump, daemon=True)
    reader.start()
    buffer = ""
    answered_partial = False

    def _answer(text: str) -> bool:
        reply = respond(strip_ansi(text))
        if reply is None:
            return False
        try:
            assert proc.stdin is not None
            proc.stdin.write((reply + "\n").encode("utf-8"))
            proc.stdin.flush()
        except (BrokenPipeError, OSError):
            return False
        return True

    try:
        eof = False
        cancelled = False
        while not eof or buffer:
            if cancel is not None and cancel.is_set():
                cancelled = True
                break
            try:
                data = chunks.get(timeout=quiet_seconds)
            except queue.Empty:
                # A quiet partial line is a prompt waiting for input.
                if buffer and not answered_partial and _answer(buffer):
                    yield buffer
                    buffer = ""
                    answered_partial = False
                elif buffer:
                    answered_partial = True  # asked once; do not spam the same prompt
                continue
            if data is None:
                eof = True
                if buffer:
                    yield buffer
                    buffer = ""
                break
            buffer += data.decode("utf-8", errors="replace")
            answered_partial = False
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.rstrip("\r")
                yield line
                _answer(line)
        if not cancelled:
            reader.join()
            proc.wait()
            if proc.returncode:
                raise subprocess.CalledProcessError(proc.returncode, command)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        reader.join(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
        for handle in (proc.stdin, proc.stdout):
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass
