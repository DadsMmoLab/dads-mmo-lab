"""Tests for `yulon.ui.widgets.job` — the background-job runner behind every view action.

The regression that motivates the first test: a runner that did not keep a
reference to its worker looked correct and did nothing at all. PySide6 connects
to a bound method through a weak reference, so the worker was collected the
moment the factory returned and its `run` slot never fired — on the test VM the
Server tab sat on "status: unknown" and Start on "starting…" forever, with no
error anywhere.
"""

from __future__ import annotations

import ast
import gc
import sys
import threading
import time
import weakref
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Slot

from tests.conftest import HANG_BOUND, HANG_BOUND_MS, process_events, pump_until, spelled_bounds
from yulon.ui.widgets.job import LineRelay, ThreadedJobRunner, in_flight, run_inline

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


def _on_the_gui_thread() -> bool:
    """Both ways of asking, because they are two different facts to a Qt program."""
    from PySide6.QtCore import QCoreApplication, QThread

    app = QCoreApplication.instance()
    return (
        threading.current_thread() is threading.main_thread()
        and app is not None
        and QThread.currentThread() is app.thread()
    )


def _held(delivery: object) -> bool:
    return any(d is delivery for *_, d in in_flight()._pairs)


def _undelivered() -> bool:
    return any(d is not None and not d.returned for *_, d in in_flight()._pairs)


@pytest.mark.parametrize("outcome", ["done", "failed"])
def test_a_plain_lambda_callback_runs_on_the_gui_thread(qapp: object, outcome: str) -> None:
    """T97, and the boundary this runner now IS rather than a rule callers keep.

    A lambda connected straight to the worker's signal ran on the worker: the
    Characters tab cleared and refilled its list from there and the app
    segfaulted on m910q (2026-09-23, 4 of 5 Send gold runs). The runner now
    hands every answer to a GUI-thread object first, so a lambda -- the shape
    that had no thread of its own -- lands where the widgets are, on both arms.
    """
    owner = QObject()
    runner = ThreadedJobRunner(owner)
    landed: list[tuple[object, bool]] = []

    def work() -> str:
        if outcome == "failed":
            raise RuntimeError("no docker")
        return "hello"

    runner(
        work,
        lambda result: landed.append((result, _on_the_gui_thread())),
        lambda exc: landed.append((str(exc), _on_the_gui_thread())),
    )
    pump_until(lambda: bool(landed), f"the {outcome} callback ran")
    assert runner.wait(HANG_BOUND_MS) is True

    expected = "no docker" if outcome == "failed" else "hello"
    assert landed == [(expected, True)], "a lambda callback ran off the GUI thread"


def test_an_answer_for_a_deleted_owner_is_dropped_not_delivered(qapp: object) -> None:
    """The behaviour a bound slot had for free, kept now that the runner calls it.

    Qt disconnected a slot whose view had been destroyed, so a job finishing
    after its tab closed called nothing. The runner calls the callable itself
    now, so it asks first: the runner's owner deleted means nothing is called,
    even for a lambda that would touch the dead view -- where calling it would
    be "Internal C++ object already deleted" at best.
    """
    import shiboken6

    owner = QObject()
    runner = ThreadedJobRunner(owner)
    gate = threading.Event()
    called: list[object] = []

    def work() -> str:
        gate.wait()
        return "late"

    runner(work, called.append, called.append)
    shiboken6.delete(owner)
    gate.set()
    assert runner.wait(HANG_BOUND_MS) is True
    pump_until(lambda: not _undelivered(), "the answer was taken off the queue")
    assert called == [], "a callback ran for an owner that no longer exists"


def test_a_bound_callback_whose_object_was_deleted_is_not_called(qapp: object) -> None:
    """The narrower case: the runner's owner lives, the callback's object does not."""
    import shiboken6

    runner = ThreadedJobRunner(QObject())
    receiver = _Receiver()
    seen, errors = receiver.results, receiver.errors
    gate = threading.Event()
    runner(lambda: gate.wait() and "late", receiver.done, receiver.failed)
    shiboken6.delete(receiver)
    gate.set()
    assert runner.wait(HANG_BOUND_MS) is True
    pump_until(lambda: not _undelivered(), "the answer was taken off the queue")
    assert seen == [] and errors == [], "a slot ran on an object that no longer exists"


