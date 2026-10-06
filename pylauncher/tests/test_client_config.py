"""Tests for `yulon.client_config` (T181 b/c): the ready-to-play client's Config.wtf.

Every test makes a real ready-to-play client with `play_client.create()` from a
small fake WoW client under `tmp_path`, so the folder carries step (a)'s marker
and its `WTF/Config.wtf` is the folder's own copy, exactly as Play finds it.
Each negative test breaks one rule and nothing else.
"""

from __future__ import annotations

import errno
import logging
import os
import shutil
import stat
from pathlib import Path

import pytest

from tests.support_case import needs_case_sensitive_disk
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


def test_a_locale_folder_that_is_a_link_is_not_entered(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    original, play = make_play(tmp_path)
    (play / "Data" / "deDE" / "REALMLIST.WTF").unlink()
    (play / "Data" / "deDE").rmdir()
    (play / "Data" / "deDE").symlink_to(original / "Data" / "deDE", target_is_directory=True)

    with caplog.at_level(logging.WARNING):
        removed = client_config.remove_locale_realmlists(play)

    assert removed == (play / "Data" / "enUS" / "realmlist.wtf",)
    assert (original / "Data" / "deDE" / "REALMLIST.WTF").is_file()
    assert any(str(play / "Data" / "deDE") in r.getMessage() for r in caplog.records)


def test_a_client_without_a_data_folder_has_nothing_to_remove(tmp_path: Path) -> None:
    _, play = make_play(tmp_path)
    shutil.rmtree(play / "Data")

    assert client_config.remove_locale_realmlists(play) == ()


@needs_case_sensitive_disk
def test_the_locale_realmlists_under_a_lowercase_data_folder_are_removed(tmp_path: Path) -> None:
    """T261: a ready-to-play client made from a `data/` client has `data/`, not `Data/`."""
    _, play = make_play(tmp_path)
    (play / "Data").rename(play / "data")

    removed = client_config.remove_locale_realmlists(play)

    assert removed == (
        play / "data" / "deDE" / "REALMLIST.WTF",
        play / "data" / "enUS" / "realmlist.wtf",
    )


@needs_case_sensitive_disk
def test_a_lowercase_data_folder_that_is_a_link_is_not_entered(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    original, play = make_play(tmp_path)
    shutil.rmtree(play / "Data")
    (play / "data").symlink_to(original / "Data", target_is_directory=True)

    with caplog.at_level(logging.WARNING):
        assert client_config.remove_locale_realmlists(play) == ()

    assert (original / "Data" / "enUS" / "realmlist.wtf").is_file()
    assert any(str(play / "data") in r.getMessage() for r in caplog.records)


def test_a_folder_without_the_marker_keeps_its_realmlist_wtf_files(tmp_path: Path) -> None:
    _, play = make_play(tmp_path)
    (play / play_client.MARKER).unlink()

    with pytest.raises(play_client.PlayClientError, match="marker|ready-to-play"):
        client_config.remove_locale_realmlists(play)

    assert (play / "Data" / "enUS" / "realmlist.wtf").is_file()
    assert (play / "Data" / "deDE" / "REALMLIST.WTF").is_file()


def test_a_data_folder_that_is_a_link_is_not_entered_and_is_named(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    original, play = make_play(tmp_path)
    shutil.rmtree(play / "Data")
    (play / "Data").symlink_to(original / "Data", target_is_directory=True)

    with caplog.at_level(logging.WARNING):
        assert client_config.remove_locale_realmlists(play) == ()

    assert (original / "Data" / "enUS" / "realmlist.wtf").is_file()
    assert (original / "Data" / "deDE" / "REALMLIST.WTF").is_file()
    assert any(str(play / "Data") in r.getMessage() for r in caplog.records)


# -- Windows' read-only refusal, simulated ------------------------------------


def refuse_read_only_unlinks(monkeypatch: pytest.MonkeyPatch) -> None:
    """`Path.unlink` refuses a read-only realmlist.wtf, as Windows does and POSIX does not."""
    real = Path.unlink

    def unlink(self: Path, missing_ok: bool = False) -> None:
        if self.name.casefold() == "realmlist.wtf" and not self.lstat().st_mode & stat.S_IWRITE:
            raise PermissionError(errno.EACCES, "Access is denied", str(self))
        real(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)


def read_only(path: Path) -> None:
    os.chmod(path, stat.S_IMODE(path.lstat().st_mode) & ~0o222)


def test_a_read_only_locale_realmlist_of_its_own_is_made_writable_and_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = tmp_path / "WoW"
    _, play = make_play(tmp_path)
    for path in (
        original / "Data" / "enUS" / "realmlist.wtf",
        play / "Data" / "enUS" / "realmlist.wtf",
    ):
        read_only(path)
    refuse_read_only_unlinks(monkeypatch)

    removed = client_config.remove_locale_realmlists(play)

    assert play / "Data" / "enUS" / "realmlist.wtf" in removed
    assert not (play / "Data" / "enUS" / "realmlist.wtf").exists()
    mode = (original / "Data" / "enUS" / "realmlist.wtf").lstat().st_mode
    assert not mode & stat.S_IWRITE, "the original's read-only flag was cleared"


def test_a_read_only_locale_realmlist_shared_through_a_hard_link_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original, play = make_play(tmp_path)
    theirs = original / "Data" / "enUS" / "realmlist.wtf"
    mine = play / "Data" / "enUS" / "realmlist.wtf"
    mine.unlink()
    os.link(theirs, mine)
    read_only(theirs)
    refuse_read_only_unlinks(monkeypatch)

    with pytest.raises(play_client.PlayClientError, match="Play again"):
        client_config.remove_locale_realmlists(play)

    assert os.path.samefile(theirs, mine)
    assert not theirs.lstat().st_mode & stat.S_IWRITE, "the original's flag was cleared"


def test_a_refused_removal_names_the_files_already_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, play = make_play(tmp_path)
    locked = play / "Data" / "enUS" / "realmlist.wtf"
    real = Path.unlink

    def unlink(self: Path, missing_ok: bool = False) -> None:
        if self == locked:
            raise PermissionError(errno.EACCES, "in use by another process", str(self))
        real(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)

    with pytest.raises(play_client.PlayClientError) as refused:
        client_config.remove_locale_realmlists(play)

    assert str(locked) in str(refused.value)
    assert f"Already removed: {play / 'Data' / 'deDE' / 'REALMLIST.WTF'}." in str(refused.value)
    assert locked.is_file()


# -- the rename and its temporary file ---------------------------------------


def test_a_rename_refused_for_a_moment_is_tried_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, play = make_play(tmp_path)
    real = os.replace
    refusals = [PermissionError(errno.EACCES, "held by a virus scanner")] * 2
    slept: list[float] = []

    def replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        if refusals:
            raise refusals.pop()
        real(src, dst)

    monkeypatch.setattr(os, "replace", replace)

    client_config.merge_config_wtf(
        play, ConfigWtf(always={"realmList": "127.0.0.1"}), first_run=False, sleep=slept.append
    )

    assert config_of(play).read_bytes() == b'SET locale "enUS"\nSET realmList "127.0.0.1"\n'
    assert len(slept) == 2


def test_a_leftover_temporary_file_linked_to_the_original_is_not_written_through(
    tmp_path: Path,
) -> None:
    original, play = make_play(tmp_path)
    leftover = play / "WTF" / f"Config.wtf.{os.getpid()}.yulon-tmp"
    os.link(original / "WTF" / "Config.wtf", leftover)

    client_config.merge_config_wtf(play, CENTURION, first_run=True)

    assert (original / "WTF" / "Config.wtf").read_bytes() == b'SET locale "enUS"\n'
    assert b'SET realmList "127.0.0.1"' in config_of(play).read_bytes()
    assert not leftover.exists()


def test_a_cleanup_that_fails_does_not_hide_why_the_write_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, play = make_play(tmp_path)
    real_unlink = Path.unlink

    def replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        raise OSError(errno.EIO, "the disk said no")

    def unlink(self: Path, missing_ok: bool = False) -> None:
        if self.name.endswith(".yulon-tmp") and self.exists():
            raise OSError(errno.EBUSY, "cleanup refused too")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(Path, "unlink", unlink)

    with pytest.raises(OSError, match="the disk said no"):
        client_config.merge_config_wtf(play, CENTURION, first_run=True)

    assert config_of(play).read_bytes() == b'SET locale "enUS"\n'


# -- removing a key (T187: "Ask in the game") ------------------------------------------


def test_a_removed_key_loses_every_line_whatever_its_case_and_nothing_else_changes(
    tmp_path: Path,
) -> None:
    _, play = make_play(tmp_path)
    config_of(play).write_bytes(
        b'SET locale "enUS"\r\nSET accountName "BOB"\r\nSET gamma "1.2"\r\n'
        b'set accountname "ALICE"\r\nSET lastCharacterIndex "0"\r\n'
    )

    client_config.merge_config_wtf(play, ConfigWtf(), first_run=False, remove=["accountName"])

    assert config_of(play).read_bytes() == (
        b'SET locale "enUS"\r\nSET gamma "1.2"\r\nSET lastCharacterIndex "0"\r\n'
    )


def test_removing_a_key_that_is_not_there_writes_nothing(tmp_path: Path) -> None:
    _, play = make_play(tmp_path)
    before = config_of(play).stat()

    client_config.merge_config_wtf(play, ConfigWtf(), first_run=False, remove=["accountName"])

    after = config_of(play).stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


def test_removing_from_a_missing_config_wtf_creates_none(tmp_path: Path) -> None:
    _, play = make_play(tmp_path)
    config_of(play).unlink()

    client_config.merge_config_wtf(play, ConfigWtf(), first_run=False, remove=["accountName"])

    assert not config_of(play).exists()


def test_a_removal_from_a_hard_linked_config_wtf_is_refused(tmp_path: Path) -> None:
    original, play = make_play(tmp_path)
    config_of(play).unlink()
    (original / "WTF" / "Config.wtf").write_bytes(b'SET accountName "BOB"\n')
    os.link(original / "WTF" / "Config.wtf", config_of(play))

    with pytest.raises(play_client.PlayClientError):
        client_config.merge_config_wtf(play, ConfigWtf(), first_run=False, remove=["accountName"])

    assert (original / "WTF" / "Config.wtf").read_bytes() == b'SET accountName "BOB"\n'
