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

import os
import sys
import threading
import time
from collections.abc import Iterable
from enum import Enum
from typing import Protocol

import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPoint, Qt, QThread, Signal, Slot
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractScrollArea,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QScrollArea,
    QTabWidget,
    QTextEdit,
    QWidget,
)

from yulon.log import get_logger

_logger = get_logger(__name__)


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

# The mirrored `pygame.CONTROLLER_*` constants above are hand-copied from SDL's
# gamecontrollerdb semantic IDs. They are fixed by the SDL spec and will not
# drift on their own, but "will not drift" is not "verified": a future pygame
# that remaps them would silently mis-bind every button with no error at all.
# This table maps each re-exported name to the pygame attribute it mirrors, so
# `_assert_sdl_parity()` can prove the mirror at controller start-up.
_SDL_PARITY: dict[str, str] = {
    "BTN_A": "CONTROLLER_BUTTON_A",
    "BTN_B": "CONTROLLER_BUTTON_B",
    "BTN_X": "CONTROLLER_BUTTON_X",
    "BTN_Y": "CONTROLLER_BUTTON_Y",
    "BTN_BACK": "CONTROLLER_BUTTON_BACK",
    "BTN_GUIDE": "CONTROLLER_BUTTON_GUIDE",
    "BTN_START": "CONTROLLER_BUTTON_START",
    "BTN_LEFT_STICK": "CONTROLLER_BUTTON_LEFTSTICK",
    "BTN_RIGHT_STICK": "CONTROLLER_BUTTON_RIGHTSTICK",
    "BTN_LB": "CONTROLLER_BUTTON_LEFTSHOULDER",
    "BTN_RB": "CONTROLLER_BUTTON_RIGHTSHOULDER",
    "BTN_DPAD_UP": "CONTROLLER_BUTTON_DPAD_UP",
    "BTN_DPAD_DOWN": "CONTROLLER_BUTTON_DPAD_DOWN",
    "BTN_DPAD_LEFT": "CONTROLLER_BUTTON_DPAD_LEFT",
    "BTN_DPAD_RIGHT": "CONTROLLER_BUTTON_DPAD_RIGHT",
    "AXIS_LEFT_X": "CONTROLLER_AXIS_LEFTX",
    "AXIS_LEFT_Y": "CONTROLLER_AXIS_LEFTY",
    "AXIS_RIGHT_X": "CONTROLLER_AXIS_RIGHTX",
    "AXIS_RIGHT_Y": "CONTROLLER_AXIS_RIGHTY",
    "AXIS_TRIGGER_LEFT": "CONTROLLER_AXIS_TRIGGERLEFT",
    "AXIS_TRIGGER_RIGHT": "CONTROLLER_AXIS_TRIGGERRIGHT",
}

_parity_result: bool | None = None


def _assert_sdl_parity() -> bool:
    """Return True when the SDL constant mirror is safe to poll live hardware with.

    Proves the hand-copied `BTN_*`/`AXIS_*` constants against pygame's own
    `CONTROLLER_*` values. A genuine drift (a future pygame remapping the
    semantic IDs) would silently re-bind every button, so it must be caught —
    but a caught drift must DEGRADE to the keyboard, not abort launch, which is
    why this returns a verdict instead of raising.

    `False` means "the mapping is known-bad, do not start the poller". `True`
    covers both "parity holds" and "nothing to verify against" (pygame absent,
    or a stubbed pygame without the constants) — the mirror cannot be proven
    wrong against nothing, and the keyboard source runs regardless.

    The verdict is memoized once computed. `None` alone means "unchecked", so a
    `False` result is NOT re-written to `True` on a later call — a drifted
    mapping stays disabled for the life of the process.
    """
    global _parity_result
    if _parity_result is not None:
        return _parity_result
    try:
        import pygame
    except ImportError:
        _parity_result = True
        return True
    if not hasattr(pygame, "CONTROLLER_BUTTON_A"):
        # A stubbed pygame (the thread test's fake) or a partial install has no
        # semantic constants to compare against — nothing to prove wrong.
        _parity_result = True
        return True
    for local, upstream in _SDL_PARITY.items():
        expected = globals()[local]
        actual = getattr(pygame, upstream, None)
        if actual != expected:
            _logger.error(
                "gamepad SDL constant drift: %s=%s but pygame.%s=%s — "
                "falling back to keyboard navigation",
                local,
                expected,
                upstream,
                actual,
            )
            _parity_result = False
            return False
    _parity_result = True
    return True


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

