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
`warcraft_decorations.py` is deleted there (T42). Colours come from the
`COLOR_*` constants `theme.py` exports.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from yulon import apply as apply_module
from yulon.manifest import Manifest, ManifestType
from yulon.manifest_store import FAMILY_FILES

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
