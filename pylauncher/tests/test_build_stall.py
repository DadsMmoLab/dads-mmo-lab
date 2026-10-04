"""T202: a build that loses Docker's builder, or stops answering, is told so in plain words.

Found by the T179 live check on a Windows test VM (Docker Desktop 29.7.2,
2026-10-03, the T179 live gate notes, phase A7). The
Centurion compile finished (`#12 DONE 2113.4s`), and then the build printed
nothing for 53 minutes, with Docker's VM idle, before it ended on its own:

    failed to receive status: rpc error: code = Unavailable desc = error reading from server: EOF

The player was shown "the build failed (exit 1). Its last words were:" and the
raw tail -- nothing that said what had happened or what to do. Phase A10 met
the same EOF mid-compile at 99%, and the build passed once Docker had more
memory.

Two halves, both driven through the real engine with only docker doubled:

* the EOF -- `rpc error: code = Unavailable`, the build's client losing the
  builder -- is named in a sentence that says what happened, the likely cause
  and how to check it, and that running it again resumes from the cache;
* a build that prints NOTHING is told so after `BUILD_QUIET_NOTICE_SECONDS`
  and stopped after `BUILD_QUIET_LIMIT_SECONDS`. After a step ends, a working
  build prints its next step within a second (measured on two real logs), and
  the longest quiet stretch in four real build logs was about a minute.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from tests.support_native import Recorder, engine, install
from yulon import docker
from yulon.catalog import native
from yulon.catalog.installer import InstallerError, InstallOptions

A7_TAIL = (
    '#12 2106.2 -- Set non-toolchain portion of runtime path of "/opt/trinitycore/bin/'
    'mmaps_generator" to "/opt/trinitycore/lib"',
    "#12 DONE 2113.4s",
    "failed to receive status: rpc error: code = Unavailable desc = error reading from server: EOF",
    "",
)
"""The live run log's last lines, verbatim (phase A7)."""


# -- the EOF -------------------------------------------------------------------


def test_a_build_that_loses_docker_s_builder_says_so_and_what_to_do(tmp_path: Path) -> None:
    rec = Recorder(images=False, build_result=docker.AttachedRun(1, A7_TAIL))
    with pytest.raises(InstallerError) as caught:
        install(rec, tmp_path / "wow")
    said = str(caught.value)
    assert said.startswith("the build failed (exit 1)"), said
    assert "lost its connection to Docker's builder" in said, said
    assert "memory" in said and "docker info" in said, "the likely cause, and how to check it"
    assert "build cache" in said and "again" in said, "running it again resumes"
    assert "error reading from server: EOF" in said, "Docker's own words stay, for a search"


def test_the_eof_is_named_even_when_it_closes_a_long_tail() -> None:
    """The detector reads the whole tail, not just its very last line."""
    tail = (*A7_TAIL, "", "")
    assert docker.builder_connection_lost(tail)


def test_an_ordinary_compile_failure_is_not_called_a_lost_builder(tmp_path: Path) -> None:
    tail = (
        "#17 12.3 src/Foo.cpp:3:1: error: expected ';'",
        '#17 ERROR: process "/bin/sh -c make" did not complete successfully: exit code: 2',
        "failed to solve: process did not complete successfully",
    )
    rec = Recorder(images=False, build_result=docker.AttachedRun(1, tail))
    with pytest.raises(InstallerError) as caught:
        install(rec, tmp_path / "wow")
    assert "Docker's builder" not in str(caught.value)
    assert not docker.builder_connection_lost(tail)


# -- a build that prints nothing -------------------------------------------------


def test_the_quiet_limits_are_generous_against_what_was_measured() -> None:
    """Ten minutes to tell, thirty to stop: the longest real quiet stretch was about a minute."""
    assert native.BUILD_QUIET_NOTICE_SECONDS == 10 * 60
    assert native.BUILD_QUIET_LIMIT_SECONDS == 30 * 60


class _SilentBuild:
    """A build seam that prints one line and then nothing, until its client is ended.

    Ending the client is `runner.end_streams_started_on(<the worker's ident>)`,
    which this double stands in for: the real one terminates the `docker compose`
    child a thread started, and the blocked read returns.
    """

    def __init__(self) -> None:
        self.ended = threading.Event()
        self.ident: int | None = None
        self.ended_ident: int | None = None

    def build(
        self, server_dir: Path, files: object, *, sink: object = None, cancel: object = None
    ) -> docker.AttachedRun:
        self.ident = threading.get_ident()
        if callable(sink):
            sink("#12 DONE 2113.4s")
        if not self.ended.wait(timeout=10):
            return docker.AttachedRun(0, ("never ended",))
        return docker.AttachedRun(143, ("#12 DONE 2113.4s",))

    def end_streams_started_on(self, ident: int) -> int:
        self.ended_ident = ident
        self.ended.set()
        return 1


def test_a_build_that_prints_nothing_is_told_then_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    silent = _SilentBuild()
    monkeypatch.setattr(native, "BUILD_QUIET_NOTICE_SECONDS", 0.2)
    monkeypatch.setattr(native, "BUILD_QUIET_LIMIT_SECONDS", 0.6)
    monkeypatch.setattr(native.runner, "end_streams_started_on", silent.end_streams_started_on)
    rec = Recorder(images=False)
    said: list[str] = []
    started = time.monotonic()
    with pytest.raises(InstallerError) as caught:
        for line in engine(rec, build=silent.build).run(
            InstallOptions(server_dir=tmp_path / "wow")
        ):
            said.append(line)
    assert time.monotonic() - started < 8, "stopped by the watchdog, not by the double's timeout"
    assert silent.ended_ident == silent.ident, "the build's OWN client was ended"
    notices = [line for line in said if "has printed nothing" in line]
    assert len(notices) == 1, said
    stopped = str(caught.value)
    assert "printed nothing" in stopped and "stopped" in stopped, stopped
    assert (
        "restart docker" in stopped.lower()
    ), "a hung builder is not cleared by pressing again alone"
    assert "build cache" in stopped, stopped
    assert "one-shot:ac-db-import" not in rec.calls, "nothing after the build ran"


def test_a_build_that_keeps_printing_is_never_stopped_however_long_it_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(native, "BUILD_QUIET_NOTICE_SECONDS", 0.3)
    monkeypatch.setattr(native, "BUILD_QUIET_LIMIT_SECONDS", 0.5)
    ended: list[int] = []
    monkeypatch.setattr(native.runner, "end_streams_started_on", ended.append)

    def chatty(
        server_dir: Path, files: object, *, sink: object = None, cancel: object = None
    ) -> docker.AttachedRun:
        for step in range(30):  # 1.5 s in all, three times the limit
            if callable(sink):
                sink(f"#12 {step} [ {step}%] Building CXX object")
            time.sleep(0.05)
        return docker.AttachedRun(0, ("built",))

    rec = Recorder(images=False)
    said = install(rec, tmp_path / "wow", build=chatty)
    assert ended == []
    assert not [line for line in said if "has printed nothing" in line]
    assert "The build finished." in said
