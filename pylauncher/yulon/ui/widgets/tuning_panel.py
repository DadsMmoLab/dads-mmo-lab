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
from yulon.manifest_store import FAMILY_FILES
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

CHIP_READ_ONLY = "read-only in this version"
"""The chip on a row this app will not write (T44 item 9).

The same fact `read_only_reason` already spells out in a sentence under the
row, at a glance: a person scanning a card of twelve settings needs to see
which of them are not theirs to change without reading twelve paragraphs.
"""

CHIP_FREE_TEXT = "free text"
"""The chip on a key the catalog declared no `type` for.

Exactly `control_kind(row) == "box" and row.type is None`, which is T43's
safety rule from the other side: such a key gets the one control that can
express anything, and nothing checks what is typed into it -- `tuning.check()`
has no declaration to check it against. An `int` with one bound is NOT free
text: it gets a text box for want of a range, and its value is still checked.
"""

PENDING_CHIPS: dict[ApplyRule, str] = {
    "rebuild": "Rebuild pending",
    "recreate": "Recreate pending",
    "restart": "Restart pending",
}
"""What a CHANGED row owes, by the rule `tuning.apply_rule()` gives it.

Keyed by the rule rather than written out per row, so the chip and the card's
own sentence -- which prices the same change through the same function --
cannot come to disagree about whether a file needs a restart or a replacement
of the containers. `read-only` has no entry because a read-only row cannot be
changed and so can owe nothing.
"""

HINT_CONF = "read at world start"
HINT_LUA = "patched into a deployed Lua script"
HINT_OTHER = "not a file this version writes"
"""The per-card sentence (T44 item 12), keyed off the BACKENDS the card's rows use.

Off the backends and not off the module's family: a `module` whose only tuning
row is a deployed `.lua` is not read at world start, it is patched into a
script that was copied into the server -- which is the same fact that makes
those rows read-only in this version.
"""

NOTHING = "nothing"
"""What a row that had no value at all says it changed FROM.

An empty string in that sentence reads as a value the file held and lost.
"""

CHANGED_FROM = "{key} · was {old}"
"""What a changed row says, and it NAMES THE KEY (T44 item 10).

`changed from 10` on a card of twelve settings says which value, not which
setting. The mockup's own spelling, and the key is the string a user will
search the module's documentation for.
"""

NOT_IN_THE_FILE = "not in the file — this is the catalog's default, not a setting"
"""The note under a key the deployed conf does not carry.

73 of the 107 shipped keys have no `default` and most have no line in the file
either: `apply.py` declines to write a key with no default, so the conf on disk
never mentions it. Showing the default with no note would read as a setting
that is already there, and pressing Save would look like a no-op when it is
the first time the key has ever been written.
"""

READ_ONLY_SUFFIX = " · read-only"
"""What a file button says about a file this tab will not write (T44 item 13)."""

RAW_REWRITE_NEEDS_RECREATE = (
    "A raw rewrite changes this FILE and nothing else. This install's compose sets AC_* "
    "environment keys on the worldserver, and an AC_* key SHADOWS the matching line in the "
    "conf — a container keeps the environment it was created with, so those keys change only "
    "when the containers are RECREATED, not when the world restarts. If the key you edit here "
    "is one of them, restarting the server will look as though your edit did nothing."
)
"""The warning under the raw editor, and it is a safety message (T44 item 16).

Measured in THIS tree rather than inherited: `catalog/composegen.py` writes a
`docker-compose.override.yml` carrying `DEFAULT_WORLD_ENV` plus
`catalog.json`'s `install.native.azerothcore.world_env`, and its own comment
(composegen.py, "an env key SHADOWS the matching row in playerbots.conf")
records that a settings surface editing that file "will appear to do nothing
until this key is removed", and that a running container keeps what it started
with until it is RECREATED.

The guided save has the same exposure and says the same thing through
`tuning.apply_sentence()`; this sentence exists because the RAW route has no
row, no declaration and no rule to price -- it is a person rewriting a whole
file, and `ModuleFiles.svelte:124-128` on `rust-main` is the prior art:
promising the fast world-only restart there "would be a promise we cannot
keep".
"""

