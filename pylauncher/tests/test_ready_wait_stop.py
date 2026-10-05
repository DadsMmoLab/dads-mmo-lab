"""Stop pressed during the ready wait is heard at once (T247).

Until T247 `wait_ready()` took no cancel, so a Stop pressed while the app waited
for the world server was not acted on until the wait ended by itself: 83 s on
m910q (2026-10-05), on a crash loop's verdict.

What a Stop then DOES depends on the press (the lead's ruling, 2026-10-05):

* **Rebuild, Update, Return** have a build from before to put back, and the new
  world may be in the middle of its database update. T158's rule holds: never
  kill a loading world. The Stop is heard at once and said; the wait goes on
  until the world is ready, crashes or runs out; then the build from before goes
  back, with no forced stop. "Stop now anyway" is the escape, with its warning.
* **Install** has nothing to put back: the wait ends at once and the world is
  left loading, with a sentence saying so.

Every test presses the panel's own Stop (`tests/support_stop.py`) on a real
press. The fake ready waits block on the cancel THEY ARE HANDED
(`ReadySpec.cancel`) and on nothing else, so a press that did not hand the
job's Stop to the wait fails at the `assert` inside the fake.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from tests.conftest import HANG_BOUND, pump_until, wait_for_panel
from tests.support_native import ENTRY, Recorder, engine, install
from tests.support_stop import stop_when
from tests.test_update_to_latest import OLD, _heads, _ready
from yulon import docker
from yulon.catalog import native
from yulon.catalog.installer import InstallOptions, ReadyWaitStopped
from yulon.ui.widgets.log_panel import STOPPED_THEN_FAILED, LogPanel

Step = Callable[[docker.ReadySpec], bool]

WATCH_PAUSES = max(1, int(native.READY_GRACE_SECONDS / docker.ReadySpec(world="x").interval))
"""How many pauses T71's watch takes between its looks, at the shipped grace and interval."""


class _Waits:
    """A ready-wait seam that answers with `steps` in turn, the last one for every later call."""

    def __init__(self, *steps: Step) -> None:
        self.steps = steps
        self.asked: list[docker.ReadySpec] = []

    def __call__(self, spec: object, ready: docker.ReadySpec) -> bool:
        self.asked.append(ready)
        return self.steps[min(len(self.asked), len(self.steps)) - 1](ready)


def _until_stop(reached: threading.Event) -> Step:
    """Hold until the wait's own cancel is set (the job's Stop), then answer "not yet"."""

    def step(ready: docker.ReadySpec) -> bool:
        assert (
            ready.cancel is not None
        ), "the ready wait was handed no cancel, so Stop cannot end it"
        reached.set()
        assert ready.cancel.wait(HANG_BOUND), "Stop never reached the ready wait"
        return False

    return step


def _until_escape(reached: threading.Event) -> Step:
    """The wait after a Stop was heard: it must still be waitable, and end on "Stop now anyway"."""

    def step(ready: docker.ReadySpec) -> bool:
        assert ready.cancel is not None, "nothing can end the wait the Stop let the load finish in"
        assert not ready.cancel.is_set(), "the wait after the Stop was handed the Stop itself"
        reached.set()
        assert ready.cancel.wait(HANG_BOUND), '"Stop now anyway" never reached the wait'
        return False

    return step


def _answers(value: bool) -> Step:
    return lambda ready: value


def _forced_stops(rec: Recorder) -> list[str]:
    return [call for call in rec.calls if call == "stop_servers:forced"]


def _update(
    rec: Recorder,
    server_dir: Path,
    waits: _Waits,
    reached: threading.Event,
    *,
    to_pin: bool = False,
    rebuild: bool = False,
    **seams: object,
) -> tuple[LogPanel, list[tuple[bool, str]]]:
    made = engine(rec, wait_ready=waits, **seams)
    options = InstallOptions(server_dir=server_dir)

    def press(cancel: threading.Event) -> Iterator[str]:
        if rebuild:
            return made.rebuild(options, cancel=cancel)
        return made.update_to_latest(options, cancel=cancel, to_pin=to_pin)

    return stop_when(
        press, reached, "the press reached its ready wait", cancel=docker.CancelWithForce()
    )


