"""Deleting and cleaning up old backups from the Maintenance tab (T604).

The backups folder, `<server>/sql_scripts/backups/`, only ever grows: every Back
up now, every update's copy, every Restore's safety copy and every Tortoise
outside item's copy lands there, and nothing took one out. This module is the
tab's way to do that without the player finding the folder by hand, and without
being able to remove the one file that would have saved the server.

**Plan, then act.** `read_shelf()` lists the folder and says, per file, what made
it, what it holds, whether it can be restored and what protects it.
`plan_delete()` and `plan_clean_up()` turn a choice into a `Plan` that names the
files together with the identity (device, inode, size, mtime) of each. Nothing is
removed until `carry_out()` has taken the maintenance lease, read the folder
AGAIN and found every named file unchanged and still unprotected: any difference
refuses the whole thing and removes nothing. A plan shown on screen is therefore
a statement about the folder at that moment, never a permission slip.

**What is never removed** (`kept_because`, each with a test):

* the newest good copy of every database. "Good" is a dump that passes
  `maintenance.verify_dump()` and is not another game's; a cut-short newest file
  or a file recorded for another game is not cover for the older one;
* the newest update copy set (`before-new-build`): the copy "Update the server"
  put back from;
* every file the restore marker names, and ALL files while the marker cannot be
  read (an unreadable marker is a restore in flight that names nothing);
* the earliest `before-<id>` set while `<id>` is installed: it is the undo a
  Remove names;
* anything with a second name (a hard link) -- removing a name frees nothing and
  the other name is not ours to judge;
* anything that is not a regular file directly inside the folder: no links, no
  sub-folders, no walk. A backups folder that leads out of the install is listed
  and never deleted from (`tuning.check_inside()`, the rule Tuning's saves obey).

A file marked read-only is deleted only by an explicit Delete on that file (the
flag is cleared, the file unlinked, and the flag put back if the unlink fails);
a Clean up never touches one.

The Backup button and this share `docker.maintenance_lease()`, so a delete cannot
run beside a dump that is adding to the folder, nor a dump start under a delete,
whether the other job is in this Yu'lon or another one (T568's reservation is
asked first, by `docker.reservation_holder()`, so the refusal can say who).

Not here, on purpose: conf `.bak` copies, record files, and the client pack
asides. They are not database dumps and the Backups box does not list them.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Literal

from yulon import apply, docker, forgetting, links, platform, tuning
from yulon.controller_wow_wotlk import maintenance
from yulon.controller_wow_wotlk.maintenance import MaintenanceError, backups_dir
from yulon.log import get_logger

logger = get_logger(__name__)

_STAMP_NAME = re.compile(r"^(?P<stamp>\d{8}_\d{6})_(?P<rest>.+)$")
_STEM = re.compile(r"^(?P<stamp>\d{8}_\d{6})(?:_(?P<rest>.*))?$")
_STAMP_FORMAT = "%Y%m%d_%H%M%S"

# Labels with a fixed spelling. `before-new-build` is matched here, BEFORE the
# `before-<id>` shape, or an update's copy would read as an item called "new-build".
_FIXED_LABELS = ("before-new-build", "after-new-build", "pre-restore", "move")
_ITEM_LABEL_LEAD = "before-"

Kind = Literal["dump", "partial", "gz"]
Identity = tuple[int, int, int, int]


class ShelfRefusal(MaintenanceError):
    """A delete or clean-up that did not happen, in a sentence for the player.

    A `MaintenanceError`, so the Maintenance tab shows it as it shows a refused
    restore.
    """


@dataclass(frozen=True)
class ShelfRow:
    """One file in the backups folder, and everything the tab says about it."""

    name: str
    kind: Kind
    size: int
    frees_bytes: int
    """What deleting it gives back: its size, or 0 for a file with another name."""
    identity: Identity
    made_at: datetime
    label: str | None
    """`None` for a backup the player asked for, else `pre-restore`, `before-new-build`,..."""
    item: str | None
    """The item id of a `before-<id>` copy, else None."""
    database: str | None
    usable: bool
    """Passes `verify_dump()` with a game record that reads: a restore could load it."""
    problem: str | None
    game: str | None
    """The game id the file records, None for one written before Yu'lon recorded it."""
    read_only: bool
    links: int
    kept_because: str | None
    cannot_delete: str | None
    named_by_yulon: bool = True
    """False for a `.sql` the player put there under a name of their own: listed, so Restore
    can still pick it, and never deleted by Yu'lon."""


