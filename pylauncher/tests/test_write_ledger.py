"""The write ledger and the walk that enforces it, in both directions (8.1a).

Grafted from design C, which the maintainer judge called the single best test
idea in the three designs: an `ast` enumeration of every place this package
writes, checked against a table a person maintains, failing **both** when a
write is not in the table and when a table row names a write that no longer
exists. One direction alone rots — the first lets a new write appear unnoticed,
the second lets the table fill with lines about code that is gone.

Why it lands in 8.1a rather than at the exit box: this is the step that adds the
first new write sites (the log snapshot), and a ledger written after the writes
is a ledger written from the code rather than from the intent.

The walker's own tests come first here, because a walker that misses a call
makes every row below it a false assurance — the failure mode this project has
already paid for once, recorded in `guards-that-prove-declarations`.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.write_sites import find_write_sites, parse_ledger, walk_tree

PACKAGE = Path(__file__).resolve().parents[1] / "yulon"
LEDGER = Path(__file__).resolve().parents[2] / "pyplan" / "write-ledger.md"


def _sites_in(source: str) -> set[str]:
    """Every write site the walker finds in one snippet, as `function::callee`."""
    tree = ast.parse(source)
    return {f"{site.function}::{site.callee}" for site in find_write_sites_in(tree)}


def find_write_sites_in(tree: ast.AST) -> list:
    return walk_tree(tree, module="snippet.py")


# -- the walker ------------------------------------------------------------


def test_a_path_opened_for_writing_is_a_write_even_though_open_reads_too() -> None:
    """`partial.open("wb")` is how `logsnap` writes; a mode-blind walk would miss it."""
    assert _sites_in("def f(p):\n    with p.open('wb') as fh:\n        fh.write(b'x')\n") == {
        "f::open(wb)"
    }


def test_a_path_opened_for_reading_is_not_a_write() -> None:
    assert _sites_in("def f(p):\n    with p.open('rb') as fh:\n        fh.read()\n") == set()


def test_replacing_text_in_a_string_is_not_a_write_to_anything() -> None:
    """`str.replace` outnumbers `os.replace` four to one in this package."""
    assert _sites_in("def f(s):\n    return s.replace('a', 'b')\n") == set()


def test_a_registry_value_set_or_deleted_is_a_write() -> None:
    """T540: the Run value that starts Yu'lon at sign-in is a change to the machine."""
    source = (
        "import winreg\n"
        "def f(k):\n"
        "    winreg.SetValueEx(k, 'a', 0, 1, 'v')\n"
        "def g(k):\n"
        "    winreg.DeleteValue(k, 'a')\n"
        "def h(k):\n"
        "    winreg.QueryValueEx(k, 'a')\n"
    )
    assert _sites_in(source) == {"f::winreg.SetValueEx", "g::winreg.DeleteValue"}


def test_replacing_a_file_is_a_write() -> None:
    assert _sites_in("import os\ndef f(a, b):\n    os.replace(a, b)\n") == {"f::os.replace"}


def test_the_sql_write_seam_is_a_write_and_the_read_seam_is_not() -> None:
    """`query()` is what `dbreads` is allowed to call; the other two are not."""
    source = (
        "def f(sql):\n"
        "    sql.query('characters', 'SELECT 1')\n"
        "    sql.run_statement('auth', 'UPDATE account SET x = 1')\n"
    )
    assert _sites_in(source) == {"f::run_statement"}


def test_renaming_a_path_over_another_is_a_write_even_spelt_as_a_bare_replace() -> None:
    """`tmp.replace(target)` is how `state.py` saves atomically, and it is not `os.replace`.

    Seven of these exist in the package and the first version of this walker
    missed every one of them, because it excluded bare `.replace(` to keep
    `str.replace` out. One positional argument and no keywords is what separates
    them: `str.replace` needs two, and `datetime.replace(tzinfo=…)` passes none.
    """
    assert _sites_in("def f(tmp, target):\n    tmp.replace(target)\n") == {"f::replace"}


def test_a_datetime_replacing_one_of_its_own_fields_is_not_a_write() -> None:
    source = "def f(t, tz):\n    return t.replace(tzinfo=tz)\n"
    assert _sites_in(source) == set()


