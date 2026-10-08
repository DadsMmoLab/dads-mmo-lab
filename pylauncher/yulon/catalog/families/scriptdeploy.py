"""Lua scripts laid into mod-ale's script folder, and the SQL they need, checked (T553, P2b).

An AzerothCore entry whose game is partly written in Lua (WoW Unbound's Mentor,
its talent bridge, its talent data) names the scripts in its catalog block
(`AzerothCoreData.lua_scripts`): a folder or a file inside a checkout the install
cloned, and the folder under `env/dist/etc/modules/lua_scripts/` it goes to. The
world reads that folder only when it starts, so the scripts are laid before the
world first starts, again before every rebuild, and again when an update has moved
the checkout they come from (`AzerothCoreInstaller`'s hooks say where).

**Only Yu'lon's own copies are ever replaced.** Each file written is recorded by
its SHA-256 in `RECORD_FILE`, in the script folder. A file on disk is replaced only
when its bytes are still the ones recorded; a file somebody changed by hand (people
did edit Unbound's `unbound_talent_data.lua`), or one that was there before Yu'lon
wrote it, is left as it is and the press says so. A record that cannot be read
counts as empty, which keeps every differing file: when in doubt, nothing is lost.
A file the checkout no longer ships is removed only under the same rule.

The record also names, under `pending`, the one file a press is about to write and
the digest it will have, saved BEFORE the write: a crash between the write and the
final record leaves bytes that are claimed afterwards exactly when they are those
bytes (`_is_ours_by_pending`), so the next press updates them instead of calling
them the player's.

The model is the ALE manifests' `deploy` step (`apply.Applier._deploy()`), which
copies a module's `lua_scripts` into the same folder; what is added here is the
record, because these files come back on every rebuild and update rather than
once per module install.

The SQL checks (`AzerothCoreData.sql_checks`) are read-only counts asked after the
import has applied the modules' SQL (`AC_UPDATES_ALLOWED_MODULES=all` with
`./modules` mounted, `base.yml.tmpl`): a count that falls short, or that cannot be
read, refuses the press before the world starts. Since T555 T3 (Unbound issue #71)
`information_schema` is asked first which of the tables are there, and only those
are counted: a table the import never made is said to be missing, by name, and is
never reported as holding 0 rows.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from yulon import docker, server_build_presses
from yulon.catalog.catalog import LUA_SCRIPTS_DIR, LuaScripts, SqlCheck
from yulon.catalog.installer import InstallerError, SelfExplainedError
from yulon.log import get_logger
from yulon.manifest import Db

logger = get_logger(__name__)

RECORD_FILE = ".yulon-lua-scripts.json"
"""Which script files Yu'lon wrote, and the SHA-256 of each, in `LUA_SCRIPTS_DIR`."""

SCRIPT_MODE = 0o644
"""Readable by everyone: the world runs as the image's `acore` user, not as the host's."""

NO_SUCH_TABLE = "ERROR 1146"
"""MySQL's "Table ... doesn't exist": the module's SQL never made it -- a missing table, not 0."""


