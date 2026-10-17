"""T754: a Stop that ends a running database import leaves the record Start reads.

T658's record (`docker.one_shot_ended_marker()`) was written only by `end_one_shot()`.
A WotLK/Unbound server started by hand (`docker compose up -d`, the Steam Deck
report) runs `ac-db-import` for about ten minutes; Stop, Restart, Recreate, Quit and
"Stop the other server" then ended that importer with `compose stop`/`down`/`docker stop`
and wrote nothing. AzerothCore creates its `updates` tables first, so the half-imported
schemas read `imported` and the next Start ran on them with an empty realm list.

Every route goes through one of three engine calls: `stop_staged()` (Stop, Restart,
the tray, Quit), `remove_staged()` (Recreate) and `Controller.stop_conflicting()`.
Each now ends a running importer through `end_one_shot(record_ended=True)` first.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from yulon import docker, runner
from yulon.controller import Controller
from yulon.controller_wow_wotlk import docker_ctl

SPEC = docker_ctl.SPEC
PROJECT = "wow-server"
IMPORTER = SPEC.import_service


def _completed(stdout: str = "", rc: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], rc, stdout, "")


class _Daemon:
    """`runner.run` for one install's project: what runs, and what each command does to it.

    `ps` with a service filter is `end_one_shot()`'s look and answers with the folder
    compose stamped; `compose stop`/`down` end every service of the project, the
    importer included, as the real ones do.
    """

    def __init__(self, folder: Path, live: set[str]) -> None:
        self.folder = folder
        self.live = set(live)
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], cwd: Any = None, timeout: Any = None) -> Any:
        self.calls.append(list(cmd))
        argv = cmd[1:]
        if argv[:2] == ["compose", "config"]:
            return _completed('{"name": "' + PROJECT + '"}')
        if argv[:2] in (["compose", "stop"], ["compose", "down"]):
            self.live.clear()
            return _completed()
        if argv[:1] == ["inspect"]:
            return _completed(PROJECT + "\n")
        if argv[:1] == ["kill"]:
            self.live -= set(argv[1:])
            return _completed()
        if argv[:1] == ["ps"]:
            if f"label={docker.SERVICE_LABEL}={IMPORTER}" in argv:
                return _completed(f"{IMPORTER}\t{self.folder}\n" if IMPORTER in self.live else "")
            return _completed("".join(n + "\n" for n in sorted(self.live)))
        return _completed()


@pytest.fixture(autouse=True)
def _no_settle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker.time, "sleep", lambda seconds: None)


def _record(folder: Path) -> Path:
    return docker.one_shot_ended_marker(folder, IMPORTER)


@pytest.mark.parametrize("press", [docker.stop_staged, docker.remove_staged])
def test_a_stop_that_ends_a_running_import_records_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, press: Any
) -> None:
    """The audit's proof, through the real `stop_staged()` / `remove_staged()`.

    Mutation this catches: dropping the end-the-import-first call from either.
    """
    assert IMPORTER == "ac-db-import"
    daemon = _Daemon(tmp_path, {SPEC.db, IMPORTER})
    monkeypatch.setattr(runner, "run", daemon)

    press(SPEC, tmp_path)

    assert IMPORTER not in daemon.live
    assert _record(tmp_path).is_file(), (
        "a Stop ended a running database import and recorded nothing, so Start will not "
        "refuse the half-imported databases"
    )
    kill = next(i for i, c in enumerate(daemon.calls) if c[1:2] == ["kill"])
    ends = (["compose", "stop"], ["compose", "down"])
    compose = next(i for i, c in enumerate(daemon.calls) if c[1:3] in ends)
    assert kill < compose, "the importer must be ended (and recorded) before compose ends it"


@pytest.mark.parametrize("press", [docker.stop_staged, docker.remove_staged])
def test_a_stop_with_no_import_running_records_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, press: Any
) -> None:
    """An import that finished long ago is not running: no record over a finished import.

    Mutation this catches: writing the record on every Stop.
    """
    daemon = _Daemon(tmp_path, {SPEC.db, SPEC.auth, SPEC.world})
    monkeypatch.setattr(runner, "run", daemon)
    monkeypatch.setattr(docker, "wait_for_the_world_to_load", lambda *a, **k: None)
    monkeypatch.setattr(docker, "save_then_stop_the_world", lambda *a, **k: None)

    assert press(SPEC, tmp_path) is True
    assert not _record(tmp_path).exists()
    assert not [c for c in daemon.calls if c[1:2] == ["kill"]]


def test_stopping_the_other_server_records_the_import_it_ends(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ "Stop the other server" ends a hand-started import of ANOTHER install: its folder
    gets the record its own tab's Start reads.

    Mutation this catches: dropping the importer pass from `stop_conflicting()`.
    """
    ours, other = tmp_path / "ours", tmp_path / "other"
    ours.mkdir()
    other.mkdir()
    daemon = _Daemon(other, {"o-db", IMPORTER})
    monkeypatch.setattr(runner, "run", daemon)
    monkeypatch.setattr(docker, "pinned_project_name", lambda server_dir: PROJECT)
    monkeypatch.setattr(docker, "container_project", lambda *_a, **_k: PROJECT)
    monkeypatch.setattr(docker, "project_containers", lambda *_a, **_k: ["o-db", IMPORTER])
    monkeypatch.setattr(docker, "container_working_dir", lambda *_a, **_k: str(other))
    stopped: list[list[str]] = []

    def stop_containers(names: list[str], **_k: Any) -> None:
        stopped.append(list(names))
        daemon.live -= set(names)

    monkeypatch.setattr(docker, "stop_containers", stop_containers)
    ac = docker.ContainerSpec(db="u-db", auth="u-auth", world="u-world", ports=(3724,))
    controller = Controller(ac, ours)
    controller.port_conflicts = lambda: ["o-db"]  # type: ignore[method-assign]

    assert controller.stop_conflicting() == ["o-db", IMPORTER]
    assert stopped == [["o-db", IMPORTER]]
    assert _record(other).is_file(), "the other server's cut import was not recorded"
    assert not _record(ours).exists(), "the record belongs in the other server's folder"


