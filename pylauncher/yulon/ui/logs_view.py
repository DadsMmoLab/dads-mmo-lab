"""The Logs tab (T93): read what Yu'lon kept, and save one file to send when something is wrong.

Everything shown here has been through the redactor: the viewer, the copied
lines and the zip. The files on disk stay raw.

The tab reads nothing when it is built -- only when it is SHOWN (and on
Refresh). A window is built before a test has pointed `config_dir()` at a
scratch folder (`test_main.py`'s module-scoped window), and the first thing a
refresh reads is the credential store.

**Every read is a job, never GUI-thread work** (T93 review). A read lists the
credential stores and every install's conf folders, and an adopted server's
folder is a path under `wsl.localhost`: touching it boots a stopped distro, and
a server on an unplugged drive blocks for the OS timeout. So each read --
sources, known passwords, redactor, file tail, redaction -- runs through the
`JobRunner` as one `_read_logs()` call, and the viewer says `READING` until
it lands. Each read carries a generation number and only the newest one is
painted, so a slow read never overwrites a newer one. Each read also builds its
own redactor, so a password stored since the last read (a channel credential at
the end of an install) is masked when the next file is picked.

"Open log folder" opens `logs/` (runs and snapshots) and never the folder that
holds `yulon.log`: that one also holds `credentials/` and `db-secrets/` in
clear text, and a player who zips the folder they were shown must not be able
to send those (lead ruling, 2026-09-23). The app log reaches support through
the zip, redacted.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
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

OPEN_FOLDER_TIP = (
    "Your run logs and server snapshots, as written \u2014 passwords are NOT taken out of "
    "these. Send the support file instead; Yu'lon's own log goes into it too."
)

READING = "Reading…"
"""What the viewer says while a read is in flight. Copy refuses to copy it."""

NOTHING_LOGGED = "Nothing has been logged yet."

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


@dataclass(frozen=True)
class _Read:
    """One read's result, painted only if `generation` is still the newest."""

    generation: int
    items: tuple[support_sources.Viewable, ...]
    shown: Path | None
    text: str


class _ReadFailed(Exception):
    """A read that raised past `_read_logs`, tagged with its generation like a `_Read`.

    The job runner hands `_read_failed` only the exception, and its callbacks
    must be the view's own bound slots (`widgets/job.py`), so the generation
    travels on the exception rather than in a closure.
    """

    def __init__(self, generation: int, cause: Exception) -> None:
        super().__init__(f"{type(cause).__name__}: {cause}")
        self.generation = generation
        self.cause = cause


def _read_tagged(
    installs: Sequence[KnownInstall],
    catalog: Catalog,
    qt_version: str,
    wanted: str | None,
    generation: int,
) -> _Read:
    """`_read_logs`, with anything it lets out raised again as `_ReadFailed(generation)`."""
    try:
        return _read_logs(installs, catalog, qt_version, wanted, generation)
    except Exception as exc:  # boundary: tag it, `_read_failed` decides whether it is shown
        raise _ReadFailed(generation, exc) from exc


