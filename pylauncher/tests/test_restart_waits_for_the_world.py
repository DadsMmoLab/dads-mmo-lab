"""A restart says done only once the world server is up, by Start's own ready rule.

From the T302 live check on yulon-ubuntu (2026-10-05, `live-t302-rates-2026-10-05`):
the Tuning tab's Restart server… on Tortoise reported `restart: done.` while
mangosd crash-looped on `[1146] Table tw_char.character_inventory_copy doesn't
exist` (`Restarting (139)`). The press stopped and started the containers and
called that done; nothing asked whether the world came up.

Now the press waits the way an install's ready stage does
(`native.wait_ready_quietly()`: the entry's own ready markers, then a watch of
`READY_GRACE_SECONDS` after the banner) and says what it found. A crash loop
after the restart is said in words on the report, and the server's own lines go
under Details with the command that prints the rest.

The docker seams are fakes that move their own clock, so nothing here sleeps or
talks to a daemon; the stop and the start go through `_Ps`, the Server tab tests'
fake docker CLI.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

import pytest

from tests.support_player_text import command_faults, text_faults
from tests.test_controller_view import ALL_UP, WOTLK, _Deferred, _Ps, _services
from yulon import docker, runner
from yulon.catalog import native
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.ui.controller_view import ControllerServices, ControllerView
from yulon.ui.widgets.job import run_inline

TORTOISE = load_catalog().get("wow-tortoise")

T302_LINES = (
    "Initiating honor maintenance...",
    "Making copy of character_inventory table.",
    "[1146] Table 'tw_char.character_inventory_copy' doesn't exist",
    "Assertion failed in HandleMySQLError",
)
"""What Tortoise's mangosd printed on every run after the T302 restart, in that order."""

WOTLK_ABORT = (
    "AC> [1146] Table 'acore_world.city_bot_poi' doesn't exist",
    ">> ABORTED",
    "# Location '/azerothcore/src/server/database/Database/MySQLConnection.cpp:634'",
)
"""T71's WotLK shape (gate `t63-owed-live`): no `fatal` marker on this entry, so the loop is
seen by its restart count alone."""


