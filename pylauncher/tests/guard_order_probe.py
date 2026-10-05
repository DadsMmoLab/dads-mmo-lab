"""T243 cold review: three tests that must run in THIS order, in one process, to be a test at all.

Not collected by the suite (no `test_` prefix):
`tests/test_long_questions_ask_through_the_fitted_box.py` runs it in a child pytest with random
order and xdist off, because the defect lives between tests. A test's own `monkeypatch` is set
up before the modal guard (`_no_forced_exit` asks for it first), so it is undone AFTER the guard,
and puts the guard's fake `question()` back on `QMessageBox` for good. A guard that then took
"whatever `question` is when I start" for Qt's own was holding its own fake, and a later test
that put Qt's real `question()` back had the guard open a real modal through it: the run sat
there until a watchdog killed it.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from yulon.ui.message_box import ask_yes_no

QT_QUESTION = QMessageBox.question
"""Qt's own static `question()`, read when this module is imported: before any test patched it."""

WATCHDOG_MS = 1500


def test_a_a_test_patches_question(qapp: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    assert ask_yes_no(None, "t", "q") is True


def test_b_the_next_test_asks_under_the_guard(qapp: object) -> None:
    assert ask_yes_no(None, "t", "q") is False


def test_c_a_test_puts_qts_own_question_back(qapp: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(QMessageBox, "question", QT_QUESTION)
    opened: list[str] = []

    def watchdog() -> None:
        box = QApplication.activeModalWidget()
        if box is not None:
            opened.append(box.windowTitle())
            box.close()

    # A timer object, stopped below: a pending single shot outlives this test and
    # closes the next test's open box (it emptied test_message_box_buttons_fit's).
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(watchdog)
    timer.start(WATCHDOG_MS)
    try:
        answer = ask_yes_no(None, "Real modal?", "q")
    finally:
        timer.stop()
    assert opened == [], "the guard opened a real modal through Qt's own question()"
    assert answer is False