# T387. On Windows SDL's default RAWINPUT driver reads every Xbox-type pad, and it
# gets that input as window messages to a helper window SDL makes on the thread
# that initialises its joysticks. Nothing here pumps window messages (see the
# poller's note on `pygame.event.pump()`), so the pad's buttons never changed:
# measured on Windows 11 with a virtual Xbox 360 pad, 0 of 3 presses read and the
# D-pad moved nothing in Yu'lon. This SDL hint makes SDL run that window on a
# thread of its own and pump it there: 3 of 3, with every pad still read by the
# same SDL driver as before (a DualShock 4 through HIDAPI read 3 of 3 either way).
# SDL reads it when the joystick subsystem starts, so it is set before the first
# `pygame.joystick.init()`; a value the player set already is left alone.
_WINDOWS_SDL_HINTS = {"SDL_JOYSTICK_THREAD": "1"}


def _prepare_sdl_hints() -> None:
    """Put the Windows-only SDL hints in place before SDL's joysticks start (T387)."""
    if sys.platform != "win32":
        return
    for name, value in _WINDOWS_SDL_HINTS.items():
        os.environ.setdefault(name, value)


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
# The mapped keys that also type, delete, or move the caret within a field.
# While a field that accepts typing has focus they reach the field: a player
# could not type an r into the console or an account name (T139). Left/Right
# move the caret within a line/editor instead of navigating, so a keyboard
# user can fix a typo mid-value. The vertical arrows, Return and Escape keep
# their pad meaning there: Up/Down still leave the field and A still opens the
# on-screen keyboard.
_TYPING_KEYS = frozenset(
    {
        int(Qt.Key.Key_L),
        int(Qt.Key.Key_R),
        int(Qt.Key.Key_Space),
        int(Qt.Key.Key_Backspace),
        int(Qt.Key.Key_Left),
        int(Qt.Key.Key_Right),
    }
)
# The mapped keys that are letters, and the only typing keys a list takes (T221).
# A list picks a row by the first letters of its name (Qt's type-ahead), so R in
# a character list chose the next sub-tab instead of Rexxar. Space, Backspace and
# the arrows stay the pad's there: a list has no caret, and Left/Right are the
# way out of it.
_LETTER_KEYS = frozenset({int(Qt.Key.Key_L), int(Qt.Key.Key_R)})
# The mapped ACTION keys an open `QMenu` reads for itself (T89): choose and close.
# The arrows reach it too, because no action is mapped to them, and so does every
# unmapped key; the other action keys stay the navigator's while a menu is open.
# Return must be here and not routed: `Navigator` chooses by sending the menu a
# Return, which would come back through this filter.
_MENU_KEYS = frozenset(
    {
        int(Qt.Key.Key_Return),
        int(Qt.Key.Key_Enter),
        int(Qt.Key.Key_Escape),
    }
)
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
    keys to move its cursor, which is a dead-end for navigation), and does not
    hand its focus to a proxy (T172). The navigator aims at a widget's centre,
    and `setFocus()` on one with a focus proxy lands on the proxy instead,
    somewhere else: a `QTabWidget` was scored at the middle of its page and
    put the focus on its tab bar, so Down from a page's top row went UP to the
    bar, and Left from a `QSpinBox` aimed at its own editor, whose proxy is the
    spinbox, and did nothing. The proxy is a stop of its own, at its own place,
    when it qualifies -- the tab bar is still where Up from the top row goes.
    """
    from PySide6.QtWidgets import QPlainTextEdit, QTextEdit

    def walk(w: QWidget) -> Iterable[QWidget]:
        policy = w.focusPolicy()
        if w is not root and (policy & Qt.FocusPolicy.TabFocus) and w.isEnabled() and w.isVisible():
            # read-only multi-line text is a D-pad trap; skip it.
            if isinstance(w, (QPlainTextEdit, QTextEdit)) and w.isReadOnly():
                pass
            elif w.focusProxy() is not None:
                # Its focus goes to the proxy, not to where it is (T172).
                pass
            else:
                yield w
        for child in w.children():
            if isinstance(child, QWidget) and child.isVisibleTo(root):
                yield from walk(child)

    yield from walk(root)


_OPPOSITE: dict[Direction, Direction] = {
    Direction.UP: Direction.DOWN,
    Direction.DOWN: Direction.UP,
    Direction.LEFT: Direction.RIGHT,
    Direction.RIGHT: Direction.LEFT,
}
"""The press that undoes a move (T175)."""

# The keys an open `QMenu` reads its own items with, one per logical event (T89).
_DIRECTION_TO_KEY: dict[Direction, Qt.Key] = {
    Direction.UP: Qt.Key.Key_Up,
    Direction.DOWN: Qt.Key.Key_Down,
    Direction.LEFT: Qt.Key.Key_Left,
    Direction.RIGHT: Qt.Key.Key_Right,
}


def _open_menu() -> QMenu | None:
    """The `QMenu` popup that is open now, or None (T89).

    A menu is the one popup the D-pad walk cannot move in: its items are
    `QAction`s, not widgets, so `_iter_focusable()` finds nothing inside it. Qt's
    own `QMenu.keyPressEvent` already walks the items -- skipping dead ones --
    chooses, and closes, so while one is open its keys are handed to it rather
    than navigated.
    """
    app = QApplication.instance()
    if isinstance(app, QApplication):
        popup = app.activePopupWidget()
        if isinstance(popup, QMenu):
            return popup
    return None


def _press_into(menu: QMenu, key: Qt.Key) -> bool:
    """Deliver `key` to `menu` as a press and a release; the menu does the rest.

    Sent, not posted, so the menu has moved (or chosen, and closed) before the
    pad's next event is resolved. It passes `KeyboardSource`'s filter on the way,
    which lets it through because it is one of the menu's own keys (`_MENU_KEYS`).
    """
    for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
        QApplication.sendEvent(menu, QKeyEvent(kind, key, Qt.KeyboardModifier.NoModifier))
    return True


def _center(w: QWidget, relative_to: QWidget) -> QPoint:
    """The widget's viewport center, in `relative_to`'s coordinate space."""
    top_left = w.mapTo(relative_to, QPoint(0, 0))
    return QPoint(top_left.x() + w.width() // 2, top_left.y() + w.height() // 2)


_Edges = tuple[int, int, int, int]
"""A widget's left, top, right and bottom, in some root's coordinates."""


def _edges(w: QWidget, relative_to: QWidget) -> _Edges:
    """The widget's left, top, right and bottom edges, in `relative_to`'s coordinate space."""
    top_left = w.mapTo(relative_to, QPoint(0, 0))
    return top_left.x(), top_left.y(), top_left.x() + w.width(), top_left.y() + w.height()


def _visible_edges(w: QWidget, root: QWidget, current: QWidget | None) -> _Edges | None:
    """The part of `w` a player can see, or None when none of it is on screen (T175).

    A module's buttons in the Modules tab's list, a Catalog tile's Install, sit
    inside a scroll area's viewport, and whatever is scrolled out of it is still
    visible to Qt: aimed at by where it would be drawn, it was a target nobody
    could see -- a chip 1655 px down a list 186 px tall at 960x640 was one
    press away. So `w` is cut by the viewport of every scroll area it is in.

    Except one the focus is in as well: in there, the next row is where the
    player is going, and it is scrolled to when focused (`_scroll_into_view`).
    Cut there too, the row under the last one showing would be nowhere, and the
    pad could not walk down a list longer than its box.
    """
    left, top, right, bottom = _edges(w, root)
    parent = w.parentWidget()
    while parent is not None and parent is not root:
        area = parent.parentWidget()
        shared = current is not None and parent.isAncestorOf(current)
        if isinstance(area, QAbstractScrollArea) and area.viewport() is parent and not shared:
            v_left, v_top, v_right, v_bottom = _edges(parent, root)
            left, top = max(left, v_left), max(top, v_top)
            right, bottom = min(right, v_right), min(bottom, v_bottom)
            if left >= right or top >= bottom:
                return None
        parent = parent.parentWidget()
    return left, top, right, bottom


def _shown_edges(w: QWidget, root: QWidget) -> _Edges:
    """The part of `w` that shows, cut by every scroll area it is in; all of it if none does."""
    return _visible_edges(w, root, None) or _edges(w, root)


def _scrolled_box_of(w: QWidget) -> QWidget | None:
    """The viewport of the innermost scroll area `w` is inside, or None (T175)."""
    parent = w.parentWidget()
    while parent is not None:
        area = parent.parentWidget()
        if isinstance(area, QAbstractScrollArea) and area.viewport() is parent:
            return parent
        parent = area
    return None


def _scroll_into_view(w: QWidget) -> None:
    """Scroll every scroll area `w` is in until `w` shows, innermost first (T175).

    The pad's focus moves with `setFocus()`, which scrolls nothing, so a row
    below the last one showing took the focus out of sight. Qt's own margin is
    kept: it brings part of the next row into view as well, which is what lets
    the next press see it from outside the list. Only a `QScrollArea` can be
    asked to; a list or table view (`_visible_edges` cuts by any scroll area)
    holds no focusable widgets of its own in this app, so none is missed.
    """
    parent = w.parentWidget()
    while parent is not None:
        if isinstance(parent, QScrollArea):
            content = parent.widget()
            if content is not None and content.isAncestorOf(w):
                parent.ensureWidgetVisible(w)
        parent = parent.parentWidget()


def _row_ahead(here: _Edges, there: _Edges, direction: Direction) -> tuple[int, int, int]:
    """`there` measured from `here` along `direction`: gap, depth, and distance aside (T175).

    `gap` is the space from `here`'s leading edge to `there`'s near one --
    negative when `there` starts before `here` ends, as a widget beside it on
    the same row does. `depth` is how far `there` reaches along the direction,
    to its far edge. `aside` is the space between the two across the
    direction: 0 when they share part of a column (Up/Down) or a row
    (Left/Right).
    """
    left, top, right, bottom = here
    o_left, o_top, o_right, o_bottom = there
    if direction in (Direction.UP, Direction.DOWN):
        aside = max(0, o_left - right, left - o_right)
        gap = o_top - bottom if direction is Direction.DOWN else top - o_bottom
        depth = gap + (o_bottom - o_top)
    else:
        aside = max(0, o_top - bottom, top - o_bottom)
        gap = o_left - right if direction is Direction.RIGHT else left - o_right
        depth = gap + (o_right - o_left)
    return gap, depth, aside


def _entry_line(box: _Edges, direction: Direction) -> _Edges:
    """The edge a press goes INTO a focused box by, as a line: its top for Down (T175).

    Measured from there, every stop inside the box lies ahead of it, and the
    nearest row is the first one that shows on that side.
    """
    left, top, right, bottom = box
    if direction is Direction.DOWN:
        return left, top, right, top
    if direction is Direction.UP:
        return left, bottom, right, bottom
    if direction is Direction.RIGHT:
        return left, top, left, bottom
    return right, top, right, bottom


BACK_CLOSES = "yulonBackCloses"
"""A top-level window's Qt property: B on the pad closes it, as it closes a dialog (T187).

