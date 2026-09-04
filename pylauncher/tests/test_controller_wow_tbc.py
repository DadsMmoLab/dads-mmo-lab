"""The TBC controller package: the facts it takes from `catalog.json`, and the one it refuses.

Nothing here needs Docker or a database. Every seam this package has —
`sql_query`, `exec_stdin`, the account `SqlSeam`, `MysqlDocker` — is handed a
fake, and the shared engines underneath (SRP6, the console parser, backup and
restore, `MarkerGate`'s five branches) are already pinned by
`test_accounts.py`, `test_console.py`, `test_maintenance.py` and
`test_sqlplan.py`. What is left to prove, and what these tests are about, is
that THIS game's values reach those engines instead of AzerothCore's: the
container names, the schema names, the database client, the ready marker, the
console prompt, the account scheme and the generated password.

The entry is loaded independently in this file rather than being read back out
of the package under test, so an assertion compares the package against
`catalog.json` and not against itself.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO, BinaryIO

import pytest

from tests.test_maintenance import FakeMysql, good_dump
from yulon import docker
from yulon.apply import ApplyError
from yulon.catalog.catalog import load_catalog
from yulon.catalog.families import sqlplan
from yulon.controller_wow_tbc import accounts, console, controller, docker_ctl, maintenance, repair
from yulon.controller_wow_wotlk import accounts as wotlk_accounts
from yulon.controller_wow_wotlk import console as wotlk_console
from yulon.controller_wow_wotlk import maintenance as wotlk_maintenance
from yulon.manifest import Db

ENTRY = load_catalog().get("wow-tbc")

# One `Avg Diff:` line as a mangosd prints it around its own log furniture; the
# marker itself is the entry's, not this file's.
WORLD_LOG_LINE = "2026-09-02 16:04:28 mangosd Update time diff: 100. Avg Diff: 44ms"

PASSWORD_FILE = ENTRY.install.password.file or ".db_password"
INSTALL_PASSWORD = "tbc-0123456789abcdef"


def an_install(tmp_path: Path, *, password: str | None = INSTALL_PASSWORD) -> Path:
    """A server directory holding the password file a `generated` plan writes."""
    if password is not None:
        (tmp_path / PASSWORD_FILE).write_text(password, encoding="utf-8")
    return tmp_path


# ----------------------------------------------------- the facts come from data


def test_the_containers_are_the_ones_the_entry_names() -> None:
    """Not `ac-database`/`ac-authserver`/`ac-worldserver`, and not retyped here either."""
    assert docker_ctl.SPEC.db == ENTRY.containers.db
    assert docker_ctl.SPEC.auth == ENTRY.containers.auth
    assert docker_ctl.SPEC.world == ENTRY.containers.world
    assert docker_ctl.SPEC.ports == (ENTRY.ports.auth, ENTRY.ports.world)
    assert not any(
        name.startswith("ac-")
        for name in (docker_ctl.SPEC.db, docker_ctl.SPEC.auth, docker_ctl.SPEC.world)
    )


def test_the_generated_stack_names_its_services_after_its_containers() -> None:
    """An empty `services` is what lets `compose up -d --no-deps <db>` take a container name.

    A `services` tuple naming the bash installers' `db`/`realmd`/`mangosd`
    would fail every generated install at the first `compose up` with `no such
    service` — the catalog entry carried exactly that until 2026-09-01.
    """
    assert ENTRY.containers.services is None
    assert docker_ctl.SPEC.services == ()


def test_the_database_client_is_the_one_the_entry_names() -> None:
    """`mariadb:11` ships no `mysql`; the AzerothCore spelling names a binary it lacks."""
    native = ENTRY.install.native
    assert native is not None
    assert docker_ctl.DB_CLIENT == native.db.client
    assert docker_ctl.DB_CLIENT != "mysql"


def test_the_schemas_are_this_cores_and_include_the_extra_ones() -> None:
    """`logs` is a schema of this install; the three roles are what an alarm is about."""
    assert set(docker_ctl.SCHEMAS) == {
        ENTRY.databases.auth,
        ENTRY.databases.characters,
        ENTRY.databases.world,
        *ENTRY.databases.extra,
    }
    assert maintenance.CORE_DATABASES == ENTRY.core_databases()
    assert "acore_auth" not in docker_ctl.SCHEMAS


# ------------------------------------------------------------ the ready marker


def test_the_ready_wait_looks_for_the_line_mangosd_prints() -> None:
    """`Avg Diff:`, and NOT AzerothCore's `ready...`.

    The second half is not decoration: `docker.wait_ready()` searches with
    `re.search`, so inheriting AzerothCore's marker would poll a mangosd log
    for a line it never prints and answer False after the full timeout — a
    server that is up, reported as never ready.
    """
    spec = docker_ctl.ready_spec()
    assert re.search(spec.world, WORLD_LOG_LINE)
    assert re.search(spec.world, "AzerothCore ready...") is None


def test_the_ready_wait_takes_its_timeout_from_the_catalog() -> None:
    """1800s is a measurement (793s to first `Avg Diff:` on m910q), not `ReadySpec`'s default."""
    native = ENTRY.install.native
    assert native is not None
    spec = docker_ctl.ready_spec()
    assert spec.timeout == float(native.ready.timeout_s)
    assert spec.timeout != docker.ReadySpec(world="x").timeout
    assert spec.restart_loop == native.ready.restart_loop


