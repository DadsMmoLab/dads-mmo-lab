"""Tests for `yulon.state` (README §11 app state)."""

from __future__ import annotations

from pathlib import Path

from yulon.state import AppState, KnownInstall, load_state, save_state, state_path


def test_state_roundtrip_remember_forget(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    assert load_state(path) == AppState()
    state = AppState()
    state.remember(KnownInstall(game="wow-wotlk", server_dir=tmp_path / "a"))
    state.remember(KnownInstall(game="wow-wotlk", server_dir=tmp_path / "a", client_dir=tmp_path))
    assert len(state.installs) == 1 and state.installs[0].client_dir == tmp_path
    save_state(state, path)
    loaded = load_state(path)
    assert loaded.find("wow-wotlk", tmp_path / "a") is not None
    loaded.forget("wow-wotlk", tmp_path / "a")
    assert loaded.installs == []
    assert not (path.with_name("state.json.tmp")).exists()


def test_state_path_lives_under_config_dir(tmp_path: Path) -> None:
    assert state_path(tmp_path) == tmp_path / "state.json"


def test_broken_state_file_is_moved_aside_not_raised(tmp_path: Path) -> None:
    """A corrupt or schema-drifted state.json must never stop the window from opening."""
    target = tmp_path / "state.json"
    target.write_text("{not json", encoding="utf-8")
    assert load_state(target).installs == []
    assert (tmp_path / "state.json.broken").read_text(encoding="utf-8") == "{not json"
    assert not target.exists()

    # Schema drift (extra="forbid") is handled the same way.
    target.write_text('{"schema_version": 1, "installs": [], "from_the_future": true}', "utf-8")
    assert load_state(target).installs == []
    assert '"from_the_future"' in (tmp_path / "state.json.broken").read_text(encoding="utf-8")
