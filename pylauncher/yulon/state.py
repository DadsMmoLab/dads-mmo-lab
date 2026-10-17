"""Per-user app state under `platform.config_dir()` (README §11): the installs we know about.

This is app state, not server data: which games are installed where (and the
client folder the user pointed at), so the Controller can offer them. It is
deliberately tiny and typed; anything bigger (cached manifests, the log) has
its own file/dir next to it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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

    def keeping_unknown_fields_of(self, known: KnownInstall | None) -> KnownInstall:
        """This record, plus the fields `known` carries that this build does not know.

        For a caller that rebuilds a remembered install from its own arguments:
        `remember()` replaces the record, so without this a field a newer Yu'lon
        wrote would be dropped at the next save (T670 review round 2).
        """
        extra = (known.model_extra if known is not None else None) or {}
        if not extra:
            return self
        return KnownInstall.model_validate({**extra, **self.model_dump()})


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


_OLDER_READERS_KNOW = frozenset({"game", "server_dir", "client_dir", "wsl_distro"})
"""Every install field v0.6.59 to v0.9.12 read. Each refused any other (`extra="forbid"`),
so a file carrying one more is exactly what such a build set aside (T670)."""


def load_state(path: Path | None = None, *, repair: bool = True) -> AppState:
    """Read the state file; a missing file is an empty state.

    A broken file (bad JSON, or a known field this build cannot read) must never
    stop the app from opening a window: it is moved aside as `state.json.broken`
    and an empty state is returned (review finding, 2026-08-21). A field this
    build does not know is not damage, and is kept (T670).

    A `state.json.broken` that an OLDER Yu'lon set aside is put back (T670; see
    `_set_aside_by_an_older_build` for how it is told from any other). A server
    in both files is the `.broken` record with state.json's set fields laid on
    top: the older build may have re-attached it, but it cannot write a field
    it does not know (a ready-to-play client). Servers only one file names are
    kept. The merged state is saved first, and only then is `.broken` renamed
    `.restored`, so the servers come back once, a server forgotten afterwards
    stays forgotten, and a failed save leaves `.broken` for the next start. A
    state.json that is itself unreadable then goes to `.damaged`, never over the
    `.broken` being restored.

    `repair=False` does everything except write, and exists for exactly one
    caller: the run that PROVES a staged update opens (`YULON_SMOKE_TEST`). That
    run is a build which is not installed yet, started while the old one still
    is, with the player's real config directory in reach, so neither the move
    aside nor the put-back runs for it. It is logged either way (T90 plan 3,
    cold review 1).
    """
    target = path if path is not None else state_path()
    if not repair:
        return _read_state(target, aside=None)
    broken = target.with_name(target.name + ".broken")
    rescued = _set_aside_by_an_older_build(broken, target)
    if rescued is None:
        return _read_state(target, aside=broken)
    state = _merged(_read_state(target, aside=target.with_name(target.name + ".damaged")), rescued)
    try:
        save_state(state, target)
    except OSError as exc:
        logger.error(f"could not save the servers put back from {broken}; kept it: {exc}")
        return state
    logger.warning(
        f"put back the servers an older Yu'lon had set aside in {broken}: "
        + ", ".join(f"{i.game} at {i.server_dir}" for i in rescued.installs)
    )
    try:
        broken.replace(target.with_name(target.name + ".restored"))
    except OSError as exc:
        logger.error(f"could not retire {broken} after putting it back: {exc}")
    return state


def _merged(state: AppState, rescued: AppState) -> AppState:
    """state.json's servers, each laid over its `.broken` record; then `.broken`'s others."""
    others = {(i.game, i.server_dir): i for i in rescued.installs}
    installs = []
    for current in state.installs:
        older = others.pop((current.game, current.server_dir), None)
        installs.append(
            current
            if older is None
            else KnownInstall.model_validate(
                {**older.model_dump(), **current.model_dump(exclude_defaults=True)}
            )
        )
    return state.model_copy(update={"installs": [*installs, *others.values()]})


def _set_aside_by_an_older_build(broken: Path, target: Path) -> AppState | None:
    """`broken` as a state to put back, or None to leave it as it is.

    Put back only what a downgrade left (review round 2): a file that carries a
    field v0.6.59 to v0.9.12 refused, beside a state.json that no build able to
    read it has saved since. Anything else may name servers the player removed
    long ago (a `.broken` from before 2026-08-22 is a byte-order mark this build
    reads now), so it is left alone.

    "Saved since" is read off state.json's contents, not its time: a rename keeps
    the file's own mtime, so `.broken` carries the time the NEWER build saved it,
    and the older build's own save after the downgrade is always later. Every
    build up to v0.9.16 writes every field it knows on every install plus
    `schema_version`; this one leaves defaults out, `schema_version` too. So a
    state.json without `schema_version` is this build's or a later one's (it had
    this same chance and did not restore), and one whose installs carry every
    newer field `.broken` does was written by a build that could read it.
    """
    raw = _raw_json(broken)
    if raw is None:
        return None
    try:
        rescued = AppState.model_validate(raw)
    except ValidationError as exc:
        logger.info(f"{broken} is unreadable to this build too; left as it is: {exc}")
        return None
    newer = _install_keys(raw) - _OLDER_READERS_KNOW
    if not newer:
        logger.info(f"{broken} holds nothing an older Yu'lon refused; left as it is")
        return None
    current = _raw_json(target)
    if current is None:  # missing (the older build never saved) or unreadable
        return rescued
    if "schema_version" in current and (
        not current.get("installs") or not newer <= _install_keys(current)
    ):
        return rescued  # an older build's save: an empty list, or none of `newer` on it
    logger.info(f"{broken} is older than a list a newer Yu'lon saved since; left as it is")
    return None


def _raw_json(path: Path) -> dict[str, Any] | None:
    """`path`'s JSON object, or None if it is missing or not one."""
    try:
        with path.open(encoding="utf-8-sig") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _install_keys(raw: dict[str, Any]) -> set[str]:
    installs = raw.get("installs")
    if not isinstance(installs, list):
        return set()
    return {key for install in installs if isinstance(install, dict) for key in install}


def _read_state(target: Path, *, aside: Path | None) -> AppState:
    """state.json, or an empty state; an unreadable one is moved to `aside` (None: left)."""
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
        if aside is None:
            logger.error(f"unreadable state file left at {target} (no repair asked for): {exc}")
            return AppState()
        try:
            target.replace(aside)
            logger.error(f"unreadable state file moved to {aside}: {exc}")
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
    # THE RULE THIS SETS: a field's default must never change meaning, because a
    # saved file that omits the field means "the default", read by every build
    # since; a new meaning needs a new field. (`schema_version`'s absence is also
    # how `_set_aside_by_an_older_build` knows a file is this build's.)
    tmp.write_text(state.model_dump_json(indent=2, exclude_defaults=True) + "\n", encoding="utf-8")
    tmp.replace(target)
    logger.debug(f"state saved: {target} ({len(state.installs)} install(s))")
    return target
