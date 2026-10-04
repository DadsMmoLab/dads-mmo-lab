"""The shared folder picker lists SD cards and USB drives on Linux (T215).

A Steam Deck player could not pick a client kept on the micro SD card: Qt's own
folder dialog, which the AppImage gets because it sets no platform theme, has
only Computer and Home in its sidebar, and Computer on Linux is just `/`.
SteamOS mounts the card under `/run/media/deck/<label>` (older SteamOS:
`/run/media/mmcblk0p1`), which nothing in the dialog pointed at.

Every test that opens a dialog replaces `folder_picker.run_dialog`, the one
step that shows it, and looks at the `QFileDialog` the code really built.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QFileDialog, QWidget

from yulon import platform
from yulon.ui import catalog_view, controller_view, folder_picker


def _two_volumes(root: Path) -> tuple[Path, Path]:
    card = root / "SDCARD"
    stick = root / "USBSTICK"
    card.mkdir(parents=True)
    stick.mkdir()
    return card, stick


def _defaults(qapp: object) -> list[QUrl]:
    """The sidebar a bare dialog has: Computer and Home."""
    probe = QFileDialog(None, "probe", "")
    try:
        return list(probe.sidebarUrls())
    finally:
        probe.deleteLater()


class _Shown:
    """A stand-in for `exec()`: records what the dialog held, then answers."""

    def __init__(self, accept_with: Path | None = None) -> None:
        self.accept_with = accept_with
        self.dialogs: list[QFileDialog] = []
        self.sidebars: list[list[QUrl]] = []
        self.directories: list[str] = []

    def __call__(self, dialog: QFileDialog) -> bool:
        self.dialogs.append(dialog)
        self.sidebars.append(list(dialog.sidebarUrls()))
        self.directories.append(dialog.directory().absolutePath())
        if self.accept_with is None:
            return False
        dialog.setDirectory(str(self.accept_with))
        return True


@pytest.fixture
def on_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "detect", lambda: "linux")


# -- the volumes --------------------------------------------------------------


def test_two_volumes_under_a_mount_root_are_both_listed(tmp_path: Path) -> None:
    card, stick = _two_volumes(tmp_path / "run-media-deck")

    assert folder_picker.removable_volumes([tmp_path / "run-media-deck"]) == [card, stick]


def test_a_missing_mount_root_adds_nothing_and_raises_nothing(tmp_path: Path) -> None:
    assert folder_picker.removable_volumes([tmp_path / "not-there"]) == []


def test_a_plain_file_under_a_mount_root_is_not_a_volume(tmp_path: Path) -> None:
    root = tmp_path / "media"
    root.mkdir()
    (root / "notes.txt").write_text("x")

    assert folder_picker.removable_volumes([root]) == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads everything")
def test_an_unreadable_entry_is_not_listed(tmp_path: Path) -> None:
    root = tmp_path / "run-media"
    card, other_user = _two_volumes(root)
    other_user.chmod(0o000)
    try:
        assert folder_picker.removable_volumes([root]) == [card]
    finally:
        other_user.chmod(0o755)


def test_the_users_own_folder_under_run_media_is_not_a_volume(tmp_path: Path) -> None:
    """`/run/media` holds `/run/media/deck` itself, which is a root, not a card."""
    run_media = tmp_path / "run-media"
    user_root = run_media / "deck"
    card = user_root / "SDCARD"
    card.mkdir(parents=True)
    old_layout = run_media / "mmcblk0p1"
    old_layout.mkdir()

    assert folder_picker.removable_volumes([user_root, run_media]) == [card, old_layout]


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need a privilege on Windows")
def test_a_volume_reached_twice_is_listed_once(tmp_path: Path) -> None:
    """Newer SteamOS keeps `/run/media/mmcblk0p1` as a link to the labelled card."""
    run_media = tmp_path / "run-media"
    user_root = run_media / "deck"
    card = user_root / "SDCARD"
    card.mkdir(parents=True)
    (run_media / "mmcblk0p1").symlink_to(card)

    assert folder_picker.removable_volumes([user_root, run_media]) == [card]


def test_the_default_roots_are_the_three_linux_mount_layouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(folder_picker, "_user_name", lambda: "deck")

    assert folder_picker.default_mount_roots() == (
        Path("/run/media/deck"),
        Path("/run/media"),
        Path("/media/deck"),
    )


def test_no_user_name_leaves_only_the_shared_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(folder_picker, "_user_name", lambda: None)

    assert folder_picker.default_mount_roots() == (Path("/run/media"),)


# -- the dialog ---------------------------------------------------------------


def test_the_dialog_keeps_the_default_sidebar_and_adds_each_volume(
    qapp: object, tmp_path: Path
) -> None:
    card, stick = _two_volumes(tmp_path / "run-media-deck")

    dialog = folder_picker.build_folder_dialog(None, "Pick", None, [card, stick])
    try:
        assert dialog.sidebarUrls() == [
            *_defaults(qapp),
            QUrl.fromLocalFile(str(card)),
            QUrl.fromLocalFile(str(stick)),
        ]
        assert dialog.fileMode() == QFileDialog.FileMode.Directory
        assert dialog.testOption(QFileDialog.Option.ShowDirsOnly)
    finally:
        dialog.deleteLater()


def test_with_no_volumes_the_sidebar_is_the_default_one(qapp: object) -> None:
    dialog = folder_picker.build_folder_dialog(None, "Pick", None, [])
    try:
        assert dialog.sidebarUrls() == _defaults(qapp)
    finally:
        dialog.deleteLater()


def test_pick_folder_shows_the_volumes_and_returns_the_chosen_one(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, on_linux: None
) -> None:
    card, stick = _two_volumes(tmp_path / "run-media-deck")
    monkeypatch.setattr(folder_picker, "mount_roots", lambda: (tmp_path / "run-media-deck",))
    shown = _Shown(accept_with=card)
    monkeypatch.setattr(folder_picker, "run_dialog", shown)

    assert folder_picker.pick_folder(None, "Pick", tmp_path) == card
    assert QUrl.fromLocalFile(str(card)) in shown.sidebars[0]
    assert QUrl.fromLocalFile(str(stick)) in shown.sidebars[0]
    assert shown.directories == [tmp_path.as_posix()]


def test_cancel_returns_none(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, on_linux: None
) -> None:
    monkeypatch.setattr(folder_picker, "mount_roots", lambda: ())
    monkeypatch.setattr(folder_picker, "run_dialog", _Shown(accept_with=None))

    assert folder_picker.pick_folder(None, "Pick", tmp_path) is None


def test_off_linux_the_static_picker_is_used_unchanged(
    qapp: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Windows and macOS list their drives already; nothing there changes."""
    monkeypatch.setattr(platform, "detect", lambda: "windows")
    calls: list[tuple[object, str, str]] = []

    def static(parent: object, title: str, start: str = "") -> str:
        calls.append((parent, title, start))
        return ""

    def no_dialog(dialog: QFileDialog) -> bool:
        raise AssertionError("no dialog instance off Linux")

    monkeypatch.setattr(folder_picker.QFileDialog, "getExistingDirectory", static)
    monkeypatch.setattr(folder_picker, "run_dialog", no_dialog)

    assert folder_picker.pick_folder(None, "Pick", tmp_path) is None
    assert calls == [(None, "Pick", str(tmp_path))]