# ------------------------------------------------- Rebuild, Update, Return: let it load


def test_stop_in_an_updates_ready_wait_lets_the_world_load_then_puts_the_old_build_back(
    qapp: object, tmp_path: Path
) -> None:
    """The live case (m910q P10), under T158's rule: the Stop is said at once, the new world
    finishes its load, and only then is it stopped -- cleanly -- and the build from before put
    back, with the sources."""
    rec, server_dir = _ready(tmp_path)
    reached = threading.Event()
    waits = _Waits(_until_stop(reached), _answers(True), _answers(True))
    panel, finished = _update(rec, server_dir, waits, reached)

    said = panel.text()
    assert said.count(docker.STOP_WAITS_FOR_THE_LOAD) == 1, said
    assert native.READY_WAIT_STOP_HINT in said, "the escape was not offered with its warning"
    assert len(waits.asked) == 3, "the wait did not go on to the load's end, then the rollback's"
    assert said.index(docker.STOP_WAITS_FOR_THE_LOAD) < said.index(native.ROLLBACK_STOPPING)
    assert _forced_stops(rec) == [], "a loading world was force-stopped"
    assert "stop_servers" in rec.calls, rec.calls
    header = panel.status_text()
    assert header.startswith(STOPPED_THEN_FAILED + native.READY_STOPPED_AFTER_LOADING), header
    assert header.endswith(native.SOURCES_PUT_BACK_NOTE), header
    assert finished and finished[0][0] is False, finished
    assert rec.calls.count("recreate") == 2, "the build from before was not put back"
    assert set(_heads(rec, server_dir).values()) == {OLD}, "the sources stayed moved"


def test_stop_in_the_return_to_the_tested_commits_ready_wait_lets_it_load_too(
    qapp: object, tmp_path: Path
) -> None:
    rec, server_dir = _ready(tmp_path)
    reached = threading.Event()
    waits = _Waits(_until_stop(reached), _answers(True), _answers(True))
    panel, _finished = _update(rec, server_dir, waits, reached, to_pin=True)

    assert panel.status_text().startswith(
        STOPPED_THEN_FAILED + native.READY_STOPPED_AFTER_LOADING
    ), panel.status_text()
    assert _forced_stops(rec) == [], "a loading world was force-stopped"
    assert rec.calls.count("recreate") == 2, "the build from before was not put back"


def test_stop_in_a_rebuilds_ready_wait_lets_it_load_too(qapp: object, tmp_path: Path) -> None:
    rec, server_dir = _ready(tmp_path)
    reached = threading.Event()
    waits = _Waits(_until_stop(reached), _answers(True), _answers(True))
    panel, _finished = _update(rec, server_dir, waits, reached, rebuild=True)

    header = panel.status_text()
    assert header.startswith(STOPPED_THEN_FAILED + native.READY_STOPPED_AFTER_LOADING), header
    assert "put back and is running again" in header, header
    assert _forced_stops(rec) == [], "a loading world was force-stopped"


def test_stop_now_anyway_ends_the_load_and_forces_the_stop_with_its_warning(
    qapp: object, tmp_path: Path
) -> None:
    """The second press: "Stop now anyway" ends the wait, and the rollback's stop is forced."""
    rec, server_dir = _ready(tmp_path)
    stopped, escaped = threading.Event(), threading.Event()
    waits = _Waits(_until_stop(stopped), _until_escape(escaped), _answers(True))
    made = engine(rec, wait_ready=waits)
    options = InstallOptions(server_dir=server_dir)
    cancel = docker.CancelWithForce()
    panel = LogPanel()
    panel.run(lambda: made.update_to_latest(options, cancel=cancel), cancel=cancel)
    pump_until(stopped.is_set, "the new build's ready wait")
    panel.stop()
    pump_until(escaped.is_set, "the wait the load finishes in")
    pump_until(lambda: native.READY_WAIT_STOP_HINT in panel.text(), "the escape was offered")
    cancel.anyway.set()  # the rebuild panel's "Stop now anyway"
    wait_for_panel(panel)

    header = panel.status_text()
    assert header.startswith(STOPPED_THEN_FAILED + native.READY_WAIT_STOPPED_ANYWAY), header
    assert _forced_stops(rec) == ["stop_servers:forced"], rec.calls
    assert rec.calls.count("recreate") == 2, "the build from before was not put back"


