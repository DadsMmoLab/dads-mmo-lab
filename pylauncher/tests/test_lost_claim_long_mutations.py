"""T549, the lead's stop-time item: a claim lost DURING a long change to the folder.

The point-in-time checks cover the instant before each change; some changes take
seconds to minutes -- deleting the old map data or the pathfinding tiles, putting the
old data back over the new partial output. Each is now asked, file by file, whether
to stop (the claim watcher's in-process `lost`, not a Docker call per file), and
stops at the next file boundary in a state the next press settles.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from yulon import links, rmtree
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


def test_a_stoppable_removal_refuses_a_root_that_is_a_link_and_leaves_its_target(
    tmp_path: Path,
) -> None:
    """Codex adversarial review, round 6: `os.walk` follows a link given as the root.

    `shutil.rmtree` refuses a top-level link; so must this. Mutation this catches: the
    `islink(path)` refusal removed, which walks into the target and deletes its files.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.map").write_bytes(b"MAPS")
    link = tmp_path / "maps"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:  # pragma: no cover - a Windows account without the link privilege
        pytest.skip("cannot make a symlink here")
    with pytest.raises(OSError, match="symbolic link") as raised:
        rmtree.remove_tree_stoppably(link, lambda: False)
    assert not isinstance(raised.value, rmtree.StoppedPartWay)
    assert (outside / "keep.map").read_bytes() == b"MAPS", "the link's target was walked"


def _junction(monkeypatch: pytest.MonkeyPatch, *paths: Path) -> None:
    """`links.is_link` says yes for exactly `paths`, as for an NTFS junction (a reparse point
    whose tag is a mount point, which `stat.S_ISLNK` and `os.path.islink` call a folder)."""
    real = links._lstat
    named = {os.fspath(path) for path in paths}

    class _Junction:
        st_mode = stat.S_IFDIR | 0o755
        st_file_attributes = links.FILE_ATTRIBUTE_REPARSE_POINT | 0x10
        st_reparse_tag = links.IO_REPARSE_TAG_MOUNT_POINT

    monkeypatch.setattr(
        links,
        "_lstat",
        lambda path, *a, **k: _Junction() if os.fspath(path) in named else real(path, *a, **k),
    )


def test_a_junction_inside_the_tree_is_never_entered_and_its_target_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cold review 2 of T549: `os.walk` and `islink` do not know an NTFS junction, so the
    removal went through `data\\maps -> D:\\shared-maps` and unlinked the target's files.

    A directory the walk would enter is made to read as a junction; its files must stay.
    Mutations this catches: the junction rule dropped from `_is_link`; the leaf decision
    made after the descent instead of before it.
    """
    tree = tmp_path / "old"
    (tree / "maps").mkdir(parents=True)
    (tree / "maps" / "own.map").write_bytes(b"MAPS")
    shared = tree / "shared"
    shared.mkdir()
    keep = [shared / f"keep-{i}.bin" for i in range(5)]
    for path in keep:
        path.write_bytes(b"SHARED")
    _junction(monkeypatch, shared)
    with pytest.raises(OSError):  # a real junction would just go; this one holds files
        rmtree.remove_tree_stoppably(tree, lambda: False)
    assert all(path.read_bytes() == b"SHARED" for path in keep), "the junction's target was walked"


def test_a_junction_given_as_the_root_is_refused_and_its_target_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation this catches: the root checked with `islink` alone."""
    target = tmp_path / "shared"
    target.mkdir()
    (target / "keep.map").write_bytes(b"MAPS")
    _junction(monkeypatch, target)
    with pytest.raises(OSError, match="junction") as raised:
        rmtree.remove_tree_stoppably(target, lambda: False)
    assert not isinstance(raised.value, rmtree.StoppedPartWay)
    assert (target / "keep.map").read_bytes() == b"MAPS"


def test_a_stoppable_removal_asks_before_every_link_it_unlinks(tmp_path: Path) -> None:
    """T567: nothing asked `stop` before a link went, and every test stayed green.

    Mutation this catches: the `stop()` check dropped from `_empty_stoppably`'s link branch.
    """
    outside = tmp_path / "outside.map"
    outside.write_bytes(b"MAPS")
    tree = tmp_path / "old"
    tree.mkdir()
    for i in range(6):
        try:
            (tree / f"link{i}").symlink_to(outside)
        except OSError:  # pragma: no cover - a Windows account without the link privilege
            pytest.skip("cannot make a symlink here")
    with pytest.raises(rmtree.StoppedPartWay):
        rmtree.remove_tree_stoppably(tree, _stop_after(2))
    assert len(list(tree.iterdir())) == 4, "two links removed, then it stopped"
    assert outside.read_bytes() == b"MAPS"


