"""Replace a module folder with a whole new copy, or leave the old one as it was (T538).

"Install from folder" (`module_source.copy_folder()`) copies the chosen folder
into a staging folder first and swaps it in only when the copy is whole: the old
copy is renamed aside, the new one renamed into `modules/<id>`, and the old one
deleted. A copy that fails is removed and the old copy is untouched; a rename into
place that fails puts the old copy back.

**The staging folder is outside `modules/`** (T538 cold review): AzerothCore's
`GetModuleSourceList()` takes every directory `file(GLOB … "${BASE_PATH}/*")`
finds, and CMake's glob lists dot-names too (measured with CMake 3.28: a
`.mod-a.yulon-old` beside `mod-a` came back as a module), so a leftover there
would be compiled beside the module. `<server>/.yulon-module-staging/` is on the
same file system as `modules/`, so each rename is still one atomic step.

**A stopped swap is settled before anything reads the module's claim**
(`settle()`, called by `Applier.install()` and `Applier.remove()`): a crash, or a
roll-back the system refused, can leave the old copy aside and nothing at
`modules/<id>`. Read then, the claim would be missing, and with it the receipts
for the files the module put into the player's client.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from yulon import rmtree
from yulon.log import get_logger

logger = get_logger(__name__)

STAGING = ".yulon-module-staging"
"""The folder beside `modules/` (in the server folder) that holds a copy being swapped in."""

_RENAME_TRIES = 5
_sleep = time.sleep  # a seam: tests do not wait


def places(dest: Path) -> tuple[Path, Path]:
    """`(partial, aside)`: where the new copy of `dest` is made, and where the old one waits."""
    base = dest.parent.parent / STAGING
    stem = f"{dest.parent.name}-{dest.name}"
    return base / f"{stem}.partial", base / f"{stem}.old"


def settle(dest: Path) -> None:
    """Undo what a stopped swap of `dest` left, before anything reads `dest`.

    A partial copy is deleted. An old copy aside with nothing at `dest` is a swap
    stopped between its two renames, and is put back; one beside a `dest` that is
    there is a swap that finished before its delete, and is deleted.

    Raises:
        OSError: the old copy could not be put back; it is still aside, whole.
    """
    partial, aside = places(dest)
    remove_quietly(partial)
    if os.path.lexists(aside):
        if os.path.lexists(dest):
            remove_quietly(aside)
        else:
            _rename(aside, dest)
            logger.info(f"put back {dest} from {aside}, where a stopped copy had left it")
    tidy(partial.parent)


def swap_in(partial: Path, dest: Path) -> None:
    """Rename the whole copy at `partial` to `dest`, the old `dest` aside first and then deleted.

    Raises:
        OSError: the swap could not be made. The old copy is back at `dest`, or,
            if even that was refused, the message names where it is kept.
    """
    _partial, aside = places(dest)
    try:
        _swap(partial, dest, aside)
    finally:
        tidy(aside.parent)


def _swap(partial: Path, dest: Path, aside: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    had_one = os.path.lexists(dest)
    try:
        if had_one:
            _rename(dest, aside)
        _rename(partial, dest)
    except BaseException as failure:
        if had_one and not os.path.lexists(dest) and os.path.lexists(aside):
            try:
                _rename(aside, dest)
            except OSError as stuck:
                remove_quietly(partial)
                raise OSError(
                    f"the new copy could not take the place of {dest} ({failure}), and the "
                    f"earlier copy could not be put back ({stuck}): it is kept whole as {aside}, "
                    f"and the next Install or Remove of this module puts it back first"
                ) from failure
        remove_quietly(partial)
        raise
    if had_one:
        remove_quietly(aside)  # T49: the old copy may hold a read-only .git


def remove_quietly(folder: Path) -> None:
    """Delete `folder` if it is there; a failure is logged, and the next settle tries again.

    Only ever a folder under `STAGING`, which no build reads, so one left behind
    costs disk space and nothing else.
    """
    if not os.path.lexists(folder):
        return
    try:
        rmtree.remove_tree(folder)
    except OSError as exc:
        logger.warning(f"could not remove {folder}; the next copy of this module removes it: {exc}")


def _rename(old: Path, new: Path) -> None:
    """`os.rename`, tried again for a while when refused (Windows: an antivirus scan's handle)."""
    for attempt in range(_RENAME_TRIES):
        try:
            os.rename(old, new)
            return
        except PermissionError:
            if attempt == _RENAME_TRIES - 1:
                raise
            _sleep(0.2 * (attempt + 1))


def tidy(staging: Path) -> None:
    """Remove the staging folder once nothing is left in it."""
    try:
        staging.rmdir()
    except OSError:
        pass
