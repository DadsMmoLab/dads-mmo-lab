"""Three seams where a database record was lost or not asked (T712, T714, T715).

- T712: a failed Rebuild's (or update's) copy goes back through `maintenance.restore()`, which
  deleted the unfinished-import record; the half-imported databases came back with no record.
- T714: "Stop the other server and start this one" asked only this Yu'lon's in-process holds,
  so it stopped a database a Quit-anyway restore was still loading.
- T715: an uninstall asked nothing about a restore record, and T677 moves that record into the
  kept backups where nothing reads it; a reinstall that kept the characters started on
  half-restored databases.

Each runs the real code: the real `_MaintenanceSnapshot` over a fake database container, the
real `Controller.stop_conflicting()`, the real `purge.Uninstaller` with its recorded seams.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.test_controller_reservation import _Recorded, fake_docker  # noqa: F401 - a fixture
from tests.test_install_wiring import WOTLK, _real_put_back, _TableServer
from tests.test_purge import _recorder
from tests.test_restore_load_pid import _marker_with
from yulon import docker, purge
from yulon.catalog import native
from yulon.catalog.installer import InstallerError
from yulon.controller_wow_wotlk import maintenance


@pytest.fixture
def live_child() -> Iterator[subprocess.Popen[bytes]]:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield child
    child.kill()
    child.wait()


def _dead_pid() -> int:
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    return gone.pid


# ---------------------------------------------------------------- T712


def _import_record(server_dir: Path) -> Path:
    service = WOTLK.container_spec().import_service
    assert service, "the WotLK spec names its import service"
    return docker.one_shot_ended_marker(server_dir, service)


def test_a_copy_put_back_over_an_unfinished_import_keeps_its_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The T712 case: the copy was taken of half-imported databases, so the record that said
    so still describes them once it is back. Mutation: put back without the record."""
    server = _TableServer({"acore_world": {"creature"}})
    copy = _real_put_back(monkeypatch, server)
    record = _import_record(tmp_path)
    record.write_text(docker.IMPORT_UNDER_WAY + "\n", encoding="utf-8")
    taken = copy.take(tmp_path, ("acore_world",))

    copy.put_back(tmp_path, taken)

    assert record.is_file(), "the put-back erased the unfinished-import record"
    assert docker.IMPORT_UNDER_WAY in record.read_text(encoding="utf-8")
    assert docker.import_record_is_from_empty(tmp_path, WOTLK.container_spec().import_service)


def test_a_record_the_failed_run_removed_comes_back_with_the_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The run verified an import (record removed) and then failed: the copy it puts back is
    the half-imported one, and so is the record."""
    server = _TableServer({"acore_world": {"creature"}})
    copy = _real_put_back(monkeypatch, server)
    record = _import_record(tmp_path)
    record.write_text(docker.IMPORT_UNDER_WAY + "\n", encoding="utf-8")
    taken = copy.take(tmp_path, ("acore_world",))
    record.unlink()  # a verified import in the run removed it

    copy.put_back(tmp_path, taken)

    assert record.is_file() and docker.IMPORT_UNDER_WAY in record.read_text(encoding="utf-8")


def test_a_record_the_failed_run_wrote_goes_with_the_copy_put_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No record when the copy was taken: the copy is of finished databases, and a record the
    run wrote (an importer it ended) describes what the put-back replaced. Left standing,
    Repair would read the player's rows as the import's and clear them."""
    server = _TableServer({"acore_world": {"creature"}})
    copy = _real_put_back(monkeypatch, server)
    record = _import_record(tmp_path)
    taken = copy.take(tmp_path, ("acore_world",))
    record.write_text("was ended before it finished\n", encoding="utf-8")

    copy.put_back(tmp_path, taken)

    assert not record.exists()


