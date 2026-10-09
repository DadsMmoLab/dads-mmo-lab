"""Catalog view — the browsable "store" of installable servers (roadmap 4.2).

Renders `catalog.json` as one tile per game with an Install button. The view
delegates: clicking Install asks the user for the server folder (and, where
the game needs it, their own client folder — README §3a), builds an engine
through the factory it was given, and streams `engine.run()`
into the `LogPanel`. No Docker, no subprocess, no business logic here
(style-guide §3); results go up as signals (§5).
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Mapping
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from yulon import docker, platform, wsl
from yulon.catalog.catalog import Catalog, CatalogEntry
from yulon.catalog.installer import (
    FORMER_DEFAULT_DIRS,
    InstallEngine,
    InstallOptions,
    ReadyWaitStopped,
    cancelled_install_message,
    compose_file,
    default_server_dir,
    platform_names,
    unsupported_platform_message,
)
from yulon.log import get_logger
from yulon.ui import folder_picker, single_instance
from yulon.ui.answers import said_yes
from yulon.ui.folder_picker import pick_folder
from yulon.ui.icons import dadcraft_icon
from yulon.ui.message_box import FittedMessageBox, ask_yes_no, show_information, show_warning
from yulon.ui.theme import COLOR_TEXT_GOLD
from yulon.ui.widgets.dadcraft_decorations import DadcraftCampaignCard
from yulon.ui.widgets.log_panel import LogPanel
from yulon.ui.widgets.prompt import InputPrompter

logger = get_logger(__name__)

QWIDGETSIZE_MAX = 16777215
"""Qt's "no maximum" for a widget dimension (`QWIDGETSIZE_MAX` in C++)."""

BRING_FROM_ANOTHER = "Bring from another computer…"
"""The tile press that builds a whole server from a move package (T601 level 2)."""
MOVE_IN_FILE = ".yulon-move-in.json"
"""`catalog.native.MOVE_IN_FILE` (pinned equal by a test): a folder a whole-server move is building.

Spelled here, not imported, so this view does not import the install engine at module scope.
"""
MOVE_PACKAGE_FILTER = "Move packages (*.zip)"

PackagePicker = Callable[[QWidget, str, Path], Path | None]
YesNo = Callable[[QWidget, str, str], bool]


def _unfinished_move(server_dir: Path) -> str:
    return (
        f"{server_dir} is a server being brought from another computer, and that did not "
        f"finish. Press {BRING_FROM_ANOTHER} with the same file and this folder to carry on."
    )


def _qt_package_picker(parent: QWidget, title: str, start: Path) -> Path | None:
    return folder_picker.pick_open_file(parent, title, start, MOVE_PACKAGE_FILTER)


InstallerFactory = Callable[[CatalogEntry], InstallEngine]
"""What builds the engine for one entry.

The `InstallEngine` protocol rather than a class: `installer_for()` decides the
family engine from `catalog.json` data, and this view is deliberately not told
what it got — every family has the same `run()`.
"""
DirPicker = Callable[[QWidget, str, Path | None], Path | None]


def _existing_ancestor(start: Path | None) -> Path | None:
    """The nearest directory at or above `start` that actually exists.

    `getExistingDirectory()` opens on a path that does not exist by showing its
    PARENT with the missing name typed into the field - where `Choose` is
    disabled, because the name names nothing, and Enter answers "Directory not
    found. Please verify the correct directory name was given."

    That is a dead end on the first install, and it is the one every new user
    meets: the suggestion (then `~/wow-server-playerbots`) by definition did
    not exist yet. The app proposed a folder and then refused its own proposal;
    the only way forward was the New Folder button, which nothing pointed at.
    Measured on a clean Arch box, 2026-08-24.

    Walking up is the fix rather than creating the directory: a picker that
    makes a folder as a side effect leaves an empty one behind when the user
    cancels, and this one is opened before anything has been agreed to.

    `parents` rather than a `while` loop that follows `.parent`, because that
    loop cannot terminate on its own: `Path('Q:/gone').parent` is `Q:/` and
    `Path('Q:/').parent` is `Q:/` again, so an unmounted drive letter spins
    forever. A guard against that is a guard someone can delete - mutation
    testing removed it and the suite HUNG rather than failed, which is a test
    that reports a defect by never finishing. `parents` is finite by
    construction, so there is nothing left to guard.
    """
    if start is None:
        return None
    for candidate in (start, *start.parents):
        if candidate.is_dir():
            return candidate
    return None


def _qt_dir_picker(parent: QWidget, title: str, start: Path | None) -> Path | None:
    return pick_folder(parent, title, _existing_ancestor(start))


SuggestionAsker = Callable[[QWidget, str, Path], "bool | None"]
"""Offer the default folder, new or already there: True to take it, False to open the picker,
None when the question was closed (Cancel, Escape, the window's X) and nothing is to start."""

USE_THIS_FOLDER_LABEL = "Use this folder"
CHOOSE_ANOTHER_FOLDER_LABEL = "Choose another folder\u2026"
CANCEL_LABEL = "Cancel"
"""The Install question's three presses, named once for the dialog and its tests."""


def _suggestion_question(game: str, suggested: Path) -> str:
    """The Install dialog's words for `suggested`: a new folder, an empty one, or a server's.

    `default_server_dir()` offers a former default folder only when it holds a
    server, and the dialog said "into this new folder" and "Yu'lon makes the
    folder" about it all the same (PR 291 Linux live test).
    """
    elsewhere = "Choose another folder if you would rather put it somewhere else."
    if compose_file(suggested) is not None:
        return (
            f"Install {game} into this existing server folder?\n\n{suggested}\n\n"
            f"A server from an earlier install is already in this folder, and Yu'lon uses it. "
            f"{elsewhere}"
        )
    if suggested.is_dir():
        return (
            f"Install {game} into this folder?\n\n{suggested}\n\n"
            f"The folder is already there. {elsewhere}"
        )
    return (
        f"Install {game} into this new folder?\n\n{suggested}\n\n"
        f"Yu'lon makes the folder when the install starts. {elsewhere}"
    )


