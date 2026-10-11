"""Tests for `yulon.state` (README §11 app state)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from yulon import state as state_module
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

    # A known field this build cannot read (here: an install with no game) is handled
    # the same way. An UNKNOWN field is not: it is a newer Yu'lon's, and is kept (T670).
    target.write_text('{"schema_version": 1, "installs": [{"server_dir": "C:/x"}]}', "utf-8")
    assert load_state(target).installs == []
    assert '"C:/x"' in (tmp_path / "state.json.broken").read_text(encoding="utf-8")


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


def test_installed_dirs_answers_one_folder_per_game() -> None:
    """The Catalog tab greys a tile per GAME, so this collapses per game."""
    app_state = AppState(
        installs=[
            KnownInstall(game="wow-wotlk", server_dir=Path("C:/srv/wotlk")),
            KnownInstall(game="wow-tbc", server_dir=Path("C:/srv/tbc")),
        ]
    )
    assert app_state.installed_dirs() == {
        "wow-wotlk": Path("C:/srv/wotlk"),
        "wow-tbc": Path("C:/srv/tbc"),
    }


def test_a_game_installed_twice_reports_the_one_remembered_last() -> None:
    """Two folders, one tile, one tooltip \u2014 and the newest is the least surprising.

    `remember()` appends, so the last entry is the most recent install. Asserted
    rather than left to the dict comprehension, because the opposite choice is
    equally easy to write and nothing else would notice: both installs keep
    their tabs either way, and only the tooltip changes.
    """
    app_state = AppState()
    app_state.remember(KnownInstall(game="wow-tbc", server_dir=Path("C:/srv/old")))
    app_state.remember(KnownInstall(game="wow-tbc", server_dir=Path("C:/srv/kept")))
    assert app_state.installed_dirs() == {"wow-tbc": Path("C:/srv/kept")}
    assert len(app_state.installs) == 2, "collapsing the tile must not forget an install"


# ------------------------------------------ T181: the ready-to-play client folder


def test_a_state_file_written_before_ready_to_play_clients_still_loads(tmp_path: Path) -> None:
    """An install record with no `play_client_dir` key loads, and reads as having none.

    Loaded from a file through `load_state()`, not built in memory: `extra="forbid"`
    moves a record it cannot read to `.broken`, so a required field would forget
    every install a player has.
    """
    target = tmp_path / "state.json"
    target.write_text(
        '{"schema_version": 1, "installs": [{"game": "wow-tbc", "server_dir": "C:/srv/tbc",'
        ' "client_dir": "C:/WoW", "wsl_distro": null}]}',
        encoding="utf-8",
    )

    loaded = load_state(target)

    assert [i.play_client_dir for i in loaded.installs] == [None]
    assert not (tmp_path / "state.json.broken").exists(), "an older state file was moved aside"


def test_the_ready_to_play_client_folder_survives_a_save_and_a_load(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    play = tmp_path / "WoW (Yu'lon \u2013 WoW TBC)"
    state = AppState()
    state.remember(
        KnownInstall(
            game="wow-tbc",
            server_dir=tmp_path / "srv",
            client_dir=tmp_path / "WoW",
            play_client_dir=play,
        )
    )
    save_state(state, path)

    loaded = load_state(path).find("wow-tbc", tmp_path / "srv")

    assert loaded is not None
    assert loaded.play_client_dir == play
    assert loaded.client_dir == tmp_path / "WoW", "the original folder is a separate fact"


# ------------------------------------------ T670: an older or newer Yu'lon on the same file


class _V0890Install(BaseModel):
    """`KnownInstall` exactly as v0.8.90 to v0.9.12 shipped it (git show v0.8.90-Public)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    game: str = Field(min_length=1)
    server_dir: Path
    client_dir: Path | None = None
    wsl_distro: str | None = None


