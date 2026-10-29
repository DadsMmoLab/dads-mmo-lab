"""Whether this server's Stop waits for the bots to be saved: the player's choice, kept (T826).

The owner, 2026-10-29: "Make a option when stopping the servers ... to wait save the bots que
(slower) and without waiting on the bots saving que (faster)." Live, a CoA Stop waited 2 to 25
minutes while its world wrote the bots' saves (`Waiting for N queries to finish`).

The choice lives in `<server>/.yulon-stop.json`, beside the server it is about, like the
Tuning tab's SQL-log answer (`sql_log_offer.py`): `{schema_version, waits_for_bot_saves}`. A
record that is missing or cannot be read is the catalog's default
(`StopWithoutBotSaves.waits_by_default`, which waits), so a broken file never makes a Stop
faster than the player asked for.

The choice reaches only the Stops the player presses (the Server tab's Stop and Restart, and
`--server stop`), through `Controller.stop(by_choice=True)`. Every Stop that needs the database
whole -- a backup's, an update's or a rebuild's, a move's, a realm switch's, a removal's --
waits for every save whatever this says (`docker.StopControl.skip_bot_saves`).

Nothing here imports Qt.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from yulon.log import get_logger

logger = get_logger(__name__)

RECORD_FILE = ".yulon-stop.json"
SCHEMA_VERSION = 1

WAIT_LABEL = "Wait for the bots to be saved (slower, safe)"
NO_WAIT_LABEL = "Do not wait for the bots to be saved (faster; bots may lose their latest changes)"
CHOICE_TITLE = "When stopping:"
CHOICE_NOTE = (
    "Players are always saved first. A backup, an update or rebuild, a realm switch, a move and "
    "a removal always wait for every save."
)
"""The line under the choice, for a screen with no hover (a Steam Deck)."""
CHOICE_NOT_KEPT = "Yu'lon could not keep this choice ({why}), so the Stop stays as it was."


def choice_tip(seconds: float) -> str:
    """What the choice does, for its tooltip and its line on the tab."""
    return (
        "Players are always saved first, either way. Not waiting stops the server "
        f"{int(seconds)} seconds after every player's save is written, so bots may lose what "
        "they did since their last save. A backup, an update or rebuild, a realm switch, a move "
        "and a removal always wait for every save, whatever this says, because they need the "
        "database complete."
    )


def _record_path(server_dir: Path) -> Path:
    return Path(server_dir) / RECORD_FILE


def waits_for_bot_saves(server_dir: Path, *, default: bool = True) -> bool:
    """The player's choice for this server: True waits for every save. `default` when unknown."""
    try:
        data = json.loads(_record_path(server_dir).read_text(encoding="utf-8"))
        value = data["waits_for_bot_saves"]
    except (OSError, ValueError, KeyError, TypeError):
        return default
    if not isinstance(value, bool):
        return default
    return value


def choose(server_dir: Path, waits: bool) -> None:
    """Keep the player's choice for this server, atomically (a temp file, then a rename).

    Raises:
        OSError: the record could not be written; the choice it replaces still stands.
    """
    payload = {"schema_version": SCHEMA_VERSION, "waits_for_bot_saves": bool(waits)}
    path = _record_path(server_dir)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=RECORD_FILE + ".", suffix=".tmp")
    os.close(fd)
    tmp = Path(name)
    try:
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    logger.info(f"{path}: the Stop {'waits' if waits else 'does not wait'} for the bots' saves")
