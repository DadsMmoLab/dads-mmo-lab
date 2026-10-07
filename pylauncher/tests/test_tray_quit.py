"""Leaving the tray (T540): the quit question, Stop-then-quit, the first-close note,
the Settings dialog and the hidden start at sign-in.

Uses `test_tray.py`'s stand-ins. The boxes are asked through seams
(`choose_quit`, `choose_note`, `tell`); one test per box builds the REAL box and
reads its buttons, and one clicks a real one, because a fake of a dialog's answer
is where `qmessagebox-question-returns-int` hid.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from tests.test_tray import FakeTrayIcon, FakeView, FakeWindow, _add, _close_by_hand
from yulon import autostart, ui_settings
from yulon.ui import tray as tray_module
from yulon.ui.tray import YulonTray


@pytest.fixture
def window(qapp: Any) -> Iterator[FakeWindow]:
    win = FakeWindow()
    win.show()
    yield win
    win.hide()
    win.deleteLater()


@pytest.fixture
def quits() -> list[int]:
    return []


@pytest.fixture
def tray(window: FakeWindow, quits: list[int]) -> Iterator[YulonTray]:
    made = YulonTray(window, icon_factory=FakeTrayIcon, available=lambda: True)
    made.install()
    made.quit_app = lambda: quits.append(1)  # type: ignore[method-assign]
    yield made
    made.uninstall()


# ----------------------------------------------------------------- Quit tray…


def test_quit_with_nothing_running_just_quits(
    tray: YulonTray, window: FakeWindow, quits: list[int]
) -> None:
    _add(window, FakeView("WotLK", "/srv/a", "stopped"))
    asked: list[int] = []
    tray.choose_quit = lambda count: asked.append(count) or "cancel"  # type: ignore[method-assign]
    tray.ask_to_quit()
    assert asked == []
    assert quits == [1]


def test_quit_leaving_the_servers_running(
    tray: YulonTray, window: FakeWindow, quits: list[int]
) -> None:
    view = _add(window, FakeView("WotLK", "/srv/a", "running"))
    asked: list[int] = []
    tray.choose_quit = lambda count: asked.append(count) or "leave"  # type: ignore[method-assign]
    tray.ask_to_quit()
    assert asked == [1]
    assert quits == [1]
    assert view.stops == 0


def test_cancel_keeps_everything(tray: YulonTray, window: FakeWindow, quits: list[int]) -> None:
    _add(window, FakeView("WotLK", "/srv/a", "running"))
    tray.choose_quit = lambda count: "cancel"  # type: ignore[method-assign]
    tray.ask_to_quit()
    assert quits == []


def test_stop_then_quit_stops_through_each_tab_and_quits_when_all_are_down(
    tray: YulonTray, window: FakeWindow, quits: list[int]
) -> None:
    up = _add(window, FakeView("WotLK", "/srv/a", "running"))
    starting = _add(window, FakeView("TBC", "/srv/b", "starting"))
    stopped = _add(window, FakeView("Vanilla", "/srv/c", "stopped"))
    window.hide()
    asked: list[int] = []
    tray.choose_quit = lambda count: asked.append(count) or "stop"  # type: ignore[method-assign]
    tray.ask_to_quit()
    assert asked == [2]
    assert (up.stops, starting.stops, stopped.stops) == (1, 1, 0)
    assert window.isVisible(), "a stop can take minutes and has Stop now anyway: show it"
    assert quits == []
    up.realm_badge.set_status("stopped")
    assert quits == [], "quit before every server was down"
    starting.realm_badge.set_status("stopped")
    assert quits == [1]


def test_a_stop_that_fails_cancels_the_quit_and_shows_its_tab(
    tray: YulonTray, window: FakeWindow, quits: list[int]
) -> None:
    from pathlib import Path

    up = _add(window, FakeView("WotLK", "/srv/a", "running"))
    tray.choose_quit = lambda count: "stop"  # type: ignore[method-assign]
    tray.ask_to_quit()
    up.action_failed.emit("Stop failed: Docker did not answer")
    up.realm_badge.set_status("running")
    up.realm_badge.set_status("stopped")
    assert quits == [], "a failed stop still quit"
    assert window.shown_tabs[-1] == ("game-wotlk", Path("/srv/a"))


def test_stop_then_quit_refuses_while_a_tab_cannot_stop_and_says_why(
    tray: YulonTray, window: FakeWindow, quits: list[int]
) -> None:
    up = _add(window, FakeView("WotLK", "/srv/a", "running"))
    up.stop_button.setEnabled(False)
    up.stop_button.setToolTip("Waiting for the database import to finish")
    told: list[str] = []
    tray.tell = lambda title, text: told.append(text)  # type: ignore[method-assign]
    tray.choose_quit = lambda count: "stop"  # type: ignore[method-assign]
    tray.ask_to_quit()
    assert up.stops == 0
    assert quits == []
    assert told and "WotLK" in told[0] and "database import" in told[0]


def test_the_quit_box_has_the_three_answers_and_leaving_them_running_is_the_default(
    qapp: Any,
) -> None:
    box, buttons = tray_module.quit_box(2)
    try:
        assert set(buttons) == {"leave", "stop", "cancel"}
        assert buttons["leave"].text() == "Quit, leave servers running"
        assert buttons["stop"].text() == "Stop 2 servers, then quit"
        assert box.defaultButton() is buttons["leave"]
        assert box.escapeButton() is buttons["cancel"]
        assert box.property(tray_module.OWN_DIALOG) is True
        assert "keep running" in box.text()
    finally:
        box.deleteLater()


def test_a_real_click_on_stop_then_quit_reads_as_stop(
    qapp: Any, monkeypatch: pytest.MonkeyPatch, tray: YulonTray
) -> None:
    """The box itself, clicked: the answer is the button clicked, by identity."""
    monkeypatch.setattr(QMessageBox, "exec", QDialog.exec)

    def click() -> None:
        box = QApplication.activeModalWidget()
        if not isinstance(box, QMessageBox):
            QTimer.singleShot(20, click)
            return
        for button in box.buttons():
            if button.text().startswith("Stop 1 server"):
                button.click()

    QTimer.singleShot(20, click)
    assert tray.choose_quit(1) == "stop"


# ------------------------------------------------------------ the first close


def test_the_first_close_says_yulon_stays_in_the_tray_once(
    tray: YulonTray, window: FakeWindow
) -> None:
    asked: list[int] = []
    tray.choose_note = lambda: asked.append(1) or "got_it"  # type: ignore[method-assign]
    assert not _close_by_hand(window)
    assert window.isHidden()
    window.show()
    assert not _close_by_hand(window)
    assert asked == [1], "the note came back in the same run"


def test_dont_show_again_is_remembered(tray: YulonTray, window: FakeWindow) -> None:
    tray.choose_note = lambda: "never"  # type: ignore[method-assign]
    _close_by_hand(window)
    assert ui_settings.load_ui_settings().tray_note == "never"
    again = YulonTray(FakeWindow(), icon_factory=FakeTrayIcon, available=lambda: True)
    assert again.note_seen, "a new run showed the note it was told never to show"


def test_quit_yulon_instead_asks_the_quit_question(
    tray: YulonTray, window: FakeWindow, quits: list[int]
) -> None:
    tray.choose_note = lambda: "quit"  # type: ignore[method-assign]
    _close_by_hand(window)
    assert quits == [1]


def test_the_note_box_has_its_three_answers(qapp: Any) -> None:
    box, buttons = tray_module.note_box()
    try:
        assert {b.text() for b in buttons.values()} == {
            "Got it",
            "Quit Yu'lon instead",
            "Don't show again",
        }
        assert box.defaultButton() is buttons["got_it"]
        assert "tray" in box.text()
    finally:
        box.deleteLater()


# ------------------------------------------------------------------ settings


def test_keep_in_tray_off_is_saved_and_read_back(tray: YulonTray) -> None:
    tray.remember_keep_in_tray(False)
    assert ui_settings.load_ui_settings().keep_in_tray is False
    again = YulonTray(FakeWindow(), icon_factory=FakeTrayIcon, available=lambda: True)
    assert again.keep_in_tray is False


def test_the_settings_dialog_turns_keep_in_tray_and_sign_in_on_and_off(
    tray: YulonTray, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon.ui.tray_settings import TraySettingsDialog

    switched: list[bool] = []
    monkeypatch.setattr(autostart, "why_not", lambda *a, **k: None)
    monkeypatch.setattr(autostart, "is_enabled", lambda *a, **k: False)
    monkeypatch.setattr(autostart, "set_enabled", lambda on, *a, **k: switched.append(on))
    dialog = TraySettingsDialog(tray)
    try:
        assert dialog.keep.isChecked() and dialog.keep.isEnabled()
        assert not dialog.sign_in.isChecked() and dialog.sign_in.isEnabled()
        dialog.keep.click()
        assert tray.keep_in_tray is False
        assert ui_settings.load_ui_settings().keep_in_tray is False
        assert not dialog.sign_in.isEnabled(), "start in the tray, with the tray off?"
        dialog.keep.click()
        dialog.sign_in.click()
        assert switched == [True]
    finally:
        dialog.deleteLater()


def test_the_settings_dialog_says_when_there_is_no_tray(
    window: FakeWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon.ui.tray_settings import NO_TRAY, TraySettingsDialog

    made = YulonTray(window, icon_factory=FakeTrayIcon, available=lambda: False)
    made.install()
    monkeypatch.setattr(autostart, "why_not", lambda *a, **k: None)
    monkeypatch.setattr(autostart, "is_enabled", lambda *a, **k: False)
    dialog = TraySettingsDialog(made)
    try:
        assert not dialog.keep.isEnabled()
        assert not dialog.sign_in.isEnabled()
        assert dialog.note.text() == NO_TRAY
        assert not dialog.note.isHidden()
    finally:
        dialog.deleteLater()
        made.uninstall()


def test_a_sign_in_switch_that_fails_says_why_and_stays_off(
    tray: YulonTray, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon.ui.tray_settings import TraySettingsDialog

    def refuse(on: bool, *a: Any, **k: Any) -> None:
        raise OSError("access denied")

    monkeypatch.setattr(autostart, "why_not", lambda *a, **k: None)
    monkeypatch.setattr(autostart, "is_enabled", lambda *a, **k: False)
    monkeypatch.setattr(autostart, "set_enabled", refuse)
    dialog = TraySettingsDialog(tray)
    try:
        dialog.sign_in.click()
        assert not dialog.sign_in.isChecked()
        assert "access denied" in dialog.note.text()
    finally:
        dialog.deleteLater()


def test_a_source_checkout_greys_the_sign_in_switch_with_the_reason(
    tray: YulonTray, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon.ui.tray_settings import TraySettingsDialog

    monkeypatch.setattr(autostart, "why_not", lambda *a, **k: autostart.NOT_INSTALLED)
    monkeypatch.setattr(autostart, "is_enabled", lambda *a, **k: False)
    dialog = TraySettingsDialog(tray)
    try:
        assert not dialog.sign_in.isEnabled()
        assert dialog.sign_in.toolTip() == autostart.NOT_INSTALLED
        # On the dialog, not only in a tooltip (yulon-win11: a greyed box with no reason).
        assert dialog.note.text() == autostart.NOT_INSTALLED
        assert not dialog.note.isHidden()
    finally:
        dialog.deleteLater()


# ------------------------------------------------------- started at sign-in


def test_a_sign_in_start_stays_hidden_in_the_tray(window: FakeWindow) -> None:
    window.hide()
    made = YulonTray(window, icon_factory=FakeTrayIcon, available=lambda: True)
    made.install()
    try:
        made.start_hidden()
        assert window.isHidden()
    finally:
        made.uninstall()


def test_a_sign_in_start_waits_for_a_late_tray_then_sits_in_it(window: FakeWindow) -> None:
    """A desktop's tray can come up after the programs it starts at sign-in."""
    window.hide()
    there = [False]
    made = YulonTray(window, icon_factory=FakeTrayIcon, available=lambda: there[0])
    made.install()
    try:
        made.start_hidden()
        assert made.icon is None and window.isHidden()
        made._wait_for_the_tray()
        assert window.isHidden()
        there[0] = True
        made._wait_for_the_tray()
        assert made.icon is not None and made.icon.isVisible()
        assert window.isHidden()
        assert not QApplication.quitOnLastWindowClosed()
    finally:
        made.uninstall()


