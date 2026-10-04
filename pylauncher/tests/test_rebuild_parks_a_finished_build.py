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
import yaml

from tests.support_native import ENTRY, Recorder, engine
from tests.test_rebuild import (  # noqa: F401 - `cmangos_gate` is a fixture
    CM_ENTRY,
    _answers,
    _Daemon,
    _daemon_for,
    _refs,
    _seams_of,
    _stale,
    a_finished_cmangos_install,
    a_finished_install,
    cm_engine,
    cmangos_gate,
)
from tests.test_rebuild_waits_for_docker import SILENT_FOR_THE_WAIT, _Clock, _Probe
from tests.test_stop_as_the_build_finishes import _StopAfterTagging
from yulon import docker, server_build_presses
from yulon.catalog import build_context, composegen, native
from yulon.catalog.installer import InstallerError, InstallOptions, rebuild_confirmation
from yulon.controller_wow_wotlk import maintenance

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


# -- Task 5: the next press uses it, or removes it before compiling -----------

REUSED = "so it is used instead of compiling again"
CHANGED = "the files in this folder have changed since it was made"


def _parked_once(tmp_path: Path) -> tuple[Recorder, _Daemon, Path]:
    """One press that kept its build (Docker silent through the recreate)."""
    rec = Recorder(images=True)
    server_dir = a_parkable_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    _refused(rec, server_dir, _silent_recreate(rec, daemon))
    assert _parked_on(daemon, server_dir) == {"after"}, daemon.names
    assert native.read_parked_build(server_dir) is not None
    rec.calls.clear()
    rec.ready_specs.clear()
    return rec, daemon, server_dir


def test_the_next_rebuild_on_an_unchanged_folder_uses_the_parked_build_and_compiles_nothing(
    tmp_path: Path,
) -> None:
    rec, daemon, server_dir = _parked_once(tmp_path)
    made = native.read_parked_build(server_dir)
    assert made is not None
    said = list(
        engine(rec, **_seams_of(rec, daemon)).rebuild(InstallOptions(server_dir=server_dir))
    )
    assert "build" not in rec.calls, rec.calls
    assert daemon.builds == 1, "one compile across both presses"
    assert all(daemon.names[ref] == "after" for ref in _refs(server_dir)), daemon.names
    recreated = [ref for ref in _refs(server_dir) if ref not in daemon.pinned]
    assert recreated and {daemon.containers[ref] for ref in recreated} == {"after"}
    assert rec.ready_specs, "the ready wait ran, as after a compile"
    assert _parked_on(daemon, server_dir) == {None}, daemon.names
    assert daemon.transient() == [], daemon.transient()
    assert native.read_parked_build(server_dir) is None
    told = [line for line in said if REUSED in line]
    assert len(told) == 1 and made.when() in told[0], said
    assert "the build you have now is kept to put back" in told[0], told
    assert said[-1].endswith(f"was rebuilt and is running in {server_dir}"), said


def test_the_opening_note_mentions_the_kept_build_only_when_one_is_recorded(
    tmp_path: Path,
) -> None:
    rec, daemon, server_dir = _parked_once(tmp_path)
    first = list(
        engine(rec, **_seams_of(rec, daemon)).rebuild(InstallOptions(server_dir=server_dir))
    )
    second = list(
        engine(rec, **_seams_of(rec, daemon)).rebuild(InstallOptions(server_dir=server_dir))
    )
    notes = [
        next(line for line in run if line.startswith("You can stop this at any time"))
        for run in (first, second)
    ]
    kept = "if the folder has not changed since the kept build was made, uses that build"
    assert kept in notes[0], notes[0]
    assert kept not in notes[1], notes[1]
    assert "three things" in notes[0] and "three things" in notes[1], notes


