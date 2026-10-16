"""Tests for `yulon.single_launcher`: one section of the player's launcher, every other byte kept.

The fixture is the shape of a real `DDO_Launcher.ini` (DDON-Launcher 1.3): a UTF-8 BOM,
CRLF line endings, a public server, the launcher's `[General]`, `[Launcher]` and
`[LocalPlay]` sections.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from yulon.catalog.catalog import SingleEntry, load_catalog
from yulon.single_launcher import (
    BACKUP_SUFFIX,
    LauncherRefusal,
    point_launcher,
    section_lines,
)

DDON: SingleEntry = load_catalog().get_single("ddon")

THEIRS = (
    "[Dogma Rising]\r\n"
    "LobbyIP = dogmarising.org\r\n"
    "LPort = 52100\r\n"
    "DLIP = dogmarising.org\r\n"
    "DLPort = 52099\r\n"
    "\r\n"
    "[General]\r\n"
    "LobbyIP = dogmarising.org\r\n"
    "LPort = 52100\r\n"
    "\r\n"
    "[LocalPlay]\r\n"
    "AutoUpdateServer = True\r\n"
)
SHIPPED = b"\xef\xbb\xbf" + THEIRS.encode("utf-8")
"""The file as the launcher wrote it: BOM, CRLF."""

OURS_AT_LOOPBACK = (
    "[Yulon DDON]\r\n"
    "LobbyIP = 127.0.0.1\r\n"
    "LPort = 52100\r\n"
    "DLIP = 127.0.0.1\r\n"
    "DLPort = 52099\r\n"
)


def _client(tmp_path: Path, content: bytes = SHIPPED) -> Path:
    client = tmp_path / "client"
    client.mkdir()
    (client / "DDO_Launcher.ini").write_bytes(content)
    return client


def test_the_section_is_written_at_the_top_and_every_other_byte_is_kept(tmp_path: Path) -> None:
    client = _client(tmp_path)
    write = point_launcher(DDON, client, "127.0.0.1")
    assert write.changed
    after = (client / "DDO_Launcher.ini").read_bytes()
    assert after == b"\xef\xbb\xbf" + (OURS_AT_LOOPBACK + "\r\n" + THEIRS).encode("utf-8")


def test_the_file_as_it_was_is_kept_once_and_never_overwritten(tmp_path: Path) -> None:
    client = _client(tmp_path)
    backup = client / ("DDO_Launcher.ini" + BACKUP_SUFFIX)
    first = point_launcher(DDON, client, "127.0.0.1")
    assert first.backup == backup and backup.read_bytes() == SHIPPED
    second = point_launcher(DDON, client, "192.168.0.60")
    assert second.changed and second.backup is None
    assert backup.read_bytes() == SHIPPED, "the second write must not replace the original"


def test_a_second_press_updates_the_section_in_place(tmp_path: Path) -> None:
    """One entry, rewritten where it is: never a second [Yulon DDON] below the first."""
    client = _client(tmp_path)
    point_launcher(DDON, client, "127.0.0.1")
    point_launcher(DDON, client, "192.168.0.60")
    text = (client / "DDO_Launcher.ini").read_bytes().decode("utf-8-sig")
    assert text.count("[Yulon DDON]") == 1
    assert text == OURS_AT_LOOPBACK.replace("127.0.0.1", "192.168.0.60") + "\r\n" + THEIRS


def test_a_press_with_nothing_to_change_writes_nothing(tmp_path: Path) -> None:
    client = _client(tmp_path)
    point_launcher(DDON, client, "127.0.0.1")
    path = client / "DDO_Launcher.ini"
    before = path.stat().st_mtime_ns, path.read_bytes()
    again = point_launcher(DDON, client, "127.0.0.1")
    assert not again.changed
    assert (path.stat().st_mtime_ns, path.read_bytes()) == before


def test_our_section_between_two_others_keeps_their_spacing(tmp_path: Path) -> None:
    """Rewritten in place, the blank line before the next section stays where it was."""
    middle = "[A]\nx = 1\n\n[Yulon DDON]\nLobbyIP = 10.0.0.9\nOld = gone\n\n[B]\ny = 2\n"
    client = _client(tmp_path, middle.encode("utf-8"))
    point_launcher(DDON, client, "127.0.0.1")
    assert (client / "DDO_Launcher.ini").read_text(encoding="utf-8") == (
        "[A]\nx = 1\n\n" + OURS_AT_LOOPBACK.replace("\r\n", "\n") + "\n[B]\ny = 2\n"
    )


def test_a_file_without_a_bom_and_with_lf_endings_keeps_both(tmp_path: Path) -> None:
    client = _client(tmp_path, THEIRS.replace("\r\n", "\n").encode("utf-8"))
    point_launcher(DDON, client, "127.0.0.1")
    after = (client / "DDO_Launcher.ini").read_bytes()
    assert not after.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in after


def test_no_client_folder_is_refused(tmp_path: Path) -> None:
    with pytest.raises(LauncherRefusal, match="Choose your DDON client folder"):
        point_launcher(DDON, None, "127.0.0.1")


def test_a_client_without_the_launcher_file_is_refused_and_nothing_is_made(
    tmp_path: Path,
) -> None:
    client = tmp_path / "client"
    client.mkdir()
    with pytest.raises(LauncherRefusal, match="Open DDON-Launcher from that folder once"):
        point_launcher(DDON, client, "127.0.0.1")
    assert list(client.iterdir()) == []


def test_our_section_twice_is_refused_and_the_file_left_alone(tmp_path: Path) -> None:
    twice = b"[Yulon DDON]\r\nLobbyIP = 1.1.1.1\r\n\r\n[yulon ddon]\r\nLobbyIP = 2.2.2.2\r\n"
    client = _client(tmp_path, twice)
    with pytest.raises(LauncherRefusal, match="more than once"):
        point_launcher(DDON, client, "127.0.0.1")
    assert (client / "DDO_Launcher.ini").read_bytes() == twice
    assert not (client / ("DDO_Launcher.ini" + BACKUP_SUFFIX)).exists()


def test_a_file_that_is_not_text_is_refused_and_left_alone(tmp_path: Path) -> None:
    binary = b"\xff\xfe[\x00G\x00]\x00"
    client = _client(tmp_path, binary)
    with pytest.raises(LauncherRefusal, match="not text"):
        point_launcher(DDON, client, "127.0.0.1")
    assert (client / "DDO_Launcher.ini").read_bytes() == binary


def test_the_section_is_filled_from_the_catalog(tmp_path: Path) -> None:
    assert section_lines(DDON, "192.168.0.60") == [
        "[Yulon DDON]",
        "LobbyIP = 192.168.0.60",
        "LPort = 52100",
        "DLIP = 192.168.0.60",
        "DLPort = 52099",
    ]


def test_a_launcher_value_may_not_spell_the_account() -> None:
    """A launcher asks for the account at its own login; the catalog cannot write one in."""
    raw = DDON.model_dump()
    raw["play"]["launcher"]["keys"]["Token"] = "{{ACCOUNT}}"
    with pytest.raises(ValidationError, match="ACCOUNT"):
        SingleEntry.model_validate(raw)


@pytest.mark.parametrize("bad", ["../DDO_Launcher.ini", "C:/DDO_Launcher.ini", "a\\b.ini"])
def test_the_launcher_file_stays_inside_the_client_folder(bad: str) -> None:
    raw = DDON.model_dump()
    raw["play"]["launcher"]["file"] = bad
    with pytest.raises(ValidationError, match="play.launcher.file"):
        SingleEntry.model_validate(raw)
