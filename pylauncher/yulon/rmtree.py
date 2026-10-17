"""Deleting a directory tree on a filesystem that argues about it.

A leaf module on purpose: `os`, `shutil`, `stat`, `pathlib` and the logger, and
nothing of Yu'lon's. It exists because the retry below was written inside
`purge.py`, where the four other callers that needed it could not reach it --
`purge` transitively imports `apply` and `git`, so `apply -> purge` is a cycle.
A user on Windows found that out the hard way (T49):

    remove sod FAILED: [WinError 5] Access is denied:
    'C:\\wow-server-playerbots\\ale_scripts\\sod\\.git\\objects\\pack\\pack-2623....idx'

The MECHANISM lives here. The SENTENCE stays with each caller: `purge` tells a
user that everything else in the uninstall plan is already gone, and that is
true of an uninstall and false of removing one module.
"""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Callable, Sequence
from pathlib import Path

from yulon import links
from yulon.log import get_logger

logger = get_logger(__name__)


class TreeRemovalError(OSError):
    """A directory tree could not be deleted. The message names the path and the cause.

    An `OSError` rather than a bare `RuntimeError` so a caller that already
    catches `OSError` around a delete keeps catching this one, and the two
    `shutil.rmtree` failures it wraps stay the same kind of thing they were.
    """


class LinkRootRefused(TreeRemovalError):
    """The root given to `remove_tree` is a symbolic link or junction (T721).

    A subclass so a caller tells this refusal by type, never by its wording.
    """


class StoppedPartWay(Exception):
    """A removal or a move its `stop` ended between two files (T549); every file left is whole."""


def _is_link(path: str) -> bool:
    """Is `path` a symbolic link or a junction: something that points out of the tree?

    `yulon.links` has the one rule (T375): on Windows a junction is a folder to
    `os.path.islink()`, `DirEntry.is_symlink()` and `os.walk()`, so a walk that trusts
    them goes THROUGH it and works on the target. A path that cannot be looked at
    counts as a link, the cautious answer: the removal then touches only that name.
    """
    try:
        return links.is_link(path)
    except OSError:
        return True


def _unlink_link(path: str) -> None:
    """Remove a link or junction itself, never what it points at."""
    try:
        os.unlink(path)
    except OSError:
        # A directory link on Windows can need `rmdir`, which removes the link and
        # refuses (never empties) a real directory.
        os.rmdir(path)


def remove_link(path: str) -> None:
    """Remove a link or junction itself, never what it points at (T677)."""
    _unlink_link(path)


def _empty_stoppably(path: str, root: Path, stop: Callable[[], bool]) -> None:
    """Remove everything under `path`, asking `stop()` before each unlink and each rmdir."""
    with os.scandir(path) as listing:
        entries = list(listing)
    for entry in entries:
        full = entry.path
        if _is_link(full):
            # A leaf, whatever it points at (files, folders, a junction to another disk):
            # only the link goes.
            if stop():
                raise StoppedPartWay(f"the removal of {root} was stopped part way")
            _unlink_link(full)
        elif entry.is_dir(follow_symlinks=False):
            _empty_stoppably(full, root, stop)
            if stop():
                raise StoppedPartWay(f"the removal of {root} was stopped part way")
            os.rmdir(full)
        else:
            if stop():
                raise StoppedPartWay(f"the removal of {root} was stopped part way")
            os.unlink(full)


def remove_tree_stoppably(path: Path, stop: Callable[[], bool]) -> bool:
    """`shutil.rmtree(path)` file by file, asking `stop()` before each; False when nothing there.

    For a long change to a folder a press may lose its claim on during it (T549):
    the claim watcher's flag is asked per file -- in process, no Docker call -- and
    the removal stops at the next file boundary. A symbolic link or an NTFS junction
    inside is removed as a link and never entered, so what it points at is untouched
    (`os.walk` does not know a junction; this does). A link or junction given as the
    ROOT is refused, as `shutil.rmtree` refuses one.

    Raises:
        StoppedPartWay: `stop()` answered True; what is left is whole files.
        OSError: as `shutil.rmtree` would, and for a root that is a link or junction.
    """
    if not os.path.lexists(path):
        return False
    if _is_link(os.fspath(path)):
        raise OSError(f"Cannot remove {path}: it is a symbolic link or junction, not a folder")
    _empty_stoppably(os.fspath(path), path, stop)
    if stop():
        raise StoppedPartWay(f"the removal of {path} was stopped part way")
    os.rmdir(path)
    return True


