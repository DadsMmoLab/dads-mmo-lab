"""Per-user app state under `platform.config_dir()` (README §11): the installs we know about.

This is app state, not server data: which games are installed where (and the
client folder the user pointed at), so the Controller can offer them. It is
deliberately tiny and typed; anything bigger (cached manifests, the log) has
its own file/dir next to it.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from yulon import platform
from yulon.log import get_logger

logger = get_logger(__name__)

STATE_FILE_NAME = "state.json"


class KnownInstall(BaseModel):
    """One server install the app manages."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    game: str = Field(min_length=1)
    server_dir: Path
    client_dir: Path | None = None
    wsl_distro: str | None = None
    """The WSL2 distro this server lives inside, if it does.

    Optional with a default, so every `state.json` written before WSL-resident
    servers were supported still loads. It sits beside `server_dir` because it
    is the same kind of fact - where this server is - and nothing infers it at
    runtime: an install was either adopted from a distro or it was not.
    """


class AppState(BaseModel):
    """Everything `state.json` holds."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    installs: list[KnownInstall] = Field(default_factory=list)

    def find(self, game: str, server_dir: Path) -> KnownInstall | None:
        """The install for `game` at `server_dir`, if known."""
        for install in self.installs:
            if install.game == game and install.server_dir == server_dir:
                return install
        return None

    def remember(self, install: KnownInstall) -> None:
        """Add or replace the install with the same game + server dir."""
        self.installs = [
            i
            for i in self.installs
            if not (i.game == install.game and i.server_dir == install.server_dir)
        ]
        self.installs.append(install)

    def forget(self, game: str, server_dir: Path) -> None:
        """Drop an install (does not touch the server files)."""
        self.installs = [
            i for i in self.installs if not (i.game == game and i.server_dir == server_dir)
        ]


def state_path(config_dir: Path | None = None) -> Path:
    """`<config_dir>/state.json` (the real per-OS dir unless one is given)."""
    return (config_dir if config_dir is not None else platform.config_dir()) / STATE_FILE_NAME


def load_state(path: Path | None = None) -> AppState:
    """Read the state file; a missing file is an empty state.

    A broken file (bad JSON, or schema drift - `extra="forbid"` makes every
    future field fatal) must never stop the app from opening a window: it is
    moved aside as `state.json.broken` and an empty state is returned
    (review finding, 2026-08-21).
    """
    target = path if path is not None else state_path()
    if not target.is_file():
        return AppState()
    try:
        # `utf-8-sig`, not `utf-8`: a byte-order mark is legal in a UTF-8 file
        # and every Windows tool that writes one puts it there — PowerShell 5.1's
        # `Set-Content -Encoding utf8` and Notepad both do. Read as plain UTF-8
        # it raises "Unexpected UTF-8 BOM", so a hand-edited state file was
        # declared corrupt and moved aside, silently forgetting every remembered
        # install. Measured on the Win11 test VM, 2026-08-22. `utf-8-sig` reads
        # a file without a BOM identically, so nothing else changes.
        with target.open(encoding="utf-8-sig") as fh:
            return AppState.model_validate(json.load(fh))
    except (OSError, ValueError, ValidationError) as exc:
        backup = target.with_name(target.name + ".broken")
        try:
            target.replace(backup)
            logger.error(f"unreadable state file moved to {backup}: {exc}")
        except OSError:
            logger.error(f"unreadable state file left at {target}: {exc}")
        return AppState()


def save_state(state: AppState, path: Path | None = None) -> Path:
    """Write the state file atomically (tmp + replace) and return its path."""
    target = path if path is not None else state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)
    logger.debug(f"state saved: {target} ({len(state.installs)} install(s))")
    return target
