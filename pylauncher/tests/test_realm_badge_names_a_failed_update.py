"""The realm badge names a world stuck at a failed database update (T608).

T600 made the Server tab's verdict line say which update failed. The badge beside it kept
saying STARTING / BUSY for a world that sits at that update forever, and once a Stop killed
the world the tab said only "stopped": the sentence lived in the Stop's output and nowhere else.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from tests.test_controller_view import WOTLK, _Ps, _services
from tests.test_dashboard import (
    FAILED_UPDATE_LOG,
    NOW,
    TORTOISE,
    _FakeSql,
    _running,
    _stamp,
)
from yulon import dashboard, docker, runner
from yulon.ui.controller_view import ControllerView
from yulon.ui.tray import status_words, tray_state, tray_tooltip
from yulon.ui.tray_flyout import dot_tone
from yulon.ui.widgets.dadcraft_decorations import DadcraftRealmBadge, realm_tone
from yulon.ui.widgets.job import run_inline

ALL_UP = "ac-database\nac-authserver\nac-worldserver\n"
SAID = (
    "The world server stopped at a database update it could not apply: 20260903063722_world.sql "
    "(the core). MariaDB said [1062] Duplicate entry '44070' for key 'PRIMARY'."
)
STUCK = dashboard.Verdict("up", players=0, bots=0, ready=False, failure=SAID)
LOOPING = dashboard.Verdict("restart_loop", restarts=7, failure=SAID)
AFTER_STOP = dashboard.Verdict("stopped", failure=SAID)


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


class _Verdicts:
    def __init__(self, verdict: dashboard.Verdict) -> None:
        self.now = verdict

    def __call__(self) -> dashboard.Verdict:
        return self.now


def _view(ps: _Ps, tmp_path: Path, verdicts: _Verdicts) -> ControllerView:
    services = _services(ps, tmp_path, [])
    services.dashboard = verdicts
    return ControllerView(WOTLK, services, status_poll_ms=0, job_runner=run_inline)


def test_a_world_stuck_at_a_failed_update_reads_update_failed_not_starting(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Mutation: drop the `_world_failed` clause from `_badge_word()` and this reads starting."""
    ps.names = ALL_UP
    view = _view(ps, tmp_path, _Verdicts(STUCK))
    view.refresh_status()

    view.refresh_verdict()

    assert view.realm_badge.status == "failed"
    text = view.realm_badge._label.text()
    assert "UPDATE FAILED" in text
    assert "STARTING" not in text and "ONLINE" not in text


