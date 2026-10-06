"""Start does not run a server on a database Docker no longer has.

Seen live on yulon-win11, 2026-10-05: an older WotLK folder whose database volume
had been pruned. Rebuild and Start both brought the server up on a new, empty
database (`Unknown database acore_auth`, the login server restarting), and the
import had to be run by hand. Start now asks two questions first, both of
Docker and neither of a guess:

* does the volume that holds the database's files exist at all;
* if it does, does the login database have its `account` table, asked of a
  database that has said it is healthy -- a database that is slow to start is
  not an empty one, so "could not tell" starts the server as before.

On "missing" or "empty" nothing is started, the player reads one plain sentence,
and the Server tab offers Repair (the install's own import) and, when there are
backups, the Maintenance tab. Nothing imports by itself: the player may have
meant to restore a backup.

The fake here is Docker's own CLI (`runner.run`) and its stdin channel
(`docker.exec_stdin`), so every check runs through the real code that asks.
"""

from __future__ import annotations

import io
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import wait_for_panel
from tests.support_player_text import command_faults, text_faults
from tests.test_controller_view import WOTLK, _Ps, _services
from yulon import database_presence, docker, runner
from yulon.catalog.catalog import load_catalog
from yulon.controller import Controller, DatabaseMissing
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import ControllerView
from yulon.ui.widgets.job import run_inline

pytestmark = pytest.mark.usefixtures("real_database_read")

TBC = load_catalog().get("wow-tbc")
VOLUME = "t-project_db-data"
SPEC = WOTLK.container_spec()


class _DbDocker(_Ps):
    """`_Ps`, plus the database's volume, its health and its tables.

    `volumes` is what `docker volume inspect` finds; `tables` is what the
    database answers for `information_schema` once it is running. A `compose
    up` that starts the database creates its volume, empty, as compose does.
    """

    def __init__(self, entry: Any = WOTLK) -> None:
        super().__init__()
        self.entry = entry
        self.spec = entry.container_spec()
        self.volumes: set[str] = {VOLUME}
        self.tables: dict[str, set[str]] = {entry.schema_map()["auth"]: {"account", "realmlist"}}
        self.healthy = True
        self.query_fails = False
        self.queries: list[str] = []
        self.mount = "/var/lib/mysql"

    def compose_json(self) -> str:
        db, auth, world = self.spec.compose_services()
        return json.dumps(
            {
                "name": self.project,
                "services": {
                    db: {
                        "image": "mysql:8.4",
                        "volumes": [
                            {"type": "volume", "source": "db-data", "target": self.mount},
                            {"type": "bind", "source": "/srv/x", "target": "/backups"},
                        ],
                    },
                    auth: {"image": "auth"},
                    world: {
                        "image": "world",
                        "volumes": [{"type": "volume", "source": "client-data", "target": "/data"}],
                    },
                },
                "volumes": {
                    "db-data": {"name": VOLUME},
                    "client-data": {"name": "t-project_client-data"},
                },
            }
        )

    def __call__(
        self, cmd: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        if cmd[:4] == ["docker", "compose", "config", "--format"]:
            self.calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, self.compose_json(), "")
        if cmd[:3] == ["docker", "volume", "inspect"]:
            self.calls.append(cmd)
            name = cmd[-1]
            if name in self.volumes:
                return subprocess.CompletedProcess(cmd, 0, name + "\n", "")
            return subprocess.CompletedProcess(
                cmd, 1, "", f"Error response from daemon: get {name}: no such volume\n"
            )
        if cmd[:2] == ["docker", "inspect"] and "{{.State.Health.Status}}" in cmd:
            self.calls.append(cmd)
            up = cmd[2] in self.names.split() and self.healthy
            return subprocess.CompletedProcess(cmd, 0, "healthy\n" if up else "starting\n", "")
        if cmd[:2] == ["docker", "stop"]:
            self.calls.append(cmd)
            gone = set(cmd[2:])
            self.names = "".join(f"{n}\n" for n in self.names.split() if n not in gone)
            return subprocess.CompletedProcess(cmd, 0, "\n".join(cmd[2:]), "")
        if cmd[:5] == ["docker", "compose", "up", "-d", "--no-deps"]:
            # Compose makes a missing named volume, empty.
            if self.spec.compose_services()[0] in cmd[5:] and VOLUME not in self.volumes:
                self.volumes.add(VOLUME)
                self.tables = {}
            result = super().__call__(cmd, cwd, timeout)
            # `start_database()` asks for the database alone; what was up stays up.
            return result
        return super().__call__(cmd, cwd, timeout)

    def exec_stdin(
        self,
        container: str,
        argv: Any,
        source: io.BytesIO,
        *,
        env: Any,
        wsl_distro: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        statement = source.read().decode("utf-8")
        self.queries.append(statement)
        if self.query_fails or container not in self.names.split():
            return subprocess.CompletedProcess(argv, 1, "", "ERROR 2002: cannot connect")
        auth = self.entry.schema_map()["auth"]
        assert f"'{auth}'" in statement and "'account'" in statement, statement
        found = 1 if "account" in self.tables.get(auth, set()) else 0
        return subprocess.CompletedProcess(argv, 0, f"{found}\n", "")


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> _DbDocker:
    fake = _DbDocker()
    monkeypatch.setattr(runner, "run", fake)
    monkeypatch.setattr(docker, "exec_stdin", fake.exec_stdin)
    # A slow database is waited for; the test's clock is short, never the code's rule.
    monkeypatch.setattr(database_presence, "DB_HEALTHY_TIMEOUT", 0.3)
    monkeypatch.setattr(docker, "_POLL_INTERVAL_SECONDS", 0.05)
    return fake


@pytest.fixture(autouse=True)
def _inline_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)


