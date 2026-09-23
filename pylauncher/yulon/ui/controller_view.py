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

import enum
import math
import re
import threading
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QFrame,
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
from yulon import (
    botlist,
    channel_setup,
    commands,
    dbreads,
    docker,
    install_wiring,
    logsnap,
    networking,
    party,
    platform,
    purge,
    resources,
    tuning,
    useraccounts,
)
from yulon import channel as channel_module
from yulon import dashboard as dashboard_module
from yulon import play as play_module
from yulon import steam as steam_module
from yulon.apply import Applier, ApplyReport, DockerSql, PendingSql, required_prompts
from yulon.catalog import composegen, native, preflight
from yulon.catalog.catalog import CatalogEntry
from yulon.catalog.families import clientdir
from yulon.catalog.installer import InstallerError, InstallOptions, rebuild_confirmation
from yulon.controller import Controller, InstallStatus, PortConflictError
from yulon.controller_wow_tbc import accounts as tbc_accounts
from yulon.controller_wow_tbc import console as tbc_console
from yulon.controller_wow_tbc import controller as tbc_controller
from yulon.controller_wow_tbc import maintenance as tbc_maintenance
from yulon.controller_wow_tbc import modules as tbc_modules
from yulon.controller_wow_tortoise import accounts as tortoise_accounts
from yulon.controller_wow_tortoise import autoupdate as tortoise_autoupdate
from yulon.controller_wow_tortoise import console as tortoise_console
from yulon.controller_wow_tortoise import controller as tortoise_controller
from yulon.controller_wow_tortoise import maintenance as tortoise_maintenance
from yulon.controller_wow_tortoise import modules as tortoise_modules
from yulon.controller_wow_vanilla import accounts as vanilla_accounts
from yulon.controller_wow_vanilla import console as vanilla_console
from yulon.controller_wow_vanilla import controller as vanilla_controller
from yulon.controller_wow_vanilla import maintenance as vanilla_maintenance
from yulon.controller_wow_vanilla import modules as vanilla_modules
from yulon.controller_wow_wotlk import accounts as wotlk_accounts
from yulon.controller_wow_wotlk import console as wotlk_console
from yulon.controller_wow_wotlk import maintenance as wotlk_maintenance
from yulon.controller_wow_wotlk import modules as wotlk_modules
from yulon.git import RunnerGit
from yulon.log import get_logger
from yulon.manifest import ConfKey, Manifest, Prompt, When
from yulon.manifest_store import FAMILY_FILES, ManifestStore
from yulon.networking import Mode, NetworkPlan, NetworkReport
from yulon.ui import lines
from yulon.ui.answers import said_yes
from yulon.ui.catalog_view import DirPicker, _qt_dir_picker
from yulon.ui.icons import dadcraft_icon, get_tab_icon
from yulon.ui.theme import (
    COLOR_BG_PARCHMENT,
    COLOR_GOLD_LIGHT,
    COLOR_TEXT_GOLD,
    COLOR_TEXT_WARNING,
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
)
from yulon.ui.widgets.party_panel import PartyPanel
from yulon.ui.widgets.tuning_panel import TuningPanel, build_tuning_cards

logger = get_logger(__name__)


def _realm_badge_status(status: InstallStatus) -> str:
    """The `DadcraftRealmBadge` state for an `InstallStatus` (Server tab).

    Maps the three-container reading onto the badge's four visual states: all
    up reads "online", some up reads "starting" (a realm still coming up), and
    none up reads "offline". The start/stop transitions set "starting"/"stopping"
    directly, since a poll has not yet seen the change.
    """
    if status.all_running:
        return "running"
    if status.any_running:
        return "starting"
    return "stopped"


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

    bring_up: Callable[[], bool]
    """Start the database alone and wait for it to be healthy. True if it had to."""
    take_down: Callable[[], None]
    """Stop the database again. Called only where `bring_up` answered True."""


_ROW_SETTLE_MS = 750
"""How long to leave the server to write what it has already reported.

Measured rather than guessed (2026-09-07, WotLK on `yulon-ubuntu`):

    level 55 -> 58: answered in 0.18s, the row changed after 0.30s
    level 58 -> 59: answered in 0.15s, the row changed after 0.26s

The sentence a person reads never waits for this: the server's own words appear
the moment they arrive, and only the LIST is scheduled.
"""

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


def _highest_level(entry: CatalogEntry) -> int:
    """The highest GM level this tree's own command accepts.

    Falls back to 3 where the tree's level store has not been measured, which is
    the level every core in this catalog calls administrator: a game whose block
    is absent draws no level controls that do anything anyway, and 3 is the
    number this app drew for all four before any of them were measured.
    """
    level = entry.accounts.level
    return level.max_level if level is not None else 3


PromptAsker = Callable[[QWidget, Manifest, Sequence[Prompt]], "Mapping[str, str] | None"]
"""Puts a manifest's own questions to the user, or returns `None` for "cancel".

A constructor seam for the reason `catalog_view`'s pickers are seams: a modal
dialog cannot run headless, and the part worth testing is what the tab does with
the answer — install with it, or change nothing at all.
"""


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

    `getExistingDirectory` answers `""` for cancel, which as a `Path` would be
    `Path(".")` — the process's working directory, which on a packaged build is
    wherever the user launched it from. So the empty string is turned back into
    a cancel here rather than handed on as a folder nobody chose.
    """
    chosen = QFileDialog.getExistingDirectory(parent, title)
    return Path(chosen) if chosen else None


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
    box = QMessageBox(
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


ModuleSqlRoute = Callable[[Callable[[str], None]], docker.AttachedRun]
"""Apply the SQL of the modules on disk, reporting the importer's lines to a sink.

