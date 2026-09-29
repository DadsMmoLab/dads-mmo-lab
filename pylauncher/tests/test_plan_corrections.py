"""T129: a corrected install-plan step reaches a server whose import marker predates it.

`MarkerGate` reads any marker row as `imported` whatever its plan hash says, and
that rule is right (bug-checklist §36): re-importing over somebody's live world to
chase a catalog edit would be far worse than not. Its cost was that a corrected
or added SQL phase reached new installs only. The lead's decision (2026-09-27) is
T106's shape: detect it and OFFER it, never apply it silently.

Two things were missing, and this file is about both:

* **A record of WHICH version of each phase an install has.** The marker row
  holds one hash over the whole plan -- notes and model defaults included -- so
  it could say "something moved" and never "this phase moved". `write_marker()`
  now writes one row per phase beside it (`yulon_install_phase`: the phase's
  name and `SqlPhase.digest()`), and an install marked before that is read
  through `RELEASED_PHASE_DIGESTS`, the per-phase digests of the only plans any
  public release ever marked an install with (measured, see that table).
* **A route that applies only what is safe to apply again.** A phase is offered
  only when the catalog declares it `reapply_when_changed`; everything else that
  changed is named and withheld. The press re-reads at press time, applies the
  agreed phases that are still stale, records each one whose runs all landed,
  and leaves the marker alone.

The database here is sqlite, one file per schema, and the SQL the app writes is
EXECUTED against it: the marker script, the phase rows, the corrected
statements and the reads that come back. The one translation is the default
schema -- `mariadb -u root mangos` makes `mangos` the schema unqualified names
land in, and sqlite spells that `main`, so the fake drops the `mangos`.
qualifier on a run whose argv names `mangos`. What is unit-tested only: no
press was run on a box for this change.
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
import threading
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import BinaryIO

import pytest

from tests.support_native import Recorder
from tests.test_families_cmangos import entry_with_sql
from yulon import docker, resources
from yulon.catalog import native, released_plans
from yulon.catalog.catalog import SqlPhase, SqlPlan, load_catalog
from yulon.catalog.families import sqlplan
from yulon.catalog.families.cmangos import CmangosInstaller
from yulon.catalog.installer import InstallerError, InstallOptions

CATALOG = load_catalog()
SCHEMAS = ("mangos", "realmd", "characters", "logs")
SECRET = "tbc-0123456789abcdef"


class _Mariadb:
    """`sqlplan`'s two seams over sqlite files, one per schema, holding real tables.

    `exec_stdin` runs the script it is handed; `query` answers a statement in
    `--batch --skip-column-names` shape (tab-separated, one line per row). A
    script stops at its first failing statement with the ones before it kept,
    which is what the client does in batch mode. `refuse` names a substring whose
    script the client rejects before running it; `down` makes every call raise
    the way a stopped container does. A schema a script names that does not
    exist yet is created for it: this fake cannot run a plan's `create` step,
    and the one test that imports from nothing needs the schemas to appear.
    """

    def __init__(self, root: Path, schemas: Sequence[str] = SCHEMAS) -> None:
        self.root = root / "mariadb"
        self.root.mkdir(parents=True, exist_ok=True)
        self.schemas = list(schemas)
        for schema in self.schemas:
            sqlite3.connect(self.root / f"{schema}.db").close()
        self.scripts: list[tuple[str | None, str]] = []
        self.refuse: str | None = None
        self.down = False

    def _connect(self, schema: str | None) -> sqlite3.Connection:
        main = str(self.root / f"{schema}.db") if schema else ":memory:"
        con = sqlite3.connect(main)
        for other in self.schemas:
            if other != schema:
                con.execute(f"ATTACH DATABASE ? AS {other}", (str(self.root / f"{other}.db"),))
        return con

    @staticmethod
    def _default(text: str, schema: str | None) -> str:
        return text.replace(f"`{schema}`.", "") if schema else text

    def exec_stdin(
        self,
        container: str,
        argv: Sequence[str],
        source: BinaryIO,
        *,
        env: Mapping[str, str],
        wsl_distro: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        text = source.read().decode("utf-8")
        schema = argv[3] if len(argv) > 3 else None
        self.scripts.append((schema, text))
        if schema is not None and schema not in self.schemas:
            self.schemas.append(schema)
        if self.down:
            raise docker.DockerCommandError(f"No such container: {container}")
        if self.refuse is not None and self.refuse in text:
            return subprocess.CompletedProcess(
                list(argv), 1, "", "ERROR 1064 (42000) at line 1: You have an error in your SQL"
            )
        con = self._connect(schema)
        try:
            con.executescript(self._default(text, schema))
        except sqlite3.Error as exc:
            return subprocess.CompletedProcess(list(argv), 1, "", f"ERROR 1146 (42S02): {exc}")
        finally:
            con.close()
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    def query(
        self,
        container: str,
        client: str,
        password: str,
        schema: str | None,
        statement: str,
        *,
        wsl_distro: str | None = None,
    ) -> str:
        if self.down:
            raise docker.DockerCommandError(f"No such container: {container}")
        if statement == "SHOW DATABASES":
            return "\n".join(["information_schema", "mysql", *self.schemas]) + "\n"
        exists = re.fullmatch(
            r"SELECT COUNT\(\*\) FROM information_schema\.tables "
            r"WHERE table_schema='(\w+)' AND table_name='(\w+)'",
            statement,
        )
        con = self._connect(None if exists else schema)
        try:
            if exists:
                rows = con.execute(
                    f"SELECT COUNT(*) FROM {exists.group(1)}.sqlite_master "
                    "WHERE type='table' AND name=?",
                    (exists.group(2),),
                ).fetchall()
            else:
                rows = con.execute(self._default(statement, schema)).fetchall()
        finally:
            con.close()
        return "".join("\t".join(str(value) for value in row) + "\n" for row in rows)

    def tables(self, schema: str) -> set[str]:
        con = sqlite3.connect(self.root / f"{schema}.db")
        try:
            return {row[0] for row in con.execute("SELECT name FROM sqlite_master")}
        finally:
            con.close()

    def rows(self, schema: str, statement: str) -> list[tuple[object, ...]]:
        con = sqlite3.connect(self.root / f"{schema}.db")
        try:
            return con.execute(statement).fetchall()
        finally:
            con.close()


BASE_STEP = "CREATE TABLE IF NOT EXISTS base_rows (id INT)"
BASE_STEP_CORRECTED = "CREATE TABLE IF NOT EXISTS base_rows_corrected (id INT)"
HOTFIX_V1 = "CREATE TABLE IF NOT EXISTS hotfix_v1 (id INT)"
HOTFIX_V2 = "CREATE TABLE IF NOT EXISTS hotfix_v2 (id INT)"
ADDED_STEP = "CREATE TABLE IF NOT EXISTS added_later (id INT)"

BASE = SqlPhase(name="world base", into="mangos", statements=(BASE_STEP,))
HOTFIX = SqlPhase(name="hotfix", into="mangos", statements=(HOTFIX_V1,), reapply_when_changed=True)


def plan(*phases: SqlPhase) -> SqlPlan:
    return SqlPlan(create=SCHEMAS, phases=phases, marker_db="mangos")


OLD = plan(BASE, HOTFIX)
"""The plan an install was imported with."""

CORRECTED = plan(BASE, HOTFIX.model_copy(update={"statements": (HOTFIX_V2,)}))
"""A later app version: the re-appliable phase's statement corrected, nothing else."""


