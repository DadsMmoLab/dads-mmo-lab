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

The model is the ALE manifests' `deploy` step (`apply.Applier._deploy()`), which
copies a module's `lua_scripts` into the same folder; what is added here is the
record, because these files come back on every rebuild and update rather than
once per module install.

The SQL checks (`AzerothCoreData.sql_checks`) are read-only counts asked after the
import has applied the modules' SQL (`AC_UPDATES_ALLOWED_MODULES=all` with
`./modules` mounted, `base.yml.tmpl`): a count that falls short, or that cannot be
read, refuses the press before the world starts.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from yulon import docker, server_build_presses
from yulon.catalog.catalog import LUA_SCRIPTS_DIR, LuaScripts, SqlCheck
from yulon.catalog.installer import InstallerError
from yulon.log import get_logger
from yulon.manifest import Db

logger = get_logger(__name__)

RECORD_FILE = ".yulon-lua-scripts.json"
"""Which script files Yu'lon wrote, and the SHA-256 of each, in `LUA_SCRIPTS_DIR`."""

SCRIPT_MODE = 0o644
"""Readable by everyone: the world runs as the image's `acore` user, not as the host's."""

NO_SUCH_TABLE = "ERROR 1146"
"""MySQL's "Table ... doesn't exist": the module's SQL never made it, which is a count of 0."""


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
    path = record_path(server_dir)
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning(f"{path} could not be read ({exc}); no script there counts as Yu'lon's")
        return {}
    files = parsed.get("files") if isinstance(parsed, dict) else None
    if not isinstance(files, dict):
        return {}
    return {
        k: v
        for k, v in files.items()
        if isinstance(k, str) and isinstance(v, str) and _inside_the_script_dir(k)
    }


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


def _write_record(server_dir: Path, files: dict[str, str]) -> None:
    path = record_path(server_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"version": 1, "files": dict(sorted(files.items()))}, indent=2) + "\n"
    _publish(path, text.encode("utf-8"))


def _publish(target: Path, data: bytes) -> None:
    """Write `data` at `target` whole: a temp sibling, then a rename over the name."""
    tmp = target.with_name(f".{target.name}.yulon-new")
    try:
        tmp.write_bytes(data)
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


def missing_sources(server_dir: Path, specs: Sequence[LuaScripts]) -> tuple[str, ...]:
    """The `src` of each spec that is not in the server dir as a file or folder."""
    return tuple(
        spec.src
        for spec in specs
        if not (server_dir / spec.src).is_dir() and not (server_dir / spec.src).is_file()
    )


def _plan(server_dir: Path, specs: Sequence[LuaScripts]) -> tuple[list[_Planned], list[str]]:
    """Every file the specs lay, and a line for each link that was not followed."""
    planned: list[_Planned] = []
    skipped: list[str] = []
    for spec in specs:
        src = server_dir / spec.src
        dest = server_dir / spec.dest.rstrip("/")
        if src.is_symlink():
            skipped.append(f"{spec.src} is a link, so no script was laid from it.")
            continue
        if src.is_file():
            pairs = [(src, dest / src.name)]
        elif src.is_dir():
            pairs = []
            for root, dirs, files in os.walk(src, followlinks=False):
                here = Path(root)
                for name in sorted(dirs):
                    if (here / name).is_symlink():
                        skipped.append(
                            f"{(here / name).relative_to(server_dir).as_posix()} is a link, "
                            "so nothing in it was laid."
                        )
                for name in sorted(files):
                    path = here / name
                    if path.is_symlink():
                        skipped.append(
                            f"{path.relative_to(server_dir).as_posix()} is a link, so it was "
                            "not laid."
                        )
                        continue
                    pairs.append((path, dest / path.relative_to(src)))
        else:
            raise InstallerError(
                f"{spec.src} is not in {server_dir}, so its Lua scripts cannot be laid and "
                "the server would start without them. Nothing was changed."
            )
        for source, target in pairs:
            rel = target.relative_to(server_dir).as_posix()
            planned.append(_Planned(source, target, rel))
    return planned, skipped


