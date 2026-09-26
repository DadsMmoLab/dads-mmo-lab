"""T122: a mob multiplier outside 0.01..100 (0 and below included) is refused before any SQL runs.

The four mob mods install `HealthModifier=HealthModifier*{hp}` and remove with
`HealthModifier/{hp}`. At 0 the install destroys every creature's value, and
the Remove cannot divide it back (MySQL writes NULL for a division by zero). A
negative multiplier flips the sign of health and damage, and dividing it back
only works if the exact same number is typed again. The question accepted both
before this ticket; T115 refused only a RE-RUN over a recorded zero. The fix wave
widened "above 0" to a finite range: the modifiers are FLOAT, so x1e-50 rounds to
0 and x1e39 overflows.

The range is declared in the manifest (`Prompt.min`/`Prompt.max`) and checked by
`apply.check_answer()`, which is the one function both the dialog and the
applier's own pre-flight call. These tests drive both ends: the real dialog
with its OK button, and the real `Applier` with a SQL runner that records
every text it is handed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialogButtonBox

from yulon import module_answers
from yulon.apply import Applier, ApplyError, check_answer
from yulon.controller_wow_wotlk import modules as wotlk_modules
from yulon.manifest import Manifest, parse_manifest
from yulon.ui.widgets.manifest_prompt import ManifestPromptDialog

MOB_MODS = ("baby-mobs", "buff-mobs", "nerf-mobs", "xbuff-mobs")


def _mob(item_id: str = "baby-mobs") -> Manifest:
    return wotlk_modules.store().load("mod", item_id)


def _all(value: str) -> dict[str, str]:
    return {"hp": value, "dmg": value, "arm": value, "spd": value}


class _Recorder:
    """A `SqlRunner` that only records what it is handed."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    def run_file(self, db: str, path: Path) -> None:
        self.texts.append(str(path))

    def run_statement(self, db: str, statement: str) -> None:
        self.texts.append(statement)


def _ok_enabled(dialog: ManifestPromptDialog) -> bool:
    box = dialog.findChild(QDialogButtonBox)
    assert box is not None
    ok = box.button(QDialogButtonBox.StandardButton.Ok)
    assert ok is not None
    return ok.isEnabled()


# ------------------------------------------------------------ the manifests

RANGE = "this must be between 0.01 and 100"
"""The dialog's and the applier's sentence for a mob multiplier out of range (fix wave).

0.01 to 100 is a ruling, not a measurement: the mods' own origin (wow-manage.sh's
configure prompt) required only "a positive number", and nothing upstream names a
range. `HealthModifier` is a FLOAT column: x1e-50 underflows to 0 (the T122 defect
again, one step removed) and x1e39 overflows. Recorded in the ticket."""


@pytest.mark.parametrize("item_id", MOB_MODS)
def test_every_multiplier_question_of_the_four_mob_mods_declares_a_finite_range(
    item_id: str,
) -> None:
    """All four questions, `spd` too: an attack time times 0 is as lost as a health."""
    manifest = _mob(item_id)
    assert manifest.prompts, item_id
    for prompt in manifest.prompts:
        assert prompt.kind == "float"
        assert (prompt.min, prompt.max) == (0.01, 100), f"{item_id}: {prompt.key}"


def _one(prompt: dict[str, object]) -> Manifest:
    return parse_manifest(
        {
            "id": "kindly",
            "name": "Kindly",
            "type": "mod",
            "game": "wow-wotlk",
            "prompts": [{"key": "k", "question": "q", **prompt}],
        }
    )


@pytest.mark.parametrize("field", ["min", "max"])
def test_a_bound_is_only_accepted_on_a_number_question(field: str) -> None:
    for kind in ("string", "bool"):
        with pytest.raises(ValueError, match=f"`{field}`"):
            _one({"kind": kind, field: 1})
    with pytest.raises(ValueError, match=f"`{field}`"):
        _one({"kind": "choice", "choices": ["1"], field: 1})
    for kind in ("int", "float"):
        assert getattr(_one({"kind": kind, field: 1}).prompts[0], field) == 1


@pytest.mark.parametrize("field", ["min", "max"])
def test_a_bound_must_itself_be_finite(field: str) -> None:
    with pytest.raises(ValueError, match="finite"):
        _one({"kind": "float", field: float("inf")})


def test_a_range_upside_down_is_refused() -> None:
    with pytest.raises(ValueError, match="min"):
        _one({"kind": "float", "min": 2, "max": 1})


@pytest.mark.parametrize(("default", "bounds"), [("0", {"min": 0.01}), ("101", {"max": 100})])
def test_a_default_outside_its_own_range_is_refused_when_the_manifest_is_read(
    default: str, bounds: dict[str, float]
) -> None:
    """A manifest cannot ship a default that its own question refuses."""
    with pytest.raises(ValueError, match="default"):
        _one({"kind": "float", "default": default, **bounds})


# ------------------------------------------------------------ the rule