@dataclass
class _World:
    """One world container after a start, as `wait_ready_quietly()`'s two seams see it.

    `wait` is `docker.wait_ready_for`: it spends the window it is handed (or up to
    the banner) and answers True only once the banner is printed. `output` is the
    look at the container between windows and during the watch after the banner.
    `elapsed` moves only when a window or a sleep spends it.

    `crash_every_s` set: every run prints `crash_lines` and dies that many seconds
    in, and docker brings it back (`restart: unless-stopped`), so the count grows
    and the status reads `restarting` -- the T302 shape.
    """

    banner: str
    boot_s: float = 20.0
    crash_every_s: float | None = None
    crash_lines: tuple[str, ...] = ()
    elapsed: float = 0.0
    looks: list[float] = field(default_factory=list)

    def clock(self) -> float:
        return self.elapsed

    def sleep(self, seconds: float) -> None:
        self.elapsed += seconds

    def wait(self, spec: docker.ContainerSpec, ready: docker.ReadySpec, **_kw: object) -> bool:
        if self.crash_every_s is None and self.boot_s <= self.elapsed + ready.timeout:
            self.elapsed = max(self.elapsed, self.boot_s)
            return True
        # The real wait returns False early on a `fatal` line or its own crash-loop
        # latch; a loop is caught within a few of its runs.
        self.elapsed += min(ready.timeout, 5 * (self.crash_every_s or ready.timeout))
        return False

    def output(self, spec: docker.ContainerSpec, **_kw: object) -> native.WorldOutput:
        self.looks.append(self.elapsed)
        if self.crash_every_s is not None:
            restarts = int(self.elapsed // self.crash_every_s)
            return native.WorldOutput("\n".join(self.crash_lines), restarts, "restarting")
        said = ["Loading maps..."] + ([self.banner] if self.elapsed >= self.boot_s else [])
        return native.WorldOutput("\n".join(said), 0, "running")


def _after_start(
    entry: CatalogEntry, world: _World, cancel: threading.Event | None = None
) -> native.StartAnswer:
    return native.ready_after_start(
        entry,
        entry.container_spec(),
        wait=world.wait,
        output=world.output,
        monotonic=world.clock,
        sleep=world.sleep,
        cancel=cancel,
    )


# -- the rule: Start's, with the entry's own markers ------------------------------------------


def test_a_world_that_comes_up_is_ready_only_after_it_stayed_up_for_the_watch() -> None:
    """The banner and then `READY_GRACE_SECONDS` of looking, as an install's ready stage does.

    Mutation: return ready at the banner without `watch_after_ready()`, and
    `elapsed` stops at the boot.
    """
    world = _World(banner="AzerothCore rev. 1 ready...")

    answer = _after_start(WOTLK, world)

    assert answer.ready
    assert world.elapsed == pytest.approx(world.boot_s + native.READY_GRACE_SECONDS)


def test_the_world_is_waited_on_with_the_entry_s_own_ready_marker() -> None:
    """Tortoise's banner is not AzerothCore's: a wait for `ready...` would never end on it."""
    seen: list[docker.ReadySpec] = []
    world = _World(banner="World server is up and running")

    def wait(spec: docker.ContainerSpec, ready: docker.ReadySpec, **kw: object) -> bool:
        seen.append(ready)
        return world.wait(spec, ready, **kw)

    answer = native.ready_after_start(
        TORTOISE,
        TORTOISE.container_spec(),
        wait=wait,
        output=world.output,
        monotonic=world.clock,
        sleep=world.sleep,
    )

    assert answer.ready
    native_block = TORTOISE.install.native
    assert native_block is not None
    assert seen and all(ready.world == native_block.ready.world for ready in seen)
    assert seen[0].fatal == native_block.ready.fatal


def test_a_crash_loop_after_the_start_is_not_ready_and_keeps_the_server_s_own_lines() -> None:
    """T302: mangosd dies on the missing table on every run. The words are the line that says why.

    Mutation: answer `ready` for a world `wait` never found the banner in, and
    this reads True.
    """
    world = _World(banner="World server is up and running", crash_every_s=3.0)
    world.crash_lines = T302_LINES

    answer = _after_start(TORTOISE, world)

    assert not answer.ready
    assert answer.verdict in {"loop", "fatal"}
    assert "character_inventory_copy" in answer.words


def test_a_wotlk_crash_loop_is_called_a_loop_and_quotes_its_last_lines() -> None:
    """No `fatal` marker on WotLK: the count is the evidence, and the last lines are quoted."""
    world = _World(banner="AzerothCore rev. 1 ready...", crash_every_s=3.0)
    world.crash_lines = WOTLK_ABORT

    answer = _after_start(WOTLK, world)

    assert answer.verdict == "loop"
    assert "city_bot_poi" in answer.words and ">> ABORTED" in answer.words


def test_a_world_that_says_ready_and_then_dies_is_not_ready() -> None:
    """T71's shape after a restart: the banner, then the abort inside the watch."""
    world = _World(banner="AzerothCore rev. 1 ready...")
    real_output = world.output

    def output(spec: docker.ContainerSpec, **kw: object) -> native.WorldOutput:
        if world.elapsed >= world.boot_s + 10:
            return native.WorldOutput("\n".join(WOTLK_ABORT), 1, "restarting")
        return real_output(spec, **kw)

    answer = native.ready_after_start(
        WOTLK,
        WOTLK.container_spec(),
        wait=world.wait,
        output=output,
        monotonic=world.clock,
        sleep=world.sleep,
    )

    assert answer.verdict == "stopped"
    assert "city_bot_poi" in answer.words


def test_the_bool_wait_answers_what_the_start_answer_says() -> None:
    """`wait_ready_quietly()` is the bool half of the same function: one rule, two shapes."""
    up = _World(banner="AzerothCore rev. 1 ready...")
    looping = _World(banner="AzerothCore rev. 1 ready...", crash_every_s=3.0)
    ready = docker.azerothcore_ready("127.0.0.1", 8085)

    for world, expected in ((up, True), (looping, False)):
        assert (
            native.wait_ready_quietly(
                WOTLK.container_spec(),
                ready,
                wait=world.wait,
                output=world.output,
                monotonic=world.clock,
                sleep=world.sleep,
            )
            is expected
        )


# -- the press: Restart server… and Recreate containers… on the Tuning tab --------------------


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


def _view(
    ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, world: _World | None
) -> ControllerView:
    services = _services(ps, tmp_path, [])
    if world is not None:
        object.__setattr__(services, "ready_after_start", partial(_after_start, WOTLK, world))
    view = ControllerView(WOTLK, services, status_poll_ms=0, job_runner=run_inline)
    monkeypatch.setattr(view, "_confirm", lambda *_a, **_k: True)
    return view


def _pressed(view: ControllerView, job: str) -> None:
    if job == "restart":
        view.restart_server()
    else:
        view.recreate_containers()


@pytest.mark.parametrize("job", ["restart", "recreate"])
def test_a_restart_into_a_crash_loop_says_so_and_puts_the_server_s_lines_under_details(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, job: str
) -> None:
    """The T302 report, through the press: stop, start, and the world crash-loops.

    Mutation: write `f"{job}: done."` whatever the wait answered, and the report
    says done over a crash loop again.
    """
    world = _World(banner="AzerothCore rev. 1 ready...", crash_every_s=3.0)
    world.crash_lines = WOTLK_ABORT
    view = _view(ps, tmp_path, monkeypatch, world)

    _pressed(view, job)

    report = view.tuning_report.toPlainText()
    assert "done" not in report
    assert "crash" in report and "Details" in report, report
    assert any(c[:4] == ["docker", "compose", "up", "-d"] for c in ps.calls), "never started"
    assert world.looks, "the press never looked at the world after the start"
    details = view.tuning_details.text()
    assert "city_bot_poi" in details and ">> ABORTED" in details
    assert "docker compose logs ac-worldserver" in details
    assert text_faults(report) == [] and command_faults(report) == []


@pytest.mark.parametrize("job", ["restart", "recreate"])
def test_a_restart_says_done_only_once_the_world_reported_ready_and_stayed_up(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, job: str
) -> None:
    world = _World(banner="AzerothCore rev. 1 ready...")
    view = _view(ps, tmp_path, monkeypatch, world)

    _pressed(view, job)

    assert view.tuning_report.toPlainText().startswith(f"{job}: done.")
    assert world.elapsed == pytest.approx(world.boot_s + native.READY_GRACE_SECONDS)


def test_a_restart_that_cannot_ask_after_the_world_does_not_say_done(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No ready wait handed to the tab (a test's fake services): it says the start, not done."""
    view = _view(ps, tmp_path, monkeypatch, None)

    view.restart_server()

    assert view.tuning_report.toPlainText() == "restart: the server was started."


@pytest.mark.parametrize(
    ("verdict", "words"),
    [
        ("stopped", "said ready"),
        ("gone", "not running"),
        ("fatal", "error"),
        ("quiet", "stopped printing"),
        ("unreadable", "Docker stopped answering"),
        ("ceiling", "still loading"),
        ("cancelled", "wait for the world server was stopped"),
    ],
)
def test_every_way_a_start_can_end_has_its_own_plain_sentence(
    qapp: object,
    ps: _Ps,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    verdict: str,
    words: str,
) -> None:
    services = _services(ps, tmp_path, [])
    answer = native.StartAnswer(verdict, "the server's last line")  # type: ignore[arg-type]
    object.__setattr__(services, "ready_after_start", lambda **_kw: answer)
    view = ControllerView(WOTLK, services, status_poll_ms=0, job_runner=run_inline)
    monkeypatch.setattr(view, "_confirm", lambda *_a, **_k: True)

    view.restart_server()

    report = view.tuning_report.toPlainText()
    assert words in report and "done" not in report, report
    assert text_faults(report) == [] and command_faults(report) == []


# -- the wiring: every game's tab gets the wait, with its own entry ---------------------------


def _games() -> Iterator[CatalogEntry]:
    yield from load_catalog().games


@pytest.mark.parametrize("entry", list(_games()), ids=lambda e: e.id)
def test_every_game_s_services_wait_for_its_own_world_after_a_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: CatalogEntry
) -> None:
    """`for_entry()` wires the wait for every game, aimed at that game's markers and container.

    Mutation: leave `ready_after_start` unset in `for_entry()`, and the press
    falls back to "the server was started" on every real install.
    """
    password_file = entry.install.password.file
    if password_file:
        (tmp_path / password_file).write_text("hunter2", encoding="utf-8")
    services = ControllerServices.for_entry(entry, tmp_path)
    assert services.ready_after_start is not None
    seen: list[tuple[str, str]] = []

    def wait(spec: docker.ContainerSpec, ready: docker.ReadySpec, **_kw: object) -> bool:
        seen.append((spec.world, ready.world))
        return True

    looked: list[str] = []

    def output(spec: docker.ContainerSpec, **_kw: object) -> native.WorldOutput:
        looked.append(spec.world)
        return native.WorldOutput(
            "loading\nready...\n(worldserver-daemon) ready...\nAvg Diff: 15ms\n"
            "World server is up and running",
            0,
            "running",
        )

    monkeypatch.setattr(docker, "wait_ready_for", wait)
    monkeypatch.setattr(native, "_world_output", output)
    monkeypatch.setattr(native.time, "sleep", lambda _s: None)

    assert services.ready_after_start().ready
    assert seen and seen[0][0] == services.controller.spec.world
    native_block = entry.install.native
    assert native_block is not None
    assert seen[0][1] == native.ready_spec_for(entry, native_block.ready).world
    assert looked and set(looked) == {services.controller.spec.world}


# -- the wait holds no button: Stop, Start and Play stay free while the world loads --------


def _deferred_view(
    ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wait: object
) -> tuple[ControllerView, _Deferred]:
    jobs = _Deferred()
    services = _services(ps, tmp_path, [])
    object.__setattr__(services, "ready_after_start", wait)
    view = ControllerView(WOTLK, services, status_poll_ms=0, job_runner=jobs)
    jobs.queue.clear()
    monkeypatch.setattr(view, "_confirm", lambda *_a, **_k: True)
    return view, jobs


def _run_the(jobs: _Deferred, on_done: object) -> None:
    [index] = [i for i, (_w, done, _e) in enumerate(jobs.queue) if done == on_done]
    jobs.run(index)


def test_the_server_tab_is_not_busy_while_the_restart_waits_for_the_world(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cold review: the wait ran inside the press's busy job, so Stop was greyed for
    the world's whole load -- up to the ready ceiling, near two hours.

    Mutation: run the wait inside the restart's own job, and `_busy` is True while
    it waits (and Stop says "Wait: Restart is running").
    """
    seen: list[bool] = []
    cancels: list[threading.Event] = []

    def wait(*, cancel: threading.Event) -> native.StartAnswer:
        seen.append(view._busy)
        cancels.append(cancel)
        return native.StartAnswer("ready")

    ps.names = ALL_UP
    view, jobs = _deferred_view(ps, tmp_path, monkeypatch, wait)

    view.restart_server()
    assert view._busy
    _run_the(jobs, view._tuning_job_done)

    assert view._busy is False, "the world wait held the Server tab"
    assert "Waiting for the world server" in view.tuning_report.toPlainText()
    _run_the(jobs, view._tuning_world_answered)
    assert seen == [False]
    assert view.tuning_report.toPlainText().startswith("restart: done.")
    assert cancels and not cancels[0].is_set()


def test_a_server_action_pressed_during_the_wait_ends_it_and_owns_the_report(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stop during the load: the wait is cancelled, and a newer press is never written over.

    Mutation: drop `_end_the_world_wait()` from `_set_busy()`, and the cancel is
    never set; drop the number check, and the old wait's answer replaces the
    newer restart's report.
    """
    cancels: list[threading.Event] = []

    def wait(*, cancel: threading.Event) -> native.StartAnswer:
        cancels.append(cancel)
        return native.StartAnswer("cancelled" if cancel.is_set() else "ready")

    ps.names = ALL_UP
    view, jobs = _deferred_view(ps, tmp_path, monkeypatch, wait)
    view.restart_server()
    _run_the(jobs, view._tuning_job_done)
    [first] = [w for w, done, _e in jobs.queue if done == view._tuning_world_answered]

    view.restart_server()  # a second press while the first wait is still out
    assert view._world_wait is None, "the newer press did not end the old wait"

    _run_the(jobs, view._tuning_job_done)
    second = [w for w, done, _e in jobs.queue if done == view._tuning_world_answered][-1]
    view._tuning_world_answered(second())
    assert view.tuning_report.toPlainText().startswith("restart: done.")
    view._tuning_world_answered(first())  # the first wait answers last: cancelled
    assert cancels[-1].is_set()
    assert view.tuning_report.toPlainText().startswith("restart: done."), "overwritten"


def test_stop_during_the_wait_cancels_it(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ps.names = ALL_UP
    view, jobs = _deferred_view(
        ps, tmp_path, monkeypatch, lambda *, cancel: native.StartAnswer("ready")
    )
    view.restart_server()
    _run_the(jobs, view._tuning_job_done)
    cancel = view._world_wait
    assert cancel is not None and not cancel.is_set()

    view.stop_server()

    assert cancel.is_set()


def test_closing_the_tab_ends_a_world_wait_still_out(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`shutdown()` joins the tab's jobs; a world wait must not hold that join for a load."""
    view, jobs = _deferred_view(
        ps, tmp_path, monkeypatch, lambda *, cancel: native.StartAnswer("ready")
    )
    view.restart_server()
    _run_the(jobs, view._tuning_job_done)
    cancel = view._world_wait
    assert cancel is not None

    view.shutdown()

    assert cancel.is_set()


def test_a_cancel_reaches_the_wait_and_ends_it_as_cancelled() -> None:
    """The Server tab's Stop ends the wait through `ready_after_start(cancel=)` (T247's verdict).

    Mutation: do not hand `cancel` on to `watch_the_start()`, and a world still
    loading is waited out to its quiet budget instead.
    """
    world = _World(banner="AzerothCore rev. 1 ready...", boot_s=10_000.0)
    stop = threading.Event()
    stop.set()

    answer = _after_start(WOTLK, world, stop)

    assert answer.verdict == "cancelled"
