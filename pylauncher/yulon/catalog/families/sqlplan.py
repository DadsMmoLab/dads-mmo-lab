"""The SQL plan kind: an ordered, typed replacement for the installers' `mysql < file` loops.

One job, family-neutral: take a `SqlPlan` from `catalog.json` and turn it into work
against the install's database container. This module's first piece is the one that
decides everything the later ones only carry out:

* `expand()` — pure — turns globs into an ordered list of `PhaseRun`s and fills the
  `{{TOKEN}}`s in literal statements (files are streamed as they are). **Order is the
  product.** A world database applied out of order does not fail: every statement is
  valid, the import "succeeds", the server starts, and a column added by update 10 is
  missing because update 9 ran after it. The natural sort therefore reproduces GNU
  `ls -v` (`filevercmp`), which is what the shell installers used and what cmangos'
  `z2817_01_mangos_x.sql` naming assumes; a plain `sorted()` runs `z10` before `z9`.

* `apply()` — carries it out: each run streamed on the stdin of `docker exec -i`, gzip
  inflated on the way, with the phase's `fail`/`warn` policy over what the client says
  back. Three failures stay three failures there — the SQL was rejected, the database
  could not be asked, the dump could not be read — for the same reason `expand()` keeps
  three answers below.

Later tasks add `create_schemas()` (the implicit phase 0: databases, the app user,
grants) and `verify()` + `write_marker()` (the completion record, written only after
`verify()` returns no failing rule). The two Protocols and `MARKER_TABLE` below are their
shared vocabulary, declared here so the transport shape has one spelling — including
which daemon holds the container, which a Protocol that dropped `wsl_distro` would make
unsayable for every one of them at once.

**A glob has three answers, not two.** "How many files matched?" hides a second question
— "were you able to look?" — and `Path.glob`/`Path.rglob` answer both with one short
list: they swallow every `OSError` and return what they managed to read. On a `warn`
phase that reads as "nothing matched, skipping", so a directory this process may not
list becomes a database quietly missing a third of its content, with no error line
anywhere. So:

1. **listed, and nothing matched it** — the phase's own `on_error` decides. A `fail`
   phase is a broken plan and is refused before anything is written; a `warn` phase
   names the pattern in the log and is skipped. This is the only answer a policy may
   soften, because it is the only one that is about the sources rather than the machine.
2. **could not look** — the directory could not be listed, or something matched and
   could not be examined. Refused whatever the policy says, naming the path and what the
   OS actually said. `warn` was never a licence to ignore the operating system.
3. **matched, and cannot be read** — not this function's question. `expand()` is pure
   listing and opens nothing: a readability probe here would double the I/O over
   thousands of dump files and still be stale by the time the stream starts. `apply()`
   opens each file at the moment it streams it and names the one that failed by `rel`.

The password travels in the exec environment (`MYSQL_PWD`), never in argv; the one place
it must appear in SQL text — `CREATE USER ... IDENTIFIED BY` — goes over stdin and is
never logged.
"""

from __future__ import annotations

import fnmatch
import gzip
import io
import re
import stat as stat_module
import subprocess
import threading
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import BinaryIO, Protocol, cast

from yulon import docker
from yulon.catalog.catalog import SqlPhase, SqlPlan
from yulon.catalog.composegen import ComposeGenError, fill
from yulon.catalog.installer import InstallerError
from yulon.catalog.native import IMPORT_CANCEL_NOTE
from yulon.log import get_logger

logger = get_logger(__name__)

MARKER_TABLE = "yulon_install"
"""The table `write_marker()` creates in `plan.marker_db`; its presence is the import record."""

_DIGITS = re.compile(r"([0-9]+)")
"""`c_isdigit`: ASCII only, like `_is_alpha`/`_is_alnum` below.

Python's `\\d` also matches `٣` and `९`, which `_prefix()`'s `_is_alnum` would then treat as
a non-digit — one function splitting a run the other refuses to. C asks `c_isdigit` in both
places and sees a UTF-8 name as bytes that are neither letters nor digits, so `[0-9]` is
both the consistent answer and the faithful one.
"""

