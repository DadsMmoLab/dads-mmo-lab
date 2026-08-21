"""Shared pytest fixtures: an offscreen Qt application for the UI tests."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp() -> Iterator[object]:
    """One offscreen `QApplication` for the whole session (Qt allows exactly one)."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def process_events(ms: int = 50) -> None:
    """Pump the Qt event loop for `ms` milliseconds (lets queued signals deliver)."""
    from PySide6.QtCore import QCoreApplication, QDeadlineTimer, QEventLoop

    deadline = QDeadlineTimer(ms)
    while not deadline.hasExpired():
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let a test block on a modal dialog.

    `QMessageBox.warning()` and friends are modal: called from a slot in an
    offscreen test they wait for a click that never comes, and the whole run
    hangs. Tests that care about a dialog patch it themselves and see their own
    patch (this one is applied first).
    """
    try:
        from PySide6.QtWidgets import QMessageBox
    except ImportError:  # pragma: no cover - Qt-less environments skip UI tests anyway
        return
    for name in ("warning", "information", "critical", "about"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
