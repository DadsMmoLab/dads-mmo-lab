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
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from yulon import docker, platform
from yulon.catalog.catalog import Catalog, CatalogEntry
from yulon.catalog.installer import (
    Installer,
    InstallOptions,
    cancelled_install_message,
    platform_names,
    unsupported_platform_message,
)
from yulon.log import get_logger
from yulon.ui.widgets.log_panel import LogPanel
from yulon.ui.widgets.prompt import InputPrompter

logger = get_logger(__name__)

InstallerFactory = Callable[[CatalogEntry], Installer]
DirPicker = Callable[[QWidget, str, Path | None], Path | None]


def _qt_dir_picker(parent: QWidget, title: str, start: Path | None) -> Path | None:
    chosen = QFileDialog.getExistingDirectory(parent, title, str(start) if start else "")
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


class CatalogView(QWidget):
    """One tile per catalog entry; Install streams the Phase 3a installer into `log_panel`."""

    install_started = Signal(str)  # game id
    install_finished = Signal(str, bool, str)  # game id, ok, message
    installed = Signal(str, object, object)  # game id, server_dir (Path), client_dir (Path|None)

    def __init__(
        self,
        catalog: Catalog,
        installer_factory: InstallerFactory,
        log_panel: LogPanel,
        *,
        pick_dir: DirPicker = _qt_dir_picker,
        home: Path | None = None,
        platform_id: Callable[[], str] = platform.detect,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._platform_id = platform_id
        self._catalog = catalog
        self._make_installer = installer_factory
        self._log = log_panel
        self._pick_dir = pick_dir
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

    def _tile(self, entry: CatalogEntry) -> QFrame:
        frame = QFrame(self)
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        box = QVBoxLayout(frame)
        title = QLabel(f"<b>{entry.name}</b> <i>({entry.status})</i>", frame)
        box.addWidget(title)
        box.addWidget(QLabel(entry.description, frame))
        box.addWidget(QLabel(f"Client: {entry.client.version} (build {entry.client.build})", frame))
        box.addWidget(QLabel(f"Emulator: {entry.emulator.name}", frame))
        button = QPushButton("Install", frame)
        button.setObjectName(f"install-{entry.id}")
        button.clicked.connect(lambda _checked=False, e=entry: self.start_install(e))
        box.addWidget(button)
        self._buttons[entry.id] = button
        if not entry.install.supports(self._platform_id()):
            # Roadmap 6.1: say it on the tile, before the click — and leave
            # "Use existing…" enabled, since managing a server installed
            # elsewhere works on every platform.
            note = QLabel(
                f"<i>Installer needs {platform_names(entry.install.platforms)} — "
                "not available on this platform yet.</i>",
                frame,
            )
            note.setWordWrap(True)
            box.addWidget(note)
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
        they get a controller tab. The only check is the one thing every install
        has: a `docker-compose.yml` in the chosen folder.
        """
        server_dir = self._pick_dir(
            self,
            f"Select the folder where {entry.name} is installed",
            self._home / entry.install.default_server_dir,
        )
        if server_dir is None:
            return False
        if not (server_dir / "docker-compose.yml").is_file():
            QMessageBox.warning(
                self,
                "Not a server folder",
                f"{server_dir} has no docker-compose.yml — pick the folder the installer created.",
            )
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
            f"Where should {entry.name} be installed?",
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
        if ok and not (server_dir / "docker-compose.yml").is_file():
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
                "docker-compose.yml — so there is nothing installed there to remember. "
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