_GLOB_META = frozenset("*?[")
"""What makes a path component a pattern rather than a name."""

_CATALOG_ERROR = "That is a catalog error, not something to fix on this machine."


class ExecStdin(Protocol):
    """`docker.exec_stdin`'s shape: `docker exec -i -e <env keys> <container> <argv>` fed `source`.

    **`wsl_distro` is part of the shape, not an implementation detail the seam may drop.**
    A container name means nothing to a daemon that does not hold it: a server living
    inside a WSL distro is `No such container` to Docker Desktop, and a variable set on
    this side does not even reach a process in a distro unless `WSLENV` names it (it
    arrives EMPTY, and the client reports an authentication failure against a perfectly
    healthy database). `docker.exec_stdin()` carries both halves, and the two existing
    `docker exec -i -e MYSQL_PWD` call sites in this app — `apply.DockerSql._argv()` and
    `maintenance.DockerMysql._exec()` — both thread a distro for the same reason.

    A Protocol that named only the four arguments the local case uses is where that gets
    undone, silently: `apply()` cannot pass an argument its own seam type does not
    declare, so an import into an existing WSL-resident install would go to the wrong
    daemon with nothing in the code to show a choice had been made. Defaulted, so a
    caller with no distro says nothing and a fake need not care; declared, so a caller
    with one can be obeyed.
    """

    def __call__(
        self,
        container: str,
        argv: Sequence[str],
        source: BinaryIO,
        *,
        env: Mapping[str, str],
        wsl_distro: str | None = None,
    ) -> subprocess.CompletedProcess[str]: ...


class SqlQuery(Protocol):
    """`docker.sql_query`'s shape: one batch, skip-column-names statement; rows back as text.

    Carries `wsl_distro` for the reason above, and one more of its own: this one is a
    PROBE, and its answer decides whether an install runs at all. Asked of the wrong
    daemon it reads as "nothing is imported" for a database that is fully populated, and
    the import stage would run again over a working server.

    Its result is stdout VERBATIM, trailing newline included, and a caller that reflexively
    `.strip()`s it destroys something it cannot get back: under `--skip-column-names` one
    row holding the empty string prints `"\\n"` while no rows print `""`, so stripping
    collapses "one empty row" into "no rows". Count with `splitlines()`.
    """

    def __call__(
        self,
        container: str,
        client: str,
        password: str,
        schema: str | None,
        statement: str,
        *,
        wsl_distro: str | None = None,
    ) -> str: ...


@dataclass(frozen=True)
class PhaseRun:
    """One unit of `apply()`: a file or a literal statement, into one schema (or none).

    `rel` is what the install log calls this run: the file's path relative to the server
    dir in posix form (`src/tbc-db/Updates/z1.sql`), or `statement N` for a literal —
    never an absolute path (which would put the user's home directory in a pasted log),
    and never the SQL text (which is how a password ends up in one).
    """

    phase: SqlPhase
    schema: str | None
    path: Path | None
    statement: str | None
    gzip: bool
    rel: str


