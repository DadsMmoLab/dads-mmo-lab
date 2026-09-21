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

import os
from dataclasses import dataclass
from pathlib import Path

from yulon import __version__
from yulon.log import get_logger
from yulon.selfupdate import layout
from yulon.selfupdate.detect import Install, InstallKind
from yulon.selfupdate.layout import Marker

logger = get_logger(__name__)


def same_build(marker_version: str, running: str) -> bool:
    """Do these two name the same build, `v` or no `v`?

    **This function exists because the comparison it replaces never matched.**
    `to_version` is the tag off the release feed, which `update.public_tag()`
    keeps the `v` on (`v0.8.70-Public`); `__version__` comes from
    `build/stamp_version.py`, whose `version_from_ref()` strips it
    (`0.8.70-Public`). A plain `!=` was therefore true of every real packaged
    build: the backup and the staged copy — about 230 MB together — were never
    removed, "Updated to Yu'lon X" never appeared, and the README's promise
    that both folders go at the next start was false (cold review 2).

    Exactly one leading `v`/`V` is stripped from each side and nothing else is
    done. **No case folding and no numeric reading**: this answers "is this the
    build the marker names", not "which of these is newer", so `0.8.7` and
    `0.8.70` stay different here even though `update.version_key` calls them
    equal — a backup from an update to one is not the backup from an update to
    the other.
    """
    return _without_v(marker_version) == _without_v(running)


def _without_v(text: str) -> str:
    stripped = text.strip()
    return stripped[1:] if stripped[:1] in ("v", "V") else stripped


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
    if not same_build(marker.to_version, running):
        # We are running the version the update was FROM, so the swap did not
        # finish. Either it was undone (every entry is in place — the rollback
        # arm) or it stopped part-way (an entry is missing), and those are two
        # different things to say and do.
        if _entries_missing(install, marker):
            return _repair(install, marker, _entries_missing(install, marker))
        return _rolled_back(install, marker)
    missing = _entries_missing(install, marker)
    if missing:
        return _repair(install, marker, missing)
    if not layout.discard_ours(install, layout.OLD_NAME):
        logger.info(f"self-update: {old} is still there; leaving it for next time")
        return Outcome()
    # The staged copy has done its job too, and it is marked, so this can only
    # ever remove something this app made.
    layout.discard_ours(install, layout.NEW_NAME)
    logger.info(f"self-update: removed the previous build at {old}")
    return Outcome(removed=True, version=marker.to_version)


def _rolled_back(install: Install, marker: Marker) -> Outcome:
    """The swap was undone, or never ran. Say so once, and take the staged copy away.

    Reached when the running build is NOT the one the marker names and every
    entry is where it should be — the helper rolled back, or gave up. Until
    cold review 2 this was silent and left a whole staged build (about 230 MB)
    beside the install until the next update overwrote it, with nothing saying
    why the update had not happened.
    """
    for name in layout.WORK_NAMES:
        layout.discard_ours(install, name)
    logger.info(f"self-update: the update to {marker.to_version} was rolled back")
    return Outcome(
        problem=(
            f"The update to {marker.to_version} could not be installed, and the version you "
            "had was put back. You can try again from See what's new."
        )
    )


def _repair(install: Install, marker: Marker, missing: list[str]) -> Outcome:
    """A swap that stopped part-way. **Put it right here if the files are there.**

    The README used to be the whole answer, and following it literally made
    things worse (cold review 2): with the executable already in `.yulon-old`
    and the old `_internal` still in place, its "move the program and
    `_internal` aside" step moved the one good `_internal` away and restored
    only the executable.

    So the app does it, whenever the app can run at all. **Every entry the
    marker names is restored, not only the ones that are missing** — a kill
    after the fourth of six moves leaves some entries already replaced by the
    new build, and restoring only the absent ones would end at a MIXTURE: the
    new `extra.dat` beside the old everything else. That was measured by this
    file's own test at kill points 4 and 5 before this loop was written this
    way. What comes out is one whole version, the one that was running before.

    An entry the target still holds is put back into `.yulon-new`, which is
    removed at the end — so nothing is deleted outside a marked directory of
    this app's own, even here.

    The executable is restored LAST, for the helper's reason: an install with
    an executable and the wrong libraries beside it is worse than one with no
    executable at all.

    A file that cannot be moved back leaves everything alone and says what is
    missing: half a repair is worse than none.
    """
    target = install.target
    assert target is not None  # a marker was read from under it
    backup = layout.work_dir(install, layout.OLD_NAME)
    staged = layout.work_dir(install, layout.NEW_NAME)
    unrecoverable = [name for name in missing if not (backup / name).exists()]
    if unrecoverable:
        logger.warning(f"self-update: cannot repair; {unrecoverable} are in neither place")
        return Outcome(problem=_cannot_repair(marker, unrecoverable))
    # `marker.entries` is stored executable-first; restoring runs backwards.
    restorable = [name for name in marker.entries if (backup / name).exists()]
    try:
        # The staging directory is where the half-installed new entries are
        # parked, and it has to be one this app can prove is its own or the
        # discard below will refuse it and leave them there. A rollback may
        # have taken it away entirely, so it is remade and marked here.
        staged.mkdir(parents=True, exist_ok=True)
        if layout.read_marker(install, layout.NEW_NAME) is None:
            layout.write_marker(
                install,
                layout.NEW_NAME,
                layout.Marker(
                    role=layout.NEW_NAME,
                    from_version=marker.from_version,
                    to_version=marker.to_version,
                    pid=os.getpid(),
                    stamp=layout.now(),
                ),
            )
        for name in reversed(restorable):
            _park(target / name, staged, name)
            os.replace(backup / name, target / name)
    except OSError as exc:
        logger.warning(f"self-update: the repair stopped at {exc}")
        return Outcome(problem=_cannot_repair(marker, missing))
    logger.info(f"self-update: put {restorable} back after a half-finished update")
    for name in layout.WORK_NAMES:
        layout.discard_ours(install, name)
    return Outcome(
        problem=(
            f"The update to {marker.to_version} did not finish, so Yu'lon has put the version "
            "you had back. You can try again from See what's new."
        )
    )


def _park(entry: Path, staged: Path, name: str) -> None:
    """Move `entry` out of the way into the staging directory, if it is there at all.

    Into `.yulon-new` rather than deleted: that directory is this app's own and
    is removed whole at the end of the repair, so even the half-installed new
    build is taken away by a marker-gated delete rather than by an `rmtree` of
    something computed here.
    """
    if not entry.exists():
        return
    spare = staged / name
    suffix = 0
    while spare.exists():
        suffix += 1
        spare = staged / f"{name}.replaced-{suffix}"
    os.replace(entry, spare)


def _cannot_repair(marker: Marker, missing: list[str]) -> str:
    """The one case the app cannot fix, in the fewest words a player can act on."""
    return (
        f"An update to {marker.to_version} did not finish and Yu'lon could not put it right: "
        f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} missing. "
        f"Move the files from the {layout.OLD_NAME} folder back into this folder, except "
        f"{layout.MARKER_NAME}; then delete {layout.OLD_NAME}."
    )


def _entries_missing(install: Install, marker: layout.Marker) -> list[str]:
    """Which of the entries the swap was supposed to place are not there.

    An AppImage names no entries — it is one file, and the file either was
    replaced or was not, which `to_version` already answers.
    """
    target = install.target
    if target is None or install.kind is InstallKind.APPIMAGE:
        return []
    return [name for name in marker.entries if not (target / name).exists()]
