"""Tests for Play through a loader (`single_play.loader_spec/start_loader`) and its launch.

The registry is a `find` fake and the client a folder under `tmp_path`; nothing is
started — `launch` and `grant` are fakes that record what Play asked of them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from yulon import loader_account, play_launch, single_client, single_play
from yulon.catalog.catalog import SingleEntry, load_catalog
from yulon.single_client import FoundClient, GrantReport
from yulon.single_play import PlayRefusal, loader_spec, start_loader

FFXI: SingleEntry = load_catalog().get_single("ffxi")


def _install(tmp_path: Path) -> Path:
    server_dir = tmp_path / "Yulon FFXI"
    (server_dir / "xiloader").mkdir(parents=True)
    (server_dir / "xiloader" / "xiloader.exe").write_bytes(b"MZ")
    return server_dir


def _client(tmp_path: Path, version: str = "30261006_0") -> Path:
    client = tmp_path / "FINAL FANTASY XI"
    client.mkdir()
    (client / "FFXiMain.dll").write_bytes(b"MZ")
    (client / "patch2.cfg").write_bytes(b"x " + version.encode() + b" y")
    return client


def _found(client: Path, lang: str = "1") -> Any:
    return lambda _e: FoundClient(folder=client, lang=lang, label="US")


def test_the_loader_is_started_with_the_server_and_the_clients_language(tmp_path: Path) -> None:
    server_dir = _install(tmp_path)
    client = _client(tmp_path)
    spec, found = loader_spec(
        FFXI, server_dir, host="127.0.0.1", os_name="windows", find=_found(client, lang="2")
    )
    exe = server_dir / "xiloader" / "xiloader.exe"
    assert spec.argv == (str(exe), "--server", "127.0.0.1", "--lang", "2")
    assert spec.cwd == exe.parent and dict(spec.env) == {}
    assert found == client


def test_off_windows_the_loader_is_refused_with_what_to_do(tmp_path: Path) -> None:
    with pytest.raises(PlayRefusal, match="on Windows only"):
        loader_spec(FFXI, _install(tmp_path), host="127.0.0.1", os_name="linux")


def test_no_client_in_the_registry_says_how_to_get_one(tmp_path: Path) -> None:
    with pytest.raises(PlayRefusal, match="Square Enix's installer"):
        loader_spec(
            FFXI, _install(tmp_path), host="127.0.0.1", os_name="windows", find=lambda _e: None
        )


def test_a_registry_folder_without_the_client_is_refused(tmp_path: Path) -> None:
    client = _client(tmp_path)
    (client / "FFXiMain.dll").unlink()
    with pytest.raises(PlayRefusal, match="no FFXiMain.dll"):
        loader_spec(
            FFXI, _install(tmp_path), host="127.0.0.1", os_name="windows", find=_found(client)
        )


def test_a_client_older_than_the_server_accepts_is_refused(tmp_path: Path) -> None:
    client = _client(tmp_path, version="30181221_0")
    with pytest.raises(PlayRefusal, match="30261006_0 or newer"):
        loader_spec(
            FFXI, _install(tmp_path), host="127.0.0.1", os_name="windows", find=_found(client)
        )


def test_a_missing_loader_says_install_fetches_it_again(tmp_path: Path) -> None:
    server_dir = tmp_path / "Yulon FFXI"
    server_dir.mkdir()
    with pytest.raises(PlayRefusal, match="xiloader is missing"):
        loader_spec(
            FFXI, server_dir, host="127.0.0.1", os_name="windows", find=_found(_client(tmp_path))
        )


def test_start_grants_first_then_launches(tmp_path: Path) -> None:
    order: list[str] = []
    spec = play_launch.LaunchSpec(argv=("xiloader.exe",), env={}, cwd=tmp_path)

    def grant(_e: SingleEntry, _c: Path) -> GrantReport:
        order.append("grant")
        return GrantReport(granted=(tmp_path / "USER",))

    elevated = start_loader(
        FFXI, spec, tmp_path, grant=grant, launch=lambda s: order.append(f"launch {s.argv[0]}")
    )
    assert order == ["grant", "launch xiloader.exe"]
    assert elevated is True, "a grant means Windows asked the player"


def test_a_failed_grant_starts_nothing(tmp_path: Path) -> None:
    spec = play_launch.LaunchSpec(argv=("xiloader.exe",), env={}, cwd=tmp_path)
    launched: list[object] = []
    with pytest.raises(PlayRefusal, match="said No"):
        start_loader(
            FFXI,
            spec,
            tmp_path,
            grant=lambda _e, _c: GrantReport(problems=(single_client.DECLINED,)),
            launch=launched.append,
        )
    assert launched == []


def test_launch_console_gives_the_loader_a_console_of_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(play_launch.platform, "detect", lambda: "windows")
    seen: dict[str, Any] = {}

    def popen(argv: list[str], **kwargs: Any) -> None:
        seen.update(kwargs, argv=argv)

    spec = play_launch.LaunchSpec(
        argv=("xiloader.exe", "--lang", "1"), env={"X": "1"}, cwd=tmp_path
    )
    play_launch.launch_console(spec, popen=popen)
    assert seen["argv"] == ["xiloader.exe", "--lang", "1"]
    assert seen["creationflags"] == (
        play_launch.CREATE_NEW_CONSOLE | play_launch.CREATE_NEW_PROCESS_GROUP
    )
    assert "stdin" not in seen and "stdout" not in seen, "the console's own stdio, no DEVNULL"
    assert seen["env"]["X"] == "1" and seen["cwd"] == str(tmp_path)


def test_play_on_the_tab_is_wired_to_these_functions() -> None:
    assert single_play.start_loader is start_loader


def test_an_empty_name_is_the_loaders_own_prompt_and_any_other_is_checked() -> None:
    assert single_play.check_loader_account(FFXI, "") is None
    assert single_play.check_loader_account(FFXI, "Shantotto") is None
    problem = single_play.check_loader_account(FFXI, "two words")
    assert problem is not None and "3 to 15 letters or digits" in problem


def test_sign_in_makes_the_account_on_the_auth_port_and_writes_the_login_file(
    tmp_path: Path,
) -> None:
    asked: list[dict[str, Any]] = []

    def ensure(game: str, _login: Any, **kwargs: Any) -> str:
        asked.append({"game": game, **kwargs})
        return "s3cret"

    path = single_play.sign_in(FFXI, "127.0.0.1", "Shantotto", config_dir=tmp_path, ensure=ensure)
    assert asked == [
        {
            "game": "ffxi",
            "host": "127.0.0.1",
            "port": 54231,
            "name": "Shantotto",
            "loader": "xiloader",
            "config_dir": tmp_path,
        }
    ]
    assert path == tmp_path / "accounts" / "ffxi-login.json"
    assert '"password": "s3cret"' in path.read_text(encoding="utf-8")


def test_a_refused_account_is_a_play_refusal(tmp_path: Path) -> None:
    def ensure(*_a: Any, **_k: Any) -> str:
        raise loader_account.AccountRefusal("Nothing answered on 127.0.0.1:54231")

    with pytest.raises(PlayRefusal, match="Nothing answered"):
        single_play.sign_in(FFXI, "127.0.0.1", "Shantotto", config_dir=tmp_path, ensure=ensure)
    assert not (tmp_path / "accounts" / "ffxi-login.json").exists()


def test_the_login_file_goes_on_the_command_line_and_the_password_never_does(
    tmp_path: Path,
) -> None:
    spec, _ = loader_spec(
        FFXI,
        _install(tmp_path),
        host="127.0.0.1",
        os_name="windows",
        find=_found(_client(tmp_path)),
    )
    login_file = tmp_path / "cfg" / "ffxi-login.json"
    signed = single_play.with_login(FFXI, spec, login_file)
    assert signed.argv == (*spec.argv, "--json", str(login_file))
    assert signed.cwd == spec.cwd
