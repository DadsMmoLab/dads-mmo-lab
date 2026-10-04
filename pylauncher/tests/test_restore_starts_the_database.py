"""T205: Restore works with the server stopped, by starting the database alone for it.

Reported from a Steam Deck (WoW WotLK, 2026-10-03): the Maintenance tab's
restore refused while the world and login servers ran ("Stop the server and try
again"), and refused again once Stop had taken everything down, because Stop
takes the database with it ("…is not running, so there is nothing to restore
into"). No press in the app reached the state the restore accepted; the player
got there with `docker compose stop` in a terminal. Backup had the same dead end
and T76 gave it `DatabaseAlone`; these tests hold Restore to the same handling.

The doubles are built to behave like the real thing, because a fake that
succeeded either way could not tell a press that started the database from one
that did not:

* `_Stack` is the install's containers. Its census is what the REAL
  `plan_restore()` / `restore()` read, and `bring_up()` changes it, so a
  re-plan after the start sees the database up exactly as `docker ps` would.
* `_Mysql` refuses every call while the database container is down -- what
  `docker exec` into a stopped container does -- so a restore that skipped the
  start fails rather than "succeeding" into nothing.
* The planner and the restore are `controller_wow_wotlk.maintenance`'s own,
  bound the way `_for_wotlk()` binds them; only the census and the client are
  doubles.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import IO

import pytest

from tests.test_controller_view import _Ps, _services
from tests.test_maintenance import good_dump
from yulon import docker, forgetting, runner
from yulon.catalog.catalog import load_catalog
from yulon.controller_wow_wotlk import maintenance
from yulon.controller_wow_wotlk.maintenance import MaintenanceError, RestorePlan
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import ControllerServices, ControllerView, DatabaseAlone
from yulon.ui.widgets.job import run_inline

CATALOG = load_catalog()
WOTLK = CATALOG.get("wow-wotlk")
TORTOISE = CATALOG.get("wow-tortoise")
SPEC = WOTLK.container_spec()
EVERYTHING = {SPEC.db, SPEC.auth, SPEC.world}

PLANNED_START = (
    "The database will be started on its own for this restore and stopped again afterwards; "
    "the game servers stay stopped."
)


class _Stack:
    """This install's containers: what is running, and the two T76/T205 halves that change it."""

    def __init__(self, *running: str, refuses: str = "") -> None:
        self.running = set(running)
        self.refuses = refuses
        self.starts = 0
        self.stops = 0
        self.because: list[str] = []

    def census(self) -> list[str]:
        return sorted(self.running)

    def bring_up(self, because: str) -> bool:
        """`docker.start_database()`'s contract: True only where it had to start it."""
        self.because.append(because)
        if SPEC.db in self.running:
            return False
        self.starts += 1
        if self.refuses:
            raise docker.DockerCommandError(f"{self.refuses}, so {because}")
        self.running.add(SPEC.db)
        return True

    def take_down(self) -> None:
        self.stops += 1
        self.running.discard(SPEC.db)

    def alone(self) -> DatabaseAlone:
        return DatabaseAlone(bring_up=self.bring_up, take_down=self.take_down)


class _Mysql:
    """The database container's client: refuses while it is down, as `docker exec` does."""

    def __init__(self, stack: _Stack, *, fails_load: str = "") -> None:
        self.stack = stack
        self.fails_load = fails_load
        self.loaded: list[bytes] = []
        self.up_while_loading: list[bool] = []

    def _up(self) -> None:
        if SPEC.db not in self.stack.running:
            raise MaintenanceError(f"Error response from daemon: {SPEC.db} is not running")

    def databases(self) -> tuple[str, ...]:
        self._up()
        return ("acore_auth", "acore_characters", "acore_world")

    def dump_into(self, database: str, sink: IO[bytes]) -> None:
        self._up()
        sink.write(good_dump(database))

    def load_from(self, source: IO[bytes]) -> None:
        self._up()
        self.up_while_loading.append(SPEC.db in self.stack.running)
        if self.fails_load:
            raise MaintenanceError(self.fails_load)
        self.loaded.append(source.read())


@pytest.fixture(autouse=True)
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    """Every `docker` command any press runs, recorded; nothing reaches a daemon."""
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


def _lifecycle_calls(ps: _Ps) -> list[list[str]]:
    """The commands that start, stop or recreate this server's containers."""
    return [
        c
        for c in ps.calls
        if c[:3]
        in (
            ["docker", "compose", "up"],
            ["docker", "compose", "stop"],
            ["docker", "compose", "down"],
        )
        or c[:2] == ["docker", "stop"]
    ]


def _real_maintenance(
    tmp_path: Path, stack: _Stack, mysql: _Mysql, *, seam: bool = True
) -> ControllerServices:
    """`_services()`, with the real planner and restore bound as `_for_wotlk()` binds them."""

    def plan_restore(path: Path, *, can_start_database: bool = False) -> RestorePlan:
        return maintenance.plan_restore(
            path, tmp_path, running=stack.census, can_start_database=can_start_database
        )

    return replace(
        _services(_Ps(), tmp_path, []),
        plan_restore=plan_restore,
        restore=lambda plan: maintenance.restore(
            plan, mysql, confirm=plan.token, running=stack.census
        ),
        interrupted_restore=lambda: maintenance.interrupted_restore(tmp_path),
        database_alone=stack.alone() if seam else None,
    )