@dataclass(frozen=True)
class Shelf:
    folder: Path
    rows: tuple[ShelfRow, ...]
    delete_refused: str | None = None
    """Set when the folder leads out of the install: listed, never deleted from."""
    folder_id: tuple[int, int] | None = None
    game_id: str | None = None


@dataclass(frozen=True)
class Rule:
    """What a Clean up selects. Any criterion left unset selects nothing by itself."""

    keep_newest: int | None = None
    """Keep this many of the newest good copies of each database; select the rest."""
    older_than_days: int | None = None
    include_unusable: bool = False
    """Also select cut-short copies, leftover `.partial` files and files Yu'lon cannot restore."""

    def __post_init__(self) -> None:
        if self.keep_newest is not None and self.keep_newest < 1:
            raise ValueError("Clean up keeps at least one copy of each database")
        if self.older_than_days is not None and self.older_than_days < 0:
            raise ValueError("A number of days cannot be negative")


@dataclass(frozen=True)
class Plan:
    names: tuple[str, ...]
    identities: Mapping[str, Identity]
    freed: int
    rule: Rule | None
    """None for a single Delete; the rule for a Clean up, re-run when it is carried out."""
    read_only: tuple[str, ...] = ()
    now: datetime | None = None
    folder_id: tuple[int, int] | None = None


@dataclass(frozen=True)
class Removed:
    names: tuple[str, ...]
    freed: int


# --------------------------------------------------------------------- reading

_UNSET: object = object()


def read_shelf(
    server_dir: Path,
    *,
    game_id: str | None,
    installed: Mapping[str, frozenset[str]] | None = None,
    marker: object = _UNSET,
) -> Shelf:
    """List the backups folder: top-level regular files only, newest first.

    Args:
        game_id: This server's game, so a file recorded for another game is known
            for what it is. None skips that check.
        installed: What `apply.installed_clones()` says is installed; read here
            when not given. A failed read protects every `before-<id>` copy.
        marker: The restore marker (`maintenance.interrupted_restore()`); read
            from the folder when not given.
    """
    folder = backups_dir(server_dir)
    refused = _outside(folder, server_dir)
    try:
        folder_st = os.stat(folder)
    except OSError:
        return Shelf(folder, (), refused, None, game_id)
    folder_id = (folder_st.st_dev, folder_st.st_ino)
    rows = _rows_in(folder, game_id)
    kept = _protections(
        server_dir,
        rows,
        game_id=game_id,
        installed=installed,
        marker=marker,
    )
    final: list[ShelfRow] = []
    for r in rows:
        reason = kept.get(r.name)
        final.append(replace(r, kept_because=reason, cannot_delete=_why_not(r, reason, refused)))
    final.sort(key=lambda r: (r.made_at, r.name), reverse=True)
    return Shelf(folder, tuple(final), refused, folder_id, game_id)


def _outside(folder: Path, server_dir: Path) -> str | None:
    try:
        tuning.check_inside(folder, server_dir)
    except tuning.TuningError as exc:
        return (
            "This backups folder leads out of the server's own folder, so Yu'lon will list "
            f"what is in it and will not delete from it. {exc}"
        )
    except OSError as exc:
        return (
            f"Yu'lon could not look at the backups folder ({exc}), so it will not delete from it."
        )
    return None


def _why_not(r: ShelfRow, kept: str | None, refused: str | None) -> str | None:
    if refused:
        return refused
    if not r.named_by_yulon:
        return (
            "Yu'lon did not make this file (its name is not one of Yu'lon's backup names), so "
            "it will not delete it. Delete it in your file manager if you want it gone."
        )
    if kept:
        return f"Yu'lon keeps this one: {kept}"
    if r.links > 1:
        return (
            "This file has another name on this disk, so deleting this one frees no space and "
            "the other name is not Yu'lon's to remove. Delete the other name instead."
        )
    return None


