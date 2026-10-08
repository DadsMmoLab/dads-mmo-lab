"""T577: the Tortoise realm is marked offline whenever its world is not up.

The core only ever CLEARS the offline bit (`Master.cpp:228`, right before the world
listens) and never sets it, so realmd advertised the realm as online for the whole load
and a 1.x client sent to a world that was not listening went back to the realm list
without a word. Yu'lon sets the bit before the world starts and before it stops; the
core clears it when the world listens. Driven here on the fake docker seams, in order.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.test_families_cmangos import Recorder
from tests.test_families_cmangos import context as cm_context
from tests.test_families_cmangos import engine as cm_engine
from yulon import docker, realm_flag
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.catalog.installer import InstallStopped
from yulon.controller_wow_tortoise import botdash, game
from yulon.controller_wow_tortoise.controller import TortoiseController

TORTOISE = load_catalog().get("wow-tortoise")
STATEMENT = "UPDATE tw_logon.realmlist SET realmflags = realmflags | 2 WHERE id=1;"


ONLINE_STATEMENT = "UPDATE tw_logon.realmlist SET realmflags = realmflags & ~2 WHERE id=1;"


def test_the_tortoise_online_statement_clears_only_the_offline_bit() -> None:
    assert realm_flag.online_statement(TORTOISE) == ONLINE_STATEMENT


def test_the_tortoise_statement_sets_the_offline_bit_on_its_realm_row() -> None:
    assert realm_flag.offline_statement(TORTOISE) == STATEMENT


def test_the_realm_id_and_column_come_from_the_entry() -> None:
    other = TORTOISE.model_copy(
        update={
            "realmlist": TORTOISE.realmlist.model_copy(
                update={"realm_id": 7, "offline_flag_column": "flag"}
            )
        }
    )
    assert realm_flag.offline_statement(other) == (
        "UPDATE tw_logon.realmlist SET flag = flag | 2 WHERE id=7;"
    )


@pytest.mark.parametrize("game_id", ["wow-wotlk", "wow-tbc", "wow-vanilla", "wow-centurion"])
def test_the_other_games_are_left_alone(game_id: str) -> None:
    assert realm_flag.offline_statement(load_catalog().get(game_id)) is None


class Docker:
    """Every docker call the controller makes, in order, on one list."""

    def __init__(self, *, db_up: bool = True, sql_fails: bool = False) -> None:
        self.events: list[str] = []
        self.db_up = db_up
        self.world_up = True
        self.sql_fails = sql_fails
        self.stop_fails = False
        self.statements: list[str] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        spec = game.entry().container_spec()
        monkeypatch.setattr(
            docker,
            "status",
            lambda **_k: [
                *([spec.db] if self.db_up else []),
                *([spec.world] if self.world_up else []),
            ],
        )

        def start_database(*_a: Any, **_k: Any) -> bool:
            self.events.append("database")
            self.db_up = True
            return True

        def sql_query(
            container: str, client: str, password: str, schema: Any, sql: str, **_k: Any
        ) -> str:
            self.events.append("sql")
            self.statements.append(sql)
            if self.sql_fails:
                raise docker.DockerCommandError("the database is not answering")
            return ""

        def start_staged(*_a: Any, **_k: Any) -> bool:
            self.events.append("start")
            self.world_up = True
            return True

        def stop_staged(*_a: Any, **_k: Any) -> bool:
            self.events.append("stop")
            if self.stop_fails:
                raise docker.StopAbandoned("the Stop was given up")
            self.world_up = False
            return True

        monkeypatch.setattr(docker, "start_database", start_database)
        monkeypatch.setattr(docker, "sql_query", sql_query)
        monkeypatch.setattr(docker, "start_staged", start_staged)
        monkeypatch.setattr(docker, "stop_staged", stop_staged)


@pytest.fixture
def tortoise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TortoiseController, Docker]:
    plan = TORTOISE.install.password
    assert plan.file is not None
    (tmp_path / plan.file).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / plan.file).write_text("tw0123456789abcd\n", encoding="utf-8")
    fake = Docker()
    fake.install(monkeypatch)
    controller = TortoiseController(tmp_path)
    monkeypatch.setattr(controller, "refuse_start", lambda: None)
    monkeypatch.setattr(controller, "port_conflicts", lambda: [])
    monkeypatch.setattr(controller, "refuse_a_missing_database", lambda: None)
    monkeypatch.setattr(controller, "_put_back_the_zone_file", lambda: None)
    monkeypatch.setattr(botdash, "start_if_on", lambda *_a, **_k: fake.events.append("dashboard"))
    return controller, fake


def test_a_start_marks_the_realm_offline_before_the_world_container_starts(
    tortoise: tuple[TortoiseController, Docker],
) -> None:
    controller, fake = tortoise
    fake.world_up = False
    controller.start()
    assert fake.statements == [STATEMENT]
    assert fake.events.index("sql") < fake.events.index("start")


def test_a_start_with_the_database_down_brings_it_up_first(
    tortoise: tuple[TortoiseController, Docker],
) -> None:
    controller, fake = tortoise
    fake.db_up = False
    fake.world_up = False
    controller.start()
    assert fake.events[:2] == ["database", "sql"]
    assert fake.events[-1] == "start"


def test_a_start_goes_on_when_the_statement_could_not_be_run(
    tortoise: tuple[TortoiseController, Docker],
) -> None:
    controller, fake = tortoise
    fake.sql_fails = True
    fake.world_up = False
    controller.start()
    assert "start" in fake.events


def test_a_start_with_the_world_already_running_leaves_the_realm_row_alone(
    tortoise: tuple[TortoiseController, Docker],
) -> None:
    """Start stays live with only the authserver down; the world would never clear the bit."""
    controller, fake = tortoise
    assert fake.world_up
    controller.start()
    assert fake.statements == []
    assert "database" not in fake.events
    assert "start" in fake.events


def test_a_restart_marks_it_offline_on_the_way_down_and_again_before_the_start(
    tortoise: tuple[TortoiseController, Docker],
) -> None:
    controller, fake = tortoise
    controller.stop()
    controller.start()
    assert fake.events == ["sql", "stop", "database", "sql", "dashboard", "start"]


def test_a_stop_with_the_database_down_does_not_start_it_to_mark_the_realm(
    tortoise: tuple[TortoiseController, Docker],
) -> None:
    controller, fake = tortoise
    fake.db_up = False
    controller.stop()
    assert fake.events == ["stop"]


def test_a_stop_given_up_with_the_world_still_running_takes_the_offline_bit_off_again(
    tortoise: tuple[TortoiseController, Docker],
) -> None:
    controller, fake = tortoise
    fake.stop_fails = True
    with pytest.raises(docker.StopAbandoned):
        controller.stop()
    assert fake.statements == [STATEMENT, ONLINE_STATEMENT]
    assert fake.events == ["sql", "stop", "sql"]


def test_a_stop_that_failed_with_the_world_gone_leaves_the_bit_set(
    tortoise: tuple[TortoiseController, Docker],
) -> None:
    controller, fake = tortoise
    fake.stop_fails = True
    fake.world_up = False
    with pytest.raises(docker.StopAbandoned):
        controller.stop()
    assert fake.statements == [STATEMENT]


# -- the install's own start and a rebuild's recreate ---------------------------------


class Marks:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.kwargs: list[dict[str, Any]] = []

    def mark(
        self,
        entry: CatalogEntry,
        spec: docker.ContainerSpec,
        server_dir: Path,
        **kwargs: Any,
    ) -> None:
        self.events.append("mark")
        self.kwargs.append(kwargs)

    def start(self, spec: docker.ContainerSpec, server_dir: Path) -> bool:
        self.events.append("start")
        return True

    def recreate(self, spec: docker.ContainerSpec, server_dir: Path, **_k: Any) -> bool:
        self.events.append("recreate")
        return True


def test_the_installs_up_marks_the_realm_offline_before_the_start(tmp_path: Path) -> None:
    marks = Marks()
    eng = cm_engine(Recorder(), entry=TORTOISE, mark_realm_offline=marks.mark, start=marks.start)
    list(eng.stage_up(cm_context(tmp_path)))
    assert marks.events == ["mark", "start"]
    assert marks.kwargs == [{"unless_world_up": True}], "the install's up re-runs on a resume"


def test_a_rebuilds_recreate_marks_the_realm_offline_before_the_recreate(tmp_path: Path) -> None:
    marks = Marks()
    eng = cm_engine(
        Recorder(),
        entry=TORTOISE,
        docker_ready=lambda: True,
        mark_realm_offline=marks.mark,
        recreate=marks.recreate,
    )
    list(eng.stage_recreate(cm_context(tmp_path)))
    assert marks.events == ["mark", "recreate"]
    assert marks.kwargs == [{}], "a replace marks a world that is up on purpose"


def test_a_rebuilds_replace_that_gave_up_takes_the_offline_bit_off_again(tmp_path: Path) -> None:
    rec = Recorder()

    def given_up(spec: docker.ContainerSpec, server_dir: Path, **_k: Any) -> bool:
        raise docker.StopAbandoned("cancelled while the world was loading")

    eng = cm_engine(rec, entry=TORTOISE, docker_ready=lambda: True, recreate=given_up)
    with pytest.raises(InstallStopped):
        list(eng.stage_recreate(cm_context(tmp_path)))
    assert rec.realm_marks == [TORTOISE.id]
    assert rec.realm_clears == [TORTOISE.id]


def test_a_rebuilds_replace_that_worked_does_not_clear_it(tmp_path: Path) -> None:
    marks = Marks()
    eng = cm_engine(
        Recorder(),
        entry=TORTOISE,
        docker_ready=lambda: True,
        mark_realm_offline=marks.mark,
        clear_realm_offline=lambda *_a: marks.events.append("clear"),
        recreate=marks.recreate,
    )
    list(eng.stage_recreate(cm_context(tmp_path)))
    assert marks.events == ["mark", "recreate"]
