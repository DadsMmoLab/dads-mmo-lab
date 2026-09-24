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
from yulon.ui.widgets.manifest_prompt import (
    COMPOUNDS_NOTE,
    REMEMBERED_NOTE,
    REMOVE_NO_RECORD_NOTE,
    ManifestPromptDialog,
)


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


def test_a_first_install_is_told_nothing_about_earlier_answers(qapp: object) -> None:
    manifest = _hearthstone()
    first = ManifestPromptDialog(None, manifest, manifest.prompts)  # type: ignore[attr-defined]
    assert REMEMBERED_NOTE not in first.notes()
    assert "no record" not in first.notes()
    assert first.answers() == {"cooldown": "30_Min"}


def test_the_answer_remembered_for_this_install_is_filled_in_over_the_default(
    qapp: object,
) -> None:
    """T104, the T100 cold-review repro: 5 minutes installed, Update pre-filled 30.

    The dialog now opens on the answer this install remembers, so an Update
    clicked straight through keeps what the player picked.

    Mutation: ignore `remembered` in the dialog and the answer is `30_Min`.
    """
    manifest = _hearthstone()
    dialog = ManifestPromptDialog(
        None,
        manifest,  # type: ignore[arg-type]
        manifest.prompts,  # type: ignore[attr-defined]
        again=True,
        remembered={"cooldown": "5_Min"},
    )
    assert dialog.answers() == {"cooldown": "5_Min"}
    assert REMEMBERED_NOTE in dialog.notes()
    assert "does not remember" not in dialog.notes(), "T100's warning is no longer true"


def test_a_remembered_number_is_filled_in_too(qapp: object) -> None:
    """Every kind is remembered, not only a choice: a text box shows the saved number."""
    manifest = parse_manifest(
        {
            "id": "kindly",
            "name": "Kindly",
            "type": "mod",
            "game": "wow-wotlk",
            "prompts": [{"key": "seconds", "question": "seconds", "kind": "int", "default": "20"}],
        }
    )
    dialog = ManifestPromptDialog(
        None, manifest, manifest.prompts, again=True, remembered={"seconds": "35"}
    )
    assert dialog.answers() == {"seconds": "35"}
    assert dialog.problem() == ""


def test_a_remembered_answer_the_question_no_longer_accepts_is_not_filled_in(
    qapp: object,
) -> None:
    """A saved option the manifest has since dropped falls back to the default, and says so."""
    manifest = _hearthstone()
    dialog = ManifestPromptDialog(
        None,
        manifest,  # type: ignore[arg-type]
        manifest.prompts,  # type: ignore[attr-defined]
        again=True,
        remembered={"cooldown": "2_Min"},
    )
    assert dialog.answers() == {"cooldown": "30_Min"}
    assert "no record" in dialog.notes()


def test_asked_again_with_nothing_remembered_says_the_defaults_are_shown(qapp: object) -> None:
    """An install made before T104 has no record, so its Update shows the defaults -- and says so.

    That much of T100's warning is still true for those installs, and only for
    them; the question it names is the one whose default is showing.
    """
    manifest = _hearthstone()
    dialog = ManifestPromptDialog(
        None, manifest, manifest.prompts, again=True  # type: ignore[attr-defined]
    )
    assert dialog.answers() == {"cooldown": "30_Min"}
    assert "no record" in dialog.notes()
    assert "as the new setting" in dialog.notes()
    assert "Hearthstone" in dialog.notes() or "cooldown" in dialog.notes().lower()
    assert REMEMBERED_NOTE not in dialog.notes()


def _baby_mobs() -> object:
    return wotlk_modules.store().load("mod", "baby-mobs")


def test_a_remove_with_no_record_asks_the_multiplier_and_says_why(qapp: object) -> None:
    """Fix wave: Remove never divides by a default in silence.

    Mutation: drop the remove note and `notes()` lacks it.
    """
    manifest = _baby_mobs()
    dialog = ManifestPromptDialog(
        None, manifest, manifest.prompts, again=True, removing=True  # type: ignore[attr-defined]
    )
    assert REMOVE_NO_RECORD_NOTE in dialog.notes()
    assert dialog.answers()["hp"] == "0.25", "pre-filled with the default"
    assert "running it again applies" not in dialog.notes(), "a Remove is not a re-run"


def test_updating_a_compounding_mod_warns_that_it_multiplies_again(qapp: object) -> None:
    """Update re-runs `HealthModifier*{hp}` on top of what is there (T115); the dialog says so."""
    manifest = _baby_mobs()
    again = ManifestPromptDialog(
        None, manifest, manifest.prompts, again=True  # type: ignore[attr-defined]
    )
    first = ManifestPromptDialog(None, manifest, manifest.prompts)  # type: ignore[attr-defined]
    assert COMPOUNDS_NOTE in again.notes()
    assert COMPOUNDS_NOTE not in first.notes()
    assert "as the new setting" not in again.notes(), "false for a module that compounds"


def test_updating_a_mod_that_does_not_compound_gets_no_such_warning(qapp: object) -> None:
    manifest = _hearthstone()
    dialog = ManifestPromptDialog(
        None, manifest, manifest.prompts, again=True  # type: ignore[attr-defined]
    )
    assert COMPOUNDS_NOTE not in dialog.notes()
