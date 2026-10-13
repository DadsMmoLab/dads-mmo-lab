"""T779: Yu'lon refuses City Bots' SQL while a real account sits on a bot's account id.

mod-city-bots owns account ids 12001-12400 (`CITYBOT<id>`); its characters SQL writes
`characters.account` for those ids without reading `acore_auth`, so a real account there would
be handed a bot's character (T777, pjerra/mod-city-bots#7). The check runs before every route
that streams the module's SQL: the Modules tab's "Apply module SQL" (`docker.apply_module_sql`),
a Rebuild's import and an update's import (`AzerothCoreInstaller`). Docker is never reached:
`DockerSql` is replaced where the guard builds it, and the importer is the Recorder's.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.support_native import ENTRY, Recorder, engine, install
from yulon import city_bots_guard, docker
from yulon.apply import ApplyRefusal
from yulon.catalog.installer import InstallerError, InstallOptions
from yulon.controller_wow_wotlk import docker_ctl, modules

IMPORTER = f"one-shot:{ENTRY.containers.db_import}"
PASSWORD = "s3cret-root-pw"


class FakeSql:
    """Stands in for `DockerSql`: records how it was built and what it was asked."""

    built: list[dict[str, Any]] = []
    asked: list[tuple[str, str]] = []
    answer: str = ""
    failure: Exception | None = None

    def __init__(self, container: str, password: str, **kwargs: Any) -> None:
        FakeSql.built.append({"container": container, "password": password, **kwargs})

    def query(self, db: str, statement: str) -> str:
        FakeSql.asked.append((db, statement))
        if FakeSql.failure is not None:
            raise FakeSql.failure
        return FakeSql.answer


@pytest.fixture(autouse=True)
def fake_sql(monkeypatch: pytest.MonkeyPatch) -> type[FakeSql]:
    FakeSql.built = []
    FakeSql.asked = []
    FakeSql.answer = ""
    FakeSql.failure = None
    monkeypatch.setattr(city_bots_guard, "DockerSql", FakeSql)
    return FakeSql


def a_server_with_city_bots(tmp_path: Path, *, with_password: bool = True) -> Path:
    server_dir = tmp_path / "wow"
    (server_dir / "modules" / "mod-city-bots").mkdir(parents=True)
    (server_dir / "modules" / "mod-playerbots").mkdir()
    if with_password:
        assert ENTRY.install.password.file is not None
        (server_dir / ENTRY.install.password.file).write_text(PASSWORD + "\n", encoding="utf-8")
    return server_dir


# ----------------------------------------------------------------- the check itself


def test_a_clean_account_range_lets_the_sql_through(tmp_path: Path) -> None:
    server_dir = a_server_with_city_bots(tmp_path)
    FakeSql.answer = ""

    city_bots_guard.refuse_if_accounts_in_the_way(ENTRY, server_dir, docker.ALL_MODULES)

    assert len(FakeSql.asked) == 1, FakeSql.asked


def test_a_real_account_on_a_bot_id_is_refused_by_id_and_name(tmp_path: Path) -> None:
    server_dir = a_server_with_city_bots(tmp_path)
    FakeSql.answer = "12005\tAnna\n"

    with pytest.raises(city_bots_guard.CityBotsAccountRefusal) as caught:
        city_bots_guard.refuse_if_accounts_in_the_way(ENTRY, server_dir, "mod-city-bots")

    said = str(caught.value)
    assert 'account 12005 "Anna"' in said, said
    assert "Nothing was applied" in said, said


def test_a_bot_name_on_an_id_outside_the_range_is_refused(tmp_path: Path) -> None:
    server_dir = a_server_with_city_bots(tmp_path)
    FakeSql.answer = "300\tCITYBOT12500\n"

    with pytest.raises(city_bots_guard.CityBotsAccountRefusal, match='account 300 "CITYBOT12500"'):
        city_bots_guard.refuse_if_accounts_in_the_way(ENTRY, server_dir, docker.ALL_MODULES)


def test_the_query_asks_the_right_question_of_the_auth_database(tmp_path: Path) -> None:
    """Read-only, in `acore_auth`, with the range and the name rule the module defines."""
    server_dir = a_server_with_city_bots(tmp_path)

    city_bots_guard.refuse_if_accounts_in_the_way(ENTRY, server_dir, docker.ALL_MODULES)

    ((db, statement),) = FakeSql.asked
    assert db == "auth"
    assert statement.startswith("SELECT ")
    for forbidden in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "REPLACE"):
        assert forbidden not in statement.upper(), statement
    assert "BETWEEN 12001 AND 12400" in statement
    assert "CONCAT('CITYBOT', id)" in statement
    assert "NOT BETWEEN 12001 AND 12400" in statement
    assert "'^CITYBOT[0-9]+$'" in statement


def test_the_password_is_this_folders_own_and_the_client_is_the_entrys(tmp_path: Path) -> None:
    server_dir = a_server_with_city_bots(tmp_path)

    city_bots_guard.refuse_if_accounts_in_the_way(ENTRY, server_dir, docker.ALL_MODULES)

    (built,) = FakeSql.built
    assert built["password"] == PASSWORD
    assert built["container"] == ENTRY.container_spec().db
    assert built["schemas"] == ENTRY.schema_map()
    assert ENTRY.install.native is not None
    assert built["client"] == ENTRY.install.native.db.client


def test_an_unknowable_password_refuses_with_the_password_text_and_asks_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T748: never the published `password` in its place."""
    server_dir = a_server_with_city_bots(tmp_path, with_password=False)
    unknown = "This server's password file is missing, so Yu'lon cannot know it and tried none."

    def refuse(self: object, folder: Path) -> str:
        raise city_bots_guard.DbPasswordUnknown(unknown)

    monkeypatch.setattr(type(ENTRY.install), "known_db_password", refuse)

    with pytest.raises(city_bots_guard.CityBotsAccountRefusal) as caught:
        city_bots_guard.refuse_if_accounts_in_the_way(ENTRY, server_dir, docker.ALL_MODULES)

    assert str(caught.value) == unknown
    assert FakeSql.built == [] and FakeSql.asked == []


