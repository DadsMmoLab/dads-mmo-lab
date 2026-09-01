"""Tests for the SQL plan kind (`yulon.catalog.families.sqlplan`).

Nothing here touches a daemon: `expand()` is pure, and the two `docker` seams
(`exec_stdin`, `sql_query`) are only compared against the Protocols that stand
in for them. The load-bearing test is the natural sort: cmangos names its
updates `z2817_01_mangos_x.sql` and the shell installers applied them with
`ls -v`, so a plain `sorted()` would run `z10` before `z9` and the world would
be updated out of order without a single error line. **A wrong order still
runs.** That is why every ordering assertion here is a SEQUENCE, never a set.

The `LS_V_*` lists are the literal output of `ls -v` over files of those names
(GNU coreutils 8.32 as shipped with Git for Windows; the `filevercmp` it uses
is unchanged in 9.4 for names like these). To regenerate:
`mkdir t && cd t && touch <names> && ls -v`.

The second load-bearing group is the THREE answers a glob can give. `Path.glob`
gives two — it swallows every `OSError` and answers with a short list — so a
directory this process may not list comes back as "nothing matched", which for
a `warn` phase means "skipped, carry on" and for the user means a database that
is quietly missing a third of its content. `expand()` walks with `iterdir()`
and keeps the answers apart; the tests below name each one.
"""

from __future__ import annotations

import gzip
import inspect
import logging
import re
import subprocess
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import BinaryIO

import pytest

from yulon import docker
from yulon.catalog import native
from yulon.catalog.catalog import CatalogEntry, SqlPhase, SqlPlan, VerifyRule, load_catalog
from yulon.catalog.families import sqlplan
from yulon.catalog.installer import InstallerError

# Real cmangos update names plus the awkward neighbours, in the order `ls -v` prints them.
LS_V_ORDER = [
    "z9_01_mangos_c.sql",
    "z10_01_mangos_b.sql",
    "z2799_01_mangos_a.sql",
    "z2817_01_mangos_x.sql",
    "z2817_02_mangos_y.sql",
    "z2817_10_mangos_d.sql",
    "z2818_01_mangos_z.sql",
    "z2818_01_mangos_zz.sql",
]

# Every corner `filevercmp` has an opinion on: a file suffix that is stripped
# for the first pass (`x.sql` < `x.y.sql` < `x1.sql`), `~` before end-of-name
# before letters, punctuation after letters, leading zeros ignored, and a
# dotted version inside the name (`1.9.0` < `1.10.0`). Captured from `ls -v`.
LS_V_AWKWARD = [
    "TBCDB_1.9.0.sql.gz",
    "TBCDB_1.10.0.sql.gz",
    "a~.sql",
    "a.sql",
    "a1~.sql",
    "a1.sql",
    "a1b.sql",
    "b9.sql",
    "b10.sql",
    "s0099_mangos.sql",
    "s0100_mangos.sql",
    "x.sql",
    "x.y.sql",
    "x1.sql",
    "x-1.sql",
    "x_1.sql",
    "z1_a.sql",
    "z01_b.sql",
]

# The other release-file spelling this engine will meet: a dated name, as the
# world-database repos and AzerothCore both write them. `2024_1_15_01` sorting
# BEFORE `2024_01_15_02` is the whole point - `1` and `01` are the same number,
# so the field after them decides, and a lexicographic sort puts it last.
LS_V_DATED = [
    "2023_12_31_00_world.sql",
    "2024_01_09_00_world.sql",
    "2024_1_15_01_world.sql",
    "2024_01_15_02_world.sql",
    "2024_01_15_10_world.sql",
    "z2817_01_mangos_spell_template.sql",
]

# Names whose CUT forms tie, so the tie-breaks decide. `w.*` all cut to `w`, which
# is `filevercmp` restoring the suffixes and comparing the whole names by the version
# rule (`b9` < `b10`, and a plain `strcmp` would say the opposite). `w.a01.sql` and
# `w.a1.sql` tie again there - same cut name, same version - so only C's last resort,
# `strcmp` of the whole name, separates them. `z0001_mangos` and `z1_mangos` are the
# same version with DIFFERENT cut names, which is the other tie-break. From `ls -v`.
LS_V_TIES = [
    "w.a1b.sql",
    "w.a01.sql",
    "w.a1.sql",
    "w.b9a.sql",
    "w.b9.sql",
    "w.b10.sql",
    "w.sql",
    "z0001_mangos.y.sql",
    "z1_mangos.sql",
    "z1_mangos.sql.gz",
]

# A dotfile sorts before every name that does not start with one - before even `~`,
# which is otherwise the lowest character there is, and which is the only neighbour
# that tells the dot rule apart from the empty cut name a dotfile also produces.
# Captured from `ls -av`.
LS_V_DOTS = [".gitignore", "~lock.sql", "a.sql", "z.sql"]

SCHEMAS = {"mangos": "mangos", "realmd": "realmd", "characters": "characters", "logs": "logs"}
TOKENS = {"REALM_HOST": "127.0.0.1", "WORLD_PORT": "8085", "CLIENT_BUILD": "8606"}


def _plan(*phases: SqlPhase) -> SqlPlan:
    return SqlPlan(
        create=("mangos", "realmd", "characters", "logs"), phases=phases, marker_db="mangos"
    )


def _touch(directory: Path, *names: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / name).write_text("SELECT 1;\n", encoding="utf-8")
    return directory


# -- natural_key: is it `ls -v`? ----------------------------------------------


def test_natural_key_reproduces_ls_v() -> None:
    shuffled = sorted(LS_V_ORDER)  # lexicographic, which is the wrong order
    assert shuffled != LS_V_ORDER, "the fixture must actually distinguish the two sorts"
    assert sorted(shuffled, key=sqlplan.natural_key) == LS_V_ORDER


def test_natural_key_matches_ls_v_on_the_awkward_names() -> None:
    assert sorted(LS_V_AWKWARD) != LS_V_AWKWARD
    assert sorted(reversed(LS_V_AWKWARD), key=sqlplan.natural_key) == LS_V_AWKWARD


def test_natural_key_orders_dated_release_names_the_way_ls_v_does() -> None:
    """`2024_01_15_02_world.sql` - the naming a plain sort gets wrong the moment a
    field loses its leading zero. Captured from `ls -v` like the other two lists."""
    assert sorted(LS_V_DATED) != LS_V_DATED
    assert sorted(reversed(LS_V_DATED), key=sqlplan.natural_key) == LS_V_DATED


def test_natural_key_ignores_leading_zeros_and_then_compares_the_rest() -> None:
    # `01` == `1`, so `_a` vs `_b` decides - exactly what `ls -v` prints.
    assert sorted(["z01_b.sql", "z1_a.sql"], key=sqlplan.natural_key) == ["z1_a.sql", "z01_b.sql"]
    assert sorted(["a.sql", "a~.sql", "a1.sql"], key=sqlplan.natural_key) == [
        "a~.sql",
        "a.sql",
        "a1.sql",
    ]
    # Two names `ls -v` cannot tell apart are still ordered deterministically, by the raw name.
    assert sorted(["z01.sql", "z1.sql"], key=sqlplan.natural_key) == ["z01.sql", "z1.sql"]


def test_natural_key_breaks_a_tie_between_cut_names_the_way_ls_v_does() -> None:
    """Two names can cut to the same thing, or to the same VERSION, and `ls -v` has a
    different answer for each. `w.b9.sql` before `w.b10.sql` needs the whole-name
    version pass; `z0001_mangos.y.sql` before `z1_mangos.sql` needs the raw bytes,
    because `0001` and `1` are one version and C then falls through to `strcmp`."""
    assert sorted(LS_V_TIES) != LS_V_TIES
    assert sorted(reversed(LS_V_TIES), key=sqlplan.natural_key) == LS_V_TIES


def test_natural_key_cuts_the_file_suffix_the_way_the_C_scanner_does() -> None:
    """A doubled dot is where coreutils' documented regex and its actual code part.

    `match_suffix()` makes one left-to-right pass: the `.` in `aa..sql.gz` arrives while
    the previous `.` is still waiting for a letter, which clears the candidate AND
    consumes it, so the cut lands on `.gz` and the compared name is `aa..sql` - not
    `aa.` as `(\\.[A-Za-z~][A-Za-z0-9~]*)*$` would have it. `ls -v` follows the code, so
    these five names are the only fixture that can tell the two implementations apart.
    """
    order = ["aa.b.sql", "aa.sql", "aa.90.sql", "aa..sql.gz", "aa..y.sql"]
    assert sorted(order) != order
    assert sorted(reversed(order), key=sqlplan.natural_key) == order


def test_natural_key_stops_cutting_at_a_character_no_suffix_may_contain() -> None:
    """`match_suffix()`'s third arm, which no other name in this module exercises.

    A character that can never appear inside a suffix (`-`, and every punctuation mark but
    `.`) also clears the pending candidate, so `a.b-c.sql` cuts to `a.b-c` while `a.b.sql`
    cuts all the way back to `a`. Drop that arm and both cut to `a`, the cut pass ties, and
    each pair below comes out reversed - which is the whole reason the cut exists. Both
    were captured from `ls -v`; the second is the shape a hand-edited cmangos update takes.
    """
    order = ["a.b.sql", "a.b-c.sql"]
    assert sorted(order) != order
    assert sorted(reversed(order), key=sqlplan.natural_key) == order
    realistic = ["z1_mangos.v2.sql", "z1_mangos.v2-fix.sql"]
    assert sorted(realistic) != realistic
    assert sorted(reversed(realistic), key=sqlplan.natural_key) == realistic


def test_natural_key_puts_a_dotfile_before_every_name_that_has_no_leading_dot() -> None:
    """`Path.glob('*.sql')` returns `.gitignore` (unlike the `glob` module), so the key
    has to have an opinion about it, and C's is unconditional: a leading dot wins before
    anything else is looked at.

    `~lock.sql` is the neighbour that makes this test mean something. Against ordinary
    letters a dotfile sorts first anyway - it cuts to an EMPTY name, which beats every
    letter - so `[".a.sql", "a.sql", "b.sql"]` passes with the dot rule deleted. `~` is
    the one character that ranks below an empty name, so only this ordering proves the
    rule is there.
    """
    assert sorted(LS_V_DOTS) != LS_V_DOTS
    assert sorted(reversed(LS_V_DOTS), key=sqlplan.natural_key) == LS_V_DOTS


