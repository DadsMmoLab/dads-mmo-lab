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
`dadcraft_decorations.py` there (T42; upstream has since renamed it from
`warcraft_`). Colours come from the `COLOR_*` constants `theme.py` exports.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
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
from yulon.ui.widgets.panel_style import panel_qss

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

FAMILY_HINTS: dict[ManifestType, str] = {
    "module": "compiled into the worldserver — a rebuild is owed before one of these runs",
    "ale": "no rebuild — the world reloads them on restart",
    "keg": "Dad's MMO Lab bundles: server side plus a client addon",
    "mod": "SQL and conf only — no compile; the importer applies the SQL",
}
"""The sentence under each card's title, saying what that family COSTS (T44 item 3).

Beside `FAMILY_TITLES` and kept in step with it by
`test_every_family_has_a_hint_that_says_what_that_family_costs`, for the same
reason the titles are here rather than in `manifest_store.py`: these are copy,
and the store has no business knowing how a tab words a card.

The four differ in exactly one thing a user has to know before pressing
Install — whether what they install reaches the running server by itself. A
`module` is C++ and is compiled into the worldserver, so it does not; an `ale`
script is read again at the next world start; a `mod` is SQL and conf files; a
`keg` also copies files into the game CLIENT, which is the one family that
touches a folder outside the server directory at all.
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
BADGE_SQL_NOT_APPLIED = "Cloned, SQL not applied"
"""An installed module whose SQL is still waiting (T44 item 5).

Not a shade of `Installed`: the clone is on disk and the worldserver will
compile it, and the rows it needs are not in the database. That is the state
T43's probe produced on a real install, and it is the half-installed reading
T41 was reported for -- a module that is "there" and does nothing.
"""

# There is deliberately NO `Not for this game` badge, and T44's item 5 asked
# for one (round 2). It would have been unreachable code:
# `ManifestStore._load_at()` RAISES on a manifest whose `game` is not the
# store's, `_load_manifests()` catches that at family scope and reports the
# whole family as broken, so no foreign-game manifest can reach this builder.
# Round 1 shipped the badge with tests that injected such a manifest straight
# into `build_module_rows()` -- coverage of a row nothing on disk can produce,
# which READS as a guarantee and is not one. Making it real means changing the
# store's validation, which is a guard in its own right; it is on the ticket as
# an open finding for the owner rather than in this diff.


def _badge_for(installed: bool, sql_owed: bool) -> str:
    """The one word the badge column says, decided here and never in a widget.

    An installed module whose SQL has not run is not simply `Installed`: the
    clone is on disk and the worldserver will compile it, and the rows it needs
    are not in the database.
    """
    if installed and sql_owed:
        return BADGE_SQL_NOT_APPLIED
    return BADGE_INSTALLED if installed else BADGE_NOT_INSTALLED


CHIP_REBUILD_PENDING = "Rebuild pending"
CHIP_SQL_PENDING = "SQL pending"
CHIP_ASKS_A_QUESTION = "asks a question"
CHIP_NEEDS_CLIENT_FOLDER = "needs the client folder"

ChipKind = Literal["owed", "fact"]

ChipAction = Literal["rebuild", "sql", "update"]
"""The job an owed chip's subpanel offers one press for (T44 item 4).

A KEY and not a label. The view routes on it -- `rebuild` is the tab's own
Rebuild server…, `sql` is Apply module SQL, `update` is the per-module pull --
and routing on the button's TEXT would break the day one of these is reworded.
"""

CHIP_ACTION_LABELS: dict[ChipAction, str] = {
    "rebuild": "Rebuild server…",
    "sql": "Apply module SQL",
    "update": "Update",
}
"""What each action's button says, spelled HERE rather than imported.

