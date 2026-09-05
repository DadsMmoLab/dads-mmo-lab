"""The Tortoise controller package: does each game fact reach the thing that uses it?

This package is almost entirely binding, so the tests are almost entirely about
VALUES ARRIVING. A binding that quietly kept AzerothCore's default is the exact
failure it exists to prevent, and every one of those defaults is a working
program that answers the wrong question — the ready marker times out on a
healthy server, the backup alarm fires on a complete dump, the restore census
watches three containers that are not there. So most tests below assert what
reached the seam, and several also assert what the WotLK default would have
done instead: the contrast is what makes the assertion fail if the binding is
dropped.

Nothing here needs Docker, a database or a real server. The catalog is read for
real, because the whole point of the package is that its facts come from it.
"""

from __future__ import annotations

import ast
import io
import os
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from tests.test_maintenance import good_dump
from yulon import docker
from yulon.catalog.catalog import Accounts, ReadyMarkers, load_catalog
from yulon.catalog.families import sqlplan
from yulon.controller_wow_tortoise import (
    accounts,
    console,
    controller,
    docker_ctl,
    game,
    maintenance,
    repair,
)
from yulon.controller_wow_wotlk import docker_ctl as wotlk_docker_ctl
from yulon.controller_wow_wotlk import maintenance as wotlk_maintenance
from yulon.manifest import Db

ENTRY = game.entry()
WOTLK = load_catalog().get("wow-wotlk")

PACKAGE_DIR = Path(game.__file__).parent


# ----------------------------------------------------------------- containers


def test_the_containers_and_ports_are_this_entrys_and_not_azerothcores() -> None:
    """`SPEC` is derived, not written out — and it is a different server."""
    assert docker_ctl.SPEC == ENTRY.container_spec()
    assert docker_ctl.SPEC.db != wotlk_docker_ctl.SPEC.db
    assert docker_ctl.SPEC.world != wotlk_docker_ctl.SPEC.world
    assert docker_ctl.SPEC.ports != wotlk_docker_ctl.SPEC.ports


def test_this_entry_names_no_import_service_so_no_repair_is_offered(tmp_path: Path) -> None:
    """The Server tab shows Repair on a `repairable` state, and its action can only refuse
    without an import service. The controller is therefore built without a probe."""
    assert docker_ctl.SPEC.import_service == ""
    state = controller.controller_for(tmp_path).import_state()
    assert state.repairable is False


# ----------------------------------------------------------------- readiness


def test_the_world_marker_is_this_cores_and_azerothcores_would_never_match_it() -> None:
    spec = docker_ctl.ready_spec("127.0.0.1", ENTRY.ports.world)
    legacy = docker.azerothcore_ready("127.0.0.1", ENTRY.ports.world)
    assert spec.world == ENTRY.install.native.ready.world  # type: ignore[union-attr]
    assert spec.world != legacy.world
    # This entry declares no auth marker: nothing waits on the realmd log, where
    # `azerothcore_ready()` waits for `<host>:<port>`. And it declares a fatal
    # one, which that spec has no field filled for at all.
    assert spec.auth is None and legacy.auth is not None
    assert spec.fatal is not None and legacy.fatal is None


def test_a_declared_regex_is_handed_over_and_a_literal_is_escaped() -> None:
    """The `regex` flag is a value that has to arrive: escaped, this entry's
    alternation matches nothing; unescaped, a literal address matches any
    character where its dots are."""
    declared = game.ready_markers()
    assert declared.regex is True
    assert docker_ctl.ready_spec("127.0.0.1", 8090).world == declared.world

    literal = ReadyMarkers(world="127.0.0.1:8085 is listening", regex=False)
    spec = docker_ctl.ready_spec_from(literal, "127.0.0.1", 8090)
    assert "127x0y0z1:8085 is listening" not in _matching(spec.world)
    assert "127.0.0.1:8085 is listening" in _matching(spec.world)


def _matching(pattern: str) -> list[str]:
    """Which of two look-alike lines `pattern` matches — an escaping check, spelled out."""
    import re

    lines = ["127.0.0.1:8085 is listening", "127x0y0z1:8085 is listening"]
    return [line for line in lines if re.search(pattern, line)]


def test_the_markers_tokens_are_filled_with_the_host_and_port_asked_about() -> None:
    markers = ReadyMarkers(world="listening on {{REALM_HOST}}:{{WORLD_PORT}}", regex=True)
    spec = docker_ctl.ready_spec_from(markers, "10.0.0.5", 8090)
    assert spec.world == "listening on 10.0.0.5:8090"


def test_a_marker_naming_a_token_nobody_fills_refuses_before_anything_is_polled() -> None:
    markers = ReadyMarkers(world="waiting for {{NOT_A_TOKEN}}", regex=True)
    with pytest.raises(docker_ctl.ReadyMarkerError, match="NOT_A_TOKEN"):
        docker_ctl.ready_spec_from(markers, "127.0.0.1", 8090)


