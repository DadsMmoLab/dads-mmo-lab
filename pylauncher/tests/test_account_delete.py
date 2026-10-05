"""The Accounts tab's **Delete account…** press (T301), on all five games.

Driven through the real tab, the real `InstallAccounts`, the real
`SoapChannel` and the real `soap.execute`, against a stand-in SOAP server on a
loopback port. The reads go to an in-memory SQLite database laid out like each
game's own, so the bot clause, the online check and the character list are
answered by a database evaluating the statements this app sends, not by a fake
that knows what each test wants.

The stand-in server removes the account and its characters when, and only
when, it is sent the exact line `account delete <NAME>` and told to succeed --
which is what the real one does (`AccountMgr::DeleteAccount`) -- so a list
that no longer shows the account after the press is the list being READ AGAIN,
not a widget being edited by hand.

The command is the same on every family, read from each pinned tree:

* WotLK, AzerothCore `7f12e89e`: `src/server/scripts/Commands/cs_account.cpp:93`
  registers `delete` under `account` with `Console::Yes`; the handler is at :330.
* Centurion, TrinityCore112 `faac5fc9`: the same file, :77, handler :294.
* TBC, mangos-tbc `75f9ae68`: `src/game/Chat/Chat.cpp:83`, `SEC_CONSOLE`, console
  allowed; handler `Chat.cpp:3800`. SOAP runs every command at `SEC_CONSOLE`
  (`src/mangosd/MaNGOSsoap.cpp:113`).
* Vanilla, mangos-classic `8ec338a1`: `Chat.cpp:83`, handler `Chat.cpp:3714`,
  `MaNGOSsoap.cpp:113`.
* Tortoise, tortoise-wow `187af788`: `src/game/Chat/Chat.cpp:71`, handler
  `src/mangosd/CliRunnable.cpp:74`; SOAP at `SEC_CONSOLE` in
  `src/mangosd/MaNGOSsoap.cpp:196`.
"""

from __future__ import annotations

import http.server
import re
import sqlite3
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from yulon import channel, docker, soap, useraccounts
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import ControllerServices, ControllerView
from yulon.ui.widgets.job import run_inline

CATALOG = load_catalog()
FAMILIES = ["wow-wotlk", "wow-tbc", "wow-vanilla", "wow-tortoise", "wow-centurion"]
CMANGOS = ["wow-tbc", "wow-vanilla", "wow-tortoise"]
"""The trees whose `ExtractAccountId` reads an all-digit argument as an account
id before it tries it as a name: mangos-classic `src/game/Chat/Chat.cpp:3358`,
mangos-tbc `Chat.cpp:3420`, tortoise-wow `Chat.cpp:3549` (`checkAccountId`
defaults to true, `Chat.h:858`)."""
PASSWORD_BY_ID = ["wow-tbc", "wow-vanilla"]
"""`account set password` goes through `ExtractAccountId` there (mangos-classic
`src/game/Chat/Level3.cpp:1097`, mangos-tbc `:1136`); the tortoise fork looks the
name up instead (`src/game/Commands/Commands.cpp:338`)."""
NAME_ONLY = ["wow-wotlk", "wow-centurion"]
"""`AccountMgr::GetId(accountName)` and nothing else: AzerothCore
`cs_account.cpp:347`, TrinityCore112 `cs_account.cpp:303`."""
APP_ACCOUNT = "YULON_AB12CD34"


# -- a database laid out like each game's own -------------------------------