@pytest.mark.parametrize(
    "value", ["0", "0.0", "-0", "-1", "-0.25", "1e-50", "0.009", "1e39", "100.01", "1e2.5"]
)
def test_check_answer_refuses_a_multiplier_outside_the_range(value: str) -> None:
    """1e-50 underflows the FLOAT column to 0 and 1e39 overflows it: neither can be divided back."""
    problem = check_answer(_mob().prompts[0], value)
    expected = "this must be a number" if value == "1e2.5" else RANGE
    assert problem == expected, (value, problem)


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "1e999"])
def test_check_answer_refuses_a_number_that_is_not_finite(value: str) -> None:
    """`float()` reads these; a range check alone would let `nan` compare false both ways."""
    assert check_answer(_mob().prompts[0], value) == "this must be a number"


@pytest.mark.parametrize("value", ["0.01", "0.25", "1", "2", "100", "1e-2", "1e2"])
def test_check_answer_accepts_a_multiplier_inside_the_range_ends_included(value: str) -> None:
    assert check_answer(_mob().prompts[0], value) == ""


@pytest.mark.parametrize(
    ("bounds", "value", "said"),
    [
        ({"min": 1}, "0", "this must be at least 1"),
        ({"max": 5}, "6", "this must be at most 5"),
        ({"min": 1, "max": 5}, "6", "this must be between 1 and 5"),
    ],
)
def test_the_sentence_names_the_bounds_the_question_has(
    bounds: dict[str, float], value: str, said: str
) -> None:
    prompt = _one({"kind": "int", **bounds}).prompts[0]
    assert check_answer(prompt, value) == said


def test_a_number_question_with_no_bound_still_accepts_zero() -> None:
    """The bound is per question: XP rates, GUIDs and the rest keep what they accepted."""
    manifest = parse_manifest(
        {
            "id": "kindly",
            "name": "Kindly",
            "type": "mod",
            "game": "wow-wotlk",
            "prompts": [{"key": "k", "question": "q", "kind": "float"}],
        }
    )
    assert check_answer(manifest.prompts[0], "0") == ""
    assert check_answer(manifest.prompts[0], "-1") == ""


# ------------------------------------------------------------ the dialog


@pytest.mark.parametrize("value", ["0", "-1", "1e-50", "1e39", "100.01"])
@pytest.mark.parametrize("removing", [False, True])
def test_the_dialog_keeps_ok_disabled_and_names_the_range(
    qapp: object, value: str, removing: bool
) -> None:
    manifest = _mob()
    dialog = ManifestPromptDialog(None, manifest, manifest.prompts, removing=removing)
    assert _ok_enabled(dialog), "the defaults are acceptable"
    dialog.set_answer("hp", value)
    assert not _ok_enabled(dialog), value
    assert dialog.problem() == f"HP multiplier — {RANGE}.", dialog.problem()


@pytest.mark.parametrize("value", ["0.25", "0.01", "100"])
def test_the_dialog_accepts_a_value_inside_the_range(qapp: object, value: str) -> None:
    manifest = _mob()
    dialog = ManifestPromptDialog(None, manifest, manifest.prompts)
    dialog.set_answer("hp", "0")
    dialog.set_answer("hp", value)
    assert _ok_enabled(dialog)
    assert dialog.problem() == ""


def test_a_remembered_zero_is_not_filled_in(qapp: object) -> None:
    """An answers file written by a build that accepted 0 must not pre-fill it."""
    manifest = _mob()
    dialog = ManifestPromptDialog(
        None, manifest, manifest.prompts, again=True, remembered=_all("0")
    )
    assert dialog.answers()["hp"] == "0.25", "the default, not the remembered zero"
    assert _ok_enabled(dialog)


# ------------------------------------------------------------ the applier


@pytest.mark.parametrize("value", ["0", "-1", "1e-50", "1e39", "100.01"])
def test_the_applier_refuses_an_install_outside_the_range_before_any_sql(
    tmp_path: Path, value: str
) -> None:
    """For callers that pass values directly, with no dialog in front."""
    db = _Recorder()
    applier = Applier(tmp_path, sql=db)
    with pytest.raises(ApplyError) as refused:
        applier.install(_mob(), {**_all("0.25"), "dmg": value})
    message = str(refused.value)
    assert "baby-mobs" in message and "Damage multiplier" in message, message
    assert RANGE in message and "Nothing was changed" in message, message
    assert db.texts == [], "no SQL was sent"
    assert not (tmp_path / module_answers.ANSWERS_FILE).exists(), "no record, no answers"


def test_the_applier_refuses_a_remove_at_zero_before_any_sql(tmp_path: Path) -> None:
    db = _Recorder()
    applier = Applier(tmp_path, sql=db)
    with pytest.raises(ApplyError, match=RANGE):
        applier.remove(_mob(), _all("0"))
    assert db.texts == []


def test_the_applier_installs_at_a_quarter(tmp_path: Path) -> None:
    db = _Recorder()
    applier = Applier(tmp_path, sql=db)
    applier.install(_mob(), _all("0.25"))
    assert len(db.texts) == 1 and "HealthModifier=HealthModifier*0.25" in db.texts[0]
    assert applier.applied_record(_mob()) == (_all("0.25"), "")