The two of them that also exist on the action bar are worded the same way on
purpose, and cannot be imported from `controller_view` -- this module deliberately
imports nothing from it (module docstring). The ellipsis on the rebuild carries
the app's own convention: that press opens a dialog first.
"""


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


def chip_conflicts_with_label(name: str) -> str:
    """The install lock's chip: what is here that this cannot sit beside, by NAME (T55).

    Named for the same reason as `chip_required_by_label()`: the id is what the
    catalog matched on, the name is what the reader sees on the other row.
    """
    return f"conflicts with {name}"


def chip_needs_label(name: str) -> str:
    """The install lock's chip when a declared requirement is absent, by NAME (T69).

    The owner's own words for the row (2026-09-16). Named beside the other two
    so the three lock chips cannot drift apart in wording.
    """
    return f"needs {name}, not installed"


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
    action: ChipAction | None = None
    """The job this chip's subpanel offers, or `None` for a chip that offers none.

    Always `None` on a `fact` chip, and asserted so: a fact names something no
    press can change, and a button under it would be a control for nothing.
    """


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
    badge: str = BADGE_NOT_INSTALLED
    """The badge column's one word, from `_badge_for()`. Decided here so the
    widget renders a decision rather than taking one (T42's split)."""

    version: str | None = None
    """What this clone is at (`7c02b1d · 2026-09-01`), or `None` for not-yet-read.

    `None` renders as NOTHING, never as a placeholder: a sha is the one string
    on this row somebody may paste into an issue, and `unknown` or `—` where
    one goes reads as something git said. A row that fills in late is fine
    (T44 item 1); a row that shows a guess is not.
    """

    installable: bool = True
    """Whether this row's Install may be pressed (T55). Meaningless on an installed row.

    False when something INSTALLED is a declared alternative to this module --
    `apply.conflicting_installed()`, the same reading the applier refuses on --
    so the tab does not offer a press whose only outcome is a refusal. False
    too when something this manifest names in `requires` is NOT installed
    (`apply.missing_requirements()`, T69): the same shape, the other direction.
    At the end of the dataclass, with a default, because the tests build rows
    positionally.
    """

    install_reason: str | None = None
    """The sentence behind `installable=False`, or `None` when Install is open."""

    install_incomplete: bool = False
    """This clone is on disk and its install never finished, so the row keeps Install (T68).

    `apply.unfinished_clones()`'s answer for this `(family, id)`: the claim this
    app wrote into the clone says `install_completed: false`, which is the state
    between the clone landing and the last of the install's steps returning.

    It changes the row's BUTTON and nothing else. `installed` stays True, so the
    badge, the sort order, the version line, the chips and `catalogued`'s
    `NOT_IN_CATALOG` reading are all untouched: the folder IS there, and a row
    that claimed otherwise would be lying about the disk to make a button
    appear. What is wrong is only that the one press on offer was Remove, when
    the applier's own refusal had just said "Press Stop, then install again"
    (T68, measured 2026-09-16).
    """


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

    rebuild_owed: frozenset[tuple[str, str]] = frozenset()
    sql_owed: Mapping[tuple[str, str], tuple[str, ...]] = field(default_factory=dict)
    behind: Mapping[tuple[str, str], int] = field(default_factory=dict)
    """All three keyed by `(family, id)` and NOT by a bare id (round 2).

    Nothing makes a manifest id unique across families: the store loads
    `manifests/<game>/<family>/` one directory at a time and no invariant spans
    them. T42 round 2 keyed the row dict, the manifest dict, the panel's widget
    dict and T43's tuning cards by the pair for exactly that reason and left
    these three on a bare id -- the ONE surface where the collision was
    invisible, until T44's `Cloned, SQL not applied` badge made an `ale`'s
    pending SQL appear on a `module` of the same name, over a module with no
    SQL at all.

    Both fillers know the family: `_note_session_facts()` is handed the
    manifest the press was about, and `apply.module_updates()` enumerates ONE
    clone directory, so every key it returns is in the `module` family by
    construction.
    """


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


class VersionCache:
    """What each installed clone is AT, read once per clone and then remembered.

    T44 item 1's whole price, in one object. A `git log -1` per installed row
    on every `reload_modules()` is what T41 refused and T42 restated: reloads
    happen after every install, every remove, every update check and every
    Refresh, and 20 modules would pay 20 subprocesses each time.

    So the reads are LAZY and CACHED, and the cache is keyed by
    `(server_dir, family, item_id)`:

    * `server_dir`, because two installs of the same catalog have different
      clones and one entry would show one install's sha on the other's row;
    * `family`, because nothing makes an id unique across families -- a `bmah`
      module lives in `modules/` and a `bmah` keg in `ale_scripts/` (T42 round
      2's collision, which cost this codebase four defects in one review).

    `None` is cached exactly like a string, and that is not an oversight: a
    `modules/` folder also holds `CMakeLists.txt` and a user can copy a module
    in with no `.git` (`apply.ModuleUpdate.is_checkout`). Such a row answers
    nothing forever and must cost ONE read, not one per paint -- which is why
    "has it been read?" is `has()` and not `known() is not None`.

    It holds no Qt and reads no disk itself: the reader is handed in, which is
    what lets the tests count the reads.
    """

    def __init__(self, read: Callable[[Path], str | None]) -> None:
        self._read = read
        self._known: dict[tuple[Path, str, str], str | None] = {}

    def path_of(self, server_dir: Path, family: str, item_id: str) -> Path:
        """Where this family's clone of `item_id` lives, by `apply.CLONE_DIRS`."""
        return server_dir / _clone_dir_of(family) / item_id

    def has(self, server_dir: Path, family: str, item_id: str) -> bool:
        """Whether this clone has been read at all -- which "the answer was None" is not."""
        return (server_dir, family, item_id) in self._known

    def known(self, server_dir: Path, family: str, item_id: str) -> str | None:
        """The cached answer, WITHOUT reading anything.

        This is what the row builder is called with, and it is why the first
        paint cannot block: a row whose clone has not been read yet carries
        `None` and simply shows nothing where the version goes.
        """
        return self._known.get((server_dir, family, item_id))

    def fill(self, server_dir: Path, family: str, item_id: str) -> str | None:
        """The answer, reading the clone once if it has not been read yet."""
        key = (server_dir, family, item_id)
        if key not in self._known:
            self._known[key] = self._read(self.path_of(server_dir, family, item_id))
        return self._known[key]

    def forget(self, item_id: str) -> None:
        """Drop this module's entry in every family and every install.

        By BARE ID, and kept so after T48 gave `ApplyReport` a family: an update
        check's `key` still carries none, and forgetting a same-id row in
        another family costs one re-read, where forgetting too little leaves a
        sha the clone has moved off on screen.
        """
        for key in [k for k in self._known if k[2] == item_id]:
            del self._known[key]

    def clear(self) -> None:
        """Forget everything -- what the Refresh press means."""
        self._known.clear()


def _chips_for(
    manifest: Manifest | None,
    family: str,
    item_id: str,
    installed: bool,
    session: SessionState,
    client_dir: Path | None,
    dependants: Sequence[str],
    blocked_by: str | None = None,
    needs: str | None = None,
) -> tuple[Chip, ...]:
    """The seven chips a row may carry, and nothing beyond them.

    `blocked_by` is the NAME of an installed module this row's manifest declares
    a conflict with, or `None` (T55). `needs` is the NAME of something this
    row's manifest declares in `requires` and that is NOT here, or `None` (T69).
    At most one of the two is ever set, because a row carries one lock and one
    reason for it. Both are decided by `build_module_rows()`,
    which is the one place that holds both the catalog and what is installed.

    Owed first and facts after, because the owed ones name work somebody has to
    do and the facts only explain the row. Six and not seven: an "Update" chip
    that could be PRESSED to pull is not here because no per-module pull exists
    below this tab -- `apply.module_updates()` counts and nothing else -- and a
    button that reports a number is not the same control as a button that
    fetches.
    """
    chips: list[Chip] = []
    key = (family, item_id)
    if key in session.rebuild_owed:
        chips.append(
            Chip(
                "owed",
                CHIP_REBUILD_PENDING,
                f"{item_id}: the worldserver has not been compiled since this changed, so it "
                "is not in the running server yet. Press Rebuild server… on this tab.",
                "rebuild",
            )
        )
    owed_sql = session.sql_owed.get(key)
    if owed_sql:
        chips.append(
            Chip(
                "owed",
                CHIP_SQL_PENDING,
                f"{item_id}: this SQL is on disk and has NOT been applied — "
                + ", ".join(owed_sql)
                + ". Press Apply module SQL on this tab.",
                "sql",
            )
        )
    behind = session.behind.get(key, 0)
    if behind > 0:
        chips.append(
            Chip(
                "owed",
                chip_update_label(behind),
                f"{item_id}: its upstream has {behind} commit(s) this checkout does not. "
                "Update fetches and RESETS the clone to the upstream tip, then re-deploys "
                "and re-applies everything the manifest declares — and it may ask this "
                "module's install questions again. It REFUSES rather than reset if the "
                "folder is a different repository, has uncommitted changes in it, or "
                "carries commits the upstream does not, and says which.",
                "update",
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
    # The two lock chips follow the CALLER's decision and no longer re-derive
    # half of it from `installed` (T68). `build_module_rows()` passes a name
    # here only for a row that offers Install, which since T68 includes a clone
    # whose install never finished -- and on that row `and not installed` would
    # have dropped the one sentence saying why the button is locked, leaving the
    # reason in the tooltip alone. The condition was never a second opinion; it
    # was the same one spelled twice, and the copy that went stale is this one.
    if blocked_by is not None:
        chips.append(
            Chip(
                "fact",
                chip_conflicts_with_label(blocked_by),
                conflict_reason(blocked_by),
            )
        )
    if needs is not None:
        chips.append(
            Chip(
                "fact",
                chip_needs_label(needs),
                apply_module.requirement_refusal(item_id, needs),
            )
        )
    return tuple(chips)


def conflict_reason(blocked_by: str) -> str:
    """Why Install is locked, in the words the tooltip, the chip and the menu all use (T55)."""
    return (
        f"{blocked_by} is installed here, and the catalog records the two as alternatives "
        f"that cannot both be installed. Remove {blocked_by} first."
    )


def build_module_rows(
    manifests: Iterable[Manifest],
    installed: Mapping[str, frozenset[str]],
    session: SessionState,
    client_dir: Path | None,
    versions: Mapping[tuple[str, str], str] | None = None,
    unfinished: Mapping[str, frozenset[str]] | None = None,
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

    `unfinished` is `apply.unfinished_clones()`'s answer, in the same shape and
    read from the same folders (T68). It is a SUBSET of `installed` -- both
    walk the clone directories -- and it is passed separately rather than folded
    in because the two facts are different questions with different remedies,
    and every reader of `installed` today means "the folder is there".
    """
    catalog: list[Manifest] = list(manifests)
    half_installed = unfinished or {}
    # What is ALREADY known, keyed the way the rows are. Handed in rather than
    # read here: this function is pure and stays pure, and the reading is the
    # one part of the version line that costs a subprocess (`VersionCache`).
    seen_versions = versions or {}
    # Keyed by (FAMILY, id) and collected as a LIST, and both halves are the
    # round-2 fix. Nothing makes a manifest id unique across families -- the
    # store loads `manifests/<game>/<family>/` one directory at a time and no
    # invariant spans them -- and the two mistakes an id-keyed dict makes are
    # different. As a membership test it reads the wrong FOLDER: `modules/` and
    # `sql_scripts/clones/` are different directories, so a `module` and a `mod`
    # sharing an id both read installed when one clone is on disk. As the source
    # of the dependency graph it DROPS a manifest: only the last one loaded
    # under that id contributed its `requires`, so a base another installed
    # module needs came back removable.
    installed_keys: set[tuple[str, str]] = set()
    installed_manifests: list[Manifest] = []
    for manifest in catalog:
        if manifest.id in installed.get(manifest.type, frozenset()):
            installed_keys.add((manifest.type, manifest.id))
            installed_manifests.append(manifest)
    # Who needs what, counted from what is INSTALLED and not from the catalog:
    # a manifest nobody has installed requires nothing of anybody, and letting
    # it lock a row would make half the catalog unremovable on a fresh install.
    #
    # Keyed by the required ID and not by (family, id), because `Manifest.
    # requires` names an id and never a family -- that is the schema's own
    # precision and inventing a family here would be a guess. The consequence is
    # deliberate and conservative: where an id really is in two families, both
    # rows are held by whatever requires it.
    dependants: dict[str, list[str]] = {}
    for manifest in installed_manifests:
        for needed in manifest.requires:
            dependants.setdefault(needed, []).append(manifest.name)
    names = {(manifest.type, manifest.id): manifest.name for manifest in catalog}

    def _blocked_by(manifest: Manifest) -> str | None:
        # The applier's own reading (T55), so the tab and the refusal cannot
        # disagree about WHAT conflicts. Only in the applier's favour can they
        # differ -- see `conflicting_installed()` on an empty leftover folder.
        found = apply_module.conflicting_installed(manifest, installed)
        if not found:
            return None
        other, kind = found[0]
        return names.get((kind, other), other)

    # `Manifest.requires` names an id and never a family, so the display name is
    # looked up across every family rather than under the requirer's own. Where
    # two families really do share an id both carry the same name anyway; where
    # the catalog knows nothing about the target -- `mod-playerbots`, which the
    # SERVER install clones -- the id IS the name, and that is the right thing
    # to print: it is what the folder under `modules/` is called.
    names_by_id = {manifest.id: manifest.name for manifest in catalog}

    def _needs(manifest: Manifest) -> str | None:
        # The applier's own reading (T69), for the same reason `_blocked_by()`
        # borrows `conflicting_installed()`: the tab must not offer a press the
        # applier will refuse. A folder under any clone directory answers it,
        # which is what makes the server-cloned `mod-playerbots` count.
        missing = apply_module.missing_requirements(manifest, installed)
        if not missing:
            return None
        return names_by_id.get(missing[0], missing[0])

    def _row(manifest: Manifest) -> ModuleRow:
        here = (manifest.type, manifest.id) in installed_keys
        # T68. `unfinished` is a subset of `installed` by construction, so this
        # is asked only where the folder is here -- a row with no clone has no
        # claim to have been read.
        halfway = here and manifest.id in half_installed.get(manifest.type, frozenset())
        # The two locks are asked of every row that OFFERS Install, which since
        # T68 includes a clone whose install never finished (review round 1).
        # They were asked of `not here` alone, which was the same set until this
        # ticket put Install back on a half-installed row: a module whose
        # requirement has since been removed, or one whose declared alternative
        # is installed, would have shown an ENABLED Install with no reason on it,
        # and the applier would have refused the press after the fact
        # (`_conflict_refusal()`, `_requires_refusal()`) -- the exact invariant
        # T55 and T69 exist to keep.
        offers_install = not here or halfway
        needed_by = dependants.get(manifest.id, [])
        blocked_by = _blocked_by(manifest) if offers_install else None
        # One lock and one reason. A conflict is about what is HERE and a
        # missing requirement about what is not, and a row told both at once
        # would have the user remove one module in order to be told to install
        # another. The conflict wins because it is the older answer and the one
        # whose remedy is on this machine already.
        needs = _needs(manifest) if offers_install and blocked_by is None else None
        lock_reason = (
            conflict_reason(blocked_by)
            if blocked_by is not None
            else (
                apply_module.requirement_refusal(manifest.id, needs) if needs is not None else None
            )
        )
        return ModuleRow(
            id=manifest.id,
            family=manifest.type,
            name=manifest.name,
            description=manifest.description,
            url=manifest.source.url if manifest.source is not None else None,
            installed=here,
            catalogued=True,
            paths=tuple(conf.file for conf in manifest.conf),
            chips=_chips_for(
                manifest,
                manifest.type,
                manifest.id,
                here,
                session,
                client_dir,
                needed_by,
                blocked_by,
                needs,
            ),
            removable=not (here and needed_by),
            remove_reason=(
                f"{', '.join(needed_by)} require this — remove them first."
                if here and needed_by
                else None
            ),
            badge=_badge_for(here, bool(session.sql_owed.get((manifest.type, manifest.id)))),
            # Installed rows only. A catalog row that is not on disk has no
            # clone to read, and looking one up for all 41 would be 20 reads
            # of folders that are not there.
            version=seen_versions.get((manifest.type, manifest.id)) if here else None,
            installable=lock_reason is None,
            install_reason=lock_reason,
            install_incomplete=halfway,
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
        rows += [_row(m) for m in family if (kind, m.id) in installed_keys]
        rows += [_row(m) for m in family if (kind, m.id) not in installed_keys]
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
                        None, kind, name, True, session, client_dir, dependants.get(name, [])
                    ),
                    removable=False,
                    remove_reason=None,
                    badge=_badge_for(True, bool(session.sql_owed.get((kind, name)))),
                    version=seen_versions.get((kind, name)),
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
    pressed_chip_action = Signal(str, str)
    clicked = Signal(str)
    menu_requested = Signal(str, QPoint)

    def __init__(self, data: ModuleRow, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.data = data
        self.setObjectName("moduleRow")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu_at)

        # Two rows now, not one: the row proper, and the subpanel an owed chip
        # opens under it (T44 item 4). The outer layout is vertical so the
        # subpanel is INSIDE this row's frame -- an expander drawn as a sibling
        # would open under whatever the card laid out next, which after a
        # reload is not necessarily the same module.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(6)
        box = QHBoxLayout()
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)
        outer.addLayout(box)

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
        # The version, beside the name. Empty until the clone has been read --
        # `set_version()` fills it in place, so a late answer never costs the
        # user their selection or their open sections (T44 item 1).
        self.version_label = QLabel(data.version or "", self)
        self.version_label.setFont(QFont("monospace"))
        self.version_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED};")
        title_row.addWidget(self.version_label)
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
        self.badge_label = QLabel(data.badge, self)
        # Three tones for four badges, and the pairing is by what the badge
        # ASKS OF THE READER rather than by its text: green for a module that
        # is here and working, amber for one that is here and not finished,
        # muted for the two that ask nothing.
        if data.badge == BADGE_SQL_NOT_APPLIED:
            badge_colour = COLOR_TEXT_WARNING
        elif data.installed:
            badge_colour = COLOR_UNCOMMON
        else:
            badge_colour = COLOR_TEXT_MUTED
        self.badge_label.setStyleSheet(f"color: {badge_colour}; font-weight: bold;")
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
                #
                # And it SELECTS first, because a child button consumes its own
                # click and would otherwise leave the row unselected while
                # writing that row's sentence into the report -- the same reason
                # `_row_install` selects before acting (round 2).
                button.clicked.connect(lambda _checked=False, which=chip: self._chip(which))
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
        # T68: a clone whose install never finished keeps Install, even though
        # the folder is there. The refusal that produced that state says "Press
        # Stop, then install again", and before this the only "again" on offer
        # was the context menu's -- the row itself read Remove. Remove is still
        # reachable there, which is the same trade the other way round and the
        # one the owner decided on (2026-09-16).
        if data.catalogued and (not data.installed or data.install_incomplete):
            self.install_button = QPushButton("Install", self)
            self.install_button.clicked.connect(lambda: self.pressed_install.emit(self.data.id))
            if not data.installable:
                self.install_button.setToolTip(data.install_reason or "")
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

        # The subpanel: hidden until an owed chip is pressed, and rebuilt per
        # press rather than one panel per chip. A row can owe three things at
        # once and only one of them is being read at a time.
        self._open_chip: Chip | None = None
        self._actions_enabled = True
        self.detail = QFrame(self)
        self.detail.setObjectName("moduleRowDetail")
        self.detail.setStyleSheet(
            f"QFrame#moduleRowDetail {{ background-color: {COLOR_BG_PARCHMENT_LIGHT}; "
            f"border-left: 3px solid {COLOR_TEXT_WARNING}; }}"
        )
        detail_box = QVBoxLayout(self.detail)
        detail_box.setContentsMargins(10, 6, 10, 6)
        detail_box.setSpacing(4)
        self.detail_label = QLabel("", self.detail)
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY};")
        detail_box.addWidget(self.detail_label)
        detail_actions = QHBoxLayout()
        detail_actions.addStretch(1)
        self.detail_button: QPushButton | None = QPushButton("", self.detail)
        self.detail_button.clicked.connect(self._detail_action)
        detail_actions.addWidget(self.detail_button)
        detail_box.addLayout(detail_actions)
        self.detail.setVisible(False)
        outer.addWidget(self.detail)

        self.set_enabled_actions(True)
        self.set_selected(False)

    def set_enabled_actions(self, enabled: bool) -> None:
        """Arm or disarm this row's one press.

        `enabled` never overrides the ROW's own answer: a module something else
        installed needs stays unremovable when a job finishes, the same rule
        `_set_busy(False)` follows for every other gated control in the view.
        """
        self._actions_enabled = enabled
        if self.install_button is not None:
            self.install_button.setEnabled(enabled and self.data.installable)
        if self.remove_button is not None:
            self.remove_button.setEnabled(enabled and self.data.removable)
        if self.detail_button is not None:
            # The subpanel's press is an APPLIER press like the two above it,
            # so the busy gate has to reach it too -- a Rebuild started from a
            # subpanel while an install is in flight is the same collision the
            # row buttons are locked for.
            self.detail_button.setEnabled(
                enabled and self._open_chip is not None and self._open_chip.action is not None
            )

    def set_version(self, version: str | None) -> None:
        """Show what this clone is at, or nothing. Never a placeholder."""
        self.version_label.setText(version or "")

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
        """A click on the row's BODY selects it — the mockup's rule, and T42's.

        Children that ignore the press (the name, the description, the paths,
        the badge) propagate it here, which is why they are part of the target
        and not dead space.

        Two children do not, and the difference is deliberate (round 2). A CHIP
        consumes its click and selects the row itself, through `_chip()`,
        because a chip press is a statement about that row. The GITHUB LINK
        consumes its click and selects nothing: it opens a browser at somebody
        else's website, and highlighting a row on this tab is not part of that.
        """
        self.clicked.emit(self.data.id)
        super().mousePressEvent(event)

    def _chip(self, chip: Chip) -> None:
        """Select the row, write the report line, and open (or shut) the subpanel.

        All three, and the report line is the one that must not be dropped: it
        is the surface a user copies into a bug report, which is why T44 keeps
        it beside the panel rather than replacing it with one.

        Pressing the chip that is already open shuts it. A row that can only
        ever grow is a row that eats the card it is on.
        """
        self.clicked.emit(self.data.id)
        self.pressed_chip.emit(self.data.id, chip.label)
        if self._open_chip is chip:
            self._open_chip = None
            self.detail.setVisible(False)
            return
        self._open_chip = chip
        self.detail_label.setText(chip.detail)
        if self.detail_button is not None:
            has_action = chip.action is not None
            self.detail_button.setText(
                CHIP_ACTION_LABELS[chip.action] if chip.action is not None else ""
            )
            self.detail_button.setVisible(has_action)
            self.detail_button.setEnabled(self._actions_enabled and has_action)
        self.detail.setVisible(True)

    def _detail_action(self) -> None:
        chip = self._open_chip
        if chip is not None and chip.action is not None:
            self.pressed_chip_action.emit(self.data.id, chip.action)

    def detail_visible(self) -> bool:
        """Whether this row's subpanel is open, read off the widget and the LAYOUT.

        `isVisibleTo(self)` rather than `isVisible()`, because a row inside a
        panel nothing has shown is not on screen and the question here is
        whether the row itself is holding the subpanel open.

        The layout half is the other lesson from `family_hint()`: a frame
        parented here but added to no layout answers `isVisibleTo` True while
        being drawn nowhere, so both have to be asked.
        """
        layout = self.layout()
        if layout is None:
            return False
        laid_out = any(
            (item := layout.itemAt(i)) is not None and item.widget() is self.detail
            for i in range(layout.count())
        )
        return laid_out and self.detail.isVisibleTo(self)

    def _menu_at(self, pos: QPoint) -> None:
        self.menu_requested.emit(self.data.id, self.mapToGlobal(pos))


class _FamilyCard(QGroupBox):
    """One family's card: the installed half, then the rest behind a toggle."""

    def __init__(self, family: str, title: str, hint: str, parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        self.family = family
        self.setObjectName("moduleFamilyCard")
        box = QVBoxLayout(self)
        box.setSpacing(4)
        # Above the header rather than beside it: the count answers "how many do
        # I have?" and the hint answers "what does having one cost?", and a
        # single line carrying both put the second half off the right edge of
        # the narrow card the mockup draws.
        self.hint_label = QLabel(hint, self)
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-style: italic;")
        box.addWidget(self.hint_label)
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

    def laid_out_hint(self) -> QLabel | None:
        """The hint label only if this card really LAYS IT OUT, else `None`.

        Read off the layout for `laid_out_rows()`'s reason, and the reason is
        not theoretical here: a `QLabel` parented to this card but added to no
        layout is still `isVisibleTo()` the panel and still carries its text,
        so the first version of this test passed with the `addWidget` call
        deleted (measured, T44).
        """
        for index in range(self._box.count()):
            item = self._box.itemAt(index)
            if item is not None and item.widget() is self.hint_label:
                return self.hint_label
        return None

    def laid_out_rows(self) -> list[RowWidget]:
        """The rows as this card really lays them out: the installed box, then the other.

        Read off the layouts and not off the list `fill()` was handed, because
        the split into two boxes is what a person actually sees -- and a test
        that reads the handed list can pass while the drawing is wrong.
        """
        out: list[RowWidget] = []
        for layout in (self._installed_layout, self._available_layout):
            for index in range(layout.count()):
                item = layout.itemAt(index)
                widget = None if item is None else item.widget()
                if isinstance(widget, RowWidget):
                    out.append(widget)
        return out

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
    chip_action_pressed = Signal(str, str)
    """`(module id, `ChipAction` key)` -- the press an owed chip's subpanel offers.

    A second signal beside `chip_pressed` rather than a flag on it: the two are
    different events. `chip_pressed` is "the user wants to read about this",
    which writes the report line; this one is "the user wants the job run",
    which the view turns into the applier press that answers it.
    """

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
        # Keyed by (FAMILY, id) since round 2. Nothing makes an id unique across
        # families, and an id-keyed dict lost the first of two rows that shared
        # one: both widgets were still DRAWN, so the user saw two rows and the
        # panel knew one -- `rows()` was short, `row()` answered with whichever
        # was built last, and a click on the other one highlighted it.
        #
        # The public shape stays id-based (`row`, `select`, `selected_id` and
        # every signal), because everything below this tab addresses a module by
        # id alone: `ApplyReport.item_id`, `apply.installed_clones()` and
        # `docker.allowed_modules()` all do, and T43 reads these names. Where a
        # bare id matches more than one row they resolve in `FAMILY_FILES`
        # order, said once in `_key_for()`; a CLICK never uses that rule,
        # because it carries the widget it happened on.
        self._rows: dict[tuple[str, str], RowWidget] = {}
        self._cards: dict[str, _FamilyCard] = {}
        self._open: dict[str, bool] = {}
        self._selected: tuple[str, str] | None = None
        self._actions_enabled = True
        # The shared look (T44 item 6), set on the PANEL so every card and
        # every button inside it inherits it. Nothing here invents a colour --
        # see `panel_style`.
        self.setStyleSheet(panel_qss())
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
            card = _FamilyCard(kind, FAMILY_TITLES[kind], FAMILY_HINTS[kind], self._content)
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
        for key, widget in self._rows.items():
            widget.set_selected(key == self._selected)

    def _make(self, data: ModuleRow) -> RowWidget:
        widget = RowWidget(data)
        widget.pressed_install.connect(self.install_pressed.emit)
        widget.pressed_remove.connect(self.remove_pressed.emit)
        widget.pressed_chip.connect(self.chip_pressed.emit)
        widget.pressed_chip_action.connect(self.chip_action_pressed.emit)
        # The WIDGET, not its id: a click must select the row it happened on,
        # and routing it through `select()` would put it through the bare-id
        # resolution rule and highlight the other family's row (round 2).
        widget.clicked.connect(lambda _id, w=widget: self._select_widget(w))
        widget.menu_requested.connect(self.context_menu_requested.emit)
        widget.set_enabled_actions(self._actions_enabled)
        self._rows[(data.family, data.id)] = widget
        return widget

    def _key_for(self, item_id: str) -> tuple[str, str] | None:
        """The row a bare id names, resolved in `FAMILY_FILES` order.

        The one place the ambiguity of a bare id is settled, so no caller
        invents a second rule. `None` where no row has that id -- which is the
        ordinary answer after a remove, not an error.
        """
        for family in FAMILY_FILES:
            if (family, item_id) in self._rows:
                return (family, item_id)
        for key in self._rows:
            if key[1] == item_id:
                return key
        return None

    def _flip(self, family: str) -> None:
        self._open[family] = not self._open.get(family, True)
        card = self._cards.get(family)
        if card is not None:
            card.set_open(self._open[family])

    # ------------------------------------------------------------------ reading

    def row(self, item_id: str) -> RowWidget:
        """The widget for this id. Raises rather than answering `None`: every caller
        of this asks about a row it has just been told exists.

        Where an id is in two families this answers the first in `FAMILY_FILES`
        order -- see `_key_for()`.
        """
        key = self._key_for(item_id)
        if key is None:
            raise KeyError(item_id)
        return self._rows[key]

    def rows(self) -> tuple[RowWidget, ...]:
        """Every row, in the order `set_rows()` was handed them.

        NOT the drawn order, and the difference matters: the cards split each
        family into an installed box and an available box, so what a person sees
        is `drawn_rows()`. This is the order the builder decided, which is what
        a test of the BUILDER wants (round 2).
        """
        return tuple(self._rows.values())

    def drawn_rows(self) -> tuple[RowWidget, ...]:
        """Every row in the order the cards really lay them out, read off the layouts."""
        out: list[RowWidget] = []
        for family in FAMILY_FILES:
            card = self._cards.get(family)
            if card is not None:
                out += card.laid_out_rows()
        return tuple(out)

    def set_version(self, family: str, item_id: str, version: str | None) -> None:
        """Fill one row's version line IN PLACE, without redrawing anything else.

        In place and not through `set_rows()`, because the fill is
        asynchronous and happens once per installed module: a reload per
        module would take the user's selection and their open sections away
        from them up to twenty times in a row.

        A row that is no longer here is ignored rather than raised on: the
        read and the write are separated by an event-loop turn, and a remove
        can land between them.
        """
        widget = self._rows.get((family, item_id))
        if widget is not None:
            widget.set_version(version)

    def selected_row(self) -> RowWidget | None:
        """The selected row itself, family included -- what `selected_id()` cannot say."""
        return None if self._selected is None else self._rows[self._selected]

    def selected_id(self) -> str | None:
        return None if self._selected is None else self._selected[1]

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

    def family_hint(self, family: str) -> QLabel | None:
        """The card's own "what this family costs" line, as the card really lays it out.

        `None` for a family with no card AND for a card that built the label
        without putting it anywhere -- see `_FamilyCard.laid_out_hint()`.
        """
        card = self._cards.get(family)
        return None if card is None else card.laid_out_hint()

    # ------------------------------------------------------------------ acting

    @Slot(str)
    def select(self, item_id: str) -> None:
        """Select the row this id names, and say so. Unknown ids are ignored, not raised.

        Resolved through `_key_for()`, so a bare id that is in two families
        selects the first in `FAMILY_FILES` order. A click does not come this
        way -- see `_make()`.
        """
        key = self._key_for(item_id)
        if key is not None:
            self._select_widget(self._rows[key])

    def _select_widget(self, widget: RowWidget) -> None:
        """Select exactly this row. The only place selection is actually set."""
        self._selected = (widget.data.family, widget.data.id)
        for known, other in self._rows.items():
            other.set_selected(known == self._selected)
        self.row_selected.emit(widget.data.id)

    def set_enabled_actions(self, enabled: bool) -> None:
        """Arm or disarm every row's press (the busy lock, and the applier gate)."""
        self._actions_enabled = enabled
        for widget in self._rows.values():
            widget.set_enabled_actions(enabled)
