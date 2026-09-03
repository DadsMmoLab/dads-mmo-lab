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

* `create_schemas()` — the implicit phase 0: the databases, the app user, its grants.

* `verify()` + `write_marker()` — the completion record, written only after `verify()`
  returns no failing rule, because an import whose `warn` phases all failed would
  otherwise read `imported` forever.

* `MarkerGate` — the question asked BEFORE all of the above: is this database already
  imported, half-written, or somebody's? Five answers, and they are not symmetric.
  `partial` is the only one that leads anywhere destructive (`reset()` drops the plan's
  schemas), so every question the gate cannot answer lands on `unreadable` instead — a
  count with no rows, two rows or a non-number is never read as zero. See its docstring
  for the ordering and what each branch costs when it is wrong.

The two Protocols and `MARKER_TABLE` below are their shared vocabulary, declared here so
the transport shape has one spelling — including which daemon holds the container, which
a Protocol that dropped `wsl_distro` would make unsayable for every one of them at once.
Every function here that talks to a database takes it and forwards it, unconditionally:
phase 0, the probe and the marker have to land on the daemon `apply()` streams into, and
because the Protocol DEFAULTS the argument, a function that quietly stopped passing it
would still type-check.

**A query has three answers, not two, for the same reason a glob does.** `sql_query()`
returns stdout verbatim: `""` is no rows, `"\\n"` is one row holding the empty string.
"Yes", "no" and "could not ask" are three verdicts, and `verify()` keeps them apart —
a rule it could not answer is a FAILING rule with its own sentence, never a count of
zero (which a `min: 0` rule would then pass, letting the marker be written over an
empty database) and never an exception (the caller has one code path).

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

The password travels in the exec environment (`MYSQL_PWD`), never in argv. It must appear
in SQL text once, in `CREATE USER ... IDENTIFIED BY`, and that statement is written in TWO
places rather than one: `create_schemas()` builds it for a plan with a `create` list, and a
catalog phase writes it as a literal statement whose `{{DB_PASSWORD}}` `expand()` fills —
which is what the shipped Tortoise plan does, its `sql.create` being empty. Both go over
stdin, and neither is logged: the run is named in the install log by `rel` (`statement 1`),
never by its SQL.

**What the CLIENT says about that statement is the other half, and it was the leak.** A
client quotes back the line it could not parse, password and all, and that sentence became
an `InstallerError` and an install-log line verbatim. So every string this module did not
itself compose goes through `_redact` at the point it enters — `apply()`'s two `except`
clauses and its client stderr, `verify()`'s unanswerable rule, `_run_sql()`'s two failures,
`MarkerGate._query()`'s answer and `MarkerGate`'s two `except docker.DockerCommandError`
clauses — and the several things done with each are done to the redacted value. The list
grows with the module; it is not a claim that these are all of them. What holds is the
rule: a string this module did not compose is redacted where it ARRIVES, so the uses
downstream inherit it. `MarkerGate` needed both halves because `stage_import()` yields
`probe().detail` into the install log verbatim.
"""

from __future__ import annotations

import fnmatch
import gzip
import io
import re
import stat as stat_module
import subprocess
import threading
import time
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

_IDENTIFIER = re.compile(r"[A-Za-z0-9_]+")
"""What may be written into SQL with NOTHING around it (`CHARACTER SET <charset>`)."""

_UNQUOTABLE = frozenset("'\\") | frozenset(map(chr, range(0x20))) | frozenset("\x7f")
"""What may not appear in a value spliced into `'...'`; there is no escaping here.

