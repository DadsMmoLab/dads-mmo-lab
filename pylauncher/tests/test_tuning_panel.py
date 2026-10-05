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


def test_a_narrow_window_shows_the_switch_and_never_stacks_the_two_halves(qapp: object) -> None:
    """T190: stacked, the cards got a 72px strip at 960x640; now a switch picks one half.

    The split stays side by side at every width -- a vertical split's sizes
    were what came back as a 20/80 column of widths in a wide window.
    """
    from PySide6.QtCore import Qt

    panel = tp.TuningPanel()
    # Shown, because a hidden widget is never sent a resize event: the same
    # test written against `resize()` alone passed with the whole rule deleted.
    panel.show()
    panel.resize(1200, 600)
    assert panel.split.orientation() == Qt.Orientation.Horizontal
    assert not panel.side_buttons.isVisible()
    panel.resize(500, 600)
    assert panel.split.orientation() == Qt.Orientation.Horizontal
    assert panel.side_buttons.isVisible()
    panel.resize(1200, 600)
    assert not panel.side_buttons.isVisible(), "the switch outlived the narrow window"
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
    card = tp.build_tuning_cards((_row(key="A"), _row(file="conf/elsewhere.conf", key="B")))[0]
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
    """Item 12's count, beside the module's name -- which is now the card's title (T190 I9).

    Mutation: count the FILES instead and Beastmaster's five settings across
    two files read as `2 settings`.
    """
    card = tp.build_tuning_cards((_row(key="A"), _row(key="B"), _row(key="C")))[0]
    widget = tp.CardWidget(card)

    assert widget.title_label.text() == "NPC Beastmaster"
    assert widget.count_label.text() == "3 settings"
    assert widget.title() == "", "the name is drawn once, as the heading, not in the frame too"
    assert widget.accessibleName() == "NPC Beastmaster"
    one = tp.CardWidget(tp.build_tuning_cards((_row(key="A"),))[0])
    assert one.count_label.text() == "1 setting"


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


def test_the_shadow_warning_is_shown_only_where_a_key_is_really_shadowed(
    qapp: object,
) -> None:
    """Round 2's finding 3. Round 1 showed this on EVERY editable conf.

    A false "you must recreate" is cheap noise; a missing one is a user editing
    a value that silently never applies, which is the T41/T43 defect class
    again. So it is driven by the pairs the view computes
    (`composegen.shadowed_by_env`) and not by "is this file writable".

    Mutation: `shadow-warning-on-every-editable-file` -- show it on
    `not read_only` again and a module conf with no env row behind it warns
    about a recreate that has nothing to do with it.
    """
    panel = tp.TuningPanel()

    panel.set_file_text("A = 1\n", read_only=False, note=None, shadowed=())
    assert not panel.shadow_warning.isVisibleTo(panel)

    panel.set_file_text(
        "AiPlayerbot.MinRandomBots = 500\n",
        read_only=False,
        note=None,
        shadowed=(("AiPlayerbot.MinRandomBots", "AC_AI_PLAYERBOT_MIN_RANDOM_BOTS"),),
    )
    assert panel.shadow_warning.isVisibleTo(panel)


def test_the_shadow_warning_names_the_keys_and_the_remedy_that_works(qapp: object) -> None:
    """The remedy round 1 gave was wrong, and the review is right about why.

    A recreate re-reads `docker-compose.override.yml`, which is regenerated
    from the same data, so it re-applies the SAME `AC_*` value and the raw edit
    still does nothing. The env row has to change or go first.

    Mutation: `shadow-warning-says-recreate-is-enough` -- put the round-1
    sentence back and the tab tells a user to take their server down for a
    remedy that cannot work.
    """
    panel = tp.TuningPanel()

    panel.set_file_text(
        "AiPlayerbot.MinRandomBots = 500\n",
        read_only=False,
        note=None,
        shadowed=(("AiPlayerbot.MinRandomBots", "AC_AI_PLAYERBOT_MIN_RANDOM_BOTS"),),
    )

    said = panel.shadow_warning.text()
    assert "AiPlayerbot.MinRandomBots" in said, "it has to name the key"
    assert "AC_AI_PLAYERBOT_MIN_RANDOM_BOTS" in said, "and the row that beats it"
    assert tp.COMPOSE_OVERRIDE in said, "and where that row lives"
    assert "recreating the containers is not enough" in said


