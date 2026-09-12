"""Tests for the Tuning tab's panel (T43 point 6).

Split the way T42 split `test_modules_panel.py`: the builder half needs no
`QApplication` at all, and the widget half asks the widgets what they drew
rather than reading pixels. Nothing here touches an install — `yulon.tuning`
is what reads and writes files, and `tests/test_tuning.py` is where that lives.
"""

from __future__ import annotations

from typing import Any

import pytest
from PySide6.QtWidgets import QCheckBox, QLineEdit, QSpinBox

from yulon import tuning
from yulon.ui.widgets import tuning_panel as tp

CONF = "env/dist/etc/modules/mod_npc_beastmaster.conf"
LUA = "env/dist/etc/modules/lua_scripts/SitMeansRest.lua"


def _row(**over: Any) -> tuning.TuningRow:
    file = over.pop("file", CONF)
    backend = tuning.backend_of(file)
    fields: dict[str, Any] = {
        "module_id": "mod-beast",
        "module_name": "NPC Beastmaster",
        "family": "module",
        "file": file,
        "key": "BeastMaster.Enable",
        "label": "Enable the Beastmaster NPC",
        "explain": None,
        "type": None,
        "min": None,
        "max": None,
        "default": None,
        "current": None,
        "installed": True,
        "backend": backend,
        "read_only_reason": tuning._read_only_reason(file, backend),
    }
    return tuning.TuningRow(**{**fields, **over})


# -- the builder ------------------------------------------------------------


def test_rows_are_grouped_into_one_card_per_module_in_the_order_they_arrived() -> None:
    rows = (
        _row(module_id="b", module_name="B", key="B1"),
        _row(module_id="a", module_name="A", key="A1"),
        _row(module_id="b", module_name="B", key="B2"),
    )
    cards = tp.build_tuning_cards(rows)
    assert [card.module_id for card in cards] == ["b", "a"]
    assert [row.key for row in cards[0].rows] == ["B1", "B2"]


def test_a_card_names_every_file_its_rows_live_in_once_each() -> None:
    """Beastmaster declares two: its own conf and the core's `worldserver.conf`."""
    core = "env/dist/etc/worldserver.conf"
    card = tp.build_tuning_cards(
        (_row(key="A"), _row(key="B"), _row(file=core, key="Creatures.CustomIDs"))
    )[0]
    assert card.files == (CONF, core)


def test_a_card_is_priced_at_its_most_expensive_row() -> None:
    card = tp.build_tuning_cards((_row(file=LUA), _row()))[0]
    assert card.rule == "restart"
    assert card.rule_sentence == tuning.apply_sentence("restart")


def test_a_card_of_nothing_but_read_only_rows_owes_nothing() -> None:
    card = tp.build_tuning_cards((_row(file=LUA, key="DURATION"),))[0]
    assert card.rule == "read-only"
    assert card.editable is False


# -- the control the safety rule picks --------------------------------------


@pytest.mark.parametrize(
    ("row", "kind"),
    [
        (_row(type="bool"), "switch"),
        (_row(type="int", min=0, max=80), "spinner"),
        (_row(type="int"), "box"),
        (_row(type="int", min=0), "box"),
        (_row(type="int", max=80), "box"),
        (_row(type="list"), "box"),
        (_row(type="text"), "box"),
        (_row(), "box"),
        (_row(file=LUA), "none"),
    ],
)
def test_a_key_gets_the_control_its_own_type_earns_and_never_a_wider_one(
    row: tuning.TuningRow, kind: str
) -> None:
    """T43's safety rule, in one function.

    No `type` is a TEXT BOX. An `int` with only one bound, or none, is a text
    box too: a spinner cannot exist without a range, and a range this app
    invented would refuse values the module is happy with.
    """
    assert tp.control_kind(row) == kind


def test_a_row_starts_at_what_the_file_says_and_falls_back_to_the_default() -> None:
    assert tp.starting_value(_row(current="0", default="1")) == "0"
    assert tp.starting_value(_row(current=None, default="1")) == "1"
    assert tp.starting_value(_row(current=None, default=None)) == ""


