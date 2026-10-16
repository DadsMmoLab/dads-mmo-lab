"""Tests for `yulon.gaming_mode`: Add to Steam Deck (Gaming Mode)… for a single server.

The scripts are the Steam Deck session's, measured on Deck #2 on 2026-10-16 and handed over
with their sha256. The first test holds the shipped templates to those bytes: rendered with
Deck #2's own values, every script must come out exactly as it was measured there.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from yulon import gaming_mode, resources, single_play, steam
from yulon.catalog.catalog import SingleEntry, load_catalog

DDON: SingleEntry = load_catalog().get_single("ddon")
DFO: SingleEntry = load_catalog().get_single("dfo")
FIXED = datetime(2026, 10, 16, 14, 0, 0)

MEASURED = {
    "ddo-launcher.sh": "c1c40306969efacf94e2b04e01268eb718c731981a6cf8844ec7a447113779f7",
    "ddo-client.sh": "786c82aace33d64bf34e1af7aef84a1981498b350788aa65b3d2b4499ba0cc24",
    "ddo-stop.sh": "83d385376a27f803799e3024409f98bde8ba35acdfce8152cfaf7024fe781b9a",
}
LIB_SHA = "4b0cf60c6d858778c830ae205ff37e89831216cb4a73cf9dc2c5054e451163e9"
DECK_2 = {
    "HOME": "/home/deck",
    "SERVER_DIR": "/home/deck/yulon-ddon",
    "ACCOUNT": "mmodad",
    "PROTON_NAME": "GE-Proton10-34",
}
TITLE = "Dragon's Dogma Online"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _profile(home: Path) -> Path:
    config = home / ".local/share/Steam/userdata/18347166/config"
    config.mkdir(parents=True)
    (home / ".local/share/Steam/config").mkdir(parents=True)
    (home / ".local/share/Steam/config/config.vdf").write_text(
        '"InstallConfigStore"\n{\n\t"Software"\n\t{\n\t\t"Valve"\n\t\t{\n\t\t\t"Steam"\n'
        '\t\t\t{\n\t\t\t\t"AutoUpdateWindowEnabled"\t\t"0"\n\t\t\t}\n\t\t}\n\t}\n}\n',
        encoding="utf-8",
    )
    return config


def _proton(home: Path, name: str = "GE-Proton10-34") -> None:
    tool = home / ".local/share/Steam/compatibilitytools.d" / name
    tool.mkdir(parents=True)
    (tool / "proton").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    (tool / "compatibilitytool.vdf").write_text(
        '"compatibilitytools"\n{\n  "compat_tools"\n  {\n'
        f'    "{name}"\n    {{\n      "install_path" "."\n      "display_name" "{name}"\n'
        '      "from_oslist"  "windows"\n      "to_oslist"    "linux"\n    }\n  }\n}\n',
        encoding="utf-8",
    )


def _machine(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A home with one Steam profile and one GE-Proton, a DDON server folder and a client."""
    home = tmp_path / "home"
    _profile(home)
    _proton(home)
    server = home / "yulon-ddon"
    server.mkdir()
    client = home / "Games" / "DDON"
    client.mkdir(parents=True)
    (client / "DDO.exe").write_bytes(b"MZ")
    return home, server, client


def _gaming(
    home: Path, server: Path, *, running: bool = False, platform_id: str = "linux"
) -> gaming_mode.GamingMode:
    return gaming_mode.GamingMode(
        DDON,
        server,
        home=home,
        config_dir=home / ".local/share/yulon",
        is_running=lambda: running,
        now=lambda: FIXED,
        platform_id=lambda: platform_id,
    )


def _shortcut_entries(home: Path) -> dict[str, dict[str, object]]:
    path = home / ".local/share/Steam/userdata/18347166/config/shortcuts.vdf"
    root, _ = steam.vdf_parse(path.read_bytes())
    return {str(e["AppName"]): e for e in root["shortcuts"].values()}


# -- the scripts themselves ---------------------------------------------------------------


