"""Clean up old backups: choose a rule, see what it would remove, then agree (T604).

The dialog never deletes. It builds a `backup_shelf.Plan` from the folder as the
Maintenance tab just read it and hands the plan back; the tab's worker re-reads the
folder under the maintenance lease and refuses everything if anything changed. What
the dialog shows is therefore what a press WOULD do now, with the total freed on the
button the player presses ("Delete 4 files") rather than in a paragraph above it.

Not themed here, and no gamepad code: `apply_dadcraft_theme(window)` styles `QDialog`
through the window's stylesheet, and an application-modal dialog is the navigation
context while it is up (`update_dialog.py` explains both).
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from yulon import backup_shelf
from yulon.backup_shelf import Plan, Rule, Shelf

TITLE = "Clean up old backups"

ALWAYS_KEPT = (
    "Yu'lon always keeps the newest good copy of every database, the copy the last update "
    "took, anything an unfinished restore needs, and the copy taken before an installed mod. "
    "Read-only files, files with another name and another game's backups are left for you "
    "to delete one by one."
)

NOTHING = "Nothing to delete with these settings."

DEFAULT_KEEP = 3
DEFAULT_DAYS = 30


def delete_label(count: int) -> str:
    return f"Delete {count} file{'' if count == 1 else 's'}"


class CleanUpDialog(QDialog):
    """Pick a rule; `plan` is what it selects now. `exec()` returns Accepted to go ahead."""

    def __init__(
        self,
        shelf: Shelf,
        *,
        keep_now: int | None,
        parent: QWidget | None = None,
        now: datetime | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        self._shelf = shelf
        self._now = now
        self._keep_now = keep_now
        self.plan: Plan | None = None
        self._unusable = len(
            backup_shelf.plan_clean_up(shelf, Rule(include_unusable=True), now=now).names
        )

        box = QVBoxLayout(self)
        intro = QLabel(ALWAYS_KEPT, self)
        intro.setWordWrap(True)
        box.addWidget(intro)

        self.keep_radio = QRadioButton("Keep the newest", self)
        self.keep_spin = QSpinBox(self)
        self.keep_spin.setRange(1, 99)
        self.keep_spin.setValue(keep_now or DEFAULT_KEEP)
        keep_row = QHBoxLayout()
        keep_row.addWidget(self.keep_radio)
        keep_row.addWidget(self.keep_spin)
        keep_row.addWidget(QLabel("copies of each database", self))
        keep_row.addStretch(1)
        box.addLayout(keep_row)

        self.age_radio = QRadioButton("Delete copies older than", self)
        self.age_spin = QSpinBox(self)
        self.age_spin.setRange(0, 3650)
        self.age_spin.setValue(DEFAULT_DAYS)
        age_row = QHBoxLayout()
        age_row.addWidget(self.age_radio)
        age_row.addWidget(self.age_spin)
        age_row.addWidget(QLabel("days", self))
        age_row.addStretch(1)
        box.addLayout(age_row)
        self.keep_radio.setChecked(True)

        self.unusable_check = QCheckBox(
            f"Also delete cut-short copies and files Yu'lon cannot restore ({self._unusable})",
            self,
        )
        self.unusable_check.setEnabled(self._unusable > 0)
        box.addWidget(self.unusable_check)

        self.auto_check = QCheckBox(
            "After every Back up now, keep only that many copies of each database", self
        )
        self.auto_check.setChecked(keep_now is not None)
        self.auto_check.setToolTip("Off unless you turn it on. Applies to Back up now only.")
        box.addWidget(self.auto_check)

        self.summary = QLabel("", self)
        self.summary.setWordWrap(True)
        box.addWidget(self.summary)

        self.files = QListWidget(self)
        self.files.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.files.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.files.setMinimumHeight(110)
        box.addWidget(self.files, 1)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        self.go = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        box.addWidget(self.buttons)

        for signal in (
            self.keep_radio.toggled,
            self.age_radio.toggled,
            self.keep_spin.valueChanged,
            self.age_spin.valueChanged,
            self.unusable_check.toggled,
            self.auto_check.toggled,
        ):
            signal.connect(self._refresh)
        self._refresh()

    # ------------------------------------------------------------------ reading

    def rule(self) -> Rule:
        return Rule(
            keep_newest=self.keep_spin.value() if self.keep_radio.isChecked() else None,
            older_than_days=self.age_spin.value() if self.age_radio.isChecked() else None,
            include_unusable=self.unusable_check.isChecked(),
        )

    def keep_wanted(self) -> tuple[bool, int | None]:
        """`(changed, value)` for the automatic keep: the number to store, None for off.

        Only the "keep the newest" rule can be automatic, so with the other chosen the
        stored setting is left exactly as it was.
        """
        if not self.keep_radio.isChecked():
            return False, self._keep_now
        value = self.keep_spin.value() if self.auto_check.isChecked() else None
        return value != self._keep_now, value

    # ------------------------------------------------------------------ drawing

    def _refresh(self, *_: object) -> None:
        self.auto_check.setEnabled(self.keep_radio.isChecked())
        self.keep_spin.setEnabled(self.keep_radio.isChecked())
        self.age_spin.setEnabled(self.age_radio.isChecked())
        self.plan = backup_shelf.plan_clean_up(self._shelf, self.rule(), now=self._now)
        by_name = {r.name: r for r in self._shelf.rows}
        self.files.clear()
        for name in self.plan.names:
            r = by_name[name]
            self.files.addItem(QListWidgetItem(f"{name}  ({backup_shelf.size_text(r.size)})"))
        count = len(self.plan.names)
        if count:
            self.summary.setText(
                f"{count} file{'' if count == 1 else 's'} would be deleted, freeing "
                f"{backup_shelf.size_text(self.plan.freed)}."
            )
        else:
            self.summary.setText(NOTHING)
        changed, _value = self.keep_wanted()
        self.go.setText(delete_label(count) if count else "Save")
        self.go.setEnabled(bool(count) or changed)
