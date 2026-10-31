"""T830: "Start over with a plain install" on a folder whose bring-in did not finish.

The bring-in is the real `MovedInInstall` over the doubles `test_move_server_flow.py` uses, cut
off in its data step after its first database went in (`_cut_off_move`). The reset is the real
`start_over.StartOver` with the real seams (`app_seams`) over that server's Maintenance world,
and the real `purge.Uninstaller` for the removal; only Docker is a double, `Volumes`, which
holds what each database of the compose project's volume holds ("moved" from the other
computer, or "fresh" from an install) the way a named volume outlives its containers. The plain
install that follows is a double that starts the server on whatever volume it finds.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.support_native import UPSTREAM_COMPOSE, Recorder
from tests.support_native import install as native_install
from tests.support_player_text import command_faults, text_faults
from tests.test_move_server_flow import Move, _cut_off_move, plan_for
from tests.test_move_server_flow import reservations as reservations  # noqa: F401 - a fixture
from yulon import backup_shelf, docker, kept_backups, logsnap, move_server, purge, start_over
from yulon.catalog import composegen, native
from yulon.catalog.catalog import load_catalog
from yulon.catalog.installer import InstallOptions
from yulon.controller_wow_wotlk import maintenance
from yulon.move_flows import MoveError
from yulon.ownership import Ownership

WOTLK = load_catalog().get("wow-wotlk")
SCHEMAS = ("acore_auth", "acore_characters", "acore_world")
NOW = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)


class Volumes:
    """Docker, as far as the reset and the plain install reach it: one compose project."""

    def __init__(self, mv: Move) -> None:
        self.box = mv.target
        self.events = mv.events
        self.project = composegen.project_name(WOTLK.id, self.box.server_dir)
        self.db_volume = f"{self.project}{purge.DB_VOLUME_SUFFIX}"
        # What the cut-off data step left: the databases it put in hold the other computer's.
        loaded = {e.removeprefix("load:") for e in self.events if e.startswith("load:")}
        self.volumes: dict[str, dict[str, str]] = {
            self.db_volume: {s: "moved" if s in loaded else "fresh" for s in SCHEMAS},
            f"{self.project}{purge.CLIENT_VOLUME_SUFFIX}": {},
        }
        spec = self.box.spec
        self.containers = [spec.db, spec.auth, spec.world]
        self.fail_volume_rm: BaseException | None = None

    def volumes_of(self, project: str) -> list[str] | None:
        return [v for v in self.volumes if v.startswith(f"{project}_")]

    def containers_of(self, project: str) -> list[str] | None:
        return list(self.containers) if project == self.project else []

    def census(self, project: str) -> docker.Running:
        return docker.Running(ours=tuple(self.box.up))

    def remove_containers(self) -> bool:
        self.events.append("rm-containers")
        self.containers.clear()
        self.box.up.clear()
        return True

    def remove_volume(self, name: str) -> None:
        if self.fail_volume_rm is not None:
            failure, self.fail_volume_rm = self.fail_volume_rm, None
            raise failure
        self.events.append(f"rm-volume:{name}")
        del self.volumes[name]

    def uninstaller(self) -> purge.Uninstaller:
        """The real Uninstall over this Docker, with the reset's own folder and restore rules."""
        return purge.Uninstaller(
            game=WOTLK.id,
            server_dir=self.box.server_dir,
            spec=self.box.spec,
            image_refs=("yulon.local/ac-wotlk-worldserver:native-test",),
            forget=lambda: None,
            claim=lambda _path: Ownership.OWNED,
            reason_of=lambda _path: "",
            project_of=lambda: self.project,
            census=self.census,
            containers_of=self.containers_of,
            volumes_of=self.volumes_of,
            folder_size=lambda _path: 0,
            snapshot=logsnap.Snapshot,
            remove_containers=self.remove_containers,
            remove_volume=self.remove_volume,
            remove_image=lambda _ref: "",
            remove_folder=start_over.empty_the_folder,
            forget_pending=lambda: None,
            forget_credential=lambda: None,
            remove_extraction_client=lambda: "",
            stop_background_jobs=lambda: None,
            take_back_client_files=lambda: ([], []),
            restore_refusal=start_over.running_load_refusal,
        )

    def seams(self) -> start_over.ResetSeams:
        un = self.uninstaller()
        real = start_over.app_seams(WOTLK, self.box.server_dir, world=self.box.world, image_refs=())
        return replace(
            real,
            projects=lambda: (self.project,),
            volumes_of=self.volumes_of,
            containers_of=self.containers_of,
            uninstall_refusal=lambda: un.plan().refusal,
            uninstall=lambda: un.run(keep_characters=False),
        )


class Plain:
    """The tile's plain install: it starts the server on the database volume it finds.

    `compose up` mounts the project's named volume when it is there and makes it when it is not,
    and the importer fills what a database lacks: a database already in the volume stays as it
    is. That is the whole of the Codex finding this answers.
    """

    def __init__(self, docker_: Volumes) -> None:
        self.docker = docker_
        self.runs = 0
        self.started_on: dict[str, str] | None = None
        self.folder_then: list[str] = []

    def run(
        self, options: InstallOptions | None = None, *, cancel: object = None, ask: object = None
    ) -> Iterator[str]:
        self.runs += 1
        assert options is not None and options.server_dir is not None
        self.folder_then = sorted(p.name for p in options.server_dir.iterdir())
        self.docker.events.append("plain-install")
        volume = self.docker.volumes.setdefault(self.docker.db_volume, {})
        for schema in SCHEMAS:
            volume.setdefault(schema, "fresh")
        self.started_on = dict(volume)
        yield "The plain install finished and the server is running."


def _press(
    mv: Move,
    vols: Volumes,
    plain: Plain,
    *,
    seams: Callable[[], start_over.ResetSeams] | None = None,
    expected: object = "read",
) -> list[str]:
    server_dir = mv.target.server_dir
    engine = start_over.StartOver(
        server_dir,
        plain=plain,
        seams=seams or vols.seams,
        expected=move_server.read_marker(server_dir) if expected == "read" else None,
        now=lambda: NOW,
    )
    return list(engine.run(InstallOptions(server_dir=server_dir, first_install=True)))


