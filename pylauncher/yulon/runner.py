"""Shared subprocess streaming wrapper.

Provides line-by-line stdout streaming so the UI can display install/build/
container output without buffering the whole run. Stderr is drained
concurrently on a background thread — this exists purely to avoid pipe-buffer
deadlock (a chatty stderr filling its OS pipe buffer while nobody reads it),
not to interleave stderr into the stream in real time: stderr lines are
yielded only after the process exits and stdout is exhausted — unless the
caller asks for `merge_stderr`, which puts both on one pipe for commands whose
real output IS stderr (BuildKit). See `stream()`'s docstring for the exact
ordering.
"""

from __future__ import annotations

import errno
import importlib
import os
import queue
import re
import subprocess
import sys
import threading
from collections.abc import Callable, Generator, Iterator, Mapping
from pathlib import Path

from yulon.log import get_logger

logger = get_logger(__name__)

# How long to wait for a terminated/killed child and its stderr-reader thread
# to actually finish, when a `stream()` generator is abandoned early.
_SHUTDOWN_TIMEOUT_SECONDS = 5.0


def _cwd_arg(cwd: Path | None) -> str | None:
    """Convert an optional Path working dir to the str `subprocess` expects."""
    return str(cwd) if cwd is not None else None


def creationflags() -> int:
    """`CREATE_NO_WINDOW` on native Windows, 0 elsewhere (roadmap 6.3).

    Every subprocess this module spawns is a console-less utility (git, docker
    compose, mysql, curl), and native Windows gives each a console window by
    default — a window that flashes over the launcher UI on every one of the
    dozen short-lived commands a single server action runs, and that a user can
    close while the build it belongs to is still running (`rust-prior-art.md`
    §4, "spawn with CREATE_NO_WINDOW or consoles flash over the UI").

    Public (not `_`-prefixed) because the three spawn sites that do not go through
    this module's `run()`/`stream()`/`interact()` — `apply.py`'s SQL runner,
    `maintenance.py`'s `docker exec`, and `console.py`'s `docker attach` client
    (`popen=subprocess.Popen`) — must apply the same flag, and a flag applied to
    some spawn sites but not others is a window that flashes anyway. `console.py`'s
    attach is POSIX-only by `pty_supported()`, so the flag is inert there today;
    it is carried anyway so the one place that *can* add a Windows console does
    not silently become the exception. The same reason `git.CONTAINER_GIT_IMAGE`
    is public: a value that must be shared exactly rather than re-derived.

    Fetched off `subprocess` at call time rather than imported at module scope,
    because `CREATE_NO_WINDOW` exists only on Windows and this module is
    type-checked for POSIX too — the same reason `pty_supported()` does not name
    `openpty` directly. `sys.platform` is checked, not `hasattr`, so a future
    stdlib flag rename cannot silently turn a no-window child into a windowed
    one without this branch noticing.
    """
    if sys.platform != "win32":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW"))  # noqa: B009 - Windows-only attribute


_FROZEN_LIBRARY_VARS = (
    "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",
    "DYLD_FRAMEWORK_PATH",
    "LIBPATH",
)
"""Loader search paths PyInstaller rewrites, and children must not inherit.

One per platform family - Linux/BSD, macOS (two of them), AIX - listed together
because the bug is one bug and a fix applied to some of them is a fix on some
platforms only.
"""


