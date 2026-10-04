"""Controller view — one install's management surface (roadmap 4.3).

Tabs: **Server** (start/stop/status with the README §12 port-conflict message),
**Console** (live worldserver log, a console command line, an accounts form),
**Modules** (the manifests the store knows, install/remove through the shared
applier, the rebuild/restart the report asks for), **Networking** (LAN /
internet play via `networking.plan()` + `apply()`, showing the router steps the
app cannot do). The view only calls down into `Controller`, `Applier`,
`console`, `networking` and signals up; it never shells out itself
(style-guide §3/§5). Every external call is a seam in `ControllerServices` so
the view is testable offscreen with fakes.

`ControllerServices.for_entry()` builds those seams out of the installed game's
own `controller_<acronym>` package, chosen by catalog id through `_FACTORIES`.
Until 7.9 this module imported `controller_wow_wotlk` directly and used it for
every install, so a TBC, Vanilla or Tortoise tab drove AzerothCore's package:
its `acore_*` schema names, its `AC>` console prompt and its `ready...` marker
reached servers that have none of them.
"""

from __future__ import annotations

import contextlib
import enum
import math
import os
import re
import shutil
import threading
import time
from collections import deque
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple, Protocol, cast

import shiboken6
from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from yulon import apply as apply_module
from yulon import bot_population as botpop
from yulon import (
    botlist,
    channel_setup,
    client_config,
    client_exe,
    client_packs,
    commands,
    dbreads,
    docker,
    install_wiring,
    logsnap,
    networking,
    party,
    platform,
    play_client,
    play_launch,
    purge,
    reset_defaults,
    resources,
    server_build_presses,
    server_time_zone,
    serverlock,
    tuning,
    useraccounts,
    wsl,
)
from yulon import channel as channel_module
from yulon import dashboard as dashboard_module
from yulon import play as play_module
from yulon import steam as steam_module
from yulon.apply import (
    Applier,
    ApplyReport,
    DockerSql,
    PendingSql,
    ReleaseDirectionUnknown,
    must_ask,
    reapplies_on_top,
    required_prompts,
)
from yulon.catalog import bot_dashboard, composegen, native, preflight, time_zone, upstream
from yulon.catalog.catalog import CatalogEntry, Client, ClientPack, ConfigWtf
from yulon.catalog.families import azerothcore, clientdir, decisions, mmaps, trinitycore
from yulon.catalog.installer import (
    InstallerError,
    InstallOptions,
    RollbackNotDone,
    WorldStoppedAfterReadyError,
    rebuild_confirmation,
)
from yulon.controller import Controller, InstallStatus, PortConflictError
from yulon.controller_wow_centurion import accounts as centurion_accounts
from yulon.controller_wow_centurion import characters as centurion_characters
from yulon.controller_wow_centurion import console as centurion_console
from yulon.controller_wow_centurion import controller as centurion_controller
from yulon.controller_wow_centurion import maintenance as centurion_maintenance
from yulon.controller_wow_tbc import accounts as tbc_accounts
from yulon.controller_wow_tbc import console as tbc_console
from yulon.controller_wow_tbc import controller as tbc_controller
from yulon.controller_wow_tbc import maintenance as tbc_maintenance
from yulon.controller_wow_tbc import modules as tbc_modules
from yulon.controller_wow_tortoise import accounts as tortoise_accounts
from yulon.controller_wow_tortoise import autoupdate as tortoise_autoupdate
from yulon.controller_wow_tortoise import botdash as tortoise_botdash
from yulon.controller_wow_tortoise import botpool as tortoise_botpool
from yulon.controller_wow_tortoise import console as tortoise_console
from yulon.controller_wow_tortoise import controller as tortoise_controller
from yulon.controller_wow_tortoise import maintenance as tortoise_maintenance
from yulon.controller_wow_tortoise import modules as tortoise_modules
from yulon.controller_wow_tortoise import poolreset as tortoise_poolreset
from yulon.controller_wow_vanilla import accounts as vanilla_accounts
from yulon.controller_wow_vanilla import console as vanilla_console
from yulon.controller_wow_vanilla import controller as vanilla_controller
from yulon.controller_wow_vanilla import maintenance as vanilla_maintenance
from yulon.controller_wow_vanilla import modules as vanilla_modules
from yulon.controller_wow_wotlk import accounts as wotlk_accounts
from yulon.controller_wow_wotlk import console as wotlk_console
from yulon.controller_wow_wotlk import maintenance as wotlk_maintenance
from yulon.controller_wow_wotlk import modules as wotlk_modules
from yulon.git import Behind, RunnerGit, is_behind
from yulon.log import get_logger
from yulon.manifest import ConfKey, Manifest, Prompt, When
from yulon.manifest_store import FAMILY_FILES, ManifestStore
from yulon.networking import Mode, NetworkPlan, NetworkReport
from yulon.ui import lines
from yulon.ui.answers import said_yes
from yulon.ui.catalog_view import DirPicker, _qt_dir_picker, offer_a_docker_group_restart
from yulon.ui.folder_picker import pick_folder
from yulon.ui.icons import dadcraft_icon, get_tab_icon
from yulon.ui.message_box import FittedMessageBox
from yulon.ui.theme import (
    COLOR_BG_PARCHMENT,
    COLOR_GOLD_LIGHT,
    COLOR_TEXT_GOLD,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_WARNING,
    PLAY_MENU_BUTTON,
    SERVER_BUILD_BUTTON,
)
from yulon.ui.widgets.dadcraft_decorations import DadcraftRealmBadge
from yulon.ui.widgets.flow_layout import flow_bar
from yulon.ui.widgets.job import JobRunner, LineRelay, threaded_job_runner
from yulon.ui.widgets.log_panel import CollapseHandle, LogPanel
from yulon.ui.widgets.manifest_prompt import ask_manifest_prompts
from yulon.ui.widgets.modules_panel import (
    ModulesPanel,
    SessionState,
    VersionCache,
    build_module_rows,
    moved_by_server_update,
)
from yulon.ui.widgets.party_panel import PartyPanel
from yulon.ui.widgets.prompt import InputPrompter
from yulon.ui.widgets.tuning_panel import TuningPanel, build_tuning_cards

logger = get_logger(__name__)


def _realm_badge_status(status: InstallStatus) -> str:
    """The `DadcraftRealmBadge` state for an `InstallStatus` (Server tab).

    All up reads "running", some up reads "partial" (PARTLY UP), none up reads
    "stopped". Some up used to read "starting", which was false for a database
    left up on its own (T188 C4). While a Start, Stop or Restart of ours runs
    the badge holds "starting", "stopping" or "restarting" instead
    (`ControllerView._hold_badge`), and a poll does not move it.
    """
    if status.all_running:
        return "running"
    if status.any_running:
        return "partial"
    return "stopped"


class _PathfindingReadBroke(RuntimeError):
    """A pathfinding read that raised, carried back with the generation it was asked in."""

    def __init__(self, generation: int, cause: Exception) -> None:
        super().__init__(str(cause))
        self.generation = generation
        self.cause = cause


class _GearReadBroke(RuntimeError):
    """A Characters gear read that raised, carrying which selection it was for (T96)."""

    def __init__(self, generation: int, name: str, cause: Exception) -> None:
        super().__init__(f"{name}: {cause}")
        self.generation = generation
        self.name = name
        self.cause = cause


class UnsupportedGameError(RuntimeError):
    """No `controller_<game>` package is wired to this catalog id.

    Raised instead of falling back to the WotLK package, which is what this
    module did for every game until 7.9: `for_wotlk()` was called for a TBC, a
    Vanilla and a Tortoise install alike, and the fallback was invisible —
    AzerothCore's `acore_*` schema names, its `AC>` console prompt and its
    `ready...` marker simply reached a server that has none of them, and the
    failures surfaced one control at a time, six clicks later.

    A game in the catalog with no factory here is a DEFECT rather than a user
    situation: `catalog.get()` has already refused an id that is not in
    `catalog.json`, so reaching this means a game was added without its
    controller package being wired. `test_controller_view.py` asserts the
    registry covers the whole catalog, so this raises in CI before it can
    raise in front of anybody.
    """


class BotBrowser(Protocol):
    """What the Bots tab needs (8.5a). One question, asked with a page and a filter."""

    def page(self, *, after: tuple[str, int] | None = None, name_like: str = "") -> object: ...


class MyPartySeam(Protocol):
    """What the My Party control needs (8.6). Three reads and three presses.

    `state()` carries the group AND the reason there is none, in one object,
    because they are one question: the empty list a broken bridge produces is
    the same empty list a working party with no bots in it produces, and the
    2026-08-20 failure is exactly that pair being told apart wrongly.

    It grew by four more in T26 (2026-09-10), and every one of them is a reading
    OF AN INSTALL for the same reason the T5 three are: `candidates()` is four
    database reads and this install's own `MaxAddedBots`, `add_named()` is the
    bridge whisper and the group poll, and the two link halves resolve two
    accounts across `acore_auth` and `acore_playerbots` before one of them
    writes. A panel that did any of it itself would be a widget that knows where
    a server folder and a database are.

    It grew by three in T5 (2026-09-09) and every one of them is here rather than
    in the panel because it is a reading OF AN INSTALL: `specs()` is this
    server's `playerbots.conf`, `max_level()` is its `worldserver.conf`, and
    `remove_all()` is the group table read at the moment of the press, against
    the guids a person confirmed. A panel that read any of them itself would be
    a widget that knows where a server folder is -- and a `remove_all` that took
    only a name would be a confirmation the panel checks and the server ignores,
    which is what round 2 rejected.
    """

    def state(self, master: str) -> party.PartyState: ...

    def specs(self, klass: str) -> tuple[str, ...]: ...

    def max_level(self) -> int | None: ...

    def add(
        self,
        master: str,
        klass: str,
        *,
        gender: str = "",
        spec: str = "",
        level: int | None = None,
    ) -> party.Addition: ...

    def remove(self, master: str, bot: str) -> party.Dismissal: ...

    def remove_all(self, master: str, confirmed: tuple[int, ...]) -> party.MassDismissal: ...

    def candidates(self, master: str) -> party.Picker: ...

    def add_named(self, master: str, name: str) -> party.NamedAddition: ...

    def link_plan(self, master: str, account: str) -> party.AccountLink: ...

    def link_account(self, master: str, account: str) -> party.AccountLink: ...


class Uninstall(Protocol):
    """What the Uninstall control needs (8.9a): a plan, and a run that takes the checkbox.

    A Protocol rather than the concrete `purge.Uninstaller` for the reason every
    other seam on this tab is one: the view is tested offscreen with a fake, and
    the fake for THIS one must be a fake -- a test that reached the real thing
    would be a test that deletes a directory.
    """

    forget: Callable[[], None]
    """How this install's record is forgotten -- the LAST thing `run()` does.

    Part of the protocol because `main.py` REPLACES it: the factory's default
    re-reads `state.json`, and the running window holds one live `AppState` that
    every tab writes into. An attribute rather than a constructor argument
    because the object is built by the factory and the live state exists only in
    the window's closure.
    """

    stop_control: docker.StopControl | None
    """How the removal of a loading world's containers is heard and forced (T158).

    Set by the tab, as `Controller.stop_control` is: the uninstall's own
    `remove_staged()` waits for a world that cannot yet hear a stop, and says
    so in the uninstall label, with the Server tab's "Stop now anyway" beside it.
    """

    def plan(self) -> purge.PurgePlan: ...

    def run(self, *, keep_characters: bool) -> purge.PurgeReport: ...


class AccountAdmin(Protocol):
    """What the Accounts tab needs of this install's accounts (8.3a).

    A read and two writes, and the split between them is owner answer 7 rather
    than a layering choice: this app reads rows and the SERVER changes them.
    """

    def listing(self) -> object: ...

    def set_password(self, account: str, password: str) -> object: ...

    def set_gm_level(self, account: str, level: int) -> object: ...


class BotDashboardSeam(Protocol):
    """The Bots tab's "Bot dashboard" switch (T127). Every method does IO: never on the GUI thread.

    `switch_on`/`switch_off`/`restart_world` are line sources for the tab's log
    panel; `state` and `world_running` are readings for `_run()`.
    """

    url: str

    def state(self) -> object: ...

    def world_running(self) -> bool | None: ...

    def switch_on(self, *, lan: bool, cancel: threading.Event | None = None) -> Iterator[str]: ...

    def switch_off(self, cancel: threading.Event | None = None) -> Iterator[str]: ...

    def restart_world(self, cancel: threading.Event | None = None) -> Iterator[str]: ...

    def rebuild(self, cancel: threading.Event | None = None) -> Iterator[str]: ...


class BotPoolRebuildSeam(Protocol):
    """The Bots tab's "Rebuild random bots…" (T144), on Tortoise only.

    `rebuild` and `restart_owed_now` are line sources for the tab's log panel
    (worker thread); `take_module_moved` is read on the GUI thread after an
    update press has finished, and answers once: None when the press did not
    move the module, else whether T123's enrolment is waiting on a restart.
    """

    def take_module_moved(self) -> tortoise_botpool.Move | None: ...

    def rebuild(
        self,
        *,
        backup: Callable[[], object] | None = None,
        cancel: threading.Event | None = None,
        restart_owed: bool = False,
    ) -> Iterator[str]: ...

    def restart_owed_now(self, cancel: threading.Event | None = None) -> Iterator[str]: ...

    def before_restore(self) -> tortoise_poolreset.TakenBack | None: ...

    def after_a_failed_restore(
        self, taken: tortoise_poolreset.TakenBack, *, loaded: bool | None
    ) -> str: ...

    def restore_warning(self) -> str | None: ...

    def put_back_file(
        self, backup: Path, target: Path, put: Callable[[Path, Path], None]
    ) -> str | None: ...


@dataclass(frozen=True)
class _PlanWithWarning:
    """A restore plan, and T144's warning about the bot request that the restore leaves alone."""

    plan: wotlk_maintenance.RestorePlan
    warning: str | None


def _unreadable(marker: object) -> bool:
    """A restore marker that was there but could not be parsed (`InterruptedRestore.readable`)."""
    return marker is not None and not getattr(marker, "readable", True)


@dataclass(frozen=True)
class _RestoredWithNote:
    """A restore's report, and T144's line when a pending bot rebuild was taken back first."""

    report: wotlk_maintenance.RestoreReport
    note: str | None


class ChannelSetup(Protocol):
    """What the Server tab needs of the command channel (8.2a).

    Two methods, and the split matters: `enable()` is told whether the world is
    running rather than deciding for itself, because the view is what knows the
    status and `channel_setup` is what owns the rule. Neither guesses at the
    other's job.

    `setup_state()` rather than `status()` deliberately: `docker.status()` takes
    a `wsl_distro`, and `test_controller_view.py`'s seam guard flags any call in
    this file that names a distro-aware seam without passing one. A method here
    that shares that name would have to be excused by hand, and a guard with an
    exemption for a name collision is a guard one step nearer to useless.
    """

    def enable(self, *, world_running: bool) -> object: ...

    def settle(self) -> object: ...

    def check(self) -> object: ...

    def repair(self) -> object: ...

    def roll_back(self) -> bool: ...

    def setup_state(self) -> object: ...


@dataclass(frozen=True)
class DatabaseAlone:
    """Bring this install's database up on its own, and put it back down (T76).

    One object rather than two callables, for `ChannelSetup`'s reason: the two
    halves are one decision seen from both ends, and a tab wired with a `start`
    and no `stop` would leave a database running that nobody asked to run.

    `bring_up` answers whether it HAD to start the container -- `docker.
    start_database()`'s own return -- and that answer is the only thing that
    may make `take_down` run. A backup on a server the user had running must
    not stop it; a backup on a stopped server must not leave it up.

    `bring_up`/`take_down` rather than `start`/`stop`, for `ChannelSetup.
    setup_state()`'s reason: `test_every_seam_for_wotlk_builds_says_which_
    daemon_it_means` flags any call in this file to a name that is declared
    somewhere with a `wsl_distro` parameter and does not pass one, and both of
    those names are. A method here that shared one would have to be excused by
    hand, and a guard with an exemption for a name collision is a guard one
    step nearer to useless.
    """

    bring_up: Callable[[str], bool]
    """Start the database alone and wait for it to be healthy. True if it had to.

    The argument is `because`: what was NOT done if the start fails, completing
    `docker.start_database()`'s timeout sentence. A parameter since T216, when
    Restore became the second caller: bound once in the factory, a restore
    whose database never came up told the player *"…so no backup was taken"*.

    One that raises has answered nothing, so `take_down` is not called after
    it; it has put back whatever it started itself (`_database_alone()`).
    """
    take_down: Callable[[], None]
    """Stop the database again. Called only where `bring_up` answered True."""


class PlanRestore(Protocol):
    """`maintenance.plan_restore()` bound to one install: the Maintenance tab's planner.

    A Protocol and not `Callable[[Path], RestorePlan]` for T216's keyword. Only
    a tab that holds a `DatabaseAlone` passes `can_start_database=True`, which
    turns "the database is not running" from a refusal into a plan that starts
    it; every other caller, and every call that omits it, gets the planner as it
    was. The restore's own re-plan, made once the database is up, never needs it.
    """

    def __call__(
        self, path: Path, *, can_start_database: bool = False
    ) -> wotlk_maintenance.RestorePlan: ...


_ROW_SETTLE_MS = 750
"""How long to leave the server to write what it has already reported.

Measured rather than guessed (2026-09-07, WotLK on `yulon-ubuntu`):

    level 55 -> 58: answered in 0.18s, the row changed after 0.30s
    level 58 -> 59: answered in 0.15s, the row changed after 0.26s

The sentence a person reads never waits for this: the server's own words appear
the moment they arrive, and only the LIST is scheduled.
"""

_LEVEL_ROLE = Qt.ItemDataRole.UserRole + 2
"""Where a character row keeps its level, for the Level box to start from (T188 A6)."""


class _CharacterAnswer(NamedTuple):
    """A Characters action's outcome, with the (name, level) a Set level press sent."""

    outcome: object
    level_sent: tuple[str, int] | None


_ROW_SETTLE_TRIES = 4
"""How many times to re-read before giving up on the row catching up.

One fixed delay measured on one server is a guess about every other -- a slower
box, a bigger world, a stalled disk (8.4a's adversarial review). So the list is
re-read up to four times, at 750ms, 1.5s, 2.25s and 3s, and stops as soon as it
changes. Four is a bound rather than a promise: past it the list is what it is,
and the server's own sentence is still on screen saying what happened.
"""


_CHARACTER_ACTIONS = (
    "Teleport",
    "Set level",
    "Rename at next login",
    "Revive",
    "Send gold to",
    "Send everything worn by",
)
"""The verbs, in the order `ControllerView.character_buttons()` returns them.

Two lists that have to stay in step would be a bug waiting; `zip(..., strict=True)`
makes a seventh button added to one and not the other raise on the first
selection rather than silently mislabel.
"""

_NO_MY_PARTY = (
    "Building a bot party from the launcher works on WoW WotLK only (owner decision, "
    "2026-09-06). The route is a pair of AzerothCore modules — the mod-ale Lua bridge, "
    "and mod-playerbots' own addclass — so {game} would need a route of its own before "
    "there could be a control here. One that sent these commands at it would be a "
    "button that cannot work."
)
"""The whole My Party surface on the three games that have no route to it.

A sentence rather than a disabled panel, which is `_build_characters_tab`'s rule
for the same situation: a control that cannot work is a promise this tab cannot
keep. The scope is the owner's (`pyplan/phase8-parity-decisions.md:41`, "My Party
WotLK-only; Browse Bots on all four") and the reason is the engine's, which is
why no later box can change it by measuring something —
`party.InstallParty.for_entry_is_possible` is the same rule spelled in the module
this text is about."""


def _no_my_party(entry: CatalogEntry) -> str:
    """What the My Party group says on a game with no route to it.

    A TrinityCore server says the family-decision registry's own note
    (`decisions.PARTY_REASON`, T179), so the registry and the tab cannot drift; the
    shipped CMaNGOS games keep their wording from before T179 (lead ruling).
    """
    native_block = entry.install.native
    if native_block is not None and native_block.family == "trinitycore":
        return decisions.PARTY_REASON
    return _NO_MY_PARTY.format(game=entry.name)


NO_ADDON_MODULES = (
    "{game} has no add-on modules: its bots and its own features are built into the "
    "server itself, so there is nothing to add or remove here."
)
"""The Modules tab of a game whose server takes no add-ons (T179: Centurion)."""

_RENAME_OFFLINE_LABEL = "has to be logged in to be renamed"
"""What the button says when the tree's entry refuses an offline rename.

The short half of a two-length refusal, and short is the whole point: the
measured sentence behind it is ~200 characters and a QPushButton is not where
200 characters go -- 8.4c photographed a 180-character label running off the
end of the window. The long half stays the entry's and becomes the tooltip.

Here rather than in the catalog because it says nothing about any particular
server: it is the field's own definition read back ("what to say instead of
offering the at-login rename to a character who is NOT logged in"), and it is
the same shape the revive refusal beside it takes.
"""


def _withheld_sentence(withheld: Mapping[str, str]) -> str:
    """One line naming the Characters verbs this tree does not offer, and why (T179).

    Grouped by reason, in the tab's own order, with the tab's own labels: the verbs
    a person would have looked for, not their ids.
    """
    labels = dict(zip(play_module.VERBS, _CHARACTER_ACTIONS, strict=True))
    reasons: dict[str, list[str]] = {}
    for verb in play_module.VERBS:
        if verb in withheld:
            reasons.setdefault(withheld[verb], []).append(labels[verb])
    return " ".join(
        f"Not offered on this server yet: {', '.join(names)} — {reason}."
        for reason, names in reasons.items()
    )


def _highest_level(entry: CatalogEntry) -> int:
    """The highest GM level this tree's own command accepts.

    Falls back to 3 where the tree's level store has not been measured, which is
    the level every core in this catalog calls administrator: a game whose block
    is absent draws no level controls that do anything anyway, and 3 is the
    number this app drew for all four before any of them were measured.
    """
    level = entry.accounts.level
    return level.max_level if level is not None else 3


class PromptAsker(Protocol):
    """Puts a manifest's own questions to the user, or returns `None` for "cancel".

    A constructor seam for the reason `catalog_view`'s pickers are seams: a modal
    dialog cannot run headless, and the part worth testing is what the tab does with
    the answer — install with it, or change nothing at all.

    `again` is True when the module is already installed -- an Update, or the
    context menu's Install over it -- so the dialog can say that what it shows
    is what gets applied now (T100 review). `remembered` is what this install
    last answered (T104, `Applier.remembered_answers()`), filled in over the
    manifest's defaults. `removing` is True for a Remove, which asks only what
    the record cannot answer (`apply.must_ask()`).
    """

    def __call__(
        self,
        parent: QWidget,
        manifest: Manifest,
        prompts: Sequence[Prompt],
        *,
        again: bool = False,
        remembered: Mapping[str, str] | None = None,
        removing: bool = False,
    ) -> Mapping[str, str] | None: ...


LinkAsker = Callable[[QWidget, str], "str | None"]
"""Puts the "paste a link" question to the user, or returns `None` for "cancel".

A constructor seam for `PromptAsker`'s reason and by the same evidence: a test
that reached the real `QInputDialog` would sit on a modal window forever. The
part worth testing is what the tab does with the answer — derive and install
it, or change nothing at all — and neither of those needs a window.
"""

FolderAsker = Callable[[QWidget, str], "Path | None"]
"""Puts the "choose a folder" question to the user, or `None` for "cancel".

A directory only. An archive is not taken in v1 (design §4): Qt's native
pickers choose a directory or a file, never either, and a second control for a
`.zip` doubles the surface for something the user does with one right-click.
"""


class CustomModuleInstall(Protocol):
    """Install a manifest this app derived rather than shipped; `None` means "clone it".

    A `Protocol` rather than the `Callable` alias this was until T47, for the
    one keyword: `replacing` carries the user's Yes to
    `ControllerServices.module_replacement_question`'s sentence, and a
    `Callable[...]` cannot give an argument a default. Every caller that is
    replacing nothing still calls this with two positional arguments and gets
    the behaviour it always had.

    DEVIATION from the design (§3.3, §3.5), forced and recorded rather than
    quiet. The design has this view call `applier.install(m, None,
    folder=FolderSource(path, copier), complete=...)` — lane B's widened
    signature, over lane A's `copy_folder` and `complete`. Neither lane was on
    that branch, so the view would not type-check against them, and a view that
    constructs `apply.FolderSource` knows one thing more about the applier than
    `ui/*_view.py` is allowed to (style-guide §3: delegate, never hold the
    business logic). So the whole call sits behind one seam, wired from
    `controller_<acronym>/modules.py` — the file whose job is "binding the
    shared applier to that game" — and the view hands it the two things only
    the view can know: which manifest, and which folder the user chose.
    Everything the design lists as `module_complete` and `module_copy_folder`
    lives on the far side of it.
    """

    def __call__(
        self, manifest: Manifest, folder: Path | None, *, replacing: bool = False
    ) -> ApplyReport: ...


def ask_module_link(parent: QWidget, title: str) -> str | None:
    """The real `LinkAsker`: one line of text, or `None` if the user cancelled.

    Cancel and an empty box are deliberately DIFFERENT answers. Cancel returns
    `None` and the tab says it changed nothing; an empty box returns `""` and
    goes to the deriving seam, which owns the "paste a link first" sentence —
    one place decides what a link has to look like, and it is not this file.
    """
    text, accepted = QInputDialog.getText(
        parent,
        title,
        MODULE_LINK_DIALOG_PROMPT,
        QLineEdit.EchoMode.Normal,
        "",
    )
    return text if accepted else None


def ask_module_folder(parent: QWidget, title: str) -> Path | None:
    """The real `FolderAsker`: a directory, or `None` if the user cancelled.

    `pick_folder` (T215) turns Qt's `""` for cancel back into `None`: as a
    `Path` it would be `Path(".")`, the process's working directory, which on a
    packaged build is wherever the user launched it from.
    """
    return pick_folder(parent, title)


SET_CLIENT_DIR_LABEL = "Set client folder…"
"""The Server tab's own button (T36), and the button the client notice offers (T62).

One constant because the notice tells the user to press something by name and
that name is a widget's label: a rename that reached only one of them would send
its reader looking for a control that is not there — the defect `REBUILD_HISTORY`
records, in miniature."""


def client_notice(manifest: Manifest) -> str:
    """What the user is told before installing a module that also changes the game client.

    Plain words and the files by name, because "client step" is this app's
    vocabulary and a user knows only what they will or will not see in the
    game. It says what happens if they carry on without a folder — the server
    half lands and the game half does not — so that choosing to set the folder
    is a decision rather than a chore (owner, 2026-09-15: "Before installing
    any modules that needs a client they should get known about it").
    """
    files = ", ".join(Path(step.src).name for step in manifest.client)
    return (
        f"{manifest.name} also changes your WoW game client, not only the server: it puts "
        f"{files} into your game folder. No client folder is set for this install, so that "
        f"part would be left out and {manifest.name} would not work properly in the game.\n\n"
        f"Set your client folder first, then press Install again. Nothing has been installed yet."
    )


def ask_to_set_client_dir(parent: QWidget, manifest: Manifest) -> bool:
    """The client notice as a dialog: True for "Set client folder…", False for Cancel.

    An instance rather than the static `question()` so the button can say what
    it does, relabelled the way `catalog_view._qt_suggestion_asker` relabels its
    own. Read through `said_yes()` all the same: `exec()` hands back a plain int
    on this PySide6, and `is StandardButton.Yes` would read every press as
    Cancel (T33). Cancel is the default and the escape button, so Enter, Escape
    and the close button all install nothing.
    """
    box = FittedMessageBox(
        QMessageBox.Icon.Information,
        f"{manifest.name} needs your game client",
        client_notice(manifest),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        parent,
    )
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    box.setEscapeButton(QMessageBox.StandardButton.Cancel)
    set_button = box.button(QMessageBox.StandardButton.Yes)
    if set_button is not None:
        set_button.setText(SET_CLIENT_DIR_LABEL)
    return said_yes(box.exec())


# ------------------------------------------------- the ready-to-play client (T181)

MAKE_PLAY_CLIENT_LABEL = "Make a ready-to-play client…"
PLAY_LABEL = "Play"
REFRESH_PLAY_CLIENT_LABEL = "Refresh from your original client"
DELETE_PLAY_CLIENT_LABEL = "Delete ready-to-play client…"
CLIENT_OPTIONS_LABEL = "Client options…"
PLAY_CLIENT_ADDRESS = "127.0.0.1"
"""What a ready-to-play client's realmlist names (spec §1, amended 2026-09-30).

The client runs on the machine that runs Yu'lon, which is where the login server
is (a WSL-resident one too, through WSL's localhost forwarding). The LAN or
public address would be wrong here: a router often does not loop a machine's
own public address back to it. The world address comes from the realm row the
Networking tab manages, not from this file.
"""


def _realm_address(record: client_packs.PackRecord) -> str:
    """The address the ready-to-play client points at: the launcher's pick, else this machine."""
    return str(record.launcher.get("realm_address") or PLAY_CLIENT_ADDRESS)


def _launcher_writes(record: client_packs.PackRecord) -> bool:
    """Whether the launcher's picks (T187) put a key into, or take one out of, Config.wtf.

    Asked only of an entry without `config_wtf`, whose realmlist.wtf carries
    this computer's address (`_address_written_elsewhere`).
    """
    return bool(
        client_packs.launcher_config_keys(record.launcher, catalog_always={})
        or client_packs.launcher_config_removals(
            record.launcher, catalog_always={}, default_address_written=True
        )
    )


def _catalog_always(cfg: ConfigWtf | None) -> dict[str, str]:
    """The Config.wtf keys the catalog sets for every player; none without a `config_wtf`."""
    return dict(cfg.always) if cfg is not None else {}


def _address_written_elsewhere(cfg: ConfigWtf | None) -> bool:
    """Whether this computer's address reaches the game without a typed one in Config.wtf.

    Through realmlist.wtf (no `config_wtf`, or one that keeps the locale
    realmlists, which Play writes) or through the catalog's own `realmList`.
    Then "Use this computer" may take a typed address's lines back out of
    Config.wtf (T187 fix 1); otherwise they are the only address there is.
    """
    if cfg is None or not cfg.remove_locale_realmlists:
        return True
    return "realmlist" in {key.casefold() for key in cfg.always}


PLAY_START_FAILED = "The server did not start, so World of Warcraft was not started."

PLAY_PENDING = (
    "Play is still starting World of Warcraft from this server's ready-to-play client. "
    "Wait for it to finish, then press this again. Nothing was changed."
)
"""Make…, Refresh, Delete and Uninstall while a Play is on its way (T181a final review)."""

LINKED_LEFT_OUT = "Left out because it is linked: "
ONEDRIVE_WARNING = (
    "This folder is inside {root}, which OneDrive syncs: it would upload the "
    "client's files and may later keep only placeholders of them on this PC. "
    "Choose a folder outside it."
)


def left_out_sentence(names: Collection[Path]) -> str:
    """Archives the original has and the ready-to-play client lacks (Refresh never adds them).

    Not called "new": the original's copy of a module patch removed after the
    switch is not, and `archives_left_out()` leaves those out of `names`.
    """
    return (
        "Left out (in your own client only): "
        + ", ".join(str(name) for name in names)
        + ". Making the ready-to-play client again takes in every archive your own "
        "client has, including other servers' module patches and patches of modules "
        "you removed, so only do that if those archives are meant for this server."
    )


PLAY_PIPELINE_RUNNING = (
    "Yu'lon is getting this server's ready-to-play client ready for Play (packs, Wow.exe, "
    "settings). Press Cancel beside Play to stop it, then close the window. Closing now "
    "would leave the client with some of its files installed; Play carries on from there."
)

PLAY_CLIENT_RUNNING = (
    "Yu'lon is still writing this server's ready-to-play client. Closing now would "
    "leave it half made or half refreshed. This window will close normally once it "
    "finishes — the Server tab says what it is doing."
)


@dataclass(frozen=True)
class PlayClientOffer:
    """What the "Make a ready-to-play client…" dialog shows, worked out off the GUI thread.

    `copies` are the receipts of this server's module files that Yu'lon put
    into the player's own client (`apply.client_receipts()`, paths inside
    `original`): the files "Also remove them from your original client" may
    take back, by the receipt's hash. `addons` are the addon folders this
    server's modules copied there, which were never receipted and are only
    named. `replan` and `free_space` are for a folder changed in the dialog.
    """

    original: Path
    target: Path
    plan: play_client.BuildPlan
    free_bytes: int | None
    copies: tuple[apply_module.ClientCopy, ...]
    addons: tuple[str, ...]
    replan: Callable[[Path], play_client.BuildPlan]
    free_space: Callable[[Path], int | None]
    client: Client | None = None

    @property
    def originals(self) -> tuple[Path, ...]:
        return tuple(Path(copy.path) for copy in self.copies)


@dataclass(frozen=True)
class PlayClientChoice:
    """The dialog's answer: where, whether a full copy was agreed to, and the removal box."""

    target: Path
    full_copy: bool
    remove_originals: bool
    client_choices: dict[str, Any] | None = None


PlayClientAsker = Callable[[QWidget, PlayClientOffer], PlayClientChoice | None]
"""How the tab asks the creation dialog: a seam, so a test answers it without a modal."""


def has_client_data(client: Client) -> bool:
    """Whether the entry describes anything to put in a ready-to-play client (T181 b/c)."""
    return bool(client.packs or client.exe_patch is not None or client.config_wtf is not None)


def has_client_choices(client: Client) -> bool:
    """Whether a player can choose anything: an optional pack or a Wow.exe option."""
    return any(pack.optional for pack in client.packs) or bool(
        client.exe_patch is not None and client.exe_patch.options
    )


class ClientOptionsBox(QWidget):
    """The packs and Wow.exe options of a server's client, as checkboxes (T181 b/c).

    Shared by the creation dialog and "Client options…", so the two cannot
    disagree about what a choice is. `choices()` is the record's shape:
    `{"packs": {id: bool}, "exe_options": {name: bool}}`. Only OPTIONAL packs are
    boxes (a required pack is not a choice), and the required download size
    (URL packs' `size_hint`; a checkout pack is already on the disk) is a line of
    its own. An `unavailable` pack (its address answered 404) says so, and is
    greyed unless an installed copy exists, which the player may still switch off.
    """

    def __init__(
        self,
        client: Client,
        choices: Mapping[str, Any] | None = None,
        *,
        installed: Collection[str] = (),
        unavailable: Collection[str] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        picked = choices or {}
        chosen_packs = picked.get("packs") or {}
        chosen_exe = picked.get("exe_options") or {}
        self._client = client
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        required = sum(
            pack.size_hint or 0
            for pack in client.packs
            if not pack.optional and pack.source.kind == "url"
        )
        self.required_label = QLabel("", self)
        self.required_label.setWordWrap(True)
        self.required_label.setText(
            f"Required download: {size_text(required)}." if required else "Nothing to download."
        )
        self.required_label.setVisible(bool(client.packs))
        box.addWidget(self.required_label)
        self.extra_label = QLabel("", self)
        self.extra_label.setWordWrap(True)
        box.addWidget(self.extra_label)
        self.pack_boxes: dict[str, QCheckBox] = {}
        self._sizes: dict[str, int] = {}
        for pack in client.packs:
            if not pack.optional:
                continue
            gone = pack.id in unavailable
            check = QCheckBox(self._pack_text(pack, gone), self)
            check.setChecked(bool(chosen_packs.get(pack.id, pack.default)))
            if gone and pack.id not in installed:
                check.setChecked(False)
                check.setEnabled(False)
            if pack.source.kind == "url":
                self._sizes[pack.id] = pack.size_hint or 0
            check.toggled.connect(self._update_extra)
            self.pack_boxes[pack.id] = check
            box.addWidget(check)
        self.exe_boxes: dict[str, QCheckBox] = {}
        if client.exe_patch is not None:
            for name, option in client.exe_patch.options.items():
                check = QCheckBox(option.label, self)
                check.setChecked(bool(chosen_exe.get(name, option.default)))
                self.exe_boxes[name] = check
                box.addWidget(check)
        self._update_extra()

    @staticmethod
    def _pack_text(pack: ClientPack, gone: bool) -> str:
        text = pack.label
        if pack.description:
            text += f" — {pack.description}"
        if pack.size_hint:
            text += f" ({size_text(pack.size_hint)})"
        if gone:
            text += " — unavailable: the server's site no longer has it"
        return text

    def _update_extra(self) -> None:
        extra = sum(
            size
            for pack_id, size in self._sizes.items()
            if self.pack_boxes[pack_id].isChecked() and self.pack_boxes[pack_id].isEnabled()
        )
        self.extra_label.setText(f"The ticked packs add {size_text(extra)}." if extra else "")
        self.extra_label.setVisible(bool(extra))

    def choices(self) -> dict[str, Any]:
        return {
            "packs": {pack_id: check.isChecked() for pack_id, check in self.pack_boxes.items()},
            "exe_options": {name: check.isChecked() for name, check in self.exe_boxes.items()},
        }


@dataclass(frozen=True)
class ClientOptionsOffer:
    """What "Client options…" shows: the client section, the record's choices, what is where.

    `installed` are the pack ids the record says are in the ready-to-play client;
    `unavailable` the optional packs whose address answered 404 at the last Play.
    """

    client: Client
    choices: dict[str, Any]
    installed: frozenset[str]
    unavailable: frozenset[str]


ClientOptionsAsker = Callable[[QWidget, ClientOptionsOffer], dict[str, Any] | None]
"""How the tab asks "Client options…": a seam, so a test answers it without a modal."""


class ClientOptionsDialog(QDialog):
    """ "Client options…": the checkboxes, Save and Cancel. Save applies at the next Play."""

    def __init__(self, offer: ClientOptionsOffer, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(CLIENT_OPTIONS_LABEL.rstrip("…"))
        box = QVBoxLayout(self)
        intro = QLabel(
            "Choose what this server's ready-to-play client has. A change takes effect "
            "at your next Play: packs are downloaded and installed, or removed, then.",
            self,
        )
        intro.setWordWrap(True)
        box.addWidget(intro)
        self.options = ClientOptionsBox(
            offer.client,
            offer.choices,
            installed=offer.installed,
            unavailable=offer.unavailable,
            parent=self,
        )
        box.addWidget(self.options)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)


def ask_client_options(parent: QWidget, offer: ClientOptionsOffer) -> dict[str, Any] | None:
    """The real `ClientOptionsAsker`: the dialog, modal; None for Cancel."""
    dialog = ClientOptionsDialog(offer, parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.options.choices()


def _gb_text(size: int) -> str:
    return f"{size / 1024**3:.1f} GB"


def _free_bytes(path: Path) -> int | None:
    """Free space on the drive `path` would be made on (its nearest existing folder)."""
    for candidate in (path, *path.parents):
        if candidate.exists():
            try:
                return shutil.disk_usage(candidate).free
            except OSError:
                return None
    return None


def _play_size_text(plan: play_client.BuildPlan, free: int | None) -> str:
    """The size line: what is shared, what is its own, or the full copy and the free space."""
    full = _gb_text(plan.shared_bytes + plan.own_bytes)
    space = f", and that drive has {_gb_text(free)} free" if free is not None else ""
    if plan.same_volume:
        return (
            f"{_gb_text(plan.shared_bytes)} of game files are shared with your client and "
            f"take no extra space; its own files take {size_text(plan.own_bytes)}{space}."
        )
    return (
        "This folder is on another drive than your client, so its game files cannot be "
        f"shared: it would be a full copy of {full}{space}."
    )


class PlayClientDialog(QDialog):
    """The creation dialog (T181a §1, §3): where, how big, and the original's module files.

    Built from a `PlayClientOffer` and read back through `choice()`, so a test
    looks at the widgets without opening a modal. OK is live only for a folder
    `plan()` accepted and, across drives, only once the full copy is agreed to.

    A folder typed or picked here is planned again through `jobs`, off the GUI
    thread: the plan walks the whole client, and the leftover of a crashed full
    copy it clears first can be gigabytes. Until that answer is in, and while
    the path field says anything but the folder it answered for, OK is dead:
    what is made is always the folder the numbers on screen are for.
    """

    def __init__(
        self,
        offer: PlayClientOffer,
        *,
        pick_dir: DirPicker = _qt_dir_picker,
        jobs: JobRunner | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Make a ready-to-play client")
        self._offer = offer
        self._pick_dir = pick_dir
        self._jobs: JobRunner = jobs or threaded_job_runner(self)
        self._plan: play_client.BuildPlan | None = offer.plan
        self._planned: Path = offer.target
        self._free = offer.free_bytes
        box = QVBoxLayout(self)
        intro = QLabel(
            f"Yu'lon makes a copy of your client at {offer.original} that is set up for "
            "this server. Play starts it. Your own client is never changed for this server.",
            self,
        )
        intro.setWordWrap(True)
        box.addWidget(intro)
        row = QHBoxLayout()
        self.path_edit = QLineEdit(str(offer.target), self)
        self.change_button = QPushButton("Change…", self)
        row.addWidget(self.path_edit, 1)
        row.addWidget(self.change_button)
        box.addLayout(row)
        self.size_label = QLabel("", self)
        self.size_label.setWordWrap(True)
        self.size_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(self.size_label)
        self.full_copy_check = QCheckBox("", self)
        box.addWidget(self.full_copy_check)
        self.links_label = QLabel("", self)
        self.links_label.setWordWrap(True)
        self.links_label.setVisible(False)
        box.addWidget(self.links_label)
        self.onedrive_label = QLabel("", self)
        self.onedrive_label.setWordWrap(True)
        self.onedrive_label.setVisible(False)
        box.addWidget(self.onedrive_label)
        self.originals_label = QLabel(
            "Yu'lon put these files into your own client for this server's modules. The "
            "ready-to-play client gets them too:",
            self,
        )
        self.originals_label.setWordWrap(True)
        self.originals_list = QListWidget(self)
        for path in offer.originals:
            self.originals_list.addItem(str(path))
        self.remove_originals_check = QCheckBox("Also remove them from your original client", self)
        self.remove_originals_check.setToolTip(
            "Only files still exactly as Yu'lon copied them are removed; any file that "
            "changed since is left where it is. Files another server of yours also uses "
            "are kept and not listed here."
        )
        self.remove_originals_check.setChecked(False)
        for widget in (self.originals_label, self.originals_list, self.remove_originals_check):
            widget.setVisible(bool(offer.originals))
            box.addWidget(widget)
        self.addons_label = QLabel(
            "Addons Yu'lon copied into your own client (it never deletes addons; you can "
            "delete these yourself): " + ", ".join(offer.addons),
            self,
        )
        self.addons_label.setWordWrap(True)
        self.addons_label.setVisible(bool(offer.addons))
        box.addWidget(self.addons_label)
        # T181 b/c: the server's packs and Wow.exe options, when its entry has any.
        self.client_options: ClientOptionsBox | None = None
        client = offer.client
        if client is not None and (client.packs or has_client_choices(client)):
            self.client_options = ClientOptionsBox(client, parent=self)
            box.addWidget(self.client_options)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert ok is not None
        ok.setText("Make it")
        self.ok_button = ok
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)
        self.path_edit.editingFinished.connect(self._path_edited)
        self.path_edit.textChanged.connect(self._path_typed)
        self.change_button.clicked.connect(self._change)
        self.full_copy_check.toggled.connect(self._update_ok)
        self._show_plan()

    def _shown_path(self) -> Path:
        return Path(self.path_edit.text().strip())

    def _path_edited(self) -> None:
        target = self._shown_path()
        if target != self._planned or self._plan is None:
            self._retarget(target)

    def _path_typed(self, _text: str) -> None:
        if self._shown_path() != self._planned:
            self.size_label.setText("Press Enter to check this folder.")
        elif self._plan is not None:
            self.size_label.setText(_play_size_text(self._plan, self._free))
        self._update_ok()

    def _change(self) -> None:
        """Pick the folder to put it in; the name stays the one shown."""
        current = Path(self.path_edit.text().strip())
        chosen = self._pick_dir(
            self, "Choose where to put the ready-to-play client", current.parent
        )
        if chosen is None:
            return
        target = chosen / current.name
        self.path_edit.setText(str(target))
        self._retarget(target)

    def _retarget(self, target: Path) -> None:
        """Plan `target` off the GUI thread; OK stays dead until its answer is in."""
        self._plan = None
        self._planned = target
        self.full_copy_check.setChecked(False)
        self.full_copy_check.setVisible(False)
        self.size_label.setText(f"Checking {target}\u2026")
        self._update_ok()
        offer = self._offer

        def work() -> tuple[Path, object, int | None]:
            # The folder rides along with its answer: an edit made meanwhile
            # must not be shown another folder's numbers.
            try:
                return target, offer.replan(target), offer.free_space(target)
            except (play_client.PlayClientError, OSError) as exc:
                return target, exc, None

        self._jobs(work, self._replanned, self._replan_failed)

    @Slot(object)
    def _replanned(self, answer: object) -> None:
        if not isinstance(answer, tuple) or answer[0] != self._planned:
            return  # an older folder's answer, overtaken by another edit
        _target, plan, free = answer
        if isinstance(plan, play_client.BuildPlan):
            self._plan = plan
            self._free = free if isinstance(free, int) else None
            self._show_plan()
            return
        self._plan = None
        self.size_label.setText(str(plan))
        self._update_ok()

    @Slot(object)
    def _replan_failed(self, exc: object) -> None:
        """Something `work()` did not expect; the folder stays unplanned and OK dead."""
        logger.warning(f"ready-to-play client: planning {self._planned} failed: {exc!r}")
        self._plan = None
        self.size_label.setText(f"{self._planned} could not be checked: {exc}")
        self._update_ok()

    def _show_plan(self) -> None:
        plan = self._plan
        if plan is None:
            self.full_copy_check.setVisible(False)
            self.links_label.setVisible(False)
            self.onedrive_label.setVisible(False)
            self._update_ok()
            return
        self.size_label.setText(_play_size_text(plan, self._free))
        self.links_label.setText(
            LINKED_LEFT_OUT + ", ".join(str(rel) for rel in plan.skipped_links) + "."
        )
        self.links_label.setVisible(bool(plan.skipped_links))
        synced = play_client.onedrive_folder(
            self._planned, env=os.environ, os_name=platform.detect()
        )
        self.onedrive_label.setText(
            ONEDRIVE_WARNING.format(root=synced) if synced is not None else ""
        )
        self.onedrive_label.setVisible(synced is not None)
        full = _gb_text(plan.shared_bytes + plan.own_bytes)
        if plan.same_volume:
            # A drive that cannot share files at all (exFAT, FAT32) is only found
            # out by trying, so the agreement is offered here too, unticked.
            self.full_copy_check.setText(
                f"If this drive cannot share files, make a full copy instead ({full})"
            )
        else:
            self.full_copy_check.setText(f"Make a full copy ({full})")
        self.full_copy_check.setVisible(True)
        self._update_ok()

    def _update_ok(self) -> None:
        self.ok_button.setEnabled(self.choice() is not None)

    def choice(self) -> PlayClientChoice | None:
        """What OK would make, or None while the folder is refused or the copy not agreed."""
        plan = self._plan
        if plan is None or self._shown_path() != self._planned:
            return None
        full_copy = self.full_copy_check.isChecked()
        if not plan.same_volume and not full_copy:
            return None
        return PlayClientChoice(
            target=self._planned,
            full_copy=full_copy,
            remove_originals=bool(self._offer.originals)
            and self.remove_originals_check.isChecked(),
            client_choices=(
                self.client_options.choices() if self.client_options is not None else None
            ),
        )


def ask_play_client(
    parent: QWidget,
    offer: PlayClientOffer,
    pick_dir: DirPicker = _qt_dir_picker,
    jobs: JobRunner | None = None,
) -> PlayClientChoice | None:
    """The real `PlayClientAsker`: the dialog, modal; None for Cancel.

    `jobs` is the tab's own runner, whose `shutdown()` joins a re-plan still
    running when the window closes; the dialog's own would be destroyed with it.
    """
    dialog = PlayClientDialog(offer, pick_dir=pick_dir, jobs=jobs, parent=parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.choice()


def _ask_with(
    parent: QWidget, title: str, text: str, yes: str, save: str | None = None
) -> str | None:
    """A question with relabelled standard buttons, Cancel the default: "yes", "save" or None.

    `ask_backup_choice()`'s shape for the same reasons: a relabelled standard
    button answers `exec()` with a value, and `No` is never one of them, so the
    suite's modal guard (which answers `No`) can never walk into an action.
    """
    buttons = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
    if save is not None:
        buttons |= QMessageBox.StandardButton.Save
    box = FittedMessageBox(QMessageBox.Icon.Question, title, text, buttons, parent)
    _relabel(box, QMessageBox.StandardButton.Yes, yes)
    if save is not None:
        _relabel(box, QMessageBox.StandardButton.Save, save)
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    box.setEscapeButton(QMessageBox.StandardButton.Cancel)
    answer = box.exec()
    if answer == QMessageBox.StandardButton.Yes:
        return "yes"
    if save is not None and answer == QMessageBox.StandardButton.Save:
        return "save"
    return None


@dataclass(frozen=True)
class _MadePlayClient:
    """A finished build, read on the GUI thread; `copies` are still to be removed (ticked)."""

    target: Path
    realmlist_problem: str | None
    copies: tuple[apply_module.ClientCopy, ...]
    choices_problem: str | None = None


class _PackStopped(Exception):
    """A pack could not be fetched or installed; Play asks what to do (T181 b).

    `pack` is the catalog's; `reason` is the engine's own sentence (or the
    checkout's, naming the file and commit); the question is the view's.
    """

    def __init__(self, pack: ClientPack, reason: str) -> None:
        super().__init__(reason)
        self.pack = pack
        self.reason = reason


@dataclass(frozen=True)
class _Prepared:
    """What the Play pipeline did, read on the GUI thread: lines to show, and 404 packs."""

    notes: tuple[str, ...]
    unavailable: frozenset[str]


def _half_installed(entry: Mapping[str, Any]) -> bool:
    """A record entry a `PartialInstall` left: no version, no checksum, a mix of files."""
    return entry.get("version") is None and entry.get("sha256") is None


def _checkout_commit(server_dir: Path, rel: str, wsl_distro: str | None = None) -> str | None:
    """The commit the checkout holding `rel` is on, read the way the Server build section does."""
    folder = (server_dir / rel).parent
    for candidate in (folder, *folder.parents):
        if (candidate / ".git").exists():
            return tortoise_botpool.head_sha(candidate, wsl_distro=wsl_distro)
        if candidate == server_dir:
            break
    return None


def _checkout_refusal(
    pack: ClientPack,
    server_dir: Path,
    wsl_distro: str | None = None,
    again: str = PLAY_LABEL,
) -> str | None:
    """The refusal for a checkout pack whose file is not there (decision 3), or None.

    Names the file and the commit the server is on, and points at the Server
    build menu: a newer commit (or the tested pin) is what has the file. `again`
    is the press to make afterwards: Play, or Make… when nothing was built.
    """
    rel = pack.source.path
    if pack.source.kind != "checkout" or rel is None:
        return None
    path = server_dir / rel
    if path.is_file() or any(path.parent.glob(f"{path.name}.part*")):
        return None
    commit = _checkout_commit(server_dir, rel, wsl_distro=wsl_distro)
    on = f"commit {commit[:10]}" if commit else "the commit it is on"
    return (
        f"“{pack.label}” is needed, but this server's checkout has no {rel} at {on}. Use "
        f"{server_build_presses.under_server_build(server_build_presses.UPDATE_TO_LATEST)} to "
        f"get a commit that has it (or "
        f"{server_build_presses.under_server_build(server_build_presses.RETURN_TO_PIN)}), "
        f"then press {again} again."
    )


@dataclass(frozen=True)
class _Compared:
    """A ready-to-play client against the original, read off the GUI thread.

    `stale` is what `play_client.stale()` listed (or, after a Refresh, what was
    refreshed); `left_out` the original's archives it has no file for, which
    Refresh never adds (`play_client.left_out_archives()`).
    """

    stale: tuple[Path, ...]
    left_out: tuple[Path, ...]


@dataclass(frozen=True)
class _UninstallOutcome:
    """An uninstall's report, and what became of its ready-to-play client (None: not asked)."""

    report: purge.PurgeReport
    play_client: str | None


def _delete_with_the_server(play: Path, *, game: str, server_dir: Path) -> str:
    """Delete a removed server's ready-to-play client; the sentence that says how it went."""
    try:
        play_client.delete(play, game=game, server_dir=server_dir, can_try_again=False)
    except play_client.PlayClientError as exc:
        return str(exc)
    return f"Its ready-to-play client at {play} was deleted; your own client keeps all its files."


ModuleSqlRoute = Callable[[Callable[[str], None]], docker.AttachedRun]
"""Apply the SQL of the modules on disk, reporting the importer's lines to a sink.

Named once because the field, the factory parameter and the tab's own attribute
all have to be the same shape, and the thing that makes this route different
from every other seam on the tab is that its ANSWER is a run rather than a
report: whether a module's SQL was applied is only knowable from what the
importer printed (`>> Applying update <file>.sql`), so the lines are the result
and the sink is not a nicety.
"""


@dataclass(frozen=True)
class Pathfinding:
    """T179: the background movement-map job's reading and its two presses, for one install.

    `status` asks Docker (`mmaps.mmaps_status`), so the Server tab asks it off the
    GUI thread; `start`/`stop` answer with the sentence they want shown and raise
    `mmaps.MmapsError` with the refusal. One object, so the reading and the presses
    cannot be wired to two different servers.
    """

    status: Callable[[], mmaps.MmapsStatus]
    start: Callable[[], str]
    stop: Callable[[], str]


def _pathfinding(
    entry: CatalogEntry, server_dir: Path, *, wsl_distro: str | None
) -> Pathfinding | None:
    """The job's seam where the entry makes its movement maps in the background.

    A catalog fact (`mmaps.background_block`), so it is wired in `_assemble` for
    every game the way the time zone is. Not for a server inside a WSL distro: the
    job runs on THIS host's Docker (`mmaps.DockerRunner`), which would be asked about
    a container it has never heard of.
    """
    if wsl_distro is not None or mmaps.background_block(entry) is None:
        return None
    return Pathfinding(
        status=lambda: mmaps.mmaps_status(server_dir, entry),
        start=lambda: mmaps.start_mmaps(server_dir, entry),
        stop=lambda: mmaps.stop_mmaps(server_dir, entry),
    )


@dataclass(frozen=True)
class WorldUpkeep:
    """T179 Task 6: what an update left a server owing, and the two presses that pay it.

    `read` is a file read (`trinitycore.needs_reextract`, `pending_world_reimport`)
    answering the map-data sentence and the world-update sentence, either None; the
    Server tab asks it off the GUI thread. `reextract` is "Re-extract map data"
    (None inside a WSL distro, where the sentence then says where the press is) and
    `finish_world` is "Finish the world update"; both stream into the log panel.
    """

    read: Callable[[], tuple[str | None, str | None]]
    reextract: install_wiring.ReextractPress | None
    finish_world: install_wiring.WorldReimportPress | None


def _world_upkeep(
    entry: CatalogEntry, server_dir: Path, *, wsl_distro: str | None
) -> WorldUpkeep | None:
    """The seam where the entry's update route can leave map data or world tables owing.

    Read off the wiring (`install_wiring.world_reimport_for_app`), which offers the
    retry for exactly the engines that leave such a record, so the tab names no
    family. Inside a WSL distro the map data cannot be extracted from here
    (`reextract_for_app` answers None), and the sentence says so instead of naming
    a press the tab does not draw.
    """
    finish = install_wiring.world_reimport_for_app(entry, server_dir, wsl_distro=wsl_distro)
    reextract = install_wiring.reextract_for_app(entry, server_dir, wsl_distro=wsl_distro)
    if finish is None and reextract is None:
        return None

    def read() -> tuple[str | None, str | None]:
        return (
            trinitycore.needs_reextract(server_dir, entry, press_here=reextract is not None),
            trinitycore.pending_world_reimport(server_dir, entry, press_here=finish is not None),
        )

    return WorldUpkeep(read=read, reextract=reextract, finish_world=finish)


@dataclass
class ControllerServices:
    """Everything the view calls down into. Real implementations by default; fakes in tests.

    The types below are named through `controller_wow_wotlk` because that is
    where the shared implementations live: every per-game package re-exports
    the SAME `ConsoleReply`, `AccountResult`, `BackupReport`, `RestorePlan`,
    `RestoreReport` and `InterruptedRestore` objects rather than defining its
    own, so an `isinstance` in this view holds whichever package answered.
    """

    controller: Controller
    logs_source: Callable[[], Iterator[str]]
    send_console: Callable[[str], wotlk_console.ConsoleReply]
    store: ManifestStore | None
    applier: Applier | None
    network_plan: Callable[[Mode], NetworkPlan]
    network_apply: Callable[[NetworkPlan], NetworkReport]
    create_account: Callable[[str, str, int], wotlk_accounts.AccountResult]
    backup: Callable[[], wotlk_maintenance.BackupReport]
    backups_dir: Callable[[], Path]
    plan_restore: PlanRestore
    restore: Callable[[wotlk_maintenance.RestorePlan], wotlk_maintenance.RestoreReport]
    interrupted_restore: Callable[[], wotlk_maintenance.InterruptedRestore | None]
    forget_interrupted: Callable[[], bool]
    dashboard: Callable[[], dashboard_module.Verdict] | None = None
    """One tick of this install's dashboard, or `None` for a game whose block is unmeasured.

    Optional because the per-tree facts the counts need are measured per tree:
    `wow-wotlk` has them (8.1a), and 8.1b, 8.1c and 8.1d add their own. A tab
    without one shows no verdict line rather than an empty or invented one.
    """
    log_snapshot: Callable[[], logsnap.Snapshot] | None = None
    """The same `logsnap.Recorder` the controller was given as its `pre_stop` hook.

    Held here as well so the tab can name the file that was just written: the
    controller's own return value is about stopping, not about evidence.
    """
    channel_setup: ChannelSetup | None = None
    """This install's command-channel setup, for a game that has one (8.2a).

    A small object rather than two callables because the two questions belong
    together: pressing enable and asking where the setup has got to are the same
    state machine seen from two sides.
    """
    database_alone: DatabaseAlone | None = None
    """How to bring this install's database up for a backup, and put it back (T76).

    `None` leaves `back_up()` doing exactly what it did before -- run the
    backup, and let it refuse if the database is down. Every shipped entry wires
    it; a tab that does not is a tab whose backup still works on a running
    server, which is the behaviour this replaces rather than one it breaks.
    """
    bots: BotBrowser | None = None
    """This install's bots, for a game whose marker is measured (8.5a).

    The tab exists only where this is wired: a Bots tab that cannot say which
    accounts are bots would have to show every character on the server, and on
    this install that is 900 rows of which 500 are the answer.
    """
    console_probe: Callable[[str], object] | None = None
    """One command through this install's command channel, for a console tree (8.2e).

    A callable and not the channel object: the tab has no business knowing what
    transport is behind it, and the wiring hands over `AttachChannel(...).send`.
    It is the CHANNEL's send and not the Console tab's own seam:
    what the Server tab shows has to come through the object every later feature
    on this tree will use, or it proves the console works and not the channel.

    `None` everywhere else. A tree with a set-up button has a verified line
    saying a real round trip answered and when, which is the same evidence by a
    better route; two ways to say it would be one more than is true.
    """
    uninstall: Uninstall | None = None
    """This install's Uninstall action, for a family whose removal is gated (8.9a).

    `None` leaves the tab with no uninstall controls at all, which is what a
    game outside 8.9a/8.9b gets. Uninstall is deliberately gated on two FAMILIES
    rather than on one box per game (owner answer 3's usual rule): the mechanism
    is the compose project and the folder, and both of those are the engine's
    rather than the emulator's.
    """
    accounts: AccountAdmin | None = None
    my_party: MyPartySeam | None = None
    """8.6's My Party, on the one tree whose route to it has ever answered.

    `None` everywhere else, and that is not a stub: the route is an
    AzerothCore Lua module, so a CMaNGOS tab gets no My Party control rather
    than a control that sends AzerothCore's commands at a server that has never
    heard of them. Even on WotLK the object refuses every press until the
    SERVER has answered `dml_bridge_ping` in the bridge's own word.
    """
    play: object | None = None
    """8.4a's Characters tab, where this tree has measured what it needs."""
    bot_dashboard: BotDashboardSeam | None = None
    """T127's switch for the bots module's own web dashboard, on Tortoise only.

    `None` for a game whose catalog conf table carries no telemetry key, which
    hides the whole group rather than showing a switch that cannot work.
    """
    bot_pool_rebuild: BotPoolRebuildSeam | None = None
    """T144's "Rebuild random bots…", on Tortoise only (`poolreset.for_entry`).

    `None` everywhere else, which leaves the button absent: the setting it
    writes is TortoiseBots', and the other games' bot modules do not read it.
    """
    steam: steam_module.SteamShortcuts | None = None
    """8.8's two Steam library entries, on Linux and the Steam Deck only.

    `None` on Windows and macOS, and that is what makes the button ABSENT there
    rather than disabled: the checklist's own words are "nothing is drawn on
    Windows or macOS", and a greyed-out control is still something drawn. The
    decision is taken once, in `_steam_seam()`, so no view code branches on the
    operating system.
    """
    """This install's user accounts, for a game whose stores are measured (8.3a).

    One object and not three callables for the reason `channel_setup` is one:
    the read and the two writes share a fact -- which account is the app's own
    -- and splitting them would be three places to remember it.
    """
    module_sql: ModuleSqlRoute | None = None
    """Run this install's importer over the modules on disk, or None if it has none.

    Defaulted, and the default is the honest answer for three of the four
    games: only AzerothCore ships a one-shot import service, so only its
    factory wires this. The cost of a defaulted seam is that it can be
    forgotten for the game that HAS one and every view test would still pass —
    the view is handed a fake — so what the factories really answer is pinned
    in `test_only_the_game_that_names_an_importer_is_wired_a_module_sql_route`.

    Takes the sink the importer's lines are handed to. It is the ONE argument
    because everything else the run needs — which container, which folder,
    which refusals — belongs below this seam, in `docker.apply_module_sql()`,
    which is where 8.7a's "not while the world is running" guard lives.
    """
    module_updates: Callable[[], tuple[apply_module.ModuleUpdate, ...]] | None = None
    """How far behind each installed module is, or None for a game with no modules.

    Defaulted for `module_sql`'s reason and wired by the same test: only
    AzerothCore has a `modules/` folder of git checkouts at all, so the three
    CMaNGOS games get a dead button rather than a press that explains itself.

    Takes nothing and returns rows that already carry their own sentence
    (`apply.ModuleUpdate.line`). The view does not format the figure, because
    8.7a's definition of done is that the number on screen equals the same range
    run by hand — a view that pluralised or defaulted it could drift from the
    seam that was tested.

    It costs one `git fetch` per installed checkout, which is why it is a button
    and not part of the status poll.
    """
    module_notes: Callable[[], Mapping[tuple[str, str], str]] | None = None
    """One extra sentence per Modules-tab row, keyed `(family, id)`, or None for none (T126).

    Read on every reload, like `installed_modules`: it opens small files and
    asks no daemon and no network. Tortoise uses it to say which release of
    TortoiseBots Manager is installed against the server's bot module.
    """
    module_version: Callable[[Path], str | None] | None = None
    """What one clone is AT, as `7c02b1d · 2026-09-01`, or None for a game with no clones.

    The THIRD seam about installed modules, and the third cost: `module_updates`
    fetches (a network round trip per module, so a button), `installed_modules`
    lists directory names (free, so every reload), and this one runs a local
    `git log -1` in ONE clone (cheap, but not free — so it is read lazily, once
    per clone, and cached by `modules_panel.VersionCache`). Splitting it out is
    what keeps T41's refusal honest: a version line folded into either of the
    other two would be paid either per reload or never.

    Takes the clone's PATH rather than an id, because the id-to-folder rule is
    `apply.CLONE_DIRS`'s and the cache already applies it. `None` from the seam
    is "could not say", which draws nothing.
    """
    installed_modules: Callable[[], Mapping[str, frozenset[str]]] | None = None
    """What is installed here per manifest family, or None for a game with no modules.

    T41: the list was built from the manifest STORE alone, so it showed what
    this game could install and never what it had. A player who pointed Yu'lon
    at a server he already ran read that as "None of modules detected", and a
    player who had just installed one correctly saw exactly the same rows he
    saw before (2026-09-12, both in `#-yulon`).

    A separate seam from `module_updates` on purpose. That one asks every
    upstream how far behind it is, costs a `git fetch` per checkout and is
    therefore a button; this one reads directory names and is cheap enough to
    run on every `reload_modules()`. Folding the cheap fact into the expensive
    call is what kept it off the list in the first place.

    Returns ids per family, not paths: the view compares them with
    `manifest.id` for that manifest's own `type` and must not learn where an
    install keeps each family. One folder per family is the point — reading
    `modules/` for all four marked an ale installed because a module of the
    same id was, and never marked a real ale at all (review, 2026-09-12).
    """
    unfinished_modules: Callable[[], Mapping[str, frozenset[str]]] | None = None
    """Which of those clones have an install that never finished, per family (T68).

    The same shape and the same folders as `installed_modules`, and always a
    SUBSET of its answer: it reads the claim this app wrote inside each clone
    and returns the ones marked `install_completed: false`. A row in this set
    keeps its Install button although the folder is on disk, because the state
    it names is exactly "the clone landed and the steps after it did not run" —
    what the T7 direct-SQL guard leaves behind when it refuses an install while
    the world is up.

    A separate seam from `installed_modules` rather than a richer return from
    it, because that answer is shared with the Tuning tab and with
    `_forget_what_is_no_longer_installed()`, and both of them mean "the folder
    is there" — the one thing this fact does not change. `None` for a game with
    no clone folders, which is the same gate `installed_modules` rides on.
    """
    unknown_modules: Callable[[], Mapping[str, Mapping[str, apply_module.Doubt]]] | None = None
    """Which recorded sourceless mods a press stopped on mid-statement, per family (T121).

    `apply.unknown_modules()`: a `pending` mark left in the answers file by a
    mob multiplier's press that never recorded its result (T115). Those ids are
    also in `installed_modules` -- the database may hold them -- and this says
    which of them the row must call `State unknown` rather than `Installed`.
    A separate seam for `unfinished_modules`' reason: the other readers of
    `installed_modules` do not change on it. One small JSON read per reload,
    on the GUI thread beside the folder listing: `reload_modules()` is not a
    job, and the file is the same one `_module_values()` already reads there.
    """
    module_from_link: Callable[[str], Manifest] | None = None
    """Derive a manifest from a link the user pasted, or raise with the refusal.

    `None` for a game with no custom-module route, which greys the button. What
    counts as a link, what the id may be called and which hosts are allowed are
    all the deriving seam's (`module_source.derive_link`, lane A) — the view
    passes the text through untouched and shows whatever sentence comes back.
    It raises rather than returning a refusal object because every caller here
    would immediately have to branch on one, and the applier next to it already
    speaks exceptions.
    """
    module_from_folder: Callable[[Path], Manifest] | None = None
    """Derive a manifest from a folder on this computer, or raise with the refusal.

    Separate from `module_from_link` rather than one call with a union: they
    refuse different things in different words (a host allow-list versus "this
    folder has no src, conf or data"), and a seam that took either would have
    to sort out which it was handed before it could say so.
    """
    module_install_custom: CustomModuleInstall | None = None
    """Install a derived manifest, copying from the folder when one is given.

    See `CustomModuleInstall` for why this is one seam rather than the
    design's `applier.install(..., folder=..., complete=...)`.
    """
    module_replacement_question: Callable[[Manifest], str | None] | None = None
    """What the user must agree to before an install replaces the clone already there (T47).

    `None` back from the seam is "nothing to ask about", which is the ordinary
    answer: no clone at that path, or one of the repository this manifest names.
    A sentence is the one case only the person can decide — a clean checkout of
    a DIFFERENT repository under the same `modules/<id>` — and it is asked
    before any job is queued, because the engine runs off the GUI thread and
    cannot open a dialog. Every other way an install would destroy something
    stays a refusal from the engine and never becomes a question.

    `Applier.replacement_question()` is the whole of it; this seam exists so the
    view can ask without holding an applier (style-guide §3).
    """
    module_forget: Callable[[Manifest], bool] | None = None
    """Drop this app's record of a custom module, answering whether there was one.

    Asked after EVERY successful remove, and its answer is the only thing that
    tells this view a module was custom — the view reads no manifest field to
    decide (design §3.3). A shipped manifest is an OFFER and stays listed
    whether or not it is installed; a derived one is a RECORD of something the
    user brought, and a record of a folder that is gone would be a list row
    whose Install re-clones a link the user just decided against.
    """
    rebuild: install_wiring.RebuildSource | None = None
    """Recompile this install and restart it on the result; None when nothing can.

    The only optional seam here, and the default is None rather than a callable
    that refuses, because the tab greys the button on it: a control that is
    visibly unavailable beats one that is pressed and then explains itself
    (roadmap 6.1, and the same rule the Console tab applies to a missing pty).

    `install_wiring.rebuild_for_app()` is what fills it, including the refusal
    for a server adopted from a WSL distro — which is a fact about the INSTALL,
    not about this view, so the view never asks about distros.
    """
    updates: native.UpdateRoute | None = None
    """Apply the install plan's re-runnable phases to this server; None when it has none.

    The second optional seam, greyed on `None` for the same reason as `rebuild`
    above. `None` here is not a missing wiring: it is the honest answer for
    three of the four games, whose plans declare no `rerun_on_marked` phase at
    all, and `_updates_route()` reads that off the catalog rather than off an id.

    One field holding a pair rather than two optional callables — see
    `native.UpdateRoute` for why the halves must not be able to arrive apart.
    """

    repair_compose: native.ComposeRepairRoute | None = None
    """T106's "Repair server files…" for this install; None where it is not offered.

    `install_wiring.repair_compose_for_app()` answers: the CMaNGOS family, and
    WotLK for the repository's own file a failed update leaves (T170), never a
    server inside a WSL distro. `None` means no banner and no check.
    """

    repair_confs: native.ConfRepairRoute | None = None
    """T137's half of the same button: write a module conf from its `.dist`; None where not offered.

    `install_wiring.repair_confs_for_app()` answers: wherever the catalog names
    `confs_from_dist` (WotLK's `playerbots.conf`). `None` means no check.
    """

    lock_folder: serverlock.FolderLockRoute | None = None
    """T174's third half of the same button: lock the server folder to this Windows account.

    `serverlock.route_for_app()` answers: Windows only, and never a server inside
    a WSL distro. `None` means no check.
    """

    corrections: native.CorrectionRoute | None = None
    """T129's "Apply database corrections…" for this install; None where it is not offered.

    `install_wiring.corrections_for_app()` answers: an entry whose plan marks a
    step `reapply_when_changed`, never a server inside a WSL distro. `None`
    means no banner and no check.
    """

    update_to_latest: native.LatestRoute | None = None
    """Move this install's sources to upstream's newest code and rebuild; None when it cannot.

    The fourth optional seam, hidden rather than greyed on `None`, and that is
    the one difference from the three above it. A greyed control says "this
    exists and is not available now"; for a server adopted from a WSL distro,
    or an entry whose catalog does not offer the route, this control does not
    exist at all and never will for that install. `install_wiring.
    update_to_latest_for_app()` is what answers, and it reads both facts there
    rather than here.

    One field holding five callables rather than five optional fields -- see
    `native.LatestRoute` for why the way BACK must not be able to arrive without
    the way forward.
    """

    adopt: native.AdoptRoute | None = None
    """Record these databases as a finished import, on the person's word; None when it cannot.

    The third optional seam, greyed on `None` for the same reason as the two
    above, and offered to exactly the installs `updates` is offered to: adopting
    buys nothing where no later press would then do anything it cannot do now.

    `None` is only half the greying here, and that is what makes this control
    different from the two above it. The other half is a READING — the databases
    have to say `populated` — and it is not in this dataclass because it is not
    a fact about the wiring: `AdoptRoute.state` is the question, and the tab
    decides when to put it.
    """

    client_dir: Path | None = None
    """This install's recorded client folder, for the Server tab's row (T36).

    A field and not a re-read of `controller.server_dir`'s neighbour, because
    nothing on `Controller` or `ContainerSpec` carries it — every other seam
    below wants it turned into something else first (`_client_dir_for_addons()`
    for the applier, the Steam entry), and the row wants the raw value, `None`
    included, to say which of its three sentences applies.
    """

    play_client_dir: Path | None = None
    """This install's ready-to-play client (T181), or `None` when it has none.

    Beside `client_dir` rather than in its place: the applier and the Steam
    entry were built over THIS folder (`for_entry()`), while `client_dir` stays
    the player's own folder, which is what the Server tab's row names.
    """

    time_zone: server_time_zone.TimeZoneRoute | None = None
    """The Tuning tab's "Server time zone" (T171), bound to this install.

    Wired in `_assemble` for every game whose compose files Yu'lon makes (a
    catalog fact, `time_zone.services`). `None` leaves the tab without it.
    """

    bot_population: botpop.BotPopulationRoute | None = None
    """The Bots tab's "Random bots" box (T99), bound to this install.

    Wired in `_assemble` for every game whose install writes a bot count
    (`bot_population.where`, a catalog fact). `None` leaves the box dead and
    the Tuning tab without the bot card.
    """

    reset_settings: reset_defaults.ResetRoute | None = None
    """The Tuning tab's Reset to default (T94), bound to this install.

    Wired in `_assemble` for every game -- which files are a game's own and how
    each default is made are catalog facts, not per-game decisions. `None` (a
    hand-built services object) leaves the button dead.
    """

    set_client_dir: Callable[[Path | None], None] | None = None
    """Write a new client folder (or clear it with `None`) for THIS install (T36).

    `None` leaves the row with no buttons at all, the same rule `uninstall`
    follows for a game outside 8.9a/8.9b: a control that cannot write anywhere
    is worse than no control. Every factory below leaves this `None` — writing
    it is `main.py`'s job alone, the way `main.py` overwrites `uninstall.forget`,
    because only the running window holds the live `AppState` every tab shares.
    A tab built outside that window (the CLI harness, a factory-only test) gets
    no client-folder controls, which is the honest answer for something with no
    state file to write back into.
    """

    set_play_client_dir: Callable[[Path | None], None] | None = None
    """Record (or clear with `None`) THIS install's ready-to-play client (T181).

    `set_client_dir`'s twin, bound by `main.py` for the same reason and left
    `None` by every factory.
    """

    other_server_dirs: Callable[[], tuple[Path, ...]] | None = None
    """The server folders of every OTHER install Yu'lon knows, on this host (T181).

    Read by Make…'s "Also remove them from your original client": a file
    another server also has a receipt for is never offered. Bound by `main.py`
    from the live `AppState`; `None` (every factory) offers this server's own.
    A WSL-distro install is left out: reading its folder boots its distro (T133).
    """

    pathfinding: Pathfinding | None = None
    """T179: the Server tab's movement-map line and its Start/Stop, where the entry has the job.

    `None` hides the line: every game whose movement maps come with the install.
    """

    world_upkeep: WorldUpkeep | None = None
    """T179 Task 6: the Server tab's map-data and world-update lines and their presses.

    `None` hides both: every game whose update route leaves neither owing.
    """

    characters_withheld: Mapping[str, str] = field(default_factory=dict)
    """Characters verbs (`play.VERBS`) this tree does not offer, each with why (T179).

    The tab draws none of them and says the reasons instead; `play.InstallPlay`
    refuses a press of one as well. Empty everywhere but a tree whose verbs have
    not all been watched to work (Centurion: Revive, held back after T208's live check).
    """

    no_modules_note: str = ""
    """What the Modules tab says in place of an empty list, for a game with no add-ons.

    Empty keeps the panel's own sentence (`modules_panel.NO_MODULES_NOTE`).
    """

    @classmethod
    def for_entry(
        cls,
        entry: CatalogEntry,
        server_dir: Path,
        client_dir: Path | None = None,
        wsl_distro: str | None = None,
        play_client_dir: Path | None = None,
    ) -> ControllerServices:
        """The real wiring for the install at `server_dir`, from THIS game's package.

        The dispatch is a lookup on the catalog id, not a chain of `if`s and not
        a family test. Two of the four games (`wow-tbc` and `wow-vanilla`) are
        the same core, the same prompt, the same schema names and the same
        account scheme — everything a family test could branch on is equal —
        and they still need different packages, because their containers and
        their ready markers differ. So the key is the id, which is the only
        thing that is unique per install.

        An id with no factory raises `UnsupportedGameError` rather than falling
        back to WotLK; that class says what the fallback cost.

        With `play_client_dir` (T181) the factory is handed the ready-to-play
        client as its client folder, so every seam built over one (the module
        applier, the Steam entry) writes and points there; the applier is told
        which folders are the player's own so a receipt from before the switch
        is taken back from the ready-to-play client. `client_dir` is then put
        back to the player's own folder for the Server tab's row. Without one
        the factory's answer is returned as it is.

        Raises:
            UnsupportedGameError: no controller package is wired for `entry.id`.
        """
        factory = _FACTORIES.get(entry.id)
        if factory is None:
            raise UnsupportedGameError(
                f"{entry.name} ({entry.id}) has no controller package in this build, so this "
                f"app cannot manage an install of it. Nothing was opened. The games it can "
                f"manage are: {', '.join(sorted(_FACTORIES))}."
            )
        if play_client_dir is None:
            return factory(entry, server_dir, client_dir, wsl_distro)
        services = factory(entry, server_dir, play_client_dir, wsl_distro)
        if services.applier is not None:
            services.applier.client_origins = _originals_of(play_client_dir, client_dir)
            services.applier.client_game = entry.id
        return replace(services, client_dir=client_dir, play_client_dir=play_client_dir)

    @classmethod
    def for_wotlk(
        cls,
        entry: CatalogEntry,
        server_dir: Path,
        client_dir: Path | None = None,
        wsl_distro: str | None = None,
        play_client_dir: Path | None = None,
    ) -> ControllerServices:
        """`for_entry()` under the name it had while WotLK was the only wiring.

        Kept because `main.py` still spells the call this way and that file is
        not this change's to edit; it dispatches like any other caller, so a
        TBC entry passed to it reaches the TBC package. Prefer `for_entry()`.
        """
        return cls.for_entry(
            entry,
            server_dir,
            client_dir,
            wsl_distro=wsl_distro,
            play_client_dir=play_client_dir,
        )


def _originals_of(play_client_dir: Path, client_dir: Path | None) -> tuple[Path, ...]:
    """The player's own client folder(s) a ready-to-play client stands in for (T181).

    The one its marker says it was made from, and the install's recorded client
    folder when that is another (the player pointed the row elsewhere since).
    An unreadable marker leaves the recorded folder alone to go by.
    """
    marker = play_client.read_marker(play_client_dir)
    found = [marker.source_client_dir] if marker is not None else []
    if client_dir is not None and client_dir not in found:
        found.append(client_dir)
    return tuple(found)


def module_kept_files(
    server_dir: Path, play_client_dir: Path, client_dir: Path | None = None
) -> tuple[Path, ...]:
    """The module client files in a ready-to-play client, relative to it, sorted (T181).

    What Play and Refresh hand `play_client.stale()`/`refresh()` as `keep`: a
    module's patch there under a name the original also has would otherwise be
    replaced by the original's. Read from the receipts in the claims of this
    server's installed modules, moved onto the ready-to-play client the way a
    Remove moves them (`apply.rebased()`), so a patch installed into the player's
    own client before the switch counts too.

    `client_dir` is the install's recorded client folder: the origins are the
    ones `for_entry()` hands the applier (`_originals_of()`), so a receipt a
    Remove would take back from the ready-to-play client is kept here too.
    """
    origins = _originals_of(play_client_dir, client_dir)
    found: set[Path] = set()
    for copy in apply_module.client_receipts(server_dir):
        path = apply_module.rebased(Path(copy.path), play_client_dir, origins)
        if path.is_relative_to(play_client_dir):
            found.add(path.relative_to(play_client_dir))
    return tuple(sorted(found))


def archives_left_out(
    server_dir: Path, play_client_dir: Path, source: Path, client_dir: Path | None = None
) -> tuple[Path, ...]:
    """The original's archives Play and Refresh name as left out of the ready-to-play client.

    Never one of this server's module patches (controller ruling, T181a): one a
    Remove took back from the ready-to-play client while the original kept it
    (`play_client.taken_back()`, the module installed before the switch with
    "Also remove them from your original client" unticked), nor one a module
    still has a receipt for (`module_kept_files()`, its origins and the clones'
    claims). Making the client again would bring such a patch back.
    """
    ignore = (
        *module_kept_files(server_dir, play_client_dir, client_dir),
        *play_client.taken_back(play_client_dir),
    )
    return play_client.left_out_archives(play_client_dir, source, ignore=ignore)


# ------------------------------------------------------ one factory per game
#
# Everything below builds a `ControllerServices` for one game out of that
# game's own `controller_<acronym>` package. What is shared sits in the helpers
# first; what differs — the controller class, the console, the account writer,
# the maintenance binding and the manifest store — is spelled out per game,
# because that is exactly the list of things a per-game package exists to
# answer differently.


def forget_record(game: str, server_dir: Path) -> Callable[[], None]:
    """`state.forget()` made to persist, for a caller that holds no live `AppState`.

    `AppState.forget()` is a method on an in-memory object and does not save, so
    persisting is load -> forget -> save. That is right for the CLI harness and
    for a tab built outside the window; it is WRONG inside the running app,
    where `main.py`'s `build_window()` closure holds one live state object that
    every tab writes into. Re-loading there would forget this install and
    silently undo whatever else the session had remembered -- so `main.py`
    hands the Uninstaller its own seam over that object instead.

    `OSError` is deliberately not caught: `purge.run()` catches it, and this is
    the last step of the action, so an unwritable config dir becomes a warning
    on a finished uninstall rather than a failure of one.
    """

    def forget() -> None:
        from yulon.state import load_state, save_state

        app_state = load_state()
        app_state.forget(game, server_dir)
        save_state(app_state)

    return forget


def _db_password(
    entry: CatalogEntry, server_dir: Path, *, wsl_distro: str | None = None
) -> apply_module.RootPassword:
    """This install's database root password, with the last-resort default.

    The entry may carry the password, or name a file the installer generated it
    into; `db_password()` knows both. The three CMaNGOS games generate one, so
    before it was read they authenticated as root with the literal "password" -
    Start and Stop need no database, which is why it surfaced later, on Create
    account and Backup. The default stays as a last resort so an install whose
    password file has gone missing still gets a tab that can start and stop,
    rather than no tab at all.

    **A server inside a WSL distro gets a reader, not the value (T133).** Every
    tab is built when the app opens, and reading `.db_password` under
    `\\\\wsl.localhost\\` then started a stopped distro. Nothing needs the password
    before something talks to the database, which starts the distro anyway, so
    the file is read at that first use (`apply.mysql_env()`), and kept once a
    read has found it: a first read that fell back to the default (the distro's
    share not reachable yet, the file briefly missing) is asked again the next
    time rather than remembered (T133 review).
    """
    if wsl_distro is None:
        return _read_db_password(entry, server_dir)
    found: list[str] = []

    def read_when_asked() -> str:
        if not found:
            password = entry.install.db_password(server_dir)
            if password is None:
                return _password_unknown(entry, server_dir)
            found.append(password)
        return found[0]

    return read_when_asked


def _read_db_password(entry: CatalogEntry, server_dir: Path) -> str:
    """`_db_password()`'s reading: the entry's password, or the default with a warning."""
    password = entry.install.db_password(server_dir)
    if password is not None:
        return password
    return _password_unknown(entry, server_dir)


def _password_unknown(entry: CatalogEntry, server_dir: Path) -> str:
    """The last-resort default, said in the log when a generated password could not be read."""
    # `db_password()` says None when the entry NAMES a password file and that
    # file cannot be read - which is not the same as "use the default", and
    # silently defaulting here would rebuild the bug this seam exists to close.
    # The tab is still built, because Start and Stop need no database and no tab
    # at all is worse; but the reason every SQL-backed control is about to fail
    # is written down once, here, instead of arriving as "access denied" six
    # clicks later.
    if entry.install.password.mode == "generated":
        logger.warning(
            f"{entry.id}: cannot read {entry.install.password.file} in "
            f"{server_dir}, so the database password is unknown - accounts, backup "
            f"and restore will fail until that file is restored"
        )
    return wotlk_modules.DEFAULT_DB_ROOT_PASSWORD


def _db_client(entry: CatalogEntry) -> str | None:
    """Which client family this game's database image ships (`install.native.db.client`).

    None for an entry with no `native` block, which is what `DockerSql` reads
    as "nothing was declared, keep the order you always had".
    """
    native = entry.install.native
    return native.db.client if native is not None else None


def _sql_for(
    entry: CatalogEntry, password: apply_module.RootPassword, *, wsl_distro: str | None
) -> DockerSql:
    """The read+write SQL seam every SQL-backed control on this tab goes through.

    Three per-install facts reach it here and nowhere else, because this is the
    only layer holding both the entry and the seam:

    * `schemas=` keeps a CMaNGOS install off AzerothCore's `acore_*` names;
    * `client=` keeps it off AzerothCore's `mysql` binary, which `mariadb:11`
      does not ship at all (`apply.mysql_client()` carries the measurement);
    * `wsl_distro=` says which daemon those databases are inside.

    The container is this ENTRY's, not the package's module-level `SPEC.db`, so
    a catalog entry naming a different one cannot end up addressing somebody
    else's database.
    """
    return DockerSql(
        entry.container_spec().db,
        password,
        schemas=entry.schema_map(),
        client=_db_client(entry),
        wsl_distro=wsl_distro,
    )


def _mysql_for(
    entry: CatalogEntry, password: apply_module.RootPassword, *, wsl_distro: str | None
) -> wotlk_maintenance.DockerMysql:
    """The dump/load seam, bound to this entry's own db container.

    `DockerMysql` is one class shared by every game's package (they re-export
    it), and it is built from the entry rather than from the package's
    `mysql_for()` for the reason above: `docker_ctl.SPEC.db` is the catalog's
    answer for the game, and the entry is the catalog's answer for THIS install.

    `client=` for the same reason `_sql_for()` directly above passes it, and
    this function is why that reason is worth repeating: it sat one function
    below a correct sibling, without it, through two commits that fixed exactly
    this in the controller packages. It is the seam the SERVER TAB uses -- the
    real backup and restore buttons for TBC, Vanilla and Tortoise, all three on
    MariaDB -- so unbound it fell back to `mysql`, which `mariadb:11` does not
    ship, whenever the container could not be probed. Found by a review that
    asked "which OTHER builders were missed", after the same defect had already
    been fixed twice (2026-09-03).
    """
    return wotlk_maintenance.DockerMysql(
        entry.container_spec().db,
        password,
        wsl_distro=wsl_distro,
        client=_db_client(entry),
    )


def _database_alone(
    spec: docker.ContainerSpec, server_dir: Path, *, wsl_distro: str | None
) -> DatabaseAlone:
    """The two halves of "bring the database up for a backup or a restore, then put it back".

    T76 for the backup, T216 for the restore. One factory and four call sites,
    because the four games must not disagree about this: the reason both need
    it is identical in all four (a `mysqldump` or `mysql` through `docker exec`
    needs the container to exist and be running), and so is the reason it is
    stopped again.

    `because` is the caller's, since T216: it completes `docker.start_database()`'s
    timeout sentence and says what was not done rather than what was attempted
    -- a user reading *"…did not report healthy within 180s, so no backup was
    taken"* knows the state their server is in, which is the whole job of that
    sentence. Bound here it was the backup's alone, and a restore whose
    database never came up would have said a backup was not taken.

    `stop_containers([spec.db])` and not `stop_staged()`: only the container
    this started may be stopped. `stop_staged()` takes the compose project down,
    and a backup that stopped a server somebody was playing on would be a far
    worse press than one that failed.

    **A start that fails puts back what it started** (T216). `start_database()`
    raises when the container does not report healthy in time, and by then
    `compose up -d` has already started it: the press failed, and left a
    database up that nobody asked to run -- after a restore that said "nothing
    was restored". So the census is taken HERE first: a database that was down
    before this call and is up after a failed start was started by it, and is
    stopped again -- "is up" asked again after the failure, because a `compose
    up` that failed outright may have created nothing, and a `docker stop` of
    nothing fails and would have reported a start that never happened (review
    round 1). If even that stop fails, the sentence says the database may still
    be running and names the command. One that was already up returns False
    at once, exactly as `start_database()` would, so nothing this did not start
    is ever stopped. A census that fails raises before anything is started, and
    stops nothing.
    """

    def bring_up(because: str) -> bool:
        if spec.db in set(docker.status(wsl_distro=wsl_distro)):
            return False
        try:
            return docker.start_database(spec, server_dir, because=because, wsl_distro=wsl_distro)
        except docker.DockerCommandError as exc:
            # Asked again rather than assumed (review round 1): when `compose up`
            # itself failed there may be no container at all, and a `docker stop`
            # of it fails too. A census that will not answer cannot rule the
            # container out, so the stop is tried.
            try:
                up = spec.db in set(docker.status(wsl_distro=wsl_distro))
            except docker.DockerCommandError:
                up = True
            if not up:
                raise
            try:
                docker.stop_containers([spec.db], wsl_distro=wsl_distro)
            except docker.DockerCommandError as stop_exc:
                raise docker.DockerCommandError(
                    f"{exc} {spec.db} may still be running: it could not be stopped again "
                    f"({stop_exc}). `docker stop {spec.db}` stops it."
                ) from exc
            raise

    return DatabaseAlone(
        bring_up=bring_up,
        take_down=lambda: docker.stop_containers([spec.db], wsl_distro=wsl_distro),
    )


def _no_manifest_store(entry: CatalogEntry) -> ManifestStore | None:
    """None, and a warning if the catalog has since said otherwise.

    Only `manifests/wow-wotlk/` existed when these factories were written and
    only `controller_wow_wotlk` has a `modules.py`, so the other three games
    have no store to open and their Modules tab says so. If one of them is
    given manifests, the honest failure is this line in the log rather than a
    tab quietly offering AzerothCore's modules for a CMaNGOS server.
    """
    if entry.has_manifests:
        logger.warning(
            f"{entry.id} now has manifests, but there is no modules.py in its controller "
            f"package, so the Modules tab has nothing to offer for it"
        )
    return None


def _steam_seam(
    entry: CatalogEntry, server_dir: Path, client_dir: Path | None
) -> steam_module.SteamShortcuts | None:
    """8.8's Add to Steam..., or `None` on the two platforms it is not for.

    The one place in this file that asks what operating system this is, and it
    asks once: 8.8 is "Linux and Steam Deck only -- nothing is drawn on Windows
    or macOS", and the way to draw nothing is to hand the view no seam, exactly
    as a game without an uninstall route is handed no `uninstall`.
    """
    if platform.detect() != "linux":
        return None
    return steam_module.SteamShortcuts(
        game=entry.name, server_dir=server_dir, client_dir=client_dir
    )


def _assemble(
    entry: CatalogEntry,
    server_dir: Path,
    *,
    client_dir: Path | None,
    wsl_distro: str | None,
    controller: Controller,
    sql: DockerSql,
    send_console: Callable[[str], wotlk_console.ConsoleReply],
    create_account: Callable[[str, str, int], wotlk_accounts.AccountResult],
    store: ManifestStore | None,
    applier: Applier | None,
    backup: Callable[[], wotlk_maintenance.BackupReport],
    plan_restore: PlanRestore,
    restore: Callable[[wotlk_maintenance.RestorePlan], wotlk_maintenance.RestoreReport],
    dashboard: Callable[[], dashboard_module.Verdict] | None = None,
    log_snapshot: logsnap.Recorder | None = None,
    channel_setup: ChannelSetup | None = None,
    accounts: AccountAdmin | None = None,
    my_party: MyPartySeam | None = None,
    play: object | None = None,
    bots: BotBrowser | None = None,
    console_probe: Callable[[str], object] | None = None,
    uninstall: Uninstall | None = None,
    module_sql: ModuleSqlRoute | None = None,
    module_updates: Callable[[], tuple[apply_module.ModuleUpdate, ...]] | None = None,
    installed_modules: Callable[[], Mapping[str, frozenset[str]]] | None = None,
    unfinished_modules: Callable[[], Mapping[str, frozenset[str]]] | None = None,
    unknown_modules: Callable[[], Mapping[str, Mapping[str, apply_module.Doubt]]] | None = None,
    module_version: Callable[[Path], str | None] | None = None,
    module_notes: Callable[[], Mapping[tuple[str, str], str]] | None = None,
    module_from_link: Callable[[str], Manifest] | None = None,
    module_from_folder: Callable[[Path], Manifest] | None = None,
    module_install_custom: CustomModuleInstall | None = None,
    module_replacement_question: Callable[[Manifest], str | None] | None = None,
    module_forget: Callable[[Manifest], bool] | None = None,
) -> ControllerServices:
    """The seams that are the same sentence for every game, plus the ones that are not.

    What is here is here because it takes no per-game decision: following a
    container's log, planning the network, and the three backup-directory
    questions, which are path arithmetic under the server dir and are the same
    function object in all four packages.
    """
    spec = entry.container_spec()
    return ControllerServices(
        client_dir=client_dir,
        steam=_steam_seam(entry, server_dir, client_dir),
        controller=controller,
        logs_source=lambda: docker.follow_logs(spec.world, wsl_distro=wsl_distro),
        send_console=send_console,
        store=store,
        applier=applier,
        network_plan=lambda mode: networking.plan(
            entry, mode, bindings=_safe_bindings(wsl_distro=wsl_distro)
        ),
        network_apply=lambda plan: networking.apply(plan, sql=sql, server_dir=server_dir),
        create_account=create_account,
        backup=backup,
        # HERE, in the shared half, for the rebuild's reason one line further
        # down: bringing a database up for a backup takes no per-game decision
        # at all -- the container name is `ContainerSpec`'s and the compose
        # service is the one `docker.start_database()` already reads off it --
        # and wiring it once is what makes "every game's Backup button works on
        # a stopped server" true by construction rather than by remembering it
        # four times. Four copies is how `_for_tortoise` came to be the one
        # factory that never bound `play`.
        database_alone=_database_alone(spec, server_dir, wsl_distro=wsl_distro),
        backups_dir=lambda: wotlk_maintenance.backups_dir(server_dir),
        plan_restore=plan_restore,
        restore=restore,
        interrupted_restore=lambda: wotlk_maintenance.interrupted_restore(server_dir),
        forget_interrupted=lambda: wotlk_maintenance.forget_interrupted_restore(server_dir),
        dashboard=dashboard,
        log_snapshot=log_snapshot,
        uninstall=uninstall,
        channel_setup=channel_setup,
        accounts=accounts,
        my_party=my_party,
        play=play,
        bots=bots,
        console_probe=console_probe,
        # Defaulted to None here rather than demanded from every factory: three
        # of the four games name no import service at all, and a keyword they
        # would all have to pass as None is a keyword that says nothing. What
        # the WotLK factory passes instead is spelled there, next to the
        # `import_service` it is conditional on.
        module_sql=module_sql,
        # Defaulted for the same reason and passed by the same factory: a game
        # with no `modules/` folder of checkouts has nothing to count.
        module_updates=module_updates,
        # T41's cheap twin of the line above, and conditional on the same
        # flag: a game with no `modules/` folder has nothing to mark.
        installed_modules=installed_modules,
        # T68's reading of the same folders, on the same flag once more: it
        # opens the claim inside each clone the line above listed.
        unfinished_modules=unfinished_modules,
        # T121: the sourceless mods a press stopped on mid-statement, read from
        # the same answers file `installed_modules` reads them from.
        unknown_modules=unknown_modules,
        # T44's version line, on the same flag again: it reads a clone's own
        # `.git`, and a game with no clones has none to read.
        module_version=module_version,
        module_notes=module_notes,
        # Defaulted for the same reason again: the four seams behind "Install
        # from link…" and "Install from folder…" belong to the one game whose
        # modules are checkouts under `modules/`, and that factory passes them.
        module_from_link=module_from_link,
        module_from_folder=module_from_folder,
        module_install_custom=module_install_custom,
        # T47's question, on the same flag as the four above: it is about the
        # clone an install of a derived manifest would land on, so it belongs
        # to the games that have such clones and to no other.
        module_replacement_question=module_replacement_question,
        module_forget=module_forget,
        # HERE, in the shared half, and not in the four per-game factories. A
        # rebuild takes no per-game decision at all — the engine is chosen from
        # `catalog.json` by `installer_for()`, and every family's stage tuple
        # carries the `build` and `ready` stages `rebuild_stages()` selects — so
        # wiring it once is what makes "every tab offers it" true by
        # construction rather than by remembering it four times. Only WotLK ever
        # prints "REBUILD required", but a CMaNGOS worldserver is compiled from
        # the same kind of checkout and its users patch it the same way.
        rebuild=install_wiring.rebuild_for_app(entry, server_dir, wsl_distro=wsl_distro),
        # HERE for the same reason the rebuild is, and offered to far fewer
        # installs: the phases exist or they do not, and that is a fact about
        # `catalog.json` which every game's tab reads the same way.
        updates=_updates_route(entry, server_dir, wsl_distro=wsl_distro),
        # Beside the updates route and gated on the same two facts, because it
        # exists for the install that route refuses: a server made by the shell
        # scripts, which carries no marker row and which T14's button therefore
        # cannot reach (T19).
        adopt=_adopt_route(entry, server_dir, wsl_distro=wsl_distro),
        # HERE for the rebuild's reason -- it takes no per-game decision that is
        # not already in `catalog.json` -- and wired through `install_wiring`
        # rather than assembled inline the way `_updates_route()` is, because
        # both of its refusals are facts about the INSTALL (the entry's flag,
        # and the WSL distro whose daemon `native.Seams` cannot address) and
        # that module is where the rebuild's identical refusal already lives.
        update_to_latest=install_wiring.update_to_latest_for_app(
            entry, server_dir, wsl_distro=wsl_distro
        ),
        # T94. HERE for the rebuild's reason: the file set and how each default
        # is made are catalog facts every game's tab reads the same way. The
        # WSL refusal lives in the route, where the distro is known.
        reset_settings=reset_defaults.route_for_app(entry, server_dir, wsl_distro=wsl_distro),
        # T106. Here for the rebuild's reason: which installs are offered it is a
        # fact of `catalog.json` (the family) and of the install (its distro),
        # both answered in `install_wiring`.
        repair_compose=install_wiring.repair_compose_for_app(
            entry, server_dir, wsl_distro=wsl_distro
        ),
        # T137. Here for T106's reason: which installs are offered it is a fact
        # of `catalog.json` (`confs_from_dist`), answered in `install_wiring`.
        repair_confs=install_wiring.repair_confs_for_app(entry, server_dir, wsl_distro=wsl_distro),
        # T174. Here for T106's reason, and offered to every game: whether a
        # folder has a DACL to lock is a fact of the platform and the distro.
        lock_folder=serverlock.route_for_app(server_dir, wsl_distro=wsl_distro),
        # T129. Here for the same reason: which steps may be offered again is a
        # fact of `catalog.json`, and the distro one of the install.
        corrections=install_wiring.corrections_for_app(entry, server_dir, wsl_distro=wsl_distro),
        # T99. HERE for the same reason: where a game keeps its bot count is a
        # catalog fact. Files only, so a server inside a WSL distro is served too.
        bot_population=botpop.bot_count_route(entry, server_dir),
        # T171. HERE for T99's reason: the file and the services are catalog
        # facts, and it is files only, so a server inside a WSL distro is served.
        time_zone=server_time_zone.time_zone_route(entry, server_dir),
        # T179. HERE for T171's reason: whether a server makes its movement maps
        # in the background is a catalog fact (`mmaps.background_block`).
        pathfinding=_pathfinding(entry, server_dir, wsl_distro=wsl_distro),
        # T179 Task 6. HERE for the same reason: whether an update can leave the
        # map data or the world tables owing is the entry's engine's fact.
        world_upkeep=_world_upkeep(entry, server_dir, wsl_distro=wsl_distro),
    )


def _updates_route(
    entry: CatalogEntry, server_dir: Path, *, wsl_distro: str | None
) -> native.UpdateRoute | None:
    """The updates control's two halves for this install, or None when it has none.

    Two reasons to answer None, and they are different facts:

    * **The plan declares no re-runnable phase.** Three of the four games, and
      it is read off the catalog (`native.update_phases()`) rather than off an
      id, so a flag added to another entry's plan reaches the button with no
      code change here.
    * **The server lives inside a WSL distro.** `native.Seams` addresses the
      local daemon and erases `wsl_distro` (its own docstring records the
      boundary), so a press would ask THIS Docker about a container it has never
      heard of. The guard would then refuse on `None` — safe, and saying the
      wrong thing. Withholding the control says the true one, which is the rule
      the app already applies to a missing pty and to a game with no manifests.

    The engine is built inside each callable, per call, for the reason
    `install_wiring.rebuild_for_app()` gives: four seams and an import gate for
    every tab the app opens, for a control most of them will never press.
    """
    if wsl_distro is not None or not native.update_phases(entry):
        return None
    options = InstallOptions(server_dir=server_dir)
    # `wsl_distro` is None past the guard above; it is passed anyway so the day
    # this route is opened to a distro (as T125 opened Rebuild and Update to
    # latest) the engine already asks the right Docker.
    return native.UpdateRoute(
        confirmation=lambda: install_wiring.installer_for_app(
            entry, wsl_distro=wsl_distro
        ).update_confirmation(options),
        press=lambda cancel: install_wiring.installer_for_app(
            entry, wsl_distro=wsl_distro
        ).update_databases(options, cancel=cancel),
    )


def _adopt_route(
    entry: CatalogEntry, server_dir: Path, *, wsl_distro: str | None
) -> native.AdoptRoute | None:
    """The adopt control's three parts for this install, or None when it has none.

    The same two refusals `_updates_route()` makes, read the same way and for
    the same reasons — the plan declares no re-runnable phase, or the server
    lives inside a WSL distro whose daemon `native.Seams` cannot address. They
    are asked twice rather than once because they are two controls: a future
    entry that gained a marker but no flagged phase would want the answer to
    differ, and reading `services.updates is not None` here would make one
    control's wiring a fact about the other's.

    Adopting where no phase is flagged is refused rather than allowed as
    harmless: the row it writes is a claim about the databases that nothing
    takes back, and a press whose only effect is that claim is a cost with
    nothing on the other side of it.

    The engine is built inside each callable, per call, for
    `install_wiring.rebuild_for_app()`'s reason — including inside `state`,
    which the tab asks each time the database comes up.
    """
    if wsl_distro is not None or not native.update_phases(entry):
        return None
    options = InstallOptions(server_dir=server_dir)
    return native.AdoptRoute(
        state=lambda: install_wiring.installer_for_app(entry, wsl_distro=wsl_distro).adopt_state(
            options
        ),
        confirmation=lambda: install_wiring.installer_for_app(
            entry, wsl_distro=wsl_distro
        ).adopt_confirmation(options),
        press=lambda cancel: install_wiring.installer_for_app(
            entry, wsl_distro=wsl_distro
        ).adopt_as_imported(options, cancel=cancel),
    )


def _record_backed_keys(store: ManifestStore) -> Callable[[], frozenset[str]]:
    """The mods whose installed state is the answers-file record, read once on first use (T121).

    `apply.relative_keys()` over the store's `mod` family -- the four mob
    multipliers, the only relative manifests, and all of them SQL mods. Lazy
    because building the services must not read the catalog; once because the
    shipped set does not change while the app runs. An unreadable record puts
    exactly these in doubt (fix wave).
    """
    cached: list[frozenset[str]] = []

    def keys() -> frozenset[str]:
        if not cached:
            cached.append(apply_module.relative_keys(store.load_all("mod")))
        return cached[0]

    return keys


def _for_wotlk(
    entry: CatalogEntry,
    server_dir: Path,
    client_dir: Path | None,
    wsl_distro: str | None,
) -> ControllerServices:
    """AzerothCore: the base `Controller`, the only import gate, the only manifest store."""
    spec = entry.container_spec()
    record_backed = _record_backed_keys(wotlk_modules.store())
    password = _db_password(entry, server_dir, wsl_distro=wsl_distro)
    sql = _sql_for(entry, password, wsl_distro=wsl_distro)
    mysql = _mysql_for(entry, password, wsl_distro=wsl_distro)
    # The probe pair is `(None, None)` for a game that names no one-shot import
    # service — `install_wiring.import_gate_for()` carries the reasoning, once,
    # for this tab, the Catalog tab and the CLI. `wsl_distro=` because that
    # function builds its own two seams and they address a daemon too.
    #
    # Its password is `fixed_db_password(entry)` and NOT the file read above,
    # which is right and is not an oversight: a gate is built only for an entry
    # that names an import service, wow-wotlk is the only one, and its plan is
    # `fixed`. The reverse substitution — this function's `sql`/`mysql`/applier
    # taking the fixed value — is the closed bug `_db_password()` describes.
    probe, reset = install_wiring.import_gate_for(entry, wsl_distro=wsl_distro)
    # 8.1a. Both are WotLK's alone for now: the counts need per-tree facts the
    # catalog only carries for this entry, and 8.1b, 8.1c and 8.1d gate their
    # own. The SAME recorder object is the controller's pre-stop hook and the
    # tab's way of naming the file, so the tab reports the snapshot that was
    # actually taken rather than one it re-derives.
    recorder = logsnap.Recorder(
        spec,
        server_dir,
        game=entry.id,
        logs_dir=platform.config_dir() / "logs",
        wsl_distro=wsl_distro,
    )
    watcher = dashboard_module.Dashboard(spec, entry, server_dir, sql=sql, wsl_distro=wsl_distro)
    # 8.2a. The account is made through this game's own SRP6 row path — the seam
    # the Accounts tab already uses — so the channel's account is created the
    # way every other account on this install is.
    channel = channel_setup.InstallChannel(
        entry,
        server_dir,
        templates_root=resources.installers_dir(),
        install_id=composegen.install_id(server_dir),
        # The scheme is the entry's or nothing: `or "azerothcore"` stood here
        # until 2026-09-09, which handed an entry whose scheme is unmeasured the
        # one guess that inserts cleanly into a table with those columns and
        # never authenticates against one without them (T12).
        create=lambda name, pw, level: wotlk_accounts.create_account(
            sql,
            name,
            pw,
            gm_level=level,
            scheme=wotlk_accounts.checked_scheme(entry.accounts.scheme, entry.id),
        ),
        # The repair seam, and the reason it is a different function from
        # `create`: `create_account` deliberately refuses to re-salt a row that
        # exists, because silently changing an owner's password is worse than
        # refusing. `reset_own_password` refuses every name that is not this
        # app's own, so the one account it can rewrite is the one it made.
        # `scheme` is bound the same way `create` binds it two lines above --
        # an entry with no declared scheme is refused by `checked_scheme`
        # rather than falling through to the writer's own AzerothCore default
        # (T22; the create= binding refused this same entry, the reset= one
        # next to it did not).
        reset=lambda name, pw: wotlk_accounts.reset_own_password(
            sql,
            name,
            pw,
            scheme=wotlk_accounts.checked_scheme(entry.accounts.scheme, entry.id),
        ),
        channel_for=lambda endpoint: channel_module.SoapChannel(
            endpoint=endpoint,
            state_of=lambda: docker.container_state(spec.world, wsl_distro=wsl_distro),
        ),
    )
    # 8.3a. The list is a database read and the two changes are the server's
    # own commands, which is owner answer 7 rather than a layering choice. Both
    # halves are given the app's own account name -- the read leaves it out,
    # the writes refuse it.
    accounts_admin = useraccounts.InstallAccounts(
        entry,
        server_dir,
        sql=sql,
        channel_for_saved=channel.live_channel,
        app_account=channel_setup.account_name(composegen.install_id(server_dir)),
    )
    # 8.4a. The Characters tab, over the same channel the account writes use
    # and the same reader the lists use: this app reads rows and the server
    # changes them (owner answer 7).
    characters_admin = play_module.InstallPlay(
        entry,
        server_dir,
        sql=sql,
        channel_for_saved=channel.live_channel,
    )
    # One applier for the Modules tab, named here so the custom-module seam
    # below is built over the SAME object the shipped route installs with.
    #
    # 8.7a, and the two seams that make its guard a defence rather than a
    # capability (T7). `world_running` is `docker.world_running()`, not My
    # Party's `container_state(...).settled` below: that property answers
    # `False` when Docker will not say, and through this guard `False` is
    # fail-OPEN — the one answer that lets SQL into a live world's tables. Both
    # are lambdas because the answer changes between the moment this tab is
    # built and the moment somebody presses Install.
    #
    # `start_database` is what makes the refusal's own sentence — *"Press Stop,
    # then install again"* — a thing that succeeds. The app's Stop takes the
    # database down with the world, so before T7 the retry died on
    # `container ... is not running` (T2's press, 2026-09-09). The world is
    # never started here: only the database, alone, which is the state the
    # guard permits.
    #
    # `dbc` is T62. Without it every `server_dbc` step -- mod-arac's race/class
    # DBCs, the SoD keg's spells -- was reported skipped and never reached the
    # server. Only an entry naming its client-data service can be given one:
    # that service is the only thing that mounts the data volume read-write.
    module_applier = (
        wotlk_modules.applier(
            server_dir,
            sql=sql,
            client_dir=client_dir,
            dbc=(
                wotlk_modules.dbc_copier(
                    server_dir, service=entry.containers.client_data, wsl_distro=wsl_distro
                )
                if entry.containers.client_data
                else None
            ),
            world_running=lambda: docker.world_running(spec.world, wsl_distro=wsl_distro),
            start_database=lambda: docker.start_database(
                spec, server_dir, because="no SQL was run", wsl_distro=wsl_distro
            ),
        )
        if entry.has_manifests
        else None
    )
    return _assemble(
        entry,
        server_dir,
        client_dir=client_dir,
        wsl_distro=wsl_distro,
        dashboard=watcher.tick,
        log_snapshot=recorder,
        channel_setup=channel,
        accounts=accounts_admin,
        play=characters_admin,
        # 8.6. The one tree whose bridge has ever answered, and the object
        # refuses every press until it answers again: the facts are re-read per
        # press, not cached, because the world can be restarted and
        # `mod_ale.conf` edited while the tab is open. `world_running` is asked
        # of the same `docker.container_state` the Server tab draws from, so
        # the group and the status line cannot disagree about a stopped world.
        my_party=party.InstallParty(
            entry,
            server_dir,
            sql=sql,
            channel_for_saved=channel.live_channel,
            container=spec.world,
            wsl_distro=wsl_distro,
            world_running=lambda: docker.container_state(spec.world, wsl_distro=wsl_distro).settled,
            # T26, and the only line of this factory the ticket needed: "Link
            # an account" writes two rows into `acore_playerbots`, and it is
            # the same `DockerSql` the reads already go through. A seam of its
            # own rather than a wider `sql`, so `dbreads.SqlReader` keeps the
            # guarantee its own docstring makes -- what is not in the type
            # cannot be called through it. Without this line the control could
            # only ever say it has no route.
            link_writer=sql,
            # T26 round 2, and the second line of this factory the ticket needs.
            # `members()` unions the bot marker's rows with the characters this
            # app added through `add_named`, and that record has to outlive the
            # panel -- it is rebuilt on every tab switch and the app is closed
            # between sessions. Keyed by install id under `config_dir()`, so two
            # installs on one machine do not read each other's parties. Without
            # this line the panel forgets an altbot the moment the tab is left,
            # and says "This character's party has no bots in it yet." under the
            # sentence that just reported one joining.
            altbots=party.AltbotMemory(
                party.altbot_store_path(), composegen.install_id(server_dir)
            ),
        ),
        # 8.9a. WotLK first, and Vanilla in 8.9b; the four seams this needs are
        # the ones every install has. `forget` is the default that reads and
        # rewrites `state.json` -- `main.py` replaces it on the tab it builds,
        # because THAT process holds one live `AppState` and re-loading from
        # disk mid-session would clobber whatever else the session changed.
        uninstall=purge.Uninstaller(
            game=entry.id,
            server_dir=server_dir,
            spec=spec,
            image_refs=composegen.built_image_refs(entry, server_dir),
            logs_dir=platform.config_dir() / "logs",
            wsl_distro=wsl_distro,
            forget=forget_record(entry.id, server_dir),
        ),
        # 8.5a. The marker is resolved per read rather than once at start-up:
        # it lives in a conf file the user can change while the app is open,
        # and a list built on a stale marker is a list of the wrong characters.
        bots=_BotBrowser(entry, server_dir, sql),
        controller=Controller(
            spec,
            server_dir,
            wsl_distro=wsl_distro,
            import_probe=probe,
            reset_unfinished=reset,
            pre_stop=recorder,
        ),
        sql=sql,
        # Three facts, from three different places, and the command needs all of
        # them: WHICH container (the spec), how to recognise this server's
        # console prompt (the entry - CMaNGOS does not print AzerothCore's), and
        # which daemon that container is inside (the distro). Without the last
        # one the attach goes to the local daemon, which has never heard of
        # `ac-worldserver`, so every console line came back as a docker error
        # rather than as a reply.
        send_console=lambda cmd: wotlk_console.send_command(
            cmd,
            container=spec.world,
            prompt=entry.console.prompt,
            prompt_precedes_answer=entry.console.prompt_precedes_answer,
            wsl_distro=wsl_distro,
        ),
        # `gm_level` is passed through rather than defaulted here: the guide
        # pairs every `account create` with `account set gmlevel ... 3`, and
        # copying that would hand administrator to every account made from the
        # tile. The spin box defaults to 0 and the user raises it.
        create_account=lambda name, pw, gm: wotlk_accounts.create_account(
            sql,
            name,
            pw,
            gm_level=gm,
            # The tab disables its button for an entry that declares no scheme;
            # this is the seam under it refusing rather than guessing (T12).
            scheme=wotlk_accounts.checked_scheme(entry.accounts.scheme, entry.id),
        ),
        store=wotlk_modules.store() if entry.has_manifests else None,
        applier=module_applier,
        # The other half of installing a module, and until now the half with no
        # button: `applier` clones the module and activates its conf, leaving
        # `data/sql/db-world/*.sql` "to ac-db-import on next start" — and no
        # Start ever reaches the importer (`docker.start_staged()` names the
        # three long-running services). This is that next start.
        #
        # Conditional on the same fact the repair's probe is, `import_service`,
        # and for the same reason: an entry that names no one-shot importer has
        # nothing to run, and a disabled button is a better answer than a
        # refusal delivered after a click. The refusal still exists underneath
        # (`docker.apply_module_sql()` opens with it), so this condition is a
        # courtesy and not the guard.
        module_sql=(
            (
                lambda output: wotlk_modules.apply_module_sql(
                    server_dir, output=output, wsl_distro=wsl_distro
                )
            )
            if spec.import_service
            else None
        ),
        # 8.7a's other half, and a different condition from the one above on
        # purpose: `import_service` is about whether a one-shot can APPLY, while
        # this is about whether there are git checkouts to COUNT. They happen to
        # agree for all four games today — only AzerothCore has both — and tying
        # this to `import_service` would make that coincidence load-bearing for
        # a future core that compiles modules and imports differently.
        module_updates=(
            (lambda: wotlk_modules.module_updates(server_dir)) if entry.has_manifests else None
        ),
        # T41: the cheap half of the same question, on every reload. Bound to
        # the same `has_manifests` flag, so the three CMaNGOS games — which have
        # no modules folder — keep a list of the catalog and nothing else.
        # T121: the folders plus the answers file's record, because the four
        # mob multipliers leave no folder and read Not installed for ever
        # without it -- and `conflicts_with` never saw them.
        installed_modules=(
            (lambda: apply_module.installed_modules(server_dir, record_backed()))
            if entry.has_manifests
            else None
        ),
        unknown_modules=(
            (lambda: apply_module.unknown_modules(server_dir, record_backed()))
            if entry.has_manifests
            else None
        ),
        # T68: which of those clones stopped part-way through their install,
        # read from the claim this app writes inside each one. Bound to the same
        # flag for the same reason -- there is no folder to open otherwise.
        unfinished_modules=(
            (lambda: apply_module.unfinished_clones(server_dir)) if entry.has_manifests else None
        ),
        # T44 item 1. `RunnerGit` and not the containerized git: this is a
        # local read of a folder the user can see, it runs once per clone, and
        # a `docker run` per module to print a sha would cost more than the
        # line is worth. A machine with no host git answers `None` and the rows
        # simply show nothing, which is the documented outcome.
        module_version=(RunnerGit().head_version if entry.has_manifests else None),
        # A module from a link or a folder (design page, lane C's four seams),
        # wired once lanes A and B were on the branch (2026-09-08). Lane C
        # left this as a comment naming the four lines because the objects
        # that fill them did not exist on its tree; they do now. Gated on the
        # same object as `applier=` rather than on `entry.has_manifests`
        # directly, because `install_custom` runs over THAT applier — the one
        # the shipped route uses — and a custom module must not be installed
        # against a second one. `store()` already carries lane A's user layer
        # by default, which is what puts a derived manifest into the list on
        # the next start.
        module_from_link=wotlk_modules.derive_link if module_applier is not None else None,
        module_from_folder=wotlk_modules.derive_folder if module_applier is not None else None,
        module_install_custom=(
            wotlk_modules.install_custom(module_applier) if module_applier is not None else None
        ),
        module_replacement_question=(
            wotlk_modules.replacement_question(module_applier)
            if module_applier is not None
            else None
        ),
        module_forget=wotlk_modules.forget if module_applier is not None else None,
        # `wsl_distro=` as well as the distro-aware `mysql`: the dump goes
        # through `docker exec`, but before it runs, maintenance censuses the
        # containers with `docker ps` — a second question, to the same daemon,
        # that was going to the Windows host. On a machine whose only Docker is
        # inside the distro that is the one with no Docker on it, so Back up now
        # answered "Docker could not be found on this machine" while the Console
        # tab, one seam over, was attached and streaming (Discord report,
        # 2026-08-27).
        backup=lambda: wotlk_maintenance.backup(
            server_dir,
            mysql,
            spec=spec,
            core_databases=entry.core_databases(),
            wsl_distro=wsl_distro,
        ),
        plan_restore=lambda path, can_start_database=False: wotlk_maintenance.plan_restore(
            path,
            server_dir,
            spec=spec,
            wsl_distro=wsl_distro,
            can_start_database=can_start_database,
        ),
        # `confirm=plan.token` is not a rubber stamp: the token can only come
        # from a plan, a plan can only come from a real file, and the human
        # confirmation is the dialog the view puts in front of this call. What
        # the token buys is that no confirmation can be spelled `True`.
        restore=lambda plan: wotlk_maintenance.restore(
            plan,
            mysql,
            confirm=plan.token,
            spec=spec,
            # Bound here for the same reason `backup` binds it four lines up, and
            # missed when the CMaNGOS wrappers were fixed on 2026-09-04. WotLK has
            # no per-game maintenance wrapper -- this lambda IS its call site -- so
            # `restore()`'s new `core_databases` default applied here unbound. It
            # was harmless only by coincidence: this entry's databases happen to be
            # the three the default names. An AzerothCore-family entry that spelled
            # them differently would have reproduced the exact bug that fix removed,
            # on the tab whose Backup button was already correct.
            core_databases=entry.core_databases(),
            wsl_distro=wsl_distro,
        ),
    )


def _for_tbc(
    entry: CatalogEntry,
    server_dir: Path,
    client_dir: Path | None,
    wsl_distro: str | None,
) -> ControllerServices:
    """WoW TBC (CMaNGOS), through `controller_wow_tbc`.

    `client_dir` is accepted and passed on. Nothing in `manifests/wow-tbc/`
    has a `client[]` step today — a CMaNGOS "module" is a conf activation or a
    SQL mod (roadmap 8.7b, `controller_wow_tbc.modules`) — but the applier is
    real now, so handing it the folder the user picked is one binding rather
    than a `del` that a future manifest would have to come back and undo.

    No `import_probe` is handed to the controller, which is `TbcController`'s
    own decision restated at the call site: the Repair button's only action is
    `docker.repair_import()`, whose first refusal is "this game does not say
    which compose service imports its databases" — and this entry names none.
    `Controller.import_state()` then answers `unreadable`, which is not
    `repairable`, so nothing is offered; `_show_repair()` gates on the same
    fact a second time.
    """
    password = _db_password(entry, server_dir, wsl_distro=wsl_distro)
    sql = _sql_for(entry, password, wsl_distro=wsl_distro)
    mysql = _mysql_for(entry, password, wsl_distro=wsl_distro)
    # 8.1b, and every fact under these two is this tree's own: `characters`,
    # `realmd`, and a bot marker that is an account prefix with no registry
    # table behind it. The seams are the same; nothing about them is inherited.
    spec = entry.container_spec()
    recorder = logsnap.Recorder(
        spec,
        server_dir,
        game=entry.id,
        logs_dir=platform.config_dir() / "logs",
        wsl_distro=wsl_distro,
    )
    watcher = dashboard_module.Dashboard(spec, entry, server_dir, sql=sql, wsl_distro=wsl_distro)
    # 8.2c. The same seam as 8.2a and a different enable route, which is the
    # whole per-tree difference: CMaNGOS reads no environment, so this entry's
    # channel is switched on by patching `etc/mangosd.conf` -- and `enable()`
    # needs this install's generated database password, because rendering its
    # compose files is part of the press.
    channel = channel_setup.InstallChannel(
        entry,
        server_dir,
        templates_root=resources.installers_dir(),
        install_id=composegen.install_id(server_dir),
        db_password=password,
        create=lambda name, pw, level: tbc_accounts.create_account(sql, name, pw, gm_level=level),
        # This core's own columns: `v`/`s`, not `salt`/`verifier`. A shared
        # implementation here would write a row that looks right and can never
        # log in.
        reset=lambda name, pw: tbc_accounts.reset_own_password(sql, name, pw),
        channel_for=lambda endpoint: channel_module.SoapChannel(
            endpoint=endpoint,
            state_of=lambda: docker.container_state(spec.world, wsl_distro=wsl_distro),
        ),
    )
    # 8.3b. The same seam as 8.3a, and this tree's own fact under it: the GM
    # level is a column on the account row, so `accounts.level.table` is null
    # and there is no access row to join -- or to write.
    accounts_admin = useraccounts.InstallAccounts(
        entry,
        server_dir,
        sql=sql,
        channel_for_saved=channel.live_channel,
        app_account=channel_setup.account_name(composegen.install_id(server_dir)),
    )
    # 8.4b. The same seam as 8.4a over this tree's own measured facts: its
    # teleport verb is `tele name` (`teleport` is not a command here at all),
    # and its inventory row carries the item's template id, so a set of gear is
    # one join where AzerothCore needs two.
    characters_admin = play_module.InstallPlay(
        entry,
        server_dir,
        sql=sql,
        channel_for_saved=channel.live_channel,
    )
    return _assemble(
        entry,
        server_dir,
        client_dir=client_dir,
        wsl_distro=wsl_distro,
        dashboard=watcher.tick,
        log_snapshot=recorder,
        channel_setup=channel,
        accounts=accounts_admin,
        play=characters_admin,
        bots=_BotBrowser(entry, server_dir, sql),
        controller=tbc_controller.TbcController(
            server_dir, wsl_distro=wsl_distro, pre_stop=recorder
        ),
        sql=sql,
        # No `prompt=`: this package binds this console's prompt and the side of
        # it the answer arrives on, both from the same catalog entry. Passing
        # them again from here would be a second source for one fact.
        send_console=lambda cmd: tbc_console.send_command(
            cmd, container=entry.container_spec().world, wsl_distro=wsl_distro
        ),
        create_account=lambda name, pw, gm: tbc_accounts.create_account(sql, name, pw, gm_level=gm),
        store=tbc_modules.store() if entry.has_manifests else None,
        # `sql=sql`, the SAME runner the console and the account tile use, and
        # that is the point of `tbc_modules.applier()` requiring it: it carries
        # this install's generated password (read once, above) and this game's
        # schema map, so a SQL mod reaches `mangos` and not `acore_world`. The
        # WotLK sibling can default its own runner because that game's password
        # is a fixed catalog value; re-deriving one here is the closed bug
        # `_db_password()` describes.
        # The two 8.7a seams are wired here for the reason they are on WotLK
        # (T7), and this family needs them at least as much: `all-stackables`
        # ships three direct `world` steps on install and two on remove here,
        # and `bug-checklist §46` — no compliant way to install a SQL mod at
        # all — was filed against CMaNGOS before it was measured elsewhere.
        applier=(
            tbc_modules.applier(
                server_dir,
                sql=sql,
                client_dir=client_dir,
                world_running=lambda: docker.world_running(spec.world, wsl_distro=wsl_distro),
                start_database=lambda: docker.start_database(
                    spec, server_dir, because="no SQL was run", wsl_distro=wsl_distro
                ),
            )
            if entry.has_manifests
            else None
        ),
        backup=lambda: tbc_maintenance.backup(server_dir, mysql, wsl_distro=wsl_distro),
        plan_restore=lambda path, can_start_database=False: tbc_maintenance.plan_restore(
            path, server_dir, wsl_distro=wsl_distro, can_start_database=can_start_database
        ),
        restore=lambda plan: tbc_maintenance.restore(
            plan, mysql, confirm=plan.token, wsl_distro=wsl_distro
        ),
    )


def _for_vanilla(
    entry: CatalogEntry,
    server_dir: Path,
    client_dir: Path | None,
    wsl_distro: str | None,
) -> ControllerServices:
    """WoW Vanilla (CMaNGOS), through `controller_wow_vanilla`.

    `controller_wow_vanilla.repair.import_gate()` builds a real `(probe, reset)`
    pair for this install and it is deliberately NOT wired here. Its probe can
    answer `absent`, `ImportState.repairable` is true for that, and the button
    that would appear runs `docker.repair_import()` — which refuses, because
    this entry names no import service. A button whose only outcome is a
    refusal is worse than no button; the state is still knowable through that
    function for anything that wants to report it rather than act on it.

    `client_dir` is accepted and passed to the applier since 8.7c. It used to be
    `del client_dir`, which was right while this tab had no manifests at all;
    now it has some, and although nothing in `manifests/wow-vanilla/` declares a
    `client[]` step today, handing over the folder the user picked is one
    binding rather than a `del` a future manifest would have to come back and
    undo — the same call the TBC factory makes.
    """
    password = _db_password(entry, server_dir, wsl_distro=wsl_distro)
    sql = _sql_for(entry, password, wsl_distro=wsl_distro)
    mysql = _mysql_for(entry, password, wsl_distro=wsl_distro)
    # 8.1c. Measured on `~/vanilla-75b` before this block was written: this tree
    # has `etc/aiplayerbot.conf` with the key live at column 0, `characters` and
    # `realmd` for its schemas, and — its own section of the read says so, not
    # TBC's — bot accounts marked only by the `account.username` prefix.
    spec = entry.container_spec()
    recorder = logsnap.Recorder(
        spec,
        server_dir,
        game=entry.id,
        logs_dir=platform.config_dir() / "logs",
        wsl_distro=wsl_distro,
    )
    watcher = dashboard_module.Dashboard(spec, entry, server_dir, sql=sql, wsl_distro=wsl_distro)
    # 8.2d. The same seam and the same enable route as TBC -- a conf file,
    # because this family reads no environment -- with this tree's own facts
    # under it. `db_password` is handed over because rendering this install's
    # compose files is part of the press, and this family generates its
    # password per install.
    channel = channel_setup.InstallChannel(
        entry,
        server_dir,
        templates_root=resources.installers_dir(),
        install_id=composegen.install_id(server_dir),
        db_password=password,
        create=lambda name, pw, level: vanilla_accounts.create_account(
            sql, name, pw, gm_level=level
        ),
        reset=lambda name, pw: vanilla_accounts.reset_own_password(sql, name, pw),
        channel_for=lambda endpoint: channel_module.SoapChannel(
            endpoint=endpoint,
            state_of=lambda: docker.container_state(spec.world, wsl_distro=wsl_distro),
        ),
    )
    # 8.3c. The same seam as 8.3a and 8.3b, over this tree's own measured fact:
    # `SHOW TABLES LIKE 'account_access'` is empty here and the level is a
    # column on the account row, so `accounts.level.table` is null and there is
    # nothing to join or to write.
    accounts_admin = useraccounts.InstallAccounts(
        entry,
        server_dir,
        sql=sql,
        channel_for_saved=channel.live_channel,
        app_account=channel_setup.account_name(composegen.install_id(server_dir)),
    )
    # 8.4c. The same seam as 8.4a and 8.4b over facts read from THIS install's
    # own checkout on m910q, 2026-09-07 — and one of them is a different number
    # from its TBC sibling's, which is the whole box: `Mail.h:49` here is
    # `#define MAX_MAIL_ITEMS 1` where the same line of the same header in
    # `~/tbc-7.4c` reads 12, so a gear set is one mail per piece and the button
    # says so before the press. The other two match TBC and were still asked
    # rather than inherited: `tele name` is the verb (`Chat.cpp:806-814`, and
    # `teleport` is not a command in that file at all), and the inventory row
    # carries the template id itself (`characters.sql:339-347` has both `item`
    # and `item_template`, and `Player.cpp:3832` writes both).
    characters_admin = play_module.InstallPlay(
        entry,
        server_dir,
        sql=sql,
        channel_for_saved=channel.live_channel,
    )
    return _assemble(
        entry,
        server_dir,
        client_dir=client_dir,
        wsl_distro=wsl_distro,
        dashboard=watcher.tick,
        log_snapshot=recorder,
        channel_setup=channel,
        accounts=accounts_admin,
        play=characters_admin,
        # 8.9b, and the second and last of the two FAMILY boxes uninstall is
        # gated on. The four seams are the ones every install has and the
        # construction is `_for_wotlk`'s, deliberately: the mechanism is the
        # compose project and the folder, which belong to the engine and not to
        # the emulator. What differs on this tree is not the seams but what the
        # ticked path costs — `wow-vanilla`'s database password is GENERATED per
        # install into `.db_password` inside the folder this deletes, so
        # `purge.Uninstaller` copies it out through `yulon.dbsecret` before
        # removing anything and refuses the whole press if it cannot. WotLK's
        # plan is `fixed`, so 8.9a kept nothing and could not exercise that at
        # all; this is its first press. `image_refs` is the BUILT refs and never
        # the pulled database image: `mariadb:11` is shared with `wow-tbc`,
        # which 8.9a's box had no way to notice (`mysql:8.4` is nobody else's).
        uninstall=purge.Uninstaller(
            game=entry.id,
            server_dir=server_dir,
            spec=spec,
            image_refs=composegen.built_image_refs(entry, server_dir),
            logs_dir=platform.config_dir() / "logs",
            wsl_distro=wsl_distro,
            forget=forget_record(entry.id, server_dir),
        ),
        bots=_BotBrowser(entry, server_dir, sql),
        controller=vanilla_controller.VanillaController(
            server_dir, wsl_distro=wsl_distro, pre_stop=recorder
        ),
        sql=sql,
        send_console=lambda cmd: vanilla_console.send_command(
            cmd, container=entry.container_spec().world, wsl_distro=wsl_distro
        ),
        create_account=lambda name, pw, gm: vanilla_accounts.create_account(
            sql, name, pw, gm_level=gm
        ),
        # 8.7c. `sql=sql`, the SAME runner the console and the account tile use,
        # which is what `vanilla_modules.applier()` requires it for: it carries
        # this install's generated password (read once, above) and this game's
        # schema map, so a SQL mod reaches `mangos` and not `acore_world`. The
        # manifests behind this store are this tree's own and not the TBC set —
        # `cross-faction` is ten keys here, because mangos-classic has
        # `AllowTwoSide.Interaction.Trade` and mangos-tbc does not.
        store=vanilla_modules.store() if entry.has_manifests else None,
        # The same two 8.7a seams as TBC and for the same reasons (T7), over
        # this tree's own containers.
        applier=(
            vanilla_modules.applier(
                server_dir,
                sql=sql,
                client_dir=client_dir,
                world_running=lambda: docker.world_running(spec.world, wsl_distro=wsl_distro),
                start_database=lambda: docker.start_database(
                    spec, server_dir, because="no SQL was run", wsl_distro=wsl_distro
                ),
            )
            if entry.has_manifests
            else None
        ),
        backup=lambda: vanilla_maintenance.backup(server_dir, mysql, wsl_distro=wsl_distro),
        plan_restore=lambda path, can_start_database=False: vanilla_maintenance.plan_restore(
            path, server_dir, wsl_distro=wsl_distro, can_start_database=can_start_database
        ),
        restore=lambda plan: vanilla_maintenance.restore(
            plan, mysql, confirm=plan.token, wsl_distro=wsl_distro
        ),
    )


def _for_centurion(
    entry: CatalogEntry,
    server_dir: Path,
    client_dir: Path | None,
    wsl_distro: str | None,
) -> ControllerServices:
    """Centurion (TrinityCore, T179), through `controller_wow_centurion`.

    Every seam is the entry's: the package takes the entry rather than reading
    the shipped catalog, because `wow-centurion` lands there only in T179 Task 7.

    The measured surfaces follow the entry's own blocks, as on every other tree
    (`test_controller_packages_agree`): the dashboard, the log snapshot and Browse
    bots ride on `observability`; Accounts on `accounts.level`; Characters on
    `play`. What is not offered is said where it would be: My Party (the
    registry's note), the Modules tab (`NO_ADDON_MODULES`), and every Characters
    verb Centurion withholds (`centurion_characters.withheld`).

    No `import_probe`: the import is the install engine's marker-gated SQL plan,
    and the Repair button's only action, `docker.repair_import()`, refuses an
    entry with no import service. No `client_dir` use beyond the Steam entry: a
    Centurion "module" does not exist.
    """
    password = _db_password(entry, server_dir, wsl_distro=wsl_distro)
    sql = _sql_for(entry, password, wsl_distro=wsl_distro)
    mysql = _mysql_for(entry, password, wsl_distro=wsl_distro)
    spec = entry.container_spec()
    measured = entry.observability is not None
    recorder = (
        logsnap.Recorder(
            spec,
            server_dir,
            game=entry.id,
            logs_dir=platform.config_dir() / "logs",
            wsl_distro=wsl_distro,
        )
        if measured
        else None
    )
    watcher = (
        dashboard_module.Dashboard(spec, entry, server_dir, sql=sql, wsl_distro=wsl_distro)
        if measured
        else None
    )
    # SOAP on `urn:TC` at 127.0.0.1:7878, switched on in `etc/worldserver.conf`
    # (`operations.enable_conf`): TrinityCore reads no environment.
    channel = channel_setup.InstallChannel(
        entry,
        server_dir,
        templates_root=resources.installers_dir(),
        install_id=composegen.install_id(server_dir),
        db_password=password,
        create=lambda name, pw, level: centurion_accounts.create_account(
            entry, sql, name, pw, gm_level=level
        ),
        reset=lambda name, pw: centurion_accounts.reset_own_password(entry, sql, name, pw),
        channel_for=lambda endpoint: channel_module.SoapChannel(
            endpoint=endpoint,
            state_of=lambda: docker.container_state(spec.world, wsl_distro=wsl_distro),
        ),
    )
    accounts_admin = (
        useraccounts.InstallAccounts(
            entry,
            server_dir,
            sql=sql,
            channel_for_saved=channel.live_channel,
            app_account=channel_setup.account_name(composegen.install_id(server_dir)),
        )
        if entry.accounts.level is not None
        else None
    )
    withheld = centurion_characters.withheld(entry)
    characters_admin = (
        play_module.InstallPlay(
            entry,
            server_dir,
            sql=sql,
            channel_for_saved=channel.live_channel,
            withheld=withheld,
        )
        if entry.play is not None
        else None
    )
    services = _assemble(
        entry,
        server_dir,
        client_dir=client_dir,
        wsl_distro=wsl_distro,
        dashboard=watcher.tick if watcher is not None else None,
        log_snapshot=recorder,
        channel_setup=channel,
        accounts=accounts_admin,
        play=characters_admin,
        bots=_BotBrowser(entry, server_dir, sql) if measured else None,
        # The Uninstall of the gated families, over this engine's two extras:
        # the movement-map job is removed before the containers and the
        # temporary extraction client through its link-safe remover (T179
        # Tasks 3 and 4, `purge.Uninstaller`'s own seams).
        uninstall=purge.Uninstaller(
            game=entry.id,
            server_dir=server_dir,
            spec=spec,
            image_refs=composegen.built_image_refs(entry, server_dir),
            logs_dir=platform.config_dir() / "logs",
            wsl_distro=wsl_distro,
            forget=forget_record(entry.id, server_dir),
        ),
        controller=centurion_controller.CenturionController(
            entry, server_dir, wsl_distro=wsl_distro, pre_stop=recorder
        ),
        sql=sql,
        send_console=lambda cmd: centurion_console.send_command(entry, cmd, wsl_distro=wsl_distro),
        create_account=lambda name, pw, gm: centurion_accounts.create_account(
            entry, sql, name, pw, gm_level=gm
        ),
        store=_no_manifest_store(entry),
        applier=None,
        backup=lambda: centurion_maintenance.backup(
            entry, server_dir, mysql, wsl_distro=wsl_distro
        ),
        plan_restore=lambda path, can_start_database=False: centurion_maintenance.plan_restore(
            entry, path, server_dir, wsl_distro=wsl_distro, can_start_database=can_start_database
        ),
        restore=lambda plan: centurion_maintenance.restore(
            entry, plan, mysql, confirm=plan.token, wsl_distro=wsl_distro
        ),
    )
    return replace(
        services,
        characters_withheld=withheld,
        no_modules_note=NO_ADDON_MODULES.format(game=entry.name),
    )


ADDONS_PARENT = "Interface"
"""The folder a client addon is written under, and the one a client must already have.

`Interface/AddOns/<Name>` is where WoW looks, and `_ApplyEngine._client()`
(`apply.py:2173-2192`) joins that onto whatever client folder it is handed --
with `copytree`/`mkdir` creating every missing parent. So a folder that is not a
WoW client does not refuse a `client` step: it GAINS an
`Interface/AddOns/TortoiseBotsManager`, and the install reports success.
"""


def _client_dir_for_addons(client_dir: Path | None) -> Path | None:
    """This install's client folder if a manifest may write an addon into it, else None.

    Two answers, and the second is the one that had to be written down (T30).
    A tab whose install record carries NO client dir has never had one --
    nothing to write into. A tab that carries one which holds no `Interface/`
    is the case that looks the same from the applier's side and is not: the step
    would create the whole tree and succeed, in a folder nobody has shown to be
    a game client, and the user would go looking for `/tbm` in the client they
    actually play.

    The check is `Interface/` rather than `Interface/AddOns/`, because the
    second is the folder an addon is entitled to create and the first is the one
    the game ships. Measured on `yulon-arch` 2026-09-11, the Turtle client used
    for T30's live half has `Interface/AddOns/` with twelve Blizzard_* folders
    in it before anything of ours is written.

    A client that genuinely has no `Interface/` -- one unpacked and never
    started -- is refused rather than filled in, and that is the deliberate half:
    the cost of refusing is one launch of the game, and the cost of guessing
    wrong is files written into a folder the app was told to treat as the user's
    own.
    """
    if client_dir is None:
        return None
    if not (client_dir / ADDONS_PARENT).is_dir():
        logger.info(
            f"{client_dir} has no {ADDONS_PARENT}/ folder, so no client addon is written "
            "into it; start the game once, or point this install at the client you play"
        )
        return None
    return client_dir


def _client_dir_row_text(client_dir: Path | None) -> str:
    """The Server tab's client-folder sentence, in the same states `_client_dir_for_addons()`
    answers (T36) -- but read with no log line, because this runs on every five-second poll and a
    client extracted but never started would print its info line forever.

    A missing folder is its own sentence rather than falling into the
    "no `Interface/`" one below: that sentence tells the owner to start the
    game, which is no help at all when the folder itself is gone (round 2
    review, non-blocking) -- a moved or deleted client says so, plainly.
    """
    if client_dir is None:
        return "Client folder: none — addons and Play need one"
    if not client_dir.is_dir():
        return f"Client folder: {client_dir} — the folder is missing"
    if not (client_dir / ADDONS_PARENT).is_dir():
        return (
            f"Client folder: {client_dir} — no {ADDONS_PARENT}/ folder yet — start the game "
            "once before installing addons"
        )
    return f"Client folder: {client_dir}"


def _check_sentence(check: preflight.Check) -> str:
    """One `preflight.Check` as the paragraph a user reads -- `Report.message()`'s own join,
    for a single check `change_client_dir()` shows outside a refusal (a warning, or the
    zero-archive case `preflight.Report.message()` never sees because it is not a refusal).
    """
    return f"{check.name}: {check.detail} {check.remedy}".rstrip()


_MPQ_COUNT_RE = re.compile(r"^(\d+) ")
"""`_mpq_check()`'s own count, at the front of its `detail` (`"0 MPQ archives ..."`)."""


def _mpq_archive_count(check: preflight.Check) -> int | None:
    """The MPQ check's archive count, read off its own `detail` rather than re-walking `Data/`.

    Round 2 review: an empty `Data/` answers `warn`, same as "a few too few", and the press used
    to let a Yes through either. The ticket's rule is "no archives is a refusal" -- and the count
    the CHECK already computed is the one number that cannot disagree with what it decided,
    where a second `mpq_files()` call over the same folder could (a file appearing or vanishing
    between the two walks, a symlink resolving differently the second time).
    """
    match = _MPQ_COUNT_RE.match(check.detail)
    return int(match.group(1)) if match else None


def _for_tortoise(
    entry: CatalogEntry,
    server_dir: Path,
    client_dir: Path | None,
    wsl_distro: str | None,
) -> ControllerServices:
    """Tortoise (CMaNGOS lineage), through `controller_wow_tortoise`.

    `controller_for()` is that package's own constructor and it is the one used
    rather than `TortoiseController(...)` directly, because the decision to
    attach no import probe is written down inside it.

    `client_dir` used to be `del`-ed here. Since 8.8 it is passed on: this tree
    has no manifests that want it, but the Steam client entry is a path to that
    folder's own executable and there is nowhere else to get it.
    """
    password = _db_password(entry, server_dir, wsl_distro=wsl_distro)
    sql = _sql_for(entry, password, wsl_distro=wsl_distro)
    mysql = _mysql_for(entry, password, wsl_distro=wsl_distro)
    # 8.1d. This is NOT a CMaNGOS tree — `cmangos.md` says so in as many words —
    # so every value under these two seams comes from its own section and its
    # own source: `tw_char`/`tw_logon`, and a bot marker that is an account
    # prefix with no registry, whose compiled default sits at
    # `PlayerbotAIConfig.cpp:545` here where TBC's is at `:500`.
    spec = entry.container_spec()
    recorder = logsnap.Recorder(
        spec,
        server_dir,
        game=entry.id,
        logs_dir=platform.config_dir() / "logs",
        wsl_distro=wsl_distro,
    )
    watcher = dashboard_module.Dashboard(spec, entry, server_dir, sql=sql, wsl_distro=wsl_distro)
    # THE CHANNEL IS SOAP AGAIN, for the third time on this tree. 8.2e wired
    # `AttachChannel` here because the fork's mangosd linked neither gsoap nor
    # `RASocket`; the fork re-added SOAP (3f9a062) and this became Vanilla's
    # `InstallChannel` over this tree's own account seam; T30 moved the entry
    # onto the Penqle core, which had no SOAP, and this went back to the console
    # attach. PR #491 put SOAP into that core (merged into `1181dev` 2026-09-17,
    # 57abad6f3: `ns1__executeCommand`, `urn:MaNGOS`, rank 4, bans refused, the
    # rank read per request from the row) and the entry's pin moved onto it, so
    # this is the `InstallChannel` over `SoapChannel` that stood here between
    # 2026-09-08 and 2026-09-11 (T86). The image template passes
    # `-DENABLE_SOAP=ON`; the entry's `operations` block says `soap` and names
    # the three `SOAP.*` conf keys the `enable_conf` writer sets.
    channel = channel_setup.InstallChannel(
        entry,
        server_dir,
        templates_root=resources.installers_dir(),
        install_id=composegen.install_id(server_dir),
        db_password=password,
        create=lambda name, pw, level: tortoise_accounts.create_account(
            sql, name, pw, gm_level=level
        ),
        reset=lambda name, pw: tortoise_accounts.reset_own_password(sql, name, pw),
        channel_for=lambda endpoint: channel_module.SoapChannel(
            endpoint=endpoint,
            state_of=lambda: docker.container_state(spec.world, wsl_distro=wsl_distro),
        ),
    )
    accounts_admin = useraccounts.InstallAccounts(
        entry,
        server_dir,
        sql=sql,
        channel_for_saved=channel.live_channel,
        app_account=channel_setup.account_name(composegen.install_id(server_dir)),
    )
    characters_admin = play_module.InstallPlay(
        entry,
        server_dir,
        sql=sql,
        channel_for_saved=channel.live_channel,
    )
    lifecycle = tortoise_controller.controller_for(
        server_dir, wsl_distro=wsl_distro, pre_stop=recorder
    )
    services = _assemble(
        entry,
        server_dir,
        client_dir=client_dir,
        wsl_distro=wsl_distro,
        dashboard=watcher.tick,
        log_snapshot=recorder,
        channel_setup=channel,
        accounts=accounts_admin,
        play=characters_admin,
        bots=_BotBrowser(entry, server_dir, sql),
        controller=lifecycle,
        sql=sql,
        # This package's `send()` takes no container: it addresses its own
        # entry's worldserver, which is the same catalog fact `spec.world` is.
        send_console=lambda cmd: tortoise_console.send(cmd, wsl_distro=wsl_distro),
        create_account=lambda name, pw, gm: tortoise_accounts.create_account(
            sql, name, pw, gm_level=gm
        ),
        # 8.7d. The store is this game's own, and the applier is the GUARDED one
        # -- `tortoise_modules.applier()` returns an `autoupdate.GuardedApplier`,
        # which is the whole of checklist 2504 on the object the tab holds. The
        # two readings it needs are callables rather than values on purpose: a
        # world can be started or stopped between the moment this tab is built
        # and the moment somebody presses Install, and a guard that decided here
        # would be guarding a fact about the past.
        #
        # `sql=sql` is the SAME runner the console, the bot browser and the
        # account tile use, carrying this install's generated password and this
        # fork's `tw_*` schema map -- so a SQL mod reaches `tw_world`, and the
        # guard's ledger query reaches `tw_world.migrations` rather than
        # `acore_world`'s.
        store=tortoise_modules.store() if entry.has_manifests else None,
        # T126: the TortoiseBots Manager row says which release is installed and
        # whether it matches the server's bot module. Two small file reads.
        module_notes=(
            (lambda: tortoise_modules.release_notes(server_dir)) if entry.has_manifests else None
        ),
        # T126 review: the four module-folder seams, so a Tortoise player can
        # SEE and TAKE a new addon release. This game's clones are the two
        # client addons under `sql_scripts/clones/`; its SQL and conf mods
        # clone nothing. "Check for updates" counts them off the GUI thread
        # (`_run`), cached a day per clone; an installed row that is behind
        # gets the Update chip, whose press is `Applier.update()` -- the path
        # with the repository, dirty-tree and local-commit checks.
        module_updates=(
            (lambda: tortoise_modules.module_updates(server_dir)) if entry.has_manifests else None
        ),
        installed_modules=(
            (lambda: apply_module.installed_clones(server_dir)) if entry.has_manifests else None
        ),
        # T121's seam rides with `installed_modules`. Tortoise ships no relative
        # (record-backed) mod, so nothing here writes a pending mark and this
        # reads empty; wired so the Modules tab asks it the same way everywhere.
        unknown_modules=(
            (lambda: apply_module.unknown_modules(server_dir)) if entry.has_manifests else None
        ),
        unfinished_modules=(
            (lambda: apply_module.unfinished_clones(server_dir)) if entry.has_manifests else None
        ),
        module_version=(RunnerGit().head_version if entry.has_manifests else None),
        applier=(
            tortoise_modules.applier(
                server_dir,
                sql=sql,
                arming=lambda: tortoise_autoupdate.read_arming(
                    server_dir,
                    world_container=spec.world,
                    schemas=entry.schema_map(),
                    sql=sql,
                    wsl_distro=wsl_distro,
                ),
                # `docker.world_running()` since T7, which is this expression
                # with one difference: an unreadable inspect is `None` rather
                # than `False`. It answers TWO guards on this game — 2504's
                # updater check on the subclass and 8.7a's direct-SQL check on
                # the base — so they cannot disagree about the world. 2504's
                # behaviour is unchanged: `GuardedApplier._guard()` narrows it
                # back with `is True`, which is what `settled` used to give it.
                # `status == "running"` alone was never enough either: a
                # container that is RESTARTING is on its way back up and its
                # next start is exactly the one the guard is about.
                world_running=lambda: docker.world_running(spec.world, wsl_distro=wsl_distro),
                start_database=lambda: docker.start_database(
                    spec, server_dir, because="no SQL was run", wsl_distro=wsl_distro
                ),
                # T30. `applier()` has taken this keyword since 8.7d and this
                # factory was the one caller that swallowed it, so a manifest
                # `client` step on this game reported "no client dir configured"
                # and copied nothing (`apply.py:2175-2177`). Nothing in
                # `manifests/wow-tortoise/` had one until the two Turtle addons
                # arrived, which is why the gap could sit here unseen -- tbc
                # (`:1441`) and vanilla (`:1603`) have always passed it.
                #
                # It is the folder the user chose as their WoW client, the same
                # value `requires_client_dir` makes the installer ask for --
                # through `_client_dir_for_addons()`, which is where the second
                # refusal lives: a record with no client dir, and a client dir
                # with no `Interface/`, both arrive here as `None` and the step
                # is skipped with a sentence rather than creating a directory
                # tree in a folder nobody has shown to be a game client.
                client_dir=_client_dir_for_addons(client_dir),
            )
            if entry.has_manifests
            else None
        ),
        backup=lambda: tortoise_maintenance.backup(server_dir, mysql, wsl_distro=wsl_distro),
        plan_restore=lambda path, can_start_database=False: tortoise_maintenance.plan_restore(
            path, server_dir, wsl_distro=wsl_distro, can_start_database=can_start_database
        ),
        restore=lambda plan: tortoise_maintenance.restore(
            plan, mysql, confirm=plan.token, wsl_distro=wsl_distro
        ),
    )
    # T123. Moving the bots module onto its registry (TortoiseBots #265) leaves
    # the bots an older module made on accounts it ignores, and only the update
    # route moves that checkout. So both of its presses end by enrolling them --
    # over SOAP first, which this core runs at console level, then the attach
    # console -- and restarting the world, which is when the module loads them.
    # `botpool.py` holds the measurement.
    console_channel = channel_module.AttachChannel(
        send=lambda cmd, **kw: tortoise_console.send(cmd, wsl_distro=wsl_distro, **kw)
    )

    def adoption_channels() -> list[channel_module.Channel]:
        soap = channel.live_channel()
        found = [soap] if isinstance(soap, channel_module.SoapChannel) else []
        return [*found, console_channel]

    # T127. The dashboard switch, and the update route rebuilding its image from
    # the module the update moved. Inside T123's wrap, so a restart T123 makes
    # for the adoption comes after the dashboard is back.
    dashboard_switch = tortoise_botdash.for_entry(
        entry, server_dir, lifecycle, wsl_distro=wsl_distro
    )
    # T144. "Rebuild random bots…" on the Bots tab, and the flag T123's wrap
    # sets when an update press moved the module, for the offer after it. The
    # rebuild enrols over the same channels T123 uses, restarts the same way,
    # and reads THIS run's world log (`docker.current_run_log`, which says when
    # it could not scope the read to this run, so the reader fails closed).
    module_moved = tortoise_botpool.ModuleMoved()
    pool_rebuild = tortoise_poolreset.for_entry(
        entry,
        server_dir,
        world_running=lambda: docker.world_running(spec.world, wsl_distro=wsl_distro),
        channels=adoption_channels,
        restart=lambda: tortoise_botpool.restart_world(lifecycle),
        world_log=lambda: docker.current_run_log(spec.world, wsl_distro=wsl_distro),
        world_started=lambda: docker.started_at(spec.world, wsl_distro=wsl_distro),
        module_moved=module_moved,
    )
    return replace(
        services,
        bot_dashboard=dashboard_switch,
        bot_pool_rebuild=pool_rebuild,
        update_to_latest=tortoise_botpool.wrap_route(
            tortoise_botdash.wrap_route(services.update_to_latest, dashboard_switch),
            entry,
            server_dir,
            channels=adoption_channels,
            restart=lambda: tortoise_botpool.restart_world(lifecycle),
            wsl_distro=wsl_distro,
            moved=module_moved,
        ),
    )


_Factory = Callable[[CatalogEntry, Path, Path | None, str | None], ControllerServices]

_FACTORIES: dict[str, _Factory] = {
    "wow-wotlk": _for_wotlk,
    "wow-tbc": _for_tbc,
    "wow-vanilla": _for_vanilla,
    "wow-tortoise": _for_tortoise,
    # T179: the `trinitycore` family's first game. Its catalog entry is T179
    # Task 7's; until it ships, nothing in the catalog reaches this row.
    "wow-centurion": _for_centurion,
}
"""Catalog id → the wiring for that game. `for_entry()` is the only reader.

A dict rather than a chain of `if`s so that adding a game is adding a row, and
so that "which games can this build manage?" has an answer that can be printed
(`UnsupportedGameError` prints it) and asserted against `catalog.json`.
"""


class _BotBrowser:
    """`botlist.page()` with this install's live marker in front of it."""

    def __init__(self, entry: CatalogEntry, server_dir: Path, sql: DockerSql) -> None:
        self.entry = entry
        self.server_dir = server_dir
        self._sql = sql

    def page(self, *, after: tuple[str, int] | None = None, name_like: str = "") -> botlist.Page:
        answer = dbreads.resolve_marker(self.entry, self.server_dir)
        if answer.marker is None:
            return botlist.Page(problem=answer.problem or "this install's bot marker is unreadable")
        return botlist.page(
            self._sql,
            self.entry,
            answer.marker,
            after=after,
            name_like=name_like,
        )


def _press_is_allowed(verdict: dashboard_module.Verdict) -> bool:
    """Whether the enable press is reachable in the state this verdict describes.

    Two clauses, and each was put here by a machine.

    `stable` is 8.1's own value and the first use of it: a world that is `up`
    with an unreachable database is not stable, which is what the TBC gate found
    in the first version of that property, and a server nobody could ask about
    is not one to aim a command at.

    `stopped` was added after yulon-win11-gate refuted the rest of it on
    2026-09-07. The press REFUSES while the world is running -- 8.2a's whole
    shape, because a failed bind is not atomic -- and `stable` is only ever true
    while the world IS running. So the only control that turns the channel on
    was live exactly when pressing it could not work and dead exactly when it
    would, and the refusal sentence asked the user to do the thing that greys
    the button out. A stopped server is the state the press is FOR.

    Everything else stays shut: `restart_loop` and `unknown` are both servers
    that may be running, and the press would refuse or, worse, write a setting
    under a world that is up. `missing` (T95) stays shut too, as it did while
    it still read as `unknown`: it is not the stopped server the press is for
    but a world container that is gone (removed by hand, or never created), and
    `missing` is only as true as the daemon asked (`ContainerState.missing`).
    The next Start recreates the container, and the verdict is one of the above.
    """
    return verdict.stable or verdict.state == "stopped"


CONSOLE_CHANNEL_SENTENCE = (
    "Commands reach this server through the worldserver console on the Console tab. "
    "This core has no remote command listener to turn on \u2014 it is built with neither "
    "SOAP nor the telnet console \u2014 so there is nothing to set up here."
)
"""What the Server tab says for a tree whose channel is the console (8.2e).

The alternative was a greyed-out "Turn on the command channel", which is the
worst of both: it says the feature exists and then refuses to explain. This
core's complete source and dependency lists name neither gsoap nor `RASocket`,
so there is no listener, no port, no account -- and the console the Console tab
already types at is the whole of its command channel.
"""


def _is_console_channel(entry: CatalogEntry) -> bool:
    """Whether this entry's command channel is the attach console.

    Read from the entry rather than from the absence of a `channel_setup`: a
    missing seam means "this build wires nothing", which is also true of a tree
    whose box has not been done yet, and those two must not say the same thing
    to a user.
    """
    operations = entry.operations
    return operations is not None and operations.channel == "attach"


def _channel_sentence(state: object) -> str:
    """One line for where the channel setup has got to.

    A function rather than a method so what it says can be read without a
    widget, the same reason `dashboard.line()` is one.
    """
    if isinstance(state, channel_setup.Verified):
        # The time is the whole point of showing this at all: a channel proved
        # once and broken since reads identically to one proved a minute ago.
        # A credential written before the field existed says so rather than
        # borrowing the current moment, which is the one answer that misleads.
        when = f" at {state.at}" if state.at else " (before this app recorded when)"
        return f"Command channel: verified as {state.account}{when}."
    if isinstance(state, channel_setup.Refused):
        return f"Command channel: refused. {state.reason}"
    if isinstance(state, channel_setup.Pending):
        return (
            f"Command channel: the account {state.account} exists and is waiting to be proved. "
            "Start the server if it is not running."
        )
    if isinstance(state, channel_setup.GaveUp):
        return f"Command channel: not set up. {state.reason}"
    return "Command channel: not set up yet."


def _safe_bindings(wsl_distro: str | None = None) -> dict[int, str] | None:
    """Which host address each published port is bound to, or None if docker refused.

    It takes the distro because it had no way to learn one, and the answer is
    read off whichever daemon is asked. `networking.plan()` uses this for one
    thing (`networking.py:204`): whether this entry's own ports came up on
    127.0.0.1 rather than 0.0.0.0, which is what makes it warn and emit
    `portproxy` commands.

    Asked of the LOCAL daemon about a WSL-resident server, the realistic wrong
    answer is a host container that happens to publish 3724 or 8085 on
    loopback: the plan then warns about, and writes portproxy rules for, a
    machine the server is not on. The other direction is quieter than it
    looks - an empty dict is falsy, so `if bindings:` skips the block entirely
    and the plan simply says nothing about bindings rather than saying
    something false.
    """
    try:
        return docker.published_bindings(wsl_distro=wsl_distro)
    except docker.DockerCommandError:
        return None


LOOPBACK_CHOICE = "Only this computer (127.0.0.1)"
"""The Networking tab's third radio, bug-checklist §41.

Named for what it DOES rather than for what it is. "Loopback" is the accurate
word and is not one a person installing a game server has any reason to know,
so the label says the effect and carries the address in brackets — the address
being the half a reader can match against the realm row, against
`networking.LOOPBACK_ADDRESS`, and against the sentence the install prints when
it leaves the row alone.

The cost of the mode — that no other machine can reach the server — is NOT in
this label. It is `networking.ONLY_THIS_COMPUTER`, which arrives as a warning
on the plan `Show plan` renders, one press before Apply: a radio wide enough to
hold that sentence would push the other two off the row, and a person who has
not pressed Show plan has not yet chosen anything.

Defined here rather than inline so a test can assert the label and the mode
together without retyping the string, and placed below `_assemble()` so it does
not move the `networking.apply(...)` call `test_controller_view.py` pins by line.
"""

UPDATE_TO_LATEST_BUTTON_LABEL = server_build_presses.UPDATE_TO_LATEST
"""The T64 press. Spelled in `server_build_presses`, which the chip and the engine read too (T155).

The ellipsis is this tab's convention for "this opens a dialog first", and here
it is carrying more than usual: it is the only thing between a single click and
a multi-hour compile of code nobody has tested.
"""

RETURN_TO_PIN_BUTTON_LABEL = server_build_presses.RETURN_TO_PIN
"""The way back off an untested commit, shown only once there is one to go back from."""


class UpdateChoice(enum.Enum):
    """What the one T64 dialog can be answered with. Three, and `CANCEL` is the default.

    An enum rather than a `bool | None`, because the two "yes" answers differ by
    the most consequential thing in the flow -- whether a backup is taken first
    -- and a caller that had to remember which of `True`/`False` meant which
    would be one rename away from starting an update that was asked to back up.
    """

    BACK_UP_FIRST = "back-up-first"
    WITHOUT_BACKUP = "without-backup"
    CANCEL = "cancel"


def ask_update_choice(parent: QWidget | None, title: str, text: str) -> UpdateChoice:
    """T64's three-way question in its own words: `ask_backup_choice()` with the update's labels."""
    return ask_backup_choice(
        parent,
        title,
        text,
        back_up_first="Back up first, then update",
        without_backup="Update without a backup",
    )


def ask_backup_choice(
    parent: QWidget | None, title: str, text: str, *, back_up_first: str, without_backup: str
) -> UpdateChoice:
    """Put a three-way "back up first / go without / Cancel" question, defaulting to Cancel.

    Never raises. T64's update and T144's bot rebuild both ask it, each with
    its own two labels.

    **The three buttons are STANDARD buttons with their labels replaced**, which
    is `catalog_view._qt_suggestion_asker()`'s shape and is chosen for the same
    two reasons: a relabelled standard button keeps its platform position and
    its keyboard role, and `box.exec()` then answers with a value this function
    can map -- a `addButton(text, role)` custom button answers with an opaque id
    that only `clickedButton()` can resolve, and `clickedButton()` is `None`
    under the test fixture that stops a modal blocking an offscreen run.

    **`No` is deliberately not one of them, and that is a safety property rather
    than a spelling.** `tests/conftest._no_modal_dialogs` answers every
    unpatched `exec()` with `No`, because `No` is the reply that takes no
    action. If `No` meant "update without a backup" here, every test in the
    suite that so much as brushed this control would start a compile of
    untested code. `Yes`, `Save` and `Cancel` are used instead, and anything
    this function does not recognise -- `No` included, and `NoButton`, which is
    what Escape and the window's close button answer -- falls through to
    `CANCEL`. The one answer that cannot be arrived at by accident is the
    destructive one.

    `Save` for "Update without a backup" is not a description of the button; it
    is a slot with an `AcceptRole` that is neither `Yes` nor `No`, and the text
    on it is what the user reads. `Cancel` carries `RejectRole`, which is what
    makes Escape land on it.
    """
    box = FittedMessageBox(
        QMessageBox.Icon.Warning,
        title,
        text,
        QMessageBox.StandardButton.Yes
        | QMessageBox.StandardButton.Save
        | QMessageBox.StandardButton.Cancel,
        parent,
    )
    _relabel(box, QMessageBox.StandardButton.Yes, back_up_first)
    _relabel(box, QMessageBox.StandardButton.Save, without_backup)
    _relabel(box, QMessageBox.StandardButton.Cancel, "Cancel")
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    # And the ESCAPE button by name. `setDefaultButton` decides what Enter does;
    # this decides what Escape and the title bar's X do, and leaving it to Qt to
    # infer from the button roles is leaving the least reversible press in this
    # app to an inference.
    box.setEscapeButton(QMessageBox.StandardButton.Cancel)
    answer = box.exec()
    if answer == QMessageBox.StandardButton.Yes:
        return UpdateChoice.BACK_UP_FIRST
    if answer == QMessageBox.StandardButton.Save:
        return UpdateChoice.WITHOUT_BACKUP
    return UpdateChoice.CANCEL


def _relabel(box: QMessageBox, which: QMessageBox.StandardButton, text: str) -> None:
    """Put `text` on a standard button, if this Qt gave us one to put it on.

    `button()` is typed as returning `QPushButton | None` and the None branch is
    not decoration: a box built without that standard button answers None, and a
    crash inside a confirmation dialog is a crash instead of a question.
    """
    found = box.button(which)
    if found is not None:
        found.setText(text)


REBUILD_BUTTON_LABEL = server_build_presses.REBUILD
"""The rebuild press's label, in one place because many things say it.

The press wears it -- the banner's button, the first entry under
`SERVER_BUILD_LABEL` since T89, and a rebuild-owed chip's subpanel button -- and
`_format_report()`, the chip's sentence and the engine's refusals tell the user
to press it by name. Two literals would be one rename away from a report that
points at a control that is not there any more, which is the class of defect
this whole feature is a fix for; T155 found the chip's copy had drifted, and the
spelling now lives in `server_build_presses`, below everything that says it.
"""

SERVER_BUILD_LABEL = server_build_presses.SERVER_BUILD
"""The Modules toolbar button that holds the three compile-this-server presses (T89).

Rebuild, "Update the server to latest…" and "Return to the tested pin…" were
three buttons side by side, and with a route wired -- every catalog entry that
offers one -- the bar was seven buttons and wrapped at the 1280x800 the app
opens at. The owner's decision of 2026-09-27: one button with a menu, the three
labels unchanged. The triangle is in the label because it is the only thing on
the bar that says this press opens a list rather than acting or asking.
"""

SERVER_BUILD_TIP = (
    "Compile this server again: from the same code, from the newest code, or back on the "
    "commit this app was tested against. Every entry asks first."
)
"""The menu button's own tooltip; each entry keeps the one its button had (T89)."""

PATHFINDING_START = "Make the pathfinding data"
PATHFINDING_STOP = "Stop making the pathfinding data"
PATHFINDING_ASKING = "Pathfinding data: asking how far it has got…"
PATHFINDING_UNREAD = "Could not read how far the pathfinding data has got: {exc}"
WORLD_UPKEEP_UNREAD = "Could not read whether the last update left anything to finish: {exc}"
WORLD_UPKEEP_BUSY = (
    "This server is busy with another action — wait for it to finish, then press this again. "
    "Nothing was started."
)

REMOVE_IDLE = "Stop and remove containers…"
REMOVE_ARMED = "Press again to remove"
"""Two labels for one button, because a teardown should not be one click away.

The wording changes rather than a dialog appearing: the explanation is a
paragraph naming what is kept, `problem_label` already renders those, and a
modal would arrive from a worker thread.
"""

REMOVE_FROM_YULON = "Remove from Yu'lon…"
"""T95: the Server tab's way off the list, the same words as `forgetting.BUTTON_LABEL`.

A local constant rather than an import, so this ticket adds no line to the
module's import block (a parallel ticket edits it). `test_controller_view.py`
asserts the two spellings are equal.
"""

STOP_ANYWAY_LABEL = "Stop now anyway"
STOP_ANYWAY_TIP = (
    "Stop the world now instead of waiting for it to finish loading. A world still loading "
    "ignores the stop and is force-stopped when the stop's grace ends, losing anything since "
    "its last save."
)
"""T158: the only way to end a load wait early, shown only while one is running."""

STOPPING_FOR_REMOVAL = "status: stopping the server first, then removing it from Yu'lon…"
STOPPING_FOR_REMOVAL_WAIT = (
    "Stopping the server before it is removed from Yu'lon. A server still loading its "
    "world can take a few minutes to stop; the buttons unlock when it has."
)
"""T95, from the m910q gate: an × pressed on a world still loading took minutes.

mangosd ignores SIGTERM while it loads. Until T158 the stop then sat out its
whole grace (`docker.STOP_GRACE_SECONDS`) and ended in a SIGKILL; since T158 it
waits until the world can hear the stop and then stops cleanly, which is still
minutes on a slow load, and says so in this label while it waits
(`_stop_notice`, with "Stop now anyway" beside it). The
status line stays one short line (it does not wrap); the wait goes in the
wrapped problem label.
"""

REPAIR_IDLE = "Repair: finish the database import…"

STOP_DOCKER_REINSTALL = "Stop reinstalling Docker"
"""The reinstall button's label while its repair runs: pressing it again stops it (T160)."""

STOPPING_DOCKER_REINSTALL = (
    "Stopping the Docker reinstall. The step running now finishes first; nothing after it runs."
)

DOCKER_REINSTALL_PROMPT_TITLE = "Reinstalling Docker"
"""The question dialogs' title for the repair, in place of "The installer needs an answer"."""

DASHBOARD_SWITCH_OFF = "Bot dashboard: Off"
DASHBOARD_SWITCH_ON = "Bot dashboard: On"
DASHBOARD_ABOUT = (
    "The bots module's own web dashboard: a live map of every bot, their health and what "
    "they are doing, stuck bots, and an Armory that shows any bot's gear. You sign in with "
    "one of your game accounts that has GM rank 2 or higher."
)
DASHBOARD_LAN_LABEL = "Allow other devices on my network"
DASHBOARD_LAN_WARNING = (
    "Leave this off unless you want to open the dashboard from another device. With it on, "
    "anyone on your network can reach the dashboard's sign-in page, and can read its "
    "/metrics page (bot counts, bot problems) without signing in at all. Signing in sends "
    "your GM account's password over plain, unencrypted HTTP. Change it while the dashboard "
    "is off."
)
DASHBOARD_OFF_QUESTION = (
    "The dashboard is stopped and removed, and it is taken out of this server's "
    "docker-compose.yml. In tortoise_bots.conf the three telemetry settings "
    "(AiPlayerbot.Observability, ObservabilityHost and ObservabilityPort) are set back to the "
    "values they had before you switched it on; if you changed those three by hand since, "
    "your changes to them are replaced. Nothing else in the file changes.\n\nSwitch it off?"
)
DASHBOARD_REBUILD_QUESTION = (
    "Yu'lon builds the dashboard again from the bots module this server has now, and starts "
    "it.\n\nIf the server is running, it is then restarted so the bots module finds the "
    "dashboard. Anyone playing is disconnected for a few minutes.\n\nRebuild it now?"
)


def dashboard_stale_text(debt: bot_dashboard.Debt) -> str:
    """The Bots tab's line for a dashboard that owes a rebuild (T162).

    It says the worse thing unless a read proved the better one: a container
    that would not go, or could not be read back, may still be running.
    """
    if debt.old_may_run:
        text = (
            "The bot dashboard could not be rebuilt after the server update, and the old one, "
            "made for the bots module as it was before, may still be running and showing wrong "
            f"bot telemetry ({debt.why})."
        )
    else:
        text = (
            "The bot dashboard is switched on but stopped: after the server update it could not "
            "be rebuilt from the new bots module, and the old one was made for the module as it "
            f"was before ({debt.why})."
        )
    if debt.where == "memory":
        text += (
            " This could not be saved to disk, so once Yu'lon is closed the next server Start "
            "brings the old dashboard back."
        )
    return f"{text} Press {tortoise_botdash.REBUILD_PRESS} to fix it."


DASHBOARD_RESTART_QUESTION = (
    "The bot dashboard is off. The world keeps trying to send to it until it restarts, which "
    "does no harm.\n\nRestart the server now? Anyone playing is disconnected for a few minutes."
)


def dashboard_on_question(*, lan: bool) -> str:
    """The switch-on question: every change it makes, the restart, and who can reach it."""
    where = (
        f"Every device on your network will be able to reach it on port "
        f"{bot_dashboard.HTTP_PORT}: its sign-in page, and its /metrics page (bot counts and "
        "bot problems), which needs no sign-in. Signing in from another device sends your GM "
        "account's password over plain, unencrypted HTTP."
        if lan
        else f"Only this PC will be able to reach it, at {bot_dashboard.URL}."
    )
    return (
        "Yu'lon builds the bots module's own dashboard (a few minutes the first time), turns "
        "the bots module's telemetry on in tortoise_bots.conf (a copy of the file is kept), adds "
        "the dashboard to this server's docker-compose.yml and starts it.\n\n"
        "If the server is running, it is then restarted so the bots module starts sending. "
        "Anyone playing is disconnected for a few minutes.\n\n"
        f"{where}\n\nSwitch it on?"
    )


REPAIR_ARMED = "Press again to overwrite the databases"
"""The same two-press gesture, for the action that really can destroy data.

Deliberately not a second kind of confirmation. There is one arm/disarm shape
on this tab and both destructive buttons use it, so a user who has learned that
pressing once only arms is not surprised by the one where it would matter most.
The armed wording is where they differ: the teardown's says what is *kept*,
this one says what is *overwritten*.
"""

UNINSTALL_RUNNING = (
    "This server is being uninstalled. Closing now would destroy the job part-way through, "
    "leaving containers, volumes or a half-deleted folder behind. The window will close "
    "normally once it finishes."
)
"""Why a tab mid-uninstall must not be torn down (8.9a).

The same reason the import has one: the work runs synchronously inside a
`_JobWorker`, so `thread.quit()` cannot preempt it, and a QThread destroyed
while running aborts the process (0xC0000409). The difference is what the crash
would land on top of - an install that is half removed, whose record may already
be gone.
"""

UNINSTALL_NO_PLAN = (
    "Show the uninstall plan first. It names the folder, its size and every Docker object "
    "that would go, and nothing is removed until you have seen it."
)
"""The gate on the Uninstall button, borrowed from the restore action.

`phase8-decisions.md`:172 asks for a typed server name and says it "is the same
pattern the restore action already uses". It is not: restore's gate is that the
PLAN must be on screen (`run_restore()` refuses with "Show the restore plan
first."). Uninstall has a `plan()` for the same reason restore does, so it gets
the gate this tab really has rather than a second confirmation idiom nobody
here has learned.
"""

FOLLOW_ENDED = "log ended \u2014 the world server is not running"
"""The Console's header when `docker logs -f` returns (T188 A15).

It returns because the world container is not running, which "finished: done"
read as a job completed.
"""

UNINSTALL_KEEP_CHARACTERS = "Uninstall this server and keep my characters"
UNINSTALL_DELETE_CHARACTERS = "Uninstall this server and delete my characters"
"""The confirm press, named for the Keep my characters choice it carries out (T188 A14)."""


IMPORT_RUNNING = (
    "Running the database import. A full one takes 10-30 minutes and cannot be stopped once "
    "it has started. What the import is printing:"
)
"""The heading above the import's live output, and the one honest thing to say.

It used to be "Running the database import… this takes several minutes." and
then nothing changed on screen until it finished, which for the action whose
armed copy warns it overwrites databases is indistinguishable from a hang — the
user's only recourse being to kill the app, during a database import.

It says "cannot be stopped" because it cannot, and the tab must not suggest
otherwise: every button on it is disabled while this runs, and there is no
cancel to offer. Abandoning a `compose up` means terminating it, which stops
`ac-db-import` part-way through writing schemas.
"""

_IMPORT_TAIL_LINES = 2


def _pending_sql_names(report: ApplyReport) -> tuple[str, ...]:
    """What a report says is still waiting for the importer, named file by file.

    `PendingSql.files` is the glob RESOLVED against the clone, and it has three
    answers. A tuple of paths is what is really on disk; `()` means the glob
    matched nothing THIS app could count (the layout is per-repository and
    upstream's own `UpdateFetcher` walks `data/sql` itself); `None` means the
    path carried an unresolved `{key}`. The last two are named by their glob
    rather than dropped, because "this module owes SQL and Yu'lon cannot list
    it" is a different thing from "this module owes nothing" -- collapsing them
    is the fault `PendingSql`'s own docstring exists to record.
    """
    names: list[str] = []
    for pending in report.pending_sql:
        names += list(pending.files) if pending.files else [pending.path]
    return tuple(names)


FORGET_RECORD_ACTION = "Forget Yu'lon's record…"
"""The context-menu entry beside Remove on a record-backed mob multiplier row (T121 fix wave)."""

FORGET_RECORD_QUESTION = (
    "Yu'lon forgets that {name} is applied. The database is not changed. Use this only if the "
    "creature values are already at their normal values, for example after restoring a backup."
)
"""What the Forget question says, word for word (T121 fix wave)."""

UNCATALOGUED_PRESS = (
    "This module is installed in this server's folder, but this game's catalog has no "
    "manifest for it, so Yu'lon has no steps to install or remove. Nothing was changed. "
    "It is still compiled into the server and its SQL is still applied by the importer."
)
"""What a press on one of T41's uncatalogued rows says.

The rows exist so somebody can SEE what is installed; Yu'lon cannot act on a
module with no manifest. Saying so is the difference between a control that is
inert and one that looks broken. Since T42 the row carries no Install or Remove
button at all and this sentence is reached through its context menu, which is
the only press such a row still answers.

Its update chip reached this sentence too until T146: Check for updates counts
every folder, so `mod-playerbots` -- cloned by the SERVER install, no manifest
-- carried an Update whose only outcome was "Nothing was changed". Such a row
now gets no update chip, except a folder the server's own update moves, whose
chip runs "Update the server to latest…" instead (`_chip_action_pressed()`).
"""

WHY_UNCATALOGUED = "Why is there no Install or Remove?"
"""The one entry an uncatalogued row's context menu offers, beside Copy Module ID.

Round 2: the menu offered "Install Selected Module" and "Remove Selected Module"
on a row with no manifest. Both ended at `UNCATALOGUED_PRESS`, so nothing was
done to the machine -- but two entries that name an action and then explain that
the action does not exist are two controls that look live and are not, which is
the reading this whole ticket exists to remove. One entry, and it names what it
really does.
"""

REBUILD_BANNER = (
    "A rebuild is owed: {names}. The module is on disk, but the running worldserver was "
    "compiled before it arrived, so nothing it does is live yet."
)
"""The amber banner above the family cards, shown only while something owes a rebuild.

It names the modules rather than saying "a module", because the sentence is
read after several installs and "which one?" is the next question. Session state
only -- see `ControllerView._rebuild_owed`.
"""

MODULE_LOAD_FAILED = "!! could not load {kind}s: {exc}"
"""A family whose manifests will not parse, said in the report box.

It used to be a row in the list. There is no list any more, and a card titled
with an error would be a fifth family; the report is where every other refusal
on this tab is read.
"""

MODULE_SQL_RUNNING = "Running the importer over the modules installed here. What it prints:"
"""The heading above the module importer's live output.

It says what is running rather than what will have happened, because at this
point nothing is known: the importer decides file by file, and a run that
applies nothing at all is a perfectly normal outcome for an install whose
modules are already ledgered in `updates`.
"""

MODULE_SQL_FINISHED = (
    "The importer finished. Any '>> Applying update <file>.sql' line above is a file that was "
    "applied just now; a module already recorded in the database's `updates` table correctly "
    "gets nothing. Modules with C++ code still need a rebuild — that is a separate job."
)
"""What is said at the end, and everything it deliberately does not say.

No count and no "N modules applied". This tab cannot know that number: the
importer works a FILE at a time and names each one itself, so a total invented
here would be the same defect 8.7a's other half was opened for — a module
reported as done while nothing ran.
"""

REPORT_LINES = 6
"""The most lines of a report box that are on screen at once, before it scrolls.

A `QPlainTextEdit` asks for 12 lines whatever is in it, and the Modules tab has
two text boxes under its list, so the default was ~230px of mostly empty text
fields over a module list that had 311 -- two whole rows of forty, measured in
the themed window at 1920x1080, where T44's approved mockup shows ten. Six lines
is what these boxes say: every sentence in the `MODULE_*`/`TUNING_*` constants
above fits, and the outputs with no length limit at all -- a rebuild's, a
database update's -- go to the `LogPanel` beside them and not here.

A CEILING, not a height: `_ReportBox` is as tall as it has something to say, one
line to six. It is empty on every start, which is when the list needs the room.
"""

MODULE_LIST_MIN_HEIGHT = 74
"""The floor under the Modules tab's list of cards, in pixels.

Below this the list is a scrollbar with the top of a family card beside it and
nothing that can be read or pressed. It is the second line of defence and not
the first: what keeps the boxes below from taking the tab is their own ceilings
(`REPORT_LINES`, and `_IdleLogPanel`'s cap), and this is what is left if one of
them is ever wrong again -- an error message wrapped into a report box did
exactly that during T73's own review.

Deliberately SHORTER than one row (85px plus its card's header under this
theme): a floor is paid for by whatever is under it, in clipped text, at the
sizes where the tab is already over-subscribed, and the LIST is the one widget
here that is complete at any height because it scrolls. That rule is what sets
the number, and T83 re-applied it at the smaller of the two window sizes the app
allows: 100 was the largest that left the 1280x800 the app OPENS at fitting with
nothing cut, and 74 is the largest that leaves the 960x600 it can be DRAGGED to
fitting too, now that the action bar above takes a second line there (measured
themed: the tab's layout asked for 452px of a 426px tab at 100, and Qt shares a
shortfall like that across every widget -- five pixels off the bottom of
`Rebuild the server…` and twenty-one off the custom-module card).
"""

MODULE_LIST_ROWS_HEIGHT = 145
"""The list height the custom-module card gives its sentence up to keep (T153).

A family card's header and two whole rows under it, measured themed: the second
row's bottom edge is 139px into the viewport at 960 wide and 143 at 1280, where
the font is larger, plus the scroll area's 2px frame. Below this the list is a
header and a row -- the T89 gate photographed it at less than that, the header
alone, on an install past its pins at 960x640 -- and the card's sentence is the
cheapest height on the tab to buy the second row with.

A second floor, above `MODULE_LIST_MIN_HEIGHT` and defended by a different rung
of `_TabFit`: the log and the report fold to keep THAT one, and only the card
goes to its one line to keep this -- and only where the list would be under it
even with both boxes folded, so a box a taller window can hold open never costs
the card its sentence (T153 round 2). Raising `MODULE_LIST_MIN_HEIGHT` instead
would fold the log and the report at the 1280x800 the app opens at, which T80
and T85 measured and the gates photographed open.

Two rows and not the three the ticket floated, because three is 185px and the
tab does not have it: at 960x640, past the pins and with the bar wrapped, the
list is 152px with the card on one line and the log and the report already
folded -- everything left above and below it is a toolbar line, the version
lines, or a strip whose only way of being shorter is to cut its words.
"""

_LIST_FLOOR_FLOOR = 40
"""The list's floor when even `MODULE_LIST_MIN_HEIGHT` is more than the tab has.

Step 4 of `_TabFit`'s order (3 until T153), and a floor under the floor rather than nothing: a
list drawn at zero is a tab with a gap in it, and the honest answer below this
is a tab that scrolls as a whole -- which it does not do today, and which is a
ticket rather than something to fake here. Forty is a scrollbar's length: enough
that what is there reads as a list somebody can drag.
"""

_NO_HEIGHT_CAP = 16777215
"""Qt's `QWIDGETSIZE_MAX`, which PySide6 does not re-export under any name.

`setMaximumHeight()` takes it to mean "no cap"; it is how `_IdleLogPanel` gives
its height back when a job finally needs it.
"""


class _ReportBox(QPlainTextEdit):
    """A report box as tall as its own text, to a ceiling of `REPORT_LINES`.

    Two things a plain `QPlainTextEdit` gets wrong under a list that wants the
    height: it asks for twelve lines when it is empty, and it asks for them in
    pixels measured once. This asks for what it holds, in lines converted through
    its own `fontMetrics()` on every layout -- so it is still right after the
    theme regenerates its stylesheet at a new font scale, which it does whenever
    the window is resized to a width it has not been styled for.

    `Maximum` vertically: the hint is a ceiling the box will not grow past, and
    it may still be shrunk under it on a small window. The floor stays the one
    the theme sets -- its stylesheet gives every `QPlainTextEdit` a 90px
    min-height so a report reads as a panel rather than a stray line, and that
    is not this ticket's to overrule (T45: the sizes are `theme.py`'s). A floor
    pinned HERE instead, at six lines, is what clipped the report by 21px at the
    size the app opens at (measured 2026-09-16, themed).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        # The document's layout knows its own height in lines, wrapped ones
        # included, and says so when it changes. Without this the box keeps the
        # height it was given before the text arrived.
        self.document().documentLayout().documentSizeChanged.connect(
            lambda _size: self.updateGeometry()
        )

    def _lines_tall(self, count: int) -> int:
        """`count` lines as this box really draws them, plus its own chrome.

        The chrome is MEASURED -- the gap between this widget and its viewport --
        rather than added up from `frameWidth()`. The theme's stylesheet gives
        every text box `padding: 7px 10px`, and padding on a scroll area is
        spent on the viewport's margins, which no property this class can name
        accounts for: a height built from the frame alone is 14px short, most of
        a line, and the box then scrolls the last of the six it was sized for.

        The trailing pixel is not a rounding fudge either: `QPlainTextEdit`
        offers a scrollbar as soon as the document is as tall as the viewport,
        not taller than it.
        """
        chrome = max(0, self.height() - self.viewport().height())
        return (
            math.ceil(self._line_height() * count)
            + int(self.document().documentMargin()) * 2
            + chrome
            + 1
        )

    def _line_height(self) -> float:
        """ONE line as the DOCUMENT lays it out, not as the font describes it.

        `fontMetrics().lineSpacing()` is a rounded integer and the text layout's
        own line height is not: at this theme's size the two differ by a pixel,
        and a pixel a line is a whole line lost over six of them -- measured, as
        a six-line box that scrolled.

        Divided by the block's own line count, which is the correction this
        needed: `blockBoundingRect` is the height of a whole PARAGRAPH, wrapped
        lines and all, and `_lines_held()` counts wrapped lines too. Multiplying
        the two asked 967px for one 120-word paragraph in a 600px-wide box and
        left the list above it nothing at all (review, round 2).
        """
        block = self.document().firstBlock()
        drawn = self.document().documentLayout().blockBoundingRect(block).height()
        block_layout = block.layout()
        wrapped = block_layout.lineCount() if block_layout is not None else 0
        if drawn > 0 and wrapped > 0:
            return drawn / wrapped
        return float(self.fontMetrics().lineSpacing())

    def _lines_held(self) -> int:
        """Lines of text in the box right now, at least one and at most the cap."""
        held = math.ceil(self.document().documentLayout().documentSize().height())
        return max(1, min(REPORT_LINES, int(held)))

    def sizeHint(self) -> QSize:
        return QSize(super().sizeHint().width(), self._lines_tall(self._lines_held()))

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(hint.width(), max(hint.height(), self._lines_tall(1)))


REPORT_STRIP_TITLE = "Last action"
"""The title on a report box's strip, and the first one those boxes have had.

A `_ReportBox` is the answer to the press the user just made, and until T80 it
was an unlabelled field that was 106px of nothing until it had one. The strip
names it AND folds it away, which is why the title arrives with the handle.
"""


class _ReportStrip(CollapseHandle):
    """The strip over a `_ReportBox`: it names the box and folds it away.

    Not part of `_ReportBox` itself, deliberately. `module_report` and
    `tuning_report` are `QPlainTextEdit`s that a dozen call sites write with
    `setPlainText()` and the tests read with `toPlainText()`; wrapping them in a
    container would have moved every one of those onto an inner widget for a
    strip that is one label. The strip OWNS the box instead, and the box is the
    same object it always was.

    **It follows the text rather than remembering a preference**, which is the
    same rule the log's handle follows: open when there is a report, folded when
    there is not. The box is empty on every start -- which is when the list needs
    the room -- and an empty box still costs the 90px min-height the theme gives
    every text field (T45: that floor is not this ticket's to overrule). A user
    who folds a long report away gets the rows back; the next press unfolds it,
    because a press whose answer is hidden is a press that looks like it did
    nothing.
    """

    wants_changed = Signal(bool)
    """What this strip is asked for has moved, and whether a PERSON asked.

    `_TabFit` listens: the room it hands back depends on what is asked for, and
    the `bool` is the difference between the tab folding something for itself and
    a user's press, which outranks the published order (T85).
    """

    def __init__(self, box: QPlainTextEdit, parent: QWidget | None = None) -> None:
        super().__init__(REPORT_STRIP_TITLE, parent)
        self._box = box
        # What was last ASKED for, and whether the tab has the height for it.
        # The fold on screen is derived from the pair -- see `_apply()`.
        self._wants_open = bool(box.toPlainText())
        self._has_room = True
        self._adjusting = False
        self.toggled.connect(self._someone_used_the_handle)
        box.textChanged.connect(self._follow_the_text)
        self._apply()

    def box(self) -> QPlainTextEdit:
        """The box this strip folds, so `_TabFit` can pick it out of the tab."""
        return self._box

    def wants_open(self) -> bool:
        """Whether a report is asked for here, whatever the tab can afford."""
        return self._wants_open

    def box_minimum(self) -> int:
        """What the box under this strip needs, whether it is showing or not.

        Asked while the box is HIDDEN -- that is when `_TabFit` is deciding
        whether to bring it back -- and both halves answer for a hidden widget,
        which is the one thing that makes this two lines rather than a trial
        layout.

        BOTH halves, and the second is the one `_owed()` was missing: a layout
        item's minimum is the larger of the widget's hint and any explicit
        `minimumHeight()` on it, and the theme gives every text field one (T45).
        Reading the hint alone under-counted an open report by 16px -- 90 against
        the 106 the layout really holds back -- so between 1030 and 1080 wide the
        sum came to 522 against a tab of 525 while the tab's real minimum was
        538, the report stayed open, and the custom-module card was drawn 108 of
        its 122. It was invisible at every other width only because the card's
        own wrapped sentence over-claims by about the same amount.
        """
        return int(max(self._box.minimumSizeHint().height(), self._box.minimumHeight()))

    def set_room(self, has_room: bool) -> None:
        """Say whether the tab can hold the box open (`_TabFit` decides, T83)."""
        if has_room == self._has_room:
            return
        self._has_room = has_room
        self._apply()

    def _follow_the_text(self) -> None:
        """A report arrived, or the box was emptied: that is what is asked for now.

        T80's rule, and the reason the user's fold is remembered only until
        here: a user who folds a long report away gets the rows back, and the
        NEXT press unfolds it again, because a press whose answer is hidden is a
        press that looks like it did nothing.
        """
        self._wants_open = bool(self._box.toPlainText())
        self.wants_changed.emit(False)
        self._apply()

    def _someone_used_the_handle(self, folded: bool) -> None:
        """A press by a person is what this strip is asked for from now on.

        Only when `_adjusting` is clear: the same signal is raised by this
        strip's OWN fold, and reading that as consent is how a report box gives
        the height up once and never asks for it again -- the defect this
        object's twin (`_IdleLogPanel`) was caught with at 1280x800.

        And the press is ASKED FOR rather than done: `_apply()` is what puts it
        on screen, and it refuses where the height is not there. Showing the box
        here instead -- which is what this did until round 3 -- opens it at a
        size `_TabFit` has already decided cannot hold it, and nothing puts it
        back, because `set_room(False)` is a no-op when the answer has not
        changed. Measured at 960x600 with a refusal in the box: one click on
        this strip drew the action bar at 76px of its 106 with `Rebuild the
        server…` sliced through, and the card's own label at 9px of 28.
        """
        if self._adjusting:
            return
        self._wants_open = not folded
        # BEFORE `_apply()`: the press is what the room is decided from, and
        # `_TabFit` answers this signal synchronously. Applying first would draw
        # the refusal this press is meant to overturn (T85).
        self.wants_changed.emit(True)
        self._apply()

    def _apply(self) -> None:
        """Open iff a report is wanted there AND the tab has room for it.

        The text half is T80's. The room half is T83's, and it is the second
        thing the Modules tab gives up when it cannot hold everything: below the
        width where the action bar wraps to two lines, a populated report box and
        every other widget's minimum do not fit, and `QBoxLayout` resolves that
        by cutting all of them -- measured at 960x600 with a refusal in the box,
        the action bar was drawn 74px of the 106 its two lines take, with its
        second line of buttons sliced in half. `_TabFit` owns the order in which
        things give and the reason the report goes before the list.

        `set_collapsed` is a no-op when the state already holds, so this does not
        fight the user on every keystroke of a report being written into the box
        a line at a time -- only an edge moves anything.
        """
        if self._adjusting:
            return
        self._adjusting = True
        try:
            self.set_collapsed(not (self._wants_open and self._has_room))
            self._box.setVisible(not self.collapsed)
        finally:
            self._adjusting = False


class _RelayButton(QPushButton):
    """A second face for a button that lives elsewhere: its words, its gate, its press (T153).

    The custom-module card's one-line form carries the card's two presses, and
    these ARE those presses rather than two more buttons to keep in step. The
    enabled state and the tooltip -- which is where a greyed button says why --
    are copied off the source whenever it changes, so the gates that grey the
    card's buttons (`_set_custom_module_buttons`, the busy lock) never need to
    know this form exists. And a press is `source.click()`, which Qt makes a
    no-op on a disabled button: a relay whose greying lagged still could not
    reach a slot its source's gate has closed.
    """

    def __init__(self, source: QPushButton, parent: QWidget | None = None) -> None:
        super().__init__(source.text(), parent)
        self._source = source
        self.clicked.connect(source.click)
        source.installEventFilter(self)
        self._follow()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802  (Qt's own name)
        """The source's greying or its tooltip moved: take the new one."""
        if watched is self._source and event.type() in (
            QEvent.Type.EnabledChange,
            QEvent.Type.ToolTipChange,
        ):
            self._follow()
        return bool(super().eventFilter(watched, event))

    def _follow(self) -> None:
        self.setEnabled(self._source.isEnabled())
        self.setToolTip(self._source.toolTip())


_WHEN_A_MINIMUM_MOVES = (
    QEvent.Type.Polish,
    QEvent.Type.PolishRequest,
    QEvent.Type.Show,
    QEvent.Type.FontChange,
    QEvent.Type.StyleChange,
    QEvent.Type.LayoutRequest,
)
"""The events after which a widget's `minimumSizeHint()` may be a new number.

`Polish` is the one that matters and the one a `changeEvent` handler does not
see: a widget is polished -- given its stylesheet's fonts, margins and borders --
on its way to being shown, which is AFTER every line of the tab that built it has
run. The rest are the ways it can change again afterwards.
"""


LOG_SHARE_OF_THE_TAB = 4
"""The most of its tab a log with a job's output in it may keep, as a divisor.

T80, and it is the whole of what that ticket promises: whatever a rebuild
writes, three quarters of the tab stay with the list above it. T73 lifted the
idle cap to `_NO_HEIGHT_CAP` the first time a job started and never put anything
back -- the honest reading of "a job's output is what the panel is for" -- and
live on a maximised 1080p desktop that was ~400px of log over a list of a row and
a half. The hand restarted the app to reach the next row.

A QUARTER, and the ticket's own suggestion of a third was measured first: a
maximised window on a real 1080p desktop is 47px shorter than the screen (GNOME's
top bar and the title bar), and at that shape a third left the list at 294px and
FOUR rows -- one under the count this is meant to guarantee. A quarter is 213px
there, which is the strip and some eight lines of output, and it leaves five.

Floored at the panel's own minimum by `_share_of_the_tab()`, so on a small window
this can only ever be "as small as the panel is allowed to be" and never a cap
that clips the strip.

The other half of the fix is that the user can fold the panel away entirely, so
this is the number that applies while they have NOT said anything; it is not a
claim that a quarter is always the right amount.
"""


class _TabFit(QObject):
    """What gives when the Modules tab cannot hold everything on it (T83).

    `QBoxLayout`'s answer to a column whose children's minimums add up to more
    than it has is to draw every one of them in proportion -- so the shortfall is
    paid in cut text, spread over whatever happens to be there. Measured at
    960x600 with a refusal in the report box: the action bar was drawn 74px of
    the 106 its two lines need, its second row of buttons sliced through, and the
    custom-module card 74 of 122. Nothing in a layout can prefer one child over
    another, which is why this object exists: the preference is real, and it is
    this.

    **The order things give in, and it is the whole of the class:**

    1. the LOG folds to its strip. It is the biggest single thing on the tab and
       the one designed to fall back -- the strip carries the job's status line,
       the elapsed clock and Stop, with the output one click away.
    2. the REPORT BOX folds to its strip. Same shape, less of it, and the strip
       names what is behind it.
    3. the CUSTOM-MODULE CARD goes to its one-line form (T153): its title and
       its two buttons on one line, without the sentence that explains them.
       Taken only where the list would be under `MODULE_LIST_ROWS_HEIGHT` --
       a family header and two rows -- even with the log AND the report
       folded, so it is the last thing to go before the list: whatever the
       boxes could have given, they are counted as having given it.
    4. the LIST's floor gives. It scrolls, so it is complete at any height; every
       pixel taken from it is a pixel of a row somebody can still scroll to.

    **And what never gives: a toolbar line, and the card's buttons.** Those are
    the things whose only way of being smaller is to cut the words inside them,
    which is the defect this whole ticket is about. The list is last rather than
    first for the same reason the log is first: order by what a shortfall COSTS
    the reader, not by what is easiest to shrink.

    **Why the card goes to a LINE and not to a strip like the two panels (T153).**
    A strip hides what is behind it until it is pressed, and a press is a
    request this object may refuse: at 960x640 with the version lines, the
    banner and the wrapped bar all on screen, the card whole does not fit even
    with the list at `_LIST_FLOOR_FLOOR`, so a folded card's strip would be a
    handle that does nothing and a small window with no way to install a module
    from a link. The line costs 50px where a strip would cost 14, and it keeps
    both presses on screen at every size the window can be.

    **Why the card and the boxes are decided apart (T153 round 2).** Each is
    decided from a sum the other does not change: the boxes with the card
    billed WHOLE, the card with both boxes billed FOLDED. Chained instead --
    the card decided from what the boxes left -- a taller window that could
    keep the report open had less left over for the list than a shorter one
    that folded it, and the card paid: measured past the pins with a rebuild
    owed, the card was whole at 1280x740 and 1280x760 and on one line at
    1280x800, so dragging the window taller took its sentence away. Apart,
    each of the three is shown at every height above the first it is shown
    at, which is `test_a_taller_window_never_takes_back_what_a_shorter_one_
    showed`. It also leaves T80's shot at 1280x800 as it was: both boxes open
    over a list of under two rows, and the card whole, because folding the
    boxes WOULD give the list its rows -- the card does not give its sentence
    for height the boxes are holding.

    The same separation is what stops a flicker. Read off the form on screen,
    a folded card leaves room over, the room says "unfold", and the unfolded
    card says "fold" -- two transitions per settle, the log's flicker (T83) in
    a third widget. And billing the card as its line for the boxes would move
    the widths at which they fold, which T85's presses are measured against.

    **One thing reorders it: a press on a handle (T85).** Steps 1 and 2 are a
    guess at which of the two panels the reader would rather keep, and a user who
    presses a chevron has just said. That panel goes to the END of the order, so
    the other one gives for it -- `_give_order()`, and the alternative was a
    chevron that does nothing at every size where the two do not both fit. The
    list's place is not up for reordering: it is step 3 whoever pressed what,
    because the list is the one widget here that is complete at any height.

    **Decided from the width the tab has NOW**, which is the second half of what
    this class is for. Every height here is a function of the theme, the theme is
    regenerated at every width the window settles at, and a cached minimum is
    stale until its widget has been polished -- so a decision read out of
    `minimumSizeHint()` while a `LayoutRequest` is in flight is taken against
    half-updated numbers. Measured before `_owed()` asked by width: every themed
    restyle below the fold unfolded the log and refolded it, TWICE per settle at
    every width from 940 to 1110, because the action bar's cached minimum still
    said one line when the log was asked and two by the time it was laid out --
    a visible flash on every drag. `minimumHeightForWidth()` at the tab's current
    width is the same question the layout is about to answer, so there is nothing
    to be stale.

    A zero-interval single-shot timer was written first, to coalesce the decision
    to after the layout pass, and it is not here because it was measured to do
    nothing once `_owed()` stopped reading stale numbers: replacing it with a
    direct call left every test green, including the one that counts folds per
    restyle. `_settling` is the guard that remains, and it is a different job --
    telling the log to fold asks for a layout, and the layout asks here again.
    """

    def __init__(
        self,
        tab: QWidget,
        log: _IdleLogPanel,
        report: _ReportStrip,
        listing: QWidget,
        floor: int,
        card: QWidget,
        card_line: QWidget,
        rows_floor: int,
    ) -> None:
        super().__init__(tab)
        self._tab = tab
        self._log = log
        self._report = report
        self._listing = listing
        self._floor = floor
        # T153's rung: the card whole, its one-line form, and the list height
        # the card gives its sentence up to keep.
        self._card = card
        self._card_line = card_line
        self._rows_floor = rows_floor
        self._settling = False
        # The panel a PERSON last asked to see, and the whole of T85's second
        # half. It is not a flag about a fold -- the bug T83's round 3 was
        # caught by -- but a record of a press, and the only thing it does is
        # move that panel to the END of the give order below.
        self._asked_for: _IdleLogPanel | _ReportStrip | None = None
        log.wants_changed.connect(lambda by_hand: self._asked(log, by_hand))
        report.wants_changed.connect(lambda by_hand: self._asked(report, by_hand))
        tab.installEventFilter(self)

    def _asked(self, panel: _IdleLogPanel | _ReportStrip, by_hand: bool) -> None:
        """What is asked for on this tab has moved: decide again.

        `by_hand` is the user's press, and it is what makes a one-way fold
        impossible: at 1280x800 with the rebuild banner up, the log and a
        populated report do not both fit, the published order folds the log, and
        the chevron the user then presses used to do nothing at all --
        `_apply()` derives the fold from `_has_room` and `set_room(False)` is a
        no-op when the answer has not changed. The press moves the log behind the
        report in the order, the report gives instead, and the log opens. Pressing
        the report's own strip puts it back, so neither is privileged: whichever
        one the user last asked to see is the one that stays.
        """
        if by_hand and panel is not self._asked_for:
            self._asked_for = panel
        self.settle()

    def _give_order(self) -> list[QObject]:
        """The panels in the order they give, cheapest to the reader first.

        `LOG, REPORT` is the published order and the one that holds until a
        person says otherwise; the panel they last pressed open goes last.
        """
        order: list[QObject] = [self._log, self._report]
        if self._asked_for is self._log:
            order.reverse()
        return order

    def _fold_until_it_fits(self, list_floor: int) -> dict[QObject, bool]:
        """Everything that is ASKED FOR, minus the rungs that had to go, in order.

        Asked for and not "open": a log the user folded by hand costs this tab
        nothing, and counting it would fold the report to make room for a panel
        nobody wants.

        `list_floor` is what to bill the list at while deciding. `settle()` runs
        this at the list's ordinary floor first, because the list is step 3 and
        nothing may spend it in advance.
        """
        open_now: dict[QObject, bool] = {
            self._log: self._log.wants_open(),
            self._report: self._report.wants_open(),
        }
        for panel in self._give_order():
            if (
                self._owed(
                    log_open=open_now[self._log],
                    report_open=open_now[self._report],
                    list_floor=list_floor,
                )
                <= self._tab.height()
            ):
                break
            open_now[panel] = False
        return open_now

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Anything that can move a height on this tab asks for a fresh decision.

        A resize is the tab's own; a layout request is a CHILD's minimum moving,
        which the tab's size does not report -- the action bar re-states its own
        whenever a width wraps it (`FlowBar.resizeEvent`), and without that half
        the log decided it fitted while the bar still claimed one line.
        """
        if event.type() in (QEvent.Type.Resize, QEvent.Type.LayoutRequest):
            self.settle()
        return bool(super().eventFilter(watched, event))

    def settle(self) -> None:
        """Apply the order above to the tab as it is now.

        `_settling` keeps the decision out of its own way: telling the log to
        fold asks for a layout, the layout asks here again, and the answer would
        be taken from a tab halfway through being re-laid -- and there would be
        no bottom to the recursion.

        **The order is not fixed**: `_give_order()` puts the panel a person
        last pressed open at the end of it. Measured themed at 1280x800 with the
        rebuild banner on screen, which is the state gate round 6 photographed:
        the tab is 620px, the banner costs it 52, and with an open log AND a
        populated report the tab's own sum is 668. One of the two has to go, the published
        order sends the log, and the chevron the user then pressed did nothing at
        all -- `_apply()` derives the fold from `_has_room`, and
        `set_room(False)` is a no-op when the answer has not changed. It is the
        ORDER that answers that press, not the panel: with the log at the end of
        it the report gives instead, at 556 against the tab's 620.
        """
        if self._settling:
            return
        self._settling = True
        try:
            height = self._tab.height()
            open_now = self._fold_until_it_fits(self._floor)
            asked = self._asked_for
            # And the list's own rows are the last thing a PRESS can spend, which
            # is where the ticket's sentence ends: the user's unfold wins
            # whenever the tab can hold that panel at its minimum, and the panel's
            # minimum is taken with everything below it on the ladder given --
            # the other panel folded AND the list at `_LIST_FLOOR_FLOOR`.
            #
            # Only for a press, and that is the whole reason this is a second
            # pass rather than the floor the first one uses. Spending the list on
            # the tab's OWN decision inverts the order: measured at 1000x700 with
            # a job's log it kept the log open nobody had asked about and took
            # the list to 47px to do it, which is the shortfall landing on the
            # widget the published order protects most. Two pixels is what it
            # buys at a 554px tab, which is a 1252x734 client -- what a 1280x800
            # window leaves once a window manager's frame is taken off it.
            if asked is not None and asked.wants_open() and not open_now[asked]:
                with_the_list = self._fold_until_it_fits(_LIST_FLOOR_FLOOR)
                if with_the_list[asked]:
                    open_now = with_the_list
            log_room = open_now[self._log]
            report_room = open_now[self._report]
            self._log.set_room(log_room)
            self._report.set_room(report_room)
            # Step 3 (T153): what the list would have with the card whole AND
            # both boxes folded, the list billed at nothing so the answer is
            # its whole share. Asked of neither box's decision above, so the
            # card cannot fold because a taller window kept a box open -- see
            # the class docstring -- and asked with the card WHOLE whichever
            # form is showing, so the answer cannot depend on its own result.
            rows = height - self._owed(log_open=False, report_open=False, list_floor=0)
            card_whole = rows >= self._rows_floor
            self._show_the_card(whole=card_whole)
            spare = height - self._owed(
                log_open=log_room, report_open=report_room, card_whole=card_whole
            )
            # Step 4: the list gives what is still missing, and never below a
            # scrollbar's worth -- under that it is not a list at all, and the
            # honest end of this ladder is a tab that scrolls (which it does not
            # today, and which is a ticket rather than a silent cut here).
            #
            # `_LIST_FLOOR_FLOOR` flat, and NOT floored at the list's own
            # `minimumSizeHint()`, which is what this read until T85 on the
            # belief that Qt ignores a minimum under a widget's hint. It does
            # not: `qSmartMinSize` takes an explicit `minimumHeight()` over the
            # hint whenever it is above zero, measured 74/40/20/1 against the
            # layout item and honoured at every one. The hint was 70 where the
            # ladder needed 40, and those 30 pixels were the last rung -- so at
            # the 960x600 the app then allowed, with the banner up, the
            # shortfall went past the list and was
            # spread over the two widgets whose only way of being shorter is to
            # cut the words in them: the wrapped action bar was drawn 82px of
            # its 106 with `Rebuild the server…` sliced through, and the
            # custom-module card 82 of 122 with both its buttons below its edge.
            wanted = self._floor if spare >= 0 else max(_LIST_FLOOR_FLOOR, self._floor + spare)
            if self._listing.minimumHeight() != wanted:
                self._listing.setMinimumHeight(wanted)
        finally:
            self._settling = False

    def _show_the_card(self, whole: bool) -> None:
        """Put the card whole, or its one line, on screen -- only on a change (T153).

        A `setVisible()` that repeats the state it is in is cheap, but this runs
        on every settle and each real change asks for a layout; the check keeps
        the event filter's round trip to the one that matters.
        """
        if self._card.isHidden() == (not whole):
            return
        self._card.setVisible(whole)
        self._card_line.setVisible(not whole)

    def _inner_width(self) -> int:
        """The width the tab's layout gives its children: the tab less its margins."""
        box = self._tab.layout()
        if box is None:
            return self._tab.width()
        margins = box.contentsMargins()
        return self._tab.width() - margins.left() - margins.right()

    def card_minimum(self) -> int:
        """What the card needs WHOLE at the tab's width now, shown or not (T153).

        Asked while the card is HIDDEN -- that is when step 3 is deciding whether
        to bring it back -- and a hidden widget's layout item answers nothing
        (its `minimumHeightForWidth()` is -1 and its `minimumSize()` 0), so the
        card's own layout is asked instead. The sentence in it wraps, which makes
        this a function of the width: `minimumSizeHint()` alone is the
        under-count `_owed()`'s comment records (122 said, 136 needed at
        1000x700). Both halves and the explicit minimum, for `box_minimum()`'s
        reason.
        """
        need = max(self._card.minimumSizeHint().height(), self._card.minimumHeight())
        layout = self._card.layout()
        if layout is not None:
            need = max(need, layout.totalMinimumHeightForWidth(self._inner_width()))
        return int(need)

    def _card_line_minimum(self) -> int:
        """What the card's one-line form needs. One row of buttons; it does not wrap."""
        line = self._card_line
        return int(max(line.minimumSizeHint().height(), line.minimumHeight()))

    def _owed(
        self,
        log_open: bool,
        report_open: bool,
        list_floor: int | None = None,
        card_whole: bool = True,
    ) -> int:
        """The height this tab needs with the log and the report in those states.

        Arithmetic over the children rather than a trial layout, because a trial
        layout is a layout: it would move widgets on screen, ask this object to
        decide again, and be the flicker it exists to remove. Both panels can
        say what they need in either state whichever they are in now
        (`open_minimum()`, `folded_minimum()`, `box_minimum()`) for exactly this
        reason.

        Every visible child is counted, whichever it is and whenever it
        arrives -- the walk is over the layout and the only widgets it names are
        the three this object can fold. The rebuild banner is one of the
        unnamed ones: it is `setVisible(True)` the moment an install says a
        compile is owed, and it costs this tab 68px at 960 wide (62 of banner
        and the spacing above it) and 52px at 1280, where the theme's font is
        smaller.

        `list_floor` is what to bill the LIST at, and it defaults to the floor
        this tab keeps when it is not short. `settle()` passes the bottom of the
        ladder instead in the one case where the list may be spent in advance:
        answering a press.

        `card_whole` is which form of the custom-module card to bill (T153):
        exactly one of the two is on the tab, and each is counted in the state
        asked about rather than the one it is in -- the card through
        `card_minimum()`, the line through its own hint.
        """
        if list_floor is None:
            list_floor = self._floor
        box = self._tab.layout()
        if box is None:
            return 0
        margins = box.contentsMargins()
        owed = margins.top() + margins.bottom()
        inner = self._inner_width()
        # Whether the card's slot is on screen at all, read off either form: a
        # tab that is not showing has nothing visible, and every other widget
        # below is skipped by the same test.
        card_on_screen = self._card.isVisible() or self._card_line.isVisible()
        shown = 0
        for index in range(box.count()):
            item = box.itemAt(index)
            widget = None if item is None else item.widget()
            if item is None or widget is None:
                continue
            if widget is self._card or widget is self._card_line:
                if not card_on_screen or widget is not (
                    self._card if card_whole else self._card_line
                ):
                    continue
                shown += 1
                owed += self.card_minimum() if card_whole else self._card_line_minimum()
                continue
            if widget is self._report.box():
                if not report_open:
                    continue
                shown += 1
                owed += self._report.box_minimum()
                continue
            if not widget.isVisible():
                continue
            shown += 1
            if widget is self._log:
                owed += self._log.open_minimum() if log_open else self._log.folded_minimum()
            elif widget is self._listing:
                owed += list_floor
            else:
                # The item's minimum at THIS WIDTH, which is the quantity
                # `QBoxLayout` shares out, and asked for the way it asks:
                # `minimumHeightForWidth()` where the item has one and
                # `minimumSize()` where it does not. Neither shortcut works.
                # `minimumSizeHint()` alone under-counts every widget whose
                # height is a function of its width -- the custom-module card
                # says 122 and needs 136 at 1000x700, because the sentence in it
                # wraps to a third line there, and the sum came out 522 against
                # a tab of 525 while the layout wanted 538, so nothing folded
                # and the card was drawn 109. `heightForWidth()` is the other
                # way wrong: it is the PREFERRED height, which over-counts, and
                # a tab that thinks it is short folds things it did not need to.
                # (The card itself is counted above since T153, by
                # `card_minimum()`, which asks its own layout the same question
                # because a hidden card's item answers nothing.)
                need = item.minimumSize().height()
                if item.hasHeightForWidth():
                    need = max(need, item.minimumHeightForWidth(inner))
                owed += need
        return owed + box.spacing() * max(0, shown - 1)


class _IdleLogPanel(LogPanel):
    """A `LogPanel` that starts folded away and never takes more than its share.

    The Modules tab's log is empty on every start -- it carries a rebuild's or a
    database update's output, and neither has run -- and an empty panel asking
    for its full 240px was a quarter of the tab's height spent on nothing.

    Folded rather than hidden: the strip with the handle, the elapsed field and
    the Stop button stays on screen, so the panel is where it was when a job does
    start. It unfolds itself the first time a run starts -- a job's output is
    then worth the height -- and from that point it is capped at
    `LOG_SHARE_OF_THE_TAB`, so the list above it is never the thing that pays.

    **Nothing here is on a timer.** The panel changes height when a job starts or
    when the user uses the handle, and at no other moment: a log that folded
    itself away after a grace period would take the failure line off the screen
    of the reader who was reading it, and every test of that rule would be a test
    of a `QTimer` (T80).

    The floor under the cap is this panel's OWN `minimumSizeHint()`, re-read on
    every event that can change it rather than measured once. A cap taken in
    `__init__` is taken before the panel has a parent, and the theme it will be
    styled by is applied to the WINDOW (`apply_dadcraft_theme(window)` in
    `build_window`) before this object exists: measured 2026-09-16 in that order,
    the cap came out 152px against a minimum of 180, so the panel was drawn 148
    -- 32px under its own floor, with the text pane clipped -- and it stayed
    there, because the next restyle only happens if the window is resized to a
    NEW width and the app opens at the one the theme was already generated for.
    """

    wants_changed = Signal(bool)
    """`_ReportStrip.wants_changed`'s twin, and it carries the same `bool`."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._watching: QWidget | None = None
        # What was last ASKED for, and whether the tab has the height for it.
        # The fold on screen is derived from the pair -- see `_apply()`.
        self._wants_open = False
        self._has_room = True
        self._adjusting = False
        self.set_collapsed(True)
        self.collapse_toggled.connect(self._someone_used_the_handle)
        self.run_started.connect(self._give_it_the_room)
        self._watch_the_tab()
        self._apply()

    def _share_of_the_tab(self) -> int:
        """The tallest this panel may be drawn right now, in pixels.

        Folded, that is its own minimum -- the strip and nothing else. Open, it
        is a quarter of the tab it sits in, floored at that same minimum so the cap
        can never clip the strip on a small window (the tab is 600px at the
        smallest the window can be dragged to, and a third of that is less than
        the strip needs).

        Measured off the PARENT and not off the window: what the list is losing
        is the tab's height, and the tab is the widget whose layout this panel
        and that list are both in.
        """
        floor = self.minimumSizeHint().height()
        if self.collapsed:
            return floor
        tab = self.parentWidget()
        if tab is None:
            return _NO_HEIGHT_CAP
        return max(floor, tab.height() // LOG_SHARE_OF_THE_TAB)

    def open_minimum(self) -> int:
        """What this panel needs with its text pane showing, open or not (T83).

        Derived and not remembered. `_TabFit` has to ask this while the panel is
        FOLDED -- that is the whole question it is deciding -- and a value kept
        from the last time the panel happened to be open is a value from before
        the last restyle, on a tab whose every height is a function of the
        window's width. Folded, `minimumSizeHint()` is the strip's, and what the
        pane adds back is its own minimum plus the one gap between them.
        """
        if not self.collapsed:
            return int(self.minimumSizeHint().height())
        return int(self.minimumSizeHint().height() + self._pane_minimum() + self._gap())

    def folded_minimum(self) -> int:
        """What this panel needs with its text pane away: the strip, and nothing else.

        `open_minimum()`'s mirror, and derived for the same reason: `_TabFit`
        asks it while the panel is OPEN, which is when the answer is not the one
        `minimumSizeHint()` gives.
        """
        if self.collapsed:
            return int(self.minimumSizeHint().height())
        return int(self.minimumSizeHint().height() - self._pane_minimum() - self._gap())

    def _pane_minimum(self) -> int:
        """What the text pane holds back, and it is BOTH of the numbers it has.

        `_ReportStrip.box_minimum()`'s trap, met a second time in the panel that
        object was written next to (T85): a layout item's minimum is the larger
        of the widget's hint and any explicit `minimumHeight()` on it, and the
        theme gives every `QPlainTextEdit` a 90px min-height while the one on
        this panel really holds back 106. Reading the hint alone made
        `open_minimum()` answer 16px light while the panel was FOLDED and the
        truth while it was open, so `_TabFit` found room, opened the panel, was
        asked again with the honest number, found none and folded it -- a log
        that flickered open and shut on one resize, measured themed at 1280x800
        with the rebuild banner up.
        """
        return int(max(self._text.minimumSizeHint().height(), self._text.minimumHeight()))

    def _gap(self) -> int:
        """The one space between this panel's strip and its text pane."""
        layout = self.layout()
        return 0 if layout is None else max(0, layout.spacing())

    def set_room(self, has_room: bool) -> None:
        """Say whether the tab can hold this panel open (`_TabFit` decides, T83)."""
        if has_room == self._has_room:
            return
        self._has_room = has_room
        self._apply()

    def _apply(self) -> None:
        """Put the derived fold on screen, and write the cap T80 sets.

        **The fold is DERIVED and not remembered**: from what was last asked for
        (`_wants_open`, which is the handle and `run_started`) and whether the
        tab has the height for it (`_has_room`, which is `_TabFit`'s answer).
        The first version remembered the DECISION instead, in a flag saying "I
        folded this myself", and it was wrong on the very screen it was written
        for -- one missed transition among the signals `set_collapsed()` raises
        left the flag saying the fold had been the user's, so the log stayed
        folded at 1280x800 with 312px of room for it and nothing later could put
        it right. A derived state has no missed transitions to survive.

        Writing the cap only when it CHANGES is what keeps this safe to call
        from a layout: `setMaximumHeight` asks for another layout, so a cap
        written unconditionally would ask for one forever. `_adjusting` is the
        same guard around the fold, and it does a second job:
        `_someone_used_the_handle()` reads it to tell a press by a PERSON from
        this method's own `set_collapsed()`.
        """
        if self._adjusting:
            return
        self._adjusting = True
        try:
            self.set_collapsed(not (self._wants_open and self._has_room))
            wanted = self._share_of_the_tab()
            if self.maximumHeight() != wanted:
                self.setMaximumHeight(wanted)
        finally:
            self._adjusting = False

    def _someone_used_the_handle(self, folded: bool) -> None:
        """A press by a person is what this panel is asked for from now on.

        Only when `_adjusting` is clear: the same signal is raised by this
        panel's OWN fold, and reading that as consent is how a log gives the
        height up once and never asks for it again.
        """
        if not self._adjusting:
            self._wants_open = not folded
            # `_ReportStrip._someone_used_the_handle`'s reason for emitting
            # before the fold is applied: the press is what the room is decided
            # from (T85).
            self.wants_changed.emit(True)
        self._apply()

    def _give_it_the_room(self) -> None:
        """A job started: ask to be open, and take a quarter of the tab to say it in.

        ASKS rather than unfolds. On a window with the room this is the unfold
        T80 promises; on one without it an open log would be a cut log, and the
        strip a `LogPanel` shows while it is folded already carries the line
        saying a job is running and the Stop button that ends it.
        """
        self._wants_open = True
        self.wants_changed.emit(False)
        self._apply()

    def wants_open(self) -> bool:
        """Whether this panel is asked for open, whatever the tab can afford."""
        return self._wants_open

    def _watch_the_tab(self) -> None:
        """Follow the parent's resizes, because the cap is a share of them.

        A `QLayout` sends `LayoutRequest` to the widget it lays out -- the tab --
        and not to the children it moves, and a child whose geometry the layout
        did not change sees no event at all. So a window dragged taller would
        leave this panel capped at a third of the size the tab USED to be, which
        is a cap that only ever shrinks. Watching the parent is the one place
        that number changes.
        """
        tab = self.parentWidget()
        if tab is self._watching:
            return
        if self._watching is not None:
            self._watching.removeEventFilter(self)
        self._watching = tab
        if tab is not None:
            tab.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """The tab resized: the cap is a share of its height, so re-write it."""
        if event.type() == QEvent.Type.Resize:
            self._apply()
        return bool(super().eventFilter(watched, event))

    def event(self, event: QEvent) -> bool:
        handled = super().event(event)
        if event.type() == QEvent.Type.ParentChange:
            self._watch_the_tab()
        if event.type() in _WHEN_A_MINIMUM_MOVES:
            self._apply()
        return handled


TUNING_SAVED = (
    "{module}: wrote {keys} in {file}. A backup of the file as it was is beside it at "
    "{backup}.\n{rule}"
)
"""What a guided save reports: what moved, where, and what it costs to apply.

The backup's path is named rather than implied, because Revert is one press and
the file is one a person may also want to look at by hand.
"""

TUNING_NOTHING_CHANGED = "{module}: nothing on this card was changed, so nothing was written."

TUNING_REFUSED = "{module}: nothing was written — {why}"
"""A refusal that names the key, in the box every other answer on this tab is read in.

`tuning.write()` checks every value before it touches the file, so this really
does mean nothing was written, and saying so is the difference between a user
who fixes one field and a user who wonders what state their conf is in.
"""

TUNING_REVERTED = "{module}: put {file} back from {backup}.\n{rule}"

TUNING_PUT_BACK_DURING_RESTORE = (
    "{press}: a restore is running on the Maintenance tab, so nothing was put back. Press it "
    "again once the restore has finished."
)
"""T145: Tortoise's restore sets aiplayerbot.conf's rebuild request off as it starts, on its
worker, and a put-back at the same moment would race that write."""

RESTORE_DURING_PUT_BACK = (
    "Nothing was restored: a backup is being put back on the Tuning tab, and it writes "
    "aiplayerbot.conf, which a restore also changes. Press Restore again once it has finished."
)
"""T145 round 7: the other order of `TUNING_PUT_BACK_DURING_RESTORE`."""

TUNING_PUT_BACK_WHILE_BUSY = (
    "{press}: another action is running on this server, so nothing was put back. Press it "
    "again once it has finished."
)

TUNING_NO_BACKUP = (
    "{module}: there is no backup of {file} to revert to. Yu'lon takes one every time it "
    "saves, so the first save is what creates it."
)

TUNING_FILE_SAVED = "Wrote {file}. A backup of it as it was is beside it at {backup}.\n{rule}"

TUNING_FILE_FAILED = "{file} was NOT written: {exc}"

TUNING_LINT_CONFIRM_TITLE = "Save this file anyway?"

MODULE_ACTION_STEPS: dict[str, When] = {
    "install": "install",
    "remove": "remove",
    "update": "install",
}
"""Which of a manifest's STEPS each press on the Modules tab runs.

Three presses, three routes on the applier, but only three kinds of step exist
(`When`) -- an update re-runs the INSTALL-time ones, because over a clone that
has moved that is exactly what it is. Spelled here rather than cast at the call
site: `When` is the manifest schema's own word and `"update"` is not one of
them, so a `cast()` would have been this view telling the type checker
something the schema does not say.
"""

TUNING_RELOAD_LABEL = "Reload from disk"
TUNING_REVERT_ALL_LABEL = "Revert all changes"
TUNING_RECREATE_LABEL = native.RECREATE_CONTAINERS_LABEL
TUNING_RESTART_LABEL = "Restart server…"
"""The Tuning tab's action bar (T44 item 7). Both ellipses are this app's own
convention for "this opens a dialog first", and both of these take the server
down."""

TUNING_RECREATE_TIP = (
    "Stop and DELETE this install's containers, then start them again from the current "
    "configuration. Your characters are not affected — the database lives in a Docker volume, "
    "which is kept. Needed for a setting the running containers do not read from your disk."
)

TUNING_RESTART_TIP = (
    "Stop the server and start it again, so the world re-reads the conf files on your disk. "
    "Everybody online is disconnected."
)

TUNING_RESTART_CONFIRM = (
    "Restart the server now?\n\nThe world stops and starts again, so it re-reads the conf "
    "files on your disk. Anybody playing is disconnected. Waiting on a restart:\n{files}"
)

TUNING_RECREATE_CONFIRM = (
    "Recreate the containers now?\n\nThis install's containers are DELETED and created again "
    "from the current configuration. Your characters are not affected — the database lives in "
    "a Docker volume, which is kept — but the server goes down and comes back up, which takes "
    "longer than a restart. Waiting on a recreate:\n{files}"
)

TUNING_BANNER = "Waiting on a {job}: {files}"
"""The Tuning tab's banner (T44 item 8), naming the DEAREST job owed and its files."""

TUNING_JOB_WORDS: dict[str, str] = {"recreate": "recreate", "restart": "restart"}

DISTRO_STOPPED = (
    "This server's WSL distro {distro} is stopped. Yu'lon reads nothing inside it while it is, "
    "because reading it would start it: press Start to start it, and the other tabs fill in "
    "once it is up."
)
"""The Server tab's line while the distro is stopped (T133, wsl-resident-servers §2)."""

DISTRO_UNKNOWN = (
    "Yu'lon couldn't ask WSL whether the distro {distro} is running, so it reads nothing inside "
    "it, because reading a stopped distro would start it: press Start to start it, and the "
    "other tabs fill in once it is up."
)
"""The same line when WSL's listing did not answer (T133 review): no permission to read."""

REPAIR_FILES_LABEL = native.REPAIR_FILES_LABEL
"""T106's press: re-render this install's docker-compose.yml from the current template.
T137's, on the same button: write a module conf the install writes from its `.dist`.
Spelled in `native` since T170, whose engine sentences send players to it."""

REPAIR_FILES_OWED = composegen.BASE_FILE
"""What a repair adds to the tab's owed-a-recreate set, and the banner looks for."""

REPAIR_FILES_BANNER = (
    "This server's docker-compose.yml differs from what this version of Yu'lon writes for it — "
    "it was written by another version, or edited by hand. Repair server files… writes it the "
    "way this version does and keeps the current file as a backup. Nothing changes until you "
    "press it."
)

REPAIR_FILES_CONFIRM = (
    "Repair this server's files now?\n\ndocker-compose.yml differs from what this version of "
    "Yu'lon writes for this server, either because another version wrote it or because it was "
    "edited by hand. Yu'lon writes it again the way this version installs it, with this "
    "install's own project name, ports and SELinux labels{counts}. Any hand edits in it are "
    "replaced; the file as it is now is kept beside it as a backup ({backup}).{confs}\n\n"
    "Nothing else changes: not your characters, not {others}, not docker-compose.override.yml "
    "or .env. The running containers keep the old file until they are recreated, which Yu'lon "
    "offers next."
)

REPAIR_FILES_CONFS = (
    "\n\nIt also sets {settings}, so the server writes its log files into the server folder, "
    "where you can read them and a recreate keeps them. Only that line changes, and each file "
    "is kept beside itself as a backup too."
)
"""T169's paragraph in the question, when the same repair sets a conf's folder setting."""

REPAIR_FILES_DONE = (
    "docker-compose.yml was repaired; the old one is kept as {backup}. The containers still run "
    "the old file until they are recreated: press Recreate containers… (the server goes down "
    "and comes back up; your characters are kept)."
)

REPAIR_FILES_UPSTREAM_BANNER = (
    "This server's docker-compose.yml is the one that came with the server's source code, not "
    "the one Yu'lon writes for it — an update that could not finish leaves it there — so "
    f"{server_build_presses.under_server_build(server_build_presses.REBUILD)} and "
    f"{server_build_presses.under_server_build(server_build_presses.UPDATE_TO_LATEST)} refuse "
    "this folder. Repair server files… writes Yu'lon's file over it and keeps it as a backup. "
    "Nothing changes until you press it."
)
"""T170: the banner for `upstream`, the repository's own file in a WotLK folder."""

REPAIR_FILES_UPSTREAM_DONE = (
    "docker-compose.yml is Yu'lon's again; the one that came with the source code is kept as "
    "{backup}. Press Recreate containers… so the containers are made from Yu'lon's file (the "
    "server goes down and comes back up; your characters are kept)."
)
"""T170: `REPAIR_FILES_DONE` for `upstream`. Its containers never ran the file that was
replaced, so "they still run the old file" would not be true of them."""

REPAIR_FILES_UPSTREAM_CONFIRM = (
    "Repair this server's files now?\n\ndocker-compose.yml in this folder is the one that came "
    "with the server's source code, unchanged from what git has — the file an update puts back "
    "when it cannot finish — and not the one Yu'lon writes for this server. Yu'lon writes its "
    "own again, the way this version installs it, with this install's own project name, ports "
    "and SELinux labels{counts}. The file as it is now is kept beside it as a backup "
    "({backup}).\n\nNothing else changes: not your characters, not your .conf settings, not "
    "docker-compose.override.yml or .env. Recreate containers…, which Yu'lon offers next, "
    "starts the containers from Yu'lon's file."
)
"""T170: the confirmation for `upstream`. Nothing in it is a hand edit to warn about."""

REPAIR_FILES_OFFERED = ("stale", "upstream")
"""The `ComposeCheck` states the Server tab offers Repair server files… on (T106, T170)."""

LOCK_FOLDER_BANNER = (
    "{readers} can read this server's folder, {folder}, and with it the database password in its "
    "files. Repair server files… locks it to your Windows account. Nothing changes until you "
    "press it."
)
"""T174's banner, for a Windows server folder somebody else can read (`exposed`)."""

LOCK_FOLDER_CONFIRM = (
    "Lock this server's folder to your Windows account?\n\nToday {readers} can read {folder}. "
    "Yu'lon gives it its own permissions instead: full control for your account, SYSTEM and "
    "Administrators, and nothing for anyone else, handed down to every file and folder inside "
    "it. The .env file, the settings files and their backups hold the database password, so "
    "after this the computer's other accounts cannot read them.{chosen}\n\nNothing is moved or "
    "rewritten, and nothing needs restarting: the server keeps running, and Docker Desktop "
    "reads and writes the folder as before. On a large server folder this can take a minute."
)

LOCK_FOLDER_CHOSEN = (
    "\n\n{names} {verb} access to this folder that somebody gave on this PC, and {loses} it "
    "too. If {pronoun} still {needs} it, it has to be given back by hand afterwards."
)
"""The confirmation's paragraph for readers that are not a broad default group (round 2)."""


def _and_list(names: Sequence[str]) -> str:
    """`A`, `A and B`, `A, B and C`."""
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def lock_folder_question(folder: Path, check: serverlock.FolderLockCheck) -> str:
    """The question before a lock, naming who can read the folder today and who loses access."""
    chosen = ""
    if check.chosen:
        many = len(check.chosen) > 1
        chosen = LOCK_FOLDER_CHOSEN.format(
            names=_and_list(check.chosen),
            verb="have" if many else "has",
            loses="lose" if many else "loses",
            pronoun="they" if many else "it",
            needs="need" if many else "needs",
        )
    return LOCK_FOLDER_CONFIRM.format(
        readers=_and_list(check.readers), folder=folder, chosen=chosen
    )


LOCK_FOLDER_DONE = (
    "{folder} is locked to your Windows account: only you, SYSTEM and Administrators can open it "
    "or anything in it."
)

LOCK_FOLDER_ALREADY = "{folder} was already locked to your Windows account; nothing was changed."

LOCK_FOLDER_FAILED = (
    "The server folder was not locked: {reason}. Its files keep the permissions they had, and "
    "the server runs as before."
)

REPAIR_CONFS_BANNER = (
    "This server has no {files}, only the {dists} it is made from, so the bots run on their "
    "built-in settings and My Party, the bot prefix and the Tuning tab have no file to read. "
    "Repair server files… writes {files} as a copy of {dists}, as a fresh install now does. "
    "Nothing changes until you press it."
)
"""T137's banner, for an install from before the install wrote the module conf."""

REPAIR_CONFS_CONFIRM = (
    "Repair this server's files now?\n\nYu'lon writes {files} as an exact copy of {dists}, the "
    "settings file the module ships, which is what a fresh install now does. No file that is "
    "there is changed. The bot population and the command channel stay as they are: Yu'lon "
    "sets those in docker-compose.override.yml, which wins over this file.\n\nThe world "
    "server reads {files} when it starts, so it takes effect after a restart, which Yu'lon "
    "offers next."
)

REPAIR_CONFS_DONE = (
    "{files} was written from {dists}. The world server reads it when it starts: press Restart "
    "server… (anybody playing is disconnected)."
)


def _conf_names(files: Sequence[str]) -> dict[str, str]:
    """`files` and their `.dist`s as the T137 sentences name them: base names, joined."""
    names = [Path(file).name for file in files]
    return {
        "files": " and ".join(names),
        "dists": " and ".join(f"{name}{azerothcore.DIST_SUFFIX}" for name in names),
    }


TUNING_REVERTED_FILE = "Put {file} back from {backup}.\n{rule}"

TUNING_ALL_REVERTED = (
    "Every control on this tab is back to what its file says. Nothing was written — this is "
    "the undo for what you typed here, not for what you saved."
)

TUNING_NO_FILE_BACKUP = (
    "There is no backup of {file} to revert to. Yu'lon takes one every time it saves, so the "
    "first save on this tab is what creates it."
)

TUNING_NAMED_BACKUP_GONE = (
    "The backup Revert file names, {backup}, is no longer beside {file}, so nothing was written."
)

TUNING_CORE_FILE = (
    "This is the server's own configuration, not a module's. Yu'lon shows it read-only in "
    "this version: who owns core configuration is a bigger question than one module's conf."
)
"""Why `worldserver.conf` is listed but not editable here (T43's own follow-up)."""


@dataclass(frozen=True)
class UndoLookup:
    """One answer to "what would Undo the last reset put back", and which reload asked."""

    generation: int
    items: tuple[reset_defaults.FileResult, ...]


@dataclass(frozen=True)
class PressAnswer:
    """One press's own facts, read fresh by a job, and which press and reload asked.

    Codex (final pass): the question must describe the files as they are WHEN
    the player is asked -- not as a reload hours earlier found them.
    """

    token: int
    generation: int
    facts: reset_defaults.PressFacts
    error: str = ""
    """Why the files could not be read -- carried in the tagged answer, so a failure is
    checked against the waiting press exactly as a success is (Codex, last pass)."""


@dataclass(frozen=True)
class _PressAsking:
    """A press waiting for its own facts: which press, which reload, and what it asked for."""

    token: int
    generation: int
    files: tuple[str, ...]
    keys: dict[str, tuple[str, ...]]
    modules: list[str]


def _read_press_facts(
    entry: CatalogEntry, server_dir: Path, files: tuple[str, ...], token: int, generation: int
) -> PressAnswer:
    """A press's facts, as a job: a stat per file and a read of the base compose file.

    A failure comes back INSIDE the answer, tagged with its press: the job
    runner's failure callback is a bound slot with no way to say which press
    failed, and an untagged failure let a stale press clear the waiting one.
    """
    try:
        facts = reset_defaults.press_facts(entry, server_dir, files)
    except Exception as exc:  # boundary: an unreadable server folder must not kill the UI
        return PressAnswer(token, generation, reset_defaults.PressFacts(), error=str(exc))
    return PressAnswer(token, generation, facts)


def _undo_still_undoable(
    server_dir: Path,
    items: tuple[reset_defaults.FileResult, ...],
    rebuild: BotPoolRebuildSeam | None = None,
) -> reset_defaults.ResetReport:
    """The Undo press's job: re-check each item NOW, then undo what still needs it.

    The items come from the last lookup, which may be older than the files: a
    file put back by hand since must not be backed up and copied over again.
    `rebuild` (Tortoise, T145): each backup goes back through its
    `put_back_file`, so a reset's backup never re-arms a random-bot rebuild.
    """
    undoable = reset_defaults.still_undoable(server_dir, items)
    if rebuild is None:
        return reset_defaults.undo(server_dir, undoable)
    return reset_defaults.undo(
        server_dir,
        undoable,
        restore=lambda backup, target: rebuild.put_back_file(
            backup, target, reset_defaults.restore
        ),
    )


def _look_up_undo(
    entry: CatalogEntry,
    server_dir: Path,
    session: tuple[reset_defaults.FileResult, ...],
    generation: int,
) -> UndoLookup:
    """The Tuning tab's Undo lookup, as a job: it lists folders and reads files (T94)."""
    return UndoLookup(generation, reset_defaults.undo_items(entry, server_dir, session))


@dataclass(frozen=True)
class BotCountAnswer:
    """One read of the bot count, and which lookup asked (an older answer is dropped)."""

    generation: int
    reading: botpop.Reading


class BotCountReadFailed(Exception):
    """A read that raised, tagged with the lookup that asked, so a stale one can be dropped."""

    def __init__(self, generation: int, why: str) -> None:
        super().__init__(why)
        self.generation = generation


def _read_bot_count(route: botpop.BotPopulationRoute, generation: int) -> BotCountAnswer:
    """The Bots tab's read, as a job: it opens a conf or the compose override (T99).

    A failure is re-raised TAGGED with its lookup (review Minor 3): the job
    runner hands the failure slot only the exception, and an untagged one let an
    old read's failure free the box while a newer read was still pending.
    """
    try:
        return BotCountAnswer(generation, route.read())
    except Exception as exc:  # boundary: an unreadable server folder must not kill the UI
        raise BotCountReadFailed(generation, str(exc)) from exc


@dataclass(frozen=True)
class TimeZoneAnswer:
    """One read of the time zone, and which lookup asked (an older answer is dropped)."""

    generation: int
    reading: server_time_zone.Reading


def _read_time_zone(route: server_time_zone.TimeZoneRoute, generation: int) -> TimeZoneAnswer:
    """The Tuning tab's time zone read, as a job: it opens the compose override (T171).

    Tagged on failure for `_read_bot_count`'s reason.
    """
    try:
        return TimeZoneAnswer(generation, route.read())
    except Exception as exc:  # boundary: an unreadable server folder must not kill the UI
        raise BotCountReadFailed(generation, str(exc)) from exc


TUNING_CORE_FILES: tuple[str, ...] = reset_defaults.AZEROTHCORE_CORE_FILES
"""The install's own conf files, listed read-only beside the module ones.

Named (in `reset_defaults`, since T94) and not discovered by a glob of
`env/dist/etc`: a glob would also list every module conf a second time, and
the point of the list is that these three are the ones this tab deliberately
will not write.
"""

REBUILD_BOTS_LABEL = "Rebuild random bots…"
REBUILD_BOTS_TITLE = "Rebuild the random bots?"
REBUILD_BOTS_TEXT = (
    "Every random bot is deleted and made again from scratch, so the bots lose their level, "
    "gear, bags, bank, quests and professions. Also lost: companions you hired from the bots, "
    "pinned bot names, guilds made only of bots, and the bots' auctions (anyone who bid is "
    "refunded).\n\n"
    "What stays: the bot accounts. Your own characters are never touched.\n\n"
    "The server restarts (or starts, if it is stopped), and anyone playing is disconnected. "
    "The bots module refuses, and "
    "deletes nothing, if someone is playing one of the bots or a guild led by a bot has a real "
    "player in it."
)
"""T144's one dialog. Its three buttons are `ask_backup_choice()`'s, relabelled."""

REBUILD_BOTS_AFTER_UPDATE = (
    "TortoiseBots changed. Rebuild the random bots so they get the new behaviour? They lose "
    "level and gear; their accounts stay."
)
"""T144's offer after an update or a return to the pin that moved TortoiseBots. No by default."""

BOT_REBUILD_RUNNING = (
    "The random bots are being rebuilt on the Bots tab, and that restarts the server. Wait for "
    "it to finish, then try again. Nothing was changed."
)
"""T144: why Back up and Restore refuse while the rebuild runs."""

RESTART_OWED_LEFT = (
    "The bots enrolled during the update come online at the server's next start. Restart the "
    "server from the Server tab when nothing else is running."
)
"""T144: the restart T123's enrolment is owed, when this tab could not make it itself."""

BOT_COUNT_LABEL = "Random bots:"
BOT_COUNT_APPLY = "Apply…"
BOT_COUNT_TITLE = "Random bots"
BOT_COUNT_RUNNING = (
    "Yu'lon is writing this server's bot count. It takes a moment; this window closes "
    "normally once it is done."
)
"""The close guard's sentence while the Bots tab's write runs (`busy_reason()`)."""

TIME_ZONE_TITLE = "Server time zone"
TIME_ZONE_APPLY = "Apply…"
TIME_ZONE_HOST = "Same as this computer ({zone})"
TIME_ZONE_KEPT = "As written: {value}"
TIME_ZONE_RUNNING = (
    "Yu'lon is writing this server's time zone. It takes a moment; this window closes "
    "normally once it is done."
)
"""T171. The close guard's sentence while the Tuning tab's time zone write runs."""

TUNING_RESET_LABEL = "Reset to default"
TUNING_RESET_ALL = "All server settings…"
TUNING_RESET_UNDO = "Undo the last reset…"
"""T94's menu. The ellipses are this app's "a dialog opens first" convention."""

TUNING_RESET_TIP = (
    "Put this server's own settings files back to how Yu'lon installed it. Module files are "
    "kept, and a backup of each file is made first."
)
TUNING_RESET_RUNNING = (
    "Yu'lon is putting this server's settings files back. It takes a few seconds; this window "
    "closes normally once it is done."
)
"""The close guard's sentence while a reset or its undo runs (`busy_reason()`)."""

TUNING_RESET_UNREADABLE = "FAILED: the settings files could not be read ({why})"
TUNING_RESET_NOTHING_TO_UNDO = (
    "Nothing to undo: every file is already as the last reset found it, or was put back since."
)
TUNING_RESET_UNDO_CONFIRM = (
    "Put back the files the last reset replaced?\n\n{files}\n\nEach one is copied back from "
    "the backup named beside it. Anything you changed in them since the reset is replaced, and "
    "kept first: each file as it is now is backed up beside it (an .undo-….bak)."
)

MODULE_SQL_BUTTON_LABEL = "Apply module SQL"
"""The Modules tab's import button, named once.

For `REBUILD_BUTTON_LABEL`'s reason and no other: `_pending_sql_lines()` tells
the user to press this by name, and two literals are one rename away from a
report pointing at a control that is not there any more.
"""

MODULE_SQL_TIP = (
    "Applies the SQL that installing a module leaves for the importer. The server must be "
    "stopped: press Stop on the Server tab first."
)
"""The button's tooltip on a game that has an importer.

It names the refusal the user is most likely to meet — `docker.apply_module_sql()`
will not write module SQL underneath a running worldserver (checklist 8.7a) —
before the click rather than after it. It is a courtesy, not the guard.
"""

MODULE_UPDATES_BUTTON_LABEL = "Check for updates"
"""The Modules tab's read-only button (checklist 8.7a's first clause)."""

MODULE_UPDATES_TIP = (
    "Asks each installed module's upstream how many commits it is behind. Fetches, and changes "
    "nothing on the server."
)
"""Named before the press, because the word "check" hides a network round trip
per installed module and a user who is offline should know which half failed."""

MODULE_UPDATES_NO_MODULES = (
    "This game has no modules folder, so there is nothing installed here to compare."
)
"""Why the button is dead on the three CMaNGOS games — `MODULE_SQL_NO_IMPORTER`'s reason."""

MODULE_UPDATES_RUNNING = "Asking each installed module's upstream how far behind it is…"

MODULE_UPDATES_NONE = (
    "No modules are installed in this server's modules folder, so there is nothing to compare."
)
"""An empty answer said out loud. A blank box reads identically to a failed
read, which is how the app once reported a step nobody ran as done."""

MODULE_SQL_NO_IMPORTER = (
    "This game has no one-shot import service, so there is nothing to run its modules' SQL with."
)
"""Why the button is dead on the three CMaNGOS games.

A disabled button with no reason on it reads as a broken app. This is the same
sentence `docker.apply_module_sql()` refuses with, said before the press
instead of after it.
"""

MODULE_LINK_BUTTON_LABEL = "Install from link…"
"""The Modules tab's fifth button: a module this app does not ship, from a link.

The ellipsis is this tab's convention for "this opens a dialog first" — the
same difference `REBUILD_BUTTON_LABEL` carries and the two buttons beside it do
not. Prior art: the rust launcher spelled the same control as a card headed
"Install from URL" with a text field and its own button
(`origin/rust-main:launcher/src/lib/pages/ModuleManager.svelte:1473-1491`).
"""

MODULE_FOLDER_BUTTON_LABEL = "Install from folder…"
"""The sixth button: the same module from a folder already on this computer.

No prior art at all — `origin/rust-main` had a URL route and nothing else,
grepped 2026-09-08.
"""

CUSTOM_MODULE_CARD_TITLE = "A module this app does not ship"
"""The custom-module card's title, and the first words of its one-line form (T153)."""

CUSTOM_MODULE_CARD_NOTE = (
    "Paste a repository link, or point at a folder on this computer. Yu'lon derives "
    "a manifest from it and installs it the same way as any row above."
)
"""The card's sentence: drawn in the card whole, and the line's tooltip (T153)."""


MODULE_LINK_TIP = (
    "Paste an https link to a module repository on github.com, gitlab.com or codeberg.org. "
    "Its name must start with mod-. The module is cloned into this server's modules folder; "
    "it does nothing until the server is rebuilt."
)
"""Named before the press, the way `MODULE_SQL_TIP` is.

Three refusals a user meets before anything happens — the host, the `mod-`
name, and the fact that a clone is inert until a rebuild — said where they cost
nothing rather than after a dialog has been filled in. The rust page's version
of this was the four-word hint `mod-* repos only`
(`ModuleManager.svelte:1491`).
"""

MODULE_FOLDER_TIP = (
    "Choose a folder on this computer holding a module (its name must start with mod-). "
    "It is copied into this server's modules folder; the original is not touched, and it "
    "does nothing until the server is rebuilt."
)
"""As `MODULE_LINK_TIP`, plus the one thing a copy has to promise: the folder
the user points at is read, never moved and never written into."""

MODULE_CUSTOM_NO_ROUTE = (
    "Only WoW WotLK takes custom modules — on this game a module is a configuration key or a "
    "SQL mod, and those ship as manifests."
)
"""Why the two buttons are dead on the three CMaNGOS games.

Measured per tree, not inherited: 8.7b and 8.7c gated that on those cores a
module is a conf activation or a SQL mod and never a directory, so there is no
`modules/` folder for a clone or a copy to land in.
"""

MODULE_LINK_DIALOG_TITLE = "Install a module from a link"

MODULE_LINK_DIALOG_PROMPT = "Link to the module's repository:"

MODULE_LINK_DIALOG_PLACEHOLDER = "https://github.com/you/mod-my-thing"
"""The example the rust CLI printed in its own refusal
(`origin/rust-main:crates/dml-wow/src/modmgr.rs:1777`)."""

MODULE_FOLDER_DIALOG_TITLE = "Choose the module's folder"

MODULE_LINK_CANCELLED = "install from link: cancelled — nothing on this machine was changed."
"""The tab's own cancel sentence, in the shape `_module_action()` already uses.

"from link" rather than an id because there is no id yet: the dialog was closed
before anything was derived, which is exactly what the sentence has to convey.
"""

MODULE_FOLDER_CANCELLED = "install from folder: cancelled — nothing on this machine was changed."

MODULE_REPLACE_TITLE = "Replace the checkout of {id}?"
"""The title over `Applier.replacement_question()`'s sentence (T47).

The title asks and the body explains, which is how every other Yes/No on this
tab reads — and the id is in it because a user who reaches this has a folder
under `modules/` whose name is the only thing the two repositories share."""

MODULE_UPDATE_UNCHECKED_TITLE = "Update {id} without checking?"
"""The title over `ReleaseDirectionUnknown.question` (T150): the release could not be placed.

Asked only after a press of Update came back saying neither the clone nor
GitHub could show the release is newer than the folder -- and it defaults to
No, because a yes is the one way left to move a module back without meaning to.
"""

_IMPORT_LINE_CHARS = 110
"""How much of the import's output the label carries: the last two lines, trimmed.

Two, because one line looks static whenever a step is slow while two show which
way it is moving. Trimmed, because this label sits above the rest of the tab and
a single 500-character line of SQL would wrap into five rows and move
everything under it.
"""


def size_text(size: int) -> str:
    """Bytes as the dialog says them: decimal units, one decimal place.

    Decimal rather than binary because that is what a user's file manager and
    their disk's label both say, and a dialog asking permission to delete
    2.3 GB should not be the one place the number reads 2.1.
    """
    for unit, step in (("TB", 10**12), ("GB", 10**9), ("MB", 10**6), ("kB", 10**3)):
        if size >= step:
            return f"{size / step:.1f} {unit}"
    return f"{size} bytes"


_POST_INSTALL_RESETTLE_MS = 60_000
"""How long after an install's first `settle()` the tab asks once more if still `Pending`.

The same wait follows a tab that opens on a `Pending` channel an earlier run
left un-proved (T138): its first ask can land in the same slow half minute.

Measured on the T86 gate: a SOAP request 8 s after `World server is up` hit the
20 s timeout while the world logged in its bots; 40 s after it answered at once.
"""


class ControllerView(QWidget):
    """Per-install tabs; see module docstring."""

    status_changed = Signal(object)  # InstallStatus
    action_failed = Signal(str)  # user-readable message
    uninstalled = Signal(str, object)  # game id, server_dir (Path) -- 8.9a
    """This install is gone. The window drops the tab and the Catalog tile resets.

    A third signal because neither existing one can mean it: `action_failed`
    carries a message and `status_changed` carries an `InstallStatus`, and
    "there is no longer anything here to have a status" is neither. Both
    payloads are needed because tabs are keyed by (game, server dir).

    Emitted only after `purge.run()` returned. A tab dropped after a FAILED
    removal would take away the only surface that could try again.
    """

    remove_requested = Signal(str, object)  # game id, server_dir (T95)
    """"Remove from Yu'lon…" was pressed on this tab. The window asks, stops and forgets.

    A signal and not a dialog of this view's own (T34 had one). The window holds
    the one live `AppState`, the tab strip and the Catalog tile, and the × on the
    sidebar tab and its right-click entry reach the same path without passing
    through this view. One path means one dialog, one refusal rule and one forget.
    """

    stopped_for_removal = Signal(str, object, bool, str)  # game id, server_dir, ok, why (T95)
    """The stop that `stop_for_removal()` ran has ended; `why` is docker's reason when not `ok`.

    Emitted LAST from its slot, because the window may drop this tab on it.
    """

    client_dir_changed = Signal(str, object, object)  # game id, server_dir, client_dir (T36)
    """This install's client folder was set, changed or cleared -- rebuild the tab.

    Not patched in place: the client dir is baked into a dozen seams at
    construction (`_client_dir_for_addons()` behind the module applier, the
    Steam entry), and threading a mutable value through each is the wrong
    shape -- `add_controller()`'s own docstring makes the same call for a
    changed WSL distro. `main.py` answers this the way it answers `uninstalled`:
    drop the tab and build a fresh one, which is what makes the new folder
    reach every seam at once rather than some of them.

    `server_dir` rides along because the tab is keyed on `(game, server_dir)`
    and `main.py` holds no other way back to this closure's key.
    """

    play_client_dir_changed = Signal(str, object, object)  # game id, server_dir, play dir (T181)
    """This install's ready-to-play client was made, recorded or deleted -- rebuild the tab.

    `client_dir_changed`'s twin for the same reason: the folder is baked into
    the applier and the Steam entry at construction (`for_entry()`), so only a
    rebuilt tab writes modules into the new folder (or back into the original).
    """

    play_state_changed = Signal()
    """Play's line, its Cancel, or a lock on the ready-to-play client changed (T187).

    The client launcher window shows the same line and Cancel as the Server tab
    and greys its settings while `play_client_busy()` says so; it reads both off
    this view when this fires rather than keeping a second copy of either.
    """

    launcher_saved = Signal(str)
    """A `save_launcher_picks()` write ended: `""` when saved, else what went wrong (T187)."""

    def __init__(
        self,
        entry: CatalogEntry,
        services: ControllerServices,
        *,
        status_poll_ms: int = 5000,
        job_runner: JobRunner | None = None,
        prompt_asker: PromptAsker | None = None,
        link_asker: LinkAsker | None = None,
        folder_asker: FolderAsker | None = None,
        pick_client_dir: DirPicker = _qt_dir_picker,
        play_client_asker: PlayClientAsker | None = None,
        client_options_asker: ClientOptionsAsker | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.entry = entry
        self.services = services
        # T187: the client launcher window driving this tab, while its press is
        # the one being answered: the play-side dialogs open over it rather
        # than over the main window (`_play_parent`). Set by the launcher before
        # each of its presses, cleared by this tab's own.
        self.dialog_host: QWidget | None = None
        # T187: opens this server's launcher window; `main.py` sets it. While it
        # is None (a tab outside the app's window) Play plays directly, as before.
        self.open_launcher: Callable[[], None] | None = None
        # T181's creation dialog, a seam for `prompt_asker`'s reason. The real
        # one is the dialog, with this tab's own folder picker behind "Change…".
        self._play_client_asker: PlayClientAsker = play_client_asker or (
            lambda parent, offer: ask_play_client(parent, offer, self._pick_client_dir, self._jobs)
        )
        # T181 b/c: "Client options…" for the same reason.
        self._client_options_asker: ClientOptionsAsker = client_options_asker or ask_client_options
        # A Make…, Refresh or Delete writing the ready-to-play client: it holds
        # the close (`busy_reason()`) the way the import does.
        self._play_client_running = False
        # Play's two waits: a Start it asked for, and a Refresh it asked for.
        self._play_after_start = False
        self._play_after_refresh = False
        # Whether the uninstall plan on screen offered to delete the
        # ready-to-play client (its marker was this server's when it was read).
        self._play_delete_offered = False
        # T133: what WSL last said about this server's distro. Every tab reads
        # the install's files as it is built, and reading
        # `\\wsl.localhost\<distro>` starts a stopped distro, so a WSL install
        # starts NOT ASKED (None) and reads nothing until an answer says
        # `running`: asked off the GUI thread once the tabs are built, and again
        # by every status poll (`InstallStatus.distro`). A server on this host
        # is `running` from the start and never waits. What waited runs on the
        # first `running` (`_distro_answered()`), keyed by what is waiting, in
        # order, so a reading asked twice while it waits runs once.
        self._distro: wsl.DistroState | None = (
            "running" if services.controller.wsl_distro is None else None
        )
        self._waiting_on_distro: dict[str, Callable[[], None]] = {}
        # How the Modules tab asks a manifest's own questions. A seam, so a test
        # can answer them without a modal dialog; the real one is the dialog.
        self._prompt_asker: PromptAsker = prompt_asker or ask_manifest_prompts
        # The same shape for the two custom-module dialogs, for the same reason.
        self._link_asker: LinkAsker = link_asker or ask_module_link
        self._folder_asker: FolderAsker = folder_asker or ask_module_folder
        # T36's client-folder press. The same `DirPicker` shape the Catalog's
        # own folder pickers use (`catalog_view._qt_dir_picker`), reused rather
        # than a second modal dialog function that would open the same window.
        self._pick_client_dir: DirPicker = pick_client_dir
        # Every service call goes through this: on a worker thread in the app,
        # inline in tests (review finding, 2026-08-21 — the window used to
        # freeze for the length of a `docker compose up`).
        self._jobs: JobRunner = job_runner or threaded_job_runner(self)
        self._busy = False
        # T188 C4/C5: the word the realm badge holds while a Start, Stop or
        # Restart of ours runs. A poll mid-stop used to flip the badge between
        # OFFLINE and "starting".
        self._badge_held: str | None = None
        # The first status read that may end that hold: the one the holding job
        # asks for as it ends, or any asked after it. None while the job runs.
        # A number and not `_busy` at answer time: a Rebuild pressed before the
        # stop's own read answered made it "stale", and STOPPING stayed on the
        # badge for the whole rebuild (T188 final review).
        self._hold_ends_at: int | None = None
        # How many status reads were asked, and which one is out.
        self._status_asks = 0
        self._status_ask_out = 0
        self._status_pending = False
        self._verdict_pending = False
        # T124's count: one ask in flight at a time, for `_status_pending`'s reason.
        self._upstream_pending = False
        self._module_pending: str | None = None
        self._console_pending = False
        self._tabs = QTabWidget(self)
        self._tabs.setIconSize(QSize(16, 16))
        self._tabs.setUsesScrollButtons(True)
        self._tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self._tabs.setDocumentMode(True)
        self._tabs.tabBar().setExpanding(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._tabs)

        self._restore_plan: wotlk_maintenance.RestorePlan | None = None
        self._remove_armed = False
        # T95. The last status poll's answer, or None while no poll has answered
        # (never polled, or docker unreachable). The window reads it to decide
        # whether a removal stops the server first.
        self._last_status: InstallStatus | None = None
        # T95. Set by a "Stop and remove containers…" press that found nothing to
        # remove. Cleared by the next Start, by a removal that did find some, and
        # by a fresh poll that sees the server running (a Rebuild, an Update, a
        # Return-to-pin or an outside start brings it up without Start). While it
        # holds, "Remove from Yu'lon…" is highlighted: it is the way out of the
        # dead end in Andood's video.
        self._nothing_to_remove = False
        # T145 round 7: a Tuning backup is being put back (Revert, raw Revert,
        # Undo the last reset); `run_restore()` refuses while it is, on Tortoise.
        self._put_back_running = False
        # Whether the poll in flight was asked while a Server action ran. Its
        # answer may predate what that action did (T95 review, round 1).
        self._status_asked_busy = False
        # Whether a refresh was asked, and dropped, while that poll was in
        # flight. The dropped one may be an action's own end-of-action refresh,
        # so the answer still coming predates it (T95 review, round 2).
        self._status_superseded = False
        # T95. Jobs that run through `_run()` without `_busy`, so neither
        # `busy_reason()` nor `_busy` sees them, and a removal must.
        self._backup_running = False
        self._restore_running = False
        self._network_applying = False
        self._import_running = False
        self._uninstall_running = False
        self._uninstall_plan: purge.PurgePlan | None = None
        """The plan currently on screen, and the only thing that authorises a run.

        Cleared by a failure, because the machine has changed under the
        photograph: a second press then has to ask for a fresh one.
        """
        # The Modules tab's run of the SAME one-shot service. A second flag
        # rather than a second meaning for `_import_running`, which also
        # decides whether the repair offer is hidden and whether Refresh is
        # locked -- overloading it would change the Server tab from here.
        self._module_sql_running = False
        # T42's three session facts, and the word "session" is the whole of
        # their contract: nothing on disk records any of them, and a restart
        # starts empty. The report box has always forgotten them too -- what
        # changed is only that they are now shown on the rows that own them.
        #
        # `_rebuild_owed`: modules whose install said `rebuild_required`,
        # cleared by a rebuild that SUCCEEDS. `_sql_owed`: the files a report
        # left to the importer, cleared when the importer finishes. `_behind`:
        # what the last update check counted, zeroes and "could not ask" left
        # out.
        #
        # All three keyed by `(FAMILY, id)` since round 2, for `SessionState`'s
        # reason: nothing makes an id unique across families, and these were
        # the last three surfaces T42 round 2 left on a bare id.
        self._rebuild_owed: set[tuple[str, str]] = set()
        # Whether the run in `rebuild_log` is a COMPILE. Three actions share
        # that panel -- the rebuild, the database updates and the adopt -- and
        # all three arrive at `_rebuild_finished`, so "the panel finished and
        # said ok" is not "the server was compiled". Clearing `_rebuild_owed` on
        # the panel's success alone would tell a user their module was live
        # because an unrelated SQL run went through.
        self._rebuild_is_compile = False
        # T146: whether that run is one of the two T64 presses, which MOVE the
        # server's checkouts as well as compiling them. A plain rebuild compiles
        # the same folders without moving them, so its finish must not drop a
        # count of how far behind they are.
        self._rebuild_moves_sources = False
        # T179 Task 6 fix round 3: an update press ended with its new build kept
        # (`WorldStoppedAfterReadyError.sources_kept`), so its sources stayed moved.
        self._update_sources_kept = False
        # T64: a `mysqldump` is running off the GUI thread, chained in front of
        # an update. `_busy` is the LOG PANEL's flag and a backup is not a job in
        # that panel, so without this nothing on the tab knows -- see
        # `_update_route_busy()` for what a second press did.
        self._backup_before_update = False
        self._sql_owed: dict[tuple[str, str], tuple[str, ...]] = {}
        self._behind: dict[tuple[str, str], int | Behind] = {}
        # T126: the newest release's tag for a counted row that follows its
        # releases. Read only for a key `_behind` still has.
        self._behind_release: dict[tuple[str, str], str] = {}
        self._behind_updated: set[tuple[str, str]] = set()
        self._repair_armed = False
        # The last answer the database gave about its own import, and whether it
        # has been asked since the database came up. Remembered because the
        # question can only be put while the database is running, and the state
        # this action exists for is one the user reaches by pressing Stop.
        self._import_state: docker.ImportState | None = None
        self._import_asked = False
        # And what the ADOPT route's own gate says, which for three of the four
        # games is a different question from the one above: `Controller.import_state()`
        # answers `unreadable` for every CMaNGOS install (`controller_for()` hands
        # it no probe on purpose, because the Repair button's only action can
        # refuse there), while the adopt button's rule needs `populated`. Asked
        # at the same moment and remembered the same way -- once per time the
        # database comes up, never on the five-second poll and never on a paint.
        self._adopt_state: docker.ImportState | None = None
        # The import talks from a worker thread; this is how what it says gets
        # onto the GUI thread. See `LineRelay` — handing `_import_line` itself
        # down as the sink would call it on the worker thread instead.
        self._import_relay = LineRelay(self)
        self._import_relay.line.connect(self._import_line)
        # T158: a stop that has to wait for a world to finish loading says so
        # while it waits, from the stop's worker thread; the relays put it on
        # this one. Every stop the controller runs speaks through the first --
        # Stop, Remove containers, the restart and recreate jobs, the Bots
        # tab's restarts, the stop before a removal and the stop of the server
        # holding the ports -- and the uninstall through the second, into its
        # own label. Both share the two events: "Stop now anyway" (the button
        # below) and the give-up `shutdown()` sets so closing never hangs on a
        # load and never signals one.
        self._stop_anyway = threading.Event()
        self._stop_abandon = threading.Event()
        self._stop_relay = LineRelay(self)
        self._stop_relay.line.connect(self._stop_notice)
        self.services.controller.stop_control = docker.StopControl(
            say=self._stop_relay.emit_line, anyway=self._stop_anyway, abandon=self._stop_abandon
        )
        self._uninstall_stop_relay = LineRelay(self)
        self._uninstall_stop_relay.line.connect(self._uninstall_stop_notice)
        if self.services.uninstall is not None:
            self.services.uninstall.stop_control = docker.StopControl(
                say=self._uninstall_stop_relay.emit_line,
                anyway=self._stop_anyway,
                abandon=self._stop_abandon,
            )
        # The one thing such a stop says that must outlive it: that the world
        # may have been force-stopped. And whether the problem label is showing
        # a stop's words at all, so the end of ANY job can take them down.
        self._stop_forced = ""
        self._stop_words_shown = False
        self._import_tail: deque[str] = deque(maxlen=_IMPORT_TAIL_LINES)
        # T127's log panel, built with the Bots tab only where the game has a dashboard.
        self.dashboard_log: LogPanel | None = None
        # T106: what the last compose check said, the backup the last repair
        # made, and whether a check is out. Before the tabs, because the Tuning
        # tab's owed-set refresh redraws the Server tab's compose banner too.
        self._compose_state: str | None = None
        self._compose_check: native.ComposeCheck | None = None
        self._compose_backup: Path | None = None
        # T170: whether that backup is the repository's own file, for the banner,
        # and whether a check is owed once the one out answers.
        self._compose_backup_upstream = False
        self._compose_pending = False
        self._compose_again = False
        # T137: the module confs the last check found missing, the ones the
        # last repair wrote (until the restart that loads them), and whether a
        # check is out.
        self._confs_missing: tuple[str, ...] = ()
        self._confs_written: tuple[str, ...] = ()
        self._confs_pending = False
        # T174: what the last folder-lock check said, and whether one is out.
        self._lock_state: serverlock.LockState | None = None
        self._lock_check: serverlock.FolderLockCheck | None = None
        self._lock_pending = False
        # T129: what the last corrections check said. Taken once each time the
        # database comes up (`_ask_about_the_import`), dropped when it goes.
        self._corrections: native.CorrectionCheck | None = None
        # T171: built before any tab, like T99's box, so `_set_busy()` can
        # always reach it; the Tuning tab is what shows it.
        self._build_time_zone_group()
        self._build_server_tab()
        self._build_console_tab()
        self._build_accounts_tab()
        self._build_characters_tab()
        self._build_bots_tab()
        self._build_maintenance_tab()
        self._build_modules_tab()
        self._build_tuning_tab()
        self._build_networking_tab()

        # T106: asked once now, whether or not this tab polls -- it reads files
        # and asks no daemon.
        self.check_server_files()

        # What the channel says needs no daemon, no database and no network:
        # it is read from the credential file (or the pending record, T138), so
        # it is shown whether or not this tab polls. Asking the SERVER about it
        # is the part that is gated on polling, just below.
        self.refresh_channel()

        # T133: now that every tab has queued what it would read, ask whether
        # the distro runs -- off the GUI thread, polling or not.
        self._ask_the_distro()

        self._timer = QTimer(self)
        # T95: through `_tick`, so a tick never marks the poll in flight stale.
        self._timer.timeout.connect(self._tick)
        self._timer.timeout.connect(self.refresh_verdict)
        self._timer.timeout.connect(self.refresh_pathfinding)
        self._timer.timeout.connect(self.refresh_world_upkeep)
        if status_poll_ms > 0:
            self._timer.start(status_poll_ms)
            self.refresh_pathfinding()
            self.refresh_world_upkeep()
            # And once now. `QTimer.start()` fires nothing until the interval
            # has passed, so a tab opened over a running server spent its first
            # five seconds saying "status: unknown" with Start enabled
            # (`pyplan/bug-checklist.md:552`). A tab told not to poll is not
            # polled at all, here included.
            self.refresh_status()
            self.refresh_verdict()
            # T127: what the files say about the bot dashboard, once. It changes
            # only through the switch, whose job re-reads it when it ends.
            self.refresh_bot_dashboard()
            # And ask the channel once, for the same reason: a credential the
            # server has stopped accepting reads as verified straight off the
            # disk, and until something asks, the repair is never offered.
            #
            # A channel found `Pending` is a row an earlier run created and
            # closed before proving (T138). The server was usually left up, so
            # no Start is coming to settle it, and this check may itself land
            # inside the ~40 s the world takes to answer SOAP -- so it gets the
            # one later ask an install gets. Read before the check, which may
            # run inline and move the state on.
            setup = self.services.channel_setup
            found_pending = setup is not None and isinstance(
                setup.setup_state(), channel_setup.Pending
            )
            self._check_the_channel()
            if found_pending:
                QTimer.singleShot(_POST_INSTALL_RESETTLE_MS, self, self._resettle_if_pending)

    # ------------------------------------------------------------- sub-tabs

    def _add_panel_tab(self, tab: QWidget, icon_name: str, title: str) -> None:
        """Add a sub-tab carrying both its icon and readable title label."""
        index = self._tabs.addTab(tab, get_tab_icon(icon_name), title)
        self._tabs.setTabToolTip(index, title)

    # ------------------------------------------------------------ server tab

    def _build_server_tab(self) -> None:
        tab = QWidget(self)
        box = QVBoxLayout(tab)
        # One line above the three up/down words, and only when this game's
        # dashboard is wired: what it says is `dashboard.line()`, which is
        # tested without a widget because a phrase reachable only through a GUI
        # test is a phrase nobody reads twice.
        self.verdict_label = QLabel("", tab)
        self.verdict_label.setWordWrap(True)
        self.verdict_label.setVisible(False)
        # 8.2a. Both are hidden for a game whose channel is not wired: 8.2b,
        # 8.2c and 8.2d add their own, and a control that cannot work is worse
        # than no control.
        self.channel_label = QLabel("", tab)
        self.channel_label.setWordWrap(True)
        # Shown for a tree with a channel to set up AND for one whose channel is
        # the console: the second has nothing to press but everything to explain
        # (8.2e).
        self.channel_label.setVisible(
            self.services.channel_setup is not None or _is_console_channel(self.entry)
        )
        if _is_console_channel(self.entry):
            self.channel_label.setText(CONSOLE_CHANNEL_SENTENCE)
        self.test_console_button = QPushButton("Test the console", tab)
        self.test_console_button.setVisible(self.services.console_probe is not None)
        self.test_console_button.clicked.connect(self.test_console)
        self.console_probe_label = QLabel("", tab)
        self.console_probe_label.setWordWrap(True)
        self.console_probe_label.setVisible(False)
        self.enable_channel_button = QPushButton("Turn on the command channel", tab)
        self.enable_channel_button.setVisible(self.services.channel_setup is not None)
        # T188 A4: set by a press that wrote the channel on. The setup state
        # stays Idle until the next Start proves it, so the state alone would
        # keep offering a press that has already been made.
        self._channel_written = False
        self.enable_channel_button.clicked.connect(self.enable_channel)
        # Hidden until the server has actually refused the saved credential.
        # This is the one control on the tab that can break a channel that
        # works -- it resets the account's password -- so it exists only where
        # there is nothing left to break.
        self.repair_channel_button = QPushButton("Repair the command channel", tab)
        self.repair_channel_button.setVisible(False)
        self.repair_channel_button.clicked.connect(self.repair_channel)
        self.status_label = QLabel("status: unknown", tab)
        # T133: said while this server's WSL distro is stopped and every tab's
        # readings wait for it (`_waits_for_the_distro()`).
        self.distro_label = QLabel("", tab)
        self.distro_label.setWordWrap(True)
        self._show_the_distro()
        # T124: what upstream has that this server was not built from. Hidden
        # until a reading says there is something -- and for no network, no
        # route, or nothing new, it stays hidden rather than saying so.
        self.upstream_label = QLabel("", tab)
        self.upstream_label.setWordWrap(True)
        self.upstream_label.setVisible(False)
        # T179: how far the background movement-map job has got, read off the
        # GUI thread on the poll, with Start where a run could begin and Stop
        # where one is running. Hidden for a server without the job.
        self._pathfinding_status: mmaps.MmapsStatus | None = None
        self._pathfinding_pending = False
        self._pathfinding_pressing = False
        # Bumped by every read and every press: a read whose generation is not
        # the newest lands after the state it describes has changed, and is dropped.
        self._pathfinding_generation = 0
        self.pathfinding_label = QLabel("", tab)
        self.pathfinding_label.setWordWrap(True)
        self.pathfinding_start_button = QPushButton(PATHFINDING_START, tab)
        self.pathfinding_start_button.clicked.connect(self.start_pathfinding)
        self.pathfinding_stop_button = QPushButton(PATHFINDING_STOP, tab)
        self.pathfinding_stop_button.clicked.connect(self.stop_pathfinding)
        self._show_pathfinding()
        # T179 Task 6: what an update left owing -- map data to extract again, world
        # tables still to import -- read off the GUI thread (a file each), with the
        # press that pays each. Hidden for a server whose update leaves neither.
        self._upkeep: tuple[str | None, str | None] = (None, None)
        self._upkeep_pending = False
        self.world_upkeep_label = QLabel("", tab)
        self.world_upkeep_label.setWordWrap(True)
        self.reextract_button = QPushButton(trinitycore.REEXTRACT_BUTTON, tab)
        self.reextract_button.clicked.connect(self.reextract_map_data)
        self.finish_world_button = QPushButton(trinitycore.FINISH_WORLD_BUTTON, tab)
        self.finish_world_button.clicked.connect(self.finish_world_update)
        self._show_world_upkeep()
        # T36. Visible for every game, including WotLK -- AzerothCore reads no
        # client itself, but the folder is still a host path a manifest's
        # `client` step or the Steam entry can use, and hiding the row there
        # would be the same dead end this ticket exists to close for WotLK's
        # `client_dir: null` records. Hidden only when `main.py` hands down no
        # write seam at all (`set_client_dir is None`), the same rule
        # `uninstall`'s controls follow. Never hidden for a WSL-distro install:
        # `wsl_distro` names where the SERVER lives, and a client folder is
        # always a path on this host, not inside that distro.
        self.client_dir_label = QLabel("", tab)
        self.client_dir_label.setWordWrap(True)
        self.client_dir_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.client_dir_label.setVisible(False)
        self.set_client_dir_button: QPushButton | None = None
        self.forget_client_dir_button: QPushButton | None = None
        if self.services.set_client_dir is not None:
            # The label, not a re-check of `self.services.client_dir` later:
            # both buttons' presence and this label's text answer the same
            # fact and must not be able to disagree the way two separate reads
            # of a value that cannot change without a rebuild never could.
            has_client = self.services.client_dir is not None
            change_label = "Change client folder…" if has_client else SET_CLIENT_DIR_LABEL
            self.set_client_dir_button = QPushButton(change_label, tab)
            self.set_client_dir_button.clicked.connect(self.change_client_dir)
            self.forget_client_dir_button = QPushButton("Forget client folder", tab)
            self.forget_client_dir_button.setVisible(has_client)
            self.forget_client_dir_button.clicked.connect(self.forget_client_dir)
            self.client_dir_label.setVisible(True)
            if not has_client and self.services.play_client_dir is None:
                # T181: the ready-to-play client is made from this folder, so
                # its button is hidden until there is one; this says where it went.
                self.set_client_dir_button.setToolTip(
                    "Set your own client folder first: Yu'lon makes this server's "
                    "ready-to-play client from it, and then offers Play."
                )
        self.client_dir_label.setText(_client_dir_row_text(self.services.client_dir))
        # Why a whole label and not a dialog: the stop path's refusals are
        # paragraphs naming containers, projects and the file to edit, and they
        # arrive from a worker thread. It was called `conflict_label` while only
        # `_start_failed` wrote to it; a stop that refused wrote nowhere at all,
        # so a refusal was indistinguishable from the silent bug the refusal
        # exists to prevent (review, 2026-08-22).
        self.problem_label = QLabel("", tab)
        self.problem_label.setWordWrap(True)
        self.problem_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse  # so the remedy can be copied
        )
        self.start_button = QPushButton("Start", tab)
        self.start_button.setIcon(dadcraft_icon("play", COLOR_GOLD_LIGHT, 14))
        self.start_button.setProperty("primary", True)
        self.stop_button = QPushButton("Stop", tab)
        self.stop_button.setIcon(dadcraft_icon("stop", "#FFB8B8", 14))
        self.stop_button.setProperty("danger", True)
        self.refresh_button = QPushButton("Refresh", tab)
        self.refresh_button.setIcon(dadcraft_icon("refresh", COLOR_TEXT_GOLD, 14))
        # Deliberate, per checklist 6.5: nothing removes a container today, and
        # whatever does must not be a stray click next to Stop. It arms on the
        # first press and acts on the second, and anything else disarms it.
        self.remove_button = QPushButton(REMOVE_IDLE, tab)
        self.remove_button.setIcon(dadcraft_icon("trash", "#FFB8B8", 14))
        self.remove_button.setProperty("danger", True)
        # Hidden unless the database has said there is an unfinished import to
        # finish. A destructive action that is always on screen is one that gets
        # pressed by accident, and this one is only ever right for a broken
        # install — the installer imports on every healthy path.
        self.repair_button = QPushButton(REPAIR_IDLE, tab)
        self.repair_button.setVisible(False)
        # Hidden until a Start is actually refused for the ports. Every v1
        # server publishes the same ones, so only one can be live at a time -
        # and refusing while leaving the user to go and find the other install
        # themselves is correct and unhelpful. This is the offer to do it.
        self.stop_other_button = QPushButton("Stop the other server and start this one", tab)
        self.stop_other_button.setProperty("primary", True)
        self.stop_other_button.setVisible(False)
        # T160. Hidden unless this is a Steam Deck whose `docker` command is
        # gone, which is what a SteamOS update leaves behind. The press runs the
        # upstream fix script's repair through the app's own questions; the
        # prompter is kept on the view because PySide6 holds its slot weakly.
        self.reinstall_docker_button = QPushButton(platform.STEAMOS_DOCKER_REPAIR_LABEL, tab)
        self.reinstall_docker_button.setProperty("primary", True)
        self.reinstall_docker_button.setVisible(False)
        self._docker_prompter: InputPrompter | None = None
        # The running repair's cancel; None when this tab is not running one.
        self._docker_repair_cancel: threading.Event | None = None
        # T158. Shown only while a stop waits for a world that cannot hear it
        # yet, and the one way to end that wait early: there is no time limit,
        # because a limit would force-stop a slow but healthy first boot.
        self.stop_anyway_button = QPushButton(STOP_ANYWAY_LABEL, tab)
        self.stop_anyway_button.setProperty("danger", True)
        self.stop_anyway_button.setToolTip(STOP_ANYWAY_TIP)
        self.stop_anyway_button.setVisible(False)
        self.repair_label = QLabel("", tab)
        self.repair_label.setWordWrap(True)
        self.repair_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.repair_label.setVisible(False)
        # 8.9a. Built only where the seam is wired, so a game outside 8.9a/8.9b
        # has no uninstall controls rather than ones that cannot work. The three
        # are a set: a button that shows the PLAN, the checkbox owner answer 2
        # asked for, and a second button that only appears once a plan is on
        # screen.
        self.uninstall_button: QPushButton | None = None
        # T34's button, and since T95 it is on EVERY tab and ALWAYS shown (owner
        # decision 4, 2026-09-23). TBC and Tortoise have no Uninstall, so gating
        # it on `services.uninstall` left them no way off the list at all (Andood,
        # 2026-09-22). The × on the sidebar tab is mouse-only; this is the route
        # for a gamepad. It is low-key: no `primary`/`danger` property, and it
        # sits after the row's stretch, away from Start and Stop.
        # `_update_forget_visibility()` lights it up (`primary`) when it is the
        # answer. The press asks the window (`remove_requested`), which does the rest.
        self.forget_install_button: QPushButton | None = QPushButton(REMOVE_FROM_YULON, tab)
        self.forget_install_button.setToolTip(
            "Stop listing this server in Yu'lon. Nothing is deleted; you are asked first."
        )
        self.forget_install_button.clicked.connect(self.forget_install)
        self.keep_characters_check = QCheckBox(
            "Keep my characters (the database volume is left alone)", tab
        )
        self.keep_characters_check.setChecked(False)  # owner answer 2: unticked by default
        # Shown only once a plan is on screen (T188 A14), like the T181 box below.
        self.keep_characters_check.setVisible(False)
        # T181 §4: Remove server offers to delete its ready-to-play client too.
        # Ticked by default, and shown only once a plan is on screen and the
        # folder still carries this server's marker (`_uninstall_plan_ready()`).
        self.delete_play_client_check = QCheckBox(
            f"Also delete its ready-to-play client at {self.services.play_client_dir}", tab
        )
        self.delete_play_client_check.setChecked(True)
        self.delete_play_client_check.setVisible(False)
        self.uninstall_confirm_button = QPushButton("Uninstall this server", tab)
        self.uninstall_confirm_button.setProperty("danger", True)
        self.uninstall_confirm_button.setVisible(False)
        self.uninstall_label = QLabel("", tab)
        self.uninstall_label.setWordWrap(True)
        self.uninstall_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.uninstall_label.setVisible(False)
        # 8.8. Beside Start, because it is the same errand seen from the other
        # side: Start plays this server from here, and this puts it and its
        # client in the Steam library so a Deck in Gaming Mode can. Built only
        # where the seam is wired, which is Linux -- on Windows and macOS there
        # is no button rather than a dead one.
        self.steam_button: QPushButton | None = None
        self.steam_label = QLabel("", tab)
        self.steam_label.setWordWrap(True)
        self.steam_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.steam_label.setVisible(False)
        if self.services.steam is not None:
            self.steam_button = QPushButton("Add to Steam\u2026", tab)
            self.steam_button.clicked.connect(self.add_to_steam)
            self.steam_label.setVisible(True)
        self._build_play_controls(tab)
        if self.services.uninstall is not None:
            self.uninstall_button = QPushButton("Uninstall\u2026", tab)
            self.uninstall_button.clicked.connect(self.show_uninstall_plan)
            self.uninstall_confirm_button.clicked.connect(self.run_uninstall)
            self.keep_characters_check.toggled.connect(self._redraw_uninstall_plan)
            self.delete_play_client_check.toggled.connect(self._redraw_uninstall_plan)
            self.uninstall_label.setVisible(True)
        self.start_button.clicked.connect(self.start_server)
        self.stop_button.clicked.connect(self.stop_server)
        self.refresh_button.clicked.connect(self.recheck)
        self.remove_button.clicked.connect(self.remove_containers)
        self.repair_button.clicked.connect(self.repair_import)
        self.stop_other_button.clicked.connect(self.stop_other_and_start)
        self.reinstall_docker_button.clicked.connect(self.reinstall_docker)
        self.stop_anyway_button.clicked.connect(self.stop_now_anyway)
        row = QHBoxLayout()
        for b in (self.start_button, self.stop_button):
            row.addWidget(b)
        # T181: beside Start and Stop, the third thing this row does with the server.
        if self.play_button is not None and self.play_menu_button is not None:
            row.addWidget(self.play_button)
            row.addWidget(self.play_menu_button)
            row.addWidget(self.play_cancel_button)
        for b in (self.refresh_button, self.remove_button, self.repair_button):
            row.addWidget(b)
        if self.steam_button is not None:
            row.addWidget(self.steam_button)
        # The actions keep their natural size instead of stretching to fill the
        # row: a Start button drawn 226px wide beside a 95px word looks like a
        # broken border, not a button. The spare width goes to a trailing gap.
        row.addStretch(1)
        # T95 decision 4: low-key and at the far end of the row, but always there.
        row.addWidget(self.forget_install_button)
        # The header line: the install's name and path, with the realm's live
        # status as a glowing gem badge on the right. `DadcraftRealmBadge` is
        # the one decoration that had a natural home in the view but was only
        # ever exercised by tests.
        name_row = QHBoxLayout()
        name_row.addWidget(
            QLabel(f"<b>{self.entry.name}</b> — {self.services.controller.server_dir}")
        )
        name_row.addStretch(1)
        self.realm_badge = DadcraftRealmBadge("stopped", tab)
        name_row.addWidget(self.realm_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        box.addLayout(name_row)
        # T106's banner, hidden until the check says this install's
        # docker-compose.yml is not what this version writes -- and, after a
        # repair, until the recreate that applies it has run. Amber and above the
        # status line for `rebuild_banner`'s reason: it is the one thing on this
        # tab the player has to decide.
        self.compose_banner = QWidget(tab)
        compose_banner_box = QHBoxLayout(self.compose_banner)
        compose_banner_box.setContentsMargins(8, 6, 8, 6)
        self.compose_banner_label = QLabel("", self.compose_banner)
        self.compose_banner_label.setWordWrap(True)
        self.compose_banner_label.setStyleSheet(f"color: {COLOR_TEXT_WARNING};")
        self.compose_banner_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse  # so the backup's name can be copied
        )
        self.compose_banner_button = QPushButton(REPAIR_FILES_LABEL, self.compose_banner)
        self.compose_banner_button.clicked.connect(self._compose_banner_pressed)
        compose_banner_box.addWidget(self.compose_banner_label, 1)
        compose_banner_box.addWidget(self.compose_banner_button)
        self.compose_banner.setStyleSheet(
            f"background-color: {COLOR_BG_PARCHMENT}; border: 1px solid {COLOR_TEXT_WARNING};"
        )
        self.compose_banner.setVisible(False)
        box.addWidget(self.compose_banner)
        # T129's banner, T106's shape: hidden until the check says this version
        # corrected an install-plan step these databases were imported with.
        self.corrections_banner = QWidget(tab)
        corrections_box = QHBoxLayout(self.corrections_banner)
        corrections_box.setContentsMargins(8, 6, 8, 6)
        self.corrections_banner_label = QLabel("", self.corrections_banner)
        self.corrections_banner_label.setWordWrap(True)
        self.corrections_banner_label.setStyleSheet(f"color: {COLOR_TEXT_WARNING};")
        self.corrections_banner_button = QPushButton(
            native.CORRECTIONS_BUTTON_LABEL, self.corrections_banner
        )
        self.corrections_banner_button.clicked.connect(self.apply_database_corrections)
        corrections_box.addWidget(self.corrections_banner_label, 1)
        corrections_box.addWidget(self.corrections_banner_button)
        self.corrections_banner.setStyleSheet(
            f"background-color: {COLOR_BG_PARCHMENT}; border: 1px solid {COLOR_TEXT_WARNING};"
        )
        self.corrections_banner.setVisible(False)
        box.addWidget(self.corrections_banner)
        box.addWidget(self.verdict_label)
        box.addWidget(self.status_label)
        box.addWidget(self.distro_label)
        box.addWidget(self.upstream_label)
        box.addWidget(self.pathfinding_label)
        pathfinding_row = QHBoxLayout()
        pathfinding_row.addWidget(self.pathfinding_start_button)
        pathfinding_row.addWidget(self.pathfinding_stop_button)
        pathfinding_row.addStretch(1)
        box.addLayout(pathfinding_row)
        box.addWidget(self.world_upkeep_label)
        upkeep_row = QHBoxLayout()
        upkeep_row.addWidget(self.reextract_button)
        upkeep_row.addWidget(self.finish_world_button)
        upkeep_row.addStretch(1)
        box.addLayout(upkeep_row)
        box.addWidget(self.client_dir_label)
        if self.set_client_dir_button is not None:
            box.addWidget(self.set_client_dir_button)
        if self.forget_client_dir_button is not None:
            box.addWidget(self.forget_client_dir_button)
        box.addWidget(self.channel_label)
        box.addWidget(self.test_console_button)
        box.addWidget(self.console_probe_label)
        box.addWidget(self.enable_channel_button)
        box.addWidget(self.repair_channel_button)
        box.addLayout(row)
        box.addWidget(self.play_label)
        box.addWidget(self.steam_label)
        box.addWidget(self.problem_label)
        box.addWidget(self.stop_anyway_button)
        box.addWidget(self.stop_other_button)
        box.addWidget(self.reinstall_docker_button)
        box.addWidget(self.repair_label)
        if self.uninstall_button is not None:
            box.addWidget(self.uninstall_button)
            # T188 A14: the choices come after the plan they change and just
            # above the press that acts on them, read in that order.
            box.addWidget(self.uninstall_label)
            box.addWidget(self.keep_characters_check)
            box.addWidget(self.delete_play_client_check)
            box.addWidget(self.uninstall_confirm_button)
        box.addStretch(1)
        self._add_panel_tab(tab, "server", "Server")

    def busy_reason(self) -> str | None:
        """Why this tab must not be torn down yet, or None.

        Both runs of the one-shot import service: the Server tab's repair and
        the Modules tab's `apply_module_sql()`. It said "only the import" and
        meant it until 8.7a gave that service a second button; the module run
        is the shorter of the two, which is not a defence, because how many
        pending SQL files a module set has is not something this tab gets to
        assume.

        Everything else here finishes inside `shutdown()`'s
        join; a database import runs for 10-30 minutes, which is long enough
        that a user WILL close the window during one — and closing during one
        froze the window for `STOP_GRACE_SECONDS + 30` seconds and then aborted
        the process, because `_JobWorker.run()` calls its work synchronously so
        `thread.quit()` cannot preempt a blocking `subprocess.run`, and a
        QThread destroyed while running aborts rather than warns (0xC0000409,
        verified, and recorded in `main.py`). Refusing the close is the honest
        outcome: the import cannot be stopped, so the only choice available was
        ever between waiting and a crash (review, 2026-08-23).
        """
        # T94: a reset or its undo is writing the server's own confs, and a
        # QThread destroyed mid-job aborts the process (see above).
        if self._reset_running:
            return TUNING_RESET_RUNNING
        if self._bot_count_writing:
            return BOT_COUNT_RUNNING
        if self._time_zone_writing:
            return TIME_ZONE_RUNNING
        if self._uninstall_running:
            return UNINSTALL_RUNNING
        if self._play_client_running:
            return PLAY_PIPELINE_RUNNING if self._play_preparing else PLAY_CLIENT_RUNNING
        if self._module_sql_running:
            return (
                "The module importer is still running. It cannot be stopped, and closing now "
                "would leave the world database part-way through a module's SQL. This window "
                "will close normally once it finishes — the Modules tab shows what it is "
                "printing."
            )
        if not self._import_running:
            return None
        return (
            "The database import is still running. It cannot be stopped, and closing now would "
            "leave the databases half-written. This window will close normally once the import "
            "finishes — it takes 10-30 minutes, and the Server tab shows what it is doing."
        )

    def shutdown(self) -> None:
        """Stop this tab's timers and join its background jobs (called before teardown)."""
        self._closed = True
        # T158: a stop still waiting for a world to load gives up at its next
        # look and sends nothing, so the joins below are not held by a load
        # and the world is left running rather than signalled mid-load.
        self._stop_abandon.set()
        self._timer.stop()
        if self._docker_repair_cancel is not None:
            # T160: a repair waiting on a question stops rather than holding
            # the join below for as long as nobody answers.
            self._docker_repair_cancel.set()
        for panel in self.log_panels():
            panel.stop()
            panel.wait(5000)
        waiter = getattr(self._jobs, "wait", None)
        if callable(waiter):
            # Derived from the grace, not a flat ten seconds. `_JobWorker.run()`
            # calls its work synchronously, so `thread.quit()` cannot interrupt a
            # blocking `subprocess.run` — and `main.py` records that a QThread
            # destroyed while running ABORTS the process (0xC0000409) rather than
            # warning. A stop now takes 58-91s measured, so a ten-second join
            # made that abort the ordinary outcome of closing the window during
            # one. Waiting out the grace is the lesser evil: the alternative is
            # not a faster exit, it is a crash (review, 2026-08-23).
            # T158's load wait has no time limit, and is not what this join
            # waits on: `_stop_abandon`, set above, ends it at its next check,
            # with nothing sent -- within a quarter of a second if it is between
            # looks, or once the look in flight returns, whose two docker
            # commands are each bounded by `docker._LOAD_LOOK_TIMEOUT` (30 s).
            # A rebuild panel's wait hears the panel's `stop()` above instead:
            # before the replace that gives the rebuild up with nothing touched;
            # in a rollback it stops the failed build regardless (its Cancel).
            waiter(int((docker.STOP_GRACE_SECONDS + 30) * 1000))

    # -------------------------------------------------------- background work

    def _run(
        self,
        work: Callable[[], object],
        on_done: Callable[[object], None],
        on_error: Callable[[object], None],
    ) -> None:
        """Run `work` off the GUI thread. `on_done`/`on_error` MUST be this view's own
        bound slots - a plain callable would be delivered on the worker thread."""
        self._jobs(work, on_done, on_error)

    # ------------------------------------------- a stopped WSL distro (T133)

    def _waits_for_the_distro(self, key: str, reading: Callable[[], None]) -> bool:
        """True, keeping `reading` for later, unless WSL last SAID this server's distro runs.

        Every reading this tab takes by itself -- building a sub-tab, a poll,
        the reload after an action -- asks this first. From the desktop session
        ANY read under `\\\\wsl.localhost\\<distro>` starts a stopped distro, and
        so does any `wsl -d` (measured, T132 M7): opening the app booted it, and
        a server killed earlier came back through `restart: unless-stopped`.
        Stopped, a listing that did not answer, and not asked yet all wait (T133
        review): only `running` is permission. `reading` runs once an answer
        says so (`_distro_answered()`); Start is what starts it, because Start is
        asked for. One gate for every reading, so a new one cannot keep the rule
        by spelling it differently -- or forget it that way.
        """
        if self._distro == "running":
            return False
        self._waiting_on_distro[key] = reading
        return True

    def _ask_the_distro(self) -> None:
        """Ask WSL's listing about this server's distro, off the GUI thread (T133).

        Two `wsl.exe -l -q` calls, each allowed a minute, so never on the GUI
        thread: the tab is built not asked, and this answers it whether or not
        the tab polls. A server on this host asks nothing.
        """
        distro = self.services.controller.wsl_distro
        if distro is None:
            return
        self._run(lambda: wsl.distro_state(distro), self._distro_asked, self._distro_ask_failed)

    @Slot(object)
    def _distro_asked(self, state: object) -> None:
        if state == "running" or state == "stopped" or state == "unknown":
            self._distro_answered(state)

    @Slot(object)
    def _distro_ask_failed(self, exc: object) -> None:
        logger.warning(f"could not ask WSL about {self.services.controller.wsl_distro}: {exc}")
        self._distro_answered("unknown")

    def _distro_answered(self, state: wsl.DistroState) -> None:
        """WSL's word on the distro: say it, and run what waited once it says `running`."""
        if state == self._distro:
            return
        self._distro = state
        self._show_the_distro()
        if state != "running":
            # The last verdict ("up -- 0 players…") described a world this
            # distro no longer runs, and it sat above "world down" and the
            # stopped line (T133 live gate). The next one waits for `running`.
            self._clear_the_verdict()
            return
        waiting = list(self._waiting_on_distro.values())
        self._waiting_on_distro.clear()
        for reading in waiting:
            reading()

    def _show_the_distro(self) -> None:
        """The Server tab's line while the distro is stopped or WSL did not say; hidden else."""
        distro = self.services.controller.wsl_distro
        said = {"stopped": DISTRO_STOPPED, "unknown": DISTRO_UNKNOWN}.get(self._distro or "", "")
        self.distro_label.setText(said.format(distro=distro))
        self.distro_label.setVisible(bool(said))

    def _world_reading(self, reading: Callable[[], object]) -> Callable[[], object]:
        """`reading`, run on the worker only if WSL says the distro runs at that moment (T133).

        For the readings a TIMER starts -- the dashboard verdict every five
        seconds, T138's later settle -- whose `_waits_for_the_distro()` answer
        is up to one poll old: a distro stopped from outside (`wsl -t`,
        `--shutdown`) since that poll would be started again by `wsl -d` and
        never idle out (T133 review). None when it does not run; the slot
        ignores a None. A server on this host asks nothing.
        """
        distro = self.services.controller.wsl_distro

        def guarded() -> object:
            if distro is not None and not wsl.may_read(distro):
                return None
            return reading()

        return guarded

    @Slot()
    def _tick(self) -> None:
        """The five-second poll: ask, unless a poll is already out (T95 review, Task 2).

        Returns without going through `refresh_status()`'s dropped-ask branch,
        so the tick never marks the answer in flight superseded. It asks
        nothing new, and on a Docker slower than the interval it would make
        every answer unknown and every poll back to back. Only an action's
        own refresh and the Refresh button (`recheck()`) do.
        """
        if self._status_pending:
            return
        self.refresh_status()

    @Slot()
    def refresh_status(self) -> None:
        """Re-read `docker ps` off the GUI thread and update the Server tab.

        Deliberately leaves `problem_label` alone: the five-second poll runs
        immediately after a failed action, and clearing here would wipe the
        explanation before it could be read. The Refresh BUTTON clears it —
        see `recheck()`.
        """
        if self._status_pending:
            # A poll is already in flight; never queue them up. But remember
            # the ask: its answer is older than this question (T95).
            self._status_superseded = True
            return
        self._status_pending = True
        self._status_superseded = False
        self._status_asked_busy = self._busy
        self._status_asks += 1
        self._status_ask_out = self._status_asks
        self._run(self.services.controller.status, self._status_ready, self._status_failed)

    @Slot()
    def refresh_verdict(self) -> None:
        """Re-read this install's verdict off the GUI thread, if it has one.

        Separate from `refresh_status()` rather than folded into it: the status
        path is the one Phase 7 proved, and a tick that now also reads a
        database is a different failure surface. Its own in-flight guard, for
        the reason `refresh_status()` has one — a tick that takes longer than
        the interval must not queue up behind itself.
        """
        if self.services.dashboard is None or self._verdict_pending:
            return
        if self._waits_for_the_distro("verdict", self.refresh_verdict):
            return
        self._verdict_pending = True
        self._run(
            self._world_reading(self.services.dashboard), self._verdict_ready, self._verdict_failed
        )

    @Slot(object)
    def _verdict_ready(self, result: object) -> None:
        self._verdict_pending = False
        if self._badge_held is not None:
            # T188 C4: "up — 3 players" under a badge saying STOPPING is two
            # readings at once; the line comes back with the first poll after.
            self._clear_the_verdict()
            return
        if result is None or self._distro != "running":
            # None is `_world_reading()` finding the distro stopped on the
            # worker; a verdict landing after a poll said stopped is as old.
            # Neither may leave an earlier verdict standing (T133).
            self._clear_the_verdict()
            return
        if not isinstance(result, dashboard_module.Verdict):
            return
        self.verdict_label.setText(dashboard_module.line(result))
        self.verdict_label.setVisible(True)
        self.enable_channel_button.setEnabled(_press_is_allowed(result))

    def _clear_the_verdict(self) -> None:
        """No verdict line: the distro is not known to run, so no world to describe (T133)."""
        self.verdict_label.setText("")
        self.verdict_label.setVisible(False)

    # ------------------------------------------- the movement-map job (T179)

    @Slot()
    def refresh_pathfinding(self) -> None:
        """Ask the job how far it has got, off the GUI thread (it asks Docker).

        Its own in-flight guard, for `refresh_verdict()`'s reason: a reading slower
        than the poll must not queue up behind itself.
        """
        seam = self.services.pathfinding
        if seam is None or self._pathfinding_pending:
            return
        self._pathfinding_pending = True
        self._pathfinding_generation += 1
        generation, ask = self._pathfinding_generation, seam.status

        def read() -> tuple[int, mmaps.MmapsStatus]:
            try:
                return generation, ask()
            except Exception as exc:  # noqa: BLE001 - carried to the GUI thread with its read
                raise _PathfindingReadBroke(generation, exc) from exc

        self._run(read, self._pathfinding_read, self._pathfinding_read_failed)

    @Slot(object)
    def _pathfinding_read(self, answer: object) -> None:
        generation, status = cast(tuple[int, object], answer)
        if generation != self._pathfinding_generation:
            return  # a press or a newer read came after it: it describes the past
        self._pathfinding_pending = False
        if isinstance(status, mmaps.MmapsStatus):
            self._pathfinding_status = status
        self._show_pathfinding()

    @Slot(object)
    def _pathfinding_read_failed(self, exc: object) -> None:
        """A reading that broke says so on its own line; the presses wait for a good one."""
        if isinstance(exc, _PathfindingReadBroke):
            if exc.generation != self._pathfinding_generation:
                return
            exc = exc.cause
        self._pathfinding_pending = False
        logger.warning(f"could not read the pathfinding data's progress: {exc}")
        self._pathfinding_status = None
        self.pathfinding_label.setText(PATHFINDING_UNREAD.format(exc=exc))
        self.pathfinding_label.setVisible(True)
        self.pathfinding_start_button.setVisible(False)
        self.pathfinding_stop_button.setVisible(False)

    def _show_pathfinding(self) -> None:
        """Draw the line and its two presses from the last reading.

        Start is offered where a run could begin and is held while a press of this
        tab runs (`_busy`): a Rebuild, an Update, a Return to the tested build or an
        Uninstall each stop the job themselves (`before_rebuild`, Uninstall's own
        seam), and a job started beside one would run against the server the press
        is replacing or removing.
        """
        if not hasattr(self, "pathfinding_label"):
            return  # `_set_busy()` before the Server tab exists
        seam, status = self.services.pathfinding, self._pathfinding_status
        if seam is None:
            for widget in (
                self.pathfinding_label,
                self.pathfinding_start_button,
                self.pathfinding_stop_button,
            ):
                widget.setVisible(False)
            return
        self.pathfinding_label.setVisible(True)
        if status is None:
            self.pathfinding_label.setText(PATHFINDING_ASKING)
        else:
            self.pathfinding_label.setText(status.line())
        can_start = status is not None and status.can_start
        can_stop = status is not None and status.can_stop
        self.pathfinding_start_button.setVisible(can_start)
        self.pathfinding_stop_button.setVisible(can_stop)
        self.pathfinding_start_button.setEnabled(
            can_start and not self._busy and not self._pathfinding_pressing
        )
        self.pathfinding_stop_button.setEnabled(can_stop and not self._pathfinding_pressing)

    @Slot()
    def start_pathfinding(self) -> None:
        """Start the job: offered after a failure, or where it never started."""
        seam = self.services.pathfinding
        if seam is None or self._busy or self._pathfinding_pressing:
            return
        self._press_pathfinding(seam.start)

    @Slot()
    def stop_pathfinding(self) -> None:
        """Stop a running job: its container and its partial output go, nothing is switched on."""
        seam = self.services.pathfinding
        if seam is None or self._pathfinding_pressing:
            return
        self._press_pathfinding(seam.stop)

    def _press_pathfinding(self, press: Callable[[], str]) -> None:
        self._pathfinding_pressing = True
        # Any read out now describes the job as it was before this press.
        self._pathfinding_generation += 1
        self._pathfinding_pending = False
        self._show_pathfinding()
        self._run(press, self._pathfinding_pressed, self._pathfinding_press_failed)

    @Slot(object)
    def _pathfinding_pressed(self, said: object) -> None:
        self._pathfinding_pressing = False
        self.problem_label.setText(str(said))
        self._pathfinding_pending = False
        self.refresh_pathfinding()

    @Slot(object)
    def _pathfinding_press_failed(self, exc: object) -> None:
        self._pathfinding_pressing = False
        self.problem_label.setText(str(exc))
        self.action_failed.emit(str(exc))
        self._pathfinding_pending = False
        self.refresh_pathfinding()

    # ------------------------------- what an update left owing (T179 Task 6)

    @Slot()
    def refresh_world_upkeep(self) -> None:
        """Read the map-data and world-update sentences, off the GUI thread (a file each).

        Inside a WSL distro the read waits for the distro to run (T133): reading
        the folder would boot it.
        """
        seam = self.services.world_upkeep
        if seam is None or self._upkeep_pending:
            return
        if self._waits_for_the_distro("world upkeep", self.refresh_world_upkeep):
            return
        self._upkeep_pending = True
        self._run(seam.read, self._world_upkeep_read, self._world_upkeep_read_failed)

    @Slot(object)
    def _world_upkeep_read(self, answer: object) -> None:
        self._upkeep_pending = False
        if isinstance(answer, tuple) and len(answer) == 2:
            self._upkeep = cast(tuple[str | None, str | None], answer)
        self._show_world_upkeep()

    @Slot(object)
    def _world_upkeep_read_failed(self, exc: object) -> None:
        """A read that broke says so on its own line; the presses wait for a good one."""
        self._upkeep_pending = False
        logger.warning(f"could not read what the last update of {self.entry.id} left: {exc}")
        self._upkeep = (None, None)
        self._show_world_upkeep()
        self.world_upkeep_label.setText(WORLD_UPKEEP_UNREAD.format(exc=exc))
        self.world_upkeep_label.setVisible(True)

    def _upkeep_held(self) -> bool:
        """Another press of this tab runs: the log panel's job, or any `_busy` action."""
        return self._busy or self.rebuild_log.running

    def _show_world_upkeep(self) -> None:
        """Draw the sentences and offer each press only where its sentence asks for it.

        Both are held while another press runs (`_busy`, the log panel): each stops
        or reads the server the other would be replacing.
        """
        if not hasattr(self, "world_upkeep_label"):
            return  # `_set_busy()` before the Server tab exists
        seam = self.services.world_upkeep
        map_data, world = self._upkeep if seam is not None else (None, None)
        said = [line for line in (world, map_data) if line]
        self.world_upkeep_label.setText("\n\n".join(said))
        self.world_upkeep_label.setVisible(bool(said))
        held = hasattr(self, "rebuild_log") and self._upkeep_held()
        can_reextract = seam is not None and seam.reextract is not None and map_data is not None
        can_finish = seam is not None and seam.finish_world is not None and world is not None
        self.reextract_button.setVisible(can_reextract)
        self.reextract_button.setEnabled(can_reextract and not held)
        self.finish_world_button.setVisible(can_finish)
        self.finish_world_button.setEnabled(can_finish and not held)

    @Slot()
    def reextract_map_data(self) -> bool:
        """Extract the map data again from the player's client; streams into the log panel.

        The client folder is the one this tab already holds (Set the client
        folder…), or, with none, the one the map data was last made from; the
        press refuses with what to pick when neither is known. The engine refuses
        while the world server may be running.
        """
        seam = self.services.world_upkeep
        press = seam.reextract if seam is not None else None
        if press is None:
            return False
        client_dir = self.services.client_dir
        return self._run_upkeep_press(
            lambda cancel: press(cancel=cancel, client_dir=client_dir),
            title=f"Extracting {self.entry.name}'s map data again",
        )

    @Slot()
    def finish_world_update(self) -> bool:
        """Import the world tables the last update left waiting; streams into the log panel."""
        seam = self.services.world_upkeep
        press = seam.finish_world if seam is not None else None
        if press is None:
            return False
        return self._run_upkeep_press(
            lambda cancel: press(cancel=cancel),
            title=f"Finishing {self.entry.name}'s world update",
        )

    def _run_upkeep_press(
        self, press: Callable[[threading.Event], Iterator[str]], *, title: str
    ) -> bool:
        """`apply_database_corrections()`'s shape: refused while busy, run in the panel, shown."""
        if self._upkeep_held():
            QMessageBox.information(self, "Something else is running", WORLD_UPKEEP_BUSY)
            return False
        cancel = self._rebuild_cancel()
        self._rebuild_is_compile = False
        started = self.rebuild_log.run(
            lambda: self._watch_for_load_wait(press(cancel)),
            title=title,
            cancel=cancel,
            record_as=self._run_record_kind(),
        )
        if started:
            panel = self.rebuild_log.parentWidget()
            if panel is not None:
                self._tabs.setCurrentWidget(panel)
        return started

    @Slot(object)
    def _verdict_failed(self, exc: object) -> None:
        """An instrument that breaks must not take the tab with it.

        It writes its own line rather than `problem_label`, which belongs to the
        actions a user pressed: a failing dashboard would otherwise wipe the
        explanation of the stop that just refused.
        """
        self._verdict_pending = False
        self.verdict_label.setText(f"could not read this server's dashboard: {exc}")
        self.verdict_label.setVisible(True)

    @Slot()
    def enable_channel(self) -> None:
        """Press the enable, and show what it said.

        The press itself refuses while the world is running — that refusal is
        the whole shape of 8.2a — so this hands it the status it already knows
        rather than re-deciding, and shows the sentence either way.
        """
        setup = self.services.channel_setup
        if setup is None:
            return
        self.problem_label.setText("")
        running = self.stop_button.isEnabled()
        try:
            setup.enable(world_running=running)
        except Exception as exc:  # noqa: BLE001 - the refusal is a sentence, not a crash
            self.problem_label.setText(str(exc))
            return
        self.problem_label.setText(
            "The command channel is written into this install's configuration. It is checked "
            "the next time you start the server."
        )
        self._channel_written = True
        self.refresh_channel()

    @Slot()
    def test_console(self) -> None:
        """Send one harmless command through this install's channel and show the answer.

        `server info` because it changes nothing and prints something a person
        can recognise. Off the GUI thread: this waits on a reply window measured
        in seconds, and the Console tab's own send is bounded the same way.
        """
        probe = self.services.console_probe
        if probe is None:
            return
        self.console_probe_label.setText("Asking the console\u2026")
        self.console_probe_label.setVisible(True)
        self._run(
            lambda: probe(commands.SERVER_INFO),
            self._console_probe_ready,
            self._console_probe_failed,
        )

    @Slot(object)
    def _console_probe_ready(self, result: object) -> None:
        """What the channel said, in the words the distinction needs.

        An answer that did not come back delimited is `indeterminate`: the
        command may have run. Saying "failed" there would invite a person to
        send it again, which for a mutation is exactly the wrong advice -- and
        is why this tree is offered no mutations yet (8.2e).
        """
        if not isinstance(result, channel_module.Answer):
            return
        if result.outcome == "yes":
            self.console_probe_label.setText(result.text.strip() or "The console answered.")
            return
        # The reason is a clause and not a sentence -- it is written to be read
        # after "Could not ask:" -- so the full stop is this line's to add. The
        # gate read it back without one when the clause below was appended.
        said = (result.reason or "the console did not answer").rstrip(".") + "."
        if result.indeterminate:
            said += " The command may still have run, so nothing here is a failure."
        self.console_probe_label.setText(f"Could not ask: {said}")

    @Slot(object)
    def _console_probe_failed(self, error: object) -> None:
        self.console_probe_label.setText(f"Could not ask: {error}")

    @Slot()
    def refresh_channel(self) -> None:
        """Say where the channel setup has got to, in words."""
        setup = self.services.channel_setup
        if setup is None:
            # A console channel has no setup to ask about and never changes, so
            # this is the whole of its answer (8.2e).
            if _is_console_channel(self.entry):
                self.channel_label.setText(CONSOLE_CHANNEL_SENTENCE)
                self.channel_label.setVisible(True)
            return
        state = setup.setup_state()
        self._show_channel(state)

    def _show_channel(self, state: object) -> None:
        """One place where a channel state becomes what the tab looks like."""
        self.channel_label.setText(_channel_sentence(state))
        self.channel_label.setVisible(True)
        self.repair_channel_button.setVisible(isinstance(state, channel_setup.Refused))
        # T188 A4: offered only while there is something to turn on -- not under
        # "verified as …", not while an account waits to be proved, not where
        # Repair is the answer, and not again after a press that took. GaveUp
        # is offered even after a press: it means the press did not take.
        self.enable_channel_button.setVisible(
            self.services.channel_setup is not None
            and (
                isinstance(state, channel_setup.GaveUp)
                or (
                    not self._channel_written
                    and not isinstance(
                        state,
                        channel_setup.Verified | channel_setup.Pending | channel_setup.Refused,
                    )
                )
            )
        )

    @Slot()
    def repair_channel(self) -> None:
        """Reset the channel account's password, and say what came back.

        Pressed rather than automatic: the reset is a write to the user's auth
        database, and one that this app is only allowed to make against its own
        account. `InstallChannel.repair()` refuses from any state but refused,
        so a stale press cannot break a channel that has since started working.
        """
        setup = self.services.channel_setup
        if setup is None:
            return
        self.problem_label.setText("")
        try:
            state = setup.repair()
        except Exception as exc:  # noqa: BLE001 - a failed repair is a sentence
            self.problem_label.setText(str(exc))
            return
        self._show_channel(state)

    @Slot()
    def recheck(self) -> None:
        """What the Refresh button does: drop the last problem, then re-read status.

        Without this the paragraph outlived whatever it described — a user could
        fix the `.env` the refusal named, press Refresh, and read "db up, auth
        up, world up" above "Nothing was stopped: this could equally be another
        install…" (review, 2026-08-22).
        """
        self._disarm_actions()
        self.problem_label.setText("")
        # Ask the database again: Refresh is the only way for a user who has
        # just fixed something to make the tab re-examine an unfinished import.
        self._import_asked = False
        self.refresh_status()
        self.check_server_files()
        # T124: the day's cache answers this, so pressing Refresh repeatedly
        # costs no network.
        self._refresh_upstream_news()

    @Slot(object)
    def _status_ready(self, result: object) -> None:
        self._status_pending = False
        superseded = self._status_superseded
        ends_the_hold = self._ends_the_hold()
        status = result
        if not isinstance(status, InstallStatus):
            # Same hole, one branch narrower: a result that is not a status
            # skipped the reveal too (T54). Not an answer either, so a removal
            # must not trust an older one (T95).
            self._last_status = None
            self._update_forget_visibility()
            self._ask_again_if_superseded(superseded)
            return
        # T95: an answer is kept only if no Server action ran while it was
        # read, and no refresh was dropped while it was. A poll asked mid-Start
        # can answer "stopped" for a server that came up a second later, and so
        # can one asked just before it whose Start's own refresh it swallowed.
        # Unknown is what makes a removal stop first.
        stale = superseded or self._status_asked_busy or self._busy
        self._last_status = None if stale else status
        if not self._busy:
            # Only while nothing of ours is running. The five-second poll used to
            # overwrite the label unconditionally, which was invisible at a
            # ten-second stop and is not at a five-minute one: the user pressed
            # Stop, read "stopping…", and then watched it revert to "world up"
            # for the next minute and a half with both buttons dead and no
            # explanation. The buttons below are still updated — it is the
            # sentence that has to hold still, not the state (review, 2026-08-23).
            parts = [
                f"db {'up' if status.db else 'down'}",
                f"auth {'up' if status.auth else 'down'}",
                f"world {'up' if status.world else 'down'}",
            ]
            self.status_label.setText("status: " + ", ".join(parts))
        self.start_button.setEnabled(not status.all_running and not self._busy)
        self.stop_button.setEnabled(status.any_running and not self._busy)
        if self._badge_held is not None and ends_the_hold:
            # Asked after our job ended, so this is the job's own follow-up
            # reading: the hold ends HERE and not when the job does. Falling back
            # to the reading from before the press flashed REALM ONLINE between
            # STOPPING and OFFLINE (T188 fix round 1).
            self._badge_held = None
        if self._badge_held is None:
            self.realm_badge.set_status(_realm_badge_status(status))
        if not stale and status.any_running:
            # T95: something brought the server back without Start, so "nothing
            # to remove" is no longer true, and a lit "Remove from Yu'lon…" beside
            # a live server points at the wrong thing. Only a fresh answer: one
            # asked before the removal says nothing about after it.
            self._nothing_to_remove = False
        self._update_forget_visibility()
        # T160: Docker answered, so there is nothing to reinstall.
        self.reinstall_docker_button.setVisible(False)
        self._update_client_dir_row()
        # T133: every answer, stale or not -- what WSL said about the distro is
        # not a fact an action of ours can make wrong the way "world down" is.
        if status.distro is not None:
            self._distro_answered(status.distro)
        self._ask_about_the_import(status)
        self.status_changed.emit(status)
        self._ask_again_if_superseded(superseded)

    def _ask_again_if_superseded(self, superseded: bool) -> None:
        """Ask once more for the refresh this poll's answer made `refresh_status()` drop (T95).

        Without it, the answer on file stays unknown until the next five-second
        tick, and an action's own end-of-action refresh is simply lost.
        """
        if superseded:
            self.refresh_status()

    def _update_client_dir_row(self) -> None:
        """Re-read the row's text on the same poll as the status line (T36).

        Only the SENTENCE, never the buttons: the recorded folder cannot
        change without a rebuild (`client_dir_changed`), but whether it has
        gained an `Interface/` folder can -- the owner's own case is a client
        extracted and pointed at before the game has ever been started.
        """
        if self.set_client_dir_button is None:
            return
        self.client_dir_label.setText(_client_dir_row_text(self.services.client_dir))

    def _forget_is_eligible(self) -> bool:
        """Whether this server's folder is confirmed gone on this host (T34; T95).

        One predicate for the two things that depend on it: the highlight on
        "Remove from Yu'lon…" (`_update_forget_visibility()`) and, through
        `folder_is_gone()`, which question the window asks and whether it
        stops the server first. Asked fresh each time, never cached, because
        a folder can come back between a poll and a press (review, T34 round 2).

        Every tab since T95: the rule is about the folder, not about whether an
        Uninstall is wired. `wsl_distro` answers False for a distro install,
        whose `server_dir` is not a path on this process's filesystem.
        """
        controller = self.services.controller
        return controller.wsl_distro is None and platform.folder_is_gone(controller.server_dir)

    def _update_forget_visibility(self) -> None:
        """Highlight "Remove from Yu'lon…" while it is the way out (T34, T54; T95 decision 4).

        The button itself is always shown now. What this re-reads on every poll,
        including a poll that could not reach Docker (T54), is whether it is
        THE answer: the folder is gone, or a remove-containers press found
        nothing (until the next Start). Read fresh on every poll rather than
        once at tab-build time, because the folder can be deleted out from
        under an open tab. `primary` is the theme's own emphasis, the property
        Start carries, so no new QSS is needed.
        """
        if self.forget_install_button is None:
            return
        highlight = self._forget_is_eligible() or self._nothing_to_remove
        if bool(self.forget_install_button.property("primary")) == highlight:
            return
        self.forget_install_button.setProperty("primary", highlight)
        style = self.forget_install_button.style()
        style.unpolish(self.forget_install_button)
        style.polish(self.forget_install_button)

    def folder_is_gone(self) -> bool:
        """Confirmed absent on this host, for the window's dialog (T95). `_forget_is_eligible()`."""
        return self._forget_is_eligible()

    def last_seen_running(self) -> bool | None:
        """Whether the last status poll saw this server running; None if unknown (T95).

        Unknown while a poll is in flight too: the answer on file is older than
        the question already asked, and something made it worth asking.
        """
        if self._status_pending or self._last_status is None:
            return None
        return self._last_status.any_running

    def forget_refusal(self) -> str | None:
        """Why this server may not be removed from Yu'lon right now, or None (T95).

        Removing drops this tab through `drop_controller()`, which is a teardown
        that joins this tab's jobs for a bounded time only. So everything that
        refuses a teardown refuses this too, and so does every job that would
        be cut off in the middle. `busy_reason()` comes first because its
        sentences are the long ones the close guard already shows. Then the
        jobs that run through `_run()` with nothing but their own flag: the
        T64 backup, a manual backup, a restore (stopped half-way it leaves the
        databases half-written), a Modules tab job (`_module_pending`, which a
        custom-module install sets too) and a network apply. Then the Modules
        tab's panel, which a rebuild, a database update or an adopt runs in.
        Last is any Server action, including the stop a removal is already
        waiting for. The sentences are `forgetting`'s; the import is local
        so this module's import block stays as it is.
        """
        from yulon import forgetting

        if (reason := self.busy_reason()) is not None:
            return reason
        if self._backup_before_update:
            return forgetting.UPDATE_BACKUP_RUNNING
        if self._backup_running:
            return forgetting.BACKUP_RUNNING
        if self._restore_running:
            return forgetting.RESTORE_RUNNING
        if self._module_pending is not None:
            return forgetting.module_running(self._module_pending)
        if self._network_applying:
            return forgetting.NETWORK_RUNNING
        if self.rebuild_log.running:
            return forgetting.PANEL_RUNNING
        if self._busy:
            return forgetting.SERVER_ACTION_RUNNING
        return None

    def _ask_about_the_import(self, status: InstallStatus) -> None:
        """Put the import question once per time the database comes up.

        Not on every poll: the probe is three `docker exec`s, and the poll runs
        every five seconds forever. Not never, either — the button has to be
        able to appear without the user knowing to press Refresh first, and the
        install this exists for is one whose Start visibly fails.
        """
        if not status.db:
            self._import_asked = False
            # The reading is DROPPED and not kept, which is what makes the adopt
            # button's rule true rather than merely once-true: the answer was
            # taken from a database that is now down, and a control that writes
            # a marker row must not stay lit on a reading nothing can renew.
            self._forget_the_adopt_reading()
            self._forget_the_corrections_reading()
            return
        if self._import_asked:
            return
        self._import_asked = True
        self._run(
            self.services.controller.import_state, self._import_state_ready, self._import_failed
        )
        # The SECOND question, put at the same moment and under the same
        # once-per-database-start rule: `AdoptRoute.state` is `MarkerGate.probe()`
        # over this install's plan, which is several `docker exec ... mariadb`
        # calls. Asked on the five-second poll it would be several of those
        # every five seconds, forever, on every tab the app has open; asked at
        # tab build time it would be paid for by every install that opens a
        # controller view, most of which will never press this. Once, when the
        # database comes up, is the same rule the import question above already
        # keeps and the same reason `_ask_about_the_import` is named for it.
        if self.services.adopt is not None:
            self._run(self.services.adopt.state, self._adopt_state_ready, self._adopt_state_failed)
        # T129's reading, under the same once-per-database-start rule and for the
        # same reason: it is `docker exec … mariadb` against the marker schema.
        if self.services.corrections is not None:
            self._run(
                self.services.corrections.check,
                self._corrections_checked,
                self._corrections_check_failed,
            )

    @Slot(object)
    def _import_state_ready(self, result: object) -> None:
        if not isinstance(result, docker.ImportState):
            return
        self._import_state = result
        self._show_repair()

    @Slot(object)
    def _adopt_state_ready(self, result: object) -> None:
        if not isinstance(result, docker.ImportState):
            return
        self._adopt_state = result
        self._set_adopt_button()

    @Slot(object)
    def _adopt_state_failed(self, exc: object) -> None:
        """A probe that raised says nothing about the databases, so nothing is offered.

        `AdoptRoute.state` is documented not to raise; this is the boundary that
        holds even if some future gate forgets, for `_import_failed()`'s reason
        and one this control has of its own — what it would arm is a press that
        writes a completion marker on a database nobody could read.
        """
        logger.warning(f"could not ask the databases whether they can be adopted: {exc}")
        self._forget_the_adopt_reading()

    def _forget_the_adopt_reading(self) -> None:
        """Drop the remembered reading and grey the control with it."""
        self._adopt_state = None
        self._set_adopt_button()

    def _set_adopt_button(self) -> None:
        """Offer the adopt press only while BOTH halves say so, and never while busy.

        Two facts, and the second is not the first — `_show_repair()`'s own
        shape. `services.adopt` is about the CATALOG: this game's plan declares
        a phase meant to be re-applied to a server that already exists, so
        adopting buys something. `_adopt_state` is about these DATABASES: they
        read `populated`, which is the one answer this press can act on.

        Every other answer greys it, and each for its own reason. `imported`
        means the row is already there and the press would refuse. `absent` and
        `partial` mean there is no import to make a claim about. `unreadable`
        means nobody could look — including the ordinary case where the database
        is simply down, which is why the reading is dropped rather than kept
        when the status poll says so.

        The busy gates are the same two the updates button carries, for the same
        reason: this press starts the database and writes to it, so one while
        another action of ours is live is two writers.
        """
        state = self._adopt_state
        offered = (
            self.services.adopt is not None
            and state is not None
            and state.state == "populated"
            and not self._busy
            and not self.rebuild_log.running
        )
        self.adopt_button.setEnabled(offered)

    @Slot(object)
    def _import_failed(self, exc: object) -> None:
        """A probe that raised says nothing about the database, so nothing is offered.

        `Controller.import_state()` is documented not to raise; this is the
        boundary that holds even if some future probe forgets, because the one
        outcome that must never follow from a failed question is a destructive
        button appearing.
        """
        logger.warning(f"could not ask the databases about their import: {exc}")
        self._import_state = None
        self._show_repair()

    def _show_repair(self) -> None:
        """Offer the repair only while the database says there is one to do AND this game can.

        Two facts, and the second is not the first. `ImportState.repairable` is
        the DATABASE's answer — "these schemas were never filled" — and it is
        true for a CMaNGOS install whose probe cannot find a marker row. What
        the button then runs is `docker.repair_import()`, whose first refusal is
        that this game never said which compose service imports its databases;
        only `wow-wotlk` names one. So a tab that offered the button on
        `repairable` alone would arm a two-press destructive gesture whose only
        possible outcome is that sentence.

        Gated on the entry rather than on `controller.import_probe` being set,
        because those are two different claims: a probe can be attached by
        anything, and the fact that decides whether the ACTION can run is the
        `import_service` the action itself refuses without.
        """
        state = self._import_state
        offer = (
            state is not None
            and state.repairable
            and bool(self.entry.container_spec().import_service)
        )
        self.repair_button.setVisible(offer)
        self.repair_label.setVisible(offer)
        if offer and state is not None:
            self.repair_label.setText(
                "This install's databases were never finished: "
                f"{state.detail}. The server will not start until the import is completed. "
                "Repair runs it again — see the button."
            )
        else:
            self._disarm_repair()
            self.repair_label.setText("")

    @Slot(object)
    def _status_failed(self, exc: object) -> None:
        self._status_pending = False
        self._last_status = None
        # Read before the ask below, which moves `_status_ask_out` on: a poll
        # asked before the holding job ended is older than that job's own
        # follow-up read (T188).
        ends_the_hold = self._ends_the_hold()
        # T95: the refresh dropped while this poll was out is asked again. The
        # app's job runner hands it to a worker thread, so its answer arrives
        # after this method has returned.
        self._ask_again_if_superseded(self._status_superseded)
        self.status_label.setText(f"status: Docker not reachable ({exc})")
        # "unknown", not "stopped": Docker not answering says nothing about the
        # server (T188 final fix round). A hold is left alone by a failure older
        # than its job's own follow-up read; that read failing ends it here.
        if self._badge_held is None or ends_the_hold:
            self._badge_held = None
            self.realm_badge.set_status("unknown")
        self._offer_docker_repair()
        # T54. The reveal used to run only on the success path, and the control
        # it reveals exists for an install that is GONE -- which is the case
        # most likely to have taken Docker with it. A user deleted their server
        # folder and Docker Desktop, was told by the uninstall refusal to press
        # "Forget this install…", and could not be shown it. The predicate needs
        # nothing from Docker: it asks `wsl_distro` and `folder_is_gone()`.
        self._update_forget_visibility()

    def _hold_badge(self, status: str) -> None:
        """Hold the realm badge at `status` while our own Start/Stop/Restart runs (T188).

        Held until the first status reading asked after the job ended answers
        (`_status_ready`), or an idle poll fails (`_status_failed`).
        `_set_busy(False)`, which every one of those jobs reaches on success
        and failure alike, asks for that reading.
        """
        self._badge_held = status
        self._hold_ends_at = None
        self.realm_badge.set_status(status)
        self._clear_the_verdict()

    def _set_busy(self, busy: bool) -> None:
        """Lock the Server buttons while an action of ours is running.

        All four, not two. Remove and Repair were left live while their own
        action ran, so a second arm-and-press during a multi-minute import or
        teardown started a second one on top of the first — and whichever
        finished first called `_set_busy(False)` and unlocked Start while the
        other was still writing schemas (review, 2026-08-23).

        **And the uninstall controls, which is the same defect on the one
        action that cannot be undone** (review, 2026-09-08). The uninstall
        controls were added outside this function, so a purge run with "Keep my
        characters" TICKED could be pressed a second time — 60 to 90 seconds is
        a long time to look at a frozen window — with the box unticked, and the
        second run removed the database volume the first run had promised to
        keep. `keep` is read at press time, so the two presses need not agree.
        Five since 2026-09-08, and Rebuild is the same argument one size larger:
        it stops and replaces the very containers Start, Stop and Remove act
        on, and it runs for the length of a compile. Both directions are locked
        — this method is what a running rebuild calls (the panel's own
        `run_started`/`run_finished`), and `rebuild_server()` refuses while
        `_busy`, so an import cannot start a rebuild on top of itself either.
        Unlocking honours the standing gate: a game with no rebuild wiring must
        not have its greyed button handed back by a job ending.
        """
        self._busy = busy
        # T179: the movement-map job's Start is held while any press runs.
        self._show_pathfinding()
        # T179 Task 6: so are Re-extract map data and Finish the world update.
        self._show_world_upkeep()
        if not busy:
            # T158: whatever job just ended, a load wait's words and its button
            # are over with it. `_tuning_job_done()` and the Bots tab's handlers
            # never write this label, so they cannot be trusted to replace them;
            # only the forced-stop warning is kept.
            self.stop_anyway_button.setVisible(False)
            self.rebuild_stop_anyway_button.setVisible(False)
            if self._stop_words_shown:
                self._stop_words_shown = False
                self.problem_label.setText(self._stop_forced)
        if busy:
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(False)
            self.remove_button.setEnabled(False)
            self.repair_button.setEnabled(False)
            # T160: a second repair on top of a running one would reset the
            # keyring under a pacman that is reading it.
            self.reinstall_docker_button.setEnabled(False)
            if self.uninstall_button is not None:
                self.uninstall_button.setEnabled(False)
            if self.forget_install_button is not None:
                self.forget_install_button.setEnabled(False)
            self.uninstall_confirm_button.setEnabled(False)
            self.keep_characters_check.setEnabled(False)
            # T36's client-folder row: a write during any other action races
            # `main.py`'s rebuild (T36 round 2 review) — the tab this press
            # would drop and reopen is the very tab a rebuild, an import or a
            # module install is running ON.
            if self.set_client_dir_button is not None:
                self.set_client_dir_button.setEnabled(False)
            if self.forget_client_dir_button is not None:
                self.forget_client_dir_button.setEnabled(False)
            # T181: Play may Start, and Make…/Refresh/Delete write the folder a
            # module install writes into and race `main.py`'s rebuild.
            if self.play_button is not None:
                self.play_button.setEnabled(False)
            if self.play_menu_button is not None:
                self.play_menu_button.setEnabled(False)
            self.delete_play_client_check.setEnabled(False)
            self.rebuild_action.setEnabled(False)
            # And both T64 presses, for the rebuild's reason exactly: each of
            # them IS that rebuild with a fetch in front of it. Disabled and not
            # hidden -- whether they exist at all is `_refresh_source_version()`'s
            # question and a job of ours must not answer it. All three dead
            # greys "Server build ▾" itself (`_set_server_build_button`, T89).
            self.update_to_latest_action.setEnabled(False)
            self.return_to_pin_action.setEnabled(False)
            # And the updates press, for the importer's reason above rather than
            # for symmetry: it reaches the same `import` stage against the same
            # databases, so one while another action is live is two writers.
            self.updates_button.setEnabled(False)
            # And the adopt press, which starts the database and writes a row
            # into it -- the same rule again, one size smaller.
            self.adopt_button.setEnabled(False)
            # Refresh too, and this one is not symmetry. `recheck()` blanks
            # `problem_label` — which during an import is the live output the
            # user is watching — and then fires `Controller.import_state()`,
            # three `docker exec ... mysql` probes, at the database the import
            # is writing schemas into. Worse, the armed paragraph teaches
            # "press Refresh now", so it is the button a hesitating user
            # reaches for (review, 2026-08-23).
            self.refresh_button.setEnabled(False)
            # The Modules tab's importer too, and for the reason above rather
            # than for symmetry: `repair_import()` and `apply_module_sql()` run
            # the SAME one-shot service against the same databases, so one
            # while the other is live is two importers writing at once.
            self.module_sql_button.setEnabled(False)
            # And the update check, which writes nothing but does a network
            # round trip per installed module: two of those in flight at once
            # would fetch the same clones twice and print one over the other.
            self.module_updates_button.setEnabled(False)
            # And the two custom-module buttons. A clone or a copy writes into
            # `modules/`, which is the directory a rebuild is reading while it
            # compiles -- so this is the same rule as the importer's, not
            # symmetry: one of these landing half-way through a build would put
            # a module into the image that no report claims is in it.
            self.module_link_button.setEnabled(False)
            self.module_folder_button.setEnabled(False)
            # And every row's own Install/Remove, which is where the two greyed
            # toolbar buttons went (T42). Same rule as the two above it: a clone
            # landing half-way through a build puts a module into the image no
            # report claims is in it.
            self.modules_panel.set_enabled_actions(False)
            # And the Tuning tab's saves: they write conf files an install,
            # a rebuild or an importer run is reading at the same moment.
            self.tuning_panel.set_enabled_actions(False)
            # And its action bar (T44 item 7). The two expensive ones stop and
            # start the very containers an install, a rebuild or an importer
            # run is using; the two cheap ones redraw the cards under a save
            # that is still in flight.
            self.tuning_reload_button.setEnabled(False)
            self.tuning_revert_all_button.setEnabled(False)
            self.tuning_recreate_button.setEnabled(False)
            self.tuning_restart_button.setEnabled(False)
            self.tuning_banner_button.setEnabled(False)
            # T94: a reset writes the same confs, and its undo puts them back.
            self.tuning_reset_button.setEnabled(False)
            self.compose_banner_button.setEnabled(False)
            # T99: the bot count is one of those confs (or the compose override
            # a recreate is reading), and its owed-job button is the banner's.
            self._set_bot_count_controls()
            self.bot_count_owed_button.setEnabled(False)
            # T171: the zone is the compose override a recreate is reading.
            self._set_time_zone_controls()
            # T162: the dashboard's rebuild restarts the world like the rest.
            if self.dashboard_log is not None:
                self.rebuild_dashboard_button.setEnabled(False)
        else:
            self.compose_banner_button.setEnabled(True)
            self.refresh_button.setEnabled(True)
            self.module_updates_button.setEnabled(self.services.module_updates is not None)
            # Back to what this install can do, never unconditionally: a game
            # with no custom-module route must not be handed a live button by
            # any job of its own finishing.
            self._set_custom_module_buttons()
            # Back to what this install can do, not unconditionally: a game
            # with no import service has no route, and re-enabling it here
            # would hand the three CMaNGOS games a live button the moment any
            # action of theirs finished.
            self.module_sql_button.setEnabled(self.services.module_sql is not None)
            # Back to what this install can do, never unconditionally: a game
            # with no store or no applier must not be handed live row buttons by
            # a job of its own finishing, and neither must a row the ROW itself
            # forbids -- `RowWidget.set_enabled_actions` keeps `removable`.
            self.modules_panel.set_enabled_actions(self._module_actions_allowed())
            self.tuning_panel.set_enabled_actions(self._module_actions_allowed())
            # Back to what is OWED, never unconditionally: a job of its own
            # finishing must not hand this tab a live "Restart server…" over a
            # change nobody made. `_refresh_tuning_owed()` is the one place
            # that rule lives, so it is what unlocks them.
            self.tuning_reload_button.setEnabled(True)
            self.tuning_banner_button.setEnabled(True)
            # Back to whether this tab HAS a route, never unconditionally.
            self._set_reset_button()
            self._set_bot_count_controls()
            self._set_time_zone_controls()
            self._set_tuning_revert_all()
            self._refresh_tuning_owed()
            # Re-enabled, not re-shown: `_show_repair()` owns whether Repair is
            # visible at all, and an invisible button being enabled is harmless.
            self.remove_button.setEnabled(True)
            self.repair_button.setEnabled(True)
            self.reinstall_docker_button.setEnabled(True)
            if self.uninstall_button is not None:
                self.uninstall_button.setEnabled(True)
            if self.forget_install_button is not None:
                self.forget_install_button.setEnabled(True)
            self.uninstall_confirm_button.setEnabled(True)
            self.keep_characters_check.setEnabled(True)
            if self.set_client_dir_button is not None:
                self.set_client_dir_button.setEnabled(True)
            if self.forget_client_dir_button is not None:
                self.forget_client_dir_button.setEnabled(True)
            if self.play_button is not None:
                self.play_button.setEnabled(True)
            if self.play_menu_button is not None:
                self.play_menu_button.setEnabled(True)
            self.delete_play_client_check.setEnabled(True)
            self.rebuild_action.setEnabled(self.services.rebuild is not None)
            # Back to what this install can do, never unconditionally, and then
            # the version line is re-read: the job that just finished may BE the
            # press that moved this server off its pins, so the line and the
            # "Return to the tested pin…" button beside it are both stale until
            # this runs.
            self._set_update_buttons()
            self._refresh_source_version()
            # And the count, which the update route drops when it moves a
            # source: re-asked here so the line does not outlive the press.
            self._refresh_upstream_news()
            # Back to what this install can do, never unconditionally: three of
            # the four games have no such phase and must not be handed a live
            # button by any job of their own finishing.
            self.updates_button.setEnabled(self.services.updates is not None)
            # Back to what this install can do AND what its databases last said,
            # which is why this one goes through the rule rather than repeating
            # half of it: a job of ours finishing must not hand back a control
            # that the reading never armed.
            self._set_adopt_button()
            # T162: back to what the dashboard's files last said, never unconditionally.
            if self.dashboard_log is not None:
                self._set_rebuild_dashboard_button()
        # T187: the launcher window greys its settings while this tab is busy.
        self.play_state_changed.emit()
        if not busy and self._badge_held is not None:
            # T188: the job that held the badge is over; the reading that ends
            # the hold is asked for here, because not every way out of a job
            # asks for one (a Start refused over a port does not).
            if self._hold_ends_at is None:
                self._hold_ends_at = self._status_asks + 1
            self.refresh_status()

    def _ends_the_hold(self) -> bool:
        """Whether the status read answering now was asked once the holding job had ended."""
        return self._hold_ends_at is not None and self._status_ask_out >= self._hold_ends_at

    @Slot()
    def start_server(self) -> None:
        """Start the install; a README §12 conflict is shown, never a raw Docker error."""
        self._disarm_actions()
        self._nothing_to_remove = False
        self._update_forget_visibility()
        self.problem_label.setText("")
        self._set_busy(True)
        self.status_label.setText("status: starting…")
        self._hold_badge("starting")
        self._run(self.services.controller.start, self._server_action_done, self._start_failed)

    @Slot()
    def stop_server(self) -> None:
        self._disarm_actions()
        self.problem_label.setText("")
        self._stop_forced = ""
        self._set_busy(True)
        self.status_label.setText("status: stopping…")
        self._hold_badge("stopping")
        self._run(self.services.controller.stop, self._stop_done, self._stop_failed)

    @Slot(object)
    def _server_action_done(self, _result: object) -> None:
        self._set_busy(False)
        self._say_zone_problem()
        self.refresh_status()
        self._settle_the_channel()
        if self.play_label.text() == PLAY_START_FAILED:
            # A later Start (or "Stop the other server and start this one") worked.
            self._say_play("")
        if self._play_after_start:
            # T181: Play asked for this Start ("Start and play"); it is done.
            self._play_after_start = False
            self._play_check_stale()

    def _say_zone_problem(self) -> str | None:
        """T171: what the last Start could not put right about the zone file, on the Server tab.

        `Controller.start()` is the one door every Start, Restart and recreate
        goes through, and it never refuses over the zone file: the server runs
        on UTC, and this line says so and names the presses that fix it.
        """
        said = getattr(self.services.controller, "zone_problem", None)
        if isinstance(said, str) and said:
            text = f"The server started, but {said}"
            self.problem_label.setText(text)
            return text
        return None

    def _check_the_channel(self) -> None:
        """Ask whether the saved credential still works, off the GUI thread.

        `check()` and not `settle()`: settle creates an account on an install
        that has none, and opening a tab is not permission to write a row into
        the user's auth database. `check()` asks nothing at all unless there is
        a saved credential or an account an earlier run created and did not
        prove (T138) to ask about, and it never creates one.
        """
        setup = self.services.channel_setup
        if setup is None:
            return
        # T133: a SOAP channel that is not answered asks the world's container
        # why, through the distro's docker.
        if self._waits_for_the_distro("channel", self._check_the_channel):
            return
        self._run(setup.check, self._channel_settled, self._channel_settle_failed)

    def _settle_the_channel(self) -> None:
        """After a start, ask the channel where it now stands.

        Run through the job runner and never on the GUI thread: `settle()`
        creates a database row and makes a SOAP round trip, and a world that is
        still loading answers slowly by design -- doing it here would freeze the
        window for as long as the server takes.

        Failures are silent by design. This is not something the user asked
        for; the channel's own line already says where the setup has got to,
        and a red paragraph about it would land on top of whatever the Start
        was actually telling them.
        """
        setup = self.services.channel_setup
        if setup is None:
            return
        self._run(setup.settle, self._channel_settled, self._channel_settle_failed)

    def settle_channel_after_install(self) -> None:
        """A fresh install is a press that already wrote the database: settle now.

        Opening a tab only `check()`s (see `_check_the_channel`), because a tab
        is not permission to write a row. An install is: it just created the
        databases, the accounts and the server folder, and on the `enable_conf`
        trees it now writes the channel keys too (T87), so the world that comes
        up at the end of it is listening. Without this, the first `settle()`
        waited for the next Start press, and every tab that speaks to the world
        said "could not ask" until then.

        Asked twice, once now and once after `_POST_INSTALL_RESETTLE_MS`, only
        if the first answer was still `Pending`: a world that has just said
        "up" spends its first half minute logging bots in (500 of them on
        Tortoise) and answers SOAP slowly, so a single ask at that moment can
        time out. `Pending` keeps the minted password in memory, and `settle()`
        on it re-verifies rather than re-creates.
        """
        self._settle_the_channel()
        QTimer.singleShot(_POST_INSTALL_RESETTLE_MS, self, self._resettle_if_pending)

    def _resettle_if_pending(self) -> None:
        # A minute is long enough for the tab to have been torn down (install,
        # then uninstall): `shutdown()` sets `_closed`, and a job started after
        # it would connect its `done` to a slot of a deleted widget. Both arms
        # name `self` as the timer's context object (T213), so a tab deleted
        # without `shutdown()` never gets here at all. `settle()`
        # on `Pending` re-verifies and never creates, which is why the tab-open
        # path may schedule this too (T138).
        if getattr(self, "_closed", False):
            return
        setup = self.services.channel_setup
        if setup is None or not isinstance(setup.setup_state(), channel_setup.Pending):
            return
        # T133: scheduled by the tab opening, not by a press, and a settle that
        # is not answered asks the world's container why, in the distro.
        if self._waits_for_the_distro("channel resettle", self._resettle_if_pending):
            return
        self._run(
            self._world_reading(setup.settle), self._channel_resettled, self._channel_settle_failed
        )

    @Slot(object)
    def _channel_settled(self, state: object) -> None:
        self._show_channel(state)

    @Slot(object)
    def _channel_resettled(self, state: object) -> None:
        """T138's later settle; None is a distro that stopped since the last poll (T133)."""
        if state is not None:
            self._show_channel(state)

    @Slot(object)
    def _channel_settle_failed(self, exc: object) -> None:
        logger.info(f"the command channel could not be settled: {exc}")

    @Slot(object)
    def _stop_done(self, result: object) -> None:
        """Say so when the Stop found nothing to stop.

        `stop_staged()` distinguishes "this was running and is now down" from
        "there was nothing of it running"; the caller discarded that, so the
        button did the same thing either way and the tab could not tell the user
        which had happened (review, 2026-08-22).
        """
        self._set_busy(False)
        said = (
            "None of this install's servers were running."
            if result is False
            else self._where_the_log_went()
        )
        # Written every time, never left alone: a stop that waited for a load
        # (T158) left "stopping it now" in this label, which is false once the
        # stop is over. Only the forced-stop warning is carried past it.
        self.problem_label.setText(self._after_the_stop(said))
        self.refresh_status()
        self.refresh_verdict()

    def _where_the_log_went(self) -> str:
        """Name the file the pre-stop snapshot wrote, say why there is none, or say nothing.

        Read after the stop job has finished, so the value was written on the
        worker thread and is read on the GUI thread with the job's completion
        between them. Nothing here touches a widget from the worker.
        """
        recorder = self.services.log_snapshot
        snapshot = getattr(recorder, "last", None) if recorder is not None else None
        if snapshot is None:
            return ""
        if snapshot.path is not None:
            return f"The server's log was saved to {snapshot.path}"
        if snapshot.problem:
            return f"The server stopped. Its log was not saved: {snapshot.problem}"
        return ""

    @Slot(str)
    def _stop_notice(self, text: str) -> None:
        """Show what a running stop is waiting for (T158). Reached only through `_stop_relay`.

        The problem label, as the import's progress and the stop before a
        removal use it: the status line does not wrap, and "the world is
        finishing its load before it can stop" needs its second sentence.
        """
        self.problem_label.setText(text)
        self._stop_words_shown = True
        self._heard_from_a_stop(text)

    @Slot(str)
    def _uninstall_stop_notice(self, text: str) -> None:
        """The same, for the uninstall's own removal of the containers, in its own label."""
        self.uninstall_label.setText(text)
        self._heard_from_a_stop(text)

    def _heard_from_a_stop(self, text: str) -> None:
        """Offer "Stop now anyway" while a stop waits; remember a warning that must stay."""
        if text in docker.LOAD_WAIT_LINES:
            self.stop_anyway_button.setEnabled(True)
            self.stop_anyway_button.setVisible(True)
        else:
            self.stop_anyway_button.setVisible(False)
        if text in docker.FORCE_STOP_WARNINGS:
            self._stop_forced = text

    @Slot()
    def stop_now_anyway(self) -> None:
        """End a load wait and send the stop now, on the person's word (T158).

        Only sets the event: the waiting stop reads it at its next look (it
        wakes for it at once) and says what it is doing, warning included.
        """
        self.stop_anyway_button.setEnabled(False)
        self._stop_anyway.set()

    def _after_the_stop(self, said: str) -> str:
        """`said`, under the forced-stop warning if this stop's load wait ran out (T158)."""
        forced, self._stop_forced = self._stop_forced, ""
        return "\n\n".join(part for part in (forced, said) if part)

    @Slot(object)
    def _start_failed(self, exc: object) -> None:
        self._set_busy(False)
        if self._play_after_start:
            self._play_after_start = False
            self._play_end(PLAY_START_FAILED)
        if isinstance(exc, PortConflictError):
            self._offer_to_stop_the_other_server(exc)
            return
        self._hide_stop_other()
        msg = str(exc)
        if isinstance(exc, docker.DockerCliMissingError) and self._offer_docker_repair():
            # T160. The missing-CLI sentence says to install Docker Desktop or
            # Docker Engine, which on a Deck after a SteamOS update is the wrong
            # errand: the button below does it, the way the fix script did.
            # (Not named by its constant here: `test_platform` counts the
            # modules that name it as the modules that RAISE it.)
            msg = platform.STEAMOS_DOCKER_GONE_HELP
        rolled = self._roll_the_channel_back_if_it_took_the_port(msg)
        self.problem_label.setText(rolled or msg)
        self.action_failed.emit(rolled or msg)
        self.refresh_status()

    def _roll_the_channel_back_if_it_took_the_port(self, message: str) -> str:
        """Undo the press when the start failed on the port the channel claims.

        Docker refuses to publish a host port something else already holds, so
        the container is never created and no setting the press wrote is ever
        read. Rolling back is what makes the next Start work; saying so is what
        stops the user pressing enable again into the same wall.

        Returns the sentence to show, or "" when this failure was about
        something else -- a start that failed for another reason must never
        quietly switch the channel off.
        """
        setup = self.services.channel_setup
        operations = self.entry.operations
        # `port is None` is an attach channel, which has no listener and so no
        # host port for a failed start to be about (8.2e).
        if setup is None or operations is None or operations.port is None:
            return ""
        if not channel_setup.blames_the_host_port(message, operations.port):
            return ""
        try:
            undone = setup.roll_back()
        except Exception as exc:  # noqa: BLE001 - the rollback is best effort
            return (
                f"The server could not start: port {operations.port} on this machine is in use "
                f"by something else, and the command channel could not be undone: {exc}"
            )
        if not undone:
            return (
                f"The server could not start: port {operations.port} on this machine is in use "
                "by something else. Free it, or stop whatever holds it, and start again."
            )
        # The press is undone, and the sentence below asks for it again (T188 A4).
        self._channel_written = False
        self.refresh_channel()
        return (
            f"The server could not start: port {operations.port} on this machine is in use by "
            "something else. The command channel has been turned off again and the port given "
            "back, so the server will start. Free that port and turn the channel on again."
        )

    def _offer_to_stop_the_other_server(self, exc: PortConflictError) -> None:
        """Name the install holding the ports, and offer to stop it.

        This used to end at "Stop it first", which is true and leaves the user to
        work out WHICH install that is and go and do it. `PortConflictError` now
        carries the compose `working_dir` label of each blocking container, so
        the offer can name the folder, and the button does the stopping.

        The offer is a control on the tab rather than a modal dialog, in this
        view's own idiom: what is being agreed to stays readable while agreeing,
        and a test can press it.
        """
        ports = ", ".join(str(port) for port in exc.ports)
        names = ", ".join(exc.containers)
        msg = (
            f"This server needs port(s) {ports}, which {exc.owner_summary()} is "
            f"using ({names}). Only one server can run at a time."
        )
        self.problem_label.setText(msg)
        self.stop_other_button.setVisible(True)
        self.stop_other_button.setEnabled(True)
        self.action_failed.emit(msg)
        self.refresh_status()

    def _hide_stop_other(self) -> None:
        """The offer only stands while the collision does."""
        self.stop_other_button.setVisible(False)

    def _offer_docker_repair(self) -> bool:
        """Show the SteamOS Docker repair when this Deck's `docker` is gone; say if it is (T160).

        Asked on the two paths that meet a missing Docker: a status poll that
        could not ask it, and a Start that had no CLI to run. Stateless, as
        `platform.steamos_docker_removed()` explains, and cheap enough for the
        GUI thread. Withdrawn by the first poll Docker answers.

        Greyed while a repair runs from ANOTHER tab: two servers on one Deck
        are two buttons, and the process allows one repair at a time
        (`platform.steamos_docker_repair_running()`). This tab's own running
        repair keeps it live, because then it is the Stop.
        """
        offered = platform.steamos_docker_removed()
        self.reinstall_docker_button.setVisible(offered)
        if self._docker_repair_cancel is None:
            self.reinstall_docker_button.setEnabled(
                not self._busy and not platform.steamos_docker_repair_running()
            )
        return offered

    @Slot()
    def reinstall_docker(self) -> None:
        """Run the SteamOS Docker repair off the GUI thread, or stop the one running (T160).

        While it runs the button is the way out: a question left open, or a
        `passwd` waiting on something nobody recognised, would otherwise hold
        the tab until its own deadline. The press sets the job's cancel, which
        the repair reads between steps, the prompter reads while it waits, and
        `set_own_password()` reads while `passwd` runs.
        """
        if self._docker_repair_cancel is not None:
            self._docker_repair_cancel.set()
            self.reinstall_docker_button.setEnabled(False)
            self.problem_label.setText(STOPPING_DOCKER_REINSTALL)
            return
        if platform.steamos_docker_repair_running():
            self.problem_label.setText(platform.STEAMOS_DOCKER_REPAIR_BUSY)
            self.reinstall_docker_button.setEnabled(False)
            return
        cancel = threading.Event()
        self._docker_repair_cancel = cancel
        self._disarm_actions()
        self._set_busy(True)
        self.reinstall_docker_button.setText(STOP_DOCKER_REINSTALL)
        self.reinstall_docker_button.setEnabled(True)
        self.problem_label.setText(
            "Reinstalling Docker. Answer the questions as they come; this can take a few minutes."
        )
        if self._docker_prompter is None:
            self._docker_prompter = InputPrompter(self, title=DOCKER_REINSTALL_PROMPT_TITLE)
        self._docker_prompter.bind_cancel(cancel)
        ask = self._docker_prompter.ask
        self._run(
            lambda: platform.repair_docker_after_steamos_update(ask=ask, cancel=cancel),
            self._docker_reinstalled,
            self._docker_reinstall_failed,
        )

    def _end_docker_reinstall(self) -> None:
        """The button is the reinstall again, and this tab's busy lock is lifted."""
        self._docker_repair_cancel = None
        self.reinstall_docker_button.setText(platform.STEAMOS_DOCKER_REPAIR_LABEL)
        self._set_busy(False)

    @Slot(object)
    def _docker_reinstalled(self, result: object) -> None:
        """Say what the repair did, and offer the restart that picks up the group."""
        self._end_docker_reinstall()
        if not isinstance(result, platform.ProvisionReport):
            return
        self.problem_label.setText("\n".join(result.manual_steps))
        if result.docker_ready:
            self.reinstall_docker_button.setVisible(False)
        elif platform.STEAMOS_DOCKER_SESSION_STEP in result.manual_steps:
            self._restart_for_the_group()
        self.refresh_status()

    def _restart_for_the_group(self) -> None:
        """Offer the `sg docker` restart when the daemon runs but this process cannot reach it.

        The same offer an install makes after it joined the group, through the
        same function (`catalog_view.offer_a_docker_group_restart()`), because
        supplementary groups are fixed at process start and the restart has to
        hand the single-instance lock over around its exec (T152).
        """
        offer_a_docker_group_restart(
            self, self.problem_label.text(), failed_title="Reinstalling Docker"
        )

    @Slot(object)
    def _docker_reinstall_failed(self, exc: object) -> None:
        self._end_docker_reinstall()
        self.problem_label.setText(f"Reinstalling Docker stopped: {exc}")
        self.refresh_status()

    @Slot()
    def stop_other_and_start(self) -> None:
        """Stop whatever holds our ports, then start this install.

        One job, not two: a stop that succeeded followed by a start that was
        never issued is the failure mode this replaces, and the two halves are
        only meaningful together.
        """
        self._disarm_actions()
        self._hide_stop_other()
        self.problem_label.setText("")
        self._set_busy(True)
        self.status_label.setText("status: stopping the other server…")
        self._hold_badge("starting")
        self._run(
            self.services.controller.stop_conflicting_and_start,
            self._server_action_done,
            self._start_failed,
        )

    @Slot(object)
    def _stop_failed(self, exc: object) -> None:
        """Show why the stop refused. This used to emit into a signal nothing read.

        `stop_staged()` refuses rather than guess when it cannot prove it owns
        the containers, and says which project does own them and how to make the
        two agree. All of that was discarded: the status went "stopping…" and
        then straight back to "db up, auth up, world up", which is exactly what
        the silent bug it replaced looked like (review, 2026-08-22).
        """
        self._set_busy(False)
        msg = str(exc)
        self.problem_label.setText(msg)
        self.action_failed.emit(msg)
        self.refresh_status()

    def stop_for_removal(self) -> None:
        """Stop this server on the job runner because it is about to leave Yu'lon's list (T95).

        This is `stop_server()`'s path: the same `controller.stop` (which saves
        the pre-stop log snapshot) and the same busy lock. Its outcome goes to
        the window instead of only to this tab, because the window decides
        what happens next (forget, or ask again) and may drop this tab.
        """
        self._disarm_actions()
        self._set_busy(True)
        self.status_label.setText(STOPPING_FOR_REMOVAL)
        self.problem_label.setText(STOPPING_FOR_REMOVAL_WAIT)
        self._hold_badge("stopping")
        self._run(
            self.services.controller.stop,
            self._stopped_for_removal,
            self._stop_for_removal_failed,
        )

    @Slot(object)
    def _stopped_for_removal(self, _result: object) -> None:
        self._set_busy(False)
        # The window may keep this tab (a job started during the stop, or the
        # record could not be written), so the stop's words must not outlive
        # it, as `_stop_for_removal_failed()` makes sure (final review, T95).
        self.problem_label.setText("")
        self.refresh_status()
        # Last: the window drops this tab on this signal. Nothing may touch
        # `self` after it (mirrors `_uninstall_done()`).
        self.stopped_for_removal.emit(self.entry.id, self.services.controller.server_dir, True, "")

    @Slot(object)
    def _stop_for_removal_failed(self, exc: object) -> None:
        """Say why here, as `_stop_failed()` does; the window asks whether to go on anyway."""
        self._set_busy(False)
        message = str(exc)
        self.problem_label.setText(message)
        self.action_failed.emit(message)
        # As `_stop_failed()` does: the status line said "stopping…", and the
        # tab may be kept (the second question can be answered No).
        self.refresh_status()
        # Last, for the reason above.
        self.stopped_for_removal.emit(
            self.entry.id, self.services.controller.server_dir, False, message
        )

    # ------------------------------------------------------------- 8.9a
    #
    # Two presses with a plan between them. The first reads (`purge.plan()`
    # touches nothing); the second acts. What is on screen between them is the
    # whole blast radius, because a destructive action that cannot state what
    # it reaches is one a user has to guess about.

    @Slot()
    def show_uninstall_plan(self) -> None:
        """Ask what an uninstall would remove, and show it. Removes nothing."""
        if self.services.uninstall is None:
            return
        self._uninstall_plan = None
        self.uninstall_confirm_button.setVisible(False)
        self.keep_characters_check.setVisible(False)
        self._play_delete_offered = False
        self.delete_play_client_check.setVisible(False)
        self.uninstall_label.setText("Working out what would be removed\u2026")
        self._run(self.services.uninstall.plan, self._uninstall_plan_ready, self._uninstall_failed)

    @Slot(object)
    def _uninstall_plan_ready(self, result: object) -> None:
        if not isinstance(result, purge.PurgePlan):  # pragma: no cover - defensive
            return
        if result.refusal:
            # A refusal is the whole answer and there must be nothing left to
            # press: `plan.refusal` non-empty means nothing was resolved, so a
            # visible Uninstall button would be an offer the app cannot keep.
            self._uninstall_plan = None
            self.uninstall_confirm_button.setVisible(False)
            self.keep_characters_check.setVisible(False)
            self.uninstall_label.setText(result.refusal)
            return
        self._uninstall_plan = result
        self.uninstall_confirm_button.setVisible(True)
        # T188 fix round 1: a plan that found no database volume has nothing to
        # keep, so a tick left from an earlier plan must not promise it.
        if not result.character_volume:
            self.keep_characters_check.setChecked(False)
        self.keep_characters_check.setVisible(bool(result.character_volume))
        # T181 §4: offered only for a folder that still carries this server's
        # marker -- `play_client.delete()` would refuse any other, and an offer
        # the press cannot keep is worse than none.
        play = self.services.play_client_dir
        self._play_delete_offered = play is not None and self._is_this_servers(
            play_client.read_marker(play)
        )
        self.delete_play_client_check.setVisible(self._play_delete_offered)
        self._redraw_uninstall_plan()

    @Slot()
    def _redraw_uninstall_plan(self) -> None:
        """Re-render the plan for the checkbox's current state.

        The checkbox does not change what `plan()` found, only which of those
        objects survives - so toggling it re-renders rather than re-planning,
        and the user is not made to wait for Docker again to read a different
        sentence.
        """
        plan = self._uninstall_plan
        if plan is None:
            return
        keep = self.keep_characters_check.isChecked()
        # T188 A14: the press names the choice it carries out.
        self.uninstall_confirm_button.setText(
            UNINSTALL_KEEP_CHARACTERS if keep else UNINSTALL_DELETE_CHARACTERS
        )
        lines = [
            f"This removes {plan.server_dir} ({size_text(plan.folder_bytes)}) and this "
            f"server's Docker project {plan.project}:",
            f"  containers: {', '.join(plan.containers) or 'none left'}",
            f"  images: {len(plan.images)} built for this install",
        ]
        if keep and plan.character_volume:
            kept = [plan.character_volume]
            if plan.client_volume:
                kept.append(plan.client_volume)
            others = [v for v in plan.volumes if v not in kept]
            lines.append(f"  volumes removed: {', '.join(others) or 'none'}")
            lines.append(
                f"  volumes KEPT: {', '.join(kept)} \u2014 your characters"
                + (" and the extracted client data" if plan.client_volume else "")
                + ". A reinstall to THIS SAME FOLDER finds them again; a reinstall anywhere "
                "else does not."
            )
        else:
            lines.append(f"  volumes removed: {', '.join(plan.volumes) or 'none'}")
            lines.append("  your characters go with the database volume.")
        lines.append("It does not touch " + "; ".join(plan.left_behind) + ".")
        play = self.services.play_client_dir
        if self._play_delete_offered and play is not None:
            if self.delete_play_client_check.isChecked():
                lines.append(
                    f"It also deletes its ready-to-play client at {play}; your own client "
                    "keeps all its files."
                )
            else:
                lines.append(f"It leaves its ready-to-play client at {play} where it is.")
        if plan.problems:
            lines.append("Could not determine: " + "; ".join(plan.problems))
        self.uninstall_label.setText("\n".join(lines))

    @Slot()
    def run_uninstall(self) -> None:
        """Remove this install. Refuses until its plan has been shown.

        The `_uninstall_running` guard is belt to `_set_busy`'s braces, and it
        is here because disabling a button is a statement about the widget
        while this is a statement about the action: a queued click, a keyboard
        Space on a button re-enabled by some other path, or a second caller of
        this slot all reach the seam without ever touching the mouse.
        """
        if self.services.uninstall is None:
            return
        if self._uninstall_running:
            return
        if self._play_client_running:
            # T181: Make…, Refresh or Delete is writing the ready-to-play
            # client, and the uninstall may delete that very folder.
            self.uninstall_label.setText(
                PLAY_PIPELINE_RUNNING if self._play_preparing else PLAY_CLIENT_RUNNING
            )
            return
        if self._play_pending:
            # T181: a Play is on its way and still writes the realmlist into,
            # and starts the game from, the folder the uninstall may delete.
            self.uninstall_label.setText(PLAY_PENDING)
            return
        self._stop_forced = ""
        if self._uninstall_plan is None:
            self.uninstall_label.setText(UNINSTALL_NO_PLAN)
            self.action_failed.emit(UNINSTALL_NO_PLAN)
            return
        keep = self.keep_characters_check.isChecked()
        uninstall = self.services.uninstall
        play = self.services.play_client_dir
        delete_play = (
            play
            if self._play_delete_offered and self.delete_play_client_check.isChecked()
            else None
        )
        game, server_dir = self.entry.id, self.services.controller.server_dir

        def work() -> _UninstallOutcome:
            report = uninstall.run(keep_characters=keep)
            # After the server is gone, never before: a removal that failed
            # keeps the tab, and the tab still plays from this folder.
            said = (
                _delete_with_the_server(delete_play, game=game, server_dir=server_dir)
                if delete_play is not None
                else None
            )
            return _UninstallOutcome(report, said)

        self._uninstall_running = True
        self._set_busy(True)
        self.uninstall_label.setText("Uninstalling\u2026")
        self._run(work, self._uninstall_done, self._uninstall_failed)

    @Slot(object)
    def _uninstall_done(self, result: object) -> None:
        self._uninstall_running = False
        self._set_busy(False)
        self._uninstall_plan = None
        self.uninstall_confirm_button.setVisible(False)
        self.keep_characters_check.setVisible(False)
        play_said: str | None = None
        if isinstance(result, _UninstallOutcome):
            report, play_said = result.report, result.play_client
        elif isinstance(result, purge.PurgeReport):
            report = result
        else:
            report = purge.PurgeReport()
        # T158: a load wait's forced-stop warning goes first, as on a Stop.
        forced, self._stop_forced = self._stop_forced, ""
        said = [part for part in (forced,) if part]
        said.append(f"{self.services.controller.server_dir} was removed.")
        if report.kept_volumes:
            said.append(
                f"Kept {', '.join(report.kept_volumes)} \u2014 reinstall to the same folder to "
                f"find those characters again."
            )
        if report.secret_kept is not None:
            # Only on a `generated` entry, where the password that opens the
            # kept volume was inside the folder that has just been deleted. The
            # sentence above promises the characters come back; this one names
            # the file that promise now rests on, so a user who moves their
            # config directory knows what has to travel with it.
            said.append(
                f"Its database password was kept at {report.secret_kept}, because the folder "
                f"holding it is gone — the reinstall reads it from there."
            )
        if report.snapshot.path is not None:
            said.append(f"The server's last log was saved to {report.snapshot.path}.")
        elif report.snapshot.problem:
            said.append(f"No log could be saved: {report.snapshot.problem}")
        said.extend(report.warnings)
        if play_said is not None:
            said.append(play_said)
        self.uninstall_label.setText(" ".join(said))
        # Last, and only on success: the window drops this tab on this signal,
        # which destroys the view. Nothing may touch `self` after it.
        self.uninstalled.emit(self.entry.id, self.services.controller.server_dir)

    @Slot(object)
    def _uninstall_failed(self, exc: object) -> None:
        """Say why, and make the next press ask for a fresh plan.

        No `uninstalled` signal: the tab is the only surface that can try again,
        and dropping it here would leave a half-removed install with nothing
        pointing at it.
        """
        self._uninstall_running = False
        self._set_busy(False)
        self._uninstall_plan = None
        self.uninstall_confirm_button.setVisible(False)
        self.keep_characters_check.setVisible(False)
        message = str(exc)
        self.uninstall_label.setText(message)
        self.action_failed.emit(message)

    @Slot()
    def forget_install(self) -> None:
        """The Server tab's "Remove from Yu'lon…" press: hand it to the window (T95).

        Until T95 this opened its own dialog and forgot through
        `services.uninstall.forget` (T34). That seam exists only on the two
        families with an Uninstall, so a TBC or Tortoise tab had no way off the
        list. The window's one path now does the work, the same path the tab's
        × and its right-click entry take: refuse while busy, ask once, stop a
        running server first, forget, drop the tab.

        Nothing may touch `self` after the emit: the window may drop this tab on it.
        """
        self.remove_requested.emit(self.entry.id, self.services.controller.server_dir)

    def _client_dir_refused(self, message: str) -> None:
        """One place both refusal paths in `change_client_dir()` report through."""
        self.action_failed.emit(message)
        QMessageBox.warning(self, f"{self.entry.name}", message)

    def _client_dir_busy(self) -> bool:
        """The round-2 review's guard, in `rebuild_server()`'s own words and shape.

        A write here does two things a running action must not race: it
        replaces `state.json`'s record, and it makes `main.py` drop this tab
        and rebuild it. `_set_busy()` locks the buttons; this is the second
        half a disabled `QPushButton` does not give for free -- a press
        already queued in Qt's event loop, or one this method is called from
        directly in a test, still has to be told no.

        Make… counts too (T181): from its press it plans, asks and builds from
        the client folder this press would change, and the tab rebuild this
        press ends in would cut it off.
        """
        if not self._busy and not self._play_client_running:
            return False
        QMessageBox.information(
            self,
            "Something else is running",
            "This server is busy with another action — wait for it to finish on the "
            "Server tab, then press this again. Nothing was changed.",
        )
        return True

    @Slot()
    def change_client_dir(self) -> None:
        """Set, change or leave this install's client folder (T36).

        Validated through `families/clientdir.py` -- the same rules the
        Install preflight applies -- for a game whose catalog entry carries a
        `ClientSpec`; the entry's own `client` block, obtained the way
        `preflight.gather()` does (`preflight.client_spec_for()`), so this
        press and a fresh install cannot disagree about what a client folder
        has to look like. WotLK's entry carries none -- AzerothCore extracts
        nothing from a client, so nothing has ever validated its folder -- and
        a minimal rule stands in for it (round 2 review): the folder must
        exist and hold a `Data/` directory, the one thing every WoW client
        ships regardless of expansion. The ONE further question, whether it
        has an `Interface/` to write an addon into, is the row's own and not
        this press's (`_client_dir_row_text()`).

        Nothing is written on a refusal. A warning is put to the user as
        "Use it anyway?" and answered through `said_yes()` (T33) rather than
        blocking, because every remaining warning here (too few but not zero
        MPQs, no locale archives, the repack smell, low free space) is
        `families/clientdir.py`'s own tri-state discipline saying extraction
        usually still works. Zero archives is pulled out of that and refused
        outright (round 2 review): an empty `Data/` is not "usually still
        works", it is the folder holding nothing to extract from at all.

        Round 2 review's third rule, ahead of every other check: the server
        folder, or anything inside it, is refused before `clientdir.validate()`
        is ever asked -- Uninstall deletes that whole tree, and a client
        folder living there would be removed out from under the game the
        first time this install is uninstalled.
        """
        if self.services.set_client_dir is None:
            return
        if self._client_dir_busy():
            return
        chosen = self._pick_client_dir(
            self,
            f"Select your {self.entry.client.version} client folder",
            self.services.client_dir,
        )
        if chosen is None:
            return
        server_dir = self.services.controller.server_dir
        if chosen.resolve().is_relative_to(server_dir.resolve()):
            self._client_dir_refused(
                f"The client folder cannot be the server folder or inside it ({server_dir}): "
                "Uninstall removes that whole tree."
            )
            return
        spec = preflight.client_spec_for(self.entry)
        if spec is not None:
            checks = clientdir.validate(chosen, spec, free_bytes=preflight.free_bytes)
            report = preflight.Report(checks=checks)
            if not report.ok():
                self._client_dir_refused(report.message())
                return
            mpq_check = next((c for c in checks if c.name == clientdir.MPQ_CHECK), None)
            if mpq_check is not None and _mpq_archive_count(mpq_check) == 0:
                self._client_dir_refused(_check_sentence(mpq_check))
                return
            warnings = report.warnings()
            if warnings:
                text = "\n".join(_check_sentence(c) for c in warnings)
                answer = QMessageBox.question(
                    self,
                    "Use this client folder anyway?",
                    f"Use it anyway? {text}",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if not said_yes(answer):
                    return
        elif not (chosen / clientdir.DATA_DIR).is_dir():
            self._client_dir_refused(
                f"{chosen} has no {clientdir.DATA_DIR}/ folder, so it is not a WoW client. "
                "Nothing was changed."
            )
            return
        try:
            self.services.set_client_dir(chosen)
        except OSError as exc:
            self._client_dir_refused(f"Could not set the client folder: {exc}")
            return
        # Last, mirroring `forget_install()`: the window rebuilds this tab on
        # this signal, which destroys the view.
        self.client_dir_changed.emit(self.entry.id, self.services.controller.server_dir, chosen)

    @Slot()
    def forget_client_dir(self) -> None:
        """Clear this install's recorded client folder (T36).

        No confirmation: unlike "Remove from Yu'lon…" this drops no tab and
        touches no Docker object, and the folder is one press away from being
        set again -- the cost of a mistaken press is one more press, not a
        server nobody can get back.
        """
        if self.services.set_client_dir is None:
            return
        if self._client_dir_busy():
            return
        try:
            self.services.set_client_dir(None)
        except OSError as exc:
            self._client_dir_refused(f"Could not forget the client folder: {exc}")
            return
        self.client_dir_changed.emit(self.entry.id, self.services.controller.server_dir, None)

    # ------------------------------------------- the ready-to-play client (T181)
    #
    # Make…, Play, Refresh and Delete. Everything that reads or writes the folder
    # runs through `_run()`: a build is gigabytes when it is a full copy, and
    # even a listing of a client walks thousands of files.
    #
    # Two flags, and why two. `_play_client_running` is held by Make… from its
    # press to its end (the plan, the dialog, the build, the removals), and by
    # Refresh and Delete: it holds the close (`busy_reason()`) and refuses a
    # second of them. `_set_busy()` is taken only around the parts that WRITE
    # (`_hold_busy()`), and given back only by whoever took it
    # (`_release_play_client()`): a Start pressed while Make… was still planning
    # owns the lock, and unlocking it from here was `_set_busy`'s recorded
    # double-unlock defect.

    def _build_play_controls(self, tab: QWidget) -> None:
        """The Play button, its ▾ menu and its label (T181).

        Built only where `main.py` hands down the write seam, the client-folder
        row's rule: Make… would make a folder nothing could record. Hidden while
        the install has neither its own client folder (nothing to make one
        from; the row's "Set client folder…" says so) nor a ready-to-play client.
        """
        self._play_holds_busy = False
        self._play_pending = False
        self._play_left_out: tuple[Path, ...] = ()
        self._play_notes: tuple[str, ...] = ()
        self._skipped: frozenset[str] = frozenset()
        self._play_preparing = False
        # T181 b/c: set by the Cancel button, read by the download between reads.
        self._play_cancel = threading.Event()
        # Optional packs whose address answered 404 at the last Play (spec §4).
        self._client_unavailable: frozenset[str] = frozenset()
        # The download's lines come from the worker through a signal, never a call.
        self._play_relay = LineRelay(self)
        self._play_relay.line.connect(self._play_progress)
        self.play_cancel_button = QPushButton("Cancel", tab)
        self.play_cancel_button.setToolTip(
            "Stop getting the client ready. A download stops between reads, so a stalled one "
            "can take a moment; what arrived is kept for next time."
        )
        self.play_cancel_button.setVisible(False)
        self.play_cancel_button.clicked.connect(self._cancel_play_download)
        self._made: _MadePlayClient | None = None
        self.play_button: QPushButton | None = None
        self.play_menu_button: QPushButton | None = None
        self.play_menu = QMenu(tab)
        self.play_menu.setToolTipsVisible(True)
        self.client_options_action: QAction | None = None
        if has_client_choices(self.entry.client):
            self.client_options_action = self.play_menu.addAction(CLIENT_OPTIONS_LABEL)
            self.client_options_action.setToolTip(
                "Choose this server's optional client packs and Wow.exe options. Takes "
                "effect at your next Play."
            )
            self.client_options_action.triggered.connect(self.client_options)
        self.refresh_play_client_action = self.play_menu.addAction(REFRESH_PLAY_CLIENT_LABEL)
        self.refresh_play_client_action.setToolTip(
            "Bring the game files and Wow.exe back in step with your own client after a "
            "patch. Your settings (WTF) and addons (Interface) are never touched."
        )
        self.refresh_play_client_action.triggered.connect(self._from_tab(self.refresh_play_client))
        self.delete_play_client_action = self.play_menu.addAction(DELETE_PLAY_CLIENT_LABEL)
        self.delete_play_client_action.setToolTip(
            "Delete the ready-to-play client. Your own client keeps all its files. Asks first."
        )
        self.delete_play_client_action.triggered.connect(self._from_tab(self.delete_play_client))
        self.play_label = QLabel("", tab)
        self.play_label.setWordWrap(True)
        self.play_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.play_label.setVisible(False)
        if self.services.set_play_client_dir is None:
            return
        has_play = self.services.play_client_dir is not None
        self.play_button = QPushButton(PLAY_LABEL if has_play else MAKE_PLAY_CLIENT_LABEL, tab)
        if has_play:
            self.play_button.setIcon(dadcraft_icon("play", COLOR_GOLD_LIGHT, 14))
            self.play_button.setToolTip(
                "Start World of Warcraft from this server's ready-to-play client, pointed "
                "at this server. Starts the server first if it is stopped (asks)."
            )
        else:
            self.play_button.setToolTip(
                "Make a copy of your client set up for this server, without changing your "
                "own client. Game files are shared, so it takes little extra space."
            )
        self.play_button.clicked.connect(self._play_pressed)
        self.play_button.setVisible(has_play or self.services.client_dir is not None)
        self.play_menu_button = QPushButton("▾", tab)
        # Its label is the arrow: the style's own indicator would be a second (T187).
        self.play_menu_button.setObjectName(PLAY_MENU_BUTTON)
        self.play_menu_button.setToolTip("More for the ready-to-play client")
        self.play_menu_button.setMenu(self.play_menu)
        self.play_menu_button.setVisible(has_play)
        self.play_label.setVisible(True)

    def _say_play(self, text: str) -> None:
        self.play_label.setText(text)
        self.play_state_changed.emit()

    def _from_tab(self, press: Callable[[], None]) -> Callable[..., None]:
        """`press` as this tab's own button makes it: its dialogs open over the tab (T187)."""

        def pressed(*_args: object) -> None:
            self.dialog_host = None
            press()

        return pressed

    @Slot()
    def _play_pressed(self) -> None:
        """The tab's Play opens the server's launcher window (T187); its Make… stays a Make….

        Without a ready-to-play client the button reads "Make a ready-to-play
        client…" and does exactly that. With one, PLAY is in the launcher, which
        is where the realm address, the account and the display are chosen.
        """
        self.dialog_host = None
        if self.services.play_client_dir is not None and self.open_launcher is not None:
            self.open_launcher()
            return
        self.play()

    def _play_parent(self) -> QWidget:
        """Where a Make…/Play/Refresh/Delete dialog opens: the launcher that pressed, or this tab.

        Only a launcher still on screen: one closed or minimized since its press
        has nobody looking at it, and a dialog over it is a dialog nobody sees.
        """
        host = self.dialog_host
        if (
            host is not None
            and shiboken6.isValid(host)
            and host.isVisible()
            and not host.isMinimized()
        ):
            return host
        return self

    @Slot(str)
    def _play_progress(self, text: str) -> None:
        """A line from the Play pipeline's worker (through `_play_relay`), on the GUI thread."""
        self._say_play(text)

    def _play_refused(self, message: str) -> None:
        """A Play-side refusal, on the label, in the log and in front of the player."""
        self._say_play(message)
        self.action_failed.emit(message)
        QMessageBox.warning(self._play_parent(), self.entry.name, message)

    def _play_client_refusal(self) -> str | None:
        """Why Make…, Play, Refresh or Delete may not start now, or None.

        A module job first: it never takes `_set_busy()` (`_module_pending`
        alone, as `forget_refusal()` reads it), and it may be writing into the
        very folder Delete would remove, or racing the tab rebuild Make… and
        Delete end with. Then any Server action, and another of these four.
        """
        if self._module_pending is not None:
            return (
                f"“{self._module_pending}” is running on this server's Modules tab. "
                "Wait for it to finish, then press this again. Nothing was changed."
            )
        if self._busy or self._play_client_running:
            return (
                "This server is busy with another action — wait for it to finish on the "
                "Server tab, then press this again. Nothing was changed."
            )
        return None

    def _play_waits_for_play(self) -> bool:
        """True (said) while a Play is on its way: Make…, Refresh and Delete wait for it.

        Not part of `_play_client_refusal()`: the Refresh a Play asks for itself
        ("Refresh and play") runs while `_play_pending` is set, and would refuse itself.
        """
        if not self._play_pending:
            return False
        QMessageBox.information(self._play_parent(), "Something else is running", PLAY_PENDING)
        return True

    def _play_client_blocked(self) -> bool:
        """`_play_client_refusal()` said to the player; True when there was one."""
        refusal = self._play_client_refusal()
        if refusal is None:
            return False
        QMessageBox.information(self._play_parent(), "Something else is running", refusal)
        return True

    def _hold_busy(self) -> None:
        self._play_holds_busy = True
        self._set_busy(True)

    def _release_play_client(self) -> None:
        """End a Make…, Refresh or Delete: give back the lock only if this took it."""
        self._play_client_running = False
        self._play_preparing = False
        if self._play_holds_busy:
            self._play_holds_busy = False
            self._set_busy(False)
        self.play_state_changed.emit()

    def _is_this_servers(self, marker: play_client.Marker | None) -> bool:
        return (
            marker is not None
            and marker.game == self.entry.id
            and marker.server_dir == self.services.controller.server_dir
        )

    def _usable_play_client(self, *, offer_remake: bool = True) -> play_client.Marker | None:
        """The recorded ready-to-play client's marker, or None (after offering to remake it).

        Asked before Play, Refresh and a module install touch the folder:
        deleted, moved or unmarked outside Yu'lon, it is not this server's any
        more, and writing into it would make a bare `…(Yu'lon)/Data` or change a
        folder Yu'lon cannot vouch for.
        """
        play = self.services.play_client_dir
        if play is None:
            return None
        marker = play_client.read_marker(play)
        if self._is_this_servers(marker):
            return marker
        if offer_remake:
            self._offer_remake(play)
        return None

    def _play_client_gone_text(self, play: Path) -> str:
        if not os.path.lexists(play):
            return (
                f"The ready-to-play client at {play} is gone: it was deleted or moved "
                "outside Yu'lon. Nothing was started or changed."
            )
        return (
            f"The folder at {play} is no longer this server's ready-to-play client (its "
            f"{play_client.MARKER} is missing or names another server), so Yu'lon will "
            "not use or change it. Nothing was started or changed."
        )

    def _offer_remake(self, play: Path) -> None:
        said = self._play_client_gone_text(play)
        self._say_play(said)
        answer = _ask_with(
            self._play_parent(),
            "The ready-to-play client is gone",
            f"{said}\n\nMake a ready-to-play client for this server again?",
            "Make it again",
        )
        if answer == "yes":
            self.make_play_client()

    # -- Make… -------------------------------------------------------------

    @Slot()
    def make_play_client(self) -> None:
        """Ask where and how, then build this server's ready-to-play client (T181 §1, §3)."""
        if self.services.set_play_client_dir is None:
            return
        if self._play_waits_for_play() or self._play_client_blocked():
            return
        original = self.services.client_dir
        if original is None:
            QMessageBox.information(
                self._play_parent(),
                MAKE_PLAY_CLIENT_LABEL,
                "A ready-to-play client is made from your own client folder, and none is "
                "set for this server. Press “Set client folder…” on this tab "
                "first. Nothing was created.",
            )
            return
        server_dir = self.services.controller.server_dir
        recorded = self.services.play_client_dir
        if recorded is not None and not os.path.lexists(recorded):
            target = recorded  # made again where it was
        else:
            target = play_client.default_target(original, self.entry.name, server_dir)
        addons = self._addons_in(original)
        others = self.services.other_server_dirs
        other_dirs = others() if others is not None else ()  # the live state, read here
        # Held from here, through the plan and the dialog, to the end: a second
        # Make…, a Delete or a Refresh is refused all that time, and so is the close.
        self._play_client_running = True
        self._say_play("Looking at your client…")
        self._run(
            lambda: self._plan_play_client(original, target, addons, other_dirs),
            self._play_plan_ready,
            self._play_client_job_failed,
        )

    def _addons_in(self, original: Path) -> tuple[str, ...]:
        """Addon folders this server's installed modules put into `original` (never receipted)."""
        installed = self._installed_clones() or {}
        names: list[str] = []
        for family, ids in installed.items():
            for module_id in sorted(ids):
                manifest = self._manifests.get((family, module_id))
                if manifest is None:
                    continue
                for step in manifest.client:
                    name = step.name or Path(step.src).name
                    if step.dest == "addons" and (original / "Interface/AddOns" / name).is_dir():
                        names.append(name)
        return tuple(sorted(set(names)))

    def _replan(self, original: Path, target: Path) -> play_client.BuildPlan:
        """`plan()` for a folder, after clearing this server's crashed attempt there.

        Off the GUI thread only: from `_plan_play_client()` and, for a folder
        changed in the dialog, through the dialog's job runner.
        """
        server_dir = self.services.controller.server_dir
        play_client.clean_partials(target, game=self.entry.id, server_dir=server_dir)
        return play_client.plan(original, target, server_dir=server_dir)

    def _plan_play_client(
        self,
        original: Path,
        target: Path,
        addons: tuple[str, ...],
        other_servers: tuple[Path, ...],
    ) -> PlayClientOffer | Path:
        """Off the GUI thread: the offer the dialog shows, or `target` if it is already made.

        A folder already carrying this server's marker (made by an earlier
        press whose record could not be saved) is recorded as it is, not built
        again: `create()` would refuse it.

        The files offered for removal from the original are this server's
        receipts there, less every path another known server also has a receipt
        for: deleting a file another server's module put there (or still uses)
        would break that server's client, and the hash cannot tell them apart.
        """
        server_dir = self.services.controller.server_dir
        # Packs are fetched at Play, but a required one the checkout lacks cannot be
        # fetched later either: refuse before anything is built (existence only).
        for pack in self.entry.client.packs:
            if not pack.optional:
                missing = _checkout_refusal(
                    pack,
                    server_dir,
                    wsl_distro=self.services.controller.wsl_distro,
                    again=f"“{MAKE_PLAY_CLIENT_LABEL}”",
                )
                if missing is not None:
                    raise play_client.PlayClientError(missing)
        if self._is_this_servers(play_client.read_marker(target)):
            return target
        build = self._replan(original, target)
        shared = {
            os.path.normcase(copy.path)
            for other in other_servers
            for copy in apply_module.client_receipts(other)
        }
        copies = tuple(
            copy
            for copy in apply_module.client_receipts(server_dir)
            if Path(copy.path).is_relative_to(original)
            and Path(copy.path).is_file()
            and os.path.normcase(copy.path) not in shared
        )
        return PlayClientOffer(
            original=original,
            target=target,
            plan=build,
            free_bytes=_free_bytes(target.parent),
            copies=copies,
            addons=addons,
            replan=lambda chosen: self._replan(original, chosen),
            free_space=lambda chosen: _free_bytes(chosen.parent),
            client=self.entry.client if has_client_data(self.entry.client) else None,
        )

    def _make_refused_now(self) -> bool:
        """Re-asked after the plan and after the dialog: did a job start meanwhile?

        `_play_client_running` is Make…'s own, so only the other two halves of
        `_play_client_refusal()` are asked.
        """
        if self._module_pending is None and not self._busy:
            return False
        self._play_client_running = False
        self._play_refused(
            "No ready-to-play client was made: another action started on this server "
            "while Yu'lon was getting it ready. Wait for it to finish, then press "
            f"“{MAKE_PLAY_CLIENT_LABEL}” again."
        )
        return True

    @Slot(object)
    def _play_plan_ready(self, result: object) -> None:
        if getattr(self, "_closed", False):
            # The tab is being torn down (a client folder changed, the window
            # closing): no dialog over it, and nothing built from its old folder.
            self._play_client_running = False
            return
        if self._make_refused_now():
            return
        if isinstance(result, Path):
            if self._remember_play_client(result):
                self._finish_make(
                    result, [f"This server's ready-to-play client at {result} is used again."]
                )
            return
        if not isinstance(result, PlayClientOffer):  # pragma: no cover - defensive
            self._release_play_client()
            return
        offer = result
        choice = self._play_client_asker(self._play_parent(), offer)
        if choice is None:
            self._release_play_client()
            self._say_play("No ready-to-play client was made.")
            return
        if self._make_refused_now():
            return
        copies = offer.copies if choice.remove_originals else ()
        self._hold_busy()
        self._say_play(f"Making the ready-to-play client at {choice.target}…")
        self._run(
            lambda: self._build_play_client(offer.original, choice, copies),
            self._play_client_made,
            self._play_client_job_failed,
        )

    def _build_play_client(
        self,
        original: Path,
        choice: PlayClientChoice,
        copies: tuple[apply_module.ClientCopy, ...],
    ) -> _MadePlayClient:
        """Off the GUI thread: build it and point it at this server. No removals yet."""
        play_client.create(
            original,
            choice.target,
            game=self.entry.id,
            server_dir=self.services.controller.server_dir,
            allow_full_copy=choice.full_copy,
        )
        problem: str | None = None
        try:
            networking.write_ready_to_play_realmlists(
                choice.target, PLAY_CLIENT_ADDRESS, self.entry.client.realmlist_file
            )
        except OSError as exc:
            problem = str(exc)
        saved: str | None = None
        if choice.client_choices is not None:
            try:
                client_packs.write_record(
                    choice.target,
                    client_packs.PackRecord(
                        {},
                        None,
                        choice.client_choices,
                        launcher=client_packs.read_record(choice.target).launcher,
                    ),
                    game=self.entry.id,
                    server_dir=self.services.controller.server_dir,
                )
            except client_packs.PackError as exc:
                saved = str(exc)
        return _MadePlayClient(choice.target, problem, copies, saved)

    @Slot(object)
    def _play_client_made(self, result: object) -> None:
        """Built: record it, and only then take the ticked files out of the original.

        In that order because the removal is only safe once the ready-to-play
        client is where this server's modules live: a record that failed leaves
        the original as the folder they go into, and its files must stay.
        """
        if not isinstance(result, _MadePlayClient):  # pragma: no cover - defensive
            self._release_play_client()
            return
        if not self._remember_play_client(result.target):
            return
        if not result.copies:
            self._finish_make(result.target, self._made_sentences(result, (), ()))
            return
        self._made = result
        self._say_play("Removing the ticked files from your own client…")
        self._run(
            lambda: apply_module.take_back_files(result.copies),
            self._play_originals_removed,
            self._play_originals_failed,
        )

    @Slot(object)
    def _play_originals_removed(self, answer: object) -> None:
        made, self._made = self._made, None
        if made is None:  # pragma: no cover - defensive
            self._release_play_client()
            return
        removed: tuple[str, ...] = ()
        left: tuple[str, ...] = ()
        if isinstance(answer, tuple) and len(answer) == 2:
            removed, left = answer
        self._finish_make(made.target, self._made_sentences(made, removed, left))

    @Slot(object)
    def _play_originals_failed(self, exc: object) -> None:
        made, self._made = self._made, None
        if made is None:  # pragma: no cover - defensive
            self._release_play_client()
            return
        said = self._made_sentences(made, (), ())
        said.append(f"The ticked files could not be removed from your own client: {exc}.")
        self._finish_make(made.target, said)

    def _made_sentences(
        self, made: _MadePlayClient, removed: tuple[str, ...], left: tuple[str, ...]
    ) -> list[str]:
        said = [f"The ready-to-play client is at {made.target}. Press Play to start it."]
        if made.realmlist_problem is not None:
            said.append(
                f"Its realmlist could not be written yet ({made.realmlist_problem}); "
                "Play writes it again first."
            )
        if made.choices_problem is not None:
            again = (
                f"choose them again under “{CLIENT_OPTIONS_LABEL}” in the ▾ menu."
                if has_client_choices(self.entry.client)
                else "the defaults will be used."
            )
            said.append(
                f"Your client choices could not be saved yet ({made.choices_problem}); {again}"
            )
        if removed:
            said.append("Removed from your own client: " + "; ".join(removed) + ".")
        if left:
            said.append("Left in your own client: " + "; ".join(left) + ".")
        return said

    def _remember_play_client(self, target: Path) -> bool:
        """Record the folder for this install; on failure say so, end Make…, answer False."""
        setter = self.services.set_play_client_dir
        if setter is None:  # pragma: no cover - the button is not built without it
            self._release_play_client()
            return False
        try:
            setter(target)
        except OSError as exc:
            self._release_play_client()
            self._play_refused(
                f"The ready-to-play client was made at {target}, but Yu'lon could not "
                f"remember it: {exc}. Nothing was removed from your own client. Press "
                f"“{MAKE_PLAY_CLIENT_LABEL}” again to use it."
            )
            return False
        logger.info(f"{self.entry.id}: ready-to-play client recorded at {target}")
        return True

    def _finish_make(self, target: Path, said: list[str]) -> None:
        """Say what Make… did, and have the tab rebuilt over the new folder."""
        self._release_play_client()
        QMessageBox.information(self._play_parent(), "Ready-to-play client", "\n\n".join(said))
        # Last: main.py drops this tab on it.
        self.play_client_dir_changed.emit(
            self.entry.id, self.services.controller.server_dir, target
        )

    @Slot(object)
    def _play_client_job_failed(self, exc: object) -> None:
        """Make…, Refresh or Delete stopped; `PlayClientError`'s text says what to do next."""
        self._release_play_client()
        if self._play_after_refresh:
            self._play_after_refresh = False
            self._play_end()
        if not isinstance(exc, play_client.PlayClientError):
            logger.warning(f"{self.entry.id}: ready-to-play client: {exc!r}")
        self._play_refused(str(exc))

    # -- Play --------------------------------------------------------------

    @Slot()
    def play(self) -> None:
        """Start the game from this server's ready-to-play client (T181 §2).

        In order: the folder must still be this server's (else the offer to
        make it again); the server must run (else "Start it first?"); a patched
        original offers Refresh; then the realmlist is written again and the
        game is started, detached.

        One Play at a time (`_play_pending`, from the press to the launch or
        the refusal): a second press while the first is on its way would start
        a second game.
        """
        if self.services.set_play_client_dir is None or self._play_pending:
            return
        if self.services.play_client_dir is None:
            self.make_play_client()
            return
        if self._play_client_blocked():
            return
        if self._usable_play_client() is None:
            return
        self._play_pending = True
        self._say_play("Checking that the server is running…")
        self._run(self.services.controller.status, self._play_status_read, self._play_failed)

    def _play_end(self, said: str | None = None) -> None:
        """The Play under way is over, started or not."""
        self._play_pending = False
        self._play_left_out = ()
        self._play_notes = ()
        if said is not None:
            self._say_play(said)
        self.play_state_changed.emit()

    def _play_still_usable(self) -> play_client.Marker | None:
        """`_usable_play_client()` part-way through a Play: a gone folder ends it first."""
        marker = self._usable_play_client(offer_remake=False)
        play = self.services.play_client_dir
        if marker is None:
            self._play_end()
            if play is not None:
                self._offer_remake(play)
        return marker

    def _play_stopped_by_another_action(self) -> bool:
        """Re-asked once the status is read: did a job start while it was being read?

        `_play_client_refusal()` (a module job, any Server action, Make…/Refresh/
        Delete) was asked at the press, but the status read runs on a worker
        with the tab unlocked, and the question below is modal. Starting the
        server, or the game, over one of them is what the press refused.
        """
        refusal = self._play_client_refusal()
        if refusal is None:
            return False
        self._play_end("Nothing was started.")
        QMessageBox.information(self._play_parent(), "Something else is running", refusal)
        return True

    @Slot(object)
    def _play_status_read(self, status: object) -> None:
        if self._play_stopped_by_another_action():
            return
        if isinstance(status, InstallStatus) and status.all_running:
            self._play_check_stale()
            return
        answer = _ask_with(
            self._play_parent(),
            "Start the server?",
            "The server is stopped. Start it first?",
            "Start and play",
        )
        if answer != "yes":
            self._play_end("Nothing was started.")
            return
        if self._play_stopped_by_another_action():
            return
        self._play_after_start = True
        self.start_server()

    def _play_check_stale(self) -> None:
        marker = self._play_still_usable()
        play = self.services.play_client_dir
        if marker is None or play is None:
            return
        server_dir, client_dir = self.services.controller.server_dir, self.services.client_dir
        source = marker.source_client_dir
        self._say_play("Comparing the ready-to-play client with your own client…")
        self._run(
            lambda: _Compared(
                play_client.stale(
                    play, source, keep=module_kept_files(server_dir, play, client_dir)
                ),
                archives_left_out(server_dir, play, source, client_dir),
            ),
            self._play_stale_read,
            self._play_failed,
        )

    @Slot(object)
    def _play_stale_read(self, found: object) -> None:
        compared = found if isinstance(found, _Compared) else _Compared((), ())
        names = [str(rel) for rel in compared.stale]
        self._play_left_out = compared.left_out
        if not names:
            self._play_launch()
            return
        left = f"\n\n{left_out_sentence(compared.left_out)}" if compared.left_out else ""
        answer = _ask_with(
            self._play_parent(),
            "Your client was patched",
            f"These files of your own client changed since the ready-to-play client was "
            f"made: {', '.join(names)}. Refresh it from your original client first? "
            f"(Your settings and addons are not touched.){left}",
            "Refresh and play",
            "Play without refreshing",
        )
        if answer == "save":
            self._play_launch()
        elif answer == "yes":
            if not self._start_refresh(then_play=True):
                self._play_end()
        else:
            self._play_end("Nothing was started.")

    def _play_launch(self) -> None:
        """The last step before the game: the client's packs, exe and config, then the launch."""
        if self.services.play_client_dir is None or self._play_still_usable() is None:
            return
        play = self.services.play_client_dir
        recorded = client_packs.read_record(play) if play is not None else None
        # Also when the catalog no longer has client data but the record still lists
        # packs or a patched exe: step 1 takes them out. And for the launcher's picks
        # alone (T187): on an entry with no client data -- every shipped game today --
        # its display and account choices are Config.wtf keys only the pipeline writes.
        if has_client_data(self.entry.client) or (
            recorded is not None
            and (recorded.packs or recorded.exe is not None or _launcher_writes(recorded))
        ):
            self._skipped = frozenset()
            self._play_prepare()
            return
        self._play_start_game()

    def _play_start_game(self) -> None:
        play = self.services.play_client_dir
        if play is None or self._play_still_usable() is None:
            return
        self._say_play("Starting World of Warcraft…")
        self._run(lambda: self._launch(play), self._play_launched, self._play_failed)

    # -- the client's packs, Wow.exe and Config.wtf (T181 b/c) -------------

    def _show_cancel(self, shown: bool) -> None:
        self.play_cancel_button.setText("Cancel")
        self.play_cancel_button.setEnabled(True)
        self.play_cancel_button.setVisible(shown)
        self.play_state_changed.emit()

    @Slot()
    def _cancel_play_download(self) -> None:
        """Stop the whole preparation; it is checked between steps, so a stalled read waits."""
        self._play_cancel.set()
        self.play_cancel_button.setEnabled(False)
        self.play_cancel_button.setText("Cancelling…")
        self.play_state_changed.emit()

    def _play_prepare(self) -> None:
        """Run the pipeline on the worker; `_skipped` are optional packs played without.

        Holds the lock the way Refresh does: it writes into the ready-to-play
        client, so a module install, Make… or a Server action must wait, and so
        must the close. Not the Play's own wait (`_play_pending` stays set).
        """
        marker = self._play_still_usable()
        play = self.services.play_client_dir
        if marker is None or play is None:
            return
        if self._play_stopped_by_another_action():
            return
        self._play_cancel.clear()
        self._play_client_running = True
        self._play_preparing = True
        self._hold_busy()
        self._show_cancel(True)
        self._say_play("Getting the ready-to-play client ready…")
        source = marker.source_client_dir
        skip = self._skipped
        self._run(
            lambda: self._prepare_client(play, source, skip),
            self._play_prepared,
            self._play_prepare_failed,
        )

    def _prepare_client(self, play: Path, source: Path, skip: frozenset[str]) -> _Prepared:
        """Off the GUI thread: packs, then the exe, then Config.wtf (spec §2, §3).

        Every record write happens as each step finishes, so a Cancel or a
        failure part-way keeps what was done and the next Play carries on.
        Talks to the GUI only through `_play_relay`.
        """
        client = self.entry.client
        game = self.entry.id
        server_dir = self.services.controller.server_dir
        say = self._play_relay.emit_line
        cancelled = self._play_cancel.is_set
        record = client_packs.read_record(play)
        packs = dict(record.packs)
        exe = record.exe
        seeded = record.config_seeded
        choices = record.choices
        notes: list[str] = []
        unavailable: set[str] = set()

        def save() -> None:
            client_packs.write_record(
                play,
                client_packs.PackRecord(packs, exe, choices, seeded, record.launcher),
                game=game,
                server_dir=server_dir,
            )

        def check_cancel() -> None:
            if cancelled():
                raise client_packs.Cancelled("Cancelled. Nothing was started.")

        wanted = {pack.id for pack in client_packs.wanted(client, record.choices)}
        # "Play without" a pack whose update was cut half way: its files are a mix of old
        # and new, so they go (and the entry) before the game starts.
        for pack_id in sorted(skip):
            half = packs.get(pack_id)
            if half is not None and _half_installed(half):
                gone = next((p for p in client.packs if p.id == pack_id), None)
                say(f"Removing the half-installed {client_packs.pack_label(gone)}…")
                left = client_packs.remove(
                    play, half, gone, game=game, server_dir=server_dir, when_off=False
                )
                files = half.get("files")
                client_packs.restore_asides(
                    play,
                    list(files) if isinstance(files, dict) else [],
                    game=game,
                    server_dir=server_dir,
                )
                del packs[pack_id]
                save()
                notes += self._removal_notes(play, source, half, gone, left)
        in_catalog = {pack.id: pack for pack in client.packs}
        # 1a. Switched off, or gone from the catalog: its recorded files go.
        for pack_id in list(packs):
            if pack_id in wanted:
                continue
            check_cancel()
            gone = in_catalog.get(pack_id)
            say(f"Removing {client_packs.pack_label(gone)}…")
            entry = packs[pack_id]
            left = client_packs.remove(play, entry, gone, game=game, server_dir=server_dir)
            del packs[pack_id]
            save()
            notes += self._removal_notes(play, source, entry, gone, left)
        # 1b. Wanted: fetch, and install when what is there differs.
        for pack in client.packs:
            if pack.id not in wanted or pack.id in skip:
                continue
            check_cancel()
            say(f"Getting {pack.label}…")
            try:
                if pack.source.kind == "checkout":
                    fetched = client_packs.fetch_checkout(pack, server_dir)
                else:
                    fetched = client_packs.fetch_url(
                        pack,
                        entry_id=game,
                        allowed_hosts=client.hosts(),
                        progress=self._download_progress(pack),
                        cancelled=cancelled,
                    )
            except client_packs.Cancelled:
                raise
            except client_packs.PackUnavailable as exc:
                if pack.optional:
                    unavailable.add(pack.id)
                    notes.append(
                        f"{pack.label} is unavailable: the server's site no longer has it. "
                        "What is installed stays until you switch it off."
                    )
                    continue
                raise _PackStopped(pack, str(exc)) from exc
            except client_packs.PackError as exc:
                raise _PackStopped(
                    pack,
                    _checkout_refusal(
                        pack, server_dir, wsl_distro=self.services.controller.wsl_distro
                    )
                    or str(exc),
                ) from exc
            have = packs.get(pack.id)
            if (
                have is not None
                and have.get("sha256") == fetched.sha256
                and have.get("version") == fetched.version
            ):
                continue
            say(f"Installing {pack.label}…")
            try:
                done = client_packs.install(
                    play, pack, fetched, game=game, server_dir=server_dir, previous=have
                )
            except client_packs.PartialInstall as exc:
                packs[pack.id] = exc.entry
                save()
                raise _PackStopped(pack, str(exc)) from exc
            except client_packs.PackError as exc:
                raise _PackStopped(pack, str(exc)) from exc
            packs[pack.id] = done
            save()
            left_behind = done.get("left_behind")
            if left_behind:
                notes.append(
                    f"{pack.label}: left alone because you changed them: "
                    + ", ".join(left_behind)
                    + "."
                )
        # 2. Wow.exe.
        if client.exe_patch is not None:
            check_cancel()
            say("Checking Wow.exe…")
            # A window pick decides `borderless`, and is saved as that exe option (T187).
            picked_exe = client_packs.launcher_exe_options(
                record.launcher,
                record.choices["exe_options"],
                client.exe_patch.options,
                catalog_always=_catalog_always(client.config_wtf),
            )
            choices = {**record.choices, "exe_options": picked_exe}
            options = client_exe.options_for(client.exe_patch, picked_exe)
            exe = client_exe.apply(play, source, client.exe_patch, options)
            try:
                save()
            except client_packs.PackError as exc:
                raise client_exe.ExeError(
                    f"Wow.exe was patched, but the note of it could not be saved ({exc}). "
                    "Press Play again: it is checked and noted then. If Play offers to refresh "
                    "Wow.exe first, say yes."
                ) from exc
        elif exe is not None:
            # The catalog dropped the patch: the original's Wow.exe comes back.
            say("Putting your own Wow.exe back…")
            if play_client.restore_original_exe(play, source, game=game, server_dir=server_dir):
                exe = None
        # 3. Config.wtf (else step (a)'s realmlist.wtf, written by `_launch`).
        cfg = client.config_wtf
        catalog_always = _catalog_always(cfg)
        written_elsewhere = _address_written_elsewhere(cfg)
        picked = client_packs.launcher_config_keys(
            record.launcher,
            catalog_always=catalog_always,
            # Config.wtf the only channel: "Use this computer" writes this computer there.
            default_address=None if written_elsewhere else PLAY_CLIENT_ADDRESS,
        )
        removed = client_packs.launcher_config_removals(
            record.launcher,
            catalog_always=catalog_always,
            default_address_written=written_elsewhere,
        )
        if cfg is None and (picked or removed):
            # An entry without `config_wtf` still gets the launcher's picks (T187); its
            # realmlist stays `_launch`'s, and no seed was written, so none is used up.
            check_cancel()
            say("Setting up Config.wtf…")
            client_config.merge_config_wtf(
                play, ConfigWtf(always=picked), first_run=False, remove=removed
            )
        elif cfg is not None:
            check_cancel()
            say("Setting up Config.wtf…")
            if cfg.remove_locale_realmlists:
                client_config.remove_locale_realmlists(play)
            else:
                try:
                    networking.write_ready_to_play_realmlists(
                        play, _realm_address(record), client.realmlist_file
                    )
                except OSError as exc:
                    raise play_client.PlayClientError(
                        f"The realmlist in {play} could not be written ({exc}), so nothing was "
                        "started. Check that you can write to that folder, then press Play again."
                    ) from exc
            # The catalog's `always` is the server's own: `launcher_config_keys` dropped every
            # pick it sets except a typed address's `realmList`/`patchList`, which win (lead
            # ruling). A pick, or a removal, also takes a seed's place.
            taken = {key.casefold() for key in (*picked, *removed)}
            cfg = cfg.model_copy(
                update={
                    "always": {
                        **{k: v for k, v in cfg.always.items() if k.casefold() not in taken},
                        **picked,
                    },
                    "seed": {k: v for k, v in cfg.seed.items() if k.casefold() not in taken},
                }
            )
            client_config.merge_config_wtf(
                play, cfg, first_run=not record.config_seeded, remove=removed
            )
            seeded = True
            save()
        return _Prepared(tuple(notes), frozenset(unavailable))

    def _download_progress(self, pack: ClientPack) -> Callable[[int, int], None]:
        """A progress callback for `pack`'s download: at most five lines a second."""
        say = self._play_relay.emit_line
        last = [-1, 0.0]  # percent, time

        def report(done: int, total: int) -> None:
            percent = int(done * 100 / total) if total else 0
            now = time.monotonic()
            if percent == last[0] or (percent < 100 and last[0] >= 0 and now - last[1] < 0.2):
                return
            last[0], last[1] = percent, now
            of = f" of {size_text(total)}" if total else ""
            say(f"Downloading {pack.label}… {percent}% ({size_text(done)}{of})")

        return report

    @staticmethod
    def _removal_notes(
        play: Path,
        source: Path,
        entry: Mapping[str, Any],
        pack: ClientPack | None,
        left: Collection[str],
    ) -> list[str]:
        """What to tell the player about a pack taken out (decision 4 and the edited files)."""
        label = client_packs.pack_label(pack)
        notes: list[str] = []
        if left:
            notes.append(
                f"{label}: left in place because you changed them: " + ", ".join(left) + "."
            )
        files = entry.get("files")
        candidates = [
            *(files if isinstance(files, dict) else ()),
            *(pack.remove_when_off if pack else ()),
        ]
        replaced = sorted(
            rel
            for rel in dict.fromkeys(candidates)
            if rel not in left and not (play / rel).exists() and (source / rel).exists()
        )
        if replaced:
            notes.append(
                f"{label} had replaced {', '.join(replaced)} from your own client, which the "
                "ready-to-play client now lacks. To bring it back, press "
                f"“{DELETE_PLAY_CLIENT_LABEL}” and then “{MAKE_PLAY_CLIENT_LABEL}” again."
            )
        return notes

    def _torn_down_during_play(self) -> bool:
        """The tab was dropped while the pipeline ran: no dialog, no launch, locks given back."""
        if not getattr(self, "_closed", False):
            return False
        self._release_play_client()
        self._play_end()
        return True

    @Slot(object)
    def _play_prepared(self, result: object) -> None:
        if self._torn_down_during_play():
            return
        self._release_play_client()
        self._show_cancel(False)
        if self._play_cancel.is_set():
            # Pressed after the last check: the player asked to stop, so nothing starts.
            self._play_end("Cancelled. Nothing was started.")
            return
        if isinstance(result, _Prepared):
            self._client_unavailable = result.unavailable
            self._play_notes = result.notes
        self._play_start_game()

    @Slot(object)
    def _play_prepare_failed(self, exc: object) -> None:
        if self._torn_down_during_play():
            return
        self._release_play_client()
        self._show_cancel(False)
        if isinstance(exc, _PackStopped):
            self._play_pack_stopped(exc)
        elif isinstance(exc, client_packs.Cancelled):
            self._play_end("Cancelled. Nothing was started.")
        elif isinstance(
            exc, (client_packs.PackError, client_exe.ExeError, play_client.PlayClientError)
        ):
            self._play_end()
            self._play_refused(str(exc))
        else:
            self._play_failed(exc)

    def _play_pack_stopped(self, stopped: _PackStopped) -> None:
        """A pack failed: a required one blocks Play (Retry); an optional one may be left out."""
        pack = stopped.pack
        self.action_failed.emit(f"{self.entry.id}: {stopped.reason}")
        self._say_play(stopped.reason)
        if pack.optional:
            play = self.services.play_client_dir
            held = client_packs.read_record(play).packs.get(pack.id) if play is not None else None
            have = held is not None and not _half_installed(held)
            if have:
                text = (
                    f"{stopped.reason}\n\nPlay with the version of “{pack.label}” you have "
                    "this time, or try again?"
                )
                skip: str | None = "Play with the version you have"
            else:
                text = f"{stopped.reason}\n\nPlay without “{pack.label}” this time, or try again?"
                skip = f"Play without {pack.label}"
        else:
            text = (
                f"“{pack.label}” is needed to play on this server, so nothing was started: "
                f"{stopped.reason}\n\nTry again?"
            )
            skip = None
        answer = _ask_with(
            self._play_parent(), f"{pack.label} could not be set up", text, "Retry", skip
        )
        if answer == "yes":
            self._play_prepare()
        elif answer == "save":
            self._skipped = self._skipped | {pack.id}
            self._play_prepare()
        else:
            self._play_end("Nothing was started.")

    def _launch(self, play: Path) -> None:
        """Off the GUI thread: the realmlist again, then the game, detached.

        Every refusal is a `LaunchRefusal` whose text says what to do next. An
        `OSError` from starting the process (a Proton that is not executable, a
        Wine that went away) comes through `launch()` raw and is worded here.
        """
        if self.entry.client.config_wtf is None:
            # An entry with a `config_wtf` had its Config.wtf merged (and the locale
            # realmlists removed) by the pipeline; every other entry is step (a)'s.
            try:
                networking.write_ready_to_play_realmlists(
                    play,
                    _realm_address(client_packs.read_record(play)),
                    self.entry.client.realmlist_file,
                )
            except OSError as exc:
                raise play_launch.LaunchRefusal(
                    f"The realmlist in {play} could not be written ({exc}), so nothing was "
                    "started. Check that you can write to that folder, then press Play again."
                ) from exc
        spec = play_launch.launch_spec(
            play,
            game=self.entry.id,
            server_dir=self.services.controller.server_dir,
            os_name=platform.detect(),
            home=Path.home(),
            config_dir=platform.config_dir(),
        )
        try:
            play_launch.launch(spec)
        except OSError as exc:
            raise play_launch.LaunchRefusal(
                f"Yu'lon could not start {spec.argv[0]} ({exc.strerror or exc}), so nothing "
                "was started. If that is Proton or Wine, check that it is still installed "
                "and can be run (reinstall it if it was moved), then press Play again."
            ) from exc
        logger.info(f"{self.entry.id}: started {' '.join(spec.argv)} in {spec.cwd}")

    @Slot(object)
    def _play_launched(self, _result: object) -> None:
        said = "World of Warcraft is starting. Closing Yu'lon does not close it."
        if self._play_left_out:
            said += " " + left_out_sentence(self._play_left_out)
        if self._play_notes:
            said += " " + " ".join(self._play_notes)
        self._play_end(said)

    @Slot(object)
    def _play_failed(self, exc: object) -> None:
        self._play_end()
        if not isinstance(exc, play_launch.LaunchRefusal):
            logger.warning(f"{self.entry.id}: Play: {exc!r}")
            exc = (
                f"Play stopped: {exc}. Nothing was started. Press Play again; if it stops "
                f"the same way, use “{REFRESH_PLAY_CLIENT_LABEL}” in the "
                "▾ menu beside Play first."
            )
        self._play_refused(str(exc))

    # -- Client options… ---------------------------------------------------

    @Slot()
    def client_options(self) -> None:
        """Choose the optional packs and Wow.exe options; the next Play applies them (T181 c)."""
        play = self.services.play_client_dir
        if self.services.set_play_client_dir is None or play is None:
            return
        if self._play_waits_for_play() or self._play_client_blocked():
            return
        if self._usable_play_client() is None:
            return
        record = client_packs.read_record(play)
        offer = ClientOptionsOffer(
            client=self.entry.client,
            choices=record.choices,
            installed=frozenset(record.packs),
            unavailable=self._client_unavailable,
        )
        chosen = self._client_options_asker(self, offer)
        if chosen is None:
            return
        # Again: the dialog is modal, and a job may have started behind it.
        if self._play_waits_for_play() or self._play_client_blocked():
            return
        game, server_dir = self.entry.id, self.services.controller.server_dir
        self._play_client_running = True
        self._hold_busy()
        self._say_play("Saving your client choices…")

        def save() -> None:
            current = client_packs.read_record(play)
            launcher = current.launcher
            borderless = chosen.get("exe_options", {}).get("borderless")
            if isinstance(borderless, bool):
                # T187: one source -- the launcher's window pick follows this box,
                # or the next Play would put back what the player just changed.
                launcher = client_packs.launcher_following_borderless(launcher, borderless)
            client_packs.write_record(
                play,
                client_packs.PackRecord(
                    current.packs, current.exe, chosen, current.config_seeded, launcher
                ),
                game=game,
                server_dir=server_dir,
            )

        self._run(save, self._client_options_saved, self._play_client_job_failed)

    @Slot(object)
    def _client_options_saved(self, _result: object) -> None:
        self._release_play_client()
        self._say_play("Your client choices are saved. They take effect at your next Play.")

    # -- the client launcher window's hooks (T187) --------------------------

    @property
    def play_unavailable(self) -> frozenset[str]:
        """The optional packs whose address answered 404 at the last Play (spec §4)."""
        return self._client_unavailable

    def play_client_busy(self) -> str | None:
        """Why the ready-to-play client's record may not be written now, or None (T187).

        A Play on its way (it reads the record and writes it back as each step
        ends), any Server action, and Make…/Refresh/Delete/Client options and a
        launcher save (`_play_client_running`). A module job is not here: it
        never writes the record, and the launcher's saves have no reason to wait
        for one -- its PLAY, Refresh and Delete still go through
        `_play_client_refusal()`, which does ask about it.
        """
        if self._play_pending:
            return PLAY_PENDING
        if self._busy or self._play_client_running:
            return (
                "This server is busy with another action — wait for it to finish, then "
                "change this again. Nothing was changed."
            )
        return None

    def save_launcher_picks(
        self,
        *,
        launcher: Mapping[str, Any] | None = None,
        drop: Collection[str] = (),
        packs: Mapping[str, bool] | None = None,
        exe_options: Mapping[str, bool] | None = None,
    ) -> str | None:
        """Save the launcher window's choices into the record, off the GUI thread (T187).

        `launcher` keys replace the saved ones and `drop` removes keys (a realm
        address put back to this computer); `packs` and `exe_options` are merged
        into the choices "Client options…" saves, which are the same record. A
        window pick also sets the `borderless` exe option where the catalog's
        patch has one (`launcher_exe_options`), so the two never disagree.

        Read, changed and written in one job under `_play_client_running`, the
        lock Client options takes: Play's pipeline reads the record and writes it
        back, and a save landing between the two would be lost. Answers the
        refusal (nothing is started) or None; the outcome comes through
        `launcher_saved`. Nothing is downloaded or written into the client here:
        the next Play applies it.
        """
        play = self.services.play_client_dir
        if self.services.set_play_client_dir is None or play is None:
            return (
                "This server has no ready-to-play client to save that in. Make one first. "
                "Nothing was changed."
            )
        refusal = self.play_client_busy()
        if refusal is not None:
            return refusal
        game, server_dir = self.entry.id, self.services.controller.server_dir
        patch = self.entry.client.exe_patch
        always = _catalog_always(self.entry.client.config_wtf)
        changes = dict(launcher or {})
        gone = frozenset(drop)

        def save() -> None:
            current = client_packs.read_record(play)
            picks = {k: v for k, v in current.launcher.items() if k not in gone}
            picks.update(changes)
            chosen_packs = {**current.choices["packs"], **(packs or {})}
            chosen_exe = {**current.choices["exe_options"], **(exe_options or {})}
            if patch is not None:
                chosen_exe = client_packs.launcher_exe_options(
                    picks, chosen_exe, patch.options, catalog_always=always
                )
            client_packs.write_record(
                play,
                client_packs.PackRecord(
                    current.packs,
                    current.exe,
                    {"packs": chosen_packs, "exe_options": chosen_exe},
                    current.config_seeded,
                    picks,
                ),
                game=game,
                server_dir=server_dir,
            )

        self._play_client_running = True
        self.play_state_changed.emit()
        self._run(save, self._launcher_picks_saved, self._launcher_picks_failed)
        return None

    @Slot(object)
    def _launcher_picks_saved(self, _result: object) -> None:
        self._release_play_client()
        self.launcher_saved.emit("")

    @Slot(object)
    def _launcher_picks_failed(self, exc: object) -> None:
        self._release_play_client()
        if not isinstance(exc, client_packs.PackError):
            logger.warning(f"{self.entry.id}: saving the launcher's choices: {exc!r}")
        self.launcher_saved.emit(str(exc) or "Your choice could not be saved.")

    # -- Refresh and Delete ------------------------------------------------

    @Slot()
    def refresh_play_client(self) -> None:
        """Bring shared game files and Wow.exe back in step with the original (T181 §4)."""
        if self._play_waits_for_play():
            return
        self._start_refresh(then_play=False)

    def _start_refresh(self, *, then_play: bool) -> bool:
        """Start a Refresh; False (said) when it could not start. `then_play` launches after."""
        play = self.services.play_client_dir
        if play is None:
            return False
        if self._play_client_blocked():
            return False
        marker = self._usable_play_client(offer_remake=not then_play)
        if marker is None:
            if then_play:
                self._play_end()
                self._offer_remake(play)
            return False
        server_dir, client_dir = self.services.controller.server_dir, self.services.client_dir
        self._play_after_refresh = then_play
        self._play_client_running = True
        self._hold_busy()
        self._say_play(f"Refreshing the ready-to-play client from {marker.source_client_dir}…")
        source = marker.source_client_dir

        def work() -> _Compared:
            done = play_client.refresh(
                play,
                source,
                game=self.entry.id,
                server_dir=server_dir,
                keep=module_kept_files(server_dir, play, client_dir),
                exe_patch=self.entry.client.exe_patch,
                catalog_always=_catalog_always(self.entry.client.config_wtf),
            )
            return _Compared(done, archives_left_out(server_dir, play, source, client_dir))

        self._run(work, self._play_client_refreshed, self._play_client_job_failed)
        return True

    @Slot(object)
    def _play_client_refreshed(self, result: object) -> None:
        self._release_play_client()
        compared = result if isinstance(result, _Compared) else _Compared((), ())
        done = [str(rel) for rel in compared.stale]
        if done:
            said = "Refreshed from your own client: " + ", ".join(done) + "."
        else:
            said = "Nothing needed refreshing: it matches your own client."
        if compared.left_out:
            said += " " + left_out_sentence(compared.left_out)
        self._say_play(said)
        if self._play_after_refresh:
            self._play_after_refresh = False
            self._play_launch()

    @Slot()
    def delete_play_client(self) -> None:
        """Delete this server's ready-to-play client; the original keeps every file (T181 §4)."""
        setter = self.services.set_play_client_dir
        play = self.services.play_client_dir
        if setter is None or play is None:
            return
        if self._play_waits_for_play() or self._play_client_blocked():
            return
        marker = play_client.read_marker(play)
        source = marker.source_client_dir if marker is not None else self.services.client_dir
        own = f"Your own client at {source}" if source is not None else "Your own client"
        if not self._confirm(
            "Delete the ready-to-play client?",
            f"Delete the ready-to-play client at {play}?\n\n{own} is not touched: it keeps "
            "all its files, the game files the two shared included. This server's modules "
            "install into your own client again afterwards. Module client files installed "
            "since the ready-to-play client was made live only in it, so your own client "
            "lacks them until those modules are reinstalled or updated. Yu'lon can make a "
            "ready-to-play client again at any time.",
            self._play_parent(),
        ):
            return
        # Again: a module job may have started while the question was open.
        if self._play_waits_for_play() or self._play_client_blocked():
            return
        server_dir = self.services.controller.server_dir
        self._play_client_running = True
        self._hold_busy()
        self._say_play(f"Deleting {play}…")
        self._run(
            lambda: play_client.delete(play, game=self.entry.id, server_dir=server_dir),
            self._play_client_deleted,
            self._play_client_job_failed,
        )

    @Slot(object)
    def _play_client_deleted(self, _result: object) -> None:
        self._release_play_client()
        setter = self.services.set_play_client_dir
        if setter is None:  # pragma: no cover - the menu is not built without it
            return
        try:
            setter(None)
        except OSError as exc:
            self._play_refused(
                f"The ready-to-play client was deleted, but Yu'lon could not forget it: {exc}. "
                f"Use “{DELETE_PLAY_CLIENT_LABEL}” again."
            )
            return
        # Last: main.py drops this tab on it.
        self.play_client_dir_changed.emit(self.entry.id, self.services.controller.server_dir, None)

    @Slot()
    def add_to_steam(self) -> None:
        """8.8. Put this server and its client in the Steam library.

        Off the GUI thread like every other action here: the press draws an icon
        and reads and rewrites two files in the user's Steam profile, and none of
        that belongs on the thread that repaints the window.

        Not part of `_set_busy()`'s lock, and that is deliberate rather than an
        oversight: this touches no container, no compose project and no database,
        so there is nothing for it to race with. It locks only its own button,
        so a second press cannot arrive while the first is still writing.
        """
        shortcuts = self.services.steam
        if shortcuts is None or self.steam_button is None:  # pragma: no cover - not built
            return
        self.steam_button.setEnabled(False)
        self.steam_label.setText("Writing the two Steam entries\u2026")
        self._run(shortcuts.add, self._steam_done, self._steam_failed)

    @Slot(object)
    def _steam_done(self, result: object) -> None:
        """Name everything the press changed, so it can be checked by hand."""
        if self.steam_button is not None:
            self.steam_button.setEnabled(True)
        said = steam_module.confirmation(cast(steam_module.AddReport, result))
        self.steam_label.setText(said)
        logger.info(f"steam: {self.entry.id}: {said}")

    @Slot(object)
    def _steam_failed(self, exc: object) -> None:
        """One of five refusals, each of which says what to do about it.

        On the label AND on `action_failed`, for the reason every refusal on this
        tab is on both: the label is where the person looks and the signal is
        what the app's own log keeps.
        """
        if self.steam_button is not None:
            self.steam_button.setEnabled(True)
        message = str(exc)
        self.steam_label.setText(message)
        self.action_failed.emit(message)

    @Slot()
    def remove_containers(self) -> None:
        """Arm on the first press; remove on the second.

        The action is safe for player data — the database is a named volume and
        `remove_staged()` never passes `-v` — but it is still a teardown, and it
        sits next to Stop. Arming says what will happen, in the same label the
        stop refusals use, before anything is touched.
        """
        if not self._remove_armed:
            # Only one of the two destructive buttons is ever armed. Both write
            # their warning into the same label, so two armed at once would show
            # one paragraph over two loaded buttons, and the second press would
            # do whichever the user had forgotten about.
            self._disarm_repair()
            self._remove_armed = True
            self.remove_button.setText(REMOVE_ARMED)
            self.problem_label.setText(
                "This stops the server and deletes its containers. Your characters are NOT "
                "affected — the database lives in a Docker volume, which is kept. The next "
                "Start recreates the containers, which takes longer than a normal start. "
                "Press Refresh to cancel."
            )
            return
        self._disarm_remove()
        self._set_busy(True)
        self._stop_forced = ""
        self.problem_label.setText("Removing containers…")
        self._run(self.services.controller.remove, self._remove_done, self._remove_failed)

    def _disarm_remove(self) -> None:
        self._remove_armed = False
        self.remove_button.setText(REMOVE_IDLE)

    def _disarm_repair(self) -> None:
        self._repair_armed = False
        self.repair_button.setText(REPAIR_IDLE)

    def _disarm_actions(self) -> None:
        """Any other server action means the user moved on from all of them.

        The restore plan included (T216 review round 1): a plan is a census of
        what ran when it was shown, and a Start, Stop or Remove changes exactly
        that. The press re-plans anyway; a plan on screen that the server has
        since moved away from is still one the player would be agreeing to.
        """
        self._disarm_remove()
        self._disarm_repair()
        self._hide_stop_other()
        self._restore_plan = None
        self.restore_button.setEnabled(False)

    @Slot(object)
    def _remove_done(self, result: object) -> None:
        self._set_busy(False)
        # T95: "nothing to remove" is where a player whose containers are gone
        # looks for a way off the list. The button in the row is that way, and it
        # lights up.
        self._nothing_to_remove = not result
        self.problem_label.setText(
            self._after_the_stop(
                "Containers removed; volumes kept. The next Start will recreate them."
                if result
                else (
                    f'There were no containers to remove. "{REMOVE_FROM_YULON}" in the row '
                    "above takes this server off Yu'lon's list and deletes nothing."
                )
            )
        )
        self._update_forget_visibility()
        self.refresh_status()

    @Slot(object)
    def _remove_failed(self, exc: object) -> None:
        self._set_busy(False)
        self.problem_label.setText(f"Could not remove the containers: {exc}")
        self.action_failed.emit(str(exc))

    @Slot()
    def repair_import(self) -> None:
        """Arm on the first press; re-run the one-shot import on the second.

        The armed paragraph says what is overwritten rather than what is kept —
        the opposite of the teardown's, and the honest way round. Everything the
        import writes is replaced, and the only reason this is offered at all is
        that the probe has already found no accounts and no characters to lose.
        `docker.repair_import()` asks the database again itself and refuses if
        that has changed since, so this text is a warning and not the guard.
        """
        if not self._repair_armed:
            self._disarm_remove()
            self._repair_armed = True
            self.repair_button.setText(REPAIR_ARMED)
            self.problem_label.setText(
                "This re-runs the database import that never finished. Everything in the auth, "
                "characters and world databases is OVERWRITTEN. It is offered because those "
                "databases hold no accounts and no characters — if that is wrong, press "
                "Refresh now, while nothing has happened yet, and restore a backup from the "
                "Maintenance tab instead. The server must be stopped; the database is started "
                "if it is not running and is left running afterwards.\n\n"
                "Press the button again to start. A full import takes 10-30 minutes, and once "
                "it starts it cannot be stopped and the window cannot be closed until it "
                "finishes."
            )
            return
        self._disarm_repair()
        self._set_busy(True)
        self._import_running = True
        self._import_tail.clear()
        # The offer described the state this run is in the middle of ending.
        # `_disarm_repair()` resets the flag and the button text and nothing
        # else, and `_show_repair()` is not reached again until the run
        # finishes — so "this install's databases were never finished" sat
        # directly under "Running the database import" for the whole 10-30
        # minutes, contradicting it (review, 2026-08-23).
        self.repair_label.setVisible(False)
        self.problem_label.setText(IMPORT_RUNNING)
        # The sink is the relay's emitter, not `_import_line`: this call runs on
        # a worker thread, and everything it invokes runs there too.
        self._run(
            lambda: self.services.controller.repair_import(self._import_relay.emit_line),
            self._repair_done,
            self._repair_failed,
        )

    @Slot(str)
    def _import_line(self, line: str) -> None:
        """Show the import's most recent output, so a long job cannot look like a hung one.

        Reached only through `_import_relay`, which is what puts it on the GUI
        thread. The whole log is deliberately NOT collected here: `docker
        compose logs ac-db-import` keeps it, `docker.run_attached()` retains a
        bounded tail for the failure message, and a half-hour of lines
        accumulating in a window that may stay open for days is the defect this
        change exists to avoid rather than one to introduce elsewhere.
        """
        # Through `lines.parse()` before anything else: these two sinks are the
        # reason T35's markers are NOT applied inside `run_attached()`, and a
        # belt on top of that braces. `QLabel` renders `\x1e` as a box glyph,
        # and a future caller wiring a marked stream in here would leave one in
        # front of every line with nothing to say what it was.
        text = lines.parse(line).text.strip()
        if not text:
            return
        if len(text) > _IMPORT_LINE_CHARS:
            text = text[:_IMPORT_LINE_CHARS] + "…"
        self._import_tail.append(text)
        self.problem_label.setText("\n".join([IMPORT_RUNNING, *self._import_tail]))

    @Slot(object)
    def _repair_done(self, _result: object) -> None:
        self._set_busy(False)
        self._import_running = False
        self.problem_label.setText(
            "The database import finished. Press Start — the server has a database to talk to now."
        )
        # The remembered answer is now stale in the one direction that matters:
        # leaving it would keep offering a repair for an install that has just
        # been repaired.
        self._import_state = None
        self._import_asked = False
        self._show_repair()
        self.refresh_status()

    @Slot(object)
    def _repair_failed(self, exc: object) -> None:
        self._set_busy(False)
        self._import_running = False
        self.problem_label.setText(str(exc))
        self.action_failed.emit(str(exc))
        self._import_asked = False
        self.refresh_status()

    # ----------------------------------------------------------- console tab

    def _build_console_tab(self) -> None:
        tab = QWidget(self)
        box = QVBoxLayout(tab)
        self.console_log = LogPanel(tab)
        self.follow_button = QPushButton("Follow worldserver log", tab)
        self.follow_button.clicked.connect(self.follow_logs)
        self.command_edit = QLineEdit(tab)
        self.command_edit.setPlaceholderText("console command, e.g. server info")
        self.send_button = QPushButton("Send", tab)
        self.send_button.clicked.connect(self.send_console_command)
        cmd_row = QHBoxLayout()
        cmd_row.addWidget(self.command_edit, 1)
        cmd_row.addWidget(self.send_button)

        self.console_note = QLabel("", tab)
        self.console_note.setWordWrap(True)
        self.console_note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.console_note.setVisible(False)
        if not self._console_available():
            # Checklist 6.5 asks for this gap to be re-scoped, "not left silently
            # broken". Refusing on click and printing the error afterwards is not
            # the same as saying so up front: the catalog tile already disables
            # Install with the reason on the tile (6.1), so the console says it
            # the same way. Following the worldserver log needs no pty and stays
            # enabled, which is most of what this tab is for.
            self.send_button.setEnabled(False)
            self.command_edit.setEnabled(False)
            self.console_note.setText(
                wotlk_console.NO_TTY_HELP.format(container=self.entry.container_spec().world)
            )
            self.console_note.setVisible(True)

        box.addWidget(self.follow_button)
        box.addWidget(self.console_log, 1)
        box.addLayout(cmd_row)
        box.addWidget(self.console_note)
        self._add_panel_tab(tab, "console", "Console")

    @Slot()
    def follow_logs(self) -> None:
        self.console_log.run(self.services.logs_source, title="worldserver log", ended=FOLLOW_ENDED)

    @Slot()
    def send_console_command(self) -> None:
        command = self.command_edit.text().strip()
        if command:
            self._send(command)
            self.command_edit.clear()

    def _send(self, command: str) -> None:
        """Send one console command off the GUI thread (it waits for the reply window).

        Guarded the way `refresh_status()` is, and for a sharper reason. A
        command costs the whole 3s window whatever it answers, an empty answer
        is a routine outcome, and silence invites a second press — which used to
        start a SECOND `docker attach` on the same container and overwrite the
        pending callback. Two clients on one tty is what puts foreign prompts
        and echoes inside each other's windows (see `console._PROMPT`), so the
        obvious response to a quiet console was also the way to corrupt the next
        reply (review, 2026-08-23).

        It used to take a `then` callback so account creation could chain
        `account set gmlevel` behind `account create`. Nothing passes it any
        more — accounts have their own tab and write the row through SRP6 — so
        it went, along with a docstring that described a caller that no longer
        exists.
        """
        if self._console_pending:
            return
        shown = command if not command.startswith("account create") else "account create ****"
        self.console_log.append(f"> {shown}")
        self._console_pending = True
        self.send_button.setEnabled(False)
        self._run(
            lambda: self.services.send_console(command),
            self._console_reply,
            self._console_failed,
        )

    def _console_available(self) -> bool:
        """Can this host type at this server's console?

        Not `pty_supported()` any more, and the difference is a whole platform.
        A Windows box managing a WSL-resident server has no pty of its own and
        can still send: the distro opens one (`console.distro_attach_argv()`).
        Asked of the controller because that is where the distro is already
        recorded — a second copy on `ControllerServices` would be one more
        thing that can disagree with the seam actually doing the work.
        """
        return wotlk_console.can_send(wsl_distro=self.services.controller.wsl_distro)

    def _console_idle(self) -> None:
        """Re-arm Send — never where it cannot send (see `_build_console_tab()`)."""
        self._console_pending = False
        self.send_button.setEnabled(self._console_available())

    @Slot(object)
    def _console_reply(self, result: object) -> None:
        self._console_idle()
        if not isinstance(result, wotlk_console.ConsoleReply):
            return
        if not result.prompted:
            # No `AC> ` anywhere in the window, so those lines are whatever
            # arrived rather than an answer. Docker's own failure looks like
            # this, and so does a worldserver still loading maps — which the tab
            # used to print as if it were a reply, into the panel that is
            # already streaming the same log.
            self.console_log.append(
                "(no console prompt in the reply window — what follows is whatever arrived, "
                "not an answer; the worldserver may still be starting)"
            )
        elif not result.lines:
            # Cutting between prompts makes an empty answer normal, and an empty
            # answer used to leave the user staring at their own echo with
            # nothing to distinguish it from a command the app had dropped.
            self.console_log.append("(no reply inside the 3s window)")
        for line in result.lines:
            self.console_log.append(line)

    @Slot(object)
    def _console_failed(self, exc: object) -> None:
        self._console_idle()
        self.console_log.append(f"!! {exc}")
        self.action_failed.emit(str(exc))

    # ----------------------------------------------------------- accounts tab

    def _build_accounts_tab(self) -> None:
        """Account creation, in its own tab because it no longer needs the console.

        It used to live under Console because it WAS the console: two commands
        typed down a `docker attach` pty. Writing the row directly means it works
        where there is no pty, which is every Windows box — so leaving it on a tab
        whose other controls are disabled there would hide the one thing that
        does work.
        """
        tab = QWidget(self)
        box = QVBoxLayout(tab)
        accounts = QGroupBox("Create account", tab)
        form = QFormLayout(accounts)
        # Fields fill the panel rather than staying at their size hint: a form
        # pinned to `FieldsStayAtSizeHint` leaves the text boxes their narrowest
        # default and drops the spare width into the gap between the label and
        # the field, which reads as a broken form inside a two-column panel.
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.account_name = QLineEdit(accounts)
        self.account_password = QLineEdit(accounts)
        self.account_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.account_gm = QSpinBox(accounts)
        # 8.3d: the ceiling is this tree's, measured by asking its own
        # command. Three of the four stop at 3; the tortoise fork accepts 4,
        # and a control that offered 0-to-3 there would hide a level the
        # tree has without anything failing.
        self.account_gm.setRange(0, _highest_level(self.entry))
        self.create_account_button = QPushButton("Create", accounts)
        self.create_account_button.clicked.connect(self.create_account)
        form.addRow("Username", self.account_name)
        form.addRow("Password", self.account_password)
        form.addRow("GM level", self.account_gm)
        form.addRow(self.create_account_button)

        # 8.3a. Hidden for a game whose account stores have not been measured:
        # 8.3b, 8.3c and 8.3d each add their own, and a list built on a guessed
        # store shows every account as level 0, which is a lie shaped like an
        # answer.
        wired = self.services.accounts is not None
        existing = QGroupBox("Accounts on this server", tab)
        existing_box = QVBoxLayout(existing)
        self.account_list = QListWidget(existing)
        self.account_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.account_list.customContextMenuRequested.connect(self._show_account_context_menu)
        self.account_list.currentRowChanged.connect(self._account_chosen)
        self.refresh_accounts_button = QPushButton("Refresh the list", existing)
        self.refresh_accounts_button.clicked.connect(self.refresh_accounts)
        change = QFormLayout()
        change.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.selected_password = QLineEdit(existing)
        self.selected_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.set_password_button = QPushButton("Set password", existing)
        self.set_password_button.clicked.connect(self.set_selected_password)
        self.selected_gm = QSpinBox(existing)
        self.selected_gm.setRange(0, _highest_level(self.entry))
        self.set_gm_button = QPushButton("Set GM level", existing)
        self.set_gm_button.clicked.connect(self.set_selected_gm_level)
        change.addRow("New password", self.selected_password)
        change.addRow(self.set_password_button)
        change.addRow("GM level", self.selected_gm)
        change.addRow(self.set_gm_button)
        existing_box.addWidget(self.account_list)
        existing_box.addWidget(self.refresh_accounts_button)
        existing_box.addLayout(change)
        existing.setVisible(wired)
        for control in (
            self.account_list,
            self.refresh_accounts_button,
            self.set_password_button,
            self.set_gm_button,
        ):
            control.setVisible(wired)
        # Nothing is chosen yet, and a button that acts on "whichever row
        # happens to be first" is a trap rather than a convenience.
        self._account_chosen(-1)

        self.account_report = QLabel("", tab)
        # A core this app cannot write an account for is said once, here, with
        # the command that does work — rather than left as a live button whose
        # every press ends in a SQL error, or worse in a row that inserts
        # cleanly and can never log in. See `catalog.Accounts`.
        if self.entry.accounts.scheme is None:
            self.create_account_button.setEnabled(False)
            for widget in (self.account_name, self.account_password, self.account_gm):
                widget.setEnabled(False)
            self.account_report.setText(
                f"{self.entry.name} keeps its accounts in a form this app does not write yet. "
                f"Make one on the Console tab instead: "
                f"{self.entry.accounts.console_command}"
            )
        self.account_report.setWordWrap(True)
        self.account_report.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        # Two panels side by side: create on the left, the server's existing
        # accounts (list + change) on the right. On a wide window the list
        # gets room without pushing the form off screen; on a narrow one each
        # panel shrinks to its own minimum and the window scrolls rather than
        # clipping. `existing` is hidden for a game with no account seam, and
        # a hidden group box hands its whole column back to `accounts`.
        columns = QHBoxLayout()
        columns.setSpacing(12)
        columns.addWidget(accounts, 1)
        columns.addWidget(existing, 1)
        box.addLayout(columns)
        box.addWidget(self.account_report)
        box.addStretch(1)
        self._add_panel_tab(tab, "accounts", "Accounts")

    def _build_characters_tab(self) -> None:
        """8.4a. Every action drawn only where this tree has the command, and
        every button naming the character it would act on.

        A button called "Revive" is one somebody presses believing it acts on
        the row they are looking at. "Revive Guglu" is one they can check before
        pressing, and it costs a `setText` per selection.
        """
        tab = QWidget(self)
        box = QVBoxLayout(tab)
        wired = self.services.play is not None

        people = QGroupBox("Characters on this server", tab)
        people_box = QVBoxLayout(people)
        self.character_list = QListWidget(people)
        self.character_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.character_list.customContextMenuRequested.connect(self._show_character_context_menu)
        self.character_list.currentRowChanged.connect(self._character_chosen)
        self.refresh_characters_button = QPushButton("Refresh the list", people)
        self.refresh_characters_button.clicked.connect(self.refresh_characters)
        people_box.addWidget(self.character_list)
        people_box.addWidget(self.refresh_characters_button)

        actions = QGroupBox("What to do", tab)
        form = QFormLayout(actions)
        # As the Accounts form: the text box and spin box grow to fill the
        # action column, and the buttons beside them sit on a shared baseline.
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.teleport_where = QLineEdit(actions)
        self.teleport_where.setPlaceholderText("a place this server knows, like Stormwind")
        self.teleport_button = QPushButton("Teleport", actions)
        self.teleport_button.clicked.connect(self.teleport_character)
        self.new_level = QSpinBox(actions)
        self.new_level.setRange(1, 255)
        self.set_level_button = QPushButton("Set level", actions)
        self.set_level_button.clicked.connect(self.set_character_level)
        self.rename_button = QPushButton("Rename at next login", actions)
        self.rename_button.clicked.connect(self.rename_character)
        self.revive_button = QPushButton("Revive", actions)
        self.revive_button.clicked.connect(self.revive_character)
        self.gold_amount = QSpinBox(actions)
        self.gold_amount.setRange(1, 214_748)
        self.mail_gold_button = QPushButton("Send gold", actions)
        self.mail_gold_button.clicked.connect(self.mail_gold)
        self.send_gear_button = QPushButton("Send everything worn", actions)
        self.send_gear_button.clicked.connect(self.send_gear_set)
        # 8.4d. The set-level group is drawn only where the tree HAS such a
        # command, and where it has not, the space it would have taken carries a
        # sentence about what the server can do instead. Not a disabled button
        # (a promise this tab cannot keep), not an empty space (which reads as a
        # tab that forgot), and not "not supported" (which says nothing a person
        # can act on): the entry's own `set_level_absent_reason`, measured with
        # the absence and stored beside it, so this view holds no English about
        # anybody's server.
        self.set_level_absent = QLabel("", actions)
        self.set_level_absent.setWordWrap(True)
        self.set_level_absent.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        # T179: a verb this tree withholds is neither drawn nor named on a button;
        # one line below the form names them and says why (the entry's own reason,
        # `services.characters_withheld`).
        withheld = self.services.characters_withheld
        self.characters_withheld_label = QLabel(_withheld_sentence(withheld), actions)
        self.characters_withheld_label.setWordWrap(True)
        self.characters_withheld_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.characters_withheld_label.setVisible(bool(withheld))
        for verb, widgets in (
            ("teleport", (self.teleport_where, self.teleport_button)),
            ("rename", (self.rename_button,)),
            ("revive", (self.revive_button,)),
            ("mail_gold", (self.gold_amount, self.mail_gold_button)),
            ("send_gear", (self.send_gear_button,)),
        ):
            if verb in withheld:
                for widget in widgets:
                    widget.setVisible(False)
        if "teleport" not in withheld:
            form.addRow("Teleport to", self.teleport_where)
            form.addRow(self.teleport_button)
        if "set_level" in withheld:
            self.new_level.setVisible(False)
            self.set_level_button.setVisible(False)
            self.set_level_absent.setVisible(False)
        elif self._set_level_command() is not None:
            form.addRow("Level", self.new_level)
            form.addRow(self.set_level_button)
            self.set_level_absent.setVisible(False)
        else:
            # Hidden as well as un-added: a widget with a parent and no layout
            # cell still draws itself at the corner of its parent, so leaving
            # the row out is not by itself leaving the control out.
            self.new_level.setVisible(False)
            self.set_level_button.setVisible(False)
            reason = self.entry.play.set_level_absent_reason if self.entry.play else None
            self.set_level_absent.setText(reason or "")
            self.set_level_absent.setVisible(bool(reason))
            if reason:
                form.addRow(self.set_level_absent)
        if "rename" not in withheld:
            form.addRow(self.rename_button)
        if "revive" not in withheld:
            form.addRow(self.revive_button)
        if "mail_gold" not in withheld:
            form.addRow("Gold", self.gold_amount)
            form.addRow(self.mail_gold_button)
        if "send_gear" not in withheld:
            form.addRow(self.send_gear_button)
        if withheld:
            form.addRow(self.characters_withheld_label)

        self.character_report = QLabel("", tab)
        self._character_generation = 0
        self._gear_generation = 0
        # One gear read at a time, and only the newest row waits behind it
        # (T96 review): see `_ask_for_gear()`.
        self._gear_in_flight = False
        self._gear_waiting: tuple[int, str] | None = None
        self.character_report.setWordWrap(True)
        self.character_report.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        # A tree whose Play block nobody has measured gets a SENTENCE rather
        # than disabled buttons: a control that cannot work is a promise this
        # tab cannot keep, and 8.4c and 8.4d are the boxes that make it work
        # there. Saying which game it is stops the sentence reading like a
        # fault in the app.
        if not wired:
            self.character_report.setText(
                f"{self.entry.name} has not had its character actions measured yet, so this tab "
                "shows none. They are read from a live server of this game, one box each, "
                "because a command that exists on one of these cores is not a command that "
                "exists on the next."
            )
        people.setVisible(wired)
        actions.setVisible(wired)
        for control in (self.character_list, self.refresh_characters_button):
            control.setVisible(wired)
        self._character_chosen(-1)

        # Two panels side by side: the roster on the left, the actions that
        # act on the chosen character on the right. The roster list is the
        # column whose content grows (hundreds of characters), so it sits in
        # its own panel; the action form is short and fixed. Both shrink on a
        # narrow window and the page scrolls rather than clipping.
        columns = QHBoxLayout()
        columns.setSpacing(12)
        columns.addWidget(people, 3)
        columns.addWidget(actions, 2)
        box.addLayout(columns)
        box.addWidget(self.character_report)
        box.addStretch(1)
        self._add_panel_tab(tab, "characters", "Characters")

    def _set_level_command(self) -> str | None:
        """This tree's set-level verb, or None where its console has no route.

        Read from the entry rather than decided by id, which is 8.4d's own
        clause: a tab that knows Tortoise by name is a tab that is wrong about
        the fifth game.
        """
        return self.entry.play.set_level_command if self.entry.play is not None else None

    def _character_actions(self) -> tuple[tuple[QPushButton, str], ...]:
        """The controls this TREE has, each with the verb that labels it.

        Pairs rather than two lists, because the two have to stay in step and a
        `zip(..., strict=True)` over a list that is now conditional would fail
        on the first selection instead of on the drawing. A button withheld
        here is withheld from the naming, the enabling and the tests at once.
        """
        every = (
            self.teleport_button,
            self.set_level_button,
            self.rename_button,
            self.revive_button,
            self.mail_gold_button,
            self.send_gear_button,
        )
        drawn = self._set_level_command() is not None
        withheld = self.services.characters_withheld
        return tuple(
            (button, label)
            for button, label, verb in zip(
                every, _CHARACTER_ACTIONS, play_module.VERBS, strict=True
            )
            if (drawn or button is not self.set_level_button) and verb not in withheld
        )

    def character_buttons(self) -> tuple[QPushButton, ...]:
        """Every control that acts on the chosen character, ON THIS TREE.

        One tuple, so the enabling, the naming and the tests all walk the same
        list -- a seventh button added to the form and forgotten here would be
        the one that stays enabled with nothing selected.
        """
        return tuple(button for button, _ in self._character_actions())

    def _character_chosen(self, row: int) -> None:
        """Name the chosen character in every button, or wait for one."""
        # Any gear read still out is about a row that is no longer chosen (T96).
        self._gear_generation += 1
        item = self.character_list.item(row) if row >= 0 else None
        if item is None:
            self._gear_waiting = None
            for button, label in self._character_actions():
                button.setText(label)
                button.setEnabled(False)
            return
        name = str(item.data(Qt.ItemDataRole.UserRole) or "")
        online = bool(item.data(Qt.ItemDataRole.UserRole + 1))
        # T188 A6: the box starts at this character's own level. It said 1 for
        # everybody, so one press of "Set level Guglu" took a 78 to level 1.
        level = item.data(_LEVEL_ROLE)
        if isinstance(level, int):
            self.new_level.setValue(level)
        for button, label in self._character_actions():
            button.setText(f"{label} {name}")
            button.setEnabled(True)
            # Cleared on every selection, not only set on the branches below: a
            # tooltip left behind from the previous row explains a refusal that
            # is no longer being made.
            button.setToolTip("")
        offline_rename = self._rename_offline_refusal()
        if not online and offline_rename:
            # 8.4d, and it is a sharper case than the revive one below: the
            # command is not merely ineffective offline on that fork, it does
            # something ELSE. `rename <char>` with no new name flags the rename
            # for a character who is logged in (`Commands.cpp:12612-12623`); for
            # one who is not, the same spelling runs
            # `UPDATE characters SET name = guid` (`:12624-12635`) and the name
            # is gone. So the refusal is the entry's, per tree, and it names
            # what the server would have done rather than only saying no.
            #
            # In two lengths, exactly as the `Ambiguous` refusal below is, and
            # for the same measured reason: the reader is a BUTTON. The entry's
            # sentence is ~200 characters, and 8.4c photographed a 180-character
            # one running off the end of the window
            # (`.notes/gates/8.4c-vanilla-m910q-2026-09-07/4-two-of-one-name.png`).
            # The short half is this view's because it is the same clause on
            # every tree that has such a refusal -- the field's own definition
            # is "what to say to a character who is NOT logged in" -- and it is
            # the shape the revive refusal beside it already takes. The measured
            # half, what THIS server would have done instead, stays the entry's
            # and is what a person gets when they ask.
            self.rename_button.setEnabled(False)
            self.rename_button.setText(f"{name} {_RENAME_OFFLINE_LABEL}")
            self.rename_button.setToolTip(offline_rename)
        if not online and not self._revive_works_offline():
            # Whether an offline revive does anything is a PER-TREE fact and the
            # entry carries it. It was a constant here, on the strength of a
            # reading taken on two other trees: `characters.health` before and
            # after, 0 and 0, "so the command does nothing". 8.4c looked at the
            # CORPSE on the Vanilla server instead and watched it go, on a
            # character that never logged in -- the offline branch is
            # `ConvertCorpseForPlayer`, which resurrects at the next login and
            # touches no health. Every other action here works offline; the
            # teleport's own help says so in as many words.
            self.revive_button.setEnabled(False)
            self.revive_button.setText(f"{name} has to be logged in to be revived")
        # The gear read is two `docker exec ... mysql` calls, so it runs through
        # the job runner and the button waits for it (T96). It ran right here
        # until then, on the GUI thread: ~240 ms per call on Docker Desktop
        # (yulon-win11, 2026-09-23), so every arrow key through the list, and
        # every refresh that kept a row selected, froze the window for about
        # half a second. Until the answer lands the button promises nothing.
        if "send_gear" in self.services.characters_withheld:
            return  # T179: not drawn, so nothing to read for it
        self.send_gear_button.setText(f"Reading what {name} is wearing…")
        self.send_gear_button.setEnabled(False)
        self._ask_for_gear(self._gear_generation, name)

    def _ask_for_gear(self, generation: int, name: str) -> None:
        """Start this row's gear read, or let it wait behind the one already out.

        ONE read at a time, and only the NEWEST row waits (Codex, T96 review):
        a read per row change meant an arrow key held down a 900-row roster
        started hundreds of workers and twice as many `docker exec`s, every one
        of them to be thrown away, and a closing tab then waited on all of them.
        A row that waits replaces whatever row waited before it; when the read
        out lands, `_next_gear_read()` starts the one waiting.
        """
        if self._gear_in_flight:
            self._gear_waiting = (generation, name)
            return
        self._gear_in_flight = True
        self._gear_waiting = None
        # Everything the worker needs is taken HERE, on the GUI thread: the
        # worker must not reach back into a view it may outlive.
        play, size = self.services.play, self._gear_set_size

        def read() -> tuple[int, str, tuple[int, int, tuple[str, str] | None]]:
            try:
                return (generation, name, size(play, name))
            except Exception as exc:  # noqa: BLE001 - carried to the GUI thread with its row
                raise _GearReadBroke(generation, name, exc) from exc

        self._run(read, self._gear_read, self._gear_read_failed)

    def _next_gear_read(self) -> None:
        """The read out has landed: start the row waiting behind it, if any.

        The row waiting is always the one chosen now: every choice replaces it,
        and choosing nothing clears it (`_character_chosen`). Nothing is
        started once the tab is closing -- `shutdown()` has joined what was
        running and nothing may be queued after it.
        """
        self._gear_in_flight = False
        waiting, self._gear_waiting = self._gear_waiting, None
        if waiting is None or getattr(self, "_closed", False):
            return
        self._ask_for_gear(*waiting)

    @Slot(object)
    def _gear_read(self, answer: object) -> None:
        """Draw the gear button from a read, if it is about the row still chosen."""
        generation, name, (pieces, mails, refusal) = cast(
            tuple[int, str, tuple[int, int, tuple[str, str] | None]], answer
        )
        self._next_gear_read()
        if generation != self._gear_generation:
            return
        self.send_gear_button.setToolTip("" if refusal is None else refusal[1])
        self.send_gear_button.setEnabled(True)
        if refusal is not None:
            # The read did not answer, and WHY is the only useful thing to draw.
            # Measured on the live Vanilla server, 2026-09-07 (8.4c): two
            # characters there are called Joleta, the read raised, and this
            # branch used to fall through to "wearing nothing" -- a sentence
            # about the character that was false, in place of a sentence about
            # the server that was true.
            self.send_gear_button.setText(refusal[0])
            self.send_gear_button.setEnabled(False)
        elif pieces:
            plural = "mail" if mails == 1 else "mails"
            self.send_gear_button.setText(f"Send {name}'s {pieces} worn items ({mails} {plural})")
        else:
            # Nothing worn is not a failure and not a thing to press: the
            # server would refuse an empty mail with a sentence about item ids.
            self.send_gear_button.setText(f"{name} is wearing nothing")
            self.send_gear_button.setEnabled(False)

    @Slot(object)
    def _gear_read_failed(self, exc: object) -> None:
        """`_gear_set_size()` answers its own failures; this is the boundary if it ever does not.

        For the row still chosen the button says the read broke -- disabled, as
        the refusal branch above is, because nothing safe is known to press --
        instead of staying on "Reading…" for good. A break about a row already
        left says nothing about the one chosen now.
        """
        logger.warning(f"could not size the chosen character's gear: {exc}")
        self._next_gear_read()
        if not isinstance(exc, _GearReadBroke) or exc.generation != self._gear_generation:
            return
        self.send_gear_button.setText(f"Could not read what {exc.name} is wearing")
        self.send_gear_button.setToolTip(str(exc.cause))
        self.send_gear_button.setEnabled(False)

    def _revive_works_offline(self) -> bool:
        """Only where this tree's own box measured that it does.

        A missing block and an unmeasured field both mean "do not offer it",
        which is the same answer for the same reason: nobody has run the command
        against that server and watched what it did.
        """
        return bool(self.entry.play is not None and self.entry.play.revive_offline)

    def _rename_offline_refusal(self) -> str:
        """What to say instead of flagging a rename on a character who is out.

        Empty on every tree whose entry carries no such refusal, which is the
        honest default here and not the one `revive_offline` takes: a rename
        flag is offered until a tree has been measured to do something worse,
        and this app has watched three trees do the harmless thing.
        """
        play = self.entry.play
        return (play.rename_offline_refusal or "") if play is not None else ""

    @staticmethod
    def _gear_set_size(play: object, name: str) -> tuple[int, int, tuple[str, str] | None]:
        """The set's size, or why there is not one -- short enough for the
        button, and in full for the tooltip behind it.

        Static, and handed the seam: it runs on a worker (T96), which must not
        read the view -- a view being torn down has already lost `services`.
        """
        if play is None:
            return (0, 0, None)
        try:
            pieces, mails = play.gear_set_size(name)  # type: ignore[attr-defined]
            return (int(pieces), int(mails), None)
        except play_module.Ambiguous as exc:
            logger.info(f"could not size {name}'s gear: {exc}")
            return (0, 0, (exc.summary, str(exc)))
        except Exception as exc:  # noqa: BLE001 - a read that failed is not a press
            logger.info(f"could not size {name}'s gear: {exc}")
            return (0, 0, (f"Could not read what {name} is wearing", str(exc)))

    def _chosen_character(self) -> str:
        item = self.character_list.currentItem()
        if item is None:
            return ""
        return str(item.data(Qt.ItemDataRole.UserRole) or "")

    def refresh_attempts_after_an_action(self) -> int:
        """How many re-reads one successful action schedules. A bound, not a promise."""
        return _ROW_SETTLE_TRIES

    @Slot()
    def refresh_characters(self) -> None:
        play = self.services.play
        if play is None:
            return
        self._character_generation += 1
        generation = self._character_generation
        # The generation rides WITH the answer rather than in a lambda around the
        # callback (T97). A lambda handed to the runner is delivered on the
        # worker thread, so the list was cleared and refilled there while this
        # thread painted it: on m910q, 2026-09-23, Send gold on a 900-character
        # bot server segfaulted the app in 4 of 5 runs.
        self._run(
            lambda: (generation, play.listing()),  # type: ignore[attr-defined]
            self._characters_arrived,
            self._characters_failed,
        )

    @Slot(object)
    def _characters_arrived(self, answer: object) -> None:
        """A list read, on the GUI thread, with the generation it was asked at."""
        generation, listed = cast(tuple[int, object], answer)
        self._characters_listed_at(generation, listed)

    def _characters_listed_at(self, generation: int, listed: object) -> None:
        """Take this answer only if it is the newest one asked for.

        Two actions in quick succession schedule two reads, and the older one
        can land after the newer: without this, the list would end up showing
        the earlier state and stay there (8.4a's adversarial review).
        """
        if generation != self._character_generation:
            logger.info("a stale character list arrived and was dropped")
            return
        self._characters_listed(listed)

    def _refresh_until_it_changes(self, before: tuple[str, ...], attempt: int = 1) -> None:
        """Re-read the list until it differs from `before`, up to the bound.

        The server answers about 0.15s before its own row is written, so the
        first read after an action can legitimately show the old state; a
        second and a third cost nothing and cover a machine slower than the one
        this was measured on.

        T213: a self-re-arming timer like the Modules version walk, with the
        same two guards. A tab removed between two reads (`shutdown()`, then
        `deleteLater()`) ends the sequence here, and the timer names `self` as
        its context object so a read queued before the delete is dropped by
        Qt instead of clearing a deleted list.
        """
        if getattr(self, "_closed", False):
            return
        self.refresh_characters()
        if self._character_rows() != before or attempt >= _ROW_SETTLE_TRIES:
            return
        QTimer.singleShot(
            _ROW_SETTLE_MS, self, lambda: self._refresh_until_it_changes(before, attempt + 1)
        )

    def _character_rows(self) -> tuple[str, ...]:
        return tuple(
            self.character_list.item(row).text() for row in range(self.character_list.count())
        )

    @Slot(object)
    def _characters_listed(self, listed: object) -> None:
        chosen = self._chosen_character()
        self.character_list.clear()
        for character in listed:  # type: ignore[attr-defined]
            where = "online" if character.online else "offline"
            item = QListWidgetItem(
                f"{character.name} — level {character.level} — {where} — {character.account}"
            )
            item.setData(Qt.ItemDataRole.UserRole, character.name)
            item.setData(Qt.ItemDataRole.UserRole + 1, bool(character.online))
            item.setData(_LEVEL_ROLE, int(character.level))
            self.character_list.addItem(item)
            if character.name == chosen:
                self.character_list.setCurrentItem(item)
        if self.character_list.currentRow() < 0:
            self._character_chosen(-1)

    @Slot(object)
    def _characters_failed(self, exc: object) -> None:
        self.character_report.setText(f"Could not read this server's characters: {exc}")

    def _character_action(self, what: str, run: object, *, level: int | None = None) -> None:
        """One press, one sentence, all three outcomes.

        `level` is a Set level press's level: on success the row keeps it at
        once (T188 fix round 1), because the list is re-read 750ms later and a
        lower level typed in between was compared with the old one. It rides
        WITH this job's answer (`_CharacterAnswer`), never in a field on the
        tab: the buttons stay live while an action runs, so a second press can
        overlap the first, and a shared field was overwritten by it.
        """
        name = self._chosen_character()
        if self.services.play is None or not name:
            return
        sent = (name, level) if level is not None else None
        self.character_report.setText(f"{what} {name}…")
        self._run(
            lambda: _CharacterAnswer(run(), sent),  # type: ignore[operator]
            self._character_done,
            self._characters_failed,
        )

    @Slot(object)
    def _character_done(self, answer: object) -> None:
        outcome, sent = cast(_CharacterAnswer, answer)
        done = bool(getattr(outcome, "done", False))
        said = getattr(outcome, "text", "") if done else getattr(outcome, "problem", "")
        self.character_report.setText(said.strip() or ("Done." if done else "It did not work."))
        if done and sent is not None:
            for row in range(self.character_list.count()):
                item = self.character_list.item(row)
                if item.data(Qt.ItemDataRole.UserRole) == sent[0]:
                    item.setData(_LEVEL_ROLE, sent[1])
        if done:
            # NOT `self.refresh_characters()`. Measured on the live server,
            # 2026-09-07: the command answers in about 0.15s and its own row
            # lands about 0.1s after that, so a list re-read the moment the
            # answer arrives shows the state BEFORE the thing that was just
            # done -- "You change the level of Aevret to 60" above a row still
            # reading 55, which reads as the action having failed.
            rows = self._character_rows()
            QTimer.singleShot(_ROW_SETTLE_MS, self, lambda: self._refresh_until_it_changes(rows))

    @Slot()
    def teleport_character(self) -> None:
        play, name = self.services.play, self._chosen_character()
        where = self.teleport_where.text().strip()
        if play is None or not name:
            return
        self._character_action(
            "Teleporting", lambda: play.teleport(name, where)  # type: ignore[attr-defined]
        )

    @Slot()
    def set_character_level(self) -> None:
        play, name = self.services.play, self._chosen_character()
        level = self.new_level.value()
        if play is None or not name:
            return
        # T188 A6: lowering resets the character's experience, the one change a
        # second press cannot undo, so it alone is asked about. Default No.
        item = self.character_list.currentItem()
        now = item.data(_LEVEL_ROLE) if item is not None else None
        if (
            isinstance(now, int)
            and level < now
            and not self._confirm(
                f"Lower {name}'s level?",
                f"Set {name} from level {now} down to level {level}? Lowering a level "
                "resets the character's experience, and raising it again does not "
                "give that back.",
            )
        ):
            return
        self._character_action(
            "Setting the level of",
            lambda: play.set_level(name, level),  # type: ignore[attr-defined]
            level=level,
        )

    @Slot()
    def rename_character(self) -> None:
        play, name = self.services.play, self._chosen_character()
        if play is None or not name:
            return
        self._character_action(
            "Marking for rename", lambda: play.rename(name)  # type: ignore[attr-defined]
        )

    @Slot()
    def revive_character(self) -> None:
        play, name = self.services.play, self._chosen_character()
        if play is None or not name:
            return
        self._character_action("Reviving", lambda: play.revive(name))  # type: ignore[attr-defined]

    @Slot()
    def mail_gold(self) -> None:
        play, name = self.services.play, self._chosen_character()
        gold = self.gold_amount.value()
        if play is None or not name:
            return
        self._character_action(
            "Sending gold to",
            lambda: play.mail_gold(  # type: ignore[attr-defined]
                name, gold=gold, subject="A gift", body="From the server owner"
            ),
        )

    @Slot()
    def send_gear_set(self) -> None:
        play, name = self.services.play, self._chosen_character()
        if play is None or not name:
            return
        self._character_action(
            "Sending the worn items of",
            lambda: play.send_gear_set(  # type: ignore[attr-defined]
                name, to=name, subject="Your gear", body="Everything you were wearing"
            ),
        )

    def _account_chosen(self, row: int) -> None:
        """Both changes act on the chosen account, so both wait for one."""
        chosen = row >= 0 and self.account_list.item(row) is not None
        self.set_password_button.setEnabled(chosen)
        self.set_gm_button.setEnabled(chosen)
        if chosen:
            item = self.account_list.item(row)
            self.selected_gm.setValue(int(item.data(Qt.ItemDataRole.UserRole + 1) or 0))

    def _chosen_account(self) -> str:
        item = self.account_list.currentItem()
        if item is None:
            return ""
        return str(item.data(Qt.ItemDataRole.UserRole) or "")

    @Slot()
    def refresh_accounts(self) -> None:
        """Read the list, off the GUI thread: it is a `docker exec` and a query."""
        admin = self.services.accounts
        if admin is None:
            return
        self._run(admin.listing, self._accounts_listed, self._accounts_failed)

    @Slot(object)
    def _accounts_listed(self, listing: object) -> None:
        chosen = self._chosen_account()
        self.account_list.clear()
        problem = getattr(listing, "problem", "")
        if problem:
            # Cleared first: an old list under a new error would be read as the
            # current accounts, which is exactly the thing the problem says not
            # to trust.
            self.account_report.setText(problem)
            self._account_chosen(-1)
            return
        for account in getattr(listing, "accounts", []):
            item = QListWidgetItem(
                f"{account.username} — id {account.id} — GM level {account.gm_level}"
            )
            item.setData(Qt.ItemDataRole.UserRole, account.username)
            item.setData(Qt.ItemDataRole.UserRole + 1, account.gm_level)
            self.account_list.addItem(item)
            if account.username == chosen:
                self.account_list.setCurrentItem(item)
        if self.account_list.currentRow() < 0:
            self._account_chosen(-1)

    @Slot(object)
    def _accounts_failed(self, exc: object) -> None:
        self.account_report.setText(f"Could not read this server's accounts: {exc}")

    @Slot()
    def set_selected_password(self) -> None:
        """Ask the server to change the chosen account's password.

        The field is cleared for the reason `create_account` clears its own: a
        password left in a widget is a password in every later repr and
        traceback frame of that widget.
        """
        admin = self.services.accounts
        account = self._chosen_account()
        if admin is None or not account:
            return
        password = self.selected_password.text()
        self.selected_password.clear()
        self.account_report.setText(f"Changing {account}'s password…")
        self._run(
            lambda: admin.set_password(account, password),
            self._account_changed,
            self._accounts_failed,
        )

    @Slot()
    def set_selected_gm_level(self) -> None:
        admin = self.services.accounts
        account = self._chosen_account()
        if admin is None or not account:
            return
        level = self.selected_gm.value()
        self.account_report.setText(f"Setting {account} to GM level {level}…")
        self._run(
            lambda: admin.set_gm_level(account, level),
            self._account_changed,
            self._accounts_failed,
        )

    @Slot(object)
    def _account_changed(self, outcome: object) -> None:
        """Say what came back, and re-read the list when something changed.

        Without the re-read the tab keeps showing the level the account no
        longer has — and the list is where a person checks that the change
        landed.
        """
        if getattr(outcome, "done", False):
            self.account_report.setText(getattr(outcome, "text", "") or "Done.")
            self.refresh_accounts()
            return
        problem = getattr(outcome, "problem", "") or "the server did not say what went wrong"
        self.account_report.setText(problem)
        # `action_failed` is what the rest of the app treats as "that did not
        # happen", and a timeout is not that: the server keeps working on a
        # command after this app stops waiting. The sentence is shown either
        # way; only the signal is withheld.
        if not getattr(outcome, "indeterminate", False):
            self.action_failed.emit(problem)

    @Slot()
    def create_account(self) -> None:
        """Write the account row directly, rather than typing it at the console.

        This is the only way to make an account on a platform with no pty, and
        the only way to make the FIRST one anywhere — SOAP needs an account
        before it will authenticate, so it cannot bootstrap itself.
        """
        name = self.account_name.text().strip()
        password = self.account_password.text()
        if not name or not password:
            # The signal alone left the button doing nothing at all
            # (review, 2026-08-22), so the tab says it as well.
            self.account_report.setText("Username and password are required.")
            self.action_failed.emit("username and password are required")
            return
        gm_level = self.account_gm.value()
        self.account_report.setText(f"Creating {name}…")
        self.create_account_button.setEnabled(False)
        # The password is passed straight into the call and the field cleared; it
        # is never stored on the view, so no later repr or traceback frame of
        # this widget can carry it.
        self._run(
            lambda: self.services.create_account(name, password, gm_level),
            self._account_done,
            self._account_failed,
        )
        self.account_password.clear()

    @Slot(object)
    def _account_done(self, result: object) -> None:
        self.create_account_button.setEnabled(True)
        if not isinstance(result, wotlk_accounts.AccountResult):
            return
        made = "created" if result.created else "already existed"
        gm = f", GM level {result.gm_level}" if result.gm_level else ""
        self.account_report.setText(f"{result.username}: {made} (id {result.account_id}){gm}.")

    @Slot(object)
    def _account_failed(self, exc: object) -> None:
        self.create_account_button.setEnabled(True)
        self.account_report.setText(f"Could not create the account: {exc}")
        self.action_failed.emit(str(exc))

    # --------------------------------------------------------------- bots tab

    def _build_bots_tab(self) -> None:
        """Browsing the bots, and My Party (8.6) under it — the design's two groups.

        Browse is absent rather than empty for a game whose marker this app has
        not measured: without one the only honest list is every character on the
        server, which on this install is 900 rows of which 500 are the answer.

        The whole TAB is absent only when neither group has a seam. Written that
        way rather than on `bots` alone because `services` is a dataclass and
        anything can be handed to it: the factories only ever wire My Party
        where the bot marker is measured too (`InstallParty.for_entry_is_possible`
        requires `observability`, which is the same fact `bots` rides on), and a
        capability that vanished because the OTHER group's seam was missing is
        exactly the shape of bug this tab must not have.
        """
        # T99's box exists whether or not the tab does, so `_set_busy()` and
        # the Tuning tab's banner can always reach it; the tab is what shows it.
        self._build_bot_count_group()
        if (
            self.services.bots is None
            and self.services.my_party is None
            and self.services.bot_population is None
        ):
            return
        tab = QWidget(self)
        box = QVBoxLayout(tab)
        self.bot_count_group.setParent(tab)
        self.bot_count_group.setVisible(self.services.bot_population is not None)
        box.addWidget(self.bot_count_group)
        browse = QGroupBox("Browse the bots", tab)
        browse_box = QVBoxLayout(browse)
        self.bot_summary = QLabel("", browse)
        self.bot_summary.setWordWrap(True)
        self.bot_list = QListWidget(browse)
        self.bot_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.bot_list.customContextMenuRequested.connect(self._show_bot_context_menu)
        row = QHBoxLayout()
        self.bot_filter = QLineEdit(browse)
        self.bot_filter.setPlaceholderText("name begins with…")
        self.bot_filter.returnPressed.connect(self.filter_bots)
        self.filter_bots_button = QPushButton("Find", browse)
        self.filter_bots_button.clicked.connect(self.filter_bots)
        self.previous_bots_button = QPushButton("Previous", browse)
        self.previous_bots_button.clicked.connect(self.previous_bot_page)
        self.next_bots_button = QPushButton("Next", browse)
        self.next_bots_button.clicked.connect(self.next_bot_page)
        # The filter is the row's one input, so it takes the spare width; the
        # three buttons stay at their own size. Without the stretch the line
        # edit collapsed to its minimum (~60px), which reads as a too-small,
        # hard-to-see textbox inside a two-column panel.
        row.addWidget(self.bot_filter, 1)
        row.addWidget(self.filter_bots_button)
        row.addWidget(self.previous_bots_button)
        row.addWidget(self.next_bots_button)
        browse_box.addWidget(self.bot_summary)
        browse_box.addWidget(self.bot_list)
        browse_box.addLayout(row)
        browse.setVisible(self.services.bots is not None)
        # A stack of cursors, one per page seen. There is no arithmetic that
        # turns "where page three starts" into "where page two starts", so the
        # only way back is the key the earlier page was read with.
        self._bot_cursors: list[tuple[str, int] | None] = [None]
        self._bot_next: tuple[str, int] | None = None
        self._bot_total: int | None = None
        self._show_page_buttons()
        # Two panels side by side: the bot roster on the left, My Party on the
        # right. Both are group boxes already; giving them equal columns lets a
        # long roster and a full party panel share the tab comfortably with
        # ample room for all inputs and dropdowns.
        columns = QHBoxLayout()
        columns.setSpacing(12)
        columns.addWidget(browse, 1)
        columns.addWidget(self._build_my_party_group(tab), 1)
        box.addLayout(columns, 1)
        if self.services.bot_dashboard is not None:
            box.addWidget(self._build_bot_dashboard_group(tab))
        self._bots_tab: QWidget | None = tab
        self._add_panel_tab(tab, "bots", "Bots")

    def _build_bot_count_group(self) -> None:
        """ "Random bots: [N] [Apply…]", the note under it, and the job it owes (T99).

        The box sets Min = Max = N, as the install does. Its value is read off
        the disk by a job (`_look_up_bot_count`, started by the Tuning tab's
        reload) and it stays dead until that answer lands, while any job of
        ours runs, and while its own write runs.
        """
        self._bot_count_generation = 0
        self._bot_count_reading: botpop.Reading | None = None
        self._bot_count_pending = False
        self._bot_count_writing = False
        self._bot_rows: tuple[tuning.TuningRow, ...] = ()
        self.bot_count_group = QGroupBox(BOT_COUNT_TITLE, self)
        inside = QVBoxLayout(self.bot_count_group)
        row = QHBoxLayout()
        row.addWidget(QLabel(BOT_COUNT_LABEL, self.bot_count_group))
        self.bot_count_box = QSpinBox(self.bot_count_group)
        self.bot_count_box.setRange(0, botpop.NO_CEILING)
        self.bot_count_box.setEnabled(False)
        self.bot_count_apply_button = QPushButton(BOT_COUNT_APPLY, self.bot_count_group)
        self.bot_count_apply_button.clicked.connect(self.apply_bot_count)
        self.bot_count_apply_button.setEnabled(False)
        # The job this change owes, the Tuning banner's own button mirrored:
        # the player is on THIS tab when the write lands, and a restart they
        # are told about on another tab is a restart they do not find.
        self.bot_count_owed_button = QPushButton("", self.bot_count_group)
        self.bot_count_owed_button.clicked.connect(self._tuning_banner_pressed)
        self.bot_count_owed_button.setVisible(False)
        row.addWidget(self.bot_count_box)
        row.addWidget(self.bot_count_apply_button)
        row.addStretch(1)
        row.addWidget(self.bot_count_owed_button)
        # Two lines: what the files say NOW (every read rewrites it), and what
        # the last press did (only a press writes it), so a read landing after
        # a write cannot wipe the write's report.
        self.bot_count_note = QLabel("", self.bot_count_group)
        self.bot_count_note.setWordWrap(True)
        self.bot_count_note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.bot_count_report = QLabel("", self.bot_count_group)
        self.bot_count_report.setWordWrap(True)
        self.bot_count_report.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        inside.addLayout(row)
        inside.addWidget(self.bot_count_note)
        inside.addWidget(self.bot_count_report)
        self._build_bot_rebuild(inside)

    def _build_bot_rebuild(self, inside: QVBoxLayout) -> None:
        """T144: "Rebuild random bots…", its report line and its own log, under the count.

        Absent -- not greyed -- without the seam, which is every game but
        Tortoise: nothing would ever enable it there. Its own log panel rather
        than the Modules tab's, because the press is made on this tab and the
        job runs for minutes; the panel's start and finish lock and unlock the
        Server tab through `_set_busy`, like every long job of ours.
        """
        self.bot_rebuild_button: QPushButton | None = None
        self.bot_rebuild_report: QLabel | None = None
        self.bot_rebuild_log: _IdleLogPanel | None = None
        self._bots_tab = None
        if self.services.bot_pool_rebuild is None:
            return
        group = self.bot_count_group
        row = QHBoxLayout()
        self.bot_rebuild_button = QPushButton(REBUILD_BOTS_LABEL, group)
        self.bot_rebuild_button.setToolTip(
            "Delete every random bot and let the bots module make them again, so they get the "
            "module's newest behaviour. Asks first, offers a backup, and restarts the server."
        )
        self.bot_rebuild_button.clicked.connect(self.rebuild_random_bots)
        row.addWidget(self.bot_rebuild_button)
        row.addStretch(1)
        self.bot_rebuild_report = QLabel("", group)
        self.bot_rebuild_report.setWordWrap(True)
        self.bot_rebuild_report.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        log = _IdleLogPanel(group)
        log.run_started.connect(self._bot_rebuild_started)
        log.run_finished.connect(self._bot_rebuild_finished)
        self.bot_rebuild_log = log
        inside.addLayout(row)
        inside.addWidget(self.bot_rebuild_report)
        inside.addWidget(log)

    def _set_bot_rebuild_button(self) -> None:
        """Live whenever nothing of ours is running: the press itself asks everything else."""
        if self.bot_rebuild_button is not None:
            self.bot_rebuild_button.setEnabled(not self._busy)

    def _bot_rebuild_refusal(self) -> str | None:
        """Why the rebuild (or its owed restart) may not start now, or None.

        `forget_refusal()` is this view's one answer to "is anything running
        on this server": a restore, a backup, the bot-count write, a Tuning
        reset, a Modules job, a network apply, the rebuild panel, any Server
        action -- every one of which either writes `etc/aiplayerbot.conf` or
        would have the world restarted under it. Plus this group's own panel.
        """
        log = self.bot_rebuild_log
        if log is not None and log.running:
            return "The random bots are already being rebuilt. Wait for it to finish."
        reason = self.forget_refusal()
        if reason is None:
            return None
        return reason.replace("Nothing was removed.", "Nothing was changed.")

    @Slot()
    def rebuild_random_bots(self, restart_owed: bool = False) -> bool:
        """Ask the one question, then run the rebuild in this group's log. False if not run (T144).

        The same three answers as the update's (`ask_backup_choice()`), Cancel
        the default and the Escape button. The backup, when asked for, is the
        first step OF the job -- `_backup_with_the_database()`, which starts
        the database alone if the server is down -- and a failed one stops it
        before anything is written. `restart_owed`: see `_offer_bot_rebuild_after_update`.
        """
        seam = self.services.bot_pool_rebuild
        log = self.bot_rebuild_log
        if seam is None or log is None:
            return False
        refusal = self._bot_rebuild_refusal()
        if refusal is not None:
            QMessageBox.information(self, "Something else is running", refusal)
            return False
        choice = ask_backup_choice(
            self,
            REBUILD_BOTS_TITLE,
            REBUILD_BOTS_TEXT,
            back_up_first="Back up first, then rebuild",
            without_backup="Rebuild without a backup",
        )
        if choice is UpdateChoice.CANCEL:
            logger.info(f"rebuilding {self.entry.id}'s random bots declined at the confirmation")
            return False
        backup = self._backup_with_the_database if choice is UpdateChoice.BACK_UP_FIRST else None
        cancel = threading.Event()
        if self._bots_tab is not None:
            # The offer after an update is answered on the Modules tab; the
            # job's lines are here.
            self._tabs.setCurrentWidget(self._bots_tab)
        if self.bot_rebuild_report is not None:
            self.bot_rebuild_report.setText("")
        return log.run(
            lambda: seam.rebuild(backup=backup, cancel=cancel, restart_owed=restart_owed),
            title=f"Rebuilding {self.entry.name}'s random bots",
            cancel=cancel,
            record_as=f"rebuild-bots-{self.entry.id}-"
            f"{composegen.install_id(self.services.controller.server_dir)}",
        )

    @Slot()
    def _bot_rebuild_started(self) -> None:
        self._set_busy(True)
        self._set_bot_rebuild_button()

    @Slot(bool, str)
    def _bot_rebuild_finished(self, ok: bool, message: str) -> None:
        """Unlock; a refusal goes on the report line and to the app log, where a user looks."""
        self._set_busy(False)
        self._set_bot_rebuild_button()
        if self.bot_rebuild_report is None:
            return
        if not ok:
            self.bot_rebuild_report.setText(message)
            self.action_failed.emit(message)
            return
        cancelled = self.bot_rebuild_log is not None and self.bot_rebuild_log.cancelled
        self.bot_rebuild_report.setText(
            "Stopped. The log below says how far it got." if cancelled else ""
        )

    def _refused_during_bot_rebuild(self) -> bool:
        """Back up and Restore, refused while the random bots are rebuilt (T144). True if refused.

        Gated on the rebuild's panel ONLY, and deliberately narrower than
        `forget_refusal()`: those two presses have never been gated on `_busy`
        or the other jobs, and widening that is a change to every game's
        Maintenance tab that this ticket does not own. The rebuild is the one
        job that restarts the world under a dump or a restore on purpose, and
        its request lives in the characters database a restore overwrites.
        """
        log = self.bot_rebuild_log
        if log is None or not log.running:
            return False
        QMessageBox.information(self, "Something else is running", BOT_REBUILD_RUNNING)
        self.maintenance_report.setPlainText(BOT_REBUILD_RUNNING)
        return True

    def _offer_bot_rebuild_after_update(self, move: tortoise_botpool.Move) -> None:
        """T144: the update just moved TortoiseBots. Ask, No by default; Yes is the button's press.

        The press's own three-way dialog follows a Yes, and that is on
        purpose: it is the one that says everything the rebuild loses, and it
        offers a backup taken NOW -- the update's own backup, if one was taken,
        predates the new server's first start and T123's enrolment.

        **One restart on the update path** (approved design point 3). When
        T123 enrolled accounts it left the restart that loads them OWED: a
        rebuild's own restart covers it, and every other answer -- No, Escape,
        Cancel on the rebuild dialog, a press refused -- runs the owed restart
        on its own, once.
        """
        if not self._confirm(REBUILD_BOTS_TITLE, REBUILD_BOTS_AFTER_UPDATE):
            logger.info(f"rebuilding {self.entry.id}'s random bots declined after the update")
            if move.restart_owed:
                self._run_owed_restart()
            return
        if not self.rebuild_random_bots(restart_owed=move.restart_owed) and move.restart_owed:
            self._run_owed_restart()

    def _run_owed_restart(self) -> None:
        """T123's restart, made now in this group's log -- or said, when it cannot start here."""
        seam = self.services.bot_pool_rebuild
        log = self.bot_rebuild_log
        if seam is None or log is None or self._bot_rebuild_refusal() is not None:
            self._say_restart_owed()
            return
        cancel = threading.Event()
        log.run(
            lambda: seam.restart_owed_now(cancel),
            title=f"Restarting {self.entry.name} for the enrolled bots",
            cancel=cancel,
        )

    def _say_restart_owed(self) -> None:
        """The owed restart could not be made: never dropped silently."""
        QMessageBox.information(self, "Restart the server", RESTART_OWED_LEFT)
        if self.bot_rebuild_report is not None:
            self.bot_rebuild_report.setText(RESTART_OWED_LEFT)

    def _set_bot_count_controls(self) -> None:
        """Live with a readable count, nothing of ours running, and no read or write in flight."""
        reading = self._bot_count_reading
        live = (
            self.services.bot_population is not None
            and reading is not None
            and reading.problem is None
            and not self._busy
            and not self._bot_count_pending
            and not self._bot_count_writing
        )
        self.bot_count_box.setEnabled(live)
        self.bot_count_apply_button.setEnabled(live)
        self._set_bot_rebuild_button()

    def _look_up_bot_count(self) -> None:
        """Read the bot count (and the Tuning tab's bot rows) off the GUI thread."""
        route = self.services.bot_population
        if route is None or self._waits_for_the_distro("bot count", self._look_up_bot_count):
            return
        self._bot_count_generation += 1
        self._bot_count_pending = True
        self._set_bot_count_controls()
        self._run(
            partial(_read_bot_count, route, self._bot_count_generation),
            self._bot_count_read,
            self._bot_count_read_failed,
        )

    @Slot(object)
    def _bot_count_read(self, answer: object) -> None:
        """The box, its note and the Tuning tab's bot card, from one read; stale answers dropped."""
        if (
            not isinstance(answer, BotCountAnswer)
            or answer.generation != self._bot_count_generation
        ):
            return
        self._bot_count_pending = False
        reading = answer.reading
        self._bot_count_reading = reading
        if reading.problem is not None:
            self.bot_count_note.setText(f"Cannot change the bot count here: {reading.problem}")
        else:
            # Never past what a QSpinBox holds (a C int): `read()` clamps its
            # ceiling, and the value is clamped again here (Codex medium).
            top = min(reading.ceiling, botpop.NO_CEILING)
            self.bot_count_box.setRange(0, top)
            if reading.max is not None:
                self.bot_count_box.setValue(max(0, min(reading.max, top)))
            self.bot_count_box.setToolTip(f"0 to {top}: {reading.ceiling_why}")
            self.bot_count_note.setText(self._bot_count_now(reading))
        self._set_bot_count_controls()
        rows = reading.rows
        if rows != self._bot_rows:
            self._bot_rows = rows
            self.tuning_panel.set_cards(build_tuning_cards(self._all_tuning_rows()))
            self._set_tuning_revert_all()

    def _bot_count_now(self, reading: botpop.Reading) -> str:
        """What the note says the server is set to now, and what the install wrote."""
        name = Path(reading.file).name
        installed = botpop.installed_count(self.entry)
        said = f" (Yu'lon installs {installed})" if installed is not None else ""
        if reading.max is None:
            return f"{name} names no random bot count; Apply writes one{said}."
        if reading.min is not None and reading.min != reading.max:
            return (
                f"Now between {reading.min} and {reading.max} in {name}{said}. Apply sets both "
                "to the one number in the box."
            )
        return f"Now {reading.max} in {name}{said}."

    @Slot(object)
    def _bot_count_read_failed(self, exc: object) -> None:
        """A read that raised: said, unless a newer read is pending (then it is stale and dropped).

        An untagged failure cannot say which read it was, so it is logged and
        never frees the box: the next reload asks again.
        """
        generation = getattr(exc, "generation", None)
        if generation != self._bot_count_generation:
            logger.warning(f"a stale or untagged bot-count read failed: {exc}")
            return
        self._bot_count_pending = False
        self.bot_count_note.setText(f"Could not read this server's bot count: {exc}")
        self._set_bot_count_controls()

    @Slot()
    def apply_bot_count(self) -> None:
        """Ask once, then write Min = Max = the box's number on the job runner (T99)."""
        route = self.services.bot_population
        reading = self._bot_count_reading
        if (
            route is None
            or reading is None
            or reading.problem is not None
            or self._busy
            or self._bot_count_pending
            or self._bot_count_writing
        ):
            return
        n = self.bot_count_box.value()
        if not self._confirm(BOT_COUNT_TITLE, botpop.question(self.entry, reading, n)):
            return
        self._bot_count_writing = True
        self._set_bot_count_controls()
        self.bot_count_report.setText(f"setting the random bots to {n}…")
        self._run(partial(route.write, n), self._bot_count_written, self._bot_count_failed)

    @Slot(object)
    def _bot_count_written(self, result: object) -> None:
        self._bot_count_writing = False
        if isinstance(result, botpop.Written):
            name = Path(result.file).name
            if result.backup is None:
                self.bot_count_report.setText(
                    f"{name} already says {result.after}, so nothing was written."
                )
            else:
                self._note_tuning_owed(result.file, result.rule)
                route: botpop.Route = "conf" if result.rule == "restart" else "env"
                self.bot_count_report.setText(
                    f"Random bots set to {result.after} in {name}; the file as it was is beside "
                    f"it at {result.backup.name}.\n" + botpop.when_it_counts(route, result.after)
                )
        # Read again: the box, its note and the Tuning tab's bot card say what the disk says.
        self._look_up_bot_count()

    @Slot(object)
    def _bot_count_failed(self, exc: object) -> None:
        """A refusal `write()` raised (it wrote nothing), or a bug: said, and the box read again."""
        self._bot_count_writing = False
        self.bot_count_report.setText(f"The bot count was NOT changed: {exc}")
        self.action_failed.emit(str(exc))
        self._look_up_bot_count()

    # ------------------------------------------------- T171: server time zone

    def _build_time_zone_group(self) -> None:
        """ "Server time zone": [where ▾] [place ▾] / [Apply…] Now … (T171).

        Two lists, not one of 400 zones: the first says "Same as this
        computer", "UTC", a hand-written value kept as it is, or a region, and
        the second the places in that region -- a short walk for a pad or the
        keyboard (a closed list jumps to a typed letter). Never a text box: a
        typed zone is how a player broke the file (owner decision 2026-09-28).
        Dead until the read lands, while any job of ours runs, and while its
        own write runs, as the bot count's box is.

        It sits at the top of the Tuning tab's card column and scrolls with the
        cards (`TuningPanel.set_header`), cold review round 2: as a row of its
        own above the panel it cost 50px, and at 960x640 the panel's minimum
        (360 of a 466px tab) left 12 to spare, so the panel was drawn under its
        minimum; and one line of both lists and the press needs ~750px, which
        the column does not have at 1280x800 (536). Compact inside: two lines,
        the status a few words with the whole sentence as its tooltip, both
        lists sized to a fixed number of characters (a long hand-written value
        is elided, whole in the tooltip), and what a press did goes to the
        tab's own report box.
        """
        self._time_zone_generation = 0
        self._time_zone_reading: server_time_zone.Reading | None = None
        self._time_zone_pending = False
        self._time_zone_writing = False
        self.time_zone_group = QGroupBox(TIME_ZONE_TITLE, self)
        grid = QGridLayout(self.time_zone_group)
        self.time_zone_where = QComboBox(self.time_zone_group)
        self.time_zone_place = QComboBox(self.time_zone_group)
        for box, chars in (
            (self.time_zone_where, len(TIME_ZONE_HOST.format(zone=time_zone.host_zone()))),
            (self.time_zone_place, 16),
        ):
            box.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            box.setMinimumContentsLength(chars)
        self.time_zone_apply_button = QPushButton(TIME_ZONE_APPLY, self.time_zone_group)
        self.time_zone_apply_button.clicked.connect(self.apply_time_zone)
        self.time_zone_note = QLabel("", self.time_zone_group)
        self.time_zone_note.setWordWrap(False)
        self.time_zone_note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        # Two lines, so the column's width at 1280x800 (536px) holds both lists
        # at a readable size: "Same as this computer (…)" on its own, then the
        # place, the press, and the status.
        grid.addWidget(self.time_zone_where, 0, 0, 1, 3, Qt.AlignmentFlag.AlignLeft)
        grid.addWidget(self.time_zone_place, 1, 0)
        grid.addWidget(self.time_zone_apply_button, 1, 1)
        grid.addWidget(self.time_zone_note, 1, 2)
        grid.setColumnStretch(2, 1)
        self._fill_time_zone_where(None)
        self.time_zone_where.currentIndexChanged.connect(self._time_zone_where_changed)
        self.time_zone_place.currentIndexChanged.connect(self._set_time_zone_controls)
        self.time_zone_group.setVisible(self.services.time_zone is not None)
        self._set_time_zone_controls()

    def _say_time_zone(self, short: str, whole: str) -> None:
        """The status after the button: a few words, and the whole sentence as the tooltip."""
        self.time_zone_note.setText(short)
        self.time_zone_note.setToolTip(whole)
        self.time_zone_group.setToolTip(whole)

    def _fill_time_zone_where(self, kept: str | None) -> None:
        """The first list: this computer, UTC, a kept hand value, then every region."""
        box = self.time_zone_where
        box.blockSignals(True)
        box.clear()
        box.addItem(TIME_ZONE_HOST.format(zone=time_zone.host_zone()), "host:")
        box.addItem(time_zone.UTC, f"zone:{time_zone.UTC}")
        if kept is not None:
            box.addItem(TIME_ZONE_KEPT.format(value=kept), f"kept:{kept}")
        regions = sorted({zone.split("/", 1)[0] for zone in time_zone.picker_zones()})
        for region in regions:
            box.addItem(region, f"region:{region}")
        box.blockSignals(False)
        self._fill_time_zone_place()

    def _fill_time_zone_place(self, zone: str | None = None) -> None:
        """The second list: the places in the region the first names, or nothing."""
        box = self.time_zone_place
        box.blockSignals(True)
        box.clear()
        kind, region = self._time_zone_where_data()
        if kind == "region":
            for name in time_zone.picker_zones():
                head, _, place = name.partition("/")
                if head == region:
                    box.addItem(place.replace("_", " "), name)
            if zone is not None:
                box.setCurrentIndex(max(0, box.findData(zone)))
        box.blockSignals(False)
        box.setEnabled(kind == "region")

    def _time_zone_where_data(self) -> tuple[str, str]:
        """What the first list names: `(kind, value)`, kept in the item as `kind:value` text."""
        kind, _, value = str(self.time_zone_where.currentData() or "").partition(":")
        return kind, value

    @Slot()
    def _time_zone_where_changed(self) -> None:
        self._fill_time_zone_place()
        self.time_zone_where.setToolTip(self.time_zone_where.currentText())
        self._set_time_zone_controls()

    def _chosen_time_zone(self) -> str | None:
        """The zone the two lists name now; `None` for the kept hand value (nothing to set)."""
        kind, value = self._time_zone_where_data()
        if kind == "host":
            return time_zone.host_zone()
        if kind == "zone":
            return str(value)
        if kind == "region":
            place = self.time_zone_place.currentData()
            return str(place) if place else None
        return None

    def _show_time_zone(self, reading: server_time_zone.Reading) -> None:
        """Point the two lists at what the file says: a hand value not in them is kept as it is."""
        zone = reading.zone
        host = time_zone.host_zone()
        listed = set(time_zone.picker_zones())
        known = zone is not None and (zone in (time_zone.UTC, host) or zone in listed)
        kept = None if known else reading.shown
        self._fill_time_zone_where(kept)
        box = self.time_zone_where
        if zone == time_zone.UTC:
            box.setCurrentIndex(box.findData(f"zone:{time_zone.UTC}"))
        elif zone == host:
            box.setCurrentIndex(0)
        elif kept is not None:
            box.setCurrentIndex(box.findData(f"kept:{kept}"))
        elif zone is not None:
            box.blockSignals(True)
            box.setCurrentIndex(box.findData(f"region:{zone.split('/', 1)[0]}"))
            box.blockSignals(False)
            self._fill_time_zone_place(zone)

    def _set_time_zone_controls(self) -> None:
        """Live with a readable zone, nothing of ours running, no read or write in flight.

        Apply only when the lists name a zone other than the one the file has.
        """
        reading = self._time_zone_reading
        live = (
            self.services.time_zone is not None
            and reading is not None
            and reading.problem is None
            and bool(time_zone.zones())
            and not self._busy
            and not self._time_zone_pending
            and not self._time_zone_writing
        )
        self.time_zone_where.setEnabled(live)
        kind = self._time_zone_where_data()[0]
        self.time_zone_place.setEnabled(live and kind == "region")
        chosen = self._chosen_time_zone()
        self.time_zone_apply_button.setEnabled(
            live and reading is not None and chosen is not None and chosen != reading.zone
        )

    def _look_up_time_zone(self) -> None:
        """Read the time zone off the GUI thread."""
        route = self.services.time_zone
        if route is None or self._waits_for_the_distro("time zone", self._look_up_time_zone):
            return
        self._time_zone_generation += 1
        self._time_zone_pending = True
        self._set_time_zone_controls()
        self._run(
            partial(_read_time_zone, route, self._time_zone_generation),
            self._time_zone_read,
            self._time_zone_read_failed,
        )

    @Slot(object)
    def _time_zone_read(self, answer: object) -> None:
        """The lists and the line under them, from one read; a stale answer is dropped."""
        if (
            not isinstance(answer, TimeZoneAnswer)
            or answer.generation != self._time_zone_generation
        ):
            return
        self._time_zone_pending = False
        reading = answer.reading
        self._time_zone_reading = reading
        if reading.problem is not None:
            self._say_time_zone(
                "Cannot be changed here", f"Cannot change the time zone here: {reading.problem}"
            )
        elif not time_zone.zones():
            self._say_time_zone("No zone list", f"Cannot set a time zone: {time_zone.MISSING_DATA}")
        else:
            self._show_time_zone(reading)
            self.time_zone_where.setToolTip(self.time_zone_where.currentText())
            short = f"Now {reading.shown}" + (" (see note)" if reading.note else "")
            self._say_time_zone(short, self._time_zone_now(reading))
        self._set_time_zone_controls()

    def _time_zone_now(self, reading: server_time_zone.Reading) -> str:
        """What the line says the server is set to now."""
        name = Path(reading.file).name
        if reading.value is None:
            said = f"Now UTC: {name} names no time zone, so the server keeps its image's own."
        else:
            said = f"Now {reading.shown} in {name}."
        if reading.login is not None and reading.login != (reading.value or time_zone.UTC):
            said += f" The login server says {reading.login}; Apply sets both."
        if reading.note is not None:
            said += f" Note: {reading.note}"
        return said

    @Slot(object)
    def _time_zone_read_failed(self, exc: object) -> None:
        """A read that raised: said, unless a newer read is pending (then it is dropped)."""
        if getattr(exc, "generation", None) != self._time_zone_generation:
            logger.warning(f"a stale or untagged time zone read failed: {exc}")
            return
        self._time_zone_pending = False
        self._say_time_zone("Could not read", f"Could not read this server's time zone: {exc}")
        self._set_time_zone_controls()

    @Slot()
    def apply_time_zone(self) -> None:
        """Ask once, then write the chosen zone on the job runner (T171)."""
        route = self.services.time_zone
        reading = self._time_zone_reading
        zone = self._chosen_time_zone()
        if (
            route is None
            or reading is None
            or reading.problem is not None
            or zone is None
            or self._busy
            or self._time_zone_pending
            or self._time_zone_writing
        ):
            return
        if not self._confirm(TIME_ZONE_TITLE, server_time_zone.question(self.entry, reading, zone)):
            return
        self._time_zone_writing = True
        self._set_time_zone_controls()
        self._say_time_zone("Setting…", f"setting the time zone to {zone}…")
        self._run(partial(route.write, zone), self._time_zone_written, self._time_zone_failed)

    @Slot(object)
    def _time_zone_written(self, result: object) -> None:
        self._time_zone_writing = False
        if isinstance(result, server_time_zone.Written):
            name = Path(result.file).name
            if result.backup is None:
                said = f"{name} already says {result.after}, so nothing was written."
            else:
                self._note_tuning_owed(result.file, result.rule)
                said = (
                    f"Time zone set to {result.after} in {name}; the file as it was is beside it "
                    f"at {result.backup.name}. " + server_time_zone.WHEN_IT_COUNTS
                )
            self.tuning_report.setPlainText(said)
        # Read again: the lists say what the disk says.
        self._look_up_time_zone()

    @Slot(object)
    def _time_zone_failed(self, exc: object) -> None:
        """A refusal `write()` raised (it wrote nothing), or a bug: said, and read again."""
        self._time_zone_writing = False
        self.tuning_report.setPlainText(f"The time zone was NOT changed: {exc}")
        self.action_failed.emit(str(exc))
        self._look_up_time_zone()

    def _build_my_party_group(self, tab: QWidget) -> QGroupBox:
        """My Party's panel, or the one line saying why this game has none (8.6).

        The panel is handed `self._run`, so the work runs wherever this view's
        work runs — one job runner for the tab, and the tests' inline runner
        reaches the panel without the panel knowing there is such a thing.
        """
        group = QGroupBox("My Party", tab)
        inside = QVBoxLayout(group)
        self.my_party_absent = QLabel("", group)
        self.my_party_absent.setWordWrap(True)
        self.my_party_absent.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        seam = self.services.my_party
        if seam is None:
            self.party_panel: PartyPanel | None = None
            self.my_party_absent.setText(_no_my_party(self.entry))
            inside.addWidget(self.my_party_absent)
            return group
        self.my_party_absent.setVisible(False)
        # T133: its two readings open the install's conf, so they wait for a
        # stopped distro like every other reading on this tab.
        self.party_panel = PartyPanel(seam, jobs=self._run, read_now=False, parent=group)
        self._read_party_facts()
        # The design's own cross-link (`b-users-surface.md:111`): a bot that just
        # joined a party is a row the Browse list above has not got yet.
        self.party_panel.party_changed.connect(self.refresh_bots)

        scroll = QScrollArea(group)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.party_panel)
        inside.addWidget(scroll, 1)
        return group

    def _read_party_facts(self) -> None:
        """My Party's level cap and spec list, unless the distro is stopped (T133)."""
        if self.party_panel is None:
            return
        if self._waits_for_the_distro("party facts", self._read_party_facts):
            return
        self.party_panel.read_install_facts()

    def _show_page_buttons(self) -> None:
        """Neither button offers a page that is not there."""
        self.previous_bots_button.setEnabled(len(self._bot_cursors) > 1)
        self.next_bots_button.setEnabled(self._bot_next is not None)

    @Slot()
    def refresh_bots(self) -> None:
        """Read the page this tab is on, off the GUI thread."""
        browser = self.services.bots
        if browser is None:
            return
        after, name_like = self._bot_cursors[-1], self.bot_filter.text().strip()
        self._run(
            lambda: browser.page(after=after, name_like=name_like),
            self._bots_listed,
            self._bots_failed,
        )

    @Slot()
    def filter_bots(self) -> None:
        """A new filter starts at the first page.

        Otherwise a filter typed on page nine shows page nine of a list that may
        now be one page long, which reads as "no bots match".
        """
        self._bot_cursors = [None]
        self.refresh_bots()

    @Slot()
    def next_bot_page(self) -> None:
        if self._bot_next is None:
            return
        self._bot_cursors.append(self._bot_next)
        self.refresh_bots()

    @Slot()
    def previous_bot_page(self) -> None:
        # The first entry is the first page's absent cursor and is never
        # popped: the button can be reached by a keyboard while a mouse sees it
        # disabled.
        if len(self._bot_cursors) > 1:
            self._bot_cursors.pop()
        self.refresh_bots()

    @Slot(object)
    def _bots_listed(self, page: object) -> None:
        self.bot_list.clear()
        problem = getattr(page, "problem", "")
        self._bot_total = getattr(page, "total", None)
        self._bot_next = getattr(page, "next_after", None)
        if problem:
            self.bot_summary.setText(problem)
            self._show_page_buttons()
            return
        # 8.5b: the split is drawn only where the two signals can disagree.
        # Three of the four trees have no playerbots schema at all, and on
        # m910q's TBC install this tab read "0 by the playerbots registry" —
        # naming a table that install has not got, beside a zero a person would
        # then go looking for. The per-row source is the same noise: one signal
        # means the same word on every row.
        split = botlist.has_registry(self.entry)
        for bot in getattr(page, "bots", []):
            where = "online" if bot.online else "offline"
            said_row = f"{bot.name} — level {bot.level} — {where}"
            self.bot_list.addItem(f"{said_row} — {bot.source}" if split else said_row)
        total = self._bot_total
        # A page number and not a row range: the rows are read by cursor, so
        # "51-100" would be a count this tab does not have and cannot get
        # without paying for it on every press.
        page_number = len(self._bot_cursors)
        shown = f"Page {page_number}, {self.bot_list.count()} shown."
        warning = getattr(page, "warning", "")
        if warning:
            # 8.5b. The clause is "warns, NEITHER reporting zero", and the first
            # live run of this path (m910q, TBC, 2026-09-07) read
            # "0 bots. Page 1, 0 shown. no character matched …" -- the number a
            # person reads first was the one the sentence after it exists to
            # contradict. The count is dropped rather than moved: the warning
            # only ever fires on a zero, so there is no other number to lose.
            self.bot_summary.setText(f"{warning}. {shown}")
            self._show_page_buttons()
            return
        counted = f"{total} {'bot' if total == 1 else 'bots'}"
        if split:
            counted += (
                f": {getattr(page, 'by_registry', 0)} by the playerbots registry, "
                f"{getattr(page, 'by_prefix', 0)} by the account prefix"
            )
        self.bot_summary.setText(f"{counted}. {shown}")
        self._show_page_buttons()

    @Slot(object)
    def _bots_failed(self, exc: object) -> None:
        self.bot_summary.setText(f"Could not read this server's bots: {exc}")

    # ------------------------------------------------------- the bot dashboard

    def _build_bot_dashboard_group(self, tab: QWidget) -> QGroupBox:
        """T127: the switch, the network opt-in, the Open button, and a log for the slow part.

        Nothing here reads a file or asks Docker on the GUI thread. The switch
        shows what the files say only once `refresh_bot_dashboard()` has read
        them through `_run()`, and every press runs in the group's own log panel,
        which locks the Server tab's buttons for as long as it runs -- a switch
        restarts the world, and so do a rebuild and an update.
        """
        group = QGroupBox("Bot dashboard", tab)
        inside = QVBoxLayout(group)
        about = QLabel(DASHBOARD_ABOUT, group)
        about.setWordWrap(True)
        row = QHBoxLayout()
        self.dashboard_switch = QCheckBox(DASHBOARD_SWITCH_OFF, group)
        self.dashboard_switch.clicked.connect(self.switch_bot_dashboard)
        self.open_dashboard_button = QPushButton("Open bot dashboard", group)
        self.open_dashboard_button.clicked.connect(self.open_bot_dashboard)
        self.open_dashboard_button.setEnabled(False)
        row.addWidget(self.dashboard_switch)
        row.addStretch(1)
        row.addWidget(self.open_dashboard_button)
        self.dashboard_lan = QCheckBox(DASHBOARD_LAN_LABEL, group)
        self.dashboard_lan_warning = QLabel(DASHBOARD_LAN_WARNING, group)
        self.dashboard_lan_warning.setWordWrap(True)
        self.dashboard_report = QLabel("", group)
        self.dashboard_report.setWordWrap(True)
        self.dashboard_report.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        # T162: shown for as long as the files say a rebuild is owed, and read
        # from them, so it survives the app closing; not the report line, which
        # the Open press rewrites.
        self.dashboard_stale = QLabel("", group)
        self.dashboard_stale.setWordWrap(True)
        self.dashboard_stale.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.rebuild_dashboard_button = QPushButton(tortoise_botdash.REBUILD_PRESS, group)
        self.rebuild_dashboard_button.clicked.connect(self.rebuild_bot_dashboard)
        stale_row = QHBoxLayout()
        stale_row.addWidget(self.dashboard_stale, 1)
        stale_row.addWidget(self.rebuild_dashboard_button)
        self.dashboard_stale.setVisible(False)
        self.rebuild_dashboard_button.setVisible(False)
        log = _IdleLogPanel(group)
        log.run_started.connect(self._dashboard_started)
        log.run_finished.connect(self._dashboard_finished)
        self.dashboard_log = log
        inside.addWidget(about)
        inside.addLayout(row)
        inside.addWidget(self.dashboard_lan)
        inside.addWidget(self.dashboard_lan_warning)
        inside.addWidget(self.dashboard_report)
        inside.addLayout(stale_row)
        inside.addWidget(log)
        # Unknown until read: the switch is not offered on a guess.
        self.dashboard_switch.setEnabled(False)
        self.dashboard_lan.setEnabled(False)
        self._dashboard_job = ""
        self._dashboard_owed = False
        return group

    @Slot()
    def refresh_bot_dashboard(self) -> None:
        """Read what this install's files say about the dashboard, off the GUI thread."""
        seam = self.services.bot_dashboard
        if seam is None or self._waits_for_the_distro("bot dashboard", self.refresh_bot_dashboard):
            return
        self._run(seam.state, self._dashboard_state_read, self._dashboard_state_failed)

    @Slot(object)
    def _dashboard_state_read(self, state: object) -> None:
        problem = getattr(state, "problem", "")
        on = bool(getattr(state, "on", False))
        running = self.dashboard_log is not None and self.dashboard_log.running
        found = getattr(state, "rebuild", None) if on else None
        debt = found if isinstance(found, bot_dashboard.Debt) else None
        owed = debt is not None
        self._show_dashboard_switch(on)
        self.dashboard_stale.setText("" if debt is None else dashboard_stale_text(debt))
        self.dashboard_stale.setVisible(owed and not problem)
        self.rebuild_dashboard_button.setVisible(owed and not problem)
        self._dashboard_owed = owed and not problem
        self._set_rebuild_dashboard_button()
        if problem:
            self.dashboard_report.setText(f"Could not tell whether the dashboard is on: {problem}")
            self.dashboard_switch.setEnabled(False)
            return
        self.dashboard_switch.setEnabled(not running)
        # The network choice is made at the switch-on and fixed while it is on:
        # changing it means a new container, and so a new address the world
        # would have to be restarted to find.
        self.dashboard_lan.setChecked(
            bool(getattr(state, "lan", False)) if on else self.dashboard_lan.isChecked()
        )
        self.dashboard_lan.setEnabled(not on and not running)
        self.open_dashboard_button.setEnabled(on and not owed)

    @Slot(object)
    def _dashboard_state_failed(self, exc: object) -> None:
        self.dashboard_report.setText(f"Could not tell whether the dashboard is on: {exc}")

    def _show_dashboard_switch(self, on: bool) -> None:
        self.dashboard_switch.setChecked(on)
        self.dashboard_switch.setText(DASHBOARD_SWITCH_ON if on else DASHBOARD_SWITCH_OFF)

    @Slot(bool)
    def switch_bot_dashboard(self, wanted: bool) -> bool:
        """The switch was pressed: ask, then run the change in the group's log. False if not run.

        The box goes back to where it was straight away; only the finished job
        and the re-read after it move it. A switch that showed "On" while a
        build was still failing would be the tab saying something untrue.
        """
        seam = self.services.bot_dashboard
        log = self.dashboard_log
        self._show_dashboard_switch(not wanted)
        if seam is None or log is None:
            return False
        if log.running or self.rebuild_log.running or self._busy:
            QMessageBox.information(
                self,
                "Something else is running",
                "This server is busy with another action. Wait for it to finish, then press "
                "the switch again. Nothing was changed.",
            )
            return False
        cancel = threading.Event()
        if wanted:
            lan = self.dashboard_lan.isChecked()
            answer = QMessageBox.question(
                self,
                "Switch the bot dashboard on?",
                dashboard_on_question(lan=lan),
            )
            if not said_yes(answer):
                return False
            self._dashboard_job = "on"
            return log.run(
                lambda: seam.switch_on(lan=lan, cancel=cancel),
                title="Switching the bot dashboard on",
                cancel=cancel,
            )
        answer = QMessageBox.question(self, "Switch the bot dashboard off?", DASHBOARD_OFF_QUESTION)
        if not said_yes(answer):
            return False
        self._dashboard_job = "off"
        return log.run(
            lambda: seam.switch_off(cancel),
            title="Switching the bot dashboard off",
            cancel=cancel,
        )

    def _set_rebuild_dashboard_button(self) -> None:
        """Live only while a rebuild is owed and nothing else of this tab's is running."""
        log = self.dashboard_log
        running = log is not None and log.running
        self.rebuild_dashboard_button.setEnabled(
            self._dashboard_owed and not running and not self._busy
        )

    @Slot()
    def rebuild_bot_dashboard(self) -> bool:
        """T162: ask, then run the dashboard's rebuild in the group's log. False if not run."""
        seam = self.services.bot_dashboard
        log = self.dashboard_log
        if seam is None or log is None:
            return False
        if log.running or self.rebuild_log.running or self._busy:
            QMessageBox.information(
                self,
                "Something else is running",
                "This server is busy with another action. Wait for it to finish, then press "
                f"{tortoise_botdash.REBUILD_PRESS} again. Nothing was changed.",
            )
            return False
        answer = QMessageBox.question(
            self, "Rebuild the bot dashboard?", DASHBOARD_REBUILD_QUESTION
        )
        if not said_yes(answer):
            return False
        cancel = threading.Event()
        self._dashboard_job = "rebuild"
        return log.run(
            lambda: seam.rebuild(cancel), title="Rebuilding the bot dashboard", cancel=cancel
        )

    @Slot()
    def _dashboard_started(self) -> None:
        self._set_busy(True)
        self.rebuild_dashboard_button.setEnabled(False)
        self.dashboard_switch.setEnabled(False)
        self.dashboard_lan.setEnabled(False)
        self.open_dashboard_button.setEnabled(False)

    @Slot(bool, str)
    def _dashboard_finished(self, ok: bool, message: str) -> None:
        """Unlock, re-read the files, and after a switch-off offer the restart."""
        self._set_busy(False)
        job, self._dashboard_job = self._dashboard_job, ""
        if not ok:
            self.dashboard_report.setText(message)
            self.action_failed.emit(message)
        self.refresh_bot_dashboard()
        seam = self.services.bot_dashboard
        cancelled = self.dashboard_log is not None and self.dashboard_log.cancelled
        if ok and not cancelled and job == "off" and seam is not None:
            self._run(
                seam.world_running, self._dashboard_offer_restart, self._dashboard_state_failed
            )

    @Slot(object)
    def _dashboard_offer_restart(self, running: object) -> None:
        seam = self.services.bot_dashboard
        log = self.dashboard_log
        if running is not True or seam is None or log is None or log.running or self._busy:
            return
        answer = QMessageBox.question(self, "Restart the server now?", DASHBOARD_RESTART_QUESTION)
        if not said_yes(answer):
            self.dashboard_report.setText(
                "The dashboard is off. The world stops trying to reach it at its next restart."
            )
            return
        cancel = threading.Event()
        self._dashboard_job = "restart"
        log.run(lambda: seam.restart_world(cancel), title="Restarting the server", cancel=cancel)

    @Slot()
    def open_bot_dashboard(self) -> None:
        """Say which of the player's accounts can sign in, then open the dashboard in the browser.

        The accounts are the Accounts tab's own listing (the bots and the app's
        own account left out), read off the GUI thread. Nothing is changed:
        granting a rank is the Accounts tab's press.
        """
        seam = self.services.bot_dashboard
        if seam is None:
            return
        accounts = self.services.accounts
        if accounts is None:
            self._open_dashboard_url()
            return
        self._run(accounts.listing, self._dashboard_accounts_read, self._dashboard_accounts_failed)

    @Slot(object)
    def _dashboard_accounts_read(self, listing: object) -> None:
        self.dashboard_report.setText(bot_dashboard.gm_hint(listing).text)
        self._open_dashboard_url()

    @Slot(object)
    def _dashboard_accounts_failed(self, exc: object) -> None:
        self.dashboard_report.setText(
            bot_dashboard.gm_hint(useraccounts.Listing(problem=str(exc))).text
        )
        self._open_dashboard_url()

    def _open_dashboard_url(self) -> None:
        seam = self.services.bot_dashboard
        if seam is not None:
            QDesktopServices.openUrl(QUrl(seam.url))

    # -------------------------------------------------------- maintenance tab

    def _build_maintenance_tab(self) -> None:
        """Backups, and a restore that cannot happen without its plan on screen.

        Deliberately shaped like the Networking tab (plan, then apply) rather
        than a confirmation dialog. A restore replaces every character on the
        server, so the thing being agreed to has to be readable while agreeing —
        `plan_restore()` collects every refusal instead of raising, precisely so
        all of them can be shown at once.
        """
        tab = QWidget(self)
        box = QVBoxLayout(tab)

        self.interrupted_label = QLabel("", tab)
        self.interrupted_label.setWordWrap(True)
        self.interrupted_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.forget_button = QPushButton("Forget that record", tab)
        self.forget_button.clicked.connect(self.forget_interrupted)
        self.interrupted_label.setVisible(False)
        self.forget_button.setVisible(False)

        top = QHBoxLayout()
        self.backup_button = QPushButton("Back up now", tab)
        self.refresh_backups_button = QPushButton("Refresh", tab)
        self.backup_button.clicked.connect(self.back_up)
        self.refresh_backups_button.clicked.connect(self.refresh_backups)
        top.addWidget(self.backup_button)
        top.addWidget(self.refresh_backups_button)
        top.addStretch(1)

        self.backup_list = QListWidget(tab)
        self.backup_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.backup_list.customContextMenuRequested.connect(self._show_backup_context_menu)
        self.backup_list.currentItemChanged.connect(self._backup_selection_changed)

        actions = QHBoxLayout()
        self.plan_restore_button = QPushButton("Show restore plan", tab)
        self.restore_button = QPushButton("Restore", tab)
        self.plan_restore_button.clicked.connect(self.show_restore_plan)
        self.restore_button.clicked.connect(self.run_restore)
        # Never enabled by selecting a file: only a plan that came back allowed
        # turns this on, and changing the selection turns it off again.
        self.restore_button.setEnabled(False)
        actions.addWidget(self.plan_restore_button)
        actions.addWidget(self.restore_button)
        actions.addStretch(1)

        self.maintenance_report = QPlainTextEdit(tab)
        self.maintenance_report.setReadOnly(True)

        # Two panels: the backup list with its buttons on the left, the restore
        # plan/result on the right. The interrupted-restore warning is a
        # full-width band above both, because it is about neither panel.
        backups = QGroupBox("Backups", tab)
        self.backup_button.setParent(backups)
        self.refresh_backups_button.setParent(backups)
        self.backup_list.setParent(backups)
        self.plan_restore_button.setParent(backups)
        self.restore_button.setParent(backups)
        backups_box = QVBoxLayout(backups)
        backups_box.addLayout(top)
        backups_box.addWidget(self.backup_list, 2)
        backups_box.addLayout(actions)

        restore = QGroupBox("Restore", tab)
        self.maintenance_report.setParent(restore)
        restore_box = QVBoxLayout(restore)
        restore_box.addWidget(self.maintenance_report, 1)

        box.addWidget(self.interrupted_label)
        box.addWidget(self.forget_button)
        columns = QHBoxLayout()
        columns.setSpacing(12)
        columns.addWidget(backups, 3)
        columns.addWidget(restore, 2)
        box.addLayout(columns)
        self._add_panel_tab(tab, "maintenance", "Maintenance")
        self.refresh_backups()

    def _selected_backup(self) -> Path | None:
        item = self.backup_list.currentItem()
        if item is None:
            return None
        path = item.data(Qt.ItemDataRole.UserRole)
        return Path(str(path))

    @Slot(object, object)
    def _backup_selection_changed(self, _current: object, _previous: object) -> None:
        """A plan belongs to one file. Selecting another must not carry it over."""
        self._restore_plan = None
        self.restore_button.setEnabled(False)

    @Slot()
    def refresh_backups(self) -> None:
        """Re-list the backups directory. Reading a directory, not doing any work."""
        if self._waits_for_the_distro("backups", self.refresh_backups):
            return
        self._restore_plan = None
        self.restore_button.setEnabled(False)
        self.backup_list.clear()
        directory = self.services.backups_dir()
        for path in sorted(directory.glob("*.sql"), reverse=True):
            size = path.stat().st_size / (1024 * 1024)
            item = QListWidgetItem(f"{path.name}  ({size:.1f} MB)")
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.backup_list.addItem(item)
        if self.backup_list.count() == 0:
            self.maintenance_report.setPlainText(f"No backups yet in {directory}.")
        self._show_interrupted()

    def _show_interrupted(self) -> None:
        """Surface a restore that never finished, and offer to put the record down."""
        record = self.services.interrupted_restore()
        if record is None:
            self.interrupted_label.setVisible(False)
            self.forget_button.setVisible(False)
            return
        if record.readable:
            named = ", ".join(record.databases) or "an unknown database"
            text = (
                f"A restore of {named} did not finish. Those databases may be half-written. "
                f"Restoring again is how that is escaped; the copy taken beforehand is "
                f"{', '.join(str(p) for p in record.safety_backup) or 'not recorded'}."
            )
        else:
            text = (
                f"There is a restore record at {record.marker} that cannot be read, so a restore "
                "was in flight but nothing about it can be established."
            )
        self.interrupted_label.setText(text)
        self.interrupted_label.setVisible(True)
        self.forget_button.setVisible(True)

    @Slot()
    def forget_interrupted(self) -> None:
        self._run(self.services.forget_interrupted, self._forget_done, self._maintenance_failed)

    @Slot(object)
    def _forget_done(self, _result: object) -> None:
        self._show_interrupted()

    def _backup_with_the_database(self) -> object:
        """Take the backup, starting the database alone first if it is down (T76).

        **Runs on the worker thread**, which is the whole reason it is a method
        and not three lines in `back_up()`: `docker.start_database()` waits up
        to `_DB_HEALTHY_TIMEOUT_SECONDS` for health, and a GUI thread parked on
        that is a frozen window.

        The backup is a `mysqldump` through `docker exec` into the database
        container, so with the stack stopped -- the ordinary state of a server
        nobody is playing on, and the state a user is in when they press
        "Update the server to latest…" -- it answered *"<db> is not running, so
        there is no database to back up"* and nothing was backed up. Measured on
        the Vanilla box, 2026-09-16 (T64's live gate, `dbdown-*`): the
        recommended button failed on the first press.

        **The same seam the applier uses for direct SQL** (`apply.Applier.
        _start_the_database_for_direct_sql`, T7) and for the same reason: the
        world is never started, only the database, because what is wanted is a
        database process to talk to and not a server that will write over the
        rows being dumped.

        **The database is left as it was found.** `start()` answers whether it
        had to start anything, and only that answer runs `stop()`. Leaving it up
        would be defensible after an update -- the rebuild recreates the whole
        stack a few minutes later -- but this is ONE decision for both buttons,
        and after the plain Backup button there is no rebuild coming: a press
        that quietly left a stopped server's database running is a press that
        changed something the user did not ask about. So it is put back, in both
        places, and the update's own `recreate` stage starts what it needs.

        `finally` and not a tidy line at the end: a backup that fails half way
        through must not leave the container up either, and `MaintenanceError`
        is the ordinary way out of here.
        """
        from yulon import forgetting

        # T216 review round 3: the databases are leased for the WHOLE backup,
        # a hot copy included -- one Backup or Restore of a server at a time.
        with contextlib.ExitStack() as leased:
            try:
                leased.enter_context(
                    docker.maintenance_lease(
                        self.services.controller.server_dir, forgetting.BACKUP_HOLDS_THE_DATABASES
                    )
                )
            except docker.MaintenanceLeaseTaken as exc:
                raise wotlk_maintenance.MaintenanceError(f"{exc} No backup was taken.") from exc
            return self._back_up_under_the_lease()

    def _back_up_under_the_lease(self) -> object:
        """`_backup_with_the_database()`'s work, once it holds the databases (worker thread)."""
        from yulon import forgetting

        alone = self.services.database_alone
        if alone is None:
            return self.services.backup()
        # T216 review round 1: held while the database this press started is
        # up, because the `finally` below stops it -- and a Start pressed in
        # between (still enabled: only the database runs) would have had its
        # database stopped under it. A database that was already up is not
        # stopped afterwards, so a hot copy releases the hold at once.
        with contextlib.ExitStack() as held:
            try:
                held.enter_context(
                    docker.hold_the_server(
                        self.services.controller.server_dir, forgetting.BACKUP_HOLDS_THE_SERVER
                    )
                )
            except docker.ServerHeldError as exc:
                # Led with what was not done, as the restore's refusal is (review round 2).
                raise wotlk_maintenance.MaintenanceError(f"No backup was taken: {exc}") from exc
            started = alone.bring_up("no backup was taken")
            if not started:
                held.close()
            try:
                return self.services.backup()
            finally:
                if started:
                    alone.take_down()

    @Slot()
    def back_up(self) -> None:
        if self._refused_during_bot_rebuild():
            return
        self.backup_button.setEnabled(False)
        self.maintenance_report.setPlainText("Backing up… this can take minutes on a full world.")
        self._backup_running = True  # T95: `forget_refusal()` reads it
        self._run(self._backup_with_the_database, self._backup_done, self._backup_failed)

    @Slot(object)
    def _backup_failed(self, exc: object) -> None:
        self._backup_running = False
        self._maintenance_failed(exc)

    @Slot(object)
    def _backup_done(self, result: object) -> None:
        self._backup_running = False
        self.backup_button.setEnabled(True)
        if not isinstance(result, wotlk_maintenance.BackupReport):
            return
        lines = [f"Backed up to {result.directory}:"]
        lines += [
            f"  {d.database}  {d.size_bytes / (1024 * 1024):.1f} MB  {d.path.name}"
            for d in result.dumps
        ]
        if result.missing_core:
            lines.append(f"  !! expected but absent: {', '.join(result.missing_core)}")
        if result.server_was_running:
            lines.append("  note: the server was running, so this is a hot copy.")
        # Re-list BEFORE writing the report: refresh_backups() writes its own
        # message when the directory is empty, so refreshing afterwards wipes
        # the one thing the user just asked for (caught by its own test).
        self.refresh_backups()
        self.maintenance_report.setPlainText("\n".join(lines))

    @Slot()
    def show_restore_plan(self) -> None:
        path = self._selected_backup()
        if path is None:
            self.maintenance_report.setPlainText("Select a backup first.")
            return
        self._restore_plan = None
        self.restore_button.setEnabled(False)
        self._run(
            lambda: self._plan_with_the_bot_request(path),
            self._restore_plan_ready,
            self._maintenance_failed,
        )

    def _plan_with_the_bot_request(self, path: Path) -> object:
        """The plan, plus T144's `always` warning where this game has the bot request (worker).

        T216: a tab that can start its database alone asks for a plan that
        allows a stopped one, and the press (`_restore_with_the_database()`)
        does the starting. Planning itself starts nothing -- a plan is read,
        often several times, and a press of "Show restore plan" that brought a
        container up would change the server before anyone agreed to anything.
        """
        if self.services.database_alone is None:
            plan = self.services.plan_restore(path)
        else:
            plan = self.services.plan_restore(path, can_start_database=True)
        seam = self.services.bot_pool_rebuild
        if seam is None:
            return plan
        return _PlanWithWarning(plan, seam.restore_warning())

    def _restore_with_the_database(self, plan: wotlk_maintenance.RestorePlan) -> object:
        """Restore, starting the database alone first if it is down (T216; worker thread).

        `_backup_with_the_database()`'s handling, given to the restore it was
        never given to. Reported from a Steam Deck on WotLK (2026-10-03): the
        plan refused while the world and login servers ran -- "Stop the server
        and try again" -- and refused again once Stop had run, because the one
        Stop takes the database down too and the plan then said there was
        "nothing to restore into". No press reached a restorable state; the
        player stopped two containers by hand in a terminal. Per-container
        buttons were asked for and are not the answer: the maintainer's reply in
        the same thread was that there is one Stop because everything has to
        stop at once.

        Only the database is started, never the world: a worldserver holds
        characters in memory and saves them back over the restored rows, which
        is the whole reason the plan refuses while one runs.

        **The plan is asked again with the database up**, by `maintenance.
        restore()` itself on the plain path and by `_restore_with_the_bot_
        request()` before it touches the conf on Tortoise's -- and without
        `can_start_database`, because at that point a database that is not up is
        a refusal again. A server started between the plan and the press is
        caught there: the database is already up, so nothing is started or
        stopped, and the running world refuses as it always did. The token is
        the file's identity, length and schemas and not the census, so the plan
        made with the database down still confirms the one made with it up, and
        a file replaced in between is still refused.

        **Nothing starts, stops or recreates the server while it runs**
        (`docker.hold_the_server()`, review round 1, 2026-10-03). With the
        server stopped, only the database is up during the restore, so Start
        stays enabled and the view is not busy: a Start pressed during the load
        brought the world up on the databases being written, and the `finally`
        below then stopped the database under it. The hold is taken whether or
        not this press starts the database -- a world started under a restore
        is the hazard either way -- and it cannot be taken while a Start, Stop
        or recreate of this server is running, whose world may exist without
        running yet and so pass the census.

        **One Backup or Restore of a server at a time** (`docker.maintenance_
        lease()`, review round 3). Separate from the hold, which is about the
        containers: a Back up pressed during the load ran its mysqldump beside
        it, and a Restore pressed while a stopped server's Backup had the
        database up loaded into it -- then the backup's cleanup stopped the
        database under the half-loaded restore. Taken first, before the hold.

        **The database is left as it was found**, by `bring_up()`'s own answer:
        only True runs `take_down()`, in `finally`, so a refusal at the press, a
        failed load and a success all put it back. A start that fails raises
        before the restore is reached, and `bring_up()` has already stopped
        anything it started (`_database_alone()`); the sentence leads with what
        the player needs to know, that nothing was restored.
        """
        from yulon import forgetting

        with contextlib.ExitStack() as leased:
            try:
                leased.enter_context(
                    docker.maintenance_lease(
                        self.services.controller.server_dir, forgetting.RESTORE_HOLDS_THE_DATABASES
                    )
                )
            except docker.MaintenanceLeaseTaken as exc:
                raise wotlk_maintenance.MaintenanceError(f"{exc} Nothing was restored.") from exc
            return self._restore_under_the_lease(plan)

    def _restore_under_the_lease(self, plan: wotlk_maintenance.RestorePlan) -> object:
        """`_restore_with_the_database()`'s work, once it holds the databases (worker thread)."""
        from yulon import forgetting

        alone = self.services.database_alone
        with contextlib.ExitStack() as held:
            try:
                held.enter_context(
                    docker.hold_the_server(
                        self.services.controller.server_dir, forgetting.RESTORE_HOLDS_THE_SERVER
                    )
                )
            except docker.ServerHeldError as exc:
                raise wotlk_maintenance.MaintenanceError(f"Nothing was restored: {exc}") from exc
            started = False
            if alone is not None:
                try:
                    started = alone.bring_up("the restore was not started")
                except Exception as exc:
                    raise wotlk_maintenance.MaintenanceError(
                        "Nothing was restored: the database could not be started on its own "
                        f"for it. {exc}"
                    ) from exc
            try:
                return self._restore_with_the_bot_request(plan)
            finally:
                if started:
                    alone.take_down()  # type: ignore[union-attr]

    def _restore_with_the_bot_request(self, plan: wotlk_maintenance.RestorePlan) -> object:
        """T144: take a pending `once:` bot rebuild back, THEN restore (worker thread).

        A restore replaces the characters database, the module's record of its
        last rebuild included, so a request left in `aiplayerbot.conf` would be
        new again afterwards and the next start would delete the restored bots.
        Before and not after: a restore is followed by a start the user makes,
        and a failed write refuses the restore rather than running it armed.

        The plan is asked again first and refused in `maintenance.restore()`'s
        own words, so a restore that would refuse does not touch the conf.

        **A restore that raises anyway** (round 5): `maintenance.restore()`
        (controller_wow_wotlk/maintenance.py:973) runs "re-census, safety dump,
        marker, load, marker removed". Everything before `_write_marker()` -- the
        confirmation, the re-census, `_safety_backup()`, the marker write itself
        ("nothing was restored") -- raises with nothing loaded; from the marker
        on, the load may have begun ("failed part-way", marker left behind on
        purpose). So "did it load" is read off the marker: a record that is not
        the one there before this press means the load was reached, and the
        request stays off; an unchanged one means nothing loaded, and it is put
        back as it was. A marker that cannot be read counts as "could not tell"
        and keeps it off.
        """
        seam = self.services.bot_pool_rebuild
        if seam is None:
            return self.services.restore(plan)
        fresh = self.services.plan_restore(plan.backup)
        if fresh.refusals:
            raise wotlk_maintenance.MaintenanceError(f"restore refused: {' '.join(fresh.refusals)}")
        if fresh.token != plan.token:
            raise wotlk_maintenance.MaintenanceError(
                f"{plan.backup.name} is not the file that was checked — it changed in between. "
                "Nothing was restored; look at it again."
            )
        marker_before = self.services.interrupted_restore()
        taken = seam.before_restore()
        try:
            report = self.services.restore(plan)
        except Exception as exc:
            if taken is None:
                raise
            loaded: bool | None
            try:
                marker_after = self.services.interrupted_restore()
            except Exception as check:  # noqa: BLE001 - "could not tell" is its own answer
                logger.warning(f"could not read the restore marker after a failed restore: {check}")
                loaded = None
            else:
                # Every unreadable marker reads back as the same record, so two of them
                # compare equal whatever happened in between: unreadable on either side
                # is "could not tell", never "nothing loaded" (T144 review round 5).
                if _unreadable(marker_before) or _unreadable(marker_after):
                    loaded = None
                else:
                    loaded = marker_after != marker_before
            said = seam.after_a_failed_restore(taken, loaded=loaded)
            raise wotlk_maintenance.MaintenanceError(f"{exc} {said}") from exc
        return _RestoredWithNote(report, taken.note if taken is not None else None)

    @Slot(object)
    def _restore_plan_ready(self, result: object) -> None:
        warning = None
        if isinstance(result, _PlanWithWarning):
            warning = result.warning
            result = result.plan
        if not isinstance(result, wotlk_maintenance.RestorePlan):
            return
        lines = [
            f"Restoring {result.backup.name} would OVERWRITE: {', '.join(result.databases)}",
            f"  size: {result.size_bytes / (1024 * 1024):.1f} MB",
        ]
        if result.interrupted is not None and result.interrupted.readable:
            lines.append(
                "  an earlier restore of "
                f"{', '.join(result.interrupted.databases)} never finished"
            )
        if result.refusals:
            lines.append("")
            lines.append("This cannot go ahead:")
            lines += [f"  - {r}" for r in result.refusals]
        else:
            lines.append("")
            # Named from the plan rather than asserted. This said "Every
            # character on the server is replaced" on EVERY allowed plan — with
            # no check that `acore_characters` was even in it — so a world-only
            # restore threatened characters it would not touch, and the word
            # "replaced" was wrong besides: mysqldump emits `DROP TABLE IF
            # EXISTS` per table and no `DROP DATABASE`, so a restore MERGES
            # (measured on Windows, 2026-08-23: a table created after the backup
            # survived a full 306 MB restore of that schema). A warning that
            # overstates on one axis and understates on the other teaches the
            # user to discount it (review, 2026-08-24).
            named = ", ".join(result.databases) if result.databases else "nothing"
            lines.append(f"This overwrites: {named}.")
            if result.starts_database:
                # T216: said before the press, because it is something the press
                # does to the server that the player did not do themselves.
                lines.append(
                    "The database will be started on its own for this restore and stopped again "
                    "afterwards; the game servers stay stopped."
                )
            lines.append(
                "Tables the backup does not contain are LEFT AS THEY ARE — a restore merges "
                "into the databases it names rather than returning them to the state the backup "
                "was taken from. Press Restore to go ahead."
            )
            if warning:
                lines.append("")
                lines.append(warning)
        self.maintenance_report.setPlainText("\n".join(lines))
        # Only a plan that is allowed arms the button, and only for this file.
        self._restore_plan = result if result.allowed else None
        self.restore_button.setEnabled(result.allowed)

    @Slot()
    def run_restore(self) -> None:
        plan = self._restore_plan
        if plan is None:
            # Belt and braces: the button is disabled without a plan, but a
            # restore is not something to leave to a widget's enabled state.
            self.maintenance_report.setPlainText("Show the restore plan first.")
            return
        if self._refused_during_bot_rebuild():
            return
        if self._put_back_running and self.services.bot_pool_rebuild is not None:
            # Not `_busy`: Back up and Restore have never been gated on it (see
            # `_refused_during_bot_rebuild`); only a put-back writes the file the
            # restore's take-back writes (`_put_back_refused`, the other order).
            self.maintenance_report.setPlainText(RESTORE_DURING_PUT_BACK)
            return
        self.restore_button.setEnabled(False)
        self.maintenance_report.setPlainText(f"Restoring {plan.backup.name}…")
        self._restore_running = True  # T95: `forget_refusal()` reads it
        self._run(
            lambda: self._restore_with_the_database(plan),
            self._restore_done,
            self._restore_failed,
        )

    @Slot(object)
    def _restore_failed(self, exc: object) -> None:
        self._restore_running = False
        self._maintenance_failed(exc)

    @Slot(object)
    def _restore_done(self, result: object) -> None:
        self._restore_running = False
        self._restore_plan = None
        note = None
        if isinstance(result, _RestoredWithNote):
            note = result.note
            result = result.report
        if not isinstance(result, wotlk_maintenance.RestoreReport):
            return
        safety = ", ".join(str(p) for p in result.safety_backup) or "none"
        # Re-list BEFORE writing the report: refresh_backups() writes its own
        # message when the directory is empty, so refreshing afterwards wipes
        # the one thing the user just asked for (caught by its own test).
        self.refresh_backups()
        self.maintenance_report.setPlainText(
            f"Restored {', '.join(result.databases)} from {result.backup}.\n"
            f"The copy taken beforehand: {safety}" + (f"\n{note}" if note else "")
        )

    @Slot(object)
    def _maintenance_failed(self, exc: object) -> None:
        self.backup_button.setEnabled(True)
        self.maintenance_report.setPlainText(f"FAILED: {exc}")
        self.action_failed.emit(str(exc))
        self._show_interrupted()

    # ----------------------------------------------------------- modules tab

    def _build_modules_tab(self) -> None:
        tab = QWidget(self)
        box = QVBoxLayout(tab)
        # T42: a panel of family cards, not a `QListWidget`. The list showed 41
        # identical lines in catalog order and said nothing about which of them
        # this install HAD -- the reading two players sent in as "None of
        # modules detected" (T41) and "I can't get any modules to work".
        self.modules_panel = ModulesPanel(tab)
        self.modules_panel.install_pressed.connect(self._row_install)
        self.modules_panel.remove_pressed.connect(self._row_remove)
        self.modules_panel.chip_pressed.connect(self._chip_pressed)
        self.modules_panel.chip_action_pressed.connect(self._chip_action_pressed)
        self.modules_panel.context_menu_requested.connect(self._show_module_context_menu)
        # T73: six lines, and not the twelve a `QPlainTextEdit` asks for. The
        # height on this tab belongs to the list above it -- see `REPORT_LINES`.
        self.module_report = _ReportBox(tab)
        self.module_report.setReadOnly(True)
        # T80: the strip that names the box above and folds it away when there is
        # nothing in it -- which is every start, and the moment the list most
        # wants the 106px an empty text field costs.
        self.module_report_strip = _ReportStrip(self.module_report, tab)
        # The two buttons that used to act on "the selection" are gone: every
        # row carries its own Install or Remove, so there is no second place for
        # the tab and the user to disagree about what is selected.
        #
        # The third button on this tab, and the only one that is not about one
        # module: it applies the pending SQL of everything installed here,
        # because that is the granularity the importer has -- it is handed the
        # module folder list and ledgers what it applies in `updates`.
        self.module_sql_button = QPushButton(MODULE_SQL_BUTTON_LABEL, tab)
        # The only read-only one: it fetches and counts and writes nothing
        # outside each clone's `.git`. It is a button rather than part of the
        # status poll because it costs one network round trip per installed
        # module, and a poll would pay that every few seconds.
        self.module_updates_button = QPushButton(MODULE_UPDATES_BUTTON_LABEL, tab)
        # New with T42 and the cheapest control on the tab: `reload_modules()`
        # re-reads the clone directories and nothing else. It exists because
        # every OTHER thing that changes what is installed -- a clone made by
        # hand, a folder deleted outside the app -- used to need a restart
        # before the tab noticed.
        self.refresh_modules_button = QPushButton("Refresh", tab)
        # `refresh_modules()` and not `reload_modules()`: Refresh is the press
        # that means "read the disk again", so it is the one that throws the
        # version cache away. Every OTHER caller of `reload_modules()` is a
        # redraw after something this app did, and those invalidate the one
        # clone they changed (T44 item 1).
        self.refresh_modules_button.clicked.connect(self.refresh_modules)
        self.refresh_modules_button.setToolTip(
            "Read this install's module folders again. Cheap: directory names and one "
            "`git log -1` per module, no network."
        )
        # The two whose subject is not in the catalog at all: a module this app
        # does not ship, named by the user.
        self.module_link_button = QPushButton(MODULE_LINK_BUTTON_LABEL, tab)
        self.module_folder_button = QPushButton(MODULE_FOLDER_BUTTON_LABEL, tab)
        self.module_link_button.clicked.connect(self.install_module_from_link)
        self.module_folder_button.clicked.connect(self.install_module_from_folder)
        self.module_sql_button.clicked.connect(self.apply_module_sql)
        self.module_updates_button.clicked.connect(self.check_module_updates)
        # The action `_format_report` has always named. It sits on THIS tab
        # because this is the tab that prints "worldserver REBUILD required
        # before this takes effect" — for 20 of the 41 shipped manifests, every
        # one of them a `module` — and until 2026-09-08 a grep for a
        # rebuild/compile/build button across `yulon/ui/` returned nothing at
        # all, so that sentence named an action this app did not have.
        #
        # The ellipsis is the convention for "this opens a dialog first".
        #
        # T89: an ENTRY in the "Server build ▾" menu, with the two T64 presses
        # below it, and no longer a button of its own. Seven buttons wrapped
        # the bar at the 1280x800 the app opens at; the three are one act --
        # compile this server again -- at three sources, so they are one
        # button, on the owner's decision of 2026-09-27. Each is a `QAction`
        # named `_action` and not `_button`, so a caller that still expects a
        # widget (`click()`, `isHidden()`) fails at mypy rather than reading
        # plausibly.
        self.server_build_button = QPushButton(SERVER_BUILD_LABEL, tab)
        self.server_build_button.setObjectName(SERVER_BUILD_BUTTON)
        self.server_build_button.setToolTip(SERVER_BUILD_TIP)
        self.server_build_menu = QMenu(self.server_build_button)
        # Off by default on a `QMenu`, and each entry's sentence is the only
        # place it says what it costs.
        self.server_build_menu.setToolTipsVisible(True)
        self.rebuild_action = self.server_build_menu.addAction(REBUILD_BUTTON_LABEL)
        self.rebuild_action.triggered.connect(self.rebuild_server)
        self.rebuild_action.setToolTip(
            "Compile the server again so modules installed since the last build are in it. "
            "Asks first — it takes as long as an install's compile and the server goes down."
        )
        # Beside the Rebuild button and never on the Catalog tab's tile, which
        # greys to "Installed" the moment the app knows the folder: an
        # established install is operated from its controller view. The ellipsis
        # is the same convention — it asks first, and the dialog names every file.
        #
        # Dead for three of the four games, and that is the honest state rather
        # than a gap: only `wow-tortoise`'s plan declares a phase meant to be
        # re-applied to a server that already exists (T10/T11).
        self.updates_button = QPushButton(native.UPDATES_BUTTON_LABEL, tab)
        self.updates_button.clicked.connect(self.apply_database_updates)
        self.updates_button.setToolTip(
            "Apply the SQL this server's install plan has gained since it was installed. "
            "Asks first, names every file, and refuses while the server is running."
        )
        # Beside the button it exists for, and dead for almost every install --
        # by design, and not the same "dead" the updates button carries. That
        # one is greyed on the CATALOG; this one is greyed until the databases
        # themselves say `populated`, which is the state of an install this app
        # did not make. On a server Yu'lon installed it never lights up at all,
        # because that server already carries the row (T19).
        self.adopt_button = QPushButton(native.ADOPT_BUTTON_LABEL, tab)
        self.adopt_button.clicked.connect(self.adopt_as_imported)
        self.adopt_button.setToolTip(
            "Say that these databases are a finished import, for a server this app did not "
            "install. Asks first, names the one row it writes, and refuses while the server "
            "is running. Yu'lon cannot check the import finished -- you are saying so."
        )
        # T64, immediately below Rebuild (right of it, until T89 made the three
        # one menu), which is what the approved design asks for: the two are
        # the same act -- compile this server again -- and they differ only in
        # what is compiled. Put anywhere else, a user looking for "how do I get
        # the newest code" would find the press that recompiles the same commit.
        #
        # ABSENT rather than greyed where there is no route, which is the one
        # place this tab breaks its own rule (see `ControllerServices.
        # update_to_latest`): for a WSL-resident server or an entry the catalog
        # does not offer this for, there is nothing that would ever enable it.
        self.update_to_latest_action = self.server_build_menu.addAction(
            UPDATE_TO_LATEST_BUTTON_LABEL
        )
        self.update_to_latest_action.triggered.connect(self.update_to_latest)
        self.update_to_latest_action.setToolTip(
            "Fetch the newest code from the repositories this server was built from and compile "
            "it. Asks first, and offers a backup: this is code nobody has tested with this app."
        )
        self.update_to_latest_action.setVisible(self.services.update_to_latest is not None)
        # Hidden until this install has actually been moved off its pins, and
        # that is not the same rule as the entry above. There is nothing to
        # return FROM on a server that is still on the commit the gates ran on,
        # and a live "Return to the tested pin…" there would offer a multi-hour
        # compile that ends exactly where it started.
        self.return_to_pin_action = self.server_build_menu.addAction(RETURN_TO_PIN_BUTTON_LABEL)
        self.return_to_pin_action.triggered.connect(self.return_to_the_tested_pin)
        self.return_to_pin_action.setToolTip(
            "Compile the server again from the commit this app was tested against. It does not "
            "undo anything the newer server wrote into your databases."
        )
        self.return_to_pin_action.setVisible(False)
        self.server_build_button.setMenu(self.server_build_menu)
        # The button greys when every entry it SHOWS is dead, and only then, so
        # the bar still says "nothing here can act" without a press -- one of
        # the two things `flow_layout`'s module docstring holds a menu costs
        # (the other, a tooltip a touch screen cannot reach, it still does). Driven off
        # each entry's own `changed`, so every place that enables, disables,
        # shows or hides an entry (the busy lock, T64's backup window, T77's
        # reading) moves the button without having to know it exists.
        for action in self.server_build_menu.actions():
            action.changed.connect(self._set_server_build_button)
        self._set_server_build_button()
        # What this install was last built from, in `native.source_revs_line()`'s
        # words. Blank -- and the whole row hidden -- for a server still on its
        # pins, which is every install that has never pressed the button above:
        # a line repeating the catalog would be a reading of `catalog.json`
        # dressed up as a reading of the folder.
        self.source_version_label = QLabel("", tab)
        self.source_version_label.setWordWrap(True)
        self.source_version_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.source_version_label.setVisible(False)
        # Its own panel, not the report box above it. `module_report` is a
        # `setPlainText` field that shows the LAST action's result, and a
        # multi-hour job written into it would show one line and then look
        # frozen — which is the exact reading that produced this feature's bug
        # report. `LogPanel` is timestamped, follows the bottom, carries a
        # ticking elapsed field and owns the Stop button, and it already exists.
        #
        # ONE panel for both long jobs on this tab, and shared rather than
        # doubled: a rebuild and a database update must not run at once — they
        # want the same containers — and a second panel would have to be locked
        # against the first, registered with `log_panels()` for the exit path,
        # and stopped by it. The panel's own `running` flag is that lock already.
        self.rebuild_log = _IdleLogPanel(tab)
        # The lock, in both directions. A rebuild replaces the containers the
        # Server tab's Start/Stop/Remove act on, so those go dead for its
        # duration; `rebuild_server()` refuses while `_busy` for the mirror
        # case. Driven off the PANEL's own signals rather than set by hand
        # around the call, so a job that fails, is stopped, or raises before its
        # first line still unlocks — the shape `_set_busy(False)` is missed by
        # is exactly how the Server tab's own buttons were left dead once
        # before.
        self.rebuild_log.run_started.connect(self._rebuild_started)
        self.rebuild_log.run_finished.connect(self._rebuild_finished)
        # T158: the rebuild panel's own "Stop now anyway", for a rebuild or a
        # rollback that is waiting to stop a world still loading. Shown only
        # while the panel's lines say so (`_watch_for_load_wait`).
        self.rebuild_stop_anyway_button = QPushButton(STOP_ANYWAY_LABEL, tab)
        self.rebuild_stop_anyway_button.setProperty("danger", True)
        self.rebuild_stop_anyway_button.setToolTip(STOP_ANYWAY_TIP)
        self.rebuild_stop_anyway_button.setVisible(False)
        self.rebuild_stop_anyway_button.clicked.connect(self.rebuild_stop_now_anyway)
        self._rebuild_force: threading.Event | None = None
        self._rebuild_wait_relay = LineRelay(self)
        self._rebuild_wait_relay.line.connect(self._rebuild_wait_line)
        # ONE action bar, where there used to be two toolbars of nine buttons:
        # the two that acted on "the selection" have moved onto the rows
        # themselves and the two custom-module presses have moved into their own
        # card, which leaves six and room for them. Read left to right: the two
        # that only READ this install, then the four that change it.
        #
        # T83: a FLOW layout and not a `QHBoxLayout`. Six buttons ask for 946px
        # of hint plus their spacing, and the tab has ~824 at the 960px
        # `main.MINIMUM_WINDOW_SIZE`; a `QHBoxLayout` answers that by drawing
        # each of them under its hint, which is a label cut off mid-word --
        # measured themed at 960 before this change, `Check for updates` in
        # 114px reading `heck for update`. `FlowLayout`'s docstring owns the
        # choice between wrapping and an overflow menu.
        self.module_actions = flow_bar(tab)
        actions = self.module_actions.flow()
        actions.addWidget(self.module_updates_button)
        actions.addWidget(self.refresh_modules_button)
        # Where the `addStretch(1)` was: the gap between the two that only read
        # this install and the four that change it, drawn while the bar is one
        # line and spent when it wraps.
        actions.add_gap()
        actions.addWidget(self.adopt_button)
        actions.addWidget(self.updates_button)
        actions.addWidget(self.module_sql_button)
        actions.addWidget(self.server_build_button)
        # The banner, hidden until something owes a rebuild. It is above the
        # cards rather than on each owing row because the ACTION is one action
        # for all of them -- one compile covers every module installed since the
        # last one -- while the chip on each row says which ones it covers.
        self.rebuild_banner = QWidget(tab)
        banner_box = QHBoxLayout(self.rebuild_banner)
        banner_box.setContentsMargins(8, 6, 8, 6)
        self.rebuild_banner_label = QLabel("", self.rebuild_banner)
        self.rebuild_banner_label.setWordWrap(True)
        self.rebuild_banner_label.setStyleSheet(f"color: {COLOR_TEXT_WARNING};")
        self.rebuild_banner_button = QPushButton(REBUILD_BUTTON_LABEL, self.rebuild_banner)
        # The SAME slot as the toolbar's, not a copy: the dialog, the refusals
        # and the busy gate are `rebuild_server()`'s, and a second caller that
        # skipped any of them would be a second rebuild route to keep in step.
        self.rebuild_banner_button.clicked.connect(self.rebuild_server)
        banner_box.addWidget(self.rebuild_banner_label, 1)
        banner_box.addWidget(self.rebuild_banner_button)
        self.rebuild_banner.setStyleSheet(
            f"background-color: {COLOR_BG_PARCHMENT}; border: 1px solid {COLOR_TEXT_WARNING};"
        )
        self.rebuild_banner.setVisible(False)
        # The custom-module card: the one place on this tab whose subject is not
        # in the catalog and not on disk yet. Its own box with a sentence,
        # because a link the user pastes is the only control here that can fail
        # before anything runs.
        custom = QGroupBox(CUSTOM_MODULE_CARD_TITLE, tab)
        self.custom_module_card = custom
        custom_box = QVBoxLayout(custom)
        custom_note = QLabel(CUSTOM_MODULE_CARD_NOTE, custom)
        custom_note.setWordWrap(True)
        custom_box.addWidget(custom_note)
        custom_row = QHBoxLayout()
        custom_row.addWidget(self.module_link_button)
        custom_row.addWidget(self.module_folder_button)
        custom_row.addStretch(1)
        custom_box.addLayout(custom_row)
        # T153: the same card on one line -- its title, then its two presses --
        # for the windows where the list needs the height its sentence takes.
        # `_TabFit` decides which of the two is on screen (step 3 of its order);
        # the line starts hidden because every tab starts with the card whole.
        # The buttons on it are relays of the card's own (`_RelayButton`), so
        # the greying and the press are the card's, whichever form is showing.
        self.custom_module_line = QWidget(tab)
        line_box = QHBoxLayout(self.custom_module_line)
        line_box.setContentsMargins(0, 0, 0, 0)
        line_title = QLabel(f"{CUSTOM_MODULE_CARD_TITLE}:", self.custom_module_line)
        line_title.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-weight: bold;")
        # The sentence the line leaves out, for a pointer that asks. Not the only
        # place it is: the card whole says it wherever the window has the room.
        line_title.setToolTip(CUSTOM_MODULE_CARD_NOTE)
        self.module_link_line_button = _RelayButton(
            self.module_link_button, self.custom_module_line
        )
        self.module_folder_line_button = _RelayButton(
            self.module_folder_button, self.custom_module_line
        )
        line_box.addWidget(line_title)
        line_box.addWidget(self.module_link_line_button)
        line_box.addWidget(self.module_folder_line_button)
        line_box.addStretch(1)
        self.custom_module_line.setVisible(False)

        # T73: ONE stretching widget on this tab, and it is the list. Everything
        # under it is as tall as it has something to say -- the report a line
        # per line to a ceiling of six, the log its strip until a job writes to
        # it -- and every pixel left over is the list's.
        #
        # It used to be 3:1:2 between the list, the report and the log, which
        # sounds like the list wins and does not: `QVBoxLayout` hands every
        # widget its `sizeHint` before it shares the SURPLUS by stretch, and
        # both text boxes' hints are twelve lines of nothing. Measured in the
        # themed window (the fonts scale with its width, so nothing here can be
        # measured without the theme on): at 1920x1080 the list had 311px of the
        # tab's 900 and two whole rows of forty, and at the 1280x800 the app
        # opens at it had 70 -- a scrollbar and the top of a family card.
        box.addWidget(self.module_actions)
        box.addWidget(self.source_version_label)
        box.addWidget(self.rebuild_banner)
        box.addWidget(self.modules_panel, 1)
        box.addWidget(custom)
        box.addWidget(self.custom_module_line)
        box.addWidget(self.module_report_strip)
        box.addWidget(self.module_report)
        box.addWidget(self.rebuild_stop_anyway_button)
        box.addWidget(self.rebuild_log)
        self.modules_panel.setMinimumHeight(MODULE_LIST_MIN_HEIGHT)
        # T83: the one place that decides what gives when this tab cannot hold
        # everything on it. Built after every widget above is in `box`, because
        # it walks that layout.
        self.modules_fit = _TabFit(
            tab,
            self.rebuild_log,
            self.module_report_strip,
            self.modules_panel,
            MODULE_LIST_MIN_HEIGHT,
            custom,
            self.custom_module_line,
            MODULE_LIST_ROWS_HEIGHT,
        )
        self._add_panel_tab(tab, "modules", "Modules")
        # The first reading, taken once the widgets it writes into exist. One
        # small file, no daemon and no remote: cheap enough to pay on every tab
        # the app opens, which is what lets an install that was updated in an
        # earlier session say so without anybody pressing anything.
        self._refresh_source_version()
        # T124's count, off the GUI thread: the cached reading on every open
        # but the day's first, which asks GitHub.
        self._refresh_upstream_news()
        # Keyed by (FAMILY, id) since round 2, for `modules_panel._rows`'s
        # reason: nothing makes an id unique across families, and an id-keyed
        # dict handed `selected_manifest()` the other family's manifest --
        # which is the object the applier is then told to install.
        self._manifests: dict[tuple[str, str], Manifest] = {}
        # The manifest the module job now in flight is really about. Remembered
        # rather than looked up again when the report comes back: an
        # `ApplyReport` carries an id and no family, so a second lookup by id is
        # a guess where this is the answer (round 2).
        self._acting_on: Manifest | None = None
        # T150: the Update in flight and the answers it was handed, so a press
        # that comes back `ReleaseDirectionUnknown` can be asked about and run
        # again with the same answers. `None` for every other job.
        self._update_asked: tuple[Manifest, Mapping[str, str] | None] | None = None
        # The importer talks from a worker thread for however long it runs, and
        # this is what carries its lines to the GUI one. Same mechanism as the
        # repair's `_import_relay`, and a separate object because the two runs
        # write to different widgets. See `LineRelay`.
        self._module_sql_relay = LineRelay(self)
        self._module_sql_relay.line.connect(self._module_sql_line)
        # T44 item 1. A game with no `module_version` seam gets a cache whose
        # reader always answers `None`, rather than a `None` cache every caller
        # would have to branch on: the rows then show nothing where the version
        # goes, which is the same thing a machine with no git shows.
        self._versions = VersionCache(self.services.module_version or (lambda _path: None))
        self._filling_versions = False
        self.reload_modules()
        # The gate that used to grey two toolbar buttons now greys every row's
        # own press: there is no other control left for it to act on.
        self.modules_panel.set_enabled_actions(self._module_actions_allowed())
        self.module_sql_button.setEnabled(self.services.module_sql is not None)
        self.module_sql_button.setToolTip(
            MODULE_SQL_TIP if self.services.module_sql is not None else MODULE_SQL_NO_IMPORTER
        )
        self.module_updates_button.setEnabled(self.services.module_updates is not None)
        self.module_updates_button.setToolTip(
            MODULE_UPDATES_TIP
            if self.services.module_updates is not None
            else MODULE_UPDATES_NO_MODULES
        )
        self._set_custom_module_buttons()
        # A separate gate from the one above, and it must stay separate: the
        # three CMaNGOS games have no manifest store at all, and their
        # worldservers are still compiled from a checkout somebody may have
        # patched. Tying the rebuild to `store` would have taken the control
        # away from three of the four games for a reason that is about
        # manifests.
        self.rebuild_action.setEnabled(self.services.rebuild is not None)
        self.rebuild_banner_button.setEnabled(self.services.rebuild is not None)
        # A third gate, separate again and for the mirror reason: this one is
        # about the install PLAN, not about the store and not about the
        # checkout. It is live for the one game whose plan declares a phase to
        # be re-applied to a server that already exists.
        self.updates_button.setEnabled(self.services.updates is not None)
        # A fourth gate, and the only one in this method that is not settled
        # here: the reading it also needs has not been taken yet, so the button
        # starts dead and lights up (or does not) the first time the status poll
        # finds the database up. Set through the one method so build time and
        # every later moment cannot disagree about the rule.
        self._set_adopt_button()

    def _module_notes(self) -> Mapping[tuple[str, str], str]:
        """T126's per-row sentences, or none. Never raises: a note is not worth a tab."""
        read = self.services.module_notes
        if read is None:
            return {}
        try:
            return read()
        except (OSError, ValueError) as exc:
            logger.debug(f"could not read the module notes for {self.entry.id}: {exc}")
            return {}

    def _session_state(self) -> SessionState:
        """What this session has learned, bundled for the row builder.

        Three plain containers on the view rather than one object, because each
        is filled and cleared by a different slot and a shared mutable object
        would make "who emptied this?" a question. Built into the frozen bundle
        here, at the one place that reads all three.
        """
        return SessionState(
            rebuild_owed=frozenset(self._rebuild_owed),
            sql_owed=dict(self._sql_owed),
            behind=dict(self._behind),
            releases={k: v for k, v in self._behind_release.items() if k in self._behind},
            updated=frozenset(k for k in self._behind_updated if k in self._behind),
        )

    def _module_actions_allowed(self) -> bool:
        """Whether a row's Install/Remove may be pressed at all.

        The old two-button gate, unchanged in substance: a game with no manifest
        store or no applier has nothing to run, and nothing on this tab may
        re-arm the rows while another action of ours is in flight.
        """
        return (
            self.services.store is not None and self.services.applier is not None and not self._busy
        )

    def _installed_clones(self) -> Mapping[str, frozenset[str]] | None:
        """What is in this install's clone folders per family, or `None` for no reader.

        Cheap enough for every reload (directory names, no git), which is why it
        is its own seam and not part of `module_updates` — see
        `ControllerServices`. Shared by the Modules tab and the Tuning tab
        rather than read twice: two readings a moment apart could disagree, and
        a module listed as installed on one tab and not on the other is the
        confusion T41 was reported for.

        `None` and `{}` are DIFFERENT answers, and T42 round 2 turns on the
        difference: `{}` is a seam that answered "nothing installed", which is
        evidence a clone has gone and the facts owed about it may be forgotten;
        `None` is a game with no reader at all, and treating that as evidence
        would throw away everything this session has learned on the first reload
        after an install. A reader that RAISED answers `{}`, exactly as it did
        before this loop was factored out of `reload_modules()`.
        """
        reader = self.services.installed_modules
        if reader is None:
            return None
        try:
            return reader()
        except Exception as exc:  # boundary: an unreadable folder must not kill the UI
            logger.warning(f"could not read which modules are installed: {exc}")
            return {}

    def _unfinished_clones(self) -> Mapping[str, frozenset[str]]:
        """Which clones here stopped part-way through their install, per family (T68).

        `{}` for a game with no reader, and `{}` again for a reader that raised
        — and unlike `_installed_clones()` the two do NOT have to be told apart
        here. Nothing is forgotten on this answer and no row is removed by it:
        it only decides whether a row that is already drawn offers Install or
        Remove, and "we could not tell" has to mean "leave the row as it was",
        which is what an empty mapping produces.
        """
        reader = self.services.unfinished_modules
        if reader is None:
            return {}
        try:
            return reader()
        except Exception as exc:  # boundary: an unreadable claim must not kill the UI
            logger.warning(f"could not read which module installs were left unfinished: {exc}")
            return {}

    def _unknown_modules(self) -> Mapping[str, Mapping[str, apply_module.Doubt]]:
        """Which recorded mods a press stopped on mid-statement, per family (T121).

        `{}` for no reader and for a reader that raised, as `_unfinished_clones()`
        answers: it only changes the badge of a row that is already drawn.
        """
        reader = self.services.unknown_modules
        if reader is None:
            return {}
        try:
            return reader()
        except Exception as exc:  # boundary: an unreadable record must not kill the UI
            logger.warning(f"could not read which module presses were left unfinished: {exc}")
            return {}

    def _load_manifests(self) -> tuple[list[Manifest], list[str]]:
        """This game's catalog in the store's own order, and what would not parse.

        Fills `self._manifests` on the way through, which is the lookup both
        tabs use to get from an id back to its steps and its conf keys.
        """
        self._manifests.clear()
        store = self.services.store
        manifests: list[Manifest] = []
        broken: list[str] = []
        if store is None:
            return manifests, broken
        for kind in FAMILY_FILES:
            # T46: one unparseable USER manifest is skipped and named here rather
            # than raising, so it costs its own row instead of the family's ~20.
            # The `except` below is still the boundary for what DOES raise: this
            # app's own catalog, and either index.
            skipped: list[str] = []
            try:
                items = list(store.load_all(kind, skipped=skipped))
            except Exception as exc:  # boundary: a broken manifest tree must not kill the UI
                broken.append(MODULE_LOAD_FAILED.format(kind=kind, exc=exc))
                continue
            broken += skipped
            manifests += items
            for manifest in items:
                # T42 round 2's key shape, threaded through T43's extraction of
                # this loop: an id two families share is two manifests, and the
                # object this dict hands out goes straight to the applier.
                self._manifests[(manifest.type, manifest.id)] = manifest
        return manifests, broken

    def reload_modules(self) -> None:
        """Re-read the catalog and the clone folders, and redraw the cards.

        Cheap on purpose and called after everything that changes what is
        installed: `store.load_all()` reads files this app shipped, and
        `installed_modules` reads directory names. No git, no network -- that is
        `check_module_updates()`, which is a button for exactly that reason.
        """
        if self.services.store is None:
            self._manifests.clear()
            self.modules_panel.set_rows(())
            if self.services.no_modules_note:
                # T179: a game whose server takes no add-ons says so, not "no
                # manifests yet", which reads as something still to come.
                self.modules_panel.empty_label.setText(self.services.no_modules_note)
            self._refresh_rebuild_banner()
            return
        if self._waits_for_the_distro("modules", self.reload_modules):
            # The rows stay as they were (none, on a tab just built): drawn
            # from the catalog alone, every installed module would read
            # "not installed" and offer Install.
            return
        answered = self._installed_clones()
        installed = answered if answered is not None else {}
        manifests, broken = self._load_manifests()
        if answered is not None:
            self._forget_what_is_no_longer_installed(installed)
        self.modules_panel.set_rows(
            build_module_rows(
                manifests,
                installed,
                self._session_state(),
                self._module_client_dir(),
                # Only what the cache ALREADY knows. Nothing is read here, so
                # the first paint of the tab costs exactly what it did before
                # T44 -- the reads happen afterwards, one event-loop turn at a
                # time, in `_fill_versions()`.
                versions=self._known_versions(installed),
                # T68, read on every reload beside the listing above: the two
                # answers must come from the same moment, or a row is drawn
                # from a folder list and a completion mark taken a press apart.
                unfinished=self._unfinished_clones(),
                # T121, the same moment again: which recorded mods are in doubt.
                unknown=self._unknown_modules(),
                notes=self._module_notes(),
                server_updated=self._server_updated(),
            )
        )
        if broken:
            # Appended, never `setPlainText`: this runs after an install has put
            # its report on screen, and a family that will not parse must not
            # take the report of the action the user just pressed with it.
            self.module_report.appendPlainText("\n".join(broken))
        self._refresh_rebuild_banner()
        self._start_filling_versions()

    @Slot()
    def refresh_modules(self) -> None:
        """The Refresh press: forget what every clone was at, then reload.

        The one invalidation that is not about a single module. A clone can
        change under this app -- a `git pull` in a terminal, a folder swapped
        by hand -- and Refresh is the press that says "read the disk again".
        """
        self._versions.clear()
        self.reload_modules()

    def _known_versions(
        self, installed: Mapping[str, frozenset[str]]
    ) -> dict[tuple[str, str], str]:
        """The version of every installed clone the cache has ALREADY read.

        Reads nothing. `VersionCache.known()` is the no-read accessor for
        exactly this call site: a version lookup that read here would put a
        `git log` per installed module back on every reload, which is the cost
        T41 refused and T42 restated.
        """
        server_dir = self.services.controller.server_dir
        found: dict[tuple[str, str], str] = {}
        for family, ids in installed.items():
            for item_id in ids:
                version = self._versions.known(server_dir, family, item_id)
                if version is not None:
                    found[(family, item_id)] = version
        return found

    def _start_filling_versions(self) -> None:
        """Read the clones this tab has not read yet, AFTER the tab is on screen.

        One module per event-loop turn, through `QTimer.singleShot(0, ...)`:
        the reads are subprocesses, and twenty of them in a row on the GUI
        thread is the second of a frozen tab that item 1 forbids. A row that
        fills in late is fine.

        `_filling_versions` keeps one walk running at a time. `reload_modules()`
        is called after every install, every remove and every update check, and
        a second walk started by a reload that happened mid-walk would read the
        same clones twice.
        """
        if self._filling_versions or self.services.module_version is None:
            return
        self._filling_versions = True
        QTimer.singleShot(0, self, self._fill_next_version)

    @Slot()
    def _fill_next_version(self) -> None:
        """Read ONE unread clone, draw it, and come back for the next.

        Every refusal this can meet is already inside the seam
        (`git.RunnerGit.head_version` answers `None` for a folder with no
        `.git`, a git that refused and a git that is not there), so there is
        nothing to catch here and nothing that can turn a reload into a
        failure.

        T213: a closed tab ends the walk here. `drop_controller()` in main.py
        runs `shutdown()` and then `deleteLater()`, and a tick already queued
        used to come back after both and call `set_version` on a deleted
        label -- a `RuntimeError` traceback inside a Qt slot. The re-arm
        names `self` as the timer's context object as well, so a tick queued
        before the delete is dropped by Qt rather than delivered.
        """
        if getattr(self, "_closed", False):
            self._filling_versions = False
            return
        if self._waits_for_the_distro("modules", self.reload_modules):
            # Stopped mid-walk: the reload that runs once it is up walks again.
            self._filling_versions = False
            return
        server_dir = self.services.controller.server_dir
        for widget in self.modules_panel.rows():
            row = widget.data
            if not row.installed or self._versions.has(server_dir, row.family, row.id):
                continue
            self.modules_panel.set_version(
                row.family, row.id, self._versions.fill(server_dir, row.family, row.id)
            )
            QTimer.singleShot(0, self, self._fill_next_version)
            return
        self._filling_versions = False

    def _forget_what_is_no_longer_installed(self, installed: Mapping[str, frozenset[str]]) -> None:
        """Drop every owed fact about a module that is no longer on disk (round 2).

        A clone can leave without this app pressing anything -- a folder deleted
        in Explorer, a server directory restored from a backup, a module removed
        by a script. Until this ran, the catalog row came back reading "Not
        installed" with a `Rebuild pending` chip on it and the module still
        named in the banner.

        Reconciled HERE, in one place, rather than by gating the chips in
        `build_module_rows()`: the chips are built from the rows and the banner
        is built from `_rebuild_owed` directly, so gating only the chips would
        leave the banner claiming a module that is gone. Reconciling the sets
        makes the two agree by construction.

        Only ever called where the seam ANSWERED. A game with no
        `installed_modules` reader reads as "nothing is installed anywhere",
        and treating that as evidence would throw away every fact this session
        has learned.

        Matched by `(family, id)` since round 2: the three sets are keyed that
        way now, so this is an exact comparison rather than a bare-id match
        that also forgot an `ale` because a `module` of the same name had gone.
        """
        here = {(family, name) for family, names in installed.items() for name in names}
        self._rebuild_owed &= here
        self._sql_owed = {key: value for key, value in self._sql_owed.items() if key in here}
        self._behind = {key: value for key, value in self._behind.items() if key in here}

    def _refresh_rebuild_banner(self) -> None:
        """Show the amber banner iff something owes a rebuild, and name what.

        Session state only. A restart forgets it, exactly as the report box
        always has -- a persisted marker is a file with its own invalidation
        rules (whose rebuild? which modules? still true after a folder was moved
        by hand?) and T42 deliberately leaves it out rather than ship one that
        can be wrong.
        """
        # The ids, not the pairs: the banner names modules to a person, and
        # `('module', 'mod-transmog')` is the key's spelling, not a name. A
        # `set` first, so an id two families owe is named once.
        owed = sorted({item_id for _family, item_id in self._rebuild_owed})
        self.rebuild_banner.setVisible(bool(owed))
        self.rebuild_banner_label.setText(REBUILD_BANNER.format(names=", ".join(owed)))

    @Slot(str)
    def _row_install(self, module_id: str) -> None:
        """A row's own Install. Selects it first, then the one shared handler."""
        self.modules_panel.select(module_id)
        self._module_action("install")

    @Slot(str)
    def _row_remove(self, module_id: str) -> None:
        self.modules_panel.select(module_id)
        self._module_action("remove")

    @Slot(str, str)
    def _chip_pressed(self, module_id: str, label: str) -> None:
        """An owed chip's press: its own sentence, in the box every answer is read in.

        The chip carries the detail rather than the view rebuilding it, so the
        words a press shows are the words the row was built with -- there is no
        second formatter to drift.
        """
        try:
            row = self.modules_panel.row(module_id)
        except KeyError:
            return
        for chip in row.data.chips:
            if chip.label == label:
                self.module_report.setPlainText(chip.detail)
                return

    @Slot(str, str)
    def _chip_action_pressed(self, module_id: str, action: str) -> None:
        """The subpanel's own press: run the job that chip names (T44 item 4).

        Routed to the SAME slots the action bar's buttons are bound to, not to
        copies of them: those carry the confirm dialog, the busy gate and the
        refusals, and a second caller that skipped any of them would be a
        second route to keep in step.

        The row is selected first for `_row_install()`'s reason -- a child
        button consumes its own click, and a job started from a row the tab has
        not selected writes its report against the wrong subject.
        """
        self.modules_panel.select(module_id)
        if action == "rebuild":
            self.rebuild_server()
        elif action == "sql":
            self.apply_module_sql()
        elif action == "update":
            # T44 item 2, and round 2's finding 1. `Applier.update()` runs the
            # install steps over the clone that is already there -- which IS
            # the pull -- after asking the three questions `install()` skips
            # for a folder its own claim vouches for: the repository, the
            # working tree, and what HEAD carries. A `reset --hard` over work
            # nobody looked at is what the ticket forbids in so many words.
            self._module_action("update")
        elif action == "server_update":
            # T146. A folder the SERVER install cloned (`mod-playerbots` on
            # WotLK) has no manifest and so no per-module steps; the button
            # that moves it is the T64 one, and this is that button's own slot
            # -- its dialog, backup offer, busy gates and refusals included.
            self.update_to_latest()

    def _server_updated(self) -> frozenset[PurePosixPath]:
        """Where "Update the server to latest…" moves a checkout here; empty with no route (T146).

        Two facts the view already holds, joined here rather than carried as a
        new service: the route is `services.update_to_latest`, and where it
        moves things is this entry's catalog data (`native.server_update_dests()`).
        An install with no route gets an empty set, so no row offers a press
        whose slot would return at its first line.
        """
        if self.services.update_to_latest is None:
            return frozenset()
        return native.server_update_dests(self.entry)

    def _selected_row_is_uncatalogued(self) -> bool:
        """Is the selected row one of T41's "installed here, not in the catalog" rows?"""
        item_id = self.modules_panel.selected_id()
        if item_id is None:
            return False
        try:
            return not self.modules_panel.row(item_id).data.catalogued
        except KeyError:
            return False

    def selected_manifest(self) -> Manifest | None:
        """The manifest of the selected row, by its FAMILY as well as its id.

        The family comes off the row rather than from a bare-id lookup, because
        the answer is handed straight to the applier: on an id that two families
        share, the id alone would install the other one's steps (round 2).
        """
        row = self.modules_panel.selected_row()
        if row is None or not row.data.catalogued:
            return None
        return self._manifests.get((row.data.family, row.data.id))

    def _module_action(self, action: str) -> None:
        """Run `action` on the selected row: `install`, `remove` or `update`.

        Three words and three routes since round 2. `update` was a second word
        for the install ROUTE until the review found what that route does to a
        clone this app's claim vouches for; it now runs `Applier.update()`,
        which asks the repository, the working tree and HEAD before it lets a
        `reset --hard` near the folder.
        """
        manifest = self.selected_manifest()
        applier = self.services.applier
        if manifest is None and self._selected_row_is_uncatalogued():
            # T41's own rows: installed here, no manifest in this game's
            # catalog, so there is nothing for the applier to run. Said rather
            # than returned in silence — a press that does nothing and explains
            # nothing is the defect this ticket exists to remove, one control
            # further along (review, 2026-09-12).
            self.module_report.setPlainText(UNCATALOGUED_PRESS)
            return
        if manifest is None or applier is None:
            return
        row = self.modules_panel.selected_row()
        if (
            action == "install"
            and row is not None
            and not row.data.installed
            and not row.data.installable
        ):
            # T55. The row's own Install is disabled for this, but the context
            # menu reaches the same handler, and the prompt dialog below is
            # asked BEFORE the applier: `mod-ah-bot-plus` has a question with no
            # default, so the user answered it and was then told no. Refused
            # here, first, in the row's own words.
            self._module_pending = None
            self.module_report.setPlainText(
                f"install {manifest.id}: not started — {row.data.install_reason} "
                f"Nothing on this machine was changed."
            )
            return
        if action == "install" and self._stopped_for_the_client(f"install {manifest.id}", manifest):
            return
        if (
            action == "update"
            and manifest.client
            and self.services.play_client_dir is not None
            and self._play_client_gone_for(f"update {manifest.id}")
        ):
            return
        relative = reapplies_on_top(manifest)
        if action in ("install", "update") and relative:
            # T115, before any question (T55's order): a mob multiplier applied
            # with values nobody can read cannot be run again safely, and no
            # answer in the dialog would change that. The same sentence the
            # applier raises. One small JSON read, as `_module_values()` does,
            # and only for the four relative mods.
            refusal = applier.reapply_refusal(manifest)
            if refusal is not None:
                self._module_pending = None
                self.module_report.setPlainText(f"{action} {manifest.id}: {refusal}")
                return
        # An update re-runs the INSTALL-time steps -- it is the install over
        # content that has moved -- so it answers the install's prompts. A mob
        # multiplier leaves no folder; its applied record is what says a second
        # Install is a re-run (T115), and since T121 it also marks the row
        # installed -- read here directly too, for a game wired without that.
        again = (
            action == "update"
            or (row is not None and row.data.installed)
            or (relative and applier.applied_record(manifest)[0] is not None)
        )
        go_ahead, values = self._module_values(manifest, MODULE_ACTION_STEPS[action], again=again)
        if not go_ahead:
            self._module_pending = None
            self.module_report.setPlainText(
                f"{action} {manifest.id}: cancelled — nothing on this machine was changed."
            )
            return
        run = {
            "install": applier.install,
            "remove": applier.remove,
            "update": applier.update,
        }[action]
        self._acting_on = manifest
        self._module_pending = f"{action} {manifest.id}"
        self._update_asked = (manifest, values) if action == "update" else None
        self.module_report.setPlainText(f"{self._module_pending}…")
        self._run(lambda: run(manifest, values), self._module_done, self._module_failed)

    def _module_values(
        self, manifest: Manifest, action: When, *, again: bool = False
    ) -> tuple[bool, Mapping[str, str] | None]:
        """Whether to go ahead, and the answers to hand the applier.

        Two values rather than a sentinel because there are three outcomes and
        only one of them is a mapping: go ahead with answers, go ahead with
        `None` (the manifest asked nothing, which is byte-for-byte the old
        call), and do not go ahead at all.

        The tab asked nothing and passed nothing until 2026-09-07, which made
        `mod-ah-bot` and `mod-ah-bot-plus` — the only two shipped manifests with
        a prompt carrying no default — unreachable from the GUI: the applier
        filled in every prompt that HAD a default and then raised on the one
        that did not, after the clone. See `widgets/manifest_prompt.py`.

        The gate is `apply.must_ask()`: since T104 (the owner, "ask all,
        remember answers") every question an install or update renders is put,
        pre-filled with what this install answered last time, else the
        manifest's default; a remove asks only what the install's record cannot
        answer, and the applier fills the rest from that record. Which shipped manifests
        ask is pinned by `test_every_module_whose_install_renders_a_question_
        asks_it`, not written here. A manifest that asks nothing gets no window
        and the applier gets `None` rather than `{}` — the call it has always
        been given.

        T92 (2026-09-22) had already widened Install to every prompt it renders:
        until then only a prompt with NO default opened the dialog, so `xp-rates`
        never asked its rates, `sitmeanrest` never asked its seconds, and
        `hearthstone-cd`'s `choice` (T100) applied upstream's RESET file unasked.
        A default shown in a box the person can change is an answer; a default
        written unseen is not.
        """
        needed = required_prompts(manifest, action)
        if not needed:
            return True, None
        # Read here, on the GUI thread: one small JSON file in the server folder,
        # the same class of read as the clone-folder listing `reload_modules()`
        # already does here, and the dialog that needs it opens on this thread.
        applier = self.services.applier
        remembered = applier.remembered_answers(manifest) if applier is not None else {}
        # A Remove of a mob multiplier divides by what the database was
        # multiplied by, which is the APPLIED record (T115); the answers T104
        # keeps after a Remove only pre-fill the boxes.
        known = remembered
        if action == "remove" and applier is not None and reapplies_on_top(manifest):
            known = applier.applied_record(manifest)[0] or {}
        asked = tuple(p for p in needed if must_ask(p, action, known))
        if not asked:
            return True, None
        answers = self._prompt_asker(
            self,
            manifest,
            asked,
            again=again,
            remembered=remembered,
            removing=action == "remove",
        )
        return (False, None) if answers is None else (True, answers)

    def _custom_route(self) -> CustomModuleInstall | None:
        """The install seam, or `None` where this game has no custom-module route."""
        return self.services.module_install_custom

    def _set_custom_module_buttons(self) -> None:
        """Grey the two custom-module buttons where the game has no route for them.

        Each button needs BOTH halves: something that can derive its kind of
        source, and somewhere to install the result. Either missing is a game
        that cannot do this at all, and a control that is visibly unavailable
        beats one that is pressed and then explains itself (roadmap 6.1).
        """
        route = self._custom_route()
        link = route is not None and self.services.module_from_link is not None
        folder = route is not None and self.services.module_from_folder is not None
        self.module_link_button.setEnabled(link)
        self.module_folder_button.setEnabled(folder)
        self.module_link_button.setToolTip(MODULE_LINK_TIP if link else MODULE_CUSTOM_NO_ROUTE)
        self.module_folder_button.setToolTip(
            MODULE_FOLDER_TIP if folder else MODULE_CUSTOM_NO_ROUTE
        )

    @Slot()
    def install_module_from_link(self) -> None:
        """Ask for a link, derive a manifest from it, and install it (design §3.3).

        The derive runs HERE, on the GUI thread, before anything is queued: it
        reads no disk and touches no network — it parses a string — so a
        refusal is one sentence in the report with no job started and nothing
        written. Only the install, which clones, goes to a worker.
        """
        derive, route = self.services.module_from_link, self._custom_route()
        if derive is None or route is None:
            return
        text = self._link_asker(self, MODULE_LINK_DIALOG_TITLE)
        if text is None:
            self._module_pending = None
            self.module_report.setPlainText(MODULE_LINK_CANCELLED)
            return
        self._install_custom_module("install from link", lambda: derive(text), None, route)

    @Slot()
    def install_module_from_folder(self) -> None:
        """Ask for a folder, derive a manifest from it, and install it (design §3.3)."""
        derive, route = self.services.module_from_folder, self._custom_route()
        if derive is None or route is None:
            return
        folder = self._folder_asker(self, MODULE_FOLDER_DIALOG_TITLE)
        if folder is None:
            self._module_pending = None
            self.module_report.setPlainText(MODULE_FOLDER_CANCELLED)
            return
        self._install_custom_module("install from folder", lambda: derive(folder), folder, route)

    def _install_custom_module(
        self,
        what: str,
        derive: Callable[[], Manifest],
        folder: Path | None,
        route: CustomModuleInstall,
    ) -> None:
        """Derive, then run the install through the same slots Install selected uses.

        `_module_done` and `_module_failed` are reused rather than copied, so
        the report is `_format_report`'s — the one that carries the C++ rebuild
        sentence and the pending-SQL lines — and there is no second place for
        that copy to drift.

        **One question can stand between the derive and the job** (T47). A
        derived id is the name of a folder under `modules/`, so it can already
        be one this app filled from a DIFFERENT repository, and installing over
        it is a `git reset --hard` that nobody asked about. The question is put
        here, on the GUI thread, before anything is queued — the engine runs on
        a worker and cannot open a dialog — and Cancel starts nothing at all:
        `route` is never called, so no git runs and the folder is untouched.
        Every other way this install would destroy something stays a refusal
        from the engine, arriving through `_module_failed` like any other.
        """
        try:
            manifest = derive()
        except Exception as exc:  # boundary: a refusal is a sentence, not a crash
            # The seam's own words, verbatim and alone. Every one of them ends
            # in "Nothing on this machine was changed", which is true here
            # because nothing has run yet: prefixing them with a "FAILED:"
            # line would put this view's vocabulary in front of a sentence
            # written to be read on its own.
            self._module_pending = None
            self._acting_on = None
            self.module_report.setPlainText(str(exc))
            self.action_failed.emit(str(exc))
            return
        if self._stopped_for_the_client(f"{what} {manifest.id}", manifest):
            return
        self._acting_on = manifest
        question = self._replacement_question(manifest)
        if question is not None and not self._confirm(
            MODULE_REPLACE_TITLE.format(id=manifest.id), question
        ):
            logger.info(f"{what} {manifest.id} declined at the replace-the-checkout question")
            self._module_pending = None
            self._acting_on = None
            self.module_report.setPlainText(
                f"{what} {manifest.id}: cancelled — nothing on this machine was changed."
            )
            return
        self._module_pending = f"{what} {manifest.id}"
        self.module_report.setPlainText(f"{self._module_pending}…")
        self._run(
            lambda: route(manifest, folder, replacing=question is not None),
            self._module_done,
            self._module_failed,
        )

    def _replacement_question(self, manifest: Manifest) -> str | None:
        """The seam's question about the clone already at this manifest's path, if any.

        Anything the seam raises is swallowed into `None`, and that is safe in
        one direction only — which is why it is done here rather than left to
        crash the GUI thread. `None` means no question is asked, and an install
        that WOULD have been asked about is then refused by the engine's own
        guard with "Nothing was changed." A failure to ask never becomes a
        silent reset.
        """
        ask = self.services.module_replacement_question
        if ask is None:
            return None
        try:
            return ask(manifest)
        except Exception as exc:  # boundary: git or the disk, on the GUI thread
            logger.warning(f"could not tell what installing {manifest.id} would replace: {exc}")
            return None

    def _module_client_dir(self) -> Path | None:
        """The folder module client files go into: the ready-to-play client once there is one."""
        if self.services.play_client_dir is not None:
            return self.services.play_client_dir
        return self.services.client_dir

    def _play_client_gone_for(self, what: str) -> bool:
        """True (nothing started) when the recorded ready-to-play client is not this server's.

        Says so in the module report and offers to make it again; the press is
        then made again by the player on the rebuilt tab.
        """
        if self._usable_play_client(offer_remake=False) is not None:
            return False
        play = self.services.play_client_dir
        assert play is not None
        self._module_pending = None
        self._acting_on = None
        self.module_report.setPlainText(
            f"{what}: not started \u2014 {self._play_client_gone_text(play)}"
        )
        self._offer_remake(play)
        return True

    def _stopped_for_the_client(self, what: str, manifest: Manifest) -> bool:
        """T62: tell the user before an install whose client half would be skipped.

        `Applier._client()` skips every `client` step when the install has no
        client folder, and says so only in the report AFTER the server half has
        landed — so `mod-arac` put its SQL and DBCs in, left `Patch-A.MPQ` out,
        and the user found out in the game, if at all. Asked here instead,
        before anything runs, by every route on this tab that installs: the
        selected row (its button, its menu entry, a chip) through
        `_module_action()`, and a link or a folder through
        `_install_custom_module()`.

        True means the install was NOT started. Setting the folder is
        `change_client_dir()` itself — the Server tab's own press, with its
        refusals — and not a copy of it; a successful set rebuilds this tab
        (`client_dir_changed`), which is why the report is written BEFORE it
        and nothing touches `self` after. The user presses Install again on the
        rebuilt tab, where the folder is set and this asks nothing.
        """
        if not manifest.client:
            return False
        if self.services.play_client_dir is not None:
            # T181: the applier writes into the ready-to-play client, so the
            # player's own folder is not needed -- but that one must still be
            # this server's, or `_client()`'s mkdir would make a bare `Data/`.
            return self._play_client_gone_for(what)
        if self.services.client_dir is not None:
            return False
        self._module_pending = None
        self._acting_on = None
        self.module_report.setPlainText(
            f"{what}: not started — {manifest.name} also changes your game client, and no "
            "client folder is set for this install. Nothing on this machine was changed."
        )
        if self.services.set_client_dir is None:
            # No write seam, so no button to offer: say it, and stop.
            QMessageBox.information(
                self, f"{manifest.name} needs your game client", client_notice(manifest)
            )
            return True
        if ask_to_set_client_dir(self, manifest):
            self.change_client_dir()
        return True

    @Slot(object)
    def _module_done(self, result: object) -> None:
        self._module_pending = None
        self._update_asked = None
        acted_on, self._acting_on = self._acting_on, None
        if not isinstance(result, ApplyReport):
            return
        self.module_report.setPlainText(_format_report(result))
        self._note_session_facts(result, acted_on)
        # The record is dropped AFTER the report is on screen and after the
        # remove returned -- a forget before the remove would drop the record of
        # a remove that then failed, leaving a folder on disk with no row in the
        # tab to try again with (`purge.py`'s ordering, phase8-decisions).
        forget = self.services.module_forget
        if result.action == "remove" and forget is not None:
            # The manifest the press was really about, not one looked up by the
            # report's id: the record being dropped is a file on disk named after
            # this manifest (round 2). Matched on the family as well since T48,
            # now that the report carries one.
            if acted_on is not None and (acted_on.type, acted_on.id) == (
                result.family,
                result.item_id,
            ):
                forget(acted_on)
        # Re-read after EVERY report, where it used to happen for the two
        # outcomes that changed what was in the list (a custom module added, a
        # record dropped). Since T42 a report also changes what the rows SAY --
        # the rebuild chip, the SQL chip and the banner all come from the report
        # that was just filed -- and the reason the old rule was narrow is gone:
        # `reload_modules()` no longer takes the selection with it, because
        # `ModulesPanel.set_rows()` keeps it wherever the id still exists.
        self.reload_modules()
        # And the Tuning tab, for the same reason one size along: an install
        # deploys a conf and a remove takes one away, so the settings this
        # install HAS changed with this report (T43).
        self.reload_tuning()

    def _note_session_facts(self, result: ApplyReport, acted_on: Manifest | None) -> None:
        """Record what this report says is still owed, for the chips and the banner.

        A remove drops the id from all three: the module is gone, so an "update
        available" for it is about a checkout that no longer exists and a
        pending-SQL list names files nothing will read.
        """
        item_id = result.item_id
        # Whatever the action was, this module's clone may be at a different
        # commit now: an install clones or fast-forwards it, a remove deletes
        # it. Dropped before `reload_modules()` runs, so the redraw does not
        # hand the row the sha it had before the action (T44 item 1).
        self._versions.forget(item_id)
        # Keyed by the REPORT, which names its own row since T48. Until then the
        # family had to come from `_acting_on`, and a report that did not match
        # the press on record was dropped -- which, once the report could say
        # which row it was about, threw away a rebuild or an SQL import the
        # user still owed for no better reason than bookkeeping (T48 review 1).
        #
        # But a mismatch means something upstream of this is wrong -- both live
        # routes set `_acting_on` immediately before their `_run()` -- so an
        # unverified report may only ADD what it says is owed. It never CLEARS:
        # a misattributed remove would otherwise wipe another twin's rebuild,
        # SQL and update facts, and nothing on reload puts them back (T48
        # review 2). Losing a warning is the one outcome this must not have;
        # a spare one is corrected by the next verified press or a restart.
        key = (result.family, item_id)
        if acted_on is None or (acted_on.type, acted_on.id) != key:
            on_record = "none" if acted_on is None else f"{acted_on.type} {acted_on.id}"
            logger.warning(
                f"the {result.action} report for {result.family} {item_id} does not match the "
                f"press on record ({on_record}); what it says is owed is recorded, nothing is "
                "cleared"
            )
            if result.action != "remove":
                if result.rebuild_required:
                    self._rebuild_owed.add(key)
                owed = _pending_sql_names(result)
                if owed:
                    self._sql_owed[key] = owed
            return
        if result.action == "remove":
            self._rebuild_owed.discard(key)
            self._sql_owed.pop(key, None)
            self._behind.pop(key, None)
            return
        # The clone has just been fetched and reset to its upstream tip -- that
        # is what `install()` does over a folder that is already there -- so
        # any "N commits behind" this session counted is now a figure about a
        # commit the checkout has moved off. Dropped rather than recounted: a
        # recount costs a network round trip, and a stale number is a wrong one
        # (T44 item 2).
        self._behind.pop(key, None)
        if result.rebuild_required:
            self._rebuild_owed.add(key)
        owed = _pending_sql_names(result)
        if owed:
            self._sql_owed[key] = owed
        else:
            self._sql_owed.pop(key, None)

    @Slot(object)
    def _module_failed(self, exc: object) -> None:
        asked, self._update_asked = self._update_asked, None
        if isinstance(exc, ReleaseDirectionUnknown) and asked is not None:
            self._ask_to_update_unchecked(exc, *asked)
            return
        what, acted_on = self._module_pending or "module action", self._acting_on
        self._module_pending = None
        self._acting_on = None
        if acted_on is not None:
            # A failure is not "nothing happened" (round 2). `install()` fetches
            # and RESETS the checkout first and then runs deploy, patches, SQL,
            # conf and the client copy; any of those can raise with the folder
            # already at a different commit. The success path drops the cached
            # version through `_note_session_facts()`, and leaving it here left
            # the row showing a sha the clone had moved off -- a wrong sha,
            # which item 1 says is worse than none.
            self._versions.forget(acted_on.id)
        self.module_report.setPlainText(f"{what} FAILED: {exc}")
        # Re-read the disk on failure too (T55 review). The same partial states
        # the comment above names -- a clone made before the SQL step raised, a
        # deploy removed before the rmtree did -- change what is installed, and
        # since T55 a row's Install is locked or opened by what is installed. A
        # tab that kept the pre-press picture would offer an install the applier
        # now refuses, or hold one shut that it would allow. Neither reload
        # writes the report, so the FAILED line above stays what the user reads.
        self.reload_modules()
        self.reload_tuning()
        self.action_failed.emit(str(exc))

    def _ask_to_update_unchecked(
        self, exc: ReleaseDirectionUnknown, manifest: Manifest, values: Mapping[str, str] | None
    ) -> None:
        """T150: nobody could show this release is not a step back -- ask, default No.

        The engine refused before it changed anything, so a No is a cancel in
        the tab's own words. A Yes runs the SAME update again with the same
        answers and the question's own `approval` -- the HEAD and the release
        it named, and nothing wider -- through the same slots, so its report and
        its failures (a new question included) land where the first press's did.
        """
        applier = self.services.applier
        if applier is None or not self._confirm(
            MODULE_UPDATE_UNCHECKED_TITLE.format(id=manifest.id), exc.question
        ):
            logger.info(f"update {manifest.id} declined at the unchecked-release question")
            self._module_pending = None
            self._acting_on = None
            self.module_report.setPlainText(
                f"update {manifest.id}: cancelled — nothing on this machine was changed."
            )
            return
        self._acting_on = manifest
        self._module_pending = f"update {manifest.id}"
        # Kept for the retry too: a yes about one release, answered after the
        # newest became another, comes back as a NEW question about that one.
        self._update_asked = (manifest, values)
        self.module_report.setPlainText(f"{self._module_pending}…")
        self._run(
            lambda: applier.update(manifest, values, approved=exc.approval),
            self._module_done,
            self._module_failed,
        )

    @Slot()
    def check_module_updates(self) -> None:
        """Ask each installed module how far behind its upstream it is (checklist 8.7a).

        Read-only, so it takes no arming and no confirmation: it fetches into
        each clone's `.git` and counts. It still goes through `_run()` and the
        busy lock, because a fetch per installed module is a network round trip
        per installed module and the GUI thread must not hold them.

        What comes back is already a list of sentences — `apply.ModuleUpdate`
        formats its own row. The definition of done for this clause is that the
        figure equals `git rev-list --count HEAD..FETCH_HEAD` run by hand, and
        a number the view re-formatted would be a second place for it to change.
        Where a shallow checkout makes that range wrong (T147) the row carries
        no figure, only that an update is there -- or GitHub's figure, when its
        compare API answered for that row (T148, `apply._question()`).
        """
        route = self.services.module_updates
        if route is None:
            return
        self._set_busy(True)
        self._module_pending = "check for module updates"
        self.module_report.setPlainText(MODULE_UPDATES_RUNNING)
        self._run(route, self._module_updates_done, self._module_updates_failed)

    @Slot(object)
    def _module_updates_done(self, result: object) -> None:
        self._set_busy(False)
        self._module_pending = None
        self.module_updates_button.setEnabled(self.services.module_updates is not None)
        if not isinstance(result, tuple):
            return
        rows = [row.line for row in result]
        self.module_report.setPlainText("\n".join(rows) if rows else MODULE_UPDATES_NONE)
        # The figure stays the seam's own -- `ModuleUpdate.line` is still what
        # the report prints. What is kept here is only the COUNT, for the row's
        # chip, and `None` ("could not ask") is deliberately not a zero: a
        # checkout git could not answer for gets no chip rather than a
        # confident "up to date".
        # Keyed `(row.family, key)`: `apply.module_updates()` enumerates ONE
        # clone directory and says which family it read, so the family is the
        # seam's answer rather than a guess. It was the literal `"module"`
        # until T126, when Tortoise began counting its `mod` clones (the two
        # client addons in `sql_scripts/clones/`).
        # `Behind.UNCOUNTED` is kept (T147): a shallow checkout that is behind
        # by a number it cannot prove still has an update to offer.
        self._behind = {
            (row.family, row.key): row.behind for row in result if is_behind(row.behind)
        }
        self._behind_release = {(row.family, row.key): row.release for row in result if row.release}
        self._behind_updated = {
            (row.family, row.key)
            for row in result
            if row.release and row.release == row.installed_release
        }
        self.reload_modules()

    @Slot(object)
    def _module_updates_failed(self, exc: object) -> None:
        self._set_busy(False)
        self._module_pending = None
        self.module_updates_button.setEnabled(self.services.module_updates is not None)
        self.module_report.setPlainText(f"check for module updates FAILED: {exc}")
        self.action_failed.emit(str(exc))

    @Slot()
    def apply_module_sql(self) -> None:
        """Run this install's importer over the modules on disk, and show what it prints.

        One press, no arming. The two-press gesture on the Server tab guards
        the actions that overwrite what is already there; this one adds update
        files that upstream's own `docker compose up` would apply on every
        start, and re-running it applies nothing a second time because the
        importer ledgers each file in `updates`.

        What it is NOT is a button that always works. The rule that a module's
        SQL must not be written underneath a live worldserver is checklist
        8.7a's, and it is enforced once, in `docker.apply_module_sql()`, which
        every caller passes through — so this method holds no copy of it and
        cannot come to disagree with it. A press while the server is running
        comes back as the refusal, in `_module_sql_failed`, saying to press
        Stop first.

        The button is locked for the length of the run and so are the Server
        tab's, because `compose run --rm` starts a NEW container each time
        rather than refusing while one is up: nothing below this tab would stop
        a second press, or a Start, from racing the writes.
        """
        route = self.services.module_sql
        if route is None:
            return
        # One call, not a second copy: `_set_busy(True)` is what locks this
        # button as well as the Server tab's, so the two cannot drift into
        # disagreeing about whether an importer is running.
        self._set_busy(True)
        self._module_sql_running = True
        self._module_pending = "apply module SQL"
        self.module_report.setPlainText(MODULE_SQL_RUNNING)
        # The sink is the relay's emitter, not `_module_sql_line`: this lambda
        # runs on a worker thread and everything it calls runs there too.
        self._run(
            lambda: route(self._module_sql_relay.emit_line),
            self._module_sql_done,
            self._module_sql_failed,
        )

    @Slot(str)
    def _module_sql_line(self, line: str) -> None:
        """Append one of the importer's lines. Reached only through the relay.

        Appended rather than summarised, and kept rather than trimmed to a
        tail: this run's whole output is a handful of lines even on a big
        install — one `>> Applying update <file>.sql` per pending file — and
        `--rm` deletes the container when it exits, so `docker compose logs`
        has nothing to add afterwards. What is on screen is what there is.
        """
        text = lines.parse(line).text.rstrip()  # see `_import_line()` for why
        if not text:
            return
        self.module_report.appendPlainText(text)

    @Slot(object)
    def _module_sql_done(self, result: object) -> None:
        self._set_busy(False)
        self._module_sql_running = False
        self._module_pending = None
        self.module_sql_button.setEnabled(self.services.module_sql is not None)
        # Deliberately not "N modules applied". This tab cannot count that: the
        # importer applies a FILE at a time and says so itself, and a module
        # whose SQL was already in `updates` is a module that correctly gets
        # nothing. Claiming a number here would be the same lie in a new place
        # — the one 8.7a's other half was fixed for.
        if isinstance(result, docker.AttachedRun):
            self.module_report.appendPlainText(MODULE_SQL_FINISHED)
        # The importer was handed every module folder on this install, so the
        # pending-SQL chips are all answered by the one run -- there is no
        # per-module outcome to keep, and `_module_sql_done` deliberately does
        # not claim a number (see the comment above).
        self._sql_owed.clear()
        self.reload_modules()
        # The run starts this install's database if it was down and leaves it
        # up, so the Server tab's line is stale — and `_set_busy(False)` does
        # not bring Start and Stop back; only a status read does.
        self.refresh_status()

    @Slot(object)
    def _module_sql_failed(self, exc: object) -> None:
        self._set_busy(False)
        self._module_sql_running = False
        self._module_pending = None
        self.module_sql_button.setEnabled(self.services.module_sql is not None)
        # The refusal verbatim and under whatever the importer had already
        # printed, because a run that got part-way is a different situation
        # from one that never started and only its own output can tell them
        # apart.
        self.module_report.appendPlainText(f"FAILED: {exc}")
        self.action_failed.emit(str(exc))
        # As above: a refusal can arrive after the database was started, and
        # Start and Stop are locked until something reads the status.
        self.refresh_status()

    def rebuild_server(self) -> bool:
        """Ask, then recompile this install and restart it on the result. False if not started.

        Returns whether anything was started, so a caller — and every test of
        the decline path — can tell "the user said no" from "the button is
        broken" without inspecting the seam.

        **The confirmation is a real gate, and everything about it is chosen so
        that it cannot be clicked through.** Yes/No with No as the default, so
        Enter declines; `said_yes(...)` rather than a check for No, because
        Escape and the window's close button both answer `NoButton` and only an
        explicit Yes may take somebody's server down for an hour; and the text
        is `rebuild_confirmation()`'s, which names the folder and quotes this
        project's own measured compile times rather than "this may take a
        while". The view does not author that copy — `catalog/installer.py`
        does, where it has assertions on it that run without Qt.

        Cancelling changes nothing at all: the seam is not called, so no engine
        is built, no daemon is asked anything and the running server is not
        touched. `test_declining_the_rebuild_confirmation_starts_nothing`.

        The refusals a rebuild can raise — no install record, a compose file
        this app did not write, a server inside a WSL distro — arrive as
        exceptions from the generator and land in the panel's own FAILED line
        plus `action_failed`, which is the same route every other refusal on
        this tab takes.
        """
        source = self.services.rebuild
        if source is None:
            return False
        if self.rebuild_log.running:
            QMessageBox.information(
                self, "Already rebuilding", "This server is already being rebuilt."
            )
            return False
        if self._busy:
            # A rebuild replaces the very containers the Server tab's actions
            # are operating on, and `busy_reason()` records that one of those —
            # the import — cannot be stopped at all and runs 10-30 minutes.
            # Refused rather than queued: the honest outcome of two actions
            # wanting the same containers is that one of them waits, and the
            # user is the one who should choose which.
            QMessageBox.information(
                self,
                "Something else is running",
                "This server is busy with another action — wait for it to finish on the "
                # T155: named in full and placed, because the reader is being
                # sent to the Server tab and has to find the press again after.
                "Server tab, then press "
                f"{server_build_presses.under_server_build(REBUILD_BUTTON_LABEL)} again. "
                "Nothing was started.",
            )
            return False
        if not said_yes(
            QMessageBox.question(
                self,
                f"Rebuild {self.entry.name}?",
                rebuild_confirmation(self.entry, self.services.controller.server_dir),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        ):
            logger.info(f"rebuild of {self.entry.id} declined at the confirmation")
            return False
        # The engine's own cancel, handed to the panel so its Stop button reaches
        # a build that is blocked between lines rather than only stopping the
        # reader of them.
        cancel = self._rebuild_cancel()
        self._rebuild_is_compile = True
        self._rebuild_moves_sources = False
        return self.rebuild_log.run(
            lambda: self._watch_for_load_wait(source(cancel)),
            title=f"Rebuilding {self.entry.name}",
            cancel=cancel,
            record_as=self._run_record_kind(),
        )

    def _set_update_buttons(self) -> None:
        """Both T64 presses, back to what this install can do and what is running.

        Its own method for `_set_adopt_button()`'s reason: three places hand
        these buttons back — a job finishing, a chained backup finishing, and a
        chained backup failing — and a rule spelled three times is a rule that
        drifts. A backup in flight keeps them dead, because `_busy` cannot see
        one.
        """
        offered = self.services.update_to_latest is not None and not self._backup_before_update
        self.update_to_latest_action.setEnabled(offered)
        self.return_to_pin_action.setEnabled(offered)

    @Slot()
    def _set_server_build_button(self) -> None:
        """Grey "Server build ▾" exactly when no entry it shows can act (T89).

        `isEnabled()` alone is already "shown and enabled": a hidden `QAction`
        reports itself disabled whatever it was last set to, and gets that
        setting back when it is shown (measured, PySide6 6.11). So an entry
        hidden for want of a route or a pin cannot light a button whose menu
        holds nothing but a dead Rebuild.
        """
        self.server_build_button.setEnabled(
            any(a.isEnabled() for a in self.server_build_menu.actions())
        )

    def _refresh_source_version(self) -> None:
        """Redraw the version line and decide whether there is a pin to return to.

        Both from ONE reading, taken here: `LatestRoute.source_version()`
        answers the line and the button from a single read of the state file.
        Asking twice would be two readings of one file that can disagree -- a
        press finishing between them is all it would take -- and the
        disagreement's shape is a live "Return to the tested pin…" over a line
        that says the server IS on it.

        **The two are no longer the same question, and conflating them is what
        T77 is.** The line is drawn whenever there is something to say, which
        includes an install that has just RETURNED to its pins; the button is
        offered only while some source is still off its pin. Shown on the
        content's own terms rather than on the line's emptiness, this control
        disappears after a successful return and comes back after an update --
        instead of offering a ~36-minute compile that ends exactly where it
        started (live gate, 2026-09-16, press 6).

        Never raises. It is called from the reload path and from every job
        finishing, and an exception on either would take the tab down over a
        line of text; the seam's own contract is that it reads rather than
        raising, and this holds it to that. The fallback hides the button as
        well as the line: a read that failed knows nothing about where the
        sources stand, and offering an hour of compiling off that is worse than
        offering nothing.
        """
        route = self.services.update_to_latest
        if route is None:
            self.source_version_label.setVisible(False)
            self.return_to_pin_action.setVisible(False)
            return
        if self._waits_for_the_distro("source version", self._refresh_source_version):
            return
        try:
            said = route.source_version()
        except OSError as exc:
            logger.warning(f"could not read what {self.entry.id} was built from: {exc}")
            said = native.SourceVersion(line="", past_the_pin=False)
        self.source_version_label.setText(said.line)
        self.source_version_label.setVisible(bool(said.line))
        self.return_to_pin_action.setVisible(said.past_the_pin)

    def _refresh_upstream_news(self) -> None:
        """Ask, off the GUI thread, how far upstream is past this server's build (T124).

        Through `_run()` and never inline: a reading that is not in the day's
        cache reads each source's HEAD through a container and asks GitHub, and
        a tab that froze for that on opening would be the defect the job runner
        exists to prevent. One in flight at a time -- a job finishing and a
        Refresh landing together ask once.
        """
        route = self.services.update_to_latest
        if route is None or route.upstream_news is None:
            self.upstream_label.setVisible(False)
            return
        if self._upstream_pending:
            return
        if self._waits_for_the_distro("upstream news", self._refresh_upstream_news):
            return
        self._upstream_pending = True
        self._run(route.upstream_news, self._upstream_news_ready, self._upstream_news_failed)

    @Slot(object)
    def _upstream_news_ready(self, result: object) -> None:
        self._upstream_pending = False
        said = upstream.line(result) if isinstance(result, upstream.UpstreamNews) else ""
        self.upstream_label.setText(said)
        self.upstream_label.setVisible(bool(said))

    @Slot(object)
    def _upstream_news_failed(self, exc: object) -> None:
        """Silent on the tab, logged: the line is news, and a failure to ask is not."""
        self._upstream_pending = False
        logger.debug(f"could not ask how far upstream is past {self.entry.id}: {exc}")
        self.upstream_label.setText("")
        self.upstream_label.setVisible(False)

    def _update_route_busy(self) -> bool:
        """The two gates both T64 presses share, put to the user and answered True when hit.

        `rebuild_server()`'s pair, in its words: this press ENDS in that rebuild,
        so anything that refuses one has to refuse the other. Factored rather
        than copied because there are two presses here and a third copy of a
        guard is the copy that drifts.

        **THREE, since the cold review of 2026-09-16, and the third is the one
        neither `_busy` nor the panel can see.** The backup this control chains
        runs through `_run()` -- off the GUI thread, for minutes -- and nothing
        else on this tab knows it is happening: `_busy` is the LOG PANEL's flag,
        set by a job in that panel, and a `mysqldump` is not one. So a second
        press during it passed both gates, and "Update without a backup" then
        tore the database container down underneath the dump that was still
        running, after which the first press's own handler reported "Backup
        failed — the update was not started" about a backup the user was
        watching succeed.
        """
        if self._backup_before_update:
            QMessageBox.information(
                self,
                "A backup is running",
                "This server is being backed up before an update. Wait for the backup to "
                "finish — it will ask whether to go ahead. Nothing was started.",
            )
            return True
        if self.rebuild_log.running:
            QMessageBox.information(
                self,
                "Already running",
                "This server already has a job running on this tab. Wait for it to finish.",
            )
            return True
        if self._busy:
            QMessageBox.information(
                self,
                "Something else is running",
                "This server is busy with another action — wait for it to finish on the "
                "Server tab, then press this again. Nothing was started.",
            )
            return True
        return False

    @Slot()
    def update_to_latest(self) -> bool:
        """Ask, optionally back up, then move the sources and rebuild (T64). False if nothing ran.

        The owner's ask of 2026-09-15 -- "a update server to latest, with a
        warning and recommendation to take a backup before updating" -- as ONE
        question with three answers rather than a warning followed by a separate
        "back up first?". Two dialogs in a row is how a user learns to click
        through the first, and the first is the one carrying the warning.

        **The backup is chained, not awaited.** `services.backup` runs off the
        GUI thread through `_run()` -- it is a `mysqldump` of a live world and
        takes minutes -- so this method returns as soon as it is started and the
        update begins in `_backup_before_update_done()`. What that costs is that
        the return value means "a press started something", not "the update
        started"; what it buys is a window that is not frozen for the length of
        a dump.

        **The backup starts the database if it is down** (T76), through
        `_backup_with_the_database()` and not `services.backup` directly. An
        update is what somebody does to a server nobody is playing on, so the
        stack is normally stopped when this is pressed -- and the backup is a
        `docker exec` into the database container, which refused. The
        recommended button therefore failed on the first press of every stopped
        server until 2026-09-16.

        **A failed backup STOPS**, which is `dml wow update`'s rule
        (`BACKUP_FAILED "Safety backup failed -- update not started"`) and not
        wow-manage's, which offered a backup and continued regardless. Somebody
        who asked for a backup first asked for it because they want one before
        this runs, and "we could not take one, so we did the dangerous thing
        anyway" is the opposite of the answer they gave.

        **And it asks again afterwards.** `Backup saved to {path}. Update now?`,
        No by default. Minutes have passed, the answer is on screen, and the
        person is at a point where saying no costs nothing at all.

        Every answer is read through `said_yes()`/`ask_update_choice()`, never by
        identity: PySide6 6.11's static `question()` returns a plain `int` (T33),
        and `is` against the enum member was always False.
        """
        route = self.services.update_to_latest
        if route is None:
            return False
        if self._update_route_busy():
            # FALSE, like every other refusal on this tab and like
            # `_start_update_to_latest()` and `return_to_the_tested_pin()`: the
            # return value answers "was anything started?", and a press that was
            # refused started nothing. It answered True here until the cold
            # review of 2026-09-16, which made the one press with three exits
            # the one whose answer meant something different from the other two.
            return False
        # The module function, not a seam on this class: a test that wants to
        # answer this drives the real dialog through `QMessageBox.exec`, which
        # is what `conftest._no_modal_dialogs` already disarms. A seam here
        # would let a test answer a question whose buttons nothing checked --
        # and the buttons are half of what this control is.
        choice = ask_update_choice(
            self, f"Update {self.entry.name} to the newest code?", route.confirmation()
        )
        if choice is UpdateChoice.CANCEL:
            logger.info(f"update to latest of {self.entry.id} declined at the confirmation")
            return False
        if choice is UpdateChoice.WITHOUT_BACKUP:
            return self._start_update_to_latest()
        # SET BEFORE the worker starts, and cleared in BOTH handlers: it is the
        # only thing on this tab that knows a `mysqldump` is in flight, and
        # `_update_route_busy()` holds what a second press does without it.
        self._backup_before_update = True
        self.backup_button.setEnabled(False)
        self.update_to_latest_action.setEnabled(False)
        self.return_to_pin_action.setEnabled(False)
        self.maintenance_report.setPlainText(
            "Backing up before the update… this can take minutes on a full world."
        )
        self._run(
            self._backup_with_the_database,
            self._backup_before_update_done,
            self._backup_before_update_failed,
        )
        return True

    @Slot(object)
    def _backup_before_update_done(self, result: object) -> None:
        """The backup finished: report it, ask once more, and only then start the update.

        A result that is not a `BackupReport` is treated as a FAILED backup and
        stops, rather than being ignored the way `_backup_done()` ignores it.
        The two are looking at the same value with different stakes: there,
        "nothing to draw" costs a report line; here, carrying on would mean
        updating without the backup somebody asked for and without anybody
        having said the backup did not happen.
        """
        # FIRST, before anything that can put a modal on screen: the flag is a
        # lock, and a lock still held while its own handler blocks on a dialog
        # would refuse the very press that dialog is asking for.
        self._backup_before_update = False
        self.backup_button.setEnabled(True)
        self._set_update_buttons()
        if not isinstance(result, wotlk_maintenance.BackupReport):
            self._backup_before_update_failed("the backup did not say what it wrote")
            return
        self._backup_done(result)
        if not said_yes(
            QMessageBox.question(
                self,
                f"Update {self.entry.name} now?",
                f"Backup saved to {result.directory}. Update now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        ):
            logger.info(f"update to latest of {self.entry.id} declined after the backup")
            self.maintenance_report.setPlainText(
                f"Backup saved to {result.directory}. The update was not started."
            )
            return
        self._start_update_to_latest()

    @Slot(object)
    def _backup_before_update_failed(self, exc: object) -> None:
        """A backup that did not happen stops the update, and says so in `dml`'s own words."""
        message = f"Backup failed — the update was not started: {exc}"
        self._backup_before_update = False
        self.backup_button.setEnabled(True)
        self._set_update_buttons()
        self.maintenance_report.setPlainText(message)
        self.action_failed.emit(message)
        QMessageBox.warning(self, f"{self.entry.name}", message)
        self._show_interrupted()

    def _start_update_to_latest(self) -> bool:
        """Run the update in the shared panel. The one place either answer ends up.

        The gates are asked AGAIN here and not trusted from the press, for the
        reason `main.on_stopped_for_removal()` asks `forget_refusal()` again,
        one size larger: on the backup path minutes
        of `mysqldump` have gone by since they were last true, and this is the
        last point at which nothing has been fetched.
        """
        route = self.services.update_to_latest
        if route is None or self._update_route_busy():
            return False
        cancel = self._rebuild_cancel()
        self._rebuild_is_compile = True
        self._rebuild_moves_sources = True
        self._update_sources_kept = False
        return self.rebuild_log.run(
            lambda: self._watch_for_load_wait(self._noting_kept_sources(route.press(cancel))),
            title=f"Updating {self.entry.name} to the newest code",
            cancel=cancel,
            record_as=self._run_record_kind(),
        )

    @Slot()
    def return_to_the_tested_pin(self) -> bool:
        """Ask, then compile this server again from the commit the gates ran on. False if not run.

        No backup is offered here and that is deliberate rather than an
        omission. The thing a backup protects against is a NEW server writing
        into an old database, and that has already happened by the time anybody
        wants this button: the offer belongs to the press that caused it, which
        is where it is made. Offering one here would suggest this undoes those
        writes, which it does not -- `native.return_to_pin_confirmation()` says
        so in as many words.

        Yes/No with No as the default, read through `said_yes()`, exactly as
        `rebuild_server()` does: it is the same compile and the same hour.
        """
        route = self.services.update_to_latest
        if route is None:
            return False
        if self._update_route_busy():
            return False
        if not said_yes(
            QMessageBox.question(
                self,
                f"Put {self.entry.name} back on the tested commit?",
                route.pin_confirmation(),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        ):
            logger.info(f"return to the tested pin of {self.entry.id} declined")
            return False
        cancel = self._rebuild_cancel()
        self._rebuild_is_compile = True
        self._rebuild_moves_sources = True
        self._update_sources_kept = False
        return self.rebuild_log.run(
            lambda: self._watch_for_load_wait(self._noting_kept_sources(route.to_pin(cancel))),
            title=f"Returning {self.entry.name} to the tested commit",
            cancel=cancel,
            record_as=self._run_record_kind(),
        )

    def apply_database_updates(self) -> bool:
        """Ask, then apply this plan's re-runnable phases to the databases. False if not started.

        The button T11's reviewer said was owed. That ticket built the route —
        a phase declared `rerun_on_marked` is applied to an install the probe
        already reads as finished, before the world starts, with no marker
        written — and then found it had no way in from the app: the Catalog
        tab's tile greys to "Installed" once the folder is known, and
        `rebuild_stages()` excludes `import` on purpose, so for a GUI user with
        an established Tortoise install *no button applies those three files*
        was still true and the CLI harness was the only caller.

        **The confirmation is composed before it is shown, and composing it can
        refuse.** The file list is expanded from the folder rather than read off
        the catalog's glob, so a clone that predates the directory those phases
        name raises here — and that is the one refusal a user meets before any
        question. It goes to `action_failed` (the app log, which is the file a
        bug report is pasted from) and to a dialog, and nothing is started; a
        `QMessageBox.question` over an empty file list would be a confirmation
        for a press that applies nothing.

        Everything else follows `rebuild_server()` exactly, and deliberately:
        Yes/No with No as the default so Enter declines, `said_yes(...)` so
        Escape and the close button decline too, and the same panel — one long
        job on this tab at a time, because a rebuild and an update want the
        same containers.
        """
        route = self.services.updates
        if route is None:
            return False
        if self.rebuild_log.running:
            QMessageBox.information(
                self,
                "Already running",
                "This server already has a job running on this tab. Wait for it to finish.",
            )
            return False
        if self._busy:
            QMessageBox.information(
                self,
                "Something else is running",
                "This server is busy with another action — wait for it to finish on the "
                "Server tab, then press this again. Nothing was started.",
            )
            return False
        try:
            text = route.confirmation()
        except InstallerError as exc:
            logger.info(f"database updates for {self.entry.id} could not be described: {exc}")
            self.action_failed.emit(str(exc))
            QMessageBox.warning(self, f"{self.entry.name}", str(exc))
            return False
        if not said_yes(
            QMessageBox.question(
                self,
                f"Apply database updates to {self.entry.name}?",
                text,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        ):
            logger.info(f"database updates for {self.entry.id} declined at the confirmation")
            return False
        cancel = self._rebuild_cancel()
        self._rebuild_is_compile = False
        self._rebuild_moves_sources = False
        return self.rebuild_log.run(
            lambda: self._watch_for_load_wait(route.press(cancel)),
            title=f"Applying database updates to {self.entry.name}",
            cancel=cancel,
            record_as=self._run_record_kind(),
        )

    # ------------------------------------ T129: corrected install-plan steps

    @Slot(object)
    def _corrections_checked(self, result: object) -> None:
        if not isinstance(result, native.CorrectionCheck):
            return
        self._corrections = result
        if result.state not in ("current", "stale"):
            # Nothing to press, and nothing the player did: said in the log.
            logger.info(f"{self.entry.id}: no database corrections offered: {result.why}")
        self._refresh_corrections_banner()

    @Slot(object)
    def _corrections_check_failed(self, exc: object) -> None:
        """`check` never raises by contract; if it does, nothing is offered on it."""
        logger.warning(f"{self.entry.id}: the database corrections check failed: {exc}")
        self._forget_the_corrections_reading()

    def _forget_the_corrections_reading(self) -> None:
        """Drop the reading and the banner with it: `_forget_the_adopt_reading()`'s rule."""
        self._corrections = None
        self._refresh_corrections_banner()

    def _refresh_corrections_banner(self) -> None:
        check = self._corrections
        if check is None or check.state != "stale":
            self.corrections_banner.setVisible(False)
            return
        held = (
            f" ({', '.join(check.withheld)} also changed, and only a new install gets "
            f"{'it' if len(check.withheld) == 1 else 'them'}.)"
            if check.withheld
            else ""
        )
        self.corrections_banner_label.setText(
            f"This version of Yu'lon corrects {', '.join(check.offered)} in this server's install "
            f"plan, and these databases were imported before that. "
            f"{native.CORRECTIONS_BUTTON_LABEL} applies it — it asks first, names every step, "
            f"and stops the world server first if it is up. Nothing changes until you "
            f"press it.{held}"
        )
        self.corrections_banner.setVisible(True)

    def apply_database_corrections(self) -> bool:
        """Ask, then apply the corrected steps the banner names (T129). False if not started.

        The owner's rule for T106, which the lead chose for this: the player
        chooses when, and nothing changes behind their back. Everything else is
        `apply_database_updates()`'s: the dialog composed before it is shown
        (composing it can refuse), Yes/No with No the default and `said_yes`,
        and the same panel -- the lock that keeps a rebuild, an updates press and
        this one from running at once. The panel lives on the Modules tab, so
        the press brings it forward.
        """
        route = self.services.corrections
        check = self._corrections
        if route is None or check is None or check.state != "stale":
            return False
        if self.rebuild_log.running or self._busy:
            QMessageBox.information(
                self,
                "Something else is running",
                "This server is busy with another action — wait for it to finish, then press "
                "this again. Nothing was started.",
            )
            return False
        try:
            text = route.confirmation(check)
        except InstallerError as exc:
            logger.info(f"database corrections for {self.entry.id} could not be described: {exc}")
            self.action_failed.emit(str(exc))
            QMessageBox.warning(self, f"{self.entry.name}", str(exc))
            return False
        if not said_yes(
            QMessageBox.question(
                self,
                f"Apply database corrections to {self.entry.name}?",
                text,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        ):
            logger.info(f"database corrections for {self.entry.id} declined at the confirmation")
            return False
        # The panel's Cancel, with its "Stop now anyway" riding on it: the press
        # stops a running world first (T159), through T158's load wait.
        cancel = self._rebuild_cancel()
        self._rebuild_is_compile = False
        # The check itself, not its names: the press is bound to the reading the
        # dialog was composed from, and refuses if the databases moved since.
        started = self.rebuild_log.run(
            lambda: self._watch_for_load_wait(route.press(check, cancel)),
            title=f"Applying database corrections to {self.entry.name}",
            cancel=cancel,
            record_as=self._run_record_kind(),
        )
        if started:
            panel = self.rebuild_log.parentWidget()
            if panel is not None:
                self._tabs.setCurrentWidget(panel)
        return started

    def adopt_as_imported(self) -> bool:
        """Ask, then record these databases as a finished import. False if nothing started.

        The press the owner chose after three rounds of the alternative. T14's
        updates button refuses an install with no marker row, and the install it
        was built for is exactly that — a Tortoise server made by the shell
        scripts, complete in every way a person can see and unmarked. Three
        rounds tried to teach the probe to prove such an import finished; each
        found the next layer of inference underneath. This button stops
        inferring: the person says so, and the row is written.

        **The reading is not re-taken here**, and that is deliberate rather than
        a shortcut. `_adopt_state` decides whether this button was live at all,
        and the press asks the databases again itself — twice, in fact, before
        and after the write. A third reading here would be one more chance for
        the answer to change between the question and the act, and the press's
        own refusals are the sentences a user should meet when it has.

        Everything else follows `apply_database_updates()` exactly: the
        confirmation composed before it is shown, Yes/No with No as the default
        so Enter declines, `said_yes(...)` so Escape and the close button
        decline too, and the same panel — one long job on this tab at a time.
        """
        route = self.services.adopt
        if route is None:
            return False
        if self.rebuild_log.running:
            QMessageBox.information(
                self,
                "Already running",
                "This server already has a job running on this tab. Wait for it to finish.",
            )
            return False
        if self._busy:
            QMessageBox.information(
                self,
                "Something else is running",
                "This server is busy with another action — wait for it to finish on the "
                "Server tab, then press this again. Nothing was started.",
            )
            return False
        try:
            text = route.confirmation()
        except InstallerError as exc:
            logger.info(f"adopting {self.entry.id} could not be described: {exc}")
            self.action_failed.emit(str(exc))
            QMessageBox.warning(self, f"{self.entry.name}", str(exc))
            return False
        if not said_yes(
            QMessageBox.question(
                self,
                f"Adopt {self.entry.name}'s databases as a finished import?",
                text,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        ):
            logger.info(f"adopting {self.entry.id} declined at the confirmation")
            return False
        cancel = self._rebuild_cancel()
        self._rebuild_is_compile = False
        self._rebuild_moves_sources = False
        return self.rebuild_log.run(
            lambda: self._watch_for_load_wait(route.press(cancel)),
            title=f"Adopting {self.entry.name}'s databases as a finished import",
            cancel=cancel,
            record_as=self._run_record_kind(),
        )

    def _run_record_kind(self) -> str:
        """What this tab's rebuild-panel jobs are kept under on disk (T93).

        `rebuild-<game>-<install id>`, the install id being the path hash every
        per-install file of this app is keyed by (`composegen.install_id`), so
        two installs of one game keep separate histories of ten.
        """
        server_dir = self.services.controller.server_dir
        return f"rebuild-{self.entry.id}-{composegen.install_id(server_dir)}"

    def _forget_what_the_server_update_moved(self) -> None:
        """Drop the count and the version of every row a finished T64 press moved (T146).

        `_note_session_facts()`'s rule for a per-module update, applied to the
        server's: the checkout is on a different commit now, so "N commits
        behind" is a figure about one it has moved off and the version the row
        showed is a sha it is no longer on. Dropped rather than recounted, for
        the same reason -- a recount is a network round trip, and a stale
        number is a wrong one.

        Exactly the rows `moved_by_server_update()` names, which is the
        predicate the row builder offered their chip by: a module clone of its
        own (`mod-transmog`) is not moved by this press and keeps its count.
        """
        moved = self._server_updated()
        for widget in self.modules_panel.rows():
            row = widget.data
            if moved_by_server_update(row.family, row.id, moved):
                self._behind.pop((row.family, row.id), None)
                self._versions.forget(row.id)

    @Slot()
    def _rebuild_started(self) -> None:
        self._set_busy(True)

    def _rebuild_cancel(self) -> docker.CancelWithForce:
        """The Cancel a rebuild-panel job gets, with the panel's "Stop now anyway" riding on it."""
        cancel = docker.CancelWithForce()
        self._rebuild_force = cancel.anyway
        return cancel

    def _noting_kept_sources(self, lines: Iterator[str]) -> Iterator[str]:
        """Pass an update press's lines through, noting the typed "sources kept" outcome.

        T179 Task 6 fix round 3: a failure that KEPT the new build
        (`WorldStoppedAfterReadyError.sources_kept`) left the sources on their new
        commits, so `_rebuild_finished()` drops the counts the move made stale, as
        after a finished press. So did a rollback that stopped before the old build
        was back (`RollbackNotDone.sources_kept`, T197). Runs on the panel's worker;
        the flag is read on the GUI thread once the job has ended.
        """
        try:
            yield from lines
        except (WorldStoppedAfterReadyError, RollbackNotDone) as exc:
            self._update_sources_kept = exc.sources_kept
            raise

    def _watch_for_load_wait(self, lines: Iterator[str]) -> Iterator[str]:
        """Pass a rebuild-panel job's lines through, noticing a load wait (T158).

        Runs on the panel's worker thread, so what it notices goes through
        `_rebuild_wait_relay`. Only the wait's own sentences are relayed; a
        compile's thousands of lines are not.
        """
        for line in lines:
            if line in docker.LOAD_WAIT_LINES or line in (
                docker.WORLD_FINISHED_LOADING,
                docker.WORLD_STOPPED_ANYWAY,
                docker.WORLD_RESTARTED_STOPPING,
            ):
                self._rebuild_wait_relay.emit_line(line)
            yield line

    @Slot(str)
    def _rebuild_wait_line(self, text: str) -> None:
        """Offer the rebuild panel's "Stop now anyway" while a load wait is on (T158)."""
        waiting = text in docker.LOAD_WAIT_LINES
        self.rebuild_stop_anyway_button.setVisible(waiting)
        self.rebuild_stop_anyway_button.setEnabled(waiting)

    @Slot()
    def rebuild_stop_now_anyway(self) -> None:
        """Stop the waited-for world regardless, on the person's word (T158)."""
        self.rebuild_stop_anyway_button.setEnabled(False)
        if self._rebuild_force is not None:
            self._rebuild_force.set()

    @Slot(bool, str)
    def _rebuild_finished(self, ok: bool, message: str) -> None:
        """Unlock, and put a refusal where the user is looking.

        The panel's own header already carries `FAILED: <message>`, but a
        refusal from this action is a paragraph — "this folder has no install
        record", "that compose file was not written by Yu'lon" — and the header
        is one wrapped label beside a Stop button. `action_failed` is the route
        every other refusal on this tab takes, and it is also what `main.py`
        connects to the app log, which is the file a user pastes into a bug
        report.
        """
        self._set_busy(False)
        # T127/T162: an update press rebuilds the bot dashboard, and a rebuild
        # that failed leaves it stopped with a press on the Bots tab to retry.
        self.refresh_bot_dashboard()
        # T179 Task 6: an update, a re-extraction or a world retry may have left
        # (or paid) what the Server tab's two lines say, and the movement maps
        # may have been started or stopped.
        self.refresh_world_upkeep()
        self.refresh_pathfinding()
        # Whatever just ran on this tab -- a rebuild, an updates press, an adopt
        # press -- may have changed what the databases read as, and one of them
        # changes it on purpose. So the remembered reading is dropped and the
        # question is put again the next time the poll finds the database up,
        # which is how the adopt button greys itself the moment its own press
        # has written the row it exists to write.
        self._import_asked = False
        self._forget_the_adopt_reading()
        self._forget_the_corrections_reading()
        compiled, self._rebuild_is_compile = self._rebuild_is_compile, False
        moved, self._rebuild_moves_sources = self._rebuild_moves_sources, False
        if moved:
            # T170: an update press writes docker-compose.yml, and one that could
            # not write it back leaves the repository's own, which the message
            # it ends on sends the player to Repair server files… for. Asked
            # again here so the banner is there when the message is read -- and
            # asked AGAIN after a check already out, which read the folder
            # before the press ended (cold review, round 2).
            if self._compose_pending:
                self._compose_again = True
            else:
                self.check_server_files()
        kept, self._update_sources_kept = self._update_sources_kept, False
        if moved and ((ok and not self.rebuild_log.cancelled) or kept):
            # T146, on `_rebuild_owed`'s terms below: only a press that finished
            # and was not stopped. A failed one puts every source back on the
            # commit it was on (`StagedInstaller._put_sources_back()`), so its
            # counts are still the counts -- unless its new build was KEPT, which
            # keeps its sources too (`sources_kept`, T179 Task 6 fix round 3).
            self._forget_what_the_server_update_moved()
        # THREE questions, not one, and every one of them has bitten this clause.
        #
        # `ok` alone is not "the server was compiled": `LogPanel` reports a
        # STOPPED job as `ok=True, message="stopped"` on purpose (its worker's
        # `except` branch — a terminated child exits non-zero and reporting that
        # as a failure would put a refusal on screen for a button the user
        # pressed), so a Stop pressed half-way through a compile arrived here
        # indistinguishable from a finished one. `LogPanel.cancelled` is the
        # property that exists for exactly this and `catalog_view.
        # _on_run_finished()` already reads it; the message is deliberately NOT
        # read, because grepping the word "stopped" would be this same defect in
        # a new place (round 2, Codex).
        #
        # `compiled` is not implied either: three actions share this panel — the
        # rebuild, the database updates and the adopt — and the last two change
        # no binary at all.
        if ok and compiled and not self.rebuild_log.cancelled:
            # The compile that just finished covers every module installed
            # before it started, which is exactly what the set holds. A failed,
            # stopped or non-compile run leaves the owing intact, because
            # nothing about the running server changed.
            self._rebuild_owed.clear()
            self.reload_modules()
        if not ok:
            self.action_failed.emit(message)
        # T144. Taken on EVERY finish, so a move is offered once and never by a
        # later job; offered only after a press that succeeded and was not
        # stopped (`LogPanel` reports a stop as ok=True, hence `cancelled`).
        rebuild = self.services.bot_pool_rebuild
        move = rebuild.take_module_moved() if rebuild is not None else None
        if move is None:
            return
        if ok and not self.rebuild_log.cancelled:
            self._offer_bot_rebuild_after_update(move)
        elif move.restart_owed:
            # A Stop that landed after T123 owed its restart: T123's own cancel
            # rule is "do not restart, say so", and that is kept.
            self._say_restart_owed()

    def log_panels(self) -> tuple[LogPanel, ...]:
        """Every streaming panel this view owns, for the exit path to join.

        `main.py` registers these so `_stop_background_threads()` can stop and
        wait on each: a `QThread` destroyed while running ABORTS the process
        (0xC0000409, verified), so a panel the exit path cannot see is a crash
        on close. It is a method rather than a list `main.py` builds by hand
        because this view grew its second panel with the rebuild control, and
        the registration was in two files at the time — a third panel added
        later is picked up by code that already exists.
        """
        extra = tuple(log for log in (self.dashboard_log, self.bot_rebuild_log) if log is not None)
        return (self.console_log, self.rebuild_log, *extra)

    # -------------------------------------------------------- networking tab

    # ------------------------------------------------------------ tuning tab

    def _build_tuning_tab(self) -> None:
        """Every setting the modules on this install declare, and the file behind it.

        Its own tab beside Modules (T43 decision 1) rather than a section inside
        it: the Modules tab is about what this install HAS, and this one is
        about what those things are set to. They share two readings and nothing
        else -- `_load_manifests()` and `_installed_clones()`, so the two tabs
        cannot disagree about which modules are here.
        """
        tab = QWidget(self)
        box = QVBoxLayout(tab)
        self.tuning_panel = TuningPanel(tab)
        # T171: the time zone, above the cards and scrolling with them.
        self.tuning_panel.set_header(self.time_zone_group)
        self.tuning_panel.save_pressed.connect(self.save_tuning)
        self.tuning_panel.revert_pressed.connect(self.revert_tuning)
        self.tuning_panel.file_selected.connect(self.open_tuning_file)
        self.tuning_panel.file_save_pressed.connect(self.save_tuning_file)
        self.tuning_panel.file_reload_pressed.connect(self.reload_tuning_file)
        self.tuning_panel.file_revert_pressed.connect(self.revert_tuning_file)
        # The one control on this tab that is not a save: it re-reads the conf
        # files off disk. It exists because the values here are read ONCE per
        # reload and a server, an editor or another Yu'lon window can change a
        # conf underneath this tab at any time. Named for what it does since
        # T44 -- "Refresh" said nothing about where the values come from.
        self.tuning_reload_button = QPushButton(TUNING_RELOAD_LABEL, tab)
        self.tuning_reload_button.clicked.connect(self.reload_tuning)
        self.tuning_reload_button.setToolTip(
            "Read this install's conf files again. Cheap: the files themselves, no network. "
            "Anything you have typed here and not saved is kept."
        )
        # The undo for the FORM, and the one control on this bar that cannot
        # destroy anything: the cards are rebuilt from the rows already read,
        # so nothing is written and nothing is re-read. The per-card Revert is
        # the one that restores a file from its backup (T44 item 7).
        self.tuning_revert_all_button = QPushButton(TUNING_REVERT_ALL_LABEL, tab)
        self.tuning_revert_all_button.clicked.connect(self.revert_all_tuning_edits)
        self.tuning_revert_all_button.setToolTip(
            "Put every control on this tab back to what its file says. Writes nothing."
        )
        self.tuning_revert_all_button.setEnabled(False)
        # Connected HERE and not up with the panel's other signals: this slot
        # reads the button above, so the connect must not exist before the
        # button does. Nothing can emit `edited` in between today -- the row
        # controls are given their value before their signals are connected --
        # but an ordering that is only provably safe is an ordering the next
        # edit breaks silently.
        self.tuning_panel.edited.connect(self._set_tuning_revert_all)
        # The two that cost something. They are on THIS tab because this is
        # the tab that prices a change -- `tuning.apply_sentence()` has been
        # naming a restart and a recreate since T43 while the app had no
        # control for either. Both ask first: they take the server down.
        self.tuning_recreate_button = QPushButton(TUNING_RECREATE_LABEL, tab)
        self.tuning_recreate_button.clicked.connect(self.recreate_containers)
        self.tuning_recreate_button.setToolTip(TUNING_RECREATE_TIP)
        self.tuning_recreate_button.setEnabled(False)
        self.tuning_restart_button = QPushButton(TUNING_RESTART_LABEL, tab)
        self.tuning_restart_button.clicked.connect(self.restart_server)
        self.tuning_restart_button.setToolTip(TUNING_RESTART_TIP)
        self.tuning_restart_button.setEnabled(False)
        # T94. A menu and not two buttons: "all" and "one file" are one action
        # at two sizes, and the per-file entries are this game's own files from
        # the catalog. Undo sits in the same menu because the raw editor lists
        # the WotLK confs read-only, so its Revert cannot reach their backups.
        self._reset_files = reset_defaults.core_files(self.entry)
        self.tuning_reset_button = QPushButton(TUNING_RESET_LABEL, tab)
        self.tuning_reset_button.setToolTip(TUNING_RESET_TIP)
        self.tuning_reset_menu = QMenu(self.tuning_reset_button)
        self.tuning_reset_menu.addAction(TUNING_RESET_ALL).triggered.connect(
            self.reset_all_to_default
        )
        self.tuning_reset_menu.addSeparator()
        for file in self._reset_files:
            action = self.tuning_reset_menu.addAction(f"{reset_defaults.label(file)}…")
            # A GUI-thread signal into a GUI-thread call, so a lambda is safe
            # here; the JOB's callbacks below are bound slots (`_run()`).
            action.triggered.connect(lambda _checked=False, one=file: self.reset_to_default((one,)))
        self.tuning_reset_menu.addSeparator()
        self.tuning_reset_undo_action = self.tuning_reset_menu.addAction(TUNING_RESET_UNDO)
        self.tuning_reset_undo_action.triggered.connect(self.undo_last_reset)
        self.tuning_reset_undo_action.setEnabled(False)
        self.tuning_reset_button.setMenu(self.tuning_reset_menu)
        self.tuning_reset_button.setVisible(bool(self._reset_files))
        self.tuning_reset_button.setEnabled(self.services.reset_settings is not None)
        actions = QHBoxLayout()
        actions.addWidget(self.tuning_reload_button)
        actions.addWidget(self.tuning_revert_all_button)
        actions.addWidget(self.tuning_reset_button)
        actions.addStretch(1)
        actions.addWidget(self.tuning_recreate_button)
        actions.addWidget(self.tuning_restart_button)
        # The banner, hidden until something is waiting. Above the cards for
        # `rebuild_banner`'s reason: the ACTION is one action for every file
        # that owes it, and its button is the one that answers the DEAREST job
        # owed -- a user who changed two files must not be offered the cheaper
        # of the two (T44 item 8).
        self.tuning_banner = QWidget(tab)
        tuning_banner_box = QHBoxLayout(self.tuning_banner)
        tuning_banner_box.setContentsMargins(8, 6, 8, 6)
        self.tuning_banner_label = QLabel("", self.tuning_banner)
        self.tuning_banner_label.setWordWrap(True)
        self.tuning_banner_label.setStyleSheet(f"color: {COLOR_TEXT_WARNING};")
        self.tuning_banner_button = QPushButton("", self.tuning_banner)
        self.tuning_banner_button.clicked.connect(self._tuning_banner_pressed)
        tuning_banner_box.addWidget(self.tuning_banner_label, 1)
        tuning_banner_box.addWidget(self.tuning_banner_button)
        self.tuning_banner.setStyleSheet(
            f"background-color: {COLOR_BG_PARCHMENT}; border: 1px solid {COLOR_TEXT_WARNING};"
        )
        self.tuning_banner.setVisible(False)
        # The same shape as the Modules tab, so the same rule (T73): the cards
        # are what grows, the report is what the last press did and no taller.
        self.tuning_report = _ReportBox(tab)
        self.tuning_report.setReadOnly(True)
        # And the same strip (T80), because it is the same box with the same
        # problem: the cards are what the height is for.
        self.tuning_report_strip = _ReportStrip(self.tuning_report, tab)
        box.addLayout(actions)
        box.addWidget(self.tuning_banner)
        box.addWidget(self.tuning_panel, 1)
        box.addWidget(self.tuning_report_strip)
        box.addWidget(self.tuning_report)
        # No `MODULE_LIST_MIN_HEIGHT` here, deliberately. Measured 2026-09-16 the
        # panel asked for 288px of its own, so a floor of 100 could never apply.
        # Measured again at T190's fix round, when the file side got its scroll
        # area: 70 side by side and 125 narrow, and side by side it was given
        # 523 at 1280x800 -- a floor still never the number that applied, and a
        # guard that cannot fire is a guard nobody can test.
        # "modules", because `icons.py` is a file T43 must not edit and it has
        # no `tuning` key: the fallback is the SERVER icon, which would collide
        # with the Server tab. Sharing the Modules puzzle is the smaller
        # collision and the truer one -- this tab is the modules' settings.
        self._add_panel_tab(tab, "modules", "Tuning")
        self._tuning_rows: tuple[tuning.TuningRow, ...] = ()
        self._tuning_newline = "\n"
        # What this session has written that the running server has not picked
        # up, by the job it owes. Session state exactly like `_rebuild_owed`,
        # and forgotten on restart for the same reason: a persisted marker is
        # a file with its own invalidation rules (T42's "Not in scope").
        self._tuning_owed: dict[str, set[str]] = {}
        # T94: a reset or undo in flight (the close guard reads it through
        # `busy_reason()`), and what the last reset of this session wrote, for
        # its Undo. Empty, the Undo reads the last press off the disk instead.
        self._reset_running = False
        self._last_reset: tuple[reset_defaults.FileResult, ...] = ()
        # What the Undo would put back, as its last lookup answered, and which
        # lookup is the newest: an older answer landing late is dropped.
        self._undo_items: tuple[reset_defaults.FileResult, ...] = ()
        self._undo_generation = 0
        # The press waiting for its own facts job: its token, and what it asked
        # for. A newer press or a reload makes an older answer stale.
        self._press_token = 0
        self._press_asking: _PressAsking | None = None
        self.reload_tuning()
        self.tuning_panel.set_enabled_actions(self._module_actions_allowed())

    def reload_tuning(self) -> None:
        """Re-read every installed module's conf and redraw the cards.

        Reads files and nothing else -- no git, no docker, no database -- so it
        is cheap enough to run after every install and every save.
        """
        if self._waits_for_the_distro("tuning", self.reload_tuning):
            # A card's Save or Revert that led here still redraws as that card's (T190).
            self.tuning_panel.defer_pressed_card()
            return
        manifests, _broken = self._load_manifests()
        rows = tuning.rows_for(
            manifests,
            self._installed_clones() or {},
            self.services.controller.server_dir,
        )
        self._tuning_rows = rows
        self.tuning_panel.set_cards(build_tuning_cards(self._all_tuning_rows()))
        # WHICH files are read-only is this module's list and not the panel's:
        # `reset_defaults.read_only_confs()` is a decision about who owns core configuration,
        # and a second copy of it inside a widget is a second place for it to
        # drift (T44 item 13).
        self.tuning_panel.set_files(self._tuning_files(), read_only=self._tuning_core_files())
        self._set_tuning_revert_all()
        # T94: whether an undo has anything to put back, asked again of the
        # files -- off the GUI thread (final review): it lists up to five
        # folders and reads each core file and backup, over 9p for a server
        # inside WSL.
        self._look_up_reset_undo()
        # T99: the bot count and this tab's bot card, read again the same way:
        # a save of that card moves the Bots tab's box, and a reset moves both.
        self._look_up_bot_count()
        # T171: and the time zone, which a reset keeps but a hand edit moves.
        self._look_up_time_zone()

    def _all_tuning_rows(self) -> tuple[tuning.TuningRow, ...]:
        """The modules' rows, then the server's own bot keys (T99, CMaNGOS and Tortoise).

        Kept apart in `_bot_rows` rather than folded into `_tuning_rows`: the
        latter is what T94's reset reads as "keys an installed MODULE keeps",
        and these are the server's own keys, which a reset puts back.
        """
        return self._tuning_rows + self._bot_rows

    @Slot()
    def _set_tuning_revert_all(self) -> None:
        """Arm "Revert all changes" iff there is an unsaved edit to drop."""
        self.tuning_revert_all_button.setEnabled(
            self._module_actions_allowed() and self.tuning_panel.has_edits()
        )

    @Slot()
    def revert_all_tuning_edits(self) -> None:
        """Drop every unsaved edit on this tab, writing nothing (T44 item 7).

        The cards are rebuilt from `self._tuning_rows` -- the rows this tab
        has already read -- rather than by calling `reload_tuning()`: a reload
        also re-reads the files, which would pick up a change somebody ELSE
        made, and that is not what a person pressing "revert my changes"
        asked for.
        """
        self.tuning_panel.set_cards(build_tuning_cards(self._all_tuning_rows()), keep_edits=False)
        self._set_tuning_revert_all()
        self.tuning_report.setPlainText(TUNING_ALL_REVERTED)

    def _confirm(self, title: str, question: str, parent: QWidget | None = None) -> bool:
        """One Yes/No dialog, defaulting to No, read through `said_yes()`.

        `said_yes()` and never `== StandardButton.Yes` by hand: PySide6's
        static `question()` returns a plain int on some builds, which is T33's
        closed bug, and one helper is the one place that can be got right.
        `parent` is for a question the launcher window asked (T187); the tab otherwise.
        """
        return said_yes(
            QMessageBox.question(
                parent if parent is not None else self,
                title,
                question,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        )

    def _note_tuning_owed(self, file: str, rule: tuning.ApplyRule | None = None) -> None:
        """Record that `file` has been written and the server has not picked it up.

        The job is `tuning.file_rule()`'s, never a guess: a conf inside a
        directory the compose binds is read off the user's own disk at world
        start and a restart is enough; one outside every bind is a copy baked
        into the image, and only a recreate picks the new one up. `rule` is for
        a caller that knows better than `file_rule()`: T94's reset prices the
        compose override as a recreate, which `file_rule()` calls read-only
        (`reset_defaults.apply_rule`).
        """
        rule = rule or tuning.file_rule(file)
        if rule in TUNING_JOB_WORDS:
            self._tuning_owed.setdefault(rule, set()).add(file)
        self._refresh_tuning_owed()

    def _refresh_tuning_owed(self) -> None:
        """Arm the two expensive buttons and draw the banner for what is owed.

        The banner's button answers the DEAREST job owed, not the last one
        noted: a user who changed one file needing a restart and another
        needing the containers replaced must not be offered the cheaper of the
        two and told that is enough.
        """
        self._refresh_compose_banner()
        recreate = sorted(self._tuning_owed.get("recreate", ()))
        restart = sorted(self._tuning_owed.get("restart", ()))
        self.tuning_recreate_button.setEnabled(bool(recreate) and not self._busy)
        self.tuning_restart_button.setEnabled(bool(restart or recreate) and not self._busy)
        job = "recreate" if recreate else ("restart" if restart else None)
        # T99: the Bots tab's copy of the banner's button, same job, same slot.
        self.bot_count_owed_button.setVisible(job is not None)
        self.bot_count_owed_button.setEnabled(not self._busy)
        if job is not None:
            self.bot_count_owed_button.setText(
                TUNING_RECREATE_LABEL if job == "recreate" else TUNING_RESTART_LABEL
            )
        if job is None:
            self.tuning_banner.setVisible(False)
            return
        files = recreate if job == "recreate" else restart
        self.tuning_banner_label.setText(
            TUNING_BANNER.format(job=TUNING_JOB_WORDS[job], files=", ".join(files))
        )
        self.tuning_banner_button.setText(
            TUNING_RECREATE_LABEL if job == "recreate" else TUNING_RESTART_LABEL
        )
        self.tuning_banner.setVisible(True)

    @Slot()
    def _tuning_banner_pressed(self) -> None:
        """The banner's own press: the SAME slot the bar's button is bound to."""
        if self.tuning_banner_button.text() == TUNING_RECREATE_LABEL:
            self.recreate_containers()
        else:
            self.restart_server()

    # ------------------------------------------------- T106: repair server files

    @Slot()
    def check_server_files(self) -> None:
        """Ask, off the GUI thread, whether docker-compose.yml is what this version writes,
        (T137) whether a module conf the install writes is missing, and (T174)
        whether the server folder is locked to this Windows account.

        Each only where its route is wired (the CMaNGOS family; WotLK; Windows).
        None is queued behind itself: a check already out answers for this one too.
        """
        # T133 first: the checks read the install's folder, so on a stopped
        # distro none runs until it is up (T174's is never wired for a distro).
        if self._waits_for_the_distro("server files", self.check_server_files):
            return
        confs = self.services.repair_confs
        if confs is not None and not self._confs_pending:
            self._confs_pending = True
            self._run(confs.check, self._server_confs_checked, self._server_confs_check_failed)
        folder = self.services.lock_folder
        if folder is not None and not self._lock_pending:
            self._lock_pending = True
            self._run(folder.check, self._server_folder_checked, self._server_folder_check_failed)
        route = self.services.repair_compose
        if route is None or self._compose_pending:
            return
        self._compose_pending = True
        self._run(route.check, self._server_files_checked, self._server_files_check_failed)

    @Slot(object)
    def _server_folder_checked(self, result: object) -> None:
        self._lock_pending = False
        if isinstance(result, serverlock.FolderLockCheck):
            self._lock_state = result.state
            self._lock_check = result
            if result.state == "unknown":
                logger.info(f"{self.entry.id}: no folder lock offered: {result.why}")
        self._refresh_compose_banner()

    @Slot(object)
    def _server_folder_check_failed(self, exc: object) -> None:
        """`check` never raises by contract; if it does, the banner stays as it was."""
        self._lock_pending = False
        logger.warning(f"{self.entry.id}: the folder lock check failed: {exc}")

    @Slot(object)
    def _server_confs_checked(self, result: object) -> None:
        self._confs_pending = False
        if isinstance(result, native.ConfCheck):
            self._confs_missing = result.missing
        self._refresh_compose_banner()

    @Slot(object)
    def _server_confs_check_failed(self, exc: object) -> None:
        """`check` never raises by contract; if it does, the banner stays as it was."""
        self._confs_pending = False
        logger.warning(f"{self.entry.id}: the module conf check failed: {exc}")

    @Slot(object)
    def _server_files_checked(self, result: object) -> None:
        self._compose_pending = False
        if self._compose_again:
            # T170: this answer read the folder before an update press ended, so
            # it is dropped for the one asked now, which draws the banner.
            self._compose_again = False
            self.check_server_files()
            return
        if not isinstance(result, native.ComposeCheck):
            return
        self._compose_state = result.state
        self._compose_check = result
        if result.state != "current" and result.state not in REPAIR_FILES_OFFERED:
            # Not offered, and not a problem of anything the player pressed: said
            # in the log rather than over `problem_label`.
            logger.info(f"{self.entry.id}: no compose repair offered: {result.why}")
        self._refresh_compose_banner()

    @Slot(object)
    def _server_files_check_failed(self, exc: object) -> None:
        """`check` never raises by contract; if it does, the banner stays as it was."""
        self._compose_pending = False
        logger.warning(f"{self.entry.id}: the compose check failed: {exc}")
        if self._compose_again:
            self._compose_again = False
            self.check_server_files()

    def _refresh_compose_banner(self) -> None:
        """Draw T106's banner from what it answers: a job owed, a stale file, a missing conf.

        A job owed for a repaired file comes first: the file on disk is then
        current, and what is left to do is apply it -- a recreate for the compose
        file, a restart for a module conf (T137), which the world reads when it
        starts. The stale compose file and the missing conf are offered by the
        same button, and the compose file goes first. Since T170 one game can be
        offered both: WotLK names `confs_from_dist` and is offered the repair of
        the repository's own compose file (`upstream`) a failed update leaves.
        The folder lock (T174) is offered on Windows to every game, so it comes
        last: a press mends the first thing owed, and the check after it offers
        the next.
        """
        restart_owed = self._tuning_owed.get("restart", set())
        written = [file for file in self._confs_written if file in restart_owed]
        if REPAIR_FILES_OWED in self._tuning_owed.get("recreate", set()):
            backup = self._compose_backup.name if self._compose_backup else "a .repair.bak"
            done = (
                REPAIR_FILES_UPSTREAM_DONE if self._compose_backup_upstream else REPAIR_FILES_DONE
            )
            self.compose_banner_label.setText(done.format(backup=backup))
            self.compose_banner_button.setText(TUNING_RECREATE_LABEL)
            self.compose_banner.setVisible(True)
        elif written:
            self.compose_banner_label.setText(REPAIR_CONFS_DONE.format(**_conf_names(written)))
            self.compose_banner_button.setText(TUNING_RESTART_LABEL)
            self.compose_banner.setVisible(True)
        elif self._compose_state in REPAIR_FILES_OFFERED:
            self.compose_banner_label.setText(
                REPAIR_FILES_UPSTREAM_BANNER
                if self._compose_state == "upstream"
                else REPAIR_FILES_BANNER
            )
            self.compose_banner_button.setText(REPAIR_FILES_LABEL)
            self.compose_banner.setVisible(True)
        elif self._confs_missing:
            names = _conf_names(self._confs_missing)
            self.compose_banner_label.setText(REPAIR_CONFS_BANNER.format(**names))
            self.compose_banner_button.setText(REPAIR_FILES_LABEL)
            self.compose_banner.setVisible(True)
        elif (
            self._lock_state == "exposed"
            and self._lock_check is not None
            and self.services.lock_folder is not None
        ):
            self.compose_banner_label.setText(
                LOCK_FOLDER_BANNER.format(
                    readers=_and_list(self._lock_check.readers),
                    folder=self.services.lock_folder.folder,
                )
            )
            self.compose_banner_button.setText(REPAIR_FILES_LABEL)
            self.compose_banner.setVisible(True)
        else:
            self.compose_banner.setVisible(False)

    @Slot()
    def _compose_banner_pressed(self) -> None:
        """The banner's press: Repair, or -- once repaired -- the Tuning tab's Recreate/Restart."""
        if self.compose_banner_button.text() == TUNING_RECREATE_LABEL:
            self.recreate_containers()
        elif self.compose_banner_button.text() == TUNING_RESTART_LABEL:
            self.restart_server()
        else:
            self.repair_server_files()

    @Slot()
    def repair_server_files(self) -> None:
        """Ask, then re-render docker-compose.yml in a job (T106). Nothing happens on a No.

        The owner's decision: the player chooses when, and nothing changes behind
        their back. The dialog says what is written, what is kept and that the
        recreate comes next. With no stale compose file and a module conf
        missing, the same press is T137's instead (`_repair_confs()`); with
        neither, and a server folder somebody else can read, T174's lock
        (`_lock_server_folder()`). A compose file on offer -- `stale`, or since
        T170 `upstream` -- always goes first (cold review, round 2).
        """
        if self._busy:
            return
        if self._compose_state not in REPAIR_FILES_OFFERED and self._confs_missing:
            self._repair_confs()
            return
        if self._compose_state not in REPAIR_FILES_OFFERED and self._lock_state == "exposed":
            self._lock_server_folder()
            return
        route = self.services.repair_compose
        if route is None:
            return
        backup = f"{composegen.BASE_FILE}.<date>{native.REPAIR_BACKUP_SUFFIX}"
        last = self._compose_check
        counts = (
            f" (it adds {last.added} lines and removes {last.removed})"
            if last is not None and last.state in REPAIR_FILES_OFFERED
            else ""
        )
        # T169: the conf lines the same press sets, and any it leaves as the player has them.
        settings = last.settings if last is not None and last.state == "stale" else ()
        kept = last.kept if last is not None and last.state == "stale" else ()
        confs = REPAIR_FILES_CONFS.format(settings=" and ".join(settings)) if settings else ""
        confs += "".join(f"\n\n{why}" for why in kept)
        others = "any other .conf setting" if settings else "your .conf settings"
        # T170: the repository's own file has its own question; T169's conf lines
        # are the CMaNGOS family's, so `settings` and `kept` are empty there.
        confirm = (
            REPAIR_FILES_UPSTREAM_CONFIRM
            if last is not None and last.state == "upstream"
            else REPAIR_FILES_CONFIRM
        )
        question = confirm.format(backup=backup, counts=counts, confs=confs, others=others)
        if not self._confirm(REPAIR_FILES_LABEL, question):
            return
        self.problem_label.setText("")
        self._set_busy(True)
        self._run(route.repair, self._server_files_repaired, self._server_files_repair_failed)

    @Slot(object)
    def _server_files_repaired(self, result: object) -> None:
        if isinstance(result, native.ComposeRepaired):
            last = self._compose_check
            self._compose_state = "current"
            if result.backup is not None:
                self._compose_backup = result.backup
                self._compose_backup_upstream = last is not None and last.state == "upstream"
                self._note_tuning_owed_recreate(REPAIR_FILES_OWED)
            else:
                self.problem_label.setText(
                    "docker-compose.yml is already what this version of Yu'lon writes; nothing "
                    "was written."
                )
        self._set_busy(False)
        self.check_server_files()

    @Slot(object)
    def _server_files_repair_failed(self, exc: object) -> None:
        self.problem_label.setText(f"The server files were not repaired: {exc}")
        self._set_busy(False)

    def _repair_confs(self) -> None:
        """Ask, then write the missing module confs from their `.dist` in a job (T137).

        The same rule as the compose repair: the player chooses when, and the
        dialog says what is written, that nothing on disk is changed, and that a
        restart comes next.
        """
        route = self.services.repair_confs
        if route is None:
            return
        question = REPAIR_CONFS_CONFIRM.format(**_conf_names(self._confs_missing))
        if not self._confirm(REPAIR_FILES_LABEL, question):
            return
        self.problem_label.setText("")
        self._set_busy(True)
        self._run(route.repair, self._server_confs_repaired, self._server_confs_repair_failed)

    @Slot(object)
    def _server_confs_repaired(self, result: object) -> None:
        if isinstance(result, native.ConfRepaired):
            self._confs_missing = ()
            self._confs_written = result.written
            for file in result.written:
                # A conf in the bound etc folder: `file_rule()` prices it a
                # restart, which is what the banner then offers.
                self._note_tuning_owed(file)
            if not result.written:
                self.problem_label.setText(
                    "The files were already there; nothing was written, and nothing was changed."
                )
        self._set_busy(False)
        self.check_server_files()

    @Slot(object)
    def _server_confs_repair_failed(self, exc: object) -> None:
        self.problem_label.setText(f"The server files were not repaired: {exc}")
        self._set_busy(False)
        self.check_server_files()

    def _lock_server_folder(self) -> None:
        """Ask, then lock the server folder to this Windows account in a job (T174).

        The compose repair's rule: the player chooses when, and the question says
        what changes and that nothing needs restarting (measured: Docker Desktop
        reads and writes a locked folder, so the running containers keep working).
        """
        route = self.services.lock_folder
        last = self._lock_check
        if route is None or last is None:
            return
        if not self._confirm(REPAIR_FILES_LABEL, lock_folder_question(route.folder, last)):
            return
        self.problem_label.setText("")
        self._set_busy(True)
        self._run(route.lock, self._server_folder_locked, self._server_folder_lock_failed)

    @Slot(object)
    def _server_folder_locked(self, result: object) -> None:
        self._lock_state = None
        self._lock_check = None
        if self.services.lock_folder is not None:
            self.problem_label.setText(
                (LOCK_FOLDER_DONE if result is True else LOCK_FOLDER_ALREADY).format(
                    folder=self.services.lock_folder.folder
                )
            )
        self._set_busy(False)
        self.check_server_files()

    @Slot(object)
    def _server_folder_lock_failed(self, exc: object) -> None:
        self.problem_label.setText(LOCK_FOLDER_FAILED.format(reason=exc))
        self._set_busy(False)
        self.check_server_files()

    def _note_tuning_owed_recreate(self, name: str) -> None:
        """Record `name` as owed a recreate, in the Tuning tab's own set, so both banners agree."""
        self._tuning_owed.setdefault("recreate", set()).add(name)
        self._refresh_tuning_owed()

    @Slot()
    def restart_server(self) -> None:
        """Stop the world and start it again, so it re-reads the confs on disk.

        Asks first, and names what is waiting: everybody playing is
        disconnected. One job on the worker rather than two presses, because a
        stop the user then has to follow with a start is a server left down by
        a control that promised a restart.
        """
        if self._busy:
            return
        owed = sorted(self._tuning_owed.get("restart", ())) or ["(nothing recorded)"]
        if not self._confirm(
            TUNING_RESTART_LABEL, TUNING_RESTART_CONFIRM.format(files="\n".join(owed))
        ):
            return
        self._set_busy(True)
        self._hold_badge("restarting")
        self.tuning_report.setPlainText("restarting the server…")
        self._run(
            lambda: ("restart", self._do_restart()), self._tuning_job_done, self._tuning_job_failed
        )

    @Slot()
    def recreate_containers(self) -> None:
        """Delete this install's containers and start them again from the current config.

        `controller.remove()` then `controller.start()`: `remove` deletes the
        containers and KEEPS the volumes, and the next start creates them
        again -- which is exactly the Server tab's own sentence for the same
        pair of calls.
        """
        if self._busy:
            return
        owed = sorted(self._tuning_owed.get("recreate", ())) or ["(nothing recorded)"]
        if not self._confirm(
            TUNING_RECREATE_LABEL, TUNING_RECREATE_CONFIRM.format(files="\n".join(owed))
        ):
            return
        self._set_busy(True)
        self._hold_badge("restarting")
        self.tuning_report.setPlainText("recreating the containers…")
        self._run(
            lambda: ("recreate", self._do_recreate()),
            self._tuning_job_done,
            self._tuning_job_failed,
        )

    def _do_restart(self) -> bool:
        """Stop, then start. ONE worker job: a stop the user then has to follow with a
        start by hand is a server left down by a control that promised a restart.

        And one lifecycle command (`docker.lifecycle()`, T216 review round 3), so a
        restore cannot take its hold between the two and leave the server stopped."""
        controller = self.services.controller
        controller.refuse_start()  # T179: before the stop, so a refusal leaves it running
        with docker.lifecycle(controller.server_dir):
            stopped = controller.stop()
            controller.start()
        return stopped

    def _do_recreate(self) -> bool:
        """Delete the containers, then start. `remove()` keeps the volumes, so the
        characters are not touched and the next start creates the containers again --
        the Server tab's own sentence for the same pair of calls."""
        controller = self.services.controller
        controller.refuse_start()  # T179: before the removal, so a refusal leaves it as it was
        # One lifecycle command, as `_do_restart()` is: a gap here leaves the server removed.
        with docker.lifecycle(controller.server_dir):
            removed = controller.remove()
            controller.start()
        return removed

    @Slot(object)
    def _tuning_job_done(self, answer: object) -> None:
        """The handler for a finished restart or recreate: forget what it covered.

        A recreate covers a restart as well -- the containers are new, so they
        have both the current environment and the current conf files -- which
        is why it clears both and a restart clears only its own.

        A bound slot that is told which job it was, not a closure made per job
        (T97): the closure was a plain callable, so the runner delivered it on
        the worker thread, and this wrote the report and started the status
        read from there.
        """
        job = cast(tuple[str, object], answer)[0]
        self._set_busy(False)
        self._tuning_owed.pop("restart", None)
        if job == "recreate":
            self._tuning_owed.pop("recreate", None)
        self._refresh_tuning_owed()
        zone = self._say_zone_problem()
        self.tuning_report.setPlainText(f"{job}: done." + (f"\n{zone}" if zone else ""))
        self.refresh_status()

    @Slot(object)
    def _tuning_job_failed(self, exc: object) -> None:
        self._set_busy(False)
        self._refresh_tuning_owed()
        self.tuning_report.setPlainText(f"FAILED: {exc}")
        self.action_failed.emit(str(exc))

    # -- T94: Reset to default

    @Slot()
    def reset_all_to_default(self) -> None:
        self.reset_to_default(self._reset_files)

    def reset_to_default(self, files: Sequence[str]) -> None:
        """Ask once, then put `files` back to how Yu'lon installed this server, off the GUI thread.

        Allowed while the server runs (owner, 2026-09-23): the files change on
        disk and the banner offers the restart or recreate that makes them
        count. Refused while another action of ours runs, which may be reading
        these files.
        """
        route = self.services.reset_settings
        if route is None or self._busy or not files:
            return
        chosen = tuple(files)
        # Owner decision 5: the keys installed modules keep in these files,
        # from this tab's OWN rows (`tuning.rows_for`), whose `file` is the
        # manifest's spelling -- `env/dist/etc/...` on WotLK, `etc/...` on
        # CMaNGOS -- which is `core_files()`'s. Read here, on the GUI thread,
        # because they are already in memory; the worker gets a copy.
        keys = reset_defaults.module_keys(self._tuning_rows, chosen)
        modules = sorted(
            {
                row.module_name
                for row in self._tuning_rows
                if row.key in keys.get(row.file, ())
                # A key the install table writes is not kept (`install_keys`),
                # so a module holding only those is not named as kept either.
                and row.key.casefold() not in reset_defaults.install_keys(self.entry, row.file)
            }
        )
        # The question names what the press will do to each file -- left
        # alone, made again, left absent -- so it is built from THIS press's
        # own facts, read fresh by a job (Codex final pass: a reload's answer
        # can be hours old; re-review: never a stat or a read on the GUI
        # thread). The button stays dead until they land.
        self._press_token += 1
        self._press_asking = _PressAsking(
            self._press_token, self._undo_generation, chosen, keys, modules
        )
        self._set_reset_button()
        self._run(
            partial(
                _read_press_facts,
                self.entry,
                self.services.controller.server_dir,
                chosen,
                self._press_token,
                self._undo_generation,
            ),
            self._press_facts_ready,
            self._press_facts_failed,
        )

    @Slot(object)
    def _press_facts_ready(self, answer: object) -> None:
        """This press's facts: ask the question from them, and hold the reset to them.

        Dropped when it is not the waiting press's answer -- a newer press, or
        a reload since (the tab moved on; the player presses again).
        """
        asking = self._press_asking
        if (
            not isinstance(answer, PressAnswer)
            or asking is None
            or answer.token != asking.token
            or answer.generation != asking.generation
            or answer.generation != self._undo_generation
        ):
            return
        self._press_asking = None
        self._set_reset_button()
        if answer.error:
            self.tuning_report.setPlainText(TUNING_RESET_UNREADABLE.format(why=answer.error))
            self.action_failed.emit(answer.error)
            return
        chosen, keys, modules = asking.files, asking.keys, asking.modules
        route = self.services.reset_settings
        if route is None or self._busy:
            return
        facts = answer.facts
        if not self._confirm(
            TUNING_RESET_LABEL, reset_defaults.question(chosen, modules, facts=facts)
        ):
            return
        self._reset_running = True
        self._set_busy(True)
        self.tuning_report.setPlainText("putting the settings back to how Yu'lon installed them…")
        # The facts the player said Yes to go with the press: `reset()`
        # refuses, writing nothing, if any file's case changed since.
        self._run(partial(route, chosen, keys, facts), self._reset_done, self._reset_failed)

    @Slot(object)
    def _press_facts_failed(self, exc: object) -> None:
        """Only a bug reaches here: `_read_press_facts` returns every failure in its answer.

        It cannot say which press it belongs to, so it never touches the waiting
        press (Codex, last pass: a stale press's failure cleared the current one
        and re-armed the button mid-read); a reload frees the button.
        """
        logger.warning(f"a Reset to default press's facts job raised: {exc}")
        self.tuning_report.setPlainText(TUNING_RESET_UNREADABLE.format(why=exc))
        self.action_failed.emit(str(exc))

    def _set_reset_button(self) -> None:
        """Pressable when this tab has a route, nothing of ours runs, and no press is asking."""
        self.tuning_reset_button.setEnabled(
            self.services.reset_settings is not None
            and not self._busy
            and self._press_asking is None
        )

    @Slot(object)
    def _reset_done(self, result: object) -> None:
        self._reset_running = False
        self._set_busy(False)
        if not isinstance(result, reset_defaults.ResetReport):
            return
        lines = result.lines()
        self.tuning_report.setPlainText("\n".join(lines))
        if result.written:
            self._last_reset = result.written
        # A recreated file owes the restart too, though there is nothing to undo.
        for item in result.changed:
            self._note_tuning_owed(item.file, reset_defaults.apply_rule(item.file))
        if result.refused:
            self.action_failed.emit(" ".join(lines[:2]))
        self._after_reset_files(result)

    @Slot(object)
    def _reset_failed(self, exc: object) -> None:
        """Only a bug reaches here: `reset()` and `undo()` report every failure they expect."""
        self._reset_running = False
        self._put_back_running = False
        self._set_busy(False)
        self.tuning_report.setPlainText(f"FAILED: {exc}")
        self.action_failed.emit(str(exc))
        # A bug may have struck after some writes, so this session's record of
        # an EARLIER press is no longer the last one: dropped, so the Undo
        # reads the newest press off the disk -- the one that just broke -- and
        # the cards and the Undo's state are read again from the files.
        self._last_reset = ()
        self.reload_tuning()

    def _reset_undo_items(self) -> tuple[reset_defaults.FileResult, ...]:
        """What "Undo the last reset…" would put back, as the last lookup answered.

        `reset_defaults.undo_items()` decides (this session's record, else the
        disk's); `_look_up_reset_undo()` asks it on the job runner.
        """
        return self._undo_items

    def _look_up_reset_undo(self) -> None:
        """Ask, off the GUI thread, what the Undo would put back; greyed until the answer lands."""
        if self._waits_for_the_distro("reset undo", self._look_up_reset_undo):
            return
        self._undo_generation += 1
        self._undo_items = ()
        self.tuning_reset_undo_action.setEnabled(False)
        # The tab moved on: a press still waiting for its facts is dropped (its
        # answer is stale by generation), and the button is pressable again.
        if self._press_asking is not None:
            self._press_asking = None
            self._set_reset_button()
        self._run(
            partial(
                _look_up_undo,
                self.entry,
                self.services.controller.server_dir,
                self._last_reset,
                self._undo_generation,
            ),
            self._undo_looked_up,
            self._undo_lookup_failed,
        )

    @Slot(object)
    def _undo_looked_up(self, result: object) -> None:
        """The lookup's answer, unless a newer reload has asked since (then it is stale)."""
        if not isinstance(result, UndoLookup) or result.generation != self._undo_generation:
            return
        self._undo_items = result.items
        self.tuning_reset_undo_action.setEnabled(bool(result.items))

    @Slot(object)
    def _undo_lookup_failed(self, exc: object) -> None:
        """An unreadable folder leaves the Undo greyed; the next reload asks again."""
        logger.warning(f"could not work out what a reset's undo would put back: {exc}")

    @Slot()
    def undo_last_reset(self) -> None:
        """Copy back what the last reset replaced, after one Yes/No, off the GUI thread."""
        if self._busy:
            return
        if self._put_back_refused(TUNING_RESET_UNDO):
            return
        items = self._reset_undo_items()
        if not items:
            self.tuning_reset_undo_action.setEnabled(False)
            return
        names = "\n".join(
            f"    {reset_defaults.label(item.file)}  <-  {item.backup.name}"
            for item in items
            if item.backup is not None
        )
        if not self._confirm(TUNING_RESET_UNDO, TUNING_RESET_UNDO_CONFIRM.format(files=names)):
            return
        self._reset_running = True
        self._put_back_running = True
        self._set_busy(True)
        self.tuning_report.setPlainText("putting back what the last reset replaced…")
        self._run(
            partial(
                _undo_still_undoable,
                self.services.controller.server_dir,
                items,
                self.services.bot_pool_rebuild,
            ),
            self._undo_done,
            self._reset_failed,
        )

    @Slot(object)
    def _undo_done(self, result: object) -> None:
        self._reset_running = False
        self._put_back_running = False
        self._set_busy(False)
        if not isinstance(result, reset_defaults.ResetReport):
            return
        lines = result.lines() if result.results else (TUNING_RESET_NOTHING_TO_UNDO,)
        self.tuning_report.setPlainText("\n".join(lines))
        for item in result.results:
            if item.outcome == "restored":
                self._note_tuning_owed(item.file, reset_defaults.apply_rule(item.file))
        # A file the undo could not put back keeps its backup, so a second
        # press tries it again; the rest are done.
        self._last_reset = tuple(item for item in result.results if item.outcome == "refused")
        if result.refused:
            self.action_failed.emit(" ".join(lines[:2]))
        self._after_reset_files(result)

    def _after_reset_files(self, result: reset_defaults.ResetReport) -> None:
        """Re-read the tab (and the Undo's state) and the open file, if the press changed it."""
        self.reload_tuning()
        current = self.tuning_panel.current_file()
        if current and any(
            item.file == current and item.outcome in ("reset", "restored")
            for item in result.results
        ):
            self.open_tuning_file(current)

    def _tuning_files(self) -> tuple[str, ...]:
        """What the raw editor offers: this install's module confs, then its own.

        Only files that are ON DISK. A conf a manifest names but nothing has
        deployed would open as an empty editor, and saving that empty editor
        would create the file -- which is an install step, not a tuning one.
        """
        server_dir = self.services.controller.server_dir
        found: list[str] = []
        for row in self._tuning_rows:
            if row.editable and row.file not in found and (server_dir / row.file).is_file():
                found.append(row.file)
        for name in self._tuning_core_files():
            if name not in found and (server_dir / name).is_file():
                found.append(name)
        return tuple(found)

    def _tuning_core_files(self) -> tuple[str, ...]:
        """The server's own confs this tab lists read-only: `TUNING_CORE_FILES` on WotLK,
        a TrinityCore server's own (T179), `reset_defaults.read_only_confs()`."""
        return reset_defaults.read_only_confs(self.entry)

    def _tuning_spec(self, family: str, module_id: str, file: str) -> dict[str, ConfKey]:
        """This module's declared keys for one file, so a value can be type-checked.

        From the MANIFEST and not from the row, because the row carries the
        declaration flattened for drawing and `tuning.check()` wants the
        declaration itself -- one object, so a field added to `ConfKey` reaches
        the refusal without a third place to copy it into.

        By FAMILY as well as id (T42 round 2's key shape), and the family is
        handed in rather than resolved: every caller has the `TuningCard` the
        rows came from, and `TuningRow.family` is where `tuning.rows_for()` put
        the manifest's own `type`. Resolving a bare id here would be a second
        rule for an ambiguity `_key_for()` already settles once, in the one
        place that has to guess.
        """
        if (family, module_id) == botpop.CARD and file == botpop.card_file(self.entry):
            return botpop.conf_keys(self.entry)
        manifest = self._manifests.get((family, module_id))
        if manifest is None:
            return {}
        return {key.key: key for conf in manifest.conf if conf.file == file for key in conf.keys}

    @Slot(str, str)
    def save_tuning(self, family: str, module_id: str) -> None:
        """Write this card's changed keys, grouped by the file each one lives in.

        Per card and not per file (T43's definition of done), because a card is
        what the user installed: NPC Beastmaster declares four keys in its own
        conf and one in the core's `worldserver.conf`, and asking somebody to
        press Save twice for one module would be the tab's file layout leaking
        into their hands.
        """
        try:
            card = self.tuning_panel.card((family, module_id))
        except KeyError:
            return
        edits = card.edits()
        if not edits:
            self.tuning_report.setPlainText(TUNING_NOTHING_CHANGED.format(module=module_id))
            return
        per_file: dict[str, dict[str, str]] = {}
        for row in card.card.rows:
            if row.key in edits:
                per_file.setdefault(row.file, {})[row.key] = edits[row.key]
        said: list[str] = []
        server_dir = self.services.controller.server_dir
        # Every file's values FIRST, across the whole card, before any of them is
        # opened. `tuning.write()` makes the same promise per file, which is not
        # the same promise: a card spanning two files (NPC Beastmaster has its
        # own conf and one key in the core's `worldserver.conf`) landed the
        # first file's change and only then refused the second, which is exactly
        # the half-applied state the guarantee exists to prevent.
        specs = {file: self._tuning_spec(family, module_id, file) for file in per_file}
        for file, values in per_file.items():
            for key, value in values.items():
                try:
                    tuning.check(specs[file].get(key), value)
                except tuning.TuningError as exc:
                    self.tuning_report.setPlainText(
                        TUNING_REFUSED.format(module=module_id, why=exc)
                    )
                    self.action_failed.emit(str(exc))
                    return
        for file, values in per_file.items():
            try:
                made = tuning.write(server_dir / file, values, spec=specs[file])
            except tuning.TuningError as exc:
                # Unreachable through the loop above, which has already checked
                # every value on the card. Kept because `tuning.write()` is a
                # public seam with its own refusals and a caller that assumed
                # otherwise would be the next half-applied save.
                self.tuning_report.setPlainText(TUNING_REFUSED.format(module=module_id, why=exc))
                self.action_failed.emit(str(exc))
                return
            except OSError as exc:
                self.tuning_report.setPlainText(
                    TUNING_REFUSED.format(
                        module=module_id, why=f"{file} could not be written: {exc}"
                    )
                )
                self.action_failed.emit(str(exc))
                return
            self._note_tuning_owed(file)
            said.append(
                TUNING_SAVED.format(
                    module=module_id,
                    keys=", ".join(values),
                    file=file,
                    backup=made.name,
                    rule=tuning.apply_sentence(tuning.file_rule(file)),
                )
            )
        self.tuning_report.setPlainText("\n".join(said))
        self.reload_tuning()

    @Slot(str, str)
    def revert_tuning(self, family: str, module_id: str) -> None:
        """Put this card's files back from the newest backup Yu'lon took of each."""
        if self._put_back_refused("Revert"):
            return
        try:
            card = self.tuning_panel.card((family, module_id))
        except KeyError:
            return
        server_dir = self.services.controller.server_dir
        said: list[str] = []
        for file in card.card.files:
            path = server_dir / file
            backups = tuning.backups_of(path)
            if not backups:
                said.append(TUNING_NO_BACKUP.format(module=module_id, file=file))
                continue
            try:
                note = self._put_back(backups[-1], path)
            except OSError as exc:
                said.append(TUNING_REFUSED.format(module=module_id, why=f"{file}: {exc}"))
                continue
            self._note_tuning_owed(file)
            said.append(
                TUNING_REVERTED.format(
                    module=module_id,
                    file=file,
                    backup=backups[-1].name,
                    rule=tuning.apply_sentence(tuning.file_rule(file)),
                )
            )
            if note:
                said.append(note)
        self.tuning_report.setPlainText("\n".join(said))
        self.reload_tuning()

    def _put_back_refused(self, press: str) -> bool:
        """Refuse a put-back of a Tuning backup now, saying why. True if refused (T145 round 6).

        `_busy` is checked HERE, not only through the greyed buttons that
        `_set_busy` leaves: a slot is also reachable without its button. And a
        Maintenance restore, which does not hold `_busy`, holds these three on
        Tortoise: it sets a pending random-bot rebuild request in
        aiplayerbot.conf off on its worker (`_restore_with_the_bot_request`),
        the very file a put-back writes. Other games' restores write no conf.
        """
        if self._busy:
            self.tuning_report.setPlainText(TUNING_PUT_BACK_WHILE_BUSY.format(press=press))
            return True
        if self._restore_running and self.services.bot_pool_rebuild is not None:
            self.tuning_report.setPlainText(TUNING_PUT_BACK_DURING_RESTORE.format(press=press))
            return True
        return False

    def _put_back(self, backup: Path, target: Path) -> str | None:
        """`tuning.restore()`, through Tortoise's rebuild-request rule where there is one (T145).

        A backup is the file as it was, so one taken while a random-bot rebuild
        request was pending holds it, and after a Maintenance restore set it
        off a Revert put it back whole: the next start deleted the restored
        bots. `put_back_file` keeps the file's own key lines whenever the backup
        asks for a rebuild anywhere; the sentence it returns goes in the report.
        """
        seam = self.services.bot_pool_rebuild
        if seam is None:
            tuning.restore(backup, target)
            return None
        # Held for the put-back's length (round 7); the two Reverts run here on
        # the GUI thread, so a Restore press cannot land inside them today, but
        # the flag says what is running rather than how it happens to be run.
        was = self._put_back_running
        self._put_back_running = True
        try:
            return seam.put_back_file(backup, target, tuning.restore)
        finally:
            self._put_back_running = was

    @Slot(str)
    def open_tuning_file(self, file: str) -> None:
        """Show one conf in the raw editor, read-only when it is the server's own."""
        path = self.services.controller.server_dir / file
        core = file in self._tuning_core_files()
        try:
            with open(path, encoding="utf-8", newline="") as handle:
                raw = handle.read()
        except OSError as exc:
            self.tuning_panel.set_file_text("", read_only=True, note=f"{file}: {exc}")
            return
        except UnicodeDecodeError as exc:
            # Shown empty and READ-ONLY rather than with replacement characters
            # in it: an editor holding U+FFFD where a byte used to be is one
            # Save away from writing that corruption to disk, and the Save
            # button is the one control that must not be live here.
            self.tuning_panel.set_file_text(
                "", read_only=True, note=tuning.NOT_UTF8.format(file=file, why=exc)
            )
            return
        # Remembered at load and re-applied at save: `QPlainTextEdit` hands back
        # "\n" whatever it was given, so a raw save of a CRLF conf would convert
        # the whole file -- the same defect `tuning.write()` reads around.
        self._tuning_newline = "\r\n" if "\r\n" in raw else "\n"
        note = TUNING_CORE_FILE if core else tuning.apply_sentence(tuning.file_rule(file))
        self.tuning_panel.set_file_text(
            raw.replace("\r\n", "\n"),
            read_only=core,
            note=note,
            # Which of THIS file's keys the running containers override (T44
            # item 16, round 2). Asked of `composegen`, which is the module
            # that writes those rows, so the tab warns about the environment
            # this install really has rather than about every editable conf --
            # the channel's rows included while its press is live
            # (`channel_world_env`, T137 review), or a shipped
            # `AiPlayerbot.CommandServerPort` went unflagged under its `=0` row.
            shadowed=composegen.shadowed_by_env(
                raw,
                composegen.channel_world_env(self.entry, self.services.controller.server_dir)
                or composegen.world_env(self.entry),
            ),
        )

    @Slot()
    def reload_tuning_file(self) -> None:
        self.open_tuning_file(self.tuning_panel.current_file())

    @Slot()
    def revert_tuning_file(self) -> None:
        """Put the open file back FROM ITS BACKUP, and say which one (T44 item 15).

        From the backup and never by re-reading the form: the editor holds
        what was last saved, so a "revert" that re-read it would put back the
        very change the user is trying to undo. `tuning.restore()` copies
        rather than moves, so a second Revert still has something to restore.
        """
        if self._put_back_refused("Revert"):
            return
        file = self.tuning_panel.current_file()
        if not file or file in self._tuning_core_files():
            return
        path = self.services.controller.server_dir / file
        # The backup the tab names, not merely the newest (T190): a card's Save
        # may have taken a newer one since, and the label still names this one.
        named = self.tuning_panel.backup_name()
        backups = tuple(b for b in tuning.backups_of(path) if named is None or b.name == named)
        if not backups:
            gone = TUNING_NAMED_BACKUP_GONE.format(backup=named, file=file) if named else ""
            self.tuning_report.setPlainText(gone or TUNING_NO_FILE_BACKUP.format(file=file))
            return
        try:
            note = self._put_back(backups[-1], path)
        except OSError as exc:
            self.tuning_report.setPlainText(TUNING_FILE_FAILED.format(file=file, exc=exc))
            self.action_failed.emit(str(exc))
            return
        said = TUNING_REVERTED_FILE.format(
            file=file,
            backup=backups[-1].name,
            rule=tuning.apply_sentence(tuning.file_rule(file)),
        )
        self.tuning_report.setPlainText(f"{said}\n{note}" if note else said)
        self._note_tuning_owed(file)
        self.open_tuning_file(file)
        self.reload_tuning()

    @Slot(str)
    def save_tuning_file(self, text: str) -> None:
        """Write the raw editor's text back, after one confirm if it stopped looking like a conf.

        The guard warns and never blocks (T43 point 4): it is a cheap pass, not
        a parser, and a refusal between a person and their own configuration
        over a rule this shallow would be worse than the typo it caught.
        """
        file = self.tuning_panel.current_file()
        if not file or file in self._tuning_core_files():
            return
        said = tuning.lint_sentence(tuning.lint(text))
        if said is not None:
            answer = QMessageBox.question(
                self,
                TUNING_LINT_CONFIRM_TITLE,
                said,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            # `==` and an int, not `is`: PySide6's static `question()` returns a
            # plain int, so `is StandardButton.Yes` is always False (T33).
            if answer != QMessageBox.StandardButton.Yes:
                return
        path = self.services.controller.server_dir / file
        try:
            made = tuning.backup(path)
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(text.replace("\n", self._tuning_newline))
        except OSError as exc:
            self.tuning_report.setPlainText(TUNING_FILE_FAILED.format(file=file, exc=exc))
            self.action_failed.emit(str(exc))
            return
        self.tuning_report.setPlainText(
            TUNING_FILE_SAVED.format(
                file=file,
                backup=made.name,
                rule=tuning.apply_sentence(tuning.file_rule(file)),
            )
        )
        # The backup's name on the tab and not only in the report, because it
        # is what arms Revert beside Save file (T44 item 15).
        self.tuning_panel.set_backup(made.name)
        self._note_tuning_owed(file)
        self.reload_tuning()

    def _build_networking_tab(self) -> None:
        tab = QWidget(self)
        box = QVBoxLayout(tab)
        self.lan_radio = QRadioButton("LAN (same Wi-Fi)", tab)
        self.internet_radio = QRadioButton("Internet play (friends elsewhere)", tab)
        self.loopback_radio = QRadioButton(LOOPBACK_CHOICE, tab)
        self.lan_radio.setChecked(True)
        group = QButtonGroup(tab)
        group.addButton(self.lan_radio)
        group.addButton(self.internet_radio)
        # In the same group as the other two, which is what makes them
        # mutually exclusive: a third radio added outside it can be checked
        # while `lan_radio` still is, and `network_mode()` would then answer
        # whichever one it happened to ask about first.
        group.addButton(self.loopback_radio)
        self.plan_button = QPushButton("Show plan", tab)
        self.apply_button = QPushButton("Apply", tab)
        self.apply_button.setEnabled(False)
        self.plan_button.clicked.connect(self.show_network_plan)
        self.apply_button.clicked.connect(self.apply_network_plan)
        self.network_text = QPlainTextEdit(tab)
        self.network_text.setReadOnly(True)
        row = QHBoxLayout()
        row.addWidget(self.lan_radio)
        row.addWidget(self.internet_radio)
        row.addWidget(self.loopback_radio)
        row.addStretch(1)
        row.addWidget(self.plan_button)
        row.addWidget(self.apply_button)
        box.addLayout(row)
        box.addWidget(self.network_text, 1)
        self._add_panel_tab(tab, "networking", "Networking")
        self._plan: NetworkPlan | None = None

    def network_mode(self) -> Mode:
        """Which mode the radios are asking for. `lan` is the answer to "none of them".

        Asked in the order the modes cost: `loopback` first because it is the
        only one that is a deliberate restriction, then `internet`, then `lan`
        as the default. The three radios share one `QButtonGroup`, so at most
        one is ever checked and the order cannot change the answer — the order
        is here so that a future radio added outside that group produces a
        wrong answer in a test rather than a silent one in the app.
        """
        if self.loopback_radio.isChecked():
            return "loopback"
        return "internet" if self.internet_radio.isChecked() else "lan"

    @Slot()
    def show_network_plan(self) -> None:
        mode = self.network_mode()
        self.network_text.setPlainText("working out the plan… (this can take a few seconds)")
        self._run(lambda: self.services.network_plan(mode), self._plan_ready, self._plan_failed)

    @Slot(object)
    def _plan_ready(self, result: object) -> None:
        if not isinstance(result, NetworkPlan):
            return
        self._plan = result
        self.network_text.setPlainText(_format_plan(result))
        self.apply_button.setEnabled(result.ready)

    @Slot(object)
    def _plan_failed(self, exc: object) -> None:
        self.network_text.setPlainText(f"could not plan: {exc}")
        self.action_failed.emit(str(exc))

    @Slot()
    def apply_network_plan(self) -> None:
        plan = self._plan
        if plan is None:
            return
        self.apply_button.setEnabled(False)
        self._network_applying = True  # T95: `forget_refusal()` reads it
        self._run(lambda: self.services.network_apply(plan), self._apply_done, self._apply_failed)

    @Slot(object)
    def _apply_done(self, result: object) -> None:
        self._network_applying = False
        if isinstance(result, NetworkReport):
            self.network_text.appendPlainText("\n" + _format_network_report(result))
        self.apply_button.setEnabled(True)

    @Slot(object)
    def _apply_failed(self, exc: object) -> None:
        self._network_applying = False
        self.network_text.appendPlainText(f"\nAPPLY FAILED: {exc}")
        self.action_failed.emit(str(exc))
        self.apply_button.setEnabled(True)

    # -------------------------------------------------------- context menus

    @staticmethod
    def _copy_to_clipboard(text: str) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)

    def _show_account_context_menu(self, pos: QPoint) -> None:
        item = self.account_list.itemAt(pos)
        if item is None:
            return
        username = str(item.data(Qt.ItemDataRole.UserRole) or "")
        menu = QMenu(self)
        copy_action = menu.addAction(f"Copy Username ({username})")
        copy_action.triggered.connect(lambda: self._copy_to_clipboard(username))
        menu.addSeparator()
        if self.set_password_button.isEnabled():
            pw_action = menu.addAction("Set Password…")
            pw_action.triggered.connect(self.set_selected_password)
        if self.set_gm_button.isEnabled():
            gm_action = menu.addAction("Set GM Level…")
            gm_action.triggered.connect(self.set_selected_gm_level)
        menu.exec(self.account_list.mapToGlobal(pos))

    def _show_character_context_menu(self, pos: QPoint) -> None:
        item = self.character_list.itemAt(pos)
        if item is None:
            return
        name = str(item.data(Qt.ItemDataRole.UserRole) or "")
        menu = QMenu(self)
        copy_action = menu.addAction(f"Copy Character Name ({name})")
        copy_action.triggered.connect(lambda: self._copy_to_clipboard(name))
        menu.addSeparator()
        if self.revive_button.isEnabled():
            revive_act = menu.addAction(f"Revive {name}")
            revive_act.triggered.connect(self.revive_character)
        if self.teleport_button.isEnabled():
            teleport_act = menu.addAction(f"Teleport {name}…")
            teleport_act.triggered.connect(self.teleport_character)
        if self.set_level_button.isEnabled():
            level_act = menu.addAction(f"Set Level of {name}…")
            level_act.triggered.connect(self.set_character_level)
        if self.mail_gold_button.isEnabled():
            gold_act = menu.addAction(f"Send Gold to {name}…")
            gold_act.triggered.connect(self.mail_gold)
        if self.send_gear_button.isEnabled():
            gear_act = menu.addAction(f"Send Worn Gear to {name}…")
            gear_act.triggered.connect(self.send_gear_set)
        if self.rename_button.isEnabled():
            rename_act = menu.addAction(f"Rename {name} at Next Login")
            rename_act.triggered.connect(self.rename_character)
        menu.exec(self.character_list.mapToGlobal(pos))

    def _show_bot_context_menu(self, pos: QPoint) -> None:
        item = self.bot_list.itemAt(pos)
        if item is None:
            return
        text = item.text()
        name = text.split(" — ")[0].strip() if " — " in text else text.strip()
        menu = QMenu(self)
        copy_action = menu.addAction(f"Copy Bot Name ({name})")
        copy_action.triggered.connect(lambda: self._copy_to_clipboard(name))
        menu.exec(self.bot_list.mapToGlobal(pos))

    def _show_backup_context_menu(self, pos: QPoint) -> None:
        item = self.backup_list.itemAt(pos)
        if item is None:
            return
        path = cast(Path, item.data(Qt.ItemDataRole.UserRole))
        menu = QMenu(self)
        if self.plan_restore_button.isEnabled():
            plan_act = menu.addAction("Show Restore Plan…")
            plan_act.triggered.connect(self.show_restore_plan)
        if self.restore_button.isEnabled():
            rest_act = menu.addAction("Restore Database from this Backup…")
            rest_act.triggered.connect(self.run_restore)
        menu.addSeparator()
        copy_act = menu.addAction("Copy Backup File Name")
        copy_act.triggered.connect(lambda: self._copy_to_clipboard(path.name))
        menu.exec(self.backup_list.mapToGlobal(pos))

    @Slot(str, QPoint)
    def _show_module_context_menu(self, module_id: str, pos: QPoint) -> None:
        """Select the row that was right-clicked, then pop its menu up where it was.

        By id since T42, because the row it is about is the row that was
        right-clicked and not whatever happened to be highlighted -- and because
        this is the only press an UNCATALOGUED row still answers: those rows
        carry no buttons, so the menu is where `UNCATALOGUED_PRESS` is reached.

        The menu is BUILT by `_module_menu()` and only shown here. That split is
        a test's doing and the code is better for it: `QMenu.exec` is a Shiboken
        slot that enters a nested event loop, and it cannot be replaced from
        Python -- measured in round 2, `QMenu.exec = <lambda>` assigns without
        error and the real one still runs -- so a test of the whole method sat
        on a real popup until it was killed. What the branches decide is now
        askable without showing anything.
        """
        self.modules_panel.select(module_id)
        self._module_menu(module_id).exec(pos)

    def _selected_row_is_record_backed(self) -> bool:
        """Whether the selected row is Installed (or in doubt) because of the answers-file record.

        A relative manifest (the four mob multipliers) leaves no folder, so its
        row can only read installed from the record (T121). That is the row a
        stale record can lie on, and the one Forget is for.
        """
        manifest = self.selected_manifest()
        row = self.modules_panel.selected_row()
        return (
            manifest is not None
            and row is not None
            and row.data.installed
            and reapplies_on_top(manifest)
        )

    @Slot()
    def _forget_module_record(self) -> None:
        """ "Forget Yu'lon's record…": drop the selected mod's applied record, sending no SQL.

        The way out of a record that no longer describes the database -- a fresh
        or restored `acore_world` -- where Remove would divide base values and
        halve every creature (T121 fix wave). Asked first, No by default, in
        words that say the database is not changed.
        """
        manifest = self.selected_manifest()
        applier = self.services.applier
        if manifest is None or applier is None:
            return
        answer = QMessageBox.question(
            self,
            "Forget Yu'lon's record?",
            FORGET_RECORD_QUESTION.format(name=manifest.name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if not said_yes(answer):
            self.module_report.setPlainText(
                f"forget {manifest.id}: cancelled — nothing on this machine was changed."
            )
            return
        problem = applier.forget_applied(manifest)
        if problem:
            self.module_report.setPlainText(f"forget {manifest.id}: not done — {problem}")
        else:
            self.module_report.setPlainText(
                f"forget {manifest.id}: Yu'lon no longer records it as applied. The database "
                "was not changed."
            )
        self.reload_modules()

    def _module_menu(self, module_id: str) -> QMenu:
        """The menu for the SELECTED row: what it offers, and what each entry does."""
        menu = QMenu(self)
        if self._selected_row_is_uncatalogued():
            # An uncatalogued row has no manifest, so there are no steps to run
            # and the menu must not offer two that do nothing. It offers the
            # ANSWER instead, which is what those two entries were reduced to
            # before round 2: one action, whose whole result is the sentence.
            why_act = menu.addAction(WHY_UNCATALOGUED)
            why_act.triggered.connect(lambda: self.module_report.setPlainText(UNCATALOGUED_PRESS))
            menu.addSeparator()
        elif self._module_actions_allowed():
            inst_act = menu.addAction("Install Selected Module")
            inst_act.triggered.connect(lambda: self._module_action("install"))
            rem_act = menu.addAction("Remove Selected Module")
            rem_act.triggered.connect(lambda: self._module_action("remove"))
            if self._selected_row_is_record_backed():
                forget_act = menu.addAction(FORGET_RECORD_ACTION)
                forget_act.triggered.connect(self._forget_module_record)
            menu.addSeparator()
        copy_act = menu.addAction("Copy Module ID")
        copy_act.triggered.connect(lambda: self._copy_to_clipboard(module_id))
        return menu


# ------------------------------------------------------------- formatting


REBUILD_HISTORY = """FACT 4, established 2026-09-07 and overturned 2026-09-08.

Every `QPushButton` in `yulon/ui/` was listed on 2026-09-07 and none of them
rebuilt anything, while `_format_report` ended with

    ⚠ worldserver REBUILD required before this takes effect

for 20 of the 41 shipped manifests -- an instruction that sent its reader
hunting for a control that had never existed. Pressing Install again did not
help either: `catalog/native.py`'s `stage_build()` skips the compile whenever
`built_images()` answers true, and the image tag is derived from the install
folder's name, so adding a module to `modules/` cannot even change the tag
that would make the build stage notice.

The button was built on 2026-09-08 (`ControllerView.rebuild_server()`, the
Modules tab, over `StagedInstaller.rebuild()` with a forced compile). This
note is kept because the sentence is only honest while that control is
reachable, and `test_the_rebuild_sentence_names_a_button_that_is_really_on_the
_tab` is what holds the two together -- it reads the label off the widget and
looks for it in the report the user is shown."""

IMPORT_CONTROL = (
    "a Start deliberately skips AzerothCore's importer — it brings up only the three "
    "long-running services — and Repair refuses a database that is already complete, so "
    "nothing you have pressed so far has run it"
)
"""FACT 5, established 2026-09-07, and half of it was overturned on 2026-09-08.

`docker.apply_module_sql()` existed and was measured working on yulon-ubuntu
2026-09-07 — the same one-shot with `AC_UPDATES_ALLOWED_MODULES=mod-aoe-loot`
applied `aoe_loot_module_string.sql` and moved `acore_world.updates` 2967 → 2968
— but no widget called it, so this sentence ended "Yu'lon cannot finish this one
for you yet" and naming a control would have been the same fault as naming a
rebuild button.

The widget was built on 2026-09-08 (`ControllerView.apply_module_sql()`, the
`MODULE_SQL_BUTTON_LABEL` button on the Modules tab), so what stays true here is
only the first half: why the SQL is still sitting there after an install. The
next action is now a button, and the line quotes its label rather than
retyping it."""


def _pending_sql_lines(pending: Sequence[PendingSql]) -> list[str]:
    """One line per deferred SQL step, saying what is on disk and unapplied.

    Three shapes because `PendingSql.files` has three answers — but all three
    say NOT applied, and the empty one earned that the hard way. The first live
    run of this code (yulon-ubuntu, 2026-09-07, the real applier against
    `/home/user/wowserver`) installed `mod-aoe-loot` and resolved its manifest
    glob `data/sql/db-world/*.sql` to nothing at all. The draft line here read
    "nothing to apply", and it was false: that clone carries
    `data/sql/db-world/base/aoe_loot_module_string.sql`, one directory deeper —
    the very file FACT 1 watched the importer apply, moving `acore_world.updates`
    2967 → 2968 an hour earlier. Two sibling modules cloned the same minute put
    theirs straight in `db-world/` (mod-solocraft: 1 file; mod-transmog: 3, plus
    an `updates/` folder), so the layout is per-repository and the manifest's
    glob is Yu'lon's own bookkeeping, not the importer's rule — upstream's
    `UpdateFetcher.cpp:159-186` walks the module's `data/sql` tree itself and
    never sees that pattern. So "the glob matched nothing" means this app cannot
    count, NOT that the module has no SQL, and replacing one confident lie with
    a quieter one would have been the whole of this box's defect again.
    """
    lines = []
    for step in pending:
        where = f"the {step.db} database"
        if step.files is None:
            lines.append(
                f"  ! NOT applied: {step.path} → {where}, and this app could not work out "
                "which files that is"
            )
        elif not step.files:
            lines.append(
                f"  ! NOT applied: nothing in the clone matches {step.path}, so this app "
                f"cannot say how much SQL {where} is owed — the module may still carry some "
                "in a folder this pattern misses, and AzerothCore's importer looks for itself"
            )
        else:
            count = len(step.files)
            plural = "" if count == 1 else "s"
            lines.append(f"  ! NOT applied: {count} file{plural} matching {step.path} → {where}")
    return lines


def _format_report(report: ApplyReport) -> str:
    """The run, drawn so that every tick is something that happened.

    Two things were wrong with this function on 2026-09-07 and they are the same
    thing twice: it stated as fact what it had not checked, and it named a next
    action that does not exist.

    The ticks came from `ApplyReport.done`, and `apply.py` put its deferred SQL
    step in that list -- so the live applier reported `DONE: sql
    data/sql/db-world/*.sql -> world: left to ac-db-import on next start` over
    an install where the SQL was never applied and nothing was going to apply
    it. That half is fixed in `apply.py`; here it means `pending_sql` is drawn
    with the other mark and the other verb (see `_pending_sql_lines`).

    The closing line said a REBUILD was required, which on 2026-09-07 was true
    and useless: every `QPushButton` in `yulon/ui/` was listed that day and none
    of them rebuilt anything, so the sentence read as an instruction to press
    something that did not exist. It is an instruction again as of 2026-09-08,
    because the button it names was built -- `ControllerView.rebuild_server()`,
    on the banner above the cards, which are above the report this line appears
    in (it said "below" until T155) -- and the label is read from
    `REBUILD_BUTTON_LABEL` rather than retyped, so a rename cannot leave the
    report pointing at nothing.

    It also said it for every C++ module and for nothing else, which is the
    right split by accident -- counted through `parse_manifest` on 2026-09-07,
    `build.rebuild` is true for 20 of the 41 shipped manifests, all of type
    `module`, and false for the other 21 (7 ale, 2 keg, 11 mod, and `mod-arac`).
    A data-only one really does work without a recompile, so "this needs a
    rebuild" and "restart to apply" are two different messages about two
    different halves of the catalog, and the report says which half this item is
    in rather than leaving the reader to infer it from the presence of a
    warning.

    The remove case still does not claim to know what went into the last build.
    The live run on yulon-ubuntu 2026-09-07 removed two modules that had been
    installed minutes earlier and never built, and a draft saying "its code was
    compiled into the worldserver" was false of both -- so it says which case
    would be bad rather than which case this is.

    Nothing here is asserted about the machine. Every claim is about this app's
    own code, which is the same code on Windows as on the Linux box the
    measurements were taken on -- deliberately, because the install the real user
    runs was built by the DML bash installer and this app has never been run
    against one of those.
    """
    item = report.item_id
    lines = [f"{report.action} {item}:"]
    lines += [f"  ✓ {step}" for step in report.done]
    lines += [f"  – skipped: {step}" for step in report.skipped]
    lines += _pending_sql_lines(report.pending_sql)
    if report.left_behind:
        # T62. `rm -r modules/mod-arac` alone reads as a clean uninstall of a
        # module whose DBCs, client patch and rows are all still in place.
        lines.append(
            f"  ⚠ Removing {item} did not undo everything it installed. Still in place: "
            f"{'; '.join(report.left_behind)}. Yu'lon cannot take these back for you."
        )
    if report.rebuild_required:
        if report.action == "remove":
            lines.append(
                f"  ⚠ {item} is a C++ module: it is off disk now, but the worldserver still "
                "runs whatever was compiled into it -- worldserver REBUILD required before this "
                f"takes effect. If {item} was in the last build it is still in there until you "
                # T89: a removal raises no banner, so the one Rebuild left on
                # the tab is the entry in the menu, and the sentence says so.
                f'press "{SERVER_BUILD_LABEL}" and then "{REBUILD_BUTTON_LABEL}".'
            )
        else:
            lines.append(
                f"  ⚠ {item} is a C++ module: it does nothing until its code is compiled "
                "into the worldserver -- worldserver REBUILD required before this takes effect. "
                # T155: it said "below", and the banner this report raises is
                # ABOVE the cards and so above this box; the menu entry is the
                # same slot and is named as the second place to find it.
                f'Press "{REBUILD_BUTTON_LABEL}" on the banner above the module list (it is '
                f'also under "{SERVER_BUILD_LABEL}"); until that has run it is on disk and '
                "inert."
            )
    elif report.restart_recommended and report.world_stopped:
        # T130: this run read the world as stopped immediately before its SQL,
        # so Start is the one press owed. "Stop and then Start" worked -- Stop
        # took down the database the run had started alone -- but it was a step
        # nobody needed, told to a player who had just been asked to press Stop.
        # Told as what it is, a reading taken when the SQL ran (Codex, round 2):
        # the world can be started between then and the moment this is read,
        # and the report asks Docker nothing more, so the sentence carries the
        # press for that case too rather than stating the world's state now.
        lines.append(
            "  ⚠ The world server was stopped when this ran; press Start on the Server tab to "
            "apply this (if it has been started since, press Stop and then Start)."
        )
    elif report.restart_recommended:
        lines.append("  ⚠ Press Stop and then Start on the Server tab to apply this.")
    if report.pending_sql:
        # Every deferred step, including the one whose glob matched nothing: a
        # zero match is this app failing to count, not the module having no SQL
        # (see `_pending_sql_lines`, and mod-aoe-loot on 2026-09-07). Warning
        # about SQL that turns out not to exist costs a sentence; staying quiet
        # about SQL that does is the defect this box is named after.
        also = " either" if report.rebuild_required else ""
        lines.append(
            f"  ⚠ That SQL has not been applied{also}: {IMPORT_CONTROL}. "
            f'Press "{MODULE_SQL_BUTTON_LABEL}" below with the server stopped.'
        )
    return "\n".join(lines)


def _format_plan(plan: NetworkPlan) -> str:
    lines = [
        f"Mode: {plan.mode}   LAN IP: {plan.lan_ip or '?'}   public IP: {plan.public_ip or '-'}",
        f"Ports: {', '.join(map(str, plan.ports))}   firewall: {plan.firewall}",
    ]
    if plan.client_realmlist:
        lines.append(f"Players set realmlist to: {plan.client_realmlist}")
    if plan.firewall_commands:
        lines.append("Firewall commands:")
        lines += ["  " + " ".join(c) for c in plan.firewall_commands]
    if plan.portproxy_commands:
        lines.append("Port proxy commands:")
        lines += ["  " + " ".join(c) for c in plan.portproxy_commands]
    if plan.realmlist_sql:
        lines.append(f"Realmlist: {plan.realmlist_sql}")
    if plan.warnings:
        lines.append("Warnings:")
        lines += [f"  ⚠ {w}" for w in plan.warnings]
    if plan.manual_steps:
        lines.append("You need to do these yourself:")
        lines += [f"  {i}. {s}" for i, s in enumerate(plan.manual_steps, 1)]
    if not plan.ready:
        lines.append("Not ready to apply — see warnings.")
    return "\n".join(lines)


def _format_network_report(report: NetworkReport) -> str:
    lines = ["Applied:"]
    lines += [f"  ✓ {d}" for d in report.done] or ["  (nothing)"]
    if report.skipped:
        lines.append("Could not do (run by hand):")
        lines += [f"  – {s}" for s in report.skipped]
    if report.restart_required:
        lines.append("⚠ restart the server so the new realmlist address is used")
    return "\n".join(lines)
