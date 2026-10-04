"""The shared file pickers list SD cards and USB drives on Linux (T215).

A Steam Deck player could not pick a client kept on the micro SD card: Qt's own
folder dialog, which the AppImage gets because it sets no platform theme, has
only Computer and Home in its sidebar, and Computer on Linux is just `/`.
SteamOS mounts the card under `/run/media/deck/<label>` (older SteamOS:
`/run/media/mmcblk0p1`), which nothing in the dialog pointed at.

Every test that opens a dialog replaces `folder_picker.run_dialog`, the one
step that shows it, and looks at the `QFileDialog` the code really built. Those
tests are Linux-only: the dialog instance is the Linux path, and Qt saves a
dialog's state to the platform's own settings store when it is destroyed,
which the suite redirects on Linux only (`conftest`, `QSettings.setPath`; on
Windows and macOS that store is the registry or a plist, which `setPath`
does not move).
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QDir, QEvent, QSettings, QUrl
from PySide6.QtWidgets import QFileDialog, QWidget

from tests.conftest import QT_SETTINGS_SCRATCH
from yulon import platform
from yulon.ui import catalog_view, controller_view, folder_picker, logs_view

linux_only = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the dialog instance is the Linux path; Qt's settings store is redirected on Linux",
)


def _two_volumes(root: Path) -> tuple[Path, Path]:
    card = root / "SDCARD"
    stick = root / "USBSTICK"
    card.mkdir(parents=True)
    stick.mkdir()
    return card, stick


BASE = [QUrl("file:"), QUrl.fromLocalFile(QDir.homePath())]
"""Computer and Home: the sidebar Qt's own dialog starts from, written out.

Written out rather than read from a bare dialog, because a bare dialog's
sidebar is whatever the last one destroyed saved to `QtProject.conf`.
"""


def _url(path: Path) -> QUrl:
    return QUrl.fromLocalFile(str(path))


def _flush_deletes() -> None:
    """Destroy every dialog `deleteLater()` queued: the moment Qt saves its state."""
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class _Shown:
    """A stand-in for `exec()`: records what the dialog held, then answers."""

    def __init__(self, accept_with: Path | None = None) -> None:
        self.accept_with = accept_with
        self.dialogs: list[QFileDialog] = []
        self.sidebars: list[list[QUrl]] = []
        self.directories: list[str] = []
        self.modes: list[QFileDialog.AcceptMode] = []
        self.filters: list[list[str]] = []

    def __call__(self, dialog: QFileDialog) -> bool:
        self.dialogs.append(dialog)
        self.sidebars.append(list(dialog.sidebarUrls()))
        self.directories.append(dialog.directory().absolutePath())
        self.modes.append(dialog.acceptMode())
        self.filters.append(list(dialog.nameFilters()))
        if self.accept_with is None:
            return False
        if dialog.acceptMode() == QFileDialog.AcceptMode.AcceptSave:
            dialog.selectFile(str(self.accept_with))
        else:
            dialog.setDirectory(str(self.accept_with))
        return True


@pytest.fixture
def on_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "detect", lambda: "linux")


@pytest.fixture
def qt_settings() -> Iterator[QSettings]:
    """The `[FileDialog]` group of the (redirected) `QtProject.conf`, emptied around the test."""
    settings = QSettings(QSettings.Scope.UserScope, "QtProject")
    settings.remove("FileDialog")
    settings.sync()
    yield settings
    _flush_deletes()
    settings.remove("FileDialog")
    settings.sync()


# -- the suite never writes the real Qt settings ------------------------------


@linux_only
def test_qt_settings_are_redirected_away_from_the_users_own(qapp: object) -> None:
    """`QtProject.conf` is shared by every Qt app the user has; the suite writes a scratch one."""
    where = Path(QSettings(QSettings.Scope.UserScope, "QtProject").fileName())

    assert QT_SETTINGS_SCRATCH in where.parents
    assert Path.home() / ".config" not in where.parents


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


def test_listing_never_touches_a_mount_point_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hung network mount under `/media/<user>` must not freeze the app when a picker opens.

    Stat, access and resolve all reach into the mounted filesystem; the
    directory listing of the parent (a tmpfs) already says what each entry is.
    """
    card, stick = _two_volumes(tmp_path / "media-user")

    def hung(*args: object, **kwargs: object) -> object:
        raise AssertionError(f"touched a mount point: {args!r}")

    with monkeypatch.context() as patched:
        for name in ("stat", "lstat", "access"):
            patched.setattr(os, name, hung)
        patched.setattr(Path, "resolve", hung)
        patched.setattr(Path, "is_dir", hung)
        listed = folder_picker.removable_volumes([tmp_path / "media-user"])

    assert listed == [card, stick]


