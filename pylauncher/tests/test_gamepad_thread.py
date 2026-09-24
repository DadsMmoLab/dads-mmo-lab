"""The gamepad poller's thread: who owns it, where it is released, and how it is joined (T111).

`GamepadSource` runs a 120 Hz SDL poll loop on a `QThread` -- live only with a
controller plugged in, which is every Steam Deck. Until T111 it built that pair
the way the memory note on the GUI segfault says not to: `QThread(self)`
parented to the source (so to the main window), and
`thread.finished -> worker.deleteLater`. Measured with the fake pad below on
the old code: the worker's C++ destructor ran on the WORKER thread (trap 3),
and a window going away while the thread still ran was
`QThread: Destroyed while thread '' is still running` and exit 134 -- which the
app reached at exit whenever SDL took longer than `stop()`'s 500 ms to let go,
because nothing else joined the thread.

No controller exists on a test box, so `FakeSdl` stands in for `pygame` and
`pygame._sdl2.controller`: one pad whose buttons the test presses, a poll
counter per thread, and a gate that can hold a poll.

The scenarios that failed on the old code run in a child process -- several of
them aborted or segfaulted it -- so a regression fails one test instead of
ending the suite.
"""

from __future__ import annotations

import gc
import os
import subprocess
import sys
import threading
import time
import types
import weakref
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from tests.conftest import HANG_BOUND, HANG_BOUND_MS, pump_until, spelled_bounds

SDL_SLOW_RELEASE = 0.8
"""How long the fake SDL takes to let go of the pad at the end of a poll. Not a deadline.

It only has to be longer than the 500 ms `GamepadSource.stop()` waited before
T111, which was the only join the gamepad thread had: `_stop_background_threads`
then returned with the thread running (measured 0.504 s) and deleting the
window aborted. A slower box only makes the release slower still.
"""

CYCLES = 25
"""Start/stop cycles in the churn scenario. A count, not a clock."""


class FakeSdl:
    """`pygame` + `pygame._sdl2.controller` with one fake pad the test drives."""

    def __init__(self) -> None:
        self.pressed: set[int] = set()
        self.polls = 0  # SDL updates made, by every poller
        self.unplugged = False
        self.slow_release = False
        self.gate = threading.Event()
        self.gate.set()
        self.held = threading.Event()
        # SDL's controller subsystem is process-global: whoever inits it last
        # and whoever quits it first decide it for every thread. `holder` is the
        # thread that inited it and has not quit; anything another thread does
        # while it holds, or an update by a thread that no longer holds, is a
        # violation -- two pollers sharing, or one tearing it down under another.
        self.holder: str | None = None
        self.violations: list[str] = []
        self.inits = 0
        self._lock = threading.Lock()

    def _who(self) -> str:
        return threading.current_thread().name + f"#{threading.get_ident()}"

    def _init(self) -> None:
        with self._lock:
            me = self._who()
            if self.holder is not None and self.holder != me:
                self.violations.append(f"init by {me} while {self.holder} holds SDL")
            self.holder = me
            self.inits += 1

    def _quit(self) -> None:
        with self._lock:
            me = self._who()
            if self.holder is not None and self.holder != me:
                self.violations.append(f"quit by {me} tore SDL down under {self.holder}")
            self.holder = None

    def _update(self) -> None:
        with self._lock:
            me = self._who()
            if self.holder != me:
                self.violations.append(f"update by {me} while SDL is held by {self.holder}")

    def modules(self) -> dict[str, types.ModuleType]:
        fake = self

        class FakeError(Exception):
            pass

        class Pad:
            def __init__(self, _index: int) -> None:
                pass

            def get_button(self, button: int) -> bool:
                return button in fake.pressed

            def get_axis(self, _axis: int) -> int:
                return 0

            def quit(self) -> None:
                pass

        def update() -> None:
            if not fake.gate.is_set():
                fake.held.set()
                fake.gate.wait()
            if fake.unplugged:
                raise FakeError("the pad went away")
            fake._update()
            fake.polls += 1

        def controller_quit() -> None:
            if fake.slow_release and threading.current_thread() is not threading.main_thread():
                time.sleep(SDL_SLOW_RELEASE)
            fake._quit()

        controller = types.ModuleType("pygame._sdl2.controller")
        controller.init = fake._init  # type: ignore[attr-defined]
        controller.quit = controller_quit  # type: ignore[attr-defined]
        controller.get_count = lambda: 1  # type: ignore[attr-defined]
        controller.update = update  # type: ignore[attr-defined]
        controller.Controller = Pad  # type: ignore[attr-defined]
        sdl2 = types.ModuleType("pygame._sdl2")
        sdl2.controller = controller  # type: ignore[attr-defined]
        joystick = types.ModuleType("pygame.joystick")
        joystick.init = lambda: None  # type: ignore[attr-defined]
        joystick.quit = lambda: None  # type: ignore[attr-defined]
        pygame = types.ModuleType("pygame")
        pygame.error = FakeError  # type: ignore[attr-defined]
        pygame.joystick = joystick  # type: ignore[attr-defined]
        pygame._sdl2 = sdl2  # type: ignore[attr-defined]
        return {
            "pygame": pygame,
            "pygame._sdl2": sdl2,
            "pygame._sdl2.controller": controller,
            "pygame.joystick": joystick,
        }


