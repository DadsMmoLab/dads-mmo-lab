"""Tests for `yulon.platform` (roadmap Phase 1.2)."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest

from yulon import platform


@pytest.mark.parametrize(
    ("sys_platform", "expected"),
    [
        ("win32", "windows"),
        ("cygwin", "windows"),
        ("darwin", "macos"),
        ("linux", "linux"),
        ("linux2", "linux"),
    ],
)
def test_detect_returns_normalized_platform(
    monkeypatch: pytest.MonkeyPatch, sys_platform: str, expected: str
) -> None:
    """`detect()` collapses sys.platform to linux/windows/macos."""
    monkeypatch.setattr(platform.sys, "platform", sys_platform)
    assert platform.detect() == expected


def test_detect_falls_back_to_linux_for_unrecognized_platform(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An unrecognized sys.platform (e.g. a BSD) defaults to linux, with a warning."""
    monkeypatch.setattr(platform.sys, "platform", "freebsd13")
    with caplog.at_level("WARNING"):
        assert platform.detect() == "linux"
    assert "freebsd13" in caplog.text


def test_config_dir_linux_uses_xdg_data_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Linux, XDG_DATA_HOME (when set) is honored."""
    monkeypatch.setattr(platform.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", "/tmp/xdg")
    assert platform.config_dir() == Path("/tmp/xdg/yulon")


def test_config_dir_linux_defaults_to_dot_local_share(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Linux without XDG_DATA_HOME, ~/.local/share/yulon is used."""
    monkeypatch.setattr(platform.sys, "platform", "linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(platform.Path, "home", lambda: Path("/home/testuser"))
    assert platform.config_dir() == Path("/home/testuser/.local/share/yulon")


def test_config_dir_windows_uses_appdata(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Windows, %APPDATA%\\yulon is used."""
    monkeypatch.setattr(platform.sys, "platform", "win32")
    appdata = r"C:\Users\test\AppData\Roaming"
    monkeypatch.setenv("APPDATA", appdata)
    # Path equality is separator-independent; assert component-wise so this test
    # still holds when run on a POSIX host (where Path normalizes to '/').
    assert platform.config_dir() == Path(appdata) / "yulon"


def test_config_dir_windows_falls_back_when_appdata_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Windows without APPDATA set, ~/AppData/Roaming/yulon is used."""
    monkeypatch.setattr(platform.sys, "platform", "win32")
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(platform.Path, "home", lambda: Path("/Users/test"))
    assert platform.config_dir() == Path("/Users/test/AppData/Roaming/yulon")


def test_config_dir_linux_treats_empty_xdg_data_home_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty (but set) XDG_DATA_HOME is treated the same as unset."""
    monkeypatch.setattr(platform.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", "")
    monkeypatch.setattr(platform.Path, "home", lambda: Path("/home/testuser"))
    assert platform.config_dir() == Path("/home/testuser/.local/share/yulon")


def test_config_dir_macos_uses_application_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On macOS, ~/Library/Application Support/yulon is used."""
    monkeypatch.setattr(platform.sys, "platform", "darwin")
    monkeypatch.setattr(platform.Path, "home", lambda: Path("/Users/test"))
    assert platform.config_dir() == Path("/Users/test/Library/Application Support/yulon")


# ------------------------------------------- machine facts (roadmap 6.2)
# The facts the native install engine's preflight is built on. Everything here
# goes through the `detect`/`run` seams rather than through `sys.platform`
# where it can: these functions answer questions about a machine none of us is
# sitting at.


def _completed(returncode: int = 0, stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, "")


def test_vm_resources_reads_the_engines_own_numbers(monkeypatch: pytest.MonkeyPatch) -> None:
    """The VM's size, not the host's: a 32 GB Mac can have a 4 GB Docker VM."""
    monkeypatch.setattr(platform, "docker_program", lambda: "docker")
    seen: list[list[str]] = []

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return _completed(stdout='{"MemTotal": 17179869184, "NCPU": 8}')

    got = platform.vm_resources(run)
    assert got == platform.VmResources(17179869184, 8)
    assert seen == [["docker", "info", "--format", "{{json .}}"]]


@pytest.mark.parametrize(
    "answer",
    [
        _completed(stdout='{"MemTotal": 0, "NCPU": 0}'),
        _completed(stdout="not json at all"),
        _completed(returncode=1),
    ],
)
def test_vm_resources_reports_nothing_rather_than_a_fabricated_zero(
    monkeypatch: pytest.MonkeyPatch, answer: subprocess.CompletedProcess[str]
) -> None:
    """A stopped Docker Desktop prints well-formed zeroes.

    Believing them refuses a perfectly good machine with "0 GB of RAM", which
    is the fabricated refusal the whole tri-state discipline exists to prevent.
    """
    monkeypatch.setattr(platform, "docker_program", lambda: "docker")
    assert platform.vm_resources(lambda _argv: answer) is None


def test_the_docker_data_root_is_unknown_on_macos_rather_than_guessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nobody on this project can verify the path, the keys, or what "free" means there.

    Docker Desktop's data lives in a sparse `Docker.raw`, and "free space"
    against it could mean the host volume's free space or the image's
    allocation minus its use. A guess would produce a confident number that
    could refuse a Mac with plenty of room.
    """
    monkeypatch.setattr(platform, "detect", lambda: "macos")
    assert platform.docker_desktop_data_root() is None


def test_the_docker_data_root_on_linux_is_the_host_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(platform, "detect", lambda: "linux")
    assert platform.docker_desktop_data_root() == Path("/var/lib/docker")


def test_the_windows_data_root_comes_from_docker_desktops_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Read defensively: an unreadable settings file falls through to the default."""
    monkeypatch.setattr(platform, "detect", lambda: "windows")
    store = tmp_path / "settings-store.json"
    store.write_text('{"dataFolder": "D:\\\\docker-data"}', encoding="utf-8")
    monkeypatch.setattr(platform, "docker_desktop_settings_file", lambda: store)
    assert platform.docker_desktop_data_root() == Path("D:\\docker-data")
    store.write_text("{ not json", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert platform.docker_desktop_data_root() == tmp_path / "Docker" / "wsl"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (r"\\fileserver\share\wow", "network path"),
        ("/Users/pk/OneDrive/wow", "cloud-synced"),
        ("/Users/pk/Library/Mobile Documents/com~apple~CloudDocs/wow", "iCloud Drive"),
        ("/Users/pk/Dropbox/wow", "cloud-synced"),
        ("/Users/pk/games/wow", ""),
    ],
)
def test_server_dir_problem_names_the_folders_that_break_a_build(
    monkeypatch: pytest.MonkeyPatch, path: str, expected: str
) -> None:
    """Each of these fails AFTER the two-to-four-hour build, which is why it is a refusal.

    A sync client rewrites files under the compiler and uploads a
    multi-gigabyte checkout; a network path mounts inside Docker as an EMPTY
    directory rather than failing.
    """
    monkeypatch.setattr(platform, "detect", lambda: "macos")
    problem = platform.server_dir_problem(Path(path))
    if not expected:
        assert problem is None
    else:
        assert problem is not None and expected in problem


def test_keep_awake_is_a_no_op_on_linux() -> None:
    """The Linux path still runs the bash installer; `systemd-inhibit` waits for its gate."""
    with platform.keep_awake(platform_id=lambda: "linux"):
        pass


def test_keep_awake_on_macos_spawns_a_caffeinate_that_dies_with_us() -> None:
    """`-w <our pid>`: a child bound to this process, so there is no cleanup to forget.

    Unverified on a Mac by this project — `caffeinate` is assumed to ship with
    the OS and `-dims` is assumed to be the right assertion set. What is tested
    here is that we ask for exactly that, and that we let it go afterwards.
    """
    import os

    spawned: list[list[str]] = []
    stopped: list[str] = []

    class FakeChild:
        def terminate(self) -> None:
            stopped.append("terminated")

    with platform.keep_awake(
        platform_id=lambda: "macos",
        spawn=lambda argv: (spawned.append(argv), FakeChild())[1],  # type: ignore[arg-type,return-value]
    ):
        assert spawned == [["caffeinate", "-dims", "-w", str(os.getpid())]]
        assert stopped == []
    assert stopped == ["terminated"]


def test_keep_awake_on_macos_survives_a_caffeinate_that_will_not_start() -> None:
    """A power helper saying no must not fail an install that would have worked."""

    def refuse(_argv: list[str]) -> object:
        raise OSError("no such file")

    with platform.keep_awake(platform_id=lambda: "macos", spawn=refuse):  # type: ignore[arg-type]
        pass


def test_keep_awake_on_windows_refuses_the_gui_thread() -> None:
    """The assertion is scoped to the thread that sets it, so the GUI thread is a lie.

    `SetThreadExecutionState` holds only while its own thread lives, so taking
    it on the main thread and then running the install on a worker would claim
    a guarantee the install does not have.
    """
    with pytest.raises(RuntimeError, match="worker thread"):
        with platform.keep_awake(platform_id=lambda: "windows"):
            pass


def test_keep_awake_on_windows_is_taken_on_a_worker_thread() -> None:
    """The other half: off the main thread it does not refuse.

    What it actually asserts to Windows cannot be checked here (there is no
    Windows power API in a test), so this is about the thread rule only —
    roadmap 6.3's live gate owns the rest.
    """
    outcome: list[str] = []

    def work() -> None:
        try:
            with platform.keep_awake(platform_id=lambda: "windows"):
                outcome.append("held")
        except RuntimeError as exc:  # pragma: no cover - would be the bug
            outcome.append(f"refused: {exc}")

    worker = threading.Thread(target=work)
    worker.start()
    worker.join()
    assert outcome == ["held"]
