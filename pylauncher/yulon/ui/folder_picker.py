"""The one folder picker every "choose a folder" button opens (T215).

**Why this exists.** On Linux the app gets Qt's own folder dialog: the AppImage
bundles its own Qt and sets no platform theme, so neither Plasma's dialog nor
the desktop portal is used. That dialog's sidebar holds Computer and Home, and
Computer on Linux is only `/`. An SD card or USB drive is mounted somewhere
under `/run/media/<user>/` (Steam Deck, Fedora, Arch, openSUSE), under
`/run/media/` itself (older SteamOS: `/run/media/mmcblk0p1`), or under
`/media/<user>/` (Ubuntu, Debian), and nothing in the dialog pointed there: a
Deck player whose client lives on the micro SD card could not find it.

So on Linux the dialog is built here as an instance, and each mounted volume
under those roots is added to the sidebar after the entries it already had.
Nothing else about the dialog changes: no option decides native versus Qt's
own, so a desktop whose platform theme gives a native dialog still gets it
(that dialog lists drives itself and ignores the sidebar), and the gamepad
navigation (T175), which works on the active modal widget, finds the same
`QFileDialog` it found before.

Windows and macOS already list their drives, so off Linux the static
`QFileDialog.getExistingDirectory()` is called exactly as before.

`mount_roots` and `run_dialog` are module attributes rather than defaults bound
at definition, so a test can hand in a fake mount tree and stand in for `exec()`
while every caller keeps calling `pick_folder()`.
"""

from __future__ import annotations

import getpass
import os
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from PySide6.QtCore import QUrl
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


def removable_volumes(roots: Iterable[Path]) -> list[Path]:
    """Each readable directory directly under one of `roots`, once, in root order.

    A root that does not exist or cannot be read adds nothing. Plain files and
    entries this user cannot open are left out (another user's
    `/run/media/<them>` among them), and so is an entry that is itself one of
    the roots: `/run/media` holds `/run/media/<user>`, which is where the cards
    are, not a card.
    """
    root_list = list(roots)
    seen: set[Path] = set()
    volumes: list[Path] = []
    for root in root_list:
        try:
            entries = sorted(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry in root_list:
                continue
            try:
                if not entry.is_dir() or not os.access(entry, os.R_OK | os.X_OK):
                    continue
                real = entry.resolve()
            except OSError:
                continue
            if real in seen:
                continue
            seen.add(real)
            volumes.append(entry)
    return volumes


def build_folder_dialog(
    parent: QWidget | None, title: str, start: Path | None, volumes: Sequence[Path]
) -> QFileDialog:
    """A folder dialog set up as the static picker sets one up, plus `volumes` in its sidebar."""
    dialog = QFileDialog(parent, title, str(start) if start else "")
    dialog.setFileMode(QFileDialog.FileMode.Directory)
    dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
    dialog.setSupportedSchemes(["file"])
    if volumes:
        sidebar = list(dialog.sidebarUrls())
        for volume in volumes:
            url = QUrl.fromLocalFile(str(volume))
            if url not in sidebar:
                sidebar.append(url)
        dialog.setSidebarUrls(sidebar)
    return dialog


def _run_dialog(dialog: QFileDialog) -> bool:
    """Show `dialog` modally; True when the user chose a folder."""
    return bool(dialog.exec() == QDialog.DialogCode.Accepted.value)


run_dialog: Callable[[QFileDialog], bool] = _run_dialog
"""Seam: the one step that shows the dialog."""


def pick_folder(parent: QWidget | None, title: str, start: Path | None = None) -> Path | None:
    """A folder the user chose, or None if they cancelled.

    Cancel comes back from Qt as `""`, which as a `Path` would be `Path(".")`,
    the process's working directory; it is turned back into None here.
    """
    if platform.detect() != "linux":
        chosen = QFileDialog.getExistingDirectory(parent, title, str(start) if start else "")
        return Path(chosen) if chosen else None
    volumes = removable_volumes(mount_roots())
    if volumes:
        logger.info(f"folder picker: listing removable volumes {[str(v) for v in volumes]}")
    dialog = build_folder_dialog(parent, title, start, volumes)
    try:
        if not run_dialog(dialog):
            return None
        files = dialog.selectedFiles()
        chosen = files[0] if files else ""
    finally:
        dialog.deleteLater()
    return Path(chosen) if chosen else None
