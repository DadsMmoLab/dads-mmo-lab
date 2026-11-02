"""A Steam Input controller layout for the WoW game tile (Steam Deck).

The Deck's built-in pad (`controller_neptune`) reads no keyboard on its own;
the ConsolePort add-on drives the game from function keys and arrows. This
module writes the layout Steam selects for the game tile — the same Steam-closed
window and byte-preserving style as `steam.py`'s shortcut and compat-tool edits
— and nothing else:

* `layout_keys(app_name)`: the configset key(s) for a shortcut name.
* `plan(steam_root, account_id, app_name)`: what is there now, and what this
  would do, per key — `write` or `keep-player` and never anything else.
* `apply(plan, ...)`: the write, refused while Steam runs, backed up first,
  recorded in Yu'lon's own state so a player-edited file stays theirs.
* `remove(...)`: the undo, which only ever takes back our own entries/files.

Off Linux this is never reached (`add()` refuses first), and on Windows/macOS
the step is skipped by the caller. No Qt: this is the write half, and the tab
only shows its sentences.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from yulon import platform, resources, steam
from yulon.log import get_logger

logger = get_logger(__name__)

LAYOUT_FILE = "controller_neptune.vdf"
"""The layout file's name inside each `<key>/` folder: the Deck's own pad."""

CONFIGSET_FILE = "configset_controller_neptune.vdf"
"""The per-type configset that says which layout each game has selected."""

LAYOUT_TEMPLATE = Path("shared") / "steam-input" / "consoleport_controller_neptune.vdf"
"""The shipped layout text, beside `gaming_mode`'s script templates."""

MARKER_KEY = "yulon"
"""The extra configset key that marks an entry as ours (SRM's `srmAppId` precedent).

Steam tolerates extra keys inside a game's entry; the marker is what tells
`remove()` — and a second press — "Yu'lon wrote this, so Yu'lon may rewrite it".
An entry for our key WITHOUT it is the player's own choice and is never touched.
"""

STATE_FILE = "steam-input.json"
"""Yu'lon's own record of what was written: `{key: sha256 of the layout bytes}`.

Beside the scripts dir (`<config_dir>/gaming-mode/`, `gaming_mode.py`'s own
folder) rather than in the Steam profile, because Steam owns that tree and
Yu'lon's bookkeeping of its own writes does not belong in it. A changed sha
means Steam or the player edited the file since — the layout is theirs now and
a second press must leave it alone (a Steam autosave rewrites the file on every
player edit in the controller editor).
"""


def layout_keys(app_name: str) -> tuple[str, ...]:
    """The configset key(s) for a shortcut called `app_name`.

    The plain `app_name.lower()` with `:` removed, plus the SRM spelling — with
    `/\\?%*:|"<>.` stripped — when the two differ. Which punctuation beyond `:`
    Steam strips is the one point the sources disagree on (research §1: every
    source agrees only that `:` goes), so both keys are written rather than one
    guessed. The example is a name with `:` and `.`: the first key keeps the
    dot, the second drops it.
    """
    lowered = app_name.lower()
    plain = lowered.replace(":", "")
    stripped = "".join(c for c in lowered if c not in '/\\?%*:|"<>.')
    if stripped and stripped != plain:
        return (plain, stripped)
    return (plain,)


@dataclass(frozen=True)
class KeyPlan:
    """What one configset key holds now, and what this press would do with it."""

    key: str
    configset: Path
    layout_file: Path
    action: str
    """`write` (no entry, or ours and unedited) or `keep-player` (theirs)."""
    entry: str | None = None
    """The entry block as found, when there is one."""
    recorded_sha: str | None = None


@dataclass(frozen=True)
class InputPlan:
    """One press's work: a `KeyPlan` per configset key, and where state lives."""

    steam_root: Path
    account_id: str
    app_name: str
    keys: tuple[KeyPlan, ...]
    state_file: Path


def _read_state(state_file: Path) -> dict[str, str]:
    try:
        raw = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)}


def _entry_block(lines: list[str], start: int) -> tuple[int, str]:
    """The full `"<key>" { ... }` entry starting at `start`, and the line past it.

    The configset indents entries one tab inside `"controller_config"`: the key
    line itself carries no brace, so counting braces from the key line ends the
    block at the key's OWN opening brace. The block starts at the key line and
    runs through its closing brace's line.
    """
    for j in range(start + 1, len(lines)):
        if lines[j].strip() == "{":
            depth = 0
            for k in range(j, len(lines)):
                depth += lines[k].count("{") - lines[k].count("}")
                if depth <= 0:
                    return k + 1, "".join(lines[start : k + 1])
            return len(lines), "".join(lines[start:])
    return len(lines), "".join(lines[start:])


