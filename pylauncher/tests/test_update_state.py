"""Tests for `update.json` (`yulon.update_state`, T90): its own file, never fatal."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from tests.conftest import HANG_BOUND
from yulon.update_state import (
    UpdateState,
    load_update_state,
    remember,
    save_update_state,
    update_lock,
    update_state_path,
)


def test_missing_file_is_an_empty_state(tmp_path: Path) -> None:
    assert load_update_state(tmp_path / "update.json") == UpdateState()


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "update.json"
    state = UpdateState(
        last_checked=12.5, etag='W/"abc"', feed="[]", skipped_version="v0.8.70-Public"
    )

    assert save_update_state(state, path) is True
    assert load_update_state(path) == state
    assert not list(tmp_path.glob("*.tmp")), "a temporary file was left behind"


def test_garbage_is_an_empty_state_and_is_left_alone(tmp_path: Path) -> None:
    """Unlike `state.json`, nothing here is worth moving aside: it can all be re-fetched."""
    path = tmp_path / "update.json"
    path.write_text("{not json", encoding="utf-8")

    assert load_update_state(path) == UpdateState()
    assert path.exists(), "nothing worth keeping is in it: the next save overwrites it"


def test_a_bom_and_an_unknown_key_are_both_fine(tmp_path: Path) -> None:
    """A newer build's extra field must not cost an older build the whole file."""
    path = tmp_path / "update.json"
    path.write_bytes(b'\xef\xbb\xbf{"etag": "x", "from_a_newer_build": 1}')

    assert load_update_state(path).etag == "x"


def test_wrong_types_are_an_empty_state(tmp_path: Path) -> None:
    path = tmp_path / "update.json"
    path.write_text('{"last_checked": "yesterday"}', encoding="utf-8")

    assert load_update_state(path) == UpdateState()


def test_an_unwritable_place_is_false_not_a_crash(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("", encoding="utf-8")

    assert save_update_state(UpdateState(), blocker / "update.json") is False


def test_it_lives_beside_state_json_and_is_not_state_json(tmp_path: Path) -> None:
    assert update_state_path(tmp_path) == tmp_path / "update.json"


def test_the_default_path_follows_the_config_dir(tmp_path: Path) -> None:
    """No argument means the app's own config dir — the suite redirects it per test."""
    from yulon import platform

    assert update_state_path() == platform.config_dir() / "update.json"
    assert update_state_path() != tmp_path / "update.json"


# ------------------------------------------------------------ two writers, one file


def test_two_writers_never_share_a_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One fixed `update.json.tmp` meant two writers filling it and renaming it twice.

    The second rename then moves a file the first writer already moved, and
    what lands at `update.json` is a mixture of two JSON documents — which
    reads back as an empty state, losing the skip AND the cached feed.
    """
    path = tmp_path / "update.json"
    names: list[str] = []
    real_replace = Path.replace

    def note(self: Path, target: Path) -> Path:
        names.append(self.name)
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", note)
    save_update_state(UpdateState(etag="one"), path)
    save_update_state(UpdateState(etag="two"), path)

    assert len(names) == 2
    assert names[0] != names[1], "both writes went through the same temporary name"
    assert not list(tmp_path.glob("*.tmp")), "a temporary file was left behind"


def test_remember_changes_only_the_fields_it_is_given(tmp_path: Path) -> None:
    """It re-reads inside the lock, so no caller can carry a stale copy back."""
    path = tmp_path / "update.json"
    save_update_state(
        UpdateState(etag='W/"kept"', feed="[]", skipped_version="v1.0.0-Public"), path
    )

    assert remember({"last_checked": 42.0}, path) is True

    state = load_update_state(path)
    assert (state.last_checked, state.etag, state.feed, state.skipped_version) == (
        42.0,
        'W/"kept"',
        "[]",
        "v1.0.0-Public",
    )


def test_remember_does_not_raise_when_it_cannot_write(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("", encoding="utf-8")

    assert remember({"etag": "x"}, blocker / "update.json") is False


def test_the_lock_is_one_lock_and_it_really_excludes(tmp_path: Path) -> None:
    """Held across a whole load-modify-save; a second writer waits rather than interleaves."""
    entered = threading.Event()
    release = threading.Event()
    order: list[str] = []

    def holder() -> None:
        with update_lock():
            order.append("first in")
            entered.set()
            release.wait(timeout=HANG_BOUND)
            order.append("first out")

    def waiter() -> None:
        entered.wait(timeout=HANG_BOUND)
        with update_lock():
            order.append("second in")

    first = threading.Thread(target=holder)
    second = threading.Thread(target=waiter)
    first.start()
    second.start()
    entered.wait(timeout=HANG_BOUND)
    # The waiter has had its turn to try and is blocked, not through.
    assert order == ["first in"]
    release.set()
    first.join(timeout=HANG_BOUND)
    second.join(timeout=HANG_BOUND)

    assert order == ["first in", "first out", "second in"]


def test_the_lock_is_reentrant_so_remember_can_nest(tmp_path: Path) -> None:
    """`remember()` runs under it and calls load/save, which may grow locks of their own."""
    path = tmp_path / "update.json"

    with update_lock():
        assert remember({"etag": "nested"}, path) is True

    assert load_update_state(path).etag == "nested"
