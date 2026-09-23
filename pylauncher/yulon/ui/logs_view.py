"""The Logs tab (T93): read what Yu'lon kept, and save one file to send when something is wrong.

Everything shown here has been through the redactor: the viewer, the copied
lines and the zip. The files on disk stay raw.

The tab reads nothing when it is built -- only when it is SHOWN (and on
Refresh). A window is built before a test has pointed `config_dir()` at a
scratch folder (`test_main.py`'s module-scoped window), and the first thing a
refresh reads is the credential store.

"Open log folder" opens `logs/` (runs and snapshots) and never the folder that
holds `yulon.log`: that one also holds `credentials/` and `db-secrets/` in
clear text, and a player who zips the folder they were shown must not be able
to send those (lead ruling, 2026-09-23). The app log reaches support through
the zip, redacted.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QStandardPaths, QUrl, Slot, qVersion
from PySide6.QtGui import QDesktopServices, QGuiApplication, QShowEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from yulon.catalog.catalog import Catalog
from yulon.log import get_logger
from yulon.state import KnownInstall
from yulon.support import bundle, runlog
from yulon.support import sources as support_sources
from yulon.support.redact import Redactor
from yulon.ui.widgets.flow_layout import flow_bar
from yulon.ui.widgets.job import JobRunner, threaded_job_runner

logger = get_logger(__name__)

COPY_LINES = 200
"""What "Copy last 200 lines" copies.

