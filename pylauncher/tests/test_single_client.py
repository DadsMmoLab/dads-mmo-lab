"""Tests for the player's own client as its installer left it (`yulon.single_client`).

The registry, the elevated batch and the commands it runs are fakes; the write probe
is real, against folders under `tmp_path`, because it is the check Play depends on.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from yulon import platform
from yulon.catalog.catalog import SingleEntry, load_catalog
from yulon.single_client import (
    DECLINED,
    GRANT,
    PROBE,
    FoundClient,
    client_version,
    describe,
    find_client,
    grant,
    grant_commands,
    unwritable,
    version_problem,
    writable,
)

FFXI: SingleEntry = load_catalog().get_single("ffxi")
SID = "S-1-5-21-1-2-3-1001"


def _client(tmp_path: Path, *, version: str = "30261006_0", folders: tuple[str, ...] = ()) -> Path:
    client = tmp_path / "FINAL FANTASY XI"
    client.mkdir(parents=True)
    (client / "FFXiMain.dll").write_bytes(b"MZ")
    (client / "patch2.cfg").write_bytes(
        b"\x00ROM/0/1.DAT 30100101_0\x00ROM/1/2.DAT " + version.encode() + b"\x00junk 2026_1\x00"
    )
    for name in folders:
        (client / name).mkdir()
    return client


def _registry(values: dict[str, str]):  # type: ignore[no-untyped-def]
    def read(key: str, subkey: str, value: str) -> str | None:
        assert (subkey, value) == ("InstallFolder", "0001")
        return values.get(key)

    return read


# -- finding the client ------------------------------------------------------------------


def test_the_registry_names_the_client_and_its_language(tmp_path: Path) -> None:
    client = _client(tmp_path)
    found = find_client(
        FFXI, os_name="windows", read=_registry({"SOFTWARE\\PlayOnlineUS": str(client) + "\\"})
    )
    assert found == FoundClient(folder=client, lang="1", label="US")


def test_the_first_region_with_a_real_folder_wins(tmp_path: Path) -> None:
    client = _client(tmp_path)
    read = _registry(
        {
            "SOFTWARE\\PlayOnlineUS": str(tmp_path / "uninstalled"),
            "SOFTWARE\\PlayOnlineEU": str(client),
        }
    )
    found = find_client(FFXI, os_name="windows", read=read)
    assert found is not None and (found.lang, found.label) == ("2", "EU")


def test_no_registry_entry_and_no_windows_find_nothing(tmp_path: Path) -> None:
    assert find_client(FFXI, os_name="windows", read=_registry({})) is None
    client = _client(tmp_path)
    linux = find_client(
        FFXI, os_name="linux", read=_registry({"SOFTWARE\\PlayOnlineUS": str(client)})
    )
    assert linux is None


# -- its version ------------------------------------------------------------------


def test_the_version_is_the_newest_one_the_patch_list_names(tmp_path: Path) -> None:
    assert client_version(FFXI, _client(tmp_path, version="30261006_0")) == "30261006_0"


def test_a_client_as_new_as_the_server_or_newer_is_fine(tmp_path: Path) -> None:
    assert version_problem(FFXI, _client(tmp_path, version="30261006_0")) is None
    newer = tmp_path / "newer"
    newer.mkdir()
    assert version_problem(FFXI, _client(newer, version="30261110_1")) is None


def test_an_older_client_is_told_to_update_in_playonline(tmp_path: Path) -> None:
    problem = version_problem(FFXI, _client(tmp_path, version="30181221_0"))
    assert problem is not None
    assert "30181221_0" in problem and "30261006_0 or newer" in problem and "PlayOnline" in problem


def test_an_unreadable_version_never_refuses(tmp_path: Path) -> None:
    client = _client(tmp_path)
    (client / "patch2.cfg").unlink()
    assert client_version(FFXI, client) is None
    assert version_problem(FFXI, client) is None


def test_describe_says_which_client_will_start(tmp_path: Path) -> None:
    client = _client(tmp_path)
    found = FoundClient(folder=client, lang="1", label="US")
    assert describe(FFXI, find=lambda _e: found) == f"Client: {client} (US, version 30261006_0)"
    assert "not installed on this computer" in describe(FFXI, find=lambda _e: None)


# -- the folders the game writes ------------------------------------------------------------


def test_the_write_probe_writes_then_removes_its_file(tmp_path: Path) -> None:
    assert writable(tmp_path) is True
    assert not (tmp_path / PROBE).exists()
    assert writable(tmp_path / "missing") is False


def test_unwritable_names_the_missing_folders_in_catalog_order(tmp_path: Path) -> None:
    client = _client(tmp_path, folders=("SYS",))
    assert unwritable(FFXI, client) == (client / "USER", client / "TEMP")


def test_the_grant_makes_missing_folders_then_grants_modify_by_sid(tmp_path: Path) -> None:
    client = _client(tmp_path, folders=("USER",))
    commands = grant_commands([client / "USER", client / "SYS"], SID)
    assert commands == [
        ["icacls", str(client / "USER"), "/grant", f"*{SID}:{GRANT}"],
        ["cmd", "/c", "mkdir", str(client / "SYS")],
        ["icacls", str(client / "SYS"), "/grant", f"*{SID}:{GRANT}"],
    ]


def test_nothing_is_granted_off_windows_or_when_every_folder_is_writable(tmp_path: Path) -> None:
    client = _client(tmp_path, folders=("USER", "SYS", "TEMP"))

    def never(*_a: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("nothing should run")

    assert grant(FFXI, client, os_name="windows", run=never, batch=never).ok  # type: ignore[arg-type]
    assert grant(FFXI, _client(tmp_path / "x"), os_name="linux", run=never).ok  # type: ignore[arg-type]


def test_a_declined_prompt_starts_nothing_and_says_how_to_retry(tmp_path: Path) -> None:
    client = _client(tmp_path)
    results = tmp_path / "results.json"

    report = grant(
        FFXI,
        client,
        os_name="windows",
        user_sid=lambda: SID,
        batch=lambda _cmds: (["powershell.exe"], results),
        run=lambda argv: subprocess.CompletedProcess(argv, platform.UAC_DECLINED, "", ""),
    )
    assert report.problems == (DECLINED,)


def test_an_answered_prompt_is_checked_by_writing_again(tmp_path: Path) -> None:
    client = _client(tmp_path)
    results = tmp_path / "results.json"

    def elevated(argv: list[str]) -> subprocess.CompletedProcess[str]:
        # What the elevated batch does: make the folders, and say each command went well.
        for name in ("USER", "SYS", "TEMP"):
            (client / name).mkdir()
        results.write_text('[{"rc": 0, "out": ""}' + ', {"rc": 0, "out": ""}' * 5 + "]")
        return subprocess.CompletedProcess(argv, 0, "", "")

    report = grant(
        FFXI,
        client,
        os_name="windows",
        user_sid=lambda: SID,
        batch=lambda _cmds: (["powershell.exe"], results),
        run=elevated,
    )
    assert report.ok
    assert report.granted == (client / "USER", client / "SYS", client / "TEMP")
    assert not results.exists(), "the results file is removed once read"


def test_a_batch_that_did_not_report_hands_the_player_the_commands(tmp_path: Path) -> None:
    client = _client(tmp_path)
    results = tmp_path / "results.json"
    report = grant(
        FFXI,
        client,
        os_name="windows",
        user_sid=lambda: SID,
        batch=lambda _cmds: (["powershell.exe"], results),
        run=lambda argv: subprocess.CompletedProcess(argv, 0, "", ""),
    )
    assert not report.ok
    assert "icacls" in report.problems[0] and "administrator terminal" in report.problems[0]


@pytest.mark.parametrize("program", ["netsh", "powershell", "del"])
def test_the_admin_batch_runs_only_icacls_and_cmd(program: str) -> None:
    with pytest.raises(ValueError, match="runs only"):
        platform.elevated_admin_batch([[program, "x"]], is_admin=False)


def test_the_admin_batch_wraps_nothing_for_an_administrator() -> None:
    assert platform.elevated_admin_batch([["icacls", "C:\\x"]], is_admin=True) is None
    assert platform.elevated_admin_batch([], is_admin=False) is None
