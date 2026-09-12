"""The Modules tab's surface: what this install HAS, then what it could have (T42).

Two things live here, and the split is the point.

`build_module_rows()` is a pure function: manifests in, `ModuleRow`s out, no Qt
anywhere near it. Everything the tab decides -- which family a row belongs to,
whether it is installed, whether it owes a rebuild, whether removing it would
break something else -- is decided there and is testable without a
`QApplication`. `ModulesPanel` draws what it is given and presses buttons; it
decides nothing.

The shape it draws is the DML launcher's `ModuleManager.svelte` in Yu'lon's own
theme (T42's mockup, approved by the owner 2026-09-12): a card per family, the
installed half first with a count, the rest behind an "Available N -- not
installed" toggle that starts collapsed when the family has anything installed
and open when it has none.

T41 is what made any of this possible and its accounting is MOVED here rather
than copied: `apply.CLONE_DIRS` gives ale and keg one directory, so a keg's
clone is read into both families' sets, and a name is accounted for per clone
FOLDER. Reading it per family listed `bmah` twice on the owner's own install
(measured 2026-09-12).

Nothing in this module imports `yulon.ui.controller_view`, and nothing imports
the decorations modules: upstream `Yulon` carries Baerthe's passes on those and
`dadcraft_decorations.py` there (T42; upstream has since renamed it from `warcraft_`). Colours come from the
`COLOR_*` constants `theme.py` exports.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QPoint, Qt, Signal, Slot
from PySide6.QtGui import QFont, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from yulon import apply as apply_module
from yulon.manifest import Manifest, ManifestType
from yulon.manifest_store import FAMILY_FILES
from yulon.ui.theme import (
    COLOR_BG_PANEL,
    COLOR_BG_PARCHMENT_LIGHT,
    COLOR_BRASS_DARK,
    COLOR_GOLD_BORDER,
    COLOR_TEXT_GOLD,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_WARNING,
    COLOR_UNCOMMON,
)

FAMILY_TITLES: dict[ManifestType, str] = {
    "module": "C++ modules",
    "ale": "ALE Lua scripts",
    "keg": "Kegs",
    "mod": "SQL & config mods",
}
"""What each family's card is called, beside the `FAMILY_FILES` it is keyed by.

Here and not in `manifest_store.py` because these are LABELS: `FAMILY_FILES`
maps a family to a filename on disk and is read by the store, the fetcher and
the catalog tests, none of which should grow a dependency on how a tab titles a
card. The two are kept in step by `test_every_family_has_a_title`.

The order rows are drawn in is `FAMILY_FILES`'s, not this mapping's -- the store
owns the order, and a second ordering here would be a second place for it to
drift.
"""

NOT_IN_CATALOG = "installed here — not in this game's catalog"
"""The description of a clone this game's catalog has never heard of (T41).

It is still installed: the server compiles it and the importer is handed its
SQL. What Yu'lon does not have is a manifest, so it has no steps to install or
remove it -- which is why such a row gets no buttons and says so when the
context menu is pressed on it (`controller_view.UNCATALOGUED_PRESS`).
"""

BADGE_INSTALLED = "Installed"
BADGE_NOT_INSTALLED = "Not installed"

CHIP_REBUILD_PENDING = "Rebuild pending"
CHIP_SQL_PENDING = "SQL pending"
CHIP_ASKS_A_QUESTION = "asks a question"
CHIP_NEEDS_CLIENT_FOLDER = "needs the client folder"

ChipKind = Literal["owed", "fact"]


def chip_update_label(behind: int) -> str:
    """The update chip's own label, so the view and the tests cannot spell it apart."""
    plural = "" if behind == 1 else "s"
    return f"Update available — {behind} commit{plural} behind"


def chip_required_by_label(names: Sequence[str]) -> str:
    """The lock chip's label: who needs this, by NAME rather than by id.

    The id is what the machine matched on (`Manifest.requires` holds ids); the
    name is what the person reading the row installed.
    """
    return f"required by {', '.join(names)}"


@dataclass(frozen=True)
class Chip:
    """One small thing a row has to say, and the sentence behind it.

    Two kinds, because they are answered differently. An `owed` chip is a job
    somebody still has to run -- a rebuild, the importer, an update -- and
    pressing it writes `detail` into the report, which is where this tab puts
    every other answer. A `fact` chip is a property of the row that no press can
    change; it carries `detail` as a tooltip and does nothing.
    """

    kind: ChipKind
    label: str
    detail: str


