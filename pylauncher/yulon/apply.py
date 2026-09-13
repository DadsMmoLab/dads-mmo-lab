"""The declarative apply engine: turn a `Manifest` into install/configure/remove steps.

This is the one place the manifest primitives (`source`, `deploy`, `patches`,
`sql`, `conf`, `client`, `server_dbc`) acquire behavior (roadmap 2.3). It is
game-agnostic and shared by every `controller_<acronym>/modules.py`
(style-guide §4); all per-item knowledge comes from the manifest, never from
a conditional here. Everything that reaches outside the process goes through
a small seam (`Git`, `SqlRunner`, `DbcCopier`) so the engine is unit-testable
without git, Docker or a network, and so the real implementations live next
to the other subprocess code.

Nothing is ever skipped silently, and nothing is ever CLAIMED silently either:
every step a run could not perform (no SQL runner, no client dir, no DBC copier)
is named in `ApplyReport.skipped`; SQL left to AzerothCore's own importer is
named in `ApplyReport.pending_sql` rather than in `done`, with the glob resolved
so the file count is one this run took (`PendingSql` records what that cost);
and `rebuild_required` says whether the worldserver must be rebuilt before the
change is live. The engine does not restart, rebuild, or touch Docker itself —
that is the controller's call (call down / signal up, §5).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from string import Formatter
from typing import IO, Literal, Protocol

from yulon import docker, platform, runner
from yulon.catalog import composegen
from yulon.dbreads import SqlReader
from yulon.git import (
    BehindReader,
    CloneSpec,
    Git,
    GitError,
    HistoryReader,
    RemoteReader,
    RunnerGit,
    TreeReader,
    same_repo,
)
from yulon.log import get_logger
from yulon.manifest import Db, Deploy, Manifest, ManifestType, Patch, Prompt, SqlStep, When
from yulon.ownership import Ownership

logger = get_logger(__name__)

# Where each family's clone lives under the server dir (mirrors wow-manage.sh).
CLONE_DIRS: dict[ManifestType, str] = {
    "module": "modules",
    "ale": "ale_scripts",
    "keg": "ale_scripts",
    "mod": "sql_scripts/clones",
}


def installed_clones(server_dir: Path) -> dict[str, frozenset[str]]:
    """What is on disk per family, read from each family's own clone directory (T41).

    `CLONE_DIRS` is the whole point: a module lands in `modules/`, an ale and a
    keg in `ale_scripts/`, a mod in `sql_scripts/clones/`. Marking every family
    against `modules/` said an ale was installed because a module of the same
    id was, and never marked a real ale at all.

    `ale` and `keg` share one folder, so they share its answer. Nothing here can
    tell which of the two a directory belongs to without opening it, and this
    function is the cheap read that runs on every reload — the row it might
    over-mark is one whose manifest the user can still read.
    """
    return {
        str(kind): docker.clone_names(server_dir / folder) for kind, folder in CLONE_DIRS.items()
    }


# Manifest `db` → MySQL schema name (AzerothCore defaults; acore_ale is Paragon's).
DB_NAMES: dict[Db, str] = {
    "auth": "acore_auth",
    "characters": "acore_characters",
    "world": "acore_world",
    "playerbots": "acore_playerbots",
    "ale": "acore_ale",
}

WORLD_HELD_DBS: frozenset[Db] = frozenset({"characters", "world", "playerbots"})
"""The databases a running worldserver holds in memory and writes back over.

The set `_refuse_direct_sql_into_a_running_world()` refuses into, enumerated
once so the guard and any reader of it cannot disagree. It is the union of what
the pages name, and no more: owner answer 7 says `characters` and `world`
(`phase8-parity-decisions.md:44`), `checklist.md:2501` says *the character or
world database*, and `phase8-designs/c-operators-risk.md:90` adds `playerbots`.

`auth` is deliberately outside it. Its writes are account rows, which the
worldserver does not cache and write back, and every page that names this rule
names it as a database the guard does not cover.

**`ale` is outside it because no page names it, not because a page allows it.**
It is the ALE Lua engine's own schema, which lives inside the worldserver
process, so the reason the other three are here plausibly applies to it too;
one shipped step targets it (`manifests/wow-wotlk/ale/paragon.json`,
`sql/0[2-9]_*.sql`, install-time). Owner answer 7, checklist 8.7a and the two
design pages are all silent on it, so it is left running rather than quietly
decided for here — an owner question, recorded in `pyplan/write-ledger.md`.
"""

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
    if not (clone / CLAIM_FILE).is_file():
        return Ownership.UNCLAIMED
    parsed = _parse_clone_claim(clone)
    if parsed is None:
        return Ownership.UNKNOWN
    if parsed.get("item_id") != item_id or parsed.get("clone_id") != composegen.install_id(clone):
        return Ownership.UNKNOWN
    return Ownership.OWNED


def _parse_clone_claim(clone: Path) -> dict[str, object] | None:
    """The claim file's object, or `None` if there is nothing usable at that name.

    Split out of `read_clone_claim()` because two questions are asked of the same
    file and only one of them is ownership. The other is
    `claim_written_by_this_app()`'s: not "is this folder mine?" but "is this a
    record THIS app wrote, that has stopped matching the folder it is in?"

    The absent/unreadable distinction stays with the CALLER, which is where it
    has to be: `read_clone_claim()` answers `UNCLAIMED` for a missing file and
    `UNKNOWN` for an unreadable one, and collapsing those two is the native
    engine's 2026-08-31 bug (`native.read_claim()`).
    """
    path = clone / CLAIM_FILE
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8") as fh:
            parsed = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.warning(f"{path} could not be read, so this folder is not treated as ours: {exc}")
        return None
    if not isinstance(parsed, dict) or parsed.get("version") != CLAIM_VERSION:
        return None
    return parsed


def claim_written_by_this_app(clone: Path, *, item_id: str) -> bool:
    """A claim that parses, is this version's, names THIS item — and another folder.

    Strictly weaker than `Ownership.OWNED`: everything matches except the one
    field that says WHERE the clone was when it was written. Only `remove()`
    acts on it, and `Applier._require_own_clone()` argues why.

    `clone_id` must be a string that simply differs. A record with the key
    missing or of the wrong type is malformed, not relocated, and stays
    `UNKNOWN` — the point of this predicate is that it recognises this app's own
    handwriting, not that it is lenient.
    """
    parsed = _parse_clone_claim(clone)
    if parsed is None:
        return False
    return (
        parsed.get("item_id") == item_id
        and isinstance(parsed.get("clone_id"), str)
        and parsed.get("clone_id") != composegen.install_id(clone)
    )


def server_dir_claim(server_dir: Path) -> Ownership:
    """Did THIS app create THIS server directory? The install engine's own record.

    Evidence from OUTSIDE any module clone, which is what makes it worth asking
    at all: `.yulon-install.json` sits at the server dir, is written only by
    `catalog.native`'s staged installer, and records the `install_id()` of the
    directory it was written in. A user hand-installing a module into their own
    AzerothCore tree cannot produce one, and a COPIED server folder carries one
    that names the original's path — so this answers `UNKNOWN` there for the same
    reason `read_clone_claim()` does.

    The `install_id` comparison is done here rather than left to
    `native.read_claim()`, which only says whether the file parsed:
    `StagedInstaller.claimed_this_folder()` checks the identity against the state
    the guard already validated, and this caller has no such state to check
    against — only the folder.

    `valid=()` because the stage names are the install engine's business and the
    only field read here is the identity; an unknown stage name being dropped
    from `completed` cannot change that answer.

    `catalog.native` is imported INSIDE this function, and it is the only thing
    in this module that wants it. Naming it at module scope would make the
    game-agnostic apply engine — imported by `networking`, `accounts`,
    `maintenance`, `repair` and the UI — drag the whole native install engine
    (docker, the staged installer, threads and queues) in behind it, for one
    JSON file at the server dir. It is not a cycle today; it is a dependency
    nobody asking `Applier` to run some SQL should have to load. `Applier`
    takes this as a seam, so the production default is the only caller.
    """
    from yulon.catalog import native

    claim = native.read_claim(server_dir, valid=())
    state = claim.state
    if state is None:
        return claim.ownership
    if state.install_id != composegen.install_id(server_dir):
        return Ownership.UNKNOWN
    return Ownership.OWNED


_SERVER_DIR_CLAIM: Callable[[Path], Ownership] = server_dir_claim
"""`server_dir_claim()` under a name that `Applier.__init__`'s parameter cannot shadow.

The seam is called `server_dir_claim` because that is the question it answers,
and inside that signature the name is the parameter. This is how the default
still reaches the function."""


def write_clone_claim(clone: Path, *, item_id: str, url: str) -> None:
    """Record that this app put `item_id`'s clone here. Raises `OSError` if it cannot.

    `url` is written for a human reading the file; it is never what ownership is
    decided on, because a URL is what everybody with the same catalog entry has.

    **Written somewhere else in the same directory and then renamed over the
    real name, never straight into it.** A plain write opens `CLAIM_FILE` for
    truncation and then fills it, so a full disk, a killed process or a lost
    power cable at the wrong instant leaves a HALF file at that name — and a
    half file is the worst of the three possible states. It does not parse; a
    claim that does not parse reads `UNKNOWN`; `UNKNOWN` refuses every caller;
    and `remove()`'s relocation licence needs a claim that PARSES, so it refuses
    too. The user is then left with a module this app installed, that this app
    will not uninstall, and no route back that does not involve finding and
    deleting a dotfile by hand. That is a permanent lockout caused entirely by
    metadata this app alone writes and reads.

    `os.replace()` of a fully written file is atomic on POSIX and on Windows, so
    a reader sees the old claim or the new one and never a fraction of either.
    The temporary file has to be in the SAME directory — a rename across
    filesystems is a copy, which is exactly the tearing being avoided — and the
    clone directory is where it goes. It is cleaned up on failure so a refusal
    never leaves debris inside a checkout `git status` will report on.
    """
    payload = {
        "version": CLAIM_VERSION,
        "item_id": item_id,
        "clone_id": composegen.install_id(clone),
        "url": url,
    }
    fd, name = tempfile.mkstemp(dir=clone, prefix=CLAIM_FILE + ".", suffix=".tmp")
    os.close(fd)
    tmp = Path(name)
    try:
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, clone / CLAIM_FILE)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


_CLIENT_NAMES: dict[str, tuple[str, ...]] = {
    "mysql": ("mysql", "mariadb"),
    "mysqldump": ("mysqldump", "mariadb-dump"),
}
"""Every name each tool may have inside the database container, AzerothCore's first.

