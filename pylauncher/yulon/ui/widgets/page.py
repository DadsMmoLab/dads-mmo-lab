"""A sub-tab that scrolls instead of squeezing (T191).

`QBoxLayout`'s answer to a column whose minimums add up to more than the tab
has is to draw every child shorter than it needs -- cut text, buttons over
buttons. A `ScrollPage` holds the tab's widget (its BODY) at no less than its
minimum and scrolls the rest into reach.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtGui import QResizeEvent, QShowEvent
from PySide6.QtWidgets import QApplication, QFrame, QScrollArea, QWidget

PAGE_NAME = "sub-tab-page"
"""The object name the page's own style sheet selects it by (an ID selector only)."""


class ScrollPage(QScrollArea):
    """A frameless scroll area around one sub-tab's body.

    **The body is sized here, not by `QScrollArea`'s resizable mode**, and the
    difference is the whole reason this is a subclass. Resizable, Qt grows a
    body whose layout is height-for-width -- any word-wrapped label makes it so
    -- to its PREFERRED height, so a tab whose minimum fits would still scroll,
    with every list in it drawn at its preferred height and the Modules tab's
    fold ladder (`_TabFit`, T83/T153) shrinking a list nobody sees shrink.
    Measured on a 200 px page holding a wrapped label and a list: 46 px of
    scroll for a body whose minimum was 92. Here the body gets the whole
    viewport when its minimum fits, and its minimum when it does not.

    Not a pad stop of its own (the launcher's `_scroll` precedent): Down from
    the sub-tab bar goes to the first control on the page.
    """

    def __init__(self, body: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(PAGE_NAME)
        self.setWidgetResizable(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # The tab's own background shows through, as it did before the page:
        # the area, its viewport and the body, each named, so nothing else
        # under the page is restyled (T188 C1: a selector-less sheet overrides
        # the window's).
        self.setStyleSheet(
            f"QScrollArea#{PAGE_NAME}, "
            f"QScrollArea#{PAGE_NAME} > QWidget#qt_scrollarea_viewport, "
            f"QScrollArea#{PAGE_NAME} > QWidget#qt_scrollarea_viewport > QWidget "
            "{ background: transparent; border: none; }"
        )
        self.setWidget(body)
        body.installEventFilter(self)
        self._fit()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """A minimum under the body moved: size the body again."""
        if watched is self.widget() and event.type() == QEvent.Type.LayoutRequest:
            self._fit()
        return bool(super().eventFilter(watched, event))

    def resizeEvent(self, event: QResizeEvent) -> None:
        """The room moved: size the body again, and tell it even when its size did not.

        A body taller than the page keeps its size when the page grows or
        shrinks under it, so Qt sends it no resize -- and the code that decides
        from the room on screen (`page_room`) listens for the body's resize.
        """
        super().resizeEvent(event)
        body = self.widget()
        if body is None:
            return
        before = body.size()
        self._fit()
        if body.size() == before:
            QApplication.sendEvent(body, QResizeEvent(before, before))

    def sizeHint(self) -> QSize:  # noqa: N802  (Qt's own name)
        """What the body prefers, uncut: the size the tab asked for before it had a page.

        `QScrollArea`'s own hint is the body's current size here (or its hint
        cut to 36x24 lines when resizable), and either moves the size the
        window is first laid out at. The Tuning tab's two columns keep the
        split they were first given, so that moved them: measured, a page
        around the Modules tab took Tuning's cards column at 1280x800 from
        536px to 136.
        """
        body = self.widget()
        return super().sizeHint() if body is None else body.sizeHint()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._fit()

    def _need(self, body: QWidget, width: int) -> int:
        """The least height `body` can be drawn at `width` without cutting anything."""
        need = max(body.minimumSizeHint().height(), body.minimumHeight())
        layout = body.layout()
        if layout is not None:
            if layout.hasHeightForWidth():
                need = max(need, layout.totalMinimumHeightForWidth(width))
            else:
                need = max(need, layout.totalMinimumSize().height())
        return int(need)

    def _fit(self) -> None:
        """The whole viewport when the body's minimum fits it; the minimum when not.

        Taken against the viewport the area would have with no scroll bars,
        then again with the vertical one, which is narrower and so may need
        more height for the same wrapped words.
        """
        body = self.widget()
        if body is None:
            return
        room = self.maximumViewportSize()
        across = self.verticalScrollBar().sizeHint().width()
        down = self.horizontalScrollBar().sizeHint().height()
        least = max(body.minimumSizeHint().width(), body.minimumWidth())
        width = max(room.width(), least)
        height = room.height() - (down if width > room.width() else 0)
        need = self._need(body, width)
        if need > height:
            width = max(room.width() - across, least)
            height = room.height() - (down if width > room.width() - across else 0)
            need = self._need(body, width)
        size = QSize(width, max(height, need))
        if body.size() != size:
            body.resize(size)


def page_of(widget: QWidget) -> ScrollPage | None:
    """The `ScrollPage` whose body `widget` is, or None."""
    viewport = widget.parentWidget()
    page = None if viewport is None else viewport.parentWidget()
    if isinstance(page, ScrollPage) and page.widget() is widget:
        return page
    return None


def page_room(widget: QWidget) -> int:
    """The height `widget` has on screen: its page's viewport, or its own height.

    What every height-aware decision on a sub-tab divides (the Modules tab's
    fold, a log's share): inside a page the body is as tall as it needs, which
    is the scrolled length and not the room.
    """
    page = page_of(widget)
    if page is None:
        return int(widget.height())
    return int(page.viewport().height())