@dataclass(frozen=True)
class ModuleRow:
    """One row of the Modules tab: everything drawn, decided before any widget exists.

    `catalogued` is not the same question as `installed`, and collapsing them is
    the defect T41 found from the other side: a clone with no manifest is
    installed and has no steps, a manifest with no clone has steps and is not
    installed, and the tab has to draw both.
    """

    id: str
    family: str
    name: str
    description: str
    url: str | None
    installed: bool
    catalogued: bool
    paths: tuple[str, ...]
    chips: tuple[Chip, ...]
    removable: bool
    remove_reason: str | None


@dataclass(frozen=True)
class SessionState:
    """What this session has learned since it started, and deliberately never persists.

    Three facts the manifest store cannot answer and the disk does not record:
    a rebuild is owed because an install said so, SQL is waiting because a
    report listed it, an upstream is ahead because a fetch counted it. None of
    them survives a restart, and that is the honest state rather than a gap --
    the report box has always forgotten them on restart too, and a persisted
    "rebuild pending" marker is a file with its own invalidation rules that
    T42 deliberately does not open (see the ticket's "Not in scope").
    """

    rebuild_owed: frozenset[str] = frozenset()
    sql_owed: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    behind: Mapping[str, int] = field(default_factory=dict)


def _clone_dir_of(kind: str) -> str:
    """Which directory this manifest family's clones land in, by its plain name.

    `apply.CLONE_DIRS` is keyed by the `ManifestType` literal; families are
    carried around as plain strings (they arrive from `FAMILY_FILES` and from
    `manifest.type`). Looked up defensively rather than cast: a family with no
    clone directory gets its own bucket under its own name, which keeps its rows
    separate instead of merging them into somebody else's folder.
    """
    for family, folder in apply_module.CLONE_DIRS.items():
        if family == kind:
            return folder
    return kind


def _chips_for(
    manifest: Manifest | None,
    item_id: str,
    installed: bool,
    session: SessionState,
    client_dir: Path | None,
    dependants: Sequence[str],
) -> tuple[Chip, ...]:
    """The six chips a row may carry, and nothing beyond them.

    Owed first and facts after, because the owed ones name work somebody has to
    do and the facts only explain the row. Six and not seven: an "Update" chip
    that could be PRESSED to pull is not here because no per-module pull exists
    below this tab -- `apply.module_updates()` counts and nothing else -- and a
    button that reports a number is not the same control as a button that
    fetches.
    """
    chips: list[Chip] = []
    if item_id in session.rebuild_owed:
        chips.append(
            Chip(
                "owed",
                CHIP_REBUILD_PENDING,
                f"{item_id}: the worldserver has not been compiled since this changed, so it "
                "is not in the running server yet. Press Rebuild server… on this tab.",
            )
        )
    owed_sql = session.sql_owed.get(item_id)
    if owed_sql:
        chips.append(
            Chip(
                "owed",
                CHIP_SQL_PENDING,
                f"{item_id}: this SQL is on disk and has NOT been applied — "
                + ", ".join(owed_sql)
                + ". Press Apply module SQL on this tab.",
            )
        )
    behind = session.behind.get(item_id, 0)
    if behind > 0:
        chips.append(
            Chip(
                "owed",
                chip_update_label(behind),
                f"{item_id}: its upstream has {behind} commit(s) this checkout does not. "
                "Yu'lon has no per-module pull yet, so updating it is a job for git in "
                "the clone folder.",
            )
        )
    if manifest is not None and not installed:
        asked = [
            prompt
            for prompt in apply_module.required_prompts(manifest, "install")
            if prompt.default is None
        ]
        if asked:
            chips.append(
                Chip(
                    "fact",
                    CHIP_ASKS_A_QUESTION,
                    "Installing this opens one dialog first: "
                    + ", ".join(prompt.question for prompt in asked),
                )
            )
        if manifest.client and client_dir is None:
            chips.append(
                Chip(
                    "fact",
                    CHIP_NEEDS_CLIENT_FOLDER,
                    "This copies files into YOUR game client, and no client folder is "
                    "recorded for this install. Set one on the Server tab first.",
                )
            )
    if installed and dependants:
        chips.append(
            Chip(
                "fact",
                chip_required_by_label(dependants),
                f"{', '.join(dependants)} — installed here — name this in `requires`, so "
                "removing it would break them. Remove them first.",
            )
        )
    return tuple(chips)