def _rows_in(folder: Path, game_id: str | None) -> list[ShelfRow]:
    rows: list[ShelfRow] = []
    try:
        entries = list(os.scandir(folder))
    except OSError as exc:
        logger.warning(f"could not list {folder}: {exc}")
        return rows
    for entry in entries:
        try:
            # `os.lstat`, not `entry.stat()`: on Windows the entry's look leaves st_ino, st_dev
            # and st_nlink at zero, and the identity and the second-name test are made of them.
            st = os.lstat(entry.path)
        except OSError:
            continue
        if links.stat_is_link(st) or not stat.S_ISREG(st.st_mode):
            continue
        kind = _kind_of(entry.name)
        if kind is None:
            continue
        rows.append(_row(Path(entry.path), entry.name, kind, st, game_id))
    return rows


def _kind_of(name: str) -> Kind | None:
    lowered = name.lower()
    if lowered.endswith(".sql.partial"):
        return "partial"
    if lowered.endswith(".sql.gz"):
        return "gz"
    if lowered.endswith(".sql"):
        return "dump"
    return None


def identity_of(st: os.stat_result) -> Identity:
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def _row(path: Path, name: str, kind: Kind, st: os.stat_result, game_id: str | None) -> ShelfRow:
    stem = re.sub(r"\.sql(\.partial|\.gz)?$", "", name, flags=re.IGNORECASE)
    stamped = _STEM.match(stem)
    made_at = datetime.fromtimestamp(st.st_mtime)
    rest = stem
    stamp_named = False
    if stamped is not None:
        try:
            made_at = datetime.strptime(stamped.group("stamp"), _STAMP_FORMAT)
            rest = stamped.group("rest") or ""
            stamp_named = True
        except ValueError:
            pass
    # A `.sql` under a name of the player's own is listed (Restore can pick it) and never
    # deleted; a leftover `.partial` and a wow-manage `.gz` are Yu'lon's to clear.
    named = stamp_named or kind != "dump"
    candidates = _candidates(rest) if kind != "gz" else [(None, rest)]
    usable, problem, game = False, None, None
    if kind == "partial":
        problem = "A backup that was cut short; it never became a copy that can be restored."
    elif kind == "gz":
        problem = "Compressed by wow-manage.sh; Yu'lon cannot restore it."
    else:
        try:
            maintenance.verify_dump(path)
            game = maintenance.backup_game(path)
            usable = True
        except MaintenanceError as exc:
            problem = str(exc)
    label, database = candidates[0]
    if usable:
        for cand_label, cand_db in candidates:
            try:
                maintenance.verify_dump(path, cand_db)
            except MaintenanceError:
                continue
            label, database = cand_label, cand_db
            break
    item = None
    if label is not None and label not in _FIXED_LABELS and label.startswith(_ITEM_LABEL_LEAD):
        item = label.removeprefix(_ITEM_LABEL_LEAD)
    read_only = not st.st_mode & stat.S_IWUSR
    return ShelfRow(
        name=name,
        kind=kind,
        size=st.st_size,
        frees_bytes=st.st_size if st.st_nlink <= 1 else 0,
        identity=identity_of(st),
        made_at=made_at,
        label=label,
        item=item,
        database=database if kind != "gz" and named else None,
        usable=usable,
        problem=problem,
        game=game,
        read_only=read_only,
        links=st.st_nlink,
        kept_because=None,
        cannot_delete=None,
        named_by_yulon=named,
    )


def _candidates(rest: str) -> list[tuple[str | None, str]]:
    """`(label, database)` readings of what follows the stamp, most likely first.

    A label never holds an underscore (the databases do), so it is the first word
    and only when it is a label Yu'lon writes. The fixed ones are tried before the
    `before-<id>` shape.
    """
    first, _, tail = rest.partition("_")
    readings: list[tuple[str | None, str]] = []
    if tail and (
        first in _FIXED_LABELS
        or (first.startswith(_ITEM_LABEL_LEAD) and len(first) > len(_ITEM_LABEL_LEAD))
    ):
        readings.append((first, tail))
    readings.append((None, rest))
    return readings