def _view(services: ControllerServices, entry: object = WOTLK) -> ControllerView:
    return ControllerView(
        entry, services, status_poll_ms=0, job_runner=run_inline  # type: ignore[arg-type]
    )


def _select_backup(view: ControllerView, tmp_path: Path) -> Path:
    """A real mysqldump in the backups dir, listed and selected as a player would."""
    path = maintenance.backups_dir(tmp_path) / "20261003_120000_acore_characters.sql"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(good_dump("acore_characters"))
    view.refresh_backups()
    for row in range(view.backup_list.count()):
        if path.name in view.backup_list.item(row).text():
            view.backup_list.setCurrentRow(row)
    return path


def _failures(view: ControllerView) -> list[str]:
    seen: list[str] = []
    view.action_failed.connect(seen.append)
    return seen


# -- the reported case ---------------------------------------------------------------------


def test_a_stopped_server_restores_by_starting_the_database_alone_and_stopping_it_again(
    qapp: object, tmp_path: Path
) -> None:
    """The Steam Deck report, through the Maintenance tab: Stop, then Restore, works.

    Asserted in the order the fix claims. Planning starts nothing; the plan says
    the database will be started; the load ran with the database up and the
    game servers down; and the press left the server stopped as it found it.
    """
    stack = _Stack()
    mysql = _Mysql(stack)
    view = _view(_real_maintenance(tmp_path, stack, mysql))
    path = _select_backup(view, tmp_path)

    view.show_restore_plan()
    assert stack.starts == 0, "showing the plan started the database"
    assert view.restore_button.isEnabled(), view.maintenance_report.toPlainText()
    assert PLANNED_START in view.maintenance_report.toPlainText()

    view.run_restore()

    assert mysql.loaded == [path.read_bytes()], view.maintenance_report.toPlainText()
    assert mysql.up_while_loading == [True]
    assert stack.starts == 1 and stack.stops == 1
    assert stack.running == set(), "the press did not leave the server as it found it"
    assert stack.because == ["the restore was not started"]
    assert "Restored acore_characters" in view.maintenance_report.toPlainText()


def test_a_database_already_up_is_neither_started_nor_stopped_by_a_restore(
    qapp: object, tmp_path: Path
) -> None:
    """Only a database this press started may be stopped by it."""
    stack = _Stack(SPEC.db)
    mysql = _Mysql(stack)
    view = _view(_real_maintenance(tmp_path, stack, mysql))
    _select_backup(view, tmp_path)

    view.show_restore_plan()
    assert view.restore_button.isEnabled()
    assert PLANNED_START not in view.maintenance_report.toPlainText()
    view.run_restore()

    assert len(mysql.loaded) == 1
    assert stack.starts == 0 and stack.stops == 0
    assert stack.running == {SPEC.db}, "a database the user had up was taken down"


def test_a_running_world_still_refuses_and_starts_nothing(qapp: object, tmp_path: Path) -> None:
    """The game servers' rule is unchanged, and "Stop the server and try again" now works.

    Only the world-and-login rule is broken here: the database is up, so the
    database rule could not refuse whatever T205 did to it.
    """
    stack = _Stack(*EVERYTHING)
    mysql = _Mysql(stack)
    view = _view(_real_maintenance(tmp_path, stack, mysql))
    _select_backup(view, tmp_path)

    view.show_restore_plan()
    text = view.maintenance_report.toPlainText()
    assert not view.restore_button.isEnabled()
    assert "Stop the server and try again." in text
    assert "nothing to restore into" not in text
    view.run_restore()
    assert mysql.loaded == [] and stack.starts == 0 and stack.stops == 0

    # And the press the refusal names now leads to a restore that goes ahead.
    stack.running.clear()
    view.show_restore_plan()
    assert view.restore_button.isEnabled(), view.maintenance_report.toPlainText()
    view.run_restore()
    assert len(mysql.loaded) == 1 and stack.running == set()


def test_a_server_started_after_the_plan_refuses_the_restore_and_is_left_running(
    qapp: object, tmp_path: Path
) -> None:
    """The plan is a census, not a permission slip: the press re-plans with the database up.

    Planned with everything stopped; the player then pressed Start. The press
    finds the database already up (so starts and stops nothing) and the re-plan
    refuses for the running world, exactly as it did before T205.
    """
    stack = _Stack()
    mysql = _Mysql(stack)
    view = _view(_real_maintenance(tmp_path, stack, mysql))
    _select_backup(view, tmp_path)
    failures = _failures(view)

    view.show_restore_plan()
    assert view.restore_button.isEnabled()
    stack.running |= EVERYTHING
    view.run_restore()

    assert mysql.loaded == []
    assert failures and "Stop the server and try again." in failures[-1], failures
    assert stack.starts == 0 and stack.stops == 0
    assert stack.running == EVERYTHING, "the player's running server was touched"


