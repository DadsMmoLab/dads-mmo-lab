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

import threading
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
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
    useraccounts,
)
from yulon import channel as channel_module
from yulon import dashboard as dashboard_module
from yulon import play as play_module
from yulon import steam as steam_module
from yulon.apply import Applier, ApplyReport, DockerSql, PendingSql, required_prompts
from yulon.catalog import composegen, native
from yulon.catalog.catalog import CatalogEntry
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
from yulon.log import get_logger
from yulon.manifest import Manifest, Prompt, When
from yulon.manifest_store import FAMILY_FILES, ManifestStore
from yulon.networking import Mode, NetworkPlan, NetworkReport
from yulon.ui.widgets.job import JobRunner, LineRelay, threaded_job_runner
from yulon.ui.widgets.log_panel import LogPanel
from yulon.ui.widgets.manifest_prompt import ask_manifest_prompts
from yulon.ui.widgets.party_panel import PartyPanel

logger = get_logger(__name__)


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

CustomModuleInstall = Callable[[Manifest, "Path | None"], ApplyReport]
"""Install a manifest this app derived rather than shipped; `None` means "clone it".

DEVIATION from the design (§3.3, §3.5), forced and recorded rather than quiet.
The design has this view call `applier.install(m, None, folder=FolderSource(
path, copier), complete=...)` — lane B's widened signature, over lane A's
`copy_folder` and `complete`. Neither lane is on this branch, so the view would
not type-check against them, and a view that constructs `apply.FolderSource`
knows one thing more about the applier than `ui/*_view.py` is allowed to
(style-guide §3: delegate, never hold the business logic). So the whole call
sits behind one seam, wired from `controller_<acronym>/modules.py` — the file
whose job is "binding the shared applier to that game" — and the view hands it
the two things only the view can know: which manifest, and which folder the
user chose. Everything the design lists as `module_complete` and
`module_copy_folder` lives on the far side of it.
"""


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
    module_from_link: Callable[[str], Manifest] | None = None,
    module_from_folder: Callable[[Path], Manifest] | None = None,
    module_install_custom: CustomModuleInstall | None = None,
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
        # Defaulted for the same reason again: the four seams behind "Install
        # from link…" and "Install from folder…" belong to the one game whose
        # modules are checkouts under `modules/`, and that factory passes them.
        module_from_link=module_from_link,
        module_from_folder=module_from_folder,
        module_install_custom=module_install_custom,
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
    module_applier = (
        wotlk_modules.applier(
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
    # THE CONSOLE IS THE CHANNEL ON THIS TREE, and it is the second time that has
    # been true. 8.2e wired `AttachChannel` here because the fork's mangosd
    # linked neither gsoap nor `RASocket`; the fork re-added SOAP (3f9a062), the
    # pin moved onto it (3a8472e) and this became Vanilla's `InstallChannel` over
    # this tree's own account seam. T30 moved the whole entry off that fork and
    # onto the Penqle core, which has no SOAP at all: the string does not occur
    # anywhere under its `src/` but in one comment, `mangosd.conf.dist.in` ships
    # no `SOAP.*` key, and no gsoap is vendored (measured on `yulon-arch`
    # 2026-09-11, `pyplan/gates/t30-measure-yulon-arch-2026-09-11/05-conf-keys.txt`).
    # So `operations.channel` says `attach` again and this is the transport that
    # goes with it.
    #
    # NOTHING SOAP-SIDE WAS DELETED for that move, deliberately: the owner has
    # asked the core's maintainer for the subsystem back and expects it. When a
    # rev lands with it, this block goes back to the `channel_setup.InstallChannel`
    # over `channel_module.SoapChannel` that stood here between 2026-09-08 and
    # 2026-09-11, and the entry's `operations` block regains the four keys its
    # own `notes` list. `SoapChannel`, `InstallChannel`, the `enable_conf` writer
    # and `tortoise_accounts.reset_own_password` are all still here and still
    # under test.
    #
    # ONE channel object, used three times: the Server tab's probe presses it,
    # the Accounts tab sends this tree's two commands down it, and the Play tab's
    # rename/revive go the same way. They are the same console and the same lock
    # -- two channels over one `docker attach` would interleave two replies in
    # one window (8.3d).
    console = channel_module.AttachChannel(
        send=lambda cmd, **kw: tortoise_console.send(cmd, wsl_distro=wsl_distro, **kw)
    )
    # There is no credential and nothing to set up on this core, so the channel
    # is simply always the console: `channel_for_saved` answers it rather than
    # looking one up, and the `AttachChannel` says "could not ask" by itself when
    # the world is not there to answer.
    accounts_admin = useraccounts.InstallAccounts(
        entry,
        server_dir,
        sql=sql,
        channel_for_saved=lambda: console,
        app_account=channel_setup.account_name(composegen.install_id(server_dir)),
    )
    characters_admin = play_module.InstallPlay(
        entry,
        server_dir,
        sql=sql,
        channel_for_saved=lambda: console,
    )
    return _assemble(
        entry,
        server_dir,
        client_dir=client_dir,
        wsl_distro=wsl_distro,
        dashboard=watcher.tick,
        log_snapshot=recorder,
        accounts=accounts_admin,
        play=characters_admin,
        # The Server tab's probe, and it is the CHANNEL's `send` rather than the
        # Console tab's own seam below: what that tab shows has to come through
        # the object every other feature on this tree uses, or it proves the
        # console works and not the channel (8.2e).
        console_probe=console.send,
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
        # Every service call goes through this: on a worker thread in the app,
        # inline in tests (review finding, 2026-08-21 — the window used to
        # freeze for the length of a `docker compose up`).
        self._jobs: JobRunner = job_runner or threaded_job_runner(self)
        self._busy = False
        self._status_pending = False
        self._verdict_pending = False
        self._module_pending: str | None = None
        # Whether the module job in flight is a CUSTOM install. A flag rather
        # than a reading of `_module_pending`'s text, and rather than
        # re-listing after every module action: `reload_modules()` clears the
        # list's selection, and a user who has just pressed "Install selected"
        # would find the row they chose deselected under them. The rust page
        # refreshed after every action (`ModuleManager.svelte:439`) because it
        # had no selection to lose.
        self._custom_install_pending = False
        self._console_pending = False
        self._tabs = QTabWidget(self)
        layout = QVBoxLayout(self)
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
        self.stop_button = QPushButton("Stop", tab)
        self.refresh_button = QPushButton("Refresh", tab)
        # Deliberate, per checklist 6.5: nothing removes a container today, and
        # whatever does must not be a stray click next to Stop. It arms on the
        # first press and acts on the second, and anything else disarms it.
        self.remove_button = QPushButton(REMOVE_IDLE, tab)
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
        self.keep_characters_check = QCheckBox(
            "Keep my characters (the database volume is left alone)", tab
        )
        self.keep_characters_check.setChecked(False)  # owner answer 2: unticked by default
        self.keep_characters_check.setVisible(False)
        self.uninstall_confirm_button = QPushButton("Uninstall this server", tab)
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
        box.addWidget(QLabel(f"<b>{self.entry.name}</b> — {self.services.controller.server_dir}"))
        box.addWidget(self.verdict_label)
        box.addWidget(self.status_label)
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
        box.addStretch(1)
        self._tabs.addTab(tab, "Server")

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
        self._ask_about_the_import(status)
        self.status_changed.emit(status)

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
            self.uninstall_confirm_button.setEnabled(False)
            self.keep_characters_check.setEnabled(False)
            self.rebuild_button.setEnabled(False)
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
            # Re-enabled, not re-shown: `_show_repair()` owns whether Repair is
            # visible at all, and an invisible button being enabled is harmless.
            self.remove_button.setEnabled(True)
            self.repair_button.setEnabled(True)
            if self.uninstall_button is not None:
                self.uninstall_button.setEnabled(True)
            self.uninstall_confirm_button.setEnabled(True)
            self.keep_characters_check.setEnabled(True)
            self.rebuild_button.setEnabled(self.services.rebuild is not None)
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
        self._run(self.services.controller.start, self._server_action_done, self._start_failed)

    @Slot()
    def stop_server(self) -> None:
        self._disarm_actions()
        self.problem_label.setText("")
        self._set_busy(True)
        self.status_label.setText("status: stopping…")
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
        text = line.strip()
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
        self._tabs.addTab(tab, "Console")

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
        self.account_list.currentRowChanged.connect(self._account_chosen)
        self.refresh_accounts_button = QPushButton("Refresh the list", existing)
        self.refresh_accounts_button.clicked.connect(self.refresh_accounts)
        change = QFormLayout()
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
        box.addWidget(accounts)
        box.addWidget(existing)
        box.addWidget(self.account_report)
        box.addStretch(1)
        self._tabs.addTab(tab, "Accounts")

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
        self.character_list.currentRowChanged.connect(self._character_chosen)
        self.refresh_characters_button = QPushButton("Refresh the list", people)
        self.refresh_characters_button.clicked.connect(self.refresh_characters)
        people_box.addWidget(self.character_list)
        people_box.addWidget(self.refresh_characters_button)

        actions = QGroupBox("What to do", tab)
        form = QFormLayout(actions)
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

        box.addWidget(people)
        box.addWidget(actions)
        box.addWidget(self.character_report)
        box.addStretch(1)
        self._tabs.addTab(tab, "Characters")

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
            # (`pyplan/gates/8.4c-vanilla-m910q-2026-09-07/4-two-of-one-name.png`).
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
        self._run(
            play.listing,  # type: ignore[attr-defined]
            lambda listed: self._characters_listed_at(generation, listed),
            self._characters_failed,
        )

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
        row.addWidget(self.bot_filter)
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
        box.addWidget(browse)
        box.addWidget(self._build_my_party_group(tab))
        self._tabs.addTab(tab, "Bots")

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
        inside.addWidget(self.party_panel)
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

        box.addWidget(self.interrupted_label)
        box.addWidget(self.forget_button)
        box.addLayout(top)
        box.addWidget(self.backup_list, 2)
        box.addLayout(actions)
        box.addWidget(self.maintenance_report, 1)
        self._tabs.addTab(tab, "Maintenance")
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

    @Slot()
    def back_up(self) -> None:
        self.backup_button.setEnabled(False)
        self.maintenance_report.setPlainText("Backing up… this can take minutes on a full world.")
        self._run(self.services.backup, self._backup_done, self._maintenance_failed)

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
        self.module_list = QListWidget(tab)
        self.module_report = QPlainTextEdit(tab)
        self.module_report.setReadOnly(True)
        self.install_module_button = QPushButton("Install selected", tab)
        self.remove_module_button = QPushButton("Remove selected", tab)
        # The third button on this tab, and the only one that is not about one
        # selected manifest: it applies the pending SQL of everything installed
        # here, because that is the granularity the importer has — it is handed
        # the module folder list and ledgers what it applies in `updates`.
        self.module_sql_button = QPushButton(MODULE_SQL_BUTTON_LABEL, tab)
        # The fourth, and the only read-only one: it fetches and counts and
        # writes nothing outside each clone's `.git`. It is a button rather than
        # part of the status poll because it costs one network round trip per
        # installed module, and a poll would pay that every few seconds.
        self.module_updates_button = QPushButton(MODULE_UPDATES_BUTTON_LABEL, tab)
        # The fifth and sixth, and the only two whose subject is not already in
        # the list above them: a module this app does not ship, named by the
        # user. They sit after "Remove selected" and before the module-SQL
        # button because that is the order the tab is read in — the two that
        # act on the selection, then the two that add to it, then the two that
        # act on everything installed.
        self.module_link_button = QPushButton(MODULE_LINK_BUTTON_LABEL, tab)
        self.module_folder_button = QPushButton(MODULE_FOLDER_BUTTON_LABEL, tab)
        self.module_link_button.clicked.connect(self.install_module_from_link)
        self.module_folder_button.clicked.connect(self.install_module_from_folder)
        self.install_module_button.clicked.connect(lambda: self._module_action("install"))
        self.remove_module_button.clicked.connect(lambda: self._module_action("remove"))
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
        self.rebuild_log = LogPanel(tab)
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
        row = QHBoxLayout()
        row.addWidget(self.install_module_button)
        row.addWidget(self.remove_module_button)
        row.addWidget(self.module_link_button)
        row.addWidget(self.module_folder_button)
        row.addWidget(self.module_sql_button)
        row.addWidget(self.module_updates_button)
        row.addStretch(1)
        row.addWidget(self.adopt_button)
        row.addWidget(self.updates_button)
        row.addWidget(self.rebuild_button)
        box.addWidget(self.module_list, 2)
        box.addLayout(row)
        box.addWidget(self.module_report, 1)
        box.addWidget(self.rebuild_log, 2)
        self._tabs.addTab(tab, "Modules")
        self._manifests: dict[str, Manifest] = {}
        # The importer talks from a worker thread for however long it runs, and
        # this is what carries its lines to the GUI one. Same mechanism as the
        # repair's `_import_relay`, and a separate object because the two runs
        # write to different widgets. See `LineRelay`.
        self._module_sql_relay = LineRelay(self)
        self._module_sql_relay.line.connect(self._module_sql_line)
        self.reload_modules()
        enabled = self.services.store is not None and self.services.applier is not None
        self.install_module_button.setEnabled(enabled)
        self.remove_module_button.setEnabled(enabled)
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
        # A separate gate from the two above, and it must stay separate: the
        # three CMaNGOS games have no manifest store at all, and their
        # worldservers are still compiled from a checkout somebody may have
        # patched. Tying the rebuild to `store` would have taken the control
        # away from three of the four games for a reason that is about
        # manifests.
        self.rebuild_button.setEnabled(self.services.rebuild is not None)
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

    def reload_modules(self) -> None:
        """Fill the list from the store (every family), newest store contents first."""
        self.module_list.clear()
        self._manifests.clear()
        store = self.services.store
        if store is None:
            self.module_list.addItem("(this game has no manifests yet)")
            return
        for kind in FAMILY_FILES:
            try:
                items = list(store.load_all(kind))
            except Exception as exc:  # boundary: a broken manifest tree must not kill the UI
                self.module_list.addItem(f"!! could not load {kind}s: {exc}")
                continue
            for manifest in items:
                item = QListWidgetItem(
                    f"[{manifest.type}] {manifest.name} — {manifest.description}"
                )
                item.setData(256, manifest.id)  # Qt.UserRole
                self.module_list.addItem(item)
                self._manifests[manifest.id] = manifest

    def selected_manifest(self) -> Manifest | None:
        item = self.module_list.currentItem()
        if item is None:
            return None
        return self._manifests.get(str(item.data(256)))

    def _module_action(self, action: str) -> None:
        manifest = self.selected_manifest()
        applier = self.services.applier
        if manifest is None or applier is None:
            return
        go_ahead, values = self._module_values(manifest, action)
        if not go_ahead:
            self._module_pending = None
            self.module_report.setPlainText(
                f"{action} {manifest.id}: cancelled — nothing on this machine was changed."
            )
            return
        run = applier.install if action == "install" else applier.remove
        self._module_pending = f"{action} {manifest.id}"
        self.module_report.setPlainText(f"{self._module_pending}…")
        self._run(lambda: run(manifest, values), self._module_done, self._module_failed)

    def _module_values(
        self, manifest: Manifest, action: str
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
        needed = required_prompts(manifest, cast(When, action))
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
            self._custom_install_pending = False
            self.module_report.setPlainText(str(exc))
            self.action_failed.emit(str(exc))
            return
        self._module_pending = f"{what} {manifest.id}"
        self._custom_install_pending = True
        self.module_report.setPlainText(f"{self._module_pending}…")
        self._run(lambda: route(manifest, folder), self._module_done, self._module_failed)

    @Slot(object)
    def _module_done(self, result: object) -> None:
        self._module_pending = None
        custom, self._custom_install_pending = self._custom_install_pending, False
        if not isinstance(result, ApplyReport):
            return
        self.module_report.setPlainText(_format_report(result))
        # The list is re-read for exactly two outcomes, both of which changed
        # what is in it: a custom module was just added, or a record was just
        # dropped. Asked AFTER the report is on screen and after the remove
        # returned -- a forget before the remove would drop the record of a
        # remove that then failed, leaving a folder on disk with no row in the
        # list to try again with (`purge.py`'s ordering, phase8-decisions).
        forgotten = False
        forget = self.services.module_forget
        if result.action == "remove" and forget is not None:
            manifest = self._manifests.get(result.item_id)
            if manifest is not None:
                forgotten = forget(manifest)
        if custom or forgotten:
            self.reload_modules()

    @Slot(object)
    def _module_failed(self, exc: object) -> None:
        what, self._module_pending = self._module_pending or "module action", None
        self._custom_install_pending = False
        self.module_report.setPlainText(f"{what} FAILED: {exc}")
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
        text = line.rstrip()
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
        Enter declines; `is ... Yes` rather than `is not ... No`, because
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
        if (
            QMessageBox.question(
                self,
                f"Rebuild {self.entry.name}?",
                rebuild_confirmation(self.entry, self.services.controller.server_dir),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            is not QMessageBox.StandardButton.Yes
        ):
            logger.info(f"rebuild of {self.entry.id} declined at the confirmation")
            return False
        # The engine's own cancel, handed to the panel so its Stop button reaches
        # a build that is blocked between lines rather than only stopping the
        # reader of them.
        cancel = threading.Event()
        return self.rebuild_log.run(
            lambda: source(cancel),
            title=f"Rebuilding {self.entry.name}",
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
        Yes/No with No as the default so Enter declines, `is ... Yes` so Escape
        and the close button decline too, and the same panel — one long job on
        this tab at a time, because a rebuild and an update want the same
        containers.
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
        if (
            QMessageBox.question(
                self,
                f"Apply database updates to {self.entry.name}?",
                text,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            is not QMessageBox.StandardButton.Yes
        ):
            logger.info(f"database updates for {self.entry.id} declined at the confirmation")
            return False
        cancel = threading.Event()
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
        so Enter declines, `is ... Yes` so Escape and the close button decline
        too, and the same panel — one long job on this tab at a time.
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
        if (
            QMessageBox.question(
                self,
                f"Adopt {self.entry.name}'s databases as a finished import?",
                text,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            is not QMessageBox.StandardButton.Yes
        ):
            logger.info(f"adopting {self.entry.id} declined at the confirmation")
            return False
        cancel = threading.Event()
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
        self._tabs.addTab(tab, "Networking")
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
    `/home/pk/wowserver`) installed `mod-aoe-loot` and resolved its manifest
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