def natural_key(name: str) -> tuple[object, ...]:
    """A sort key that orders names as GNU `ls -v` does (coreutils `filevercmp`).

    Five elements, in `filevercmp`'s own order of decisions:

    1. **Where the dots are.** `.` first, then `..`, then other dotfiles, then everything
       else. `Path.glob('*.sql')` returns `.a.sql` where the `glob` module would not, so
       the key needs an opinion; one leading dot is then dropped, as C does, before the
       rest is looked at.
    2. **The name with its file suffix cut** (`_prefix()` — the first dot that begins an
       unbroken run of suffixes to the end), compared by the Debian version rule: digit
       runs compare numerically with leading zeros ignored; between digits, characters
       compare with `~` lowest, then end-of-name, then digits, then letters, then
       everything else. Digits and end-of-name both score 0, which is what makes this
       alternating (character-run, integer) shape equivalent to C's character walk: the
       `0` closing every run is the sentinel C compares a digit against.
    3. **That cut name as raw bytes.** `001` and `1` are the same version, and C then
       falls through to `strcmp` of the whole names; whatever it decides there, it
       decides on the first byte where the two cut names differ, which is this element.
    4. **The whole name by the same version rule** — C's "restore the suffixes if the cut
       names were identical", which is what makes `x.sql` < `x.y.sql` and puts
       `TBCDB_1.9.0.sql.gz` before `TBCDB_1.10.0.sql.gz`.
    5. **The raw name**, so two names `ls -v` cannot tell apart (`z01.sql`, `z1.sql`)
       still sort deterministically — and in C's own order, which is `strcmp`'s.

    One corner is not reproducible by ANY key, because there C's answer depends on the
    other string rather than on either one alone: a digit run of only zeros is SKIPPED
    when the other name has ended there, so `bb0~` sorts below `bb` (`~` beats
    end-of-name once the `0` is gone) while the same `0` counts as a character worth 0
    against a letter (`a0b` < `ab`). A key would have to drop the run and keep it at
    once. It needs a `~` in a file name to bite; `~` is a Debian version convention and
    no SQL release in any of the shipped plans uses one.

    ASCII names only, which is what every SQL release file in every plan is. C compares
    BYTES and asks `c_isalpha`, so a UTF-8 `é` is two non-letters to it and one
    non-ASCII code point to Python; the two disagree only when such a name is compared
    against punctuation, and this key is not the place to guess which answer a given
    locale's `ls` would give. `_is_alpha`/`_is_alnum` keep C's ASCII-only test so the
    ordering of ASCII names is exact, and a non-ASCII name sorts deterministically
    rather than correctly.

    `test_sqlplan.py`'s captured `LS_V_*` lists are the definition. Beyond them two
    different things have been checked, and they are not the same claim:

    **That `sorted(names, key=...)` prints what `ls -v` prints.** Checked over 4,800
    generated names built from digits, letters, `_ - . ~ +`, doubled dots, leading dots
    and `.sql`/`.sql.gz`/`.y.sql` tails. This is the property the installers actually
    depend on - the shell scripts consumed `ls -v`'s output, qsort and all - and a
    whole-corpus comparison is the right test for it.

    **That this key implements `filevercmp`'s COMPARISON.** A corpus sort cannot show
    that, and the reason is worth keeping: `filevercmp` is NOT TRANSITIVE, so qsort's
    output does not imply `cmp(a, b) <= 0` for adjacent pairs. Comparing a sorted
    8,086-name corpus against `ls -v` reported 6,323 mismatches and 1,409 inverted
    adjacent pairs - every one an artefact of that. The sound oracle is one pair per
    directory: re-probed that way, all 1,409 disputed pairs plus 3,000 random agreed,
    4,409 of 4,409, none unresolved.

    **The `~` corner, bounded rather than shrugged at.** 1,326 pairs built to hit it
    gave 12 disagreements, all involving `~` and none without, every one of the shape
    "a zero-only digit run then `~` against a name that ends there" - `a` vs `a0~`,
    `bb` vs `bb0~`. Concretely, `ls -v` puts `x0~.sql` before `x.sql`; this key does
    not.
    """
    rank = 0 if name == "." else 1 if name == ".." else 2 if name.startswith(".") else 3
    body = name[1:] if rank == 2 else name
    prefix = _prefix(body)
    return (rank, _verkey(prefix), prefix, _verkey(body), name)


def _prefix(name: str) -> str:
    """The name without its file suffix, by coreutils' `match_suffix()` scan.

    The documented rule is the regex `(\\.[A-Za-z~][A-Za-z0-9~]*)*$`, but C does not
    match that regex — it makes ONE left-to-right pass and remembers the first `.` that
    could still start the tail. A `.` that arrives while the previous `.` is still
    waiting for its letter (`aa..sql.gz`) clears the candidate and is itself consumed, so
    the tail starts at the NEXT dot (`.gz`) and not at `.sql.gz`, which is where the
    regex would put it. Names with a doubled dot are the only ones that tell the two
    apart, and `ls -v` follows the code; so does this.

    The result may be empty, as in C: `..sql` cuts to ``.
    """
    match: int | None = None
    read_alpha = False
    for index, char in enumerate(name):
        if read_alpha:
            read_alpha = False
            if not _is_alpha(char) and char != "~":
                match = None
        elif char == ".":
            read_alpha = True
            if match is None:
                match = index
        elif not _is_alnum(char) and char != "~":
            match = None
    return name if match is None else name[:match]


