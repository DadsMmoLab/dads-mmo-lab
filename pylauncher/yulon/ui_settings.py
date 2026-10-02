"""What Yu'lon's windows remember between launches: `<config_dir>/ui.json` (T187).

Today that is each server's client launcher window: where it was and how big
(`QWidget.saveGeometry()`, base64), and the realm addresses typed into it.

Its own file for `update_state.py`'s reason, not a field of `state.json`:
`AppState` is `extra="forbid"`, so an older build -- which is what a player has
after putting `.old` back -- would call a `state.json` carrying these fields
corrupt and open with no servers. Nothing in here is worth that. So this model
ignores keys it does not know, an unreadable file is an empty state that is
left in place, and the next save overwrites it.

No Qt here: the window turns its geometry into the string it hands over.

**Only addresses.** The history is cleaned on the way in AND out by the realm
box's own rule (`client_packs.clean_launcher`: letters, digits, dots, dashes and
colons), so a `user:password@host` someone pastes, or a whole line, can never be
written here. No account name and no password is ever handed to this module.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from yulon import client_packs, platform
from yulon.log import get_logger

logger = get_logger(__name__)

UI_SETTINGS_FILE_NAME = "ui.json"

ADDRESS_HISTORY = 5
"""How many typed realm addresses a launcher offers again, newest first."""

THIS_COMPUTER = "127.0.0.1"
"""The realm box's default (`controller_view.PLAY_CLIENT_ADDRESS`): always offered, not kept."""

STALE_TMP_SECONDS = 24 * 60 * 60
"""How old a leftover `ui.json.*.tmp` must be before a load may delete it.

`update_state.STALE_TMP_SECONDS`'s reason: old enough that it cannot belong to a
write still in flight, in this copy of Yu'lon or another."""

_LOCK = threading.RLock()
"""Held across every load-modify-save, for `update_state._LOCK`'s reason: one file, two writers
(a launcher's geometry and another launcher's address can be saved back to back)."""


def recent_addresses(addresses: Iterable[object]) -> list[str]:
    """`addresses` as the history keeps them: usable, not this computer, once each, five."""
    kept: list[str] = []
    for address in addresses:
        if not isinstance(address, str) or address == THIS_COMPUTER or address in kept:
            continue
        if "realm_address" not in client_packs.clean_launcher({"realm_address": address}):
            continue
        kept.append(address)
        if len(kept) == ADDRESS_HISTORY:
            break
    return kept


class LauncherPlace(BaseModel):
    """One server's launcher window: where it was, and the addresses typed into it."""

    model_config = ConfigDict(extra="ignore")

    geometry: str | None = None
    """`QWidget.saveGeometry()`, base64. None until the window was first closed."""
    addresses: list[str] = Field(default_factory=list)
    """Typed realm addresses, newest first (`recent_addresses`)."""

    @field_validator("addresses", mode="before")
    @classmethod
    def _only_addresses(cls, value: object) -> list[str]:
        return recent_addresses(value) if isinstance(value, list) else []


class UiSettings(BaseModel):
    """Everything `ui.json` holds."""

    model_config = ConfigDict(extra="ignore")

    launchers: dict[str, LauncherPlace] = Field(default_factory=dict)
    noticed_leftovers: list[str] = Field(default_factory=list)
    """T179: the temporary client copies Yu'lon has already said it could not remove,
    by folder, so the start-up notice is said once per folder and not at every start."""

    @field_validator("noticed_leftovers", mode="before")
    @classmethod
    def _only_paths(cls, value: object) -> list[str]:
        return (
            [str(item) for item in value if isinstance(item, str)]
            if isinstance(value, list)
            else []
        )


def launcher_key(game: str, server_dir: Path) -> str:
    """One server's entry: the same (game, server folder) pair its sidebar tab is keyed by."""
    return f"{game}|{server_dir}"


def ui_settings_path(config_dir: Path | None = None) -> Path:
    """`<config_dir>/ui.json` (the real per-OS dir unless one is given)."""
    return (config_dir if config_dir is not None else platform.config_dir()) / (
        UI_SETTINGS_FILE_NAME
    )


