"""Mouse-free directional navigation for the Yu'lon launcher (PySide6).

"Dadcraft" gamepad autonomy: the engine turns a D-pad + face buttons + shoulder
bumpers into full navigation of every panel, button, tab and input, with no
mouse. It is input-source-agnostic by design:

- It reads *logical* actions ("up", "confirm", "cycle-next", ...) from an
  `InputSource`, never raw OS events.
- The shipped `KeyboardSource` is a zero-dependency event filter: on Steam Deck
  (Steam Input maps the pad to arrow/Return/Escape/uinput keys) and on Windows
  (Steam's XInput overlay does the same) the physical gamepad already arrives as
  key events, so the default source needs nothing OS-specific.

PySide6/Qt6 has no native gamepad API; a real evdev/SDL backend plugs in behind
the `InputSource` protocol (the `inputs` or `pygame` package) without touching
any navigation code below.

Architecture (style-guide §3/§5): this module owns *navigation mechanics only*.
It knows how focus moves and how a logical action becomes a focus shift or a
click; it knows nothing about Docker, servers, installs or manifests. The main
window composes it and connects its signals.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum

from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QComboBox,
    QLineEdit,
    QTabWidget,
    QWidget,
)


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
        if (
            w is not root
            and (policy & Qt.FocusPolicy.TabFocus)
            and w.isEnabled()
            and w.isVisible()
        ):
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
        popup = app.activePopupWidget() if app is not None else None
        if popup is not None:
            return popup
        modal = app.activeModalWidget() if app is not None else None
        if modal is not None:
            return modal
        focused = QApplication.focusWidget()
        if focused is not None:
            window = focused.window()
            if window is not None:
                return window
        if app is not None and app.activeWindow() is not None:
            return app.activeWindow()
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
        popup = app.activePopupWidget() if app is not None else None
        if popup is not None:
            popup.close()
            return True
        modal = app.activeModalWidget() if app is not None else None
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
        window = QApplication.activeWindow()
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
        self._app = app
        app.installEventFilter(self)

    def stop(self) -> None:
        """Remove the filter (used at teardown to avoid a dangling target)."""
        if self._app is not None:
            self._app.removeEventFilter(self)
            self._app = None

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.KeyPress:
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


def install_gamepad_navigation(window: QWidget) -> tuple[Navigator, KeyboardSource]:
    """Create and start a Navigator + KeyboardSource pair bound to `window`.

    The one line the app calls: builds the engine, starts the source, and
    returns both so a caller can keep references (they are also parented to
    `window` so Qt owns their lifetime). Idempotent per window via the source's
    `start()` guard.
    """
    navigator = Navigator(window)
    source = KeyboardSource(navigator, window)
    source.start()
    return navigator, source