def test_a_path_that_cannot_be_looked_at_counts_as_a_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T567: `_is_link` answers True when the look fails, so the removal touches only that name.

    Mutation this catches: `_is_link` answering False on an `OSError`.
    """

    def unreadable(path: object) -> bool:
        raise PermissionError(13, "cannot look", os.fspath(path))  # type: ignore[arg-type]

    monkeypatch.setattr(links, "is_link", unreadable)
    assert rmtree._is_link(os.fspath(tmp_path)) is True


def test_a_folder_that_cannot_be_looked_at_is_not_entered_by_the_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same rule end to end: the unreadable folder's files are not walked and deleted."""
    tree = tmp_path / "old"
    mystery = tree / "mystery"
    mystery.mkdir(parents=True)
    keep = mystery / "keep.map"
    keep.write_bytes(b"MAPS")
    real = links.is_link

    def look(path: str | os.PathLike[str]) -> bool:
        if os.fspath(path) == os.fspath(mystery):
            raise PermissionError(13, "cannot look", os.fspath(path))
        return real(path)

    monkeypatch.setattr(links, "is_link", look)
    with pytest.raises(OSError):  # the folder is not empty, so it is not removed as a link
        rmtree.remove_tree_stoppably(tree, lambda: False)
    assert keep.read_bytes() == b"MAPS", "a folder that could not be looked at was walked"


# -- `remove_tree`, the plain removal: what its retry may reach (T705: R1, R2) -------------


def _a_target_with_a_read_only_file_and_a_locked_folder(target: Path) -> Path:
    """A folder the retry would change if it went there: a read-only file, a folder no walk enters.

    `_clear_read_only()` adds the write bit to the file; `_remove_unenterable()` removes the
    empty folder it cannot list. Returns the file.
    """
    target.mkdir()
    keep = target / "keep.map"
    keep.write_bytes(b"MAPS")
    keep.chmod(0o444)
    (target / "locked").mkdir()
    (target / "locked").chmod(0)
    return keep


def _unlock(target: Path) -> None:
    for path in (target / "locked", target / "keep.map"):
        if path.exists():
            path.chmod(0o755)


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes stand for the read-only flag here")
def test_remove_tree_refuses_a_root_that_is_a_symlink_and_leaves_its_target(
    tmp_path: Path,
) -> None:
    """Mutation this catches (R1): the root-link refusal removed.

    `shutil.rmtree` refuses a link root, and the retry then went through it: the target's
    read-only file was made writable and its locked folder removed.
    """
    target = tmp_path / "shared"
    keep = _a_target_with_a_read_only_file_and_a_locked_folder(target)
    link = tmp_path / "server"
    link.symlink_to(target, target_is_directory=True)
    try:
        with pytest.raises(rmtree.TreeRemovalError):
            rmtree.remove_tree(link)
        assert stat.S_IMODE(keep.stat().st_mode) == 0o444, "the retry chmodded through the link"
        assert (target / "locked").is_dir(), "the retry removed a folder through the link"
        assert link.is_symlink()
    finally:
        _unlock(target)


def test_remove_tree_refuses_a_junction_given_as_the_root_and_its_target_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T705: the root was checked with `Path.is_symlink()`, which calls a junction a folder.

    On Windows `shutil.rmtree` refuses the junction and the retry's walks went through it.
    Here the stand-in junction is a real folder to Linux's `shutil.rmtree`, so the same
    missing check deletes it outright: either way the target must stay whole.
    """
    target = tmp_path / "shared"
    target.mkdir()
    (target / "keep.map").write_bytes(b"MAPS")
    _junction(monkeypatch, target)
    with pytest.raises(rmtree.TreeRemovalError, match="junction"):
        rmtree.remove_tree(target)
    assert (target / "keep.map").read_bytes() == b"MAPS"


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes stand for the read-only flag here")
def test_the_retry_never_clears_a_flag_through_a_junction_inside_the_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T705: `_clear_read_only()` walked with `os.walk`, which enters a junction on 3.11.

    Asked of the helper itself: Linux's `shutil.rmtree` does not know the stand-in junction,
    so the first, plain delete would empty it before any retry ran. On Windows that delete
    leaves a junction's target alone and the retry is the step that went through.
    """
    tree = tmp_path / "server"
    tree.mkdir()
    shared = tree / "shared"
    keep = _a_target_with_a_read_only_file_and_a_locked_folder(shared)
    _junction(monkeypatch, shared)
    try:
        rmtree._clear_read_only(tree)
        assert stat.S_IMODE(keep.stat().st_mode) == 0o444, "a flag was cleared through it"
    finally:
        _unlock(shared)


