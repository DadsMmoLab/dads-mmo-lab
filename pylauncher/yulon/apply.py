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

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Literal, Protocol

from yulon import platform, runner
from yulon.catalog import composegen
from yulon.git import CloneSpec, Git, GitError, RemoteReader, RunnerGit, same_repo
from yulon.log import get_logger
from yulon.manifest import Db, Deploy, Manifest, ManifestType, Patch, SqlStep, When
from yulon.ownership import Ownership

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

_CLIENT_PROBE_TIMEOUT_SECONDS = 30.0
"""Bounded, because this runs before any SQL and a wedged daemon must not turn
one statement into an indefinite wait — but not tightly. 10s was the first
value and it was too short: `docker exec` against a remote context has to bring
up its transport first, so the probe timed out, fell back to the classic name,
and every statement then failed against a container that does not have it. The
answer is cached, so this is paid once per container per run."""

_CONF_KEY_WRITE_SUFFIXES = (".conf",)


class ApplyError(RuntimeError):
    """A step failed in a way that must stop the run (missing template value, git failure, ...)."""


CLAIM_FILE = ".yulon-clone.json"
"""What this app writes INSIDE a clone it made, so it can recognise it later.

The evidence half of `Ownership`. It is written after a clone succeeds and read
before the next one starts, and it is the only thing that separates "this app's
own `modules/mod-x`" from "a `modules/mod-x` the user put there by hand" — the
convention every AzerothCore user follows, at a path this app picks from a
catalog id it did not ask the user about.

Inside the clone rather than beside it for three reasons: `modules/` is scanned
by AzerothCore's CMake and a sibling of the module directories would be a new
kind of entry there; `git reset --hard` does not remove untracked files, so the
claim survives the very update path it authorises; and `remove()` deleting the
clone deletes the claim with it, with no second place to forget about. It joins
`include.sh` as the second file this engine writes into a clone.
"""

CLAIM_VERSION = 1
"""Bumped only for a change this version could not read. A reader that does not
recognise the version answers `UNKNOWN`, which refuses — never `UNCLAIMED`,
which would let a newer app's clone be treated as a stranger's."""


def read_clone_claim(clone: Path, *, item_id: str) -> Ownership:
    """Did THIS app clone THIS item into THIS folder? The three-answer version.

    Deliberately a copy of `catalog.native.read_claim()`'s SHAPE and none of its
    contents, because the two claims prove different things. There, one record
    covers a whole server install and the clone stages corroborate it against
    `git remote get-url origin`, since the record says nothing about any
    particular sub-checkout. Here the record is per-clone: it is inside the very
    directory in question and names the item whose catalog id chose the path, so
    it is the corroboration rather than something needing it.

    `UNKNOWN` for a file that will not open, will not parse, is not an object,
    carries a version this code does not know, or names another folder or
    another item. All of those are "there is something here and this app cannot
    read it as its own", and the native engine paid for treating that as absent:
    a corrupt state file made it MORE confident than a missing one, and
    `git reset --hard` ran over a user's checkout. Not repeated here.

    The identity is `composegen.install_id()` over the clone's own path — the
    same normalisation (absolute, forward slashes, case-folded on Windows) the
    install engine uses on a server dir — so a COPIED server folder carries
    claims that describe directories somewhere else, and answers `UNKNOWN`
    rather than authorising a reset inside the copy.
    """
    path = clone / CLAIM_FILE
    if not path.is_file():
        return Ownership.UNCLAIMED
    try:
        with path.open(encoding="utf-8") as fh:
            parsed = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.warning(f"{path} could not be read, so this folder is not treated as ours: {exc}")
        return Ownership.UNKNOWN
    if not isinstance(parsed, dict) or parsed.get("version") != CLAIM_VERSION:
        return Ownership.UNKNOWN
    if parsed.get("item_id") != item_id or parsed.get("clone_id") != composegen.install_id(clone):
        return Ownership.UNKNOWN
    return Ownership.OWNED


def write_clone_claim(clone: Path, *, item_id: str, url: str) -> None:
    """Record that this app put `item_id`'s clone here. Raises `OSError` if it cannot.

    `url` is written for a human reading the file; it is never what ownership is
    decided on, because a URL is what everybody with the same catalog entry has.
    """
    payload = {
        "version": CLAIM_VERSION,
        "item_id": item_id,
        "clone_id": composegen.install_id(clone),
        "url": url,
    }
    (clone / CLAIM_FILE).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