Named once because the field, the factory parameter and the tab's own attribute
all have to be the same shape, and the thing that makes this route different
from every other seam on the tab is that its ANSWER is a run rather than a
report: whether a module's SQL was applied is only knowable from what the
importer printed (`>> Applying update <file>.sql`), so the lines are the result
and the sink is not a nicety.
"""


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
    plan_restore: Callable[[Path], wotlk_maintenance.RestorePlan]
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

    @classmethod
    def for_entry(
        cls,
        entry: CatalogEntry,
        server_dir: Path,
        client_dir: Path | None = None,
        wsl_distro: str | None = None,
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
        return factory(entry, server_dir, client_dir, wsl_distro)

    @classmethod
    def for_wotlk(
        cls,
        entry: CatalogEntry,
        server_dir: Path,
        client_dir: Path | None = None,
        wsl_distro: str | None = None,
    ) -> ControllerServices:
        """`for_entry()` under the name it had while WotLK was the only wiring.

        Kept because `main.py` still spells the call this way and that file is
        not this change's to edit; it dispatches like any other caller, so a
        TBC entry passed to it reaches the TBC package. Prefer `for_entry()`.
        """
        return cls.for_entry(entry, server_dir, client_dir, wsl_distro=wsl_distro)


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


def _db_password(entry: CatalogEntry, server_dir: Path) -> str:
    """This install's database root password, with the last-resort default.

    The entry may carry the password, or name a file the installer generated it
    into; `db_password()` knows both. The three CMaNGOS games generate one, so
    before it was read they authenticated as root with the literal "password" -
    Start and Stop need no database, which is why it surfaced later, on Create
    account and Backup. The default stays as a last resort so an install whose
    password file has gone missing still gets a tab that can start and stop,
    rather than no tab at all.
    """
    password = entry.install.db_password(server_dir)
    if password is not None:
        return password
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


def _sql_for(entry: CatalogEntry, password: str, *, wsl_distro: str | None) -> DockerSql:
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
    entry: CatalogEntry, password: str, *, wsl_distro: str | None
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
    """The two halves of "bring the database up for a backup, then put it back" (T76).

    One factory and four call sites, because the four games must not disagree
    about this: the reason the backup needed it is identical in all four (a
    `mysqldump` through `docker exec` needs the container to exist and be
    running), and so is the reason it is stopped again.

    `because` completes `docker.start_database()`'s timeout sentence, and it
    says what was not done rather than what was attempted -- a user reading
    *"…did not report healthy within 180s, so no backup was taken"* knows the
    state their server is in, which is the whole job of that sentence.

    `stop_containers([spec.db])` and not `stop_staged()`: only the container
    this started may be stopped. `stop_staged()` takes the compose project down,
    and a backup that stopped a server somebody was playing on would be a far
    worse press than one that failed.
    """
    return DatabaseAlone(
        bring_up=lambda: docker.start_database(
            spec, server_dir, because="no backup was taken", wsl_distro=wsl_distro
        ),
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
    plan_restore: Callable[[Path], wotlk_maintenance.RestorePlan],
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
    module_version: Callable[[Path], str | None] | None = None,
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
        # T44's version line, on the same flag again: it reads a clone's own
        # `.git`, and a game with no clones has none to read.
        module_version=module_version,
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
    return native.UpdateRoute(
        confirmation=lambda: install_wiring.installer_for_app(entry).update_confirmation(options),
        press=lambda cancel: install_wiring.installer_for_app(entry).update_databases(
            options, cancel=cancel
        ),
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
        state=lambda: install_wiring.installer_for_app(entry).adopt_state(options),
        confirmation=lambda: install_wiring.installer_for_app(entry).adopt_confirmation(options),
        press=lambda cancel: install_wiring.installer_for_app(entry).adopt_as_imported(
            options, cancel=cancel
        ),
    )


def _for_wotlk(
    entry: CatalogEntry,
    server_dir: Path,
    client_dir: Path | None,
    wsl_distro: str | None,
) -> ControllerServices:
    """AzerothCore: the base `Controller`, the only import gate, the only manifest store."""
    spec = entry.container_spec()
    password = _db_password(entry, server_dir)
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
        installed_modules=(
            (lambda: apply_module.installed_clones(server_dir)) if entry.has_manifests else None
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
        plan_restore=lambda path: wotlk_maintenance.plan_restore(
            path, server_dir, spec=spec, wsl_distro=wsl_distro
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
    password = _db_password(entry, server_dir)
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
        plan_restore=lambda path: tbc_maintenance.plan_restore(
            path, server_dir, wsl_distro=wsl_distro
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
    password = _db_password(entry, server_dir)
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
        plan_restore=lambda path: vanilla_maintenance.plan_restore(
            path, server_dir, wsl_distro=wsl_distro
        ),
        restore=lambda plan: vanilla_maintenance.restore(
            plan, mysql, confirm=plan.token, wsl_distro=wsl_distro
        ),
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
    password = _db_password(entry, server_dir)
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
        controller=tortoise_controller.controller_for(
            server_dir, wsl_distro=wsl_distro, pre_stop=recorder
        ),
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
        plan_restore=lambda path: tortoise_maintenance.plan_restore(
            path, server_dir, wsl_distro=wsl_distro
        ),
        restore=lambda plan: tortoise_maintenance.restore(
            plan, mysql, confirm=plan.token, wsl_distro=wsl_distro
        ),
    )


_Factory = Callable[[CatalogEntry, Path, Path | None, str | None], ControllerServices]

_FACTORIES: dict[str, _Factory] = {
    "wow-wotlk": _for_wotlk,
    "wow-tbc": _for_tbc,
    "wow-vanilla": _for_vanilla,
    "wow-tortoise": _for_tortoise,
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
    under a world that is up.
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

UPDATE_TO_LATEST_BUTTON_LABEL = "Update the server to latest…"
"""The T64 press, in one place because the button and its tests both say it.

The ellipsis is this tab's convention for "this opens a dialog first", and here
it is carrying more than usual: it is the only thing between a single click and
a multi-hour compile of code nobody has tested.
"""

RETURN_TO_PIN_BUTTON_LABEL = "Return to the tested pin…"
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
    """Put T64's three-way question, defaulting to Cancel. Never raises.

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
    box = QMessageBox(
        QMessageBox.Icon.Warning,
        title,
        text,
        QMessageBox.StandardButton.Yes
        | QMessageBox.StandardButton.Save
        | QMessageBox.StandardButton.Cancel,
        parent,
    )
    _relabel(box, QMessageBox.StandardButton.Yes, "Back up first, then update")
    _relabel(box, QMessageBox.StandardButton.Save, "Update without a backup")
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


REBUILD_BUTTON_LABEL = "Rebuild the server…"
"""The rebuild button's label, in one place because two things say it.

The button wears it, and `_format_report()` tells the user to press it by
name. Two literals would be one rename away from a report that points at a
control that is not there any more, which is the class of defect this whole
feature is a fix for.
"""

REMOVE_IDLE = "Stop and remove containers…"
REMOVE_ARMED = "Press again to remove"
"""Two labels for one button, because a teardown should not be one click away.

The wording changes rather than a dialog appearing: the explanation is a
paragraph naming what is kept, `problem_label` already renders those, and a
modal would arrive from a worker thread.
"""

REPAIR_IDLE = "Repair: finish the database import…"
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

