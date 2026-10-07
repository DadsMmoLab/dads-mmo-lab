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

* `phase_drift()` + `record_phases()` — T129: which version of each phase an imported
  install has (`PHASE_TABLE`, written beside the marker; `RELEASED_PHASE_DIGESTS` for a
  marker from before it), what a later plan changed, and the record a corrections press
  leaves. The marker rule below is unchanged: nothing here re-imports anything.

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
import hashlib
import io
import re
import stat as stat_module
import subprocess
import threading
import time
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import BinaryIO, Protocol, cast

from yulon import docker
from yulon.catalog.catalog import SqlPhase, SqlPlan
from yulon.catalog.composegen import ComposeGenError, fill
from yulon.catalog.installer import InstallerError, InstallStopped
from yulon.catalog.native import IMPORT_CANCEL_NOTE
from yulon.catalog.released_plans import RELEASED_PHASE_DIGESTS
from yulon.log import get_logger

logger = get_logger(__name__)

MARKER_TABLE = "yulon_install"
"""The table `write_marker()` creates in `plan.marker_db`; its presence is the import record."""

PHASE_TABLE = "yulon_install_phase"
"""Beside the marker: which version (`SqlPhase.digest()`) of each phase this install has (T129).

One row per phase name. `write_marker()` writes a row for every phase the import
applied whole, just before the marker row (the adopt press writes none), and
`record_phases()` replaces a row when a correction is applied.
`applied_unix` 0 marks a row carried over from `RELEASED_PHASE_DIGESTS` rather
than written when its phase ran.
"""


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
    renames: tuple[tuple[str, str], ...] = ()
    """`(from, to)` database names substituted in this file's text as it is streamed (T179).

    What `centurion/sql/import.sh:41-43` does with `sed` before it loads its three
    schema and routine files: the dumps' triggers and procedures name the live
    realm's databases, and they must name this install's. Empty for every run
    whose file the plan does not list in `rename_files` -- every other file is
    streamed exactly as it lies on disk, the module's rule (A10).
    """


