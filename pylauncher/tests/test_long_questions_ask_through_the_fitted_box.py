"""T243: every long Yes/No question is asked through `ask_yes_no()`, the box that fits the screen.

`tests/test_dialogs_fit_the_screen.py` proves the box fits, scrolls and keeps
its buttons on each screen size. This file proves the presses actually use it:
a press that went back to the static `QMessageBox.question()` would build Qt's
own box, which grows with its text and can put Yes and No below the screen,
and every geometry test would still pass, because none of them goes through
the press.

The modal is replaced by a recorder on `QMessageBox.exec` -- the only way the
fitted box is answered -- and answers No. The static `question()` is the
conftest's, which also answers No: so "the box the recorder saw" is the
difference, not the answer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from tests.test_controller_view import (
    WOTLK,
    _adopt_services,
    _forget_action,
    _mob_tab,
    _Ps,
    _rebuild_services,
    _select_module,
    _server_cloned_view,
    _services,
    _updates_services,
)
from yulon import runner
from yulon.catalog.installer import rebuild_confirmation
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import ControllerView, ask_backup_choice
from yulon.ui.message_box import FittedMessageBox, ask_yes_no
from yulon.ui.widgets.job import run_inline

SB = QMessageBox.StandardButton

QT_QUESTION = QMessageBox.question
"""Qt's own static `question()`, read at import: before any test or guard patched it."""


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


@pytest.fixture
def boxes(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Each box `exec()` was called on, as read when it was, answered No."""
    seen: list[dict[str, Any]] = []

    def record(box: QMessageBox) -> int:
        seen.append(
            {
                "fitted": isinstance(box, FittedMessageBox),
                "title": box.windowTitle(),
                "text": box.text(),
                "buttons": box.standardButtons(),
                "default": box.standardButton(box.defaultButton()),
                "escape": box.standardButton(box.escapeButton()),
            }
        )
        return int(SB.No.value)

    monkeypatch.setattr(QMessageBox, "exec", record)
    return seen


def _the_yes_no_box(boxes: list[dict[str, Any]], text: str) -> dict[str, Any]:
    assert len(boxes) == 1, f"expected one question through the fitted box, saw {boxes}"
    (box,) = boxes
    assert box["fitted"], "asked through a box that does not fit itself to the screen"
    assert box["text"] == text
    assert box["buttons"] == SB.Yes | SB.No
    assert box["default"] == SB.No, "Enter would not decline"
    assert box["escape"] == SB.No, "Escape and the close button would not decline"
    return box


def test_the_rebuild_question_is_the_fitted_box(
    qapp: object, ps: _Ps, tmp_path: Path, boxes: list[dict[str, Any]]
) -> None:
    services, started = _rebuild_services(ps, tmp_path)
    view = ControllerView(WOTLK, services, status_poll_ms=0)

    assert view.rebuild_server() is False
    assert started == []
    box = _the_yes_no_box(
        boxes,
        rebuild_confirmation(
            WOTLK, services.controller.server_dir, kept_build=services.kept_build is not None
        ),
    )
    assert box["title"] == f"Rebuild {WOTLK.name}?"


def test_the_forget_record_question_is_the_fitted_box(
    qapp: object, ps: _Ps, tmp_path: Path, boxes: list[dict[str, Any]]
) -> None:
    """T399: "Forget Yu'lon's record..." asked through the static `question()`, which has no
    height limit and no escape button. Answered No here: nothing is forgotten.

    Catches the press going back to `QMessageBox.question(...)`: the recorder sees no box.
    """
    from yulon import module_answers
    from yulon.controller_wow_wotlk import modules
    from yulon.ui.controller_view import FORGET_RECORD_QUESTION

    view, _sql, _asked = _mob_tab(ps, tmp_path)
    _select_module(view, "baby-mobs")
    view._module_action("install")
    boxes.clear()  # the install's own questions are not this test's

    action = _forget_action(view, "baby-mobs")
    assert action is not None
    action.trigger()

    box = _the_yes_no_box(boxes, FORGET_RECORD_QUESTION.format(name="Baby Mobs"))
    assert box["title"] == "Forget Yu'lon's record?"
    manifest = modules.store().load("mod", "baby-mobs")
    assert module_answers.read_applied(tmp_path / "srv", manifest) is not None, "No forgot it"


def test_the_forget_press_has_no_static_question_call() -> None:
    """T399's own static-call check, until a repo-wide guard covers `question` (T355 covers
    warning/information/critical): no `QMessageBox.question(...)` inside the Forget press.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(ControllerView._forget_module_record)))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "question"
    ]
    assert calls == []


