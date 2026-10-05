"""Stop pressed during the ready wait ends the wait at once (T247).

Until T247 `wait_ready()` took no cancel, so a Stop pressed while the app waited
for the world server was not acted on until the wait ended by itself: 83 s on
m910q (2026-10-05), on a crash loop's verdict, and a healthy slow boot would hold
it for many minutes. Every test here presses the panel's own Stop
(`tests/support_stop.py`) on a real press and reads what the panel shows.

The fake ready waits block on the cancel THEY ARE HANDED (`ReadySpec.cancel`)
and on nothing else. A press that did not hand the job's cancel to the wait
fails at once on the `assert` inside the fake, rather than hanging until a
deadlock breaker.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

from tests.conftest import HANG_BOUND
from tests.support_native import Recorder, engine, install
from tests.support_stop import stop_when
from tests.test_update_to_latest import OLD, _heads, _ready
from yulon import docker
from yulon.catalog import native
from yulon.catalog.installer import InstallOptions, ReadyWaitStopped
from yulon.ui.widgets.log_panel import STOPPED_THEN_FAILED, LogPanel


class _HeldWait:
    """A ready wait that, the first time, holds until the job's cancel is set, then says "not yet".

    Every later call is the rollback's wait for the build from before, and
    answers `later`. Every `ReadySpec` asked about is kept, in order.
    """

    def __init__(self, later: bool = True) -> None:
        self.reached = threading.Event()
        self.later = later
        self.asked: list[docker.ReadySpec] = []

    def __call__(self, spec: object, ready: docker.ReadySpec) -> bool:
        self.asked.append(ready)
        if len(self.asked) > 1:
            return self.later
        assert (
            ready.cancel is not None
        ), "the ready wait was handed no cancel, so Stop cannot end it"
        self.reached.set()
        assert ready.cancel.wait(HANG_BOUND), "Stop never reached the ready wait"
        return False


def _update_stopped_in_its_ready_wait(
    rec: Recorder, server_dir: Path, wait: _HeldWait, *, to_pin: bool = False
) -> tuple[LogPanel, list[tuple[bool, str]]]:
    made = engine(rec, wait_ready=wait)
    options = InstallOptions(server_dir=server_dir)

    def press(cancel: threading.Event) -> Iterator[str]:
        return made.update_to_latest(options, cancel=cancel, to_pin=to_pin)

    return stop_when(press, wait.reached, "the press reached the new build's ready wait")


def test_stop_in_an_updates_ready_wait_ends_it_and_puts_the_old_build_back(
    qapp: object, tmp_path: Path
) -> None:
    """The live case (m910q P10): Stop in "Waiting for the world server" during an update.

    The wait ends on the Stop, and the press then does what a failed wait does:
    the new build was never seen to come up, so the build from before goes back,
    and so do the sources. The header says the player stopped it.

    The fake world says nothing new, so a wait whose window simply ended would
    read it as "stopped printing" -- that sentence must not be the one shown,
    because the window ended on the Stop, not on silence.
    """
    rec, server_dir = _ready(tmp_path)
    wait = _HeldWait()
    panel, finished = _update_stopped_in_its_ready_wait(rec, server_dir, wait)

    header = panel.status_text()
    assert header.startswith(STOPPED_THEN_FAILED + native.READY_WAIT_STOPPED), header
    assert "put back and is running again" in header, header
    assert header.endswith(native.SOURCES_PUT_BACK_NOTE), header
    assert "stopped printing" not in header, "a window the Stop ended was read as silence"
    assert finished and finished[0][0] is False, finished
    assert rec.calls.count("recreate") == 2, "the build from before was not put back"
    assert set(_heads(rec, server_dir).values()) == {OLD}, "the sources stayed moved"


def test_the_rollbacks_own_wait_for_the_build_from_before_is_not_cut_short(
    qapp: object, tmp_path: Path
) -> None:
    """A rollback is finished, not given up: its wait is handed no cancel, as `back()` is not.

    The Stop that ended the new build's wait is already set. Handed on, it
    would end the old build's wait before it began, and the press would say
    nothing true about whether the build it put back is up.
    """
    rec, server_dir = _ready(tmp_path)
    wait = _HeldWait(later=False)
    panel, _finished = _update_stopped_in_its_ready_wait(rec, server_dir, wait)

    assert len(wait.asked) >= 2, "the rollback never waited for the build it put back"
    assert [ready.cancel for ready in wait.asked[1:]] == [None] * (len(wait.asked) - 1)
    assert "did not report ready either" in panel.status_text(), panel.status_text()


def test_stop_in_the_return_to_the_tested_commits_ready_wait_puts_the_old_build_back(
    qapp: object, tmp_path: Path
) -> None:
    """The same route with `to_pin`: the Return press reaches the same wait the same way."""
    rec, server_dir = _ready(tmp_path)
    wait = _HeldWait()
    panel, _finished = _update_stopped_in_its_ready_wait(rec, server_dir, wait, to_pin=True)

    header = panel.status_text()
    assert header.startswith(STOPPED_THEN_FAILED + native.READY_WAIT_STOPPED), header
    assert rec.calls.count("recreate") == 2, "the build from before was not put back"


def test_stop_in_a_rebuilds_ready_wait_puts_the_old_build_back(
    qapp: object, tmp_path: Path
) -> None:
    """Rebuild: no source moves, and the build from before goes back."""
    rec, server_dir = _ready(tmp_path)
    wait = _HeldWait()
    made = engine(rec, wait_ready=wait)
    options = InstallOptions(server_dir=server_dir)
    panel, finished = stop_when(
        lambda cancel: made.rebuild(options, cancel=cancel),
        wait.reached,
        "the rebuild reached the new build's ready wait",
    )

    header = panel.status_text()
    assert header.startswith(STOPPED_THEN_FAILED + native.READY_WAIT_STOPPED), header
    assert "put back and is running again" in header, header
    assert finished and finished[0][0] is False, finished
    assert rec.calls.count("recreate") == 2, "the build from before was not put back"


def test_stop_while_the_world_is_watched_after_its_banner_puts_the_old_build_back(
    qapp: object, tmp_path: Path
) -> None:
    """Stop in the minute after the banner (T71's watch): the build is not proved yet either.

    The watch's pause holds until the Stop. Without the cancel the watch would
    run its whole minute and then say "The server is up." over a press the
    player had stopped.
    """
    rec, server_dir = _ready(tmp_path)
    reached = threading.Event()
    gate: list[threading.Event] = []

    def pause(_seconds: float) -> None:
        rec.calls.append("pause")
        if not reached.is_set():
            reached.set()
            assert gate[0].wait(HANG_BOUND), "Stop never came"

    made = engine(rec, sleep=pause)
    options = InstallOptions(server_dir=server_dir)

    def press(cancel: threading.Event) -> Iterator[str]:
        gate.append(cancel)
        return made.rebuild(options, cancel=cancel)

    panel, _finished = stop_when(press, reached, "the watch after the banner")

    header = panel.status_text()
    assert header.startswith(STOPPED_THEN_FAILED + native.READY_WAIT_STOPPED), header
    calls = rec.calls
    first = calls.index("pause")
    stop = next(i for i, call in enumerate(calls) if call.startswith("stop_servers"))
    assert "pause" not in calls[first + 1 : stop], "the watch went on after the Stop"
    said = panel.text()
    assert said.index(native.ROLLBACK_STOPPING) < said.index(
        "The server is up."
    ), "the new build was called up over a press the player had stopped"
    assert rec.calls.count("recreate") == 2, "the build from before was not put back"


def test_stop_in_an_installs_ready_wait_is_a_clean_stop_that_says_the_server_is_left_loading(
    qapp: object, tmp_path: Path
) -> None:
    """An install has no build from before: the wait ends, and the press says what it left.

    Its containers are up and its world is loading. Nothing stops them -- a world
    signalled mid-load is the harm T158 waits to avoid -- so the log says they
    are left running, and that pressing Install again on the folder resumes at
    the wait.
    """
    rec = Recorder()
    wait = _HeldWait()
    made = engine(rec, wait_ready=wait)
    options = InstallOptions(server_dir=tmp_path / "server")
    panel, finished = stop_when(
        lambda cancel: made.run(options, cancel=cancel),
        wait.reached,
        "the install reached its ready wait",
    )

    assert panel.status_text() == "cancelled"
    assert finished == [(True, "stopped")], finished
    assert native.INSTALL_LEFT_LOADING in panel.text(), panel.text()
    assert not [c for c in rec.calls if c.startswith("stop")], rec.calls
    record = native.read_state(tmp_path / "server", valid=())
    assert record is not None and native.READY_WAIT_STOPPED in record.last_error


def test_an_install_stopped_in_its_ready_wait_resumes_at_the_wait(tmp_path: Path) -> None:
    """The sentence's promise, driven: the next Install starts the servers and waits again."""
    rec = Recorder()
    cancel = threading.Event()

    def stopped(spec: object, ready: docker.ReadySpec) -> bool:
        cancel.set()
        return False

    server_dir = tmp_path / "server"
    said: list[str] = []
    try:
        for line in engine(rec, wait_ready=stopped).run(
            InstallOptions(server_dir=server_dir), cancel=cancel
        ):
            said.append(line)
    except ReadyWaitStopped:
        pass
    else:
        raise AssertionError(f"the stopped install did not end on the Stop: {said}")
    rec.calls.clear()
    again = install(rec, server_dir)
    assert "build" not in rec.calls, rec.calls
    assert again[-1].endswith(f"is installed and running in {server_dir}"), again