def test_the_verdict_arriving_first_still_gives_the_failed_word(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    ps.names = ALL_UP
    view = _view(ps, tmp_path, _Verdicts(STUCK))

    view.refresh_verdict()
    view.refresh_status()

    assert view.realm_badge.status == "failed"


def test_a_crash_loop_keeps_its_own_word(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    ps.names = ALL_UP
    view = _view(ps, tmp_path, _Verdicts(LOOPING))
    view.refresh_status()

    view.refresh_verdict()

    assert view.realm_badge.status == "loop"


def test_the_failed_word_goes_when_the_next_verdict_has_no_failure(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    ps.names = ALL_UP
    verdicts = _Verdicts(STUCK)
    view = _view(ps, tmp_path, verdicts)
    view.refresh_status()
    view.refresh_verdict()

    verdicts.now = dashboard.Verdict("up", players=0, bots=500)
    view.refresh_verdict()

    assert view.realm_badge.status == "running"


def test_after_a_stop_the_tab_still_says_which_update_failed(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Mutation: make `line()` return a bare "stopped" and the sentence is gone from the tab."""
    ps.names = ""  # the Stop killed the world
    view = _view(ps, tmp_path, _Verdicts(AFTER_STOP))
    view.refresh_status()

    view.refresh_verdict()

    assert view.realm_badge.status == "stopped"
    shown = view.verdict_label.text()
    assert shown.startswith("stopped")
    assert "20260903063722_world.sql" in shown and "[1062]" in shown


def test_a_stopped_verdict_without_a_failure_is_still_bare() -> None:
    assert dashboard.line(dashboard.Verdict("stopped")) == "stopped"


def test_the_dashboard_keeps_the_sentence_for_the_run_a_stop_killed(tmp_path: Path) -> None:
    """The world is read as failed while it runs; the same run, stopped, keeps the sentence.

    Mutation: drop `failure=` from the `stopped` verdict in `_tick()`.
    """
    run = _stamp(NOW - timedelta(minutes=2))
    states = [_running(run), docker.ContainerState("exited", run, 0)]
    watch = dashboard.Dashboard(
        TORTOISE.container_spec(),
        TORTOISE,
        tmp_path,
        sql=_FakeSql(),
        state_of=lambda _container: states[0] if len(states) == 2 else states[-1],
        daemon_of=lambda: "bridge-before",
        log_of=lambda _container, _since: FAILED_UPDATE_LOG,
        now=lambda: NOW,
    )
    assert watch.tick().failure
    states.pop(0)

    stopped = watch.tick()

    assert stopped.state == "stopped"
    assert "20260903063722_world.sql" in dashboard.line(stopped)


def test_a_stopped_run_that_is_not_the_failed_one_does_not_inherit_the_sentence(
    tmp_path: Path,
) -> None:
    """Mutation: drop the run check from `_kept_failure()` and the new run is called failed."""
    run = _stamp(NOW - timedelta(minutes=2))
    later = _stamp(NOW - timedelta(seconds=10))
    states = [_running(run), docker.ContainerState("exited", later, 0)]
    watch = dashboard.Dashboard(
        TORTOISE.container_spec(),
        TORTOISE,
        tmp_path,
        sql=_FakeSql(),
        state_of=lambda _container: states[0],
        daemon_of=lambda: "bridge-before",
        log_of=lambda _container, _since: FAILED_UPDATE_LOG,
        now=lambda: NOW,
    )
    assert watch.tick().failure
    states.pop(0)

    assert watch.tick().failure == ""


def test_the_failed_word_is_down_and_the_tray_and_flyout_say_so(qapp: object) -> None:
    assert realm_tone("failed") == "down"
    assert status_words("failed") == "Update failed"
    assert dot_tone("failed") == "attention"


def test_the_badge_renders_the_failed_word_by_itself(qapp: object) -> None:
    badge = DadcraftRealmBadge("failed")
    assert "UPDATE FAILED" in badge._label.text()


def test_a_world_brought_back_by_something_else_does_not_read_failed(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The stopped verdict keeps the sentence; it must not arm the badge for the next run.

    Mutation: drop `and result.state != "stopped"` from `_verdict_ready()` and the healthy
    new run (docker compose up, Docker Desktop) reads UPDATE FAILED.
    """
    ps.names = ""
    verdicts = _Verdicts(AFTER_STOP)
    view = _view(ps, tmp_path, verdicts)
    view.refresh_status()
    view.refresh_verdict()
    assert "20260903063722_world.sql" in view.verdict_label.text()

    ps.names = ALL_UP  # started outside Yu'lon; no verdict has landed yet
    view.refresh_status()

    assert view.realm_badge.status != "failed"
    assert "UPDATE FAILED" not in view.realm_badge._label.text()


def test_the_tray_icon_needs_a_look_for_a_failed_update_like_for_a_loop() -> None:
    """Mutation: drop "failed" from `tray_state()` and the icon draws plain."""
    assert tray_state(["failed"]) == "attention"
    assert tray_state(["running", "failed"]) == "attention"


def test_the_tray_tooltip_names_a_failed_update() -> None:
    """Mutation: leave "failed" out of the tooltip's others and it says "no servers online"."""
    text = tray_tooltip([("WoW Tortoise", "failed"), ("WoW TBC", "stopped")])
    assert "WoW Tortoise — Update failed" in text
    assert "WoW TBC" not in text


def test_the_launcher_banner_does_not_say_a_hung_world_stopped() -> None:
    from yulon.ui import launcher_window

    assert "stuck at a failed update" in launcher_window.FAILED_BANNER
