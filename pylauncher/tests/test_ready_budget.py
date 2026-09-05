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

import threading
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

    elapsed: float = 0.0
    windows: list[float] = field(default_factory=list)
    """Every `ReadySpec.timeout` the engine handed a window, in order."""

    def wait_ready(self, spec: docker.ContainerSpec, ready: docker.ReadySpec) -> bool:
        self.windows.append(ready.timeout)
        if self.boot_s <= self.elapsed + ready.timeout:
            self.elapsed = self.boot_s
            return True
        self.elapsed += ready.timeout
        return False

    def output(self, spec: docker.ContainerSpec) -> native.WorldOutput:
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
    seams = {"wait_ready": world.wait_ready, "world_output": world.output, **overrides}
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
    """`ContainerState()` answers `""` for a read that failed; that is not "exited"."""
    world = FakeWorld(boot_s=float("inf"), status="", quiet_after_s=0.0)
    message = _refusal(world)

    assert "is not running" not in message
    assert "stopped printing" in message


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


def test_a_one_window_ceiling_is_still_one_window() -> None:
    """A `timeout_s` larger than the ceiling must not floor the wait to zero windows."""
    world = FakeWorld(boot_s=float("inf"))
    markers = ReadyMarkers(world="Avg Diff:", timeout_s=native.READY_CEILING_SECONDS * 2)
    with pytest.raises(InstallerError, match="never reported ready"):
        list(_installer(world).wait_for_ready(_ctx(), markers))
    assert world.windows == [float(native.READY_CEILING_SECONDS * 2)]


# -- the seam behind it -----------------------------------------------------


def test_the_default_output_seam_reads_this_run_of_the_world_container() -> None:
    """Not wired anywhere but here, so the default IS the production path.

    Both halves matter. `this_run_only` is why a restarted server's OLD ready
    banner cannot be read as progress (docker.py, 2026-08-22), and asking
    `container_state` first is what makes the restart count and the log come
    from the same look at the machine.
    """
    asked: list[tuple[str, object]] = []

    def container_state(name: str) -> docker.ContainerState:
        asked.append(("state", name))
        return docker.ContainerState("running", "2026-09-04T18:59:55Z", 3)

    def logs(name: str, *, this_run_only: bool = False, since: str = "") -> str:
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


def test_a_daemon_that_will_not_answer_is_unknown_and_never_progress() -> None:
    """`docker` failing must not read as "it printed something new"."""

    def boom(name: str, **_kwargs: object) -> object:
        raise docker.DockerCommandError("docker inspect exited 1")

    original = docker.container_state
    docker.container_state = boom  # type: ignore[assignment]
    try:
        out = native.Seams().world_output(ENTRY.container_spec())
    finally:
        docker.container_state = original

    assert out == native.WorldOutput(text="", restarts=None, status="")


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

    def output(spec: docker.ContainerSpec) -> native.WorldOutput:
        return next(readings, world.output(spec))

    message = _refusal(world, world_output=output)

    assert "restarted" in message and "crash loop" in message
    assert len(world.windows) == 2, "one window later than a readable first look, and no more"


def test_an_unreadable_restart_count_is_not_counted_towards_a_crash_loop() -> None:
    """`restarts=None` is "could not ask", and a guess in either direction is worse."""
    world = FakeWorld(boot_s=float("inf"), quiet_after_s=0.0)
    unknown = native.WorldOutput(text="", restarts=None, status="")
    message = _refusal(world, world_output=lambda spec: unknown)

    assert "restarted" not in message
    assert "stopped printing" in message
