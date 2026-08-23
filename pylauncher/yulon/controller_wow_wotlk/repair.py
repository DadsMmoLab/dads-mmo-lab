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
   holds characters is still somebody's server.
3. *Did the import finish?* This one is only half-answerable, and the honest
   answer is written into the state rather than hidden. A schema with no tables
   proves the import did not get to it; a schema with tables proves nothing
   about whether it got *all* of them. AzerothCore writes no completion marker,
   and a hardcoded "acore_world should have N tables" would be a number that
   silently rots with every upstream release. So a machine that died late in the
   import — every schema created, some tables missing — reads as `imported`
   here, and this module does not pretend otherwise. What makes that acceptable
   is question 2: the case is only reachable on a database with no player data,
   where the fallback (delete the containers and install again) costs time and
   nothing else. Recorded in `pyplan/phase6-decisions.md`.

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
        present = [name for name in CORE_DATABASES if name in mysql.databases()]
    except MaintenanceError as exc:
        return docker.ImportState("unreadable", str(exc))
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
    if populated:
        return docker.ImportState("populated", populated)

    empty = [name for name in CORE_DATABASES if not tables[name]]
    if not empty:
        return docker.ImportState(
            "imported",
            "; ".join(f"{name} has {len(tables[name])} tables" for name in CORE_DATABASES),
        )
    if len(empty) == len(CORE_DATABASES):
        return docker.ImportState(
            "absent", f"{', '.join(empty)} hold no tables at all, so the import never ran"
        )
    return docker.ImportState(
        "partial",
        f"{', '.join(empty)} hold no tables, while "
        + "; ".join(
            f"{name} has {len(tables[name])}" for name in CORE_DATABASES if name not in empty
        ),
    )


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
