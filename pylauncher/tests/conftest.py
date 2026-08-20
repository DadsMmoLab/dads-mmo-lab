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