# ----------------------------------------------------------------- protections


def _foreign(r: ShelfRow, game_id: str | None) -> bool:
    return game_id is not None and r.game is not None and r.game != game_id


def _protections(
    server_dir: Path,
    rows: list[ShelfRow],
    *,
    game_id: str | None,
    installed: Mapping[str, frozenset[str]] | None,
    marker: object,
) -> dict[str, str]:
    kept: dict[str, str] = {}

    def keep(name: str, why: str) -> None:
        kept.setdefault(name, why)

    # 1. the newest good copy of every database
    newest: dict[str, ShelfRow] = {}
    for r in rows:
        if r.kind != "dump" or not r.usable or r.database is None or _foreign(r, game_id):
            continue
        best = newest.get(r.database)
        if best is None or (r.made_at, r.name) > (best.made_at, best.name):
            newest[r.database] = r
    for database, r in newest.items():
        keep(r.name, f"it is the newest good copy of {database}.")

    # 2. the newest update copy set
    updates = [r for r in rows if r.label == "before-new-build"]
    if updates:
        latest = max(r.made_at for r in updates)
        for r in updates:
            if r.made_at == latest:
                keep(
                    r.name, "it is the copy the last update took, kept so the update can be undone."
                )

    # 3. the restore marker
    record = maintenance.interrupted_restore(server_dir) if marker is _UNSET else marker
    if record is not None:
        if not getattr(record, "readable", True):
            for r in rows:
                keep(
                    r.name,
                    "a restore record in this folder cannot be read, so Yu'lon cannot tell "
                    "which backups an unfinished restore needs.",
                )
        else:
            named = {Path(str(record.backup)).name} | {  # type: ignore[attr-defined]
                Path(str(p)).name for p in record.safety_backup  # type: ignore[attr-defined]
            }
            for r in rows:
                if r.name in named:
                    keep(r.name, "an unfinished restore names it.")

    # 4. the earliest copy before an item that is installed
    items = {r.item for r in rows if r.item}
    if items:
        try:
            now_installed = (
                installed if installed is not None else apply.installed_clones(server_dir)
            )
            present = set().union(*now_installed.values()) if now_installed else set()
        except Exception as exc:  # noqa: BLE001 - "could not tell" protects, it never guesses
            logger.warning(f"could not read what is installed in {server_dir}: {exc}")
            for r in rows:
                if r.item:
                    keep(
                        r.name,
                        "Yu'lon could not read which items are installed, and this is the undo "
                        "copy of one.",
                    )
        else:
            for item in items & present:
                copies = [r for r in rows if r.item == item]
                earliest = min(r.made_at for r in copies)
                for r in copies:
                    if r.made_at == earliest:
                        keep(
                            r.name,
                            f"it was taken before {item} was installed, and {item} still is.",
                        )
    return kept


# ----------------------------------------------------------------------- plans


def plan_delete(shelf: Shelf, name: str) -> Plan:
    """The plan for deleting this one file, or the reason it cannot be."""
    found = next((r for r in shelf.rows if r.name == name), None)
    if found is None:
        raise ShelfRefusal(f"{name} is not in the backups list, so Yu'lon will not delete it.")
    if found.cannot_delete:
        raise ShelfRefusal(f"{found.name}: {found.cannot_delete} Nothing was deleted.")
    return Plan(
        names=(found.name,),
        identities={found.name: found.identity},
        freed=found.frees_bytes,
        rule=None,
        read_only=(found.name,) if found.read_only else (),
        folder_id=shelf.folder_id,
    )


def plan_clean_up(shelf: Shelf, rule: Rule, *, now: datetime | None = None) -> Plan:
    """The files `rule` selects among those Yu'lon is allowed to remove."""
    now = now or datetime.now()
    chosen = _select(shelf, rule, now)
    return Plan(
        names=tuple(r.name for r in chosen),
        identities={r.name: r.identity for r in chosen},
        freed=sum(r.frees_bytes for r in chosen),
        rule=rule,
        now=now,
        folder_id=shelf.folder_id,
    )


