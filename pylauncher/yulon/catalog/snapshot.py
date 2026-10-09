"""The copy of the databases a new build can change, taken right before it first starts (T217).

"Update the server to latest…" and "Return to the tested pin…" roll a build back
when it does not come up. Until T217 the rollback put the old images back and
started them on the databases as the new build had left them: a WoW WotLK update
whose new mod-playerbots dropped two tables on its first start left the old
server crash-looping on "Table 'acore_playerbots.playerbots_speech' doesn't exist".

The update route now copies the databases the family names
(`StagedInstaller.databases_a_new_build_changes()`) with the servers stopped,
immediately before the new build's first start, and its rollback puts that copy
back before the old build starts again.

What lives here is the seam and its two records. The dump and the restore are the
Maintenance tab's own (`controller_wow_wotlk.maintenance.backup()` / `restore()`),
and `install_wiring.database_snapshot_for()` binds them, because `catalog/` must
not import a controller package (the same shape as the import probe). Two things
are done here: forgetting older copies (only the last one per server is kept), and
finding, for "Return to the tested pin…", the newest dump that is from before the
updates the tested commit does not ship (`copy_from_before()`, T630).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from yulon.catalog.installer import InstallerError
from yulon.log import get_logger

logger = get_logger(__name__)

SNAPSHOT_LABEL = "before-new-build"
"""What the copy's file names carry between their timestamp and their database.

`maintenance.backup(label=...)` writes `<stamp>_before-new-build_<database>.sql`,
so a copy the update took is never mistaken for a backup the player asked for, and
`prune_older()` can tell which files are the update's own to forget."""


ROLLBACK_SAFETY_LABEL = "after-new-build"
"""The label of the safety copy `maintenance.restore()` takes as the copy goes back.

It holds the databases as the new build left them, so nothing that build wrote
is lost. A label of its own, not the Maintenance tab's `pre-restore`, so
`prune_older()` can keep only the newest per database without touching a copy
the player's own Restore took (cold review of 23361ca3)."""


BACKUPS_FOLDER = Path("sql_scripts") / "backups"
"""Where every copy and backup of a server's databases lives, relative to its folder.

`controller_wow_wotlk.maintenance.BACKUP_SUBDIR`, spelled again because `catalog/`
must not import a controller package; a test pins the two equal (T630)."""

DUMP_TRAILER = b"-- Dump completed"
"""What mysqldump writes last; a dump without it was cut short (`maintenance._DUMP_TRAILER`)."""

UPDATES_TABLE = b"CREATE TABLE `updates`"
"""What a dump of an AzerothCore database holds for its update ledger (T630)."""

DUMP_READ_BYTES = 4 * 1024 * 1024
"""How much of a dump `copy_from_before()` reads at a time; a world dump is some hundreds of MB."""

_EDGE_BYTES = 8192

_STAMPED = re.compile(r"^\d{8}_\d{6}_")


@dataclass(frozen=True)
class Snapshot:
    """One copy, as taken: where it is, its files (one per database) and their databases."""

    directory: Path
    files: tuple[Path, ...]
    databases: tuple[str, ...]
    size_bytes: int = 0

    def named(self) -> str:
        """The files as the player finds them: `backups/<file>`, joined."""
        return ", ".join(f"{self.directory.name}/{path.name}" for path in self.files)


@dataclass(frozen=True)
class PutBack:
    """What putting a copy back did: the databases restored, and the restore's own safety copies.

    The safety copies are what the databases held just before the copy went back
    (`maintenance.restore()` dumps them first), so what the new build wrote is kept
    rather than lost.
    """

    restored: tuple[str, ...]
    safety: tuple[Path, ...] = ()


class CopyNotUsable(InstallerError):
    """The copy failed its check before anything was dropped or loaded (cold review, T217).

    Cut short, or holding no table list: nothing in the databases was touched, and
    the copy is not one Maintenance's Restore would accept either, so the sentence
    must not send the player there with it.
    """


class DatabaseSnapshot(Protocol):
    """Take a copy of named databases, put it back, and forget the older copies.

    Each call raises `InstallerError` with the sentence a player reads when it
    cannot do what it says; it never half-reports success.
    """

    def take(self, server_dir: Path, databases: Sequence[str]) -> Snapshot:
        """Dump `databases` into the server's backups folder, starting the database if down."""
        ...

    def put_back(self, server_dir: Path, snapshot: Snapshot) -> PutBack:
        """Restore every file of `snapshot`; the world and login servers must be stopped."""
        ...

    def prune(self, server_dir: Path, snapshot: Snapshot) -> tuple[Path, ...]:
        """Remove every older copy the update took; keep `snapshot`. Returns what was removed."""
        ...


