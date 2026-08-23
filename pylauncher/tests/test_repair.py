"""Tests for the WotLK import probe (`controller_wow_wotlk.repair`).

Both seams are faked, so nothing here needs a daemon or a database. What the
fakes model is the shape of the real answers: `SHOW DATABASES` as a tuple of
schema names, and `mysql --batch --skip-column-names` as tab-separated lines.
"""

from __future__ import annotations

from yulon.apply import ApplyError
from yulon.controller_wow_wotlk import repair
from yulon.controller_wow_wotlk.maintenance import CORE_DATABASES, MaintenanceError
from yulon.manifest import Db

AUTH, CHARACTERS, WORLD = CORE_DATABASES


class _Mysql:
    """`MysqlDocker`'s `databases()`, which is all the probe asks of it."""

    def __init__(self, *schemas: str, fails: str = "") -> None:
        self.schemas = schemas
        self.fails = fails
        self.asked = 0

    def databases(self) -> tuple[str, ...]:
        self.asked += 1
        if self.fails:
            raise MaintenanceError(self.fails)
        return ("information_schema", "mysql", *self.schemas)

    def dump_into(self, database: str, sink: object) -> None:  # pragma: no cover - unused
        raise AssertionError("the probe must never dump")

    def load_from(self, source: object) -> None:  # pragma: no cover - unused
        raise AssertionError("the probe must never load")


class _Sql:
    """`DockerSql.query()` over a table map plus a row count per key table."""

    def __init__(
        self,
        tables: dict[str, list[str]] | None = None,
        counts: dict[str, int] | None = None,
        *,
        fails: str = "",
    ) -> None:
        self.tables = tables or {}
        self.counts = counts or {}
        self.fails = fails
        self.asked: list[tuple[Db, str]] = []

    def query(self, db: Db, statement: str) -> str:
        self.asked.append((db, statement))
        if self.fails:
            raise ApplyError(self.fails)
        if "information_schema" in statement:
            return "".join(
                f"{schema}\t{table}\n" for schema, names in self.tables.items() for table in names
            )
        # The counting query: one column per key table that exists, in the
        # order `repair.KEY_TABLES` lists them.
        wanted = [
            self.counts.get(f"{schema}.{table}", 0)
            for schema, table in repair.KEY_TABLES.items()
            if f"`{schema}`.`{table}`" in statement
        ]
        return "\t".join(str(n) for n in wanted) + "\n"


def _full_world() -> list[str]:
    return [f"world_table_{n}" for n in range(1103)]


def test_a_populated_database_is_never_reported_as_repairable() -> None:
    """The answer that stops a re-import. It is about rows, not about completeness."""
    sql = _Sql(
        tables={AUTH: ["account"], CHARACTERS: ["characters"], WORLD: _full_world()},
        counts={f"{AUTH}.account": 651, f"{CHARACTERS}.characters": 37},
    )
    state = repair.import_state(sql, _Mysql(AUTH, CHARACTERS, WORLD))
    assert state.state == "populated"
    assert state.repairable is False
    assert "651" in state.detail and "37" in state.detail, state.detail


def test_player_data_outranks_a_visibly_unfinished_import() -> None:
    """A half-imported database that holds characters is still somebody's server.

    This is the ordering that matters: asked the other way round, an install
    whose world schema never finished would read as `partial`, offer the button,
    and lose the accounts somebody had already made.
    """
    sql = _Sql(
        tables={AUTH: ["account"], CHARACTERS: ["characters"], WORLD: []},
        counts={f"{AUTH}.account": 4},
    )
    state = repair.import_state(sql, _Mysql(AUTH, CHARACTERS))
    assert state.state == "populated", state


def test_an_empty_server_reads_as_absent() -> None:
    """No schemas at all: nothing was ever imported, and nothing can be lost."""
    state = repair.import_state(_Sql(), _Mysql())
    assert state.state == "absent"
    assert state.repairable is True
    assert AUTH in state.detail