def child_env(env: Mapping[str, str] | None = None) -> dict[str, str] | None:
    """The environment a spawned child should actually get.

    THE PACKAGED LAUNCHER BREAKS THE TOOLS IT SHELLS OUT TO, and this is where
    that stops. PyInstaller points `LD_LIBRARY_PATH` at the bundle's own
    `_internal` directory so the frozen interpreter finds its libraries. Every
    child process inherits it, and a system binary that links anything the
    bundle also ships then loads the BUNDLE's copy. Measured inside the v0.6.5
    AppImage on Arch, 2026-08-24:

        bash  -> symbol lookup error: undefined symbol: rl_print_keybinding
        curl  -> libssl.so.3: version `OPENSSL_3.2.0' not found
                 (required by /usr/lib/libcurl.so.4)
        git   -> libpcre2-8.so.0: no version information available

    bash died outright, so `installer.bash_available()` - which ran
    `bash -c "exit 0"` and was the FIRST subprocess an install made - answered
    False, and the user was told "this machine has no working bash" about a
    machine whose bash was fine. That probe went with the bash engine in 7.2;
    the leak is unchanged and still reaches every child. curl loses HTTPS
    entirely, which is what "refuses to download files" looks like from the
    outside.

    PyInstaller saves the pre-launch value as `<VAR>_ORIG` for exactly this
    purpose. Restoring it (or removing the variable when there was nothing to
    restore) hands the child the environment it would have had if the user had
    typed the command themselves.

    Returns None when not frozen, so a source checkout keeps inheriting this
    process's environment exactly as before - the bug does not exist there, and
    neither should the fix. That asymmetry is also why the whole test suite and
    the CLI harness stayed green while the shipped artifact could not run
    `bash`: they never run frozen.
    """
    if not getattr(sys, "frozen", False):
        return dict(env) if env is not None else None
    base = dict(env) if env is not None else dict(os.environ)
    for var in _FROZEN_LIBRARY_VARS:
        original = base.pop(f"{var}_ORIG", None)
        if original:
            base[var] = original
        else:
            # No _ORIG, or an empty one: the variable was unset before the
            # bundle set it, so unset is what the child should see. Leaving an
            # empty string behind is not the same thing - an empty
            # LD_LIBRARY_PATH means "the current directory" to some loaders.
            base.pop(var, None)
    return base