def test_a_delivery_is_held_until_it_delivered_and_released_after(qapp: object) -> None:
    """The lifetime half (see `InFlight`): the GUI-thread object carrying the answer
    must outlive the thread it listens to until its queued slot has run, and must
    not outlive that -- a closure over a view would keep the view with it."""
    owner = QObject()
    runner = ThreadedJobRunner(owner)
    results: list[object] = []
    runner(lambda: 7, lambda r: results.append(r), results.append)
    delivery = weakref.ref(in_flight()._pairs[-1][2])  # type: ignore[arg-type]
    assert runner.wait(HANG_BOUND_MS) is True
    assert delivery() is not None, "dropped with its answer still queued"
    pump_until(lambda: results == [7], "the answer arrived")
    pump_until(lambda: (gc.collect(), delivery() is None)[1], "the delivery was released")


def test_a_delivery_is_held_while_its_callback_runs_a_nested_event_loop(qapp: object) -> None:
    """Cold review of the runner change: the delivery was marked done and its
    sweep scheduled BEFORE the callback ran. A callback that runs a nested loop
    -- every QMessageBox and `exec()` a view shows from an answer -- let the
    thread finish and the sweep drop the delivery while its own slot was still
    on the stack (probe: 300/300). It is held until the callback RETURNS.
    """
    runner = ThreadedJobRunner(QObject())
    during: list[bool] = []

    def modal(_result: object) -> None:
        # A nested loop, as a dialog shown from an answer runs one: spun until
        # the thread has finished and its `finished` -> sweep has been delivered.
        pump_until(lambda: not thread.isRunning(), "the thread finished inside the callback")
        process_events()
        during.append(_held(delivery()))

    runner(lambda: 3, modal, modal)
    thread, _worker, held = in_flight()._pairs[-1]
    delivery = weakref.ref(held)  # type: ignore[arg-type]
    del held
    pump_until(lambda: bool(during), "the callback's nested loop saw the thread finish")
    assert during == [True], "the delivery was released while its own callback was running"
    pump_until(lambda: (gc.collect(), delivery() is None)[1], "the delivery was released after")


def test_a_callback_that_raises_is_logged_and_the_next_job_still_delivers(
    qapp: object, caplog: pytest.LogCaptureFixture
) -> None:
    """Cold review: a callback's exception went to stderr through PySide, which a
    windowed Windows build does not have -- it vanished. It goes to the log."""
    runner = ThreadedJobRunner(QObject())
    later: list[object] = []

    def boom(_result: object) -> None:
        raise RuntimeError("the view fell over")

    with caplog.at_level("ERROR", logger="yulon.ui.widgets.job"):
        runner(lambda: 1, boom, boom)
        pump_until(lambda: not _undelivered(), "the raising callback returned")
        runner(lambda: 2, later.append, later.append)
        pump_until(lambda: later == [2], "the next job's answer")
    assert runner.wait(HANG_BOUND_MS) is True
    assert any(
        "the view fell over" in r.getMessage()
        or (r.exc_info and "the view fell over" in str(r.exc_info[1]))
        for r in caplog.records
    ), caplog.text


def test_a_job_with_no_callback_is_released_and_said(
    qapp: object, caplog: pytest.LogCaptureFixture
) -> None:
    """Cold review: a `None` callback never marked the delivery done, so the
    pair was held for the life of the app."""
    runner = ThreadedJobRunner(QObject())
    with caplog.at_level("WARNING", logger="yulon.ui.widgets.job"):
        runner(lambda: 1, None, None)  # type: ignore[arg-type]
        delivery = weakref.ref(in_flight()._pairs[-1][2])  # type: ignore[arg-type]
        assert runner.wait(HANG_BOUND_MS) is True
        pump_until(lambda: (gc.collect(), delivery() is None)[1], "the delivery was released")
    assert "no callback" in caplog.text


