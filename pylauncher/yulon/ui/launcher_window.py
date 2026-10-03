"""The client launcher window: one server's game launcher (T187).

One window per server, with one big PLAY at the bottom left: whether the realm
is up, which ready-to-play client PLAY starts, the realm address the game
connects to, which account to fill in, how the game window opens, the server's
optional client packs, and the addons in the client.

**It drives the server's `ControllerView`; it does not copy it.** PLAY is the
view's `play()`, Make…/Refresh/Delete are the view's presses, the progress line
and Cancel are the view's own, and every choice is saved through
`ControllerView.save_launcher_picks()`, which takes the lock Client options…
takes. So every guard -- a module job, a Server action, another Play -- is the
one the Server tab already has, and there is one Play pipeline. The record the
choices go into (`client_packs.PackRecord.launcher`) is applied by that
pipeline at the next Play, never on change: nothing is downloaded, patched or
written into the client by changing a setting here.

**Every read is on a worker** (`reads`, through `job_runner`): the record, the
addon list and the comparison with the player's own client when the window
opens or a job ends; the accounts, the online count and the realm row when the
realm is up. The database is asked nothing while the realm is down -- it would
only time out -- and never while a WSL server's distro is not known to run
(T133: a read there would start it).

Opening, raising, remembering the window's size and closing it with its server
are `main.py`'s (Task 4): the window hands over what to remember (`closed`,
`geometry_text()`, `addresses_changed`) and is given it back (`addresses`,
`restore_geometry_text()`). `set_view()` points an open window at the tab
`main.py` rebuilds after Make…/Delete; a view that is destroyed with nothing to
replace it leaves the window greyed rather than calling into it.

The view's own dialogs for a press made here open over this window: each press
sets `ControllerView.dialog_host` first. B on a controller closes the window
(`gamepad.BACK_CLOSES`), as it closes a dialog.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import shiboken6
from PySide6.QtCore import QByteArray, QSize, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import (
    QCloseEvent,
    QDesktopServices,
    QGuiApplication,
    QHideEvent,
    QResizeEvent,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from yulon import (
    client_packs,
    dashboard,
    dbreads,
    docker,
    launcher_reads,
    play_client,
    ui_settings,
    wsl,
)
from yulon.catalog.catalog import CatalogEntry
from yulon.log import get_logger
from yulon.ui import theme
from yulon.ui.controller_view import (
    MAKE_PLAY_CLIENT_LABEL,
    PLAY_CLIENT_ADDRESS,
    ControllerView,
    module_kept_files,
    size_text,
)
from yulon.ui.gamepad import BACK_CLOSES
from yulon.ui.icons import dadcraft_icon
from yulon.ui.widgets.dadcraft_decorations import DadcraftRealmBadge
from yulon.ui.widgets.job import JobRunner, threaded_job_runner

logger = get_logger(__name__)

PLAY_TEXT = "  PLAY"
"""Two spaces ahead of the word: the room between the ▶ icon and the letters."""
COMPACT_BELOW = 720
"""Below this window height the two reassurance lines move into tooltips (T187).

At the 960x640 minimum the left column has no room for every line and a
scrollbar would hide the Log in as box under PLAY; "Your own client is never
changed" and "The password is never stored" stay on the dropdowns' tooltips
there, and are on screen from the Steam Deck's 800 up."""
ASK_IN_THE_GAME = "Ask in the game"
GAMES_OWN = "The game's own setting"
GAME_REMEMBERS = "As the game remembers it"
"""Log in as with nothing picked: Config.wtf's `accountName` is left as the game last wrote it."""
SET_BY_SERVER = "set by this server"
NO_EXTRAS = "This server offers no extras."
PASSWORD_NOTE = "The password is never stored: you type it in the game."
SAVING = "Saving your choice…"
STOPPED_BANNER = "Play starts the server first (about 1 minute)"
STARTING_BANNER = "The server is starting…"
STOPPING_BANNER = "The server is stopping…"
VIEW_GONE = (
    "This server's tab was closed, so nothing can be started from here. Open the launcher "
    "again from the server."
)

MINIMUM_SIZE = QSize(960, 640)
DEFAULT_SIZE = QSize(1100, 760)
"""The size a launcher opens at the first time: the mockup's, on a Steam Deck and up.

Never the screen's whole width (a 1920-wide stretch spreads two columns apart
for nothing); smaller where the screen is (`fitted_size`)."""

ONLINE_REFRESH_MS = 60_000
"""How often the banner's count and uptime are read again while the window shows the
realm up. A minute: the uptime is shown to the minute, and each read is two small
queries and one `docker inspect`, on a worker."""

ADDON_ROWS = 3
"""How many rows of "Addons in this client" always show; the list grows into spare height."""

ACCOUNT_KEEP = "keep:"
"""The account item that leaves Config.wtf's `accountName` as the game has it (no pick saved)."""
ACCOUNT_ASK = "ask:"
"""The account item "Ask in the game": the record says `None`, and Play takes the name out."""


def account_data(name: str) -> str:
    """The combo's data for an account name (strings, because Qt hands tuples back as lists)."""
    return f"name:{name}"


WINDOW_LABELS: dict[str, str] = {
    "fullscreen": "Full screen",
    "windowed": "Windowed",
    "maximized": "Windowed, maximized",
    "borderless": "Borderless window",
}
"""How each `client_packs.WINDOW_MODES` pick reads in the Window box."""

COMMON_RESOLUTIONS: tuple[tuple[int, int], ...] = (
    (1024, 768),
    (1280, 720),
    (1280, 800),
    (1366, 768),
    (1600, 900),
    (1920, 1080),
    (1920, 1200),
    (2560, 1080),
    (2560, 1440),
    (3840, 2160),
)
"""The sizes offered beside this screen's own. All of them, not only the ones that fit
this screen: the game may run on another monitor, and a window larger than the
screen is the player's to choose."""


def fitted_size(available: QSize) -> QSize:
    """`DEFAULT_SIZE` where the screen's free area has room, smaller where not, never
    under `MINIMUM_SIZE` (the window cannot be made smaller than that anyway)."""
    return QSize(
        max(MINIMUM_SIZE.width(), min(DEFAULT_SIZE.width(), available.width())),
        max(MINIMUM_SIZE.height(), min(DEFAULT_SIZE.height(), available.height())),
    )


# -- what the window reads ------------------------------------------------------


@dataclass(frozen=True)
class Online:
    """The banner's count: bots and players online, and how long the world has been up."""

    bots: int
    players: int
    uptime: timedelta | None = None


@dataclass(frozen=True)
class ServerReading:
    """What the server's database said. Each part is `None` when it could not be read."""

    accounts: tuple[str, ...] | None
    online: Online | None
    announced: str | None


UNREAD = ServerReading(accounts=None, online=None, announced=None)


@dataclass(frozen=True)
class ClientReading:
    """What the ready-to-play client's folder said."""

    record: client_packs.PackRecord
    source: Path | None
    """The player's own client it was made from (its marker), or None when unreadable."""
    stale: tuple[Path, ...] | None
    """What differs from the player's own client, or None when they could not be compared."""
    addons: tuple[str, ...]
    addons_dir: Path
    """The folder "Open AddOns folder" opens: `Interface/AddOns`, or the nearest that exists."""


class LauncherReads(Protocol):
    """The two reads, each run on a worker. A seam, so a test answers the database part."""

    def server(self) -> ServerReading: ...

    def client(self, play: Path) -> ClientReading: ...