def build_module_rows(
    manifests: Iterable[Manifest],
    installed: Mapping[str, frozenset[str]],
    session: SessionState,
    client_dir: Path | None,
) -> tuple[ModuleRow, ...]:
    """Every row the Modules tab draws, in the order it draws them.

    `manifests` is this game's catalog in ITS own order (the store's, family by
    family); `installed` is `apply.installed_clones()`'s answer, ids per family
    read from each family's own clone directory.

    The order is: family by family in `FAMILY_FILES` order, and inside a family
    the installed rows first in catalog order, then the rest in catalog order,
    then the clones no manifest matched. "Installed first" is the whole of T42's
    first line -- a user who adopted a server he already ran reads the top of
    each card and sees what he has.
    """
    catalog: list[Manifest] = list(manifests)
    installed_ids: dict[str, Manifest] = {}
    for manifest in catalog:
        if manifest.id in installed.get(manifest.type, frozenset()):
            installed_ids[manifest.id] = manifest
    # Who needs what, counted from what is INSTALLED and not from the catalog:
    # a manifest nobody has installed requires nothing of anybody, and letting
    # it lock a row would make half the catalog unremovable on a fresh install.
    dependants: dict[str, list[str]] = {}
    for manifest in installed_ids.values():
        for needed in manifest.requires:
            dependants.setdefault(needed, []).append(manifest.name)

    def _row(manifest: Manifest) -> ModuleRow:
        here = manifest.id in installed_ids
        needed_by = dependants.get(manifest.id, [])
        return ModuleRow(
            id=manifest.id,
            family=manifest.type,
            name=manifest.name,
            description=manifest.description,
            url=manifest.source.url if manifest.source is not None else None,
            installed=here,
            catalogued=True,
            paths=tuple(conf.file for conf in manifest.conf),
            chips=_chips_for(manifest, manifest.id, here, session, client_dir, needed_by),
            removable=not (here and needed_by),
            remove_reason=(
                f"{', '.join(needed_by)} require this — remove them first."
                if here and needed_by
                else None
            ),
        )

    # T41's per-FOLDER accounting, moved here from `reload_modules()`. `ale` and
    # `keg` share `ale_scripts/`, so a keg's clone arrives in both sets; a name
    # any manifest of the SAME FOLDER claims is accounted for, and a name this
    # loop turns into a row is accounted for too, so one clone is one row.
    accounted: dict[str, set[str]] = {}
    for manifest in catalog:
        accounted.setdefault(_clone_dir_of(manifest.type), set()).add(manifest.id)

    rows: list[ModuleRow] = []
    for kind in FAMILY_FILES:
        family = [m for m in catalog if m.type == kind]
        rows += [_row(m) for m in family if m.id in installed_ids]
        rows += [_row(m) for m in family if m.id not in installed_ids]
        known = accounted.setdefault(_clone_dir_of(kind), set())
        for name in sorted(installed.get(kind, frozenset())):
            if name in known:
                continue
            known.add(name)
            rows.append(
                ModuleRow(
                    id=name,
                    family=kind,
                    name=name,
                    description=NOT_IN_CATALOG,
                    url=None,
                    installed=True,
                    catalogued=False,
                    paths=(),
                    chips=_chips_for(
                        None, name, True, session, client_dir, dependants.get(name, [])
                    ),
                    removable=False,
                    remove_reason=None,
                )
            )
    return tuple(rows)


NO_MODULES_NOTE = "(this game has no manifests yet)"
"""What the panel says when there is nothing at all to draw.

The three CMaNGOS games have no manifest store, and a tab that answers them with
an empty box looks broken in exactly the way T41 was reported for. Carried by
the panel rather than by the view so the same sentence covers "no store" and "a
store with nothing in it" -- two states the user has no way to tell apart and no
different action for.
"""

BUTTON_COLUMN_WIDTH = 110
"""The shared width of every row's action column, so the buttons line up.

A fixed width and not a layout-derived one: `Install` and `Remove` are different
lengths, and a column that sized itself per row put the presses on a ragged edge
down the card.
"""


