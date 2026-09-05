"""The `ready` stage's budget: what "it never came up" is allowed to mean.

The incident this file exists for, measured on yulon-win11-gate 2026-09-04 from
the world server's OWN timestamps (`docker logs -t`), Docker Desktop, the server
directory on a 9p share reading at about 1.4 MB/s:

    Vanilla  06:12:43Z mangosd start -> 06:37:22Z first `Avg Diff:`  = 24.6 min
    TBC      18:59:55Z mangosd start -> 19:45:58Z first `Avg Diff:`  = 46.0 min

Both entries carried `ready.timeout_s: 1800`, so the first fitted and the second
did not. TBC's install ended `install failed: The server started but never
reported ready`, exit 1, while `tbc-mangosd` was up with `restarts=0`, had
loaded its world, and went on printing its diff loop for hours afterwards. The
install was complete and correct; only the verdict was wrong.

The two games differ in the size of the world being read over that mount, not in
anything either core does, so no fixed wall-clock number is right on both a
native Linux disk and a 9p share. What separates the two cases is not how long
the server has taken but whether it is still SAYING anything: the budget here is
therefore a quiet one, and every test below drives it against a world server
whose printing is the variable.

Nothing in this file sleeps or talks to a daemon. `FakeWorld` is the clock: it
advances only when the engine spends a window on it, so a 46-minute boot and a
six-hour ceiling both run in microseconds.
"""

from __future__ import annotations

import ast
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests.support_native import ENTRY, TBC, Recorder
from yulon import docker, resources
from yulon.catalog import native
from yulon.catalog.catalog import CatalogEntry, ReadyMarkers
from yulon.catalog.families.cmangos import CmangosInstaller
from yulon.catalog.installer import InstallerError

TBC_QUIET_S = 1800
"""What `catalog.json` gives both CMaNGOS entries. Pinned by `test_the_catalogue...` below."""

TBC_BOOT_S = 46 * 60
"""The measured 9p first boot the old budget called a failure (yulon-win11-gate, 2026-09-04)."""


@dataclass
class FakeWorld:
    """A world server that boots slowly and prints while it does it.

    Stands in for BOTH seams the ready wait uses, so one object is the whole
    machine: `wait_ready` is `docker.wait_ready_for` (it polls for the window it
    is given and answers True only once the banner has been printed) and
    `output` is the engine's look at what the container has said since.

    `elapsed` moves only inside `wait_ready`, which is what makes the file fast
    and what makes it honest: the engine cannot see time the fake did not grant
    it, so a test that asserts "six hours" is asserting about windows the engine
    actually spent, not about a number it read.
    """

    boot_s: float = TBC_BOOT_S
    """When the world server prints its ready marker. `inf` means never."""

    quiet_after_s: float | None = None
    """When it stops printing altogether. None: it prints all the way through."""

    print_every_s: float = 60.0
    restart_every_s: float | None = None
    fatal_after_s: float | None = None
    status: str = "running"

    gives_up_after_s: float | None = None
    """How long a window that does NOT find the banner actually lasts. None: all of it.

    The real `docker.wait_ready()` returns False EARLY on three of its four
    paths -- a `fatal` line, its own crash-loop latch, and a missing docker CLI
    after a 30-second grace -- so a window handed 1800 seconds can end in 30 and
    hand back the same `False` a fully spent one does. Until this field existed
    the fake always consumed the whole window, so no test in this file could
    tell a duration the engine MEASURED from one it assumed off the window
    count -- and the engine assumed (review, m910q 2026-09-05).
    """

    elapsed: float = 0.0
    windows: list[float] = field(default_factory=list)
    """Every `ReadySpec.timeout` the engine handed a window, in order."""

    def clock(self) -> float:
        """The engine's `monotonic` seam: the time this fake has actually granted."""
        return self.elapsed

    def wait_ready(
        self, spec: docker.ContainerSpec, ready: docker.ReadySpec, **_kwargs: object
    ) -> bool:
        self.windows.append(ready.timeout)
        if self.boot_s <= self.elapsed + ready.timeout:
            self.elapsed = self.boot_s
            return True
        spent = ready.timeout if self.gives_up_after_s is None else self.gives_up_after_s
        self.elapsed += min(spent, ready.timeout)
        return False

    def output(self, spec: docker.ContainerSpec, **_kwargs: object) -> native.WorldOutput:
        printing_until = self.elapsed if self.quiet_after_s is None else self.quiet_after_s
        lines = [
            f">> Loading something big, {int(n * self.print_every_s)}s in"
            for n in range(1, int(min(self.elapsed, printing_until) / self.print_every_s) + 1)
        ]
        if self.fatal_after_s is not None and self.elapsed >= self.fatal_after_s:
            lines.append("Correct *.map files not found in data directory.")
        restarts = 0 if self.restart_every_s is None else int(self.elapsed / self.restart_every_s)
        return native.WorldOutput(text="\n".join(lines), restarts=restarts, status=self.status)


def _installer(
    world: FakeWorld, entry: CatalogEntry = TBC, **overrides: object
) -> native.StagedInstaller:
    """The spine with its ready seams pointed at `world` and nothing else changed."""
    rec = Recorder()
    seams = {
        "wait_ready": world.wait_ready,
        "world_output": world.output,
        "monotonic": world.clock,
        **overrides,
    }
    return CmangosInstaller(
        entry,
        installers_root=resources.installers_dir(),
        import_probe=rec.probe,
        reset_unfinished=rec.reset,
        seams=rec.seams(**seams),
    )


def _ctx() -> native.StageContext:
    return native.StageContext(
        server_dir=Path("/srv/wow"),
        client_dir=None,
        state=native.InstallState("wow-tbc", "abc", "cmangos"),
        cancel=threading.Event(),
        secrets=native.Secrets("hunter2"),
    )


