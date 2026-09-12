"""The Tuning tab's surface: every module setting, with the file behind it (T43).

T42's split, kept: the first half of this module is pure, so what the tab
DECIDES — which control a key earns, what a card costs to apply, what counts as
changed — is decided without a `QApplication` and is testable as data. The
widgets below draw what they are handed and press buttons; they decide nothing,
and they read and write nothing. `yulon.tuning` owns the disk.

**The safety rule runs through every choice here.** `control_kind()` gives a key
with no `type` a TEXT BOX, never a switch and never a spinner, and gives an
`int` with anything less than both bounds a text box too — a spinner cannot
exist without a range, and a range invented here would refuse values the module
is perfectly happy with. `starting_value()` never fabricates: a key the file
does not carry shows its default with a note SAYING it is not in the file, so
nobody reads a placeholder as a setting.

Nothing here imports `yulon.ui.controller_view` or any decorations module;
colours come from the `COLOR_*` constants `theme.py` exports.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QResizeEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from yulon import tuning
from yulon.tuning import ApplyRule, TuningRow
from yulon.ui.theme import (
    COLOR_BG_PANEL,
    COLOR_GOLD_BORDER,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_WARNING,
    COLOR_UNCOMMON,
)

ControlKind = Literal["switch", "spinner", "box", "none"]

NOTHING = "nothing"
"""What a row that had no value at all says it changed FROM.

An empty string in that sentence reads as a value the file held and lost.
"""

CHANGED_FROM = "changed from {old}"

NOT_IN_THE_FILE = "not in the file — this is the catalog's default, not a setting"
"""The note under a key the deployed conf does not carry.

73 of the 107 shipped keys have no `default` and most have no line in the file
either: `apply.py` declines to write a key with no default, so the conf on disk
never mentions it. Showing the default with no note would read as a setting
that is already there, and pressing Save would look like a no-op when it is
the first time the key has ever been written.
"""

NOTHING_TO_TUNE = (
    "Nothing to tune yet. Settings appear here for the modules this install has, so "
    "install one on the Modules tab first."
)

NARROW_WIDTH = 900
"""Below this, the cards and the file editor stack instead of sitting side by side.

A number and not a stylesheet query, because a `QSplitter` cannot be told to
wrap: it is one orientation or the other, and the resize is where the app finds
out which.
"""

BOOL_WORDS: dict[str, tuple[str, str]] = {
    "true": ("true", "false"),
    "false": ("true", "false"),
    "1": ("1", "0"),
    "0": ("1", "0"),
}
"""How a switch writes itself back, keyed on how the file already spells it.

