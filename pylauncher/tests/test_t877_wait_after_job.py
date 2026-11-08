"""T877: no "Wait: ... is running." is left on the Server tab once the job has ended.

The reason a press is greyed with is kept on the press (`set_enabled_why()`); a press
that a job's end leaves greyed through a plain `setEnabled(False)` (nothing flips, so
no enabled event drops the reason) kept "Wait: Switch realm type is running." for good.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QAbstractButton, QLabel, QWidget

from tests.test_controller_view import (
    TORTOISE,
    _FakeRealmSwitch,
    _Ps,
    _services,
)
from yulon import runner
from yulon.catalog.catalog import load_catalog
from yulon.ui.controller_view import ControllerServices, ControllerView
from yulon.ui.widgets.job import run_inline

_WAIT = re.compile(r"^Wait:.*is running", re.S)


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


def _stale(view: ControllerView) -> list[str]:
    """Every widget or action of the view whose text or tooltip is still a running-job wait."""
    found: list[str] = []
    names = {id(value): name for name, value in vars(view).items()}
    for widget in view.findChildren(QWidget):
        texts = [widget.toolTip()]
        if isinstance(widget, QLabel | QAbstractButton):
            texts.append(widget.text())
        for text in texts:
            if _WAIT.match(text):
                found.append(f"{names.get(id(widget), type(widget).__name__)}: {text!r}")
    for action in view.findChildren(QAction):
        for text in (action.text(), action.toolTip(), str(action.property("yulon_grey_why") or "")):
            if _WAIT.match(text):
                found.append(f"{names.get(id(action), action.text())}: {text!r}")
    return found


def _coa_view(tmp_path: Path, **kw: Any) -> ControllerView:
    """`test_controller_view._coa_view` with the jobs running inline."""
    from tests.test_coa_realm import write_file

    write_file(tmp_path, "coa")
    entry = load_catalog().get("wow-coa")
    services = ControllerServices.for_wotlk(entry, tmp_path, None)
    return ControllerView(entry, services, status_poll_ms=0, **kw)


class _Moving(_FakeRealmSwitch):
    """A switch that moves the type like the engine: the picked type is then the active one."""

    def __init__(self, folder: Path) -> None:
        super().__init__()
        self.folder = folder

    def switch_to(self, target: str):  # type: ignore[no-untyped-def]
        from tests.test_coa_realm import write_file

        write_file(self.folder, target)
        yield from super().switch_to(target)


class _Failing(_FakeRealmSwitch):
    def switch_to(self, target: str):  # type: ignore[no-untyped-def]
        raise RuntimeError("the import refused")
        yield ""


@pytest.mark.parametrize("fails", [False, True])
def test_switch_realm_type_leaves_no_wait_after_it_ends(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fails: bool
) -> None:
    view = _coa_view(tmp_path, job_runner=run_inline)
    monkeypatch.setattr(view, "_confirm", lambda title, question: True)
    view.realm_type_choice.setCurrentIndex(1)
    view.services.realm_switch = _Failing() if fails else _Moving(tmp_path)  # type: ignore[assignment]
    monkeypatch.setattr(view, "refresh_status", lambda: None)
    monkeypatch.setattr(runner, "run", ps)

    view.switch_realm_type()

    assert view.services.realm_switch.targets == ["hero"] or fails  # type: ignore[attr-defined]
    assert not view._busy
    assert not _stale(view), "\n".join(_stale(view))


@pytest.mark.parametrize("fails", [False, True])
def test_start_leaves_no_wait_after_it_ends(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fails: bool
) -> None:
    view = ControllerView(
        TORTOISE, _services(ps, tmp_path, []), status_poll_ms=0, job_runner=run_inline
    )
    if fails:
        monkeypatch.setattr(
            view.services.controller, "start", lambda: (_ for _ in ()).throw(RuntimeError("no"))
        )
    view.start_button.click()
    assert not view._busy
    assert not _stale(view), "\n".join(_stale(view))


@pytest.mark.parametrize("ok", [True, False])
def test_the_bot_rebuild_leaves_no_wait_after_it_ends(
    qapp: object, ps: _Ps, tmp_path: Path, ok: bool
) -> None:
    view = ControllerView(
        TORTOISE, _services(ps, tmp_path, []), status_poll_ms=0, job_runner=run_inline
    )
    view._bot_rebuild_started()
    assert _stale(view) != [], "the sweep sees nothing while the job runs: the test proves nothing"
    view._bot_rebuild_finished(ok, "" if ok else "it failed")
    assert not _stale(view), "\n".join(_stale(view))
