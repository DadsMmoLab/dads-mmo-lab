"""`sqlplan.apply()` against a real mariadb:11 and real dump files (roadmap 7.3, J.4).

Two claims that no fake can settle, because both are about what the DATABASE does:

* A phase's `warn` policy is only worth having if the reason it reports is the
  client's own sentence. `ERROR 1050 (42S01) at line 1: Table 't' already
  exists` is what mariadb actually prints, newline and all, and `_last_line()`
  has to find it in there.
* **A truncated `.sql.gz` really does leave a half-populated database, and the
  client really does exit 0 over it.** That is the defect H.4 was rewritten
  around: the first version of `exec_stdin()` caught `OSError` on the read
  side, `gzip.BadGzipFile` is one, and a corrupt dump therefore fed the client
  a prefix of valid SQL that it accepted without complaint. The gate below
  counts the rows the truncated dump left behind, so "half the data, reported
  as success" is a measurement here rather than a story. Measured against
  mariadb:11 on 2026-09-01: a 2,000-row dump cut in half committed 912 rows,
  and the only thing anywhere that objected was the read.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from tests.integration.conftest import MARIADB_ROOT_PASSWORD
from yulon import docker
from yulon.catalog.catalog import SqlPhase
from yulon.catalog.families import sqlplan
from yulon.catalog.installer import InstallerError

pytestmark = pytest.mark.integration

SCHEMA = "yulon_j4"
_ROW_BYTES = 8000
"""Wide rows so a few thousand of them make a dump several of `_pump()`'s 1 MiB reads."""


def _rows(count: int, start: int = 1) -> bytes:
    payload = "x" * _ROW_BYTES
    return "".join(
        f"INSERT INTO t (n, payload) VALUES ({n}, '{payload}');\n"
        for n in range(start, start + count)
    ).encode("utf-8")


def _run(
    phase: SqlPhase, *, schema: str | None, path: Path | None, rel: str, sql: str = ""
) -> sqlplan.PhaseRun:
    return sqlplan.PhaseRun(phase, schema, path, sql or None, phase.gzip, rel)


def test_apply_imports_real_files_in_order_and_reports_what_the_client_said(
    tmp_path: Path, mariadb_container: str
) -> None:
    """A statement, a gzipped dump and a `warn` phase the real client rejects.

    The warn phase's file recreates a table that the dump already made, which is
    exactly the shape cmangos' own update directories produce against a database
    that is already partly current - and the shape the shell installers hid with
    `2>/dev/null`.
    """
    dump = tmp_path / "TBCDB.sql.gz"
    with gzip.open(dump, "wb") as fh:
        fh.write(b"CREATE TABLE t (n INT PRIMARY KEY, payload TEXT);\n")
        fh.write(_rows(200))
    clash = tmp_path / "Updates" / "z1_clash.sql"
    clash.parent.mkdir()
    clash.write_bytes(b"CREATE TABLE t (n INT);\n")
    good = tmp_path / "Updates" / "z2_more.sql"
    good.write_bytes(_rows(50, start=1000))

    create = SqlPhase(name="databases", statements=(f"CREATE DATABASE {SCHEMA};",))
    base = SqlPhase(name="world base", into=SCHEMA, files=("*.sql.gz",), gzip=True)
    updates = SqlPhase(name="world updates", into=SCHEMA, files=("Updates/*.sql",), on_error="warn")
    runs = (
        _run(create, schema=None, path=None, rel="statement 1", sql=f"CREATE DATABASE {SCHEMA};"),
        _run(base, schema=SCHEMA, path=dump, rel="TBCDB.sql.gz"),
        _run(updates, schema=SCHEMA, path=clash, rel="Updates/z1_clash.sql"),
        _run(updates, schema=SCHEMA, path=good, rel="Updates/z2_more.sql"),
    )
    sunk: list[str] = []
    lines = list(
        sqlplan.apply(
            runs,
            container=mariadb_container,
            client="mariadb",
            password=MARIADB_ROOT_PASSWORD,
            exec_stdin=docker.exec_stdin,
            sink=sunk.append,
            cancel=None,
        )
    )
    assert lines[0] == "databases: statement 1 (no schema)"
    assert lines[1] == f"world base: TBCDB.sql.gz -> {SCHEMA}"
    warning = next(line for line in lines if line.startswith("warning:"))
    assert "Updates/z1_clash.sql" in warning
    # The client's own words, found by `_last_line()` in what it really printed.
    assert "already exists" in warning, warning
    assert "is on_error: warn" in warning
    assert any("already exists" in line for line in sunk), sunk
    assert lines[-1] == f"world updates: Updates/z2_more.sql -> {SCHEMA}"
    assert MARIADB_ROOT_PASSWORD not in " ".join(lines + sunk)

    # The forgiven failure did not cost the phase the file after it.
    count = docker.sql_query(
        mariadb_container, "mariadb", MARIADB_ROOT_PASSWORD, SCHEMA, "SELECT COUNT(*) FROM t"
    )
    assert count.splitlines() == ["250"], count


def test_a_truncated_dump_leaves_a_half_written_database_and_is_still_refused(
    tmp_path: Path, mariadb_container: str
) -> None:
    """The measurement, not the anecdote: rows land, the client is happy, apply() refuses.

    Nothing downstream of this could have caught it. The prefix is valid SQL,
    every statement in it committed, and a row count or a table list would agree
    the import had worked - which is why the read failure has to be an error and
    not a debug line.
    """
    dump = tmp_path / "TBCDB.sql.gz"
    with gzip.open(dump, "wb") as fh:
        fh.write(b"CREATE TABLE t (n INT PRIMARY KEY, payload TEXT);\n")
        fh.write(_rows(2000))
    whole = dump.read_bytes()
    dump.write_bytes(whole[: len(whole) // 2])

    create = SqlPhase(name="databases", statements=(f"CREATE DATABASE {SCHEMA};",))
    base = SqlPhase(name="world base", into=SCHEMA, files=("*.sql.gz",), gzip=True)
    runs = (
        _run(create, schema=None, path=None, rel="statement 1", sql=f"CREATE DATABASE {SCHEMA};"),
        _run(base, schema=SCHEMA, path=dump, rel="TBCDB.sql.gz"),
    )
    with pytest.raises(InstallerError) as excinfo:
        list(
            sqlplan.apply(
                runs,
                container=mariadb_container,
                client="mariadb",
                password=MARIADB_ROOT_PASSWORD,
                exec_stdin=docker.exec_stdin,
                sink=lambda _: None,
                cancel=None,
            )
        )
    assert "TBCDB.sql.gz" in str(excinfo.value)
    assert "could not be read" in str(excinfo.value)
    assert "EOFError" in str(excinfo.value), "a truncated download, named as one"
    assert isinstance(excinfo.value.__cause__, docker.SourceUnreadableError)

    landed = docker.sql_query(
        mariadb_container, "mariadb", MARIADB_ROOT_PASSWORD, SCHEMA, "SELECT COUNT(*) FROM t"
    )
    rows = int(landed.strip())
    assert 0 < rows < 2000, (
        f"{rows} rows of 2000 - the point of this gate is that a corrupt dump imports "
        "PARTLY and reports nothing, so a number at either end means it did not reproduce"
    )