def test_stopping_the_other_server_with_its_import_finished_records_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The importer is listed (`project_containers` lists exited ones) but not running.

    Mutation this catches: recording on the name alone, without asking whether it runs.
    """
    ours, other = tmp_path / "ours", tmp_path / "other"
    ours.mkdir()
    other.mkdir()
    daemon = _Daemon(other, {"o-db"})
    monkeypatch.setattr(runner, "run", daemon)
    monkeypatch.setattr(docker, "pinned_project_name", lambda server_dir: PROJECT)
    monkeypatch.setattr(docker, "container_project", lambda *_a, **_k: PROJECT)
    monkeypatch.setattr(docker, "project_containers", lambda *_a, **_k: ["o-db", IMPORTER])
    monkeypatch.setattr(docker, "container_working_dir", lambda *_a, **_k: str(other))
    monkeypatch.setattr(docker, "stop_containers", lambda names, **_k: None)
    ac = docker.ContainerSpec(db="u-db", auth="u-auth", world="u-world", ports=(3724,))
    controller = Controller(ac, ours)
    controller.port_conflicts = lambda: ["o-db"]  # type: ignore[method-assign]

    controller.stop_conflicting()
    assert not _record(other).exists()


def test_a_game_with_no_import_service_is_not_asked_about_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CMaNGOS and Centurion have no importer container: their Stop asks Docker nothing new.

    Mutation this catches: dropping the no-import-service return, which sends
    `end_one_shot("")` and its settle wait into every CMaNGOS Stop.
    """
    tbc = docker.ContainerSpec(db="tbc-db", auth="tbc-realmd", world="tbc-mangosd", ports=())
    daemon = _Daemon(tmp_path, {"tbc-db"})
    monkeypatch.setattr(runner, "run", daemon)

    docker.stop_staged(tbc, tmp_path)

    assert not [c for c in daemon.calls if any(docker.SERVICE_LABEL in a for a in c)]