def test_the_wait_timeout_is_the_entrys_unless_the_caller_says_otherwise() -> None:
    declared = game.ready_markers()
    assert docker_ctl.ready_spec("127.0.0.1", 8090).timeout == float(declared.timeout_s)
    assert docker_ctl.ready_spec("127.0.0.1", 8090, timeout=12.0).timeout == 12.0
    assert docker_ctl.ready_spec("127.0.0.1", 8090).restart_loop == declared.restart_loop


def test_the_controllers_wait_ready_polls_with_this_games_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, a_world_container_that_answers: None
) -> None:
    """The base class builds an AzerothCore spec in this very method; the
    override is what puts this game's markers in front of `wait_ready()`."""
    seen: list[tuple[str, str, docker.ReadySpec]] = []

    def fake_wait_ready(
        auth: str, world: str, spec: docker.ReadySpec, *, wsl_distro: str | None = None
    ) -> bool:
        seen.append((auth, world, spec))
        return True

    monkeypatch.setattr(docker, "wait_ready", fake_wait_ready)
    assert controller.controller_for(tmp_path).wait_ready("127.0.0.1", ENTRY.ports.world) is True
    auth, world, spec = seen[0]
    assert (auth, world) == (ENTRY.containers.auth, ENTRY.containers.world)
    assert spec.world == docker_ctl.ready_spec("127.0.0.1", ENTRY.ports.world).world
    assert spec.world != docker.azerothcore_ready("127.0.0.1", ENTRY.ports.world).world


# ------------------------------------------------------------------- accounts


class _FakeSql:
    """Records every statement, and answers the two reads `create_account()` makes."""

    def __init__(self, existing_id: int | None = None, gm_level: int = 0) -> None:
        self.statements: list[tuple[Db, str]] = []
        self.existing_id = existing_id
        self.gm_level = gm_level

    def run_statement(self, db: Db, statement: str) -> None:
        self.statements.append((db, statement))
        if statement.startswith("INSERT INTO account"):
            self.existing_id = 7

    def query(self, db: Db, statement: str) -> str:
        self.statements.append((db, statement))
        if "FROM account WHERE username" in statement:
            return "" if self.existing_id is None else f"{self.existing_id}\n"
        return f"{self.gm_level}\n"

    def written(self) -> str:
        """Everything that reached the seam, reads included."""
        return "\n".join(statement for _db, statement in self.statements)

    def writes(self) -> str:
        """Only what was WRITTEN. A read naming a column proves nothing about the write."""
        return "\n".join(
            statement for _db, statement in self.statements if not statement.startswith("SELECT")
        )


def test_the_account_row_is_this_cores_shape_and_never_azerothcores() -> None:
    sql = _FakeSql()
    result = accounts.create_account(sql, "dad", "pw")
    written = sql.written()
    assert result.created is True
    assert "sha_pass_hash" in written
    # The columns of the other two schemes this app knows. Written into this
    # core's `account` table they would be a row that cannot log in.
    assert "verifier" not in written and "salt" not in written
    assert " v, s," not in written


def test_the_gm_grant_writes_this_cores_column_and_never_account_access() -> None:
    sql = _FakeSql()
    result = accounts.create_account(sql, "dad", "pw", gm_level=3)
    written = sql.writes()
    assert result.gm_level == 3
    assert "UPDATE account SET `rank` = 3" in written
    # AzerothCore's table and CMaNGOS-proper's column: neither exists here.
    assert "account_access" not in sql.written()
    assert "gmlevel" not in sql.written()


def test_the_statements_reach_this_cores_auth_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """`DockerSql` puts the schema in argv. Built without this game's map it
    names AzerothCore's, and every statement dies with `Unknown database`."""
    seen: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    accounts.sql_for("pw").run_statement("auth", "SELECT 1")
    assert seen[0][-1] == ENTRY.databases.auth
    assert seen[0][-1] != WOTLK.databases.auth
    assert ENTRY.containers.db in seen[0]


