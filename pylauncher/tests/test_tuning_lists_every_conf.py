"""The Tuning tab's file buttons offer every conf in the server's modules folder (T569).

A Steam Deck player's `env/dist/etc/modules/` held about fifty `.conf` files and the
tab offered sixteen of them: the file list was built from the settings the catalog
declares for INSTALLED modules, so a module the catalog does not know, or one whose
only declared key is a wildcard (`AutoBalance.Enable.*`), had no button. The folder
below is that player's, name for name.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt

from tests.conftest import process_events
from tests.test_controller_view import _deploy, _Ps, _services, _with_the_core_confs
from yulon import runner, tuning
from yulon.catalog.catalog import load_catalog
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import ControllerView
from yulon.ui.widgets.job import run_inline

WOTLK = load_catalog().get("wow-wotlk")
MODULES = "env/dist/etc/modules"

PLAYER_CONFS = (
    "1v1arena AutoBalance AutoRevive bookofknowledge challenge_modes character_services "
    "GainHonorGuard individualProgression mod_ahbot mod_ale mod_aoe_loot mod_appreciation "
    "mod_assistant mod_autofish mod_auto_gather mod_city_bots mod_city_siege mod_customserver "
    "mod_dead_means_dead mod_dungeon_clear mod_dungeon_master mod_easy_respawn "
    "mod_flightmaster_whistle mod_fortis_autobalance mod_gobject_editor mod_guildhouse "
    "mod_improved_bank mod_interact_key mod_learnspells mod_levelsync mod_mystic_merchant "
    "mod_nemesis_system mod_npc_beastmaster mod_optimal_bot_raid mod_player_bot_guildhouse "
    "mod_player_bot_level_brackets mod-quest-loot-party mod_shared_professions mod-stat-booster "
    "mod_talentbutton mod-time_is_time MultiBotBridge npc_buffer npc_enchanter playerbots "
    "pocketportal random_enchants reset_raid_cooldowns Solocraft SoloLfg transmog"
).split()
"""The `.conf` names in the player's `ls` (shot2 of the report), each beside a `.conf.dist`."""


@pytest.fixture(autouse=True)
def _inline_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


def _players_folder(server: Path) -> None:
    _with_the_core_confs(server)
    for name in PLAYER_CONFS:
        _deploy(server, f"{MODULES}/{name}.conf", f"# {name}\nKey = 1\n")
        _deploy(server, f"{MODULES}/{name}.conf.dist", f"# {name} default\nKey = 1\n")
    (server / MODULES / "lua_scripts").mkdir()


def _players_view(ps: _Ps, server: Path, installed: frozenset[str] = frozenset()) -> ControllerView:
    _players_folder(server)
    services: Any = _services(ps, server, [])
    object.__setattr__(services, "installed_modules", lambda: {"module": installed})
    return ControllerView(WOTLK, services, status_poll_ms=0)