class _NotAnException(BaseException):
    """What `_JobWorker.run` does not catch: it catches `Exception` only."""


def test_a_job_ended_by_a_base_exception_releases_its_thread_and_delivery(
    qapp: object, caplog: pytest.LogCaptureFixture
) -> None:
    """Cold review: a `BaseException` from the work emitted nothing, so the
    thread was never told to quit and the delivery waited forever."""
    runner = ThreadedJobRunner(QObject())
    called: list[object] = []

    def work() -> None:
        raise _NotAnException("stop")

    with caplog.at_level("ERROR", logger="yulon.ui.widgets.job"):
        runner(work, called.append, called.append)
        delivery = weakref.ref(in_flight()._pairs[-1][2])  # type: ignore[arg-type]
        thread = in_flight()._pairs[-1][0]
        pump_until(lambda: not thread.isRunning(), "the thread finished")
        pump_until(lambda: (gc.collect(), delivery() is None)[1], "the delivery was released")
    assert called == [], "a job with no answer answered"
    assert "_NotAnException" in caplog.text
    ended = [r for r in caplog.records if "_NotAnException" in r.getMessage()]
    assert ended and ended[0].exc_info is not None, "logged without its traceback"


_SYSTEM_EXIT_IN_A_JOB = """
import gc
import sys

from PySide6.QtCore import QCoreApplication, QDeadlineTimer, QEventLoop, QObject

app = QCoreApplication([])
from yulon.ui.widgets.job import ThreadedJobRunner, in_flight  # noqa: E402

owner = QObject()
runner = ThreadedJobRunner(owner)
called, later = [], []


def pump(until):
    deadline = QDeadlineTimer(60_000)
    while not until() and not deadline.hasExpired():
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)
    return until()


def work():
    raise SystemExit(3)


runner(work, called.append, called.append)
thread, _worker, delivery = in_flight()._pairs[-1]
thread_ended = pump(lambda: not thread.isRunning())
released = pump(lambda: all(d is not delivery for *_, d in in_flight()._pairs))
runner(lambda: 5, later.append, later.append)
answered = pump(lambda: later == [5])
print("STILL RUNNING", thread_ended, released, answered, called, later, flush=True)
# Joined before exit, as `main()` joins every job: a QThread still running at
# interpreter exit is Qt's own abort, and would read as this test's failure
# (measured: the second job's thread, answered but not yet finished).
in_flight().wait_all(60_000)
"""
"""A job whose work raises `SystemExit`, in a process of its own: before the
fix, the re-raise after `abandoned` took the process down (3.13: abort 4 of 4),
and a test that did that in-process would take the suite with it."""


def test_a_system_exit_in_a_job_ends_the_job_and_not_the_process() -> None:
    """Scoped re-review: `SystemExit` from the work was re-raised after
    `abandoned`, and PySide does not swallow it -- the process aborted or
    wedged. Python's own `threading` ignores `SystemExit` in a thread; so does
    this runner now. The thread ends, the delivery is released, nothing is
    called, the next job still answers, and the process is still there to say so.
    """
    import os
    import subprocess

    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    done = subprocess.run(
        [sys.executable, "-c", _SYSTEM_EXIT_IN_A_JOB],
        env=env,
        capture_output=True,
        text=True,
        timeout=HANG_BOUND,
    )

    assert done.returncode == 0, f"the process did not survive:\n{done.stdout}\n{done.stderr}"
    assert "STILL RUNNING True True True [] [5]" in done.stdout, done.stdout + done.stderr
    assert done.stderr.count("Traceback") <= 1, f"the traceback was printed twice:\n{done.stderr}"


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
    assert spelled_bounds(__file__) == {"HANG_BOUND", "HANG_BOUND_MS", "STILL_RUNNING"}


_UI = Path(__file__).resolve().parents[1] / "yulon" / "ui"

_HANDS_TO_THE_RUNNER = frozenset({"_run", "_jobs", "_read_alongside"})
"""The names a view or panel hands a job to the runner through.

`_jobs` is the runner itself; `_run` and `_read_alongside` are the one-line
wrappers that pass their callbacks straight on to it.
"""


