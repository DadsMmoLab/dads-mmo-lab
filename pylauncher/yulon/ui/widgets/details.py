"""One "Details" fold: the plain sentence above it, the exact text inside it (T194).

A result a player reads first in words -- "Added to your Steam library",
"Apply opens ports 3724 and 8085" -- and the paths, commands and SQL behind it
for whoever wants to check them by hand. Folded by default, read-only and
selectable when open, and hidden altogether while it has nothing to hold.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPlainTextEdit, QVBoxLayout, QWidget

from yulon import ansi
from yulon.ui.widgets.log_panel import CollapseHandle

_LINES_SHOWN = 8
"""How many lines tall the open fold is before it scrolls."""


class Details(QWidget):
    """A `CollapseHandle` titled "Details" over a read-only, selectable text box."""

    def __init__(self, parent: QWidget | None = None, *, title: str = "Details") -> None:
        super().__init__(parent)
        self.setObjectName("details")
        self.handle = CollapseHandle(title, self)
        self.text_box = QPlainTextEdit(self)
        self.text_box.setObjectName("details-text")
        self.text_box.setReadOnly(True)
        self.text_box.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.text_box.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        lines = self.text_box.fontMetrics().lineSpacing() * _LINES_SHOWN
        self.text_box.setMaximumHeight(lines + 2 * self.text_box.frameWidth() + 8)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.handle)
        layout.addWidget(self.text_box)
        self.handle.toggled.connect(self._fold)
        self.handle.set_collapsed(True)
        self.setVisible(False)

    @property
    def collapsed(self) -> bool:
        """True while the text is folded away under the handle."""
        return self.handle.collapsed

    def set_text(self, text: str) -> None:
        """Hold `text`, folded; an empty text hides the whole fold.

        Terminal colour codes are taken out (`yulon.ansi`): a program's own words
        are what this fold holds, and a text box draws none of them (T214).
        """
        plain = ansi.strip(text)
        self.text_box.setPlainText(plain)
        self.handle.set_collapsed(True)
        self.setVisible(bool(plain))

    def text(self) -> str:
        """What the fold holds, open or not."""
        return self.text_box.toPlainText()

    def _fold(self, collapsed: bool) -> None:
        self.text_box.setVisible(not collapsed)
        self.updateGeometry()
