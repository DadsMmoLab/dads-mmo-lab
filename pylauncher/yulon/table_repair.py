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
error), then a copy of the table's own files, and only then `REPAIR TABLE`. A
press that refuses at any of those steps has written nothing to the table.

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
from typing import Protocol

from yulon import docker

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

    def plus(self, other: Crashed) -> Crashed:
        """Both readings, deduped in order: a press keeps what a backup's failure found."""
        seen = {table.sql_name for table in self.tables}
        extra = tuple(table for table in other.tables if table.sql_name not in seen)
        return Crashed(self.tables + extra, self.unnamed or other.unnamed)


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
    "Yu'lon could not copy the damaged table's files, so there is no extra copy of them. "
    "Repair the table anyway? The backup is already saved, and a repair can lose rows it "
    "cannot read."
)
NOTHING_REPAIRED_BACKUP_KEPT = "Nothing was repaired; the backup is kept."
BACKUP_FAILED = "Yu'lon could not take the backup, so nothing was repaired."
REPAIR_FAILED = "The repair did not finish. The backup is kept."
CHECK_UNREADABLE = "Yu'lon could not check the repaired table."


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
    """The two halves of the database seam this press needs."""

    def query(self, sql: str) -> str:
        """Run one read and return its tab-separated rows, no header."""

    def execute(self, sql: str) -> None:
        """Run one statement. Raises on failure."""


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
    wanted = _validated(tables)
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
            copy_dir: Path | None
            try:
                datadir = _datadir(mysql)
                if not datadir:
                    raise ValueError("the database did not say where its files are")
                copy_dir = copy_files(
                    tuple(one.crashed for one in resolved),
                    datadir,
                    {one.sql_name: one.engine for one in resolved},
                )
            except Exception as exc:  # noqa: BLE001 - every copy failure is the same question
                detail = _detail(exc)
                logger.warning(f"could not copy the damaged table's files: {detail or exc}")
                if not confirm_without_copy(COPY_FAILED_QUESTION):
                    return RepairReport(
                        sentence=NOTHING_REPAIRED_BACKUP_KEPT,
                        detail=detail,
                        backup=backups,
                        refused=True,
                    )
                copy_dir = None

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
                f"({engine or 'unknown'}); restore a backup on the Maintenance tab, or ask "
                "for help.",
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
            found = crashed_tables(text, schemas).tables
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
