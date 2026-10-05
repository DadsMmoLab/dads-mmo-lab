"""The app's question box: buttons never narrower than their labels (T157), and never off
the screen however long the question grows (T243).

**Why a plain `QMessageBox` clips a long label under this app's theme.** The
theme's `QPushButton { min-width: 64px }` is meant as a floor under a button's
size, and in the size hint it is one. But Qt's style-sheet style also turns it
into an EXPLICIT `setMinimumWidth()` on every button it polishes -- 98px, once
the padding and border are added -- and a layout takes an explicit minimum over
the button's own `minimumSizeHint()`. Most layouts never notice, because they
hand a button its size hint whenever there is room. `QMessageBox` does not: it
fixes its own width at its layout's MINIMUM (no narrower than 500px on a wide
screen), so every button in it can be pressed down to 98px. "Update without a
backup" is 198px wide at its hint and was drawn 131px wide (measured offscreen,
2026-09-27, in the 400px box an 800px screen gets); on a 1920 desktop the same
day it read "Update without a backu".

**Why here and not in the theme.** Dropping `min-width` from the sheet would
also fix it, for every button in the app at once -- and that is the trouble.
The short buttons would lose their floor, and, by the same layout rule
read the other way, every row of buttons in the main window would stop being
able to shrink below its full labels: a new floor under a window that has to fit
a 960×640 handheld (Qt's rule, not measured on the window). The squeeze is only
wrong where a box sizes itself to its minimum, and that is this class.

**Why on every show AND every layout request, not once.** `QMessageBox` sizes
itself in its `showEvent()`, so the pin goes in just before the show reaches it
and the first frame is already right; pinned any later, the labels are drawn
clipped and then jump. And the style sheet sets the 98px again each time it
re-polishes a button: `main.py` re-applies the theme to the window on every
resize, dialog open or not. The re-polish is followed by a posted
`LayoutRequest`, the other event `QMessageBox` recomputes its width on, so the
pin goes in before that one too. Setting a minimum to the value it already has
is a no-op in Qt, so this does not feed itself.

**T243: the question scrolls; the buttons stay.** `QMessageBox` fixes its HEIGHT
the way it fixes its width -- to everything its label needs -- and has no upper
bound: once T223, T224 and T217 had each added a paragraph a player needs, the
Rebuild question was taller than a 1920×1080 screen and its Yes and No were
below the bottom of it (yulon-win11, 2026-10-05). So the question is not shown
in Qt's label. Qt's label keeps the text (`text()` is still the question) but is
hidden, and the words are shown paragraph by paragraph in a scroll area in its
place. The scroll area is given a minimum size that `QMessageBox`'s own sizing
then fixes the box to: as tall as the words need, and no taller than the screen
the box opens on leaves room for, after the buttons and a window frame. The
buttons are outside the scroll area, so they are always on screen.

**The pad reads by stopping.** The D-pad moves the focus (`gamepad.Navigator`);
it does not scroll. So when the words do not all show, each paragraph becomes a
place the pad (and Tab) can stop -- the navigator scrolls a stop into view as it
lands (T175), and the theme rings it (`QUESTION_PARAGRAPH`). When everything
shows, the paragraphs are not stops, and the pad goes from button to button as
it always did.

Build every question through this class rather than `QMessageBox(...)` --
`tests/test_message_box_buttons_fit.py` refuses a new bare construction -- and
every Yes/No question through `ask_yes_no()`, whose box this is.
"""

from __future__ import annotations

from typing import cast

from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtGui import Qt as GuiQt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from yulon.ui.answers import said_yes
from yulon.ui.theme import QUESTION_PARAGRAPH

_REFIT_ON = (QEvent.Type.Show, QEvent.Type.LayoutRequest)
"""The two events `QMessageBox` recomputes its size on (`showEvent`, `event`)."""

QUESTION_SCROLL = "question-scroll"
"""The object name of the scroll area that holds the question's paragraphs."""

TEXT_COLUMN_CHARS = 72
"""How wide the question's column is, in average characters of its font: about a book's line."""

FRAME_ALLOWANCE = 48
"""Pixels kept free for the window frame the system draws round the box.

The box is sized before it is decorated, and a title bar is about 32px on
Windows at 100% and none under gamescope; this is that, with a margin, so the
frame is on screen too."""

MIN_TEXT_LINES = 3
"""The fewest lines of the question that show, however small the screen."""

_QT_MESSAGE_LABEL = "qt_msgbox_label"
"""The object name Qt gives `QMessageBox`'s own text label (qmessagebox.cpp)."""