# -- every way out of the press puts the database back ---------------------------------------


def test_a_database_that_cannot_start_says_nothing_was_restored(
    qapp: object, tmp_path: Path
) -> None:
    """Bring-up failed: say so plainly, restore nothing, and leave nothing running.

    `take_down()` is not called: a `bring_up()` that raises answered nothing,
    and the real one puts back anything it started itself (see the factory
    test below). The double started nothing, so the stack is as it was.
    """
    stack = _Stack(refuses=f"{SPEC.db} did not report healthy within 180s")
    mysql = _Mysql(stack)
    view = _view(_real_maintenance(tmp_path, stack, mysql))
    _select_backup(view, tmp_path)
    failures = _failures(view)

    view.show_restore_plan()
    view.run_restore()

    assert mysql.loaded == []
    assert failures, "the failed start was not reported"
    assert failures[-1].startswith("Nothing was restored"), failures[-1]
    assert "did not report healthy" in failures[-1], "docker's own reason was dropped"
    assert stack.running == set() and stack.stops == 0
    assert maintenance.interrupted_restore(tmp_path) is None, "a marker for a restore never begun"


def test_a_failed_restore_still_stops_the_database_it_started(qapp: object, tmp_path: Path) -> None:
    """A load that fails part-way still puts the database back down (`finally`)."""
    stack = _Stack()
    mysql = _Mysql(stack, fails_load="ERROR 2013 (HY000): Lost connection")
    view = _view(_real_maintenance(tmp_path, stack, mysql))
    _select_backup(view, tmp_path)
    failures = _failures(view)

    view.show_restore_plan()
    view.run_restore()

    assert failures and "failed part-way" in failures[-1], failures
    assert mysql.up_while_loading == [True]
    assert stack.starts == 1 and stack.stops == 1
    assert stack.running == set()


def test_a_restore_refused_at_the_press_still_stops_the_database_it_started(
    qapp: object, tmp_path: Path
) -> None:
    """The file changed between the plan and the press: refused, and the database put back.

    The plan was made with the database down and the press re-planned with it
    up; the token still caught the replaced file, so the protection is not
    weakened by the database being started in between.
    """
    stack = _Stack()
    mysql = _Mysql(stack)
    view = _view(_real_maintenance(tmp_path, stack, mysql))
    path = _select_backup(view, tmp_path)
    failures = _failures(view)

    view.show_restore_plan()
    path.write_bytes(good_dump("acore_characters", "acore_world"))
    view.run_restore()

    assert failures and "changed in between" in failures[-1], failures
    assert mysql.loaded == []
    assert stack.starts == 1 and stack.stops == 1
    assert stack.running == set()


def test_a_tab_without_the_database_seam_refuses_a_stopped_server_as_before(
    qapp: object, tmp_path: Path
) -> None:
    """`database_alone=None` keeps today's behaviour: nothing can start it, so it refuses."""
    stack = _Stack()
    mysql = _Mysql(stack)
    view = _view(_real_maintenance(tmp_path, stack, mysql, seam=False))
    _select_backup(view, tmp_path)

    view.show_restore_plan()
    assert not view.restore_button.isEnabled()
    assert "nothing to restore into" in view.maintenance_report.toPlainText()


# -- T144's path, on the one game that has the bot request -----------------------------------


class _Taken:
    """What T144's `before_restore()` hands back when it set a pending request off."""

    note = "The random-bot rebuild request was set back to off."


class _BotRequest:
    """T144's seam: records what the press asked of it; `pending` is a request it takes back."""

    def __init__(self, *, pending: bool = False) -> None:
        self.calls: list[str] = []
        self.pending = pending

    def before_restore(self) -> _Taken | None:
        self.calls.append("before-restore")
        return _Taken() if self.pending else None

    def after_a_failed_restore(self, taken: _Taken, *, loaded: bool | None) -> str:
        self.calls.append(f"after-a-failed-restore:loaded={loaded}")
        return "The request was put back as it was."

    def restore_warning(self) -> None:
        return None

    def take_module_moved(self) -> None:
        return None


class _NoBots:
    def page(self, *, after: object = None, name_like: str = "") -> object:
        return None


def _tortoise(
    tmp_path: Path, stack: _Stack, mysql: _Mysql, *, pending: bool = False
) -> tuple[ControllerView, _BotRequest]:
    request = _BotRequest(pending=pending)
    services = replace(
        _real_maintenance(tmp_path, stack, mysql), bots=_NoBots(), bot_pool_rebuild=request
    )
    return _view(services, TORTOISE), request


def test_the_bot_request_path_also_starts_and_stops_the_database(
    qapp: object, tmp_path: Path
) -> None:
    """`_restore_with_the_bot_request()` re-plans before it touches the conf; it needs the db up."""
    stack = _Stack()
    mysql = _Mysql(stack)
    view, request = _tortoise(tmp_path, stack, mysql)
    _select_backup(view, tmp_path)

    view.show_restore_plan()
    assert view.restore_button.isEnabled(), view.maintenance_report.toPlainText()
    view.run_restore()

    assert request.calls == ["before-restore"]
    assert len(mysql.loaded) == 1, view.maintenance_report.toPlainText()
    assert stack.starts == 1 and stack.stops == 1 and stack.running == set()