def test_the_users_own_folder_under_run_media_is_not_a_volume(tmp_path: Path) -> None:
    """`/run/media` holds `/run/media/deck` itself, which is a root, not a card."""
    run_media = tmp_path / "run-media"
    user_root = run_media / "deck"
    card = user_root / "SDCARD"
    card.mkdir(parents=True)
    old_layout = run_media / "mmcblk0p1"
    old_layout.mkdir()

    assert folder_picker.removable_volumes([user_root, run_media]) == [card, old_layout]


def test_another_users_folder_under_run_media_is_not_a_volume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/run/media/<someone else>` is their mount root, which this user cannot open."""
    run_media = tmp_path / "run-media"
    (run_media / "alice").mkdir(parents=True)
    old_layout = run_media / "mmcblk0p1"
    old_layout.mkdir()
    monkeypatch.setattr(folder_picker, "SHARED_ROOT", run_media)
    monkeypatch.setattr(folder_picker, "_is_login_name", lambda name: name == "alice")

    assert folder_picker.removable_volumes([run_media]) == [old_layout]


def test_a_drive_named_like_an_account_under_the_users_own_root_is_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stick labelled `backup` under `/media/<user>`: Debian has a `backup` account."""
    media_user = tmp_path / "media-user"
    stick = media_user / "backup"
    stick.mkdir(parents=True)
    monkeypatch.setattr(folder_picker, "SHARED_ROOT", tmp_path / "run-media")
    monkeypatch.setattr(folder_picker, "_is_login_name", lambda name: True)

    assert folder_picker.removable_volumes([media_user]) == [stick]


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need a privilege on Windows")
def test_a_symlinked_entry_is_not_listed(tmp_path: Path) -> None:
    """Newer SteamOS keeps `/run/media/mmcblk0p1` as a link to the labelled card.

    Links are skipped (lead's ruling, 2026-10-04): following one would stat
    the mount it points into, and the card it points at is listed under its
    label already. On older SteamOS `mmcblk0p1` is the mount directory itself
    and is listed (the test above).
    """
    run_media = tmp_path / "run-media"
    user_root = run_media / "deck"
    card = user_root / "SDCARD"
    card.mkdir(parents=True)
    (run_media / "mmcblk0p1").symlink_to(card)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (run_media / "a-link").symlink_to(elsewhere)

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


# -- the sidebar ----------------------------------------------------------------


def test_the_sidebar_is_computer_home_and_each_volume(tmp_path: Path) -> None:
    card, stick = _two_volumes(tmp_path / "run-media-deck")

    assert folder_picker.sidebar_urls([card, stick]) == [*BASE, _url(card), _url(stick)]


def test_with_no_volumes_the_sidebar_is_computer_and_home() -> None:
    assert folder_picker.sidebar_urls([]) == BASE


@linux_only
def test_pick_folder_shows_the_volumes_and_returns_the_chosen_one(
    qapp: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    on_linux: None,
    qt_settings: QSettings,
) -> None:
    card, stick = _two_volumes(tmp_path / "run-media-deck")
    monkeypatch.setattr(folder_picker, "mount_roots", lambda: (tmp_path / "run-media-deck",))
    shown = _Shown(accept_with=card)
    monkeypatch.setattr(folder_picker, "run_dialog", shown)

    assert folder_picker.pick_folder(None, "Pick", tmp_path) == card
    assert shown.sidebars == [[*BASE, _url(card), _url(stick)]]
    assert shown.directories == [tmp_path.as_posix()]
    assert shown.dialogs[0].fileMode() == QFileDialog.FileMode.Directory
    assert shown.dialogs[0].testOption(QFileDialog.Option.ShowDirsOnly)


@linux_only
def test_a_second_picker_shows_only_its_own_volumes(
    qapp: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    on_linux: None,
    qt_settings: QSettings,
) -> None:
    """Qt saves the sidebar when a dialog is destroyed and loads it into the next one.

    Built from the saved list, every drive ever listed stayed for good and the
    list only grew.
    """
    first = tmp_path / "first" / "OLDSTICK"
    second = tmp_path / "second" / "SDCARD"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    shown = _Shown(accept_with=None)
    monkeypatch.setattr(folder_picker, "run_dialog", shown)

    monkeypatch.setattr(folder_picker, "mount_roots", lambda: (tmp_path / "first",))
    folder_picker.pick_folder(None, "Pick", tmp_path)
    _flush_deletes()
    monkeypatch.setattr(folder_picker, "mount_roots", lambda: (tmp_path / "second",))
    folder_picker.pick_folder(None, "Pick", tmp_path)

    assert shown.sidebars == [[*BASE, _url(first)], [*BASE, _url(second)]]


@linux_only
def test_a_picker_leaves_the_saved_sidebar_as_it_found_it(
    qapp: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    on_linux: None,
    qt_settings: QSettings,
) -> None:
    """`QtProject.conf` is every Qt app's: a drive Yu'lon lists is not written into it.

    Nor is a bookmark the user made in another Qt app's dialog thrown away.
    """
    bookmark = tmp_path / "my-bookmark"
    bookmark.mkdir()
    another_apps = QFileDialog(None, "another Qt app", str(tmp_path))
    another_apps.setSidebarUrls([*BASE, _url(bookmark)])
    another_apps.deleteLater()
    _flush_deletes()
    qt_settings.sync()
    saved = [url.toString() for url in (*BASE, _url(bookmark))]
    assert qt_settings.value("FileDialog/shortcuts") == saved
    card = tmp_path / "run-media-deck" / "SDCARD"
    card.mkdir(parents=True)
    monkeypatch.setattr(folder_picker, "mount_roots", lambda: (tmp_path / "run-media-deck",))
    shown = _Shown(accept_with=card)
    monkeypatch.setattr(folder_picker, "run_dialog", shown)

    folder_picker.pick_folder(None, "Pick", tmp_path)
    _flush_deletes()

    assert shown.sidebars == [[*BASE, _url(card)]]
    qt_settings.sync()
    assert qt_settings.value("FileDialog/shortcuts") == saved


@linux_only
def test_cancel_returns_none(
    qapp: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    on_linux: None,
    qt_settings: QSettings,
) -> None:
    monkeypatch.setattr(folder_picker, "mount_roots", lambda: ())
    monkeypatch.setattr(folder_picker, "run_dialog", _Shown(accept_with=None))

    assert folder_picker.pick_folder(None, "Pick", tmp_path) is None


@linux_only
def test_the_log_says_how_many_drives_and_never_their_paths(
    qapp: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    on_linux: None,
    qt_settings: QSettings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Mount paths carry the user name and the card's label; the log goes into support files."""
    _two_volumes(tmp_path / "run-media-deck")
    monkeypatch.setattr(folder_picker, "mount_roots", lambda: (tmp_path / "run-media-deck",))
    monkeypatch.setattr(folder_picker, "run_dialog", _Shown(accept_with=None))

    with caplog.at_level(logging.DEBUG, logger="yulon"):
        folder_picker.pick_folder(None, "Pick", tmp_path)

    said = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("yulon"))
    assert "2 removable drives" in said
    assert "SDCARD" not in said
    assert "run-media-deck" not in said


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


def test_off_linux_the_static_save_picker_is_used_unchanged(
    qapp: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(platform, "detect", lambda: "macos")
    calls: list[tuple[object, str, str, str]] = []

    def static(parent: object, title: str, start: str, name_filter: str) -> tuple[str, str]:
        calls.append((parent, title, start, name_filter))
        return str(tmp_path / "logs"), name_filter

    def no_dialog(dialog: QFileDialog) -> bool:
        raise AssertionError("no dialog instance off Linux")

    monkeypatch.setattr(folder_picker.QFileDialog, "getSaveFileName", static)
    monkeypatch.setattr(folder_picker, "run_dialog", no_dialog)

    chosen = logs_view._qt_save_picker(None, tmp_path / "suggested.zip")  # type: ignore[arg-type]

    assert chosen == tmp_path / "logs.zip"
    assert calls == [
        (None, "Save logs for support", str(tmp_path / "suggested.zip"), "Zip files (*.zip)")
    ]


# -- every call site goes through it ------------------------------------------


@pytest.fixture
def a_volume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, on_linux: None, qt_settings: QSettings
) -> Path:
    root = tmp_path / "run-media-deck"
    card = root / "SDCARD"
    card.mkdir(parents=True)
    monkeypatch.setattr(folder_picker, "mount_roots", lambda: (root,))

    def bare_static_picker(*args: object) -> str:
        raise AssertionError("the bare static picker was used: its sidebar has no volumes")

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", bare_static_picker)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", bare_static_picker)
    return card


@linux_only
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
    assert shown.sidebars == [[*BASE, _url(a_volume)]]
    # `_existing_ancestor` still decides where it opens.
    assert shown.directories == [tmp_path.as_posix()]


@linux_only
def test_the_install_folder_picker_cancel_is_none(
    qapp: object, a_volume: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(folder_picker, "run_dialog", _Shown(accept_with=None))

    assert catalog_view._qt_dir_picker(None, "Install into", None) is None  # type: ignore[arg-type]


@linux_only
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
    assert shown.sidebars == [[*BASE, _url(a_volume)]]


@linux_only
def test_the_client_folder_picker_cancel_is_none(
    qapp: object, a_volume: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(folder_picker, "run_dialog", _Shown(accept_with=None))

    assert controller_view.ask_module_folder(None, "Set client folder") is None  # type: ignore[arg-type]


@linux_only
def test_the_support_file_save_picker_lists_the_card_and_saves_a_zip_on_it(
    qapp: object, a_volume: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    shown = _Shown(accept_with=a_volume / "yulon-support")
    monkeypatch.setattr(folder_picker, "run_dialog", shown)

    chosen = logs_view._qt_save_picker(None, tmp_path / "yulon-support-2026.zip")  # type: ignore[arg-type]

    assert chosen == a_volume / "yulon-support.zip"
    assert shown.sidebars == [[*BASE, _url(a_volume)]]
    assert shown.modes == [QFileDialog.AcceptMode.AcceptSave]
    assert shown.filters == [["Zip files (*.zip)"]]
    assert shown.directories == [tmp_path.as_posix()]
    assert shown.dialogs[0].windowTitle() == "Save logs for support"


@linux_only
def test_the_support_file_save_picker_keeps_a_zip_name_as_typed(
    qapp: object, a_volume: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(folder_picker, "run_dialog", _Shown(accept_with=a_volume / "Logs.ZIP"))

    chosen = logs_view._qt_save_picker(None, tmp_path / "s.zip")  # type: ignore[arg-type]

    assert chosen == a_volume / "Logs.ZIP"


@linux_only
def test_the_support_file_save_picker_cancel_is_none(
    qapp: object, a_volume: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(folder_picker, "run_dialog", _Shown(accept_with=None))

    assert logs_view._qt_save_picker(None, tmp_path / "s.zip") is None  # type: ignore[arg-type]