@pytest.mark.skipif(
    os.name == "nt" or getattr(os, "geteuid", lambda: 0)() == 0,
    reason="a mode-0 folder is unlistable to a user only",
)
def test_the_retry_never_removes_a_folder_through_a_junction_inside_the_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T705: `_remove_unenterable()` scandirred into a junction and `rmdir`-ed on its far side."""
    tree = tmp_path / "server"
    tree.mkdir()
    shared = tree / "shared"
    _a_target_with_a_read_only_file_and_a_locked_folder(shared)
    _junction(monkeypatch, shared)
    try:
        rmtree._remove_unenterable(tree)
        assert (shared / "locked").is_dir(), "a folder behind the junction was removed"
    finally:
        _unlock(shared)


def test_a_folder_still_there_after_the_delete_is_never_reported_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation this catches (R2): the "still there afterwards" check removed.

    `shutil.rmtree` answers and the folder is back: on Windows a file another program holds
    is only marked for deletion, and a container writing to a bind mount makes the folder
    again. The delete runs for real; the folder is made again right after it.
    """
    import shutil

    tree = tmp_path / "server"
    (tree / "src").mkdir(parents=True)
    real = shutil.rmtree

    def deleted_then_made_again(path: Path) -> None:
        real(path)
        path.mkdir()

    monkeypatch.setattr(shutil, "rmtree", deleted_then_made_again)
    with pytest.raises(rmtree.TreeRemovalError, match="still there afterwards"):
        rmtree.remove_tree(tree)


# -- T705 review notes: an unreadable root, a real symlink inside the tree ------------------

_POSIX_USER = pytest.mark.skipif(
    os.name == "nt" or getattr(os, "geteuid", lambda: 0)() == 0,
    reason="POSIX modes refuse a user only",
)


@_POSIX_USER
def test_a_root_that_cannot_be_looked_at_is_refused_as_unexamined_not_as_a_link(
    tmp_path: Path,
) -> None:
    """A root whose `lstat` fails with EACCES is not a link: the refusal says what is true."""
    parent = tmp_path / "locked"
    tree = parent / "server"
    (tree / "src").mkdir(parents=True)
    parent.chmod(0o600)  # no search bit: nothing under it can be looked at
    try:
        with pytest.raises(rmtree.TreeRemovalError) as refused:
            rmtree.remove_tree(tree)
    finally:
        parent.chmod(0o755)
    said = str(refused.value)
    assert "could not be examined" in said, said
    assert "symbolic link" not in said, said
    assert (tree / "src").is_dir(), "still refused: nothing was deleted"


@_POSIX_USER
def test_the_retry_never_clears_a_flag_through_a_real_symlink_inside_the_tree(
    tmp_path: Path,
) -> None:
    """A real POSIX symlink to a read-only file outside, in a folder the first delete cannot empty.

    The plain `shutil.rmtree` fails on the write-protected folder and leaves the link in it;
    the retry's flag clearing then meets the link. `os.walk` + `os.chmod` (which follows
    links) made the OUTSIDE file writable; the retry must clear the link's folder only.
    """
    outside = tmp_path / "elsewhere" / "realmlist.wtf"
    outside.parent.mkdir()
    outside.write_bytes(b"KEEP")
    outside.chmod(0o444)
    tree = tmp_path / "server"
    held = tree / "held"
    held.mkdir(parents=True)
    (held / "realmlist.wtf").symlink_to(outside)
    held.chmod(0o555)  # the first delete cannot unlink the link
    try:
        rmtree.remove_tree(tree)
        assert not tree.exists() and not os.path.lexists(tree)
        assert stat.S_IMODE(outside.stat().st_mode) == 0o444, "chmodded through the link"
        assert outside.read_bytes() == b"KEEP"
    finally:
        if held.exists():
            held.chmod(0o755)
        outside.chmod(0o644)
