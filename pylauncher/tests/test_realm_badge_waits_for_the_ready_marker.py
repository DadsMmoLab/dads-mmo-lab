"""The header says STARTING until the world's ready marker is seen for this run (T451).

From the m910q sitting of 2026-10-06 (T382 live, notes 1-2): the header and the
launcher read `● REALM ONLINE` for a second or more between a start and the
first verdict tick, on the tick between two crashes of a loop, and through the
whole "starting — Docker restarted…" window while the world crash-looped,
because the badge read `docker ps` alone and a container is running long
before the world can take a login.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_controller_view import WOTLK, _Ps, _services
from yulon import dashboard, runner
from yulon.ui.controller_view import ControllerView
from yulon.ui.widgets.job import run_inline

ALL_UP = "ac-database\nac-authserver\nac-worldserver\n"
READY = dashboard.Verdict("up", players=0, bots=500)
LOADING = dashboard.Verdict("up", players=0, bots=0, ready=False)


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


def _view(ps: _Ps, tmp_path: Path, verdicts: _Verdicts | None) -> ControllerView:
    services = _services(ps, tmp_path, [])
    services.dashboard = verdicts
    return ControllerView(WOTLK, services, status_poll_ms=0, job_runner=run_inline)


def test_a_poll_that_lands_before_any_verdict_reads_starting(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Mutation: drop the `_world_ready` clause from `_badge_word()`, and this reads running."""
    ps.names = ALL_UP
    view = _view(ps, tmp_path, _Verdicts(READY))

    view.refresh_status()

    assert view.realm_badge.status == "starting"
    assert "ONLINE" not in view.realm_badge._label.text()


def test_a_running_world_that_has_not_said_ready_reads_starting(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    ps.names = ALL_UP
    view = _view(ps, tmp_path, _Verdicts(LOADING))
    view.refresh_status()

    view.refresh_verdict()

    assert view.realm_badge.status == "starting"


def test_the_badge_goes_online_when_the_verdict_says_ready(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    ps.names = ALL_UP
    verdicts = _Verdicts(LOADING)
    view = _view(ps, tmp_path, verdicts)
    view.refresh_status()
    view.refresh_verdict()

    verdicts.now = READY
    view.refresh_verdict()

    assert view.realm_badge.status == "running"


@pytest.mark.parametrize("state", ["starting", "stopped", "unknown", "missing"])
def test_a_verdict_that_is_not_an_up_world_never_reads_online(
    qapp: object, ps: _Ps, tmp_path: Path, state: str
) -> None:
    """The Docker-restart window (verdict "starting"), and a run between two failed starts."""
    ps.names = ALL_UP
    view = _view(ps, tmp_path, _Verdicts(dashboard.Verdict(state)))  # type: ignore[arg-type]
    view.refresh_status()
    view.refresh_verdict()

    assert view.realm_badge.status == "starting"


def test_a_world_that_went_down_and_came_back_is_not_ready_again_at_once(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """A stale ready verdict says nothing about the next run.

    Mutation: leave `_world_ready` alone in `_status_ready()`, and the poll after
    the world came back reads running on the old verdict.
    """
    ps.names = ALL_UP
    view = _view(ps, tmp_path, _Verdicts(READY))
    view.refresh_status()
    view.refresh_verdict()
    assert view.realm_badge.status == "running"
    ps.names = ""
    view.refresh_status()
    ps.names = ALL_UP

    view.refresh_status()

    assert view.realm_badge.status == "starting"


def test_an_install_with_no_verdict_keeps_the_polls_word(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Nothing would ever say ready, so nothing may wait for it."""
    ps.names = ALL_UP
    view = _view(ps, tmp_path, None)

    view.refresh_status()

    assert view.realm_badge.status == "running"
