"""The friend-server launcher (T807): play on a server Yu'lon did not install.

One window, opened from the toolbar's **Launcher** button. A friend's or a public
server is not installed here, so there is no server folder, no realmlist row and
no Server tab — what the player has is a game, a realm address typed in, and
their own client. The window saves each entry as a named friend server
(`ui.json`, `FriendServer`), makes its ready-to-play client with the same
`play_client.create()` rules the Server tab uses (the archives shared with the
player's own client, and a full copy only when the player agrees to its size),
writes the typed realm into the copy's realmlist files, and starts the game with
`play_launch.launch()` — detached, so closing Yu'lon never closes the game.

**The client folder's ownership is the marker's.** A ready-to-play client is
marked for `friend_server_dir(name)` — the entry's synthetic server identity,
because every play_client API is keyed by (game, server_dir) — and both Play and
Remove hand that identity back to `play_client`, whose marker check refuses a
folder that belongs to another game, another server, or nobody: Remove can
delete only what Yu'lon made here, never the player's own client.

The games offered are the catalog's whose ready-to-play client is complete
without a server of its own: an entry with client packs, a Wow.exe patch or a
`config_wtf` needs that server's install to fetch and lay them (each step is
keyed by the server folder), so Centurion and Unbound stay out of the picker,
and the window says why rather than offering a client that would not work.

Everything that touches the disk runs on a worker (`job_runner`, the
`ThreadedJobRunner` the Server tab uses; tests run `run_inline`), one job at a
time (`_pending`), and every question a player could answer is a seam
(`pick_dir`, `ask_yes`, `launch`, `which`), so a test drives the window without
a modal and without the host.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from yulon import networking, play_client, play_launch, ui_settings
from yulon.catalog.catalog import Catalog, CatalogEntry
from yulon.log import get_logger
from yulon.play_client import PlayClientError
from yulon.play_launch import LaunchRefusal
from yulon.ui.catalog_view import DirPicker, _qt_dir_picker
from yulon.ui.message_box import ask_yes_no, show_information, show_warning
from yulon.ui.widgets.job import JobRunner, threaded_job_runner
from yulon.ui_settings import FriendServer

logger = get_logger(__name__)

STANDARD_AUTH_PORT = 3724
"""The port a typed realm address without one names (the game's standard)."""

_NOT_OFFERED = (
    "Centurion and Unbound need their own server's client packs, so they are not "
    "offered here — install the server to make their clients."
)
"""Said under the game picker, so a game missing from it is a sentence, not a silence."""


def _safe_name(name: str) -> str:
    """`name` as folder-name characters, the way `play_launch.prefix_path` spells a game."""
    folded = re.sub(r"[^A-Za-z0-9_.-]", "_", name).strip("._")
    return folded or "server"


def friend_server_dir(name: str) -> Path:
    """The synthetic server folder a friend server's play_client work is keyed by.

    There is no real server folder: every `play_client`/`play_launch` call is
    keyed by (game, server_dir), and the marker, the Proton prefix and the
    removals all answer the same value for the same entry, so one name is one
    identity. It is never created and never read.
    """
    return Path("friend-servers") / _safe_name(name)


def parse_realm(text: str) -> tuple[str, int] | None:
    """(host, port) from a typed `host[:port]`; None when it is not one.

    `host` is what goes into `set realmlist <host>`: letters, digits, dots,
    dashes and underscores, as a domain name or an IPv4 address is spelled. No
    port means the standard 3724. An IPv6 address is refused — the game's
    realmlist line reaches a v4 auth server, so a typed `::1` is a realm no
    client can ever log in to, and saying so beats storing it.
    """
    value = text.strip()
    host, sep, port = value.rpartition(":")
    if not sep:
        host, port = value, ""
    if sep and not port:
        return None  # a colon with nothing after it is a typo, not the standard port
    if port and not port.isdigit():
        return None
    number = int(port) if port else STANDARD_AUTH_PORT
    if not 1 <= number <= 65535 or not host or re.search(r"\s", host):
        return None
    if not re.fullmatch(r"[A-Za-z0-9._-]+", host):
        return None
    return host, number


def client_without_a_server(entry: CatalogEntry) -> bool:
    """Whether this game's ready-to-play client is complete without its server installed.

    Packs, the Wow.exe patch and `config_wtf` are fetched and laid by the
    server's own tab, keyed by the server folder (T181 b/c); an entry with any
    of them is not offered here.
    """
    client = entry.client
    return not (client.packs or client.exe_patch is not None or client.config_wtf is not None)


# -- the worker bodies (module level: nothing here touches GUI state) ------------


class _Plan:
    """What one Make job planned, for the GUI slot that asks the consent question.

    It carries the entry the player pressed Make on — name, game and realm — because
    the fields stay editable while the plan walks the client: the build that follows
    must write the identity that was planned, not whatever the fields say by then.
    """

    def __init__(
        self,
        original: Path,
        target: Path,
        plan: play_client.BuildPlan,
        *,
        name: str,
        entry: CatalogEntry,
        realm: _Realm,
    ) -> None:
        self.original = original
        self.target = target
        self.plan = plan
        self.name = name
        self.entry = entry
        self.realm = realm


class _Built:
    """What one Make job built, for the GUI slot that finishes it."""

    def __init__(self, target: Path, notes: tuple[str, ...]) -> None:
        self.target = target
        self.notes = notes


class _Realm:
    """A parsed realm address and the line it is saved as."""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port

    @property
    def typed(self) -> str:
        """The address as the player would spell it: the standard port left off."""
        return self.host if self.port == STANDARD_AUTH_PORT else f"{self.host}:{self.port}"


def _plan_work(
    original: Path, target: Path, *, name: str, entry: CatalogEntry, realm: _Realm
) -> _Plan:
    """Walk the client and sort its files, off the GUI thread (the walk is gigabytes)."""
    plan = play_client.plan(original, target, server_dir=friend_server_dir(name))
    return _Plan(original, target, plan, name=name, entry=entry, realm=realm)


class _Removed:
    """What one Remove job did, for the GUI slot that forgets the entry."""

    def __init__(self, name: str, said: str) -> None:
        self.name = name
        self.said = said


def _remove_work(server: FriendServer) -> _Removed:
    """Delete the client Yu'lon made (marker-checked), off the GUI thread: it can be huge."""
    said = play_client.delete(
        server.play_folder, game=server.game, server_dir=friend_server_dir(server.name)
    )
    return _Removed(server.name, said or "")


def _size_text(plan: play_client.BuildPlan) -> str:
    """The size line, in the Server tab's shape: shared, or a full copy."""
    full = f"{(plan.shared_bytes + plan.own_bytes) / 1024**3:.1f} GB"
    if plan.same_volume:
        shared = f"{plan.shared_bytes / 1024**3:.1f} GB"
        return (
            f"{shared} of game files are shared with your client and take no extra space; "
            f"its own files take {plan.own_bytes / 1024**3:.1f} GB."
        )
    return (
        "That folder is on another drive than your client, so its game files cannot be "
        f"shared: it would be a full copy of about {full}."
    )


def _create_work(
    original: Path,
    target: Path,
    *,
    name: str,
    game: str,
    identity: Path,
    full_copy: bool,
    realm: _Realm,
    realmlist_file: str,
    settings_path: Path | None,
) -> _Built:
    """Build the folder, write the realm into it, save the entry — off the GUI thread.

    `allow_full_copy` is what the player agreed to on the GUI thread; a drive
    that turns out not to share even beside the client is refused by
    `create()` itself, with the same size sentence the Server tab shows.
    """
    play_client.create(original, target, game=game, server_dir=identity, allow_full_copy=full_copy)
    notes: list[str] = []
    try:
        networking.write_ready_to_play_realmlists(
            target, realm.host, realmlist_file, auth_port=realm.port
        )
    except OSError as exc:
        notes.append(f"Its realmlist could not be written yet ({exc}); Play writes it again first.")
    saved = ui_settings.remember_friend_server(
        FriendServer(
            name=name,
            game=game,
            realm=realm.typed,
            host=realm.host,
            auth_port=realm.port,
            client_folder=original,
            play_folder=target,
        ),
        settings_path,
    )
    if not saved:
        notes.append(
            "The friend server could not be saved to ui.json; it is forgotten when Yu'lon closes."
        )
    return _Built(target, tuple(notes))


def _play_work(
    server: FriendServer,
    entry: CatalogEntry,
    identity: Path,
    *,
    os_name: str,
    home: Path,
    config_dir: Path,
    which: Callable[[str], str | None],
    launch: Callable[[play_launch.LaunchSpec], None],
) -> play_launch.LaunchSpec:
    """The realmlist again, then the game — the Server tab's `_launch` for a typed realm.

    The realm is the SAVED one: a port the player typed is kept on the line,
    the standard port is left off. The game starts detached and outlives Yu'lon.
    """
    try:
        networking.write_ready_to_play_realmlists(
            server.play_folder, server.host, entry.client.realmlist_file, auth_port=server.auth_port
        )
    except OSError as exc:
        raise LaunchRefusal(
            f"The realmlist in {server.play_folder} could not be written ({exc}), so nothing "
            "was started. Check that you can write to that folder, then press Play again."
        ) from exc
    spec = play_launch.launch_spec(
        server.play_folder,
        game=server.game,
        server_dir=identity,
        os_name=os_name,
        home=home,
        config_dir=config_dir,
        which=which,
    )
    launch(spec)
    return spec


# -- the window -----------------------------------------------------------------


class FriendLauncher(QWidget):
    """The friend-server launcher window: game, realm, own client, Make, Play, Remove."""

    closed = Signal()
    """Emitted on close, so the window's owner can drop its reference."""

    def __init__(
        self,
        catalog: Catalog,
        *,
        os_name: Callable[[], str] | None = None,
        home: Path | None = None,
        config_dir: Path | None = None,
        pick_dir: DirPicker = _qt_dir_picker,
        ask_yes: Callable[[QWidget, str, str], bool] = ask_yes_no,
        launch: Callable[[play_launch.LaunchSpec], None] = play_launch.launch,
        which: Callable[[str], str | None] = shutil.which,
        env: Mapping[str, str] | None = None,
        job_runner: JobRunner | None = None,
        settings_path: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Launcher — play on a friend's server")
        self.setObjectName("friend-launcher")
        self._os_name = os_name if os_name is not None else _detect_os_name
        self._home = home if home is not None else Path.home()
        self._config_dir = config_dir if config_dir is not None else _config_dir_of()
        self._pick_dir = pick_dir
        self._ask_yes = ask_yes
        self._launch = launch
        self._which = which
        self._env = env
        self._jobs: JobRunner = job_runner if job_runner is not None else threaded_job_runner(self)
        self._settings_path = settings_path
        self._catalog = catalog
        self._games = [entry for entry in catalog.games if client_without_a_server(entry)]
        self._pending: tuple[str, str] | None = None
        """The job under way as (what, name), so a second press waits instead of doubling."""
        self._build()
        self.refresh_saved()

    # -- building ----------------------------------------------------------

    def _build(self) -> None:
        box = QVBoxLayout(self)
        intro = QLabel(
            "Play on a server that is not installed here — a friend's, or a public one.\n"
            "Pick the game, type the realm address (for example 192.168.1.20:3724), name it, "
            "and make its ready-to-play client from your own client. Your own client is "
            "never changed.",
            self,
        )
        intro.setWordWrap(True)
        intro.setObjectName("friend-launcher-intro")
        box.addWidget(intro)

        row = QHBoxLayout()
        row.addWidget(QLabel("Game:", self))
        self.game_pick = QComboBox(self)
        self.game_pick.setObjectName("friend-launcher-game")
        for entry in self._games:
            self.game_pick.addItem(f"{entry.name} ({entry.client.version})", entry.id)
        row.addWidget(self.game_pick, 1)
        box.addLayout(row)
        note = QLabel(_NOT_OFFERED, self)
        note.setWordWrap(True)
        note.setObjectName("friend-launcher-note")
        box.addWidget(note)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name:", self))
        self.name_field = QLineEdit(self)
        self.name_field.setObjectName("friend-launcher-name")
        self.name_field.setPlaceholderText("Mark's server")
        name_row.addWidget(self.name_field, 1)
        box.addLayout(name_row)

        realm_row = QHBoxLayout()
        realm_row.addWidget(QLabel("Realm address:", self))
        self.realm_field = QLineEdit(self)
        self.realm_field.setObjectName("friend-launcher-realm")
        self.realm_field.setPlaceholderText("host or host:port, for example 192.168.1.20:3724")
        realm_row.addWidget(self.realm_field, 1)
        box.addLayout(realm_row)

        client_row = QHBoxLayout()
        client_row.addWidget(QLabel("Your client folder:", self))
        self.client_field = QLineEdit(self)
        self.client_field.setObjectName("friend-launcher-client")
        client_row.addWidget(self.client_field, 1)
        browse = QPushButton("Browse…", self)
        browse.setObjectName("friend-launcher-browse")
        browse.clicked.connect(self._browse_for_client)
        client_row.addWidget(browse)
        box.addLayout(client_row)

        self.make_button = QPushButton("Make client", self)
        self.make_button.setObjectName("friend-launcher-make")
        self.make_button.setToolTip(
            "Make this friend server's ready-to-play client from your own client. "
            "The game files are shared with your own client where the drive allows, "
            "and a full copy is made only once you agree to its size."
        )
        self.make_button.clicked.connect(self.make_client)
        box.addWidget(self.make_button)

        self.saved_list = QListWidget(self)
        self.saved_list.setObjectName("friend-launcher-saved")
        box.addWidget(self.saved_list, 1)

        saved_row = QHBoxLayout()
        self.play_button = QPushButton("Play", self)
        self.play_button.setObjectName("friend-launcher-play")
        self.play_button.setToolTip("Start the game against this friend server's realm.")
        self.play_button.clicked.connect(self.play_selected)
        saved_row.addWidget(self.play_button)
        self.remove_button = QPushButton("Remove", self)
        self.remove_button.setObjectName("friend-launcher-remove")
        self.remove_button.setToolTip(
            "Forget this friend server and delete the ready-to-play client Yu'lon made "
            "for it. Your own client folder is never touched."
        )
        self.remove_button.clicked.connect(self.remove_selected)
        saved_row.addWidget(self.remove_button)
        saved_row.addStretch(1)
        box.addLayout(saved_row)

        self.status = QLabel("", self)
        self.status.setObjectName("friend-launcher-status")
        self.status.setWordWrap(True)
        box.addWidget(self.status)

    # -- the saved list ------------------------------------------------------

    def refresh_saved(self) -> None:
        """Re-read the saved friend servers into the list, keeping the selection.

        The list is rebuilt rather than updated in place, so a Make or a Remove
        that just rewrote the file does not leave a stale row — and the row the
        player had is put back by name, or a Make's fresh entry would be there
        and unselected just when Play is the next press.
        """
        selected = self._selected_name()
        self.saved_list.clear()
        for server in ui_settings.friend_servers(self._settings_path):
            item = QListWidgetItem(f"{server.name} — {server.realm}", self.saved_list)
            item.setData(Qt.ItemDataRole.UserRole, server.name)
            if server.name == selected:
                item.setSelected(True)
        if selected is None and self.saved_list.count():
            # Nothing was selected before this rebuild (a Make just added the
            # first entry): the row Play would start is the one selected, or
            # the list is a dead end until the player clicks it.
            self.saved_list.setCurrentRow(0)
        self._sync_buttons()

    def _selected_name(self) -> str | None:
        item = self.saved_list.currentItem()
        return None if item is None else str(item.data(Qt.ItemDataRole.UserRole))

    def _entry_of(self, game: str) -> CatalogEntry | None:
        return next((one for one in self._games if one.id == game), None)

    def _saved(self, name: str) -> FriendServer | None:
        return next(
            (one for one in ui_settings.friend_servers(self._settings_path) if one.name == name),
            None,
        )

    def _say(self, text: str) -> None:
        self.status.setText(text)

    def _set_pending(self, value: tuple[str, str] | None) -> None:
        """Record the job under way, and lock the buttons that would start another."""
        self._pending = value
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        idle = self._pending is None
        has_saved = bool(self.saved_list.count())
        self.make_button.setEnabled(idle)
        self.play_button.setEnabled(idle and has_saved)
        self.remove_button.setEnabled(idle and has_saved)

    # -- make ---------------------------------------------------------------

    def _browse_for_client(self) -> None:
        chosen = self._pick_dir(self, "Select your own World of Warcraft client folder", self._home)
        if chosen is not None:
            self.client_field.setText(str(chosen))

    @Slot()
    def make_client(self) -> None:
        """Make (or rebuild) the selected game's ready-to-play client for this entry.

        The entry is named and its realm validated first; the plan runs on the
        worker (it walks the whole client), the size-consent question is asked
        on the GUI thread with the plan's answer — a full copy of a stated size
        is made only when the player says so, the Server tab's own rule — and
        the build runs as the second job.
        """
        if self._pending is not None:
            return
        entry = self._entry_of(str(self.game_pick.currentData()))
        name = self.name_field.text().strip()
        if entry is None:
            self._say("Pick the game first.")
            return
        if not name:
            self._say("Give this friend server a name first.")
            return
        realm = parse_realm(self.realm_field.text())
        if realm is None:
            self._say(
                "That realm address is not one the game can reach: type it as "
                "host or host:port (for example 192.168.1.20 or 192.168.1.20:3724). "
                "Nothing was made."
            )
            return
        clash = self._name_clash(name)
        if clash is not None:
            self._say(
                f'"{name}" cannot be used next to "{clash}": names are saved as folder names '
                "with odd characters swapped for underscores, so the two would share one "
                "ready-to-play client and removing one would delete the other's. "
                "Give this one a different name. Nothing was made."
            )
            return
        original = Path(self.client_field.text().strip())
        if not original.is_dir():
            picked = self._pick_dir(
                self, "Select your own World of Warcraft client folder", self._home
            )
            if picked is None:
                return
            original = picked
        target = self._target_for(entry, name, original)
        self._set_pending(("plan", name))
        self._say(f"Checking your client {original}…")
        self._jobs(
            lambda: _plan_work(original, target, name=name, entry=entry, realm=_Realm(*realm)),
            self._planned,
            self._failed,
        )

    def _name_clash(self, name: str) -> str | None:
        """The saved entry whose folder identity `name` would share, or None.

        `friend_server_dir` folds a name to folder-name characters, so "Mark's server"
        and "Mark_s server" are one identity: one marker, one prefix, one delete. The
        same name again is a rebuild of its own entry, not a clash.
        """
        identity = friend_server_dir(name).name.casefold()
        for one in ui_settings.friend_servers(self._settings_path):
            if one.name != name and friend_server_dir(one.name).name.casefold() == identity:
                return one.name
        return None

    def _target_for(self, entry: CatalogEntry, name: str, original: Path) -> Path:
        """Where this entry's ready-to-play client lives, or its default next to the client.

        An entry the player already made keeps the folder it has, so Play and
        Remove keep working; a new one gets `default_target`'s sibling, with the
        OneDrive question pinned to this machine's answers (`os_name` is the
        window's seam, never read off the host by a test).
        """
        saved = self._saved(name)
        if saved is not None and saved.game == entry.id:
            return saved.play_folder
        return play_client.default_target(
            original,
            entry.name,
            friend_server_dir(name),
            env=self._env,
            os_name=self._os_name(),
        )

    @Slot(object)
    def _planned(self, result: object) -> None:
        """The plan answered: state the size, and ask the full-copy consent when it is one."""
        if not isinstance(result, _Plan):  # pragma: no cover - defensive
            self._failed(result)
            return
        plan = result.plan
        # The plan's own entry, never the fields' now: they stay editable while it runs.
        entry = result.entry
        name = result.name
        full_copy = False
        if not plan.same_volume:
            if not self._ask_yes(
                self,
                "Full copy needed",
                f"{_size_text(plan)}\n\nMake the full copy at {result.target}?",
            ):
                self._set_pending(None)
                self._say("No copy was made: it would be a full copy, and you said no.")
                return
            full_copy = True
        else:
            self._say(f"{_size_text(plan)} Making it…")
        self._set_pending(("create", name))
        self._jobs(
            lambda: _create_work(
                result.original,
                result.target,
                name=name,
                game=entry.id,
                identity=friend_server_dir(name),
                full_copy=full_copy,
                realm=result.realm,
                realmlist_file=entry.client.realmlist_file,
                settings_path=self._settings_path,
            ),
            self._made,
            self._failed,
        )

    @Slot(object)
    def _made(self, result: object) -> None:
        """Built: the record is saved, the list refreshed, the player told where it is."""
        self._set_pending(None)
        if not isinstance(result, _Built):  # pragma: no cover - defensive
            self._failed(result)
            return
        for note in result.notes:
            show_information(self, "Friend server", note)
        self.refresh_saved()
        self._say(f"The ready-to-play client is at {result.target}. Press Play to start the game.")

    @Slot(object)
    def _failed(self, exc: object) -> None:
        """One job's refusal or failure, said as it is — every play_client refusal names
        the folder and what to do, and says what was left as it was."""
        self._set_pending(None)
        if isinstance(exc, (PlayClientError, LaunchRefusal)):
            said = str(exc)
            if isinstance(exc, PlayClientError) and exc.flags_lost:
                said += " " + play_client.flags_lost_warning(exc.flags_lost)
        else:
            said = f"{type(exc).__name__}: {exc}"
        show_warning(self, "Friend server", said)
        self.refresh_saved()

    # -- play / remove -------------------------------------------------------

    @Slot()
    def play_selected(self) -> None:
        """Start the selected friend server's game against its saved realm.

        The ready-to-play client must still be there and still be this entry's
        — `read_marker()` is the proof — and the launch is one job, so a double
        press cannot start two games.
        """
        if self._pending is not None:
            return
        name = self._selected_name()
        if name is None:
            return
        server = self._saved(name)
        if server is None:
            self._say("That friend server is gone from the list; pick another.")
            return
        entry = self._entry_of(server.game)
        if entry is None:
            self._say(f"{server.game} is not one of the launcher's games any more.")
            return
        identity = friend_server_dir(name)
        marker = play_client.read_marker(server.play_folder)
        if marker is None or marker.game != server.game or marker.server_dir != identity:
            self._say(
                f"The ready-to-play client at {server.play_folder} is not this friend "
                "server's (it is gone, or it was made for another game or server). "
                "Make it again, or Remove the entry."
            )
            return
        self._set_pending(("play", name))
        self._say("Writing the realm address and starting the game…")
        self._jobs(
            lambda: _play_work(
                server,
                entry,
                identity,
                os_name=self._os_name(),
                home=self._home,
                config_dir=self._config_dir,
                which=self._which,
                launch=self._launch,
            ),
            self._launched,
            self._failed,
        )

    @Slot(object)
    def _launched(self, spec: object) -> None:
        self._set_pending(None)
        if not isinstance(spec, play_launch.LaunchSpec):  # pragma: no cover - defensive
            self._failed(spec)
            return
        logger.info(f"friend server: started {' '.join(spec.argv)} in {spec.cwd}")
        name = self._selected_name()
        server = self._saved(name) if name is not None else None
        realm = server.realm if server is not None else "the realm"
        self._say(f"The game is starting against {realm}.")

    @Slot()
    def remove_selected(self) -> None:
        """Forget the selected friend server, and delete only the client Yu'lon made.

        The question first (a delete is not undone), then `play_client.delete()`
        with this entry's synthetic identity: its marker check refuses a folder
        that was not made for this friend server — no marker, another game's,
        another server's — and the player's own client is never marked at all,
        so it can never be the folder a Remove takes away.
        """
        if self._pending is not None:
            self._say(
                f"{self._pending[1]} is still being worked on; "
                "press Remove again once that has finished."
            )
            return
        name = self._selected_name()
        if name is None:
            return
        server = self._saved(name)
        if server is None:
            self._say("That friend server is gone from the list; pick another.")
            return
        if not self._ask_yes(
            self,
            "Remove friend server",
            f"Forget {name} and delete the ready-to-play client Yu'lon made for it at "
            f"{server.play_folder}?\n\nYour own client at {server.client_folder} is not touched.",
        ):
            return
        self._set_pending(("remove", name))
        self._say(f"Removing {name}…")
        self._jobs(lambda: _remove_work(server), self._removed, self._failed)

    @Slot(object)
    def _removed(self, result: object) -> None:
        """The client is gone: the entry is forgotten, and the player is told."""
        self._set_pending(None)
        if not isinstance(result, _Removed):  # pragma: no cover - defensive
            self._failed(result)
            return
        if not ui_settings.forget_friend_server(result.name, self._settings_path):
            self._say(
                f"{result.name} was deleted, but the list could not be saved; it may reappear."
            )
        else:
            self._say(result.said or f"{result.name} was removed.")
        self.refresh_saved()

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt's name
        self.closed.emit()
        super().closeEvent(event)


def _detect_os_name() -> str:
    from yulon import platform

    return platform.detect()


def _config_dir_of() -> Path:
    from yulon import platform

    return platform.config_dir()
