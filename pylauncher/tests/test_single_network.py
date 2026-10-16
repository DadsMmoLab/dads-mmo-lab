"""Tests for a single-container server's address and firewall (`yulon.single_network`)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from yulon import platform, single_network
from yulon.catalog.catalog import SingleEntry, load_catalog
from yulon.single_network import AddressFileError, Reach

DDON: SingleEntry = load_catalog().get_single("ddon")
BOM = chr(0xFEFF)
HEADER = "#Id,Name,Brief,MaxLoginNum,IpAddress,Port,IsHide,RpcPort,RpcAuth,LoginId,PreventLogin"
ROW = "10,Local Host,Brief,1000,127.0.0.1,52000,false,52099,AuthToken,1,FALSE"


def _server(tmp_path: Path, text: str) -> Path:
    assert DDON.advertise is not None
    target = tmp_path / DDON.advertise.dest
    target.parent.mkdir(parents=True)
    target.write_bytes(text.encode("utf-8"))
    return tmp_path


def test_reading_skips_the_mark_and_the_hash_and_finds_the_column(tmp_path: Path) -> None:
    server = _server(tmp_path, BOM + HEADER + "\n" + ROW)
    assert single_network.read_address(DDON, server) == "127.0.0.1"


def test_writing_changes_only_the_column_and_keeps_everything_else_byte_for_byte(
    tmp_path: Path,
) -> None:
    second = ROW.replace("10,", "11,", 1)
    original = BOM + HEADER + "\r\n" + ROW + "\r\n" + second
    server = _server(tmp_path, original)

    assert single_network.write_address(DDON, server, "192.168.0.60") is True

    assert DDON.advertise is not None
    written = (server / DDON.advertise.dest).read_bytes().decode("utf-8")
    assert written == original.replace("127.0.0.1", "192.168.0.60")
    assert written.startswith(BOM)
    assert single_network.read_address(DDON, server) == "192.168.0.60"


def test_writing_the_address_already_there_changes_nothing(tmp_path: Path) -> None:
    server = _server(tmp_path, BOM + HEADER + "\n" + ROW)
    assert DDON.advertise is not None
    before = (server / DDON.advertise.dest).stat().st_mtime_ns
    assert single_network.write_address(DDON, server, "127.0.0.1") is False
    assert (server / DDON.advertise.dest).stat().st_mtime_ns == before


@pytest.mark.parametrize("address", ["ddon.example.com", "::1", "300.1.1.1", ""])
def test_only_an_ipv4_address_is_advertised(tmp_path: Path, address: str) -> None:
    server = _server(tmp_path, BOM + HEADER + "\n" + ROW)
    with pytest.raises(AddressFileError, match="not an IPv4 address"):
        single_network.write_address(DDON, server, address)


def test_a_header_without_the_column_is_refused_rather_than_guessed(tmp_path: Path) -> None:
    server = _server(tmp_path, "#Id,Name,Port\n10,Local Host,52000")
    with pytest.raises(AddressFileError, match="no IpAddress column"):
        single_network.write_address(DDON, server, "192.168.0.60")


def test_a_quoted_field_is_refused_rather_than_split_wrongly(tmp_path: Path) -> None:
    server = _server(tmp_path, HEADER + "\n" + ROW.replace("Local Host", '"Local, Host"'))
    with pytest.raises(AddressFileError, match="quoted field"):
        single_network.write_address(DDON, server, "192.168.0.60")


def test_the_choices_offer_this_network_only_when_there_is_one() -> None:
    assert single_network.reaches(lambda: None) == (
        Reach("loopback", "Only this computer", "127.0.0.1"),
    )
    assert single_network.reaches(lambda: "192.168.0.60")[1] == Reach(
        "lan", "Computers on this network (192.168.0.60)", "192.168.0.60"
    )


def test_firewall_rules_are_one_per_port_on_windows_and_none_elsewhere() -> None:
    commands = single_network.firewall_commands(DDON, "windows")
    assert [c[:5] for c in commands] == [["netsh", "advfirewall", "firewall", "add", "rule"]] * 3
    assert "name=Yulon DDON 52100" in commands[0]
    assert single_network.firewall_commands(DDON, "linux") == []


def _proc(argv: list[str], rc: int, out: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, rc, out, "")


def test_rules_already_there_are_not_added_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    """netsh adds a second rule of one name without a word; every Apply would grow the list."""
    ran: list[list[str]] = []

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        ran.append(argv)
        return _proc(argv, 0)

    report = single_network.open_firewall(DDON, "windows", run=run)
    assert report.already == (52100, 52000, 52099)
    assert report.added == () and report.skipped == ()
    assert all("show" in argv for argv in ran)


def test_missing_rules_are_added_behind_one_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    results = tmp_path / "results.json"
    results.write_text(json.dumps([{"rc": 0, "out": "Ok."}, {"rc": 1, "out": "denied"}]))
    monkeypatch.setattr(
        platform, "elevated_batch", lambda _backend, cmds: (["powershell.exe", "batch"], results)
    )
    ran: list[list[str]] = []

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        ran.append(argv)
        if "show" in argv:
            return _proc(argv, 0 if "name=Yulon DDON 52100" in argv else 1)
        return _proc(argv, 0)

    report = single_network.open_firewall(DDON, "windows", run=run)

    assert report.already == (52100,)
    assert report.added == (52000,)
    assert len(report.skipped) == 1 and "denied" in report.skipped[0]
    assert ran.count(["powershell.exe", "batch"]) == 1
    assert not results.exists(), "the batch's result file is removed"


def test_a_declined_prompt_says_so_and_adds_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    results = tmp_path / "results.json"
    monkeypatch.setattr(
        platform, "elevated_batch", lambda _backend, cmds: (["powershell.exe", "batch"], results)
    )

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        if "show" in argv:
            return _proc(argv, 1)
        return _proc(argv, platform.UAC_DECLINED)

    report = single_network.open_firewall(DDON, "windows", run=run)
    assert report.added == ()
    assert report.skipped == (platform.UAC_DECLINED_TEXT,)