def _ready(world: FakeWorld, **overrides: object) -> list[str]:
    return list(_installer(world, **overrides).stage_ready(_ctx()))


def _refusal(world: FakeWorld, **overrides: object) -> str:
    with pytest.raises(InstallerError) as caught:
        _ready(world, **overrides)
    return str(caught.value)


# -- the incident -----------------------------------------------------------


def test_a_world_server_printing_through_a_46_minute_boot_is_not_a_failed_install() -> None:
    """The RED this lane was opened for: TBC's measured 9p boot, second by second.

    46 minutes against a 30-minute budget. Under the old single-window wait this
    raised `The server started but never reported ready` at 30:00 while the
    container was up, had never restarted, and was printing a line a minute --
    and it went on to print its ready marker sixteen minutes later, exactly as
    the real one did on yulon-win11-gate.
    """
    world = FakeWorld(boot_s=TBC_BOOT_S)
    lines = _ready(world)

    assert lines[-1] == "The server is up."
    assert world.elapsed == pytest.approx(TBC_BOOT_S)
    assert len(world.windows) == 2, "one 30-minute window cannot hold a 46-minute boot"


def test_the_wait_says_out_loud_that_it_is_still_being_given_time() -> None:
    """A user watching a 46-minute install must be told why it is still going.

    The note is the observable half of the decision: it names the evidence the
    engine acted on (the server is printing) rather than only counting minutes.
    """
    world = FakeWorld(boot_s=TBC_BOOT_S)
    lines = _ready(world)
    notes = [line for line in lines if "still printing" in line]

    assert len(notes) == 1, lines
    assert "30 minutes" in notes[0]


def test_a_boot_far_past_any_budget_still_finishes_while_the_server_talks() -> None:
    """Five hours of loading, granted one window at a time, with no number to raise.

    The point of a quiet budget: `timeout_s` stops being a guess about how slow
    the slowest disk anyone owns might be.
    """
    world = FakeWorld(boot_s=5 * 60 * 60)
    assert _ready(world)[-1] == "The server is up."
    assert world.elapsed == pytest.approx(5 * 60 * 60)


# -- and the failures it must still call failures ---------------------------


def test_a_server_that_goes_quiet_is_refused_and_the_sentence_says_which() -> None:
    """Alive, not restarting -- and silent. That is stuck, and stuck is a failure."""
    world = FakeWorld(boot_s=float("inf"), quiet_after_s=5 * 60)
    message = _refusal(world)

    assert "never reported ready" in message
    assert "stopped printing" in message
    assert "30 minutes" in message
    assert "mangosd" in message, "the sentence must name the log to read"
    # It gave up on the FIRST silent window rather than sitting out a ceiling:
    # one window to see the last output, one to see none.
    assert len(world.windows) == 2


def test_the_two_verdicts_do_not_share_a_sentence() -> None:
    """A refusal that cannot say which of the two happened is the bug, restated."""
    quiet = _refusal(FakeWorld(boot_s=float("inf"), quiet_after_s=0.0))
    looping = _refusal(FakeWorld(boot_s=float("inf"), restart_every_s=60.0))

    assert quiet != looping
    assert "stopped printing" in quiet and "restart" not in quiet
    assert "restarted" in looping and "stopped printing" not in looping


def test_a_crash_loop_is_still_refused_when_every_restart_prints_afresh() -> None:
    """A crash loop prints, so "it is printing" cannot be the whole rule.

    `docker.wait_ready()` counts restarts within ONE call and this wait now
    makes several, so the count has to be kept HERE or a looping server would
    look like a busy one and be granted windows until the ceiling.
    """
    world = FakeWorld(boot_s=float("inf"), restart_every_s=60.0)
    message = _refusal(world)

    assert "never reported ready" in message
    assert "restarted" in message and "crash loop" in message
    assert len(world.windows) == 1, "four restarts inside the first window is already a loop"


def test_a_container_that_is_no_longer_running_is_named_as_such() -> None:
    world = FakeWorld(boot_s=float("inf"), status="exited", quiet_after_s=0.0)
    message = _refusal(world)

    assert "is not running" in message
    assert "exited" in message


def test_a_status_docker_would_not_answer_is_not_read_as_a_dead_container() -> None:
    """`ContainerState()` answers `""` for a read that failed; that is not "exited".

    The STATUS alone, which is what `_read_world()` keys on: this fake still
    reports a restart count, so the only thing saying "could not ask" is the
    empty status. `test_a_docker_that_stops_answering_is_not_reported_as_a_stuck_server`
    drives the whole reading `_world_output()` really produces for an unreadable
    daemon, where the count is `None` too.
    """
    world = FakeWorld(boot_s=float("inf"), status="", quiet_after_s=0.0)
    message = _refusal(world)

    assert "is not running" not in message
    assert "docker stopped answering" in message


def test_a_fatal_line_ends_the_wait_and_the_whole_line_is_quoted_back() -> None:
    """Not the pattern, and not just the part of the line the pattern happened to cover.

    `docker.wait_ready()` logs `match.group(0)`, which for this alternation is
    `Correct *.map files not found` -- the branch, with the server's own "in
    data directory" cut off. A user reading a refusal needs the sentence the
    server printed, so the match is widened to its line here.
    """
    world = FakeWorld(boot_s=float("inf"), fatal_after_s=0.0)
    markers = ReadyMarkers(
        world="Avg Diff:", fatal="Correct \\*.map files not found|Database .* not found", regex=True
    )
    installer = _installer(world)
    with pytest.raises(InstallerError) as caught:
        list(installer.wait_for_ready(_ctx(), markers))

    assert "Correct *.map files not found in data directory." in str(caught.value)
    assert "Database .* not found" not in str(caught.value), "the pattern, not the line"


