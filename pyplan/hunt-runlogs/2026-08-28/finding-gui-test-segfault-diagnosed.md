# The GUI-test segfault, diagnosed — 2026-08-28

Open bug with a dedicated spike branch (`spike/segfault-repro`, top commit *"run the GUI suite 20x on
CI to catch the segfault"*, on `e8a845f8` "the GUI tests crash on window count (#112)"). This run
replaced "twenty CI runs and hope" with a 45% local reproduction and a live gdb backtrace.

Box: yulon-ubuntu, Ubuntu 24.04.4, Python 3.12.3, PySide6 6.11.2, pytest 9.1.1, pluggy 1.6.0,
8 vCPU / 23 GB. `QT_QPA_PLATFORM` is not set in the shell — it defaults to `offscreen` at
`tests/conftest.py:13`.

## Rate — measured, not adjectival
| Suite | Runs | Failures | Rate |
|---|---|---|---|
| GUI-only (the 6 files using the `qapp` fixture: `test_catalog_view`, `test_controller_view`, `test_job`, `test_log_panel`, `test_main`, `test_prompt` — 149 tests) | 20 | 9 | **45%** |
| Non-GUI (everything else minus integration, ~821 tests) | 10 | 0 | **0%** |
| Full suite (970 tests, diluted) | 15 | 1 | ~7% |

**The spike's framing is aimed at the right target** — confirmed empirically, not assumed. But it
does not need twenty CI runs: isolating the six GUI files hits it in 2-3 runs on one VM.

Of the 9 failures: 6× SIGSEGV (rc=139), 2× SIGBUS (rc=135), 1× a clean deadlock. `dmesg -T`
independently confirms the crash is native and in a Qt thread:
`QThread[6383]: segfault at 47 ip 00000000004bb91b ... in python3.12`.

## Root cause — a GIL / Qt-mutex lock-order inversion
`pytest-randomly` is not installed, so order is fixed, which is why it lands at the same relative
position each time: the `test_job.py`/`test_log_panel.py` → `test_main.py` module boundary. Every
traceback has the same shape — the thread taking the signal has `<no Python frame>` (native Qt/C++),
while the visible Python thread is mid-fixture-setup for the next module.

The deadlock instance is the same race resolving cleanly instead of corrupting memory, caught live
with `gdb -p <pid> -batch -ex "thread apply all bt"`:

- **Main thread**: blocked in `QObjectPrivate::ConnectionData::cleanOrphanedConnectionsImpl` →
  `QBasicMutex::lockInternal()`, reached from a `QAbstractSlider::rangeChanged` signal invoking a
  Python slot that **re-enters `QCoreApplication::processEvents`**.
- **A thread named `QThread`**: blocked in `~QObject()` → `Shiboken::GilState::acquire()` →
  `PyGILState_Ensure` — it needs the GIL to run a Python-level override during C++ teardown.

Each holds what the other needs.

## The suspected component, and the connection worth noting
`yulon/ui/widgets/job.py:72` — `ThreadedJobRunner`'s own docstring: *"destroyed while running aborts
the process rather than warning."* `tests/test_main.py`'s module-scoped `_app_window` fixture carries
the identical warning at teardown next to its `main._stop_background_threads(window)`. Six idle
worker threads, all blocked on `PyEval_RestoreThread`, were still alive in the hung process —
consistent with `ThreadedJobRunner` QThreads from earlier tests not being fully joined before the
next module's fixture builds a new window.

**The connection the diagnosis stops just short of:** that fixture's teardown is

    main._stop_background_threads(window)
    QApplication.processEvents()

and the gdb backtrace names *re-entering `processEvents`* as how the main thread reached the
contended Qt mutex. So the teardown's own `processEvents()` — running events while a QThread may
still be inside `~QObject()` waiting on the GIL — is a prime candidate for the trigger, not merely a
bystander. Worth testing directly: does the crash rate move if that call is removed, or if the
threads are joined with a wait before it?

## Status
**Diagnosis only. No fix attempted, deliberately** — a precise reproduction handed to an existing
spike branch is worth more than a guessed patch, and the fix here touches thread teardown, where a
wrong guess is worse than the bug.

Raw logs on yulon-ubuntu under `~/segfault-diag/` (`full_*.log`, `gui/run_*.log`, `nogui/run_*.log`).

---

# CORRECTION — the fixing pass found the real cause (2026-08-28, later the same day)

The diagnosis above was of a **hung** run and is a real bug (trap 3 below), but it is **not** the
crash the loops were counting. Fixing it did not move the rate (1/20 → 5/20, noise). Running the six
GUI files **under gdb** reproduced the counted crash on run 1, with native frames:

    Thread "QThread" received signal SIGBUS
    #0 QMetaMethod::name()
    #2 PySide::SignalManager::qt_metacall
    #4 QThread::started(QThread::QPrivateSignal)

Only two threads alive; main blocked in `take_gil`; no Python frame on the worker. A freshly
scheduled thread emits `started`, PySide looks `worker.run` up on the receiver, and **the worker's
C++ object is already freed**: the panel/view/local holding its only Python reference was collected
between `thread.start()` and the OS scheduling the thread. Scheduling latency is the window — which
is exactly why the rate tracked load and why gdb made it near-deterministic.

Three sites carried it, found one at a time because each fix only moved the crash:
1. `LogPanel.run()` — panel GC'd → worker gone (crash at the controller_view tests).
2. `ThreadedJobRunner.__call__` — `_live` died with the view.
3. `main.py build_window()` — `setProperty("update_worker", ...)` "to keep references alive": **a Qt
   property holds a `QObject*`, not a Python reference.** The worker died when the function returned.
   Crash at test 113 = the end of `test_main.py`.

Fix: `job.InFlight`, a GUI-thread QObject owning every started pair until `finished` reaches its
queued `sweep()`; threads unparented so a collected owner cannot destroy a running QThread (abort).
Deterministic RED on Linux against the old code: **`Aborted (core dumped)`, exit 134**. GREEN after.

Gate: 25 runs under gdb, plus the plain 20-run loop.