def test_a_crash_after_the_stop_takes_the_normal_crash_path(qapp: object, tmp_path: Path) -> None:
    """The world crash-loops while the Stop waits on its load: that verdict is the failure, and
    the rollback stops it as it would without a Stop -- not forced."""
    rec, server_dir = _ready(tmp_path)
    reached = threading.Event()
    looks = {"n": 0}

    def world(spec: object) -> native.WorldOutput:
        looks["n"] += 1
        return native.WorldOutput(
            text="loading", restarts=0 if looks["n"] <= 2 else 9, status="running"
        )

    waits = _Waits(_until_stop(reached), _answers(False), _answers(True))
    panel, _finished = _update(rec, server_dir, waits, reached, world_output=world)

    header = panel.status_text()
    assert header.startswith(STOPPED_THEN_FAILED), header
    assert "crash loop" in header, header
    assert native.READY_STOPPED_AFTER_LOADING not in header, header
    assert _forced_stops(rec) == [], rec.calls
    assert rec.calls.count("recreate") == 2, "the build from before was not put back"


def test_a_crash_in_the_watch_while_a_stop_is_pending_puts_the_old_build_back(
    qapp: object, tmp_path: Path
) -> None:
    """The lead's ruling (2026-10-05): Stop pending, the world reports ready, then crashes
    inside the watch. Without a Stop that keeps the build (T71); with one, both the Stop and the
    crash point back, so the build from before goes back, and the sentence says both."""
    rec, server_dir = _ready(tmp_path)
    reached = threading.Event()
    banner = threading.Event()

    def came_up(ready: docker.ReadySpec) -> bool:
        banner.set()
        return True

    def world(spec: object) -> native.WorldOutput:
        if banner.is_set() and rec.calls.count("recreate") == 1:
            return native.WorldOutput(
                text="ready...\nWorld server is up and running\n>> ABORTED",
                restarts=0,
                status="exited",
            )
        return native.WorldOutput(
            text="mangosd loading\nready...\nAvg Diff: 15ms\nWorld server is up and running",
            restarts=0,
            status="running",
        )

    waits = _Waits(_until_stop(reached), came_up, _answers(True))
    panel, finished = _update(rec, server_dir, waits, reached, world_output=world)

    header = panel.status_text()
    assert header.startswith(STOPPED_THEN_FAILED + native.READY_CRASHED_AFTER_STOP), header
    assert "KEPT" not in header, header
    assert "put back and is running again" in header, header
    assert finished and finished[0][0] is False, finished
    assert rec.calls.count("recreate") == 2, "the build from before was not put back"
    assert _forced_stops(rec) == [], rec.calls
    assert set(_heads(rec, server_dir).values()) == {OLD}, "the sources stayed with a crashed build"


def test_the_rollbacks_own_wait_announces_that_stop_cannot_end_it(
    qapp: object, tmp_path: Path
) -> None:
    """A rollback is finished, not given up: its wait is handed no cancel, and says so once."""
    rec, server_dir = _ready(tmp_path)
    reached = threading.Event()
    waits = _Waits(_until_stop(reached), _answers(False), _answers(False))
    panel, _finished = _update(rec, server_dir, waits, reached)

    assert len(waits.asked) == 3, waits.asked
    assert waits.asked[2].cancel is None, "the rollback's wait could be ended by the Stop"
    assert panel.text().count(native.ROLLBACK_WAIT_UNSTOPPABLE) == 1, panel.text()
    assert "did not report ready either" in panel.status_text(), panel.status_text()


