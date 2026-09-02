"""Worldserver console access for TBC: the shared transport, this core's delimiter.

The transport in `controller_wow_wotlk/console.py` is not AzerothCore-specific
and is reused whole: `docker attach --sig-proxy=false` over a pty (or over
`script(1)` inside a WSL distro, for a Windows host that has no pty of its own),
one command written, a fixed listening window, then a detach that never
forwards a signal into the server. The generated CMaNGOS stack keeps
`stdin_open: true` and `tty: true` on the mangosd service for exactly this
reason (`shared/cmangos/base.yml.tmpl`), so the attach has a console to reach.

Two facts differ, and the string alone is not one of them — it is two:

* the prompt is `mangos>`, not `AC>`;
* it comes AFTER the answer, not in front of it. AzerothCore reads its console
  with GNU readline, which redisplays the prompt immediately before what it is
  about to print; CMaNGOS reads with `fgets` and prints its prompt only once
  the command has finished (research, 2026-08-26, recorded on
  `catalog.Console`). Same delimiter, the answer on opposite sides of it — a
  core that declared only the string would have every reply parsed as empty.

Both come from `console` in the catalog entry, so the parser is told where to
cut rather than this module deciding.
"""

from __future__ import annotations

import subprocess

from yulon.controller_wow_tbc import docker_ctl
from yulon.controller_wow_wotlk import console as _shared
from yulon.controller_wow_wotlk.console import (
    DETACH_KEYS as DETACH_KEYS,
)
from yulon.controller_wow_wotlk.console import (
    NO_SCRIPT_HELP as NO_SCRIPT_HELP,
)
from yulon.controller_wow_wotlk.console import (
    NO_TTY_HELP as NO_TTY_HELP,
)
from yulon.controller_wow_wotlk.console import (
    ConsoleError as ConsoleError,
)
from yulon.controller_wow_wotlk.console import (
    ConsoleReply as ConsoleReply,
)
from yulon.controller_wow_wotlk.console import (
    attach_argv as attach_argv,
)
from yulon.controller_wow_wotlk.console import (
    can_send as can_send,
)
from yulon.controller_wow_wotlk.console import (
    pty_supported as pty_supported,
)

PROMPT = docker_ctl.ENTRY.console.prompt
"""`mangos>` — what this core prints when it is ready for the next command."""

PROMPT_PRECEDES_ANSWER = docker_ctl.ENTRY.console.prompt_precedes_answer
"""False: an `fgets` console prints its prompt after the answer, not in front of it."""


def send_command(
    command: str,
    *,
    container: str = docker_ctl.SPEC.world,
    wsl_distro: str | None = None,
    # The shared default, referenced rather than restated: a second number here
    # would drift from the one every other game's console waits.
    window: float = _shared._DEFAULT_WINDOW_SECONDS,
    popen: type[subprocess.Popen[bytes]] = subprocess.Popen,
) -> ConsoleReply:
    """Send one console line to mangosd and return that command's answer.

    The shared `send_command()` with this game's container and both console
    facts bound. `window` bounds how long the console is listened to, not what
    counts as the reply — the prompt is what delimits that.
    """
    return _shared.send_command(
        command,
        container=container,
        wsl_distro=wsl_distro,
        window=window,
        prompt=PROMPT,
        prompt_precedes_answer=PROMPT_PRECEDES_ANSWER,
        popen=popen,
    )


def attach(container: str = docker_ctl.SPEC.world) -> list[str]:
    """The interactive attach argv for a terminal; Ctrl+P, Ctrl+Q detaches."""
    return attach_argv(container)
