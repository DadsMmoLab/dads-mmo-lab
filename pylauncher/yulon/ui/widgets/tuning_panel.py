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

import html
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from PySide6.QtCore import (
    QEvent,
    QObject,
    QRegularExpression,
    Qt,
    QTimer,
    Signal,
    SignalInstance,
)
from PySide6.QtGui import QFont, QRegularExpressionValidator, QResizeEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from yulon import server_rates, tuning
from yulon.manifest_store import FAMILY_FILES
from yulon.tuning import ApplyRule, TuningRow
from yulon.ui.message_box import FittedMessageBox
from yulon.ui.theme import (
    COLOR_BG_PANEL,
    COLOR_BG_PARCHMENT_LIGHT,
    COLOR_GOLD_BORDER,
    COLOR_GOLD_BRIGHT,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_WARNING,
    COLOR_UNCOMMON,
)
from yulon.ui.widgets.flow_layout import FlowScroll, flow_bar
from yulon.ui.widgets.panel_style import panel_qss

ControlKind = Literal["switch", "spinner", "box", "none"]

CHIP_READ_ONLY = "read-only in this version"
"""The chip on a row this app will not write (T44 item 9).

The same fact `read_only_reason` already spells out in a sentence under the
row, at a glance: a person scanning a card of twelve settings needs to see
which of them are not theirs to change without reading twelve paragraphs.
"""

CHIP_ON_THE_RATES_CARD = "set on the Server rates card"
"""The chip on a module's row whose key the Server rates card writes (T302).

Not `CHIP_READ_ONLY`: that one promises a later version that writes the row, and
this row will never be written here -- the card above it is where it is changed.
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

COMPOSE_OVERRIDE = "docker-compose.override.yml"
"""Where the `AC_*` rows that beat a conf key live, named so a user can go there."""


def shadow_warning(shadowed: Sequence[tuple[str, str]]) -> str:
    """What to say about the keys of this file the container environment beats.

    T44 item 16, rewritten in round 2 on two counts the review was right about.

    **Where it is shown.** Round 1 put it on every editable conf, which is
    noise on the ones nothing shadows and says nothing at all about WHICH key
    is affected. The pairs come from `composegen.shadowed_by_env()`, which
    matches this file's own keys against the env rows this install's override
    really carries, through AzerothCore's measured `AC_ + upper_snake`
    transform (`Config.cpp:435-438`).

    **What it tells the user to do.** Round 1 said to recreate the containers.
    That does not work: the override is regenerated from the same data, so a
    recreate re-applies the same `AC_*` value and the edit still does nothing.
    The row has to change or go FIRST -- and only then does the recreate the
    new environment needs make any difference. A remedy that cannot work is
    worse than none, because the user spends a server outage finding that out.

    The keys are named, all of them, rather than counted: the question a person
    has in front of this warning is whether the line they are about to edit is
    one of them.
    """
    pairs = ", ".join(f"{key} (set by {name})" for key, name in shadowed)
    return (
        f"Editing this file will NOT change {pairs}. This install's "
        f"{COMPOSE_OVERRIDE} sets that environment row on the worldserver, and AzerothCore "
        f"reads the environment in preference to the conf file and to its own default — so "
        f"recreating the containers is not enough either, because they are recreated from "
        f"the same {COMPOSE_OVERRIDE}. Change or remove the row there first, then recreate. "
        f"Every other key in this file is read from the file."
    )


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
"""Below this, the cards and the file editor take turns instead of sitting side by side.

A number and not a stylesheet query, because a `QSplitter` cannot be told to
wrap, and the resize is where the app finds out which shape it is in.

Turns and not a stack (T190). Stacked, the 960x640 tab gave the cards a 72px
strip over the editor -- and the vertical split's 20/80 heights came back as
20/80 WIDTHS the moment the window grew, because nothing reset them: every
session passes a narrow layout first, so every wide window had a 219px column
of cards that scrolled sideways.
"""

CARDS_MIN_WIDTH = 440
"""The narrowest the card column may be side by side (T190): a card, its rows and
their values still read across. Raised by `TuningPanel.set_header` when the
header above the cards asks for more."""

CARDS_MAX_WIDTH = 760
"""The widest a fresh split makes the card column: past it the rows only spread out."""

EDITOR_MIN_WIDTH = 360
"""The narrowest the file editor may be side by side (T190)."""

FILE_AREA_NAME = "tuning-file-scroll"
"""The file side's scroll area, named so its own sheet reaches it and nothing else."""

FILE_BUTTONS_AREA_NAME = "tuning-file-buttons-scroll"
"""The file buttons' own scroll area (T569), named for the same reason."""

FILE_BUTTON_LINES = 3
"""How many lines of file buttons the Tuning tab shows before the rest scrolls (T569).

Three lines is what leaves the editor its four lines at the 1280x800 the app
opens at, with the restart banner and the open report over the tab."""

SIDE_SETTINGS = "Settings"
SIDE_EDIT_FILE = "Edit file"
"""The narrow window's two-button switch (T190).

Two checkable buttons and not a `QTabWidget`: the gamepad's RB/LB go to the
nearest tab widget (`gamepad._nearest_tab_widget`), and an inner one would take
them from the app's own tabs whenever the focus was on this panel.
"""


CHECKED_QSS = (
    f"QPushButton:checked {{ background-color: {COLOR_BG_PARCHMENT_LIGHT}; "
    f"color: {COLOR_GOLD_BRIGHT}; border-bottom: 2px solid {COLOR_GOLD_BRIGHT}; }}"
)
"""How a chosen button looks: the switch's open side, the open file's button (T190).

The theme has no `:checked` rule for a push button, so a chosen one read the
same as the rest and only what lay below said which was chosen. The theme's
own hover sheet and accent; no colour of this panel's.
"""

FILE_READ_ONLY = "read-only — the server's own file"
"""What the editor's title adds for a file this tab will not write (T190)."""