def _select(shelf: Shelf, rule: Rule, now: datetime) -> list[ShelfRow]:
    if shelf.delete_refused:
        return []
    # What a sweep may touch at all: not protected, not read-only, no second name,
    # and not recorded for another game (that one is for the player to judge by hand).
    allowed = {
        r.name
        for r in shelf.rows
        if r.cannot_delete is None and not r.read_only and not _foreign(r, shelf.game_id)
    }
    chosen: dict[str, ShelfRow] = {}
    if rule.keep_newest is not None:
        by_database: dict[str, list[ShelfRow]] = {}
        for r in shelf.rows:
            if r.kind == "dump" and r.usable and r.database and not _foreign(r, shelf.game_id):
                by_database.setdefault(r.database, []).append(r)
        for copies in by_database.values():
            copies.sort(key=lambda r: (r.made_at, r.name), reverse=True)
            for r in copies[rule.keep_newest :]:
                chosen[r.name] = r
    if rule.older_than_days is not None:
        cutoff = now - timedelta(days=rule.older_than_days)
        for r in shelf.rows:
            if r.usable and r.made_at < cutoff:
                chosen[r.name] = r
    if rule.include_unusable:
        for r in shelf.rows:
            if not r.usable:
                chosen[r.name] = r
    return sorted(
        (r for name, r in chosen.items() if name in allowed),
        key=lambda r: (r.made_at, r.name),
    )


# ------------------------------------------------------------------ carrying out


def carry_out(
    server_dir: Path,
    plan: Plan,
    *,
    game_id: str | None,
    installed: Mapping[str, frozenset[str]] | None = None,
    spec: docker.ContainerSpec | None = None,
    wsl_distro: str | None = None,
) -> Removed:
    """Remove what `plan` names, if the folder is still exactly as the plan saw it.

    Worker-thread work. The order matters and is the point: the reservation is
    asked about first (inspect only, so the refusal can name who holds the server),
    then the maintenance lease is taken, and only then is the folder read again and
    compared with the plan. Anything that differs refuses everything.

    Raises:
        ShelfRefusal: with nothing removed, or with the names of the files already
            removed when a later one would not go.
    """
    if not plan.names:
        return Removed((), 0)
    if spec is not None:
        holder = docker.reservation_holder(server_dir, wsl_distro=wsl_distro)
        if holder is not None and not holder.here:
            raise ShelfRefusal(f"{_holder_refusal(holder, server_dir)} Nothing was deleted.")
    try:
        with docker.maintenance_lease(
            server_dir,
            forgetting.DELETE_HOLDS_THE_DATABASES,
            press=forgetting.PRESS_DELETE_BACKUPS,
            spec=spec,
            wsl_distro=wsl_distro,
        ):
            return _remove_under_the_lease(server_dir, plan, game_id=game_id, installed=installed)
    except (docker.MaintenanceLeaseTaken, docker.ServerHeldError) as exc:
        raise ShelfRefusal(f"{exc} Nothing was deleted.") from exc


def _holder_refusal(holder: docker.ServerHolder, server_dir: Path) -> str:
    label = server_dir.name or "this server"
    if holder.ours and not holder.here and holder.live_here() is not True:
        return forgetting.server_reservation_left(
            label, holder.name, forgetting.PRESS_DELETE_BACKUPS
        )
    if not holder.known:
        return forgetting.server_reservation_unsaid(
            label, holder.name, forgetting.PRESS_DELETE_BACKUPS
        )
    return forgetting.server_busy_elsewhere(
        label, holder.press, holder.since(), holder.who, forgetting.PRESS_DELETE_BACKUPS
    )


def _changed(name: str | None = None) -> ShelfRefusal:
    what = f"{name} changed" if name else "The backups folder changed"
    return ShelfRefusal(
        f"{what} since you looked at it, so nothing was deleted. Look at the list again."
    )