The FILE's spelling and not this tab's: a conf written entirely in `1`s must not
gain a `true` because a switch was flipped in a GUI, and a Lua script written in
`true`/`false` must not gain a `1`. A key whose file says nothing at all falls
back to `1`/`0`, which is what every `.conf` in this catalog uses.
"""

DEFAULT_BOOL: tuple[str, str] = ("1", "0")


@dataclass(frozen=True)
class TuningCard:
    """One module's settings, and what changing any of them costs.

    A card and not a file, because Save is per module: a module with two conf
    files (NPC Beastmaster has its own and one key in the core's
    `worldserver.conf`) is one thing the user installed and one thing they
    press Save on. The view groups the edits by file on the way down.
    """

    module_id: str
    module_name: str
    family: str
    rows: tuple[TuningRow, ...]
    files: tuple[str, ...]
    rule: ApplyRule

    @property
    def rule_sentence(self) -> str:
        return tuning.apply_sentence(self.rule)

    @property
    def editable(self) -> bool:
        """Whether anything on this card can be written at all."""
        return any(row.editable for row in self.rows)


def build_tuning_cards(rows: Sequence[TuningRow]) -> tuple[TuningCard, ...]:
    """Group settings into one card per module, keeping the order they arrived in.

    The order is `tuning.rows_for()`'s and is not re-sorted here: it is already
    family by family, catalog order inside a family, and each manifest's own
    file and key order. A second sort in the view would be a second place for
    the catalog's own ordering to be overruled.
    """
    order: list[str] = []
    grouped: dict[str, list[TuningRow]] = {}
    for row in rows:
        if row.module_id not in grouped:
            order.append(row.module_id)
            grouped[row.module_id] = []
        grouped[row.module_id].append(row)
    cards: list[TuningCard] = []
    for module_id in order:
        mine = grouped[module_id]
        files: list[str] = []
        for row in mine:
            if row.file not in files:
                files.append(row.file)
        cards.append(
            TuningCard(
                module_id=module_id,
                module_name=mine[0].module_name,
                family=mine[0].family,
                rows=tuple(mine),
                files=tuple(files),
                rule=tuning.worst(tuning.apply_rule(row) for row in mine),
            )
        )
    return tuple(cards)


def control_kind(row: TuningRow) -> ControlKind:
    """Which control this key earns — T43's safety rule, in one function.

    A row this app cannot write gets no control at all. A `bool` gets a switch.
    An `int` gets a spinner only when the catalog stated BOTH bounds, because a
    spinner is a range and a range this app made up would refuse a value the
    module accepts. Everything else — an `int` with one bound or none, a `list`,
    a `text`, and above all a key with NO type — gets a text box, which is the
    one control that can express anything.
    """
    if not row.editable:
        return "none"
    if row.type == "bool":
        return "switch"
    if row.type == "int" and row.min is not None and row.max is not None:
        return "spinner"
    return "box"


def starting_value(row: TuningRow) -> str:
    """What the control opens at: the file's value, else the catalog's default, else empty.

    Never an invention. `current` is `None` only when the file did not say —
    `tuning.rows_for()` refuses to guess one — and the fallback to `default` is
    labelled as such by `value_note()` rather than passed off as a reading.
    """
    if row.current is not None:
        return row.current
    return row.default or ""


def value_note(row: TuningRow) -> str | None:
    """`NOT_IN_THE_FILE` when the deployed conf does not carry this key, else `None`."""
    return None if row.current is not None else NOT_IN_THE_FILE


def bool_words(row: TuningRow) -> tuple[str, str]:
    """The (on, off) spellings this row's own file uses."""
    seen = (row.current or row.default or "").strip().lower()
    return BOOL_WORDS.get(seen, DEFAULT_BOOL)


# -- the widgets ------------------------------------------------------------


