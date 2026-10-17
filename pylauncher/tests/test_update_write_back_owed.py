"""T783: the Update route and T766's "write-back owed" record (`WRITE_BACK_OWED_FILE`).

"Put the folders back…" writes the record before its first checkout and removes it once its
write-back (compose files and carried patch) is whole; Rebuild refuses while it is there. The
Update route moves the same folders and writes the same files, so:

1. a WHOLE Update -- new build compiled, started and recorded -- leaves nothing owed, and
   removes a record an earlier press left. Not right after its patch step: a later compile or
   start failure rolls the folders back, and that rollback's write-back can fail. A half-way
   Update leaves the record alone;
2. the Update's rollback (`_restore_the_folder()`) moves folders back with a checkout, which
   puts upstream's files over this app's, so it writes the record before its first move and
   removes it -- the one it wrote -- only once its own write-back is whole;
3. the record does not refuse an Update: the Update writes the patch itself before it
   compiles (the check sits inside `rebuild()`'s `servers_down is None`).

Every test drives the real engine through `tests.support_native.Recorder`.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.support_native import ENTRY, Recorder, engine
from tests.test_rebuild_start_records import heads_follow_the_recorder, ready_to_update
from yulon import server_build_presses
from yulon.catalog import native
from yulon.catalog.installer import InstallerError, InstallOptions

PUT_BACK = server_build_presses.PUT_THE_FOLDERS_BACK
CORE = ENTRY.emulator.sources[0]


def _owed(server_dir: Path) -> bool:
    return (server_dir / native.WRITE_BACK_OWED_FILE).exists()


def _updating(tmp_path: Path, *, stale: bool) -> tuple[Recorder, Path]:
    rec, server_dir = ready_to_update(tmp_path)
    heads_follow_the_recorder(rec)
    if stale:
        # An earlier "Put the folders back…" that did not finish left the record.
        assert native.owe_write_back(server_dir) == ""
        assert _owed(server_dir)
    return rec, server_dir


def _patch_fails_on_call(made: Any, failing: int, seen: list[str]) -> None:
    """`apply_carried_patches()` fails on its `failing`-th call (1-based) and works otherwise."""
    carried = made.apply_carried_patches

    def patch(server_dir: Path) -> Iterator[str]:
        seen.append("patch")
        if len(seen) == failing:
            raise OSError("the disk is full")
        yield from carried(server_dir)

    made.apply_carried_patches = patch


def test_a_whole_update_removes_a_stale_record_and_rebuild_goes_on(tmp_path: Path) -> None:
    rec, server_dir = _updating(tmp_path, stale=True)
    made = engine(rec)
    assert made.rebuild_refusal_before_asking(server_dir) is not None, "the ground"

    list(made.update_to_latest(InstallOptions(server_dir=server_dir)))

    assert "build" in rec.calls, rec.calls
    assert not _owed(server_dir), "a whole Update wrote the compose files and the patch"
    assert made.rebuild_refusal_before_asking(server_dir) is None
    assert not native.put_the_folders_back_offered(server_dir)


def test_the_record_does_not_refuse_an_update(tmp_path: Path) -> None:
    """Item 3: Rebuild refuses on the record, the Update route's `rebuild()` does not."""
    rec, server_dir = _updating(tmp_path, stale=True)
    made = engine(rec)
    with pytest.raises(InstallerError):
        list(made.rebuild(InstallOptions(server_dir=server_dir)))
    assert "build" not in rec.calls, "the ground: a plain Rebuild refuses"

    said = list(made.update_to_latest(InstallOptions(server_dir=server_dir)))

    assert "build" in rec.calls, said
    assert rec.calls.count("recreate") == 1, rec.calls


def test_an_update_failing_after_its_patch_step_leaves_a_stale_record(tmp_path: Path) -> None:
    """Item 1: a half-way Update removes nothing, even when its rollback was whole."""
    rec, server_dir = _updating(tmp_path, stale=True)
    rec.ready = False  # compiled, patched, and the new build never comes up: the rollback runs
    seen: list[str] = []
    made = engine(rec)
    _patch_fails_on_call(made, 0, seen)  # never fails

    with pytest.raises(InstallerError):
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))

    assert seen == ["patch", "patch"], "the Update's patch step, then the rollback's"
    assert _owed(server_dir), "an earlier press's record stays until a whole write-back"
    refused = made.rebuild_refusal_before_asking(server_dir)
    assert refused is not None and PUT_BACK in refused, refused


