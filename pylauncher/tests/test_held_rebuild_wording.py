"""T743: what a held Rebuild and an update's left-stopped rollback say (W1-W4).

Found by the T739+T741 live proof on yulon-ubuntu2 (2026-10-12,
.notes/gates/live-t739-t741-yulon-ubuntu2-2026-10-12/): the behaviour was right and the
words were not. A held press (an import that never finished) printed the generic Rebuild
opening ("waits for the server to come back up ... DOWN ... until it reports ready"), a
rollback that "is put back automatically" if the new build "does not come up", and "Step
3 of 3 (100%): ready" for a stage it skips (W1); it said "The old servers' containers were
removed" with none there (W2); the update rollback it left stopped closed on "the old build
starts on the databases it knows" and "it stays stopped until its world tables are in"
(TrinityCore's reason, on AzerothCore) (W3); and the held question promised "the build you
have now stays as it is" to a server whose build is gone (W4).

Each test drives the real `rebuild()`, `update_to_latest()` or `rebuild_confirmation()`
through T739's own machine (`tests.test_unfinished_import_never_starts`).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.support_native import ENTRY
from tests.test_ended_import_is_not_trusted import _done, _Server
from tests.test_unfinished_import_never_starts import (
    AUTH,
    CHARACTERS,
    FAILED_IMPORT,
    KILLED,
    SERVICE,
    WORLD,
    _crashed_first_import,
    _machine,
    _press,
    _updatable,
)
from tests.test_update_to_latest import _gated  # noqa: F401 - autouse: CMaNGOS engines' gate
from yulon import docker
from yulon.catalog import native
from yulon.catalog.installer import InstallOptions, rebuild_confirmation

STEP = re.compile(r"^Step (\d+) of (\d+) \(\d+%\): (\S+)$")


def _held_press(tmp_path: Path, *, gone: bool) -> list[str]:
    server = _Server({})
    rec, server_dir, made, _fake = _machine(tmp_path, server)
    _crashed_first_import(server_dir, server)
    if gone:
        rec.images = False  # the build is gone, and (the default) no container is left
    rec.ready = False
    said = _press(made, server_dir, missing_images_ok=gone)
    assert said[-1] == native.held_rebuild_ending(ENTRY.name, server_dir), said[-3:]
    return said


@pytest.mark.parametrize("gone", [False, True], ids=["build-there", "build-gone"])
def test_a_held_press_never_says_it_starts_waits_or_rolls_a_start_back(
    tmp_path: Path, gone: bool
) -> None:
    """W1: nothing is started, so nothing comes back up, reports ready or is put back for it."""
    said = "\n".join(_held_press(tmp_path, gone=gone))

    for claim in (
        "come back up",
        "until it reports ready",
        "does not come up",
        "so the new build is what starts",
    ):
        assert claim not in said, (claim, said)


@pytest.mark.parametrize("gone", [False, True], ids=["build-there", "build-gone"])
def test_a_held_press_counts_only_the_steps_it_runs(tmp_path: Path, gone: bool) -> None:
    """W1: "Step 3 of 3 (100%): ready" was printed for a stage the held press skips."""
    said = _held_press(tmp_path, gone=gone)

    steps = [m.groups() for m in map(STEP.match, said) if m]
    assert "ready" not in [name for _n, _total, name in steps], steps
    assert "--- ready" not in said, said
    totals = {int(total) for _n, total, _name in steps}
    assert totals == {len(steps)}, steps


def test_a_held_press_over_no_containers_does_not_say_it_removed_some(tmp_path: Path) -> None:
    """W2: the build gone and no container left, as on the box: nothing to have removed."""
    said = "\n".join(_held_press(tmp_path, gone=True))

    assert "containers were removed" not in said, said


def test_a_left_stopped_update_rollback_names_neither_a_start_nor_world_tables(
    tmp_path: Path,
) -> None:
    """W3: AzerothCore, T658's record, the update's import fails, the copy goes back and the
    old build is left STOPPED for the unfinished import, not for world tables."""
    server = _Server({AUTH: _done("account"), CHARACTERS: _done("characters"), WORLD: _done("x")})
    rec, server_dir, made, fake = _machine(tmp_path, server)
    _updatable(rec, server_dir)
    docker.one_shot_ended_marker(server_dir, SERVICE).write_text(KILLED, encoding="utf-8")
    rec.one_shot_result = FAILED_IMPORT

    with pytest.raises(native.ServersLeftStopped) as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))

    said = str(raised.value)
    assert fake.put_back_calls and "left STOPPED" in said, "the ground: put back, stopped"
    assert native.ENDED_IMPORT_REFUSAL in said, said
    assert "world tables" not in said, said
    assert "starts on the databases" not in said, said


def test_the_held_question_promises_no_build_it_cannot_see(tmp_path: Path) -> None:
    """W4: the question is asked on the GUI thread and reads no image, so it cannot know a
    build is there; under the record it must not promise one stays."""
    server = _Server({})
    _rec, server_dir, _made, _fake = _machine(tmp_path, server)
    _crashed_first_import(server_dir, server)
    asked = rebuild_confirmation(ENTRY, server_dir)

    assert native.HELD_CONFIRMATION in asked, "the ground: the held question"
    assert "build you have now" not in asked, asked