def test_a_server_that_prints_forever_is_stopped_at_the_ceiling() -> None:
    """The outer bound, because a quiet budget alone can never end.

    The ceiling is not a load budget -- it is eight times the slowest first boot
    this project has measured -- and its sentence says so, because a user whose
    install stops here has been told the wrong thing if it reads like "too slow".
    """
    world = FakeWorld(boot_s=float("inf"))
    message = _refusal(world)

    assert world.elapsed == pytest.approx(native.READY_CEILING_SECONDS)
    assert "6 hours" in message
    assert "still printing" in message
    assert "stopped printing" not in message


# -- the numbers, and where they come from ----------------------------------


def test_every_window_is_the_catalogue_number_and_the_ceiling_is_a_whole_number_of_them() -> None:
    world = FakeWorld(boot_s=float("inf"))
    _refusal(world)

    assert set(world.windows) == {float(TBC_QUIET_S)}
    assert len(world.windows) == native.READY_CEILING_SECONDS // TBC_QUIET_S


def test_the_catalogue_still_carries_the_number_this_file_drives() -> None:
    """A pin, so an edit to `catalog.json` cannot quietly re-time every test above."""
    assert TBC.install.native is not None
    assert TBC.install.native.ready.timeout_s == TBC_QUIET_S
    assert TBC.install.native.ready.restart_loop == 4


def test_a_budget_larger_than_the_ceiling_buys_one_window_of_the_ceiling() -> None:
    """One window, not zero -- and the ceiling's length, not the catalogue's.

    Two rules meet here and both have to hold. A `timeout_s` above the ceiling
    must not floor the wait to zero windows, or a catalogue number would switch
    the wait off; and it must not raise the ceiling either, or `timeout_s` would
    be an escape hatch from the one bound that exists because `wait_ready()`
    takes no cancel. Until 2026-09-05 the second rule lost: this spent 12 hours
    for a 12-hour budget.
    """
    world = FakeWorld(boot_s=float("inf"))
    markers = ReadyMarkers(world="Avg Diff:", timeout_s=native.READY_CEILING_SECONDS * 2)
    with pytest.raises(InstallerError, match="never reported ready"):
        list(_installer(world).wait_for_ready(_ctx(), markers))
    assert world.windows == [float(native.READY_CEILING_SECONDS)]


# -- the seam behind it -----------------------------------------------------


def test_the_default_output_seam_reads_this_run_of_the_world_container() -> None:
    """Not wired anywhere but here, so the default IS the production path.

    Both halves matter. `this_run_only` is why a restarted server's OLD ready
    banner cannot be read as progress (docker.py, 2026-08-22), and asking
    `container_state` first is what makes the restart count and the log come
    from the same look at the machine.
    """
    asked: list[tuple[str, object]] = []

    def container_state(name: str, **_kwargs: object) -> docker.ContainerState:
        asked.append(("state", name))
        return docker.ContainerState("running", "2026-09-04T18:59:55Z", 3)

    def logs(name: str, *, this_run_only: bool = False, since: str = "", **_kw: object) -> str:
        asked.append(("logs", (name, this_run_only, since)))
        return "loading\n"

    original_state, original_logs = docker.container_state, docker._logs
    docker.container_state, docker._logs = container_state, logs  # type: ignore[assignment]
    try:
        out = native.Seams().world_output(ENTRY.container_spec())
    finally:
        docker.container_state, docker._logs = original_state, original_logs

    assert out == native.WorldOutput(text="loading\n", restarts=3, status="running")
    assert asked[0] == ("state", ENTRY.container_spec().world)
    assert asked[1] == ("logs", (ENTRY.container_spec().world, True, "2026-09-04T18:59:55Z"))


def test_a_log_that_will_not_read_under_a_state_that_will_is_still_a_reading() -> None:
    """The half of "docker would not answer" that is NOT unknown, and must not claim to be.

    `_logs()` returns `""` for a log it could not read AND for a container that
    has printed nothing yet, and docker offers nothing that tells those apart.
    So a readable state with an unreadable log is a real reading with an empty
    log -- it goes on to look like a silent container, which
    `wait_for_ready()` refuses after one quiet budget. Conservative in the only
    direction that matters: it never calls a dead server ready.

    The companion to
    `test_a_docker_that_would_not_answer_is_never_read_as_a_container_that_never_died`,
    which drives the case where the STATE is what could not be read. That one
    replaced a version of this test whose double raised `DockerCommandError`:
    nothing in `docker.py` raises it from these two calls, so the test was
    driving a branch production could not reach.
    """

    def unreadable_log(name: str, **_kwargs: object) -> str:
        return ""

    original_state, original_logs = docker.container_state, docker._logs
    docker.container_state = lambda name, **kw: docker.ContainerState("running", "T0", 2)  # type: ignore[assignment]
    docker._logs = unreadable_log  # type: ignore[assignment]
    try:
        out = native.Seams().world_output(ENTRY.container_spec())
    finally:
        docker.container_state, docker._logs = original_state, original_logs

    assert out == native.WorldOutput(text="", restarts=2, status="running")