def test_a_read_only_file_gets_no_shadow_warning_whatever_it_carries(qapp: object) -> None:
    """Nothing can be typed into it, so no edit of it can silently fail to apply.

    Mutation: drop the `read_only` arm and `worldserver.conf` -- 700 lines this
    app will not write, several of them shadowed -- warns about an edit nobody
    can make.
    """
    panel = tp.TuningPanel()

    panel.set_file_text(
        "AiPlayerbot.MinRandomBots = 500\n",
        read_only=True,
        note=None,
        shadowed=(("AiPlayerbot.MinRandomBots", "AC_AI_PLAYERBOT_MIN_RANDOM_BOTS"),),
    )

    assert not panel.shadow_warning.isVisibleTo(panel)


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
    opened: list[str] = []
    panel.file_selected.connect(opened.append)
    panel.set_files(["a/mod.conf", "a/other.conf"])
    panel.set_file_text("A = 1\n", read_only=False, note=None)
    assert panel.backup_label.text() == ""

    panel.set_backup("mod.conf.2026-09-13T01-02-03.bak")
    assert "mod.conf.2026-09-13T01-02-03.bak" in panel.backup_label.text()

    # T190: the same file read again -- what Reload and every save now do --
    # keeps the backup it was saved with, and Revert stays armed.
    panel.set_file_text("A = 1\n", read_only=False, note=None)
    assert "mod.conf.2026-09-13T01-02-03.bak" in panel.backup_label.text()
    assert panel.file_revert_button.isEnabled()

    panel.file_buttons()[1].click()
    assert opened[-1] == "a/other.conf"
    panel.set_file_text("B = 2\n", read_only=False, note=None)
    assert panel.backup_label.text() == ""
    assert not panel.file_revert_button.isEnabled()


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


# -- T190 B3: the row shows its value, and a number box takes numbers --------


def test_the_key_line_is_drawn_only_when_the_label_says_something_else() -> None:
    assert tp.shows_key_line(_row(key="BeastMaster.Enable", label="Enable the NPC")) is True
    assert tp.shows_key_line(_row(key="BeastMaster.Enable", label="BeastMaster.Enable")) is False


def test_a_row_with_no_label_of_its_own_names_its_key_once(qapp: object) -> None:
    """45 of 157 keys have no `label`, so the label IS the key -- and was printed twice."""
    from PySide6.QtWidgets import QLabel

    editor = tp.RowEditor(_row(key="Transmog.Enable", label="Transmog.Enable", current="1"))
    named = [label for label in editor.findChildren(QLabel) if label.text() == "Transmog.Enable"]
    assert named == [editor.label], [label.objectName() or label.text() for label in named]
    assert editor.key_label is None
    assert editor.label.toolTip() == "Transmog.Enable"


def test_a_labelled_row_keeps_its_key_line_under_the_label(qapp: object) -> None:
    editor = tp.RowEditor(_row(key="BeastMaster.Enable", label="Enable the Beastmaster NPC"))
    assert editor.key_label is not None and editor.key_label.text() == "BeastMaster.Enable"
    assert editor.label.toolTip() == "BeastMaster.Enable"