class ViewReads:
    """The real reads, over what the view's services hold.

    Plain values are taken off the view when this is made, so the worker never
    touches a widget: the catalog entry, the folders, the container's name, and
    the accounts seam's SQL reader and app account name.
    """

    def __init__(self, view: ControllerView) -> None:
        services = view.services
        self.entry: CatalogEntry = view.entry
        self.server_dir: Path = services.controller.server_dir
        self.client_dir: Path | None = services.client_dir
        self._distro: str | None = services.controller.wsl_distro
        self._world: str = services.controller.spec.world
        admin = services.accounts
        sql = getattr(admin, "sql", None)
        app_account = getattr(admin, "app_account", None)
        self._sql: dbreads.SqlReader | None = sql if isinstance(sql, dbreads.SqlReader) else None
        self._app_account: str | None = app_account if isinstance(app_account, str) else None

    def server(self) -> ServerReading:
        """Accounts, the online count and the realm row; never a read into a stopped distro."""
        if self._distro is not None and not wsl.may_read(self._distro):
            return UNREAD
        sql, app_account = self._sql, self._app_account
        if sql is None or app_account is None:
            return UNREAD
        marker = dbreads.resolve_marker(self.entry, self.server_dir).marker
        accounts: tuple[str, ...] | None = None
        online: Online | None = None
        if marker is not None:
            accounts = launcher_reads.account_names(
                sql, self.entry, marker, app_account=app_account
            )
            counts = launcher_reads.online_counts(sql, self.entry, marker)
            if counts is not None:
                online = Online(counts[0], counts[1], self._uptime())
        return ServerReading(accounts, online, launcher_reads.announced_address(sql, self.entry))

    def _uptime(self) -> timedelta | None:
        try:
            state = docker.container_state(self._world, wsl_distro=self._distro)
        except Exception as exc:  # noqa: BLE001 - an uptime is a nicety, never a lost window
            logger.debug(f"could not read how long {self._world} has been up: {exc}")
            return None
        if not state.settled:
            return None
        return dashboard.run_length(state.started_at, datetime.now(UTC))

    def client(self, play: Path) -> ClientReading:
        """The record, the addons and the comparison with the player's own client."""
        record = client_packs.read_record(play)
        marker = play_client.read_marker(play)
        source = Path(marker.source_client_dir) if marker is not None else None
        stale: tuple[Path, ...] | None = None
        ours = (
            marker is not None
            and marker.game == self.entry.id
            and marker.server_dir == self.server_dir
        )
        if ours and source is not None:
            try:
                stale = play_client.stale(
                    play,
                    source,
                    keep=module_kept_files(self.server_dir, play, self.client_dir),
                )
            except Exception as exc:  # noqa: BLE001 - "could not compare" is an answer
                logger.info(f"could not compare {play} with {source}: {exc}")
        return ClientReading(
            record,
            source,
            stale,
            launcher_reads.addon_folders(play),
            launcher_reads.addons_folder(play),
        )


def _open_in_file_manager(path: Path) -> bool:
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


# -- small widgets --------------------------------------------------------------


class _PathLabel(QLabel):
    """A folder path on one line, shortened in the middle to its width; the whole in the tooltip."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(80)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

    def set_path(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self._elide()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt's own name
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        shown = self.fontMetrics().elidedText(
            self._full, Qt.TextElideMode.ElideMiddle, max(0, self.width())
        )
        if shown != self.text():
            self.setText(shown)


def _muted(text: str, parent: QWidget, *, wrap: bool = True) -> QLabel:
    label = QLabel(text, parent)
    label.setWordWrap(wrap)
    label.setStyleSheet(f"color: {theme.COLOR_TEXT_MUTED};")
    return label


def _warning(parent: QWidget) -> QLabel:
    """A one-off sentence in the warning colour; hidden until there is something to say."""
    label = QLabel("", parent)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {theme.COLOR_TEXT_WARNING};")
    label.setVisible(False)
    return label


def _resolution_text(value: str) -> str:
    width, _, height = value.partition("x")
    return f"{width} × {height}"


def _core(entry: CatalogEntry) -> str:
    """The emulator's short name: `AzerothCore`, `CMaNGOS`, `tortoise-wow`."""
    words = entry.emulator.name.split()
    return words[0].split("/")[0] if words else entry.emulator.name


def _fixed_window(always: Mapping[str, str]) -> str | None:
    """How the catalog's own `gxWindow`/`gxMaximize` read, or None when it sets neither."""
    keys = {key.casefold(): value for key, value in always.items()}
    if "gxwindow" not in keys and "gxmaximize" not in keys:
        return None
    window, maximize = keys.get("gxwindow"), keys.get("gxmaximize")
    if window == "0":
        return WINDOW_LABELS["fullscreen"]
    if maximize == "1":
        return WINDOW_LABELS["maximized"]
    if window == "1":
        return WINDOW_LABELS["windowed"]
    return f"gxWindow {window or '?'}, gxMaximize {maximize or '?'}"