@dataclass(frozen=True)
class _Planned:
    """One file a press lays: where it comes from, where it goes, its bytes."""

    src: Path
    target: Path
    rel: str
    """`target` relative to the server dir, POSIX: the record's key and the log's name."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def record_path(server_dir: Path) -> Path:
    return server_dir.joinpath(*PurePosixPath(LUA_SCRIPTS_DIR).parts, RECORD_FILE)


def read_record(server_dir: Path) -> dict[str, str]:
    """The record, or empty when there is none or it cannot be read (then nothing is replaced)."""
    return _read_record(server_dir)[0]


def _read_record(server_dir: Path) -> tuple[dict[str, str], str, bool]:
    """The record, why it cannot be read ("" when it can), and whether it held `pending`.

    A record that held `pending` is saved again by the press that read it, so the
    entry never outlives that press (T563 cold review): left on disk, a no-op press
    would keep it, and a file the player put there later, with those very bytes,
    would be adopted as Yu'lon's and removed or replaced.
    """
    path = record_path(server_dir)
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, "", False
    except (OSError, ValueError) as exc:
        logger.warning(f"{path} could not be read ({exc}); no script there counts as Yu'lon's")
        return {}, str(exc), False
    files = parsed.get("files") if isinstance(parsed, dict) else None
    if not isinstance(files, dict):
        return {}, "it holds no list of files", False
    kept = {
        k: v
        for k, v in files.items()
        if isinstance(k, str) and isinstance(v, str) and _inside_the_script_dir(k)
    }
    pending = parsed.get("pending")
    if isinstance(pending, dict):
        for rel, digest in pending.items():
            if isinstance(rel, str) and isinstance(digest, str) and _inside_the_script_dir(rel):
                if _is_ours_by_pending(server_dir, rel, digest):
                    kept[rel] = digest
    return kept, "", bool(pending)


def _is_ours_by_pending(server_dir: Path, rel: str, digest: str) -> bool:
    """Is the file at `rel` the one a press that never finished was writing (T563)?

    A press records the digest it is about to write BEFORE it writes the file, so
    a crash between the two leaves bytes on disk that the record only knows as
    "pending". They are Yu'lon's exactly when they are those bytes; anything else
    at that name is not claimed, and a link never is.
    """
    path = server_dir.joinpath(*PurePosixPath(rel).parts)
    if path.is_symlink() or _through_a_link(server_dir, path) is not None:
        return False
    try:
        return _sha(path.read_bytes()) == digest
    except OSError:
        return False


def _inside_the_script_dir(rel: str) -> bool:
    """Is a record key a plain relative path strictly below `LUA_SCRIPTS_DIR`?

    The record is a file anyone can edit, so a key is a path this module may
    delete only when it could have written it (Codex, both reviews): no absolute
    path, no `..`, no backslash, and inside the script folder.
    """
    path = PurePosixPath(rel)
    root = PurePosixPath(LUA_SCRIPTS_DIR).parts
    return (
        "\\" not in rel
        and not path.is_absolute()
        and ".." not in path.parts
        and len(path.parts) > len(root)
        and path.parts[: len(root)] == root
    )


def _write_record(
    server_dir: Path, files: dict[str, str], pending: dict[str, str] | None = None
) -> None:
    """Save the record; with nothing left in it, remove the file (T563).

    `pending` is the one file about to be written and the digest it will have:
    written first, so a crash between the file and the final record leaves it
    claimable (see `_is_ours_by_pending`).
    """
    path = record_path(server_dir)
    if not files and not pending:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    body: dict[str, object] = {"version": 1, "files": dict(sorted(files.items()))}
    if pending:
        body["pending"] = dict(sorted(pending.items()))
    text = json.dumps(body, indent=2) + "\n"
    _publish(path, text.encode("utf-8"))


def _publish(target: Path, data: bytes) -> None:
    """Write `data` at `target` whole: a temp sibling, then a rename over the name."""
    tmp = target.with_name(f".{target.name}.yulon-new")
    try:
        # Whatever sits at the temp name is removed, not written through: a link
        # planted there (T563) would send the bytes, and the chmod, to its target.
        # `O_EXCL` then refuses a name that reappeared (on POSIX it refuses a link
        # too, even a dangling one); `O_NOFOLLOW` is belt and braces where there is
        # one, and Windows has none.
        tmp.unlink(missing_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(tmp, flags | getattr(os, "O_BINARY", 0), SCRIPT_MODE)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(tmp, SCRIPT_MODE)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def _through_a_link(server_dir: Path, target: Path) -> Path | None:
    """The first folder between the server dir and `target` that is a link, if any.

    Never written through (T530's rule for module files): a link sends the write
    to somebody else's folder.
    """
    here = server_dir
    for part in target.relative_to(server_dir).parts[:-1]:
        here = here / part
        if here.is_symlink():
            return here
    return None


def _link_refusal(server_dir: Path, link: Path, remedy: str) -> InstallerError:
    """The press stops at a link where the scripts go: writing through it writes elsewhere."""
    return SelfExplainedError(
        f"{link.relative_to(server_dir).as_posix()} is a link, so the Lua scripts were not laid "
        "through it and the server would start without them. Nothing was written there. Make "
        f"it a plain folder, then press {remedy}."
    )


def missing_sources(server_dir: Path, specs: Sequence[LuaScripts]) -> tuple[str, ...]:
    """The `src` of each spec that is not in the server dir as a file or folder."""
    return tuple(
        spec.src
        for spec in specs
        if not (server_dir / spec.src).is_dir() and not (server_dir / spec.src).is_file()
    )


def _linked_source(server_dir: Path, src: Path) -> Path | None:
    """The first link at or under a Lua source: the source, a folder above it, or inside it."""
    link = src if src.is_symlink() else _through_a_link(server_dir, src / "_")
    if link is not None:
        return link
    if not src.is_dir():
        return None
    for root, dirs, files in os.walk(src, followlinks=False):
        dirs.sort()
        for name in [*dirs, *sorted(files)]:
            if (Path(root) / name).is_symlink():
                return Path(root) / name
    return None


def linked_sources(server_dir: Path, specs: Sequence[LuaScripts]) -> tuple[str, ...]:
    """The first link (relative to the server dir) in each present source that has one."""
    found: list[str] = []
    for spec in specs:
        src = server_dir / spec.src
        if not src.is_dir() and not src.is_file():
            continue
        link = _linked_source(server_dir, src)
        if link is not None:
            found.append(link.relative_to(server_dir).as_posix())
    return tuple(found)


def _plan(server_dir: Path, specs: Sequence[LuaScripts], remedy: str) -> list[_Planned]:
    """Every file the specs lay; a link anywhere in a source refuses the whole plan."""
    planned: list[_Planned] = []
    for spec in specs:
        src = server_dir / spec.src
        dest = server_dir / spec.dest.rstrip("/")
        link = _linked_source(server_dir, src)
        if link is not None:
            raise SelfExplainedError(
                f"{link.relative_to(server_dir).as_posix()} is a link, so Yu'lon did not read "
                "the Lua scripts through it and the server would start without them. Nothing "
                f"was changed. Put a plain copy of the file or folder there, then press {remedy}."
            )
        if src.is_file():
            pairs = [(src, dest / src.name)]
        elif src.is_dir():
            pairs = []
            for root, _dirs, files in os.walk(src, followlinks=False):
                here = Path(root)
                pairs.extend(
                    (here / name, dest / (here / name).relative_to(src)) for name in sorted(files)
                )
        else:
            raise SelfExplainedError(
                f"{spec.src} is not in {server_dir}, so its Lua scripts cannot be laid. Nothing "
                f"was changed. Once that folder is back, press {remedy}."
            )
        for source, target in pairs:
            rel = target.relative_to(server_dir).as_posix()
            planned.append(_Planned(source, target, rel))
    return planned


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _renamed_in_place(
    server_dir: Path, record: dict[str, str], planned: Sequence[_Planned]
) -> dict[str, str]:
    """Shipped name -> the record key for the very same file on disk (T563).

    A case-insensitive folder (Windows, macOS) holds `Mentor.lua` and `mentor.lua`
    in one file. When the checkout renames the first to the second, the record
    knows the old spelling and the plan has the new one: without this the new
    name reads as nobody's, and removing the old name as "no longer shipped"
    deletes the file the plan just counted as in place. Asked of the disk (the
    same file under both names), not guessed from the folder's kind, so a
    case-sensitive folder, where they are two files, is left alone. Only names that
    differ by case count: a hard link under another name is the player's own.
    """
    shipped = {item.rel for item in planned}
    found: dict[str, str] = {}
    for rel in sorted(record):
        if rel in shipped:
            continue
        gone = server_dir.joinpath(*PurePosixPath(rel).parts)
        if _through_a_link(server_dir, gone) is not None or gone.is_symlink() or not gone.is_file():
            continue
        for item in planned:
            if item.rel in record or item.rel in found or item.target.is_symlink():
                continue
            if item.rel.casefold() != rel.casefold():
                continue  # another name for the file (a hard link of the player's) is not a rename
            if item.target.is_file() and _same_file(gone, item.target):
                found[item.rel] = rel
                break
    return found


def lay(server_dir: Path, specs: Sequence[LuaScripts], *, quiet: bool = False) -> Iterator[str]:
    """Lay every script the specs name; one line per file that changed or was kept.

    `quiet` leaves out the summary when nothing changed (the update route and a
    Rebuild call this on every press, and "all current" every time is noise).

    **No specs is not always nothing to do** (T563): an entry that dropped its
    `lua_scripts` still has the record of the ones an earlier press laid, and
    those are removed under the stale rule below, and the record with them.

    Raises:
        InstallerError: a source is not there or has a link in it (nothing is written),
            a file could not be written (files written before it stay, and are in the
            record), or the record itself could not be saved after the files were.
    """
    if not specs and not record_path(server_dir).exists():
        return
    remedy = server_build_presses.under_server_build(server_build_presses.REBUILD)
    # The script folder, each entry's own folder in it, and the record: none may be
    # a link (or under one), or the press would write where the link points. Asked
    # before anything is read or written, so a refusal here writes nothing at all.
    for target in (
        record_path(server_dir),
        *(server_dir / spec.dest.rstrip("/") / RECORD_FILE for spec in specs),
    ):
        link = target if target.is_symlink() else _through_a_link(server_dir, target)
        if link is not None:
            raise _link_refusal(server_dir, link, remedy)
    planned = _plan(server_dir, specs, remedy)
    folders = ", ".join(sorted({spec.dest.rstrip("/") for spec in specs}))
    to_delete = f"{folders} and that file" if folders else "that file"
    record, unreadable, had_pending = _read_record(server_dir)
    if not specs and not record and not had_pending:
        return
    if unreadable:
        yield (
            f"{LUA_SCRIPTS_DIR}/{RECORD_FILE}, Yu'lon's list of the scripts it laid, could not "
            f"be read ({unreadable}), so no script counts as Yu'lon's and none is replaced or "
            f"removed. To have them laid fresh, delete {to_delete}, then press "
            f"{remedy}."
        )
    kept_record = dict(record)
    renamed = _renamed_in_place(server_dir, record, planned)
    wrote = current = 0
    finished = False
    pending_written = False
    try:
        for item in planned:
            link = _through_a_link(server_dir, item.target)
            if link is not None:
                raise _link_refusal(server_dir, link, remedy)
            data = item.src.read_bytes()
            new = _sha(data)
            if item.target.is_symlink():
                yield f"{item.rel} is a link Yu'lon did not make, so it was left as it is."
                continue
            try:
                old = _sha(item.target.read_bytes())
            except FileNotFoundError:
                old = None
            owned = record.get(item.rel) or record.get(renamed.get(item.rel, ""))
            if old == new:
                # Claimed only if it was already Yu'lon's: a file that was there
                # first and happens to match stays the player's (Codex review).
                if owned is not None:
                    kept_record[item.rel] = new
                current += 1
                continue
            if old is not None and owned != old:
                yield (
                    f"{item.rel} was changed on this machine, so it was left as it is and the "
                    f"server runs that copy, not the one this server ships. To take the shipped "
                    f"one, delete the file and press {remedy}."
                )
                continue
            item.target.parent.mkdir(parents=True, exist_ok=True)
            pending_written = True
            _write_record(server_dir, kept_record, {item.rel: new})
            _publish(item.target, data)
            kept_record[item.rel] = new
            wrote += 1
            yield f"{'Updated' if old is not None else 'Laid'} {item.rel}."
        shipped = {item.rel for item in planned}
        for rel, digest in sorted(record.items()):
            if rel in shipped:
                continue
            if rel in renamed.values():
                kept_record.pop(rel, None)
                continue
            path = server_dir.joinpath(*PurePosixPath(rel).parts)
            kept_record.pop(rel, None)
            link = _through_a_link(server_dir, path)
            if link is not None:
                yield (
                    f"{rel} is no longer shipped, but {link.relative_to(server_dir).as_posix()} "
                    "is a link, so nothing was removed through it."
                )
                continue
            try:
                same = not path.is_symlink() and _sha(path.read_bytes()) == digest
            except FileNotFoundError:
                continue
            if same:
                path.unlink()
                yield f"Removed {rel}: this server no longer ships it."
            else:
                yield f"{rel} is no longer shipped and was changed on this machine; left as it is."
        finished = True
    except OSError as exc:
        raise SelfExplainedError(
            f"The Lua scripts could not be laid ({exc}). Once the reason is fixed, press "
            f"{remedy}: it lays them again."
        ) from exc
    finally:
        if kept_record != record or pending_written or had_pending:
            try:
                _write_record(server_dir, kept_record)
            except OSError as exc:
                logger.warning(f"could not write {record_path(server_dir)}: {exc}")
                if finished:
                    raise SelfExplainedError(
                        "The Lua scripts were copied, but Yu'lon could not save its list of "
                        f"them, {LUA_SCRIPTS_DIR}/{RECORD_FILE} ({exc}), so it stopped here and "
                        f"started nothing new. Once that is fixed, delete {to_delete}, "
                        f"then press {remedy}."
                    ) from exc
    if specs and (wrote or not quiet):
        yield f"Lua scripts are in place ({wrote} written, {current} already current)."


SqlAsk = Callable[[str, str], str]
"""`(schema, statement) -> the client's stdout`: the install's `sql_query` seam, bound."""

TABLES_QUESTION = "SELECT table_schema, table_name FROM information_schema.tables WHERE "
"""How the one question about which tables exist starts (`read_checks`)."""

_PLAIN_NAME = re.compile(r"^[A-Za-z0-9_]+$")
"""A schema name that may be spliced into the tables question as a quoted literal."""


class ChecksUnreadable(Exception):  # noqa: N818 - a state, worded for the player
    """The database could not be asked, or gave an answer that is not what was asked.

    `str()` is the reason, in the player's words, without "the server was not
    started": the caller says what it did about it.
    """


@dataclass(frozen=True)
class ChecksReading:
    """What the database holds for a set of checks, before anything is worded.

    `missing` are the checked tables `information_schema` does not list (or that
    vanished before their count), sorted, each once; `counts` is every check whose
    table is there, with the rows it counted, in the checks' order; `short` the
    ones of those under their `at_least`.
    """

    missing: tuple[str, ...]
    short: tuple[tuple[SqlCheck, int], ...]
    counts: tuple[tuple[SqlCheck, int], ...]


def _tables_question(wanted: dict[str, set[str]]) -> str:
    groups = []
    for schema, tables in sorted(wanted.items()):
        names = ", ".join(f"'{name}'" for name in sorted(tables))
        groups.append(f"(table_schema = '{schema}' AND table_name IN ({names}))")
    return f"{TABLES_QUESTION}{' OR '.join(groups)};"


def _tables_there(wanted: dict[str, set[str]], ask: SqlAsk) -> set[tuple[str, str]]:
    """`(schema, table)` for each wanted table `information_schema` lists. Raises ChecksUnreadable.

    Under `--batch --skip-column-names` each row is `schema<TAB>table`, and no rows
    is an empty answer: every wanted table is missing then. A line of any other
    shape is an answer to some other question (a warning, an error printed to
    stdout), and nothing is read from it.
    """
    schema = sorted(wanted)[0]
    try:
        answer = ask(schema, _tables_question(wanted))
    except docker.DockerCommandError as exc:
        raise ChecksUnreadable(f"the database did not say which tables it has ({exc})") from exc
    found: set[tuple[str, str]] = set()
    for line in answer.splitlines():
        if not line.strip():
            continue
        where, tab, table = line.strip().partition("\t")
        if not tab or not _PLAIN_NAME.match(where) or not _PLAIN_NAME.match(table):
            raise ChecksUnreadable(
                f"the database answered {line.strip()[:80]!r} when asked which tables it has"
            )
        found.add((where.casefold(), table.casefold()))
    return found


def read_checks(checks: Sequence[SqlCheck], schemas: dict[Db, str], ask: SqlAsk) -> ChecksReading:
    """Which checked tables are there, and what each one that is there counts.

    Two steps (T555 T3, Unbound #71): one `information_schema.tables` question for
    every table the checks name, then one count per check whose table is listed.
    A count refused with ERROR 1146 (the table went between the two questions) is
    a missing table too. Nothing is ever read as 0 that was not answered as 0.

    Raises:
        ChecksUnreadable: a schema name that is not one plain name (never spliced
            into a question), a question the database did not answer, or an answer
            that is not the rows or the count asked for -- an empty count included.
    """
    wanted: dict[str, set[str]] = {}
    for check in checks:
        schema = schemas[check.db]
        if not _PLAIN_NAME.match(schema):
            raise ChecksUnreadable(f"{schema!r} is not one plain schema name")
        wanted.setdefault(schema, set()).add(check.table)
    if not wanted:
        return ChecksReading((), (), ())
    there = _tables_there(wanted, ask)
    missing: set[str] = set()
    counts: list[tuple[SqlCheck, int]] = []
    for check in checks:
        schema = schemas[check.db]
        where = f"{schema}.{check.table}"
        if (schema.casefold(), check.table.casefold()) not in there:
            missing.add(check.table)
            continue
        try:
            answer = ask(schema, check.statement(schema))
        except docker.DockerCommandError as exc:
            if NO_SUCH_TABLE in str(exc):
                missing.add(check.table)
                continue
            raise ChecksUnreadable(f"{where}: {exc}") from exc
        first = answer.strip().splitlines()[0].strip() if answer.strip() else ""
        if not first:
            raise ChecksUnreadable(f"{where}: the database gave no answer when asked its rows")
        try:
            found = int(first)
        except ValueError:
            raise ChecksUnreadable(
                f"{where}: the database answered {first[:80]!r} when asked how many rows it "
                "has, which is not a count"
            ) from None
        counts.append((check, found))
    return ChecksReading(
        missing=tuple(sorted(missing)),
        short=tuple((check, found) for check, found in counts if found < check.at_least),
        counts=tuple(counts),
    )


def check_sql(
    checks: Sequence[SqlCheck], schemas: dict[Db, str], ask: SqlAsk, server_name: str
) -> Iterator[str]:
    """Read the checks (`read_checks`); refuse missing tables, a short count, or no answer.

    Fail closed (Codex, adversarial review): a count that could not be asked, or
    an answer that is not a number, refuses as a short count does. The check is
    there because the world must not start without that data, and "could not
    tell" is not "it is there". The press can be run again once the database
    answers; nothing was started. A table that is not there is named as missing
    (T555 T3), never counted as 0.

    Raises:
        InstallerError: a table is not there, a count fell short, or the database
            could not be read.
    """
    try:
        reading = read_checks(checks, schemas, ask)
    except ChecksUnreadable as exc:
        raise InstallerError(
            f"{server_name}'s database could not be checked for what it needs ({exc}). The "
            "server was not started; press the same button again once the database answers."
        ) from exc
    if reading.missing:
        why = " ".join(
            dict.fromkeys(check.reason for check in checks if check.table in reading.missing)
        )
        raise InstallerError(
            f"{server_name} tables missing: {', '.join(reading.missing)}. {why} The server "
            "was not started."
        )
    for check, found in reading.short:
        schema = schemas[check.db]
        raise InstallerError(
            f"{server_name}'s {check.db} database is missing what it needs: {check.reason} "
            f"({schema}.{check.table} has {found} matching rows, at least {check.at_least} "
            "are needed). The server was not started."
        )
    for check, found in reading.counts:
        yield f"{schemas[check.db]}.{check.table} has what this server needs ({found} rows)."
