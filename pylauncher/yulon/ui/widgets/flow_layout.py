"""A row of controls that wraps onto a second line rather than clipping (T83).

`QHBoxLayout` has one answer when a row of buttons is wider than the widget it
is in: it shrinks every button below its own size hint, and Qt then draws each
button's label cut off mid-word. Measured themed on the Modules tab at the 960px
`main.MINIMUM_WINDOW_SIZE`, before this existed: `Check for updates` was drawn
in 114px against the 144 it asked for and read `heck for update`, and
`Rebuild the server…` read `ebuild the server`. There is no warning, no
ellipsis and no tooltip -- the control simply lies about what it does.

So the row wraps. Every item keeps its own size hint and the ones that do not
fit go onto the next line, which costs one line of height and only at the
widths where the alternative was a cut word.

WRAPPING rather than an overflow menu, and the reason is what these particular
buttons carry. Four of the six on the Modules action bar are GREYED on an
ordinary install -- `Apply module SQL` where the game has no importer,
`Check for updates` where nothing is cloned, `Adopt as imported…` until the
database says `populated`, `Apply pending database updates…` for three of the
four games -- and each of them explains itself in a tooltip. Inside a
`QMenu` both facts move behind a press: the reader cannot see that the action is
dead until they open the menu, and on a touch screen (the Steam Deck is a
shipping target, `theme.TOUCH_TARGET_PX`) they cannot reach the tooltip that
says why at all. That is the same argument T75 made about the lock chips, and it
is decided the same way here. Wrapping also keeps every button a real widget in
the tab order, which is what `ui.gamepad`'s D-pad walk enumerates.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QLayout, QLayoutItem, QSizePolicy, QWidget

FLOW_SPACING = 6
"""Between two controls on a line, and between one line and the next.

`QHBoxLayout`'s own default on this style, spelled here because a flow layout
has no parent layout to inherit it from.
"""


class FlowLayout(QLayout):
    """Lays its items left to right, starting a new line when one runs out.

    `add_gap()` marks ONE position as the place a single line's leftover width
    goes, which is what an `addStretch(1)` in the `QHBoxLayout` this replaces was
    for: the Modules action bar reads as "the two that only READ this install"
    then "the four that change it", and that grouping is a gap between them. On a
    line that is exactly full, and on every wrapped layout, there is no leftover
    and the gap is worth nothing -- which is the right answer, because a wrapped
    bar has already lost the single-line grouping the gap was drawing.
    """

    def __init__(self, parent: QWidget | None = None, spacing: int = FLOW_SPACING) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._gap_at: int | None = None
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    # -- QLayout's own interface ----------------------------------------

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802  (Qt's own name)
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802  (Qt's own name)
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802  (Qt's own name)
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802  (Qt's own name)
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802  (Qt's own name)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802  (Qt's own name)
        return self._lay(QRect(0, 0, width, 0), place=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802  (Qt's own name)
        super().setGeometry(rect)
        self._lay(rect, place=True)

    def sizeHint(self) -> QSize:  # noqa: N802  (Qt's own name)
        """One line of everything, which is what the bar wants when it can have it."""
        margins = self.contentsMargins()
        widths = [item.sizeHint().width() for item in self._items]
        spacing = self.spacing() * max(0, len(widths) - 1)
        return QSize(
            sum(widths) + spacing + margins.left() + margins.right(),
            self._line_height() + margins.top() + margins.bottom(),
        )

    def minimumSize(self) -> QSize:  # noqa: N802  (Qt's own name)
        """The widest single item, and as tall as the lines THIS width makes of them.

        The width is the one the layout was last given, because a flow layout's
        height is not a property of its contents alone and this is the only
        number that says which question is being asked. Before the first
        `setGeometry()` there is no such width and the answer is one line, which
        is also the floor at any width.

        It reported one line at every width until T83 round 1, and that is not a
        conservative answer -- it is a false one, and it is invisible in exactly
        the place it does damage. `heightForWidth()` is a PREFERENCE a parent
        `QBoxLayout` grants out of surplus; `minimumSize()` is what it protects
        when there is none, and it is what reaches the TAB's own
        `minimumSizeHint()`. Measured at 1000x700 with the Modules log open
        after a job: the tab's layout claimed it needed 482px when the real need
        with a wrapped bar was 538, so nothing above could know the tab was
        over-subscribed -- and `_IdleLogPanel` was drawn 153px against the 180 it
        needs, with its text pane clipped, which is the state T80 exists to
        prevent. A minimum that under-reports does not save the height for
        somebody else; it spends it and says nothing.
        """
        margins = self.contentsMargins()
        width = max((item.minimumSize().width() for item in self._items), default=0)
        one_line = self._line_height() + margins.top() + margins.bottom()
        laid_out = self.geometry().width()
        tall = one_line if laid_out <= 0 else max(one_line, self.heightForWidth(laid_out))
        return QSize(width + margins.left() + margins.right(), tall)

    # -- ours -----------------------------------------------------------

    def add_gap(self) -> None:
        """Mark this position as where a single line's leftover width goes."""
        self._gap_at = len(self._items)

    def _line_height(self) -> int:
        return max((item.sizeHint().height() for item in self._items), default=0)

    def _lines(self, width: int) -> list[list[int]]:
        """The item indexes, split into the lines `width` pixels make of them."""
        lines: list[list[int]] = []
        line: list[int] = []
        used = 0
        for index, item in enumerate(self._items):
            need = item.sizeHint().width()
            step = need if not line else need + self.spacing()
            if line and used + step > width:
                lines.append(line)
                line, used = [index], need
                continue
            used += step
            line.append(index)
        if line:
            lines.append(line)
        return lines

    def _lay(self, rect: QRect, place: bool) -> int:
        """Place the items inside `rect` (or just answer how tall they would be).

        One function for both so the height a parent is told and the geometry the
        children are given cannot disagree -- the shape that produces a bar drawn
        one line tall with its second line underneath the widget below it.
        """
        margins = self.contentsMargins()
        inner = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        lines = self._lines(inner.width())
        height = self._line_height()
        for number, line in enumerate(lines):
            # The leftover only exists on a bar that fits on ONE line; a wrapped
            # bar has spent all of it. See `add_gap()`.
            leftover = 0
            if len(lines) == 1 and self._gap_at is not None:
                used = sum(self._items[i].sizeHint().width() for i in line)
                leftover = max(0, inner.width() - used - self.spacing() * max(0, len(line) - 1))
            x = inner.x()
            y = inner.y() + number * (height + self.spacing())
            for index in line:
                if index == self._gap_at:
                    x += leftover
                item = self._items[index]
                need = item.sizeHint().width()
                if place:
                    item.setGeometry(QRect(QPoint(x, y), QSize(need, height)))
                x += need + self.spacing()
        return int(
            len(lines) * height
            + self.spacing() * max(0, len(lines) - 1)
            + margins.top()
            + margins.bottom()
        )


