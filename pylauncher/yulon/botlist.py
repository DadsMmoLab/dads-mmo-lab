"""Browsing this server's bots, one page at a time (Phase 8.5a, 8.5b, 8.5c).

8.1a already counts them. What this adds is the list, and one fact per row that
the count cannot carry: **which signal identified it**. On WotLK there are
two — the playerbots registry and the account-name prefix — and they disagree in
exactly the case that matters. An install whose prefix was changed after its
bots were made has rows the registry knows about and the prefix does not; a
single number hides that, and a person looking for a missing bot has nowhere to
look.

**Only WotLK has two.** The three CMaNGOS trees carry the account prefix and
nothing else, so on those the split is a sentence about nothing: it named the
`playerbots` registry, which those installs have not got, and put a zero beside
it that a person would then go looking for. `has_registry()` is the one reading
of that fact, and both the SQL that attributes a row and the sentence the tab
prints take it from there (8.5b, measured on m910q's TBC install 2026-09-07:
900 bots of 901 characters, the split reading 0/900 on every read).

**Where a marker matching nothing is reachable at all.** On WotLK it is not —
8.5a asked with a marker matching nothing and still got 1000, because the
registry arm answers whether or not the prefix does. On a one-signal tree the
zero is real, and the warning beside it is the only honest output. That path
was exercised against a live server for the first time on 8.5b.

**And a zero has two causes, which had one sentence between them.** The name
filter is folded into the WHERE the total is counted over, so a filter matching
nothing reached the marker warning: the tab blamed a marker that had matched 500
rows a keystroke earlier. `_why_zero` picks the sentence by which question was
asked, and the point is not only that the filtered sentence was false — it is
that the marker sentence is the whole live evidence for the warn clause on a
one-signal tree, and a second cause for the same words would have left 8.5c's
gate unable to say which one it photographed (8.5c, 2026-09-07).

Paged because the owner runs 500 and has said the number is his to raise. Read
live, never cached: the population moves while the tab is open.

**Paged by where the last page ended, not by how many rows to skip.** The list
is read fresh on every press from a table that changes while the tab is open,
and `OFFSET` counts rows: one bot logging out before the boundary shifts every
later page by one, so a row is shown twice and the row that took its place is
never shown at all. A cursor is anchored to a row instead — `(name, guid)`,
which is a total order where `name` alone is not, so equal names cannot swap
places between two reads (adversarial review, 2026-09-07).

The total beside it is still a separate count and can be a moment older than
the rows. That is what a live list is; it is labelled rather than pretended
away.

**The filter is escaped with `!`, not with a backslash.** `NO_BACKSLASH_ESCAPES`
is a real mode on both MySQL and MariaDB, and under it a backslash is an
ordinary character — so a filter escaped with one silently stops escaping and
starts matching. A name with an apostrophe is a name, and it is doubled rather
than stripped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from yulon.catalog.catalog import CatalogEntry
from yulon.dbreads import Marker, SqlReader, bot_clause
from yulon.log import get_logger

logger = get_logger(__name__)

PAGE_SIZE = 50

ESCAPE = "!"
"""The LIKE escape character, chosen for what it is NOT.

