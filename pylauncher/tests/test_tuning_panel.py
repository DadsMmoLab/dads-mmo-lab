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


def test_a_card_names_every_job_its_rows_owe_and_a_read_only_row_owes_none() -> None:
    card = tp.build_tuning_cards((_row(file=LUA), _row()))[0]
    assert card.rules == ("restart",)
    assert card.rule_sentence == tuning.apply_sentence("restart")


def test_a_card_of_nothing_but_read_only_rows_owes_nothing() -> None:
    card = tp.build_tuning_cards((_row(file=LUA, key="DURATION"),))[0]
    assert card.rules == ()
    assert card.rule_sentence == tuning.apply_sentence("read-only")
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
    assert editor.changed_label.text() == tp.CHANGED_FROM.format(key="BeastMaster.Enable", old="30")
    editor.control.setValue(30)
    assert not editor.changed
    # The MARK, not just the property: asserting `changed` alone left
    # `setVisible(True)` unconditional and the test still passed (mutation M51).
    assert editor.changed_label.isHidden()


def test_a_row_that_was_not_in_the_file_says_it_changed_from_nothing(qapp: object) -> None:
    editor = tp.RowEditor(_row(current=None, default=None))
    editor.control.setText("7")
    assert editor.changed_label.text() == tp.CHANGED_FROM.format(
        key="BeastMaster.Enable", old=tp.NOTHING
    )


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
    saved: list[tuple[str, str]] = []
    reverted: list[tuple[str, str]] = []
    card.save_pressed.connect(lambda family, item: saved.append((family, item)))
    card.revert_pressed.connect(lambda family, item: reverted.append((family, item)))
    assert card.save_button is not None and card.revert_button is not None
    card.save_button.click()
    card.revert_button.click()
    assert saved == [("module", "mod-beast")]
    assert reverted == [("module", "mod-beast")]


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
    # T44 item 13: buttons, not a combo box. The FILE each one stands for is
    # its tooltip -- the label is a basename and is not the identity.
    assert [b.toolTip() for b in panel.file_buttons()] == [
        CONF,
        "env/dist/etc/worldserver.conf",
    ]
    panel.file_buttons()[1].click()
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
    # T44 item 14: a clean file now says so, where T43 left the label empty.
    assert panel.lint_label.text() == tp.LINT_OK
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


def test_a_switch_over_a_key_the_file_never_carried_is_not_changed_until_it_is_moved(
    qapp: object,
) -> None:
    """A checkbox cannot draw "no value": unchecked and off look the same.

    Found by the full gate after point 7 typed the shipped keys. Five of
    `mod-transmog`'s keys are `bool` with no value in the conf and no default, so
    every card opened with five unchecked boxes whose starting value was `""` --
    and `value() != start` made all five read as CHANGED. Pressing Save on a
    card nobody had touched wrote five keys.
    """
    editor = tp.RowEditor(_row(type="bool", current=None, default=None))
    assert editor.value() == "0"
    assert not editor.changed, "an untouched switch reported a change nobody made"
    assert editor.changed_label.isHidden()
    editor.control.setChecked(True)
    assert editor.changed and editor.value() == "1"
    editor.control.setChecked(False)
    # Back to off, which IS a change from "no value at all": saving it writes
    # the key for the first time, which is the whole point of the tab.
    assert editor.changed
    assert editor.changed_label.text() == tp.CHANGED_FROM.format(
        key="BeastMaster.Enable", old=tp.NOTHING
    )


def test_a_spinner_over_a_key_the_file_never_carried_is_not_changed_until_it_is_moved(
    qapp: object,
) -> None:
    """The same hole one control along: a spinner sits at its minimum, not at nothing."""
    editor = tp.RowEditor(_row(type="int", min=5, max=80, current=None, default=None))
    assert editor.value() == "5" and not editor.changed
    editor.control.setValue(6)
    assert editor.changed


def test_a_text_box_typed_into_and_cleared_again_is_not_changed(qapp: object) -> None:
    editor = tp.RowEditor(_row(current="1"))
    editor.control.setText("2")
    assert editor.changed
    editor.control.setText("1")
    assert not editor.changed


def test_a_card_whose_rows_cost_two_different_things_says_both() -> None:
    """One module, two files, two costs — and the cheaper job must not vanish.

    The shipped catalog cannot produce this today (every conf it names is under
    `env/dist/etc/`), which is exactly why it is asserted here with synthetic
    rows rather than left to the first module that can.
    """
    card = tp.build_tuning_cards((_row(key="A"), _row(file="etc/elsewhere.conf", key="B")))[0]
    assert card.rules == ("recreate", "restart")
    said = card.rule_sentence
    assert tuning.apply_sentence("recreate") in said
    assert tuning.apply_sentence("restart") in said