def test_the_next_rebuild_after_an_edit_removes_the_parked_build_before_compiling(
    tmp_path: Path,
) -> None:
    rec, daemon, server_dir = _parked_once(tmp_path)
    (server_dir / SOURCE).write_text("void World::Update() { tick(); }\n", encoding="utf-8")
    log: list[str] = []
    seams = _seams_of(rec, daemon)
    inner_build = seams["build"]
    assert callable(inner_build)

    def build(*args: object, **kw: object) -> object:
        log.append("build")
        return inner_build(*args, **kw)

    def remove_image(ref: str, force: bool = False) -> str:
        log.append(f"rmi:{ref}")
        return daemon.remove_image(ref, force)

    said = list(
        engine(rec, **{**seams, "build": build, "remove_image": remove_image}).rebuild(
            InstallOptions(server_dir=server_dir)
        )
    )
    first_parked = min(i for i, entry in enumerate(log) if entry.endswith(native.PARKED_TAG_SUFFIX))
    assert first_parked < log.index("build"), log
    assert all(daemon.names[ref] == "after-2" for ref in _refs(server_dir)), daemon.names
    assert _parked_on(daemon, server_dir) == {None}, daemon.names
    assert native.read_parked_build(server_dir) is None
    told = [line for line in said if CHANGED in line]
    assert len(told) == 1 and "it was removed and the server is compiled again" in told[0], said


def test_a_parked_name_moved_by_someone_else_is_not_used(tmp_path: Path) -> None:
    rec, daemon, server_dir = _parked_once(tmp_path)
    daemon.names[_parked(server_dir)[0]] = "somebody-elses"
    said = list(
        engine(rec, **_seams_of(rec, daemon)).rebuild(InstallOptions(server_dir=server_dir))
    )
    assert "build" in rec.calls, rec.calls
    assert all(daemon.names[ref] == "after-2" for ref in _refs(server_dir)), daemon.names
    assert _parked_on(daemon, server_dir) == {None}, daemon.names
    assert native.read_parked_build(server_dir) is None
    assert not [line for line in said if REUSED in line], said
    assert [line for line in said if "no longer on Docker under the names" in line], said


def test_parked_names_without_a_record_are_swept_and_the_server_compiled(tmp_path: Path) -> None:
    rec, daemon, server_dir = _parked_once(tmp_path)
    (server_dir / native.PARKED_BUILD_FILE).unlink()
    list(engine(rec, **_seams_of(rec, daemon)).rebuild(InstallOptions(server_dir=server_dir)))
    assert "build" in rec.calls, rec.calls
    assert _parked_on(daemon, server_dir) == {None}, daemon.names
    assert all(daemon.names[ref] == "after-2" for ref in _refs(server_dir)), daemon.names


def test_a_reused_build_that_does_not_come_up_is_rolled_back_and_not_parked_again(
    tmp_path: Path,
) -> None:
    rec, daemon, server_dir = _parked_once(tmp_path)
    with pytest.raises(InstallerError) as raised:
        list(
            engine(rec, **_seams_of(rec, daemon, wait_ready=_answers(False, True))).rebuild(
                InstallOptions(server_dir=server_dir)
            )
        )
    assert "build" not in rec.calls, rec.calls
    assert "put back and is running again" in str(raised.value), raised.value
    assert all(daemon.names[ref] == "before" for ref in _refs(server_dir)), daemon.names
    assert _parked_on(daemon, server_dir) == {None}, daemon.names
    assert native.read_parked_build(server_dir) is None
    assert daemon.transient() == [], daemon.transient()


def test_a_reused_build_whose_recreate_docker_never_answers_is_parked_again(
    tmp_path: Path,
) -> None:
    rec, daemon, server_dir = _parked_once(tmp_path)
    made = native.read_parked_build(server_dir)
    assert made is not None
    _said, failed = _refused(rec, server_dir, _silent_recreate(rec, daemon))
    assert "build" not in rec.calls, rec.calls
    _on_the_old_build(daemon, server_dir)
    assert _parked_on(daemon, server_dir) == {"after"}, daemon.names
    again = native.read_parked_build(server_dir)
    assert again is not None and again.made_unix == made.made_unix, (made, again)
    assert again.fingerprint == made.fingerprint
    assert KEPT in str(failed), failed


