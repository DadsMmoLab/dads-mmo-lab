"""A module Update keeps the commit it moved from, and a put-back returns to it (T557).

Driven against real git: a `file://` origin, a depth-1 clone made by the real
`RunnerGit`, and the Applier's own guards reading the real checkout. The
manifest's github URL is swapped for the local origin at the clone seam only,
as `test_apply._LocalOrigin` does, so branch, depth and `rev` reach git exactly
as `Applier` built them.
"""

from __future__ import annotations

import dataclasses
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from yulon import module_moves
from yulon.apply import Applier, ApplyError
from yulon.git import CloneSpec, GitError, RunnerGit, git_available
from yulon.manifest import Manifest, parse_manifest

pytestmark = pytest.mark.skipif(not git_available(), reason="needs a host git")

ITEM = "mod-x"
KEY = f"module/{ITEM}"
URL = "https://github.com/acme/mod-x"


class _LocalOrigin:
    """`RunnerGit` with the manifest's URL answered by a local repository; records each call."""

    def __init__(self, origin: Path) -> None:
        self.origin = origin
        self.specs: list[CloneSpec] = []

    def _local(self, spec: CloneSpec) -> CloneSpec:
        self.specs.append(spec)
        return dataclasses.replace(spec, url=self.origin.as_uri())

    def clone(self, spec: CloneSpec) -> None:
        RunnerGit().clone(self._local(spec))

    def clone_lines(self, spec: CloneSpec, *, stage: str = "clone") -> Iterator[str]:
        return RunnerGit().clone_lines(self._local(spec), stage=stage)


