"""The realm badge never says REALM ONLINE over a world that is crash-looping.

From the m910q sitting of 2026-10-06 (`live-m910q-sitting-2026-10-06`, PART A):
a Rebuild failed at the ready wait, its rollback failed too, and the containers
were left up with the world crash-looping -- while the header read
`● REALM ONLINE`. The badge read `docker ps` alone, and a crash-looping world is
in `docker ps` between its restarts; the Server tab's verdict line under it
already said `restart loop`.

Now the badge takes the verdict's word for the world: a `restart_loop` verdict
over containers that are up reads CRASH LOOP, and the next verdict that is not a
loop gives the badge back to the poll.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_controller_view import WOTLK, _Ps, _services
from yulon import dashboard, runner
from yulon.ui import launcher_window
from yulon.ui.controller_view import ControllerView
from yulon.ui.widgets.dadcraft_decorations import DadcraftRealmBadge, realm_tone
from yulon.ui.widgets.job import run_inline

ALL_UP = "ac-database\nac-authserver\nac-worldserver\n"


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


class _Verdicts:
    """The dashboard seam, answering whatever the test last set."""

    def __init__(self, verdict: dashboard.Verdict) -> None:
        self.now = verdict

    def __call__(self) -> dashboard.Verdict:
        return self.now


LOOP = dashboard.Verdict("restart_loop", restarts=6)
UP = dashboard.Verdict("up", players=0, bots=500)


def _view(ps: _Ps, tmp_path: Path, verdicts: _Verdicts) -> ControllerView:
    services = _services(ps, tmp_path, [])
    services.dashboard = verdicts
    return ControllerView(WOTLK, services, status_poll_ms=0, job_runner=run_inline)


def test_a_crash_looping_world_reads_crash_loop_not_realm_online(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The T391 shape: all three containers in `docker ps`, the verdict a loop.

    Mutation: set the badge from `_realm_badge_status(status)` alone in
    `_status_ready()`, and this reads "running" (REALM ONLINE) again.
    """
    ps.names = ALL_UP
    view = _view(ps, tmp_path, _Verdicts(LOOP))

    view.refresh_verdict()
    view.refresh_status()

    assert view.realm_badge.status == "loop"
    assert "CRASH LOOP" in view.realm_badge._label.text()
    assert "ONLINE" not in view.realm_badge._label.text()


def test_a_loop_verdict_that_lands_after_the_poll_takes_the_badge_down_at_once(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The other order: the poll said all up first; the verdict must not wait a poll.

    Mutation: leave the badge alone in `_verdict_ready()`, and it stays "running"
    until the next status poll.
    """
    ps.names = ALL_UP
    verdicts = _Verdicts(UP)
    view = _view(ps, tmp_path, verdicts)
    view.refresh_status()
    view.refresh_verdict()
    assert view.realm_badge.status == "running"

    verdicts.now = LOOP
    view.refresh_verdict()

    assert view.realm_badge.status == "loop"


def test_once_the_loop_is_over_the_badge_reads_the_poll_again(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Fixed and up: REALM ONLINE comes back with the first verdict that is not a loop."""
    ps.names = ALL_UP
    verdicts = _Verdicts(LOOP)
    view = _view(ps, tmp_path, verdicts)
    view.refresh_status()
    view.refresh_verdict()
    assert view.realm_badge.status == "loop"

    verdicts.now = UP
    view.refresh_verdict()

    assert view.realm_badge.status == "running"


def test_a_loop_with_the_world_stopped_reads_offline_not_crash_loop(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Nothing of the server in `docker ps`: the poll's OFFLINE stands, whatever came before."""
    ps.names = ""
    view = _view(ps, tmp_path, _Verdicts(LOOP))

    view.refresh_verdict()
    view.refresh_status()

    assert view.realm_badge.status == "stopped"


def test_a_loop_verdict_does_not_move_a_badge_our_own_job_holds(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """T188: while our Start/Stop/Restart runs the badge says that job, not a reading."""
    ps.names = ALL_UP
    view = _view(ps, tmp_path, _Verdicts(LOOP))
    view._hold_badge("restarting")

    view.refresh_verdict()

    assert view.realm_badge.status == "restarting"


def test_the_crash_loop_word_is_the_restarting_tone_and_the_launcher_explains_it(
    qapp: object,
) -> None:
    """The sidebar dot and the launcher's PLAY line read the same word (T187, T192)."""
    badge = DadcraftRealmBadge("loop")

    assert realm_tone("loop") == "restarting"
    assert badge._label.text() == "◆ CRASH LOOP"
    reason = launcher_window._REASONS["loop"]
    assert "crash" in reason and "Server tab" in reason


def test_a_loop_seen_before_our_own_restart_is_not_carried_past_it(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The verdict was about the run the restart ended; the first poll after it is the poll's.

    Mutation: keep `_world_loops` through `_clear_the_verdict()`, and the badge
    reads CRASH LOOP after the restart before any verdict about the new run.
    """
    ps.names = ALL_UP
    verdicts = _Verdicts(LOOP)
    view = _view(ps, tmp_path, verdicts)
    view.refresh_status()
    view.refresh_verdict()
    assert view.realm_badge.status == "loop"
    monkeypatch.setattr(view, "_confirm", lambda *_a, **_k: True)

    view.restart_server()

    assert view.realm_badge.status == "starting"  # T451: no verdict about the new run yet