_CLIENT_NAMES: dict[str, tuple[str, ...]] = {
    "mysql": ("mysql", "mariadb"),
    "mysqldump": ("mysqldump", "mariadb-dump"),
}
"""What each tool may be called inside the database container, best guess first."""

_client_cache: dict[tuple[str, str], str] = {}


def mysql_client(db_container: str, tool: str = "mysql") -> str:
    """The name `db_container` actually answers to for `tool`.

    **`mariadb:11` ships neither `mysql` nor `mysqldump`.** MariaDB deprecated
    the `mysql*` symlinks and removed them in 11, leaving only `mariadb` and
    `mariadb-dump`. wow-tbc and wow-vanilla run `mariadb:11`, so every statement
    this app sent them died before it reached a database; wow-tortoise pins
    `mariadb:10.6`, which still has the symlinks, which is why it worked and
    hid this (measured on a live TBC server, 2026-08-26).

    Asked of the container rather than derived from the image tag: the tag is
    not visible from here, images get rebuilt, and `command -v` is the same
    question the shell would ask. The answer is cached per container because it
    cannot change without the container being replaced.

    Falls back to the first candidate when the probe cannot run at all, so a
    daemon hiccup produces the same failure it always did rather than a new one.
    """
    key = (db_container, tool)
    cached = _client_cache.get(key)
    if cached is not None:
        return cached
    candidates = _CLIENT_NAMES.get(tool, (tool,))
    resolved = _probe_client(db_container, candidates)
    if resolved is None:
        return candidates[0]
    if resolved != candidates[0]:
        logger.info(f"{db_container} has no `{tool}`; using `{resolved}`")
    _client_cache[key] = resolved
    return resolved