def _remove_under_the_lease(
    server_dir: Path,
    plan: Plan,
    *,
    game_id: str | None,
    installed: Mapping[str, frozenset[str]] | None,
) -> Removed:
    fresh = read_shelf(server_dir, game_id=game_id, installed=installed)
    if fresh.delete_refused:
        raise ShelfRefusal(f"{fresh.delete_refused} Nothing was deleted.")
    if plan.folder_id is not None and plan.folder_id != fresh.folder_id:
        raise _changed()
    by_name = {r.name: r for r in fresh.rows}
    for name in plan.names:
        r = by_name.get(name)
        if r is None or name != os.path.basename(name) or r.identity != plan.identities.get(name):
            raise _changed(name)
        if r.cannot_delete:
            raise ShelfRefusal(f"{name}: {r.cannot_delete} Nothing was deleted.")
        if plan.rule is not None and r.read_only:
            raise _changed(name)
    if plan.rule is not None:
        again = plan_clean_up(fresh, plan.rule, now=plan.now)
        if set(again.names) != set(plan.names):
            raise _changed()
    done: list[str] = []
    freed = 0
    for name in plan.names:
        try:
            _remove_one(fresh, server_dir, by_name[name])
        except ShelfRefusal as exc:
            if done:
                raise ShelfRefusal(f"{exc} Removed before that: {', '.join(done)}.") from exc
            raise
        done.append(name)
        freed += by_name[name].frees_bytes
    logger.info(f"deleted {len(done)} backup file(s) from {fresh.folder}, freeing {freed} bytes")
    return Removed(tuple(done), freed)


def _remove_one(shelf: Shelf, server_dir: Path, row: ShelfRow) -> None:
    """Remove one listed file, after looking at it once more. The only place a file goes."""
    name = row.name
    path = shelf.folder / name
    if name != os.path.basename(name) or name in (".", "..") or "\\" in name:
        raise ShelfRefusal(f"{name} is not a file name Yu'lon will delete.")
    try:
        folder_st = os.stat(shelf.folder)
        st = os.lstat(path)
        tuning.check_inside(path, server_dir)
    except (OSError, tuning.TuningError) as exc:
        raise ShelfRefusal(
            f"{name} could not be looked at ({exc}), so it was not deleted."
        ) from exc
    if (folder_st.st_dev, folder_st.st_ino) != shelf.folder_id:
        raise _changed()
    if identity_of(st) != row.identity or links.stat_is_link(st) or not stat.S_ISREG(st.st_mode):
        raise _changed(name)
    if st.st_nlink > 1:
        raise ShelfRefusal(
            f"{name} has another name on this disk, so it was not deleted. Nothing was deleted."
        )
    mode = st.st_mode
    cleared = not mode & stat.S_IWUSR
    try:
        if cleared:
            os.chmod(path, mode | stat.S_IWUSR)
        os.unlink(path)
    except OSError as exc:
        if cleared:
            with contextlib.suppress(OSError):
                os.chmod(path, mode)
        raise ShelfRefusal(f"{name} could not be deleted ({exc}).") from exc


def clean_up(
    server_dir: Path,
    rule: Rule,
    *,
    game_id: str | None,
    installed: Mapping[str, frozenset[str]] | None = None,
    spec: docker.ContainerSpec | None = None,
    wsl_distro: str | None = None,
    now: datetime | None = None,
) -> Removed:
    """Plan a Clean up by `rule` from the folder as it is now and carry it out (worker thread)."""
    found = read_shelf(server_dir, game_id=game_id, installed=installed)
    plan = plan_clean_up(found, rule, now=now)
    return carry_out(
        server_dir, plan, game_id=game_id, installed=installed, spec=spec, wsl_distro=wsl_distro
    )


# --------------------------------------------------------- the automatic keep


_KEEP_DIR = "backup-keep"


def _keep_file(server_dir: Path) -> Path | None:
    claimed = docker.reservation_name(server_dir)
    if not claimed:
        return None
    ident = claimed.removeprefix(docker.SERVER_CLAIM_PREFIX)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", ident):
        return None
    return platform.config_dir() / _KEEP_DIR / f"{ident}.json"