def test_an_entry_that_declares_no_scheme_refuses_instead_of_guessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wrong scheme writes a row that looks right and can never authenticate,
    so there is no default to fall back to."""
    unmeasured = ENTRY.model_copy(update={"accounts": Accounts(scheme=None)})
    monkeypatch.setattr(game, "entry", lambda: unmeasured)
    sql = _FakeSql()
    with pytest.raises(NotImplementedError, match="no account scheme"):
        accounts.create_account(sql, "dad", "pw")
    assert sql.statements == []


def test_an_install_whose_password_file_is_missing_says_so_rather_than_guessing(
    tmp_path: Path,
) -> None:
    with pytest.raises(accounts.AccountError, match="not knowable"):
        accounts.sql_for_install(tmp_path)


# ---------------------------------------------------------------- maintenance


class _FakeMysql:
    """A database container answering from memory (the `MysqlDocker` seam)."""

    def __init__(self, present: tuple[str, ...]) -> None:
        self.present = present
        self.dumped: list[str] = []

    def databases(self) -> tuple[str, ...]:
        return self.present

    def dump_into(self, database: str, sink: Any) -> None:
        self.dumped.append(database)
        sink.write(good_dump(database))

    def load_from(self, source: Any) -> None:
        raise AssertionError("no test here loads a dump")


def _running(*names: str) -> maintenance.RunningNames:
    return lambda: list(names)


def test_a_backup_of_this_cores_schemas_reports_nothing_missing(tmp_path: Path) -> None:
    """The alarm names the core schemas it could not find. Left at the shared
    default it names AzerothCore's, which is how a Tortoise backup reported all
    three missing on a dump that had taken everything."""
    present = game.core_databases()
    report = maintenance.backup(
        tmp_path, _FakeMysql(present), running=_running(ENTRY.containers.db)
    )
    assert report.missing_core == ()
    assert set(report.databases) == set(present)

    # The same fake through the shared function's own default, which is what
    # this binding replaces.
    other = wotlk_maintenance.backup(
        tmp_path / "wotlk",
        _FakeMysql(present),
        spec=docker_ctl.SPEC,
        running=_running(ENTRY.containers.db),
    )
    assert set(other.missing_core) == set(wotlk_maintenance.CORE_DATABASES)


def test_a_restore_is_refused_while_this_games_worldserver_is_running(tmp_path: Path) -> None:
    """The census watches this game's container names. With AzerothCore's spec
    a live mangosd is invisible, and the restore is overwritten by the server's
    own saves minutes later."""
    backup_file = tmp_path / "backup.sql"
    backup_file.write_bytes(good_dump(ENTRY.databases.characters))
    plan = maintenance.plan_restore(
        backup_file,
        tmp_path,
        running=_running(ENTRY.containers.db, ENTRY.containers.world),
    )
    assert plan.allowed is False
    assert any(ENTRY.containers.world in refusal for refusal in plan.refusals)


def test_a_restore_is_allowed_with_only_this_games_database_up(tmp_path: Path) -> None:
    backup_file = tmp_path / "backup.sql"
    backup_file.write_bytes(good_dump(ENTRY.databases.characters))
    plan = maintenance.plan_restore(backup_file, tmp_path, running=_running(ENTRY.containers.db))
    assert plan.refusals == ()
    assert plan.allowed is True


# --------------------------------------------------------------------- repair


class _FakeServer:
    """Answers the three questions `MarkerGate.probe()` asks, and records how it asked."""

    def __init__(self, databases: Sequence[str], *, marker_hash: str | None = None) -> None:
        self.databases = list(databases)
        self.marker_hash = marker_hash
        self.calls: list[tuple[str, str, str, str | None, str]] = []

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
        self.calls.append((container, client, password, schema, statement))
        if statement == "SHOW DATABASES":
            return "".join(f"{name}\n" for name in self.databases)
        if "information_schema.tables" in statement:
            exists = self.marker_hash is not None and sqlplan.MARKER_TABLE in statement
            return f"{1 if exists else 0}\n"
        if statement.startswith("SELECT plan_hash"):
            return "" if self.marker_hash is None else f"{self.marker_hash}\n"
        raise AssertionError(f"unexpected statement: {statement}")

    def statements(self) -> list[str]:
        return [statement for *_rest, statement in self.calls]


def _install(tmp_path: Path, password: str = "tortoise-abc123") -> Path:
    """A server dir carrying the password file this entry's install generates."""
    plan = ENTRY.install.password
    assert plan.file is not None
    (tmp_path / plan.file).write_text(password, encoding="utf-8")
    return tmp_path