def _is_alpha(char: str) -> bool:
    """`c_isalpha`: ASCII only, whatever the locale or the code point."""
    return char.isascii() and char.isalpha()


def _is_alnum(char: str) -> bool:
    """`c_isalnum`: ASCII only."""
    return char.isascii() and char.isalnum()


def _verkey(text: str) -> tuple[object, ...]:
    """Alternating (character-run tuple, int) pairs; every run ends with the end-of-name 0.

    The alternation always starts with a character run (`re.split` on a capturing group
    yields text first, empty or not), so two keys built here always compare tuple against
    tuple and int against int — never `int < tuple`, which would raise.
    """
    key: list[object] = []
    for index, part in enumerate(_DIGITS.split(text)):
        if index % 2 == 0:
            key.append(tuple(_order(char) for char in part) + (0,))
        else:
            key.append(int(part))
    return tuple(key)


def _order(char: str) -> int:
    """`filevercmp`'s `order()`: `~` below end-of-name (0), letters as
    themselves, everything else above every letter."""
    if char == "~":
        return -1
    if _is_alpha(char):
        return ord(char)
    return ord(char) + 256


def expand(
    plan: SqlPlan, server_dir: Path, schemas: Mapping[str, str], tokens: Mapping[str, str]
) -> tuple[PhaseRun, ...]:
    """Every file and statement the plan applies, in the order it applies them. Pure.

    Phases run in the order `catalog.json` lists them; within a phase, globs are relative
    to `server_dir` and expanded per pattern, each sorted on its own (`natural` ==
    `ls -v`, `name` == plain), so a phase listing two directories runs the first
    directory's files before the second's — the order the scripts' two `ls -v` loops
    gave. `into_each` runs its schemas in declaration order. Literal statements have
    their `{{TOKEN}}`s filled here, through the one `composegen.fill` (A6); files never
    are, because a dump that happens to contain `{{` is a dump and not a template.

    Every schema name the plan mentions — `create`, `marker_db`, each `verify.db` and
    `player_data.db` as well as the phases' `into`/`into_each` — is checked against
    `schemas` first, before a single directory is listed. Those four are read later by
    the marker and the verifier, which have no map in reach; a typo there would otherwise
    surface stages after the import, against a database nobody created.

    A pattern names exactly one directory and one filename pattern (`a/b/*.sql`). A
    wildcard higher up would require a walk, and a walk is where "nothing matched" and
    "could not look" merge back into one answer — see the module docstring. No shipped
    plan needs one, so it is refused rather than half-supported.

    Raises:
        InstallerError: the plan or a phase names a schema outside `schemas`; a pattern
            escapes the server dir, is rooted, or wildcards a directory; a `fail` phase's
            pattern matched no file; a directory could not be listed or a matching entry
            could not be examined (whatever the phase's `on_error` says); or a statement
            carries a token `tokens` does not have.
    """
    _check_plan_schemas(plan, schemas)
    runs: list[PhaseRun] = []
    for phase in plan.phases:
        targets = _targets(phase, schemas)
        for number, statement in enumerate(phase.statements, start=1):
            # `into_each` and `statements` are alternatives (the model refuses both), so
            # whenever there is a statement at all there is exactly one target.
            filled = _fill_statement(statement, phase, tokens)
            runs.append(PhaseRun(phase, targets[0][0], None, filled, False, f"statement {number}"))
        for schema, patterns in targets:
            for pattern in patterns:
                for path in _matches(server_dir, pattern, phase):
                    rel = path.relative_to(server_dir).as_posix()
                    runs.append(PhaseRun(phase, schema, path, None, phase.gzip, rel))
    return tuple(runs)


