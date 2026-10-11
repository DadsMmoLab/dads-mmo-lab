"""T730: a restore load another Yu'lon left running holds the server for every press.

"Quit anyway" (T690) leaves the load running in Docker; only the restore record knows
(`InterruptedRestore.load_running`). The one cross-process hold check (`docker.held_reason()`
and the gates behind it) answers from that record, so a Start/Remove-containers, a module or
account write (`server_claim`) and Back up now (`maintenance_lease`) refuse with the restore's
own sentence without each door asking. A plain Stop is not refused here: the Server tab asks
first (T711), "Stop always stops". The child is a real process, never a fake pid.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.test_restore_load_pid import _marker_with
from yulon import docker, forgetting
from yulon.controller_wow_wotlk import maintenance

SPEC: Any = None  # no reservation is made in these tests (RESERVATIONS_ON is off)
SAYS = "still loading its databases"


@pytest.fixture
def live_child() -> Iterator[Any]:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield child
    child.kill()
    child.wait()


@pytest.fixture(autouse=True)
def _no_commands(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    seen: list[list[str]] = []
    monkeypatch.setattr(docker, "_run", lambda argv, **_kw: seen.append(list(argv)))
    return seen


def test_held_reason_answers_from_a_running_load(tmp_path: Path, live_child: Any) -> None:
    _marker_with(tmp_path, live_child.pid)
    said = docker.held_reason(tmp_path)
    assert said is not None and SAYS in said
    assert not docker.held_here(tmp_path), "held_here stays this process's own"


@pytest.mark.parametrize("how", ["dead", "other-host", "stale-token", "old", "none", "unreadable"])
def test_a_load_that_is_not_running_holds_nothing(
    tmp_path: Path, how: str, live_child: Any
) -> None:
    if how == "dead":
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        _marker_with(tmp_path, gone.pid)
    elif how == "other-host":
        _marker_with(tmp_path, live_child.pid, host="some-other-computer")
    elif how == "stale-token":
        _marker_with(tmp_path, live_child.pid, token="not-its-start-time")
    elif how == "old":
        _marker_with(tmp_path, live_child.pid)
        old = time.time() - 3 * 24 * 3600
        os.utime(maintenance.marker_path(tmp_path), (old, old))
    elif how == "unreadable":
        maintenance.backups_dir(tmp_path).mkdir(parents=True)
        maintenance.marker_path(tmp_path).write_text("{ not json", encoding="utf-8")
    assert docker.held_reason(tmp_path) is None


def test_a_start_or_container_removal_is_refused_with_the_restores_sentence(
    tmp_path: Path, live_child: Any, _no_commands: list[list[str]]
) -> None:
    _marker_with(tmp_path, live_child.pid)
    with pytest.raises(docker.ServerHeldError, match=SAYS):
        docker.start(tmp_path)
    with pytest.raises(docker.ServerHeldError, match=SAYS):
        with docker.lifecycle(tmp_path):
            pass  # a Restart / recreate
    assert _no_commands == [], "nothing was run"


def test_a_module_or_account_write_is_refused(tmp_path: Path, live_child: Any) -> None:
    _marker_with(tmp_path, live_child.pid)
    with pytest.raises(docker.ServerHeldError, match=SAYS):
        with docker.server_claim(tmp_path, press="A module's SQL"):
            pytest.fail("the press ran")


def test_back_up_now_is_refused(tmp_path: Path, live_child: Any) -> None:
    _marker_with(tmp_path, live_child.pid)
    with pytest.raises(docker.MaintenanceLeaseTaken, match=SAYS):
        with docker.maintenance_lease(tmp_path, "A backup"):
            pytest.fail("the backup ran")


def test_a_plain_stop_is_not_refused(tmp_path: Path, live_child: Any) -> None:
    """ "Stop always stops": the Server tab's own question (T711) is the only gate on it."""
    _marker_with(tmp_path, live_child.pid)
    with docker.server_claim(tmp_path, press=forgetting.PRESS_STOP):
        pass


def test_nothing_is_refused_without_a_record(tmp_path: Path) -> None:
    assert docker.held_reason(tmp_path) is None
    docker.start(tmp_path)
    with docker.maintenance_lease(tmp_path, "A backup"):
        pass


def test_a_restore_of_this_process_keeps_its_own_hold_sentence(
    tmp_path: Path, live_child: Any
) -> None:
    """The orphan answer steps aside for this process's own hold, which speaks for itself."""
    _marker_with(tmp_path, live_child.pid)
    with docker.hold_the_server(tmp_path, "OWN RESTORE SENTENCE", press="Restore", alone="x"):
        assert docker.held_reason(tmp_path) == "OWN RESTORE SENTENCE"
        with pytest.raises(docker.ServerHeldError, match="OWN RESTORE SENTENCE"):
            docker.start(tmp_path)


class _World:
    """Just what the move flows' stop door touches."""

    def __init__(self, server: Path) -> None:
        self.server_dir = server
        self.stopped = 0

    def stop_server(self) -> None:
        self.stopped += 1


def test_the_move_flows_stop_door_is_refused_and_stops_nothing(
    tmp_path: Path, live_child: Any
) -> None:
    from yulon.move_flows import MoveError, refuse_over_a_running_load

    world = _World(tmp_path)
    refuse_over_a_running_load(world)  # type: ignore[arg-type]  # no record: goes ahead
    _marker_with(tmp_path, live_child.pid)
    with pytest.raises(MoveError, match=SAYS):
        refuse_over_a_running_load(world)  # type: ignore[arg-type]


def test_a_move_ins_stop_step_asks_it_before_stopping(
    tmp_path: Path, live_child: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon import move_flows, move_server

    monkeypatch.setattr(move_flows, "_server_is_up", lambda _w: True)
    world = _World(tmp_path)
    _marker_with(tmp_path, live_child.pid)
    with pytest.raises(move_flows.MoveError, match=SAYS):
        list(move_server._stop_if_running(world))  # type: ignore[arg-type]
    assert world.stopped == 0


def test_a_record_with_no_start_time_holds_nothing_new(tmp_path: Path, live_child: Any) -> None:
    """A reused pid cannot be told from the load, and Forget refuses while it reads as running."""
    _marker_with(tmp_path, live_child.pid, token="")
    record = maintenance.interrupted_restore(tmp_path)
    assert record is not None and record.load_running  # the old effect stays
    assert docker.held_reason(tmp_path) is None
    docker.start(tmp_path)
    with docker.maintenance_lease(tmp_path, "A backup"):
        pass


@pytest.mark.parametrize("stop_allowed", [True])
def test_an_import_that_would_stop_the_server_is_refused_before_any_stop_or_write(
    tmp_path: Path, live_child: Any, stop_allowed: bool
) -> None:
    """Mutation: replace `refuse_over_a_running_load` in `run_import` with `pass`."""
    from tests.test_move_flows import ready
    from yulon import move_flows

    box, _package, plan = ready(
        tmp_path, running_now=("ac-database", "ac-authserver", "ac-worldserver")
    )
    _marker_with(box.server_dir, live_child.pid)
    with pytest.raises(move_flows.MoveError, match=SAYS):
        move_flows.run_import(
            box.world, plan, confirm=None, use_old_realm_name=False, stop_allowed=stop_allowed
        )
    assert box.events == [], "nothing was stopped, dumped or loaded"
