"""Yu'lon's own server account is there and its password is not (T386), on all five games.

Seen live on Tortoise (2026-10-05): a fresh data folder against a server that
already had the app's account. The Start's settle minted a password, `create`
kept the row's own one (as it must: it never re-passwords a row that exists),
and the channel sat at "waiting to be proved" -- with no Repair, and a Refresh
that asked nothing.

Driven through the real tab, the real `InstallChannel` each game's own factory
builds (its real `create`, `reset` and account lookup over the writer's real
SQL), the real `SoapChannel` and the real `soap.execute`. The auth database is
an in-memory SQLite one laid out like each game's own, evaluating the
statements this app sends; the SOAP server is a stand-in on a loopback port
that lets a request in only when its password matches what that database
holds for the account, by the game's own hash. So "the channel proved itself"
here means the password Yu'lon saved is the one the row now has.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import re
import sqlite3
import subprocess
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests.support_player_text import command_faults, player_text_faults
from yulon import channel as channel_module
from yulon import channel_setup, passwordcheck, runner, soap
from yulon.apply import ApplyError
from yulon.catalog import composegen
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.controller_wow_wotlk import accounts as writer
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import ControllerServices, ControllerView
from yulon.ui.widgets.job import run_inline

CATALOG = load_catalog()
FAMILIES = ["wow-wotlk", "wow-tbc", "wow-vanilla", "wow-tortoise", "wow-centurion"]
FORGOTTEN = "Forgotten_99"
"""The password the app's row holds and no file on this machine remembers."""
LOST_LINE = "Yu'lon lost the password for its own server account. Repair sets a new one."


# -- an auth database laid out like each game's own ----------------------------


_SCHEMA = {
    "azerothcore": (
        "CREATE TABLE account (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE,"
        " salt BLOB, verifier BLOB, expansion INTEGER, reg_mail TEXT, email TEXT, joindate TEXT)",
        "CREATE TABLE account_access (id INTEGER, gmlevel INTEGER, RealmID INTEGER,"
        " PRIMARY KEY (id, RealmID))",
    ),
    "trinitycore": (
        "CREATE TABLE account (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE,"
        " salt BLOB, verifier BLOB, reg_mail TEXT, email TEXT, joindate TEXT)",
        "CREATE TABLE account_access (AccountID INTEGER, SecurityLevel INTEGER,"
        " RealmID INTEGER, PRIMARY KEY (AccountID, RealmID))",
    ),
    "mangos_srp6": (
        "CREATE TABLE account (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE,"
        " v TEXT, s TEXT, joindate TEXT, gmlevel INTEGER DEFAULT 0)",
    ),
    "mangos_sha": (
        "CREATE TABLE account (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE,"
        " sha_pass_hash TEXT, v TEXT, s TEXT, joindate TEXT, `rank` INTEGER DEFAULT 0)",
    ),
}