class FlowBar(QWidget):
    """A widget holding a `FlowLayout`, that re-states its height when it wraps.

    `resizeEvent` is the half that is easy to leave out and easy to MEASURE as
    dead. A parent `QBoxLayout` works out its children's minimums when it is
    invalidated and re-uses them until something says they have moved -- and a
    plain resize says nothing. `FlowLayout.minimumSize()` answers for the width
    the layout was last GIVEN, so without this the bar reports its new minimum
    and the tab it is in goes on being laid out against the old one: measured on
    the Modules tab, resized 1400 -> 960 with no restyle, the bar said 106 while
    the tab's `minimumSizeHint()` stayed at the 376px it had with the bar on one
    line, instead of 432.

    It reads as dead code through any test that goes through
    `apply_dadcraft_theme()`, because a restyle invalidates every layout on the
    window and the parent re-asks anyway -- which is what most drags do
    (`main._Window._restyle_for_width`) and what every themed test here does. It
    was deleted once on exactly that evidence. The test that holds it down
    resizes without a restyle
    (`test_a_wrap_reaches_the_tabs_own_minimum_without_a_restyle`).

    Guarded on the answer CHANGING and not on the resize itself:
    `updateGeometry()` asks for a layout pass, a layout pass resizes this
    widget, and an unguarded call is that loop with nothing to stop it.

    One thing that looks as though it belongs here is deliberately absent, and
    it was written first: `QSizePolicy.setHeightForWidth(True)`. Every account
    of flow layouts says a parent asks for `heightForWidth()` only when the
    child's policy declares one, and that is the rule for a widget with NO
    layout -- `QWidget::hasHeightForWidth()` answers its layout's when it has
    one. Mutating the whole `setSizePolicy()` call away leaves every test here
    green. The vertical `Minimum` is kept on purpose: this bar's hint is a floor
    and not a target, so a column with height to spare spends it on what is
    under the bar, which on the Modules tab is the module list (T73).
    """

    def __init__(self, parent: QWidget | None = None, spacing: int = FLOW_SPACING) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self._flow = FlowLayout(self, spacing)
        self._needed = 0

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802  (Qt's own name)
        super().resizeEvent(event)
        needed = self._flow.heightForWidth(self.width())
        if needed != self._needed:
            self._needed = needed
            self.updateGeometry()

    def flow(self) -> FlowLayout:
        """This bar's layout, typed -- `layout()` answers a bare `QLayout`."""
        return self._flow


def flow_bar(parent: QWidget | None = None, spacing: int = FLOW_SPACING) -> FlowBar:
    """A `FlowBar`, named as the thing a caller builds rather than as a class."""
    return FlowBar(parent, spacing)
