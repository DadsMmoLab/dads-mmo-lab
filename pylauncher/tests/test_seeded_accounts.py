"""CMaNGOS's four seeded accounts are locked before a TBC or Vanilla server starts (T668).

`realmd.sql` ships ADMINISTRATOR (GM 3), GAMEMASTER (2), MODERATOR (1) and PLAYER (0), each
with password = username, and the auth port is open to the network. These tests go through
the paths that reach a start -- `seeded_accounts.settle()` itself, `Controller.start()`, the
install's `up` and a rebuild's recreate -- over a fake `docker.sql_query` that is a small
stateful `realmd.account` table: it answers the SELECT from its rows and applies an UPDATE
only if the UPDATE's own WHERE still matches, as MySQL would. So a second run is answered by
what the first one wrote, and a repeated change shows as a second UPDATE.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from tests.support_native import Recorder
from tests.test_families_cmangos import context as cmangos_context
from tests.test_families_cmangos import engine as cmangos_engine
from yulon import docker, seeded_accounts
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.controller import Controller, PortConflictError
from yulon.controller_wow_wotlk.accounts import mangos_srp6_credentials

CATALOG = load_catalog()
TBC = CATALOG.get("wow-tbc")
VANILLA = CATALOG.get("wow-vanilla")
TORTOISE = CATALOG.get("wow-tortoise")
WOTLK = CATALOG.get("wow-wotlk")
PASSWORD = "tbc-0123456789abcdef"
SEEDED = ("ADMINISTRATOR", "GAMEMASTER", "MODERATOR", "PLAYER")

# The two pairs the live TBC and Vanilla servers both shipped (tests/test_accounts.py pins
# them); GAMEMASTER and MODERATOR are made with the same helper from a fixed salt.
_MEASURED = {
    "ADMINISTRATOR": (
        "8EB5DE915AA3D805FA7099CF61C0BB8A77990EA869078A0C5B9EEE55828F4505",
        "312B99EEF1C0196BB73B79D114CE161C5D089319E6EF54FAA6117DAB8B672C14",
    ),
    "PLAYER": (
        "EBA23AF194D89B8061CA7FEBA06D336B1C38D8FBDABA76F2C51D45141362D881",
        "3738EC7E7C731FD431C716990C6D97CA5C1D50EF0DA7DE9819076DE1D03AA891",
    ),
}


def _credentials(name: str, password: str, salt_byte: int) -> tuple[str, str]:
    return mangos_srp6_credentials(name, password, salt=bytes([salt_byte]) * 32)


def seeded_rows() -> dict[str, dict[str, object]]:
    rows: dict[str, dict[str, object]] = {}
    for index, (name, gm) in enumerate(zip(SEEDED, (3, 2, 1, 0), strict=True), start=1):
        s, v = _MEASURED[name] if name in _MEASURED else _credentials(name, name, 0x40 + index)
        rows[name] = {"id": index, "s": s, "v": v, "gmlevel": gm}
    return rows


def _decoded(statement: str) -> list[str]:
    return [bytes.fromhex(h).decode() for h in re.findall(r"X'([0-9A-Fa-f]*)'", statement)]


class AuthDb:
    """`realmd.account` as `docker.sql_query` sees it: statements in, rows out."""

    def __init__(self, rows: dict[str, dict[str, object]] | None = None) -> None:
        self.rows = rows if rows is not None else seeded_rows()
        self.statements: list[str] = []
        self.fail_with: Exception | None = None
        self.before_update: object = None
        self.databases_started = 0

    def add_player(self, name: str, password: str, gm: int = 0) -> None:
        s, v = _credentials(name, password, 0x70 + len(self.rows))
        self.rows[name] = {"id": len(self.rows) + 1, "s": s, "v": v, "gmlevel": gm}

    def sql_query(
        self,
        container: str,
        client: str,
        password: str,
        schema: str | None,
        statement: str,
        *,
        wsl_distro: str | None = None,
    ) -> str:
        assert password == PASSWORD, "root's password comes from the install's own file"
        self.statements.append(statement)
        if self.fail_with is not None:
            raise self.fail_with
        if statement.startswith("SELECT"):
            assert re.fullmatch(
                r"SELECT username, s, v FROM \w+\.account WHERE username IN \(.*\);?",
                statement,
            ), statement
            wanted = {n.upper() for n in _decoded(statement)}
            out = [
                f"{name}\t{row['s']}\t{row['v']}\n"
                for name, row in self.rows.items()
                if name.upper() in wanted
            ]
            if callable(self.before_update):
                self.before_update()
            return "".join(out)
        match = re.fullmatch(
            r"UPDATE \w+\.account SET v = _utf8mb4 X'(?P<v>[0-9A-F]+)',"
            r" s = _utf8mb4 X'(?P<s>[0-9A-F]+)'"
            r" WHERE username = _utf8mb4 X'(?P<name>[0-9A-F]+)'"
            r" AND v = _utf8mb4 X'(?P<oldv>[0-9A-F]+)'"
            r" AND s = _utf8mb4 X'(?P<olds>[0-9A-F]+)';"
            r" SELECT ROW_COUNT\(\);",
            statement,
        )
        assert match is not None, f"a statement this table does not model: {statement}"
        text = {k: bytes.fromhex(g).decode() for k, g in match.groupdict().items()}
        row = next((r for n, r in self.rows.items() if n.upper() == text["name"].upper()), None)
        if row is not None and row["v"] == text["oldv"] and row["s"] == text["olds"]:
            row["v"], row["s"] = text["v"], text["s"]
            return "1\n"
        return "0\n"

    def updates(self) -> list[str]:
        return [s for s in self.statements if s.startswith("UPDATE")]

    def updated_names(self) -> list[str]:
        return [_decoded(s)[2] for s in self.updates()]

    def seeded_state(self, name: str) -> bool:
        row = self.rows[name]
        s = str(row["s"])
        raw = bytes.fromhex(s)[::-1]
        return row["v"] == mangos_srp6_credentials(name, name, salt=raw)[1]


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> AuthDb:
    fake = AuthDb()
    monkeypatch.setattr(docker, "sql_query", fake.sql_query)

    def start_database(*_a: object, **_k: object) -> bool:
        fake.databases_started += 1
        return True

    monkeypatch.setattr(docker, "start_database", start_database)
    monkeypatch.setattr(docker, "status", lambda **_k: [])
    return fake


def an_install(tmp_path: Path, entry: CatalogEntry = TBC) -> Path:
    (tmp_path / (entry.install.password.file or ".db_password")).write_text(PASSWORD)
    return tmp_path


def settle(tmp_path: Path, entry: CatalogEntry = TBC) -> str | None:
    return seeded_accounts.settle(entry, entry.container_spec(), an_install(tmp_path, entry))


# -- the rows are changed ---------------------------------------------------------------


@pytest.mark.parametrize("entry", [TBC, VANILLA], ids=lambda e: e.id)
def test_the_four_seeded_accounts_stop_taking_their_own_name_as_the_password(
    db: AuthDb, tmp_path: Path, entry: CatalogEntry
) -> None:
    before = {n: dict(r) for n, r in db.rows.items()}
    assert all(db.seeded_state(n) for n in SEEDED), "the fixture starts seeded"

    said = settle(tmp_path, entry)

    assert not any(db.seeded_state(n) for n in SEEDED)
    for name in SEEDED:
        assert db.rows[name]["v"] != before[name]["v"] and db.rows[name]["s"] != before[name]["s"]
        assert db.rows[name]["id"] == before[name]["id"], "the row is kept, not deleted"
        assert db.rows[name]["gmlevel"] == before[name]["gmlevel"]
        assert re.fullmatch(r"[0-9A-F]{64}", str(db.rows[name]["v"]))
        assert re.fullmatch(r"[0-9A-F]{64}", str(db.rows[name]["s"]))
    assert said is not None
    assert all(name in said for name in SEEDED)
    assert "ADMINISTRATOR" in said and "password" in said.lower()


def test_each_account_gets_its_own_random_password(db: AuthDb, tmp_path: Path) -> None:
    settle(tmp_path)
    first = {n: (db.rows[n]["s"], db.rows[n]["v"]) for n in SEEDED}
    assert len({s for s, _v in first.values()}) == 4 and len({v for _s, v in first.values()}) == 4
    db.rows = seeded_rows()  # the same seeded rows again: a new draw, not the same one
    settle(tmp_path)
    assert {n: (db.rows[n]["s"], db.rows[n]["v"]) for n in SEEDED} != first


def test_the_statements_carry_hex_literals_and_no_password(db: AuthDb, tmp_path: Path) -> None:
    settle(tmp_path)
    assert len(db.updates()) == 4
    for statement in db.updates():
        assert re.fullmatch(
            r"UPDATE realmd\.account SET v = _utf8mb4 X'[0-9A-F]+', s = _utf8mb4 X'[0-9A-F]+'"
            r" WHERE username = _utf8mb4 X'[0-9A-F]+' AND v = _utf8mb4 X'[0-9A-F]+'"
            r" AND s = _utf8mb4 X'[0-9A-F]+'; SELECT ROW_COUNT\(\);",
            statement,
        ), statement
        assert "'ADMINISTRATOR'" not in statement and "PLAYER'" not in statement
    assert any(s.startswith("SELECT") for s in db.statements)
    assert all(s.startswith(("SELECT", "UPDATE")) for s in db.statements), "never a DELETE"


# -- a row that is no longer seeded is left alone -----------------------------------------


def test_an_administrator_whose_password_was_changed_keeps_it(db: AuthDb, tmp_path: Path) -> None:
    s, v = _credentials("ADMINISTRATOR", "my-own-long-secret", 0x55)
    db.rows["ADMINISTRATOR"]["s"], db.rows["ADMINISTRATOR"]["v"] = s, v

    said = settle(tmp_path)

    assert (db.rows["ADMINISTRATOR"]["s"], db.rows["ADMINISTRATOR"]["v"]) == (s, v)
    assert "ADMINISTRATOR" not in db.updated_names()
    assert sorted(db.updated_names()) == ["GAMEMASTER", "MODERATOR", "PLAYER"]
    assert said is not None and "ADMINISTRATOR" not in said


def test_a_row_with_the_seeded_verifier_but_another_salt_is_not_the_seeded_row(
    db: AuthDb, tmp_path: Path
) -> None:
    """The stored v is judged against the row's OWN salt, not against a pinned pair."""
    db.rows["GAMEMASTER"]["v"] = _credentials("GAMEMASTER", "GAMEMASTER", 0x41)[1]
    db.rows["GAMEMASTER"]["s"] = _credentials("GAMEMASTER", "GAMEMASTER", 0x99)[0]
    settle(tmp_path)
    assert "GAMEMASTER" not in db.updated_names()