class RowWidget(QFrame):
    """One module's row: what it is, what it owes, and the one press it offers.

    It holds the `ModuleRow` it was built from (`.data`), because the view needs
    the chip DETAIL behind a press and the row is the only place that pairs a
    label with it.

    `install_button` and `remove_button` are `None` where the row does not offer
    that press, rather than present-and-hidden: an installed module has nothing
    to install, an uninstalled one has nothing to remove, and a clone with no
    manifest has neither because Yu'lon has no steps for it (T41).
    """

    pressed_install = Signal(str)
    pressed_remove = Signal(str)
    pressed_chip = Signal(str, str)
    clicked = Signal(str)
    menu_requested = Signal(str, QPoint)

    def __init__(self, data: ModuleRow, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.data = data
        self.setObjectName("moduleRow")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu_at)

        box = QHBoxLayout(self)
        box.setContentsMargins(10, 8, 10, 8)
        box.setSpacing(10)

        left = QVBoxLayout()
        left.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self.name_label = QLabel(data.name, self)
        self.name_label.setStyleSheet(f"color: {COLOR_TEXT_GOLD}; font-weight: bold;")
        title_row.addWidget(self.name_label)
        if data.url is not None:
            # `Manifest.source.url` already resolves a slug to GitHub, so the
            # link is the seam's answer and not a second spelling of it.
            self.link_label: QLabel | None = QLabel(f'<a href="{data.url}">GitHub</a>', self)
            self.link_label.setOpenExternalLinks(True)
            self.link_label.setToolTip(data.url)
            title_row.addWidget(self.link_label)
        else:
            self.link_label = None
        title_row.addStretch(1)
        left.addLayout(title_row)

        self.description_label = QLabel(data.description, self)
        self.description_label.setWordWrap(True)
        self.description_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED};")
        left.addWidget(self.description_label)

        if data.paths:
            self.paths_label: QLabel | None = QLabel("\n".join(data.paths), self)
            self.paths_label.setFont(QFont("monospace"))
            self.paths_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED};")
            left.addWidget(self.paths_label)
        else:
            self.paths_label = None
        box.addLayout(left, 1)

        middle = QVBoxLayout()
        middle.setSpacing(4)
        self.badge_label = QLabel(BADGE_INSTALLED if data.installed else BADGE_NOT_INSTALLED, self)
        self.badge_label.setStyleSheet(
            f"color: {COLOR_UNCOMMON if data.installed else COLOR_TEXT_MUTED}; font-weight: bold;"
        )
        middle.addWidget(self.badge_label)
        chips = QHBoxLayout()
        chips.setSpacing(4)
        buttons: list[QPushButton] = []
        for chip in data.chips:
            button = QPushButton(chip.label, self)
            button.setFlat(True)
            button.setToolTip(chip.detail)
            colour = COLOR_TEXT_WARNING if chip.kind == "owed" else COLOR_TEXT_MUTED
            button.setStyleSheet(
                f"color: {colour}; border: 1px solid {COLOR_BRASS_DARK}; padding: 1px 6px;"
            )
            if chip.kind == "owed":
                # Only an owed chip is a press: a fact chip names something no
                # press can change, and wiring it would put a sentence in the
                # report that answers nothing.
                button.clicked.connect(
                    lambda _checked=False, label=chip.label: self.pressed_chip.emit(
                        self.data.id, label
                    )
                )
            buttons.append(button)
            chips.addWidget(button)
        chips.addStretch(1)
        self.chip_buttons = tuple(buttons)
        middle.addLayout(chips)
        middle.addStretch(1)
        box.addLayout(middle, 1)

        self.install_button: QPushButton | None = None
        self.remove_button: QPushButton | None = None
        column = QVBoxLayout()
        if data.catalogued and not data.installed:
            self.install_button = QPushButton("Install", self)
            self.install_button.clicked.connect(lambda: self.pressed_install.emit(self.data.id))
            column.addWidget(self.install_button)
        elif data.catalogued:
            self.remove_button = QPushButton("Remove", self)
            self.remove_button.clicked.connect(lambda: self.pressed_remove.emit(self.data.id))
            if not data.removable:
                self.remove_button.setToolTip(data.remove_reason or "")
            column.addWidget(self.remove_button)
        action = self.install_button or self.remove_button
        if action is not None:
            action.setFixedWidth(BUTTON_COLUMN_WIDTH)
        column.addStretch(1)
        holder = QWidget(self)
        holder.setLayout(column)
        holder.setFixedWidth(BUTTON_COLUMN_WIDTH)
        box.addWidget(holder)
        self.set_enabled_actions(True)
        self.set_selected(False)

    def set_enabled_actions(self, enabled: bool) -> None:
        """Arm or disarm this row's one press.

        `enabled` never overrides the ROW's own answer: a module something else
        installed needs stays unremovable when a job finishes, the same rule
        `_set_busy(False)` follows for every other gated control in the view.
        """
        if self.install_button is not None:
            self.install_button.setEnabled(enabled)
        if self.remove_button is not None:
            self.remove_button.setEnabled(enabled and self.data.removable)

    def set_selected(self, selected: bool) -> None:
        """Highlight, through the theme's own constants (T42 forbids touching `theme.py`)."""
        if selected:
            self.setStyleSheet(
                f"QFrame#moduleRow {{ background-color: {COLOR_BG_PARCHMENT_LIGHT}; "
                f"border-left: 3px solid {COLOR_GOLD_BORDER}; }}"
            )
        else:
            self.setStyleSheet(
                f"QFrame#moduleRow {{ background-color: {COLOR_BG_PANEL}; "
                f"border-left: 3px solid transparent; }}"
            )

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802  (Qt's own name)
        """A click ANYWHERE on the row selects it — the mockup's rule, and T42's.

        Children that ignore the press (the labels) propagate it here, which is
        why the description and the paths are part of the target and not dead
        space.
        """
        self.clicked.emit(self.data.id)
        super().mousePressEvent(event)

    def _menu_at(self, pos: QPoint) -> None:
        self.menu_requested.emit(self.data.id, self.mapToGlobal(pos))


class _FamilyCard(QGroupBox):
    """One family's card: the installed half, then the rest behind a toggle."""

    def __init__(self, family: str, title: str, parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        self.family = family
        box = QVBoxLayout(self)
        box.setSpacing(4)
        self.installed_header = QLabel("Installed (0)", self)
        self.installed_header.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-weight: bold;")
        box.addWidget(self.installed_header)
        self.installed_box = QWidget(self)
        self._installed_layout = QVBoxLayout(self.installed_box)
        self._installed_layout.setContentsMargins(0, 0, 0, 0)
        self._installed_layout.setSpacing(2)
        box.addWidget(self.installed_box)
        self.toggle: QToolButton | None = None
        self.available_box = QWidget(self)
        self._available_layout = QVBoxLayout(self.available_box)
        self._available_layout.setContentsMargins(0, 0, 0, 0)
        self._available_layout.setSpacing(2)
        self._box = box

    def fill(self, installed: Sequence[RowWidget], available: Sequence[RowWidget]) -> None:
        self.installed_header.setText(f"Installed ({len(installed)})")
        self.installed_header.setVisible(bool(installed))
        self.installed_box.setVisible(bool(installed))
        for row in installed:
            self._installed_layout.addWidget(row)
        if available:
            self.toggle = QToolButton(self)
            self.toggle.setCheckable(True)
            self.toggle.setText(f"Available {len(available)} — not installed")
            self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            self.toggle.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; border: none;")
            self._box.addWidget(self.toggle)
            for row in available:
                self._available_layout.addWidget(row)
            self._box.addWidget(self.available_box)
        else:
            self.available_box.setVisible(False)

    def set_open(self, is_open: bool) -> None:
        if self.toggle is not None:
            self.toggle.setChecked(is_open)
            self.toggle.setArrowType(Qt.ArrowType.DownArrow if is_open else Qt.ArrowType.RightArrow)
        self.available_box.setVisible(is_open and self.toggle is not None)


class ModulesPanel(QWidget):
    """The Modules tab's list, as four cards of rows inside one scroll area.

    Call down / signal up: it is handed rows and says which one was pressed. It
    reads no disk, holds no manifest and knows nothing about an applier -- the
    view turns a signal into an action, exactly as it did when this was a
    `QListWidget` and a pair of toolbar buttons.

    It keeps two pieces of state across `set_rows()`, and both are the user's
    rather than the data's: which families they opened, and which row they were
    reading. A reload happens after every install, every remove and every update
    check, and losing either to a reload the user did not ask for is the defect
    this rule exists for.
    """

    install_pressed = Signal(str)
    remove_pressed = Signal(str)
    chip_pressed = Signal(str, str)
    row_selected = Signal(str)
    context_menu_requested = Signal(str, QPoint)
    """The row's id and a GLOBAL position, for the view's own context menu.

    A fifth signal beside T42's four because the ticket keeps
    `_show_module_context_menu` and moves it to take the row's id: the menu it
    builds is about the applier and the clipboard, neither of which this widget
    may know about.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: dict[str, RowWidget] = {}
        self._cards: dict[str, _FamilyCard] = {}
        self._open: dict[str, bool] = {}
        self._selected: str | None = None
        self._actions_enabled = True
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._area = QScrollArea(self)
        self._area.setWidgetResizable(True)
        self._content = QWidget(self._area)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setSpacing(8)
        self.empty_label = QLabel(NO_MODULES_NOTE, self._content)
        self.empty_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED};")
        self._content_layout.addWidget(self.empty_label)
        self._content_layout.addStretch(1)
        self._area.setWidget(self._content)
        outer.addWidget(self._area)

    # ------------------------------------------------------------------ drawing

    def set_rows(self, rows: Sequence[ModuleRow]) -> None:
        """Draw these rows, keeping each family's toggle and the selection.

        Everything else is rebuilt: a row's chips, badge and button all change
        with what the session has learned, and a diff would be a second model of
        the same thing. The cards are cheap -- 41 rows on the largest shipped
        catalog.
        """
        keep = self._selected
        for card in self._cards.values():
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()
        self._rows.clear()
        for kind in FAMILY_FILES:
            family = [row for row in rows if row.family == kind]
            if not family:
                continue
            if kind not in self._open:
                # The rule, applied once per family and never again: a family
                # you have something in opens on what you HAVE.
                self._open[kind] = not any(row.installed for row in family)
            card = _FamilyCard(kind, FAMILY_TITLES[kind], self._content)
            # Built in the order they were HANDED to us and only then split into
            # the two halves, so `rows()` reports the order the tab draws in
            # rather than the order the cards happen to be filled in. Building
            # the installed half first would make `rows()` installed-first
            # whatever the builder decided -- which is a reading that agrees with
            # T42's first line by accident and would go on agreeing with it after
            # the sort was deleted.
            widgets = [self._make(row) for row in family]
            card.fill(
                [w for w in widgets if w.data.installed],
                [w for w in widgets if not w.data.installed],
            )
            if card.toggle is not None:
                card.toggle.clicked.connect(lambda _checked=False, name=kind: self._flip(name))
            card.set_open(self._open[kind])
            self._cards[kind] = card
            self._content_layout.insertWidget(self._content_layout.count() - 1, card)
        self.empty_label.setVisible(not self._rows)
        self._selected = keep if keep in self._rows else None
        for item_id, widget in self._rows.items():
            widget.set_selected(item_id == self._selected)

    def _make(self, data: ModuleRow) -> RowWidget:
        widget = RowWidget(data)
        widget.pressed_install.connect(self.install_pressed.emit)
        widget.pressed_remove.connect(self.remove_pressed.emit)
        widget.pressed_chip.connect(self.chip_pressed.emit)
        widget.clicked.connect(self.select)
        widget.menu_requested.connect(self.context_menu_requested.emit)
        widget.set_enabled_actions(self._actions_enabled)
        self._rows[data.id] = widget
        return widget

    def _flip(self, family: str) -> None:
        self._open[family] = not self._open.get(family, True)
        card = self._cards.get(family)
        if card is not None:
            card.set_open(self._open[family])

    # ------------------------------------------------------------------ reading

    def row(self, item_id: str) -> RowWidget:
        """The widget for this id. Raises rather than answering `None`: every caller
        of this asks about a row it has just been told exists."""
        return self._rows[item_id]

    def rows(self) -> tuple[RowWidget, ...]:
        """Every row, in the order `set_rows()` was handed them (= the drawn order)."""
        return tuple(self._rows.values())

    def selected_id(self) -> str | None:
        return self._selected

    def available_open(self, family: str) -> bool:
        """Whether this family's "not installed" half is showing."""
        return self._open.get(family, True)

    def available_toggle(self, family: str) -> QToolButton | None:
        """The toggle, or `None` for a family with nothing left to install."""
        card = self._cards.get(family)
        return None if card is None else card.toggle

    def installed_header(self, family: str) -> QLabel | None:
        card = self._cards.get(family)
        return None if card is None else card.installed_header

    # ------------------------------------------------------------------ acting

    @Slot(str)
    def select(self, item_id: str) -> None:
        """Select a row and say so. Unknown ids are ignored, not raised."""
        if item_id not in self._rows:
            return
        self._selected = item_id
        for known, widget in self._rows.items():
            widget.set_selected(known == item_id)
        self.row_selected.emit(item_id)

    def set_enabled_actions(self, enabled: bool) -> None:
        """Arm or disarm every row's press (the busy lock, and the applier gate)."""
        self._actions_enabled = enabled
        for widget in self._rows.values():
            widget.set_enabled_actions(enabled)
