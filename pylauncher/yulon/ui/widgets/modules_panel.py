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

from PySide6.QtCore import QPoint, QSize, Qt, Signal, Slot
from PySide6.QtGui import QFont, QMouseEvent, QResizeEvent
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
    TOUCH_TARGET_PX,
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
    """The eight chips a row may carry, and nothing beyond them.

    `blocked_by` is the NAME of an installed module this row's manifest declares
    a conflict with, or `None` (T55). `needs` is the NAME of something this
    row's manifest declares in `requires` and that is NOT here, or `None` (T69).
    At most one of the two is ever set, because a row carries one lock and one
    reason for it. Both are decided by `build_module_rows()`,
    which is the one place that holds both the catalog and what is installed.

    **This order is load-bearing since T75**, because it is also the order the
    row DROPS chips in when they do not fit its width:

    1. the OWED chips, which name work somebody still has to do;
    2. the LOCK chips, which say why Install cannot be pressed at all (T55, T69);
    3. the remaining facts, which only explain the row.

    The lock chips moved ahead of the other facts in review: on a 1280x800
    handheld -- the Steam Deck, which is a shipping target and has no hover --
    `battlepass` showed `needs the client folder` and hid
    `needs AzerothCore Lua Engine (ALE), not installed` behind the overflow
    mark, so the one sentence saying why the button is dead was in a tooltip a
    touch user cannot open. A lock is the last fact to go.

    Eight and not nine: an "Update" chip that could be PRESSED to pull is not
    here because no per-module pull exists below this tab --
    `apply.module_updates()` counts and nothing else -- and a button that
    reports a number is not the same control as a button that fetches.
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
    # The two lock chips come BEFORE the other facts (T75 review), and follow
    # the CALLER's decision rather than re-deriving half of it from `installed`
    # (T68). `build_module_rows()` passes a name here only for a row that offers
    # Install, which since T68 includes a clone whose install never finished --
    # and on that row `and not installed` would have dropped the one sentence
    # saying why the button is locked, leaving the reason in the tooltip alone.
    # The condition was never a second opinion; it was the same one spelled
    # twice, and the copy that went stale is this one.
    #
    # Their PLACE in this list is the same argument made about width instead of
    # about a boolean: a chip the row cannot fit goes behind the overflow mark,
    # where its sentence is a tooltip, and a tooltip is not reachable by touch.
    # Measured at 1280x800 before the move: `battlepass` drew
    # `needs the client folder` and hid `needs AzerothCore Lua Engine (ALE), not
    # installed`, which is the one that explains the dead button.
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

ROW_VERTICAL_PADDING = 2
"""The pixels above and below a row's content (T75). Eight before.

Here beside `BUTTON_COLUMN_WIDTH` rather than in `theme.py`, for the reason that
one is: these are this widget's own geometry and no stylesheet reads them. T45's
rule is about what the THEME authors -- fonts, colours, `min-height`s, the touch
floor -- and nothing here renders larger than the theme says. It renders in less
space around it.
"""

ROW_LINE_SPACING = 0
"""Between the row's name line and its description line.

Nothing, and the two lines still read apart: the name is gold and bold and the
description is muted, which is a stronger separator than two pixels ever were.
Two pixels here is two pixels off the height of every row in the list, and the
row's total is what T75 is about.
"""

DETAIL_SPACING = 4
"""Between the row proper and the subpanel an owed chip opens under it.

Not `ROW_LINE_SPACING`: this gap is only ever drawn on the ONE row whose chip is
open, so it costs the list nothing, and the panel it separates is a different
thing from the row rather than a second line of it.
"""

ROW_GAP = 1
"""Between one row and the next inside a card. Two before.

The gap the eye reads between two rows is not this: it is this plus the two
rows' own `ROW_VERTICAL_PADDING`, so five pixels of sheet still separate one
module's button from the next one's. What this number alone decides is how many
rows a screen holds -- it is paid 40 times on the shipped WotLK catalog.
"""

CARD_SPACING = 6
"""Between one family card and the next, and under a card's last row. Eight before."""

CARD_LINE_SPACING = 2
"""Between a card's hint, its headers, its toggle and its rows. Four before.

Four of these gaps stand above the first row of the list -- the hint, the
installed header, the toggle -- so halving them is 8px off where the rows start
on every window, paid once rather than per row.
"""

