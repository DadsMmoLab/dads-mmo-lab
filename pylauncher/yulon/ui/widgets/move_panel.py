"""The Maintenance tab's "Move to another computer" group (T601, level 1).

Two presses. "Pack for another computer…" writes this server's accounts and characters
into one file; "Bring in accounts and characters…" reads such a file into this server. The
engine is `move_flows`; this is the questions in front of it.

**What is asked, and why it is asked in this shape.**

* Packing asks once, in words that carry the three facts the owner wanted said (2026-10-09):
  the file holds logins, keep it private; the world is not in it; and, if the server is
  running, that it is stopped for the pack and started again. A yes covers all three, which
  is what `stop_allowed` hands the engine.
* Bringing in is a plan first. The plan is read without changing anything and shown in the
  report; a yes after it is an answer about THAT plan, and when it replaces people the
  engine wants the plan's own token back, so a yes cannot be spelled `True`.
* Both questions default to No and Escape declines, like every other here.

The panel owns no thread: it is handed the tab's `JobRunner`, and its done/failed slots are
this object's own, so the results land on the GUI thread. Everything it shows goes to the
tab's report box through `report`, so a move reads like a backup and a restore do.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from yulon.move import counts_phrase
from yulon.move_flows import ExportPlan, ExportResult, ImportPlan, ImportResult, MoveServices
from yulon.ui import folder_picker
from yulon.ui.answers import said_yes
from yulon.ui.message_box import FittedMessageBox
from yulon.ui.widgets.job import JobRunner

TITLE = "Move to another computer"
EXPLAIN = (
    "Pack this server's accounts and characters into one file, and bring such a file into "
    "a server of the same game on another computer. The world itself (custom items, NPCs) "
    "stays where it is. The file holds login data: keep it private."
)
PACK_BUTTON = "Pack for another computer…"
PACK_SERVER_BUTTON = "Pack the whole server…"
BRING_IN_BUTTON = "Bring in accounts and characters…"
PACK_FAILED = "Packing did not finish. Nothing was changed on this server."
BRING_IN_FAILED = "Bringing in did not finish. The Details say what stopped it."
PLAN_FAILED = "Yu'lon could not read that file or this server. Nothing was changed."
PACKAGE_FILTER = "Move packages (*.zip)"

Asker = Callable[[str, str, str | None], tuple[bool, bool]]
"""`(title, text, option label or None) -> (said yes, option ticked)`; No is the default."""
FolderPicker = Callable[[str, Path], Path | None]
PackagePicker = Callable[[str, Path], Path | None]


def pack_question(plan: ExportPlan) -> str:
    """What the pack dialog says, so the player's yes covers everything the press does."""
    lines = [
        "Pack this server's accounts and characters into a file?",
        "",
        "The file holds every account's login data, so keep it private and delete it once "
        "the move is done. Passwords are not in it in plain words, but anyone who has the "
        "file can try to work them out.",
        "The world (custom items, NPCs, objects you placed) is not in it.",
    ]
    if plan.server_running:
        lines += [
            "",
            "The server is running. It will be stopped while Yu'lon packs it, so the copy is "
            "one consistent picture, and started again afterwards.",
        ]
    return "\n".join(lines)


def whole_question(plan: ExportPlan) -> str:
    """What the whole-server pack dialog says (level 2)."""
    lines = [
        "Pack the whole server into a file, to build it again on another computer?",
        "",
        "It holds every database (the world too, so custom items, NPCs and objects you placed "
        "come along), the settings files, the module answers and which version of each module "
        "and of the server it was built from. The other computer builds the server again at that "
        "version from the Catalog (Bring from another computer…), then puts all of this in.",
        "The file holds every account's login data, so keep it private and delete it once the "
        "move is done. The database password and Yu'lon's command-channel login are not in it.",
    ]
    if plan.server_running:
        lines += [
            "",
            "The server is running. It will be stopped while Yu'lon packs it, so the copy is "
            "one consistent picture, and started again afterwards.",
        ]
    return "\n".join(lines)


