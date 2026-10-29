"""The Server tab's "Update client" beside Play, for a game whose makers publish its client (T825).

Only the press, its Cancel and one line of text: the work is `yulon.coa_client`'s,
run off the GUI thread through the view's job runner, with its progress lines
crossing back through a `LineRelay`. Nothing here ever blocks Play, and a check
that fails says `coa_client.CHECK_FAILED` instead of raising.

The check at tab open reads only the manifest and the folder's recorded version
(`coa_client.status`), with a short deadline. Update client then reads every
file (`plan`), says in one sentence what it would download, asks (with the
Keep/Replace choice when the player changed files), and applies.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Slot
from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton, QWidget

from yulon import coa_client
from yulon.catalog.catalog import CatalogEntry
from yulon.log import get_logger
from yulon.ui.message_box import FittedMessageBox
from yulon.ui.widgets.job import JobRunner, LineRelay

logger = get_logger(__name__)

UPDATE_CLIENT_LABEL = "Update client"
UPDATE_CLIENT_TIP = (
    "Check every file of your client folder against the client the Conquest of Azeroth "
    "project publishes, and download only what differs. Files you changed are kept unless "
    "you say replace, and every replaced file is kept in a backup."
)
CHECKING = "Checking for a client update…"
NO_CLIENT_FOLDER = "Choose your client folder first; then Update client can check it."


class Choice(Enum):
    KEEP = "keep"
    REPLACE = "replace"
    CANCEL = "cancel"


Asker = Callable[[QWidget, str, str, bool], Choice]
"""`(parent, title, text, has_edited) -> Choice`: the question before anything is changed."""


def ask_update(parent: QWidget, title: str, text: str, has_edited: bool) -> Choice:
    """Update / (Update and replace my changed files) / Cancel, defaulting to Cancel.

    Relabelled STANDARD buttons, as `controller_view.ask_backup_choice` does and for its
    reasons: the test fixture's `No` is not one of them, so an unpatched box cancels.
    """
    buttons = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
    if has_edited:
        buttons |= QMessageBox.StandardButton.Save
    box = FittedMessageBox(QMessageBox.Icon.Question, title, text, buttons, parent)
    labels = {
        QMessageBox.StandardButton.Yes: (
            "Update, keep my changed files" if has_edited else "Update"
        ),
        QMessageBox.StandardButton.Save: "Update, replace my changed files",
        QMessageBox.StandardButton.Cancel: "Cancel",
    }
    for which, label in labels.items():
        button = box.button(which)
        if button is not None:
            button.setText(label)
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    box.setEscapeButton(QMessageBox.StandardButton.Cancel)
    try:
        answer = box.exec()
    finally:
        box.deleteLater()
    if answer == QMessageBox.StandardButton.Yes:
        return Choice.KEEP
    if answer == QMessageBox.StandardButton.Save and has_edited:
        return Choice.REPLACE
    return Choice.CANCEL


@dataclass(frozen=True)
class _Planned:
    manifest: coa_client.Manifest
    items: list[coa_client.PlanItem]
    client_dir: Path


class ClientUpdateControl(QObject):
    """The press, its Cancel and its line. `button`/`cancel_button`/`label` go in the Play box."""

    def __init__(
        self,
        entry: CatalogEntry,
        server_dir: Path,
        client_dir: Callable[[], Path | None],
        jobs: JobRunner,
        parent: QWidget,
        *,
        ask: Asker = ask_update,
        opener: Any = None,
    ) -> None:
        super().__init__(parent)
        update = entry.client.update
        assert update is not None  # built only for an entry that publishes its client
        self._entry = entry
        self._update = update
        self._hosts = entry.client.hosts()
        self._server_dir = server_dir
        self._client_dir = client_dir
        self._jobs = jobs
        self._parent = parent
        self._ask = ask
        self._opener = opener
        self._cancel = threading.Event()
        self.running = False
        self._relay = LineRelay(self)
        self._relay.line.connect(self._progress)
        self.button = QPushButton(UPDATE_CLIENT_LABEL, parent)
        self.button.setToolTip(UPDATE_CLIENT_TIP)
        self.button.clicked.connect(self.update_pressed)
        self.cancel_button = QPushButton("Cancel", parent)
        self.cancel_button.setToolTip(
            "Stop the client update between reads. What arrived is kept, and the next "
            "Update client continues from there."
        )
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self._cancel_pressed)
        self.label = QLabel("", parent)
        self.label.setWordWrap(True)
        self.label.setVisible(False)
        self.refresh_visibility()

    def refresh_visibility(self) -> None:
        self.button.setVisible(self._client_dir() is not None)

    def _say(self, text: str) -> None:
        self.label.setText(text)
        self.label.setVisible(bool(text))

    # -- the check --------------------------------------------------------------------------

    def check(self) -> None:
        """Read the manifest and the folder's recorded version, off the GUI thread."""
        client = self._client_dir()
        self.refresh_visibility()
        if client is None or self.running:
            return
        self._say(CHECKING)

        def work() -> object:
            manifest = coa_client.fetch_manifest(
                self._update.manifest_url,
                hosts=self._hosts,
                opener=self._opener,
                seconds=coa_client.CHECK_SECONDS,
            )
            return coa_client.check_sentence(client, manifest)[1]

        self._jobs(work, self._checked, self._check_failed)

    @Slot(object)
    def _checked(self, said: object) -> None:
        if not self.running:
            self._say(str(said))

    @Slot(object)
    def _check_failed(self, exc: object) -> None:
        logger.info(f"client update check for {self._entry.id} failed: {exc}")
        if not self.running:
            self._say(coa_client.CHECK_FAILED)

    # -- the update -------------------------------------------------------------------------

    @Slot()
    def update_pressed(self) -> None:
        client = self._client_dir()
        if client is None:
            self._say(NO_CLIENT_FOLDER)
            return
        if self.running:
            return
        self._begin()
        say = self._relay.emit_line
        cancel = self._cancel

        def work() -> object:
            manifest = coa_client.fetch_manifest(
                self._update.manifest_url, hosts=self._hosts, opener=self._opener
            )
            state = coa_client.read_state(client)
            items = coa_client.plan(client, manifest, state, cancelled=cancel.is_set, progress=say)
            return _Planned(manifest, items, client)

        self._jobs(work, self._planned, self._failed)

    def _begin(self) -> None:
        self.running = True
        self._cancel.clear()
        self.button.setEnabled(False)
        self.cancel_button.setVisible(True)
        self._say("Checking your client files against the update…")

    def _end(self, text: str) -> None:
        self.running = False
        self.button.setEnabled(True)
        self.cancel_button.setVisible(False)
        self._say(text)

    @Slot(object)
    def _planned(self, planned: object) -> None:
        assert isinstance(planned, _Planned)
        items = planned.items
        writes = [i for i in items if i.action in ("add", "replace")]
        edited = [i for i in items if i.action == "keep-edited" and not i.remembered]
        if not writes and not edited:
            # Nothing to download: record the version so the check reads "up to date".
            self._apply(planned, replace_edited=False)
            return
        text = (
            f"{coa_client.summary(items)}\n\nThis changes your client folder {planned.client_dir} "
            f"itself. Every file it replaces is kept in {coa_client.BACKUP_DIR} there. Your "
            "settings, add-ons and graphics files are never replaced. Close the game first."
        )
        choice = self._ask(self._parent, "Update the client", text, bool(edited))
        if choice is Choice.CANCEL:
            self._end("The client update was not started; nothing was changed.")
            return
        self._apply(planned, replace_edited=choice is Choice.REPLACE)

    def _apply(self, planned: _Planned, *, replace_edited: bool) -> None:
        say = self._relay.emit_line
        cancel = self._cancel
        entry, server_dir = self._entry, self._server_dir

        def work() -> object:
            outcome = coa_client.apply(
                planned.client_dir,
                planned.manifest,
                planned.items,
                objects_url=self._update.objects_url,
                hosts=self._hosts,
                replace_edited=replace_edited,
                opener=self._opener,
                progress=say,
                cancelled=cancel.is_set,
            )
            said = outcome.sentence(planned.manifest.version)
            try:
                after = coa_client.map_data_sentence(entry, server_dir, planned.client_dir)
            except Exception as exc:  # noqa: BLE001 - a read; the update itself is done
                logger.warning(f"could not compare the map data with the client: {exc}")
                after = None
            return f"{said}\n\n{after}" if after else said

        self._jobs(work, self._applied, self._failed)

    @Slot(object)
    def _applied(self, said: object) -> None:
        self._end(str(said))

    @Slot(object)
    def _failed(self, exc: object) -> None:
        if isinstance(exc, coa_client.ClientUpdateError):
            self._end(str(exc))
            return
        logger.warning(f"client update of {self._entry.id} failed: {exc!r}")
        self._end(f"The client update stopped: {exc}. Nothing more was changed.")

    @Slot(str)
    def _progress(self, text: str) -> None:
        if self.running:
            self._say(text)

    @Slot()
    def _cancel_pressed(self) -> None:
        self._cancel.set()
        self._say("Stopping the client update…")