@pytest.mark.parametrize(
    ("allowed", "present"),
    [
        (docker.ALL_MODULES, False),  # module not installed
        ("mod-playerbots", True),  # installed but withheld from the importer
    ],
)
def test_without_city_bots_in_the_importers_reach_nothing_is_asked(
    tmp_path: Path, allowed: str, present: bool
) -> None:
    server_dir = a_server_with_city_bots(tmp_path)
    if not present:
        (server_dir / "modules" / "mod-city-bots").rmdir()

    city_bots_guard.refuse_if_accounts_in_the_way(ENTRY, server_dir, allowed)

    assert FakeSql.built == [] and FakeSql.asked == []


def test_a_database_that_cannot_be_read_refuses(tmp_path: Path) -> None:
    server_dir = a_server_with_city_bots(tmp_path)
    FakeSql.failure = city_bots_guard.ApplyError("SQL failed (query -> acore_auth): no route")

    with pytest.raises(city_bots_guard.CityBotsAccountRefusal, match="nothing was applied"):
        city_bots_guard.refuse_if_accounts_in_the_way(ENTRY, server_dir, docker.ALL_MODULES)


def test_the_password_rides_the_environment_never_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the REAL `DockerSql`, with only the process launch replaced."""
    monkeypatch.setattr(city_bots_guard, "DockerSql", city_bots_guard_real_sql())
    server_dir = a_server_with_city_bots(tmp_path)
    launched: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        launched.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("yulon.apply.mysql_client", lambda *_a, **_k: "mysql")
    monkeypatch.setattr("yulon.platform.docker_prefix", lambda _distro=None: ["docker"])

    city_bots_guard.refuse_if_accounts_in_the_way(ENTRY, server_dir, docker.ALL_MODULES)

    ((argv, kwargs),) = launched
    assert PASSWORD not in " ".join(argv), argv
    assert "MYSQL_PWD" in argv
    env = kwargs["env"]
    assert isinstance(env, dict) and env["MYSQL_PWD"] == PASSWORD
    assert b"SELECT" in kwargs["input"]  # type: ignore[operator]


def city_bots_guard_real_sql() -> type:
    from yulon.apply import DockerSql

    return DockerSql


# ------------------------------------------------- the Modules tab's "Apply module SQL"


def test_the_button_hands_the_importer_a_check_that_refuses_before_anything_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir = a_server_with_city_bots(tmp_path)
    FakeSql.answer = "12005\tAnna\n"
    seen: dict[str, Any] = {}

    def fake(spec: docker.ContainerSpec, folder: Path, **kwargs: Any) -> docker.AttachedRun:
        seen.update(kwargs)
        kwargs["before_run"]()
        seen["streamed"] = True
        return docker.AttachedRun(0, ("done",))

    monkeypatch.setattr(docker, "apply_module_sql", fake)
    said: list[str] = []

    with pytest.raises(docker.DockerRefusal, match="12005"):
        modules.apply_module_sql(server_dir, spec=docker_ctl.SPEC, output=said.append, ledger=None)

    assert "streamed" not in seen


def test_the_button_runs_the_importer_when_the_range_is_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir = a_server_with_city_bots(tmp_path)
    streamed: list[bool] = []

    def fake(spec: docker.ContainerSpec, folder: Path, **kwargs: Any) -> docker.AttachedRun:
        kwargs["before_run"]()
        streamed.append(True)
        return docker.AttachedRun(0, ("done",))

    monkeypatch.setattr(docker, "apply_module_sql", fake)

    modules.apply_module_sql(server_dir, spec=docker_ctl.SPEC, output=lambda _l: None, ledger=None)

    assert streamed == [True]
    assert len(FakeSql.asked) == 1


def test_the_button_asks_nothing_when_city_bots_is_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir = a_server_with_city_bots(tmp_path)
    (server_dir / "modules" / "mod-city-bots").rmdir()

    def fake(spec: docker.ContainerSpec, folder: Path, **kwargs: Any) -> docker.AttachedRun:
        kwargs["before_run"]()
        return docker.AttachedRun(0, ("done",))

    monkeypatch.setattr(docker, "apply_module_sql", fake)

    modules.apply_module_sql(server_dir, spec=docker_ctl.SPEC, output=lambda _l: None, ledger=None)

    assert FakeSql.built == [] and FakeSql.asked == []


# ------------------------------------------------------- a Rebuild's and an update's import


def a_finished_install_with_city_bots(rec: Recorder, tmp_path: Path) -> Path:
    server_dir = tmp_path / "server"
    install(rec, server_dir)
    (server_dir / "modules" / "mod-city-bots").mkdir(parents=True)
    assert ENTRY.install.password.file is not None
    (server_dir / ENTRY.install.password.file).write_text(PASSWORD + "\n", encoding="utf-8")
    rec.calls.clear()
    rec.one_shot_modules.clear()
    return server_dir


def test_a_rebuild_with_a_clean_range_imports(tmp_path: Path) -> None:
    rec = Recorder(images=True)
    server_dir = a_finished_install_with_city_bots(rec, tmp_path)

    list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))

    assert rec.calls.count(IMPORTER) == 1, rec.calls
    assert len(FakeSql.asked) == 1


def test_a_rebuild_over_a_real_account_on_a_bot_id_streams_nothing(tmp_path: Path) -> None:
    rec = Recorder(images=True)
    server_dir = a_finished_install_with_city_bots(rec, tmp_path)
    FakeSql.answer = "12005\tAnna\n"

    with pytest.raises(InstallerError, match='account 12005 "Anna"'):
        list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))

    assert IMPORTER not in rec.calls, rec.calls


def test_an_update_over_a_bot_name_on_a_stray_id_streams_nothing(tmp_path: Path) -> None:
    rec = Recorder()
    server_dir = tmp_path / "server"
    install(rec, server_dir)
    (server_dir / "modules" / "mod-city-bots").mkdir(parents=True)
    assert ENTRY.install.password.file is not None
    (server_dir / ENTRY.install.password.file).write_text(PASSWORD + "\n", encoding="utf-8")
    for source in ENTRY.emulator.sources:
        rec.heads[server_dir / source.dest] = "a" * 40
        rec.upstream[server_dir / source.dest] = "b" * 40
    rec.calls.clear()
    FakeSql.answer = "300\tCITYBOT12500\n"

    with pytest.raises(InstallerError, match="CITYBOT12500"):
        list(engine(rec).update_to_latest(InstallOptions(server_dir=server_dir)))

    assert IMPORTER not in rec.calls, rec.calls


def test_a_rebuild_without_city_bots_asks_the_database_nothing(tmp_path: Path) -> None:
    rec = Recorder(images=True)
    server_dir = tmp_path / "server"
    install(rec, server_dir)
    rec.calls.clear()

    list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))

    assert rec.calls.count(IMPORTER) == 1, rec.calls
    assert FakeSql.built == [] and FakeSql.asked == []


# ------------------------------------------------------------ the refusal's own sentences


def test_a_bot_name_on_another_id_is_given_its_own_reason(tmp_path: Path) -> None:
    """T779 low note 1: the stray-name case says why it blocks, not only the in-range reason."""
    server_dir = a_server_with_city_bots(tmp_path)
    FakeSql.answer = "300\tCITYBOT12500\n"

    with pytest.raises(city_bots_guard.CityBotsAccountRefusal) as caught:
        city_bots_guard.refuse_if_accounts_in_the_way(ENTRY, server_dir, docker.ALL_MODULES)

    said = str(caught.value)
    assert 'account 300 "CITYBOT12500"' in said, said
    assert "collide" in said and "username" in said, said
    # The in-range reason is not claimed for an account that is not on a bot's number.
    assert "kick that player" not in said, said


def test_the_button_refuses_in_its_own_words_when_the_catalog_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T779 low note 2: a catalog read error inside `before_run` is a `DockerRefusal` too."""
    server_dir = a_server_with_city_bots(tmp_path)
    streamed: list[bool] = []

    def fake(spec: docker.ContainerSpec, folder: Path, **kwargs: Any) -> docker.AttachedRun:
        kwargs["before_run"]()
        streamed.append(True)
        return docker.AttachedRun(0, ("done",))

    def broken(*_a: object, **_k: object) -> object:
        raise ValueError("catalog.json: line 3 is not JSON")

    monkeypatch.setattr(docker, "apply_module_sql", fake)
    monkeypatch.setattr(modules, "load_catalog", broken)

    with pytest.raises(docker.DockerRefusal, match="line 3 is not JSON"):
        modules.apply_module_sql(
            server_dir, spec=docker_ctl.SPEC, output=lambda _l: None, ledger=None
        )

    assert streamed == []