def test_the_bot_request_path_refuses_a_server_started_after_the_plan(
    qapp: object, tmp_path: Path
) -> None:
    """Its own re-plan refuses before the conf is touched, and the server is left running."""
    stack = _Stack()
    mysql = _Mysql(stack)
    view, request = _tortoise(tmp_path, stack, mysql)
    _select_backup(view, tmp_path)
    failures = _failures(view)

    view.show_restore_plan()
    stack.running |= EVERYTHING
    view.run_restore()

    assert failures and "Stop the server and try again." in failures[-1], failures
    assert request.calls == [], "the conf was touched for a restore that refused"
    assert mysql.loaded == [] and stack.stops == 0 and stack.running == EVERYTHING


# -- the wiring every game really gets -------------------------------------------------------


def test_every_game_plans_a_stopped_server_as_one_its_restore_starts_the_database_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the callable each game factory builds: the keyword reaches the shared planner.

    Nothing is running in this fake. Without the keyword every game still says
    its own database is not running (`test_each_game_s_restore_plan_censuses_
    its_own_containers`); with it, none does, and every plan records the start.
    """
    monkeypatch.setattr(
        docker.runner,
        "run",
        lambda cmd, cwd=None, timeout=None: subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    for game in controller_view_module._FACTORIES:
        entry = CATALOG.get(game)
        services = ControllerServices.for_entry(entry, tmp_path / game)
        assert services.database_alone is not None, game
        plan = services.plan_restore(tmp_path / "missing.sql", can_start_database=True)
        refusals = " ".join(plan.refusals)
        assert "is not running" not in refusals, (game, refusals)
        assert plan.starts_database is True, game


class _Docker:
    """`docker.status` / `start_database` / `stop_containers`, for the factory's own halves."""

    def __init__(self, running: list[str] | None, *, start_fails: bool = False) -> None:
        self.running = running
        self.start_fails = start_fails
        self.started: list[str] = []
        self.stopped: list[list[str]] = []

    def status(self, wsl_distro: str | None = None) -> list[str]:
        if self.running is None:
            raise docker.DockerCommandError("Cannot connect to the Docker daemon")
        return list(self.running)

    def start_database(
        self,
        spec: docker.ContainerSpec,
        server_dir: Path,
        *,
        because: str = "",
        wsl_distro: str | None = None,
    ) -> bool:
        self.started.append(because)
        assert self.running is not None
        self.running.append(spec.db)  # `compose up -d` ran; the health wait is what failed
        if self.start_fails:
            raise docker.DockerCommandError(
                f"{spec.db} did not report healthy within 180s, so {because}."
            )
        return True

    def stop_containers(self, names: list[str], *, wsl_distro: str | None = None) -> None:
        self.stopped.append(list(names))
        if self.running is not None:
            for name in names:
                if name in self.running:
                    self.running.remove(name)


def _factory(monkeypatch: pytest.MonkeyPatch, fake: _Docker) -> Iterator[DatabaseAlone]:
    for name in ("status", "start_database", "stop_containers"):
        monkeypatch.setattr(controller_view_module.docker, name, getattr(fake, name))
    for game in controller_view_module._FACTORIES:
        spec = CATALOG.get(game).container_spec()
        yield controller_view_module._database_alone(spec, Path("srv"), wsl_distro=None)


def test_a_start_that_fails_after_compose_started_the_container_stops_it_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`start_database()` raises on a health timeout with the container UP; it must not stay up.

    The database was down before the press, so whatever is running under its
    name afterwards was started by it -- and a restore that "leaves nothing
    running" when it could not start the database has to put that back too.
    """
    fake = _Docker([], start_fails=True)
    for alone in _factory(monkeypatch, fake):
        fake.running, fake.stopped = [], []
        with pytest.raises(docker.DockerCommandError, match="so nothing was run"):
            alone.bring_up("nothing was run")
        assert fake.running == [], "a database whose start failed was left running"
        assert len(fake.stopped) == 1


def test_a_start_that_never_reached_docker_stops_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Docker would not say what runs: nothing was started, so nothing may be stopped."""
    fake = _Docker(None)
    for alone in _factory(monkeypatch, fake):
        with pytest.raises(docker.DockerCommandError):
            alone.bring_up("nothing was run")
    assert fake.started == [] and fake.stopped == []