class RowEditor(QWidget):
    """One setting: its name, the author's sentence, one control, and what changed.

    `control` is `None` on a row this app does not write, rather than a disabled
    control: a greyed spinner still shows a number, and a number shown beside a
    setting nobody can change is a number somebody will try to change.
    """

    edited = Signal()

    def __init__(self, row: TuningRow, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.row = row
        self.kind = control_kind(row)
        self._start = starting_value(row)
        self._words = bool_words(row)

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 4, 0, 4)
        box.setSpacing(2)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.label = QLabel(row.label, self)
        self.label.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-weight: bold;")
        self.label.setToolTip(row.key)
        top.addWidget(self.label)
        top.addStretch(1)
        self.control: QCheckBox | QSpinBox | QLineEdit | None = self._make_control()
        if self.control is not None:
            top.addWidget(self.control)
        else:
            self.value_label: QLabel | None = QLabel(self._start or "—", self)
            self.value_label.setFont(QFont("monospace"))
            self.value_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED};")
            top.addWidget(self.value_label)
        box.addLayout(top)

        # The key itself, always, under whatever the label says: `label` may be
        # the catalog's own words, and the key is what the user will search the
        # module's documentation for.
        self.key_label = QLabel(row.key, self)
        self.key_label.setFont(QFont("monospace"))
        self.key_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED};")
        box.addWidget(self.key_label)

        self.explain_label: QLabel | None = None
        if row.explain:
            # Shown only when the catalog has the author's sentence. Absent
            # means absent: prose invented about somebody else's module would
            # be worse than the silence it replaced.
            self.explain_label = QLabel(row.explain, self)
            self.explain_label.setWordWrap(True)
            self.explain_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED};")
            box.addWidget(self.explain_label)

        self.note_label: QLabel | None = None
        note = value_note(row)
        if note is not None:
            self.note_label = QLabel(note, self)
            self.note_label.setWordWrap(True)
            self.note_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-style: italic;")
            box.addWidget(self.note_label)

        self.reason_label: QLabel | None = None
        if row.read_only_reason is not None:
            self.reason_label = QLabel(row.read_only_reason, self)
            self.reason_label.setWordWrap(True)
            self.reason_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED};")
            box.addWidget(self.reason_label)

        self.changed_label = QLabel("", self)
        self.changed_label.setStyleSheet(f"color: {COLOR_TEXT_WARNING};")
        self.changed_label.setVisible(False)
        box.addWidget(self.changed_label)

    def _make_control(self) -> QCheckBox | QSpinBox | QLineEdit | None:
        if self.kind == "none":
            return None
        if self.kind == "switch":
            switch = QCheckBox(self)
            switch.setChecked(self._start.strip().lower() in ("1", "true"))
            switch.stateChanged.connect(lambda _state: self._touched())
            return switch
        if self.kind == "spinner":
            assert self.row.min is not None and self.row.max is not None
            spinner = QSpinBox(self)
            spinner.setRange(self.row.min, self.row.max)
            try:
                spinner.setValue(int(self._start))
            except ValueError:
                # The file holds something an `int` key cannot mean. The spinner
                # cannot show it, so it sits at the nearest bound and the note
                # below says what the file actually says -- the alternative is a
                # control that silently reports a value nobody wrote.
                spinner.setValue(self.row.min)
            spinner.valueChanged.connect(lambda _value: self._touched())
            return spinner
        field = QLineEdit(self)
        field.setText(self._start)
        field.textChanged.connect(lambda _text: self._touched())
        return field

    def value(self) -> str:
        """What this row would be written as, in the file's own spelling."""
        if isinstance(self.control, QCheckBox):
            on, off = self._words
            return on if self.control.isChecked() else off
        if isinstance(self.control, QSpinBox):
            return str(self.control.value())
        if isinstance(self.control, QLineEdit):
            return self.control.text()
        return self._start

    @property
    def changed(self) -> bool:
        return self.control is not None and self.value() != self._start

    def _touched(self) -> None:
        shown = self.changed
        if shown:
            self.changed_label.setText(CHANGED_FROM.format(old=self._start or NOTHING))
        self.changed_label.setVisible(shown)
        self.edited.emit()

    def set_enabled_actions(self, enabled: bool) -> None:
        if self.control is not None:
            self.control.setEnabled(enabled)