class _Sql:
    """The read seam, answered by SQLite evaluating the statement that was sent.

    Two MySQL spellings this module uses are translated and nothing else:
    `LEFT()` is registered as a function, and the `_utf8mb4 X'…'` string literal
    becomes a quoted string, because SQLite would read `X'…'` as a blob that no
    text column ever equals.
    """

    def __init__(self, entry: CatalogEntry) -> None:
        self.entry = entry
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.create_function("LEFT", 2, lambda text, n: None if text is None else text[:n])
        self.asked: list[tuple[str, str]] = []
        self.lag: int | None = None
        """None: a delete lands at once. N: the row is still there for the next N reads
        of the account table, as AzerothCore's asynchronous delete leaves it
        (`AccountMgr.cpp:139,178`, measured live on yulon-ubuntu, 2026-10-05)."""
        self.pending: str | None = None
        schemas = entry.schema_map()
        for name in sorted(set(schemas.values())):
            self.conn.execute(f"ATTACH DATABASE ':memory:' AS {name}")
        self.auth = schemas["auth"]
        self.chars = schemas["characters"]
        level = entry.accounts.level
        assert level is not None
        if level.table is None:
            self.conn.execute(
                f"CREATE TABLE {self.auth}.account (id INTEGER, username TEXT, "
                f"{level.level_column} INTEGER)"
            )
        else:
            self.conn.execute(f"CREATE TABLE {self.auth}.account (id INTEGER, username TEXT)")
            self.conn.execute(
                f"CREATE TABLE {self.auth}.{level.table} "
                f"({level.account_column} INTEGER, {level.level_column} INTEGER)"
            )
        ops = entry.observability
        assert ops is not None
        self.table = ops.characters.table
        self.conn.execute(
            f"CREATE TABLE {self.chars}.{ops.characters.table} "
            f"(guid INTEGER, {ops.characters.account} INTEGER, name TEXT, "
            f"{ops.characters.online} INTEGER)"
        )
        registry = ops.bots.registry
        if registry is not None:
            self.conn.execute(
                f"CREATE TABLE {schemas[registry.database]}.{registry.table} "
                f"({registry.account_column} INTEGER, {registry.type_column} INTEGER)"
            )

    def account(self, id: int, username: str, *characters: tuple[str, bool]) -> None:
        ops = self.entry.observability
        assert ops is not None
        with self.lock:
            self.conn.execute(
                f"INSERT INTO {self.auth}.account (id, username) VALUES (?, ?)", (id, username)
            )
            for guid, (name, online) in enumerate(characters, start=id * 100):
                self.conn.execute(
                    f"INSERT INTO {self.chars}.{self.table} VALUES (?, ?, ?, ?)",
                    (guid, id, name, 1 if online else 0),
                )

    def register_bot(self, id: int) -> None:
        ops = self.entry.observability
        assert ops is not None and ops.bots.registry is not None
        registry = ops.bots.registry
        schema = self.entry.schema_map()[registry.database]
        with self.lock:
            self.conn.execute(f"INSERT INTO {schema}.{registry.table} VALUES (?, 1)", (id,))

    def remove(self, username: str) -> None:
        """What the server's own `AccountMgr::DeleteAccount` does, now or after `lag` reads."""
        if self.lag:
            self.pending = username
            return
        self._remove_now(username)

    def _remove_now(self, username: str) -> None:
        with self.lock:
            row = self.conn.execute(
                f"SELECT id FROM {self.auth}.account WHERE UPPER(username) = ?",
                (username.upper(),),
            ).fetchone()
            if row is None:
                return
            self.conn.execute(f"DELETE FROM {self.chars}.{self.table} WHERE account = ?", row)
            self.conn.execute(f"DELETE FROM {self.auth}.account WHERE id = ?", row)

    def remove_id(self, account_id: int) -> None:
        """`account delete <digits>` on a CMaNGOS tree: the digits are an id."""
        with self.lock:
            row = self.conn.execute(
                f"SELECT username FROM {self.auth}.account WHERE id = ?", (account_id,)
            ).fetchone()
        if row is not None:
            self.remove(row[0])

    def account_id(self, argument: str, *, digits_are_ids: bool) -> int | None:
        """Which account a command's account argument reaches, as the server reads it."""
        if digits_are_ids and argument.isdigit():
            return int(argument)
        with self.lock:
            row = self.conn.execute(
                f"SELECT id FROM {self.auth}.account WHERE UPPER(username) = ?",
                (argument.upper(),),
            ).fetchone()
        return None if row is None else int(row[0])

    def set_level(self, account_id: int, level: int) -> None:
        """What `account set gmlevel` writes, wherever this tree keeps the level."""
        block = self.entry.accounts.level
        assert block is not None
        with self.lock:
            if block.table is None:
                self.conn.execute(
                    f"UPDATE {self.auth}.account SET {block.level_column} = ? WHERE id = ?",
                    (level, account_id),
                )
            else:
                self.conn.execute(
                    f"INSERT INTO {self.auth}.{block.table} VALUES (?, ?)", (account_id, level)
                )

    def usernames(self) -> list[str]:
        with self.lock:
            return [r[0] for r in self.conn.execute(f"SELECT username FROM {self.auth}.account")]

    def query(self, db: str, statement: str) -> str:
        self.asked.append((db, statement))
        if self.pending is not None and f"{self.auth}.account" in statement:
            if self.lag:
                self.lag -= 1
            else:
                self._remove_now(self.pending)
                self.pending = None
        translated = re.sub(
            r"_utf8mb4 X'([0-9A-F]*)'",
            lambda m: "'" + bytes.fromhex(m.group(1)).decode("utf-8").replace("'", "''") + "'",
            statement,
        )
        with self.lock:
            rows = self.conn.execute(translated).fetchall()
        return "".join(
            "\t".join("NULL" if v is None else str(v) for v in row) + "\n" for row in rows
        )


# -- a SOAP server on a loopback port ---------------------------------------


_ENVELOPE = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" '
    'xmlns:ns1="urn:AC"><SOAP-ENV:Body>{inner}</SOAP-ENV:Body></SOAP-ENV:Envelope>\n'
)


