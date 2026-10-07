"""T551: the tray's left-click panel is design C, the compact status list ("Replace B with C").

"Servers" and "N online" at the top; one 44 px row per server (a status dot, the
name, a second line, and a small square icon button: Play online, Start stopped,
Open when it needs attention, none while starting; Tortoise has a second icon for
its bot dashboard); the tray's two switches, live; Open Yu'lon | Quit tray….
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QApplication, QToolButton

from tests.test_tray import FakeTrayIcon, FakeView, FakeWindow, _add
from tests.test_tray_dashboard import TortoiseView
from yulon import autostart, dashboard
from yulon.ui import tray_flyout
from yulon.ui.tray import YulonTray


@pytest.fixture
def window(qapp: Any) -> Iterator[FakeWindow]:
    win = FakeWindow()
    win.show()
    yield win
    win.hide()
    win.deleteLater()


@pytest.fixture
def tray(window: FakeWindow, monkeypatch: pytest.MonkeyPatch) -> Iterator[YulonTray]:
    monkeypatch.setattr(autostart, "why_not", lambda *a, **k: None)
    monkeypatch.setattr(autostart, "is_enabled", lambda *a, **k: False)
    made = YulonTray(window, icon_factory=FakeTrayIcon, available=lambda: True)
    made.install()
    made.note_seen = True
    yield made
    if made.flyout is not None:
        made.flyout.hide()
    made.uninstall()


def _open(tray: YulonTray) -> tray_flyout.TrayFlyout:
    tray.toggle_flyout()
    assert tray.flyout is not None and tray.flyout.isVisible()
    QApplication.processEvents()
    return tray.flyout


def _row(panel: tray_flyout.TrayFlyout, title: str) -> Any:
    for row in panel.cards():
        if row.title.text() == title:
            return row
    raise AssertionError(f"no row {title!r}")


def _action(row: Any) -> str:
    """The row's icon button's verb, from its accessible name ("Play WotLK"), or "" when none."""
    return "" if row.action.isHidden() else row.action.accessibleName().split(" ")[0]


def test_the_header_says_servers_and_how_many_are_online(
    tray: YulonTray, window: FakeWindow
) -> None:
    _add(window, FakeView("WotLK", "/srv/a", "running"))
    _add(window, FakeView("TBC", "/srv/b", "stopped"))
    panel = _open(tray)
    assert panel.header.text() == "Servers"
    assert panel.count.text() == "1 online"


def test_each_server_is_one_44_px_row_with_a_dot_name_and_second_line(
    tray: YulonTray, window: FakeWindow
) -> None:
    up = _add(window, FakeView("WotLK", "/srv/a", "running"))
    up.last_verdict = dashboard.Verdict("up", players=2, bots=10, uptime=timedelta(minutes=7))
    _add(window, FakeView("TBC", "/srv/b", "starting"))
    _add(window, FakeView("Vanilla", "/srv/c", "stopped"))
    _add(window, FakeView("Cata", "/srv/d", "loop"))
    panel = _open(tray)
    for row in panel.cards():
        assert row.height() == tray_flyout.ROW_HEIGHT == 44
    wotlk, tbc, vanilla, cata = (_row(panel, n) for n in ("WotLK", "TBC", "Vanilla", "Cata"))
    assert (wotlk.dot.tone, tbc.dot.tone, vanilla.dot.tone, cata.dot.tone) == (
        "up",
        "between",
        "down",
        "attention",
    )
    assert wotlk.detail.text() == "2 players · 10 bots"
    assert tbc.detail.text() == "Starting"
    assert vanilla.detail.text() == "Stopped"
    assert cata.detail.text() == "Crash loop"


def test_the_icon_button_is_play_start_open_or_none(tray: YulonTray, window: FakeWindow) -> None:
    _add(window, FakeView("WotLK", "/srv/a", "running"))
    _add(window, FakeView("TBC", "/srv/b", "starting"))
    _add(window, FakeView("Vanilla", "/srv/c", "stopped"))
    _add(window, FakeView("Cata", "/srv/d", "partial"))
    panel = _open(tray)
    assert [_action(_row(panel, n)) for n in ("WotLK", "TBC", "Vanilla", "Cata")] == [
        "Play",
        "",
        "Start",
        "Open",
    ]
    assert _row(panel, "TBC").action.isHidden(), "a server starting has no icon button"
    for row in panel.cards():
        assert isinstance(row.action, QToolButton)
        assert row.action.width() == row.action.height(), "not a small square icon button"


def test_the_buttons_do_what_they_say(tray: YulonTray, window: FakeWindow) -> None:
    _add(window, FakeView("WotLK", "/srv/a", "running"))
    stopped = _add(window, FakeView("Vanilla", "/srv/c", "stopped"))
    _add(window, FakeView("Cata", "/srv/d", "loop"))
    panel = _open(tray)
    _row(panel, "WotLK").action.click()
    assert window.opened == [("game-wotlk", Path("/srv/a"))]
    panel = _open(tray)
    _row(panel, "Vanilla").action.click()
    assert stopped.starts == 1
    assert _row(panel, "Vanilla").detail.text() == "Starting", "a Start must show at once"
    assert _action(_row(panel, "Vanilla")) == ""
    _row(panel, "Cata").action.click()
    assert window.shown_tabs == [("game-cata", Path("/srv/d"))]


def test_start_is_greyed_with_the_tabs_own_reason(tray: YulonTray, window: FakeWindow) -> None:
    view = _add(window, FakeView("Vanilla", "/srv/c", "stopped"))
    view.start_button.setEnabled(False)
    view.start_button.setToolTip("Waiting for the module install to finish")
    row = _row(_open(tray), "Vanilla")
    assert not row.action.isEnabled()
    assert row.action.toolTip() == "Waiting for the module install to finish"


@pytest.mark.parametrize(
    ("status", "switch_on", "name", "enabled"),
    [
        ("stopped", True, "Bot dashboard", False),
        ("running", False, "Turn on the bot dashboard…", True),
        ("running", True, "Bot dashboard", True),
    ],
)
def test_tortoise_keeps_its_bot_dashboard_in_three_states(
    tray: YulonTray, window: FakeWindow, status: str, switch_on: bool, name: str, enabled: bool
) -> None:
    view = _add(window, TortoiseView(status, switch_on=switch_on))
    row = _row(_open(tray), "Tortoise")
    assert not row.dashboard.isHidden()
    assert row.dashboard.accessibleName() == f"{name} ({row.title.text()})"
    assert row.dashboard.isEnabled() is enabled
    if not enabled:
        assert row.dashboard.toolTip() == "Start the server first to open its bot dashboard."
    row.dashboard.click()
    if switch_on and enabled:
        assert view.opened_dashboard == 1
    elif enabled:
        assert view.shown_switch == 1


def test_a_server_without_a_dashboard_shows_no_second_icon(
    tray: YulonTray, window: FakeWindow
) -> None:
    _add(window, FakeView("WotLK", "/srv/a", "running"))
    assert _row(_open(tray), "WotLK").dashboard.isHidden()


# ------------------------------------------------------------------ switches


def test_the_trays_two_switches_sit_at_the_foot_and_act_at_once(
    tray: YulonTray, window: FakeWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    switched: list[bool] = []
    monkeypatch.setattr(autostart, "set_enabled", lambda on, *a, **k: switched.append(on))
    panel = _open(tray)
    assert panel.switches.keep.text() == "Keep running when Yu'lon is closed"
    assert panel.switches.sign_in.text() == "Start the tray when I sign in"
    assert panel.switches.keep.isChecked()
    panel.switches.sign_in.click()
    assert switched == [True]


def test_turning_keep_off_from_the_panel_brings_the_window_back(
    tray: YulonTray, window: FakeWindow
) -> None:
    """With the window hidden, the tray icon going would leave Yu'lon running unseen."""
    window.hide()
    panel = _open(tray)
    panel.switches.keep.click()
    assert tray.keep_in_tray is False
    assert window.isVisible(), "Yu'lon was left running with no window and no icon"