The order is the order the container is asked in, and it is only a guess. A
caller that knows which client this game's image ships passes it, and
`_candidates()` moves that spelling to the front — see `_DECLARED_SPELLINGS`."""

_DECLARED_SPELLINGS: dict[str, dict[str, str]] = {
    "mysql": {"mysql": "mysql", "mariadb": "mariadb"},
    "mysqldump": {"mysql": "mysqldump", "mariadb": "mariadb-dump"},
}
"""How each declared `install.native.db.client` spells each tool.

The catalog's `DbFacts.client` is one of `mysql` / `mariadb` and names the
FAMILY, not a binary: the dump tool of the `mariadb` family is `mariadb-dump`,
not `mariadb`. This table is where that translation lives, so no caller has to
know that `mysqldump` and `mariadb-dump` are the pair.

A `tool` or a `client` this table does not know leaves `_CLIENT_NAMES`' order
untouched rather than inventing a name."""

_client_cache: dict[tuple[str, str, str], str] = {}
"""Resolved names, keyed by container, tool AND declared client.

The client is in the key because it decides the ORDER the container is asked
in, and the order decides the answer for an image that ships both spellings
(`command -v a || command -v b` short-circuits on the first that exists)."""


def _candidates(tool: str, client: str | None) -> tuple[str, ...]:
    """Every spelling of `tool`, with the one this game's entry declares first.

    The declared spelling is put in FRONT of the others rather than used
    instead of them, which is the whole difference between reading the catalog
    and trusting it: an image that has been rebuilt, or an entry whose `client`
    was written from the image tag rather than from the image, still resolves
    to the binary that is actually in the container. What the declaration buys
    is the case where nobody can be asked — see `mysql_client()`'s fallback.
    """
    names = _CLIENT_NAMES.get(tool, (tool,))
    declared = _DECLARED_SPELLINGS.get(tool, {}).get(client or "")
    if declared is None or declared not in names:
        return names
    return (declared, *(name for name in names if name != declared))


def mysql_client(db_container: str, tool: str = "mysql", *, client: str | None = None) -> str:
    """The name `db_container` actually answers to for `tool`.

    `client` is the catalog's `install.native.db.client` for this game, or None
    from a caller that does not hold an entry. It changes two things and
    nothing else: which spelling is tried first, and — the reason 7.9 asks for
    it — which one is used when the container cannot be asked at all. Left to
    the guess, that fallback is `mysql`, and a CMaNGOS install on `mariadb:11`
    has no such binary, so every statement afterwards died with `executable
    file not found` rather than with a database error.

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
    key = (db_container, tool, client or "")
    cached = _client_cache.get(key)
    if cached is not None:
        return cached
    candidates = _candidates(tool, client)
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


class FolderCopier(Protocol):
    """Put the folder at `src` at `dest`, replacing whatever is at `dest`.

    The second way to fill `modules/<id>`, beside `Git.clone`, and a seam for
    the reason `Git` is one: what it does reaches outside the process, and the
    engine's own tests must be able to state what it was handed without a real
    tree on disk. It is deliberately NOT a `Git` variant — a path is not a clone
    URL, and routing a copy through `CloneSpec` would drag the HTTP/1.1 and
    `core.autocrlf` pins, the container mount logic and a `file://` URL into a
    job that is a directory copy.

    Two obligations the implementation carries and this engine does not check:
    the destination is REPLACED rather than merged into (a copy is a snapshot of
    the folder, not a union with an older one), and no `.git` is carried across
    (a copy has no upstream, and a half-copied one would answer `remote_url()`
    with a repository this install has nothing to do with). Raising `OSError` is
    how it reports failure; `Applier._copy_folder()` turns that into the
    applier's own vocabulary.
    """

    def __call__(self, src: Path, dest: Path) -> None: ...


@dataclass(frozen=True)
class FolderSource:
    """A folder to copy in, and the copier that will do it.

    The two travel together because neither is usable alone: a path with no
    copier is an install that silently puts nothing anywhere, and the engine
    must not be able to be handed one. `install(folder=...)` is therefore all or
    nothing, and the dataclass is what makes that true at the call site rather
    than in a runtime check.
    """

    path: Path
    copier: FolderCopier