def _qt_suggestion_asker(parent: QWidget, game: str, suggested: Path) -> bool | None:
    """ "Install into this folder?" - Yes takes it, No opens the picker, Cancel closes it.

    **This exists because a file picker cannot say "make this one".** The
    suggestion is a folder that by definition does not exist on a first install,
    and `QFileDialog.getExistingDirectory()` returns only directories that do.
    `_existing_ancestor()` therefore walks up and opens on the PARENT with
    nothing filled in, and the name the user has to create was put in the
    dialog's TITLE - the one place in a window that cannot be clicked and, at 91
    characters for `wow-server-playerbots`, is truncated by the window manager
    before it reaches the name (owner, on Fedora 44, 2026-09-03). The app told
    the user what to make in the one place they could neither read nor act on.

    Asking first turns both symptoms off at once: the folder is named in body
    text that wraps, and taking it is one button. Nothing is created here -
    `_existing_ancestor()`'s rule stands, and for its original reason: a picker
    that makes a directory as a side effect leaves an empty one behind when the
    user cancels, and this runs before anything has been agreed to. The path is
    created by the install itself, which is also what claims it (`native.py`,
    `_claim_before_writing`).

    Default is Yes. The suggestion is right for nearly every install, and the
    one it is wrong for - a second install of the same game - is a folder the
    user is already thinking about.

    Cancel is the Escape button, named. With only Yes and No, Qt made No the
    escape button, so Escape opened the folder picker instead of closing the
    question (PR 291's Windows live test, 2026-10-04).
    """
    box = FittedMessageBox(
        QMessageBox.Icon.Question,
        f"Install {game}",
        _suggestion_question(game, suggested),
        QMessageBox.StandardButton.Yes
        | QMessageBox.StandardButton.No
        | QMessageBox.StandardButton.Cancel,
        parent,
    )
    box.setDefaultButton(QMessageBox.StandardButton.Yes)
    box.setEscapeButton(QMessageBox.StandardButton.Cancel)
    for which, label in (
        (QMessageBox.StandardButton.Yes, USE_THIS_FOLDER_LABEL),
        (QMessageBox.StandardButton.No, CHOOSE_ANOTHER_FOLDER_LABEL),
        (QMessageBox.StandardButton.Cancel, CANCEL_LABEL),
    ):
        button = box.button(which)
        if button is not None:
            button.setText(label)
    answer = box.exec()
    if said_yes(answer):
        return True
    if answer == QMessageBox.StandardButton.No:
        return False
    return None


def _pin_compose_project(server_dir: Path) -> None:
    """Freeze the compose project name so the folder can be moved later.

    Best-effort on purpose: a server that is otherwise fine must not fail to
    attach because Docker happened to be down at that moment. The cost of
    skipping it is the pre-existing behaviour, not a new failure.
    """
    try:
        docker.pin_project_name(server_dir)
    except OSError as exc:  # unwritable .env, vanished directory
        logger.warning(f"could not pin the compose project name in {server_dir}: {exc}")


WslServerPicker = Callable[[tuple[wsl.FoundServer, ...]], "wsl.FoundServer | None"]
"""Chooses one of the servers discovery found, or None to cancel.

A constructor seam for the same reason `DirPicker` is one: a modal dialog cannot
run headless, and the logic worth testing is what happens with the answer.
"""


def _qt_wsl_server_picker(found: tuple[wsl.FoundServer, ...]) -> wsl.FoundServer | None:
    """The real picker: one line per server, chosen by name."""
    labels = [
        f"{server.project}  —  {server.distro}  ({'running' if server.running else 'stopped'})"
        for server in found
    ]
    choice, ok = QInputDialog.getItem(
        None,
        "Servers found in WSL",
        "Yu'lon found these Docker Compose projects inside your WSL distros.\n"
        "Adopting one lets Yu'lon manage it from here.",
        labels,
        0,
        False,
    )
    if not ok or choice not in labels:
        return None
    return found[labels.index(choice)]


INSTALL_STOPPED_TITLE = "Install stopped"
"""The popup's title when a stopped install ended on a failure that says what it left (T228)."""


def ready_wait_stopped_message(entry: CatalogEntry, kind: type) -> str:
    """The popup for an install stopped in its ready wait (T247 review): the log's own sentence.

    `kind` is the Stop's type (`LogPanel.stopped_by`): stopped inside the watch
    after its banner, the world had reported ready, and the sentence says so.
    """
    from yulon.catalog import native  # the engine's module; imported where it is used

    left = (
        native.INSTALL_LEFT_RUNNING
        if issubclass(kind, native.StoppedInTheWatch)
        else native.INSTALL_LEFT_LOADING
    )
    return (
        f"Stop was pressed while {entry.name}'s world server was starting, so it has not been "
        f"remembered as an install yet. {left}"
    )


class Identification(Enum):
    """What `_identify()` established about a folder: three answers, not two.

    `_looks_like()` answered this question with a bool, and a bool has room for
    two of the three - so "I could not check" was returned as True and arrived
    at the caller spelled exactly like "this is that game". The two are not the
    same claim and the caller has to be able to act on the difference, so they
    are separate members here.
    """

    MATCHES = "matches"
    """The compose file was read and names a container this entry uses."""

    DIFFERENT = "different"
    """The compose file was read and names none of them - evidence of another game."""

    UNVERIFIED = "unverified"
    """No evidence either way: no compose file to read, or the read failed.

    Deliberately NOT a refusal. See `_identify()` for why adoption continues on
    this answer, and what the caller owes the user in exchange.
    """


def _identify(entry: CatalogEntry, server_dir: Path) -> Identification:
    """Does the compose file in `server_dir` name any container this game uses?

    Discovery finds compose PROJECTS, not WoW servers - `docker compose ls`
    reports a TBC install, a Nextcloud and someone's blog with equal enthusiasm.
    Adopting one under the wrong catalog entry produces a tab whose every button
    names containers that do not exist, failing separately and confusingly
    instead of once and clearly.

    The catalog's container names are the evidence; the project name is a folder
    name and proves nothing.

    `UNVERIFIED` rather than a refusal when the file cannot be read at all. The
    folder lives inside a distro and is reached over a UNC path, and that read
    can fail for reasons unrelated to which game it is - refusing on "I could
    not check" would block the migration this feature exists to provide. That
    reasoning is unchanged and is why this still does not refuse.

    WITHDRAWN, from the version of this docstring that said True on an
    unreadable file: "Adopting one under the wrong catalog entry produces a tab
    whose every button names containers that do not exist" as a statement of the
    WHOLE cost. It is still a cost, and it is still the one the `DIFFERENT`
    branch is written for; it is no longer the largest. A folder that merely
    *looked* adoptable reaches file deletion, which is not an annoyance.

    That chain was re-read link by link on 2026-09-02, in the code rather than
    from the earlier note, and each link is where it says:

      - `adopt_from_wsl()` below emits `adopted` for an `UNVERIFIED` folder
        once the user answers yes to the confirm (it was unconditional when
        this chain was first written, and the chain still holds, because the
        user can say yes);
      - `catalog.json` gives `wow-wotlk` `"has_manifests": true`, and it is the
        only entry of the four that carries it;
      - `controller_view.ControllerServices.for_wotlk()` passes that same
        `server_dir` into `wotlk_modules.applier()` when `entry.has_manifests`,
        and `apply.Applier.__init__` keeps it as `self.server_dir`;
      - `apply.Applier.install()` clones into
        `server_dir / CLONE_DIRS[type] / id`, and `git.RunnerGit.clone()`
        `shutil.rmtree()`s that destination BEFORE the first git invocation.

    The precondition on the deletion, which the earlier note left out: it fires
    only when the destination already exists AND is not itself a git checkout
    (`(dest / ".git").is_dir()` sends it down the update path instead). What is
    deleted is that subdirectory of the adopted folder, not the adopted folder
    itself - still the user's files, still without anyone having established
    that the folder is ours. Because the rmtree precedes the fetch, a clone that
    was never going to succeed deletes anyway. All of this is asserted in
    `test_an_unverified_adoption_reaches_the_applier_with_no_ownership_check`,
    which drives it with only `runner.run` doubled.

    So the answer widened instead of hardening: the migration keeps working, and
    the caller - and the user - are told which of the three they got.
    """
    compose = compose_file(server_dir)
    if compose is None:
        return Identification.UNVERIFIED
    try:
        text = compose.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return Identification.UNVERIFIED
    spec = entry.container_spec()
    if any(name and name in text for name in (spec.db, spec.auth, spec.world)):
        return Identification.MATCHES
    return Identification.DIFFERENT