CHOICE_SAVE = "Save"
CHOICE_DISCARD = "Discard"
CHOICE_CANCEL = "Cancel"
CHOICE_OVERWRITE = "Overwrite"
CHOICE_RELOAD = "Reload instead"
"""The answers to the panel's two questions (T190): typing in the editor and another
file picked; typing in the editor and Save file over a file changed on disk."""

UNSAVED_ANSWERS = (CHOICE_SAVE, CHOICE_DISCARD, CHOICE_CANCEL)
UNSAVED_TITLE = "Unsaved typing"
UNSAVED_QUESTION = (
    "{name} has typing you have not saved. Save it before opening {other}, or discard it?"
)
UNSAVED_GONE_QUESTION = (
    "{name} is no longer offered on this tab, so your typing in it cannot be saved here. "
    "Discard it and open {other}?"
)
STALE_TITLE = "File changed on disk"
STALE_QUESTION = (
    "{name} changed on disk after the editor read it, or could not be read again, so the "
    "editor may not show what the file holds now. Overwrite it with your typing, or reload "
    "the file and lose your typing?"
)

EDITOR_STALE = (
    "This file changed on disk after the editor read it, and the editor kept your typing, "
    "so it does not show that change. Save file asks before writing over it."
)
EDITOR_UNREAD = (
    "The editor kept your typing: the file could not be read again, so the editor cannot "
    "tell whether it changed. Save file asks before writing over it."
)
"""Under a re-read that FAILED with typing in the editor (T190 fix round 2): the read's
error says what went wrong, this says the typing was not blanked with it."""

EDITOR_GONE = (
    "This file is no longer offered on this tab, so Save file is off. The editor kept your "
    "typing so you can copy it; pick another file to leave it."
)
"""Under typing whose file a reload no longer lists (T190 fix round 2)."""

"""What the editor's note says when a re-read found the file changed under typing (T190).

A card's Save, Revert or Reset, or Reload from disk, read the open file again;
the typing is kept rather than replaced, and without this the editor showed a
file that was no longer the one on disk with nothing to say so.
"""


