"""T388's probe: open the real main window on one screen and measure what a player can reach.

Run by `tests/test_main_window_fits_the_screen.py` in a child process, for the
reason `dialog_fit_probe.py` gives: the screen is the thing under test, and a
process has only the screens its platform plugin made.
`QT_QPA_PLATFORM=offscreen:configfile=<json>` gives this one a Steam Deck, an
800×600 desktop, or two monitors side by side. The offscreen plugin draws a 2 px
frame round a top-level window once it is created, so a window "the size of the
screen" is 4 px wider than it, as a real framed window is.

The window is the one `main.build_window()` builds -- the path the app ships,
not the smoke-test one -- with one remembered WotLK server, so the sidebar holds
a server tab as well as Catalog and Logs. What `build_window()` would do to the
machine is shut off the way `tests/test_main.py`'s window fixture shuts it off:
no GitHub check, no `state.json` read or written, no sweep of client copies, no
status polling.

"Reachable" is measured the way a player reaches a control: the tab is chosen
by clicking its sidebar entry, every scroll area the control is in is scrolled
to it (what the pad does, `gamepad._scroll_into_view`, and what dragging the
scroll bars does by hand), and then its centre has to be on the screen and
inside the window and every viewport it is in.

`python -m tests.main_window_fit_probe [open|move]` from `pylauncher/`. Prints
one JSON object.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractScrollArea,
    QApplication,
    QMainWindow,
    QScrollArea,
    QTabWidget,
    QWidget,
)

from yulon.ui.theme import CHECK_UPDATES_BUTTON

SETTLE_MS = 400
"""Long enough for the window's 120 ms restyle timer and the layout after it."""


def _rect(r: QRect) -> list[int]:
    return [r.x(), r.y(), r.width(), r.height()]


def _global(widget: QWidget) -> QRect:
    return QRect(widget.mapToGlobal(QPoint(0, 0)), widget.size())


def _shown_part(widget: QWidget, window: QWidget) -> QRect:
    """The part of `widget` not cut off by a viewport it is in, or by the window's own edges."""
    shown = _global(widget)
    parent = widget.parentWidget()
    while parent is not None:
        area = parent.parentWidget()
        if isinstance(area, QAbstractScrollArea) and area.viewport() is parent:
            shown = shown.intersected(_global(parent))
        parent = parent.parentWidget()
    return shown.intersected(_global(window))


def _bring_into_view(widget: QWidget) -> None:
    from yulon.ui.gamepad import _scroll_into_view

    _scroll_into_view(widget)
    QApplication.processEvents()


def _reachable(widget: QWidget, window: QWidget) -> bool:
    """`widget`'s centre is on the screen and drawn, once every scroll area is moved to it."""
    _bring_into_view(widget)
    centre = _global(widget).center()
    available = window.screen().availableGeometry()
    return available.contains(centre) and _shown_part(widget, window).contains(centre)


def _name(widget: QWidget) -> str:
    text = widget.text() if isinstance(widget, QAbstractButton) else ""
    return f"{type(widget).__name__}({widget.objectName() or text!r})"


def _buttons_shown_in(page: QWidget) -> list[QAbstractButton]:
    return [
        b
        for b in page.findChildren(QAbstractButton)
        if b.isVisible() and b.isEnabled() and b.width() > 0 and b.height() > 0
    ]


def _choose_tab(window: QMainWindow, tabs: QTabWidget, index: int) -> str | None:
    """Click the sidebar's way to tab `index`; None, or why it could not be clicked."""
    pins = window.yulon_sidebar_pins  # type: ignore[attr-defined]
    button = pins.buttons.get(index)
    if button is not None:
        if not _reachable(button, window):
            return f"the pinned button {_name(button)} is off screen"
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    else:
        bar = tabs.tabBar()
        if not _reachable(bar, window):
            return "the sidebar is off screen"
        centre = bar.tabRect(index).center()
        point = bar.mapToGlobal(centre)
        available = window.screen().availableGeometry()
        if not (available.contains(point) and _shown_part(bar, window).contains(point)):
            return f"tab {tabs.tabText(index)!r} is off screen"
        QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=centre)
    QTest.qWait(50)
    if tabs.currentIndex() != index:
        return f"clicking tab {index} left tab {tabs.currentIndex()} current"
    return None


CHECKED: list[str] = []
"""Every control `_out_of_reach` was asked about, so a walk that saw nothing is not a pass."""


def _out_of_reach(where: str, buttons: list[QAbstractButton], window: QWidget) -> list[str]:
    CHECKED.extend(f"{where}: {_name(b)}" for b in buttons)
    return [
        f"{where}: {_name(b)} at {_rect(_global(b))}" for b in buttons if not _reachable(b, window)
    ]