def test_no_realmd_marker_is_waited_for() -> None:
    """This entry's `ready.auth` is null, so nothing is claimed about the auth log."""
    native = ENTRY.install.native
    assert native is not None and native.ready.auth is None
    assert docker_ctl.ready_spec().auth is None


def test_a_literal_marker_is_escaped_before_it_is_searched_with() -> None:
    """Unescaped, `ready...` matches `alREADY UP-to-date`; escaped it matches only itself."""
    pattern = docker_ctl._pattern("ready...", regex=False)
    assert re.search(pattern, ">> Database is already up-to-date!") is None
    assert re.search(pattern, "worldserver ready...")


def test_a_marker_needing_a_token_is_refused_rather_than_escaped() -> None:
    """Only the install spine can fill `{{REALM_HOST}}`; escaping it matches nothing, silently."""
    with pytest.raises(ValueError) as caught:
        docker_ctl._pattern("{{REALM_HOST}}:3724", regex=False)
    assert "{{REALM_HOST}}" in str(caught.value)


def test_wait_server_ready_refuses_an_argument_it_would_otherwise_drop() -> None:
    with pytest.raises(TypeError) as caught:
        docker_ctl.wait_server_ready(restart_loop=9)  # type: ignore[arg-type]
    assert "restart_loop" in str(caught.value)


# --------------------------------------------------------------- the controller


def test_the_controller_waits_for_this_games_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The base class calls `azerothcore_ready()`; inheriting that is the bug this closes."""
    seen: dict[str, object] = {}

    def fake_wait(
        spec: docker.ContainerSpec, ready: docker.ReadySpec, *, wsl_distro: str | None = None
    ) -> bool:
        seen["spec"] = spec
        seen["ready"] = ready
        seen["distro"] = wsl_distro
        return True

    monkeypatch.setattr(docker, "wait_ready_for", fake_wait)
    assert controller.TbcController(tmp_path, wsl_distro="Ubuntu").wait_ready() is True
    ready = seen["ready"]
    assert isinstance(ready, docker.ReadySpec)
    assert re.search(ready.world, WORLD_LOG_LINE)
    assert re.search(ready.world, "AzerothCore ready...") is None
    assert seen["spec"] == docker_ctl.SPEC
    assert seen["distro"] == "Ubuntu"


def test_the_controller_forwards_a_shorter_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller that wants a 5-second poll gets one, without editing the catalog."""
    seen: dict[str, docker.ReadySpec] = {}

    def fake_wait(
        spec: docker.ContainerSpec, ready: docker.ReadySpec, *, wsl_distro: str | None = None
    ) -> bool:
        seen["ready"] = ready
        return True

    monkeypatch.setattr(docker, "wait_ready_for", fake_wait)
    controller.TbcController(tmp_path).wait_ready(timeout=5.0, interval=0.25)
    assert seen["ready"].timeout == 5.0
    assert seen["ready"].interval == 0.25


