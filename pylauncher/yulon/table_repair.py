"""Repair a database table a crash left damaged (T822).

MariaDB marks a MyISAM or Aria table crashed when a write was cut off, and the world
then cannot read the characters. The reported case (WoW Tortoise, 2026-10-28) was
`ERROR 1194 (HY000) at line 1: Table 'characters' is marked as crashed and should be
repaired`; the player fixed it by hand in HeidiSQL and asked where the button was.

This module is family-neutral: it knows the error wording, the two engines MariaDB
can repair, and the order the press takes. Which container, which client and which
schemas belong to the caller, as `TableRepair`'s seams.

**The ordering is the safety property.** The hold, then the engine read, then a
backup with the damaged table LEFT OUT (a dump that reads it fails on the same
error), then a copy of the table's own files taken while a read lock holds that
table still, and only then `REPAIR TABLE`. A press that refuses at any of those
steps has written nothing to the table.

**Never a table it was not asked about.** Every statement names one (schema,
table) pair from `crashed_tables()`, and both halves have already passed
`_IDENTIFIER`: no quote, backtick, space, `;` or `-` can reach SQL, and every
statement still back-quotes the name.

**Never `USE_FRM`.** MariaDB documents it as able to destroy an index beyond
recovery; the plain repair, then a check, then ONE `EXTENDED` repair is the whole
of what this offers.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, TypeVar

from yulon import docker
from yulon.server_build_presses import MAINTENANCE_TAB

logger = logging.getLogger(__name__)

_IDENTIFIER = re.compile(r"[A-Za-z0-9_$]{1,64}")
"""The whole injection defence for a name read out of a program's own words.

MariaDB's schema and table names are `[0-9A-Za-z_$]`, at most 64 characters, and a
real one never needs a quote or a backtick. Anything else is not escaped: the name
is DROPPED and the press refuses. Escaping would have to be right on every SQL mode.

The same rule validates what `information_schema` returns before it reaches SQL:
a schema is a name on the server's disk, not something this module wrote.
"""

_EXTENSION = re.compile(r"\.(?:MYI|MYD|MAI|MAD|frm)$", re.IGNORECASE)

_NAMED = (
    re.compile(
        r"Table '(?P<name>[^']{1,200})' is marked as crashed and should be repaired",
        re.IGNORECASE,
    ),
    re.compile(
        r"Table '(?P<name>[^']{1,200})' is marked as crashed and last "
        r"\(automatic\?\) repair failed",
        re.IGNORECASE,
    ),
    re.compile(
        r"Incorrect key file for table '(?P<name>[^']{1,200})'; try to repair it",
        re.IGNORECASE,
    ),
)
"""The three wordings that NAME the table, searched in this order but reported by position."""

_UNNAMED = re.compile(
    r"Got error (?:126|127|132|134|144|145)\b.*from storage engine",
    re.IGNORECASE | re.DOTALL,
)
"""A storage-engine error with no table in it: the card says Yu'lon cannot tell which one."""

MAX_TABLES = 8
"""At most this many tables from one message: a sentence, not a database listing."""

REPAIRABLE = frozenset({"MYISAM", "ARIA"})
"""The engines `REPAIR TABLE` can repair here (Aria is MariaDB's crash-safe MyISAM heir)."""

REPAIR_HOLD_REASON = (
    "A damaged database table is being repaired on this server, so nothing may start, stop "
    "or recreate it until the repair has finished. Wait for it to finish, then try again. "
    "Nothing was started or stopped."
)
REPAIR_PRESS = "Repair the damaged table"

REPAIR_TABLES_LABEL = "Repair the damaged table…"
REPAIR_TABLES_LABEL_MANY = "Repair the damaged tables…"


@dataclass(frozen=True)
class CrashedTable:
    """One table an error named, as the schema it was resolved to and its name."""

    db: str
    table: str
    bare: bool = field(default=False, compare=False)
    """The error named only the table (MariaDB's usual wording), not its schema.

    `db` is then the statement's first schema as a provisional answer for the card;
    the press resolves it against `information_schema` before it writes anything.
    Excluded from equality so a test can compare the pair it expected.
    """

    @property
    def qualified(self) -> str:
        return f"{self.db}.{self.table}"

    @property
    def sql_name(self) -> str:
        return f"`{self.db}`.`{self.table}`"


