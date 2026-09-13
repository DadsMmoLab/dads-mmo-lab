"""The QSS the Modules and Tuning tabs share (T44 item 6).

One module and not two copies of a string, because the two tabs are meant to
look like one surface: the owner's note on the press was "I want all the info
that was on this one, and same look", then "same with tuning", and a bevel
spelled twice is a bevel that can come to differ.

**Every colour here comes from `theme.py`'s `COLOR_*` constants and T44 forbids
inventing one.** That is not a style preference: `theme.py` is Baerthe's and
upstream carries its own passes on it, so a hex value typed here would be a
second palette to keep in step with a file this branch may not touch.
`test_the_panel_qss_invents_no_colour` reads every `#rrggbb` out of the sheet
and fails on one the theme does not export.

What it changes, and the whole of it:

* **The notch.** `theme.py` draws a `QGroupBox` title at `top left`; the
  mockups draw it centred over the top edge. Scoped to the two card object
  names rather than to `QGroupBox`, so the Modules tab's own "A module this app
  does not ship" box -- a sibling of the panel, not a card -- keeps the app's
  ordinary notch.
* **The bevel.** `theme.py`'s buttons are a flat sheet with one hairline. These
  get a lit top-left and a shadowed bottom-right, inverted while pressed. The
  total border thickness is 3px vertically in BOTH states on purpose: the
  theme's own comment on `QPushButton:pressed` records that a padding shift
  there once stopped a pressed button lining up with its neighbours, so the
  box must not change size -- only the content moves, which is what makes it
  read as a press.
"""

from __future__ import annotations

from yulon.ui.theme import (
    COLOR_BRASS_DEEP,
    COLOR_GOLD_BRASS,
)

CARD_OBJECT_NAMES: tuple[str, ...] = ("moduleFamilyCard", "tuningCard")
"""The two `QGroupBox` object names that are CARDS, and so take the centre notch."""


def panel_qss() -> str:
    """The shared sheet, for `ModulesPanel` and `TuningPanel` to set on themselves.

    A function rather than a constant so the test that reads its colours calls
    the same thing the panels do -- a constant built at import time and a
    function would be two answers to check.
    """
    notch = "\n".join(
        f"QGroupBox#{name}::title {{ subcontrol-position: top center; left: 0px; }}"
        for name in CARD_OBJECT_NAMES
    )
    return f"""
{notch}
QPushButton {{
    border-top: 1px solid {COLOR_GOLD_BRASS};
    border-left: 1px solid {COLOR_GOLD_BRASS};
    border-right: 1px solid {COLOR_BRASS_DEEP};
    border-bottom: 2px solid {COLOR_BRASS_DEEP};
}}
QPushButton:pressed {{
    border-top: 2px solid {COLOR_BRASS_DEEP};
    border-left: 1px solid {COLOR_BRASS_DEEP};
    border-right: 1px solid {COLOR_GOLD_BRASS};
    border-bottom: 1px solid {COLOR_GOLD_BRASS};
}}
"""


__all__ = ["CARD_OBJECT_NAMES", "panel_qss"]
