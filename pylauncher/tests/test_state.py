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


def test_a_state_file_with_a_byte_order_mark_is_not_treated_as_corrupt(tmp_path: Path) -> None:
    """Every Windows tool that writes UTF-8 writes a BOM, and a BOM is legal.

    Read as plain `utf-8` it raises "Unexpected UTF-8 BOM", so a hand-edited
    state file was declared corrupt, moved to `.broken`, and every remembered
    install silently forgotten. Measured on the Win11 test VM, 2026-08-22.
    """
    target = tmp_path / "state.json"
    body = '{"schema_version": 1, "installs": [{"game": "wow-wotlk", "server_dir": "C:/wow"}]}'
    target.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))

    loaded = load_state(target)

    assert [i.game for i in loaded.installs] == ["wow-wotlk"]
    assert not (tmp_path / "state.json.broken").exists(), "moved a perfectly good file aside"


def test_a_known_install_can_name_the_distro_it_lives_in(tmp_path: Path) -> None:
    """Where a server lives is the same kind of fact as which folder it is in."""
    install = KnownInstall(
        game="wow-wotlk",
        server_dir=Path(r"\\wsl.localhost\dml-arch\home\dml\games\srv"),
        wsl_distro="dml-arch",
    )
    assert install.wsl_distro == "dml-arch"


def test_state_written_before_wsl_support_still_loads() -> None:
    """The field is optional, so no user's state.json is invalidated by adding it."""
    parsed = KnownInstall.model_validate({"game": "wow-wotlk", "server_dir": "C:/srv/wow"})
    assert parsed.wsl_distro is None