def test_in_a_440px_card_the_value_box_keeps_room_for_its_value(qapp: object) -> None:
    """B3: a long label beside the box took the whole row and left the value 28px.

    Laid out at the narrowest the card's own layout will go, where every widget
    sits at its minimum -- so it is the box's floor that is measured, not
    whatever room a wide card happened to leave -- and that narrowest card
    still fits the 440px column, so the floor was not bought by widening it.
    """
    from tests.conftest import process_events

    card = tp.CardWidget(
        tp.build_tuning_cards(
            (
                _row(
                    # A short path, so the card's monospace file line is not
                    # what sets its narrowest width: the value box's own floor
                    # is what is measured, not the room a long path leaves.
                    file="env/dist/etc/modules/b.conf",
                    key="BeastMaster.Enable",
                    label="Enable the Beastmaster NPC for every class of character on this realm",
                    current="anything at all",
                ),
            )
        )[0]
    )
    from yulon.ui.theme import apply_dadcraft_theme

    # Styled as the app styles a 960px window, whatever an earlier test left on
    # the application: a widget's own sheet is the one that applies.
    apply_dadcraft_theme(card, width=960)
    card.show()
    try:
        process_events()
        narrowest = card.minimumSizeHint().width()
        assert narrowest <= tp.CARDS_MIN_WIDTH, f"the card needs {narrowest}px"
        card.resize(narrowest, 600)
        process_events()
        field = card.editors["BeastMaster.Enable"].control
        assert isinstance(field, QLineEdit)
        need = tp.VALUE_MIN_CHARS * field.fontMetrics().averageCharWidth()
        assert field.width() >= need, f"the value has {field.width()}px, needs {need}"

        # Again after the app restyles it for a wide window, as `_at` does: the
        # font grows, and a floor counted in the old font's characters is short.
        before = field.fontMetrics().averageCharWidth()
        apply_dadcraft_theme(card, width=1920)
        process_events()
        assert field.fontMetrics().averageCharWidth() > before, "control: the font grew"
        card.resize(card.minimumSizeHint().width(), 600)
        process_events()
        need = tp.VALUE_MIN_CHARS * field.fontMetrics().averageCharWidth()
        assert field.width() >= need, f"restyled, the value has {field.width()}px, needs {need}"
    finally:
        card.close()


def test_an_int_with_one_bound_takes_digits_and_a_sign_and_nothing_else(qapp: object) -> None:
    """Typed, the way a player types: letters do not go in, `-5` does. No range is invented."""
    from PySide6.QtTest import QTest

    editor = tp.RowEditor(_row(type="int", min=0, current="10"))
    field = editor.control
    assert isinstance(field, QLineEdit)
    field.clear()
    QTest.keyClicks(field, "a5b")
    assert field.text() == "5"
    field.clear()
    QTest.keyClicks(field, "-5")
    assert field.text() == "-5", "a bound is tuning.check()'s to apply at Save, not the box's"
    assert editor.changed


def test_a_key_with_no_type_takes_anything_and_keeps_its_free_text_chip(qapp: object) -> None:
    from PySide6.QtTest import QTest

    editor = tp.RowEditor(_row(type=None, current=""))
    field = editor.control
    assert isinstance(field, QLineEdit)
    assert field.validator() is None
    QTest.keyClicks(field, "abc")
    assert field.text() == "abc"
    assert tp.CHIP_FREE_TEXT in editor.chips.text()


def test_a_file_value_the_box_would_refuse_is_shown_as_written(qapp: object) -> None:
    """Never clamped, never blanked: the file said `abc`, so the box says `abc`."""
    editor = tp.RowEditor(_row(type="int", min=0, current="abc"))
    assert isinstance(editor.control, QLineEdit)
    assert editor.control.text() == "abc"
    assert editor.value() == "abc" and not editor.changed


# -- T190 A28/I9/B15: card titles, the restart note once, one Reload ----------


def _restart_card(module_id: str) -> tuple[tuning.TuningRow, ...]:
    return (_row(module_id=module_id, module_name=f"Module {module_id}", key=f"{module_id}.A"),)


RECREATE_FILE = "conf/mod_outside_every_bind.conf"
"""A conf no compose bind reaches, so a change to it owes a recreate, not a restart."""


