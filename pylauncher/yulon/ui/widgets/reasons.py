"""Why a press is greyed, said on the tab and on hover (T195, I3/A10/A23/I4).

A Steam Deck has no hover, and a disabled button takes no focus, so a reason
kept only in a tooltip is one a Deck player never reads. `set_enabled_why()`
greys a press with its reason as the tooltip and hands the reason to a
`ReasonLine`, a muted line on the same tab that shows the reasons of every
greyed press it watches, one per line, and hides itself when there are none.

Most of the view still re-enables presses with a plain `setEnabled(True)`, so
the line watches its presses' own enabled and shown events: a press enabled
by any route drops its reason and gets its own tooltip back, and nothing on
the line outlives the state it explained. It also watches every widget
between a press and the one that holds both it and the line, because hiding a
box around a press (the Docker banner around its reinstall press) hides the
press without telling it.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QLabel, QWidget
from shiboken6 import isValid

from yulon.ui.theme import COLOR_TEXT_MUTED

LINE_NAME = "reason-line"
"""The object name the line's own style sheet selects it by (an ID selector only)."""

_REASON = "yulon_reason"
"""The reason the press was last greyed with by `set_enabled_why()`, or empty."""

_STANDING = "yulon_standing_reason"
"""The reason `ReasonLine.watch()` gives the press whenever it is greyed without one."""

_BASE_TIP = "yulon_base_tooltip"
"""The press's own tooltip, put back when it is enabled again."""

_SHOWN_TIP = "yulon_shown_reason"
"""The reason the tooltip was set to, so a tooltip somebody set since is left alone."""

_LINE = "yulon_reason_line"
"""The line watching the press, so a later call need not name it again."""


def reason_of(press: QWidget) -> str:
    """Why `press` is greyed: the reason it was greyed with, else its standing one, else ""."""
    if press.isEnabled():
        return ""
    return str(press.property(_REASON) or press.property(_STANDING) or "")


def _show_tip(press: QWidget, reason: str) -> None:
    """Put `reason` in the tooltip, keeping the press's own one to give back."""
    if press.property(_SHOWN_TIP) is None:
        press.setProperty(_BASE_TIP, press.toolTip())
    press.setToolTip(reason)
    press.setProperty(_SHOWN_TIP, reason)


def _give_tip_back(press: QWidget) -> None:
    """The press's own tooltip again, unless somebody has set another since the reason."""
    shown = press.property(_SHOWN_TIP)
    if shown is None:
        return
    if press.toolTip() == shown:
        press.setToolTip(str(press.property(_BASE_TIP) or ""))
    press.setProperty(_SHOWN_TIP, None)
    press.setProperty(_BASE_TIP, None)


def _settle_tip(press: QWidget) -> None:
    """The tooltip a press in its current state should carry."""
    reason = reason_of(press)
    if reason:
        _show_tip(press, reason)
    else:
        _give_tip_back(press)


class ReasonLine(QLabel):
    """A muted, word-wrapped, selectable line of the reasons its greyed presses carry.

    In the order the presses were watched, each reason once, and only for a
    press that would be drawn: a hidden press explains nothing.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self.setObjectName(LINE_NAME)
        self.setStyleSheet(f"QLabel#{LINE_NAME} {{ color: {COLOR_TEXT_MUTED}; }}")
        self.setWordWrap(True)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self._presses: list[QWidget] = []
        # The widgets between each press and the holder it shares with this
        # line, whose own showing and hiding moves what the line says.
        self._between: list[QWidget] = []
        self.setVisible(False)

    def watch(self, press: QWidget, *, standing: str | None = None) -> None:
        """Explain `press` on this line; `standing` is its reason whenever it is greyed."""
        if standing is not None:
            press.setProperty(_STANDING, standing)
        if press not in self._presses:
            self._presses.append(press)
            press.installEventFilter(self)
        press.setProperty(_LINE, self)
        _settle_tip(press)
        self._hook()
        self.refresh()

    def reasons(self) -> list[str]:
        """The reasons on the line now, in the order the presses were watched."""
        said: list[str] = []
        self._presses = [press for press in self._presses if isValid(press)]
        for press in self._presses:
            reason = reason_of(press)
            if reason and reason not in said and self._drawn(press):
                said.append(reason)
        return said

    def refresh(self) -> None:
        """Say the reasons there are now, or hide."""
        said = self.reasons()
        self.setText("\n".join(said))
        self.setVisible(bool(said))

    def _hook(self) -> None:
        """Watch every widget between each press and the holder it shares with this line.

        Asked again whenever a press, the line or one of those widgets moves
        to another parent, which is what a layout does to all of them.
        """
        self._between = [node for node in self._between if isValid(node)]
        for press in self._presses:
            if not isValid(press):
                continue
            node = press.parentWidget()
            while node is not None and not node.isAncestorOf(self):
                if node not in self._between:
                    node.installEventFilter(self)
                    self._between.append(node)
                node = node.parentWidget()

    def event(self, event: QEvent) -> bool:
        # Asked of `_presses` too: Qt can send this from inside the constructor.
        if event.type() == QEvent.Type.ParentChange and hasattr(self, "_presses"):
            self._hook()
            self.refresh()
        return bool(super().event(event))

    def _drawn(self, press: QWidget) -> bool:
        """Whether `press` is shown inside the widget that holds both it and this line.

        Asked of their common ancestor and not the window, so a line on a tab
        that is not the current one still knows what that tab will show.
        """
        holder = press.parentWidget()
        while holder is not None and not holder.isAncestorOf(self):
            holder = holder.parentWidget()
        return press.isVisibleTo(holder) if holder is not None else not press.isHidden()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        kind = event.type()
        if kind == QEvent.Type.ParentChange:
            self._hook()
            self.refresh()
        elif (
            kind == QEvent.Type.EnabledChange
            and isinstance(watched, QWidget)
            and watched in self._presses
        ):
            if watched.isEnabled():
                # Enabled by any route: whatever it was greyed for is over.
                watched.setProperty(_REASON, None)
            _settle_tip(watched)
            self.refresh()
        elif kind in (QEvent.Type.ShowToParent, QEvent.Type.HideToParent):
            self.refresh()
        return False


def set_enabled_why(press: QWidget, reason: str | None, line: ReasonLine | None = None) -> None:
    """Enable `press` (`reason` None) or grey it, saying why on its tooltip and `line`.

    `line` is the one watching it; once named, a later call may leave it out.
    An empty reason is refused: a greyed press with nothing to say is the
    thing this module exists to stop.
    """
    if reason is not None and not reason.strip():
        raise ValueError("a greyed press needs a reason; pass None to enable it")
    if line is not None and press.property(_LINE) is not line:
        line.watch(press)
    watching = press.property(_LINE)
    press.setProperty(_REASON, reason)
    press.setEnabled(reason is None)
    _settle_tip(press)
    if isinstance(watching, ReasonLine):
        watching.refresh()


def drop_reason(press: QWidget) -> None:
    """Forget why `press` was greyed, leaving it greyed (its standing reason still holds)."""
    press.setProperty(_REASON, None)
    _settle_tip(press)
    watching = press.property(_LINE)
    if isinstance(watching, ReasonLine):
        watching.refresh()