def _git(cwd: Path, *argv: str) -> str:
    author = ["-c", "user.email=t@example.invalid", "-c", "user.name=t"]
    return subprocess.run(
        ["git", *author, *argv], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _publish(origin: Path, version: str) -> str:
    (origin / "src").mkdir(parents=True, exist_ok=True)
    (origin / "src" / "x.cpp").write_text(f"// {version}\n", encoding="utf-8")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-qm", version)
    return _git(origin, "rev-parse", "HEAD")


def _manifest(**source: Any) -> Manifest:
    return parse_manifest(
        {
            "schema_version": 1,
            "id": ITEM,
            "name": "X",
            "type": "module",
            "game": "wow-wotlk",
            "description": "A module for the test.",
            "source": {"repo": "acme/mod-x", **source},
            "build": {"rebuild": True},
        }
    )


@dataclasses.dataclass
class _Rig:
    origin: Path
    server: Path
    applier: Applier
    git: _LocalOrigin
    manifest: Manifest

    @property
    def clone(self) -> Path:
        return self.applier.clone_dir(self.manifest)

    def head(self) -> str:
        return _git(self.clone, "rev-parse", "HEAD")

    def ledger(self) -> module_moves.Ledger:
        ledger = module_moves.read(self.server)
        assert ledger is not None
        return ledger


def _rig(tmp_path: Path, manifest: Manifest | None = None) -> _Rig:
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    server = tmp_path / "server"
    server.mkdir()
    git = _LocalOrigin(origin)
    applier = Applier(server, git=git, remote_url=lambda _dest: URL)
    return _Rig(origin, server, applier, git, manifest or _manifest())


def _installed_at_a(rig: _Rig) -> str:
    a = _publish(rig.origin, "A")
    rig.applier.install(rig.manifest)
    assert rig.head() == a
    return a


# -- step 2: Update writes the record -----------------------------------------


def test_update_records_the_commit_it_moved_from(tmp_path: Path) -> None:
    """Test 1: clone at A, Update to B, the record says A→B."""
    rig = _rig(tmp_path)
    a = _installed_at_a(rig)
    b = _publish(rig.origin, "B")

    rig.applier.update(rig.manifest)

    assert rig.head() == b
    move = rig.ledger().moves[KEY]
    assert (move.from_sha, move.to_sha) == (a, b)


def test_install_alone_records_nothing(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    _installed_at_a(rig)
    _publish(rig.origin, "B")

    rig.applier.install(rig.manifest)

    assert KEY not in rig.ledger().moves


def test_second_unbuilt_update_keeps_the_first_from(tmp_path: Path) -> None:
    """Test 2: A→B, no build, B→C: the put-back must go to A, the commit that was built."""
    rig = _rig(tmp_path)
    a = _installed_at_a(rig)
    _publish(rig.origin, "B")
    rig.applier.update(rig.manifest)
    c = _publish(rig.origin, "C")

    rig.applier.update(rig.manifest)

    move = rig.ledger().moves[KEY]
    assert (move.from_sha, move.to_sha) == (a, c)


def test_update_that_moved_nothing_records_nothing(tmp_path: Path) -> None:
    """Test 3: already on the tip."""
    rig = _rig(tmp_path)
    _installed_at_a(rig)

    rig.applier.update(rig.manifest)

    assert KEY not in rig.ledger().moves


def test_update_that_fails_after_the_reset_keeps_the_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test 4: a step after the clone raises; the clone moved, so the record stays."""
    rig = _rig(tmp_path)
    a = _installed_at_a(rig)
    b = _publish(rig.origin, "B")

    def broken(*_args: object) -> None:
        raise ApplyError("deploy failed")

    monkeypatch.setattr(rig.applier, "_deploy", broken)
    with pytest.raises(ApplyError):
        rig.applier.update(rig.manifest)

    assert rig.head() == b
    move = rig.ledger().moves[KEY]
    assert (move.from_sha, move.to_sha) == (a, b)


def test_a_refused_update_leaves_no_entry(tmp_path: Path) -> None:
    """A tracked file edited by hand: Update refuses before anything moves, and records nothing."""
    rig = _rig(tmp_path)
    _installed_at_a(rig)
    _publish(rig.origin, "B")
    (rig.clone / "src" / "x.cpp").write_text("// mine\n", encoding="utf-8")

    with pytest.raises(ApplyError):
        rig.applier.update(rig.manifest)

    assert KEY not in rig.ledger().moves
    assert not (rig.server / module_moves.MOVES_FILE).exists()


def test_a_clone_step_that_fails_leaves_no_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The clone seam fails before it moves HEAD: nothing is left to act on later.

    A write-ahead left at `{from: A, to: null}` would read as a move the moment a
    hand reset took HEAD anywhere else, and put the module back to A unasked.
    """
    rig = _rig(tmp_path)
    _installed_at_a(rig)
    _publish(rig.origin, "B")

    def offline(_spec: CloneSpec) -> None:
        raise GitError("fatal: unable to access the remote")

    monkeypatch.setattr(rig.git, "clone", offline)
    with pytest.raises(ApplyError):
        rig.applier.update(rig.manifest)

    assert KEY not in rig.ledger().moves


def test_remove_drops_the_ledger_entries(tmp_path: Path) -> None:
    """Test 19."""
    rig = _rig(tmp_path)
    _installed_at_a(rig)
    b = _publish(rig.origin, "B")
    rig.applier.update(rig.manifest)
    module_moves.skip(rig.server, KEY, tip=b)
    module_moves.record_start(rig.server, "module/mod-y", head=b, release="")

    rig.applier.remove(rig.manifest)

    ledger = rig.ledger()
    assert KEY not in ledger.moves and KEY not in ledger.skipped
    assert "module/mod-y" in ledger.moves, "only the removed module's entries go"


def test_a_remove_on_a_server_with_no_record_writes_none(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    _installed_at_a(rig)

    rig.applier.remove(rig.manifest)

    assert not (rig.server / module_moves.MOVES_FILE).exists()


class _Sql:
    def __init__(self) -> None:
        self.files: list[str] = []

    def run_file(self, db: Any, path: Path) -> None:
        self.files.append(path.name)

    def run_statement(self, db: Any, statement: str) -> None:
        self.files.append(statement)


def test_an_update_that_ran_database_changes_says_so_in_the_record(tmp_path: Path) -> None:
    """D5: the put-back runs no SQL, so the record must know the update did."""
    manifest = parse_manifest(
        {
            **_manifest().model_dump(mode="json", exclude_none=True),
            "sql": [{"db": "world", "path": "data/sql/x.sql", "applied_by": "direct"}],
        }
    )
    rig = _rig(tmp_path, manifest)
    sql = _Sql()
    rig.applier.sql = sql
    (rig.origin / "data" / "sql").mkdir(parents=True)
    (rig.origin / "data" / "sql" / "x.sql").write_text("SELECT 1;\n", encoding="utf-8")
    _installed_at_a(rig)
    assert sql.files, "the fixture's install must have sent its SQL"
    _publish(rig.origin, "B")

    rig.applier.update(rig.manifest)

    assert rig.ledger().moves[KEY].sql


def test_an_update_with_no_database_changes_says_none(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    _installed_at_a(rig)
    _publish(rig.origin, "B")

    rig.applier.update(rig.manifest)

    assert not rig.ledger().moves[KEY].sql