def test_a_card_heading_is_the_module_name_and_its_count() -> None:
    three = tp.build_tuning_cards((_row(key="A"), _row(key="B"), _row(key="C")))[0]
    one = tp.build_tuning_cards((_row(key="A"),))[0]
    assert tp.card_heading(three) == ("NPC Beastmaster", "3 settings")
    assert tp.card_heading(one) == ("NPC Beastmaster", "1 setting")


def test_the_restart_rule_is_lifted_only_when_a_card_owes_exactly_a_restart() -> None:
    restart = tp.build_tuning_cards(_restart_card("a"))
    recreate = tp.build_tuning_cards((_row(file=RECREATE_FILE),))
    assert tuning.file_rule(RECREATE_FILE) == "recreate", "control: the file owes a recreate"
    assert tp.lifted_rule(restart + recreate) == ("restart",)
    assert tp.lifted_rule(recreate) is None


def test_the_restart_sentence_is_said_once_and_a_dearer_one_stays_on_its_card(
    qapp: object,
) -> None:
    """B15: three conf cards said the same restart sentence three times, under three hints.

    Set twice: the second set has no restart card, so a note that was only
    ever put up, never taken down, fails.
    """
    from PySide6.QtWidgets import QLabel

    restart = tuning.apply_sentence("restart")
    rows = _restart_card("a") + _restart_card("b") + _restart_card("c")
    rows += (_row(module_id="d", module_name="Module d", key="d.A", file=RECREATE_FILE),)
    panel = tp.TuningPanel()
    panel.set_cards(tp.build_tuning_cards(rows))
    panel.show()
    try:
        said = [w for w in panel.findChildren(QLabel) if w.isVisible() and restart in w.text()]
        assert said == [panel.rule_note], [w.text()[:40] for w in said]
        recreate = panel.card("d")
        assert recreate.rule_label.isVisible()
        assert tuning.apply_sentence("recreate") in recreate.rule_label.text()
        for key in ("a", "b", "c"):
            assert not panel.card(key).hint_label.isVisible(), f"card {key} still says its hint"
            assert not panel.card(key).rule_label.isVisible()

        panel.set_cards(tp.build_tuning_cards(rows[3:]))
        assert not panel.rule_note.isVisible(), "the note outlived the last restart card"
    finally:
        panel.close()


def test_the_panel_has_no_reload_of_its_own(qapp: object) -> None:
    """A28: the tab's bar has the one Reload; the editor's second one is gone."""
    from PySide6.QtWidgets import QPushButton

    panel = tp.TuningPanel()
    panel.resize(1200, 600)
    panel.show()
    try:
        panel.set_files(["a/mod.conf"])
        shown = [b.text() for b in panel.findChildren(QPushButton) if b.isVisible()]
        assert "Reload from disk" not in shown, shown
        assert "Save file" in shown and "Revert file" in shown, shown
    finally:
        panel.close()


def test_the_same_files_again_reads_the_open_file_again_and_keeps_typing_in_it(
    qapp: object,
) -> None:
    """The tab's Reload, and every save, hand the same list back: the open file is read again.

    Before, the editor kept its old text, so after a card's save a later Save
    file wrote the old value back. When the player has typed into it, the text
    read is not put over their typing: somebody else's reload keeps it.
    """
    from PySide6.QtTest import QTest

    panel = tp.TuningPanel()
    opened: list[str] = []
    panel.file_selected.connect(opened.append)
    panel.set_files(["a/mod.conf", "a/other.conf"])
    panel.file_buttons()[1].click()
    panel.set_file_text("B = 1\n", read_only=False, note=None)
    opened.clear()

    panel.set_files(["a/mod.conf", "a/other.conf"])
    assert opened == ["a/other.conf"]
    panel.set_file_text("B = 1\n", read_only=False, note=None)

    QTest.keyClicks(panel.editor, "# mine")
    opened.clear()
    panel.set_files(["a/mod.conf", "a/other.conf"])
    # Read again even then, so a file changed on disk is noticed -- but the
    # text that comes back does not replace the typing.
    assert opened == ["a/other.conf"]
    panel.set_file_text("B = 1\n", read_only=False, note=None)
    assert "# mine" in panel.editor.toPlainText(), "a reload threw the typing away"
    assert panel.current_file() == "a/other.conf"


