"""The `ready` stage's budget: what "it never came up" is allowed to mean.

The incident this file exists for, measured on yulon-win11-gate 2026-09-04 from
the world server's OWN timestamps (`docker logs -t`), Docker Desktop, the server
directory on a 9p share reading at about 1.4 MB/s:

    Vanilla  06:12:43Z mangosd start -> 06:37:22Z first `Avg Diff:`  = 1479 s
    TBC      18:59:55Z mangosd start -> 19:45:58Z first `Avg Diff:`  = 2763 s

(24.6 and 46.0 minutes, which is how the gate write-up rounded them; the
seconds below are always computed from the stamps, never from the minutes.)

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
import math
import re
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


def _stamps_apart(start: str, end: str) -> int:
    """Seconds between two `HH:MM:SS` stamps, wrapping once over midnight.

    The stamps are the measurement; the seconds are arithmetic on them, done
    here rather than typed. `native.py` carried 1476 and 2760 for the two
    `docker logs -t` boots until 2026-09-05 -- 24.6 * 60 and 46.0 * 60, the
    write-up's rounded MINUTES multiplied back out, three seconds adrift of the
    stamps printed on the same line -- and `TBC_BOOT_S` below was spelled
    `46 * 60` for the same reason. Nothing downstream was wrong (the floor takes
    the largest of the three, which is Tortoise's) and nothing said so either,
    which is the failure this removes: a citation its own numbers do not support.
    """
    begin = [int(part) for part in start.split(":")]
    finish = [int(part) for part in end.split(":")]
    spanned = (finish[0] - begin[0]) * 3600 + (finish[1] - begin[1]) * 60 + finish[2] - begin[2]
    return spanned + (24 * 3600 if spanned < 0 else 0)


TBC_QUIET_S = 1800
"""What `catalog.json` gives both CMaNGOS entries. Pinned by `test_the_catalogue...` below."""

TBC_BOOT_S = _stamps_apart("18:59:55", "19:45:58")
"""The measured 9p first boot the old budget called a failure (yulon-win11-gate, 2026-09-04).

2763 s, from the two `docker logs -t` stamps in this file's header."""


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
                # A ready wait does not have to be a `def`. Three packages
                # carried `wait_ready = docker.wait_ready` at module level until
                # 2026-09-05 -- the single-shot primitive, re-exported under the
                # package's public name, in a block whose comment says "callers
                # import from here" -- and this walk could not see one of them,
                # because a binding is an `ast.Assign` and it matched only
                # definitions. MODULE level and not class level: `Seams` in
                # `native.py` declares a `wait_ready` FIELD whose default is the
                # primitive, which is correct there and is not a call site.
                if isinstance(parent, ast.Module) and isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Name) and target.id in WAIT_FUNCTION_NAMES:
                            found[f"{module}::{target.id} (module-level binding)"] = (
                                path,
                                node.lineno,
                            )
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
    single_shot = ("wait_ready_for", "wait_ready")
    offenders: list[str] = []
    naming_it: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        module = path.relative_to(root.parent).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "wait_ready_for":
                naming_it.add(module)
            # `docker.wait_ready` as well as `docker.wait_ready_for`, and the
            # ATTRIBUTE rather than only a call of it: the three aliases retired
            # on 2026-09-05 never called anything, they bound the primitive to a
            # public name and waited for a caller. `docker.` in front is
            # required so that `self._seams.wait_ready(...)` -- the install
            # spine's own seam, which is allowed to be the single window -- is
            # not read as one of these.
            if (
                isinstance(node, ast.Attribute)
                and node.attr in single_shot
                and isinstance(node.value, ast.Name)
                and node.value.id == "docker"
                and module not in allowed
            ):
                offenders.append(f"{module}:{node.lineno} (docker.{node.attr})")

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

    TWO WINDOWS, and that is the whole shape of this test. `wait_ready_quietly()`
    looks at the container twice — once before the loop and once after every
    window that did not find the banner — and the SECOND look is the one whose
    reading forms the verdict. The first version of this test drove a `wait`
    double that answered True on its first window, so the second `look()` was
    never reached: mutating it to `look(spec)` left this file at 57 passed and
    the whole suite green apart from one unrelated test the docker guard caught
    (review, m910q 2026-09-05). With the mutation and two windows all eight
    sites go red on `{'state': ['dml-arch', None], ...}` — the behaviour being
    restored is the blocker itself, a WSL install whose window 2 onward reads
    the host daemon, comes back empty, and ends `unreadable` on a healthy
    printing container.
    """
    asked: dict[str, list[object]] = {"wait": [], "state": [], "logs": []}
    printed = ["loading\n", "loading\nloaded the maps\n"]

    def wait(spec: docker.ContainerSpec, ready: docker.ReadySpec, **kwargs: object) -> bool:
        asked["wait"].append(kwargs.get("wsl_distro"))
        # False, then True: the second `look()` only happens after a window that
        # did NOT find the banner, so a double that answers True at once records
        # one read and leaves the other unwatched.
        return len(asked["wait"]) > 1

    def state(container: str, **kwargs: object) -> docker.ContainerState:
        asked["state"].append(kwargs.get("wsl_distro"))
        return docker.ContainerState("running", "2026-09-05T00:00:00Z", 0)

    def logs(container: str, **kwargs: object) -> str:
        # Fresh output each time, so the reading between the two windows is
        # `alive` and the wait goes round again instead of ending as quiet.
        asked["logs"].append(kwargs.get("wsl_distro"))
        return printed[min(len(asked["logs"]) - 1, len(printed) - 1)]

    monkeypatch.setattr(docker, "wait_ready_for", wait)
    monkeypatch.setattr(docker, "container_state", state)
    monkeypatch.setattr(docker, "_logs", logs)

    assert _drive_every_wait(tmp_path, "dml-arch")[site]() is True
    assert asked == {
        "wait": ["dml-arch", "dml-arch"],
        "state": ["dml-arch", "dml-arch"],
        "logs": ["dml-arch", "dml-arch"],
    }, "every read this wait makes goes to the daemon it was told to watch, EVERY time"


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


def _tortoise_stage_line() -> re.Match[str]:
    """The 9p ready-stage wall, re-derived from the gate's own write-up rather than retyped.

    `native.SLOWEST_MEASURED_FIRST_BOOT_SECONDS` is a measurement, and a
    measurement typed into a source file is a number somebody chose. This reads
    `pyplan/gates/7.7-win11-tortoise/README.md` -- the evidence copied off
    `yulon-win11-gate` on 2026-09-05 while the containers were still up -- and
    checks the stated seconds against the two clock stamps beside them, so the
    artefact has to be self-consistent before this file will believe it.

    That makes the ceiling constant OWNED by a file on disk: editing the number
    in `native.py` without the run that justifies it goes red here.
    """
    readme = (
        Path(__file__).resolve().parents[2]
        / "pyplan"
        / "gates"
        / "7.7-win11-tortoise"
        / "README.md"
    )
    assert readme.is_file(), f"{readme} is the evidence the management floor is derived from"
    found = re.search(
        r"Ready stage wall: (\d\d):(\d\d):(\d\d)\D+(\d\d):(\d\d):(\d\d) = \*\*(\d+) s\*\*",
        readme.read_text(encoding="utf-8"),
    )
    assert found is not None, (
        f"{readme} no longer states its ready stage as `Ready stage wall: HH:MM:SS -> "
        "HH:MM:SS = **N s**`, so this file cannot re-derive the number it bounds waits with"
    )
    return found


def _tortoise_stage_wall_from_the_gate() -> int:
    """The seconds that write-up states. Cross-checked against its own stamps by a test.

    Split from the parse so that only ONE test dies if the artefact and its
    arithmetic disagree. This runs at import time -- `parametrize` needs the
    number at collection -- and an assertion here takes the whole file down with
    a collection error, which is the right noise for a MISSING evidence file and
    much too much for a mistyped one.
    """
    return int(_tortoise_stage_line()[7])


MEASURED_9P_BOOTS: dict[str, int] = {
    # yulon-win11-gate 2026-09-04, `docker logs -t`, mangosd start -> first `Avg
    # Diff:`. The two stamps are in this file's own header and in
    # `pyplan/checklist.md`'s 7.7 block; the seconds are computed from them.
    "wow-vanilla, 06:12:43Z -> 06:37:22Z": _stamps_apart("06:12:43", "06:37:22"),
    "wow-tbc, 18:59:55Z -> 19:45:58Z": _stamps_apart("18:59:55", "19:45:58"),
    # yulon-win11-gate 2026-09-05, the ready STAGE's wall clock, which is what a
    # wait actually sits through. Read off the gate's own write-up.
    "wow-tortoise, ready-stage wall": _tortoise_stage_wall_from_the_gate(),
}
"""Every first boot this project has timed on Docker Desktop's 9p share. All healthy.