def _started_servers(fake: _DbDocker) -> list[list[str]]:
    """Every `compose up` that names the login or the world server."""
    return [
        c
        for c in fake.calls
        if c[:3] == ["docker", "compose", "up"] and (SPEC.auth in c or SPEC.world in c)
    ]


# -- what Docker is asked ------------------------------------------------------------------


def test_the_database_volume_is_the_one_compose_mounts_at_the_database_files(
    db: _DbDocker, tmp_path: Path
) -> None:
    """Named by compose, never built from a folder name: the client-data volume is not it."""
    assert docker.database_volume(SPEC, tmp_path) == VOLUME


def test_no_volume_is_named_when_the_database_files_are_not_in_a_volume(
    db: _DbDocker, tmp_path: Path
) -> None:
    db.mount = "/elsewhere"
    assert docker.database_volume(SPEC, tmp_path) is None


def test_a_missing_volume_reads_missing_and_starts_nothing(db: _DbDocker, tmp_path: Path) -> None:
    db.volumes.clear()
    reading = database_presence.take_reading(WOTLK, tmp_path)
    assert reading.presence == "missing"
    assert not [c for c in db.calls if c[:3] == ["docker", "compose", "up"]]


def test_a_database_with_no_account_table_reads_empty_and_is_put_back_down(
    db: _DbDocker, tmp_path: Path
) -> None:
    db.tables = {}
    reading = database_presence.take_reading(WOTLK, tmp_path)
    assert reading.presence == "empty"
    stops = [c for c in db.calls if c[:2] == ["docker", "stop"]]
    assert [c[-1] for c in stops] == [SPEC.db], "the database it started is stopped again"
    assert SPEC.db not in db.names.split()


def test_a_database_that_was_already_up_is_left_up(db: _DbDocker, tmp_path: Path) -> None:
    db.tables = {}
    db.names = f"{SPEC.db}\n"
    assert database_presence.take_reading(WOTLK, tmp_path).presence == "empty"
    assert not [c for c in db.calls if c[:2] == ["docker", "stop"]]


def test_a_database_with_its_account_table_reads_present(db: _DbDocker, tmp_path: Path) -> None:
    assert database_presence.take_reading(WOTLK, tmp_path).presence == "present"


def test_a_slow_database_is_never_called_empty(db: _DbDocker, tmp_path: Path) -> None:
    """Not healthy in time: nothing is concluded, and no query is asked of it."""
    db.tables = {}
    db.healthy = False
    reading = database_presence.take_reading(WOTLK, tmp_path)
    assert reading.presence == "unknown"
    assert db.queries == []


