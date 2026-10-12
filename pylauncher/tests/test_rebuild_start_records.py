"""T680: every way out of a Rebuild or an update leaves the two start records true.

`START_REFUSED_FILE` refuses every Start until a Rebuild succeeds; `SOURCES_OFF_FILE`
refuses it while a source folder is off the commit the running build came from.

* L11: a Rebuild whose world came up and then stopped KEEPS the new build (T71),
  and until T680 left the refusal file behind, so Start went on refusing with a
  reason that was no longer true. The sources-off record likewise.
* L12: the update route wrote the sources-off record only when a put-back failed.
  A press that ended any other way with the sources moved -- Yu'lon killed, a
  Docker error no handler wraps -- left new sources and no record, and Start ran
  the old image on them. The record is now written before the first fetch, and
  each way out either forgets it or leaves it naming what is really off.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support_native import ENTRY, Recorder, engine, install
from tests.test_rebuild import ABORTED_AFTER_READY
from yulon import docker
from yulon.catalog import native
from yulon.catalog.installer import InstallerError, InstallOptions, WorldStoppedAfterReadyError

OLD = "a" * 40
NEW = "b" * 40


def a_finished_install(rec: Recorder, tmp_path: Path) -> Path:
    server_dir = tmp_path / "wow"
    install(rec, server_dir)
    rec.calls.clear()
    return server_dir


def ready_to_update(tmp_path: Path) -> tuple[Recorder, Path]:
    """A finished WotLK install whose every source sits on OLD, with NEW upstream."""
    rec = Recorder()
    server_dir = a_finished_install(rec, tmp_path)
    for source in ENTRY.emulator.sources:
        rec.heads[server_dir / source.dest] = OLD
        rec.upstream[server_dir / source.dest] = NEW
        (server_dir / source.dest / ".git").mkdir(parents=True, exist_ok=True)
        (server_dir / source.dest / ".git" / "HEAD").write_text(OLD + "\n")
    rec.calls.clear()
    return rec, server_dir


def heads_follow_the_recorder(rec: Recorder) -> None:
    """Write `.git/HEAD` where the double moves a head (a fetch, a put-back), as git does."""
    real = rec.on_clone

    def moved(dest: Path) -> None:
        if real is not None:
            real(dest)
        (dest / ".git" / "HEAD").write_text(rec.heads[dest] + "\n")

    rec.on_clone = moved
    put_back = rec.restore_rev

    def restored(dest: Path, rev: str) -> None:
        put_back(dest, rev)
        (dest / ".git" / "HEAD").write_text(rev + "\n")

    rec.restore_rev = restored  # type: ignore[method-assign]


# -- L11 ----------------------------------------------------------------------


def test_a_kept_rebuild_forgets_the_start_refusal_it_resolved(tmp_path: Path) -> None:
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    assert native.owe_start(server_dir, why=native.UNTESTED_BUILD) == ""
    assert native.owed_start_refusal(server_dir) is not None

    with pytest.raises(WorldStoppedAfterReadyError):
        list(
            engine(rec, world_output=lambda spec: ABORTED_AFTER_READY).rebuild(
                InstallOptions(server_dir=server_dir)
            )
        )

    assert native.owed_start_refusal(server_dir) is None, "the kept build is one tested build"
    assert not (server_dir / native.START_REFUSED_FILE).exists()


def test_a_kept_rebuild_forgets_the_sources_off_record(tmp_path: Path) -> None:
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    # T758: a folder the Rebuild does not build from. A row for one it does (a source
    # off its build) refuses the Rebuild before anything, so it never gets this far.
    stray = server_dir / "elsewhere"
    (stray / ".git").mkdir(parents=True)
    (stray / ".git" / "HEAD").write_text("c" * 40 + "\n")
    assert native.remember_sources_off(server_dir, [("some/repo", stray, OLD)]) == ""
    assert native.sources_off_refusal(server_dir) is not None, "the ground: Start refuses"

    with pytest.raises(WorldStoppedAfterReadyError):
        list(
            engine(rec, world_output=lambda spec: ABORTED_AFTER_READY).rebuild(
                InstallOptions(server_dir=server_dir)
            )
        )

    assert not (server_dir / native.SOURCES_OFF_FILE).exists(), "made from the folders as they are"


def test_a_rebuild_that_puts_the_old_build_back_keeps_the_start_refusal(tmp_path: Path) -> None:
    """The other side: no new build was kept, so a refusal the press found stays."""
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    assert native.owe_start(server_dir) == ""

    rec.ready = False
    with pytest.raises(InstallerError) as raised:
        list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))

    assert not isinstance(raised.value, WorldStoppedAfterReadyError)
    assert (server_dir / native.START_REFUSED_FILE).exists()


# -- L12 ----------------------------------------------------------------------


def test_an_update_ended_mid_compile_leaves_start_refused_on_the_moved_sources(
    tmp_path: Path,
) -> None:
    """Yu'lon gone mid-compile: nothing more runs, and the folder is on NEW under the old image."""
    rec, server_dir = ready_to_update(tmp_path)
    heads_follow_the_recorder(rec)
    press = engine(rec).update_to_latest(InstallOptions(server_dir=server_dir))

    for line in press:
        if line == "--- build":
            break  # and never resumed: the process is gone
    else:
        pytest.fail("the update never reached its compile")

    off = native.sources_still_off(server_dir)
    assert {row.built for row in off} == {OLD}, off
    assert native.sources_off_refusal(server_dir) is not None


