"""The Server tab of a single server (`catalog.SingleEntry`), in either runtime.

One page, because a server of this kind is one process with no databases, modules,
characters or realm to give a tab of their own: its status, folder and ports; Start and
Stop; who can connect; Play with the player's own client; where to get help; a way to
take it off Yu'lon's list; and its live log. The controller is a
`single_controller.SingleServer`: one container (DDON) or host programs (DFO), and the
tab draws both the same way. A server that takes players on this computer only (no
`advertise` block) says so where the others offer a network.

Rendering and wiring only (style-guide §3): every Docker, file and process question goes
through the controller, `single_network`, `single_play` or `single_launcher`, on a
worker thread
(`ui/widgets/job.py`), and the answers come back to bound slots of this view
(contribution guide, Qt rule 2). The view never reaches into the window that holds it:
it says what happened with signals, and writes what it remembers through the callables
the window hands it.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from yulon import gaming_mode, platform, single_launcher, single_network, single_play
from yulon.catalog.catalog import SingleEntry
from yulon.log import get_logger
from yulon.play_launch import LaunchSpec
from yulon.single_controller import RUNNING, STARTING, STOPPED, SingleServer
from yulon.single_network import LOOPBACK, FirewallReport, Reach
from yulon.ui.folder_picker import pick_folder
from yulon.ui.help_places import HELP_BUTTON, HelpPlacesBox
from yulon.ui.icons import dadcraft_icon
from yulon.ui.message_box import show_information, show_warning
from yulon.ui.theme import COLOR_TEXT_GOLD
from yulon.ui.widgets.dadcraft_decorations import DadcraftRealmBadge
from yulon.ui.widgets.job import JobRunner, ThreadedJobRunner, threaded_job_runner
from yulon.ui.widgets.log_panel import LogPanel

logger = get_logger(__name__)

POLL_MS = 5000
"""How often the tab asks the controller how the server is."""

CONSOLE_ENDED = "the server stopped"
"""What the console says when the log follow ends because the server did."""

THIS_COMPUTER_ONLY = (
    "This server takes players on this computer only: its game runs on a loopback address "
    "(127.0.0.2) that no other computer can reach, so there is no network to choose."
)
"""What "Who can connect" says for a server with no address to advertise (DFO)."""

ANOTHER_ADDRESS = "Another address…"
"""The last choice under "Who can connect": an address the player types (a VPN's, say)."""

DirPicker = Callable[[QWidget | None, str, Path | None], Path | None]
StartClient = Callable[[LaunchSpec], bool]
OpenFirewall = Callable[[SingleEntry, str], FirewallReport]
PointLauncher = Callable[[SingleEntry, Path | None, str], single_launcher.LauncherWrite]


class SingleServerView(QWidget):
    """The Server tab of one single-container install."""

    action_failed = Signal(str)
    """A press failed; the sentence the player was shown. The window logs it."""

    remove_requested = Signal(str, object)
    """"Remove from Yu'lon…" was pressed: (game id, server dir). The window asks and forgets."""

    stopped_for_removal = Signal(str, object, bool, str)
    """The stop a removal asked for ended: (game id, server dir, ok, why it failed).

    The same four the WoW Server tab sends, so the window's removal reads one shape.
    """

    def __init__(
        self,
        entry: SingleEntry,
        controller: SingleServer,
        *,
        client_dir: Path | None = None,
        remember_client_dir: Callable[[Path | None], None] | None = None,
        account: str = "",
        remember_account: Callable[[str], None] | None = None,
        run_job: JobRunner | None = None,
        poll_ms: int = POLL_MS,
        reaches: Callable[[], tuple[Reach, ...]] = single_network.reaches,
        os_name: Callable[[], str] = platform.detect,
        pick_dir: DirPicker = pick_folder,
        start_client: StartClient = single_play.start,
        open_firewall: OpenFirewall = single_network.open_firewall,
        point_launcher: PointLauncher = single_launcher.point_launcher,
        gaming_mode_seam: gaming_mode.GamingModeSeam | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.entry = entry
        self.controller = controller
        self.client_dir = client_dir
        self._remember_client_dir = remember_client_dir
        self._remember_account = remember_account
        self._os_name = os_name
        self._pick_dir = pick_dir
        self._start_client = start_client
        self._open_firewall = open_firewall
        self._point_launcher = point_launcher
        # Steam Deck Gaming Mode (Linux only): `main.py` hands no seam elsewhere, and
        # no seam draws no button (`gaming_mode.seam_for()`).
        self._gaming_mode = gaming_mode_seam
        # The threaded runner is held so `shutdown()` can join it; a test's runner is not.
        self._runner: ThreadedJobRunner | None = None
        self._run: JobRunner
        if run_job is None:
            self._runner = threaded_job_runner(self)
            self._run = self._runner
        else:
            self._run = run_job
        self._polling = False
        self._pressing = ""  # what a press in flight is doing, in the words a refusal uses
        self._address = LOOPBACK  # the address the Apply in flight is setting

        layout = QVBoxLayout(self)
        layout.addLayout(self._build_head())
        layout.addLayout(self._build_server_buttons())
        layout.addWidget(self._section("Who can connect"))
        self.apply_network_button: QPushButton | None = None
        if entry.advertise is not None:
            layout.addLayout(self._build_network(reaches()))
        else:
            layout.addWidget(self._meta(THIS_COMPUTER_ONLY))
        if entry.play is not None:
            layout.addWidget(self._section("Play"))
            layout.addLayout(self._build_play(account))
        layout.addWidget(self._section("Server console"))
        self.console = LogPanel(self, shows_failure=False)
        layout.addWidget(self.console, 1)

        self._set_buttons(self.realm_badge.status)
        self._timer = QTimer(self)
        self._timer.setInterval(poll_ms)
        self._timer.timeout.connect(self.poll)
        self._timer.start()
        QTimer.singleShot(0, self.poll)
        if entry.advertise is not None:
            QTimer.singleShot(0, self.read_address)

    # -- building ----------------------------------------------------------------

    def _section(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setObjectName("section-title")
        return label

    def _meta(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setObjectName("tile-meta")
        label.setWordWrap(True)
        return label

    def _build_head(self) -> QVBoxLayout:
        title = QLabel(f"<b>{self.entry.name}</b>", self)
        title.setObjectName("section-title")
        self.realm_badge = DadcraftRealmBadge("unknown", self)
        head = QHBoxLayout()
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.realm_badge)

        open_folder = QPushButton("Open folder", self)
        open_folder.setIcon(dadcraft_icon("folder", COLOR_TEXT_GOLD, 14))
        open_folder.clicked.connect(self._open_folder)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self._meta(f"Server folder: {self.controller.server_dir}"), 1)
        folder_row.addWidget(open_folder)

        box = QVBoxLayout()
        box.addLayout(head)
        box.addLayout(folder_row)
        box.addWidget(
            self._meta("Ports: " + ", ".join(f"{p.label} {p.port}" for p in self.entry.ports))
        )
        return box

    def _build_server_buttons(self) -> QHBoxLayout:
        self.start_button = QPushButton("Start", self)
        self.start_button.setObjectName(f"start-{self.entry.id}")
        self.start_button.setProperty("primary", True)
        self.start_button.clicked.connect(self.start_server)
        self.stop_button = QPushButton("Stop", self)
        self.stop_button.setObjectName(f"stop-{self.entry.id}")
        self.stop_button.clicked.connect(self.stop_server)
        self.remove_button = QPushButton("Remove from Yu'lon…", self)
        self.remove_button.setToolTip(
            "Take this server off Yu'lon's list. Nothing on disk or in Docker is deleted."
        )
        self.remove_button.clicked.connect(self._ask_to_remove)
        help_button = QPushButton(HELP_BUTTON, self)
        help_button.clicked.connect(self._show_help)
        row = QHBoxLayout()
        row.addWidget(self.start_button)
        row.addWidget(self.stop_button)
        row.addStretch(1)
        row.addWidget(help_button)
        row.addWidget(self.remove_button)
        return row

    def _build_network(self, reaches: tuple[Reach, ...]) -> QVBoxLayout:
        self.address_label = self._meta("Clients are told to connect to: …")
        self.reach_box = QComboBox(self)
        for reach in reaches:
            self.reach_box.addItem(reach.label, reach.address)
        self.reach_box.addItem(ANOTHER_ADDRESS, None)
        self.reach_box.currentIndexChanged.connect(self._reach_changed)
        self.other_address = QLineEdit(self)
        self.other_address.setPlaceholderText("an IPv4 address, such as 100.64.0.7")
        self.other_address.setVisible(False)
        self.apply_network_button = QPushButton("Apply", self)
        self.apply_network_button.clicked.connect(self.apply_network)
        row = QHBoxLayout()
        row.addWidget(self.reach_box, 1)
        row.addWidget(self.other_address, 1)
        row.addWidget(self.apply_network_button)
        logins = ", ".join(str(p.port) for p in self.entry.ports)
        box = QVBoxLayout()
        box.addWidget(self.address_label)
        box.addLayout(row)
        box.addWidget(
            self._meta(
                "To play from another computer, such as a Steam Deck, choose this network, "
                f"then point that computer's {self.entry.name} launcher at the address above "
                f"(ports {logins})."
            )
        )
        return box

    def _build_play(self, account: str) -> QVBoxLayout:
        assert self.entry.play is not None
        self.client_label = self._meta(self._client_text())
        choose = QPushButton("Choose client folder…", self)
        choose.setIcon(dadcraft_icon("folder", COLOR_TEXT_GOLD, 14))
        choose.clicked.connect(self.choose_client_dir)
        client_row = QHBoxLayout()
        client_row.addWidget(self.client_label, 1)
        client_row.addWidget(choose)
        self.account_edit = QLineEdit(account, self)
        self.account_edit.setPlaceholderText("account name")
        self.account_edit.setMaxLength(20)
        self.play_button = QPushButton("Play", self)
        self.play_button.setObjectName(f"play-{self.entry.id}")
        self.play_button.setProperty("primary", True)
        self.play_button.clicked.connect(self.play)
        play_row = QHBoxLayout()
        play_row.addWidget(QLabel("Account:", self))
        play_row.addWidget(self.account_edit, 1)
        play_row.addWidget(self.play_button)
        box = QVBoxLayout()
        box.addLayout(client_row)
        box.addLayout(play_row)
        made = (
            "Start runs the server for this account, and makes it the first time a new name "
            "is used; the server also reads its game data from this client."
            if self.entry.install.process is not None
            else "Your account is made the first time you log in with a new name."
        )
        box.addWidget(
            self._meta(f"{made} Yu'lon never downloads a client: choose the one you already have.")
        )
        launcher = single_launcher.launcher_of(self.entry)
        if launcher is not None:
            self.launcher_button = QPushButton(f"Add to {launcher.name}", self)
            self.launcher_button.setObjectName(f"launcher-{self.entry.id}")
            self.launcher_button.clicked.connect(self.add_to_launcher)
            launcher_row = QHBoxLayout()
            launcher_row.addWidget(
                self._meta(
                    f"Playing through {launcher.name} instead? This lists the server in it as "
                    f"{launcher.section}. Nothing else in that file changes."
                ),
                1,
            )
            launcher_row.addWidget(self.launcher_button)
            box.addLayout(launcher_row)
        if self._gaming_mode is not None:
            self.gaming_mode_button = QPushButton(gaming_mode.BUTTON, self)
            self.gaming_mode_button.setObjectName(f"gaming-mode-{self.entry.id}")
            self.gaming_mode_button.clicked.connect(self.add_to_gaming_mode)
            gaming_row = QHBoxLayout()
            gaming_row.addWidget(
                self._meta(
                    "Play from Gaming Mode: adds a Server, a game and a Server Stop shortcut "
                    "to Steam. Quit Steam first."
                ),
                1,
            )
            gaming_row.addWidget(self.gaming_mode_button)
            box.addLayout(gaming_row)
        return box

    def _client_text(self) -> str:
        assert self.entry.play is not None
        if self.client_dir is None:
            return f"Client: not chosen yet ({self.entry.play.client})"
        return f"Client: {self.client_dir}"

    # -- what the window asks of every server tab -------------------------------

    def log_panels(self) -> list[LogPanel]:
        """Every panel this view owns, for the window's stop-and-join at teardown."""
        return [self.console]

    def busy_reason(self) -> str | None:
        """Why the window may not close yet: a press that has not answered."""
        if self._pressing:
            return f"{self.entry.name} is still {self._pressing}."
        return None

    def shutdown(self) -> None:
        """Stop polling and join this view's jobs before the window is torn down.

        A `QThread` destroyed while running aborts the process, so the console's
        follower and every job are ended here, not left to the widget's deletion.
        """
        self._timer.stop()
        self.console.stop()
        self.console.wait(5000)
        if self._runner is not None:
            self._runner.wait(5000)

    # -- status ------------------------------------------------------------------

    @Slot()
    def poll(self) -> None:
        """Ask how the server is, off the GUI thread; one question at a time."""
        if self._polling or self._pressing:
            return
        self._polling = True
        self._run(self.controller.status, self._on_status, self._on_poll_failed)

    @Slot(object)
    def _on_status(self, status: object) -> None:
        self._polling = False
        said = str(status)
        self.realm_badge.set_status(said)
        self._set_buttons(said)
        if said in (RUNNING, STARTING) and not self.console.running:
            self.console.clear()
            if self.controller.follows_with_cancel:
                # A follow that polls a file has no child for the panel's Stop to end;
                # it ends on this event instead (`SingleServer.follows_with_cancel`).
                cancel = threading.Event()
                self.console.run(
                    lambda: self.controller.follow_logs(cancel),
                    title="server console",
                    cancel=cancel,
                    ended=CONSOLE_ENDED,
                )
            else:
                self.console.run(
                    self.controller.follow_logs, title="server console", ended=CONSOLE_ENDED
                )

    @Slot(object)
    def _on_poll_failed(self, exc: object) -> None:
        self._polling = False
        logger.warning(f"{self.entry.id}: status poll failed: {exc}")
        self.realm_badge.set_status("unknown")
        self._set_buttons("unknown")

    def _set_buttons(self, status: str) -> None:
        free = not self._pressing
        self.start_button.setEnabled(free and status == STOPPED)
        self.stop_button.setEnabled(free and status in (RUNNING, STARTING))
        if self.apply_network_button is not None:
            self.apply_network_button.setEnabled(free)
        if self.entry.play is not None:
            self.play_button.setEnabled(free and status == RUNNING)
        if single_launcher.launcher_of(self.entry) is not None:
            self.launcher_button.setEnabled(free)
        if self._gaming_mode is not None:
            self.gaming_mode_button.setEnabled(free)

    def _begin(self, doing: str, badge: str | None = None) -> bool:
        """Claim the tab for one press; False if another one is still running."""
        if self._pressing:
            return False
        self._pressing = doing
        if badge is not None:
            self.realm_badge.set_status(badge)
        self._set_buttons(self.realm_badge.status)
        return True

    def _end(self) -> None:
        self._pressing = ""
        self._set_buttons(self.realm_badge.status)

    # -- Start and Stop -----------------------------------------------------------

    @Slot()
    def start_server(self) -> None:
        """Start the server; the badge reads "starting" until a poll sees it listen.

        With the account and the client folder Play shows: a host-program server is
        started FOR an account and reads its game data from the player's client
        (`SingleServer.start`); a container server reads neither.
        """
        account = self.account_edit.text().strip() if self.entry.play is not None else ""
        client_dir = self.client_dir
        if not self._begin("starting", STARTING):
            return
        if self.entry.install.process is not None and self._remember_account is not None:
            self._remember_account(account)
        self._run(
            lambda: self.controller.start(account=account, client_dir=client_dir),
            self._on_pressed,
            self._on_press_failed,
        )

    @Slot()
    def stop_server(self) -> None:
        """Stop the server; the badge reads "stopping" until Docker answers."""
        if self._begin("stopping", "stopping"):
            self.console.stop()
            self._run(self.controller.stop, self._on_pressed, self._on_press_failed)

    @Slot(object)
    def _on_pressed(self, _result: object) -> None:
        self._end()
        self.poll()

    @Slot(object)
    def _on_press_failed(self, exc: object) -> None:
        what = self._pressing or "working"
        self._end()
        self._fail(f"{self.entry.name} could not finish {what}", str(exc))
        self.poll()

    def _fail(self, title: str, message: str) -> None:
        logger.warning(f"{self.entry.id}: {title}: {message}")
        self.action_failed.emit(message)
        show_warning(self, title, message)

    # -- who can connect ----------------------------------------------------------

    @Slot()
    def read_address(self) -> None:
        """Read the address clients are told to connect to, off the GUI thread."""
        self._run(self.controller.advertised, self._on_address, self._on_address_failed)

    @Slot(object)
    def _on_address(self, address: object) -> None:
        said = str(address) if address else "nothing yet"
        self.address_label.setText(f"Clients are told to connect to: {said}")
        index = self.reach_box.findData(address)
        if address and index == -1:
            index = self.reach_box.findData(None)
            self.other_address.setText(str(address))
        if index != -1:
            self.reach_box.setCurrentIndex(index)

    @Slot(object)
    def _on_address_failed(self, exc: object) -> None:
        self.address_label.setText(f"Clients are told to connect to: unknown ({exc})")

    @Slot(int)
    def _reach_changed(self, _index: int) -> None:
        self.other_address.setVisible(self.reach_box.currentData() is None)

    def chosen_address(self) -> str:
        """The address the "Who can connect" choice names, as typed or as offered."""
        data = self.reach_box.currentData()
        return str(data) if data is not None else self.other_address.text().strip()

    @Slot()
    def apply_network(self) -> None:
        """Tell clients to connect to the chosen address, then open the firewall for others.

        The address first, then — for an address other computers dial, on Windows — the
        firewall rules, behind one administrator prompt the player can decline.
        """
        address = self.chosen_address()
        problem = single_network.check_address(address)
        if problem is not None:
            show_warning(self, "That address cannot be used", problem)
            return
        if not self._begin("changing who can connect"):
            return
        self._address = address
        self._run(
            lambda: self.controller.advertise(address),
            self._on_advertised,
            self._on_press_failed,
        )

    @Slot(object)
    def _on_advertised(self, restarted: object) -> None:
        if restarted:
            self.console.stop()
        if self._address == LOOPBACK or not single_network.firewall_commands(
            self.entry, self._os_name()
        ):
            self._end()
            self.read_address()
            self.poll()
            return
        os_name = self._os_name()
        self._run(
            lambda: self._open_firewall(self.entry, os_name),
            self._on_firewall,
            self._on_press_failed,
        )

    @Slot(object)
    def _on_firewall(self, report: object) -> None:
        self._end()
        self.read_address()
        self.poll()
        if not isinstance(report, FirewallReport):
            return
        if report.skipped:
            show_warning(
                self,
                "Other computers may not be able to connect",
                "\n\n".join(report.skipped),
            )
        elif report.added:
            show_information(
                self,
                "Other computers can connect",
                "Windows Firewall now lets other computers on your network reach ports "
                + ", ".join(str(p) for p in report.added)
                + ".",
            )

    # -- Play -----------------------------------------------------------------------

    @Slot()
    def choose_client_dir(self) -> None:
        """Ask for the player's own client folder and remember it with the server."""
        assert self.entry.play is not None
        chosen = self._pick_dir(
            self, f"Select {self.entry.play.client}", self.client_dir or Path.home()
        )
        if chosen is None:
            return
        self.client_dir = chosen
        self.client_label.setText(self._client_text())
        if self._remember_client_dir is not None:
            try:
                self._remember_client_dir(chosen)
            except OSError as exc:
                self._fail("The client folder was not remembered", str(exc))

    @Slot()
    def play(self) -> None:
        """Start the player's client, pointed at this server through this computer."""
        account = self.account_edit.text().strip()
        # The tab's own platform seam, as the firewall rules ask it: the real one in the
        # app, and the one a test pins, on whatever machine runs the test.
        os_name = self._os_name()
        play = self.entry.play
        gaming = play.gaming_mode if play is not None else None
        try:
            if os_name == "linux" and gaming is not None and gaming.desktop_play:
                # DFO on Linux: the game has to join the server's private network, which
                # its Gaming Mode client script does (`gaming_mode.play_spec()`).
                spec = gaming_mode.play_spec(
                    self.entry,
                    self.client_dir,
                    account=account,
                    server_dir=self.controller.server_dir,
                )
            else:
                spec = single_play.launch_spec(
                    self.entry,
                    self.client_dir,
                    host=LOOPBACK,
                    account=account,
                    server_dir=self.controller.server_dir,
                    os_name=os_name,
                )
        except single_play.PlayRefusal as exc:
            show_warning(self, f"{self.entry.name} was not started", str(exc))
            return
        if self._remember_account is not None:
            self._remember_account(account)
        if not self._begin("starting the game"):
            return
        self._run(lambda: self._play_as(account, spec), self._on_played, self._on_play_failed)

    def _play_as(self, account: str, spec: LaunchSpec) -> bool:
        """Start the client, once the running server is known to be this account's (worker).

        A host-program server is started for one account and logs every client in as
        it, whatever the client says (`SingleServer.session_account`); a client named
        for another account would show one name over another's saves. A container
        server answers None and is never refused here.
        """
        running_for = self.controller.session_account()
        if running_for is not None and running_for != account:
            raise single_play.PlayRefusal(
                f"The server is running for {running_for}. Play as {running_for}, or Stop the "
                f"server and Start it again to play as {account}."
            )
        return self._start_client(spec)

    @Slot(object)
    def _on_played(self, _elevated: object) -> None:
        self._end()

    @Slot(object)
    def _on_play_failed(self, exc: object) -> None:
        self._end()
        self._fail(f"{self.entry.name} was not started", str(exc))

    @Slot()
    def add_to_launcher(self) -> None:
        """List this server in the player's own launcher, as one section of its settings.

        Through this computer (`LOOPBACK`), as Play is: the file is in a client folder
        on this computer, and the address the network advertises can change under it.
        """
        if not self._begin("adding the server to its launcher"):
            return
        client_dir = self.client_dir
        self._run(
            lambda: self._point_launcher(self.entry, client_dir, LOOPBACK),
            self._on_launcher_written,
            self._on_launcher_failed,
        )

    @Slot(object)
    def _on_launcher_written(self, write: object) -> None:
        self._end()
        launcher = single_launcher.launcher_of(self.entry)
        if launcher is None or not isinstance(write, single_launcher.LauncherWrite):
            return
        lead = (
            f"{launcher.name} now lists {launcher.section}."
            if write.changed
            else f"{launcher.name} already lists {launcher.section}; nothing needed changing."
        )
        show_information(
            self,
            f"Added to {launcher.name}",
            f"{lead} Open {launcher.name}, choose {launcher.section}, then Register the first "
            "time and log in.",
        )

    @Slot()
    def add_to_gaming_mode(self) -> None:
        """Write this server's Steam Deck Gaming Mode scripts and its three Steam shortcuts.

        The account typed beside Play is the one the Gaming Mode client logs in with, so
        it is remembered here as Play remembers it: the client script reads it back.
        """
        seam = self._gaming_mode
        if seam is None:
            return
        account = self.account_edit.text().strip()
        if self._remember_account is not None and single_play.check_account(account) is None:
            self._remember_account(account)
        if not self._begin("adding the Gaming Mode shortcuts"):
            return
        client_dir = self.client_dir
        self._run(
            lambda: seam(client_dir, account),
            self._on_gaming_mode_added,
            self._on_gaming_mode_failed,
        )

    @Slot(object)
    def _on_gaming_mode_added(self, report: object) -> None:
        self._end()
        if isinstance(report, gaming_mode.GamingModeReport):
            show_information(self, "Added to Steam Deck", gaming_mode.confirmation(report))

    @Slot(object)
    def _on_gaming_mode_failed(self, exc: object) -> None:
        self._end()
        self._fail(f"{self.entry.name} was not added to Steam", str(exc))

    @Slot(object)
    def _on_launcher_failed(self, exc: object) -> None:
        self._end()
        launcher = single_launcher.launcher_of(self.entry)
        name = launcher.name if launcher is not None else "its launcher"
        self._fail(f"{self.entry.name} was not added to {name}", str(exc))

    # -- the rest ----------------------------------------------------------------

    @Slot()
    def _open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.controller.server_dir)))

    @Slot()
    def _show_help(self) -> None:
        box = HelpPlacesBox(self, self.entry.name, self.entry.help_places)
        try:
            box.exec()
        finally:
            box.deleteLater()

    @Slot()
    def _ask_to_remove(self) -> None:
        self.remove_requested.emit(self.entry.id, Path(self.controller.server_dir))

    def stop_for_removal(self) -> None:
        """Stop the server before the window forgets it; answer on `stopped_for_removal`.

        Whatever the last poll said: a server started outside Yu'lon since then would
        otherwise be forgotten while it ran. `SingleController.stop()` answers False
        when there was nothing to stop, which is a success here.
        """
        self._pressing = "stopping"
        self.realm_badge.set_status("stopping")
        self._set_buttons("stopping")
        self.console.stop()
        self._run(self.controller.stop, self._on_removal_stop, self._on_removal_stop_failed)

    @Slot(object)
    def _on_removal_stop(self, _result: object) -> None:
        self._end()
        self.stopped_for_removal.emit(self.entry.id, Path(self.controller.server_dir), True, "")

    @Slot(object)
    def _on_removal_stop_failed(self, exc: object) -> None:
        self._end()
        self.stopped_for_removal.emit(
            self.entry.id, Path(self.controller.server_dir), False, str(exc)
        )
        self.poll()
