"""A sub-tab that scrolls instead of squeezing (T191).

`QBoxLayout`'s answer to a column whose minimums add up to more than the tab
has is to draw every child shorter than it needs -- cut text, buttons over
buttons. A `ScrollPage` holds the tab's widget (its BODY) at no less than its
minimum and scrolls the rest into reach.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtGui import QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QFrame,
    QListWidget,
    QScrollArea,
    QStyle,
    QStyleOptionViewItem,
    QWidget,
)

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
        if isinstance(layout, QBoxLayout) and layout.hasHeightForWidth():
            need = max(need, column_floor(layout, width))
        elif layout is not None:
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


def column_floor(layout: QBoxLayout, width: int) -> int:
    """The height under which a top-to-bottom `layout` at `width` starts cutting its children.

    NOT `totalMinimumHeightForWidth()`, which is the sum of the minimums and
    is what the layout does not use. Laying out a column that is height-for-
    width, `QBoxLayout::setGeometry()` takes every height-for-width child's
    PREFERRED height for that width as its minimum, and the other children's
    minimum; below that sum it shares the shortfall out -- largest first, so
    three boxes come out the same height. Measured on Tortoise's Bots tab at
    1920x1080: the minimums summed to 828 of a 900 px page, and the random-bot
    box, the two columns and the dashboard box were each drawn 290 px, the
    log strip in two of them cut from 68 to 59.
    """
    margins = layout.contentsMargins()
    inner = width - margins.left() - margins.right()
    total = margins.top() + margins.bottom()
    shown = 0
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item is None or item.isEmpty():
            continue
        shown += 1
        if item.hasHeightForWidth():
            across = max(item.minimumSize().width(), min(inner, item.maximumSize().width()))
            total += item.heightForWidth(across)
        else:
            total += item.minimumSize().height()
    return int(total + max(0, layout.spacing()) * max(0, shown - 1))


class RowsList(QListWidget):
    """A list that never asks for less than `rows` whole rows of the theme's height (T191).

    The row is asked of the STYLE (the item padding and the touch-target
    minimum the theme sets), so the floor follows the font the window's width
    gives the theme, and holds before the list has a single item. Asked on
    every layout and never stored: `QWidget` already asks its layout again on
    a font or style change, so a restyle reaches the drawn height with nothing
    here to do it.
    """

    def __init__(self, rows: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows = rows

    def row_height(self) -> int:
        """One row as the style draws it: a real row's when there is one, else a probe's."""
        if self.count():
            return int(self.sizeHintForRow(0))
        option = QStyleOptionViewItem()
        self.initViewItemOption(option)
        option.text = "Xg"
        option.features |= QStyleOptionViewItem.ViewItemFeature.HasDisplay
        size = self.style().sizeFromContents(
            QStyle.ContentsType.CT_ItemViewItem, option, QSize(), self
        )
        return int(size.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802  (Qt's own name)
        hint = super().minimumSizeHint()
        return QSize(hint.width(), self._rows * self.row_height() + 2 * self.frameWidth())


class RowsScroll(QScrollArea):
    """A scroll area in a page that never gets shorter than its content's first `rows` rows.

    A scroll area's own minimum is a sliver, so in a column with a stretch
    beside it, it is the thing the layout takes height from: My Party's box
    stacked under the bot list at 960x640 came out 68 px, under its Character
    row and over nothing (T191 B1). The rows are the content layout's own
    first items, at their preferred height, so the floor follows the theme.
    """

    def __init__(self, rows: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows = rows
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)

    def minimumSizeHint(self) -> QSize:  # noqa: N802  (Qt's own name)
        hint = super().minimumSizeHint()
        content = self.widget()
        layout = None if content is None else content.layout()
        if layout is None:
            return hint
        margins = layout.contentsMargins()
        height = margins.top()
        taken = 0
        for index in range(layout.count()):
            item = layout.itemAt(index)
            if item is None or item.isEmpty():
                continue
            if taken == self._rows:
                break
            height += item.sizeHint().height() + (max(0, layout.spacing()) if taken else 0)
            taken += 1
        return QSize(hint.width(), max(hint.height(), height + 2 * self.frameWidth()))


class _Stacker(QObject):
    """`stack_when_narrow`'s watcher: one decision per resize or layout request of the owner."""

    def __init__(self, columns: QBoxLayout, owner: QWidget) -> None:
        super().__init__(owner)
        self._columns = columns
        self._owner = owner
        owner.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() in (QEvent.Type.Resize, QEvent.Type.LayoutRequest):
            self.settle()
        return bool(super().eventFilter(watched, event))

    def wanted_width(self) -> int:
        """What the columns ask for side by side: their preferred widths, the gaps, the margins."""
        widths = []
        for index in range(self._columns.count()):
            item = self._columns.itemAt(index)
            widget = None if item is None else item.widget()
            if widget is not None and not widget.isHidden():
                widths.append(widget.sizeHint().width())
        outer = self._owner.layout()
        margins = 0
        if outer is not None:
            margins = outer.contentsMargins().left() + outer.contentsMargins().right()
        return sum(widths) + max(0, self._columns.spacing()) * max(0, len(widths) - 1) + margins

    def settle(self) -> None:
        side_by_side = self.wanted_width() <= self._owner.width()
        direction = (
            QBoxLayout.Direction.LeftToRight if side_by_side else QBoxLayout.Direction.TopToBottom
        )
        if self._columns.direction() != direction:
            self._columns.setDirection(direction)


def stack_when_narrow(columns: QBoxLayout, owner: QWidget) -> QObject:
    """Lay `columns` side by side while `owner` is as wide as they ask, stacked when not.

    Side by side under that width, each column is drawn narrower than it asks
    and the buttons in it cut their labels (T191 B1: My Party's "Show this
    character's party" drawn 164 px of the 199 it needs at 960x640). Stacked,
    each has the page's width, and the page scrolls for the height.
    """
    stacker = _Stacker(columns, owner)
    stacker.settle()
    return stacker


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