def schemas() -> dict[str, str]:
    return {name: name for name in SCHEMAS}


def gate(db: _Mariadb, the_plan: SqlPlan) -> sqlplan.MarkerGate:
    return sqlplan.MarkerGate(
        the_plan,
        container="tbc-db",
        client="mariadb",
        password=SECRET,
        schemas=schemas(),
        sql_query=db.query,
        exec_stdin=db.exec_stdin,
    )


def imported_with(db: _Mariadb, the_plan: SqlPlan, tmp_path: Path) -> None:
    """What a finished import leaves: every phase's SQL applied, then the marker written.

    Through `sqlplan.expand()`, `apply()` and `write_marker()` themselves, so the
    record is the one the import writes and not one this test spells.
    """
    runs = sqlplan.expand(the_plan, tmp_path, schemas(), {})
    list(
        sqlplan.apply(
            runs,
            container="tbc-db",
            client="mariadb",
            password=SECRET,
            exec_stdin=db.exec_stdin,
            sink=lambda line: None,
            cancel=None,
        )
    )
    sqlplan.write_marker(
        the_plan,
        landed=the_plan.phases,
        container="tbc-db",
        client="mariadb",
        password=SECRET,
        exec_stdin=db.exec_stdin,
    )


def marked_before_t129(db: _Mariadb, plan_hash: str) -> None:
    """The marker an app from before this change wrote: the row and nothing beside it.

    The script is the one `write_marker()` sent until T129, verbatim.
    """
    db.exec_stdin(
        "tbc-db",
        ["mariadb", "-u", "root", "mangos"],
        _bytes(
            "CREATE TABLE IF NOT EXISTS `mangos`.`yulon_install` "
            "(plan_hash CHAR(16) NOT NULL, finished_unix BIGINT NOT NULL);\n"
            "INSERT INTO `mangos`.`yulon_install` (plan_hash, finished_unix) "
            f"VALUES ('{plan_hash}', 1757000000);\n"
        ),
        env={"MYSQL_PWD": SECRET},
    )


def _bytes(text: str) -> BinaryIO:
    import io

    return io.BytesIO(text.encode("utf-8"))


# -- the phase's own identity --------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        {"into": "realmd"},
        {"statements": (HOTFIX_V2,)},
        {"sort": "name"},
    ],
)
def test_a_phase_digest_moves_with_what_the_phase_applies(change: dict[str, object]) -> None:
    """Anything that changes WHAT lands in the database is a new version of the phase.

    Catches a field dropped from the digest's list.
    """
    assert HOTFIX.model_copy(update=change).digest() != HOTFIX.digest()


def test_a_phase_digest_moves_with_its_files_gzip_and_per_schema_globs() -> None:
    files = SqlPhase(name="dumps", into="mangos", files=("a/*.sql",))
    assert files.model_copy(update={"files": ("b/*.sql",)}).digest() != files.digest()
    assert files.model_copy(update={"gzip": True}).digest() != files.digest()
    each = SqlPhase(name="core updates", into_each={"mangos": "u/mangos/*.sql"})
    changed = each.model_copy(update={"into_each": {"mangos": "u/mangos/*.sql", "logs": "u/*.sql"}})
    assert changed.digest() != each.digest()


@pytest.mark.parametrize(
    "change",
    [
        {"notes": ("a sentence about the phase",)},
        {"on_error": "warn"},
        {"reapply_when_changed": False},
        {"assert_update_level": False},
        {"same_columns": (("a_copy", "a_table"),)},
    ],
)
def test_a_phase_digest_does_not_move_with_what_only_describes_or_governs_it(
    change: dict[str, object],
) -> None:
    """A note, a policy or a flag edit is not a correction, and must offer nothing.

    The whole-plan hash moves with every one of these (and with any model field
    added later), which is exactly why it could not answer this question.

    Catches the digest written over `model_dump()`.
    """
    assert HOTFIX.model_copy(update=change).digest() == HOTFIX.digest()
    assert re.fullmatch(r"[0-9a-f]{16}", HOTFIX.digest())


def test_a_phase_cannot_be_both_rerun_on_every_press_and_offered_when_changed() -> None:
    """`rerun_on_marked` already applies the phase on every press; the second flag would be dead.

    Catches the validator deleted.
    """
    with pytest.raises(ValueError, match="rerun_on_marked"):
        SqlPhase(
            name="both",
            into="mangos",
            statements=("SELECT 1",),
            rerun_on_marked=True,
            reapply_when_changed=True,
        )


def test_the_only_phases_offered_again_are_the_ones_whose_text_proves_them_idempotent() -> None:
    """Enumerated off the whole catalog, so the flag stays a fact about the data.

    `spell_template hotfix` is six `ADD COLUMN IF NOT EXISTS` in one `ALTER` --
    applying it to a table that already has them changes nothing, which can be
    read off the JSON alone. Tortoise's `character_inventory_copy table` (T159)
    is one `CREATE TABLE IF NOT EXISTS`, which leaves a table that is there --
    rows and all -- as it was. Every other shipped phase is a dump (which drops and
    re-creates its tables), a chain of updates, a file in a clone this tree does
    not hold, or a statement that overwrites a value a person may have changed
    since (`UPDATE account SET expansion = 1`, the realm row).

    Catches the flag dropped, moved to another phase, or put on a dump.
    """
    flagged = {
        (entry.id, phase.name)
        for entry in CATALOG.games
        if entry.install.native is not None and entry.install.native.cmangos is not None
        for phase in entry.install.native.cmangos.sql.phases
        if phase.reapply_when_changed
    }
    assert flagged == {
        ("wow-tbc", "spell_template hotfix"),
        ("wow-vanilla", "spell_template hotfix"),
        ("wow-tortoise", "character_inventory_copy table"),
    }
    for game, name in flagged:
        entry = CATALOG.get(game)
        assert entry.install.native is not None and entry.install.native.cmangos is not None
        phase = next(p for p in entry.install.native.cmangos.sql.phases if p.name == name)
        assert not phase.files, "a file's idempotence cannot be read off the catalog"
        for statement in phase.statements:
            assert ";" not in statement, "one statement per entry, or the check reads half of it"
            if statement.startswith("CREATE TABLE IF NOT EXISTS "):
                continue
            clauses = re.split(
                r",\s*(?=ADD )", statement.removeprefix("ALTER TABLE spell_template ")
            )
            assert all(clause.startswith("ADD COLUMN IF NOT EXISTS ") for clause in clauses)