def test_a_row_without_a_verifier_is_left_alone(db: AuthDb, tmp_path: Path) -> None:
    db.rows["MODERATOR"]["v"] = "NULL"
    db.rows["MODERATOR"]["s"] = ""
    settle(tmp_path)
    assert "MODERATOR" not in db.updated_names()


def test_a_password_changed_between_the_read_and_the_write_is_not_overwritten(
    db: AuthDb, tmp_path: Path
) -> None:
    """The UPDATE's own WHERE names the verifier it read, so a late change wins."""

    def player_changes_it() -> None:
        s, v = _credentials("ADMINISTRATOR", "changed-in-the-meantime", 0x56)
        db.rows["ADMINISTRATOR"]["s"], db.rows["ADMINISTRATOR"]["v"] = s, v
        db.before_update = None

    db.before_update = player_changes_it
    said = settle(tmp_path)
    assert said is not None and "ADMINISTRATOR" not in said, "not named: nothing was changed"
    assert all(name in said for name in ("GAMEMASTER", "MODERATOR", "PLAYER"))
    s, v = _credentials("ADMINISTRATOR", "changed-in-the-meantime", 0x56)
    assert (db.rows["ADMINISTRATOR"]["s"], db.rows["ADMINISTRATOR"]["v"]) == (s, v)