def test_a_first_reading_the_daemon_refused_does_not_switch_off_the_crash_check() -> None:
    """The baseline is the first reading that HAS a count, not the first reading.

    A container is at its least inspectable in the seconds after `up`, which is
    exactly when the reading before the first window is taken. Latching `None`
    as the baseline would leave a crash loop unwatched for the whole wait, and
    nothing downstream would ever notice.

    The price is one window and it is asserted here rather than glossed: the
    baseline is adopted from the window that first answers, so the growth is
    seen at the window after it. A window later is not the same as never.
    """
    world = FakeWorld(boot_s=float("inf"), restart_every_s=60.0)
    readings = iter([native.WorldOutput(text="", restarts=None, status="")])

    def output(spec: docker.ContainerSpec, **_kwargs: object) -> native.WorldOutput:
        return next(readings, world.output(spec))

    message = _refusal(world, world_output=output)

    assert "restarted" in message and "crash loop" in message
    assert len(world.windows) == 2, "one window later than a readable first look, and no more"


def test_an_unreadable_restart_count_is_not_counted_towards_a_crash_loop() -> None:
    """`restarts=None` is "could not ask", and a guess in either direction is worse.

    What this reading gets SAID about it is
    `test_a_docker_that_stops_answering_is_not_reported_as_a_stuck_server`'s
    subject; this one is only about the count never being read as a number.
    """
    world = FakeWorld(boot_s=float("inf"), quiet_after_s=0.0)
    unknown = native.WorldOutput(text="", restarts=None, status="")
    message = _refusal(world, world_output=lambda spec, **_kwargs: unknown)

    assert "restarted" not in message
    assert "crash loop" not in message


# -- what the reviewer measured on m910q, 2026-09-05 ------------------------


def test_a_docker_that_would_not_answer_is_never_read_as_a_container_that_never_died() -> None:
    """`WorldOutput`'s docstring, driven against what `container_state()` REALLY does.

    That docstring promises "`restarts` is `None` for 'could not ask', never 0:
    0 is a container that has never died, and reading a failed `docker inspect`
    as that would let a crash loop run out the whole ceiling." The seam tried to
    deliver it with `except docker.DockerCommandError`, and neither
    `docker.container_state()` nor `docker._logs()` raises: the first returns a
    default `ContainerState()` on a non-zero inspect ("could not read the state
    of ..."), the second returns `""`. So the only branch that produced `None`
    was one nothing could reach, and every real failed inspect came back as the
    fabricated `restarts=0` the docstring forbids.
    """

    def refused(name: str, **_kwargs: object) -> docker.ContainerState:
        # Verbatim what `container_state()` returns when `docker inspect` exits
        # non-zero: no exception, a default-constructed state.
        return docker.ContainerState()

    original = docker.container_state
    docker.container_state = refused  # type: ignore[assignment]
    try:
        out = native.Seams().world_output(ENTRY.container_spec())
    finally:
        docker.container_state = original

    assert out.restarts is None, "a failed inspect is not a container that has never restarted"
    assert out == native.WorldOutput(text="", restarts=None, status="")


def test_a_crash_loop_is_named_a_loop_while_docker_is_restarting_the_container() -> None:
    """The one confusion this wait exists to prevent, arriving through the STATUS list.

    Every compose service here carries `restart: unless-stopped`, so a
    crash-looping world server spends most of its life reporting `restarting`
    rather than `running` (`docker.ContainerState.settled` says exactly this),
    and `docker.wait_ready()` answers False right after a restart -- which is
    when a window ends. With `restarting` outside the alive statuses it is read
    as "it is not running any more", the sentence for a container that has
    stopped for good, and the loop is never named.

    The restart pace is 1000 s against a 1800 s window, and that is the whole
    test. This test was written with 60 s, which puts the container thirty
    restarts past the threshold by the end of the FIRST window -- and
    `_read_world()` asks the crash-loop question before the status one, so it
    never reached the status at all and passed with `restarting` deleted from
    the list (whole file green, m910q 2026-09-05). At 1000 s the count grows by
    one and then three, under a threshold of four, so the first two windows are
    decided by the status and the third by the count. Drop `restarting` and the
    first window ends this with the stop sentence instead.
    """
    world = FakeWorld(boot_s=float("inf"), restart_every_s=1000.0, status="restarting")
    message = _refusal(world)

    assert "crash loop" in message and "restarted" in message
    assert "is not running any more" not in message
    assert len(world.windows) == 3, "two windows the status decided, and one the count did"


def test_a_crash_loop_caught_between_restarts_is_still_a_loop_and_not_a_stop() -> None:
    """And the ORDER of the two questions, which the status list alone does not settle.

    `restart: unless-stopped` cycles a failing container running -> exited ->
    restarting -> running, so an inspect can land on `exited` in the moment
    between the process dying and the policy restarting it -- and a window ends
    at exactly that moment, because that is when `wait_ready()` gave up. The
    restart COUNT is the stronger evidence of the two: it is cumulative, where
    the status is one sample. So the loop question is asked first, and a
    container that has died four times is called a crash loop even when the
    sample says `exited`.

    The narrow reading of this test: swap the two checks in `_read_world()` and
    it goes red while the whole rest of the suite stays green (m910q,
    2026-09-05), which is what makes the order a decision rather than an
    accident of how it was typed.
    """
    world = FakeWorld(boot_s=float("inf"), restart_every_s=60.0, status="exited")
    message = _refusal(world)

    assert "crash loop" in message
    assert (
        "is not running any more" not in message
    ), "a container docker will restart has not stopped"


