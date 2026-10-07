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
    """`docker._docker` for `end_one_shot()`: running importers, the folder each was brought
    up from, and what kill does."""

    def __init__(
        self,
        running: list[str],
        folder: Path,
        *,
        kill_works: bool = True,
        foreign: tuple[str, ...] = (),
    ) -> None:
        self.running = list(running)
        self.folder = folder
        self.kill_works = kill_works
        self.foreign = foreign
        self.asked: list[list[str]] = []

    def __call__(
        self, argv: list[str], *a: object, **k: object
    ) -> subprocess.CompletedProcess[str]:
        self.asked.append(list(argv))
        if argv[0] == "ps":
            rows = [
                f"{name}\t{'/elsewhere/wow-server' if name in self.foreign else self.folder}"
                for name in self.running
            ]
            return _completed("\n".join(rows))
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
    """Asked twice, a settle apart: a create the daemon finished late is seen too
    (Codex adversarial review; `container_end.LATE_CREATE_SETTLE`, T321's bound).

    Mutation this catches: answering "nothing runs" on one empty look.
    """
    daemon = _Daemon([], tmp_path)
    monkeypatch.setattr(docker, "_docker", daemon)
    assert docker.end_one_shot(IMPORTER, tmp_path) is None
    assert [argv[0] for argv in daemon.asked] == ["ps", "ps"]
    assert f"label={docker.PROJECT_LABEL}={project}" in daemon.asked[0]
    assert f"label={docker.SERVICE_LABEL}={IMPORTER}" in daemon.asked[0]


def test_end_one_shot_kills_this_installs_running_importer_and_sees_it_gone(
    monkeypatch: pytest.MonkeyPatch, project: str, tmp_path: Path
) -> None:
    """The `up` container and a `run` one both carry the project and service labels."""
    daemon = _Daemon([IMPORTER, f"{project}-{IMPORTER}-run-0a1b2c"], tmp_path)
    monkeypatch.setattr(docker, "_docker", daemon)
    assert docker.end_one_shot(IMPORTER, tmp_path) is None
    assert ["kill", IMPORTER, f"{project}-{IMPORTER}-run-0a1b2c"] in daemon.asked
    assert daemon.running == []
    # Gone is "seen empty twice, a settle apart", after a kill as before one (adversarial
    # review: a create in flight can land after the first empty look).
    assert [argv[0] for argv in daemon.asked] == ["ps", "kill", "ps", "ps"]


def test_end_one_shot_never_kills_another_installs_importer(
    monkeypatch: pytest.MonkeyPatch, project: str, tmp_path: Path
) -> None:
    """Codex adversarial review: project and service labels are not proof of ownership.

    Two installs in folders of the same name share a compose project name, so the
    folder the container was brought up from has to be this install's too.

    Mutation this catches: killing on the project and service labels alone.
    """
    daemon = _Daemon([IMPORTER], tmp_path, foreign=(IMPORTER,))
    monkeypatch.setattr(docker, "_docker", daemon)
    assert docker.end_one_shot(IMPORTER, tmp_path) is None
    assert not [argv for argv in daemon.asked if argv[0] == "kill"], daemon.asked


def test_end_one_shot_says_which_importer_would_not_go(
    monkeypatch: pytest.MonkeyPatch, project: str, tmp_path: Path
) -> None:
    """Still running at the deadline: named, with the command that removes it."""
    daemon = _Daemon([IMPORTER], tmp_path, kill_works=False)
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
    assert [s for s in rec.ended_one_shots if s == IMPORTER] == [IMPORTER, IMPORTER]


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
    assert [s for s in rec.ended_one_shots if s == IMPORTER] == [IMPORTER]
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
    # As the Stop lands, and once more after the CLI is gone (Codex review of 5b67822c).
    assert ended and set(ended) == {(IMPORTER, tmp_path)}, ended


