"""Tests for the QSS the Modules and Tuning tabs share (T44 item 6)."""

from __future__ import annotations

import re

from yulon.ui import theme
from yulon.ui.widgets import modules_panel as mp
from yulon.ui.widgets import panel_style
from yulon.ui.widgets import tuning_panel as tp

_HEX = re.compile(r"#[0-9A-Fa-f]{3,8}")


def _theme_colours() -> set[str]:
    return {
        value.lower()
        for name, value in vars(theme).items()
        if name.startswith("COLOR_") and isinstance(value, str)
    }


def test_the_panel_qss_invents_no_colour() -> None:
    """T44 forbids a new colour, and a forbidden thing needs a test that can see it.

    `theme.py` is a file this branch may not edit and upstream carries its own
    passes on it, so a hex value typed into a panel is a second palette to keep
    in step with a file nobody here may touch.

    Mutation: hard-code any hex into `panel_qss()` -- including one the theme
    happens to use elsewhere but does not export as a `COLOR_*` -- and this
    fails naming it.
    """
    used = {value.lower() for value in _HEX.findall(panel_style.panel_qss())}

    assert used <= _theme_colours(), sorted(used - _theme_colours())
    assert used, "a sheet with no colour in it is not painting anything"


def test_the_notch_moves_to_the_centre_for_the_two_card_kinds_and_nothing_else() -> None:
    """The mockups' notch, scoped to the CARDS (T44 item 6).

    Unscoped it would also move the Modules tab's "A module this app does not
    ship" box, which is not a card and is not in the mockup's change.

    Mutation: drop the object-name qualifier and the selector matches every
    QGroupBox the sheet reaches.
    """
    qss = panel_style.panel_qss()

    for name in panel_style.CARD_OBJECT_NAMES:
        assert f"QGroupBox#{name}::title" in qss
    assert "subcontrol-position: top center" in qss
    assert "\nQGroupBox::title" not in qss


def test_a_pressed_button_keeps_the_same_box_it_had() -> None:
    """The bevel moves the content, never the box.

    `theme.py`'s own comment on `QPushButton:pressed` records that a padding
    shift there once stopped a pressed button lining up with its neighbours
    down a row, so the total border has to weigh the same in both states.

    Mutation: make the pressed top border 3px and a pressed button in a row of
    them grows by a pixel and nudges the rest.
    """
    qss = panel_style.panel_qss()
    rules = qss.split("QPushButton:pressed")
    normal, pressed = rules[0], rules[1]

    def weight(block: str, edge: str) -> int:
        found = re.search(rf"border-{edge}: (\d+)px", block)
        assert found is not None, (edge, block)
        return int(found.group(1))

    assert weight(normal, "top") + weight(normal, "bottom") == 3
    assert weight(pressed, "top") + weight(pressed, "bottom") == 3
    assert weight(normal, "top") != weight(pressed, "top"), "the bevel has to invert"


def test_both_panels_wear_the_shared_sheet(qapp: object) -> None:
    """One surface, one sheet. Mutation: set it on one panel and the two tabs differ."""
    assert mp.ModulesPanel().styleSheet() == panel_style.panel_qss()
    assert tp.TuningPanel().styleSheet() == panel_style.panel_qss()


def test_both_card_kinds_carry_the_object_name_the_sheet_selects(qapp: object) -> None:
    """A selector that names an object nothing is called styles nothing.

    Mutation: rename `setObjectName("tuningCard")` and the Tuning tab's cards
    keep the app's left notch while `panel_qss()` goes on claiming otherwise.
    """
    from yulon import tuning

    row = tuning.TuningRow(
        module_id="m",
        module_name="M",
        family="module",
        file="env/dist/etc/modules/m.conf",
        key="K",
        label="K",
        explain=None,
        type=None,
        min=None,
        max=None,
        default=None,
        current="1",
        installed=True,
        backend="conf",
        read_only_reason=None,
    )
    panel = mp.ModulesPanel()
    panel.set_rows(
        mp.build_module_rows(
            [],
            {"module": frozenset({"mod-hand"})},
            mp.SessionState(),
            None,
        )
    )
    card = panel._cards["module"]
    assert card.objectName() == "moduleFamilyCard"

    assert tp.CardWidget(tp.build_tuning_cards((row,))[0]).objectName() == "tuningCard"