def test_the_wait_reports_the_time_it_measured_and_not_the_windows_it_counted() -> None:
    """Every duration this printed was assumed off the window count, and windows end early.

    `docker.wait_ready()` returns False EARLY on three of its four paths, so a
    window handed 30 minutes can end in two -- and the note the user read said
    30 minutes anyway, because it was `(spent + 1) * timeout_s` rather than a
    clock. The same arithmetic bounded the wait: twelve windows was read as six
    hours, so a server whose windows ended early was given up on after 24
    minutes by a refusal that said six.
    """
    world = FakeWorld(boot_s=float("inf"), gives_up_after_s=120.0)
    lines: list[str] = []
    with pytest.raises(InstallerError) as caught:
        for line in _installer(world).stage_ready(_ctx()):
            lines.append(line)
    notes = [line for line in lines if "still printing" in line]

    assert "2 minutes" in notes[0], f"the window lasted 120s, not 1800s: {notes[0]!r}"
    assert world.elapsed == pytest.approx(
        native.READY_CEILING_SECONDS
    ), "the ceiling is six hours of wall clock, not twelve windows of any length"
    assert "6 hours" in str(caught.value)


def test_the_ready_stage_says_a_first_boot_can_take_many_minutes() -> None:
    """The announcement the ready stage lost, and no test noticed it going.

    `grep -rn "Waiting for the world server"` returned nothing after the quiet
    budget landed (reviewer, m910q 2026-09-05): the user saw "Waiting for the
    database." and then silence for up to a whole window. The database half
    still announces itself; the half that can take forty-six minutes did not.
    """
    lines = _ready(FakeWorld(boot_s=0.0))

    assert lines[0] == "Waiting for the database."
    assert "world server" in lines[1]
    assert "30 minutes" in lines[1], "the announcement names the budget it will apply"


def test_the_ceiling_is_not_overshot_by_a_budget_that_does_not_divide_it() -> None:
    """`READY_CEILING_SECONDS`' docstring says the wait never spends more. It did.

    Windows were counted with a rounding-UP division, so a `timeout_s` that does
    not divide six hours bought a whole extra window: 2500 -> 9 windows ->
    22500 seconds, fifteen minutes past a ceiling documented as the point where
    an install stops.
    """
    world = FakeWorld(boot_s=float("inf"))
    markers = ReadyMarkers(world="Avg Diff:", timeout_s=2500)
    with pytest.raises(InstallerError, match="never reported ready"):
        list(_installer(world).wait_for_ready(_ctx(), markers))

    assert world.elapsed <= native.READY_CEILING_SECONDS
    assert sum(world.windows) <= native.READY_CEILING_SECONDS


def test_the_restart_threshold_is_the_catalogue_number_and_not_one() -> None:
    """`markers.restart_loop` was READ and its VALUE never asserted.

    Mutating it to 1 left the whole suite green, because every crash-loop test
    above drives a container that has restarted far past any threshold by the
    end of its first window. This one restarts slowly enough that 4 and 1
    disagree about which window refuses and about the count in the sentence.
    """
    world = FakeWorld(boot_s=float("inf"), restart_every_s=700.0)
    markers = ReadyMarkers(world="Avg Diff:", timeout_s=TBC_QUIET_S, restart_loop=4)
    with pytest.raises(InstallerError) as caught:
        list(_installer(world).wait_for_ready(_ctx(), markers))

    assert "restarted 5 times" in str(caught.value)
    assert len(world.windows) == 2, "two restarts in the first window is not yet a loop of four"


def test_a_wait_shorter_than_a_minute_is_reported_in_seconds() -> None:
    """`_spell_seconds(30)` answered "0 minutes" -- true of nothing that happened.

    Reachable as soon as durations are measured rather than assumed: a window
    that ends on `docker.wait_ready()`'s missing-CLI grace lasts 30 seconds.
    """
    assert native._spell_seconds(30) == "30 seconds"
    assert native._spell_seconds(1) == "1 second"
    assert native._spell_seconds(90) == "2 minutes"
    assert native._spell_seconds(3600) == "1 hour"


# -- one number, one meaning ------------------------------------------------


def test_the_management_wait_gives_a_printing_server_another_window_too() -> None:
    """The spine's reading of `timeout_s`, in a form the four other waits can share.

    `wait_for_ready()` is a generator that yields progress and raises four
    different sentences; a controller polling an already-installed server wants
    a bool. What both need is the same reading of the catalogue number, so the
    decision lives in one function and the spine's loop is the one that dresses
    it in sentences.
    """
    world = FakeWorld(boot_s=TBC_BOOT_S)
    ready = docker.ReadySpec(world="Avg Diff:", timeout=float(TBC_QUIET_S))
    got = native.wait_ready_quietly(
        TBC.container_spec(),
        ready,
        wait=world.wait_ready,
        output=world.output,
        monotonic=world.clock,
    )

    assert got is True
    assert len(world.windows) == 2, "a 46-minute boot does not fit one 30-minute window"


def test_a_management_wait_that_cannot_see_the_container_spends_one_window() -> None:
    """An unreachable daemon must not turn a bounded wait into a six-hour poll.

    `world_output` answering `WorldOutput("", None, "")` is "docker would not
    talk", and two of those in a row is the "quiet" verdict -- so this ends
    after one window, which is exactly what the single-shot wait it replaces
    did. No branch of its own delivers that, and one was written and deleted:
    see `wait_ready_quietly`'s docstring for the mutation that showed the guard
    was inert everywhere except the case where it was wrong.
    """
    world = FakeWorld(boot_s=float("inf"))
    blind = native.WorldOutput(text="", restarts=None, status="")
    got = native.wait_ready_quietly(
        TBC.container_spec(),
        docker.ReadySpec(world="Avg Diff:", timeout=float(TBC_QUIET_S)),
        wait=world.wait_ready,
        output=lambda spec, **_kwargs: blind,
        monotonic=world.clock,
    )

    assert got is False
    assert len(world.windows) == 1


WAIT_FUNCTION_NAMES = ("wait_ready", "wait_server_ready")
"""What a ready wait is called in this package. The audit below finds them by walking."""