def _window() -> object:
    """A shown window with two buttons, so a D-pad press has somewhere to go."""
    from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

    window = QWidget()
    layout = QVBoxLayout(window)
    layout.addWidget(QPushButton("first"))
    layout.addWidget(QPushButton("second"))
    window.show()
    return window


def _polling(fake: FakeSdl) -> Callable[[], bool]:
    return lambda: fake.polls > 0


@pytest.fixture
def fake_sdl(monkeypatch: pytest.MonkeyPatch, qapp: object) -> Iterator[FakeSdl]:
    """The fake pad in `sys.modules`; every gamepad thread is joined before it is taken away."""
    from yulon.ui.widgets.job import in_flight

    fake = FakeSdl()
    for name, module in fake.modules().items():
        monkeypatch.setitem(sys.modules, name, module)
    yield fake
    fake.gate.set()
    assert in_flight().wait_all(HANG_BOUND_MS), "a gamepad thread outlived its test"


# ------------------------------------------------------------ in-process


def test_pad_input_reaches_the_navigator_on_the_gui_thread(
    fake_sdl: FakeSdl, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A D-pad press and a B press move focus / act from the GUI thread, never the poller's."""
    from yulon.ui import gamepad

    gui = threading.get_ident()
    seen: list[tuple[str, bool]] = []
    navigate, perform = gamepad.Navigator.navigate, gamepad.Navigator.perform

    def recording_navigate(self: gamepad.Navigator, direction: gamepad.Direction) -> bool:
        seen.append(("navigate", threading.get_ident() == gui))
        return navigate(self, direction)

    def recording_perform(self: gamepad.Navigator, action: gamepad.Action) -> bool:
        seen.append(("perform", threading.get_ident() == gui))
        return perform(self, action)

    monkeypatch.setattr(gamepad.Navigator, "navigate", recording_navigate)
    monkeypatch.setattr(gamepad.Navigator, "perform", recording_perform)
    window = _window()
    _navigator, _keyboard, source = gamepad.install_gamepad_navigation(window)  # type: ignore[arg-type]
    try:
        fake_sdl.pressed.add(gamepad.BTN_DPAD_DOWN)
        pump_until(lambda: any(name == "navigate" for name, _ in seen), "a D-pad press")
        fake_sdl.pressed.clear()
        fake_sdl.pressed.add(gamepad.BTN_B)
        pump_until(lambda: any(name == "perform" for name, _ in seen), "a B press")
        fake_sdl.pressed.clear()
    finally:
        source.stop()
    assert seen and all(on_gui for _name, on_gui in seen), seen


# ---------------------------------------------------- in a child process


def _child(scenario: str) -> None:
    """One scenario that aborted the process on the old code. Prints CHILD-OK on success."""
    from PySide6.QtCore import qInstallMessageHandler
    from PySide6.QtWidgets import QApplication

    fake = FakeSdl()
    sys.modules.update(fake.modules())
    _app = QApplication.instance() or QApplication([])
    messages: list[str] = []
    qInstallMessageHandler(lambda _kind, _where, text: messages.append(text))

    import shiboken6

    from yulon.ui import gamepad
    from yulon.ui.widgets.job import in_flight

    window = _window()
    _navigator, keyboard, source = gamepad.install_gamepad_navigation(window)  # type: ignore[arg-type]
    pump_until(_polling(fake), "the poller polled")
    first = source._thread
    assert first is not None

    if scenario in ("released-after-stop", "released-after-unplug"):
        # Trap 3: the worker must never be destroyed on its own thread.
        # `thread.finished -> worker.deleteLater` ran the Python-subclassed
        # worker's destructor there, needing the GIL off the GUI thread; the
        # old code fired `destroyed` off the GUI thread on both ways a poll ends,
        # and with this probe connected it segfaulted the unplug case outright.
        # DIRECT, so the probe runs on whichever thread destroys the worker: on
        # an auto connection PySide delivers a lambda by the sender's affinity,
        # and a worker deleted from the GUI thread while it belongs to the
        # (finished) poller thread then reports nothing at all (measured).
        from PySide6.QtCore import Qt

        gui = threading.get_ident()
        destroyed_on: list[bool] = []
        worker = source._worker
        assert worker is not None
        worker.destroyed.connect(
            lambda *_: destroyed_on.append(threading.get_ident() == gui),
            Qt.ConnectionType.DirectConnection,
        )
        alive = weakref.ref(worker)
        del worker
        if scenario == "released-after-stop":
            source.stop()
        else:
            fake.unplugged = True

        def released_on_record() -> bool:
            gc.collect()
            return alive() is None and bool(destroyed_on)

        pump_until(released_on_record, "the worker was released")
        assert destroyed_on == [True], "the worker was destroyed off the GUI thread (trap 3)"
        source.stop()
        assert in_flight().wait_all(HANG_BOUND_MS)
        shiboken6.delete(window)
    elif scenario == "slow-exit":
        # The app's own exit path, with SDL slower to let go than stop() waited.
        import main

        window.yulon_gamepad = source  # type: ignore[attr-defined]
        window.yulon_keyboard = keyboard  # type: ignore[attr-defined]
        fake.slow_release = True
        main._stop_background_threads(window)
        assert not first.isRunning(), "_stop_background_threads left the gamepad thread running"
        shiboken6.delete(window)
    elif scenario == "source-dropped":
        # The memory note's deterministic shape: hold the poll on a gate, drop the
        # only owner the app knows of, collect -- the worker must still be alive,
        # because `in_flight()` holds the pair until its thread has FINISHED.
        orphan = gamepad.GamepadSource(navigator=None)  # type: ignore[arg-type]
        orphan.start()
        worker, thread = orphan._worker, orphan._thread
        assert worker is not None and thread is not None
        alive = weakref.ref(worker)
        before = fake.polls
        pump_until(lambda: fake.polls > before, "the orphan's poller polled")
        fake.gate.clear()
        assert fake.held.wait(HANG_BOUND), "the poll never reached the gate"
        orphan.stop()
        del orphan, worker
        gc.collect()
        assert alive() is not None, "the worker was released while its thread still ran (trap 1)"
        assert thread.parent() is None, "a parented QThread dies with its parent, running or not"
        fake.gate.set()

        def released() -> bool:
            gc.collect()
            return alive() is None

        pump_until(released, "the worker was released after its thread finished")
        source.stop()
        assert in_flight().wait_all(HANG_BOUND_MS)
        shiboken6.delete(window)
    elif scenario == "window-deleted":
        # No stop(): the window goes while the pad is being polled.
        shiboken6.delete(window)
        pump_until(lambda: not first.isRunning(), "the orphaned poller stopped")
        assert in_flight().wait_all(HANG_BOUND_MS)
    elif scenario == "restart":
        # The old thread's finish arrives around the new pair's start; it must not take it.
        source.stop()
        source.start()
        pump_until(lambda: source._thread is not None, "the restarted poller started")
        second = source._thread
        assert second is not None and second is not first
        pump_until(lambda: not first.isRunning(), "the first poller finished")
        before = fake.polls
        pump_until(lambda: fake.polls > before, "the second poller kept polling")
        assert source._thread is second, "the old thread's finish cleared the new pair"
        source.stop()
        assert in_flight().wait_all(HANG_BOUND_MS)
        assert not second.isRunning(), "stop() could not reach the restarted poller"
        assert not fake.violations, fake.violations
        shiboken6.delete(window)
    elif scenario in ("restart-overlap", "restart-cancelled"):
        # SDL is global. Hold the old poll mid-update, `stop(); start()`, and
        # only then let it go: the old worker still has an update and its SDL
        # `quit()` to run. A new pair started meanwhile inits SDL under it and
        # has it torn down by that quit (Codex review of e135f880). The start
        # must wait for the old thread's `finished`; a second `stop()` before
        # then cancels it.
        fake.gate.clear()
        assert fake.held.wait(HANG_BOUND), "the poll never reached the gate"
        inits_before = fake.inits
        source.stop()
        source.start()
        if scenario == "restart-cancelled":
            source.stop()
        fake.gate.set()
        pump_until(lambda: not first.isRunning(), "the old poller finished")
        assert not fake.violations, fake.violations
        if scenario == "restart-overlap":
            pump_until(lambda: source._thread is not None, "the waiting start ran")
            before = fake.polls
            pump_until(lambda: fake.polls > before, "the new poller polled")
            second = source._thread
            assert second is not None and second.isRunning()
            assert fake.holder is not None, "no poller holds SDL"
        else:
            pump_until(lambda: source._stopping is None, "the old poller's finish arrived")
            assert source._thread is None, "a cancelled start still started a poller"
            assert fake.inits == inits_before, "a cancelled start still touched SDL"
            assert fake.holder is None, "SDL is still held with no poller"
        assert not fake.violations, fake.violations
        source.stop()
        assert in_flight().wait_all(HANG_BOUND_MS)
        shiboken6.delete(window)
    elif scenario == "churn":
        # Each restart is the `restart` race again: the previous thread's finish
        # reaches the GUI thread after the next pair has started.
        threads = [first]
        for _ in range(CYCLES):
            source.stop()
            source.start()
            pump_until(lambda: source._thread is not None, "the restarted poller started")
            current = source._thread
            assert current is not None
            previous = threads[-1]
            threads.append(current)
            pump_until(lambda p=previous: not p.isRunning(), "the previous poller finished")
            before = fake.polls
            pump_until(lambda b=before: fake.polls > b, "the restarted poller polled")
            assert source._thread is current, "a late finish cleared the running pair"
        source.stop()
        assert in_flight().wait_all(HANG_BOUND_MS)
        assert not any(t.isRunning() for t in threads), "a gamepad QThread leaked"
        assert not fake.violations, fake.violations
        shiboken6.delete(window)
    else:
        raise SystemExit(f"no scenario {scenario!r}")

    gc.collect()
    thread_warnings = [m for m in messages if "QThread" in m or "QObject" in m]
    assert not thread_warnings, thread_warnings
    print("CHILD-OK", flush=True)


_CHILD_ENTRY = """\
import sys
from tests.test_gamepad_thread import _child
_child(sys.argv[1])
"""


@pytest.mark.parametrize(
    "scenario",
    [
        "released-after-stop",
        "released-after-unplug",
        "slow-exit",
        "source-dropped",
        "window-deleted",
        "restart",
        "restart-overlap",
        "restart-cancelled",
        "churn",
    ],
)
def test_the_gamepad_thread_never_aborts_the_process(scenario: str) -> None:
    """Each of these failed, aborted (exit 134) or segfaulted before T111.

    * `released-after-stop`, `released-after-unplug` -- trap 3: the worker was
      destroyed on its own thread (`thread.finished -> worker.deleteLater`).

    * `slow-exit` -- the app's exit path: `main._stop_background_threads()`
      with SDL taking longer to release the pad than `stop()`'s old 500 ms
      wait. Nothing else joined the thread, and the parented QThread died
      with the window.
    * `source-dropped` -- a poll held on a gate while the source is dropped:
      the old parented QThread went with it (trap 1's shape from the memory note).
    * `window-deleted` -- the window goes while polling, with no `stop()`.
    * `restart` -- `stop(); start()`: the OLD thread's queued finish cleared
      the NEW pair, whose worker was collected before its `run` began.
    * `restart-overlap` -- `stop(); start()` while the old poll is still inside
      SDL: the new pair inited SDL under it and the old one's `quit()` tore it
      down under the new one. Added after the Codex review of the first fix.
    * `restart-cancelled` -- `stop(); start(); stop()`: the waiting start is dropped.
    * `churn` -- a restart twenty-five times over; no QThread may be left running.
    """
    pylauncher = Path(__file__).resolve().parent.parent
    done = subprocess.run(
        [sys.executable, "-c", _CHILD_ENTRY, scenario],
        cwd=pylauncher,
        env={**os.environ, "PYTHONPATH": str(pylauncher), "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        text=True,
        timeout=HANG_BOUND,
    )
    assert (
        done.returncode == 0 and "CHILD-OK" in done.stdout
    ), f"exit {done.returncode}\n{done.stdout}\n{done.stderr}"


def test_no_wall_clock_bound_in_this_file_is_written_as_a_bare_number() -> None:
    """Every bound here is one of the named ones, the audit the other Qt test files run."""
    assert spelled_bounds(__file__) == {"HANG_BOUND", "HANG_BOUND_MS", "SDL_SLOW_RELEASE"}