def _probe_client(db_container: str, candidates: tuple[str, ...]) -> str | None:
    """Ask the container which of `candidates` it has, or None if it cannot say.

    Its own function so tests can answer for it without also intercepting the
    statements under test — every caller here runs `docker exec`, and a probe
    sharing that seam would show up in argv assertions that are about SQL.
    """
    program = platform.docker_program()
    if program is None:
        return None
    probe = " || ".join(f"command -v {name}" for name in candidates)
    try:
        proc = subprocess.run(
            [program, "exec", db_container, "sh", "-c", probe],
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            # `os.environ`, not a bare `child_env()`: the calls this probe is
            # resolving for run with the process environment, so DOCKER_HOST and
            # friends must reach the probe too. Without it the probe talks to a
            # different daemon than the statements do, silently answers "no
            # mariadb here", and every statement then names a binary the real
            # container does not have.
            env=runner.child_env(dict(os.environ)),
            creationflags=runner.creationflags(),
            timeout=_CLIENT_PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        # WARNING, not DEBUG: falling back is a guess, and when the guess is
        # wrong every statement afterwards fails with "executable file not
        # found" — a failure that reads like a broken database rather than an
        # unanswered question.
        logger.warning(f"could not ask {db_container} which client it has: {exc}")
        return None
    found = proc.stdout.strip().splitlines()
    if proc.returncode != 0 or not found:
        return None
    return found[0].rsplit("/", 1)[-1]


def mysql_env(root_password: str, wsl_distro: str | None = None) -> dict[str, str]:
    """This process's environment plus `MYSQL_PWD`, so the password never enters argv.

    `docker exec -e MYSQL_PWD` (no `=value`) forwards the variable from OUR
    environment into the container, where the client reads it instead of
    prompting. `-p<password>` would put the secret in a command line every local
    process can read (`ps`, Task Manager, `/proc/<pid>/cmdline`).

    Module-level rather than a `DockerSql` method because `maintenance.py` runs
    `mysqldump`, not `mysql`, and so cannot reuse `DockerSql` itself — but the
    one rule that must never be re-derived is how the password is handed over
    (style-guide §4). `wsl_distro` is part of that rule now: a variable set here
    does NOT reach a process inside a distro unless `WSLENV` names it, so both
    callers get the crossing right by using this rather than by remembering.
    """
    if wsl_distro is not None:
        # Crossing into a distro, the variable does not follow just because it
        # is set here - measured, it arrives EMPTY, and mysql then reports an
        # authentication failure against a perfectly healthy database.
        # `wsl_env()` names it in WSLENV, which is what carries it across.
        return platform.wsl_env({"MYSQL_PWD": root_password})
    env = dict(os.environ)
    env["MYSQL_PWD"] = root_password
    return env


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
    root_password: str = field(repr=False)
    """Kept out of the repr, like `maintenance.DockerMysql.root_password`.

    A frozen dataclass reprs every field by default, and this object is handed
    to worker threads and closed over by the seams the tabs call: a pytest
    assertion diff, a logged object or a traceback frame dump in a UI error
    handler would each print the database password. Its `DockerMysql` sibling
    closed this channel on 2026-08-23 and this one was missed, because the two
    are built side by side at every call site (2026-08-30).
    """
    wsl_distro: str | None = None
    """The WSL2 distro this server's docker lives in, if it is not local."""
    schemas: Mapping[Db, str] = field(default_factory=lambda: DB_NAMES)
    """This server's `manifest db key → schema name` map.

    Defaults to `DB_NAMES` so every AzerothCore caller reads as it did, and is
    overridden from `CatalogEntry.schema_map()` for a game whose schemas are
    named anything else. It is per-instance rather than a module constant
    because one process can hold two installs of different cores at once.
    """

    def run_file(self, db: Db, path: Path) -> None:
        with path.open("rb") as fh:
            proc = self._mysql(db, stdin=fh)
        _check_sql(proc, f"{path.name} → {self._schema(db)}")

    def run_statement(self, db: Db, statement: str) -> None:
        # Over stdin, never `-e <sql>`: argv is world-readable (`ps`, Task
        # Manager, /proc/<pid>/cmdline) and a statement can carry a password.
        proc = self._mysql(db, statement=statement)
        _check_sql(proc, f"inline → {self._schema(db)}")

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
        _check_sql(proc, f"query → {self._schema(db)}")
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
                # Not the default strict decode. `text=True` alone raises
                # UnicodeDecodeError out of here on any byte mysql emits that is
                # not UTF-8 -- a binary column selected as text, or a latin1
                # error message -- and that type is neither `ApplyError` nor the
                # `AccountError` that `accounts.create_account` documents as the
                # only one a caller has to handle. `runner.py` already decodes
                # this way. Found by a live query against a real server
                # (2026-08-23).
                errors="replace",
                check=False,
                env=runner.child_env(self._env()),
                creationflags=runner.creationflags(),
            )
        except OSError as exc:
            # Logged with the real errno first, the way `docker._docker()` does, so a
            # docker.exe blocked by an ACL or by AV leaves evidence instead of being
            # reported to the user as "install Docker Desktop" with nothing in the log
            # to contradict it (review finding, 2026-08-23).
            logger.warning(f"{argv[0]} could not be started: {exc}")
            raise ApplyError(platform.DOCKER_CLI_MISSING_HELP) from exc

    def _env(self) -> dict[str, str]:
        # The distro goes with the password. Without it `mysql_env()` builds an
        # environment with no WSLENV, so `docker exec -e MYSQL_PWD` forwards a
        # variable that arrives EMPTY inside the distro and mysql reports an
        # authentication failure against a healthy database. The unit test for
        # WSLENV called `mysql_env()` directly and so never saw this.
        return mysql_env(self.root_password, self.wsl_distro)

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
        prefix = platform.docker_prefix(self.wsl_distro)
        if prefix is None:
            raise ApplyError(platform.DOCKER_CLI_MISSING_HELP)
        return [
            *prefix,
            "exec",
            "-i",
            "-e",
            "MYSQL_PWD",  # value taken from OUR env by `docker exec`, not written here
            self.db_container,
            mysql_client(self.db_container),
            "-uroot",
            *extra,
            self._schema(db),
        ]

    def _schema(self, db: Db) -> str:
        """The schema name for `db` on THIS server, or a refusal naming it.

        A missing key is a database this core does not have, and there is no
        safe fallback: connecting to the AzerothCore name instead is what
        produced `Unknown database 'acore_auth'` on every CMaNGOS install, and
        connecting to some other schema of this server's would be worse.
        Raised before the argv is built, so nothing runs.
        """
        try:
            return self.schemas[db]
        except KeyError:
            raise ApplyError(
                f"this server has no {db} database; it has " f"{', '.join(sorted(self.schemas))}"
            ) from None