_CAMPAIGN_GLYPHS = {
    "wow-wotlk": "❄️",
    "wow-unbound": "🌀",
    "wow-tbc": "🔥",
    "wow-vanilla": "⚔️",
    "wow-tortoise": "🐢",
    "wow-centurion": "🛡️",
}

_CAMPAIGN_SUBTITLES = {
    "wow-wotlk": "Wrath of the Lich King",
    "wow-unbound": "Wrath of the Lich King, multi-class",
    "wow-tbc": "The Burning Crusade",
    "wow-vanilla": "Classic Vanilla",
    "wow-tortoise": "Turtle WoW Solo",
    "wow-centurion": "Level-60 PvP with bots",
}
"""The tile's second line; the client version in brackets comes from the entry (T194 C18).

Typed here, the Tortoise tile said 1.17.2 for a server whose client must be 1.18.1.
"""

_SERVER_SOFTWARE = {
    "wow-wotlk": "AzerothCore with mod-playerbots",
    "wow-unbound": "AzerothCore with mod-playerbots and Wrath Unbound",
    "wow-tbc": "CMaNGOS TBC with playerbots",
    "wow-vanilla": "CMaNGOS Classic with playerbots",
    "wow-tortoise": "the Tortoise WoW core with TortoiseBots",
    "wow-centurion": "Centurion, a TrinityCore with its own playerbots",
}
"""What a tile's tooltip names as its server software (T194 F8).

In words, not the entry's emulator name: that carries repositories and a
branch ("tortoise-wow/tortoise-wow @ 1181dev"), which a player has no use for.
"""


def _server_software(entry: CatalogEntry) -> str:
    """The tile's "Server software:" words; an entry not listed gets its name without a branch."""
    return _SERVER_SOFTWARE.get(entry.id) or re.sub(r"\s*@\s*\S+", "", entry.emulator.name)


RECOMMENDED = "wow-wotlk"
"""The one tile marked "Recommended" (T195 C23): where a first-time player should start."""

WELCOME = (
    "Yu'lon downloads and builds the server for you. Nothing changes on this "
    "computer until you press Install."
)
"""The line under the Catalog heading (T195 C23)."""