@dataclass
class _Soap:
    """What the stand-in server says, and every command it was sent.

    `mode` is `result` (prints `text`), `empty` (`<result/>`, T226), `fault`
    (a gSOAP sender fault carrying `text`) or `silent` (closes the connection
    without a byte, which is a CMaNGOS refusal, `MaNGOSsoap.cpp:133`).
    """

    sql: _Sql
    mode: str = "result"
    text: str = "Account ALICE deleted."
    commands: list[str] = field(default_factory=list)
    passwords_set: list[int] = field(default_factory=list)
    """The account ids a password change reached, as the server resolved them."""
    port: int = 0

    def handler(self) -> type[http.server.BaseHTTPRequestHandler]:
        stand_in = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - the stdlib spells it this way
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                found = re.search(rb"<command>(.*?)</command>", body, re.DOTALL)
                command = soap.unescape(found.group(1).decode("utf-8")) if found else ""
                stand_in.commands.append(command)
                if stand_in.mode == "silent":
                    self.close_connection = True
                    return
                if stand_in.mode == "fault":
                    status = 500
                    inner = (
                        "<SOAP-ENV:Fault><faultcode>SOAP-ENV:Client</faultcode>"
                        f"<faultstring>{soap.escape(stand_in.text)}</faultstring>"
                        "</SOAP-ENV:Fault>"
                    )
                else:
                    status = 200
                    hit = re.fullmatch(r"account delete ([A-Za-z0-9_]+)", command)
                    if hit and hit.group(1).isdigit() and stand_in.sql.entry.id in CMANGOS:
                        # `ExtractAccountId` tries the digits as an id first.
                        stand_in.sql.remove_id(int(hit.group(1)))
                    elif hit:
                        stand_in.sql.remove(hit.group(1))
                    sql, game = stand_in.sql, stand_in.sql.entry.id
                    level = re.fullmatch(
                        r"account set gmlevel ([A-Za-z0-9_]+) (\d+)(?: -1)?", command
                    )
                    if level:
                        target = sql.account_id(level.group(1), digits_are_ids=game in CMANGOS)
                        if target is not None:
                            sql.set_level(target, int(level.group(2)))
                    secret = re.fullmatch(r"account set password ([A-Za-z0-9_]+) \S+ \S+", command)
                    if secret:
                        target = sql.account_id(
                            secret.group(1), digits_are_ids=game in PASSWORD_BY_ID
                        )
                        if target is not None:
                            stand_in.passwords_set.append(target)
                    result = (
                        "<result/>"
                        if stand_in.mode == "empty"
                        else f"<result>{soap.escape(stand_in.text)}</result>"
                    )
                    inner = f"<ns1:executeCommandResponse>{result}</ns1:executeCommandResponse>"
                payload = _ENVELOPE.format(inner=inner).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/xml; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args: object) -> None:
                """Quiet."""

        return _Handler


@dataclass
class _Server:
    entry: CatalogEntry
    sql: _Sql
    wire: _Soap
    admin: useraccounts.InstallAccounts
    slept: list[float] = field(default_factory=list)


def _seed(sql: _Sql) -> None:
    """The same people on every game.

    ALICE has two characters, both offline; BOB has one, in the game; CAROL has
    none. A bot account by this game's own prefix, the auction-house account and
    this install's channel account are there too, none with anybody online, so a
    refusal of any of them can only be the rule that names it.
    """
    ops = sql.entry.observability
    assert ops is not None
    sql.account(7, "ALICE", ("Guglu", False), ("Ganaar", False))
    sql.account(9, "BOB", ("Borin", True), ("Bramble", False))
    sql.account(11, "CAROL")
    sql.account(20, f"{ops.bots.account_prefix.upper()}1", ("Botty", False))
    sql.account(30, "AHBOT", ("Auctioneer", False))
    sql.account(40, APP_ACCOUNT)