def test_a_query_that_fails_is_not_an_empty_database(db: _DbDocker, tmp_path: Path) -> None:
    db.query_fails = True
    assert database_presence.take_reading(WOTLK, tmp_path).presence == "unknown"


def test_a_cmangos_server_is_asked_about_its_own_login_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """realmd, not acore_auth; the generated password is read from the folder."""
    fake = _DbDocker(TBC)
    monkeypatch.setattr(runner, "run", fake)
    monkeypatch.setattr(docker, "exec_stdin", fake.exec_stdin)
    plan = TBC.install.password
    assert plan.file is not None
    (tmp_path / plan.file).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / plan.file).write_text("tbc0123456789abcd\n", encoding="utf-8")
    assert database_presence.take_reading(TBC, tmp_path).presence == "present"
    assert any("'realmd'" in q for q in fake.queries)
    fake.tables = {}
    assert database_presence.take_reading(TBC, tmp_path).presence == "empty"


# -- the real Start ------------------------------------------------------------------------


def test_start_refuses_a_missing_database_and_starts_no_server(
    db: _DbDocker, tmp_path: Path
) -> None:
    db.volumes.clear()
    with pytest.raises(DatabaseMissing) as refused:
        Controller(SPEC, tmp_path).start()
    assert str(refused.value) == database_presence.MISSING
    assert _started_servers(db) == []
    assert VOLUME not in db.volumes, "no empty volume was made in its place"


def test_start_refuses_an_empty_database_and_leaves_nothing_running(
    db: _DbDocker, tmp_path: Path
) -> None:
    db.tables = {}
    with pytest.raises(DatabaseMissing):
        Controller(SPEC, tmp_path).start()
    assert _started_servers(db) == []
    assert db.names.split() == []


def test_start_starts_a_server_whose_database_is_there(db: _DbDocker, tmp_path: Path) -> None:
    Controller(SPEC, tmp_path).start()
    assert len(_started_servers(db)) == 1


def test_start_starts_as_before_when_the_database_could_not_be_asked(
    db: _DbDocker, tmp_path: Path
) -> None:
    db.healthy = False
    db.tables = {}
    Controller(SPEC, tmp_path).start()
    assert len(_started_servers(db)) == 1


def test_the_sentence_is_plain_words() -> None:
    said = database_presence.MISSING
    assert said == (
        "The server's database is missing (Docker's copy was removed). Repair rebuilds it from "
        "the server files; your characters cannot come back unless you have a backup."
    )
    assert text_faults(said) == [] and command_faults(said) == []


def test_an_empty_database_is_not_said_to_have_been_removed(db: _DbDocker, tmp_path: Path) -> None:
    """T421: the volume is there, so "Docker's copy was removed" is false for it."""
    db.tables = {}
    with pytest.raises(DatabaseMissing) as refused:
        Controller(SPEC, tmp_path).start()
    assert str(refused.value) == database_presence.EMPTY
    assert "removed" not in database_presence.EMPTY
    assert database_presence.EMPTY != database_presence.MISSING
    assert text_faults(database_presence.EMPTY) == []
    assert command_faults(database_presence.EMPTY) == []


# -- the Server tab ------------------------------------------------------------------------


def _view(db: _DbDocker, tmp_path: Path, presses: list[str]) -> ControllerView:
    services = _services(db, tmp_path, [])

    def repair(cancel: object = None) -> Iterator[str]:
        presses.append("repair")
        yield "Repairing"

    services.repair_database = repair
    return ControllerView(WOTLK, services, status_poll_ms=0)


