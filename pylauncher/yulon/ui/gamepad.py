"""Mouse-free directional navigation for the Yu'lon launcher (PySide6).

"Dadcraft" gamepad autonomy: the engine turns a D-pad + face buttons + shoulder
bumpers into full navigation of every panel, button, tab and input, with no
mouse. It is input-source-agnostic by design: every source decodes the physical
device into *logical* `Direction`/`Action` events and hands them to the one
`Navigator`, which is the only thing that moves focus.

Two sources ship, for the two ways a pad reaches Qt:

- `GamepadSource` reads the physical controller through SDL's **GameController**
  sub-API (`pygame._sdl2.controller`), NOT raw `pygame.joystick`. The controller
  layer consults SDL's `gamecontrollerdb.txt` to normalize *every* device — the
  Steam Deck's virtual Xbox 360 pad (Linux evdev), a wired Xbox pad (Windows
  XInput), and a Bluetooth controller on macOS (IOHID / Game Controller
  framework) — onto one semantic enum, so `CONTROLLER_BUTTON_A` is always
  "Submit" and the D-pad always navigates, with zero per-OS code. This is the
  fix for the macOS-Bluetooth-telemetry failure and the out-of-the-box Deck
  requirement: we read what Steam Input already assigns, never ask the user to
  remap.
- `KeyboardSource` stays as a zero-dependency fallback for desktop arrow-key
  use and keyboard-emulation, so a checkout without `pygame` still navigates.

`GamepadSource` imports `pygame` lazily and degrades to a no-op when it is not
installed or no controller is present (CI, a headless box), so a missing
dependency never stops the app from launching.

PySide6/Qt6 dropped the legacy `QtGamepad` module; `pygame` (SDL2) is the one
backend that covers all three target OSes — and only its `_sdl2.controller` API
(not the raw joystick API) provides the cross-device semantic mapping. SDL2
already ships on SteamOS, so the AppImage on the Deck does not bloat.

Architecture (style-guide §3/§5): this module owns *navigation mechanics only*.
It knows how focus moves and how a logical action becomes a focus shift or a
click; it knows nothing about Docker, servers, installs or manifests. The main
window composes it and connects its signals.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from enum import Enum
from typing import Protocol

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPoint, Qt, QThread, Signal, Slot
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QComboBox,
    QLineEdit,
    QTabWidget,
    QWidget,
)


class GameController(Protocol):
    """The slice of `pygame._sdl2.controller.Controller` the poller reads.

    A Protocol rather than an import of `pygame` because that package is optional
    and absent from CI/desktop checkouts — mypy would otherwise fail on a missing
    stub. The object it stands for emits SDL_GameController *semantic* button and
    axis values (see the `CONTROLLER_*` module constants), which is what makes the
    mapping portable across evdev / XInput / IOHID.
    """

    def get_button(self, button: int) -> bool: ...
    def get_axis(self, axis: int) -> int: ...
    def quit(self) -> None: ...


# --- SDL_GameController semantic button/axis enum (Xbox-normalized). ---------
# These are pygame's own `CONTROLLER_*` constants, re-exported here so the poller
# never imports pygame at module scope. The values are the SDL2 gamecontrollerdb
# semantic IDs — identical on Linux, Windows and macOS — so a Steam Deck's
# virtual Xbox pad and a macOS Bluetooth controller both report `A == 0`.
BTN_A = 0
BTN_B = 1
BTN_X = 2
BTN_Y = 3
BTN_BACK = 4
BTN_GUIDE = 5
BTN_START = 6
BTN_LEFT_STICK = 7
BTN_RIGHT_STICK = 8
BTN_LB = 9
BTN_RB = 10
BTN_DPAD_UP = 11
BTN_DPAD_DOWN = 12
BTN_DPAD_LEFT = 13
BTN_DPAD_RIGHT = 14

AXIS_LEFT_X = 0
AXIS_LEFT_Y = 1
AXIS_RIGHT_X = 2
AXIS_RIGHT_Y = 3
AXIS_TRIGGER_LEFT = 4
AXIS_TRIGGER_RIGHT = 5

# The raw SDL ceiling `SDL_GameControllerAxis` can reach (Sint16 magnitude),
# used to express the deadzone as a fraction of the full range.
AXIS_MAX = 32768

# Deadzone for the stick, as a fraction of the full raw SDL range.
#
# `pygame._sdl2.controller.Controller.get_axis()` returns the RAW
# `SDL_GameControllerGetAxis` value — a `Sint16` in [-32768, 32767], NOT the
# normalized [-1.0, 1.0] float that `pygame.joystick` returns. A deadzone of
# "0.5" meant to read "half deflection" was therefore compared against a raw
# integer magnitude, and a resting stick's jitter (a few units around 0) sailed
# straight through it: every D-pad-less drift registered as a direction. The
# deadzone is now a fraction of the raw range, so 0.5 == half deflection of a
# real stick, and the resting jitter of ±a few units is swallowed whole.
DEADZONE = 0.5

# Hold-repeat timing: a held direction repeats after this delay, then at this
# interval, so a user can hold the D-pad to scroll a list fast.
FIRST_REPEAT_S = 0.45
REPEAT_S = 0.12

POLL_S = 1 / 120

# How often the worker re-inits the SDL controller subsystem to reveal pads that
# connected after launch. macOS SDL2 never re-lists a hotplugged Bluetooth pad
# through `get_count()` alone — only a `quit()`+`init()` rescan does (measured
# on SDL 2.28.4). A few seconds keeps the launcher responsive to a pad the owner
# turns on mid-session without re-polling SDL every tick.
RESCAN_INTERVAL_S = 3.0


class Direction(Enum):
    """A logical, source-agnostic movement axis."""

    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"


class Action(Enum):
    """A logical, source-agnostic discrete command."""

    CONFIRM = "confirm"  # A / Return / Space
    BACK = "back"  # B / Escape
    CYCLE_PREV = "cycle_prev"  # L1 / previous tab
    CYCLE_NEXT = "cycle_next"  # R1 / next tab


# The shipped keyboard mapping. Steam Input's Desktop configuration maps a Deck
# pad to these keys; XInput through Steam's overlay maps the same way. Users
# with a non-default mapping still land here because the *logical* layer (below)
# is what the engine reads, not the raw key.
_KEY_TO_DIRECTION: dict[int, Direction] = {
    int(Qt.Key.Key_Up): Direction.UP,
    int(Qt.Key.Key_Down): Direction.DOWN,
    int(Qt.Key.Key_Left): Direction.LEFT,
    int(Qt.Key.Key_Right): Direction.RIGHT,
}
_KEY_TO_ACTION: dict[int, Action] = {
    int(Qt.Key.Key_Return): Action.CONFIRM,
    int(Qt.Key.Key_Enter): Action.CONFIRM,
    int(Qt.Key.Key_Space): Action.CONFIRM,
    int(Qt.Key.Key_Escape): Action.BACK,
    int(Qt.Key.Key_Backspace): Action.BACK,
    # Shoulder bumpers map to tab cycling. L/R are the natural "switch view"
    # pair; the L/R glyph keys are harmless aliases for desktop users.
    int(Qt.Key.Key_L): Action.CYCLE_PREV,
    int(Qt.Key.Key_R): Action.CYCLE_NEXT,
}
# Discrete actions must not auto-repeat (holding A must not spam clicks); held
# *direction* keys DO repeat so a user can fast-scroll a list.
_NON_REPEATING_ACTIONS = frozenset(_KEY_TO_ACTION.values())


# Face + shoulder buttons → logical actions, keyed by SDL_GameController semantic
# button (the `CONTROLLER_BUTTON_*` enum). A = confirm, B = back, LB/RB cycle the
# enclosing tab widget. X/Y/BACK/START/GUIDE/sticks are deliberately unmapped.
_BUTTON_TO_ACTION: dict[int, Action] = {
    BTN_A: Action.CONFIRM,
    BTN_B: Action.BACK,
    BTN_LB: Action.CYCLE_PREV,
    BTN_RB: Action.CYCLE_NEXT,
}


def _iter_focusable(root: QWidget) -> Iterable[QWidget]:
    """Yield every focusable, enabled, visible descendant of `root` (in order).

    A widget qualifies when it has `TabFocus` in its policy, is enabled, is
    visible to `root`, and is not a read-only text surface that would swallow
    the D-pad (a read-only `QPlainTextEdit`/`QTextEdit` still consumes arrow
    keys to move its cursor, which is a dead-end for navigation).
    """
    from PySide6.QtWidgets import QPlainTextEdit, QTextEdit

    def walk(w: QWidget) -> Iterable[QWidget]:
        policy = w.focusPolicy()
        if w is not root and (policy & Qt.FocusPolicy.TabFocus) and w.isEnabled() and w.isVisible():
            # read-only multi-line text is a D-pad trap; skip it.
            if isinstance(w, (QPlainTextEdit, QTextEdit)) and w.isReadOnly():
                pass
            else:
                yield w
        for child in w.children():
            if isinstance(child, QWidget) and child.isVisibleTo(root):
                yield from walk(child)

    yield from walk(root)


def _center(w: QWidget, relative_to: QWidget) -> QPoint:
    """The widget's viewport center, in `relative_to`'s coordinate space."""
    top_left = w.mapTo(relative_to, QPoint(0, 0))
    return QPoint(top_left.x() + w.width() // 2, top_left.y() + w.height() // 2)


class Navigator(QObject):
    """The directional focus engine: resolves movement and executes actions.

    It is a command target, not a widget. The composing window calls
    `navigate(direction)` / `perform(action)` directly (call-down). Context is
    derived from Qt's own modality/popup state each event, so focus can never
    escape a modal dialog or an open dropdown.
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cache: dict[int, list[QWidget]] = {}

    # -- context ---------------------------------------------------------

    def _context_root(self) -> QWidget:
        """The widget subtree navigation is trapped inside, in precedence order.

        1. An open popup (a `QComboBox` list, a `QMenu`) — nothing outside it is
           reachable until it closes.
        2. An active modal (`QMessageBox`, `QInputDialog`, prompt dialogs).
        3. The focused widget's top-level window.
        """
        app = QApplication.instance()
        if isinstance(app, QApplication):
            popup = app.activePopupWidget()
            if popup is not None:
                return popup
            modal = app.activeModalWidget()
            if modal is not None:
                return modal
            active = app.activeWindow()
            if active is not None:
                return active
        focused = QApplication.focusWidget()
        if focused is not None:
            window = focused.window()
            if window is not None:
                return window
        raise RuntimeError("no widget context to navigate")

    # -- focusable enumeration ------------------------------------------

    def _focusable(self, root: QWidget) -> list[QWidget]:
        """Every focusable descendant of `root`, cached per widget id.

        Call `invalidate()` after the tree changes (a tab opens, an install
        adopts a server, a tile is added).
        """
        key = id(root)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        found = list(_iter_focusable(root))
        self._cache[key] = found
        return found

    def invalidate(self) -> None:
        """Drop the focus-chain caches; call after the widget tree changes."""
        self._cache.clear()

    # -- directional resolution ------------------------------------------

    def navigate(self, direction: Direction) -> bool:
        """Move focus one step in `direction`. Returns whether focus moved."""
        root = self._context_root()
        candidates = self._focusable(root)
        current = QApplication.focusWidget()
        if current is None and candidates:
            # Nothing focused yet (first D-pad press on a fresh window): seed the
            # top-left-most focusable so there is a visible origin for both the
            # user's :focus ring and every later projection. Without this,
            # `origin` anchors at (0,0) and the first press can land arbitrarily.
            current = self._top_left(candidates, root)
            current.setFocus(Qt.FocusReason.OtherFocusReason)
            return True

        origin = _center(current, root) if current is not None else QPoint(0, 0)
        target = self._pick(candidates, current, origin, direction, root)
        if target is None:
            return False
        target.setFocus(Qt.FocusReason.OtherFocusReason)
        return True

    @staticmethod
    def _top_left(candidates: list[QWidget], root: QWidget) -> QWidget:
        """The candidate closest to `root`'s top-left corner — a stable seed."""
        return min(candidates, key=lambda w: (_center(w, root).y(), _center(w, root).x()))

    @staticmethod
    def _pick(
        candidates: list[QWidget],
        current: QWidget | None,
        origin: QPoint,
        direction: Direction,
        root: QWidget,
    ) -> QWidget | None:
        """The best candidate in `direction`.

        For each candidate, project its center onto the travel axis (`proj`,
        positive when it lies *ahead* in the direction of travel) and record the
        perpendicular distance (`offset`). Both `origin` and every candidate
        center are measured in **the same** coordinate space — `root`'s — which is
        essential: `root` may be a popup or modal, not the top-level window, and
        measuring one in `root` space and the other in `window()` space would make
        the projection nonsense (a real defect that skewed navigation inside
        dropdowns and dialogs).

        Among ahead candidates, prefer the one in the same row/column (smallest
        offset), then the nearest. When no candidate is ahead (an edge), wrap to
        the nearest candidate by perpendicular offset.
        """
        scored: list[tuple[float, float, QWidget]] = []
        for other in candidates:
            if other is current:
                continue
            center = _center(other, root)
            dx = center.x() - origin.x()
            dy = center.y() - origin.y()
            if direction is Direction.RIGHT:
                proj, offset = float(dx), float(abs(dy))
            elif direction is Direction.LEFT:
                proj, offset = float(-dx), float(abs(dy))
            elif direction is Direction.DOWN:
                proj, offset = float(dy), float(abs(dx))
            else:  # UP
                proj, offset = float(-dy), float(abs(dx))
            scored.append((proj, offset, other))

        if not scored:
            return None

        ahead = [s for s in scored if s[0] > 0]
        if ahead:
            # Same row/column first, then nearest in the travel direction.
            ahead.sort(key=lambda s: (s[1], s[0]))
            return ahead[0][2]

        # Edge: nothing is ahead, so wrap. The target is the candidate closest to
        # the row/column we are leaving (smallest perpendicular offset); when
        # several tie there, prefer the one FURTHEST behind us so a single press
        # lands on the far extreme rather than crawling back one step. `proj` is
        # negative for all of these (none is ahead), so "smallest proj" means
        # "most negative" = furthest behind.
        scored.sort(key=lambda s: (s[1], s[0]))
        return scored[0][2]

    # -- discrete actions -------------------------------------------------

    def perform(self, action: Action) -> bool:
        """Execute a discrete logical action in the active context."""
        if action is Action.CONFIRM:
            return self._confirm()
        if action is Action.BACK:
            return self._back()
        if action in (Action.CYCLE_PREV, Action.CYCLE_NEXT):
            return self._cycle(action)
        return False

    def _confirm(self) -> bool:
        widget = QApplication.focusWidget()
        if isinstance(widget, QAbstractButton):
            # A disabled button's click() is a silent no-op; refuse it so a
            # greyed-out Install/Start does not pretend to have acted.
            if widget.isEnabled():
                widget.click()
            return True
        if isinstance(widget, QLineEdit):
            # Ask the platform for its on-screen keyboard (SteamOS/Windows).
            from PySide6.QtGui import QGuiApplication

            QGuiApplication.inputMethod().show()
            return True
        if isinstance(widget, QComboBox):
            # Open the dropdown; the popup becomes the context root next event.
            widget.showPopup()
            return True
        # A list/tab/other focusable: nothing to "click"; treat as no-op but
        # still consume so the key does not leak.
        return True

    def _back(self) -> bool:
        # Back is "close the thing I am in": a popup, then a modal. If none is
        # open, it does nothing rather than yanking focus unpredictably.
        app = QApplication.instance()
        if isinstance(app, QApplication):
            popup = app.activePopupWidget()
            if popup is not None:
                popup.close()
                return True
            modal = app.activeModalWidget()
            if modal is not None:
                modal.close()
                return True
        return False

    def _cycle(self, action: Action) -> bool:
        widget = QApplication.focusWidget()
        tabs = self._nearest_tab_widget(widget)
        if tabs is None or tabs.count() < 2:
            return False
        step = 1 if action is Action.CYCLE_NEXT else -1
        tabs.setCurrentIndex((tabs.currentIndex() + step) % tabs.count())
        return True

    @staticmethod
    def _nearest_tab_widget(widget: QWidget | None) -> QTabWidget | None:
        """The first ancestor QTabWidget of the focused widget, else the active
        window's `sidebar-tabs` rail, else None."""
        node = widget
        while node is not None:
            if isinstance(node, QTabWidget):
                return node
            node = node.parentWidget()
        app = QApplication.instance()
        if isinstance(app, QApplication):
            window = app.activeWindow()
            if window is not None:
                rail = window.findChild(QTabWidget, "sidebar-tabs")
                if rail is not None:
                    return rail
        return None