def _check_plan_schemas(plan: SqlPlan, schemas: Mapping[str, str]) -> None:
    """Refuse a plan naming a database this game does not have, wherever it names it."""
    named: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("create", plan.create),
        ("marker_db", (plan.marker_db,)),
        ("verify", tuple(rule.db for rule in plan.verify)),
        ("player_data", tuple(table.db for table in plan.player_data)),
    )
    for field, values in named:
        for value in values:
            if value not in schemas:
                raise InstallerError(
                    f"the SQL plan's `{field}` names {value!r}, which is not one of this "
                    f"game's databases ({', '.join(schemas)}). {_CATALOG_ERROR}"
                )


def _fill_statement(statement: str, phase: SqlPhase, tokens: Mapping[str, str]) -> str:
    try:
        return fill(statement, tokens)
    except ComposeGenError as exc:
        raise InstallerError(
            f"the SQL phase '{phase.name}' has a statement that could not be filled in: "
            f"{exc}. {_CATALOG_ERROR}"
        ) from exc


def _targets(
    phase: SqlPhase, schemas: Mapping[str, str]
) -> list[tuple[str | None, tuple[str, ...]]]:
    """(server schema, glob patterns) pairs for one phase; one pair unless `into_each`."""
    if phase.into_each:
        return [
            (_schema(name, phase, schemas), (pattern,)) for name, pattern in phase.into_each.items()
        ]
    schema = _schema(phase.into, phase, schemas) if phase.into else None
    return [(schema, phase.files)]


def _schema(name: str, phase: SqlPhase, schemas: Mapping[str, str]) -> str:
    try:
        return schemas[name]
    except KeyError:
        raise InstallerError(
            f"the SQL phase '{phase.name}' writes into {name!r}, which is not one of this "
            f"game's databases ({', '.join(schemas)}). {_CATALOG_ERROR}"
        ) from None


def _split(pattern: str, phase: SqlPhase) -> tuple[tuple[str, ...], str]:
    """(directory components, filename pattern), or a refusal.

    The pattern is judged as the posix string `catalog.json` holds, on every platform.
    `Path('/etc/x').is_absolute()` is **False** on Windows — a rooted posix path has no
    drive — and `Path('C:/srv') / '/etc/x'` is `C:/etc/x`, so the naive check passes a
    pattern through on exactly the platform where it then escapes the server folder.
    """
    refusal = (
        f"the SQL phase '{phase.name}' globs {pattern}, which is not a plain path inside "
        f"the server folder. {_CATALOG_ERROR}"
    )
    windows = PureWindowsPath(pattern)
    if "\\" in pattern or windows.drive or windows.is_absolute():
        raise InstallerError(refusal)
    posix = PurePosixPath(pattern)
    if posix.is_absolute() or ".." in posix.parts or not posix.parts:
        raise InstallerError(refusal)
    directory, name = posix.parts[:-1], posix.parts[-1]
    if any(_GLOB_META & set(part) for part in directory):
        raise InstallerError(
            f"the SQL phase '{phase.name}' globs {pattern}, which wildcards a folder name. "
            f"A phase names one folder and one file pattern. {_CATALOG_ERROR}"
        )
    return directory, name


def _listing(directory: Path, pattern: str, phase: SqlPhase) -> list[Path] | None:
    """Everything in `directory`, or `None` when there is simply no such directory.

    `None` is answer one (nothing to match); every other `OSError` is answer two and
    stops the install regardless of `on_error` — see the module docstring. That includes
    `NotADirectoryError`, which is what a half-unpacked source tree looks like: a
    regular file standing where `Updates/` belongs is not "the sources shipped no
    updates", it is a broken checkout, and a `warn` phase must not shrug at it.
    """
    try:
        return sorted(directory.iterdir())
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise InstallerError(
            f"the SQL phase '{phase.name}' globs {pattern}, but {directory} could not be "
            f"read ({exc}). That is not the same as finding no files there, so nothing "
            "was applied. Make that folder readable and install again."
        ) from exc


