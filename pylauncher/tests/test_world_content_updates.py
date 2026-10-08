"""An existing TBC or Vanilla server gets the world updates its `*-db` pin adds (T531).

Before T531 "Update the server to latest…" left `src/tbc-db` / `src/classic-db` on the
commit the server was INSTALLED at (`held_at_its_pin()` keeps every `*-db` source out
of the button's fetch), and its rebuild has no import stage, so a server installed at
tbc-db ba4755de never got 0035-0043 however often it was updated.

The design (ticket T531): with the old servers stopped and the new build not yet
started (`servers_down_work().forward()`), the `*-db` checkout is moved to its catalog
pin -- never to upstream's tip -- and each file of an `on_update: apply_new` phase that
the world database's file ledger (`sqlplan.FILE_TABLE`) does not hold is applied once,
in order. The ledger is seeded from the checkout BEFORE it moves, the first time, so
what the install applied is never applied again. A file the ledger holds with other
content is named and never re-run; a file a stopped press left `started` is never
retried. `report` phases only name what the move added or changed.

What is unit-tested only here: the live half is the ticket's proof (an install at
tbc-db ba4755de, updated, compared with a fresh install).
"""

from __future__ import annotations

import io
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import BinaryIO

import pytest

from tests.support_native import Recorder
from tests.test_families_cmangos import ENTRY as TBC
from tests.test_families_cmangos import engine as tbc_engine
from tests.test_families_cmangos import entry_with_sql
from tests.test_families_cmangos import install as tbc_install
from yulon import docker
from yulon.catalog import native
from yulon.catalog.catalog import CatalogEntry, SqlPhase, load_catalog
from yulon.catalog.families import sqlplan
from yulon.catalog.families.cmangos import CmangosInstaller
from yulon.catalog.installer import InstallerError, InstallOptions
from yulon.docker import AttachedRun

OLD = "a" * 40
NEW = "b" * 40


@pytest.fixture(autouse=True)
def _gated(monkeypatch: pytest.MonkeyPatch) -> None:
    """`test_families_cmangos.gated`: the engine's import gate is the Recorder's probe pair.

    An autouse fixture belongs to its module, so it is copied here (as
    `test_update_to_latest.py` does): without it the catch-up's "is this world a
    finished import?" would go to a real `MarkerGate`.
    """

    def gate(self: CmangosInstaller, ctx: native.StageContext) -> native.ImportGate:
        attached = getattr(self, "_test_gate", None)
        assert attached is not None, "build CMaNGOS engines with tbc_engine(rec)"
        return attached

    monkeypatch.setattr(CmangosInstaller, "_gate", gate, raising=True)


# -- the catalog field --------------------------------------------------------


def test_on_update_defaults_to_leave_and_is_not_part_of_what_a_phase_applies() -> None:
    """The flag changes the UPDATE route only: an import applies the same files either way.

    So it is outside `digest()`, and T129's correction offer does not read a newly
    flagged phase as a changed one on every install there is.
    """
    plain = SqlPhase(name="content updates", into="mangos", files=("src/x-db/Updates/*.sql",))
    flagged = plain.model_copy(update={"on_update": "apply_new"})
    assert plain.on_update == "leave"
    assert SqlPhase.model_validate(flagged.model_dump()).on_update == "apply_new"
    assert flagged.digest() == plain.digest()


@pytest.mark.parametrize(
    "fields",
    [
        {"statements": ("SELECT 1",)},
        {"into_each": {"mangos": "src/x-db/Updates/*.sql"}, "into": None, "files": ()},
        {"rerun_on_marked": True},
    ],
    ids=["statements", "into_each", "rerun_on_marked"],
)
def test_on_update_is_refused_where_the_ledger_could_not_key_it_or_would_run_it_twice(
    fields: dict[str, object],
) -> None:
    """A statement has no file to record; `into_each` writes more than the one schema the
    route checks; and a `rerun_on_marked` phase already runs on every Install press, so a
    second route applying its files is exactly the double run this design exists to stop."""
    base: dict[str, object] = {
        "name": "content updates",
        "into": "mangos",
        "files": ("src/x-db/Updates/*.sql",),
        "on_update": "apply_new",
    }
    base.update(fields)
    if "statements" in fields:
        base["files"] = ()
    with pytest.raises(ValueError, match="on_update"):
        SqlPhase.model_validate(base)


def _phases(game: str) -> dict[str, SqlPhase]:
    entry = load_catalog().get(game)
    assert entry.install.native is not None and entry.install.native.cmangos is not None
    return {phase.name: phase for phase in entry.install.native.cmangos.sql.phases}