class _V0890State(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    installs: list[_V0890Install] = Field(default_factory=list)


def _v0890_load_state(target: Path) -> list[str]:
    """v0.8.90's `load_state`: a file it cannot validate goes to `.broken`, no installs."""
    try:
        with target.open(encoding="utf-8-sig") as fh:
            state = _V0890State.model_validate(json.load(fh))
    except (OSError, ValueError, ValidationError):
        target.replace(target.with_name(target.name + ".broken"))
        return []
    return [i.game for i in state.installs]


def _three_installs(tmp_path: Path) -> AppState:
    state = AppState()
    state.remember(KnownInstall(game="wow-wotlk", server_dir=tmp_path / "wotlk"))
    state.remember(
        KnownInstall(game="wow-tbc", server_dir=tmp_path / "tbc", client_dir=tmp_path / "WoW")
    )
    state.remember(
        KnownInstall(
            game="wow-vanilla",
            server_dir=tmp_path / "vanilla",
            client_dir=tmp_path / "WoW1",
            play_client_dir=tmp_path / "WoW1 (Yu'lon)",
        )
    )
    return state


def test_an_older_yulon_reads_the_state_this_one_writes_when_no_newer_field_is_used(
    tmp_path: Path,
) -> None:
    """A field left at its default is not written, so an older reader has nothing to refuse.

    Every install without a ready-to-play client is a file v0.8.90 reads as it is.
    """
    path = tmp_path / "state.json"
    state = AppState()
    state.remember(KnownInstall(game="wow-wotlk", server_dir=tmp_path / "wotlk"))
    state.remember(
        KnownInstall(game="wow-tbc", server_dir=tmp_path / "tbc", client_dir=tmp_path / "WoW")
    )
    save_state(state, path)

    assert _v0890_load_state(path) == ["wow-wotlk", "wow-tbc"]
    assert not (tmp_path / "state.json.broken").exists(), "the older Yu'lon moved it aside"
    assert load_state(path) == state, "leaving defaults out lost nothing for this build"


def test_a_state_file_from_a_newer_yulon_keeps_its_installs_and_its_new_fields(
    tmp_path: Path,
) -> None:
    """A field this build does not know is a newer build's, not damage (T670).

    The installs load, nothing is moved aside, and a save writes the unknown fields
    back unchanged, so going forward again finds them where they were.
    """
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "installs": [
                    {"game": "wow-wotlk", "server_dir": "C:/srv/wotlk", "from_the_future": 7},
                    {"game": "wow-tbc", "server_dir": "C:/srv/tbc"},
                ],
                "also_from_the_future": {"k": [1, 2]},
            }
        ),
        encoding="utf-8",
    )

    loaded = load_state(path)

    assert [i.game for i in loaded.installs] == ["wow-wotlk", "wow-tbc"]
    assert not (tmp_path / "state.json.broken").exists()
    loaded.remember(KnownInstall(game="wow-vanilla", server_dir=Path("C:/srv/vanilla")))
    save_state(loaded, path)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["also_from_the_future"] == {"k": [1, 2]}
    assert written["installs"][0]["from_the_future"] == 7
    assert "from_the_future" not in written["installs"][1]


def _save_as_v0916(state: AppState, path: Path) -> None:
    """How v0.9.13 to v0.9.16 saved: every field written, defaults and all."""
    path.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")


def _v0890_save(path: Path, installs: list[dict[str, object]]) -> None:
    """How v0.8.90 saved: its own schema, every field written."""
    saved = _V0890State.model_validate({"installs": installs})
    path.write_text(saved.model_dump_json(indent=2) + "\n", encoding="utf-8")


def _downgrade(tmp_path: Path) -> Path:
    """v0.9.16 saved three servers; v0.8.90 then started, refused the file, set it aside.

    The set-aside file keeps the mtime of v0.9.16's save (a rename does not touch it),
    and it is made an hour older here, so every later save by the old build is newer:
    a rule that read "`.broken` newer than state.json" would refuse every one of these.
    """
    path = tmp_path / "state.json"
    _save_as_v0916(_three_installs(tmp_path), path)
    assert _v0890_load_state(path) == []  # v0.8.90 has never heard of play_client_dir
    hour_ago = time.time() - 3600
    os.utime(tmp_path / "state.json.broken", (hour_ago, hour_ago))
    return path


def test_going_forward_after_an_older_yulon_wiped_the_list_brings_the_servers_back(
    tmp_path: Path,
) -> None:
    """The measured downgrade: v0.8.90 moves the file aside and saves an empty list.

    The next start of this build finds `.broken`, which it CAN read, and puts its
    installs back. The `.broken` file is then retired, so the servers come back once:
    one forgotten afterwards stays forgotten.
    """
    path = _downgrade(tmp_path)
    _v0890_save(path, [])

    back = load_state(path)

    assert back == _three_installs(tmp_path)
    assert load_state(path) == back, "the restore was written to state.json"
    assert not (tmp_path / "state.json.broken").exists(), "the restored file was not retired"
    assert (tmp_path / "state.json.restored").is_file()
    back.forget("wow-tbc", tmp_path / "tbc")
    save_state(back, path)
    assert [i.game for i in load_state(path).installs] == ["wow-wotlk", "wow-vanilla"]


def test_a_server_the_older_yulon_re_attached_keeps_what_only_the_newer_one_knew(
    tmp_path: Path,
) -> None:
    """Review round 2: a server in both files is the `.broken` record with the older
    build's SET fields laid on top. The older build cannot write a ready-to-play
    client, so its record alone would lose it, and `.broken` is then retired."""
    path = _downgrade(tmp_path)
    _v0890_save(
        path,
        [
            # re-attached with "Use existing..." and no client folder given
            {"game": "wow-vanilla", "server_dir": str(tmp_path / "vanilla")},
            # re-attached and pointed at a new client folder: that choice is newer
            {"game": "wow-tbc", "server_dir": str(tmp_path / "tbc"), "client_dir": "C:/NewWoW"},
            # a server only the older build knows
            {"game": "wow-tbc", "server_dir": str(tmp_path / "tbc2")},
        ],
    )

    back = load_state(path)

    assert [(i.game, i.server_dir.name) for i in back.installs] == [
        ("wow-vanilla", "vanilla"),
        ("wow-tbc", "tbc"),
        ("wow-tbc", "tbc2"),
        ("wow-wotlk", "wotlk"),
    ]
    vanilla = back.find("wow-vanilla", tmp_path / "vanilla")
    assert vanilla is not None
    assert vanilla.play_client_dir == tmp_path / "WoW1 (Yu'lon)", "the play client was lost"
    assert vanilla.client_dir == tmp_path / "WoW1", "the client folder was lost"
    tbc = back.find("wow-tbc", tmp_path / "tbc")
    assert tbc is not None and tbc.client_dir == Path("C:/NewWoW")
    assert load_state(path) == back


