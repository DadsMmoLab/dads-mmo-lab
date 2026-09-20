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
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from yulon import platform
from yulon.log import get_logger

logger = get_logger(__name__)

UPDATE_STATE_FILE_NAME = "update.json"


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
    """
    target = path if path is not None else update_state_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")
        tmp.replace(target)
    except OSError as exc:
        logger.info(f"update state not saved to {target}: {exc}")
        return False
    return True