_LIST_FLOOR_FLOOR = 40
"""The list's floor when even `MODULE_LIST_MIN_HEIGHT` is more than the tab has.

Step 3 of `_TabFit`'s order, and a floor under the floor rather than nothing: a
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
    3. the LIST's floor gives. It scrolls, so it is complete at any height; every
       pixel taken from it is a pixel of a row somebody can still scroll to.

    **And what never gives: a toolbar line, and the custom-module card.** Those
    are the two whose only way of being smaller is to cut the words inside them,
    which is the defect this whole ticket is about. The list is last rather than
    first for the same reason the log is first: order by what a shortfall COSTS
    the reader, not by what is easiest to shrink.

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
    ) -> None:
        super().__init__(tab)
        self._tab = tab
        self._log = log
        self._report = report
        self._listing = listing
        self._floor = floor
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
            spare = height - self._owed(log_open=log_room, report_open=report_room)
            # Step 3: the list gives what is still missing, and never below a
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

    def _owed(self, log_open: bool, report_open: bool, list_floor: int | None = None) -> int:
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
        """
        if list_floor is None:
            list_floor = self._floor
        box = self._tab.layout()
        if box is None:
            return 0
        margins = box.contentsMargins()
        owed = margins.top() + margins.bottom()
        inner = self._tab.width() - margins.left() - margins.right()
        shown = 0
        for index in range(box.count()):
            item = box.itemAt(index)
            widget = None if item is None else item.widget()
            if item is None or widget is None:
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
TUNING_RECREATE_LABEL = "Recreate containers…"
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

TUNING_REVERTED_FILE = "Put {file} back from {backup}.\n{rule}"

TUNING_ALL_REVERTED = (
    "Every control on this tab is back to what its file says. Nothing was written — this is "
    "the undo for what you typed here, not for what you saved."
)

TUNING_NO_FILE_BACKUP = (
    "There is no backup of {file} to revert to. Yu'lon takes one every time it saves, so the "
    "first save on this tab is what creates it."
)

TUNING_CORE_FILE = (
    "This is the server's own configuration, not a module's. Yu'lon shows it read-only in "
    "this version: who owns core configuration is a bigger question than one module's conf."
)
"""Why `worldserver.conf` is listed but not editable here (T43's own follow-up)."""

TUNING_CORE_FILES: tuple[str, ...] = (
    "env/dist/etc/worldserver.conf",
    "env/dist/etc/authserver.conf",
    "env/dist/etc/modules/playerbots.conf",
)
"""The install's own conf files, listed read-only beside the module ones.

Named here and not discovered by a glob of `env/dist/etc`: a glob would also
list every module conf a second time, and the point of the list is that these
three are the ones this tab deliberately will not write.
"""

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

_IMPORT_LINE_CHARS = 110
"""How much of the import's output the label carries: the last two lines, trimmed.

Two, because one line looks static whenever a step is slow while two show which
way it is moving. Trimmed, because this label sits above the rest of the tab and
a single 500-character line of SQL would wrap into five rows and move
everything under it.
"""