def test_a_rollback_whose_write_back_fails_leaves_the_record(tmp_path: Path) -> None:
    """Item 2: the rollback's checkout put upstream's files back and the patch did not follow."""
    rec, server_dir = _updating(tmp_path, stale=False)
    rec.ready = False
    seen: list[str] = []
    made = engine(rec)
    _patch_fails_on_call(made, 2, seen)  # the Update's own patch works; the rollback's fails

    with pytest.raises(InstallerError):
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))

    assert seen == ["patch", "patch"], seen
    assert _owed(server_dir), "the rollback's write-back is owed"
    assert native.put_the_folders_back_offered(server_dir)
    refused = made.rebuild_refusal_before_asking(server_dir)
    assert refused is not None and PUT_BACK in refused, refused
    rec.calls.clear()
    with pytest.raises(InstallerError) as plain:
        list(made.rebuild(InstallOptions(server_dir=server_dir)))
    assert PUT_BACK in str(plain.value), plain.value
    assert "build" not in rec.calls, rec.calls


def test_a_rollback_writes_the_record_before_its_first_move_and_removes_it_when_whole(
    tmp_path: Path,
) -> None:
    rec, server_dir = _updating(tmp_path, stale=False)
    rec.ready = False
    made = engine(rec)
    at_the_move: list[bool] = []
    put_back = made._put_sources_back

    def moving(moved: Any) -> Any:
        at_the_move.append(_owed(server_dir))
        return (yield from put_back(moved))

    made._put_sources_back = moving

    with pytest.raises(InstallerError):
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))

    assert at_the_move == [True], "written before the first checkout"
    assert not _owed(server_dir), "the rollback's own record goes once its write-back is whole"
    assert made.rebuild_refusal_before_asking(server_dir) is None


def test_a_rollback_whose_record_cannot_be_written_says_so_and_still_moves_the_folders_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 2 (a): not a refusal, unlike the press -- the folders must not stay ahead."""
    rec, server_dir = _updating(tmp_path, stale=False)
    rec.ready = False
    made = engine(rec)
    real = native.owe_write_back
    asked: list[Path] = []

    def unwritable(folder: Path) -> str:
        asked.append(folder)
        if len(asked) == 1:
            return f"{folder / native.WRITE_BACK_OWED_FILE} could not be written (disk full)"
        return real(folder)

    monkeypatch.setattr(native, "owe_write_back", unwritable)
    said: list[str] = []
    with pytest.raises(InstallerError):
        for line in made.update_to_latest(InstallOptions(server_dir=server_dir)):
            said.append(line)

    assert asked == [server_dir], asked
    told = [line for line in said if "could not be written (disk full)" in line]
    assert len(told) == 1 and "nothing will refuse a rebuild" in told[0], said
    restored = [call for call in rec.calls if call.startswith("restore:")]
    assert restored, f"the folders were not moved back: {rec.calls}"
    assert said.index(told[0]) < next(
        i for i, line in enumerate(said) if "was put back on" in line
    ), "said before the first folder moved"
    assert not _owed(server_dir)


def test_a_rollback_with_a_folder_that_did_not_come_back_keeps_the_record(
    tmp_path: Path,
) -> None:
    """Round 2 (b): the write-back is whole only with every folder back."""
    rec, server_dir = _updating(tmp_path, stale=False)
    core = server_dir / CORE.dest
    rec.restore_errors[core] = OSError("the folder is locked")
    rec.ready = False

    with pytest.raises(InstallerError):
        list(engine(rec).update_to_latest(InstallOptions(server_dir=server_dir)))

    assert {row.dest for row in native.sources_still_off(server_dir)} == {core}, "the ground"
    assert _owed(server_dir), "a folder off its build: the write-back is not whole"
    assert native.put_the_folders_back_offered(server_dir)