class CatalogView(QWidget):
    """One tile per catalog entry; Install streams the Phase 3a installer into `log_panel`."""

    install_started = Signal(str)  # game id
    install_finished = Signal(str, bool, str)  # game id, ok, message
    installed = Signal(str, object, object)  # game id, server_dir (Path), client_dir (Path|None)
    fresh_install = Signal(str, object, object)
    """The same three, emitted after `installed` ONLY from `_on_run_finished()`.

    `installed` also fires from "Use existing...", which pointed the app at a
    folder and wrote nothing; a window slot that writes anything (the T87
    post-install channel settle mints an account) hangs off this one, so a
    folder the user merely pointed at never gets a row written into its auth
    database (review, 2026-09-18).
    """
    adopted = Signal(str, object, object, object)
    """A server adopted from a WSL distro: game id, server_dir, client_dir, distro name.

    Separate from `installed` rather than a fourth argument on it, because every
    existing emitter and receiver of that signal means "there is no distro" and
    widening it would make all of them say so explicitly for no benefit.
    """

    def __init__(
        self,
        catalog: Catalog,
        installer_factory: InstallerFactory,
        log_panel: LogPanel,
        *,
        pick_dir: DirPicker = _qt_dir_picker,
        ask_suggestion: SuggestionAsker = _qt_suggestion_asker,
        installed_games: Mapping[str, Path] | None = None,
        home: Path | None = None,
        platform_id: Callable[[], str] = platform.detect,
        pick_wsl_server: WslServerPicker = _qt_wsl_server_picker,
        wsl_distros: Callable[[], tuple[str, ...]] = platform.wsl_distros,
        dir_problem: Callable[[Path], str | None] = platform.server_dir_problem,
        move_in: object | None = None,
        pick_package: PackagePicker = _qt_package_picker,
        ask_yes: YesNo = ask_yes_no,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        # T601 level 2: `ui.move_in.MoveIn` (a plan for a file, the engine for a plan), or None
        # to draw no "Bring from another computer…" press. Typed loosely here so this view
        # does not import the move engine at module scope.
        self._move_in = move_in
        self._pick_package = pick_package
        self._ask_yes = ask_yes
        self._move_buttons: dict[str, QPushButton] = {}
        self._platform_id = platform_id
        self._dir_problem = dir_problem
        self._catalog = catalog
        self._make_installer = installer_factory
        self._log = log_panel
        self._pick_dir = pick_dir
        self._ask_suggestion = ask_suggestion
        self._pick_wsl_server = pick_wsl_server
        self._wsl_distros = wsl_distros
        self._adopt_buttons: dict[str, QPushButton] = {}
        self._home = home if home is not None else Path.home()
        self._buttons: dict[str, QPushButton] = {}
        self._gated: set[str] = set()  # ids the platform gate disabled (roadmap 6.1)
        self._existing_buttons: dict[str, QPushButton] = {}
        self._installed_dirs: dict[str, Path] = dict(installed_games or {})
        self._current: tuple[str, Path, Path | None] | None = None
        self._prompter: InputPrompter | None = None

        header_row = QHBoxLayout()
        header_label = QLabel("Choose a game to install", self)
        header_label.setObjectName("section-title")
        header_row.addWidget(header_label)
        header_row.addStretch(1)

        # Hidden until the window shows the install log for the first time
        # (`main.build_catalog_tab`): before any install there is no log to show.
        self.toggle_console_button = QPushButton("▼ Hide install log", self)
        self.toggle_console_button.setObjectName("toggle-console-btn")
        self.toggle_console_button.setIcon(dadcraft_icon("console", COLOR_TEXT_GOLD, 14))
        self.toggle_console_button.setVisible(False)
        header_row.addWidget(self.toggle_console_button)

        welcome = QLabel(WELCOME, self)
        welcome.setObjectName("catalog-welcome")
        welcome.setWordWrap(True)

        grid = QGridLayout()
        grid.setSpacing(12)
        grid.setContentsMargins(0, 4, 0, 4)
        # Two tiles per row, wrapping into further rows. A single row of four
        # demanded 4x235 + spacing ≈ 980px, well past the 420px the splitter is
        # free to give this pane, so a horizontal scrollbar appeared on every
        # narrow window. Two-up drops that floor to ~480px, keeps each tile wide
        # enough for its buttons, and lets the grid grow downward, where the
        # vertical scrollbar belongs.
        columns = 2
        # Top-aligned, with the spare height in an empty row under the last
        # one: a tile is as tall as its own text at any width, never stretched
        # to its row neighbour or to the shelf (T195 C24).
        for index, entry in enumerate(catalog.games):
            grid.addWidget(
                self._tile(entry), index // columns, index % columns, Qt.AlignmentFlag.AlignTop
            )
        for col in range(columns):
            grid.setColumnStretch(col, 1)
        grid.setRowStretch((len(catalog.games) + columns - 1) // columns, 1)

        inner = QWidget()
        inner.setLayout(grid)
        scroll = QScrollArea(self)
        scroll.setObjectName("catalog-shelf-scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(inner)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addLayout(header_row)
        layout.addWidget(welcome)
        layout.addWidget(scroll, 1)
        self._log.run_finished.connect(self._on_run_finished)

    # -- tiles ----------------------------------------------------------

    @staticmethod
    def _tile_text(text: str, frame: QFrame, role: str = "") -> QLabel:
        """A line of tile text that gives way instead of widening the tile.

        A QLabel without `setWordWrap` demands its longest line, so every tile
        was as wide as its worst sentence — WotLK's description is 118
        characters, Vanilla's emulator line 66 — and two of those per grid row
        asked for some 3200px where the Catalog tab has under 700. The second
        column, Install buttons and all, was drawn past the right edge of the
        viewport at the size the window opens at: reachable only by a horizontal
        scrollbar most people never looked for, and clickable blind (v0.6.51,
        2026-08-30). Wrapping is what the platform-gate note twenty lines below
        already did; the rest of the tile simply never got it.

        Every line goes through here rather than only the long ones, because
        which line is longest is a property of `catalog.json` — the next entry
        someone adds must not be able to push the buttons off screen again.

        `role` names an objectName the theme styles (`tile-title`, `tile-desc`,
        `tile-meta`, `tile-warning`) so the tile reads as a hierarchy — a bold
        gold heading, a legible body line, and small muted metadata — instead
        of four identical-looking lines.
        """
        label = QLabel(text, frame)
        label.setWordWrap(True)
        if role:
            label.setObjectName(role)
        return label

    def _tile(self, entry: CatalogEntry) -> QFrame:
        frame = DadcraftCampaignCard(entry.id, self)
        frame.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        frame.customContextMenuRequested.connect(
            lambda pos, e=entry, f=frame: self._show_tile_context_menu(pos, e, f)
        )
        # As tall as its own text (T195 C24). The card fixes itself at 370 px,
        # which made a one-line description a tall, mostly empty tile.
        frame.setMinimumHeight(0)
        frame.setMaximumHeight(QWIDGETSIZE_MAX)
        # The emulator is for the curious, so it is on hover (T195 C23).
        frame.setToolTip(f"Server software: {_server_software(entry)}")
        box = QVBoxLayout(frame)
        box.setSpacing(6)
        box.setContentsMargins(14, 14, 14, 14)

        header_box = QHBoxLayout()
        glyph = QLabel(_CAMPAIGN_GLYPHS.get(entry.id, "⚔️"), frame)
        glyph.setObjectName("tile-glyph")
        header_box.addWidget(glyph)

        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        title_col.addWidget(
            self._tile_text(
                f"<b>{entry.name}</b>",
                frame,
                role="tile-title",
            )
        )
        sub_title = QLabel(
            f"{_CAMPAIGN_SUBTITLES.get(entry.id, entry.name)} ({entry.client.version})", frame
        )
        sub_title.setObjectName("tile-subtitle")
        title_col.addWidget(sub_title)
        header_box.addLayout(title_col, 1)
        box.addLayout(header_box)

        desc_scroll = QScrollArea(frame)
        desc_scroll.setObjectName("tile-desc-box")
        desc_scroll.setWidgetResizable(True)
        desc_scroll.setFrameShape(QFrame.Shape.NoFrame)
        desc_label = self._tile_text(entry.description, desc_scroll, role="tile-desc")
        desc_scroll.setWidget(desc_label)
        # No stretch: a one-line description made a ~370 px tile (T195 C24).
        box.addWidget(desc_scroll)

        meta_box = QHBoxLayout()
        meta_box.setSpacing(6)
        meta_box.addWidget(
            self._tile_text(
                f"Client {entry.client.version} (b{entry.client.build})",
                frame,
                role="tile-meta",
            )
        )
        if entry.id == RECOMMENDED:
            meta_box.addWidget(self._tile_text("Recommended", frame, role="tile-meta"))
        meta_box.addStretch(1)
        box.addLayout(meta_box)

        button = QPushButton("Install", frame)
        button.setIcon(dadcraft_icon("download", "#FFF1A8", 16))
        button.setObjectName(f"install-{entry.id}")
        button.setProperty("primary", True)
        button.setToolTip(f"Install {entry.name} server into your chosen directory.")
        button.clicked.connect(lambda _checked=False, e=entry: self.start_install(e))
        box.addWidget(button)
        self._buttons[entry.id] = button
        if not entry.install.supports(self._platform_id()):
            # Roadmap 6.1: say it on the tile, before the click — and leave
            # "Use existing…" enabled, since managing a server installed
            # elsewhere works on every platform.
            box.addWidget(
                self._tile_text(
                    f"Installer needs {platform_names(entry.install.platforms)} — "
                    "not available on this platform yet.",
                    frame,
                    role="tile-warning",
                )
            )
            button.setEnabled(False)
            button.setToolTip(unsupported_platform_message(entry, self._platform_id()))
            self._gated.add(entry.id)
        self._show_installed(entry.id)
        existing = QPushButton("Use existing…", frame)
        existing.setIcon(dadcraft_icon("folder", COLOR_TEXT_GOLD, 14))
        existing.setObjectName(f"existing-{entry.id}")
        existing.setToolTip(
            "Manage a server that is already installed (by a script, or before this app)."
        )
        existing.clicked.connect(lambda _checked=False, e=entry: self.attach_existing(e))
        box.addWidget(existing)
        self._existing_buttons[entry.id] = existing
        if self._move_in is not None:
            bring = QPushButton(BRING_FROM_ANOTHER, frame)
            bring.setIcon(dadcraft_icon("download", COLOR_TEXT_GOLD, 14))
            bring.setObjectName(f"move-in-{entry.id}")
            bring.setToolTip(
                f"Build a {entry.name} server from a file packed on another computer with Pack "
                "the whole server…: the same version, modules, settings, world and characters."
            )
            bring.clicked.connect(
                lambda _checked=False, e=entry: self.bring_from_another_computer(e)
            )
            box.addWidget(bring)
            self._move_buttons[entry.id] = bring
            if entry.id in self._gated or entry.id in self._installed_dirs:
                bring.setEnabled(False)

        # Only where a WSL-resident server can exist. On Linux, macOS, and on a
        # Windows box with no distros, this button would be an offer the machine
        # cannot honour - and `wsl_distros()` answers () for all of them, so the
        # one check covers every case.
        if self._wsl_distros():
            adopt = QPushButton("Find in WSL…", frame)
            adopt.setIcon(dadcraft_icon("network", COLOR_TEXT_GOLD, 14))
            adopt.setObjectName(f"adopt-wsl-{entry.id}")
            adopt.setToolTip(
                "Adopt a server that lives inside a WSL distro — for example one the "
                "DML Launcher built. Yu'lon manages it where it is; nothing is moved "
                "or reinstalled."
            )
            adopt.clicked.connect(lambda _checked=False, e=entry: self.adopt_from_wsl(e))
            box.addWidget(adopt)
            self._adopt_buttons[entry.id] = adopt
        return frame

    def _show_installed(self, game_id: str) -> None:
        """Say "Installed" and go grey on a tile whose server the app already knows.

        Owner-asked, 2026-09-04. An Install button that still looks pressable on
        a game that is already installed is an offer the app does not mean: the
        second press asks for a folder, and then either `_guard()` refuses the
        first install’s folder for being non-empty, or a SECOND server is built
        for the same game — which cannot run beside the first, because container
        names are global per game and `_refuse_foreign_containers` refuses
        exactly that. Both outcomes are a minutes-long detour to reach a no.

        The tooltip names the FOLDER rather than saying only "already
        installed", because two boxes of the same game on one machine is a real
        case here, and then the useful question is which one this tile is
        remembering.

        Called once per tile at build time and again at each of the three points
        that produce an install, so one function decides how an installed tile
        looks instead of four places that have to agree.
        """
        if game_id not in self._installed_dirs:
            return
        button = self._buttons[game_id]
        button.setText("Installed")
        button.setEnabled(False)
        # A second server of one game cannot run beside the first (container names are
        # global per game), so a whole server brought in is offered only where Install is.
        bring = getattr(self, "_move_buttons", {}).get(game_id)
        if bring is not None:
            bring.setEnabled(False)
        button.setToolTip(
            f"Already installed in {self._installed_dirs[game_id]} — its own tab manages it."
        )

    def _show_tile_context_menu(self, pos: QPoint, entry: CatalogEntry, frame: QWidget) -> None:
        menu = QMenu(self)
        if entry.id not in self._installed_dirs and entry.install.supports(self._platform_id()):
            install_action = menu.addAction("Install Server…")
            install_action.triggered.connect(lambda: self.start_install(entry))

        existing_action = menu.addAction("Use Existing Server…")
        existing_action.triggered.connect(lambda: self.attach_existing(entry))

        if self._wsl_distros():
            wsl_action = menu.addAction("Find in WSL…")
            wsl_action.triggered.connect(lambda: self.adopt_from_wsl(entry))

        menu.addSeparator()
        copy_action = menu.addAction("Copy Server Details")
        copy_action.triggered.connect(
            lambda: self._copy_text(
                f"{entry.name} ({entry.emulator.name}) - "
                f"Client: {entry.client.version} (build {entry.client.build})"
            )
        )
        menu.exec(frame.mapToGlobal(pos))

    @staticmethod
    def _copy_text(text: str) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)

    def _remember_installed(self, game_id: str, server_dir: Path) -> None:
        """Record an install this view just produced, and grey its button."""
        self._installed_dirs[game_id] = server_dir
        self._show_installed(game_id)

    def forget_installed(self, game_id: str, surviving: Mapping[str, Path]) -> None:
        """An install of `game_id` is gone (8.9a). Recompute this tile from what is left.

        The inverse of `_remember_installed()`, and the way OUT this view did
        not have: `_show_installed()` early-returns for a game that is not in
        `_installed_dirs`, so nothing could ever un-grey a tile.

        `surviving` is the whole `AppState.installed_dirs()` after the record was
        forgotten, and NOT a game id to delete a key for. That is the difference
        between right and nearly right here: `installed_dirs()` is one folder per
        GAME, last remembered wins, so a machine with two WotLK installs still
        has one after the first is purged - and its tab is still open. Flipping
        the tile back to "Install" there offers a third install of a game that
        already has two.

        The re-enable is the enable rule from `_set_buttons_enabled()` and not a
        bare `setEnabled(True)`: a tile the platform gate disabled (roadmap 6.1)
        must not become pressable because an install elsewhere was removed.
        """
        button = self._buttons.get(game_id)
        if button is None:
            return
        if game_id in surviving:
            self._installed_dirs[game_id] = surviving[game_id]
            self._show_installed(game_id)
            return
        self._installed_dirs.pop(game_id, None)
        button.setText("Install")
        button.setToolTip("")
        button.setEnabled(game_id not in self._gated)

    def button_for(self, game_id: str) -> QPushButton:
        """The Install button of a tile (tests / accessibility)."""
        return self._buttons[game_id]

    def existing_button_for(self, game_id: str) -> QPushButton:
        """The "Use existing…" button of a tile (tests / accessibility)."""
        return self._existing_buttons[game_id]

    # -- attach an install made elsewhere -------------------------------

    def attach_existing(self, entry: CatalogEntry) -> bool:
        """Register a server dir that already holds an install; False if not attached.

        Installs made by the CLI harness (`python -m yulon.install_wiring`), by
        an older build, or before the app was reinstalled never pass through
        `start_install()`, so this is how they get a controller tab. Two checks:
        a compose file in the chosen folder, under any of the names Compose
        itself accepts (`installer.COMPOSE_FILENAMES`), because the TBC and
        Vanilla scripts wrote `compose.yml` rather than `docker-compose.yml`
        and those installs are still on disk - nothing Yu'lon writes has that
        name since 7.2, when both families moved to `composegen`, whose
        `BASE_FILE` is `docker-compose.yml` - and the same folder rule the
        INSTALL path applies
        (`platform.server_dir_problem`, which `StagedInstaller.preflight()`
        asks before it provisions anything), because a folder Docker cannot
        mount is no more attachable than it is installable. Attaching never
        reaches an engine, which is why the rule is asked here by hand. That
        second check was missing until a tester attached a server living inside
        WSL (2026-08-26): the rule existed and simply was not wired to the
        button they pressed.
        """
        start = self._home / entry.install.default_server_dir
        former = FORMER_DEFAULT_DIRS.get(entry.id)
        if not start.is_dir() and former is not None and (self._home / former).is_dir():
            start = self._home / former
        server_dir = self._pick_dir(
            self, f"Select the folder where {entry.name} is installed", start
        )
        if server_dir is None:
            return False
        if (server_dir / MOVE_IN_FILE).exists():
            show_information(self, "A move that did not finish", _unfinished_move(server_dir))
            return False
        if compose_file(server_dir) is None:
            show_warning(
                self,
                "Not a server folder",
                f"{server_dir} has no compose file (compose.yml or docker-compose.yml) — "
                "pick the folder the installer created.",
            )
            return False
        # Asked second, so "there is no server here" beats "this folder would
        # not work anyway" - the first is the likelier mistake and the easier
        # one to act on.
        folder_problem = self._dir_problem(server_dir)
        if folder_problem is not None:
            show_warning(self, "That folder will not work", folder_problem)
            return False
        client_dir: Path | None = None
        if entry.install.requires_client_dir:
            client_dir = self._pick_dir(
                self,
                f"Select your {entry.client.version} client folder (the app never downloads one)",
                self._home,
            )
            if client_dir is None:
                return False
        logger.info(f"attaching existing {entry.id} install at {server_dir}")
        # Deliberately NOT pinned here. `pin_project_name()` writes whatever
        # compose calls the project *now*, which is the folder's current
        # basename — and an already-moved install is precisely what this path
        # exists to adopt. Pinning `azerothcore` onto containers compose created
        # under `wow-server` makes the mismatch permanent (a pin outranks the
        # basename, and it is never revised), so the server could never be
        # stopped from here again and a later start would build a fresh, empty
        # database volume beside the real one. Only `_on_run_finished()` may
        # pin: there the basename provably is what the containers were just
        # created under (review, 2026-08-22).
        self._remember_installed(entry.id, server_dir)
        self.installed.emit(entry.id, server_dir, client_dir)
        return True

    def adopt_from_wsl(self, entry: CatalogEntry) -> bool:
        """Adopt a server that lives inside a WSL distro. False if nothing was adopted.

        The migration path off the DML Launcher, which builds its servers inside
        a distro with Docker CE of their own. Yu'lon is replacing that launcher,
        and a multi-hour compile is not something to ask a user to repeat, so
        those servers are adopted rather than refused.

        Discovery asks docker what projects exist rather than scanning folders,
        so nothing here depends on the other product's layout - see
        `yulon.wsl.find_servers()`. Stopped distros are listed but not opened,
        because opening one starts it.
        """
        found = wsl.find_servers()
        if not found:
            show_information(
                self,
                "No servers found in WSL",
                "No Docker Compose projects were found in the running WSL distros.\n\n"
                "If the server is in a distro that is not running, start that distro "
                "first — Yu'lon does not start them itself, because doing so as a side "
                "effect of looking around is not its call to make.",
            )
            return False

        chosen = self._pick_wsl_server(found)
        if chosen is None:
            return False

        identified = _identify(entry, chosen.server_dir)
        if identified is Identification.DIFFERENT:
            show_warning(
                self,
                "That is a different server",
                f"{chosen.project} in {chosen.distro} does not look like a "
                f"{entry.name} install — its compose file names none of the containers "
                f"{entry.name} uses.\n\n"
                "Adopting it here would give you a tab whose every button talks about "
                "containers that do not exist. Pick the entry that matches it, or a "
                "different server.",
            )
            return False
        if identified is Identification.UNVERIFIED:
            # ASK, do not announce. This used to be a `QMessageBox.warning` - a
            # notice with an OK button - and a notice cannot refuse. Three things
            # made that the wrong instrument, and the owner chose the confirm:
            #
            #   1. The user has never seen this folder. `_qt_wsl_server_picker`
            #      offers "{project}  -  {distro}  (running)"; `server_dir` appears
            #      for the FIRST time in this dialog. Charging the user for a fact
            #      they were never shown, with nowhere to say "that is not it".
            #   2. The click carried no information. `adopt_from_wsl` returned True
            #      whether the box was dismissed or not, so a user who clicked
            #      through produced exactly what a clean verification produced -
            #      the bool problem this function was fixed for, one layer up in
            #      the UI.
            #   3. The stated cost is FILE DELETION, and
            #      `test_an_unverified_adoption_reaches_the_applier_with_no_ownership_check`
            #      proves it reachable rather than asserting it.
            #
            # BEFORE the client-folder prompt, so a user who declines is not first
            # made to go and find their WoW install. The record of an unverified
            # adoption moves to just before the emit instead, where it describes
            # something that actually happened - which is what the ordering fix
            # this replaces was really about.
            # The cost sentence is CONDITIONAL, because the flat version was not
            # true. "Installing or removing a module writes into that folder and
            # deletes files under it" describes `Applier`, which only exists for an
            # entry with `has_manifests` - `wow-wotlk` alone of the four when this
            # was written, and `wow-tbc` as well since 8.7b. For Vanilla and
            # Tortoise `controller_view` still passes `applier=None` and DISABLES
            # both module buttons, so the user was being warned about an action
            # they cannot perform. It errs safe, which is exactly why it survived
            # review of the previous wording: a sentence spelled like a true one,
            # aimed at the cost this time instead of the remedy.
            # The condition is the reason this needed no edit when TBC gained
            # manifests: it asks the entry, not a list of game ids. What DID need
            # saying is that TBC's cost is smaller than the sentence implies -
            # every `manifests/wow-tbc/` item is a conf activation or a SQL mod,
            # so nothing there clones and nothing there is deleted. The sentence
            # over-warns rather than under-warns, which is the right way round,
            # and one TBC manifest with a `source` would make it exact again.
            cost = (
                "Installing or removing a module from its tab writes into that "
                "folder and deletes files under it."
                if entry.has_manifests
                else "Its tab will run docker commands against containers that "
                "may not be the ones in that folder."
            )
            if not said_yes(
                QMessageBox.question(
                    self,
                    "Adopt without checking?",
                    f"Yu'lon could not read a compose file in {chosen.server_dir}, so "
                    f"nothing confirms that {chosen.project} in {chosen.distro} is a "
                    f"{entry.name} install.\n\n"
                    f"Adopt it anyway? {cost} So say yes only if you are sure this "
                    "is the right folder.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
            ):
                # Default No, and anything that is not an explicit Yes refuses.
                #
                # `not said_yes(...)` and NOT `== No`, which is how most people would
                # write it. `QMessageBox.question` returns `NoButton` (0) when the
                # dialog is closed with the window chrome or Escape, and `NoButton !=
                # Yes` but also `NoButton != No`, so the `== No` spelling ADOPTS in
                # exactly the case this comment used to only declare. It survived the
                # whole suite when a reviewer mutated it on 2026-09-02;
                # `test_closing_the_unverified_confirm_without_answering_adopts_nothing`
                # is what makes the distinction fail now. `said_yes()` compares by
                # `==`, not `is` (T33: PySide6 6.11.2's static `question()` returns a
                # plain `int`, for which `is Yes` is always False).
                logger.info(
                    f"declined to adopt {entry.id} from {chosen.distro}: nothing "
                    f"confirmed {chosen.server_dir} is a {entry.name} install"
                )
                return False

        client_dir: Path | None = None
        if entry.install.requires_client_dir:
            # Still on the Windows side: the client is the user's own WoW
            # install and nothing about it moved into a distro.
            client_dir = self._pick_dir(
                self,
                f"Select your {entry.client.version} client folder (the app never downloads one)",
                self._home,
            )
            if client_dir is None:
                return False

        if identified is Identification.UNVERIFIED:
            # Deliberately here and not beside the question above: this line is a
            # record, and a record written before the client-folder prompt
            # described adoptions the user then cancelled. It now cannot be
            # written unless the adoption is one line from being emitted.
            logger.warning(
                f"adopting {entry.id} from {chosen.distro} UNVERIFIED: no readable compose "
                f"file in {chosen.server_dir}, so nothing confirms it is a {entry.name} "
                "install - the user was asked and said yes"
            )
        logger.info(
            f"adopting {entry.id} from WSL distro {chosen.distro}: "
            f"project {chosen.project} at {chosen.server_dir}"
        )
        self._remember_installed(entry.id, chosen.server_dir)
        self.adopted.emit(entry.id, chosen.server_dir, client_dir, chosen.distro)
        return True

    # -- install --------------------------------------------------------

    def start_install(self, entry: CatalogEntry) -> bool:
        """Ask for folders, then run the installer into the log panel. False if not started."""
        if self._log.running:
            show_information(self, "Busy", "Another job is still running.")
            return False
        if not entry.install.supports(self._platform_id()):
            # Before the folder prompts, not after them (roadmap 6.1): asking
            # where to install something that cannot be installed is the rudest
            # possible order.
            message = unsupported_platform_message(entry, self._platform_id())
            show_information(self, "Not available on this platform", message)
            self.install_finished.emit(entry.id, False, message)
            return False
        folders = self._ask_folders(entry)
        if folders is None:
            return False
        server_dir, client_dir = folders
        if (server_dir / MOVE_IN_FILE).exists():
            # T601 level 2: a plain install would finish the build and never put the modules,
            # settings or data in, and then the tile could no longer bring the server in.
            show_information(self, "A move that did not finish", _unfinished_move(server_dir))
            return False
        return self._run_install(entry, server_dir, client_dir, self._make_installer(entry))

    def _ask_folders(self, entry: CatalogEntry) -> tuple[Path, Path | None] | None:
        """The server folder (the suggestion, or a pick) and, where needed, the client folder."""
        # Offered BEFORE the picker, because the picker cannot offer it. The
        # suggestion does not exist yet on a first install and
        # `getExistingDirectory()` returns only folders that do, so the name
        # used to live in the dialog's title - unclickable, and truncated by the
        # window manager at 91 characters before it reached the name itself
        # (owner, Fedora 44, 2026-09-03). Here it is body text and one button.
        suggested = default_server_dir(entry, self._home)
        taken = self._ask_suggestion(self, entry.name, suggested)
        if taken is None:
            return None
        if taken:
            server_dir: Path | None = suggested
        else:
            server_dir = self._pick_dir(self, f"Where should {entry.name} be installed?", suggested)
        if server_dir is None:
            return None
        client_dir: Path | None = None
        if entry.install.requires_client_dir:
            client_dir = self._pick_dir(
                self,
                f"Select your {entry.client.version} client folder (the app never downloads one)",
                self._home,
            )
            if client_dir is None:
                return None
        return server_dir, client_dir

    def bring_from_another_computer(self, entry: CatalogEntry) -> bool:
        """Build a whole server from a move package (T601 level 2). False if not started.

        The file first, then the folders, then the plan (read without changing anything), then
        one question showing it, then the run in the log panel as an install, so the server is
        remembered when it ends exactly as an installed one is.
        """
        if self._move_in is None:
            return False
        if self._log.running:
            show_information(self, "Busy", "Another job is still running.")
            return False
        if not entry.install.supports(self._platform_id()):
            show_information(
                self,
                "Not available on this platform",
                unsupported_platform_message(entry, self._platform_id()),
            )
            return False
        path = self._pick_package(self, "Choose the file packed on the other computer", self._home)
        if path is None:
            return False
        folders = self._ask_folders(entry)
        if folders is None:
            return False
        server_dir, client_dir = folders
        plan = self._move_in.plan(path, server_dir)  # type: ignore[attr-defined]
        if not plan.allowed:
            show_information(self, "This server cannot be brought in", plan.text())
            return False
        if plan.entry is None or plan.entry.id != entry.id:
            name = plan.entry.name if plan.entry is not None else "another game"
            show_information(
                self,
                "Another game",
                f"This file holds a {name} server. Use the {name} tile's {BRING_FROM_ANOTHER}",
            )
            return False
        if not self._ask_yes(self, "Bring this server in?", plan.text()):
            return False
        installer = self._move_in.installer(plan)  # type: ignore[attr-defined]
        return self._run_install(
            entry,
            server_dir,
            client_dir,
            installer,
            title=f"Bringing {entry.name} from another computer",
        )

    def _run_install(
        self,
        entry: CatalogEntry,
        server_dir: Path,
        client_dir: Path | None,
        installer: InstallEngine,
        *,
        title: str | None = None,
    ) -> bool:
        options = InstallOptions(server_dir=server_dir, client_dir=client_dir)
        # No synchronous preflight here: `run()` re-preflights on the worker
        # thread, and preflight can mean full Docker provisioning — minutes of
        # work that used to freeze the window (review finding, 2026-08-21).
        # Failures surface through `_on_run_finished` as a dialog instead.
        cancel = threading.Event()
        self._current = (entry.id, server_dir, client_dir)
        self._set_buttons_enabled(False)
        # One prompter for this view, reused. It has to be held on `self` at all
        # — PySide6 keeps bound-method slots by weak reference, so a prompter
        # owned only by this frame would be collected and its dialog would never
        # appear — but building a NEW one per install parented to the view left
        # every previous one alive for the session, each still holding the
        # password it last carried. `ask()` clears the answer on the way out;
        # this stops the objects accumulating (review, 2026-08-22).
        if self._prompter is None:
            self._prompter = InputPrompter(self)
        prompter = self._prompter
        prompter.bind_cancel(cancel)
        started = self._log.run(
            lambda: installer.run(options, cancel=cancel, ask=prompter.ask),
            title=title or f"Installing {entry.name}",
            cancel=cancel,
            record_as=f"install-{entry.id}",
        )
        if started:
            self.install_started.emit(entry.id)
        else:
            self._current = None
            self._set_buttons_enabled(True)
        return started

    def _on_run_finished(self, ok: bool, message: str) -> None:
        if self._current is None:
            return  # a job this view did not start
        game_id, server_dir, client_dir = self._current
        self._current = None
        self._set_buttons_enabled(True)
        if self._log.cancelled and not ok:
            # T228 cold review: Stop was pressed and the job still FAILED, with a
            # sentence about what it left (`TrueAfterStop`, a kept build). The
            # header reads "Stopped. FAILED: ..."; the popup says exactly that,
            # rather than the plain-cancel copy beside it. Not remembered either.
            said = self._log.status_text()
            logger.info(f"install of {game_id} was stopped and left something: {message}")
            show_warning(self, INSTALL_STOPPED_TITLE, said)
            self.install_finished.emit(game_id, False, said)
            return
        stopped_by = self._log.stopped_by
        if (
            self._log.cancelled
            and stopped_by is not None
            and issubclass(stopped_by, ReadyWaitStopped)
        ):
            # T247 review: stopped in its ready wait, the install has left a server
            # running and starting, with no tab yet. The popup says what the log
            # says -- press Install again on the same folder, which resumes at the
            # wait and then remembers it -- not the generic cancel copy.
            note = ready_wait_stopped_message(self._catalog.get(game_id), stopped_by)
            logger.info(f"install of {game_id} was stopped in its ready wait; not remembered yet")
            show_information(self, INSTALL_STOPPED_TITLE, note)
            self.install_finished.emit(game_id, False, note)
            return
        if self._log.cancelled:
            # A cancelled install reaches here as a SUCCESS: `runner.interact()`
            # returns rather than raising when its cancel event is set, so the
            # generator ends normally and `ok` is True. Driven through this very
            # button against the real script and stopped during the source clone,
            # that pinned a compose project name into a half-cloned folder and
            # emitted `installed`, which `main.py` writes into `state.json` and
            # turns into a permanent tab — an install the user had explicitly
            # cancelled, and on a run stopped earlier still, a directory that did
            # not exist at all (install gate, 2026-08-23).
            note = cancelled_install_message(self._catalog.get(game_id), server_dir)
            logger.info(f"install of {game_id} was cancelled; nothing remembered")
            show_information(self, "Install cancelled", note)
            self.install_finished.emit(game_id, False, note)
            return
        if ok and compose_file(server_dir) is None:
            # A clean exit is not proof of an install. Until 7.2 the bash
            # installer exited 0 for "Keeping existing install — exiting.",
            # which is what a user got by pressing Install a second time on a
            # folder the previous attempt left behind; that pinned a compose
            # project name into a half-cloned folder and grew a permanent tab
            # for a server that was never built. The engine has no such path,
            # but the check stays because it is the one `attach_existing()`
            # makes, deliberately: the compose file is the single thing every
            # install of every game has, and the pin below is the part with
            # teeth — `docker.py` records that an install-time pin is inherited
            # by any copy of the folder, so Stop in the copy can stop the
            # original's server (review, 2026-08-23).
            ok = False
            message = (
                f"The installer exited without error, but {server_dir} has no "
                "compose file (compose.yml or docker-compose.yml) — so there is nothing "
                "installed there to remember. "
                "Look in the folder, then install again or pick a different one."
            )
            logger.info(f"{game_id} exited 0 with no compose file in {server_dir}; not remembered")
        if not ok and not self._offer_a_restart_instead(message):
            show_warning(self, "Install failed", message)
        self.install_finished.emit(game_id, ok, message)
        if ok:
            _pin_compose_project(server_dir)
            # AFTER `_set_buttons_enabled(True)` ran above: that pass re-enables
            # every ungated tile, so greying this one has to come second or the
            # unlock undoes it. Asserted, not just stated.
            self._remember_installed(game_id, server_dir)
            self.installed.emit(game_id, server_dir, client_dir)
            self.fresh_install.emit(game_id, server_dir, client_dir)

    def _offer_a_restart_instead(self, message: str) -> bool:
        """Offer to restart when a restart is now the whole of what is missing.

        The install that just failed is the one that ASKED for the docker group
        and got it: `usermod` ran, and the very process that ran it cannot see
        the result, because supplementary groups are credentials fixed at login.
        Before this, that user was told to log out and back in — measured on the
        live Ubuntu gate as: consent, sudo, log out, log back in, find the
        launcher, press Install again.

        Asks the MACHINE whether a restart would help, rather than remembering
        that a join happened. `docker_group_reexec()` returns an argv only when
        the group is in the database and not in this process — which is the
        condition, stated exactly, and it is right on the three paths a flag
        gets wrong: the user declined, the join failed, or the group was
        revoked. Each of those leaves the group out of the database, so the
        predicate is false and no restart is offered.

        It does NOT carry a fourth path. The caller runs this on EVERY failed
        install and `docker_group_reexec()` asks nothing about what the failure
        was, so a user who is in the database group but not in this process is
        offered a restart for a download that 404'd or a disk that filled up
        just as readily as for a docker-socket denial. A separate fix made the
        predicate return None under root, which removed the instance that was
        permanently true for every `sudo yulon` install; the general case is
        still live, and the cost of it is one dialog offering a restart that
        will not help. Whenever the predicate says None this returns False and
        the plain warning is shown, unchanged.

        Returns True when it has spoken to the user, so the caller does not also
        show its own dialog: the question below already carries `message` in
        full. `said_yes(...)` and not `not said_yes(...)` inverted, because
        Escape and the window's close button both return `NoButton`, and only
        an explicit Yes may throw away a running application.
        """
        return offer_a_docker_group_restart(self, message, failed_title="Install failed")

    def _set_buttons_enabled(self, enabled: bool) -> None:
        """Lock the tiles while a job runs, and unlock them when it ends.

        Unlocking must never re-enable an Install button the platform gate
        disabled (roadmap 6.1) — the tile's own note says it cannot be installed
        here — nor one that already reads "Installed" (owner, 2026-09-04). Same
        shape of bug in both: those are standing facts about the TILE, and this
        function knows only whether a job is running. The gate half was latent
        while every catalog entry was Linux-only and armed the moment 6.2
        widened WotLK; the installed half is armed immediately, because
        `_on_run_finished` calls this and then greys the tile that just
        installed. "Use existing…" is deliberately outside both rules: managing
        a server someone else installed works on every platform, and pointing
        the app at a second copy of a known game is not an install.
        """
        for game_id, button in self._buttons.items():
            button.setEnabled(
                enabled and game_id not in self._gated and game_id not in self._installed_dirs
            )
        for button in self._existing_buttons.values():
            button.setEnabled(enabled)


def offer_a_docker_group_restart(parent: QWidget, message: str, *, failed_title: str) -> bool:
    """`CatalogView._offer_a_restart_instead()`'s offer, for any view that needs it (T160).

    Moved out of the method, unchanged, so the Server tab's SteamOS Docker
    repair makes the same offer rather than a second copy of it: the restart
    has to hand the single-instance lock over around its exec (T152), and a
    copy that called `restart_under_docker_group()` bare would restart into an
    app that finds its own lock held and refuses to open. `failed_title`
    heads the warning shown when the exec itself fails. See the method for
    when this is offered and why it returns True.
    """
    # Asked ONCE, here, and the answer is what the restart execs (T152). It
    # can run `id -nG` for up to five seconds, and the restart gives the
    # single-instance lock up around its exec: asked again in there, those
    # seconds were a window in which another launch could take the lock.
    argv = platform.docker_group_reexec()
    if argv is None:
        return False
    if said_yes(
        QMessageBox.question(
            parent,
            "Restart Yu'lon to finish setting up Docker",
            f"{message}\n\n"
            "Docker is set up and your account has been given access to it. "
            "Yu'lon just needs to start again to pick that up — you do NOT "
            "need to log out.\n\n"
            "Restart Yu'lon now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
    ):
        # Only returns if the exec failed, and then the user is told what
        # actually went wrong rather than being left looking at a dialog
        # that closed and did nothing. The single-instance lock is handed
        # over around it: the exec keeps this PID, and the restarted app
        # would otherwise find its own lock held and refuse to open (T152).
        with single_instance.handed_over() as handover:
            platform.restart_under_docker_group(reexec=lambda: argv)
        if handover.lost:
            # Another Yu'lon took the lock in the moment it was free. Two
            # copies running is what the lock exists to prevent, so this
            # one goes, rather than carrying on unguarded beside it - and
            # it goes FIRST, before anything that waits on the player: a
            # modal box here kept this window and its jobs running beside
            # the winner until someone pressed OK (review, round 3).
            logger.warning(f"docker-group restart failed and the lock was lost: {message}")
            window = parent.window()
            # T540: `yulon_quit`, not `close()`: with the tray up, a close is a
            # hide, and this copy must GO. A window without one (no tray, a
            # test's) closes as it always did.
            # `yulon_quit_for_good` (cold review): an import that refuses this
            # quit now does not let the next close hide the copy into the tray.
            quit_for_real = getattr(
                window, "yulon_quit_for_good", getattr(window, "yulon_quit", window.close)
            )
            if not quit_for_real():
                # Refused, because something that cannot be stopped is
                # running (the database import): force-quitting would
                # leave it half-written, so the window stays until it ends,
                # and says why without blocking the work it is waiting for.
                box = FittedMessageBox(
                    QMessageBox.Icon.Warning,
                    single_instance.LOST_TITLE,
                    f"{message}\n\n{single_instance.LOST_TEXT}",
                    QMessageBox.StandardButton.Ok,
                    window,
                )
                box.setWindowModality(Qt.WindowModality.NonModal)
                box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
                box.show()
            return True
        show_warning(parent, failed_title, message)
    return True