def _find_key_entry(text: str, key: str) -> tuple[int, int, str] | None:
    """The half-open line range and text of `"<key>" { ... }`, or None."""
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines[:-1]):
        if line.strip() == f'"{key}"' and lines[i + 1].strip() == "{":
            stop, block = _entry_block(lines, i)
            return i, stop, block
    return None


def plan(
    steam_root: Path,
    account_id: str,
    app_name: str,
    *,
    config_dir: Path | None = None,
) -> InputPlan:
    """What a press for `app_name` would do, per configset key.

    `write`: no entry for the key, or our own entry (`"yulon" "1"`) whose layout
    file's sha256 still equals the one Yu'lon's state recorded. `keep-player`:
    an entry for the key that is not ours, a recorded sha that no longer
    matches the file (Steam or the player edited it), or a layout file already
    there with no entry at all. Never anything else.
    """
    base = steam_root / "steamapps" / "common" / "Steam Controller Configs" / account_id / "config"
    state_file = (
        (config_dir if config_dir is not None else platform.config_dir())
        / "gaming-mode"
        / STATE_FILE
    )
    recorded = _read_state(state_file)
    keys: list[KeyPlan] = []
    for key in layout_keys(app_name):
        configset = base / CONFIGSET_FILE
        layout_file = base / key / LAYOUT_FILE
        entry: str | None = None
        text: str | None = None
        if configset.is_file():
            try:
                text = configset.read_text(encoding="utf-8", errors="surrogateescape")
            except OSError:
                text = None
            if text is not None:
                found = _find_key_entry(text, key)
                if found is not None:
                    entry = found[2]
        if entry is None:
            action = "keep-player" if layout_file.is_file() else "write"
        elif f'"{MARKER_KEY}"' not in entry:
            action = "keep-player"
        else:
            want = recorded.get(key)
            action = "write"
            if want is not None and layout_file.is_file():
                try:
                    have = hashlib.sha256(layout_file.read_bytes()).hexdigest()
                except OSError:
                    have = None
                if have != want:
                    action = "keep-player"
        keys.append(
            KeyPlan(
                key=key,
                configset=configset,
                layout_file=layout_file,
                action=action,
                entry=entry,
                recorded_sha=recorded.get(key),
            )
        )
    return InputPlan(
        steam_root=steam_root,
        account_id=account_id,
        app_name=app_name,
        keys=tuple(keys),
        state_file=state_file,
    )


def layout_bytes(
    *,
    steam_root: Path,
    account_id: str,
    key: str,
    installers_root: Path | None = None,
) -> bytes:
    """The shipped layout text with its markers filled for this machine and key."""
    template = installers_root if installers_root is not None else resources.installers_dir()
    template = template / LAYOUT_TEMPLATE
    try:
        text = template.read_text(encoding="utf-8")
    except OSError as exc:
        raise steam.SteamRefusal(
            f"The controller layout template could not be read: {exc}. Nothing was written."
        ) from exc
    if "@STEAM_ROOT@" not in text or "@ACCOUNT@" not in text or "@KEY@" not in text:
        raise steam.SteamRefusal(
            "The controller layout template is missing its markers. Nothing was written."
        )
    return (
        text.replace("@STEAM_ROOT@", str(steam_root))
        .replace("@ACCOUNT@", account_id)
        .replace("@KEY@", key)
        .encode("utf-8")
    )