def test_with_deck_2s_values_every_script_is_the_one_measured_there() -> None:
    folder = resources.installers_dir() / "ddon" / "gaming-mode"
    for name, sha in MEASURED.items():
        rendered = gaming_mode.render((folder / name).read_text(encoding="utf-8"), DECK_2)
        assert _sha(rendered.encode("utf-8")) == sha, name
    lib = (resources.installers_dir() / gaming_mode.LIB_TEMPLATE).read_bytes()
    assert _sha(lib) == LIB_SHA
    assert gaming_mode.lib_version(lib.decode("utf-8")) == (1, 3, 0)


def test_a_marker_nothing_fills_is_an_error() -> None:
    with pytest.raises(ValueError, match="@ACCOUNT@"):
        gaming_mode.render("TOKEN=@ACCOUNT@", {"HOME": "/home/deck"})


def test_dockers_own_braces_are_left_alone() -> None:
    """Why the scripts use `@NAME@` and not `{{NAME}}`: they hold Docker's templates."""
    text = "docker inspect -f '{{.State.Running}}' \"$@\" @HOME@"
    assert gaming_mode.render(text, {"HOME": "/h"}) == (
        "docker inspect -f '{{.State.Running}}' \"$@\" /h"
    )


# -- one press ------------------------------------------------------------------------------


def test_a_press_writes_three_scripts_the_library_and_three_shortcuts(tmp_path: Path) -> None:
    home, server, client = _machine(tmp_path)
    config_vdf = home / ".local/share/Steam/config/config.vdf"
    before = config_vdf.read_bytes()
    report = _gaming(home, server).add(client, "mmodad")

    scripts = home / ".local/share/yulon/gaming-mode/ddon"
    assert report.scripts == (
        scripts / "ddo-launcher.sh",
        scripts / "ddo-client.sh",
        scripts / "ddo-stop.sh",
    )
    launcher_text = (scripts / "ddo-launcher.sh").read_text(encoding="utf-8")
    assert f"source {home}/dml-launcher-lib.sh" in launcher_text
    assert f'SERVER_DIR="${{SERVER_DIR:-{server}}}"' in launcher_text
    client_text = (scripts / "ddo-client.sh").read_text(encoding="utf-8")
    assert 'TOKEN="${TOKEN:-mmodad}"' in client_text
    assert 'PROTON_NAME="${PROTON_NAME:-GE-Proton10-34}"' in client_text
    assert "@" + "HOME@" not in launcher_text + client_text
    if os.name != "nt":
        for script in report.scripts:
            assert os.stat(script).st_mode & 0o777 == 0o755
    assert _sha((home / "dml-launcher-lib.sh").read_bytes()) == LIB_SHA
    assert "was installed (1.3.0)" in report.lib

    entries = _shortcut_entries(home)
    assert set(entries) == {f"{TITLE} Server", TITLE, f"{TITLE} Server Stop"}
    server_entry = entries[f"{TITLE} Server"]
    assert server_entry["Exe"] == '"/usr/bin/konsole"'
    assert server_entry["StartDir"] == '"/usr/bin/"'
    # Quoted for a shell: a home folder with a space in it stays one argument.
    assert shlex.split(str(server_entry["LaunchOptions"])) == [
        "--hold",
        "-e",
        "bash",
        str(scripts / "ddo-launcher.sh"),
    ]
    assert entries[TITLE]["Exe"] == f'"{scripts / "ddo-client.sh"}"'
    assert entries[f"{TITLE} Server Stop"]["Exe"] == f'"{scripts / "ddo-stop.sh"}"'
    assert config_vdf.read_bytes() == before, "no compatibility tool for any of the three"
    assert not report.replaced


def test_a_second_press_replaces_the_three_in_place_and_keeps_their_appids(
    tmp_path: Path,
) -> None:
    home, server, client = _machine(tmp_path)
    _gaming(home, server).add(client, "mmodad")
    first = {name: e["appid"] for name, e in _shortcut_entries(home).items()}
    report = _gaming(home, server).add(client, "other_name")
    second = {name: e["appid"] for name, e in _shortcut_entries(home).items()}
    assert report.replaced
    assert second == first
    assert report.backup is not None and report.backup.exists()


