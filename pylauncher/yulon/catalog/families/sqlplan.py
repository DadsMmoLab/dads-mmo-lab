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

Later tasks add `create_schemas()` (the implicit phase 0: databases, the app user,
grants), `apply()` (streams each file on the stdin of `docker exec -i`, gzip inflated on
the way, with the phase's `fail`/`warn` policy), and `verify()` + `write_marker()` (the
completion record, written only after `verify()` returns no failing rule). The two
Protocols and `MARKER_TABLE` below are their shared vocabulary, declared here so the
transport shape has one spelling.

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
import re
import stat as stat_module
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import BinaryIO, Protocol

from yulon.catalog.catalog import SqlPhase, SqlPlan
from yulon.catalog.composegen import ComposeGenError, fill
from yulon.catalog.installer import InstallerError
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

    The real function takes a further defaulted `wsl_distro`; the protocol names only
    what `apply()` passes, so a test fake may be the four parameters and nothing else.
    """

    def __call__(
        self, container: str, argv: Sequence[str], source: BinaryIO, *, env: Mapping[str, str]
    ) -> subprocess.CompletedProcess[str]: ...


class SqlQuery(Protocol):
    """`docker.sql_query`'s shape: one batch, skip-column-names statement; rows back as text."""

    def __call__(
        self, container: str, client: str, password: str, schema: str | None, statement: str
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

    `test_sqlplan.py`'s captured `LS_V_*` lists are the definition. Beyond them this key
    was checked against `ls -v` (GNU coreutils 8.32) over 4,800 generated names built
    from digits, letters, `_ - . ~ +`, doubled dots, leading dots and
    `.sql`/`.sql.gz`/`.y.sql` tails: identical on every name without a `~`, and only the
    corner above on the ones with.
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
