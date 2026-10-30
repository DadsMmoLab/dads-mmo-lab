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

## The environment layer (AzerothCore)

On AzerothCore an environment variable beats both the conf file and the compiled
default, **including for a key that is absent from the file**, and the naming
rule is generic rather than per-key: every ini key `X` is looked up as
`"AC_" + upper_snake(X)`, so `AiPlayerbot.RandomBotAccountPrefix` is
`AC_AI_PLAYERBOT_RANDOM_BOT_ACCOUNT_PREFIX` (`AC_PLAYERBOTS_…` for a module past
the T657 rename). That is measured, not derived — `Config.cpp:435-438` for the
name, `:370-374` and `:391-394` for the transform, `:540-552` for env winning at
read time — recorded in `.notes/phase8-reads/azerothcore.md:119-124`.

Since T805's review `resolve_marker()` reads that layer, in the order the server
does: the world service's `environment:` in the compose files (the override over
the base, as compose merges them), then the conf, then the module default. A
setting it cannot read for certain -- a list-form entry, an `env_file:` it
cannot follow, a `${…}` it cannot resolve, two different values -- is a refusal
rather than a guess, because the Bots tab's delete writes by this prefix.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Protocol, runtime_checkable

from yulon import playerbots_keys, table_repair
from yulon.catalog import compose_env, composegen
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


PREFIX_ESCAPE = "!"
"""The LIKE escape for a prefix match: `botlist.ESCAPE`'s reasoning (a backslash means nothing
under `NO_BACKSLASH_ESCAPES`), and a character `_SAFE_PREFIX` never lets into a prefix."""


def prefix_match(column: str, prefix: str) -> str:
    """SQL true when `column` STARTS WITH `prefix`, case-insensitively, and nothing more.

    A bare `LIKE 'RNDBOT_%'` is not that: `_` is LIKE's one-character wildcard and
    `_SAFE_PREFIX` allows it, so `RNDBOT_` would also match `RNDBOTX…` -- and the Bots
    tab's delete writes by this test. The wildcards (and the escape itself) are escaped
    and the escape is named, so every reader and the delete ask the one literal question.
    The prefix has passed `_SAFE_PREFIX` (no quote, no backslash) before it gets here.
    """
    escaped = "".join(
        PREFIX_ESCAPE + char if char in ("%", "_", PREFIX_ESCAPE) else char
        for char in prefix.upper()
    )
    return f"UPPER({column}) LIKE '{escaped}%' ESCAPE '{PREFIX_ESCAPE}'"


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
    """`env` when the world container's environment set it (AzerothCore), `conf` when the
    install's own file did, `default` when the module's did.

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
    crashed: tuple[table_repair.CrashedTable, ...] = ()
    """The damaged tables a read failure named (T822); empty on every other failure."""
    crash_unnamed: bool = False
    """A storage-engine crash error that named no table this app may repair (T822)."""


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
    if bots.prefix_conf_file is None or bots.prefix_conf_key is None:
        # A tree with no prefix setting (T179: Centurion's fixed bot accounts):
        # the catalog's prefix is the marker, and nothing on disk can change it.
        return _checked(Marker(bots.account_prefix, "default"), bots)
    # T805 review: the environment beats the conf and the default on AzerothCore.
    from_env = _env_prefix(entry, server_dir, bots.prefix_conf_key)
    if from_env.problem:
        return MarkerAnswer(problem=from_env.problem)
    if from_env.value is not None:
        return _checked(Marker(from_env.value, "env"), bots)
    conf = server_dir / bots.prefix_conf_file
    try:
        text = conf.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        # Absent is not the same as unreadable, and this half was measured on
        # yulon-ubuntu on 2026-09-06 rather than reasoned about. An AzerothCore
        # install made before T137 has `playerbots.conf.dist` and no
        # `playerbots.conf` (the install writes one since, and Repair server
        # files offers it to the older ones), and the worldserver says so itself:
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
    # T657: the key as this server's mod-playerbots reads it (`Playerbots.` since ed54b459).
    key = playerbots_keys.key(bots.prefix_conf_key, playerbots_keys.module_prefix(server_dir))
    values = _conf_values(text, key)
    if len(set(values)) > 1:
        return MarkerAnswer(
            problem=(
                f"{key} is set more than once in {conf.name}, to "
                f"{' and '.join(repr(v) for v in sorted(set(values)))}; which one the server "
                "takes is not something this can decide"
            )
        )
    if not values:
        return _checked(Marker(bots.account_prefix, "default"), bots)
    if not values[0]:
        return MarkerAnswer(
            problem=(
                f"{key} is blank in {conf.name}. An empty prefix matches every "
                "account, which would report every player as a bot, so nothing is counted "
                "until it is set."
            )
        )
    return _checked(Marker(values[0], "conf"), bots)


@dataclass(frozen=True)
class _EnvAnswer:
    """The prefix the world's environment sets, None when it sets none, or why it is unknown."""

    value: str | None = None
    problem: str = ""


