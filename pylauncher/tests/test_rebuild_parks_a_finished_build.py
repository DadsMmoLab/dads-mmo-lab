"""T224: a finished build that replaced no container is kept, and the next Rebuild may use it.

Seen on yulon-win11, 2026-10-04: an 85-minute Centurion compile finished, Docker
did not answer the recreate, and the restore deleted the new build. The owner's
answers (2026-10-04):

* D1 -- keep (park) the finished build on EVERY exit where the build is complete
  and no container was replaced, Stop included;
* D2 -- Rebuild, Update and Return to the tested pin may each use a kept build,
  on an exact fingerprint match only, and still recreate, wait for ready and keep
  the rollback;
* D3 -- the Server tab shows the kept build, with a "Remove kept build…" press;
* D4 -- no keeping for a server inside a WSL distro, in v1.

Start, Restart, Recreate and Install never reach a kept build.

Every test drives `rebuild()` against `test_rebuild._Daemon`, so which image each
name holds is read off the fake daemon, and the fingerprint is the real
`build_context.fingerprint()` over real files: `a_parkable_install()` adds the
recipe AzerothCore's checkout carries (`apps/docker/Dockerfile`, which the build
overlay names) and one source file to the install `a_finished_install()` makes.
Without the recipe there is no fingerprint, which is the fixture T223's tests use
and therefore their "removed" path.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from tests.support_native import Recorder, engine
from tests.test_rebuild import _Daemon, _daemon_for, _refs, _seams_of, a_finished_install
from tests.test_rebuild_waits_for_docker import SILENT_FOR_THE_WAIT, _Clock, _Probe
from tests.test_stop_as_the_build_finishes import _StopAfterTagging
from yulon import docker, server_build_presses
from yulon.catalog import build_context, native
from yulon.catalog.installer import InstallerError, InstallOptions

RECIPE = "apps/docker/Dockerfile"
SOURCE = "src/server/game/World.cpp"
KEPT = "The build that had just finished is kept"
REMOVED = "The build that had just finished was removed"


def a_parkable_install(rec: Recorder, tmp_path: Path) -> Path:
    server_dir = a_finished_install(rec, tmp_path)
    recipe = server_dir / RECIPE
    recipe.parent.mkdir(parents=True, exist_ok=True)
    recipe.write_text("FROM ubuntu:24.04 AS build\nCOPY . /azerothcore\n", encoding="utf-8")
    source = server_dir / SOURCE
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("void World::Update() {}\n", encoding="utf-8")
    # The fixture's own claim: without a fingerprint every test below is about
    # the "not kept" path, whatever its name says.
    assert build_context.fingerprint(server_dir, refs=_refs(server_dir)) is not None
    return server_dir


def _parked(server_dir: Path) -> list[str]:
    return [ref + native.PARKED_TAG_SUFFIX for ref in _refs(server_dir)]


def _parked_on(daemon: _Daemon, server_dir: Path) -> set[str | None]:
    return {daemon.names.get(name) for name in _parked(server_dir)}


def _on_the_old_build(daemon: _Daemon, server_dir: Path) -> None:
    assert all(daemon.names[ref] == "before" for ref in _refs(server_dir)), daemon.names
    assert set(daemon.containers.values()) == {"before"}, daemon.containers
    assert daemon.transient() == [], daemon.transient()


def _silent_recreate(rec: Recorder, daemon: _Daemon, **overrides: object) -> dict[str, object]:
    """Docker silent through the recreate's three minutes, back for the restore."""
    clock = _Clock()
    return {
        **_seams_of(rec, daemon),
        "docker_ready": _Probe(clock, *SILENT_FOR_THE_WAIT, then=True),
        "monotonic": clock.monotonic,
        "sleep": clock.sleep,
        **overrides,
    }


def _refused(
    rec: Recorder, server_dir: Path, seams: dict[str, object], cancel: threading.Event | None = None
) -> tuple[list[str], InstallerError]:
    said: list[str] = []
    with pytest.raises(InstallerError) as raised:
        for line in engine(rec, **seams).rebuild(
            InstallOptions(server_dir=server_dir), cancel=cancel
        ):
            said.append(line)
    return said, raised.value


