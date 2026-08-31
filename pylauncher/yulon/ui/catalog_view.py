"""Catalog view — the browsable "store" of installable servers (roadmap 4.2).

Renders `catalog.json` as one tile per game with an Install button. The view
delegates: clicking Install asks the user for the server folder (and, where
the game needs it, their own client folder — README §3a), builds an
`Installer` through the factory it was given, and streams `installer.run()`
into the `LogPanel`. No Docker, no subprocess, no business logic here
(style-guide §3); results go up as signals (§5).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from yulon import docker, platform, wsl
from yulon.catalog.catalog import Catalog, CatalogEntry
from yulon.catalog.installer import (
    InstallEngine,
    InstallOptions,
    cancelled_install_message,
    compose_file,
    platform_names,
    unsupported_platform_message,
)
from yulon.log import get_logger
from yulon.ui.widgets.log_panel import LogPanel
from yulon.ui.widgets.prompt import InputPrompter

logger = get_logger(__name__)

InstallerFactory = Callable[[CatalogEntry], InstallEngine]
"""What builds the engine for one entry.

The Protocol rather than the `Installer` class since roadmap 6.2: an entry may
now be installed by the bash script or by the native engine, `installer_for()`
decides which from `catalog.json` data, and this view is deliberately not told
what it got — the two have the same `run()`.
"""
DirPicker = Callable[[QWidget, str, Path | None], Path | None]


def _existing_ancestor(start: Path | None) -> Path | None:
    """The nearest directory at or above `start` that actually exists.

    `getExistingDirectory()` opens on a path that does not exist by showing its
    PARENT with the missing name typed into the field - where `Choose` is
    disabled, because the name names nothing, and Enter answers "Directory not
    found. Please verify the correct directory name was given."

    That is a dead end on the first install, and it is the one every new user
    meets: the suggestion is `~/wow-server-playerbots`, which by definition does
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
    opens_at = _existing_ancestor(start)
    chosen = QFileDialog.getExistingDirectory(parent, title, str(opens_at) if opens_at else "")
    return Path(chosen) if chosen else None


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
        f"{server.project}  —  {server.distro}" f"  ({'running' if server.running else 'stopped'})"
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


def _looks_like(entry: CatalogEntry, server_dir: Path) -> bool:
    """Does the compose file in `server_dir` name any container this game uses?

    Discovery finds compose PROJECTS, not WoW servers - `docker compose ls`
    reports a TBC install, a Nextcloud and someone's blog with equal enthusiasm.
    Adopting one under the wrong catalog entry produces a tab whose every button
    names containers that do not exist, failing separately and confusingly
    instead of once and clearly.

    The catalog's container names are the evidence; the project name is a folder
    name and proves nothing.

    True when the file cannot be read at all. The folder lives inside a distro
    and is reached over a UNC path, and that read can fail for reasons unrelated
    to which game it is - refusing on "I could not check" would block the
    migration this feature exists to provide.
    """
    compose = compose_file(server_dir)
    if compose is None:
        return True
    try:
        text = compose.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return True
    spec = entry.container_spec()
    return any(name and name in text for name in (spec.db, spec.auth, spec.world))