def test_the_probe_asks_with_the_client_the_entry_declares(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MariaDB 11 ships no `mysql` binary, so a hardcoded client is a stack that
    cannot be asked anything. The name has to travel from the entry to argv."""
    server = _FakeServer(game.core_databases())
    monkeypatch.setattr(docker, "sql_query", server.query)
    repair.import_state(_install(tmp_path))
    clients = {client for _container, client, *_rest in server.calls}
    assert clients == {ENTRY.install.native.db.client}  # type: ignore[union-attr]
    assert clients != {WOTLK.install.native.db.client}  # type: ignore[union-attr]
    assert {container for container, *_rest in server.calls} == {ENTRY.containers.db}


def test_the_probe_reads_the_marker_this_familys_installer_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not AzerothCore's `updates`/`updates_include`, which no CMaNGOS core has
    — every schema of a finished install would look unfinished."""
    server = _FakeServer(game.core_databases())
    monkeypatch.setattr(docker, "sql_query", server.query)
    state = repair.import_state(_install(tmp_path))
    written = "\n".join(server.statements())
    assert sqlplan.MARKER_TABLE in written
    assert game.sql_plan().marker_db in written
    assert "updates_include" not in written
    # No marker row, so this install did not finish through this app's engine.
    assert state.state == "partial"


def test_a_marker_row_reads_as_imported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _FakeServer(game.core_databases(), marker_hash=game.sql_plan().plan_hash())
    monkeypatch.setattr(docker, "sql_query", server.query)
    state = repair.import_state(_install(tmp_path))
    assert state.state == "imported"
    assert state.complete is True


def test_a_password_that_cannot_be_read_is_unreadable_and_never_repairable(
    tmp_path: Path,
) -> None:
    state = repair.import_state(tmp_path)
    assert state.state == "unreadable"
    assert state.repairable is False


# -------------------------------------------------------------------- console


needs_pty = pytest.mark.skipif(not console.can_send(), reason="no pty on this platform")


class _FakeProc:
    """A console that prints its prompt AFTER the answer, as an `fgets` one does."""

    window = b"account create dad pw\r\nAccount created.\r\nmangos> "

    def __init__(self, argv: list[str], **kwargs: Any) -> None:
        self.argv = argv
        stdin = kwargs.get("stdin")
        self.stdin = os.dup(stdin) if isinstance(stdin, int) else stdin
        self.stdout = io.BytesIO(self.window)
        self._rc: int | None = None

    def terminate(self) -> None:
        self._rc = 0

    def kill(self) -> None:
        self._rc = -9

    def wait(self, timeout: float | None = None) -> int:
        return self._rc if self._rc is not None else 0

    def poll(self) -> int | None:
        return self._rc


@needs_pty
def test_the_reply_is_cut_with_this_consoles_prompt_which_follows_the_answer() -> None:
    """Both facts have to arrive, and each has its own failure.

    With AzerothCore's prompt the window holds no delimiter at all, so nothing
    is cut and the reply is flagged unprompted. With this prompt but readline's
    ordering, the answer is looked for on the far side of the one prompt there
    is, and comes back empty — the shape the research note on `catalog.Console`
    warned a core declaring only the string would land in.
    """
    made: list[_FakeProc] = []

    def popen(argv: list[str], **kwargs: Any) -> _FakeProc:
        proc = _FakeProc(argv, **kwargs)
        made.append(proc)
        return proc

    reply = console.send("account create dad pw", window=0.01, popen=popen)  # type: ignore[arg-type]
    assert made[0].argv[-1] == ENTRY.containers.world
    assert reply.prompted is True
    assert reply.lines == ("Account created.",)

    wrong_prompt = _same_window_parsed_as(popen)
    assert wrong_prompt.prompted is False

    wrong_ordering = _same_window_parsed_as(popen, prompt=ENTRY.console.prompt)
    assert wrong_ordering.lines == ()


def _same_window_parsed_as(popen: Any, prompt: str | None = None) -> console.ConsoleReply:
    """The same bytes through the shared transport, with its own defaults left in place."""
    from yulon.controller_wow_wotlk import console as wotlk_console

    extra = {} if prompt is None else {"prompt": prompt}
    return wotlk_console.send_command(
        "account create dad pw",
        container=ENTRY.containers.world,
        window=0.01,
        popen=popen,
        **extra,
    )


def test_attach_names_this_installs_worldserver() -> None:
    assert console.attach()[-1] == ENTRY.containers.world


# ---------------------------------------------------------- no game literals


def test_no_module_in_this_package_spells_a_game_fact_as_a_literal() -> None:
    """The whole design in one assertion: every fact comes from the entry.

    A container name, schema name, prompt or client binary written into this
    package's code is a second copy of something `catalog.json` already says,
    and the two can then disagree. Docstrings are exempt — they explain the
    facts and are checked by a reader, not by an import.
    """
    facts = {
        ENTRY.containers.db,
        ENTRY.containers.auth,
        ENTRY.containers.world,
        ENTRY.databases.auth,
        ENTRY.databases.characters,
        ENTRY.databases.world,
        *ENTRY.databases.extra,
        ENTRY.console.prompt,
        ENTRY.install.native.db.client,  # type: ignore[union-attr]
        WOTLK.containers.db,
        WOTLK.databases.auth,
    }
    offenders: list[str] = []
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        prose = {
            id(node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if id(node) in prose:
                continue
            for fact in facts:
                if fact in node.value:
                    offenders.append(f"{path.name}:{node.lineno} spells {fact!r}")
    assert offenders == []