def measure(window: QMainWindow) -> dict[str, Any]:
    """Where the window is, and every control of every main tab a player cannot reach."""
    from yulon.ui.controller_view import ControllerView

    screen = window.screen()
    available = screen.availableGeometry()
    frame = window.frameGeometry()
    tabs = window.property("tabs")
    assert isinstance(tabs, QTabWidget)
    CHECKED.clear()
    unreachable: list[str] = []
    titles: list[str] = []
    for index in range(tabs.count()):
        title = tabs.tabText(index)
        titles.append(title)
        problem = _choose_tab(window, tabs, index)
        if problem is not None:
            unreachable.append(f"{title}: {problem}")
            continue
        page = tabs.widget(index)
        if isinstance(page, ControllerView):
            # A server tab is its own row of tabs: each is chosen as a player chooses it.
            inner = page._tabs
            for sub in range(inner.count()):
                inner.setCurrentIndex(sub)
                QTest.qWait(30)
                where = f"{title} / {inner.tabText(sub)}"
                unreachable.extend(_out_of_reach(where, _buttons_shown_in(page), window))
        else:
            unreachable.extend(_out_of_reach(title, _buttons_shown_in(page), window))
    header_button = window.findChild(QAbstractButton, CHECK_UPDATES_BUTTON)
    if header_button is not None and not _reachable(header_button, window):
        unreachable.append(f"header: {_name(header_button)}")
    return {
        "screen": screen.name(),
        "available": _rect(available),
        "frame": _rect(frame),
        "client": _rect(window.geometry()),
        "minimum": [window.minimumWidth(), window.minimumHeight()],
        "inside": available.contains(frame),
        "scrolls": isinstance(window.centralWidget(), QScrollArea),
        "content_minimum": [
            window.centralWidget().widget().minimumWidth(),
            window.centralWidget().widget().minimumHeight(),
        ]
        if isinstance(window.centralWidget(), QScrollArea)
        else None,
        "tabs": titles,
        "unreachable": unreachable,
        "checked": len(CHECKED),
        "checked_tabs": sorted({c.split(":")[0] for c in CHECKED}),
    }


def _build(scratch: Path) -> QMainWindow:
    """`main.build_window()`, with the machine shut out and one WotLK server remembered."""
    import main
    from yulon import state, update, update_state
    from yulon.ui.controller_view import ControllerView
    from yulon.ui.widgets.update_bar import UpdateBar

    server_dir = scratch / "wotlk-server"
    server_dir.mkdir()
    remembered = state.AppState(installs=[state.KnownInstall(game="wow-wotlk", server_dir=server_dir)])
    update.check_with_cache = lambda **kwargs: None  # type: ignore[assignment]
    update_state.update_state_path = lambda config_dir=None: scratch / "update.json"  # type: ignore[assignment]
    state.load_state = lambda path=None, repair=True: remembered  # type: ignore[assignment]
    state.save_state = lambda app_state, path=None: None  # type: ignore[assignment]
    main.sweep_leftover_client_copies = lambda **kwargs: None  # type: ignore[assignment]
    UpdateBar.offer_notice = lambda self, text, on_shown=None: None  # type: ignore[method-assign]
    real_init = ControllerView.__init__

    def _no_polling(self: Any, entry: Any, services: Any, **kwargs: Any) -> None:
        kwargs["status_poll_ms"] = 0
        real_init(self, entry, services, **kwargs)

    ControllerView.__init__ = _no_polling  # type: ignore[method-assign]
    window = main.build_window()
    assert isinstance(window, QMainWindow)
    return window


def run(mode: str) -> dict[str, Any]:
    import main
    from yulon.ui.theme import apply_dadcraft_theme

    app = QApplication.instance() or QApplication(sys.argv)
    apply_dadcraft_theme(app)
    result: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as tmp:
        window = _build(Path(tmp))
        try:
            window.show()
            QTest.qWait(SETTLE_MS)
            result["opened"] = measure(window)
            if mode == "move":
                # The second screen, the way a player drags a window onto it: the
                # window's top-left corner is put inside that screen.
                screens = QGuiApplication.screens()
                small = screens[1]
                window.move(small.geometry().topLeft() + QPoint(20, 20))
                QTest.qWait(SETTLE_MS)
                result["moved"] = measure(window)
                window.move(screens[0].geometry().topLeft() + QPoint(20, 20))
                QTest.qWait(SETTLE_MS)
                result["back"] = measure(window)
        finally:
            window.close()
            main._stop_background_threads(window)
            QApplication.processEvents()
    del app
    return result


if __name__ == "__main__":
    print(json.dumps(run(sys.argv[1] if len(sys.argv) > 1 else "open")))
