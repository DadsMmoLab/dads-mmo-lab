"""The first start after an update: put the previous build out with the rubbish — carefully.

The helper moves the entries the build ships into `<target>/.yulon-old/` and
leaves them there on purpose: it is the way back the README tells a player how
to use by hand, and it has to survive until the new build has actually opened.

**Three things are checked before anything is deleted** (cold review 1). The
first version of this file deleted `<target>.old` on sight — which, for an
install unpacked into a Downloads folder, was the player's own documents. Now:

1. the directory must carry a marker this app wrote (`layout.is_ours`);
2. the marker's `to_version` must be **the version now running**, so a start of
   the OLD build never throws away the backup it might need;
3. every entry the marker names must be present in the install folder, so a
   swap that stopped part-way is reported rather than tidied away.

Never raises. It runs while the window is being built, and a launcher that will
not open because it could not delete a folder is a worse bug than a folder left
on disk.
"""

from __future__ import annotations

from dataclasses import dataclass

from yulon import __version__
from yulon.log import get_logger
from yulon.selfupdate import layout
from yulon.selfupdate.detect import Install, InstallKind

logger = get_logger(__name__)


@dataclass(frozen=True)
class Outcome:
    """What the first start found, and what to say about it.

    `removed` is what the banner's "Updated to Yu'lon X" is said on, so it has
    to mean "an update really did finish here". `problem` is the other half: a
    swap that stopped part-way is a sentence the player needs, not silence.
    """

    removed: bool = False
    version: str = ""
    problem: str = ""


def finish_previous_update(install: Install, *, running: str = __version__) -> Outcome:
    """Remove a `.yulon-old` this app made, for a swap that really finished.

    `running` is injected rather than read here so a test can say which build
    is open without pretending to be one.
    """
    target = install.target
    if target is None:
        return Outcome()
    marker = layout.read_marker(install, layout.OLD_NAME)
    old = layout.work_dir(install, layout.OLD_NAME)
    if not old.exists():
        return Outcome()
    if marker is None:
        # Not ours. Never touched, and never mentioned to the player either:
        # a folder they made is not news.
        logger.info(f"self-update: {old} carries no marker of ours; leaving it alone")
        return Outcome()
    if marker.to_version != running:
        # We are running the version the update was FROM, so the swap did not
        # happen — the helper gave up, or rolled back. The backup is the thing
        # that would put this install right and it is not ours to remove.
        logger.info(
            f"self-update: {old} belongs to an update to {marker.to_version!r} and this is "
            f"{running!r}; leaving it alone"
        )
        return Outcome()
    missing = _entries_missing(install, marker)
    if missing:
        logger.warning(f"self-update: the swap did not finish; {missing} are not in {target}")
        return Outcome(
            problem=(
                f"An update to {marker.to_version} did not finish: {', '.join(missing)} "
                f"{'is' if len(missing) == 1 else 'are'} missing from your Yu'lon folder. "
                f"The previous version is in the {layout.OLD_NAME} folder beside it — "
                "pylauncher/README.md says how to put it back."
            )
        )
    if not layout.discard_ours(install, layout.OLD_NAME):
        logger.info(f"self-update: {old} is still there; leaving it for next time")
        return Outcome()
    # The staged copy has done its job too, and it is marked, so this can only
    # ever remove something this app made.
    layout.discard_ours(install, layout.NEW_NAME)
    logger.info(f"self-update: removed the previous build at {old}")
    return Outcome(removed=True, version=marker.to_version)


def _entries_missing(install: Install, marker: layout.Marker) -> list[str]:
    """Which of the entries the swap was supposed to place are not there.

    An AppImage names no entries — it is one file, and the file either was
    replaced or was not, which `to_version` already answers.
    """
    target = install.target
    if target is None or install.kind is InstallKind.APPIMAGE:
        return []
    return [name for name in marker.entries if not (target / name).exists()]