def test_a_kept_build_that_cannot_be_put_on_the_tags_stays_kept(tmp_path: Path) -> None:
    """Docker refuses the second of four tags: the first goes back, and the kept build stays."""
    rec, daemon, server_dir = _parked_once(tmp_path)
    moved: list[str] = []

    def tag_image(src: str, dst: str) -> str:
        if src.endswith(native.PARKED_TAG_SUFFIX):
            moved.append(dst)
            if len(moved) == 2:
                return "Error response from daemon: read-only file system"
        return daemon.tag_image(src, dst)

    with pytest.raises(InstallerError) as raised:
        list(
            engine(rec, **_seams_of(rec, daemon, tag_image=tag_image)).rebuild(
                InstallOptions(server_dir=server_dir)
            )
        )
    assert "build" not in rec.calls, rec.calls
    _on_the_old_build(daemon, server_dir)
    assert _parked_on(daemon, server_dir) == {"after"}, daemon.names
    assert native.read_parked_build(server_dir) is not None
    message = str(raised.value)
    assert "The kept build is still kept" in message, message
    assert REMOVED not in message, message


# -- D2: Update and Return reach `rebuild()`'s build stage too ----------------

OLD = "a" * 40
NEW = "b" * 40


def _on_old_with_somewhere_to_go(rec: Recorder, server_dir: Path) -> None:
    for source in ENTRY.emulator.sources:
        rec.heads[server_dir / source.dest] = OLD
        rec.upstream[server_dir / source.dest] = NEW
    rec.clones.clear()
    rec.calls.clear()


def test_update_to_latest_onto_the_same_files_reuses_the_parked_build(tmp_path: Path) -> None:
    """The fake's update moves the commit and no file: the folder fingerprints the same."""
    rec, daemon, server_dir = _parked_once(tmp_path)
    _on_old_with_somewhere_to_go(rec, server_dir)
    said = list(
        engine(rec, **_seams_of(rec, daemon)).update_to_latest(
            InstallOptions(server_dir=server_dir)
        )
    )
    assert rec.clones, "the update moved its sources"
    assert "build" not in rec.calls, rec.calls
    assert [line for line in said if REUSED in line], said
    assert all(daemon.names[ref] == "after" for ref in _refs(server_dir)), daemon.names
    assert native.read_parked_build(server_dir) is None


def test_update_to_latest_onto_new_sources_removes_it(tmp_path: Path) -> None:
    rec, daemon, server_dir = _parked_once(tmp_path)
    _on_old_with_somewhere_to_go(rec, server_dir)

    def new_code(dest: Path) -> None:
        (dest / "NEWS.md").write_text("upstream moved on\n", encoding="utf-8")

    rec.on_clone = new_code
    said = list(
        engine(rec, **_seams_of(rec, daemon)).update_to_latest(
            InstallOptions(server_dir=server_dir)
        )
    )
    assert "build" in rec.calls, rec.calls
    assert [line for line in said if CHANGED in line], said
    assert all(daemon.names[ref] == "after-2" for ref in _refs(server_dir)), daemon.names
    assert _parked_on(daemon, server_dir) == {None}, daemon.names
    assert native.read_parked_build(server_dir) is None


# -- Start, Restart, Recreate and Install never reach a kept build ------------


def _compose_up(daemon: _Daemon, server_dir: Path) -> None:
    """`docker compose up` as compose does it: each service's `image:` from the files on disk.

    Base plus override, the two compose loads on its own (the build overlay is
    passed only by `build_staged()`). Start, Restart and Recreate are all this
    command (`docker.start_staged()`; Recreate is stop, remove, start).
    """
    images: dict[str, str] = {}
    for name in (composegen.BASE_FILE, composegen.OVERRIDE_FILE):
        loaded = yaml.safe_load((server_dir / name).read_text(encoding="utf-8")) or {}
        for service, body in (loaded.get("services") or {}).items():
            if isinstance(body, dict) and "image" in body:
                images[service] = body["image"]
    for image in images.values():
        if image in daemon.names and image in daemon.live:
            daemon.containers[image] = daemon.names[image]


