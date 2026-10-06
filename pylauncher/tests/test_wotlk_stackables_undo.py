"""T394: Remove on WotLK's "All Stackables to 200" puts every stack size back.

The module repo's Down script cannot know what each stack size was, so the
checksum of item_template came back different (stackable=200 rows 5141 -> 0).
Bigger Stacks on TBC/Vanilla/Tortoise (T385) keeps `yulon_stackable_backup`;
WotLK now keeps the SAME table, with the same statements, around the repo's
files. These tests pin the order, and that the two shapes cannot drift.
"""

from __future__ import annotations

from yulon.controller_wow_tortoise import modules as tortoise_modules
from yulon.controller_wow_wotlk import modules as wotlk_modules


def _steps(when: str) -> list[tuple[str, str]]:
    manifest = wotlk_modules.store().load("mod", "all-stackables")
    return [
        ("file" if step.path else "sql", step.path or step.statement or "")
        for step in manifest.sql
        if step.when == when
    ]


def _tortoise(when: str) -> list[str]:
    manifest = tortoise_modules.store().load("mod", "all-stackables")
    return [step.statement or "" for step in manifest.sql if step.when == when]


def test_install_takes_the_backup_before_the_repos_up_script() -> None:
    steps = _steps("install")
    assert [kind for kind, _ in steps] == ["sql", "sql", "file"]
    assert steps[0][1].startswith("CREATE TABLE IF NOT EXISTS yulon_stackable_backup")
    assert steps[1][1].startswith("INSERT IGNORE INTO yulon_stackable_backup")
    assert steps[2][1] == "All_Stackables_200_Up.sql"


def test_remove_runs_down_then_puts_the_backup_back_then_drops_it() -> None:
    steps = _steps("remove")
    assert [kind for kind, _ in steps] == ["sql", "file", "sql", "sql"]
    assert steps[0][1].startswith("CREATE TABLE IF NOT EXISTS yulon_stackable_backup")
    assert steps[1][1] == "All_Stackables_200_Down.sql"
    assert steps[2][1].startswith("UPDATE item_template t JOIN yulon_stackable_backup b")
    assert steps[3][1] == "DROP TABLE IF EXISTS yulon_stackable_backup;"


def test_the_backup_statements_are_the_ones_bigger_stacks_uses() -> None:
    """One shape for the backup everywhere, so a fix to one is a fix to both."""
    wotlk_install = [text for kind, text in _steps("install") if kind == "sql"]
    assert wotlk_install == _tortoise("install")[:2]
    wotlk_remove = [text for kind, text in _steps("remove") if kind == "sql"]
    assert wotlk_remove[1:] == _tortoise("remove")