def _snapshot(folder: Path) -> dict[str, bytes]:
    return {
        p.relative_to(folder).as_posix(): p.read_bytes()
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


@pytest.fixture
def cut_off(tmp_path: Path) -> tuple[Move, Volumes, Plain]:
    mv = _cut_off_move(tmp_path)
    vols = Volumes(mv)
    assert vols.volumes[vols.db_volume] == {
        "acore_auth": "moved",
        "acore_characters": "fresh",
        "acore_world": "fresh",
    }, "the data step should have stopped after its first database"
    return mv, vols, Plain(vols)


def _kept_folder(mv: Move) -> Path:
    sets = kept_backups.earlier_sets(mv.target.server_dir)
    assert len(sets) == 1, sets
    return sets[0]


# =========================================================== the reset, end to end


def test_a_cut_off_bring_in_starts_over_on_fresh_databases(
    cut_off: tuple[Move, Volumes, Plain],
) -> None:
    """The Codex CRITICAL: the plain install must never start on what the bring-in loaded.

    Mutation: `_remove()` skipped (the old rename) -> the removal never runs, the baseline finds
    the volume, and the press refuses; with the baseline also gone, the server starts on the
    moved auth database.
    """
    mv, vols, plain = cut_off
    server_dir = mv.target.server_dir
    record_before = (server_dir / move_server.MOVE_IN_FILE).read_bytes()
    lines = _press(mv, vols, plain)
    assert plain.runs == 1
    assert plain.started_on == {s: "fresh" for s in SCHEMAS}, plain.started_on
    # The folder the plain install was handed: the bring-in's records and files all gone; the
    # reset's marker is still there until the plain install writes its own record (T830 H1).
    assert set(plain.folder_then) <= {
        docker.FOLDER_ID_FILE,
        start_over.RESET_FILE,
    }, plain.folder_then
    assert start_over.READY in lines
    kept = _kept_folder(mv)
    aside = sorted(kept.glob(f"{start_over.SET_ASIDE_PREFIX}*.json"))
    assert [p.read_bytes() for p in aside] == [record_before]
    assert not (server_dir / start_over.RESET_FILE).exists()


def test_the_copy_is_taken_before_anything_is_removed(
    cut_off: tuple[Move, Volumes, Plain],
) -> None:
    """Mutation: the copy step moved after the removal -> the dumps come after `rm-*`."""
    mv, vols, plain = cut_off
    start = len(mv.events)
    _press(mv, vols, plain)
    events = mv.events[start:]
    dumps = [i for i, e in enumerate(events) if e.startswith("dump:")]
    removals = [i for i, e in enumerate(events) if e.startswith("rm-")]
    assert sorted(events[i] for i in dumps) == [f"dump:{s}" for s in SCHEMAS]
    assert removals and max(dumps) < min(removals), events
    # The copy is kept beside the server folder, where the Backups list shows it.
    kept = _kept_folder(mv)
    copies = sorted(kept.rglob(f"*_{backup_shelf.START_OVER_LABEL}_*.sql"))
    assert [p.name.split(f"{backup_shelf.START_OVER_LABEL}_")[1] for p in copies] == [
        f"{s}.sql" for s in SCHEMAS
    ]
    sets = backup_shelf._kept_sets(mv.target.server_dir, WOTLK.id)
    made = {backup_shelf.what_made_it(r) for s in sets for r in s.rows if r.database in SCHEMAS}
    assert "taken before an unfinished bring-in was started over (it may be half-written)" in made


def test_a_copy_that_fails_refuses_and_removes_nothing(
    cut_off: tuple[Move, Volumes, Plain],
) -> None:
    """Mutation: `_copy()` swallows the failure -> the removal runs with no copy of the world."""
    mv, vols, plain = cut_off
    server_dir = mv.target.server_dir
    mv.target.db.fail_dump_of = "acore_world"
    before = _snapshot(server_dir)
    volumes_before = json.dumps(vols.volumes, sort_keys=True)
    with pytest.raises(start_over.StartOverError) as refused:
        _press(mv, vols, plain)
    assert str(refused.value) == start_over.COPY_FAILED
    assert "acore_world" in refused.value.detail
    assert json.dumps(vols.volumes, sort_keys=True) == volumes_before
    assert not [e for e in mv.events if e.startswith("rm-")]
    assert plain.runs == 0
    after = _snapshot(server_dir)
    # Only the copies the backup managed before it failed are new; nothing else changed.
    assert {k: v for k, v in after.items() if "before-start-over" not in k} == before
    assert not (server_dir / start_over.RESET_FILE).exists(), "nothing was removed: no reset"
    assert move_server.unfinished_move_in(server_dir)


def test_a_volume_that_will_not_go_leaves_the_bring_in_record_active(
    cut_off: tuple[Move, Volumes, Plain],
) -> None:
    """Mutation: the record set aside before the removal -> it is gone after the failure."""
    mv, vols, plain = cut_off
    server_dir = mv.target.server_dir
    record = (server_dir / move_server.MOVE_IN_FILE).read_bytes()
    vols.fail_volume_rm = docker.DockerCommandError("the volume is in use")
    with pytest.raises(start_over.StartOverError) as refused:
        _press(mv, vols, plain)
    said = str(refused.value)
    assert said.startswith("Starting over stopped part-way.")
    assert said.endswith(start_over.KEPT_RECORD)
    assert (server_dir / move_server.MOVE_IN_FILE).read_bytes() == record
    assert move_server.unfinished_move_in(server_dir)
    assert plain.runs == 0
    reset = json.loads((server_dir / start_over.RESET_FILE).read_text(encoding="utf-8"))
    assert reset["removing"] is True and "removed" not in reset["done"]
    # Bring-in is refused now: what its record says is done may be gone.
    again = plan_for(mv.path, server_dir)
    assert again.refusals == (move_server.START_OVER_UNFINISHED,)


def test_a_reset_killed_part_way_is_carried_on_and_copies_once(
    cut_off: tuple[Move, Volumes, Plain],
) -> None:
    """A crash (nothing of the press's own handling runs) during the removal, then Install again.

    Mutation: `_Reset.read()` ignored (every press starts afresh) -> the second press copies
    again, from a server whose database is already gone, and refuses.
    """
    mv, vols, plain = cut_off
    server_dir = mv.target.server_dir

    class Killed(BaseException):
        pass

    vols.fail_volume_rm = Killed()
    with pytest.raises(Killed):
        _press(mv, vols, plain)
    assert (server_dir / start_over.RESET_FILE).exists()
    assert (server_dir / move_server.MOVE_IN_FILE).exists()
    dumps = len([e for e in mv.events if e.startswith("dump:")])
    # A Bring-in pressed now refuses before it writes anything.
    record = (server_dir / move_server.MOVE_IN_FILE).read_bytes()
    with pytest.raises(MoveError) as refused:
        list(mv.install().run(InstallOptions(server_dir=server_dir)))
    assert str(refused.value).startswith(move_server.START_OVER_UNFINISHED)
    assert (server_dir / move_server.MOVE_IN_FILE).read_bytes() == record
    lines = _press(mv, vols, plain, expected=None)
    assert lines[0] == "Carrying on starting this folder over with a plain install."
    assert len([e for e in mv.events if e.startswith("dump:")]) == dumps, "copied twice"
    assert plain.runs == 1 and plain.started_on == {s: "fresh" for s in SCHEMAS}
    assert not (server_dir / start_over.RESET_FILE).exists()


def _copies(mv: Move) -> list[Path]:
    """The dumps the copy step made, still in the server folder's backups (nothing removed)."""
    return sorted(
        maintenance.backups_dir(mv.target.server_dir).glob(
            f"*_{backup_shelf.START_OVER_LABEL}_*.sql"
        )
    )


def _crash_before_removal(mv: Move, vols: Volumes, plain: Plain) -> list[Path]:
    """Stop a reset dead inside its first volume removal, having copied and not moved backups."""

    class Killed(BaseException):
        pass

    vols.fail_volume_rm = Killed()
    with pytest.raises(Killed):
        _press(mv, vols, plain)
    dumps = _copies(mv)
    assert len(dumps) == len(SCHEMAS), dumps
    return dumps


def _resume(mv: Move, vols: Volumes, plain: Plain) -> start_over.StartOverError:
    """Press Install again on the stopped reset, expecting the refused copy."""
    with pytest.raises(start_over.StartOverError) as refused:
        _press(mv, vols, plain, expected=None)
    return refused.value


def test_a_resumed_reset_refuses_a_copy_file_that_is_gone(
    cut_off: tuple[Move, Volumes, Plain],
) -> None:
    """H2: nothing is removed on the strength of a dump that is no longer there.

    Mutation: `_revalidate()` skipped -> the removal goes ahead and the volume is gone, with
    the dump it was meant to protect already missing.
    """
    mv, vols, plain = cut_off
    dumps = _crash_before_removal(mv, vols, plain)
    dumps[0].unlink()
    events, volumes = list(mv.events), json.dumps(vols.volumes, sort_keys=True)
    refused = _resume(mv, vols, plain)
    assert str(refused) == start_over.damaged_copy(dumps[0])
    assert mv.events == events and json.dumps(vols.volumes, sort_keys=True) == volumes
    assert plain.runs == 0
    reset = json.loads((mv.target.server_dir / start_over.RESET_FILE).read_text(encoding="utf-8"))
    assert reset["done"] == ["project", "stopped"], reset["done"]
    assert reset["copies"] == [] and reset["removing"] is False


def test_a_resumed_reset_refuses_a_copy_file_that_was_cut_short(
    cut_off: tuple[Move, Volumes, Plain],
) -> None:
    """H2: a dump whose size is not the recorded one is not the copy that was made.

    Mutation: the size comparison dropped -> the shortened dump passes and volumes go.
    """
    mv, vols, plain = cut_off
    dumps = _crash_before_removal(mv, vols, plain)
    cut = dumps[1]
    cut.write_bytes(cut.read_bytes()[:-10])
    volumes = json.dumps(vols.volumes, sort_keys=True)
    refused = _resume(mv, vols, plain)
    assert str(refused) == start_over.damaged_copy(cut)
    assert json.dumps(vols.volumes, sort_keys=True) == volumes
    assert plain.runs == 0


def test_a_resumed_reset_refuses_a_copy_file_whose_contents_changed(
    cut_off: tuple[Move, Volumes, Plain],
) -> None:
    """H2: same size, different bytes — only the recorded sha256 tells it apart.

    Mutation: the sha256 comparison dropped -> this passes and volumes are removed in favour
    of a dump that is not the one written.
    """
    mv, vols, plain = cut_off
    dumps = _crash_before_removal(mv, vols, plain)
    changed = bytearray(dumps[2].read_bytes())
    changed[-1] ^= 0xFF
    dumps[2].write_bytes(bytes(changed))
    volumes = json.dumps(vols.volumes, sort_keys=True)
    refused = _resume(mv, vols, plain)
    assert str(refused) == start_over.damaged_copy(dumps[2])
    assert json.dumps(vols.volumes, sort_keys=True) == volumes
    assert plain.runs == 0


def test_a_resumed_reset_refuses_a_copy_that_does_not_cover_every_database(
    cut_off: tuple[Move, Volumes, Plain],
) -> None:
    """H2: the recorded dumps must cover every schema the copy step was asked to copy.

    Mutation: the coverage check dropped -> a reset with no dump of the auth database removes
    the volume that held it.
    """
    mv, vols, plain = cut_off
    _crash_before_removal(mv, vols, plain)
    path = mv.target.server_dir / start_over.RESET_FILE
    body = json.loads(path.read_text(encoding="utf-8"))
    body["copies"] = [copy for copy in body["copies"] if copy["database"] != "acore_auth"]
    path.write_text(json.dumps(body), encoding="utf-8")
    volumes = json.dumps(vols.volumes, sort_keys=True)
    refused = _resume(mv, vols, plain)
    assert str(refused) == start_over.damaged_copy(Path(body["copies_dir"]))
    assert json.dumps(vols.volumes, sort_keys=True) == volumes
    assert plain.runs == 0


def test_the_marker_outlives_a_death_in_the_plain_installs_first_stage(
    cut_off: tuple[Move, Volumes, Plain],
) -> None:
    """H1: a folder whose plain install died before writing any record is still ours.

    Start over keeps `.yulon-start-over.json` until the plain install has written an ownership
    record. A real Install accepts the folder (marker, nothing else of the bring-in), says it is
    continuing, and removes the marker — while this press's own clone has not happened yet.

    Mutation: `reset.forget()` left at the end of `_reset()` -> the marker is gone and the
    folder below has only the bring-in's set-aside and no record, so Install refuses it; and no
    `CONTINUING_START_OVER` line can be said.
    """
    mv, vols, plain = cut_off
    server_dir = mv.target.server_dir

    class Killed(BaseException):
        pass

    class Dying(Plain):
        def run(self, options=None, *, cancel=None, ask=None):  # type: ignore[no-untyped-def]
            self.runs += 1
            raise Killed  # the process died before Install wrote anything at all

    with pytest.raises(Killed):
        _press(mv, vols, Dying(vols))
    assert (server_dir / start_over.RESET_FILE).exists()
    assert not (server_dir / native.STATE_FILE).exists()
    # Bring-in refuses it, with the sentence that sends the player to Install.
    assert plan_for(mv.path, server_dir).refusals == (move_server.START_OVER_UNFINISHED,)
    assert move_server.START_OVER_UNFINISHED == (
        "This folder is being reinstalled from scratch (Start over); press Install to finish it."
    )
    # Install accepts it, says so once, and a finished install never leaves the marker.
    rec = Recorder()
    real_unmodified = rec.file_unmodified

    def unmodified(dest: Path, relative_path: str) -> bool | None:
        # The double keys `tracked` by the destination it was handed; the checkout arrives in
        # the server folder through `_move_clone_into()`, so answer for the file it moved, with
        # the content the double wrote — the real `git status` knows it at either path.
        if (dest / relative_path) in rec.tracked:
            return real_unmodified(dest, relative_path)
        path = dest / relative_path
        return (
            relative_path == composegen.BASE_FILE
            and path.is_file()
            and path.read_text(encoding="utf-8") == UPSTREAM_COMPOSE
        )

    rec.file_unmodified = unmodified  # type: ignore[method-assign]
    lines = native_install(rec, server_dir)
    assert lines.count(native.CONTINUING_START_OVER) == 1, lines
    assert (server_dir / native.STATE_FILE).is_file()
    assert not (server_dir / start_over.RESET_FILE).exists()


def test_a_reset_that_stopped_after_setting_the_record_aside_only_finishes(
    cut_off: tuple[Move, Volumes, Plain],
) -> None:
    """The marker outlives the reset: a death in the plain install's first stage is carried on
    by the next press, which runs the install again without recopying or removing anything."""
    mv, vols, plain = cut_off
    server_dir = mv.target.server_dir

    class Killed(BaseException):
        pass

    class Dying(Plain):
        def run(self, options=None, *, cancel=None, ask=None):  # type: ignore[no-untyped-def]
            self.runs += 1
            assert (server_dir / start_over.RESET_FILE).exists(), "the marker left with the record"
            raise Killed

    dying = Dying(vols)
    with pytest.raises(Killed):
        _press(mv, vols, dying)
    assert not (server_dir / move_server.MOVE_IN_FILE).exists()
    assert (server_dir / start_over.RESET_FILE).exists()
    assert dying.runs == 1
    dumps = len([e for e in mv.events if e.startswith("dump:")])
    lines = _press(mv, vols, plain, expected=None)
    assert plain.runs == 1 and start_over.READY in lines
    assert len([e for e in mv.events if e.startswith("dump:")]) == dumps, "copied twice"
    assert not (server_dir / start_over.RESET_FILE).exists()
    assert len(list(_kept_folder(mv).glob(f"{start_over.SET_ASIDE_PREFIX}*"))) == 1


def test_a_users_own_clone_folder_survives_a_start_over_and_the_install(
    cut_off: tuple[Move, Volumes, Plain],
) -> None:
    """R2: `<server folder>.yulon-clone` was a fixed name Yu'lon never made, and the old clone
    step deleted whatever was there. It is never touched now, and the clone lands in a folder
    Yu'lon made and marked.

    Mutation: the staging name back to the fixed one (or the removal without the marker check)
    -> `my-notes.txt` is gone.
    """
    mv, vols, plain = cut_off
    server_dir = mv.target.server_dir
    theirs = server_dir.with_name(server_dir.name + ".yulon-clone")
    theirs.mkdir()
    (theirs / "my-notes.txt").write_bytes(b"mine")

    class Killed(BaseException):
        pass

    class Dying(Plain):
        def run(self, options=None, *, cancel=None, ask=None):  # type: ignore[no-untyped-def]
            self.runs += 1
            raise Killed  # the plain install stopped; its marker is still there

    with pytest.raises(Killed):
        _press(mv, vols, Dying(vols))
    assert (server_dir / start_over.RESET_FILE).exists(), "the marker the install needs is gone"
    rec = Recorder()
    real_unmodified = rec.file_unmodified

    def unmodified(dest: Path, relative_path: str) -> bool | None:
        # The checkout arrives in the server folder through `_move_clone_into()`.
        if (dest / relative_path) in rec.tracked:
            return real_unmodified(dest, relative_path)
        path = dest / relative_path
        return (
            relative_path == composegen.BASE_FILE
            and path.is_file()
            and path.read_text(encoding="utf-8") == UPSTREAM_COMPOSE
        )

    rec.file_unmodified = unmodified  # type: ignore[method-assign]
    lines = native_install(rec, server_dir)
    assert lines.count(native.CONTINUING_START_OVER) == 1, lines
    assert (server_dir / native.STATE_FILE).is_file()
    notes = theirs / "my-notes.txt"
    assert notes.read_bytes() == b"mine", "Yu'lon removed a folder it did not make"
    staged = [spec.dest for spec in rec.clones if spec.dest.parent == server_dir.parent]
    assert staged and all(path != theirs for path in staged), staged
    assert all(
        path.name.startswith(server_dir.name + start_over.STAGING_SUFFIX) for path in staged
    ), staged
    assert not [path for path in staged if path.exists()], "a staging folder was left beside it"


def test_a_death_after_the_bring_in_record_was_removed_is_carried_on(
    cut_off: tuple[Move, Volumes, Plain],
) -> None:
    """R1: the archive is named in the record BEFORE the source is removed.

    A death between removing `.yulon-move-in.json` and recording the step used to leave a
    folder with no bring-in record and no finished step, which every later press refused as
    `RECORD_GONE`. The next press now finds the archive the record named, records the step and
    starts the plain install.

    Mutation: `set_aside_to` not saved ahead (the name chosen only when writing) -> the resume
    has no archive to point at and refuses.
    """
    mv, vols, plain = cut_off
    server_dir = mv.target.server_dir
    record_before = (server_dir / move_server.MOVE_IN_FILE).read_bytes()
    real_finished = start_over._Reset.finished

    class Killed(BaseException):
        pass

    def die_at_set_aside(self: start_over._Reset, step: str) -> None:
        if step == "set-aside":
            raise Killed  # the record is saved with the step; the death is just before that
        real_finished(self, step)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(start_over._Reset, "finished", die_at_set_aside)
        with pytest.raises(Killed):
            _press(mv, vols, plain)
    assert not (server_dir / move_server.MOVE_IN_FILE).exists(), "the crash is after the removal"
    reset = json.loads((server_dir / start_over.RESET_FILE).read_text(encoding="utf-8"))
    assert "set-aside" not in reset["done"]
    archive = Path(reset["set_aside_to"])
    assert archive.read_bytes() == record_before
    assert plain.runs == 0
    lines = _press(mv, vols, plain, expected=None)
    assert lines[0] == "Carrying on starting this folder over with a plain install."
    assert plain.runs == 1 and plain.started_on == {s: "fresh" for s in SCHEMAS}
    assert not (server_dir / start_over.RESET_FILE).exists()
    assert [p.name for p in _kept_folder(mv).glob(f"{start_over.SET_ASIDE_PREFIX}*")] == [
        archive.name
    ], "a second archive was made"


def test_a_death_between_the_archive_and_the_source_removal_only_finishes_the_move(
    cut_off: tuple[Move, Volumes, Plain],
) -> None:
    """R1: with the archive written and the source still there, the archive wins.

    The archive already holds the record's bytes; the next press removes the source and
    records the step, without writing a second archive or reading a name nobody wrote.

    Mutation: `_set_aside()` writing the archive again from the source (no such check) -> the
    archive is replaced under a second name.
    """
    mv, vols, plain = cut_off
    server_dir = mv.target.server_dir
    record_before = (server_dir / move_server.MOVE_IN_FILE).read_bytes()
    real_write = start_over._write_whole

    class Killed(BaseException):
        pass

    def write_then_die(target: Path, data: bytes) -> None:
        real_write(target, data)
        raise Killed

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(start_over, "_write_whole", write_then_die)
        with pytest.raises(Killed):
            _press(mv, vols, plain)
    assert (server_dir / move_server.MOVE_IN_FILE).read_bytes() == record_before
    reset = json.loads((server_dir / start_over.RESET_FILE).read_text(encoding="utf-8"))
    archive = Path(reset["set_aside_to"])
    assert archive.read_bytes() == record_before and "set-aside" not in reset["done"]
    dumps = len([e for e in mv.events if e.startswith("dump:")])
    lines = _press(mv, vols, plain, expected=None)
    assert plain.runs == 1 and start_over.READY in lines
    assert not (server_dir / move_server.MOVE_IN_FILE).exists()
    assert archive.read_bytes() == record_before
    assert [p.name for p in _kept_folder(mv).glob(f"{start_over.SET_ASIDE_PREFIX}*")] == [
        archive.name
    ], "a second archive was made"
    assert len([e for e in mv.events if e.startswith("dump:")]) == dumps, "copied twice"
    assert not (server_dir / start_over.RESET_FILE).exists()


def test_a_resumed_reset_whose_archive_is_gone_still_refuses(
    cut_off: tuple[Move, Volumes, Plain],
) -> None:
    """R1: the archive is the only proof the bring-in's record was kept; without it, RECORD_GONE.

    Mutation: `_finish_set_aside()` recording the step without looking at the archive -> a
    reset goes on with no copy of the record it was made from.
    """
    mv, vols, plain = cut_off
    server_dir = mv.target.server_dir
    real_finished = start_over._Reset.finished

    class Killed(BaseException):
        pass

    def die_at_set_aside(self: start_over._Reset, step: str) -> None:
        if step == "set-aside":
            raise Killed
        real_finished(self, step)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(start_over._Reset, "finished", die_at_set_aside)
        with pytest.raises(Killed):
            _press(mv, vols, plain)
    reset = json.loads((server_dir / start_over.RESET_FILE).read_text(encoding="utf-8"))
    Path(reset["set_aside_to"]).unlink()
    before = _snapshot(server_dir)
    with pytest.raises(start_over.StartOverError) as refused:
        _press(mv, vols, plain, expected=None)
    assert str(refused.value) == start_over.RECORD_GONE
    assert plain.runs == 0 and _snapshot(server_dir) == before


def test_the_set_aside_record_never_overwrites_a_file(
    cut_off: tuple[Move, Volumes, Plain],
) -> None:
    """Mutation: `os.O_EXCL` dropped (or the name reused) -> the earlier file is overwritten."""
    mv, vols, plain = cut_off
    server_dir = mv.target.server_dir
    record = (server_dir / move_server.MOVE_IN_FILE).read_bytes()
    kept = kept_backups.kept_backups_path(server_dir)
    taken = kept.parent / f"{kept.name}.taken"
    # The backups move to `kept`; the same name at the same second is already there in it.
    stamp = NOW.strftime("%Y%m%dT%H%M%SZ")
    real_keep = purge.keep_backups

    def keep_then_collide(found: purge.BackupsFound, state: purge.KeptState) -> None:
        real_keep(found, state)
        assert state.where is not None
        (state.where / f"{start_over.SET_ASIDE_PREFIX}{stamp}.json").write_text(
            "an earlier one", encoding="utf-8"
        )
        taken.write_text(str(state.where), encoding="utf-8")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(purge, "keep_backups", keep_then_collide)
        _press(mv, vols, plain)
    folder = Path(taken.read_text(encoding="utf-8"))
    first = folder / f"{start_over.SET_ASIDE_PREFIX}{stamp}.json"
    second = folder / f"{start_over.SET_ASIDE_PREFIX}{stamp}-2.json"
    assert first.read_text(encoding="utf-8") == "an earlier one"
    assert second.read_bytes() == record


def test_a_volume_left_under_the_plain_installs_project_stops_the_install(
    cut_off: tuple[Move, Volumes, Plain],
) -> None:
    """The folder's compose project is not the one the plain install will use, and a database
    volume of the latter is there: the Uninstall removes the folder's project only, and the
    check after it refuses, so the plain install never mounts that volume.

    Mutation: the check looks at containers only -> the plain install starts on "moved".
    """
    mv, vols, plain = cut_off
    server_dir = mv.target.server_dir
    planned = f"{vols.project}-planned"
    vols.volumes[f"{planned}{purge.DB_VOLUME_SUFFIX}"] = {s: "moved" for s in SCHEMAS}
    real = vols.seams

    def seams() -> start_over.ResetSeams:
        return replace(real(), projects=lambda: (vols.project, planned))

    with pytest.raises(start_over.StartOverError) as refused:
        _press(mv, vols, plain, seams=seams)
    said = str(refused.value)
    assert f"Docker still has {planned}{purge.DB_VOLUME_SUFFIX}" in said, said
    assert said.endswith(start_over.KEPT_RECORD)
    assert plain.runs == 0
    assert move_server.unfinished_move_in(server_dir)


def test_stop_between_two_steps_before_the_removal_leaves_the_bring_in_as_it_was(
    cut_off: tuple[Move, Volumes, Plain],
) -> None:
    mv, vols, plain = cut_off
    server_dir = mv.target.server_dir
    record = (server_dir / move_server.MOVE_IN_FILE).read_bytes()
    stop = threading.Event()
    stop.set()
    start = len(mv.events)
    engine = start_over.StartOver(server_dir, plain=plain, seams=vols.seams, now=lambda: NOW)
    with pytest.raises(start_over.StartOverError) as stopped:
        list(engine.run(InstallOptions(server_dir=server_dir), cancel=stop))
    assert str(stopped.value) == f"{start_over.STOPPED} {start_over.UNCHANGED}"
    assert (server_dir / move_server.MOVE_IN_FILE).read_bytes() == record
    assert not (server_dir / start_over.RESET_FILE).exists()
    assert plain.runs == 0 and not [e for e in mv.events[start:] if e.startswith(("rm-", "dump:"))]


def test_a_bring_in_stopped_before_its_install_made_anything_starts_over_without_a_copy(
    cut_off: tuple[Move, Volumes, Plain],
) -> None:
    """No install record and nothing in Docker: there is no database, so there is no copy, and
    the folder is emptied (its backups kept beside it) without the Uninstall."""
    mv, vols, plain = cut_off
    server_dir = mv.target.server_dir
    (server_dir / native.STATE_FILE).unlink()
    vols.volumes.clear()
    vols.containers.clear()
    lines = _press(mv, vols, plain)
    assert "This server has no database yet, so there is nothing to copy." in lines
    assert plain.runs == 1 and set(plain.folder_then) <= {
        docker.FOLDER_ID_FILE,
        start_over.RESET_FILE,
    }
    assert not [e for e in mv.events if e.startswith("rm-")]


def test_no_install_record_with_a_volume_still_in_docker_removes_nothing(
    cut_off: tuple[Move, Volumes, Plain],
) -> None:
    """A volume Yu'lon cannot prove is this folder's (no install record): refused, untouched.

    Mutation: `_refuse_removal()` skipped for a folder with no record -> the folder is emptied.
    """
    mv, vols, plain = cut_off
    server_dir = mv.target.server_dir
    (server_dir / native.STATE_FILE).unlink()
    vols.containers.clear()
    mv.target.db.fail_dump_of = None
    with pytest.raises(start_over.StartOverError) as refused:
        _press(mv, vols, plain)
    said = str(refused.value)
    assert said.startswith("This folder has no install record, and Docker still has"), said
    assert vols.db_volume in vols.volumes and plain.runs == 0
    assert (server_dir / move_server.MOVE_IN_FILE).exists()
    assert not (server_dir / start_over.RESET_FILE).exists()
    assert not [e for e in mv.events if e.startswith("rm-")]


# =========================================================== one at a time


def test_a_bring_in_during_a_start_over_is_refused_and_no_record_is_lost(
    cut_off: tuple[Move, Volumes, Plain],
) -> None:
    """Two windows of one Yu'lon: the reset pauses before it reads the record, and again
    between its check and the record's set-aside, and a Bring-in is pressed at each pause.

    The Docker reservation is shared inside one process, so this is the folder count's work
    (`move_server.starting_over`), and then the reset's record.
    Mutation: `MovedInInstall.run` without `bringing_in()` -> the first Bring-in goes ahead
    (folder_refusal sees no reset record yet) and saves its record under the reset.
    """
    mv, vols, plain = cut_off
    server_dir = mv.target.server_dir
    record = (server_dir / move_server.MOVE_IN_FILE).read_bytes()
    installs = mv.engine.runs
    at_begin, at_aside = threading.Event(), threading.Event()
    begin_on, aside_on = threading.Event(), threading.Event()
    real_aside = start_over.StartOver._set_aside

    def seams() -> start_over.ResetSeams:
        at_begin.set()
        assert begin_on.wait(30)
        return vols.seams()

    def paused_aside(self: start_over.StartOver, reset: start_over._Reset) -> Path:
        at_aside.set()
        assert aside_on.wait(30)
        return real_aside(self, reset)

    said: list[object] = []

    def reset() -> None:
        try:
            said.extend(_press(mv, vols, plain, seams=seams))
        except BaseException as exc:  # noqa: BLE001 - the thread's answer, whatever it is
            said.append(exc)

    def bring_in() -> Exception:
        with pytest.raises(MoveError) as refused:
            list(mv.install().run(InstallOptions(server_dir=server_dir)))
        return refused.value

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(start_over.StartOver, "_set_aside", paused_aside)
        worker = threading.Thread(target=reset)
        worker.start()
        try:
            assert at_begin.wait(30)
            first = bring_in()
            assert (server_dir / move_server.MOVE_IN_FILE).read_bytes() == record
            begin_on.set()
            assert at_aside.wait(30)
            second = bring_in()
            assert (server_dir / move_server.MOVE_IN_FILE).read_bytes() == record
        finally:
            begin_on.set()
            aside_on.set()
            worker.join(30)
    assert not worker.is_alive()
    assert str(first) == move_server.STARTING_OVER_NOW
    assert str(second).startswith(move_server.START_OVER_UNFINISHED)
    assert not [s for s in said if isinstance(s, BaseException)], said
    assert plain.runs == 1 and mv.engine.runs == installs
    aside = list(_kept_folder(mv).glob(f"{start_over.SET_ASIDE_PREFIX}*.json"))
    assert [p.read_bytes() for p in aside] == [record], "the bring-in's record was lost"


@pytest.mark.parametrize("where", ["before it names itself", "named in its record"])
def test_a_start_over_during_a_bring_in_of_this_yulon_is_refused(
    tmp_path: Path, where: str
) -> None:
    """Before the bring-in has saved its name, only this process's count knows of it.

    Mutation: `starting_over()` does not look at the bring-ins -> "before it names itself"
    resets under the running press (the record names no press yet).
    """
    mv = _cut_off_move(tmp_path)
    vols = Volumes(mv)
    plain = Plain(vols)
    server_dir = mv.target.server_dir
    inside, leave = threading.Event(), threading.Event()
    real_read = move_server._Record.read.__func__  # type: ignore[attr-defined]
    real_cut_off = move_server._Record.load_cut_off

    def held_read(cls: type, folder: Path, digest: str) -> move_server._Record:
        inside.set()
        assert leave.wait(30)
        return real_read(cls, folder, digest)  # type: ignore[no-any-return]

    def held_inside(self: move_server._Record) -> bool:
        inside.set()
        assert leave.wait(30)
        return real_cut_off(self)

    errors: list[BaseException] = []

    def bring_in() -> None:
        try:
            list(mv.install().run(InstallOptions(server_dir=server_dir)))
        except BaseException as exc:  # noqa: BLE001 - whatever ends it
            errors.append(exc)

    with pytest.MonkeyPatch.context() as patch:
        if where == "named in its record":
            patch.setattr(move_server._Record, "load_cut_off", held_inside)
        else:
            patch.setattr(move_server._Record, "read", classmethod(held_read))
        worker = threading.Thread(target=bring_in)
        worker.start()
        try:
            assert inside.wait(30)
            marker = move_server.read_marker(server_dir)
            assert marker is not None
            if where == "named in its record":
                # The running press is named in its record while it runs.
                assert marker["pressing"]["pid"] == os.getpid()  # type: ignore[index]
            else:
                assert "pressing" not in marker
            before = _snapshot(server_dir)
            with pytest.raises(MoveError) as refused:
                _press(mv, vols, plain, expected=None)
            assert str(refused.value) == move_server.BRINGING_IN_NOW
            assert _snapshot(server_dir) == before
        finally:
            leave.set()
            worker.join(30)
    assert plain.runs == 0 and not [e for e in mv.events if e.startswith("rm-")]
    # The press ended: its name is gone from the record, and a start over may run.
    marker = move_server.read_marker(server_dir)
    assert marker is None or "pressing" not in marker


def test_a_bring_in_still_running_in_another_yulon_refuses_the_start_over(
    cut_off: tuple[Move, Volumes, Plain],
) -> None:
    """The record names a live process on this computer (another Yu'lon's press).

    Mutation: `press_running()` answers None -> the reset removes the folder under it.
    """
    mv, vols, plain = cut_off
    server_dir = mv.target.server_dir
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        marker = move_server.read_marker(server_dir)
        assert marker is not None
        pressing = {
            "pid": child.pid,
            "host": maintenance.this_host(),
            "token": maintenance.process_identity(child.pid)[0],
        }
        (server_dir / move_server.MOVE_IN_FILE).write_text(
            json.dumps({**marker, "pressing": pressing}), encoding="utf-8"
        )
        before = _snapshot(server_dir)
        with pytest.raises(start_over.StartOverError) as refused:
            _press(mv, vols, plain, expected=None)
    finally:
        child.kill()
        child.wait()
    assert "running in another Yu'lon" in str(refused.value)
    assert _snapshot(server_dir) == before
    assert plain.runs == 0
    # Once that process is gone the same record is an ended press, and the reset runs.
    _press(mv, vols, plain, expected=None)
    assert plain.runs == 1


def test_a_bring_in_that_names_itself_after_the_reset_record_stops_the_reset(
    cut_off: tuple[Move, Volumes, Plain], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second look: a bring-in in another Yu'lon that looked before the reset's record was
    written has written its name by the time the reset reads again.

    Mutation: the second `read_marker()` in `_begin()` dropped -> the reset goes on.
    """
    mv, vols, plain = cut_off
    server_dir = mv.target.server_dir
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    real_save = start_over._Reset.save

    def save_then_theirs(self: start_over._Reset) -> None:
        real_save(self)
        if not self.done:
            marker = move_server.read_marker(server_dir)
            assert marker is not None
            pressing = {"pid": child.pid, "host": maintenance.this_host(), "token": ""}
            (server_dir / move_server.MOVE_IN_FILE).write_text(
                json.dumps({**marker, "pressing": pressing}), encoding="utf-8"
            )

    monkeypatch.setattr(start_over._Reset, "save", save_then_theirs)
    try:
        with pytest.raises(start_over.StartOverError, match="running in another Yu'lon"):
            _press(mv, vols, plain, expected=None)
    finally:
        child.kill()
        child.wait()
    assert not (server_dir / start_over.RESET_FILE).exists()
    assert not [e for e in mv.events if e.startswith("rm-")]
    assert plain.runs == 0


def test_a_start_over_that_began_after_the_bring_ins_look_stops_the_bring_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bring-in's half of the second look: its record saved, then the reset record seen.

    Mutation: the `start_over_under_way()` check after `record.save()` dropped -> it installs.
    """
    mv = _cut_off_move(tmp_path)
    server_dir = mv.target.server_dir
    real_save = move_server._Record.save
    calls = {"n": 0}

    def save_then_reset(self: move_server._Record) -> None:
        real_save(self)
        calls["n"] += 1
        if calls["n"] == 1:
            (server_dir / move_server.RESET_FILE).write_text('{"package": "x"}', encoding="utf-8")

    monkeypatch.setattr(move_server._Record, "save", save_then_reset)
    events, installs = list(mv.events), mv.engine.runs
    with pytest.raises(MoveError) as refused:
        list(mv.install().run(InstallOptions(server_dir=server_dir)))
    assert str(refused.value) == f"{move_server.START_OVER_UNFINISHED} Nothing was started."
    assert mv.events == events and mv.engine.runs == installs


def test_another_yulons_reservation_refuses_the_start_over_and_changes_nothing(
    cut_off: tuple[Move, Volumes, Plain], reservations: Path
) -> None:
    """The cross-process reservation is taken first, through the real `docker.server_claim`.

    Mutation: the claim taken after the reset's first step -> the reset record is written.
    """
    from tests.test_controller_reservation import _holds

    mv, vols, plain = cut_off
    server_dir = mv.target.server_dir
    theirs = _holds(reservations, server_dir, press="Bring in a move")
    try:
        before = _snapshot(server_dir)
        with pytest.raises(start_over.StartOverError, match="Another Yu'lon is working on"):
            _press(mv, vols, plain)
    finally:
        theirs.kill()
    assert _snapshot(server_dir) == before
    assert plain.runs == 0


def test_the_start_over_holds_the_reservation_while_it_resets(
    cut_off: tuple[Move, Volumes, Plain], reservations: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Between the check and the set-aside the server is reserved in Docker under this press, so
    another Yu'lon's take of it is refused by the daemon: exactly one goes ahead.

    Mutation: `claim=` bound to a context that reserves nothing -> no container, theirs runs.
    """
    from tests.support_fake_docker import containers as fake_containers

    mv, vols, plain = cut_off
    server_dir = mv.target.server_dir
    seen: dict[str, object] = {}
    real_aside = start_over.StartOver._set_aside

    def look_then_aside(self: start_over.StartOver, reset: start_over._Reset) -> Path:
        name = docker.SERVER_CLAIM_PREFIX + str(docker.folder_id(server_dir))
        seen["ours"] = name in fake_containers(reservations)
        holder = docker.reservation_holder(server_dir)
        seen["press"] = holder.press if holder is not None else None
        theirs = subprocess.run(
            [
                str(reservations.parent / "fake-docker"),
                "run", "--rm", "-i", "--name", name,
                "--entrypoint", "sh", "yulon.local/wotlk-server:native", "-c", "cat >/dev/null",
            ],
            capture_output=True,
            text=True,
            input="",
            timeout=30,
            check=False,
        )  # fmt: skip
        seen["said"] = theirs.stderr
        seen["theirs"] = theirs.returncode
        return real_aside(self, reset)

    monkeypatch.setattr(start_over.StartOver, "_set_aside", look_then_aside)
    _press(mv, vols, plain)
    assert seen["ours"] is True and seen["press"] == start_over.PRESS_START_OVER, seen
    assert seen["theirs"] != 0 and "Conflict" in str(seen["said"]), seen
    assert plain.runs == 1


# =========================================================== refusals, records, words


def test_a_folder_with_a_start_over_record_is_refused_by_bring_in(tmp_path: Path) -> None:
    mv = Move(tmp_path)
    folder = mv.target.server_dir
    folder.mkdir()
    (folder / native.STATE_FILE).write_text("{}", encoding="utf-8")
    move_server.write_marker(folder, move_server.package_digest(mv.plan.manifest), [])  # type: ignore[arg-type]
    (folder / move_server.RESET_FILE).write_text("{}", encoding="utf-8")
    assert plan_for(mv.path, folder).refusals == (move_server.START_OVER_UNFINISHED,)


def test_the_start_over_record_is_one_of_yulons_own_files(tmp_path: Path) -> None:
    from yulon.ui import catalog_view

    assert move_server.RESET_FILE == native.RESET_FILE == catalog_view.RESET_FILE
    assert native.RESET_FILE in native.OUR_OWN_FILES
    (tmp_path / native.RESET_FILE).write_text("{}", encoding="utf-8")
    assert not native._listing(tmp_path, ignoring=native.OUR_OWN_FILES)


def test_a_finished_or_missing_bring_in_has_nothing_to_start_over(
    cut_off: tuple[Move, Volumes, Plain],
) -> None:
    mv, vols, plain = cut_off
    server_dir = mv.target.server_dir
    marker = move_server.read_marker(server_dir)
    assert marker is not None
    move_server.write_marker(server_dir, str(marker["package"]), list(move_server.STEPS))
    with pytest.raises(start_over.StartOverError) as refused:
        _press(mv, vols, plain, expected=None)
    assert str(refused.value) == start_over.NOTHING_TO_START_OVER
    (server_dir / move_server.MOVE_IN_FILE).write_text("not json", encoding="utf-8")
    with pytest.raises(start_over.StartOverError) as unreadable:
        _press(mv, vols, plain, expected=None)
    assert str(unreadable.value) == start_over.RECORD_UNREADABLE
    assert plain.runs == 0 and not (server_dir / start_over.RESET_FILE).exists()


def test_a_record_that_changed_since_the_question_is_refused(
    cut_off: tuple[Move, Volumes, Plain],
) -> None:
    mv, vols, plain = cut_off
    server_dir = mv.target.server_dir
    asked = move_server.read_marker(server_dir)
    assert asked is not None
    move_server.write_marker(server_dir, str(asked["package"]), ["revs"])
    engine = start_over.StartOver(server_dir, plain=plain, seams=vols.seams, expected=asked)
    with pytest.raises(start_over.StartOverError) as refused:
        list(engine.run(InstallOptions(server_dir=server_dir)))
    assert str(refused.value) == start_over.RECORD_CHANGED
    assert plain.runs == 0 and not (server_dir / start_over.RESET_FILE).exists()


SENTENCES = (
    start_over.QUESTION,
    start_over.FINISH_QUESTION,
    start_over.NOTHING_TO_START_OVER,
    start_over.RECORD_UNREADABLE,
    start_over.RECORD_CHANGED,
    start_over.RECORD_GONE,
    start_over.NO_PROJECT,
    start_over.COPY_FAILED,
    start_over.LOST,
    start_over.KEPT_RECORD,
    start_over.UNCHANGED,
    start_over.READY,
    start_over.reset_unreadable(Path("/srv/wow/.yulon-start-over.json")),
    start_over.damaged_copy(Path("/srv/wow/backups/copy.sql")),
    start_over.not_yulons_folder(Path("/srv/wow.yulon-clone-0123abcd")),
    start_over.staging_unmade(Path("/srv/wow"), OSError("no space left on device")),
    native.CONTINUING_START_OVER,
    move_server.START_OVER_UNFINISHED,
    move_server.STARTING_OVER_NOW,
    move_server.BRINGING_IN_NOW,
)


@pytest.mark.parametrize("sentence", SENTENCES)
def test_the_question_and_every_refusal_are_plain(sentence: str) -> None:
    assert text_faults(sentence) == [], sentence
    assert command_faults(sentence) == [], sentence


def test_the_question_says_what_is_removed_and_kept() -> None:
    assert (
        "The unfinished bring-in and everything it loaded into this server will be removed, then "
        "a plain install starts. Nothing from the other computer is kept. A copy of what is "
        "there is kept first."
    ) in start_over.QUESTION
