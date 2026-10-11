"""T697: the tray's menu and flyout hold the same per-server actions; one "Quit Yu'lon…".

The owner's UI decision of 2026-10-11 (R7): the right-click menu gets Start and
Stop, the flyout gets Stop and Restart, and both show the same actions for a
server; the flyout's row for a stopped server shows Play (T694: the tab's PLAY
starts it, waits for the realm and plays), not only Start. "Quit tray…" and the
first-close note's "Quit Yu'lon instead" were two names for one thing: both are
"Quit Yu'lon…" now, with T690's refusals behind it unchanged.

Every press reaches the tab through `YulonTray` with T690's busy guard: a row
built before a job began must not start or stop a server under that job.

Uses `test_tray.py`'s stand-ins (a window with what `main.build_window()` puts on
the real one, tabs that are a badge and three buttons), and the real
`ControllerView` for the busy guard.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QApplication, QMenu

from tests.test_tray import FakeTrayIcon, FakeView, FakeWindow, _add
from yulon import autostart
from yulon.ui import tray as tray_module
from yulon.ui import tray_flyout
from yulon.ui.tray import YulonTray
from yulon.ui.widgets.reasons import set_enabled_why

QUIT = "Quit Yu'lon…"

STATUSES = (
    "running",
    "online",
    "starting",
    "stopping",
    "restarting",
    "partial",
    "loop",
    "failed",
    "unknown",
    "stopped",
    "offline",
)
"""Every word a Server tab's realm badge says (`realm_tone`, `dot_tone`, `status_words`)."""


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


@pytest.fixture
def told(tray: YulonTray) -> list[tuple[str, str]]:
    said: list[tuple[str, str]] = []
    tray.tell = lambda title, text: said.append((title, text))  # type: ignore[method-assign]
    return said


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


def _texts(menu: QMenu) -> list[str]:
    return [action.text() for action in menu.actions() if not action.isSeparator()]


def _menu_action(menu: QMenu, text: str) -> Any:
    found = [a for a in menu.actions() if a.text() == text]
    assert len(found) == 1, (text, _texts(menu))
    return found[0]


def _menu_server_actions(menu: QMenu, title: str) -> dict[str, tuple[bool, str]]:
    """{action: (enabled, reason when greyed)} the menu offers for one server."""
    held: dict[str, tuple[bool, str]] = {}
    for action in menu.actions():
        for name in ("Start", "Stop", "Restart"):
            if action.text() == f"{name} {title}":
                held[name] = (action.isEnabled(), "" if action.isEnabled() else action.toolTip())
    play = next(a for a in menu.actions() if a.text() == "Play").menu()
    assert isinstance(play, QMenu)
    row = next(a for a in play.actions() if a.text().split(" (")[0] == title)
    # A Play row is in the submenu for every server; it is an action only where it is
    # live, or greyed for a reason (a stopped server whose Start the tab greys). A
    # QAction with no tooltip of its own answers its text.
    reason = row.toolTip() if row.toolTip() != row.text() else ""
    if row.isEnabled() or reason:
        held["Play"] = (row.isEnabled(), "" if row.isEnabled() else reason)
    return held


def _flyout_server_actions(row: Any) -> dict[str, tuple[bool, str]]:
    """{action: (enabled, reason when greyed)} the flyout's row offers (Open is a way in)."""
    held: dict[str, tuple[bool, str]] = {}
    for button in row.action_buttons():
        if button.isHidden():
            continue
        name = button.accessibleName().split(" ")[0]
        if name == "Open":
            continue
        held[name] = (button.isEnabled(), "" if button.isEnabled() else button.toolTip())
    return held


# ------------------------------------------------------------- the menu


def test_the_menu_offers_start_for_a_stopped_server_and_stop_for_a_running_one(
    tray: YulonTray, window: FakeWindow
) -> None:
    wotlk = _add(window, FakeView("WotLK", "/srv/a", "running"))
    vanilla = _add(window, FakeView("Vanilla", "/srv/c", "stopped"))
    _add(window, FakeView("TBC", "/srv/b", "starting"))
    menu = tray.build_menu()
    texts = _texts(menu)
    assert "Start Vanilla" in texts and "Stop Vanilla" not in texts
    assert "Stop WotLK" in texts and "Start WotLK" not in texts
    assert not [t for t in texts if t.endswith(" TBC")], "a server starting has no action"
    # Under its own row, after the T559 Restart.
    assert texts.index("Start Vanilla") == texts.index("Vanilla — Stopped") + 1
    assert texts.index("Stop WotLK") == texts.index("Restart WotLK") + 1
    _menu_action(menu, "Start Vanilla").trigger()
    _menu_action(menu, "Stop WotLK").trigger()
    assert (vanilla.starts, wotlk.stops) == (1, 1)