The quote and the backslash are the obvious two. The control characters are the
half that is easy to miss and easier to exploit: `create_schemas()` builds its
script by JOINING LINES, and the client ends a statement at `;` on a line, so a
newline inside a password does not have to escape the quotes to add statements —
it closes the line it is on and writes the next one itself, and
`IDENTIFIED BY 'x<newline>GRANT ALL ...'` is a grant nothing here intended. `\\r`
does the same on a client that treats it as a line end and is invisible in a
pasted password either way.
"""


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

    **Nothing the client says leaves here with the password in it.** A phase's literal
    statements are filled from the catalog's `{{TOKEN}}`s, and the shipped Tortoise plan
    fills `CREATE USER ... IDENTIFIED BY '{{DB_PASSWORD}}'` — so the secret is in the SQL
    THIS function streams, not only in `create_schemas()`'s script, and a client quotes
    back the line it could not parse. Every string that arrives from outside this module
    is passed through `_redact` once, where it arrives: the two `except` clauses, and the
    client's stderr at the moment it is split. The four things done with that stderr —
    the install log, the `fail` message, the log record, the `warn` line — read the
    redacted local rather than `proc`, so they inherit it.

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
                f"({_redact(str(exc), password)}). Nothing after it was applied."
            ) from exc
        except (RuntimeError, OSError) as exc:
            raise InstallerError(
                f"The import stopped: {run.rel} could not be read "
                f"({_redact(_read_failure(exc), password)}). "
                "The download may be incomplete; nothing after it was applied."
            ) from exc
        # THE ENTRANCE. Everything below reads `stderr`, never `proc.stderr`: the
        # install log line, the `fail` message, the log record and the `warn` line
        # are four uses of ONE redacted value, so a fifth inherits the redaction
        # instead of having to remember it. See `_redact_lines`.
        stderr = _redact_lines((proc.stderr or "").splitlines(), password)
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


def create_schemas(
    plan: SqlPlan,
    *,
    container: str,
    client: str,
    password: str,
    schemas: Mapping[str, str],
    user: str,
    charset: str,
    exec_stdin: ExecStdin,
    wsl_distro: str | None = None,
) -> None:
    """The implicit phase 0: databases, the app user, its grants. Skipped when `create` is empty.

    Idempotent (`IF NOT EXISTS`, then `ALTER USER` to the current secret) so a
    resume over a database that already has them is a no-op rather than an
    error. One secret: the app user gets the same password the root account
    has, exactly as the scripts did with one `DB_PASSWORD` — the emulator
    connects as `user`, the import streams as root. The password appears in
    this SQL text unavoidably (`IDENTIFIED BY`), though NOT only here — a
    catalog phase can write the same statement as a `{{DB_PASSWORD}}` literal
    for `apply()` to stream, which is what the shipped Tortoise plan does and
    why the redaction is at both. It goes over stdin, this text is never
    logged, and `_run_sql()` takes it back out of whatever the client says.

    **The plan's schema names are judged before the empty-`create` shortcut**,
    not after. Otherwise this function silently accepts a plan `expand()`
    refuses — a bogus `marker_db` with no `create` list — and the two agree
    only because of the order the family happens to call them in. Empty
    `create` is not a corner: it is Tortoise. The value checks below stay after
    it, because they are about a splice that does not happen when there is no
    script to splice into.

    Three values are spliced in, and each is checked for the splice it lands in.
    `password` and `user` go inside `'...'`, which has no escaping — see
    `_UNQUOTABLE`. `charset` goes in with nothing around it at all, and
    `DbFacts.charset` is a free-text catalog field with no pattern on it, so it
    must be a plain identifier. The schema names come from `schemas` after
    `_check_plan_schemas()` has refused any the game does not have, in the same
    words `expand()` uses.

    `wsl_distro` names the daemon holding `container`; see `ExecStdin`. Phase 0
    has to reach the same daemon `apply()` streams into, or the databases exist
    in neither place the user will look.
    """
    _check_plan_schemas(plan, schemas)
    if not plan.create:
        return
    _refuse_unquotable(password, "the database password")
    _refuse_unquotable(user, "the database user name")
    if not _IDENTIFIER.fullmatch(charset):
        raise InstallerError(
            f"the database charset {charset!r} is not a plain identifier, and it is written "
            f"into `CREATE DATABASE ... CHARACTER SET` with nothing around it. {_CATALOG_ERROR}"
        )
    names = [schemas[name] for name in plan.create]
    lines = [f"CREATE DATABASE IF NOT EXISTS `{name}` CHARACTER SET {charset};" for name in names]
    lines.append(f"CREATE USER IF NOT EXISTS '{user}'@'%' IDENTIFIED BY '{password}';")
    lines.append(f"ALTER USER '{user}'@'%' IDENTIFIED BY '{password}';")
    lines += [f"GRANT ALL PRIVILEGES ON `{name}`.* TO '{user}'@'%';" for name in names]
    lines.append("FLUSH PRIVILEGES;")
    _run_sql(
        "\n".join(lines) + "\n",
        what="creating the databases and the application user",
        container=container,
        client=client,
        password=password,
        schema=None,
        exec_stdin=exec_stdin,
        wsl_distro=wsl_distro,
    )


@dataclass(frozen=True)
class UpdateLevel:
    """One schema's expected update level: the column the last file applied should have left.

    `phase` and `rel` are carried so a failure can name the file it read the
    expectation off, rather than only the column it went looking for.
    """

    phase: str
    schema: str
    rel: str
    column: str


def update_levels(runs: Sequence[PhaseRun]) -> tuple[UpdateLevel, ...]:
    """What each `assert_update_level` phase says its schemas should be at, after it ran.

    Read off the RUNS rather than off the plan's globs, and that is the whole
    point: `expand()` has already resolved the patterns against this server dir
    and sorted them by the phase's own `sort`, so the file named here is
    necessarily the last file `apply()` actually streamed into that schema.
    A second glob evaluated here could disagree with the import — different
    working directory, a file added between the two — and then the check would
    be about something nobody imported.

    The column name is CMaNGOS's own convention, verified against the shipped
    trees on 2026-09-03: every core update's first statement is
    `ALTER TABLE <version table> CHANGE COLUMN required_<previous> required_<this> bit`,
    so a schema that has applied `z2837_01_mangos_gobject_near_link.sql` carries
    `required_z2837_01_mangos_gobject_near_link` and nothing else does.

    A run with no schema, or no file, is skipped rather than guessed at: the
    model already refuses the flag on a `statements` phase, and a schema-less
    run has no database to ask.
    """
    last: dict[tuple[str, str], PhaseRun] = {}
    for run in runs:
        if not run.phase.assert_update_level or run.schema is None or run.path is None:
            continue
        last[(run.phase.name, run.schema)] = run
    return tuple(
        UpdateLevel(
            phase=phase_name,
            schema=schema,
            rel=run.rel,
            column=f"required_{_stem(run.path.name if run.path else '')}",
        )
        for (phase_name, schema), run in last.items()
    )


def _stem(filename: str) -> str:
    """`z2837_01_mangos_gobject_near_link.sql` -> `z2837_01_mangos_gobject_near_link`.

    `Path.stem` drops only the LAST suffix, which is right here and would not be
    for a `.sql.gz`; these phases are plain `.sql` and the model refuses the flag
    where there is no file at all. Spelled out so the assumption is visible.
    """
    return filename[: -len(".sql")] if filename.endswith(".sql") else filename


def check_update_levels(
    runs: Sequence[PhaseRun],
    *,
    container: str,
    client: str,
    password: str,
    sql_query: SqlQuery,
    wsl_distro: str | None = None,
) -> tuple[str, ...]:
    """Ask each schema whether it really reached the update level its phase applied.

    The failure this exists for: `wow-vanilla`'s `core updates` phase is
    `on_error: warn`, 171 of its 172 files failed with
    `ERROR 1054 Unknown column 'required_<previous>' in 'db_version'`, and the
    install ended `WoW Vanilla is installed and running`. Both readings of that
    transcript — the dump already contained those updates (true, as it turned
    out), or the realm is 171 updates behind and `warn` is covering a broken
    world — produce the IDENTICAL log. The only instrument that separates them
    is this question, and nothing asked it (2026-09-03).

    Asked of `information_schema.columns` rather than the version table by name,
    because the table differs per schema (`db_version`, `character_db_version`,
    `realmd_db_version`, `logs_db_version`) and naming all four in the catalog
    would be four more things to keep true. The column is unique to the update
    that created it, so finding it anywhere in the schema is the answer.

    Returns one sentence per schema that is NOT where it should be; empty when
    every schema checks out. A query that cannot be answered is a failure and
    never a pass — same rule as `verify()`, and for the same reason: this runs
    immediately before the marker that makes the whole import skippable.
    """
    failed: list[str] = []
    for level in update_levels(runs):
        query = (
            "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema="
            f"{_quoted(level.schema)} AND column_name={_quoted(level.column)}"
        )
        try:
            answer = sql_query(
                container, client, password, level.schema, query, wsl_distro=wsl_distro
            )
        except docker.DockerCommandError as exc:
            failed.append(
                f"{level.schema}: could not be asked what update level it is at "
                f"({_redact(str(exc), password)})"
            )
            continue
        rows = answer.splitlines()
        if len(rows) != 1:
            failed.append(
                f"{level.schema}: the update-level question came back with {len(rows)} rows, "
                "which is not a count"
            )
            continue
        try:
            found = int(rows[0].strip())
        except ValueError:
            failed.append(
                f"{level.schema}: the update-level question answered {rows[0]!r}, not a count"
            )
            continue
        if found < 1:
            failed.append(
                f"{level.schema} is not at the update level '{level.phase}' applied: it has no "
                f"`{level.column}` column, which {level.rel} leaves behind. Every file in that "
                "phase renames the previous file's column, so a schema missing this one stopped "
                "somewhere in the chain and the warnings above were real"
            )
            continue
        logger.info(f"{level.schema} is at {level.column} (from {level.rel})")
    return tuple(failed)


def _quoted(value: str) -> str:
    """A single-quoted SQL literal for a name this module controls.

    `_refuse_unquotable()` already refuses schema names carrying a quote or a
    backslash before any of this runs, and a column name here is built from a
    filename in the checkout. Kept as one function anyway so the quoting is in
    one place rather than in two f-strings.
    """
    _refuse_unquotable(value, "a name in the update-level check")
    return f"'{value}'"


def verify(
    plan: SqlPlan,
    *,
    container: str,
    client: str,
    password: str,
    sql_query: SqlQuery,
    wsl_distro: str | None = None,
) -> tuple[str, ...]:
    """Every verify rule, in order; one sentence per rule that FAILED, `()` when all pass.

    The gate before `write_marker()`: the family raises when this is non-empty
    and writes no marker, because an import whose `warn` phases all failed
    would otherwise read `imported` forever. `rule.db` is the schema as it is
    on the server: the plan's names are the server's names (A10).

    **Four ways a rule fails, and they are four different sentences**, because
    the answer to each is a different thing to do:

    1. **The count is short** — the only one that is about the DUMPS. The
       database answered; there is simply not enough in it.
    2. **The query could not be answered** — the client failed, the table does
       not exist, the container is not running, there is no docker CLI
       (`DockerCliMissingError` is a `DockerCommandError`). A failing rule, never
       an exception: the caller has one code path and the sentence says which it
       was.
    3. **No rows at all** — `sql_query()` returns stdout verbatim, so this is
       `""`. It is NOT a count of zero. A `COUNT(*)` always answers with exactly
       one row, so nothing back means the question was not the one we think we
       asked — and `VerifyRule.min` is `ge=0`, so reading it as zero would let a
       `min: 0` rule PASS an unanswerable query and the marker be written over an
       empty database.
    4. **Something that is not one number** — two rows, or one row that is not
       an integer (the empty string included, which is what a single empty row
       looks like). `splitlines()[0]` would read the first of two rows as if it
       were the count.

    3 and 4 are why the answer is never `.strip()`ed as a whole: under
    `--skip-column-names` one row holding the empty string prints `"\\n"` and no
    rows print `""`, and stripping collapses those two into each other. Count
    with `splitlines()`; an individual row may be trimmed.
    """
    failed: list[str] = []
    for rule in plan.verify:
        try:
            answer = sql_query(
                container, client, password, rule.db, rule.query, wsl_distro=wsl_distro
            )
        except docker.DockerCommandError as exc:
            failed.append(
                f"{rule.db}: `{rule.query}` could not be answered ({_redact(str(exc), password)})"
            )
            continue
        rows = answer.splitlines()
        if not rows:
            failed.append(
                f"{rule.db}: `{rule.query}` came back with no rows at all, so there is no "
                "count to check. A COUNT query always answers with one row, so this is not "
                "a count of zero."
            )
            continue
        if len(rows) != 1:
            failed.append(
                f"{rule.db}: `{rule.query}` came back with {len(rows)} rows, which is not a count"
            )
            continue
        try:
            count = int(rows[0].strip())
        except ValueError:
            failed.append(f"{rule.db}: `{rule.query}` answered {rows[0]!r}, which is not a count")
            continue
        if count < rule.min:
            failed.append(f"{rule.db}: `{rule.query}` is {count}, expected at least {rule.min}")
            continue
        logger.info(f"verified {rule.db}: {rule.query} = {count} (>= {rule.min})")
    return tuple(failed)


def write_marker(
    plan: SqlPlan,
    *,
    container: str,
    client: str,
    password: str,
    exec_stdin: ExecStdin,
    wsl_distro: str | None = None,
) -> None:
    """Record that this plan finished, in `<marker_db>.yulon_install`.

    Only ever called after `verify()` returned `()`; the row is what
    `MarkerGate.probe()` reads as `imported`. The plan hash is stored so an
    upgrade that changes the plan can be SEEN in the log — it is never a
    reason to re-import (see the probe's table: a mismatched hash is a
    finished import from an older plan).

    Written to the daemon that holds `container`, like everything else here: a
    marker on the wrong daemon is a probe that reads `partial` forever and an
    install that repeats itself every time it is asked to run.
    """
    text = (
        f"CREATE TABLE IF NOT EXISTS `{plan.marker_db}`.`{MARKER_TABLE}` "
        "(plan_hash CHAR(16) NOT NULL, finished_unix BIGINT NOT NULL);\n"
        f"INSERT INTO `{plan.marker_db}`.`{MARKER_TABLE}` (plan_hash, finished_unix) "
        f"VALUES ('{plan.plan_hash()}', {int(time.time())});\n"
    )
    _run_sql(
        text,
        what="writing the import marker",
        container=container,
        client=client,
        password=password,
        schema=plan.marker_db,
        exec_stdin=exec_stdin,
        wsl_distro=wsl_distro,
    )


def _run_sql(
    text: str,
    *,
    what: str,
    container: str,
    client: str,
    password: str,
    schema: str | None,
    exec_stdin: ExecStdin,
    wsl_distro: str | None = None,
) -> None:
    """One SQL script over stdin, as an `InstallerError` whichever way it goes wrong.

    Two of `apply()`'s three failures reach here, and they stay apart for the
    same reason they do there: a non-zero exit is the CLIENT rejecting the SQL,
    while `DockerCommandError` is the database not having been asked at all —
    no CLI, no such container, a container that is not running. The third
    (a dump that could not be read) cannot happen: the source is a `BytesIO`
    this module just built. Neither may escape as a bare `RuntimeError` from a
    stage whose every other error the installer shows as an `InstallerError`.
    """
    try:
        proc = exec_stdin(
            container,
            _client_argv(client, schema),
            io.BytesIO(text.encode("utf-8")),
            env={"MYSQL_PWD": password},
            wsl_distro=wsl_distro,
        )
    except docker.DockerCommandError as exc:
        raise InstallerError(
            f"The import stopped while {what}: the database could not be asked "
            f"({_redact(str(exc), password)})."
        ) from exc
    if proc.returncode != 0:
        reason = _last_line((proc.stderr or "").splitlines()) or f"exit {proc.returncode}"
        raise InstallerError(f"The import stopped while {what}: {_redact(reason, password)}")


def _redact(said: str, password: str) -> str:
    """Take the password back out of whatever the client said about it. EVERY time it says it.

    Two scripts in this app contain the secret, not one: `create_schemas()`
    builds `IDENTIFIED BY '<password>'` for the plans that have a `create`
    list, and a catalog phase's literal statements are `{{TOKEN}}`-filled, so
    the shipped Tortoise plan writes the same statement through `apply()`
    instead (its `sql.create` is empty and `create_schemas()` returns at its
    first line). A client quotes back the line it could not parse —
    `ERROR 1064 (42000) at line 1: ... near ''hunter2'@'%''` — and that
    sentence becomes an `InstallerError` and an install-log line, which is what
    a user pastes into a bug report.

    ALL occurrences, which is `str.replace`'s default and is the point rather
    than an accident: `IDENTIFIED BY '<pw>'` and an `ALTER USER` on the next
    line put the secret in one message twice, and a client that echoes a
    two-line context quotes both. Leaving the second is leaving the password.

    Redacting a short password may also blank an innocent word elsewhere in the
    line; that is the harmless direction of the mistake.
    """
    return said.replace(password, "***") if password else said


def _redact_lines(said: Sequence[str], password: str) -> list[str]:
    """`_redact` over the client's whole stderr, at the one point it enters `apply()`.

    A list rather than a generator because `apply()` reads it twice — once into
    the install log, once for `_last_line()` — and a redaction that could be
    consumed by the first reader is one the failure message would not get.
    """
    return [_redact(line, password) for line in said]


def _refuse_unquotable(value: str, what: str) -> None:
    """A value spliced into `'...'` must survive it; see `_UNQUOTABLE` for what does not.

    Generated passwords are hex and the fixed one passed `composegen._refuse_unsafe`,
    so this is a second lock on a door that should already be shut — and it is a
    different door: that one is about YAML, this one about a joined SQL script.
    """
    bad = sorted(set(value) & _UNQUOTABLE)
    if bad:
        raise InstallerError(
            f"{what} contains {' '.join(repr(char) for char in bad)}, which cannot be written "
            "into SQL safely — there is no escaping inside the quotes it goes in. Use letters, "
            "digits and simple punctuation."
        )


_TABLE_EXISTS = (
    "SELECT COUNT(*) FROM information_schema.tables "
    "WHERE table_schema='{schema}' AND table_name='{table}'"
)
"""Whether one table is there — ASKED, rather than inferred from a failing query.

