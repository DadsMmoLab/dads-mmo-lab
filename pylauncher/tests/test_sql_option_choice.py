"""T100: a module that ships several alternative SQL files lets the player pick one.

A player on Discord, 2026-09-20: "no way to pick WHICH option file to run" (Stackables,
Hearthstone Tweaks). The inventory in the ticket read every catalogued upstream
repo: the only one that ships several ALTERNATIVE SQL files is
`AsgavinYT/hearthstone-cooldowns`, and its manifest already carried the right
`choice` prompt. What was wrong was the part between the prompt and the player
-- the Install dialog never opened for it (`test_controller_view.py`, the
T100 tests) -- and two things in the manifest that made a choice mean nothing
once it was made: re-choosing matched no row, and Remove did not undo.

These tests are the manifest half. They drive the SHIPPED manifest through the
real `Applier`, with only the clone and the database faked, so what is pinned
is which file the engine actually hands to MySQL for each answer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from yulon import apply as apply_module
from yulon.apply import Applier
from yulon.git import CloneSpec
from yulon.manifest import parse_manifest

# Listed off the upstream repository, AsgavinYT/hearthstone-cooldowns @ 76ef309,
# shallow-cloned 2026-09-23: these five files and nothing else end in `.sql`.
# The README names Hearthstone_30_Min.sql as the way to "set it back to normal".
HEARTHSTONE_SHIPPED = (
    "Hearthstone_1_Sec.sql",
    "Hearthstone_1_Min.sql",
    "Hearthstone_5_Min.sql",
    "Hearthstone_15_Min.sql",
    "Hearthstone_30_Min.sql",
)
HEARTHSTONE_RESET = "Hearthstone_30_Min.sql"


class _Clone:
    """Writes the upstream file names into the clone dir instead of cloning."""

    def __init__(self, names: tuple[str, ...]) -> None:
        self.names = names

    def is_unmodified(self, dest: Path, relative_path: str) -> bool | None:
        return True

    def no_local_commits(self, dest: Path, branch: str | None) -> bool | None:
        return True

    def clone(self, spec: CloneSpec) -> None:
        spec.dest.mkdir(parents=True, exist_ok=True)
        for name in self.names:
            (spec.dest / name).write_text(f"-- {name}\n", encoding="utf-8")
        (spec.dest / ".git").mkdir(exist_ok=True)


class _Sql:
    def __init__(self) -> None:
        self.files: list[tuple[str, str]] = []
        self.statements: list[tuple[str, str]] = []

    def run_file(self, db: str, path: Path) -> None:
        self.files.append((db, path.name))

    def run_statement(self, db: str, statement: str) -> None:
        self.statements.append((db, statement))


def _hearthstone() -> Any:
    from yulon.controller_wow_wotlk import modules as wotlk_modules

    return wotlk_modules.store().load("mod", "hearthstone-cd")


def _raw_hearthstone() -> dict[str, Any]:
    import json

    from yulon.controller_wow_wotlk import modules as wotlk_modules

    path = wotlk_modules.BUNDLED_MANIFESTS_DIR / wotlk_modules.GAME / "mods" / "hearthstone-cd.json"
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return raw


def test_the_hearthstone_manifest_validates_and_asks_a_choice() -> None:
    """The shipped JSON parses under the schema the catalog is checked against.

    `parse_manifest()` is the pydantic model `manifest.schema.json` is dumped
    from, and `test_manifest.py::test_checked_in_json_schema_is_current` holds
    the two equal -- so passing here is passing the schema.
    """
    manifest = parse_manifest(_raw_hearthstone())
    (prompt,) = manifest.prompts
    assert prompt.key == "cooldown"
    assert prompt.kind == "choice"


def test_the_hearthstone_choices_are_exactly_the_files_upstream_ships() -> None:
    """One answer per shipped file, no answer without a file, no file without one.

    Mutation: drop `"1_Sec"` from the manifest's `choices` and the set is short
    one; add `"2_Min"` and it names a file the clone does not carry.
    """
    (prompt,) = _hearthstone().prompts
    offered = {f"Hearthstone_{choice}.sql" for choice in prompt.choices}
    assert offered == set(HEARTHSTONE_SHIPPED)
    assert len(prompt.choices) == len(HEARTHSTONE_SHIPPED)


def test_the_hearthstone_default_is_the_answer_it_always_had() -> None:
    """Nothing changes for someone who clicks straight through the new dialog."""
    (prompt,) = _hearthstone().prompts
    assert prompt.default == "30_Min"
    assert apply_module.check_answer(prompt, prompt.default) == ""


def test_a_choice_prompt_is_one_the_install_dialog_must_put_to_the_player() -> None:
    """T100's root cause: the dialog opened only for a prompt with no default.

    `hearthstone-cd`'s only question has a default, so Install asked nothing and
    applied `Hearthstone_30_Min.sql` -- upstream's RESET file. The mod installed
    and did nothing. A `choice` is asked even when it has a default, because
    being asked is the whole of what a choice is for.

    Mutation: make `must_ask()` return `prompt.default is None` and this fails.
    """
    needed = apply_module.required_prompts(_hearthstone(), "install")
    assert [p.key for p in needed] == ["cooldown"]
    assert [p.key for p in needed if apply_module.must_ask(p)] == ["cooldown"]


@pytest.mark.parametrize("choice", ["1_Sec", "1_Min", "5_Min", "15_Min", "30_Min"])
def test_each_answer_runs_the_reset_then_its_own_file(tmp_path: Path, choice: str) -> None:
    """The chosen value reaches MySQL as the chosen file, through the real applier.

    The reset runs FIRST because every non-reset upstream file is
    `... WHERE spellcooldown_1=-1 AND entry=6948`: it only moves a Hearthstone
    that is still on the stock cooldown. Without the reset, choosing 1 minute
    over an earlier 5 minutes (Update re-asks the question) matched no row
    and changed nothing, while the report said the file had run.

    Mutation: delete the reset step from the manifest and the first element
    is the chosen file alone.
    """
    sql = _Sql()
    report = Applier(tmp_path, git=_Clone(HEARTHSTONE_SHIPPED), sql=sql).install(
        _hearthstone(), {"cooldown": choice}
    )
    assert sql.files == [("world", HEARTHSTONE_RESET), ("world", f"Hearthstone_{choice}.sql")]
    assert sql.statements == []
    assert f"sql Hearthstone_{choice}.sql → world" in report.done, report.done


def test_an_answer_that_is_not_a_shipped_file_is_refused_before_the_clone(tmp_path: Path) -> None:
    """A value from somewhere other than the dialog cannot name a file that is not there."""
    sql = _Sql()
    clone = _Clone(HEARTHSTONE_SHIPPED)
    with pytest.raises(apply_module.ApplyError, match="choose one of"):
        Applier(tmp_path, git=clone, sql=sql).install(_hearthstone(), {"cooldown": "2_Min"})
    assert sql.files == []
    assert not any(tmp_path.rglob("Hearthstone_*.sql")), "it cloned before refusing"


def test_remove_runs_upstreams_own_reset_file(tmp_path: Path) -> None:
    """Remove puts the Hearthstone back on 30 minutes where install moved it.

    Install writes `item_template.spellcooldown_1`/`spellcategorycooldown_1`
    for item 6948. The remove step used to be an UPDATE of `spell_dbc` row 8690
    -- a different table from the one install wrote -- so Remove left the
    shortened cooldown in place (ported from wow-manage.sh,
    which had the same line). Upstream ships the undo; Remove now runs it.

    Mutation: put the `spell_dbc` statement back and `files` is empty while
    `statements` is not.
    """
    applier = Applier(tmp_path, git=_Clone(HEARTHSTONE_SHIPPED), sql=_Sql())
    applier.install(_hearthstone(), {"cooldown": "1_Min"})

    sql = _Sql()
    applier.sql = sql
    applier.remove(_hearthstone())
    assert sql.files == [("world", HEARTHSTONE_RESET)]
    assert sql.statements == []
    assert apply_module.required_prompts(_hearthstone(), "remove") == ()
