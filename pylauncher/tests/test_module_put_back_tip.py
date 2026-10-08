"""T557 step 5: Check for updates does not offer the tip that was put back (D2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from yulon import apply as apply_module
from yulon import module_moves
from yulon.git import Behind, Counted, is_behind

KEY = module_moves.key("module", "mod-x")
BAD = "b" * 40
OTHER = "c" * 40
HEAD = "a" * 40


class _CountingGit:
    """A `CountedReader` that says `fetched` for every clone, and counts how often it was asked."""

    def __init__(self, fetched: str, behind: object = 1, head: str = HEAD) -> None:
        self.fetched = fetched
        self.behind = behind
        self.head = head
        self.counted = 0

    def commits_behind(self, dest: Path, branch: str | None, *, release: bool = False) -> object:
        return self.counted_behind(dest, branch, release=release).behind

    def counted_behind(self, dest: Path, branch: str | None, *, release: bool = False) -> Counted:
        self.counted += 1
        return Counted(self.behind, head=self.head, fetched=self.fetched)  # type: ignore[arg-type]

    def head_sha(self, dest: Path) -> str | None:
        return self.head


def _server(tmp_path: Path, *, skipped: str | None = BAD) -> Path:
    (tmp_path / "modules" / "mod-x" / ".git").mkdir(parents=True)
    if skipped is not None:
        assert module_moves.skip(tmp_path, KEY, tip=skipped) == ""
    return tmp_path


def test_check_for_updates_does_not_offer_the_put_back_tip(tmp_path: Path) -> None:
    server = _server(tmp_path)
    (row,) = apply_module.module_updates(server, git=_CountingGit(fetched=BAD))
    assert not is_behind(row.behind)
    assert row.line == (
        "mod-x: the newest version (bbbbbbb) did not build on this server and was put back, "
        "so it is not offered. Yu'lon will offer the next one when its author publishes it."
    )


def test_a_newer_tip_than_the_put_back_one_is_offered_again(tmp_path: Path) -> None:
    server = _server(tmp_path)
    (row,) = apply_module.module_updates(server, git=_CountingGit(fetched=OTHER))
    assert row.behind == 1
    assert row.line == "mod-x: 1 commit behind"
    assert row.put_back_tip == ""


def test_the_skip_is_compared_on_the_fetched_tip_and_not_on_head(tmp_path: Path) -> None:
    """HEAD equal to the skipped tip, fetched newer: the author moved on, so offer it."""
    server = _server(tmp_path)
    git = _CountingGit(fetched=OTHER, head=BAD)
    (row,) = apply_module.module_updates(server, git=git)
    assert row.behind == 1


def test_an_unreadable_record_hides_nothing(tmp_path: Path) -> None:
    server = _server(tmp_path, skipped=None)
    (server / module_moves.MOVES_FILE).write_text("{not json", encoding="utf-8")
    (row,) = apply_module.module_updates(server, git=_CountingGit(fetched=BAD))
    assert row.behind == 1


def test_a_shallow_uncounted_row_on_the_put_back_tip_is_not_offered(tmp_path: Path) -> None:
    server = _server(tmp_path)
    (row,) = apply_module.module_updates(
        server, git=_CountingGit(fetched=BAD, behind=Behind.UNCOUNTED)
    )
    assert not is_behind(row.behind)
    assert row.put_back_tip == BAD


def test_the_cached_rows_hide_a_tip_that_was_put_back_after_they_were_counted(
    tmp_path: Path,
) -> None:
    """The row counted before the put-back is valid by HEAD, and still must not offer the tip."""
    server = _server(tmp_path, skipped=None)
    git = _CountingGit(fetched=BAD)
    (first,) = apply_module.cached_module_updates(server, kind="module", git=git, now=1_000)
    assert first.behind == 1
    assert module_moves.skip(server, KEY, tip=BAD) == ""

    (later,) = apply_module.cached_module_updates(server, kind="module", git=git, now=1_001)
    assert git.counted == 1, "the cached row was meant to be reused"
    assert not is_behind(later.behind)
    assert later.put_back_tip == BAD


def test_a_cleared_skip_offers_the_tip_again_from_the_cache(tmp_path: Path) -> None:
    server = _server(tmp_path)
    git = _CountingGit(fetched=BAD)
    (first,) = apply_module.cached_module_updates(server, kind="module", git=git, now=1_000)
    assert first.put_back_tip == BAD
    assert module_moves.clear_skip(server, KEY) == ""
    (later,) = apply_module.cached_module_updates(server, kind="module", git=git, now=1_001)
    assert later.behind == 1
    assert later.put_back_tip == ""


@pytest.mark.parametrize("skipped", [None, OTHER])
def test_no_skip_for_this_tip_changes_nothing(tmp_path: Path, skipped: str | None) -> None:
    server = _server(tmp_path, skipped=skipped)
    (row,) = apply_module.module_updates(server, git=_CountingGit(fetched=BAD))
    assert row.behind == 1