READY_WAIT_PRIMITIVE = "yulon/docker.py"
"""The one module allowed to spend a `ReadySpec.timeout` as a single total.

`docker.wait_ready()` IS the single window: it is what every quiet budget is
built out of, and `native.wait_ready_quietly()` calls it once per window. Every
other `wait_ready`/`wait_server_ready` in the package is a management wait and
must go through that function.
"""


def _wait_functions() -> dict[str, tuple[Path, int]]:
    """Every ready wait defined under `yulon/`, found by parsing rather than by memory.

    `f"{module}::{qualname}"` -> (file, line). The previous version of the test
    below carried its sites in a `parametrize` list of four names, and it was
    wrong the day it was written: `TortoiseController.wait_ready()` and
    `controller_wow_wotlk.docker_ctl.wait_server_ready()` were both still
    spending `timeout_s` as one fixed total, and a test whose docstring said
    "every ready wait in the app" could not see either of them. A list of names
    in a test is a claim about the tree; this reads the tree.
    """
    root = Path(__file__).resolve().parent.parent / "yulon"
    found: dict[str, tuple[Path, int]] = {}
    for path in sorted(root.rglob("*.py")):
        module = path.relative_to(root.parent).as_posix()
        if module == READY_WAIT_PRIMITIVE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for parent in ast.walk(tree):
            for node in ast.iter_child_nodes(parent):
                if isinstance(node, ast.FunctionDef) and node.name in WAIT_FUNCTION_NAMES:
                    owner = parent.name + "." if isinstance(parent, ast.ClassDef) else ""
                    found[f"{module}::{owner}{node.name}"] = (path, node.lineno)
    return found


def _drive_every_wait(server_dir: Path, distro: str) -> dict[str, Callable[[], bool]]:
    """One caller per site `_wait_functions()` finds, keyed the same way.

    Keyed rather than listed so the audit can assert the two sets are EQUAL: a
    game package added with its own `wait_server_ready()` fails here until
    somebody drives it, which is the failure the four-name parametrize could
    not produce.
    """
    from yulon.controller import Controller
    from yulon.controller_wow_tbc import controller as tbc_controller
    from yulon.controller_wow_tbc import docker_ctl as tbc_ctl
    from yulon.controller_wow_tortoise import controller as tortoise_controller
    from yulon.controller_wow_tortoise import docker_ctl as tortoise_ctl
    from yulon.controller_wow_vanilla import controller as vanilla_controller
    from yulon.controller_wow_vanilla import docker_ctl as vanilla_ctl
    from yulon.controller_wow_wotlk import docker_ctl as wotlk_ctl

    host, port = "127.0.0.1", 8085
    return {
        "yulon/controller.py::Controller.wait_ready": lambda: Controller(
            tbc_ctl.SPEC, server_dir, wsl_distro=distro
        ).wait_ready(host, port),
        "yulon/controller_wow_tbc/controller.py::TbcController.wait_ready": (
            lambda: tbc_controller.TbcController(server_dir, wsl_distro=distro).wait_ready()
        ),
        "yulon/controller_wow_tbc/docker_ctl.py::wait_server_ready": (
            lambda: tbc_ctl.wait_server_ready(wsl_distro=distro)
        ),
        "yulon/controller_wow_tortoise/controller.py::TortoiseController.wait_ready": (
            lambda: tortoise_controller.TortoiseController(
                server_dir, wsl_distro=distro
            ).wait_ready(host, port)
        ),
        "yulon/controller_wow_tortoise/docker_ctl.py::wait_server_ready": (
            lambda: tortoise_ctl.wait_server_ready(host, port, wsl_distro=distro)
        ),
        "yulon/controller_wow_vanilla/controller.py::VanillaController.wait_ready": (
            lambda: vanilla_controller.VanillaController(server_dir, wsl_distro=distro).wait_ready(
                host, port
            )
        ),
        "yulon/controller_wow_vanilla/docker_ctl.py::wait_server_ready": (
            lambda: vanilla_ctl.wait_server_ready(wsl_distro=distro)
        ),
        "yulon/controller_wow_wotlk/docker_ctl.py::wait_server_ready": (
            lambda: wotlk_ctl.wait_server_ready(host, port, wsl_distro=distro)
        ),
    }


def test_the_list_of_ready_waits_this_file_drives_is_the_list_the_package_defines(
    tmp_path: Path,
) -> None:
    """The enumeration itself, before anything is asserted ABOUT the sites.

    Two sites went a whole round unnoticed because the test that claimed to
    cover them named four of them in prose. This compares the tree against the
    drivers, so the next one cannot.
    """
    walked = set(_wait_functions())
    driven = set(_drive_every_wait(tmp_path, "dml-arch"))

    assert walked, "the walk found no ready wait at all, so it is asserting nothing"
    assert walked == driven, (
        f"ready waits with no driver: {sorted(walked - driven)}; "
        f"drivers for nothing: {sorted(driven - walked)}"
    )


def test_no_ready_wait_outside_the_primitive_spends_the_budget_as_one_total() -> None:
    """`docker.wait_ready_for()` called anywhere but by the quiet loop IS the bug.

    Asserted by parsing rather than by driving, because driving cannot see a
    site nobody thought to drive -- which is exactly how
    `TortoiseController.wait_ready()` and the WotLK `wait_server_ready()`
    survived the round that moved the other six.

    The audit is checked against itself as well: `native.py` must still name
    `docker.wait_ready_for`, or a rename would leave this scanning for a
    spelling nothing uses and passing on every tree there will ever be.
    """
    root = Path(__file__).resolve().parent.parent / "yulon"
    allowed = {READY_WAIT_PRIMITIVE, "yulon/catalog/native.py"}
    offenders: list[str] = []
    naming_it: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        module = path.relative_to(root.parent).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "wait_ready_for":
                naming_it.add(module)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "wait_ready_for"
                and module not in allowed
            ):
                offenders.append(f"{module}:{node.lineno}")

    assert "yulon/catalog/native.py" in naming_it, "the quiet loop no longer names the primitive"
    assert offenders == [], (
        "these spend the quiet budget as one fixed total, which is the reading the "
        f"2026-09-04 incident disproved: {offenders}"
    )


