"""T388: a maximized window is left to the system; the fit does not shrink or move it."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QMainWindow

from yulon.ui.window_fit import fit_to_screen

pytestmark = pytest.mark.usefixtures("qapp")


def test_a_maximized_window_is_not_refitted(qapp: QApplication) -> None:
    window = QMainWindow()
    fit = fit_to_screen(window, (1280, 800), (960, 640))
    window.showMaximized()
    qapp.processEvents()
    if not window.isMaximized():
        pytest.skip("this platform plugin does not maximize")
    before = (window.minimumWidth(), window.minimumHeight())
    window.setMinimumSize(10, 10)
    fit.apply()
    assert (window.minimumWidth(), window.minimumHeight()) == (10, 10), before
    window.close()
