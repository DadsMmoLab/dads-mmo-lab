"""The Tuning tab's one-line offer to stop an old server printing every SQL statement (T619).

#411 made a NEW Tortoise install write `LogFilter_SQLText = 1` into `mangosd.conf`, which
stops the world from printing each statement it runs into its log. A server installed
before that carries the dist's `0`. The install's conf stage would set the key, but it
only runs on an install resume, and neither Rebuild nor Update to latest runs it; before
this module the only route was Reset to default, which puts the whole file back and loses
the player's other tuning.

**Offered, never applied.** A `0` in the file is the same bytes whether the old install
wrote the dist's value or the player chose it, and nothing records which. So the tab asks,
once per file, and a "Keep it as it is" is as final as a "Turn it off": either answer is
kept in `<server>/.yulon-sql-log-offer.json` and the file is not asked about again, so a
player who wants the statements is not nagged (a record that cannot be read just asks
again; the question is harmless).

**Only what the SERVER reads as off is offered, by the server's own reading** (read at the
pinned core, tortoise-wow 187af788): `Config::GetBoolDefault` (`src/shared/Config/Config.cpp`)
is on for exactly `true`, `TRUE`, `yes`, `YES` and `1`, and off for anything else -- `on`,
`y`, `True`, `0`, `false`, `2`, an empty value -- so all of those are offered. A key with no
line takes `logFilterData`'s default for `sql_text` (`src/shared/Log.cpp`), which is false:
the statements print, so absent is offered too. When the key has several active lines in one
section the LAST wins (ACE's ini import overwrites, and `tuning.write` rewrites that line);
lines in different sections are ambiguous and left alone. The press re-reads the file, so a
value changed since the offer is not written over.

**Which files, and what value, come from the catalog's conf table**, not from a list here:
every file whose table carries `LogFilter_SQLText`, with the value the table wants. When
the login server's `realmd.conf` gets the key in the catalog, it is offered with no change
here.

The write is `tuning.write`: that key only, a backup of the file first, the file's line
endings and every other byte kept. Nothing here imports Qt.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from yulon import reset_defaults, tuning
from yulon.catalog import composegen
from yulon.catalog.catalog import CatalogEntry
from yulon.log import get_logger

logger = get_logger(__name__)

KEY = "LogFilter_SQLText"
RECORD_FILE = ".yulon-sql-log-offer.json"
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Offer:
    """One conf that could be told to stop printing the statements."""

    file: str
    """Relative to the server folder, as the Tuning tab spells it (`etc/mangosd.conf`)."""
    now: str
    """What the file says (`0`, `false`, ...), or empty when it has no such line."""


@dataclass(frozen=True)
class Done:
    """A file that was written, and the backup of what it said before."""

    file: str
    backup: Path


def wanted(entry: CatalogEntry) -> dict[str, str]:
    """Each conf this game's catalog table sets the key in -> the value it wants.

    Server-folder-relative paths, in table order. Empty for a game that does not set it.
    """
    table = reset_defaults._conf_table(entry)
    if table is None:
        return {}
    return {
        f"{composegen.SERVER_CONF_DIR}/{name}": patch.keys[KEY]
        for name, patch in table.files.items()
        if KEY in patch.keys
    }


def _record_path(server_dir: Path) -> Path:
    return server_dir / RECORD_FILE


def _answered(server_dir: Path) -> dict[str, str]:
    """Which files have been answered, and how. Anything unreadable reads as nothing."""
    try:
        data = json.loads(_record_path(server_dir).read_text(encoding="utf-8"))
        answered = data["answered"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    if not isinstance(answered, dict):
        return {}
    return {str(k): str(v) for k, v in answered.items()}


def _record(server_dir: Path, files: Iterable[str], how: str) -> None:
    answered = _answered(server_dir)
    answered.update({file: how for file in files})
    payload = {"schema_version": SCHEMA_VERSION, "answered": answered}
    path = _record_path(server_dir)
    tmp: Path | None = None
    try:
        fd, name = tempfile.mkstemp(dir=server_dir, prefix=RECORD_FILE + ".", suffix=".tmp")
        os.close(fd)
        tmp = Path(name)
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
        # Not asking again is a courtesy, never a safety: the offer is made again.
        logger.warning(f"could not record the answer about {KEY} in {path}: {exc}")


SERVER_READS_AS_ON = frozenset({"true", "TRUE", "yes", "YES", "1"})
"""The only spellings `Config::GetBoolDefault` reads as on at the pinned core (see above)."""


def _reading(path: Path) -> str | None:
    """What the server would read the key as: its value, `""` when the file has no line.

    `None` when the file cannot be read, or the key sits in more than one section (the
    server takes the first section that has it, which this does not try to guess).
    """
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError):
        return None
    section = ""
    found: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped[0] == "#":
            continue
        if stripped[0] == "[":
            section = stripped
            continue
        head, sep, tail = stripped.partition("=")
        if sep and head.strip() == KEY:
            found[section] = tail.strip().strip('"')
    if len(found) > 1:
        return None
    return next(iter(found.values()), "")


def _offer_for(server_dir: Path, file: str) -> Offer | None:
    value = _reading(server_dir / file)
    if value is None or value in SERVER_READS_AS_ON:
        return None
    return Offer(file, value)


def offers(entry: CatalogEntry, server_dir: Path) -> tuple[Offer, ...]:
    """The confs to offer the change for: not answered yet, and reading plainly off."""
    answered = _answered(server_dir)
    found = (_offer_for(server_dir, file) for file in wanted(entry) if file not in answered)
    return tuple(offer for offer in found if offer is not None)


def turn_off(entry: CatalogEntry, server_dir: Path, files: Iterable[str]) -> tuple[Done, ...]:
    """Write the key into each of `files` that is STILL offered; return what was written.

    Each file is judged again here, so one that now says on, or that is no longer a file
    this game sets the key in, is not written. The answer is recorded once all are
    written. A refusal from `tuning.write` (a link out of the server folder, a file it
    cannot read) is raised for the caller to say, and the files after it are not written.

    Raises:
        tuning.TuningError: the file is not one Yu'lon will write.
        OSError: the file could not be written.
    """
    want = wanted(entry)
    asked = set(files)
    done: list[Done] = []
    for file in want:
        if file not in asked or _offer_for(server_dir, file) is None:
            continue
        made = tuning.write(server_dir / file, {KEY: want[file]}, root=server_dir)
        done.append(Done(file, made))
        _record(server_dir, [file], "applied")
    return tuple(done)


def keep(entry: CatalogEntry, server_dir: Path, files: Iterable[str]) -> None:
    """The player's "keep it as it is": write no conf, and do not ask again for these files."""
    known = set(wanted(entry))
    _record(server_dir, [file for file in files if file in known], "kept")