def _job_callbacks_that_are_not_bound_slots() -> list[str]:
    """Every callback handed to the runner in `yulon/ui` that is not `self.<a @Slot>`.

    Lint, not the thread-safety boundary: the runner delivers every callback on
    the GUI thread itself (`_Delivery`), whether or not this passes.

    What it does NOT see, so a pass is read as no more than it is:

    * Slots are looked up per FILE, not per class. A name decorated `@Slot` in
      one class of a file passes for a same-named plain method in another class
      of that file -- `party_panel.py` defines `add_named` and `link_account`
      twice, and `log_panel.py` defines `run` twice.
    * Only calls on `self.` are examined; a runner reached as `panel._run(...)`
      or held in a local variable is not.
    * `*args` spread into the call is ignored, and so is anything reached only
      through it.
    * The first positional argument is taken to be the work and skipped, which
      is the shape of every wrapper here; one that took its callbacks first
      would have its work checked and a callback skipped.
    * Only `yulon/ui` is walked. A runner used outside it is not checked.
    """
    found = []
    for path in sorted(_UI.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        slots = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and any("Slot" in ast.unparse(d) for d in node.decorator_list)
        }
        for function in ast.walk(tree):
            if not isinstance(function, ast.FunctionDef):
                continue
            passing_on = (
                {a.arg for a in function.args.args}
                if function.name in _HANDS_TO_THE_RUNNER
                else set()
            )
            for call in ast.walk(function):
                if not (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id == "self"
                    and call.func.attr in _HANDS_TO_THE_RUNNER
                ):
                    continue
                for callback in [*call.args[1:], *(k.value for k in call.keywords)]:
                    bound = (
                        isinstance(callback, ast.Attribute)
                        and isinstance(callback.value, ast.Name)
                        and callback.value.id == "self"
                        and callback.attr in slots
                    )
                    handed_on = isinstance(callback, ast.Name) and callback.id in passing_on
                    if not (bound or handed_on):
                        found.append(
                            f"{path.relative_to(_UI.parent.parent)}:{call.lineno} "
                            f"{ast.unparse(callback)[:70]}"
                        )
    return found


def test_every_job_callback_in_the_ui_is_a_bound_slot() -> None:
    """T97. SUPPLEMENTARY lint now, not the safety boundary.

    The boundary is the runner: `ThreadedJobRunner` hands every answer to a
    GUI-thread `_Delivery`, so a lambda or closure runs on the GUI thread
    whatever it is (`test_a_plain_lambda_callback_runs_on_the_gui_thread`).
    This check came first, when the rule was the caller's to keep: two call
    sites broke it -- the Characters tab's list refresh, measured on m910q
    2026-09-23 segfaulting the app in 4 of 5 Send gold runs on a 900-character
    bot server, and the Tuning panel's restart/recreate completion.

    It stays because a bound slot still carries one thing a lambda cannot: an
    object the delivery can ask whether it is still alive, rather than only
    the runner's owner. It is lint -- see the limits on the helper above -- and
    a pass says nothing about thread safety that the runner's own tests do not.
    """
    assert _job_callbacks_that_are_not_bound_slots() == []


def test_the_callback_guard_sees_a_lambda(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard above answers [] -- which is also what a guard that looks at
    nothing answers. So it is pointed at a file with the defect in it."""
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "view.py").write_text(
        "class V:\n"
        "    @Slot(object)\n"
        "    def good(self, x): ...\n"
        "    def plain(self, x): ...\n"
        "    def press(self):\n"
        "        self._run(work, self.good, self.good)\n"
        "        self._run(work, lambda r: self.good(r), self.good)\n"
        "        self._jobs(work, self.plain, self.good)\n"
        "        self._run(work, self.make('x'), self.good)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "_UI", ui)
    found = _job_callbacks_that_are_not_bound_slots()
    assert [line.split(" ", 1)[1] for line in found] == [
        "lambda r: self.good(r)",
        "self.plain",
        "self.make('x')",
    ]