Completer = Callable[[Manifest, Path], Manifest]
"""Finish a manifest from the content that is now at its clone path.

A manifest DERIVED from a link or a folder — rather than shipped — knows its
id, its name and its type, and cannot know one thing more until the content is
on disk: which `conf/*.conf.dist` to activate, which `data/sql/<db>/` to report.
So the derivation is completed here, inside the install that fetched the
content, rather than by a second clone into a scratch directory followed by a
second install. Handed the clone AFTER it is filled and before any step reads
the manifest; whatever it returns is what every later step reads.

`None` is the shipped case — a manifest whose author wrote every field — and
then nothing is called and nothing changes.
"""


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
    client: str | None = None
    """Which client family this server's database image ships: `install.native.db.client`.

    The sibling of `schemas` and carried for the same reason — a per-install
    fact that used to be a literal in this file. `schemas` says which database
    to connect to; this says which BINARY does the connecting, and on a CMaNGOS
    install both were AzerothCore's.

    None means "nothing was declared", and the resolver then keeps the order it
    always had, so every caller that passes nothing behaves exactly as it did.
    It is a hint and not an instruction either way: `mysql_client()` still asks
    the container, and this only decides the order and the answer when the
    container cannot be asked.
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
            mysql_client(self.db_container, client=self.client),
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
class PendingSql:
    """SQL this run put on disk for AzerothCore's importer and did NOT apply.

    `applied_by="db-import"` hands a module's `data/sql/**` to upstream's own
    `UpdateFetcher`, which is right — applying those files by hand leaves the
    `updates` ledger without their hashes and a later real import re-runs them.
    But "right to defer" was written down as `done`: until 2026-09-07 `_sql()`
    appended `sql <glob> → <db>: left to ac-db-import on next start` to the done
    list having run nothing and checked nothing, not even that the glob matched
    a file, and `controller_view._format_report()` ticks every done entry. The
    live applier on yulon-ubuntu therefore reported

        DONE: sql data/sql/db-world/*.sql -> world: left to ac-db-import on next start

    over an install where nothing whatever had been applied and nothing was
    going to be: `docker.start_staged()` names the three long-running services
    so a Start never reaches the importer, and `docker.repair_import()` refuses
    a database that is already complete.

    So the deferral is a value now, and it is a value rather than a sentence
    because the caller that has to tell "applied" from "not applied yet" must
    not do it by grepping English — that would be this same defect one layer up.

    `files` is the glob RESOLVED against the clone, so the count a reader is
    shown is one this run actually took:

    * a tuple of clone-relative paths — what is on disk waiting,
    * `()` — the glob matched nothing, which is not an error and is NOT "this
      module has no SQL". Measured on the real clones, yulon-ubuntu 2026-09-07:
      `mod-aoe-loot` keeps its one file in `data/sql/db-world/base/`, one
      directory below the `data/sql/db-world/*.sql` its manifest names, while
      `mod-solocraft` (1 file) and `mod-transmog` (3, plus an `updates/` folder)
      put theirs exactly there. The layout is per-repository and this pattern is
      Yu'lon's own bookkeeping — `UpdateFetcher.cpp:159-186` joins the module's
      `data/sql` path and walks it itself, so upstream applies what this misses.
      An empty answer therefore means "this app cannot count", and the caller
      must not draw it as "nothing to do",
    * `None` — the path carries a `{key}` and this run could not resolve it.
      A third answer for the same reason `docker.importer_sees_modules()` keeps
      one: globbing the raw `{key}` would match nothing and report a confident
      "no files". No shipped manifest has such a path today (all 20 db-import
      steps across 15 manifests are literal `data/sql/db-*/*.sql`), and
      `_action_templates()` deliberately leaves db-import paths out of
      `required_prompts()`, so there is no value to render one with.
    """

    db: Db
    path: str
    files: tuple[str, ...] | None


@dataclass(frozen=True)
class ApplyReport:
    """What one install/configure/remove run did, did not do, and still needs.

    Four lists, because a run has four outcomes and only the first two were ever
    written down: `done` is what happened, `skipped` is what could not happen,
    `pending_sql` is what was deliberately left to another program to do (see
    `PendingSql`), and `rebuild_required` is what no program in this app can do
    at all. A caller can answer "is this module's SQL in the database?" from
    `pending_sql` alone, without reading a word of the report.
    """

    action: When
    item_id: str
    done: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    rebuild_required: bool = False
    restart_recommended: bool = False
    pending_sql: tuple[PendingSql, ...] = ()


@dataclass
class _Log:
    done: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    pending_sql: list[PendingSql] = field(default_factory=list)
    # Set by `_conf()` the moment it actually writes a byte to a deployed conf
    # file — a template copied in, or a key set — never by a conf step that
    # found nothing to do (the file already there, or no keyed value to write).
    # See `_report()`: this is the fourth thing `restart_recommended` can now see.
    conf_restart: bool = False


class _NoAdoption(Enum):
    """Why an existing checkout was not adopted — one value per fact, per answer.

    `Applier._adoption_refusal()` asks three questions of a checkout whose
    `origin` already matches, and each of the last two has THREE answers, not
    two. Carrying only "adopted / not adopted" out of that made one sentence do
    the work of five, and one of the five was a lie: fact 4 fetches, so a user
    with no internet connection reaches it having passed facts 2 and 3 — the
    folder is provably one this app installed and provably has nothing
    uncommitted in it — and was then told there was no record of it and that
    continuing would throw away their changes. Both halves untrue, to the one
    user who had done nothing at all.

    So the answer that leaves the guard is the fact that stopped it, and
    `_no_adoption_message()` turns each into its own sentence. Collapsing states
    into one answer is the fault this whole guard exists to undo (`Ownership`
    counts the three times it has bitten this codebase); it would be a poor joke
    to re-commit it in the message that reports it.

    Fact 2 is the one place two answers still share a value, and it is on
    purpose: a server dir with no `.yulon-install.json` and one whose file will
    not parse both arrive as `NO_RECORD`. Neither says this app installed the
    folder, so the user's situation and their remedy are the same either way —
    unlike facts 3 and 4, where "no" and "could not ask" describe two different
    people. Written down rather than left to be discovered.
    """

    NO_RECORD = "no-record"
    EDITED = "edited"
    TREE_UNSEEN = "tree-unseen"
    COMMITTED = "committed"
    HISTORY_UNSEEN = "history-unseen"


def _no_adoption_message(refusal: _NoAdoption, rel: str, retry: str) -> str:
    """The sentence each refusal gets: what was found, and what to do about it.

    Written out one by one rather than assembled from clauses, because the whole
    point is that they differ — a template with a hole in it is how they became
    the same sentence in the first place. What they share is the remedy, and the
    remedy is shared because it really is the same: move the folder aside and
    press the button again. Except when nobody could look, where the remedy is
    to make looking possible.
    """
    aside = (
        "Move that folder aside — a module clone holds nothing but the module, so re-cloning "
        f"it costs only the download — and then {retry}."
    )
    messages = {
        _NoAdoption.NO_RECORD: (
            f"{rel} is already a git checkout and there is no record here of one this app "
            f"made. Continuing would run `git fetch` and `git reset --hard` over it, which "
            f"throws away anything you have changed there, so nothing was touched. {aside}"
        ),
        _NoAdoption.EDITED: (
            f"{rel} is a checkout of the repository this module comes from, but it has "
            f"changes in it that were never committed. Continuing would run `git fetch` and "
            f"`git reset --hard` over it, which throws those changes away, so nothing was "
            f"touched. {aside}"
        ),
        _NoAdoption.TREE_UNSEEN: (
            f"{rel} is a checkout of the repository this module comes from, but git would "
            f"not say whether anything in it has been changed. Adopting it would mean "
            f"running `git fetch` and `git reset --hard` over a folder this app could not "
            f"look inside first, so nothing was touched. {aside}"
        ),
        _NoAdoption.COMMITTED: (
            f"{rel} is a checkout of the repository this module comes from with nothing "
            f"uncommitted in it, but it carries commits of your own. Continuing would run "
            f"`git fetch` and `git reset --hard` over it, which moves those commits off the "
            f"branch and leaves them reachable only through git's reflog, so nothing was "
            f"touched. {aside}"
        ),
        _NoAdoption.HISTORY_UNSEEN: (
            f"{rel} is a checkout of the repository this module comes from and nothing in it "
            f"has been changed, but this app could not reach that repository to check whether "
            f"the checkout also carries commits of its own. It will not run `git fetch` and "
            f"`git reset --hard` over a folder it could not finish checking, so nothing was "
            f"touched. That check needs the internet: get back online and {retry}. If the "
            f"checkout is your own work rather than an older install, move that folder aside "
            f"and {retry} instead."
        ),
    }
    return messages[refusal]


# --------------------------------------------------- the answers to prompts

_INT = re.compile(r"[+-]?\d+")

_BOOL_WORDS = frozenset({"0", "1", "true", "false", "yes", "no", "on", "off"})

_SAFE_IN_QUERY = re.compile(r"^[A-Za-z0-9_.-]+$")
"""What an answer may contain before it is pasted into an `ExistsCheck.query`.

Not an escape: a refusal, exactly as `dbreads._SAFE_PREFIX` is one and for the
same reason. Every answer that reaches a check today is a character GUID, and
the set of characters a GUID needs does not include a quote, a semicolon or a
backslash — so the answer to anything else is "I will not use this", rather than
an escaping rule that has to be right on four cores and two SQL modes.
"""


def _fields(template: str) -> set[str]:
    """The `{key}` names a `_render()` of this template would look up."""
    return {
        name.split(".")[0].split("[")[0] for _, name, _, _ in Formatter().parse(template) if name
    }


def _action_templates(manifest: Manifest, action: When) -> list[str]:
    """Every string this action would put through `_render()`, in the engine's own order.

    Read off the `Applier` methods below rather than guessed: `_patches` and
    `_sql` filter on `when`, `_sql` returns before rendering for a `db-import`
    step (which is why `mod-ah-bot`'s SQL glob is absent here), and `_conf` runs
    for install and configure only and skips a glob or a non-`.conf` file. A
    template this misses is a value the user is never asked for; a template it
    invents is a question nobody needs to answer — so the two must be kept in
    step, and `test_required_prompts_are_only_the_ones_the_action_actually_renders`
    is what says they are.
    """
    out = [patch.replace for patch in manifest.patches if patch.when == action]
    for step in manifest.sql:
        if step.when != action or step.applied_by != "direct":
            continue
        out.append(step.statement if step.statement is not None else step.path or "")
    if action in ("install", "configure"):
        for conf in manifest.conf:
            if _is_glob(conf.file) or not conf.file.endswith(_CONF_KEY_WRITE_SUFFIXES):
                continue
            out += [key.default for key in conf.keys if key.default is not None]
    return out


def required_prompts(manifest: Manifest, action: When) -> tuple[Prompt, ...]:
    """The manifest's prompts whose value this action would actually render.

    The question the Modules tab has to answer before it can ask a human
    anything, and the reason it is per-ACTION rather than per-manifest: removing
    `mod-ah-bot` renders nothing at all, and a dialog asking for the AH bot's
    GUID before deleting it would be a question about nothing.

    Order is the manifest's own, so a check that reads an earlier answer (
    `mod-ah-bot`'s `bot_account`, whose `ExistsCheck` also names `{bot_guid}`)
    is run after the answer it depends on has been validated.
    """
    wanted: set[str] = set()
    for template in _action_templates(manifest, action):
        wanted |= _fields(template)
    return tuple(prompt for prompt in manifest.prompts if prompt.key in wanted)


def check_answer(prompt: Prompt, value: str) -> str:
    """Why this answer cannot be used, or `""` if it can. Never raises.

    A returned sentence, not an exception, because both callers want to say it
    rather than to unwind: the dialog puts it under the box the user is still
    typing in, and the applier wraps it in a refusal that names the module.
    """
    text = value.strip()
    if not text:
        return "this cannot be left empty"
    if prompt.kind == "int":
        return "" if _INT.fullmatch(text) else "this must be a whole number"
    if prompt.kind == "float":
        try:
            float(text)
        except ValueError:
            return "this must be a number"
        return ""
    if prompt.kind == "bool":
        return "" if text.lower() in _BOOL_WORDS else "this must be yes or no"
    if prompt.kind == "choice":
        return "" if text in prompt.choices else "choose one of: " + ", ".join(prompt.choices)
    return ""


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
        unmodified: Callable[[Path, str], bool | None] | None = None,
        no_local_commits: Callable[[Path, str | None], bool | None] | None = None,
        server_dir_claim: Callable[[Path], Ownership] | None = None,
        world_running: Callable[[], bool | None] | None = None,
        start_database: Callable[[], bool] | None = None,
    ) -> None:
        self.server_dir = server_dir
        self.git: Git = git if git is not None else RunnerGit()
        self.sql = sql
        # "Is this install's worldserver up?" — a seam, because the answer lives
        # in Docker and this module does not touch Docker (module docstring,
        # style-guide §3/§5). Three-valued for the reason every other reader
        # here is: `None` is "could not ask", which is not "no". Default absent,
        # and absent means the behaviour every caller has today — no guard —
        # which is `phase8-designs/c-operators-risk.md:345`'s own requirement
        # and the reason wiring it is a separate, later change.
        #
        # PRIVATE, and named the way `party.py:996` names the same seam, because
        # a public `self.world_running` here would collide with
        # `controller_wow_tortoise.autoupdate.GuardedApplier`, which already
        # carries one (`autoupdate.py:461`) for its own updater guard and
        # assigns it AFTER `super().__init__`. Sharing the name would have wired
        # this guard live on Tortoise alone, by accident, with a `bool` seam
        # where this one is `bool | None` — one game guarded, three not, and no
        # page saying so. That subclass is nonetheless the one caller in the
        # tree that ALREADY holds the fact this guard needs.
        self._world_running = world_running
        # "Put this install's database back, alone." The other half of the
        # refusal above, and a seam for the same reason: the primitive is
        # `docker.start_database()` and this module never touches Docker.
        #
        # It exists because the refusal's own instruction could not be followed.
        # T2 pressed *"Press Stop, then install again"* through the app's own
        # Stop on 2026-09-09 and the retry died on `container ... is not
        # running`: `stop_staged()` takes the database down with the world, and
        # `DockerSql` is a `docker exec` into a container that is no longer
        # there. `docker.start_database()` put it back alone in 6.6 s with the
        # world still down, which is exactly the state the guard permits. So the
        # route was missing a caller, not a primitive
        # (`8.7a-direct-sql-yulon-ubuntu2-2026-09-09/README.md`, *The dead end*).
        #
        # Returns whether it HAD to start it, so the report can say so only when
        # something happened: a `done` line for a start that did not take place
        # is `PendingSql`'s closed bug wearing a different hat. Absent means the
        # behaviour every caller had before this landed, byte for byte.
        self._start_database = start_database
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
        # "Is this path exactly what the checkout's HEAD committed?", narrowed
        # the same way and for the same reasons. Two guards need it: the
        # adoption rule in `_require_own_clone()`, which will not adopt a
        # checkout somebody has edited, and the one defence against an upstream
        # repository that tracks a file at `CLAIM_FILE`'s name.
        self.unmodified: Callable[[Path, str], bool | None] = (
            unmodified
            if unmodified is not None
            else (
                self.git.is_unmodified
                if isinstance(self.git, TreeReader)
                else RunnerGit().is_unmodified
            )
        )
        # "Does HEAD carry commits the update would throw away?", narrowed the
        # same way again. The third question and not a rephrasing of the second:
        # `unmodified` compares the tree and the index against HEAD, so it
        # answers "clean" for a checkout somebody has committed their own work
        # into. Only `_adoption_refusal()` asks this, and it is the fact that
        # keeps adoption from meaning "clean tree, therefore nothing of yours
        # here".
        self.no_local_commits: Callable[[Path, str | None], bool | None] = (
            no_local_commits
            if no_local_commits is not None
            else (
                self.git.no_local_commits
                if isinstance(self.git, HistoryReader)
                else RunnerGit().no_local_commits
            )
        )
        # "Did this app create the server directory this clone is under?" — a
        # seam rather than a module-level call, for the reason the two above are
        # seams: it reaches outside the process, and a test of the guard should
        # not need a real native-engine state file on disk to state its answer.
        # The default is this module's own `server_dir_claim()`, which is where
        # the `catalog.native` import lives and why it lives inside a function.
        self.server_dir_claim: Callable[[Path], Ownership] = (
            server_dir_claim if server_dir_claim is not None else _SERVER_DIR_CLAIM
        )

    # -- public ------------------------------------------------------------

    def clone_dir(self, manifest: Manifest) -> Path:
        """Where this item's clone lives (`modules/<id>`, `ale_scripts/<id>`, ...)."""
        return self.server_dir / CLONE_DIRS[manifest.type] / manifest.id

    def install(
        self,
        manifest: Manifest,
        values: Mapping[str, str] | None = None,
        *,
        folder: FolderSource | None = None,
        complete: Completer | None = None,
    ) -> ApplyReport:
        """Clone or copy, deploy, patch, run install-time SQL, activate conf, copy client/DBC.

        `folder` is the second way to fill `modules/<id>`: the bytes come from a
        directory on the user's own disk through `FolderSource.copier` instead
        of from a repository through `Git.clone`. It is the ONLY thing that
        changes — the claim, `include.sh` and every step after them are the
        same statements, over whatever is now at the clone path. A manifest that
        also carries a `source` is a caller contradiction and is refused before
        either route runs.

        `complete` finishes a DERIVED manifest from the content that has just
        landed; see `Completer`. It runs after the clone or the copy, and
        everything from `_deploy()` onwards reads what it returned.

        `_check_values()` is NOT re-run against the completed manifest: it is
        the caller's answers that are being checked, and the fields a completer
        fills (conf files to activate, SQL to report) carry no prompts to
        answer. Named here because it is the one step the completed manifest
        does not reach.
        """
        vals = self._values(manifest, values)
        log = _Log()
        self._check_values(manifest, "install", vals, log)
        clone = self.clone_dir(manifest)
        if folder is not None and manifest.source is not None:
            raise ApplyError(
                f"{manifest.id}: one source, not two — this manifest is cloned from "
                f"{manifest.source.url} and was also handed the folder {folder.path} to copy. "
                f"Nothing was changed."
            )
        if folder is not None:
            self._require_own_clone(manifest, clone, "install")
            self._copy_folder(folder, clone, log)
        elif manifest.source is None:
            # A manifest with no source never clones, so the guard used to sit
            # entirely inside the branch below — and that left `install()` with
            # the hole `configure()` was given a guard for. An install-time
            # `in_clone` patch rewrites a line in a file at `modules/<id>`, and
            # for a SOURCELESS manifest every file there was put there by
            # somebody else BY DEFINITION: this app never cloned it. So it is
            # the case that needs the question asked most, not least. Gated on
            # such a patch actually existing, like `configure()`, so a refusal
            # is never about a folder this run would not have touched.
            if clone.exists() and any(p.in_clone and p.when == "install" for p in manifest.patches):
                self._require_own_clone(manifest, clone, "install")
        else:
            self._require_own_clone(manifest, clone, "install")
            try:
                self.git.clone(
                    CloneSpec(
                        url=manifest.source.url,
                        dest=clone,
                        branch=manifest.source.branch,
                        sparse_path=manifest.source.sparse_path,
                        rev=manifest.source.rev,
                    )
                )
            except GitError as exc:  # one failure vocabulary for the whole applier
                raise ApplyError(str(exc)) from exc
            log.done.append(f"clone {manifest.source.url} → {_rel(self.server_dir, clone)}")
        if folder is not None or manifest.source is not None:
            # This app filled the folder, by either route, so both of the files
            # it writes INTO a checkout go in — and they are written here rather
            # than in a helper each branch calls, because the ledger row
            # `apply.py::install::touch` names this function and a walker that
            # stopped finding it would report a write site that had gone.
            url = manifest.source.url if manifest.source is not None else ""
            try:
                write_clone_claim(clone, item_id=manifest.id, url=url)
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
        if complete is not None:
            manifest = self._completed(manifest, clone, complete)
        self._deploy(manifest, clone, log)
        self._patches(manifest, clone, vals, "install", log)
        self._sql(manifest, clone, vals, "install", log)
        self._conf(manifest, clone, vals, log)
        self._client(manifest, clone, log)
        self._dbc(manifest, clone, log)
        return self._report("install", manifest, log)

    def configure(self, manifest: Manifest, values: Mapping[str, str] | None = None) -> ApplyReport:
        """Re-apply the value-bearing steps: configure-time patches/SQL and conf keys.

        **Nothing in the shipped app calls this yet** — every `.configure(` in
        the tree is in a test (grepped, 2026-09-01). The ownership guard below
        is correct and it is not live protection for anything a user can press:
        the Modules tab binds Install and Remove only. Kept because the manifest
        primitive it applies is real and its UI is a roadmap item, but do not
        count it when reasoning about what actually guards a user today.
        """
        vals = self._values(manifest, values)
        log = _Log()
        self._check_values(manifest, "configure", vals, log)
        clone = self.clone_dir(manifest)
        if clone.exists() and any(p.in_clone and p.when == "configure" for p in manifest.patches):
            # The third writer through `clone_dir()`, and the smallest: an
            # `in_clone` patch edits a file INSIDE the checkout. It cannot
            # delete anything, but it is still this app rewriting a line in a
            # file it did not put there, so it asks the same question. Gated on
            # the manifest actually having such a patch: a refusal about a
            # folder this run would never touch would be a refusal about
            # nothing.
            self._require_own_clone(manifest, clone, "configure")
        self._patches(manifest, clone, vals, "configure", log)
        self._sql(manifest, clone, vals, "configure", log)
        self._conf(manifest, clone, vals, log)
        return self._report("configure", manifest, log)

    def remove(self, manifest: Manifest, values: Mapping[str, str] | None = None) -> ApplyReport:
        """Run remove-time patches/SQL, delete deployed files and the clone. DB rows are kept."""
        vals = self._values(manifest, values)
        log = _Log()
        self._check_values(manifest, "remove", vals, log)
        clone = self.clone_dir(manifest)
        if clone.exists():
            # Before the SQL, not next to the `rmtree` below: a refusal must
            # leave the install exactly as it was, and remove-time SQL is not
            # undoable. The same guard as `install()`, because the exposure is
            # the same one and worse — that `rmtree` needs no git seam to
            # destroy a directory whose only crime is matching a catalog id.
            self._require_own_clone(manifest, clone, "remove")
        self._patches(manifest, clone, vals, "remove", log)
        self._sql(manifest, clone, vals, "remove", log)
        for step in manifest.deploy:
            self._undeploy(step, clone, log)
        if clone.exists():
            shutil.rmtree(clone)
            log.done.append(f"rm -r {_rel(self.server_dir, clone)}")
        return self._report("remove", manifest, log)

    # -- filling the clone from somewhere that is not git ------------------

    def _copy_folder(self, folder: FolderSource, clone: Path, log: _Log) -> None:
        """Run the copier, and refuse anything short of a folder at the clone path.

        Two failures, one vocabulary. `OSError` — an unreadable source, a full
        disk, a permission — becomes `ApplyError`, exactly as `GitError` does
        for a clone: every caller of `install()` handles that one type, and a
        bare `PermissionError` reaching the Modules tab arrives as an unhandled
        worker exception rather than as a report line.

        And a copier that returned having put NOTHING at `clone` is refused
        too, rather than believed. It is the failure that costs most if it is
        not caught here: the very next statements write this app's claim into
        that path and touch `include.sh` in it, so the run would go on to
        manufacture the evidence that the folder is a module this app
        installed — and report a rebuild for content that is not there. The
        check is `is_dir()` on the destination, which is the one thing every
        implementation of this seam must have produced.
        """
        try:
            folder.copier(folder.path, clone)
        except OSError as exc:
            raise ApplyError(
                f"{folder.path} could not be copied into "
                f"{_rel(self.server_dir, clone)}: {exc}. Nothing was changed."
            ) from exc
        if not clone.is_dir():
            raise ApplyError(
                f"copying {folder.path} left nothing at {_rel(self.server_dir, clone)}, so there "
                f"is no module there to install. Nothing was changed."
            )
        log.done.append(f"copy {folder.path} → {_rel(self.server_dir, clone)}")

    def _completed(self, manifest: Manifest, clone: Path, complete: Completer) -> Manifest:
        """The completer's manifest, once it is still a manifest for the SAME item.

        `id`, `type` and `game` are the three fields everything already done
        depends on: `clone_dir()` is built from `type` and `id`, so they name
        the folder that has just been filled and the claim written inside it,
        and `game` is which install this manifest belongs to at all. A completer
        that changed one of them would have this run report an install of an
        item nothing installed, over another item's clone — so the difference is
        raised rather than relabelled.

        Only those three. A completer's whole job is to add conf files, SQL
        steps and the rest from what it found on disk, and checking those would
        be checking that it did nothing.

        Exceptions out of `complete` itself are NOT wrapped: this seam is the
        caller's own derivation code rather than a subprocess or a filesystem,
        and its failures are its own to name. `_copy_folder()` wraps `OSError`
        because a copier IS the filesystem.
        """
        finished = complete(manifest, clone)
        changed = [
            f"{field} ({getattr(manifest, field)!r} → {getattr(finished, field)!r})"
            for field in ("id", "type", "game")
            if getattr(finished, field) != getattr(manifest, field)
        ]
        if changed:
            raise ApplyError(
                f"{manifest.id}: finishing this manifest from what is at "
                f"{_rel(self.server_dir, clone)} changed {', '.join(changed)}, so it is no longer "
                f"the item that was installed there. Nothing further was changed."
            )
        return finished

    # -- the answers -------------------------------------------------------

    def _check_values(
        self, manifest: Manifest, action: When, vals: Mapping[str, str], log: _Log
    ) -> None:
        """Refuse an answer this action cannot use, BEFORE anything is written.

        This used to happen at the end. `install()` cloned the repository, wrote
        `include.sh`, ran the deploy and the patches, and only reached the
        missing value inside `_conf()` — so a `mod-ah-bot-plus` install with no
        GUID left a full checkout at `modules/mod-ah-bot-plus` on disk and
        reported `conf AuctionHouseBot.GUIDs: no value for {bot_guid}`. Half an
        install is worse than none of one: the next attempt then meets the
        ownership guard over a folder this app itself abandoned.

        Nothing here writes, and the two things it reads are the answers it was
        handed and (through `_check_exists`) the server's own database.
        """
        for prompt in required_prompts(manifest, action):
            value = vals.get(prompt.key)
            if value is None:
                raise ApplyError(
                    f"{manifest.id}: {prompt.question} — no value for {{{prompt.key}}}. "
                    f"Nothing was changed."
                )
            problem = check_answer(prompt, value)
            if problem:
                raise ApplyError(
                    f"{manifest.id}: {prompt.question} — {problem}, and {value!r} is not. "
                    f"Nothing was changed."
                )
            self._check_exists(manifest, prompt, vals, log)

    def _check_exists(
        self, manifest: Manifest, prompt: Prompt, vals: Mapping[str, str], log: _Log
    ) -> None:
        """Ask the database whether the thing this answer names is really there.

        Three outcomes, and the difference between the last two is the whole
        point of writing it this way:

        * a row came back — nothing is said, the install goes on;
        * **no row came back** — the answer is refused by name, because a GUID
          that matches no character is not an error inside the AH bot module,
          it is a module that silently posts nothing;
        * **the question could not be put** — no reader on this seam, or the
          query itself failed — which is reported in `skipped` and does NOT
          refuse. A database is legitimately stopped while a module is being
          installed, and an install that a stopped server can veto would be a
          worse defect than the one this check is here for.
        """
        check = prompt.exists
        if check is None:
            return
        unsafe = sorted(
            key
            for key in _fields(check.query) | _fields(check.missing)
            if not _SAFE_IN_QUERY.fullmatch(vals.get(key, ""))
        )
        if unsafe:
            raise ApplyError(
                f"{manifest.id}: {', '.join(unsafe)} cannot be used in a database question "
                f"(letters, digits, dot, dash and underscore only). Nothing was changed."
            )
        if not isinstance(self.sql, SqlReader):
            log.skipped.append(
                f"{prompt.key}={vals[prompt.key]}: NOT checked against the database "
                f"(this install has no database reader), so a wrong answer here will look "
                f"like a module that does nothing"
            )
            return
        statement = _render(check.query, vals, f"prompt {prompt.key}")
        try:
            rows = self.sql.query(check.db, statement)
        except Exception as exc:  # noqa: BLE001 - every seam failure is one answer here
            logger.warning(f"could not check {prompt.key} against {check.db}: {exc}")
            log.skipped.append(
                f"{prompt.key}={vals[prompt.key]}: NOT checked against the database "
                f"({exc}), so a wrong answer here will look like a module that does nothing"
            )
            return
        if not rows.strip():
            raise ApplyError(
                f"{manifest.id}: {_render(check.missing, vals, f'prompt {prompt.key}')}. "
                f"Nothing was changed."
            )

    # -- the guard ---------------------------------------------------------

    def _require_own_clone(self, manifest: Manifest, clone: Path, action: When) -> None:
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
        4. A checkout of the right repository with no claim, in a server
           directory this app installed, with nothing modified in it and no
           commits of its own: adopted. `_adoption_refusal()` has the four
           facts, why three of them were not enough, and — when one of them
           says no — which one the user is told about.
        5. Everything else is a checkout this app did not make. `origin` is
           asked only to say WHICH refusal — a different repository, this
           repository, or a git that would not answer — because every branch
           of it refuses. That is the divergence from
           `native.refuse_unowned_checkout()`, which can be reached with the
           question already answered by its caller.

        `UNKNOWN` is refused with its own sentence, per `Ownership`: a damaged
        claim means this app knows LESS than an absent one, so it must not act
        more freely.

        **`action` is here because a refusal's REMEDY is not the same for the
        three callers, and one wrong remedy causes the harm it prevents.** The
        first version said "move the folder aside" everywhere. For `install()`
        that is right and cheap. For `remove()` it is advice to strand yourself:
        moving the clone aside makes `clone.exists()` false, and then this run's
        remove-time SQL and `_undeploy()` either fail outright (an `in_clone`
        patch or an SQL file that is no longer there) or quietly leave the
        deployed files behind — so the module stays half-installed in the
        database with no route back through the app. Every `remove()` refusal
        therefore keeps the user pointed at removing, and says why the folder
        must not simply be made to disappear.

        **Does `remove()` accept weaker proof than `install()`? Yes — one
        specific piece of it, and no more.** The asymmetry is in what a refusal
        COSTS. Refusing `install()` costs the user nothing they had: their
        folder is untouched, and the remedy (move it aside, install again) is a
        download. Refusing `remove()` costs them the only in-app route to a
        clean database, and there is no second one — the rows and the deployed
        files stay, forever, and the folder they were told to move aside is not
        the problem. The relocation case makes that a real lockout rather than a
        theoretical one: `install_id()` is a hash of the ABSOLUTE path, so
        moving or renaming a server folder makes EVERY claim in it read
        `UNKNOWN` at once.

        So `remove()` — and only `remove()` — also accepts
        `claim_written_by_this_app()`: a claim that parses, carries this
        version, names THIS item, and differs from `OWNED` in exactly one field,
        the folder it was written in. That is this app's own handwriting; a
        hand-installing user does not produce it, and a stranger's checkout has
        no claim file at all. What it cannot distinguish is a MOVED install from
        a COPIED one — nothing on disk can — and that is the whole of what is
        given up: in a copy, `remove()` deletes a clone that this app made a copy
        of, for an item the user has just asked to remove. `install()` is not
        given the same licence, because there the same input would authorise
        `git reset --hard` inside a copy somebody may be keeping precisely
        because it is not the original.

        **Be exact about what that weaker path proves, because it returns before
        `remote_url()` is ever reached.** It never asks git anything: its whole
        evidence is a `.git` directory plus a file at `CLAIM_FILE`'s name whose
        JSON has this version, this item id, and some other folder's
        `install_id()`. It does NOT establish that the checkout is a checkout of
        the manifest's repository. Writing such a file needs local write access
        to this exact path under a server directory, which is inside the trust
        boundary already, and it is not a new asymmetry — the `OWNED` path is
        origin-blind for the same reason and by the same design (the claim is
        its own corroboration). Recorded so the next reader does not mistake
        "this app's own handwriting" for "this is the right repository".
        """
        if not clone.exists():
            return
        rel = _rel(self.server_dir, clone)
        retry = f"{action} {manifest.id} again"
        if not clone.is_dir():
            raise ApplyError(
                f"{rel} is a file, not this app's clone of {manifest.id}. Nothing was changed. "
                f"Move it aside and {retry}."
            )
        url = manifest.source.url if manifest.source is not None else ""
        if not (clone / ".git").is_dir():
            # A folder with no `.git` used to be somebody else's by definition,
            # and the claim was never even read here. It is not any more: a
            # module installed from a FOLDER is a copy, a copy carries no `.git`
            # (see `FolderCopier`), and the only thing separating this app's own
            # copy from a tarball somebody unpacked at this path is the claim
            # inside it — the same evidence, in the same file, that authorises
            # re-cloning and removing a checkout. Without this the app could
            # install a module from a folder and then never uninstall it.
            #
            # It is the claim and nothing else. `origin` and the adoption facts
            # below all need a repository, and a copy has none: git would either
            # refuse to answer or answer about whatever checkout the folder was
            # copied FROM, which is a repository this install has nothing to do
            # with. So an unrecognised folder still falls through to the
            # leftovers refusal exactly as it always did — and a damaged claim
            # is itself one of the leftovers, which is why UNKNOWN needs no
            # separate sentence here.
            owned = read_clone_claim(clone, item_id=manifest.id)
            if owned is Ownership.OWNED:
                return
            if owned is Ownership.UNKNOWN and self._relocation_licence(
                manifest, clone, rel, action
            ):
                return
            leftovers = sorted(item.name for item in clone.iterdir())
            if leftovers:
                raise ApplyError(
                    f"{rel} already has files in it and was not put there by this app "
                    f"({', '.join(leftovers[:5])}). Nothing was changed. Move that folder "
                    f"aside — or delete it yourself if you no longer want it — and then "
                    f"{retry}.{self._removal_note(action, manifest)}"
                )
            return
        owned = read_clone_claim(clone, item_id=manifest.id)
        if owned is Ownership.OWNED:
            return
        if owned is Ownership.UNKNOWN and self.unmodified(clone, CLAIM_FILE) is True:
            # The one thing that stops an upstream repository which TRACKS a
            # file at this name from locking a module out for good. After
            # `git reset --hard FETCH_HEAD` the repository's own copy is back at
            # that path, it reads `UNKNOWN`, and every later install, configure
            # and remove of that module refuses — permanently, with a message
            # about a file the user cannot delete without dirtying their
            # checkout. Git can tell the two apart with certainty: a claim THIS
            # app wrote is never committed to a module's repository, so a file
            # here that `status` reports as unchanged from HEAD is the
            # repository's content and not a claim at all. Treated as if the
            # name were free, so the folder is judged on its origin and on the
            # adoption evidence below, exactly as an unclaimed one is.
            #
            # `is True` and not truthiness: `None` is "git could not be asked",
            # which must keep refusing.
            logger.warning(
                f"{clone / CLAIM_FILE} is committed content of that repository, not a claim "
                f"this app wrote; judging {rel} on its origin instead"
            )
            owned = Ownership.UNCLAIMED
        if owned is Ownership.UNKNOWN:
            if self._relocation_licence(manifest, clone, rel, action):
                return
            # "Move the folder aside" is offered to the two callers it is a
            # remedy for and withheld from `remove()`, for which it is the
            # opposite of one — see this method's docstring.
            put_back = "Put this server folder back where it was installed"
            if action != "remove":
                put_back += f", or move {rel} aside,"
            raise ApplyError(
                f"{rel} holds a {CLAIM_FILE} this app cannot read as its own, so it cannot tell "
                f"whether that checkout is its own {manifest.id} or your own work. Nothing was "
                f"changed. The likeliest cause is an install folder that was moved, renamed or "
                f"copied since {manifest.id} was installed: that file records the folder's own "
                f"path, so it stops matching. {put_back} and then "
                f"{retry}.{self._removal_note(action, manifest)}"
            )
        remote = self.remote_url(clone)
        if remote is None:
            raise ApplyError(
                f"{rel} is a git checkout, but git would not say what it is a checkout of, so "
                f"nothing was changed. Move that folder aside and then "
                f"{retry}.{self._removal_note(action, manifest)}"
            )
        if url and not same_repo(remote, url):
            raise ApplyError(
                f"{rel} is a checkout of {remote}, not of {url}. Nothing was changed. Move that "
                f"folder aside and then {retry}.{self._removal_note(action, manifest)}"
            )
        branch = manifest.source.branch if manifest.source is not None else None
        # A manifest with no source never clones, so there is no repository to
        # be a checkout OF and nothing to adopt: the folder is somebody else's
        # by definition, which is what `NO_RECORD` says.
        refusal = (
            self._adoption_refusal(clone, remote, url, branch) if url else _NoAdoption.NO_RECORD
        )
        if refusal is None:
            return
        raise ApplyError(
            _no_adoption_message(refusal, rel, retry) + self._removal_note(action, manifest)
        )

    def _relocation_licence(self, manifest: Manifest, clone: Path, rel: str, action: When) -> bool:
        """May a `remove()` proceed over this app's own claim naming another folder?

        The weaker proof `remove()` — and only `remove()` — accepts, argued in
        `_require_own_clone()`'s docstring. One function rather than a paragraph
        repeated in two branches: the checkout case and the copied-folder case
        reach `UNKNOWN` by different routes and the licence is the same one, so
        a rule stated twice would be a rule that can diverge (style-guide §4).

        Never for `install()` or `configure()`, whatever the folder holds.
        """
        if action != "remove" or not claim_written_by_this_app(clone, item_id=manifest.id):
            return False
        logger.warning(
            f"{rel} holds this app's own claim for {manifest.id} naming a different "
            f"folder (this install was moved, renamed or copied); removing anyway, "
            f"because refusing would leave {manifest.id} in the database with no way out"
        )
        return True

    def _removal_note(self, action: When, manifest: Manifest) -> str:
        """The sentence a `remove()` refusal must carry, and the other two must not.

        Every remedy above ends in "and then remove <id> again", and that "then"
        is the whole point: a user who moves the folder aside and stops has not
        removed anything, they have hidden the files and kept the database rows.
        Only this app knows which rows and which deployed files those are.
        """
        if action != "remove":
            return ""
        return (
            f" Removing {manifest.id} also undoes the database changes it made and deletes the "
            f"files it deployed elsewhere in this server, and only this app can do that — so "
            f"moving or deleting this folder is not by itself an uninstall."
        )

    def _adoption_refusal(
        self, clone: Path, remote: str, url: str, branch: str | None
    ) -> _NoAdoption | None:
        """Adopt an existing checkout of the RIGHT repository — on four facts, not one.

        `None` is the adoption; anything else is the fact that stopped it, which
        the caller turns into that fact's own sentence. Which fact, and which of
        its answers, is the whole of what a refused user needs to hear — see
        `_NoAdoption`.

        The gap this narrows: a module installed by a build older than the claim
        file has no claim, so the first Install after that change is refused. A
        migration on `origin` alone would have undone the guard exactly — a
        matching `origin` is what EVERYBODY with this catalog entry has, and
        "the user's own checkout of the right repository" is the case the guard
        was written for.

        **It does not close that gap for every existing user, and the commit
        that introduced it said it did.** A module whose upstream ships no
        `include.sh` gets one written by `install()` itself; that file is
        untracked; fact 3 asks about the whole tree; so exactly those modules
        fail adoption and their users still get the refusal and its remedy. The
        behaviour is right — see the paragraph on untracked files below — but
        the claim was overbroad, and this is where a reader will look for it.

        Four independent facts, and a hand-installed module fails one of them:

        1. `origin` names the repository the manifest does (established by the
           caller, and passed in so this reads as one rule rather than half of
           one).
        2. `server_dir_claim()` is `OWNED`: `.yulon-install.json`, OUTSIDE this
           folder, at the server dir, says this app CREATED this server
           directory. A user who cloned a module into their own AzerothCore tree
           cannot produce that file, and it is not something a module repository
           can carry — it is not in the folder being adopted at all. This is the
           fact that does the work.
        3. The checkout is unmodified. `git reset --hard FETCH_HEAD` destroys
           exactly the tracked changes `status` reports, so an empty answer is a
           proof that adopting costs nothing, and `None` — git could not be
           asked — is not that proof and refuses. It refuses under its own
           name (`TREE_UNSEEN`, not `EDITED`): nobody established that this
           checkout has anything uncommitted in it.
        4. HEAD carries no commit the update would not. **`status` is not this
           question and cannot be made into it.** It compares the working tree
           and the index against HEAD; it says nothing at all about what HEAD
           itself is. A user who cloned this catalog's own repository into a
           directory this app created and then COMMITTED their customisations
           has a perfectly clean tree, passes facts 1-3, and the update that
           adoption authorises — `fetch` + `reset --hard FETCH_HEAD` — moves
           HEAD off those commits and leaves them reachable only through the
           reflog. So the fourth fact asks git the question the third one only
           looks like: `rev-list FETCH_HEAD..HEAD` is empty, after
           `no_local_commits()` has run the update's own fetch to put a truthful
           commit behind `FETCH_HEAD`. `None` — git could not be asked, or the
           fetch could not reach the remote — refuses, per `Ownership`'s three
           outcomes: "nothing to compare against" is not "nothing to lose". It
           refuses as `HISTORY_UNSEEN` rather than `COMMITTED`, because the
           commonest way to reach it is a machine that is offline, and telling
           somebody who has committed nothing that their commits are in the way
           would be the collapse this fact was added to end. `branch` is the
           manifest's, because the manifest's is what the update will fetch.

           **This fact costs a network round trip, and that is why it is asked
           last.** It runs only once facts 2 and 3 have both said yes, moments
           before the install fetches the same refs anyway; an offline machine
           gets a refusal for an install that could not have proceeded. Its
           first version compared against `refs/remotes/origin/<branch>` and
           `refs/remotes/origin/HEAD`, and the second of those is never
           refreshed by the branchless `fetch origin HEAD` this app runs — so
           one legitimate update made every already-updated module look like the
           user's own work and refused exactly the population the fact exists to
           let through. `git.RunnerGit.no_local_commits()` carries the
           measurement.

           This is the third time in this codebase that a question with more
           states than the answer being carried has produced a bug (see
           `native.read_claim()`'s absent-vs-unreadable collapse and
           `read_clone_claim()`'s note about it). "Clean" and "has nothing of
           the user's in it" are different facts, and only one of them is what
           `status` returns.

        The whole tree, `"."`, not one path, for fact 3: there is no file in a
        module clone this app can point at as the one that matters. That also
        means an UNTRACKED file blocks adoption, which is stricter than the harm
        requires — a hard reset does not delete untracked files — and it is the
        `include.sh` case above. Deliberate, and NOT allowlisted even for that
        one generated name: the file this app writes is empty, a user's
        `include.sh` need not be, so an exact-name allowlist would have to
        become a content check to be safe, and a content check is the first step
        of deciding which of somebody's untracked files are innocent. The
        direction of this error is a re-clone; the direction of that one is lost
        work.
        """
        if self.server_dir_claim(self.server_dir) is not Ownership.OWNED:
            return _NoAdoption.NO_RECORD
        # `is False` / `is not True` throughout, never truthiness: the whole
        # point is that these seams answer three things, and which of the two
        # refusals it is decides what the user is told.
        clean = self.unmodified(clone, ".")
        if clean is not True:
            if clean is False:
                return _NoAdoption.EDITED
            return _NoAdoption.TREE_UNSEEN
        nothing_of_theirs = self.no_local_commits(clone, branch)
        if nothing_of_theirs is not True:
            if nothing_of_theirs is False:
                return _NoAdoption.COMMITTED
            return _NoAdoption.HISTORY_UNSEEN
        logger.info(
            f"adopting the existing checkout at {_rel(self.server_dir, clone)}: it is a clean "
            f"checkout of {remote} (the manifest's {url}) with no commits of its own, inside a "
            f"server directory this app installed"
        )
        return None

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
        self._refuse_direct_sql_into_a_running_world(manifest, when)
        # Second, and never first: a press against a live world is refused above
        # having started nothing. Starting containers under a world this guard
        # is about to refuse would undo the guard's own advice on a stack the
        # user had Stopped.
        if self._start_the_database_for_direct_sql(manifest, when, log):
            # THIRD, and the reason the guard is asked twice for one press.
            # `start_database()` waits for the database to report healthy, up to
            # 180 s (`docker._DB_HEALTHY_TIMEOUT_SECONDS = 180.0`, read on the
            # tree 2026-09-09; these comments said 120 s until then). The first
            # reading is
            # that old by the time the first statement would be sent, and the
            # Server tab's Start is a button the same user can press in the
            # meantime — as is a `compose up` in another terminal. A world
            # started inside that window is holding these tables when the writes
            # land, which is the whole of what this guard is for, and the report
            # would have said the world was left stopped.
            #
            # The same sentence as the first refusal, deliberately: it is the
            # same fact and the same remedy, and a second vocabulary for one
            # rule is how a user comes to meet two. Nothing is reported when it
            # raises — `install()` never returns a report — so the database this
            # run started is left up and unmentioned. That is the safe side of
            # the trade: a container that is running when it need not be, rather
            # than rows written under a live world.
            self._refuse_direct_sql_into_a_running_world(manifest, when)
        for step in manifest.sql:
            if step.when != when:
                continue
            if step.applied_by == "db-import":
                log.pending_sql.append(self._pending_sql(step, clone))
                continue
            if self.sql is None:
                log.skipped.append(f"sql → {step.db}: no SQL runner configured")
                continue
            self._run_sql(step, clone, vals, log)

    def _refuse_direct_sql_into_a_running_world(self, manifest: Manifest, when: When) -> None:
        """Checklist 8.7a's guard: no direct SQL into a live world's databases.

        Owner answer 7 (`phase8-parity-decisions.md:44`) is the rule — *no
        direct writes to `characters`/`world` while running; reads are fine* —
        and until this function existed the applier had **no** running-world
        guard on this route at all. Only the `db-import` route was ever pressed
        live (`8.7a-wotlk-yulon-ubuntu-2026-09-08/11-cycle2-up.log`), and that
        route's guard is not here: it is `docker.apply_module_sql()`
        (`docker.py:2029-2036`), which refuses when `spec.world`/`spec.auth` are
        in `_running().ours`. **That guard could not be reused.** It needs a
        `ContainerSpec`, a compose project name and Docker itself, and this
        module's whole contract is that it never touches Docker (module
        docstring, style-guide §3/§5): the applier is called down into and
        signals up. So this is a second ENFORCEMENT POINT for one rule, not a
        second rule — the fact is asked through a seam so the caller can hand
        both routes the same answer, and the sentence deliberately echoes
        `docker.py`'s ("holds ... in memory and saves them back over whatever it
        finds. Press Stop") so a user meets one rule and not two.

        Four decisions, each of which could have gone the other way:

        * **A pre-pass over the action's steps, not a check inside the loop.**
          `all-stackables` sends three statements to `world` on install; a guard
          consulted per statement can let the first through and refuse the
          second, which is a half-applied mod and a worse bug than no guard.
          Nothing has run when this raises, so *no rows written* is true of the
          whole action and not merely of the step that tripped it.
        * **It raises rather than reporting `skipped`.** `phase8-designs/
          c-operators-risk.md` says both — `:90` "refused", `:345` "skipped with
          the step named" — and `checklist.md:2501`, which is the definition of
          done this box ticks on, says *refused ... with the step named and no
          rows written, and applies once the server is stopped*. A `skipped`
          line inside a press that otherwise succeeds is not a refusal: the
          module would end up marked installed with its SQL never applied, which
          is the very defect 8.7a's third clause is about. `ApplyError` is this
          engine's one refusal vocabulary and every caller of `install()`
          already handles it. It also has to be a raise for `remove()`, whose
          SQL is the module's *undo* and whose next statements delete the clone
          holding it — a skip there loses that file forever, and `remove()`'s
          own comment already puts its refusals before the SQL for this reason.
          What the message must NOT claim is that nothing happened: by the time
          `_sql()` runs, `install()` has cloned, deployed and patched. It says
          what is true — no SQL, no rows — and that a second press repeats the
          steps already taken.
        * **`auth` is never a reason to refuse, but an `auth` step alongside a
          refused one does not run either.** Half a manifest is not an outcome
          anyone asked for, and the `auth` write survives the refusal being
          lifted (a second press re-runs it).
        * **The seam absent means the behaviour every caller had before this
          landed, byte for byte** (`c-operators-risk.md:345`). It shipped that
          way: for one day no caller passed `world_running` at all, so
          `_world_running` was `None` on all four games and this function
          returned at its first line — a capability, not a defence. T2's press
          had to attach the seam itself, and recorded that the Modules tab as it
          then shipped would have written 7 219 rows into a live world without a
          word. Every factory the app builds an `Applier` through passes it now
          (T7), and `test_apply.py` enumerates them so the next one added is
          caught rather than discovered.

        Fails closed on anything short of a clear "no": `None` and a seam that
        raises are both refusals, because *could not ask* is not *not running* —
        the same three-valued discipline `docker._running()` uses for a project
        it cannot read.
        """
        if self._world_running is None:
            return  # no seam: the behaviour every existing caller has today
        at_risk = [
            step
            for step in manifest.sql
            if step.when == when and step.applied_by == "direct" and step.db in WORLD_HELD_DBS
        ]
        # No runner means this run writes nothing whatever and `_sql()` already
        # says so per step. Refusing here would be a refusal about a write that
        # was never going to happen, and it would replace that message.
        if not at_risk or self.sql is None:
            return
        why = ""
        try:
            running: bool | None = self._world_running()
        except Exception as exc:  # noqa: BLE001 - any seam failure is one answer here
            logger.warning(f"could not tell whether the world is running: {exc}")
            running, why = None, f"{type(exc).__name__}: {exc}"
        if running is False:
            return
        # Named as the manifest spells them, and unrendered for `_pending_sql`'s
        # reason: `_render()` raises for a value this run has not got, and a
        # refusal that dies while composing its own sentence names nothing.
        steps = ", ".join(f"sql {step.path or 'inline'} → {step.db}" for step in at_risk)
        dbs = ", ".join(sorted({step.db for step in at_risk}))
        if running is None:
            raise ApplyError(
                f"{manifest.id}: could not tell whether the world server is running "
                f"({why or 'the seam gave no answer'}), and a running one holds {dbs} in memory "
                f"and writes back over whatever it finds there. No SQL was run and no rows were "
                f"written: {steps}. Stop the server, then {when} again."
            )
        raise ApplyError(
            f"{manifest.id}: the world server is running, and it holds {dbs} in memory and writes "
            f"back over whatever it finds there. No SQL was run and no rows were written: "
            f"{steps}. Press Stop, then {when} again — the steps this run already took repeat, "
            f"and the SQL follows them."
        )

    def _start_the_database_for_direct_sql(self, manifest: Manifest, when: When, log: _Log) -> bool:
        """Make *"Press Stop, then install again"* a thing that can be done.

        The guard above tells a user to stop the server. The app's Stop is
        `docker.stop_staged()`, which takes the whole compose project down —
        database included — and every direct SQL step in this engine is a
        `docker exec` into that container. So the instruction ended in
        `Error response from daemon: container 3c922e8e... is not running`,
        measured through the app's own controls (T2, `4-press-world-stopped.log`).
        That is `bug-checklist §46` — *"there is no compliant way to install a
        SQL mod at all"* — whose title scopes it to CMaNGOS and whose mechanism
        is shared, so it was AzerothCore's too.

        Three decisions:

        * **The world is not started, ever.** Only the database, alone, which is
          the "world down, database up" state §46 says the app had no way to
          reach. Starting the world would put back the very thing the refusal
          above exists to keep away from these tables.
        * **Every `direct` step counts, not only the `WORLD_HELD_DBS` ones.**
          The guard's set is about what a running worldserver holds in memory;
          this is about whether there is a database process to talk to at all,
          and an `auth`-only mod fails the same way on a stopped stack. The
          `when` filter is kept, so a configure over a manifest whose SQL is all
          install-time reaches for nothing.
        * **A failure to start is a refusal, carrying the daemon's own
          sentence.** `docker.start_database()` names the container, the timeout
          and where the logs are; nothing here knows better, and a paraphrase
          would send the operator looking in the wrong place. Nothing has run
          when this raises, for the same reason the guard is a pre-pass.

        Returns whether the start seam was CONSULTED — not whether it started
        anything. `_sql()` re-reads the running-world guard on a true answer,
        because consulting it is what opens the window: the call can block for
        up to three minutes waiting on health (`_DB_HEALTHY_TIMEOUT_SECONDS`),
        and the world can come up inside it. `False` here means nothing was
        asked of Docker and no time passed,
        so the first reading is still the current one.
        """
        if self._start_database is None or self.sql is None:
            return False
        direct = [
            step for step in manifest.sql if step.when == when and step.applied_by == "direct"
        ]
        if not direct:
            return False
        try:
            started = self._start_database()
        except Exception as exc:  # noqa: BLE001 - any failure to start is one answer here
            steps = ", ".join(f"sql {step.path or 'inline'} → {step.db}" for step in direct)
            raise ApplyError(
                f"{manifest.id}: the database could not be started, so no SQL was run and no "
                f"rows were written: {steps}. {exc}"
            ) from exc
        if started:
            log.done.append("started the database alone; the world server was left stopped")
        return True

    def _pending_sql(self, step: SqlStep, clone: Path) -> PendingSql:
        """Resolve a deferred step's glob so the count reported is a real one.

        The path is globbed UNRENDERED on purpose. `_action_templates()` leaves
        db-import paths out of `required_prompts()`, so a `{key}` in one was
        never put to the user and `_render()` here would raise on a step this
        run is not performing — turning a report into a failure. A `{key}` gets
        `files=None` instead of a glob that would match nothing and call it
        zero; see `PendingSql`.

        A step with no `path` cannot happen — `SqlStep._exactly_one_body`
        requires exactly one of `path`/`statement`, and an inline `statement`
        is never `applied_by="db-import"` (there is no file for the importer to
        find) — but the assert says so rather than the type checker alone.
        """
        assert step.path is not None
        if _fields(step.path):
            return PendingSql(db=step.db, path=step.path, files=None)
        matches = sorted(clone.glob(step.path)) if _is_glob(step.path) else [clone / step.path]
        # `as_posix()`, not `_rel()`: these names are compared against the
        # manifest's own `path` (forward slashes, always) and read by a person
        # who may be on Windows, and `_rel()` would answer
        # `data\sql\db-world\a.sql` there and `data/sql/db-world/a.sql` on the
        # Linux box the same install was measured on. One spelling, both.
        found = tuple(p.relative_to(clone).as_posix() for p in matches if p.is_file())
        return PendingSql(db=step.db, path=step.path, files=found)

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
                log.done.append(
                    f"activate {conf.file} from {conf.template} — the world reads "
                    f"{conf.file} at its next start"
                )
                log.conf_restart = True
            writes = [(k.key, k.default) for k in conf.keys if k.default is not None]
            # A key the catalog names with no `default` is a step nobody takes.
            # Measured on yulon-ubuntu 2026-09-08 (8.7a, defect 1): the four
            # `mod_npc_beastmaster.conf` keys and `Creatures.CustomIDs` on the
            # core's own `worldserver.conf` were dropped here in silence — absent
            # from `done`, absent from `skipped`, with the file byte-identical
            # afterwards. Reported rather than filled in: which value belongs in
            # a user's core configuration is the catalog's sentence to write, and
            # `Creatures.CustomIDs` in particular is an APPEND to a list this
            # applier has no syntax for.
            valueless = [k.key for k in conf.keys if k.default is None]
            if valueless:
                log.skipped.append(
                    f"conf {conf.file}: no value in the catalog for "
                    f"{', '.join(valueless)} — not written"
                )
            if not writes:
                continue
            if not target.is_file():
                log.skipped.append(f"conf {conf.file}: file missing, keys not written")
                continue
            changed = False
            for key, default in writes:
                mode = _set_conf_key(target, key, _render(default, vals, f"conf {key}"))
                changed = changed or mode != "unchanged"
            if not changed:
                continue  # every key already read this value: nothing to restart for
            log.done.append(
                f"set {len(writes)} key(s) in {conf.file} — the world reads "
                f"{conf.file} at its next start"
            )
            log.conf_restart = True

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
            # Declared first, then derived. Three of the four derived clauses
            # read the manifest itself — NPCs, direct SQL and server DBCs, all
            # of which reach the database or the data volume. The fourth reads
            # what THIS RUN actually did: `log.conf_restart`, set by `_conf()`
            # the moment it writes a byte to a file the running world reads
            # only at startup (T27; measured live, `pyplan/gates/8.6-spec-
            # takes-effect-yulon-ubuntu2-2026-09-10/03-activate.log:38` —
            # activating `mod-playerbots`' conf reported `restart_recommended
            # = False` while the world went on running the OLD config until
            # the next restart). A conf step that found nothing to write —
            # the file already there, no keyed value in the catalog — leaves
            # `conf_restart` False, same as a manifest with no `conf` at all.
            # `build.restart` is the one clause that is a DECLARATION rather
            # than an observation; every clause here can only ADD a yes, never
            # take one away.
            restart_recommended=bool(
                manifest.build.restart
                or manifest.npcs
                or any(s.applied_by == "direct" for s in manifest.sql)
                or manifest.server_dbc
                or log.conf_restart
            ),
            pending_sql=tuple(log.pending_sql),
        )
        logger.info(
            f"{action} {manifest.id}: {len(report.done)} step(s), "
            f"{len(report.skipped)} skipped, {len(report.pending_sql)} left unapplied, "
            f"rebuild={report.rebuild_required}"
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


_KeyMode = Literal["replace", "append", "unchanged"]


def _set_conf_key(path: Path, key: str, value: str) -> _KeyMode:
    """Set `key = value` in a worldserver-style conf: replace the line, append it, or —

    T27 round 2 (Codex adversarial review): a re-apply of a keyed conf that already
    reads `key = value` byte-for-byte used to hit the replace branch every time and
    write the file anyway, so `_conf()` could not tell a real change from a no-op —
    it reported `restart_recommended = True` over a conf it had not touched. `new ==
    text` is that no-op, caught before the write rather than after: nothing on disk
    changes, and the caller sees `"unchanged"` rather than `"replace"`.
    """
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*=.*$", re.MULTILINE)
    new, count = pattern.subn(f"{key} = {value}", text, count=1)
    if count:
        if new == text:
            return "unchanged"
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


@dataclass(frozen=True)
class ModuleUpdate:
    """One installed module and how far behind its upstream it is (checklist 8.7a)."""

    key: str
    """The folder name under `modules/` — which is the module's id, and the same
    string `docker.allowed_modules()` hands the importer."""

    path: Path
    is_checkout: bool
    """Whether this folder is a git checkout at all. A `modules/` directory also
    holds `CMakeLists.txt`, `ModulesLoader.cpp.in.cmake` and friends beside the
    modules (read off yulon-ubuntu, 2026-09-07), and a user can copy a module in
    by hand with no `.git` in it. Both are still listed — a folder the importer
    will be handed is worth showing — but neither can be asked."""

    behind: int | None
    """Commits the upstream has that this checkout does not. `None` is "could not
    ask": no `.git`, an offline machine, a repository that has gone private.
    Never collapsed into `0` — see `git.BehindReader`."""

    @property
    def line(self) -> str:
        """The row as the Modules tab prints it.

        Here rather than in the view because it is the sentence the figure is
        READ in, and the one thing 8.7a's definition of done is about is that
        this number equals the same range run by hand. A view that formatted it
        itself could round, pluralise or default it without a test noticing.
        """
        if not self.is_checkout:
            return f"{self.key}: not a git checkout — nothing to compare"
        if self.behind is None:
            return f"{self.key}: could not ask (no answer from git)"
        plural = "" if self.behind == 1 else "s"
        return f"{self.key}: {self.behind} commit{plural} behind"


def module_updates(
    server_dir: Path,
    *,
    git: BehindReader,
    branches: Mapping[str, str | None] | None = None,
    kind: ManifestType = "module",
) -> tuple[ModuleUpdate, ...]:
    """How far behind each installed module of `server_dir` is (checklist 8.7a).

    Enumerated from DISK, not from the manifest store, and that is the whole
    point: "installed" means a clone is in `modules/`, so a module a user put
    there by hand is listed and a manifest nobody installed is not. It is the
    same enumeration `docker.allowed_modules()` hands the importer, for the same
    reason — the folder is what the server has, and the catalog is only what it
    could have had.

    `branches` maps a module key to the branch its manifest names, because the
    manifest's branch is what an update would fetch. Every module in the
    `wow-wotlk` catalog omits `source.branch`, so the ordinary answer is `None`
    and the ordinary fetch is `origin HEAD` — which is exactly why a wrong
    branch here would go unnoticed until the first module that names one.

    Costs one `git fetch` per checkout, so it belongs behind a control the user
    pressed. Nothing outside each clone's `.git` is written and no working tree
    is touched.
    """
    root = server_dir / CLONE_DIRS[kind]
    branch_of = branches or {}
    try:
        entries = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name)
    except OSError as exc:
        # Not an error and not empty-with-a-shrug: the three CMaNGOS games have
        # no `modules/` at all, and a server dir that cannot be listed is a
        # different problem than one with nothing installed. Logged, either way.
        logger.debug(f"no {CLONE_DIRS[kind]} folder to list under {server_dir}: {exc}")
        return ()
    rows: list[ModuleUpdate] = []
    for path in entries:
        checkout = (path / ".git").is_dir()
        behind = git.commits_behind(path, branch_of.get(path.name)) if checkout else None
        rows.append(ModuleUpdate(key=path.name, path=path, is_checkout=checkout, behind=behind))
    return tuple(rows)
