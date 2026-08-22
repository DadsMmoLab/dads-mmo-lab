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
    timeout: float | None = None,
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
        timeout: Seconds to wait before giving up, or None for no bound. A
            timeout is reported as a non-zero `returncode` with the reason in
            `stderr`, not raised, so every existing caller keeps its shape.
            There is nothing sound to default this to — a `compose up` may take
            minutes — so it is per-call, and the callers that need one are the
            ones running on the GUI thread.

    Returns:
        The completed process (stdout/stderr available as strings). Does not
        raise on non-zero exit — inspect `returncode`.
    """
    logger.debug(f"run() called: command={command} cwd={cwd}")
    try:
        return subprocess.run(
            command,
            cwd=_cwd_arg(cwd),
            env=dict(env) if env is not None else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        logger.warning(f"{command[0]} did not answer within {timeout}s; giving up")
        return subprocess.CompletedProcess(
            command, 124, _as_text(exc.stdout), f"timed out after {timeout}s"
        )


def _as_text(captured: object) -> str:
    """`TimeoutExpired.stdout` is bytes even in text mode, and may be None."""
    if isinstance(captured, bytes):
        return captured.decode("utf-8", errors="replace")
    return captured if isinstance(captured, str) else ""


# A prompt-answering callback for `interact()`: gets each output line (and each
# quiet partial line, i.e. a prompt with no trailing newline) with ANSI colour
# codes stripped, returns the text to send to stdin (without newline) or None.
Responder = Callable[[str], str | None]

# Asked when the child prints the exact marker the CALLER chose, and never
# otherwise. Returning None means "I cannot answer this either", and the caller
# is left to cancel.
#
# There used to be a heuristic here: a partial line ending in one of : ? > ]
# after a moment's quiet was taken for a prompt. Measured against real build
# output it fires on "[ 43%]", "Get:12 ... [345 kB]", "note:", "#12 sha256:abc
# [2/5]" and every gcc diagnostic — and `interact()` reads in 4096-byte chunks,
# so a chunk boundary landing on one of those during a 2-4 hour compile is
# routine rather than exotic. The result was an application-modal dialog that
# blocked the Stop button, quoted a fragment of compiler output, and wrote
# whatever was typed into the build's stdin.
#
# So the guess is gone. The one prompt that actually needed answering is sudo's,
# and sudo lets the caller choose its wording through SUDO_PROMPT — an exact,
# unguessable string no compiler will ever print (review, 2026-08-22).
Prompter = Callable[[str], str | None]

_ANSI = re.compile(r"\[[0-9;?]*[ -/]*[@-~]")

# How long a partial line must sit unchanged before it is looked at.
_PROMPT_QUIET_SECONDS = 0.3


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences (the install scripts colour everything)."""
    return _ANSI.sub("", text)


def pty_supported() -> bool:
    """True where a pseudo-terminal can be opened (POSIX). False on Windows."""
    return hasattr(os, "openpty") and hasattr(os, "login_tty")


def open_pty() -> tuple[int, int]:
    """`os.openpty()`, fetched dynamically because it does not exist on Windows.

    Callers must check `pty_supported()` first. Lives here rather than in a
    per-game package because two unrelated features need a terminal: the
    worldserver console (docker refuses to attach to a non-TTY) and the
    installer (sudo reads its password from /dev/tty, never from stdin) —
    style-guide §4.
    """
    open_it = getattr(os, "openpty")  # noqa: B009 - POSIX-only attribute
    master, slave = open_it()
    return int(master), int(slave)


def _become_terminal_session(slave: int) -> None:
    """Child-side: make `slave` this process's controlling terminal.

    This is what makes `/dev/tty` resolve inside the child, and `/dev/tty` is
    the whole point: sudo deliberately does not read its password from stdin, so
    a child holding a pty on fd 0 but with no CONTROLLING terminal still fails
    with "a terminal is required to read the password". Inheriting a pty is not
    enough — the session has to be claimed.

    Runs after fork and before exec, so it must do nothing that could take a
    lock another thread held at fork time. `os.login_tty` is a single libc call
    (setsid, TIOCSCTTY, dup2 onto 0/1/2), which is what makes it safe here where
    a general Python callback would not be.
    """
    login_tty = getattr(os, "login_tty")  # noqa: B009 - POSIX-only attribute
    login_tty(slave)