def test_a_database_already_up_is_not_started_by_the_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Up already: `bring_up()` answers False and touches nothing, so nothing is stopped later."""
    for game in controller_view_module._FACTORIES:
        db = CATALOG.get(game).container_spec().db
        fake = _Docker([db])
        monkeypatch.setattr(controller_view_module.docker, "status", fake.status)
        monkeypatch.setattr(controller_view_module.docker, "start_database", fake.start_database)
        monkeypatch.setattr(controller_view_module.docker, "stop_containers", fake.stop_containers)
        alone = controller_view_module._database_alone(
            CATALOG.get(game).container_spec(), Path("srv"), wsl_distro=None
        )
        assert alone.bring_up("nothing was run") is False, game
        assert fake.started == [] and fake.stopped == [], game


def test_a_failed_restore_on_the_bot_request_path_puts_the_request_back_and_the_database_down(
    qapp: object, tmp_path: Path
) -> None:
    """T144's failure branch, reached with a database this press started (review round 1).

    The load fails after the marker is written, so T144 keeps the request off
    and says so -- and the database the press started is still taken down.
    """
    stack = _Stack()
    mysql = _Mysql(stack, fails_load="ERROR 2013 (HY000): Lost connection")
    view, request = _tortoise(tmp_path, stack, mysql, pending=True)
    _select_backup(view, tmp_path)
    failures = _failures(view)

    view.show_restore_plan()
    view.run_restore()

    assert request.calls == ["before-restore", "after-a-failed-restore:loaded=True"]
    assert failures and "failed part-way" in failures[-1], failures
    assert "The request was put back as it was." in failures[-1]
    assert stack.starts == 1 and stack.stops == 1 and stack.running == set()


def test_a_start_that_failed_before_the_container_existed_stops_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`compose up` itself failed: nothing is running, so nothing is stopped (review round 1).

    A `docker stop` of a container that was never created fails too, and the
    message would then have claimed the press started something it had not.
    """

    class NeverCreated(_Docker):
        def start_database(self, spec: docker.ContainerSpec, server_dir: Path, **_: object) -> bool:
            self.started.append("compose up")
            raise docker.DockerCommandError(
                "docker compose up -d --no-deps exited 1: no such image"
            )

    fake = NeverCreated([])
    for alone in _factory(monkeypatch, fake):
        with pytest.raises(docker.DockerCommandError) as raised:
            alone.bring_up("nothing was run")
        assert str(raised.value) == "docker compose up -d --no-deps exited 1: no such image"
    assert fake.stopped == [], "a container that was never created was stopped"


def test_a_database_that_could_not_be_stopped_again_says_it_may_still_be_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stop after a failed start failed too: say so, honestly, and name the command."""

    class StopFails(_Docker):
        def stop_containers(self, names: list[str], *, wsl_distro: str | None = None) -> None:
            self.stopped.append(list(names))
            raise docker.DockerCommandError("docker stop exited 1: daemon busy")

    fake = StopFails([], start_fails=True)
    for alone in _factory(monkeypatch, fake):
        fake.running = []
        with pytest.raises(docker.DockerCommandError) as raised:
            alone.bring_up("nothing was run")
        said = str(raised.value)
        db = fake.stopped[-1][0]
        assert said.startswith(f"{db} did not report healthy within 180s, so nothing was run.")
        assert f"{db} may still be running" in said and "daemon busy" in said, said
        assert f"`docker stop {db}`" in said, said


# -- review round 1: nothing starts, stops or recreates the server under a restore ----------


def _press_during_the_load(
    view: ControllerView, mysql: _Mysql, press: Callable[[], object]
) -> list[object]:
    """Make `press` happen while the restore is loading, the way a player's click would."""
    pressed: list[object] = []
    load = mysql.load_from

    def load_and_press(source: IO[bytes]) -> None:
        pressed.append(press())
        load(source)

    mysql.load_from = load_and_press  # type: ignore[method-assign]
    return pressed


@pytest.mark.parametrize("press", ["start", "stop", "restart", "recreate", "stop_other_and_start"])
def test_no_server_press_starts_or_stops_anything_while_a_restore_runs(
    qapp: object,
    tmp_path: Path,
    ps: _Ps,
    monkeypatch: pytest.MonkeyPatch,
    press: str,
) -> None:
    """Every Server and Tuning press that starts, stops or recreates is refused under a restore.

    The press found in review: with the server stopped, Restore starts the
    database alone, so Start is still enabled (not everything runs) and the
    view is not busy. A Start during the load brought the world up on the
    databases being written -- the very hazard the plan refuses for -- and the
    restore's cleanup then stopped the database under the running world.
    Stop, Restart and the recreate are the same hazard from the other side: a
    stop takes the database from under the load. One hold at the docker layer
    refuses all of them; nothing here reaches `docker compose`.
    """
    monkeypatch.setattr(ControllerView, "_confirm", lambda self, title, question: True)
    stack = _Stack()
    mysql = _Mysql(stack)
    view = _view(_real_maintenance(tmp_path, stack, mysql))
    path = _select_backup(view, tmp_path)
    failures = _failures(view)
    presses = {
        "start": view.start_server,
        "stop": view.stop_server,
        "restart": view.restart_server,
        "recreate": view.recreate_containers,
        "stop_other_and_start": view.stop_other_and_start,
    }
    _press_during_the_load(view, mysql, presses[press])

    view.show_restore_plan()
    before = len(ps.calls)
    view.run_restore()

    assert mysql.loaded == [path.read_bytes()], "the restore itself did not finish"
    assert _lifecycle_calls(ps) == [], ps.calls[before:]
    said = " ".join(failures) + view.problem_label.text() + view.tuning_report.toPlainText()
    assert forgetting.RESTORE_HOLDS_THE_SERVER in said, said
    assert stack.starts == 1 and stack.stops == 1 and stack.running == set()