def test_the_editor_names_the_file_it_shows_and_says_when_it_is_the_servers_own(
    qapp: object,
) -> None:
    panel = tp.TuningPanel()
    core = "env/dist/etc/worldserver.conf"
    mine = "env/dist/etc/modules/mod_npc_beastmaster.conf"
    panel.set_files([mine, core], read_only=[core])
    panel.set_file_text("BeastMaster.Enable = 1\n", read_only=False, note=None)
    title = panel.file_title.text()
    assert "mod_npc_beastmaster.conf" in title and mine in title
    assert tp.FILE_READ_ONLY not in title

    panel.file_buttons()[1].click()
    panel.set_file_text("# conf\n", read_only=True, note=None)
    title = panel.file_title.text()
    assert "worldserver.conf" in title and core in title
    assert tp.FILE_READ_ONLY in title


# -- T190 final round: the panel never loses or quietly overrides typing -------


class _Answers:
    """The panel's question (`TuningPanel.choose`), answered as the test says, each one kept."""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.asked: list[tuple[str, ...]] = []

    def __call__(self, title: str, question: str, choices: tuple[str, ...]) -> str:
        self.asked.append(tuple(choices))
        return self.answer


def _typed_into_mod_conf(qapp: object) -> tuple[tp.TuningPanel, list[str], list[str]]:
    """A shown panel with two files, `a/mod.conf` open and `# mine` typed into it."""
    from PySide6.QtTest import QTest

    panel = tp.TuningPanel()
    panel.resize(1200, 600)
    panel.show()
    opened: list[str] = []
    saved: list[str] = []
    panel.file_selected.connect(opened.append)
    panel.file_save_pressed.connect(saved.append)
    panel.set_files(["a/mod.conf", "a/other.conf"])
    panel.set_file_text("A = 1\n", read_only=False, note=None)
    panel.editor.moveCursor(panel.editor.textCursor().MoveOperation.End)
    QTest.keyClicks(panel.editor, "# mine")
    opened.clear()
    return panel, opened, saved


UNSAVED_CHOICES = (tp.CHOICE_SAVE, tp.CHOICE_DISCARD, tp.CHOICE_CANCEL)
STALE_CHOICES = (tp.CHOICE_OVERWRITE, tp.CHOICE_RELOAD, tp.CHOICE_CANCEL)


def _click(button: Any) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    QTest.mouseClick(button, Qt.MouseButton.LeftButton)


def test_cancel_on_a_file_button_over_unsaved_typing_leaves_everything_as_it_was(
    qapp: object,
) -> None:
    """A file button threw the editor's typing away without a word (Codex adversarial)."""
    panel, opened, saved = _typed_into_mod_conf(qapp)
    try:
        ask = _Answers(tp.CHOICE_CANCEL)
        panel.choose = ask
        _click(panel.file_buttons()[1])
        assert ask.asked == [UNSAVED_CHOICES]
        assert opened == [] and saved == []
        assert panel.current_file() == "a/mod.conf"
        assert panel.editor.toPlainText() == "A = 1\n# mine"
        first, second = panel.file_buttons()
        assert first.isChecked() and not second.isChecked(), "the click's check was left on"
    finally:
        panel.close()


