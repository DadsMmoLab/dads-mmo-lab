"""Worldserver console access for WotLK: send a command over `docker attach`.

AzerothCore's GM/account commands (`account create`, `account set gmlevel`,
`.server info`) are typed at the worldserver's console. The container keeps
that console on its stdin, so `docker attach` reaches it; `--sig-proxy=false`
makes sure detaching never forwards a signal into the worldserver (the guide's
"never press Ctrl+C" rule, enforced by the transport instead of the user).
`send_command()` attaches, writes ONE command, collects what the server prints
for a short window, detaches, and returns the lines — enough for the Phase 4
accounts workflow without a live interactive session.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass

from yulon import platform, runner
from yulon.controller_wow_wotlk import docker_ctl
from yulon.log import get_logger

logger = get_logger(__name__)

# How long docker needs to attach before the console is listening, and the
# prompt the worldserver prints in front of every line it echoes.
_ATTACH_SETTLE_SECONDS = 0.6
_PROMPT = "AC>"

_DEFAULT_WINDOW_SECONDS = 3.0


class ConsoleError(RuntimeError):
    """`docker attach` could not be started (daemon down, container missing, ...)."""


@dataclass(frozen=True)
class ConsoleReply:
    """What the worldserver printed in the window after a command was sent."""

    command: str
    lines: tuple[str, ...]


# Both live in `yulon.runner` now: the installer needs a terminal too (sudo
# reads its password from /dev/tty), and a helper two features share does not
# belong in one game's package (style-guide §4). Re-exported so this module's
# own callers and tests keep reading naturally.
pty_supported = runner.pty_supported
_open_pty = runner.open_pty


NO_TTY_HELP = (
    "The worldserver console needs a terminal. Docker refuses `docker attach` "
    "when its input is not a TTY, and this platform has no pseudo-terminal, so "
    "the app cannot send console commands here yet. Use the worldserver console "
    "in a terminal for now: `docker attach --sig-proxy=false {container}` "
    "(Ctrl+P then Ctrl+Q to leave it running)."
)


def attach_argv(container: str) -> list[str]:
    """The exact `docker attach` invocation used (pinned by tests).

    argv[0] is `platform.docker_program()`, not the literal `docker`, and this
    is the site where getting that wrong is worst. Everywhere else an
    unresolved CLI is a command that failed and can be retried; here it is the
    GM console — account creation, `.server info`, every gameplay command the
    app offers — coming up dead on a machine where Docker is installed and
    running, because this process was started before the installer wrote its
    PATH entry (see `platform.docker_program()`).

    Raises:
        ConsoleError: this host has no docker CLI at all. Raised rather than
            returned as an unusable argv so `send_command()` fails before it
            opens a pty, and so the user gets `DOCKER_CLI_MISSING_HELP` instead
            of a `FileNotFoundError` from `Popen`.
    """
    program = platform.docker_program()
    if program is None:
        raise ConsoleError(platform.DOCKER_CLI_MISSING_HELP)
    return [program, "attach", "--sig-proxy=false", container]


def send_command(
    command: str,
    *,
    container: str = docker_ctl.SPEC.world,
    window: float = _DEFAULT_WINDOW_SECONDS,
    popen: type[subprocess.Popen[bytes]] = subprocess.Popen,
) -> ConsoleReply:
    """Send one console line to the worldserver and return what it printed within `window`."""
    if any(ch in command.strip("\n") for ch in ("\n", "\r")) or not command.strip():
        raise ValueError("send_command() takes exactly one non-empty command line")
    logger.info(f"console → {container}: {command.split(' ', 2)[0:2]}")  # never log passwords
    if not pty_supported():
        raise ConsoleError(NO_TTY_HELP.format(container=container))
    # Resolve the CLI BEFORE the pty exists. `attach_argv()` can raise, and the
    # `except OSError` below would not catch a ConsoleError — the master/slave
    # pair would leak one fd per attempt.
    argv = attach_argv(container)
    # The worldserver container runs with tty=true, so docker REFUSES to attach
    # unless its stdin is a terminal ("the input device is not a TTY"). Writing
    # to /proc/1/fd/0 does not help either: that fd is the terminal, so a write
    # only prints text, it never reaches the console's input. A real pty does
    # (live-verified on the Ubuntu VM, 2026-08-21).
    master, slave = _open_pty()
    try:
        proc = popen(
            argv,
            stdin=slave,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
    except OSError as exc:
        os.close(master)
        os.close(slave)
        # The fourth module that has to say this — `DOCKER_CLI_MISSING_HELP`'s own
        # docstring names all four. `attach_argv()` above covers the never-resolved
        # case; this covers the pinned-then-removed one, where argv[0] is an
        # absolute path that has since gone (a Docker Desktop uninstall or in-place
        # upgrade mid-session), which used to surface as a bare [Errno 2].
        # Logged with the real errno first, the way `docker._docker()` does, so a
        # docker.exe blocked by an ACL or by AV leaves evidence instead of being
        # reported to the user as "install Docker Desktop" with nothing in the log
        # to contradict it (review finding, 2026-08-23).
        logger.warning(f"{argv[0]} could not be started: {exc}")
        raise ConsoleError(platform.DOCKER_CLI_MISSING_HELP) from exc
    os.close(slave)  # the child holds its own copy
    assert proc.stdout is not None
    out: list[str] = []

    def _pump() -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            out.append(raw.decode("utf-8", errors="replace").rstrip("\r\n"))

    reader = threading.Thread(target=_pump, daemon=True)
    reader.start()
    # Give docker a moment to attach, or the first command is written into the
    # void before the console is listening.
    time.sleep(_ATTACH_SETTLE_SECONDS)
    try:
        os.write(master, (command.strip("\n") + "\n").encode("utf-8"))
    except OSError as exc:
        _close_console(proc, master, reader)
        raise ConsoleError(f"could not write to the worldserver console: {exc}") from exc
    time.sleep(window)
    # Detach without stopping the server. `docker attach` ignores SIGTERM here,
    # so kill our client outright — the container is untouched either way.
    _close_console(proc, master, reader)
    return ConsoleReply(command=command, lines=_reply_lines(out, command))


def _close_console(proc: subprocess.Popen[bytes], master: int, reader: threading.Thread) -> None:
    """Kill the attach client, close the pty and join the reader (never raises)."""
    proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:  # pragma: no cover - kill() not honoured
        logger.warning("docker attach did not exit after kill()")
    try:
        os.close(master)
    except OSError:  # pragma: no cover - already closed
        pass
    reader.join(timeout=2)


def _reply_lines(raw: list[str], command: str) -> tuple[str, ...]:
    """The console's answer: ANSI stripped, prompts and our own echo removed."""
    sent = command.strip()
    lines: list[str] = []
    for line in raw:
        text = runner.strip_ansi(line).replace("\x1b", "").strip()
        while text.startswith(_PROMPT):
            text = text[len(_PROMPT) :].lstrip()
        if not text or text == sent:
            continue
        lines.append(text)
    return tuple(lines)


def attach(container: str = docker_ctl.SPEC.world) -> list[str]:
    """The interactive attach argv for a terminal; Ctrl+P, Ctrl+Q detaches."""
    return attach_argv(container)