def test_a_one_shot_is_ended_only_after_its_cli_was_claimed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Codex review: with the cancel set before the lazy CLI starts, the first poll claims
    nothing, and a container ended then does not exist yet. The ending waits for the CLI:
    the watcher's claim of it, or its first line.

    Mutation this catches: `on_cancel` consumed on the first poll, claimed or not.
    """
    from tests.conftest import HANG_BOUND

    monkeypatch.setattr(
        docker.platform,
        "docker_prefix",
        lambda *a, **k: (sys.executable, "-c", _ONE_LINE_THEN_SILENCE),
    )
    order: list[str] = []
    polled_unclaimed = threading.Event()
    real_end_stream = docker.runner.end_stream

    def end_stream(stream: object) -> bool:
        claimed = real_end_stream(stream)  # type: ignore[arg-type]
        order.append(f"claim:{claimed}")
        if not claimed:
            polled_unclaimed.set()
        return claimed

    monkeypatch.setattr(docker.runner, "end_stream", end_stream)
    monkeypatch.setattr(docker, "end_one_shot", lambda *a, **k: order.append("end-one-shot"))
    real_spawn = docker.runner._spawn

    def held_spawn(start):  # type: ignore[no-untyped-def]
        # The CLI starts only once the watcher has polled and found nothing to claim.
        polled_unclaimed.wait(HANG_BOUND)
        spawned = real_spawn(start)
        order.append("spawned")
        return spawned

    monkeypatch.setattr(docker.runner, "_spawn", held_spawn)
    cancel = threading.Event()
    cancel.set()
    result: list[docker.AttachedRun] = []
    worker = threading.Thread(
        target=lambda: result.append(docker.run_one_shot(IMPORTER, tmp_path, cancel=cancel)),
        daemon=True,
    )
    worker.start()
    worker.join(timeout=HANG_BOUND)
    assert not worker.is_alive()
    assert "end-one-shot" in order, order
    assert order.index("spawned") < order.index("end-one-shot"), order


def test_a_cancel_set_after_the_last_line_still_ends_what_the_run_made(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Codex adversarial review: set after the final line and before the exit, a cancel is
    seen by neither the read nor the watcher. The run's ending runs all the same; its exit
    stays its own (the run finished; the stage reports the late Stop as before).

    Mutation this catches: no reconciliation when the read ends with the cancel set.
    """
    monkeypatch.setattr(
        docker.platform,
        "docker_prefix",
        lambda *a, **k: (sys.executable, "-c", "print('Importing acore_world', flush=True)"),
    )
    ended: list[str] = []
    monkeypatch.setattr(docker, "end_one_shot", lambda service, *a, **k: ended.append(service))
    cancel = threading.Event()

    me = threading.get_ident()

    def last_line(line: str) -> None:
        # Wait until the CLI has exited, so there is nothing left for the watcher to claim.
        from tests.conftest import HANG_BOUND

        with docker.runner._LIVE_STREAMS_LOCK:
            mine = [c for c in docker.runner._LIVE_STREAMS.values() if c.started_on == me]
        assert mine and mine[-1].proc is not None
        mine[-1].proc.wait(timeout=HANG_BOUND)
        cancel.set()

    run = docker.run_one_shot(IMPORTER, tmp_path, sink=last_line, cancel=cancel)
    assert run.returncode == 0, run
    assert ended == [IMPORTER], ended


@pytest.mark.parametrize("reading", [ABSENT, IMPORTED], ids=["absent", "imported"])
def test_a_leftover_importer_is_ended_before_the_databases_are_read(
    tmp_path: Path, reading: docker.ImportState
) -> None:
    """Cold review of 9bc9da0a (MUST): the guard ran after the probe, so the stage acted on a
    reading a live importer was still changing. `absent` then imported over the tables it
    had made with no reset -- IMPORT_CANCEL_NOTE's "permanently unimportable" case -- and
    `imported` returned before the guard, the importer writing on as the world started.

    Mutation this catches: `end_one_shot()` asked after `gate.probe()`.
    """
    rec = Recorder(probe_answers=[reading, IMPORTED])
    order: list[str] = []
    real_probe = rec.probe

    def probe() -> docker.ImportState:
        order.append("probe")
        return real_probe()

    def end_one_shot(service: str, server_dir: Path) -> docker.OneShotLeft | None:
        order.append("end-one-shot")
        return None

    made = engine(rec, end_one_shot=end_one_shot)
    made._probe = probe  # type: ignore[attr-defined]
    list(made.run(InstallOptions(server_dir=tmp_path / "s")))
    assert "end-one-shot" in order and "probe" in order, order
    assert order.index("end-one-shot") < order.index("probe"), order