def _configset_with_entry(text: str, key: str) -> tuple[str, str]:
    """`text` with `"<key>" { "autosave" "1" "yulon" "1" }` added; the block too.

    Line surgery in `compat_mapping()`'s style: every other byte is kept. Three
    cases: our entry is already there (replaced in place, byte-identical when
    nothing changed), the `controller_config` map holds other entries (ours
    goes at its end, before the map's closing brace), or the file is absent
    (the minimal wrapper). A file whose `controller_config` map cannot be found
    is refused rather than reformatted.
    """
    newline = "\r\n" if "\r\n" in text else "\n"
    ind = "\t"
    entry_block = (
        f'{ind}"{key}"{newline}{ind}{{{newline}'
        f'{ind}{ind}"autosave"{ind}{ind}"1"{newline}'
        f'{ind}{ind}"{MARKER_KEY}"{ind}{ind}"1"{newline}{ind}}}{newline}'
    )
    if not text:
        return (
            f'"controller_config"{newline}{{{newline}{entry_block}}}{newline}',
            entry_block,
        )
    lines = text.splitlines(keepends=True)
    found = _find_key_entry(text, key)
    if found is not None and f'"{MARKER_KEY}"' in found[2]:
        start, stop, _ = found
        return "".join(lines[:start] + [entry_block] + lines[stop:]), entry_block
    for i, line in enumerate(lines[:-1]):
        if line.strip() == '"controller_config"' and lines[i + 1].strip() == "{":
            depth = 0
            for j in range(i + 1, len(lines)):
                for ch in lines[j]:
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            return "".join(lines[:j] + [entry_block] + lines[j:]), entry_block
    raise steam.SteamRefusal(
        "The controller configset does not have the controller_config map "
        "this edit goes into; nothing was changed."
    )


@dataclass
class InputApplyReport:
    """What one `apply()` did: the keys written, and the keys left to the player."""

    app_name: str
    written: tuple[str, ...] = ()
    kept: tuple[str, ...] = ()
    backups: tuple[Path, ...] = ()