def test_a_stop_the_tab_cannot_do_now_is_greyed_with_its_reason(
    tray: YulonTray, window: FakeWindow
) -> None:
    wotlk = _add(window, FakeView("WotLK", "/srv/a", "running"))
    set_enabled_why(wotlk.stop_button, "Wait: Rebuild is running.")
    menu = tray.build_menu()
    action = _menu_action(menu, "Stop WotLK")
    assert not action.isEnabled()
    assert action.toolTip() == "Wait: Rebuild is running."
    row = _row(_open(tray), "WotLK")
    assert not row.stop.isEnabled()
    assert row.stop.toolTip() == "Wait: Rebuild is running."


def test_the_menu_has_one_quit_and_it_is_quit_yulon(tray: YulonTray, window: FakeWindow) -> None:
    _add(window, FakeView("WotLK", "/srv/a", "running"))
    menu = tray.build_menu()
    quits = [t for t in _texts(menu) if t.startswith("Quit")]
    assert quits == [QUIT]
    asked: list[int] = []
    tray.ask_to_quit = lambda: asked.append(1)  # type: ignore[method-assign]
    tray.build_menu(menu)
    _menu_action(menu, QUIT).trigger()
    assert asked == [1]


# ------------------------------------------------------------ the flyout


def test_a_running_servers_row_has_play_stop_and_restart(
    tray: YulonTray, window: FakeWindow
) -> None:
    view = _add(window, FakeView("WotLK", "/srv/a", "running"))
    row = _row(_open(tray), "WotLK")
    assert _flyout_server_actions(row) == {
        "Play": (True, ""),
        "Stop": (True, ""),
        "Restart": (True, ""),
    }
    assert row.stop.accessibleName() == "Stop WotLK"
    assert row.restart.accessibleName() == "Restart WotLK"
    row.stop.click()
    assert view.stops == 1
    view.realm_badge.set_status("running")
    assert tray.flyout is not None
    _row(tray.flyout, "WotLK").restart.click()
    assert view.restarts == 1


def test_a_stopped_servers_row_shows_play_which_starts_and_plays_and_start(
    tray: YulonTray, window: FakeWindow
) -> None:
    """Night decision #5: a stopped server's row shows Play, not only Start."""
    view = _add(window, FakeView("Vanilla", "/srv/c", "stopped"))
    panel = _open(tray)
    row = _row(panel, "Vanilla")
    assert _flyout_server_actions(row) == {"Play": (True, ""), "Start": (True, "")}
    assert row.action.accessibleName() == "Play Vanilla", "Play is the row's first button"
    assert row.start.accessibleName() == "Start Vanilla"
    row.action.click()
    assert (view.played, view.starts, window.opened) == (1, 0, [])
    row = _row(_open(tray), "Vanilla")
    row.start.click()
    assert view.starts == 1


def test_a_stopped_servers_play_is_greyed_with_starts_reason(
    tray: YulonTray, window: FakeWindow
) -> None:
    """Play on a stopped server is a Start first: greyed when the tab's Start is, saying why."""
    view = _add(window, FakeView("Vanilla", "/srv/c", "stopped"))
    view.start_button.setEnabled(False)
    view.start_button.setToolTip("Waiting for the module install to finish")
    row = _row(_open(tray), "Vanilla")
    assert _flyout_server_actions(row) == {
        "Play": (False, "Waiting for the module install to finish"),
        "Start": (False, "Waiting for the module install to finish"),
    }


def test_a_stop_click_begun_on_one_server_never_lands_on_another(
    tray: YulonTray, window: FakeWindow
) -> None:
    """T551's press-time check, for the new buttons too: rows are reused by position."""
    first = _add(window, FakeView("WotLK", "/srv/a", "running"))
    second = _add(window, FakeView("TBC", "/srv/b", "running"))
    panel = _open(tray)
    row = _row(panel, "WotLK")
    row.stop.pressed.emit()
    window.yulon_controllers.remove(first)
    window.servers_changed.emit()  # the row is now TBC's
    row.stop.clicked.emit()
    assert (first.stops, second.stops) == (0, 0), "the click landed on another server"


def test_the_flyouts_quit_is_quit_yulon(tray: YulonTray) -> None:
    assert _open(tray).quit_button.text() == QUIT


# ------------------------------------------------ one list, two surfaces


@pytest.mark.parametrize("greyed", [False, True])
def test_the_menu_and_the_flyout_offer_the_same_actions_for_every_status(
    tray: YulonTray, window: FakeWindow, greyed: bool
) -> None:
    """R7: "both show the same per-server actions". Enumerated over every badge word, with
    the tab's buttons live and greyed: a status one surface acts on and the other does not
    fails here."""
    views = []
    for i, status in enumerate(STATUSES):
        view = _add(window, FakeView(f"S{i}", f"/srv/{i}", status))
        if greyed:
            for press in (view.start_button, view.stop_button, view.restart_button):
                set_enabled_why(press, f"Wait: {status} job.")
        views.append(view)
    menu = tray.build_menu()
    panel = _open(tray)
    offered: set[str] = set()
    for i, status in enumerate(STATUSES):
        title = f"S{i}"
        in_menu = _menu_server_actions(menu, title)
        in_flyout = _flyout_server_actions(_row(panel, title))
        assert in_menu == in_flyout, (status, in_menu, in_flyout)
        offered |= set(in_menu)
    assert offered == {"Play", "Start", "Stop", "Restart"}