LINT_OK = "✓ every line reads as Key = Value"
"""The verdict when `tuning.lint()` finds nothing (T44 item 14).

Its words are the lint's own rule, not a tick on its own: the guard checks
that each line is blank, a comment, a `[section]` header, or a `Key = Value`
assignment, and a bare "looks fine" would not tell anybody what was checked.
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
    rules: tuple[ApplyRule, ...]
    """EVERY job this card's changes owe, most expensive first, `()` for none.

    A tuple and not one rule: a card whose rows span two costs owes both, and
    naming only the dearer one leaves the cheaper job undone (`tuning.owed`).
    """

    @property
    def rule_sentence(self) -> str:
        return tuning.owed_sentence(self.rules)

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
    # Keyed by (family, id), not by id. Nothing in the store, the schema or the
    # catalog tests makes a manifest id unique across families, and T42 round 2
    # found the same collision four places along on the Modules tab. Grouped by
    # id alone, an `ale` and a `keg` that share `bmah` became ONE card titled
    # with the first family's name and holding both families' rows -- and
    # `save_tuning()` then reads the whole card's declarations out of the FIRST
    # family's manifest, type-checking one module's values against another's
    # before writing them to the other one's file.
    order: list[tuple[str, str]] = []
    grouped: dict[tuple[str, str], list[TuningRow]] = {}
    for row in rows:
        key = (row.family, row.module_id)
        if key not in grouped:
            order.append(key)
            grouped[key] = []
        grouped[key].append(row)
    cards: list[TuningCard] = []
    for key in order:
        module_id = key[1]
        mine = grouped[key]
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
                # `apply_rule()` without `in_clone`, and it can never need it:
                # `ConfFile.file` is relative to the SERVER dir and the model
                # has no `in_clone` field -- only `Patch` does -- so no manifest
                # can point a tuning row at a file inside a module's own source
                # tree. `test_a_conf_file_can_never_be_a_clone_file_so_the_
                # rebuild_branch_has_no_caller` fails the day that changes.
                rules=tuning.owed(tuning.apply_rule(row) for row in mine),
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


def picker_label(file: str, *, duplicate: bool, read_only: bool) -> str:
    """What one file's button says (T44 item 13).

    The BASENAME while it is unique among the files offered, because every
    path here starts with the same 20 characters of `env/dist/etc/` and a row
    of full paths is wider than the window. The full path the moment it is
    not: nothing makes a basename unique, and two modules that both ship
    `mod.conf` must not give a person two identical buttons.
    """
    name = file if duplicate else file.rsplit("/", 1)[-1]
    return f"{name}{READ_ONLY_SUFFIX}" if read_only else name


def row_chips(row: TuningRow, *, changed: bool = False) -> tuple[str, ...]:
    """Everything this row has to say about itself in one word each (T44 item 9).

    Every one of them is a fact T43 already computed and then drew only as
    prose, or not at all: `read_only_reason`, `control_kind()` against
    `row.type`, and `tuning.apply_rule()`.

    `changed` is the live half and is passed in rather than read off the row,
    because "has the user moved this control?" is a property of the WIDGET and
    this function is on the pure side of the module.
    """
    if not row.editable:
        return (CHIP_READ_ONLY,)
    chips: list[str] = []
    if changed:
        pending = PENDING_CHIPS.get(tuning.apply_rule(row))
        if pending is not None:
            chips.append(pending)
    if control_kind(row) == "box" and row.type is None:
        chips.append(CHIP_FREE_TEXT)
    return tuple(chips)


def bounds_note(row: TuningRow) -> str | None:
    """`0–80` for an int the catalog gave BOTH bounds for, else `None` (T44 item 11).

    Both, and never one: the pair is exactly what earns this row a spinner
    (`control_kind`), and printing a half-range would state a limit the
    catalog never declared -- the invention that function refuses a spinner
    over in the first place.
    """
    if row.type != "int" or row.min is None or row.max is None:
        return None
    return f"{row.min}–{row.max}"


def card_hint(card: TuningCard) -> str:
    """What KIND of thing this card's settings are, from its rows' own backends.

    One sentence per card and the dearest backend wins, because a card that
    spans two is honest only about the half a user is most likely to be
    surprised by.
    """
    backends = {row.backend for row in card.rows}
    if "lua" in backends:
        return HINT_LUA
    if "other" in backends:
        return HINT_OTHER
    return HINT_CONF


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
        self._moved = False
        """Whether the user has moved this control since the row was drawn.

        Necessary, not belt-and-braces: a switch cannot DRAW "no value". A
        `bool` key the conf does not carry starts at `""` and is rendered as an
        unchecked box, whose value reads `0` -- so `value() != start` called it
        changed the moment the card appeared, and pressing Save on a card nobody
        had touched wrote five of `mod-transmog`'s keys. A spinner has the same
        hole one control along: it sits at its minimum, not at nothing. Every
        control's signal fires only on a real change, so this flag is exactly
        "somebody moved it", and `value() != start` is still required on top so
        that moving one and moving it back is not a change.
        """

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 4, 0, 4)
        box.setSpacing(2)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.label = QLabel(row.label, self)
        self.label.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-weight: bold;")
        self.label.setToolTip(row.key)
        top.addWidget(self.label)
        # The chips, between the name and the control. Rebuilt on every edit
        # rather than toggled, because the `pending` one comes and goes with
        # the value and the other two never change -- one code path for both
        # keeps them from drifting out of the order `row_chips()` decides.
        self.chips = QLabel("", self)
        self.chips.setStyleSheet(f"color: {COLOR_TEXT_MUTED};")
        top.addWidget(self.chips)
        top.addStretch(1)
        bounds = bounds_note(row)
        self.bounds_label: QLabel | None = None
        if bounds is not None:
            # Beside the spinner, not under it: a spinner shows one number and
            # the question a person has about it is what else it will accept.
            self.bounds_label = QLabel(bounds, self)
            self.bounds_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED};")
            top.addWidget(self.bounds_label)
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
        self._draw_chips()
        self._draw_rail()

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
        return self._moved and self.control is not None and self.value() != self._start

    def _touched(self) -> None:
        self._moved = True
        shown = self.changed
        if shown:
            self.changed_label.setText(
                CHANGED_FROM.format(key=self.row.key, old=self._start or NOTHING)
            )
        self.changed_label.setVisible(shown)
        self._draw_chips()
        self._draw_rail()
        self.edited.emit()

    def _draw_chips(self) -> None:
        """Redraw this row's chips for what is true of it NOW."""
        marks = row_chips(self.row, changed=self.changed)
        self.chips.setText("  ".join(marks))
        self.chips.setVisible(bool(marks))

    def _draw_rail(self) -> None:
        """The amber edge down a changed row (T44 item 10).

        Set here and not once in `__init__`, because it has to come and go: a
        rail every row wears marks nothing, and a value moved and moved back
        is not a change (`changed` asks both questions).
        """
        if self.changed:
            self.setStyleSheet(f"border-left: 2px solid {COLOR_TEXT_WARNING}; padding-left: 6px;")
        else:
            self.setStyleSheet("")

    def set_enabled_actions(self, enabled: bool) -> None:
        if self.control is not None:
            self.control.setEnabled(enabled)


