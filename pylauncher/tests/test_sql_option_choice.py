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
from pydantic import ValidationError

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
def test_each_answer_runs_the_reset_and_its_own_file_as_one_transaction(
    tmp_path: Path, choice: str
) -> None:
    """The chosen value reaches MySQL as the chosen file, through the real applier.

    The reset runs FIRST because every non-reset upstream file is
    `... WHERE spellcooldown_1=-1 AND entry=6948`: it only moves a Hearthstone
    that is still on the stock cooldown. Without the reset, choosing 1 minute
    over an earlier 5 minutes matched no row and changed nothing.

    And the two go down as ONE text inside one transaction (T100 review,
    Codex): as two separate files, a chosen file that failed left the reset
    committed, so a working custom cooldown became 30 minutes on a failure.

    Mutation: split the step back into two `path` steps and `files` is two
    separate runs while `statements` is empty.
    """
    sql = _Sql()
    report = Applier(tmp_path, git=_Clone(HEARTHSTONE_SHIPPED), sql=sql).install(
        _hearthstone(), {"cooldown": choice}
    )
    assert sql.files == []
    assert sql.statements == [
        (
            "world",
            "START TRANSACTION;\n"
            f"-- {HEARTHSTONE_RESET}\n"
            f"-- Hearthstone_{choice}.sql\n"
            "COMMIT;\n",
        )
    ]
    assert f"sql {HEARTHSTONE_RESET} → world" in report.done, report.done
    assert f"sql Hearthstone_{choice}.sql → world" in report.done, report.done


class _TransactionalDb:
    """A world DB with ONE value in it, and MySQL's two ways of taking SQL.

    A file (`run_file`) is autocommit: each line lands as it runs. A text that
    starts `START TRANSACTION;` is applied to a copy, and the copy is kept only
    if every line ran -- `mysql` in batch mode stops at the first error and the
    closed session rolls the open transaction back. Lines are `SET <n>` or
    `FAIL`; `FAIL` is a statement MySQL refuses.
    """

    def __init__(self, cooldown: int) -> None:
        self.cooldown = cooldown
        self.sent: list[str] = []

    def _apply(self, text: str, value: int) -> int:
        for line in text.splitlines():
            if line.startswith("SET "):
                value = int(line.split()[1])
            elif line == "FAIL":
                raise apply_module.ApplyError("mysql exited 1: ERROR 1146 (42S02)")
        return value

    def run_file(self, db: str, path: Path) -> None:
        self.sent.append(f"file {path.name}")
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            self.cooldown = self._apply(line, self.cooldown)

    def run_statement(self, db: str, statement: str) -> None:
        self.sent.append(statement)
        # `_apply` works on a copy and only returns if every line ran: that is
        # the rollback of a text that opened a transaction and never reached
        # COMMIT. (A text without one would be autocommit per line; none is sent.)
        self.cooldown = self._apply(statement, self.cooldown)


class _CloneWith(_Clone):
    def __init__(self, texts: dict[str, str]) -> None:
        super().__init__(tuple(texts))
        self.texts = texts

    def clone(self, spec: CloneSpec) -> None:
        super().clone(spec)
        for name, text in self.texts.items():
            (spec.dest / name).write_text(text, encoding="utf-8")


def test_a_chosen_file_that_fails_leaves_the_earlier_cooldown_in_place(tmp_path: Path) -> None:
    """Codex, T100 review: the reset committed, then the chosen file failed.

    A Hearthstone on 5 minutes (300000) and a second install whose chosen file
    MySQL refuses. Run as two files, the reset landed first and the cooldown
    was 30 minutes when the failure was reported. Run as one transaction the
    failure takes the reset with it.

    Mutation: split the step into two `path` steps and `cooldown` is -1.
    """
    texts = {name: f"-- {name}\n" for name in HEARTHSTONE_SHIPPED}
    texts[HEARTHSTONE_RESET] = "SET -1\n"
    texts["Hearthstone_1_Min.sql"] = "SET 60000\nFAIL\n"
    db = _TransactionalDb(cooldown=300000)
    with pytest.raises(apply_module.ApplyError, match="ERROR 1146"):
        Applier(tmp_path, git=_CloneWith(texts), sql=db).install(
            _hearthstone(), {"cooldown": "1_Min"}
        )
    assert db.cooldown == 300000
    assert len(db.sent) == 1 and db.sent[0].startswith("START TRANSACTION;")
    assert db.sent[0].rstrip().endswith("COMMIT;")


