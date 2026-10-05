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

from PySide6.QtCore import QEvent, QObject, QRect, Qt, QTimer
from PySide6.QtGui import QFocusEvent, QImage, QKeyEvent, QPixmap, QShowEvent
from PySide6.QtGui import Qt as GuiQt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from yulon.log import get_logger
from yulon.ui.answers import said_yes
from yulon.ui.theme import QUESTION_PARAGRAPH

logger = get_logger(__name__)

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
        self._paragraphs: list[QLabel] = []
        self._scroll: QScrollArea | None = None
        self._scrolled_text: str | None = None
        # Where the focus was before it last arrived somewhere in this box: a
        # paragraph, a button, or neither. Read when a pad move crosses between
        # the question and the buttons (`_focus_arrived`).
        self._focus_was: str | None = None
        self._buttons_watched: set[int] = set()
        # Now, not at the first show: a widget added to a box that is already
        # being shown is shown a turn of the event loop later, and `QMessageBox`
        # sizes itself without a widget that is not shown yet.
        self._place_question()

    def event(self, event: QEvent) -> bool:
        if event.type() in _REFIT_ON:
            fit_buttons_to_labels(self)
            self._fit_question_to_screen()
        return bool(super().event(event))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt's name
        """Enter on a paragraph does nothing: it is a place to read, not a press.

        `QDialog` hands Enter to the default button wherever the focus is, and the
        install-folder question's default is Yes (cold review).
        """
        if (
            event.type() == QEvent.Type.KeyPress
            and watched in self._paragraphs
            and isinstance(event, QKeyEvent)
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        ):
            return True
        if event.type() == QEvent.Type.FocusIn and isinstance(event, QFocusEvent):
            self._focus_arrived(watched, event.reason())
        return bool(super().eventFilter(watched, event))

    def _focus_arrived(self, widget: QObject, reason: Qt.FocusReason) -> None:
        """Keep a focused paragraph in view, and land a pad move between the parts sensibly.

        Live test, yulon-win11 2026-10-05: Tab from No wrapped to a first paragraph
        scrolled out of view; Up from the buttons skipped the paragraphs scrolled
        below the view, because the pad only aims at what shows (T175); and Down
        from the last paragraph landed on Yes. So a paragraph that takes the focus
        is scrolled into view; the pad (`OtherFocusReason`) coming up from the
        buttons lands on the LAST paragraph, the one nearest them; and coming down
        from the question it lands on the default button. Tab and the mouse go
        where they go. The pad's own move finishes first (it scrolls its target
        into view after `setFocus()`), so the landing is put right a turn later.
        """
        was, self._focus_was = self._focus_was, None
        pad = reason == Qt.FocusReason.OtherFocusReason
        if widget in self._paragraphs:
            self._focus_was = "paragraph"
            last = self._paragraphs[-1]
            if pad and was == "button" and widget is not last:
                QTimer.singleShot(0, self, lambda: self._land_on(last))
            else:
                self._show_paragraph(widget)
        elif widget in self.buttons():
            self._focus_was = "button"
            default = self.defaultButton()
            if pad and was == "paragraph" and default is not None and widget is not default:
                QTimer.singleShot(0, self, lambda: self._land_on(default))

    def _land_on(self, widget: QWidget) -> None:
        widget.setFocus(Qt.FocusReason.OtherFocusReason)
        if widget in self._paragraphs:
            self._show_paragraph(widget)

    def _show_paragraph(self, paragraph: QObject) -> None:
        if self._scroll is not None and isinstance(paragraph, QWidget):
            self._scroll.ensureWidgetVisible(paragraph, 0, 0)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt's name
        """Watch the buttons' focus too: they exist, relabelled and all, by the time it shows."""
        for button in self.buttons():
            if id(button) not in self._buttons_watched:
                self._buttons_watched.add(id(button))
                button.installEventFilter(self)
        if self._focus_was is None and self.focusWidget() in self.buttons():
            self._focus_was = "button"
        super().showEvent(event)

    # Qt lays the box out again (`setupLayout()`) on each of these, from a fresh
    # grid that holds its own label and not the scroll area: the question would
    # vanish, Qt's label staying hidden (cold review). So the question is put
    # back in its place after each, and made again after a new text.

    def setText(self, text: str) -> None:  # noqa: N802 - Qt's name
        super().setText(text)
        self._place_question()

    def setIcon(self, icon: QMessageBox.Icon) -> None:  # noqa: N802 - Qt's name
        super().setIcon(icon)
        self._place_question()

    def setIconPixmap(self, pixmap: QPixmap | QImage) -> None:  # noqa: N802 - Qt's name
        super().setIconPixmap(pixmap)
        self._place_question()

    def setInformativeText(self, text: str) -> None:  # noqa: N802 - Qt's name
        super().setInformativeText(text)
        self._place_question()

    def setDetailedText(self, text: str) -> None:  # noqa: N802 - Qt's name
        super().setDetailedText(text)
        self._place_question()

    def setCheckBox(self, cb: QCheckBox) -> None:  # noqa: N802 - Qt's name
        super().setCheckBox(cb)
        self._place_question()

    # -- the question, in a scroll area --------------------------------------

    def _fit_question_to_screen(self) -> None:
        """Size the question's scroll area so `QMessageBox` fixes the box inside the screen."""
        scroll = self._scroll
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

    def _place_question(self) -> None:
        """Show the question in a scroll area in Qt's label's cell, hiding Qt's label.

        Made again when the text is not the one it shows; put back in the grid
        when Qt has laid the box out without it.
        """
        label = self.findChild(QLabel, _QT_MESSAGE_LABEL)
        grid = self.layout()
        if label is None or not isinstance(grid, QGridLayout) or grid.indexOf(label) < 0:
            # Qt's own box, unbounded: say so in the log, so a Qt that lays its box
            # out differently is found from yulon.log rather than from a player whose
            # buttons are below the screen. CI runs the newest PySide6, which is the
            # one a release ships, and `tests/test_dialogs_fit_the_screen.py` fails there.
            logger.warning(
                f"T243: this Qt's QMessageBox has no {_QT_MESSAGE_LABEL} in a grid layout; "
                f"the question {self.windowTitle()!r} is shown unscrolled"
            )
            return
        if self._scroll is not None and self._scrolled_text != self.text():
            grid.removeWidget(self._scroll)
            self._scroll.hide()
            self._scroll.setParent(None)  # out of `findChildren()` at once, not at deletion
            self._scroll.deleteLater()
            self._scroll, self._paragraphs = None, []
        if self._scroll is None:
            self._scroll = self._question_scroll(label)
            self._scrolled_text = self.text()
            if self._scroll is None:
                label.show()
                return
        if grid.indexOf(self._scroll) < 0:
            index = grid.indexOf(label)
            row, column, rows, columns = cast(
                "tuple[int, int, int, int]", grid.getItemPosition(index)
            )
            grid.addWidget(self._scroll, row, column, rows, columns)
        label.hide()

    def _question_scroll(self, label: QLabel) -> QScrollArea | None:
        """A scroll area holding the question, a label per paragraph; None for no text."""
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
            paragraph.installEventFilter(self)
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
        return scroll

    def _room(self) -> QRect:
        """The free area of the screen the box opens on."""
        return self.screen().availableGeometry()


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