def test_natural_key_is_a_total_order_over_every_captured_name() -> None:
    """No pair of keys may raise, and the sort must not depend on the input order.

    A key built out of alternating tuples and ints compares fine only while both
    keys keep the same shape; a key that ever puts an `int` where another puts a
    `tuple` raises `TypeError` on a comparison that the small fixtures above may
    never make. Sorting every rotation of the whole corpus makes them all.
    """
    corpus = sorted(set(LS_V_ORDER + LS_V_AWKWARD + LS_V_DATED + LS_V_TIES + LS_V_DOTS))
    expected = sorted(corpus, key=sqlplan.natural_key)
    for cut in range(len(corpus)):
        rotated = corpus[cut:] + corpus[:cut]
        assert sorted(rotated, key=sqlplan.natural_key) == expected


# -- expand: order is the product ---------------------------------------------


def test_expand_globs_per_pattern_in_natural_order(tmp_path: Path) -> None:
    _touch(tmp_path / "src" / "tbc-db" / "Updates", *LS_V_ORDER)
    phase = SqlPhase(
        name="content updates",
        into="mangos",
        files=("src/tbc-db/Updates/*.sql",),
        on_error="warn",
    )
    runs = sqlplan.expand(_plan(phase), tmp_path, SCHEMAS, TOKENS)
    assert [r.path.name for r in runs if r.path is not None] == LS_V_ORDER
    assert (
        runs[0].rel == "src/tbc-db/Updates/z9_01_mangos_c.sql"
    ), "posix, relative to the server dir"
    assert {r.schema for r in runs} == {"mangos"}
    assert {r.phase for r in runs} == {phase}
    assert all(r.statement is None and r.gzip is False for r in runs)


def test_expand_keeps_glob_order_across_patterns_and_sorts_each(tmp_path: Path) -> None:
    _touch(tmp_path / "dbc" / "original_data", "b10.sql", "b9.sql")
    _touch(tmp_path / "dbc" / "cmangos_fixes", "a2.sql", "a1.sql")
    phase = SqlPhase(
        name="dbc data",
        into="mangos",
        files=("dbc/original_data/*.sql", "dbc/cmangos_fixes/*.sql"),
        on_error="warn",
    )
    runs = sqlplan.expand(_plan(phase), tmp_path, SCHEMAS, TOKENS)
    assert [r.path.name for r in runs if r.path is not None] == [
        "b9.sql",
        "b10.sql",
        "a1.sql",
        "a2.sql",
    ]


def test_expand_runs_the_phases_in_the_order_the_plan_lists_them(tmp_path: Path) -> None:
    """The base schema must exist before the content that references it. Nothing in a
    per-phase assertion catches a loop that iterates `plan.phases` the other way."""
    for name in ("base", "content", "hotfix"):
        _touch(tmp_path / name, "a.sql")
    phases = tuple(
        SqlPhase(name=name, into="mangos", files=(f"{name}/*.sql",))
        for name in ("base", "content", "hotfix")
    )
    runs = sqlplan.expand(_plan(*phases), tmp_path, SCHEMAS, TOKENS)
    assert [r.rel for r in runs] == ["base/a.sql", "content/a.sql", "hotfix/a.sql"]


def test_expand_name_sort_is_plain(tmp_path: Path) -> None:
    _touch(tmp_path / "acid", "z10.sql", "z9.sql")
    phase = SqlPhase(name="ACID", into="mangos", files=("acid/*.sql",), sort="name")
    runs = sqlplan.expand(_plan(phase), tmp_path, SCHEMAS, TOKENS)
    assert [r.path.name for r in runs if r.path is not None] == ["z10.sql", "z9.sql"]


def test_expand_into_each_routes_every_glob_to_its_schema(tmp_path: Path) -> None:
    for schema in ("mangos", "realmd"):
        _touch(tmp_path / "updates" / schema, "z1.sql")
    phase = SqlPhase(
        name="core updates",
        into_each={
            "realmd": "updates/realmd/*.sql",
            "mangos": "updates/mangos/*.sql",
            "logs": "updates/logs/*.sql",
        },
        on_error="warn",
    )
    runs = sqlplan.expand(_plan(phase), tmp_path, SCHEMAS, TOKENS)
    assert [(r.schema, r.path.name) for r in runs if r.path is not None] == [
        ("realmd", "z1.sql"),
        ("mangos", "z1.sql"),
    ]


def test_expand_gzip_flag_and_statements(tmp_path: Path) -> None:
    full = tmp_path / "Full_DB"
    full.mkdir()
    (full / "TBCDB_1.9.0.sql.gz").write_bytes(b"")
    world = SqlPhase(
        name="world content", into="mangos", files=("Full_DB/TBCDB_*.sql.gz",), gzip=True
    )
    hotfix = SqlPhase(
        name="expansion unlock", into="realmd", statements=("UPDATE account SET expansion = 1",)
    )
    runs = sqlplan.expand(_plan(world, hotfix), tmp_path, SCHEMAS, TOKENS)
    assert runs[0].gzip is True and runs[0].path == full / "TBCDB_1.9.0.sql.gz"
    assert runs[0].rel == "Full_DB/TBCDB_1.9.0.sql.gz"
    assert runs[1] == sqlplan.PhaseRun(
        hotfix, "realmd", None, "UPDATE account SET expansion = 1", False, "statement 1"
    )


def test_expand_fills_statement_tokens_and_refuses_an_unknown_one(tmp_path: Path) -> None:
    realm = SqlPhase(
        name="realm row",
        into="realmd",
        statements=(
            "INSERT INTO realmlist (name, address, port) VALUES "
            "('Yulon', '{{REALM_HOST}}', {{WORLD_PORT}})",
            "UPDATE realmlist SET realmbuilds = '{{CLIENT_BUILD}}'",
        ),
    )
    runs = sqlplan.expand(_plan(realm), tmp_path, SCHEMAS, TOKENS)
    assert [r.statement for r in runs] == [
        "INSERT INTO realmlist (name, address, port) VALUES ('Yulon', '127.0.0.1', 8085)",
        "UPDATE realmlist SET realmbuilds = '8606'",
    ]
    assert [r.rel for r in runs] == ["statement 1", "statement 2"]
    bad = SqlPhase(
        name="grants", into="realmd", statements=("GRANT ALL ON *.* TO '{{DB_USER}}'@'%'",)
    )
    with pytest.raises(InstallerError, match=r"grants.*DB_USER"):
        sqlplan.expand(_plan(bad), tmp_path, SCHEMAS, TOKENS)


def test_expand_never_substitutes_anything_into_a_file(tmp_path: Path) -> None:
    """A10: files are streamed as they are. A dump holding `{{` is a dump, not a
    template - and `fill()` would refuse it, turning a valid install into an error."""
    directory = tmp_path / "Updates"
    directory.mkdir()
    dump = directory / "z1_{{REALM_HOST}}.sql"
    dump.write_text("INSERT INTO x VALUES ('{{REALM_HOST}}');\n", encoding="utf-8")
    phase = SqlPhase(name="content updates", into="mangos", files=("Updates/*.sql",))
    (run,) = sqlplan.expand(_plan(phase), tmp_path, SCHEMAS, TOKENS)
    assert run.path == dump
    assert run.rel == "Updates/z1_{{REALM_HOST}}.sql"
    assert run.statement is None
    assert dump.read_text(encoding="utf-8") == "INSERT INTO x VALUES ('{{REALM_HOST}}');\n"


def test_expand_does_not_open_the_files_it_lists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`expand()` is pure listing. Whether a file can be READ is `apply()`'s answer,
    given per file and by name; opening every dump here would double the I/O and
    still be stale by the time the stream starts."""
    _touch(tmp_path / "Updates", "z1.sql")

    def refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError("expand() opened a file")

    monkeypatch.setattr(Path, "open", refuse)
    monkeypatch.setattr(Path, "read_bytes", refuse)
    monkeypatch.setattr(Path, "read_text", refuse)
    phase = SqlPhase(name="content updates", into="mangos", files=("Updates/*.sql",))
    (run,) = sqlplan.expand(_plan(phase), tmp_path, SCHEMAS, TOKENS)
    assert run.rel == "Updates/z1.sql"


def test_expand_schema_less_phase(tmp_path: Path) -> None:
    (tmp_path / "create_databases.sql").write_text("", encoding="utf-8")
    phase = SqlPhase(name="create", files=("create_databases.sql",))
    (run,) = sqlplan.expand(_plan(phase), tmp_path, SCHEMAS, TOKENS)
    assert run.schema is None
    assert run.rel == "create_databases.sql"


# -- the three answers a glob can give ----------------------------------------


def test_expand_refuses_an_empty_fail_phase_and_skips_an_empty_warn_phase(tmp_path: Path) -> None:
    """Answer one: the directory was looked at, and nothing matched."""
    fail = SqlPhase(name="realmd base", into="realmd", files=("missing/*.sql",))
    with pytest.raises(InstallerError, match="realmd base"):
        sqlplan.expand(_plan(fail), tmp_path, SCHEMAS, TOKENS)
    warn = SqlPhase(name="ACID", into="mangos", files=("missing/*.sql",), on_error="warn")
    assert sqlplan.expand(_plan(warn), tmp_path, SCHEMAS, TOKENS) == ()


def test_an_empty_warn_phase_says_which_pattern_matched_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    warn = SqlPhase(name="ACID", into="mangos", files=("acid/*.sql",), on_error="warn")
    with caplog.at_level(logging.WARNING, logger="yulon.catalog.families.sqlplan"):
        assert sqlplan.expand(_plan(warn), tmp_path, SCHEMAS, TOKENS) == ()
    assert "acid/*.sql" in caplog.text and "ACID" in caplog.text


def test_a_directory_that_cannot_be_listed_is_not_reported_as_nothing_matched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Answer two, the one `Path.glob` cannot give.

    `glob` swallows the `OSError` and hands back a short list, so an unlistable
    directory reads as "nothing matched" - which a `warn` phase then SKIPS. The
    phase policy is about the sources being incomplete; it was never a licence
    to ignore an error from the operating system, so this refuses either way.
    """
    _touch(tmp_path / "Updates", "z1.sql")
    real = Path.iterdir

    def blocked(self: Path) -> object:
        if self.name == "Updates":
            raise PermissionError(13, "Permission denied")
        return real(self)

    monkeypatch.setattr(Path, "iterdir", blocked)
    warn = SqlPhase(
        name="content updates", into="mangos", files=("Updates/*.sql",), on_error="warn"
    )
    with pytest.raises(InstallerError, match=r"could not be read|could not be listed"):
        sqlplan.expand(_plan(warn), tmp_path, SCHEMAS, TOKENS)
    # And the message must name the folder and what the OS actually said.
    with pytest.raises(InstallerError, match=r"Updates.*Permission denied"):
        sqlplan.expand(_plan(warn), tmp_path, SCHEMAS, TOKENS)