class LauncherWindow(QWidget):
    """One server's game launcher; see the module docstring."""

    server_tab_requested = Signal(str, object)
    """"Server tab…": bring the main window to this server's tab (game id, server_dir)."""
    closed = Signal()
    """The window was closed: `main.py` remembers where it was (`geometry_text()`)."""
    addresses_changed = Signal(list)
    """The typed realm addresses to offer again, newest first (`ui_settings.recent_addresses`)."""

    def __init__(
        self,
        view: ControllerView,
        *,
        reads: LauncherReads | None = None,
        job_runner: JobRunner | None = None,
        open_folder: Callable[[Path], bool] | None = None,
        addresses: Sequence[str] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        # Held for the window's life: the runner keeps its workers referenced
        # until they finish, which is what keeps a collected worker from
        # dropping a job (job.py, rule one).
        self._jobs: JobRunner = job_runner or threaded_job_runner(self)
        self._open_folder = open_folder or _open_in_file_manager
        self._view: ControllerView | None = None
        self._given_reads = reads
        self.reads: LauncherReads = reads or ViewReads(view)
        self._client: ClientReading | None = None
        self._server: ServerReading | None = None
        # Offered again in the realm box, newest first: what `main.py` remembered
        # for this server, cleaned again here (only addresses, never this computer).
        self._typed: list[str] = ui_settings.recent_addresses(addresses)
        self._stale_account: str | None = None
        self._reconcile_owed = False
        # A client read is on its worker: the owed account check waits for its
        # answer rather than judging the reading it replaces (fix round 1).
        self._client_pending = False
        # The last client read failed: the reading held is the one from before
        # it, too old to judge a saved account by (Task 5 fix).
        self._client_unread = False
        # The stale-account "Ask" save on its way, and the record a failed one was
        # made for: not tried again for that record, or a read-only record would
        # be saved, refused, re-read and saved again for ever (final review).
        self._auto_saving: client_packs.PackRecord | None = None
        self._auto_failed: client_packs.PackRecord | None = None
        # A launcher save is on its way (PLAY waits for it), and PLAY's own press
        # waiting for the typed address it saved first.
        self._saving = False
        self._play_after_save = False
        # Hidden by a close (not a minimize): what it shows may be old by the
        # time it is opened again, so the next show reads it again.
        self._reread_on_show = False
        self._was_busy = False
        self._client_asked = 0
        self._server_asked = 0
        self._online_asked = 0
        self.pack_boxes: dict[str, QCheckBox] = {}
        self.pack_sizes: dict[str, QLabel] = {}
        self._always: dict[str, str] = {}
        self._borderless = False
        self._compact = False
        self.setMinimumSize(MINIMUM_SIZE)
        screen = QGuiApplication.primaryScreen()
        self.resize(
            fitted_size(screen.availableGeometry().size()) if screen is not None else DEFAULT_SIZE
        )
        # B on a controller closes this window as it closes a dialog (T187).
        self.setProperty(BACK_CLOSES, True)
        # The banner's count and uptime, read again while the window shows the
        # realm up (`_sync_online_timer`); stopped whenever it is hidden.
        self.online_timer = QTimer(self)
        self.online_timer.setInterval(ONLINE_REFRESH_MS)
        self.online_timer.timeout.connect(self._refresh_online)
        self._build()
        self.set_view(view, reads=reads)

    # -- the view this window drives ---------------------------------------

    def set_view(self, view: ControllerView, *, reads: LauncherReads | None = None) -> None:
        """Drive `view` from now on: the tab `main.py` rebuilt after Make… or Delete."""
        old = self._view
        if old is not None and shiboken6.isValid(old):
            for signal, slot in self._links(old):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):  # pragma: no cover - already gone
                    pass
        self._view = view
        self.reads = reads or self._given_reads or ViewReads(view)
        for signal, slot in self._links(view):
            signal.connect(slot)
        self._client = None
        self._server = None
        self._stale_account = None
        # A read still on its worker was asked for the old view: its answer must
        # not land on this one (Task 5 fix). The fresh reads below re-check.
        self._client_asked += 1
        self._server_asked += 1
        self._client_pending = False
        self._client_unread = False
        self._reconcile_owed = False
        self._auto_saving = None
        self._auto_failed = None
        self._saving = False
        self._play_after_save = False
        self._was_busy = view.play_client_busy() is not None
        self._bind_entry(view.entry, view.services.controller.server_dir)
        self.realm_badge.set_status(view.realm_badge.status)
        self._render()
        self._follow_play()
        self._reload_client()
        if self.realm_badge.status == "running":
            self._reload_server()
        self._sync_online_timer()
        if self._has_play():
            self.play_button.setFocus()
        else:
            self.make_button.setFocus()

    def _links(self, view: ControllerView) -> list[tuple[Any, Callable[..., None]]]:
        return [
            (view.play_state_changed, self._follow_play),
            (view.launcher_saved, self._saved),
            (view.realm_badge.status_changed, self._realm_changed),
            (view.destroyed, self._view_gone),
        ]

    @property
    def view(self) -> ControllerView | None:
        """The Server tab this window drives; None once it is gone."""
        return self._alive()

    def _alive(self) -> ControllerView | None:
        view = self._view
        if view is None or not shiboken6.isValid(view):
            return None
        return view

    def _has_play(self) -> bool:
        view = self._alive()
        return (
            view is not None
            and view.services.set_play_client_dir is not None
            and view.services.play_client_dir is not None
        )

    def _play_dir(self) -> Path | None:
        view = self._alive()
        return view.services.play_client_dir if view is not None and self._has_play() else None

    @Slot()
    def _view_gone(self) -> None:
        self._view = None
        self._render()

    # -- building ----------------------------------------------------------

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_banner())
        body = QHBoxLayout()
        body.setContentsMargins(24, 10, 24, 10)
        body.setSpacing(20)
        body.addLayout(self._build_left(), 11)
        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(self._build_right(), 1)
        right.addLayout(self._build_footer())
        body.addLayout(right, 9)
        outer.addLayout(body, 1)
        # Controller order (spec §1): Realm address, then Log in as, then PLAY.
        QWidget.setTabOrder(self.realm_combo, self.account_combo)
        QWidget.setTabOrder(self.account_combo, self.play_button)

    def _build_banner(self) -> QFrame:
        self.banner = QFrame(self)
        self.banner.setObjectName("launcher-banner")
        self.banner.setStyleSheet(
            "QFrame#launcher-banner { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, "
            f"stop:0 {theme.COLOR_BG_PANEL}, stop:0.55 {theme.COLOR_EMBER}, "
            f"stop:1 {theme.COLOR_BG_DARK}); "
            f"border-bottom: 1px solid {theme.COLOR_GOLD_BRASS}; }}"
            " QFrame#launcher-banner QLabel { background: transparent; border: none; }"
        )
        box = QVBoxLayout(self.banner)
        box.setContentsMargins(24, 10, 24, 10)
        box.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(16)
        self.banner_title = QLabel("", self.banner)
        self.banner_title.setStyleSheet(
            f"font-family: {theme.FONT_FAMILY_TITLE}; font-size: 28px; font-weight: bold; "
            f"color: {theme.COLOR_GOLD_BRIGHT};"
        )
        top.addWidget(self.banner_title, 0, Qt.AlignmentFlag.AlignBottom)
        self.banner_subtitle = QLabel("", self.banner)
        self.banner_subtitle.setStyleSheet(f"color: {theme.COLOR_TEXT_GOLD};")
        top.addWidget(self.banner_subtitle, 1, Qt.AlignmentFlag.AlignBottom)
        box.addLayout(top)
        row = QHBoxLayout()
        row.setSpacing(12)
        self.realm_badge = DadcraftRealmBadge("stopped", self.banner)
        row.addWidget(self.realm_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        self.online_label = QLabel("", self.banner)
        self.online_label.setStyleSheet(f"color: {theme.COLOR_TEXT_PRIMARY};")
        row.addWidget(self.online_label, 1)
        box.addLayout(row)
        return self.banner

    def _scroll(self, inner: QWidget, name: str) -> QScrollArea:
        """A frameless scroll area: the column fits at 960x640, and scrolls rather than clip."""
        area = QScrollArea(self)
        area.setObjectName(name)
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Not a stop of its own: up from PLAY is Log in as, not the column around
        # it (the pad's first press from the first focus, T187 Task 4).
        area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        area.setStyleSheet(
            f"QScrollArea#{name}, QScrollArea#{name} > QWidget > QWidget "
            "{ background: transparent; border: none; }"
        )
        area.setWidget(inner)
        return area

    def _build_left(self) -> QVBoxLayout:
        left = QVBoxLayout()
        left.setSpacing(8)
        inner = QWidget(self)
        column = QVBoxLayout(inner)
        column.setContentsMargins(0, 0, 4, 0)
        column.setSpacing(8)
        column.addWidget(self._build_client_box(inner))
        column.addWidget(self._build_make_box(inner))
        column.addWidget(self._build_realm_box(inner))
        column.addStretch(1)
        column.addWidget(self._build_account_box(inner))
        # The left column scrolls as one box, so the D-pad walks it top to
        # bottom before it looks across at the right column (`gamepad._pick`'s
        # scrolled-list rule), and PLAY stays put beneath it at any height.
        self.left_scroll = self._scroll(inner, "launcher-left")
        left.addWidget(self.left_scroll, 1)

        self.play_button = QPushButton(PLAY_TEXT, self)
        self.play_button.setObjectName(theme.LAUNCHER_PLAY_BUTTON)
        self.play_button.setProperty("primary", True)
        self.play_button.setIcon(dadcraft_icon("play", theme.COLOR_BG_DARK, 24))
        self.play_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.play_button.clicked.connect(self._play)
        play_row = QHBoxLayout()
        play_row.setSpacing(8)
        play_row.addWidget(self.play_button, 1)
        # Cancel beside PLAY rather than on a line of its own: the line under
        # PLAY is the one the left column has least room for.
        self.cancel_button = QPushButton("Cancel", self)
        self.cancel_button.setToolTip(
            "Stop getting the client ready. A download stops between reads; what arrived is "
            "kept for next time."
        )
        self.cancel_button.clicked.connect(self._cancel)
        play_row.addWidget(self.cancel_button)
        left.addLayout(play_row)
        self.play_reason_label = _muted("", self)
        left.addWidget(self.play_reason_label)
        self.progress_label = QLabel("", self)
        self.progress_label.setWordWrap(True)
        self.progress_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        left.addWidget(self.progress_label)
        return left

    def _build_client_box(self, parent: QWidget) -> QGroupBox:
        self.client_box = QGroupBox("Client", parent)
        grid = QGridLayout(self.client_box)
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)
        grid.addWidget(_muted("Ready to play", self.client_box, wrap=False), 0, 0)
        self.client_path_label = _PathLabel(self.client_box)
        grid.addWidget(self.client_path_label, 0, 1)
        grid.addWidget(_muted("Made from", self.client_box, wrap=False), 1, 0)
        self.client_source_label = _PathLabel(self.client_box)
        grid.addWidget(self.client_source_label, 1, 1)
        self.client_source_note = _muted("Your own client is never changed.", self.client_box)
        grid.addWidget(self.client_source_note, 2, 1)
        grid.addWidget(_muted("Status", self.client_box, wrap=False), 3, 0)
        self.client_status_label = QLabel("", self.client_box)
        self.client_status_label.setWordWrap(True)
        grid.addWidget(self.client_status_label, 3, 1)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.refresh_button = QPushButton("Refresh from my client", self.client_box)
        self.refresh_button.setToolTip(
            "Bring the game files and Wow.exe back in step with your own client after a "
            "patch. Your settings (WTF) and addons (Interface) are never touched."
        )
        self.refresh_button.clicked.connect(self._refresh)
        self.open_folder_button = QPushButton("Open folder", self.client_box)
        self.open_folder_button.clicked.connect(self._open_play_folder)
        self.delete_button = QPushButton("Delete…", self.client_box)
        self.delete_button.setToolTip(
            "Delete the ready-to-play client. Your own client keeps all its files. Asks first."
        )
        self.delete_button.clicked.connect(self._delete)
        for button in (self.refresh_button, self.open_folder_button, self.delete_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        grid.addLayout(buttons, 4, 0, 1, 2)
        return self.client_box

    def _build_make_box(self, parent: QWidget) -> QGroupBox:
        self.make_box = QGroupBox("Client", parent)
        box = QVBoxLayout(self.make_box)
        box.addWidget(
            _muted(
                "This server has no ready-to-play client yet. Yu'lon makes one from your own "
                "WoW client: the game files are shared, and your own client is never changed.",
                self.make_box,
            )
        )
        row = QHBoxLayout()
        self.make_button = QPushButton(MAKE_PLAY_CLIENT_LABEL, self.make_box)
        self.make_button.setProperty("primary", True)
        self.make_button.clicked.connect(self._make)
        row.addWidget(self.make_button)
        row.addStretch(1)
        box.addLayout(row)
        return self.make_box

    def _build_realm_box(self, parent: QWidget) -> QGroupBox:
        self.realm_box = QGroupBox("Realm address", parent)
        grid = QGridLayout(self.realm_box)
        grid.setColumnStretch(0, 1)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        self.realm_combo = QComboBox(self.realm_box)
        self.realm_combo.setEditable(True)
        self.realm_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.realm_combo.setToolTip(
            "The address the game logs in at: 127.0.0.1 on this computer, the server's LAN "
            "address at home, or its internet name. Written to this client's realmlist only."
        )
        line = self.realm_combo.lineEdit()
        if line is not None:
            # Return, not the focus leaving: a half-typed address moved away from
            # is not one the player meant (final review). PLAY commits it too.
            line.returnPressed.connect(self._address_entered)
        self.realm_combo.activated.connect(self._address_entered)
        grid.addWidget(self.realm_combo, 0, 0)
        self.this_computer_button = QPushButton("Use this computer", self.realm_box)
        self.this_computer_button.setToolTip(f"Log in at {PLAY_CLIENT_ADDRESS}, this computer.")
        self.this_computer_button.clicked.connect(self._use_this_computer)
        grid.addWidget(self.this_computer_button, 0, 1)
        self.realm_mismatch_label = _warning(self.realm_box)
        grid.addWidget(self.realm_mismatch_label, 1, 0, 1, 2)
        self.realm_note = _warning(self.realm_box)
        grid.addWidget(self.realm_note, 2, 0, 1, 2)
        return self.realm_box

    def _build_account_box(self, parent: QWidget) -> QGroupBox:
        self.account_box = QGroupBox("Log in as", parent)
        box = QVBoxLayout(self.account_box)
        # No bottom margin: PLAY is right under this box, and the D-pad goes
        # from the account to PLAY because nothing fits between the two.
        box.setContentsMargins(10, 6, 10, 0)
        box.setSpacing(6)
        # The note ABOVE the dropdown, for the same reason.
        self.account_note = _muted(PASSWORD_NOTE, self.account_box)
        box.addWidget(self.account_note)
        self.account_combo = QComboBox(self.account_box)
        self.account_combo.setToolTip(
            "Fills in the account name on the game's login screen. The password is never "
            "stored or written: you type it in the game."
        )
        self.account_combo.activated.connect(self._account_picked)
        box.addWidget(self.account_combo)
        return self.account_box

    def _build_right(self) -> QScrollArea:
        inner = QWidget(self)
        column = QVBoxLayout(inner)
        column.setContentsMargins(0, 0, 4, 0)
        column.setSpacing(10)

        self.display_box = QGroupBox("Display", inner)
        grid = QGridLayout(self.display_box)
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(12)
        grid.addWidget(_muted("Window", self.display_box, wrap=False), 0, 0)
        self.window_combo = QComboBox(self.display_box)
        self.window_combo.activated.connect(self._window_picked)
        grid.addWidget(self.window_combo, 0, 1)
        grid.addWidget(_muted("Resolution", self.display_box, wrap=False), 1, 0)
        self.resolution_combo = QComboBox(self.display_box)
        self.resolution_combo.activated.connect(self._resolution_picked)
        grid.addWidget(self.resolution_combo, 1, 1)
        self.display_note = _muted("Set in the game's Config.wtf at your next Play.", inner)
        grid.addWidget(self.display_note, 2, 0, 1, 2)
        column.addWidget(self.display_box)

        self.extras_box = QGroupBox("Extras for this server", inner)
        self._extras_layout = QVBoxLayout(self.extras_box)
        self._extras_layout.setSpacing(4)
        self._extras_rows = QVBoxLayout()
        self._extras_rows.setSpacing(2)
        self._extras_layout.addLayout(self._extras_rows)
        self.extras_note = _muted("", self.extras_box)
        self._extras_layout.addWidget(self.extras_note)
        column.addWidget(self.extras_box)

        self.addons_box = QGroupBox("Addons in this client", inner)
        addons = QVBoxLayout(self.addons_box)
        self.addons_list = QListWidget(self.addons_box)
        self.addons_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        addons.addWidget(self.addons_list, 1)
        self.addons_empty = _muted("No addons in this client yet.", self.addons_box)
        addons.addWidget(self.addons_empty)
        row = QHBoxLayout()
        self.open_addons_button = QPushButton("Open AddOns folder", self.addons_box)
        self.open_addons_button.clicked.connect(self._open_addons_folder)
        row.addWidget(self.open_addons_button)
        row.addStretch(1)
        addons.addLayout(row)
        # The list takes whatever height the column has spare (`_render_addons`
        # moves the stretch to the end of the column while there is no list).
        column.addWidget(self.addons_box, 1)

        self.settings_note = _warning(inner)
        column.addWidget(self.settings_note)
        column.addStretch(0)
        self._right_column = column
        self.right_scroll = self._scroll(inner, "launcher-right")
        return self.right_scroll

    def _build_footer(self) -> QHBoxLayout:
        """The build, "Server tab…" and Close, under the right column, level with PLAY's line.

        Not a full-width strip under both columns as in the mockup: that strip
        was the 60 px the left column lacked at 960x640, where it pushed Log in
        as out of sight under PLAY.
        """
        row = QHBoxLayout()
        row.setSpacing(8)
        self.build_label = _muted("", self, wrap=False)
        row.addWidget(self.build_label)
        row.addStretch(1)
        self.server_tab_button = QPushButton("Server tab…", self)
        self.server_tab_button.setToolTip("Show this server's tab in Yu'lon's main window.")
        self.server_tab_button.clicked.connect(self._server_tab)
        row.addWidget(self.server_tab_button)
        self.close_button = QPushButton("Close", self)
        self.close_button.clicked.connect(self.close)
        row.addWidget(self.close_button)
        return row

    def _bind_entry(self, entry: CatalogEntry, server_dir: Path) -> None:
        """Everything that is a fact about the game: titles, the window modes, the extras."""
        self.setWindowTitle(f"Play — {entry.name}")
        self.banner_title.setText(entry.name)
        self.banner_subtitle.setText(f"{_core(entry)} · {entry.client.version} · {server_dir.name}")
        self.build_label.setText(f"Build {entry.client.build}")
        cfg = entry.client.config_wtf
        self._always = dict(cfg.always) if cfg is not None else {}
        patch = entry.client.exe_patch
        self._borderless = patch is not None and "borderless" in patch.options
        while self._extras_rows.count():
            item = self._extras_rows.takeAt(0)
            layout = item.layout() if item is not None else None
            if layout is not None:
                while layout.count():
                    child = layout.takeAt(0)
                    widget = child.widget() if child is not None else None
                    if widget is not None:
                        widget.deleteLater()
        self.pack_boxes = {}
        self.pack_sizes = {}
        for pack in entry.client.packs:
            if not pack.optional:
                continue
            row = QHBoxLayout()
            check = QCheckBox(pack.label, self.extras_box)
            if pack.description:
                check.setToolTip(pack.description)
            check.clicked.connect(lambda on, pack_id=pack.id: self._pack_toggled(pack_id, on))
            row.addWidget(check)
            row.addStretch(1)
            size = _muted(
                size_text(pack.size_hint) if pack.size_hint else "", self.extras_box, wrap=False
            )
            row.addWidget(size)
            self._extras_rows.addLayout(row)
            self.pack_boxes[pack.id] = check
            self.pack_sizes[pack.id] = size
        self.extras_note.setText(
            "Downloaded from the server makers' site at your next Play. Your own client is "
            "never changed."
            if self.pack_boxes
            else NO_EXTRAS
        )

    # -- reading -----------------------------------------------------------

    def _reload_client(self) -> None:
        play = self._play_dir()
        if play is None:
            return
        self._client_asked += 1
        asked = self._client_asked
        reads = self.reads
        self._client_pending = True

        def work() -> tuple[int, ClientReading | Exception]:
            # The ask's number rides with the answer -- a failure's too -- so a
            # slower earlier read landing after a later one is recognised and
            # dropped, and only the LAST ask's answer ends `_client_pending`.
            try:
                return asked, reads.client(play)
            except Exception as exc:  # noqa: BLE001 - said in `_client_read`
                return asked, exc

        self._jobs(work, self._client_read, self._client_failed)

    @Slot(object)
    def _client_read(self, answer: object) -> None:
        if not isinstance(answer, tuple) or answer[0] != self._client_asked:
            return
        self._client_pending = False
        result = answer[1]
        if isinstance(result, Exception):
            self._client_failed(result)
            return
        if not isinstance(result, ClientReading):
            return
        self._client = result
        self._client_unread = False
        self._reconcile_account()
        self._render()

    @Slot(object)
    def _client_failed(self, exc: object) -> None:
        self._client_pending = False
        self._client_unread = True
        logger.warning(f"the launcher could not read the ready-to-play client: {exc!r}")
        self._render()

    def _reload_server(self) -> None:
        self._server_asked += 1
        asked = self._server_asked
        reads = self.reads
        self._jobs(lambda: (asked, reads.server()), self._server_read, self._server_failed)

    @Slot(object)
    def _server_read(self, answer: object) -> None:
        if not isinstance(answer, tuple) or answer[0] != self._server_asked:
            return
        result = answer[1]
        if not isinstance(result, ServerReading):
            return
        self._server = result
        self._reconcile_account()
        self._render()

    @Slot(object)
    def _server_failed(self, exc: object) -> None:
        logger.warning(f"the launcher could not read the server: {exc!r}")
        self._server = UNREAD
        self._render()

    def _sync_online_timer(self) -> None:
        """Read the count again only while someone can see it and the realm is up."""
        if self.isVisible() and self.realm_badge.status == "running":
            if not self.online_timer.isActive():
                self.online_timer.start()
        else:
            self.online_timer.stop()
            self._online_asked += 1  # a refresh still on its worker is not wanted now

    @Slot()
    def _refresh_online(self) -> None:
        """The timer: the count and uptime only, on the runner like every read.

        Not a whole `_reload_server()`: that re-renders the account box, which
        would rebuild a dropdown under the player once a minute. Its own ask
        number, so it never makes a whole read's answer look stale.
        """
        if not self.isVisible() or self.realm_badge.status != "running":
            return
        self._online_asked += 1
        asked = self._online_asked
        reads = self.reads
        self._jobs(lambda: (asked, reads.server()), self._online_read, self._online_failed)

    @Slot(object)
    def _online_read(self, answer: object) -> None:
        if not isinstance(answer, tuple) or answer[0] != self._online_asked:
            return
        result = answer[1]
        if not isinstance(result, ServerReading) or self._server is None:
            return  # the first whole read brings the count with it
        self._server = replace(self._server, online=result.online)
        self._render_banner()

    @Slot(object)
    def _online_failed(self, exc: object) -> None:
        logger.info(f"the launcher could not read the online count again: {exc!r}")

    def _record(self) -> client_packs.PackRecord | None:
        return self._client.record if self._client is not None else None

    def _reconcile_account(self) -> None:
        """Review Focus 2: a saved account the server no longer has falls back to Ask."""
        if self._client_pending or self._client_unread:
            # The reading held is older than the client (a read is on its way,
            # or the last one failed): judged on it, a valid account could be
            # saved over with "Ask". The next good `_client_read` runs this.
            self._reconcile_owed = True
            return
        if self._auto_saving is not None:
            self._reconcile_owed = True  # judged again once that save has answered
            return
        record, server = self._record(), self._server
        if record is None or server is None or server.accounts is None:
            return
        if record == self._auto_failed:
            # Its "Ask" save failed: said once; tried again when the record changes.
            self._reconcile_owed = False
            return
        saved = record.launcher.get("account")
        if not isinstance(saved, str) or "accountname" in self._fixed_keys():
            self._reconcile_owed = False  # nothing saved to check any more
            return
        known = {client_packs.clean_account(name) for name in server.accounts}
        if saved in known:
            self._reconcile_owed = False  # the fresh reading's account is a valid one
            return
        view = self._alive()
        if view is None or view.play_client_busy() is not None:
            # A Play or a job holds the record: the save would only be refused,
            # and its refusal shown. `_follow_play` asks again once it is idle.
            self._reconcile_owed = True
            return
        self._reconcile_owed = False
        self._stale_account = saved
        self._auto_saving = record
        if not self._save(launcher={"account": None}):
            self._auto_saving = None
            self._auto_save_failed(record)

    def _auto_save_failed(self, record: client_packs.PackRecord) -> None:
        if self._auto_failed != record:
            logger.warning(
                "the launcher could not save Ask in the game for an account the server no "
                "longer has; it tries again when the client's record changes"
            )
        self._auto_failed = record

    # -- following the view ------------------------------------------------

    @Slot(str)
    def _realm_changed(self, status: str) -> None:
        self.realm_badge.set_status(status)
        if status == "running":
            self._reload_server()
        self._sync_online_timer()
        self._render()

    @Slot()
    def _follow_play(self) -> None:
        """The view's Play line and Cancel, and whether a job holds the client now."""
        view = self._alive()
        if view is None:
            return
        text = view.play_label.text()
        self.progress_label.setText(text)
        self.progress_label.setVisible(bool(text))
        self._render_room()
        source = view.play_cancel_button
        self.cancel_button.setVisible(not source.isHidden())
        self.cancel_button.setEnabled(source.isEnabled())
        self.cancel_button.setText(source.text())
        busy = view.play_client_busy() is not None
        if self._was_busy and not busy:
            # A Make…, Refresh, Play, Client options or save just ended: what
            # the client holds may have changed under the window.
            self._reload_client()
        self._was_busy = busy
        if not busy and not self._client_pending and self._reconcile_owed:
            # Only when no client read is on its way, whatever started it: the
            # view can say "idle" more than once (`_play_end(said)` does, through
            # `_say_play` and again itself), and an owed check run on the reading
            # from BEFORE the job could save "Ask" over the account the job left
            # there. `_client_read` runs it on the fresh reading instead.
            self._reconcile_account()
        self._render_enabled()

    @Slot(str)
    def _saved(self, problem: str) -> None:
        """A launcher save has answered: "" saved, else why not."""
        self._saving = False
        attempt, self._auto_saving = self._auto_saving, None
        if attempt is not None and problem:
            self._auto_save_failed(attempt)
        then_play, self._play_after_save = self._play_after_save, False
        self._say(problem)
        self._render_enabled()
        if then_play and not problem and self._has_play():
            if (view := self._pressing()) is not None:
                view.play()  # the press that saved the typed address first

    def _say(self, problem: str) -> None:
        self.settings_note.setText(problem)
        self.settings_note.setVisible(bool(problem))

    # -- rendering ---------------------------------------------------------

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt's own name
        super().showEvent(event)
        if self._reread_on_show and not event.spontaneous():
            # Opened again after a close: the client (an addon added, a Refresh
            # from the Server tab) and the server (an account made) may have
            # changed while nobody looked.
            self._reread_on_show = False
            self._reload_client()
            if self.realm_badge.status == "running":
                self._reload_server()
        self._sync_online_timer()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802 - Qt's own name
        super().hideEvent(event)
        if not event.spontaneous():
            self._reread_on_show = True  # closed, not minimized
        self._sync_online_timer()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt's own name
        super().closeEvent(event)
        if event.isAccepted():
            self.closed.emit()

    def geometry_text(self) -> str:
        """Where the window is and how big, as text `ui_settings` can keep."""
        return base64.b64encode(self.saveGeometry().data()).decode("ascii")

    def restore_geometry_text(self, text: str) -> bool:
        """Put the window back where `geometry_text()` said; False (nothing moved) for rubbish."""
        if not text:
            return False
        try:
            raw = base64.b64decode(text.encode("ascii"), validate=True)
        except (binascii.Error, UnicodeEncodeError, ValueError):
            return False
        return bool(self.restoreGeometry(QByteArray(raw)))

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt's own name
        super().resizeEvent(event)
        compact = self.height() < COMPACT_BELOW
        if compact != self._compact:
            self._compact = compact
            self._render_room()
            self._render_account()

    def _render_room(self) -> None:
        """What the window leaves out below `COMPACT_BELOW`: lines that repeat a tooltip."""
        roomy = not self._compact
        self.client_source_note.setVisible(roomy)
        self.display_note.setVisible(roomy)
        self.extras_note.setVisible(roomy or not self.pack_boxes)
        # A box that would only say "no extras" is left out when room is short.
        self.extras_box.setVisible(roomy or bool(self.pack_boxes))
        # The progress line takes the explanation's place while it says something.
        self.play_reason_label.setVisible(roomy or not self.progress_label.text())
        self._size_addons()

    def _fixed_keys(self) -> set[str]:
        return {key.casefold() for key in self._always}

    def _render(self) -> None:
        has_play = self._has_play()
        self.client_box.setVisible(has_play)
        self.make_box.setVisible(not has_play)
        self._render_banner()
        self._render_client()
        self._render_realm()
        self._render_account()
        self._render_display()
        self._render_extras()
        self._render_addons()
        self._render_enabled()

    def _render_enabled(self) -> None:
        view = self._alive()
        has_play = self._has_play()
        idle = view is not None and view.play_client_busy() is None
        for box in (self.display_box, self.account_box, self.realm_box, self.extras_box):
            box.setEnabled(has_play and idle)
        self.play_button.setEnabled(view is not None and has_play and not self._saving)
        for button in (
            self.refresh_button,
            self.open_folder_button,
            self.delete_button,
            self.open_addons_button,
        ):
            button.setEnabled(view is not None and has_play)
        self.make_button.setEnabled(
            view is not None and view.services.set_play_client_dir is not None
        )
        self.server_tab_button.setEnabled(view is not None)
        if view is None:
            self.play_reason_label.setText(VIEW_GONE)
        elif self._saving and has_play:
            self.play_reason_label.setText(SAVING)
        elif not has_play:
            self.play_reason_label.setText(
                f"Make a ready-to-play client first (“{MAKE_PLAY_CLIENT_LABEL}” above): "
                "PLAY starts the game from it."
            )
        elif self.realm_badge.status == "running":
            self.play_reason_label.setText(
                "Starts World of Warcraft from this server's ready-to-play client."
            )
        else:
            self.play_reason_label.setText(
                "The server is stopped: PLAY starts it, waits for the realm, then starts "
                "the game."
            )

    def _render_banner(self) -> None:
        status = self.realm_badge.status
        server = self._server
        if status == "running":
            online = server.online if server is not None else None
            if online is None:
                text = ""
            else:
                bots = f"{online.bots} bot{'s' if online.bots != 1 else ''}"
                players = f"{online.players} player{'s' if online.players != 1 else ''}"
                text = f"{bots} and {players} online"
                if online.uptime is not None:
                    text += f" · {dashboard.uptime_text(online.uptime)}"
        elif status == "stopping":
            # T188 C5: the tab's Stop holds the badge here; "Play starts the
            # server first" under a stop in progress was a promise to undo it.
            text = STOPPING_BANNER
        elif status in ("starting", "importing", "working", "building", "restarting"):
            text = STARTING_BANNER
        else:
            text = STOPPED_BANNER
        self.online_label.setText(text)

    def _render_client(self) -> None:
        play = self._play_dir()
        reading = self._client
        self.client_path_label.set_path(str(play) if play is not None else "")
        source = reading.source if reading is not None else None
        self.client_source_label.set_path(str(source) if source is not None else "")
        status = self.client_status_label
        status.setToolTip("")
        if reading is None:
            status.setText("Looking at the client…")
            status.setStyleSheet(f"color: {theme.COLOR_TEXT_MUTED};")
        elif reading.stale is None:
            status.setText("Could not compare it with your own client.")
            status.setStyleSheet(f"color: {theme.COLOR_TEXT_MUTED};")
        elif reading.stale:
            status.setText("Your own client was patched since: press Refresh from my client.")
            status.setToolTip("Changed: " + ", ".join(str(rel) for rel in reading.stale))
            status.setStyleSheet(f"color: {theme.COLOR_TEXT_WARNING};")
        else:
            status.setText("✓ Up to date with your own client")
            status.setStyleSheet(f"color: {theme.COLOR_UNCOMMON};")

    def _saved_address(self) -> str | None:
        record = self._record()
        address = record.launcher.get("realm_address") if record is not None else None
        return address if isinstance(address, str) else None

    def _render_realm(self, *, force: bool = False) -> None:
        """The realm box from the record -- but never under the player's typing.

        A read landing or the realm coming up re-renders the window; with the
        box focused that would replace what is being typed (M2). `force` is
        for the box's own answers: an address refused, this computer kept.
        """
        saved = self._saved_address()
        current = saved or PLAY_CLIENT_ADDRESS
        combo = self.realm_combo
        line = combo.lineEdit()
        if not force and (combo.hasFocus() or (line is not None and line.hasFocus())):
            self._render_mismatch(current)
            return
        offered = [PLAY_CLIENT_ADDRESS]
        announced = self._server.announced if self._server is not None else None
        for address in (saved, announced, *self._typed):
            if address and address not in offered:
                offered.append(address)
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(offered)
        combo.setCurrentIndex(offered.index(current))
        combo.setEditText(current)
        combo.blockSignals(False)
        self._render_mismatch(current)

    def _render_mismatch(self, typed: str) -> None:
        """Review Focus 1: the realm row will send the game elsewhere -- say so, and where."""
        announced = self._server.announced if self._server is not None else None
        if announced is None or announced.casefold() == typed.casefold():
            self.realm_mismatch_label.setText("")
            self.realm_mismatch_label.setVisible(False)
            return
        self.realm_mismatch_label.setText(
            f"The game logs in at {typed}, then the server sends it on to {announced}. "
            "PLAY still works; the Networking tab changes that address."
        )
        self.realm_mismatch_label.setVisible(True)

    def _fixed(self, combo: QComboBox, shown: str) -> None:
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(f"{shown} — {SET_BY_SERVER}", None)
        combo.setEnabled(False)
        combo.setToolTip("This server sets it for every player, so it cannot be changed here.")
        combo.blockSignals(False)

    def _fill(self, combo: QComboBox, items: list[tuple[str, str]], current: str) -> None:
        combo.blockSignals(True)
        combo.clear()
        for text, data in items:
            combo.addItem(text, data)
        index = combo.findData(current)
        combo.setCurrentIndex(max(0, index))
        combo.setEnabled(True)
        combo.blockSignals(False)

    def _display(self) -> dict[str, str]:
        record = self._record()
        shown = record.launcher.get("display") if record is not None else None
        return dict(shown) if isinstance(shown, dict) else {}

    def _window_now(self) -> str:
        """The window mode in force: the pick, else Borderless where Client options ticked it."""
        window = self._display().get("window", "")
        record = self._record()
        if (
            not window
            and self._borderless
            and record is not None
            and record.choices["exe_options"].get("borderless") is True
        ):
            return "borderless"  # Client options' box decides while nothing is picked
        return str(window)

    def _render_display(self) -> None:
        fixed_window = _fixed_window(self._always)
        display = self._display()
        if fixed_window is not None:
            self._fixed(self.window_combo, fixed_window)
        else:
            window = self._window_now()
            # Always offered (lead ruling): a saved mode can be cleared again.
            items = [(GAMES_OWN, "")]
            for mode in client_packs.WINDOW_MODES:
                if mode != "borderless" or self._borderless:
                    items.append((WINDOW_LABELS[mode], mode))
            self._fill(self.window_combo, items, window)
        fixed = {key.casefold(): value for key, value in self._always.items()}.get("gxresolution")
        if fixed is not None:
            self._fixed(self.resolution_combo, _resolution_text(fixed))
        else:
            screen = self._screen_size()
            items = [(GAMES_OWN, "")]
            sizes = [f"{w}x{h}" for w, h in COMMON_RESOLUTIONS]
            if screen is not None and screen not in sizes:
                sizes.insert(0, screen)
            saved = display.get("resolution", "")
            if saved and saved not in sizes:
                sizes.append(saved)
            for size in sizes:
                label = _resolution_text(size)
                items.append((f"{label} (this screen)" if size == screen else label, size))
            self._fill(self.resolution_combo, items, saved)

    @staticmethod
    def _screen_size() -> str | None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return None
        size, ratio = screen.size(), screen.devicePixelRatio()
        return f"{round(size.width() * ratio)}x{round(size.height() * ratio)}"

    def _render_account(self) -> None:
        fixed = {key.casefold(): value for key, value in self._always.items()}.get("accountname")
        stale = self._stale_account
        note = PASSWORD_NOTE
        if stale is not None:
            note = (
                f"{stale} is no longer an account on this server, so the game will ask for "
                f"the account name. {PASSWORD_NOTE}"
            )
        self.account_note.setText(note)
        self.account_note.setVisible(stale is not None or not self._compact)
        if fixed is not None:
            self._fixed(self.account_combo, fixed)
            return
        record = self._record()
        has_key = record is not None and "account" in record.launcher
        saved = record.launcher.get("account") if record is not None else None
        names: list[str] = []
        accounts = self._server.accounts if self._server is not None else None
        for name in accounts or ():
            clean = client_packs.clean_account(name)
            if clean is not None and clean not in names:
                names.append(clean)
        if isinstance(saved, str) and saved not in names:
            names.append(saved)  # not read yet, or the read failed: kept, not dropped
        # Always offered (lead ruling), as Window's "The game's own setting" is.
        items = [(GAME_REMEMBERS, ACCOUNT_KEEP)]
        items += [(name, account_data(name)) for name in names]
        items.append((ASK_IN_THE_GAME, ACCOUNT_ASK))
        if not has_key:
            current = ACCOUNT_KEEP
        elif isinstance(saved, str):
            current = account_data(saved)
        else:
            current = ACCOUNT_ASK
        self._fill(self.account_combo, items, current)

    def _render_extras(self) -> None:
        record = self._record()
        view = self._alive()
        unavailable = view.play_unavailable if view is not None else frozenset()
        chosen = record.choices["packs"] if record is not None else {}
        installed = record.packs if record is not None else {}
        entry = view.entry if view is not None else None
        for pack in entry.client.packs if entry is not None else ():
            check = self.pack_boxes.get(pack.id)
            if check is None:
                continue
            gone = pack.id in unavailable
            check.setText(f"{pack.label} (unavailable)" if gone else pack.label)
            check.setChecked(bool(chosen.get(pack.id, pack.default)))
            check.setEnabled(not gone or pack.id in installed)

    def _render_addons(self) -> None:
        names = self._client.addons if self._client is not None else ()
        self.addons_list.clear()
        self.addons_list.addItems(list(names))
        self.addons_list.setVisible(bool(names))
        self.addons_empty.setVisible(not names)
        column = self._right_column
        column.setStretch(column.indexOf(self.addons_box), 1 if names else 0)
        column.setStretch(column.count() - 1, 0 if names else 1)
        self._size_addons()

    def _size_addons(self) -> None:
        """At least three rows of the list showing; one where the window is compact."""
        rows = 1 if self._compact else ADDON_ROWS
        row = self.addons_list.sizeHintForRow(0)
        if row <= 0:
            row = self.addons_list.fontMetrics().height() + 8
        frame = 2 * self.addons_list.frameWidth()
        self.addons_list.setMinimumHeight(rows * row + frame)

    # -- the player's presses ----------------------------------------------

    def _save(
        self,
        *,
        launcher: Mapping[str, Any] | None = None,
        drop: Collection[str] = (),
        packs: Mapping[str, bool] | None = None,
        exe_options: Mapping[str, bool] | None = None,
    ) -> bool:
        """Start a save; False when it was refused (said) and nothing was started."""
        view = self._alive()
        if view is None:
            return False
        self._say("")
        # Before the call: an inline runner answers inside it, through `_saved`.
        self._saving = True
        refusal = view.save_launcher_picks(
            launcher=launcher, drop=drop, packs=packs, exe_options=exe_options
        )
        if refusal is not None:
            self._saving = False
            self._say(refusal)
            self._render()  # back to what the record says
            return False
        return True

    @Slot(int)
    def _window_picked(self, index: int) -> None:
        mode = self.window_combo.itemData(index)
        if not isinstance(mode, str):
            return
        display = self._display()
        old = self._window_now()
        if mode:
            display["window"] = mode
        else:
            display.pop("window", None)
        exe: dict[str, bool] | None = None
        if not mode and self._borderless and old == "borderless":
            exe = {"borderless": False}  # back to the game's own: no borderless left behind
        self._save(launcher={"display": display}, exe_options=exe)

    @Slot(int)
    def _resolution_picked(self, index: int) -> None:
        size = self.resolution_combo.itemData(index)
        if not isinstance(size, str):
            return
        display = self._display()
        if size:
            display["resolution"] = size
        else:
            display.pop("resolution", None)
        self._save(launcher={"display": display})

    @Slot(int)
    def _account_picked(self, index: int) -> None:
        data = self.account_combo.itemData(index)
        if not isinstance(data, str):
            return
        self._stale_account = None
        if data == ACCOUNT_KEEP:
            self._save(drop=("account",))
        elif data == ACCOUNT_ASK:
            self._save(launcher={"account": None})
        else:
            self._save(launcher={"account": data.removeprefix("name:")})

    @Slot()
    def _address_entered(self) -> None:
        """Return in the realm box, or an address picked from its list."""
        if not self.realm_box.isEnabled() or self._saving:
            # Greyed while a save or a job holds the client, or a save of this
            # very address already on its way: not a new entry.
            return
        self._commit_address()

    def _commit_address(self) -> str:
        """Save what the realm box holds if it is new: "none", "saving" or "refused" (said).

        Only when it is not this computer's; an address the game cannot use is
        refused with why, and never saved nor remembered.
        """
        typed = self.realm_combo.currentText().strip()
        saved = self._saved_address()
        self.realm_note.setText("")
        self.realm_note.setVisible(False)
        if typed == (saved or PLAY_CLIENT_ADDRESS):
            self._render_mismatch(typed)
            return "none"
        if not typed or typed == PLAY_CLIENT_ADDRESS:
            return self._this_computer()
        if "realm_address" not in client_packs.clean_launcher({"realm_address": typed}):
            self.realm_note.setText(
                f"“{typed}” is not an address the game can use: letters, digits, dots, dashes "
                "and colons, with a letter or digit and no dot at either end. Nothing was "
                "saved."
            )
            self.realm_note.setVisible(True)
            self._render_realm(force=True)
            return "refused"
        history = ui_settings.recent_addresses([typed, *self._typed])
        if history != self._typed:
            self._typed = history
            self.addresses_changed.emit(list(history))
        return "saving" if self._save(launcher={"realm_address": typed}) else "refused"

    @Slot()
    def _use_this_computer(self) -> None:
        self._this_computer()

    def _this_computer(self) -> str:
        self.realm_note.setText("")
        self.realm_note.setVisible(False)
        if self._saved_address() is None:
            self._render_realm(force=True)
            return "none"
        # Saved as None, not dropped (lead ruling): the next Play writes this computer's
        # address where Config.wtf is the only place for it, and elsewhere takes the typed
        # address's realmList/patchList back out (realmlist.wtf or the catalog's own
        # realmList carries this computer's address there).
        return "saving" if self._save(launcher={"realm_address": None}) else "refused"

    def _pack_toggled(self, pack_id: str, on: bool) -> None:
        self._save(packs={pack_id: on})

    def _pressing(self) -> ControllerView | None:
        """The live view, told that the dialogs of the press about to be made are this window's."""
        view = self._alive()
        if view is not None:
            view.dialog_host = self
        return view

    @Slot()
    def _play(self) -> None:
        """PLAY; an address typed and not yet saved is saved first, then Play starts.

        One press (final review): without this the click's focus change saved the
        address and the Play behind it was refused as busy.
        """
        if not self._has_play() or self._saving or self._alive() is None:
            return
        # Before the commit: an inline runner answers inside it, through `_saved`.
        self._play_after_save = True
        if self._commit_address() != "none":
            if not self._saving:  # refused, or already saved and played
                self._play_after_save = False
            return
        self._play_after_save = False
        if (view := self._pressing()) is not None:
            view.play()

    @Slot()
    def _make(self) -> None:
        if (view := self._pressing()) is not None:
            view.make_play_client()

    @Slot()
    def _refresh(self) -> None:
        if self._has_play() and (view := self._pressing()) is not None:
            view.refresh_play_client()

    @Slot()
    def _delete(self) -> None:
        if self._has_play() and (view := self._pressing()) is not None:
            view.delete_play_client()

    @Slot()
    def _cancel(self) -> None:
        if (view := self._pressing()) is not None:
            view.play_cancel_button.click()

    def _open(self, path: Path | None) -> None:
        if path is None:
            return
        if not self._open_folder(path):
            self._say(f"Yu'lon could not open {path}. Open it from your file manager.")

    @Slot()
    def _open_play_folder(self) -> None:
        self._open(self._play_dir())

    @Slot()
    def _open_addons_folder(self) -> None:
        reading = self._client
        self._open(reading.addons_dir if reading is not None else self._play_dir())

    @Slot()
    def _server_tab(self) -> None:
        view = self._alive()
        if view is not None:
            self.server_tab_requested.emit(view.entry.id, view.services.controller.server_dir)
