"""Worldserver console access for Centurion: the shared transport, this entry's prompt.

The transport is `controller_wow_wotlk/console.py`'s, reused whole: `docker attach
--sig-proxy=false` over a pty, one line written, a listening window, a detach
that forwards no signal. The generated stack keeps `stdin_open`/`tty` on the
worldserver (`shared/trinitycore/base.yml.tmpl`) -- it also STOPS on stdin EOF
(`CliRunnable.cpp:183-186`), so the attach must never close the console's stdin,
which `--sig-proxy=false` and the detach keys already guarantee.

The prompt and the side of it the answer lands on are the entry's `console`
block: `TC>` (`CLI_PREFIX`, `CliRunnable.cpp:42`), read with GNU readline and
printed again when a command finishes (`:92-96`, `:157`) -- AzerothCore's console
code, which TrinityCore is the parent of.
"""

from __future__ import annotations

import subprocess

from yulon.catalog.catalog import CatalogEntry
from yulon.controller_wow_wotlk import console as _shared
from yulon.controller_wow_wotlk.console import ConsoleError as ConsoleError
from yulon.controller_wow_wotlk.console import ConsoleReply as ConsoleReply
from yulon.controller_wow_wotlk.console import attach_argv as attach_argv
from yulon.controller_wow_wotlk.console import can_send as can_send


def send_command(
    entry: CatalogEntry,
    command: str,
    *,
    wsl_distro: str | None = None,
    window: float = _shared._DEFAULT_WINDOW_SECONDS,
    popen: type[subprocess.Popen[bytes]] = subprocess.Popen,
) -> ConsoleReply:
    """Send one console line to this entry's worldserver and return that command's answer."""
    return _shared.send_command(
        command,
        container=entry.container_spec().world,
        wsl_distro=wsl_distro,
        window=window,
        prompt=entry.console.prompt,
        prompt_precedes_answer=entry.console.prompt_precedes_answer,
        popen=popen,
    )


def attach(entry: CatalogEntry) -> list[str]:
    """The interactive attach argv for a terminal; Ctrl+P, Ctrl+Q detaches."""
    return attach_argv(entry.container_spec().world)
