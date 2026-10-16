"""Tests for Play on a single-container server (`yulon.single_play`)."""

from __future__ import annotations

from pathlib import Path

import pytest

from yulon import play_launch, single_play
from yulon.catalog.catalog import SingleEntry, load_catalog
from yulon.single_play import PlayRefusal

DDON: SingleEntry = load_catalog().get_single("ddon")


def _client(tmp_path: Path) -> Path:
    client = tmp_path / "ddon client"
    client.mkdir()
    (client / "DDO.exe").write_bytes(b"MZ")
    return client


def _spec(tmp_path: Path, **kwargs: object) -> play_launch.LaunchSpec:
    options: dict[str, object] = {
        "host": "127.0.0.1",
        "account": "mmodad",
        "server_dir": tmp_path / "server",
        "os_name": "windows",
        "home": tmp_path,
        "config_dir": tmp_path / "config",
    }
    options.update(kwargs)
    client = options.pop("client_dir", _client(tmp_path))
    return single_play.launch_spec(DDON, client, **options)  # type: ignore[arg-type]


def test_the_client_gets_upstream_s_argument_with_this_server_filled_in() -> None:
    """One argument, as upstream's README starts DDO.exe (README.adoc, measured 2026-10-12)."""
    assert single_play.arguments(DDON, host="192.168.0.60", account="mmodad") == (
        "addr=192.168.0.60 port=52100 token=mmodad DL=http://192.168.0.60:52099/win/ "
        "LVer=03.04.003.20181115.0 RVer=3040008",
    )


def test_on_windows_the_client_itself_is_started_from_its_own_folder(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    exe = tmp_path / "ddon client" / "DDO.exe"
    assert spec.argv == (str(exe), *single_play.arguments(DDON, host="127.0.0.1", account="mmodad"))
    assert spec.cwd == exe.parent
    assert spec.env == {}


def test_elsewhere_with_no_runner_the_refusal_names_this_game_s_client(tmp_path: Path) -> None:
    with pytest.raises(PlayRefusal, match="can run DDO.exe(.|\n)*DDON is a Windows program"):
        _spec(tmp_path, os_name="linux")


@pytest.mark.parametrize(
    ("client_dir", "says"),
    [(None, "Choose your DDON client folder"), (Path("nowhere"), "There is no DDO.exe")],
)
def test_no_client_or_no_exe_is_refused_with_what_to_do(
    tmp_path: Path, client_dir: Path | None, says: str
) -> None:
    with pytest.raises(PlayRefusal, match=says):
        _spec(tmp_path, client_dir=client_dir)


@pytest.mark.parametrize("name", ["", "ab", "has space", 'quo"te', "x" * 21, "név"])
def test_an_account_name_the_command_line_could_split_is_refused(tmp_path: Path, name: str) -> None:
    with pytest.raises(PlayRefusal, match="3 to 20 letters"):
        _spec(tmp_path, account=name)


def test_a_client_that_needs_administrator_rights_is_started_through_the_prompt(
    tmp_path: Path,
) -> None:
    spec = _spec(tmp_path)
    elevated: list[play_launch.LaunchSpec] = []

    def refuse(_spec: play_launch.LaunchSpec) -> None:
        error = OSError("The requested operation requires elevation")
        error.winerror = single_play.ERROR_ELEVATION_REQUIRED  # type: ignore[attr-defined]
        raise error

    assert single_play.start(spec, os_name="windows", launch=refuse, start_elevated=elevated.append)
    assert elevated == [spec]


def test_a_plain_start_asks_for_nothing(tmp_path: Path) -> None:
    started: list[play_launch.LaunchSpec] = []
    spec = _spec(tmp_path)
    assert not single_play.start(spec, os_name="windows", launch=started.append)
    assert started == [spec]


def test_any_other_failure_is_a_refusal_not_a_prompt(tmp_path: Path) -> None:
    def refuse(_spec: play_launch.LaunchSpec) -> None:
        raise OSError(2, "No such file")

    with pytest.raises(PlayRefusal, match="could not be started"):
        single_play.start(_spec(tmp_path), os_name="windows", launch=refuse)