Every one printed the whole way and ended `RestartCount=0`. They are the
evidence under `native.MEASURED_9P_FIRST_BOOTS_SECONDS`, and the reason a
management ceiling shorter than the largest of them is a bug rather than a
policy: it refuses a server that was about to succeed, which is the 2026-09-04
verdict this whole file exists to remove.

Not one of the three seconds figures is typed here: two are computed from their
stamps, the third is read out of the gate's README.
"""


def _budgets_in_use() -> dict[str, float]:
    """Every quiet budget a management wait is handed today, asked of the real spec builders.

    Not read off `catalog.json`: two of the eight sites do not use it.
    `Controller.wait_ready()` and `controller_wow_wotlk.docker_ctl` build
    `docker.azerothcore_ready()`, whose timeout is `ReadySpec`'s own 480 s
    default -- the smallest budget in the app, and the one the four-window
    ceiling of 2026-09-05 bounded at 1920 s, shorter than all three boots above.
    """
    from yulon.controller_wow_tbc import docker_ctl as tbc_ctl
    from yulon.controller_wow_tortoise import docker_ctl as tortoise_ctl
    from yulon.controller_wow_vanilla import docker_ctl as vanilla_ctl

    return {
        "Controller.wait_ready / wow-wotlk (azerothcore_ready default)": (
            docker.azerothcore_ready("127.0.0.1", 3724).timeout
        ),
        "wow-tbc docker_ctl.ready_spec()": tbc_ctl.ready_spec().timeout,
        "wow-vanilla docker_ctl.ready_spec()": vanilla_ctl.ready_spec().timeout,
        "wow-tortoise docker_ctl.ready_spec()": tortoise_ctl.ready_spec("127.0.0.1", 3724).timeout,
    }


BUDGETS_IN_USE = _budgets_in_use()


def test_the_gate_write_up_the_floor_is_read_from_agrees_with_its_own_clock() -> None:
    """The artefact has to be self-consistent before this file believes a number in it.

    `pyplan/gates/7.7-win11-tortoise/README.md` states the ready stage as
    `23:41:37 -> 00:43:19 = **3702 s**`, and the arithmetic is what makes that a
    measurement rather than a figure: the two stamps are copied out of the
    installer's own transcript. Checked here so that editing the seconds without
    the run behind them fails HERE, next to the evidence, as well as in
    `native.py`'s constant.

    An incomplete artefact reads as fact -- so this asks the file, and then asks
    the file to agree with itself.
    """
    found = _tortoise_stage_line()
    start = int(found[1]) * 3600 + int(found[2]) * 60 + int(found[3])
    end = int(found[4]) * 3600 + int(found[5]) * 60 + int(found[6])
    spanned = end - start + (24 * 3600 if end < start else 0)

    assert spanned == int(found[7]), (
        f"the 7.7 write-up states {found[7]}s of ready stage and its own stamps span "
        f"{spanned}s; one of the two is wrong and the management floor is read off it"
    )


def test_the_boots_this_file_bounds_waits_with_are_the_difference_between_their_own_stamps() -> (
    None
):
    """`native.py`'s three seconds figures, each checked against the citation beside it.

    Two are `docker logs -t` stamps and the third is the 7.7 gate's README. All
    three are recomputed here rather than compared to a second typed copy --
    until 2026-09-05 `native.py` printed 1476 and 2760 against stamps that span
    1479 and 2763, because somebody multiplied the write-up's rounded minutes
    (24.6, 46.0) back out by 60. Nothing downstream was wrong (the floor takes
    the largest, which is Tortoise's) and nothing said so either.
    """
    assert sorted(native.MEASURED_9P_FIRST_BOOTS_SECONDS) == sorted(MEASURED_9P_BOOTS.values()), (
        f"native.py bounds waits with {native.MEASURED_9P_FIRST_BOOTS_SECONDS} and the stamps "
        f"and artefacts they cite give {sorted(MEASURED_9P_BOOTS.values())}"
    )
    assert _stamps_apart("23:59:00", "00:01:00") == 120, "a stage that crosses midnight is 2 min"


def test_the_management_floor_stands_clear_of_the_slowest_boot_by_a_measured_margin() -> None:
    """The floor is the slowest boot WIDENED, and both halves are derived.

    Round 3 left the ceiling owned by nothing: measured on m910q 2026-09-05,
    every value of `MANAGEMENT_CEILING_WINDOWS` from 2 to 11 kept all 2593 tests
    green. Round 4 fixed that by reading the floor off the evidence -- and put
    it exactly ON the largest sample, where a review measured the consequence in
    one line: through the real `wait_ready_quietly()` at the 480 s callers, a
    3702 s boot was accepted and a 3703 s boot was refused at elapsed 3702 s.

    So the floor now carries a margin, and the margin is the widest gap the
    three measurements actually exhibit (2763 / 1479 = 1.868), applied once
    above the slowest of them. Recomputed here from `MEASURED_9P_BOOTS`, whose
    Tortoise entry comes out of the gate's own write-up, so neither the
    measurement nor the multiplier is a number typed in two places.
    """
    boots = sorted(MEASURED_9P_BOOTS.values())
    widest_gap = max(slower / faster for faster, slower in zip(boots, boots[1:], strict=False))

    assert native.MANAGEMENT_FLOOR_MARGIN == pytest.approx(widest_gap), (
        f"the floor is widened by {native.MANAGEMENT_FLOOR_MARGIN:.4f} and the widest gap "
        f"between two boots this project has measured is {widest_gap:.4f} ({boots})"
    )
    assert native.MANAGEMENT_FLOOR_SECONDS == math.ceil(boots[-1] * widest_gap), (
        f"the floor is {native.MANAGEMENT_FLOOR_SECONDS}s and the slowest measured boot "
        f"widened by that margin is {math.ceil(boots[-1] * widest_gap)}s"
    )
    assert native.MANAGEMENT_FLOOR_SECONDS > native.SLOWEST_MEASURED_FIRST_BOOT_SECONDS, (
        "a floor sitting exactly on the slowest sample refuses the first healthy boot one "
        "second slower than the one boot anybody happened to time"
    )
    assert native.MANAGEMENT_FLOOR_SECONDS < native.READY_CEILING_SECONDS, (
        "a floor at or above the install ceiling would give every management wait the "
        "install's six hours, which is the regression the two ceilings exist to undo"
    )


def test_a_boot_slower_than_every_one_measured_is_still_inside_the_margin() -> None:
    """The margin, driven through the real wait rather than compared to a constant.

    The smallest budget any caller hands one is 480 s
    (`docker.azerothcore_ready()`'s default, used by `Controller.wait_ready()`
    and `controller_wow_wotlk`), so it is the site with the least room and the
    one this asserts against. Both boots below were refused there on 2026-09-05
    before the margin existed, measured on m910q at elapsed 3702 s.
    """
    smallest = min(BUDGETS_IN_USE.values())
    for boot in (native.SLOWEST_MEASURED_FIRST_BOOT_SECONDS + 1, native.MANAGEMENT_FLOOR_SECONDS):
        world = FakeWorld(boot_s=float(boot))

        got = native.wait_ready_quietly(
            TBC.container_spec(),
            docker.ReadySpec(world="Avg Diff:", timeout=smallest),
            wait=world.wait_ready,
            output=world.output,
            monotonic=world.clock,
        )

        assert got is True, (
            f"a {boot}s boot was refused by the ceiling a {smallest:.0f}s budget buys "
            f"({native.management_ceiling(smallest):.0f}s), and the slowest boot this project "
            f"has measured is {native.SLOWEST_MEASURED_FIRST_BOOT_SECONDS}s -- so the margin "
            "over it is not being spent"
        )


@pytest.mark.parametrize("budget", sorted(BUDGETS_IN_USE.items()))
@pytest.mark.parametrize("boot", sorted(MEASURED_9P_BOOTS.items()))
def test_no_boot_this_project_has_measured_is_refused_by_a_management_ceiling(
    boot: tuple[str, int], budget: tuple[str, float]
) -> None:
    """Twelve waits: every measured boot against every budget a caller hands one.

    This is the assertion the ceiling exists to survive, and it is driven
    through the real `wait_ready_quietly()` rather than compared against
    `management_ceiling()` -- the ceiling is spent by a LOOP, whose last window
    is shortened to land on it, and a wait that stops one second early refuses a
    healthy server exactly as a wall-clock timeout did.

    It was RED before this round for two of the twelve: at four windows the
    480 s callers stopped at 1920 s, and the TBC and Tortoise boots are 2763 s
    and 3702 s. Those two sites are `Controller.wait_ready()` and
    `controller_wow_wotlk.docker_ctl.wait_server_ready()`, which is to say the
    2026-09-04 defect was still live at two of the app's eight ready waits after
    the round that removed it from the other six.
    """
    world = FakeWorld(boot_s=float(boot[1]))

    got = native.wait_ready_quietly(
        TBC.container_spec(),
        docker.ReadySpec(world="Avg Diff:", timeout=budget[1]),
        wait=world.wait_ready,
        output=world.output,
        monotonic=world.clock,
    )

    assert got is True, (
        f"a {boot[1]}s boot ({boot[0]}) was refused by the ceiling a {budget[1]:.0f}s budget "
        f"buys ({budget[0]}, ceiling {native.management_ceiling(budget[1]):.0f}s) -- and that "
        "boot happened, on a healthy server, with RestartCount=0"
    )


def test_the_management_ceiling_is_the_floor_or_the_cap_for_every_budget_shipped() -> None:
    """`MANAGEMENT_CEILING_WINDOWS` decides no shipped ceiling today, and this says so.

    The multiple only answers for a budget between 3458 s and 10800 s, and no
    caller hands one: below that the derived floor wins, at Tortoise's 10800 s
    the install cap does. So 2 and 3 are indistinguishable on this tree (m910q
    2026-09-05, whole module per value: 1 red, 2 green, 3 green, 4 red, 5 red,
    11 red), which is why the constant's docstring claims only "more than one"
    -- a docstring arguing for a specific multiple would be a reason with
    nothing behind it. The green band was 2 alone until the floor was widened
    the same day; 1800 * 3 = 5400 s used to be above the floor and now is not.
    (The band's lower end is `MANAGEMENT_FLOOR_SECONDS / 2` and moves with it:
    it read 1851 s while the floor sat on the slowest sample, 3458 s since the
    margin was added on 2026-09-05.)

    The audit is written to FAIL on what it cannot see: the day an entry lands
    in that band, this test goes red and the multiple has to be owned by
    somebody with a measurement, instead of quietly starting to decide a
    ceiling.
    """
    floor = float(native.MANAGEMENT_FLOOR_SECONDS)
    cap = float(native.READY_CEILING_SECONDS)
    decided_by_the_multiple = {
        who: budget
        for who, budget in BUDGETS_IN_USE.items()
        if native.management_ceiling(budget) not in (floor, cap)
    }

    assert decided_by_the_multiple == {}, (
        f"{decided_by_the_multiple} now get a ceiling of "
        f"timeout * {native.MANAGEMENT_CEILING_WINDOWS}, a number nothing on this project has "
        "measured. Either derive the multiple or bound these callers another way"
    )
    assert sorted({native.management_ceiling(b) for b in BUDGETS_IN_USE.values()}) == [floor, cap]
    assert native.management_ceiling(
        BUDGETS_IN_USE["wow-tortoise docker_ctl.ready_spec()"]
    ) == pytest.approx(cap), (
        "Tortoise's timeout_s was widened to 10800 on 2026-09-05 (eb5f3b3f), and at that size "
        "its management ceiling IS the install ceiling -- recorded here rather than left to be "
        "rediscovered, because it is the one budget where the two waits cannot be separated"
    )


def test_a_management_wait_spends_more_than_one_window_of_every_budget_in_use() -> None:
    """One window is the fixed total this lane replaced, whatever the budget is.

    `MANAGEMENT_CEILING_WINDOWS = 1` is red here and only here: Tortoise's
    10800 s budget is the one large enough that the multiple, not the floor,
    decides how many windows it buys, so at 1 that site goes straight back to
    spending `timeout_s` once -- which is the bug, for the entry that has the
    most of it to spend.
    """
    for who, budget in BUDGETS_IN_USE.items():
        world = FakeWorld(boot_s=float("inf"))

        got = native.wait_ready_quietly(
            TBC.container_spec(),
            docker.ReadySpec(world="Avg Diff:", timeout=budget),
            wait=world.wait_ready,
            output=world.output,
            monotonic=world.clock,
        )

        assert got is False
        assert len(world.windows) >= 2, (
            f"{who} spends its {budget:.0f}s budget {len(world.windows)} time(s), which is the "
            "single-shot total the 2026-09-04 incident disproved"
        )


def test_a_management_wait_is_bounded_by_the_ceiling_and_shortens_its_last_window() -> None:
    """A management wait could block for six hours, and nothing tested the change.

    The four management waits used to spend `timeout` ONCE, as a total, so the
    call was bounded at 480 s, 1800 s or 10800 s depending on which it was.
    Reading the number as a quiet budget was right; taking the INSTALL ceiling
    with it was not, and it went to 21600 s with no test naming the change. (An
    earlier version of this line said a Stop/Start BUTTON could block for six
    hours. It could not: `yulon/ui/controller_view.py:998` and `:1006` call
    `controller.start` and `.stop`, neither of which waits. The only code that
    runs a management wait is `pyplan/gates/gate-79-controller-surface.py`,
    three times a run -- see `native.MANAGEMENT_CEILING_WINDOWS`, which had the
    claim removed on 2026-09-05 while this docstring kept it.)

    The last window is the second half of this. `management_ceiling()` is no
    longer a whole number of budgets -- 6916 is not a multiple of 1800 -- so the
    `min(ready.timeout, ceiling - spent)` term is what makes the wait stop AT
    the ceiling instead of overshooting it by most of a window. Until 2026-09-05
    the shortening could not fire for any real caller and no test drove it.

    Two mutations of that term, both run on m910q 2026-09-05:
    `window = ready.timeout if (clock() - started) < ceiling else 0.0` gives
    four whole windows and 7200 s (`2 failed, 75 passed`, this test on
    `assert 7200.0 == 6916.0` and the two-ceilings test with it). A plain
    `window = ready.timeout` does not overshoot -- it never returns:
    `if window <= 0` is the loop's ONLY exit that does not depend on the server,
    so against a server that keeps printing the wait runs for ever, and
    `test_a_management_wait_spends_more_than_one_window_of_every_budget_in_use`
    hangs (killed at 45 s, verified as the test in flight with `-x -v`). The
    round that added the term wrote the first result down for the second
    mutation; they are not the same edit and only one of them is a bounded wait.
    """
    world = FakeWorld(boot_s=float("inf"))

    got = native.wait_ready_quietly(
        TBC.container_spec(),
        docker.ReadySpec(world="Avg Diff:", timeout=float(TBC_QUIET_S)),
        wait=world.wait_ready,
        output=world.output,
        monotonic=world.clock,
    )
    ceiling = native.management_ceiling(float(TBC_QUIET_S))

    assert got is False
    assert world.elapsed == pytest.approx(ceiling)
    assert world.elapsed > TBC_QUIET_S, "one window is the single-shot total this replaced"
    assert world.elapsed < native.READY_CEILING_SECONDS, "the install ceiling is not this one"
    assert world.windows[:-1] == [float(TBC_QUIET_S)] * (len(world.windows) - 1)
    assert 0 < world.windows[-1] < TBC_QUIET_S, (
        f"the last window is {world.windows[-1]}s; it must be cut short so the wait lands on "
        f"the {ceiling}s ceiling rather than overshooting it"
    )


def test_the_install_wait_and_a_management_wait_stop_in_different_places() -> None:
    """Same catalogue number, same talkative server, two different ceilings -- on purpose.

    An install is a long operation the user started knowing it was long and
    which streams progress the whole way; six hours is there only so a server
    printing rubbish for ever cannot hang it. A management wait is one something
    is waiting on -- today the only code that runs one is the 7.9 controller
    gate, three times a run. Collapsing the two was the regression;
    `MANAGEMENT_CEILING_WINDOWS` and `MANAGEMENT_FLOOR_SECONDS` carry
    the argument for the size between them.
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
    assert managed.elapsed == pytest.approx(native.management_ceiling(float(TBC_QUIET_S)))
    assert managed.elapsed < installing.elapsed


def test_a_quiet_budget_larger_than_the_install_ceiling_does_not_raise_it() -> None:
    """`management_ceiling()` is a multiple of a catalogue number, so it needs a cap.

    The largest `timeout_s` shipped is Tortoise's 10800, read 2026-09-05, which
    already lands exactly on the cap; a hypothetical six-hour budget must not
    buy a twelve-hour poll behind a button.
    """
    assert native.management_ceiling(float(native.READY_CEILING_SECONDS)) == pytest.approx(
        native.READY_CEILING_SECONDS
    )
    assert native.management_ceiling(1.0) == pytest.approx(native.MANAGEMENT_FLOOR_SECONDS), (
        "a caller may not ask to be bounded below the slowest boot anyone here has measured, "
        "widened by the margin over it"
    )


# -- what a window that did not find the banner is allowed to mean -----------


def test_a_first_look_the_daemon_refused_does_not_switch_the_crash_check_off_here_either() -> None:
    """The management loop's copy of the baseline rule, which nothing owned.

    `_restart_baseline()` keeps the first reading that HAS a restart count, not
    the first reading. Both loops need it, and until 2026-09-05 only the install
    spine's copy was tested: deleting the two lines from `wait_ready_quietly()`
    left the whole suite green on m910q (2594 passed, 0 failures), while the
    identical two lines in `wait_for_ready()` were owned by
    `test_a_first_reading_the_daemon_refused_does_not_switch_off_the_crash_check`.
    They are one function now, and this is the second driver of it.

    The fixture violates exactly one rule -- the first look answers "could not
    ask" -- and everything after it is readable. Without the late baseline
    `grew` is `None` for the rest of the call, so the crash loop is invisible
    and the container is refused as quiet three windows later instead of named
    as a loop at the second.
    """
    readings = [
        native.WorldOutput(text="", restarts=None, status=""),
        native.WorldOutput(text="starting", restarts=0, status="restarting"),
        native.WorldOutput(text="starting again", restarts=4, status="restarting"),
    ]
    windows: list[float] = []
    now = {"t": 0.0}

    def wait(spec: docker.ContainerSpec, ready: docker.ReadySpec, **_kwargs: object) -> bool:
        windows.append(ready.timeout)
        now["t"] += ready.timeout
        return False

    def look(spec: docker.ContainerSpec, **_kwargs: object) -> native.WorldOutput:
        return readings[min(len(windows), len(readings) - 1)]

    got = native.wait_ready_quietly(
        TBC.container_spec(),
        docker.ReadySpec(world="Avg Diff:", timeout=480.0, restart_loop=4),
        wait=wait,
        output=look,
        monotonic=lambda: now["t"],
    )

    assert got is False
    assert len(windows) == 2, (
        "the crash loop is four restarts past a baseline of 0, taken from the second look; "
        f"{len(windows)} windows means the baseline stayed None and the loop was never seen"
    )


def test_a_container_that_restarted_without_printing_anything_new_is_not_called_quiet() -> None:
    """Any change at all counts as life, and the whole reading is what is compared.

    `_read_world()` asks `now == before` on the whole `WorldOutput`, not on its
    text. Weakening it to `now.text == before.text` left the entire suite green
    on m910q 2026-09-05, and it is not a safe weakening: `docker._logs()` is
    scoped to the CURRENT run, so a container that restarted below the crash
    threshold reprints the same first lines and its text is genuinely
    unchanged. Compared on text alone that server is "quiet" -- refused, with a
    sentence saying it stopped printing -- while the restart count in the same
    reading says it is very much alive.

    One rule broken: the container restarted once. The threshold is four, so
    this is not a loop; the status is `running`, so it is not gone; the only
    question left is the quiet one, which is what makes this a test of that
    question and not of the three ahead of it.
    """
    readings = [
        native.WorldOutput(text="World initialised", restarts=0, status="running"),
        native.WorldOutput(text="World initialised", restarts=1, status="running"),
    ]
    windows: list[float] = []

    def wait(spec: docker.ContainerSpec, ready: docker.ReadySpec, **_kwargs: object) -> bool:
        windows.append(ready.timeout)
        return len(windows) > 1

    def look(spec: docker.ContainerSpec, **_kwargs: object) -> native.WorldOutput:
        return readings[min(len(windows), len(readings) - 1)]

    got = native.wait_ready_quietly(
        TBC.container_spec(),
        docker.ReadySpec(world="Avg Diff:", timeout=float(TBC_QUIET_S), restart_loop=4),
        wait=wait,
        output=look,
        monotonic=lambda: 0.0,
    )

    assert got is True, (
        "the container restarted between the two readings and printed the same first lines "
        "again; a wait that reads only the text calls that silence and gives up on it"
    )
    assert len(windows) == 2