# -------------------------------------------------------- the minute after the banner


def _stopped_in_watch_pause(
    rec: Recorder, server_dir: Path, pause_number: int
) -> tuple[LogPanel, list[tuple[bool, str]]]:
    """Rebuild whose new world says ready at once, with Stop pressed in the watch's Nth pause."""
    reached = threading.Event()
    gate: list[threading.Event] = []
    pauses = {"n": 0}

    def pause(_seconds: float) -> None:
        rec.calls.append("pause")
        pauses["n"] += 1
        if pauses["n"] == pause_number:
            reached.set()
            assert gate[0].wait(HANG_BOUND), "Stop never came"

    made = engine(rec, sleep=pause)
    options = InstallOptions(server_dir=server_dir)

    def press(cancel: threading.Event) -> Iterator[str]:
        gate.append(cancel)
        return made.rebuild(options, cancel=cancel)

    return stop_when(press, reached, "the watch after the banner", cancel=docker.CancelWithForce())


def test_stop_during_the_watch_after_the_banner_says_it_reported_ready_and_rolls_back(
    qapp: object, tmp_path: Path
) -> None:
    """The world printed ready and was being watched when Stop came: the minute was not over,
    so the build is not proved, and the sentence says it DID report ready."""
    rec, server_dir = _ready(tmp_path)
    panel, _finished = _stopped_in_watch_pause(rec, server_dir, 1)

    header = panel.status_text()
    assert header.startswith(STOPPED_THEN_FAILED + native.READY_STOPPED_IN_THE_WATCH), header
    assert "reported ready" in native.READY_STOPPED_IN_THE_WATCH
    assert "never seen to come up" not in header, header
    calls = rec.calls
    first = calls.index("pause")
    stop = next(i for i, call in enumerate(calls) if call.startswith("stop_servers"))
    assert "pause" not in calls[first + 1 : stop], "the watch went on after the Stop"
    said = panel.text()
    assert said.index(native.ROLLBACK_STOPPING) < said.index(
        "The server is up."
    ), "the new build was called up over a press the player had stopped"
    assert rec.calls.count("recreate") == 2, "the build from before was not put back"
    assert _forced_stops(rec) == [], rec.calls


def test_stop_in_the_last_pause_of_the_watch_came_too_late_and_the_build_is_kept(
    qapp: object, tmp_path: Path
) -> None:
    """The whole minute was watched: the build met T71's proof before the Stop could matter."""
    rec, server_dir = _ready(tmp_path)
    panel, _finished = _stopped_in_watch_pause(rec, server_dir, WATCH_PAUSES)

    said = panel.text()
    assert native.READY_STOP_TOO_LATE in said, said
    assert said.index(native.READY_STOP_TOO_LATE) < said.index("The server is up."), said
    assert f"{ENTRY.name} was rebuilt and is running" in said, said
    assert rec.calls.count("recreate") == 1, "a build that met its proof was put back"
    assert native.ROLLBACK_STOPPING not in said, said


# -------------------------------------------------------------------- Install


def test_stop_in_an_installs_ready_wait_is_a_clean_stop_that_says_the_server_is_left_loading(
    qapp: object, tmp_path: Path
) -> None:
    """An install has no build from before: the wait ends at once, and the press says what it
    left. Its world is left loading -- T158: never killed mid-load -- and Install again on the
    folder resumes at the wait."""
    rec = Recorder()
    reached = threading.Event()
    waits = _Waits(_until_stop(reached))
    made = engine(rec, wait_ready=waits)
    options = InstallOptions(server_dir=tmp_path / "server")
    panel, finished = stop_when(
        lambda cancel: made.run(options, cancel=cancel), reached, "the install's ready wait"
    )

    assert panel.status_text() == "cancelled"
    assert finished == [(True, "stopped")], finished
    assert native.INSTALL_LEFT_LOADING in panel.text(), panel.text()
    assert len(waits.asked) == 1, "the install waited on after the Stop"
    assert not [c for c in rec.calls if c.startswith("stop")], rec.calls
    record = native.read_state(tmp_path / "server", valid=())
    assert record is not None and native.READY_WAIT_STOPPED in record.last_error