def split_sizes(total: int, cards_min: int = CARDS_MIN_WIDTH) -> tuple[int, int]:
    """`(cards, editor)` for a side-by-side split `total` pixels wide (T190).

    Half each, the cards held between `cards_min` and `CARDS_MAX_WIDTH` -- and
    `cards_min` wins over the cap, because a header the column has to hold is
    not something a maximum can shrink.
    """
    cards = min(max(total // 2, cards_min), max(CARDS_MAX_WIDTH, cards_min))
    return cards, total - cards


def is_narrow(width: int, cards_min: int = CARDS_MIN_WIDTH) -> bool:
    """Whether a panel `width` wide shows one half at a time (T190).

    Under `NARROW_WIDTH`, or under what both halves need side by side.
    """
    return width < max(NARROW_WIDTH, cards_min + EDITOR_MIN_WIDTH)


VALUE_MIN_CHARS = 12
"""The fewest characters a value box keeps beside its label (T190 B3).

In a narrow card a long label took the whole row and squeezed the box until
its value was gone; the label wraps instead, and the box keeps this much.
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
        if row.read_only_reason == server_rates.ON_THE_RATES_CARD:
            return (CHIP_ON_THE_RATES_CARD,)
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
    """`0–80` for an int or a float (T302) given BOTH bounds, else `None` (T44 item 11).

    Both, and never one: the pair is exactly what earns this row a spinner
    (`control_kind`), and printing a half-range would state a limit the
    catalog never declared -- the invention that function refuses a spinner
    over in the first place.
    """
    if row.type not in ("int", "float") or row.min is None or row.max is None:
        return None
    return f"{row.min}–{row.max}"


def card_heading(card: TuningCard) -> tuple[str, str]:
    """`(title, count)` for a card's first line (T190 I9): the module, then how many.

    The module's name is the title. Before, the bold primary line said
    "Settings 3" and the name sat in the frame, muted and small, so the eye
    found three cards called "Settings".
    """
    count = len(card.rows)
    return card.module_name, f"{count} setting" if count == 1 else f"{count} settings"


def lifted_rule(cards: Sequence[TuningCard]) -> tuple[ApplyRule, ...] | None:
    """The rule said once above the cards instead of on each of them, or `None` (T190 B15).

    Only a bare restart, the cheap and common case: it is the same sentence on
    every conf card. A card that owes anything dearer, or two jobs, keeps its own
    sentence on the card, where it is read beside the Save it prices.
    """
    restart: tuple[ApplyRule, ...] = ("restart",)
    return restart if any(card.rules == restart for card in cards) else None


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


def shows_key_line(row: TuningRow) -> bool:
    """Whether the key gets a line of its own under the label (T190 B3).

    Only when the label says something else. A key with no `label` in the
    catalog -- 45 of 157 -- is labelled with the key itself (`tuning.rows_for`),
    and a second line saying the same words is a line a value could have had.
    """
    return row.label != row.key


INT_TEXT = r"-?[0-9]*"
"""What a box for an `int` with fewer than two bounds lets a player type (T190).

The digits 0 to 9 and a leading minus, and NO range: the range is the catalog's
to state, and `tuning.check()` applies the one bound it has at Save. `[0-9]` and
not `\\d`: `_guard_number` asks this rule in Python, where `\\d` also matches other
scripts' digits, which no server reads. `*` and not `+`, so the box can be
emptied on the way to a new number.
"""


DECIMAL_TEXT = r"-?[0-9]*\.?[0-9]*"
"""What a box for a `float` key lets a player type (T302): digits and one point.

The partial spellings on the way to a number (`-`, `2.`, `.`) are let through,
and `tuning.check()` decides at Save, with `tuning.DECIMAL`, whether the result
is one the core reads as the same number. The range is not typed into the box
-- it is printed beside it (`bounds_note`) and checked at Save.
"""

NUMBER_TEXT: dict[str, str] = {"int": INT_TEXT, "float": DECIMAL_TEXT}
"""The box rule per numeric `type`; any other type's box takes any text."""


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
        # Wraps, so a long label gives up width to the value rather than the
        # value giving up all of its own (T190 B3).
        self.label.setWordWrap(True)
        top.addWidget(self.label, 1)
        # The chips, between the name and the control. Rebuilt on every edit
        # rather than toggled, because the `pending` one comes and goes with
        # the value and the other two never change -- one code path for both
        # keeps them from drifting out of the order `row_chips()` decides.
        self.chips = QLabel("", self)
        self.chips.setStyleSheet(f"color: {COLOR_TEXT_MUTED};")
        top.addWidget(self.chips)
        # No spacer here: the label's stretch takes the spare width, so it
        # wraps only when the row really is too narrow for it on one line.
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

        # The key itself under the label whenever the label is the catalog's own
        # words: the key is what the user will search the module's documentation
        # for. Not when the label already IS the key (`shows_key_line`); the
        # label's tooltip names it either way.
        self.key_label: QLabel | None = None
        if shows_key_line(row):
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
            self._keep_room(spinner)
            return spinner
        field = QLineEdit(self)
        # The file's value as written, BEFORE the validator: `setText` does not
        # validate, so a value the box would refuse to have typed is still shown
        # rather than blanked -- the T43 rule that nothing here invents a value.
        field.setText(self._start)
        pattern = NUMBER_TEXT.get(self.row.type or "")
        if pattern is not None:
            numbers = QRegularExpressionValidator(QRegularExpression(pattern), field)
            self._guard_number(field, numbers, pattern)
            field.textChanged.connect(lambda _text: self._guard_number(field, numbers, pattern))
        field.textChanged.connect(lambda _text: self._touched())
        self._keep_room(field)
        return field

    @staticmethod
    def _guard_number(field: QLineEdit, numbers: QRegularExpressionValidator, pattern: str) -> None:
        """The number rule on the box only while its text keeps it (T190 final review).

        A file can hold `1.5` under an `int` key. With the rule on, every edit of
        it -- Backspace included -- made another text the rule refuses, so Qt
        refused the edit and the value could not be changed at all. Off while the
        text breaks it, on again the moment the text is a number.
        """
        fits = re.fullmatch(pattern, field.text()) is not None
        # `None` is Qt's own "no validator"; the stubs type the argument as required.
        field.setValidator(numbers if fits else None)  # type: ignore[arg-type]

    def control_state(self) -> bool | int | str | None:
        """What the control is set to, in its own terms: checked, a number, the text."""
        if isinstance(self.control, QCheckBox):
            return self.control.isChecked()
        if isinstance(self.control, QSpinBox):
            return self.control.value()
        if isinstance(self.control, QLineEdit):
            return self.control.text()
        return None

    def set_control_state(self, state: bool | int | str | None) -> None:
        """Put the control back to `control_state()`'s answer, if it is still that kind of control.

        Through the control's own setter, so its change signal marks the row as
        moved exactly as a press would -- and a state that matches what the
        file now says moves nothing, so it is not counted as typing.
        """
        if isinstance(self.control, QCheckBox) and isinstance(state, bool):
            self.control.setChecked(state)
        elif isinstance(self.control, QSpinBox) and type(state) is int:
            self.control.setValue(state)
        elif isinstance(self.control, QLineEdit) and isinstance(state, str):
            self.control.setText(state)

    def _keep_room(self, control: QWidget) -> None:
        """`VALUE_MIN_CHARS` of this control's own font, so a value is never squeezed out.

        Counted again whenever that font changes (`eventFilter`): the app
        restyles every font with the window's width, and a floor counted in the
        font the box was built with fell 2px short once it grew.
        """
        control.setMinimumWidth(VALUE_MIN_CHARS * control.fontMetrics().averageCharWidth())
        if not getattr(self, "_watching_font", False):
            self._watching_font = True
            control.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802  (Qt's own name)
        # By identity with the control, read with `getattr`: a font can change
        # before `__init__` has stored the control it built.
        if (
            event.type() == QEvent.Type.FontChange
            and isinstance(watched, QWidget)
            and watched is getattr(self, "control", None)
        ):
            self._keep_room(watched)
        return False

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
        # No frame title (T190 I9): the module's name is the card's heading,
        # drawn once, bold and primary, and still its accessible name.
        super().__init__("", parent)
        self.card = card
        # What `panel_style.panel_qss()` selects to notch the title centre-top.
        self.setObjectName("tuningCard")
        self.setAccessibleName(card.module_name)
        box = QVBoxLayout(self)
        box.setSpacing(4)

        title, count = card_heading(card)
        heading = QHBoxLayout()
        self.title_label = QLabel(title, self)
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-weight: bold;")
        heading.addWidget(self.title_label, 1)
        self.count_label = QLabel(count, self)
        self.count_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED};")
        heading.addWidget(self.count_label)
        box.addLayout(heading)
        # What KIND of thing these settings are, derived from the rows' own
        # backends (`card_hint`) rather than from the module's family.
        self.hint_label = QLabel(card_hint(card), self)
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-style: italic;")
        box.addWidget(self.hint_label)

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

    def set_rule_lifted(self, lifted: bool) -> None:
        """Hide this card's own rule sentence and hint while the panel says them once (B15)."""
        self.rule_label.setVisible(not lifted)
        self.hint_label.setVisible(not lifted)

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
        self._header: QWidget | None = None
        self._pressed_card: tuple[str, str] | None = None
        """The card whose Save or Revert is being handled: its redraw shows the
        file, not the typing that press just wrote or put back (T190)."""
        self._owed_cards: set[tuple[str, str]] = set()
        """Pressed cards whose redraw the view put off (`defer_pressed_card`)."""
        self.choose: Callable[[str, str, tuple[str, ...]], str | None] = self._ask
        """`(title, question, answers) -> the answer pressed`, `None` for none.

        An attribute so a test answers it; the app asks in a message box."""
        self._narrow: bool | None = None
        """The shape last laid out: `None` until the first resize, so the first
        one -- wide or narrow -- is a change and sets the split up (T190)."""
        self.setStyleSheet(panel_qss())
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        # The narrow window's switch (T190): which half has the whole panel.
        # Hidden until a resize finds the panel narrow; starts on the settings,
        # and the choice is kept across a trip through a wide window.
        self.side_buttons = QWidget(self)
        side_box = QHBoxLayout(self.side_buttons)
        side_box.setContentsMargins(0, 0, 0, 0)
        side_box.setSpacing(4)
        self.settings_button = QPushButton(SIDE_SETTINGS, self.side_buttons)
        self.edit_file_button = QPushButton(SIDE_EDIT_FILE, self.side_buttons)
        # Exclusive by hand and not through an exclusive `QButtonGroup`: Qt
        # takes Tab focus off a group's unchecked buttons, and the pad stops
        # only where Tab focus is -- Right from Settings went past "Edit file"
        # up to the tab's bar, and the unchosen side could not be reached.
        for button in (self.settings_button, self.edit_file_button):
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, chosen=button: self._side_picked(chosen))
            side_box.addWidget(button)
        side_box.addStretch(1)
        self.settings_button.setChecked(True)
        # The chosen side says so: the theme has no `:checked` rule for a
        # button, so both read alike and only the content below told them apart.
        # The theme's own hover sheet and accent, no colour of this panel's.
        self.side_buttons.setStyleSheet(CHECKED_QSS)
        self.side_buttons.setVisible(False)
        outer.addWidget(self.side_buttons)
        # Always side by side (T190). A vertical split's sizes came back as
        # widths when the window grew; one half is hidden instead when narrow.
        self.split = QSplitter(Qt.Orientation.Horizontal, self)
        self.split.setChildrenCollapsible(False)

        self._area = QScrollArea(self.split)
        self._area.setWidgetResizable(True)
        # The column has a floor (`CARDS_MIN_WIDTH`, or the header's) that the
        # cards fit in, so no sideways scroll bar appears; one is left possible
        # rather than turned off, because off would cut a card that somehow did
        # not fit instead of letting it be scrolled to (cold review round 1).
        self._area.setMinimumWidth(CARDS_MIN_WIDTH)
        self._content = QWidget(self._area)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setSpacing(8)
        # The restart sentence, once, over every card that owes only a restart
        # (T190 B15, `lifted_rule`). Under the header: `set_header` inserts
        # above it.
        self.rule_note = QLabel("", self._content)
        self.rule_note.setWordWrap(True)
        self.rule_note.setStyleSheet(f"color: {COLOR_UNCOMMON};")
        self.rule_note.setVisible(False)
        self._content_layout.addWidget(self.rule_note)
        self.empty_label = QLabel(NOTHING_TO_TUNE, self._content)
        self.empty_label.setWordWrap(True)
        self.empty_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED};")
        self._content_layout.addWidget(self.empty_label)
        self._content_layout.addStretch(1)
        self._area.setWidget(self._content)
        self.split.addWidget(self._area)

        self._file_side = QWidget(self.split)
        self._file_side.setMinimumWidth(EDITOR_MIN_WIDTH)
        # The file side scrolls, as the cards' side does (T190, the Linux live
        # test). Its rows -- two lines of file buttons at 960x640, the file's
        # name, its note, the editor's floor, the backup line, the presses --
        # can ask for more height than the tab has left under the restart
        # banner and an open "Last action"; laid out straight into the
        # splitter, Qt cut every row at once and drew them over one another.
        # In a scroll area the rows keep their heights and the side scrolls.
        file_side_box = QVBoxLayout(self._file_side)
        file_side_box.setContentsMargins(0, 0, 0, 0)
        self._file_area = QScrollArea(self._file_side)
        self._file_area.setObjectName(FILE_AREA_NAME)
        self._file_area.setWidgetResizable(True)
        self._file_area.setFrameShape(QFrame.Shape.NoFrame)
        # Not a stop of its own for the pad or Tab: the file buttons, the editor
        # and the presses are, and the pad scrolls to whichever it lands on.
        # Focusable, the area took Down from the switch before the conf
        # buttons under it (T175's press, `test_controller_view`).
        self._file_area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._file_area.setStyleSheet(
            f"QScrollArea#{FILE_AREA_NAME} {{ background-color: transparent; border: none; }}"
            f"QScrollArea#{FILE_AREA_NAME} > QWidget > QWidget {{ background-color: transparent; }}"
        )
        file_side_box.addWidget(self._file_area)
        right = QWidget(self._file_area)
        right_box = QVBoxLayout(right)
        right_box.setContentsMargins(4, 0, 0, 0)
        # The picker as BUTTONS (T44 item 13). A combo box shows one file and
        # hides the rest behind a press; this install offers a handful, and
        # which ones they are is half the answer to "what can I tune here?".
        # A `FlowBar` (T190), so the buttons wrap to a second line rather than
        # each being cut to fit one -- T83's defect, on this row of five.
        # In a `FlowScroll` (T569): a server with fifty confs wraps the buttons
        # onto a dozen lines, which pushed the editor off the screen. The bar
        # keeps `FILE_BUTTON_LINES` lines and the rest scrolls inside it.
        self.files = flow_bar()
        self.files.setStyleSheet(CHECKED_QSS)
        self.file_buttons_area = FlowScroll(self.files, FILE_BUTTON_LINES, right)
        self.file_buttons_area.setObjectName(FILE_BUTTONS_AREA_NAME)
        scroll = f"QScrollArea#{FILE_BUTTONS_AREA_NAME}"
        self.file_buttons_area.setStyleSheet(
            f"{scroll} {{ background-color: transparent; border: none; }}"
            f"{scroll} > QWidget > QWidget {{ background-color: transparent; }}"
        )
        self._file_buttons: list[QPushButton] = []
        self._current_file = ""
        self._read_only_files: frozenset[str] = frozenset()
        self._backup_file: str | None = None
        """Which file the backup named on the tab is of, so reading that same
        file again keeps it (T190) and opening another drops it."""
        self._backup_name: str | None = None
        """The backup named on the tab; Revert file restores exactly it (`backup_name`)."""
        self._text_file: str | None = None
        """Which file the editor's text was read from, `None` before any."""
        self._loaded_text = ""
        """The text last put in the editor from disk: what any typing started from."""
        self._stale = False
        """Whether a re-read under typing found the file changed since `_loaded_text`,
        or could not read it at all: Save file asks before writing over it."""
        self._kept = ""
        """Why the editor still holds typing a re-read did not replace: `EDITOR_STALE`,
        `EDITOR_UNREAD`, `EDITOR_GONE`, or `""`. Said in `file_note`."""
        self._gone = False
        """Whether the open file's typing outlived the file's place in the list."""
        self._note = ""
        """The note `set_file_text` was handed; `file_note` adds `EDITOR_STALE` to it."""
        self._reverting = False
        """Set while Revert file is handled: the file it puts back replaces the typing."""
        right_box.addWidget(self.file_buttons_area)
        # Which file this is, said rather than left to the checked button
        # (T190): its name, its path, and whether it is the server's own.
        self.file_title = QLabel("", right)
        self.file_title.setWordWrap(True)
        self.file_title.setTextFormat(Qt.TextFormat.RichText)
        right_box.addWidget(self.file_title)
        self.file_note = QLabel("", right)
        self.file_note.setWordWrap(True)
        self.file_note.setStyleSheet(f"color: {COLOR_TEXT_MUTED};")
        right_box.addWidget(self.file_note)
        # Item 16. Under the note and above the box a person types into,
        # because it is about what typing into it will and will not do -- and
        # shown only for a file that really has a shadowed key in it.
        self.shadow_warning = QLabel("", right)
        self.shadow_warning.setWordWrap(True)
        self.shadow_warning.setStyleSheet(f"color: {COLOR_TEXT_WARNING};")
        self.shadow_warning.setVisible(False)
        right_box.addWidget(self.shadow_warning)
        self.editor = QPlainTextEdit(right)
        self.editor.setFont(QFont("monospace"))
        # Four lines at least, in place of the theme's 90px floor for every
        # multi-line box: at 960x640 the file side also holds two lines of file
        # buttons, the file's name and its note, and the height the editor did
        # not need was height the panel was drawn short of (T190).
        lines = 4 * self.editor.fontMetrics().lineSpacing()
        self.editor.setStyleSheet(
            f"background-color: {COLOR_BG_PANEL}; border: 1px solid {COLOR_GOLD_BORDER}; "
            f"color: {COLOR_TEXT_PRIMARY}; min-height: {lines}px;"
        )
        self.editor.textChanged.connect(self._relint)
        self._editor_dirty = False
        """Typing in the editor that no `set_file_text` has replaced since.

        `set_file_text` puts its text in with the signals blocked, so only a
        player's typing sets it. A window going narrow with this set shows the
        editor, so the typing is not hidden behind the Settings side."""
        self.editor.textChanged.connect(self._typed)
        right_box.addWidget(self.editor, 1)
        self.lint_label = QLabel("", right)
        self.lint_label.setWordWrap(True)
        self.lint_label.setStyleSheet(f"color: {COLOR_TEXT_WARNING};")
        # The backup's name, said rather than implied (T44 item 15). It is the
        # only record of what the file said before, and the one thing a user
        # needs in order to look at it by hand.
        self.backup_label = QLabel("", right)
        self.backup_label.setWordWrap(True)
        self.backup_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED};")
        # Shown only while it names a backup: an empty line still cost the
        # file side its height at 960x640 (T190).
        self.backup_label.setVisible(False)
        right_box.addWidget(self.backup_label)
        # No Reload here (T190 A28): the tab's bar has the one, and it re-reads
        # this file too (`set_files`). `file_reload_pressed` stays for the view.
        # The guard's verdict on the row of the two presses it is about, not
        # on a line of its own above them (T190, the height at 960x640).
        file_actions = QHBoxLayout()
        file_actions.addWidget(self.lint_label, 1)
        # Dead until there is a backup to restore FROM. A Revert with nothing
        # behind it is a press that can only explain itself, and the card's own
        # Revert already answers that case with a sentence.
        self.file_revert_button = QPushButton("Revert file", right)
        self.file_revert_button.setToolTip(
            "Put this file back from the backup Yu'lon took at the last save on this tab."
        )
        self.file_revert_button.setEnabled(False)
        self.file_revert_button.clicked.connect(self._revert_file_pressed)
        file_actions.addWidget(self.file_revert_button)
        self.file_save_button = QPushButton("Save file", right)
        self.file_save_button.clicked.connect(lambda: self._save_file_pressed())
        file_actions.addWidget(self.file_save_button)
        right_box.addLayout(file_actions)
        self._file_area.setWidget(right)
        self.split.addWidget(self._file_side)
        outer.addWidget(self.split, 1)
        # Whether the note over the cards is on screen changes as the column
        # scrolls, resizes or lays out, and the editor's note follows it.
        for watched in (self._content, self.rule_note, self._area.viewport()):
            watched.installEventFilter(self)
        self._area.verticalScrollBar().valueChanged.connect(self._draw_file_note)

    # ------------------------------------------------------------ the cards

    def set_cards(self, cards: Sequence[TuningCard], *, keep_edits: bool = True) -> None:
        """Draw one card per module, keeping what was typed on them unless `keep_edits` is off.

        Kept by `(family, module, key)` (T190 final review): every save and
        every Reload draws all the cards again, and one card's Save took every
        other card's typing with it. The card whose own Save or Revert caused
        this redraw shows the file, and "Revert all changes" passes
        `keep_edits=False`, which is the one press meant to drop them.
        """
        typed: dict[tuple[str, str, str], bool | int | str | None] = {}
        if keep_edits:
            for (family, module_id), widget in self._cards.items():
                if (family, module_id) == self._pressed_card or (
                    (family, module_id) in self._owed_cards
                ):
                    continue
                for key, editor in widget.editors.items():
                    if editor.changed:
                        typed[(family, module_id, key)] = editor.control_state()
        self._owed_cards.clear()
        for widget in self._cards.values():
            widget.setParent(None)
            widget.deleteLater()
        self._cards.clear()
        self._order.clear()
        for card in cards:
            widget = CardWidget(card, self._content)
            for key, editor in widget.editors.items():
                if (card.family, card.module_id, key) in typed:
                    editor.set_control_state(typed[(card.family, card.module_id, key)])
            widget.save_pressed.connect(
                lambda family, module_id: self._card_pressed(self.save_pressed, family, module_id)
            )
            widget.revert_pressed.connect(
                lambda family, module_id: self._card_pressed(self.revert_pressed, family, module_id)
            )
            widget.edited.connect(self.edited.emit)
            widget.set_enabled_actions(self._actions_enabled)
            self._cards[(card.family, card.module_id)] = widget
            self._order.append((card.family, card.module_id))
            self._content_layout.insertWidget(self._content_layout.count() - 1, widget)
        self.empty_label.setVisible(not self._cards)
        lifted = lifted_rule(cards)
        self.rule_note.setText(tuning.owed_sentence(lifted) if lifted else "")
        self.rule_note.setVisible(lifted is not None)
        for widget in self._cards.values():
            widget.set_rule_lifted(widget.card.rules == lifted)
        self._draw_file_note()

    def _card_pressed(self, signal: SignalInstance, family: str, module_id: str) -> None:
        """Hand a card's Save or Revert up, naming the card for the redraw it causes."""
        self._pressed_card = (family, module_id)
        try:
            signal.emit(family, module_id)
        finally:
            self._pressed_card = None

    def pressed_card(self) -> tuple[str, str] | None:
        """The card whose Save or Revert is being handled now, or None (T622).

        For a view that finishes the press later, on a job: it keeps the card and gives it back
        to `owe_redraw` if the press ends in a redraw.
        """
        return self._pressed_card

    def owe_redraw(self, card: tuple[str, str] | None) -> None:
        """`card`'s press has ended in a redraw that is about to come: show it the file (T622).

        Called only when the press changed the file. A press that changed nothing (refused,
        failed) must not call it, or the card's typing is dropped by the next redraw.
        """
        if card is not None:
            self._owed_cards.add(card)

    def defer_pressed_card(self) -> None:
        """The redraw a card's Save or Revert caused is put off; it still shows that card's file.

        The view calls this when it keeps its reload for later (WSL's distro
        not running yet, `_waits_for_the_distro`): by the time the reload runs
        the press is over, and the card's typing was carried across as if no
        press had been made -- a Revert that looked like it did nothing (T190
        fix round 2). Kept until the next `set_cards`, so a second put-off
        reload replacing the first cannot drop it.
        """
        if self._pressed_card is not None:
            self._owed_cards.add(self._pressed_card)

    def set_header(self, widget: QWidget) -> None:
        """Put `widget` above the cards, in the same scrolling column (T171).

        For a server setting that is not a module's -- the time zone -- and
        that the view builds: here it costs the tab no height of its own, where
        a row above the panel took the 44px the Tuning tab does not have at
        960x640 (the panel's own minimum is 360 there, of a 466px tab). Kept
        through every `set_cards()`, which inserts the cards after it.
        """
        widget.setParent(self._content)
        self._content_layout.insertWidget(0, widget)
        # The header is a floor for the column under it (T190): asked at every
        # resize (`_cards_min`), because its width follows the font, and the
        # app restyles the font with the window's width.
        self._header = widget

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
        writable = not self.editor.isReadOnly() and not self._gone
        self.file_save_button.setEnabled(enabled and writable)
        self.file_revert_button.setEnabled(enabled and bool(self.backup_label.text()) and writable)

    # ------------------------------------------------------------- the file

    def set_files(self, files: Sequence[str], *, read_only: Sequence[str] = ()) -> None:
        """Draw one button per file, keeping the file already open if it is still listed.

        `read_only` is handed IN rather than decided here: which files this
        app will not write is `reset_defaults.read_only_confs()`, a decision
        about who owns core configuration, and this widget must not carry a
        second copy of that list.
        """
        keep = self._current_file
        self._read_only_files = frozenset(read_only)
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
            button.clicked.connect(lambda _checked=False, name=file: self._file_clicked(name))
            self._file_buttons.append(button)
            self.files.flow().addWidget(button)
        self.file_buttons_area.refit()
        if keep in files:
            # Read again (T190 A28): the tab's Reload and every save hand the
            # same list back, and an editor kept as it was went stale under a
            # card's save -- a later Save file then wrote the old value back.
            # Under typing too: `set_file_text` keeps the typing and says when
            # the file changed (`EDITOR_STALE`).
            self._file_picked(keep)
        elif keep and self._editor_dirty:
            # The open file is no longer offered, and the player typed into
            # it: the typing stays on screen and nothing switches until they
            # pick another file (which asks first). It cannot be saved here.
            self._mark_current(keep)
            self._gone = True
            self._kept = EDITOR_GONE
            self.file_save_button.setEnabled(False)
            self.file_revert_button.setEnabled(False)
            self._draw_file_note()
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
        """Name the backup the last save took, and arm Revert (T44 item 15).

        Only a save that wrote the open file names one, so a name here also
        says the editor's text is on disk: the editor counts as clean again,
        and the next reload reads it in (T190).
        """
        self._backup_file = self._current_file if name else None
        self._backup_name = name or None
        if name:
            self._editor_dirty = False
        self.backup_label.setText(f"Backup of this file as it was: {name}" if name else "")
        self.backup_label.setVisible(bool(name))
        self.file_revert_button.setEnabled(
            self._actions_enabled and bool(name) and not self.editor.isReadOnly()
        )

    def backup_name(self) -> str | None:
        """The backup the tab names for the open file, `None` when it names none.

        Revert file restores exactly this one (T190): a card's Save on the open
        file takes a newer backup, and Revert restoring the newest put back a
        file the label never named.
        """
        return self._backup_name

    def _mark_current(self, file: str) -> None:
        self._current_file = file
        for button in self._file_buttons:
            button.setChecked(button.toolTip() == file)
        self._draw_file_title()
        # The open file's button may be on a line the area has scrolled past
        # (T569), and a Reload or a card's save redraws every button. After the
        # layout pass: a button just made has no place until then.
        QTimer.singleShot(0, self, self._show_current_button)

    def _show_current_button(self) -> None:
        """Scroll the file buttons so the checked one shows."""
        for button in self._file_buttons:
            if button.isChecked():
                self.file_buttons_area.ensureWidgetVisible(button, 0, 0)
                return

    def _draw_file_title(self) -> None:
        """The open file's name in bold, its path muted, and whether it is the server's own."""
        file = self._current_file
        if not file:
            self.file_title.setText("")
            return
        name = html.escape(file.rsplit("/", 1)[-1], quote=False)
        path = html.escape(file, quote=False)
        said = (
            f" · {html.escape(FILE_READ_ONLY, quote=False)}"
            if file in self._read_only_files
            else ""
        )
        self.file_title.setText(
            f'<span style="color: {COLOR_TEXT_PRIMARY}; font-weight: bold;">{name}</span> '
            f'<span style="color: {COLOR_TEXT_MUTED};">{path}{said}</span>'
        )

    def set_file_text(
        self,
        text: str,
        *,
        read_only: bool,
        note: str | None,
        shadowed: Sequence[tuple[str, str]] = (),
    ) -> None:
        """Show one file. `shadowed` is the `(key, AC_* row)` pairs the environment beats.

        Handed in rather than computed: which rows this install's compose
        carries is `composegen`'s answer and the view's to fetch, and a widget
        that imported the compose generator to find out would be a second place
        for that question to be asked.
        """
        same = self._text_file == self._current_file
        if same and self._editor_dirty and not self._reverting:
            # The same file read again under typing (a card's Save, Revert or
            # Reset, or Reload): the typing stays, and the editor says when the
            # file it started from is no longer what is on disk (T190).
            self._gone = False
            if read_only:
                # A read that failed (the view hands its error as the note): the
                # file was writable when the typing began, and the typing is
                # not blanked with the read (T190 fix round 2).
                read_only = self.editor.isReadOnly()
                self._stale = True
                self._kept = EDITOR_UNREAD
            else:
                self._stale = text != self._loaded_text
                self._kept = EDITOR_STALE if self._stale else ""
        else:
            self._put_text(text, keep_place=same)
        self._text_file = self._current_file
        self.editor.setReadOnly(read_only)
        self._note = note or ""
        self._draw_file_note()
        # Only where a key really IS shadowed, and only on a file this tab will
        # write: nothing can be typed into a read-only one, so no edit of it
        # can silently fail to apply (item 16, round 2).
        self.shadow_warning.setText(shadow_warning(shadowed) if shadowed else "")
        self.shadow_warning.setVisible(bool(shadowed) and not read_only)
        self.file_save_button.setEnabled(self._actions_enabled and not read_only)
        # A backup of the file you were looking at a moment ago is not a
        # backup of this one, so the name goes with the text it described --
        # but the same file read again keeps it (T190): every save re-reads.
        if self._current_file != self._backup_file:
            self.set_backup(None)
        else:
            self.file_revert_button.setEnabled(
                self._actions_enabled and bool(self.backup_label.text()) and not read_only
            )
        self._draw_file_title()
        self._relint()

    def _put_text(self, text: str, *, keep_place: bool) -> None:
        """Replace the editor's text with the file's, where the player was if it is the same file.

        The scroll and the cursor are kept on a re-read of the same file
        (T190): every save and Reload re-reads it, and it jumped to line one.
        """
        bar = self.editor.verticalScrollBar()
        across = self.editor.horizontalScrollBar()
        place = (bar.value(), across.value(), self.editor.textCursor().position())
        blocked = self.editor.blockSignals(True)
        self.editor.setPlainText(text)
        self.editor.blockSignals(blocked)
        self._editor_dirty = False
        self._loaded_text = text
        self._stale = False
        self._kept = ""
        self._gone = False
        if keep_place:
            cursor = self.editor.textCursor()
            cursor.setPosition(min(place[2], len(text)))
            self.editor.setTextCursor(cursor)
            bar.setValue(place[0])
            across.setValue(place[1])

    def _file_picked(self, name: str) -> None:
        self._mark_current(name)
        if name:
            self.file_selected.emit(name)

    def _file_clicked(self, name: str) -> None:
        """A file button's press: ask first when the editor holds typing (T190 final review).

        Save hands the typing up and opens `name` only once it was saved;
        Discard opens it; Cancel, or no answer, leaves the editor as it was.
        """
        if self._editor_dirty:
            # No Save for a file the list no longer offers: it cannot be written here.
            answers = (CHOICE_DISCARD, CHOICE_CANCEL) if self._gone else UNSAVED_ANSWERS
            asked = UNSAVED_GONE_QUESTION if self._gone else UNSAVED_QUESTION
            answer = self.choose(
                UNSAVED_TITLE,
                asked.format(
                    name=self._current_file.rsplit("/", 1)[-1], other=name.rsplit("/", 1)[-1]
                ),
                answers,
            )
            if answer == CHOICE_DISCARD:
                self._editor_dirty = False
            elif answer != CHOICE_SAVE or not self._save_file_pressed():
                # The click checked its button; the open file's is the checked one.
                self._mark_current(self._current_file)
                return
        self._file_picked(name)

    def _save_file_pressed(self) -> bool:
        """Save file's press: hand the text up, asking first over a file changed on disk.

        True when the view saved it, which it says by naming a backup
        (`set_backup` counts the editor clean again).
        """
        if self._stale and self._editor_dirty:
            answer = self.choose(
                STALE_TITLE,
                STALE_QUESTION.format(name=self._current_file.rsplit("/", 1)[-1]),
                (CHOICE_OVERWRITE, CHOICE_RELOAD, CHOICE_CANCEL),
            )
            if answer == CHOICE_RELOAD:
                self._editor_dirty = False
                self._file_picked(self._current_file)
                return False
            if answer != CHOICE_OVERWRITE:
                return False
        self.file_save_pressed.emit(self.editor.toPlainText())
        return not self._editor_dirty

    def _revert_file_pressed(self) -> None:
        """Revert file's press: the file it puts back replaces any typing, as asked."""
        self._reverting = True
        try:
            self.file_revert_pressed.emit()
        finally:
            self._reverting = False

    def _ask(self, title: str, question: str, choices: tuple[str, ...]) -> str | None:
        """`choose` in the app: a message box with one button per answer; Cancel is Escape."""
        # Fitted (T157): a plain box squeezes "Reload instead" below its label.
        box = FittedMessageBox(QMessageBox.Icon.Question, title, question, parent=self)
        buttons: dict[QPushButton, str] = {}
        for answer in choices:
            role = (
                QMessageBox.ButtonRole.RejectRole
                if answer == CHOICE_CANCEL
                else QMessageBox.ButtonRole.AcceptRole
            )
            buttons[box.addButton(answer, role)] = answer
        cancel = next((b for b, a in buttons.items() if a == CHOICE_CANCEL), None)
        if cancel is not None:
            box.setEscapeButton(cancel)
            box.setDefaultButton(cancel)
        box.exec()
        pressed = box.clickedButton()
        return buttons.get(pressed) if isinstance(pressed, QPushButton) else None

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

    def _cards_min(self) -> int:
        """The card column's floor: `CARDS_MIN_WIDTH`, or the header plus the column's chrome.

        The chrome is the column's own -- its frame, its scroll bar, the
        margins of the box the cards sit in -- read off the widgets, not typed.
        """
        if self._header is None:
            return CARDS_MIN_WIDTH
        margins = self._content_layout.contentsMargins()
        chrome = (
            2 * self._area.frameWidth()
            + self._area.verticalScrollBar().sizeHint().width()
            + margins.left()
            + margins.right()
        )
        return max(CARDS_MIN_WIDTH, self._header.sizeHint().width() + chrome)

    def _draw_file_note(self) -> None:
        """The editor's note, unless the note over the cards says the same and shows.

        Side by side both said the restart sentence (T190 B15). Only while the
        cards' note is on screen, though: at 960x640 on the Edit file side the
        cards are hidden, and the file being edited still needs its sentence.
        """
        said = self._note
        # On screen, not merely shown: scrolled out of the column's view it says
        # nothing to anybody (T190 final review).
        echoed = said == self.rule_note.text() and not self.rule_note.visibleRegion().isEmpty()
        if self._kept:
            said = self._kept if echoed or not said else f"{said}\n{self._kept}"
            echoed = False
        self.file_note.setText(said)
        self.file_note.setVisible(bool(said) and not echoed)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802  (Qt's own name)
        if event.type() in (
            QEvent.Type.Move,
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.Hide,
        ):
            self._draw_file_note()
        return False

    def _side_picked(self, chosen: QPushButton) -> None:
        """One side checked, always: pressing the checked one again keeps it."""
        self.settings_button.setChecked(chosen is self.settings_button)
        self.edit_file_button.setChecked(chosen is self.edit_file_button)
        self._show_sides()

    def _typed(self) -> None:
        self._editor_dirty = True

    def _show_sides(self) -> None:
        """Both halves when wide; when narrow, the one the switch names."""
        narrow = bool(self._narrow)
        editing = self.edit_file_button.isChecked()
        self.side_buttons.setVisible(narrow)
        self._area.setVisible(not narrow or not editing)
        self._file_side.setVisible(not narrow or editing)
        self._draw_file_note()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802  (Qt's own name)
        """Side by side while there is room, one half at a time when there is not (T190).

        On every switch to side by side -- and the first layout -- the split
        is set to `split_sizes()`; between switches a drag of the handle is the
        user's and is kept.
        """
        cards_min = self._cards_min()
        self._area.setMinimumWidth(cards_min)
        narrow = is_narrow(self.width(), cards_min)
        if narrow != self._narrow:
            self._narrow = narrow
            if narrow and self._editor_dirty:
                self.settings_button.setChecked(False)
                self.edit_file_button.setChecked(True)
            self._show_sides()
            if not narrow:
                total = self.width() - self.split.handleWidth()
                self.split.setSizes(list(split_sizes(total, cards_min)))
        super().resizeEvent(event)


__all__ = [
    "CARDS_MAX_WIDTH",
    "CARDS_MIN_WIDTH",
    "CHANGED_FROM",
    "CardWidget",
    "EDITOR_MIN_WIDTH",
    "VALUE_MIN_CHARS",
    "NOTHING",
    "NOTHING_TO_TUNE",
    "NOT_IN_THE_FILE",
    "RowEditor",
    "TuningCard",
    "TuningPanel",
    "bool_words",
    "build_tuning_cards",
    "card_heading",
    "control_kind",
    "is_narrow",
    "lifted_rule",
    "shows_key_line",
    "split_sizes",
    "starting_value",
    "value_note",
]
