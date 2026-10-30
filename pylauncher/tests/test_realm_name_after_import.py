"""T808: the saved realm name survives every press that imports or makes the login database again.

An import seeds the catalog's own name into the realm row. The install's closing step put the
owner's choice back; a Rebuild, "Update the server to latest…" (which ends in a Rebuild) and
Repair of a missing database did not, so the realm list said the catalog name until another
Start (Codex review of T808, round 1). Each now ends in the one shared step,
`StagedInstaller._apply_realm_name()`, which writes the saved name and reads the row back.

The machine is `support_native.Recorder` with `reseeds_realm_name`: every importer run (the
one-shot import service, and the `recreate` a Rebuild's new build starts with) sets the row's
name back to the catalog's, as a real import does.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support_native import ABSENT, ENTRY, IMPORTED, Recorder, engine, install
from tests.test_repair_a_missing_database import _reads
from tests.test_update_to_latest import _press
from tests.test_update_to_latest import _ready as _ready_to_update
from yulon import realm_name, resources
from yulon.catalog.installer import InstallOptions
from yulon.manifest import Db

CHOSEN = "Jerra's Realm"
SEEDED = "AzerothCore"


class _RowSql:
    """The Networking tab's `sql` seam over the Recorder's realm row: what the field would show."""

    def __init__(self, rec: Recorder) -> None:
        self.rec = rec

    def query(self, db: Db, statement: str) -> str:
        assert statement == realm_name.name_query(ENTRY)
        return self.rec.realm_name.encode("utf-8").hex().upper() + "\n"

    def run_statement(self, db: Db, statement: str) -> None:  # pragma: no cover - reads only
        raise AssertionError("the field only reads")


def _shown(rec: Recorder, server_dir: Path) -> realm_name.NameState:
    return realm_name.RealmNames(ENTRY, server_dir, _RowSql(rec)).current()


def _installed_with_a_chosen_name(rec: Recorder, tmp_path: Path) -> Path:
    server_dir = tmp_path / "wow"
    install(rec, server_dir)
    assert realm_name.record_name(server_dir, CHOSEN) == ""
    rec.realm_name = CHOSEN  # what Save name left in the row
    rec.reseeds_realm_name = True
    rec.calls.clear()
    rec.sql_scripts.clear()
    return server_dir


def _name_statements(rec: Recorder) -> list[str]:
    return [s for s in rec.sql_scripts if " SET name=" in s]


def test_a_rebuild_whose_import_reseeds_the_row_ends_with_the_chosen_name(tmp_path: Path) -> None:
    """Mutation: drop `_apply_realm_name()` from `rebuild()` and the row ends as SEEDED."""
    rec = Recorder(images=True)
    server_dir = _installed_with_a_chosen_name(rec, tmp_path)

    said = list(
        engine(rec, read_database=_reads("present")).rebuild(InstallOptions(server_dir=server_dir))
    )

    assert "one-shot:ac-db-import" in rec.calls and "recreate" in rec.calls, rec.calls
    assert rec.realm_name == CHOSEN
    assert _name_statements(rec) == [realm_name.name_sql(ENTRY, CHOSEN)]
    # After the importer and the servers' recreate, never before them (it would be undone).
    assert rec.calls.index("sql") > rec.calls.index("recreate"), rec.calls
    assert f"The realm is called '{CHOSEN}' in the game's realm list, the name you chose." in said
    assert said[-1] == f"{ENTRY.name} was rebuilt and is running in {server_dir}"
    assert _shown(rec, server_dir) == realm_name.NameState(CHOSEN, True)


def test_update_to_latest_ends_its_rebuild_with_the_chosen_name(tmp_path: Path) -> None:
    """The update's own route into `rebuild()`. Mutation: as above."""
    rec, server_dir = _ready_to_update(tmp_path)
    assert realm_name.record_name(server_dir, CHOSEN) == ""
    rec.realm_name = CHOSEN
    rec.reseeds_realm_name = True
    rec.sql_scripts.clear()

    said = _press(rec, server_dir)

    assert "recreate" in rec.calls, rec.calls
    assert rec.realm_name == CHOSEN
    assert _name_statements(rec) == [realm_name.name_sql(ENTRY, CHOSEN)]
    assert any("is running on" in line for line in said), said
    assert _shown(rec, server_dir).shown == CHOSEN