# -- every call site goes through it ------------------------------------------


@pytest.fixture
def a_volume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, on_linux: None) -> Path:
    root = tmp_path / "run-media-deck"
    card = root / "SDCARD"
    card.mkdir(parents=True)
    monkeypatch.setattr(folder_picker, "mount_roots", lambda: (root,))

    def bare_static_picker(*args: object) -> str:
        raise AssertionError("the bare static picker was used: its sidebar has no volumes")

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", bare_static_picker)
    return card


def test_the_install_folder_picker_lists_the_card_and_can_pick_it(
    qapp: object, a_volume: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    shown = _Shown(accept_with=a_volume)
    monkeypatch.setattr(folder_picker, "run_dialog", shown)
    parent = QWidget()
    try:
        chosen = catalog_view._qt_dir_picker(parent, "Install into", tmp_path / "gone" / "deeper")
    finally:
        parent.deleteLater()

    assert chosen == a_volume
    assert QUrl.fromLocalFile(str(a_volume)) in shown.sidebars[0]
    # `_existing_ancestor` still decides where it opens.
    assert shown.directories == [tmp_path.as_posix()]


def test_the_install_folder_picker_cancel_is_none(
    qapp: object, a_volume: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(folder_picker, "run_dialog", _Shown(accept_with=None))

    assert catalog_view._qt_dir_picker(None, "Install into", None) is None  # type: ignore[arg-type]


def test_the_client_folder_picker_lists_the_card_and_can_pick_it(
    qapp: object, a_volume: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shown = _Shown(accept_with=a_volume)
    monkeypatch.setattr(folder_picker, "run_dialog", shown)
    parent = QWidget()
    try:
        chosen = controller_view.ask_module_folder(parent, "Set client folder")
    finally:
        parent.deleteLater()

    assert chosen == a_volume
    assert QUrl.fromLocalFile(str(a_volume)) in shown.sidebars[0]


def test_the_client_folder_picker_cancel_is_none(
    qapp: object, a_volume: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(folder_picker, "run_dialog", _Shown(accept_with=None))

    assert controller_view.ask_module_folder(None, "Set client folder") is None  # type: ignore[arg-type]
