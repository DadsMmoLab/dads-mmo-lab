"""T225, seen live: a Stop mid-compile whose build Docker finished in the background anyway.

Sitting B on yulon-win11, 2026-10-05: Stop was pressed during the compile. Two
minutes later `<ref>` still held the old image (the same as its `-rollback`), so
the press let the `-rollback` names go -- and about 13 minutes after the Stop
BuildKit exported a new image and moved the live tag onto it. Nothing said so;
the containers stayed on the old image, now untagged, and the next Start would
have run a build that never started.

The lead's decision: after a cancelled or abandoned compile the `-rollback`
names are KEPT, whatever the ids say at that moment, and a record says a stopped
build may still land. Every Start then compares each live tag with its
`-rollback` name and refuses when one moved; the next Rebuild (or Update, or
Return, which run the same code) puts the tags back and keeps the landed build
for reuse when it is whole and the folder has not changed. Uninstall removes the
names and the record.

Every test drives `rebuild()` against `test_rebuild._Daemon`; "landing" is
`daemon.compiled()` called after the press has ended.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from tests.support_native import Recorder, engine
from tests.test_rebuild import _Daemon, _daemon_for, _refs, _seams_of, a_finished_install
from tests.test_rebuild_parks_a_finished_build import a_parkable_install
from yulon import docker, server_build_presses
from yulon.catalog import native
from yulon.catalog.installer import InstallerError, InstallOptions
from yulon.controller import Controller, StartRefused

LANDED = native.STOPPED_BUILD_LANDED_REFUSAL


def _rollback_names(server_dir: Path) -> list[str]:
    return sorted(ref + native.ROLLBACK_TAG_SUFFIX for ref in _refs(server_dir))


def _stopped_mid_compile(
    rec: Recorder, daemon: _Daemon, server_dir: Path
) -> tuple[list[str], InstallerError]:
    """Stop pressed while the compiler runs: compose is killed before it tags anything."""
    stop = threading.Event()

    def build(server_dir: Path, files: object, **_kw: object) -> docker.AttachedRun:
        rec.calls.append("build")
        stop.set()
        return docker.AttachedRun(docker.CANCELLED_RETURNCODE, ("[42/1838] Building CXX",))

    said: list[str] = []
    with pytest.raises(InstallerError) as raised:
        for line in engine(rec, **{**_seams_of(rec, daemon), "build": build}).rebuild(
            InstallOptions(server_dir=server_dir), cancel=stop
        ):
            said.append(line)
    rec.calls.clear()
    return said, raised.value


def test_a_stop_mid_compile_keeps_the_rollback_names_and_says_why(tmp_path: Path) -> None:
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    _said, failed = _stopped_mid_compile(rec, daemon, server_dir)
    assert sorted(daemon.transient()) == _rollback_names(server_dir), daemon.transient()
    assert all(daemon.names[name] == "before" for name in _rollback_names(server_dir))
    assert all(daemon.names[ref] == "before" for ref in _refs(server_dir)), daemon.names
    assert native.read_stopped_build(server_dir) is not None
    message = str(failed)
    assert native.STOPPED_BUILD_NOTE in message, message
    # Nothing landed: Start runs the build the server has.
    assert engine(rec, **_seams_of(rec, daemon)).start_refusal(server_dir) is None


def test_a_build_that_lands_after_the_stop_refuses_start_until_a_rebuild(tmp_path: Path) -> None:
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    _stopped_mid_compile(rec, daemon, server_dir)
    daemon.compiled()  # BuildKit, thirteen minutes later
    eng = engine(rec, **_seams_of(rec, daemon))
    assert eng.start_refusal(server_dir) == LANDED
    # A Rebuild is the way out, so it is not refused by it.
    assert eng.start_refusal(server_dir, rebuilding=True) is None
    rebuild = server_build_presses.under_server_build(server_build_presses.REBUILD)
    assert f"Press {rebuild}" in LANDED, LANDED


def test_the_controllers_start_compares_the_tags_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Start, Restart, Recreate and PLAY all pass `Controller.refuse_start()` first."""
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    _stopped_mid_compile(rec, daemon, server_dir)
    asked: list[tuple[str, object]] = []

    def image_id(ref: str, *, wsl_distro: str | None = None) -> str | None:
        asked.append((ref, wsl_distro))
        return daemon.image_id(ref)

    monkeypatch.setattr(docker, "image_id", image_id)
    from yulon.catalog.catalog import load_catalog

    controller = Controller(load_catalog().get("wow-wotlk").container_spec(), server_dir)
    controller.refuse_start()  # nothing landed
    assert asked, "Start did not look at the tags"
    daemon.compiled()
    with pytest.raises(StartRefused) as refused:
        controller.refuse_start()
    assert str(refused.value) == LANDED


def test_a_server_with_no_stopped_build_never_asks_docker_at_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def image_id(ref: str, **_kw: object) -> str | None:
        raise AssertionError("Start asked Docker with no stopped build recorded")

    monkeypatch.setattr(docker, "image_id", image_id)
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    assert native.stopped_build_refusal(server_dir, image_id) is None


