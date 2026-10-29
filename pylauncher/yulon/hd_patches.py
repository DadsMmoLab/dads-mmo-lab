"""Enable HD patches: the per-install choice the CoA launcher offers under Play (T828).

The Conquest of Azeroth client carries its HD texture and model patches in its own
files, and its executable switches them off when started as `<exe> -hd 0`. The official
launcher (v0.7.6) keeps a saved "Enable HD patches", on by default, and passes those
arguments when it is off. Yu'lon keeps the same choice per install, in
`<server>/.yulon-hd-patches.json` (`{schema_version, enabled}`), so it survives a
deleted or remade ready-to-play client and each server keeps its own.

**On is the default, and anything unreadable is on**: a missing record, one that is
not JSON, or one whose `enabled` is not a plain true/false. Off is only ever what the
player chose. The arguments themselves are the catalog's (`catalog.HdPatches`), never
spelled here, and an entry without them is never given any.

No Qt here.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from yulon.catalog.catalog import Client
from yulon.log import get_logger

logger = get_logger(__name__)

RECORD_FILE = ".yulon-hd-patches.json"
SCHEMA_VERSION = 1

LABEL = "Enable HD patches"
TIP = (
    "Turns the HD texture and model patches off for a lighter, original look; applies "
    "the next time you start the game."
)
STEAM_KEEPS_OLD = (
    "A Steam shortcut made earlier keeps the old choice until you use Add to Steam again."
)


def _record_path(server_dir: Path) -> Path:
    return server_dir / RECORD_FILE


def enabled(server_dir: Path) -> bool:
    """Whether this install starts the client with its HD patches: True unless saved off."""
    try:
        data = json.loads(_record_path(server_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    if not isinstance(data, dict):
        return True
    value = data.get("enabled")
    return value if isinstance(value, bool) else True


def save(server_dir: Path, on: bool) -> None:
    """Keep the choice, through a temp file renamed into place.

    Raises:
        OSError: it could not be written; the record is as it was.
    """
    payload = {"schema_version": SCHEMA_VERSION, "enabled": bool(on)}
    path = _record_path(server_dir)
    fd, name = tempfile.mkstemp(dir=server_dir, prefix=RECORD_FILE + ".", suffix=".tmp")
    os.close(fd)
    tmp = Path(name)
    try:
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def launch_args(client: Client, server_dir: Path) -> tuple[str, ...]:
    """The arguments Play adds to the client's executable: the catalog's off arguments
    when this install has HD patches off, else none."""
    if client.hd_patches is None or enabled(server_dir):
        return ()
    return client.hd_patches.args_when_off