def _size_text(size: int) -> str:
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
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.entry = entry
        self.services = services
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
        self._status_pending = False
        self._verdict_pending = False
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
        # T64: a `mysqldump` is running off the GUI thread, chained in front of
        # an update. `_busy` is the LOG PANEL's flag and a backup is not a job in
        # that panel, so without this nothing on the tab knows -- see
        # `_update_route_busy()` for what a second press did.
        self._backup_before_update = False
        self._sql_owed: dict[tuple[str, str], tuple[str, ...]] = {}
        self._behind: dict[tuple[str, str], int] = {}
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
        self._import_tail: deque[str] = deque(maxlen=_IMPORT_TAIL_LINES)
        self._build_server_tab()
        self._build_console_tab()
        self._build_accounts_tab()
        self._build_characters_tab()
        self._build_bots_tab()
        self._build_maintenance_tab()
        self._build_modules_tab()
        self._build_tuning_tab()
        self._build_networking_tab()

        # What the channel says needs no daemon, no database and no network:
        # it is read from the credential file, so it is shown whether or not
        # this tab polls. Asking the SERVER about it is the part that is gated
        # on polling, just below.
        self.refresh_channel()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh_status)
        self._timer.timeout.connect(self.refresh_verdict)
        if status_poll_ms > 0:
            self._timer.start(status_poll_ms)
            # And once now. `QTimer.start()` fires nothing until the interval
            # has passed, so a tab opened over a running server spent its first
            # five seconds saying "status: unknown" with Start enabled
            # (`pyplan/bug-checklist.md:552`). A tab told not to poll is not
            # polled at all, here included.
            self.refresh_status()
            self.refresh_verdict()
            # And ask the channel once, for the same reason: a credential the
            # server has stopped accepting reads as verified straight off the
            # disk, and until something asks, the repair is never offered.
            self._check_the_channel()

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
        self.enable_channel_button.clicked.connect(self.enable_channel)
        # Hidden until the server has actually refused the saved credential.
        # This is the one control on the tab that can break a channel that
        # works -- it resets the account's password -- so it exists only where
        # there is nothing left to break.
        self.repair_channel_button = QPushButton("Repair the command channel", tab)
        self.repair_channel_button.setVisible(False)
        self.repair_channel_button.clicked.connect(self.repair_channel)
        self.status_label = QLabel("status: unknown", tab)
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
        # T34: the dead end Uninstall's own refusal names. Hidden until a poll
        # finds `server_dir` gone, because that is the one fact that makes
        # Uninstall's ownership check permanently unanswerable and this
        # button's the only remaining way off the tab. `wsl_distro` keeps it
        # hidden for a distro install even then - `server_dir` there is a path
        # on THIS process, not inside the distro, so its `is_dir()` answers a
        # question about the wrong filesystem.
        self.forget_install_button: QPushButton | None = None
        self.keep_characters_check = QCheckBox(
            "Keep my characters (the database volume is left alone)", tab
        )
        self.keep_characters_check.setChecked(False)  # owner answer 2: unticked by default
        self.keep_characters_check.setVisible(False)
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
        if self.services.uninstall is not None:
            self.uninstall_button = QPushButton("Uninstall\u2026", tab)
            self.uninstall_button.clicked.connect(self.show_uninstall_plan)
            self.uninstall_confirm_button.clicked.connect(self.run_uninstall)
            self.keep_characters_check.toggled.connect(self._redraw_uninstall_plan)
            self.keep_characters_check.setVisible(True)
            self.forget_install_button = QPushButton("Forget this install\u2026", tab)
            self.forget_install_button.setVisible(False)
            self.forget_install_button.clicked.connect(self.forget_install)
            self.uninstall_label.setVisible(True)
        self.start_button.clicked.connect(self.start_server)
        self.stop_button.clicked.connect(self.stop_server)
        self.refresh_button.clicked.connect(self.recheck)
        self.remove_button.clicked.connect(self.remove_containers)
        self.repair_button.clicked.connect(self.repair_import)
        self.stop_other_button.clicked.connect(self.stop_other_and_start)
        row = QHBoxLayout()
        for b in (
            self.start_button,
            self.stop_button,
            self.refresh_button,
            self.remove_button,
            self.repair_button,
        ):
            row.addWidget(b)
        if self.steam_button is not None:
            row.addWidget(self.steam_button)
        # The actions keep their natural size instead of stretching to fill the
        # row: a Start button drawn 226px wide beside a 95px word looks like a
        # broken border, not a button. The spare width goes to a trailing gap.
        row.addStretch(1)
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
        box.addWidget(self.verdict_label)
        box.addWidget(self.status_label)
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
        box.addWidget(self.steam_label)
        box.addWidget(self.problem_label)
        box.addWidget(self.stop_other_button)
        box.addWidget(self.repair_label)
        if self.uninstall_button is not None:
            box.addWidget(self.uninstall_button)
            box.addWidget(self.keep_characters_check)
            box.addWidget(self.uninstall_label)
            box.addWidget(self.uninstall_confirm_button)
        if self.forget_install_button is not None:
            box.addWidget(self.forget_install_button)
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
        if self._uninstall_running:
            return UNINSTALL_RUNNING
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
        self._timer.stop()
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

    @Slot()
    def refresh_status(self) -> None:
        """Re-read `docker ps` off the GUI thread and update the Server tab.

        Deliberately leaves `problem_label` alone: the five-second poll runs
        immediately after a failed action, and clearing here would wipe the
        explanation before it could be read. The Refresh BUTTON clears it —
        see `recheck()`.
        """
        if self._status_pending:
            return  # a poll is already in flight; never queue them up
        self._status_pending = True
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
        self._verdict_pending = True
        self._run(self.services.dashboard, self._verdict_ready, self._verdict_failed)

    @Slot(object)
    def _verdict_ready(self, result: object) -> None:
        self._verdict_pending = False
        if not isinstance(result, dashboard_module.Verdict):
            return
        self.verdict_label.setText(dashboard_module.line(result))
        self.verdict_label.setVisible(True)
        self.enable_channel_button.setEnabled(_press_is_allowed(result))

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

    @Slot(object)
    def _status_ready(self, result: object) -> None:
        self._status_pending = False
        status = result
        if not isinstance(status, InstallStatus):
            # Same hole, one branch narrower: a result that is not a status
            # skipped the reveal too (T54).
            self._update_forget_visibility()
            return
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
        self.realm_badge.set_status(_realm_badge_status(status))
        self._update_forget_visibility()
        self._update_client_dir_row()
        self._ask_about_the_import(status)
        self.status_changed.emit(status)

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
        """The Forget control's whole rule, asked wherever it has to hold (T34).

        One predicate rather than three separate checks copied around: the
        button's visibility, the press that opens the confirmation, and the
        press that actually forgets all have to agree, and a folder that comes
        back between any two of those moments must be read the same way each
        time it is asked (review, T34 round 2).
        """
        if self.services.uninstall is None:
            return False
        controller = self.services.controller
        return controller.wsl_distro is None and platform.folder_is_gone(controller.server_dir)

    def _update_forget_visibility(self) -> None:
        """Show "Forget this install…" exactly while `server_dir` is gone (T34).

        Read fresh on every poll rather than once at tab-build time: the folder
        can be deleted out from under an open tab, and a button that only
        appeared on the next launch would leave the owner stuck exactly as long
        as the bug this ticket fixes did.
        """
        if self.forget_install_button is None:
            return
        self.forget_install_button.setVisible(self._forget_is_eligible())

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
        self.status_label.setText(f"status: Docker not reachable ({exc})")
        self.realm_badge.set_status("stopped")
        # T54. The reveal used to run only on the success path, and the control
        # it reveals exists for an install that is GONE -- which is the case
        # most likely to have taken Docker with it. A user deleted their server
        # folder and Docker Desktop, was told by the uninstall refusal to press
        # "Forget this install…", and could not be shown it. The predicate needs
        # nothing from Docker: it asks `wsl_distro` and `folder_is_gone()`.
        self._update_forget_visibility()

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
        if busy:
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(False)
            self.remove_button.setEnabled(False)
            self.repair_button.setEnabled(False)
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
            self.rebuild_button.setEnabled(False)
            # And both T64 presses, for the rebuild's reason exactly: each of
            # them IS that rebuild with a fetch in front of it. Disabled and not
            # hidden -- whether they exist at all is `_refresh_source_version()`'s
            # question and a job of ours must not answer it.
            self.update_to_latest_button.setEnabled(False)
            self.return_to_pin_button.setEnabled(False)
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
        else:
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
            self._set_tuning_revert_all()
            self._refresh_tuning_owed()
            # Re-enabled, not re-shown: `_show_repair()` owns whether Repair is
            # visible at all, and an invisible button being enabled is harmless.
            self.remove_button.setEnabled(True)
            self.repair_button.setEnabled(True)
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
            self.rebuild_button.setEnabled(self.services.rebuild is not None)
            # Back to what this install can do, never unconditionally, and then
            # the version line is re-read: the job that just finished may BE the
            # press that moved this server off its pins, so the line and the
            # "Return to the tested pin…" button beside it are both stale until
            # this runs.
            self._set_update_buttons()
            self._refresh_source_version()
            # Back to what this install can do, never unconditionally: three of
            # the four games have no such phase and must not be handed a live
            # button by any job of their own finishing.
            self.updates_button.setEnabled(self.services.updates is not None)
            # Back to what this install can do AND what its databases last said,
            # which is why this one goes through the rule rather than repeating
            # half of it: a job of ours finishing must not hand back a control
            # that the reading never armed.
            self._set_adopt_button()

    @Slot()
    def start_server(self) -> None:
        """Start the install; a README §12 conflict is shown, never a raw Docker error."""
        self._disarm_actions()
        self.problem_label.setText("")
        self._set_busy(True)
        self.status_label.setText("status: starting…")
        self.realm_badge.set_status("starting")
        self._run(self.services.controller.start, self._server_action_done, self._start_failed)

    @Slot()
    def stop_server(self) -> None:
        self._disarm_actions()
        self.problem_label.setText("")
        self._set_busy(True)
        self.status_label.setText("status: stopping…")
        self.realm_badge.set_status("starting")
        self._run(self.services.controller.stop, self._stop_done, self._stop_failed)

    @Slot(object)
    def _server_action_done(self, _result: object) -> None:
        self._set_busy(False)
        self.refresh_status()
        self._settle_the_channel()

    def _check_the_channel(self) -> None:
        """Ask whether the saved credential still works, off the GUI thread.

        `check()` and not `settle()`: settle creates an account on an install
        that has none, and opening a tab is not permission to write a row into
        the user's auth database. `check()` asks nothing at all unless there is
        a credential to ask about.
        """
        setup = self.services.channel_setup
        if setup is None:
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
        QTimer.singleShot(_POST_INSTALL_RESETTLE_MS, self._resettle_if_pending)

    def _resettle_if_pending(self) -> None:
        # A minute is long enough for the tab to have been torn down (install,
        # then uninstall): `shutdown()` sets `_closed`, and a job started after
        # it would connect its `done` to a slot of a deleted widget.
        if getattr(self, "_closed", False):
            return
        setup = self.services.channel_setup
        if setup is None or not isinstance(setup.setup_state(), channel_setup.Pending):
            return
        self._settle_the_channel()

    @Slot(object)
    def _channel_settled(self, state: object) -> None:
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
        if result is False:
            self.problem_label.setText("None of this install's servers were running.")
        else:
            self._say_where_the_log_went()
        self.refresh_status()
        self.refresh_verdict()

    def _say_where_the_log_went(self) -> None:
        """Name the file the pre-stop snapshot wrote, or say why there is none.

        Read after the stop job has finished, so the value was written on the
        worker thread and is read on the GUI thread with the job's completion
        between them. Nothing here touches a widget from the worker.
        """
        recorder = self.services.log_snapshot
        snapshot = getattr(recorder, "last", None) if recorder is not None else None
        if snapshot is None:
            return
        if snapshot.path is not None:
            self.problem_label.setText(f"The server's log was saved to {snapshot.path}")
        elif snapshot.problem:
            self.problem_label.setText(
                f"The server stopped. Its log was not saved: {snapshot.problem}"
            )

    @Slot(object)
    def _start_failed(self, exc: object) -> None:
        self._set_busy(False)
        if isinstance(exc, PortConflictError):
            self._offer_to_stop_the_other_server(exc)
            return
        self._hide_stop_other()
        msg = str(exc)
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
        self.realm_badge.set_status("starting")
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
            self.uninstall_label.setText(result.refusal)
            return
        self._uninstall_plan = result
        self.uninstall_confirm_button.setVisible(True)
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
        lines = [
            f"This removes {plan.server_dir} ({_size_text(plan.folder_bytes)}) and this "
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
        if self._uninstall_plan is None:
            self.uninstall_label.setText(UNINSTALL_NO_PLAN)
            self.action_failed.emit(UNINSTALL_NO_PLAN)
            return
        keep = self.keep_characters_check.isChecked()
        uninstall = self.services.uninstall
        self._uninstall_running = True
        self._set_busy(True)
        self.uninstall_label.setText("Uninstalling\u2026")
        self._run(
            lambda: uninstall.run(keep_characters=keep),
            self._uninstall_done,
            self._uninstall_failed,
        )

    @Slot(object)
    def _uninstall_done(self, result: object) -> None:
        self._uninstall_running = False
        self._set_busy(False)
        self._uninstall_plan = None
        self.uninstall_confirm_button.setVisible(False)
        report = result if isinstance(result, purge.PurgeReport) else purge.PurgeReport()
        said = [f"{self.services.controller.server_dir} was removed."]
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
        message = str(exc)
        self.uninstall_label.setText(message)
        self.action_failed.emit(message)

    @Slot()
    def forget_install(self) -> None:
        """Drop this tab's record without touching Docker (T34).

        `services.uninstall.forget` and not `purge.forget_record()`: inside a
        running window that attribute is `main.py`'s closure over the ONE live
        `AppState` every tab writes into, and `forget_record()`'s own default
        would load `state.json`, forget this install, and save — silently
        undoing whatever else the session had remembered since. Off the GUI
        thread is not needed here the way it is for `run_uninstall()` — this
        writes one small file and asks Docker nothing — so a raised `OSError`
        is caught in place rather than through `_run()`'s worker.

        No project-name guess reaches Docker: without a folder to read a claim
        from, nothing here can tell this install's containers, volumes or
        images from a neighbour's, so they are left exactly where they are and
        the confirmation says so.

        `_forget_is_eligible()` is asked again both before the confirmation
        and right before the write, not trusted from the poll that showed the
        button: the folder can come back in either gap — a restore, a mistaken
        delete undone — and a press queued against a folder that is gone must
        not forget a record for one that no longer is (review, T34 round 2).
        """
        if self.services.uninstall is None:
            return
        server_dir = self.services.controller.server_dir
        if not self._forget_is_eligible():
            self.uninstall_label.setText(f"{server_dir} is back; nothing was forgotten.")
            return
        answer = QMessageBox.question(
            self,
            "Forget this install?",
            f"{server_dir} no longer exists. Forget this install? Yu'lon removes only its "
            "own record of it — the tab closes and the Catalog offers the game again. Any "
            "Docker containers, volumes or images named for it are NOT touched, because "
            "without the folder Yu'lon cannot prove which ones were its own; remove those "
            "from Docker Desktop yourself if they remain.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if not said_yes(answer):
            return
        if not self._forget_is_eligible():
            self.uninstall_label.setText(f"{server_dir} is back; nothing was forgotten.")
            return
        try:
            self.services.uninstall.forget()
        except OSError as exc:
            message = f"Could not forget {server_dir}: {exc}"
            self.uninstall_label.setText(message)
            self.action_failed.emit(message)
            return
        # Last: the window drops this tab on this signal, which destroys the
        # view. Nothing may touch `self` after it (mirrors `_uninstall_done()`).
        self.uninstalled.emit(self.entry.id, server_dir)

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
        """
        if not self._busy:
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

        No confirmation: unlike "Forget this install…" this drops no tab and
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
        self.problem_label.setText("Removing containers…")
        self._run(self.services.controller.remove, self._remove_done, self._remove_failed)

    def _disarm_remove(self) -> None:
        self._remove_armed = False
        self.remove_button.setText(REMOVE_IDLE)

    def _disarm_repair(self) -> None:
        self._repair_armed = False
        self.repair_button.setText(REPAIR_IDLE)

    def _disarm_actions(self) -> None:
        """Any other server action means the user moved on from all of them."""
        self._disarm_remove()
        self._disarm_repair()
        self._hide_stop_other()

    @Slot(object)
    def _remove_done(self, result: object) -> None:
        self._set_busy(False)
        self.problem_label.setText(
            "Containers removed; volumes kept. The next Start will recreate them."
            if result
            else "There were no containers to remove."
        )
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
        self.console_log.run(self.services.logs_source, title="worldserver log")

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
        form.addRow("Teleport to", self.teleport_where)
        form.addRow(self.teleport_button)
        if self._set_level_command() is not None:
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
        form.addRow(self.rename_button)
        form.addRow(self.revive_button)
        form.addRow("Gold", self.gold_amount)
        form.addRow(self.mail_gold_button)
        form.addRow(self.send_gear_button)

        self.character_report = QLabel("", tab)
        self._character_generation = 0
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
        return tuple(
            (button, label)
            for button, label in zip(every, _CHARACTER_ACTIONS, strict=True)
            if drawn or button is not self.set_level_button
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
        item = self.character_list.item(row) if row >= 0 else None
        if item is None:
            for button, label in self._character_actions():
                button.setText(label)
                button.setEnabled(False)
            return
        name = str(item.data(Qt.ItemDataRole.UserRole) or "")
        online = bool(item.data(Qt.ItemDataRole.UserRole + 1))
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
        pieces, mails, refusal = self._gear_set_size(name)
        self.send_gear_button.setToolTip("" if refusal is None else refusal[1])
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

    def _gear_set_size(self, name: str) -> tuple[int, int, tuple[str, str] | None]:
        """The set's size, or why there is not one -- short enough for the
        button, and in full for the tooltip behind it."""
        play = self.services.play
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
        """
        self.refresh_characters()
        if self._character_rows() != before or attempt >= _ROW_SETTLE_TRIES:
            return
        QTimer.singleShot(
            _ROW_SETTLE_MS, lambda: self._refresh_until_it_changes(before, attempt + 1)
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
            self.character_list.addItem(item)
            if character.name == chosen:
                self.character_list.setCurrentItem(item)
        if self.character_list.currentRow() < 0:
            self._character_chosen(-1)

    @Slot(object)
    def _characters_failed(self, exc: object) -> None:
        self.character_report.setText(f"Could not read this server's characters: {exc}")

    def _character_action(self, what: str, run: object) -> None:
        """One press, one sentence, all three outcomes."""
        name = self._chosen_character()
        if self.services.play is None or not name:
            return
        self.character_report.setText(f"{what} {name}…")
        self._run(run, self._character_done, self._characters_failed)  # type: ignore[arg-type]

    @Slot(object)
    def _character_done(self, outcome: object) -> None:
        done = bool(getattr(outcome, "done", False))
        said = getattr(outcome, "text", "") if done else getattr(outcome, "problem", "")
        self.character_report.setText(said.strip() or ("Done." if done else "It did not work."))
        if done:
            # NOT `self.refresh_characters()`. Measured on the live server,
            # 2026-09-07: the command answers in about 0.15s and its own row
            # lands about 0.1s after that, so a list re-read the moment the
            # answer arrives shows the state BEFORE the thing that was just
            # done -- "You change the level of Aevret to 60" above a row still
            # reading 55, which reads as the action having failed.
            rows = self._character_rows()
            QTimer.singleShot(_ROW_SETTLE_MS, lambda: self._refresh_until_it_changes(rows))

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
        self._character_action(
            "Setting the level of", lambda: play.set_level(name, level)  # type: ignore[attr-defined]
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
        if self.services.bots is None and self.services.my_party is None:
            return
        tab = QWidget(self)
        box = QVBoxLayout(tab)
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
        box.addLayout(columns)
        self._add_panel_tab(tab, "bots", "Bots")

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
            self.my_party_absent.setText(_NO_MY_PARTY.format(game=self.entry.name))
            inside.addWidget(self.my_party_absent)
            return group
        self.my_party_absent.setVisible(False)
        self.party_panel = PartyPanel(seam, jobs=self._run, parent=group)
        # The design's own cross-link (`b-users-surface.md:111`): a bot that just
        # joined a party is a row the Browse list above has not got yet.
        self.party_panel.party_changed.connect(self.refresh_bots)

        scroll = QScrollArea(group)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.party_panel)
        inside.addWidget(scroll, 1)
        return group

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
        alone = self.services.database_alone
        if alone is None:
            return self.services.backup()
        started = alone.bring_up()
        try:
            return self.services.backup()
        finally:
            if started:
                alone.take_down()

    @Slot()
    def back_up(self) -> None:
        self.backup_button.setEnabled(False)
        self.maintenance_report.setPlainText("Backing up… this can take minutes on a full world.")
        self._run(self._backup_with_the_database, self._backup_done, self._maintenance_failed)

    @Slot(object)
    def _backup_done(self, result: object) -> None:
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
            lambda: self.services.plan_restore(path),
            self._restore_plan_ready,
            self._maintenance_failed,
        )

    @Slot(object)
    def _restore_plan_ready(self, result: object) -> None:
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
            lines.append(
                "Tables the backup does not contain are LEFT AS THEY ARE — a restore merges "
                "into the databases it names rather than returning them to the state the backup "
                "was taken from. Press Restore to go ahead."
            )
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
        self.restore_button.setEnabled(False)
        self.maintenance_report.setPlainText(f"Restoring {plan.backup.name}…")
        self._run(lambda: self.services.restore(plan), self._restore_done, self._maintenance_failed)

    @Slot(object)
    def _restore_done(self, result: object) -> None:
        self._restore_plan = None
        if not isinstance(result, wotlk_maintenance.RestoreReport):
            return
        safety = ", ".join(str(p) for p in result.safety_backup) or "none"
        # Re-list BEFORE writing the report: refresh_backups() writes its own
        # message when the directory is empty, so refreshing afterwards wipes
        # the one thing the user just asked for (caught by its own test).
        self.refresh_backups()
        self.maintenance_report.setPlainText(
            f"Restored {', '.join(result.databases)} from {result.backup}.\n"
            f"The copy taken beforehand: {safety}"
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
        # The ellipsis is the convention for "this opens a dialog first": it is
        # the only visual difference between this and the two buttons beside it,
        # and the two beside it act immediately.
        self.rebuild_button = QPushButton(REBUILD_BUTTON_LABEL, tab)
        self.rebuild_button.clicked.connect(self.rebuild_server)
        self.rebuild_button.setToolTip(
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
        # T64, immediately right of Rebuild, which is what the approved design
        # asks for: the two are the same act -- compile this server again -- and
        # they differ only in what is compiled. Put anywhere else, a user
        # looking for "how do I get the newest code" would find the button that
        # recompiles the same commit.
        #
        # ABSENT rather than greyed where there is no route, which is the one
        # place this tab breaks its own rule (see `ControllerServices.
        # update_to_latest`): for a WSL-resident server or an entry the catalog
        # does not offer this for, there is nothing that would ever enable it.
        self.update_to_latest_button = QPushButton(UPDATE_TO_LATEST_BUTTON_LABEL, tab)
        self.update_to_latest_button.clicked.connect(self.update_to_latest)
        self.update_to_latest_button.setToolTip(
            "Fetch the newest code from the repositories this server was built from and compile "
            "it. Asks first, and offers a backup: this is code nobody has tested with this app."
        )
        self.update_to_latest_button.setVisible(self.services.update_to_latest is not None)
        # Hidden until this install has actually been moved off its pins, and
        # that is not the same rule as the button above. There is nothing to
        # return FROM on a server that is still on the commit the gates ran on,
        # and a live "Return to the tested pin…" there would offer a multi-hour
        # compile that ends exactly where it started.
        self.return_to_pin_button = QPushButton(RETURN_TO_PIN_BUTTON_LABEL, tab)
        self.return_to_pin_button.clicked.connect(self.return_to_the_tested_pin)
        self.return_to_pin_button.setToolTip(
            "Compile the server again from the commit this app was tested against. It does not "
            "undo anything the newer server wrote into your databases."
        )
        self.return_to_pin_button.setVisible(False)
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
        actions.addWidget(self.rebuild_button)
        actions.addWidget(self.update_to_latest_button)
        actions.addWidget(self.return_to_pin_button)
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
        custom = QGroupBox("A module this app does not ship", tab)
        custom_box = QVBoxLayout(custom)
        custom_note = QLabel(
            "Paste a repository link, or point at a folder on this computer. Yu'lon derives "
            "a manifest from it and installs it the same way as any row above.",
            custom,
        )
        custom_note.setWordWrap(True)
        custom_box.addWidget(custom_note)
        custom_row = QHBoxLayout()
        custom_row.addWidget(self.module_link_button)
        custom_row.addWidget(self.module_folder_button)
        custom_row.addStretch(1)
        custom_box.addLayout(custom_row)

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
        box.addWidget(self.module_report_strip)
        box.addWidget(self.module_report)
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
        )
        self._add_panel_tab(tab, "modules", "Modules")
        # The first reading, taken once the widgets it writes into exist. One
        # small file, no daemon and no remote: cheap enough to pay on every tab
        # the app opens, which is what lets an install that was updated in an
        # earlier session say so without anybody pressing anything.
        self._refresh_source_version()
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
        self.rebuild_button.setEnabled(self.services.rebuild is not None)
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
            self._refresh_rebuild_banner()
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
                self.services.client_dir,
                # Only what the cache ALREADY knows. Nothing is read here, so
                # the first paint of the tab costs exactly what it did before
                # T44 -- the reads happen afterwards, one event-loop turn at a
                # time, in `_fill_versions()`.
                versions=self._known_versions(installed),
                # T68, read on every reload beside the listing above: the two
                # answers must come from the same moment, or a row is drawn
                # from a folder list and a completion mark taken a press apart.
                unfinished=self._unfinished_clones(),
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
        QTimer.singleShot(0, self._fill_next_version)

    @Slot()
    def _fill_next_version(self) -> None:
        """Read ONE unread clone, draw it, and come back for the next.

        Every refusal this can meet is already inside the seam
        (`git.RunnerGit.head_version` answers `None` for a folder with no
        `.git`, a git that refused and a git that is not there), so there is
        nothing to catch here and nothing that can turn a reload into a
        failure.
        """
        server_dir = self.services.controller.server_dir
        for widget in self.modules_panel.rows():
            row = widget.data
            if not row.installed or self._versions.has(server_dir, row.family, row.id):
                continue
            self.modules_panel.set_version(
                row.family, row.id, self._versions.fill(server_dir, row.family, row.id)
            )
            QTimer.singleShot(0, self._fill_next_version)
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
        # An update re-runs the INSTALL-time steps -- it is the install over
        # content that has moved -- so it answers the install's prompts.
        go_ahead, values = self._module_values(manifest, MODULE_ACTION_STEPS[action])
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
        self.module_report.setPlainText(f"{self._module_pending}…")
        self._run(lambda: run(manifest, values), self._module_done, self._module_failed)

    def _module_values(
        self, manifest: Manifest, action: When
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

        The gate is deliberately narrow. A dialog opens only when this action
        would really render a value the manifest has no default for, so 39 of
        the 41 manifests get no new window and the applier gets `None` rather
        than `{}` — the call it has always been given.
        """
        needed = required_prompts(manifest, action)
        if not any(prompt.default is None for prompt in needed):
            return True, None
        answers = self._prompt_asker(self, manifest, needed)
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
        if not manifest.client or self.services.client_dir is not None:
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
        # Keyed `("module", key)`: `apply.module_updates()` enumerates ONE clone
        # directory -- `CLONE_DIRS["module"]`, which is `modules/` -- so every
        # key it returns is in that family by construction, and inventing a
        # family here would be a guess where this is the answer.
        self._behind = {("module", row.key): row.behind for row in result if (row.behind or 0) > 0}
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
                "Server tab, then press Rebuild again. Nothing was started.",
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
        cancel = threading.Event()
        self._rebuild_is_compile = True
        return self.rebuild_log.run(
            lambda: source(cancel),
            title=f"Rebuilding {self.entry.name}",
            cancel=cancel,
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
        self.update_to_latest_button.setEnabled(offered)
        self.return_to_pin_button.setEnabled(offered)

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
            self.return_to_pin_button.setVisible(False)
            return
        try:
            said = route.source_version()
        except OSError as exc:
            logger.warning(f"could not read what {self.entry.id} was built from: {exc}")
            said = native.SourceVersion(line="", past_the_pin=False)
        self.source_version_label.setText(said.line)
        self.source_version_label.setVisible(bool(said.line))
        self.return_to_pin_button.setVisible(said.past_the_pin)

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
        self.update_to_latest_button.setEnabled(False)
        self.return_to_pin_button.setEnabled(False)
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

        The gates are asked AGAIN here and not trusted from the press, for
        `forget_install()`'s reason one size larger: on the backup path minutes
        of `mysqldump` have gone by since they were last true, and this is the
        last point at which nothing has been fetched.
        """
        route = self.services.update_to_latest
        if route is None or self._update_route_busy():
            return False
        cancel = threading.Event()
        self._rebuild_is_compile = True
        return self.rebuild_log.run(
            lambda: route.press(cancel),
            title=f"Updating {self.entry.name} to the newest code",
            cancel=cancel,
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
        cancel = threading.Event()
        self._rebuild_is_compile = True
        return self.rebuild_log.run(
            lambda: route.to_pin(cancel),
            title=f"Returning {self.entry.name} to the tested commit",
            cancel=cancel,
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
        cancel = threading.Event()
        self._rebuild_is_compile = False
        return self.rebuild_log.run(
            lambda: route.press(cancel),
            title=f"Applying database updates to {self.entry.name}",
            cancel=cancel,
        )

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
        cancel = threading.Event()
        self._rebuild_is_compile = False
        return self.rebuild_log.run(
            lambda: route.press(cancel),
            title=f"Adopting {self.entry.name}'s databases as a finished import",
            cancel=cancel,
        )

    @Slot()
    def _rebuild_started(self) -> None:
        self._set_busy(True)

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
        # Whatever just ran on this tab -- a rebuild, an updates press, an adopt
        # press -- may have changed what the databases read as, and one of them
        # changes it on purpose. So the remembered reading is dropped and the
        # question is put again the next time the poll finds the database up,
        # which is how the adopt button greys itself the moment its own press
        # has written the row it exists to write.
        self._import_asked = False
        self._forget_the_adopt_reading()
        compiled, self._rebuild_is_compile = self._rebuild_is_compile, False
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
        return (self.console_log, self.rebuild_log)

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
            "Anything you have typed here and not saved is dropped."
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
        actions = QHBoxLayout()
        actions.addWidget(self.tuning_reload_button)
        actions.addWidget(self.tuning_revert_all_button)
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
        # No `MODULE_LIST_MIN_HEIGHT` here, deliberately: `TuningPanel` asks for
        # 288px of its own as a minimum where `ModulesPanel` asks for 70, so a
        # floor of 100 under it could never be the number that applied. A guard
        # that cannot fire is a guard nobody can test (measured 2026-09-16).
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
        self.reload_tuning()
        self.tuning_panel.set_enabled_actions(self._module_actions_allowed())

    def reload_tuning(self) -> None:
        """Re-read every installed module's conf and redraw the cards.

        Reads files and nothing else -- no git, no docker, no database -- so it
        is cheap enough to run after every install and every save.
        """
        manifests, _broken = self._load_manifests()
        rows = tuning.rows_for(
            manifests,
            self._installed_clones() or {},
            self.services.controller.server_dir,
        )
        self._tuning_rows = rows
        self.tuning_panel.set_cards(build_tuning_cards(rows))
        # WHICH files are read-only is this module's list and not the panel's:
        # `TUNING_CORE_FILES` is a decision about who owns core configuration,
        # and a second copy of it inside a widget is a second place for it to
        # drift (T44 item 13).
        self.tuning_panel.set_files(self._tuning_files(), read_only=TUNING_CORE_FILES)
        self._set_tuning_revert_all()

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
        self.tuning_panel.set_cards(build_tuning_cards(self._tuning_rows))
        self._set_tuning_revert_all()
        self.tuning_report.setPlainText(TUNING_ALL_REVERTED)

    def _confirm(self, title: str, question: str) -> bool:
        """One Yes/No dialog, defaulting to No, read through `said_yes()`.

        `said_yes()` and never `== StandardButton.Yes` by hand: PySide6's
        static `question()` returns a plain int on some builds, which is T33's
        closed bug, and one helper is the one place that can be got right.
        """
        return said_yes(
            QMessageBox.question(
                self,
                title,
                question,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        )

    def _note_tuning_owed(self, file: str) -> None:
        """Record that `file` has been written and the server has not picked it up.

        The job is `tuning.file_rule()`'s, never a guess: a conf inside a
        directory the compose binds is read off the user's own disk at world
        start and a restart is enough; one outside every bind is a copy baked
        into the image, and only a recreate picks the new one up.
        """
        rule = tuning.file_rule(file)
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
        recreate = sorted(self._tuning_owed.get("recreate", ()))
        restart = sorted(self._tuning_owed.get("restart", ()))
        self.tuning_recreate_button.setEnabled(bool(recreate) and not self._busy)
        self.tuning_restart_button.setEnabled(bool(restart or recreate) and not self._busy)
        job = "recreate" if recreate else ("restart" if restart else None)
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
        self.tuning_report.setPlainText("recreating the containers…")
        self._run(
            lambda: ("recreate", self._do_recreate()),
            self._tuning_job_done,
            self._tuning_job_failed,
        )

    def _do_restart(self) -> bool:
        """Stop, then start. ONE worker job: a stop the user then has to follow with a
        start by hand is a server left down by a control that promised a restart."""
        controller = self.services.controller
        stopped = controller.stop()
        controller.start()
        return stopped

    def _do_recreate(self) -> bool:
        """Delete the containers, then start. `remove()` keeps the volumes, so the
        characters are not touched and the next start creates the containers again --
        the Server tab's own sentence for the same pair of calls."""
        controller = self.services.controller
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
        self.tuning_report.setPlainText(f"{job}: done.")
        self.refresh_status()

    @Slot(object)
    def _tuning_job_failed(self, exc: object) -> None:
        self._set_busy(False)
        self._refresh_tuning_owed()
        self.tuning_report.setPlainText(f"FAILED: {exc}")
        self.action_failed.emit(str(exc))

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
        for name in TUNING_CORE_FILES:
            if name not in found and (server_dir / name).is_file():
                found.append(name)
        return tuple(found)

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
                tuning.restore(backups[-1], path)
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
        self.tuning_report.setPlainText("\n".join(said))
        self.reload_tuning()

    @Slot(str)
    def open_tuning_file(self, file: str) -> None:
        """Show one conf in the raw editor, read-only when it is the server's own."""
        path = self.services.controller.server_dir / file
        core = file in TUNING_CORE_FILES
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
            # this install really has rather than about every editable conf.
            shadowed=composegen.shadowed_by_env(raw, composegen.world_env(self.entry)),
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
        file = self.tuning_panel.current_file()
        if not file or file in TUNING_CORE_FILES:
            return
        path = self.services.controller.server_dir / file
        backups = tuning.backups_of(path)
        if not backups:
            self.tuning_report.setPlainText(TUNING_NO_FILE_BACKUP.format(file=file))
            return
        try:
            tuning.restore(backups[-1], path)
        except OSError as exc:
            self.tuning_report.setPlainText(TUNING_FILE_FAILED.format(file=file, exc=exc))
            self.action_failed.emit(str(exc))
            return
        self.tuning_report.setPlainText(
            TUNING_REVERTED_FILE.format(
                file=file,
                backup=backups[-1].name,
                rule=tuning.apply_sentence(tuning.file_rule(file)),
            )
        )
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
        if not file or file in TUNING_CORE_FILES:
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
        self._run(lambda: self.services.network_apply(plan), self._apply_done, self._apply_failed)

    @Slot(object)
    def _apply_done(self, result: object) -> None:
        if isinstance(result, NetworkReport):
            self.network_text.appendPlainText("\n" + _format_network_report(result))
        self.apply_button.setEnabled(True)

    @Slot(object)
    def _apply_failed(self, exc: object) -> None:
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
    one row below the report this line appears in -- and the label is read from
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
                f'press "{REBUILD_BUTTON_LABEL}" below.'
            )
        else:
            lines.append(
                f"  ⚠ {item} is a C++ module: it does nothing until its code is compiled "
                "into the worldserver -- worldserver REBUILD required before this takes effect. "
                f'Press "{REBUILD_BUTTON_LABEL}" below; until that has run it is on disk and '
                "inert."
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
