"""T607 item 1: no retry is offered while another Yu'lon holds the server (T568 plan section 6).

A stuck `started` world-update row may belong to a press another Yu'lon is still running. The
corrections banner read the databases only; `docker.reservation_holder()` had no production
caller, so a retry was offered over a live press. The reading now asks the daemon, inspect
only, and says "busy" -- the banner stays up as a sentence with no button -- while a
reservation is held by anyone but this process.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support_native import Recorder
from tests.test_controller_view import _Ps
from tests.test_families_cmangos import entry_with_sql
from tests.test_plan_corrections import (
    CORRECTED,
    OLD,
    _database,
    _Mariadb,
    _view,
    folder,
    imported_with,
)
from yulon import docker, forgetting, resources, runner
from yulon.catalog import native
from yulon.catalog.families.cmangos import CmangosInstaller
from yulon.ui import controller_view as controller_view_module


class _Answers:
    """A corrections route whose readings are taken in turn; the last one repeats."""

    def __init__(self, *states: str) -> None:
        self.states = list(states)
        self.checks = 0

    def check(self) -> native.CorrectionCheck:
        self.checks += 1
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        if state == "stale":
            return native.CorrectionCheck("stale", offered=("spell_template hotfix",))
        return native.CorrectionCheck(state, why="said so")  # type: ignore[arg-type]

    def route(self) -> native.CorrectionRoute:
        return native.CorrectionRoute(
            check=self.check,
            confirmation=lambda check: "apply?",
            press=lambda check, cancel: iter(()),
        )


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    from yulon.ui.widgets.job import run_inline

    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(controller_view_module, "_corrections_clock", fake)
    return fake


def _holder(**fields: object) -> docker.ServerHolder:
    base: dict[str, object] = {
        "name": "yulon-busy-abc",
        "container": "c0ffee",
        "press": "Update the server to latest…",
        "who": "pk@THEIR-PC (Windows)",
        "created": "2026-10-09T14:02:00Z",
    }
    return docker.ServerHolder(**{**base, **fields})  # type: ignore[arg-type]


def _engine(
    db: _Mariadb, asked: list[Path], holder: docker.ServerHolder | None
) -> CmangosInstaller:
    rec = Recorder()
    rec.db_started = True

    def reservation_holder(server_dir: Path, **_k: object) -> docker.ServerHolder | None:
        asked.append(server_dir)
        return holder

    return CmangosInstaller(
        entry_with_sql(CORRECTED),
        installers_root=resources.installers_dir(),
        seams=rec.seams(
            platform_id=lambda: "linux",
            exec_stdin=db.exec_stdin,
            sql_query=db.query,
            world_running=lambda container: False,
            db_running=lambda container: True,
            reservation_holder=reservation_holder,
        ),
    )


def test_a_stale_reading_with_another_yulon_holding_the_server_is_busy_and_offers_nothing(
    tmp_path: Path,
) -> None:
    db = _Mariadb(tmp_path)
    imported_with(db, OLD, tmp_path)
    asked: list[Path] = []
    check = _engine(db, asked, _holder()).correction_check(folder(tmp_path))
    assert check.state == "busy", check
    assert "Another Yu'lon" in check.why and "Update the server to latest" in check.why
    assert "pk@THEIR-PC" in check.why
    assert asked == [folder(tmp_path).server_dir]


def test_a_reservation_this_process_holds_is_not_busy(tmp_path: Path) -> None:
    """The press itself reads the check under its own reservation; it must still match."""
    db = _Mariadb(tmp_path)
    imported_with(db, OLD, tmp_path)
    check = _engine(db, [], _holder(here=True)).correction_check(folder(tmp_path))
    assert check.state == "stale", check


def test_nobody_holding_leaves_the_reading_stale(tmp_path: Path) -> None:
    db = _Mariadb(tmp_path)
    imported_with(db, OLD, tmp_path)
    assert _engine(db, [], None).correction_check(folder(tmp_path)).state == "stale"


def test_a_current_reading_asks_the_daemon_nothing(tmp_path: Path) -> None:
    db = _Mariadb(tmp_path)
    imported_with(db, CORRECTED, tmp_path)
    asked: list[Path] = []
    assert _engine(db, asked, _holder()).correction_check(folder(tmp_path)).state == "current"
    assert asked == []


def test_a_daemon_that_will_not_say_leaves_the_reading_stale(tmp_path: Path) -> None:
    """The press takes the reservation itself and refuses in its own words; a status path
    must not turn a slow Docker into a missing offer."""
    db = _Mariadb(tmp_path)
    imported_with(db, OLD, tmp_path)
    rec = Recorder()
    rec.db_started = True

    def broken(*_a: object, **_k: object) -> docker.ServerHolder:
        raise docker.DockerCommandError("timed out")

    engine = CmangosInstaller(
        entry_with_sql(CORRECTED),
        installers_root=resources.installers_dir(),
        seams=rec.seams(
            platform_id=lambda: "linux",
            exec_stdin=db.exec_stdin,
            sql_query=db.query,
            world_running=lambda container: False,
            db_running=lambda container: True,
            reservation_holder=broken,
        ),
    )
    assert engine.correction_check(folder(tmp_path)).state == "stale"


def test_the_sentence_names_the_press_who_and_since_when() -> None:
    said = forgetting.corrections_held_elsewhere(
        "WoW TBC", "Update the server to latest…", "14:02 (3 minutes ago)", "pk@THEIR-PC (Windows)"
    )
    assert "14:02 (3 minutes ago)" in said and "pk@THEIR-PC (Windows)" in said
    assert "Update the server to latest" in said
    assert "world updates" in said and "no retry" in said.lower().replace("nothing", "no retry")


# ----------------------------------------------------------------------- the tab


def test_a_busy_reading_shows_a_sentence_and_no_button(
    qapp: object,
    ps: object,
    tmp_path: Path,
) -> None:
    route = _Answers("busy")
    view = _view(ps, tmp_path, route)
    _database(ps, view, up=True)
    assert not view.corrections_banner.isHidden()  # type: ignore[attr-defined]
    assert view.corrections_banner_button.isHidden()  # type: ignore[attr-defined]
    assert "said so" in view.corrections_banner_label.text()  # type: ignore[attr-defined]
    assert view.apply_database_corrections() is False  # type: ignore[attr-defined]


def test_a_busy_reading_is_asked_again_every_half_minute_for_as_long_as_it_stands(
    qapp: object,
    ps: object,
    tmp_path: Path,
    clock,
) -> None:
    from yulon.ui.controller_view import BUSY_ASKED_AGAIN_AFTER

    route = _Answers("busy")
    view = _view(ps, tmp_path, route)
    _database(ps, view, up=True)
    for asked in range(2, 14):  # past the six bounded waits of an unreadable reading
        clock.now += BUSY_ASKED_AGAIN_AFTER - 1
        _database(ps, view, up=True)
        assert route.checks == asked - 1, "asked before the half minute was up"
        clock.now += 1
        _database(ps, view, up=True)
        assert route.checks == asked


def test_a_busy_reading_that_clears_brings_the_button_back(
    qapp: object,
    ps: object,
    tmp_path: Path,
    clock,
) -> None:
    from yulon.ui.controller_view import BUSY_ASKED_AGAIN_AFTER

    route = _Answers("busy", "stale")
    view = _view(ps, tmp_path, route)
    _database(ps, view, up=True)
    assert view.corrections_banner_button.isHidden()  # type: ignore[attr-defined]
    clock.now += BUSY_ASKED_AGAIN_AFTER
    _database(ps, view, up=True)
    assert route.checks == 2
    assert not view.corrections_banner_button.isHidden()  # type: ignore[attr-defined]


def test_the_seam_is_bound_to_the_wsl_distro() -> None:
    seams = native.Seams.in_wsl("dml-ubuntu")
    assert seams.reservation_holder is not docker.reservation_holder