def test_a_file_where_a_directory_belongs_is_not_reported_as_nothing_matched(
    tmp_path: Path,
) -> None:
    """The same second answer, with no fake anywhere: `iterdir()` on a regular file
    raises `NotADirectoryError` on both Windows and Linux. A half-unpacked source
    tree looks exactly like this, and `warn` must not swallow it."""
    (tmp_path / "Updates").write_text("not a directory", encoding="utf-8")
    warn = SqlPhase(
        name="content updates", into="mangos", files=("Updates/*.sql",), on_error="warn"
    )
    with pytest.raises(InstallerError) as caught:
        sqlplan.expand(_plan(warn), tmp_path, SCHEMAS, TOKENS)
    assert "Updates" in str(caught.value)
    assert "no file matching" not in str(caught.value), "that would be the wrong answer"


def test_a_missing_directory_is_nothing_matched_and_not_a_failure_to_look(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The two answers must not collapse into each other in EITHER direction. A source
    the plan lists optionally (playerbots SQL a core may not ship) is simply absent."""
    warn = SqlPhase(name="playerbots world", into="mangos", files=("bots/*.sql",), on_error="warn")
    with caplog.at_level(logging.WARNING, logger="yulon.catalog.families.sqlplan"):
        assert sqlplan.expand(_plan(warn), tmp_path, SCHEMAS, TOKENS) == ()
    assert "could not" not in caplog.text.lower()


def test_an_entry_that_cannot_be_examined_is_refused_rather_than_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Path.is_file()` swallows every `OSError` and answers False, so one unstattable
    entry would silently shorten the list - the same two-answer bug one level down."""
    _touch(tmp_path / "Updates", "z1.sql", "z2.sql")
    real = Path.stat

    def blocked(self: Path, **kwargs: object) -> object:
        if self.name == "z2.sql":
            raise PermissionError(13, "Permission denied")
        return real(self, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "stat", blocked)
    warn = SqlPhase(
        name="content updates", into="mangos", files=("Updates/*.sql",), on_error="warn"
    )
    with pytest.raises(InstallerError, match=r"z2\.sql"):
        sqlplan.expand(_plan(warn), tmp_path, SCHEMAS, TOKENS)


def test_a_directory_whose_name_matches_the_glob_is_not_a_file_to_apply(tmp_path: Path) -> None:
    updates = _touch(tmp_path / "Updates", "z1.sql")
    (updates / "archive.sql").mkdir()
    phase = SqlPhase(name="content updates", into="mangos", files=("Updates/*.sql",))
    runs = sqlplan.expand(_plan(phase), tmp_path, SCHEMAS, TOKENS)
    assert [r.rel for r in runs] == ["Updates/z1.sql"]


# -- what a pattern is allowed to be ------------------------------------------


def test_expand_refuses_an_unknown_schema_and_an_escaping_glob(tmp_path: Path) -> None:
    with pytest.raises(InstallerError, match="playerbots"):
        sqlplan.expand(
            _plan(SqlPhase(name="x", into="playerbots", statements=("SELECT 1",))),
            tmp_path,
            SCHEMAS,
            TOKENS,
        )
    with pytest.raises(InstallerError, match=r"\.\./"):
        sqlplan.expand(
            _plan(SqlPhase(name="x", into="mangos", files=("../*.sql",))), tmp_path, SCHEMAS, TOKENS
        )


def test_expand_refuses_an_unmapped_schema_named_by_into_each(tmp_path: Path) -> None:
    phase = SqlPhase(name="core updates", into_each={"playerbots": "updates/pb/*.sql"})
    with pytest.raises(InstallerError, match=r"core updates.*playerbots"):
        sqlplan.expand(_plan(phase), tmp_path, SCHEMAS, TOKENS)


@pytest.mark.parametrize(
    "pattern",
    [
        "/etc/cron.d/x.sql",
        "//host/share/x.sql",
        "C:/Windows/x.sql",
        "C:\\Windows\\x.sql",
        "src\\tbc-db\\Updates\\*.sql",
        "src/tbc-db/../../*.sql",
    ],
)
def test_expand_refuses_a_glob_that_leaves_the_server_folder(tmp_path: Path, pattern: str) -> None:
    """`Path('/etc/x').is_absolute()` is FALSE on Windows - it has no drive - and
    `Path('C:/srv') / '/etc/x'` is `C:/etc/x`. A rooted posix pattern therefore
    escapes the server dir on exactly the platform whose check let it through, so
    the pattern is judged as the posix string it is, on every platform.

    The phase is `warn` on purpose. Under `fail`, a pattern that was NOT recognised as
    an escape still raises - it points at a folder that does not exist, so the empty-glob
    rule refuses it, with the pattern in the message. That refusal is a different rule
    answering a question this test did not ask; `warn` makes it return `()` instead, so
    only the escape check can produce an error here, and the wording is pinned too.
    """
    phase = SqlPhase(name="x", into="mangos", files=(pattern,), on_error="warn")
    with pytest.raises(InstallerError) as caught:
        sqlplan.expand(_plan(phase), tmp_path, SCHEMAS, TOKENS)
    assert pattern in str(caught.value)
    assert "not a plain path inside the server folder" in str(caught.value)


def test_expand_refuses_a_wildcard_in_a_directory_component(tmp_path: Path) -> None:
    """One pattern lists exactly one directory - that is what makes "could not look"
    an answer it can give. A wildcard above the filename would need a walk, and a
    walk over an unreadable branch is where the two answers merge again. No plan
    needs one, so it is refused loudly instead of being half-supported.

    `warn` again, and for the same reason: under `fail` a pattern that slipped through
    the check would still raise, because `src/*/Updates` is not a folder that exists and
    the empty-glob rule refuses that. Only `warn` makes the two answers distinguishable.
    """
    _touch(tmp_path / "src" / "tbc-db" / "Updates", "z1.sql")
    phase = SqlPhase(name="x", into="mangos", files=("src/*/Updates/*.sql",), on_error="warn")
    with pytest.raises(InstallerError) as caught:
        sqlplan.expand(_plan(phase), tmp_path, SCHEMAS, TOKENS)
    assert "src/*/Updates/*.sql" in str(caught.value)
    assert "wildcards a folder name" in str(caught.value)


# -- the schema map covers the WHOLE plan, not just the phases ----------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("create", ("mangos", "playerbots")),
        ("marker_db", "playerbots"),
        ("verify", ({"db": "playerbots", "query": "SELECT COUNT(*) FROM x", "min": 1},)),
        ("player_data", ({"db": "playerbots", "table": "account"},)),
    ],
)
def test_expand_refuses_a_plan_that_names_a_schema_outside_the_map(
    tmp_path: Path, field: str, value: object
) -> None:
    """`create`, `marker_db`, `verify.db` and `player_data.db` are read by J.5/J.6 with
    no map in reach. If one of them names a database this game does not have, the
    place to say so is here - before a single file is streamed - not three stages
    later against a schema that was never created."""
    _touch(tmp_path / "base", "realmd.sql")
    fields: dict[str, object] = {
        "create": ("mangos",),
        "phases": (SqlPhase(name="realmd base", into="realmd", files=("base/*.sql",)),),
        "marker_db": "mangos",
    }
    fields[field] = value
    plan = SqlPlan.model_validate(fields)
    with pytest.raises(InstallerError, match="playerbots"):
        sqlplan.expand(plan, tmp_path, SCHEMAS, TOKENS)


@pytest.mark.parametrize("game", ["wow-tbc", "wow-vanilla", "wow-tortoise"])
def test_every_shipped_cmangos_plan_only_names_its_own_databases(tmp_path: Path, game: str) -> None:
    """The identity map `cmangos.py:_schemas()` will build (A10), against the real
    plans. A `catalog.json` edit that renames a database in one block and not the
    other fails here rather than at install time on somebody's machine."""
    entry: CatalogEntry = load_catalog().get(game)
    native = entry.install.native
    assert native is not None and native.cmangos is not None
    databases = entry.databases
    assert databases is not None
    schemas = {
        name: name
        for name in (databases.auth, databases.characters, databases.world, *databases.extra)
    }
    plan = native.cmangos.sql
    mentioned = {*plan.create, plan.marker_db}
    mentioned.update(rule.db for rule in plan.verify)
    mentioned.update(table.db for table in plan.player_data)
    for phase in plan.phases:
        mentioned.update(phase.into_each or {})
        if phase.into:
            mentioned.add(phase.into)
    assert mentioned <= set(schemas), f"{game}: {sorted(mentioned - set(schemas))}"
    # And `expand()` must agree. Nothing is on disk, so the first `fail` phase
    # refuses - but only AFTER the schema names have been judged, which is the
    # assertion: the refusal must not be about a database name.
    assert plan.phases[0].on_error == "fail"
    with pytest.raises(InstallerError) as caught:
        sqlplan.expand(plan, tmp_path, schemas, _shipped_tokens())
    assert "is not one of this game's databases" not in str(caught.value)


def _shipped_tokens() -> dict[str, str]:
    """Enough of `CmangosInstaller._tokens()` for the statements the shipped plans hold."""
    return {
        "REALM_HOST": "127.0.0.1",
        "WORLD_PORT": "8085",
        "AUTH_PORT": "3724",
        "CLIENT_BUILD": "8606",
        "DB_USER": "mangos",
        "DB_PASSWORD": "hunter2",
        "DB_HOST": "db",
        "AUTH_DB": "realmd",
        "WORLD_DB": "mangos",
        "CHAR_DB": "characters",
        "LOGS_DB": "logs",
    }


# -- a realistic tree, in the order it would really be applied ----------------

# Illustrative content for the shipped wow-tbc plan. The DIRECTORIES are the real
# globs out of `catalog.json`; the file names carry the real spellings - cmangos'
# `z<rev>_<seq>_mangos_<desc>.sql` updates, tbc-db's `TBCDB_<version>.sql.gz` full
# dump, ACID's `acid_tbc.sql` - and every per-directory order below was captured
# from `ls -v` over exactly these names.
TBC_TREE: dict[str, tuple[str, ...]] = {
    "src/mangos-tbc/sql/base": ("realmd.sql", "characters.sql", "logs.sql"),
    "src/tbc-db/Full_DB": ("TBCDB_1.13.1.sql.gz",),
    "src/tbc-db/Updates": (
        "z2817_01_mangos_9330_gameobject.sql",
        "z9_01_mangos_2000_areatrigger.sql",
        "z2818_01_mangos_9340_item_template.sql",
        "z2799_01_mangos_9316_spell_template.sql",
        "z2817_10_mangos_9339_npc_text.sql",
        "z10_01_mangos_2001_creature.sql",
        "z2817_02_mangos_9331_quest_template.sql",
        "z2800_01_mangos_9317_creature_template.sql",
    ),
    "src/tbc-db/ACID": ("acid_tbc.sql",),
    "src/mangos-tbc/sql/base/dbc/original_data": (
        "spell_dbc.sql",
        "areatrigger_template.sql",
        "taxi_nodes.sql",
        "item_template.sql",
    ),
    "src/mangos-tbc/sql/base/dbc/cmangos_fixes": ("spell_dbc.sql",),
    "src/mangos-tbc/sql/updates/mangos": (
        "z2819_10_mangos_loot.sql",
        "z2817_01_mangos_spell_template.sql",
        "z2820_01_mangos_areatrigger.sql",
        "z2818_01_mangos_creature_template.sql",
        "z2819_02_mangos_quest.sql",
    ),
    "src/mangos-tbc/sql/updates/realmd": ("z2801_01_realmd_account.sql",),
    "src/mangos-tbc/sql/updates/characters": ("z2802_01_characters_pet.sql",),
    "src/mangos-tbc/src/modules/Bots/sql/characters": (
        "playerbots_characters.sql",
        "ai_playerbot_names.sql",
    ),
    "src/mangos-tbc/src/modules/Bots/sql/world": (
        "playerbots_world.sql",
        "ai_playerbot_texts.sql",
    ),
    "src/mangos-tbc/src/modules/Bots/sql/world/tbc": ("ai_playerbot_tbc.sql",),
}