class CardWidget(QGroupBox):
    """One module's card: its rows, what a save costs, and the two presses."""

    save_pressed = Signal(str)
    revert_pressed = Signal(str)

    def __init__(self, card: TuningCard, parent: QWidget | None = None) -> None:
        super().__init__(card.module_name, parent)
        self.card = card
        box = QVBoxLayout(self)
        box.setSpacing(4)

        self.files_label = QLabel("\n".join(card.files), self)
        self.files_label.setFont(QFont("monospace"))
        self.files_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED};")
        box.addWidget(self.files_label)

        # The chip the mockup shows, and its sentence is COMPUTED
        # (`tuning.apply_rule`) rather than typed here, so the words on the card
        # and the words under the raw editor cannot drift apart.
        self.rule_label = QLabel(card.rule_sentence, self)
        self.rule_label.setWordWrap(True)
        self.rule_label.setStyleSheet(
            f"color: {COLOR_TEXT_WARNING if card.rule != 'restart' else COLOR_UNCOMMON};"
        )
        box.addWidget(self.rule_label)

        self.editors: dict[str, RowEditor] = {}
        for row in card.rows:
            editor = RowEditor(row, self)
            self.editors[row.key] = editor
            box.addWidget(editor)

        self.save_button: QPushButton | None = None
        self.revert_button: QPushButton | None = None
        if card.editable:
            # No Save on a card nothing can be written on -- a button that
            # refuses every press is a worse answer than no button and the
            # sentence that says why.
            actions = QHBoxLayout()
            actions.addStretch(1)
            self.revert_button = QPushButton("Revert", self)
            self.revert_button.setToolTip(
                "Put this module's conf back from the backup Yu'lon took at the last save."
            )
            self.revert_button.clicked.connect(
                lambda: self.revert_pressed.emit(self.card.module_id)
            )
            actions.addWidget(self.revert_button)
            self.save_button = QPushButton("Save", self)
            self.save_button.clicked.connect(lambda: self.save_pressed.emit(self.card.module_id))
            actions.addWidget(self.save_button)
            box.addLayout(actions)

    def edits(self) -> dict[str, str]:
        """The keys the user moved, and nothing else.

        Only what CHANGED: a save that wrote every key on the card would rewrite
        the 73 defaultless ones with a default nobody chose, which is the exact
        thing `apply.py` declines to do.
        """
        return {key: editor.value() for key, editor in self.editors.items() if editor.changed}

    def set_enabled_actions(self, enabled: bool) -> None:
        for editor in self.editors.values():
            editor.set_enabled_actions(enabled)
        for button in (self.save_button, self.revert_button):
            if button is not None:
                button.setEnabled(enabled)


