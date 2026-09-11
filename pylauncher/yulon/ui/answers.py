"""Reading a `QMessageBox.question()` answer without the identity trap (T33).

Measured on PySide6 6.11.2, Python 3.11 and 3.13 (both CI legs, `2026-09-11`,
`pyplan/gates/t33-yes-reads-as-no-2026-09-11/static_probe.py`): the **static** `QMessageBox.question(...)`
returns a plain `int` -- `16384` for Yes, `65536` for No -- not a
`QMessageBox.StandardButton` member. An instance's `standardButton(clickedButton())`
still returns the member, so the two call shapes hand back different types for
the same button. `StandardButton` is int-backed, so `==` holds either way;
`is`/`is not` does not, and was always False against the static call's `int`.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox


def said_yes(answer: object) -> bool:
    """Whether a `QMessageBox.question()` answer was Yes, on either PySide6 return shape.

    `==`, not `is`/`is not` -- see the module docstring. `NoButton` (0, what the
    static call returns when the dialog is dismissed with Escape or the window's
    close button) is not equal to `Yes`, so a dismissed dialog still reads as a
    refusal here, the same as it did through the `is`/`is not` spelling this
    replaces.
    """
    return answer == QMessageBox.StandardButton.Yes