def lay(server_dir: Path, specs: Sequence[LuaScripts], *, quiet: bool = False) -> Iterator[str]:
    """Lay every script the specs name; one line per file that changed or was kept.

    `quiet` leaves out the summary when nothing changed (the update route and a
    Rebuild call this on every press, and "all current" every time is noise).

    Raises:
        InstallerError: a source is not there, or a file could not be written.
            Files written before it stay, and are in the record.
    """
    if not specs:
        return
    planned, skipped = _plan(server_dir, specs)
    yield from skipped
    record = read_record(server_dir)
    kept_record = dict(record)
    wrote = current = 0
    remedy = server_build_presses.under_server_build(server_build_presses.REBUILD)
    try:
        for item in planned:
            link = _through_a_link(server_dir, item.target)
            if link is not None:
                yield (
                    f"{link.relative_to(server_dir).as_posix()} is a link, so "
                    f"{item.target.name} was not written through it."
                )
                continue
            data = item.src.read_bytes()
            new = _sha(data)
            if item.target.is_symlink():
                yield f"{item.rel} is a link Yu'lon did not make, so it was left as it is."
                continue
            try:
                old = _sha(item.target.read_bytes())
            except FileNotFoundError:
                old = None
            if old == new:
                # Claimed only if it was already Yu'lon's: a file that was there
                # first and happens to match stays the player's (Codex review).
                if item.rel in record:
                    kept_record[item.rel] = new
                current += 1
                continue
            if old is not None and record.get(item.rel) != old:
                yield (
                    f"{item.rel} was changed on this machine, so it was left as it is and the "
                    f"server runs that copy, not the one this server ships. To take the shipped "
                    f"one, delete the file and press {remedy}."
                )
                continue
            item.target.parent.mkdir(parents=True, exist_ok=True)
            _publish(item.target, data)
            kept_record[item.rel] = new
            wrote += 1
            yield f"{'Updated' if old is not None else 'Laid'} {item.rel}."
        shipped = {item.rel for item in planned}
        for rel, digest in sorted(record.items()):
            if rel in shipped:
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
    except OSError as exc:
        raise InstallerError(
            f"The Lua scripts could not be laid ({exc}). The server would start without them, "
            "so it was not started."
        ) from exc
    finally:
        if kept_record != record:
            try:
                _write_record(server_dir, kept_record)
            except OSError as exc:
                logger.warning(f"could not write {record_path(server_dir)}: {exc}")
    if wrote or not quiet:
        yield f"Lua scripts are in place ({wrote} written, {current} already current)."


SqlAsk = Callable[[str, str], str]
"""`(schema, statement) -> the client's stdout`: the install's `sql_query` seam, bound."""


def check_sql(
    checks: Sequence[SqlCheck], schemas: dict[Db, str], ask: SqlAsk, server_name: str
) -> Iterator[str]:
    """Ask every count; refuse the first that falls short or cannot be read.

    Fail closed (Codex, adversarial review): a count that could not be asked, or
    an answer that is not a number, refuses as a short count does. The check is
    there because the world must not start without that data, and "could not
    tell" is not "it is there". The press can be run again once the database
    answers; nothing was started.

    Raises:
        InstallerError: a count fell short, its table is not there, or it could
            not be read.
    """
    for check in checks:
        schema = schemas[check.db]
        try:
            answer = ask(schema, check.statement(schema))
        except docker.DockerCommandError as exc:
            if NO_SUCH_TABLE not in str(exc):
                raise InstallerError(
                    f"{server_name}'s {check.db} database could not be checked for what it "
                    f"needs ({schema}.{check.table}: {exc}). The server was not started; "
                    "press the same button again once the database answers."
                ) from exc
            answer = "0"
        first = answer.strip().splitlines()[0].strip() if answer.strip() else "0"
        try:
            found = int(first)
        except ValueError:
            raise InstallerError(
                f"{server_name}'s {check.db} database answered {first!r} when asked how many "
                f"rows {schema}.{check.table} has, which is not a count. The server was not "
                "started."
            ) from None
        if found < check.at_least:
            raise InstallerError(
                f"{server_name}'s {check.db} database is missing what it needs: {check.reason} "
                f"({schema}.{check.table} has {found} matching rows, at least {check.at_least} "
                "are needed). The server was not started."
            )
        yield f"{schema}.{check.table} has what this server needs ({found} rows)."