def test_a_held_claim_does_not_end_the_one_shot_before_its_cli_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cold review of 9bc9da0a: a claim made while the CLI is being started is held (T546)
    and reads as claimed; the container the CLI is about to make does not exist yet, so
    the run's ending waits until the CLI has started.

    Mutation this catches: the watcher running `on_cancel` on a held claim.
    """
    from tests.conftest import HANG_BOUND

    monkeypatch.setattr(
        docker.platform,
        "docker_prefix",
        lambda *a, **k: (sys.executable, "-c", _ONE_LINE_THEN_SILENCE),
    )
    order: list[str] = []
    claimed_held = threading.Event()
    real_end_stream = docker.runner.end_stream

    def end_stream(stream: object) -> bool:
        claimed = real_end_stream(stream)  # type: ignore[arg-type]
        started = docker.runner.stream_started(stream)  # type: ignore[arg-type]
        order.append(f"claim:{claimed}:started:{started}")
        if claimed and not started:
            claimed_held.set()
        return claimed

    monkeypatch.setattr(docker.runner, "end_stream", end_stream)
    monkeypatch.setattr(docker, "end_one_shot", lambda *a, **k: order.append("end-one-shot"))
    real_spawn = docker.runner._spawn

    def spawn_after_a_held_claim(start):  # type: ignore[no-untyped-def]
        assert claimed_held.wait(HANG_BOUND), "the watcher never held a claim"
        threading.Event().wait(0.3)  # more polls with the claim held, before the CLI exists
        started = real_spawn(start)
        order.append("spawned")
        return started

    monkeypatch.setattr(docker.runner, "_spawn", spawn_after_a_held_claim)
    cancel = threading.Event()
    cancel.set()
    result: list[docker.AttachedRun] = []
    worker = threading.Thread(
        target=lambda: result.append(docker.run_one_shot(IMPORTER, tmp_path, cancel=cancel)),
        daemon=True,
    )
    worker.start()
    worker.join(timeout=HANG_BOUND)
    assert not worker.is_alive()
    assert "end-one-shot" in order, order
    assert order.index("spawned") < order.index("end-one-shot"), order


# A CLI that takes 1 s to stop on SIGTERM: its container could still appear in that second.
_SLOW_TO_STOP = (
    "import signal, sys, time\n"
    "signal.signal(signal.SIGTERM, lambda *a: (time.sleep(1), sys.exit(0)))\n"
    "print('Importing acore_world', flush=True)\n"
    "time.sleep(600)\n"
)


def test_a_cancelled_one_shot_is_ended_again_once_its_cli_is_gone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Codex review of 5b67822c: the CLI having started does not mean its container exists,
    so the ending runs once more after the CLI has exited.

    Mutation this catches: no ending after the CLI is gone (every call sees it running).
    """
    from tests.conftest import HANG_BOUND

    monkeypatch.setattr(
        docker.platform,
        "docker_prefix",
        lambda *a, **k: (sys.executable, "-c", _SLOW_TO_STOP),
    )
    spawned: list[object] = []
    real_spawn = docker.runner._spawn

    def spawn(start):  # type: ignore[no-untyped-def]
        proc, job = real_spawn(start)
        spawned.append(proc)
        return proc, job

    monkeypatch.setattr(docker.runner, "_spawn", spawn)
    calls: list[bool] = []
    monkeypatch.setattr(
        docker,
        "end_one_shot",
        lambda *a, **k: calls.append(spawned[0].poll() is not None),  # type: ignore[attr-defined]
    )
    cancel = threading.Event()
    result: list[docker.AttachedRun] = []
    worker = threading.Thread(
        target=lambda: result.append(
            docker.run_one_shot(IMPORTER, tmp_path, sink=lambda line: cancel.set(), cancel=cancel)
        ),
        daemon=True,
    )
    worker.start()
    worker.join(timeout=HANG_BOUND)
    assert not worker.is_alive()
    assert result[0].returncode == docker.CANCELLED_RETURNCODE, result
    assert calls and calls[-1] is True, f"never ended after the CLI had exited: {calls}"


# ---------------------------------------------------------------- Repair the database


