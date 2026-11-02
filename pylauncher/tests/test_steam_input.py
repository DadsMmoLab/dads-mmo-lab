"""Tests for `yulon.steam_input`: the ConsolePort controller layout (Steam Deck).

Tmp-path Steam trees throughout — no real Steam, no real profile. The layout
bytes asserted verbatim-apart-from-markers are the shipped static file, so a
dict-based rewrite that collapsed the duplicate `group`/`preset` keys fails
here rather than on a Deck.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime
from functools import partial
from pathlib import Path

import pytest

from yulon import client_packs, play_client, resources, steam, steam_input

FIXED = datetime(2026, 11, 2, 12, 0, 0)
ACCOUNT = "18347166"
GAME = "Turtle WoW"


def _steam_root(home: Path) -> Path:
    return home / ".local/share/Steam"


def _profile(home: Path) -> Path:
    config = _steam_root(home) / f"userdata/{ACCOUNT}/config"
    config.mkdir(parents=True)
    steam_config = _steam_root(home) / "config"
    steam_config.mkdir(parents=True)
    (steam_config / "config.vdf").write_text(
        '"InstallConfigStore"\n{\n\t"Software"\n\t{\n\t\t"Valve"\n\t{\n'
        '\t\t\t"Steam"\n\t\t\t{\n\t\t\t\t"AutoUpdateWindowEnabled"\t\t"0"\n'
        "\t\t\t}\n\t\t}\n\t}\n}\n",
        encoding="utf-8",
    )
    return config


def _plan(home: Path, app_name: str = GAME) -> steam_input.InputPlan:
    return steam_input.plan(
        _steam_root(home),
        ACCOUNT,
        app_name,
        config_dir=home / ".local/share/yulon",
    )


def _shipped() -> bytes:
    return (resources.installers_dir() / steam_input.LAYOUT_TEMPLATE).read_bytes()


def _shipped_text() -> str:
    return _shipped().decode("utf-8")


# -- layout_keys -----------------------------------------------------------------


def test_layout_keys_plain_name_is_one_key() -> None:
    assert steam_input.layout_keys(GAME) == ("turtle wow",)


def test_layout_keys_name_with_colon_and_dot_gives_both_spellings() -> None:
    assert steam_input.layout_keys("A: B.C") == ("a b.c", "a bc")


def test_layout_keys_name_with_colon_only_strips_it() -> None:
    assert steam_input.layout_keys("Riposte: Frontline") == ("riposte frontline",)


# -- the shipped file --------------------------------------------------------------


def test_the_shipped_layout_is_the_research_example_with_the_lead_title() -> None:
    text = _shipped_text()
    research = (
        Path("/home/perzi/dads-mmo-lab/.notes/research/T853-controller_neptune.example.vdf")
        .read_text(encoding="utf-8")
        .replace("Yu'lon ConsolePort (WoW)", "Yu'lon: ConsolePort (WoW)")
    )
    assert text == research
    assert text.count('"group"') > 1, "duplicate group keys survive: no dict round-trip"
    assert text.count('"preset"') == 1
    assert text.count("{") == text.count("}")


# -- fresh press ---------------------------------------------------------------------


def test_fresh_press_writes_entry_layout_and_state(tmp_path: Path) -> None:
    _profile(tmp_path)
    input_plan = _plan(tmp_path)
    assert [key.action for key in input_plan.keys] == ["write"]

    report = steam_input.apply(input_plan, steam_running=False)

    assert report.written == ("turtle wow",) and report.kept == ()
    key = input_plan.keys[0]
    assert key.layout_file.is_file()
    assert key.layout_file.read_bytes() == steam_input.layout_bytes(
        steam_root=_steam_root(tmp_path), account_id=ACCOUNT, key="turtle wow"
    )
    want = _shipped_text().replace("@STEAM_ROOT@", str(_steam_root(tmp_path)))
    want = want.replace("@ACCOUNT@", ACCOUNT).replace("@KEY@", "turtle wow")
    assert key.layout_file.read_text(encoding="utf-8") == want
    text = key.configset.read_text(encoding="utf-8")
    assert '"turtle wow"' in text and '"yulon"' in text and '"autosave"' in text
    recorded = json.loads(input_plan.state_file.read_text(encoding="utf-8"))
    assert recorded == {"turtle wow": hashlib.sha256(key.layout_file.read_bytes()).hexdigest()}


# -- an existing configset keeps every other byte -------------------------------------


def _realistic_configset() -> str:
    return (
        '"controller_config"\n'
        "{\n"
        '\t"xbox cloud gaming (xcloud)"\n'
        "\t{\n"
        '\t\t"autosave"\t\t"1"\n'
        "\t}\n"
        '\t"413080"\n'
        "\t{\n"
        '\t\t"template"\t\t"controller_neptune_gamepad+mouse.vdf"\n'
        "\t}\n"
        "}\n"
    )


def test_existing_configset_gains_only_our_entry(tmp_path: Path) -> None:
    _profile(tmp_path)
    input_plan = _plan(tmp_path)
    key = input_plan.keys[0]
    key.configset.parent.mkdir(parents=True, exist_ok=True)
    before = _realistic_configset()
    key.configset.write_text(before, encoding="utf-8")

    steam_input.apply(input_plan, steam_running=False)

    after = key.configset.read_text(encoding="utf-8")
    assert '"turtle wow"' in after and '"yulon"\t\t"1"' in after
    assert after.replace(_inserted_block(after, "turtle wow"), "") == before


def _inserted_block(text: str, key: str) -> str:
    """The entry block `apply()` added for `key`, for the take-back comparison."""
    found = steam_input._find_key_entry(text, key)
    assert found is not None
    return found[2]


def test_crlf_line_endings_survive_the_edit(tmp_path: Path) -> None:
    _profile(tmp_path)
    input_plan = _plan(tmp_path)
    key = input_plan.keys[0]
    key.configset.parent.mkdir(parents=True, exist_ok=True)
    before = _realistic_configset().replace("\n", "\r\n")
    with key.configset.open("w", encoding="utf-8", newline="") as fh:
        fh.write(before)

    steam_input.apply(input_plan, steam_running=False)

    with key.configset.open("r", encoding="utf-8", newline="") as fh:
        after = fh.read()
    assert "\r\n" in after and after.count("\r\n") == after.count("\n")
    assert after.replace(_inserted_block(after, "turtle wow"), "") == before


# -- re-run: idempotent; player edits and foreign entries are kept ---------------------


def test_rerun_rewrites_idempotently_with_no_duplicate_entry(tmp_path: Path) -> None:
    _profile(tmp_path)
    first = _plan(tmp_path)
    steam_input.apply(first, steam_running=False)
    before = first.keys[0].configset.read_bytes()

    second = _plan(tmp_path)
    assert [key.action for key in second.keys] == ["write"]
    steam_input.apply(second, steam_running=False)

    assert second.keys[0].configset.read_bytes() == before
    text = second.keys[0].configset.read_text(encoding="utf-8")
    assert text.count('"turtle wow"') == 1


def test_player_edited_file_is_kept(tmp_path: Path) -> None:
    _profile(tmp_path)
    steam_input.apply(_plan(tmp_path), steam_running=False)
    input_plan = _plan(tmp_path)
    key = input_plan.keys[0]
    key.layout_file.write_bytes(key.layout_file.read_bytes() + b"\n")

    rerun = _plan(tmp_path)

    assert [entry.action for entry in rerun.keys] == ["keep-player"]
    report = steam_input.apply(rerun, steam_running=False)
    assert report.written == () and report.kept == ("turtle wow",)
    assert key.layout_file.read_bytes().endswith(b"\n")


def test_foreign_entry_for_our_key_is_kept(tmp_path: Path) -> None:
    _profile(tmp_path)
    input_plan = _plan(tmp_path)
    key = input_plan.keys[0]
    key.configset.parent.mkdir(parents=True, exist_ok=True)
    key.configset.write_text(
        '"controller_config"\n{\n\t"turtle wow"\n\t{\n\t\t"template"\t\t'
        '"controller_neptune_gamepad+mouse.vdf"\n\t}\n}\n',
        encoding="utf-8",
    )

    rerun = _plan(tmp_path)

    assert [entry.action for entry in rerun.keys] == ["keep-player"]
    before = key.configset.read_bytes()
    steam_input.apply(rerun, steam_running=False)
    assert key.configset.read_bytes() == before and not key.layout_file.exists()


def test_layout_file_without_an_entry_is_kept(tmp_path: Path) -> None:
    _profile(tmp_path)
    input_plan = _plan(tmp_path)
    key = input_plan.keys[0]
    key.layout_file.parent.mkdir(parents=True, exist_ok=True)
    key.layout_file.write_bytes(b"someone saved a layout here\n")

    rerun = _plan(tmp_path)

    assert [entry.action for entry in rerun.keys] == ["keep-player"]


# -- refusals, backups, removal ----------------------------------------------------------


def test_steam_running_refuses_and_writes_nothing(tmp_path: Path) -> None:
    _profile(tmp_path)
    input_plan = _plan(tmp_path)

    with pytest.raises(steam.SteamRefusal, match="Steam is running"):
        steam_input.apply(input_plan, steam_running=True)

    key = input_plan.keys[0]
    assert not key.configset.exists() and not key.layout_file.exists()
    assert not input_plan.state_file.exists()


def test_backup_is_taken_before_the_configset_edit(tmp_path: Path) -> None:
    _profile(tmp_path)
    input_plan = _plan(tmp_path)
    key = input_plan.keys[0]
    key.configset.parent.mkdir(parents=True, exist_ok=True)
    before = _realistic_configset()
    key.configset.write_text(before, encoding="utf-8")

    report = steam_input.apply(input_plan, steam_running=False)

    assert len(report.backups) == 1
    assert report.backups[0].read_text(encoding="utf-8") == before
    assert report.backups[0].name.startswith("configset_controller_neptune.vdf.yulon-bak-")


def test_remove_takes_back_only_ours(tmp_path: Path) -> None:
    _profile(tmp_path)
    steam_input.apply(_plan(tmp_path), steam_running=False)
    input_plan = _plan(tmp_path)

    report = steam_input.remove(input_plan)

    key = input_plan.keys[0]
    assert report.entries == ("turtle wow",) and key.layout_file in report.files
    assert '"turtle wow"' not in key.configset.read_text(encoding="utf-8")
    assert not key.layout_file.exists()


def test_remove_leaves_a_foreign_entry_and_an_edited_file(tmp_path: Path) -> None:
    _profile(tmp_path)
    steam_input.apply(_plan(tmp_path), steam_running=False)
    key = _plan(tmp_path).keys[0]
    key.layout_file.write_bytes(b"player edit\n")
    key.configset.write_text(
        key.configset.read_text(encoding="utf-8").replace('"yulon"', '"tampered"'),
        encoding="utf-8",
    )
    input_plan = _plan(tmp_path)

    report = steam_input.remove(input_plan)

    assert report.entries == () and report.files == ()
    assert key.layout_file.is_file()
    assert '"turtle wow"' in key.configset.read_text(encoding="utf-8")


# -- the wire-up: SteamShortcuts.add -------------------------------------------------------


def _shortcuts(
    home: Path, *, config_dir: Path | None = None, account_id: str | None = ACCOUNT
) -> steam.SteamShortcuts:
    client = home / "clients/TurtleWoW"
    client.mkdir(parents=True, exist_ok=True)
    if not (client / "WoW.exe").exists():
        (client / "WoW.exe").write_bytes(b"MZ")
    tool = home / ".local/share/Steam/compatibilitytools.d/GE-Proton11"
    tool.mkdir(parents=True, exist_ok=True)
    (tool / "proton").write_text("#!/bin/sh\n", encoding="utf-8")
    (tool / "compatibilitytool.vdf").write_text(
        '"compatibilitytools"\n{\n  "compat_tools"\n  {\n'
        '    "GE-Proton11"\n    {\n      "install_path" "."\n'
        '      "display_name" "GE-Proton11"\n      "from_oslist"  "windows"\n'
        '      "to_oslist"    "linux"\n    }\n  }\n}\n',
        encoding="utf-8",
    )
    return steam.SteamShortcuts(
        game=GAME,
        server_dir=home / "tortoise-vm",
        client_dir=client,
        home=home,
        is_running=lambda: False,
        now=lambda: FIXED,
        platform_id=lambda: "linux",
        launcher=lambda: ("/usr/bin/yulon", ""),
        config_dir=config_dir,
        account_id=account_id,
    )


def test_gaming_add_with_layout_writes_it_and_the_report_line(tmp_path: Path) -> None:
    _profile(tmp_path)
    config_dir = tmp_path / ".local/share/yulon"

    report = _shortcuts(tmp_path, config_dir=config_dir).add(controller_layout=True)

    layout = (
        tmp_path
        / ".local/share/Steam/steamapps/common/Steam Controller Configs"
        / ACCOUNT
        / "config/turtle wow/controller_neptune.vdf"
    )
    assert layout.is_file()
    assert report.layout == (
        "Steam Input: a ConsolePort controller layout for Turtle WoW "
        "(in game, type /cp recalibrate once)."
    )
    assert "Steam Input" in steam.confirmation(report)


def test_gaming_add_without_layout_writes_nothing(tmp_path: Path) -> None:
    _profile(tmp_path)

    report = _shortcuts(tmp_path).add(controller_layout=False)

    base = tmp_path / ".local/share/Steam/steamapps/common/Steam Controller Configs"
    assert not base.exists()
    assert report.layout == ""


def test_keep_player_report_line(tmp_path: Path) -> None:
    _profile(tmp_path)
    _shortcuts(tmp_path).add(controller_layout=True)
    key = _plan(tmp_path).keys[0]
    key.layout_file.write_bytes(key.layout_file.read_bytes() + b"\n")

    report = _shortcuts(tmp_path).add(controller_layout=True)

    assert report.layout == (
        "Steam Input: Turtle WoW already has a controller layout, so it was left as it is."
    )


def test_server_and_stop_tiles_get_no_layout(tmp_path: Path) -> None:
    _profile(tmp_path)
    _shortcuts(tmp_path).add(controller_layout=True)

    base = tmp_path / ".local/share/Steam/steamapps/common/Steam Controller Configs" / ACCOUNT
    assert sorted(path.name for path in (base / "config").iterdir()) == [
        "configset_controller_neptune.vdf",
        "turtle wow",
    ]


# -- T853-C: the seam reads the ready-to-play client's ConsolePort choice ---------------


def _play_with_record(home: Path, *, consoleport: bool | None) -> Path:
    """A ready-to-play client whose record says ConsolePort on, off, or nothing.

    A real marker and a real `write_record` through its gate — not hand-written
    JSON — so the seam reads what Play writes. `None` is a client with no
    record at all (made, never chosen through).
    """
    original = home / "clients/TurtleWoW"
    original.mkdir(parents=True, exist_ok=True)
    (original / "WoW.exe").write_bytes(b"MZ")
    (original / "Data").mkdir(exist_ok=True)
    (original / "Data" / "common.MPQ").write_bytes(b"mpq" * 100)
    play = home / "WoW (Yu'lon)"
    server = home / "tortoise-vm"
    play_client.create(original, play, game="wow-turtle", server_dir=server, allow_full_copy=True)
    if consoleport is not None:
        client_packs.write_record(
            play,
            client_packs.PackRecord(
                packs={},
                exe=None,
                choices={"packs": {"consoleport": consoleport}, "exe_options": {}},
                config_seeded=False,
                launcher={},
            ),
            game="wow-turtle",
            server_dir=server,
        )
    return play


def _wow_shortcuts(home: Path, play: Path | None) -> steam.SteamShortcuts:
    """The WoW press: the seam reads the record at press time, off the GUI thread."""
    shortcuts = _shortcuts(home, config_dir=home / ".local/share/yulon")
    return replace(
        shortcuts,
        controller_layout=(
            partial(steam_input.consoleport_chosen, play) if play is not None else None
        ),
    )


def _controller_base(home: Path) -> Path:
    return home / ".local/share/Steam/steamapps/common/Steam Controller Configs"


def test_wow_add_with_consoleport_true_writes_the_layout(tmp_path: Path) -> None:
    _profile(tmp_path)
    play = _play_with_record(tmp_path, consoleport=True)

    report = _wow_shortcuts(tmp_path, play).add()

    layout = _controller_base(tmp_path) / ACCOUNT / "config/turtle wow/controller_neptune.vdf"
    assert layout.is_file()
    assert (_controller_base(tmp_path) / ACCOUNT / "config" / steam_input.CONFIGSET_FILE).is_file()
    assert report.layout == (
        "Steam Input: a ConsolePort controller layout for Turtle WoW "
        "(in game, type /cp recalibrate once)."
    )


def test_wow_add_with_consoleport_false_writes_nothing(tmp_path: Path) -> None:
    _profile(tmp_path)
    play = _play_with_record(tmp_path, consoleport=False)

    report = _wow_shortcuts(tmp_path, play).add()

    assert not _controller_base(tmp_path).exists()
    assert report.layout == ""


def test_wow_add_with_consoleport_absent_writes_nothing(tmp_path: Path) -> None:
    _profile(tmp_path)
    play = _play_with_record(tmp_path, consoleport=None)
    record = client_packs.read_record(play)
    assert record.choices["packs"] == {}

    report = _wow_shortcuts(tmp_path, play).add()

    assert not _controller_base(tmp_path).exists()
    assert report.layout == ""


def test_wow_add_without_a_record_writes_nothing(tmp_path: Path) -> None:
    _profile(tmp_path)

    report = _wow_shortcuts(tmp_path, None).add()

    assert not _controller_base(tmp_path).exists()
    assert report.layout == ""


def test_wow_add_refuses_while_steam_runs_before_any_write(tmp_path: Path) -> None:
    _profile(tmp_path)
    play = _play_with_record(tmp_path, consoleport=True)
    shortcuts = _wow_shortcuts(tmp_path, play)
    running = replace(shortcuts, is_running=lambda: True)

    with pytest.raises(steam.SteamRefusal, match="Steam is running"):
        running.add()

    assert not (
        tmp_path / ".local/share/Steam/userdata" / ACCOUNT / "config/shortcuts.vdf"
    ).exists()
    assert not _controller_base(tmp_path).exists()