_INTERPOLATION = re.compile(
    r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:(?P<op>:?-)(?P<default>[^}]*))?\}"
)


def _env_prefix(entry: CatalogEntry, server_dir: Path, conf_key: str) -> _EnvAnswer:
    """The prefix the world container's environment sets, read as compose builds it.

    AzerothCore only (`install.native.family`): no other core reads `AC_*`. The
    world service's `environment:` in `docker-compose.yml` and then
    `docker-compose.override.yml` -- compose merges the override's keys over the base's,
    so the override's value wins -- under both of mod-playerbots' spellings (T657),
    and any `env_file:` of that service. A mention of the name anywhere else in a
    compose file (a list-form entry, another service's block) is a refusal: this
    reader cannot say what compose makes of it, and a wrong answer here is a delete
    by the wrong prefix.
    """
    native = entry.install.native
    if native is None or native.family != "azerothcore":
        return _EnvAnswer()
    names = playerbots_keys.reading_order(composegen.env_name_for(conf_key), None, of_env=True)
    service = entry.container_spec().world
    dotenv = _dotenv(server_dir / composegen.DOTENV_FILE)
    found: str | None = None
    for file in (composegen.BASE_FILE, composegen.OVERRIDE_FILE):
        path = server_dir / file
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        except (OSError, UnicodeDecodeError) as exc:
            return _EnvAnswer(problem=f"could not read {path} for {names[0]}: {exc}")
        lines = text.split("\n")
        at = compose_env.env_lines(lines, service)
        spots = sorted(i for name in names for i in at.get(name, []))
        mentioned = sorted(
            i
            for i, line in enumerate(lines)
            if not compose_env.says_nothing(line) and any(name in line for name in names)
        )
        if spots != mentioned:
            return _EnvAnswer(
                problem=(
                    f"{names[0]} is named in {file} in a form Yu'lon cannot read for certain "
                    "(outside the world service's `KEY: value` environment lines), so the bot "
                    "account prefix this server runs with is not known"
                )
            )
        values = {compose_env.env_value(lines[i]) for i in spots}
        for env_file in _env_files(lines, service):
            resolved = _interpolated(env_file, dotenv)
            if resolved is None:
                return _EnvAnswer(
                    problem=f"the world's env_file {env_file!r} in {file} cannot be resolved, "
                    f"so whether it sets {names[0]} is not known"
                )
            try:
                env_text = (server_dir / resolved).read_text(encoding="utf-8")
            except FileNotFoundError:
                continue
            except (OSError, UnicodeDecodeError) as exc:
                return _EnvAnswer(problem=f"could not read {resolved} for {names[0]}: {exc}")
            values |= {v for k, v in _dotenv_text(env_text).items() if k in names}
        resolved_values = {_interpolated(v, dotenv) for v in values}
        if None in resolved_values:
            return _EnvAnswer(
                problem=f"{names[0]} in {file} uses a `$` substitution Yu'lon cannot resolve"
            )
        if len(resolved_values) > 1:
            return _EnvAnswer(
                problem=(
                    f"{names[0]} is set more than once for the world in {file}, to "
                    f"{' and '.join(repr(v) for v in sorted(str(v) for v in resolved_values))}; "
                    "which one the server takes is not something this can decide"
                )
            )
        if resolved_values:
            found = resolved_values.pop()
    if found is not None and not found:
        return _EnvAnswer(
            problem=(
                f"{names[0]} is blank in the world's environment. An empty prefix matches every "
                "account, so nothing is counted until it is set."
            )
        )
    return _EnvAnswer(value=found)


def _env_files(lines: list[str], service: str) -> list[str]:
    """The `env_file:` entries of `service` (a scalar or a list of plain paths)."""
    found: list[str] = []
    service_at: int | None = None
    key_at: int | None = None
    for raw in lines:
        line = raw.rstrip("\r")
        if compose_env.says_nothing(line):
            continue
        depth = compose_env.indent(line)
        bare = line.strip()
        if service_at is not None and depth <= service_at:
            service_at = None
        if service_at is None:
            if bare == f"{service}:":
                service_at = depth
            key_at = None
            continue
        if key_at is not None and depth > key_at and bare.startswith("-"):
            found.append(bare[1:].strip().strip("\"'"))
            continue
        key_at = None
        if bare.startswith("env_file:"):
            value = bare[len("env_file:") :].strip()
            if value:
                found.append(value.strip("\"'"))
            else:
                key_at = depth
    return found


