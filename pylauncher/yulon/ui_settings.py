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
import re
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


DEFAULT_THEME = "arthas"
"""The default UI theme (`yulon/ui/theme.py`)."""

VALID_THEMES: tuple[str, ...] = (
    "arthas",
    "gruvbox-dark",
    "gruvbox-light",
    "98x-light",
    "98x-dark",
    "ocean",
    "ocean-dark",
    "jungle",
    "jungle-dark",
    "floral",
    "floral-dark",
    "dracula-dark",
    "dracula-light",
)
"""Canonical theme IDs that `ui.json` may store."""

_THEME_ALIASES: dict[str, str] = {
    "98x": "98x-light",
}

_THEME_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def clean_theme(value: object, config_dir: Path | None = None) -> str:
    """Normalize `value` to a valid bundled or user theme ID, or `DEFAULT_THEME`."""
    if not isinstance(value, str):
        return DEFAULT_THEME
    cleaned = value.strip().lower()
    cleaned = _THEME_ALIASES.get(cleaned, cleaned)
    if cleaned in VALID_THEMES:
        return cleaned
    if _THEME_ID_RE.match(cleaned):
        base = config_dir if config_dir is not None else platform.config_dir()
        themes_dir = base / "themes"
        if (themes_dir / f"{cleaned}.json").is_file():
            return cleaned
        if themes_dir.is_dir():
            try:
                for path in sorted(themes_dir.glob("*.json")):
                    try:
                        raw = json.loads(path.read_text(encoding="utf-8-sig"))
                    except (OSError, ValueError):
                        continue
                    if isinstance(raw, dict):
                        raw_id = str(raw.get("id") or path.stem).strip().lower()
                        if raw_id == cleaned:
                            return cleaned
            except OSError:
                pass
    return DEFAULT_THEME


class LauncherPlace(BaseModel):
    """One server's launcher window: where it was, and the addresses typed into it."""

    model_config = ConfigDict(extra="ignore")

    geometry: str | None = None
    """`QWidget.saveGeometry()`, base64. None until the window was first closed."""
    addresses: list[str] = Field(default_factory=list)
    """Typed realm addresses, newest first (`recent_addresses`)."""
    account: str = ""
    """The account name Play last started a single-container server's client with (DDON's
    client takes it on its command line); "" until one was used."""

    @field_validator("addresses", mode="before")
    @classmethod
    def _only_addresses(cls, value: object) -> list[str]:
        return recent_addresses(value) if isinstance(value, list) else []


class FriendServer(BaseModel):
    """One saved friend server (T807): a game whose server is NOT installed here.

    The player picks a game, types a realm address and names the entry; the
    ready-to-play client Yu'lon made for it is remembered by its folder. `realm`
    is the address as typed; `host` and `auth_port` are what it parsed to.
    """

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1)
    game: str = Field(min_length=1)
    realm: str = Field(min_length=1)
    host: str = Field(min_length=1)
    auth_port: int = Field(ge=1, le=65535)
    client_folder: Path
    """The player's own client the ready-to-play copy was made from."""
    play_folder: Path
    """The ready-to-play client itself, marked for this friend server."""