# -- what an install marked before this change is known to have ---------------

RELEASED = {
    "wow-tbc": "b64174fea797fad4",
    "wow-vanilla": "5b654233d1dd2279",
    "wow-tortoise": "6f0ae5810a40956a",
}
"""The plan hash every public release marked each game's installs with, measured 2026-09-27.

Computed with each tag's own code over its own catalog: v0.8.0-Public,
v0.8.4-Public, v0.8.65-Public, v0.8.7-Public and v0.8.90-Public all give these
for TBC and Vanilla, and every one from v0.8.4 on gives this for Tortoise. The
one other released hash, Tortoise's `8b60e764371f2293` in v0.8.0-Public, is the
retired core T30 replaced (three of its phases name directories the Penqle core
does not have) and is deliberately NOT in the table: an install on it reads as
`unknown` and is offered nothing.
"""


def test_the_release_table_covers_every_plan_a_public_release_marked_an_install_with() -> None:
    """The keys are the measured set, no more and no fewer.

    Catches a hash missing (those installs would read `unknown` forever) and the
    retired Tortoise core's hash added.
    """
    assert set(released_plans.RELEASED_PHASE_DIGESTS) == set(RELEASED.values())


EXPECTED_FOR_RELEASED_INSTALLS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "wow-tbc": ((), ("instance updates",)),
    "wow-vanilla": ((), ("instance updates",)),
    "wow-tortoise": (("character_inventory_copy table",), ()),
}
"""(offered, withheld) for an install any public release made, against the plan shipped now.

TBC and Vanilla offer nothing and withhold one step ADDED since (T141): the
DB repository's instance files, whose first file deletes every dungeon and raid
spawn over MyISAM tables that no transaction can put back. The owner's decision
of 2026-09-29 is new installs only, so those servers read `held` -- logged, no
banner -- and the dialog of any later offer names the step as not applied.
Tortoise's (T159) is a step added since as well, which makes the table the
core's weekly honor maintenance truncates, and every Tortoise server a release
installed is offered exactly that one step. This is the table to change --
deliberately, in the same change -- when a shipped phase is corrected or added.
"""


@pytest.mark.parametrize("game", sorted(RELEASED))
def test_what_an_install_from_a_public_release_is_offered_is_what_this_file_expects(
    game: str,
) -> None:
    """The release table read against the shipped plan, through the comparison the tab uses.

    Every list but Tortoise's offer and the withheld `instance updates` of TBC
    and Vanilla (T141) is empty, which also proves the table was generated
    from the plans it names: one digest over the wrong fields, or from
    the wrong plan, would put that phase in one of them. There is no skip: the day a
    shipped phase is corrected this fails, and the message says what to do.

    Catches a table entry that disagrees with its plan, and a correction to a
    shipped phase that nobody noticed reaches every released install.
    """
    entry = CATALOG.get(game)
    assert entry.install.native is not None and entry.install.native.cmangos is not None
    drift = sqlplan.phase_drift(
        entry.install.native.cmangos.sql, released_plans.RELEASED_PHASE_DIGESTS[RELEASED[game]]
    )
    assert (drift.offered, drift.withheld) == EXPECTED_FOR_RELEASED_INSTALLS[game], (
        f"{game}: installs from a public release will now be OFFERED {drift.offered} and have "
        f"{drift.withheld} WITHHELD. If that correction is intended, put these two tuples in "
        "EXPECTED_FOR_RELEASED_INSTALLS and say in the CHANGELOG what those players will see; "
        "an offered phase must be `reapply_when_changed` for a reason its text shows. If it is "
        "not intended, the edit changed what a shipped phase applies. Never edit "
        "released_plans.py to make this pass: it is the record of what those installs have."
    )


# -- the record: written with the marker, read back through the gate ----------


def test_the_marker_is_written_with_one_row_per_phase_naming_its_version(tmp_path: Path) -> None:
    """The record a later version compares against, read back through the real gate.

    Catches the phase rows dropped from `write_marker()`, and a row keyed or
    valued by anything but the phase's name and digest.
    """
    db = _Mariadb(tmp_path)
    imported_with(db, OLD, tmp_path)
    ledger = gate(db, OLD).phase_ledger()
    assert ledger is not None
    assert ledger.marker == OLD.plan_hash()
    assert ledger.recorded == {"world base": BASE.digest(), "hotfix": HOTFIX.digest()}
    assert ledger.assumed == {}
    assert gate(db, OLD).probe().state == "imported"


