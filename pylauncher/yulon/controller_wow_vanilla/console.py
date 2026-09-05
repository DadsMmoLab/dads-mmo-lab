"""Worldserver console access for Vanilla: the shared transport, this core's prompt.

Everything about getting a line into a container's console — `docker attach`
with `--sig-proxy=false`, the pty on POSIX, `script(1)` inside a WSL distro on
Windows, the detach keys, the reply window — is transport and is already
game-agnostic in `controller_wow_wotlk/console.py`. Its live gate (Ubuntu VM,
2026-08-23) measured that transport, not AzerothCore, and none of it is
repeated here.

What differs is how this core DELIMITS an answer, and it is two facts rather
than one:

* the prompt is `mangos>`, not `AC>`;
* `prompt_precedes_answer` is FALSE. AzerothCore reads its console with GNU
  readline, which redisplays the prompt in front of what it is about to print,
  so the answer sits after the first prompt. CMaNGOS reads with `fgets` and
  prints its prompt from `commandFinished()` — after the answer. Same
  delimiter, the reply on the other side of it.

Both are read from `entry().console`, which is where that research was
recorded (2026-08-26). A binding that set only the string would parse every
CMaNGOS reply as an empty tuple flagged `prompted=True`: a command that
answered, reported as a command that said nothing.

**What has not been measured here.** The transport's live gate ran against an
AzerothCore worldserver. That this game's `mangosd` container keeps a console
on its stdin, and that it prints its prompt exactly twice per command the way
`_parse_reply()` depends on, are read off the catalog entry and have not been
watched on a running Vanilla server by this package.
"""

from __future__ import annotations

import subprocess

from yulon.controller_wow_vanilla import docker_ctl, entry

# The transport, imported rather than re-derived. Its own module docstring is
# the record of what was measured about it.
from yulon.controller_wow_wotlk import console as transport
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

PROMPT = entry().console.prompt
"""What this core prints when it is ready for the next command (`mangos>`)."""

PROMPT_PRECEDES_ANSWER = entry().console.prompt_precedes_answer
"""False for this core's `fgets` console: the prompt comes AFTER the answer."""

_WINDOW = transport._DEFAULT_WINDOW_SECONDS
"""The transport's own reply window, read rather than restated.

Deliberately reaching for a private name: this package measured no window of
its own, and writing `3.0` here would create a second source of truth for a
number that belongs to the transport. If the transport changes it, this
changes with it.
"""


def send_command(
    command: str,
    *,
    container: str = docker_ctl.SPEC.world,
    wsl_distro: str | None = None,
    window: float = _WINDOW,
    popen: type[subprocess.Popen[bytes]] = subprocess.Popen,
) -> ConsoleReply:
    """Send one console line to this install's worldserver and return its answer.

    The shared `send_command()` with this game's three facts filled in: which
    container, what its prompt looks like, and which side of that prompt the
    answer is on. Everything the shared function documents about the window,
    the detach and the parse applies unchanged.

    Raises:
        ConsoleError: no pty on this platform, no docker CLI, no `script` in
            the distro, or the pty could not be written to. A command the
            SERVER rejects is not an error — its refusal is returned like any
            other answer.
        ValueError: `command` is empty or carries more than one line.
    """
    return transport.send_command(
        command,
        container=container,
        wsl_distro=wsl_distro,
        window=window,
        prompt=PROMPT,
        prompt_precedes_answer=PROMPT_PRECEDES_ANSWER,
        popen=popen,
    )


def attach(container: str = docker_ctl.SPEC.world) -> list[str]:
    """The interactive attach argv for a terminal; Ctrl+P, Ctrl+Q detaches.

    Bound to this install's worldserver. The argv itself carries no prompt —
    a human reading the console does the delimiting.
    """
    return transport.attach(container)
