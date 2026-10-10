"""The Server tab's line under the buttons says the truth while the world crash-loops (T629).

T29 rerun on yulon-arch, 2026-10-09: Restart led to a world crash loop on honor
maintenance (T159). The badge read CRASH LOOP and the realm line "restart loop --
28 restarts", but the line under the buttons still read "The server is already
running." -- the greyed Start's reason, set from `docker ps` alone, where a looping
world is in the list between its restarts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_controller_view import _Ps, _services
from tests.test_dashboard import _loop_states, _loop_watch
from yulon import dashboard, runner
from yulon.catalog import native
from yulon.catalog.catalog import load_catalog
from yulon.controller import Controller
from yulon.ui.controller_view import (
    SERVER_ALL_RUNNING,
    SERVER_LOOPING,
    SERVER_LOOPING_CORRECTIONS,
    ControllerView,
)
from yulon.ui.widgets.job import run_inline

GAMES = ("wow-wotlk", "wow-tbc", "wow-vanilla", "wow-tortoise", "wow-centurion", "wow-unbound")

DB_ASSERT_LINE = (
    "/src/src/shared/Database/DatabaseMysql.cpp:190: Error: Assertion in HandleMySQLError "
    "failed: false\n"
)


HONOR_LOG = (
    "Initiating honor maintenance...\nMaking copy of character_inventory table.\n"
    "[1146] Table 'tw_char.character_inventory_copy' doesn't exist\n"
    "Your database structure is not up to date.\n" + DB_ASSERT_LINE + "./mangosd(main+0x326)\n"
)


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


LOOP = dashboard.Verdict("restart_loop", restarts=28)
HONOR_LOOP = dashboard.Verdict("restart_loop", restarts=28, honor_copy_missing=True)
UP = dashboard.Verdict("up", players=0, bots=500)


def _view(
    game: str, ps: _Ps, tmp_path: Path, verdicts: _Verdicts, *, corrections: bool = False
) -> ControllerView:
    entry = load_catalog().get(game)
    spec = entry.container_spec()
    ps.names = f"{spec.db}\n{spec.auth}\n{spec.world}\n"
    services = _services(ps, tmp_path, [])
    services.controller = Controller(spec, tmp_path)
    services.dashboard = verdicts
    if corrections:
        services.corrections = native.CorrectionRoute(
            check=lambda: native.CorrectionCheck("stale", offered=("character_inventory_copy",)),
            confirmation=lambda check: "apply?",
            press=lambda check, cancel: iter(()),
        )
    return ControllerView(entry, services, status_poll_ms=0, job_runner=run_inline)


def _said(view: ControllerView) -> str:
    return view.server_reasons.text()


@pytest.mark.parametrize("game", GAMES)
def test_a_crash_looping_world_is_not_called_already_running(
    qapp: object, ps: _Ps, tmp_path: Path, game: str
) -> None:
    """Poll first, verdict second -- and the other way round -- both say the loop.

    Mutation: leave `SERVER_ALL_RUNNING` in `_start_reason()` whatever the loop, and the
    line reads "The server is already running." again.
    """
    view = _view(game, ps, tmp_path, _Verdicts(LOOP))
    view.refresh_status()
    view.refresh_verdict()
    assert _said(view) == SERVER_LOOPING.format(restarts=28)
    assert "already running" not in _said(view)

    other = _view(game, ps, tmp_path, _Verdicts(LOOP))
    other.refresh_verdict()
    other.refresh_status()
    assert _said(other) == SERVER_LOOPING.format(restarts=28)


def test_the_sentence_matches_the_realm_line_count(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    view = _view("wow-tortoise", ps, tmp_path, _Verdicts(LOOP))
    view.refresh_status()
    view.refresh_verdict()
    assert "28 restarts" in _said(view)
    assert "28 restarts" in view.verdict_label.text()


@pytest.mark.parametrize("game", GAMES)
def test_a_healthy_world_still_reads_already_running(
    qapp: object, ps: _Ps, tmp_path: Path, game: str
) -> None:
    view = _view(game, ps, tmp_path, _Verdicts(UP))
    view.refresh_status()
    view.refresh_verdict()
    assert _said(view) == SERVER_ALL_RUNNING


def test_the_line_goes_back_when_the_loop_ends(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """Mutation: do not re-set the reason in `_verdict_ready()`, and it keeps the loop sentence."""
    verdicts = _Verdicts(LOOP)
    view = _view("wow-tortoise", ps, tmp_path, verdicts)
    view.refresh_status()
    view.refresh_verdict()
    verdicts.now = UP
    view.refresh_verdict()
    assert _said(view) == SERVER_ALL_RUNNING


def test_a_cleared_verdict_gives_the_line_back_while_the_server_is_still_polled_up(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Mutation: leave out the refresh in `_clear_the_verdict()`, and the loop sentence stays."""
    view = _view("wow-tortoise", ps, tmp_path, _Verdicts(LOOP))
    view.refresh_status()
    view.refresh_verdict()
    view._clear_the_verdict()
    assert _said(view) == SERVER_ALL_RUNNING