def test_deleting_is_a_write_because_the_ledger_is_about_what_can_be_lost() -> None:
    source = "import shutil\ndef f(p):\n    p.unlink()\n    shutil.rmtree(p)\n"
    assert _sites_in(source) == {"f::unlink", "f::shutil.rmtree"}


def test_a_file_created_with_os_open_is_a_write_even_though_it_names_no_mode() -> None:
    """The hole this walker had until 2026-09-07, found by its own other direction.

    `save_credential` creates its file with `os.open(...) `+ `os.fdopen` so the
    private mode is in the CREATION rather than in a later chmod. The walker
    knew `os.replace` and `Path.open` and not this, so the write was invisible —
    and what surfaced it was the ledger's second direction: a row I added by
    hand resolved to nothing, which is the check that exists because a table can
    outlive its code. It caught the reverse instead.

    Every `os.open` counts, without inspecting its flags. Over-inclusive is the
    safe direction for a ledger: a read that gets a row costs a line, and a
    write that gets none costs the whole guarantee.
    """
    source = "import os\ndef f(p):\n    return os.open(p, os.O_WRONLY | os.O_CREAT, 0o600)\n"
    assert _sites_in(source) == {"f::os.open"}


def test_a_write_inside_a_nested_function_is_attributed_to_that_function() -> None:
    """Otherwise two writes in one module collapse onto one ledger row."""
    source = "def outer():\n    def inner(p):\n        p.write_text('x')\n    return inner\n"
    assert _sites_in(source) == {"inner::write_text"}


# -- three shapes the walk could not see, widened 2026-09-08 ---------------
#
# A retrospective audit found the ledger claiming completeness over a walk that
# was blind to three call shapes. Each of the three is written here as a snippet
# FIRST, so the walker's answer is the assertion and not a reading of it; each
# corresponds to a live call in the package, named in its docstring, so none of
# them is a hypothetical. The ground before the widening, recorded because a
# guard that was already true proves nothing: `pytest tests/test_write_ledger.py`
# was **18 passed** at `c0513d6d` with all three of these shapes invisible.


def test_writing_to_a_file_descriptor_is_a_write_even_though_it_names_no_path() -> None:
    """`os.write(master, ...)` is how a console command reaches a running world.

    Live in two places at `c0513d6d`: `controller_wow_wotlk/console.py:318` sends
    the command into the worldserver's pty, and `runner.py:647` is the same shape
    for every subprocess this app drives on a pty. It is the FURTHEST-reaching
    write in the package -- what goes down that descriptor is a GM command, and
    `.account set gmlevel`, `.character rename` and `.reset level` all change the
    database on the other side of it -- and the walk could not see it, because a
    file descriptor is an integer and the walker was looking for paths.
    """
    source = "import os\ndef f(fd, payload):\n    os.write(fd, payload)\n"
    assert _sites_in(source) == {"f::os.write"}


def test_an_open_whose_mode_is_computed_is_a_write_because_it_might_be() -> None:
    """`part.open("ab" if resumed else "wb")` -- `platform.py:2220`, the resumable download.

    `_mode_of()` returns `""` for any mode that is not an `ast.Constant`, and the
    caller then asked whether `""` intersects the writing modes. It does not, so
    a computed mode read as "not a write" -- the one direction of that question a
    walker must never guess, since the whole point of an unknown is that it could
    be either. A 74 MB AppImage lands through this call.

    The answer is `open(?)`, deliberately not `open(ab)`: the walker does not know
    which branch runs and the ledger must not print a mode nobody measured.
    """
    source = (
        "def f(p, resumed):\n"
        "    with p.open('ab' if resumed else 'wb') as fh:\n"
        "        fh.write(b'x')\n"
    )
    assert _sites_in(source) == {"f::open(?)"}


def test_a_module_that_opens_a_path_carries_its_mode_second_and_the_walk_knows_which() -> None:
    """`gzip.open(path, "rb")` — `catalog/families/sqlplan.py`, reading a dump.

    Caught by the widening itself, on its first run: teaching `_mode_of()` to
    answer "unknown" turned this READ into a write, because the walker decided
    where the mode sits by asking whether the call is an attribute — true of
    `part.open("wb")`, where the path is the receiver, and equally true of
    `gzip.open(path, "rb")`, where it is the first argument. Position 0 is then
    `run.path`, which is not a constant, which under the new rule is "unknown".

    So the receiver decides, not the syntax: a NAME THAT IS A MODULE takes its
    mode second. Recorded here rather than in a comment because a false positive
    in a ledger is not harmless — a row about a read is a row that teaches the
    next reader the table is approximate.
    """
    source = "import gzip\ndef f(p):\n    return gzip.open(p, 'rb')\n"
    assert _sites_in(source) == set()