@dataclass(frozen=True)
class Crashed:
    """What one error text named, and whether it named nothing this module could use."""

    tables: tuple[CrashedTable, ...] = ()
    unnamed: bool = False


def crashed_tables(text: str, schemas: Sequence[str]) -> Crashed:
    """Every table `text` names as crashed, and whether an error named none of them.

    `schemas` is what the failing statement touched, in its own order: the first is
    the provisional schema for a bare table name (MariaDB usually prints only the
    table), and a qualified name outside the list is dropped rather than repaired.
    """
    found: list[tuple[int, str]] = []
    for pattern in _NAMED:
        for match in pattern.finditer(text):
            found.append((match.start(), match.group("name")))
    found.sort(key=lambda item: item[0])
    tables: list[CrashedTable] = []
    seen: set[str] = set()
    unnamed = bool(_UNNAMED.search(text))
    for _, raw in found:
        table = _table_from(raw, schemas)
        if table is None:
            unnamed = True
            continue
        if table.qualified in seen:
            continue
        seen.add(table.qualified)
        tables.append(table)
        if len(tables) == MAX_TABLES:
            break
    return Crashed(tuple(tables), unnamed)


def _table_from(raw: str, schemas: Sequence[str]) -> CrashedTable | None:
    """One name out of an error, or None if it is not a name this module may use."""
    if not schemas:
        return None
    name = raw.replace("\\", "/").strip()
    name = _EXTENSION.sub("", name)
    if name.startswith("./"):
        name = name[2:]
    name = name.strip("`")
    bare = "/" not in name and "." not in name
    if "/" in name:
        parts = [part.strip("`") for part in name.split("/") if part]
        if len(parts) < 2:
            return None
        db, table = parts[-2], parts[-1]
    elif "." in name:
        db, table = (part.strip("`") for part in name.split(".", 1))
    else:
        db, table = schemas[0], name
    if not _IDENTIFIER.fullmatch(db) or not _IDENTIFIER.fullmatch(table):
        return None
    if db not in schemas:
        return None
    return CrashedTable(db, table, bare=bare)


def merge_tables(
    first: Sequence[CrashedTable], second: Sequence[CrashedTable]
) -> tuple[CrashedTable, ...]:
    """Both lists as one, in order, without the same (schema, table) twice."""
    seen: set[str] = set()
    merged: list[CrashedTable] = []
    for table in (*first, *second):
        if table.sql_name in seen:
            continue
        seen.add(table.sql_name)
        merged.append(table)
    return tuple(merged)


DAMAGED_ATTRIBUTE = "damaged_tables"
"""What a refusal carries its damaged tables on, so the view can offer the press (T839).

An attribute and not an exception class: the refusal crosses `catalog/` (an
`InstallerError` wrapped by the rebuild's rollback) and the controller packages (a
`MaintenanceError`), and neither may import the other's types. `damaged_in()` reads it
anywhere on the exception's cause chain.
"""

_ErrorT = TypeVar("_ErrorT", bound=BaseException)


def mark_damaged(error: _ErrorT, tables: Sequence[CrashedTable]) -> _ErrorT:
    """`error`, carrying `tables` for `damaged_in()`."""
    setattr(error, DAMAGED_ATTRIBUTE, tuple(tables))
    return error


def damaged_in(exc: BaseException | None) -> tuple[CrashedTable, ...]:
    """The damaged tables a refusal (or anything it was raised from) carries; () when none."""
    seen: set[int] = set()
    current = exc
    while current is not None and id(current) not in seen and len(seen) < 32:
        seen.add(id(current))
        found = getattr(current, DAMAGED_ATTRIBUTE, ())
        if found:
            return tuple(table for table in found if isinstance(table, CrashedTable))
        current = current.__cause__ or current.__context__
    return ()