def interact(
    command: list[str],
    cwd: Path | None = None,
    *,
    respond: Responder,
    ask: Prompter | None = None,
    ask_marker: str | None = None,
    env: Mapping[str, str] | None = None,
    terminal: bool = False,
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
    string, that string plus a newline is written to the child's input. Lines
    are yielded raw (with colour codes); `respond()` receives them stripped.

    `ask` is the escape hatch for the one prompt no rule can answer: sudo's
    password. It is consulted ONLY when the pending text contains `ask_marker`,
    an exact string the caller arranged for the child to print. Without a
    marker `ask` is never called at all — a deliberate dead default, because the
    previous version guessed from the shape of a line and could not tell a
    password prompt from `[ 43%]`.

    `terminal=True` runs the child on a pseudo-terminal and makes that terminal
    its controlling tty. This is what the sudo case needs and a pipe cannot
    give: sudo reads from /dev/tty precisely so that a piped stdin cannot feed
    it a password. Measured — a child reading stdin answers through a pipe, the
    same child reading /dev/tty does not, and real sudo says "a terminal is
    required to read the password". POSIX only; ignored where `pty_supported()`
    is False, which is every Windows box and no installer host (the catalog's
    install scripts are Linux-only).

    Raises `subprocess.CalledProcessError` on non-zero exit, like `stream()`.
    If the generator is abandoned early the child is terminated (then killed)
    and its reader thread joined, exactly like `stream()`.
    """
    logger.debug(f"interact() called: command={command} cwd={cwd} terminal={terminal}")
    on_pty = terminal and pty_supported()
    master = slave = -1
    if on_pty:
        master, slave = open_pty()
    try:
        if on_pty:
            proc = subprocess.Popen(
                command,
                cwd=_cwd_arg(cwd),
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=dict(env) if env is not None else None,
                bufsize=0,
                preexec_fn=lambda: _become_terminal_session(slave),
            )
        else:
            proc = subprocess.Popen(
                command,
                cwd=_cwd_arg(cwd),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=dict(env) if env is not None else None,
                bufsize=0,
            )
    except BaseException:
        for fd in (master, slave):
            if fd >= 0:
                os.close(fd)
        raise
    if on_pty:
        # The parent's copy would otherwise hold the pty open forever, so the
        # read below would never see EOF after the child exits.
        os.close(slave)
        slave = -1
        out_fd = master
    else:
        assert proc.stdout is not None
        out_fd = proc.stdout.fileno()
    chunks: queue.Queue[bytes | None] = queue.Queue()

    def _write(payload: bytes) -> bool:
        """Send bytes to the child, whichever transport it is on."""
        try:
            if on_pty:
                os.write(master, payload)
            else:
                assert proc.stdin is not None
                proc.stdin.write(payload)
                proc.stdin.flush()
        except (BrokenPipeError, OSError):
            return False
        return True

    def _pump() -> None:
        try:
            while True:
                data = os.read(out_fd, 4096)
                if not data:
                    break
                chunks.put(data)
        except OSError:
            # On a pty the master raises EIO rather than returning b"" when the
            # last slave closes. That is this transport's EOF, not a failure.
            pass
        finally:
            chunks.put(None)

    reader = threading.Thread(target=_pump, daemon=True)
    reader.start()
    buffer = ""
    answered_partial = False

    def _is_the_prompt(text: str) -> bool:
        """True only for the exact marker the caller arranged for. No guessing."""
        if not ask_marker:
            return False
        return ask_marker in strip_ansi(text)

    def _answer(text: str) -> bool:
        clean = strip_ansi(text)
        reply = respond(clean)
        if reply is None and ask is not None and _is_the_prompt(text):
            reply = ask(clean)
        if reply is None:
            return False
        return _write((reply + "\n").encode("utf-8"))

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
                # A partial line that has gone quiet is the child waiting for
                # input — or just a slow build. Ask about it once.
                if not buffer or answered_partial:
                    continue
                if _answer(buffer) or _is_the_prompt(buffer):
                    # Shown either way. A prompt the user declined still has to
                    # reach the log, or the install appears to freeze with
                    # nothing on screen explaining why (review, 2026-08-22).
                    yield buffer
                    buffer = ""
                    answered_partial = False
                else:
                    answered_partial = True  # asked once; do not spam the same line
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
        for fd in (master, slave):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