def is_snapshot_file(path: Path) -> bool:
    """A dump an update took before a new build started: `<stamp>_before-new-build_<db>.sql`."""
    return path.suffix == ".sql" and f"_{SNAPSHOT_LABEL}_" in path.name


def _rollback_safety_database(path: Path) -> str | None:
    """The database of a rollback safety copy (`<stamp>_after-new-build_<db>.sql`), else None."""
    marker = f"_{ROLLBACK_SAFETY_LABEL}_"
    if path.suffix != ".sql" or marker not in path.name:
        return None
    return path.stem.split(marker, 1)[1]


def older_copies(directory: Path, keep: Sequence[Path]) -> tuple[Path, ...]:
    """The update copies in `directory` other than `keep`, newest first. Never raises.

    What a rollback whose old build did not come up names as kept (T217): a
    backup the player took, or a restore's safety copy, is not one of them.
    """
    kept = {path.name for path in keep}
    try:
        found = sorted(directory.iterdir())
    except OSError:
        return ()
    older = [path for path in found if path.name not in kept and is_snapshot_file(path)]
    return tuple(reversed(older))


def prune_older(directory: Path, keep: Sequence[Path]) -> tuple[Path, ...]:
    """Remove every update copy in `directory` that is not in `keep`. Never raises.

    The owner's rule of 2026-10-04: only the last copy per server is kept. Called
    once a copy is no longer the only good one there is -- after the new build came
    up, or after the copy went back -- never when a copy is taken: a copy that could
    not be put back is named in the sentence as the one to restore, and the next
    press must not remove it before the player has.

    The rollback's own safety copies (`ROLLBACK_SAFETY_LABEL`) go the same way:
    the newest per database is kept, every older one removed. The timestamp
    leads the name, so the newest is the last in name order.

    Only the update's own files: a backup the player took, and a Maintenance-tab
    restore's `pre-restore` safety copy, are left alone. A file that will not go
    is logged and left.
    """
    kept = {path.name for path in keep}
    removed: list[Path] = []
    try:
        found = sorted(directory.iterdir())
    except OSError as exc:
        logger.warning(f"could not list {directory} to forget older update copies: {exc}")
        return ()
    newest_safety: dict[str, str] = {}
    for path in found:
        database = _rollback_safety_database(path)
        if database is not None:
            newest_safety[database] = path.name
    for path in found:
        database = _rollback_safety_database(path)
        if database is not None:
            if path.name == newest_safety[database]:
                continue
        elif path.name in kept or not is_snapshot_file(path):
            continue
        try:
            path.unlink()
        except OSError as exc:
            logger.warning(f"could not remove the older update copy {path}: {exc}")
            continue
        removed.append(path)
    if removed:
        logger.info(f"forgot {len(removed)} older update copy file(s) in {directory}")
    return tuple(removed)


def copy_from_before(directory: Path, database: str, updates: Sequence[str]) -> Path | None:
    """The newest complete dump of `database` in `directory` naming none of `updates`; never raises.

    What "Return to the tested pin…" names when the database already has updates the
    tested commit does not ship (T630): the way back is a copy from before them. Read
    from the dump itself, not from its label or its time: the copy an update kept is
    from before THAT update, which after a second update is not before the first, and a
    restore of a copy that still holds them would cost the player everything since and
    open nothing. Any label counts (a backup the player took, an update's copy, a
    restore's safety copy); a dump that was cut short, or that has no `updates` table,
    is not one a restore can be sent to. None when there is no such dump or the folder
    cannot be read.
    """
    try:
        found = sorted(
            path
            for path in directory.iterdir()
            if _STAMPED.match(path.name) and path.name.endswith(f"_{database}.sql")
        )
    except OSError:
        return None
    for path in reversed(found):
        if _holds_none_of(path, updates):
            return path
    return None


def _holds_none_of(path: Path, updates: Sequence[str]) -> bool:
    """A complete dump with an `updates` table whose rows name none of `updates`. Never raises.

    mysqldump quotes each row's name (`'2026_09_21_00_playerbots_speech.sql'`), so the
    quoted name is looked for, across the seams between reads.
    """
    needles = [f"'{name}'".encode() for name in updates]
    # The tail kept between reads: longer than any name, and holding the trailer line
    # at the end (`maintenance._EDGE_BYTES`'s 8 KiB).
    keep = max([_EDGE_BYTES, *(len(needle) for needle in needles)])
    carry = b""
    table = False
    try:
        with path.open("rb") as dump:
            while True:
                piece = dump.read(DUMP_READ_BYTES)
                if not piece:
                    break
                window = carry + piece
                if any(needle in window for needle in needles):
                    return False
                table = table or UPDATES_TABLE in window
                carry = window[-keep:]
    except OSError as exc:
        logger.warning(f"could not read {path} to see which updates it holds: {exc}")
        return False
    return table and DUMP_TRAILER in carry