def test_the_server_buttons_work_again_once_the_restore_has_finished(
    qapp: object, tmp_path: Path, ps: _Ps
) -> None:
    """The hold is the restore's and ends with it: the next Start reaches compose."""
    stack = _Stack()
    mysql = _Mysql(stack)
    view = _view(_real_maintenance(tmp_path, stack, mysql))
    _select_backup(view, tmp_path)
    view.show_restore_plan()
    view.run_restore()
    assert len(mysql.loaded) == 1

    view.start_server()
    assert any(c[:3] == ["docker", "compose", "up"] for c in ps.calls), ps.calls


def test_a_backup_that_started_the_database_holds_the_server_too(
    qapp: object, tmp_path: Path, ps: _Ps
) -> None:
    """Backup's cleanup stops the database it started; a Start in between must not happen."""
    stack = _Stack()
    services = _real_maintenance(tmp_path, stack, _Mysql(stack))
    view = _view(services)
    pressed: list[object] = []
    failures = _failures(view)

    def back_up() -> maintenance.BackupReport:
        pressed.append(view.start_server())
        return maintenance.BackupReport(directory=tmp_path, dumps=())

    view.services.backup = back_up
    view.back_up()

    assert pressed, "the backup never ran"
    assert _lifecycle_calls(ps) == [], ps.calls
    assert forgetting.BACKUP_HOLDS_THE_SERVER in " ".join(failures) + view.problem_label.text()
    assert stack.running == set()


def test_a_backup_of_a_database_that_was_already_up_holds_nothing(
    qapp: object, tmp_path: Path, ps: _Ps
) -> None:
    """A hot backup of a running server leaves Start alone: nothing will be stopped after it."""
    stack = _Stack(SPEC.db)
    view = _view(_real_maintenance(tmp_path, stack, _Mysql(stack)))

    def back_up() -> maintenance.BackupReport:
        view.start_server()
        return maintenance.BackupReport(directory=tmp_path, dumps=())

    view.services.backup = back_up
    view.back_up()

    assert any(c[:3] == ["docker", "compose", "up"] for c in ps.calls), ps.calls


def test_a_restore_pressed_while_the_server_is_starting_is_refused(
    qapp: object, tmp_path: Path
) -> None:
    """The other order: a Start's database-healthy wait is no time to restore.

    While compose waits for the database to report healthy the world container
    exists but is not running, so the name census passes. The hold cannot be
    taken while a start is in flight, and the restore says nothing was restored.
    """
    stack = _Stack()
    mysql = _Mysql(stack)
    view = _view(_real_maintenance(tmp_path, stack, mysql))
    _select_backup(view, tmp_path)
    failures = _failures(view)
    view.show_restore_plan()

    with docker._in_flight(tmp_path):
        view.run_restore()

    assert mysql.loaded == [] and stack.starts == 0
    assert failures and failures[-1].startswith("Nothing was restored"), failures
    assert docker.SERVER_IN_MOTION in failures[-1]


def test_a_start_disarms_the_restore_plan(qapp: object, tmp_path: Path) -> None:
    """A plan is a census; a Start changes what it counted, so it is not carried over."""
    stack = _Stack()
    mysql = _Mysql(stack)
    view = _view(_real_maintenance(tmp_path, stack, mysql))
    _select_backup(view, tmp_path)
    view.show_restore_plan()
    assert view.restore_button.isEnabled()

    view.start_server()

    assert not view.restore_button.isEnabled()
    view.run_restore()
    assert mysql.loaded == []
    assert view.maintenance_report.toPlainText() == "Show the restore plan first."


# -- the hold itself, at the docker layer --------------------------------------------------

_LIFECYCLE = {
    "start_staged": lambda d: docker.start_staged(SPEC, d),
    "start": lambda d: docker.start(d),
    "recreate_staged": lambda d: docker.recreate_staged(SPEC, d),
    "stop_servers_staged": lambda d: docker.stop_servers_staged(SPEC, d),
    "stop_staged": lambda d: docker.stop_staged(SPEC, d),
    "remove_staged": lambda d: docker.remove_staged(SPEC, d),
}


@pytest.mark.parametrize("name", sorted(_LIFECYCLE))
def test_a_held_server_refuses_every_lifecycle_command_and_runs_none(
    tmp_path: Path, ps: _Ps, name: str
) -> None:
    """Each door, asked directly: refused in the holder's own words, before docker is run."""
    with docker.hold_the_server(tmp_path, "held for a test"):
        with pytest.raises(docker.ServerHeldError, match="held for a test"):
            _LIFECYCLE[name](tmp_path)
    assert ps.calls == [], ps.calls


def test_a_hold_on_one_server_leaves_another_alone(tmp_path: Path, ps: _Ps) -> None:
    other = tmp_path / "other"
    other.mkdir()
    with docker.hold_the_server(tmp_path / "held", "held for a test"):
        docker.start(other)
    assert ps.calls and ps.calls[-1][:3] == ["docker", "compose", "up"]