def stream(
    command: list[str], cwd: Path | None = None, *, merge_stderr: bool = False
) -> Generator[str, None, None]:
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
    That is why the return type is a `Generator` and not an `Iterator`: the
    `close()` is part of the contract, and a caller that stops early should say
    so (`contextlib.closing`) rather than leave it to refcounting.

    Args:
        command: The argv list to execute (no shell interpolation).
        cwd: Optional working directory for the child process.
        merge_stderr: Send the child's stderr into the SAME pipe as its stdout,
            so both are yielded live and in the order the child wrote them.
            Added for the native install engine's build stage (roadmap 6.2):
            BuildKit writes all of its progress to stderr, and the default
            ordering above turns a two-to-four-hour compile into a blank log
            panel that only fills in once the build has already finished.
            Interleaving costs the ability to tell the two streams apart, which
            is why it is opt-in: every existing caller reads a command whose
            stderr is an error report rather than its output.

    Yields:
        Each output line (all of stdout, in order, then any stderr) as a
        string without a trailing newline. With `merge_stderr`, one interleaved
        stream in the child's own order.

    Raises:
        subprocess.CalledProcessError: If the command exits non-zero (only
            raised if the generator is fully exhausted normally).
        OSError: If `command`'s executable cannot be found/started (propagates
            directly from `subprocess.Popen`).
    """
    logger.debug(f"stream() called: command={command} cwd={cwd} merge_stderr={merge_stderr}")
    proc = subprocess.Popen(
        command,
        cwd=_cwd_arg(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=child_env(),
        creationflags=creationflags(),
    )
    stderr_lines: list[str] = []

    def _drain_stderr() -> None:
        assert proc.stderr is not None
        stderr_lines.extend(line.rstrip("\n") for line in proc.stderr)

    # With the streams merged there is no second pipe to drain, and starting a
    # reader on `proc.stderr` (which is then None) would raise inside the
    # thread rather than at the call site.
    reader: threading.Thread | None = None
    if not merge_stderr:
        reader = threading.Thread(target=_drain_stderr, daemon=True)
        reader.start()

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            yield line.rstrip("\n")

        if reader is not None:
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
        if reader is not None:
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
    *,
    stdin: int | None = subprocess.DEVNULL,
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
        stdin: Optional standard input stream or descriptor (defaults to DEVNULL).

    Returns:
        The completed process (stdout/stderr available as strings). Does not
        raise on non-zero exit — inspect `returncode`.
    """
    logger.debug(f"run() called: command={command} cwd={cwd}")
    try:
        return subprocess.run(
            command,
            cwd=_cwd_arg(cwd),
            env=child_env(env),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
            creationflags=creationflags(),
            stdin=stdin,
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
    """True where a pseudo-terminal can be opened (POSIX). False on Windows.

    Only `openpty` is required. It briefly also demanded `os.login_tty`, which
    is Python 3.11+, but `console.py` shares this predicate and needs no such
    thing — a 3.10 interpreter would have been told "this platform has no
    pseudo-terminal", which is false (review, 2026-08-22).
    """
    return hasattr(os, "openpty")


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


# Claim the pty as the controlling terminal WITHOUT running Python after fork.
#
# `/dev/tty` is the whole point: sudo deliberately does not read its password
# from stdin, so a child holding a pty on fd 0 but with no CONTROLLING terminal
# still fails with "a terminal is required to read the password". Inheriting a
# pty is not enough — the session has to be claimed.
#
# The obvious way to claim it is `preexec_fn=lambda: os.login_tty(slave)`. That
# is a Python callback executed between fork and exec, in a process whose Qt GUI
# thread and job threads are still live: closure read, global lookup, frame
# creation, `getattr`, an args tuple — every step can allocate, and an allocator
# lock held by another thread at fork time wedges the child there while the
# parent blocks inside Popen, before `interact()`'s cancel loop is ever entered.
# CPython's own comment at that call site reads "This is where the user has
# asked us to deadlock their program" (review, 2026-08-22).
#
# So the claim is delegated to `sh`, after exec, where no Python is involved:
# `start_new_session=True` calls setsid() in C, leaving a session leader with no
# controlling terminal, and re-`open()`ing the slave BY NAME without O_NOCTTY is
# what makes it one. The shell then execs the real command, so the pid, signals
# and exit status are the command's own.
_CLAIM_THE_TERMINAL = 'exec <"$1" >"$1" 2>&1; shift; exec "$@"'


def _terminal_argv(slave: int, command: list[str]) -> list[str]:
    """`command`, wrapped so it starts owning `slave` as its controlling terminal."""
    ttyname = getattr(os, "ttyname")  # noqa: B009 - POSIX-only attribute
    return ["sh", "-c", _CLAIM_THE_TERMINAL, "sh", str(ttyname(slave)), *command]


def _silence_terminal_echo(slave: int) -> None:
    """Stop the line discipline echoing back everything we type into the child.

    A fresh pty has ECHO on, so every answer `respond()` writes would be echoed
    onto the master, land in the output buffer, and be yielded straight into the
    log panel. sudo turns echo off around its own password read, so the measured
    "the password never appeared in the output" was sudo's doing, not ours —
    which is not a property to rely on for anything else the app types
    (review, 2026-08-22). Best effort: a pty that will not take the setting is
    not a reason to refuse to install.
    """
    try:
        # Fetched dynamically for the same reason `open_pty` is: the module does
        # not exist on Windows, and mypy checks this file for that platform too.
        termios = importlib.import_module("termios")
        attrs = termios.tcgetattr(slave)
        attrs[3] &= ~(termios.ECHO | termios.ECHONL)  # index 3 is lflag
        termios.tcsetattr(slave, termios.TCSANOW, attrs)
    except Exception as exc:  # noqa: BLE001 - never fail an install over echo
        logger.debug(f"could not turn off terminal echo: {exc}")


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
            _silence_terminal_echo(slave)
            proc = subprocess.Popen(
                _terminal_argv(slave, command),
                cwd=_cwd_arg(cwd),
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=child_env(env),
                bufsize=0,
                start_new_session=True,  # see _CLAIM_THE_TERMINAL
            )
        else:
            proc = subprocess.Popen(
                command,
                cwd=_cwd_arg(cwd),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=child_env(env),
                bufsize=0,
                creationflags=creationflags(),
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
        except OSError as exc:
            # On a pty the master raises EIO rather than returning b"" when the
            # last slave closes. That is this transport's EOF, not a failure —
            # and so is EBADF, which is what the `finally` closing `master` under
            # a live read looks like from here. Anything else means output was
            # lost, and a truncated log on a failed two-hour build is exactly
            # when the log matters, so say so (review, 2026-08-22).
            if exc.errno not in (errno.EIO, errno.EBADF):
                logger.warning(f"reading the child's output stopped early: {exc}")
        finally:
            chunks.put(None)

    reader = threading.Thread(target=_pump, daemon=True)
    try:
        reader.start()
    except BaseException:
        # The child is already running and `master` is open, and the try/finally
        # below has not been entered yet. `RuntimeError: can't start new thread`
        # is not far-fetched in a long-lived GUI process (review, 2026-08-22).
        proc.kill()
        proc.wait()
        if master >= 0:
            os.close(master)
        raise
    buffer = ""
    answered_partial = False

    def _the_prompt(text: str) -> str | None:
        """The marker and everything after it, or None if the marker is not there.

        Only the exact marker the caller arranged for — no guessing. The slice
        matters as much as the match: `ask()` used to receive the whole pending
        buffer, which on a terminal is routinely non-empty (progress output ends
        in `\\r`, and only `\\n` splits a line here). So the question put to the
        user was whatever the script last printed with the prompt stuck on the
        end — measured: `Checking directory /opt/azerothcore ... [marker]
        password:`, which `is_secret()` then classified as NOT a secret because
        it contains the word "directory", and the root password was typed into
        an unmasked field and written to the log (review, 2026-08-22).
        """
        if not ask_marker:
            return None
        clean = strip_ansi(text)
        at = clean.find(ask_marker)
        return None if at < 0 else clean[at:]

    def _answer(text: str, *, blocked: bool = False) -> bool:
        prompt = _the_prompt(text) if blocked else None
        if prompt is not None:
            # The marker proves the child is blocked on the ONE prompt we know
            # about. Everything printed before it is unrelated output that
            # merely shares the buffer, and a `respond()` built from unanchored
            # `search`es — as the bash engine's rule table was until 7.2 — got
            # the whole buffer, so its bare `(y/n)` catch-all answered sudo's
            # password read with "y". Measured: a child printing
            # `Reset the keyring? (y/n) \r` and
            # then the marker got `GOT:y` and `ask()` was never called, which is
            # the pre-6.1.5 symptom arriving by a new route. Slicing for `ask`
            # alone was half a fix (review, 2026-08-23).
            reply = respond(prompt)
            if reply is None and ask is not None:
                reply = ask(prompt)
        else:
            reply = respond(strip_ansi(text))
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
                if proc.poll() is not None:
                    # The child is gone but the reader has not seen EOF. On a
                    # pty that is normal: the install scripts leave a
                    # `sudo -n true; sleep 60` keepalive whose orphaned `sleep`
                    # still holds the slave, so `os.read(master)` returns
                    # neither b"" nor EIO for up to a minute after the install
                    # finished — a minute of "installing" with no output and no
                    # way out (review, 2026-08-22).
                    #
                    # This used to require an EMPTY buffer, so a script whose
                    # last line had no trailing newline never took it — and on
                    # cancel those bytes were dropped, which was exactly the
                    # text the bash engine's `run()` built its failure message
                    # from. Yield them, then stop (review, 2026-08-23).
                    logger.debug("child exited; ending the read rather than waiting for EOF")
                    if buffer:
                        yield buffer
                        buffer = ""
                    break
                # A partial line that has gone quiet is the child waiting for
                # input — or just a slow build. Ask about it once.
                if not buffer or answered_partial:
                    continue
                if _answer(buffer, blocked=True) or _the_prompt(buffer) is not None:
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
            # Bounded: an orphan holding the slave keeps the reader in os.read
            # long after the child is gone, and this join used to be unbounded.
            reader.join(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
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
