"""The one picker every "choose a folder" and "save as" button opens (T215).

**Why this exists.** On Linux the app gets Qt's own file dialog: the AppImage
bundles its own Qt and sets no platform theme, so neither Plasma's dialog nor
the desktop portal is used. That dialog's sidebar holds Computer and Home, and
Computer on Linux is only `/`. An SD card or USB drive is mounted somewhere
under `/run/media/<user>/` (Steam Deck, Fedora, Arch, openSUSE), under
`/run/media/` itself (older SteamOS: `/run/media/mmcblk0p1`), or under
`/media/<user>/` (Ubuntu, Debian), and nothing in the dialog pointed there: a
Deck player whose client lives on the micro SD card could not find it.

So on Linux the dialog is built here as an instance, and its sidebar is
Computer, Home and each volume mounted under those roots right now. Nothing
else about the dialog changes: no option decides native versus Qt's own, so a
desktop whose platform theme gives a native dialog still gets it (that dialog
lists drives itself and ignores the sidebar), and the gamepad navigation
(T175), which works on the active modal widget, finds the same `QFileDialog`
it found before.

**Qt saves the sidebar.** A widget `QFileDialog` writes its sidebar to
`QtProject.conf` when it is destroyed, shown or not, and loads it into the next
one; that file is shared by every Qt app the user runs. The sidebar shown is
therefore built from Computer and Home, never from the saved list (built from
it, every drive ever listed stayed for good and the list only grew), and the
saved list is put back before the dialog goes, so no drive is written into the
file and a bookmark made in another Qt app survives.

Windows and macOS already list their drives, so off Linux the static
`QFileDialog` functions are called exactly as before.

`mount_roots` and `run_dialog` are module attributes rather than defaults bound
at definition, so a test can hand in a fake mount tree and stand in for `exec()`
while every caller keeps calling `pick_folder()` / `pick_save_file()`.
"""

from __future__ import annotations

import getpass
import os
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from PySide6.QtCore import QDir, QUrl
from PySide6.QtWidgets import QDialog, QFileDialog, QWidget

from yulon import platform
from yulon.log import get_logger

logger = get_logger(__name__)


def _user_name() -> str | None:
    """The login name udisks names `/run/media/<user>` after, or None if unknown."""
    try:
        return getpass.getuser() or None
    except (OSError, KeyError):
        return None


def default_mount_roots() -> tuple[Path, ...]:
    """Where Linux desktops mount a removable volume, the user's own folder first.

    First so that a volume reached twice (newer SteamOS keeps
    `/run/media/mmcblk0p1` as a link to `/run/media/deck/<label>`) is listed
    under its labelled name.
    """
    user = _user_name()
    if user is None:
        return (Path("/run/media"),)
    return (Path("/run/media") / user, Path("/run/media"), Path("/media") / user)


mount_roots: Callable[[], tuple[Path, ...]] = default_mount_roots
"""Seam: the roots `pick_folder()` looks under."""

SHARED_ROOT = Path("/run/media")
"""The one root that also holds each user's own mount root, `/run/media/<user>`."""


PASSWD_FILE = Path("/etc/passwd")
"""Seam: the local account list `_is_login_name()` reads."""


def _is_login_name(name: str) -> bool:
    """Is `name` a local account? Then `/run/media/<name>` is its mount root, not a drive.

    Reads the local `/etc/passwd` itself and never asks `pwd`/NSS: on a desktop
    whose accounts come from sssd or LDAP, a lookup while that server is
    unreachable blocks, and this runs on the GUI thread as a picker opens.
    Such accounts are not in the file, so their `/run/media/<them>` folder is
    listed; it is a folder this user cannot open, which is the lesser harm. An
    unreadable or missing file gives no names, so nothing is left out.
    """
    try:
        text = PASSWD_FILE.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    for line in text.splitlines():
        if ":" not in line:
            continue
        if line.split(":", 1)[0] == name:
            return True
    return False


def removable_volumes(roots: Iterable[Path]) -> list[Path]:
    """Each directory directly under one of `roots`, in root order.

    **Never touches a mount point itself.** The listing comes from the root's
    own directory (a tmpfs), whose entries already say whether each is a
    directory, so nothing here stats, opens or resolves the volume mounted
    there: a hung network mount under `/media/<user>` would otherwise freeze
    the app when a picker opens. For the same reason a symlinked entry is
    skipped rather than followed; newer SteamOS keeps `/run/media/mmcblk0p1`
    as a link to `/run/media/deck/<label>`, which is listed under its label.

    A root that does not exist or cannot be read adds nothing. Plain files are
    left out, and so is an entry that is itself one of the roots. In
    `SHARED_ROOT` an entry named after an account on this machine is left out
    too: `/run/media` holds `/run/media/<user>` for each user who mounted
    something, which is a mount root, not a drive. Only there: under
    `/media/<user>` a stick labelled `backup` or `games` (both system
    accounts on Debian) is a drive.
    """
    root_list = list(roots)
    volumes: list[Path] = []
    for root in root_list:
        try:
            with os.scandir(root) as listing:
                entries = sorted(listing, key=lambda entry: entry.name)
        except OSError:
            continue
        for entry in entries:
            path = root / entry.name
            if path in root_list:
                continue
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if root == SHARED_ROOT and _is_login_name(entry.name):
                continue
            volumes.append(path)
    return volumes