@pytest.fixture(params=FAMILIES)
def server(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[_Server]:
    yield from _server(request.param, tmp_path)


@pytest.fixture
def wotlk(tmp_path: Path) -> Iterator[_Server]:
    yield from _server("wow-wotlk", tmp_path)


@pytest.fixture(params=CMANGOS)
def cmangos(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[_Server]:
    yield from _server(request.param, tmp_path)


@pytest.fixture(params=PASSWORD_BY_ID)
def password_by_id(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[_Server]:
    yield from _server(request.param, tmp_path)


@pytest.fixture
def tortoise(tmp_path: Path) -> Iterator[_Server]:
    yield from _server("wow-tortoise", tmp_path)


@pytest.fixture(params=NAME_ONLY)
def name_only(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[_Server]:
    yield from _server(request.param, tmp_path)


def _server(game: str, tmp_path: Path) -> Iterator[_Server]:
    entry = CATALOG.get(game)
    sql = _Sql(entry)
    _seed(sql)
    wire = _Soap(sql)
    httpd = http.server.HTTPServer(("127.0.0.1", 0), wire.handler())
    wire.port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    live = channel.SoapChannel(
        endpoint=soap.Endpoint(
            host="127.0.0.1", port=wire.port, account=APP_ACCOUNT, password="pw"
        ),
        state_of=lambda: docker.ContainerState("running", "2026-10-05T10:00:00Z", 0),
        timeout=5.0,
    )
    slept: list[float] = []
    admin = useraccounts.InstallAccounts(
        entry,
        tmp_path / entry.id,
        sql=sql,
        channel_for_saved=lambda: live,
        app_account=APP_ACCOUNT,
        sleep=slept.append,
    )
    try:
        yield _Server(entry, sql, wire, admin, slept)
    finally:
        httpd.shutdown()
        httpd.server_close()


# -- the tab ------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _inline_jobs(qapp: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)


@dataclass
class _Asked:
    questions: list[tuple[str, str]] = field(default_factory=list)
    answer: QMessageBox.StandardButton = QMessageBox.StandardButton.Yes


@pytest.fixture
def asked(monkeypatch: pytest.MonkeyPatch) -> _Asked:
    record = _Asked()

    def question(_parent: object, title: str, text: str, *_rest: object) -> object:
        record.questions.append((title, text))
        return record.answer

    monkeypatch.setattr(controller_view_module.QMessageBox, "question", question)
    return record


def _tab(server: _Server, tmp_path: Path) -> tuple[ControllerView, list[str]]:
    services = ControllerServices.for_entry(server.entry, tmp_path / server.entry.id)
    services.accounts = server.admin
    view = ControllerView(server.entry, services, status_poll_ms=0, job_runner=run_inline)
    failed: list[str] = []
    view.action_failed.connect(failed.append)
    view.refresh_accounts()
    return view, failed


def _listed(view: ControllerView) -> list[str]:
    return [
        str(view.account_list.item(i).data(controller_view_module.Qt.ItemDataRole.UserRole))
        for i in range(view.account_list.count())
    ]


def _choose(view: ControllerView, account: str) -> None:
    view.account_list.setCurrentRow(_listed(view).index(account))


def test_the_press_is_named_by_its_label_constant(server: _Server, tmp_path: Path) -> None:
    view, _ = _tab(server, tmp_path)

    assert controller_view_module.DELETE_ACCOUNT_LABEL == "Delete account…"
    assert view.delete_account_button.text() == controller_view_module.DELETE_ACCOUNT_LABEL
    assert view.delete_account_button.isEnabled() is False
    _choose(view, "ALICE")
    assert view.delete_account_button.isEnabled() is True


def test_each_game_asks_first_naming_the_characters_then_deletes_and_reads_the_list_again(
    server: _Server, tmp_path: Path, asked: _Asked
) -> None:
    """The whole press, on every family: ask, send the server's own line, re-read."""
    view, failed = _tab(server, tmp_path)
    assert "ALICE" in _listed(view)
    _choose(view, "ALICE")

    view.delete_account_button.click()

    assert len(asked.questions) == 1
    title, question = asked.questions[0]
    assert "ALICE" in title
    assert "ALICE" in question and "Ganaar" in question and "Guglu" in question
    assert server.wire.commands == ["account delete ALICE"]
    report = view.account_report.text()
    assert report.startswith("Deleted the account ALICE"), report
    assert "Ganaar" in report and "Guglu" in report
    # Read again from the database the server changed, not edited in the widget.
    assert "ALICE" not in _listed(view)
    assert "BOB" in _listed(view)
    assert failed == []


def test_an_account_with_no_characters_is_asked_about_as_one(
    wotlk: _Server, tmp_path: Path, asked: _Asked
) -> None:
    view, _ = _tab(wotlk, tmp_path)
    _choose(view, "CAROL")

    view.delete_account_button.click()

    assert "CAROL has no characters" in asked.questions[0][1]
    assert wotlk.wire.commands == ["account delete CAROL"]
    assert view.account_report.text() == "Deleted the account CAROL. It had no characters."


def test_saying_no_sends_nothing(wotlk: _Server, tmp_path: Path, asked: _Asked) -> None:
    asked.answer = QMessageBox.StandardButton.No
    view, _ = _tab(wotlk, tmp_path)
    _choose(view, "ALICE")

    view.delete_account_button.click()

    assert len(asked.questions) == 1
    assert wotlk.wire.commands == []
    assert "ALICE" in _listed(view)


def test_an_empty_result_is_done(wotlk: _Server, tmp_path: Path, asked: _Asked) -> None:
    """T226: `<result/>` is a command that ran and printed nothing."""
    wotlk.wire.mode = "empty"
    view, failed = _tab(wotlk, tmp_path)
    _choose(view, "ALICE")

    view.delete_account_button.click()

    assert wotlk.wire.commands == ["account delete ALICE"]
    assert view.account_report.text().startswith("Deleted the account ALICE")
    assert "ALICE" not in _listed(view)
    assert failed == []


def test_a_fault_is_a_refusal_in_the_servers_own_words(
    wotlk: _Server, tmp_path: Path, asked: _Asked
) -> None:
    wotlk.wire.mode = "fault"
    wotlk.wire.text = "Account ALICE NOT deleted (probably sql file format was updated)"
    view, failed = _tab(wotlk, tmp_path)
    _choose(view, "ALICE")

    view.delete_account_button.click()

    report = view.account_report.text()
    assert report.startswith("The server did not delete ALICE"), report
    assert "probably sql file format was updated" in report
    assert "Deleted" not in report
    assert "ALICE" in _listed(view)
    assert failed and failed[-1] == report


def test_a_fault_with_no_words_still_says_it_was_refused(
    wotlk: _Server, tmp_path: Path, asked: _Asked
) -> None:
    wotlk.wire.mode = "fault"
    wotlk.wire.text = ""
    view, _ = _tab(wotlk, tmp_path)
    _choose(view, "ALICE")

    view.delete_account_button.click()

    assert view.account_report.text() == "The server did not delete ALICE, and did not say why."


def test_a_server_that_hung_up_says_it_may_have_happened_and_reads_the_list(
    wotlk: _Server, tmp_path: Path, asked: _Asked
) -> None:
    """CMaNGOS's refusal: a closed connection, so whether it ran is not known.

    The list is read again, because it is the check the sentence asks for.
    """
    wotlk.wire.mode = "silent"
    view, failed = _tab(wotlk, tmp_path)
    _choose(view, "ALICE")
    wotlk.sql.asked.clear()

    view.delete_account_button.click()

    assert "may already have been made" in view.account_report.text()
    assert "Deleted" not in view.account_report.text()
    assert failed == []
    listings = [s for db, s in wotlk.sql.asked if "ORDER BY a.username" in s]
    assert listings, "the list was not read again"


def test_an_account_with_somebody_in_the_game_is_refused_before_asking(
    server: _Server, tmp_path: Path, asked: _Asked
) -> None:
    """Lead decision: refuse, naming who is online, rather than log them out."""
    view, failed = _tab(server, tmp_path)
    _choose(view, "BOB")

    view.delete_account_button.click()

    assert asked.questions == []
    assert server.wire.commands == []
    report = view.account_report.text()
    assert "Borin is in the game right now" in report, report
    assert "Bramble" not in report
    assert failed == [report]


# -- the rules, where the command is sent ------------------------------------


def _confirmed(account: str, *characters: str, account_id: int = 0) -> useraccounts.DeletePlan:
    """A plan as the person was shown it, for a call that skips the question."""
    return useraccounts.DeletePlan(account, characters=characters, account_id=account_id)


def _characters_read(sql: _Sql) -> list[str]:
    return [s for db, s in sql.asked if f"{sql.chars}.{sql.table}" in s]


def test_the_apps_own_account_is_refused_before_anything_is_read(wotlk: _Server) -> None:
    plan = wotlk.admin.delete_plan(APP_ACCOUNT)
    outcome = wotlk.admin.delete_account(_confirmed(APP_ACCOUNT))

    assert "reserves for its own command channel" in plan.problem
    assert outcome.done is False
    assert "reserves for its own command channel" in outcome.problem
    assert wotlk.sql.asked == []
    assert wotlk.wire.commands == []


def test_another_installs_channel_account_is_refused_too(wotlk: _Server) -> None:
    wotlk.sql.account(41, "YULON_FFFFFFFF")

    outcome = wotlk.admin.delete_account(_confirmed("YULON_FFFFFFFF", account_id=41))

    assert "another install's command channel" in outcome.problem
    assert wotlk.wire.commands == []


def test_a_bot_account_by_this_games_prefix_is_refused(server: _Server) -> None:
    ops = server.entry.observability
    assert ops is not None
    bot = f"{ops.bots.account_prefix.upper()}1"

    plan = server.admin.delete_plan(bot)
    outcome = server.admin.delete_account(_confirmed(bot, "Botty", account_id=20))

    assert plan.problem.startswith(f"{bot} belongs to this server's bots"), plan.problem
    assert outcome.problem == plan.problem
    assert server.wire.commands == []
    # The bot rule, and not the character read after it, did the refusing.
    assert _characters_read(server.sql) == []


def test_a_bot_account_the_module_registered_is_refused_whatever_its_name(
    wotlk: _Server,
) -> None:
    """The registry arm alone: ZED matches no prefix."""
    wotlk.sql.account(21, "ZED")
    wotlk.sql.register_bot(21)

    outcome = wotlk.admin.delete_account(_confirmed("ZED", account_id=21))

    assert outcome.problem.startswith("ZED belongs to this server's bots"), outcome.problem
    assert wotlk.wire.commands == []


def test_the_auction_house_account_is_refused(wotlk: _Server) -> None:
    outcome = wotlk.admin.delete_account(_confirmed("AHBOT", "Auctioneer", account_id=30))

    assert "the auction house runs as" in outcome.problem
    assert wotlk.wire.commands == []
    assert wotlk.sql.asked == []


def test_somebody_online_is_refused_where_the_command_is_sent(wotlk: _Server) -> None:
    """Not only before the question: they may have logged in while it was open."""
    outcome = wotlk.admin.delete_account(_confirmed("BOB", "Borin", "Bramble", account_id=9))

    assert outcome.done is False
    assert "Borin is in the game right now" in outcome.problem
    assert wotlk.wire.commands == []


def test_two_people_online_are_both_named(wotlk: _Server) -> None:
    wotlk.sql.account(12, "DAVE", ("Zora", True), ("Arn", True))

    plan = wotlk.admin.delete_plan("DAVE")

    assert "Arn and Zora are in the game right now" in plan.problem, plan.problem


def test_a_character_made_while_the_question_was_open_stops_the_delete(wotlk: _Server) -> None:
    plan = wotlk.admin.delete_plan("ALICE")
    assert plan.characters == ("Ganaar", "Guglu")
    wotlk.sql.conn.execute(f"INSERT INTO {wotlk.sql.chars}.characters VALUES (1, 7, 'Newbie', 0)")

    outcome = wotlk.admin.delete_account(plan)

    assert outcome.done is False
    assert "Newbie" in outcome.problem
    assert wotlk.wire.commands == []


def test_an_account_that_is_not_there_is_said_so(wotlk: _Server) -> None:
    outcome = wotlk.admin.delete_account(_confirmed("NOBODY", account_id=1))

    assert outcome.problem == (
        "There is no account named NOBODY on this server now. Press Refresh the list."
    )
    assert wotlk.wire.commands == []


def test_a_name_the_server_would_refuse_is_refused_before_any_read(wotlk: _Server) -> None:
    outcome = wotlk.admin.delete_account(_confirmed("AL ICE", account_id=7))

    assert outcome.done is False
    assert wotlk.sql.asked == []
    assert wotlk.wire.commands == []


def test_no_channel_says_where_to_turn_it_on(wotlk: _Server, tmp_path: Path) -> None:
    admin = useraccounts.InstallAccounts(
        wotlk.entry,
        tmp_path,
        sql=wotlk.sql,
        channel_for_saved=lambda: None,
        app_account=APP_ACCOUNT,
    )

    plan = admin.delete_plan("ALICE")

    assert "Server tab" in plan.problem
    assert "Server tab" in admin.delete_account(_confirmed("ALICE", account_id=7)).problem


def test_a_characters_read_that_fails_refuses_rather_than_asking_about_nobody(
    wotlk: _Server,
) -> None:
    def broken(db: str, statement: str) -> str:
        if db == "characters":
            raise RuntimeError("the characters database did not answer")
        return _Sql.query(wotlk.sql, db, statement)

    wotlk.sql.query = broken  # type: ignore[method-assign]

    plan = wotlk.admin.delete_plan("ALICE")

    assert plan.problem.startswith("Could not read ALICE's characters"), plan.problem
    assert "did not answer" in plan.problem


def test_an_account_made_again_under_the_same_name_is_not_the_one_confirmed(
    wotlk: _Server,
) -> None:
    """Codex, both reviews: the name and the characters are not the account.

    CAROL (no characters) is confirmed, then deleted elsewhere and made again
    under the same name, still with no characters. Only the id tells them apart.
    """
    plan = wotlk.admin.delete_plan("CAROL")
    assert plan.problem == "" and plan.account_id == 11
    wotlk.sql.remove("CAROL")
    wotlk.sql.account(71, "CAROL")

    outcome = wotlk.admin.delete_account(plan)

    assert outcome.done is False
    assert outcome.problem.startswith("CAROL is not the account you were asked about"), outcome
    assert wotlk.wire.commands == []


def test_the_right_click_delete_acts_on_the_row_clicked_not_the_one_chosen_before(
    wotlk: _Server, tmp_path: Path, asked: _Asked, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex: the menu entry read `currentItem()`, which need not be the clicked row."""
    view, _ = _tab(wotlk, tmp_path)
    _choose(view, "ALICE")
    carol = view.account_list.item(_listed(view).index("CAROL"))
    offered: list[str] = []

    class _Menu(controller_view_module.QMenu):
        """`QMenu.exec` is a Shiboken slot nothing can patch; a subclass's is Python."""

        def exec(self, *_args: object) -> None:  # type: ignore[override]
            for action in self.actions():
                offered.append(action.text())
                if action.text() == controller_view_module.DELETE_ACCOUNT_LABEL:
                    action.trigger()

    monkeypatch.setattr(controller_view_module, "QMenu", _Menu)
    view._show_account_context_menu(view.account_list.visualItemRect(carol).center())

    assert controller_view_module.DELETE_ACCOUNT_LABEL in offered
    assert len(asked.questions) == 1 and "CAROL" in asked.questions[0][0]
    assert wotlk.wire.commands == ["account delete CAROL"]


def test_a_character_made_again_under_the_same_name_is_not_the_one_confirmed(
    wotlk: _Server,
) -> None:
    """Codex, the second normal review: a name is not a character either.

    Guglu is deleted and made again on the same account while the question is
    open; the names match what the person was shown, the rows do not.
    """
    plan = wotlk.admin.delete_plan("ALICE")
    assert plan.characters == ("Ganaar", "Guglu")
    wotlk.sql.conn.execute(f"DELETE FROM {wotlk.sql.chars}.characters WHERE name = 'Guglu'")
    wotlk.sql.conn.execute(f"INSERT INTO {wotlk.sql.chars}.characters VALUES (5, 7, 'Guglu', 0)")

    outcome = wotlk.admin.delete_account(plan)

    assert outcome.done is False
    assert outcome.problem.startswith("ALICE's characters changed while you were being asked")
    assert wotlk.wire.commands == []


# -- an all-digit name on the CMaNGOS trees (cold review) --------------------


def _digits(server: _Server) -> None:
    """`123` is account 50; account 123 is somebody else entirely."""
    server.sql.account(50, "123", ("Numbo", False))
    server.sql.account(123, "VICTIM", ("Innocent", False))


def test_an_all_digit_name_is_refused_on_a_tree_that_reads_digits_as_an_id(
    cmangos: _Server, tmp_path: Path, asked: _Asked
) -> None:
    """`account delete 123` there deletes account id 123, not the account named 123."""
    _digits(cmangos)
    view, failed = _tab(cmangos, tmp_path)
    _choose(view, "123")

    view.delete_account_button.click()

    report = view.account_report.text()
    assert report.startswith("123 is a name made only of digits"), report
    # The command the player types is on a line of its own (T296).
    assert report.splitlines()[-1] == "account delete ID"
    assert asked.questions == []
    assert cmangos.wire.commands == []
    assert "VICTIM" in _listed(view) and "123" in _listed(view)
    assert failed == [report]


def test_the_digit_rule_needs_no_read_and_holds_where_the_line_is_sent(cmangos: _Server) -> None:
    _digits(cmangos)

    outcome = cmangos.admin.delete_account(_confirmed("123", "Numbo", account_id=50))

    assert outcome.problem.startswith("123 is a name made only of digits"), outcome
    assert cmangos.sql.asked == []
    assert cmangos.wire.commands == []


def test_an_all_digit_name_is_deleted_by_name_where_the_server_reads_names_only(
    name_only: _Server, tmp_path: Path, asked: _Asked
) -> None:
    """The neighbour of the rule above: AzerothCore and TrinityCore read a name."""
    _digits(name_only)
    view, _ = _tab(name_only, tmp_path)
    _choose(view, "123")

    view.delete_account_button.click()

    assert name_only.wire.commands == ["account delete 123"]
    assert "123" not in _listed(view)
    assert "VICTIM" in _listed(view)


def test_a_name_with_a_trailing_line_break_is_not_a_name() -> None:
    """`$` matches before a final newline; the rule is the whole string."""
    from yulon import commands

    assert commands.valid_account_name("ALICE") is True
    assert commands.valid_account_name("ALICE\n") is False
    assert commands.valid_account_password("pass1234\n") is False
    assert commands.valid_character_name("Guglu\n") is False


class _Stop(Exception):
    """The writer got past the name rules and reached the database."""


def _writer_reaches_the_database(monkeypatch: pytest.MonkeyPatch) -> None:
    from yulon.controller_wow_wotlk import accounts as writer

    def first_write(*_args: object, **_kwargs: object) -> object:
        raise _Stop

    monkeypatch.setattr(writer, "_account_row", first_write)


@pytest.mark.parametrize("game", FAMILIES)
def test_yulon_makes_an_all_digit_account_only_where_the_server_looks_names_up(
    game: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through each game's own create seam: such an account elsewhere could never be named."""
    from yulon.controller_wow_wotlk import accounts as writer

    _writer_reaches_the_database(monkeypatch)
    services = ControllerServices.for_entry(CATALOG.get(game), tmp_path / game)

    if game in NAME_ONLY:
        with pytest.raises(_Stop):
            services.create_account("123", "pw1234", 0)
    else:
        with pytest.raises(writer.AccountError, match="only of digits"):
            services.create_account("123", "pw1234", 0)


@pytest.mark.parametrize("scheme", ["azerothcore", "trinitycore", "mangos_srp6", "mangos_sha"])
def test_a_caller_that_does_not_say_the_tree_looks_names_up_gets_a_refusal(
    scheme: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse by default, whatever the scheme: a future tree on any scheme starts safe."""
    from yulon.controller_wow_wotlk import accounts as writer

    _writer_reaches_the_database(monkeypatch)

    with pytest.raises(writer.AccountError, match="only of digits"):
        writer.create_account(object(), "123", "pw1234", scheme=scheme)  # type: ignore[arg-type]
    with pytest.raises(_Stop):
        writer.create_account(
            object(), "123", "pw1234", scheme=scheme, names_are_names=True  # type: ignore[arg-type]
        )
    with pytest.raises(_Stop):
        writer.create_account(object(), "ALICE1", "pw1234", scheme=scheme)  # type: ignore[arg-type]


def test_every_new_sentence_passes_the_player_text_rules(wotlk: _Server) -> None:
    """T248's and T194's rules, read off the sentences themselves rather than by hand."""
    from tests.support_player_text import command_faults, text_faults
    from yulon import commands

    wotlk.sql.account(21, "ZED")
    wotlk.sql.register_bot(21)
    said = [
        commands.digits_read_as_an_id("123", commands.DELETE_BY_ID),
        commands.digits_read_as_an_id("123", commands.GM_LEVEL_BY_ID),
        commands.digits_read_as_an_id("123", commands.PASSWORD_BY_ID),
        controller_view_module.delete_account_question("ALICE", ("Ganaar", "Guglu")),
        controller_view_module.delete_account_question("ALICE", ("Guglu",)),
        controller_view_module.delete_account_question("CAROL", ()),
        controller_view_module.DELETE_ACCOUNT_LABEL,
        wotlk.admin.delete_plan(APP_ACCOUNT).problem,
        wotlk.admin.delete_plan("YULON_FFFFFFFF").problem,
        wotlk.admin.delete_plan("AHBOT").problem,
        wotlk.admin.delete_plan("RNDBOT1").problem,
        wotlk.admin.delete_plan("ZED").problem,
        wotlk.admin.delete_plan("BOB").problem,
        wotlk.admin.delete_plan("NOBODY").problem,
    ]
    plan = wotlk.admin.delete_plan("ALICE")
    wotlk.sql.conn.execute(f"INSERT INTO {wotlk.sql.chars}.characters VALUES (1, 7, 'Newbie', 0)")
    said.append(wotlk.admin.delete_account(plan).problem)
    wotlk.sql.conn.execute(f"DELETE FROM {wotlk.sql.chars}.characters WHERE guid = 1")
    wotlk.wire.mode = "fault"
    wotlk.wire.text = ""
    said.append(wotlk.admin.delete_account(wotlk.admin.delete_plan("ALICE")).problem)
    wotlk.wire.mode = "result"
    said.append(wotlk.admin.delete_account(wotlk.admin.delete_plan("ALICE")).text)
    said.append(wotlk.admin.delete_account(wotlk.admin.delete_plan("CAROL")).text)

    assert all(said), said
    for text in said:
        assert text_faults(text) == [], text
        assert command_faults(text) == [], text
    # The one command a player types stays on its own line (T296's rule).
    for refusal in said[:3]:
        *words, command = refusal.splitlines()
        assert command.startswith("account ") and command not in "\n".join(words)


def test_the_line_itself_refuses_an_all_digit_name_where_digits_are_ids() -> None:
    """The builder's own guard, for any caller that skips `useraccounts`."""
    from yulon import commands

    with pytest.raises(commands.CommandError, match="only of digits"):
        commands.account_delete("123", digits_are_ids=True)
    assert commands.account_delete("123", digits_are_ids=False) == "account delete 123"
    with pytest.raises(commands.CommandError, match="only of digits"):
        commands.account_create("123", "pw1234", digits_are_ids=True)


# -- T340, folded in: the other account commands on those trees ---------------


def _levels(view: ControllerView) -> dict[str, int]:
    return {
        str(view.account_list.item(i).data(controller_view_module.Qt.ItemDataRole.UserRole)): int(
            view.account_list.item(i).data(controller_view_module.Qt.ItemDataRole.UserRole + 1)
        )
        for i in range(view.account_list.count())
    }


def test_a_gm_level_for_an_all_digit_name_is_refused_where_digits_are_ids(
    cmangos: _Server, tmp_path: Path
) -> None:
    """`account set gmlevel 123 3` there would make account id 123 a GM."""
    _digits(cmangos)
    view, failed = _tab(cmangos, tmp_path)
    _choose(view, "123")
    view.selected_gm.setValue(3)

    view.set_gm_button.click()

    report = view.account_report.text()
    assert report.startswith("123 is a name made only of digits"), report
    assert report.splitlines()[-1] == "account set gmlevel ID LEVEL"
    assert cmangos.wire.commands == []
    view.refresh_accounts()
    assert _levels(view)["VICTIM"] == 0
    assert failed == [report]


def test_a_gm_level_for_an_all_digit_name_is_set_by_name_where_names_are_names(
    name_only: _Server, tmp_path: Path
) -> None:
    _digits(name_only)
    view, _ = _tab(name_only, tmp_path)
    _choose(view, "123")
    view.selected_gm.setValue(2)

    view.set_gm_button.click()

    assert name_only.wire.commands == ["account set gmlevel 123 2 -1"]
    assert _levels(view)["123"] == 2 and _levels(view)["VICTIM"] == 0


def test_a_password_for_an_all_digit_name_is_refused_where_digits_are_ids(
    password_by_id: _Server, tmp_path: Path
) -> None:
    _digits(password_by_id)
    view, failed = _tab(password_by_id, tmp_path)
    _choose(view, "123")
    view.selected_password.setText("n3w-p@ss")

    view.set_password_button.click()

    report = view.account_report.text()
    assert report.startswith("123 is a name made only of digits"), report
    assert report.splitlines()[-1] == "account set password ID NEWPASSWORD NEWPASSWORD"
    assert password_by_id.wire.commands == []
    assert password_by_id.wire.passwords_set == []
    assert failed == [report]


def test_tortoise_looks_a_password_change_up_by_name_so_it_is_sent(
    tortoise: _Server, tmp_path: Path
) -> None:
    """The neighbour: the fork's `account set password` calls `GetId(name)`."""
    _digits(tortoise)
    view, _ = _tab(tortoise, tmp_path)
    _choose(view, "123")
    view.selected_password.setText("n3w-p@ss")

    view.set_password_button.click()

    assert tortoise.wire.commands == ["account set password 123 n3w-p@ss n3w-p@ss"]
    assert tortoise.wire.passwords_set == [50]


def test_the_lines_themselves_refuse_an_all_digit_name_where_digits_are_ids() -> None:
    from yulon import commands

    with pytest.raises(commands.CommandError, match="only of digits"):
        commands.account_set_gm_level("123", 1, realms=False, highest=3, digits_are_ids=True)
    with pytest.raises(commands.CommandError, match="only of digits"):
        commands.account_set_password("123", "pw1234", digits_are_ids=True)
    assert (
        commands.account_set_gm_level("123", 1, realms=False, highest=3, digits_are_ids=False)
        == "account set gmlevel 123 1"
    )
    assert (
        commands.account_set_password("123", "pw1234", digits_are_ids=False)
        == "account set password 123 pw1234 pw1234"
    )


# -- the live test's one defect: AzerothCore deletes in the background --------


def _row_reads(sql: _Sql, account_id: int) -> int:
    return sum(1 for _db, s in sql.asked if f"WHERE id = {account_id}" in s)


def test_a_delete_the_server_finishes_later_is_waited_for_before_the_list_is_read(
    wotlk: _Server, tmp_path: Path, asked: _Asked
) -> None:
    """Measured live: the list still showed the account until Refresh.

    The stand-in says "deleted" and keeps the row for two more reads, so the
    press must read it three times, a short sleep apart, before re-reading the list.
    """
    view, failed = _tab(wotlk, tmp_path)
    _choose(view, "ALICE")
    wotlk.sql.lag = 2
    wotlk.sql.asked.clear()

    view.delete_account_button.click()

    assert wotlk.wire.commands == ["account delete ALICE"]
    assert _row_reads(wotlk.sql, 7) == 3
    assert len(wotlk.slept) == 2 and all(0 < step <= 1 for step in wotlk.slept)
    assert view.account_report.text().startswith("Deleted the account ALICE")
    assert "ALICE" not in _listed(view)
    assert failed == []


def test_a_delete_still_unfinished_after_the_wait_says_so_plainly(
    wotlk: _Server, tmp_path: Path, asked: _Asked
) -> None:
    """Bounded at about ten seconds, then the truth: still being removed, Refresh shows it."""
    view, failed = _tab(wotlk, tmp_path)
    _choose(view, "ALICE")
    wotlk.sql.lag = 1000

    view.delete_account_button.click()

    assert 8 <= sum(wotlk.slept) <= 12, wotlk.slept
    report = view.account_report.text()
    assert report.startswith("Deleted the account ALICE"), report
    assert "still removing it" in report and "Refresh the list" in report
    assert failed == []
    from tests.support_player_text import command_faults, text_faults

    assert text_faults(report) == [] and command_faults(report) == []


def test_a_delete_that_lands_at_once_does_not_wait(wotlk: _Server) -> None:
    outcome = wotlk.admin.delete_account(wotlk.admin.delete_plan("ALICE"))

    assert outcome.done is True
    assert wotlk.slept == []
    assert "still removing" not in outcome.text