def test_discard_on_a_file_button_over_unsaved_typing_opens_the_other_file(qapp: object) -> None:
    panel, opened, saved = _typed_into_mod_conf(qapp)
    try:
        panel.choose = _Answers(tp.CHOICE_DISCARD)
        _click(panel.file_buttons()[1])
        assert opened == ["a/other.conf"] and saved == []
        assert panel.current_file() == "a/other.conf"
        panel.set_file_text("B = 2\n", read_only=False, note=None)
        assert panel.editor.toPlainText() == "B = 2\n"
    finally:
        panel.close()


def test_save_on_a_file_button_over_unsaved_typing_saves_then_opens_the_other_file(
    qapp: object,
) -> None:
    """Save hands the typing up, and the switch follows only once the view says it saved."""
    panel, opened, saved = _typed_into_mod_conf(qapp)
    # What the view does with a save that worked: it names the backup it took.
    panel.file_save_pressed.connect(lambda _text: panel.set_backup("mod.conf.1.bak"))
    try:
        panel.choose = _Answers(tp.CHOICE_SAVE)
        _click(panel.file_buttons()[1])
        assert saved == ["A = 1\n# mine"]
        assert opened == ["a/other.conf"]
        assert panel.current_file() == "a/other.conf"
    finally:
        panel.close()


def test_save_on_a_file_button_that_does_not_save_stays_on_the_file(qapp: object) -> None:
    """A save the view refused (a lint No, a disk error) names no backup: nothing switches."""
    panel, opened, saved = _typed_into_mod_conf(qapp)
    try:
        panel.choose = _Answers(tp.CHOICE_SAVE)
        _click(panel.file_buttons()[1])
        assert saved == ["A = 1\n# mine"], "control: Save was pressed through"
        assert opened == []
        assert panel.current_file() == "a/mod.conf"
        assert panel.editor.toPlainText() == "A = 1\n# mine"
    finally:
        panel.close()


def test_a_reload_that_finds_the_file_changed_keeps_the_typing_and_says_so(qapp: object) -> None:
    """The editor kept typing over a card's save and said nothing (cold review)."""
    panel, opened, _saved = _typed_into_mod_conf(qapp)
    try:
        panel.set_files(["a/mod.conf", "a/other.conf"])
        assert opened == ["a/mod.conf"], "a reload reads the open file again"
        panel.set_file_text("A = 1\n", read_only=False, note=None)
        assert panel.editor.toPlainText() == "A = 1\n# mine"
        assert tp.EDITOR_STALE not in panel.file_note.text(), "the file had not changed"

        panel.set_file_text("A = 0\n", read_only=False, note=None)
        assert panel.editor.toPlainText() == "A = 1\n# mine", "the reload threw the typing away"
        assert panel.file_note.isVisible() and tp.EDITOR_STALE in panel.file_note.text()
    finally:
        panel.close()


def _stale(qapp: object) -> tuple[tp.TuningPanel, list[str], list[str]]:
    panel, opened, saved = _typed_into_mod_conf(qapp)
    panel.set_files(["a/mod.conf", "a/other.conf"])
    panel.set_file_text("A = 0\n", read_only=False, note=None)
    opened.clear()
    return panel, opened, saved


def test_save_file_over_a_file_changed_on_disk_asks_and_cancel_writes_nothing(
    qapp: object,
) -> None:
    panel, opened, saved = _stale(qapp)
    try:
        ask = _Answers(tp.CHOICE_CANCEL)
        panel.choose = ask
        _click(panel.file_save_button)
        assert ask.asked == [STALE_CHOICES]
        assert saved == [] and opened == []
        assert panel.editor.toPlainText() == "A = 1\n# mine"
    finally:
        panel.close()


def test_overwrite_saves_the_typing_over_the_changed_file(qapp: object) -> None:
    panel, _opened, saved = _stale(qapp)
    try:
        panel.choose = _Answers(tp.CHOICE_OVERWRITE)
        _click(panel.file_save_button)
        assert saved == ["A = 1\n# mine"]
    finally:
        panel.close()