def repairable_only(
    tables: Sequence[CrashedTable], schemas: Sequence[str]
) -> tuple[CrashedTable, ...]:
    """The tables the Repair press may touch: those in its own `schemas` (T822, T839)."""
    return tuple(table for table in tables if table.db in schemas)


def damaged_in_dump(text: str, database: str, schemas: Sequence[str]) -> tuple[CrashedTable, ...]:
    """The tables a failed dump of `database` names as crashed (T839).

    mysqldump prints `Got error: 1194: Table 'character_honor_cp' is marked as crashed and
    should be repaired when using LOCK TABLES`: the bare table, and a dump of one schema
    reads only that schema's tables, so a bare name is `database`'s own -- known, not
    provisional. A qualified name must still be one of `schemas` (or `database`).
    """
    ordered = tuple(dict.fromkeys(name for name in (database, *schemas) if name))
    if not ordered:
        return ()
    return tuple(
        CrashedTable(table.db, table.table) for table in crashed_tables(text, ordered).tables
    )


def damaged_refusal(
    tables: Sequence[CrashedTable],
    *,
    repairable: Sequence[str],
    what: str = "the update",
    again: str = "update again",
    nothing_changed: bool = True,
    ended: str = "did not start",
) -> str:
    """The plain sentence for a press a damaged table stopped (T839); names every table.

    Only a table in `repairable` (the Repair press's own schemas) is sent to that press;
    any other -- the world database's -- is pointed at a backup instead.
    """
    names = ", ".join(table.qualified for table in tables)
    if len(tables) == 1:
        lead = f"Table {names} is damaged (a crash left it half-written)"
    else:
        lead = f"Tables {names} are damaged (a crash left them half-written)"
    said = f"{lead}, so a complete copy of your databases cannot be made and {what} {ended}."
    fixable = repairable_only(tables, repairable)
    other = [table for table in tables if table not in fixable]
    if other:
        dbs = ", ".join(dict.fromkeys(table.db for table in other))
        said += (
            f" Repair on the server card cannot fix {dbs}: restore it from a backup on the"
            " Maintenance tab, or ask for help."
        )
        if fixable:
            fixed = ", ".join(table.qualified for table in fixable)
            said += f" Use {repair_button_label(fixable)} on the server card for {fixed}."
        said += f" Then {again}."
    else:
        said += f" Use {repair_button_label(tables)} on the server card, then {again}."
    return f"{said} Nothing was changed." if nothing_changed else said


CHECK_SECONDS = 60
"""How long the pre-check's `CHECK TABLE` may run before the server aborts it (T839)."""


def _check_targets_statement(schemas: Sequence[str]) -> str:
    known = ", ".join(f"'{name}'" for name in schemas)
    # A table the server cannot open reports no engine, and that is the table wanted most.
    return (
        "SELECT TABLE_SCHEMA, TABLE_NAME FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA IN ({known}) AND TABLE_TYPE = 'BASE TABLE' "
        "AND (ENGINE IS NULL OR UPPER(ENGINE) IN ('MYISAM', 'ARIA'));"
    )


def check_statement(tables: Sequence[CrashedTable]) -> str:
    """One bounded `CHECK TABLE … FAST QUICK` over `tables` (all already validated)."""
    names = ", ".join(table.sql_name for table in tables)
    return f"SET SESSION max_statement_time = {CHECK_SECONDS};\n" f"CHECK TABLE {names} FAST QUICK;"


_GOOD_STATUS = frozenset({"ok", "table is already up to date"})


def damaged_in_check(raw: str, schemas: Sequence[str]) -> tuple[CrashedTable, ...]:
    """The tables `CHECK TABLE`'s rows (`Table, Op, Msg_type, Msg_text`) call damaged.

    Damaged is an `error` row, or no `status` row saying OK. A `warning` alone ("1 client
    is using or hasn't closed the table properly") is a running server, not damage.
    """
    rows: dict[str, list[tuple[str, str]]] = {}
    for line in raw.splitlines():
        fields = line.split("\t")
        if len(fields) < 4:
            continue
        rows.setdefault(fields[0].strip(), []).append(
            (fields[2].strip().lower(), fields[3].strip().lower())
        )
    damaged: list[CrashedTable] = []
    for name, said in rows.items():
        bad = any(kind == "error" for kind, _ in said) or not any(
            kind == "status" and text in _GOOD_STATUS for kind, text in said
        )
        if not bad:
            continue
        table = _table_from(name, schemas)
        if table is None or table.bare:
            logger.warning(f"CHECK TABLE called a table damaged that Yu'lon cannot name: {name}")
            continue
        damaged.append(table)
        if len(damaged) == MAX_TABLES:
            break
    return tuple(damaged)


