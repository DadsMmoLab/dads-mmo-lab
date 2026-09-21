"""The first start after an update: put the previous build out with the rubbish — carefully.

The helper moves the entries the build ships into `<target>/.yulon-old/` and
leaves them there on purpose: it is the way back the README tells a player how
to use by hand, and it has to survive until the new build has actually opened.

**This module never moves a build entry.** It deletes marked working
directories, and it reports. That is the whole of what it may do, and the rule
is the same one the swap helper exists for: **the running app never renames its
own files.**

A version of this file did restore entries in place, and it was removed on the
lead's decision (2026-09-21). Three things were wrong with it:

* it ran **inside the live process** and renamed `<target>/_internal` — the
  directory that process is executing out of. On Linux the rename succeeds and
  every later lazy import reads the new tree through the old path; on Windows
  the loaded files are locked and it fails.
* it had **no undo of its own**: an `OSError` on the second entry returned
  "could not put it right" with the first entry already swapped back, which is
  the mixed-version install the whole design is built to prevent.
* it was reachable **almost never**. Entries leave executable-FIRST and arrive
  executable-LAST, so every kill point of a half-done swap leaves the target
  with no executable at all — the app cannot start, so nothing here can run.
  With an executable present the only states are untouched, swap complete, and
  rollback complete. The in-place repair was the most dangerous kind of code
  for the rarest state.

So a half-done swap is **reported and nothing is touched**: the message says
what is where and gives the one instruction `pylauncher/README.md` carries, and
`tests/test_selfupdate_recovery.py` executes that instruction at every kill
point. A player whose install has no executable has that path anyway, because
the app cannot start to offer them anything else.

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

from yulon import __version__
from yulon.log import get_logger
from yulon.selfupdate import layout
from yulon.selfupdate.detect import Install, InstallKind
from yulon.selfupdate.layout import Marker

logger = get_logger(__name__)


def recovery_steps(new_entries: tuple[str, ...] = ()) -> tuple[str, ...]:
    """The steps that put the previous version back, as DATA. **The one source.**

    The message, the README and the test that executes the procedure all come
    from here, because when they were three copies they disagreed: the app was
    still printing a one-step instruction that the README had already replaced,
    and following it on a swap killed after the third move destroyed the only
    copy of the old `_internal` (round 4, M4).

    `new_entries` are names the NEW build ships and the old one did not. They
    have to be removed by hand: nothing in `.yulon-old` will replace them, and
    left behind they are a collision that refuses every later update for ever.
    """
    first = (
        f"In the Yu'lon folder, delete (or move away) every file and folder that also exists "
        f"inside {layout.OLD_NAME}."
    )
    second = (
        f"Move everything in {layout.OLD_NAME} except {_KEPT} into the Yu'lon folder, then "
        f"delete {layout.OLD_NAME} and {layout.NEW_NAME}."
    )
    if not new_entries:
        return (first, second)
    return (
        first,
        second,
        f"Delete {', '.join(sorted(new_entries))} from the Yu'lon folder: "
        "the new version brought them and the old one does not use them.",
    )


_KEPT = ", ".join(sorted(layout.BOOKKEEPING))
"""The bookkeeping files a player leaves where they are, named in the instruction."""


def steps_as_text(new_entries: tuple[str, ...] = ()) -> str:
    """The steps as one numbered sentence, for a message that has one line to say it in."""
    return " ".join(f"{n}. {step}" for n, step in enumerate(recovery_steps(new_entries), 1))


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
    return without_v(marker_version) == without_v(running)


def without_v(text: str) -> str:
    """One leading `v`/`V` off, for comparing and for SHOWING.

    Public because the banner uses it too: the marker carries the feed's tag
    (`v0.8.73-Public`) and the window title carries `__version__`
    (`0.8.73-Public`), and the first live gate showed the two spellings side by
    side on one screen — "Updated to Yu'lon v0.8.73-Public." over a title
    saying 0.8.73-Public (round 3, F9). One spelling everywhere.
    """
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

    **The only thing this can change on disk is a marked working directory**,
    and it changes those only through `layout.discard_ours`. It never moves,
    renames or restores an entry of the build — see the module docstring for
    the three reasons the version that did was removed.

    **`.yulon-old` is deleted on exactly two answers** (round 3): the swap
    completed and the running build is the one the marker names, or the backup
    holds no build entry at all (the helper's rollback moved everything back,
    so there is nothing in there to lose). Every other state is REPORTED and
    nothing is touched — including the one the third cold review measured,
    where a player had restored two entries of three and `_rolled_back` deleted
    the only remaining copy of the third while saying it had been put back.

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
    if _another_copy_owns_this(marker):
        # **A second instance must not tidy up the first one's update** (round
        # 3, F1). Measured there: instance B started while A held a staged
        # build waiting for a close gate, removed both work dirs, and told the
        # player the update had been rolled back — which was false, and left
        # A's next press starting a helper that exited 64 against an app that
        # had already closed.
        logger.info(f"self-update: pid {marker.pid} is still updating here; leaving it alone")
        return Outcome()
    held = _entries_in_backup(install, marker)
    if not held:
        # Nothing in the backup but this app's own bookkeeping.
        # The helper put everything back: there is nothing in the backup to
        # lose, whatever the target is missing. **This branch comes before the
        # missing-entries one on purpose**: a build that ships a THIRD entry
        # for the first time names three in its marker, and an old install that
        # never had the third would otherwise be told it was missing at every
        # start, for ever (round 3).
        return _rolled_back(install, marker)
    missing = _entries_missing(install, marker)
    if missing:
        # A swap that stopped part-way, or an install a player has partly put
        # back by hand. Either way this is not a state to tidy from inside the
        # process that is running out of it: say what is where and stop.
        return _half_done(install, marker, missing)
    if not same_build(marker.to_version, running):
        # Everything is in place, the backup still holds a copy of the build,
        # and this is not the version the update was to. Somebody has put the
        # old build back by hand, or is running an older one. Nothing here is
        # safe to delete and nothing needs moving.
        return _not_installed(install, marker)
    if not layout.discard_ours(install, layout.OLD_NAME):
        logger.info(f"self-update: {old} is still there; leaving it for next time")
        return Outcome()
    # The staged copy has done its job too, and it is marked, so this can only
    # ever remove something this app made.
    layout.discard_ours(install, layout.NEW_NAME)
    layout.discard_ours(install, layout.DOWNLOAD_NAME)
    logger.info(f"self-update: removed the previous build at {old}")
    return Outcome(removed=True, version=without_v(marker.to_version))


def _another_copy_owns_this(marker: Marker) -> bool:
    """Is the copy of Yu'lon that made this working directory still running?

    The same liveness question `layout.another_copy_is_updating` asks before
    staging, asked again at start — because the answer decides whether these
    directories are rubbish or somebody's work in progress.
    """
    return marker.pid not in (0, os.getpid()) and layout.pid_is_alive(marker.pid)


def _entries_in_backup(install: Install, marker: Marker) -> list[str]:
    """What is in `.yulon-old` that is not this app's own bookkeeping.

    **Not just the entries the marker names** (round 4). A backup holding a
    `player.sav` — dropped in there by somebody, or left by a build that once
    shipped it — is not empty, and `_rolled_back` deletes what this answers
    empty about. Anything in there that is not ours is a reason to report
    rather than to delete.
    """
    backup = layout.work_dir(install, layout.OLD_NAME)
    del marker
    try:
        return sorted(p.name for p in backup.iterdir() if p.name not in layout.BOOKKEEPING)
    except OSError:
        return []


def _rolled_back(install: Install, marker: Marker) -> Outcome:
    """The backup holds nothing: the helper put every entry back. Say so once.

    Reached when `.yulon-old` carries a marker of ours and not one build entry
    — which is what a completed rollback, or a give-up before the first move,
    leaves behind. There is nothing in there to lose, so the marked directories
    go and the staged build with them; until cold review 2 this was silent and
    left about 230 MB beside the install with nothing saying why.

    **Deletes only marked working directories**, like everything else in this
    module.
    """
    tail = _and_the_log(install)
    for name in layout.WORK_NAMES:
        layout.discard_ours(install, name)
    logger.info(f"self-update: the update to {marker.to_version} was rolled back")
    return Outcome(
        problem=(
            f"The update to {without_v(marker.to_version)} could not be installed, and the "
            "version you had was put back. You can try again from See what's new."
            f"{tail}"
        )
    )


def _not_installed(install: Install, marker: Marker) -> Outcome:
    """Everything is in place, the backup still holds a build, and this is not that build.

    Somebody has put the old version back by hand, or is running an older one.
    Nothing here is missing and nothing here is safe to delete — the backup is
    the only copy of what it holds — so this says where things are and stops.
    """
    logger.info(f"self-update: an update to {marker.to_version} is not the build running here")
    return Outcome(
        problem=(
            f"The update to {without_v(marker.to_version)} is not installed. The version in "
            f"the {layout.OLD_NAME} folder beside Yu'lon is the one you had; delete that "
            f"folder when you no longer want it.{_and_the_log(install)}"
        )
    )


def _half_done(install: Install, marker: Marker, missing: list[str]) -> Outcome:
    """A swap that stopped part-way. **Reported, and nothing is touched.**

    Not repaired in this process, for the three reasons the module docstring
    gives. What the player gets is the instruction — rendered from
    `recovery_steps()`, the one place it exists — and an install that is
    exactly as the helper left it, including the backup that is the way out.
    """
    logger.warning(f"self-update: {missing} are missing after an update to {marker.to_version}")
    return Outcome(
        problem=(
            f"An update to {without_v(marker.to_version)} did not finish: "
            f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} missing from this "
            f"folder. Nothing has been changed. To put the version you had back: "
            f"{steps_as_text(marker.new_entries)}"
            f"{_and_the_log(install)}"
        )
    )


def _and_the_log(install: Install) -> str:
    """The helper's last word, when it left one. `""` otherwise.

    The Windows gate could only guess at what had happened because the helper
    left no trace; the log exists for that, and this is what makes the claim in
    `layout.HELPER_LOG` true rather than aspirational.
    """
    tail = layout.helper_log_tail(install)
    return f" (the installer's last message was: {tail})" if tail else ""


def _entries_missing(install: Install, marker: layout.Marker) -> list[str]:
    """Which of the entries the swap was supposed to place are not there.

    An AppImage names no entries — it is one file, and the file either was
    replaced or was not, which `to_version` already answers.
    """
    target = install.target
    if target is None or install.kind is InstallKind.APPIMAGE:
        return []
    return [name for name in marker.entries if not (target / name).exists()]