# ------------------------------------------------------------ the module Install press (T787)


def _install_press(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, started: bool = True
) -> tuple[Path, Any, Any]:
    """The WotLK factory's applier over a scripted database, the guard's reads in its log."""
    from tests.test_apply import _city_bots_clone, _FakeGit, _ready, _ScriptedDb

    server_dir = a_server_with_city_bots(tmp_path)
    (server_dir / "modules" / "mod-city-bots").rmdir()  # the press clones it
    (server_dir / "modules" / "mod-playerbots" / ".git").mkdir()
    db = _ScriptedDb(_ready())

    def start() -> bool:
        db.log.append("start_database")
        return started

    def logged_query(self: FakeSql, db_name: str, statement: str) -> str:
        db.log.append(f"guard {db_name}")
        FakeSql.asked.append((db_name, statement))
        if FakeSql.failure is not None:
            raise FakeSql.failure
        return FakeSql.answer

    monkeypatch.setattr(FakeSql, "query", logged_query)
    applier = modules.applier(
        server_dir,
        world_running=db.world_running,
        start_database=start,
        git=_FakeGit(_city_bots_clone(), unmodified=True, no_local_commits=True),
        sql=db,
    )
    return server_dir, applier, db


def _city_bots_manifest() -> Any:
    from tests.test_apply import _shipped

    return _shipped("mod-city-bots")