def test_the_start_button_says_the_database_is_missing_and_offers_repair(
    qapp: object, db: _DbDocker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.volumes.clear()
    presses: list[str] = []
    view = _view(db, tmp_path, presses)
    view.start_server()
    assert view.problem_label.text().startswith(database_presence.MISSING)
    assert view.repair_database_button.isVisibleTo(view)
    assert not view.restore_backup_button.isVisibleTo(view), "no backups, no offer"
    assert _started_servers(db) == []
    monkeypatch.setattr(controller_view_module, "ask_yes_no", lambda *a, **k: True)
    view.repair_database_button.click()
    wait_for_panel(view.rebuild_log)
    assert presses == ["repair"], "Repair runs only when pressed"


def test_nothing_is_imported_until_repair_is_pressed(
    qapp: object, db: _DbDocker, tmp_path: Path
) -> None:
    db.volumes.clear()
    presses: list[str] = []
    view = _view(db, tmp_path, presses)
    view.start_server()
    assert presses == []


def test_repair_declined_runs_nothing(
    qapp: object, db: _DbDocker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.volumes.clear()
    presses: list[str] = []
    view = _view(db, tmp_path, presses)
    view.start_server()
    monkeypatch.setattr(controller_view_module, "ask_yes_no", lambda *a, **k: False)
    view.repair_database_button.click()
    assert presses == []


def test_with_backups_the_tab_also_offers_them(qapp: object, db: _DbDocker, tmp_path: Path) -> None:
    backups = tmp_path / "sql_scripts" / "backups"
    backups.mkdir(parents=True)
    (backups / "2026-10-05_acore_characters.sql").write_text("-- dump\n", encoding="utf-8")
    db.volumes.clear()
    view = _view(db, tmp_path, [])
    view.start_server()
    assert view.restore_backup_button.isVisibleTo(view)
    view.restore_backup_button.click()
    current = view._tabs.currentWidget()
    assert current is not None and current.isAncestorOf(view.backup_list)


def test_a_start_that_works_takes_the_offer_down(
    qapp: object, db: _DbDocker, tmp_path: Path
) -> None:
    db.volumes.clear()
    view = _view(db, tmp_path, [])
    view.start_server()
    db.volumes.add(VOLUME)
    view.start_server()
    assert not view.repair_database_button.isVisibleTo(view)
    assert not view.restore_backup_button.isVisibleTo(view)


def test_a_rebuild_refused_for_the_database_puts_the_same_offer_on_the_server_tab(
    qapp: object, db: _DbDocker, tmp_path: Path
) -> None:
    """The Rebuild's refusal is in its panel; its ways out go where a refused Start puts them."""
    view = _view(db, tmp_path, [])
    view._rebuild_finished(False, f"{database_presence.MISSING} Nothing was changed.")
    assert view.problem_label.text() == database_presence.MISSING
    assert view.repair_database_button.isVisibleTo(view)


# -- the presses that stop before they start (cold review) ---------------------------------


def _all_up(db: _DbDocker) -> None:
    db.names = "".join(f"{name}\n" for name in (SPEC.db, SPEC.auth, SPEC.world))


def _stops(db: _DbDocker) -> list[list[str]]:
    return [
        c
        for c in db.calls
        if c[:2] == ["docker", "stop"]
        or c[:3] in (["docker", "compose", "stop"], ["docker", "compose", "rm"])
        or c[:3] == ["docker", "compose", "down"]
    ]


@pytest.mark.parametrize("press", ["_do_restart", "_do_recreate"])
def test_restart_and_recreate_refuse_an_empty_database_before_they_stop_anything(
    qapp: object, db: _DbDocker, tmp_path: Path, press: str
) -> None:
    """A world running on the empty database compose made is the case T377 was filed for."""
    _all_up(db)
    db.tables = {}
    view = _view(db, tmp_path, [])
    with pytest.raises(DatabaseMissing):
        getattr(view, press)()
    assert _stops(db) == [], "refused after the stop"
    assert sorted(db.names.split()) == sorted((SPEC.db, SPEC.auth, SPEC.world))


def test_a_refused_restart_offers_repair_on_the_server_tab(
    qapp: object, db: _DbDocker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _all_up(db)
    db.tables = {}
    view = _view(db, tmp_path, [])
    monkeypatch.setattr(view, "_confirm", lambda *a, **k: True)
    view.restart_server()
    assert view.problem_label.text() == database_presence.EMPTY
    assert view.repair_database_button.isVisibleTo(view)


def test_the_bots_restart_refuses_before_its_stop(db: _DbDocker, tmp_path: Path) -> None:
    from yulon.controller_wow_tortoise import botpool

    _all_up(db)
    db.tables = {}
    with pytest.raises(DatabaseMissing):
        botpool.restart_world(Controller(SPEC, tmp_path))
    assert _stops(db) == []


def test_stopping_the_other_server_waits_for_this_one_s_database(
    db: _DbDocker, tmp_path: Path
) -> None:
    db.volumes.clear()
    db.ports = "tbc-realmd\t0.0.0.0:3724->3724/tcp\n"
    with pytest.raises(DatabaseMissing):
        Controller(SPEC, tmp_path).stop_conflicting_and_start()
    assert _stops(db) == []


def test_a_restart_that_works_takes_the_offer_down(
    qapp: object, db: _DbDocker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.volumes.clear()
    view = _view(db, tmp_path, [])
    view.start_server()
    db.volumes.add(VOLUME)
    monkeypatch.setattr(view, "_confirm", lambda *a, **k: True)
    view.restart_server()
    assert not view.repair_database_button.isVisibleTo(view)


def test_a_world_in_a_restart_loop_does_not_take_the_offer_down(
    qapp: object, db: _DbDocker, tmp_path: Path
) -> None:
    """On an empty database the world restarts over and over, and `docker ps` lists it."""
    view = _view(db, tmp_path, [])
    view._rebuild_finished(False, f"{database_presence.MISSING} Nothing was changed.")
    _all_up(db)
    view.refresh_status()
    assert view.repair_database_button.isVisibleTo(view)


def test_a_tortoise_start_refused_for_its_database_starts_no_bot_dashboard(
    db: _DbDocker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon.controller_wow_tortoise import botdash
    from yulon.controller_wow_tortoise.controller import TortoiseController

    tortoise = load_catalog().get("wow-tortoise")
    fake = _DbDocker(tortoise)
    fake.volumes.clear()
    monkeypatch.setattr(runner, "run", fake)
    asked: list[str] = []
    monkeypatch.setattr(botdash, "start_if_on", lambda *a, **_k: asked.append("dashboard"))
    plan = tortoise.install.password
    assert plan.file is not None
    (tmp_path / plan.file).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / plan.file).write_text("tw0123456789abcd\n", encoding="utf-8")
    with pytest.raises(DatabaseMissing):
        TortoiseController(tmp_path).start()
    assert asked == []


def test_after_a_stop_the_database_sentence_stays_beside_the_offer(
    qapp: object, db: _DbDocker, tmp_path: Path
) -> None:
    """T422: Repair and Restore stay shown after a Stop, so the sentence that explains them does."""
    db.volumes.clear()
    view = _view(db, tmp_path, [])
    view.start_server()
    assert view.repair_database_button.isVisibleTo(view)
    view._stop_done(True)
    assert view.repair_database_button.isVisibleTo(view)
    assert database_presence.MISSING in view.problem_label.text()
    assert view.problem_label.text().count(database_presence.MISSING) == 1


def test_after_a_stop_the_empty_sentence_is_the_one_kept(
    qapp: object, db: _DbDocker, tmp_path: Path
) -> None:
    db.tables = {}
    view = _view(db, tmp_path, [])
    view.start_server()
    view._stop_done(True)
    assert database_presence.EMPTY in view.problem_label.text()


def test_a_stop_with_no_offer_shown_says_no_database_sentence(
    qapp: object, db: _DbDocker, tmp_path: Path
) -> None:
    view = _view(db, tmp_path, [])
    view._stop_done(True)
    assert "database is" not in view.problem_label.text()


def test_pressing_repair_takes_the_kept_sentence_down(
    qapp: object, db: _DbDocker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.volumes.clear()
    view = _view(db, tmp_path, [])
    view.start_server()
    monkeypatch.setattr(controller_view_module, "ask_yes_no", lambda *a, **k: True)
    view.repair_database_button.click()
    wait_for_panel(view.rebuild_log)
    view._stop_done(True)
    assert database_presence.MISSING not in view.problem_label.text()