# -- no other account is touched ---------------------------------------------------------


def test_real_player_accounts_are_never_touched(db: AuthDb, tmp_path: Path) -> None:
    db.add_player("ALICE", "hunter22")
    db.add_player("BOBBY", "BOBBY")  # a weak password of his own is still his account
    db.add_player("YULON_CHANNEL", "x" * 12, gm=3)
    db.add_player("RNDBOT1", "RNDBOT1")
    before = {n: dict(db.rows[n]) for n in ("ALICE", "BOBBY", "YULON_CHANNEL", "RNDBOT1")}

    settle(tmp_path)

    for name, row in before.items():
        assert db.rows[name] == row, name
    assert set(db.updated_names()) == set(SEEDED)
    queried = [n for s in db.statements if s.startswith("SELECT") for n in _decoded(s)]
    assert set(queried) == set(SEEDED), "only the four names are even read"


def test_the_names_are_the_ones_the_catalog_leaves_out_of_the_player_count() -> None:
    for entry in (TBC, VANILLA):
        native = entry.install.native
        assert native is not None and native.cmangos is not None
        excluded = [
            tuple(item.exclude_usernames)
            for item in native.cmangos.sql.player_data
            if item.exclude_usernames
        ]
        assert excluded == [seeded_accounts.NAMES], entry.id
    assert seeded_accounts.NAMES == SEEDED