class CardWidget(QGroupBox):
    """One module's card: its rows, what a save costs, and the two presses."""

    save_pressed = Signal(str, str)
    edited = Signal()
    """Somebody moved a control on this card -- which arms "Revert all changes"."""

    revert_pressed = Signal(str, str)
    """`(family, module_id)`, because an id alone can name two cards.

    The card knows which one it is, so the press says so and no reader has to
    resolve it -- `ModulesPanel` solves the same problem by letting a click
    carry its widget (T42 round 2).
    """

    def __init__(self, card: TuningCard, parent: QWidget | None = None) -> None:
        super().__init__(card.module_name, parent)
        self.card = card
        box = QVBoxLayout(self)
        box.setSpacing(4)

        heading = QHBoxLayout()
        self.count_label = QLabel(f"Settings {len(card.rows)}", self)
        self.count_label.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-weight: bold;")
        heading.addWidget(self.count_label)
        # What KIND of thing these settings are, derived from the rows' own
        # backends (`card_hint`) rather than from the module's family.
        self.hint_label = QLabel(card_hint(card), self)
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-style: italic;")
        heading.addWidget(self.hint_label)
        heading.addStretch(1)
        box.addLayout(heading)

        self.files_label = QLabel("\n".join(card.files), self)
        self.files_label.setFont(QFont("monospace"))
        self.files_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED};")
        box.addWidget(self.files_label)

        # The chip the mockup shows, and its sentence is COMPUTED
        # (`tuning.apply_rule`) rather than typed here, so the words on the card
        # and the words under the raw editor cannot drift apart.
        self.rule_label = QLabel(card.rule_sentence, self)
        self.rule_label.setWordWrap(True)
        # Amber for anything more than a restart, and for a card that owes two
        # jobs: the cheap end is one press on this tab, the dear end is a
        # compile or a container replacement.
        cheap = card.rules in ((), ("restart",))
        self.rule_label.setStyleSheet(f"color: {COLOR_UNCOMMON if cheap else COLOR_TEXT_WARNING};")
        box.addWidget(self.rule_label)

        self.editors: dict[str, RowEditor] = {}
        for row in card.rows:
            editor = RowEditor(row, self)
            editor.edited.connect(self.edited.emit)
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
                lambda: self.revert_pressed.emit(self.card.family, self.card.module_id)
            )
            actions.addWidget(self.revert_button)
            self.save_button = QPushButton("Save", self)
            self.save_button.clicked.connect(
                lambda: self.save_pressed.emit(self.card.family, self.card.module_id)
            )
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

    save_pressed = Signal(str, str)
    revert_pressed = Signal(str, str)
    file_selected = Signal(str)
    file_save_pressed = Signal(str)
    file_reload_pressed = Signal()
    file_revert_pressed = Signal()
    edited = Signal()
    """Somebody moved a control on any card (T44 item 7's "Revert all changes")."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cards: dict[tuple[str, str], CardWidget] = {}
        self._order: list[tuple[str, str]] = []
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
        # The picker as BUTTONS (T44 item 13). A combo box shows one file and
        # hides the rest behind a press; this install offers a handful, and
        # which ones they are is half the answer to "what can I tune here?".
        self.files = QWidget(right)
        self._files_layout = QHBoxLayout(self.files)
        self._files_layout.setContentsMargins(0, 0, 0, 0)
        self._files_layout.setSpacing(4)
        self._file_buttons: list[QPushButton] = []
        self._current_file = ""
        right_box.addWidget(self.files)
        self.file_note = QLabel("", right)
        self.file_note.setWordWrap(True)
        self.file_note.setStyleSheet(f"color: {COLOR_TEXT_MUTED};")
        right_box.addWidget(self.file_note)
        # Item 16. Under the note and above the box a person types into,
        # because it is about what typing into it will and will not do.
        self.recreate_warning = QLabel(RAW_REWRITE_NEEDS_RECREATE, right)
        self.recreate_warning.setWordWrap(True)
        self.recreate_warning.setStyleSheet(f"color: {COLOR_TEXT_WARNING};")
        self.recreate_warning.setVisible(False)
        right_box.addWidget(self.recreate_warning)
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
        # The backup's name, said rather than implied (T44 item 15). It is the
        # only record of what the file said before, and the one thing a user
        # needs in order to look at it by hand.
        self.backup_label = QLabel("", right)
        self.backup_label.setWordWrap(True)
        self.backup_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED};")
        right_box.addWidget(self.backup_label)
        file_actions = QHBoxLayout()
        self.file_reload_button = QPushButton("Reload from disk", right)
        self.file_reload_button.clicked.connect(self.file_reload_pressed.emit)
        file_actions.addWidget(self.file_reload_button)
        file_actions.addStretch(1)
        # Dead until there is a backup to restore FROM. A Revert with nothing
        # behind it is a press that can only explain itself, and the card's own
        # Revert already answers that case with a sentence.
        self.file_revert_button = QPushButton("Revert", right)
        self.file_revert_button.setToolTip(
            "Put this file back from the backup Yu'lon took at the last save on this tab."
        )
        self.file_revert_button.setEnabled(False)
        self.file_revert_button.clicked.connect(self.file_revert_pressed.emit)
        file_actions.addWidget(self.file_revert_button)
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
            widget.edited.connect(self.edited.emit)
            widget.set_enabled_actions(self._actions_enabled)
            self._cards[(card.family, card.module_id)] = widget
            self._order.append((card.family, card.module_id))
            self._content_layout.insertWidget(self._content_layout.count() - 1, widget)
        self.empty_label.setVisible(not self._cards)

    def cards(self) -> tuple[CardWidget, ...]:
        return tuple(self._cards[key] for key in self._order)

    def has_edits(self) -> bool:
        """Whether anything on this tab has been changed and not saved."""
        return any(widget.edits() for widget in self._cards.values())

    def _key_for(self, which: str | tuple[str, str]) -> tuple[str, str]:
        """The card a caller names, by `(family, id)` or by a bare id.

        `ModulesPanel._key_for()`'s rule rather than a second one invented here:
        a bare id resolves in `FAMILY_FILES` order, so the ambiguity is settled
        in one place per panel and both panels settle it the same way. Callers
        that HAVE the family -- every one inside this module, and the view,
        which reads `TuningCard.family` -- pass the pair and never guess.
        """
        if isinstance(which, tuple):
            return which
        for family in FAMILY_FILES:
            if (family, which) in self._cards:
                return (family, which)
        for key in self._cards:
            if key[1] == which:
                return key
        raise KeyError(which)

    def card(self, which: str | tuple[str, str]) -> CardWidget:
        return self._cards[self._key_for(which)]

    def edits(self, which: str | tuple[str, str]) -> dict[str, str]:
        return self.card(which).edits()

    def set_enabled_actions(self, enabled: bool) -> None:
        self._actions_enabled = enabled
        for widget in self._cards.values():
            widget.set_enabled_actions(enabled)
        self.files.setEnabled(enabled)
        self.file_reload_button.setEnabled(enabled)
        self.file_save_button.setEnabled(enabled and not self.editor.isReadOnly())
        self.file_revert_button.setEnabled(
            enabled and bool(self.backup_label.text()) and not self.editor.isReadOnly()
        )

    # ------------------------------------------------------------- the file

    def set_files(self, files: Sequence[str], *, read_only: Sequence[str] = ()) -> None:
        """Draw one button per file, keeping the file already open if it is still listed.

        `read_only` is handed IN rather than decided here: which files this
        app will not write is `controller_view.TUNING_CORE_FILES`, a decision
        about who owns core configuration, and this widget must not carry a
        second copy of that list.
        """
        keep = self._current_file
        for button in self._file_buttons:
            button.setParent(None)
            button.deleteLater()
        self._file_buttons.clear()
        seen: dict[str, int] = {}
        for file in files:
            name = file.rsplit("/", 1)[-1]
            seen[name] = seen.get(name, 0) + 1
        for file in files:
            button = QPushButton(
                picker_label(
                    file,
                    duplicate=seen[file.rsplit("/", 1)[-1]] > 1,
                    read_only=file in read_only,
                ),
                self.files,
            )
            button.setCheckable(True)
            button.setToolTip(file)
            button.clicked.connect(lambda _checked=False, name=file: self._file_picked(name))
            self._file_buttons.append(button)
            self._files_layout.insertWidget(self._files_layout.count(), button)
        if keep in files:
            self._mark_current(keep)
        elif files:
            self._file_picked(files[0])
        else:
            self._current_file = ""
            self._mark_current("")

    def file_buttons(self) -> tuple[QPushButton, ...]:
        """The picker's buttons, in the order they are drawn."""
        return tuple(self._file_buttons)

    def current_file(self) -> str:
        """Which file the editor is showing, or `""` when there is none."""
        return self._current_file

    def set_backup(self, name: str | None) -> None:
        """Name the backup the last save took, and arm Revert (T44 item 15)."""
        self.backup_label.setText(f"Backup of this file as it was: {name}" if name else "")
        self.file_revert_button.setEnabled(
            self._actions_enabled and bool(name) and not self.editor.isReadOnly()
        )

    def _mark_current(self, file: str) -> None:
        self._current_file = file
        for button in self._file_buttons:
            button.setChecked(button.toolTip() == file)

    def set_file_text(self, text: str, *, read_only: bool, note: str | None) -> None:
        blocked = self.editor.blockSignals(True)
        self.editor.setPlainText(text)
        self.editor.blockSignals(blocked)
        self.editor.setReadOnly(read_only)
        self.file_note.setText(note or "")
        self.file_note.setVisible(bool(note))
        # Only on a file this tab will actually write: a warning about a
        # rewrite nobody can make is noise over `worldserver.conf` (item 16).
        self.recreate_warning.setVisible(not read_only)
        self.file_save_button.setEnabled(self._actions_enabled and not read_only)
        # A backup of the file you were looking at a moment ago is not a
        # backup of this one, so the name goes with the text it described.
        self.set_backup(None)
        self._relint()

    def _file_picked(self, name: str) -> None:
        self._mark_current(name)
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
        # The verdict BOTH ways (T44 item 14). T43 drew only the complaint, so
        # a clean file and an unlinted one looked identical -- and a guard a
        # user cannot tell from a guard that is not running is not reassuring
        # anybody.
        self.lint_label.setText(said or LINT_OK)
        self.lint_label.setStyleSheet(
            f"color: {COLOR_TEXT_WARNING};" if said else f"color: {COLOR_UNCOMMON};"
        )

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
