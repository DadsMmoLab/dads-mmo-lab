"""Tests for `yulon.platform` (roadmap Phase 1.2)."""

from __future__ import annotations

import errno
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path, PureWindowsPath

import pytest

from yulon import platform

# -- folder_is_gone() (T34 round 2) --------------------------------------


def test_an_existing_file_is_not_a_gone_folder(tmp_path: Path) -> None:
    """`is_dir()` alone would answer False here too — a file is not a directory."""
    plain_file = tmp_path / "state.json"
    plain_file.write_text("{}")
    assert platform.folder_is_gone(plain_file) is False


def test_a_broken_symlink_counts_as_gone(tmp_path: Path) -> None:
    """It points nowhere, which is what this predicate exists to name."""
    target = tmp_path / "was-here"
    link = tmp_path / "server"
    link.symlink_to(target)
    assert not target.exists()
    assert platform.folder_is_gone(link) is True


def test_a_probe_that_cannot_be_answered_is_not_read_as_gone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Permission denied and a stalled network share are "cannot tell", not "gone".

    Only a confirmed `FileNotFoundError` may say so; every other `OSError`
    leaves the caller behaving as it did before this predicate existed.
    """

    def refuses(path: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(platform.os, "stat", refuses)
    assert platform.folder_is_gone(tmp_path / "locked") is False


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


def _canned(stdout: str) -> Callable[[list[str]], subprocess.CompletedProcess[str]]:
    """A runner that answers any argv with one `docker info` document."""
    return lambda _argv: _completed(stdout=stdout)


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


def test_vm_resources_bounds_the_other_docker_info(monkeypatch: pytest.MonkeyPatch) -> None:
    """Preflight's `docker info` is a probe too, and hangs the same way.

    Same defect as `docker_ready()`'s, one function further down: the default
    runner passed no `timeout`, so a wedged Docker CLI held the preflight that
    is only trying to find out how big the VM is.
    """
    monkeypatch.setattr(platform, "docker_program", lambda: "docker")
    seen: list[object] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(kwargs.get("timeout"))
        return _completed(returncode=124)

    monkeypatch.setattr(platform.runner, "run", fake_run)
    assert platform.vm_resources() is None
    assert seen and all(isinstance(bound, float) for bound in seen), f"unbounded probe: {seen}"


def test_the_macos_data_root_prefers_the_settings_store_and_falls_back_to_docker_raw(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Docker Desktop's `diskPath` wins; absent, the documented default `Docker.raw`.

    The fallback path is believed, not measured — it is read only as a target
    for the host free-space probe, and a miss is reported *unchecked*, never as
    a refusal. The settings keys themselves are read defensively, exactly like
    the Windows branch: an unreadable file falls through to the default.
    """
    monkeypatch.setattr(platform, "detect", lambda: "macos")
    monkeypatch.setattr(platform.Path, "home", lambda: Path("/Users/deck"))

    store = tmp_path / "settings-store.json"
    store.write_text('{"diskPath": "/Volumes/Big/docker-data"}', encoding="utf-8")
    monkeypatch.setattr(platform, "docker_desktop_settings_file", lambda: store)
    assert platform.docker_desktop_data_root() == Path("/Volumes/Big/docker-data")

    store.write_text('{"virtualDiskPath": "/Volumes/Fast/Docker.raw"}', encoding="utf-8")
    assert platform.docker_desktop_data_root() == Path("/Volumes/Fast/Docker.raw")

    store.write_text("{ not json", encoding="utf-8")
    assert platform.docker_desktop_data_root() == Path(
        "/Users/deck/Library/Containers/com.docker.docker/Data/vms/0/data/Docker.raw"
    )


def test_docker_desktop_settings_file_on_macos_prefers_settings_store_and_falls_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(platform, "detect", lambda: "macos")
    monkeypatch.setattr(platform.Path, "home", lambda: tmp_path)

    group_dir = tmp_path / "Library" / "Group Containers" / "group.com.docker"
    group_dir.mkdir(parents=True)

    # When neither exists, default to settings-store.json
    assert platform.docker_desktop_settings_file() == group_dir / "settings.json"

    # When settings.json exists
    settings_old = group_dir / "settings.json"
    settings_old.write_text('{"diskPath": "/data"}', encoding="utf-8")
    assert platform.docker_desktop_settings_file() == settings_old

    # When settings-store.json exists, it takes precedence
    settings_new = group_dir / "settings-store.json"
    settings_new.write_text('{"diskPath": "/data2"}', encoding="utf-8")
    assert platform.docker_desktop_settings_file() == settings_new


# The shapes `docker info` came back in, measured on a Windows 11 gate box
# (Docker Desktop 29.7.2) on 2026-09-16 for T39. The two below are the same WSL
# distro, once on Docker Desktop's WSL integration and once with a native
# `docker.io` engine; only the `Name` is rewritten here, since a box name does
# not belong in this repo. Note what they share: `DockerRootDir` is
# `/var/lib/docker` in BOTH, so that field alone cannot tell the daemons apart
# and a data root read straight out of it measures the wrong filesystem under
# Desktop. `OperatingSystem` is what separates them.
_DESKTOP_INTEGRATION_INFO = (
    '{"OperatingSystem": "Docker Desktop", "DockerRootDir": "/var/lib/docker", '
    '"Name": "docker-desktop", "ServerVersion": "29.7.2", "OSType": "linux", '
    '"Driver": "overlay2", "MemTotal": 16768684032, "NCPU": 7}'
)
_NATIVE_ENGINE_INFO = (
    '{"OperatingSystem": "Ubuntu 26.04.1 LTS", "DockerRootDir": "/var/lib/docker", '
    '"Name": "a-wsl-distro", "ServerVersion": "29.1.3", "OSType": "linux", '
    '"Driver": "overlayfs", "MemTotal": 16768684032, "NCPU": 7}'
)


def _windows_profile_with_desktops_vhdx(root: Path, user: str = "a-windows-user") -> Path:
    """Build the measured `/mnt/<drive>/Users/<user>/AppData/...` tree under `root`."""
    vhdx = root.joinpath("c", "Users", user, *platform._DESKTOP_WSL_VHDX)
    vhdx.parent.mkdir(parents=True, exist_ok=True)
    vhdx.write_bytes(b"not really 23 GB")
    return vhdx


def test_a_native_engine_on_linux_is_measured_where_it_says_it_keeps_its_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`Docker Root Dir`, as reported — the daemon is the host's own.

    Measured: a `docker.io` engine inside the WSL distro answered
    `OperatingSystem=Ubuntu 26.04.1 LTS`, and pulling 3.59 GB of images grew
    that distro's `/` by 3,591,659,520 bytes. `/var/lib/docker` really is the
    filesystem that pays here.
    """
    monkeypatch.setattr(platform, "detect", lambda: "linux")
    monkeypatch.setattr(platform, "docker_program", lambda: "docker")
    run = _canned(_NATIVE_ENGINE_INFO)
    assert platform.docker_desktop_data_root(run) == Path("/var/lib/docker")


def test_docker_desktops_wsl_integration_is_measured_on_the_windows_drive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Not the distro's `/var/lib/docker` — that directory does not exist there.

    This is T39. `detect()` answers "linux" inside WSL, so a launcher in a
    distro using Desktop's WSL integration used to measure the distro's own
    filesystem: `free_bytes()` walked up the missing `/var/lib/docker` to
    `/var/lib` and reported 1,024,681,578,496 bytes free for a Docker whose
    images land in `docker_data.vhdx` on C:, which had 34,857,996,288 free at
    09:52 that day and 30,476,148,736 at 12:10, after more images (measured on
    the gate box, 2026-09-16, with the app's own code). 954 GiB against a 40 GB
    floor passes; 28 GiB refuses.
    """
    monkeypatch.setattr(platform, "detect", lambda: "linux")
    monkeypatch.setattr(platform, "docker_program", lambda: "docker")
    vhdx = _windows_profile_with_desktops_vhdx(tmp_path)
    monkeypatch.setattr(platform, "_windows_drive_mounts", lambda: [tmp_path / "c"])

    got = platform.docker_desktop_data_root(_canned(_DESKTOP_INTEGRATION_INFO))
    assert got == vhdx
    assert got != Path("/var/lib/docker")