# -- Task 4: which exits keep the build ---------------------------------------


def test_a_recreate_docker_never_answers_parks_the_finished_build(tmp_path: Path) -> None:
    rec = Recorder(images=True)
    server_dir = a_parkable_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    _said, failed = _refused(rec, server_dir, _silent_recreate(rec, daemon))
    _on_the_old_build(daemon, server_dir)
    assert _parked_on(daemon, server_dir) == {"after"}, daemon.names
    record = native.read_parked_build(server_dir)
    assert record is not None
    assert record.images == {ref: "after" for ref in _refs(server_dir)}, record
    assert record.fingerprint == build_context.fingerprint(server_dir, refs=_refs(server_dir))
    message = str(failed)
    assert KEPT in message, message
    assert "Start does not use the kept build" in message, message
    assert "the server is still on the build it had before this rebuild" in message, message
    rebuild = server_build_presses.under_server_build(server_build_presses.REBUILD)
    assert f"pressing {rebuild} again uses it instead of compiling" in message, message
    assert REMOVED not in message, message
    # The live tags are back on the build that ran, so Start is not refused.
    assert engine(rec).start_refusal(server_dir) is None


def test_a_stop_during_the_docker_wait_parks_too(tmp_path: Path) -> None:
    rec = Recorder(images=True)
    server_dir = a_parkable_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    clock = _Clock()
    stop = threading.Event()
    seams = {
        **_seams_of(rec, daemon),
        "docker_ready": _Probe(clock, False, False, then=True, on_ask={2: stop}),
        "monotonic": clock.monotonic,
        "sleep": clock.sleep,
    }
    said, failed = _refused(rec, server_dir, seams, cancel=stop)
    _on_the_old_build(daemon, server_dir)
    assert _parked_on(daemon, server_dir) == {"after"}, daemon.names
    assert KEPT in str(failed), failed
    waiting = [line for line in said if "up to 3 minutes for Docker" in line]
    assert len(waiting) == 1, said
    assert "keeps the finished build: a later rebuild uses it" in waiting[0], waiting
    assert "gives up the new build" not in waiting[0], waiting


def test_a_stop_as_the_build_finishes_parks_it(tmp_path: Path) -> None:
    """T225 and T224 together: the tags go back, and the build they had moved to is kept."""
    rec = Recorder(images=True)
    server_dir = a_parkable_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    stop = threading.Event()
    seams = {**_seams_of(rec, daemon), "build": _StopAfterTagging(rec, daemon, stop)}
    _said, failed = _refused(rec, server_dir, seams, cancel=stop)
    _on_the_old_build(daemon, server_dir)
    assert _parked_on(daemon, server_dir) == {"after"}, daemon.names
    message = str(failed)
    assert message.startswith(native.STOPPED_AS_THE_BUILD_FINISHED), message
    assert KEPT in message, message


def test_a_stop_after_one_of_four_images_was_tagged_parks_nothing(tmp_path: Path) -> None:
    rec = Recorder(images=True)
    server_dir = a_parkable_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    stop = threading.Event()
    build = _StopAfterTagging(
        rec, daemon, stop, docker.CANCELLED_RETURNCODE, only=_refs(server_dir)[:1]
    )
    _said, failed = _refused(rec, server_dir, {**_seams_of(rec, daemon), "build": build}, stop)
    _on_the_old_build(daemon, server_dir)
    assert _parked_on(daemon, server_dir) == {None}, daemon.names
    assert native.read_parked_build(server_dir) is None
    message = str(failed)
    assert REMOVED in message and "every image" in message, message


