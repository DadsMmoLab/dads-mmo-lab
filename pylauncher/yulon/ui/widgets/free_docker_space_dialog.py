"""Free Docker space: tick what goes, see what each part is, then agree (T864).

The dialog never removes anything. It shows what the Maintenance tab just
read -- Docker's build cache, Yu'lon's old untagged builds, this server's
kept build -- with a word under each row saying what it is, and hands back
a `docker_space.Choice`; the tab's worker re-reads everything under the
maintenance lease and refuses when a server build runs. Unticking every box
greys the Ok button, so an empty agreement cannot go ahead.

Not themed here, and no gamepad code: `apply_dadcraft_theme(window)` styles
`QDialog` through the window's stylesheet, and an application-modal dialog is
the navigation context while it is up (`update_dialog.py` explains both).
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from yulon import docker_space

TITLE = "Docker's disk space"


class FreeDockerSpaceDialog(QDialog):
    """Tick the parts to remove; `choice()` is what is ticked now."""

    def __init__(
        self,
        space: docker_space.Space,
        *,
        backups_pointer: str,
        windows: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(TITLE)

        box = QVBoxLayout(self)

        self.cache_box = QCheckBox(docker_space.cache_row(space), self)
        self.cache_box.setChecked(True)
        box.addWidget(self.cache_box)
        cache_note = QLabel(docker_space.CACHE_NOTE, self)
        cache_note.setWordWrap(True)
        box.addWidget(cache_note)

        has_old = space.old_builds is not None and len(space.old_builds) > 0
        self.old_builds_box = QCheckBox(docker_space.old_builds_row(space), self)
        self.old_builds_box.setChecked(has_old)
        self.old_builds_box.setEnabled(has_old)
        box.addWidget(self.old_builds_box)
        old_builds_note = QLabel(docker_space.OLD_BUILDS_NOTE, self)
        old_builds_note.setWordWrap(True)
        box.addWidget(old_builds_note)

        self.kept_box: QCheckBox | None = None
        if space.kept_build:
            self.kept_box = QCheckBox(docker_space.kept_row(space), self)
            self.kept_box.setChecked(False)
            box.addWidget(self.kept_box)
            kept_note = QLabel(docker_space.KEPT_NOTE, self)
            kept_note.setWordWrap(True)
            box.addWidget(kept_note)

        never = QLabel(docker_space.NEVER_NOTE, self)
        never.setWordWrap(True)
        box.addWidget(never)
        pointer = QLabel(backups_pointer, self)
        pointer.setWordWrap(True)
        box.addWidget(pointer)
        if windows:
            windows_note = QLabel(docker_space.WINDOWS_NOTE, self)
            windows_note.setWordWrap(True)
            box.addWidget(windows_note)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        self.go = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.go.setText("Remove ticked")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        box.addWidget(self.buttons)

        self.cache_box.toggled.connect(self._refresh)
        self.old_builds_box.toggled.connect(self._refresh)
        if self.kept_box is not None:
            self.kept_box.toggled.connect(self._refresh)
        self._refresh()

    def choice(self) -> docker_space.Choice:
        """What is ticked now (kept False when there is no kept box)."""
        return docker_space.Choice(
            cache=self.cache_box.isChecked(),
            old_builds=self.old_builds_box.isChecked(),
            kept_build=self.kept_box.isChecked() if self.kept_box is not None else False,
        )

    def _refresh(self, *_: object) -> None:
        picked = self.choice()
        self.go.setEnabled(picked.cache or picked.old_builds or picked.kept_build)