def load_ui_settings(path: Path | None = None) -> UiSettings:
    """Read `ui.json`; anything unreadable is an empty state, left where it is. Never raises.

    `utf-8-sig` for `state.py`'s reason: Windows tools write a byte-order mark.
    """
    target = path if path is not None else ui_settings_path()
    _sweep_stale_temporaries(target, time.time())
    try:
        with target.open(encoding="utf-8-sig") as fh:
            return UiSettings.model_validate(json.load(fh))
    except FileNotFoundError:
        return UiSettings()
    except (OSError, ValueError, ValidationError) as exc:
        logger.info(f"window settings at {target} unreadable, starting empty: {exc}")
        return UiSettings()


def _sweep_stale_temporaries(target: Path, now: float) -> None:
    """Delete `ui.json.*.tmp` files a day old: a save killed between its write and its
    rename left them, and nothing else comes back for one. Never raises."""
    try:
        for leftover in target.parent.glob(target.name + ".*.tmp"):
            try:
                if now - leftover.stat().st_mtime > STALE_TMP_SECONDS:
                    leftover.unlink(missing_ok=True)
            except OSError:  # gone already, or not ours to remove
                continue
    except OSError as exc:
        logger.debug(f"could not sweep temporary files beside {target}: {exc}")


def save_ui_settings(settings: UiSettings, path: Path | None = None) -> bool:
    """Write `ui.json` atomically through a unique temporary file. False = not written."""
    target = path if path is not None else ui_settings_path()
    tmp: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(dir=target.parent, prefix=target.name + ".", suffix=".tmp")
        os.close(handle)
        tmp = Path(name)
        tmp.write_text(settings.model_dump_json(indent=2) + "\n", encoding="utf-8")
        tmp.replace(target)
    except OSError as exc:
        logger.info(f"window settings not saved to {target}: {exc}")
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError as gone:  # the answer is still False, never a raise
                logger.info(f"could not remove the temporary {tmp}: {gone}")
        return False
    return True


def launcher_place(game: str, server_dir: Path, path: Path | None = None) -> LauncherPlace:
    """What one server's launcher remembers; an empty place when nothing is."""
    found = load_ui_settings(path).launchers.get(launcher_key(game, server_dir))
    return found if found is not None else LauncherPlace()


def remember_launcher(
    game: str,
    server_dir: Path,
    *,
    geometry: str | None = None,
    addresses: Iterable[str] | None = None,
    path: Path | None = None,
) -> bool:
    """Write the fields given onto what `ui.json` says right now. False = not written.

    Re-read inside the lock, so a geometry saved for one server never writes
    back a stale copy of another's addresses.
    """
    key = launcher_key(game, server_dir)
    with _LOCK:
        settings = load_ui_settings(path)
        place = settings.launchers.get(key, LauncherPlace())
        update: dict[str, object] = {}
        if geometry is not None:
            update["geometry"] = geometry
        if addresses is not None:
            update["addresses"] = recent_addresses(addresses)
        settings.launchers[key] = place.model_copy(update=update)
        return save_ui_settings(settings, path)


def forget_launcher(game: str, server_dir: Path, path: Path | None = None) -> bool:
    """Drop one server's entry (it was removed from Yu'lon). True when nothing is left of it."""
    key = launcher_key(game, server_dir)
    with _LOCK:
        settings = load_ui_settings(path)
        if key not in settings.launchers:
            return True
        del settings.launchers[key]
        return save_ui_settings(settings, path)


def first_leftover_notices(targets: Iterable[str], path: Path | None = None) -> list[str]:
    """The folders in `targets` not said before; all of `targets` remembered from now on.

    What is remembered is exactly the folders still left, so one that is removed and
    later left again is said again. If `ui.json` cannot be written, every folder is
    new -- a notice said twice is better than one never said.
    """
    left = list(dict.fromkeys(targets))
    with _LOCK:
        settings = load_ui_settings(path)
        fresh = [target for target in left if target not in settings.noticed_leftovers]
        if settings.noticed_leftovers != left:
            settings.noticed_leftovers = left
            if not save_ui_settings(settings, path):
                return left
    return fresh
