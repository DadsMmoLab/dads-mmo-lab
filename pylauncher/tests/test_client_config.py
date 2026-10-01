"""Tests for `yulon.client_config` (T181 b/c): the ready-to-play client's Config.wtf.

Every test makes a real ready-to-play client with `play_client.create()` from a
small fake WoW client under `tmp_path`, so the folder carries step (a)'s marker
and its `WTF/Config.wtf` is the folder's own copy, exactly as Play finds it.
Each negative test breaks one rule and nothing else.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from yulon import client_config, play_client
from yulon.catalog.catalog import ConfigWtf

CENTURION = ConfigWtf(
    always={"realmList": "127.0.0.1", "realmName": "Centurion", "hwDetect": "0"},
    seed={"gxWindow": "1", "gxMaximize": "1"},
)


def fake_client(root: Path) -> Path:
    c = root / "WoW"
    (c / "Data" / "enUS").mkdir(parents=True)
    (c / "Data" / "deDE").mkdir(parents=True)
    (c / "Data" / "common.MPQ").write_bytes(b"mpq" * 100)
    (c / "Data" / "enUS" / "realmlist.wtf").write_text("set realmlist logon.example\n")
    (c / "Data" / "deDE" / "REALMLIST.WTF").write_text("set realmlist logon.example\n")
    (c / "Wow.exe").write_bytes(b"MZexe")
    (c / "WTF").mkdir()
    (c / "WTF" / "Config.wtf").write_bytes(b'SET locale "enUS"\n')
    return c


def make_play(tmp_path: Path) -> tuple[Path, Path]:
    original = fake_client(tmp_path)
    play = tmp_path / "WoW (Yu'lon)"
    play_client.create(
        original,
        play,
        game="wow-wotlk",
        server_dir=tmp_path / "srv",
        allow_full_copy=False,
        reflink=lambda src, dst: False,
    )
    return original, play


def config_of(play: Path) -> Path:
    return play / "WTF" / "Config.wtf"


def test_a_missing_config_wtf_is_created_with_the_always_and_seed_keys(tmp_path: Path) -> None:
    _, play = make_play(tmp_path)
    config_of(play).unlink()
    (play / "WTF").rmdir()

    written = client_config.merge_config_wtf(play, CENTURION, first_run=True)

    assert written == config_of(play)
    assert written.read_bytes().splitlines() == [
        b'SET realmList "127.0.0.1"',
        b'SET realmName "Centurion"',
        b'SET hwDetect "0"',
        b'SET gxWindow "1"',
        b'SET gxMaximize "1"',
    ]


def test_an_always_key_overwrites_the_existing_line_whatever_its_case(tmp_path: Path) -> None:
    _, play = make_play(tmp_path)
    config_of(play).write_bytes(
        b'SET locale "enUS"\nSET realmlist "logon.example"\nSET hwdetect "1"\n'
    )

    client_config.merge_config_wtf(play, CENTURION, first_run=False)

    assert config_of(play).read_bytes() == (
        b'SET locale "enUS"\n'
        b'SET realmlist "127.0.0.1"\n'
        b'SET hwdetect "0"\n'
        b'SET realmName "Centurion"\n'
    )


def test_an_always_key_set_twice_in_the_file_is_changed_on_every_line(tmp_path: Path) -> None:
    _, play = make_play(tmp_path)
    config_of(play).write_bytes(b'SET realmList "a"\nSET gamma "1"\nSET realmList "b"\n')

    client_config.merge_config_wtf(
        play, ConfigWtf(always={"realmList": "127.0.0.1"}), first_run=False
    )

    assert config_of(play).read_bytes() == (
        b'SET realmList "127.0.0.1"\nSET gamma "1"\nSET realmList "127.0.0.1"\n'
    )


def test_a_seed_key_is_not_added_after_the_first_run(tmp_path: Path) -> None:
    _, play = make_play(tmp_path)

    client_config.merge_config_wtf(play, ConfigWtf(seed={"gxWindow": "1"}), first_run=False)

    assert config_of(play).read_bytes() == b'SET locale "enUS"\n'


def test_a_seed_key_the_player_already_set_keeps_the_players_value(tmp_path: Path) -> None:
    _, play = make_play(tmp_path)
    config_of(play).write_bytes(b'SET gxWindow "0"\n')

    client_config.merge_config_wtf(play, CENTURION, first_run=True)

    lines = config_of(play).read_bytes().splitlines()
    assert lines[0] == b'SET gxWindow "0"'
    assert b'SET gxWindow "1"' not in lines
    assert b'SET gxMaximize "1"' in lines


def test_a_lower_case_set_line_counts_as_the_key_being_present(tmp_path: Path) -> None:
    """WoW runs Config.wtf as console commands, and `set` is as good as `SET` to it.

    Not recognising the line would append a seed after it, and the later line
    wins in WoW: the player's own choice would be overridden.
    """
    _, play = make_play(tmp_path)
    config_of(play).write_bytes(b'set gxWindow "0"\n')

    client_config.merge_config_wtf(play, ConfigWtf(seed={"gxWindow": "1"}), first_run=True)

    assert config_of(play).read_bytes() == b'set gxWindow "0"\n'


def test_a_crlf_file_stays_crlf_on_every_line_including_the_added_ones(tmp_path: Path) -> None:
    _, play = make_play(tmp_path)
    config_of(play).write_bytes(b'SET locale "enUS"\r\nSET realmList "old"\r\n')

    client_config.merge_config_wtf(play, CENTURION, first_run=True)

    raw = config_of(play).read_bytes()
    assert raw.count(b"\r\n") == 6
    assert raw.count(b"\n") == 6
    assert raw.startswith(b'SET locale "enUS"\r\nSET realmList "127.0.0.1"\r\n')


def test_an_lf_file_gains_no_carriage_return(tmp_path: Path) -> None:
    _, play = make_play(tmp_path)

    client_config.merge_config_wtf(play, CENTURION, first_run=True)

    raw = config_of(play).read_bytes()
    assert b"\r" not in raw
    assert raw.count(b"\n") == 6


def test_a_last_line_without_an_ending_gets_one_before_lines_are_added(tmp_path: Path) -> None:
    _, play = make_play(tmp_path)
    config_of(play).write_bytes(b'SET locale "enUS"\r\nSET gamma "1"')

    client_config.merge_config_wtf(
        play, ConfigWtf(always={"realmList": "127.0.0.1"}), first_run=False
    )

    assert config_of(play).read_bytes() == (
        b'SET locale "enUS"\r\nSET gamma "1"\r\nSET realmList "127.0.0.1"\r\n'
    )


def test_unrelated_lines_are_kept_in_their_order_byte_for_byte(tmp_path: Path) -> None:
    _, play = make_play(tmp_path)
    kept = (
        b'SET locale "enUS"\n'
        b"\n"
        b"# a comment the player wrote\n"
        b'  SET accountName "\xe6\xf8\xe5"\n'
        b'SET broken "no closing quote\n'
        b'SET gxResolution "1920x1080"   \n'
    )
    config_of(play).write_bytes(kept)

    client_config.merge_config_wtf(
        play, ConfigWtf(always={"realmList": "127.0.0.1"}), first_run=False
    )

    assert config_of(play).read_bytes() == kept + b'SET realmList "127.0.0.1"\n'


def test_a_second_merge_changes_nothing(tmp_path: Path) -> None:
    _, play = make_play(tmp_path)
    client_config.merge_config_wtf(play, CENTURION, first_run=True)
    first = config_of(play).read_bytes()

    client_config.merge_config_wtf(play, CENTURION, first_run=True)

    assert config_of(play).read_bytes() == first


def test_the_original_clients_config_wtf_is_never_changed(tmp_path: Path) -> None:
    original, play = make_play(tmp_path)

    client_config.merge_config_wtf(play, CENTURION, first_run=True)

    assert (original / "WTF" / "Config.wtf").read_bytes() == b'SET locale "enUS"\n'
    assert not os.path.samefile(original / "WTF" / "Config.wtf", config_of(play))


def test_a_hard_linked_config_wtf_is_refused_and_neither_name_changes(tmp_path: Path) -> None:
    original, play = make_play(tmp_path)
    config_of(play).unlink()
    os.link(original / "WTF" / "Config.wtf", config_of(play))

    with pytest.raises(play_client.PlayClientError, match="shares"):
        client_config.merge_config_wtf(play, CENTURION, first_run=True)

    assert (original / "WTF" / "Config.wtf").read_bytes() == b'SET locale "enUS"\n'
    assert os.path.samefile(original / "WTF" / "Config.wtf", config_of(play))
    assert sorted(p.name for p in (play / "WTF").iterdir()) == ["Config.wtf"]


def test_a_wtf_folder_that_is_a_link_is_refused(tmp_path: Path) -> None:
    original, play = make_play(tmp_path)
    config_of(play).unlink()
    (play / "WTF").rmdir()
    (play / "WTF").symlink_to(original / "WTF", target_is_directory=True)

    with pytest.raises(play_client.PlayClientError, match="link"):
        client_config.merge_config_wtf(play, CENTURION, first_run=True)

    assert sorted(p.name for p in (original / "WTF").iterdir()) == ["Config.wtf"]
    assert (original / "WTF" / "Config.wtf").read_bytes() == b'SET locale "enUS"\n'


def test_a_folder_without_the_marker_gets_no_config_wtf(tmp_path: Path) -> None:
    _, play = make_play(tmp_path)
    (play / play_client.MARKER).unlink()

    with pytest.raises(play_client.PlayClientError, match="marker|ready-to-play"):
        client_config.merge_config_wtf(play, CENTURION, first_run=True)

    assert config_of(play).read_bytes() == b'SET locale "enUS"\n'


# -- the locale realmlist.wtf files -----------------------------------------


def test_every_locale_realmlist_wtf_is_removed_whatever_its_case(tmp_path: Path) -> None:
    _, play = make_play(tmp_path)

    removed = client_config.remove_locale_realmlists(play)

    assert removed == (
        play / "Data" / "deDE" / "REALMLIST.WTF",
        play / "Data" / "enUS" / "realmlist.wtf",
    )
    assert sorted(p.name for p in (play / "Data" / "enUS").iterdir()) == []
    assert sorted(p.name for p in (play / "Data" / "deDE").iterdir()) == []


def test_the_originals_realmlist_wtf_files_are_kept(tmp_path: Path) -> None:
    original, play = make_play(tmp_path)

    client_config.remove_locale_realmlists(play)

    assert (original / "Data" / "enUS" / "realmlist.wtf").is_file()
    assert (original / "Data" / "deDE" / "REALMLIST.WTF").is_file()


def test_only_realmlist_wtf_in_a_locale_folder_is_removed(tmp_path: Path) -> None:
    _, play = make_play(tmp_path)
    enus = play / "Data" / "enUS"
    (enus / "realmlist.wtf.bak").write_text("keep")
    (enus / "realmlist.txt").write_text("keep")
    (play / "Data" / "realmlist.wtf").write_text("keep")
    (play / "realmlist.wtf").write_text("keep")
    (enus / "sub").mkdir()
    (enus / "sub" / "realmlist.wtf").write_text("keep")

    client_config.remove_locale_realmlists(play)

    assert sorted(p.name for p in enus.iterdir()) == ["realmlist.txt", "realmlist.wtf.bak", "sub"]
    assert (play / "Data" / "realmlist.wtf").is_file()
    assert (play / "realmlist.wtf").is_file()
    assert (enus / "sub" / "realmlist.wtf").is_file()


def test_a_locale_folder_that_is_a_link_is_not_entered(tmp_path: Path) -> None:
    original, play = make_play(tmp_path)
    (play / "Data" / "deDE" / "REALMLIST.WTF").unlink()
    (play / "Data" / "deDE").rmdir()
    (play / "Data" / "deDE").symlink_to(original / "Data" / "deDE", target_is_directory=True)

    removed = client_config.remove_locale_realmlists(play)

    assert removed == (play / "Data" / "enUS" / "realmlist.wtf",)
    assert (original / "Data" / "deDE" / "REALMLIST.WTF").is_file()


def test_a_client_without_a_data_folder_has_nothing_to_remove(tmp_path: Path) -> None:
    _, play = make_play(tmp_path)
    shutil.rmtree(play / "Data")

    assert client_config.remove_locale_realmlists(play) == ()


def test_a_folder_without_the_marker_keeps_its_realmlist_wtf_files(tmp_path: Path) -> None:
    _, play = make_play(tmp_path)
    (play / play_client.MARKER).unlink()

    with pytest.raises(play_client.PlayClientError, match="marker|ready-to-play"):
        client_config.remove_locale_realmlists(play)

    assert (play / "Data" / "enUS" / "realmlist.wtf").is_file()
    assert (play / "Data" / "deDE" / "REALMLIST.WTF").is_file()
