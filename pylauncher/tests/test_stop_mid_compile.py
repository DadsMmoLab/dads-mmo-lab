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

import json
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
UNCHECKED = native.STOPPED_BUILD_UNCHECKED_REFUSAL


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
    """Through the call site every Start passes (scoped re-review 6)."""
    asked: list[str] = []

    def image_id(ref: str, **_kw: object) -> str | None:
        asked.append(ref)
        raise AssertionError("Start asked Docker with no stopped build recorded")

    monkeypatch.setattr(docker, "image_id", image_id)
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    from yulon.catalog.catalog import load_catalog

    Controller(load_catalog().get("wow-wotlk").container_spec(), server_dir).refuse_start()
    assert asked == []


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


# -- scoped re-review: the record lives until a press succeeds ----------------


def test_a_rebuild_that_fails_before_compiling_keeps_the_record_and_the_names(
    tmp_path: Path,
) -> None:
    """Must-fix 1, its exact sequence: Stop at T0; a Rebuild at T0+5 min while BuildKit still
    solves (no tag moved yet) fails before it compiles; the old solve lands at T0+13."""
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    _stopped_mid_compile(rec, daemon, server_dir)

    def cannot_run(server_dir: Path, files: object, **_kw: object) -> docker.AttachedRun:
        raise OSError("docker: executable file not found")

    with pytest.raises(InstallerError) as raised:
        list(
            engine(rec, **_seams_of(rec, daemon, build=cannot_run)).rebuild(
                InstallOptions(server_dir=server_dir)
            )
        )
    assert "could not be run" in str(raised.value), raised.value
    assert native.read_stopped_build(server_dir) is not None
    assert sorted(daemon.transient()) == _rollback_names(server_dir), daemon.transient()
    assert native.STOPPED_EARLIER_NOTE in str(raised.value), raised.value
    daemon.compiled()  # T0+13
    assert engine(rec, **_seams_of(rec, daemon)).start_refusal(server_dir) == LANDED


def test_a_failed_compile_after_a_stop_keeps_the_record_and_the_names(tmp_path: Path) -> None:
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    _stopped_mid_compile(rec, daemon, server_dir)
    rec.build_result = docker.AttachedRun(1, ("cc1plus: error",))
    with pytest.raises(InstallerError):
        list(engine(rec, **_seams_of(rec, daemon)).rebuild(InstallOptions(server_dir=server_dir)))
    assert native.read_stopped_build(server_dir) is not None
    assert sorted(daemon.transient()) == _rollback_names(server_dir), daemon.transient()


def test_a_restore_after_a_stop_keeps_the_names_it_put_back_from(tmp_path: Path) -> None:
    """The new build does not come up; the old one is put back -- and a stopped solve may
    still land, so the `-rollback` names and the record stay for the Start check."""
    from tests.test_rebuild import _answers

    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    _stopped_mid_compile(rec, daemon, server_dir)
    with pytest.raises(InstallerError) as raised:
        list(
            engine(rec, **_seams_of(rec, daemon, wait_ready=_answers(False, True))).rebuild(
                InstallOptions(server_dir=server_dir)
            )
        )
    assert "put back and is running again" in str(raised.value), raised.value
    assert all(daemon.names[ref] == "before" for ref in _refs(server_dir)), daemon.names
    assert native.read_stopped_build(server_dir) is not None
    assert sorted(
        name for name in daemon.transient() if name.endswith(native.ROLLBACK_TAG_SUFFIX)
    ) == _rollback_names(server_dir), daemon.transient()
    assert engine(rec, **_seams_of(rec, daemon)).start_refusal(server_dir) is None
    daemon.compiled()
    assert engine(rec, **_seams_of(rec, daemon)).start_refusal(server_dir) == LANDED


def test_a_rebuild_that_succeeds_forgets_the_record_and_the_names(tmp_path: Path) -> None:
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    _stopped_mid_compile(rec, daemon, server_dir)
    list(engine(rec, **_seams_of(rec, daemon)).rebuild(InstallOptions(server_dir=server_dir)))
    assert native.read_stopped_build(server_dir) is None
    assert daemon.transient() == [], daemon.transient()


def test_the_landed_build_keeps_the_date_of_the_stopped_press(tmp_path: Path) -> None:
    """`made_unix` is read: the kept build says when the stopped press compiled it."""
    rec = Recorder(images=True)
    server_dir = a_parkable_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    _stopped_mid_compile(rec, daemon, server_dir)
    stopped = native.read_stopped_build(server_dir)
    assert stopped is not None
    (server_dir / native.STOPPED_BUILD_FILE).write_text(
        json.dumps(
            {
                "version": 1,
                "refs": list(stopped.refs),
                "fingerprint": stopped.fingerprint,
                "made_unix": 1_700_000_000,
            }
        ),
        encoding="utf-8",
    )
    daemon.compiled()
    said = list(
        engine(rec, **_seams_of(rec, daemon)).rebuild(InstallOptions(server_dir=server_dir))
    )
    when = native.ParkedBuild("0" * 64, {}, 1_700_000_000, "x").when()
    assert [line for line in said if f"The build kept from {when}" in line], said