def keep_setting(server_dir: Path) -> int | None:
    """How many copies of each database to keep after every backup; None, the default, is off.

    Stored in the app's config folder, per install, and not in `KnownInstall`: a
    setting about deleting files should not travel with a copied `state.json`.
    """
    path = _keep_file(server_dir)
    if path is None:
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    keep = raw.get("keep") if isinstance(raw, dict) else None
    if isinstance(keep, bool) or not isinstance(keep, int) or keep < 1:
        return None
    return keep


def set_keep_setting(server_dir: Path, keep: int | None) -> None:
    """Turn the automatic keep on (a number of copies, at least one) or off (None)."""
    if keep is not None and keep < 1:
        raise ValueError("Yu'lon keeps at least one copy of each database")
    path = _keep_file(server_dir)
    if path is None:
        raise ShelfRefusal(
            "This server has no id file yet, so Yu'lon has nowhere to remember the setting."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    if keep is None:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        return
    temp = path.with_suffix(".json.yulon-tmp")
    temp.write_text(json.dumps({"keep": keep}), encoding="utf-8")
    os.replace(temp, path)


# ------------------------------------------------------------- what the tab says

_MADE_BY = {
    None: "a backup you took",
    "pre-restore": "taken before a restore",
    "before-new-build": "taken before an update",
    "after-new-build": "the new build's data, taken as an update went back",
    "move": "taken for moving to another computer",
}


def what_made_it(r: ShelfRow) -> str:
    """The row's "what made it" column, in the player's words."""
    if r.kind == "partial":
        return "a backup that was cut short"
    if r.kind == "gz":
        return "made by wow-manage.sh"
    if r.item:
        return f"taken before {r.item} was installed"
    return _MADE_BY.get(r.label, f"labelled {r.label}")


def game_name(game_id: str | None) -> str:
    """The catalog's name for a game id, the id itself when this build has none."""
    if game_id is None:
        return "no game recorded"
    return _catalog_names().get(game_id, game_id)


@lru_cache(maxsize=1)
def _catalog_names() -> dict[str, str]:
    from yulon.catalog.catalog import load_catalog

    return {entry.id: entry.name for entry in load_catalog().games}


def describe(r: ShelfRow, game_id: str | None = None) -> str:
    """One line for the Backups list: date · database · size · what made it · game."""
    when = f"{r.made_at:%Y-%m-%d %H:%M}"
    size = f"{r.size / (1024 * 1024):.1f} MB"
    database = r.database or "-"
    game = game_name(r.game) if r.kind == "dump" else "-"
    if r.kind == "dump" and _foreign(r, game_id):
        game += " (another game)"
    parts = [when, database, size, what_made_it(r), game]
    if not r.usable and r.kind == "dump":
        parts.append("cut short or unreadable")
    return " · ".join(parts)


def size_text(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MB" if n >= 1024 * 1024 else f"{max(n, 0) / 1024:.0f} KB"


@dataclass(frozen=True)
class Seam:
    """What the Maintenance tab calls: this install's shelf, bound to its folder and game."""

    server_dir: Path
    game_id: str | None
    spec: docker.ContainerSpec | None = None
    wsl_distro: str | None = None

    def read(self) -> Shelf:
        return read_shelf(self.server_dir, game_id=self.game_id)

    def delete(self, plan: Plan) -> Removed:
        """Remove what `plan` names, if the folder is still as the plan saw it (worker)."""
        return carry_out(
            self.server_dir, plan, game_id=self.game_id, spec=self.spec, wsl_distro=self.wsl_distro
        )

    def keep(self) -> int | None:
        return keep_setting(self.server_dir)

    def set_keep(self, keep: int | None) -> None:
        set_keep_setting(self.server_dir, keep)

    def retain(self) -> Removed | None:
        """After a Back up now: the automatic keep, when the player turned it on (worker)."""
        keep = self.keep()
        if keep is None:
            return None
        return clean_up(
            self.server_dir,
            Rule(keep_newest=keep),
            game_id=self.game_id,
            spec=self.spec,
            wsl_distro=self.wsl_distro,
        )
