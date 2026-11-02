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
import os
import shutil
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
"""Yu'lon's own record of what was written: `{state_key: sha256 of the layout bytes}`.

Beside the scripts dir (`<config_dir>/gaming-mode/`, `gaming_mode.py`'s own
folder) rather than in the Steam profile, because Steam owns that tree and
Yu'lon's bookkeeping of its own writes does not belong in it. The state key
joins the Steam root, the account id, and the configset key, because two
libraries — two machines' roots, or two accounts on one — hold different
layout files under the same app name, and a sha recorded for one must never
answer for the other. A changed sha means Steam or the player edited the file
since — the layout is theirs now and a second press must leave it alone (a
Steam autosave rewrites the file on every player edit in the controller
editor).
"""
OLD_SUFFIX = ".yulon-old"
"""Where a replaced layout file waits while the press commits (T853 round 2, H1).

`apply()` moves each existing layout aside under this sibling before its
temp takes its name, then the configset, then the state; only after all
three land are the asides removed. A failure puts every aside back, so the
first key's file is restored when the second key's fails.
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
    prior_text: str | None = None
    """The whole configset file as read at plan time, when there was one."""


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


def _state_key(steam_root: Path, account_id: str, key: str) -> str:
    """The state record behind one configset key: root, account, and key.

    Keyed three ways because two Steam libraries — two machines' roots, or two
    accounts on one — hold different layout files under the same app name. A
    sha recorded for one must never answer for the other.
    """
    return f"{steam_root}\x1f{account_id}\x1f{key}"


def _read_configset(configset: Path) -> str | None:
    """The configset text, or None when there is no configset yet.

    Only a missing file means "not there yet". Every other read failure —
    permissions, a directory by that name, undecodable bytes — refuses before
    anything is written: treating what cannot be read as empty would replace
    the other games' entries with a fresh file holding only ours.
    """
    try:
        with configset.open("r", encoding="utf-8", errors="strict", newline="") as fh:
            return fh.read()
    except FileNotFoundError:
        return None
    except (IsADirectoryError, PermissionError, UnicodeDecodeError, OSError) as exc:
        raise steam.SteamRefusal(
            f"The controller configset {configset} cannot be read ({exc}). "
            "Yu'lon will not replace a file it cannot see — other games' "
            "layouts may live in it. Nothing was written."
        ) from exc


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
    matches the file (Steam or the player edited it), a layout file already
    there with no state record of our own write (ours is proven by the
    record, not by the marker in the file), or a layout file already there
    with no entry at all. Never anything else.
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
        text: str | None = _read_configset(configset)
        if text is not None:
            found = _find_key_entry(text, key)
            if found is not None:
                entry = found[2]
        if entry is None:
            if layout_file.is_file():
                action = "keep-player"
            else:
                action = "write"
        elif f'"{MARKER_KEY}"' not in entry:
            action = "keep-player"
        else:
            want = recorded.get(_state_key(steam_root, account_id, key))
            if want is None:
                action = "keep-player"
            else:
                action = "write"
                if layout_file.is_file():
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
                recorded_sha=recorded.get(_state_key(steam_root, account_id, key)),
                prior_text=text,
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


def _stage_key(
    steam_root: Path, account_id: str, key: str, existing: str | None
) -> tuple[str, bytes]:
    """The new configset text and the layout bytes for one `write` key.

    Raises `SteamRefusal` for a missing template or a configset whose
    `controller_config` map cannot be found — with nothing written.
    """
    payload = layout_bytes(steam_root=steam_root, account_id=account_id, key=key)
    new_text, _ = _configset_with_entry(existing or "", key)
    return new_text, payload


def planned_texts(input_plan: InputPlan) -> dict[str, tuple[str, bytes]]:
    """The staged work for every `write` key: the new configset text and the layout bytes.

    Rendered — and refused — BEFORE the first Steam write lands, so the caller
    (`steam.SteamShortcuts.add`) can validate the whole layout step while the
    shortcuts are still unwritten. A missing template or a configset whose
    `controller_config` map cannot be found raises `SteamRefusal` with nothing
    written. Stages from the plan's `prior_text` (taken at plan time);
    `apply()` re-reads the live file and renders again, so a configset that
    changed since the plan is never written from a stale read.
    """
    return {
        key_plan.key: _stage_key(
            input_plan.steam_root, input_plan.account_id, key_plan.key, key_plan.prior_text
        )
        for key_plan in input_plan.keys
        if key_plan.action == "write"
    }


def apply(
    input_plan: InputPlan,
    *,
    steam_running: bool = False,
) -> InputApplyReport:
    """Write every `write` key of `input_plan`; refuse while Steam runs.

    One transaction over ALL keys of the press. Two halves, in this order:

    1. Stage: render every key's layout to `<layout>.yulon-tmp`, the edited
       configset text to `<configset>.yulon-tmp`, the new state to its temp.
       The configset is re-read from disk (refusing an unreadable file
       exactly as `plan()` does — never treated as empty), so a configset
       that changed since the plan is never written from a stale read. Any
       failure here removes every temp and refuses with nothing committed.
    2. Commit: layout files first (each `os.replace` from its temp; an
       existing file is moved aside as `<layout>.yulon-old` until the end),
       then the configset (`os.replace`), then the state.

    On any failure in step 2: put back what was replaced (the configset
    from the backup `steam.py` already takes, old layouts from `.yulon-old`,
    new layout files removed), remove every temp, and raise `SteamRefusal`
    saying nothing of the layout was kept. On success remove the `.yulon-old`
    files. Backs up the configset first (`*.yulon-bak-<stamp>`, `steam.py`'s
    helper). Keeps every other byte: adds `"<key>" { "autosave" "1" "yulon"
    "1" }` inside `"controller_config"` (creating the file with the minimal
    wrapper if absent). Records the written sha256 in Yu'lon's own state
    beside the scripts dir, keyed by `(steam_root, account_id, key)`.
    `keep-player` keys are left exactly as they are.
    """
    if steam_running:
        raise steam.SteamRefusal(
            "Steam is running. Steam keeps the controller config in memory and rewrites "
            "it when it exits, so anything written now would be thrown away "
            "without a word. Quit Steam completely — check the tray — and press "
            "Add to Steam… again."
        )
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    app_name = input_plan.app_name
    kept = [key_plan.key for key_plan in input_plan.keys if key_plan.action != "write"]
    writes = [key_plan for key_plan in input_plan.keys if key_plan.action == "write"]
    backups: list[Path] = []
    if not writes:
        logger.info(f"steam input: {app_name}: wrote 0 layout(s), kept {len(kept)} player one(s)")
        return InputApplyReport(app_name=app_name, written=(), kept=tuple(kept), backups=())
    configsets = {key_plan.configset for key_plan in writes}
    if len(configsets) != 1:
        raise steam.SteamRefusal(
            "The controller configset this would write spans more than one file. "
            "Nothing of the layout was kept."
        )
    configset = next(iter(configsets))
    # Fresh read, not the plan's `prior_text`: the file may have changed
    # since the plan (a press plans, then writes other files, then
    # applies), and writing from a stale read would drop the other games'
    # entries. An unreadable file refuses here exactly as in `plan()`.
    live = _read_configset(configset)
    staged: list[tuple[KeyPlan, str, bytes]] = []
    acc = live or ""
    for key_plan in writes:
        payload = layout_bytes(
            steam_root=input_plan.steam_root,
            account_id=input_plan.account_id,
            key=key_plan.key,
        )
        acc, _ = _configset_with_entry(acc, key_plan.key)
        staged.append((key_plan, acc, payload))
    new_text = acc
    new_state_text: str | None = None
    try:
        state_raw: dict[str, Any] = {}
        if input_plan.state_file.is_file():
            loaded = json.loads(input_plan.state_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state_raw = loaded
        for key_plan, _, payload in staged:
            state_raw[_state_key(input_plan.steam_root, input_plan.account_id, key_plan.key)] = (
                hashlib.sha256(payload).hexdigest()
            )
        new_state_text = json.dumps(state_raw, indent=2, sort_keys=True) + "\n"
    except (OSError, ValueError) as exc:
        raise steam.SteamRefusal(
            "The controller layout was staged, but what was staged could not be "
            f"recorded ({exc}). Nothing of the layout was kept."
        ) from exc
    # Stage 1: every temp lands before anything is committed.
    layout_tmps: list[tuple[KeyPlan, Path]] = []
    aside: list[tuple[Path, Path]] = []
    created_layouts: list[Path] = []
    configset_tmp = configset.with_name(configset.name + steam.TMP_SUFFIX)
    state_tmp = input_plan.state_file.with_name(input_plan.state_file.name + steam.TMP_SUFFIX)
    assert new_state_text is not None
    # Codex round 3: the aside name is fixed, so one already there (left by an interrupted
    # commit, or anybody's) would be overwritten by the commit below and could be the only
    # copy of a player's layout. Refuse before anything is written instead.
    for key_plan in writes:
        old = key_plan.layout_file.with_name(key_plan.layout_file.name + OLD_SUFFIX)
        if os.path.lexists(old):
            raise steam.SteamRefusal(
                f"{old} is already there, and Yu'lon will not write over it: it may be the only "
                "copy of a controller layout. Move it somewhere safe and try again. "
                "Nothing of the layout was kept."
            )
    try:
        for key_plan in writes:
            key_plan.layout_file.parent.mkdir(parents=True, exist_ok=True)
        backup = steam._backup(configset, stamp)
        if backup is not None:
            backups.append(backup)
        for key_plan, _, payload in staged:
            layout_tmp = key_plan.layout_file.with_name(
                key_plan.layout_file.name + steam.TMP_SUFFIX
            )
            layout_tmp.write_bytes(payload)
            layout_tmps.append((key_plan, layout_tmp))
        try:
            configset_tmp.write_text(
                new_text, encoding="utf-8", errors="surrogateescape", newline=""
            )
        except (UnicodeDecodeError, UnicodeEncodeError, ValueError) as exc:
            raise steam.SteamRefusal(
                "The controller configset this would write is not plain text "
                f"Steam can read ({exc}). Nothing of the layout was kept."
            ) from exc
        input_plan.state_file.parent.mkdir(parents=True, exist_ok=True)
        state_tmp.write_text(new_state_text, encoding="utf-8")
    except steam.SteamRefusal:
        _drop_temps([tmp for _, tmp in layout_tmps] + [configset_tmp, state_tmp])
        raise
    except OSError as exc:
        _drop_temps([tmp for _, tmp in layout_tmps] + [configset_tmp, state_tmp])
        raise steam.SteamRefusal(
            steam.UNWRITABLE.format(path=configset.parent, reason=exc.strerror or exc)
            + " Nothing of the layout was kept."
        ) from exc
    # Stage 2: commit — layouts, then the configset, then the state.
    try:
        for key_plan, layout_tmp in layout_tmps:
            target = key_plan.layout_file
            if target.exists():
                old = target.with_name(target.name + OLD_SUFFIX)
                os.replace(target, old)
                aside.append((target, old))
            os.replace(layout_tmp, target)
            if not any(dst == target for dst, _ in aside):
                created_layouts.append(target)
        os.replace(configset_tmp, configset)
        os.replace(state_tmp, input_plan.state_file)
    except steam.SteamRefusal:
        _restore_commit(
            configset=configset,
            backups=backups,
            aside=aside,
            created=created_layouts,
            temps=[tmp for _, tmp in layout_tmps] + [configset_tmp, state_tmp],
        )
        raise
    except OSError as exc:
        _restore_commit(
            configset=configset,
            backups=backups,
            aside=aside,
            created=created_layouts,
            temps=[tmp for _, tmp in layout_tmps] + [configset_tmp, state_tmp],
        )
        raise steam.SteamRefusal(
            steam.UNWRITABLE.format(path=configset.parent, reason=exc.strerror or exc)
            + " Nothing of the layout was kept."
        ) from exc
    for _, old in aside:
        try:
            old.unlink(missing_ok=True)
        except OSError:
            logger.warning("steam input: could not remove %s", old, exc_info=True)
    written = tuple(key_plan.key for key_plan, _, _ in staged)
    logger.info(
        f"steam input: {app_name}: wrote {len(written)} layout(s), kept {len(kept)} player one(s)"
    )
    return InputApplyReport(
        app_name=app_name, written=written, kept=tuple(kept), backups=tuple(backups)
    )


def _drop_temps(temps: list[Path]) -> None:
    """Remove every staged temp; a failure to remove one is passed over."""
    for tmp in temps:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _restore_commit(
    *,
    configset: Path,
    backups: list[Path],
    aside: list[tuple[Path, Path]],
    created: list[Path],
    temps: list[Path],
) -> None:
    """Put back what a failed commit replaced: configset, layouts, temps gone."""
    _drop_temps(temps)
    if backups:
        try:
            shutil.copy2(backups[0], configset)
        except OSError:
            logger.warning("steam input: could not restore %s", configset, exc_info=True)
    else:
        try:
            configset.unlink(missing_ok=True)
        except OSError:
            pass
    for target in created:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
    for target, old in reversed(aside):
        try:
            os.replace(old, target)
        except OSError:
            logger.warning("steam input: could not restore %s", target, exc_info=True)


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
        state_key = _state_key(input_plan.steam_root, input_plan.account_id, key_plan.key)
        recorded: str | None = None
        try:
            raw = json.loads(input_plan.state_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get(state_key), str):
                recorded = str(raw[state_key])
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


def apply_for_game(
    steam_root: Path,
    account_id: str,
    app_name: str,
    *,
    config_dir: Path | None = None,
) -> InputApplyReport:
    """Plan and apply the layout for one GAME tile's `app_name`, as one step.

    What `steam.SteamShortcuts.add()` and `gaming_mode.add()` both do with
    `controller_layout=True`: refusal while Steam runs is the CALLER's (both
    refuse before anything is written), so this passes `steam_running=False`.
    Turning ConsolePort off later removes nothing: the player may like the
    layout on its own, and `remove()` stays available for the shortcut's
    removal path.
    """
    return apply(plan(steam_root, account_id, app_name, config_dir=config_dir), steam_running=False)


def consoleport_chosen(play_dir: Path | None) -> bool:
    """Whether the ready-to-play client at `play_dir` chose the ConsolePort pack.

    Read at press time, off the GUI thread like the rest of `add`: the record
    (`choices["packs"]["consoleport"]`) is the player's current answer, and
    None (no ready-to-play client) or no record means no layout.
    """
    if play_dir is None:
        return False
    try:
        from yulon import client_packs

        return bool(client_packs.read_record(play_dir).choices.get("packs", {}).get("consoleport"))
    except OSError:
        return False


def consoleport_module_installed(server_dir: Path) -> bool:
    """Whether this server installed the ConsolePort module into its own client (T855).

    Read at press time, off the GUI thread like `consoleport_chosen`, from the
    clone folders the Modules tab itself reads: `sql_scripts/clones/consoleport`
    is there exactly while the tab says Installed. A CoA server has no
    ready-to-play client and no pack record, so the module's own record is the
    switch the pack choice is on WotLK.
    """
    from yulon import apply as _apply

    try:
        return "consoleport" in _apply.installed_clones(server_dir).get("mod", frozenset())
    except OSError:
        return False


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
    "apply_for_game",
    "consoleport_chosen",
    "consoleport_module_installed",
    "layout_bytes",
    "layout_keys",
    "plan",
    "planned_texts",
    "remove",
    "report_sentence",
]