def _read_logs(
    installs: Sequence[KnownInstall],
    catalog: Catalog,
    qt_version: str,
    wanted: str | None,
    generation: int,
) -> _Read:
    """List what can be shown and read `wanted` (else the first), redacted. Never raises.

    Runs on a worker thread. The redactor is built HERE, for this read, from
    the passwords known now.
    """
    try:
        sources = support_sources.sources_for_app(installs, catalog, qt_version=qt_version)
        known = support_sources.gather_known(sources)
        redactor = Redactor.build(known.values, home=Path.home())
        # A label is a file name and is shown too, so it is redacted like a line.
        items = tuple(
            support_sources.Viewable(redactor.redact(item.label), item.path)
            for item in support_sources.viewables(sources)
        )
    except Exception as exc:  # boundary: the tab says so rather than losing the read
        logger.warning(f"the Logs tab could not list its files: {type(exc).__name__}")
        return _Read(generation, (), None, f"The logs could not be listed ({type(exc).__name__}).")
    shown = next((item.path for item in items if str(item.path) == wanted), None)
    if shown is None and items:
        shown = items[0].path
    if shown is None:
        return _Read(generation, items, None, "")
    try:
        text = support_sources.read_tail(shown)
    except OSError as exc:
        text = f"This file could not be read: {exc.strerror or type(exc).__name__}"
    return _Read(generation, items, shown, redactor.redact(text))


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
        self._generation = 0
        """The newest read started; a result from any other is dropped."""
        self._reading = False
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
        self.source_picker.currentIndexChanged.connect(self._on_pick)
        self.viewer = QPlainTextEdit(self)
        self.viewer.setReadOnly(True)
        self.viewer.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.viewer.setPlaceholderText(NOTHING_LOGGED)
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

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802  (Qt's own name)
        super().showEvent(event)
        self.refresh()

    @Slot()
    def refresh(self) -> None:
        """Re-list what exists and re-read the picked file, keeping the selection."""
        self._start_read(self.source_picker.currentData())

    @Slot(int)
    def _on_pick(self, index: int) -> None:
        if index >= 0:
            self._start_read(self.source_picker.itemData(index))

    def _start_read(self, wanted: object) -> None:
        """Start a read on the job runner; the viewer says `READING` until it lands."""
        self._generation += 1
        generation = self._generation
        # Taken on this thread, where they live: the install list and the Qt version.
        installs = list(self._installs())
        catalog = self._catalog
        qt_version = qVersion()
        chosen = str(wanted) if wanted else None
        self._reading = True
        self.viewer.setPlainText(READING)
        self._jobs(
            lambda: _read_tagged(installs, catalog, qt_version, chosen, generation),
            self._read_done,
            self._read_failed,
        )

    @Slot(object)
    def _read_done(self, result: object) -> None:
        if not isinstance(result, _Read) or result.generation != self._generation:
            return
        self._reading = False
        self.source_picker.blockSignals(True)
        try:
            self.source_picker.clear()
            for item in result.items:
                self.source_picker.addItem(item.label, str(item.path))
            if result.shown is not None:
                self.source_picker.setCurrentIndex(self.source_picker.findData(str(result.shown)))
        finally:
            self.source_picker.blockSignals(False)
        self.viewer.setPlainText(result.text)
        scrollbar = self.viewer.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    @Slot(object)
    def _read_failed(self, error: object) -> None:
        # `_read_logs` catches everything it can meet, so this is the boundary
        # behind it. Like `_read_done`, only the newest read may speak: an older
        # one failing while a newer one is in flight must leave `READING` up.
        if isinstance(error, _ReadFailed):
            if error.generation != self._generation:
                return
            error = error.cause
        logger.debug(f"a Logs tab read failed: {type(error).__name__}")
        if self._reading:
            self._reading = False
            self.viewer.setPlainText(f"The logs could not be read ({type(error).__name__}).")

    def shown_text(self) -> str:
        """What the viewer shows -- already redacted."""
        return self.viewer.toPlainText()

    # -- the buttons --------------------------------------------------------

    @Slot()
    def copy_last_lines(self) -> None:
        """Put the viewer's last `COPY_LINES` lines on the clipboard: what it shows, redacted."""
        if self._reading:
            self.status.setText("Still reading this log; copy again in a moment.")
            return
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
        installs = list(self._installs())
        catalog = self._catalog
        qt_version = qVersion()
        seams = self._bundle_seams

        def work() -> bundle.BundleReport:
            # Worker thread, like every other read here (see the module docstring).
            sources = support_sources.sources_for_app(installs, catalog, qt_version=qt_version)
            return bundle.save(dest, sources, seams=seams)

        self._set_saving(dest)
        self.status.setText("Saving the support file… reading each server's log can take a minute.")
        self._jobs(work, self._saved, self._save_failed)
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