class TuningPanel(QWidget):
    """The tab: guided cards on one side, the file itself on the other.

    Call down / signal up, like `ModulesPanel`. It is handed cards and file
    text; it says which module was saved, which file was picked, and what the
    editor holds. It opens nothing.
    """

    save_pressed = Signal(str)
    revert_pressed = Signal(str)
    file_selected = Signal(str)
    file_save_pressed = Signal(str)
    file_reload_pressed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cards: dict[str, CardWidget] = {}
        self._order: list[str] = []
        self._actions_enabled = True
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.split = QSplitter(Qt.Orientation.Horizontal, self)

        self._area = QScrollArea(self.split)
        self._area.setWidgetResizable(True)
        self._content = QWidget(self._area)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setSpacing(8)
        self.empty_label = QLabel(NOTHING_TO_TUNE, self._content)
        self.empty_label.setWordWrap(True)
        self.empty_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED};")
        self._content_layout.addWidget(self.empty_label)
        self._content_layout.addStretch(1)
        self._area.setWidget(self._content)
        self.split.addWidget(self._area)

        right = QWidget(self.split)
        right_box = QVBoxLayout(right)
        right_box.setContentsMargins(4, 0, 0, 0)
        picker_row = QHBoxLayout()
        picker_row.addWidget(QLabel("File:", right))
        self.files = QComboBox(right)
        self.files.currentTextChanged.connect(self._file_picked)
        picker_row.addWidget(self.files, 1)
        right_box.addLayout(picker_row)
        self.file_note = QLabel("", right)
        self.file_note.setWordWrap(True)
        self.file_note.setStyleSheet(f"color: {COLOR_TEXT_MUTED};")
        right_box.addWidget(self.file_note)
        self.editor = QPlainTextEdit(right)
        self.editor.setFont(QFont("monospace"))
        self.editor.setStyleSheet(
            f"background-color: {COLOR_BG_PANEL}; border: 1px solid {COLOR_GOLD_BORDER}; "
            f"color: {COLOR_TEXT_PRIMARY};"
        )
        self.editor.textChanged.connect(self._relint)
        right_box.addWidget(self.editor, 1)
        self.lint_label = QLabel("", right)
        self.lint_label.setWordWrap(True)
        self.lint_label.setStyleSheet(f"color: {COLOR_TEXT_WARNING};")
        right_box.addWidget(self.lint_label)
        file_actions = QHBoxLayout()
        self.file_reload_button = QPushButton("Reload from disk", right)
        self.file_reload_button.clicked.connect(self.file_reload_pressed.emit)
        file_actions.addWidget(self.file_reload_button)
        file_actions.addStretch(1)
        self.file_save_button = QPushButton("Save file", right)
        self.file_save_button.clicked.connect(
            lambda: self.file_save_pressed.emit(self.editor.toPlainText())
        )
        file_actions.addWidget(self.file_save_button)
        right_box.addLayout(file_actions)
        self.split.addWidget(right)
        outer.addWidget(self.split)

    # ------------------------------------------------------------ the cards

    def set_cards(self, cards: Sequence[TuningCard]) -> None:
        for widget in self._cards.values():
            widget.setParent(None)
            widget.deleteLater()
        self._cards.clear()
        self._order.clear()
        for card in cards:
            widget = CardWidget(card, self._content)
            widget.save_pressed.connect(self.save_pressed.emit)
            widget.revert_pressed.connect(self.revert_pressed.emit)
            widget.set_enabled_actions(self._actions_enabled)
            self._cards[card.module_id] = widget
            self._order.append(card.module_id)
            self._content_layout.insertWidget(self._content_layout.count() - 1, widget)
        self.empty_label.setVisible(not self._cards)

    def cards(self) -> tuple[CardWidget, ...]:
        return tuple(self._cards[module_id] for module_id in self._order)

    def card(self, module_id: str) -> CardWidget:
        return self._cards[module_id]

    def edits(self, module_id: str) -> dict[str, str]:
        return self._cards[module_id].edits()

    def set_enabled_actions(self, enabled: bool) -> None:
        self._actions_enabled = enabled
        for widget in self._cards.values():
            widget.set_enabled_actions(enabled)
        self.files.setEnabled(enabled)
        self.file_reload_button.setEnabled(enabled)
        self.file_save_button.setEnabled(enabled and not self.editor.isReadOnly())

    # ------------------------------------------------------------- the file

    def set_files(self, files: Sequence[str]) -> None:
        """Fill the picker, keeping the file already open if it is still listed."""
        keep = self.files.currentText()
        blocked = self.files.blockSignals(True)
        self.files.clear()
        self.files.addItems(list(files))
        self.files.blockSignals(blocked)
        if keep in files:
            self.files.setCurrentText(keep)
        elif files:
            self._file_picked(files[0])

    def set_file_text(self, text: str, *, read_only: bool, note: str | None) -> None:
        blocked = self.editor.blockSignals(True)
        self.editor.setPlainText(text)
        self.editor.blockSignals(blocked)
        self.editor.setReadOnly(read_only)
        self.file_note.setText(note or "")
        self.file_note.setVisible(bool(note))
        self.file_save_button.setEnabled(self._actions_enabled and not read_only)
        self._relint()

    def _file_picked(self, name: str) -> None:
        if name:
            self.file_selected.emit(name)

    def _relint(self) -> None:
        """The live guard, on an editable `.conf` only.

        Read-only only. DML scopes its own lint to `.conf` files because its
        picker also lists `.env` and compose files; this picker lists nothing
        but `.conf` files, because every other backend a manifest can name is
        already read-only (`tuning._read_only_reason`) — so the read-only flag
        is the same gate, asked once.
        """
        if self.editor.isReadOnly():
            self.lint_label.setText("")
            return
        said = tuning.lint_sentence(tuning.lint(self.editor.toPlainText()))
        self.lint_label.setText(said or "")

    # ------------------------------------------------------------ the shape

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802  (Qt's own name)
        """Side by side while there is room, stacked when there is not."""
        wide = self.width() >= NARROW_WIDTH
        self.split.setOrientation(Qt.Orientation.Horizontal if wide else Qt.Orientation.Vertical)
        super().resizeEvent(event)


__all__ = [
    "CHANGED_FROM",
    "CardWidget",
    "NOTHING",
    "NOTHING_TO_TUNE",
    "NOT_IN_THE_FILE",
    "RowEditor",
    "TuningCard",
    "TuningPanel",
    "bool_words",
    "build_tuning_cards",
    "control_kind",
    "starting_value",
    "value_note",
]