def apply(
    input_plan: InputPlan,
    *,
    steam_running: bool = False,
) -> InputApplyReport:
    """Write every `write` key of `input_plan`; refuse while Steam runs.

    Backs up the configset first (`*.yulon-bak-<stamp>`, `steam.py`'s helper).
    Writes the layout file (dir created), then edits the configset KEEPING EVERY
    OTHER BYTE: adds `"<key>" { "autosave" "1" "yulon" "1" }` inside
    `"controller_config"` (creating the file with the minimal wrapper if
    absent). Records the written sha256 in Yu'lon's own state beside the
    scripts dir. `keep-player` keys are left exactly as they are.
    """
    if steam_running:
        raise steam.SteamRefusal(
            "Steam is running. Steam keeps the controller config in memory and rewrites "
            "it when it exits, so anything written now would be thrown away "
            "without a word. Quit Steam completely — check the tray — and press "
            "Add to Steam… again."
        )
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    steam_root = input_plan.steam_root
    account_id = input_plan.account_id
    app_name = input_plan.app_name
    written: list[str] = []
    kept: list[str] = []
    backups: list[Path] = []
    for key_plan in input_plan.keys:
        if key_plan.action != "write":
            kept.append(key_plan.key)
            continue
        payload = layout_bytes(
            steam_root=steam_root,
            account_id=account_id,
            key=key_plan.key,
        )
        key_plan.layout_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = key_plan.layout_file.with_name(key_plan.layout_file.name + steam.TMP_SUFFIX)
        try:
            with key_plan.configset.open(
                "r", encoding="utf-8", errors="surrogateescape", newline=""
            ) as fh:
                existing = fh.read()
        except OSError:
            existing = ""
        new_text, _ = _configset_with_entry(existing, key_plan.key)
        try:
            backup = steam._backup(key_plan.configset, stamp)
            if backup is not None:
                backups.append(backup)
            tmp.write_bytes(payload)
            tmp.replace(key_plan.layout_file)
            compat_tmp = key_plan.configset.with_name(key_plan.configset.name + steam.TMP_SUFFIX)
            compat_tmp.write_text(new_text, encoding="utf-8", errors="surrogateescape", newline="")
            compat_tmp.replace(key_plan.configset)
        except OSError as exc:
            raise steam.SteamRefusal(
                steam.UNWRITABLE.format(path=key_plan.configset.parent, reason=exc.strerror or exc)
            ) from exc
        sha = hashlib.sha256(payload).hexdigest()
        try:
            state_raw: dict[str, Any] = {}
            if input_plan.state_file.is_file():
                loaded = json.loads(input_plan.state_file.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    state_raw = loaded
            state_raw[key_plan.key] = sha
            input_plan.state_file.parent.mkdir(parents=True, exist_ok=True)
            state_tmp = input_plan.state_file.with_name(
                input_plan.state_file.name + steam.TMP_SUFFIX
            )
            state_tmp.write_text(json.dumps(state_raw, indent=2, sort_keys=True) + "\n")
            state_tmp.replace(input_plan.state_file)
        except (OSError, ValueError) as exc:
            raise steam.SteamRefusal(
                f"The controller layout was written, but what was written could not be "
                f"recorded in {input_plan.state_file} ({exc}). Nothing else was changed."
            ) from exc
        written.append(key_plan.key)
    logger.info(
        f"steam input: {app_name}: wrote {len(written)} layout(s), kept {len(kept)} player one(s)"
    )
    return InputApplyReport(
        app_name=app_name, written=tuple(written), kept=tuple(kept), backups=tuple(backups)
    )


@dataclass(frozen=True)
class InputRemoveReport:
    """What one `remove()` took back: our entries and our unedited files only."""

    entries: tuple[str, ...] = ()
    files: tuple[Path, ...] = ()
    backups: tuple[Path, ...] = ()


def _remove_entry_block(text: str, key: str) -> str | None:
    """`text` with our `"<key>" { ... }` entry taken out; None when absent."""
    found = _find_key_entry(text, key)
    if found is None:
        return None
    start, stop, block = found
    if f'"{MARKER_KEY}"' not in block:
        return None
    lines = text.splitlines(keepends=True)
    return "".join(lines[:start] + lines[stop:])


def remove(input_plan: InputPlan) -> InputRemoveReport:
    """Take back only what Yu'lon wrote: our entries, and our unedited files.

    An entry without the `"yulon"` marker stays, whatever key it is under. A
    layout file whose sha256 no longer equals the recorded one stays too: the
    player edited it in Steam's editor since, so it is theirs now. Never
    raises for a missing file.
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    entries: list[str] = []
    files: list[Path] = []
    backups: list[Path] = []
    for key_plan in input_plan.keys:
        try:
            with key_plan.configset.open(
                "r", encoding="utf-8", errors="surrogateescape", newline=""
            ) as fh:
                text: str | None = fh.read()
        except OSError:
            text = None
        if text is not None and f'"{key_plan.key}"' in text:
            trimmed = _remove_entry_block(text, key_plan.key)
            if trimmed is not None and trimmed != text:
                try:
                    backup = steam._backup(key_plan.configset, stamp)
                    if backup is not None:
                        backups.append(backup)
                    tmp = key_plan.configset.with_name(key_plan.configset.name + steam.TMP_SUFFIX)
                    tmp.write_text(trimmed, encoding="utf-8", errors="surrogateescape", newline="")
                    tmp.replace(key_plan.configset)
                except OSError as exc:
                    raise steam.SteamRefusal(
                        steam.UNWRITABLE.format(
                            path=key_plan.configset.parent, reason=exc.strerror or exc
                        )
                    ) from exc
                entries.append(key_plan.key)
        recorded: str | None = None
        try:
            raw = json.loads(input_plan.state_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get(key_plan.key), str):
                recorded = str(raw[key_plan.key])
        except (OSError, ValueError):
            recorded = None
        if recorded is not None and key_plan.layout_file.is_file():
            try:
                have = hashlib.sha256(key_plan.layout_file.read_bytes()).hexdigest()
            except OSError:
                have = None
            if have == recorded:
                try:
                    backup = steam._backup(key_plan.layout_file, stamp)
                    if backup is not None:
                        backups.append(backup)
                    key_plan.layout_file.unlink()
                    files.append(key_plan.layout_file)
                except OSError as exc:
                    raise steam.SteamRefusal(
                        steam.UNWRITABLE.format(
                            path=key_plan.layout_file.parent, reason=exc.strerror or exc
                        )
                    ) from exc
    return InputRemoveReport(entries=tuple(entries), files=tuple(files), backups=tuple(backups))


def report_sentence(report: InputApplyReport) -> str:
    """The one line `steam`'s confirmation gets: written, or left alone."""
    if report.written:
        return (
            f"Steam Input: a ConsolePort controller layout for {report.app_name} "
            "(in game, type /cp recalibrate once)."
        )
    return (
        f"Steam Input: {report.app_name} already has a controller layout, "
        "so it was left as it is."
    )


__all__ = [
    "CONFIGSET_FILE",
    "LAYOUT_FILE",
    "LAYOUT_TEMPLATE",
    "InputApplyReport",
    "InputPlan",
    "InputRemoveReport",
    "KeyPlan",
    "STATE_FILE",
    "apply",
    "layout_bytes",
    "layout_keys",
    "plan",
    "remove",
    "report_sentence",
]