# -- scoped re-review: a check that cannot be completed refuses ---------------


def _recorded(tmp_path: Path) -> tuple[Recorder, _Daemon, Path]:
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    _stopped_mid_compile(rec, daemon, server_dir)
    return rec, daemon, server_dir


def test_a_rollback_name_deleted_by_hand_refuses_start(tmp_path: Path) -> None:
    rec, daemon, server_dir = _recorded(tmp_path)
    del daemon.names[_refs(server_dir)[0] + native.ROLLBACK_TAG_SUFFIX]
    assert native.stopped_build_refusal(server_dir, daemon.image_id) == UNCHECKED


def test_a_rollback_lookup_that_times_out_refuses_start(tmp_path: Path) -> None:
    rec, daemon, server_dir = _recorded(tmp_path)

    def image_id(ref: str) -> str | None:
        if ref.endswith(native.ROLLBACK_TAG_SUFFIX):
            raise TimeoutError("docker image inspect timed out")
        return daemon.image_id(ref)

    assert native.stopped_build_refusal(server_dir, image_id) == UNCHECKED


def test_a_live_tag_that_does_not_answer_refuses_start(tmp_path: Path) -> None:
    rec, daemon, server_dir = _recorded(tmp_path)
    del daemon.names[_refs(server_dir)[1]]
    assert native.stopped_build_refusal(server_dir, daemon.image_id) == UNCHECKED


def test_a_docker_that_answers_nothing_does_not_refuse_start_here(tmp_path: Path) -> None:
    """Compose fails on its own then; the refusal is for a check Docker half-answered."""
    rec, daemon, server_dir = _recorded(tmp_path)
    assert native.stopped_build_refusal(server_dir, lambda ref: None) is None


def test_a_landed_tag_wins_over_an_unanswered_one(tmp_path: Path) -> None:
    rec, daemon, server_dir = _recorded(tmp_path)
    daemon.compiled(_refs(server_dir)[:1])
    del daemon.names[_refs(server_dir)[1] + native.ROLLBACK_TAG_SUFFIX]
    assert native.stopped_build_refusal(server_dir, daemon.image_id) == LANDED


# -- scoped re-review: every start path asks ----------------------------------


def test_rebuild_random_bots_is_refused_before_anything_once_a_stopped_build_landed(
    tmp_path: Path,
) -> None:
    """T197 round-4 rule: asked before the backup, the enrolment and the armed key."""
    from tests import test_rebuild_random_bots as random_bots
    from yulon import tuning
    from yulon.controller_wow_tortoise import poolreset

    path = random_bots._conf(tmp_path)
    world = random_bots.World(tmp_path)
    refs = ("yulon.local/tortoise-worldserver:native-0123abcd",)
    assert native.remember_stopped_build(tmp_path, native.StoppedBuild(refs, None, 1)) == ""
    ids = {refs[0]: "sha256:landed", refs[0] + native.ROLLBACK_TAG_SUFFIX: "sha256:old"}
    with pytest.raises(poolreset.PoolResetError) as refused:
        world.run(backup=world.backup, image_id=ids.get)
    assert str(refused.value).startswith(LANDED), refused.value
    assert world.events == [], "no backup, no enrolment, no restart"
    assert world.restarts == 0 and world.running is True
    assert path.read_bytes() == random_bots.CONF_TEXT.encode("utf-8")
    assert tuning.backups_of(path) == ()


def test_a_press_abandoned_before_it_compiles_keeps_an_earlier_stops_names(tmp_path: Path) -> None:
    rec, daemon, server_dir = _recorded(tmp_path)
    run = engine(rec, **_seams_of(rec, daemon)).rebuild(InstallOptions(server_dir=server_dir))
    for line in run:
        if line.startswith("Building the server."):
            break
    else:
        raise AssertionError("the rebuild never started its build")
    run.close()
    assert native.read_stopped_build(server_dir) is not None
    assert sorted(daemon.transient()) == _rollback_names(server_dir), daemon.transient()


def test_a_new_build_that_came_up_and_stopped_settles_the_record(tmp_path: Path) -> None:
    """T71's keep: the new build started, so like a success the stop's record goes."""
    from tests.test_rebuild import ABORTED_AFTER_READY

    rec, daemon, server_dir = _recorded(tmp_path)
    with pytest.raises(InstallerError):
        list(
            engine(
                rec, **_seams_of(rec, daemon, world_output=lambda spec: ABORTED_AFTER_READY)
            ).rebuild(InstallOptions(server_dir=server_dir))
        )
    assert native.read_stopped_build(server_dir) is None
    assert daemon.transient() == [], daemon.transient()
