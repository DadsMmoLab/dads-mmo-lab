"""T225: a Stop that lands after `docker compose build` moved the live tags.

Seen in code on T223's branch (2026-10-04): `stage_build()` read the Stop only
after compose had returned, and by then compose had already moved the live tags
onto the new build. The press then counted as "never built": the old build's
`-rollback` names were let go, and the live tags stayed on a build that had never
started -- the next Start ran it, and the way back was gone.

The fix reads "built" off the daemon: the live tags moved when a ref's image is
not its `-rollback` name's image, and an id Docker does not give counts as moved.
Every test drives `rebuild()` against `test_rebuild._Daemon`, so which image each
name holds is read off the fake daemon and never off a sentence.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.support_native import Recorder, engine
from tests.test_rebuild import _Daemon, _daemon_for, _refs, _seams_of, a_finished_install
from yulon import docker
from yulon.catalog import native
from yulon.catalog.installer import InstallerError, InstallOptions, RollbackNotDone

STOPPED_AS_IT_FINISHED = "The rebuild was stopped as its build finished"
ON_THE_OLD_BUILD = "the server is still on the build it had before this rebuild"


def _rollback_names(server_dir: Path) -> list[str]:
    return sorted(ref + native.ROLLBACK_TAG_SUFFIX for ref in _refs(server_dir))


def _all_on(daemon: _Daemon, server_dir: Path, image: str) -> bool:
    return all(daemon.names.get(ref) == image for ref in _refs(server_dir))


class _StopAfterTagging:
    """The build seam: compose tags `only` (every ref by default), then the Stop lands.

    `returncode` 0 is a Stop read after compose finished; `CANCELLED_RETURNCODE` is
    a Stop that killed the compose client after it had tagged.
    """

    def __init__(
        self,
        rec: Recorder,
        daemon: _Daemon,
        stop: threading.Event,
        returncode: int = 0,
        only: tuple[str, ...] | None = None,
    ) -> None:
        self.rec = rec
        self.daemon = daemon
        self.stop = stop
        self.returncode = returncode
        self.only = only

    def __call__(
        self, server_dir: Path, files: object, *, sink: object = None, cancel: object = None
    ) -> docker.AttachedRun:
        self.rec.calls.append("build")
        self.daemon.compiled(self.only)
        self.stop.set()
        return docker.AttachedRun(self.returncode, ("naming to yulon.local/... done",))


def _press(
    tmp_path: Path,
    *,
    returncode: int = 0,
    only_first: bool = False,
    overriding: Callable[[_Daemon, Path], dict[str, object]] | None = None,
) -> tuple[Recorder, _Daemon, Path, list[str], BaseException]:
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    stop = threading.Event()
    only = _refs(server_dir)[:1] if only_first else None
    seams = {
        **_seams_of(rec, daemon),
        "build": _StopAfterTagging(rec, daemon, stop, returncode, only),
        **(overriding(daemon, server_dir) if overriding is not None else {}),
    }
    said: list[str] = []
    with pytest.raises(InstallerError) as raised:
        for line in engine(rec, **seams).rebuild(
            InstallOptions(server_dir=server_dir), cancel=stop
        ):
            said.append(line)
    return rec, daemon, server_dir, said, raised.value


def test_a_stop_read_after_compose_moved_the_tags_puts_them_back_on_the_build_from_before(
    tmp_path: Path,
) -> None:
    rec, daemon, server_dir, _said, failed = _press(tmp_path)
    assert _all_on(daemon, server_dir, "before"), daemon.names
    assert daemon.transient() == [], daemon.transient()
    assert "recreate" not in rec.calls, rec.calls
    message = str(failed)
    assert message.startswith(STOPPED_AS_IT_FINISHED), message
    assert ON_THE_OLD_BUILD in message, message
    # Not the install's words: this was a rebuild.
    assert "the install was stopped" not in message.lower(), message


def test_a_stop_that_killed_compose_after_it_tagged_puts_the_tags_back(tmp_path: Path) -> None:
    """The second form: compose was killed AFTER it tagged, so the run is "cancelled", not 0."""
    rec, daemon, server_dir, _said, failed = _press(
        tmp_path, returncode=docker.CANCELLED_RETURNCODE
    )
    assert _all_on(daemon, server_dir, "before"), daemon.names
    assert daemon.transient() == [], daemon.transient()
    assert "recreate" not in rec.calls, rec.calls
    assert ON_THE_OLD_BUILD in str(failed), failed


def test_a_stop_after_one_of_four_images_was_tagged_puts_that_one_back(tmp_path: Path) -> None:
    rec, daemon, server_dir, _said, failed = _press(
        tmp_path, returncode=docker.CANCELLED_RETURNCODE, only_first=True
    )
    assert len(_refs(server_dir)) == 4, "the WotLK geometry this case is about"
    assert _all_on(daemon, server_dir, "before"), daemon.names
    assert daemon.transient() == [], daemon.transient()
    assert ON_THE_OLD_BUILD in str(failed), failed


def test_a_stop_after_the_swap_counts_an_unanswered_id_as_moved(tmp_path: Path) -> None:
    """Docker gives no id for the live tags: they are put back, which is right either way."""

    def silent_about_the_live_tags(daemon: _Daemon, server_dir: Path) -> dict[str, object]:
        live = set(_refs(server_dir))
        return {"image_id": lambda ref: None if ref in live else daemon.image_id(ref)}

    _rec, daemon, server_dir, _said, failed = _press(
        tmp_path, overriding=silent_about_the_live_tags
    )
    assert _all_on(daemon, server_dir, "before"), daemon.names
    assert daemon.transient() == [], daemon.transient()
    assert ON_THE_OLD_BUILD in str(failed), failed


def test_a_stop_before_compose_tagged_anything_still_releases_the_rollback_names(
    tmp_path: Path,
) -> None:
    """The control: no tag moved, so the `-rollback` names are duplicates and go, as before."""
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    stop = threading.Event()

    def build(server_dir: Path, files: object, **_kw: object) -> docker.AttachedRun:
        rec.calls.append("build")
        stop.set()
        return docker.AttachedRun(docker.CANCELLED_RETURNCODE, ("cc1plus ...",))

    with pytest.raises(InstallerError) as raised:
        list(
            engine(rec, **{**_seams_of(rec, daemon), "build": build}).rebuild(
                InstallOptions(server_dir=server_dir), cancel=stop
            )
        )
    assert _all_on(daemon, server_dir, "before"), daemon.names
    assert daemon.transient() == [], daemon.transient()
    # No restore ran: nothing was named `-failed` and nothing was moved back.
    assert ON_THE_OLD_BUILD not in str(raised.value), raised.value


def test_the_rollback_names_outlive_a_put_back_that_docker_refuses(tmp_path: Path) -> None:

    def refuses_the_move_back(daemon: _Daemon, _server_dir: Path) -> dict[str, object]:
        def tag_image(src: str, dst: str) -> str:
            if src.endswith(native.ROLLBACK_TAG_SUFFIX):
                return "Error response from daemon: read-only file system"
            return daemon.tag_image(src, dst)

        return {"tag_image": tag_image}

    _rec, daemon, server_dir, _said, failed = _press(tmp_path, overriding=refuses_the_move_back)
    assert isinstance(failed, RollbackNotDone), type(failed)
    assert set(daemon.transient()) >= set(_rollback_names(server_dir)), daemon.transient()
    assert all(daemon.names[name] == "before" for name in _rollback_names(server_dir))


def test_a_consumer_that_stops_reading_after_the_swap_keeps_the_rollback_names(
    tmp_path: Path,
) -> None:
    """The `BaseException` path: the generator is closed at "The build finished."."""
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    run = engine(rec, **_seams_of(rec, daemon)).rebuild(InstallOptions(server_dir=server_dir))
    for line in run:
        if line == "The build finished.":
            break
    else:
        raise AssertionError("the rebuild never said its build finished")
    assert _all_on(daemon, server_dir, "after"), "the swap had happened when reading stopped"
    run.close()
    assert sorted(daemon.transient()) == _rollback_names(server_dir), daemon.transient()
    assert all(daemon.names[name] == "before" for name in _rollback_names(server_dir))
    # Nothing could put the tags back without yielding, so no Start may run the
    # untested build they name (cold review, lead's decision).
    assert engine(rec).start_refusal(server_dir) == native.UNTESTED_BUILD_REFUSAL


def test_a_consumer_that_stops_reading_before_the_swap_lets_the_rollback_names_go(
    tmp_path: Path,
) -> None:
    """The control for the case above: before the compile the names are duplicates."""
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    run = engine(rec, **_seams_of(rec, daemon)).rebuild(InstallOptions(server_dir=server_dir))
    for line in run:
        if line.startswith("Building the server."):
            break
    else:
        raise AssertionError("the rebuild never started its build")
    assert _all_on(daemon, server_dir, "before")
    run.close()
    assert daemon.transient() == [], daemon.transient()
    assert engine(rec).start_refusal(server_dir) is None


def test_a_stop_after_the_swap_with_no_rollback_says_the_new_images_are_there(
    tmp_path: Path,
) -> None:
    """T170: nothing was kept, so nothing goes back; the sentence must not say "not all there"."""
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    daemon.names.clear()
    daemon.containers.clear()
    stop = threading.Event()

    def images_built(refs: object) -> bool:
        return all(ref in daemon.names for ref in refs)  # type: ignore[attr-defined]

    with pytest.raises(InstallerError) as raised:
        list(
            engine(
                rec,
                **{
                    **_seams_of(rec, daemon),
                    "build": _StopAfterTagging(rec, daemon, stop),
                    "images_built": images_built,
                },
            ).rebuild(InstallOptions(server_dir=server_dir), cancel=stop, missing_images_ok=True)
        )
    message = str(raised.value)
    assert message.startswith(STOPPED_AS_IT_FINISHED), message
    assert native.NO_ROLLBACK_UNTOUCHED in message, message
    assert native.NO_ROLLBACK_NOT_BUILT not in message, message
    assert _all_on(daemon, server_dir, "after"), daemon.names


def test_a_failed_compile_with_docker_silent_is_not_taken_for_a_build_that_moved_the_tags(
    tmp_path: Path,
) -> None:
    """Cold review, CRITICAL: exit 1 and no image id is a compile that never finished.

    An unanswered id counts as moved only when the build run could have tagged
    (it returned 0, or was cancelled). Here it failed, so nothing is put back,
    no Start is refused, and the sentence is the compile's own.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    rec.build_result = docker.AttachedRun(1, ("cc1plus: error: out of memory",))
    rec.ids_silent = True
    said: list[str] = []
    with pytest.raises(InstallerError) as raised:
        for line in engine(rec).rebuild(InstallOptions(server_dir=server_dir)):
            said.append(line)
    message = str(raised.value)
    assert message.startswith("the build failed (exit 1)"), message
    assert ON_THE_OLD_BUILD not in message and "was removed" not in message, message
    assert not [line for line in said if "Putting the build from before" in line], said
    assert not [call for call in rec.calls if call.endswith(native.FAILED_TAG_SUFFIX)], rec.calls
    assert sorted(c for c in rec.calls if c.startswith("rmi:")) == sorted(
        f"rmi:{name}" for name in _rollback_names(server_dir)
    ), rec.calls
    assert engine(rec).start_refusal(server_dir) is None


def test_a_stop_with_docker_silent_after_compose_finished_still_puts_the_tags_back(
    tmp_path: Path,
) -> None:
    """The other half of the rule: run returned 0, no id answers, so the restore runs."""
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    rec.ids_silent = True
    stop = threading.Event()

    def build(server_dir: Path, files: object, **_kw: object) -> docker.AttachedRun:
        rec.calls.append("build")
        stop.set()
        return docker.AttachedRun(0, ("naming to ... done",))

    with pytest.raises(InstallerError) as raised:
        list(engine(rec, build=build).rebuild(InstallOptions(server_dir=server_dir), cancel=stop))
    assert str(raised.value).startswith(native.STOPPED_AS_THE_BUILD_FINISHED), raised.value
    assert [call for call in rec.calls if call.endswith(native.FAILED_TAG_SUFFIX)], rec.calls