def test_the_return_to_the_pin_question_is_the_fitted_box(
    qapp: object, ps: _Ps, tmp_path: Path, boxes: list[dict[str, Any]]
) -> None:
    view, spy = _server_cloned_view(ps, tmp_path)
    route = view.services.update_to_latest
    assert route is not None

    assert view.return_to_the_tested_pin() is False
    assert spy.pin_presses == []
    _the_yes_no_box(boxes, route.pin_confirmation())


def test_the_tabs_own_yes_no_questions_are_the_fitted_box(
    qapp: object, ps: _Ps, tmp_path: Path, boxes: list[dict[str, Any]]
) -> None:
    """`_confirm()`: Repair server files and every other Yes/No the tab asks with it."""
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)

    assert view._confirm("Repair server files…", "Repair this server's files now?") is False
    box = _the_yes_no_box(boxes, "Repair this server's files now?")
    assert box["title"] == "Repair server files…"


def test_the_database_updates_question_is_the_fitted_box(
    qapp: object, ps: _Ps, tmp_path: Path, boxes: list[dict[str, Any]]
) -> None:
    services, started, _asked = _updates_services(ps, tmp_path)
    view = ControllerView(WOTLK, services, status_poll_ms=0)

    assert view.apply_database_updates() is False
    assert started == []
    _the_yes_no_box(boxes, "apply three files?")


def test_the_adopt_question_is_the_fitted_box(
    qapp: object, ps: _Ps, tmp_path: Path, boxes: list[dict[str, Any]]
) -> None:
    services, started, _asked, _probed = _adopt_services(ps, tmp_path)
    view = ControllerView(WOTLK, services, status_poll_ms=0)

    assert view.adopt_as_imported() is False
    assert started == []
    _the_yes_no_box(boxes, "write one row?")


def test_the_database_corrections_question_is_the_fitted_box(
    qapp: object, ps: _Ps, tmp_path: Path, boxes: list[dict[str, Any]]
) -> None:
    from tests.test_plan_corrections import _database, _Route, _view

    route = _Route("stale")
    view = _view(ps, tmp_path, route)
    _database(ps, view, up=True)

    assert view.apply_database_corrections() is False  # type: ignore[attr-defined]
    assert route.pressed == []
    assert len(boxes) == 1 and boxes[0]["fitted"], boxes
    assert boxes[0]["buttons"] == SB.Yes | SB.No and boxes[0]["default"] == SB.No


# -- what a real click answers, and what the suite's guard answers -------------


@pytest.mark.parametrize(
    ("answer", "said"),
    [(SB.Yes, True), (int(SB.Yes.value), True), (SB.No, False), (SB.NoButton, False)],
    ids=["yes-member", "yes-plain-int", "no", "escape"],
)
def test_ask_yes_no_is_yes_only_for_yes_in_either_shape(
    qapp: object, monkeypatch: pytest.MonkeyPatch, answer: object, said: bool
) -> None:
    """T33: `exec()` answers a plain int on PySide6 6.11; an enum check would read Yes as No."""
    monkeypatch.setattr(QMessageBox, "exec", lambda _box: answer)
    assert ask_yes_no(None, "t", "q") is said