def test_a_newer_library_is_kept_and_an_older_or_unversioned_one_is_replaced(
    tmp_path: Path,
) -> None:
    home, server, client = _machine(tmp_path)
    lib = home / "dml-launcher-lib.sh"
    lib.write_text('#!/bin/bash\nDML_LIB_VERSION="1.4.0"\n', encoding="utf-8")
    report = _gaming(home, server).add(client, "mmodad")
    assert lib.read_text(encoding="utf-8") == '#!/bin/bash\nDML_LIB_VERSION="1.4.0"\n'
    assert "kept" in report.lib
    lib.write_text('#!/bin/bash\nDML_LIB_VERSION="1.2.0"\n', encoding="utf-8")
    report = _gaming(home, server).add(client, "mmodad")
    assert _sha(lib.read_bytes()) == LIB_SHA and "updated from 1.2.0 to 1.3.0" in report.lib
    lib.write_text("#!/bin/bash\necho old\n", encoding="utf-8")
    _gaming(home, server).add(client, "mmodad")
    assert _sha(lib.read_bytes()) == LIB_SHA


def test_the_prefixs_own_proton_is_the_one_the_client_falls_back_to(tmp_path: Path) -> None:
    from yulon import play_launch

    home, server, client = _machine(tmp_path)
    _proton(home, "GE-Proton9-27")
    prefix = play_launch.prefix_path(home / ".local/share/yulon", "ddon", server)
    prefix.mkdir(parents=True)
    (prefix / "version").write_text("GE-Proton9-27\n", encoding="utf-8")
    report = _gaming(home, server).add(client, "mmodad")
    assert 'PROTON_NAME="${PROTON_NAME:-GE-Proton9-27}"' in report.scripts[1].read_text(
        encoding="utf-8"
    )


def test_a_prefix_made_by_a_proton_that_is_gone_is_refused_and_nothing_is_written(
    tmp_path: Path,
) -> None:
    from yulon import play_launch

    home, server, client = _machine(tmp_path)
    prefix = play_launch.prefix_path(home / ".local/share/yulon", "ddon", server)
    prefix.mkdir(parents=True)
    (prefix / "version").write_text("GE-Proton8-1\n", encoding="utf-8")
    with pytest.raises(steam.SteamRefusal, match="made by 'GE-Proton8-1'"):
        _gaming(home, server).add(client, "mmodad")
    assert not (home / ".local/share/yulon/gaming-mode").exists()
    assert not (home / "dml-launcher-lib.sh").exists()


# -- refusals, all before the first byte -----------------------------------------------------


@pytest.mark.parametrize("platform_id", ["windows", "macos"])
def test_nothing_is_written_off_linux(tmp_path: Path, platform_id: str) -> None:
    home, server, client = _machine(tmp_path)
    with pytest.raises(steam.SteamRefusal, match="Steam Deck and Linux feature"):
        _gaming(home, server, platform_id=platform_id).add(client, "mmodad")
    assert not (home / "dml-launcher-lib.sh").exists()


def test_steam_running_is_refused_in_this_buttons_words(tmp_path: Path) -> None:
    home, server, client = _machine(tmp_path)
    with pytest.raises(steam.SteamRefusal) as caught:
        _gaming(home, server, running=True).add(client, "mmodad")
    message = str(caught.value)
    assert "Steam is running" in message
    assert gaming_mode.BUTTON in message and "press Add to Steam…" not in message
    assert not (home / ".local/share/yulon/gaming-mode").exists()