def _matches(server_dir: Path, pattern: str, phase: SqlPhase) -> list[Path]:
    """The files one pattern selects, in this phase's order."""
    directory, name = _split(pattern, phase)
    entries = _listing(server_dir.joinpath(*directory), pattern, phase)
    found: list[Path] = []
    for entry in entries or ():
        if not fnmatch.fnmatchcase(entry.name, name):
            continue
        try:
            info = entry.stat()
        except OSError as exc:
            raise InstallerError(
                f"the SQL phase '{phase.name}' matched {entry} on {pattern}, but could not "
                f"examine it ({exc}). Dropping it silently would apply the rest of this "
                "phase and leave the database short, so nothing was applied."
            ) from exc
        if not stat_module.S_ISREG(info.st_mode):
            logger.warning(f"SQL phase '{phase.name}': {entry} matches {pattern} but is not a file")
            continue
        found.append(entry)
    if phase.sort == "natural":
        found.sort(key=lambda path: natural_key(path.name))
    else:
        found.sort(key=lambda path: path.name)
    if not found:
        if phase.on_error == "fail":
            raise InstallerError(
                f"the SQL phase '{phase.name}' found no file matching {pattern} under "
                f"{server_dir}. The sources may not have cloned completely."
            )
        logger.warning(f"SQL phase '{phase.name}': nothing matched {pattern}, skipping it")
    return found


def apply(
    runs: Sequence[PhaseRun],
    *,
    container: str,
    client: str,
    password: str,
    exec_stdin: ExecStdin,
    sink: docker.OutputSink,
    cancel: threading.Event | None,
    wsl_distro: str | None = None,
) -> Iterator[str]:
    """Run every `PhaseRun` in order, streaming each file on the client's stdin.

    Yields one line per run (for the install log, naming the run by its server-dir-relative
    `rel`) BEFORE the work it describes, so the longest step of the install is not a still
    screen, and pushes the client's stderr into `sink` line by line whatever the exit code
    was — a warning the shell installers threw away with `2>/dev/null` is visible without
    stopping anything. Policy comes from the run's phase: `fail` raises naming the file and
    the client's last stderr line — the one that says which line of which statement — and
    `warn` yields a warning naming the file and moves on.

    `cancel` is checked before each run and never mid-file: a half-applied file is exactly
    the `partial` state `MarkerGate.reset()` exists to clear, and the cancel note says so.

    **Three ways a run can go wrong, and they are three different sentences.** `expand()`
    already keeps "nothing matched" apart from "could not look"; the same distinction has
    to survive the moment the bytes move, because the answer to each is different:

    1. **The client rejected the SQL** — a non-zero exit with the client's own words. The
       only one `on_error: warn` may soften, because it is the only one that is about the
       sources rather than about this machine or the daemon.
    2. **The database could not be asked** — no docker CLI, no such container, a container
       that is not running. `DockerCliMissingError` is a `DockerCommandError` is a
       `RuntimeError`, so the clause that catches a bad dump would swallow this one too
       unless it is answered first — and the user would be told their download is corrupt
       when what is missing is Docker.
    3. **The dump could not be read** — `docker.SourceUnreadableError`, whose whole reason
       for existing is this clause. `exec_stdin()` refuses to enumerate what a `BinaryIO`
       may raise (a truncated `.sql.gz` raises `EOFError`, mangled deflate bytes
       `zlib.error`, a file that was never gzip `gzip.BadGzipFile` — and only the last is
       an `OSError`), so it normalises all of them to one `RuntimeError` subclass and keeps
       the original as `__cause__`. `(RuntimeError, OSError)` is therefore enough, and the
       cause is reported because WHICH corruption it was is the actionable half: "download
       it again" answers a truncated file and not an unreadable one. This is never softened
       by `warn`. It is the failure that used to pass: half a dump is valid SQL, so the
       client exits 0 and every check afterwards agrees the import worked.

    `wsl_distro` names the daemon holding `container`; see `ExecStdin`. It is forwarded on
    every call rather than only when set, so there is no untested road on which the choice
    quietly stops being made.
    """
    env = {"MYSQL_PWD": password}
    for run in runs:
        _check_cancel(cancel)
        yield _describe(run)
        argv = _client_argv(client, run.schema)
        try:
            with _open(run) as source:
                proc = exec_stdin(container, argv, source, env=env, wsl_distro=wsl_distro)
        except docker.DockerCommandError as exc:
            raise InstallerError(
                f"The import stopped: {run.rel} could not be sent to the database "
                f"({exc}). Nothing after it was applied."
            ) from exc
        except (RuntimeError, OSError) as exc:
            raise InstallerError(
                f"The import stopped: {run.rel} could not be read ({_read_failure(exc)}). "
                "The download may be incomplete; nothing after it was applied."
            ) from exc
        stderr = (proc.stderr or "").splitlines()
        for line in stderr:
            sink(line)
        if proc.returncode == 0:
            continue
        reason = _last_line(stderr) or f"the client exited {proc.returncode}"
        if run.phase.on_error == "fail":
            raise InstallerError(
                f"The import stopped: {run.rel} failed while loading into "
                f"{run.schema or 'the server'} ({reason}). Nothing after it was applied."
            )
        logger.warning(f"SQL phase '{run.phase.name}': {run.rel} failed: {reason}")
        yield (
            f"warning: {run.rel} failed ({reason}); continuing because '{run.phase.name}' "
            "is on_error: warn"
        )