def test_a_key_the_file_does_not_carry_says_so_instead_of_looking_set() -> None:
    """73 of the 107 shipped keys have no default and most have no line either."""
    assert tp.value_note(_row(current=None, default="1")) == tp.NOT_IN_THE_FILE
    assert tp.value_note(_row(current=None, default=None)) == tp.NOT_IN_THE_FILE
    assert tp.value_note(_row(current="1", default="1")) is None


# -- the widgets ------------------------------------------------------------


def test_a_bool_key_draws_a_switch_and_reads_back_the_files_own_spelling(
    qapp: object,
) -> None:
    editor = tp.RowEditor(_row(type="bool", current="true"))
    assert isinstance(editor.control, QCheckBox)
    assert editor.control.isChecked() and editor.value() == "true"
    editor.control.setChecked(False)
    assert editor.value() == "false"


def test_a_bool_key_in_a_zero_one_file_comes_back_as_zero_or_one(qapp: object) -> None:
    """The spelling is the FILE's, not the tab's: a conf of 1s must not gain a `true`."""
    editor = tp.RowEditor(_row(type="bool", current="1"))
    editor.control.setChecked(False)
    assert editor.value() == "0"


def test_a_bounded_int_draws_a_spinner_inside_its_own_bounds(qapp: object) -> None:
    editor = tp.RowEditor(_row(type="int", min=0, max=80, current="30"))
    assert isinstance(editor.control, QSpinBox)
    assert (editor.control.minimum(), editor.control.maximum()) == (0, 80)
    assert editor.value() == "30"


def test_an_unbounded_int_draws_a_text_box_so_nothing_clamps_it(qapp: object) -> None:
    editor = tp.RowEditor(_row(type="int", current="900000"))
    assert isinstance(editor.control, QLineEdit)
    assert editor.value() == "900000"


def test_a_key_with_no_type_draws_a_text_box(qapp: object) -> None:
    editor = tp.RowEditor(_row(current="anything at all"))
    assert isinstance(editor.control, QLineEdit)
    assert editor.value() == "anything at all"


def test_a_read_only_row_has_no_control_and_says_why(qapp: object) -> None:
    editor = tp.RowEditor(_row(file=LUA, key="DURATION", current="20"))
    assert editor.control is None
    assert editor.value() == "20"
    assert editor.reason_label is not None
    assert editor.reason_label.text() == tuning.LUA_IS_NOT_IN_V1


def test_a_changed_row_is_marked_and_says_what_it_changed_from(qapp: object) -> None:
    editor = tp.RowEditor(_row(type="int", min=0, max=80, current="30"))
    assert not editor.changed and editor.changed_label.isHidden()
    editor.control.setValue(40)
    assert editor.changed
    assert editor.changed_label.text() == tp.CHANGED_FROM.format(old="30")
    editor.control.setValue(30)
    assert not editor.changed
    # The MARK, not just the property: asserting `changed` alone left
    # `setVisible(True)` unconditional and the test still passed (mutation M51).
    assert editor.changed_label.isHidden()


def test_a_row_that_was_not_in_the_file_says_it_changed_from_nothing(qapp: object) -> None:
    editor = tp.RowEditor(_row(current=None, default=None))
    editor.control.setText("7")
    assert editor.changed_label.text() == tp.CHANGED_FROM.format(old=tp.NOTHING)


def test_a_card_offers_only_the_keys_that_changed(qapp: object) -> None:
    """Save writes what the user moved and leaves the rest of the file alone."""
    card = tp.CardWidget(
        tp.build_tuning_cards((_row(key="A", current="1"), _row(key="B", current="2")))[0]
    )
    assert card.edits() == {}
    card.editors["A"].control.setText("9")
    assert card.edits() == {"A": "9"}


def test_a_card_whose_rows_are_all_read_only_offers_no_save(qapp: object) -> None:
    card = tp.CardWidget(tp.build_tuning_cards((_row(file=LUA, key="DURATION"),))[0])
    assert card.save_button is None and card.revert_button is None


def test_a_cards_save_and_revert_name_their_own_module(qapp: object) -> None:
    card = tp.CardWidget(tp.build_tuning_cards((_row(key="A"),))[0])
    saved: list[str] = []
    reverted: list[str] = []
    card.save_pressed.connect(saved.append)
    card.revert_pressed.connect(reverted.append)
    assert card.save_button is not None and card.revert_button is not None
    card.save_button.click()
    card.revert_button.click()
    assert saved == ["mod-beast"] and reverted == ["mod-beast"]