def test_docker_dying_keeps_its_own_reason_when_the_verdict_is_cleared(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Server up, Docker dies: the poll says "come back when Docker answers", and the verdict
    cleared after it must not put "already running" back under the Docker banner.

    Mutation: drop the docker-banner guard and the standing-reason guard in
    `_refresh_start_reason()`.
    """
    from yulon import docker

    view = _view("wow-tortoise", ps, tmp_path, _Verdicts(UP))
    view.refresh_status()
    view.refresh_verdict()
    view._status_failed(docker.DockerCommandError("cannot connect to the Docker daemon"))
    said = _said(view)
    assert "Docker" in said and "already running" not in said

    view._clear_the_verdict()
    assert _said(view) == said
    view._verdict_ready(LOOP)
    assert _said(view) == said


def test_a_stopped_distro_keeps_the_start_reason_it_has(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The distro stops after a poll saw the world up: no verdict can bring "already running"
    back over a server that is not running.

    Mutation: drop the distro guard in `_refresh_start_reason()` and the line is rewritten.
    """
    view = _view("wow-tortoise", ps, tmp_path, _Verdicts(LOOP))
    view.refresh_status()
    view.refresh_verdict()
    view._distro = "running"
    before = _said(view)
    view._distro_answered("stopped")
    assert _said(view) == before


def test_t159s_signature_points_at_apply_database_corrections(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Mutation: drop the `honor_copy_missing` branch, and it says only the generic loop."""
    view = _view("wow-tortoise", ps, tmp_path, _Verdicts(HONOR_LOOP), corrections=True)
    view.refresh_status()
    view.refresh_verdict()
    assert not view.corrections_banner_button.isHidden()
    assert _said(view) == SERVER_LOOPING_CORRECTIONS.format(restarts=28)
    assert native.CORRECTIONS_BUTTON_LABEL in _said(view)


def test_the_pointer_is_not_made_when_the_button_is_not_there(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Signature in the log but no corrections offered: a pointer at nothing is not said.

    Mutation: do not ask the banner's button, and this says Apply database corrections.
    """
    view = _view("wow-tortoise", ps, tmp_path, _Verdicts(HONOR_LOOP))
    view.refresh_status()
    view.refresh_verdict()
    assert _said(view) == SERVER_LOOPING.format(restarts=28)


def test_a_busy_job_keeps_its_own_reason(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """Mutation: skip the `_busy` guard in the verdict's refresh, and the job's words go."""
    verdicts = _Verdicts(UP)
    view = _view("wow-tortoise", ps, tmp_path, verdicts)
    view.refresh_status()
    view.refresh_verdict()
    view._busy = True
    before = _said(view)
    verdicts.now = LOOP
    view.refresh_verdict()
    assert _said(view) == before


# -------------------------------------------------- the dashboard reads the signature


def test_a_dead_run_with_the_honor_signature_is_flagged(tmp_path: Path) -> None:
    tick = _loop_watch(
        tmp_path, {"r1": HONOR_LOG}, _loop_states(("restarting", "r1"), ("restarting", "r1"))
    )
    first, second = tick(), tick()
    assert first.state == "restart_loop" and first.honor_copy_missing
    assert second.honor_copy_missing


def test_another_crash_is_not_flagged(tmp_path: Path) -> None:
    tick = _loop_watch(
        tmp_path,
        {"r1": "Loading spell chains...\n[1146] Table 'tw_char.other' doesn't exist\n"},
        _loop_states(("restarting", "r1")),
    )
    assert not tick().honor_copy_missing


def test_the_flag_does_not_flicker_on_the_fresh_run_between_two_crashes(tmp_path: Path) -> None:
    """Mutation: judge a running run by its own (still empty) log, and the flag drops."""
    logs = {"r1": HONOR_LOG, "r2": "Loading...\n"}
    tick = _loop_watch(
        tmp_path,
        logs,
        _loop_states(("restarting", "r1"), ("restarting", "r1"), ("running", "r2")),
    )
    tick()
    dead, alive = tick(), tick()
    assert dead.honor_copy_missing
    assert alive.state == "restart_loop" and alive.honor_copy_missing


def test_a_world_that_is_not_looping_carries_no_flag(tmp_path: Path) -> None:
    tick = _loop_watch(tmp_path, {"r1": HONOR_LOG}, _loop_states(("exited", "r1")))
    assert not tick().honor_copy_missing


def test_the_flag_goes_back_when_a_later_dead_run_lacks_the_signature(tmp_path: Path) -> None:
    """Mutation: `self._honor = self._honor or ...` keeps the flag for ever."""
    logs = {"r1": HONOR_LOG, "r2": "Loading spell chains...\n"}
    tick = _loop_watch(
        tmp_path,
        logs,
        _loop_states(
            ("restarting", "r1"), ("restarting", "r1"), ("running", "r2"), ("restarting", "r2")
        ),
    )
    tick()
    assert tick().honor_copy_missing
    tick()
    assert not tick().honor_copy_missing


def test_the_real_tortoise_crash_is_flagged_whatever_its_backtrace_length(tmp_path: Path) -> None:
    """The 2026-09-28 live capture (fedora), verbatim: 13 lines follow the 1146 line.

    Mutation: look at the last ten lines only, and the signature is missed.
    """
    log = (Path(__file__).parent / "fixtures" / "t629_honor_maintenance_crash.log").read_text(
        encoding="utf-8"
    )
    tick = _loop_watch(tmp_path, {"r1": log}, _loop_states(("restarting", "r1")))
    assert tick().honor_copy_missing


def test_an_earlier_honor_1146_then_another_database_death_is_not_flagged(
    tmp_path: Path,
) -> None:
    """Mutation: take the first [NNNN] line before the assert instead of the last."""
    log = HONOR_LOG + "[1062] Duplicate entry '1' for key 'PRIMARY'\n" + DB_ASSERT_LINE
    tick = _loop_watch(tmp_path, {"r1": log}, _loop_states(("restarting", "r1")))
    assert not tick().honor_copy_missing


def test_the_honor_flag_goes_with_a_loop_that_ended(tmp_path: Path) -> None:
    """Loop with the signature, a stop, then a new loop whose first run is running: no flag.

    Mutation: leave out the reset on the non-loop path in `Dashboard.tick()`.
    """
    tick = _loop_watch(
        tmp_path,
        {"r1": HONOR_LOG, "r2": "Loading...\n"},
        _loop_states(
            ("restarting", "r1"),
            ("restarting", "r1"),
            ("exited", "r1"),
            ("running", "r2"),
        ),
    )
    first, _, stopped, again = tick(), tick(), tick(), tick()
    assert first.honor_copy_missing and stopped.state == "stopped"
    assert again.state == "restart_loop" and not again.honor_copy_missing


def test_an_unreadable_log_after_a_loop_ended_carries_no_old_flag(tmp_path: Path) -> None:
    """The next loop's dead run cannot be read: the earlier loop's flag is not inherited."""

    class Unreadable(dict[str, str]):
        def get(self, key: str, default: str | None = None) -> str:
            if key == "r2":
                raise OSError("docker logs failed")
            return super().get(key, default or "")

    tick = _loop_watch(
        tmp_path,
        Unreadable({"r1": HONOR_LOG}),
        _loop_states(
            ("restarting", "r1"), ("restarting", "r1"), ("exited", "r1"), ("restarting", "r2")
        ),
    )
    tick(), tick(), tick()
    again = tick()
    assert again.state == "restart_loop" and not again.honor_copy_missing


def test_a_verdict_that_cannot_reach_docker_leaves_the_loop_line_for_the_poll(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The verdict read fails on Docker before the poll does (banner still hidden): the line
    keeps the loop sentence until the poll says what Docker said.

    Mutation: refresh Start's line in the Docker-unreachable branch of `_verdict_failed()`.
    """
    from yulon import docker

    view = _view("wow-tortoise", ps, tmp_path, _Verdicts(LOOP))
    view.refresh_status()
    view.refresh_verdict()
    assert view.docker_banner.isHidden()
    view._verdict_failed(docker.DockerCommandError("cannot connect to the Docker daemon"))
    assert _said(view) == SERVER_LOOPING.format(restarts=28)


def test_an_early_non_fatal_1146_does_not_point_at_the_corrections(tmp_path: Path) -> None:
    """The table error is not the last thing the run said: it died for another reason.

    Mutation: search the whole log for the signature without the death anchor.
    """
    log = (
        HONOR_LOG.split("Your database")[0]
        + "".join(f"Loading {n}...\n" for n in range(40))
        + "ASSERT failed\n"
    )
    tick = _loop_watch(tmp_path, {"r1": log}, _loop_states(("restarting", "r1")))
    assert not tick().honor_copy_missing


def test_the_line_follows_the_corrections_banner_showing_and_hiding(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """The banner's check lands after the verdict: the pointer appears with it, and goes.

    Mutation: leave `_refresh_start_reason()` out of `_refresh_corrections_banner()`.
    """
    view = _view("wow-tortoise", ps, tmp_path, _Verdicts(HONOR_LOOP))
    view.refresh_status()
    view.refresh_verdict()
    assert _said(view) == SERVER_LOOPING.format(restarts=28)
    view._corrections = native.CorrectionCheck("stale", offered=("character_inventory_copy",))
    view._refresh_corrections_banner()
    assert _said(view) == SERVER_LOOPING_CORRECTIONS.format(restarts=28)
    view._corrections = None
    view._refresh_corrections_banner()
    assert _said(view) == SERVER_LOOPING.format(restarts=28)


def test_a_banner_up_over_our_own_sentence_leaves_it_to_the_banner(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Mutation: drop the docker-banner guard (the standing-reason guard alone then passes
    only because the reason is not ours; here it is)."""
    view = _view("wow-tortoise", ps, tmp_path, _Verdicts(LOOP))
    view.refresh_status()
    view.refresh_verdict()
    view.docker_banner.setVisible(True)
    view.refresh_verdict()
    assert _said(view) == SERVER_LOOPING.format(restarts=28)


def test_another_reason_on_start_is_not_overwritten(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """Mutation: drop the standing-reason guard."""
    from yulon.ui.widgets.reasons import set_enabled_why

    verdicts = _Verdicts(UP)
    view = _view("wow-tortoise", ps, tmp_path, verdicts)
    view.refresh_status()
    view.refresh_verdict()
    set_enabled_why(view.start_button, "Something that knows more.")
    verdicts.now = LOOP
    view.refresh_verdict()
    assert _said(view) == "Something that knows more."


def test_a_held_badge_keeps_the_line_as_it_is(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """Mutation: drop the `_badge_held` guard, and a cleared verdict rewrites the loop line."""
    view = _view("wow-tortoise", ps, tmp_path, _Verdicts(LOOP))
    view.refresh_status()
    view.refresh_verdict()
    view._hold_badge("restarting")
    view.refresh_verdict()
    assert _said(view) == SERVER_LOOPING.format(restarts=28)