def _dotenv(path: Path) -> dict[str, str]:
    """`.env`'s `KEY=value` lines, the values compose substitutes; empty when there is none."""
    try:
        return _dotenv_text(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return {}


def _dotenv_text(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip().removeprefix("export ").strip()] = value.strip().strip("\"'")
    return out


def _interpolated(value: str, dotenv: dict[str, str]) -> str | None:
    """`value` with compose's `${NAME}`, `${NAME:-x}` and `${NAME-x}` resolved, else None.

    Read from `.env` only: Yu'lon runs compose with no variable of its own for these, and
    a `$` form other than those three is not guessed at.
    """

    def one(match: re.Match[str]) -> str:
        name, op, default = match.group("name"), match.group("op"), match.group("default")
        if name in dotenv and not (op == ":-" and dotenv[name] == ""):
            return dotenv[name]
        if op is None:
            raise KeyError(name)
        return default or ""

    try:
        out = _INTERPOLATION.sub(one, value)
    except KeyError:
        return None
    return None if "$" in out else out


def _checked(marker: Marker, bots: BotMarker) -> MarkerAnswer:
    """Refuse a prefix that cannot be put inside a SQL string as it stands."""
    if not _SAFE_PREFIX.match(marker.prefix):
        return MarkerAnswer(
            problem=(
                f"the bot account prefix {marker.prefix!r} "
                f"({bots.prefix_conf_key or 'from the catalog'}) contains "
                "characters that cannot be used in a query; nothing is counted until it is "
                "changed"
            )
        )
    return MarkerAnswer(marker=marker)


def _conf_values(text: str, key: str) -> list[str]:
    """Every ACTIVE setting of `key`, in file order, unquoted.

    Each line is trimmed first, as every core's own reader does (AzerothCore's
    `Config.cpp` and CMaNGOS's `Config::Reload` alike), so an INDENTED setting
    is live (T572). A commented line never matches: it starts with `#`. Every
    copy is returned and `resolve_marker` refuses a conflict, because the two
    families this serves disagree on which copy wins.
    """
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=(.*)$")
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
        f"WHERE {prefix_match('username', marker.prefix)})"
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


# T437: a playerbots table is given this long after the world started to appear (T425).
MISSING_TABLE_GRACE = timedelta(minutes=2)


class MissingTableSaid:
    """Which world run has already been told its playerbots table is missing (T437).

    One per watcher, passed to `population()`: the tick repeats every few seconds and
    the sentence must be said once per run, then again if the table returns and goes.
    """

    def __init__(self) -> None:
        self.run: str | None = None

    def should_say(self, run: str) -> bool:
        if self.run == run:
            return False
        self.run = run
        return True

    def table_found(self) -> None:
        self.run = None


def population(
    sql: SqlReader,
    entry: CatalogEntry,
    marker: Marker,
    *,
    world_up: timedelta | None = None,
    said: MissingTableSaid | None = None,
    run: str = "",
) -> Population:
    """Online players, online bots, and the two totals behind the warning.

    One statement, not four. This runs on the same five-second tick as the
    status poll, and every `docker exec … mysql` costs about a third of a second
    of process startup before any SQL happens (`docker.py:1883-1893` records the
    same measurement for `docker inspect`).

    `world_up` is how long the world has run and `said` remembers the warning (T437): a
    playerbots table still missing after `MISSING_TABLE_GRACE` is warned about once per
    `run`, not logged at info on every tick.
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
        # A schema the world has not created yet is the poll arriving early (T424): the
        # next tick asks again, so it is said at info; anything else is a warning.
        # T425: the world makes a playerbots table on its first start: ERROR 1146 is the early poll.
        text = str(exc)
        missing_bots_table = "1146" in text and "playerbots" in text
        early = "Unknown database" in text or missing_bots_table
        if missing_bots_table and said is not None and world_up is not None:
            if world_up > MISSING_TABLE_GRACE:
                if said.should_say(run):
                    logger.warning(
                        "The bots' tables are still missing, so bots cannot be counted. "
                        "Stop and Start the server once; if they are still missing, press "
                        "Save logs for support… and ask for help."
                    )
                return Population(problem=f"could not read the server's characters: {exc}")
        crashed = table_repair.crashed_tables(
            text, (entry.databases.characters, entry.databases.auth)
        )
        if crashed.tables or crashed.unnamed:
            # T822: a crash left a table damaged. The program's own words are the log's;
            # the card says what happened in plain words and offers the repair.
            logger.warning(f"a database table is damaged: {text}")
            return Population(
                problem=table_repair.damaged_sentence(crashed),
                crashed=crashed.tables,
                crash_unnamed=crashed.unnamed,
            )
        say = logger.info if early else logger.warning
        say(f"could not count this server's population: {exc}")
        return Population(problem=f"could not read the server's characters: {exc}")
    if said is not None:
        said.table_found()
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