class CatalogView(QWidget):
    """One tile per catalog entry; Install streams the Phase 3a installer into `log_panel`."""

    install_started = Signal(str)  # game id
    install_finished = Signal(str, bool, str)  # game id, ok, message
    installed = Signal(str, object, object)  # game id, server_dir (Path), client_dir (Path|None)
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
        home: Path | None = None,
        platform_id: Callable[[], str] = platform.detect,
        pick_wsl_server: WslServerPicker = _qt_wsl_server_picker,
        wsl_distros: Callable[[], tuple[str, ...]] = platform.wsl_distros,
        dir_problem: Callable[[Path], str | None] = platform.server_dir_problem,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._platform_id = platform_id
        self._dir_problem = dir_problem
        self._catalog = catalog
        self._make_installer = installer_factory
        self._log = log_panel
        self._pick_dir = pick_dir
        self._pick_wsl_server = pick_wsl_server
        self._wsl_distros = wsl_distros
        self._adopt_buttons: dict[str, QPushButton] = {}
        self._home = home if home is not None else Path.home()
        self._buttons: dict[str, QPushButton] = {}
        self._gated: set[str] = set()  # ids the platform gate disabled (roadmap 6.1)
        self._existing_buttons: dict[str, QPushButton] = {}
        self._current: tuple[str, Path, Path | None] | None = None
        self._prompter: InputPrompter | None = None

        grid = QGridLayout()
        for index, entry in enumerate(catalog.games):
            grid.addWidget(self._tile(entry), index // 2, index % 2)
        inner = QWidget()
        inner.setLayout(grid)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        layout = QVBoxLayout(self)
        layout.addWidget(scroll, 1)
        self._log.run_finished.connect(self._on_run_finished)

    # -- tiles ----------------------------------------------------------

    @staticmethod
    def _tile_text(text: str, frame: QFrame) -> QLabel:
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
        """
        label = QLabel(text, frame)
        label.setWordWrap(True)
        return label

    def _tile(self, entry: CatalogEntry) -> QFrame:
        frame = QFrame(self)
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        box = QVBoxLayout(frame)
        box.addWidget(self._tile_text(f"<b>{entry.name}</b> <i>({entry.status})</i>", frame))
        box.addWidget(self._tile_text(entry.description, frame))
        box.addWidget(
            self._tile_text(f"Client: {entry.client.version} (build {entry.client.build})", frame)
        )
        box.addWidget(self._tile_text(f"Emulator: {entry.emulator.name}", frame))
        button = QPushButton("Install", frame)
        button.setObjectName(f"install-{entry.id}")
        button.clicked.connect(lambda _checked=False, e=entry: self.start_install(e))
        box.addWidget(button)
        self._buttons[entry.id] = button
        if not entry.install.supports(self._platform_id()):
            # Roadmap 6.1: say it on the tile, before the click — and leave
            # "Use existing…" enabled, since managing a server installed
            # elsewhere works on every platform.
            box.addWidget(
                self._tile_text(
                    f"<i>Installer needs {platform_names(entry.install.platforms)} — "
                    "not available on this platform yet.</i>",
                    frame,
                )
            )
            button.setEnabled(False)
            button.setToolTip(unsupported_platform_message(entry, self._platform_id()))
            self._gated.add(entry.id)
        existing = QPushButton("Use existing…", frame)
        existing.setObjectName(f"existing-{entry.id}")
        existing.setToolTip(
            "Manage a server that is already installed (by a script, or before this app)."
        )
        existing.clicked.connect(lambda _checked=False, e=entry: self.attach_existing(e))
        box.addWidget(existing)
        self._existing_buttons[entry.id] = existing

        # Only where a WSL-resident server can exist. On Linux, macOS, and on a
        # Windows box with no distros, this button would be an offer the machine
        # cannot honour - and `wsl_distros()` answers () for all of them, so the
        # one check covers every case.
        if self._wsl_distros():
            adopt = QPushButton("Find in WSL…", frame)
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

    def button_for(self, game_id: str) -> QPushButton:
        """The Install button of a tile (tests / accessibility)."""
        return self._buttons[game_id]

    def existing_button_for(self, game_id: str) -> QPushButton:
        """The "Use existing…" button of a tile (tests / accessibility)."""
        return self._existing_buttons[game_id]

    # -- attach an install made elsewhere -------------------------------

    def attach_existing(self, entry: CatalogEntry) -> bool:
        """Register a server dir that already holds an install; False if not attached.

        Installs made by the shell scripts or the CLI harness (or before the app
        was reinstalled) never pass through `start_install()`, so this is how
        they get a controller tab. Two checks: a compose file in the chosen
        folder, under any of the names Compose itself accepts
        (`installer.COMPOSE_FILENAMES`) - the TBC and Vanilla scripts write
        `compose.yml`, not `docker-compose.yml` - and the same folder rule
        `start_install()` applies, because a folder Docker cannot mount is no
        more attachable than it is installable. That second one was missing
        until a tester attached a server living inside WSL (2026-08-26): the
        rule existed and simply was not wired to the button they pressed.
        """
        server_dir = self._pick_dir(
            self,
            f"Select the folder where {entry.name} is installed",
            self._home / entry.install.default_server_dir,
        )
        if server_dir is None:
            return False
        if compose_file(server_dir) is None:
            QMessageBox.warning(
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
            QMessageBox.warning(self, "That folder will not work", folder_problem)
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
            QMessageBox.information(
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

        if not _looks_like(entry, chosen.server_dir):
            QMessageBox.warning(
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

        logger.info(
            f"adopting {entry.id} from WSL distro {chosen.distro}: "
            f"project {chosen.project} at {chosen.server_dir}"
        )
        self.adopted.emit(entry.id, chosen.server_dir, client_dir, chosen.distro)
        return True

    # -- install --------------------------------------------------------

    def start_install(self, entry: CatalogEntry) -> bool:
        """Ask for folders, then run the installer into the log panel. False if not started."""
        if self._log.running:
            QMessageBox.information(self, "Busy", "Another job is still running.")
            return False
        if not entry.install.supports(self._platform_id()):
            # Before the folder prompts, not after them (roadmap 6.1): asking
            # where to install something that cannot be installed is the rudest
            # possible order.
            message = unsupported_platform_message(entry, self._platform_id())
            QMessageBox.information(self, "Not available on this platform", message)
            self.install_finished.emit(entry.id, False, message)
            return False
        server_dir = self._pick_dir(
            self,
            # The suggested name moved into the title when the picker stopped
            # opening on a path that does not exist - the dialog can no longer
            # pre-fill it, so this is where the user is told what to make.
            f"Where should {entry.name} be installed? "
            f"(suggested: a new folder called {entry.install.default_server_dir})",
            self._home / entry.install.default_server_dir,
        )
        if server_dir is None:
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
        options = InstallOptions(server_dir=server_dir, client_dir=client_dir)
        installer = self._make_installer(entry)
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
            title=f"Installing {entry.name}",
            cancel=cancel,
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
            note = cancelled_install_message(self._catalog.get(game_id).name, server_dir)
            logger.info(f"install of {game_id} was cancelled; nothing remembered")
            QMessageBox.information(self, "Install cancelled", note)
            self.install_finished.emit(game_id, False, note)
            return
        if ok and compose_file(server_dir) is None:
            # A clean exit is not proof of an install. The scripts exit 0 for
            # "Keeping existing install — exiting." too, which is what a user
            # gets by pressing Install a second time on a folder the previous
            # attempt left behind: `PROMPT_RULES` answers "n" to "Remove it and
            # start fresh?" because nothing in the GUI ever sets `reinstall`.
            # That used to pin a compose project name into a half-cloned folder
            # and grow a permanent tab for a server that was never built — and
            # the pin is the part with teeth, since `docker.py` records that an
            # install-time pin is inherited by any copy of the folder, so Stop
            # in the copy can stop the original's server. The check is the one
            # `attach_existing()` makes, deliberately: the compose file is the
            # single thing every install of every game has (review, 2026-08-23).
            ok = False
            message = (
                f"The installer exited without error, but {server_dir} has no "
                "compose file (compose.yml or docker-compose.yml) — so there is nothing "
                "installed there to remember. "
                "That is what the scripts do when they find an existing folder and are "
                "told not to replace it: delete the folder and install again, or pick a "
                "different one."
            )
            logger.info(f"{game_id} exited 0 with no compose file in {server_dir}; not remembered")
        if not ok:
            QMessageBox.warning(self, "Install failed", message)
        self.install_finished.emit(game_id, ok, message)
        if ok:
            _pin_compose_project(server_dir)
            self.installed.emit(game_id, server_dir, client_dir)

    def _set_buttons_enabled(self, enabled: bool) -> None:
        """Lock the tiles while a job runs, and unlock them when it ends.

        Unlocking must never re-enable an Install button the platform gate
        disabled (roadmap 6.1) — the tile's own note says it cannot be installed
        here. Latent while every catalog entry is Linux-only; armed the moment
        6.2 widens WotLK and leaves the other three. "Use existing…" is
        deliberately platform-independent: managing a server someone else
        installed works everywhere.
        """
        for game_id, button in self._buttons.items():
            button.setEnabled(enabled and game_id not in self._gated)
        for button in self._existing_buttons.values():
            button.setEnabled(enabled)