def test_start_restart_recreate_and_install_never_reach_a_kept_build(tmp_path: Path) -> None:
    rec, daemon, server_dir = _parked_once(tmp_path)
    for name in (composegen.BASE_FILE, composegen.OVERRIDE_FILE, composegen.BUILD_FILE):
        text = (server_dir / name).read_text(encoding="utf-8")
        assert native.PARKED_TAG_SUFFIX not in text, name
    daemon.containers = {}
    _compose_up(daemon, server_dir)
    assert daemon.containers and set(daemon.containers.values()) == {"before"}, daemon.containers
    assert set(daemon.containers) <= set(_refs(server_dir)), daemon.containers
    # The argv those presses issue name services, never an image.
    spec = ENTRY.container_spec()
    for argv in (docker.staged_up_argv(spec), docker.recreate_argv(spec)):
        assert not [word for word in argv if native.PARKED_TAG_SUFFIX in word], argv
    # Install (a resume of a finished install): no question about a kept name,
    # no tag moved, no compile.
    asked: list[str] = []

    def image_id(ref: str) -> str | None:
        asked.append(ref)
        return daemon.image_id(ref)

    def tag_image(src: str, dst: str) -> str:
        asked.append(f"{src}->{dst}")
        return daemon.tag_image(src, dst)

    list(
        engine(rec, **_seams_of(rec, daemon, image_id=image_id, tag_image=tag_image)).run(
            InstallOptions(server_dir=server_dir)
        )
    )
    assert "build" not in rec.calls, rec.calls
    assert not [entry for entry in asked if native.PARKED_TAG_SUFFIX in entry], asked
    assert all(daemon.names[ref] == "before" for ref in _refs(server_dir)), daemon.names
    assert _parked_on(daemon, server_dir) == {"after"}, daemon.names
    assert native.read_parked_build(server_dir) is not None


# -- D3 and the confirmation: what the player is shown -----------------------


def test_the_confirmation_mentions_a_kept_build_with_its_date_only_when_recorded(
    tmp_path: Path,
) -> None:
    rec, daemon, server_dir = _parked_once(tmp_path)
    made = native.read_parked_build(server_dir)
    assert made is not None
    asked = rebuild_confirmation(ENTRY, server_dir)
    assert f"A finished build from {made.when()} is kept from an earlier rebuild" in asked, asked
    assert "uses it instead of compiling" in asked, asked
    assert "it is removed first and the server is compiled as usual" in asked, asked
    # A server inside WSL keeps none (D4), and its tab does not read the record.
    assert "is kept from an earlier rebuild" not in rebuild_confirmation(
        ENTRY, server_dir, kept_build=False
    )
    list(engine(rec, **_seams_of(rec, daemon)).rebuild(InstallOptions(server_dir=server_dir)))
    assert "is kept from an earlier rebuild" not in rebuild_confirmation(ENTRY, server_dir)


def test_the_server_tab_note_reads_the_record_and_nothing_else(tmp_path: Path) -> None:
    rec, daemon, server_dir = _parked_once(tmp_path)
    made = native.read_parked_build(server_dir)
    assert made is not None
    note = native.parked_build_note(server_dir)
    assert note is not None and made.when() in note, note
    assert "Start does not use it" in note and native.REMOVE_KEPT_BUILD_LABEL in note, note
    (server_dir / native.PARKED_BUILD_FILE).write_text("{not json", encoding="utf-8")
    assert native.parked_build_note(server_dir) is None


def test_remove_kept_build_releases_the_names_and_forgets_the_record(tmp_path: Path) -> None:
    rec, daemon, server_dir = _parked_once(tmp_path)
    said = engine(rec, **_seams_of(rec, daemon)).remove_kept_build(
        InstallOptions(server_dir=server_dir)
    )
    assert _parked_on(daemon, server_dir) == {None}, daemon.names
    assert "after" not in daemon.names.values(), daemon.names
    assert native.read_parked_build(server_dir) is None
    assert all(daemon.names[ref] == "before" for ref in _refs(server_dir)), daemon.names
    assert said.startswith("The kept build was removed"), said


def test_remove_kept_build_says_which_names_docker_kept(tmp_path: Path) -> None:
    rec, daemon, server_dir = _parked_once(tmp_path)
    stuck = _parked(server_dir)[0]

    def remove_image(ref: str, force: bool = False) -> str:
        if ref == stuck:
            return "Error response from daemon: read-only file system"
        return daemon.remove_image(ref, force)

    said = engine(rec, **_seams_of(rec, daemon, remove_image=remove_image)).remove_kept_build(
        InstallOptions(server_dir=server_dir)
    )
    assert stuck in said and "read-only" not in said, said
    assert "was not removed" in said, said
    assert native.read_parked_build(server_dir) is not None, "the record went, the name stayed"


