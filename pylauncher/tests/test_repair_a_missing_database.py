"""Rebuild refuses a server whose database Docker no longer has, and Repair makes it again (T377).

`test_start_notices_a_missing_database.py` is the Start half. This is the
engine's: a Rebuild ends in a start, so it asks the same question before it
compiles anything, and the Repair the Server tab offers is the install's own
database stages run again -- start the database, import, start the server, wait
for it -- with nothing written to the install's record.

The machine is `support_native.Recorder`; the reading is the engine's
`read_database` seam, stated per test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support_native import ABSENT, ENTRY, IMPORTED, Recorder, engine, install
from tests.test_families_cmangos import engine as cm_engine
from yulon import database_presence
from yulon.catalog import native
from yulon.catalog.installer import InstallerError, InstallOptions
from yulon.database_presence import Reading


def _reads(presence: str) -> object:
    asked: list[Path] = []

    def read(entry: object, server_dir: Path) -> Reading:
        asked.append(server_dir)
        return Reading(presence)  # type: ignore[arg-type]

    read.asked = asked  # type: ignore[attr-defined]
    return read


def _finished(rec: Recorder, tmp_path: Path) -> Path:
    server_dir = tmp_path / "wow"
    install(rec, server_dir)
    rec.calls.clear()
    return server_dir


# -- the Rebuild ---------------------------------------------------------------------------


@pytest.mark.parametrize("presence", ["missing", "empty"])
def test_a_rebuild_refuses_a_missing_database_before_it_builds_anything(
    tmp_path: Path, presence: str
) -> None:
    rec = Recorder(images=True)
    server_dir = _finished(rec, tmp_path)
    with pytest.raises(InstallerError) as refused:
        list(
            engine(rec, read_database=_reads(presence)).rebuild(
                InstallOptions(server_dir=server_dir)
            )
        )
    assert database_presence.MISSING in str(refused.value)
    assert "build" not in rec.calls and "recreate" not in rec.calls, rec.calls


@pytest.mark.parametrize("presence", ["present", "unknown"])
def test_a_rebuild_goes_on_when_the_database_is_there_or_could_not_be_asked(
    tmp_path: Path, presence: str
) -> None:
    rec = Recorder(images=True)
    server_dir = _finished(rec, tmp_path)
    read = _reads(presence)
    list(engine(rec, read_database=read).rebuild(InstallOptions(server_dir=server_dir)))
    assert read.asked == [server_dir]  # type: ignore[attr-defined]
    assert "build" in rec.calls and "recreate" in rec.calls, rec.calls


# -- the Repair ----------------------------------------------------------------------------


@pytest.mark.parametrize("presence", ["missing", "empty"])
def test_repair_runs_the_install_s_database_stages_and_records_none_of_them(
    tmp_path: Path, presence: str
) -> None:
    rec = Recorder(images=True)
    server_dir = _finished(rec, tmp_path)
    record = (server_dir / native.STATE_FILE).read_bytes()
    # What the import's own probe reads on a new, empty database, and after the import.
    rec.db_started = False
    rec.probe_answers = [ABSENT, IMPORTED]
    said = list(
        engine(rec, read_database=_reads(presence)).repair_database(
            InstallOptions(server_dir=server_dir)
        )
    )
    stages = [line[4:] for line in said if line.startswith("--- ")]
    assert stages == ["client-data", "start-db", "import", "up", "ready"], said
    assert "one-shot:ac-db-import" in rec.calls and "start" in rec.calls, rec.calls
    assert "build" not in rec.calls and "recreate" not in rec.calls
    assert (server_dir / native.STATE_FILE).read_bytes() == record, "nothing is recorded"


@pytest.mark.parametrize("presence", ["present", "unknown"])
def test_repair_refuses_unless_docker_says_the_database_is_missing_or_empty(
    tmp_path: Path, presence: str
) -> None:
    """Only over nothing: a database that is there, or one nobody could ask, is not imported."""
    rec = Recorder(images=True)
    server_dir = _finished(rec, tmp_path)
    with pytest.raises(InstallerError):
        list(
            engine(rec, read_database=_reads(presence)).repair_database(
                InstallOptions(server_dir=server_dir)
            )
        )
    assert rec.calls == [], rec.calls


def test_a_cmangos_repair_has_no_client_data_step(tmp_path: Path) -> None:
    """Its map data is in the server folder, not in a volume a prune removes."""
    names = [stage.name for stage in cm_engine(Recorder()).repair_database_stages()]
    assert names == ["start-db", "import", "up", "ready"]
    assert all(not stage.recorded for stage in cm_engine(Recorder()).repair_database_stages())


def test_the_engine_asks_docker_by_default() -> None:
    """The real reading, not a stand-in, unless a test says otherwise."""
    assert native.Seams().read_database is database_presence.read
    assert ENTRY.install.native is not None


# -- the wiring ----------------------------------------------------------------------------


def test_every_game_s_tab_is_wired_the_repair_and_it_drives_that_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon import install_wiring
    from yulon.catalog.catalog import load_catalog

    rec = Recorder(images=True)
    server_dir = _finished(rec, tmp_path)
    rec.db_started = False
    rec.probe_answers = [ABSENT, IMPORTED]
    built = engine(rec, read_database=_reads("missing"))
    monkeypatch.setattr(install_wiring, "installer_for_app", lambda entry, **kw: built)
    for game in load_catalog().games:
        assert install_wiring.repair_database_for_app(game, server_dir) is not None, game.id
    repair = install_wiring.repair_database_for_app(ENTRY, server_dir)
    assert repair is not None
    said = list(repair(None))
    assert f"Repairing {ENTRY.name}'s database in {server_dir}" in said
    assert "start" in rec.calls
