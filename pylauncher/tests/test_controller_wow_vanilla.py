"""The Vanilla controller package: the CMaNGOS facts, and that they actually arrive.

This package is mostly bindings, so most of these tests are about a VALUE
reaching the shared code rather than about a body here. That is deliberate:
every defect this package can have is a fact taken from the wrong place, and
the shape of the failure is always the same — a call that runs happily against
the wrong container, the wrong schema or the wrong log line.

So wherever it can be done, a test asserts BOTH halves: that this game's value
arrives, and that the AzerothCore value it replaced would have produced a
different, wrong answer. A test that only pinned the CMaNGOS value would stay
green over a binding that passed it to nothing.

Nothing here reaches a daemon or a database. Every seam is faked.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO, BinaryIO

import pytest

from yulon import docker
from yulon.catalog.catalog import load_catalog
from yulon.catalog.installer import InstallerError
from yulon.controller_wow_vanilla import GAME_ID, accounts, console, docker_ctl
from yulon.controller_wow_vanilla import maintenance as vanilla_maintenance
from yulon.controller_wow_vanilla import repair as vanilla_repair
from yulon.controller_wow_vanilla.controller import VanillaController
from yulon.controller_wow_wotlk import accounts as wotlk_accounts
from yulon.controller_wow_wotlk import console as wotlk_console
from yulon.controller_wow_wotlk import maintenance as wotlk_maintenance
from yulon.manifest import Db

ENTRY = load_catalog().get(GAME_ID)
"""Read from the catalog file again, on purpose.

