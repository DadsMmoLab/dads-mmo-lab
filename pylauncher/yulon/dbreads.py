"""Typed reads over the server's own databases (Phase 8.1a).

Reads only. This module never calls the write half of the SQL seam, and that is
asserted over the syntax tree rather than left to review — see
`tests/test_write_ledger.py`. Owner answer 7 (2026-09-06) is the reason: nothing
in the app writes `characters` or `world` while the world is running, so the one
module that talks to those databases is the one that only ever asks questions.

## Identifying a bot

Two arms, always both, because each alone has failed in production and the
failures are recorded (`.notes/phase8-reads/hypeer.md`, from `botid.rs`):

* **The registry alone fails open.** `playerbots_account_type` is filled in by
  the bot module as it goes, and on a freshly built install it can hold zero
  rows while a thousand bot characters play. `account NOT IN (<empty set>)` is
  true for every row, so every bot is reported as a person and the bot count
  reports zero — with nothing about the output looking wrong (incident
  2026-08-01).
* **The prefix alone fails closed, and worse.** An empty prefix compiles to
  `LIKE '%'`, which matches every account and calls the whole family bots
  (`botid.rs:33-38`). So a prefix that cannot be read is refused rather than
  defaulted or blanked.

## What is NOT resolved here, and is named rather than assumed

On AzerothCore an environment variable beats both the conf file and the compiled
default, **including for a key that is absent from the file**, and the naming
rule is generic rather than per-key: every ini key `X` is looked up as
`"AC_" + upper_snake(X)`, so `AiPlayerbot.RandomBotAccountPrefix` is
`AC_AI_PLAYERBOT_RANDOM_BOT_ACCOUNT_PREFIX`. That is measured, not derived —
`Config.cpp:435-438` for the name, `:370-374` and `:391-394` for the transform,
`:540-552` for env winning at read time — and the proof the rule is generic is
that the catalog already relies on it for `AiPlayerbot.MinRandomBots`. It is all
recorded in `.notes/phase8-reads/azerothcore.md:119-124`.

**This module does not read that layer yet**, and that is a real gap rather than
an unknown one. It was written here as "unmeasured" until 2026-09-06, which was
wrong about the evidence: the answer was already in the phase's own read.

What the gap costs is bounded. Yu'lon's generated override sets no prefix key,
so on an install this app made, the conf-or-default answer is the one in force.
An install where somebody set that variable by hand produces a marker that reads
fine and matches nothing — which `population()` reports as a **warning** rather
than as zero, so it surfaces as a question instead of a wrong number.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from yulon.catalog.catalog import BotMarker, CatalogEntry
from yulon.log import get_logger
from yulon.manifest import Db

logger = get_logger(__name__)

_SAFE_PREFIX = re.compile(r"^[A-Za-z0-9_.-]+$")
"""What a bot-account prefix may contain before it is pasted into a SQL string.