def test_docker_desktops_data_disk_that_cannot_be_pinned_down_stays_unchecked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No candidate, or several profiles with one each, is unknown — never a guess.

    The alternative is picking a profile and putting its drive's free space
    under a refusal, which is a number nothing measured.
    """
    monkeypatch.setattr(platform, "detect", lambda: "linux")
    monkeypatch.setattr(platform, "docker_program", lambda: "docker")
    monkeypatch.setattr(platform, "_windows_drive_mounts", lambda: [tmp_path / "c"])

    # Nothing there at all: the interop mount may not exist, or Desktop may
    # keep its disk somewhere this search does not know about.
    assert platform.docker_desktop_data_root(_canned(_DESKTOP_INTEGRATION_INFO)) is None

    # Two Windows profiles, each with a Desktop install. Which one belongs to
    # the daemon that just answered is not something this code can tell.
    _windows_profile_with_desktops_vhdx(tmp_path, "one-user")
    _windows_profile_with_desktops_vhdx(tmp_path, "another-user")
    assert platform.docker_desktop_data_root(_canned(_DESKTOP_INTEGRATION_INFO)) is None


@pytest.mark.parametrize(
    "answer",
    [
        _completed(returncode=1),
        _completed(stdout="not json at all"),
        _completed(stdout='{"DockerRootDir": "/var/lib/docker"}'),
        _completed(stdout='{"OperatingSystem": "", "DockerRootDir": "/var/lib/docker"}'),
        _completed(stdout='{"OperatingSystem": "Ubuntu 26.04.1 LTS", "DockerRootDir": ""}'),
    ],
)
def test_a_linux_data_root_that_was_never_established_is_never_var_lib_docker(
    monkeypatch: pytest.MonkeyPatch, answer: subprocess.CompletedProcess[str]
) -> None:
    """A daemon that did not answer leaves the row unchecked.

    `/var/lib/docker` as a default is the defect this ticket is about: it is a
    real directory on one kind of machine and a fabrication on the other, and
    nothing in a failed probe says which machine this is.
    """
    monkeypatch.setattr(platform, "detect", lambda: "linux")
    monkeypatch.setattr(platform, "docker_program", lambda: "docker")
    assert platform.docker_desktop_data_root(lambda _argv: answer) is None


def test_no_docker_binary_leaves_the_linux_data_root_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(platform, "detect", lambda: "linux")
    monkeypatch.setattr(platform, "docker_program", lambda: None)
    assert platform.docker_desktop_data_root() is None


def test_windows_drive_mounts_are_single_letters_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`/mnt/wsl` and `/mnt/host` are WSL's own, not drives.

    Measured on the box: a distro on Desktop's WSL integration carries
    `/mnt/wsl/docker-desktop/...`, and Desktop's utility VM carries
    `/mnt/host/c`. Treating either as a drive would send the search off
    looking for `Users` inside Docker's own plumbing.
    """
    for name in ("c", "d", "wsl", "host", "docker-desktop-disk"):
        (tmp_path / name).mkdir()
    monkeypatch.setattr(platform, "_WSL_MOUNT_ROOT", tmp_path)
    assert [mount.name for mount in platform._windows_drive_mounts()] == ["c", "d"]


def test_a_machine_with_no_mnt_at_all_leaves_the_data_root_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A real Linux box has no `/mnt` to list, and that is an answer, not an error.

    Only the absent case is exercised here. A `/mnt` that exists but refuses to
    be read raises `PermissionError`, which is an `OSError` and lands in the
    same `except` — the same branch, not a second one this test also covers.
    """
    monkeypatch.setattr(platform, "_WSL_MOUNT_ROOT", tmp_path / "there-is-no-mnt")
    assert platform._windows_drive_mounts() == []


# -- Docker Desktop's data root on Windows (T199 round 2) -----------------
#
# Docker Desktop 29.7.2 records Settings -> Resources -> Advanced -> Disk image
# location as `CustomWslDistroDir` in settings-store.json, and the data disk
# then lives at `<dir>\disk\docker_data.vhdx` (seen on the Windows gate box,
# 2026-10-03). The rule throughout: a location the settings NAME but that
# cannot be found is unchecked, never the default — the default may be the
# stale copy the move left behind, and its drive's free space under a refusal
# is a guess. Only settings that name nothing (or no settings file at all)
# keep the default.
#
# The Windows branch's paths are Windows paths, which a POSIX test host cannot
# open; `_windows_host_path`, the one call that turns a validated Windows path
# into a host path (`Path(str(location))` on Windows), is pointed at `tmp_path`
# here: `E:\X` -> `<tmp>/E/X`, `\\nas\share\X` -> `<tmp>/UNC/nas/share/X`.
# Everything before it — reading the store, the key order, the absolute-path
# rule — and the existence checks after it are the real code.


def _drives_under(root: Path) -> Callable[[PureWindowsPath], Path]:
    def to_host(location: PureWindowsPath) -> Path:
        anchor = location.drive
        if anchor.endswith(":"):
            base = root / anchor[0].upper()
        else:
            base = root.joinpath("UNC", *anchor.strip("\\").split("\\"))
        return base.joinpath(*location.parts[1:])

    return to_host


def _windows_machine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A Windows machine with its drives under `tmp_path`; returns where its store goes."""
    monkeypatch.setattr(platform, "detect", lambda: "windows")
    monkeypatch.setattr(platform, "_windows_host_path", _drives_under(tmp_path))
    store = tmp_path / "Roaming" / "Docker" / "settings-store.json"
    store.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    return store


def _legacy_disk(folder: Path, name: str = "DockerDesktop.vhdx") -> Path:
    """A folder a legacy key can name: it holds a disk image directly."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(b"a disk image")
    return folder


def _windows_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, settings: dict[str, object]
) -> None:
    """A Windows machine whose Docker Desktop settings store says `settings`."""
    store = _windows_machine(monkeypatch, tmp_path)
    store.write_text(json.dumps(settings), encoding="utf-8")


def _data_disk(folder: Path) -> Path:
    """Docker Desktop's data disk as it sits in `folder`: `<folder>/disk/docker_data.vhdx`."""
    vhdx = folder / "disk" / "docker_data.vhdx"
    vhdx.parent.mkdir(parents=True, exist_ok=True)
    vhdx.write_bytes(b"not really 23 GB")
    return vhdx


def _stale_default(tmp_path: Path) -> Path:
    """The C: copy a move leaves behind under `%LOCALAPPDATA%\\Docker\\wsl`."""
    return _data_disk(tmp_path / "Local" / "Docker" / "wsl")


def _said_about(caplog: pytest.LogCaptureFixture, key: str) -> list[str]:
    return [record.getMessage() for record in caplog.records if key in record.getMessage()]


def test_a_moved_docker_desktop_disk_is_measured_where_it_was_moved_to(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`CustomWslDistroDir` wins, over a legacy `dataFolder` that resolves as well.

    Before T199 the key was never read: this answered the `dataFolder`, and with
    no legacy key the default under `%LOCALAPPDATA%` — the drive the player had
    just been told to move the disk off. The folder is returned, not the file:
    free space is the volume's, and `disk_usage` reads a folder reliably.
    """
    _data_disk(tmp_path / "E" / "DockerDesktopWSL")
    _legacy_disk(tmp_path / "D" / "docker-data")
    _stale_default(tmp_path)
    _windows_store(
        monkeypatch,
        tmp_path,
        {"CustomWslDistroDir": "E:\\DockerDesktopWSL", "dataFolder": "D:\\docker-data"},
    )
    assert platform.docker_desktop_data_root() == tmp_path / "E" / "DockerDesktopWSL" / "disk"


def test_a_disk_moved_to_a_network_share_is_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A UNC path is absolute on Windows; the share's folder is measured."""
    _data_disk(tmp_path / "UNC" / "nas" / "docker" / "DockerDesktopWSL")
    _windows_store(
        monkeypatch, tmp_path, {"CustomWslDistroDir": "\\\\nas\\docker\\DockerDesktopWSL"}
    )
    assert platform.docker_desktop_data_root() == (
        tmp_path / "UNC" / "nas" / "docker" / "DockerDesktopWSL" / "disk"
    )


@pytest.mark.parametrize(
    "settings",
    [
        None,
        {},
        {"CustomWslDistroDir": ""},
        {"CustomWslDistroDir": "   ", "dataFolder": ""},
        {"CustomWslDistroDir": None},
    ],
    ids=["no-store-file", "no-keys", "empty-key", "blank-keys", "null-key"],
)
def test_settings_that_name_no_location_keep_the_unconditional_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, settings: dict[str, object] | None
) -> None:
    """No move named is exactly the old answer: the default folder, disk or no disk.

    An empty `CustomWslDistroDir` is "no move" because that is how Docker
    Desktop itself reads it: its backend carries "customWslDistroDir is empty,
    setting it to the default value" (seen in com.docker.backend.exe on the gate
    box, 2026-10-03). A missing settings file is a fresh install, not an
    unreadable one.
    """
    store = _windows_machine(monkeypatch, tmp_path)
    if settings is not None:
        store.write_text(json.dumps(settings), encoding="utf-8")
    assert not (tmp_path / "Local" / "Docker").exists()
    assert platform.docker_desktop_data_root() == tmp_path / "Local" / "Docker" / "wsl"