def test_the_panel_draws_a_card_per_module_and_finds_one_by_id(qapp: object) -> None:
    panel = tp.TuningPanel()
    panel.set_cards(
        tp.build_tuning_cards(
            (_row(module_id="a", module_name="A", key="A1"), _row(module_id="b", key="B1"))
        )
    )
    assert [card.card.module_id for card in panel.cards()] == ["a", "b"]
    assert panel.card("b").card.module_name == "NPC Beastmaster"
    with pytest.raises(KeyError):
        panel.card("gone")


def test_a_panel_with_nothing_to_tune_says_so(qapp: object) -> None:
    panel = tp.TuningPanel()
    panel.set_cards(())
    assert not panel.empty_label.isHidden()
    panel.set_cards(tp.build_tuning_cards((_row(),)))
    assert panel.empty_label.isHidden()


def test_the_panel_keeps_the_users_edits_out_of_a_redraw(qapp: object) -> None:
    """`set_cards()` rebuilds; a reload the user did not ask for must not do it."""
    panel = tp.TuningPanel()
    cards = tp.build_tuning_cards((_row(key="A", current="1"),))
    panel.set_cards(cards)
    panel.card("mod-beast").editors["A"].control.setText("9")
    assert panel.edits("mod-beast") == {"A": "9"}


def test_busy_disables_every_card_and_gives_them_back(qapp: object) -> None:
    panel = tp.TuningPanel()
    panel.set_cards(tp.build_tuning_cards((_row(key="A"),)))
    card = panel.card("mod-beast")
    assert card.save_button is not None
    panel.set_enabled_actions(False)
    assert not card.save_button.isEnabled()
    panel.set_enabled_actions(True)
    assert card.save_button.isEnabled()


# -- the raw editor beside the cards ----------------------------------------


def test_the_file_box_lists_the_files_it_was_handed_and_names_the_one_picked(
    qapp: object,
) -> None:
    panel = tp.TuningPanel()
    picked: list[str] = []
    panel.file_selected.connect(picked.append)
    panel.set_files((CONF, "env/dist/etc/worldserver.conf"))
    assert [panel.files.itemText(i) for i in range(panel.files.count())] == [
        CONF,
        "env/dist/etc/worldserver.conf",
    ]
    panel.files.setCurrentIndex(1)
    assert picked[-1] == "env/dist/etc/worldserver.conf"


def test_a_read_only_file_is_shown_and_cannot_be_saved(qapp: object) -> None:
    """`worldserver.conf` is core configuration, not a module's (T43's follow-up)."""
    panel = tp.TuningPanel()
    panel.set_file_text("[worldserver]\n", read_only=True, note="core configuration")
    assert panel.editor.isReadOnly()
    assert not panel.file_save_button.isEnabled()
    assert "core configuration" in panel.file_note.text()


def test_an_editable_file_lints_live_and_never_blocks(qapp: object) -> None:
    panel = tp.TuningPanel()
    panel.set_file_text("A = 1\n", read_only=False, note=None)
    assert panel.lint_label.text() == ""
    assert panel.file_save_button.isEnabled()
    panel.editor.setPlainText("A = 1\nnot a setting\n")
    said = panel.lint_label.text()
    assert "Line 2" in said and "not a setting" in said
    assert panel.file_save_button.isEnabled(), "the guard warns; it never blocks"


def test_the_file_save_hands_up_the_text_that_is_in_the_box(qapp: object) -> None:
    panel = tp.TuningPanel()
    sent: list[str] = []
    panel.file_save_pressed.connect(sent.append)
    panel.set_file_text("A = 1\n", read_only=False, note=None)
    panel.editor.setPlainText("A = 2\n")
    panel.file_save_button.click()
    assert sent == ["A = 2\n"]


def test_a_narrow_window_stacks_the_two_halves(qapp: object) -> None:
    from PySide6.QtCore import Qt

    panel = tp.TuningPanel()
    # Shown, because a hidden widget is never sent a resize event: the same
    # test written against `resize()` alone passed with the whole rule deleted.
    panel.show()
    panel.resize(1200, 600)
    assert panel.split.orientation() == Qt.Orientation.Horizontal
    panel.resize(500, 600)
    assert panel.split.orientation() == Qt.Orientation.Vertical
    panel.close()
