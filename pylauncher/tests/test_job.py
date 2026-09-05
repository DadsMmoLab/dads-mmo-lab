"""Tests for `yulon.ui.widgets.job` — the background-job runner behind every view action.

The regression that motivates the first test: a runner that did not keep a
reference to its worker looked correct and did nothing at all. PySide6 connects
to a bound method through a weak reference, so the worker was collected the
moment the factory returned and its `run` slot never fired — on the test VM the
Server tab sat on "status: unknown" and Start on "starting…" forever, with no
error anywhere.
"""

from __future__ import annotations

import threading
import time

import pytest
from PySide6.QtCore import QObject, Slot

from tests.conftest import HANG_BOUND_MS, pump_until, spelled_bounds
from yulon.ui.widgets.job import LineRelay, ThreadedJobRunner, run_inline

STILL_RUNNING = 0.2
"""How long the job in `test_wait_joins_running_jobs` keeps running. Not a deadline.

The assertion there is that `wait()` JOINS a job, so the job must still be
running when `wait()` is called, and this is what keeps it so. It points the
way `DWELL_PROOF` does in `test_log_panel.py`: a slower box only makes the job
more surely still running. Measured on m910q 2026-09-04: `runner(...)` and
`runner.wait(...)` are one statement apart on the calling thread, and the
whole test reported `0.20s call`, which is this number plus the join.
"""


class _Receiver(QObject):
    """A GUI-thread QObject with real slots — what a view is, in miniature."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[object] = []
        self.errors: list[object] = []
        self.threads: list[int] = []
        self.lines: list[str] = []

    @Slot(object)
    def done(self, result: object) -> None:
        self.results.append(result)
        self.threads.append(threading.get_ident())

    @Slot(object)
    def failed(self, exc: object) -> None:
        self.errors.append(exc)
        self.threads.append(threading.get_ident())

    @Slot(str)
    def line(self, text: str) -> None:
        self.lines.append(text)
        self.threads.append(threading.get_ident())


def test_threaded_runner_actually_runs_the_work_and_answers_on_the_gui_thread(
    qapp: object,
) -> None:
    """The work runs on a worker thread; the callback lands back on the GUI thread."""
    receiver = _Receiver()
    runner = ThreadedJobRunner(receiver)
    worker_threads: list[int] = []

    def work() -> str:
        worker_threads.append(threading.get_ident())
        return "hello"

    runner(work, receiver.done, receiver.failed)
    pump_until(lambda: bool(receiver.results), "the result reached the receiver")
    assert receiver.results == ["hello"], "the job never ran (weak-reference regression)"
    assert worker_threads and worker_threads[0] != threading.get_ident()  # ran off the GUI thread
    assert receiver.threads == [threading.get_ident()]  # answered ON the GUI thread
    assert runner.wait(HANG_BOUND_MS) is True


def test_threaded_runner_reports_failures_instead_of_raising(qapp: object) -> None:
    receiver = _Receiver()
    runner = ThreadedJobRunner(receiver)

    def boom() -> None:
        raise RuntimeError("no docker")

    runner(boom, receiver.done, receiver.failed)
    pump_until(lambda: bool(receiver.errors), "the failure reached the receiver")
    assert receiver.results == []
    assert isinstance(receiver.errors[0], RuntimeError) and "no docker" in str(receiver.errors[0])
    assert runner.wait(HANG_BOUND_MS) is True


def test_wait_joins_running_jobs(qapp: object) -> None:
    """`wait()` exists so the app can join jobs before Qt tears down (a live QThread aborts)."""
    receiver = _Receiver()
    runner = ThreadedJobRunner(receiver)
    runner(lambda: time.sleep(STILL_RUNNING), receiver.done, receiver.failed)
    assert runner.wait(HANG_BOUND_MS) is True


def test_a_line_relay_delivers_on_the_gui_thread_whoever_emits(qapp: object) -> None:
    """The whole reason the import's output sink is a relay and not a bound slot.

    `repair_import()` runs on a worker thread and calls its sink there. Handing
    it a view's own `@Slot(str)` would look identical at the call site and be a
    plain Python call — the widget written to from the wrong thread. Emitting a
    signal on a QObject that lives on the GUI thread is the one mechanism that
    crosses the boundary, and this pins that it really does: the line is emitted
    from another thread and arrives on this one.
    """
    relay = LineRelay()
    receiver = _Receiver()
    relay.line.connect(receiver.line)

    emitted_from: list[int] = []

    def emit_from_a_worker() -> None:
        emitted_from.append(threading.get_ident())
        relay.emit_line("applying acore_world")

    worker = threading.Thread(target=emit_from_a_worker)
    worker.start()
    worker.join()
    pump_until(lambda: bool(receiver.lines), "the line reached the receiver")

    assert emitted_from and emitted_from[0] != threading.get_ident(), "emitted on the GUI thread"
    assert receiver.lines == ["applying acore_world"]
    assert receiver.threads == [threading.get_ident()], "the slot ran on the emitting thread"


def test_run_inline_has_the_same_contract() -> None:
    seen: list[object] = []
    errors: list[object] = []
    run_inline(lambda: 42, seen.append, errors.append)
    assert seen == [42] and errors == []

    def boom() -> None:
        raise ValueError("nope")

    run_inline(boom, seen.append, errors.append)
    assert isinstance(errors[0], ValueError)


@pytest.mark.parametrize("bad", [KeyboardInterrupt, SystemExit])
def test_run_inline_does_not_swallow_exit_signals(bad: type[BaseException]) -> None:
    """Only `Exception` is a job failure; interpreter-level signals must propagate."""

    def raiser() -> None:
        raise bad()

    with pytest.raises(bad):
        run_inline(raiser, lambda _r: None, lambda _e: None)


def test_no_wall_clock_bound_in_this_file_is_written_as_a_bare_number() -> None:
    """Every bound here must be spelled as one of the named ones, and nothing else.

    The same audit `test_log_panel.py` runs on itself, for the same reason: the
    bounds are the only thing in this file a loaded box can move, and a number
    typed at a call site carries no docstring. Until 2026-09-04 this file kept
    its own copy of the log panel's pump helper with `timeout: float = 10.0`,
    a `runner.wait(5000)` whose result was thrown away, and no audit at all --
    appending `gate.wait(5.0)` to it left 7 passed on m910q that day.
    """
    assert spelled_bounds(__file__) == {"HANG_BOUND_MS", "STILL_RUNNING"}