class FittedMessageBox(QMessageBox):
    """`QMessageBox`, with each button as wide as its label and the box inside the screen."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[call-overload]
        self._scroll: QScrollArea | None = None
        self._paragraphs: list[QLabel] = []
        # Now, not at the first show: a widget added to a box that is already
        # being shown is shown a turn of the event loop later, and `QMessageBox`
        # sizes itself without a widget that is not shown yet.
        # The text is the constructor's: every caller passes it there, and nothing
        # here follows a later `setText()`.
        self._question_scroll()

    def event(self, event: QEvent) -> bool:
        kind = event.type()
        if kind in _REFIT_ON:
            fit_buttons_to_labels(self)
            self._fit_question_to_screen()
        handled = super().event(event)
        if kind in _REFIT_ON and self.isVisible():
            # Centred as it opens; afterwards only kept on screen, so a box the
            # player moved stays where they put it.
            self._keep_on_screen(centre=kind == QEvent.Type.Show)
        return bool(handled)

    # -- the question, in a scroll area --------------------------------------

    def _fit_question_to_screen(self) -> None:
        """Size the question's scroll area so `QMessageBox` fixes the box inside the screen."""
        scroll = self._question_scroll()
        layout = self.layout()
        body = scroll.widget() if scroll is not None else None
        lines = body.layout() if body is not None else None
        if scroll is None or layout is None or lines is None:
            return
        room = self._room()
        metrics = self._paragraphs[0].fontMetrics()
        bar = scroll.verticalScrollBar().sizeHint().width()
        scroll.setMinimumSize(0, 0)
        layout.activate()
        chrome = layout.totalMinimumSize() - scroll.minimumSizeHint()
        column = metrics.averageCharWidth() * TEXT_COLUMN_CHARS
        column = max(1, min(column, _qt_hard_width_limit(room) - chrome.width() - bar))
        needed = lines.totalHeightForWidth(column)
        floor = metrics.lineSpacing() * MIN_TEXT_LINES
        shown = min(needed, max(floor, room.height() - FRAME_ALLOWANCE - chrome.height()))
        scroll.setMinimumSize(column + bar, shown)
        # The pad cannot scroll, so the paragraphs are stops only when not all of them show.
        overflows = needed > shown
        for paragraph in self._paragraphs:
            policy = paragraph.focusPolicy()
            if overflows:
                policy = Qt.FocusPolicy(policy | Qt.FocusPolicy.TabFocus)
            else:
                policy = Qt.FocusPolicy(policy & ~Qt.FocusPolicy.TabFocus)
            paragraph.setFocusPolicy(policy)

    def _question_scroll(self) -> QScrollArea | None:
        """The scroll area holding the question, put in place of Qt's label the first time."""
        if self._scroll is not None:
            return self._scroll
        label = self.findChild(QLabel, _QT_MESSAGE_LABEL)
        grid = self.layout()
        if label is None or not isinstance(grid, QGridLayout):
            return None
        index = grid.indexOf(label)
        if index < 0:
            return None
        row, column, rows, columns = cast("tuple[int, int, int, int]", grid.getItemPosition(index))
        text = self.text()
        rich = self.textFormat() == Qt.TextFormat.RichText or (
            self.textFormat() == Qt.TextFormat.AutoText and GuiQt.mightBeRichText(text)
        )
        parts = [text] if rich else [p.strip("\n") for p in text.split("\n\n") if p.strip()]
        if not parts:
            return None
        body = QWidget()
        lines = QVBoxLayout(body)
        lines.setContentsMargins(0, 0, 0, 0)
        lines.setSpacing(label.fontMetrics().lineSpacing())
        flags = label.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
        for part in parts:
            paragraph = QLabel(part)
            paragraph.setObjectName(QUESTION_PARAGRAPH)
            paragraph.setTextFormat(Qt.TextFormat.RichText if rich else Qt.TextFormat.PlainText)
            paragraph.setWordWrap(True)
            paragraph.setTextInteractionFlags(flags)
            paragraph.setOpenExternalLinks(label.openExternalLinks())
            lines.addWidget(paragraph)
            self._paragraphs.append(paragraph)
        lines.addStretch(1)
        scroll = QScrollArea()
        scroll.setObjectName(QUESTION_SCROLL)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        scroll.setWidget(body)
        label.hide()
        grid.addWidget(scroll, row, column, rows, columns)
        self._scroll = scroll
        return scroll

    # -- where the box opens -------------------------------------------------

    def _room(self) -> QRect:
        """The free area of the screen the box opens on."""
        return self.screen().availableGeometry()

    def _keep_on_screen(self, *, centre: bool) -> None:
        """Move the box wholly onto the screen; with `centre`, over its window first.

        `QDialog` places a box BEFORE `QMessageBox` sizes it in its `showEvent()`,
        so the place was worked out for a box of another size: a 752px box was
        drawn from y=336 on an 800px screen, its buttons below the bottom.
        """
        room = self._room()
        frame = self.frameGeometry()
        left, top = frame.left(), frame.top()
        if centre:
            parent = self.parentWidget()
            middle = parent.window().frameGeometry().center() if parent else room.center()
            left, top = middle.x() - frame.width() // 2, middle.y() - frame.height() // 2
        left = min(left, room.right() + 1 - frame.width())
        top = min(top, room.bottom() + 1 - frame.height())
        place = QPoint(max(left, room.left()), max(top, room.top()))
        if place != frame.topLeft():
            self.move(place)


def _qt_hard_width_limit(room: QRect) -> int:
    """The widest `QMessageBox` makes itself on a screen with this free area (qmessagebox.cpp).

    A box whose layout asks for more is cut to this, so the question's column is
    kept inside it.
    """
    if room.width() <= 1024:
        return room.width()
    return min(room.width() - 480, 1000)


def fit_buttons_to_labels(box: QMessageBox) -> None:
    """Set every button's minimum width to its size hint, which is its label's width.

    The size hint already carries the theme's 64px floor, so a short label
    keeps its old width and only a long one grows.
    """
    for button in box.findChildren(QPushButton):
        button.ensurePolished()
        button.setMinimumWidth(button.sizeHint().width())


def ask_yes_no(parent: QWidget | None, title: str, text: str) -> bool:
    """Ask a Yes/No question in a box that fits the screen; True only for an explicit Yes.

    No is the default, so Enter declines, and the escape button, so Escape and
    the window's close button decline too; `said_yes()` reads the answer in
    either of the shapes PySide6 returns it in (T33). Every press that asked
    through the static `QMessageBox.question()` asks through this instead
    (T243): that box grows with its text and has no bound.
    """
    box = FittedMessageBox(
        QMessageBox.Icon.Question,
        title,
        text,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        parent,
    )
    box.setDefaultButton(QMessageBox.StandardButton.No)
    box.setEscapeButton(QMessageBox.StandardButton.No)
    try:
        return said_yes(box.exec())
    finally:
        box.deleteLater()
