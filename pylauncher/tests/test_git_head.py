"""Tests for `yulon.catalog.git_head`: the commit a checkout is on, read with no git run (T230).

The reader moved here out of `native.py` so that `build_context` can use it
without importing `native` (which imports `build_context`).
`tests/test_update_rollback_database.py` still reads it as `native.read_head_file`,
unchanged, and that is the re-export's test.
"""

from __future__ import annotations

from pathlib import Path

from yulon.catalog import git_head, native

OLD = "a" * 40
NEW = "b" * 40
SHA256 = "c" * 64


def _gitdir(root: Path) -> Path:
    gitdir = root / ".git"
    (gitdir / "refs" / "heads").mkdir(parents=True)
    return gitdir


def test_a_head_resolves_through_a_loose_ref_packed_refs_or_itself(tmp_path: Path) -> None:
    gitdir = _gitdir(tmp_path)
    # Detached: HEAD holds the commit itself, SHA-1 or SHA-256.
    assert git_head.resolve_head(gitdir, f"{OLD}\n") == OLD
    assert git_head.resolve_head(gitdir, SHA256) == SHA256
    # On a branch whose ref is only in packed-refs.
    (gitdir / "packed-refs").write_text(
        f"# pack-refs with: peeled fully-peeled sorted\n{NEW} refs/heads/main\n"
        f"{OLD} refs/heads/mainline\n",
        encoding="utf-8",
    )
    assert git_head.resolve_head(gitdir, "ref: refs/heads/main\n") == NEW
    # A loose ref wins over packed-refs, as git reads it.
    (gitdir / "refs" / "heads" / "main").write_text(f"{OLD}\n", encoding="utf-8")
    assert git_head.resolve_head(gitdir, "ref: refs/heads/main\n") == OLD


def test_a_head_that_names_no_commit_resolves_to_none(tmp_path: Path) -> None:
    gitdir = _gitdir(tmp_path)
    # A missing ref, with no packed-refs at all.
    assert git_head.resolve_head(gitdir, "ref: refs/heads/main\n") is None
    # A symref chain: the loose ref holds a ref, not a commit.
    (gitdir / "refs" / "heads" / "main").write_text("ref: refs/heads/other\n", encoding="utf-8")
    assert git_head.resolve_head(gitdir, "ref: refs/heads/main\n") is None
    # Text that is no commit id.
    assert git_head.resolve_head(gitdir, "not a commit\n") is None
    assert git_head.resolve_head(gitdir, OLD[:12]) is None


def test_read_head_file_reads_head_and_resolves_it(tmp_path: Path) -> None:
    gitdir = _gitdir(tmp_path)
    assert git_head.read_head_file(tmp_path) is None, "no HEAD at all"
    (gitdir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (gitdir / "refs" / "heads" / "main").write_text(f"{NEW}\n", encoding="utf-8")
    assert git_head.read_head_file(tmp_path) == NEW


def test_native_still_offers_the_same_reader() -> None:
    assert native.read_head_file is git_head.read_head_file
