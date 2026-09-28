"""The Tuning tab's "Server time zone" group, driven through its own widgets (T171)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt

from tests.test_controller_view import (
    RESET_QUIET,
    _menu_action,
    _Ps,
    _reset_answer,
    _reset_yes,
    _services,
    _wotlk_override,
    _wotlk_server,
)
from yulon import reset_defaults, resources, runner, server_time_zone
from yulon.catalog import composegen, time_zone
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import (
    TIME_ZONE_HOST,
    TIME_ZONE_KEPT,
    TUNING_RECREATE_LABEL,
    TUNING_RESET_ALL,
    ControllerView,
)
from yulon.ui.widgets.job import run_inline

CATALOG = load_catalog()
WOTLK = CATALOG.get("wow-wotlk")
TBC = CATALOG.get("wow-tbc")
OVERRIDE = composegen.OVERRIDE_FILE
OSLO = "Europe/Oslo"


@pytest.fixture(autouse=True)
def _inline_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """`test_controller_view`'s rule: a click's job runs inline, so its result is there after."""
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


def _view(
    ps: _Ps, tmp_path: Path, entry: CatalogEntry = WOTLK, *, wired: bool = True, **kw: Any
) -> ControllerView:
    services = _services(ps, tmp_path, [])
    route = server_time_zone.time_zone_route(entry, tmp_path) if wired else None
    object.__setattr__(services, "time_zone", route)
    return ControllerView(entry, services, status_poll_ms=0, **kw)


def _tbc_installed(server_dir: Path) -> str:
    plan = composegen.render(
        TBC,
        server_dir,
        templates_root=resources.installers_dir(),
        db_password="pw",
        platform_id=lambda: "linux",
    )
    (server_dir / composegen.BASE_FILE).write_text(plan.base, encoding="utf-8")
    (server_dir / OVERRIDE).write_text(plan.override, encoding="utf-8")
    return plan.override


def _pick(view: ControllerView, zone: str) -> None:
    """Choose `zone` the way a player does: its region in the first list, then its place."""
    where = view.time_zone_where
    where.setCurrentIndex(where.findData(f"region:{zone.split('/', 1)[0]}"))
    view.time_zone_place.setCurrentIndex(view.time_zone_place.findData(zone))


def _where(view: ControllerView) -> str:
    return view.time_zone_where.currentText()


def test_the_zone_is_read_off_the_gui_thread_and_the_lists_wait_for_it(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wotlk_override(tmp_path)
    reads: list[int] = []
    real = server_time_zone.read

    def spy(*args: Any, **kwargs: Any) -> server_time_zone.Reading:
        reads.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(server_time_zone, "read", spy)
    held: list[tuple[Any, Any, Any]] = []
    view = _view(
        ps, tmp_path, job_runner=lambda work, done, failed: held.append((work, done, failed))
    )

    assert reads == [], "the zone was read on the GUI thread"
    assert not view.time_zone_where.isEnabled() and not view.time_zone_apply_button.isEnabled()
    jobs = [
        job for job in held if getattr(job[1], "__func__", None) is ControllerView._time_zone_read
    ]
    assert len(jobs) == 1
    work, done, _failed = jobs[0]
    done(work())

    assert reads
    assert _where(view) == time_zone.UTC
    assert view.time_zone_where.isEnabled()
    assert not view.time_zone_apply_button.isEnabled(), "UTC is what the file already says"
    assert view.time_zone_note.text() == "Now UTC"
    assert "names no time zone" in view.time_zone_note.toolTip()


def test_apply_writes_both_servers_backs_up_and_offers_the_recreate(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = _wotlk_override(tmp_path)
    view = _view(ps, tmp_path)
    asked: list[str] = []
    _reset_yes(monkeypatch, asked)

    _pick(view, OSLO)
    assert view.time_zone_place.isEnabled() and view.time_zone_apply_button.isEnabled()
    view.time_zone_apply_button.click()

    now = (tmp_path / OVERRIDE).read_text(encoding="utf-8")
    assert now == installed + (
        '      TZ: "Europe/Oslo"\n  ac-authserver:\n    environment:\n      TZ: "Europe/Oslo"\n'
    )
    backups = list(tmp_path.glob(f"{OVERRIDE}.*.bak"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == installed
    assert len(asked) == 1 and OSLO in asked[0] and "RECREATED" in asked[0]
    assert view.tuning_banner.isHidden() is False
    assert view.tuning_banner_button.text() == TUNING_RECREATE_LABEL
    assert OVERRIDE in view.tuning_banner_label.text()
    assert backups[0].name in view.tuning_report.toPlainText()
    assert view.time_zone_note.text() == f"Now {OSLO}"
    assert (_where(view), view.time_zone_place.currentData()) == ("Europe", OSLO), "read again"
    assert not view.time_zone_apply_button.isEnabled()


def test_same_as_this_computer_on_a_cmangos_server_writes_the_rule(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(time_zone, "host_zone", lambda: OSLO)
    _tbc_installed(tmp_path)
    view = _view(ps, tmp_path, TBC)
    _reset_yes(monkeypatch)

    assert view.time_zone_where.itemText(0) == TIME_ZONE_HOST.format(zone=OSLO)
    view.time_zone_where.setCurrentIndex(0)
    assert not view.time_zone_place.isEnabled()
    view.time_zone_apply_button.click()

    assert (tmp_path / OVERRIDE).read_text(encoding="utf-8").count(f'TZ: "{OSLO}"') == 2
    assert time_zone.ready(TBC, tmp_path, OSLO)
    assert view.time_zone_where.currentIndex() == 0, "the file's zone IS this computer's"


def test_no_on_the_question_writes_nothing(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QMessageBox

    installed = _wotlk_override(tmp_path)
    view = _view(ps, tmp_path)
    asked: list[str] = []
    _reset_answer(monkeypatch, QMessageBox.StandardButton.No, asked)
    _pick(view, OSLO)
    view.time_zone_apply_button.click()

    assert len(asked) == 1
    assert (tmp_path / OVERRIDE).read_text(encoding="utf-8") == installed
    assert not list(tmp_path.glob("*.bak")) and view.tuning_banner.isHidden()


def test_a_hand_written_value_is_shown_as_it_is_and_nothing_is_offered_for_it(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    text = _wotlk_override(tmp_path).replace(
        "    environment:\n", "    environment:\n      TZ: Mars/Olympus\n"
    )
    (tmp_path / OVERRIDE).write_text(text, encoding="utf-8")

    view = _view(ps, tmp_path)

    assert _where(view) == TIME_ZONE_KEPT.format(value="Mars/Olympus")
    assert not view.time_zone_apply_button.isEnabled(), "kept until the player picks a zone"
    assert (tmp_path / OVERRIDE).read_text(encoding="utf-8") == text


def test_a_file_the_tab_cannot_change_leaves_the_lists_dead_and_says_why(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    (tmp_path / composegen.BASE_FILE).write_text(
        composegen.GENERATED_MARKER + "\nservices: {}\n", encoding="utf-8"
    )
    view = _view(ps, tmp_path)
    assert "not on disk" in view.time_zone_note.toolTip()
    assert not view.time_zone_where.isEnabled() and not view.time_zone_apply_button.isEnabled()


def test_the_lists_are_dead_while_a_job_of_ours_runs(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    _wotlk_override(tmp_path)
    view = _view(ps, tmp_path)
    _pick(view, OSLO)
    assert view.time_zone_apply_button.isEnabled()

    view._set_busy(True)
    assert not view.time_zone_where.isEnabled() and not view.time_zone_apply_button.isEnabled()
    view._set_busy(False)
    assert view.time_zone_where.isEnabled() and view.time_zone_apply_button.isEnabled()


def test_the_group_is_absent_without_a_route(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    view = _view(ps, tmp_path, wired=False)
    assert view.time_zone_group.isHidden()


def test_the_lists_are_closed_lists_a_pad_and_a_keyboard_can_reach(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Never a text box (owner): the gamepad's confirm opens a closed list's popup."""
    _wotlk_override(tmp_path)
    view = _view(ps, tmp_path)
    for box in (view.time_zone_where, view.time_zone_place):
        assert box.isEditable() is False
        assert box.focusPolicy() & Qt.FocusPolicy.TabFocus
    regions = [view.time_zone_where.itemText(i) for i in range(view.time_zone_where.count())]
    assert regions[:2] == [TIME_ZONE_HOST.format(zone=time_zone.UTC), time_zone.UTC]
    assert "Europe" in regions and "America" in regions and len(regions) < 20


def test_reset_to_default_keeps_the_zone_the_lists_show(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Owner decision 2026-09-28: Reset to default does NOT reset the time zone."""
    _wotlk_server(tmp_path)
    server_time_zone.write(WOTLK, tmp_path, OSLO)
    services = _services(ps, tmp_path, [])
    object.__setattr__(services, "time_zone", server_time_zone.time_zone_route(WOTLK, tmp_path))
    object.__setattr__(
        services, "reset_settings", reset_defaults.route_for_app(WOTLK, tmp_path, seams=RESET_QUIET)
    )
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    asked: list[str] = []
    _reset_yes(monkeypatch, asked)

    _menu_action(view, TUNING_RESET_ALL).trigger()

    assert "time zone is kept" in asked[0]
    assert server_time_zone.read(WOTLK, tmp_path).zone == OSLO
    view.reload_tuning()
    assert (_where(view), view.time_zone_place.currentData()) == ("Europe", OSLO)