def test_a_sign_in_start_with_no_tray_at_all_shows_the_window(window: FakeWindow) -> None:
    window.hide()
    made = YulonTray(window, icon_factory=FakeTrayIcon, available=lambda: False)
    made.install()
    try:
        made.start_hidden()
        for _ in range(tray_module.TRAY_WAIT_TRIES):
            made._wait_for_the_tray()
        assert window.isVisible(), "no tray came, and nothing was shown"
    finally:
        made.uninstall()


def test_on_windows_the_note_says_where_a_hidden_icon_is(
    qapp: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows 11 puts a new tray icon under the ^ beside the clock (yulon-win11, 2026-10-07)."""
    monkeypatch.setattr(tray_module.sys, "platform", "win32")
    box, _buttons = tray_module.note_box()
    try:
        assert "^" in box.text()
    finally:
        box.deleteLater()
    monkeypatch.setattr(tray_module.sys, "platform", "linux")
    box, _buttons = tray_module.note_box()
    try:
        assert "^" not in box.text()
    finally:
        box.deleteLater()


# ------------------------------------------------- Codex review, 2026-10-07


def test_a_stop_that_ends_without_the_server_down_ends_the_wait_and_says_so(
    tray: YulonTray, window: FakeWindow, quits: list[int]
) -> None:
    """Adversarial review [high]: Docker went quiet after the Stop, so the badge read
    "unknown" and never "stopped"; the wait had no end and every later Quit was refused."""
    from pathlib import Path

    up = _add(window, FakeView("WotLK", "/srv/a", "running"))
    told: list[str] = []
    tray.tell = lambda title, text: told.append(text)  # type: ignore[method-assign]
    tray.choose_quit = lambda count: "stop"  # type: ignore[method-assign]
    tray.ask_to_quit()
    up.realm_badge.set_status("unknown")  # the Stop's job ended; its reading did not answer
    assert quits == []
    assert told and "WotLK" in told[0]
    assert window.shown_tabs[-1] == ("game-wotlk", Path("/srv/a"))
    # The wait is over: Quit tray… asks again, and leaving it running quits.
    tray.choose_quit = lambda count: "leave"  # type: ignore[method-assign]
    tray.ask_to_quit()
    assert quits == [1]


def test_quit_tray_again_while_waiting_can_quit_now_or_keep_waiting(
    tray: YulonTray, window: FakeWindow, quits: list[int]
) -> None:
    _add(window, FakeView("WotLK", "/srv/a", "running"))
    tray.choose_quit = lambda count: "stop"  # type: ignore[method-assign]
    tray.ask_to_quit()
    asked: list[int] = []
    tray.choose_while_stopping = lambda count: asked.append(count) or "wait"  # type: ignore[method-assign]
    tray.ask_to_quit()
    assert asked == [1] and quits == []
    tray.choose_while_stopping = lambda count: "quit"  # type: ignore[method-assign]
    tray.ask_to_quit()
    assert quits == [1]


def test_the_while_stopping_box_has_its_two_answers(qapp: Any) -> None:
    box, buttons = tray_module.while_stopping_box(2)
    try:
        assert set(buttons) == {"wait", "quit"}
        assert box.defaultButton() is buttons["wait"]
        assert box.property(tray_module.OWN_DIALOG) is True
    finally:
        box.deleteLater()


def test_keep_in_tray_off_also_turns_start_at_sign_in_off(
    tray: YulonTray, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Normal review [P2]: the sign-in entry stayed on, greyed, with no way to turn it off."""
    from yulon.ui.tray_settings import TraySettingsDialog

    switched: list[bool] = []
    on = [True]
    monkeypatch.setattr(autostart, "why_not", lambda *a, **k: None)
    monkeypatch.setattr(autostart, "is_enabled", lambda *a, **k: on[0])

    def set_enabled(value: bool, *a: Any, **k: Any) -> None:
        switched.append(value)
        on[0] = value

    monkeypatch.setattr(autostart, "set_enabled", set_enabled)
    dialog = TraySettingsDialog(tray)
    try:
        assert dialog.sign_in.isChecked()
        dialog.keep.click()
        assert switched == [False]
        assert not dialog.sign_in.isChecked()
    finally:
        dialog.deleteLater()


def test_a_sign_in_entry_can_always_be_turned_off(
    window: FakeWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even where it could not be turned on now (no tray here today), an entry that is on
    can be turned off."""
    from yulon.ui.tray_settings import TraySettingsDialog

    made = YulonTray(window, icon_factory=FakeTrayIcon, available=lambda: False)
    made.install()
    monkeypatch.setattr(autostart, "why_not", lambda *a, **k: None)
    monkeypatch.setattr(autostart, "is_enabled", lambda *a, **k: True)
    switched: list[bool] = []
    monkeypatch.setattr(autostart, "set_enabled", lambda v, *a, **k: switched.append(v))
    dialog = TraySettingsDialog(made)
    try:
        assert dialog.sign_in.isChecked() and dialog.sign_in.isEnabled()
        dialog.sign_in.click()
        assert switched == [False]
    finally:
        dialog.deleteLater()
        made.uninstall()


def test_the_first_close_note_sits_over_the_window(
    tray: YulonTray, window: FakeWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """yulon-ubuntu: a parentless note opened in the screen's corner, away from the window."""
    seen: list[Any] = []
    real = tray_module.note_box

    def spy(parent: Any = None) -> Any:
        box, buttons = real(parent)
        seen.append(box.parent())
        return box, buttons

    monkeypatch.setattr(tray_module, "note_box", spy)
    tray.choose_note()
    assert seen == [window]


def test_a_stop_that_never_ends_ends_the_wait_after_its_deadline(
    tray: YulonTray, window: FakeWindow, quits: list[int]
) -> None:
    """Adversarial review [high] on e76f5175: no status and no failure ever came; the wait
    now has a deadline past the Stop's own (docker.STOP_PROCESS_DEADLINE_SECONDS)."""
    up = _add(window, FakeView("WotLK", "/srv/a", "running"))
    told: list[str] = []
    tray.tell = lambda title, text: told.append(text)  # type: ignore[method-assign]
    tray.choose_quit = lambda count: "stop"  # type: ignore[method-assign]
    tray.ask_to_quit()
    assert up.realm_badge.status == "stopping"
    assert tray._stop_deadline is not None and tray._stop_deadline.isActive()
    assert tray._stop_deadline.interval() > tray_module.docker.STOP_PROCESS_DEADLINE_SECONDS * 1000
    tray._stop_deadline.timeout.emit()
    assert quits == []
    assert told and "WotLK" in told[0]
    tray.choose_quit = lambda count: "leave"  # type: ignore[method-assign]
    tray.ask_to_quit()
    assert quits == [1], "the wait did not end"


def test_a_refused_application_quit_does_not_turn_close_to_tray_off(
    tray: YulonTray, window: FakeWindow
) -> None:
    """Adversarial review [medium]: Cmd+Q refused by the busy guard latched the quit flag,
    and every later close quit instead of hiding."""
    from PySide6.QtCore import QEvent

    app = QApplication.instance()
    assert isinstance(app, QApplication)
    tray.note_seen = True
    tray.eventFilter(app, QEvent(QEvent.Type.Quit))
    assert not tray.keeping  # the quit is let through while it is being decided
    QApplication.processEvents()  # the quit was refused: the loop runs on
    assert tray.keeping
    assert not _close_by_hand(window)
    assert window.isHidden()