def test_each_status_gets_the_actions_a_player_expects(tray: YulonTray, window: FakeWindow) -> None:
    expected = {
        "running": {"Play", "Stop", "Restart"},
        "partial": {"Stop", "Restart"},
        "loop": {"Stop"},
        "stopped": {"Play", "Start"},
        "starting": set(),
        "stopping": set(),
        "restarting": set(),
        "unknown": set(),
        "failed": set(),
    }
    for status in expected:
        _add(window, FakeView(status, f"/srv/{status}", status))
    panel = _open(tray)
    got = {status: set(_flyout_server_actions(_row(panel, status))) for status in expected}
    assert got == expected


# ------------------------------------------------- the busy guard (T690)


@pytest.mark.parametrize(
    ("verb", "press", "count"),
    [("Start", "start", "starts"), ("Stop", "stop", "stops"), ("Restart", "restart", "restarts")],
)
def test_a_press_over_a_running_action_says_why_and_does_nothing(
    tray: YulonTray,
    window: FakeWindow,
    told: list[tuple[str, str]],
    verb: str,
    press: str,
    count: str,
) -> None:
    """F-4 for every tray action: the row may be older than the job now holding the tab."""
    view = _add(window, FakeView("WotLK", "/srv/a", "stopped" if verb == "Start" else "running"))
    view.action_in_progress = lambda: "Rebuild"  # type: ignore[attr-defined]
    getattr(tray, press)(view)
    assert getattr(view, count) == 0, f"{verb} ran on top of a running action"
    assert len(told) == 1, told
    title, text = told[0]
    assert title == f"Yu'lon cannot {verb.lower()} that server yet"
    assert "WotLK" in text and "Rebuild" in text and f"Press {verb} again" in text


@pytest.mark.parametrize(
    ("verb", "press", "count", "button"),
    [
        ("Start", "start", "starts", "start_button"),
        ("Stop", "stop", "stops", "stop_button"),
        ("Restart", "restart", "restarts", "restart_button"),
    ],
)
def test_a_press_the_tab_has_greyed_since_the_row_was_built_says_why(
    tray: YulonTray,
    window: FakeWindow,
    told: list[tuple[str, str]],
    verb: str,
    press: str,
    count: str,
    button: str,
) -> None:
    view = _add(window, FakeView("WotLK", "/srv/a", "stopped" if verb == "Start" else "running"))
    set_enabled_why(getattr(view, button), "The server is not running.")
    getattr(tray, press)(view)
    assert getattr(view, count) == 0
    assert len(told) == 1 and "The server is not running." in told[0][1], told


def test_stop_from_the_tray_does_not_run_on_top_of_a_real_tabs_restart(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real `ControllerView.stop_server()` has no busy check of its own (its button is
    greyed instead): the tray's Stop must ask first. Mutation: drop the guard from
    `YulonTray.stop()`."""
    from tests.test_teardown_guard import _view
    from yulon.ui import controller_view as controller_view_module
    from yulon.ui.widgets.job import run_inline

    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)
    view, _fake, _ = _view(tmp_path)
    stopped: list[int] = []
    started: list[int] = []

    def stop(*_a: Any, **_k: Any) -> bool:
        stopped.append(1)
        return True

    def start(*_a: Any, **_k: Any) -> bool:
        started.append(1)
        return True

    monkeypatch.setattr(view.services.controller, "stop", stop)
    monkeypatch.setattr(view.services.controller, "start", start)
    view._set_busy(True, "Restart")
    win = FakeWindow()
    win.yulon_controllers.append(view)
    made = YulonTray(win, icon_factory=FakeTrayIcon, available=lambda: True)
    said: list[tuple[str, str]] = []
    made.tell = lambda title, text: said.append((title, text))  # type: ignore[method-assign]
    made.install()
    try:
        made.stop(view)
        made.start(view)
        assert (stopped, started) == ([], []), "a tray press ran over the tab's Restart"
        assert view._busy_job == "Restart", "the Restart lost its lock"
        assert len(said) == 2 and all("Restart" in text for _t, text in said), said
    finally:
        made.uninstall()
        win.deleteLater()


# ---------------------------------------------------- one quit, one name


def test_the_first_close_note_offers_quit_yulon(qapp: Any) -> None:
    box, buttons = tray_module.note_box()
    try:
        assert buttons["quit"].text() == QUIT
        assert "Quit tray" not in box.text() and QUIT in box.text()
    finally:
        box.deleteLater()


def test_no_player_facing_text_says_quit_tray_any_more() -> None:
    """One name for the one way out: a sentence that still says "Quit tray…" points the
    player at a menu entry that is not there."""
    root = Path(__file__).resolve().parent.parent
    sources = [root / "main.py", *sorted((root / "yulon").rglob("*.py"))]
    hits = [
        f"{path.relative_to(root)}:{n}"
        for path in sources
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if "Quit tray" in line or "Quit Yu'lon instead" in line
    ]
    assert hits == []
