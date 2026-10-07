"""T539: a Stopped database import's container is ended, and a retry never clears under one.

The install's import runs `docker compose up --no-deps ac-db-import` attached, and a
Stop ends the CLI. Whether the importer's container goes with it is compose's
choice and the platform's: on Linux compose stops it when its CLI is signalled
(measured on yulon-ubuntu 2026-10-07), on Windows the Stop ends compose itself
with docker.exe (T246/T299) and Docker Desktop keeps a container whose CLI is gone
(T303). An importer left running is then under the next Install's `partial`
branch, which DROPs the half-written schemas it is still writing.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

import pytest

from tests.support_native import ABSENT, IMPORTED, PARTIAL, Recorder, engine
from yulon import docker
from yulon.after_stop import TrueAfterStop
from yulon.catalog.installer import InstallerError, InstallOptions, InstallStopped

IMPORTER = "ac-db-import"


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["docker"], returncode, stdout, "")


class _Daemon:
    """`docker._docker` for `end_one_shot()`: a list of running importers, and what kill does."""

    def __init__(self, running: list[str], *, kill_works: bool = True) -> None:
        self.running = list(running)
        self.kill_works = kill_works
        self.asked: list[list[str]] = []

    def __call__(
        self, argv: list[str], *a: object, **k: object
    ) -> subprocess.CompletedProcess[str]:
        self.asked.append(list(argv))
        if argv[0] == "ps":
            return _completed("\n".join(self.running))
        if argv[0] == "kill":
            if self.kill_works:
                self.running = [name for name in self.running if name not in argv[1:]]
            return _completed()
        raise AssertionError(f"unexpected docker call {argv}")


@pytest.fixture
def project(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(docker, "pinned_project_name", lambda server_dir: "wow-server")
    monkeypatch.setattr(docker.time, "sleep", lambda seconds: None)
    return "wow-server"


def test_end_one_shot_with_nothing_running_kills_nothing(
    monkeypatch: pytest.MonkeyPatch, project: str, tmp_path: Path
) -> None:
    daemon = _Daemon([])
    monkeypatch.setattr(docker, "_docker", daemon)
    assert docker.end_one_shot(IMPORTER, tmp_path) is None
    assert [argv[0] for argv in daemon.asked] == ["ps"]
    assert f"label={docker.PROJECT_LABEL}={project}" in daemon.asked[0]
    assert f"label={docker.SERVICE_LABEL}={IMPORTER}" in daemon.asked[0]


def test_end_one_shot_kills_this_installs_running_importer_and_sees_it_gone(
    monkeypatch: pytest.MonkeyPatch, project: str, tmp_path: Path
) -> None:
    """The `up` container and a `run` one both carry the project and service labels."""
    daemon = _Daemon([IMPORTER, f"{project}-{IMPORTER}-run-0a1b2c"])
    monkeypatch.setattr(docker, "_docker", daemon)
    assert docker.end_one_shot(IMPORTER, tmp_path) is None
    assert ["kill", IMPORTER, f"{project}-{IMPORTER}-run-0a1b2c"] in daemon.asked
    assert daemon.running == []


def test_end_one_shot_says_which_importer_would_not_go(
    monkeypatch: pytest.MonkeyPatch, project: str, tmp_path: Path
) -> None:
    """Still running at the deadline: named, with the command that removes it."""
    daemon = _Daemon([IMPORTER], kill_works=False)
    monkeypatch.setattr(docker, "_docker", daemon)
    ticks = iter(range(0, 1000, 10))
    monkeypatch.setattr(docker.time, "monotonic", lambda: float(next(ticks)))
    left = docker.end_one_shot(IMPORTER, tmp_path)
    assert left is not None
    assert left.names == (IMPORTER,)
    assert "still running" in left.reason


def test_end_one_shot_that_cannot_ask_docker_does_not_claim_it_is_gone(
    monkeypatch: pytest.MonkeyPatch, project: str, tmp_path: Path
) -> None:
    monkeypatch.setattr(docker, "_docker", lambda argv, *a, **k: _completed(returncode=1))
    left = docker.end_one_shot(IMPORTER, tmp_path)
    assert left is not None and left.names == ()


# ---------------------------------------------------------------- the engine


def _stopped_import(rec: Recorder, cancel: threading.Event):  # type: ignore[no-untyped-def]
    """The one-shot seam with Stop pressed during the IMPORT (the server-data one runs clean)."""

    def one_shot(service: str, server_dir: Path, **_kw: object) -> docker.AttachedRun:
        rec.calls.append(f"one-shot:{service}")
        if service != IMPORTER:
            return docker.AttachedRun(0, ("ran",))
        cancel.set()
        return docker.AttachedRun(docker.CANCELLED_RETURNCODE, ("importing acore_world",))

    return one_shot


def test_a_stopped_import_ends_its_importer_before_it_says_stopped(tmp_path: Path) -> None:
    """The Stop's sentence promises the half-written databases are cleared before the import
    runs again; that is only true once no importer is still writing them.

    Mutation this catches: the Stop raising `InstallStopped` without `end_one_shot()`.
    """
    rec = Recorder()
    cancel = threading.Event()
    with pytest.raises(InstallStopped):
        list(
            engine(rec, one_shot=_stopped_import(rec, cancel)).run(
                InstallOptions(server_dir=tmp_path / "s"), cancel=cancel
            )
        )
    # Once before the import (nothing left from an earlier run) and once after its Stop.
    assert rec.ended_one_shots == [IMPORTER, IMPORTER]


def test_a_stopped_import_whose_importer_would_not_end_says_so_after_the_stop(
    tmp_path: Path,
) -> None:
    """Not a clean Stop: the databases may still be written. Shown under "Stopped. FAILED:"."""
    rec = Recorder()
    cancel = threading.Event()

    def end_one_shot(service: str, server_dir: Path) -> docker.OneShotLeft | None:
        # Nothing before the import; after its Stop, an importer that would not go.
        if not cancel.is_set():
            return None
        return docker.OneShotLeft((IMPORTER,), "it was still running 30 s after the kill")

    with pytest.raises(InstallerError) as raised:
        list(
            engine(rec, one_shot=_stopped_import(rec, cancel), end_one_shot=end_one_shot).run(
                InstallOptions(server_dir=tmp_path / "s"), cancel=cancel
            )
        )
    assert isinstance(raised.value, TrueAfterStop), type(raised.value)
    assert not isinstance(raised.value, InstallStopped), "a Stop that left a writer is not clean"
    said = str(raised.value)
    assert IMPORTER in said and f"docker rm -f {IMPORTER}" in said, said


@pytest.mark.parametrize("before", [PARTIAL, ABSENT], ids=["partial", "absent"])
def test_a_retry_never_clears_or_imports_under_a_running_importer(
    tmp_path: Path, before: docker.ImportState
) -> None:
    """The guard before the reset: an importer that cannot be ended stops the stage cold.

    Mutation this catches: the guard missing (the reset runs, and the one-shot).
    """
    rec = Recorder(probe_answers=[before, IMPORTED])
    rec.one_shot_left = docker.OneShotLeft((IMPORTER,), "Docker would not kill it")
    with pytest.raises(InstallerError) as raised:
        list(engine(rec).run(InstallOptions(server_dir=tmp_path / "s")))
    assert "reset" not in rec.calls, rec.calls
    assert f"one-shot:{IMPORTER}" not in rec.calls, rec.calls
    assert IMPORTER in str(raised.value), raised.value


def test_a_retry_ends_a_leftover_importer_before_it_clears(tmp_path: Path) -> None:
    """One that CAN be ended is ended first; then the reset and the import run as before."""
    rec = Recorder(probe_answers=[PARTIAL, IMPORTED])
    list(engine(rec).run(InstallOptions(server_dir=tmp_path / "s")))
    assert rec.ended_one_shots == [IMPORTER]
    assert rec.calls.index("reset") < rec.calls.index(f"one-shot:{IMPORTER}"), rec.calls


_ONE_LINE_THEN_SILENCE = "import time; print('Importing acore_world', flush=True); time.sleep(600)"


def test_a_cancelled_one_shot_has_its_container_ended_as_the_stop_lands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Measured live (yulon-ubuntu, 2026-10-07): one SIGTERM to `compose up` starts a
    graceful stop, `_end_child` kills the CLI 5 s later, and the orphaned compose kept the
    importer and the pipe 46 s. So the run's own cancel ends the container at once.

    Mutation this catches: `run_one_shot()` not handing `run_attached()` its ending.
    """
    from tests.conftest import HANG_BOUND

    monkeypatch.setattr(
        docker.platform,
        "docker_prefix",
        lambda *a, **k: (sys.executable, "-c", _ONE_LINE_THEN_SILENCE),
    )
    ended: list[tuple[str, Path]] = []
    monkeypatch.setattr(
        docker,
        "end_one_shot",
        lambda service, server_dir, **k: ended.append((service, server_dir)),
    )
    cancel = threading.Event()
    result: list[docker.AttachedRun] = []

    def work() -> None:
        result.append(
            docker.run_one_shot(IMPORTER, tmp_path, sink=lambda line: cancel.set(), cancel=cancel)
        )

    worker = threading.Thread(target=work, daemon=True, name="test-t539-one-shot")
    worker.start()
    worker.join(timeout=HANG_BOUND)
    assert not worker.is_alive(), "the cancelled one-shot did not return"
    assert result[0].returncode == docker.CANCELLED_RETURNCODE, result
    assert ended == [(IMPORTER, tmp_path)], ended