def test_a_build_whose_files_changed_while_it_compiled_is_not_parked(tmp_path: Path) -> None:
    rec = Recorder(images=True)
    server_dir = a_parkable_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)

    def build(server_dir: Path, files: object, **_kw: object) -> docker.AttachedRun:
        rec.calls.append("build")
        (server_dir / SOURCE).write_text("void World::Update() { tick(); }\n", encoding="utf-8")
        daemon.compiled()
        return docker.AttachedRun(0, ("compiled",))

    _said, failed = _refused(rec, server_dir, _silent_recreate(rec, daemon, build=build))
    _on_the_old_build(daemon, server_dir)
    assert _parked_on(daemon, server_dir) == {None}, daemon.names
    assert native.read_parked_build(server_dir) is None
    message = str(failed)
    assert REMOVED in message and "changed while it compiled" in message, message


def test_a_wsl_server_is_not_parked_and_says_why(tmp_path: Path) -> None:
    rec = Recorder(images=True)
    server_dir = a_parkable_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    in_wsl = native.Seams.in_wsl("Ubuntu-24.04")
    seams = _silent_recreate(
        rec, daemon, distro=in_wsl.distro, context_fingerprint=in_wsl.context_fingerprint
    )
    _said, failed = _refused(rec, server_dir, seams)
    _on_the_old_build(daemon, server_dir)
    assert _parked_on(daemon, server_dir) == {None}, daemon.names
    assert native.read_parked_build(server_dir) is None
    message = str(failed)
    assert REMOVED in message and "inside WSL" in message, message


def test_the_wsl_seams_never_take_a_fingerprint(tmp_path: Path) -> None:
    """D4, at the seam: a host walk of `\\\\wsl.localhost` boots the distro (T133)."""
    rec = Recorder(images=True)
    server_dir = a_parkable_install(rec, tmp_path)
    assert native.Seams().context_fingerprint is build_context.fingerprint
    assert native.Seams.in_wsl("Ubuntu-24.04").context_fingerprint(server_dir) is None


def test_a_record_that_cannot_be_written_releases_the_parked_names(tmp_path: Path) -> None:
    rec = Recorder(images=True)
    server_dir = a_parkable_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    # A folder where the record's temporary file goes: the write fails, and only it.
    (server_dir / (native.PARKED_BUILD_FILE + ".yulon-new")).mkdir()
    _said, failed = _refused(rec, server_dir, _silent_recreate(rec, daemon))
    _on_the_old_build(daemon, server_dir)
    assert _parked_on(daemon, server_dir) == {None}, daemon.names
    assert native.read_parked_build(server_dir) is None
    assert "after" not in daemon.names.values(), daemon.names
    message = str(failed)
    assert REMOVED in message and "could not be written" in message, message


def test_parking_again_replaces_the_old_record_before_any_tag_moves(tmp_path: Path) -> None:
    """Write order (plan §1b): forget the old record, tag, read the ids, write the new one."""
    rec = Recorder(images=True)
    server_dir = a_parkable_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    stale = native.ParkedBuild(
        fingerprint="0" * 64,
        images={ref: "sha256:stale" for ref in _refs(server_dir)},
        made_unix=1,
        app="0.0.1",
    )
    record_at_first_park: list[bool] = []

    def build(server_dir: Path, files: object, **_kw: object) -> docker.AttachedRun:
        rec.calls.append("build")
        daemon.compiled()
        # A record that appeared while it compiled: the sweep before the
        # compile cannot have taken it, so only the park's own order can.
        assert native.remember_parked_build(server_dir, stale) == ""
        return docker.AttachedRun(0, ("compiled",))

    def tag_image(src: str, dst: str) -> str:
        if dst.endswith(native.PARKED_TAG_SUFFIX) and not record_at_first_park:
            record_at_first_park.append((server_dir / native.PARKED_BUILD_FILE).exists())
        return daemon.tag_image(src, dst)

    _refused(rec, server_dir, _silent_recreate(rec, daemon, build=build, tag_image=tag_image))
    assert record_at_first_park == [False], "the old record was still there as a tag moved"
    record = native.read_parked_build(server_dir)
    assert record is not None and set(record.images.values()) == {"after"}, record
