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
from yulon.selfupdate.cleanup import finish_previous_update
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
    assert outcome.version == NEW_VERSION
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


def test_a_backup_from_an_update_that_did_not_happen_is_kept(tmp_path: Path) -> None:
    """We are running the version the update was FROM, so the swap did not finish.

    The backup is the thing that would put this install right; deleting it here
    is deleting the way back on the one start that proves it is needed.
    """
    install = _install(tmp_path)
    old = _a_finished_swap(install, to=NEW_VERSION)

    outcome = finish_previous_update(install, running=OLD_VERSION)

    assert outcome.removed is False
    assert old.exists()
    assert (old / "yulon").read_text(encoding="utf-8") == "the previous build"


def test_a_swap_that_stopped_part_way_is_reported_rather_than_tidied_away(
    tmp_path: Path,
) -> None:
    """A helper killed between two moves. The entries the marker names are not all there."""
    install = _install(tmp_path)
    assert install.target is not None
    old = _a_finished_swap(install)
    # `_internal` never arrived: the helper was killed after the executable.
    (install.target / "_internal").rmdir()

    outcome = finish_previous_update(install, running=NEW_VERSION)

    assert outcome.removed is False
    assert "_internal" in outcome.problem
    assert layout.OLD_NAME in outcome.problem
    assert "README" in outcome.problem, "the player is told where to read what to do"
    assert old.exists(), "the way back was deleted on the one start that proves it is needed"


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