def bring_in_question(plan: ImportPlan) -> str:
    """What the bring-in dialog says. Only called for a plan that is allowed."""
    manifest = plan.manifest
    assert manifest is not None  # an allowed plan has read its package
    counts = manifest.counts
    lines = [
        f"Bring in {counts_phrase(counts)} from {plan.path.name}?",
        f"Packed on {manifest.made_by.made[:16].replace('T', ' ')} ({manifest.game.name}).",
        "",
        "Their logins and passwords come with them, and so do GM levels and the bots. "
        "This server's own accounts, characters and bots are replaced; its world, realm "
        "address and settings are left as they are.",
    ]
    if plan.replaces is not None:
        lines += ["", plan.replaces.sentence]
    if plan.server_running:
        lines += [
            "",
            "The server is running. It will be stopped, and it stays stopped afterwards: "
            "press Start when you are ready.",
        ]
    return "\n".join(lines)


def plan_report(plan: ImportPlan) -> str:
    """The plan as the report box shows it, refusals first and all of them at once."""
    if plan.refusals:
        return "This cannot go ahead:\n" + "\n".join(f"  - {r}" for r in plan.refusals)
    manifest = plan.manifest
    assert manifest is not None
    return (
        f"{plan.path.name} fits this server.\n"
        f"It would replace: {', '.join(plan.schemas)}."
        + (f"\n{plan.replaces.sentence}" if plan.replaces is not None else "")
    )