The client launcher sets it. Not the main window: B there would quit the app.
"""


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
        # The last move the pad made: from where, to where, which way (T175).
        self._last_move: tuple[QWidget, QWidget, Direction] | None = None
        # The cache is a list of what was focusable WHEN it was walked, and a
        # widget shown, hidden, enabled or disabled since is a different list
        # (T153). Nothing that changes one of those calls `invalidate()` -- a
        # tab switched with a bumper, a report strip folding, the Modules tab's
        # custom-module card going to one line -- so the navigator watches for
        # the change itself. Measured before this, offscreen: after RB from the
        # Modules tab to Tuning the cache still held the Modules tab's 70
        # widgets, six of the Tuning tab's ten were out of reach, and the next
        # D-pad press put the focus on a hidden button of the tab just left.
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    _TREE_CHANGES = (QEvent.Type.Show, QEvent.Type.Hide, QEvent.Type.EnabledChange)
    """The events after which a cached focus chain may be wrong (T153).

    A widget's own `Show`/`Hide` arrives for an implicit change too -- the
    children of a tab page that is hidden are each sent one -- and
    `EnabledChange` likewise reaches every child of a widget that is greyed.
    """

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802  (Qt's own name)
        """Drop the caches when any widget's visibility or greying moves; eat nothing.

        Cheap on purpose, because it sees every event in the application: a
        clear of an already-empty dict is skipped, and the walk it forces is
        paid on the next D-pad press, not here. The pad's last move is
        forgotten likewise once the focus goes anywhere but where that move
        put it -- a click, a Tab, a dialog -- so `_way_back` only ever undoes
        the press just made (T175).
        """
        if self._cache and event.type() in self._TREE_CHANGES and isinstance(watched, QWidget):
            self._cache.clear()
        last = self._last_move
        if last is not None and event.type() == QEvent.Type.FocusIn and watched is not last[1]:
            self._last_move = None
        return False

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
        adopts a server, a tile is added); a widget shown, hidden, enabled or
        disabled invalidates it by itself (`eventFilter`, T153). A miss is also
        recovered here: a cached widget whose C++ object was deleted after the
        cache was built (a
        rebuilt tab, a removed tile) is a dangling wrapper — calling
        `mapTo()`/`setFocus()` on it raises `RuntimeError: Internal C++ object
        already deleted`. Dead entries are dropped on read, and a cache whose
        entries are all dead is re-walked, so navigation can never touch a
        deleted object even when a mutation forgot `invalidate()`.
        """
        key = id(root)
        cached = self._cache.get(key)
        if cached is not None:
            alive = [w for w in cached if shiboken6.isValid(w)]
            if alive:
                self._cache[key] = alive
                return alive
            self._cache.pop(key, None)
        found = [w for w in _iter_focusable(root) if shiboken6.isValid(w)]
        self._cache[key] = found
        return found

    def invalidate(self) -> None:
        """Drop the focus-chain caches; call after the widget tree changes."""
        self._cache.clear()

    # -- directional resolution ------------------------------------------

    def navigate(self, direction: Direction) -> bool:
        """Move focus one step in `direction`. Returns whether focus moved."""
        menu = _open_menu()
        if menu is not None:
            # T89: an open menu moves its own highlight; see `_open_menu()`.
            return _press_into(menu, _DIRECTION_TO_KEY[direction])
        root = self._context_root()
        candidates = self._focusable(root)
        current = QApplication.focusWidget()
        if current is not None and not shiboken6.isValid(current):
            current = None
        if current is None and candidates:
            # Nothing focused yet (first D-pad press on a fresh window): seed the
            # top-left-most focusable so there is a visible origin for both the
            # user's :focus ring and every later projection. Without this,
            # `origin` anchors at (0,0) and the first press can land arbitrarily.
            # Top-left of what SHOWS (T175): a list scrolled part way still has
            # its rows above the box at their places, out of sight.
            showing = [w for w in candidates if _visible_edges(w, root, None) is not None]
            current = self._top_left(showing or candidates, root)
            current.setFocus(Qt.FocusReason.OtherFocusReason)
            _scroll_into_view(current)
            return True

        origin = QPoint(0, 0)
        if current is not None:
            # From the part of it that shows (T175): a row cut off at the bottom
            # of its list is measured from the list's edge, not from where the
            # rest of it would be drawn.
            left, top, right, bottom = _shown_edges(current, root)
            origin = QPoint((left + right) // 2, (top + bottom) // 2)
        target = self._way_back(candidates, current, direction, root)
        if target is None:
            target = self._pick(candidates, current, origin, direction, root)
        if target is None:
            return False
        target.setFocus(Qt.FocusReason.OtherFocusReason)
        _scroll_into_view(target)
        self._last_move = (current, target, direction) if current is not None else None
        return True

    def _way_back(
        self,
        candidates: list[QWidget],
        current: QWidget | None,
        direction: Direction,
        root: QWidget,
    ) -> QWidget | None:
        """Where the last move came from, when this press undoes it; else None (T175).

        The nearest row is not a symmetric rule: Down from a narrow button onto
        a wide list, and Up from the list goes to whichever button above is
        best centred over it -- measured on the real window, about 460 of 1004
        presses were not undone by the opposite one. The pad remembers its own
        last move instead: the opposite press from where it landed goes back,
        if the focus has not moved since by any other means and the widget it
        came from is still a place the pad can stop that shows, and does not
        hold the focus.
        """
        last = self._last_move
        if last is None or current is None:
            return None
        came_from, went_to, went = last
        if current is not went_to or direction is not _OPPOSITE[went]:
            return None
        if not shiboken6.isValid(came_from) or came_from not in candidates:
            return None
        if came_from.isAncestorOf(current):
            # Nor back out to a box that holds the focus, any more than `_pick`
            # goes there: Down from the Modules list to its top row and Up went
            # back to the list, and the next Up entered it at its bottom row.
            return None
        if _visible_edges(came_from, root, current) is None:
            return None
        return came_from

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
        dropdowns and dialogs). A candidate is measured by the part of it on
        screen, and one with none on screen is not a candidate (T175,
        `_visible_edges`); nor is a box that holds the focused widget.

        Among ahead candidates, in order (T175):

        1. A stop that shows inside the focused widget -- a module's row with
           the Modules list focused, whichever way the press goes: the box is
           entered before it is left, at the edge the press goes in by (its top
           for Down, `_entry_line`), by the rule of 2 among its own stops.
        2. The nearest row (Up/Down): of the candidates that start past the
           focused widget's leading edge, those that start before the nearest
           of them ENDS, so no row is ever wholly passed over, wherever its
           stops stand. In that row, the one sharing the focused widget's
           column, else the least far aside, then the best centred. Left and
           Right do the same with the nearest column, but only among the
           candidates that share the focused widget's row. With the focus in
           a scrolled list, stops in that list come first; from outside a
           list the pad can stop on, the list comes before the stops in it.
           Nothing on the row: the same from each box that holds the focused
           widget, innermost first, among the stops outside it.
        3. Nothing qualifies: the old rule, the candidate most nearly in line
           (smallest offset), then the nearest.

        The old rule alone let Down jump whole rows: from the Tuning tab's conf
        list at 960x640 it went to the full-width "Last action" strip at the
        bottom, 0 px off-centre, past the three conf buttons straight under the
        list, 2 px off. When no candidate is ahead (an edge), wrap to the
        nearest candidate by perpendicular offset.
        """
        scored: list[tuple[float, float, _Edges, QWidget]] = []
        for other in candidates:
            if current is not None and other.isAncestorOf(current):
                # Not the focused widget, nor a box that holds it (T175): a press
                # goes past the box, never out to it. Taken as a candidate, the
                # Modules list -- level with the row the focus was on -- was
                # where Left went from its Install, and the next Left went back
                # in: the rail was never reached.
                continue
            seen = _visible_edges(other, root, current)
            if seen is None:
                continue
            center = QPoint((seen[0] + seen[2]) // 2, (seen[1] + seen[3]) // 2)
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
            scored.append((proj, offset, seen, other))

        if not scored:
            return None

        ahead = [s for s in scored if s[0] > 0]
        if current is not None:
            here = _shown_edges(current, root)
            inside = []
            for offset, seen, other in ((s[1], s[2], s[3]) for s in scored):
                if not current.isAncestorOf(other):
                    continue
                # Cut to what shows of the box itself (T195): a stop in a tile
                # is measured uncut by the shelf the focus is in, while the
                # tile's own edge here is cut by it. Uncut, an Install half
                # under the shelf's bottom edge lay behind the tile's leading
                # edge for Up, and the press went past it to the description.
                cut = (
                    max(seen[0], here[0]),
                    max(seen[1], here[1]),
                    min(seen[2], here[2]),
                    min(seen[3], here[3]),
                )
                if cut[0] < cut[2] and cut[1] < cut[3]:
                    inside.append((offset, cut, other))
            if inside:
                # The focused widget is a box with stops in it that show -- the
                # Modules list, the Catalog's shelf or one of its tiles: it is
                # entered before it is left, at its leading edge, by the same
                # nearest-row rule as below. Down goes to the top row that
                # shows, Up to the bottom one. Measured from the box's middle,
                # most in line first, Down from the Modules list at 960x640
                # went to a chip 100 px down, past the header and two rows.
                entry = _entry_line(here, direction)
                target = Navigator._nearest_row(inside, entry, direction, None)
                if target is not None:
                    return target
            ahead_seen = [(s[1], s[2], s[3]) for s in ahead]
            target = Navigator._nearest_row(ahead_seen, here, direction, _scrolled_box_of(current))
            if target is not None:
                return target
            # Nothing that way on the focused widget's own row: from each box
            # that holds it, innermost first, the nearest row past the box --
            # Left from a module's Install with nothing beside it goes on past
            # the Modules list to the rail beside the list.
            box = current.parentWidget()
            while box is not None and box is not root:
                if isinstance(box, QAbstractScrollArea) or box in candidates:
                    # Only what is outside the box. What shows inside it lies
                    # behind its edge anyway; a row scrolled out of the list
                    # the focus is in does not, and tier 2 above has already
                    # taken any that lies that way.
                    beyond = [(s[1], s[2], s[3]) for s in scored if not box.isAncestorOf(s[3])]
                    target = Navigator._nearest_row(
                        beyond, _shown_edges(box, root), direction, None
                    )
                    if target is not None:
                        return target
                box = box.parentWidget()
        if ahead:
            # Same row/column first, then nearest in the travel direction.
            ahead.sort(key=lambda s: (s[1], s[0]))
            return ahead[0][3]

        # Edge: nothing is ahead, so wrap. The target is the candidate closest to
        # the row/column we are leaving (smallest perpendicular offset); when
        # several tie there, prefer the one FURTHEST behind us so a single press
        # lands on the far extreme rather than crawling back one step. `proj` is
        # negative for all of these (none is ahead), so "smallest proj" means
        # "most negative" = furthest behind.
        scored.sort(key=lambda s: (s[1], s[0]))
        return scored[0][3]

    @staticmethod
    def _nearest_row(
        seen: list[tuple[float, _Edges, QWidget]],
        here: _Edges,
        direction: Direction,
        box: QWidget | None,
    ) -> QWidget | None:
        """The nearest row past `here` among `seen`, and the stop `_pick` takes in it (T175).

        `seen` is (offset, visible edges, widget) for each candidate, `box` the
        viewport of the scrolled list the focus is in, if any.
        """
        sideways = direction in (Direction.LEFT, Direction.RIGHT)
        past = []
        for offset, edges, other in seen:
            gap, depth, aside = _row_ahead(here, edges, direction)
            # Left and Right keep to the row: a nearest COLUMN taken across
            # the whole height of the Modules list sent Left from one
            # module's Install to a chip 900 px further down it.
            if gap >= 0 and (sideways is False or aside == 0):
                past.append((gap, depth, aside, offset, other))
        if box is not None:
            # The list the focus is in keeps it until nothing in the list
            # lies that way (T175), as a web page's scroll container does:
            # between two of the Modules list's groups the next group's
            # first row is further than the strip under the list, which
            # took Down out of the list and left half of it out of reach.
            within = [s for s in past if box.isAncestorOf(s[4])]
            past = within or past
        if not past:
            return None
        row_ends = min(s[1] for s in past)
        row = [s for s in past if s[0] < row_ends]
        # A list the pad can stop on is entered from outside at the list, not
        # past it at a row inside: whatever shows of a row in there starts no
        # nearer than the list's own edge. Taken at the row, the Catalog's
        # shelf was nobody's next stop at 1280x800 -- from the rail its first
        # tile, 4 px nearer level, won the tie.
        lists = [s[4] for s in row if isinstance(s[4], QAbstractScrollArea)]
        row = [s for s in row if not any(a is not s[4] and a.isAncestorOf(s[4]) for a in lists)]
        return min(row, key=lambda s: (s[2], s[3], s[0]))[4]

    # -- discrete actions -------------------------------------------------

    def perform(self, action: Action) -> bool:
        """Execute a discrete logical action in the active context."""
        menu = _open_menu()
        if menu is not None and action is not Action.BACK:
            # T89: A chooses the highlighted item (the menu closes itself), and
            # a bumper does nothing -- switching tab would leave the menu open
            # over a page its button is no longer on. B is `_back()`'s, below.
            if action is Action.CONFIRM:
                return _press_into(menu, Qt.Key.Key_Return)
            return False
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
            # Ask the platform for its on-screen keyboard (SteamOS/Windows),
            # unless the field is read-only — summoning a keyboard there would
            # be a dead, confusing interaction.
            if not widget.isReadOnly():
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
        # Back is "close the thing I am in": a popup, then a modal, then a window
        # that says B closes it (`BACK_CLOSES`: the client launcher, T187). If
        # none is open, it does nothing rather than yanking focus unpredictably
        # -- the main window never closes on B.
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
            active = app.activeWindow()
            if active is not None and active.property(BACK_CLOSES) is True:
                active.close()
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


def _accepts_typing(widget: QWidget | None, key: int) -> bool:
    """Whether `key` pressed on `widget` is typed into it rather than navigating.

    A field the user can type into takes every typing key (T139); a list takes
    only the letters, for its type-ahead (T221).
    """
    if key not in _TYPING_KEYS:
        return False
    if isinstance(widget, QLineEdit | QPlainTextEdit | QTextEdit | QAbstractSpinBox):
        return not widget.isReadOnly()
    if isinstance(widget, QComboBox):
        return widget.isEditable()
    if isinstance(widget, QAbstractItemView):
        return key in _LETTER_KEYS
    return False


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
            if _open_menu() is not None:
                # T89: an open menu walks, chooses and closes on its OWN keys
                # (`_open_menu()`); eaten here, Down did nothing and Return
                # chose nothing, so a menu opened from the pad could only be
                # closed again. Only those keys, though: the menu ignores
                # Backspace and Space, which are B and A under Steam Input's
                # keyboard emulation, so every other mapped key keeps its pad
                # meaning through the navigator -- which closes the menu on B,
                # chooses on A and does nothing on a bumper.
                if key in _KEY_TO_ACTION and key not in _MENU_KEYS:
                    self._navigator.perform(_KEY_TO_ACTION[key])
                    return True
                return False

            if key in _KEY_TO_DIRECTION:
                # Horizontal arrows double as typing keys: they move a caret in
                # a field, so while a text field has focus they reach it instead
                # of navigating (see `_TYPING_KEYS`). Every other direction key
                # is CONSUMED by the navigator, even when it dead-ends at an
                # edge: returning `False` here would otherwise let Up/Down move
                # a spinbox value or leak into a widget and desync focus.
                # Navigation either moved focus or intentionally did nothing —
                # the key must never leak.
                if _accepts_typing(QApplication.focusWidget(), key):
                    return False
                self._navigator.navigate(_KEY_TO_DIRECTION[key])
                return True

            if key in _KEY_TO_ACTION:
                if _accepts_typing(QApplication.focusWidget(), key):
                    return False
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
        # `navigator` is accepted but deliberately inert: the source is wired to
        # the Navigator in `install_gamepad_navigation()`, which connects the
        # emitted signals (`gamepad.direction -> navigator.navigate`), not by
        # storing a reference here. Keeping the parameter preserves the caller
        # symmetry and the thread-safety tests (`GamepadSource(navigator=None)`),
        # but a second caller wiring it again inside would double-drive focus.
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
        if not _assert_sdl_parity():
            # The mirrored SDL constants do not match this pygame build: every
            # button would be mis-bound. Degrade to the keyboard rather than
            # drive the UI with a wrong mapping (the keyboard source already
            # runs independent of pygame).
            return
        # Before ANY joystick init in this process: this probe is the first, and
        # the poller (and its rescans) inherit the environment it leaves.
        _prepare_sdl_hints()
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
        except pygame.error:
            # SDL failed to init (a headless box, a container with pygame
            # installed but no controller subsystem): nothing to read. Degrade
            # to the keyboard rather than aborting launch with a raw traceback.
            return
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
        # A `threading.Event`, not a bare bool: the GUI thread sets it from
        # `stop()` while the poller reads it, and an Event's flag is safe to
        # cross threads without assuming the GIL (the codebase-standard cancel
        # primitive — see `log_panel.py`/`update_progress.py`).
        self._stop = threading.Event()
        self._held: Direction | None = None
        self._held_since = 0.0
        self._last_emit = 0.0
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
            while not self._stop.is_set():
                # `_sdl2ctl.update()` is `SDL_GameControllerUpdate()`: it
                # refreshes the controller layer's POLLED state for the
                # `get_button`/`get_axis` reads below. `pygame.event.pump()`
                # is NOT used here — it requires `pygame.init()`'s video/event
                # system, which nothing initializes (we init only the controller
                # subsystem), so the first pump raises
                # `pygame.error: video system not initialized`. On Windows the
                # window messages SDL's RAWINPUT driver needs are pumped by
                # SDL's own thread instead (`_prepare_sdl_hints`, T387).
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
        """Signal the poll loop to break; the flag is thread-safe."""
        self._stop.set()

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
            self._last_emit = now
            self.direction.emit(direction)
            return
        # Same direction still held: after the initial delay, re-emit once per
        # interval so a held D-pad scrolls a list instead of stepping once.
        # The clock is `_last_emit` (monotonic time of the previous emit), not
        # arithmetic on the assumed poll cadence — a slow tick can neither
        # double-emit nor skip a repeat, because the emit is gated on real
        # elapsed time since the last one rather than a reconstructed count.
        elapsed = now - self._held_since
        if elapsed >= FIRST_REPEAT_S and now - self._last_emit >= REPEAT_S:
            self._last_emit = now
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
