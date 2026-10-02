"""What the windows remember between launches: `<config_dir>/ui.json` (T187).

Its own file for `update_state.py`'s reason: nothing here is worth a
`state.json` moved aside by an older build that does not know the field.
"""

from __future__ import annotations

import json
from pathlib import Path

from yulon import platform, ui_settings
from yulon.ui_settings import (
    ADDRESS_HISTORY,
    LauncherPlace,
    forget_launcher,
    launcher_place,
    load_ui_settings,
    recent_addresses,
    remember_launcher,
    ui_settings_path,
)

SERVER = Path("/srv/wotlk")


def test_the_file_lives_in_the_config_dir_beside_state_json(tmp_path: Path) -> None:
    assert ui_settings_path(tmp_path) == tmp_path / "ui.json"
    assert ui_settings_path() == platform.config_dir() / "ui.json"


def test_nothing_remembered_is_an_empty_place(tmp_path: Path) -> None:
    place = launcher_place("wow-wotlk", SERVER, tmp_path / "ui.json")
    assert place == LauncherPlace()
    assert place.geometry is None and place.addresses == []


def test_a_launchers_geometry_and_addresses_are_kept_per_server(tmp_path: Path) -> None:
    path = tmp_path / "ui.json"
    assert remember_launcher("wow-wotlk", SERVER, geometry="AAAA", path=path)
    assert remember_launcher("wow-wotlk", SERVER, addresses=["10.0.0.5"], path=path)
    assert remember_launcher("wow-tbc", SERVER, geometry="BBBB", path=path)

    wotlk = launcher_place("wow-wotlk", SERVER, path)
    assert wotlk.geometry == "AAAA", "the second write lost the first's field"
    assert wotlk.addresses == ["10.0.0.5"]
    assert launcher_place("wow-tbc", SERVER, path).geometry == "BBBB"
    assert launcher_place("wow-wotlk", Path("/srv/other"), path) == LauncherPlace()


def test_forgetting_a_server_takes_only_its_own_entry(tmp_path: Path) -> None:
    path = tmp_path / "ui.json"
    remember_launcher("wow-wotlk", SERVER, geometry="AAAA", addresses=["lan.host"], path=path)
    remember_launcher("wow-tbc", SERVER, geometry="BBBB", path=path)

    assert forget_launcher("wow-wotlk", SERVER, path)

    assert launcher_place("wow-wotlk", SERVER, path) == LauncherPlace()
    assert launcher_place("wow-tbc", SERVER, path).geometry == "BBBB"
    assert "lan.host" not in path.read_text(encoding="utf-8")


def test_an_unreadable_file_is_an_empty_state_and_is_left_in_place(tmp_path: Path) -> None:
    path = tmp_path / "ui.json"
    path.write_text("{ not json", encoding="utf-8")
    assert load_ui_settings(path).launchers == {}
    assert path.read_text(encoding="utf-8") == "{ not json"


def test_a_newer_builds_extra_keys_do_not_cost_the_rest(tmp_path: Path) -> None:
    path = tmp_path / "ui.json"
    key = ui_settings.launcher_key("wow-wotlk", SERVER)
    path.write_text(
        "﻿"
        + json.dumps(
            {"future": 1, "launchers": {key: {"geometry": "AAAA", "zoom": 2, "addresses": []}}}
        ),
        encoding="utf-8",
    )
    assert launcher_place("wow-wotlk", SERVER, path).geometry == "AAAA"


def test_a_save_that_cannot_be_written_answers_false_and_never_raises(tmp_path: Path) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("", encoding="utf-8")
    assert not remember_launcher("wow-wotlk", SERVER, geometry="AAAA", path=blocker / "ui.json")
    assert list(tmp_path.iterdir()) == [blocker], "a temporary file was left behind"


def test_the_history_is_the_last_five_newest_first_and_only_addresses() -> None:
    typed = ["a.lan", "b.lan", "c.lan", "d.lan", "e.lan", "f.lan"]
    assert recent_addresses(typed) == typed[:ADDRESS_HISTORY]
    assert ADDRESS_HISTORY == 5
    assert recent_addresses(["a.lan", "a.lan", "b.lan"]) == ["a.lan", "b.lan"]
    # This computer is the default, never history; nothing the realm box refuses
    # is kept, so no `user:secret@host` or a pasted line can reach the file.
    assert recent_addresses(["127.0.0.1", "me:hunter2@host", "a b", "", "x" * 300, 7]) == []


def test_a_refused_address_never_reaches_the_file(tmp_path: Path) -> None:
    path = tmp_path / "ui.json"
    remember_launcher("wow-wotlk", SERVER, addresses=["me:hunter2@host", "10.0.0.5"], path=path)
    text = path.read_text(encoding="utf-8")
    assert "hunter2" not in text
    assert launcher_place("wow-wotlk", SERVER, path).addresses == ["10.0.0.5"]


def test_a_hand_edited_history_is_cleaned_on_the_way_in(tmp_path: Path) -> None:
    path = tmp_path / "ui.json"
    key = ui_settings.launcher_key("wow-wotlk", SERVER)
    path.write_text(
        json.dumps({"launchers": {key: {"addresses": ["pw@host", "ok.lan", "127.0.0.1"]}}}),
        encoding="utf-8",
    )
    assert launcher_place("wow-wotlk", SERVER, path).addresses == ["ok.lan"]