def find_damaged(query: Callable[[str], str], schemas: Sequence[str]) -> tuple[CrashedTable, ...]:
    """Ask the server which MyISAM/Aria tables of `schemas` are damaged; read-only (T839).

    Two reads: the tables a crash can mark (`information_schema`), then one `CHECK TABLE
    … FAST QUICK` over them, which checks only tables not closed properly and is bounded
    by `CHECK_SECONDS`. Raises whatever `query` raises; the caller decides what a check
    that could not run means.
    """
    wanted = tuple(name for name in schemas if _IDENTIFIER.fullmatch(name))
    if not wanted:
        return ()
    targets: list[CrashedTable] = []
    for line in query(_check_targets_statement(wanted)).splitlines():
        fields = line.split("\t")
        if len(fields) < 2:
            continue
        db, table = fields[0].strip(), fields[1].strip()
        if db in wanted and _IDENTIFIER.fullmatch(db) and _IDENTIFIER.fullmatch(table):
            targets.append(CrashedTable(db, table))
    if not targets:
        return ()
    return damaged_in_check(query(check_statement(targets)), wanted)


def damaged_sentence(crashed: Crashed) -> str:
    """The Server card's plain line for a damaged table; the raw SQL stays in the log."""
    if not crashed.tables:
        return (
            "a database table is damaged, most likely from the computer stopping suddenly; "
            "Yu'lon cannot tell which one, so press Save logs for support… and ask for help"
        )
    names = ", ".join(table.qualified for table in crashed.tables)
    if len(crashed.tables) == 1:
        lead = f"a database table is damaged ({names})"
    else:
        lead = f"database tables are damaged ({names})"
    return (
        f"{lead}, most likely from the computer stopping suddenly, so characters cannot " "be read"
    )


def repair_banner_text(tables: Sequence[CrashedTable]) -> str:
    """The amber banner's sentence, singular or plural."""
    if len(tables) == 1:
        return "The damaged table can be repaired. Yu'lon backs up the database first."
    return "The damaged tables can be repaired. Yu'lon backs up the database first."


def repair_button_label(tables: Sequence[CrashedTable]) -> str:
    return REPAIR_TABLES_LABEL if len(tables) == 1 else REPAIR_TABLES_LABEL_MANY


def confirmation(tables: Sequence[CrashedTable]) -> str:
    """The question asked before anything is written; No is the default."""
    names = ", ".join(table.qualified for table in tables)
    one = len(tables) == 1
    dbs = list(dict.fromkeys(table.db for table in tables))
    db = dbs[0] if len(dbs) == 1 else "its databases"
    subject = "the damaged table" if one else "the damaged tables"
    was = "was" if one else "were"
    only = "this table" if one else "these tables"
    files = "the damaged table's files" if one else "the damaged tables' files"
    return (
        f"Repair {subject}? {names} in {db} {was} damaged, most likely when the computer "
        f"stopped suddenly. Yu'lon first saves a backup of {db} and a copy of {files}, then "
        f"repairs only {only}. Players can stay connected; restart the server afterwards. A "
        "repair can lose rows it cannot read; the copy keeps them for a specialist. Repair "
        "now?"
    )


COPY_FAILED_QUESTION = (
    "Yu'lon could not copy every damaged table's files, so there is no complete extra copy "
    "of them. Repair the tables anyway? The backup leaves them out, so a repair can lose "
    "rows nothing can bring back."
)
COPY_MAY_BE_INCONSISTENT_QUESTION = (
    "Yu'lon could not hold the damaged tables still while it copied their files, so the "
    "copy may be inconsistent. Repair the tables anyway? The backup leaves them out, so a "
    "repair can lose rows nothing can bring back."
)
NOTHING_REPAIRED_BACKUP_KEPT = "Nothing was repaired; the backup is kept."
BACKUP_FAILED = "Yu'lon could not take the backup, so nothing was repaired."
REPAIR_FAILED = "The repair did not finish. The backup is kept."