def test_a_chosen_file_missing_from_the_clone_runs_no_sql_at_all(tmp_path: Path) -> None:
    """Preflight: every file the press will run is looked for before the first one runs.

    An upstream that renamed or dropped `Hearthstone_5_Min.sql` used to get
    its reset applied and then the refusal. Now it gets the refusal only.

    Here the transaction alone would also send nothing; the next test is the
    one the preflight is needed for, a press of several steps.
    """
    present = tuple(n for n in HEARTHSTONE_SHIPPED if n != "Hearthstone_5_Min.sql")
    sql = _Sql()
    with pytest.raises(apply_module.ApplyError, match="Hearthstone_5_Min.sql") as caught:
        Applier(tmp_path, git=_Clone(present), sql=sql).install(
            _hearthstone(), {"cooldown": "5_Min"}
        )
    assert "nothing was run" in str(caught.value)
    assert sql.files == [] and sql.statements == []


def test_the_preflight_covers_every_step_of_the_press_not_only_its_own(tmp_path: Path) -> None:
    """Two plain file steps: the second one missing stops the first from running."""
    manifest = parse_manifest(
        {
            "id": "two-files",
            "name": "Two files",
            "type": "mod",
            "game": "wow-wotlk",
            "source": {"repo": "someone/two-files"},
            "sql": [{"db": "world", "path": "a.sql"}, {"db": "world", "path": "b.sql"}],
        }
    )
    sql = _Sql()
    with pytest.raises(apply_module.ApplyError, match="b.sql"):
        Applier(tmp_path, git=_Clone(("a.sql",)), sql=sql).install(manifest)
    assert sql.files == []


def test_a_transaction_refuses_a_file_that_would_commit_on_its_own(tmp_path: Path) -> None:
    """DDL commits implicitly in MySQL, so a `then` step holding one is not atomic.

    Refused before anything is sent, rather than sent and called a transaction.
    """
    manifest = parse_manifest(
        {
            "id": "ddl",
            "name": "DDL",
            "type": "mod",
            "game": "wow-wotlk",
            "source": {"repo": "someone/ddl"},
            "sql": [{"db": "world", "path": "a.sql", "then": ["b.sql"]}],
        }
    )
    texts = {"a.sql": "UPDATE t SET x = 1;\n", "b.sql": "  create table t2 (id int);\n"}
    sql = _Sql()
    with pytest.raises(apply_module.ApplyError, match="commits on its own"):
        Applier(tmp_path, git=_CloneWith(texts), sql=sql).install(manifest)
    assert sql.files == [] and sql.statements == []


@pytest.mark.parametrize(
    "step, refusal",
    [
        ({"db": "world", "statement": "SELECT 1", "then": ["b.sql"]}, "then"),
        ({"db": "world", "path": "a/*.sql", "then": ["b.sql"]}, "glob"),
        ({"db": "world", "path": "a.sql", "then": ["b/*.sql"]}, "glob"),
        ({"db": "world", "path": "a.sql", "then": ["b.sql"], "applied_by": "db-import"}, "direct"),
    ],
)
def test_then_is_only_for_named_files_the_app_runs_itself(
    step: dict[str, Any], refusal: str
) -> None:
    body = {"id": "x", "name": "X", "type": "mod", "game": "wow-wotlk", "sql": [step]}
    with pytest.raises(ValidationError, match=refusal):
        parse_manifest(body)


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