def test_no_client_folder_and_a_bad_account_are_refused(tmp_path: Path) -> None:
    home, server, client = _machine(tmp_path)
    with pytest.raises(steam.SteamRefusal, match="Choose your DDON client folder first"):
        _gaming(home, server).add(None, "mmodad")
    with pytest.raises(steam.SteamRefusal, match="no DDO.exe"):
        _gaming(home, server).add(home, "mmodad")
    with pytest.raises(steam.SteamRefusal, match="3 to 20 letters"):
        _gaming(home, server).add(client, "two words")
    assert not (home / "dml-launcher-lib.sh").exists()


def test_no_steam_profile_is_refused_in_this_buttons_words(tmp_path: Path) -> None:
    home = tmp_path / "home"
    server = home / "yulon-ddon"
    server.mkdir(parents=True)
    client = home / "client"
    client.mkdir()
    (client / "DDO.exe").write_bytes(b"MZ")
    with pytest.raises(steam.SteamRefusal) as caught:
        _gaming(home, server).add(client, "mmodad")
    assert "No Steam profile" in str(caught.value) and gaming_mode.BUTTON in str(caught.value)


def test_a_compatibility_tool_already_set_on_one_of_them_is_named(tmp_path: Path) -> None:
    """An appid kept from a hand-made shortcut may carry a Proton; Proton in Proton breaks."""
    home, server, client = _machine(tmp_path)
    _gaming(home, server).add(client, "mmodad")
    appid = int(_shortcut_entries(home)[TITLE]["appid"])
    config_vdf = home / ".local/share/Steam/config/config.vdf"
    text, _ = steam.compat_mapping(
        config_vdf.read_text(encoding="utf-8"), appid=appid, tool="GE-Proton10-34"
    )
    config_vdf.write_text(text, encoding="utf-8")
    report = _gaming(home, server).add(client, "mmodad")
    assert any(TITLE in note and "Compatibility" in note for note in report.notes)


# -- the seam the tab is handed ----------------------------------------------------------------


def test_the_seam_is_linux_only_and_only_for_a_game_with_scripts(tmp_path: Path) -> None:
    assert gaming_mode.seam_for(DDON, tmp_path, platform_id=lambda: "windows") is None
    assert gaming_mode.seam_for(DDON, tmp_path, platform_id=lambda: "macos") is None
    assert gaming_mode.seam_for(DDON, tmp_path, platform_id=lambda: "linux") is not None
    assert gaming_mode.seam_for(DFO, tmp_path, platform_id=lambda: "linux") is not None
    assert DDON.play is not None
    bare = DDON.model_copy(update={"play": DDON.play.model_copy(update={"gaming_mode": None})})
    assert gaming_mode.seam_for(bare, tmp_path, platform_id=lambda: "linux") is None


# -- DFO: the private network's scripts ----------------------------------------------------

DFO_TITLE = "Dungeon Fighter Online"
DFO_SCRIPTS = ("dfo-launcher.sh", "dfo-client.sh", "dfo-stop.sh")


