"""A module Update keeps the commit it moved from, and a put-back returns to it (T557).

Driven against real git: a `file://` origin, a depth-1 clone made by the real
`RunnerGit`, and the Applier's own guards reading the real checkout. The
manifest's github URL is swapped for the local origin at the clone seam only,
as `test_apply._LocalOrigin` does, so branch, depth and `rev` reach git exactly
as `Applier` built them.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from yulon import git as git_module
from yulon import module_moves, runner
from yulon.apply import (
    PUT_BACK_KEPT_SQL,
    Applier,
    ApplyError,
    LastUpdate,
    PutBackRefused,
    clone_release,
    reflog_update,
)
from yulon.git import CloneSpec, ContainerGit, GitError, ReflogEntry, RunnerGit, git_available
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


# -- step 3: last_update(), put_back() ----------------------------------------


def _argv_seen(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Every host git argv from here on, run for real."""
    seen: list[list[str]] = []
    real = git_module._run_git

    def spy(argv: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return real(argv, cwd)

    monkeypatch.setattr(git_module, "_run_git", spy)
    return seen


def _updated_a_to_b(rig: _Rig) -> tuple[str, str]:
    a = _installed_at_a(rig)
    b = _publish(rig.origin, "B")
    rig.applier.update(rig.manifest)
    assert rig.head() == b
    return a, b


def test_last_update_reads_the_record_first(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    a, b = _updated_a_to_b(rig)

    last = rig.applier.last_update(rig.manifest)

    assert last == LastUpdate(item_id=ITEM, from_sha=a, to_sha=b, source="ledger")


def test_put_back_runs_no_fetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test 6: offline-safe. No `fetch`, and the clone seam is never asked."""
    rig = _rig(tmp_path)
    a, _b = _updated_a_to_b(rig)
    last = rig.applier.last_update(rig.manifest)
    assert last is not None
    clones = len(rig.git.specs)
    seen = _argv_seen(monkeypatch)

    rig.applier.put_back(rig.manifest, last=last)

    assert rig.head() == a
    assert len(rig.git.specs) == clones, "the put-back went through the clone seam"
    assert not [argv for argv in seen if "fetch" in argv], seen
    assert [argv for argv in seen if "checkout" in argv], "the restore is git's own checkout"


def test_put_back_moves_the_move_to_skipped(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    _a, b = _updated_a_to_b(rig)
    last = rig.applier.last_update(rig.manifest)
    assert last is not None

    report = rig.applier.put_back(rig.manifest, last=last)

    ledger = rig.ledger()
    assert KEY not in ledger.moves
    assert ledger.skipped[KEY].tip == b
    assert rig.applier.last_update(rig.manifest) is None, "a put-back is not an update"
    assert any("back on" in line for line in report.done), report.done


def test_put_back_refused_on_an_edited_tree(tmp_path: Path) -> None:
    """Test 7: `--force` would throw the player's edit away, so nothing moves."""
    rig = _rig(tmp_path)
    _a, b = _updated_a_to_b(rig)
    last = rig.applier.last_update(rig.manifest)
    assert last is not None
    (rig.clone / "src" / "x.cpp").write_text("// fixed by hand\n", encoding="utf-8")

    with pytest.raises(PutBackRefused) as refused:
        rig.applier.put_back(rig.manifest, last=last)

    assert refused.value.edited
    assert rig.head() == b
    assert (rig.clone / "src" / "x.cpp").read_text(encoding="utf-8") == "// fixed by hand\n"
    assert KEY in rig.ledger().moves


def test_put_back_refused_when_head_moved(tmp_path: Path) -> None:
    """Test 8: HEAD is not the update's `to` any more."""
    rig = _rig(tmp_path)
    a, b = _updated_a_to_b(rig)
    stale = LastUpdate(item_id=ITEM, from_sha=a, to_sha="d" * 40, source="ledger")

    with pytest.raises(PutBackRefused) as refused:
        rig.applier.put_back(rig.manifest, last=stale)

    assert not refused.value.edited
    assert rig.head() == b


def test_put_back_runs_no_sql_and_says_the_database_changes_were_kept(tmp_path: Path) -> None:
    """D5."""
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
    _updated_a_to_b(rig)
    last = rig.applier.last_update(rig.manifest)
    assert last is not None and last.sql
    sent = len(sql.files)

    report = rig.applier.put_back(rig.manifest, last=last)

    assert len(sql.files) == sent, "a put-back sent SQL"
    assert PUT_BACK_KEPT_SQL in report.skipped


def test_put_back_restores_the_release_tag(tmp_path: Path) -> None:
    """Test 17: the claim names the release the clone is back on."""
    rig = _rig(tmp_path)
    a, b = _updated_a_to_b(rig)
    last = LastUpdate(item_id=ITEM, from_sha=a, to_sha=b, source="ledger", from_release="v1")

    rig.applier.put_back(rig.manifest, last=last)

    assert clone_release(rig.clone, item_id=ITEM) == "v1"


def test_last_update_from_the_reflog_after_a_ledgerless_update(tmp_path: Path) -> None:
    """Test 20: a pre-T557 update, against real git (depth 1, detached after the put-back)."""
    rig = _rig(tmp_path)
    a, b = _updated_a_to_b(rig)
    (rig.server / module_moves.MOVES_FILE).unlink()

    last = rig.applier.last_update(rig.manifest)
    assert last == LastUpdate(item_id=ITEM, from_sha=a, to_sha=b, source="reflog")

    rig.applier.update(rig.manifest)  # nothing new: one more reset onto the same commit
    (rig.server / module_moves.MOVES_FILE).unlink(missing_ok=True)
    assert rig.applier.last_update(rig.manifest) == last

    rig.applier.put_back(rig.manifest, last=last)
    assert rig.head() == a

    c = _publish(rig.origin, "C")
    rig.applier.update(rig.manifest)
    assert rig.head() == c, "an Update after a put-back moves a detached shallow clone on"


def test_a_hand_reset_is_not_read_as_an_update(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    _updated_a_to_b(rig)
    (rig.server / module_moves.MOVES_FILE).unlink()
    _git(rig.clone, "reset", "--hard", "HEAD@{1}")

    assert rig.applier.last_update(rig.manifest) is None


def test_a_fresh_install_has_no_last_update(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    _installed_at_a(rig)

    assert rig.applier.last_update(rig.manifest) is None


def test_an_unreadable_record_still_offers_the_reflog_answer(tmp_path: Path) -> None:
    """Test 18's other half: the automatic path is closed, the press is not."""
    rig = _rig(tmp_path)
    a, b = _updated_a_to_b(rig)
    (rig.server / module_moves.MOVES_FILE).write_text("{torn", encoding="utf-8")

    last = rig.applier.last_update(rig.manifest)

    assert last == LastUpdate(item_id=ITEM, from_sha=a, to_sha=b, source="reflog")


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        ([("B", "reset: moving to FETCH_HEAD"), ("A", "clone: from x")], "A"),
        (
            [
                ("B", "reset: moving to FETCH_HEAD"),
                ("B", "reset: moving to FETCH_HEAD"),
                ("A", "reset: moving to FETCH_HEAD"),
            ],
            "A",
        ),
        ([("B", f"checkout: moving from {'a' * 40} to {'b' * 40}"), ("A", "x")], "A"),
        ([("B", f"checkout: moving from main to {'b' * 40}"), ("A", "clone: from x")], None),
        # The player's own `git checkout mybranch` from a detached HEAD is not an update.
        ([("B", f"checkout: moving from {'a' * 40} to mybranch"), ("A", "x")], None),
        ([("B", "reset: moving to HEAD@{1}"), ("A", "reset: moving to FETCH_HEAD")], None),
        ([("B", "commit: mine"), ("A", "reset: moving to FETCH_HEAD")], None),
        ([("B", "reset: moving to FETCH_HEAD")], None),
        ([("A", "reset: moving to FETCH_HEAD")], None),
        ([], None),
    ],
    ids=[
        "reset",
        "no-op-update-walked-past",
        "release-checkout",
        "first-install-pin",
        "hand-checkout-of-a-branch",
        "hand-reset",
        "commit",
        "nothing-before",
        "head-not-newest",
        "empty",
    ],
)
def test_the_reflog_walk(entries: list[tuple[str, str]], expected: str | None) -> None:
    said = tuple(ReflogEntry(sha=sha, subject=subject) for sha, subject in entries)
    assert reflog_update(said, "B") == expected


def test_container_git_reflog_argv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test 21: the reflog is read in the read-only container, with no network."""
    dest = tmp_path / "mod-x"
    (dest / ".git").mkdir(parents=True)
    seen: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return subprocess.CompletedProcess(
            argv, 0, f"{'b' * 40}\treset: moving to FETCH_HEAD\n", ""
        )

    monkeypatch.setattr(runner, "run", fake_run)
    impl = ContainerGit(selinux_enforcing=lambda: False, filesystem_type=lambda _p: "ext4")

    assert impl.reflog(dest) == (ReflogEntry("b" * 40, "reset: moving to FETCH_HEAD"),)
    (argv,) = seen
    assert argv[-len(git_module.REFLOG_ARGS) :] == git_module.REFLOG_ARGS
    assert argv[argv.index("--network") + 1] == "none"
    assert f"{dest}:/git:ro" in argv


def test_container_git_restore_has_no_fetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test 21: the put-back's restore is one checkout, in the container that may write."""
    dest = tmp_path / "mod-x"
    (dest / ".git").mkdir(parents=True)
    seen: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(runner, "run", fake_run)
    impl = ContainerGit(selinux_enforcing=lambda: False, filesystem_type=lambda _p: "ext4")

    impl.restore_rev(dest, "a" * 40)

    (argv,) = seen
    assert argv[-4:] == ["checkout", "--detach", "--force", "a" * 40]
    assert "fetch" not in argv


def test_a_second_put_back_of_a_detached_clone_offers_nothing_more(tmp_path: Path) -> None:
    """After a put-back the clone is detached, so the next put-back's checkout reads as an update.

    Its reflog's newest move is then `checkout: moving from <B> to <A>`, the same shape a
    release module's Update leaves, and the commit before it is B, the tip just put back.
    """
    rig = _rig(tmp_path)
    a, b = _updated_a_to_b(rig)
    first = rig.applier.last_update(rig.manifest)
    assert first is not None
    rig.applier.put_back(rig.manifest, last=first)
    module_moves.clear_skip(rig.server, KEY)  # the player said try it anyway
    rig.applier.update(rig.manifest)
    assert rig.head() == b
    again = rig.applier.last_update(rig.manifest)
    assert again is not None and again.from_sha == a

    rig.applier.put_back(rig.manifest, last=again)

    assert rig.head() == a
    assert rig.applier.last_update(rig.manifest) is None


# -- review: a record with no destination is never acted on by itself ------------


def _forget_where_the_update_landed(rig: _Rig) -> None:
    """The state a killed app or a lost second write leaves: `{from: A, to: null}`."""
    path = rig.server / module_moves.MOVES_FILE
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["moves"][KEY]["to"] = None
    path.write_text(json.dumps(payload), encoding="utf-8")


def _hand_commit(rig: _Rig) -> str:
    _git(rig.clone, "commit", "--allow-empty", "-qm", "mine")
    return rig.head()


def test_a_move_with_no_destination_is_not_acted_on_after_a_hand_commit(tmp_path: Path) -> None:
    """`to: null` used to read as "any HEAD but `from`", so the player's own commit was
    taken for the update and a failed build put the clone back over it.

    Mutation: let `last_update()` answer from a move with no `to`.
    """
    rig = _rig(tmp_path)
    a, _b = _updated_a_to_b(rig)
    _forget_where_the_update_landed(rig)
    mine = _hand_commit(rig)

    assert rig.applier.last_update(rig.manifest) is None
    said = rig.applier.after_failed_build(("mod-x",), lambda _id: rig.manifest)

    assert rig.head() == mine, "the player's commit was left behind"
    assert a not in said
    assert _git(rig.clone, "log", "-1", "--format=%s") == "mine"


def test_a_move_with_no_destination_is_offered_by_hand_and_never_put_back_by_itself(
    tmp_path: Path,
) -> None:
    rig = _rig(tmp_path)
    a, b = _updated_a_to_b(rig)
    _forget_where_the_update_landed(rig)

    last = rig.applier.last_update(rig.manifest)
    assert last == LastUpdate(item_id=ITEM, from_sha=a, to_sha=b, source="reflog")
    said = rig.applier.after_failed_build(("mod-x",), lambda _id: rig.manifest)

    assert rig.head() == b
    assert "right-click mod-x" in said


def test_put_back_refuses_when_the_player_committed_on_top(tmp_path: Path) -> None:
    """The check lives in `put_back()` too: a `last` read before the commit is stale.

    Mutation: drop the re-read of `last_update()` in `put_back()`.
    """
    rig = _rig(tmp_path)
    a, b = _updated_a_to_b(rig)
    last = rig.applier.last_update(rig.manifest)
    assert last is not None
    (rig.server / module_moves.MOVES_FILE).unlink()
    mine = _hand_commit(rig)

    with pytest.raises(PutBackRefused):
        rig.applier.put_back(
            rig.manifest, last=dataclasses.replace(last, to_sha=mine, source="reflog")
        )

    assert rig.head() == mine


# -- review: what the failure sentences say --------------------------------------


def test_a_put_back_that_moved_git_but_failed_after_says_it_is_back_on_the_old_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Git is already on A when a later step fails, so "tried to put it back and could not" lies.

    Mutation: say "tried … could not" whatever git shows.
    """
    rig = _rig(tmp_path)
    a, _b = _updated_a_to_b(rig)

    def broken(*_args: object) -> None:
        raise ApplyError("deploy failed")

    monkeypatch.setattr(rig.applier, "_deploy", broken)
    said = rig.applier.after_failed_build((ITEM,), lambda _id: rig.manifest)

    assert rig.head() == a
    assert f"{ITEM} is back on {a[:7]}, but the steps after that failed (deploy failed)" in said
    assert "could not" not in said


def test_a_put_back_that_never_moved_git_still_says_it_could_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = _rig(tmp_path)
    a, b = _updated_a_to_b(rig)

    def broken(*_args: object) -> None:
        raise GitError("checkout failed")

    monkeypatch.setattr(RunnerGit, "restore_rev", lambda self, dest, rev: broken())
    said = rig.applier.after_failed_build((ITEM,), lambda _id: rig.manifest)

    assert rig.head() == b
    assert f"tried to put it back on {a[:7]} and could not" in said


def test_an_error_naming_only_a_module_that_was_not_updated_lists_the_waiting_updates(
    tmp_path: Path,
) -> None:
    """The broken module may be an update whose path the stream did not carry.

    Mutation: list the waiting updates only when the error names no module at all.
    """
    rig = _rig(tmp_path)
    _updated_a_to_b(rig)
    other = rig.manifest.model_copy(update={"id": "mod-never-updated"})

    said = rig.applier.after_failed_build(("mod-never-updated",), lambda _id: other)

    assert "mod-never-updated was not updated since your last build that worked" in said
    assert f"updated since your last build that worked: {ITEM}." in said
    assert "choose Put back the last update" in said
    assert "does not say which module" not in said


def test_a_module_the_sentences_already_cover_is_not_listed_as_waiting_again(
    tmp_path: Path,
) -> None:
    rig = _rig(tmp_path)
    _updated_a_to_b(rig)
    (rig.clone / "src" / "x.cpp").write_text("// edited\n", encoding="utf-8")

    said = rig.applier.after_failed_build((ITEM,), lambda _id: rig.manifest)

    assert "files in its folder were changed" in said
    assert "updated since your last build that worked" not in said
