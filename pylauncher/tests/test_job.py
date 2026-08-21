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

from tests.conftest import process_events
from yulon.ui.widgets.job import ThreadedJobRunner, run_inline


class _Receiver(QObject):
    """A GUI-thread QObject with real slots — what a view is, in miniature."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[object] = []
        self.errors: list[object] = []
        self.threads: list[int] = []

    @Slot(object)
    def done(self, result: object) -> None:
        self.results.append(result)
        self.threads.append(threading.get_ident())

    @Slot(object)
    def failed(self, exc: object) -> None:
        self.errors.append(exc)
        self.threads.append(threading.get_ident())


def _pump_until(predicate: object, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not predicate():  # type: ignore[operator]
        process_events(20)


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
    _pump_until(lambda: receiver.results)
    assert receiver.results == ["hello"], "the job never ran (weak-reference regression)"
    assert worker_threads and worker_threads[0] != threading.get_ident()  # ran off the GUI thread
    assert receiver.threads == [threading.get_ident()]  # answered ON the GUI thread
    assert runner.wait(5000) is True


def test_threaded_runner_reports_failures_instead_of_raising(qapp: object) -> None:
    receiver = _Receiver()
    runner = ThreadedJobRunner(receiver)

    def boom() -> None:
        raise RuntimeError("no docker")

    runner(boom, receiver.done, receiver.failed)
    _pump_until(lambda: receiver.errors)
    assert receiver.results == []
    assert isinstance(receiver.errors[0], RuntimeError) and "no docker" in str(receiver.errors[0])
    runner.wait(5000)


def test_wait_joins_running_jobs(qapp: object) -> None:
    """`wait()` exists so the app can join jobs before Qt tears down (a live QThread aborts)."""
    receiver = _Receiver()
    runner = ThreadedJobRunner(receiver)
    runner(lambda: time.sleep(0.2), receiver.done, receiver.failed)
    assert runner.wait(10_000) is True


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