def _unresolved_custom_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, broken: str) -> object:
    """A `CustomWslDistroDir` value that breaks exactly one of its rules.

    Each is otherwise as close to valid as its rule allows: where a reading of
    the bad value could land on a real disk, that disk is made — `E:X` read as
    `E:\\X`, `E:` read as `E:\\`, the relative name under the working directory —
    so only "absolute" is what refuses it. The missing folder is absolute, so
    only "exists" is broken; the folder holding only `main/ext4.vhdx` (the
    distro, not the data) exists, so only "holds the data disk" is.
    """
    if broken == "not-a-string":
        return 42
    if broken == "drive-relative":
        _data_disk(tmp_path / "E" / "DockerDesktopWSL")
        return "E:DockerDesktopWSL"
    if broken == "rooted-without-drive":
        _data_disk(tmp_path / "DockerDesktopWSL")
        return "\\DockerDesktopWSL"
    if broken == "bare-drive":
        _data_disk(tmp_path / "E")
        return "E:"
    if broken == "relative":
        monkeypatch.chdir(tmp_path)
        _data_disk(tmp_path / "DockerDesktopWSL")
        return "DockerDesktopWSL"
    if broken == "missing":
        return "E:\\does-not-exist"
    assert broken == "no-data-disk", broken
    folder = tmp_path / "E" / "DockerDesktopWSL"
    (folder / "main").mkdir(parents=True)
    (folder / "main" / "ext4.vhdx").write_bytes(b"the distro, not the data")
    return "E:\\DockerDesktopWSL"


_UNRESOLVED_CUSTOM_DIRS = [
    "not-a-string",
    "drive-relative",
    "rooted-without-drive",
    "bare-drive",
    "relative",
    "missing",
    "no-data-disk",
]


@pytest.mark.parametrize("broken", _UNRESOLVED_CUSTOM_DIRS)
def test_a_move_that_cannot_be_found_is_unchecked_even_with_a_default_disk_there(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    broken: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The settings say the disk was moved and it is not there: no guessed drive.

    The default disk exists here — the copy a move leaves behind on C: — and it
    is still not the answer: its drive's free space under a refusal would be a
    guess about where the images go. One log line names the key and the value.
    """
    value = _unresolved_custom_dir(monkeypatch, tmp_path, broken)
    _stale_default(tmp_path)
    _windows_store(monkeypatch, tmp_path, {"CustomWslDistroDir": value})
    with caplog.at_level("INFO", logger=platform.logger.name):
        assert platform.docker_desktop_data_root() is None
    said = _said_about(caplog, "CustomWslDistroDir")
    assert len(said) == 1 and repr(value) in said[0], said


@pytest.mark.parametrize("broken", ["drive-relative", "missing"])
def test_a_move_that_cannot_be_found_does_not_fall_back_to_a_legacy_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, broken: str
) -> None:
    """The first location the settings name decides; an older key is no better a guess."""
    value = _unresolved_custom_dir(monkeypatch, tmp_path, broken)
    _legacy_disk(tmp_path / "D" / "docker-data")
    _windows_store(
        monkeypatch, tmp_path, {"CustomWslDistroDir": value, "dataFolder": "D:\\docker-data"}
    )
    assert platform.docker_desktop_data_root() is None


@pytest.mark.parametrize(
    ("key", "value", "image", "expected"),
    [
        ("dataFolder", "D:\\docker-data", "docker-data/DockerDesktop.vhdx", "docker-data"),
        ("DataFolder", "D:\\docker-data", "docker-data/Docker.raw", "docker-data"),
        ("diskPath", "D:\\vm\\DockerDesktop.vhdx", "vm/DockerDesktop.vhdx", "vm"),
        ("virtualDiskPath", "D:\\vm\\disk.VHDX", "vm/disk.VHDX", "vm"),
    ],
    ids=["folder-with-vhdx", "folder-with-raw", "vhdx-file", "upper-case-suffix"],
)
def test_a_legacy_key_naming_a_disk_image_is_the_data_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    key: str,
    value: str,
    image: str,
    expected: str,
) -> None:
    """`dataFolder` and the other legacy spellings still answer when no move is named.

    Only with proof that Docker's disk is there: the path is a disk image
    (`.vhdx`, `.raw`), or a folder holding one directly. A file comes back as
    its folder, as the moved disk does, so `disk_usage` is handed a folder.
    """
    (tmp_path / "D" / image).parent.mkdir(parents=True)
    (tmp_path / "D" / image).write_bytes(b"a disk image")
    _stale_default(tmp_path)
    _windows_store(monkeypatch, tmp_path, {key: value})
    assert platform.docker_desktop_data_root() == tmp_path / "D" / expected


@pytest.mark.parametrize(
    "value",
    [
        42,
        "docker-data",
        "D:docker-data",
        "D:\\does-not-exist",
        "D:\\empty",
        "D:\\vm\\notes.txt",
        "D:\\nested",
    ],
    ids=[
        "not-a-string",
        "relative",
        "drive-relative",
        "missing",
        "folder-without-image",
        "file-not-an-image",
        "image-two-levels-down",
    ],
)
def test_a_legacy_data_folder_that_cannot_be_found_is_unchecked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    value: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A legacy key gets the same rule as the move: named and not there is unchecked.

    Before round 2 any non-empty string was taken as it stood, so a
    `D:\\docker-data` that did not exist sent `free_bytes()` walking up to `D:\\`
    — or, with no D: at all, to nothing — and called that Docker's disk. An
    existing path is not proof either (round 3): a folder with no disk image in
    it, a file that is not one, or an image one folder further down than a
    legacy key points is no evidence that Docker's disk lives there. Where a
    reading of the bad value could land on a real image, the image is made, so
    each value breaks only its own rule.
    """
    monkeypatch.chdir(tmp_path)
    _legacy_disk(tmp_path / "docker-data")
    _legacy_disk(tmp_path / "D" / "docker-data")
    (tmp_path / "D" / "empty").mkdir(parents=True)
    (tmp_path / "D" / "empty" / "notes.txt").write_text("not a disk")
    _legacy_disk(tmp_path / "D" / "vm", "notes.txt")
    _data_disk(tmp_path / "D" / "nested")
    _stale_default(tmp_path)
    _windows_store(monkeypatch, tmp_path, {"dataFolder": value})
    with caplog.at_level("INFO", logger=platform.logger.name):
        assert platform.docker_desktop_data_root() is None
    said = _said_about(caplog, "dataFolder")
    assert len(said) == 1 and repr(value) in said[0], said


@pytest.mark.parametrize(
    "content",
    [b"{ not json", b"\xef\xbb\xbf{}", b"[]", None],
    ids=["bad-json", "bom", "not-an-object", "a-directory"],
)
def test_a_settings_file_that_is_there_but_cannot_be_read_is_unchecked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: bytes | None
) -> None:
    """It may name a move this code cannot see, so the default would be a guess.

    The BOM case is deliberate: Docker Desktop itself refuses a store that starts
    with one (measured on the gate box, 2026-09-16). The directory stands in for
    every `OSError` on open other than "not there" — a sharing violation while
    Desktop rewrites the file, a permission refusal.
    """
    store = _windows_machine(monkeypatch, tmp_path)
    _stale_default(tmp_path)
    if content is None:
        store.mkdir()
    else:
        store.write_bytes(content)
    assert platform.docker_desktop_data_root() is None


@pytest.mark.parametrize("older", [None, {"CustomWslDistroDir": "E:\\DockerDesktopWSL"}])
def test_an_unreadable_current_store_is_unchecked_whatever_the_older_file_says(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, older: dict[str, object] | None
) -> None:
    """`settings.json` stands in for `settings-store.json` only when that is not there.

    Here the current store is there and cannot be read (a directory stands in
    for a sharing violation; `is_file()` says False for it, which is how the
    old choice of file fell through to `settings.json`, or with no
    `settings.json` to nothing at all, and so to the default).
    """
    store = _windows_machine(monkeypatch, tmp_path)
    _stale_default(tmp_path)
    _data_disk(tmp_path / "E" / "DockerDesktopWSL")
    store.mkdir()
    if older is not None:
        (store.parent / "settings.json").write_text(json.dumps(older), encoding="utf-8")
    assert platform.docker_desktop_data_root() is None


