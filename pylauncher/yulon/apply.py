"""The declarative apply engine: turn a `Manifest` into install/configure/remove steps.

This is the one place the manifest primitives (`source`, `deploy`, `patches`,
`sql`, `conf`, `client`, `server_dbc`) acquire behavior (roadmap 2.3). It is
game-agnostic and shared by every `controller_<acronym>/modules.py`
(style-guide §4); all per-item knowledge comes from the manifest, never from
a conditional here. Everything that reaches outside the process goes through
a small seam (`Git`, `SqlRunner`, `DbcCopier`) so the engine is unit-testable
without git, Docker or a network, and so the real implementations live next
to the other subprocess code.

Nothing is ever skipped silently: every step a run could not perform (no SQL
runner, no client dir, no DBC copier) is named in `ApplyReport.skipped`, and
`rebuild_required` says whether the worldserver must be rebuilt before the
change is live. The engine does not restart, rebuild, or touch Docker itself —
that is the controller's call (call down / signal up, §5).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Literal, Protocol

from yulon import platform
from yulon.git import CloneSpec, Git, GitError, RunnerGit
from yulon.log import get_logger
from yulon.manifest import Db, Deploy, Manifest, ManifestType, Patch, SqlStep, When

logger = get_logger(__name__)

# Where each family's clone lives under the server dir (mirrors wow-manage.sh).
CLONE_DIRS: dict[ManifestType, str] = {
    "module": "modules",
    "ale": "ale_scripts",
    "keg": "ale_scripts",
    "mod": "sql_scripts/clones",
}

# Manifest `db` → MySQL schema name (AzerothCore defaults; acore_ale is Paragon's).
DB_NAMES: dict[Db, str] = {
    "auth": "acore_auth",
    "characters": "acore_characters",
    "world": "acore_world",
    "playerbots": "acore_playerbots",
    "ale": "acore_ale",
}

_CONF_KEY_WRITE_SUFFIXES = (".conf",)


class ApplyError(RuntimeError):
    """A step failed in a way that must stop the run (missing template value, git failure, ...)."""


# ------------------------------------------------------------------- seams


class SqlRunner(Protocol):
    """Run SQL against one of the server's databases."""

    def run_file(self, db: Db, path: Path) -> None: ...

    def run_statement(self, db: Db, statement: str) -> None: ...


class DbcCopier(Protocol):
    """Copy DBC files from a host directory into the server's `data/dbc/` volume."""

    def copy_dbc_dir(self, src: Path) -> None: ...