def _lock_statements(tables: Sequence[_Resolved]) -> tuple[str, ...]:
    """The two ways to stop the server writing these tables, best first (T822).

    `FLUSH TABLES … WITH READ LOCK` also flushes each table's own files, which is what makes
    the copy a snapshot of the disk rather than of memory; it can be refused for a table it
    cannot open, so a plain `LOCK TABLES … READ` is tried next. Both names are already
    through `_IDENTIFIER` and back-quoted, so neither text can carry anything else.
    """
    names = ", ".join(one.sql_name for one in tables)
    return (f"FLUSH TABLES {names} WITH READ LOCK;", f"LOCK TABLES {names} READ;")


@dataclass(frozen=True)
class RepairReport:
    """What a press did, and the one sentence the player reads."""

    sentence: str
    detail: str = ""
    """The program's own words, for the log and Details; never on the player's line."""
    repaired: tuple[CrashedTable, ...] = ()
    still_damaged: tuple[CrashedTable, ...] = ()
    found_more: tuple[CrashedTable, ...] = ()
    """Tables another table's damage kept out of the backup: the next press includes them."""
    backup: object | None = None
    copy_dir: Path | None = None
    refused: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.repaired) and not self.still_damaged


class RepairMysql(Protocol):
    """The halves of the database seam this press needs."""

    def query(self, sql: str) -> str:
        """Run one read and return its tab-separated rows, no header."""

    def execute(self, sql: str) -> None:
        """Run one statement. Raises on failure."""

    def read_lock(self, *statements: str) -> AbstractContextManager[None]:
        """Hold a table read lock from the first of `statements` the server grants.

        One session keeps it until the block ends and is released on every path, including
        an exception inside the block. Raises when it cannot be granted.
        """


class Backup(Protocol):
    def __call__(
        self,
        *,
        only: Sequence[str],
        label: str,
        ignore_tables: Mapping[str, Sequence[str]],
    ) -> object:
        """Dump `only` into the backups folder, leaving `ignore_tables` out."""


@dataclass(frozen=True)
class _Resolved:
    db: str
    table: str
    engine: str

    @property
    def qualified(self) -> str:
        return f"{self.db}.{self.table}"

    @property
    def sql_name(self) -> str:
        return f"`{self.db}`.`{self.table}`"

    @property
    def crashed(self) -> CrashedTable:
        return CrashedTable(self.db, self.table)


_CopyFiles = Callable[[Sequence[CrashedTable], str, Mapping[str, str]], Path]
"""Copy each table's files into the backups folder; `engines` is keyed by `sql_name`."""


@dataclass(frozen=True)
class TableRepair:
    """The press's seams, bound to one install by its own factory."""

    schemas: tuple[str, ...]
    mysql: RepairMysql
    hold: Callable[[], AbstractContextManager[None]]
    backup: Backup
    copy_files: _CopyFiles

    def repair(
        self,
        tables: Sequence[CrashedTable],
        *,
        confirm_without_copy: Callable[[str], bool],
    ) -> RepairReport:
        """`repair()` with this install's seams."""
        return repair(
            tables,
            schemas=self.schemas,
            mysql=self.mysql,
            hold=self.hold,
            backup=self.backup,
            copy_files=self.copy_files,
            confirm_without_copy=confirm_without_copy,
        )