class UiSettings(BaseModel):
    """Everything `ui.json` holds."""

    model_config = ConfigDict(extra="ignore")

    launchers: dict[str, LauncherPlace] = Field(default_factory=dict)
    friend_servers: tuple[FriendServer, ...] = ()
    """T807: the saved friend servers, newest last, one per name."""
    noticed_leftovers: list[str] = Field(default_factory=list)
    """T179: the temporary client copies Yu'lon has already said it could not remove,
    by folder, so the start-up notice is said once per folder and not at every start."""
    keep_in_tray: bool = True
    """T540: closing the window hides it into the tray (where a tray exists)."""
    tray_note: str = ""
    """T540: "never" once the player said not to show "Yu'lon stays in the tray" again."""
    theme: str = DEFAULT_THEME
    """The chosen UI theme (`yulon/ui/theme.py`); 'arthas' until changed in Settings."""

    @field_validator("noticed_leftovers", mode="before")
    @classmethod
    def _only_paths(cls, value: object) -> list[str]:
        return (
            [str(item) for item in value if isinstance(item, str)]
            if isinstance(value, list)
            else []
        )

    @field_validator("theme", mode="before")
    @classmethod
    def _only_valid_theme(cls, value: object) -> str:
        return clean_theme(value)


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
            raw = json.load(fh)
        if isinstance(raw, dict) and isinstance(raw.get("theme"), str):
            raw["theme"] = clean_theme(raw["theme"], config_dir=target.parent)
        settings = UiSettings.model_validate(raw)
        if isinstance(raw, dict) and isinstance(raw.get("theme"), str):
            object.__setattr__(
                settings, "theme", clean_theme(raw["theme"], config_dir=target.parent)
            )
        return settings
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
    account: str | None = None,
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
        if account is not None:
            update["account"] = account
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


def unnoticed_leftovers(targets: Iterable[str], path: Path | None = None) -> list[str]:
    """The folders in `targets` not yet shown in a notice; the record pruned to `targets`.

    Reads, and writes only to FORGET a folder that is no longer left, so one that is
    removed and later left again is said again. Nothing is marked as said here: that
    is `remember_leftover_notices()`, called when a notice is really on screen.
    """
    left = list(dict.fromkeys(targets))
    with _LOCK:
        settings = load_ui_settings(path)
        kept = [target for target in settings.noticed_leftovers if target in left]
        if kept != settings.noticed_leftovers:
            settings.noticed_leftovers = kept
            save_ui_settings(settings, path)
    return [target for target in left if target not in kept]


def remember_leftover_notices(targets: Iterable[str], path: Path | None = None) -> bool:
    """Mark these folders as said: a notice naming them was shown. False = not written."""
    with _LOCK:
        settings = load_ui_settings(path)
        added = [target for target in targets if target not in settings.noticed_leftovers]
        if not added:
            return True
        settings.noticed_leftovers = [*settings.noticed_leftovers, *added]
        return save_ui_settings(settings, path)


def remember_tray(
    *, keep_in_tray: bool | None = None, tray_note: str | None = None, path: Path | None = None
) -> bool:
    """T540: write the tray's two choices onto what `ui.json` says now. False = not written."""
    with _LOCK:
        settings = load_ui_settings(path)
        if keep_in_tray is not None:
            settings.keep_in_tray = keep_in_tray
        if tray_note is not None:
            settings.tray_note = tray_note
        return save_ui_settings(settings, path)


def friend_servers(path: Path | None = None) -> tuple[FriendServer, ...]:
    """The saved friend servers, in the order they are kept (T807). Never raises."""
    return load_ui_settings(path).friend_servers


def remember_friend_server(server: FriendServer, path: Path | None = None) -> bool:
    """Save one friend server under its name, replacing the one of that name (T807).

    False = not written (the file could not be saved; the window says so and the
    entry still works until the app closes).
    """
    with _LOCK:
        settings = load_ui_settings(path)
        kept = [one for one in settings.friend_servers if one.name != server.name]
        kept.append(server)
        settings.friend_servers = tuple(kept)
        return save_ui_settings(settings, path)


def forget_friend_server(name: str, path: Path | None = None) -> bool:
    """Drop one saved friend server by name (T807). True when it is not on the list any more."""
    with _LOCK:
        settings = load_ui_settings(path)
        kept = tuple(one for one in settings.friend_servers if one.name != name)
        if len(kept) == len(settings.friend_servers):
            return True
        settings.friend_servers = kept
        return save_ui_settings(settings, path)


def remember_theme(theme: str, path: Path | None = None) -> bool:
    """Write the chosen UI theme onto what `ui.json` says now. False = not written."""
    target = path if path is not None else ui_settings_path()
    with _LOCK:
        settings = load_ui_settings(target)
        object.__setattr__(settings, "theme", clean_theme(theme, config_dir=target.parent))
        return save_ui_settings(settings, target)