# -- the collision T42 round 2 found, one tab along --------------------------


def _twin(family: str, key: str, file: str) -> tuning.TuningRow:
    return _row(module_id="bmah", module_name="BMAH", family=family, key=key, file=file)


def test_an_id_in_two_families_gets_two_cards_not_one_merged_card() -> None:
    """Nothing makes a manifest id unique across families (T42 round 2).

    Grouped by id alone, an `ale` and a `keg` sharing `bmah` became ONE card
    titled with the first family's name, holding both families' rows — and
    `save_tuning()` would then read the whole card's spec out of the FIRST
    family's manifest, type-checking one module's values against another's
    declarations before writing them to the other one's file.
    """
    cards = tp.build_tuning_cards(
        (
            _twin("ale", "A", "env/dist/etc/modules/lua_scripts/a.lua"),
            _twin("keg", "B", "env/dist/etc/modules/k.conf"),
        )
    )
    assert [(card.family, card.module_id) for card in cards] == [
        ("ale", "bmah"),
        ("keg", "bmah"),
    ]
    assert [row.key for row in cards[0].rows] == ["A"]
    assert [row.key for row in cards[1].rows] == ["B"]


def test_the_panel_addresses_both_families_of_a_shared_id(qapp: object) -> None:
    panel = tp.TuningPanel()
    panel.set_cards(
        tp.build_tuning_cards(
            (
                _twin("ale", "A", "env/dist/etc/modules/lua_scripts/a.lua"),
                _twin("keg", "B", "env/dist/etc/modules/k.conf"),
            )
        )
    )
    assert len(panel.cards()) == 2
    # The bare id keeps working and resolves in `FAMILY_FILES` order, which is
    # `ModulesPanel._key_for()`'s rule rather than a second one invented here.
    assert panel.card("bmah").card.family == "ale"
    assert panel.card(("keg", "bmah")).card.family == "keg"


def test_a_cards_save_carries_its_family_so_the_view_never_guesses(qapp: object) -> None:
    """The press names the card it happened on, not an id two cards can answer to.

    `ModulesPanel` solved the same thing by letting a click carry its widget
    (T42 round 2). Here the card already knows its family, so the signal
    carries it and `save_tuning()` has nothing left to resolve.
    """
    panel = tp.TuningPanel()
    panel.set_cards(
        tp.build_tuning_cards(
            (
                _twin("ale", "A", "env/dist/etc/modules/lua_scripts/a.lua"),
                _twin("keg", "B", "env/dist/etc/modules/k.conf"),
            )
        )
    )
    saved: list[tuple[str, str]] = []
    reverted: list[tuple[str, str]] = []
    panel.save_pressed.connect(lambda family, item: saved.append((family, item)))
    panel.revert_pressed.connect(lambda family, item: reverted.append((family, item)))
    keg = panel.card(("keg", "bmah"))
    assert keg.save_button is not None and keg.revert_button is not None
    keg.save_button.click()
    keg.revert_button.click()
    assert saved == [("keg", "bmah")] and reverted == [("keg", "bmah")]


# -- what the tab already knew and threw away (T44 items 9-12, 14) -----------


def test_a_row_that_cannot_be_written_says_so_in_a_chip() -> None:
    """Item 9's third chip, out of a fact `TuningRow` has carried since T43.

    `read_only_reason` is already drawn as a SENTENCE under the row; the chip
    is the same fact at a glance, so a person scanning a card of twelve
    settings can see which of them are not theirs to change.

    Mutation: return `()` for a read-only row and the only mark left is a
    paragraph of prose halfway down the row.
    """
    assert tp.row_chips(_row(file=LUA)) == (tp.CHIP_READ_ONLY,)


def test_a_key_with_no_declared_type_is_chipped_as_free_text() -> None:
    """Item 9's second chip. The fact is `control_kind() == "box" and type is None`.

    That pair is exactly T43's safety rule: a key the catalog says nothing
    about gets the one control that can express anything, and the chip is the
    tab admitting it is not validating what you type.

    Mutation: chip every text box and an `int` with one bound -- which IS
    validated on save by `tuning.check()` -- claims it is free text.
    """
    assert tp.row_chips(_row(type=None)) == (tp.CHIP_FREE_TEXT,)
    assert tp.row_chips(_row(type="int", min=0)) == ()
    assert tp.row_chips(_row(type="bool")) == ()