`entry()` is what the package under test reads; this is a second, independent
read of the same file, so a test comparing them is comparing the package's
answer with the data rather than with itself.
"""

WOTLK = load_catalog().get("wow-wotlk")

NATIVE = ENTRY.install.native
assert NATIVE is not None, "the wow-vanilla entry must carry an install.native block"
READY = NATIVE.ready


# --------------------------------------------------------------- docker_ctl


def test_the_spec_is_this_entrys_containers_and_not_azerothcores() -> None:
    """`SPEC` is the catalog entry's own spec, so compose and the controller agree.

    The second half is the one with teeth: every name here differs from the
    AzerothCore package's, so a binding that forwarded the WotLK spec would
    manage `ac-worldserver` — a container this install does not have, which
    `docker ps` reports as simply absent rather than as an error.
    """
    assert docker_ctl.SPEC == ENTRY.container_spec()
    wotlk = WOTLK.container_spec()
    assert docker_ctl.SPEC.db != wotlk.db
    assert docker_ctl.SPEC.auth != wotlk.auth
    assert docker_ctl.SPEC.world != wotlk.world


def test_this_game_has_no_one_shot_importer_for_a_start_to_select() -> None:
    """No `import_service`, so `compose_services()` is the three long-running ones.

    `docker.repair_import()` refuses without one, which is why `docker_ctl`
    does not re-export it. The assertion is on the spec rather than on the
    module's attribute list because the spec is what the refusal reads.
    """
    assert ENTRY.containers.db_import is None
    assert docker_ctl.SPEC.import_service == ""
    assert docker_ctl.SPEC.compose_services() == (
        docker_ctl.SPEC.db,
        docker_ctl.SPEC.auth,
        docker_ctl.SPEC.world,
    )


def test_the_declared_database_client_is_this_images_and_not_azerothcores() -> None:
    """`DB_CLIENT` comes from `install.native.db`, where the entry declares it.

    `mariadb:11` ships neither `mysql` nor `mysqldump`, so this is not a
    naming preference: a statement naming the AzerothCore client does not
    reach a database at all.
    """
    assert NATIVE is not None
    wotlk_native = WOTLK.install.native
    assert wotlk_native is not None
    assert docker_ctl.DB_CLIENT == NATIVE.db.client
    assert docker_ctl.DB_CLIENT != wotlk_native.db.client


def test_the_ready_marker_is_this_worldservers_line_and_azerothcores_would_never_match() -> None:
    """The spec waits on the entry's own marker, escaped, with no auth line.

    A CMaNGOS worldserver never prints `ready...`, so the AzerothCore spec
    could only ever time out here — and the entry says `ready.auth` is null,
    so there is no realmd line to wait for either.
    """
    spec = docker_ctl.ready_spec()
    log_line = f"[world] {READY.world} 128ms, Sessions: 3"

    assert re.search(spec.world, log_line) is not None
    azerothcore = docker.azerothcore_ready("127.0.0.1", 3724)
    assert re.search(azerothcore.world, log_line) is None
    assert spec.auth is None and azerothcore.auth is not None


def test_the_ready_marker_is_escaped_so_it_cannot_become_a_wildcard() -> None:
    """Literal markers are `re.escape`d, the rule the install spine applies.

    `ready...` unescaped matches `alREADY UP-to-date` in a loading log, which
    is how a still-loading server gets reported as up. This game's marker has
    no metacharacter today; the escaping is what keeps the first one that does
    from becoming a wildcard.
    """
    assert READY.regex is False
    assert docker_ctl.ready_spec().world == re.escape(READY.world)


def test_the_ready_timeout_is_the_entrys_and_not_the_python_default() -> None:
    """480s was measured to call a working CMaNGOS first boot a failed install.

    m910q, 2026-09-02: 793s from container start to the first `Avg Diff:` on
    four cores. The entry carries the number that measurement produced, and a
    spec built here has to take it rather than `ReadySpec`'s own default.
    """
    spec = docker_ctl.ready_spec()
    assert spec.timeout == float(READY.timeout_s)
    assert spec.timeout != docker.ReadySpec.timeout
    assert spec.restart_loop == READY.restart_loop


def test_a_caller_may_shorten_the_wait_but_not_misspell_it() -> None:
    """`timeout`/`interval` forward; anything else is a typo, refused not dropped."""
    assert docker_ctl.ready_spec(timeout=5.0, interval=0.25).timeout == 5.0
    assert docker_ctl.ready_spec(interval=0.25).interval == 0.25
    with pytest.raises(TypeError, match="restart_loop"):
        docker_ctl.ready_spec(restart_loop=9)


def test_wait_server_ready_polls_this_installs_containers_with_this_games_markers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The spec and the ready markers both arrive at `docker.wait_ready_for()`.

    Faked at the docker layer, which is the last point where a wrong container
    name is still visible: past it, polling the wrong containers looks exactly
    like a server that has not come up.
    """
    seen: dict[str, object] = {}

    def fake(
        spec: docker.ContainerSpec, ready: docker.ReadySpec, *, wsl_distro: str | None = None
    ) -> bool:
        seen.update(spec=spec, ready=ready, distro=wsl_distro)
        return True

    monkeypatch.setattr(docker, "wait_ready_for", fake)
    assert docker_ctl.wait_server_ready(wsl_distro="yulon-ubuntu") is True
    assert seen["spec"] == ENTRY.container_spec()
    assert seen["distro"] == "yulon-ubuntu"
    ready = seen["ready"]
    assert isinstance(ready, docker.ReadySpec)
    assert ready.world == re.escape(READY.world)