class KeyboardSource(QObject):
    """The shipped input source: a global event filter over the QApplication.

    Installs once on the application, decodes every `KeyPress` into a logical
    navigation command, and hands it to the attached `Navigator`. It is the
    zero-dependency bridge that makes a Deck pad (Steam Input keyboard
    emulation) and a Windows XInput pad (Steam overlay emulation) drive the same
    engine with no OS-specific code.
    """

    def __init__(self, navigator: Navigator, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._navigator = navigator
        self._app: QApplication | None = None

    def start(self) -> None:
        """Install the filter on the current QApplication (idempotent)."""
        app = QApplication.instance()
        if app is None or self._app is app:
            return
        if isinstance(app, QApplication):
            self._app = app
            app.installEventFilter(self)

    def stop(self) -> None:
        """Remove the filter (used at teardown to avoid a dangling target)."""
        if self._app is not None:
            self._app.removeEventFilter(self)
            self._app = None

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            key = int(event.key())

            if key in _KEY_TO_DIRECTION:
                # A direction key is always CONSUMED by the navigator, even when
                # it dead-ends at an edge: returning `False` here would let the
                # arrow key fall through to the focused widget and move a
                # spinbox value or a text cursor. Navigation either moved focus
                # or intentionally did nothing — the key must never leak.
                self._navigator.navigate(_KEY_TO_DIRECTION[key])
                return True

            if key in _KEY_TO_ACTION:
                action = _KEY_TO_ACTION[key]
                if action in _NON_REPEATING_ACTIONS and event.isAutoRepeat():
                    # Swallow the repeat of a held discrete action.
                    return True
                self._navigator.perform(action)
                return True

        return super().eventFilter(watched, event)


def install_gamepad_navigation(window: QWidget) -> tuple[Navigator, KeyboardSource, GamepadSource]:
    """Create and start every input source bound to `window`.

    Builds the one `Navigator`, then starts the keyboard filter (always) and the
    SDL gamepad reader (no-op when `pygame` is absent). Returns the navigator
    and both sources so a caller can keep references; they are also parented to
    `window` so Qt owns their lifetime.
    """
    navigator = Navigator(window)
    keyboard = KeyboardSource(navigator, window)
    gamepad = GamepadSource(navigator, window)
    keyboard.start()
    # The gamepad source emits logical events as queued signals; route them into
    # the same navigator the keyboard filter drives.
    gamepad.direction.connect(navigator.navigate)
    gamepad.action.connect(navigator.perform)
    gamepad.start()
    return navigator, keyboard, gamepad


class GamepadSource(QObject):
    """Reads the physical controller through SDL and feeds the navigator.

    A `QThread` + worker poll the pad at ~120 Hz; every decoded logical event is
    emitted as a queued Qt signal onto the GUI thread. Face/shoulder buttons are
    edge-triggered (one action per press), the D-pad and left stick are
    level-triggered with a deadzone and a hold-repeat for fast list scrolling.
    The Xbox/XInput layout is the target because the Steam Deck's own base
    template presents exactly that (a virtual Xbox 360 pad) to any non-Steam
    game — the lowest common denominator the app must read, from a user who was
    never asked to remap anything.
    """

    #: Emits `Direction` on the GUI thread.
    direction = Signal(object)
    #: Emits `Action` on the GUI thread.
    action = Signal(object)

    def __init__(self, navigator: Navigator, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: QThread | None = None
        self._worker: _GamepadWorker | None = None
        self._running = False
        # A stopped pair whose thread has not finished yet, and whether a
        # `start()` arrived meanwhile -- see `start()`.
        self._stopping: QThread | None = None
        self._start_pending = False

    def start(self) -> None:
        """Begin polling SDL for a connected pad. No-op if unavailable.

        Availability is decided HERE on the GUI thread — if `pygame` is absent,
        or no controller is present, no worker/thread is created and teardown
        cannot be polluted by a doomed poller. The `_sdl2.controller` sub-API is
        imported lazily; its `init()`/`quit()` pair and `get_count()` here are
        the exact same surface the worker uses, so the probe is authoritative.
        """
        if self._running:
            return
        if self._stopping is not None and self._stopping.isRunning():
            # A stopped poller is still on its way out, and its last act is
            # SDL's global `quit()`. Probing or starting now would init SDL
            # under it and let that quit tear SDL down under the new poller,
            # so the start waits for the old thread's `finished`
            # (`_on_thread_finished`). A `stop()` before then cancels it.
            self._start_pending = True
            return
        try:
            import pygame  # noqa: F401  # availability probe only
            from pygame._sdl2 import controller as _sdl2ctl  # noqa: F401
        except ImportError:
            return
        try:
            # The controller subsystem ALONE under-counts on macOS: it reports
            # only the device SDL enumerated first (a phantom that reads all
            # zeros) and never sees a second, real pad. `pygame.joystick.init()`
            # (the raw HID layer) is what forces a full enumeration, after which
            # `get_count()` sees every device. Both layers are initialized here
            # and in the worker, in this order.
            pygame.joystick.init()
            _sdl2ctl.init()
            has_stick = _sdl2ctl.get_count() > 0
        finally:
            _sdl2ctl.quit()
            pygame.joystick.quit()
        if not has_stick:
            return
        self._start_thread()
        self._running = True

    def _start_thread(self) -> None:
        """Start one poller pair, owned the way `job.ThreadedJobRunner` owns its pairs (T111).

        - **No parent on the QThread.** It was `QThread(self)`, so the thread
          went with the source (and the source with the window); a QThread
          destroyed while running aborts the process. Measured before T111:
          SDL slower than `stop()`'s 500 ms to let go at exit, then the window
          deleted -> `QThread: Destroyed while thread is still running`, exit 134.
        - **Held by `in_flight()` until `finished`, no `deleteLater`.**
          `thread.finished -> worker.deleteLater` ran the worker's destructor ON
          the worker thread (measured), which needs the GIL there -- trap 3 of
          the GUI segfault. `InFlight.sweep()` drops the pair on the GUI thread
          instead, after the thread has exited, and `wait_all()` in
          `main._stop_background_threads()` is its join at exit.
        - **Input is queued to this object's thread** (the GUI thread), so the
          navigator only ever moves focus from there.
        """
        from yulon.ui.widgets.job import in_flight

        worker = _GamepadWorker()
        thread = QThread()
        worker.moveToThread(thread)
        in_flight().hold(thread, worker)
        thread.started.connect(worker.run)
        queued = Qt.ConnectionType.QueuedConnection
        worker.direction.connect(self.direction, queued)
        worker.action.connect(self.action, queued)
        thread.finished.connect(self._on_thread_finished)
        # A source deleted without `stop()` (its window gone) must not leave the
        # poll running with nobody to stop it: `in_flight().wait_all()` can quit
        # an event loop, not this loop. Direct, because `stop()` only sets a flag
        # and the source's own thread is the one being torn down.
        self.destroyed.connect(worker.stop, Qt.ConnectionType.DirectConnection)
        self._thread = thread
        self._worker = worker
        thread.start()

    @Slot()
    def _on_thread_finished(self) -> None:
        """A poller thread has ended: forget it, and run a `start()` that waited for it.

        Queued to the GUI thread. The current pair is cleared only if IT has
        ended (the pad went away): clearing a running pair dropped its worker
        before its `run` began (trap 1, measured on `stop(); start()` before
        T111). A finished thread has already run its worker's SDL `quit()`, so a
        pending start is safe to make now.
        """
        if self._thread is not None and not self._thread.isRunning():
            self._running = False
            self._thread = None
            self._worker = None
        if self._stopping is not None and not self._stopping.isRunning():
            self._stopping = None
            if self._start_pending:
                self._start_pending = False
                self.start()

    def stop(self) -> None:
        """Tell the poller to stop; the join is `in_flight().wait_all()` at exit.

        The worker breaks its loop on the next tick and quits its own thread.
        Waiting here as well was the only join the thread had before T111, and
        at 500 ms it lost to a slow SDL release (see `_start_thread`);
        `main._stop_background_threads()` calls this and then `wait_all()`.
        The references can go at once: `in_flight()` holds the pair until its
        thread has finished. The thread is remembered as `_stopping` so a
        `start()` before it finishes waits for it, and a pending start is
        cancelled here.
        """
        self._start_pending = False
        if self._worker is not None:
            self._worker.stop()
        if self._thread is not None:
            self._thread.quit()
            self._stopping = self._thread
        self._thread = None
        self._worker = None
        self._running = False


class _GamepadWorker(QObject):
    """The SDL poller, living on its own thread. Emits logical events via signals.

    Kept separate from `GamepadSource` so the GUI-thread object never touches
    SDL directly: SDL's joystick state must be read from one thread (the poller),
    and the signals carry the decoded result back across the thread boundary.
    """

    direction = Signal(object)
    action = Signal(object)
    finished = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._stop = False
        self._held: Direction | None = None
        self._held_since = 0.0
        self._pressed: set[tuple[int, int]] = set()

    @Slot()
    def run(self) -> None:
        """Poll the SDL GameController layer until told to stop.

        The whole body is one `try/finally`: whatever happens — `pygame` missing,
        no controller present, a poll raising — `_finish()` runs exactly once,
        so the owning `QThread` quits and `in_flight()` lets the pair go, never
        left running into Qt's interpreter teardown (which aborts with 0xC0000409).
        """
        import pygame  # optional dep; availability is probed on the GUI thread first
        from pygame._sdl2 import controller as _sdl2ctl

        try:
            # `pygame.joystick.init()` FIRST, or the controller layer under-counts
            # on macOS: without the raw HID layer initialized, `get_count()` sees
            # only the first-enumerated device (a phantom) and misses the real pad
            # sitting at a higher index. See `GamepadSource.start()`.
            pygame.joystick.init()
            _sdl2ctl.init()
        except pygame.error:
            # No controller subsystem (headless CI, a container): nothing to read.
            self._finish()
            return

        sticks: dict[int, GameController] = {}

        def reenumerate() -> bool:
            """Keep `sticks` in sync with SDL's current controller set.

            Controllers appear ASYNCHRONOUSLY on macOS Bluetooth: the pad often
            enumerates *after* launch, and — worse — SDL2 on macOS does NOT
            re-list a freshly-connected device through `get_count()` no matter how
            long we poll; only a full subsystem `quit()` + `init()` cycle makes it
            re-scan and expose the new device (verified on macOS / SDL 2.28.4).
            `GamepadSource.start()`'s probe was always a fresh init, which is why
            it saw the pad while the long-lived worker did not.

            The loop below therefore re-inits the subsystem periodically (a cheap
            rescan) to reveal hotplugged pads, and `reenumerate()` re-opens any
            index SDL now reports that we do not yet hold. Returns False when the
            subsystem dies.
            """
            try:
                count = _sdl2ctl.get_count()
            except pygame.error:
                return False
            want = set(range(min(count, 4)))
            for i in list(sticks.keys() - want):
                try:
                    sticks.pop(i).quit()
                except pygame.error:
                    pass
            for i in sorted(want - sticks.keys()):
                try:
                    sticks[i] = _sdl2ctl.Controller(i)
                except pygame.error:
                    # Enumerated a moment ago, gone a moment later: skip.
                    continue
            return True

        last_rescan = time.monotonic()

        def rescan_if_due() -> None:
            """Periodic full subsystem re-init to reveal hotplugged controllers.

            A long-lived process must quit + re-init SDL's controller subsystem to
            see a Bluetooth pad that connects after launch (macOS SDL2 defect —
            `get_count()` alone never re-lists it). Doing this every
            `RESCAN_INTERVAL_S` is cheap enough (a few ms) to be invisible, and it
            is the difference between "works if the pad was on at launch" and
            "works whenever the pad turns on".
            """
            nonlocal last_rescan
            now = time.monotonic()
            if now - last_rescan < RESCAN_INTERVAL_S:
                return
            for stick in sticks.values():
                try:
                    stick.quit()
                except pygame.error:
                    pass
            sticks.clear()
            _sdl2ctl.quit()
            pygame.joystick.quit()
            pygame.joystick.init()
            _sdl2ctl.init()
            last_rescan = time.monotonic()

        try:
            reenumerate()
            while not self._stop:
                # `_sdl2ctl.update()` is `SDL_GameControllerUpdate()`: it
                # refreshes the controller layer's POLLED state for the
                # `get_button`/`get_axis` reads below. `pygame.event.pump()`
                # is NOT used here — it requires `pygame.init()`'s video/event
                # system, which nothing initializes (we init only the controller
                # subsystem), so the first pump raises
                # `pygame.error: video system not initialized`.
                try:
                    rescan_if_due()
                    _sdl2ctl.update()
                    if not reenumerate():
                        break
                    self._poll(sticks)
                except pygame.error:
                    # Controller unplugged mid-poll (Bluetooth drop, Deck sleep):
                    # stop cleanly rather than raising out of the worker thread,
                    # which would orphan the poller and tear down Qt badly.
                    break
                time.sleep(POLL_S)
        finally:
            for stick in sticks.values():
                try:
                    stick.quit()
                except pygame.error:
                    pass
            _sdl2ctl.quit()
            pygame.joystick.quit()
            self._finish()

    def _finish(self) -> None:
        """Say so, and end OUR thread's event loop from inside it.

        Nothing else would: the QThread object lives on the GUI thread, so a
        `finished -> thread.quit` connection is queued there, and at exit
        `main._stop_background_threads()` blocks that thread in a join with
        nothing pumping it (`LogPanel`'s worker does the same, for the same
        reason). Guarded so a `run()` driven on the GUI thread cannot quit the
        app's own loop.
        """
        self.finished.emit()
        thread = self.thread()
        app = QCoreApplication.instance()
        if thread is not None and (app is None or thread is not app.thread()):
            thread.quit()

    @Slot()
    def stop(self) -> None:
        """Signal the poll loop to break (thread-safe enough for a bool flag)."""
        self._stop = True

    def _poll(self, sticks: dict[int, GameController]) -> None:
        """Decode one snapshot of every controller into logical events.

        `sticks` is keyed by the STABLE SDL controller index (not list position),
        so a pad's edge-triggered button state survives re-enumeration: opening a
        new controller elsewhere must not re-key an existing one and make a held
        button re-emit. Any live pad may be the one the owner is holding (see
        `run()`'s note about Mac phantoms), so every controller is read each tick
        and the union of their inputs drives the navigator.
        """
        any_direction = False
        for index, stick in sticks.items():
            for button, action in _BUTTON_TO_ACTION.items():
                if stick.get_button(button):
                    if (index, button) not in self._pressed:
                        self._pressed.add((index, button))
                        self.action.emit(action)
                else:
                    self._pressed.discard((index, button))

            # D-pad and left stick both drive movement. The D-pad edges take
            # precedence while pressed; otherwise the left stick, deadzoned.
            d_pad = _dpad_direction(stick)
            if d_pad is not None:
                self._update_direction(d_pad)
                any_direction = True
                continue
            dx, dy = (
                stick.get_axis(AXIS_LEFT_X),
                stick.get_axis(AXIS_LEFT_Y),
            )
            stick_dir = _axis_to_direction(dx, dy, DEADZONE)
            if stick_dir is not None:
                self._update_direction(stick_dir)
                any_direction = True
        # No controller asserted a direction this tick: clear the held state so
        # a fresh press next tick re-triggers the immediate-emit path. Skipped
        # when a pad DID assert one, otherwise the hold-repeat clock would reset
        # every tick and a held D-pad could never fast-scroll.
        if not any_direction:
            self._update_direction(None)

    def _update_direction(self, direction: Direction | None) -> None:
        now = time.monotonic()
        if direction is None:
            self._held = None
            self._held_since = 0.0
            return
        if direction is not self._held:
            # A fresh press (or a change of direction): emit immediately, then
            # begin the hold-repeat clock.
            self._held = direction
            self._held_since = now
            self.direction.emit(direction)
            return
        # Same direction still held: after the initial delay, re-emit once per
        # interval so a held D-pad scrolls a list instead of stepping once.
        elapsed = now - self._held_since
        if elapsed >= FIRST_REPEAT_S:
            steps = int((elapsed - FIRST_REPEAT_S) / REPEAT_S)
            previous = int((elapsed - POLL_S - FIRST_REPEAT_S) / REPEAT_S)
            if steps > previous:
                self.direction.emit(direction)


def _axis_to_direction(dx: int, dy: int, deadzone: float) -> Direction | None:
    """Map a raw SDL axis pair to a cardinal direction, with a deadzone.

    `dx`/`dy` are raw `Sint16` values (from `Controller.get_axis()`), so the
    deadzone is expressed as a fraction of the full range rather than compared
    against a normalized float. `AXIS_MAX` is the SDL ceiling (32768); the
    fraction keeps the same "half deflection" meaning the old normalize-in-
    ones-head comment intended, without ever dividing the raw value.
    """
    threshold = int(AXIS_MAX * deadzone)
    ax, ay = abs(dx), abs(dy)
    if ax < threshold and ay < threshold:
        return None
    if ax >= ay:
        return Direction.RIGHT if dx > 0 else Direction.LEFT
    return Direction.DOWN if dy > 0 else Direction.UP


def _dpad_direction(stick: GameController) -> Direction | None:
    """The direction of a pressed D-pad edge, else None.

    SDL's GameController API reports the D-pad as four buttons (DPAD_*), not a
    hat — so each edge is read individually and collapsed into a cardinal
    direction, with diagonals resolving by a fixed precedence (vertical over
    horizontal) to keep a two-edges-held state deterministic.
    """
    up = stick.get_button(BTN_DPAD_UP)
    down = stick.get_button(BTN_DPAD_DOWN)
    left = stick.get_button(BTN_DPAD_LEFT)
    right = stick.get_button(BTN_DPAD_RIGHT)
    if up and not down:
        return Direction.UP
    if down and not up:
        return Direction.DOWN
    if left and not right:
        return Direction.LEFT
    if right and not left:
        return Direction.RIGHT
    return None
