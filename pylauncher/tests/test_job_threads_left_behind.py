"""The suite joins, at each test's end, the job threads that test left running (T769).

A view built with real job threads and never pumped leaves each job's thread waiting in its
event loop for a `quit` queued to the GUI thread. Before T764 the run aborted after its
summary for it; T764 joined them all at the end of the run, which kept the run alive but
blamed a job that HUNG on no test at all. The autouse fixture in `conftest.py` joins them per
test instead. These tests drive the very generator it runs, with bounds of their own.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import pytest
from PySide6.QtCore import QObject, QThread, Slot

from tests.conftest import (
    HANG_BOUND,
    HANG_BOUND_MS,
    joining_the_job_threads_left_behind,
    pump_until,
)
from yulon.ui.widgets.job import ThreadedJobRunner, in_flight

NOT_A_HANG_MS = 200
"""How long the guard waits, in these tests, for a job blocked on a gate the test holds.

Not a deadline anything has to meet: the job cannot finish until the test opens the gate,
so any bound proves the guard names it. Kept short so the proof is quick.
"""


class _Owner(QObject):
    """The GUI-thread object a view would be, with real slots for the answers."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[object] = []

    @Slot(object)
    def done(self, result: object) -> None:
        self.results.append(result)


def _start(owner: _Owner, work: Callable[[], object]) -> QThread:
    """Start `work` on a real job thread, as a view does, and hand back that thread."""
    ThreadedJobRunner(owner)(work, owner.done, owner.done)
    return in_flight()._pairs[-1][0]


def _gated(gate: threading.Event, entered: threading.Event) -> Callable[[], object]:
    def gated_work() -> object:
        entered.set()
        gate.wait(HANG_BOUND)
        return "late"

    return gated_work


def _release(thread: QThread, gate: threading.Event) -> None:
    """Open the gate and pump: the job's answer and its thread's `quit` are queued here."""
    gate.set()
    pump_until(lambda: not thread.isRunning(), "the gated job's thread never ended")


def test_a_job_its_test_never_pumped_is_joined_at_the_tests_end(qapp: object) -> None:
    """The five files' shape: the work is done, the thread waits for a quit nobody delivers."""
    owner = _Owner()
    ran = threading.Event()
    guard = joining_the_job_threads_left_behind(HANG_BOUND_MS)
    next(guard)

    def work() -> object:
        ran.set()
        return "answer"

    thread = _start(owner, work)
    assert ran.wait(HANG_BOUND), "the job never ran"
    assert thread.isRunning(), "premise: unpumped, the thread waits for its queued quit"
    next(guard, None)
    assert not thread.isRunning(), "the guard did not join the thread the test left"


def test_a_job_that_does_not_finish_fails_the_test_that_started_it(qapp: object) -> None:
    owner = _Owner()
    gate, entered = threading.Event(), threading.Event()
    guard = joining_the_job_threads_left_behind(NOT_A_HANG_MS)
    next(guard)
    thread = _start(owner, _gated(gate, entered))
    assert entered.wait(HANG_BOUND), "the job never started"
    try:
        with pytest.raises(pytest.fail.Exception) as failed:
            next(guard, None)
    finally:
        _release(thread, gate)
    said = str(failed.value)
    assert "gated_work" in said, said
    assert f"{NOT_A_HANG_MS} ms" in said, said
    pump_until(lambda: owner.results == ["late"], "the released job's answer never arrived")


def test_a_thread_running_before_the_test_began_is_left_to_its_owner(qapp: object) -> None:
    """A module fixture's job is its own: joining it here would wait on work a later test owns."""
    owner = _Owner()
    gate, entered = threading.Event(), threading.Event()
    thread = _start(owner, _gated(gate, entered))
    try:
        assert entered.wait(HANG_BOUND), "the job never started"
        guard = joining_the_job_threads_left_behind(NOT_A_HANG_MS)
        next(guard)
        next(guard, None)
        assert thread.isRunning(), "the guard joined a thread it found already running"
    finally:
        _release(thread, gate)
    pump_until(lambda: owner.results == ["late"], "the released job's answer never arrived")


_left_by_the_test_before: list[QThread] = []


def test_this_test_leaves_a_job_thread_behind(qapp: object) -> None:
    """Half of an end-to-end pair, the deliberate leak: a real job, never pumped."""
    ran = threading.Event()

    def work() -> object:
        ran.set()
        return "answer"

    thread = _start(_Owner(), work)
    assert ran.wait(HANG_BOUND), "the job never ran"
    assert thread.isRunning(), "premise: unpumped, the thread waits for its queued quit"
    _left_by_the_test_before.append(thread)


def test_the_job_thread_the_test_before_left_was_joined_at_its_end(qapp: object) -> None:
    """The other half: across a real test boundary, the autouse fixture did the join."""
    if not _left_by_the_test_before:
        pytest.skip(
            "test_this_test_leaves_a_job_thread_behind did not run first (-k or reordering)"
        )
    assert not _left_by_the_test_before[0].isRunning(), "the leaked thread outlived its test"
