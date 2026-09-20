"""What the update check remembers between launches: `<config_dir>/update.json`.

Its own file, NOT a field of `state.json`. `AppState` is `extra="forbid"`, so a
build older than this one — which is exactly what a player has after putting
`.old` back — would call a `state.json` carrying update fields corrupt, move it
aside as `.broken`, and open with an empty list of installs. Nothing this file
holds is worth that.

For the same reason this model IGNORES keys it does not know (a newer build's
extra field must not make an older build throw the file away), and an unreadable
file is simply an empty state: everything in here can be asked of GitHub again,
so there is never anything to rescue and nothing is moved aside. The next save
overwrites it.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from yulon import platform
from yulon.log import get_logger

logger = get_logger(__name__)

UPDATE_STATE_FILE_NAME = "update.json"

_LOCK = threading.RLock()
"""Held across every load-modify-save of `update.json`. One file, two writers.

Two of them, and they are not hypothetical: the launch check runs on its own
`QThread` for up to five seconds, and in that window the player can press
"Check for updates" or "Skip this version" on a bar an earlier check put up.
Without this, the losing pattern is a read-modify-write race with the whole
file as the unit — the check reads the state, the player's skip is written,
the check writes back the copy it read, and the skip is gone. The app's own
tests found it before a user did (cold review, 2026-09-21).

An `RLock`, so `remember()` calling `load`/`save` under it is not a deadlock if
either ever grows a lock of its own. Process-wide only: two COPIES of Yu'lon
running at once are not serialised by it, and the unique temp name below is
what keeps that case from corrupting the file rather than merely racing it.
"""


class UpdateState(BaseModel):
    """Everything `update.json` holds."""

    model_config = ConfigDict(extra="ignore")

    last_checked: float = 0.0
    """When the feed was last asked for, as a wall-clock epoch. 0.0 = never."""
    etag: str | None = None
    """The feed's `ETag`, sent back as `If-None-Match` so a repeat costs no rate limit."""
    feed: str | None = None
    """The raw feed body, kept so a cached answer can be re-judged against the version
    running NOW — a user who updates by hand must not be told about an update they have."""
    skipped_version: str | None = None
    """The one tag the player pressed "Skip this version" on. A newer one un-hides the bar."""


def update_state_path(config_dir: Path | None = None) -> Path:
    """`<config_dir>/update.json` (the real per-OS dir unless one is given)."""
    return (
        config_dir if config_dir is not None else platform.config_dir()
    ) / UPDATE_STATE_FILE_NAME


def load_update_state(path: Path | None = None) -> UpdateState:
    """Read `update.json`; anything unreadable is an empty state. Never raises.

    `utf-8-sig`, for `state.py`'s reason: a byte-order mark is legal in a UTF-8
    file and every Windows tool that writes one puts it there, and read as plain
    UTF-8 it raises "Unexpected UTF-8 BOM".
    """
    target = path if path is not None else update_state_path()
    try:
        with target.open(encoding="utf-8-sig") as fh:
            return UpdateState.model_validate(json.load(fh))
    except FileNotFoundError:
        return UpdateState()
    except (OSError, ValueError, ValidationError) as exc:
        logger.info(f"update state at {target} unreadable, starting empty: {exc}")
        return UpdateState()


def save_update_state(state: UpdateState, path: Path | None = None) -> bool:
    """Write `update.json` atomically. False means it was not written; never raises.

    A failure here costs one more request to GitHub tomorrow, which is not worth
    a dialog and certainly not worth an exception out of a background thread —
    but the caller is told, so nothing reports a save that did not happen.

    The temporary file gets a **unique** name from `mkstemp` rather than the
    fixed `update.json.tmp` it had until the cold review of 2026-09-21. One
    name meant two writers filling the same file at once — the launch check on
    its thread and a Skip on the GUI thread — and then renaming it over the
    real one twice: the second rename moves a file the first writer had already
    moved, and what lands is a mixture of two JSON documents, which reads back
    as an empty state and loses both the skip and the cached feed. Under the
    lock this cannot happen in one process, and with a unique name it cannot
    happen between two copies of the app either.
    """
    target = path if path is not None else update_state_path()
    handle = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(dir=target.parent, prefix=target.name + ".", suffix=".tmp")
        os.close(handle)
        handle = None
        tmp = Path(name)
        tmp.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")
        tmp.replace(target)
    except OSError as exc:
        logger.info(f"update state not saved to {target}: {exc}")
        return False
    return True


@contextmanager
def update_lock() -> Iterator[None]:
    """Hold the one lock on `update.json` for a whole load-modify-save. See `_LOCK`."""
    with _LOCK:
        yield


def remember(fields: Mapping[str, object], path: Path | None = None) -> bool:
    """Write only `fields` onto whatever `update.json` says right now. Never raises.

    The read and the write are one operation, and the state is re-read INSIDE
    the lock rather than passed in: a caller that loaded the state, went to the
    network for five seconds and then saved its copy back would erase whatever
    the player did in between. Every writer of this file goes through here, and
    each of them names only the fields it owns.
    """
    with update_lock():
        current = load_update_state(path)
        return save_update_state(current.model_copy(update=dict(fields)), path)