def test_every_conf_in_the_modules_folder_has_a_button(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The report: a conf of a module the catalog does not describe had no button.

    Mutation: build the list from the settings rows alone, as it was, and every
    name the catalog does not declare keys for goes missing.
    """
    view = _players_view(ps, tmp_path, frozenset({"mod-npc-beastmaster", "mod-transmog"}))
    offered = {f for f in view._tuning_files()}
    expected = {f"{MODULES}/{name}.conf" for name in PLAYER_CONFS} - {f"{MODULES}/playerbots.conf"}
    missing = sorted(f.rsplit("/", 1)[-1] for f in expected - offered)
    assert missing == [], f"{len(missing)} of the folder's confs have no button: {missing}"
    assert [b.toolTip() for b in view.tuning_panel.file_buttons()] == list(view._tuning_files())


def test_the_lists_order_is_known_modules_then_the_rest_by_name_then_the_servers_own(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Yu'lon's own modules first (the cards' order), the others A-Z regardless of case.

    Beastmaster keeps one key in the server's own `worldserver.conf`, so that
    file is among the cards' files and stays where its card puts it, as before.
    """
    view = _players_view(ps, tmp_path, frozenset({"mod-npc-beastmaster", "mod-transmog"}))
    names = [f.rsplit("/", 1)[-1] for f in view._tuning_files()]
    cards = ["mod_npc_beastmaster.conf", "worldserver.conf", "transmog.conf"]
    assert names[:3] == cards
    rest = names[3:-2]
    assert names[-2:] == ["authserver.conf", "playerbots.conf"], "the server's own come last"
    assert len(rest) == len(PLAYER_CONFS) - 3, rest
    assert rest == sorted(rest, key=lambda n: (n.lower(), n)), rest


def test_a_dist_file_a_folder_and_a_vanished_conf_are_not_offered(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    view = _players_view(ps, tmp_path)
    (tmp_path / MODULES / "notes.txt").write_text("hi", encoding="utf-8")
    (tmp_path / MODULES / "a_folder.conf").mkdir()
    files = view._tuning_files()
    assert not any(f.endswith((".dist", ".txt", "lua_scripts", "a_folder.conf")) for f in files)
    assert files.count(f"{MODULES}/playerbots.conf") == 1, "its core button is not doubled"


def test_a_conf_the_catalog_does_not_know_opens_in_the_editor_and_saves(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opening and saving are the same path as any listed conf: backup, then the file."""
    from yulon import tuning

    view = _players_view(ps, tmp_path)
    target = f"{MODULES}/MultiBotBridge.conf"
    path = tmp_path / target
    original = path.read_bytes()
    assert target in view._tuning_files()
    next(b for b in view.tuning_panel.file_buttons() if b.toolTip() == target).click()
    assert view.tuning_panel.current_file() == target
    assert view.tuning_panel.editor.toPlainText().startswith("# MultiBotBridge")
    assert not view.tuning_panel.editor.isReadOnly()

    view.save_tuning_file("# MultiBotBridge\nKey = 2\n")
    assert path.read_bytes() == b"# MultiBotBridge\nKey = 2\n"
    backups = tuning.backups_of(path)
    assert len(backups) == 1 and backups[0].read_bytes() == original


# -- fifty buttons must not push the editor off the screen ------------------------


def _fifty_conf_window(
    ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ControllerView, Any]:
    """The player's folder in the real window, as `test_tuning_layout` builds it."""
    from tests.test_controller_view import _controller_in_the_real_window, _wotlk_override
    from yulon import server_time_zone
    from yulon.catalog import composegen, time_zone

    _players_folder(tmp_path)
    monkeypatch.setattr(time_zone, "host_zone", lambda: "Europe/Oslo")
    installed = _wotlk_override(tmp_path)
    (tmp_path / composegen.OVERRIDE_FILE).write_text(
        installed
        + '      TZ: "Europe/Oslo"\n  ac-authserver:\n    environment:\n      TZ: "Europe/Oslo"\n',
        encoding="utf-8",
    )
    services: Any = _services(ps, tmp_path, [])
    object.__setattr__(
        services, "installed_modules", lambda: {"module": frozenset({"mod-npc-beastmaster"})}
    )
    object.__setattr__(services, "time_zone", server_time_zone.time_zone_route(WOTLK, tmp_path))
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    window, _tab = _controller_in_the_real_window(view, "Tuning")
    return view, window


@pytest.mark.parametrize("size", [(1280, 800), (960, 640), (1340, 740)], ids=str)
def test_fifty_file_buttons_scroll_in_their_own_box_and_leave_the_editor_its_room(
    qapp: object,
    ps: _Ps,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    size: tuple[int, int],
) -> None:
    """The report: "it only shows what it can fit, and there isn't a scrollbar".

    Every button can be reached, the area that holds them keeps to a few lines of
    height, and the editor under them is not pushed off the screen.

    Mutation: let the area take every line (`FILE_BUTTON_LINES = 99`) and the
    buttons push the editor down; drop the scroll bar and the last buttons
    cannot be reached.
    """
    from PySide6.QtTest import QTest

    from tests.test_controller_view import _at
    from yulon.ui.widgets import tuning_panel as tp

    view, window = _fifty_conf_window(ps, tmp_path, monkeypatch)
    panel = view.tuning_panel
    _at(window, size)
    if size == (960, 640):
        panel.edit_file_button.click()
        _at(window, size)
    buttons = panel.file_buttons()
    assert len(buttons) > 50, "control: the whole folder is offered"
    area = panel.file_buttons_area
    line = buttons[0].sizeHint().height()
    assert area.height() <= tp.FILE_BUTTON_LINES * (line + panel.files.flow().spacing())
    assert area.verticalScrollBar().isVisible(), "no scroll bar over fifty buttons"

    side = panel.split.widget(1)
    lines = panel.editor.viewport().height() // panel.editor.fontMetrics().lineSpacing()
    assert lines >= 4, f"the editor shows {lines} lines"
    if size != (960, 640):
        # At 960x640 the file side is 324px high with the banner up and scrolls by design
        # (T190); from 1280x800 up the editor is on screen without scrolling it.
        editor_bottom = panel.editor.mapTo(side, panel.editor.rect().bottomLeft()).y()
        assert editor_bottom <= side.height(), "the editor's foot is below the file side"

    # Every button can be brought into view by the scroll bar, and a press opens its file.
    bar = area.verticalScrollBar()
    for button in (buttons[0], buttons[len(buttons) // 2], buttons[-1]):
        area.ensureWidgetVisible(button, 0, 0)
        process_events()
        top = button.mapTo(area.viewport(), button.rect().topLeft()).y()
        assert 0 <= top and top + button.height() <= area.viewport().height() + 1, button.text()
    assert bar.value() == bar.maximum() or buttons[-1].isVisible()
    QTest.mouseClick(buttons[-1], Qt.MouseButton.LeftButton)
    process_events()
    assert panel.current_file() == buttons[-1].toolTip()


def test_the_open_files_button_is_scrolled_into_view(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opening a file far down the list (a card's "open file" or a reload) shows its button."""
    from tests.test_controller_view import _at

    view, window = _fifty_conf_window(ps, tmp_path, monkeypatch)
    panel = view.tuning_panel
    _at(window, (1280, 800))
    last = panel.file_buttons()[-1]
    view.open_tuning_file(last.toolTip())
    panel._mark_current(last.toolTip())
    process_events()
    area = panel.file_buttons_area
    top = last.mapTo(area.viewport(), last.rect().topLeft()).y()
    assert 0 <= top and top + last.height() <= area.viewport().height() + 1


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_a_conf_the_file_system_calls_the_same_file_gets_one_button(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    """Codex review: on Windows `PlayerBots.conf` IS `playerbots.conf`, and must stay read-only.

    The same holds on a Mac's case-blind volume (T573 item 3; `os.path.normcase` is the
    identity there). This test disk tells names apart, so a second spelling is made a hard
    link of the first: the file system answering "same file", as the real disk does.
    A conf the cards name in another case than the folder does is one button too.

    Mutation: compare the raw names and both spellings get a button.
    """
    monkeypatch.setattr(tuning, "_disk_ignores_case", lambda: platform in ("win32", "darwin"))
    view = _players_view(ps, tmp_path, frozenset({"mod-npc-beastmaster", "mod-transmog"}))
    for real, other in (
        ("playerbots.conf", "PlayerBots.conf"),
        ("mod_npc_beastmaster.conf", "Mod_NPC_BeastMaster.conf"),
    ):
        os.link(tmp_path / MODULES / real, tmp_path / MODULES / other)
    files = view._tuning_files()
    assert [f for f in files if f.lower().endswith("/playerbots.conf")] == [
        f"{MODULES}/playerbots.conf"
    ]
    assert [f for f in files if f.lower().endswith("/mod_npc_beastmaster.conf")] == [
        f"{MODULES}/mod_npc_beastmaster.conf"
    ]


def test_two_spellings_that_are_two_files_each_get_a_button_on_a_case_sensitive_mac_volume(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex review: a case-sensitive APFS volume holds `Foo.conf` and `foo.conf` as two files.

    Mutation: dedupe on the platform's key alone and the second spelling vanishes from the list.
    """
    monkeypatch.setattr(tuning, "_disk_ignores_case", lambda: True)
    view = _players_view(ps, tmp_path, frozenset({"mod-npc-beastmaster", "mod-transmog"}))
    _deploy(tmp_path, f"{MODULES}/Extra.conf", "# a\n")
    _deploy(tmp_path, f"{MODULES}/extra.conf", "# b\n")
    files = view._tuning_files()
    assert {f"{MODULES}/Extra.conf", f"{MODULES}/extra.conf"} <= set(files)


def test_the_scroll_box_restates_its_height_when_the_bar_wraps_without_the_box_resizing(
    qapp: object,
) -> None:
    """`FlowBar.height_needed_changed`: a wrap that moves while the box keeps its size.

    Wider buttons at the same width make a third line. The box is not resized,
    so only the bar's own signal can tell it.

    Mutation: drop the `emit()` in `FlowBar.resizeEvent` and the height stays two lines.
    """
    from PySide6.QtWidgets import QPushButton

    from yulon.ui.widgets.flow_layout import FlowScroll, flow_bar

    bar = flow_bar()
    area = FlowScroll(bar, 4)
    buttons = [QPushButton(f"b{i}", bar) for i in range(6)]
    for button in buttons:
        button.setFixedSize(100, 30)
        bar.flow().addWidget(button)
    area.resize(320, 50)
    area.show()
    process_events()
    two_lines = area.height()
    assert two_lines == 2 * 30 + bar.flow().spacing(), f"control: two lines, {two_lines}px"
    for button in buttons:
        button.setFixedSize(150, 30)
    process_events()
    assert area.width() == 320
    assert area.height() == 3 * 30 + 2 * bar.flow().spacing(), f"{area.height()}px"
    area.close()