def test_a_switch_that_cannot_be_used_here_is_greyed_with_the_reason(
    tray: YulonTray, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(autostart, "why_not", lambda *a, **k: autostart.NOT_INSTALLED)
    panel = _open(tray)
    assert not panel.switches.sign_in.isEnabled()
    assert panel.switches.note.text() == autostart.NOT_INSTALLED
    assert not panel.switches.note.isHidden()


def test_the_footer_is_open_yulon_and_quit_tray(tray: YulonTray, window: FakeWindow) -> None:
    window.hide()
    panel = _open(tray)
    assert panel.open_button.text() == "Open Yu'lon"
    assert panel.quit_button.text() == "Quit tray…"
    assert panel.open_button.y() == panel.quit_button.y(), "the footer is one row split in two"
    panel.open_button.click()
    assert window.isVisible()


# ---------------------------------------------------------------- pad and size


def test_the_pad_starts_on_the_first_rows_button_and_moves_down_the_list(
    tray: YulonTray, window: FakeWindow
) -> None:
    from yulon.ui.gamepad import Action, Direction, Navigator

    _add(window, FakeView("WotLK", "/srv/a", "running"))
    _add(window, FakeView("Vanilla", "/srv/c", "stopped"))
    navigator = Navigator()
    panel = _open(tray)
    assert QApplication.focusWidget() is _row(panel, "WotLK").action
    assert navigator.navigate(Direction.DOWN)
    assert QApplication.focusWidget() is _row(panel, "Vanilla").action
    assert navigator.perform(Action.BACK)
    assert not panel.isVisible()
    navigator.deleteLater()


def test_a_long_list_fits_a_small_screen(tray: YulonTray, window: FakeWindow) -> None:
    """A Steam Deck is 1280x800; the panel never grows past the screen it is on."""
    for i in range(20):
        _add(window, FakeView(f"Server{i}", f"/srv/{i}", "stopped"))
    panel = _open(tray)
    assert panel.height() <= tray_flyout.FLYOUT_MAX_HEIGHT
    assert panel.height() < 800


# ------------------------------------------------- Codex adversarial, T551


def test_keep_off_stays_on_when_the_sign_in_entry_cannot_be_removed(
    tray: YulonTray, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[high]: keep-in-tray went off while the sign-in entry that needs it stayed on disk."""
    monkeypatch.setattr(autostart, "is_enabled", lambda *a, **k: True)

    def refuse(on: bool, *a: Any, **k: Any) -> None:
        raise OSError("access denied")

    monkeypatch.setattr(autostart, "set_enabled", refuse)
    panel = _open(tray)
    panel.switches.keep.click()
    assert tray.keep_in_tray is True
    assert panel.switches.keep.isChecked()
    assert panel.switches.sign_in.isChecked()
    assert "access denied" in panel.switches.note.text()


def test_a_setting_that_could_not_be_saved_says_so(
    tray: YulonTray, window: FakeWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[medium]: ui.json not written, and the switch looked saved."""
    from yulon import ui_settings

    monkeypatch.setattr(ui_settings, "remember_tray", lambda **k: False)
    panel = _open(tray)
    panel.switches.keep.click()
    assert "saved" in panel.switches.note.text()
    assert not panel.switches.note.isHidden()


def test_a_click_begun_on_one_server_never_lands_on_another(
    tray: YulonTray, window: FakeWindow
) -> None:
    """[medium]: rows are reused by position; a refresh between press and release turned
    the pressed row into another server."""
    first = _add(window, FakeView("Vanilla", "/srv/c", "stopped"))
    second = _add(window, FakeView("WotLK", "/srv/a", "stopped"))
    panel = _open(tray)
    row = _row(panel, "Vanilla")
    row.action.pressed.emit()
    window.yulon_controllers.remove(first)
    window.servers_changed.emit()  # the row is now WotLK's
    row.action.clicked.emit()
    assert (first.starts, second.starts) == (0, 0), "the click landed on another server"


def test_a_dashboard_click_begun_on_one_server_never_lands_on_another(
    tray: YulonTray, window: FakeWindow
) -> None:
    """Codex on 4e3549e9: the dashboard icon lacked the press-time check Play/Start had."""
    first = _add(window, TortoiseView("running", switch_on=True))
    second = TortoiseView("running", switch_on=True)
    second.entry.id = "game-tortoise-2"
    second.services.controller.server_dir = Path("/srv/t2")
    _add(window, second)
    panel = _open(tray)
    row = panel.cards()[0]
    assert row.view is first
    row.dashboard.pressed.emit()
    window.yulon_controllers.remove(first)
    window.servers_changed.emit()  # the row is now the second Tortoise's
    row.dashboard.clicked.emit()
    assert (first.opened_dashboard, second.opened_dashboard) == (0, 0)


def test_the_panel_and_an_open_settings_dialog_never_disagree(
    tray: YulonTray, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex on 4e3549e9: two TraySwitches, each reading the state only when built."""
    from yulon.ui.tray_settings import TraySettingsDialog

    on = [False]
    monkeypatch.setattr(autostart, "is_enabled", lambda *a, **k: on[0])
    monkeypatch.setattr(autostart, "set_enabled", lambda v, *a, **k: on.__setitem__(0, v))
    dialog = TraySettingsDialog(tray)
    try:
        panel = _open(tray)
        panel.switches.sign_in.click()
        assert dialog.sign_in.isChecked(), "the dialog kept the old sign-in state"
        dialog.keep.click()  # off: the sign-in entry goes with it
        assert not panel.switches.keep.isChecked()
        assert not panel.switches.sign_in.isChecked()
    finally:
        dialog.deleteLater()


def test_start_is_a_power_symbol_not_the_server_glyph(tray: YulonTray, window: FakeWindow) -> None:
    """Lead, 2026-10-07: the server glyph's two bars did not read as Start; Play has ▶."""
    _add(window, FakeView("Vanilla", "/srv/c", "stopped"))
    row = _row(_open(tray), "Vanilla")
    assert row.action.accessibleName() == "Start Vanilla"
    assert row.action.toolTip() == "Start Vanilla"
    assert row.action.icon().cacheKey() == tray_flyout.power_icon().cacheKey()
    image = tray_flyout.power_icon().pixmap(32, 32).toImage()
    amber = [
        image.pixelColor(x, y).alpha() > 0
        for x, y in ((16, 4), (4, 18), (28, 18), (16, 29))  # the line's top, the ring's sides
    ]
    assert all(amber), amber
    assert image.pixelColor(16, 22).alpha() == 0, "the ring is open inside"


# ------------------------------------------------- cold review, T551


def test_the_row_buttons_show_where_the_focus_is(tray: YulonTray, window: FakeWindow) -> None:
    """MUST: the pad's focus starts on a row's icon button, and nothing showed it."""
    from PySide6.QtGui import QColor, QPalette

    from yulon.ui.theme import COLOR_GOLD_BRIGHT, apply_dadcraft_theme

    app = QApplication.instance()
    assert isinstance(app, QApplication)
    palette = QPalette(app.palette())
    apply_dadcraft_theme(app)
    try:
        _add(window, FakeView("WotLK", "/srv/a", "running"))
        _add(window, FakeView("Vanilla", "/srv/c", "stopped"))
        panel = _open(tray)
        focused, other = _row(panel, "WotLK").action, _row(panel, "Vanilla").action
        assert focused.hasFocus()
        QApplication.processEvents()

        def edge(button: QToolButton) -> QColor:
            image = button.grab().toImage()
            return image.pixelColor(1, image.height() // 2)

        gold = QColor(COLOR_GOLD_BRIGHT)
        ring = edge(focused)
        assert (
            abs(ring.red() - gold.red()) < 30 and abs(ring.green() - gold.green()) < 30
        ), ring.name()
        assert edge(other).name() != ring.name(), "every button looks focused"
    finally:
        app.setStyleSheet("")
        app.setPalette(palette)


def test_each_icon_button_says_which_server_it_is_for(tray: YulonTray, window: FakeWindow) -> None:
    """SHOULD: "Play" alone told a screen reader nothing about which server."""
    _add(window, FakeView("WotLK", "/srv/a", "running"))
    _add(window, FakeView("Cata", "/srv/d", "loop"))
    panel = _open(tray)
    for title, verb in (("WotLK", "Play"), ("Cata", "Open")):
        row = _row(panel, title)
        assert row.action.accessibleName() == f"{verb} {title}"
        assert row.action.toolTip() == f"{verb} {title}"


def test_the_status_dot_says_the_status(tray: YulonTray, window: FakeWindow) -> None:
    """SHOULD: a dot alone is colour, which a screen reader cannot see."""
    _add(window, FakeView("WotLK", "/srv/a", "running"))
    _add(window, FakeView("TBC", "/srv/b", "starting"))
    _add(window, FakeView("Vanilla", "/srv/c", "stopped"))
    _add(window, FakeView("Cata", "/srv/d", "partial"))
    _add(window, FakeView("Mop", "/srv/e", "unknown"))  # Docker did not answer (Codex)
    panel = _open(tray)
    names = ("WotLK", "TBC", "Vanilla", "Cata", "Mop")
    assert [_row(panel, n).dot.accessibleName() for n in names] == [
        "Online",
        "Starting",
        "Stopped",
        "Needs attention",
        "Status unknown",
    ]


def test_the_switches_are_read_again_each_time_the_panel_opens(
    tray: YulonTray, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sign-in entry can change outside Yu'lon (removed by hand, another copy)."""
    on = [False]
    monkeypatch.setattr(autostart, "is_enabled", lambda *a, **k: on[0])
    panel = _open(tray)
    assert not panel.switches.sign_in.isChecked()
    tray.hide_flyout()
    on[0] = True
    panel = _open(tray)
    assert panel.switches.sign_in.isChecked()