def test_the_kept_build_route_is_offered_except_inside_wsl(tmp_path: Path) -> None:
    from yulon import install_wiring

    route = install_wiring.kept_build_for_app(ENTRY, tmp_path)
    assert route is not None
    assert route.check() is None, "no record, no note"
    assert install_wiring.kept_build_for_app(ENTRY, tmp_path, wsl_distro="Ubuntu") is None


def test_kept_names_that_do_not_hold_the_build_once_tagged_are_not_recorded(
    tmp_path: Path,
) -> None:
    """The ids are read back after the tags: a record must never name what its names do not hold.

    Docker answers the tag and the name still holds another image (another
    client moved it in between); the ids it would record are not the build.
    """
    rec = Recorder(images=True)
    server_dir = a_parkable_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    stale = _parked(server_dir)[0]

    def tag_image(src: str, dst: str) -> str:
        said = daemon.tag_image(src, dst)
        if dst == stale:
            daemon.names[dst] = "somebody-elses"
        return said

    _said, failed = _refused(rec, server_dir, _silent_recreate(rec, daemon, tag_image=tag_image))
    _on_the_old_build(daemon, server_dir)
    assert native.read_parked_build(server_dir) is None
    assert _parked_on(daemon, server_dir) == {None}, daemon.names
    assert REMOVED in str(failed) and "did not hold it" in str(failed), failed