def test_a_module_opening_a_path_for_writing_is_still_a_write() -> None:
    """The other direction of the rule above: the exemption is about POSITION, not about gzip."""
    source = "import gzip\ndef f(p):\n    return gzip.open(p, 'wb')\n"
    assert _sites_in(source) == {"f::open(wb)"}


def test_deleting_a_docker_volume_is_a_write_and_the_ledger_is_the_place_it_is_named() -> None:
    """`docker volume rm` -- `docker.py:972`, the most destructive thing Phase 8 added.

    It deletes a database volume: every character on that install, in one command,
    with no undo and nothing on the filesystem to recover from. The walk saw
    nothing at all, because the destruction is not a Python call -- it is an argv
    handed to a subprocess, and the ledger's whole vocabulary was `shutil` and
    `Path`.

    Matched on the ARGV and not on the function that runs it, which is the rule
    this project has already paid for once (`audit-by-argv-not-by-string`): a
    guard keyed to `_docker(` is a guard a rename walks past.
    """
    source = "def f(name):\n    return _docker(['volume', 'rm', name])\n"
    assert _sites_in(source) == {"f::docker volume rm"}


def test_a_docker_delete_behind_a_spread_launcher_is_still_a_write() -> None:
    """`[*launcher, "rm", "-f", name]` -- `git.py`'s clone container, ended on a Stop (T240).

    The docker program comes first and is spread in, because it is a list (a WSL
    distro's docker is `wsl.exe -d <distro> -- docker`). The walk stopped at the
    first element that was not a string and so saw no verb at all. A spread
    AHEAD of the verb is the program and is stepped over; one after the verb has
    started ends the verb as before, so `['volume', *flags, 'rm']` is not read as
    `volume rm`.
    """
    source = "def f(launcher, name):\n    return run([*launcher, 'rm', '-f', name])\n"
    assert _sites_in(source) == {"f::docker rm"}
    reading = "def f(prefix):\n    return run([*prefix, 'compose', 'ls', '--all'])\n"
    assert _sites_in(reading) == set(), "a read behind a launcher is still a read"
    split = "def f(flags, name):\n    return run(['volume', *flags, 'rm', name])\n"
    assert _sites_in(split) == set(), "a spread inside the verb ends it"


def test_writing_the_import_marker_is_a_write_even_though_it_is_not_the_sql_seam() -> None:
    """`sqlplan.write_marker()` -- the row every later press reads as "this import finished".

    The install engine reaches a database through `exec_stdin`, not through the
    `sql` seam, so every SQL write it makes is an argv with SQL on its stdin --
    the third shape the 2026-09-08 audit found this walk blind to, and the one
    the page has recorded in prose rather than in rows since T14. Teaching the
    walk that whole seam is bigger than one ticket, because `apply()` streams
    whatever a plan names.

    This one function is different and was named for it (T19): one spelling of
    one row, and a BUTTON on it -- an adopt press that records a finished
    import on a person's word, for a server this app did not install. A ledger
    saying "every place" while missing the write a user can now ask for by name
    would be missing the most consequential row in the app.

    Catches `write_marker` dropped from `_SQL_WRITE_METHODS`, which takes the
    ledger's only row about a completion marker out with it.
    """
    source = "from x import sqlplan\ndef f(plan):\n    sqlplan.write_marker(plan)\n"
    assert _sites_in(source) == {"f::write_marker"}


def test_reading_the_marker_back_is_not_a_write() -> None:
    """The other direction, for `query()`'s reason: the probe reads this row constantly.

    `MarkerGate.probe()` is `SELECT plan_hash ...` through the read seam, and a
    walk that matched on the marker's noun rather than on the writer's name
    would put every probe in a table about what can be lost.
    """
    source = "def f(gate):\n    return gate.probe()\n"
    assert _sites_in(source) == set()