def _read_failure(exc: BaseException) -> str:
    """What the read actually raised, class and all.

    `docker.SourceUnreadableError` normalises the TYPE so `apply()` has one clause to
    catch, and its message carries the original's words but not its class — and the class
    is the distinction that matters here. `EOFError` is a download that stopped;
    `zlib.error` and `gzip.BadGzipFile` are bytes that arrived wrong; `PermissionError` is
    a file this process may not open. Only the first two are answered by fetching it
    again, so the report says which one happened rather than only that something did.
    """
    cause = exc.__cause__ or exc
    return f"{type(cause).__name__}: {cause}"


def _open(run: PhaseRun) -> BinaryIO:
    """The bytes the client reads: the file (inflated when gzip) or the statement.

    Opened here, at the moment it is streamed, and closed by the caller's `with` before
    the next run. `expand()` deliberately probes nothing (see the module docstring), so
    this is where a missing or unreadable file is first found out about — and a handle
    held past its run is a file Windows will not let the later stages move or delete.
    """
    if run.path is None:
        return io.BytesIO((run.statement or "").encode("utf-8"))
    if run.gzip:
        # `GzipFile` is a `BufferedIOBase`, which typeshed does not spell as
        # `BinaryIO` although it reads exactly like one; the cast says so once.
        return cast(BinaryIO, gzip.open(run.path, "rb"))
    return cast(BinaryIO, run.path.open("rb"))


def _client_argv(client: str, schema: str | None) -> list[str]:
    """`mariadb -u root <schema>` — no `-p`, the password is in the exec environment."""
    argv = [client, "-u", "root"]
    if schema is not None:
        argv.append(schema)
    return argv


def _describe(run: PhaseRun) -> str:
    """`<phase>: <rel> -> <schema>`; a schema-less run says `(no schema)` instead.

    By `rel`, never by the SQL: `CREATE USER ... IDENTIFIED BY` is a statement, and the
    install log is something users paste into bug reports.
    """
    if run.schema is None:
        return f"{run.phase.name}: {run.rel} (no schema)"
    return f"{run.phase.name}: {run.rel} -> {run.schema}"


def _last_line(lines: Sequence[str]) -> str:
    """The last thing the client actually SAID, which is not `lines[-1]`.

    Clients end their stderr with a newline, so `splitlines()` leaves blanks behind the
    line that names which statement broke; quoting one of those would collapse the reason
    into the exit-code fallback and hide the only useful sentence in the failure.
    """
    for line in reversed(lines):
        if line.strip():
            return line.strip()
    return ""


def _check_cancel(cancel: threading.Event | None) -> None:
    """Stop between runs, with the one wording every cancel in the app uses (A10)."""
    if cancel is not None and cancel.is_set():
        raise InstallerError(f"The import was stopped. {IMPORT_CANCEL_NOTE}")