# -- running it twice does nothing the second time ------------------------------------------


def test_the_second_run_changes_nothing(db: AuthDb, tmp_path: Path) -> None:
    first = settle(tmp_path)
    after_first = {n: dict(r) for n, r in db.rows.items()}
    sent = len(db.updates())
    assert first is not None and sent == 4

    second = settle(tmp_path)

    assert second is None, "nothing locked, nothing said"
    assert len(db.updates()) == sent, "no UPDATE on the second run"
    assert db.rows == after_first, "the new random verifiers are not rolled again"


# -- engines it must leave alone ------------------------------------------------------------


@pytest.mark.parametrize("entry", [TORTOISE, WOTLK], ids=lambda e: e.id)
def test_other_cores_are_not_asked_anything(
    db: AuthDb, tmp_path: Path, entry: CatalogEntry
) -> None:
    assert settle(tmp_path, entry) is None
    assert db.statements == [] and db.databases_started == 0


# -- failure ---------------------------------------------------------------------------------


def test_a_database_that_cannot_be_asked_says_so_and_never_raises(
    db: AuthDb, tmp_path: Path
) -> None:
    db.fail_with = docker.DockerCommandError("container is not running")
    said = settle(tmp_path)
    assert said is not None and "could not" in said and "ADMINISTRATOR" in said
    assert "still" in said


def test_no_password_on_file_asks_nothing_and_says_the_accounts_are_open(
    db: AuthDb, tmp_path: Path
) -> None:
    said = seeded_accounts.settle(TBC, TBC.container_spec(), tmp_path)
    assert said == seeded_accounts.NOT_LOCKED.format(why="the database password could not be read")
    assert "still log in" in said and "ADMINISTRATOR" in said
    assert db.statements == []


# -- the paths that reach a start -----------------------------------------------------------


def _controller_start(
    monkeypatch: pytest.MonkeyPatch,
    server_dir: Path,
    entry: CatalogEntry = TBC,
    *,
    at_start: list[int] | None = None,
    db: AuthDb | None = None,
) -> Controller:
    def start_staged(*_a: object, **_k: object) -> bool:
        if at_start is not None and db is not None:
            at_start.append(len(db.updates()))  # how many accounts were locked by now
        return True

    monkeypatch.setattr(docker, "start_staged", start_staged)
    controller = Controller(entry.container_spec(), server_dir)
    controller.entry = entry
    monkeypatch.setattr(controller, "port_conflicts", lambda: [])
    controller.start()
    return controller


@pytest.mark.parametrize("entry", [TBC, VANILLA], ids=lambda e: e.id)
def test_an_existing_install_is_repaired_by_its_next_start_and_the_start_says_so(
    db: AuthDb, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: CatalogEntry
) -> None:
    server_dir = an_install(tmp_path, entry)

    controller = _controller_start(monkeypatch, server_dir, entry)

    assert not any(db.seeded_state(n) for n in SEEDED)
    said = controller.seeded_accounts_locked
    assert said is not None and "ADMINISTRATOR" in said
    again = _controller_start(monkeypatch, server_dir, entry)
    assert again.seeded_accounts_locked is None
    assert len(db.updates()) == 4, "the second start rolled nothing"


