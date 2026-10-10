"""T646: the update's question tells the truth about kept copies, and a restore names its time.

T633 (owner, 2026-10-09): older database copies go only after an update succeeds, so every
failed update keeps its copy set. The question said "Yu'lon keeps only the newest", true only
after a success; and a rollback's put-back of a world database (~1 minute per 150 MB) carried
no duration.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.support_native import FakeSnapshot, Recorder
from tests.test_update_rollback_database import TORTOISE, _old_build_comes_back, _press
from tests.test_update_to_latest import _gated, _spine  # noqa: F401 - `_gated` is autouse
from yulon import install_wiring
from yulon.catalog import native, snapshot

MB = 1_048_576
RULE = "kept until an update succeeds"
CLEAN_UP = "Maintenance → Clean up…"


def _lay(server_dir: Path, name: str, size: int) -> None:
    folder = server_dir / snapshot.BACKUPS_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b"")
    os.truncate(path, size)  # sparse: only its size is read


def _kept(server_dir: Path, megabytes: int) -> None:
    _lay(server_dir, "20261001_100000_before-new-build_tw_world.sql", megabytes * MB // 2)
    _lay(server_dir, "20261001_100000_after-new-build_tw_world.sql", megabytes * MB // 2)
    # A backup the player took is not an update copy and is not counted.
    _lay(server_dir, "20261002_100000_tw_world.sql", 900 * MB)


def test_the_update_question_says_when_copies_are_kept_and_how_to_clear_them(
    tmp_path: Path,
) -> None:
    route = install_wiring.update_to_latest_for_app(TORTOISE, tmp_path)
    assert route is not None
    text = route.confirmation()
    assert "keeps only the newest" not in text, text
    assert RULE in text, text
    assert CLEAN_UP in text, text


def test_the_update_question_shows_what_the_kept_copies_take_when_it_is_large(
    tmp_path: Path,
) -> None:
    _kept(tmp_path, 1200)
    route = install_wiring.update_to_latest_for_app(TORTOISE, tmp_path)
    assert route is not None
    text = route.confirmation()
    assert "1.2 GB" in text, text


def test_the_update_question_says_no_size_when_the_kept_copies_are_small(tmp_path: Path) -> None:
    _kept(tmp_path, 400)
    route = install_wiring.update_to_latest_for_app(TORTOISE, tmp_path)
    assert route is not None
    text = route.confirmation()
    assert "400.0 MB" not in text and "already" not in text, text


def test_the_return_question_says_the_rule_and_the_size_when_the_copies_are_large(
    tmp_path: Path,
) -> None:
    _kept(tmp_path, 1200)
    route = install_wiring.update_to_latest_for_app(TORTOISE, tmp_path)
    assert route is not None
    text = route.pin_confirmation()
    assert "1.2 GB" in text and RULE in text and CLEAN_UP in text, text


def test_the_return_question_adds_nothing_when_the_copies_are_small(tmp_path: Path) -> None:
    route = install_wiring.update_to_latest_for_app(TORTOISE, tmp_path)
    assert route is not None
    text = route.pin_confirmation()
    assert "Clean up" not in text and RULE not in text, text


def test_the_kept_size_counts_update_copies_and_their_safety_copies_only(tmp_path: Path) -> None:
    _kept(tmp_path, 100)
    assert snapshot.kept_copies_bytes(tmp_path / snapshot.BACKUPS_FOLDER) == 100 * MB
    assert snapshot.kept_copies_bytes(tmp_path / "missing") == 0


@pytest.mark.parametrize(
    ("size", "said"),
    [
        (2 * MB, "less than a minute"),
        (144 * MB, "at least about 1 minute"),
        (238 * MB, "at least about 2 minutes"),
        (900 * MB, "at least about 6 minutes"),
    ],
)
def test_the_restore_line_names_a_rough_duration_from_the_size(size: int, said: str) -> None:
    copy = snapshot.Snapshot(Path("/b"), (Path("/b/x_tw_world.sql"),), ("tw_world",), size)
    assert said in native.copy_putting_back_line(copy)


def test_a_rollback_says_how_long_the_put_back_takes(tmp_path: Path) -> None:
    class Big(FakeSnapshot):
        def take(self, server_dir: Path, databases: object) -> snapshot.Snapshot:
            made = super().take(server_dir, databases)  # type: ignore[arg-type]
            big = snapshot.Snapshot(made.directory, made.files, made.databases, 238 * MB)
            self.taken[-1] = big
            return big

    def make(rec: Recorder) -> FakeSnapshot:
        return Big(rec)

    _rec, _dir, _made, _fake, said, raised = _press(
        tmp_path, TORTOISE, copy=make, wait_ready=_old_build_comes_back()
    )
    assert raised is not None
    putting = [line for line in said if line.startswith("Putting")]
    assert putting and "at least about 2 minutes" in putting[0], said