@pytest.mark.parametrize("game", ["wow-tbc", "wow-vanilla"])
def test_the_shipped_tbc_and_vanilla_plans_bring_new_content_updates_and_name_the_rest(
    game: str,
) -> None:
    """The ticket's decisions, read off the data.

    Only the `*-db` repository's numbered content updates are applied. Instances stay
    new-installs-only (owner, 2026-09-29) and ACID is one whole-file load, so both are
    named; the bots' world SQL moves with every update and what an install has of it
    cannot be known, so it is named too. Nothing touches characters, realmd or logs.
    """
    phases = _phases(game)
    said = {name: phase.on_update for name, phase in phases.items() if phase.on_update != "leave"}
    assert said == {
        "content updates": "apply_new",
        "instance updates": "report",
        "ACID": "report",
        "core updates": "refuse_new",
        "playerbots world": "replace_changed",
    }, said


def test_every_shipped_phase_that_applies_on_update_writes_the_world_from_a_db_repository() -> None:
    """Enumerated over the whole catalog, through the same rule the route moves sources by.

    `apply_new` is only safe where the checkout never moved since the import applied it
    -- a `*-db` source (`native.held_at_its_pin`) -- and only into the world schema.
    """
    found = 0
    for entry in load_catalog().games:
        block = entry.install.native
        if block is None or block.cmangos is None:
            continue
        held = [s.dest for s in entry.emulator.sources if native.held_at_its_pin(s)]
        for phase in block.cmangos.sql.phases:
            if phase.on_update != "apply_new":
                continue
            found += 1
            assert phase.into == entry.databases.world, (entry.id, phase.name)
            for glob in phase.files:
                assert any(glob.startswith(f"{dest}/") for dest in held), (entry.id, glob)
    assert found == 2


# -- the ledger, pure ---------------------------------------------------------


def _run(rel: str, phase: SqlPhase, server_dir: Path) -> sqlplan.PhaseRun:
    return sqlplan.PhaseRun(phase, "mangos", server_dir / rel, None, False, rel)


CONTENT = SqlPhase(
    name="content updates",
    into="mangos",
    files=("src/tbc-db/Updates/*.sql",),
    on_error="warn",
    on_update="apply_new",
)


def test_the_ledger_reads_back_what_it_writes() -> None:
    """One spelling each way, so a row a press wrote is the row the next press reads."""
    rows = (
        sqlplan.FileRow("content updates", "src/tbc-db/Updates/0001_a.sql", "1" * 64, "seeded"),
        sqlplan.FileRow(
            "content updates", "src/tbc-db/Updates/0004_c.1,2.sql", "2" * 64, "applied"
        ),
    )
    script = sqlplan.file_rows_sql("mangos", rows, now=7)
    assert f"CREATE TABLE IF NOT EXISTS `mangos`.`{sqlplan.FILE_TABLE}`" in script
    assert "ENGINE=InnoDB" in script
    answer = "".join(f"{r.phase}\t{r.file}\t{r.sha256}\t{r.state}\n" for r in rows)
    assert sqlplan.parse_file_ledger(answer) == {(r.phase, r.file): r for r in rows}
    assert sqlplan.parse_file_ledger("") == {}


def test_a_ledger_answer_that_is_not_four_columns_is_refused_rather_than_guessed() -> None:
    """A row that cannot be read is a ledger that cannot be trusted: applying from it could
    run a file twice, so the press stops instead."""
    with pytest.raises(ValueError):
        sqlplan.parse_file_ledger("content updates\tsrc/x.sql\n")


def test_a_file_name_the_ledger_cannot_hold_is_refused_by_name() -> None:
    with pytest.raises(InstallerError, match="it's.sql"):
        sqlplan.file_rows_sql(
            "mangos", (sqlplan.FileRow("content updates", "src/it's.sql", "1" * 64, "seeded"),), 1
        )


def _lay(server_dir: Path, rel: str, body: str) -> Path:
    path = server_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_seeding_records_every_file_of_a_phase_the_ledger_has_never_seen(tmp_path: Path) -> None:
    """The first press records what the install applied: the files on disk BEFORE the move."""
    runs = [
        _run(rel, CONTENT, tmp_path)
        for rel in ("src/tbc-db/Updates/0001.sql", "src/tbc-db/Updates/0002.sql")
    ]
    for run in runs:
        _lay(tmp_path, run.rel, f"-- {run.rel}\n")
    seeded = sqlplan.seed_rows(runs, {})
    assert [(r.file, r.state) for r in seeded] == [(run.rel, "seeded") for run in runs]
    assert seeded[0].sha256 == sqlplan.file_digest(runs[0].path)  # type: ignore[arg-type]

    known = {(r.phase, r.file): r for r in seeded}
    assert sqlplan.seed_rows(runs, known) == (), "a phase with rows is never seeded again"