def test_a_put_back_that_fails_part_way_keeps_the_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two databases: the first goes back, the second fails. The databases are in no known
    state, and the record must still stand. Mutation: drop `keeps_the_import_record=True`
    from the put-back's `restore()` (the first file's restore removed it)."""

    class _FailsSecond(_TableServer):
        loads = 0

        def load_from(self, source: Any) -> None:
            self.loads += 1
            if self.loads == 2:
                raise maintenance.MaintenanceError("the database went away")
            super().load_from(source)

    server = _FailsSecond({"acore_world": {"creature"}, "acore_characters": {"characters"}})
    copy = _real_put_back(monkeypatch, server)
    record = _import_record(tmp_path)
    record.write_text(docker.IMPORT_UNDER_WAY + "\n", encoding="utf-8")
    taken = copy.take(tmp_path, ("acore_world", "acore_characters"))
    assert len(taken.files) == 2

    with pytest.raises(InstallerError):
        copy.put_back(tmp_path, taken)

    assert record.is_file(), "a part-way put-back erased the unfinished-import record"


def test_the_maintenance_tabs_own_restore_still_removes_the_record(
    tmp_path: Path,
) -> None:
    """T678 round 3 stands for a player's restore: it replaces the databases with a backup's."""
    from tests.test_maintenance import DB, FakeMysql, a_backup_of, plan_restore, restore, running

    record = _import_record(tmp_path)
    record.write_text(docker.IMPORT_UNDER_WAY + "\n", encoding="utf-8")
    plan = plan_restore(a_backup_of(tmp_path, "acore_world"), tmp_path, running=running(DB))
    restore(plan, FakeMysql(("acore_world",)), confirm=plan.token, running=running(DB))
    assert not record.exists()


# ---------------------------------------------------------------- T714


def _other_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, list[list[str]]]:
    ours, other = tmp_path / "ours", tmp_path / "other"
    ours.mkdir()
    other.mkdir()
    (other / native.STATE_FILE).write_text("{}", encoding="utf-8")  # a Yu'lon server
    stopped: list[list[str]] = []
    monkeypatch.setattr(docker, "stop_containers", lambda names, **_kw: stopped.append(names))
    monkeypatch.setattr(docker, "container_project", lambda *_a, **_kw: "other")
    monkeypatch.setattr(docker, "project_containers", lambda *_a, **_kw: ["o-db"])
    monkeypatch.setattr(docker, "container_working_dir", lambda *_a, **_kw: str(other))
    return ours, other, stopped


def test_the_other_servers_database_is_not_stopped_under_a_restore_still_loading(
    fake_docker: Path,  # noqa: F811 - the fixture
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    live_child: subprocess.Popen[bytes],
) -> None:
    """T714: Quit anyway left the other server's restore loading; nothing of this process holds
    it. Mutation: drop the restore-record ask from `stop_conflicting()`."""
    ours, other, stopped = _other_server(tmp_path, monkeypatch)
    _marker_with(other, live_child.pid)
    controller = _Recorded(ours)
    controller.port_conflicts = lambda: ["o-db"]  # type: ignore[method-assign]

    with pytest.raises(docker.ServerHeldError) as refused:
        controller.stop_conflicting()

    assert stopped == [], "the other server's database was stopped under its restore"
    said = str(refused.value)
    assert "restore" in said and "still" in said and "half-written" in said, said


def test_the_other_server_with_a_dead_restore_record_is_stopped_as_before(
    fake_docker: Path,  # noqa: F811 - the fixture
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A record whose load is gone: stopping that database writes nothing into it, and the
    record still refuses that server's own Start."""
    ours, other, stopped = _other_server(tmp_path, monkeypatch)
    _marker_with(other, _dead_pid())
    controller = _Recorded(ours)
    controller.port_conflicts = lambda: ["o-db"]  # type: ignore[method-assign]

    assert controller.stop_conflicting() == ["o-db"]
    assert stopped == [["o-db"]]


# ---------------------------------------------------------------- T715


@pytest.mark.parametrize("load", ["running", "unfinished"])
def test_an_uninstall_is_refused_while_a_restore_is_running_or_unfinished(
    tmp_path: Path, load: str, live_child: subprocess.Popen[bytes]
) -> None:
    """T715: refused before ownership or Docker is asked, by `plan()` and by `run()` alike
    (`run()` does not pass through `plan()`). Mutation: drop the call from `_resolve()`."""
    rec = _recorder(tmp_path)
    _marker_with(rec.server_dir, live_child.pid if load == "running" else _dead_pid())

    plan = rec.uninstaller().plan()
    assert plan.refusal and "restore" in plan.refusal, plan
    with pytest.raises(purge.PurgeRefusal) as refused:
        rec.uninstaller().run(keep_characters=True)

    said = str(refused.value)
    assert "Nothing was removed" in said, said
    assert ("still" in said) == (load == "running"), said
    assert "Forget that record" in said or load == "running", said
    assert rec.order == [], f"the uninstall reached {rec.order} over a restore record"
    assert maintenance.marker_path(rec.server_dir).is_file(), "the record was carried away"


def test_an_uninstall_with_no_restore_record_goes_ahead(tmp_path: Path) -> None:
    rec = _recorder(tmp_path)
    rec.uninstaller().run(keep_characters=False)
    assert "forget" in rec.order


def test_an_unreadable_restore_record_refuses_too(tmp_path: Path) -> None:
    rec = _recorder(tmp_path)
    marker = maintenance.marker_path(rec.server_dir)
    marker.parent.mkdir(parents=True)
    marker.write_text("{not json", encoding="utf-8")
    with pytest.raises(purge.PurgeRefusal):
        rec.uninstaller().run(keep_characters=True)
    assert rec.order == []
