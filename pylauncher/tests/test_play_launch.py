"""Tests for `yulon.play_launch` (T181a, Play step 3: the launch command per OS).

Every launch here goes to a fake `popen`; nothing in this file starts a process.
The argv is asserted as a whole tuple, because a launch command that is right
in every part but its order still starts nothing.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from yulon import platform, play_launch
from yulon.play_launch import LaunchRefusal, LaunchSpec, launch, launch_spec

NATIVE = ".local/share/Steam"
FLATPAK = ".var/app/com.valvesoftware.Steam/data/Steam"


def _play(tmp_path: Path) -> Path:
    """A ready-to-play client folder holding the game binary, spelled as Windows ships it."""
    play = tmp_path / "WoW 3.3.5a (Yu'lon – WotLK)"
    play.mkdir()
    (play / "Wow.exe").write_bytes(b"MZ")
    return play


def _proton(home: Path, root: str = NATIVE, tool: str = "Proton 9.0") -> Path:
    """An official Proton under `<root>/steamapps/common`, with its `proton` script."""
    folder = home / root / "steamapps/common" / tool
    folder.mkdir(parents=True)
    script = folder / "proton"
    script.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    return script


def _which(found: dict[str, str]) -> Callable[[str], str | None]:
    return lambda name: found.get(name)


_NOTHING_ON_PATH = _which({})
"""No `wine` or anything else on PATH: the default, so a runner found is one a test put there."""


def _spec(
    tmp_path: Path,
    os_name: str,
    *,
    which: Callable[[str], str | None] = _NOTHING_ON_PATH,
    server_dir: Path | None = None,
    game: str = "wow-wotlk",
    play: Path | None = None,
) -> LaunchSpec:
    return launch_spec(
        play if play is not None else _play(tmp_path),
        game=game,
        server_dir=server_dir if server_dir is not None else tmp_path / "servers/one",
        os_name=os_name,
        home=tmp_path / "home",
        config_dir=tmp_path / "config",
        which=which,
    )


# --------------------------------------------------------------------------
# Windows
# --------------------------------------------------------------------------


def test_windows_runs_wow_exe_itself_from_the_play_folder(tmp_path: Path) -> None:
    play = _play(tmp_path)

    spec = _spec(tmp_path, "windows", play=play)

    assert spec.argv == (str(play / "Wow.exe"),)
    assert spec.cwd == play
    assert dict(spec.env) == {}


# --------------------------------------------------------------------------
# Linux: Proton, then Wine, then a refusal
# --------------------------------------------------------------------------


def test_linux_runs_the_client_through_proton_with_a_prefix_of_its_own(
    tmp_path: Path,
) -> None:
    play = _play(tmp_path)
    proton = _proton(tmp_path / "home")

    spec = _spec(tmp_path, "linux", play=play)

    assert spec.argv == (str(proton), "run", str(play / "Wow.exe"))
    assert spec.cwd == play
    prefix = Path(spec.env["STEAM_COMPAT_DATA_PATH"])
    assert prefix.parent == tmp_path / "config" / "proton"
    assert prefix.is_dir(), "Proton is handed a prefix folder that does not exist"
    assert spec.env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] == str(tmp_path / "home" / NATIVE)


def test_each_server_gets_its_own_proton_prefix_and_keeps_it(tmp_path: Path) -> None:
    """A prefix per server, and the SAME one on every press.

    Catches a prefix shared across servers (one server's WTF settings in
    another's Wine registry) and one keyed on Python's per-process `hash()`,
    which would make a new prefix every time Yu'lon starts.
    """
    _proton(tmp_path / "home")
    play = _play(tmp_path)

    first = _spec(tmp_path, "linux", play=play, server_dir=tmp_path / "servers/one")
    again = _spec(tmp_path, "linux", play=play, server_dir=tmp_path / "servers/one")
    other = _spec(tmp_path, "linux", play=play, server_dir=tmp_path / "servers/two")

    assert first.env["STEAM_COMPAT_DATA_PATH"] == again.env["STEAM_COMPAT_DATA_PATH"]
    assert first.env["STEAM_COMPAT_DATA_PATH"] != other.env["STEAM_COMPAT_DATA_PATH"]


def test_the_proton_prefix_name_does_not_depend_on_this_process(tmp_path: Path) -> None:
    """The prefix name is sha256 of the server folder, pinned, not `hash()`.

    `hash()` of a str is salted per process (PYTHONHASHSEED), so a prefix keyed
    on it moves every run while looking stable inside one test.
    """
    _proton(tmp_path / "home")
    server = tmp_path / "servers/one"
    digest = hashlib.sha256(str(server).encode("utf-8")).hexdigest()[:12]

    spec = _spec(tmp_path, "linux", server_dir=server, game="wow-tbc")

    assert Path(spec.env["STEAM_COMPAT_DATA_PATH"]).name == f"wow-tbc-{digest}"


def test_a_flatpak_steam_is_found_and_named_as_the_steam_root(tmp_path: Path) -> None:
    """The Deck-style native root is not the only one: the Flathub Steam counts."""
    proton = _proton(tmp_path / "home", root=FLATPAK)

    spec = _spec(tmp_path, "linux")

    assert spec.argv[0] == str(proton)
    assert spec.env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] == str(tmp_path / "home" / FLATPAK)


def test_proton_is_used_before_a_system_wine(tmp_path: Path) -> None:
    """Owner decision 3: Proton directly; Wine is the fallback, not the peer."""
    proton = _proton(tmp_path / "home")

    spec = _spec(tmp_path, "linux", which=_which({"wine": "/usr/bin/wine"}))

    assert spec.argv[0] == str(proton)


def test_linux_without_proton_falls_back_to_the_system_wine(tmp_path: Path) -> None:
    play = _play(tmp_path)

    spec = _spec(tmp_path, "linux", play=play, which=_which({"wine": "/usr/bin/wine"}))

    assert spec.argv == ("/usr/bin/wine", str(play / "Wow.exe"))
    assert spec.cwd == play
    assert "STEAM_COMPAT_DATA_PATH" not in spec.env


def test_linux_with_neither_says_what_to_install_and_that_nothing_started(
    tmp_path: Path,
) -> None:
    with pytest.raises(LaunchRefusal) as caught:
        _spec(tmp_path, "linux")

    text = str(caught.value)
    assert "Proton" in text and "Steam" in text
    assert "Wine" in text
    assert "nothing was started" in text.lower()


def test_a_steam_with_no_proton_script_is_not_proton(tmp_path: Path) -> None:
    """A Proton folder whose `proton` is missing (half-installed) runs nothing."""
    (tmp_path / "home" / NATIVE / "steamapps/common/Proton 9.0").mkdir(parents=True)

    with pytest.raises(LaunchRefusal):
        _spec(tmp_path, "linux")


# --------------------------------------------------------------------------
# macOS: Wine or a refusal
# --------------------------------------------------------------------------


def test_macos_runs_the_client_through_wine(tmp_path: Path) -> None:
    play = _play(tmp_path)

    spec = _spec(tmp_path, "macos", play=play, which=_which({"wine": "/opt/homebrew/bin/wine"}))

    assert spec.argv == ("/opt/homebrew/bin/wine", str(play / "Wow.exe"))


def test_macos_without_wine_says_to_install_it(tmp_path: Path) -> None:
    with pytest.raises(LaunchRefusal) as caught:
        _spec(tmp_path, "macos")

    assert "Wine" in str(caught.value)
    assert "nothing was started" in str(caught.value).lower()


# --------------------------------------------------------------------------
# the binary is gone
# --------------------------------------------------------------------------


@pytest.mark.parametrize("os_name", ["windows", "linux", "macos"])
def test_a_missing_wow_exe_is_refused_with_refresh_named(tmp_path: Path, os_name: str) -> None:
    """Catches handing a runner a path that is not there: WoW never opens, and
    Wine's own "file not found" lands in a log nobody reads."""
    play = tmp_path / "play"
    play.mkdir()

    with pytest.raises(LaunchRefusal) as caught:
        _spec(tmp_path, os_name, play=play, which=_which({"wine": "/usr/bin/wine"}))

    assert "Wow.exe" in str(caught.value)
    assert "Refresh from your original client" in str(caught.value)


# --------------------------------------------------------------------------
# launch(): detached, quiet, and nothing leaks back into Yu'lon
# --------------------------------------------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[object, dict[str, object]]] = []

    def __call__(self, argv: object, **kwargs: object) -> object:
        self.calls.append((argv, kwargs))
        return object()


def _a_spec(tmp_path: Path) -> LaunchSpec:
    return LaunchSpec(
        argv=("/usr/bin/wine", str(tmp_path / "Wow.exe")),
        env={"STEAM_COMPAT_DATA_PATH": str(tmp_path / "prefix")},
        cwd=tmp_path,
    )


def test_launch_on_posix_starts_a_new_session_with_no_stdio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`start_new_session`: closing Yu'lon (or its terminal) sends WoW no SIGHUP."""
    monkeypatch.setattr(platform, "detect", lambda: "linux")
    popen = _Recorder()

    launch(_a_spec(tmp_path), popen=popen)

    ((argv, kwargs),) = popen.calls
    assert argv == ["/usr/bin/wine", str(tmp_path / "Wow.exe")]
    assert kwargs["start_new_session"] is True
    assert "creationflags" not in kwargs
    for stream in ("stdin", "stdout", "stderr"):
        assert kwargs[stream] is subprocess.DEVNULL, stream
    assert kwargs["cwd"] == str(tmp_path)


def test_launch_on_windows_detaches_from_this_console_and_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(platform, "detect", lambda: "windows")
    popen = _Recorder()

    launch(_a_spec(tmp_path), popen=popen)

    ((_, kwargs),) = popen.calls
    assert kwargs["creationflags"] == 0x00000008 | 0x00000200
    assert "start_new_session" not in kwargs


def test_launch_merges_the_spec_env_into_a_new_dict_and_leaves_ours_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The child gets this process's environment plus the spec's; Yu'lon gets nothing new.

    Catches `os.environ.update(spec.env)`, which works for the child and then
    points every later Proton-free launch at the last server's prefix.
    """
    monkeypatch.setattr(platform, "detect", lambda: "linux")
    monkeypatch.setenv("YULON_TEST_MARKER", "kept")
    monkeypatch.delenv("STEAM_COMPAT_DATA_PATH", raising=False)
    popen = _Recorder()

    launch(_a_spec(tmp_path), popen=popen)

    ((_, kwargs),) = popen.calls
    env = kwargs["env"]
    assert isinstance(env, dict)
    assert env["YULON_TEST_MARKER"] == "kept"
    assert env["STEAM_COMPAT_DATA_PATH"] == str(tmp_path / "prefix")
    assert "STEAM_COMPAT_DATA_PATH" not in os.environ


def test_launch_without_a_popen_reaches_the_guarded_spawn(tmp_path: Path) -> None:
    """The default spawn is looked up per call, so the suite's guard can refuse it.

    A default bound at definition time would be `subprocess.Popen` itself, and a
    test that forgot `popen=` would start a real game.
    """
    assert play_launch._spawn is not subprocess.Popen, "the conftest guard is not in place"
    with pytest.raises(pytest.fail.Exception, match="real game"):
        launch(_a_spec(tmp_path))
