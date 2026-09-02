"""The worldserver console for Tortoise: the shared transport, told how THIS console delimits.

The transport is not game-specific and is not repeated here. `docker attach`
with `--sig-proxy=false`, the pty (or `script(1)` inside a WSL distro), the
detach sequence and the reply window all live in
`controller_wow_wotlk/console.py`, which was live-gated against a real
worldserver on 2026-08-23.

Two facts about THIS console are, and both are already parameters of
`send_command()`:

* the prompt is `mangos>`, not `AC>`;
* it is printed AFTER the answer, not in front of it. AzerothCore reads its
  console with GNU readline, which redisplays the prompt before printing;
  CMaNGOS and this fork read with `fgets` and prompt only once the command has
  finished (research recorded on `catalog.Console`). Same delimiter, the answer
  on the opposite side of it — so passing only the string would have made every
  reply from this server an empty tuple flagged `prompted=True`.

Both come from the entry, so the parser is told what the catalog says rather
than what this file remembers.
"""

from __future__ import annotations

import subprocess

from yulon.controller_wow_tortoise import docker_ctl, game

# The transport's own vocabulary, re-exported so a caller of this package can
# catch and type against it without importing another game's package.
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
from yulon.controller_wow_wotlk.console import attach_argv, send_command

# `can_send()` asks the host and the distro, never the game, so it is re-exported
# as it stands: the Console tab's enablement question has one implementation.
from yulon.controller_wow_wotlk.console import (
    can_send as can_send,
)


def send(
    command: str,
    *,
    wsl_distro: str | None = None,
    popen: type[subprocess.Popen[bytes]] = subprocess.Popen,
    **kwargs: float,
) -> ConsoleReply:
    """Send one line to this install's worldserver console and return that command's answer.

    `kwargs` forwards `window`, which bounds how long the console is listened
    to. Forwarded rather than given a value here: nobody has measured how long a
    mangosd takes to answer a command, and a number invented in this file would
    read like one that had been.

    Raises:
        ConsoleError: this host cannot type at a console (no pty and no distro),
            there is no docker CLI, or `docker attach` could not be started.
    """
    return send_command(
        command,
        container=docker_ctl.SPEC.world,
        wsl_distro=wsl_distro,
        prompt=game.entry().console.prompt,
        prompt_precedes_answer=game.entry().console.prompt_precedes_answer,
        popen=popen,
        **kwargs,
    )


def attach() -> list[str]:
    """The interactive `docker attach` argv for this install's worldserver.

    What `NO_TTY_HELP` tells a Windows user to run in a terminal, built for them
    rather than typed. Ctrl+P, Ctrl+Q detaches without stopping the container.
    """
    return attach_argv(docker_ctl.SPEC.world)