def repair(
    tables: Sequence[CrashedTable],
    *,
    schemas: Sequence[str],
    mysql: RepairMysql,
    hold: Callable[[], AbstractContextManager[None]],
    backup: Backup,
    copy_files: _CopyFiles,
    confirm_without_copy: Callable[[str], bool],
) -> RepairReport:
    """Back up, copy, and repair exactly the tables `tables` names, in that order.

    Raises nothing for a refusal: every way of not repairing is a report with the
    one sentence to show, so the press has a single shape to display. A hold that
    cannot be taken and an engine this MariaDB cannot repair refuse before the
    first write.
    """
    # T839: a name outside `schemas` (the world database's) is dropped, never repaired.
    wanted = _validated(repairable_only(tables, schemas))
    if not wanted:
        return RepairReport(
            sentence="Yu'lon cannot tell which table to repair; nothing was changed.",
            refused=True,
        )
    try:
        with hold():
            resolved, refusal = _engines(wanted, schemas=schemas, mysql=mysql)
            if refusal is not None:
                return refusal
            backups, refusal = _backed_up(resolved, schemas=schemas, backup=backup)
            if refusal is not None:
                return refusal
            copy_dir, refusal = _copied_files(
                resolved,
                mysql=mysql,
                copy_files=copy_files,
                backup=backups,
                confirm_without_copy=confirm_without_copy,
            )
            if refusal is not None:
                return refusal

            repaired: list[CrashedTable] = []
            still: list[CrashedTable] = []
            for one in resolved:
                try:
                    mysql.execute(f"REPAIR TABLE {one.sql_name};")
                except Exception as exc:  # noqa: BLE001 - the backup stands, so report it
                    detail = _detail(exc)
                    logger.warning(f"the repair of {one.qualified} did not finish: {detail or exc}")
                    return RepairReport(
                        sentence=REPAIR_FAILED,
                        detail=detail,
                        repaired=tuple(repaired),
                        still_damaged=tuple(one.crashed for one in resolved[len(repaired) :]),
                        backup=backups,
                        copy_dir=copy_dir,
                        refused=True,
                    )
                if _checked(mysql, one):
                    repaired.append(one.crashed)
                    continue
                try:
                    mysql.execute(f"REPAIR TABLE {one.sql_name} EXTENDED;")
                except Exception as exc:  # noqa: BLE001 - the CHECK below says what is true
                    logger.warning(f"the extended repair of {one.qualified} did not finish: {exc}")
                if _checked(mysql, one):
                    repaired.append(one.crashed)
                else:
                    still.append(one.crashed)

            return RepairReport(
                sentence=_sentence(repaired, still, copy_dir),
                repaired=tuple(repaired),
                still_damaged=tuple(still),
                backup=backups,
                copy_dir=copy_dir,
            )
    except docker.ServerHeldError as exc:
        return RepairReport(sentence=str(exc), refused=True)


def _validated(tables: Sequence[CrashedTable]) -> tuple[CrashedTable, ...]:
    valid: list[CrashedTable] = []
    seen: set[str] = set()
    for table in tables:
        if not _IDENTIFIER.fullmatch(table.db) or not _IDENTIFIER.fullmatch(table.table):
            continue
        if table.sql_name in seen:
            continue
        seen.add(table.sql_name)
        valid.append(table)
    return tuple(valid)