def test_an_install_stopped_in_the_watch_after_its_banner_says_the_world_had_reported_ready(
    qapp: object, tmp_path: Path
) -> None:
    """Codex review: Stop inside T71's watch on an install. The world HAD reported ready, so the
    log and the popup say so, not that it is still loading."""
    from yulon.ui.catalog_view import ready_wait_stopped_message

    rec = Recorder()
    reached = threading.Event()
    gate: list[threading.Event] = []

    def pause(_seconds: float) -> None:
        if not reached.is_set():
            reached.set()
            assert gate[0].wait(HANG_BOUND), "Stop never came"

    made = engine(rec, sleep=pause)
    options = InstallOptions(server_dir=tmp_path / "server")

    def press(cancel: threading.Event) -> Iterator[str]:
        gate.append(cancel)
        return made.run(options, cancel=cancel)

    panel, finished = stop_when(press, reached, "the install's watch after the banner")

    said = panel.text()
    assert panel.status_text() == "cancelled"
    assert native.INSTALL_LEFT_RUNNING in said, said
    assert native.INSTALL_LEFT_LOADING not in said, said
    assert panel.stopped_by is native.StoppedInTheWatch, panel.stopped_by
    popup = ready_wait_stopped_message(ENTRY, native.StoppedInTheWatch)
    assert native.INSTALL_LEFT_RUNNING in popup and native.INSTALL_LEFT_LOADING not in popup


def test_an_install_stopped_in_its_ready_wait_resumes_at_the_wait(tmp_path: Path) -> None:
    """The sentence's promise, driven: the next Install starts the servers and waits again."""
    rec = Recorder()
    cancel = threading.Event()

    def stopped(spec: object, ready: docker.ReadySpec) -> bool:
        cancel.set()
        return False

    server_dir = tmp_path / "server"
    said: list[str] = []
    with pytest.raises(ReadyWaitStopped):
        for line in engine(rec, wait_ready=stopped).run(
            InstallOptions(server_dir=server_dir), cancel=cancel
        ):
            said.append(line)
    rec.calls.clear()
    again = install(rec, server_dir)
    assert "build" not in rec.calls, rec.calls
    assert again[-1].endswith(f"is installed and running in {server_dir}"), again


# ------------------------------------------------------- the management waits


def test_a_stop_ends_a_management_wait_and_leaves_the_world_loading(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`wait_ready_quietly()` hears a cancel: it ends the WAIT, stops nothing, and says so.

    The world keeps printing, so a wait that did not ask the cancel would go on
    to a second window (and fail on the fake's assert).
    """
    cancel = threading.Event()
    asked: list[docker.ReadySpec] = []
    looks = {"n": 0}

    def wait(spec: object, ready: docker.ReadySpec, **_kw: object) -> bool:
        assert not asked, "it waited again after the Stop"
        asked.append(ready)
        cancel.set()
        return False

    def output(spec: object, **_kw: object) -> native.WorldOutput:
        looks["n"] += 1
        return native.WorldOutput(text="loading " * looks["n"], restarts=0, status="running")

    with caplog.at_level(logging.INFO, logger="yulon.catalog.native"):
        up = native.wait_ready_quietly(
            ENTRY.container_spec(),
            docker.ReadySpec(world="ready", timeout=600.0),
            wait=wait,
            output=output,
            cancel=cancel,
        )
    assert up is False
    assert asked[0].cancel is cancel, "the wait underneath was not handed the cancel"
    assert native.MANAGEMENT_WAIT_STOPPED in caplog.text
