"""T828: CoA's "Enable HD patches", the official launcher's option, in Yu'lon.

Off starts the client as `<exe> -hd 0` (read from the CoA launcher v0.7.6); on, the
default, adds nothing. The choice is kept per install in the server folder, read at
every Play, and reaches every way Yu'lon starts the CoA client: the Server tab's Play
(and the launcher window's, which is the same press) on Windows, Proton and Wine, and
the Steam shortcut's launch options on Linux.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from yulon import hd_patches, play_launch, steam
from yulon.catalog.catalog import CatalogEntry, Client, load_catalog
from yulon.ui.widgets.job import run_inline

CATALOG = load_catalog()
COA = CATALOG.get("wow-coa")
WOTLK = CATALOG.get("wow-wotlk")
OFF = ("-hd", "0")


def _server(tmp_path: Path) -> Path:
    server = tmp_path / "servers" / "coa"
    server.mkdir(parents=True, exist_ok=True)
    return server


def _coa_client(root: Path) -> Path:
    client = root / "Ascension"
    (client / "Data" / "enUS").mkdir(parents=True)
    (client / "Ascension.exe").write_bytes(b"MZ the game")
    return client


# -- the catalog -----------------------------------------------------------------


def test_the_coa_entry_turns_hd_off_with_exactly_hd_0() -> None:
    assert COA.client.hd_patches is not None
    assert COA.client.hd_patches.args_when_off == OFF


CLIENT: dict[str, Any] = {"version": "3.3.5a", "build": 12340, "hd_patches": {"args_when_off": []}}


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["-hd 0"],
        ["-hd", "0;rm"],
        ["$(x)"],
        ["a|b"],
        ["`x`"],
        ["'x'"],
        ['"x"'],
        ["a&b"],
        [""],
        ["a\nb"],
        [1],
    ],
)
def test_an_off_argument_that_is_not_one_plain_word_is_refused(args: list[object]) -> None:
    raw = copy.deepcopy(CLIENT)
    raw["hd_patches"]["args_when_off"] = args
    with pytest.raises(ValidationError):
        Client.model_validate(raw)


def test_hd_patches_takes_no_other_key() -> None:
    raw = copy.deepcopy(CLIENT)
    raw["hd_patches"] = {"args_when_off": ["-hd", "0"], "default": False}
    with pytest.raises(ValidationError):
        Client.model_validate(raw)


# -- the record ------------------------------------------------------------------


def test_on_is_the_default_and_adds_nothing(tmp_path: Path) -> None:
    server = _server(tmp_path)
    assert hd_patches.enabled(server) is True
    assert hd_patches.launch_args(COA.client, server) == ()


def test_off_is_saved_and_read_back_as_exactly_hd_0(tmp_path: Path) -> None:
    server = _server(tmp_path)
    hd_patches.save(server, False)
    assert hd_patches.enabled(server) is False
    assert hd_patches.launch_args(COA.client, server) == OFF
    record = json.loads((server / hd_patches.RECORD_FILE).read_text(encoding="utf-8"))
    assert record == {"schema_version": 1, "enabled": False}
    hd_patches.save(server, True)
    assert hd_patches.launch_args(COA.client, server) == ()
    assert [p.name for p in server.iterdir()] == [hd_patches.RECORD_FILE]


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        "null",
        "{}",
        '{"enabled": "false"}',
        '{"enabled": 0}',
        '{"enabled": null}',
        "\udcff",
    ],
)
def test_a_broken_record_reads_as_on(tmp_path: Path, text: str) -> None:
    server = _server(tmp_path)
    (server / hd_patches.RECORD_FILE).write_bytes(text.encode("utf-8", "surrogateescape"))
    assert hd_patches.enabled(server) is True
    assert hd_patches.launch_args(COA.client, server) == ()


def test_a_record_that_is_a_folder_reads_as_on(tmp_path: Path) -> None:
    server = _server(tmp_path)
    (server / hd_patches.RECORD_FILE).mkdir()
    assert hd_patches.enabled(server) is True


def test_an_entry_without_hd_patches_is_never_given_arguments(tmp_path: Path) -> None:
    server = _server(tmp_path)
    hd_patches.save(server, False)
    assert WOTLK.client.hd_patches is None
    assert hd_patches.launch_args(WOTLK.client, server) == ()


def test_a_failed_save_raises_and_leaves_the_record_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _server(tmp_path)
    hd_patches.save(server, False)

    def refuse(*_a: object, **_k: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("yulon.hd_patches.os.replace", refuse)
    with pytest.raises(OSError):
        hd_patches.save(server, True)
    assert hd_patches.enabled(server) is False
    assert [p.name for p in server.iterdir()] == [hd_patches.RECORD_FILE]


# -- the launch spec on every OS -------------------------------------------------


def _spec(play: Path, tmp_path: Path, os_name: str, **kw: Any) -> play_launch.LaunchSpec:
    return play_launch.launch_spec(
        play,
        game=COA.id,
        server_dir=_server(tmp_path),
        os_name=os_name,
        home=kw.pop("home", tmp_path / "home"),
        config_dir=tmp_path / "config",
        exe_names=COA.client.exe_names,
        wine_dll_overrides=COA.client.wine_dll_overrides,
        **kw,
    )


def _proton_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    tool = home / ".local/share/Steam/steamapps/common/Proton 9.0"
    tool.mkdir(parents=True)
    (tool / "proton").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    return home


def test_windows_proton_and_wine_all_carry_the_arguments_after_the_exe(tmp_path: Path) -> None:
    play = _coa_client(tmp_path)
    exe = str(play / "Ascension.exe")
    home = _proton_home(tmp_path)

    windows = _spec(play, tmp_path, "windows", args=OFF)
    proton = _spec(play, tmp_path, "linux", args=OFF, home=home)
    wine = _spec(
        play,
        tmp_path,
        "linux",
        args=OFF,
        home=tmp_path / "no-proton",
        which=lambda _n: "/usr/bin/wine",
    )
    mac = _spec(
        play,
        tmp_path,
        "macos",
        args=OFF,
        home=tmp_path / "no-proton",
        which=lambda _n: "/opt/wine",
    )

    assert windows.argv == (exe, "-hd", "0")
    assert proton.argv[1:] == ("run", exe, "-hd", "0")
    assert wine.argv == ("/usr/bin/wine", exe, "-hd", "0")
    assert mac.argv == ("/opt/wine", exe, "-hd", "0")
    assert {windows.cwd, proton.cwd, wine.cwd} == {play}


def test_no_arguments_leave_every_argv_as_it_was(tmp_path: Path) -> None:
    play = _coa_client(tmp_path)
    assert _spec(play, tmp_path, "windows").argv == (str(play / "Ascension.exe"),)


# -- the Steam shortcut ----------------------------------------------------------


def _steam_entry(tmp_path: Path, args: tuple[str, ...]) -> dict[str, Any]:
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
        launch_args=lambda: args,
        home=tmp_path,
        is_running=lambda: False,
        now=lambda: datetime(2026, 10, 29, 12, 0, 0),
        platform_id=lambda: "linux",
        launcher=lambda: ("/usr/bin/yulon", ""),
    ).add()
    root, _ = steam.vdf_parse((config / "shortcuts.vdf").read_bytes())
    return next(e for e in root["shortcuts"].values() if e["AppName"] == "WoW Conquest of Azeroth")


def test_a_steam_shortcut_made_with_hd_off_carries_hd_0_after_the_command(
    tmp_path: Path,
) -> None:
    entry = _steam_entry(tmp_path, OFF)
    assert entry["LaunchOptions"] == 'WINEDLLOVERRIDES="divxtac=d" %command% -hd 0'


def test_a_steam_shortcut_made_with_hd_on_is_as_before(tmp_path: Path) -> None:
    entry = _steam_entry(tmp_path, ())
    assert entry["LaunchOptions"] == 'WINEDLLOVERRIDES="divxtac=d" %command%'


def test_launch_options_with_arguments_and_no_overrides() -> None:
    assert steam._client_launch_options(None, OFF) == "%command% -hd 0"
    assert steam._client_launch_options(None, ()) == ""


def test_the_tab_s_steam_seam_reads_the_choice_at_the_press(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon.ui import controller_view as view_module

    monkeypatch.setattr("yulon.ui.controller_view.platform.detect", lambda: "linux")
    server = _server(tmp_path)
    seam = view_module._steam_seam(COA, server, None)
    assert seam is not None
    assert seam.launch_args() == ()
    hd_patches.save(server, False)
    assert seam.launch_args() == OFF


# -- the Server tab --------------------------------------------------------------


def _view(tmp_path: Path, entry: CatalogEntry = COA) -> Any:
    from yulon.ui.controller_view import ControllerServices, ControllerView

    client = tmp_path / "client"
    (client / "Data").mkdir(parents=True, exist_ok=True)
    services = ControllerServices.for_entry(entry, _server(tmp_path), client)
    services.set_play_client_dir = lambda _p: None
    return ControllerView(entry, services, status_poll_ms=0, job_runner=run_inline)


def test_the_box_is_on_the_coa_tab_only_ticked_by_default(qapp: object, tmp_path: Path) -> None:
    view = _view(tmp_path)
    check = view.hd_patches_check
    assert check is not None
    assert check.text() == "Enable HD patches"
    assert check.toolTip() == hd_patches.TIP
    assert check.isChecked()
    assert _view(tmp_path / "w", WOTLK).hd_patches_check is None


def test_unticking_saves_and_a_new_tab_reads_it_back(qapp: object, tmp_path: Path) -> None:
    view = _view(tmp_path)
    view.hd_patches_check.setChecked(False)
    assert hd_patches.enabled(_server(tmp_path)) is False
    assert "off from the next time you start the game" in view.play_label.text()
    again = _view(tmp_path)
    assert again.hd_patches_check is not None and not again.hd_patches_check.isChecked()
    again.hd_patches_check.setChecked(True)
    assert hd_patches.enabled(_server(tmp_path)) is True


def test_a_failed_save_puts_the_box_back_and_says_so(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    view = _view(tmp_path)

    def refuse(_server_dir: Path, _on: bool) -> None:
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(hd_patches, "save", refuse)
    view.hd_patches_check.setChecked(False)
    assert view.hd_patches_check.isChecked()
    assert "could not be saved" in view.play_label.text()
    assert hd_patches.enabled(_server(tmp_path)) is True


@pytest.mark.parametrize("off", [False, True])
def test_play_starts_the_client_with_hd_0_only_when_off(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, off: bool
) -> None:

    monkeypatch.setattr("yulon.ui.controller_view.platform.detect", lambda: "windows")
    started: list[play_launch.LaunchSpec] = []
    monkeypatch.setattr(play_launch, "launch", lambda spec, **_k: started.append(spec))
    view = _view(tmp_path)
    if off:
        view.hd_patches_check.setChecked(False)
    play = _coa_client(tmp_path / "play")
    view._launch(play)
    exe = str(play / "Ascension.exe")
    assert [s.argv for s in started] == [(exe, "-hd", "0") if off else (exe,)]


def test_play_on_linux_carries_hd_0_through_proton(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.setattr("yulon.ui.controller_view.platform.detect", lambda: "linux")
    monkeypatch.setattr("yulon.ui.controller_view.platform.config_dir", lambda: tmp_path / "config")
    script = tmp_path / "proton"
    monkeypatch.setattr(play_launch, "_proton", lambda _home: (script, tmp_path / "steam"))
    started: list[play_launch.LaunchSpec] = []
    monkeypatch.setattr(play_launch, "launch", lambda spec, **_k: started.append(spec))
    view = _view(tmp_path)
    view.hd_patches_check.setChecked(False)
    play = _coa_client(tmp_path / "play")
    view._launch(play)
    assert [s.argv for s in started] == [
        (str(script), "run", str(play / "Ascension.exe"), "-hd", "0")
    ]
    assert started[0].env["WINEDLLOVERRIDES"] == "divxtac=d"
