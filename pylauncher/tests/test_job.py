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
import sys
import threading
import time
from pathlib import Path

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


_UI = Path(__file__).resolve().parents[1] / "yulon" / "ui"

_HANDS_TO_THE_RUNNER = frozenset({"_run", "_jobs", "_read_alongside"})
"""The names a view or panel hands a job to the runner through.

`_jobs` is the runner itself; `_run` and `_read_alongside` are the one-line
wrappers that pass their callbacks straight on to it.
"""


def _job_callbacks_that_are_not_bound_slots() -> list[str]:
    """Every callback handed to the runner in `yulon/ui` that is not `self.<a @Slot>`.

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
    """T97. The rule this module's docstring states, checked rather than trusted.

    A lambda or a closure handed to the runner as `on_done` is delivered on the
    WORKER thread, and whatever it does to a widget it does from there. Two
    call sites broke the rule: the Characters tab's list refresh -- measured on
    m910q 2026-09-23 segfaulting the app in 4 of 5 Send gold runs on a
    900-character bot server, the worker's frame in `character_list.clear()` --
    and the Tuning panel's restart/recreate completion, which set widget text
    and started a thread from the worker. Both read as ordinary code at the call
    site; this names each one.
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