def step_name(run: PhaseRun) -> str:
    """What a confirmation dialog calls this run (T424): a file by its path, a literal by its phase.

    `rel` stays `statement N` -- the install log and the applied record key on it -- but
    to a person "statement 1" says nothing; the phase's own name says what it does.
    """
    if run.path is not None or not run.rel.startswith("statement "):
        return run.rel
    total = len(run.phase.statements)
    if total <= 1:
        return run.phase.name
    return f"{run.phase.name} (step {run.rel.removeprefix('statement ')} of {total})"


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
    plan: SqlPlan,
    server_dir: Path,
    schemas: Mapping[str, str],
    tokens: Mapping[str, str],
    *,
    renames: Sequence[tuple[str, str]] = (),
    rename_files: Collection[str] = (),
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

    `renames` and `rename_files` are `TrinityCoreSqlPlan`'s (T179): each run whose
    `rel` is one of `rename_files` carries the renames, `to` turned into the server's
    name for that schema, and `_open()` substitutes them as the file is streamed. No
    other run carries any. A listed file this import does not apply is refused: the
    procedures it holds would otherwise be missing, or loaded from another file
    naming the live realm's databases, and neither says so anywhere.

    Raises:
        InstallerError: the plan or a phase names a schema outside `schemas`; a pattern
            escapes the server dir, is rooted, or wildcards a directory; a `fail` phase's
            pattern matched no file; a directory could not be listed or a matching entry
            could not be examined (whatever the phase's `on_error` says); a statement
            carries a token `tokens` does not have; or a rename names a schema outside
            `schemas`, or a file this import does not apply.
    """
    _check_plan_schemas(plan, schemas)
    renamed = _renamed(renames, schemas)
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
                    swaps = renamed if rel in rename_files else ()
                    runs.append(PhaseRun(phase, schema, path, None, phase.gzip, rel, swaps))
    # Only the listed files THIS plan's globs reach: the corrections and re-run
    # routes expand a plan cut down to a few phases, and a file of a phase they
    # left out is not one they were ever going to apply.
    globs = [glob for phase in plan.phases for glob in phase.files]
    globs += [glob for phase in plan.phases for glob in (phase.into_each or {}).values()]
    reached = {rel for rel in rename_files if any(fnmatch.fnmatchcase(rel, g) for g in globs)}
    applied = {run.rel for run in runs if run.path is not None}
    unapplied = sorted(reached - applied)
    if unapplied:
        raise InstallerError(
            f"the SQL plan renames the databases named in {', '.join(unapplied)}, but this "
            f"import does not apply {'it' if len(unapplied) == 1 else 'them'} -- the file is "
            "not in the server's sources. Nothing was applied. The sources may not have "
            "cloned completely."
        )
    return tuple(runs)


def _renamed(
    renames: Sequence[tuple[str, str]], schemas: Mapping[str, str]
) -> tuple[tuple[str, str], ...]:
    """Each `(from, to)` with `to` as the server spells that schema, or a catalog refusal."""
    resolved: list[tuple[str, str]] = []
    for old, new in renames:
        if new not in schemas:
            raise InstallerError(
                f"the SQL plan renames {old!r} to {new!r}, which is not one of this game's "
                f"databases ({', '.join(schemas)}). {_CATALOG_ERROR}"
            )
        resolved.append((old, schemas[new]))
    return tuple(resolved)


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
                    f"the SQL plan's {field} setting names {value!r}, which is not one of this "
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
    cancel_note: str = IMPORT_CANCEL_NOTE,
    on_refused: Callable[[PhaseRun], None] | None = None,
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
    the `partial` state `MarkerGate.reset()` exists to clear, and the cancel note says so —
    for the caller that reset is true of. `cancel_note` defaults to `IMPORT_CANCEL_NOTE`
    because that caller, `_import`'s own fresh-import path, is the one whose next
    `stage_import()` really does call `gate.reset()` over a `partial` state. The other
    caller, `_rerun_on_marked()`, runs only after the gate already reads a finished
    import — a stop there changes neither the marker nor the gate's answer — and passes
    its own truthful note rather than inheriting a promise this call cannot keep (T19,
    round-1 rework).

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

    `on_refused` is told about each run a `warn` phase carried on past (T129): the
    corrections press records a phase only when every one of its runs landed, and the
    warning line is for a person, not for code to parse.
    """
    env = {"MYSQL_PWD": password}
    for run in runs:
        _check_cancel(cancel, cancel_note)
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
        if on_refused is not None:
            on_refused(run)
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
    if run.renames:
        # Read whole and substituted, as `import.sh`'s `sed` does: the three files
        # this applies to are the schema and routine dumps, tens of kilobytes. The
        # names are `[A-Za-z0-9_]+` (the model's rule), so bytes are exact.
        opened = gzip.open(run.path, "rb") if run.gzip else run.path.open("rb")
        with opened as handle:
            data = handle.read()
        for old, new in run.renames:
            data = data.replace(old.encode("ascii"), new.encode("ascii"))
        return io.BytesIO(data)
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
    renamed = (
        " (database names " + ", ".join(f"{old} -> {new}" for old, new in run.renames) + ")"
        if run.renames
        else ""
    )
    if run.schema is None:
        return f"{run.phase.name}: {run.rel} (no schema){renamed}"
    return f"{run.phase.name}: {run.rel} -> {run.schema}{renamed}"


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


def _check_cancel(cancel: threading.Event | None, note: str) -> None:
    """Stop between runs, with the one wording every cancel in the app uses (A10).

    `note` names whatever THIS call really leaves behind — `apply()`'s own
    `cancel_note`, passed through rather than read again from `IMPORT_CANCEL_NOTE`
    here, so a caller whose reset promise does not hold cannot be overruled by
    this function's own default.
    """
    if cancel is not None and cancel.is_set():
        raise InstallStopped(f"The import was stopped. {note}")


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
            f"into the statement that creates the databases with nothing around it. "
            f"{_CATALOG_ERROR}"
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


COLUMN_FIELDS = (
    "column_name",
    "column_type",
    "is_nullable",
    "character_set_name",
    "collation_name",
    "extra",
)
"""What `check_same_columns()` compares for each column, in `ordinal_position` order (T159).

Name and type were all round 2 compared, and on MariaDB `column_type` carries
neither nullability nor charset or collation (Codex, round 3): a copy that
declares `NOT NULL` where the original allows NULL takes the step's `IF NOT
EXISTS`, and the core's `INSERT ... SELECT *` then fails on the first NULL; a
different collation converts or refuses the text it copies. `extra` is where a
generated column or `auto_increment` shows."""

_FIELD_WORDS = {
    "is_nullable": "nullable",
    "character_set_name": "character set",
    "collation_name": "collation",
    "extra": "extra",
}
"""How the refusal names each field past the name and type, which it spells as SQL does."""


def _columns(
    schema: str,
    table: str,
    *,
    container: str,
    client: str,
    password: str,
    sql_query: SqlQuery,
    wsl_distro: str | None,
) -> tuple[tuple[str, ...], ...]:
    """`COLUMN_FIELDS` of each column of `schema`.`table`, in order; empty when there is no table.

    Each value as the client prints it -- a NULL is the word `NULL` in batch
    mode -- and compared as printed: two columns with no charset read alike.
    """
    query = (
        f"SELECT {', '.join(COLUMN_FIELDS)} FROM information_schema.columns WHERE table_schema="
        f"{_quoted(schema)} AND table_name={_quoted(table)} ORDER BY ordinal_position"
    )
    answer = sql_query(container, client, password, schema, query, wsl_distro=wsl_distro)
    width = len(COLUMN_FIELDS)
    rows = [line.split("\t") for line in answer.splitlines() if line.strip()]
    return tuple(tuple(value.strip() for value in (row + [""] * width)[:width]) for row in rows)


def check_same_columns(
    runs: Sequence[PhaseRun],
    *,
    container: str,
    client: str,
    password: str,
    sql_query: SqlQuery,
    wsl_distro: str | None = None,
) -> tuple[str, ...]:
    """Ask whether each `same_columns` table these runs' phases name is built like its original.

    T159, from Codex: `CREATE TABLE IF NOT EXISTS copy LIKE original` succeeds
    over a `copy` that is already there whatever it looks like, so a step
    judged by its statement alone is called landed over a table the core's
    `INSERT INTO copy SELECT * FROM original` then fails on. The question is
    the one that copy asks: the same columns in the same order, each alike in
    every field of `COLUMN_FIELDS` (`information_schema.columns`, by
    `ordinal_position`) -- name, type, nullability, charset, collation, extra.

    Returns one sentence per table that is not, naming it and the first place
    it differs and saying what to do; empty when every one checks out. A table
    that cannot be asked is a failure and never a pass, `check_update_levels()`'s
    rule.
    """
    failed: list[str] = []
    seen: dict[tuple[str, str], SqlPhase] = {}
    for run in runs:
        if run.phase.same_columns and run.schema is not None:
            seen.setdefault((run.phase.name, run.schema), run.phase)
    for (_name, schema), phase in seen.items():
        for table, original in phase.same_columns:
            try:
                mine, theirs = (
                    _columns(
                        schema,
                        name,
                        container=container,
                        client=client,
                        password=password,
                        sql_query=sql_query,
                        wsl_distro=wsl_distro,
                    )
                    for name in (table, original)
                )
            except docker.DockerCommandError as exc:
                failed.append(
                    f"{schema}.{table}: its columns could not be read to compare them with "
                    f"{original}'s ({_redact(str(exc), password)})"
                )
                continue
            if not theirs:
                failed.append(
                    f"{schema}.{original} has no columns to compare {table} with -- the table "
                    f"it is a copy of is not there"
                )
            elif mine != theirs:
                failed.append(
                    f"{schema}.{table} is already there but is not built like {original} "
                    f"({_first_difference(mine, theirs, original)}), so it cannot hold a copy of "
                    f"it. It was probably made by hand or by an older server. Rename it (for "
                    f"example `RENAME TABLE {schema}.{table} TO {schema}.{table}_old`) or drop "
                    f"it, and this step makes it again"
                )
    return tuple(failed)


def _first_difference(
    mine: Sequence[tuple[str, ...]], theirs: Sequence[tuple[str, ...]], original: str
) -> str:
    """Where two column lists first part, in words: the position, and what differs there.

    A different name or type reads as the two declarations; any other field
    reads as that field's value on each side (`_FIELD_WORDS`).
    """
    for position, (have, want) in enumerate(zip(mine, theirs, strict=False), start=1):
        if have[:2] != want[:2]:
            return (
                f"column {position} is `{have[0]} {have[1]}` where {original} has "
                f"`{want[0]} {want[1]}`"
            )
        for index, field in enumerate(COLUMN_FIELDS[2:], start=2):
            if have[index] != want[index]:
                return (
                    f"column {position} (`{have[0]}`) has {_FIELD_WORDS[field]} "
                    f"`{have[index]}` where {original} has `{want[index]}`"
                )
    return f"it has {len(mine)} columns where {original} has {len(theirs)}"


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
    landed: Sequence[SqlPhase] | None,
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

    **The version of each phase in `landed` goes in first, in `PHASE_TABLE`**
    (T129): the row a later app compares the phase it ships against, to offer a
    corrected one. `landed` is what the import applied WHOLE -- a `warn` phase
    with a refused step is left out, the rule the corrections press records by
    -- and `None` writes no record at all: the adopt press ran no phase and
    cannot say which version of any is there, so its install reads as knowing
    nothing and is offered nothing (Codex, round 1). First and in the same
    script, because the client stops at the first statement that fails: phase
    rows with no marker are `partial`, which the next press clears and imports
    again, never a marker over a record that was refused.

    Written to the daemon that holds `container`, like everything else here: a
    marker on the wrong daemon is a probe that reads `partial` forever and an
    install that repeats itself every time it is asked to run.
    """
    now = int(time.time())
    text = (
        f"CREATE TABLE IF NOT EXISTS `{plan.marker_db}`.`{MARKER_TABLE}` "
        "(plan_hash CHAR(16) NOT NULL, finished_unix BIGINT NOT NULL);\n"
        + (
            ""
            if landed is None
            else _phase_rows(
                plan.marker_db, {phase.name: (phase.digest(), now) for phase in landed}
            )
        )
        + f"INSERT INTO `{plan.marker_db}`.`{MARKER_TABLE}` (plan_hash, finished_unix) "
        f"VALUES ('{plan.plan_hash()}', {now});\n"
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


def _phase_rows(marker_db: str, rows: Mapping[str, tuple[str, int]]) -> str:
    """`PHASE_TABLE`'s `CREATE TABLE IF NOT EXISTS`, and one `REPLACE` of `rows` (name -> version).

    `REPLACE` because the name is the key: a correction applied replaces the row
    its phase had. The name goes into `'...'` with nothing around it, so it is
    refused here if it could break out -- a catalog value, and a second lock on
    a door the catalog should already keep shut.
    """
    text = (
        f"CREATE TABLE IF NOT EXISTS `{marker_db}`.`{PHASE_TABLE}` "
        "(phase VARCHAR(191) NOT NULL PRIMARY KEY, digest CHAR(16) NOT NULL, "
        "applied_unix BIGINT NOT NULL);\n"
    )
    if not rows:
        return text
    for name in rows:
        _refuse_unquotable(name, f"the SQL phase name {name!r}")
    values = ", ".join(f"('{name}', '{digest}', {when})" for name, (digest, when) in rows.items())
    return (
        text
        + f"REPLACE INTO `{marker_db}`.`{PHASE_TABLE}` (phase, digest, applied_unix) "
        + f"VALUES {values};\n"
    )


@dataclass(frozen=True)
class PhaseLedger:
    """Which version of each phase an imported install has, and where that was read (T129).

    `recorded` is `PHASE_TABLE`'s rows. `assumed` is what `RELEASED_PHASE_DIGESTS`
    says the marker's plan applied, for every phase with no row of its own -- an
    install marked before T129 has only those. Both empty is an install whose
    marker names a plan nobody released: nothing is known, so nothing is stale.
    """

    marker: str
    """The newest marker row's plan hash."""
    recorded: Mapping[str, str]
    assumed: Mapping[str, str]

    @property
    def known(self) -> dict[str, str]:
        """Every phase's version this install is known to have; a row beats the table."""
        return {**self.assumed, **self.recorded}


@dataclass(frozen=True)
class PhaseDrift:
    """The phases a plan has that an install does not, by what may be done about each (T129)."""

    offered: tuple[str, ...]
    """Changed or added, and declared `reapply_when_changed`: the Server tab offers them."""
    withheld: tuple[str, ...]
    """Changed or added, and not declared safe to apply again: named, never applied."""


def phase_drift(plan: SqlPlan, known: Mapping[str, str]) -> PhaseDrift:
    """Compare the plan's phases with an install's `PhaseLedger.known`, in plan order. Pure.

    A phase is stale when the install has no version of it (added since) or a
    different one (corrected since). `rerun_on_marked` phases are left out:
    T11's route applies them on every press already. A phase the install has
    and the plan no longer does asks for nothing -- there is nothing to apply.
    """
    offered: list[str] = []
    withheld: list[str] = []
    for phase in plan.phases:
        if phase.rerun_on_marked or known.get(phase.name) == phase.digest():
            continue
        (offered if phase.reapply_when_changed else withheld).append(phase.name)
    return PhaseDrift(offered=tuple(offered), withheld=tuple(withheld))


def record_phases(
    ledger: PhaseLedger,
    applied: Sequence[SqlPhase],
    *,
    marker_db: str,
    container: str,
    client: str,
    password: str,
    exec_stdin: ExecStdin,
    wsl_distro: str | None = None,
) -> None:
    """Record that `applied` are now at this version on the install `ledger` was read from (T129).

    Only after they ran, and only those whose runs all landed. The rows
    `ledger.assumed` holds are written too, as they are (`applied_unix` 0): the
    reader trusts rows over the release table, so an install from before T129
    given ONE row would otherwise lose what the table said about every other
    phase. The marker row is not touched -- it says the whole plan finished,
    which this press is not.

    Raises:
        InstallerError: the client refused the script, or could not be reached.
    """
    now = int(time.time())
    rows = {name: (digest, 0) for name, digest in ledger.assumed.items()}
    rows.update({phase.name: (phase.digest(), now) for phase in applied})
    _run_sql(
        _phase_rows(marker_db, rows),
        what="recording which version of each phase this install has",
        container=container,
        client=client,
        password=password,
        schema=marker_db,
        exec_stdin=exec_stdin,
        wsl_distro=wsl_distro,
    )


FILE_TABLE = "yulon_install_file"
"""Beside the marker: each world-update file an existing server has, and in what state (T531).

One row per `(phase, file)`, written only by the update route's world catch-up
(`CmangosInstaller.servers_down_work()`), never by the import. `seeded` is a file
that was in the checkout before the route first moved it, so the import applied
it; `started` is written BEFORE a file runs and `applied`/`failed` after, so a
press that stops mid-file leaves `started`, which is never run again. It lives in
the world schema, so it travels with the world: a restored world brings its own
rows, and a Reset that drops the world drops them with it.
"""

FILE_SEEDED = "seeded"
FILE_STARTED = "started"
FILE_APPLIED = "applied"
FILE_FAILED = "failed"

_FILE_STATES = frozenset({FILE_SEEDED, FILE_STARTED, FILE_APPLIED, FILE_FAILED})
_FILE_MAX = 255
"""`FILE_TABLE.file`'s `VARCHAR(255)`: a longer path cannot be recorded, so it is not applied."""


@dataclass(frozen=True)
class FileRow:
    """One row of `FILE_TABLE`: a phase's file, the sha256 of its bytes, and its state."""

    phase: str
    file: str
    sha256: str
    state: str


FileLedger = Mapping[tuple[str, str], FileRow]


def file_digest(path: Path) -> str:
    """The sha256 of a file's bytes as they lie on disk: what the ledger compares."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_ledger_query(marker_db: str) -> str:
    """The one question the ledger is read with; `parse_file_ledger()` reads its answer."""
    return f"SELECT phase, file, sha256, state FROM `{marker_db}`.`{FILE_TABLE}`"


def parse_file_ledger(answer: str) -> dict[tuple[str, str], FileRow]:
    """`file_ledger_query()`'s answer as rows. A row that is not four known columns raises.

    Raises `ValueError` rather than skipping the row: a ledger that cannot be read
    is a ledger that cannot say a file already ran, and guessing there is how a
    file runs twice.
    """
    rows: dict[tuple[str, str], FileRow] = {}
    for line in answer.splitlines():
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != 4 or parts[3] not in _FILE_STATES:
            raise ValueError(f"unreadable {FILE_TABLE} row: {line!r}")
        row = FileRow(*parts)
        rows[(row.phase, row.file)] = row
    return rows


def recordable(rel: str) -> bool:
    """Can `FILE_TABLE` hold this path? Not past 255, and nothing `'...'` cannot carry."""
    return len(rel) <= _FILE_MAX and not set(rel) & _UNQUOTABLE


def file_rows_sql(marker_db: str, rows: Sequence[FileRow], now: int, *, claim: bool = False) -> str:
    """`FILE_TABLE`'s `CREATE TABLE IF NOT EXISTS`, and one `REPLACE` of `rows`.

    `claim` writes a plain `INSERT` instead: the key is `(phase, file)`, so it fails
    when ANY row for that file is already there, which is what makes the `started`
    row a claim two presses cannot both win (Codex, T531) -- the client refuses the
    second, and that press stops before it runs the file.

    InnoDB named, because the dumps' own tables are MyISAM and a MyISAM key is
    capped at 1000 bytes, which `(phase, file)` in utf8mb4 is past.
    """
    text = (
        f"CREATE TABLE IF NOT EXISTS `{marker_db}`.`{FILE_TABLE}` "
        "(phase VARCHAR(191) NOT NULL, file VARCHAR(255) NOT NULL, sha256 CHAR(64) NOT NULL, "
        "state VARCHAR(8) NOT NULL, at_unix BIGINT NOT NULL, PRIMARY KEY (phase, file)) "
        "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n"
    )
    if not rows:
        return text
    for row in rows:
        _refuse_unquotable(row.phase, f"the SQL phase name {row.phase!r}")
        if not recordable(row.file):
            raise InstallerError(
                f"The world update {row.file!r} cannot be recorded as applied, so it was not "
                "applied: its path is too long or holds a character SQL cannot carry here."
            )
        if row.state not in _FILE_STATES or not _HEX64.fullmatch(row.sha256):
            raise InstallerError(f"internal: a malformed {FILE_TABLE} row {row!r}")
    values = ", ".join(
        f"('{row.phase}', '{row.file}', '{row.sha256}', '{row.state}', {now})" for row in rows
    )
    return (
        text
        + f"{'INSERT' if claim else 'REPLACE'} INTO `{marker_db}`.`{FILE_TABLE}` "
        + "(phase, file, sha256, state, at_unix) "
        + f"VALUES {values};\n"
    )


_HEX64 = re.compile(r"[0-9a-f]{64}")


def record_world_files(
    rows: Sequence[FileRow],
    *,
    marker_db: str,
    container: str,
    client: str,
    password: str,
    exec_stdin: ExecStdin,
    wsl_distro: str | None = None,
    claim: bool = False,
) -> None:
    """Write `rows` into `FILE_TABLE` (making it when there is none). The ledger's one write.

    Raises:
        InstallerError: the client refused the script, or could not be reached.
    """
    _run_sql(
        file_rows_sql(marker_db, rows, int(time.time()), claim=claim),
        what=(
            "claiming a world update (another update of this server may be applying it)"
            if claim
            else "recording which world updates this server has"
        ),
        container=container,
        client=client,
        password=password,
        schema=marker_db,
        exec_stdin=exec_stdin,
        wsl_distro=wsl_distro,
    )


_SYSTEM_SCHEMAS = ("mysql", "information_schema", "performance_schema", "sys")
"""Schemas no world update has business naming, beside the server's own non-world ones."""


def _sql_tokens(backslash_escapes: bool) -> re.Pattern[str]:
    """The tokenizer for one reading of `\\` inside a string (see `_SQL_TOKENS`)."""
    if backslash_escapes:
        strings = r"'(?:[^'\\]|\\.|'')*'?" + r'|"(?:[^"\\]|\\.|"")*"?'
    else:
        strings = r"'(?:[^']|'')*'?" + r'|"(?:[^"]|"")*"?'
    return re.compile(
        strings
        + r"|`[^`]*`?"
        + r"|--(?=[\s\x00-\x1f]|$)[^\n]*|#[^\n]*"
        + r"|/\*M?![0-9]*|\*/"
        + r"|/\*.*?(?:\*/|$)"
        + r"|[^'\"`#/*-]+"
        + r"|.",
        re.S,
    )


_SQL_TOKENS = _sql_tokens(backslash_escapes=True)
_SQL_TOKENS_NO_BACKSLASH = _sql_tokens(backslash_escapes=False)
"""SQL read left to right: a string, an identifier, a comment, an executable comment's
markers, or plain text. Whichever starts first wins, so an apostrophe in a comment is
the comment's and a `--` in a string is the string's (Codex, T531)."""


def _code_only(text: str, *, backslash_escapes: bool = True) -> tuple[str, bool]:
    """The SQL MySQL would execute, and whether it holds an executable comment: string
    bodies emptied, comments made spaces, an executable comment's body kept (only its
    `/*!NNNNN` or MariaDB's `/*M!NNNNN`, and `*/`, go). A marker inside a string or a
    plain comment is text."""
    parts: list[str] = []
    executable = False
    tokens = _SQL_TOKENS if backslash_escapes else _SQL_TOKENS_NO_BACKSLASH
    for match in tokens.finditer(text):
        token = match.group(0)
        if token[0] == '"' and re.fullmatch(r'"\w+"', token):
            # Under ANSI_QUOTES -- set in the file, globally, or by a client -- this is
            # an identifier, `"characters"."foo"`; read as one either way (Codex, T531).
            parts.append(f" {token[1:-1]}")
        elif token[0] in "'\"":
            parts.append(" '' ")
        elif token[0] == "`":
            # A quoted name is a token of its own even glued to a keyword
            # (UPDATE`characters`.`x`), so it starts after a space here (Codex, T531).
            parts.append(f" {token}")
        elif token.startswith(("/*!", "/*M!")):
            executable = True
            parts.append(" ")
        elif token == "*/":
            parts.append(" ")
        elif token.startswith(("--", "#", "/*")):
            parts.append(" ")
        else:
            parts.append(token)
    return "".join(parts), executable


def foreign_schemas(
    path: Path, others: Collection[str], *, executable_comments_ok: bool = False
) -> tuple[str, ...]:
    """What in a world update's text could reach past the world schema; empty when nothing.

    The update runs with `into` as the client's DEFAULT schema only -- the account
    can reach every schema -- so a file that says `USE characters` or
    `characters.x` would write there (Codex, T531). Read as the SQL MySQL runs
    (`_code_only()`): comments become spaces -- a separator, as they are to MySQL --
    an executable comment's body is kept and the file is flagged for having one,
    and string literals are emptied -- quest text says "ten logs. Then …" (cold
    review of T531), and a refused file holds back every later update -- so a
    schema name counts at a SQL boundary followed by a dot, whitespace around the
    dot allowed as MySQL allows it (`logs . x`). `--` is a comment only with
    whitespace after it, as in MySQL (`a--1` is arithmetic). Measured
    2026-10-07: none of tbc-db 86672361's 44 or classic-db ec4f5961's 357
    Updates/*.sql files trips it.

    `executable_comments_ok` is the bot reload's reading (T534 cold review): the
    bots' mysqldump files carry `/*!40101 … */` lines, so an executable comment is not
    by itself a reason there -- its body is still read for schema names, and both
    quote readings are still taken. Of sql_mode changes, only mysqldump's own pair
    (`_DUMP_SQL_MODE`) is accepted; any other one is refused as in a world update.
    """
    raw = path.read_text(encoding="utf-8", errors="replace")
    found: list[str] = []
    # Both readings of `\'`: with backslash escapes, and as NO_BACKSLASH_ESCAPES (set
    # globally or by a client) reads it -- whatever either one exposes counts (Codex, T531).
    paired = True
    if executable_comments_ok:
        # mysqldump's own pair, and nothing else, may set sql_mode in a bot file: a switch
        # part-way (NO_BACKSLASH_ESCAPES) changes what is a string from there on and can
        # hide a statement from both readings (re-review of T534). The pair is one header
        # then one footer, and the saved mode is named nowhere else -- a footer alone
        # would restore whatever a file put in @OLD_SQL_MODE itself (Codex).
        kinds = [m.group(1) is not None for m in _DUMP_SQL_MODE.finditer(raw)]
        paired = kinds in ([], [True], [True, False])
        raw = _DUMP_SQL_MODE.sub(" ", raw)
        paired = paired and not re.search(r"(?i)OLD_SQL_MODE", raw)
    for escapes in (True, False):
        text, executable = _code_only(raw, backslash_escapes=escapes)
        for reason in _foreign_in(text, executable and not executable_comments_ok, others):
            if reason not in found:
                found.append(reason)
    if not paired and "a change of sql_mode" not in found:
        found.append("a change of sql_mode")
    return tuple(found)


_DUMP_SQL_MODE = re.compile(
    r"/\*!40101\s+SET\s+(?:(@OLD_SQL_MODE\s*=\s*@@SQL_MODE\s*,\s*SQL_MODE\s*=\s*"
    r"'NO_AUTO_VALUE_ON_ZERO')|SQL_MODE\s*=\s*IFNULL\(\s*@OLD_SQL_MODE\s*,\s*''\s*\))\s*\*/",
    re.I,
)
"""mysqldump's header and footer SQL_MODE lines, exactly: the only sql_mode a bot file may set."""

_CLIENT_COMMAND = re.compile(r"(?im)^[ \t]*(?:\\[a-z.!#?]|(?:connect|source|system)\b)")
"""A mysql/mariadb CLIENT command at the start of a line (T548): `\\u db` and `connect`
switch schema, `\\.`/`source` read another file, `\\!`/`system` run a shell. The client
acts on them itself, so no SQL reading sees them; on a line of their own they are refused."""


def _foreign_in(text: str, executable: bool, others: Collection[str]) -> list[str]:
    """`foreign_schemas()` over one reading of the file's SQL."""
    found = [
        name
        for name in sorted({*others, *_SYSTEM_SCHEMAS})
        if re.search(rf"(?:^|[\s(,=;])`?{re.escape(name)}`?\s*\.\s*`?[A-Za-z_]", text, re.I | re.M)
    ]
    if re.search(r"(?im)(^|;)\s*USE\s", text):
        found.append("USE")
    if executable:
        found.append("an executable comment")
    if _CLIENT_COMMAND.search(text):
        found.append("a mysql client command")
    if re.search(r"(?i)\bsql_mode\b", text):
        # ANSI_QUOTES or NO_BACKSLASH_ESCAPES would change what is a string from that
        # point on, which this reading cannot follow (Codex, T531): refused, not guessed.
        found.append("a change of sql_mode")
    if re.search(r"(?i)\b(CREATE|DROP|ALTER)\s+(DATABASE|SCHEMA)\b", text):
        found.append("a whole-database statement")
    return found


_TOKENS = re.compile(
    r"'(?:[^'\\]|\\.|'')*'"
    r'|"(?:[^"\\]|\\.)*"'
    r"|`[^`]*`"
    r"|--[^\n]*|#[^\n]*"
    r"|/\*![0-9]*|\*/"
    r"|/\*.*?\*/"
    r"|;"
    r"|[^'\"`;#/*-]+"
    r"|.",
    re.S,
)
_TABLE = r"`?(?:\w+`?\.`?)?(\w+)`?"


def _statements(text: str) -> Iterator[str]:
    """Each statement's text with comments and string bodies gone; quote-aware. An
    executable comment (`/*!40101 … */`) is read as the SQL MySQL runs from it."""
    parts: list[str] = []
    for match in _TOKENS.finditer(text):
        token = match.group(0)
        if token == ";":
            yield "".join(parts).strip()
            parts = []
        elif token.startswith("/*!") or token == "*/":
            # An executable comment's body IS SQL the server runs: kept, markers dropped.
            parts.append(" ")
        elif token.startswith(("--", "#", "/*")):
            parts.append(" ")
        elif token[0] in "'\"":
            parts.append("''")
        else:
            parts.append(token)
    tail = "".join(parts).strip()
    if tail:
        yield tail


def whole_table_problem(path: Path) -> str | None:
    """Why re-running this file whole could leave something a single run would not; None if
    it cannot (T534).

    Whole-table means: every table the file INSERTs into or UPDATEs is dropped
    (`DROP TABLE IF EXISTS`) or emptied (`DELETE FROM t` / `TRUNCATE t`, no WHERE)
    earlier in the same file -- `REPLACE` and `INSERT IGNORE` included. `CREATE
    TABLE`/`INDEX`, `DELETE … WHERE`, `SET`, `SELECT`, `LOCK`/`UNLOCK TABLES` and mysqldump's
    `ALTER TABLE … DISABLE/ENABLE KEYS` are idempotent as statements; an executable
    comment's body is read as the statement it is. Anything else (`ALTER`, `RENAME`,
    `USE`, a procedure) is a reason.
    Measured 2026-10-07 at playerbots 45bed519: every sql/world, world/tbc and
    world/classic file passes.
    """
    if only_creates_indexes(path):
        return None  # run one index at a time by the reload, each made fresh
    emptied: set[str] = set()
    dropped: set[str] = set()
    for raw in _statements(path.read_text(encoding="utf-8", errors="replace")):
        if not raw:
            continue
        sql = " ".join(raw.split())
        head = sql.upper()
        if head.startswith(("SET ", "SELECT ", "LOCK TABLES", "UNLOCK TABLES")):
            continue
        if re.fullmatch(rf"(?i)ALTER TABLE {_TABLE} (DISABLE|ENABLE) KEYS", sql):
            continue
        match = re.match(rf"(?i)CREATE TABLE (?:IF NOT EXISTS )?{_TABLE}", sql)
        if match:
            # Dropped first, or the second run fails (plain) or keeps the OLD table's
            # definition (IF NOT EXISTS) -- not what a fresh install has (Codex, T534 r4-5).
            if match.group(1).lower() not in dropped:
                return f"it creates {match.group(1)} without dropping it first"
            continue
        match = re.match(rf"(?i)CREATE (?:UNIQUE )?INDEX \S+ ON {_TABLE}", sql)
        if match:
            # In a mixed file an index that is already there stops the script part-way;
            # only on a table this file dropped is it new for certain (index-only files
            # are run one index at a time instead, and never reach here).
            if match.group(1).lower() not in dropped:
                return f"it adds an index to {match.group(1)}, which it did not drop first"
            continue
        match = re.match(rf"(?i)DROP TABLE IF EXISTS {_TABLE}", sql)
        if match:
            emptied.add(match.group(1).lower())
            dropped.add(match.group(1).lower())
            continue
        match = re.match(rf"(?i)(?:DELETE FROM|TRUNCATE(?: TABLE)?) {_TABLE}(.*)$", sql)
        if match:
            rest = match.group(2).strip().upper()
            if not rest:
                # Only the plain whole-table form empties it (Codex, T534).
                emptied.add(match.group(1).lower())
                continue
            if rest.startswith("WHERE ") and not re.search(r"\b(LIMIT|ORDER BY)\b", rest):
                continue  # deletes the same rows however often it runs
            return f"it runs a statement that is not safe to repeat ({sql[:40]}…)"
        # REPLACE and INSERT IGNORE add a row wherever no key collides, so they need
        # the table emptied first like a plain INSERT does (Codex, T534 round 2).
        if re.match(r"(?i)UPDATE ", sql) and not re.match(rf"(?i)UPDATE {_TABLE} SET ", sql):
            # A join, a list or an alias can write a table other than the first one
            # named (Codex, T534): only the single-table form is read.
            return f"it runs an UPDATE of more than one table ({sql[:40]}…)"
        match = re.match(rf"(?i)(?:INSERT(?: IGNORE)? INTO|REPLACE INTO|UPDATE) {_TABLE}", sql)
        if match:
            if match.group(1).lower() not in emptied:
                return f"it writes {match.group(1)} without emptying it first"
            continue
        return f"it runs a statement that is not safe to repeat ({sql[:40]}…)"
    return None


def only_creates_indexes(path: Path) -> bool:
    """Is this file nothing but `CREATE [UNIQUE] INDEX` statements (and `SET`s)?

    The bots' `ai_playerbot_indexes.sql`: on a server that has the indexes the
    client refuses the first one, which says they are there, not that a table
    was harmed (T534).
    """
    seen = False
    for raw in _statements(path.read_text(encoding="utf-8", errors="replace")):
        head = " ".join(raw.split()).upper()
        if not head or head.startswith("SET "):
            continue
        if not head.startswith(("CREATE INDEX", "CREATE UNIQUE INDEX")):
            return False
        seen = True
    return seen


def index_statements(path: Path) -> list[str]:
    """Each `CREATE [UNIQUE] INDEX` statement of an index-only file, as written (T534)."""
    return [
        " ".join(raw.split())
        for raw in _statements(path.read_text(encoding="utf-8", errors="replace"))
        if " ".join(raw.split()).upper().startswith(("CREATE INDEX", "CREATE UNIQUE INDEX"))
    ]


def recreate_index_statement(statement: str) -> str | None:
    """`DROP INDEX` + the same `CREATE INDEX`, for an index that is already there (T534)."""
    match = re.match(
        r"(?is)\s*CREATE\s+(?:UNIQUE\s+)?INDEX\s+(`?\w+`?)\s+ON\s+(`?\w+`?)", statement
    )
    if match is None:
        return None
    return f"DROP INDEX {match.group(1)} ON {match.group(2)};\n{statement}"


def seed_rows(runs: Sequence[PhaseRun], ledger: FileLedger) -> tuple[FileRow, ...]:
    """`seeded` rows for every file of a phase the ledger holds NO row of, in run order. Pure
    but for reading each file's bytes.

    Asked with the runs expanded BEFORE the checkout moves: a phase with no rows is
    one whose files were applied by the import from the checkout as it stood then.
    A file the ledger could not hold is left out, and is then offered as new and
    refused by name when it is recorded, never applied unrecorded.
    """
    known = {phase for phase, _file in ledger}
    return tuple(
        FileRow(run.phase.name, run.rel, file_digest(run.path), FILE_SEEDED)
        for run in runs
        if run.path is not None and run.phase.name not in known and recordable(run.rel)
    )


@dataclass(frozen=True)
class PendingFiles:
    """What the ledger says about a phase's files as they lie on disk now (T531)."""

    new: tuple[PhaseRun, ...]
    """Not in the ledger, in a phase nothing is stuck in: applied once, in this order."""
    changed: tuple[str, ...]
    """In the ledger with other bytes: edited upstream since; never run again."""
    unsure: tuple[str, ...]
    """Left `started` by a press that stopped mid-file: never run again."""
    failed: tuple[str, ...]
    """Recorded `failed`: the client refused it. Never run again."""
    withheld: tuple[str, ...]
    """Not in the ledger, but its phase has an `unsure` or `failed` file: the updates are a
    chain in file order, so nothing after a file that did not land runs past it."""
    moved: tuple[PhaseRun, ...] = ()
    """Not in the ledger under this name, but its exact bytes are, under another: a file
    upstream renamed or moved (Codex, T531). Recorded `seeded` under the new name and
    never run again."""


def pending_files(runs: Sequence[PhaseRun], ledger: FileLedger) -> PendingFiles:
    """Sort the runs against the ledger. Pure but for reading each held file's bytes.

    A `started` or `failed` row is the player's to settle, never this function's: it
    is not run again, and no new file of its phase runs either until that row is
    gone (`CmangosInstaller._catch_up_world()` says how).
    """
    stuck = {
        phase for (phase, _file), row in ledger.items() if row.state in (FILE_STARTED, FILE_FAILED)
    }
    here = {(run.phase.name, run.rel) for run in runs}
    # A move: the same phase, bytes the ledger holds under a path that is GONE now
    # (Codex, T531 round 5) -- a copy beside its original is new, and runs.
    gone_bytes = {
        (phase, row.sha256) for (phase, file), row in ledger.items() if (phase, file) not in here
    }
    new: list[PhaseRun] = []
    moved: list[PhaseRun] = []
    withheld: list[str] = []
    changed: list[str] = []
    unsure: list[str] = []
    failed: list[str] = []
    for run in runs:
        row = ledger.get((run.phase.name, run.rel))
        if row is None:
            if run.path is not None and (run.phase.name, file_digest(run.path)) in gone_bytes:
                moved.append(run)
            elif run.phase.name in stuck:
                withheld.append(run.rel)
            else:
                new.append(run)
        elif row.state == FILE_STARTED:
            unsure.append(run.rel)
        elif row.state == FILE_FAILED:
            failed.append(run.rel)
        elif run.path is not None and file_digest(run.path) != row.sha256:
            changed.append(run.rel)
    # A stuck row whose file is gone from the checkout still holds its phase back, so
    # it is named too, or the player would be told "they wait behind it" about nothing.
    phases = {run.phase.name for run in runs}
    for (phase, file), row in sorted(ledger.items()):
        if phase in phases and (phase, file) not in here:
            if row.state == FILE_STARTED:
                unsure.append(file)
            elif row.state == FILE_FAILED:
                failed.append(file)
    return PendingFiles(
        new=tuple(new),
        changed=tuple(changed),
        unsure=tuple(unsure),
        failed=tuple(failed),
        withheld=tuple(withheld),
        moved=tuple(moved),
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


def plan_schemas(plan: SqlPlan, schemas: Mapping[str, str]) -> tuple[str, ...]:
    """Every schema on the server this plan touches, in `create` order then first mention.

    The same five places `expand()` reads names from, refused in the same words
    when one is not this game's: `_check_plan_schemas()` for the four the model
    holds, `_targets()` for a phase's `into`/`into_each`.

    Computed when a `MarkerGate` is BUILT rather than when it probes, because
    `probe()` may not raise: a catalog typo that surfaced out of the probe would
    reach `stage_import()` as neither a state nor an `InstallerError`, and the
    stage has one code path for each.

    Public since T19: the adopt confirmation lists the databases the row it
    writes is a claim about, and that list has to be THIS one — a dialog naming
    `plan.create` would leave out the schemas only a phase's `into` mentions,
    which for the Tortoise plan is every one of them.
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
        self._names = plan_schemas(plan, schemas)
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

    def adoption_gaps(self) -> tuple[str, ...]:
        """What this plan names and these databases do not have. `()` when nothing is missing.

        The reading an ADOPT press consents to (T19), and deliberately not part
        of `probe()`. Presence and nothing else: every schema the plan names
        exists, and every table its `player_data` names is inside the schema
        that should hold it. Nothing here claims the import FINISHED — three
        rounds of trying to derive that from the plan (per-schema table counts,
        the plan's own `verify` rules, then the table set parsed out of every
        dump file) each found the next layer of inference, and the answer was to
        stop inferring and let the person say so. What is left is the check that
        keeps a marker row off a database that is plainly not the thing being
        claimed: a `realmd` that was never created, an `account` table that is
        not there.

        `player_data` is the only place a `SqlPlan` names a TABLE rather than a
        file, which is why the set is exactly that and not more. A table created
        inside a dump is invisible without opening the dump.

        A schema that is missing is named ONCE — its tables are not then listed
        after it, because "realmd does not exist" already says why
        `realmd.account` is not there and a reader given both reads two faults.

        Unlike `probe()`, this MAY raise: `docker.DockerCommandError` travels
        out of `_databases()` and `_table_exists()`. That is the point of it
        being separate. A state has an `unreadable` member to land in; a list of
        gaps has none, and an empty tuple from a database that never answered
        would read as "everything is there" — the one answer that would let the
        row be written over a database nobody could see. The caller turns the
        raise into its own refusal.

        Raises:
            docker.DockerCommandError: the databases could not be asked.
        """
        present = self._databases()
        gaps = [
            f"{name} does not exist on this server" for name in self._names if name not in present
        ]
        for data in self._plan.player_data:
            schema = self._schemas[data.db]
            if schema in present and not self._table_exists(schema, data.table):
                gaps.append(f"{schema}.{data.table} is not there")
        return tuple(gaps)

    def phase_ledger(self) -> PhaseLedger | None:
        """Which version of each phase these databases have; None when there is no marker (T129).

        Read only for an install the probe would call `imported`: no marker row
        is no finished import this app recorded, and there is nothing to
        compare a phase against. The marker's hash picks the release table's
        entry; `PHASE_TABLE`'s rows, when the table is there, are laid over it.

        Unlike `probe()`, this MAY raise, for `adoption_gaps()`'s reason: an
        empty ledger from a database that never answered would read as "this
        install has none of the phases", and every re-appliable one would be
        offered on the strength of a question nobody answered.

        Raises:
            docker.DockerCommandError: the databases could not be asked, or
                answered in a shape that is not the one asked for.
        """
        present = [name for name in self._names if name in self._databases()]
        marker = self._marker(present)
        if marker is None:
            return None
        marker_db = self._schemas[self._plan.marker_db]
        recorded: dict[str, str] = {}
        if self._table_exists(marker_db, PHASE_TABLE):
            answer = self._query(
                marker_db, f"SELECT phase, digest FROM `{marker_db}`.`{PHASE_TABLE}`"
            )
            for line in answer.splitlines():
                fields = line.split("\t")
                if len(fields) != 2:
                    raise docker.DockerCommandError(
                        f"{marker_db}.{PHASE_TABLE} answered {line!r}, which is not a phase "
                        "and its version"
                    )
                recorded[fields[0]] = fields[1].strip()
        release = RELEASED_PHASE_DIGESTS.get(marker, {})
        assumed = {name: digest for name, digest in release.items() if name not in recorded}
        return PhaseLedger(marker=marker, recorded=recorded, assumed=assumed)

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