def test_the_older_settings_file_is_read_when_the_current_one_is_not_there(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _windows_machine(monkeypatch, tmp_path)
    _stale_default(tmp_path)
    _data_disk(tmp_path / "E" / "DockerDesktopWSL")
    (store.parent / "settings.json").write_text(
        json.dumps({"CustomWslDistroDir": "E:\\DockerDesktopWSL"}), encoding="utf-8"
    )
    assert platform.docker_desktop_data_root() == tmp_path / "E" / "DockerDesktopWSL" / "disk"


def test_macos_ignores_the_windows_custom_disk_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No evidence of the key on macOS: a store naming only it still gives `Docker.raw`."""
    monkeypatch.setattr(platform, "detect", lambda: "macos")
    monkeypatch.setattr(platform.Path, "home", lambda: Path("/Users/deck"))
    custom = tmp_path / "Elsewhere"
    _data_disk(custom)
    store = tmp_path / "settings-store.json"
    store.write_text(json.dumps({"CustomWslDistroDir": str(custom)}), encoding="utf-8")
    monkeypatch.setattr(platform, "docker_desktop_settings_file", lambda: store)
    assert platform.docker_desktop_data_root() == Path(
        "/Users/deck/Library/Containers/com.docker.docker/Data/vms/0/data/Docker.raw"
    )


# -- the same chain under Desktop's WSL integration -------------------------


def _wsl_desktop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(platform, "detect", lambda: "linux")
    monkeypatch.setattr(platform, "docker_program", lambda: "docker")
    monkeypatch.setattr(platform, "_WSL_MOUNT_ROOT", tmp_path)


def _wsl_profile_store(
    root: Path,
    settings: dict[str, object] | bytes,
    user: str = "pk",
    name: str = "settings-store.json",
) -> None:
    """`/mnt/c/Users/<user>/AppData/Roaming/Docker/<name>` holding `settings`."""
    store = root.joinpath("c", "Users", user, "AppData", "Roaming", "Docker", name)
    store.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(settings, bytes):
        store.write_bytes(settings)
    else:
        store.write_text(json.dumps(settings), encoding="utf-8")


def _wsl_root() -> Path | None:
    """What preflight is handed under Desktop's WSL integration."""
    return platform.docker_desktop_data_root(_canned(_DESKTOP_INTEGRATION_INFO))


@pytest.mark.parametrize("name", ["settings-store.json", "settings.json"])
def test_wsl_integration_follows_a_moved_disk_to_its_drive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str
) -> None:
    """`E:\\DockerDesktopWSL` is `/mnt/e/DockerDesktopWSL` from the distro.

    The C: copy is left in place, as it was on the gate box after the move: the
    profile still offers exactly one candidate, and it is the E: one. The older
    `settings.json` is read when there is no `settings-store.json`, as on Windows.
    """
    _wsl_desktop(monkeypatch, tmp_path)
    _windows_profile_with_desktops_vhdx(tmp_path, "pk")
    _wsl_profile_store(tmp_path, {"CustomWslDistroDir": "E:\\DockerDesktopWSL"}, name=name)
    _data_disk(tmp_path / "e" / "DockerDesktopWSL")
    assert _wsl_root() == tmp_path / "e" / "DockerDesktopWSL" / "disk"


def test_wsl_integration_reads_a_legacy_data_folder_through_the_mount(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _wsl_desktop(monkeypatch, tmp_path)
    _windows_profile_with_desktops_vhdx(tmp_path, "pk")
    _wsl_profile_store(tmp_path, {"dataFolder": "E:\\docker-data"})
    _legacy_disk(tmp_path / "e" / "docker-data")
    assert _wsl_root() == tmp_path / "e" / "docker-data"


@pytest.mark.parametrize(
    "settings",
    [
        {"CustomWslDistroDir": "E:\\DockerDesktopWSL"},
        {"CustomWslDistroDir": "E:DockerDesktopWSL"},
        {"CustomWslDistroDir": "\\\\nas\\docker\\DockerDesktopWSL"},
        {"dataFolder": "E:\\does-not-exist"},
        b"{ not json",
    ],
    ids=["no-data-disk", "drive-relative", "unc", "legacy-missing", "unreadable-store"],
)
def test_wsl_integration_never_falls_back_to_a_stale_default_for_a_move_it_cannot_find(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, settings: dict[str, object] | bytes
) -> None:
    """Named and not found from the distro is unchecked, with C:'s copy right there.

    `E:DockerDesktopWSL` is drive-relative and must not become
    `/mnt/e/DockerDesktopWSL`, whose data disk is made here so that only that
    rule can refuse it. A UNC share has no `/mnt/<letter>` to stand for it.
    """
    _wsl_desktop(monkeypatch, tmp_path)
    _windows_profile_with_desktops_vhdx(tmp_path, "pk")
    _wsl_profile_store(tmp_path, settings)
    moved = tmp_path / "e" / "DockerDesktopWSL"
    if settings == {"CustomWslDistroDir": "E:\\DockerDesktopWSL"}:
        (moved / "main").mkdir(parents=True)
    else:
        _data_disk(moved)
    assert _wsl_root() is None


def test_wsl_integration_with_nothing_named_keeps_the_profile_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _wsl_desktop(monkeypatch, tmp_path)
    default = _windows_profile_with_desktops_vhdx(tmp_path, "pk")
    _wsl_profile_store(tmp_path, {"CustomWslDistroDir": "", "SomethingElse": True})
    assert _wsl_root() == default


def test_wsl_integration_with_two_profiles_holding_a_disk_each_is_unchecked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One moved to E:, one at its default: which owns the daemon is not knowable here."""
    _wsl_desktop(monkeypatch, tmp_path)
    _windows_profile_with_desktops_vhdx(tmp_path, "pk")
    _wsl_profile_store(tmp_path, {"CustomWslDistroDir": "E:\\DockerDesktopWSL"})
    _data_disk(tmp_path / "e" / "DockerDesktopWSL")
    _windows_profile_with_desktops_vhdx(tmp_path, "another-user")
    assert _wsl_root() is None


def test_wsl_integration_with_one_profile_unresolved_is_unchecked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A second profile whose move cannot be found may be the daemon's, so no answer."""
    _wsl_desktop(monkeypatch, tmp_path)
    default = _windows_profile_with_desktops_vhdx(tmp_path, "pk")
    _wsl_profile_store(tmp_path, {"CustomWslDistroDir": "E:\\gone"}, user="another-user")
    assert default.is_file()
    assert _wsl_root() is None


@pytest.mark.parametrize("how", ["permission", "not-a-directory", "store-is-a-directory"])
def test_wsl_integration_with_one_profile_whose_settings_cannot_be_read_is_unchecked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, how: str
) -> None:
    """Any error finding or reading a profile's settings is "cannot tell", not "absent".

    `pk` has a perfectly good default disk; `another-user`'s settings may name
    a move this distro cannot see, and that profile may be the daemon's. A
    `Path.is_file()` that swallows the error (it does on Python 3.14 for every
    `OSError`, and for a directory always) must not turn this into "no file".
    """
    _wsl_desktop(monkeypatch, tmp_path)
    _windows_profile_with_desktops_vhdx(tmp_path, "pk")
    docker = tmp_path.joinpath("c", "Users", "another-user", "AppData", "Roaming", "Docker")
    if how == "not-a-directory":
        docker.parent.mkdir(parents=True)
        docker.write_text("a file where Docker's settings folder should be")
        assert _wsl_root() is None
        return
    _wsl_profile_store(tmp_path, {"CustomWslDistroDir": ""}, user="another-user")
    if how == "store-is-a-directory":
        (docker / "settings-store.json").unlink()
        (docker / "settings-store.json").mkdir()
        _wsl_profile_store(tmp_path, {}, user="another-user", name="settings.json")
        assert _wsl_root() is None
        return
    if os.geteuid() == 0:
        pytest.skip("root reads a folder whatever its mode")
    docker.chmod(0)
    try:
        assert _wsl_root() is None
    finally:
        docker.chmod(0o755)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (r"\\fileserver\share\wow", "network path"),
        ("/Users/user/OneDrive/wow", "cloud-synced"),
        ("/Users/user/Library/Mobile Documents/com~apple~CloudDocs/wow", "iCloud Drive"),
        ("/Users/user/Dropbox/wow", "cloud-synced"),
        ("/Users/user/games/wow", ""),
        ("/", "root of a filesystem"),
        ("/etc", "system directory"),
        ("/tmp", "system directory"),
        # One "up" click from where the picker opens, and `--reinstall` would
        # have run `sudo rm -rf /home` - every account on the machine.
        ("/home", "system directory"),
        ("/media", "system directory"),
        ("/mnt", "system directory"),
        # A folder UNDER a reserved one is fine - only the tree roots are
        # refused, exactly as the scripts' `case` list does it.
        ("/var/lib/wow", ""),
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


INHIBIT_ARGV = [
    "systemd-inhibit",
    "--what=idle:sleep",
    "--who=Yu'lon",
    "--why=installing a server",
    "sleep",
    "infinity",
]


def test_keep_awake_on_linux_holds_a_systemd_inhibit_and_lets_it_go() -> None:
    """The `caffeinate` shape on Linux: a detached inhibitor, terminated on the way out."""
    spawned: list[list[str]] = []
    stopped: list[str] = []

    class FakeChild:
        def terminate(self) -> None:
            stopped.append("terminated")

    with platform.keep_awake(
        platform_id=lambda: "linux",
        spawn=lambda argv: (spawned.append(argv), FakeChild())[1],  # type: ignore[arg-type,return-value]
    ):
        assert spawned == [INHIBIT_ARGV]
        assert stopped == []
    assert stopped == ["terminated"]


