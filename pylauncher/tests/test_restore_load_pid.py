"""A restore whose Yu'lon has quit may still be loading: the next start must know (T690).

"Quit anyway" ends Yu'lon with `os._exit`, and the restore load is a plain
`docker exec -i ... mysql` child that nothing kills: it carries on writing the
databases. A relaunch that reported an "interrupted restore" and offered the
safety copy would put a second writer on the same databases. So the load's pid goes
into the restore marker, and the next start says the restore is still running, and
offers neither the interrupted-restore action nor a second restore.

The child here is a real process (a stub standing in for the docker client), never a
fake pid.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

import pytest

from tests.test_maintenance import (
    AT,
    DB,
    FakeMysql,
    a_backup_of,
    plan_restore,
    restore,
    running,
)
from yulon.controller_wow_wotlk import maintenance
from yulon.controller_wow_wotlk.maintenance import (
    DockerMysql,
    MaintenanceError,
    forget_interrupted_restore,
    interrupted_restore,
)

# Stands in for the `docker` client: reads the dump from stdin, says who it is, and keeps
# "loading" until it is told to stop.
STUB = """
import os, sys, time
sys.stdin.buffer.read()
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "load.pid"), "w") as f:
    f.write(str(os.getpid()))
while not os.path.exists(os.path.join(here, "release")):
    time.sleep(0.05)
"""


@dataclass(frozen=True)
class _LoadingMysql(DockerMysql):
    """The real `load_from` (a child process), everything else answered from memory."""

    def databases(self) -> tuple[str, ...]:
        return ("acore_world",)

    def dump_into(self, database: str, sink: IO[bytes]) -> None:
        FakeMysql(("acore_world",)).dump_into(database, sink)


@pytest.fixture
def stub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "stub"
    folder.mkdir()
    script = folder / "docker_stub.py"
    script.write_text(STUB, encoding="utf-8")
    monkeypatch.setattr(
        maintenance.platform, "docker_prefix", lambda _distro: [sys.executable, str(script)]
    )
    monkeypatch.setattr(maintenance, "mysql_client", lambda *a, **k: "mysql")
    monkeypatch.setattr(maintenance, "mysql_env", lambda *a, **k: {})
    return folder


def _wait_for(path: Path, seconds: float = 20.0) -> None:
    end = time.monotonic() + seconds
    while not path.exists():
        assert time.monotonic() < end, f"{path.name} never appeared"
        time.sleep(0.05)


def test_the_running_load_is_named_in_the_marker_while_it_runs(tmp_path: Path, stub: Path) -> None:
    """Mutation: drop the pid hook from `DockerMysql._exec()`: `load_pid` stays 0."""
    server = tmp_path / "server"
    server.mkdir()
    mysql = _LoadingMysql(DB, "pw")
    plan = plan_restore(a_backup_of(tmp_path, "acore_world"), server, running=running(DB))
    outcome: list[Any] = []

    def run() -> None:
        try:
            outcome.append(restore(plan, mysql, confirm=plan.token, running=running(DB), now=AT))
        except Exception as exc:  # noqa: BLE001 - reported by the test thread
            outcome.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        _wait_for(stub / "load.pid")
        child = int((stub / "load.pid").read_text())
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            marker = interrupted_restore(server)
            if marker is not None and marker.load_pid:
                break
            time.sleep(0.05)
        assert marker is not None and marker.load_pid == child, marker
        assert marker.load_running, "a live load must read as running"
        assert plan_restore(
            plan.backup, server, running=running(DB)
        ).refusals, "a second restore must be refused while the first one writes"
        with pytest.raises(MaintenanceError, match="still running"):
            forget_interrupted_restore(server)
    finally:
        (stub / "release").write_text("go")
        worker.join(30)
    assert not isinstance(outcome[0], Exception), outcome
    assert interrupted_restore(server) is None, "a finished restore leaves no marker"


def _marker_with(server: Path, pid: int, *, host: str | None = None) -> None:
    folder = maintenance.backups_dir(server)
    folder.mkdir(parents=True, exist_ok=True)
    record = maintenance.InterruptedRestore(
        marker=maintenance.marker_path(server),
        backup=server / "b.sql",
        databases=("acore_world",),
        safety_backup=(),
        started_at="2026-10-11T04:00:00",
        load_pid=pid,
        load_host=host if host is not None else maintenance.this_host(),
    )
    record.marker.write_text(record.as_json(), encoding="utf-8")


@pytest.fixture
def live_child() -> Any:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield child
    child.kill()
    child.wait()


def test_a_load_that_outlived_yulon_reads_as_still_running(tmp_path: Path, live_child: Any) -> None:
    _marker_with(tmp_path, live_child.pid)
    marker = interrupted_restore(tmp_path)
    assert marker is not None and marker.load_running
    plan = plan_restore(a_backup_of(tmp_path, "acore_world"), tmp_path, running=running(DB))
    assert any("still running" in refusal for refusal in plan.refusals), plan.refusals


def test_a_load_that_has_ended_is_an_interrupted_restore_as_before(tmp_path: Path) -> None:
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    _marker_with(tmp_path, gone.pid)
    marker = interrupted_restore(tmp_path)
    assert marker is not None and not marker.load_running
    plan = plan_restore(a_backup_of(tmp_path, "acore_world"), tmp_path, running=running(DB))
    assert not any("still running" in refusal for refusal in plan.refusals)
    assert forget_interrupted_restore(tmp_path) is True


def test_a_pid_from_another_computer_or_a_very_old_marker_is_not_trusted(
    tmp_path: Path, live_child: Any
) -> None:
    """A trap otherwise: a shared folder, or a pid the system gave to something else since."""
    _marker_with(tmp_path, live_child.pid, host="some-other-computer")
    marker = interrupted_restore(tmp_path)
    assert marker is not None and not marker.load_running
    _marker_with(tmp_path, live_child.pid)
    old = time.time() - 3 * 24 * 3600
    os.utime(maintenance.marker_path(tmp_path), (old, old))
    marker = interrupted_restore(tmp_path)
    assert marker is not None and not marker.load_running


def test_the_tab_says_wait_and_offers_nothing_while_the_load_runs(
    qapp: object, tmp_path: Path, live_child: Any
) -> None:
    """Mutation: drop the `load_running` branch from `_show_interrupted()`."""
    from tests.test_teardown_guard import _view

    view, _fake, _ = _view(tmp_path)
    _marker_with(tmp_path, live_child.pid)
    record = interrupted_restore(tmp_path)
    view.services.interrupted_restore = lambda: record  # type: ignore[method-assign,assignment]

    view._show_interrupted()
    view.shutdown()

    assert not view.interrupted_label.isHidden()
    text = view.interrupted_label.text()
    assert "still running" in text and "wait" in text.lower(), text
    assert "Restoring again" not in text, "the escape was offered over a live load"
    assert view.forget_button.isHidden(), "Forget was offered over a live load"