def test_the_docker_verbs_that_only_make_things_are_not_writes_here() -> None:
    """Named rather than omitted, the way `mkdir` is.

    `run`, `build`, `pull`, `up` and `image tag` all change the machine, and none
    of them can lose anything the user had: the ledger's own vocabulary is what
    can be LOST (`test_deleting_is_a_write_because_the_ledger_is_about_what_can_be_lost`
    above says so in its name). Including them would add a dozen rows about
    `inspect`-adjacent creation and bury the four that matter.
    """
    source = (
        "def f(ref, src, dst):\n"
        "    _docker(['run', '--rm', ref])\n"
        "    _docker(['image', 'tag', src, dst])\n"
        "    _docker(['compose', 'up', '-d'])\n"
    )
    assert _sites_in(source) == set()


def test_a_docker_read_is_not_a_write_even_when_it_shares_a_noun_with_one() -> None:
    """`volume ls`, `volume inspect` and `image inspect` all lead with a destructive noun.

    The verb is the second word, so a match on `volume` alone would put four read
    sites in a table about destruction.
    """
    source = (
        "def f(name):\n"
        "    _docker(['volume', 'ls', '--filter', name])\n"
        "    _docker(['volume', 'inspect', name])\n"
        "    _docker(['image', 'inspect', name])\n"
    )
    assert _sites_in(source) == set()


def test_changing_a_windows_dacl_is_a_write_the_way_a_chmod_is() -> None:
    """`advapi32.SetNamedSecurityInfoW(...)` -- `winacl._apply_dacl`, T151.

    It changes who may read a folder of passwords, and the files already in it,
    which is what `chmod` does on POSIX, and `chmod` has been in this walk's
    vocabulary from the start. Spelt as a `ctypes` call it names no path method
    and no module the walk knew, so without this the one Windows permission
    change in the package would be invisible to its own audit.
    """
    source = (
        "def f(advapi32, p, dacl):\n"
        "    advapi32.SetNamedSecurityInfoW(p, 1, 4, None, None, dacl, None)\n"
    )
    assert _sites_in(source) == {"f::SetNamedSecurityInfoW"}


def test_reading_a_windows_dacl_is_not_a_write() -> None:
    source = (
        "def f(advapi32, p):\n"
        "    advapi32.GetNamedSecurityInfoW(p, 1, 4, None, None, None, None, None)\n"
    )
    assert _sites_in(source) == set()


# -- the ledger, both directions -------------------------------------------


def test_every_write_in_the_package_is_in_the_ledger() -> None:
    """A new write site is a decision; this is where it stops being a silent one."""
    found = {site.key for site in find_write_sites(PACKAGE)}
    listed = {row.site for row in parse_ledger(LEDGER)}
    missing = sorted(found - listed)
    assert not missing, (
        "these writes are not in pyplan/write-ledger.md:\n  "
        + "\n  ".join(missing)
        + "\nAdd a row saying what each writes and whether the world may be running."
    )


def test_every_ledger_row_still_resolves_to_a_write_in_the_package() -> None:
    """The direction that keeps the table honest when code is deleted."""
    found = {site.key for site in find_write_sites(PACKAGE)}
    listed = {row.site for row in parse_ledger(LEDGER)}
    stale = sorted(listed - found)
    assert not stale, "pyplan/write-ledger.md names writes that no longer exist:\n  " + "\n  ".join(
        stale
    )


def test_every_ledger_row_says_what_it_writes_and_whether_the_world_may_be_up() -> None:
    """A row with an empty cell is a row nobody filled in."""
    for row in parse_ledger(LEDGER):
        assert row.writes, f"{row.site} does not say what it writes"
        assert row.running, f"{row.site} does not say whether the world may be running"


def test_dbreads_appears_in_the_ledger_only_if_it_ever_writes() -> None:
    """Owner answer 7: the module that reads `characters` must never write it.

    Asserted over the syntax tree rather than by reading the module, because
    "it does not call that" is exactly the kind of claim that stops being true
    without anybody noticing.
    """
    writes = [site for site in find_write_sites(PACKAGE) if site.module == "dbreads.py"]
    assert writes == [], f"dbreads.py writes: {[s.key for s in writes]}"
