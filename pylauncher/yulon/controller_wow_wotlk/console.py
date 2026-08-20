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

import subprocess
import threading
import time
from dataclasses import dataclass

from yulon.controller_wow_wotlk import docker_ctl
from yulon.log import get_logger

logger = get_logger(__name__)

_DEFAULT_WINDOW_SECONDS = 3.0


class ConsoleError(RuntimeError):
    """`docker attach` could not be started (daemon down, container missing, ...)."""


@dataclass(frozen=True)
class ConsoleReply:
    """What the worldserver printed in the window after a command was sent."""

    command: str
    lines: tuple[str, ...]


def attach_argv(container: str) -> list[str]:
    """The exact `docker attach` invocation used (pinned by tests)."""
    return ["docker", "attach", "--sig-proxy=false", container]


def send_command(
    command: str,
    *,
    container: str = docker_ctl.SPEC.world,
    window: float = _DEFAULT_WINDOW_SECONDS,
    popen: type[subprocess.Popen[bytes]] = subprocess.Popen,
) -> ConsoleReply:
    """Send one console line to the worldserver and return what it printed within `window`."""
    if "\n" in command.strip("\n") or not command.strip():
        raise ValueError("send_command() takes exactly one non-empty command line")
    logger.info(f"console → {container}: {command.split(' ', 2)[0:2]}")  # never log passwords
    try:
        proc = popen(
            attach_argv(container),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
    except OSError as exc:
        raise ConsoleError(f"docker attach failed to start: {exc}") from exc
    assert proc.stdin is not None and proc.stdout is not None
    out: list[str] = []

    def _pump() -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            out.append(raw.decode("utf-8", errors="replace").rstrip("\r\n"))

    reader = threading.Thread(target=_pump, daemon=True)
    reader.start()
    try:
        proc.stdin.write((command.strip("\n") + "\n").encode("utf-8"))
        proc.stdin.flush()
    except (BrokenPipeError, OSError) as exc:
        proc.kill()
        raise ConsoleError(f"could not write to the worldserver console: {exc}") from exc
    time.sleep(window)
    # Detach: closing our end is all a non-TTY attach needs; the server keeps running.
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    reader.join(timeout=2)
    return ConsoleReply(command=command, lines=tuple(out))


def attach(container: str = docker_ctl.SPEC.world) -> list[str]:
    """The interactive attach argv for a terminal; Ctrl+P, Ctrl+Q detaches."""
    return attach_argv(container)