def test_keep_awake_on_linux_survives_a_missing_systemd_inhibit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No systemd (or no binary) is a warning, and the install still runs."""

    def refuse(_argv: list[str]) -> object:
        raise OSError("no such file")

    with caplog.at_level("WARNING"):
        with platform.keep_awake(platform_id=lambda: "linux", spawn=refuse):  # type: ignore[arg-type]
            pass
    assert "may be interrupted" in caplog.text


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


def test_keep_awake_on_macos_handles_already_terminated_child() -> None:
    class DeadChild:
        def terminate(self) -> None:
            raise ProcessLookupError("process already dead")

    with platform.keep_awake(
        platform_id=lambda: "macos",
        spawn=lambda _argv: DeadChild(),  # type: ignore[arg-type,return-value]
    ):
        pass


def test_keep_awake_on_macos_works_on_a_worker_thread() -> None:
    outcome: list[str] = []

    def work() -> None:
        try:
            with platform.keep_awake(platform_id=lambda: "macos"):
                outcome.append("held")
        except Exception as exc:  # pragma: no cover
            outcome.append(f"failed: {exc}")

    worker = threading.Thread(target=work)
    worker.start()
    worker.join()
    assert outcome == ["held"]


def test_keep_awake_on_macos_survives_a_caffeinate_that_will_not_start() -> None:
    """A power helper saying no must not fail an install that would have worked."""

    def refuse(_argv: list[str]) -> object:
        raise OSError("no such file")

    with platform.keep_awake(platform_id=lambda: "macos", spawn=refuse):  # type: ignore[arg-type]
        pass


def _record_execution_state(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Stand in for `SetThreadExecutionState` and collect the flags it is handed.

    Answering 1 (non-zero) is what the real call returns when Windows accepts
    the assertion; 0 is the refusal `_keep_awake_windows()` warns about.
    """
    flags: list[int] = []

    def _state(value: int) -> int:
        flags.append(value)
        return 1

    monkeypatch.setattr(platform, "_windows_execution_state", _state)
    return flags


def test_keep_awake_on_windows_refuses_the_declared_gui_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The assertion is scoped to the thread that sets it, so the GUI thread's claim is a lie.

    `SetThreadExecutionState` holds only while its own thread lives, so taking
    it on the thread that runs the event loop and then handing the install to a
    `QThread` would claim a guarantee the install does not have.

    Refused by identity against what `declare_gui_thread()` recorded, not by
    `threading.main_thread()`; the module global is restored by `monkeypatch`.
    """
    monkeypatch.setattr(platform, "_gui_thread", None)
    flags = _record_execution_state(monkeypatch)
    platform.declare_gui_thread()
    assert platform.gui_thread() is threading.current_thread()

    with pytest.raises(RuntimeError, match="this is the GUI thread"):
        with platform.keep_awake(platform_id=lambda: "windows"):
            pass
    assert flags == [], "the refused claim still touched the power API"


def test_keep_awake_on_windows_is_taken_on_a_worker_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half, with a GUI declared: the worker the app installs on is not refused."""
    monkeypatch.setattr(platform, "_gui_thread", None)
    _record_execution_state(monkeypatch)
    platform.declare_gui_thread()
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


def test_keep_awake_on_windows_holds_the_main_thread_of_a_process_with_no_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """bug-checklist §43: the headless harness's main thread IS the thread doing the install.

    `install_wiring` iterates the engine's generator on the thread that called
    `main()`, and that thread lives exactly as long as the install, so the
    assertion held there is honest. The old rule refused it by main-thread
    identity — measured on `yulon-win11-gate` 2026-09-05 05:12:16 box-local,
    where a whole TBC press ran unheld and said so once in `yulon.log`.
    """
    monkeypatch.setattr(platform, "_gui_thread", None)
    flags = _record_execution_state(monkeypatch)
    assert threading.current_thread() is threading.main_thread()

    with platform.keep_awake(platform_id=lambda: "windows"):
        pass

    assert flags == [
        platform._ES_CONTINUOUS | platform._ES_SYSTEM_REQUIRED,
        platform._ES_CONTINUOUS,
    ], "the assertion was not taken and cleared on this thread"