def test_a_changed_row_is_chipped_with_the_job_that_change_owes() -> None:
    """Item 9's first chip, and its word comes from `tuning.apply_rule()`.

    Computed and never typed: the card above already prices the same change
    through the same function, and a chip spelled by hand would be a second
    place for `recreate` to be called a restart.

    Mutation: hard-code "Restart pending" and a key in a conf OUTSIDE every
    bind -- which needs the containers replaced -- promises the fast restart.
    """
    bound = _row(file=CONF, type="bool")
    unbound = _row(file="modules/mod-x/conf/mod-x.conf", type="bool")

    assert tp.row_chips(bound, changed=True) == (tp.PENDING_CHIPS["restart"],)
    assert tp.row_chips(unbound, changed=True) == (tp.PENDING_CHIPS["recreate"],)
    assert tuning.apply_rule(unbound) == "recreate"


def test_an_int_with_both_bounds_shows_them_beside_the_control() -> None:
    """Item 11. The bounds are already what earn the row a spinner (`control_kind`).

    Mutation: show them for a one-bound int too and the row claims a limit the
    catalog never stated -- which is the invention `control_kind()` refuses a
    spinner over in the first place.
    """
    assert tp.bounds_note(_row(type="int", min=0, max=80)) == "0–80"
    assert tp.bounds_note(_row(type="int", min=0)) is None
    assert tp.bounds_note(_row(type="int")) is None
    assert tp.bounds_note(_row(type="bool")) is None


def test_a_card_hint_comes_from_the_backends_its_rows_really_use() -> None:
    """Item 12's per-card sentence, derived and not typed.

    A conf card is read at world start; a card with a `.lua` row in it is
    patched into a script that was deployed into the server, which is why
    those rows are read-only in this version.

    Mutation: key the hint off the module's FAMILY and a `module` whose only
    tuning row is a deployed Lua script claims its settings are read at world
    start.
    """
    conf_card = tp.build_tuning_cards((_row(),))[0]
    lua_card = tp.build_tuning_cards((_row(file=LUA),))[0]

    assert tp.card_hint(conf_card) == tp.HINT_CONF
    assert tp.card_hint(lua_card) == tp.HINT_LUA


def test_a_card_says_how_many_settings_it_carries(qapp: object) -> None:
    """Item 12's count, beside the module's name.

    Mutation: count the FILES instead and Beastmaster's five settings across
    two files read as `Settings 2`.
    """
    card = tp.build_tuning_cards((_row(key="A"), _row(key="B"), _row(key="C")))[0]
    widget = tp.CardWidget(card)

    assert widget.count_label.text() == "Settings 3"


def test_a_changed_row_names_its_key_and_the_value_it_had(qapp: object) -> None:
    """Item 10. `changed from 10` does not say WHICH key, on a card of twelve.

    Mutation: drop the key from the sentence and the line reads `· was 10`
    with nothing to attach it to.
    """
    editor = tp.RowEditor(
        _row(key="beastmaster.min_level", type="int", min=0, max=80, current="10")
    )
    assert isinstance(editor.control, QSpinBox)

    editor.control.setValue(20)

    assert editor.changed_label.text() == "beastmaster.min_level · was 10"


def test_a_changed_row_gets_a_rail_down_its_left_edge(qapp: object) -> None:
    """Item 10's other half: the change is findable by scrolling, not by reading.

    Mutation: set the rail once in `__init__` and every row on the card wears
    it, changed or not.
    """
    editor = tp.RowEditor(_row(type="bool", current="0"))
    assert "border-left" not in editor.styleSheet()

    assert isinstance(editor.control, QCheckBox)
    editor.control.setChecked(True)

    assert "border-left" in editor.styleSheet()


def test_the_lint_verdict_is_shown_when_the_file_is_clean(qapp: object) -> None:
    """Item 14: `lint()` already answers; T43 drew only the half that complains.

    A guard that is silent when it passes is a guard a user cannot tell from a
    guard that is not running.

    Mutation: keep `said or ""` and a clean file shows nothing, which is what
    an unlinted file also shows.
    """
    panel = tp.TuningPanel()
    panel.set_file_text("A = 1\n# a comment\n", read_only=False, note=None)

    assert panel.lint_label.text() == tp.LINT_OK

    panel.editor.setPlainText("A = 1\nnonsense\n")

    assert panel.lint_label.text() != tp.LINT_OK
    assert "nonsense" in panel.lint_label.text()


def test_a_read_only_file_gets_no_verdict_either_way(qapp: object) -> None:
    """Nothing can be typed into it, so there is nothing to say about what was.

    Mutation: lint read-only files too and `worldserver.conf` -- 700 lines this
    app will not write -- gets a verdict about somebody else's file.
    """
    panel = tp.TuningPanel()
    panel.set_file_text("nonsense\n", read_only=True, note=None)

    assert panel.lint_label.text() == ""