def test_this_stack_has_no_one_shot_import_so_none_is_offered(tmp_path: Path) -> None:
    """CMaNGOS imports from the installer, not from a compose service.

    The consequence is the point: a controller built here answers `unreadable`
    rather than presenting a Repair button, and `repair_import()` refuses
    without reaching Docker at all.
    """
    assert ENTRY.containers.db_import is None
    assert not hasattr(docker_ctl, "repair_import")
    tbc = controller.TbcController(tmp_path)
    state = tbc.import_state()
    assert state.state == "unreadable" and state.repairable is False
    with pytest.raises(docker.DockerCommandError):
        tbc.repair_import()


# ------------------------------------------------------------------- accounts


class FakeSql:
    """The account `SqlSeam` over one imagined `realmd`, keyed by the folded name."""

    def __init__(self) -> None:
        self.statements: list[tuple[str, str]] = []
        self.queries: list[tuple[str, str]] = []
        self.account_id: int | None = None
        self.gmlevel = 0

    def run_statement(self, db: Db, statement: str) -> None:
        self.statements.append((db, statement))
        if statement.startswith("INSERT INTO account ("):
            self.account_id = 17
        elif statement.startswith("UPDATE account SET gmlevel"):
            self.gmlevel = int(statement.split("gmlevel = ")[1].split(" ")[0])

    def query(self, db: Db, statement: str) -> str:
        self.queries.append((db, statement))
        if statement.startswith("SELECT id FROM account"):
            return "" if self.account_id is None else f"{self.account_id}\n"
        if statement.startswith("SELECT gmlevel FROM account"):
            return f"{self.gmlevel}\n"
        raise AssertionError(f"unexpected query: {statement}")


def test_an_account_is_written_the_way_this_core_stores_one() -> None:
    """`v`/`s` and `account.gmlevel` — never salt/verifier, never an `account_access` row.

    A wrong scheme does not fail: it writes a row that looks perfectly correct
    and can never log in, which is why the scheme is asserted through the
    statements it produces rather than by reading the constant back.
    """
    sql = FakeSql()
    result = accounts.create_account(sql, "bob", "hunter2", gm_level=2)
    assert result.created is True and result.gm_level == 2

    written = [statement for _, statement in sql.statements]
    insert = next(s for s in written if s.startswith("INSERT INTO account ("))
    assert "(username, v, s, joindate)" in insert
    assert "salt" not in insert and "verifier" not in insert and "sha_pass_hash" not in insert
    assert not any("account_access" in s for s in written)
    assert f"UPDATE account SET gmlevel = 2 WHERE id = {result.account_id}" in written
    assert not any("hunter2" in s for s in written), "the password must never reach a statement"


def test_account_statements_are_addressed_to_the_auth_role() -> None:
    """The seam resolves that role to `realmd`; see the next test."""
    sql = FakeSql()
    accounts.create_account(sql, "bob", "hunter2")
    assert {db for db, _ in sql.statements} == {"auth"}
    assert {db for db, _ in sql.queries} == {"auth"}


def test_the_account_seam_addresses_this_cores_schema_names() -> None:
    """Without `schemas=`, every statement would run against `acore_auth` and die there.

    `ERROR 1049 Unknown database 'acore_auth'` on every SQL-backed control of a
    CMaNGOS install was a real report (2026-08-26).
    """
    sql = accounts.sql_for("pw")
    assert sql.db_container == ENTRY.containers.db
    assert sql._schema("auth") == ENTRY.databases.auth
    assert sql._schema("world") == ENTRY.databases.world
    with pytest.raises(ApplyError):
        # This core keeps no playerbots schema of its own, and the seam says so
        # rather than connecting to one of its other databases.
        sql._schema("playerbots")