def test_keep_awake_on_windows_says_in_the_log_that_the_assertion_was_taken(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """§43's gate reads a log file, and absence of a warning is not evidence of a hold.

    The INFO line is the positive half: a line that was never reached and a
    line that succeeded look identical if only the warning is looked for.
    """
    monkeypatch.setattr(platform, "_gui_thread", None)
    _record_execution_state(monkeypatch)

    with caplog.at_level("INFO", logger="yulon.platform"):
        with platform.keep_awake(platform_id=lambda: "windows"):
            pass

    assert "holding this machine awake for the build: SetThreadExecutionState" in caplog.text
    assert "not holding this machine awake" not in caplog.text


def test_keep_awake_on_windows_still_runs_the_build_when_windows_refuses(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A power API answering 0 is a warning, never a refused install."""
    monkeypatch.setattr(platform, "_gui_thread", None)
    monkeypatch.setattr(platform, "_windows_execution_state", lambda _flags: 0)
    ran = False

    with caplog.at_level("WARNING", logger="yulon.platform"):
        with platform.keep_awake(platform_id=lambda: "windows"):
            ran = True

    assert ran
    assert "Windows refused the keep-awake assertion" in caplog.text


def test_the_missing_cli_help_names_every_module_that_raises_it() -> None:
    """The docstring enumerates its callers, so the enumeration is asked of the package.

    `DOCKER_CLI_MISSING_HELP` is one sentence with several homes: each module
    that cannot find the Docker CLI raises its own error type carrying this
    text, and the constant's docstring names them so a reader can reach them all
    from one place. That list said "Four modules" for as long as it took
    `maintenance` to start raising it and nobody to notice, and `console.py`
    carried a "the fourth module that has to say this" comment that went wrong
    with it (audit, 2026-08-24).

    SET equality, not "each name appears somewhere". The first version of this
    test asked whether each module's name occurred in the docstring at all, and
    a mutation that deleted `maintenance` from the LIST survived it — the same
    word was still there in the prose sentence explaining the fix. A test that a
    passing sentence can satisfy by accident is not a test. Comparing sets also
    catches the other direction, a module that stops raising it and is left
    named.

    Modules are found by reading source, not by importing: importing them all
    would drag PySide6 in, and the question is about references in the tree.
    """
    package = Path(platform.__file__).parent
    raisers = {
        path.stem
        for path in package.rglob("*.py")
        if path.name != "platform.py"
        and "DOCKER_CLI_MISSING_HELP" in path.read_text(encoding="utf-8")
    }
    assert raisers, "nothing references the constant — this test is measuring the wrong thing"

    # A constant carries no `__doc__`, so the sentence is read from source too.
    source = (package / "platform.py").read_text(encoding="utf-8")
    marker = "Modules that raise it: "
    line = source[source.index(marker) + len(marker) :].splitlines()[0]
    named = set(re.findall(r"`([a-z_]+)`", line))

    assert named == raisers, (
        f"the constant's docstring names {sorted(named)} but {sorted(raisers)} raise it; "
        f"missing {sorted(raisers - named)}, stale {sorted(named - raisers)}"
    )


def test_the_home_directory_itself_is_refused_before_the_installer_sees_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The picker opens on home, so this is the path a click-through actually produces.

    Live gate on clean Fedora 44 (2026-08-25): choosing `/home/user` passed
    `server_dir_problem()`, reached `install-wow-wotlk-fedora.sh`, and died on
    its `case "$SERVER_DIR" in /|"$HOME"|...` branch - but only AFTER the user
    had typed a sudo password into Yu'lon's own dialog and waited through
    Docker discovery. The refusal has to happen at the picker or it costs the
    user a password and a wait.
    """
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    problem = platform.server_dir_problem(tmp_path)
    assert problem is not None
    assert "home folder itself" in problem
    # The message must say what to do instead, not merely refuse - but it must
    # NOT name a specific game's folder: `_reserved_dir_reason` has no entry to
    # read one from, and naming WotLK's for all four catalog entries pointed a
    # TBC install straight at an existing WotLK install (review finding).
    assert "dedicated subfolder" in problem
    assert "wow-server-playerbots" not in problem
    # a dedicated subfolder of the same home is fine
    assert platform.server_dir_problem(tmp_path / "wow-server-playerbots") is None


def test_a_symlink_onto_a_reserved_directory_is_refused_too() -> None:
    """The scripts `realpath -m --` before their `case`; a lexical check cannot.

    On Fedora Atomic `/home` is a symlink to `/var/home`, so a picker returning
    `/home/user` and a script seeing `/var/home/user` disagree about whether the
    path is the home folder - and the user pays for that disagreement with a
    sudo password and a wait before the script refuses.
    """
    with tempfile.TemporaryDirectory() as raw:
        link = Path(raw) / "shortcut"
        try:
            link.symlink_to("/tmp", target_is_directory=True)
        except (OSError, NotImplementedError):  # pragma: no cover - needs privilege on Windows
            pytest.skip("cannot create a directory symlink on this machine")
        if link.resolve() == link:  # pragma: no cover - resolution disabled
            pytest.skip("symlinks are not resolved on this filesystem")
        problem = platform.server_dir_problem(link)
        assert problem is not None and "system directory" in problem


# ---------------------------------------------------------------- WSL-resident

# Real bytes, captured from `wsl -l -q` on a Windows 11 box (2026-08-26).
# `wsl.exe` writes UTF-16LE, and decoding it as UTF-8 yields NUL-riddled
# garbage rather than an error - so the wrong reading looks like a distro named
# "d\x00m\x00l\x00...". Two days of this project have already been spent on
# that, which is why the fixture is the real thing rather than a hand-written
# unicode string.
WSL_LIST_UTF16 = (
    b"d\x00m\x00l\x00-\x00a\x00r\x00c\x00h\x00\r\x00\n\x00"
    b"d\x00o\x00c\x00k\x00e\x00r\x00-\x00d\x00e\x00s\x00k\x00t\x00o\x00p\x00\r\x00\n\x00"
)


def test_docker_prefix_is_the_local_cli_when_no_distro_is_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ordinary case is unchanged: whatever `docker_program()` resolved."""
    monkeypatch.setattr(platform, "docker_program", lambda: "docker")
    assert platform.docker_prefix(None) == ("docker",)


def test_docker_prefix_routes_through_wsl_for_a_distro(monkeypatch: pytest.MonkeyPatch) -> None:
    """A server inside a distro is reached by its own docker, not Docker Desktop's.

    Measured against a real WSL-resident server (2026-08-26): `wsl -d dml-arch
    -- docker compose ps` returns rc=0 and the running container, and exit codes
    propagate faithfully - which is what makes this substitution safe for the 21
    lifecycle call sites that read them.
    """
    monkeypatch.setattr(platform, "_which", lambda name, path=None: "C:\\Windows\\wsl.exe")
    assert platform.docker_prefix("dml-arch") == (
        "C:\\Windows\\wsl.exe",
        "-d",
        "dml-arch",
        "--",
        "docker",
    )


def test_docker_prefix_is_none_when_the_host_cannot_reach_docker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """None is a real answer, the same one `docker_program()` gives.

    Callers already turn that into an error carrying DOCKER_CLI_MISSING_HELP, so
    a Windows box with no WSL fails the way a box with no docker already does
    rather than through a new channel.
    """
    monkeypatch.setattr(platform, "docker_program", lambda: None)
    assert platform.docker_prefix(None) is None

    monkeypatch.setattr(platform, "_which", lambda name, path=None: None)
    assert platform.docker_prefix("dml-arch") is None


def test_docker_prefix_does_not_ask_the_host_about_its_own_docker_for_a_distro(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A WSL install must not need Docker Desktop installed at all.

    The tester who prompted this has NO Docker Desktop on Windows - the daemon
    lives inside the distro. Resolving the local CLI first would refuse a
    perfectly good server for the absence of something it never uses.
    """
    asked = []
    monkeypatch.setattr(
        platform, "docker_program", lambda: asked.append("local") or None  # type: ignore[func-returns-value]
    )
    monkeypatch.setattr(platform, "_which", lambda name, path=None: "wsl.exe")
    assert platform.docker_prefix("dml-arch") is not None
    assert asked == [], "the local docker CLI was consulted for a WSL install"


def test_wsl_env_names_the_variables_that_must_cross(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without WSLENV a forwarded variable arrives EMPTY, not missing.

    `apply.py` runs `docker exec -e MYSQL_PWD` with no `=value` deliberately, so
    the password never appears in an argv `ps` can read. Measured: without
    WSLENV the inner process sees `[]`, with it `[from-windows]` - so forgetting
    this produces an authentication failure rather than a missing-setting error,
    which is the worst kind of bug to debug.
    """
    monkeypatch.delenv("WSLENV", raising=False)
    env = platform.wsl_env({"MYSQL_PWD": "hunter2"})
    assert env["MYSQL_PWD"] == "hunter2"
    assert "MYSQL_PWD" in env["WSLENV"].split(":")


def test_wsl_env_keeps_an_existing_wslenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Someone else's WSLENV is not ours to discard."""
    monkeypatch.setenv("WSLENV", "THEIRS/p")
    env = platform.wsl_env({"MYSQL_PWD": "hunter2"})
    assert "THEIRS/p" in env["WSLENV"]
    assert "MYSQL_PWD" in env["WSLENV"]


def test_wsl_env_marks_a_path_variable_for_translation(monkeypatch: pytest.MonkeyPatch) -> None:
    """T376: a Windows folder handed to a distro has to arrive as the distro's spelling of it.

    `/p` is what tells WSL to turn `C:\\Users\\pk\\...` into `/mnt/c/Users/pk/...`;
    without it buildx inside the distro is handed a path it reads as relative.
    A variable not named a path keeps the bare name.
    """
    monkeypatch.delenv("WSLENV", raising=False)
    env = platform.wsl_env({"BUILDX_CONFIG": "C:\\cfg", "OTHER": "x"}, paths=("BUILDX_CONFIG",))
    assert env["BUILDX_CONFIG"] == "C:\\cfg"
    assert env["WSLENV"].split(":") == ["BUILDX_CONFIG/p", "OTHER"]


def test_wsl_env_replaces_an_existing_unflagged_spelling_of_a_path_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare `BUILDX_CONFIG` already in WSLENV would win the crossing untranslated (T376)."""
    monkeypatch.setenv("WSLENV", "THEIRS/p:BUILDX_CONFIG")
    env = platform.wsl_env({"BUILDX_CONFIG": "C:\\cfg"}, paths=("BUILDX_CONFIG",))
    assert env["WSLENV"].split(":") == ["THEIRS/p", "BUILDX_CONFIG/p"]


def test_wsl_distros_decodes_utf16(monkeypatch: pytest.MonkeyPatch) -> None:
    """`wsl.exe` writes UTF-16LE and a UTF-8 read returns garbage, not an error."""
    monkeypatch.setattr(platform, "_wsl_list_bytes", lambda: WSL_LIST_UTF16)
    assert platform.wsl_distros() == ("dml-arch", "docker-desktop")


def test_wsl_distros_is_empty_rather_than_raising_without_wsl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Windows box with no WSL, and every non-Windows host, has no distros."""
    monkeypatch.setattr(platform, "_wsl_list_bytes", lambda: None)
    assert platform.wsl_distros() == ()


def test_a_utf8_read_of_the_same_bytes_would_have_been_garbage() -> None:
    """The fixture earns its place: prove the wrong decoding is silently wrong.

    Not a test of our code - a test of the trap, so that anyone tempted to
    simplify `wsl_distros()` to `subprocess.run(..., text=True)` sees what that
    produces before they try it.
    """
    naive = [d for d in WSL_LIST_UTF16.decode("utf-8", "ignore").split() if d]
    assert naive != ["dml-arch", "docker-desktop"]
    assert "\x00" in naive[0], "the UTF-8 reading is expected to carry NUL bytes"


def test_wsl_linux_path_converts_a_unc_path_back_to_the_distro_view() -> None:
    """`\\\\wsl.localhost\\dml-arch\\home\\dml\\x` is `/home/dml/x` to the distro.

    A WSL install's `server_dir` is stored in its Windows UNC form, because that
    is what the picker returns and what `compose_file()` can read. Docker inside
    the distro knows nothing of it, so the argv needs the Linux spelling.
    """
    got = platform.wsl_linux_path(Path(r"\\wsl.localhost\dml-arch\home\dml\games\srv"))
    assert got == "/home/dml/games/srv"
    assert platform.wsl_linux_path(Path(r"\\wsl$\Ubuntu\srv\wow")) == "/srv/wow"


def test_wsl_linux_path_is_none_for_a_path_that_is_not_in_wsl() -> None:
    """An ordinary Windows path has no Linux spelling, and guessing one would lie."""
    assert platform.wsl_linux_path(Path(r"C:\Users\user\srv")) is None
    assert platform.wsl_linux_path(Path(r"\\nas\share\srv")) is None


def test_wsl_linux_path_keeps_the_distro_root_itself() -> None:
    """The share root is the distro's `/`."""
    assert platform.wsl_linux_path(Path(r"\\wsl.localhost\dml-arch")) == "/"


def test_a_wsl_path_is_refused_in_words_that_fit_what_happened() -> None:
    """`\\\\wsl.localhost\\...` is a network path to Windows, but not to the user.

    A tester's server, installed by the DML Launcher, lives inside a WSL distro.
    The generic refusal told them to "pick a folder on this machine's own disk" -
    which it IS. Docker Desktop cannot bind-mount it ("is not a valid Windows
    path", measured on Windows 11), so the refusal is right and only the wording
    was wrong.
    """
    for text in (r"\\wsl.localhost\dml-arch\home\dml\games\srv", r"\\wsl$\Ubuntu\srv"):
        problem = platform.server_dir_problem(Path(text))
        assert problem is not None, f"{text} was accepted"
        assert "WSL" in problem, f"the WSL case is not named in: {problem}"
        assert "own disk" not in problem, "the generic network wording leaked into the WSL case"


def test_an_ordinary_unc_path_keeps_the_network_wording() -> None:
    """A real file share is still a file share."""
    problem = platform.server_dir_problem(Path(r"\\nas\media\srv"))
    assert problem is not None
    assert "network path" in problem
    assert "WSL" not in problem


# ------------------------------------------------------------------- SELinux
# Every probe below has THREE outcomes, and these tests are shaped around
# telling them apart: yes, no, and could-not-ask. The bash lineage of these
# functions shipped a bug for exactly that reason — a helper the test harness
# had not lifted exited 127, the caller read 127 as "this filesystem cannot
# hold labels", and the Fedora install went unlabelled while the test asserted
# an empty list and passed. So a fake `run` that RAISES (the command is not
# there) must not answer the same as one that returns non-zero, and neither may
# answer the same as a clean "no".


def _done(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, "")


def _never(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """A runner that must not be reached; the kernel's own file answered already."""
    raise AssertionError(argv)


def _no_tools(name: str) -> str | None:
    return None


def _has_getenforce(name: str) -> str | None:
    return "/usr/sbin/getenforce" if name == "getenforce" else None


def test_selinux_enforcing_reads_selinuxfs_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`/sys/fs/selinux/enforce` is the kernel's own answer; `getenforce` is the fallback."""
    monkeypatch.setattr(platform.sys, "platform", "linux")
    enforce = tmp_path / "enforce"
    enforce.write_text("1", encoding="utf-8")
    assert platform.selinux_enforcing(enforce_path=enforce, run=_never, which=_no_tools) is True
    enforce.write_text("0\n", encoding="utf-8")
    assert platform.selinux_enforcing(enforce_path=enforce, run=_never, which=_no_tools) is False


def test_selinux_enforcing_falls_back_to_getenforce_then_to_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No selinuxfs: ask the tool when it is there, and read its absence as a definite no."""
    monkeypatch.setattr(platform.sys, "platform", "linux")
    missing = tmp_path / "no-selinuxfs"
    calls: list[list[str]] = []

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return _done("Enforcing\n")

    assert platform.selinux_enforcing(enforce_path=missing, run=run, which=_has_getenforce) is True
    assert calls == [["getenforce"]]

    def permissive(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return _done("Permissive\n")

    def disabled(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return _done("Disabled\n")

    for said in (permissive, disabled):
        got = platform.selinux_enforcing(enforce_path=missing, run=said, which=_has_getenforce)
        assert got is False, said.__name__
    # No selinuxfs and no getenforce: SELinux is not on this box — that is a
    # "no", not a shrug. Ubuntu and Arch must not get an "unchecked" preflight
    # line for a subsystem they do not have.
    assert platform.selinux_enforcing(enforce_path=missing, run=run, which=_no_tools) is False


def test_selinux_enforcing_separates_a_no_from_a_tool_it_could_not_ask(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A broken, unstartable or babbling `getenforce` is `None`, never `False`.

    `False` means "SELinux is not enforcing here", and the install acts on it:
    no `:z`, no relabel, a green preflight line. A non-zero exit, an `OSError`
    or an answer this code does not recognise says nothing of the sort, and
    letting any of those spell "no" is the exact bug the shell version shipped.
    """
    monkeypatch.setattr(platform.sys, "platform", "linux")
    missing = tmp_path / "no-selinuxfs"

    def broken(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return _done("", 1)

    def not_there(argv: list[str]) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(2, "No such file or directory", "getenforce")

    def babbling(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return _done("getenforce: SELinux is disabled on this system\n")

    for run in (broken, not_there, babbling):
        got = platform.selinux_enforcing(enforce_path=missing, run=run, which=_has_getenforce)
        assert got is None, run.__name__


def test_selinux_enforcing_falls_through_an_unreadable_selinuxfs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A path that exists but will not read is not an answer either; ask the tool."""
    monkeypatch.setattr(platform.sys, "platform", "linux")
    unreadable = tmp_path / "enforce-dir"
    unreadable.mkdir()

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return _done("Enforcing\n")

    got = platform.selinux_enforcing(enforce_path=unreadable, run=run, which=_has_getenforce)
    assert got is True


def test_selinux_enforcing_is_none_off_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows and macOS have no SELinux to be enforcing or not, and no probe runs."""
    monkeypatch.setattr(platform.sys, "platform", "win32")
    assert platform.selinux_enforcing(run=_never, which=_has_getenforce) is None


def test_filesystem_type_asks_stat_for_the_first_existing_ancestor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The server dir rarely exists at preflight; its parent's filesystem is the answer."""
    monkeypatch.setattr(platform.sys, "platform", "linux")
    calls: list[list[str]] = []

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return _done("ext2/ext3\n")

    assert platform.filesystem_type(tmp_path / "wow" / "deeper", run=run) == "ext2/ext3"
    assert calls == [["stat", "-f", "-c", "%T", str(tmp_path)]]
    monkeypatch.setattr(platform.sys, "platform", "darwin")
    assert platform.filesystem_type(tmp_path, run=run) is None


def test_filesystem_type_is_none_whenever_stat_did_not_answer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Non-zero, unstartable, or nothing on stdout: unknown, and never an empty type.

    `""` would sail through `selinux_labels_supported()` as a filesystem merely
    not on the deny-list — the same "a failure reads as a yes" shape that cost
    the shell relabel six hours of CI.
    """
    monkeypatch.setattr(platform.sys, "platform", "linux")

    def broken(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return _done("", 1)

    def not_there(argv: list[str]) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(2, "No such file or directory", "stat")

    def silent(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return _done("  \n")

    for run in (broken, not_there, silent):
        assert platform.filesystem_type(tmp_path, run=run) is None, run.__name__


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="needs GNU coreutils stat")
def test_filesystem_type_reads_the_real_stat_on_linux(tmp_path: Path) -> None:
    """The argv is only right if a real `stat` accepts it, and no fake can prove that."""
    got = platform.filesystem_type(tmp_path / "not-created-yet")
    assert got is not None
    assert got == got.strip().lower()
    assert " " not in got


@pytest.mark.parametrize(
    ("fs_type", "supported"),
    [
        (None, True),
        ("ext2/ext3", True),
        ("btrfs", True),
        ("xfs", True),
        ("ntfs", False),
        ("NTFS3", False),
        ("fuseblk", False),
        ("exfat", False),
        ("nfs4", False),
        ("9p", False),
    ],
)
def test_selinux_labels_supported_is_a_deny_list(fs_type: str | None, supported: bool) -> None:
    """Unknown means labels work (the common case); only the listed ones cannot carry them."""
    assert platform.selinux_labels_supported(fs_type) is supported


def test_the_deny_list_is_the_one_the_fedora_gate_passed_with() -> None:
    """The shipped installers' `case` arm, verbatim; a name dropped here silently arms `:z`."""
    assert platform.SELINUX_NOLABEL_FS == frozenset(
        {"exfat", "ntfs", "ntfs3", "fuseblk", "msdos", "vfat", "cifs", "smb2", "nfs", "nfs4", "9p"}
    )


def test_bind_label_is_z_only_when_enforcing_on_a_labelable_filesystem() -> None:
    assert platform.bind_label(enforcing=True, fs_type="ext2/ext3") == ":z"
    assert platform.bind_label(enforcing=True, fs_type=None) == ":z"
    assert platform.bind_label(enforcing=True, fs_type="ntfs") == ""
    assert platform.bind_label(enforcing=False, fs_type="ext2/ext3") == ""
    assert platform.bind_label(enforcing=None, fs_type="ext2/ext3") == ""


def test_bind_label_answers_only_the_two_strings_composegen_will_splice() -> None:
    """`render()` refuses anything else, and that refusal lands mid-install, not here.

    Nothing wires the two together until A.5, so this and its twin in
    `test_composegen.py` are the whole of the agreement between them.
    """
    fs_types: list[str | None] = [None, "", "ext2/ext3", "xfs", "9p", "NTFS3", "tmpfs"]
    answers = {
        platform.bind_label(enforcing=enforcing, fs_type=fs_type)
        for enforcing in (True, False, None)
        for fs_type in fs_types
    }
    assert answers <= {"", ":z"}, answers


# ------------------------------------------------- relabelling and --user args
# `relabel_for_containers()` reports what it DID, not what it learned, so its
# single `False` is not the collapsed "could not ask" the probes above refuse:
# a missing `chcon`, a `chcon` that would not start and a `chcon` that exited
# non-zero all leave the same fact behind — the files are unlabelled — and each
# one says so in the log. `:z` on the bind lines is what carries the install.


def test_relabel_for_containers_runs_chcon_without_sudo(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The Fedora script's `selinux_label_for_containers`, ported.

    Recursive, our own files, no sudo.
    """
    calls: list[list[str]] = []

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return _done()

    def which(name: str) -> str | None:
        return "/usr/bin/chcon" if name == "chcon" else None

    assert platform.relabel_for_containers(tmp_path, run=run, which=which) is True
    assert calls == [["chcon", "-Rt", "container_file_t", str(tmp_path)]]
    with caplog.at_level("WARNING"):
        assert (
            platform.relabel_for_containers(tmp_path, run=lambda a: _done("", 1), which=which)
            is False
        )
        assert platform.relabel_for_containers(tmp_path, run=run, which=_no_tools) is False

        def refuse(argv: list[str]) -> subprocess.CompletedProcess[str]:
            raise OSError("no such file")

        assert platform.relabel_for_containers(tmp_path, run=refuse, which=which) is False
    assert caplog.text.count("could not relabel") == 3
    # The three failures are three different sentences, because "chcon is not
    # installed" and "chcon exit 1" send the reader to different places.
    assert len(set(caplog.text.splitlines())) == 3


def test_relabel_for_containers_asks_for_no_privilege_at_all(tmp_path: Path) -> None:
    """Privilege transparency is asserted on the ARGV, not on the sentence describing it.

    The server directory is the user's own, so labelling it needs nothing but
    the user. A `sudo`, a `pkexec` or a `chown` sneaking in here would be a
    silent privilege ask in the middle of an install — the one thing this
    project never does without the explicit consent path.
    """
    calls: list[list[str]] = []

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return _done()

    platform.relabel_for_containers(tmp_path, run=run, which=lambda name: f"/usr/bin/{name}")
    assert calls, "nothing was run at all, so this proves nothing"
    forbidden = {"sudo", "pkexec", "doas", "su", "chown", "chmod", "setfacl", "usermod"}
    for argv in calls:
        assert not forbidden & set(argv), argv


def test_container_user_args_is_uid_gid_on_linux_and_nothing_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One home for the `docker run` uid:gid policy.

    Linux only; Docker Desktop runs as the image's user.
    """
    monkeypatch.setattr(platform.os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setattr(platform.os, "getgid", lambda: 1000, raising=False)
    assert platform.container_user_args(platform_id=lambda: "linux") == ["--user", "1000:1000"]
    assert platform.container_user_args(platform_id=lambda: "macos") == []
    assert platform.container_user_args(platform_id=lambda: "windows") == []


def test_container_user_args_leaves_evidence_when_it_cannot_ask_for_a_uid(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A Linux with no `os.getuid` is a contradiction, and silence would hide it.

    The empty list is the only safe answer — a `--user` cannot be spelled
    without the numbers — but it is the same empty list Docker Desktop gets for
    a completely different reason, so the log is what tells the two apart.
    """
    monkeypatch.delattr(platform.os, "getuid", raising=False)
    monkeypatch.delattr(platform.os, "getgid", raising=False)
    with caplog.at_level("WARNING"):
        assert platform.container_user_args(platform_id=lambda: "linux") == []
    assert "getuid" in caplog.text
    caplog.clear()
    with caplog.at_level("WARNING"):
        assert platform.container_user_args(platform_id=lambda: "windows") == []
    assert caplog.text == "", "Docker Desktop having no getuid is normal, not a warning"


def test_compose_ready_is_false_when_the_plugin_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Docker engine without the Compose v2 plugin (T56).

    Reported from a Steam Deck on 0.8.65-Public. The user installed Docker by
    hand, so the daemon answered and preflight passed — and the install then
    died on step 4 of 9 with:

        the build failed (exit 125). Its last words were:
        unknown shorthand flag: 'f' in -f / Usage: docker [OPTIONS] COMMAND

    Yu'lon builds with `docker compose -f <file> build`. With no plugin the
    word `compose` is not a command, so `-f` falls through to `docker` itself
    and it prints its TOP-LEVEL usage — which is why the message names neither
    Compose nor the file it was given.

    `docker info` cannot see this: the daemon is fine. The plugin is a separate
    thing and has to be asked about separately.
    """
    monkeypatch.setattr(platform, "docker_programs", lambda: ["docker"])
    seen: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return _completed(returncode=125)

    monkeypatch.setattr(platform.runner, "run", fake_run)
    assert platform.compose_ready() is False
    assert seen and seen[0][1:] == ["compose", "version"], seen
    # Bounded like every other probe: an unbounded one hangs the preflight.
    assert platform.compose_ready(timeout=1.0) is False


def test_compose_ready_is_true_when_the_plugin_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    """And the ordinary machine is unaffected."""
    monkeypatch.setattr(platform, "docker_programs", lambda: ["docker"])
    monkeypatch.setattr(
        platform.runner,
        "run",
        lambda argv, **kw: _completed(returncode=0),
    )
    assert platform.compose_ready() is True


def test_the_compose_probe_actually_bounds_the_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default path must carry the deadline to the child (T56 review).

    `_bounded()` bounds a runner of OURS and returns anything else unchanged:

        return do.bounded(seconds) if isinstance(do, _DefaultRunner) else do

    so `runner.run` passed straight through and the advertised 10 seconds never
    reached the subprocess. The probe's own docstring claimed it was "shaped
    exactly like docker_ready()" while differing in that one line. A hung Docker
    CLI would have stalled the whole preflight.

    Asserted against the DEFAULT runner rather than an injected fake, because an
    injected fake is exactly what the original tests used and exactly why they
    could not see this.
    """
    monkeypatch.setattr(platform, "docker_programs", lambda: ["docker"])
    seen: list[object] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(kwargs.get("timeout"))
        return _completed(returncode=1)

    monkeypatch.setattr(platform.runner, "run", fake_run)
    platform.compose_ready(timeout=7.0)
    assert seen, "the probe never ran the subprocess"
    assert all(isinstance(bound, float) for bound in seen), f"unbounded probe: {seen}"


def test_a_folder_on_a_drive_that_is_not_there_is_not_confirmed_gone(tmp_path: Path) -> None:
    """An absent VOLUME is not an absent folder (T54 review).

    `os.stat()` raises `FileNotFoundError` for `E:\\Games\\Yulon Wotlk` both when
    the folder was deleted and when the whole of `E:` is unplugged, asleep, or a
    disconnected network share. The two are indistinguishable at the leaf.

    That mattered the moment T54 made the Forget control appear while Docker is
    unreachable: an offline drive takes Docker's daemon with it often enough
    (VM on that disk, machine just woken), and the user is then told the folder
    "no longer exists" and offered a button that drops the only record of a
    LIVE install, with its containers and volumes still on the machine.

    Before T54 this was prevented by accident -- no Docker, no reveal. The fix
    removed the accident, so the predicate has to mean what it says.
    """
    # A path whose PARENT does not exist either: the volume is what is missing.
    missing_volume = tmp_path / "not-a-mounted-thing" / "server"
    assert not platform.folder_is_gone(
        missing_volume
    ), "a folder under a root that is not there was reported as confirmed gone"

    # The ordinary case is unchanged: the parent is there, the folder is not.
    assert platform.folder_is_gone(tmp_path / "deleted-server")

    # And a folder that exists is still not gone.
    (tmp_path / "real").mkdir()
    assert not platform.folder_is_gone(tmp_path / "real")


# --- T574: bind_tcp tells a reserved port from a taken one --------------------------


def _wsa(code: int) -> OSError:
    """An OSError the way Windows raises one: errno set from, and winerror holding, the WSA code."""
    error = OSError(code, "socket error")
    error.winerror = code  # type: ignore[attr-defined]
    return error


@pytest.mark.parametrize(
    ("error", "windows", "want"),
    [
        (_wsa(10013), True, "reserved"),  # WSAEACCES: the excluded range / exclusive owner
        (_wsa(10048), True, "in_use"),  # WSAEADDRINUSE
        (OSError(errno.EADDRINUSE, "Address already in use"), False, "in_use"),
        # A permission error off Windows says nothing about what the daemon will be allowed.
        (OSError(13, "Permission denied"), False, "unknown"),
        (OSError(99, "Cannot assign requested address"), False, "unknown"),
        (_wsa(10049), True, "unknown"),
    ],
)
def test_classify_bind_error(error: OSError, windows: bool, want: str) -> None:
    assert platform.classify_bind_error(error, windows=windows) == want


def test_bind_tcp_sees_a_free_port_and_a_taken_one() -> None:
    import socket

    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        taken = holder.getsockname()[1]
        got = platform.bind_tcp("127.0.0.1", taken)
        assert got.status == "in_use", got
    finally:
        holder.close()
    # Free again once the holder closed (the probe must not itself leave it bound).
    again = platform.bind_tcp("127.0.0.1", taken)
    assert again.status == "free", again
    assert platform.bind_tcp("127.0.0.1", taken).status == "free"


def test_bind_tcp_reports_what_the_socket_raised() -> None:
    class Refusing:
        def setsockopt(self, *_a: object) -> None: ...

        def bind(self, _addr: object) -> None:
            raise _wsa(10013)

        def close(self) -> None: ...

    got = platform.bind_tcp("127.0.0.1", 3306, make_socket=lambda *_a: Refusing(), windows=True)
    assert (got.host, got.port, got.status) == ("127.0.0.1", 3306, "reserved")
    assert "10013" in got.detail


@pytest.mark.parametrize(("windows", "sets_reuse"), [(True, False), (False, True)])
def test_bind_tcp_asks_for_address_reuse_everywhere_but_windows(
    windows: bool, sets_reuse: bool
) -> None:
    """On Windows SO_REUSEADDR means "share it with its owner", which hides a taken port."""
    import socket

    options: list[tuple[object, ...]] = []

    class Quiet:
        def setsockopt(self, *args: object) -> None:
            options.append(args)

        def bind(self, _addr: object) -> None: ...

        def close(self) -> None: ...

    got = platform.bind_tcp("127.0.0.1", 3306, make_socket=lambda *_a: Quiet(), windows=windows)
    assert got.status == "free"
    assert bool(options) is sets_reuse
    if sets_reuse:
        assert options == [(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)]
