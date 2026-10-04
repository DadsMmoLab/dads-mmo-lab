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
not import a controller package (the same shape as the import probe). The one
thing done here is forgetting older copies: only the last one per server is kept.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from yulon.log import get_logger

logger = get_logger(__name__)

SNAPSHOT_LABEL = "before-new-build"
"""What the copy's file names carry between their timestamp and their database.

`maintenance.backup(label=...)` writes `<stamp>_before-new-build_<database>.sql`,
so a copy the update took is never mistaken for a backup the player asked for, and
`prune_older()` can tell which files are the update's own to forget."""


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


def prune_older(directory: Path, keep: Sequence[Path]) -> tuple[Path, ...]:
    """Remove every update copy in `directory` that is not in `keep`. Never raises.

    The owner's rule of 2026-10-04: only the last copy per server is kept. Called
    once a copy is no longer the only good one there is -- after the new build came
    up, or after the copy went back -- never when a copy is taken: a copy that could
    not be put back is named in the sentence as the one to restore, and the next
    press must not remove it before the player has.

    Only the update's own files (`is_snapshot_file()`): a backup the player took,
    and a restore's safety copy, are left alone. A file that will not go is logged
    and left.
    """
    kept = {path.name for path in keep}
    removed: list[Path] = []
    try:
        found = sorted(directory.iterdir())
    except OSError as exc:
        logger.warning(f"could not list {directory} to forget older update copies: {exc}")
        return ()
    for path in found:
        if path.name in kept or not is_snapshot_file(path):
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