# `sql/updates/logs` is deliberately absent: cmangos does not always ship one, the
# phase is `warn`, and its absence must be a skip and not a stop.
TBC_EXPECTED: list[tuple[str | None, str]] = [
    ("realmd", "src/mangos-tbc/sql/base/realmd.sql"),
    ("characters", "src/mangos-tbc/sql/base/characters.sql"),
    ("logs", "src/mangos-tbc/sql/base/logs.sql"),
    ("mangos", "src/tbc-db/Full_DB/TBCDB_1.13.1.sql.gz"),
    ("mangos", "src/tbc-db/Updates/z9_01_mangos_2000_areatrigger.sql"),
    ("mangos", "src/tbc-db/Updates/z10_01_mangos_2001_creature.sql"),
    ("mangos", "src/tbc-db/Updates/z2799_01_mangos_9316_spell_template.sql"),
    ("mangos", "src/tbc-db/Updates/z2800_01_mangos_9317_creature_template.sql"),
    ("mangos", "src/tbc-db/Updates/z2817_01_mangos_9330_gameobject.sql"),
    ("mangos", "src/tbc-db/Updates/z2817_02_mangos_9331_quest_template.sql"),
    ("mangos", "src/tbc-db/Updates/z2817_10_mangos_9339_npc_text.sql"),
    ("mangos", "src/tbc-db/Updates/z2818_01_mangos_9340_item_template.sql"),
    ("mangos", "src/tbc-db/ACID/acid_tbc.sql"),
    ("mangos", "src/mangos-tbc/sql/base/dbc/original_data/areatrigger_template.sql"),
    ("mangos", "src/mangos-tbc/sql/base/dbc/original_data/item_template.sql"),
    ("mangos", "src/mangos-tbc/sql/base/dbc/original_data/spell_dbc.sql"),
    ("mangos", "src/mangos-tbc/sql/base/dbc/original_data/taxi_nodes.sql"),
    ("mangos", "src/mangos-tbc/sql/base/dbc/cmangos_fixes/spell_dbc.sql"),
    ("mangos", "src/mangos-tbc/sql/updates/mangos/z2817_01_mangos_spell_template.sql"),
    ("mangos", "src/mangos-tbc/sql/updates/mangos/z2818_01_mangos_creature_template.sql"),
    ("mangos", "src/mangos-tbc/sql/updates/mangos/z2819_02_mangos_quest.sql"),
    ("mangos", "src/mangos-tbc/sql/updates/mangos/z2819_10_mangos_loot.sql"),
    ("mangos", "src/mangos-tbc/sql/updates/mangos/z2820_01_mangos_areatrigger.sql"),
    ("realmd", "src/mangos-tbc/sql/updates/realmd/z2801_01_realmd_account.sql"),
    ("characters", "src/mangos-tbc/sql/updates/characters/z2802_01_characters_pet.sql"),
    ("mangos", "statement 1"),
    ("characters", "src/mangos-tbc/src/modules/Bots/sql/characters/ai_playerbot_names.sql"),
    ("characters", "src/mangos-tbc/src/modules/Bots/sql/characters/playerbots_characters.sql"),
    ("mangos", "src/mangos-tbc/src/modules/Bots/sql/world/ai_playerbot_texts.sql"),
    ("mangos", "src/mangos-tbc/src/modules/Bots/sql/world/playerbots_world.sql"),
    ("mangos", "src/mangos-tbc/src/modules/Bots/sql/world/tbc/ai_playerbot_tbc.sql"),
    ("realmd", "statement 1"),
    ("realmd", "statement 2"),
]


def test_the_shipped_tbc_plan_expands_over_a_realistic_tree_in_the_applying_order(
    tmp_path: Path,
) -> None:
    """The whole product of this task, end to end, against the real `catalog.json`.

    Every failure this module exists to prevent shows here as a diff and nowhere
    else: `z10` before `z9`, `cmangos_fixes` before `original_data`, the realm row
    written before the base schema, the world dump landing in `realmd`. None of
    them would raise on a real install - the SQL applies, the server starts, and
    the world is wrong a week later.
    """
    for folder, names in TBC_TREE.items():
        _touch(tmp_path.joinpath(*folder.split("/")), *names)
    entry = load_catalog().get("wow-tbc")
    native = entry.install.native
    assert native is not None and native.cmangos is not None
    databases = entry.databases
    assert databases is not None
    schemas = {
        name: name
        for name in (databases.auth, databases.characters, databases.world, *databases.extra)
    }
    runs = sqlplan.expand(native.cmangos.sql, tmp_path, schemas, _shipped_tokens())
    assert [(r.schema, r.rel) for r in runs] == TBC_EXPECTED
    gzipped = [r.rel for r in runs if r.gzip]
    assert gzipped == ["src/tbc-db/Full_DB/TBCDB_1.13.1.sql.gz"]
    assert [r.phase.name for r in runs[:4]] == [
        "realmd base",
        "characters base",
        "logs base",
        "world content",
    ]


# -- the seams J.4-J.6 will call through --------------------------------------


def test_the_marker_table_is_the_one_name_the_probe_and_the_writer_share() -> None:
    assert sqlplan.MARKER_TABLE == "yulon_install"


@pytest.mark.parametrize(
    ("protocol", "real"),
    [(sqlplan.ExecStdin, docker.exec_stdin), (sqlplan.SqlQuery, docker.sql_query)],
)
def test_the_protocols_describe_the_real_docker_seams(protocol: type, real: object) -> None:
    """A Protocol is checked by mypy, which never sees `tests/`. This asserts the same
    thing at runtime: every parameter the protocol names exists on the real function,
    with the same kind and position, so a rename in `docker.py` cannot leave the fakes
    in J.4-J.6 agreeing with a shape nothing implements."""
    expected = list(inspect.signature(protocol.__call__).parameters.values())[1:]  # drop `self`
    actual = inspect.signature(real).parameters  # type: ignore[arg-type]
    assert [p.name for p in expected] == list(actual)[: len(expected)]
    for parameter in expected:
        assert actual[parameter.name].kind == parameter.kind, parameter.name
    for name, parameter in list(actual.items())[len(expected) :]:
        assert parameter.default is not inspect.Parameter.empty, f"{name} has no default"


@pytest.mark.parametrize(
    ("protocol", "real"),
    [(sqlplan.ExecStdin, docker.exec_stdin), (sqlplan.SqlQuery, docker.sql_query)],
)
def test_both_protocols_can_name_the_daemon_they_are_talking_to(
    protocol: type, real: object
) -> None:
    """Neither seam may lose `wsl_distro` on its way through a Protocol.

    Both real functions take it, and both fought for it rather than being
    listed in `test_docker.py`'s `_DAEMON_AGNOSTIC`: a container name means
    nothing to a daemon that does not hold it, so a server living inside a WSL
    distro is `No such container` to Docker Desktop. The two existing
    `docker exec -i -e MYSQL_PWD` call sites in this app
    (`apply.DockerSql._argv()`, `maintenance.DockerMysql._exec()`) both thread
    a distro for exactly that reason.

    A Protocol that omits the parameter is where that gets undone, and quietly:
    `apply()` cannot pass an argument its own seam type does not declare, so
    every import would go to the local daemon with nothing to show it had been
    decided. Defaulted, so a caller with no distro says nothing and a test fake
    need not care - but declared, so a caller WITH one can be obeyed.
    """
    declared = inspect.signature(protocol.__call__).parameters["wsl_distro"]
    actual = inspect.signature(real).parameters["wsl_distro"]  # type: ignore[arg-type]
    assert declared.kind is inspect.Parameter.KEYWORD_ONLY
    assert declared.kind is actual.kind
    assert declared.default is None and actual.default is None


# -- J.4: apply(), the streaming import ---------------------------------------
#
# Three failures stay three failures here, and only one of them is about SQL: a
# statement the client rejected, a database that could not be reached at all,
# and a dump this side could not read. `expand()` already keeps "nothing
# matched" apart from "could not look" one layer up (see the module docstring);
# collapsing them again at the moment the bytes move would undo it.
#
# The third has history. `docker.exec_stdin()` was first written with a single
# `except OSError` around the read, `gzip.BadGzipFile` IS an `OSError`, and a
# corrupt `.sql.gz` therefore fed the client HALF A DUMP - still valid SQL, so
# it exited 0 and every check downstream agreed the import had worked. The
# tests below drive the real `docker._pump()` over real corrupt files rather
# than raising an exception of their own choosing, because "the fake raised
# exactly what its author expected" is how that defect survives a test suite.