def test_repair_ends_a_leftover_importer_before_it_reads_the_databases(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Re-review of 7312223b (MUST): Repair is offered in exactly the half-written state an
    orphaned importer leaves (Yu'lon closed mid-import; Docker Desktop keeps the container),
    and it read, cleared and re-imported under it.

    Mutation this catches: `repair_import()` without the guard, or with it after the probe.
    """
    from yulon.controller_wow_wotlk.docker_ctl import SPEC

    order: list[str] = []
    monkeypatch.setattr(docker, "install_project", lambda *a, **k: "wow-server")
    monkeypatch.setattr(docker, "_running", lambda *a, **k: docker.Running())
    monkeypatch.setattr(docker, "start_database", lambda *a, **k: order.append("start-db"))
    monkeypatch.setattr(
        docker, "end_one_shot", lambda service, *a, **k: order.append(f"end-one-shot:{service}")
    )
    monkeypatch.setattr(docker, "run_one_shot", lambda *a, **k: docker.AttachedRun(0))
    monkeypatch.setattr(docker, "verify_import", lambda *a, **k: None)

    def probe() -> docker.ImportState:
        order.append("probe")
        return ABSENT

    assert docker.repair_import(SPEC, tmp_path, probe) is True
    assert f"end-one-shot:{SPEC.import_service}" in order, order
    assert order.index(f"end-one-shot:{SPEC.import_service}") < order.index("probe"), order


def test_repair_refuses_while_an_importer_it_could_not_end_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Nothing is read, cleared or run under it; the sentence names it and how to end it."""
    from yulon.controller_wow_wotlk.docker_ctl import SPEC

    asked: list[str] = []
    monkeypatch.setattr(docker, "install_project", lambda *a, **k: "wow-server")
    monkeypatch.setattr(docker, "_running", lambda *a, **k: docker.Running())
    monkeypatch.setattr(docker, "start_database", lambda *a, **k: None)
    monkeypatch.setattr(
        docker,
        "end_one_shot",
        lambda *a, **k: docker.OneShotLeft((IMPORTER,), "Docker would not kill it"),
    )
    monkeypatch.setattr(docker, "run_one_shot", lambda *a, **k: asked.append("import"))

    with pytest.raises(docker.DockerRefusal) as refused:
        docker.repair_import(SPEC, tmp_path, lambda: asked.append("probe") or PARTIAL)
    assert asked == [], asked
    assert f"docker rm -f {IMPORTER}" in str(refused.value), refused.value


# ---------------------------------------------------------------- the server-data download

DOWNLOADER = "ac-client-data-init"


def test_a_download_left_running_is_ended_before_another_starts_and_refused_if_not(
    tmp_path: Path,
) -> None:
    """Re-review of 7312223b (SHOULD): the client-data stage had no guard of its own.

    Mutation this catches: the download started without asking `end_one_shot()` first.
    """
    from tests.support_native import ENTRY

    assert ENTRY.containers.client_data == DOWNLOADER
    rec = Recorder()

    def end_one_shot(service: str, server_dir: Path) -> docker.OneShotLeft | None:
        rec.ended_one_shots.append(service)
        if service == DOWNLOADER:
            return docker.OneShotLeft((DOWNLOADER,), "Docker would not kill it")
        return None

    with pytest.raises(InstallerError) as raised:
        list(engine(rec, end_one_shot=end_one_shot).run(InstallOptions(server_dir=tmp_path / "s")))
    assert f"one-shot:{DOWNLOADER}" not in rec.calls, rec.calls
    assert "server-data download" in str(raised.value), raised.value
    assert f"docker rm -f {DOWNLOADER}" in str(raised.value), raised.value


def test_a_stopped_download_whose_container_would_not_end_is_not_a_clean_stop(
    tmp_path: Path,
) -> None:
    """A surviving download container read as a clean Stop (re-review of 7312223b).

    Mutation this catches: the Stop's result of `end_one_shot()` thrown away.
    """
    rec = Recorder()
    cancel = threading.Event()

    def one_shot(service: str, server_dir: Path, **_kw: object) -> docker.AttachedRun:
        rec.calls.append(f"one-shot:{service}")
        cancel.set()
        return docker.AttachedRun(docker.CANCELLED_RETURNCODE, ("downloading maps",))

    def end_one_shot(service: str, server_dir: Path) -> docker.OneShotLeft | None:
        if service == DOWNLOADER and cancel.is_set():
            return docker.OneShotLeft((DOWNLOADER,), "it was still running 30 s after the kill")
        return None

    with pytest.raises(InstallerError) as raised:
        list(
            engine(rec, one_shot=one_shot, end_one_shot=end_one_shot).run(
                InstallOptions(server_dir=tmp_path / "s"), cancel=cancel
            )
        )
    assert isinstance(raised.value, TrueAfterStop), type(raised.value)
    assert not isinstance(raised.value, InstallStopped), raised.value
    assert DOWNLOADER in str(raised.value), raised.value