`SELECT ... FROM a_table_that_is_not_there` and "the client could not be
reached" both come back as a `DockerCommandError`, and the two answers lead to
opposite branches: one to `partial`, which drops databases, the other to
`unreadable`, which drops nothing. Asking `information_schema` keeps them apart:
a table that is absent is a COUNT of 0, and an error is still an error.
"""

_MARKER_LOOKUP = "SELECT plan_hash FROM `{schema}`.`{table}` ORDER BY finished_unix DESC LIMIT 1"
"""The newest marker row's hash. A re-import appends a row; the last one to finish is current."""

_VOLUME_NOTE = (
    "If its volume was created by an earlier install with a different password, use Remove to "
    "delete this install's containers and volumes, then install again."
)
"""What to do about the commonest `unreadable`, which is not a bug in the databases."""


def _plan_schemas(plan: SqlPlan, schemas: Mapping[str, str]) -> tuple[str, ...]:
    """Every schema on the server this plan touches, in `create` order then first mention.

    The same five places `expand()` reads names from, refused in the same words
    when one is not this game's: `_check_plan_schemas()` for the four the model
    holds, `_targets()` for a phase's `into`/`into_each`.

    Computed when a `MarkerGate` is BUILT rather than when it probes, because
    `probe()` may not raise: a catalog typo that surfaced out of the probe would
    reach `stage_import()` as neither a state nor an `InstallerError`, and the
    stage has one code path for each.
    """
    _check_plan_schemas(plan, schemas)
    seen: dict[str, None] = {}
    for name in (*plan.create, plan.marker_db):
        seen.setdefault(schemas[name], None)
    for rule in plan.verify:
        seen.setdefault(schemas[rule.db], None)
    for data in plan.player_data:
        seen.setdefault(schemas[data.db], None)
    for phase in plan.phases:
        for schema, _patterns in _targets(phase, schemas):
            if schema is not None:
                seen.setdefault(schema, None)
    return tuple(seen)