def test_only_files_the_ledger_does_not_hold_are_new_and_the_rest_are_named(
    tmp_path: Path,
) -> None:
    """New: absent from the ledger. Changed: held with other content, never re-run.
    Unsure (`started`) and failed: never run again, and they hold back every new file
    of their phase until the player deletes their row."""
    rels = [f"src/tbc-db/Updates/000{n}.sql" for n in range(1, 7)]
    runs = [_run(rel, CONTENT, tmp_path) for rel in rels]
    for run in runs:
        _lay(tmp_path, run.rel, f"-- {run.rel}\n")
    digest = {run.rel: sqlplan.file_digest(run.path) for run in runs}  # type: ignore[arg-type]
    ledger = {
        ("content updates", rels[0]): sqlplan.FileRow(
            "content updates", rels[0], digest[rels[0]], "seeded"
        ),
        ("content updates", rels[1]): sqlplan.FileRow(
            "content updates", rels[1], "9" * 64, "seeded"
        ),
        ("content updates", rels[2]): sqlplan.FileRow(
            "content updates", rels[2], digest[rels[2]], "started"
        ),
        ("content updates", rels[3]): sqlplan.FileRow(
            "content updates", rels[3], digest[rels[3]], "failed"
        ),
        ("content updates", rels[4]): sqlplan.FileRow(
            "content updates", rels[4], digest[rels[4]], "applied"
        ),
    }
    owed = sqlplan.pending_files(runs, ledger)
    assert owed.new == (), "nothing new runs past a file that did not land"
    assert owed.withheld == (rels[5],)
    assert owed.changed == (rels[1],)
    assert owed.unsure == (rels[2],)
    assert owed.failed == (rels[3],)

    settled = {key: row for key, row in ledger.items() if row.state not in ("started", "failed")}
    assert [run.rel for run in sqlplan.pending_files(runs, settled).new] == [
        rels[2],
        rels[3],
        rels[5],
    ]


# -- the update route, on a real TBC install ------------------------------------

ROW = re.compile(r"\('([^']*)', '([^']*)', '([0-9a-f]{64})', '([a-z]+)', [0-9]+\)")