def test_a_hold_is_refused_while_a_lifecycle_command_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The start's own `compose up` is where a restore must not begin."""
    refused: list[str] = []

    def compose(cmd: list[str], cwd: Path | None = None, timeout: float | None = None) -> object:
        try:
            with docker.hold_the_server(tmp_path, "a restore"):
                pass
        except docker.ServerHeldError as exc:
            refused.append(str(exc))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(runner, "run", compose)
    docker.start(tmp_path)

    assert refused == [docker.SERVER_IN_MOTION]
    with docker.hold_the_server(tmp_path, "after"):
        pass  # and the start's mark is gone once it returned


def test_a_restore_cannot_begin_between_a_recreate_s_stop_and_its_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recreate is ONE lifecycle command: its servers are down between its two halves.

    A hold taken there would refuse the recreate's own start and leave the
    server down; so the recreate is in flight from its stop to its start.
    """
    refused: list[str] = []

    def start_half(*args: object, **kwargs: object) -> bool:
        try:
            with docker.hold_the_server(tmp_path, "a restore"):
                pass
        except docker.ServerHeldError as exc:
            refused.append(str(exc))
        return True

    monkeypatch.setattr(docker, "stop_servers_staged", lambda *a, **k: None)
    monkeypatch.setattr(docker, "start_staged", start_half)
    docker.recreate_staged(SPEC, tmp_path)

    assert refused == [docker.SERVER_IN_MOTION]


# -- review round 2: a hold and a mark that end when their job fails ------------------------


def test_a_lifecycle_command_that_fails_does_not_keep_the_server_marked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A start whose `compose up` raised is over: a restore may be taken afterwards."""

    def compose_fails(
        cmd: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> object:
        return subprocess.CompletedProcess(cmd, 1, "", "port is already allocated")

    monkeypatch.setattr(runner, "run", compose_fails)
    with pytest.raises(docker.DockerCommandError, match="port is already allocated"):
        docker.start(tmp_path)

    with docker.hold_the_server(tmp_path, "a restore"):
        pass


def test_the_server_buttons_work_again_once_a_failed_restore_has_ended(
    qapp: object, tmp_path: Path, ps: _Ps
) -> None:
    """The hold is released by a restore that fails too: the next Start reaches compose."""
    stack = _Stack()
    mysql = _Mysql(stack, fails_load="ERROR 2013 (HY000): Lost connection")
    view = _view(_real_maintenance(tmp_path, stack, mysql))
    _select_backup(view, tmp_path)
    failures = _failures(view)
    view.show_restore_plan()
    view.run_restore()
    assert failures and "failed part-way" in failures[-1], failures

    view.start_server()
    assert any(c[:3] == ["docker", "compose", "up"] for c in ps.calls), ps.calls


def test_a_backup_pressed_while_the_server_is_starting_says_no_backup_was_taken(
    qapp: object, tmp_path: Path
) -> None:
    """Backup's refusal leads with what was not done, as Restore's does."""
    stack = _Stack()
    view = _view(_real_maintenance(tmp_path, stack, _Mysql(stack)))
    backups: list[int] = []
    view.services.backup = lambda: backups.append(1)  # type: ignore[assignment,func-returns-value]
    failures = _failures(view)

    with docker._in_flight(tmp_path):
        view.back_up()

    assert backups == [] and stack.starts == 0
    assert failures and failures[-1] == f"No backup was taken: {docker.SERVER_IN_MOTION}", failures


# -- review round 3: one Backup or Restore of a server at a time ---------------------------


def _backup_that_presses(
    view: ControllerView, stack: _Stack, press: Callable[[], object]
) -> list[bool]:
    """A backup that presses `press` half way through, and records whether the db was up."""
    ran: list[bool] = []

    def back_up() -> maintenance.BackupReport:
        press()
        ran.append(SPEC.db in stack.running)
        return maintenance.BackupReport(
            directory=maintenance.backups_dir(view.services.controller.server_dir), dumps=()
        )

    view.services.backup = back_up
    return ran


def test_a_restore_pressed_during_a_backup_that_started_the_database_is_refused(
    qapp: object, tmp_path: Path
) -> None:
    """The worse order (review round 3): the backup's cleanup would stop the db under the load.

    The plan was shown with the server stopped, so it is armed. The backup
    starts the database; a Restore pressed now found it up, started nothing,
    and loaded -- and the backup's `finally` then stopped the database under
    the half-loaded restore.
    """
    stack = _Stack()
    mysql = _Mysql(stack)
    view = _view(_real_maintenance(tmp_path, stack, mysql))
    _select_backup(view, tmp_path)
    failures = _failures(view)
    view.show_restore_plan()
    assert view.restore_button.isEnabled()
    ran = _backup_that_presses(view, stack, view.run_restore)

    view.back_up()

    assert mysql.loaded == [], "a restore loaded while a backup held the server"
    assert failures, "the refused restore said nothing"
    assert failures[-1] == f"{forgetting.BACKUP_HOLDS_THE_DATABASES} Nothing was restored."
    assert ran == [True], "the backup did not run to the end"
    assert stack.starts == 1 and stack.stops == 1 and stack.running == set()
    assert "Backed up to" in view.maintenance_report.toPlainText()


def test_a_restore_pressed_during_a_hot_backup_is_refused(qapp: object, tmp_path: Path) -> None:
    """A database already up holds no lifecycle hold -- and still leases the databases."""
    stack = _Stack(SPEC.db)
    mysql = _Mysql(stack)
    view = _view(_real_maintenance(tmp_path, stack, mysql))
    _select_backup(view, tmp_path)
    failures = _failures(view)
    view.show_restore_plan()
    assert view.restore_button.isEnabled()
    ran = _backup_that_presses(view, stack, view.run_restore)

    view.back_up()

    assert mysql.loaded == []
    assert failures and failures[-1] == (
        f"{forgetting.BACKUP_HOLDS_THE_DATABASES} Nothing was restored."
    ), failures
    assert ran == [True] and stack.running == {SPEC.db}


def test_a_backup_pressed_during_a_restore_is_refused_and_dumps_nothing(
    qapp: object, tmp_path: Path
) -> None:
    """Scenario 1: a mysqldump beside the load is a backup of half-old, half-new data."""
    stack = _Stack()
    mysql = _Mysql(stack)
    view = _view(_real_maintenance(tmp_path, stack, mysql))
    path = _select_backup(view, tmp_path)
    failures = _failures(view)
    backups: list[int] = []
    view.services.backup = lambda: backups.append(1)  # type: ignore[assignment,func-returns-value]
    _press_during_the_load(view, mysql, view.back_up)

    view.show_restore_plan()
    view.run_restore()

    assert backups == [], "a backup ran beside the restore's load"
    refused = [f for f in failures if f.endswith("No backup was taken.")]
    assert refused == [f"{forgetting.RESTORE_HOLDS_THE_DATABASES} No backup was taken."], failures
    assert mysql.loaded == [path.read_bytes()], "the restore itself did not finish"
    assert stack.running == set()


def test_the_lease_is_exclusive_and_named_by_its_holder(tmp_path: Path) -> None:
    with docker.maintenance_lease(tmp_path, "the first job"):
        with pytest.raises(docker.MaintenanceLeaseTaken, match="the first job"):
            with docker.maintenance_lease(tmp_path, "the second job"):
                pass
        with docker.maintenance_lease(tmp_path / "another", "another server's job"):
            pass
    with docker.maintenance_lease(tmp_path, "after"):
        pass


def test_the_lease_is_released_by_a_job_that_raises(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        with docker.maintenance_lease(tmp_path, "a job"):
            raise RuntimeError("boom")
    with docker.maintenance_lease(tmp_path, "after"):
        pass


# -- review round 3: a composite is one lifecycle command from its first step to its last --


def _hold_attempt(server_dir: Path, refused: list[str]) -> Callable[[], None]:
    def start() -> None:
        try:
            with docker.hold_the_server(server_dir, "a restore"):
                pass
        except docker.ServerHeldError as exc:
            refused.append(str(exc))
        refused.append("started")

    return start


@pytest.mark.parametrize("composite", ["_do_restart", "_do_recreate"])
def test_a_restore_cannot_begin_between_a_restart_s_two_halves(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, composite: str
) -> None:
    """Restart is stop then start, recreate is remove then start: one command each."""
    stack = _Stack()
    view = _view(_real_maintenance(tmp_path, stack, _Mysql(stack)))
    controller = view.services.controller
    refused: list[str] = []
    monkeypatch.setattr(controller, "stop", lambda: True)
    monkeypatch.setattr(controller, "remove", lambda: True)
    monkeypatch.setattr(controller, "start", _hold_attempt(controller.server_dir, refused))

    assert getattr(view, composite)() is True

    assert refused == [docker.SERVER_IN_MOTION, "started"]


def test_a_restore_cannot_begin_between_the_bot_rebuild_s_stop_and_start(tmp_path: Path) -> None:
    from yulon.controller_wow_tortoise import botpool

    refused: list[str] = []

    class Lifecycle:
        server_dir = tmp_path

        def stop(self) -> bool:
            return True

        start = staticmethod(_hold_attempt(tmp_path, refused))

    botpool.restart_world(Lifecycle())  # type: ignore[arg-type]

    assert refused == [docker.SERVER_IN_MOTION, "started"]


def test_a_held_server_refuses_a_bot_restart_as_a_failed_stop(tmp_path: Path) -> None:
    """Refused before its stop: `StopFailed`, which its caller reads as "nothing was started"."""
    from yulon.controller_wow_tortoise import botpool

    class Lifecycle:
        server_dir = tmp_path

        def stop(self) -> bool:
            raise AssertionError("stopped a held server")

        def start(self) -> None:
            raise AssertionError("started a held server")

    with docker.hold_the_server(tmp_path, "a restore"):
        with pytest.raises(botpool.StopFailed, match="a restore"):
            botpool.restart_world(Lifecycle())  # type: ignore[arg-type]