def _check_sql(proc: subprocess.CompletedProcess[str], what: str) -> None:
    """Raise with the reason, wherever the reason happens to be.

    `docker exec` reports its OWN failures on STDOUT, not stderr — a container
    missing the client binary answers

        OCI runtime exec failed: ... exec: "mysql": executable file not found

    on stdout with stderr empty. Reading only stderr turned that into
    `SQL failed (query -> realmd):` with nothing after the colon, which is the
    least useful message this app can produce; it cost an hour of looking in the
    wrong place (2026-08-26). mysql's own errors still arrive on stderr, so
    stderr stays first and stdout is the fallback.
    """
    if proc.returncode == 0:
        return
    reason = proc.stderr.strip() or proc.stdout.strip()
    raise ApplyError(f"SQL failed ({what}): {reason}")


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
        remote_url: Callable[[Path], str | None] | None = None,
    ) -> None:
        self.server_dir = server_dir
        self.git: Git = git if git is not None else RunnerGit()
        self.sql = sql
        self.client_dir = client_dir
        self.dbc = dbc
        # Whose checkout is already at the clone path? Asked through the SAME
        # git the clones go through whenever that git can answer (`RunnerGit`
        # and the containerized one both can), because a machine with no host
        # git must not get a `None` here — `None` is a refusal, and a refusal
        # for the wrong reason is still a wrong answer. A `Git` that only
        # clones falls back to the host CLI, which is what a fake wants.
        self.remote_url: Callable[[Path], str | None] = (
            remote_url
            if remote_url is not None
            else (
                self.git.remote_url
                if isinstance(self.git, RemoteReader)
                else RunnerGit().remote_url
            )
        )

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
            self._require_own_clone(manifest, clone)
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
            try:
                write_clone_claim(clone, item_id=manifest.id, url=manifest.source.url)
            except OSError as exc:
                # Never fatal — the clone is on disk and the rest of the install
                # is what the user asked for — but never silent either: without
                # the claim the NEXT install of this item is refused, and the
                # report is the only place that can say so in advance.
                log.skipped.append(
                    f"{CLAIM_FILE}: could not be written ({exc}), so this app will not "
                    f"recognise {_rel(self.server_dir, clone)} as its own next time"
                )
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
        if clone.exists() and any(p.in_clone and p.when == "configure" for p in manifest.patches):
            # The third writer through `clone_dir()`, and the smallest: an
            # `in_clone` patch edits a file INSIDE the checkout. It cannot
            # delete anything, but it is still this app rewriting a line in a
            # file it did not put there, so it asks the same question. Gated on
            # the manifest actually having such a patch: a refusal about a
            # folder this run would never touch would be a refusal about
            # nothing.
            self._require_own_clone(manifest, clone)
        self._patches(manifest, clone, vals, "configure", log)
        self._sql(manifest, clone, vals, "configure", log)
        self._conf(manifest, clone, vals, log)
        return self._report("configure", manifest, log)

    def remove(self, manifest: Manifest, values: Mapping[str, str] | None = None) -> ApplyReport:
        """Run remove-time patches/SQL, delete deployed files and the clone. DB rows are kept."""
        vals = self._values(manifest, values)
        log = _Log()
        clone = self.clone_dir(manifest)
        if clone.exists():
            # Before the SQL, not next to the `rmtree` below: a refusal must
            # leave the install exactly as it was, and remove-time SQL is not
            # undoable. The same guard as `install()`, because the exposure is
            # the same one and worse — that `rmtree` needs no git seam to
            # destroy a directory whose only crime is matching a catalog id.
            self._require_own_clone(manifest, clone)
        self._patches(manifest, clone, vals, "remove", log)
        self._sql(manifest, clone, vals, "remove", log)
        for step in manifest.deploy:
            self._undeploy(step, clone, log)
        if clone.exists():
            shutil.rmtree(clone)
            log.done.append(f"rm -r {_rel(self.server_dir, clone)}")
        return self._report("remove", manifest, log)

    # -- the guard ---------------------------------------------------------

    def _require_own_clone(self, manifest: Manifest, clone: Path) -> None:
        """Refuse `modules/<id>` unless this app can show the folder is its own.

        Both destructive paths go through here, and neither had anything before
        (three reviewers, 2026-08-31). `install()` handed the path straight to
        the clone seam, which `shutil.rmtree`s a destination that is not a git
        checkout and runs `git fetch` + `git reset --hard FETCH_HEAD` on one
        that is, without ever comparing its origin; `remove()` deleted it
        outright. The path is not obscure: it is `modules/<id>` under the
        server dir, which is exactly where an AzerothCore user installs a
        module by hand, and the id comes from a catalog this app chose. The
        shipped Modules tab binds "Install selected" to `install()` directly,
        so one press over a hand-made `modules/mod-x` was enough.

        The order is the order of the evidence, cheapest and most certain
        first, and nothing is written before all of it is in:

        0. Nothing at the path: the ordinary first install, and the only case
           with nothing to lose. Allowed without asking git anything.
        1. Not a directory — a FILE at the clone path is somebody's,
           and neither `rmtree` nor a clone may decide what it was.
        2. A directory with no `.git`: hand-installed content, or a tarball
           unpacked there. Refused if it holds anything, allowed if it is an
           empty folder somebody made — there is nothing there to lose.
        3. A checkout this app's own claim vouches for: allowed, and this is
           the ONLY way through. `read_clone_claim()` says why the claim is
           enough on its own here, where `catalog.native` needs its record
           corroborated by `origin`.
        4. Everything else is a checkout this app did not make. `origin` is
           asked only to say WHICH refusal — a different repository, this
           repository, or a git that would not answer — because every branch
           of it refuses. That is the divergence from
           `native.refuse_unowned_checkout()`, which can be reached with the
           question already answered by its caller.

        `UNKNOWN` is refused with its own sentence, per `Ownership`: a damaged
        claim means this app knows LESS than an absent one, so it must not act
        more freely.
        """
        if not clone.exists():
            return
        if not clone.is_dir():
            raise ApplyError(
                f"{_rel(self.server_dir, clone)} is a file, not this app's clone of "
                f"{manifest.id}. Nothing was changed. Move it aside and try again."
            )
        url = manifest.source.url if manifest.source is not None else ""
        if not (clone / ".git").is_dir():
            leftovers = sorted(item.name for item in clone.iterdir())
            if leftovers:
                raise ApplyError(
                    f"{_rel(self.server_dir, clone)} already has files in it and was not put "
                    f"there by this app ({', '.join(leftovers[:5])}). Nothing was changed. "
                    f"Move that folder aside — or delete it yourself if you no longer want it — "
                    f"and install {manifest.id} again into an empty one."
                )
            return
        owned = read_clone_claim(clone, item_id=manifest.id)
        if owned is Ownership.OWNED:
            return
        if owned is Ownership.UNKNOWN:
            raise ApplyError(
                f"{_rel(self.server_dir, clone)} holds a {CLAIM_FILE} this app cannot read as "
                f"its own, so it cannot tell whether that checkout is its own {manifest.id} or "
                f"your work. Nothing was changed. Delete that file if it is left over from an "
                f"install that was interrupted, or move the folder aside and install again."
            )
        remote = self.remote_url(clone)
        if remote is None:
            raise ApplyError(
                f"{_rel(self.server_dir, clone)} is a git checkout, but git would not say what "
                f"it is a checkout of, so nothing was changed. Move that folder aside and "
                f"install {manifest.id} again."
            )
        if url and not same_repo(remote, url):
            raise ApplyError(
                f"{_rel(self.server_dir, clone)} is a checkout of {remote}, not of {url}. "
                f"Nothing was changed. Move that folder aside and install {manifest.id} again."
            )
        raise ApplyError(
            f"{_rel(self.server_dir, clone)} is already a git checkout and there is no record "
            f"here of one this app made. Continuing would run `git fetch` and `git reset "
            f"--hard` over it, which throws away anything you have changed there, so nothing "
            f"was touched. Move that folder aside — a module clone holds nothing but the "
            f"module, so re-cloning it costs only the download — and install {manifest.id} "
            f"again."
        )

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