@dataclass(frozen=True)
class DockerSql:
    """`SqlRunner` over `docker exec <db_container> mysql`, like wow-manage.sh does."""

    db_container: str
    root_password: str

    def run_file(self, db: Db, path: Path) -> None:
        with path.open("rb") as fh:
            proc = self._mysql(db, stdin=fh)
        _check_sql(proc, f"{path.name} → {DB_NAMES[db]}")

    def run_statement(self, db: Db, statement: str) -> None:
        # Over stdin, never `-e <sql>`: argv is world-readable (`ps`, Task
        # Manager, /proc/<pid>/cmdline) and a statement can carry a password.
        proc = self._mysql(db, statement=statement)
        _check_sql(proc, f"inline → {DB_NAMES[db]}")

    def query(self, db: Db, statement: str) -> str:
        """Run one SELECT and return its rows, tab-separated, one per line.

        `run_statement()` discards stdout, which is right for the applier — it
        only ever asserts a step succeeded. Account creation genuinely has to
        read (does this username exist, and what id did it get), so this is the
        read half of the same seam rather than a second one beside it.

        `--skip-column-names` because every caller wants values, not a header,
        and `--batch` so the separator is a tab whether or not the client
        decided it was talking to a terminal.

        The exit code is checked for the same reason `run_statement()` checks
        it, and one more: a reader cannot tell "no rows" from "the query never
        ran". `accounts._account_id()` reads no rows as "this username is free"
        and inserts, so a `query()` that returned "" on failure would turn an
        unreachable database into a green light to write.

        Raises:
            ApplyError: no docker CLI (from `_mysql()`), or `mysql` exited
                non-zero.
        """
        proc = self._mysql(db, statement=statement, extra=("--batch", "--skip-column-names"))
        _check_sql(proc, f"query → {DB_NAMES[db]}")
        return proc.stdout

    def _mysql(
        self,
        db: Db,
        *,
        stdin: IO[bytes] | None = None,
        statement: str | None = None,
        extra: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        """Run one `docker exec ... mysql`, with the missing-CLI guards in one place.

        Exactly one of `stdin` (a file to pipe in) and `statement` (a string to
        pipe in) is given; both arrive as the child's stdin, and neither is ever
        put in argv — see `run_statement()`.

        `subprocess.run` is called here rather than `yulon.runner`, which is a
        style-guide §3 deviation the file already carried: `run_file()` needs
        `stdin=<open file>` and `runner.run()` has no way to express it. Left as
        it was found, and not widened — the point of this method is that the two
        callers stop repeating the call, not that a third gets added.

        Raises:
            ApplyError: There is no docker CLI to run. Both roads to that, since
                the resolution cache remembers a hit: never resolved at all
                (`_argv()`), and resolved earlier to a `docker.exe` that has
                since been uninstalled or moved by a Docker Desktop update
                (the `OSError` here). The second used to surface as a bare
                `[WinError 2]` (review, 2026-08-23).
        """
        argv = self._argv(db, extra=extra)
        try:
            return subprocess.run(
                argv,
                stdin=stdin,
                input=statement,
                capture_output=True,
                text=True,
                check=False,
                env=self._env(),
            )
        except OSError as exc:
            # Logged with the real errno first, the way `docker._docker()` does, so a
            # docker.exe blocked by an ACL or by AV leaves evidence instead of being
            # reported to the user as "install Docker Desktop" with nothing in the log
            # to contradict it (review finding, 2026-08-23).
            logger.warning(f"{argv[0]} could not be started: {exc}")
            raise ApplyError(platform.DOCKER_CLI_MISSING_HELP) from exc

    def _env(self) -> dict[str, str]:
        """Our environment plus `MYSQL_PWD`, so the password never enters argv.

        `docker exec` passes `-e MYSQL_PWD` through to the client inside the
        container; `mysql` reads it instead of prompting. `-p<password>` would
        put the secret in a command line every local process can read.
        """
        env = dict(os.environ)
        env["MYSQL_PWD"] = self.root_password
        return env

    def _argv(self, db: Db, *, extra: tuple[str, ...] = ()) -> list[str]:
        """`docker exec ... mysql <db>`, with the CLI name this host can start.

        `extra` carries client flags that only one caller wants (`query()`'s
        output formatting). It defaults to empty so the write path's argv is
        byte-identical to what it was before the read path existed.

        Raises:
            ApplyError: no docker CLI here. A manifest apply runs straight
                after an install, on the same process that may still be blind
                to the PATH Docker Desktop's installer wrote — see
                `platform.docker_program()`.
        """
        program = platform.docker_program()
        if program is None:
            raise ApplyError(platform.DOCKER_CLI_MISSING_HELP)
        return [
            program,
            "exec",
            "-i",
            "-e",
            "MYSQL_PWD",  # value taken from OUR env by `docker exec`, not written here
            self.db_container,
            "mysql",
            "-uroot",
            *extra,
            DB_NAMES[db],
        ]


def _check_sql(proc: subprocess.CompletedProcess[str], what: str) -> None:
    if proc.returncode != 0:
        raise ApplyError(f"SQL failed ({what}): {proc.stderr.strip()}")


# ------------------------------------------------------------------ report


@dataclass(frozen=True)
class ApplyReport:
    """What one install/configure/remove run did, did not do, and still needs."""

    action: When
    item_id: str
    done: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    rebuild_required: bool = False
    restart_recommended: bool = False


@dataclass
class _Log:
    done: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


# ------------------------------------------------------------------- engine


class Applier:
    """Apply manifests to one server install rooted at `server_dir`.

    Optional seams default to "absent": without a `SqlRunner` every direct SQL
    step is reported as skipped (never run half a migration), without a
    `client_dir` client files are skipped, without a `DbcCopier` DBCs are.
    """

    def __init__(
        self,
        server_dir: Path,
        *,
        git: Git | None = None,
        sql: SqlRunner | None = None,
        client_dir: Path | None = None,
        dbc: DbcCopier | None = None,
    ) -> None:
        self.server_dir = server_dir
        self.git: Git = git if git is not None else RunnerGit()
        self.sql = sql
        self.client_dir = client_dir
        self.dbc = dbc

    # -- public ------------------------------------------------------------

    def clone_dir(self, manifest: Manifest) -> Path:
        """Where this item's clone lives (`modules/<id>`, `ale_scripts/<id>`, ...)."""
        return self.server_dir / CLONE_DIRS[manifest.type] / manifest.id

    def install(self, manifest: Manifest, values: Mapping[str, str] | None = None) -> ApplyReport:
        """Clone, deploy, patch, run install-time SQL, activate conf, copy client/DBC files."""
        vals = self._values(manifest, values)
        log = _Log()
        clone = self.clone_dir(manifest)
        if manifest.source is not None:
            try:
                self.git.clone(
                    CloneSpec(
                        url=manifest.source.url,
                        dest=clone,
                        branch=manifest.source.branch,
                        sparse_path=manifest.source.sparse_path,
                    )
                )
            except GitError as exc:  # one failure vocabulary for the whole applier
                raise ApplyError(str(exc)) from exc
            log.done.append(f"clone {manifest.source.url} → {_rel(self.server_dir, clone)}")
            if manifest.type == "module":
                # CMake's CollectSourceFiles() silently skips a module without include.sh.
                include = clone / "include.sh"
                if not include.exists():
                    include.touch()
                    log.done.append("touch include.sh")
        self._deploy(manifest, clone, log)
        self._patches(manifest, clone, vals, "install", log)
        self._sql(manifest, clone, vals, "install", log)
        self._conf(manifest, clone, vals, log)
        self._client(manifest, clone, log)
        self._dbc(manifest, clone, log)
        return self._report("install", manifest, log)

    def configure(self, manifest: Manifest, values: Mapping[str, str] | None = None) -> ApplyReport:
        """Re-apply the value-bearing steps: configure-time patches/SQL and conf keys."""
        vals = self._values(manifest, values)
        log = _Log()
        clone = self.clone_dir(manifest)
        self._patches(manifest, clone, vals, "configure", log)
        self._sql(manifest, clone, vals, "configure", log)
        self._conf(manifest, clone, vals, log)
        return self._report("configure", manifest, log)

    def remove(self, manifest: Manifest, values: Mapping[str, str] | None = None) -> ApplyReport:
        """Run remove-time patches/SQL, delete deployed files and the clone. DB rows are kept."""
        vals = self._values(manifest, values)
        log = _Log()
        clone = self.clone_dir(manifest)
        self._patches(manifest, clone, vals, "remove", log)
        self._sql(manifest, clone, vals, "remove", log)
        for step in manifest.deploy:
            self._undeploy(step, clone, log)
        if clone.exists():
            shutil.rmtree(clone)
            log.done.append(f"rm -r {_rel(self.server_dir, clone)}")
        return self._report("remove", manifest, log)

    # -- steps -------------------------------------------------------------

    def _deploy(self, manifest: Manifest, clone: Path, log: _Log) -> None:
        for step in manifest.deploy:
            src = clone / step.src
            target = self._deploy_target(step.src, step.dest)
            if src.is_dir():
                shutil.copytree(src, target, dirs_exist_ok=True)
            elif src.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
            else:
                raise ApplyError(f"deploy source missing in clone: {src}")
            log.done.append(f"deploy {step.src} → {_rel(self.server_dir, target)}")
            for old, new in step.rename:
                (target / old).replace(target / new)
                log.done.append(f"rename {old} → {new}")

    def _undeploy(self, step: Deploy, clone: Path, log: _Log) -> None:
        """Delete exactly what `_deploy()` put under `dest` — never the dest dir itself.

        A directory source may land in a SHARED dir (battlepass: `lua_scripts/` →
        `.../lua_scripts/`, next to every other ALE script), so the deployed set is
        re-derived from the clone's `src` listing plus `rename`. Without the clone
        that set is unknowable for a directory: the files stay and the step is
        reported skipped — never guessed at (review finding, 2026-08-21).
        """
        target = self._deploy_target(step.src, step.dest)
        src = clone / step.src
        if src.is_dir():
            deployed = {entry.name for entry in src.iterdir()}
            for old, new in step.rename:
                old_parts, new_parts = Path(old).parts, Path(new).parts
                if len(old_parts) == 1 and old_parts[0] in deployed:
                    deployed.discard(old_parts[0])
                    deployed.add(new_parts[0])
            for name in sorted(deployed):
                self._rm(target / name, log)
            return
        if target.is_file():
            self._rm(target, log)
        elif target.is_dir():
            log.skipped.append(
                f"{step.src}: clone missing, so the files deployed into "
                f"{_rel(self.server_dir, target)} are unknown and were left in place"
            )

    def _rm(self, path: Path, log: _Log) -> None:
        if path.is_dir():
            shutil.rmtree(path)
            log.done.append(f"rm -r {_rel(self.server_dir, path)}")
        elif path.is_file() or path.is_symlink():
            path.unlink()
            log.done.append(f"rm {_rel(self.server_dir, path)}")

    def _deploy_target(self, src: str, dest: str) -> Path:
        target = self.server_dir / dest
        # "file.lua" → "dir/" means dir/file.lua; "dir/" → "dir2/" means dir2 itself.
        if dest.endswith("/") and not src.endswith("/"):
            return target / Path(src).name
        return target

    def _patches(
        self, manifest: Manifest, clone: Path, vals: Mapping[str, str], when: When, log: _Log
    ) -> None:
        for patch in manifest.patches:
            if patch.when != when:
                continue
            base = clone if patch.in_clone else self.server_dir
            files = sorted(base.glob(patch.file)) if _is_glob(patch.file) else [base / patch.file]
            replacement = _render(patch.replace, vals, f"patch {patch.file}")
            for path in files:
                if not path.is_file():
                    raise ApplyError(f"patch target missing: {path}")
                changed = _apply_patch(path, patch, replacement)
                log.done.append(
                    f"patch {_rel(base, path)} ({'changed' if changed else 'already applied'})"
                )

    def _sql(
        self, manifest: Manifest, clone: Path, vals: Mapping[str, str], when: When, log: _Log
    ) -> None:
        for step in manifest.sql:
            if step.when != when:
                continue
            if step.applied_by == "db-import":
                log.done.append(f"sql {step.path} → {step.db}: left to ac-db-import on next start")
                continue
            if self.sql is None:
                log.skipped.append(f"sql → {step.db}: no SQL runner configured")
                continue
            self._run_sql(step, clone, vals, log)

    def _run_sql(self, step: SqlStep, clone: Path, vals: Mapping[str, str], log: _Log) -> None:
        assert self.sql is not None
        if step.statement is not None:
            self.sql.run_statement(step.db, _render(step.statement, vals, "sql statement"))
            log.done.append(f"sql inline → {step.db}")
            return
        assert step.path is not None
        pattern = _render(step.path, vals, "sql path")
        files = sorted(clone.glob(pattern)) if _is_glob(pattern) else [clone / pattern]
        for path in files:
            if not path.is_file():
                raise ApplyError(f"sql file missing in clone: {path}")
            self.sql.run_file(step.db, path)
            log.done.append(f"sql {_rel(clone, path)} → {step.db}")

    def _conf(self, manifest: Manifest, clone: Path, vals: Mapping[str, str], log: _Log) -> None:
        for conf in manifest.conf:
            if _is_glob(conf.file) or not conf.file.endswith(_CONF_KEY_WRITE_SUFFIXES):
                continue  # Lua/DB-table "conf" is patched or prompted, not key-written
            target = self.server_dir / conf.file
            if conf.template is not None and not target.exists():
                template = clone / conf.template
                if not template.is_file():
                    log.skipped.append(f"conf {conf.file}: template {conf.template} not in clone")
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(template, target)
                log.done.append(f"activate {conf.file} from {conf.template}")
            writes = [(k.key, k.default) for k in conf.keys if k.default is not None]
            if not writes:
                continue
            if not target.is_file():
                log.skipped.append(f"conf {conf.file}: file missing, keys not written")
                continue
            for key, default in writes:
                _set_conf_key(target, key, _render(default, vals, f"conf {key}"))
            log.done.append(f"set {len(writes)} key(s) in {conf.file}")

    def _client(self, manifest: Manifest, clone: Path, log: _Log) -> None:
        for step in manifest.client:
            if self.client_dir is None:
                log.skipped.append(f"client {step.src}: no client dir configured")
                continue
            src = clone / step.src
            if step.dest == "addons":
                target = self.client_dir / "Interface" / "AddOns" / (step.name or src.name)
            elif step.dest == "interface":
                target = self.client_dir / "Interface"
            else:
                target = self.client_dir / "Data"
            if src.is_dir():
                shutil.copytree(src, target, dirs_exist_ok=True)
            elif src.is_file():
                target.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target / src.name)
            else:
                raise ApplyError(f"client source missing in clone: {src}")
            log.done.append(f"client {step.src} → {step.dest}")

    def _dbc(self, manifest: Manifest, clone: Path, log: _Log) -> None:
        for step in manifest.server_dbc:
            if self.dbc is None:
                log.skipped.append(f"server_dbc {step.src}: no DBC copier configured")
                continue
            self.dbc.copy_dbc_dir(clone / step.src)
            log.done.append(f"server_dbc {step.src} → data/dbc/")

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _values(manifest: Manifest, values: Mapping[str, str] | None) -> dict[str, str]:
        merged = {p.key: p.default for p in manifest.prompts if p.default is not None}
        merged.update(values or {})
        return merged

    def _report(self, action: When, manifest: Manifest, log: _Log) -> ApplyReport:
        report = ApplyReport(
            action=action,
            item_id=manifest.id,
            done=tuple(log.done),
            skipped=tuple(log.skipped),
            rebuild_required=manifest.build.rebuild and action != "configure",
            restart_recommended=bool(
                manifest.npcs
                or any(s.applied_by == "direct" for s in manifest.sql)
                or manifest.server_dbc
            ),
        )
        logger.info(
            f"{action} {manifest.id}: {len(report.done)} step(s), "
            f"{len(report.skipped)} skipped, rebuild={report.rebuild_required}"
        )
        return report


