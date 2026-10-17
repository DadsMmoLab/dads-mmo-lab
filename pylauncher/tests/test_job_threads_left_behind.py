"""The suite joins, at each test's end, the job threads that test left running (T769).

A view built with real job threads and never pumped leaves each job's thread waiting in its
event loop for a `quit` queued to the GUI thread. Before T764 the run aborted after its
summary for it; T764 joined them all at the end of the run, which kept the run alive but
blamed a job that HUNG on no test at all. The autouse fixture in `conftest.py` joins them per
test instead, and since T771 fails the test that left one. These tests drive the very
generator it runs, with bounds of their own, and the last runs a pytest session under the
fixture itself.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import pytest
from PySide6.QtCore import QObject, QThread, Slot

from tests.conftest import (
    HANG_BOUND,
    HANG_BOUND_MS,
    joining_the_job_threads_left_behind,
    pump_until,
    take_every_held_answer,
)
from yulon.ui.widgets.job import ThreadedJobRunner, in_flight

pytest_plugins = ["pytester"]

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


def test_a_job_its_test_never_pumped_fails_that_test_and_is_joined(qapp: object) -> None:
    """The five files' shape: the work is done, the thread waits for a quit nobody delivers.
    Since T771 that fails the test (its answer would land in whichever test pumps next), and
    the thread is still joined, so it cannot outlive the test either."""
    owner = _Owner()
    ran = threading.Event()
    guard = joining_the_job_threads_left_behind(HANG_BOUND_MS)
    next(guard)

    def unpumped_work() -> object:
        ran.set()
        return "answer"

    thread = _start(owner, unpumped_work)
    assert ran.wait(HANG_BOUND), "the job never ran"
    assert thread.isRunning(), "premise: unpumped, the thread waits for its queued quit"
    with pytest.raises(pytest.fail.Exception) as failed:
        next(guard, None)
    said = str(failed.value)
    assert "never took" in said and "unpumped_work" in said, said
    take_every_held_answer()
    assert not thread.isRunning(), "the guard did not join the thread the test left"


def test_a_thread_joined_directly_with_its_answer_still_queued_fails_the_test(
    qapp: object,
) -> None:
    """An exit join or `wait_all` ends the thread, but the answer is still queued to the GUI
    thread: the thread is no longer running, and the answer would land in whichever test
    pumps next (T771 round 2). The guard must look at held threads, not only running ones."""
    owner = _Owner()
    ran = threading.Event()
    guard = joining_the_job_threads_left_behind(HANG_BOUND_MS)
    next(guard)

    def joined_but_unanswered() -> object:
        ran.set()
        return "answer"

    thread = _start(owner, joined_but_unanswered)
    assert ran.wait(HANG_BOUND), "the job never ran"
    thread.quit()
    assert thread.wait(int(HANG_BOUND * 1000)), "premise: the direct join must end the thread"
    assert not thread.isRunning()
    assert owner.results == [], "premise: nothing has taken the answer"
    with pytest.raises(pytest.fail.Exception) as failed:
        next(guard, None)
    assert "never took" in str(failed.value) and "joined_but_unanswered" in str(failed.value)
    take_every_held_answer()


def test_draining_a_joined_threads_answer_does_not_hide_a_running_unpumped_job(
    qapp: object,
) -> None:
    """A file that calls `take_every_held_answer()` after its joins must still have the guard
    fail a job it never pumped: the helper takes the answers of ended threads only (T771
    round 3; it used to pump every held answer, which switched the guard off)."""
    owner = _Owner()
    ran = threading.Event()
    guard = joining_the_job_threads_left_behind(HANG_BOUND_MS)
    next(guard)

    def running_and_unpumped() -> object:
        ran.set()
        return "answer"

    thread = _start(owner, running_and_unpumped)
    assert ran.wait(HANG_BOUND), "the job never ran"
    assert thread.isRunning(), "premise: unpumped, the thread waits for its queued quit"
    # A second job, joined directly as a test's shutdown would: only ITS answer may be taken.
    second_ran = threading.Event()
    second_owner = _Owner()

    def joined_second() -> object:
        second_ran.set()
        return "second"

    second = _start(second_owner, joined_second)
    assert second_ran.wait(HANG_BOUND), "the second job never ran"
    second.quit()
    assert second.wait(int(HANG_BOUND * 1000)), "premise: the direct join must end the thread"
    take_every_held_answer()
    assert second_owner.results == ["second"], "the helper did not take the joined job's answer"
    assert owner.results == [], "the helper took the answer of a job still running"
    with pytest.raises(pytest.fail.Exception) as failed:
        next(guard, None)
    assert "never took" in str(failed.value) and "running_and_unpumped" in str(failed.value)
    take_every_held_answer()


def test_draining_leaves_the_answer_of_a_thread_held_before_the_test_to_its_owner(
    qapp: object,
) -> None:
    """A module fixture's job, ended and unanswered when the test began, is not the test's."""
    owner = _Owner()
    ran = threading.Event()

    def before_work() -> object:
        ran.set()
        return "early"

    thread = _start(owner, before_work)
    assert ran.wait(HANG_BOUND), "the job never ran"
    thread.quit()
    assert thread.wait(int(HANG_BOUND * 1000)), "premise: the direct join must end the thread"
    guard = joining_the_job_threads_left_behind(HANG_BOUND_MS)
    next(guard)
    take_every_held_answer()
    assert owner.results == [], "the helper took an answer held before the test began"
    next(guard, None)
    pump_until(lambda: owner.results == ["early"], "the owner's answer never arrived")


def test_a_job_whose_answer_arrived_is_not_a_leak(qapp: object) -> None:
    """Its answer taken, the thread may still be on its way out (its `quit` is queued behind
    the answer, and a test's pump can stop between the two): the guard joins it and the test
    passes. Held in exactly that state here: only the answer's own events are delivered."""
    from PySide6.QtCore import QCoreApplication

    owner = _Owner()
    guard = joining_the_job_threads_left_behind(HANG_BOUND_MS)
    next(guard)
    thread = _start(owner, lambda: "answer")
    delivery = in_flight()._pairs[-1][2]
    assert delivery is not None
    deadline = time.monotonic() + HANG_BOUND
    while not delivery.delivered and time.monotonic() < deadline:
        QCoreApplication.sendPostedEvents(delivery, 0)
        time.sleep(0.01)
    assert owner.results == ["answer"], "the job's answer never arrived"
    assert thread.isRunning(), "premise: the thread's quit is still queued"
    next(guard, None)
    assert not thread.isRunning(), "the guard did not join the thread"


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


_A_SUITE_RUN_UNDER_THE_REAL_FIXTURE = """
import threading

from tests.conftest import HANG_BOUND
from tests.test_job_threads_left_behind import _Owner, _start

left = []


def test_a_leaks_a_job_thread(qapp):
    ran = threading.Event()

    def leaked_work():
        ran.set()
        return "answer"

    thread = _start(_Owner(), leaked_work)
    assert ran.wait(HANG_BOUND), "the job never ran"
    assert thread.isRunning(), "premise: unpumped, the thread waits for its queued quit"
    left.append(thread)


def test_b_finds_it_joined():
    assert left, "test_a did not run first"
    assert not left[0].isRunning(), "the leaked thread outlived its test"
"""


def test_the_suites_own_fixture_fails_and_joins_a_leak_across_a_real_test_boundary(
    pytester: pytest.Pytester, qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end, in ONE test (T771): a pytest session in this process, under the very
    autouse fixture `conftest.py` registers, runs a test that leaks an unpumped job thread
    and a second that looks at it. Before, the pair ran as two tests of this file, and with
    `-n auto --dist load` they often landed on different workers: the second skipped, and
    the fixture switched off went unnoticed in CI."""
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    pytester.makeconftest(
        "from tests.conftest import _job_threads_a_test_leaves_running_are_joined, qapp\n"
    )
    pytester.makepyfile(test_pair=_A_SUITE_RUN_UNDER_THE_REAL_FIXTURE)
    result = pytester.runpytest_inprocess(
        "-p", "no:xdist", "-p", "no:randomly", "-p", "no:cacheprovider", "-p", "no:repeat"
    )
    result.assert_outcomes(passed=2, errors=1)
    result.stdout.fnmatch_lines(["*ERROR at teardown of test_a_leaks_a_job_thread*"])
    result.stdout.fnmatch_lines(["*never took*leaked_work*"])
    take_every_held_answer()


def test_a_held_thread_deleted_under_its_wrapper_is_swept_not_raised_on(qapp: object) -> None:
    """CI (py3.11) met `RuntimeError: ... QThread already deleted` from `InFlight` at teardown.

    A held pair's thread can be destroyed while its Python wrapper is still in the list;
    `sweep()`, `wait_all()` and `still_running()` must treat it as finished, not raise, or one
    deleted thread stops the exit join for every other job.
    """
    import shiboken6
    from PySide6.QtCore import QObject, QThread

    from yulon.ui.widgets.job import InFlight

    held = InFlight()
    thread = QThread()
    held.hold(thread, QObject())
    shiboken6.delete(thread)

    assert held.still_running() == []
    assert held.wait_all(timeout_ms=100) is True
    assert held._pairs == []