Not an escape: a refusal. The value arrives from a file on the install's disk
and ends up inside a quoted `LIKE`, and the set of characters a real prefix
needs (an account name) does not include a quote, a percent sign or a
backslash. Anything else is answered with "I will not use this" rather than with
an escaping rule that has to be right on four cores and two SQL modes.
"""


@runtime_checkable
class SqlReader(Protocol):
    """The read half of `apply.DockerSql`, and deliberately only that half.

    Typed as a Protocol with one method so nothing in this module can reach
    `run_statement()` or `run_file()` even by accident: what is not in the type
    cannot be called through it.

    `runtime_checkable` since 2026-09-07, for one caller outside this module:
    `apply.Applier` holds a `SqlRunner` — the WRITE half — and has to find out
    whether the object it was handed can also be asked a question, because the
    real one (`DockerSql`) can and a test's write-only fake cannot. An
    `isinstance` against this Protocol only checks the method is THERE; that is
    the whole claim being made, and the alternative was `hasattr`.
    """

    def query(self, db: Db, statement: str) -> str: ...


@dataclass(frozen=True)
class Marker:
    """The bot marker in force on one install, and where its value came from."""

    prefix: str
    source: str
    """`conf` when the install's own file set it, `default` when the module's did.

    Reported to the user rather than kept: "no bots found, prefix `rndbot` from
    the module default" and "…from playerbots.conf" send a person to two
    different places.
    """


@dataclass(frozen=True)
class MarkerAnswer:
    """A marker, or the reason there is none. Never both, never neither."""

    marker: Marker | None = None
    problem: str = ""


@dataclass(frozen=True)
class Population:
    """How many of each are on this server, or why the question has no answer.

    `players` and `bots` are `None` — not zero — whenever `problem` is set. A
    database that cannot be asked must not be able to look like a server nobody
    is playing on.
    """

    players: int | None = None
    bots: int | None = None
    characters: int | None = None
    problem: str = ""
    warning: str = ""


def resolve_marker(entry: CatalogEntry, server_dir: Path) -> MarkerAnswer:
    """Read the bot prefix this install is actually running with.

    Three outcomes, which is the shape this phase gives every question that can
    fail to have an answer: the live value, the module's default when the key is
    simply not set (which is what the server itself would use), or a refusal
    that names what stopped it.
    """
    ops = entry.observability
    if ops is None:
        return MarkerAnswer(problem=f"{entry.id} has no measured bot marker yet")
    bots = ops.bots
    conf = server_dir / bots.prefix_conf_file
    try:
        text = conf.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        # Absent is not the same as unreadable, and this half was measured on
        # yulon-ubuntu on 2026-09-06 rather than reasoned about. A normal
        # AzerothCore install ships `playerbots.conf.dist` and no
        # `playerbots.conf`, and the worldserver says so itself:
        #
        #     > Config::LoadFile: Failed open file
        #       '/azerothcore/env/dist/etc/modules/playerbots.conf'
        #     > Not found modules config files
        #
        # It then runs on the module's compiled defaults — the `.dist` beside
        # it is NOT loaded. So an absent file is exactly what "the default is in
        # force" looks like on this core, and refusing here would leave the tab
        # with no counts on an ordinary install.
        return _checked(Marker(bots.account_prefix, "default"), bots)
    except OSError as exc:
        # A file that IS there and will not open says nothing about what is in
        # it. That is the half of the old rule the measurement leaves standing.
        return MarkerAnswer(problem=f"could not read {conf}: {exc}")
    values = _conf_values(text, bots.prefix_conf_key)
    if len(set(values)) > 1:
        return MarkerAnswer(
            problem=(
                f"{bots.prefix_conf_key} is set more than once in {conf.name}, to "
                f"{' and '.join(repr(v) for v in sorted(set(values)))}; which one the server "
                "takes is not something this can decide"
            )
        )
    if not values:
        return _checked(Marker(bots.account_prefix, "default"), bots)
    if not values[0]:
        return MarkerAnswer(
            problem=(
                f"{bots.prefix_conf_key} is blank in {conf.name}. An empty prefix matches every "
                "account, which would report every player as a bot, so nothing is counted "
                "until it is set."
            )
        )
    return _checked(Marker(values[0], "conf"), bots)


def _checked(marker: Marker, bots: BotMarker) -> MarkerAnswer:
    """Refuse a prefix that cannot be put inside a SQL string as it stands."""
    if not _SAFE_PREFIX.match(marker.prefix):
        return MarkerAnswer(
            problem=(
                f"the bot account prefix {marker.prefix!r} ({bots.prefix_conf_key}) contains "
                "characters that cannot be used in a query; nothing is counted until it is "
                "changed"
            )
        )
    return MarkerAnswer(marker=marker)


def _conf_values(text: str, key: str) -> list[str]:
    """Every ACTIVE setting of `key`, in file order, unquoted.

    Column 0 only, matching `conf.patch()`'s rule and for its reason: the
    shipped files are full of commented prose that a looser pattern would read
    as settings.
    """
    pattern = re.compile(rf"^{re.escape(key)}\s*=(.*)$")
    found: list[str] = []
    for line in text.splitlines():
        hit = pattern.match(line)
        if hit:
            found.append(hit.group(1).strip().strip('"'))
    return found


def bot_clause(entry: CatalogEntry, marker: Marker) -> str:
    """The SQL that is true of a bot's character row, for THIS install's schemas.

    Written against the characters table's account column, so it composes into
    any query over `characters` as `(<clause>)` or `NOT (<clause>)`.
    """
    ops = entry.observability
    if ops is None:  # pragma: no cover - resolve_marker refuses first
        raise ValueError(f"{entry.id} has no measured bot marker yet")
    schemas = entry.schema_map()
    account = ops.characters.account
    arms = [
        f"{account} IN (SELECT id FROM {schemas['auth']}.account "
        f"WHERE UPPER(username) LIKE '{marker.prefix.upper()}%')"
    ]
    registry = ops.bots.registry
    if registry is not None and registry.database in schemas:
        types = ", ".join(str(t) for t in registry.types)
        arms.insert(
            0,
            f"{account} IN (SELECT {registry.account_column} FROM "
            f"{schemas[registry.database]}.{registry.table} "
            f"WHERE {registry.type_column} IN ({types}))",
        )
    return " OR ".join(arms)


def population(sql: SqlReader, entry: CatalogEntry, marker: Marker) -> Population:
    """Online players, online bots, and the two totals behind the warning.

    One statement, not four. This runs on the same five-second tick as the
    status poll, and every `docker exec … mysql` costs about a third of a second
    of process startup before any SQL happens (`docker.py:1883-1893` records the
    same measurement for `docker inspect`).
    """
    ops = entry.observability
    if ops is None:  # pragma: no cover - resolve_marker refuses first
        return Population(problem=f"{entry.id} has no measured bot marker yet")
    clause = bot_clause(entry, marker)
    table = f"{entry.databases.characters}.{ops.characters.table}"
    online = ops.characters.online
    statement = (
        "SELECT "
        f"SUM({online} = 1 AND NOT ({clause})), "
        f"SUM({online} = 1 AND ({clause})), "
        f"SUM({clause}), "
        "COUNT(*) "
        f"FROM {table};"
    )
    try:
        raw = sql.query("characters", statement)
    except Exception as exc:  # noqa: BLE001 - every seam failure is one answer here
        logger.warning(f"could not count this server's population: {exc}")
        return Population(problem=f"could not read the server's characters: {exc}")
    numbers = _four_numbers(raw)
    if numbers is None:
        return Population(
            problem=f"the character count came back as {raw.strip()!r}, which is not four numbers"
        )
    players, bots, bots_total, characters = numbers
    warning = ""
    if bots_total == 0 and characters > 0:
        warning = (
            f"no account matched the bot marker {ops.bots.account_prefix!r}"
            if marker.source == "default"
            else f"no account matched the bot marker {marker.prefix!r}"
        ) + f", though this server has {characters} characters"
    return Population(players=players, bots=bots, characters=characters, warning=warning)


def _four_numbers(raw: str) -> tuple[int, int, int, int] | None:
    """Parse one row of four counts; `None` for anything else.

    `SUM()` over no rows is SQL `NULL`, which arrives as the four characters
    `NULL`, and that is a zero here rather than a parse failure — an empty
    characters table is a real state, not a broken query.
    """
    rows = [line for line in raw.splitlines() if line.strip()]
    if len(rows) != 1:
        return None
    fields = rows[0].split("\t")
    if len(fields) != 4:
        return None
    out: list[int] = []
    for field in fields:
        value = field.strip()
        if value in ("NULL", ""):
            out.append(0)
        elif value.lstrip("-").isdigit():
            out.append(int(value))
        else:
            return None
    return out[0], out[1], out[2], out[3]
