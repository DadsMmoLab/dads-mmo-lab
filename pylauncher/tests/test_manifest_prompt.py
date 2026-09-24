"""Tests for the manifest question dialog (`yulon.ui.widgets.manifest_prompt`).

The dialog is the half of Lane A a user actually sees, and the reason it is a
widget with a `problem()` rather than a `QDialog.exec()` and nothing else is
that the refusal is the interesting part: the two AH bot modules ask for a
character GUID, and "0", "" and "Ahbot" are all things a person types into that
box. What must never happen is what happened before 2026-09-07 — the answer
travelling as far as the applier, which cloned the module and only THEN
discovered it had nothing to write.
"""

from __future__ import annotations

import pytest

from yulon.controller_wow_wotlk import modules as wotlk_modules
from yulon.manifest import parse_manifest
from yulon.ui.widgets.manifest_prompt import ManifestPromptDialog


def _ahbot() -> object:
    return wotlk_modules.store().load("module", "mod-ah-bot")


def test_the_dialog_shows_the_manifest_question_the_player_can_act_on(qapp: object) -> None:
    manifest = _ahbot()
    dialog = ManifestPromptDialog(None, manifest, manifest.prompts)  # type: ignore[attr-defined]
    labels = dialog.questions()
    assert any("create the AHBOT account" in text for text in labels), labels
    assert "mod-ah-bot" in dialog.windowTitle() or "Auction House Bot" in dialog.windowTitle()


def test_a_number_box_refuses_letters_and_emptiness_before_anything_is_applied(
    qapp: object,
) -> None:
    manifest = _ahbot()
    dialog = ManifestPromptDialog(None, manifest, manifest.prompts)  # type: ignore[attr-defined]

    assert dialog.problem() != "", "an unfilled dialog must not be acceptable"
    dialog.set_answer("bot_guid", "Ahbot")
    dialog.set_answer("bot_account", "7")
    assert "whole number" in dialog.problem()
    assert "GUID of the AH bot character" in dialog.problem(), dialog.problem()

    dialog.set_answer("bot_guid", "42")
    assert dialog.problem() == ""
    assert dialog.answers() == {"bot_guid": "42", "bot_account": "7"}


@pytest.mark.parametrize(
    ("kind", "extra", "bad", "good"),
    [
        ("int", {}, "1.5", "12"),
        ("float", {}, "wide", "1.5"),
        ("bool", {}, "maybe", "1"),
        ("choice", {"choices": ["red", "blue"]}, "", "blue"),
        ("string", {}, "   ", "Ahbot"),
    ],
)
def test_every_prompt_kind_gets_a_control_that_answers_in_its_own_kind(
    qapp: object, kind: str, extra: dict[str, object], bad: str, good: str
) -> None:
    """The schema's five kinds, all of them, because a kind with no control
    would silently become a text box that accepts anything."""
    manifest = parse_manifest(
        {
            "id": "kindly",
            "name": "Kindly",
            "type": "mod",
            "game": "wow-wotlk",
            "prompts": [{"key": "k", "question": "a question", "kind": kind, **extra}],
        }
    )
    dialog = ManifestPromptDialog(None, manifest, manifest.prompts)
    dialog.set_answer("k", bad)
    assert dialog.problem() != "", f"{kind} accepted {bad!r}"
    dialog.set_answer("k", good)
    assert dialog.problem() == "", f"{kind} refused {good!r}: {dialog.problem()}"
    assert dialog.answers() == {"k": good}


def test_a_prompt_with_a_default_starts_filled_in(qapp: object) -> None:
    manifest = parse_manifest(
        {
            "id": "kindly",
            "name": "Kindly",
            "type": "mod",
            "game": "wow-wotlk",
            "prompts": [{"key": "seconds", "question": "seconds", "kind": "int", "default": "20"}],
        }
    )
    dialog = ManifestPromptDialog(None, manifest, manifest.prompts)
    assert dialog.answers() == {"seconds": "20"}
    assert dialog.problem() == ""


def _hearthstone() -> object:
    return wotlk_modules.store().load("mod", "hearthstone-cd")


def test_running_a_choice_again_says_the_answer_shown_is_the_one_applied(qapp: object) -> None:
    """T100 review: an Update or a second Install re-asks with the DEFAULT selected.

    Nothing remembers the earlier answer (that is T104's question for the
    owner), so a player who picked 5 minutes, pressed Update and clicked OK got
    30 minutes back without a word. Until answers are remembered, the dialog
    says so: what is selected is what gets applied, now.

    Mutation: drop the note and `notes()` carries no "applies the answer".
    """
    manifest = _hearthstone()
    first = ManifestPromptDialog(None, manifest, manifest.prompts)  # type: ignore[attr-defined]
    again = ManifestPromptDialog(
        None, manifest, manifest.prompts, again=True  # type: ignore[attr-defined]
    )
    assert "applies the answer" not in first.notes()
    assert "applies the answer" in again.notes()
    assert "does not remember" in again.notes()
    assert again.answers() == {"cooldown": "30_Min"}, "still the default: the note is why"


def test_running_a_non_choice_again_gets_no_choice_note(qapp: object) -> None:
    """The note is about picking between options, so a number prompt is not given it."""
    manifest = parse_manifest(
        {
            "id": "kindly",
            "name": "Kindly",
            "type": "mod",
            "game": "wow-wotlk",
            "prompts": [{"key": "seconds", "question": "seconds", "kind": "int", "default": "20"}],
        }
    )
    dialog = ManifestPromptDialog(None, manifest, manifest.prompts, again=True)
    assert "applies the answer" not in dialog.notes()