# -- the picker, the backup and the recreate warning (T44 items 13, 15, 16) --


def test_the_picker_is_buttons_and_a_read_only_file_says_so_on_its_own(qapp: object) -> None:
    """Item 13. A combo box hides every other file behind a press; buttons do not.

    The label is the BASENAME, because the paths are 40 characters of
    `env/dist/etc/modules/` that every row shares, and the full path is the
    tooltip.

    Mutation: label every button with the full path and the row of them is
    wider than the window at any size the mockup draws.
    """
    core = "env/dist/etc/worldserver.conf"
    panel = tp.TuningPanel()
    panel.set_files((CONF, core), read_only=(core,))

    labels = [b.text() for b in panel.file_buttons()]
    assert labels == ["mod_npc_beastmaster.conf", "worldserver.conf · read-only"]
    assert panel.file_buttons()[0].toolTip() == CONF


def test_two_files_with_the_same_basename_are_told_apart_by_their_paths(qapp: object) -> None:
    """A basename is only a label while it is unique, and nothing makes it unique.

    Mutation: always use the basename and two modules that both ship `mod.conf`
    give the user two identical buttons.
    """
    a = "env/dist/etc/modules/mod.conf"
    b = "env/dist/etc/other/mod.conf"
    panel = tp.TuningPanel()
    panel.set_files((a, b), read_only=())

    assert [button.text() for button in panel.file_buttons()] == [a, b]


def test_pressing_a_file_button_selects_it_and_says_which(qapp: object) -> None:
    """Mutation: emit nothing and the editor never changes file again."""
    core = "env/dist/etc/worldserver.conf"
    panel = tp.TuningPanel()
    picked: list[str] = []
    panel.file_selected.connect(picked.append)
    panel.set_files((CONF, core), read_only=(core,))
    picked.clear()

    panel.file_buttons()[1].click()

    assert picked == [core]
    assert panel.current_file() == core


def test_the_editable_file_carries_the_recreate_warning_and_a_read_only_one_does_not(
    qapp: object,
) -> None:
    """Item 16, and it is a safety message rather than decoration.

    This install's compose sets `AC_*` environment keys on the worldserver
    (`catalog/composegen.py`'s `DEFAULT_WORLD_ENV` plus `catalog.json`'s
    `world_env`), and an `AC_*` key SHADOWS the matching line in the conf. A
    container keeps the environment it was created with, so a raw rewrite that
    changed a shadowed key looks as though it did nothing until the containers
    are RECREATED -- which is the promise `ModuleFiles.svelte:124-128` on
    `rust-main` says cannot be kept.

    Mutation: show it for read-only files too and `worldserver.conf`, which
    this tab will not write at all, warns about a rewrite nobody can make.
    """
    panel = tp.TuningPanel()
    panel.set_file_text("A = 1\n", read_only=False, note=None)
    assert panel.recreate_warning.isVisibleTo(panel)
    assert "AC_" in panel.recreate_warning.text()
    assert "RECREATED" in panel.recreate_warning.text()

    panel.set_file_text("A = 1\n", read_only=True, note=None)
    assert not panel.recreate_warning.isVisibleTo(panel)


def test_the_backup_name_is_shown_after_a_save_and_cleared_on_the_next_file(
    qapp: object,
) -> None:
    """Item 15. `tuning.write()` takes one every time; the tab never said its name.

    Cleared when the file changes, because a backup of the file you were
    looking at a moment ago is not a backup of this one.

    Mutation: leave it set across `set_file_text()` and the name of one file's
    backup sits under another file's text.
    """
    panel = tp.TuningPanel()
    panel.set_file_text("A = 1\n", read_only=False, note=None)
    assert panel.backup_label.text() == ""

    panel.set_backup("mod.conf.2026-09-13T01-02-03.bak")
    assert "mod.conf.2026-09-13T01-02-03.bak" in panel.backup_label.text()

    panel.set_file_text("B = 2\n", read_only=False, note=None)
    assert panel.backup_label.text() == ""


def test_the_raw_editor_offers_a_revert_beside_save_file(qapp: object) -> None:
    """Item 15's other half, and it is dead until a save has made a backup.

    Mutation: enable it always and the first press on a file nobody has saved
    is a press with nothing to restore from.
    """
    panel = tp.TuningPanel()
    pressed: list[bool] = []
    panel.file_revert_pressed.connect(lambda: pressed.append(True))
    panel.set_file_text("A = 1\n", read_only=False, note=None)

    assert panel.file_revert_button.isEnabled() is False

    panel.set_backup("mod.conf.2026-09-13T01-02-03.bak")
    assert panel.file_revert_button.isEnabled() is True
    panel.file_revert_button.click()

    assert pressed == [True]