def test_an_old_broken_file_with_nothing_an_older_yulon_refused_is_left_alone(
    tmp_path: Path,
) -> None:
    """A `.broken` from before 2026-08-22 (a byte-order mark the reader then refused)
    holds only fields every build reads: it is not what a downgrade sets aside, and the
    servers in it may be ones the player has removed since."""
    path = tmp_path / "state.json"
    broken = tmp_path / "state.json.broken"
    body = '{"schema_version": 1, "installs": [{"game": "wow-wotlk", "server_dir": "C:/gone"}]}'
    broken.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
    # Beside it, the list an older build saved after the player removed every server:
    # nothing else here would refuse the `.broken`, so this rule alone has to.
    _v0890_save(path, [])

    assert load_state(path).installs == []
    assert broken.read_bytes() == b"\xef\xbb\xbf" + body.encode("utf-8"), "it was touched"
    assert not (tmp_path / "state.json.restored").exists()


@pytest.mark.parametrize("since", ["v0.9.16", "this build", "this build, every server removed"])
def test_a_broken_file_a_newer_build_has_saved_over_since_is_left_alone(
    tmp_path: Path, since: str
) -> None:
    """After the downgrade, a build that READS `.broken` has saved state.json: the player
    rebuilt the list there, and what `.broken` holds would bring back servers removed since."""
    path = _downgrade(tmp_path)
    rebuilt = AppState(installs=[KnownInstall(game="wow-wotlk", server_dir=tmp_path / "wotlk")])
    if since.endswith("removed"):
        rebuilt = AppState()
    if since == "v0.9.16":
        _save_as_v0916(rebuilt, path)
    else:
        save_state(rebuilt, path)

    assert load_state(path) == rebuilt
    assert (tmp_path / "state.json.broken").is_file(), "it was renamed"
    assert not (tmp_path / "state.json.restored").exists()


def test_a_missing_state_file_beside_a_readable_broken_one_is_restored(tmp_path: Path) -> None:
    """The older build never saved after moving the file aside."""
    path = _downgrade(tmp_path)
    assert not path.exists()

    assert load_state(path) == _three_installs(tmp_path)
    assert path.is_file()


def test_a_broken_file_this_build_cannot_read_either_is_left_alone(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    (tmp_path / "state.json.broken").write_text("{not json", encoding="utf-8")
    save_state(AppState(installs=[KnownInstall(game="wow-tbc", server_dir=tmp_path)]), path)

    assert [i.game for i in load_state(path).installs] == ["wow-tbc"]
    assert (tmp_path / "state.json.broken").read_text(encoding="utf-8") == "{not json"


def test_the_smoke_run_restores_nothing_and_writes_nothing(tmp_path: Path) -> None:
    """`repair=False` is the staged build's proof run: it must not touch the player's files."""
    path = _downgrade(tmp_path)
    _v0890_save(path, [])
    before = path.read_text(encoding="utf-8")

    assert load_state(path, repair=False).installs == []
    assert path.read_text(encoding="utf-8") == before
    assert (tmp_path / "state.json.broken").is_file()


def test_a_damaged_state_file_does_not_take_the_name_of_the_broken_one_being_restored(
    tmp_path: Path,
) -> None:
    """Review round 2: state.json itself unreadable. It goes to `.damaged`, never over the
    `.broken` the servers are about to come back from."""
    path = _downgrade(tmp_path)
    path.write_text("{half a file", encoding="utf-8")

    assert load_state(path) == _three_installs(tmp_path)
    assert (tmp_path / "state.json.damaged").read_text(encoding="utf-8") == "{half a file"
    assert "play_client_dir" in (tmp_path / "state.json.restored").read_text(encoding="utf-8")


def test_a_restore_that_cannot_be_saved_keeps_the_broken_file_for_the_next_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`.broken` is retired only after the merged state is on disk; otherwise it is the
    one copy of those servers left. The damaged state.json is the harshest case."""
    path = _downgrade(tmp_path)
    path.write_text("{half a file", encoding="utf-8")
    real_save = state_module.save_state

    def full_disk(state: AppState, path: Path | None = None) -> Path:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(state_module, "save_state", full_disk)
    assert load_state(path) == _three_installs(tmp_path), "this session still sees them"
    assert "play_client_dir" in (tmp_path / "state.json.broken").read_text(encoding="utf-8")
    assert not (tmp_path / "state.json.restored").exists()

    monkeypatch.setattr(state_module, "save_state", real_save)
    assert load_state(path) == _three_installs(tmp_path), "the next start restores them"
    assert (tmp_path / "state.json.restored").is_file()