@pytest.mark.parametrize("site", sorted(_drive_every_wait(Path("/srv/wow"), "d")))
def test_every_ready_wait_in_the_app_spends_the_catalogue_number_the_same_way(
    site: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every site built a `ReadySpec` from `timeout_s` and waited it out ONCE, as a total.

    That is the same number the install spine spends as a quiet WINDOW. Two
    readings of one catalogue field is how the incident this lane exists for
    reached a user in the first place, so every site goes through the one
    function that holds the reading -- asserted by taking the single-shot road
    away, so a site that still walks it fails here rather than in a log.
    """
    seen: list[docker.ReadySpec] = []

    def quietly(spec: docker.ContainerSpec, ready: docker.ReadySpec, **_kwargs: object) -> bool:
        seen.append(ready)
        return True

    def refuse(*_args: object, **_kwargs: object) -> bool:
        raise AssertionError("a ready wait must not spend the quiet budget as one total")

    monkeypatch.setattr(native, "wait_ready_quietly", quietly)
    monkeypatch.setattr(docker, "wait_ready_for", refuse)

    assert _drive_every_wait(tmp_path, "dml-arch")[site]() is True
    assert len(seen) == 1


@pytest.mark.parametrize("site", sorted(_drive_every_wait(Path("/srv/wow"), "d")))
def test_a_ready_wait_asks_one_daemon_for_the_wait_the_state_and_the_log(
    site: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A WSL install's verdict was formed from a daemon the wait was never watching.

    `wait_ready_quietly()` forwarded `wsl_distro` to `docker.wait_ready_for()`
    and called `_world_output()` without it -- and `_world_output()` is where
    the container's state and log are read. On Windows a WSL install's
    containers exist only inside the distro, so those two reads went to Docker
    Desktop on the host and came back "no such object" for a container that was
    up and printing, which this wait reads as a daemon that would not talk and
    gives up on after one window. The opposite is available too: a stale host
    container of the same name answers, and a dead server is called alive.

    Three reads, one daemon, asserted together rather than one at a time,
    because the defect is a RELATIONSHIP between them and no read is wrong on
    its own. RED on m910q 2026-09-05, all eight sites at once:
    `{'state': None, 'logs': None, 'wait': 'dml-arch'}`.
    """
    asked: dict[str, object] = {}

    def wait(spec: docker.ContainerSpec, ready: docker.ReadySpec, **kwargs: object) -> bool:
        asked["wait"] = kwargs.get("wsl_distro")
        return True

    def state(container: str, **kwargs: object) -> docker.ContainerState:
        asked["state"] = kwargs.get("wsl_distro")
        return docker.ContainerState("running", "2026-09-05T00:00:00Z", 0)

    def logs(container: str, **kwargs: object) -> str:
        asked["logs"] = kwargs.get("wsl_distro")
        return "loading\n"

    monkeypatch.setattr(docker, "wait_ready_for", wait)
    monkeypatch.setattr(docker, "container_state", state)
    monkeypatch.setattr(docker, "_logs", logs)

    assert _drive_every_wait(tmp_path, "dml-arch")[site]() is True
    assert asked == {"wait": "dml-arch", "state": "dml-arch", "logs": "dml-arch"}


# -- the status list, enumerated ---------------------------------------------


@pytest.mark.parametrize("status", ["running", "restarting"])
def test_a_status_docker_reports_of_a_live_container_is_not_read_as_a_stop(status: str) -> None:
    """One test per entry in `native._ALIVE_STATUSES`, so dropping any of them goes red.

    Until 2026-09-05 not one of the seven statuses docker can report had a test.
    The file's only named status test drove a container that had restarted
    thirty times, and `_read_world()` asks the crash-loop question FIRST, so it
    never reached the status at all -- it passed for a different reason than its
    name, and dropping `restarting` from the tuple left the whole file green
    (m910q, 2026-09-05). Here the container has never restarted, so the status
    is the only question left that can end the wait.

    The third alive status, `""`, is owned by
    `test_a_docker_that_stops_answering_is_not_reported_as_a_stuck_server`,
    which drives it in the only shape `_world_output()` can actually produce it
    in: an unreadable state carries no restart count either.
    """
    world = FakeWorld(boot_s=float("inf"), quiet_after_s=0.0, status=status)
    message = _refusal(world)

    assert "is not running any more" not in message, f"{status!r} is not a container that stopped"
    assert "stopped printing" in message


@pytest.mark.parametrize("status", ["created", "exited", "paused", "dead", "removing"])
def test_a_status_that_means_nothing_will_print_is_named_a_stop(status: str) -> None:
    """And the other half of the list: every status docker has that is NOT alive.

    Docker's set is `created`, `running`, `restarting`, `exited`, `paused`,
    `dead` and `removing`; the two tests either side of this line name all
    seven, so adding one to `_ALIVE_STATUSES` costs a red test rather than a
    six-hour wait on a container that is never going to print again.

    This container prints all the way through and has never restarted, so
    neither the crash-loop question nor the quiet one can answer for it: the
    status is what ends the wait, at the first window, and the sentence quotes
    what docker actually said.
    """
    world = FakeWorld(boot_s=float("inf"), status=status)
    message = _refusal(world)

    assert "is not running any more" in message
    assert repr(status) in message, "the refusal quotes docker's own word for it"
    assert len(world.windows) == 1, "nothing is going to print, so nothing waits for it to"


# -- what the round-3 review measured on m910q, 2026-09-05 -------------------


def test_a_docker_that_stops_answering_is_not_reported_as_a_stuck_server() -> None:
    """The daemon died mid-wait, and the refusal said the SERVER had stopped printing.

    `_world_output()` answers `WorldOutput("", None, "")` when `container_state()`
    could not read the container at all, and two identical readings of that were
    the `quiet` verdict -- "it stopped printing anything at all ... so this one
    is stuck rather than slow". Nothing here had read the log; nothing here knew
    anything about the server. It also contradicted the announcement printed
    moments earlier, which says this wait is watching what the server prints,
    and it sent the user to `docker compose logs` -- the one command that cannot
    work when docker is the thing that is down.

    The `""` entry in `native._ALIVE_STATUSES` is owned here: take it out and
    this arrives as `gone`, and the user is told a running container has stopped
    because the daemon in front of it did.
    """
    world = FakeWorld(boot_s=float("inf"))
    blind = native.WorldOutput(text="", restarts=None, status="")
    message = _refusal(world, world_output=lambda spec, **_kwargs: blind)

    assert "docker stopped answering" in message
    assert "stuck rather than slow" not in message
    assert "stopped printing" not in message
    assert "is not running any more" not in message, "an unreadable state is not a stopped one"


def test_a_measured_duration_that_rounds_to_zero_is_not_reported_as_zero() -> None:
    """`_spell_seconds(0)` answered "0 seconds", which is its own docstring's complaint.

    That docstring condemns `"0 minutes"` because it is "true of nothing that
    ever happened", and every duration this function is handed is one the wait
    MEASURED -- so it happened, so zero is never the honest word for it. A
    window can end in effectively no time: `docker.wait_ready()` returns False
    on its first poll when the log already holds a `fatal` line, and the two
    `monotonic` reads either side of that can land in one clock tick.
    """
    assert native._spell_seconds(0) == "less than a second"
    assert native._spell_seconds(0.4) == "less than a second"
    assert native._spell_seconds(1) == "1 second"


# -- two ceilings, because there are two kinds of wait -----------------------


def test_a_management_wait_is_bounded_by_the_timeout_its_caller_handed_it() -> None:
    """A Stop/Start button could block for six hours, and nothing tested the change.

    The four management waits used to spend `timeout` ONCE, as a total, so the
    call was bounded at 480 s, 1800 s or 3600 s depending on which of them it
    was. Reading the number as a quiet budget was right; taking the INSTALL
    ceiling with it was not, and it went from 480-3600 s to 21600 s with no test
    naming the change. `timeout=` bounds the call again, at
    `MANAGEMENT_CEILING_WINDOWS` times itself -- still strictly longer than the
    single-shot total it replaced, so no wait that used to succeed is cut short.
    """
    world = FakeWorld(boot_s=float("inf"))
    got = native.wait_ready_quietly(
        TBC.container_spec(),
        docker.ReadySpec(world="Avg Diff:", timeout=float(TBC_QUIET_S)),
        wait=world.wait_ready,
        output=world.output,
        monotonic=world.clock,
    )

    assert got is False
    assert len(world.windows) == native.MANAGEMENT_CEILING_WINDOWS
    assert world.elapsed == pytest.approx(TBC_QUIET_S * native.MANAGEMENT_CEILING_WINDOWS)
    assert world.elapsed > TBC_QUIET_S, "one window is the single-shot total this replaced"


def test_the_install_wait_and_a_management_wait_stop_in_different_places() -> None:
    """Same catalogue number, same talkative server, two different ceilings -- on purpose.

    An install is a long operation the user started knowing it was long and
    which streams progress the whole way; six hours is there only so a server
    printing rubbish for ever cannot hang it. A management wait is behind a
    button the user just pressed and is looking at. Collapsing the two was the
    regression; `MANAGEMENT_CEILING_WINDOWS` carries the argument for the size.
    """
    managed = FakeWorld(boot_s=float("inf"))
    assert (
        native.wait_ready_quietly(
            TBC.container_spec(),
            docker.ReadySpec(world="Avg Diff:", timeout=float(TBC_QUIET_S)),
            wait=managed.wait_ready,
            output=managed.output,
            monotonic=managed.clock,
        )
        is False
    )

    installing = FakeWorld(boot_s=float("inf"))
    with pytest.raises(InstallerError, match="never reported ready"):
        list(
            _installer(installing).wait_for_ready(
                _ctx(), ReadyMarkers(world="Avg Diff:", timeout_s=TBC_QUIET_S)
            )
        )

    assert installing.elapsed == pytest.approx(native.READY_CEILING_SECONDS)
    assert managed.elapsed == pytest.approx(TBC_QUIET_S * native.MANAGEMENT_CEILING_WINDOWS)
    assert managed.elapsed < installing.elapsed


def test_a_quiet_budget_larger_than_the_install_ceiling_does_not_raise_it() -> None:
    """`management_ceiling()` is a multiple of a catalogue number, so it needs a cap.

    Nothing in `catalog.json` is near this today -- the largest `timeout_s` is
    Tortoise's 3600, read 2026-09-05, which buys four hours -- and that is
    exactly when a bound costs nothing to add.
    """
    assert native.management_ceiling(float(TBC_QUIET_S)) == pytest.approx(
        TBC_QUIET_S * native.MANAGEMENT_CEILING_WINDOWS
    )
    assert native.management_ceiling(float(native.READY_CEILING_SECONDS)) == pytest.approx(
        native.READY_CEILING_SECONDS
    )