class MarkerGate:
    """`native.ImportGate` for a plan with a marker: the probe's five branches, and a reset.

    The same table the AzerothCore probe answers (`controller_wow_wotlk/repair.py`),
    with the evidence this family actually has: a marker row `write_marker()`
    left, and the plan's `player_data` tables.

    **The branches are ordered by what a wrong answer would cost, not by what is
    cheapest to ask.** `imported` is checked before `populated` because a marker
    is proof and a row count is not — a module that seeds 400 accounts would
    otherwise turn a finished import into "somebody's server". `populated` is
    checked before `partial` because `partial` is the only branch that leads to
    `reset()`, which DROPS DATABASES, and it must never see one with a person's
    rows in it.

    **Everything this gate cannot answer is `unreadable`, never `partial`.** A
    count that came back with no rows, with two rows, or with something that is
    not a number is a question that was not the one we think we asked; reading
    any of them as zero would drop a stranger's realmd on the strength of a
    client error. That is why `_table_exists()` asks `information_schema` for a
    COUNT instead of running a `SELECT` and treating the failure as absence.

    Catalog errors — a schema name this game does not have, a seeded username
    that cannot be quoted — are raised by `__init__`, so `probe()` has no way to
    raise at all and `reset()` raises only `InstallerError`.

    `wsl_distro` names the daemon holding `container`; see `SqlQuery`. Asked of
    the wrong daemon, this reads `absent` for a fully populated database and the
    import runs again over a working server.
    """

    def __init__(
        self,
        plan: SqlPlan,
        *,
        container: str,
        client: str,
        password: str,
        schemas: Mapping[str, str],
        sql_query: SqlQuery,
        exec_stdin: ExecStdin,
        wsl_distro: str | None = None,
    ) -> None:
        self._plan = plan
        self._container = container
        self._client = client
        self._password = password
        self._schemas = schemas
        self._sql_query = sql_query
        self._exec_stdin = exec_stdin
        self._wsl_distro = wsl_distro
        self._names = _plan_schemas(plan, schemas)
        for data in plan.player_data:
            for name in data.exclude_usernames:
                _refuse_unquotable(name, f"the seeded account name {name!r} in the SQL plan")

    def probe(self) -> docker.ImportState:
        """What state the plan's schemas are in. Never raises.

        | none of the plan's schemas exist            | absent     |
        | a marker row exists (any hash)              | imported   |
        | rows in a `player_data` table beyond seeds  | populated  |
        | schemas exist, no marker, no player rows    | partial    |
        | anything could not be asked or read         | unreadable |

        A marker whose hash differs from this plan's is a finished import by an
        older plan: logged, reported as imported, never re-run — an app upgrade
        must not `DROP realmd` on a server with accounts.

        `imported` is the only branch that reports `complete`. `populated` says
        nothing about whether the schemas are finished (it short-circuits on the
        first row), and `stage_import()` skips a `populated` database only when
        it is complete — so claiming completeness here would let an import that
        died half way read as done.
        """
        try:
            present = [name for name in self._names if name in self._databases()]
            if not present:
                return docker.ImportState(
                    "absent", f"none of {', '.join(self._names)} exists on this server yet"
                )
            marker = self._marker(present)
            if marker is not None:
                return docker.ImportState("imported", self._marker_detail(marker), complete=True)
            populated = self._player_rows(present)
        except docker.DockerCommandError as exc:
            # THE ENTRANCE for what the DAEMON said, `apply()`'s shape rather
            # than `_run_sql()`'s: redacted here, once, so the branch that
            # yields this into the install log (`stage_import()` does, verbatim)
            # and any branch added later inherit it. The client's STDOUT has its
            # own entrance, in `_query()`.
            return docker.ImportState(
                "unreadable",
                f"the database could not be asked what state it is in "
                f"({_redact(str(exc), self._password)}). {_VOLUME_NOTE}",
            )
        if populated:
            return docker.ImportState("populated", populated)
        return docker.ImportState(
            "partial",
            f"{', '.join(present)} exist{'s' if len(present) == 1 else ''} but there is no "
            "import marker, so the import never finished",
        )

    def reset(self) -> tuple[str, ...]:
        """Drop the plan's schemas that exist — only from `partial`, only the plan's own.

        Returns the schemas dropped (`()` from `absent`). Refuses every other
        state by name: `populated` is somebody's server, `imported` needs no
        reset, `unreadable` proves nothing. Probed again here rather than
        trusting the caller, because this is the one function on this path that
        destroys anything, and the list is READ AGAIN after the probe rather
        than reused from it — a database that went unreachable in between is a
        refusal, not a silent no-op.

        Only `InstallerError` leaves here. A `DockerCommandError` escaping a
        stage whose every other failure the installer shows as an
        `InstallerError` would reach the user as a bare `RuntimeError`.
        """
        state = self.probe()
        if state.state == "absent":
            return ()
        if state.state == "populated":
            raise InstallerError(
                f"This install holds player data ({state.detail}), so nothing was dropped."
            )
        if state.state == "imported":
            raise InstallerError(
                f"This install is already imported ({state.detail}); nothing to reset."
            )
        if state.state == "unreadable":
            raise InstallerError(
                "The databases could not be asked what state they are in, so nothing was "
                f"dropped. {state.detail}"
            )
        doomed = [name for name in self._names if name in self._list("nothing was dropped")]
        for name in doomed:
            logger.warning(f"dropping {name}: it was left half-written by an interrupted import")
        _run_sql(
            "".join(f"DROP DATABASE IF EXISTS `{name}`;\n" for name in doomed),
            what="clearing the half-written databases",
            container=self._container,
            client=self._client,
            password=self._password,
            schema=None,
            exec_stdin=self._exec_stdin,
            wsl_distro=self._wsl_distro,
        )
        left = [name for name in doomed if name in self._list("the import was not re-run")]
        if left:
            raise InstallerError(
                f"{', '.join(left)} could not be dropped, so the import was not re-run."
            )
        return tuple(doomed)

    def _query(self, schema: str | None, statement: str) -> str:
        """One statement, with the client's stdout redacted the moment it arrives.

        THE ENTRANCE for what the CLIENT printed. `_redact` is `str.replace`,
        not `strip`: it removes no newline, so `""` (no rows) and `"\\n"` (one
        row holding the empty string) still say different things afterwards —
        which is the distinction `_marker()` turns into `partial` or `imported`.
        """
        answer = self._sql_query(
            self._container,
            self._client,
            self._password,
            schema,
            statement,
            wsl_distro=self._wsl_distro,
        )
        return _redact(answer, self._password)

    def _databases(self) -> set[str]:
        return {line.strip() for line in self._query(None, "SHOW DATABASES").splitlines()}

    def _list(self, consequence: str) -> set[str]:
        """`_databases()` for `reset()`, where being unable to look is a refusal.

        `probe()` turns the same failure into a state; here there is no state to
        return, and the caller of `reset()` acts on what comes back.
        """
        try:
            return self._databases()
        except docker.DockerCommandError as exc:
            raise InstallerError(
                f"The databases could not be listed ({_redact(str(exc), self._password)}), "
                f"so {consequence}."
            ) from exc

    def _count(self, schema: str, statement: str, what: str) -> int:
        """One `COUNT(*)`, or `DockerCommandError` — never a guess that reads as zero.

        Four answers and only one of them is a number, kept apart for the reason
        `verify()` keeps them apart one function up, plus one this function has
        of its own: here the fallback is not a rule that fails, it is `partial`,
        and `partial` deletes. So no rows, two rows and a non-number are all
        raised, and `probe()` reports them as `unreadable`.
        """
        rows = self._query(schema, statement).splitlines()
        if not rows:
            raise docker.DockerCommandError(
                f"{what} came back with no rows at all, so there is no count to check. A "
                "COUNT query always answers with one row, so this is not a count of zero."
            )
        if len(rows) != 1:
            raise docker.DockerCommandError(
                f"{what} came back with {len(rows)} rows, which is not a count"
            )
        try:
            return int(rows[0].strip())
        except ValueError:
            raise docker.DockerCommandError(
                f"{what} answered {rows[0]!r}, which is not a count"
            ) from None

    def _table_exists(self, schema: str, table: str) -> bool:
        return (
            self._count(
                schema,
                _TABLE_EXISTS.format(schema=schema, table=table),
                f"whether {schema}.{table} exists",
            )
            > 0
        )

    def _marker(self, present: Sequence[str]) -> str | None:
        """The newest marker row's hash, or None when nothing recorded an import.

        `None` means NO ROW, which is `""` from the client. One row holding the
        empty string is `"\\n"`, and that is a marker: the row's EXISTENCE is the
        record, its hash only says which plan wrote it. The two differ by a
        single newline and by which branch the caller lands in — `partial`,
        which drops databases, or `imported`, which never touches them.

        More than one row cannot happen under `LIMIT 1`, so it is a question
        that was not the one we asked, and it is raised rather than read.
        """
        marker_db = self._schemas[self._plan.marker_db]
        if marker_db not in present or not self._table_exists(marker_db, MARKER_TABLE):
            return None
        rows = self._query(
            marker_db, _MARKER_LOOKUP.format(schema=marker_db, table=MARKER_TABLE)
        ).splitlines()
        if not rows:
            return None
        if len(rows) != 1:
            raise docker.DockerCommandError(
                f"{marker_db}.{MARKER_TABLE} answered {len(rows)} rows to a LIMIT 1 query"
            )
        return rows[0].strip()

    def _marker_detail(self, marker: str) -> str:
        """What the `imported` branch says, and the one line an upgrade leaves in the log."""
        detail = f"{self._schemas[self._plan.marker_db]}.{MARKER_TABLE} records a finished import"
        if marker != self._plan.plan_hash():
            detail += (
                f" by an older plan ({marker}, this app's is {self._plan.plan_hash()}); "
                "it is kept as it is"
            )
            logger.info(detail)
        return detail

    def _player_rows(self, present: Sequence[str]) -> str:
        """`"3 rows in characters.characters"` for what a person made; `""` for none.

        Every `player_data` table is asked, not only until the first one answers,
        so the refusal names all of them. A table whose schema is not there, or
        which has not been created yet, is skipped rather than counted — its
        absence is what `partial` is about, not evidence of a player.

        The excluded usernames go into `NOT IN ('...')` with no escaping around
        them; `__init__` refused any that could break out, before a statement
        was built.
        """
        said: list[str] = []
        for data in self._plan.player_data:
            schema = self._schemas[data.db]
            if schema not in present or not self._table_exists(schema, data.table):
                continue
            statement = f"SELECT COUNT(*) FROM `{schema}`.`{data.table}`"
            if data.exclude_usernames:
                names = ", ".join(f"'{name}'" for name in data.exclude_usernames)
                statement += f" WHERE username NOT IN ({names})"
            rows = self._count(schema, statement, f"{schema}.{data.table}")
            if rows:
                said.append(f"{rows} rows in {schema}.{data.table}")
        return ", ".join(said)
