"""Tests for `update.json` (`yulon.update_state`, T90): its own file, never fatal."""

from __future__ import annotations

from pathlib import Path

from yulon.update_state import UpdateState, load_update_state, save_update_state, update_state_path


def test_missing_file_is_an_empty_state(tmp_path: Path) -> None:
    assert load_update_state(tmp_path / "update.json") == UpdateState()


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "update.json"
    state = UpdateState(
        last_checked=12.5, etag='W/"abc"', feed="[]", skipped_version="v0.8.70-Public"
    )

    assert save_update_state(state, path) is True
    assert load_update_state(path) == state
    assert not path.with_name("update.json.tmp").exists(), "the temporary file was left behind"


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
