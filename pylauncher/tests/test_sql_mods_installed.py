"""A mod with no repository that only runs SQL shows as installed, and its Remove undoes it.

Bigger Stacks (`all-stackables`) on TBC, Vanilla and Tortoise has no `source`: its
install sends three statements to the world database and leaves no folder under
`sql_scripts/clones/`. It is not relative (`reapplies_on_top()` is False), so
T121's `applied` record, written only by the relative mob multipliers, never
named it either. The row read Not installed after a finished install and offered
no Remove (T385).

Its install now writes the same `applied` record once a statement has been sent,
and its Remove drops it. Being in `applied`, it is dropped with the database's
other records when a database is imported fresh. An install made before the
record existed is not read back from the database (the Modules tab reads no
database on a reload): Install again adopts it, because the statements are
re-runnable and the backup keeps the original values.

The applier is the REAL `Applier`; the SQL runner records what it is handed.
The tab tests press the real row buttons over the shipped wiring
(`ControllerServices.for_entry()`) and a stopped stack.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from tests.test_controller_view import CMANGOS_SQL_GAMES, _cmangos_stack
from yulon import apply as apply_module
from yulon import module_answers
from yulon.apply import Applier, ApplyError
from yulon.catalog.catalog import load_catalog
from yulon.controller_wow_tbc import modules as tbc_modules
from yulon.controller_wow_tortoise import modules as tortoise_modules
from yulon.controller_wow_vanilla import modules as vanilla_modules
from yulon.controller_wow_wotlk import modules as wotlk_modules
from yulon.manifest import Manifest
from yulon.manifest_store import ManifestStore
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import ControllerView
from yulon.ui.widgets.job import run_inline
from yulon.ui.widgets.modules_panel import BADGE_INSTALLED, BADGE_NOT_INSTALLED

STORES: dict[str, ManifestStore] = {
    "wow-tbc": tbc_modules.store(),
    "wow-tortoise": tortoise_modules.store(),
    "wow-vanilla": vanilla_modules.store(),
    "wow-wotlk": wotlk_modules.store(),
}
SOURCELESS_SQL_GAMES = ("wow-tbc", "wow-tortoise", "wow-vanilla")
KEY = "mod/all-stackables"


class _Recorder:
    """A `SqlRunner` that records every statement; `fail_on` makes one raise."""

    def __init__(self, fail_on: str = "") -> None:
        self.texts: list[str] = []
        self.fail_on = fail_on

    def run_file(self, db: str, path: Path) -> None:
        raise AssertionError(f"Bigger Stacks runs no file, got {path}")

    def run_statement(self, db: str, statement: str) -> None:
        if self.fail_on and self.fail_on in statement:
            raise ApplyError(f"ERROR 1146: the statement failed: {statement[:40]}")
        self.texts.append(statement)


def _stacks(game: str) -> Manifest:
    return STORES[game].load("mod", "all-stackables")


def _applied(server_dir: Path) -> frozenset[str]:
    return module_answers.recorded_keys(server_dir).applied


def _installed(server_dir: Path) -> frozenset[str]:
    return apply_module.installed_modules(server_dir).get("mod", frozenset())


# ------------------------------------------------------------ which mods


def test_bigger_stacks_with_no_repository_is_the_only_mod_with_a_database_receipt() -> None:
    """Read off every shipped manifest: no source, SQL sent straight to the database,
    not relative. WotLK's Bigger Stacks has a repository and a folder; the mob
    multipliers already record what they applied (T115); Experience Rates has no SQL.
    """
    found = {
        (game, manifest.id)
        for game, store in STORES.items()
        for manifest in store.load_all("mod")
        if apply_module.database_receipt(manifest)
    }
    assert found == {(game, "all-stackables") for game in SOURCELESS_SQL_GAMES}


@pytest.mark.parametrize("game", SOURCELESS_SQL_GAMES)
def test_its_install_can_be_run_again_over_an_older_install(game: str) -> None:
    """An install from before the receipt is adopted by Install again, which must be
    harmless over itself: the backup table only if it is not there, its rows only if
    they are not there (the ORIGINAL values stay), then the one absolute UPDATE.

    Fails if a manifest edit makes a second install overwrite the backup.
    """
    install = [step.statement or "" for step in _stacks(game).sql if step.when == "install"]
    assert install[0].startswith("CREATE TABLE IF NOT EXISTS yulon_stackable_backup")
    assert install[1].startswith("INSERT IGNORE INTO yulon_stackable_backup")
    assert re.fullmatch(
        r"UPDATE item_template SET stackable = \{stack_size\} WHERE .*;", install[2]
    )
    assert len(install) == 3


# ------------------------------------------------------------ the applier


@pytest.mark.parametrize("game", SOURCELESS_SQL_GAMES)
def test_install_records_it_and_it_reads_installed(tmp_path: Path, game: str) -> None:
    """Mutation: drop the record write in `Applier.install()` and it reads Not installed."""
    sql = _Recorder()

    Applier(tmp_path, sql=sql).install(_stacks(game), {"stack_size": "500"})

    assert len(sql.texts) == 3
    assert KEY in _applied(tmp_path)
    assert "all-stackables" in _installed(tmp_path)
    assert "all-stackables" not in apply_module.installed_clones(tmp_path).get("mod", set())
    assert module_answers.read_applied(tmp_path, _stacks(game)) == {"stack_size": "500"}


@pytest.mark.parametrize("game", SOURCELESS_SQL_GAMES)
def test_remove_runs_the_restore_and_drops_the_record(tmp_path: Path, game: str) -> None:
    """Mutation: drop the record clear in `Applier.remove()` and it still reads Installed."""
    sql = _Recorder()
    applier = Applier(tmp_path, sql=sql)
    applier.install(_stacks(game), {"stack_size": "500"})

    applier.remove(_stacks(game))

    assert sql.texts[3].startswith("UPDATE item_template t JOIN yulon_stackable_backup b")
    assert sql.texts[4] == "DROP TABLE IF EXISTS yulon_stackable_backup;"
    assert KEY not in _applied(tmp_path)
    assert "all-stackables" not in _installed(tmp_path)


def test_with_no_sql_runner_nothing_is_recorded(tmp_path: Path) -> None:
    """Nothing reached the database, so nothing may say it is there.

    Mutation: record on every finished install and the row reads Installed over a
    database nothing touched.
    """
    Applier(tmp_path, sql=None).install(_stacks("wow-tortoise"), {"stack_size": "500"})

    assert KEY not in _applied(tmp_path)
    assert "all-stackables" not in _installed(tmp_path)


def test_a_remove_with_no_sql_runner_keeps_the_record(tmp_path: Path) -> None:
    """The restore never ran, so the stacks are still raised and Remove stays on offer."""
    Applier(tmp_path, sql=_Recorder()).install(_stacks("wow-tortoise"), {"stack_size": "500"})

    Applier(tmp_path, sql=None).remove(_stacks("wow-tortoise"))

    assert KEY in _applied(tmp_path)


def test_a_failed_install_records_nothing(tmp_path: Path) -> None:
    """A statement that fails leaves no record, so the row offers Install again, which
    is safe over whatever the failed run left (`test_its_install_can_be_run_again...`)."""
    sql = _Recorder(fail_on="UPDATE item_template")

    with pytest.raises(ApplyError):
        Applier(tmp_path, sql=sql).install(_stacks("wow-tortoise"), {"stack_size": "500"})

    assert len(sql.texts) == 2
    assert KEY not in _applied(tmp_path)


def test_a_failed_restore_keeps_the_record(tmp_path: Path) -> None:
    """The restore failed, so the stacks may still be raised: Remove stays on offer, and a
    second Remove runs the same restore again."""
    Applier(tmp_path, sql=_Recorder()).install(_stacks("wow-tortoise"), {"stack_size": "500"})

    with pytest.raises(ApplyError):
        Applier(tmp_path, sql=_Recorder(fail_on="DROP TABLE")).remove(_stacks("wow-tortoise"))

    assert KEY in _applied(tmp_path)


def test_a_database_imported_fresh_drops_the_record(tmp_path: Path) -> None:
    """The record is about the database, so it goes with it (`forget_database_records()`,
    run by the install engine when it imports the databases fresh). The saved answer stays.
    """
    Applier(tmp_path, sql=_Recorder()).install(_stacks("wow-tortoise"), {"stack_size": "500"})

    forgot, problem = module_answers.forget_database_records(tmp_path)

    assert forgot and problem == ""
    assert "all-stackables" not in _installed(tmp_path)
    assert module_answers.read_answers(tmp_path, _stacks("wow-tortoise")) == {"stack_size": "500"}


def test_the_record_is_not_a_relative_one(tmp_path: Path) -> None:
    """A second Install is not refused and does not undo the first: Bigger Stacks is
    absolute, so it runs its three statements again (T115's re-run is for the
    multipliers only)."""
    sql = _Recorder()
    applier = Applier(tmp_path, sql=sql)
    applier.install(_stacks("wow-tortoise"), {"stack_size": "500"})

    applier.install(_stacks("wow-tortoise"), {"stack_size": "1000"})

    assert len(sql.texts) == 6
    assert sql.texts[5] == "UPDATE item_template SET stackable = 1000 WHERE stackable > 1;"
    assert module_answers.read_applied(tmp_path, _stacks("wow-tortoise")) == {"stack_size": "1000"}


# ------------------------------------------------------------ through the tab


@pytest.fixture
def _inline_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)


def _tab(
    game: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ControllerView, list[tuple[str, str]], Path]:
    stack, services, _manifest = _cmangos_stack(game, tmp_path, monkeypatch, world_up=False)
    view = ControllerView(
        load_catalog().get(game),
        services,
        status_poll_ms=0,
        prompt_asker=lambda parent, manifest, prompts, **_: {"stack_size": "500"},
    )
    return view, stack.sql, tmp_path / game


@pytest.mark.usefixtures("_inline_jobs")
@pytest.mark.parametrize("game", CMANGOS_SQL_GAMES)
def test_bigger_stacks_installs_shows_installed_and_removes_through_the_row_buttons(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, game: str
) -> None:
    """The ticket's sequence on the shipped wiring, with the world stopped: Install, the
    row says Installed and offers Remove; Remove runs the restore and the row says Not
    installed again.

    Mutation: drop the record write and the row still reads Not installed after Install.
    """
    view, sent, server_dir = _tab(game, tmp_path, monkeypatch)
    row = view.modules_panel.row("all-stackables")
    assert row.data.badge == BADGE_NOT_INSTALLED

    row.install_button.click()

    assert len(sent) == 3, sent
    row = view.modules_panel.row("all-stackables")
    assert row.data.installed and row.data.badge == BADGE_INSTALLED
    assert row.remove_button is not None and row.install_button is None
    assert KEY in _applied(server_dir)

    row.remove_button.click()

    assert len(sent) == 5, sent
    assert sent[3][1].startswith("UPDATE item_template t JOIN yulon_stackable_backup b")
    row = view.modules_panel.row("all-stackables")
    assert not row.data.installed and row.data.badge == BADGE_NOT_INSTALLED
    assert row.install_button is not None


FORGET_ACTION = "Forget Yu'lon's record…"


@pytest.mark.usefixtures("_inline_jobs")
def test_forget_is_offered_for_bigger_stacks_and_clears_its_record_without_any_sql(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record left over a database restored from a backup would offer a Remove whose
    restore fails (the backup table is not there), for ever. Forget is the way out,
    as it is for the mob multipliers: it asks first, No by default, says the database
    is not changed, and sends no SQL.

    Mutation: keep Forget to the relative mods only and no action is offered.
    """
    view, sent, server_dir = _tab("wow-tortoise", tmp_path, monkeypatch)
    view.modules_panel.row("all-stackables").install_button.click()
    asked: list[tuple[object, ...]] = []

    def question(*a: object, **_k: object) -> int:
        asked.append(a)
        return int(QMessageBox.StandardButton.Yes.value)

    monkeypatch.setattr(controller_view_module.QMessageBox, "question", question)
    view.modules_panel.select("all-stackables")
    menu = view._module_menu("all-stackables")
    (action,) = [a for a in menu.actions() if a.text() == FORGET_ACTION]
    action.trigger()

    assert len(asked) == 1
    assert asked[0][2] == (
        "Yu'lon forgets that Bigger Stacks is installed. The database is not changed. Use "
        "this only if the stack sizes are already back to normal, for example after "
        "restoring a backup."
    )
    assert asked[0][4] == QMessageBox.StandardButton.No, "default No"
    assert len(sent) == 3, "Forget sent no SQL"
    assert KEY not in _applied(server_dir)
    assert view.modules_panel.row("all-stackables").data.badge == BADGE_NOT_INSTALLED


def test_the_record_file_names_it_under_applied(tmp_path: Path) -> None:
    """The record is T121's `applied` map, nothing new: an older Yu'lon reading this file
    reads the mod as installed too, and the fresh-import drop already covers it."""
    Applier(tmp_path, sql=_Recorder()).install(_stacks("wow-vanilla"), {"stack_size": "500"})

    record = json.loads((tmp_path / module_answers.ANSWERS_FILE).read_text(encoding="utf-8"))

    assert record["applied"] == {KEY: {"stack_size": "500"}}