def test_the_controller_waits_with_this_games_markers_not_the_inherited_ones(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`VanillaController.wait_ready()` overrides the AzerothCore-shaped base body.

    Left inherited it would build `docker.azerothcore_ready()`, whose world
    marker this server never prints. The controller's distro travels with it.
    """
    seen: dict[str, object] = {}

    def fake(
        spec: docker.ContainerSpec, ready: docker.ReadySpec, *, wsl_distro: str | None = None
    ) -> bool:
        seen.update(spec=spec, ready=ready, distro=wsl_distro)
        return False

    monkeypatch.setattr(docker, "wait_ready_for", fake)
    controller = VanillaController(tmp_path, wsl_distro="yulon-ubuntu")
    assert controller.wait_ready("127.0.0.1", 3724, timeout=1.0) is False
    ready = seen["ready"]
    assert isinstance(ready, docker.ReadySpec)
    assert ready.world == re.escape(READY.world)
    assert ready.world != docker.azerothcore_ready("127.0.0.1", 3724).world
    assert ready.timeout == 1.0
    assert seen["distro"] == "yulon-ubuntu"


def test_the_controller_manages_this_installs_containers(tmp_path: Path) -> None:
    """The base class is handed this game's spec and the server dir it was given."""
    controller = VanillaController(tmp_path)
    assert controller.spec == ENTRY.container_spec()
    assert controller.server_dir == tmp_path


# ------------------------------------------------------------------ console


def test_the_console_binding_carries_this_cores_prompt_and_its_side_of_the_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both console facts and this install's worldserver reach the shared transport.

    Two facts, not one: the string alone would leave `prompt_precedes_answer`
    at the readline default and every reply from this core would be parsed as
    empty.
    """
    seen: dict[str, object] = {}

    def fake(command: str, **kwargs: object) -> wotlk_console.ConsoleReply:
        seen.update(kwargs, command=command)
        return wotlk_console.ConsoleReply(command=command, lines=())

    monkeypatch.setattr(wotlk_console, "send_command", fake)
    console.send_command("server info", wsl_distro="yulon-ubuntu")
    assert seen["container"] == ENTRY.container_spec().world
    assert seen["prompt"] == ENTRY.console.prompt
    assert seen["prompt_precedes_answer"] == ENTRY.console.prompt_precedes_answer
    assert seen["wsl_distro"] == "yulon-ubuntu"


def test_this_cores_prompt_convention_is_what_makes_a_reply_readable() -> None:
    """The two constants, applied to a window this core's console would produce.

    Reaches `_parse_reply()` directly — a private function, deliberately —
    because the parse IS what these two values control, and nothing public
    shows the difference without a pty and a container. The window below is an
    `fgets` console's: it echoes the command, prints the answer, and only then
    prints its prompt.

    With `prompt_precedes_answer` left at the AzerothCore default the same
    window yields an empty reply flagged `prompted=True` — a command that
    answered, reported as one that said nothing.
    """
    window = [
        "gm list",
        "No gamemasters online.",
        f"{console.PROMPT} ",
    ]

    ours = wotlk_console._parse_reply(
        window,
        "gm list",
        prompt=console.PROMPT,
        prompt_precedes_answer=console.PROMPT_PRECEDES_ANSWER,
    )
    assert ours.lines == ("No gamemasters online.",)
    assert ours.prompted is True

    readline = wotlk_console._parse_reply(
        window, "gm list", prompt=console.PROMPT, prompt_precedes_answer=True
    )
    assert readline.lines == ()


def test_the_attach_argv_names_this_installs_worldserver() -> None:
    """The terminal escape hatch points at `vanilla-mangosd`, not `ac-worldserver`."""
    argv = console.attach()
    assert argv[-1] == ENTRY.container_spec().world
    assert WOTLK.container_spec().world not in argv


# ----------------------------------------------------------------- accounts


class _FakeSql:
    """A `SqlSeam` over one tiny in-memory `account` table.

    Keeps only the two facts the writer leans on: the table is keyed by the
    folded username, and a query for a name that is not there returns no rows.
    """

    def __init__(self) -> None:
        self.statements: list[tuple[Db, str]] = []
        self.accounts: dict[str, int] = {}
        self.gmlevel: dict[int, int] = {}

    def run_statement(self, db: Db, statement: str) -> None:
        self.statements.append((db, statement))
        insert = re.search(r"INSERT INTO account ?\(username, v, s", statement)
        if insert:
            name = self._name_in(statement)
            self.accounts[name] = len(self.accounts) + 1
        grant = re.search(r"UPDATE account SET gmlevel = (\d+) WHERE id = (\d+)", statement)
        if grant:
            self.gmlevel[int(grant.group(2))] = int(grant.group(1))

    def query(self, db: Db, statement: str) -> str:
        self.statements.append((db, statement))
        if statement.startswith("SELECT id FROM account"):
            found = self.accounts.get(self._name_in(statement))
            return "" if found is None else f"{found}\n"
        if statement.startswith("SELECT gmlevel FROM account"):
            account_id = int(re.findall(r"id = (\d+)", statement)[0])
            return f"{self.gmlevel.get(account_id, 0)}\n"
        raise AssertionError(f"unexpected query: {statement}")

    @staticmethod
    def _name_in(statement: str) -> str:
        """The first `_utf8mb4 X'...'` literal, decoded — how the writer spells a name."""
        hexed = re.findall(r"_utf8mb4 X'([0-9A-F]*)'", statement)
        return bytes.fromhex(hexed[0]).decode("utf-8")


def test_an_account_is_written_the_way_this_core_stores_one() -> None:
    """Hex `v`/`s` on `account`, the level in `gmlevel`, and no `account_access`.

    The AzerothCore shape would insert binary `salt`/`verifier` plus an
    `expansion` column and then write a row into a table this core does not
    have — a failure two statements after an account row that is already
    committed.
    """
    sql = _FakeSql()
    result = accounts.create_account(sql, "vanillabob", "hunter2", gm_level=2)

    assert result.created is True and result.gm_level == 2
    written = [statement for _, statement in sql.statements]
    insert = [s for s in written if s.startswith("INSERT INTO account")][0]
    assert "INSERT INTO account (username, v, s, joindate)" in insert
    assert "salt" not in insert and "verifier" not in insert and "expansion" not in insert
    assert not [s for s in written if "account_access" in s]
    assert [s for s in written if "UPDATE account SET gmlevel = 2" in s]


def test_no_statement_this_package_writes_ever_carries_the_password() -> None:
    """Only the derived salt and verifier reach the SQL, and neither reverses."""
    sql = _FakeSql()
    accounts.create_account(sql, "vanillabob", "hunter2", gm_level=3)
    assert not [s for _, s in sql.statements if "hunter2" in s]


def test_the_account_scheme_is_read_from_the_catalog_and_is_not_azerothcores() -> None:
    """A wrong scheme writes a row that looks correct and can never log in.

    So the value is taken from the entry, and the test compares it with the
    catalog rather than with a literal in this file.
    """
    assert accounts.scheme() == ENTRY.accounts.scheme
    assert accounts.scheme() != (WOTLK.accounts.scheme or "azerothcore")


def test_the_account_row_is_the_one_a_live_vanilla_server_wrote() -> None:
    """The crypto is the shared writer's, not a second copy of a modulus.

    `mangos_srp6_credentials()` was solved against the seeded rows a live
    Vanilla server shipped (2026-08-26, pinned in `test_accounts.py`); this
    asserts the package reuses that function rather than reimplementing it,
    which is the only way those vectors keep covering this game.
    """
    assert accounts.mangos_srp6_credentials is wotlk_accounts.mangos_srp6_credentials
    assert accounts.fold is wotlk_accounts.fold


def test_the_sql_seam_addresses_this_cores_schemas_and_this_installs_container() -> None:
    """`auth` reaches `realmd` here, not `acore_auth` — the 1049 bug in one line."""
    seam = accounts.sql_for("secret", wsl_distro="yulon-ubuntu")
    assert seam.db_container == ENTRY.container_spec().db
    assert dict(seam.schemas) == ENTRY.schema_map()
    assert seam.schemas["auth"] == ENTRY.databases.auth
    assert seam.wsl_distro == "yulon-ubuntu"


def test_the_installs_password_is_read_from_its_file_rather_than_defaulted(
    tmp_path: Path,
) -> None:
    """This entry generates its password, so the file is the only place it exists."""
    plan_file = ENTRY.install.password.file
    assert plan_file is not None
    (tmp_path / plan_file).write_text("vanilla-0123456789abcdef\n", encoding="utf-8")
    seam = accounts.sql_for_install(tmp_path)
    assert seam.root_password == "vanilla-0123456789abcdef"


def test_an_unreadable_password_file_is_refused_and_not_replaced_with_a_guess(
    tmp_path: Path,
) -> None:
    """Defaulting here is how every SQL control came to authenticate as "password"."""
    with pytest.raises(accounts.AccountError, match="not knowable"):
        accounts.sql_for_install(tmp_path)


# -------------------------------------------------------------- maintenance


def _dump_bytes(database: str) -> bytes:
    """A minimal file that passes `verify_dump()` and names `database`."""
    return (
        b"-- MySQL dump 10.13  Distrib 8.4.0\n"
        b"--\n"
        + f"CREATE DATABASE /*!32312 IF NOT EXISTS*/ `{database}`;\nUSE `{database}`;\n".encode()
        + b"INSERT INTO t VALUES (1);\n"
        b"-- Dump completed on 2026-09-02\n"
    )


class _FakeMysql:
    """A `MysqlDocker` over a fixed list of schemas; dumps are written to the sink."""

    def __init__(self, databases: Sequence[str]) -> None:
        self._databases = tuple(databases)
        self.dumped: list[str] = []

    def databases(self) -> tuple[str, ...]:
        return ("information_schema", "mysql", *self._databases)

    def dump_into(self, database: str, sink: IO[bytes]) -> None:
        self.dumped.append(database)
        sink.write(_dump_bytes(database))

    def load_from(self, source: IO[bytes]) -> None:  # pragma: no cover - restore is planned only
        source.read()


def test_the_absent_schema_alarm_names_schemas_this_core_could_have(tmp_path: Path) -> None:
    """The default core list is AzerothCore's, and it fired on every CMaNGOS backup.

    Both halves are asserted against one fake server: this package's `backup()`
    reports nothing missing, and the shared one with its own default reports
    all three `acore_*` names absent — on the same dump, which took everything
    the server had (Discord report, 2026-08-26).
    """
    present = [ENTRY.databases.auth, ENTRY.databases.characters, ENTRY.databases.world]
    mysql = _FakeMysql(present)

    ours = vanilla_maintenance.backup(tmp_path, mysql, running=lambda: [ENTRY.container_spec().db])
    assert ours.missing_core == ()
    assert set(ours.databases) == set(present)

    theirs = wotlk_maintenance.backup(
        tmp_path / "again",
        _FakeMysql(present),
        spec=docker_ctl.SPEC,
        running=lambda: [ENTRY.container_spec().db],
    )
    assert theirs.missing_core == wotlk_maintenance.CORE_DATABASES
    assert vanilla_maintenance.CORE_DATABASES == ENTRY.core_databases()
    assert not [name for name in vanilla_maintenance.CORE_DATABASES if name.startswith("acore_")]


def test_a_backup_refuses_when_this_installs_database_container_is_down(
    tmp_path: Path,
) -> None:
    """The census is by this game's container name; the WotLK one is not running here."""
    mysql = _FakeMysql([ENTRY.databases.world])
    with pytest.raises(wotlk_maintenance.MaintenanceError, match=ENTRY.container_spec().db):
        vanilla_maintenance.backup(tmp_path, mysql, running=lambda: [WOTLK.container_spec().db])


def test_a_restore_is_refused_while_this_installs_worldserver_is_running(
    tmp_path: Path,
) -> None:
    """A live worldserver saves characters back over a restore within minutes.

    The refusal reads container names, so the second half is the test: with the
    AzerothCore spec the very same census raises no objection at all, and the
    restore would go ahead underneath a running `vanilla-mangosd`.
    """
    backup_file = tmp_path / "20260902_000000_characters.sql"
    backup_file.write_bytes(_dump_bytes(ENTRY.databases.characters))
    spec = ENTRY.container_spec()

    def census() -> list[str]:
        return [spec.db, spec.world]

    plan = vanilla_maintenance.plan_restore(backup_file, tmp_path, running=census)
    assert not plan.allowed
    assert [reason for reason in plan.refusals if spec.world in reason]

    blind = wotlk_maintenance.plan_restore(
        backup_file, tmp_path, spec=WOTLK.container_spec(), running=census
    )
    assert [reason for reason in blind.refusals if WOTLK.container_spec().db in reason]
    assert not [reason for reason in blind.refusals if spec.world in reason]


def test_the_maintenance_machinery_is_the_shared_one(tmp_path: Path) -> None:
    """Nothing about dumping or verifying is re-implemented for this game."""
    assert vanilla_maintenance.verify_dump is wotlk_maintenance.verify_dump
    assert vanilla_maintenance.interrupted_restore is wotlk_maintenance.interrupted_restore
    assert vanilla_maintenance.backups_dir(tmp_path) == wotlk_maintenance.backups_dir(tmp_path)


def test_the_dump_seam_is_bound_to_this_installs_database_container() -> None:
    mysql = vanilla_maintenance.mysql_for("secret", wsl_distro="yulon-ubuntu")
    assert mysql.db_container == ENTRY.container_spec().db
    assert mysql.wsl_distro == "yulon-ubuntu"


# -------------------------------------------------------------------- repair

PLAN = vanilla_repair.sql_plan()
MARKER_DB = PLAN.marker_db


class _FakeServer:
    """`docker.sql_query` + `docker.exec_stdin` over one imaginary database server.

    Answers are shaped like `--batch --skip-column-names` output, because the
    difference between "no rows" (`""`) and "one row holding the empty string"
    (`"\\n"`) is the difference between `partial`, which drops databases, and
    `imported`, which never touches them.
    """

    def __init__(
        self,
        databases: Sequence[str] = (),
        tables: Mapping[str, Sequence[str]] | None = None,
        marker_hash: str | None = None,
        rows: Mapping[tuple[str, str], int] | None = None,
    ) -> None:
        self.databases = list(databases)
        self.tables = {name: set(names) for name, names in (tables or {}).items()}
        if marker_hash is not None:
            self.tables.setdefault(MARKER_DB, set()).add(vanilla_repair.MARKER_TABLE)
        self.marker_hash = marker_hash
        self.rows = dict(rows or {})
        self.clients: list[str] = []
        self.containers: list[str] = []
        self.passwords: list[str] = []
        self.dropped: list[str] = []

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
        self.containers.append(container)
        self.clients.append(client)
        self.passwords.append(password)
        if statement == "SHOW DATABASES":
            return "\n".join(["information_schema", "mysql", *self.databases]) + "\n"
        exists = re.search(r"table_schema='([^']+)' AND table_name='([^']+)'", statement)
        if exists:
            here = self.tables.get(exists.group(1), set())
            return "1\n" if exists.group(2) in here else "0\n"
        if statement.startswith("SELECT plan_hash"):
            return f"{self.marker_hash}\n" if self.marker_hash else ""
        count = re.search(r"SELECT COUNT\(\*\) FROM `([^`]+)`\.`([^`]+)`", statement)
        if count:
            return f"{self.rows.get((count.group(1), count.group(2)), 0)}\n"
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
        text = source.read().decode("utf-8")
        for name in re.findall(r"DROP DATABASE IF EXISTS `([^`]+)`", text):
            self.dropped.append(name)
            self.databases = [db for db in self.databases if db != name]
        return subprocess.CompletedProcess(list(argv), 0, "", "")


def _all_schemas() -> list[str]:
    return list(vanilla_repair.schemas())


def test_the_probe_reads_this_installs_marker_row_as_a_finished_import() -> None:
    """`yulon_install` is the CMaNGOS completion record; AzerothCore has no such table.

    The WotLK probe answers this question from `updates`/`updates_include`,
    which this core never writes — so a copy of it would call this finished
    install unimported and offer to overwrite it.
    """
    server = _FakeServer(databases=_all_schemas(), marker_hash=PLAN.plan_hash())
    state = vanilla_repair.gate("pw", sql_query=server.query, exec_stdin=server.exec_stdin).probe()
    assert state.state == "imported"
    assert state.complete is True
    assert vanilla_repair.MARKER_TABLE in state.detail


def test_the_probe_says_absent_when_none_of_this_games_schemas_exist() -> None:
    """The names asked about are `realmd`/`characters`/`mangos`/`logs`, from the entry."""
    server = _FakeServer(databases=["acore_auth", "acore_world"])
    state = vanilla_repair.gate("pw", sql_query=server.query, exec_stdin=server.exec_stdin).probe()
    assert state.state == "absent"
    for name in _all_schemas():
        assert name in state.detail


def test_schemas_with_no_marker_and_no_player_rows_are_the_only_resettable_state() -> None:
    """`partial` is the branch that drops, so it must not be reachable with rows."""
    server = _FakeServer(databases=_all_schemas())
    state = vanilla_repair.gate("pw", sql_query=server.query, exec_stdin=server.exec_stdin).probe()
    assert state.state == "partial"
    assert state.repairable is True


def test_a_character_row_stops_the_probe_short_of_the_branch_that_deletes() -> None:
    """Somebody's server is `populated`, whatever else is missing."""
    characters = ENTRY.databases.characters
    server = _FakeServer(
        databases=_all_schemas(),
        tables={characters: ["characters"]},
        rows={(characters, "characters"): 3},
    )
    state = vanilla_repair.gate("pw", sql_query=server.query, exec_stdin=server.exec_stdin).probe()
    assert state.state == "populated"
    assert state.repairable is False


def test_the_probe_asks_this_installs_container_with_the_client_the_entry_declares() -> None:
    """A probe naming `mysql` against `mariadb:11` cannot run, so it cannot answer."""
    assert NATIVE is not None
    server = _FakeServer(databases=_all_schemas())
    vanilla_repair.gate("secret", sql_query=server.query, exec_stdin=server.exec_stdin).probe()
    assert set(server.clients) == {NATIVE.db.client}
    assert set(server.containers) == {ENTRY.container_spec().db}
    assert set(server.passwords) == {"secret"}


def test_a_reset_drops_only_this_games_schemas() -> None:
    """From `partial`, and only the plan's own names — never a stranger's database."""
    server = _FakeServer(databases=[*_all_schemas(), "someone_elses_db"])
    dropped = vanilla_repair.gate(
        "pw", sql_query=server.query, exec_stdin=server.exec_stdin
    ).reset()
    assert set(dropped) == set(_all_schemas())
    assert "someone_elses_db" not in server.dropped


def test_import_gate_reads_the_installs_password_and_probes_with_it(tmp_path: Path) -> None:
    """The generated password lives in the install's own file; nothing else knows it."""
    plan_file = ENTRY.install.password.file
    assert plan_file is not None
    (tmp_path / plan_file).write_text("vanilla-0123456789abcdef\n", encoding="utf-8")
    server = _FakeServer(databases=_all_schemas(), marker_hash=PLAN.plan_hash())

    probe, _ = vanilla_repair.import_gate(
        tmp_path, sql_query=server.query, exec_stdin=server.exec_stdin
    )
    assert probe().state == "imported"
    assert set(server.passwords) == {"vanilla-0123456789abcdef"}


def test_an_unknowable_password_makes_the_probe_unreadable_rather_than_raising(
    tmp_path: Path,
) -> None:
    """`ImportProbe`'s callers are a status timer and a button; neither can catch.

    `unreadable` is not `repairable`, so the destructive action stays hidden.
    The reset is the opposite and refuses out loud.
    """
    probe, reset = vanilla_repair.import_gate(tmp_path)
    state = probe()
    assert state.state == "unreadable"
    assert state.repairable is False
    with pytest.raises(InstallerError, match="Nothing was dropped"):
        reset()