def test_an_update_whose_compile_raises_a_docker_error_leaves_start_refused(
    tmp_path: Path,
) -> None:
    rec, server_dir = ready_to_update(tmp_path)
    heads_follow_the_recorder(rec)

    def gone(*_args: object, **_kwargs: object) -> docker.AttachedRun:
        raise docker.DockerCommandError("Cannot connect to the Docker daemon")

    with pytest.raises(Exception):  # noqa: B017 - whatever escapes, the record must be true
        list(engine(rec, build=gone).update_to_latest(InstallOptions(server_dir=server_dir)))

    heads = {
        read
        for read in (native.read_head_file(server_dir / s.dest) for s in ENTRY.emulator.sources)
    }
    off = native.sources_still_off(server_dir)
    if heads == {OLD}:
        assert off == (), "the sources went back, so nothing is off"
    else:
        assert off, "sources left on NEW under the old image must refuse Start"


def test_an_update_refused_before_the_compile_leaves_nothing_off(tmp_path: Path) -> None:
    """The record written before the fetch reads as nothing once the sources are back."""
    rec, server_dir = ready_to_update(tmp_path)
    heads_follow_the_recorder(rec)

    def refuse_after_the_move(*_args: object, **_kwargs: object) -> object:
        raise InstallerError("refused after the move")

    made = engine(rec)
    made.check_carried_patches = refuse_after_the_move  # type: ignore[method-assign]

    with pytest.raises(InstallerError, match="refused after the move"):
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))

    assert not (server_dir / native.SOURCES_OFF_FILE).exists(), "put back: nothing is off"
    assert native.sources_off_refusal(server_dir) is None


def test_an_update_that_succeeds_leaves_no_record(tmp_path: Path) -> None:
    rec, server_dir = ready_to_update(tmp_path)
    heads_follow_the_recorder(rec)

    list(engine(rec).update_to_latest(InstallOptions(server_dir=server_dir)))

    assert not (server_dir / native.SOURCES_OFF_FILE).exists()


def test_an_update_whose_world_stops_after_ready_keeps_the_sources_and_no_record(
    tmp_path: Path,
) -> None:
    rec, server_dir = ready_to_update(tmp_path)
    heads_follow_the_recorder(rec)

    with pytest.raises(WorldStoppedAfterReadyError):
        list(
            engine(rec, world_output=lambda spec: ABORTED_AFTER_READY).update_to_latest(
                InstallOptions(server_dir=server_dir)
            )
        )

    assert native.sources_still_off(server_dir) == (), "the kept build was made from NEW"
    assert not (server_dir / native.SOURCES_OFF_FILE).exists()


def test_a_folder_an_earlier_press_left_off_keeps_its_row_when_an_update_moves_the_rest(
    tmp_path: Path,
) -> None:
    rec, server_dir = ready_to_update(tmp_path)
    heads_follow_the_recorder(rec)
    stray = server_dir / "elsewhere"
    (stray / ".git").mkdir(parents=True)
    (stray / ".git" / "HEAD").write_text("c" * 40 + "\n")
    assert native.remember_sources_off(server_dir, [("other/repo", stray, "d" * 40)]) == ""
    press = engine(rec).update_to_latest(InstallOptions(server_dir=server_dir))

    for line in press:
        if line == "--- build":
            break
    else:
        pytest.fail("the update never reached its compile")

    off = {row.dest: row.built for row in native.sources_still_off(server_dir)}
    assert off.get(stray) == "d" * 40, off
    assert set(off.values()) == {OLD, "d" * 40}, off