def test_the_scheme_is_read_from_the_entry() -> None:
    assert accounts.SCHEME == ENTRY.accounts.scheme == "mangos_srp6"


def test_an_entry_that_declares_no_scheme_refuses_instead_of_guessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`None` means "this app does not write rows for this core", not "use the default"."""
    monkeypatch.setattr(accounts, "SCHEME", None)
    with pytest.raises(accounts.AccountError) as caught:
        accounts.create_account(FakeSql(), "bob", "hunter2")
    assert ENTRY.accounts.console_command in str(caught.value)


# -------------------------------------------------------------------- console


def test_the_console_is_told_this_cores_prompt_and_which_side_the_answer_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two facts, not one: `mangos>`, and that it is printed AFTER the answer.

    That the pair parses a real mangosd window is already pinned by
    `test_console.py::test_a_mangos_console_is_parsed_by_its_own_prompt`, which
    also shows what the right string on the wrong side does — an empty reply,
    presented as the server's answer. What is checked here is that both values
    travel from the entry into the shared transport.
    """
    seen: dict[str, object] = {}

    def fake_send(command: str, **kwargs: object) -> wotlk_console.ConsoleReply:
        seen.update(kwargs)
        seen["command"] = command
        return wotlk_console.ConsoleReply(command=command, lines=())

    monkeypatch.setattr(wotlk_console, "send_command", fake_send)
    console.send_command("server info")
    assert seen["prompt"] == ENTRY.console.prompt
    assert seen["prompt"] == "mangos>"
    assert seen["prompt_precedes_answer"] == ENTRY.console.prompt_precedes_answer
    assert seen["prompt_precedes_answer"] is False
    assert seen["container"] == ENTRY.containers.world


def test_the_attach_argv_names_this_games_worldserver() -> None:
    argv = console.attach()
    assert argv[-1] == ENTRY.containers.world
    assert "--sig-proxy=false" in argv, "detaching must never signal the worldserver"


# ---------------------------------------------------------------- maintenance


def test_a_backup_of_this_core_reports_nothing_missing(tmp_path: Path) -> None:
    """The bound `core_databases` is what closes a reported bug.

    With the WotLK default this backup reported "expected but absent:
    acore_auth, acore_characters, acore_world" on a dump that had taken
    everything the server had (Discord report, 2026-08-26).
    """
    mysql = FakeMysql(("realmd", "characters", "mangos", "logs"))
    report = maintenance.backup(tmp_path, mysql, running=lambda: [ENTRY.containers.db])
    assert report.missing_core == ()
    assert set(report.databases) == {"realmd", "characters", "mangos", "logs"}


def test_a_backup_names_a_missing_schema_in_this_cores_spelling(tmp_path: Path) -> None:
    """The alarm has to be a schema this install could plausibly have."""
    mysql = FakeMysql(("realmd", "characters"))
    report = maintenance.backup(tmp_path, mysql, running=lambda: [ENTRY.containers.db])
    assert report.missing_core == (ENTRY.databases.world,)


def test_a_backup_refuses_while_this_games_database_is_down(tmp_path: Path) -> None:
    """The census is by container name, so it has to be this game's name.

    Censused with AzerothCore's spec, a stopped `tbc-db` reads as "not one of
    mine" and the backup goes ahead against a database that is not there.
    """
    mysql = FakeMysql(("realmd", "characters", "mangos"))
    with pytest.raises(wotlk_maintenance.MaintenanceError) as caught:
        maintenance.backup(tmp_path, mysql, running=lambda: ["ac-database"])
    assert ENTRY.containers.db in str(caught.value)


def test_the_dump_seam_is_bound_to_this_games_container() -> None:
    assert maintenance.mysql_for("pw", wsl_distro="Ubuntu").db_container == ENTRY.containers.db


# --------------------------------------------------------------------- repair