def _engines(
    tables: Sequence[CrashedTable], *, schemas: Sequence[str], mysql: RepairMysql
) -> tuple[tuple[_Resolved, ...], RepairReport | None]:
    """Resolve every name and read its engine; a missing one or an unrepairable one refuses."""
    pairs = ", ".join(f"('{one.db}', '{one.table}')" for one in tables)
    names = ", ".join(f"'{one.table}'" for one in tables)
    known = ", ".join(f"'{name}'" for name in schemas)
    statement = (
        "SELECT TABLE_SCHEMA, TABLE_NAME, ENGINE FROM information_schema.TABLES "
        f"WHERE (TABLE_SCHEMA, TABLE_NAME) IN ({pairs}) "
        f"OR (TABLE_NAME IN ({names}) AND TABLE_SCHEMA IN ({known}));"
    )
    try:
        raw = mysql.query(statement)
    except Exception as exc:  # noqa: BLE001 - a read that failed refuses the press
        detail = _detail(exc)
        logger.warning(f"could not read the damaged tables' engines: {detail or exc}")
        return (), RepairReport(
            sentence="Yu'lon could not ask the database about the damaged table; nothing "
            "was changed.",
            detail=detail,
            refused=True,
        )
    rows = _rows(raw)
    resolved: list[_Resolved] = []
    seen: set[str] = set()
    for table in tables:
        exact = [row for row in rows if row[0] == table.db and row[1] == table.table]
        if table.bare:
            found = [row for row in rows if row[1] == table.table]
            if len(found) != 1:
                if found:
                    where = ", ".join(row[0] for row in found)
                    return (), RepairReport(
                        sentence=f"More than one database has a table called "
                        f"{table.table} ({where}), so Yu'lon cannot tell which one is "
                        "damaged; nothing was changed.",
                        refused=True,
                    )
                return (), _missing(table)
            exact = found
        if not exact:
            return (), _missing(table)
        db, name, engine = exact[0]
        if not _IDENTIFIER.fullmatch(db) or not _IDENTIFIER.fullmatch(name):
            return (), RepairReport(
                sentence="Yu'lon will not repair a table whose name it cannot read; "
                "nothing was changed.",
                refused=True,
            )
        if engine.strip().upper() not in REPAIRABLE:
            return (), RepairReport(
                sentence=f"{db}.{name} is stored in a way that cannot be repaired here "
                f"({engine or 'unknown'}); restore a backup on the {MAINTENANCE_TAB} tab, or "
                "ask for help.",
                refused=True,
            )
        one = _Resolved(db, name, engine)
        if one.sql_name not in seen:
            seen.add(one.sql_name)
            resolved.append(one)
    return tuple(resolved), None


def _missing(table: CrashedTable) -> RepairReport:
    return RepairReport(
        sentence=f"Yu'lon cannot find the table {table.qualified}; nothing was changed.",
        refused=True,
    )


def _backed_up(
    tables: Sequence[_Resolved], *, schemas: Sequence[str], backup: Backup
) -> tuple[object | None, RepairReport | None]:
    """Dump each schema with the damaged tables left out; a failed dump refuses."""
    groups: dict[str, list[str]] = {}
    for table in tables:
        groups.setdefault(table.db, []).append(table.table)
    reports: list[object] = []
    for db, names in groups.items():
        try:
            reports.append(
                backup(
                    only=[db],
                    label="before-repair",
                    ignore_tables={db: list(names)},
                )
            )
        except Exception as exc:  # noqa: BLE001 - a failed dump is a refusal
            text = f"{exc}\n{_detail(exc)}".strip()
            logger.warning(f"the backup before a repair did not finish: {text}")
            # T839: the dump was of `db` alone, so a bare name is `db`'s own table.
            found = damaged_in_dump(text, db, schemas)
            known = {table.sql_name for table in tables}
            more = tuple(one for one in found if one.sql_name not in known)
            if more:
                return reports, RepairReport(
                    sentence=f"Another table is damaged too: {more[0].qualified}. Press "
                    "Repair again to include it.",
                    detail=text,
                    found_more=more,
                    refused=True,
                )
            return reports, RepairReport(sentence=BACKUP_FAILED, detail=text, refused=True)
    return reports, None


