"""T549, the lead's stop-time item: a claim lost DURING a long change to the folder.

The point-in-time checks cover the instant before each change; some changes take
seconds to minutes -- deleting the old map data or the pathfinding tiles, putting the
old data back over the new partial output. Each is now asked, file by file, whether
to stop (the claim watcher's in-process `lost`, not a Docker call per file), and
stops at the next file boundary in a state the next press settles.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yulon import rmtree
from yulon.catalog.families import extract


def _files(root: Path, count: int, *, sub: str = "maps") -> list[Path]:
    made = []
    for i in range(count):
        path = root / sub / f"{i:04}.map"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"MAPS")
        made.append(path)
    return made


def _stop_after(count: int):  # type: ignore[no-untyped-def]
    """A `stop` that answers True once it has been asked `count` times."""
    asked = [0]

    def stop() -> bool:
        asked[0] += 1
        return asked[0] > count

    return stop


def test_a_stoppable_removal_stops_between_files_and_leaves_only_whole_files(
    tmp_path: Path,
) -> None:
    """Mutation this catches: the removal not asking `stop` per file (everything goes)."""
    tree = tmp_path / "old"
    _files(tree, 5)
    with pytest.raises(rmtree.StoppedPartWay):
        rmtree.remove_tree_stoppably(tree, _stop_after(2))
    left = sorted(path.name for path in (tree / "maps").iterdir())
    assert len(left) == 3, left
    assert all((tree / "maps" / name).read_bytes() == b"MAPS" for name in left)


def test_a_stoppable_removal_with_nothing_to_stop_removes_everything(tmp_path: Path) -> None:
    tree = tmp_path / "old"
    _files(tree, 5)
    assert rmtree.remove_tree_stoppably(tree, lambda: False) is True
    assert not tree.exists()
    assert rmtree.remove_tree_stoppably(tree, lambda: False) is False


def test_a_supersede_stopped_part_way_stays_marked_for_the_next_press(tmp_path: Path) -> None:
    """The mark goes last, so the next press's `drop_superseded()` finishes the deletion.

    Mutation this catches: `supersede()` deleting without asking `stop`.
    """
    data = tmp_path / "data"
    _files(data / extract.PREVIOUS_DIR, 6)
    with pytest.raises(rmtree.StoppedPartWay):
        extract.supersede(data, stop=_stop_after(3))
    assert (data / extract.SUPERSEDED_MARK).is_dir(), "the mark went before the data"
    assert (data / extract.PREVIOUS_DIR).is_dir()
    assert extract.drop_superseded(data) is True  # the next press finishes it
    assert not (data / extract.PREVIOUS_DIR).exists()


def test_a_put_back_stopped_part_way_is_finished_by_the_next_call(tmp_path: Path) -> None:
    """Each name leaves `PREVIOUS_DIR` only once settled, so the next `put_back()` finishes.

    Mutation this catches: `put_back()` clearing the new output without asking `stop`.
    """
    data = tmp_path / "data"
    _files(data, 4, sub="maps")  # the new extraction's partial output
    _files(data / extract.PREVIOUS_DIR, 2, sub="maps")  # the old map data
    (data / extract.PREVIOUS_DIR / "vmaps").mkdir()
    with pytest.raises(rmtree.StoppedPartWay):
        extract.put_back(data, stop=_stop_after(2))
    assert (data / extract.PREVIOUS_DIR).is_dir(), "the old data left aside before it was back"
    extract.put_back(data)
    assert not (data / extract.PREVIOUS_DIR).exists()
    assert sorted(path.name for path in (data / "maps").iterdir()) == ["0000.map", "0001.map"]


def test_a_set_aside_stopped_part_way_is_put_back_by_the_next_press(tmp_path: Path) -> None:
    """Renames, each asked first; the next press's settle puts the moved ones back.

    Mutation this catches: `set_aside()` renaming without asking `stop`.
    """
    data = tmp_path / "data"
    for name in ("maps", "vmaps", "dbc"):
        (data / name).mkdir(parents=True)
        (data / name / "x").write_bytes(b"OLD")
    with pytest.raises(rmtree.StoppedPartWay):
        extract.set_aside(data, ("maps", "vmaps", "dbc"), stop=_stop_after(1))
    moved = sorted(path.name for path in (data / extract.PREVIOUS_DIR).iterdir())
    assert moved == ["maps"], moved
    extract.put_back(data)
    assert sorted(path.name for path in data.iterdir()) == ["dbc", "maps", "vmaps"]


def test_a_put_back_stopped_between_names_is_finished_by_the_next_call(tmp_path: Path) -> None:
    """Mutation this catches: `put_back()` not asking `stop` before each name."""
    data = tmp_path / "data"
    for name in ("maps", "vmaps", "dbc"):  # nothing of the new output in the way
        (data / extract.PREVIOUS_DIR / name).mkdir(parents=True)
    with pytest.raises(rmtree.StoppedPartWay):
        extract.put_back(data, stop=_stop_after(1))
    assert len(list((data / extract.PREVIOUS_DIR).iterdir())) == 2, "one name was put back"
    extract.put_back(data)
    assert sorted(path.name for path in data.iterdir()) == ["dbc", "maps", "vmaps"]


def test_a_stoppable_removal_asks_before_every_directory_it_removes(tmp_path: Path) -> None:
    """Codex normal and adversarial reviews, round 5: a tree of empty folders went unchecked.

    Mutation this catches: `rmdir` without a look at `stop`.
    """
    tree = tmp_path / "old"
    for i in range(6):
        (tree / f"d{i}").mkdir(parents=True)
    with pytest.raises(rmtree.StoppedPartWay):
        rmtree.remove_tree_stoppably(tree, _stop_after(2))
    left = [path for path in tree.iterdir()]
    assert len(left) == 4, left  # two removed, then it stopped