ROSTER_FILE = "2026_07_15_00_citizen_roster.sql"


def test_the_install_press_imports_the_roster_when_the_range_is_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, applier, db = _install_press(tmp_path, monkeypatch)

    applier.install(_city_bots_manifest())

    assert db.files == [("playerbots", ROSTER_FILE)]
    assert len(FakeSql.asked) == 1 and FakeSql.asked[0][0] == "auth"
    # Asked with the database up, and before the roster is sent.
    assert db.log.index("start_database") < db.log.index("guard auth")
    assert db.log.index("guard auth") < db.log.index(f"run_file playerbots:{ROSTER_FILE}")


def test_the_install_press_over_a_real_account_on_a_bot_id_sends_no_roster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, applier, db = _install_press(tmp_path, monkeypatch)
    FakeSql.answer = "12005\tAnna\n"

    with pytest.raises(ApplyRefusal) as caught:
        applier.install(_city_bots_manifest())

    said = str(caught.value)
    assert 'account 12005 "Anna"' in said, said
    assert "Nothing was applied" in said, said
    assert db.files == [] and db.statements == []
    assert not [line for line in db.log if line.startswith("query playerbots")], db.log


def test_the_install_press_with_an_unknowable_password_refuses_with_the_password_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T748: the guard never tries the published `password` in its place."""
    _, applier, db = _install_press(tmp_path, monkeypatch)
    unknown = "This server's password file is missing, so Yu'lon cannot know it and tried none."

    def refuse(self: object, folder: Path) -> str:
        raise city_bots_guard.DbPasswordUnknown(unknown)

    monkeypatch.setattr(type(ENTRY.install), "known_db_password", refuse)

    with pytest.raises(ApplyRefusal) as caught:
        applier.install(_city_bots_manifest())

    assert str(caught.value) == unknown
    assert db.files == [] and FakeSql.asked == []


def test_the_install_press_refuses_when_the_database_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, applier, db = _install_press(tmp_path, monkeypatch)
    FakeSql.failure = city_bots_guard.ApplyError("SQL failed (query -> acore_auth): no route")

    with pytest.raises(ApplyRefusal, match="nothing was applied"):
        applier.install(_city_bots_manifest())

    assert db.files == []


@pytest.mark.parametrize("started", [True, False], ids=["was-stopped", "was-running"])
def test_the_install_press_starts_the_database_once_and_leaves_it_as_the_seam_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, started: bool
) -> None:
    """The guard reuses the press's own start (`docker.start_database`), which stops nothing.

    One start for the press, guard included; a database this press started is reported, one
    that was already up is not, and nothing after the guard touches Docker's lifecycle.
    """
    _, applier, db = _install_press(tmp_path, monkeypatch, started=started)

    report = applier.install(_city_bots_manifest())

    assert db.log.count("start_database") == 1, db.log
    assert db.log.index("start_database") < db.log.index("guard auth")
    said = any("started the database alone" in line for line in report.done)
    assert said is started, report.done


def test_another_module_s_install_press_asks_the_guard_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from yulon.manifest import parse_manifest
    from yulon.resources import manifests_dir

    raw = json.loads(
        (manifests_dir() / "wow-wotlk" / "modules" / "mod-city-bots.json").read_text("utf-8")
    )
    other = parse_manifest({**raw, "id": "mod-other-bots", "name": "Other Bots"})
    _, applier, db = _install_press(tmp_path, monkeypatch)

    applier.install(other)

    assert db.files == [("playerbots", ROSTER_FILE)]
    assert FakeSql.built == [] and FakeSql.asked == []


def test_the_install_press_asks_the_server_whose_database_the_applier_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second AzerothCore server (Unbound) is checked as itself, never as WotLK (T552)."""
    from yulon.catalog.catalog import load_catalog

    unbound = load_catalog().get("wow-unbound")
    server_dir, _, db = _install_press(tmp_path, monkeypatch)
    assert unbound.install.password.file is not None
    (server_dir / unbound.install.password.file).write_text("ub-pw\n", encoding="utf-8")
    from tests.test_apply import _city_bots_clone, _FakeGit

    applier = modules.applier(
        server_dir,
        world_running=db.world_running,
        start_database=lambda: True,
        git=_FakeGit(_city_bots_clone(), unmodified=True, no_local_commits=True),
        sql=db,
        db_container=unbound.container_spec().db,
    )

    applier.install(_city_bots_manifest())

    (built,) = FakeSql.built
    assert built["container"] == unbound.container_spec().db
    assert built["password"] == "ub-pw"
    assert built["schemas"] == unbound.schema_map()