def _qt_ask(parent: QWidget) -> Asker:
    def ask(title: str, text: str, option: str | None) -> tuple[bool, bool]:
        box = FittedMessageBox(
            QMessageBox.Icon.Question,
            title,
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            parent,
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.setEscapeButton(QMessageBox.StandardButton.No)
        check: QCheckBox | None = None
        if option is not None:
            check = QCheckBox(option, box)
            box.setCheckBox(check)
        try:
            yes = said_yes(box.exec())
            return yes, bool(check is not None and check.isChecked())
        finally:
            box.deleteLater()

    return ask


class MovePanel(QGroupBox):
    """The group. `running` is true from a press until its result is shown."""

    def __init__(
        self,
        services: MoveServices,
        *,
        jobs: JobRunner,
        report: Callable[[str], None],
        failed: Callable[[object, str], None],
        changed: Callable[[], None],
        ask: Asker | None = None,
        pick_folder: FolderPicker | None = None,
        pick_package: PackagePicker | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(TITLE, parent)
        self._services = services
        self._jobs = jobs
        self._report = report
        self._failed = failed
        self._changed = changed
        self._ask: Asker = ask or _qt_ask(self)
        self._pick_folder: FolderPicker = pick_folder or (
            lambda title, start: folder_picker.pick_folder(self, title, start)
        )
        self._pick_package: PackagePicker = pick_package or (
            lambda title, start: folder_picker.pick_open_file(self, title, start, PACKAGE_FILTER)
        )
        self.running = False
        self._plan: ImportPlan | None = None
        self._whole = False

        explain = QLabel(EXPLAIN, self)
        explain.setWordWrap(True)
        self.pack_button = QPushButton(PACK_BUTTON, self)
        self.bring_in_button = QPushButton(BRING_IN_BUTTON, self)
        self.pack_server_button: QPushButton | None = None
        if services.export_server is not None:
            self.pack_server_button = QPushButton(PACK_SERVER_BUTTON, self)
            self.pack_server_button.clicked.connect(self.pack_server)
        self.pack_button.clicked.connect(self.pack)
        self.bring_in_button.clicked.connect(self.bring_in)
        presses = QHBoxLayout()
        presses.addWidget(self.pack_button)
        if self.pack_server_button is not None:
            presses.addWidget(self.pack_server_button)
        presses.addWidget(self.bring_in_button)
        presses.addStretch(1)
        box = QVBoxLayout(self)
        box.addWidget(explain)
        box.addLayout(presses)

    # ------------------------------------------------------------ state

    def _begin(self, said: str) -> None:
        self.running = True
        self.pack_button.setEnabled(False)
        self.bring_in_button.setEnabled(False)
        if self.pack_server_button is not None:
            self.pack_server_button.setEnabled(False)
        self._report(said)

    def _end(self) -> None:
        self.running = False
        self.pack_button.setEnabled(True)
        self.bring_in_button.setEnabled(True)
        if self.pack_server_button is not None:
            self.pack_server_button.setEnabled(True)
        self._changed()

    # ------------------------------------------------------------ pack

    @Slot()
    def pack(self) -> None:
        if self.running:
            return
        self._whole = False
        self._begin("Looking at the server…")
        self._jobs(self._services.plan_export, self._pack_planned, self._pack_plan_failed)

    @Slot()
    def pack_server(self) -> None:
        if self.running or self._services.export_server is None:
            return
        self._whole = True
        self._begin("Looking at the server…")
        self._jobs(self._services.plan_export, self._pack_planned, self._pack_plan_failed)

    @Slot(object)
    def _pack_planned(self, result: object) -> None:
        if not isinstance(result, ExportPlan):
            self._end()
            return
        if not result.allowed:
            self._end()
            self._report("This cannot go ahead:\n" + "\n".join(f"  - {r}" for r in result.refusals))
            return
        whole = self._whole
        if whole:
            yes, _ = self._ask("Pack the whole server?", whole_question(result), None)
        else:
            yes, _ = self._ask("Pack accounts and characters?", pack_question(result), None)
        if not yes:
            self._end()
            self._report("Nothing was packed.")
            return
        folder = self._pick_folder("Where should the file go?", self._services.default_folder())
        if folder is None:
            self._end()
            self._report("Nothing was packed: no folder was chosen.")
            return
        stop_allowed = result.server_running
        export = self._services.export_server if whole else self._services.export
        assert export is not None
        self._report("Packing… this can take a few minutes with many bots.")
        self._jobs(
            lambda: export(folder, stop_allowed),
            self._pack_done,
            self._pack_failed,
        )

    @Slot(object)
    def _pack_plan_failed(self, exc: object) -> None:
        self._end()
        self._failed(exc, PACK_FAILED)

    @Slot(object)
    def _pack_done(self, result: object) -> None:
        self._end()
        if isinstance(result, ExportResult):
            self._report(result.text())

    @Slot(object)
    def _pack_failed(self, exc: object) -> None:
        self._end()
        self._failed(exc, PACK_FAILED)

    # ------------------------------------------------------------ bring in

    @Slot()
    def bring_in(self) -> None:
        if self.running:
            return
        path = self._pick_package("Choose a move package", self._services.default_folder())
        if path is None:
            return
        self._begin(f"Reading {path.name}…")
        self._jobs(
            lambda: self._services.plan_import(path), self._import_planned, self._import_plan_failed
        )

    @Slot(object)
    def _import_planned(self, result: object) -> None:
        if not isinstance(result, ImportPlan):
            self._end()
            return
        self._plan = result
        self._report(plan_report(result))
        if not result.allowed:
            self._end()
            return
        yes, old_name = self._ask(
            "Bring in accounts and characters?",
            bring_in_question(result),
            _realm_option(result),
        )
        if not yes:
            self._end()
            self._report("Nothing was brought in.")
            return
        confirm = result.token if result.replaces is not None else None
        stop_allowed = result.server_running
        self._report("Bringing in… this can take a few minutes with many bots.")
        self._jobs(
            lambda: self._services.run_import(result, confirm, old_name, stop_allowed),
            self._import_done,
            self._import_failed,
        )

    @Slot(object)
    def _import_plan_failed(self, exc: object) -> None:
        self._end()
        self._failed(exc, PLAN_FAILED)

    @Slot(object)
    def _import_done(self, result: object) -> None:
        self._end()
        if isinstance(result, ImportResult):
            self._report(result.text())

    @Slot(object)
    def _import_failed(self, exc: object) -> None:
        self._end()
        self._failed(exc, BRING_IN_FAILED)


def _realm_option(plan: ImportPlan) -> str | None:
    """The tick box that brings the old realm name, or None when the package names none."""
    if plan.manifest is None or not plan.manifest.realm_name:
        return None
    return f"Use the old realm name ({plan.manifest.realm_name})"


__all__ = ["MovePanel", "bring_in_question", "pack_question", "plan_report", "whole_question"]
