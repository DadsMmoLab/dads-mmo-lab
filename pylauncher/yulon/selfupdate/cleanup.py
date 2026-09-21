"""The first start after an update: put the previous build out with the rubbish.

The helper renames the old install to `<target>.old` and leaves it there on
purpose — it is the rollback the README tells a player how to use by hand, and
it has to survive until the new build has actually opened. This runs when it
has, which is the earliest moment the old one is provably not needed.

Never raises. It runs while the window is being built, and a launcher that will
not open because it could not delete a folder is a worse bug than a folder left
on disk.
"""

from __future__ import annotations

from yulon.log import get_logger
from yulon.selfupdate.detect import Install
from yulon.selfupdate.stage import discard, sibling

logger = get_logger(__name__)


def finish_previous_update(install: Install) -> bool:
    """Remove `<target>.old`. True if something was there and is now gone.

    The answer is what the banner says "Updated to Yu'lon X" on, so it has to
    mean "an update really did just happen here" — an `.old` that could not be
    removed answers False rather than True, and says nothing.
    """
    target = install.target
    if target is None:
        return False
    old = sibling(target, ".old")
    if not old.exists():
        return False
    discard(old)
    if old.exists():
        logger.info(f"self-update: {old} is still there; leaving it for next time")
        return False
    logger.info(f"self-update: removed the previous build at {old}")
    return True