def test_the_marker_row_is_the_last_thing_the_script_writes(tmp_path: Path) -> None:
    """A marker with no phase rows beside it would read as an install from before T129.

    So the phase rows go first, and a script that fails on them leaves no marker
    -- `partial`, which the next press clears and imports again -- rather than a
    marker whose record is missing.

    Catches the INSERT moved ahead of the phase rows.
    """
    db = _Mariadb(tmp_path)
    db.refuse = None
    original = db.exec_stdin

    def fail_at_phase_rows(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        source = args[2]
        assert hasattr(source, "read")
        text = source.read().decode("utf-8")  # type: ignore[union-attr]
        broken = text.replace("REPLACE INTO", "REPLACE INTOO")
        return original(args[0], args[1], _bytes(broken), **kwargs)  # type: ignore[arg-type]

    with pytest.raises(InstallerError):
        sqlplan.write_marker(
            OLD,
            landed=OLD.phases,
            container="tbc-db",
            client="mariadb",
            password=SECRET,
            exec_stdin=fail_at_phase_rows,  # type: ignore[arg-type]
        )
    assert gate(db, OLD).probe().state == "partial"


def test_a_marker_from_a_released_plan_reads_as_that_plans_phases(tmp_path: Path) -> None:
    """An install marked before T129 has no phase rows; its plan hash says which plan it was.

    Catches the release table not consulted (the install would read `unknown`
    and never be offered a correction).
    """
    db = _Mariadb(tmp_path)
    marked_before_t129(db, RELEASED["wow-tbc"])
    ledger = gate(db, OLD).phase_ledger()
    assert ledger is not None
    assert ledger.recorded == {}
    assert ledger.assumed == released_plans.RELEASED_PHASE_DIGESTS[RELEASED["wow-tbc"]]


def test_a_marker_from_a_plan_nobody_released_knows_nothing(tmp_path: Path) -> None:
    """A development build's hash: no rows, no table entry, so no phase can be called stale.

    Catches `unknown` read as "every phase is missing", which would offer every
    re-appliable phase to every such install.
    """
    db = _Mariadb(tmp_path)
    marked_before_t129(db, "0123456789abcdef")
    ledger = gate(db, OLD).phase_ledger()
    assert ledger is not None and ledger.known == {}


def test_databases_with_no_marker_have_no_ledger(tmp_path: Path) -> None:
    """No marker is no finished import this app recorded; there is nothing to compare."""
    assert gate(_Mariadb(tmp_path), OLD).phase_ledger() is None


def test_a_ledger_that_cannot_be_read_raises_rather_than_reading_as_empty(tmp_path: Path) -> None:
    """ "Could not ask" is not "nothing recorded" -- the second offers every added phase."""
    db = _Mariadb(tmp_path)
    imported_with(db, OLD, tmp_path)
    db.down = True
    with pytest.raises(docker.DockerCommandError):
        gate(db, OLD).phase_ledger()


# -- what is stale, and which of it may be offered -----------------------------


def test_a_changed_reappliable_phase_is_offered_and_a_changed_dump_is_withheld() -> None:
    """The two answers a changed phase can get, and the flag is what decides between them.

    Catches the flag ignored in either direction.
    """
    known = {"world base": BASE.digest(), "hotfix": HOTFIX.digest()}
    later = plan(
        BASE.model_copy(update={"statements": (BASE_STEP_CORRECTED,)}),
        HOTFIX.model_copy(update={"statements": (HOTFIX_V2,)}),
    )
    drift = sqlplan.phase_drift(later, known)
    assert drift.offered == ("hotfix",)
    assert drift.withheld == ("world base",)


def test_an_added_phase_is_stale_and_an_unchanged_one_is_not() -> None:
    added = SqlPhase(
        name="added", into="mangos", statements=(ADDED_STEP,), reapply_when_changed=True
    )
    drift = sqlplan.phase_drift(
        plan(BASE, HOTFIX, added), {"world base": BASE.digest(), "hotfix": HOTFIX.digest()}
    )
    assert drift.offered == ("added",)
    assert drift.withheld == ()


def test_a_phase_run_on_every_press_is_left_to_its_own_route() -> None:
    """T11's phases already reach every install; this route neither offers nor withholds them."""
    every = SqlPhase(name="every", into="mangos", statements=("SELECT 1",), rerun_on_marked=True)
    drift = sqlplan.phase_drift(plan(BASE, every), {"world base": BASE.digest()})
    assert drift.offered == () and drift.withheld == ()


def test_a_removed_phase_asks_for_nothing() -> None:
    """A phase the plan no longer has is not something to apply."""
    drift = sqlplan.phase_drift(plan(BASE), {"world base": BASE.digest(), "gone": "0" * 16})
    assert drift.offered == () and drift.withheld == ()


# -- the press, through the real family engine and the real gate ---------------


def an_engine(the_plan: SqlPlan, db: _Mariadb, *, world: bool | None = False) -> CmangosInstaller:
    """A TBC engine over `the_plan`, whose databases are `db` and whose world reads `world`.

    Nothing between the engine and `db` is a double: `_gate()` builds the real
    `MarkerGate`, the stage spine runs `start-db` and `import`, and every script
    the press sends is executed.
    """
    rec = Recorder()
    rec.db_started = True
    return CmangosInstaller(
        entry_with_sql(the_plan),
        installers_root=resources.installers_dir(),
        seams=rec.seams(
            platform_id=lambda: "linux",
            exec_stdin=db.exec_stdin,
            sql_query=db.query,
            world_running=lambda container: world,
            db_running=lambda container: True,
        ),
    )


def folder(tmp_path: Path) -> InstallOptions:
    server_dir = tmp_path / "srv"
    server_dir.mkdir(exist_ok=True)
    return InstallOptions(server_dir=server_dir)


def pressed(
    engine: CmangosInstaller, tmp_path: Path, check: native.CorrectionCheck | None = None
) -> list[str]:
    """A press of what the engine offers now -- the dialog's check -- or of `check` when given."""
    options = folder(tmp_path)
    return list(engine.apply_corrections(check or engine.correction_check(options), options))


def forged(marker: str = "0123456789abcdef", *offered: str) -> native.CorrectionCheck:
    """A check no reading produced: what a press handed stale or made-up provenance gets."""
    names = offered or ("hotfix",)
    return native.CorrectionCheck(
        "stale", offered=names, marker=marker, baseline=tuple((name, None) for name in names)
    )


def marker_rows(db: _Mariadb) -> list[tuple[object, ...]]:
    return db.rows("mangos", "SELECT plan_hash FROM yulon_install")


def test_an_install_imported_before_a_correction_is_offered_it(tmp_path: Path) -> None:
    """The reading behind the banner: the corrected phase, and only it.

    Catches the check comparing whole-plan hashes (it would call the unchanged
    `world base` stale too), and a check that never reads the record.
    """
    db = _Mariadb(tmp_path)
    imported_with(db, OLD, tmp_path)
    check = an_engine(CORRECTED, db).correction_check(folder(tmp_path))
    assert check.state == "stale", check
    assert check.offered == ("hotfix",)
    assert check.withheld == ()


def test_the_install_the_import_just_marked_is_offered_nothing(tmp_path: Path) -> None:
    db = _Mariadb(tmp_path)
    imported_with(db, CORRECTED, tmp_path)
    assert an_engine(CORRECTED, db).correction_check(folder(tmp_path)).state == "current"


def test_the_press_applies_the_correction_records_it_and_the_offer_goes_away(
    tmp_path: Path,
) -> None:
    """One press: the corrected statement lands, its version is recorded, the marker stays.

    And the SECOND reading answers differently from the first, which is the
    only way to show the record was written and read back rather than assumed.

    Catches the record not written (offered forever), the whole plan re-run
    (`world base` would be sent again), and a new marker row.
    """
    db = _Mariadb(tmp_path)
    imported_with(db, OLD, tmp_path)
    before = marker_rows(db)
    sent_before = len(db.scripts)
    engine = an_engine(CORRECTED, db)
    said = pressed(engine, tmp_path)
    assert "hotfix_v2" in db.tables("mangos"), said
    sent = [text for _, text in db.scripts[sent_before:]]
    assert not [text for text in sent if BASE_STEP in text], "a phase that did not change ran"
    assert not [text for text in sent if "DROP" in text.upper()], sent
    assert marker_rows(db) == before, "the marker row moved"
    assert any("hotfix" in line and "recorded" in line for line in said), said
    assert engine.correction_check(folder(tmp_path)).state == "current"


def test_a_changed_phase_that_is_not_safe_to_apply_again_is_named_and_never_applied(
    tmp_path: Path,
) -> None:
    """Withheld, even when the press names it: the flag is re-read at press time, not trusted.

    Catches the press applying whatever it is handed, and the confirmation
    leaving the withheld phase out.
    """
    db = _Mariadb(tmp_path)
    imported_with(db, OLD, tmp_path)
    later = plan(
        BASE.model_copy(update={"statements": (BASE_STEP_CORRECTED,)}),
        HOTFIX.model_copy(update={"statements": (HOTFIX_V2,)}),
    )
    engine = an_engine(later, db)
    check = engine.correction_check(folder(tmp_path))
    assert (check.offered, check.withheld) == (("hotfix",), ("world base",))
    text = engine.correction_confirmation(check, folder(tmp_path))
    assert "world base" in text and "not applied" in text.lower(), text
    both = native.CorrectionCheck(
        "stale",
        offered=("hotfix", "world base"),
        marker=check.marker,
        baseline=(*check.baseline, ("world base", BASE.digest())),
    )
    said = pressed(engine, tmp_path, both)
    assert any("world base is not applied" in line for line in said), said
    assert "hotfix_v2" in db.tables("mangos")
    assert "base_rows_corrected" not in db.tables("mangos"), "a withheld phase was applied"
    assert engine.correction_check(folder(tmp_path)).withheld == ("world base",)


def test_the_confirmation_names_the_phase_its_steps_and_the_stopped_server(tmp_path: Path) -> None:
    db = _Mariadb(tmp_path)
    imported_with(db, OLD, tmp_path)
    engine = an_engine(CORRECTED, db)
    text = engine.correction_confirmation(
        engine.correction_check(folder(tmp_path)), folder(tmp_path)
    )
    assert "hotfix" in text
    assert "statement 1" in text, "the steps the press streams are not listed"
    assert "STOPS" in text, "the dialog does not say the press stops the world (T159)"
    assert "marker" in text


@pytest.mark.parametrize("world", [True, None])
def test_a_press_while_the_world_is_up_or_unreadable_sends_nothing(
    tmp_path: Path, world: bool | None
) -> None:
    """Owner answer 7, through the spine's own guard: nothing is written under a live world.

    Since T159 a world that reads as running is STOPPED by the press; this
    world goes on reading as running after the stop (the Recorder's stop does
    nothing), so the reading right before the import still refuses it.
    `test_tortoise_inventory_copy.py` drives a world the stop does take down.

    Catches the press built without `_refuse_writes_into_a_running_world()`.
    """
    db = _Mariadb(tmp_path)
    imported_with(db, OLD, tmp_path)
    sent_before = len(db.scripts)
    with pytest.raises(InstallerError, match="Nothing was applied"):
        pressed(an_engine(CORRECTED, db, world=world), tmp_path)
    assert db.scripts[sent_before:] == []
    assert "hotfix_v2" not in db.tables("mangos")


def test_databases_with_no_marker_are_offered_nothing_and_a_press_touches_nothing(
    tmp_path: Path,
) -> None:
    """No marker: `partial` or somebody's server, and this route reaches neither import arm.

    Catches the route falling into `stage_import()`, whose `partial` arm drops
    every schema the plan names.
    """
    db = _Mariadb(tmp_path)
    engine = an_engine(CORRECTED, db)
    assert engine.correction_check(folder(tmp_path)).state == "unmarked"
    with pytest.raises(InstallerError, match="Nothing was applied"):
        pressed(engine, tmp_path, forged())
    assert db.scripts == []


def test_databases_that_cannot_be_asked_are_offered_nothing(tmp_path: Path) -> None:
    db = _Mariadb(tmp_path)
    imported_with(db, OLD, tmp_path)
    db.down = True
    assert an_engine(CORRECTED, db).correction_check(folder(tmp_path)).state == "unreadable"


def test_an_install_from_a_released_plan_is_offered_nothing_today(tmp_path: Path) -> None:
    """Every released TBC install, read through the release table against the shipped plan.

    The measured fact behind the ticket: no install is stale on the day this
    ships, so the tab stays quiet for every one of them. Since T141 it is
    `held` rather than `current`: the instance files were added after every
    release, and the owner's decision (2026-09-29) keeps them for new installs,
    so the step is withheld -- logged, no banner, nothing to press.

    Catches the release table missing the hash (`unknown`) or disagreeing with
    the shipped plan (`stale`), and the instance step being offered to a
    server that already has players.
    """
    db = _Mariadb(tmp_path)
    marked_before_t129(db, RELEASED["wow-tbc"])
    rec = Recorder()
    rec.db_started = True
    engine = CmangosInstaller(
        CATALOG.get("wow-tbc"),
        installers_root=resources.installers_dir(),
        seams=rec.seams(platform_id=lambda: "linux", exec_stdin=db.exec_stdin, sql_query=db.query),
    )
    check = engine.correction_check(folder(tmp_path))
    assert (check.state, check.offered, check.withheld) == ("held", (), ("instance updates",))


def test_an_install_from_a_plan_nobody_released_is_offered_nothing(tmp_path: Path) -> None:
    db = _Mariadb(tmp_path)
    marked_before_t129(db, "0123456789abcdef")
    assert an_engine(CORRECTED, db).correction_check(folder(tmp_path)).state == "unknown"


def test_a_press_on_an_install_from_before_t129_keeps_what_the_release_table_said(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its first row must not erase the table's word for every other phase.

    The reader trusts rows over the table, so a press that wrote only the
    corrected phase's row would leave `world base` unknown -- and stale the day
    the table entry is not there to fill it in. The table entry is taken away
    after the press to show the rows now say it on their own.

    Catches `record_phases()` writing the applied phases only.
    """
    released = "feedfacefeedface"
    monkeypatch.setitem(
        released_plans.RELEASED_PHASE_DIGESTS,  # type: ignore[arg-type]
        released,
        {"world base": BASE.digest(), "hotfix": HOTFIX.digest()},
    )
    db = _Mariadb(tmp_path)
    marked_before_t129(db, released)
    engine = an_engine(CORRECTED, db)
    assert engine.correction_check(folder(tmp_path)).offered == ("hotfix",)
    pressed(engine, tmp_path)
    monkeypatch.delitem(released_plans.RELEASED_PHASE_DIGESTS, released)  # type: ignore[arg-type]
    assert engine.correction_check(folder(tmp_path)).state == "current"


def test_a_warn_phase_whose_file_failed_is_applied_but_not_recorded(tmp_path: Path) -> None:
    """The phase's own `on_error` is kept, and the record does not claim what did not land.

    `warn` lets the run continue past a refused step, as the import does. The
    record is what makes the offer go away, so a phase with a refused step is
    left unrecorded and offered again rather than marked as having arrived.

    Catches the record written for every phase the press started.
    """
    db = _Mariadb(tmp_path)
    imported_with(db, OLD, tmp_path)
    warned = HOTFIX.model_copy(
        update={"statements": (HOTFIX_V2, "BROKEN STATEMENT"), "on_error": "warn"}
    )
    engine = an_engine(plan(BASE, warned), db)
    said = pressed(engine, tmp_path)
    assert "hotfix_v2" in db.tables("mangos"), "warn stopped at the refused step"
    assert any("not recorded" in line for line in said), said
    assert engine.correction_check(folder(tmp_path)).offered == ("hotfix",)


def test_a_fail_phase_that_is_refused_stops_the_press_and_records_nothing(tmp_path: Path) -> None:
    db = _Mariadb(tmp_path)
    imported_with(db, OLD, tmp_path)
    failing = HOTFIX.model_copy(update={"statements": ("BROKEN STATEMENT",)})
    engine = an_engine(plan(BASE, failing), db)
    with pytest.raises(InstallerError, match="statement 1"):
        pressed(engine, tmp_path)
    assert engine.correction_check(folder(tmp_path)).offered == ("hotfix",)


def test_a_second_press_of_the_same_dialog_is_refused_and_sends_nothing(tmp_path: Path) -> None:
    """The press re-reads; the dialog's reading no longer describes these databases.

    Catches the press applying by name whatever the record says now.
    """
    db = _Mariadb(tmp_path)
    imported_with(db, OLD, tmp_path)
    engine = an_engine(CORRECTED, db)
    check = engine.correction_check(folder(tmp_path))
    pressed(engine, tmp_path, check)
    sent_before = len(db.scripts)
    with pytest.raises(InstallerError, match="changed since"):
        pressed(engine, tmp_path, check)
    assert db.scripts[sent_before:] == []


def test_a_game_whose_plan_declares_no_reappliable_phase_refuses_the_press(tmp_path: Path) -> None:
    """WotLK: nothing could ever be offered, so the press is refused before anything.

    Tortoise was here too until T159 gave its plan a step to offer.

    Catches the spine handing the press to a family that does not read it.
    """
    for game in ("wow-wotlk",):
        from yulon.catalog.installer import installer_for

        with pytest.raises(InstallerError, match="nothing for this to apply"):
            list(installer_for(CATALOG.get(game)).apply_corrections(forged(), folder(tmp_path)))


# -- the wiring ----------------------------------------------------------------


def test_the_games_whose_plans_mark_a_step_are_wired_the_control_and_no_others(
    tmp_path: Path,
) -> None:
    """Read off the catalog: TBC, Vanilla and (since T159) Tortoise, and a WSL distro never.

    Catches the route wired for every entry, the reader keyed to an id, and the
    distro test dropped.
    """
    from yulon.install_wiring import corrections_for_app
    from yulon.ui.controller_view import ControllerServices

    for game, offered in [
        ("wow-tbc", True),
        ("wow-vanilla", True),
        ("wow-tortoise", True),
        ("wow-wotlk", False),
    ]:
        entry = CATALOG.get(game)
        assert (corrections_for_app(entry, tmp_path / game) is not None) is offered, game
        services = ControllerServices.for_entry(entry, tmp_path / game)
        assert (services.corrections is not None) is offered, game
    assert corrections_for_app(CATALOG.get("wow-tbc"), tmp_path, wsl_distro="Ubuntu") is None


def test_the_wired_route_reads_and_presses_the_install_it_was_built_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route `for_entry()` builds reaches THIS folder's databases through the real engine.

    `installer_for_app` is bound to an engine over the fake database, and
    nothing else: the options the route hands it name the folder it was built
    for, and the press lands the correction there.
    """
    from yulon import install_wiring

    db = _Mariadb(tmp_path)
    imported_with(db, OLD, tmp_path)
    server_dir = folder(tmp_path).server_dir
    assert server_dir is not None
    monkeypatch.setattr(
        install_wiring, "installer_for_app", lambda entry, **kw: an_engine(CORRECTED, db)
    )
    route = install_wiring.corrections_for_app(CATALOG.get("wow-tbc"), server_dir)
    assert route is not None
    check = route.check()
    assert check.offered == ("hotfix",)
    assert str(server_dir) in route.confirmation(check)
    list(route.press(check, None))
    assert route.check().state == "current"


# -- the Server tab ------------------------------------------------------------


class _Route:
    """A `CorrectionRoute` double that records what the tab asked of it."""

    def __init__(self, state: str = "stale", *, angry: bool = False) -> None:
        self.state = state
        self.angry = angry
        self.checks = 0
        self.confirmed: list[native.CorrectionCheck] = []
        self.pressed: list[tuple[native.CorrectionCheck, object]] = []
        self.refuse_dialog: str | None = None

    def check(self) -> native.CorrectionCheck:
        self.checks += 1
        if self.angry:
            raise RuntimeError("the daemon is not there")
        if self.state == "stale":
            return native.CorrectionCheck("stale", offered=("spell_template hotfix",))
        return native.CorrectionCheck(self.state, why="said so")  # type: ignore[arg-type]

    def confirmation(self, check: native.CorrectionCheck) -> str:
        self.confirmed.append(check)
        if self.refuse_dialog is not None:
            raise InstallerError(self.refuse_dialog)
        return "apply spell_template hotfix?"

    def press(self, check: native.CorrectionCheck, cancel: threading.Event | None) -> Iterator[str]:
        self.pressed.append((check, cancel))
        yield "spell_template hotfix: applied and recorded."

    def route(self) -> native.CorrectionRoute:
        return native.CorrectionRoute(
            check=self.check, confirmation=self.confirmation, press=self.press
        )


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> object:
    from tests.test_controller_view import _Ps
    from yulon import runner
    from yulon.ui import controller_view as controller_view_module
    from yulon.ui.widgets.job import run_inline

    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


def _view(ps: object, tmp_path: Path, route: _Route | None) -> object:
    from tests.test_controller_view import _services
    from yulon.ui.controller_view import ControllerView

    services = _services(ps, tmp_path, [])  # type: ignore[arg-type]
    services.corrections = route.route() if route is not None else None
    return ControllerView(CATALOG.get("wow-wotlk"), services, status_poll_ms=0)


def _database(ps: object, view: object, up: bool) -> None:
    ps.names = "ac-database\n" if up else ""  # type: ignore[attr-defined]
    view.refresh_status()  # type: ignore[attr-defined]


def _answer(monkeypatch: pytest.MonkeyPatch, yes: bool) -> list[str]:
    from PySide6.QtWidgets import QMessageBox

    asked: list[str] = []

    def question(parent: object, title: str, text: str, *a: object, **k: object) -> int:
        asked.append(text)
        return int((QMessageBox.StandardButton.Yes if yes else QMessageBox.StandardButton.No).value)

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    return asked


def test_a_stale_install_shows_the_banner_once_the_database_is_up(
    qapp: object, ps: object, tmp_path: Path
) -> None:
    """Asked once per time the database comes up -- it is `docker exec`s -- and never at build.

    Catches the check taken on every poll, taken at tab build, and a banner
    drawn for a state that offers nothing.
    """
    route = _Route("stale")
    view = _view(ps, tmp_path, route)
    assert route.checks == 0, "asked before the database was up"
    assert view.corrections_banner.isHidden()  # type: ignore[attr-defined]
    _database(ps, view, up=True)
    _database(ps, view, up=True)
    assert route.checks == 1
    assert not view.corrections_banner.isHidden()  # type: ignore[attr-defined]
    assert view.corrections_banner_button.text() == native.CORRECTIONS_BUTTON_LABEL  # type: ignore[attr-defined]
    assert "spell_template hotfix" in view.corrections_banner_label.text()  # type: ignore[attr-defined]


@pytest.mark.parametrize("state", ["current", "held", "unknown", "unmarked", "unreadable"])
def test_anything_but_stale_gets_no_banner(
    qapp: object, ps: object, tmp_path: Path, state: str
) -> None:
    view = _view(ps, tmp_path, _Route(state))
    _database(ps, view, up=True)
    assert view.corrections_banner.isHidden()  # type: ignore[attr-defined]


def test_a_check_that_raises_leaves_no_banner(qapp: object, ps: object, tmp_path: Path) -> None:
    view = _view(ps, tmp_path, _Route(angry=True))
    _database(ps, view, up=True)
    assert view.corrections_banner.isHidden()  # type: ignore[attr-defined]


def test_a_tab_with_no_route_has_no_banner_and_a_harmless_press(
    qapp: object, ps: object, tmp_path: Path
) -> None:
    view = _view(ps, tmp_path, None)
    _database(ps, view, up=True)
    assert view.corrections_banner.isHidden()  # type: ignore[attr-defined]
    assert view.apply_database_corrections() is False  # type: ignore[attr-defined]


def test_the_banner_goes_with_the_database_and_is_asked_again_when_it_returns(
    qapp: object, ps: object, tmp_path: Path
) -> None:
    route = _Route("stale")
    view = _view(ps, tmp_path, route)
    _database(ps, view, up=True)
    _database(ps, view, up=False)
    assert view.corrections_banner.isHidden(), "the reading outlived the database"  # type: ignore[attr-defined]
    _database(ps, view, up=True)
    assert route.checks == 2
    assert not view.corrections_banner.isHidden()  # type: ignore[attr-defined]


def test_declining_the_confirmation_applies_nothing(
    qapp: object, ps: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dialog is the route's own, composed for the reading the banner showed.

    Catches the confirmation skipped, and a dialog composed without the check.
    """
    route = _Route("stale")
    view = _view(ps, tmp_path, route)
    _database(ps, view, up=True)
    asked = _answer(monkeypatch, yes=False)
    view.corrections_banner_button.click()  # type: ignore[attr-defined]
    assert asked == ["apply spell_template hotfix?"]
    assert [check.offered for check in route.confirmed] == [("spell_template hotfix",)]
    assert route.pressed == []


def test_yes_presses_the_offered_steps_into_the_panel_and_the_reading_is_taken_again(
    qapp: object, ps: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The press gets exactly the phases the dialog named, and a cancel the panel's Stop sets.

    Catches the press handed something else, run without a cancel event, and a
    banner that outlives the press that answered it.
    """
    from tests.conftest import pump_until

    route = _Route("stale")
    view = _view(ps, tmp_path, route)
    _database(ps, view, up=True)
    _answer(monkeypatch, yes=True)
    view.corrections_banner_button.click()  # type: ignore[attr-defined]
    pump_until(
        lambda: "applied and recorded" in view.rebuild_log.text(),  # type: ignore[attr-defined]
        "the corrections press's output reached the panel",
    )
    assert len(route.pressed) == 1
    agreed, cancel = route.pressed[0]
    assert agreed is route.confirmed[0], "the press was not bound to the reading the dialog showed"
    assert cancel is not None
    view._rebuild_finished(True, "")  # type: ignore[attr-defined]
    assert view.corrections_banner.isHidden()  # type: ignore[attr-defined]
    route.state = "current"
    _database(ps, view, up=True)
    assert route.checks == 2
    assert view.corrections_banner.isHidden()  # type: ignore[attr-defined]


def test_a_dialog_that_cannot_be_composed_says_why_and_starts_nothing(
    qapp: object, ps: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QMessageBox

    route = _Route("stale")
    route.refuse_dialog = "src/x/*.sql matched nothing in the clone"
    view = _view(ps, tmp_path, route)
    _database(ps, view, up=True)
    warned: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda parent, title, text, *a: warned.append(text))
    )
    asked = _answer(monkeypatch, yes=True)
    assert view.apply_database_corrections() is False  # type: ignore[attr-defined]
    assert warned == ["src/x/*.sql matched nothing in the clone"]
    assert asked == [] and route.pressed == []


def test_an_install_whose_only_changes_are_withheld_gets_no_offer(tmp_path: Path) -> None:
    """`held`: something changed, nothing may be applied -- so no banner, only a log line.

    Catches `held` folded into `stale`, which would put a banner with nothing to
    press on the tab for the life of the install.
    """
    db = _Mariadb(tmp_path)
    imported_with(db, OLD, tmp_path)
    later = plan(BASE.model_copy(update={"statements": (BASE_STEP_CORRECTED,)}), HOTFIX)
    check = an_engine(later, db).correction_check(folder(tmp_path))
    assert (check.state, check.offered, check.withheld) == ("held", (), ("world base",))
    assert "world base" in check.why


def test_the_adopt_dialog_says_a_corrected_step_will_not_be_offered_to_what_it_adopts(
    tmp_path: Path,
) -> None:
    """The adopt press writes the marker and no step record, and its dialog says what that costs.

    Catches the dialog left promising nothing about corrections, or claiming a
    record the press does not write.
    """
    engine = CmangosInstaller(CATALOG.get("wow-tbc"), installers_root=resources.installers_dir())
    text = native.adopt_confirmation(CATALOG.get("wow-tbc"), tmp_path, engine.marker_row())
    assert "This writes ONE row and nothing else." in text
    assert "never offered a corrected install-plan step" in text
    assert sqlplan.PHASE_TABLE not in text


# -- round 2 (Codex and the cold review) ---------------------------------------


def adoptable(the_plan: SqlPlan) -> SqlPlan:
    """`the_plan` as the adopt press accepts it: a player table, and a phase run on every press.

    The player table makes databases holding a character read `populated`; the
    `rerun_on_marked` phase is what the adopt press exists to unlock, and it is
    refused for a plan without one (T19). No shipped plan has one today, so the
    adopt route is offered nowhere yet -- which is why this is a fix to a route
    and not to anything a player has pressed.
    """
    from yulon.catalog.catalog import PlayerData

    every = SqlPhase(
        name="every press",
        into="mangos",
        statements=("CREATE TABLE IF NOT EXISTS every_press (id INT)",),
        rerun_on_marked=True,
    )
    return the_plan.model_copy(
        update={
            "phases": (*the_plan.phases, every),
            "player_data": (PlayerData(db="characters", table="characters"),),
        }
    )


def test_adopting_a_server_records_no_step_versions_so_nothing_is_claimed(tmp_path: Path) -> None:
    """Adoption runs no phase, so it may not say which version of any phase is there.

    The press writes its marker through the same writer the import does, and a
    record stamped with this app's phases would read `current` for every one of
    them -- a correction to one would never be offered. With no record, the
    install reads `unknown`: nothing is claimed and nothing is offered.

    Catches the adopt route writing the import's phase rows (Codex, T129 round 1).
    """
    db = _Mariadb(tmp_path)
    db.exec_stdin(
        "tbc-db",
        ["mariadb", "-u", "root", "characters"],
        _bytes("CREATE TABLE characters (guid INT); INSERT INTO characters VALUES (1);"),
        env={"MYSQL_PWD": SECRET},
    )
    the_plan = adoptable(CORRECTED)
    engine = an_engine(the_plan, db)
    assert engine.adopt_state(folder(tmp_path)).state == "populated"
    list(engine.adopt_as_imported(folder(tmp_path)))
    assert gate(db, the_plan).probe().state == "imported"
    assert "yulon_install_phase" not in db.tables("mangos")
    assert engine.correction_check(folder(tmp_path)).state == "unknown"


def test_a_press_on_an_install_whose_record_is_unknown_sends_nothing(tmp_path: Path) -> None:
    """Fail closed at the press, not only at the banner: no record means nothing is stale.

    Catches the press computing drift over an empty record, which reads every
    re-appliable phase as added and runs it (Codex, T129 round 1).
    """
    db = _Mariadb(tmp_path)
    marked_before_t129(db, "0123456789abcdef")
    sent_before = len(db.scripts)
    with pytest.raises(InstallerError, match="Nothing was applied"):
        pressed(an_engine(CORRECTED, db), tmp_path, forged())
    assert db.scripts[sent_before:] == []
    assert "hotfix_v2" not in db.tables("mangos")


def test_a_world_that_starts_after_the_first_reading_is_still_refused(tmp_path: Path) -> None:
    """The second reading, right before the import stage: T14's pattern.

    The first reading passes, the database is started, and a Start pressed in
    that window puts a live world behind the tables this writes.

    Catches the guard on the import stage deleted (cold review, T129 round 1).
    """
    db = _Mariadb(tmp_path)
    imported_with(db, OLD, tmp_path)
    answers = [False, True]
    rec = Recorder()
    rec.db_started = True
    engine = CmangosInstaller(
        entry_with_sql(CORRECTED),
        installers_root=resources.installers_dir(),
        seams=rec.seams(
            platform_id=lambda: "linux",
            exec_stdin=db.exec_stdin,
            sql_query=db.query,
            world_running=lambda container: answers.pop(0) if len(answers) > 1 else answers[0],
        ),
    )
    sent_before = len(db.scripts)
    check = engine.correction_check(folder(tmp_path))
    with pytest.raises(InstallerError, match="it stops the world server first") as caught:
        pressed(engine, tmp_path, check)
    # T159: "Press Stop" here would take the database down, and the banner with it.
    assert "Press Stop" not in str(caught.value)
    assert db.scripts[sent_before:] == []


def test_a_family_that_ignored_the_corrections_flag_could_still_not_drop_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The press's context keeps `updates_only`, so the import stage's table stays unreachable.

    A family that did not read `ctx.corrections` would fall through to
    `stage_import()`, whose `partial` arm drops every schema the plan names.
    `updates_only` is the older promise that stops it: that route refuses
    anything that does not read as a finished import.

    Catches `apply_corrections()` clearing `updates_only` (cold review, T129 round 1).
    """
    from dataclasses import replace

    original = CmangosInstaller._import

    def ignoring(self: CmangosInstaller, ctx: native.StageContext) -> Iterator[str]:
        return original(self, replace(ctx, corrections=None))

    monkeypatch.setattr(CmangosInstaller, "_import", ignoring)
    db = _Mariadb(tmp_path)
    runs = sqlplan.expand(OLD, tmp_path, schemas(), {})
    list(
        sqlplan.apply(
            runs,
            container="tbc-db",
            client="mariadb",
            password=SECRET,
            exec_stdin=db.exec_stdin,
            sink=lambda line: None,
            cancel=None,
        )
    )
    assert gate(db, OLD).probe().state == "partial"
    with pytest.raises(InstallerError):
        pressed(an_engine(CORRECTED, db), tmp_path, forged(OLD.plan_hash()))
    assert not [text for _, text in db.scripts if "DROP DATABASE" in text], db.scripts


def test_the_import_records_no_version_for_a_warn_phase_whose_step_was_refused(
    tmp_path: Path,
) -> None:
    """The install route records the way the press does: only what landed whole.

    A `warn` phase carries on past a refused step, and the import still finishes
    -- but a row naming that phase's version would say the step is there, and a
    later correction of it would never be offered.

    Catches `write_marker()` stamping every phase of the plan (cold review, T129
    round 1).
    """
    db = _Mariadb(tmp_path, schemas=())
    warned = HOTFIX.model_copy(update={"statements": ("BROKEN STATEMENT",), "on_error": "warn"})
    the_plan = SqlPlan(create=(), phases=(BASE, warned), marker_db="mangos")
    engine = an_engine(the_plan, db)
    from dataclasses import replace

    ctx = replace(
        engine._update_context(folder(tmp_path).server_dir, None),  # type: ignore[arg-type]
        updates_only=False,
    )
    said = list(engine._import(ctx))
    assert any("imported and marked complete" in line for line in said), said
    ledger = gate(db, the_plan).phase_ledger()
    assert ledger is not None
    assert ledger.recorded == {"world base": BASE.digest()}


@pytest.mark.parametrize("name", ["x" * 192, "it's", "tab\there"])
def test_a_phase_name_the_record_cannot_hold_is_refused_when_the_catalog_loads(name: str) -> None:
    """Not at the end of an import, where the marker write would fail the same way every time.

    The record's key is `VARCHAR(191)` and the name goes into `'...'` unescaped.
    A name that breaks either would fail `write_marker()` after the whole import
    had run, leave no marker, and the next press would import again, forever.

    Catches the length or character check left to the writer alone.
    """
    with pytest.raises(ValueError):
        SqlPhase(name=name, into="mangos", statements=("SELECT 1",))


@pytest.mark.parametrize("what", ["the record", "the marker"])
def test_a_press_whose_dialog_no_longer_describes_the_databases_sends_nothing(
    tmp_path: Path, what: str
) -> None:
    """Bound to the confirmed provenance: the marker and each offered phase's recorded version.

    Between the dialog and the press the databases can be imported again, or a
    second app can apply the step. The press then refuses whole rather than
    applying what the person agreed to over a record they were not shown.

    Catches the press re-deriving the offer without comparing it to the check
    it was handed (Codex, T129 round 1).
    """
    db = _Mariadb(tmp_path)
    imported_with(db, OLD, tmp_path)
    engine = an_engine(CORRECTED, db)
    check = engine.correction_check(folder(tmp_path))
    assert check.offered == ("hotfix",)
    if what == "the record":
        change = "UPDATE yulon_install_phase SET digest='aaaaaaaaaaaaaaaa' WHERE phase='hotfix'"
    else:
        change = "INSERT INTO yulon_install VALUES ('bbbbbbbbbbbbbbbb', 9999999999)"
    db.exec_stdin("tbc-db", ["mariadb", "-u", "root", "mangos"], _bytes(change), env={})
    assert engine.correction_check(folder(tmp_path)).offered == ("hotfix",), "still stale"
    sent_before = len(db.scripts)
    with pytest.raises(InstallerError, match="changed since"):
        pressed(engine, tmp_path, check)
    assert db.scripts[sent_before:] == []
    assert "hotfix_v2" not in db.tables("mangos")