def remove_tree(path: Path) -> None:
    """Delete a directory tree, or raise. Never silently partial.

    The plain `shutil.rmtree` first, because on Linux that is the whole story
    and a pre-emptive `chmod` pass over an AzerothCore checkout is a walk of
    every inode in a 1.3 GB `.git` for nothing.

    The retry is for Windows, where git writes packs and loose objects
    read-only: `shutil.rmtree` stops on the first of them and leaves a
    half-deleted checkout, which is worse than an undeleted one. `rmtree` is not
    atomic, so by the time it reaches a pack it has already removed an unknown
    part of the tree. `_clear_read_only()` clears the bit and the delete is
    tried again.

    And if it still cannot finish, it RAISES, because a delete that reports
    success having deleted nothing is the failure this function exists to make
    impossible.
    """
    try:
        linked = links.is_link(path)
    except OSError as exc:
        # Not `_is_link()`, whose cautious "a link" is right for a walk that then
        # touches only the name, and false as a sentence about a root it could not see.
        raise TreeRemovalError(
            f"{path} could not be examined ({exc}), so it was not deleted; nothing was changed."
        ) from exc
    if linked:
        # `shutil.rmtree` REFUSES a top-level directory symlink, and before the
        # review of 2026-09-13 the retry below then walked through it and chmodded
        # and `rmdir`-ed on the far side. A junction too (T705): `Path.is_symlink()`
        # calls one a folder, so this asks `links`. The retry's own walks no longer
        # enter a link either; the root is still refused here, by the function that
        # knows it is about to walk, rather than in each caller.
        raise LinkRootRefused(
            f"{path} is a symbolic link or junction, not a directory this app may delete "
            f"through. Remove the link itself if that is what was meant; nothing was changed."
        )
    try:
        shutil.rmtree(path)
    except OSError:
        logger.info(f"{path} would not delete; clearing read-only flags and retrying")
        _clear_read_only(path)
        _remove_unenterable(path)
        try:
            shutil.rmtree(path)
        except OSError as exc:
            raise TreeRemovalError(_undeletable(path, exc)) from exc
    if path.exists():
        raise TreeRemovalError(_undeletable(path, "it is still there afterwards"))


def remove_tree_last(path: Path, last: Sequence[str]) -> None:
    """`remove_tree(path)`, its folders first and the top-level names in `last` after all else.

    T682: `shutil.rmtree` works in listing order, and on NTFS that is alphabetical, so an
    install's own records at the top (`.yulon-install.json` among them) went before `src/`
    and `.git`, where a removal actually stops (a held file, a read-only pack). The next
    Uninstall then found no record and refused the folder as not Yu'lon's. Here every folder
    goes first, then the other files at the top, then `last` in the order given, then the
    folder itself: a removal that stops part-way leaves the records a second press reads.

    A link or junction at the top is removed as a link, never entered. A root that is a link,
    or not a folder, is `remove_tree()`'s to refuse.

    Raises:
        TreeRemovalError: as `remove_tree()`.
    """
    # `links`, not `Path.is_symlink()`: that calls a junction a folder (T713), and the walk
    # below would empty the junction's target before `remove_tree` refused the root.
    if _is_link(os.fspath(path)) or not path.is_dir():
        remove_tree(path)
        return
    try:
        with os.scandir(path) as listing:
            entries = [entry for entry in listing if entry.name not in last]
        folders = [e for e in entries if not _is_link(e.path) and e.is_dir(follow_symlinks=False)]
    except OSError as exc:
        raise TreeRemovalError(_undeletable(path, exc)) from exc
    rest = [e for e in entries if e not in folders]
    for entry in folders:
        remove_tree(Path(entry.path))
    for name in (*(e.name for e in rest), *last):
        _remove_one(path / name)
    remove_tree(path)