def test_the_suites_guard_answers_a_yes_no_box_the_way_it_answers_the_static_question(
    qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`conftest._no_modal_dialogs`: a test that answers `question()` answers the fitted box too.

    The presses moved from the static `question()` to `ask_yes_no()` (T243), and
    every test that says Yes to one of them says it by patching `question`. The
    guard hands a Yes/No box to whatever `question` is now, with the box's own
    parent, title, text, buttons and default.
    """
    heard: list[tuple[object, ...]] = []

    def question(*args: object) -> object:
        heard.append(args)
        return SB.Yes

    monkeypatch.setattr(QMessageBox, "question", question)
    assert ask_yes_no(None, "Title", "Text?") is True
    assert heard == [(None, "Title", "Text?", SB.Yes | SB.No, SB.No)]


def test_the_suites_guard_answers_a_three_way_box_no_whatever_question_says(
    qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only Yes/No is the static question's shape: a test that said Yes to a Rebuild must not
    also choose "back up first" in a three-way box it never meant to answer."""
    monkeypatch.setattr(QMessageBox, "question", lambda *a: SB.Yes)
    answer = ask_backup_choice(None, "t", "q", back_up_first="first", without_backup="without")
    assert answer.value == "cancel"


def test_the_pad_sees_which_paragraph_it_stopped_on(qapp: object) -> None:
    """A paragraph is a stop only when the question overflows, and the theme rings the one the
    pad is on: the gold line on its left edge, there when it has the focus and not before."""
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QLabel, QWidget

    from tests.conftest import process_events
    from yulon.ui.theme import COLOR_GOLD_BRIGHT, QUESTION_PARAGRAPH, apply_dadcraft_theme

    parent = QWidget()
    apply_dadcraft_theme(parent, width=960)
    # Six long paragraphs: taller than the suite's 800×800 offscreen screen.
    text = "\n\n".join(["The server is rebuilt from its sources. " * 30] * 6)
    box = FittedMessageBox(QMessageBox.Icon.Question, "t", text, SB.Yes | SB.No, parent)
    try:
        box.show()
        process_events()
        paragraphs = box.findChildren(QLabel, QUESTION_PARAGRAPH)
        assert len(paragraphs) == 6
        # The last: a programmatic `setFocus()` from the buttons is a pad move, and the pad
        # coming up from the buttons lands on the last paragraph (live test, 2026-10-05).
        stop = paragraphs[-1]
        assert stop.focusPolicy() & Qt.FocusPolicy.TabFocus, "not a stop the pad can reach"

        def edge() -> QColor:
            # Rendered over black, not `grab()`ed: a label paints no background
            # of its own, and what `grab()` leaves under it is not a colour.
            image = QImage(stop.size(), QImage.Format.Format_ARGB32)
            image.fill(QColor("black"))
            stop.render(image)
            return image.pixelColor(0, stop.height() // 2)

        unlit = edge()
        stop.setFocus()
        process_events()
        assert stop.hasFocus()
        assert edge() == QColor(COLOR_GOLD_BRIGHT), "the focused paragraph is not ringed"
        assert unlit != QColor(COLOR_GOLD_BRIGHT), "every paragraph is ringed, focused or not"
    finally:
        box.hide()
        box.deleteLater()
        parent.deleteLater()


def test_a_question_that_shows_whole_has_no_paragraph_stops(qapp: object) -> None:
    """The pad goes from button to button, as before T243, when nothing needs scrolling."""
    from PySide6.QtWidgets import QLabel

    from tests.conftest import process_events
    from yulon.ui.theme import QUESTION_PARAGRAPH

    box = FittedMessageBox(QMessageBox.Icon.Question, "t", "Short?\n\nYes.", SB.Yes | SB.No)
    try:
        box.show()
        process_events()
        paragraphs = box.findChildren(QLabel, QUESTION_PARAGRAPH)
        assert [p.text() for p in paragraphs] == ["Short?", "Yes."]
        assert not any(p.focusPolicy() & Qt.FocusPolicy.TabFocus for p in paragraphs)
    finally:
        box.hide()
        box.deleteLater()


def test_a_rich_text_question_stays_one_rich_label(qapp: object) -> None:
    """Split on blank lines, rich text would lose its markup across paragraphs; it is not split,
    and is shown as rich text, as `QMessageBox`'s own label would have shown it."""
    from PySide6.QtWidgets import QLabel

    from yulon.ui.theme import QUESTION_PARAGRAPH

    text = "<p>Rebuild <b>now</b>?</p>\n\n<p>Say no and nothing happens.</p>"
    box = FittedMessageBox(QMessageBox.Icon.Question, "t", text, SB.Yes | SB.No)
    try:
        paragraphs = box.findChildren(QLabel, QUESTION_PARAGRAPH)
        assert [p.text() for p in paragraphs] == [text]
        assert paragraphs[0].textFormat() == Qt.TextFormat.RichText
    finally:
        box.deleteLater()


def test_the_suites_guard_outlives_a_tests_own_undo(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test's `monkeypatch.undo()` must not hand the next question to a real modal loop:
    `test_an_older_press_failing_leaves_the_current_press_waiting` blocked there until killed.
    Read by name, because the real `exec()` would block this test the same way."""
    monkeypatch.setattr(QMessageBox, "exec", lambda _box: SB.Yes)
    monkeypatch.undo()
    assert QMessageBox.exec.__name__ == "_answer_like_the_static_question"
    assert QMessageBox.question(None, "t", "q") == SB.No


def test_a_qt_whose_box_is_laid_out_otherwise_is_said_in_the_log_and_still_asks(
    qapp: object, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Codex (adversarial): the fit leans on Qt's own label. A Qt without it gets Qt's own box,
    which still asks and still answers, and the log says the question is not scrolled."""
    import logging

    from yulon.ui import message_box

    monkeypatch.setattr(message_box, "_QT_MESSAGE_LABEL", "not_in_this_qt")
    with caplog.at_level(logging.WARNING):
        box = FittedMessageBox(QMessageBox.Icon.Question, "Rebuild?", "Long.", SB.Yes | SB.No)
    try:
        assert box._scroll is None
        assert "'Rebuild?' is shown unscrolled" in caplog.text
        box.show()  # sizing an unscrolled box must not raise
        assert box.text() == "Long."
    finally:
        box.hide()
        box.deleteLater()


def test_the_suites_guard_never_hands_a_box_to_qts_own_question(
    qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex (adversarial): a test that put the real static `question()` back must not have the
    guard open a real modal through it; the box is answered No instead."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    monkeypatch.setattr(QMessageBox, "question", QT_QUESTION)
    opened: list[str] = []

    def watchdog() -> None:  # a real modal would otherwise hold this test for ever
        box = QApplication.activeModalWidget()
        if box is not None:
            opened.append(box.windowTitle())
            box.close()

    # A timer object, stopped below: a pending single shot outlives this test and
    # closes the next test's open box (it emptied test_message_box_buttons_fit's).
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(watchdog)
    timer.start(1500)
    try:
        assert ask_yes_no(None, "Real modal?", "q") is False
    finally:
        timer.stop()
    assert opened == [], "the guard opened a real modal through Qt's own question()"


def test_the_suites_guard_holds_whatever_order_the_tests_run_in() -> None:
    """Cold review: test A patches `question`; B runs under the guard; C puts Qt's own
    `question()` back and asks. On the guard that took its own leftover fake for Qt's, C opened
    a real modal. Run in a child pytest, in that order (`tests/guard_order_probe.py` says why)."""
    import os
    import subprocess
    import sys

    pylauncher = Path(__file__).resolve().parents[1]
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:randomly",
            "-p",
            "no:xdist",
            "-p",
            "no:cacheprovider",
            "tests/guard_order_probe.py",
        ],
        cwd=pylauncher,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert done.returncode == 0 and "3 passed" in done.stdout, done.stdout + done.stderr


LONG = "\n\n".join(["The server is rebuilt from its sources. " * 30] * 6)
"""Six long paragraphs: taller than the suite's 800×800 offscreen screen."""


def _set_icon_pixmap(box: QMessageBox) -> None:
    from PySide6.QtGui import QPixmap

    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.red)
    box.setIconPixmap(pixmap)


def _set_check_box(box: QMessageBox) -> None:
    from PySide6.QtWidgets import QCheckBox

    box.setCheckBox(QCheckBox("Do not ask again"))


LATER_SETTERS = {
    "informative-text": lambda box: box.setInformativeText("And one more thing."),
    "icon": lambda box: box.setIcon(QMessageBox.Icon.Warning),
    "icon-pixmap": _set_icon_pixmap,
    "check-box": _set_check_box,
    "detailed-text": lambda box: box.setDetailedText("Details."),
    "text": lambda box: box.setText(LONG.replace("server", "world")),
}


@pytest.mark.parametrize("change", sorted(LATER_SETTERS))
def test_a_later_setter_keeps_the_question_scrolled_and_shown(qapp: object, change: str) -> None:
    """Cold review: Qt lays its box out again on these setters (`setupLayout()`), which dropped
    the scroll area while Qt's own label stayed hidden -- the question vanished. The question is
    put back in its place, still scrolled, still the box's own text, and the box still fits."""
    from PySide6.QtWidgets import QLabel, QScrollArea

    from tests.conftest import process_events
    from yulon.ui.message_box import QUESTION_SCROLL
    from yulon.ui.theme import QUESTION_PARAGRAPH

    box = FittedMessageBox(QMessageBox.Icon.Question, "t", LONG, SB.Yes | SB.No)
    try:
        LATER_SETTERS[change](box)
        box.show()
        process_events()
        scroll = box.findChild(QScrollArea, QUESTION_SCROLL)
        assert scroll is not None and scroll.isVisible()
        assert box.layout().indexOf(scroll) >= 0, "the question is no longer in the box's layout"
        own = box.findChild(QLabel, "qt_msgbox_label")
        assert own is not None and not own.isVisible()
        shown = [p for p in box.findChildren(QLabel, QUESTION_PARAGRAPH) if p.isVisibleTo(box)]
        assert " ".join(p.text() for p in shown) == " ".join(box.text().split("\n\n"))
        room = box.screen().availableGeometry()
        assert room.contains(box.frameGeometry()), (box.frameGeometry(), room)
    finally:
        box.hide()
        box.deleteLater()


def test_enter_on_a_paragraph_presses_no_button(qapp: object) -> None:
    """Cold review: the install-folder question's default is Yes, and Enter on a focused
    paragraph pressed it. A paragraph is a place to read; Enter there does nothing."""
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLabel

    from tests.conftest import process_events
    from yulon.ui.theme import QUESTION_PARAGRAPH

    box = FittedMessageBox(QMessageBox.Icon.Question, "t", LONG, SB.Yes | SB.No)
    box.setDefaultButton(SB.Yes)
    clicked: list[object] = []
    box.buttonClicked.connect(clicked.append)
    try:
        box.show()
        process_events()
        paragraph = box.findChildren(QLabel, QUESTION_PARAGRAPH)[-1]  # where the pad lands
        paragraph.setFocus()
        process_events()
        assert paragraph.hasFocus()
        for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            QTest.keyClick(paragraph, key)
            process_events()
        assert clicked == [] and box.isVisible(), "Enter on a paragraph pressed a button"
        # Control: Enter where it belongs still presses.
        yes = box.button(SB.Yes)
        assert yes is not None
        yes.setFocus()
        QTest.keyClick(yes, Qt.Key.Key_Return)
        process_events()
        assert clicked == [yes]
    finally:
        box.hide()
        box.deleteLater()


# -- T243 live test (yulon-win11, 2026-10-05): where the focus lands among the paragraphs --------


def _open_long_box() -> FittedMessageBox:
    """A shown Yes/No box, default No, whose question overflows the 800×800 offscreen screen."""
    from tests.conftest import process_events

    box = FittedMessageBox(QMessageBox.Icon.Question, "t", LONG, SB.Yes | SB.No)
    box.setDefaultButton(SB.No)
    box.show()
    process_events()
    return box


def _paragraphs(box: FittedMessageBox) -> list[Any]:
    from PySide6.QtWidgets import QLabel

    from yulon.ui.theme import QUESTION_PARAGRAPH

    return [p for p in box.findChildren(QLabel, QUESTION_PARAGRAPH) if p.isVisibleTo(box)]


def _in_view(box: FittedMessageBox, widget: Any) -> bool:
    """Whether all of `widget` shows in the question's scroll area."""
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtWidgets import QScrollArea

    from yulon.ui.message_box import QUESTION_SCROLL

    scroll = box.findChild(QScrollArea, QUESTION_SCROLL)
    assert scroll is not None
    viewport = scroll.viewport()
    seen = QRect(widget.mapTo(viewport, QPoint(0, 0)), widget.size())
    return viewport.rect().contains(seen)


def test_tab_onto_a_paragraph_scrolls_it_into_view(qapp: object) -> None:
    """Live test: Tab from No wrapped to the first paragraph while it was scrolled out of view."""
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QScrollArea

    from tests.conftest import process_events
    from yulon.ui.message_box import QUESTION_SCROLL

    box = _open_long_box()
    try:
        scroll = box.findChild(QScrollArea, QUESTION_SCROLL)
        assert scroll is not None
        bar = scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        process_events()
        first = _paragraphs(box)[0]
        assert not _in_view(box, first), "control: the first paragraph starts out of view"
        no = box.button(SB.No)
        assert no is not None
        no.setFocus()
        process_events()
        for _ in range(4):
            QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab)
            process_events()
            if QApplication.focusWidget() in _paragraphs(box):
                break
        focused = QApplication.focusWidget()
        assert focused in _paragraphs(box), "Tab never reached a paragraph"
        assert _in_view(box, focused), "Tab put the focus on a paragraph out of view"
    finally:
        box.hide()
        box.deleteLater()


def test_pad_up_from_the_buttons_lands_on_the_last_paragraph_and_shows_it(qapp: object) -> None:
    """Live test: Up from No skipped the paragraphs scrolled below the view."""
    from tests.conftest import process_events
    from yulon.ui.gamepad import Direction, Navigator

    box = _open_long_box()
    try:
        last = _paragraphs(box)[-1]
        assert not _in_view(box, last), "control: the last paragraph starts below the view"
        no = box.button(SB.No)
        assert no is not None
        no.setFocus()
        process_events()
        assert Navigator().navigate(Direction.UP)
        process_events()
        assert QApplication.focusWidget() is last, "Up did not land on the nearest paragraph"
        assert _in_view(box, last)
    finally:
        box.hide()
        box.deleteLater()


def test_pad_down_from_the_last_paragraph_lands_on_the_default_button(qapp: object) -> None:
    """Live test: Down from the last paragraph landed on Yes; the default (No) is where it goes."""
    from tests.conftest import process_events
    from yulon.ui.gamepad import Direction, Navigator

    box = _open_long_box()
    try:
        last = _paragraphs(box)[-1]
        last.setFocus()
        process_events()
        assert QApplication.focusWidget() is last
        assert Navigator().navigate(Direction.DOWN)
        process_events()
        assert QApplication.focusWidget() is box.button(SB.No)
    finally:
        box.hide()
        box.deleteLater()
