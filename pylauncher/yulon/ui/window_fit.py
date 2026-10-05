"""Keep the main window inside the screen it is on (T388).

The window is built for 1280×800 with a 960×640 floor, and a screen can be
smaller than that: a Steam Deck's working area is 1280×752 with a panel, an
800×600 desktop has no room for the floor at all. Left alone, Windows opens the
window at the screen plus its frame and Qt refuses to size it below the floor,
so the edges and the buttons on them are off the screen.

`WindowFit` owns the window's size limits. The floor is the smaller of 960×640
and what the screen's free area leaves after the window's frame; where the
screen has less than the floor, the window's contents (wrapped in a scroll area
by `scroll_wrapped`) keep their own 960×640 minimum and scroll. The limits are
worked out again on first show and whenever the window moves to another screen
or the screen's free area changes.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRect, QTimer
from PySide6.QtGui import QScreen
from PySide6.QtWidgets import QApplication, QFrame, QMainWindow, QScrollArea, QWidget

SCROLL_OBJECT_NAME = "window-scroll"
"""The scroll area round the window's contents; the theme makes it see-through."""


def scroll_wrapped(content: QWidget, floor: tuple[int, int]) -> QScrollArea:
    """`content` inside a frameless scroll area, the content never smaller than `floor`."""
    content.setMinimumSize(*floor)
    content.setObjectName("window-scroll-content")
    area = QScrollArea()
    area.setObjectName(SCROLL_OBJECT_NAME)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setWidgetResizable(True)
    area.setWidget(content)
    return area


class WindowFit(QObject):
    """Sets a window's size, floor and place from the screen it is on."""

    def __init__(
        self, window: QMainWindow, default: tuple[int, int], floor: tuple[int, int]
    ) -> None:
        super().__init__(window)
        self._window = window
        self._default = default
        self._floor = floor
        self._screen: QScreen | None = None
        self._watched: QScreen | None = None
        self._busy = False
        window.installEventFilter(self)
        self.apply(first=True)

    # -- measuring -------------------------------------------------------

    def _margins(self) -> tuple[int, int]:
        """Width and height the frame adds to the window; 0 until the window is shown."""
        w = self._window
        frame = w.frameGeometry()
        inside = w.geometry()
        return max(0, frame.width() - inside.width()), max(0, frame.height() - inside.height())

    def _available(self) -> QRect:
        screen = self._window.screen() or QApplication.primaryScreen()
        return screen.availableGeometry()

    # -- acting ----------------------------------------------------------

    def apply(self, first: bool = False) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            self._apply(first)
        finally:
            self._busy = False

    def _apply(self, first: bool) -> None:
        w = self._window
        avail = self._available()
        margin_w, margin_h = self._margins()
        room_w = max(1, avail.width() - margin_w)
        room_h = max(1, avail.height() - margin_h)
        w.setMinimumSize(min(self._floor[0], room_w), min(self._floor[1], room_h))
        want_w, want_h = self._default if first else (w.width(), w.height())
        w.resize(min(want_w, room_w), min(want_h, room_h))
        if w.isVisible():
            self._move_inside(avail)

    def _move_inside(self, avail: QRect) -> None:
        w = self._window
        frame = w.frameGeometry()
        x = min(max(frame.x(), avail.left()), max(avail.left(), avail.right() + 1 - frame.width()))
        y = min(max(frame.y(), avail.top()), max(avail.top(), avail.bottom() + 1 - frame.height()))
        if (x, y) != (frame.x(), frame.y()):
            w.move(w.x() + x - frame.x(), w.y() + y - frame.y())

    # -- following the screen ---------------------------------------------

    def _watch(self, screen: QScreen | None) -> None:
        if screen is self._watched:
            return
        if self._watched is not None:
            try:
                self._watched.availableGeometryChanged.disconnect(self._changed)
                self._watched.geometryChanged.disconnect(self._changed)
            except (RuntimeError, TypeError):
                pass
        self._watched = screen
        if screen is not None:
            screen.availableGeometryChanged.connect(self._changed)
            screen.geometryChanged.connect(self._changed)

    def _changed(self, *_args: object) -> None:
        self.apply()

    def _check_screen(self) -> None:
        screen = self._window.screen()
        if screen is not self._screen:
            self._screen = screen
            self._watch(screen)
            self.apply()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self._window:
            kind = event.type()
            if kind == QEvent.Type.Show:
                self._screen = self._window.screen()
                self._watch(self._screen)
                self.apply()
                # The frame's size is only known once the window exists.
                QTimer.singleShot(0, self.apply)
            elif kind in (QEvent.Type.Move, QEvent.Type.ScreenChangeInternal):
                if self._window.isVisible():
                    self._check_screen()
        return False


def fit_to_screen(
    window: QMainWindow, default: tuple[int, int], floor: tuple[int, int]
) -> WindowFit:
    """Size `window` for the screen it opens on and keep it fitted."""
    return WindowFit(window, default, floor)
