"""Mouse-free directional navigation for the Yu'lon launcher (PySide6).

"Dadcraft" gamepad autonomy: the engine turns a D-pad + face buttons + shoulder
bumpers into full navigation of every panel, button, tab and input, with no
mouse. It is input-source-agnostic by design: every source decodes the physical
device into *logical* `Direction`/`Action` events and hands them to the one
`Navigator`, which is the only thing that moves focus.

Two sources ship, for the two ways a pad reaches Qt:

- `GamepadSource` reads the physical controller directly through SDL (`pygame`).
  On the Steam Deck, adding the AppImage to Steam presents the built-in pad as
  a **virtual Xbox 360 pad** to any non-Steam game — so "basic XInput" is what
  every user gets by default, delivered as SDL joystick events. SDL normalizes
  evdev (SteamOS), XInput (Windows) and IOHID (macOS) to one model, so the same
  poll loop works on all three with no OS-specific code. This is the
  lowest-common-denominator source: we cannot ask the user to change Steam
  Input's template, so we read what Steam Input always produces.
- `KeyboardSource` stays as a zero-dependency fallback for desktop arrow-key
  use and for the keyboard-emulation path, so a checkout without `pygame`
  still navigates.

`GamepadSource` imports `pygame` lazily and degrades to a no-op when it is not
installed (CI, an un-reinstalled checkout), so a missing dependency never stops
the app from launching.

PySide6/Qt6 has no native gamepad API; `pygame` (SDL2) is the one backend that
covers all three target OSes — `inputs` drops macOS and `evdev` is Linux-only.
SDL2 already ships on SteamOS, so the AppImage does not bloat on the Deck.

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

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QThread, Signal, Slot
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QComboBox,
    QLineEdit,
    QTabWidget,
    QWidget,
)


class Joystick(Protocol):
    """The slice of `pygame.joystick.Joystick` the poller reads.

    A Protocol rather than an import of `pygame` because that package is optional
    and absent from CI/desktop checkouts — mypy would otherwise fail on a missing
    stub. The real object satisfies this surface; nothing else in the module
    touches `pygame` except `_GamepadWorker.run()`.
    """

    def get_numbuttons(self) -> int: ...
    def get_button(self, index: int) -> bool: ...
    def get_numaxes(self) -> int: ...
    def get_axis(self, index: int) -> float: ...
    def get_numhats(self) -> int: ...
    def get_hat(self, index: int) -> tuple[float, float]: ...


# --- Xbox/XInput layout + polling timing (module-level: shared by the worker) ---
# The Steam Deck's base template presents the built-in pad as a virtual Xbox 360
# pad to any non-Steam game, so these SDL joystick indices are what every user
# gets without being asked to remap anything.
BTN_A = 0
BTN_B = 1
BTN_X = 2
BTN_Y = 3
BTN_LB = 4
BTN_RB = 5
BTN_BACK = 6
BTN_START = 7

# SDL joystick axes (XInput order). LX/LY drive movement; LT/RT are unmapped on
# purpose (analog triggers rest under the fingers and would fire actions).
AXIS_LX = 0
AXIS_LY = 1

# Deadzone for the stick, in SDL's [-1, 1] axis units.
DEADZONE = 0.5

# Hold-repeat timing: a held direction repeats after this delay, then at this
# interval, so a user can hold the D-pad to scroll a list fast.
FIRST_REPEAT_S = 0.45
REPEAT_S = 0.12

POLL_S = 1 / 120


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

        origin = _center(current, root) if current is not None else QPoint(0, 0)
        target = self._pick(candidates, current, origin, direction)
        if target is None:
            return False
        target.setFocus(Qt.FocusReason.OtherFocusReason)
        return True

    @staticmethod
    def _pick(
        candidates: list[QWidget],
        current: QWidget | None,
        origin: QPoint,
        direction: Direction,
    ) -> QWidget | None:
        """The best candidate in `direction`.

        For each candidate, project its center onto the travel axis (`proj`,
        positive when it lies *ahead* in the direction of travel) and record the
        perpendicular distance (`offset`). Among ahead candidates, prefer the
        one in the same row/column (smallest offset), then the nearest. When no
        candidate is ahead (we are at an edge), wrap to the nearest candidate in
        the perpendicular sense, leaning to the far extreme.
        """
        scored: list[tuple[float, float, QWidget]] = []
        for other in candidates:
            if other is current:
                continue
            center = _center(other, other.window())
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

        # Edge: no candidate ahead. Wrap to the nearest by perpendicular offset,
        # and among those, the one furthest behind (the opposite extreme).
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
                # Held direction keys repeat (fast scroll); a single tap moves
                # exactly one step. Either way the navigator handles it.
                return self._navigator.navigate(_KEY_TO_DIRECTION[key])

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

    def start(self) -> None:
        """Begin polling SDL for a connected pad. No-op if unavailable.

        The `pygame` import lives in the worker's `run()`, but availability is
        checked HERE on the GUI thread first — if the package is absent, no
        worker is ever created and the `try/except ImportError` cannot leak a
        doomed `QThread` into teardown. A missing joystick is also decided here,
        so a headless box never spawns a poller at all.
        """
        if self._running:
            return
        try:
            import pygame  # noqa: F401  # availability probe only
        except ImportError:
            return
        try:
            pygame.joystick.init()
            has_stick = pygame.joystick.get_count() > 0
        finally:
            pygame.joystick.quit()
        if not has_stick:
            return
        self._start_thread()
        self._running = True

    def _start_thread(self) -> None:
        worker = _GamepadWorker()
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.direction.connect(self.direction)
        worker.action.connect(self.action)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_thread_finished)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_thread_finished(self) -> None:
        self._running = False
        self._thread = None
        self._worker = None

    def stop(self) -> None:
        """Stop polling cleanly (the worker breaks its loop on the next tick)."""
        if self._worker is not None:
            self._worker.stop()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(500)
            self._thread = None
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
        self._pressed: set[int] = set()

    @Slot()
    def run(self) -> None:
        """Poll SDL until told to stop; the only method that imports/uses `pygame`.

        The whole body is one `try/finally`: whatever happens — `pygame` missing,
        no joystick present, a poll raising — `finished` is emitted exactly once so
        the owning `QThread` quits and the pair is torn down, never left running
        into Qt's interpreter teardown (which aborts with 0xC0000409).
        """
        import pygame  # optional dep; availability is probed on the GUI thread first
        try:
            pygame.joystick.init()
            count = pygame.joystick.get_count()
        except pygame.error:
            # No joystick subsystem (headless CI, a container): nothing to read.
            self.finished.emit()
            return
        if count == 0:
            self.finished.emit()
            return
        stick = pygame.joystick.Joystick(0)
        stick.init()
        pygame.event.pump()
        try:
            while not self._stop:
                pygame.event.pump()
                self._poll(stick)
                time.sleep(POLL_S)
        finally:
            pygame.joystick.quit()
            self.finished.emit()

    def stop(self) -> None:
        """Signal the poll loop to break (thread-safe enough for a bool flag)."""
        self._stop = True

    def _poll(self, stick: Joystick) -> None:
        """Decode one snapshot of button and axis state into logical events."""
        # Buttons: edge-triggered. A press emits once; a held button emits
        # nothing more (except a held direction, handled below).
        current_pressed: set[int] = set()
        for idx in range(stick.get_numbuttons()):
            if stick.get_button(idx):
                current_pressed.add(idx)

        for idx in current_pressed - self._pressed:
            self._handle_button_down(idx)
        self._pressed = current_pressed

        # D-pad (hat) and left stick: level-triggered with a deadzone, plus a
        # hold-repeat so a held direction keeps stepping.
        dx, dy = self._read_stick(stick)
        hat = self._read_hat(stick)
        if hat is not None:
            dx, dy = hat
        direction = _axis_to_direction(dx, dy, DEADZONE)
        self._update_direction(direction)

    def _handle_button_down(self, idx: int) -> None:
        mapping = {
            BTN_A: Action.CONFIRM,
            BTN_B: Action.BACK,
            BTN_LB: Action.CYCLE_PREV,
            BTN_RB: Action.CYCLE_NEXT,
        }
        action = mapping.get(idx)
        if action is not None:
            self.action.emit(action)

    def _read_stick(self, stick: Joystick) -> tuple[float, float]:
        if stick.get_numaxes() > max(AXIS_LX, AXIS_LY):
            return stick.get_axis(AXIS_LX), stick.get_axis(AXIS_LY)
        return 0.0, 0.0

    def _read_hat(self, stick: Joystick) -> tuple[float, float] | None:
        if stick.get_numhats() == 0:
            return None
        hx, hy = stick.get_hat(0)  # (-1..1, -1..1): right = +x, up = +y
        if hx == 0 and hy == 0:
            return None
        return float(hx), float(hy)

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


def _axis_to_direction(dx: float, dy: float, deadzone: float) -> Direction | None:
    """Map a 2D axis/hat vector to a cardinal direction, with a deadzone.

    The dominant axis wins; both are suppressed inside the deadzone so a resting
    stick (which never reads exactly 0.0) does not drift.
    """
    if abs(dx) < deadzone and abs(dy) < deadzone:
        return None
    if abs(dx) >= abs(dy):
        return Direction.RIGHT if dx > 0 else Direction.LEFT
    return Direction.DOWN if dy > 0 else Direction.UP