def test_the_next_rebuild_puts_a_landed_build_back_and_uses_it(tmp_path: Path) -> None:
    rec = Recorder(images=True)
    server_dir = a_parkable_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    _stopped_mid_compile(rec, daemon, server_dir)
    daemon.compiled()
    said = list(
        engine(rec, **_seams_of(rec, daemon)).rebuild(InstallOptions(server_dir=server_dir))
    )
    assert "build" not in rec.calls, rec.calls
    assert [line for line in said if "finished in Docker's background" in line], said
    assert [line for line in said if "so it is used instead of compiling again" in line], said
    assert all(daemon.names[ref] == "after" for ref in _refs(server_dir)), daemon.names
    assert daemon.transient() == [], daemon.transient()
    assert native.read_stopped_build(server_dir) is None
    assert native.read_parked_build(server_dir) is None
    assert engine(rec, **_seams_of(rec, daemon)).start_refusal(server_dir) is None


def test_the_next_rebuild_after_a_stop_that_never_landed_compiles_as_usual(
    tmp_path: Path,
) -> None:
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    _stopped_mid_compile(rec, daemon, server_dir)
    list(engine(rec, **_seams_of(rec, daemon)).rebuild(InstallOptions(server_dir=server_dir)))
    assert "build" in rec.calls, rec.calls
    assert all(daemon.names[ref] == "after" for ref in _refs(server_dir)), daemon.names
    assert daemon.transient() == [], daemon.transient()
    assert native.read_stopped_build(server_dir) is None


def test_a_partly_landed_build_is_put_back_and_not_kept(tmp_path: Path) -> None:
    rec = Recorder(images=True)
    server_dir = a_parkable_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    _stopped_mid_compile(rec, daemon, server_dir)
    daemon.compiled(_refs(server_dir)[:1])
    assert engine(rec, **_seams_of(rec, daemon)).start_refusal(server_dir) == LANDED
    seams = _seams_of(rec, daemon)
    inner = seams["build"]
    assert callable(inner)
    seen: list[dict[str, str | None]] = []

    def build(*args: object, **kw: object) -> object:
        seen.append({ref: daemon.names.get(ref) for ref in _refs(server_dir)})
        return inner(*args, **kw)

    list(engine(rec, **{**seams, "build": build}).rebuild(InstallOptions(server_dir=server_dir)))
    assert seen and set(seen[0].values()) == {"before"}, "the compile ran over a moved tag"
    assert native.read_parked_build(server_dir) is None


def test_a_landed_build_whose_tag_will_not_go_back_refuses_and_keeps_the_record(
    tmp_path: Path,
) -> None:
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    _stopped_mid_compile(rec, daemon, server_dir)
    daemon.compiled()

    def tag_image(src: str, dst: str) -> str:
        if src.endswith(native.ROLLBACK_TAG_SUFFIX):
            return "Error response from daemon: read-only file system"
        return daemon.tag_image(src, dst)

    with pytest.raises(InstallerError) as raised:
        list(
            engine(rec, **_seams_of(rec, daemon, tag_image=tag_image)).rebuild(
                InstallOptions(server_dir=server_dir)
            )
        )
    assert "build" not in rec.calls, rec.calls
    assert "Nothing was compiled" in str(raised.value), raised.value
    assert native.read_stopped_build(server_dir) is not None
    assert engine(rec, **_seams_of(rec, daemon)).start_refusal(server_dir) == LANDED


def test_a_run_abandoned_mid_compile_keeps_the_names_even_when_the_ids_still_match(
    tmp_path: Path,
) -> None:
    """The `BaseException` path, with Docker answering: nothing moved YET."""
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    line_said = "[42/1838] Building CXX object src/server/game/World.cpp.o"

    def build(
        server_dir: Path, files: object, *, sink: object = None, cancel: object = None
    ) -> docker.AttachedRun:
        assert callable(sink)
        sink(line_said)
        return docker.AttachedRun(docker.CANCELLED_RETURNCODE, (line_said,))

    run = engine(rec, **{**_seams_of(rec, daemon), "build": build}).rebuild(
        InstallOptions(server_dir=server_dir)
    )
    for line in run:
        if "Building CXX object" in line:
            break
    else:
        raise AssertionError("the compiler's line never reached the panel")
    run.close()
    assert sorted(daemon.transient()) == _rollback_names(server_dir), daemon.transient()
    assert native.read_stopped_build(server_dir) is not None
    assert engine(rec, **_seams_of(rec, daemon)).start_refusal(server_dir) is None


def test_a_compile_that_failed_still_lets_the_rollback_names_go(tmp_path: Path) -> None:
    """The control: exit 1 is a finished answer, and no build of it can land later."""
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    rec.build_result = docker.AttachedRun(1, ("cc1plus: error",))
    with pytest.raises(InstallerError):
        list(engine(rec, **_seams_of(rec, daemon)).rebuild(InstallOptions(server_dir=server_dir)))
    assert daemon.transient() == [], daemon.transient()
    assert native.read_stopped_build(server_dir) is None