def _copied_files(
    tables: Sequence[_Resolved],
    *,
    mysql: RepairMysql,
    copy_files: _CopyFiles,
    backup: object | None,
    confirm_without_copy: Callable[[str], bool],
) -> tuple[Path | None, RepairReport | None]:
    """Copy the damaged files while the server cannot write those tables (T822).

    A copy is only worth keeping while the table is still: `mysqld` keeps writing for as long
    as players are connected, and a copy of a file taken between two of its writes is a
    mixture of both. So a read lock is taken on ONE session first, held for exactly the copy,
    and released in a `finally` on every path. The backup this press just took deliberately
    leaves the damaged table out, so this copy is the only one that can still hold the rows a
    repair may not be able to read.

    A lock that cannot be taken does not stop the copy -- a copy that may be inconsistent is
    still better than none, and the player can say No next -- but it is never silent: the
    same question a failed copy asks is asked, and only a Yes repairs the table. The same
    holds for the copy itself failing: No stops with the backup kept, Yes repairs without it.

    Returns the folder, or `(None, a refusal)` when the player said No.
    """
    crashed = tuple(one.crashed for one in tables)
    engines = {one.sql_name: one.engine for one in tables}
    try:
        datadir = _datadir(mysql)
        if not datadir:
            raise ValueError("the database did not say where its files are")
    except Exception as exc:  # noqa: BLE001 - a read that failed is a copy that failed
        refusal = _copy_failed(exc, backup=backup, confirm_without_copy=confirm_without_copy)
        return None, refusal
    lock = mysql.read_lock(*_lock_statements(tables))
    held = True
    lock_detail = ""
    try:
        lock.__enter__()
    except Exception as exc:  # noqa: BLE001 - a lock that could not be taken is a warning
        held = False
        lock_detail = _detail(exc) or str(exc)
        logger.warning(f"the damaged tables could not be held still for the copy: {lock_detail}")
    copy_dir: Path | None = None
    failure: Exception | None = None
    try:
        copy_dir = copy_files(crashed, datadir, engines)
    except Exception as exc:  # noqa: BLE001 - every copy failure is the same question
        failure = exc
    finally:
        if held:
            try:
                lock.__exit__(None, None, None)
            except Exception as exc:  # noqa: BLE001 - the copy is out; releasing is best effort
                logger.warning(f"the table lock could not be released: {_detail(exc) or str(exc)}")
    if failure is not None:
        return None, _copy_failed(failure, backup=backup, confirm_without_copy=confirm_without_copy)
    if not held and not confirm_without_copy(COPY_MAY_BE_INCONSISTENT_QUESTION):
        return None, RepairReport(
            sentence=NOTHING_REPAIRED_BACKUP_KEPT,
            detail=lock_detail,
            backup=backup,
            refused=True,
        )
    return copy_dir, None


def _copy_failed(
    exc: Exception, *, backup: object | None, confirm_without_copy: Callable[[str], bool]
) -> RepairReport | None:
    """Ask about a copy that could not be taken; None means repair without one."""
    detail = _detail(exc) or str(exc)
    logger.warning(f"could not copy the damaged tables' files: {detail}")
    if confirm_without_copy(COPY_FAILED_QUESTION):
        return None
    return RepairReport(
        sentence=NOTHING_REPAIRED_BACKUP_KEPT,
        detail=detail,
        backup=backup,
        refused=True,
    )


def _datadir(mysql: RepairMysql) -> str:
    raw = mysql.query("SELECT @@datadir;")
    rows = [line.strip() for line in raw.splitlines() if line.strip()]
    return rows[-1] if rows else ""


def _checked(mysql: RepairMysql, table: _Resolved) -> bool:
    """Whether `CHECK TABLE` now says OK, in its last column of its last row."""
    try:
        raw = mysql.query(f"CHECK TABLE {table.sql_name};")
    except Exception as exc:  # noqa: BLE001 - a check that cannot run is not a good check
        logger.warning(f"could not check {table.qualified}: {exc}")
        return False
    rows = [line for line in raw.splitlines() if line.strip()]
    if not rows:
        return False
    fields = rows[-1].split("\t")
    return bool(fields) and fields[-1].strip().upper() == "OK"


def _rows(raw: str) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for line in raw.splitlines():
        fields = line.split("\t")
        if len(fields) >= 3:
            rows.append((fields[0].strip(), fields[1].strip(), fields[2].strip()))
    return rows


def _detail(exc: object) -> str:
    detail = getattr(exc, "detail", "")
    return str(detail) if detail else ""


def _sentence(
    repaired: Sequence[CrashedTable], still: Sequence[CrashedTable], copy_dir: Path | None
) -> str:
    if still:
        names = ", ".join(table.qualified for table in still)
        return (
            f"The repair did not fix {names}. Nothing else was changed; the backup and the "
            "copy of the damaged files are kept in the backups folder. Ask for help before "
            "you restore."
        )
    names = ", ".join(table.qualified for table in repaired)
    tail = " Press **Restart** so the server reads it again."
    if copy_dir is None:
        return (
            f"Repaired {names}. The backup is in the Backups list, but the copy of the "
            f"damaged files could not be made.{tail}"
        )
    return f"Repaired {names}. The backup is in the Backups list.{tail}"
