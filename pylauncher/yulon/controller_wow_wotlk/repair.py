"""Does this WotLK install's database look imported? (checklist 6.5, "repair / re-import")

One job: answer `docker.ImportProbe` for an AzerothCore install. `docker.py`
runs the one-shot import and refuses on the answer; it must not know that the
schemas are called `acore_*` or that an account is a row in `account`
(style-guide §3), so those facts live here and nowhere else.

**What can be proved, and what cannot.** Three questions are asked, in this
order, and only the first two have certain answers:

1. *Do the schemas exist?* `SHOW DATABASES`, through `maintenance.DockerMysql`
   — the only seam here that can talk to the server without first naming a
   schema to connect to, which is exactly the case an unimported install is in.
   `apply.DockerSql` puts the database in argv (`mysql -uroot acore_auth`), so
   against a never-imported server every one of its calls fails with
   "Unknown database".
2. *Is there player data?* `acore_auth.account` and `acore_characters.characters`.
   This is the question the refusal is built on, because it is the one about
   something that cannot be recreated. It is asked whenever those tables exist,
   *before* any judgement about completeness — a half-imported database that
   holds characters is still somebody's server. What it cannot tell is *who*
   made the rows: the import applies every module's `data/sql/db-auth` and
   `db-characters` updates too, and a live import on yulon-ubuntu (2026-08-23)
   left 400 accounts and 400 characters on a never-played install, all of them
   written by mod-city-bots' own update files. So a freshly imported server
   here can read `populated` and there is no honest way from this module to say
   otherwise — see `docker.DatabaseImport`.
3. *Did the import finish?* Answered from AzerothCore's own bookkeeping, and
   the first version of this got it badly wrong. It asked only whether each
   schema had **any** tables, argued in this docstring that a late death was an
   acceptable blind spot, and was then handed a real interrupted import by the
   live gate of 2026-08-23: `ac-db-import` killed 19 seconds in left
   `acore_world` holding exactly three tables — `achievement_category_dbc`,
   `achievement_criteria_data`, `achievement_criteria_dbc`, the base dump having
   reached the letter "a" — out of the 316 a finished import writes. This probe
   called that `imported`, so `repair_import()` refused with "there is nothing
   to repair" and the button built for precisely this state never appeared. The
   blind spot was not late, it was total.

   What is used instead is `updates` and `updates_include`, the two tables
   AzerothCore's own updater keeps to record which SQL files it has applied. A
   finished import has both in every core schema; the interrupted one above had
   them in `acore_auth` and `acore_characters` and neither in `acore_world`,
   which is exactly the distinction that was missing. This is upstream's
   bookkeeping rather than a table count of ours, so it does not rot with every
   release the way a hardcoded "`acore_world` should have N tables" would.

   It is still not a completion marker, because AzerothCore does not write one.
   The base dump is applied in one file and its tables land in roughly
   alphabetical order, so an interruption *after* `updates_include` and before
   the end leaves a schema this reads as finished. That window is small — for
   the world schema it is the tables sorting after "u" — and the fallback for
   anything inside it is unchanged: no player data can be at risk, because
   question 2 is asked first, so deleting the containers and installing again
   costs time and nothing else. Recorded in `pyplan/phase6-decisions.md`.

Nothing here writes. The seam is deliberately the read-only half of
`apply.DockerSql` — narrower than `accounts.SqlSeam`, which also carries
`run_statement()` — so no future edit can turn a probe into a change.
"""

from __future__ import annotations

from typing import Protocol

from yulon import docker
from yulon.apply import DB_NAMES, ApplyError
from yulon.controller_wow_wotlk.maintenance import (
    CORE_DATABASES,
    MaintenanceError,
    MysqlDocker,
)
from yulon.log import get_logger
from yulon.manifest import Db

logger = get_logger(__name__)

# Which `Db` key each core schema name belongs to, so a query can be routed
# through a schema that provably exists rather than through a fixed one that
# may not (`DockerSql` connects *to* a database, and a missing one is an error).
_DB_KEYS: dict[str, Db] = {DB_NAMES[key]: key for key in ("auth", "characters", "world")}

# Where player data lives: schema -> the table that proves somebody has used
# this server. `account` because that is the row `accounts.create_account()`
# writes and the one the authserver reads; `characters` because that is the
# table a restore exists to protect. Both are asked, because an install can
# genuinely have accounts and no characters yet, and losing the accounts is
# still losing something the user made.
KEY_TABLES: dict[str, str] = {
    DB_NAMES["auth"]: "account",
    DB_NAMES["characters"]: "characters",
}


# The two tables AzerothCore's updater maintains for itself: `updates` records
# every SQL file it has applied, `updates_include` the directories it draws
# them from. A schema that has them has been through the updater; a schema
# that does not has, at most, part of a base dump in it.
#
# Chosen over a table count after the live gate of 2026-08-23 produced an
# `acore_world` holding three tables out of 316 and a probe that called it
# imported. A count would have caught that one too, and would have needed a
# new number every time upstream added a table.
IMPORT_MARKERS: tuple[str, ...] = ("updates", "updates_include")


class SqlQuery(Protocol):
    """The read-only slice of `apply.DockerSql` this module needs."""

    def query(self, db: Db, statement: str) -> str: ...


