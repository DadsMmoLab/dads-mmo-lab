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
* a build that prints NOTHING is told so after `BUILD_QUIET_NOTICE_SECONDS`,
  and told it looks stalled -- how to check, what to do -- after
  `BUILD_STALLED_SECONDS`. Nothing is ever ended for silence. After a step ends, a working
  build prints its next step within a second (measured on two real logs), and
  the longest quiet stretch in four real build logs was about a minute.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from tests.support_native import Recorder, engine, install
from yulon import docker, server_build_presses
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
    # Docker Desktop's WSL 2 engine has no memory slider: Windows sizes it (review, 2026-10-04).
    assert ".wslconfig" in said and "no memory slider" in said, said
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
#
# Lead ruling on review (2026-10-04): never stop a build for silence alone. A
# slow export can be quiet for long, and on Windows `proc.terminate()` would end
# only docker.exe while docker-compose.exe and docker-buildx.exe ran on. So a
# silent build is TOLD -- at 10 minutes, and as looking stalled at 30 with how
# to check and what to do -- and nothing is ended.


def test_the_quiet_thresholds_are_generous_against_what_was_measured() -> None:
    """Ten minutes to tell, thirty to call it stalled: the longest real quiet stretch was ~1 min."""
    assert native.BUILD_QUIET_NOTICE_SECONDS == 10 * 60
    assert native.BUILD_STALLED_SECONDS == 30 * 60


def test_a_35_minute_silent_build_gets_both_notices_and_is_never_ended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scaled: notice at 0.2 s, stalled at 0.5 s, silence for 0.7 s (35 of 30 minutes)."""
    monkeypatch.setattr(native, "BUILD_QUIET_NOTICE_SECONDS", 0.2)
    monkeypatch.setattr(native, "BUILD_STALLED_SECONDS", 0.5)
    ended: list[int] = []
    monkeypatch.setattr(native.runner, "end_streams_started_on", ended.append)
    cancel = threading.Event()

    def silent(
        server_dir: Path, files: object, *, sink: object = None, cancel: object = None
    ) -> docker.AttachedRun:
        if callable(sink):
            sink("#12 DONE 2113.4s")
        time.sleep(0.7)
        if callable(sink):
            sink("#13 [stage-1 3/5] COPY --from=builder /opt/trinitycore /opt/trinitycore")
        return docker.AttachedRun(0, ("built",))

    rec = Recorder(images=False)
    said = list(
        engine(rec, build=silent).run(InstallOptions(server_dir=tmp_path / "wow"), cancel=cancel)
    )
    assert ended == [], "nothing was terminated"
    assert not cancel.is_set(), "the user's Stop was not pulled on their behalf"
    quiet = [line for line in said if line == native.build_quiet_notice()]
    stalled = [line for line in said if line == native.build_stalled_notice()]
    assert len(quiet) == 1 and len(stalled) == 1, said
    assert said.index(quiet[0]) < said.index(stalled[0])
    assert "The build finished." in said, "the build ran to its own end"


def test_the_stalled_notice_says_how_to_check_and_what_to_do_and_stops_nothing() -> None:
    said = native.build_stalled_notice()
    assert "looks stalled" in said
    assert "docker info" in said and "Task Manager" in said and "top" in said
    assert "restart Docker Desktop" in said and "docker service" in said
    assert "build cache" in said, "pressing again resumes"
    assert server_build_presses.under_server_build(server_build_presses.REBUILD) in said
    assert "will not stop it" in said
    assert "stopped" not in native.build_quiet_notice().replace("not stopped", "")


def test_a_build_that_keeps_printing_is_never_told_however_long_it_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(native, "BUILD_QUIET_NOTICE_SECONDS", 0.3)
    monkeypatch.setattr(native, "BUILD_STALLED_SECONDS", 0.5)

    def chatty(
        server_dir: Path, files: object, *, sink: object = None, cancel: object = None
    ) -> docker.AttachedRun:
        for step in range(30):  # 1.5 s in all, three times the stalled mark
            if callable(sink):
                sink(f"#12 {step} [ {step}%] Building CXX object")
            time.sleep(0.05)
        return docker.AttachedRun(0, ("built",))

    rec = Recorder(images=False)
    said = install(rec, tmp_path / "wow", build=chatty)
    assert native.build_quiet_notice() not in said
    assert native.build_stalled_notice() not in said
    assert "The build finished." in said


def test_only_the_build_stage_is_watched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An import or an extraction can be quiet for long and legitimately; only the build is told."""
    watched: dict[str, object] = {}
    real = native.StagedInstaller._pump

    def spy(self: native.StagedInstaller, call: object, **kwargs: object) -> object:
        watched[str(kwargs["stage"])] = kwargs.get("watch")
        return real(self, call, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(native.StagedInstaller, "_pump", spy)
    install(Recorder(images=False), tmp_path / "wow")
    assert "build" in watched and len(watched) > 1, watched
    assert isinstance(watched.pop("build"), native.QuietWatch)
    assert all(watch is None for watch in watched.values()), watched
