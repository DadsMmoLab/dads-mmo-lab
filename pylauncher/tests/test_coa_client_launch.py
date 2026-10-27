"""T812 C7: the Conquest of Azeroth client launches as its own client.

`wow-coa`'s client is `Ascension.exe` (not `Wow.exe`), runs under Proton/Wine
with `WINEDLLOVERRIDES="divxtac=d"`, lands in Steam with those launch options,
shares its `*.MPQ.DISABLED`/`*.MPQ.ORIGINAL` archives by link instead of copying
2 GB, and points at this server's login port in `Data/enUS/realmlist.wtf`.
"""

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from yulon import networking, play_client, play_launch, steam
from yulon.catalog.catalog import load_catalog

COA = load_catalog().get("wow-coa")
WOTLK = load_catalog().get("wow-wotlk")


def _coa_client(root: Path) -> Path:
    client = root / "Ascension"
    (client / "Data" / "enUS").mkdir(parents=True)
    (client / "Ascension.exe").write_bytes(b"MZ the game")
    (client / "Data" / "patch-WB1.MPQ.DISABLED").write_bytes(b"MPQ disabled")
    (client / "Data" / "patch-B.MPQ.ORIGINAL").write_bytes(b"MPQ original")
    (client / "Data" / "common.MPQ").write_bytes(b"MPQ shared" * 100)
    return client


def _nothing_on_path(name: str) -> str | None:
    return None


def _spec(
    play: Path,
    tmp_path: Path,
    os_name: str,
    *,
    which: Callable[[str], str | None] = _nothing_on_path,
) -> play_launch.LaunchSpec:
    return play_launch.launch_spec(
        play,
        game=COA.id,
        server_dir=tmp_path / "servers" / "coa",
        os_name=os_name,
        home=tmp_path / "home",
        config_dir=tmp_path / "config",
        exe_names=COA.client.exe_names,
        wine_dll_overrides=COA.client.wine_dll_overrides,
        which=which,
    )


def test_the_coa_client_runs_ascension_exe_not_wow_exe(tmp_path: Path) -> None:
    play = _coa_client(tmp_path)
    (play / "Wow.exe").write_bytes(b"MZ the stock exe, also here")

    spec = _spec(play, tmp_path, "windows")

    assert spec.argv == (str(play / "Ascension.exe"),)

def test_a_missing_ascension_exe_names_that_exe(tmp_path: Path) -> None:
    play = tmp_path / "play"
    play.mkdir()

    with pytest.raises(play_launch.LaunchRefusal) as caught:
        _spec(play, tmp_path, "windows")

    assert "ascension.exe" in str(caught.value).casefold()
    assert "Refresh from your original client" in str(caught.value)

def test_proton_and_wine_get_the_dll_override(tmp_path: Path) -> None:
    play = _coa_client(tmp_path)
    proton_home = tmp_path / "home"
    tool = proton_home / ".local/share/Steam/steamapps/common/Proton 9.0"
    tool.mkdir(parents=True)
    (tool / "proton").write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    proton = play_launch.launch_spec(
        play,
        game=COA.id,
        server_dir=tmp_path / "servers" / "coa",
        os_name="linux",
        home=proton_home,
        config_dir=tmp_path / "config",
        exe_names=COA.client.exe_names,
        wine_dll_overrides=COA.client.wine_dll_overrides,
    )
    wine = play_launch.launch_spec(
        play,
        game=COA.id,
        server_dir=tmp_path / "servers" / "coa",
        os_name="linux",
        home=tmp_path / "no-proton-home",
        config_dir=tmp_path / "config",
        exe_names=COA.client.exe_names,
        wine_dll_overrides=COA.client.wine_dll_overrides,
        which=lambda name: "/usr/bin/wine",
    )

    assert proton.argv[2] == str(play / "Ascension.exe")
    assert proton.env["WINEDLLOVERRIDES"] == "divxtac=d"
    assert wine.argv == ("/usr/bin/wine", str(play / "Ascension.exe"))
    assert wine.env["WINEDLLOVERRIDES"] == "divxtac=d"
def test_windows_gets_no_override(tmp_path: Path) -> None:
    play = _coa_client(tmp_path)

    assert _spec(play, tmp_path, "windows").env == {}


def test_a_steam_shortcut_carries_the_override(tmp_path: Path) -> None:
    from datetime import datetime

    from tests.test_steam import _profile, _proton

    config = _profile(tmp_path)
    _proton(tmp_path)
    client = _coa_client(tmp_path)

    steam.SteamShortcuts(
        game="WoW Conquest of Azeroth",
        server_dir=tmp_path / "yulon-coa",
        client_dir=client,
        exe_names=COA.client.exe_names,
        wine_dll_overrides=COA.client.wine_dll_overrides,
        home=tmp_path,
        is_running=lambda: False,
        now=lambda: datetime(2026, 10, 27, 12, 0, 0),
        platform_id=lambda: "linux",
        launcher=lambda: ("/usr/bin/yulon", ""),
    ).add()

    root, _ = steam.vdf_parse((config / "shortcuts.vdf").read_bytes())
    entry = next(
        e for e in root["shortcuts"].values() if e["AppName"] == "WoW Conquest of Azeroth"
    )
    assert entry["Exe"] == f'"{client / "Ascension.exe"}"'
    assert entry["LaunchOptions"] == 'WINEDLLOVERRIDES="divxtac=d" %command%'


def test_a_disabled_mpq_is_linked_not_copied(tmp_path: Path) -> None:
    original = _coa_client(tmp_path)
    target = tmp_path / "CoA play"

    play_client.create(
        original,
        target,
        game=COA.id,
        server_dir=tmp_path / "srv",
        allow_full_copy=False,
        reflink=lambda _s, _d: False,
    )

    for name in ("patch-WB1.MPQ.DISABLED", "patch-B.MPQ.ORIGINAL", "common.MPQ"):
        assert os.path.samefile(original / "Data" / name, target / "Data" / name), name
    assert not os.path.samefile(original / "Ascension.exe", target / "Ascension.exe")


def test_coa_realmlist_lands_in_data_enus_with_the_port(tmp_path: Path) -> None:
    play = tmp_path / "play"
    (play / "Data" / "enUS").mkdir(parents=True)

    (written,) = networking.write_ready_to_play_realmlists(
        play, "127.0.0.1", auth_port=COA.ports.auth
    )

    assert written == play / "Data" / "enUS" / "realmlist.wtf"
    assert written.read_text(encoding="utf-8") == "set realmlist 127.0.0.1:3726\n"


def test_wotlk_still_finds_wow_exe(tmp_path: Path) -> None:
    client = tmp_path / "WoW"
    client.mkdir()
    (client / "Wow.exe").write_bytes(b"MZ")
    (client / "TurtleWoW.exe").write_bytes(b"MZ launcher")

    assert steam.client_executable(client).name == "Wow.exe"
    assert COA.client.exe_names == ("ascension.exe",)
    assert WOTLK.client.exe_names == ("wow.exe",)
    assert WOTLK.client.wine_dll_overrides is None


def test_the_build_check_reads_ascension_exe(tmp_path: Path) -> None:
    from yulon.catalog.families import clientdir

    client = _coa_client(tmp_path)

    assert steam.client_executable(client, names=COA.client.exe_names).name == "Ascension.exe"
    assert clientdir.build_check(client, COA.client, COA.client.exe_names) is None
    (client / "Wow.exe").write_bytes(b"MZ a stock exe nobody should read here")
    assert steam.client_executable(client, names=COA.client.exe_names).name == "Ascension.exe"
