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
    """One server install the app manages.

    `extra="allow"`, not `"forbid"` (T670): a field this build does not know was
    written by a NEWER Yu'lon. Refusing it moved the whole file aside and opened
    with no servers, and every build up to v0.9.16 still does that to a file a
    newer one wrote. Allowed, it is read, carried, and written back as it was.
    """

    model_config = ConfigDict(extra="allow", frozen=True)

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
    play_client_dir: Path | None = None
    """This server's ready-to-play client (T181), if the player made one.

    A folder Yu'lon built from `client_dir` and marked as its own; once set,
    module client files go into it and never into `client_dir`. Optional with a
    default, so every `state.json` written before it existed still loads.
    """


class AppState(BaseModel):
    """Everything `state.json` holds. Unknown top-level keys are kept too (T670)."""

    model_config = ConfigDict(extra="allow")

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

    def installed_dirs(self) -> dict[str, Path]:
        """One folder per game — what the Catalog tab greys its Install button on.

        A dict and not the list, because the tile is per GAME while an install
        is per (game, folder): the tile has one button and one thing to say, so
        the shape it needs is the shape it gets rather than a comprehension
        written out at the one call site.

        Last remembered wins where a game has two. `remember()` appends, so that
        is the most recent one — and the tooltip built from this exists to answer
        "which install is this tile talking about", so the newest is the least
        surprising answer. Nothing is dropped from `installs`; both keep their
        tabs.
        """
        return {install.game: install.server_dir for install in self.installs}

    def forget(self, game: str, server_dir: Path) -> None:
        """Drop an install (does not touch the server files)."""
        self.installs = [
            i for i in self.installs if not (i.game == game and i.server_dir == server_dir)
        ]


def state_path(config_dir: Path | None = None) -> Path:
    """`<config_dir>/state.json` (the real per-OS dir unless one is given)."""
    return (config_dir if config_dir is not None else platform.config_dir()) / STATE_FILE_NAME


def load_state(path: Path | None = None, *, repair: bool = True) -> AppState:
    """Read the state file; a missing file is an empty state.

    A broken file (bad JSON, or a known field this build cannot read) must never
    stop the app from opening a window: it is moved aside as `state.json.broken`
    and an empty state is returned (review finding, 2026-08-21). A field this
    build does not know is not damage, and is kept (T670).

    A `state.json.broken` this build CAN read is put back (T670): it is what an
    older Yu'lon left after refusing a newer one's file (every build up to v0.9.16
    refused an unknown field). Its servers are added to those in `state.json`,
    where the file there already names one that wins, the result is saved, and
    the `.broken` file is renamed `.restored`, so it is put back once and a
    server forgotten afterwards stays forgotten.

    `repair=False` does everything except write, and exists for exactly one
    caller: the run that PROVES a staged update opens (`YULON_SMOKE_TEST`). That
    run is a build which is not installed yet, started while the old one still
    is, with the player's real config directory in reach, so neither the move
    aside nor the put-back runs for it. It is logged either way (T90 plan 3,
    cold review 1).
    """
    target = path if path is not None else state_path()
    if not repair:
        return _read_state(target, repair=False)
    broken = target.with_name(target.name + ".broken")
    rescued = _readable(broken)
    was_there = target.is_file()
    state = _read_state(target, repair=True)
    if rescued is None:
        return state
    moved_aside = was_there and not target.is_file()  # `broken` now holds THAT file
    known = {(i.game, i.server_dir) for i in state.installs}
    back = [i for i in rescued.installs if (i.game, i.server_dir) not in known]
    if back:
        state.installs = [*state.installs, *back]
        try:
            save_state(state, target)
        except OSError as exc:
            logger.error(f"could not save the servers put back from {broken}: {exc}")
            return state
        logger.warning(
            f"put back {len(back)} server(s) an older Yu'lon had set aside in {broken}: "
            + ", ".join(f"{i.game} at {i.server_dir}" for i in back)
        )
    if not moved_aside:
        try:
            broken.replace(target.with_name(target.name + ".restored"))
        except OSError as exc:
            logger.error(f"could not retire {broken} after reading it back: {exc}")
    return state


def _readable(path: Path) -> AppState | None:
    """`path` as a state this build reads, or None if it is missing or unreadable."""
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8-sig") as fh:
            return AppState.model_validate(json.load(fh))
    except (OSError, ValueError, ValidationError) as exc:
        logger.info(f"{path} is unreadable to this build too; left as it is: {exc}")
        return None


def _read_state(target: Path, *, repair: bool) -> AppState:
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
        if not repair:
            logger.error(f"unreadable state file left at {target} (no repair asked for): {exc}")
            return AppState()
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
    # Defaults are left out (T670): a build older than a field reads a record that
    # does not mention it, and refuses one that does (up to v0.9.16 every field was
    # `extra="forbid"`). So an install that uses no newer feature stays readable to
    # every older Yu'lon. Unknown fields a newer one wrote are always written back.
    tmp.write_text(state.model_dump_json(indent=2, exclude_defaults=True) + "\n", encoding="utf-8")
    tmp.replace(target)
    logger.debug(f"state saved: {target} ({len(state.installs)} install(s))")
    return target
