"""T864: Free Docker space on the Maintenance tab (view only; no daemon, no removal).

The seam is fake; the removal itself is `docker_space`'s, tested in
`test_docker_space.py`. Here: the button text and hiding, the busy refusal,
read-then-agree-then-report, cancel meaning nothing, and the dialog's own
boxes and Ok gating.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QDialog

from tests.test_backup_cleanup_view import make_view as _backup_make_view
from tests.test_controller_view import (  # noqa: F401
    WOTLK,
    _FakeMaintenance,
    _Ps,
    _services,
    ps,
)
from yulon import docker_space, server_build_presses
from yulon.server_build_presses import MAINTENANCE_TAB  # noqa: F401
from yulon.ui import controller_view as view_module
from yulon.ui.controller_view import ControllerView
from yulon.ui.widgets import free_docker_space_dialog as dialog_module
from yulon.ui.widgets.free_docker_space_dialog import FreeDockerSpaceDialog
from yulon.ui.widgets.job import run_inline


class FakeSpace:
    """A `docker_space.Seam` double: `read()` answers, `free()` records."""

    def __init__(self, space: docker_space.Space) -> None:
        self.space = space
        self.read_calls = 0
        self.free_calls: list[docker_space.Choice] = []

    def read(self) -> docker_space.Space:
        self.read_calls += 1
        return self.space

    def free(self, choice: docker_space.Choice) -> docker_space.Freed:
        self.free_calls.append(choice)
        return docker_space.Freed(("first line", "second line"))


@pytest.fixture
def made(tmp_path: Path) -> _FakeMaintenance:
    return _FakeMaintenance()


def make_view(
    ps: _Ps,  # noqa: F811
    tmp_path: Path,
    made: _FakeMaintenance,
    monkeypatch: pytest.MonkeyPatch,
    *,
    seam: Any,
) -> ControllerView:
    (tmp_path / "sql_scripts" / "backups").mkdir(parents=True, exist_ok=True)
    services = dataclasses.replace(_services(ps, tmp_path, [], made), docker_space=seam)
    view = ControllerView(WOTLK, services, status_poll_ms=0, job_runner=run_inline)

    def run_in_line(fn: Any, done: Any, failed: Any) -> None:
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001 - what the runner hands `failed`
            failed(exc)
        else:
            done(result)

    monkeypatch.setattr(view, "_run", run_in_line)
    return view


def _space(**overrides: Any) -> docker_space.Space:
    base: dict[str, Any] = {
        "cache_bytes": None,
        "old_builds": (),
        "kept_build": False,
        "kept_build_bytes": None,
    }
    base.update(overrides)
    return docker_space.Space(**base)  # type: ignore[arg-type]


# -- 20. button text and hiding --------------------------------------------------


def test_the_button_reads_the_press_label_and_hides_without_a_seam(
    qapp: object,
    ps: _Ps,  # noqa: F811
    tmp_path: Path,
    made: _FakeMaintenance,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = make_view(ps, tmp_path, made, monkeypatch, seam=FakeSpace(_space()))
    assert view.free_docker_space_button.text() == server_build_presses.FREE_DOCKER_SPACE
    assert view.free_docker_space_button.text() == view_module.FREE_DOCKER_SPACE_LABEL
    hidden = _backup_make_view(ps, tmp_path, made, monkeypatch, seam=None)
    hidden.services = dataclasses.replace(hidden.services, docker_space=None)
    hidden.free_docker_space_button.setVisible(hidden.services.docker_space is not None)
    assert not hidden.free_docker_space_button.isVisible()


# -- 21. busy ------------------------------------------------------------------


def test_while_a_rebuild_is_busy_the_press_names_the_wait_and_reads_nothing(
    qapp: object,
    ps: _Ps,  # noqa: F811
    tmp_path: Path,
    made: _FakeMaintenance,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seam = FakeSpace(_space())
    view = make_view(ps, tmp_path, made, monkeypatch, seam=seam)
    view._busy = True
    view._busy_job = "Rebuild"
    view.free_docker_space()
    assert view_module.wait_for("Rebuild") in view.maintenance_report.toPlainText()
    assert seam.read_calls == 0


def test_a_distro_wait_greys_the_button_and_pressing_reads_nothing(
    qapp: object,
    ps: _Ps,  # noqa: F811
    tmp_path: Path,
    made: _FakeMaintenance,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R1: while the backups wait for a stopped distro, the press is greyed and reads nothing."""
    seam = FakeSpace(_space())
    view = make_view(ps, tmp_path, made, monkeypatch, seam=seam)
    view._waiting_on_distro["backups"] = view.refresh_backups
    view._settle_docker_space_button()
    assert not view.free_docker_space_button.isEnabled()
    assert view.free_docker_space_button.toolTip() == view_module.FREE_DOCKER_SPACE_WAITS_FOR_DISTRO
    view.free_docker_space()
    assert view_module.FREE_DOCKER_SPACE_WAITS_FOR_DISTRO in view.maintenance_report.toPlainText()
    assert seam.read_calls == 0