class _Auth:
    """The SQL seam, answered by SQLite evaluating the statement that was sent.

    MySQL spellings the writer uses are translated and nothing else: the
    `_utf8mb4 X'…'` text literal becomes a quoted string, `NOW()`, `IF()` and
    `GREATEST()` are registered, and `ON DUPLICATE KEY UPDATE` becomes SQLite's upsert.
    """

    def __init__(self, entry: CatalogEntry) -> None:
        self.entry = entry
        self.scheme = entry.accounts.scheme
        assert self.scheme is not None
        self.lock = threading.Lock()
        self.down = False
        """True: the database container is not running, so every statement fails."""
        self.writes: list[str] = []
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.create_function("NOW", 0, lambda: "2026-10-06 00:00:00")
        self.conn.create_function("IF", 3, lambda test, yes, no: yes if test else no)
        self.conn.create_function("GREATEST", -1, max)
        for statement in _SCHEMA[self.scheme]:
            self.conn.execute(statement)
        self.conn.execute("CREATE TABLE realmlist (id INTEGER)")
        self.conn.execute("INSERT INTO realmlist VALUES (1)")
        self.conn.execute(
            "CREATE TABLE realmcharacters (realmid INTEGER, acctid INTEGER," " numchars INTEGER)"
        )

    def seed(self, name: str, password: str, level: int) -> None:
        """A row as the game's own `account create` would leave it, at `level`."""
        name = name.upper()
        with self.lock:
            if self.scheme in ("azerothcore", "trinitycore"):
                salt = writer.make_salt()
                self.conn.execute(
                    "INSERT INTO account (username, salt, verifier) VALUES (?, ?, ?)",
                    (name, salt, writer.verifier_for(name, password, salt)),
                )
                (account_id,) = self.conn.execute(
                    "SELECT id FROM account WHERE username = ?", (name,)
                ).fetchone()
                self.conn.execute(
                    "INSERT INTO account_access VALUES (?, ?, -1)", (account_id, level)
                )
            elif self.scheme == "mangos_srp6":
                s_hex, v_hex = writer.mangos_srp6_credentials(name, password)
                self.conn.execute(
                    "INSERT INTO account (username, v, s, gmlevel) VALUES (?, ?, ?, ?)",
                    (name, v_hex, s_hex, level),
                )
            else:
                self.conn.execute(
                    "INSERT INTO account (username, sha_pass_hash, `rank`) VALUES (?, ?, ?)",
                    (name, writer.mangos_password_hash(name, password), level),
                )

    def accepts(self, name: str, password: str) -> bool:
        """Whether this password logs `name` in, by the game's own hash of the row."""
        with self.lock:
            if self.scheme in ("azerothcore", "trinitycore"):
                row = self.conn.execute(
                    "SELECT salt, verifier FROM account WHERE UPPER(username) = ?",
                    (name.upper(),),
                ).fetchone()
                return row is not None and writer.verifier_for(name, password, row[0]) == row[1]
            if self.scheme == "mangos_srp6":
                row = self.conn.execute(
                    "SELECT s, v FROM account WHERE UPPER(username) = ?", (name.upper(),)
                ).fetchone()
                return (
                    row is not None
                    and passwordcheck.matches("mangos_srp6", name, password, row) is True
                )
            row = self.conn.execute(
                "SELECT sha_pass_hash FROM account WHERE UPPER(username) = ?", (name.upper(),)
            ).fetchone()
            return row is not None and row[0] == passwordcheck.sha_hash(name, password)

    def rows(self) -> dict[str, str]:
        """Every account's whole row, by name: the before-and-after snapshot."""
        with self.lock:
            cursor = self.conn.execute("SELECT * FROM account")
            return {
                str(row[1]): hashlib.sha256(repr(row).encode()).hexdigest()
                for row in cursor.fetchall()
            }

    def _sql(self, statement: str) -> str:
        if self.down:
            raise ApplyError("the database container is not running")
        translated = re.sub(
            r"_utf8mb4 X'([0-9A-F]*)'",
            lambda m: "'" + bytes.fromhex(m.group(1)).decode("utf-8").replace("'", "''") + "'",
            statement,
        )
        return translated.replace("ON DUPLICATE KEY UPDATE", "ON CONFLICT DO UPDATE SET")

    def run_statement(self, _db: object, statement: str) -> None:
        translated = self._sql(statement)
        self.writes.append(statement)
        with self.lock:
            self.conn.execute(translated)

    def query(self, _db: object, statement: str) -> str:
        translated = self._sql(statement)
        with self.lock:
            rows = self.conn.execute(translated).fetchall()
        return "".join(
            "\t".join("NULL" if v is None else str(v) for v in row) + "\n" for row in rows
        )


# -- a SOAP server that checks the password against that database -------------


_ENVELOPE = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/">'
    "<SOAP-ENV:Body><ns1:executeCommandResponse><result>{text}</result>"
    "</ns1:executeCommandResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>\n"
)


@dataclass
class _Soap:
    """The world's command channel. `loading`: it takes the request and says nothing."""

    auth: _Auth
    loading: bool = False
    asked_as: list[str] = field(default_factory=list)
    port: int = 0

    def handler(self) -> type[http.server.BaseHTTPRequestHandler]:
        stand_in = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - the stdlib spells it this way
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if stand_in.loading:
                    # What a world busy logging its bots in looks like from
                    # here: a connection, and nothing back.
                    self.close_connection = True
                    return
                header = self.headers.get("Authorization", "")
                name, _, password = (
                    base64.b64decode(header.removeprefix("Basic ")).decode().partition(":")
                )
                stand_in.asked_as.append(name)
                if not stand_in.auth.accepts(name, password):
                    self.send_response(401)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                payload = _ENVELOPE.format(text="Players online: 0").encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/xml; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args: object) -> None:
                """Quiet."""

        return _Handler


# -- the tab, over each game's own factory ------------------------------------