@pytest.mark.usefixtures("cmangos_gate")
def test_a_rebuild_that_rewrote_its_recipe_keeps_what_it_compiled_and_the_next_one_uses_it(
    tmp_path: Path,
) -> None:
    """F1 is of the folder the compile read, not of the one the failure path put back.

    The CMaNGOS family writes its recipe again before compiling, and a press that
    replaced no container puts the recipe it found back (T8). The kept build was
    compiled from the NEW recipe, which the next press renders again, so that is
    what its record must name -- and the folder did not change while it compiled.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_cmangos_install(rec, tmp_path)
    _stale(server_dir)
    refs = composegen.built_image_refs(CM_ENTRY, server_dir, platform_id=lambda: "linux")
    daemon = _Daemon(refs, frozenset())
    clock = _Clock()
    seams = {
        **_seams_of(rec, daemon),
        "docker_ready": _Probe(clock, *SILENT_FOR_THE_WAIT, then=True),
        "monotonic": clock.monotonic,
        "sleep": clock.sleep,
    }
    with pytest.raises(InstallerError) as raised:
        list(cm_engine(rec, **seams).rebuild(InstallOptions(server_dir=server_dir)))
    assert KEPT in str(raised.value), raised.value
    assert {daemon.names.get(ref + native.PARKED_TAG_SUFFIX) for ref in refs} == {"after"}
    assert all(daemon.names[ref] == "before" for ref in refs), daemon.names
    rec.calls.clear()
    said = list(
        cm_engine(rec, **_seams_of(rec, daemon)).rebuild(InstallOptions(server_dir=server_dir))
    )
    assert "build" not in rec.calls, rec.calls
    assert [line for line in said if REUSED in line], said
    assert all(daemon.names[ref] == "after" for ref in refs), daemon.names


DOCKER_DOWN = (
    'error during connect: Get "http://%2F%2F.%2Fpipe%2FdockerDesktopLinuxEngine/v1.47/images/'
    'json": open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.'
)


def test_remove_kept_build_keeps_the_record_when_docker_does_not_answer(tmp_path: Path) -> None:
    """Codex adversarial review: an unanswered Docker is not "no names left"."""
    rec, daemon, server_dir = _parked_once(tmp_path)

    def silent(ref: str, force: bool = False) -> str:
        return DOCKER_DOWN

    said = engine(
        rec, **_seams_of(rec, daemon, image_id=lambda ref: None, remove_image=silent)
    ).remove_kept_build(InstallOptions(server_dir=server_dir))
    assert native.read_parked_build(server_dir) is not None, "the record went with the names kept"
    assert _parked_on(daemon, server_dir) == {"after"}, daemon.names
    assert "was not removed" in said, said
    assert not said.startswith("The kept build was removed"), said


def test_a_mismatched_kept_build_keeps_its_record_while_docker_keeps_its_names(
    tmp_path: Path,
) -> None:
    """The sweep before a compile: names Docker would not remove keep the record that names them."""
    rec, daemon, server_dir = _parked_once(tmp_path)
    (server_dir / SOURCE).write_text("void World::Update() { tick(); }\n", encoding="utf-8")

    def remove_image(ref: str, force: bool = False) -> str:
        if ref.endswith(native.PARKED_TAG_SUFFIX):
            return DOCKER_DOWN
        return daemon.remove_image(ref, force)

    list(
        engine(rec, **_seams_of(rec, daemon, remove_image=remove_image)).rebuild(
            InstallOptions(server_dir=server_dir)
        )
    )
    assert _parked_on(daemon, server_dir) == {"after"}, daemon.names
    assert native.read_parked_build(server_dir) is not None


# -- WotLK in normal use: the upstream .dockerignore admits backups and .git ---

UPSTREAM_DOCKERIGNORE = (
    Path(__file__).resolve().parent / "data" / "azerothcore-wotlk-7f12e89e" / "dockerignore"
)
"""The root `.dockerignore` of mod-playerbots/azerothcore-wotlk, byte for byte, at commit
7f12e89ee5f467a50e62eba1d525eac7dc953d03 (the catalog's pin for wow-wotlk, read 2026-10-05).
WotLK renders none of its own, so this is what Docker filters the server folder with."""


def test_on_wotlk_a_new_backup_in_the_server_folder_changes_the_fingerprint(
    tmp_path: Path,
) -> None:
    """Cold review, lead's v1 decision: fail-safe, and pinned so the behaviour is visible.

    Upstream's `.dockerignore` does not leave out `.git`, a module's own `.git`
    or the Maintenance backups (`maintenance.backups_dir()`, which T217's update
    copy shares), so each changes the fingerprint and a kept build is not used
    after it -- though the Dockerfile copies none of them. A follow-up decides
    what WotLK may leave out; this records what v1 does.
    """
    rec = Recorder(images=True)
    server_dir = a_parkable_install(rec, tmp_path)
    (server_dir / ".dockerignore").write_bytes(UPSTREAM_DOCKERIGNORE.read_bytes())
    assert build_context.parse_dockerignore(UPSTREAM_DOCKERIGNORE.read_text("utf-8")) is not None
    refs = _refs(server_dir)
    before = build_context.fingerprint(server_dir, refs=refs)
    assert before is not None, "upstream's .dockerignore must parse, or nothing is ever kept"
    backups = maintenance.backups_dir(server_dir)
    backups.mkdir(parents=True, exist_ok=True)
    (backups / "20261005_010000_acore_characters.sql").write_text("-- dump\n", encoding="utf-8")
    after_backup = build_context.fingerprint(server_dir, refs=refs)
    assert after_backup is not None and after_backup != before
    (server_dir / ".git" / "FETCH_HEAD").write_text("c1a9220 branch\n", encoding="utf-8")
    assert build_context.fingerprint(server_dir, refs=refs) not in (before, after_backup)
    # And the excluded tree really is left out: a build folder changes nothing.
    again = build_context.fingerprint(server_dir, refs=refs)
    (server_dir / "build").mkdir()
    (server_dir / "build" / "CMakeCache.txt").write_text("x\n", encoding="utf-8")
    assert build_context.fingerprint(server_dir, refs=refs) == again


def test_a_kept_build_refused_part_way_with_docker_silent_about_the_tags_still_puts_them_back(
    tmp_path: Path,
) -> None:
    """Putting the kept build on the tags is a run that tags: an unanswered id counts as moved."""
    rec, daemon, server_dir = _parked_once(tmp_path)
    live = set(_refs(server_dir))
    moved: list[str] = []

    def tag_image(src: str, dst: str) -> str:
        if src.endswith(native.PARKED_TAG_SUFFIX):
            moved.append(dst)
            if len(moved) == 2:
                return "Error response from daemon: read-only file system"
        return daemon.tag_image(src, dst)

    def image_id(ref: str) -> str | None:
        return None if ref in live else daemon.image_id(ref)

    with pytest.raises(InstallerError):
        list(
            engine(rec, **_seams_of(rec, daemon, tag_image=tag_image, image_id=image_id)).rebuild(
                InstallOptions(server_dir=server_dir)
            )
        )
    assert all(daemon.names[ref] == "before" for ref in _refs(server_dir)), daemon.names
    assert daemon.transient() == [], daemon.transient()