class FakeServer:
    """`docker.sql_query` over one imagined TBC server; the write seam refuses to be used.

    `tables` holds `"<schema>.<table>"`, `rows` a count per `(schema, table)`
    and `seeded` how many of those carry one of the plan's excluded usernames,
    so a `WHERE username NOT IN (...)` query answers the difference — which is
    the whole of "a fresh install with only its seeded accounts".
    """

    def __init__(
        self,
        databases: Sequence[str] = (),
        tables: Sequence[str] = (),
        marker: str | None = None,
        rows: Mapping[tuple[str, str], int] | None = None,
        seeded: Mapping[tuple[str, str], int] | None = None,
    ) -> None:
        self.databases = list(databases)
        self.tables = set(tables)
        self.marker = marker
        self.rows = dict(rows or {})
        self.seeded = dict(seeded or {})
        self.asked: list[tuple[str, str, str, str | None, str, str | None]] = []

    def query(
        self,
        container: str,
        client: str,
        password: str,
        schema: str | None,
        statement: str,
        *,
        wsl_distro: str | None = None,
    ) -> str:
        self.asked.append((container, client, password, schema, statement, wsl_distro))
        if statement == "SHOW DATABASES":
            return "\n".join(["information_schema", "mysql", *self.databases]) + "\n"
        exists = re.search(r"table_schema='([^']+)' AND table_name='([^']+)'", statement)
        if exists:
            return "1\n" if f"{exists.group(1)}.{exists.group(2)}" in self.tables else "0\n"
        if statement.startswith("SELECT plan_hash"):
            return "" if self.marker is None else f"{self.marker}\n"
        count = re.search(r"SELECT COUNT\(\*\) FROM `([^`]+)`\.`([^`]+)`", statement)
        if count:
            key = (count.group(1), count.group(2))
            total = self.rows.get(key, 0)
            return f"{total - self.seeded.get(key, 0) if 'NOT IN' in statement else total}\n"
        raise AssertionError(f"unexpected query: {statement}")

    def exec_stdin(
        self,
        container: str,
        argv: Sequence[str],
        source: BinaryIO,
        *,
        env: Mapping[str, str],
        wsl_distro: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        raise AssertionError("asking what state the databases are in must never write")


def state_of(
    server: FakeServer, tmp_path: Path, *, wsl_distro: str | None = None
) -> docker.ImportState:
    return repair.import_state(
        an_install(tmp_path),
        wsl_distro=wsl_distro,
        sql_query=server.query,
        exec_stdin=server.exec_stdin,
    )


def imported_server(marker: str | None = None) -> FakeServer:
    """A server whose four schemas exist and whose marker table is there."""
    return FakeServer(
        databases=("realmd", "characters", "mangos", "logs"),
        tables=("mangos.yulon_install",),
        marker=marker,
    )


def test_the_probe_asks_this_games_container_with_the_client_the_catalog_names(
    tmp_path: Path,
) -> None:
    """A `mysql` here reaches nothing: `mariadb:11` does not ship that binary."""
    server = imported_server(marker=repair.PLAN.plan_hash())
    state_of(server, tmp_path, wsl_distro="Ubuntu")
    containers = {asked[0] for asked in server.asked}
    clients = {asked[1] for asked in server.asked}
    distros = {asked[5] for asked in server.asked}
    assert containers == {ENTRY.containers.db}
    assert clients == {docker_ctl.DB_CLIENT}
    assert distros == {"Ubuntu"}, "a container inside a distro is No such container to the host"


def test_the_password_is_the_one_this_install_generated(tmp_path: Path) -> None:
    """Read from the file the plan names — this entry has no fixed password to fall back on."""
    server = imported_server(marker=repair.PLAN.plan_hash())
    state_of(server, tmp_path)
    assert {asked[2] for asked in server.asked} == {INSTALL_PASSWORD}


def test_an_unreadable_password_is_said_rather_than_replaced_by_a_default(
    tmp_path: Path,
) -> None:
    """The literal "password" against a server that never used it fails six clicks later."""
    server = imported_server()
    state = repair.import_state(tmp_path, sql_query=server.query, exec_stdin=server.exec_stdin)
    assert state.state == "unreadable"
    assert PASSWORD_FILE in state.detail
    assert server.asked == [], "nothing may be asked with a password we do not have"


def test_a_marker_row_reads_imported(tmp_path: Path) -> None:
    """The row's existence is the record; its hash only says which plan wrote it."""
    server = imported_server(marker=repair.PLAN.plan_hash())
    state = state_of(server, tmp_path)
    assert state.state == "imported" and state.complete is True
    assert f"{repair.PLAN.marker_db}.{repair.MARKER_TABLE}" in state.detail


def test_a_marker_from_an_older_plan_is_still_a_finished_import(tmp_path: Path) -> None:
    """An app upgrade must not read its own new plan hash as "never imported"."""
    server = imported_server(marker="0000000000000000")
    assert state_of(server, tmp_path).state == "imported"


def test_player_rows_read_populated(tmp_path: Path) -> None:
    """Rows beyond the plan's seeded accounts are somebody's server."""
    server = FakeServer(
        databases=("realmd", "characters", "mangos", "logs"),
        tables=("characters.characters", "realmd.account"),
        rows={("characters", "characters"): 3, ("realmd", "account"): 5},
        seeded={("realmd", "account"): 4},
    )
    state = state_of(server, tmp_path)
    assert state.state == "populated"
    assert "3 rows in characters.characters" in state.detail
    assert "1 rows in realmd.account" in state.detail
    assert state.repairable is False


def test_a_server_with_no_marker_is_not_reported_as_repairable(tmp_path: Path) -> None:
    """The heart of this module: an install this app did not create looks exactly like this.

    `MarkerGate` calls it `partial`, which is right for the installer — it has
    just created those schemas itself. A controller also manages installs
    adopted through "Use existing…", where the absence of a table only this
    app's installer writes proves nothing, and `partial` is the state that
    authorises dropping databases.
    """
    server = FakeServer(
        databases=("realmd", "characters", "mangos", "logs"),
        tables=("characters.characters", "realmd.account"),
        rows={("realmd", "account"): 4},
        seeded={("realmd", "account"): 4},
    )
    state = state_of(server, tmp_path)
    assert state.state == "unreadable" and state.repairable is False
    assert repair.MARKER_TABLE in state.detail or "marker" in state.detail
    # The gate's own sentence survives the translation, so the reason is not lost.
    assert "realmd" in state.detail

    raw = repair.import_gate(
        an_install(tmp_path), sql_query=server.query, exec_stdin=server.exec_stdin
    ).probe()
    assert raw.state == "partial", "the gate itself is unchanged; only this module's report is"


def test_a_server_with_none_of_the_schemas_is_absent(tmp_path: Path) -> None:
    """`absent` is passed through: nothing exists, so nothing can be a stranger's."""
    state = state_of(FakeServer(databases=("something_else",)), tmp_path)
    assert state.state == "absent"


def test_a_database_that_cannot_be_asked_is_unreadable(tmp_path: Path) -> None:
    """A daemon that will not answer is not an empty database."""

    def refuse(*args: object, **kwargs: object) -> str:
        raise docker.DockerCommandError("No such container: tbc-db")

    server = FakeServer()
    state = repair.import_state(
        an_install(tmp_path), sql_query=refuse, exec_stdin=server.exec_stdin
    )
    assert state.state == "unreadable" and "tbc-db" in state.detail


def test_the_gate_is_the_shared_one_and_not_a_second_probe(tmp_path: Path) -> None:
    """AzerothCore's `updates`/`updates_include` pair does not exist on this core."""
    server = imported_server(marker=repair.PLAN.plan_hash())
    gate = repair.import_gate(
        an_install(tmp_path), sql_query=server.query, exec_stdin=server.exec_stdin
    )
    assert isinstance(gate, sqlplan.MarkerGate)
    assert gate.probe().state == "imported"
    asked = [statement for _, _, _, _, statement, _ in server.asked]
    assert asked, "a probe that asks nothing proves nothing"
    assert not any("updates" in statement for statement in asked)
    assert any(repair.MARKER_TABLE in statement for statement in asked)


def test_reset_refuses_and_names_the_fact_that_is_missing() -> None:
    """A hole that announces itself: dropping on the absence of our own marker is not safe."""
    with pytest.raises(NotImplementedError) as caught:
        repair.reset_unfinished()
    said = str(caught.value)
    assert repair.MARKER_TABLE in said
    assert "updates" in said, "the sentence has to name what AzerothCore has and this core lacks"


# ------------------------------------------------ the shared engines are shared


def test_the_shared_types_are_reused_rather_than_copied() -> None:
    """A forked dataclass is how the two packages start disagreeing about a report.

    Identity, not equality: these are the same objects the WotLK package
    exposes, so the view can hold either package's name for them.
    """
    assert accounts.AccountResult is wotlk_accounts.AccountResult
    assert accounts.AccountError is wotlk_accounts.AccountError
    assert console.ConsoleReply is wotlk_console.ConsoleReply
    assert maintenance.DockerMysql is wotlk_maintenance.DockerMysql
    assert maintenance.BackupReport is wotlk_maintenance.BackupReport
    assert maintenance.RestorePlan is wotlk_maintenance.RestorePlan


def test_the_backup_engine_is_the_shared_one(tmp_path: Path) -> None:
    """The wrapper binds arguments; it must not have grown a second dump path.

    Proved by the seam: a dump written by this package goes through the same
    `MysqlDocker` protocol the shared engine calls, and the file that lands is
    verified by the shared `verify_dump()`.
    """

    class Watching(FakeMysql):
        seen: list[str] = []

        def dump_into(self, database: str, sink: IO[bytes]) -> None:
            Watching.seen.append(database)
            super().dump_into(database, sink)

    report = maintenance.backup(
        tmp_path, Watching(("mangos",)), running=lambda: [ENTRY.containers.db]
    )
    assert Watching.seen == ["mangos"]
    dump = report.dumps[0]
    assert wotlk_maintenance.verify_dump(dump.path, "mangos") == dump.size_bytes


def test_a_restores_safety_dump_reports_missing_in_this_cores_spelling(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The restore path has to carry this game's core names, not AzerothCore's.

    The gap this closes was found by a live run, not by a test, and the shape of
    it is why: `backup()` was bound correctly in every per-game wrapper, so a
    Backup button on TBC reported nothing missing. But `restore()` takes a
    safety dump of its own before it overwrites anything, and THAT call reached
    the shared `backup()` with no `core_databases`, so it fell back to
    `acore_auth, acore_characters, acore_world` and announced that a healthy
    CMaNGOS server was missing all three. Measured on m910q, 2026-09-04:

        WARNING [yulon.controller_wow_wotlk.maintenance] this install has no
        acore_auth, acore_characters, acore_world; backing up what it does

    It could not be fixed at the wrapper, which is the interesting part:
    `restore()` had no `core_databases` parameter at all, so there was nothing
    for a per-game wrapper to bind. The names had to be threaded through
    `restore()` and `_safety_backup()` first.

    Asserted on `missing_core` reaching a log, because that warning is the whole
    user-visible symptom: no dump is lost either way, and a person reading
    "this install has no acore_auth" on a TBC server has been told their server
    is broken when it is not.
    """
    server_dir = tmp_path
    backup_file = server_dir / "20260904_000000_characters.sql"
    backup_file.parent.mkdir(parents=True, exist_ok=True)
    backup_file.write_bytes(good_dump("characters"))
    mysql = FakeMysql(("realmd", "characters", "mangos", "logs"))
    plan = maintenance.plan_restore(backup_file, server_dir, running=lambda: [ENTRY.containers.db])
    assert plan.refusals == (), plan.refusals
    with caplog.at_level("WARNING"):
        maintenance.restore(plan, mysql, confirm=plan.token, running=lambda: [ENTRY.containers.db])
    complaints = [r.getMessage() for r in caplog.records if "this install has no" in r.getMessage()]
    assert complaints == [], (
        "the safety dump announced a missing core database using AzerothCore's names on a "
        f"CMaNGOS install: {complaints}"
    )