def base_sidebar() -> list[QUrl]:
    """Computer and Home: what Qt's own dialog starts with before any saved state."""
    return [QUrl("file:"), QUrl.fromLocalFile(QDir.homePath())]


def sidebar_urls(volumes: Sequence[Path]) -> list[QUrl]:
    """The sidebar a picker shows: Computer, Home, then each volume once."""
    sidebar = base_sidebar()
    for volume in volumes:
        url = QUrl.fromLocalFile(str(volume))
        if url not in sidebar:
            sidebar.append(url)
    return sidebar


def build_folder_dialog(parent: QWidget | None, title: str, start: Path | None) -> QFileDialog:
    """A folder dialog set up as `QFileDialog.getExistingDirectory()` sets one up."""
    dialog = QFileDialog(parent, title, str(start) if start else "")
    dialog.setFileMode(QFileDialog.FileMode.Directory)
    dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
    dialog.setSupportedSchemes(["file"])
    return dialog


def build_save_dialog(
    parent: QWidget | None, title: str, suggested: Path, name_filter: str
) -> QFileDialog:
    """A save dialog set up as `QFileDialog.getSaveFileName()` sets one up.

    The suggested path is split by Qt as the static function splits it: the
    dialog opens in its folder with its name typed in.
    """
    dialog = QFileDialog(parent, title, str(suggested), name_filter)
    dialog.setFileMode(QFileDialog.FileMode.AnyFile)
    dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
    dialog.setSupportedSchemes(["file"])
    return dialog


def build_open_dialog(
    parent: QWidget | None, title: str, start: Path | None, name_filter: str
) -> QFileDialog:
    """An open-a-file dialog set up as `QFileDialog.getOpenFileName()` sets one up (T601)."""
    dialog = QFileDialog(parent, title, str(start) if start else "", name_filter)
    dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
    dialog.setSupportedSchemes(["file"])
    return dialog


def _run_dialog(dialog: QFileDialog) -> bool:
    """Show `dialog` modally; True when the user chose a folder."""
    return bool(dialog.exec() == QDialog.DialogCode.Accepted.value)


run_dialog: Callable[[QFileDialog], bool] = _run_dialog
"""Seam: the one step that shows the dialog."""


def _choose(dialog: QFileDialog) -> str | None:
    """Show `dialog` with the drives in its sidebar; the path chosen, or None on cancel.

    The saved sidebar is read before it is replaced and put back before the
    dialog is destroyed, so what Qt writes to `QtProject.conf` is what was
    there (see the module docstring). Qt's `""` for cancel becomes None.
    """
    volumes = removable_volumes(mount_roots())
    if volumes:
        logger.info(f"file picker: listing {len(volumes)} removable drives")
    saved = list(dialog.sidebarUrls())
    dialog.setSidebarUrls(sidebar_urls(volumes))
    try:
        if not run_dialog(dialog):
            return None
        files = dialog.selectedFiles()
        return files[0] if files and files[0] else None
    finally:
        dialog.setSidebarUrls(saved)
        dialog.deleteLater()


def pick_folder(parent: QWidget | None, title: str, start: Path | None = None) -> Path | None:
    """A folder the user chose, or None if they cancelled.

    Cancel comes back from Qt as `""`, which as a `Path` would be `Path(".")`,
    the process's working directory; it is turned back into None here.
    """
    if platform.detect() != "linux":
        chosen = QFileDialog.getExistingDirectory(parent, title, str(start) if start else "")
        return Path(chosen) if chosen else None
    chosen_path = _choose(build_folder_dialog(parent, title, start))
    return Path(chosen_path) if chosen_path else None


def pick_save_file(
    parent: QWidget | None, title: str, suggested: Path, name_filter: str
) -> str | None:
    """The file name the user chose to save as, as typed, or None if they cancelled."""
    if platform.detect() != "linux":
        chosen, _filter = QFileDialog.getSaveFileName(parent, title, str(suggested), name_filter)
        return chosen or None
    return _choose(build_save_dialog(parent, title, suggested, name_filter))


def pick_open_file(
    parent: QWidget | None, title: str, start: Path | None, name_filter: str
) -> Path | None:
    """An existing file the user chose, or None if they cancelled (T601: a move package)."""
    if platform.detect() != "linux":
        chosen, _filter = QFileDialog.getOpenFileName(
            parent, title, str(start) if start else "", name_filter
        )
        return Path(chosen) if chosen else None
    chosen_path = _choose(build_open_dialog(parent, title, start, name_filter))
    return Path(chosen_path) if chosen_path else None