# -- 22-23. read, agree, report; cancel ----------------------------------------


def accept_with(monkeypatch: pytest.MonkeyPatch, setup: Any) -> list[FreeDockerSpaceDialog]:
    seen: list[FreeDockerSpaceDialog] = []

    def exec_(self: FreeDockerSpaceDialog) -> int:
        seen.append(self)
        setup(self)
        return int(QDialog.DialogCode.Accepted)

    monkeypatch.setattr(FreeDockerSpaceDialog, "exec", exec_)
    return seen


def test_read_then_agreed_runs_free_with_the_dialog_choice_and_reports_lines(
    qapp: object,
    ps: _Ps,  # noqa: F811
    tmp_path: Path,
    made: _FakeMaintenance,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seam = FakeSpace(
        _space(
            cache_bytes=1024,
            old_builds=(docker_space.OldBuild(image_id="a", size=1024, project="yulon-x"),),
            kept_build=False,
        )
    )
    view = make_view(ps, tmp_path, made, monkeypatch, seam=seam)
    accept_with(monkeypatch, lambda dialog: None)
    view.free_docker_space()
    assert seam.read_calls == 1
    assert seam.free_calls == [docker_space.Choice(True, True, False)]
    assert view.maintenance_report.toPlainText() == "first line\nsecond line"


def test_cancelling_the_dialog_frees_nothing(
    qapp: object,
    ps: _Ps,  # noqa: F811
    tmp_path: Path,
    made: _FakeMaintenance,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seam = FakeSpace(_space(cache_bytes=1024, old_builds=(), kept_build=False))
    view = make_view(ps, tmp_path, made, monkeypatch, seam=seam)
    monkeypatch.setattr(
        FreeDockerSpaceDialog, "exec", lambda self: int(QDialog.DialogCode.Rejected)
    )
    view.free_docker_space()
    assert seam.free_calls == []


# -- 24. the dialog itself ------------------------------------------------------


def test_the_dialog_boxes_and_ok_gating(qapp: object) -> None:
    full = _space(
        cache_bytes=3 * 1024**3,
        old_builds=(docker_space.OldBuild(image_id="a", size=1024**3, project="yulon-wow-coa-x"),),
        kept_build=True,
        kept_build_bytes=2 * 1024**3,
    )
    dialog = FreeDockerSpaceDialog(full, backups_pointer="pointer", windows=False)
    try:
        assert dialog.cache_box.isChecked()
        assert dialog.old_builds_box.isChecked() and dialog.old_builds_box.isEnabled()
        assert dialog.kept_box is not None and not dialog.kept_box.isChecked()
        assert dialog.go.isEnabled()
        assert dialog.go.text() == "Remove ticked"
        dialog.cache_box.setChecked(False)
        dialog.old_builds_box.setChecked(False)
        assert not dialog.go.isEnabled()
        assert dialog.choice() == docker_space.Choice(
            cache=False, old_builds=False, kept_build=False
        )
    finally:
        dialog.deleteLater()

    no_kept = FreeDockerSpaceDialog(_space(), backups_pointer="pointer", windows=False)
    try:
        assert no_kept.kept_box is None
        assert not no_kept.old_builds_box.isEnabled()
        assert "none" in no_kept.old_builds_box.text()
    finally:
        no_kept.deleteLater()


def test_windows_note_only_with_windows(qapp: object) -> None:
    on = FreeDockerSpaceDialog(_space(), backups_pointer="p", windows=True)
    off = FreeDockerSpaceDialog(_space(), backups_pointer="p", windows=False)
    try:
        assert dialog_module.FreeDockerSpaceDialog is not None
        assert view_module.DOCKER_SPACE_BACKUPS_POINTER not in ""

        def texts(dialog: object) -> list[str]:
            layout = dialog.layout()  # type: ignore[union-attr]
            found: list[str] = []
            assert layout is not None
            for i in range(layout.count()):
                widget = layout.itemAt(i).widget()
                get_text = getattr(widget, "text", None)
                try:
                    text = get_text() if callable(get_text) else ""
                except RuntimeError:
                    text = ""
                found.append(text if isinstance(text, str) else "")
            return found

        assert any("Docker Desktop" in text for text in texts(on))
        assert not any("Docker Desktop" in text for text in texts(off))
    finally:
        on.deleteLater()
        off.deleteLater()
