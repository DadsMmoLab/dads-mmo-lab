"""The first start after an update, and the three things it checks first (T90 plan 3).

The first version of this deleted `<install>.old` on sight, and for an install
unpacked into a Downloads folder that was the player's own documents. Every
test here is one of the gates that replaced it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from yulon.selfupdate import layout
from yulon.selfupdate.cleanup import finish_previous_update, same_build
from yulon.selfupdate.detect import Install, InstallKind

NEW_VERSION = "v0.8.70-Public"
OLD_VERSION = "0.8.66-Public"


def _install(root: Path) -> Install:
    target = root / "app"
    (target / "_internal").mkdir(parents=True)
    (target / "yulon").write_text("the new build", encoding="utf-8")
    return Install(InstallKind.TARBALL, target, "yulon", True)


def _a_finished_swap(install: Install, *, to: str = NEW_VERSION) -> Path:
    """`.yulon-old` as the helper leaves it when the swap really happened."""
    old = layout.work_dir(install, layout.OLD_NAME)
    old.mkdir(parents=True, exist_ok=True)
    (old / "yulon").write_text("the previous build", encoding="utf-8")
    (old / "_internal").mkdir(exist_ok=True)
    layout.write_marker(
        install,
        layout.OLD_NAME,
        layout.Marker(
            role=layout.OLD_NAME,
            from_version=OLD_VERSION,
            to_version=to,
            pid=os.getpid(),
            stamp=layout.now(),
            entries=("yulon", "_internal"),
            state=layout.SWAPPING,
        ),
    )
    return old


def test_a_finished_swap_is_tidied_up_and_named(tmp_path: Path) -> None:
    install = _install(tmp_path)
    old = _a_finished_swap(install)

    outcome = finish_previous_update(install, running=NEW_VERSION)

    assert outcome.removed is True
    assert outcome.version == "0.8.70-Public", "one spelling of the version"
    assert outcome.problem == ""
    assert not old.exists()


def test_the_staged_copy_goes_with_it(tmp_path: Path) -> None:
    """`.yulon-new` has done its job too, and it is 230 MB."""
    install = _install(tmp_path)
    _a_finished_swap(install)
    layout.write_marker(
        install,
        layout.NEW_NAME,
        layout.Marker(layout.NEW_NAME, OLD_VERSION, NEW_VERSION, os.getpid(), layout.now()),
    )
    finish_previous_update(install, running=NEW_VERSION)
    assert not layout.work_dir(install, layout.NEW_NAME).exists()


def test_a_backup_folder_this_app_did_not_make_is_never_touched(tmp_path: Path) -> None:
    """**The data loss, in its smallest form.** A `.yulon-old` a player made is theirs."""
    install = _install(tmp_path)
    assert install.target is not None
    theirs = install.target / layout.OLD_NAME
    theirs.mkdir()
    (theirs / "notes.txt").write_text("mine", encoding="utf-8")

    outcome = finish_previous_update(install, running=NEW_VERSION)

    assert outcome.removed is False
    assert outcome.problem == "", "a folder the player made is not news"
    assert (theirs / "notes.txt").read_text(encoding="utf-8") == "mine"


def test_a_backup_from_an_update_that_did_not_happen_is_the_rollback_arm(
    tmp_path: Path,
) -> None:
    """We are running the version the update was FROM, and everything is in place.

    That is the helper's rollback, or its give-up: the swap did not happen.
    The player is told once, and the staged build goes — it is a whole copy of
    the app and nothing is going to install it now.
    """
    install = _install(tmp_path)
    old = _a_finished_swap(install, to=NEW_VERSION)

    outcome = finish_previous_update(install, running=OLD_VERSION)

    assert outcome.removed is False
    # The backup still holds a copy of the build, so it is the ONLY copy of
    # what is in it and nothing here deletes it (round 3, F6).
    assert "is not installed" in outcome.problem
    assert old.exists(), "the only copy of the old build was deleted"


def test_a_swap_that_stopped_part_way_is_reported_and_nothing_is_touched(
    tmp_path: Path,
) -> None:
    """A helper killed between two moves. **The app says so and changes nothing.**

    It used to restore the entries here, inside the live process, renaming the
    `_internal` that process was executing out of — with no undo of its own if
    the second rename failed. Removed on the lead's decision (2026-09-21); the
    whole-tree proof lives in `test_selfupdate_recovery.py`, which builds every
    half-done state and hashes the install before and after.
    """
    install = _install(tmp_path)
    assert install.target is not None
    old = _a_finished_swap(install)
    # `_internal` never arrived: the helper was killed after the executable.
    (install.target / "_internal").rmdir()

    outcome = finish_previous_update(install, running=NEW_VERSION)

    assert outcome.removed is False
    assert "did not finish" in outcome.problem
    assert "Nothing has been changed." in outcome.problem
    assert layout.OLD_NAME in outcome.problem and layout.MARKER_NAME in outcome.problem
    assert not (install.target / "_internal").exists(), "the app put an entry back"
    assert old.exists(), "the way out was removed"
    assert (old / "yulon").read_text(encoding="utf-8") == "the previous build"


def test_a_half_finished_swap_whose_files_are_in_neither_place_is_left_alone(
    tmp_path: Path,
) -> None:
    """Nothing to say beyond what is missing, and nothing to do about it here."""
    install = _install(tmp_path)
    assert install.target is not None
    old = _a_finished_swap(install)
    (install.target / "_internal").rmdir()
    (old / "_internal").rmdir()

    outcome = finish_previous_update(install, running=NEW_VERSION)

    assert outcome.removed is False
    assert "_internal" in outcome.problem
    assert layout.OLD_NAME in outcome.problem and layout.MARKER_NAME in outcome.problem
    assert old.exists(), "the backup was removed on the one start that proves it is needed"


def test_nothing_to_clean_up_says_nothing(tmp_path: Path) -> None:
    install = _install(tmp_path)
    outcome = finish_previous_update(install, running=NEW_VERSION)
    assert (outcome.removed, outcome.problem) == (False, "")


@pytest.mark.parametrize(
    "install",
    [
        Install(InstallKind.SOURCE, None, "", False),
        Install(InstallKind.UNSUPPORTED, None, "", False),
    ],
)
def test_an_install_with_no_target_has_nothing_to_clean_up(install: Install) -> None:
    assert finish_previous_update(install, running=NEW_VERSION).removed is False


def test_a_backup_that_cannot_be_removed_answers_false_without_raising(tmp_path: Path) -> None:
    """It runs at startup. A traceback here would be a launcher that will not open."""
    install = _install(tmp_path)
    assert install.target is not None
    old = _a_finished_swap(install)
    install.target.chmod(0o500)
    try:
        assert finish_previous_update(install, running=NEW_VERSION).removed is False
        assert old.exists(), "the precondition: this one really could not be removed"
    finally:
        install.target.chmod(0o700)


def test_an_appimage_that_was_replaced_is_tidied_up(tmp_path: Path) -> None:
    target = tmp_path / "Yulon.AppImage"
    target.write_bytes(b"the new build")
    install = Install(InstallKind.APPIMAGE, target, "", True)
    old = layout.work_dir(install, layout.OLD_NAME)
    old.mkdir()
    (old / layout.APPIMAGE_ENTRY).write_bytes(b"the previous build")
    layout.write_marker(
        install,
        layout.OLD_NAME,
        layout.Marker(
            layout.OLD_NAME,
            OLD_VERSION,
            NEW_VERSION,
            os.getpid(),
            layout.now(),
            entries=(layout.APPIMAGE_ENTRY,),
        ),
    )

    outcome = finish_previous_update(install, running=NEW_VERSION)

    assert outcome.removed is True
    assert not old.exists()
    assert target.read_bytes() == b"the new build"


# -- the version comparison that never matched -------------------------------


def test_the_running_version_and_the_marker_agree_on_a_real_release(tmp_path: Path) -> None:
    """**The defect that made this whole file inert in a packaged build.**

    `to_version` is the tag off the release feed, which `update.public_tag()`
    keeps the `v` on. `__version__` comes from `build/stamp_version.py`, which
    strips it. A plain `!=` was therefore true of every real build: the backup
    and the staged copy — about 230 MB — were never removed, "Updated to
    Yu'lon X" never appeared, and the README's promise was false.

    **The running value is DERIVED from the build script** rather than typed,
    so the two can never drift apart again: whatever `version_from_ref` does to
    a tag is what this test compares against.
    """
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "build"))
    from stamp_version import version_from_ref

    tag = "v0.8.73-Public"
    running = version_from_ref(tag)
    assert running == "0.8.73-Public", "the build script no longer strips the v"
    assert running != tag, "the precondition: the two shapes really do differ"

    install = _install(tmp_path)
    _a_finished_swap(install, to=tag)

    outcome = finish_previous_update(install, running=running)

    assert outcome.removed is True, "a real build would never have cleaned up"
    assert outcome.version == running, "one spelling of the version"


@pytest.mark.parametrize(
    ("marker_version", "running", "same"),
    [
        ("v0.8.73-Public", "0.8.73-Public", True),
        ("0.8.73-Public", "v0.8.73-Public", True),
        ("v0.8.73-Public", "v0.8.73-Public", True),
        ("0.8.73-Public", "0.8.73-Public", True),
        ("V0.8.73-Public", "0.8.73-Public", True),
        ("v0.8.73-Public", "0.8.74-Public", False),
        # `.7` and `.70` are the SAME release to `update.version_key`, which
        # orders them. They are not the same BUILD, and this answers that
        # question: a backup from an update to one is not the backup from an
        # update to the other.
        ("v0.8.7-Public", "0.8.70-Public", False),
        ("vv0.8.73-Public", "0.8.73-Public", False),
        ("", "", True),
    ],
)
def test_exactly_one_leading_v_is_stripped_from_each_side(
    marker_version: str, running: str, same: bool
) -> None:
    assert same_build(marker_version, running) is same


def test_a_rollback_is_said_once_and_the_staged_build_goes_with_it(tmp_path: Path) -> None:
    """The helper undid the swap. Until cold review 2 nothing said so and 230 MB stayed."""
    install = _install(tmp_path)
    old = _a_finished_swap(install, to="v9.9.9-Public")
    layout.write_marker(
        install,
        layout.NEW_NAME,
        layout.Marker(layout.NEW_NAME, OLD_VERSION, "v9.9.9-Public", os.getpid(), layout.now()),
    )
    staged = layout.work_dir(install, layout.NEW_NAME)
    (staged / "yulon").write_text("the build that was not installed", encoding="utf-8")

    # An empty backup: the helper put EVERY entry back, which is what a
    # completed rollback leaves. That is the state this arm is about — a
    # backup that still holds one is the only copy of it, and is never deleted.
    (old / "yulon").unlink()
    (old / "_internal").rmdir()

    outcome = finish_previous_update(install, running=OLD_VERSION)

    assert outcome.removed is False
    assert "could not be installed" in outcome.problem
    assert "9.9.9-Public" in outcome.problem
    assert not staged.exists(), "a whole staged build was left beside the install"
    assert not old.exists()
