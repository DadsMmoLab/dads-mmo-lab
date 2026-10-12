"""T758: a source folder still off its build (`SOURCES_OFF_FILE`) on the Update/Return and
Rebuild routes.

An earlier press could not put a folder back, or Yu'lon died mid-compile: the record names
the commit the running build came from, the folder sits elsewhere, and every Start refuses
(T217 (a): the old world server applies the database updates it finds in that folder and
crash-loops). Three ways round that refusal, closed here:

* an Update or Return whose new build failed rolled back to the folder's HEAD -- the stray
  commit -- and started the old build on it. The put-back now aims at the commit the record
  names, so the rollback starts the old build on its own sources; and the rollback's start
  asks the record too, for a folder this press did not move;
* a Rebuild with no `source_revs` (every install until its first Update succeeds) compared
  the folder with the catalog pin, called it "an older pin", compiled it, and forgot the
  record: the new core on the old databases without the update's database work (T220);
* with the scripts record (`SCRIPTS_NOT_BACK`) beside it, Return and Update were refused by
  that record, which names Rebuild, and Rebuild by the folder, which names Update: only a hand
  `git checkout` got out.

Every test drives the real engine through `tests.support_native.Recorder`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support_native import ENTRY, Recorder, engine
from tests.test_rebuild_start_records import NEW, OLD, heads_follow_the_recorder, ready_to_update
from yulon import server_build_presses
from yulon.catalog import native
from yulon.catalog.installer import InstallerError, InstallOptions

STRAY = "c" * 40
CORE = ENTRY.emulator.sources[0]


def _left_off(server_dir: Path, rec: Recorder, head: str) -> Path:
    """An earlier press left the core folder on `head`; the running build is OLD's."""
    core = server_dir / CORE.dest
    rec.heads[core] = head
    (core / ".git" / "HEAD").write_text(head + "\n")
    assert native.remember_sources_off(server_dir, [(CORE.repo, core, OLD)]) == ""
    assert native.sources_off_refusal(server_dir) is not None, "the ground: Start refuses"
    return core


# -- the Update/Return rollback ----------------------------------------------------------


def test_an_update_rollback_puts_a_folder_left_off_back_on_its_build_and_starts_it_there(
    tmp_path: Path,
) -> None:
    rec, server_dir = ready_to_update(tmp_path)
    heads_follow_the_recorder(rec)
    core = _left_off(server_dir, rec, STRAY)
    rec.ready = False  # the new build does not come up: the rollback runs

    with pytest.raises(InstallerError) as raised:
        list(engine(rec).update_to_latest(InstallOptions(server_dir=server_dir)))

    assert f"restore:{core.name}->{OLD[:7]}" in rec.calls, rec.calls
    assert native.read_head_file(core) == OLD, "back on the commit the old build was made from"
    assert native.sources_off_refusal(server_dir) is None, raised.value
    assert not (server_dir / native.SOURCES_OFF_FILE).exists()
    # So the old build that was started is on its own sources, not on STRAY.
    assert rec.calls.count("recreate") == 2, rec.calls


def test_an_update_whose_put_back_fails_leaves_the_old_build_stopped_and_the_record(
    tmp_path: Path,
) -> None:
    rec, server_dir = ready_to_update(tmp_path)
    heads_follow_the_recorder(rec)
    core = _left_off(server_dir, rec, STRAY)
    rec.restore_errors[core] = OSError("the folder is locked")
    rec.ready = False

    said: list[str] = []
    with pytest.raises(InstallerError) as raised:
        for line in engine(rec).update_to_latest(InstallOptions(server_dir=server_dir)):
            said.append(line)

    assert rec.calls.count("recreate") == 1, "the old build was started on a folder off it"
    assert "STOPPED" in str(raised.value), raised.value
    assert [line for line in said if f"could NOT be put back on {OLD[:7]}" in line], said
    assert {row.built for row in native.sources_still_off(server_dir)} == {OLD}


def test_a_rollback_does_not_start_the_old_build_while_a_folder_it_did_not_move_is_off(
    tmp_path: Path,
) -> None:
    """The rollback's own start asks the record, as every Start does (T217 (a))."""
    rec, server_dir = ready_to_update(tmp_path)
    heads_follow_the_recorder(rec)
    stray = server_dir / "elsewhere"
    (stray / ".git").mkdir(parents=True)
    (stray / ".git" / "HEAD").write_text(STRAY + "\n")
    assert native.remember_sources_off(server_dir, [("other/repo", stray, "d" * 40)]) == ""
    rec.ready = False

    with pytest.raises(InstallerError) as raised:
        list(engine(rec).update_to_latest(InstallOptions(server_dir=server_dir)))

    assert rec.calls.count("recreate") == 1, f"the old build was started: {raised.value}"
    refusal = native.sources_off_refusal(server_dir)
    assert refusal is not None and refusal in str(raised.value), raised.value
    assert "STOPPED" in str(raised.value), raised.value