class _Exec:
    """`docker.exec_stdin` recorded by field: argv, the bytes actually read, the env.

    `failing` maps a file name (or statement) to the stderr the client would
    print; those calls exit 1. Everything else exits 0.

    `wsl_distro` is recorded, in `distros`, rather than accepted and dropped.
    That distinction is the whole point: `test_docker.py`'s completeness guard
    is an AST walk over the SIGNATURE, so a function that takes the distro and
    forgets it keeps that guard green while a WSL-resident install execs
    against Docker Desktop and is told `No such container`. A fake that
    swallowed the value would reproduce the same blind spot one layer up.
    """

    def __init__(self, failing: dict[str, str] | None = None) -> None:
        self.failing = failing or {}
        self.calls: list[tuple[str, tuple[str, ...], bytes, dict[str, str]]] = []
        self.distros: list[str | None] = []

    def __call__(
        self,
        container: str,
        argv: Sequence[str],
        source: BinaryIO,
        *,
        env: Mapping[str, str],
        wsl_distro: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        data = source.read()
        self.calls.append((container, tuple(argv), data, dict(env)))
        self.distros.append(wsl_distro)
        text = data.decode(errors="replace")
        for key, stderr in self.failing.items():
            if key in text:
                return subprocess.CompletedProcess(list(argv), 1, "", stderr)
        return subprocess.CompletedProcess(list(argv), 0, "", "")


class _Collect:
    """A write-only sink `docker._pump()` may close without losing what it wrote."""

    def __init__(self) -> None:
        self.data = bytearray()
        self.closed = False

    def write(self, chunk: bytes) -> int:
        self.data += chunk
        return len(chunk)

    def close(self) -> None:
        self.closed = True


class _PumpingExec:
    """`exec_stdin` down to the one part that decides a corrupt dump: `docker._pump()`.

    The read failure `apply()` has to recognise is not something a test can
    honestly invent. A truncated `.sql.gz` raises `EOFError`, mangled deflate
    bytes raise `zlib.error`, and something that was never gzip raises
    `gzip.BadGzipFile`; `apply()` names none of the three and should not,
    because `exec_stdin()` normalises every one of them to
    `SourceUnreadableError` on the way past - which is precisely what makes
    catching `(RuntimeError, OSError)` sufficient. So this fake runs the REAL
    pump over a REAL corrupt file and lets it produce the real exception,
    instead of raising the one its author had in mind.

    It then exits 0, because that is what actually happened: half a dump is
    valid SQL and the client is delighted by it.
    """

    def __init__(self) -> None:
        self.sink = _Collect()
        self.calls = 0

    def __call__(
        self,
        container: str,
        argv: Sequence[str],
        source: BinaryIO,
        *,
        env: Mapping[str, str],
        wsl_distro: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.calls += 1
        docker._pump(source, self.sink, container)
        return subprocess.CompletedProcess(list(argv), 0, "", "")


class _Refusing:
    """An `exec_stdin` that reaches no daemon at all, the way a missing CLI does."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    def __call__(
        self,
        container: str,
        argv: Sequence[str],
        source: BinaryIO,
        *,
        env: Mapping[str, str],
        wsl_distro: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.calls += 1
        raise self.error


def _file_run(
    tmp_path: Path, rel: str, text: str, *, phase: SqlPhase, schema: str = "mangos"
) -> sqlplan.PhaseRun:
    """A `PhaseRun` for `<tmp_path>/<rel>` as `expand()` would build it (`rel` posix).

    `newline=""` because the assertions below are about BYTES. `write_text()` translates
    `\\n` to `\\r\\n` on Windows, so without it a test asserting the client received
    `b"SELECT 1;\\n"` fails on the platform this is developed on - and it would be
    asserting the wrong thing anyway: a dump arrives from a clone or a download with
    whatever line endings it has, and `apply()` streams the file's bytes untouched.
    """
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if rel.endswith(".gz"):
        with gzip.open(path, "wb") as fh:
            fh.write(text.encode())
    else:
        path.write_text(text, encoding="utf-8", newline="")
    return sqlplan.PhaseRun(phase, schema, path, None, phase.gzip, rel)


def _run_apply(
    runs: Sequence[sqlplan.PhaseRun],
    exec_stdin: sqlplan.ExecStdin,
    *,
    sink: docker.OutputSink | None = None,
    cancel: threading.Event | None = None,
    wsl_distro: str | None = None,
    client: str = "mysql",
    password: str = "pw",
    container: str = "c",
) -> list[str]:
    """`apply()` drained, with the arguments every test would otherwise repeat."""
    return list(
        sqlplan.apply(
            runs,
            container=container,
            client=client,
            password=password,
            exec_stdin=exec_stdin,
            sink=sink if sink is not None else (lambda _: None),
            cancel=cancel,
            wsl_distro=wsl_distro,
        )
    )


FAIL = SqlPhase(name="realmd base", into="realmd", files=("x.sql",))
WARN = SqlPhase(name="content updates", into="mangos", files=("x.sql",), on_error="warn")
GZ = SqlPhase(name="world content", into="mangos", files=("x.sql.gz",), gzip=True)
GZ_WARN = SqlPhase(
    name="world updates", into="mangos", files=("x.sql.gz",), gzip=True, on_error="warn"
)


def test_apply_streams_each_file_as_root_with_the_password_in_env(tmp_path: Path) -> None:
    ex = _Exec()
    runs = (
        _file_run(
            tmp_path, "realmd.sql", "CREATE TABLE account (id INT);\n", phase=FAIL, schema="realmd"
        ),
        sqlplan.PhaseRun(FAIL, None, None, "SELECT 1", False, "statement 1"),
    )
    sunk: list[str] = []
    lines = list(
        sqlplan.apply(
            runs,
            container="tbc-db",
            client="mariadb",
            password="pw",
            exec_stdin=ex,
            sink=sunk.append,
            cancel=None,
        )
    )
    assert ex.calls == [
        (
            "tbc-db",
            ("mariadb", "-u", "root", "realmd"),
            b"CREATE TABLE account (id INT);\n",
            {"MYSQL_PWD": "pw"},
        ),
        ("tbc-db", ("mariadb", "-u", "root"), b"SELECT 1", {"MYSQL_PWD": "pw"}),
    ]
    assert lines == ["realmd base: realmd.sql -> realmd", "realmd base: statement 1 (no schema)"]
    assert "pw" not in " ".join(lines) and sunk == []


def test_apply_inflates_gzip_on_the_way_in(tmp_path: Path) -> None:
    ex = _Exec()
    run = _file_run(
        tmp_path, "TBCDB_1.9.0.sql.gz", "INSERT INTO item_template VALUES (1);\n", phase=GZ
    )
    _run_apply((run,), ex, client="mariadb")
    assert ex.calls[0][2] == b"INSERT INTO item_template VALUES (1);\n"


def test_apply_fail_policy_stops_naming_the_file_and_the_last_stderr_line(tmp_path: Path) -> None:
    ex = _Exec(failing={"BROKEN": "Warning: x\nERROR 1064 (42000) at line 3: You have an error\n"})
    runs = (
        _file_run(tmp_path, "z1.sql", "BROKEN;\n", phase=FAIL, schema="realmd"),
        _file_run(tmp_path, "z2.sql", "SELECT 2;\n", phase=FAIL, schema="realmd"),
    )
    sunk: list[str] = []
    with pytest.raises(InstallerError, match=r"z1\.sql.*ERROR 1064 \(42000\) at line 3"):
        _run_apply(runs, ex, sink=sunk.append)
    assert len(ex.calls) == 1, "fail stops at the first failing file"
    assert sunk == ["Warning: x", "ERROR 1064 (42000) at line 3: You have an error"]


def test_apply_warn_policy_names_the_relative_path_and_continues(tmp_path: Path) -> None:
    ex = _Exec(failing={"BROKEN": "ERROR 1050: Table already exists\n"})
    runs = (
        _file_run(tmp_path, "src/tbc-db/Updates/z1.sql", "BROKEN;\n", phase=WARN),
        _file_run(tmp_path, "src/tbc-db/Updates/z2.sql", "SELECT 2;\n", phase=WARN),
    )
    lines = _run_apply(runs, ex)
    assert len(ex.calls) == 2
    assert lines == [
        "content updates: src/tbc-db/Updates/z1.sql -> mangos",
        "warning: src/tbc-db/Updates/z1.sql failed (ERROR 1050: Table already exists); "
        "continuing because 'content updates' is on_error: warn",
        "content updates: src/tbc-db/Updates/z2.sql -> mangos",
    ]
    assert str(tmp_path) not in " ".join(lines), "the log names files relative to the server dir"


def test_apply_stops_on_cancel_before_the_next_file(tmp_path: Path) -> None:
    ex = _Exec()
    cancel = threading.Event()
    runs = (
        _file_run(tmp_path, "z1.sql", "SELECT 1;\n", phase=WARN),
        _file_run(tmp_path, "z2.sql", "SELECT 2;\n", phase=WARN),
    )
    gen = sqlplan.apply(
        runs,
        container="c",
        client="mysql",
        password="pw",
        exec_stdin=ex,
        sink=lambda _: None,
        cancel=cancel,
    )
    assert next(gen) == "content updates: z1.sql -> mangos"
    cancel.set()
    with pytest.raises(InstallerError, match="stopped"):
        next(gen)
    assert len(ex.calls) == 1


# -- which daemon the import execs against ------------------------------------


def test_apply_execs_against_the_distro_that_holds_the_container(tmp_path: Path) -> None:
    """The value, not the parameter. Accepting `wsl_distro` and dropping it is invisible.

    `test_docker.py::test_every_function_that_talks_to_docker_can_say_which_daemon`
    parses signatures, so it cannot tell a forwarded distro from a forgotten
    one - proved by mutation in H.5. This asserts the value arrives, for every
    run in the plan, so an import into a WSL-resident install is asked of the
    daemon that actually holds its database.
    """
    ex = _Exec()
    runs = (
        _file_run(tmp_path, "z1.sql", "SELECT 1;\n", phase=WARN),
        sqlplan.PhaseRun(WARN, "mangos", None, "SELECT 2", False, "statement 1"),
    )
    _run_apply(runs, ex, wsl_distro="Ubuntu-22.04")
    assert ex.distros == ["Ubuntu-22.04", "Ubuntu-22.04"]


def test_apply_names_no_distro_when_the_install_is_local(tmp_path: Path) -> None:
    """An install created here has no distro to name, and says so rather than guessing."""
    ex = _Exec()
    run = _file_run(tmp_path, "z1.sql", "SELECT 1;\n", phase=WARN)
    _run_apply((run,), ex)
    assert ex.distros == [None]


# -- the three ways a run can go wrong, kept apart ----------------------------


def test_apply_refuses_a_truncated_dump_the_client_would_have_swallowed(tmp_path: Path) -> None:
    """A half-downloaded `.sql.gz`: `EOFError`, exit 0, and a database missing a third.

    This is the exact shape H.4 was rewritten for. The client is fed however
    many bytes inflated cleanly, that prefix is valid SQL, and it exits 0 - so
    nothing downstream will ever notice. Only the read failure says anything,
    and only if `apply()` hears it.
    """
    # Big enough that the truncated half is several of `docker._pump()`'s 1 MiB reads:
    # the point is that whole chunks of valid SQL reach the client BEFORE the stream
    # gives out, which is what makes the client's exit 0 so convincing.
    path = tmp_path / "TBCDB_1.9.0.sql.gz"
    with gzip.open(path, "wb") as fh:
        fh.write(b"INSERT INTO item_template VALUES (1);\n" * 400_000)
    whole = path.read_bytes()
    path.write_bytes(whole[: len(whole) // 2])
    run = sqlplan.PhaseRun(GZ, "mangos", path, None, True, "TBCDB_1.9.0.sql.gz")
    ex = _PumpingExec()
    with pytest.raises(InstallerError) as excinfo:
        _run_apply((run,), ex)
    assert "TBCDB_1.9.0.sql.gz" in str(excinfo.value)
    assert "could not be read" in str(excinfo.value)
    assert "EOFError" in str(excinfo.value), "which corruption it was is the actionable half"
    assert isinstance(excinfo.value.__cause__, docker.SourceUnreadableError)
    assert isinstance(excinfo.value.__cause__.__cause__, EOFError)
    assert len(ex.sink.data) >= 1 << 20, "megabytes of valid SQL reached the client first"
    assert ex.sink.closed, "the client still gets its EOF, so nothing is left waiting"


def test_apply_refuses_a_dump_that_was_never_gzip_and_says_so_differently(tmp_path: Path) -> None:
    """The other corruption, told apart from the first by `__cause__` and nothing else.

    A truncated download and a file that is not a gzip at all get the same
    sentence from `SourceUnreadableError` unless the cause's class survives
    into what the user is shown. "Download it again" answers one of them.
    """
    path = tmp_path / "TBCDB_1.9.0.sql.gz"
    path.write_bytes(b"<!DOCTYPE html><title>404 Not Found</title>")
    run = sqlplan.PhaseRun(GZ, "mangos", path, None, True, "TBCDB_1.9.0.sql.gz")
    with pytest.raises(InstallerError) as excinfo:
        _run_apply((run,), _PumpingExec())
    assert "BadGzipFile" in str(excinfo.value)
    assert "EOFError" not in str(excinfo.value)


def test_a_warn_phase_does_not_soften_a_dump_that_could_not_be_read(tmp_path: Path) -> None:
    """`on_error: warn` is a policy about SQL, never about the operating system.

    The same rule `expand()` keeps for "could not look": a phase may forgive a
    statement its own sources got wrong, and may not forgive a file this
    machine could not deliver - that is a database silently short by however
    much the broken file held.
    """
    path = tmp_path / "z1.sql.gz"
    path.write_bytes(b"not gzip at all")
    run = sqlplan.PhaseRun(GZ_WARN, "mangos", path, None, True, "z1.sql.gz")
    with pytest.raises(InstallerError):
        _run_apply((run,), _PumpingExec())


def test_apply_names_a_dump_it_could_not_open_without_blaming_the_database(
    tmp_path: Path,
) -> None:
    """The file was gone before a single byte moved: no exec, and no SQL in the story."""
    ex = _Exec()
    run = sqlplan.PhaseRun(FAIL, "realmd", tmp_path / "vanished.sql", None, False, "vanished.sql")
    with pytest.raises(InstallerError) as excinfo:
        _run_apply((run,), ex)
    assert "vanished.sql" in str(excinfo.value)
    assert "could not be read" in str(excinfo.value)
    assert "FileNotFoundError" in str(excinfo.value)
    assert ex.calls == [], "nothing was sent to the database, so nothing may be blamed on it"


def test_a_daemon_that_never_answered_is_not_reported_as_a_broken_dump(tmp_path: Path) -> None:
    """Three situations, three sentences. This is the one no dump and no SQL caused.

    `DockerCliMissingError` is a `DockerCommandError` is a `RuntimeError`, so
    the very clause that catches `SourceUnreadableError` swallows it too unless
    it is answered first - and the user is then told their download is corrupt
    when what is actually missing is Docker.
    """
    ex = _Refusing(docker.DockerCliMissingError("there is no docker CLI on this machine"))
    run = _file_run(tmp_path, "z1.sql", "SELECT 1;\n", phase=FAIL, schema="realmd")
    with pytest.raises(InstallerError) as excinfo:
        _run_apply((run,), ex)
    assert "could not be read" not in str(excinfo.value)
    assert "no docker CLI" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, docker.DockerCliMissingError)


def test_a_bug_in_the_seam_is_not_dressed_up_as_a_corrupt_download(tmp_path: Path) -> None:
    """The clause is `(RuntimeError, OSError)` and not `except Exception`, deliberately.

    `SourceUnreadableError` exists precisely so this catch can be narrow: it is
    the one type every read failure arrives as. Widening to `Exception` would
    also swallow a `TypeError` from a seam whose signature drifted and report
    it to the user as a corrupt dump, which is a bug report nobody can act on
    and a whole afternoon looking at the wrong file.
    """
    ex = _Refusing(ValueError("the seam was called wrongly"))
    run = _file_run(tmp_path, "z1.sql", "SELECT 1;\n", phase=FAIL, schema="realmd")
    with pytest.raises(ValueError, match="called wrongly"):
        _run_apply((run,), ex)


def test_a_container_that_is_not_running_is_not_reported_as_a_broken_dump(
    tmp_path: Path,
) -> None:
    """The daemon answered; it said no. Still not a file this side could not read."""
    ex = _Refusing(docker.DockerCommandError("Error response from daemon: No such container: c"))
    run = _file_run(tmp_path, "z1.sql", "SELECT 1;\n", phase=WARN)
    with pytest.raises(InstallerError) as excinfo:
        _run_apply((run,), ex)
    assert "could not be read" not in str(excinfo.value)
    assert "No such container" in str(excinfo.value)


# -- what the client said, and what is done with it ---------------------------


def test_apply_shows_a_warning_the_client_printed_even_though_it_succeeded(
    tmp_path: Path,
) -> None:
    """`2>/dev/null` made visible: stderr reaches the sink whatever the exit code was.

    Every shipped shell installer discarded the client's stderr, so
    `[Warning] Using a password on the command line` and the deprecation
    notices that precede a broken update went nowhere. Only the failing branch
    needing them would put them back in the dark on the run that still worked.
    """
    sunk: list[str] = []

    class _Noisy(_Exec):
        def __call__(self, *args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            proc = super().__call__(*args, **kwargs)  # type: ignore[arg-type]
            return subprocess.CompletedProcess(proc.args, 0, "", "[Warning] deprecated syntax\n")

    run = _file_run(tmp_path, "z1.sql", "SELECT 1;\n", phase=FAIL, schema="realmd")
    lines = _run_apply((run,), _Noisy(), sink=sunk.append)
    assert sunk == ["[Warning] deprecated syntax"]
    assert lines == ["realmd base: z1.sql -> realmd"], "a warning is not a failure"


def test_apply_falls_back_to_the_exit_code_when_the_client_printed_nothing(
    tmp_path: Path,
) -> None:
    """A client killed by a signal exits 137 with both pipes empty (measured, H.5).

    "z1.sql failed ()" names nothing anybody can act on; the number at least
    says which client died and how.
    """

    class _Silent(_Exec):
        def __call__(self, *args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            proc = super().__call__(*args, **kwargs)  # type: ignore[arg-type]
            return subprocess.CompletedProcess(proc.args, 137, "", "")

    run = _file_run(tmp_path, "z1.sql", "SELECT 1;\n", phase=WARN)
    lines = _run_apply((run,), _Silent())
    assert lines[1] == (
        "warning: z1.sql failed (the client exited 137); continuing because "
        "'content updates' is on_error: warn"
    )


def test_apply_quotes_the_last_thing_the_client_said_not_the_last_line_it_printed(
    tmp_path: Path,
) -> None:
    """mysql ends its stderr with a newline, and `splitlines()` leaves blanks behind it.

    `lines[-1]` would quote the empty string and the reason would collapse into
    the exit-code fallback, hiding the one line that says WHICH statement broke.
    """
    ex = _Exec(failing={"BROKEN": "ERROR 1064 (42000) at line 7: check the manual\n\n   \n"})
    run = _file_run(tmp_path, "z1.sql", "BROKEN;\n", phase=WARN)
    lines = _run_apply((run,), ex)
    assert "ERROR 1064 (42000) at line 7: check the manual" in lines[1]
    assert "exited 1" not in lines[1]


def test_apply_logs_a_forgiven_failure_as_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The install log line is for the user; the log record is for the bug report."""
    ex = _Exec(failing={"BROKEN": "ERROR 1050: Table already exists\n"})
    run = _file_run(tmp_path, "src/Updates/z1.sql", "BROKEN;\n", phase=WARN)
    with caplog.at_level(logging.WARNING, logger=sqlplan.logger.name):
        _run_apply((run,), ex)
    assert "src/Updates/z1.sql" in caplog.text
    assert "ERROR 1050" in caplog.text


# -- the rest of the contract -------------------------------------------------


def test_apply_never_lets_the_password_out_of_the_environment(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """`CREATE USER ... IDENTIFIED BY` is the one statement that carries the secret.

    It goes over stdin and is named in the log by `rel` - `statement 1` - which
    is why `PhaseRun` carries one at all. Describing a run by its SQL would put
    the database password in every install log anybody ever pastes.
    """
    secret = "not-a-real-password-7f3a"
    statement = f"CREATE USER 'mangos'@'%' IDENTIFIED BY '{secret}';"
    ex = _Exec(failing={"CREATE USER": "ERROR 1396 (HY000): Operation CREATE USER failed\n"})
    run = sqlplan.PhaseRun(WARN, "mangos", None, statement, False, "statement 1")
    sunk: list[str] = []
    with caplog.at_level(logging.DEBUG, logger=sqlplan.logger.name):
        lines = _run_apply((run,), ex, sink=sunk.append, password=secret)
    assert ex.calls[0][2] == statement.encode(), "the client still got the real statement"
    assert ex.calls[0][3] == {"MYSQL_PWD": secret}
    assert secret not in " ".join(lines)
    assert secret not in " ".join(sunk)
    assert secret not in caplog.text
    assert secret not in " ".join(ex.calls[0][1])


def test_apply_closes_each_dump_before_it_opens_the_next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stream held open past its run is a file Windows will not let anyone delete.

    The import is followed by stages that move and delete the source tree, and
    a `gzip.GzipFile` nobody closed keeps a handle on a multi-gigabyte dump for
    as long as the installer runs.
    """
    opened: list[BinaryIO] = []
    real = sqlplan._open

    def spy(run: sqlplan.PhaseRun) -> BinaryIO:
        handle = real(run)
        opened.append(handle)
        return handle

    monkeypatch.setattr(sqlplan, "_open", spy)
    runs = (
        _file_run(tmp_path, "z1.sql", "SELECT 1;\n", phase=WARN),
        _file_run(tmp_path, "z2.sql.gz", "SELECT 2;\n", phase=GZ_WARN),
    )
    _run_apply(runs, _Exec())
    assert len(opened) == 2
    assert all(handle.closed for handle in opened)


def test_apply_stops_before_the_first_file_when_cancel_is_already_set(tmp_path: Path) -> None:
    """Stop pressed while the previous stage was still finishing: nothing is applied."""
    ex = _Exec()
    cancel = threading.Event()
    cancel.set()
    run = _file_run(tmp_path, "z1.sql", "SELECT 1;\n", phase=WARN)
    with pytest.raises(InstallerError):
        _run_apply((run,), ex, cancel=cancel)
    assert ex.calls == []


def test_the_cancel_message_says_a_half_written_import_needs_no_undoing(tmp_path: Path) -> None:
    """The note is `native.IMPORT_CANCEL_NOTE`, imported and not restated (A10).

    Cancelling between files leaves exactly the `partial` state
    `MarkerGate.reset()` clears, so the user is told they can simply install
    again - and told it in the one wording every other cancel in the app uses.
    """
    cancel = threading.Event()
    cancel.set()
    run = _file_run(tmp_path, "z1.sql", "SELECT 1;\n", phase=WARN)
    with pytest.raises(InstallerError) as excinfo:
        _run_apply((run,), _Exec(), cancel=cancel)
    assert native.IMPORT_CANCEL_NOTE in str(excinfo.value)


def test_apply_with_nothing_to_do_execs_nothing(tmp_path: Path) -> None:
    ex = _Exec()
    assert _run_apply((), ex) == []
    assert ex.calls == []


def test_the_fail_message_says_the_import_stopped_and_what_was_left_alone(
    tmp_path: Path,
) -> None:
    """A stopped import is recoverable; a half-applied one the user must reason about is not.

    Naming the schema as well as the file is what tells `realmd/z1.sql` from
    the same file applied into `characters` by an `into_each` phase.
    """
    ex = _Exec(failing={"BROKEN": "ERROR 1064 (42000) at line 3: You have an error\n"})
    run = _file_run(tmp_path, "z1.sql", "BROKEN;\n", phase=FAIL, schema="realmd")
    with pytest.raises(InstallerError) as excinfo:
        _run_apply((run,), ex)
    message = str(excinfo.value)
    assert "z1.sql" in message and "realmd" in message
    assert "Nothing after it was applied" in message


def test_a_schemaless_run_says_so_rather_than_naming_a_database(tmp_path: Path) -> None:
    """Tortoise's `create_databases.sql` runs before any schema exists to run it in."""
    ex = _Exec()
    run = sqlplan.PhaseRun(FAIL, None, None, "CREATE DATABASE mangos;", False, "statement 1")
    lines = _run_apply((run,), ex)
    assert lines == ["realmd base: statement 1 (no schema)"]
    assert ex.calls[0][1] == ("mysql", "-u", "root")


def test_apply_yields_the_file_it_is_about_to_run_before_it_runs_it(tmp_path: Path) -> None:
    """The install log is read while the import is running, and a big dump takes minutes.

    Naming a file only once it has finished leaves the user watching a still
    screen through the longest step of the install, which is exactly when they
    conclude it has hung.
    """
    seen: list[str] = []

    class _Watching(_Exec):
        def __call__(self, *args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            seen.append("exec")
            return super().__call__(*args, **kwargs)  # type: ignore[arg-type]

    run = _file_run(tmp_path, "z1.sql", "SELECT 1;\n", phase=WARN)
    gen = sqlplan.apply(
        (run,),
        container="c",
        client="mysql",
        password="pw",
        exec_stdin=_Watching(),
        sink=lambda _: None,
        cancel=None,
    )
    assert next(gen) == "content updates: z1.sql -> mangos"
    assert seen == [], "the line arrives before the minutes of work it describes"
    assert list(gen) == []
    assert seen == ["exec"]


# -- J.5: create_schemas(), verify() and write_marker() -----------------------
#
# Three functions that all talk to the same database as `apply()` does, and the
# first thing every one of them has to get right is WHICH database. A container
# name means nothing to a daemon that does not hold it, so a distro accepted
# and dropped here sends phase 0, the verify probe and the marker to Docker
# Desktop while `apply()` streams the import into WSL. The probe is the one
# that bites: asked of the wrong daemon it reads "nothing imported" for a fully
# populated database and the import stage runs again over a working server. So
# every fake below RECORDS `wsl_distro` and every test asserts it ARRIVED - a
# signature check would pass on a function that takes it and forgets it.
#
# The second thing is that a query has three answers, not two. `sql_query()`
# returns stdout verbatim: no rows is `""` and one row holding the empty string
# is `"\n"`. `.strip()` collapses those two into each other, and
# `int(x) if x else 0` collapses "could not be asked" into "the count is zero"
# - which for a `min: 0` rule (the model allows one) would PASS an unanswerable
# query and let the marker be written over an empty database.

PLAN = SqlPlan(
    create=("mangos", "realmd", "characters", "logs"),
    phases=(SqlPhase(name="realmd base", into="realmd", files=("realmd.sql",)),),
    verify=(VerifyRule(db="mangos", query="SELECT COUNT(*) FROM item_template", min=10000),),
    marker_db="mangos",
)

ITEM_COUNT = "SELECT COUNT(*) FROM item_template"


class _Query:
    """`docker.sql_query` as a table of answers, recording everything it was asked.

    `answers` maps a statement to the client's stdout VERBATIM - the trailing
    newline included, because that is the only thing telling one empty row from
    no rows at all. `error` is raised instead, for the daemon that cannot be
    reached.

    `wsl_distro` goes into `distros` rather than being swallowed, for the reason
    `_Exec` records it: a fake that accepted the value and forgot it would
    reproduce, one layer up, exactly the bug the parameter exists to prevent.
    """

    def __init__(
        self, answers: Mapping[str, str] | None = None, *, error: Exception | None = None
    ) -> None:
        self.answers = dict(answers or {})
        self.error = error
        self.asked: list[tuple[str, str, str, str | None, str]] = []
        self.distros: list[str | None] = []

    def __call__(
        self,
        container: str,
        client: str,
        password: str,
        schema: str | None,
        statement: str,
        *,
        wsl_distro: str | None = None,
    ) -> str:
        self.asked.append((container, client, password, schema, statement))
        self.distros.append(wsl_distro)
        if self.error is not None:
            raise self.error
        return self.answers[statement]


def _verify(
    plan: SqlPlan, query: sqlplan.SqlQuery, *, wsl_distro: str | None = None
) -> tuple[str, ...]:
    """`verify()` with the arguments every test would otherwise repeat."""
    return sqlplan.verify(
        plan,
        container="c",
        client="mysql",
        password="pw",
        sql_query=query,
        wsl_distro=wsl_distro,
    )


def _create(
    exec_stdin: sqlplan.ExecStdin,
    *,
    plan: SqlPlan = PLAN,
    container: str = "c",
    client: str = "mysql",
    password: str = "pw",
    schemas: Mapping[str, str] | None = None,
    user: str = "mangos",
    charset: str = "utf8mb4",
    wsl_distro: str | None = None,
) -> None:
    """`create_schemas()` with this suite's defaults; every argument overridable."""
    sqlplan.create_schemas(
        plan,
        container=container,
        client=client,
        password=password,
        schemas=SCHEMAS if schemas is None else schemas,
        user=user,
        charset=charset,
        exec_stdin=exec_stdin,
        wsl_distro=wsl_distro,
    )


def test_create_schemas_writes_databases_user_grants_and_flush_over_stdin() -> None:
    ex = _Exec()
    _create(ex, container="tbc-db", client="mariadb", password="pw-1")
    container, argv, data, env = ex.calls[0]
    assert (container, argv, env) == ("tbc-db", ("mariadb", "-u", "root"), {"MYSQL_PWD": "pw-1"})
    text = data.decode()
    assert text.splitlines() == [
        "CREATE DATABASE IF NOT EXISTS `mangos` CHARACTER SET utf8mb4;",
        "CREATE DATABASE IF NOT EXISTS `realmd` CHARACTER SET utf8mb4;",
        "CREATE DATABASE IF NOT EXISTS `characters` CHARACTER SET utf8mb4;",
        "CREATE DATABASE IF NOT EXISTS `logs` CHARACTER SET utf8mb4;",
        "CREATE USER IF NOT EXISTS 'mangos'@'%' IDENTIFIED BY 'pw-1';",
        "ALTER USER 'mangos'@'%' IDENTIFIED BY 'pw-1';",
        "GRANT ALL PRIVILEGES ON `mangos`.* TO 'mangos'@'%';",
        "GRANT ALL PRIVILEGES ON `realmd`.* TO 'mangos'@'%';",
        "GRANT ALL PRIVILEGES ON `characters`.* TO 'mangos'@'%';",
        "GRANT ALL PRIVILEGES ON `logs`.* TO 'mangos'@'%';",
        "FLUSH PRIVILEGES;",
    ]


def test_create_schemas_execs_against_the_daemon_that_holds_the_container() -> None:
    """Phase 0 must land on the same daemon `apply()` streams the import into.

    Created on Docker Desktop while the import goes into a WSL distro, the
    databases exist in neither place the user will look.
    """
    ex = _Exec()
    _create(ex, wsl_distro="Ubuntu-24.04")
    assert ex.distros == ["Ubuntu-24.04"]


def test_create_schemas_is_skipped_for_an_empty_create_list() -> None:
    ex = _Exec()
    plan = SqlPlan(create=(), phases=PLAN.phases, marker_db="tw_world")
    _create(ex, plan=plan, schemas={"tw_world": "tw_world"}, user="root")
    assert ex.calls == []


def test_create_schemas_refuses_a_password_that_cannot_be_quoted() -> None:
    ex = _Exec()
    with pytest.raises(InstallerError, match="password"):
        _create(ex, password="a'b")
    assert ex.calls == [], "nothing may be sent before the refusal"


def test_create_schemas_refuses_a_user_name_that_cannot_be_quoted() -> None:
    ex = _Exec()
    with pytest.raises(InstallerError, match="user name"):
        _create(ex, user="a\\b")
    assert ex.calls == []


# No quote in either payload, deliberately. The obvious splice
# (`a<newline>GRANT ... TO 'x'@'%'`) also carries a `'`, so the quote rule
# refuses it and the test passes with the line-break rule deleted - an
# assertion a NEIGHBOURING rule satisfies. These two are refused by the line
# break or by nothing.
@pytest.mark.parametrize("splice", ["a\nGRANT ALL PRIVILEGES ON *.* TO mangos; -- ", "a\rb"])
def test_create_schemas_refuses_a_secret_carrying_a_line_break(splice: str) -> None:
    """A quote is not the only way out of `'...'`; this script is JOINED LINES.

    The client reads a script line by line and ends a statement at `;`. A
    newline inside the password therefore does not have to escape the quotes to
    add statements - it closes the line it is on and writes the next one
    itself, and `IDENTIFIED BY 'a<newline>GRANT ...'` is a grant this installer
    never intended. `\\r` does the same on a client that treats it as a line
    end, and is invisible in a pasted password either way.
    """
    ex = _Exec()
    with pytest.raises(InstallerError, match="password"):
        _create(ex, password=splice)
    with pytest.raises(InstallerError, match="user name"):
        _create(ex, user=splice)
    assert ex.calls == []


def test_create_schemas_refuses_a_charset_that_is_not_a_plain_identifier() -> None:
    """`CHARACTER SET x` is an UNQUOTED splice, so it has no quotes to escape at all.

    `charset` is `DbFacts.charset`, a free-text catalog field with no pattern
    on it, and it lands in the one place in this script where there is nothing
    around the value.
    """
    ex = _Exec()
    with pytest.raises(InstallerError, match="charset"):
        _create(ex, charset="utf8mb4; DROP DATABASE mangos")
    assert ex.calls == []


def test_create_schemas_refuses_an_unknown_database_in_the_plans_own_words() -> None:
    """One refusal, not two: `_check_plan_schemas()` already owns this sentence.

    A second wording for the same catalog error is a sentence that drifts from
    the first one the day either is edited, so this asserts the two are the
    SAME string rather than merely that both complain.
    """
    plan = SqlPlan(create=("mangos", "nowhere"), phases=PLAN.phases, marker_db="mangos")
    with pytest.raises(InstallerError) as fromcreate:
        _create(_Exec(), plan=plan)
    with pytest.raises(InstallerError) as fromexpand:
        sqlplan.expand(plan, Path("."), SCHEMAS, TOKENS)
    assert str(fromcreate.value) == str(fromexpand.value)
    assert "nowhere" in str(fromcreate.value)


def test_create_schemas_raises_when_the_client_fails() -> None:
    ex = _Exec(failing={"CREATE DATABASE": "ERROR 1045 (28000): Access denied\n"})
    with pytest.raises(InstallerError, match="Access denied"):
        _create(ex)


def test_create_schemas_never_quotes_the_password_back_in_its_failure() -> None:
    """The one SQL script in this app that CONTAINS the secret is also the one a
    client can quote back.

    `ERROR 1064 ... near ''s3cret'@'%''` is what a client says about the line
    it could not parse, and that line is `IDENTIFIED BY '<password>'`. The
    message becomes an `InstallerError`, which the installer shows and the user
    pastes into a bug report.
    """
    ex = _Exec(
        failing={"IDENTIFIED BY": "ERROR 1064 (42000) at line 5: syntax error near ''s3cret''\n"}
    )
    with pytest.raises(InstallerError) as excinfo:
        _create(ex, password="s3cret")
    assert "s3cret" not in str(excinfo.value)
    assert "ERROR 1064" in str(excinfo.value), "the client's reason still has to survive"


def test_create_schemas_says_the_import_stopped_when_no_daemon_can_be_reached() -> None:
    """No docker CLI is not a SQL failure, and it must not surface as a raw
    `RuntimeError` out of a stage whose every other error is an `InstallerError`."""
    refusing = _Refusing(docker.DockerCliMissingError("docker is not installed"))
    with pytest.raises(InstallerError) as excinfo:
        _create(refusing)
    assert "docker is not installed" in str(excinfo.value)


def test_verify_returns_nothing_when_every_rule_passes() -> None:
    ok = _Query({ITEM_COUNT: "12345\n"})
    assert _verify(PLAN, ok) == ()
    assert ok.asked == [("c", "mysql", "pw", "mangos", ITEM_COUNT)], "rule.db is the schema"


def test_verify_asks_the_daemon_that_holds_the_container() -> None:
    """The probe decides whether the import runs. Asked of Docker Desktop about a
    container living in a distro, it reads as an empty database and a populated
    server is imported over."""
    ok = _Query({ITEM_COUNT: "12345\n"})
    assert _verify(PLAN, ok, wsl_distro="Ubuntu-24.04") == ()
    assert ok.distros == ["Ubuntu-24.04"]


def test_verify_names_a_failing_rule_with_its_count_and_minimum() -> None:
    (failed,) = _verify(PLAN, _Query({ITEM_COUNT: "17\n"}))
    assert re.search(r"mangos.*item_template.*17.*10000", failed)


def test_verify_calls_a_non_numeric_answer_not_a_count() -> None:
    (failed,) = _verify(PLAN, _Query({ITEM_COUNT: "not a number\n"}))
    assert "item_template" in failed and "not a count" in failed


def test_verify_does_not_read_an_unanswerable_query_as_a_count_of_zero() -> None:
    """Yes, no and could-not-ask are three answers, and `min: 0` is constructible.

    `int(answer) if answer else 0` turns the third into the second, and `0 >= 0`
    then PASSES - so a rule nothing could answer would let `write_marker()`
    record a finished import over an empty database. A COUNT query always
    returns exactly one row, so no rows at all is never a count of zero.
    """
    plan = SqlPlan(
        create=PLAN.create,
        phases=PLAN.phases,
        verify=(VerifyRule(db="mangos", query=ITEM_COUNT, min=0),),
        marker_db="mangos",
    )
    (failed,) = _verify(plan, _Query({ITEM_COUNT: ""}))
    assert "item_template" in failed and "no rows" in failed


def test_verify_tells_one_empty_row_from_no_rows_at_all() -> None:
    """`.strip()` destroys the only thing that separates them.

    Under `--skip-column-names` one row holding the empty string prints `"\\n"`
    and no rows print `""`; stripped, both are `""`. Both are failures, but not
    the SAME failure, and the sentence the user reads is the difference between
    "it answered a blank" and "it answered nothing at all".
    """
    (empty_row,) = _verify(PLAN, _Query({ITEM_COUNT: "\n"}))
    (no_rows,) = _verify(PLAN, _Query({ITEM_COUNT: ""}))
    assert empty_row != no_rows
    assert "no rows" in no_rows and "no rows" not in empty_row


def test_verify_refuses_more_than_one_row_as_a_count() -> None:
    """Two rows back is not a count either - and `splitlines()[0]` reads the first
    of them as if it were."""
    (failed,) = _verify(PLAN, _Query({ITEM_COUNT: "12345\n99\n"}))
    assert "2 rows" in failed and "not a count" in failed


def test_verify_treats_an_unanswerable_query_as_a_failing_rule_and_does_not_raise() -> None:
    down = _Query(
        error=docker.DockerCommandError("ERROR 1146: Table 'mangos.item_template' doesn't exist")
    )
    (failed,) = _verify(PLAN, down)
    assert "item_template" in failed and "doesn't exist" in failed


def test_verify_reports_every_failing_rule_in_order_and_stays_quiet_about_the_rest() -> None:
    """One sentence per FAILING rule, and the passing ones contribute nothing - an
    install that fails one of three checks must not read as three failures."""
    plan = SqlPlan(
        create=PLAN.create,
        phases=PLAN.phases,
        verify=(
            VerifyRule(db="mangos", query="SELECT COUNT(*) FROM a", min=10),
            VerifyRule(db="realmd", query="SELECT COUNT(*) FROM b", min=10),
            VerifyRule(db="characters", query="SELECT COUNT(*) FROM c", min=10),
        ),
        marker_db="mangos",
    )
    query = _Query(
        {
            "SELECT COUNT(*) FROM a": "3\n",
            "SELECT COUNT(*) FROM b": "500\n",
            "SELECT COUNT(*) FROM c": "0\n",
        }
    )
    failed = _verify(plan, query)
    assert len(failed) == 2
    assert "FROM a" in failed[0] and "FROM c" in failed[1]
    assert [schema for (_, _, _, schema, _) in query.asked] == ["mangos", "realmd", "characters"]


def test_verify_over_a_plan_with_no_rules_asks_nothing_and_passes() -> None:
    plan = SqlPlan(create=PLAN.create, phases=PLAN.phases, marker_db="mangos")
    query = _Query()
    assert _verify(plan, query) == ()
    assert query.asked == []


def test_write_marker_creates_the_table_and_records_the_plan_hash() -> None:
    ex = _Exec()
    sqlplan.write_marker(PLAN, container="tbc-db", client="mariadb", password="pw", exec_stdin=ex)
    container, argv, data, env = ex.calls[0]
    assert (container, argv, env) == (
        "tbc-db",
        ("mariadb", "-u", "root", "mangos"),
        {"MYSQL_PWD": "pw"},
    )
    lines = data.decode().splitlines()
    assert lines[0] == (
        "CREATE TABLE IF NOT EXISTS `mangos`.`yulon_install` "
        "(plan_hash CHAR(16) NOT NULL, finished_unix BIGINT NOT NULL);"
    )
    assert re.fullmatch(
        rf"INSERT INTO `mangos`\.`yulon_install` \(plan_hash, finished_unix\) VALUES "
        rf"\('{PLAN.plan_hash()}', \d+\);",
        lines[1],
    )


def test_write_marker_writes_to_the_daemon_that_holds_the_container() -> None:
    """A marker written to the wrong daemon is a probe that reads `partial` forever."""
    ex = _Exec()
    sqlplan.write_marker(
        PLAN,
        container="c",
        client="mysql",
        password="pw",
        exec_stdin=ex,
        wsl_distro="Ubuntu-24.04",
    )
    assert ex.distros == ["Ubuntu-24.04"]


def test_write_marker_raises_when_the_client_fails() -> None:
    ex = _Exec(failing={"CREATE TABLE": "ERROR 1142 (42000): CREATE command denied\n"})
    with pytest.raises(InstallerError, match="CREATE command denied"):
        sqlplan.write_marker(PLAN, container="c", client="mysql", password="pw", exec_stdin=ex)


def test_write_marker_says_the_import_stopped_when_no_daemon_can_be_reached() -> None:
    refusing = _Refusing(docker.DockerCommandError("No such container: c"))
    with pytest.raises(InstallerError, match="No such container"):
        sqlplan.write_marker(
            PLAN, container="c", client="mysql", password="pw", exec_stdin=refusing
        )


def test_marker_hash_is_stable_across_equal_plans_and_changes_with_the_plan() -> None:
    again = SqlPlan(
        create=("mangos", "realmd", "characters", "logs"),
        phases=(SqlPhase(name="realmd base", into="realmd", files=("realmd.sql",)),),
        verify=(VerifyRule(db="mangos", query=ITEM_COUNT, min=10000),),
        marker_db="mangos",
    )
    assert PLAN.plan_hash() == again.plan_hash()
    assert re.fullmatch(r"[0-9a-f]{16}", PLAN.plan_hash())
    changed = SqlPlan(
        create=PLAN.create,
        phases=(SqlPhase(name="realmd base", into="realmd", files=("other.sql",)),),
        verify=PLAN.verify,
        marker_db="mangos",
    )
    assert changed.plan_hash() != PLAN.plan_hash()