def test_reload_instead_reads_the_file_again_and_saves_nothing(qapp: object) -> None:
    panel, opened, saved = _stale(qapp)
    try:
        panel.choose = _Answers(tp.CHOICE_RELOAD)
        _click(panel.file_save_button)
        assert saved == []
        assert opened == ["a/mod.conf"]
        panel.set_file_text("A = 0\n", read_only=False, note=None)
        assert panel.editor.toPlainText() == "A = 0\n"
        assert tp.EDITOR_STALE not in panel.file_note.text()
    finally:
        panel.close()


def test_a_cards_revert_shows_what_the_file_says_and_other_cards_keep_their_typing(
    qapp: object,
) -> None:
    """A card's Revert is the one press that drops that card's typing; the rest is kept."""

    def rows(a: str) -> tuple[tuning.TuningRow, ...]:
        return (
            _row(module_id="a", module_name="A", key="a.K", current=a),
            _row(module_id="b", module_name="B", key="b.K", current="1"),
        )

    panel = tp.TuningPanel()
    panel.resize(1200, 600)
    panel.show()
    try:
        panel.set_cards(tp.build_tuning_cards(rows("1")))
        for key, typed in (("a", "5"), ("b", "7")):
            field = panel.card(key).editors[f"{key}.K"].control
            assert isinstance(field, QLineEdit)
            field.setText(typed)
        # What the view does on a Revert: the file goes back, the cards are drawn again.
        panel.revert_pressed.connect(
            lambda _f, _m: panel.set_cards(tp.build_tuning_cards(rows("2")))
        )
        button = panel.card("a").revert_button
        assert button is not None
        _click(button)
        reverted = panel.card("a").editors["a.K"].control
        assert isinstance(reverted, QLineEdit) and reverted.text() == "2"
        assert panel.edits("a") == {}
        assert panel.edits("b") == {"b.K": "7"}
    finally:
        panel.close()


def test_the_editors_note_shows_when_the_note_over_the_cards_is_scrolled_away(
    qapp: object,
) -> None:
    """The restart sentence was hidden in the editor while its twin was scrolled out of view."""
    from tests.conftest import process_events

    restart = tuning.apply_sentence("restart")
    rows: tuple[tuning.TuningRow, ...] = ()
    for module in "abcdefghijkl":
        rows += _restart_card(module)
    panel = tp.TuningPanel()
    panel.resize(1200, 400)
    panel.show()
    try:
        panel.set_cards(tp.build_tuning_cards(rows))
        panel.set_files([CONF])
        panel.set_file_text("BeastMaster.Enable = 1\n", read_only=False, note=restart)
        process_events()
        assert not panel.file_note.isVisible(), "control: the note over the cards is on screen"
        bar = panel._area.verticalScrollBar()
        assert bar.maximum() > 0, "control: the cards scroll"
        bar.setValue(bar.maximum())
        process_events()
        assert panel.rule_note.visibleRegion().isEmpty(), "control: scrolled out of view"
        assert panel.file_note.isVisible() and restart in panel.file_note.text()
        bar.setValue(0)
        process_events()
        assert not panel.file_note.isVisible(), "said twice again once scrolled back"
    finally:
        panel.close()