def import_state(sql: SqlQuery, mysql: MysqlDocker) -> docker.ImportState:
    """What state this install's `acore_*` schemas are in. Never raises.

    Every failure — no docker CLI, a database container that is not running, a
    client that will not connect — comes back as `unreadable` carrying the
    reason, because both callers (a button's visibility and a refusal) need an
    answer rather than an exception, and `unreadable` is not `repairable`.
    """
    try:
        # Asked once and held. Written as a comprehension condition, the call is
        # re-evaluated for every element of `CORE_DATABASES` — three real
        # `docker exec ... SHOW DATABASES` round trips instead of one, which
        # quietly made this probe five execs while its own docstring, its test,
        # and `phase6-decisions.md` §5 all said three (review, 2026-08-23).
        existing = mysql.databases()
    except MaintenanceError as exc:
        return docker.ImportState("unreadable", str(exc))
    present = [name for name in CORE_DATABASES if name in existing]
    if not present:
        return docker.ImportState(
            "absent", f"none of {', '.join(CORE_DATABASES)} exists on this server yet"
        )

    # Routed through a schema that exists, though the query itself reads
    # `information_schema`, which is always there.
    through = _DB_KEYS[present[0]]
    schemas = ", ".join(f"'{name}'" for name in CORE_DATABASES)
    try:
        listing = sql.query(
            through,
            "SELECT table_schema, table_name FROM information_schema.tables "
            f"WHERE table_schema IN ({schemas});",
        )
    except ApplyError as exc:
        return docker.ImportState("unreadable", str(exc))
    # Every table name, not just a count, because the same answer has to settle
    # "how much of this schema is there" and "does the table holding player data
    # exist" — and a second round trip to `docker exec` costs about 0.3s. A full
    # WotLK world schema is roughly 1500 rows of two short columns.
    tables: dict[str, set[str]] = {name: set() for name in CORE_DATABASES}
    for line in listing.splitlines():
        schema, _, table = line.strip().partition("\t")
        if schema in tables and table:
            tables[schema].add(table)

    populated = _player_data(sql, through, tables)
    if populated is None:
        return docker.ImportState(
            "unreadable", "the account and character counts could not be read"
        )

    # "Unfinished", not "empty". The distinction is the whole of what the
    # partial gate found: a schema can be far from empty and nowhere near done.
    unfinished = [name for name in CORE_DATABASES if not set(IMPORT_MARKERS) <= tables[name]]
    bare = [name for name in CORE_DATABASES if not tables[name]]
    if populated:
        # Completeness is carried alongside, not folded into the state. The
        # state has to stay ordered by danger — any row at all means refuse,
        # and it is computed before completeness for exactly that reason — but
        # `repair_import()`'s post-check needs to know whether the schemas are
        # finished, and a half-imported database that a module has already
        # seeded rows into looks identical to a finished one from the state
        # alone (review, 2026-08-23).
        return docker.ImportState(
            "populated",
            (
                populated
                if not unfinished
                else f"{populated}, but {_unfinished_detail(unfinished, tables)}"
            ),
            complete=not unfinished,
        )

    if not unfinished:
        return docker.ImportState(
            "imported",
            "; ".join(f"{name} has {len(tables[name])} tables" for name in CORE_DATABASES),
            complete=True,
        )
    if len(bare) == len(CORE_DATABASES):
        return docker.ImportState(
            "absent", f"{_holds(bare)} no tables at all, so the import never ran"
        )
    return docker.ImportState("partial", _unfinished_detail(unfinished, tables))


def _unfinished_detail(unfinished: list[str], tables: dict[str, set[str]]) -> str:
    """Name each unfinished schema and say how far it actually got.

    The count is in the sentence because "acore_world was never finished" and
    "acore_world has 3 of the 316 tables it should" are the same fact told at
    very different volumes, and this string is what a user reads before deciding
    whether to let something overwrite their databases.
    """
    said = ", ".join(f"{name} has {len(tables[name])} tables" for name in unfinished)
    one = len(unfinished) == 1
    subject, verb = ("it", "was") if one else ("they", "were")
    return f"{said} but no import record, so {subject} {verb} never finished"


def _holds(names: list[str]) -> str:
    """`"acore_auth holds"` for one name, `"a, b hold"` for several.

    A detail string here is rendered verbatim into the Server tab, so it has to
    agree with itself grammatically for one schema as well as three.
    """
    return f"{', '.join(names)} {'holds' if len(names) == 1 else 'hold'}"


def _player_data(sql: SqlQuery, through: Db, tables: dict[str, set[str]]) -> str | None:
    """How much of this server somebody made, as a sentence — `""` for none.

    `None` means the question could not be answered, which callers must treat as
    "assume there is data": an unreadable count is the one case where guessing
    "empty" ends with the characters gone.
    """
    asked = [
        (schema, table)
        for schema, table in KEY_TABLES.items()
        if table in tables.get(schema, set())
    ]
    if not asked:
        # Neither table exists, so there is nowhere for an account or a
        # character to be. Not a failed read — a proven zero.
        return ""
    counts = ", ".join(f"(SELECT COUNT(*) FROM `{schema}`.`{table}`)" for schema, table in asked)
    try:
        answer = sql.query(through, f"SELECT {counts};")
    except ApplyError as exc:
        where = ", ".join(f"{schema}.{table}" for schema, table in asked)
        logger.warning(f"could not count the player data in {where}: {exc}")
        return None
    fields = answer.strip().split("\t")
    if len(fields) != len(asked):
        logger.warning(f"expected {len(asked)} counts from the server, got {answer.strip()!r}")
        return None
    said: list[str] = []
    for (schema, table), field in zip(asked, fields, strict=True):
        try:
            rows = int(field.strip())
        except ValueError:
            logger.warning(f"{schema}.{table} answered {field.strip()!r}, which is not a count")
            return None
        if rows:
            said.append(f"{rows} rows in {schema}.{table}")
    return ", ".join(said)
