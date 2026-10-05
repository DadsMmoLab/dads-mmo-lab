"""The fingerprint a Windows world server's map-data copy is brought up to date by (T219).

On Windows a TrinityCore world server reads its map data from the install's
`world-data` volume instead of from the server folder's `data/` over Docker
Desktop's file share (`composegen.world_data_dirs()`), and its entrypoint copies
into the volume each folder whose line in `data/.yulon-world-data` differs from
the line the volume kept (`world-data-sync.sh`). This module writes that file.

`data/` stays the one place everything writes and checks -- the client-data stage,
the DBC overlay, the start check, the extraction evidence, the pathfinding job
and its tile count (whose resume check, T209 `mmaps._evidence()`, hashes the
HOST folder) -- so none of that changes. The volume is a copy of it.

**When.** Before every start Yu'lon makes: the Server tab's Start
(`Controller.start()`, the door every Start, Restart and Recreate goes through),
the install's `up`, a rebuild's recreate (Rebuild, Update to latest, Return to
the tested pin), the finish of a world update, and the pathfinding job's start.
A start Yu'lon does not make -- Docker's restart policy, a hand-run `compose up`
-- copies by the last fingerprint written, which is right unless `data/` was
changed by hand in between.

**What.** One `<folder> <sha256>` line per folder the entry names, the hash over
each file's relative path, size and modification time (metadata, not content:
about 0.45 s warm and 1.5 s cold for 20,643 files on yulon-win11, 2026-10-04).
`-` for a folder the server must see empty: one that is not there, and `mmaps`
until a pathfinding run is `done` (pathfinding is off until then anyway, and a
half-made set must not be copied). Written only when its bytes differ, so an
unchanged install copies nothing and keeps the file's time.

**Mirrored or not is read from the compose file**, never from the platform (T219
decision 6): a Windows install still on its old bind file gets no fingerprint
and no copy until Repair server files... rewrites it.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Sequence
from pathlib import Path

from yulon.catalog import composegen
from yulon.catalog.catalog import CatalogEntry
from yulon.log import get_logger

logger = get_logger(__name__)

FINGERPRINT_FILE = ".yulon-world-data"
"""In the server folder's `data/`, and the volume keeps its own copy of the lines it holds;
`world-data-sync.sh` spells the same name."""

DATA_DIR = "data"

EMPTY = "-"
"""A folder's line when the world server must see it empty."""

MMAPS_DIR = "mmaps"
"""The pathfinding job's folder (`mmaps.MMAPS_DIR`): copied only once a run is `done`."""

_DECLARED = re.compile(rf"^  {re.escape(composegen.WORLD_DATA_VOLUME)}:[ \t]*$", re.MULTILINE)


def mirrored(server_dir: Path) -> bool:
    """Does this install's base compose file declare the `world-data` volume?

    A two-space key under the top-level `volumes:`, as `composegen` writes it. An
    install with no base file, or one that cannot be read, is not mirrored.
    """
    try:
        text = (server_dir / composegen.BASE_FILE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return _DECLARED.search(text) is not None


def fingerprint(data_dir: Path, dirs: Sequence[str], mmaps_done: bool) -> str:
    """The fingerprint file's text for `data_dir`: one line per folder of `dirs`, in order.

    Raises:
        OSError: a folder that is there could not be listed, or a file in it looked at.
    """
    lines = []
    for name in dirs:
        if name == MMAPS_DIR and not mmaps_done:
            value = EMPTY
        else:
            value = _folder_hash(data_dir / name) or EMPTY
        lines.append(f"{name} {value}\n")
    return "".join(lines)


def _folder_hash(folder: Path) -> str | None:
    """sha256 over the sorted `path NUL size NUL mtime_ns` of every file below; None if absent.

    `os.scandir` because on Windows its `DirEntry.stat()` is answered from the
    folder listing, without opening each file. Links are not followed into folders.
    """
    if not folder.is_dir():
        return None
    entries: list[str] = []
    pending = [(folder, "")]
    while pending:
        here, prefix = pending.pop()
        with os.scandir(here) as listing:
            for entry in listing:
                relative = f"{prefix}{entry.name}"
                if entry.is_dir(follow_symlinks=False):
                    pending.append((Path(entry.path), f"{relative}/"))
                    continue
                stat = entry.stat()
                entries.append(f"{relative}\0{stat.st_size}\0{stat.st_mtime_ns}")
    digest = hashlib.sha256()
    for line in sorted(entries):
        digest.update(line.encode("utf-8", "surrogateescape") + b"\n")
    return digest.hexdigest()


def refresh(entry: CatalogEntry, server_dir: Path) -> str | None:
    """Write `data/.yulon-world-data` for a start, when this install is mirrored.

    Nothing for an entry with no `world_data_dirs` or an install whose compose file
    binds `data/`. Returns None, or -- when the file could not be worked out or
    written -- the sentence to show, after logging it. Never raises: a start goes
    on without it, and the copy then follows the last fingerprint written.
    """
    native = entry.install.native
    trinitycore = native.trinitycore if native is not None else None
    if trinitycore is None or not trinitycore.world_data_dirs or not mirrored(server_dir):
        return None
    path = server_dir / DATA_DIR / FINGERPRINT_FILE
    staged = path.with_name(path.name + ".yulon-new")
    try:
        text = fingerprint(
            server_dir / DATA_DIR, trinitycore.world_data_dirs, _mmaps_done(server_dir)
        )
        body = text.encode("utf-8")
        try:
            if path.read_bytes() == body:
                return None
        except FileNotFoundError:
            pass
        staged.write_bytes(body)
        os.replace(staged, path)
    except OSError as exc:
        try:
            staged.unlink(missing_ok=True)
        except OSError:
            pass
        said = (
            f"Yu'lon could not write {path} ({exc}), so the world server's copy of the map "
            "data may not be brought up to date at this start. Check that the server folder "
            "can be written, then start the server again."
        )
        logger.warning(said)
        return said
    logger.info(f"wrote {path} for the world server's map-data copy")
    return None


def _mmaps_done(server_dir: Path) -> bool:
    """Has a pathfinding run finished, by its record? Imported here: `mmaps` calls `refresh`."""
    from yulon.catalog.families import mmaps

    record = mmaps.read_record(server_dir)
    return record is not None and record.state == "done"