Discord cuts a message at 2000 characters, so a paste is for a quick look; the
support file is the real route.
"""

OPEN_FOLDER_TIP = "Your run logs and server snapshots. Yu'lon's own log goes into the support file."

SavePicker = Callable[[QWidget, Path], Path | None]


def _qt_save_picker(parent: QWidget, suggested: Path) -> Path | None:
    chosen, _filter = QFileDialog.getSaveFileName(
        parent, "Save logs for support", str(suggested), "Zip files (*.zip)"
    )
    if not chosen:
        return None
    path = Path(chosen)
    return path if path.suffix.lower() == ".zip" else path.with_name(path.name + ".zip")


def _default_folder() -> Path:
    where = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
    return Path(where) if where else Path.home()


def _set_clipboard(text: str) -> None:
    QGuiApplication.clipboard().setText(text)


def _open_folder(folder: Path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def _size_text(size: int) -> str:
    """Bytes as a person reads them: `3.2 MB`, `412 KB`. Decimal, as Discord states its limit."""
    if size >= 1_000_000:
        return f"{size / 1_000_000:.1f} MB"
    return f"{max(1, round(size / 1000))} KB"


def _why_not_saved(name: str, error: object) -> str:
    """One line for the status bar: which file, what the OS said, and what to try."""
    if isinstance(error, OSError):
        said = error.strerror or type(error).__name__
        if isinstance(error, PermissionError):
            hint = (
                "If the old file is open somewhere (a chat upload, a preview), close it and "
                "try again, or pick another folder."
            )
        else:
            hint = "Try again, or pick another folder."
        return f"Could not save {name}: {said}. {hint}"
    return (
        f"Could not save {name}: something went wrong ({type(error).__name__}). "
        "Yu'lon's own log has the details."
    )


class LogsView(QWidget):
    """Source picker, redacted viewer, and Save / Open folder / Copy / Refresh."""

    def __init__(
        self,
        installs: Callable[[], Sequence[KnownInstall]],
        catalog: Catalog,
        *,
        jobs: JobRunner | None = None,
        pick_save_path: SavePicker = _qt_save_picker,
        clipboard: Callable[[str], None] = _set_clipboard,
        open_folder: Callable[[Path], None] = _open_folder,
        bundle_seams: bundle.Seams | None = None,
        now: Callable[[], datetime] = datetime.now,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._installs = installs
        self._catalog = catalog
        self._jobs: JobRunner = jobs if jobs is not None else threaded_job_runner(self)
        self._pick_save_path = pick_save_path
        self._clipboard = clipboard
        self._open_folder = open_folder
        self._bundle_seams = bundle_seams
        self._now = now
        self._redactor: Redactor | None = None
        self._saving_to: Path | None = None

        intro = QLabel(
            "Something not working? Press <b>Save logs for support…</b> and send us the file "
            "it makes. Passwords and your home folder are taken out first.",
            self,
        )
        intro.setWordWrap(True)
        self.source_picker = QComboBox(self)
        # Never as wide as the longest run-log name: that width would become the
        # window's minimum (T32's lesson, `LogPanel`'s console picker).
        self.source_picker.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.source_picker.setMinimumContentsLength(24)
        self.source_picker.currentIndexChanged.connect(self._show_selected)
        self.viewer = QPlainTextEdit(self)
        self.viewer.setReadOnly(True)
        self.viewer.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.save_button = QPushButton("Save logs for support…", self)
        self.save_button.setToolTip(
            "One zip of every log and settings file, passwords taken out, to send to support"
        )
        self.save_button.clicked.connect(self.save_for_support)
        self.open_folder_button = QPushButton("Open log folder", self)
        self.open_folder_button.setToolTip(OPEN_FOLDER_TIP)
        self.open_folder_button.clicked.connect(self.open_log_folder)
        self.copy_button = QPushButton(f"Copy last {COPY_LINES} lines", self)
        self.copy_button.clicked.connect(self.copy_last_lines)
        self.refresh_button = QPushButton("Refresh", self)
        self.refresh_button.clicked.connect(self.refresh)
        self.status = QLabel("", self)
        self.status.setWordWrap(True)

        bar = flow_bar(self)
        for button in (
            self.save_button,
            self.open_folder_button,
            self.copy_button,
            self.refresh_button,
        ):
            bar.flow().addWidget(button)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self.source_picker)
        layout.addWidget(self.viewer, 1)
        layout.addWidget(bar)
        layout.addWidget(self.status)

    # -- reading ----------------------------------------------------------

    def _sources(self) -> support_sources.Sources:
        return support_sources.sources_for_app(
            self._installs(), self._catalog, qt_version=qVersion()
        )

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802  (Qt's own name)
        super().showEvent(event)
        self.refresh()

    @Slot()
    def refresh(self) -> None:
        """Re-read what exists, rebuild the redactor, keep the selection where it was."""
        sources = self._sources()
        known = support_sources.gather_known(sources)
        self._redactor = Redactor.build(known.values, home=Path.home())
        chosen = self.source_picker.currentData()
        self.source_picker.blockSignals(True)
        try:
            self.source_picker.clear()
            for item in support_sources.viewables(sources):
                self.source_picker.addItem(item.label, str(item.path))
            index = self.source_picker.findData(chosen) if chosen else -1
            self.source_picker.setCurrentIndex(max(index, 0))
        finally:
            self.source_picker.blockSignals(False)
        self._show_selected()

    @Slot()
    def _show_selected(self) -> None:
        data = self.source_picker.currentData()
        if not data or self._redactor is None:
            self.viewer.setPlainText("")
            self.viewer.setPlaceholderText("Nothing has been logged yet.")
            return
        try:
            text = support_sources.read_tail(Path(str(data)))
        except OSError as exc:
            text = f"This file could not be read: {exc.strerror or type(exc).__name__}"
        self.viewer.setPlainText(self._redactor.redact(text))
        scrollbar = self.viewer.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def shown_text(self) -> str:
        """What the viewer shows -- already redacted."""
        return self.viewer.toPlainText()

    # -- the buttons --------------------------------------------------------

    @Slot()
    def copy_last_lines(self) -> None:
        """Put the viewer's last `COPY_LINES` lines on the clipboard."""
        lines = self.shown_text().splitlines()[-COPY_LINES:]
        if not lines:
            self.status.setText("Nothing to copy yet.")
            return
        self._clipboard("\n".join(lines))
        self.status.setText(f"Copied the last {len(lines)} lines, passwords already taken out.")

    @Slot()
    def open_log_folder(self) -> None:
        """Open `logs/` (runs and snapshots) in the file manager, making it first if need be."""
        folder = runlog.logs_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.status.setText(
                f"Could not open the log folder {folder}: {exc.strerror or type(exc).__name__}."
            )
            return
        self._open_folder(folder)

    @Slot()
    def save_for_support(self) -> bool:
        """Ask where, then build the zip on a worker thread. False if nothing was started."""
        if self._saving_to is not None:
            return False
        suggested = _default_folder() / f"yulon-support-{self._now():%Y%m%d-%H%M%S}.zip"
        dest = self._pick_save_path(self, suggested)
        if dest is None:
            logger.debug("support file: the save dialog was cancelled")
            return False
        sources = self._sources()
        seams = self._bundle_seams
        self._set_saving(dest)
        self.status.setText("Saving the support file… reading each server's log can take a minute.")
        self._jobs(lambda: bundle.save(dest, sources, seams=seams), self._saved, self._save_failed)
        return True

    @Slot(object)
    def _saved(self, report: object) -> None:
        self._set_saving(None)
        if not isinstance(report, bundle.BundleReport):
            return
        skipped = (
            f" {len(report.skipped)} skipped, MANIFEST.txt says why." if report.skipped else ""
        )
        text = (
            f"Saved {report.path} ({_size_text(report.size)}). Send this file; the passwords "
            f"are already taken out.{skipped}"
        )
        if report.size > bundle.ZIP_CAP:
            text += (
                f" It is larger than the {bundle.ZIP_CAP // 1_000_000} MB Yu'lon aims for, so it "
                "may not fit Discord's free upload limit; MANIFEST.txt inside lists what was "
                "trimmed."
            )
        self.status.setText(text)
        logger.info(f"support file saved: {len(report.included)} files, {report.size} bytes")

    @Slot(object)
    def _save_failed(self, error: object) -> None:
        name = self._saving_to.name if self._saving_to is not None else "the support file"
        self._set_saving(None)
        self.status.setText(_why_not_saved(name, error))

    def _set_saving(self, dest: Path | None) -> None:
        self._saving_to = dest
        self.save_button.setEnabled(dest is None)

    def busy_reason(self) -> str | None:
        """Why the window must not close now, or None. Read by `main._busy_reasons()`."""
        if self._saving_to is None:
            return None
        return (
            "Yu'lon is still saving the support file. It finishes on its own within a "
            "minute or two; close the window again then."
        )