def test_an_int_box_holding_a_value_it_would_refuse_can_still_be_edited(qapp: object) -> None:
    """`1.5` in the file: Backspace did nothing, because every shorter text was refused too."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    editor = tp.RowEditor(_row(type="int", min=0, current="1.5"))
    field = editor.control
    assert isinstance(field, QLineEdit)
    assert editor.value() == "1.5" and not editor.changed, "control: shown as written"
    QTest.keyClick(field, Qt.Key.Key_End)
    QTest.keyClick(field, Qt.Key.Key_Backspace)
    assert field.text() == "1."
    QTest.keyClick(field, Qt.Key.Key_Backspace)
    assert field.text() == "1"
    QTest.keyClicks(field, "x5")
    assert field.text() == "15", "once the text is a number again, letters stay out"


def test_an_int_box_whose_file_holds_other_digits_drops_the_rule_until_they_are_gone(
    qapp: object,
) -> None:
    """`_guard_int` asks the same rule in Python: the rule must not call `١٢` a fit."""
    editor = tp.RowEditor(_row(type="int", min=0, current="١٢"))
    field = editor.control
    assert isinstance(field, QLineEdit)
    assert field.validator() is None, "the rule held a text it refuses, and froze the box"


def test_reading_the_same_file_again_keeps_the_editors_scroll_and_cursor(qapp: object) -> None:
    """Every save and Reload re-reads the open file; it jumped back to line one each time."""
    from tests.conftest import process_events

    text = "".join(f"Key{i} = {i}\n" for i in range(300))
    panel = tp.TuningPanel()
    panel.resize(1200, 400)
    panel.show()
    try:
        panel.set_files(["a/mod.conf"])
        panel.set_file_text(text, read_only=False, note=None)
        process_events()
        cursor = panel.editor.textCursor()
        cursor.setPosition(text.index("Key150"))
        panel.editor.setTextCursor(cursor)
        bar = panel.editor.verticalScrollBar()
        assert bar.maximum() > 120, "control: the file scrolls"
        bar.setValue(120)
        panel.set_files(["a/mod.conf"])
        panel.set_file_text(text.replace("Key5 = 5", "Key5 = 6"), read_only=False, note=None)
        process_events()
        assert bar.value() == 120
        assert panel.editor.textCursor().position() == text.index("Key150")
    finally:
        panel.close()


def test_revert_file_replaces_the_typing_with_the_file_it_put_back(qapp: object) -> None:
    """The one re-read that is meant to drop the typing: the player asked for the backup."""
    from PySide6.QtTest import QTest

    panel, _opened, _saved = _typed_into_mod_conf(qapp)
    try:
        panel.set_backup("mod.conf.1.bak")
        panel.editor.moveCursor(panel.editor.textCursor().MoveOperation.End)
        QTest.keyClicks(panel.editor, " again")
        # What the view does on Revert file: the backup is copied back and read in.
        panel.file_revert_pressed.connect(
            lambda: panel.set_file_text("A = 0\n", read_only=False, note=None)
        )
        _click(panel.file_revert_button)
        assert panel.editor.toPlainText() == "A = 0\n"
        assert tp.EDITOR_STALE not in panel.file_note.text()
    finally:
        panel.close()


def test_the_question_box_makes_cancel_the_default_and_the_escape_answer(
    qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Return or Escape on the question must never overwrite or discard anything."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QMessageBox

    seen: dict[str, str] = {}

    def escape(box: QMessageBox) -> int:
        default, escape_button = box.defaultButton(), box.escapeButton()
        seen["default"] = default.text() if default is not None else ""
        seen["escape"] = escape_button.text() if escape_button is not None else ""
        box.show()
        QTest.keyClick(box, Qt.Key.Key_Escape)
        return 0

    monkeypatch.setattr(QMessageBox, "exec", escape)
    panel = tp.TuningPanel()
    try:
        answer = panel._ask("t", "q", STALE_CHOICES)
        assert seen == {"default": tp.CHOICE_CANCEL, "escape": tp.CHOICE_CANCEL}
        assert answer == tp.CHOICE_CANCEL
    finally:
        panel.close()


def test_a_reload_that_cannot_read_the_file_blanks_only_a_clean_editor(qapp: object) -> None:
    """The view hands a failed read as empty, read-only text; with no typing that is shown."""
    panel = tp.TuningPanel()
    panel.set_files(["a/mod.conf"])
    panel.set_file_text("A = 1\n", read_only=False, note=None)
    panel.set_files(["a/mod.conf"])
    panel.set_file_text("", read_only=True, note="a/mod.conf: gone")
    assert panel.editor.toPlainText() == "" and panel.editor.isReadOnly()
    assert tp.EDITOR_UNREAD not in panel.file_note.text()