def test_a_repair_of_a_missing_database_ends_with_the_chosen_name(tmp_path: Path) -> None:
    """The import makes the login database from nothing. Mutation: drop it from the Repair."""
    rec = Recorder(images=True)
    server_dir = _installed_with_a_chosen_name(rec, tmp_path)
    rec.realm_name = ""  # Docker lost the database: no row at all until the import seeds one
    rec.db_started = False
    rec.probe_answers = [ABSENT, IMPORTED]

    said = list(
        engine(rec, read_database=_reads("missing")).repair_database(
            InstallOptions(server_dir=server_dir)
        )
    )

    assert "one-shot:ac-db-import" in rec.calls, rec.calls
    assert rec.realm_name == CHOSEN
    assert _name_statements(rec) == [realm_name.name_sql(ENTRY, CHOSEN)]
    assert rec.calls.index("sql") > rec.calls.index("start"), rec.calls
    assert f"The realm is called '{CHOSEN}' in the game's realm list, the name you chose." in said
    assert _shown(rec, server_dir).shown == CHOSEN


def test_with_no_name_saved_a_rebuild_sends_no_name_and_says_nothing_of_it(
    tmp_path: Path,
) -> None:
    rec = Recorder(images=True)
    server_dir = tmp_path / "wow"
    install(rec, server_dir)
    rec.reseeds_realm_name = True
    rec.sql_scripts.clear()

    said = list(
        engine(rec, read_database=_reads("present")).rebuild(InstallOptions(server_dir=server_dir))
    )

    assert _name_statements(rec) == []
    assert not [line for line in said if "realm is called" in line or "realm name" in line]
    assert rec.realm_name == SEEDED


def test_a_row_that_does_not_read_back_the_name_is_said_and_the_rebuild_still_succeeds(
    tmp_path: Path,
) -> None:
    """An UPDATE that matched nothing exits 0 too: the step reads the row before it says so."""
    rec = Recorder(images=True)
    server_dir = _installed_with_a_chosen_name(rec, tmp_path)
    rec.failing_sql = " SET name="

    said = list(
        engine(rec, read_database=_reads("present")).rebuild(InstallOptions(server_dir=server_dir))
    )

    line = next(line for line in said if CHOSEN in line)
    assert line.startswith("The rebuild is finished, but the realm name you chose"), line
    assert "next Start puts it back" in line
    assert said[-1] == f"{ENTRY.name} was rebuilt and is running in {server_dir}"
    assert realm_name.read_saved_name(server_dir) == CHOSEN, "the choice is kept for the Start"


def test_a_name_the_row_does_not_take_is_caught_by_the_read_back(tmp_path: Path) -> None:
    """Mutation: drop the read-back and the step says the name is set while the row disagrees."""
    rec = Recorder(images=True)
    server_dir = _installed_with_a_chosen_name(rec, tmp_path)
    real = rec.exec_stdin

    def ignores_the_name(*args: object, **kwargs: object) -> object:
        proc = real(*args, **kwargs)  # type: ignore[arg-type]
        rec.realm_name = SEEDED  # exits 0, row unchanged (no row with that id, say)
        return proc

    rec.exec_stdin = ignores_the_name  # type: ignore[method-assign]
    said = list(
        engine(rec, read_database=_reads("present")).rebuild(InstallOptions(server_dir=server_dir))
    )

    line = next(line for line in said if CHOSEN in line)
    assert f"the realm row reads '{SEEDED}'" in line, line


def test_a_coa_install_never_reads_or_applies_a_saved_name_after_an_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T808 x T812: the post-import step stands aside on a realm-type entry; the realm switch
    writes a CoA realm's name. Mutation: drop the `follows_realm_type` return in
    `_apply_realm_name()` and the saved name is read (and an UPDATE sent)."""
    from types import SimpleNamespace

    from yulon.catalog.catalog import load_catalog
    from yulon.catalog.families.azerothcore import AzerothCoreInstaller

    coa = load_catalog().get("wow-coa")
    server_dir = tmp_path / "coa"
    assert realm_name.record_name(server_dir, CHOSEN) == ""
    rec = Recorder(images=True)
    read: list[Path] = []
    monkeypatch.setattr(realm_name, "read_saved_name", lambda d: read.append(d) or CHOSEN)
    installer = AzerothCoreInstaller(
        coa,
        installers_root=resources.installers_dir(),
        import_probe=rec.probe,
        reset_unfinished=rec.reset,
        seams=rec.seams(),
    )

    said = list(installer._apply_realm_name(SimpleNamespace(server_dir=server_dir), "Done"))

    assert said == [] and read == [] and _name_statements(rec) == []