def _remove_one(path: Path) -> None:
    """Remove one top-level entry of `remove_tree_last()`; a name that is not there is fine."""
    try:
        if not os.path.lexists(path):
            return
        if _is_link(os.fspath(path)):
            _unlink_link(os.fspath(path))
        elif path.is_dir():
            remove_tree(path)
        else:
            try:
                os.unlink(path)
            except PermissionError:
                # Windows: a read-only file refuses its unlink until the bit is cleared.
                os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
                os.unlink(path)
    except TreeRemovalError:
        raise
    except OSError as exc:
        raise TreeRemovalError(_undeletable(path, exc)) from exc


def _undeletable(path: Path, problem: object) -> str:
    """The neutral half of the sentence: what could not be deleted, and the likely why.

    Deliberately says nothing about what else has or has not happened. Every
    caller knows that and this module does not; `purge` adds its own paragraph.
    """
    return (
        f"{path} could not be deleted ({problem}). On Windows this is usually a read-only "
        f"file inside .git, or a program holding a file open in that folder."
    )


def _remove_unenterable(path: Path) -> None:
    """`os.rmdir` every directory entry under `path` that a walk cannot enter.

    The second stop the Windows press found, after the read-only one -- measured
    on `yulon-win11` 2026-09-08 on a real WotLK install
    (`.notes/gates/8.9a-wotlk-yulon-win11-2026-09-08/`): AzerothCore's clone
    stage runs git inside a Linux container with the server dir bind-mounted,
    so every symlink in the repository lands on NTFS as an
    `IO_REPARSE_TAG_LX_SYMLINK` reparse point (`0xa000001d`). Python does not
    know that tag: `entry.is_symlink()` answers False and
    `entry.is_dir(follow_symlinks=False)` answers True, so `shutil.rmtree`
    recurses into it and dies with `[WinError 1920] The file cannot be accessed
    by the system` -- on both presses, with the containers, volumes and images
    already gone and the folder stuck at 12,405 entries. `_clear_read_only()`
    cannot help; the entry is not read-only, it is unreadable.

    What the box answered when asked (`probe_lxsymlink.py`): `os.rmdir` removes
    the reparse point itself, and with it gone `rmtree` walks the rest. The
    POSIX shape of the same stop is a directory whose mode refuses `scandir`,
    which `os.rmdir` also takes when it is empty; a non-empty one it cannot
    take is left for the retry to name. Never follows anything: `rmdir` on a
    link removes the link, and that is the property that makes this safe to
    run over a checkout that may point outside itself.
    """
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                children = list(entries)
        except OSError:
            if current == path:
                return
            try:
                os.rmdir(current)
                logger.info(f"removed an entry the walk could not enter: {current}")
            except OSError as exc:
                logger.info(f"could not remove {current}, leaving it for the retry: {exc}")
            continue
        for entry in children:
            if _is_link(entry.path):
                # Never entered (T705): a junction answers `is_dir` and scandirs on its far
                # side. `rmdir` takes the link itself, the `0xa000001d` case above included.
                try:
                    os.rmdir(entry.path)
                except OSError:
                    pass
                continue
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                is_dir = False
            if is_dir:
                stack.append(Path(entry.path))


def _clear_read_only(path: Path) -> None:
    """Add the write bit back to everything under `path`, top down.

    Directories first and then their contents, because on POSIX it is the
    DIRECTORY's write bit that refuses the unlink, and on Windows it is the
    FILE's read-only attribute that refuses the delete. Clearing both is one
    walk, and a failure on any single entry is left for the retry to report
    against the tree rather than raised against one file.
    """
    # `links.walk`, not `os.walk` (T705): on Python 3.11 under Windows `os.walk` goes
    # through a junction and would clear the flags of the files it points at.
    for root, dirs, files, _linked in links.walk(path):
        for name in (*dirs, *files):
            target = Path(root) / name
            try:
                os.chmod(target, target.lstat().st_mode | stat.S_IWRITE)
            except OSError:
                continue
    try:
        os.chmod(path, path.lstat().st_mode | stat.S_IWRITE)
    except OSError:
        pass