def test_schemas_that_exist_but_hold_no_tables_also_read_as_absent() -> None:
    """`ac-db-import` creates the databases before it fills them, so this is a real state."""
    sql = _Sql(tables={AUTH: [], CHARACTERS: [], WORLD: []})
    state = repair.import_state(sql, _Mysql(AUTH, CHARACTERS, WORLD))
    assert state.state == "absent", state
    assert state.repairable is True


def test_an_import_that_stopped_part_way_reads_as_partial() -> None:
    """Some schemas filled and some not is the interrupted install this action exists for."""
    sql = _Sql(tables={AUTH: ["account"], CHARACTERS: [], WORLD: _full_world()})
    state = repair.import_state(sql, _Mysql(AUTH, CHARACTERS, WORLD))
    assert state.state == "partial", state
    assert state.repairable is True
    assert CHARACTERS in state.detail


def test_a_finished_import_with_nobody_on_it_yet_is_not_repairable() -> None:
    """Straight after an install there are no accounts, and there is nothing to repair either."""
    sql = _Sql(tables={AUTH: ["account"], CHARACTERS: ["characters"], WORLD: _full_world()})
    state = repair.import_state(sql, _Mysql(AUTH, CHARACTERS, WORLD))
    assert state.state == "imported", state
    assert state.repairable is False


def test_a_database_that_cannot_be_asked_is_not_reported_as_empty() -> None:
    """A stopped database container answers nothing, which is not the same as answering zero."""
    state = repair.import_state(_Sql(), _Mysql(fails="no such container: ac-database"))
    assert state.state == "unreadable"
    assert state.repairable is False
    assert "ac-database" in state.detail


def test_a_failed_table_listing_is_not_reported_as_an_empty_schema() -> None:
    sql = _Sql(fails="ERROR 2002 (HY000): Can't connect to local MySQL server")
    state = repair.import_state(sql, _Mysql(AUTH, CHARACTERS, WORLD))
    assert state.state == "unreadable", state


def test_a_count_that_cannot_be_read_is_treated_as_data_present() -> None:
    """The one case where guessing "empty" ends with the characters gone.

    The listing succeeds and the count query then fails, so the probe knows the
    tables are there and does not know what is in them. Reporting `partial`
    would offer to overwrite them.
    """
    sql = _Sql(tables={AUTH: ["account"], CHARACTERS: ["characters"], WORLD: []})
    real_query = sql.query

    def flaky(db: Db, statement: str) -> str:
        if "COUNT(*)" in statement:
            raise ApplyError("Lost connection to MySQL server during query")
        return real_query(db, statement)

    sql.query = flaky  # type: ignore[method-assign]
    state = repair.import_state(sql, _Mysql(AUTH, CHARACTERS, WORLD))
    assert state.state == "unreadable", state
    assert state.repairable is False


def test_the_probe_connects_through_a_schema_that_exists() -> None:
    """`DockerSql` puts the database in argv, so it cannot connect to one that is absent.

    On a server where only the world schema was created, routing the query
    through `acore_auth` — the fixed choice — would fail with "Unknown database"
    and the whole probe would read as unreadable.
    """
    sql = _Sql(tables={WORLD: _full_world()})
    repair.import_state(sql, _Mysql(WORLD))
    assert sql.asked, "nothing was queried at all"
    assert sql.asked[0][0] == "world", sql.asked[0]


def test_the_schema_listing_is_asked_for_once_not_once_per_schema() -> None:
    """`SHOW DATABASES` is a real `docker exec`, and it was being run three times.

    Written as a comprehension condition — `[n for n in CORE_DATABASES if n in
    mysql.databases()]` — the call is re-evaluated for every element, so the
    probe cost five execs while its own docstring, the poll guard that fires it,
    and `phase6-decisions.md` §5 all justified themselves against three
    (review, 2026-08-23).
    """
    mysql = _Mysql("acore_auth", "acore_world", "acore_characters")
    repair.import_state(_Sql({"acore_auth": ["account"]}, {"account": 0}), mysql)
    assert mysql.asked == 1, f"SHOW DATABASES ran {mysql.asked} times"