A backslash means nothing under `NO_BACKSLASH_ESCAPES`, which both MySQL and
MariaDB really ship, so an escaped filter would quietly become an unescaped one.
`!` has no meaning in either mode.
"""


@dataclass(frozen=True)
class Bot:
    """One bot character, and the signal that said so."""

    name: str
    level: int
    online: bool
    source: str
    """`registry` or `prefix` — which signal found it.

    Read on every tree, shown only where `has_registry()` is true: on a tree
    with one signal it is the same word on every row.
    """


@dataclass(frozen=True)
class Page:
    """One page of bots, or why there is none.

    `total` is `None` rather than 0 when the answer is not known, for the reason
    8.1a's `Population` gives: on this server zero is never true, so reporting
    it turns a broken read into a confident lie.
    """

    bots: list[Bot] = field(default_factory=list)
    total: int | None = None
    by_registry: int = 0
    by_prefix: int = 0
    problem: str = ""
    warning: str = ""
    next_after: tuple[str, int] | None = None
    """Where the next page starts, or None when this is the last one."""


def literal(text: str) -> str:
    """One string as a hex blob, so the server never parses what is inside it.

    The shape `play.literal` and `useraccounts` use. A quoted literal with its
    quote doubled is not enough here: MySQL and MariaDB also read a backslash
    inside quotes by default, so a typed `\\'` closed the literal (T683).
    """
    return "_utf8mb4 X'" + text.encode("utf-8").hex().upper() + "'"


def escape_like(text: str) -> str:
    """One user-typed filter, with the LIKE wildcards made literal.

    Two wildcards and the escape itself and no more: anything else a person
    types is a character in a character's name. The result goes to the server
    through `literal`, never inside quotes.
    """
    out = text.replace(ESCAPE, ESCAPE + ESCAPE)
    return out.replace("%", ESCAPE + "%").replace("_", ESCAPE + "_")


def page(
    sql: SqlReader,
    entry: CatalogEntry,
    marker: Marker,
    *,
    after: tuple[str, int] | None = None,
    limit: int = PAGE_SIZE,
    name_like: str = "",
) -> Page:
    """One page of this install's bots, with the split by signal.

    Two statements and not one: the totals are the answer to "how many, and by
    which signal", and the page is the answer to "show me these fifty". Asking
    them together would mean either counting 500 rows to show 50 or showing 50
    and guessing at the total.
    """
    ops = entry.observability
    if ops is None:
        return Page(problem=f"{entry.name} has no measured bot marker yet, so it will not guess")
    if not marker.prefix.strip():
        return Page(
            problem=(
                "this install's bot marker could not be read, and an empty marker would match "
                "every account on the server"
            )
        )
    clause = bot_clause(entry, marker)
    registry = _registry_clause(entry, ops)
    table = f"{entry.databases.characters}.{ops.characters.table}"
    online = ops.characters.online
    where = f"({clause})"
    if name_like:
        where += f" AND name LIKE {literal('%' + escape_like(name_like) + '%')} ESCAPE '{ESCAPE}'"
    counted = where
    if after is not None:
        # The row comparison, not `name > x OR (name = x AND guid > y)`: it is
        # the same thing, it is what an index on (name, guid) can seek to, and
        # it cannot be got subtly wrong.
        where += f" AND (name, guid) > ({literal(after[0])}, {int(after[1])})"

    try:
        counts = sql.query(
            "characters",
            "SELECT COUNT(*), "
            f"SUM({registry}), "
            f"SUM(NOT ({registry})) "
            f"FROM {table} WHERE {counted};",
        )
        rows = sql.query(
            "characters",
            f"SELECT name, level, {online}, "
            f"CASE WHEN {registry} THEN 'registry' ELSE 'prefix' END, guid "
            f"FROM {table} WHERE {where} ORDER BY name, guid LIMIT {int(limit)};",
        )
    except Exception as exc:  # noqa: BLE001 - every seam failure is one answer here
        logger.warning(f"could not browse {entry.id}'s bots: {exc}")
        return Page(problem=f"could not read this server's bots: {exc}")

    numbers = _three(counts)
    if numbers is None:
        return Page(
            problem=f"the bot count came back as {counts.strip()!r}, which is not three numbers"
        )
    total, by_registry, by_prefix = numbers

    warning = ""
    if total == 0:
        warning = _why_zero(sql, table, f"({clause})", marker, name_like)

    parsed = _rows(rows)
    if isinstance(parsed, str):
        return Page(problem=parsed, total=None)
    bots, keys = parsed
    # A short page is the last one. A full page MAY be the last one -- the only
    # way to know is to ask for one more row than is shown, and a spare row per
    # press is a worse trade than a Next that occasionally lands on nothing.
    next_after = keys[-1] if len(bots) == limit and keys else None
    return Page(
        bots=bots,
        total=total,
        by_registry=by_registry,
        by_prefix=by_prefix,
        warning=warning,
        next_after=next_after,
    )


def _why_zero(sql: SqlReader, table: str, clause: str, marker: Marker, name_like: str) -> str:
    """The sentence beside a zero, chosen by which question produced it (8.5c).

    Two different things drive `total` to zero and they had one sentence between
    them, because the name filter is folded into the very WHERE the total is
    counted over. Typing a filter that matched nothing made the tab say *"no
    character matched the bot marker 'RNDBOT'"* — of a marker that had matched
    500 rows a keystroke earlier. That is a false sentence on its own, and it
    was worse than that: on a one-signal tree the marker warning is the whole
    evidence for 8.5b's third clause, so a second cause for the same words left
    the gate unable to say which one it had photographed (adversarial review of
    8.5c's plan, 2026-09-07).

    The denominators differ with the question. An unfiltered zero is held
    against the whole characters table — "did this marker find anybody at all on
    a populated server". A filtered zero is held against the bots the marker DID
    find, because quoting the character count there would read as the marker
    having failed. A filtered zero on a server where the marker found nothing
    either falls through to the marker's sentence: the filter is not the
    interesting half of that answer.
    """
    if name_like:
        bots = _count(sql, table, clause)
        if bots:
            return (
                f"no bot's name contains {name_like!r} — this server has "
                f"{bots} {'bot' if bots == 1 else 'bots'}"
            )
    characters = _count(sql, table, "")
    if characters:
        return (
            f"no character matched the bot marker {marker.prefix!r}, though this server has "
            f"{characters} characters"
        )
    return ""


def _count(sql: SqlReader, table: str, where: str) -> int | None:
    """One COUNT, or None when the server would not say.

    A failure here is not the page's failure — the rows and the total are
    already in hand — so it is logged and the sentence is left off rather than
    turned into a problem.
    """
    statement = f"SELECT COUNT(*) FROM {table}{f' WHERE {where}' if where else ''};"
    try:
        answer = sql.query("characters", statement).strip()
    except Exception as exc:  # noqa: BLE001
        logger.info(f"could not ask {statement}: {exc}")
        return None
    return int(answer) if answer.isdigit() and int(answer) > 0 else None


def has_registry(entry: CatalogEntry) -> bool:
    """Whether this tree has a second bot signal to attribute a row to (8.5b).

    The split is worth showing only where two signals can disagree. Of the four
    games, only WotLK's AzerothCore carries the playerbots registry; the three
    CMaNGOS trees have the account prefix and nothing else, and on m910q's TBC
    install the schema list read exactly `characters, logs, realmd, mangos, sys,
    mysql, performance_schema` on 2026-09-07 — no `playerbots` anywhere.

    One definition, read by both the SQL that attributes a row and the sentence
    that reports the attribution, because two independent readings of the same
    catalog field are two things that can drift apart.
    """
    ops = entry.observability
    if ops is None:
        return False
    registry = ops.bots.registry
    return registry is not None and registry.database in entry.schema_map()


def _registry_clause(entry: CatalogEntry, ops: object) -> str:
    """True of a character whose account the playerbots registry owns.

    `1 = 0` where there is no registry, so the CASE still parses and every row
    is attributed to the prefix — which is the truth on a tree that has only
    that signal.

    This is the ATTRIBUTION clause, and it is the one that is allowed to
    degenerate to a constant. `dbreads.bot_clause` is the IDENTITY clause and is
    not: the Rust prior art recorded an incident on 2026-08-01 where the
    identity clause degraded to `0 = 1` on a registry-less tree and reported a
    server full of bots as having none (`botid.rs:178-190`). The two must stay
    on their own sides of that line; on TBC, measured on 2026-09-07, the
    identity clause has one arm and answers 900 while this one reads `1 = 0`.
    """
    registry = ops.bots.registry  # type: ignore[attr-defined]
    schemas = entry.schema_map()
    if not has_registry(entry):
        return "1 = 0"
    account = ops.characters.account  # type: ignore[attr-defined]
    types = ", ".join(str(t) for t in registry.types)
    return (
        f"{account} IN (SELECT {registry.account_column} FROM "
        f"{schemas[registry.database]}.{registry.table} "
        f"WHERE {registry.type_column} IN ({types}))"
    )


def _three(raw: str) -> tuple[int, int, int] | None:
    rows = [line for line in raw.splitlines() if line.strip()]
    if len(rows) != 1:
        return None
    fields = rows[0].split("\t")
    if len(fields) != 3:
        return None
    out: list[int] = []
    for value in (f.strip() for f in fields):
        # `SUM()` over no rows is SQL NULL, which is a zero here: a server with
        # no bots at all is a real state, not a broken query.
        if value in ("NULL", ""):
            out.append(0)
        elif value.lstrip("-").isdigit():
            out.append(int(value))
        else:
            return None
    return out[0], out[1], out[2]


def _rows(raw: str) -> tuple[list[Bot], list[tuple[str, int]]] | str:
    """The page's rows and their keys, or the sentence explaining why neither.

    The guid comes back beside each row and is not shown: it is the second half
    of the cursor, and reading it here is what stops the caller having to ask
    for it again.
    """
    bots: list[Bot] = []
    keys: list[tuple[str, int]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        if (
            len(fields) != 5
            or not fields[1].strip().lstrip("-").isdigit()
            or not fields[4].strip().isdigit()
        ):
            return f"a bot row came back as {line.strip()!r}"
        name = fields[0].strip()
        bots.append(
            Bot(
                name=name,
                level=int(fields[1]),
                online=fields[2].strip() == "1",
                source=fields[3].strip(),
            )
        )
        keys.append((name, int(fields[4])))
    return bots, keys