@dataclass
class _Box:
    entry: CatalogEntry
    auth: _Auth
    wire: _Soap
    view: ControllerView
    app: str
    install_id: str


@pytest.fixture(autouse=True)
def _inline_jobs(qapp: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)
    monkeypatch.setattr(
        runner, "run", lambda cmd, *a, **k: subprocess.CompletedProcess(cmd, 0, "", "")
    )


@pytest.fixture(params=FAMILIES)
def box(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[_Box]:
    yield from _box(request.param, tmp_path, monkeypatch)


def _box(game: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Box]:
    entry = CATALOG.get(game)
    operations = entry.operations
    level = entry.accounts.level
    assert operations is not None and operations.gm_level is not None and level is not None
    server_dir = tmp_path / entry.id
    server_dir.mkdir()
    if entry.install.password.file:
        (server_dir / entry.install.password.file).write_text("hunter2", encoding="utf-8")
    install_id = composegen.install_id(server_dir)
    app = channel_setup.account_name(install_id)

    auth = _Auth(entry)
    auth.seed(app, FORGOTTEN, operations.gm_level)
    auth.seed("ALICE", "alice_pw_1", 0)
    auth.seed("RNDBOT1", "bot_pw_1", 0)
    wire = _Soap(auth)
    httpd = http.server.HTTPServer(("127.0.0.1", 0), wire.handler())
    wire.port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    real = channel_module.SoapChannel

    def to_the_stand_in(*, endpoint: soap.Endpoint, **kwargs: object) -> object:
        assert endpoint.port == operations.port, "the factory addressed the entry's own port"
        moved = soap.Endpoint(
            host=endpoint.host,
            port=wire.port,
            account=endpoint.account,
            password=endpoint.password,
            namespace=endpoint.namespace,
        )
        return real(endpoint=moved, timeout=5.0, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(channel_module, "SoapChannel", to_the_stand_in)
    monkeypatch.setattr(controller_view_module, "_sql_for", lambda *_a, **_k: auth)
    services = ControllerServices.for_entry(entry, server_dir)
    assert services.channel_setup is not None
    services.update_to_latest = None  # Refresh asks GitHub for news otherwise
    view = ControllerView(entry, services, status_poll_ms=0)
    try:
        yield _Box(entry, auth, wire, view, app, install_id)
    finally:
        view.shutdown()
        httpd.shutdown()
        httpd.server_close()


def _line(box: _Box) -> str:
    return box.view.channel_label.text()


def _offers_repair(box: _Box) -> bool:
    return not box.view.repair_channel_button.isHidden()


def _saved(box: _Box) -> soap.Endpoint | None:
    return channel_setup.load_credential(box.entry.id, box.install_id)


# -- the tests ------------------------------------------------------------------


def test_the_start_offers_repair_when_the_own_account_has_no_saved_password(box: _Box) -> None:
    """The live case: a fresh data folder, the row already there, the world still loading.

    No answer from the server is needed to know the password is lost: the
    account writer said the row was already there and kept its own password.
    """
    before = box.auth.rows()
    box.wire.loading = True
    assert "not set up yet" in _line(box), "the ground: nothing saved on this machine"

    box.view._settle_the_channel()  # what the Start press runs when it is done

    assert _line(box) == f"Command channel: {LOST_LINE}"
    assert _offers_repair(box)
    assert box.auth.rows() == before, "a Start rewrote nobody's password"
    assert _saved(box) is None
    assert not channel_setup.pending_path(
        box.entry.id, box.install_id
    ).exists(), "a password the row does not have was kept as though it did"
    assert player_text_faults(box.view) == []
    assert command_faults(_line(box)) == []


def test_repair_sets_a_new_password_for_the_own_account_only_and_proves_it(box: _Box) -> None:
    box.view._settle_the_channel()
    assert _offers_repair(box), "the ground: Repair is offered"
    before = box.auth.rows()

    box.view.repair_channel_button.click()

    assert _line(box).startswith(f"Command channel: verified as {box.app} at ")
    assert not _offers_repair(box)
    saved = _saved(box)
    assert saved is not None and saved.account == box.app
    assert box.auth.accepts(box.app, saved.password), "the saved password is the row's"
    assert not box.auth.accepts(box.app, FORGOTTEN)
    after = box.auth.rows()
    assert after[box.app] != before[box.app]
    assert {k: v for k, v in after.items() if k != box.app} == {
        k: v for k, v in before.items() if k != box.app
    }, "Repair changed another account"
    assert box.wire.asked_as[-1] == box.app
    assert box.view.problem_label.text() == ""


def test_repair_while_the_world_loads_keeps_the_password_and_refresh_proves_it(
    box: _Box,
) -> None:
    """The reset is a database write, so it works before the world answers; the proof waits."""
    box.view._settle_the_channel()
    box.wire.loading = True

    box.view.repair_channel_button.click()

    assert "waiting to be proved" in _line(box)
    assert _saved(box) is None, "nothing is saved as working before the server said so"
    pending = channel_setup.load_pending(box.entry.id, box.install_id)
    assert pending is not None and box.auth.accepts(
        box.app, pending.password
    ), "the new password was kept for the next ask"

    box.wire.loading = False
    box.view.recheck()  # the Refresh button

    assert _line(box).startswith(f"Command channel: verified as {box.app} at ")
    saved = _saved(box)
    assert saved is not None and saved.password == pending.password


def test_repair_with_the_database_down_says_so_and_changes_nothing(box: _Box) -> None:
    box.view._settle_the_channel()
    before = box.auth.rows()
    box.auth.down = True

    box.view.repair_channel_button.click()

    assert _offers_repair(box), "Repair is still there to press again"
    assert "Start the server" in _line(box)
    assert box.view.problem_label.text() == ""
    assert player_text_faults(box.view) == []
    box.auth.down = False
    assert box.auth.rows() == before
    assert _saved(box) is None


def test_refresh_finds_the_own_account_and_offers_repair(box: _Box) -> None:
    """A Refresh (and the look a tab takes on opening) reads the row; it writes nothing."""
    before = box.auth.rows()

    box.view.recheck()

    assert _line(box) == f"Command channel: {LOST_LINE}"
    assert _offers_repair(box)
    assert box.auth.rows() == before
    assert box.auth.writes == [], "a look wrote to the auth database"


def test_refresh_on_a_server_without_the_account_creates_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control: no row, so there is nothing to repair and a Refresh makes none."""
    for box in _box("wow-tortoise", tmp_path, monkeypatch):
        with box.auth.lock:
            box.auth.conn.execute("DELETE FROM account WHERE username = ?", (box.app,))
        box.view.recheck()
        assert "not set up yet" in _line(box)
        assert not _offers_repair(box)
        assert box.auth.writes == []


def test_a_start_that_lands_while_the_world_loads_asks_again_a_minute_later(
    box: _Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No row yet, so the Start's settle makes one; the world is loading, so it waits.

    One ask after a Start used to be all there was: the line sat at "waiting
    to be proved" until something else asked. An install already asks once
    more a minute later; a Start now does the same, and only while it waits.
    """
    with box.auth.lock:
        box.auth.conn.execute("DELETE FROM account WHERE username = ?", (box.app,))
    scheduled: list[tuple[int, object]] = []
    monkeypatch.setattr(
        controller_view_module.QTimer,
        "singleShot",
        lambda ms, *call: scheduled.append((ms, call[-1])),
    )
    box.wire.loading = True

    box.view._server_action_done(None)  # the Start press, finished

    assert "waiting to be proved" in _line(box)
    again = [fn for ms, fn in scheduled if ms == controller_view_module._POST_INSTALL_RESETTLE_MS]
    assert len(again) == 1, "a Start that found the world loading never asked again"

    box.wire.loading = False
    again[0]()  # type: ignore[operator]

    assert _line(box).startswith(f"Command channel: verified as {box.app} at ")
    saved = _saved(box)
    assert saved is not None and box.auth.accepts(box.app, saved.password)
    assert not _offers_repair(box)


def test_a_repair_that_waits_for_the_world_outlives_a_close_of_yulon(
    box: _Box, tmp_path: Path
) -> None:
    """A saved password the server stopped taking, a Repair while the world loads, a close.

    The reset changed the row, so the saved credential is wrong from that
    moment. Left on disk it is read first at the next launch, the tab calls a
    stale password "verified", and the world's first answer is a refusal and
    a second Repair.
    """
    operations = box.entry.operations
    assert operations is not None and operations.port is not None
    channel_setup.save_credential(
        channel_setup.Verified(account=box.app, password="Stale_pw_1", at="2026-10-01 00:00 UTC"),
        game=box.entry.id,
        install_id=box.install_id,
        host="127.0.0.1",
        port=operations.port,
        namespace=operations.namespace or "urn:AC",
    )

    def opened() -> ControllerView:
        services = ControllerServices.for_entry(box.entry, tmp_path / box.entry.id)
        services.update_to_latest = None
        return ControllerView(box.entry, services, status_poll_ms=0)

    first = opened()
    first.recheck()
    assert _offers_repair_on(first), "the ground: the stale password is refused"
    box.wire.loading = True
    first.repair_channel_button.click()
    assert "waiting to be proved" in first.channel_label.text()
    first.shutdown()

    second = opened()
    try:
        second.recheck()
        assert "waiting to be proved" in second.channel_label.text(), second.channel_label.text()
        box.wire.loading = False
        second.recheck()
        assert second.channel_label.text().startswith(f"Command channel: verified as {box.app} at ")
        saved = _saved(box)
        assert saved is not None and box.auth.accepts(box.app, saved.password)
        assert not _offers_repair_on(second)
    finally:
        second.shutdown()


def _offers_repair_on(view: ControllerView) -> bool:
    return not view.repair_channel_button.isHidden()


# -- a look from Idle and a settle from Idle, at once (cold review) -------------


def _racing(tmp_path: Path) -> tuple[object, list[object]]:
    """A WotLK install over `test_channel_pending`'s world, with the row lookup wired."""
    from tests.test_channel_pending import _launch, _World

    world = _World()
    world.loading = False
    looks: list[object] = []
    setup = _launch(tmp_path, world)
    setup._exists = lambda name: name in world.rows
    return (world, setup), looks


def test_a_refresh_during_a_start_settle_does_not_call_the_new_row_lost(tmp_path: Path) -> None:
    """The settle's `create` has made the row and its round trip is still out.

    The row is there and nothing is saved yet, which is exactly what a lost
    password looks like from a look alone. A Refresh landing then must not
    say so, nor leave a Repair that would reset a password that works.
    """
    (world, setup), looks = _racing(tmp_path)
    made = world.create

    def create_then_refresh(name: str, password: str, level: int) -> None:
        made(name, password, level)
        looks.append(setup.check())  # the Refresh press, on another thread

    world.create = create_then_refresh
    setup._create = create_then_refresh

    after = setup.settle()

    assert isinstance(after, channel_setup.Verified)
    assert not any(isinstance(look, channel_setup.Refused) for look in looks), looks
    assert isinstance(setup.setup_state(), channel_setup.Verified)


def test_a_settle_that_finishes_during_the_look_is_not_overwritten(tmp_path: Path) -> None:
    """The look asked the database, and the settle proved the row before it answered."""
    (world, setup), _looks = _racing(tmp_path)

    def exists_while_the_settle_lands(name: str) -> bool:
        setup.settle()  # the Start's settle, finishing on another thread
        return name in world.rows

    setup._exists = exists_while_the_settle_lands

    looked = setup.check()

    assert isinstance(looked, channel_setup.Verified), looked
    assert isinstance(setup.setup_state(), channel_setup.Verified)
    assert world.resets == 0


# -- after "Repair the database…" the account is gone (T423) --------------------

GONE_LINE = "Yu'lon's own server account is not in the database any more. Repair makes it again."


def _proved(box: _Box) -> None:
    """The ground: a channel that was proved on this machine, so a credential is saved."""
    box.view._settle_the_channel()
    box.view.repair_channel_button.click()
    assert _line(box).startswith(f"Command channel: verified as {box.app} at ")


def _drop_the_account(box: _Box) -> None:
    """What re-importing the database does to a row the import did not carry."""
    with box.auth.lock:
        box.auth.conn.execute("DELETE FROM account WHERE username = ?", (box.app,))


def _repair_the_database(box: _Box, monkeypatch: pytest.MonkeyPatch, *, ok: bool = True) -> None:
    from tests.conftest import wait_for_panel

    def route(cancel: object = None) -> Iterator[str]:
        if not ok:
            raise RuntimeError("the import failed")
        yield "Repairing"

    box.view.services.repair_database = route
    monkeypatch.setattr(controller_view_module, "ask_yes_no", lambda *a, **k: True)
    assert box.view.repair_database()
    wait_for_panel(box.view.rebuild_log)


def test_after_a_database_repair_the_channel_says_its_account_is_gone(
    box: _Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The live case: the world is not up yet, so only the database can say the row is gone."""
    _proved(box)
    _drop_the_account(box)
    box.wire.loading = True
    assert _line(box).startswith("Command channel: verified"), "the ground: the stale row"

    _repair_the_database(box, monkeypatch)

    assert _line(box) == f"Command channel: {GONE_LINE}"
    assert _offers_repair(box)
    assert player_text_faults(box.view) == []
    assert command_faults(_line(box)) == []


def test_repair_makes_the_gone_account_again_and_proves_it(
    box: _Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    _proved(box)
    _drop_the_account(box)
    box.wire.loading = True
    _repair_the_database(box, monkeypatch)
    before = box.auth.rows()
    assert box.app not in before

    box.wire.loading = False
    box.view.repair_channel_button.click()

    assert _line(box).startswith(f"Command channel: verified as {box.app} at ")
    saved = _saved(box)
    assert saved is not None and box.auth.accepts(box.app, saved.password)
    after = box.auth.rows()
    assert {k: v for k, v in after.items() if k != box.app} == before, "another account changed"
    assert len([k for k in after if k.startswith("YULON_")]) == 1, "a second app account"


def test_a_world_that_is_up_and_refuses_the_gone_account_is_repaired_too(box: _Box) -> None:
    """Refresh with the world answering: it refuses the saved password, and Repair must still work.

    A reset of a row that is not there changes nothing, so Repair on its own
    would be refused a second time.
    """
    _proved(box)
    _drop_the_account(box)

    box.view.recheck()
    assert _offers_repair(box)
    box.view.repair_channel_button.click()

    assert _line(box).startswith(f"Command channel: verified as {box.app} at ")
    saved = _saved(box)
    assert saved is not None and box.auth.accepts(box.app, saved.password)


def test_a_database_repair_that_failed_still_asks_the_channel_again(
    box: _Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A half-done import leaves a database nobody has read, so the row is not left as it was."""
    _proved(box)
    _drop_the_account(box)
    box.wire.loading = True
    _repair_the_database(box, monkeypatch, ok=False)
    assert _line(box) == f"Command channel: {GONE_LINE}"


def test_a_database_that_cannot_be_asked_leaves_the_proved_channel_alone(
    box: _Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    _proved(box)
    box.auth.down = True
    _repair_the_database(box, monkeypatch)
    assert _line(box).startswith(f"Command channel: verified as {box.app}")
    assert not _offers_repair(box)


def test_with_the_channel_off_repair_leaves_enable_on_offer(box: _Box) -> None:
    """The row is there, the setting is off: the world cannot answer, so Repair ends waiting.

    Hiding Enable then left a player with a channel that could never be proved.
    """
    operations = box.entry.operations
    assert operations is not None
    if operations.enable_conf is not None:
        # The trees that read the channel from their conf: a conf with none of its keys.
        conf = box.view.services.channel_setup.server_dir / operations.enable_conf.file
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text('[worldserver]\nDataDir = "."\n', encoding="utf-8")
    box.view._settle_the_channel()
    assert _offers_repair(box)
    box.wire.loading = True  # a world with the channel off answers nobody

    box.view.repair_channel_button.click()

    assert "waiting to be proved" in _line(box)
    assert not box.view.enable_channel_button.isHidden(), "Enable is not offered"


def test_with_the_channel_on_a_waiting_repair_does_not_offer_enable(
    box: _Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup = box.view.services.channel_setup
    assert setup is not None
    monkeypatch.setattr(type(setup), "is_enabled", lambda self: True, raising=False)
    box.view._settle_the_channel()
    box.wire.loading = True

    box.view.repair_channel_button.click()

    assert "waiting to be proved" in _line(box)
    assert box.view.enable_channel_button.isHidden()


def test_an_override_that_reads_differently_is_not_called_off(box: _Box) -> None:
    """A file written by an older render is not 'off': Enable would be refused on a running world."""
    setup = box.view.services.channel_setup
    assert setup is not None
    (setup.server_dir / composegen.OVERRIDE_FILE).write_text(
        "# some older text\n", encoding="utf-8"
    )
    operations = box.entry.operations
    assert operations is not None
    if operations.enable_conf is not None:
        conf = setup.server_dir / operations.enable_conf.file
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text(
            "".join(f"{k} = {v}\n" for k, v in operations.enable_conf.keys.items()),
            encoding="utf-8",
        )
    assert setup.is_enabled() is None
