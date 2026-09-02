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

import inspect
import logging
from pathlib import Path

import pytest

from yulon import docker
from yulon.catalog.catalog import CatalogEntry, SqlPhase, SqlPlan, load_catalog
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