def test_the_accounts_are_locked_before_the_servers_are_started(
    db: AuthDb, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The auth server must never listen with the seeded rows: lock first, then start."""
    at_start: list[int] = []
    _controller_start(monkeypatch, an_install(tmp_path), at_start=at_start, db=db)
    assert at_start == [4], "all four were locked when docker.start_staged ran"


def test_a_start_that_is_refused_locks_nothing(
    db: AuthDb, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir = an_install(tmp_path)
    monkeypatch.setattr(docker, "start_staged", lambda *a, **k: True)
    controller = Controller(TBC.container_spec(), server_dir)
    controller.entry = TBC
    monkeypatch.setattr(controller, "port_conflicts", lambda: ["3724"])
    monkeypatch.setattr(controller, "_owners_of", lambda conflicts: {})
    # The refusal names the ports the conflict holds (`_ports_taken_by`, T671): Docker's
    # answer to who publishes them is faked too, so the refusal reaches no real CLI.
    monkeypatch.setattr(docker, "port_publishers", lambda ports, **_kw: {})
    with pytest.raises(PortConflictError):
        controller.start()
    assert db.updates() == []


@pytest.mark.parametrize("entry", [TBC, VANILLA], ids=lambda e: e.id)
def test_the_installs_up_locks_them_before_the_servers_start(
    db: AuthDb, tmp_path: Path, entry: CatalogEntry
) -> None:
    """A new install: the import has just laid the seeded rows and `up` is the next stage."""
    order: list[str] = []
    started = Recorder()
    server_dir = an_install(tmp_path, entry)
    eng = cmangos_engine(
        started,
        entry=entry,
        lock_seeded_accounts=seeded_accounts.settle,
        start=lambda spec, where: order.append(f"start after {len(db.updates())} updates") or True,
    )

    said = list(eng.stage_up(cmangos_context(server_dir)))

    assert order == ["start after 4 updates"], "locked first, then the servers start"
    assert not any(db.seeded_state(n) for n in SEEDED)
    assert any("ADMINISTRATOR" in line for line in said), said


def test_a_rebuilds_recreate_locks_them_too(db: AuthDb, tmp_path: Path) -> None:
    server_dir = an_install(tmp_path)
    eng = cmangos_engine(
        Recorder(),
        docker_ready=lambda: True,
        recreate=lambda *a, **k: True,
        lock_seeded_accounts=seeded_accounts.settle,
    )
    said = list(eng.stage_recreate(cmangos_context(server_dir)))
    assert not any(db.seeded_state(n) for n in SEEDED)
    assert any("ADMINISTRATOR" in line for line in said), said


def test_the_shipped_seam_is_the_real_one() -> None:
    from yulon.catalog import native

    assert native.Seams.__dataclass_fields__["lock_seeded_accounts"].default is (
        seeded_accounts.settle
    )


def test_the_log_carries_no_verifier_or_salt(
    db: AuthDb, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    settle(tmp_path)
    logged = caplog.text
    for name in SEEDED:
        assert str(db.rows[name]["v"]) not in logged and str(db.rows[name]["s"]) not in logged


# -- review round 2: a server Docker brought back, and the notice's second line -------------


def test_the_notice_says_how_to_use_administrator_again(db: AuthDb, tmp_path: Path) -> None:
    said = settle(tmp_path)
    assert said is not None
    assert said.endswith("To use ADMINISTRATOR again, set its password in the Accounts tab.")
    assert len(said) < 400, "short"


def test_a_player_who_kept_their_own_administrator_is_not_told_to_set_one(
    db: AuthDb, tmp_path: Path
) -> None:
    s, v = _credentials("ADMINISTRATOR", "my-own-long-secret", 0x55)
    db.rows["ADMINISTRATOR"]["s"], db.rows["ADMINISTRATOR"]["v"] = s, v
    said = settle(tmp_path)
    assert said is not None and "To use ADMINISTRATOR again" not in said


def test_asked_not_to_start_the_database_it_starts_nothing(db: AuthDb, tmp_path: Path) -> None:
    said = seeded_accounts.settle(
        TBC, TBC.container_spec(), an_install(tmp_path), start_database=False
    )
    assert said is not None and "ADMINISTRATOR" in said
    assert db.databases_started == 0
    assert not any(db.seeded_state(n) for n in SEEDED)
    again = seeded_accounts.settle(
        TBC, TBC.container_spec(), an_install(tmp_path), start_database=False
    )
    assert again is None and len(db.updates()) == 4


def test_the_controller_with_the_database_down_starts_nothing_and_reads_nothing(
    db: AuthDb, tmp_path: Path
) -> None:
    controller = Controller(TBC.container_spec(), an_install(tmp_path))
    controller.entry = TBC
    assert controller.lock_seeded_accounts_if_up() is None  # `docker.status` answers: nothing up
    assert db.statements == [] and db.databases_started == 0
    assert controller.seeded_accounts_locked is None


def test_the_controller_reads_the_database_through_its_own_status_seam(
    db: AuthDb, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The status poll's seam (`status()`), which tests stub; never a second `docker ps`."""
    from yulon.controller import InstallStatus

    def no_docker(**_k: object) -> list[str]:
        raise AssertionError("asked docker directly")

    monkeypatch.setattr(docker, "status", no_docker)
    controller = Controller(TBC.container_spec(), an_install(tmp_path))
    controller.entry = TBC
    monkeypatch.setattr(
        controller, "status", lambda: InstallStatus(db=True, auth=False, world=False)
    )
    assert controller.lock_seeded_accounts_if_up() is not None
    assert not any(db.seeded_state(n) for n in SEEDED)


def test_another_cores_controller_asks_nothing_not_even_docker(
    db: AuthDb, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_docker(**_k: object) -> list[str]:
        raise AssertionError("asked docker")

    monkeypatch.setattr(docker, "status", no_docker)
    controller = Controller(WOTLK.container_spec(), an_install(tmp_path, WOTLK))
    controller.entry = WOTLK
    assert controller.lock_seeded_accounts_if_up() is None
    assert db.statements == []


def test_the_controller_locks_an_install_whose_database_is_already_up(
    db: AuthDb, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(docker, "status", lambda **_k: [TBC.container_spec().db])
    controller = Controller(TBC.container_spec(), an_install(tmp_path))
    controller.entry = TBC
    said = controller.lock_seeded_accounts_if_up()
    assert said is not None and controller.seeded_accounts_locked == said
    assert controller.lock_seeded_accounts_if_up() is None
    assert controller.seeded_accounts_locked == said, "the earlier note stays until a Start"
    assert len(db.updates()) == 4


def test_the_lock_waits_while_a_restore_holds_the_server_and_says_nothing_false(
    db: AuthDb, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T716: the poll's lock is a press on the server; a restore alone refuses it, writes nothing.

    Mutation: call `seeded_accounts.settle()` outside `docker.server_hold()`.
    """
    monkeypatch.setattr(docker, "status", lambda **_k: [TBC.container_spec().db])
    controller = Controller(TBC.container_spec(), an_install(tmp_path))
    controller.entry = TBC
    with docker.hold_the_server(
        controller.server_dir, "A restore is running.", alone="A restore writes to it."
    ):
        with pytest.raises(docker.ServerHeldError):
            controller.lock_seeded_accounts_if_up()
        assert db.statements == [], "the lock reached the database inside a restore's hold"
    assert controller.seeded_accounts_locked is None, "a false 'accounts still open' warning"
    # The restore over, the next poll locks.
    assert controller.lock_seeded_accounts_if_up() is not None
    assert len(db.updates()) == 4


# the Server tab and the Accounts tab, built the way main.py builds them


@pytest.fixture
def inline_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    from yulon.ui import controller_view
    from yulon.ui.widgets.job import run_inline

    monkeypatch.setattr(controller_view, "threaded_job_runner", lambda _parent: run_inline)


def _view(server_dir: Path, entry: CatalogEntry = TBC) -> object:
    from tests.conftest import process_events
    from yulon.ui.controller_view import ControllerServices, ControllerView

    services = ControllerServices.for_entry(entry, server_dir, None, None)
    services.dashboard = None  # the verdict reads container state; not what these are about
    view = ControllerView(entry, services)
    process_events(10)
    return view


def _tick(view: object) -> None:
    from tests.conftest import process_events

    view._tick()  # type: ignore[attr-defined]
    process_events(10)


def test_opening_the_app_on_a_database_docker_already_started_locks_the_accounts(
    qapp: object, db: AuthDb, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inline_jobs: None
) -> None:
    up = [TBC.container_spec().db]
    monkeypatch.setattr(docker, "status", lambda **_k: list(up))
    view = _view(an_install(tmp_path))
    try:
        _tick(view)
        assert not any(db.seeded_state(n) for n in SEEDED), "locked without a press of Start"
        assert "ADMINISTRATOR" in view.notice_label.text()  # type: ignore[attr-defined]
        assert not view.notice_label.isHidden()  # type: ignore[attr-defined]
        asked = len(db.statements)
        _tick(view)
        assert len(db.statements) == asked, "one ask while the database stays up"
    finally:
        view.shutdown()  # type: ignore[attr-defined]


def test_a_database_that_goes_down_and_comes_back_is_asked_again(
    qapp: object, db: AuthDb, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inline_jobs: None
) -> None:
    up = [TBC.container_spec().db]
    monkeypatch.setattr(docker, "status", lambda **_k: list(up))
    view = _view(an_install(tmp_path))
    try:
        _tick(view)
        asked = len(db.statements)
        up.clear()
        _tick(view)
        up.append(TBC.container_spec().db)
        _tick(view)
        assert len(db.statements) == asked + 1, "one more read, no more writes"
        assert len(db.updates()) == 4
    finally:
        view.shutdown()  # type: ignore[attr-defined]


def test_a_database_that_comes_up_later_is_asked_when_it_does(
    qapp: object, db: AuthDb, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inline_jobs: None
) -> None:
    up: list[str] = []
    monkeypatch.setattr(docker, "status", lambda **_k: list(up))
    view = _view(an_install(tmp_path))
    try:
        _tick(view)
        assert db.statements == [], "database down: nothing asked, nothing started"
        up.append(TBC.container_spec().db)
        _tick(view)
        assert not any(db.seeded_state(n) for n in SEEDED)
    finally:
        view.shutdown()  # type: ignore[attr-defined]


def test_a_reservation_that_cannot_be_made_still_says_the_accounts_are_not_locked(
    db: AuthDb, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T716 round 2: only a held server waits silently; an unavailable reservation is said.

    Mutation: let `ServerReservationUnavailable` propagate like `ServerHeldError`.
    """
    monkeypatch.setattr(docker, "status", lambda **_k: [TBC.container_spec().db])

    def cannot(*_a: object, **_k: object) -> None:
        raise docker.ServerReservationUnavailable("The server's id file is unreadable.")

    monkeypatch.setattr(docker, "server_hold", cannot)
    controller = Controller(TBC.container_spec(), an_install(tmp_path))
    controller.entry = TBC
    said = controller.lock_seeded_accounts_if_up()
    assert said is not None and "could not lock" in said and "id file is unreadable" in said
    assert controller.seeded_accounts_locked == said
    assert db.statements == []


def test_polls_during_a_restore_log_no_warning(
    db: AuthDb, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(docker, "status", lambda **_k: [TBC.container_spec().db])
    controller = Controller(TBC.container_spec(), an_install(tmp_path))
    controller.entry = TBC
    with docker.hold_the_server(controller.server_dir, "A restore.", alone="Alone."):
        for _ in range(3):
            with pytest.raises(docker.ServerHeldError):
                controller.lock_seeded_accounts_if_up()
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_poll_during_a_restore_writes_nothing_and_the_next_one_locks(
    qapp: object, db: AuthDb, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inline_jobs: None
) -> None:
    """T716: the status poll finds the database up inside a restore's hold; it waits silently."""
    up: list[str] = []
    monkeypatch.setattr(docker, "status", lambda **_k: list(up))
    view = _view(an_install(tmp_path))
    try:
        server_dir = view.services.controller.server_dir  # type: ignore[attr-defined]
        _tick(view)  # the database is down: nothing asked yet
        up.append(TBC.container_spec().db)
        with docker.hold_the_server(server_dir, "A restore.", alone="A restore writes alone."):
            _tick(view)
            assert db.statements == [], "the lock wrote beside the restore's load"
            controller = view.services.controller  # type: ignore[attr-defined]
            assert controller.seeded_accounts_locked is None
        _tick(view)
        assert len(db.updates()) == 4, "the poll after the restore locks them"
    finally:
        view.shutdown()  # type: ignore[attr-defined]


def test_the_accounts_tab_loading_its_list_locks_them_first(
    qapp: object, db: AuthDb, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inline_jobs: None
) -> None:
    from tests.conftest import process_events

    up: list[str] = []  # the opening poll finds nothing running, so only the tab can lock
    monkeypatch.setattr(docker, "status", lambda **_k: list(up))
    view = _view(an_install(tmp_path))
    try:
        assert db.statements == []
        up.append(TBC.container_spec().db)
        listing = type("Listing", (), {"problem": "", "accounts": []})()
        accounts = type("Accounts", (), {"listing": staticmethod(lambda: listing)})()
        view.services.accounts = accounts  # type: ignore[attr-defined]
        view.refresh_accounts()  # type: ignore[attr-defined]
        process_events(10)
        assert not any(db.seeded_state(n) for n in SEEDED)
    finally:
        view.shutdown()  # type: ignore[attr-defined]


def test_on_a_stopped_distro_neither_reading_runs_until_it_is_up(
    qapp: object, db: AuthDb, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inline_jobs: None
) -> None:
    up: list[str] = []  # the opening poll finds nothing running
    monkeypatch.setattr(docker, "status", lambda **_k: list(up))
    view = _view(an_install(tmp_path))
    try:
        up.append(TBC.container_spec().db)
        view.services.controller.wsl_distro = "Ubuntu-test"  # type: ignore[attr-defined]
        from yulon.controller import InstallStatus

        monkeypatch.setattr(
            view.services.controller,  # type: ignore[attr-defined]
            "status",
            lambda: InstallStatus(db=True, auth=False, world=False),
        )
        view._distro = "stopped"  # type: ignore[attr-defined]
        view.refresh_seeded_accounts()  # type: ignore[attr-defined]
        assert db.statements == [], "a stopped distro is not read"
        assert view._waiting_on_distro, "kept for when WSL says it runs"  # type: ignore[attr-defined]
        view._distro_answered("running")  # type: ignore[attr-defined]
        assert not any(db.seeded_state(n) for n in SEEDED)
    finally:
        view.shutdown()  # type: ignore[attr-defined]


def test_a_database_not_ready_on_the_first_poll_is_locked_on_a_later_one(
    qapp: object, db: AuthDb, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inline_jobs: None
) -> None:
    """T718: the container is up but mysqld is not answering; the next polls ask again."""
    up: list[str] = []
    monkeypatch.setattr(docker, "status", lambda **_k: list(up))
    view = _view(an_install(tmp_path))
    try:
        controller = view.services.controller  # type: ignore[attr-defined]
        _tick(view)
        up.append(TBC.container_spec().db)
        db.fail_with = docker.DockerCommandError("Can't connect to local MySQL server")
        _tick(view)
        assert seeded_accounts.is_not_locked(controller.seeded_accounts_locked)
        db.fail_with = None  # the database finished loading
        _tick(view)
        assert len(db.updates()) == 4, "a later poll locks them"
        assert not any(db.seeded_state(n) for n in SEEDED)
        assert not seeded_accounts.is_not_locked(controller.seeded_accounts_locked)
        assert "could not lock" not in view.notice_label.text()  # type: ignore[attr-defined]
    finally:
        view.shutdown()  # type: ignore[attr-defined]


def test_a_lock_that_keeps_failing_slows_down_but_never_gives_up(
    qapp: object,
    db: AuthDb,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    inline_jobs: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    up: list[str] = []
    monkeypatch.setattr(docker, "status", lambda **_k: list(up))
    seeded_accounts._last_failure.clear()
    view = _view(an_install(tmp_path))
    try:
        _tick(view)
        up.append(TBC.container_spec().db)
        db.fail_with = docker.DockerCommandError("refused")
        for _ in range(60):
            _tick(view)
        asked = sum(1 for s in db.statements if s.startswith("SELECT"))
        assert seeded_accounts.RETRY_POLLS <= asked < 40, "every poll first, then every Nth"
        warned = [r for r in caplog.records if "seeded accounts were not locked" in r.message]
        assert len(warned) == 1, "the same reason is logged once"
        assert "could not lock" in view.notice_label.text()  # type: ignore[attr-defined]
        db.fail_with = None  # the database finally answers
        for _ in range(seeded_accounts.SLOW_EVERY + 1):
            _tick(view)
        assert len(db.updates()) == 4, "still not given up after 60 polls"
        assert view.notice_label.text().count("could not lock") == 0  # type: ignore[attr-defined]
    finally:
        view.shutdown()  # type: ignore[attr-defined]


def test_a_not_locked_line_is_cleared_when_a_later_poll_finds_nothing_seeded(
    qapp: object, db: AuthDb, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inline_jobs: None
) -> None:
    """T718: Refresh on the Accounts tab locked them meanwhile; the stale warning must go."""
    up: list[str] = []
    monkeypatch.setattr(docker, "status", lambda **_k: list(up))
    view = _view(an_install(tmp_path))
    try:
        controller = view.services.controller  # type: ignore[attr-defined]
        _tick(view)
        up.append(TBC.container_spec().db)
        db.fail_with = docker.DockerCommandError("not ready")
        _tick(view)
        assert "could not lock" in view.notice_label.text()  # type: ignore[attr-defined]
        db.fail_with = None
        assert settle(tmp_path) is not None  # locked by someone else
        _tick(view)
        assert controller.seeded_accounts_locked is None
        assert view.notice_label.text() == ""  # type: ignore[attr-defined]
    finally:
        view.shutdown()  # type: ignore[attr-defined]


def test_a_database_that_goes_down_and_up_logs_its_next_failure_again(
    qapp: object,
    db: AuthDb,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    inline_jobs: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    up: list[str] = []
    monkeypatch.setattr(docker, "status", lambda **_k: list(up))
    seeded_accounts._last_failure.clear()
    view = _view(an_install(tmp_path))

    def warnings() -> int:
        return len([r for r in caplog.records if "seeded accounts were not locked" in r.message])

    try:
        _tick(view)
        db.fail_with = docker.DockerCommandError("Can't connect")
        up.append(TBC.container_spec().db)
        for _ in range(3):
            _tick(view)
        assert warnings() == 1
        up.clear()
        _tick(view)  # the database is down
        up.append(TBC.container_spec().db)
        for _ in range(3):
            _tick(view)
        assert warnings() == 2, "the same reason after a restart is a new failure"
        assert settle(tmp_path) is not None
        assert warnings() == 3, "a Start's own failed lock is logged whatever the poll said"
    finally:
        view.shutdown()  # type: ignore[attr-defined]