class WorldDb:
    """The world schema's file ledger as the client would keep it, in front of a Recorder.

    Rows arrive in the scripts `sqlplan.record_world_files()` streams and leave through
    the SELECT `sqlplan.read_file_ledger()` asks, so a second press reads what the first
    one wrote -- which is the whole of "once". Every other script and question goes
    through to the Recorder untouched.
    """

    def __init__(self, rec: Recorder) -> None:
        self.rec = rec
        self.rows: dict[tuple[str, str], tuple[str, str]] = {}
        self.writes: list[str] = []
        self.rival: set[str] = set()
        self.table = False
        """Whether `CREATE TABLE IF NOT EXISTS … yulon_install_file` has run: until it has,
        a SELECT from the ledger fails the way the client does (cold review of T531)."""

    def exec_stdin(
        self,
        container: str,
        argv: Sequence[str],
        source: BinaryIO,
        *,
        env: Mapping[str, str],
        wsl_distro: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        data = source.read()
        text = data.decode("utf-8", errors="replace")
        if f"CREATE TABLE IF NOT EXISTS `mangos`.`{sqlplan.FILE_TABLE}`" in text:
            self.table = True
        if f"INSERT INTO `mangos`.`{sqlplan.FILE_TABLE}`" in text:
            # A claim: the key is (phase, file), so ANY row already there refuses it,
            # as the client would with ERROR 1062. `rival` is another press that
            # claimed the file between this press's ledger read and its claim.
            for phase, file, sha, _state in ROW.findall(text):
                if file in self.rival:
                    self.rows[(phase, file)] = (sha, "started")
                if (phase, file) in self.rows:
                    self.rec.calls.append(f"claim-refused:{file}")
                    return subprocess.CompletedProcess(
                        list(argv), 1, "", f"ERROR 1062 (23000) at line 2: Duplicate entry '{file}'"
                    )
        if sqlplan.FILE_TABLE in text:
            for phase, file, sha, state in ROW.findall(text):
                self.rows[(phase, file)] = (sha, state)
                self.writes.append(f"{state}:{file}")
        return self.rec.exec_stdin(
            container, argv, io.BytesIO(data), env=env, wsl_distro=wsl_distro
        )

    def sql_query(
        self,
        container: str,
        client: str,
        password: str,
        schema: str | None,
        statement: str,
        *,
        wsl_distro: str | None = None,
    ) -> str:
        if sqlplan.FILE_TABLE in statement:
            self.rec.calls.append("ledger-read")
            if not self.table:
                raise docker.DockerCommandError(
                    f"ERROR 1146 (42S02) at line 1: Table 'mangos.{sqlplan.FILE_TABLE}' "
                    "doesn't exist"
                )
            return "".join(
                f"{phase}\t{file}\t{sha}\t{state}\n"
                for (phase, file), (sha, state) in sorted(self.rows.items())
            )
        return self.rec.sql_query(
            container, client, password, schema, statement, wsl_distro=wsl_distro
        )

    def state(self, rel: str) -> str:
        return self.rows[("content updates", rel)][1]


TBC_DB = next(s for s in TBC.emulator.sources if native.held_at_its_pin(s))
PIN = TBC_DB.rev or ""
BOTS = next(s for s in TBC.emulator.sources if s.repo == "cmangos/playerbots")
U1 = "src/tbc-db/Updates/0001.sql"
U2 = "src/tbc-db/Updates/0002.sql"
U3 = "src/tbc-db/Updates/0003_new_at_the_pin.sql"
U4 = "src/tbc-db/Updates/0004_after_it.sql"
I3 = "src/tbc-db/Updates/Instances/0003_new_instance.sql"


def _overrides(db: WorldDb, world: list[bool | None]) -> dict[str, object]:
    return {
        "exec_stdin": db.exec_stdin,
        "sql_query": db.sql_query,
        "world_running": lambda container: world[0],
    }


def _installed(
    tmp_path: Path, *, entry: CatalogEntry = TBC
) -> tuple[Recorder, Path, WorldDb, list[bool | None]]:
    """A finished TBC install, tbc-db on `OLD`; its pin brings `U3` and an edit to `U1`."""
    rec = Recorder()
    db = WorldDb(rec)
    world: list[bool | None] = [False]
    server_dir = tmp_path / "tbc"
    tbc_install(rec, server_dir, tmp_path / "client", entry=entry, **_overrides(db, world))
    for source in entry.emulator.sources:
        rec.heads[server_dir / source.dest] = OLD
        rec.upstream[server_dir / source.dest] = NEW
    db_dest = server_dir / TBC_DB.dest

    def the_pin_brings(dest: Path) -> None:
        if dest == db_dest:
            _lay(server_dir, U3, f"-- {U3}\nSELECT 3;\n")
            _lay(server_dir, U4, f"-- {U4}\nSELECT 4;\n")
            _lay(server_dir, I3, f"-- {I3}\nSELECT 'instance';\n")
            _lay(server_dir, U1, f"-- {U1}\nSELECT 'edited upstream';\n")

    rec.on_clone = the_pin_brings
    rec.diffs[(db_dest, OLD, PIN)] = (
        ("M", "Updates/0001.sql"),
        ("A", "Updates/0003_new_at_the_pin.sql"),
        ("A", "Updates/0004_after_it.sql"),
        ("M", "Updates/Instances/0001.sql"),
        ("A", "Updates/Instances/0003_new_instance.sql"),
        ("M", "ACID/0001.sql"),
    )
    rec.clones.clear()
    rec.calls.clear()
    rec.sql_calls.clear()
    return rec, server_dir, db, world


def _press(
    rec: Recorder,
    server_dir: Path,
    db: WorldDb,
    world: list[bool | None],
    *,
    entry: CatalogEntry = TBC,
    to_pin: bool = False,
    said: list[str] | None = None,
) -> list[str]:
    """One press; its lines also go into `said` as they come, so a press that raises keeps them."""
    made: CmangosInstaller = tbc_engine(rec, entry=entry, **_overrides(db, world))
    lines = said if said is not None else []
    for line in made.update_to_latest(InstallOptions(server_dir=server_dir), to_pin=to_pin):
        lines.append(line)
    return lines


def _sent(rec: Recorder, rel: str) -> int:
    return rec.sql_calls.count(f"-- {rel}")


def test_an_update_brings_the_new_file_once_and_never_the_ones_the_install_applied(
    tmp_path: Path,
) -> None:
    rec, server_dir, db, world = _installed(tmp_path)
    lines = _press(rec, server_dir, db, world)

    assert rec.heads[server_dir / TBC_DB.dest] == PIN, "tbc-db goes to its catalog pin"
    assert not any(
        spec.url == TBC_DB.url and spec.rev is None for spec in rec.clones
    ), "a *-db source never follows the button to upstream's tip"
    assert _sent(rec, U3) == 1 and _sent(rec, U4) == 1
    assert rec.sql_calls.index(f"-- {U3}") < rec.sql_calls.index(f"-- {U4}"), "in file order"
    assert _sent(rec, U1) == 0 and _sent(rec, U2) == 0, "what the install applied is not re-run"
    assert db.state(U1) == "seeded" and db.state(U2) == "seeded"
    assert db.state(U3) == "applied"
    assert db.writes.index(f"started:{U3}") < db.writes.index(f"applied:{U3}")
    text = "\n".join(lines)
    assert U1 in text and "not run again" in text, "the edited file is named"
    for named in ("Updates/Instances/0001.sql", "ACID/0001.sql", I3):
        assert named in text, named
    assert _sent(rec, I3) == 0, "a report phase's new file is named, never applied"
    # With the servers stopped, after the compile and before the new build starts.
    calls = rec.calls
    assert calls.index("stop_servers") < calls.index("ledger-read") < calls.index("recreate")

    rec.sql_calls.clear()
    _press(rec, server_dir, db, world)
    assert _sent(rec, U3) == 0 and _sent(rec, U4) == 0, "a second press applies nothing twice"


def test_the_return_to_the_tested_pin_brings_them_too(tmp_path: Path) -> None:
    rec, server_dir, db, world = _installed(tmp_path)
    _press(rec, server_dir, db, world, to_pin=True)
    assert rec.heads[server_dir / TBC_DB.dest] == PIN
    assert _sent(rec, U3) == 1


def test_a_file_a_stopped_press_left_started_is_named_and_nothing_after_it_runs(
    tmp_path: Path,
) -> None:
    """`started` means Yu'lon cannot tell whether the file went in. It is never run again
    on Yu'lon's own word, and the files after it wait: the updates are a chain."""
    rec, server_dir, db, world = _installed(tmp_path)
    for rel in (U1, U2):
        db.rows[("content updates", rel)] = (sqlplan.file_digest(server_dir / rel), "seeded")
    db.rows[("content updates", U3)] = ("0" * 64, "started")
    db.table = True
    lines = _press(rec, server_dir, db, world)
    assert _sent(rec, U3) == 0 and _sent(rec, U4) == 0
    assert any(
        U3 in line and "cannot tell" in line and "Apply database corrections" in line
        for line in lines
    )
    assert any("wait behind it" in line for line in lines), lines
    assert ("content updates", U4) not in db.rows


def test_a_skipped_file_that_is_back_in_the_checkout_is_named_not_run_and_holds_nothing(
    tmp_path: Path,
) -> None:
    """T566: the player skipped U3 when its file was gone. The pin brings it back: it is
    said, not run, and the update behind it (U4) still goes in."""
    rec, server_dir, db, world = _installed(tmp_path)
    for rel in (U1, U2):
        db.rows[("content updates", rel)] = (sqlplan.file_digest(server_dir / rel), "seeded")
    db.rows[("content updates", U3)] = ("0" * 64, "skipped")
    db.table = True
    lines = _press(rec, server_dir, db, world)
    assert _sent(rec, U3) == 0, "a skipped file is never run"
    assert _sent(rec, U4) == 1 and db.state(U4) == "applied"
    assert db.state(U3) == "skipped"
    assert any(U3 in line and "skip" in line and "not run" in line for line in lines), lines
    assert not any("wait behind it" in line for line in lines), lines


def test_a_file_that_fails_stops_the_world_updates_there_and_is_never_retried_unasked(
    tmp_path: Path,
) -> None:
    """The lead's rule: a refused file stops the catch-up at that file, and the player is
    told which and how to run it again (the Apply database corrections press, T545)."""
    rec, server_dir, db, world = _installed(tmp_path)
    rec.failing_sql = "SELECT 3;"
    lines = _press(rec, server_dir, db, world)
    assert db.state(U3) == "failed"
    assert _sent(rec, U4) == 0, "nothing after the refused file runs"
    stop = [line for line in lines if "refused the world update" in line]
    assert (
        stop and U3 in stop[0] and "Apply database corrections" in stop[0] and "fresh" in stop[0]
    ), lines
    assert "recreate" in rec.calls, "the new build still starts"

    rec.failing_sql = ""
    rec.sql_calls.clear()
    lines = _press(rec, server_dir, db, world)
    assert _sent(rec, U3) == 0 and _sent(rec, U4) == 0, "not without the player's say-so"
    assert any(U3 in line and "refused" in line for line in lines), lines

    # The say-so: the row deleted, the next update tries it and what follows it.
    del db.rows[("content updates", U3)]
    rec.sql_calls.clear()
    _press(rec, server_dir, db, world)
    assert _sent(rec, U3) == 1 and _sent(rec, U4) == 1
    assert db.state(U3) == "applied" and db.state(U4) == "applied"


def test_a_world_that_reads_running_in_the_window_stops_the_press_before_anything_is_written(
    tmp_path: Path,
) -> None:
    rec, server_dir, db, world = _installed(tmp_path)
    world[0] = None
    with pytest.raises(InstallerError):
        _press(rec, server_dir, db, world)
    assert _sent(rec, U3) == 0
    assert db.rows == {}, "nothing seeded, nothing recorded"
    assert rec.heads[server_dir / TBC_DB.dest] == OLD, "the world-database checkout did not move"


def test_a_world_that_does_not_read_as_imported_gets_nothing_and_its_checkout_stays(
    tmp_path: Path,
) -> None:
    rec, server_dir, db, world = _installed(tmp_path)
    rec.probe_answers = [docker.ImportState("partial", "half a world")]
    lines = _press(rec, server_dir, db, world)
    assert _sent(rec, U3) == 0 and db.rows == {}
    assert rec.heads[server_dir / TBC_DB.dest] == OLD
    assert any("does not read as a finished import" in line for line in lines), lines


def test_a_rolled_back_update_keeps_what_went_in_and_the_next_press_does_not_repeat_it(
    tmp_path: Path,
) -> None:
    """The world is not copied (owner, 2026-10-04): what was applied stays, and is recorded,
    and the build from before runs with it. The ledger is what keeps the next press honest."""
    rec, server_dir, db, world = _installed(tmp_path)
    rec.ready = False
    said: list[str] = []
    with pytest.raises(InstallerError):
        _press(rec, server_dir, db, world, said=said)
    assert _sent(rec, U3) == 1 and db.state(U3) == "applied"
    assert any("stay in" in line and "build from before" in line for line in said), said
    assert rec.heads[server_dir / TBC_DB.dest] == PIN, "the checkout stays with what went in"
    rec.ready = True
    rec.sql_calls.clear()
    _press(rec, server_dir, db, world)
    assert _sent(rec, U3) == 0


def _with_on_update(phase_name: str, **fields: object) -> CatalogEntry:
    assert TBC.install.native is not None and TBC.install.native.cmangos is not None
    plan = TBC.install.native.cmangos.sql
    phases = tuple(
        phase.model_copy(update=fields) if phase.name == phase_name else phase
        for phase in plan.phases
    )
    return entry_with_sql(plan.model_copy(update={"phases": phases}))


@pytest.mark.parametrize(
    ("phase_name", "fields"),
    [
        ("playerbots world", {"on_update": "apply_new"}),
        ("content updates", {"into": "characters"}),
    ],
    ids=["outside-a-db-repository", "into-the-characters"],
)
def test_a_phase_the_route_cannot_apply_safely_refuses_before_the_compile(
    tmp_path: Path, phase_name: str, fields: dict[str, object]
) -> None:
    entry = _with_on_update(phase_name, **fields)
    rec, server_dir, db, world = _installed(tmp_path, entry=entry)
    with pytest.raises(InstallerError, match="catalog error"):
        _press(rec, server_dir, db, world, entry=entry)
    assert "build" not in rec.calls
    assert {rec.heads[server_dir / s.dest] for s in entry.emulator.sources} == {OLD}


def test_a_rebuild_that_fails_to_compile_applies_nothing_and_the_next_press_still_owes_it(
    tmp_path: Path,
) -> None:
    rec, server_dir, db, world = _installed(tmp_path)
    rec.build_result = AttachedRun(2, ("error: no",))
    with pytest.raises(InstallerError):
        _press(rec, server_dir, db, world)
    assert _sent(rec, U3) == 0
    rec.build_result = AttachedRun(0, ("built",))
    _press(rec, server_dir, db, world)
    assert _sent(rec, U3) == 1


@pytest.mark.parametrize("game", ["wow-tbc", "wow-vanilla", "wow-tortoise", "wow-wotlk"])
def test_both_confirmations_say_the_world_content_fixes_go_in_exactly_where_they_do(
    game: str,
) -> None:
    """Read off the catalog: the two games whose plan brings new world updates say so in the
    update's question and in the return's, with what a rollback does not undo."""
    entry = load_catalog().get(game)
    brings = game in ("wow-tbc", "wow-vanilla")
    for text in (
        native.update_to_latest_confirmation(entry, Path("/srv"), "x/y"),
        native.return_to_pin_confirmation(entry, Path("/srv"), "x/y"),
    ):
        said = "world content fixes" in text and "stay applied" in text
        assert said is brings, (game, text)


@pytest.mark.parametrize("how", ["edits", "commits"])
def test_a_world_database_checkout_with_the_players_own_work_is_refused_before_the_compile(
    tmp_path: Path, how: str
) -> None:
    """Codex on T531: the catch-up moves the `*-db` checkout, so it gets the route's own
    refusals first -- a file the player edited there, or a commit of theirs, is never what
    the move throws away. Refused before anything is built, every moved source back."""
    rec, server_dir, db, world = _installed(tmp_path)
    db_dest = server_dir / TBC_DB.dest
    if how == "edits":
        rec.edits[db_dest] = ("Updates/0001.sql",)
    else:
        rec.diverged.add(db_dest)
    with pytest.raises(InstallerError, match="tbc-db"):
        _press(rec, server_dir, db, world)
    assert "build" not in rec.calls
    assert rec.heads[db_dest] == OLD and _sent(rec, U3) == 0 and db.rows == {}
    assert {rec.heads[server_dir / s.dest] for s in TBC.emulator.sources} == {OLD}


def test_two_presses_cannot_both_claim_a_file_so_it_never_runs_twice(tmp_path: Path) -> None:
    """Codex on T531: the `started` row is a plain INSERT on (phase, file), so a second
    press (another Yu'lon, the CLI) that read the same ledger loses the claim and stops
    before it runs the file, instead of running it a second time."""
    rec, server_dir, db, world = _installed(tmp_path)
    db.rival = {U3}
    with pytest.raises(InstallerError, match="claiming a world update"):
        _press(rec, server_dir, db, world)
    assert f"claim-refused:{U3}" in rec.calls
    assert _sent(rec, U3) == 0 and _sent(rec, U4) == 0


@pytest.mark.parametrize(
    "body",
    [
        "USE characters;\nDELETE FROM character_inventory;\n",
        "UPDATE `characters`.`characters` SET money = 0;\n",
        "DELETE FROM realmd.account WHERE id = 1;\n",
    ],
    ids=["use", "backticked", "bare"],
)
def test_a_world_update_that_reaches_characters_or_accounts_is_never_applied(
    tmp_path: Path, body: str
) -> None:
    """Codex on T531: `into` is only the client's default schema. A file that names
    another schema is named and not run, nothing after it runs, and nothing is claimed."""
    rec, server_dir, db, world = _installed(tmp_path)
    db_dest = server_dir / TBC_DB.dest

    def the_pin_brings(dest: Path) -> None:
        if dest == db_dest:
            _lay(server_dir, U3, f"-- {U3}\n{body}")
            _lay(server_dir, U4, f"-- {U4}\nSELECT 4;\n")

    rec.on_clone = the_pin_brings
    lines = _press(rec, server_dir, db, world)
    assert _sent(rec, U3) == 0 and _sent(rec, U4) == 0
    assert ("content updates", U3) not in db.rows
    assert any(U3 in line and "reaches outside mangos" in line for line in lines), lines


def test_the_schema_scan_flags_what_could_reach_another_schema_and_not_a_table_column(
    tmp_path: Path,
) -> None:
    """Raw text, comments included (MySQL runs `/*! */`); `table.column` is not a schema."""
    others = {"characters", "realmd", "logs"}

    def scan(text: str) -> tuple[str, ...]:
        path = _lay(tmp_path, "x.sql", text)
        return sqlplan.foreign_schemas(path, others)

    assert scan("UPDATE gameobject SET a=0 WHERE gameobject.id=gameobject_template.entry;\n") == ()
    assert scan("UPDATE logs.logs_x SET a = 1;\n") == ("logs",)
    assert scan("/*!50000 INSERT INTO characters.foo VALUES (1) */;\n") == (
        "characters",
        "an executable comment",
    )
    assert scan("SELECT 1; use realmd;\n") == ("USE",)
    assert scan("DELETE FROM mysql.user;\n") == ("mysql",)
    assert scan("INSERT INTO t VALUES ('#'); UPDATE `characters`.c SET x=1;\n") == ("characters",)


def test_a_file_upstream_renamed_is_recorded_under_its_new_name_and_never_run_again(
    tmp_path: Path,
) -> None:
    """Codex on T531: the ledger keys a path, so a renamed file would look new. Its bytes
    give it away: a new path holding bytes the ledger already has is not run."""
    rec, server_dir, db, world = _installed(tmp_path)
    db_dest = server_dir / TBC_DB.dest
    renamed = "src/tbc-db/Updates/0002_renamed_upstream.sql"

    def the_pin_renames(dest: Path) -> None:
        if dest == db_dest:
            old = server_dir / U2
            _lay(server_dir, renamed, old.read_text(encoding="utf-8"))
            old.unlink()

    rec.on_clone = the_pin_renames
    lines = _press(rec, server_dir, db, world)
    assert rec.sql_calls.count(f"-- {U2}") == 0, "the renamed file kept U2's bytes, line and all"
    assert db.state(renamed) == "seeded"
    assert any(renamed in line and "another name" in line for line in lines), lines


def test_a_copy_beside_its_original_is_new_and_a_gone_stuck_file_is_still_named(
    tmp_path: Path,
) -> None:
    """Codex round 5: a move needs the old path GONE (a copy beside it runs), and a
    `failed` row whose file upstream removed is still named as what holds the phase."""
    rels = [f"src/tbc-db/Updates/000{n}.sql" for n in range(1, 4)]
    runs = [_run(rel, CONTENT, tmp_path) for rel in rels]
    for run in runs[:2]:
        _lay(tmp_path, run.rel, "-- same bytes\n")
    _lay(tmp_path, rels[2], "-- other\n")
    same = sqlplan.file_digest(tmp_path / rels[0])
    ledger = {
        ("content updates", rels[0]): sqlplan.FileRow("content updates", rels[0], same, "seeded")
    }
    owed = sqlplan.pending_files(runs, ledger)
    assert [run.rel for run in owed.new] == [rels[1], rels[2]] and owed.moved == ()

    gone = "src/tbc-db/Updates/0000_removed.sql"
    ledger[("content updates", gone)] = sqlplan.FileRow("content updates", gone, "1" * 64, "failed")
    owed = sqlplan.pending_files(runs, ledger)
    assert owed.failed == (gone,) and owed.new == ()


def test_a_whole_database_statement_is_never_applied(tmp_path: Path) -> None:
    path = _lay(tmp_path, "x.sql", "DROP DATABASE analytics;\n")
    assert sqlplan.foreign_schemas(path, set()) == ("a whole-database statement",)


def test_the_first_press_on_a_server_with_no_ledger_makes_the_table_before_reading_it(
    tmp_path: Path,
) -> None:
    """Cold review of T531: every existing server has no ledger table, and a SELECT from a
    missing table fails. The `CREATE TABLE IF NOT EXISTS` must go first."""
    rec, server_dir, db, world = _installed(tmp_path)
    assert db.table is False
    _press(rec, server_dir, db, world)
    assert db.table is True and db.state(U3) == "applied"


def test_a_started_row_whose_file_is_gone_is_named_and_holds_the_phase(tmp_path: Path) -> None:
    run = _run("src/tbc-db/Updates/0001.sql", CONTENT, tmp_path)
    _lay(tmp_path, run.rel, "-- 1\n")
    gone = "src/tbc-db/Updates/0000_removed.sql"
    ledger = {
        ("content updates", gone): sqlplan.FileRow("content updates", gone, "1" * 64, "started")
    }
    owed = sqlplan.pending_files([run], ledger)
    assert owed.unsure == (gone,) and owed.new == () and owed.withheld == (run.rel,)


@pytest.mark.parametrize(
    "text",
    [
        "UPDATE quest_template SET Details='Bring me ten logs. Then return.' WHERE entry=1;\n",
        'UPDATE quest_template SET Details="Two characters. Both." WHERE entry=2;\n',
        "UPDATE creature_template SET SubName='realmd.example' WHERE entry=3;\n",
        "INSERT INTO t VALUES ('use realmd;');\n",
        "UPDATE t SET text='/*! example' WHERE a=1;\n-- /*! in a comment\n",
    ],
    ids=["prose", "double-quoted", "dotted-in-string", "use-in-string", "exec-marker-in-text"],
)
def test_quest_text_and_other_strings_never_trip_the_schema_scan(tmp_path: Path, text: str) -> None:
    """Cold review of T531, SHOULD 2: a refused file blocks every later update, so a false
    alarm from prose in a string literal is not cheap. Strings are not SQL."""
    path = _lay(tmp_path, "x.sql", text)
    assert sqlplan.foreign_schemas(path, {"characters", "realmd", "logs"}) == ()


@pytest.mark.parametrize(
    ("text", "reached"),
    [
        ("-- don't change this\nUPDATE characters.foo SET a='x';\n", ("characters",)),
        ("# it's fine\nDELETE FROM realmd.account;\n", ("realmd",)),
        ("UPDATE/* reason */characters.foo SET a=1;\n", ("characters",)),
        ("INSERT INTO/* reason */mysql.user VALUES (1);\n", ("mysql",)),
        ("UPDATE t SET a='-- not a comment'; UPDATE logs.x SET b=1;\n", ("logs",)),
        ("SET @v=@v--1; UPDATE characters.foo SET a=1;\n", ("characters",)),
        ("UPDATE characters . quest SET a=1;\n", ("characters",)),
        ("UPDATE `realmd` .`account` SET a=1;\n", ("realmd",)),
        ("/*M!100100 UPDATE characters.foo SET a=1 */;\n", ("characters", "an executable comment")),
        (
            'SET sql_mode=\'ANSI_QUOTES\';\nUPDATE "characters"."foo" SET a=1;\n',
            ("characters", "a change of sql_mode"),
        ),
        ('UPDATE "characters"."foo" SET a=1;\n', ("characters",)),
        ("UPDATE`characters`.`foo` SET a=1;\n", ("characters",)),
        ("UPDATE t SET x='a\\'; UPDATE characters.foo SET x=1;\n", ("characters",)),
    ],
    ids=[
        "apostrophe-in-dash-comment",
        "apostrophe-in-hash-comment",
        "comment-before-name",
        "comment-after-into",
        "dashes-in-string",
        "minus-minus-is-not-a-comment",
        "spaced-dot",
        "spaced-backticked-dot",
        "mariadb-executable-comment",
        "ansi-quotes",
        "ansi-quotes-set-elsewhere",
        "backtick-glued-to-keyword",
        "no-backslash-escapes",
    ],
)
def test_comments_are_separators_and_never_hide_a_statement(
    tmp_path: Path, text: str, reached: tuple[str, ...]
) -> None:
    """Codex on T531's string stripping: an apostrophe in a comment must not open a
    string that swallows the next statement, and a comment between a keyword and a
    schema name is a separator to MySQL, so it must be one to the scan."""
    path = _lay(tmp_path, "x.sql", text)
    assert sqlplan.foreign_schemas(path, {"characters", "realmd", "logs"}) == reached