def _dfo_machine(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A home with a Steam profile and NO GE-Proton: DFO's client finds Proton 11 itself."""
    home = tmp_path / "home"
    _profile(home)
    server = home / "yulon-dfo"
    server.mkdir()
    client = home / "Games" / "DFO 115"
    client.mkdir(parents=True)
    (client / "DFO.exe").write_bytes(b"MZ")
    return home, server, client


def _dfo(home: Path, server: Path) -> gaming_mode.GamingMode:
    return gaming_mode.GamingMode(
        DFO,
        server,
        home=home,
        config_dir=home / ".local/share/yulon",
        is_running=lambda: False,
        now=lambda: FIXED,
        platform_id=lambda: "linux",
    )


def test_dfo_needs_no_ge_proton_and_every_marker_is_filled(tmp_path: Path) -> None:
    home, server, client = _dfo_machine(tmp_path)
    report = _dfo(home, server).add(client, "mmodad")

    scripts = home / ".local/share/yulon/gaming-mode/dfo"
    assert report.scripts == tuple(scripts / name for name in DFO_SCRIPTS)
    texts = {path.name: path.read_text(encoding="utf-8") for path in report.scripts}
    for name, text in texts.items():
        assert gaming_mode.markers(text) == set(), name
        assert "\r" not in text, f"{name} must keep LF line endings"
        assert f'YULON_DATA="{home / ".local/share/yulon"}"' in text or name == "dfo-launcher.sh"
    launcher = texts["dfo-launcher.sh"]
    assert f"source {home}/dml-launcher-lib.sh" in launcher
    assert f'SCRIPTS="{scripts}"' in launcher
    assert f'D="${{D:-{server}}}"' in launcher
    assert 'ACCOUNT="${ACCOUNT:-mmodad}"' in texts["dfo-client.sh"]
    # The sniper note is DDON's: DFO runs on Proton 11 and SteamLinuxRuntime_4.
    assert not any("sniper" in note for note in report.notes)
    entries = _shortcut_entries(home)
    assert set(entries) == {f"{DFO_TITLE} Server", DFO_TITLE, f"{DFO_TITLE} Server Stop"}


@pytest.mark.skipif(shutil.which("bash") is None or os.name == "nt", reason="needs POSIX bash")
def test_every_rendered_dfo_script_is_valid_bash(tmp_path: Path) -> None:
    home, server, client = _dfo_machine(tmp_path)
    report = _dfo(home, server).add(client, "mmodad")
    for script in report.scripts:
        result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, f"{script.name}: {result.stderr}"


def test_a_value_a_script_cannot_quote_is_refused_and_nothing_is_written(
    tmp_path: Path,
) -> None:
    home, _server, client = _dfo_machine(tmp_path)
    server = home / "yulon $dfo"
    server.mkdir()
    with pytest.raises(steam.SteamRefusal, match="cannot quote"):
        _dfo(home, server).add(client, "mmodad")
    assert not (home / ".local/share/yulon/gaming-mode").exists()
    assert not (home / "dml-launcher-lib.sh").exists()


def test_check_values_allows_a_plain_backslash_and_refuses_the_ones_that_escape() -> None:
    assert gaming_mode.check_values({"SERVER_DIR": "/run/media/deck/SD card/DFO"}) is None
    assert gaming_mode.check_values({"SERVER_DIR": "C:\\Users\\x"}) is None
    for bad in ('a"b', "a$b", "a`b", "a\nb", "a\\\\b", "a\\"):
        assert gaming_mode.check_values({"SERVER_DIR": bad}) is not None, repr(bad)


def test_desktop_play_runs_the_rendered_client_script_with_the_tabs_own_values(
    tmp_path: Path,
) -> None:
    home, server, client = _dfo_machine(tmp_path)
    spec = gaming_mode.play_spec(
        DFO, client, account="mmodad", server_dir=server, home=home, config_dir=home / "y"
    )
    assert spec.argv[:2] == ("/bin/bash", "-c") and spec.argv[3] == "dfo-client.sh"
    assert gaming_mode.markers(spec.argv[2]) == set()
    assert "exec nsenter -t" in spec.argv[2]
    assert spec.env == {
        "YULON_ACCOUNT": "mmodad",
        "YULON_CLIENT_DIR": str(client),
        "YULON_SERVER_DIR": str(server),
    }
    assert not (home / "y" / "gaming-mode").exists(), "a Play writes nothing"


def test_desktop_play_refuses_as_play_does(tmp_path: Path) -> None:
    home, server, client = _dfo_machine(tmp_path)
    with pytest.raises(single_play.PlayRefusal, match="Choose"):
        gaming_mode.play_spec(DFO, None, account="mmodad", server_dir=server, home=home)
    with pytest.raises(single_play.PlayRefusal, match="no DFO.exe"):
        gaming_mode.play_spec(DFO, tmp_path, account="mmodad", server_dir=server, home=home)
    with pytest.raises(single_play.PlayRefusal, match="3 to 20"):
        gaming_mode.play_spec(DFO, client, account="a b", server_dir=server, home=home)
    with pytest.raises(single_play.PlayRefusal, match="no client"):
        gaming_mode.play_spec(DDON, client, account="mmodad", server_dir=server, home=home)