CHIP_SPACING = 4
"""Between the badge, the chips, and the overflow chip after them."""

CHIP_OVERFLOW_LABEL = "…"
"""The chip that stands for the chips the row's width could not fit (T75).

One character, because it is the only chip whose width is spent on saying that
there are others: every pixel it takes is a pixel a real chip does not get. What
it hides is in its tooltip, label and sentence both, and what it hides is always
the LAST chips -- which is why `_chips_for()`'s order is owed, then locks, then
the rest: the work somebody still has to do survives the squeeze, then the
reason Install cannot be pressed at all, and only what merely explains the row
goes behind the mark.

The tooltip is the reason the locks are second rather than last: it is not
reachable at all on a touch screen, and the Steam Deck is a shipping target
(`theme.TOUCH_TARGET_PX` exists for the same reason).
"""

ELIDED_LABEL_MIN_CHARS = 8
"""How much of an elided label must survive the narrowest window.

A `QLabel` that does not wrap reports its whole text as its minimum width, which
on a row is a floor the card cannot go under -- the description of `mod-ah-bot`
would decide how narrow the window may be. Eight characters and a tooltip is the
honest floor: enough to see that something is written there, and the whole of it
one hover away.
"""


class _ElidedLabel(QLabel):
    """One line of text that shortens itself to the width it is given (T75).

    The row used to spend a wrapped line -- sometimes two -- on the description
    and another on the conf files, and a row is a LIST row: what it needs is one
    line that says as much as fits and hands the rest to a tooltip. `full_text`
    is what it was given and what the tooltip carries; `text()` is Qt's and is
    whatever is on screen, which is why nothing should read a value back out of
    it.

    The minimum width is overridden for the reason in `ELIDED_LABEL_MIN_CHARS`:
    a non-wrapping `QLabel`'s own minimum is its whole string, and three of those
    on a row is a floor the window cannot be dragged under.
    """

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.full_text = text
        self.setToolTip(text)
        self.setWordWrap(False)
        self._relayout()

    # No setter, deliberately: a row is REBUILT on every `set_rows()` (the panel
    # says so in its own docstring), so a label whose text changes in place would
    # be a mechanism with no caller -- and the one thing on this row that really
    # does fill in late, the version, is a plain `QLabel` with `set_version()`.

    def minimumSizeHint(self) -> QSize:  # noqa: N802  (Qt's own name)
        hint = super().minimumSizeHint()
        return QSize(
            min(hint.width(), self.fontMetrics().averageCharWidth() * ELIDED_LABEL_MIN_CHARS),
            hint.height(),
        )

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802  (Qt's own name)
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        width = self.width()
        if width <= 0:
            super().setText(self.full_text)
            return
        super().setText(
            self.fontMetrics().elidedText(self.full_text, Qt.TextElideMode.ElideRight, width)
        )


class _ChipStrip(QWidget):
    """Every chip a row carries, on ONE line, with an "…" for what did not fit (T75).

    The row drew its chips in a `QHBoxLayout` under the badge, which cost a
    second line on every row whether it had a chip or not -- half of T75's 85px.
    One line is cheap and one line can run out of room, so this widget places its
    own children rather than handing them to a layout: it shows as many chips as
    the width takes, in `_chips_for()`'s order (owed, then locks, then the rest
    -- that function's docstring owns the reason), and the rest go
    behind `CHIP_OVERFLOW_LABEL` with their labels and their sentences in its
    tooltip.

    `buttons` is EVERY chip's button, hidden ones included, because that tuple is
    what `RowWidget.chip_buttons` publishes and a caller asking "does this row
    say `SQL pending`?" is asking about the row and not about today's width.
    `visible_chip_labels()` is the other question, asked separately.
    """

    def __init__(
        self, buttons: Sequence[QPushButton], chips: Sequence[Chip], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.buttons = tuple(buttons)
        self.chips = tuple(chips)
        for button in self.buttons:
            # Built by the row (it is the row that wires their presses) and
            # adopted here, because this widget places its children by geometry
            # and can only place its own.
            button.setParent(self)
        self.overflow: QPushButton | None = None
        if self.buttons:
            self.overflow = QPushButton(CHIP_OVERFLOW_LABEL, self)
            self.overflow.setFlat(True)
            self.overflow.setStyleSheet(
                f"color: {COLOR_TEXT_MUTED}; border: 1px solid {COLOR_BRASS_DARK}; "
                f"padding: 0px 6px; min-height: {TOUCH_TARGET_PX}px;"
            )
            self.overflow.setVisible(False)
        self._place()

    def _line_height(self) -> int:
        return max((b.sizeHint().height() for b in self.buttons), default=0)

    def sizeHint(self) -> QSize:  # noqa: N802  (Qt's own name)
        widths = [b.sizeHint().width() for b in self.buttons]
        spacing = CHIP_SPACING * max(0, len(widths) - 1)
        return QSize(sum(widths) + spacing, self._line_height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802  (Qt's own name)
        # One chip's worth of width and no more: the strip must be squeezable to
        # the "…" alone, or a row with six chips is what decides how narrow the
        # window may be dragged -- which is the floor `_ElidedLabel` exists to
        # avoid on the other half of the row.
        width = 0 if self.overflow is None else self.overflow.sizeHint().width()
        return QSize(width, self._line_height())

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802  (Qt's own name)
        super().resizeEvent(event)
        self._place()

    def visible_chip_labels(self) -> tuple[str, ...]:
        """The chips this width leaves drawn, in the order they are drawn.

        `isVisibleTo(self)` and not `isVisible()`, which is `detail_visible()`'s
        lesson from T44 in the other direction: a row on a tab nobody has opened
        is not on screen, and the question here is what THIS widget has hidden,
        not whether the window is showing. Asked with `isVisible()` a strip on a
        background tab answers that it has hidden everything.
        """
        return tuple(b.text() for b in self.buttons if b.isVisibleTo(self))

    def hidden_chips(self) -> tuple[Chip, ...]:
        """The chips the width could not fit, which are the ones in the tooltip."""
        return tuple(
            chip
            for chip, button in zip(self.chips, self.buttons, strict=True)
            if not button.isVisibleTo(self)
        )

    def _place(self) -> None:
        if not self.buttons:
            return
        available = self.width()
        height = self.height() or self._line_height()
        widths = [b.sizeHint().width() for b in self.buttons]
        needed = sum(widths) + CHIP_SPACING * (len(widths) - 1)
        shown = len(self.buttons) if needed <= available else self._how_many_fit(widths, available)
        x = 0
        for index, button in enumerate(self.buttons):
            if index < shown:
                button.setGeometry(x, 0, widths[index], height)
                x += widths[index] + CHIP_SPACING
            button.setVisible(index < shown)
        if self.overflow is not None:
            hiding = self.buttons[shown:]
            self.overflow.setVisible(bool(hiding))
            if hiding:
                self.overflow.setGeometry(x, 0, self.overflow.sizeHint().width(), height)
                self.overflow.setToolTip(
                    "Also on this row:\n\n"
                    + "\n\n".join(f"{chip.label} — {chip.detail}" for chip in self.hidden_chips())
                )

    def _how_many_fit(self, widths: Sequence[int], available: int) -> int:
        """How many chips fit beside the "…", which is itself always drawn.

        The overflow chip's own width is reserved FIRST and never traded away:
        fitting one more real chip by dropping the mark that says others exist
        is the one outcome this widget must not produce -- the row would then be
        quietly wrong rather than merely short.
        """
        assert self.overflow is not None
        room = available - self.overflow.sizeHint().width() - CHIP_SPACING
        used = 0
        for count, width in enumerate(widths):
            used += width if count == 0 else width + CHIP_SPACING
            if used > room:
                return count
        return len(widths)


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
        outer.setContentsMargins(10, ROW_VERTICAL_PADDING, 10, ROW_VERTICAL_PADDING)
        outer.setSpacing(DETAIL_SPACING)
        box = QHBoxLayout()
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)
        outer.addLayout(box)

        left = QVBoxLayout()
        left.setSpacing(ROW_LINE_SPACING)
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
        # The conf files this manifest writes, FOLDED onto the name line (T75).
        # They were a line of their own -- one per file, stacked -- which is a
        # paragraph of paths on a row whose subject is a module. Joined, elided
        # and hovered: the whole list is still one hover away, and on a wide
        # window it is simply read.
        if data.paths:
            self.paths_label: _ElidedLabel | None = _ElidedLabel(", ".join(data.paths), self)
            self.paths_label.setFont(QFont("monospace"))
            self.paths_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED};")
            title_row.addWidget(self.paths_label, 1)
        else:
            self.paths_label = None
        title_row.addStretch(1)
        left.addLayout(title_row)

        # One line, elided, with the whole sentence in its tooltip. Wrapped, a
        # long description was worth up to 20px of extra row on the narrow
        # windows -- the 105px rows measured at 1280x800 before T75 -- and it was
        # the one thing on the row whose height nobody could predict.
        self.description_label = _ElidedLabel(data.description, self)
        self.description_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED};")
        left.addWidget(self.description_label)
        box.addLayout(left, 1)

        middle = QHBoxLayout()
        middle.setSpacing(CHIP_SPACING)
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
        buttons: list[QPushButton] = []
        for chip in data.chips:
            button = QPushButton(chip.label, self)
            button.setFlat(True)
            button.setToolTip(chip.detail)
            colour = COLOR_TEXT_WARNING if chip.kind == "owed" else COLOR_TEXT_MUTED
            button.setStyleSheet(
                f"color: {colour}; border: 1px solid {COLOR_BRASS_DARK}; padding: 0px 6px; "
                f"min-height: {TOUCH_TARGET_PX}px;"
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
        self.chip_buttons = tuple(buttons)
        # Beside the badge and no longer under it (T75): the second line the
        # chips had was drawn on every row, chips or none, and a row that owes
        # nothing is most of the list.
        self.chip_strip = _ChipStrip(buttons, data.chips, self)
        middle.addWidget(self.chip_strip, 1)
        # TWO shares against the text column's one (T75 review). What this column
        # carries is the row's STATE, and a chip that does not fit is a sentence
        # in a tooltip -- which on the Steam Deck (1280x800, touch) is a sentence
        # nobody can read. What the left column carries is prose, and it is
        # already elided with the whole of it one hover away on a machine that
        # HAS hover. Measured at 1280x800: an even split gave the strip 373px
        # against a lock chip of 341 plus the mark, so `battlepass` drew no chip
        # at all; two shares give it 533 and both of its chips are on screen.
        box.addLayout(middle, 2)

        self.install_button: QPushButton | None = None
        self.remove_button: QPushButton | None = None
        column = QVBoxLayout()
        # No margins of its own (T75). A `QVBoxLayout` on a bare `QWidget` takes
        # the style's default 9px on all four sides, so the action column asked
        # for 69px around a 51px button and WAS the row's 85px floor -- measured
        # themed at every width from 960 to 2560, where the two text columns
        # varied and this one did not.
        column.setContentsMargins(0, 0, 0, 0)
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
            # The theme gives every `QPushButton` `padding: 8px 16px` on top of
            # its `min-height`, which is right for a dialog's buttons and is
            # 17px of air per module row. The vertical padding goes and
            # `TOUCH_TARGET_PX` is spelled back in explicitly: an override that
            # dropped the `min-height` with it would take the handheld floor
            # away, which is the one size on this row that is not ours to spend
            # (T45, and `theme.TOUCH_TARGET_PX`'s own docstring).
            action.setStyleSheet(f"padding: 0px 16px; min-height: {TOUCH_TARGET_PX}px;")
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
        # The theme's `QGroupBox` padding IS the card's inset -- 12px at the top
        # and 10px on each side -- and this layout's own 9px default was a second
        # one inside it (T75). So all four are dropped, horizontally included:
        # the sides already clear the card's border by the theme's 10px, and each
        # row adds 10px of its own on top of that.
        box.setContentsMargins(0, 0, 0, CARD_SPACING)
        box.setSpacing(CARD_LINE_SPACING)
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
        self._installed_layout.setSpacing(ROW_GAP)
        box.addWidget(self.installed_box)
        self.toggle: QToolButton | None = None
        self.available_box = QWidget(self)
        self._available_layout = QVBoxLayout(self.available_box)
        self._available_layout.setContentsMargins(0, 0, 0, 0)
        self._available_layout.setSpacing(ROW_GAP)
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
        # No margin of its own inside the scroll area (T75): the cards are
        # QGroupBoxes and the theme already gives each one a 10px top margin, a
        # 12px top padding and a border, so the default 9px here was a fourth
        # inset between the viewport's edge and the first row -- 9px off the top
        # of the list on every window.
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(CARD_SPACING)
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