# -- a Rebuild with no `source_revs` ------------------------------------------------------


def test_a_first_rebuild_refuses_a_folder_the_sources_off_record_names(tmp_path: Path) -> None:
    rec, server_dir = ready_to_update(tmp_path)
    heads_follow_the_recorder(rec)
    rec.images = True
    core = _left_off(server_dir, rec, NEW)
    made = engine(rec)
    state = native.read_state(server_dir, valid=made.stage_names())
    assert state is not None and state.rev_for(CORE.repo) is None, "the ground: no source_revs"

    asked = made.rebuild_refusal_before_asking(server_dir)
    with pytest.raises(InstallerError) as refused:
        list(made.rebuild(InstallOptions(server_dir=server_dir)))

    expected = native.source_off_its_build(core, NEW, OLD)
    assert str(refused.value) == expected
    assert asked == expected, "the question is not asked before a press that refuses"
    assert "build" not in rec.calls, rec.calls
    assert (server_dir / native.SOURCES_OFF_FILE).exists(), "Start stays refused"


def test_a_first_rebuild_goes_on_once_the_folder_is_back_on_its_build(tmp_path: Path) -> None:
    rec, server_dir = ready_to_update(tmp_path)
    heads_follow_the_recorder(rec)
    rec.images = True
    core = _left_off(server_dir, rec, NEW)
    rec.heads[core] = OLD
    (core / ".git" / "HEAD").write_text(OLD + "\n")
    made = engine(rec)

    assert made.rebuild_refusal_before_asking(server_dir) is None
    list(made.rebuild(InstallOptions(server_dir=server_dir)))
    assert "build" in rec.calls


# -- the loop: the scripts record beside a folder off its build ----------------------------


def _the_loop(tmp_path: Path) -> tuple[Recorder, Path, Path]:
    rec, server_dir = ready_to_update(tmp_path)
    heads_follow_the_recorder(rec)
    rec.images = True
    core = _left_off(server_dir, rec, NEW)
    assert native.owe_start(server_dir, why=native.SCRIPTS_NOT_BACK) == ""
    made = engine(rec)
    assert native.owed_start_refusal(server_dir) == native.SCRIPTS_NOT_BACK_REFUSAL
    rebuild = server_build_presses.under_server_build(server_build_presses.REBUILD)
    assert rebuild in native.SCRIPTS_NOT_BACK_REFUSAL, "the scripts record names Rebuild"
    refused = made.rebuild_refusal_before_asking(server_dir)
    assert refused is not None, "Rebuild refuses the folder"
    assert server_build_presses.UPDATE_TO_LATEST in refused, "and names Update"
    return rec, server_dir, core


@pytest.mark.parametrize("to_pin", [False, True], ids=["update", "return"])
def test_update_and_return_get_out_of_the_scripts_record_and_a_folder_off(
    tmp_path: Path, to_pin: bool
) -> None:
    rec, server_dir, _core = _the_loop(tmp_path)

    list(engine(rec).update_to_latest(InstallOptions(server_dir=server_dir), to_pin=to_pin))

    assert "build" in rec.calls
    assert native.owed_start_refusal(server_dir) is None
    assert native.sources_off_refusal(server_dir) is None


def test_a_return_that_fails_in_the_loop_leaves_rebuild_as_the_way_out(tmp_path: Path) -> None:
    rec, server_dir, core = _the_loop(tmp_path)
    rec.ready = False

    with pytest.raises(InstallerError) as raised:
        list(engine(rec).update_to_latest(InstallOptions(server_dir=server_dir), to_pin=True))

    assert rec.calls.count("recreate") == 1, "the old build is not started under the record"
    assert native.SCRIPTS_NOT_BACK_REFUSAL in str(raised.value), raised.value
    assert native.read_head_file(core) == OLD
    assert native.sources_off_refusal(server_dir) is None
    assert (
        engine(rec).rebuild_refusal_before_asking(server_dir) is None
    ), "the press the scripts record names is no longer refused"


def test_the_scripts_record_alone_still_refuses_an_update(tmp_path: Path) -> None:
    """With every folder on its build, Rebuild is the way out the record names, as before."""
    rec, server_dir = ready_to_update(tmp_path)
    assert native.owe_start(server_dir, why=native.SCRIPTS_NOT_BACK) == ""

    with pytest.raises(InstallerError) as refused:
        list(engine(rec).update_to_latest(InstallOptions(server_dir=server_dir)))

    assert str(refused.value).startswith(native.SCRIPTS_NOT_BACK_REFUSAL)
    assert not [call for call in rec.calls if call.startswith("clone:")], rec.calls