# --------------------------------------------------------------- functions


def _render(template: str, values: Mapping[str, str], what: str) -> str:
    """`{key}` substitution; a missing key is an `ApplyError`, never silent garbage."""
    try:
        return template.format_map(dict(values))
    except KeyError as exc:
        raise ApplyError(f"{what}: no value for {{{exc.args[0]}}}") from exc
    except (IndexError, ValueError) as exc:
        raise ApplyError(f"{what}: bad template {template!r}: {exc}") from exc


def _is_glob(path: str) -> bool:
    return any(ch in path for ch in "*?[")


def _apply_patch(path: Path, patch: Patch, replacement: str) -> bool:
    text = path.read_text(encoding="utf-8")
    if patch.regex:
        new = re.sub(patch.find, replacement, text, flags=re.MULTILINE)
    else:
        new = text.replace(patch.find, replacement)
    if new == text:
        return False
    path.write_text(new, encoding="utf-8", newline="\n")
    return True


_KeyMode = Literal["replace", "append"]


def _set_conf_key(path: Path, key: str, value: str) -> _KeyMode:
    """Set `key = value` in a worldserver-style conf: replace the line, or append it."""
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*=.*$", re.MULTILINE)
    new, count = pattern.subn(f"{key} = {value}", text, count=1)
    if count:
        path.write_text(new, encoding="utf-8", newline="\n")
        return "replace"
    sep = "" if text.endswith("\n") or not text else "\n"
    path.write_text(f"{text}{sep}{key} = {value}\n", encoding="utf-8", newline="\n")
    return "append"


def _rel(base: Path, path: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)
