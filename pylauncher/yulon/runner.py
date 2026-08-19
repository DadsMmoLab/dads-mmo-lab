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

import subprocess
import threading
from collections.abc import Iterator
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


def run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command to completion and return the completed process.

    Captures output (text mode, UTF-8) rather than streaming it. Use `stream()`
    when line-by-line output is needed; use this for fire-and-collect calls.

    Args:
        command: The argv list to execute (no shell interpolation).
        cwd: Optional working directory for the child process.

    Returns:
        The completed process (stdout/stderr available as strings). Does not
        raise on non-zero exit — inspect `returncode`.
    """
    logger.debug(f"run() called: command={command} cwd={cwd}")
    return subprocess.run(
        command,
        cwd=_cwd_arg(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
