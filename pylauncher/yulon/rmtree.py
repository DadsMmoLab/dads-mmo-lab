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
from pathlib import Path

from yulon.log import get_logger

logger = get_logger(__name__)


class TreeRemovalError(OSError):
    """A directory tree could not be deleted. The message names the path and the cause.

    An `OSError` rather than a bare `RuntimeError` so a caller that already
    catches `OSError` around a delete keeps catching this one, and the two
    `shutil.rmtree` failures it wraps stay the same kind of thing they were.
    """


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
    (`pyplan/gates/8.9a-wotlk-yulon-win11-2026-09-08/`): AzerothCore's clone
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
    for root, dirs, files in os.walk(path):
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
