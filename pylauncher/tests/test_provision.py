"""Tests for Docker/WSL2 provisioning (`yulon.platform`, roadmap 5.1) through its seams.

Nothing here installs anything: `run`, `which` and `download` are fakes, and
`detect()` is pinned via `sys.platform`. The assertions are about the plans
(exact commands per OS), the honesty rules (reboot/re-login reported, nothing
silent), and the early exit when a daemon already answers.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from yulon import platform

# The directory the current Docker Desktop installs itself into, and the
# `docker.exe` inside it. Real strings from a real machine (Windows 11 Pro
# 26200, 2026-08-23) so the fakes below describe the case that was measured.
DOCKER_BIN_DIR = r"C:\Users\pk\AppData\Local\Programs\DockerDesktop\resources\bin"
DOCKER_EXE = DOCKER_BIN_DIR + r"\docker.EXE"


class _Run:
    """Records argv; answers `docker info` with `docker_rc`, everything else with 0."""

    def __init__(self, docker_rc: int = 1, fail: set[str] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.docker_rc = docker_rc
        self.fail = fail or set()

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        if argv[:2] == ["docker", "info"]:
            return subprocess.CompletedProcess(argv, self.docker_rc, "", "")
        if " ".join(argv) in self.fail:
            return subprocess.CompletedProcess(argv, 1, "", "a password is required")
        return subprocess.CompletedProcess(argv, 0, "", "")


class _OffPathWhich:
    """`shutil.which` on a box where `docker` exists but not on the live PATH.

    The bare lookup answers None — that is the defect, and what the launcher's
    own process sees after Docker Desktop's installer has run. A lookup handed
    an explicit search path finds it, exactly as the real `shutil.which` does.

    A lookup of the absolute path answers itself, also as the real one does:
    `shutil.which` treats a name containing a separator as a direct existence
    check and ignores PATH entirely. `docker_program()` relies on that to
    confirm a candidate before pinning it.
    """

    def __init__(self, bin_dir: str = DOCKER_BIN_DIR, installed: bool = True) -> None:
        self.bin_dir = bin_dir
        self.installed = installed

    def __call__(self, name: str, path: str | None = None) -> str | None:
        if not self.installed:
            return None
        if name == DOCKER_EXE:
            return DOCKER_EXE
        if name != "docker" or path is None:
            return None
        return DOCKER_EXE if self.bin_dir in path else None


def _no_off_path_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the Windows PATH re-read off, for tests that are about something else.

    Without this, a developer box that is genuinely IN the defect state — Docker
    Desktop installed, its bin dir absent from this process's PATH — makes the
    Windows tests below take a second, real `docker.exe` candidate and stop
    matching their own `run.calls` assertions. Hermetic by construction rather
    than by luck.
    """
    monkeypatch.setattr(platform, "_windows_docker_programs", lambda: ())


def _no_default_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset every Program Files/AppData root so no REAL Docker Desktop is found.

    Without this these tests pass or fail depending on whether the machine
    running them happens to have Docker Desktop installed, which is exactly the
    machine-specific behaviour the code under test exists to remove.
    """
    for var in platform._DOCKER_DESKTOP_ROOT_VARS:
        monkeypatch.delenv(var, raising=False)


def _default_install(monkeypatch: pytest.MonkeyPatch, root: Path) -> Path:
    """Pretend a stock Docker Desktop lives under `root` as Program Files."""
    _no_default_install(monkeypatch)
    monkeypatch.setenv("ProgramFiles", str(root))
    exe = root / "Docker" / "Docker" / platform.DOCKER_DESKTOP_EXE
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("not really an executable", encoding="utf-8")
    return exe


def _started(calls: list[list[str]]) -> list[str]:
    """The `Start-Process` commands that were run (installer excluded)."""
    return [c[-1] for c in calls if "Start-Process" in c[-1] and "--accept-license" not in c[-1]]


def test_already_running_docker_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _Run(docker_rc=0)
    report = platform.ensure_docker(run=run)
    assert report.docker_ready and report.ok and report.done == ("docker already running",)
    assert run.calls == [["docker", "info"]]


def test_linux_engine_plan_per_package_manager() -> None:
    assert platform.docker_engine_commands("pacman", steamos=True) == [
        ["steamos-readonly", "disable"],
        ["pacman", "-Sy", "--noconfirm", "docker", "docker-compose"],
        ["steamos-readonly", "enable"],
        ["systemctl", "enable", "--now", "docker"],
    ]
    apt = platform.docker_engine_commands("apt", steamos=False)
    assert apt[0] == ["apt-get", "update"] and "docker.io" in apt[1]
    assert "docker-buildx" in apt[1]  # compose build needs BuildKit; docker.io lacks it
    assert platform.docker_engine_commands("dnf", steamos=False)[0][:3] == [
        "dnf",
        "-y",
        "install",
    ]
    # The group join is not in any plan, on purpose: the argv exists only
    # inside the consent branch, so there is no ungated construction site.
    for pm in ("pacman", "apt", "dnf", "zypper"):
        for steamos in (True, False):
            plan = platform.docker_engine_commands(pm, steamos=steamos)
            assert not [c for c in plan if "usermod" in c], (pm, steamos, plan)
    assert platform.linux_package_manager(
        lambda n: "/usr/bin/apt-get" if n == "apt-get" else None
    ) == ("apt")
    assert platform.linux_package_manager(lambda n: None) is None


def test_linux_runs_under_sudo_n_and_reports_password_needs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(platform.sys, "platform", "linux")
    monkeypatch.setattr(platform, "is_steamos", lambda: False)
    run = _Run(fail={"sudo -n apt-get update"})
    report = platform.ensure_docker(
        run=run,
        which=lambda n: "/usr/bin/apt-get" if n == "apt-get" else None,
        user="pk",
        wait_seconds=0.0,
    )
    assert report.platform == "linux"
    # `id -nG` comes first and needs no privilege: whether the user is already
    # a member is settled before anything runs under sudo.
    assert run.calls[1] == ["id", "-nG", "pk"]
    assert run.calls[2] == ["sudo", "-n", "apt-get", "update"]
    assert any(s.startswith("apt-get update: exit 1") for s in report.skipped)
    assert any("needed a password" in m for m in report.manual_steps)
    # No prompter, so nothing was asked and nothing was joined — the re-login
    # advice would be false here, and used to be printed unconditionally.
    assert report.docker_group == "not-asked"
    assert not [m for m in report.manual_steps if "Log out and back in" in m]
    assert any("Skipped joining the docker group" in m for m in report.manual_steps)
    assert report.docker_ready is False and report.ok is False


def test_linux_dry_run_runs_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform.sys, "platform", "linux")
    monkeypatch.setattr(platform, "is_steamos", lambda: True)
    run = _Run()
    report = platform.ensure_docker(
        run=run,
        which=lambda n: "/usr/bin/pacman" if n == "pacman" else None,
        dry_run=True,
        user="deck",
    )
    assert run.calls == [["docker", "info"]]
    assert report.skipped[0] == "(dry run) steamos-readonly disable"
    assert report.docker_ready is False


def test_linux_without_a_package_manager_gives_manual_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(platform.sys, "platform", "linux")
    report = platform.ensure_docker(run=_Run(), which=lambda n: None)
    assert report.done == () and "Install Docker Engine by hand" in report.manual_steps[0]


def test_windows_installs_wsl_first_and_reports_the_reboot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform.sys, "platform", "win32")
    _no_off_path_docker(monkeypatch)

    class _WinRun(_Run):
        def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
            self.calls.append(argv)
            if argv[:2] == ["docker", "info"]:
                return subprocess.CompletedProcess(argv, 1, "", "")
            if argv[:2] == ["wsl.exe", "--status"]:
                return subprocess.CompletedProcess(argv, 1, "", "not installed")
            return subprocess.CompletedProcess(argv, 0, "", "")

    run = _WinRun()
    report = platform.ensure_docker(run=run, which=lambda n: None)
    assert report.reboot_required is True and report.docker_ready is False
    assert any("Reboot Windows" in m for m in report.manual_steps)
    assert any("wsl.exe" in " ".join(c) and "--install" in " ".join(c) for c in run.calls)
    assert not any("Docker Desktop" in " ".join(c) for c in run.calls)  # not before the reboot


def test_windows_downloads_and_silently_installs_docker_desktop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(platform.sys, "platform", "win32")
    monkeypatch.setattr(platform, "config_dir", lambda: tmp_path / "appdata")
    _no_off_path_docker(monkeypatch)
    exe = _default_install(monkeypatch, tmp_path / "pf")
    downloads: list[tuple[str, Path]] = []

    def download(url: str, dest: Path) -> Path:
        downloads.append((url, dest))
        return dest

    class _WinRun(_Run):
        def __init__(self) -> None:
            super().__init__()
            self.docker_after_start = False

        def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
            self.calls.append(argv)
            if argv[:2] == ["docker", "info"]:
                return subprocess.CompletedProcess(
                    argv, 0 if self.docker_after_start else 1, "", ""
                )
            if f"Start-Process {platform._ps_quote(exe)}" in " ".join(argv):
                self.docker_after_start = True
            return subprocess.CompletedProcess(argv, 0, "", "")

    run = _WinRun()
    report = platform.ensure_docker(
        run=run, which=lambda n: None, download=download, wait_seconds=1.0
    )
    assert downloads[0][0] == platform.DOCKER_DESKTOP_WINDOWS_URL
    assert downloads[0][1] == tmp_path / "appdata" / "downloads" / "Docker Desktop Installer.exe"
    install = [c for c in run.calls if "--accept-license" in " ".join(c)]
    assert (
        install
        and "-Verb RunAs" in " ".join(install[0])
        and "--backend=wsl-2" in " ".join(install[0])
    )
    assert report.docker_ready is True and report.ok
    assert any("WSL2 present" == d for d in report.done)


def test_macos_plan_downloads_dmg_and_copies_the_app(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(platform.sys, "platform", "darwin")
    monkeypatch.setattr(platform, "config_dir", lambda: tmp_path)
    # Pin absent so a dev box that already has Docker.app in /Applications
    # doesn't silently take the "already installed" short circuit here.
    monkeypatch.setattr(platform.Path, "exists", lambda self: False)
    run = _Run()
    report = platform.ensure_docker(run=run, download=lambda u, d: d, dry_run=True)
    assert report.platform == "macos"
    assert report.skipped[0].startswith("(dry run) download https://desktop.docker.com/mac/main/")
    assert any("hdiutil attach" in s for s in report.skipped)
    assert any("cp -R /Volumes/YulonDocker/Docker.app /Applications/" in s for s in report.skipped)
    assert run.calls == [["docker", "info"]]


def test_macos_provisioning_never_escalates_privileges(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The macOS install path emits no sudo, no group join and no sudoers write.

    Roadmap 6.4.3 says to assert the privilege-transparency rule on the emitted
    argv through the run seam. The macOS branch is the one the rule is *easiest*
    to satisfy — Docker Desktop manages its own access and nobody here touches a
    Unix group — so this is less about catching a likely bug than about never
    having to wonder: each command this path runs is spelled below, and each is
    a non-`sudo`, non-escalating `hdiutil`/`cp`/`open`.

    Run NON-dry so the commands actually execute through the `_Run` seam and the
    assertion sees the real argv, not the "(dry run) …" placeholders. The
    Docker.app existence check is pinned false so a Docker-equipped developer
    box does not silently take the "already installed" short circuit and stop
    asserting anything; the ready-poll is zeroed and the download faked.
    """
    monkeypatch.setattr(platform.sys, "platform", "darwin")
    monkeypatch.setattr(platform, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(platform.Path, "exists", lambda self: False)
    run = _Run()
    report = platform.ensure_docker(
        run=run, download=lambda u, d: d, wait_seconds=0.0, dry_run=False
    )
    assert report.platform == "macos"

    text = [" ".join(argv) for argv in run.calls]
    assert not [c for c in text if c.startswith("sudo")], text
    assert not _joins_the_docker_group(run.calls), run.calls
    assert not [c for c in text if "sudoers" in c or "NOPASSWD" in c], text
    assert not [c for c in text if "chmod" in c and "docker.sock" in c], text
    # The commands that DID run are the harmless, fixed macOS set.
    assert any("hdiutil attach" in c for c in text), text
    assert any("cp -R /Volumes/YulonDocker/Docker.app /Applications/" in c for c in text)
    assert any("open -a Docker" in c for c in text)


def test_windows_provisioning_never_escalates_privileges(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The Windows install path writes no sudoers rule and joins no group (6.4.3).

    The privilege-transparency rule's Unix vocabulary — the `docker` group, a
    `sudoers.d`/`NOPASSWD` rule, `chmod 666` on the socket — has no Windows
    counterpart, so some of this is vacuous, which is itself the point: a third
    platform satisfies the rule by construction, and pinning it means a future
    contributor who ports a `docker-group`-style step to the Windows path cannot
    do it silently. The one elevation Windows DOES use, `Start-Process -Verb
    RunAs`, is the interactive UAC prompt the user clicks, not silent host
    escalation — and it is asserted present, so the test exercises the real
    install rather than the already-installed short circuit.

    Run NON-dry so the argv is seen, with the same fake as
    `test_windows_provisioning_finishes_without_a_manual_step`.
    """
    monkeypatch.setattr(platform.sys, "platform", "win32")
    monkeypatch.setattr(platform, "config_dir", lambda: tmp_path)
    desktop_exe = _default_install(monkeypatch, tmp_path / "pf")
    which = _OffPathWhich(installed=False)
    monkeypatch.setattr(platform, "_which", which)
    monkeypatch.setattr(platform, "_registry_search_path", lambda: DOCKER_BIN_DIR)

    class _WinRun(_Run):
        def __init__(self) -> None:
            super().__init__()
            self.engine_up = False

        def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
            self.calls.append(argv)
            shown = " ".join(argv)
            if "--accept-license" in shown:
                which.installed = True
            if f"Start-Process {platform._ps_quote(desktop_exe)}" in shown:
                self.engine_up = True
            if argv[1:] == ["info"]:
                ready = self.engine_up and argv[0] == DOCKER_EXE
                return subprocess.CompletedProcess(argv, 0 if ready else 1, "", "")
            return subprocess.CompletedProcess(argv, 0, "", "")

    run = _WinRun()
    report = platform.ensure_docker(
        run=run, which=lambda n: None, download=lambda u, d: d, wait_seconds=1.0
    )
    assert report.platform == "windows"
    assert report.docker_ready is True and report.ok

    text = [" ".join(argv) for argv in run.calls]
    # No Unix privilege escalation survives on this path, ever.
    assert not [c for c in text if c.startswith("sudo")], text
    assert not _joins_the_docker_group(run.calls), run.calls
    assert not [c for c in text if "sudoers" in c or "NOPASSWD" in c], text
    assert not [c for c in text if "chmod" in c and "docker.sock" in c], text
    # The one elevation Windows uses is the interactive UAC prompt, present and
    # explicit — the install actually went through the elevated path, so the
    # no-silent-escalation assertions above are about a run that would have
    # escalated there had a group-join been bolted on.
    assert any("-Verb RunAs" in c for c in text), text


def test_ensure_wsl2_is_a_noop_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform.sys, "platform", "linux")
    report = platform.ensure_wsl2(run=_Run(docker_rc=0))
    assert report.done == ("WSL2 not needed on this OS",) and report.docker_ready is True


def test_powershell_quoting_survives_an_apostrophe_in_the_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A user profile like `O'Brien` must not break out of the elevated command string."""
    home = tmp_path / "O'Brien"
    monkeypatch.setattr(platform.sys, "platform", "win32")
    monkeypatch.setattr(platform, "config_dir", lambda: home)
    _no_off_path_docker(monkeypatch)
    _no_default_install(monkeypatch)  # never consult the real machine's Program Files

    class _WinRun(_Run):
        def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
            self.calls.append(argv)
            return subprocess.CompletedProcess(
                argv, 0 if argv[:2] != ["docker", "info"] else 1, "", ""
            )

    run = _WinRun()
    platform.ensure_docker(run=run, which=lambda n: None, download=lambda u, d: d, wait_seconds=0.0)
    install = [c for c in run.calls if "--accept-license" in " ".join(c)]
    assert install, "the silent install command was never built"
    command = install[0][-1]
    assert "O''Brien" in command  # doubled, i.e. escaped
    assert command.count("Start-Process '") == 1


# ------------------------------------------------------ finding the docker CLI
# Windows never revises a running process's environment, so the launcher that
# runs Docker Desktop's installer cannot see the PATH entry that installer
# writes. Everything below is about resolving `docker` anyway, in the same run.


def test_windows_docker_ready_uses_the_path_the_installer_just_wrote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The engine is up and plain `docker` is unresolvable. Both must be true at once.

    This is the reproduction, reduced: `shutil.which("docker")` answers None
    because this process's PATH predates the install, while the binary is
    sitting in a directory the registry already knows about.
    """
    monkeypatch.setattr(platform.sys, "platform", "win32")
    monkeypatch.setattr(platform, "_which", _OffPathWhich())
    monkeypatch.setattr(platform, "_registry_search_path", lambda: DOCKER_BIN_DIR)

    class _WinRun(_Run):
        def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
            self.calls.append(argv)
            # Only the absolute path can be started; the bare name is exactly
            # what a real CreateProcess cannot find.
            rc = 0 if argv[0] == DOCKER_EXE else 1
            return subprocess.CompletedProcess(argv, rc, "", "")

    run = _WinRun()
    assert platform.docker_ready(run) is True
    assert run.calls == [["docker", "info"], [DOCKER_EXE, "info"]]


def test_windows_provisioning_finishes_without_a_manual_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A first run installs Docker Desktop, starts it, and finds it — no restart.

    The whole point of the fix. The fake follows the real timeline: nothing is
    on PATH to begin with, the silent install puts `docker.exe` somewhere the
    registry names (and this process still cannot see), and the engine answers
    only once Docker Desktop has been started. Before the fix this ended in
    "open Docker Desktop ... then try again" with the engine already running.
    """
    monkeypatch.setattr(platform.sys, "platform", "win32")
    monkeypatch.setattr(platform, "config_dir", lambda: tmp_path)
    # Where the silent install puts the app. Pinned because the start step now
    # resolves a real path instead of the bare name `Start-Process 'Docker
    # Desktop'`, which resolved nowhere on any machine.
    desktop_exe = _default_install(monkeypatch, tmp_path / "pf")
    which = _OffPathWhich(installed=False)
    monkeypatch.setattr(platform, "_which", which)
    monkeypatch.setattr(platform, "_registry_search_path", lambda: DOCKER_BIN_DIR)

    class _WinRun(_Run):
        def __init__(self) -> None:
            super().__init__()
            self.engine_up = False

        def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
            self.calls.append(argv)
            shown = " ".join(argv)
            if "--accept-license" in shown:
                which.installed = True  # the installer wrote a PATH we cannot see
            if f"Start-Process {platform._ps_quote(desktop_exe)}" in shown:
                self.engine_up = True
            if argv[1:] == ["info"]:
                ready = self.engine_up and argv[0] == DOCKER_EXE
                return subprocess.CompletedProcess(argv, 0 if ready else 1, "", "")
            return subprocess.CompletedProcess(argv, 0, "", "")

    run = _WinRun()
    report = platform.ensure_docker(
        run=run, which=lambda n: None, download=lambda u, d: d, wait_seconds=1.0
    )
    assert report.docker_ready is True and report.ok
    assert report.manual_steps == ()
    assert [DOCKER_EXE, "info"] in run.calls


def test_an_already_running_docker_desktop_is_never_reinstalled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Docker up but off our PATH must short-circuit, not download 500MB over it.

    `ensure_docker()`'s early exit runs through the same resolution, so the
    same blindness used to make it reinstall a Docker Desktop that was working.
    """
    monkeypatch.setattr(platform.sys, "platform", "win32")
    monkeypatch.setattr(platform, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(platform, "_which", _OffPathWhich())
    monkeypatch.setattr(platform, "_registry_search_path", lambda: DOCKER_BIN_DIR)
    downloads: list[str] = []

    def download(url: str, dest: Path) -> Path:
        downloads.append(url)
        return dest

    class _WinRun(_Run):
        def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
            self.calls.append(argv)
            rc = 0 if argv == [DOCKER_EXE, "info"] else 1
            return subprocess.CompletedProcess(argv, rc, "", "")

    run = _WinRun()
    report = platform.ensure_docker(run=run, which=lambda n: None, download=download)
    assert report.done == ("docker already running",) and report.ok
    assert downloads == []
    # Nothing beyond the two probes: no WSL check, no installer, no Start-Process.
    assert run.calls == [["docker", "info"], [DOCKER_EXE, "info"]]


def test_windows_falls_back_to_the_known_install_directories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A registry that will not answer still leaves the default layouts to try."""
    bin_dir = tmp_path / "resources" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "docker.exe").write_text("", encoding="utf-8")

    def _no_registry() -> str:
        raise OSError("access denied")

    monkeypatch.setattr(platform.sys, "platform", "win32")
    monkeypatch.setattr(platform, "_which", lambda name, path=None: None)
    monkeypatch.setattr(platform, "_registry_search_path", _no_registry)
    monkeypatch.setattr(platform, "_windows_docker_bins", lambda: (bin_dir,))
    assert platform.docker_programs() == ("docker", str(bin_dir / "docker.exe"))


def test_a_docker_on_the_live_path_costs_nothing_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    """The healthy machine must not pay for the broken one's rediscovery."""

    def _boom() -> str:
        raise AssertionError("the registry was read on a machine that did not need it")

    monkeypatch.setattr(platform.sys, "platform", "win32")
    monkeypatch.setattr(platform, "_which", lambda name, path=None: r"C:\bin\docker.exe")
    monkeypatch.setattr(platform, "_registry_search_path", _boom)
    assert platform.docker_programs() == ("docker",)


def test_macos_falls_back_to_docker_desktops_own_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A Finder-launched .app runs with launchd's PATH, which never names docker.

    `/usr/bin:/bin:/usr/sbin:/sbin` is what a double-clicked `Yulon.app` gets,
    and Docker Desktop's CLI is a symlink in `/usr/local/bin` - so plain
    `docker` was a `FileNotFoundError`, `ensure_docker()` opened Docker.app and
    polled for 180 seconds, and the install failed with Docker fully running
    (macOS gate, 2026-08-25). The Windows fix of 2026-08-23, on a second OS.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text("", encoding="utf-8")
    monkeypatch.setattr(platform.sys, "platform", "darwin")
    monkeypatch.setattr(platform, "_which", lambda name, path=None: None)
    monkeypatch.setattr(platform, "_macos_docker_bins", lambda: (tmp_path / "missing", bin_dir))
    assert platform.docker_programs() == ("docker", str(bin_dir / "docker"))


def test_macos_with_docker_on_the_live_path_stats_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom() -> tuple[Path, ...]:
        raise AssertionError("the fallback directories were read on a machine that did not need it")

    monkeypatch.setattr(platform.sys, "platform", "darwin")
    monkeypatch.setattr(platform, "_which", lambda name, path=None: "/usr/local/bin/docker")
    monkeypatch.setattr(platform, "_macos_docker_bins", _boom)
    assert platform.docker_programs() == ("docker",)


def test_the_macos_fallback_directories_are_where_docker_desktop_puts_its_cli() -> None:
    assert platform._macos_docker_bins() == (
        Path("/usr/local/bin"),
        Path("/opt/homebrew/bin"),
        Path("/Applications/Docker.app/Contents/Resources/bin"),
    )


@pytest.mark.parametrize(("sys_platform", "expected"), [("linux", "linux")])
def test_off_windows_nothing_changes(
    monkeypatch: pytest.MonkeyPatch, sys_platform: str, expected: str
) -> None:
    """`shutil.which` is correct and sufficient on Linux, so no extra machinery runs.

    Asserted as "the registry is never touched and exactly one command runs",
    not merely as a return value: a PATH re-read would be meaningless on Linux,
    where a shell exports PATH to the children it starts. macOS used to be in
    this parametrization; see `test_macos_falls_back_to_docker_desktops_own_cli`.
    """

    def _boom() -> str:
        raise AssertionError(f"the Windows PATH re-read ran on {expected}")

    monkeypatch.setattr(platform.sys, "platform", sys_platform)
    monkeypatch.setattr(platform, "_registry_search_path", _boom)
    monkeypatch.setattr(platform, "_which", lambda name, path=None: None)
    assert platform.detect() == expected
    assert platform.docker_programs() == ("docker",)
    run = _Run(docker_rc=1)
    assert platform.docker_ready(run) is False
    assert run.calls == [["docker", "info"]]


def test_a_candidate_that_cannot_be_started_is_not_the_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`subprocess` raising OSError means "no such binary", not "no daemon"."""
    monkeypatch.setattr(platform.sys, "platform", "win32")
    monkeypatch.setattr(platform, "_which", _OffPathWhich())
    monkeypatch.setattr(platform, "_registry_search_path", lambda: DOCKER_BIN_DIR)
    tried: list[str] = []

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        tried.append(argv[0])
        if argv[0] == "docker":
            raise FileNotFoundError(2, "The system cannot find the file specified")
        return subprocess.CompletedProcess(argv, 0, "", "")

    assert platform.docker_ready(run) is True
    assert tried == ["docker", DOCKER_EXE]


# ------------------------------------------------- picking ONE of the candidates
# `docker_programs()` answers "everything worth trying". Every argv in the app
# needs exactly one name, and until 2026-08-23 every one of them hardcoded
# `docker` — so provisioning could find the CLI and the very next
# `docker compose up` still could not.


def _unresolved(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo conftest's pin, so the real resolution runs."""
    monkeypatch.setattr(platform, "_resolved_docker_cli", None)


def test_docker_program_picks_the_exe_the_live_path_cannot_see(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The plain name is skipped because it resolves nowhere — the absolute path wins."""
    _unresolved(monkeypatch)
    monkeypatch.setattr(platform.sys, "platform", "win32")
    monkeypatch.setattr(platform, "_which", _OffPathWhich())
    monkeypatch.setattr(platform, "_registry_search_path", lambda: DOCKER_BIN_DIR)
    assert platform.docker_program() == DOCKER_EXE


def test_a_healthy_machine_still_gets_the_plain_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing changes where `docker` already works — no absolute path in any argv."""
    _unresolved(monkeypatch)
    monkeypatch.setattr(platform.sys, "platform", "win32")
    monkeypatch.setattr(platform, "_which", lambda name, path=None: r"C:\bin\docker.exe")
    assert platform.docker_program() == "docker"


def _off_path_which(name: str, path: str | None = None) -> str | None:
    """`docker` resolves nowhere; any absolute candidate confirms itself."""
    return None if name == "docker" else name


def test_the_found_cli_puts_its_own_directory_on_this_processs_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """argv[0] was only half the fix: the CLI shells out to helpers BY NAME.

    Reported from a Mac (2026-08-26). `docker` was found inside the bundle and
    every command started; the first one that had to reach a registry died:

        the bind-mount probe of /Users/js/Documents failed: Unable to find
        image 'alpine/git@sha256:c028...' locally
        docker: error getting credentials - err: exec:
        "docker-credential-desktop": executable file not found in $PATH

    `docker` resolves its credential helper through PATH, and the PATH a
    Finder-launched .app inherits from launchd is the same one that could not
    name `docker` in the first place. So no pull could authenticate, the
    bind-mount probe read the failed pull as a failed mount, and preflight
    refused the install with "a container could not see <folder>" — about a
    folder that was shared, on a machine where the same command worked from
    Terminal.
    """
    bin_dir = tmp_path / "Docker.app" / "Contents" / "Resources" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "docker").write_text("", encoding="utf-8")
    _unresolved(monkeypatch)
    monkeypatch.setattr(platform.sys, "platform", "darwin")
    monkeypatch.setattr(platform, "_which", _off_path_which)
    monkeypatch.setattr(platform, "_macos_docker_bins", lambda: (bin_dir,))
    monkeypatch.setenv("PATH", os.pathsep.join(["/usr/bin", "/bin", "/usr/sbin", "/sbin"]))

    assert platform.docker_program() == str(bin_dir / "docker")
    entries = os.environ["PATH"].split(os.pathsep)
    assert entries[0] == str(bin_dir)
    assert "/usr/bin" in entries, "the inherited PATH was replaced instead of extended"


def test_a_plain_docker_leaves_the_path_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare name has no directory to adopt, and a working PATH nothing to fix."""
    _unresolved(monkeypatch)
    monkeypatch.setattr(platform.sys, "platform", "darwin")
    monkeypatch.setattr(platform, "_which", lambda name, path=None: "/usr/local/bin/docker")
    monkeypatch.setenv("PATH", os.pathsep.join(["/usr/local/bin", "/usr/bin"]))

    assert platform.docker_program() == "docker"
    assert os.environ["PATH"] == os.pathsep.join(["/usr/local/bin", "/usr/bin"])


def test_a_directory_already_on_the_path_is_not_added_twice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Resolution re-runs on every miss; the PATH must not grow an entry per call."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text("", encoding="utf-8")
    _unresolved(monkeypatch)
    monkeypatch.setattr(platform.sys, "platform", "darwin")
    monkeypatch.setattr(platform, "_which", _off_path_which)
    monkeypatch.setattr(platform, "_macos_docker_bins", lambda: (bin_dir,))
    before = os.pathsep.join([str(bin_dir), "/usr/bin"])
    monkeypatch.setenv("PATH", before)

    platform.docker_program()
    assert os.environ["PATH"] == before


def test_a_host_with_no_docker_at_all_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """None, not a hopeful `"docker"` that becomes a WinError 2 two lines later."""
    _unresolved(monkeypatch)
    monkeypatch.setattr(platform.sys, "platform", "linux")
    monkeypatch.setattr(platform, "_which", lambda name, path=None: None)
    assert platform.docker_program() is None


def test_the_answer_is_resolved_once_and_then_costs_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`wait_ready()` issues ~1200 docker commands; resolution must not be in that loop.

    Measured on Windows 11 (2026-08-23): `docker_programs()` costs 7.5ms with
    docker on PATH and 14.7ms without, and the second case logs a line each
    time. Across one 8-minute `wait_ready()` that is ~18s of PATH scanning and
    1200 identical log entries, for an answer that cannot have changed.

    The first resolution walks the PATH twice, not once, and that is left
    alone: `docker_programs()` asks whether the plain name resolves in order to
    decide whether to go hunting, and `docker_program()` asks again to decide
    whether to *use* it. 15ms, once per process, is not worth collapsing two
    readable functions into one.
    """
    _unresolved(monkeypatch)
    monkeypatch.setattr(platform.sys, "platform", "win32")
    lookups: list[str] = []

    def _which(name: str, path: str | None = None) -> str | None:
        lookups.append(name)
        return r"C:\bin\docker.exe"

    monkeypatch.setattr(platform, "_which", _which)
    first = platform.docker_program()
    assert lookups == ["docker", "docker"]  # the two walks of the one resolution
    for _ in range(50):
        assert platform.docker_program() == first
    assert lookups == ["docker", "docker"], "the PATH was walked for an answer already known"


def test_a_miss_is_never_cached_so_an_install_mid_run_is_picked_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scenario that created this defect: Docker arrives WHILE the app is running.

    `ensure_docker()` downloads and silently installs Docker Desktop, and the
    installer writes its `resources\\bin` to the registry PATH — which this
    already-running process will never be handed. If "no docker" were cached,
    the launcher would be pinned to that answer for the rest of its life and a
    first run still could not finish unattended.
    """
    _unresolved(monkeypatch)
    monkeypatch.setattr(platform.sys, "platform", "win32")
    which = _OffPathWhich(installed=False)
    monkeypatch.setattr(platform, "_which", which)
    monkeypatch.setattr(platform, "_registry_search_path", lambda: DOCKER_BIN_DIR)

    assert platform.docker_program() is None
    which.installed = True  # the silent installer has just finished
    assert platform.docker_program() == DOCKER_EXE


@pytest.mark.skipif(sys.platform != "win32", reason="reads the real Windows registry")
def test_the_registry_path_reads_back_expanded_and_usable() -> None:
    """The one thing no fake can check: that `winreg` plumbing works at all.

    Read-only, and only the two PATH values. `%USERPROFILE%`-style entries are
    stored literally (`REG_EXPAND_SZ`), so surviving `%` here would mean every
    such directory silently searched under a name it does not have.
    """
    found = platform._registry_search_path()
    assert found, "neither the machine nor the user PATH could be read"
    assert "%" not in found
    assert any(Path(entry).is_dir() for entry in found.split(";") if entry)


# ------------------------------------------------- starting Docker Desktop
# `Start-Process 'Docker Desktop'` was the whole start step until 2026-08-22.
# It resolves nothing — ShellExecute looks a bare name up on PATH and in the
# App Paths registry, and Docker Desktop registers neither — so it exited 1
# with "The system cannot find the file specified" on every Windows machine,
# including one where the app had just been installed successfully.


def test_windows_starts_the_docker_desktop_that_is_actually_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(platform.sys, "platform", "win32")
    _no_off_path_docker(monkeypatch)
    exe = _default_install(monkeypatch, tmp_path)
    run = _Run()

    platform.ensure_docker(run=run, which=lambda n: "C:/docker.exe", wait_seconds=0.0)

    started = _started(run.calls)
    assert started == [f"Start-Process {platform._ps_quote(exe)}"]
    assert str(exe) in started[0]
    assert "Start-Process 'Docker Desktop'" not in started[0]  # the bare name resolves nowhere


def test_windows_finds_docker_desktop_installed_somewhere_else(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A PC that used the installer's --installation-dir, or a future layout.

    Nothing on disk matches a known layout, so only what Windows itself reports
    can find this one — which is why the probe is asked before the list.
    """
    monkeypatch.setattr(platform.sys, "platform", "win32")
    _no_off_path_docker(monkeypatch)
    _no_default_install(monkeypatch)
    elsewhere = tmp_path / "games" / "DockerDesktop"
    exe = elsewhere / platform.DOCKER_DESKTOP_EXE
    exe.parent.mkdir(parents=True)
    exe.write_text("not really an executable", encoding="utf-8")

    class _WinRun(_Run):
        def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
            if "Docker Inc." in argv[-1]:  # the registry/Start-menu/PATH probe
                self.calls.append(argv)
                # Shaped like the real reply measured on Windows 11 (4.83.0):
                # version strings and an uninstall command line as noise, the
                # install FOLDER (`InstallLocation`) among them.
                out = (
                    "4.83.0\n"
                    f'"{elsewhere}\\Docker Desktop Installer.exe" "uninstall"\n'
                    f"{elsewhere}\n"
                )
                return subprocess.CompletedProcess(argv, 0, out, "")
            return super().__call__(argv)

    run = _WinRun()
    report = platform.ensure_docker(run=run, which=lambda n: "C:/docker.exe", wait_seconds=0.0)

    assert _started(run.calls) == [f"Start-Process {platform._ps_quote(exe)}"]
    assert not any("could not find Docker Desktop" in m for m in report.manual_steps)


def test_windows_says_what_to_do_when_docker_desktop_is_nowhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(platform.sys, "platform", "win32")
    _no_off_path_docker(monkeypatch)
    _no_default_install(monkeypatch)
    run = _Run()  # the probe answers with nothing, like a PC without Docker Desktop

    began = time.monotonic()
    report = platform.ensure_docker(run=run, which=lambda n: "C:/docker.exe", wait_seconds=600.0)
    elapsed = time.monotonic() - began

    assert _started(run.calls) == [], "nothing was found, so nothing may be launched"
    assert report.ok is False and report.docker_ready is False
    assert any("Start menu" in m and "Docker Desktop" in m for m in report.manual_steps)
    assert any(m.startswith("start Docker Desktop: no ") for m in report.skipped)
    assert elapsed < 30.0, "a poll that cannot succeed must not hold the user for 10 minutes"


def test_the_probe_asks_windows_rather_than_guessing_one_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hardcoded layouts are the fallback; these sources are the answer."""
    monkeypatch.setattr(platform.sys, "platform", "win32")
    _no_default_install(monkeypatch)
    run = _Run()
    platform.find_docker_desktop(run)

    script = run.calls[0][-1]
    assert run.calls[0][:3] == ["powershell.exe", "-NoProfile", "-Command"]
    assert r"HKLM:\SOFTWARE\Docker Inc.\Docker" in script  # Docker's own key
    assert r"CurrentVersion\App Paths\Docker Desktop.exe" in script  # what Start-Process reads
    assert "$env:ProgramData\\Microsoft\\Windows\\Start Menu" in script  # the all-users shortcut
    assert "$env:APPDATA\\Microsoft\\Windows\\Start Menu" in script  # the per-user one
    assert "Get-Command 'Docker Desktop.exe'" in script  # PATH
    assert "SilentlyContinue" in script  # a key this PC lacks must not kill the probe
    # Measured on a Windows 11 PC with Docker Desktop 4.83.0 (2026-08-23): a
    # per-user install writes nothing to HKLM, and the only registry value that
    # named it was HKCU's InstallLocation. HKLM alone finds such a PC nothing.
    assert r"HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Docker Desktop" in script


def test_a_known_layout_still_answers_when_the_probe_cannot_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No PowerShell (locked down, or missing) must not mean "not installed".

    The layout used here is the one measured on Windows 11 with Docker Desktop
    4.83.0: `%LOCALAPPDATA%\\Programs\\DockerDesktop`, not Program Files.
    """
    _no_default_install(monkeypatch)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    exe = tmp_path / "Programs" / "DockerDesktop" / platform.DOCKER_DESKTOP_EXE
    exe.parent.mkdir(parents=True)
    exe.write_text("not really an executable", encoding="utf-8")

    def no_powershell(argv: list[str]) -> subprocess.CompletedProcess[str]:
        raise OSError(2, "The system cannot find the file specified")

    assert platform.find_docker_desktop(no_powershell) == exe


def test_starting_docker_desktop_survives_an_apostrophe_in_the_install_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(platform.sys, "platform", "win32")
    _no_off_path_docker(monkeypatch)
    exe = _default_install(monkeypatch, tmp_path / "O'Brien Games")
    run = _Run()
    platform.ensure_docker(run=run, which=lambda n: "C:/docker.exe", wait_seconds=0.0)

    command = _started(run.calls)[0]
    assert "O''Brien Games" in command  # doubled, i.e. it cannot end the string early
    assert command.endswith(f"{platform.DOCKER_DESKTOP_EXE}'")
    assert str(exe) in command.replace("''", "'")


def test_linux_does_not_blame_a_password_for_a_failure_that_was_not_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A skipped step is reported by its real cause, not by the likeliest one.

    Measured in a throwaway `ubuntu:24.04` container on yulon-ubuntu
    (2026-08-24): `systemctl enable --now docker` failed with `sudo: systemctl:
    command not found`, and the report told the user "Some steps needed a
    password; run them in a terminal with sudo: systemctl enable --now docker"
    — advice that fails the same way, for a machine where nothing was wrong
    with sudo at all. Every skip was being attributed to a password because a
    password is what usually causes one.

    `sudo -n` says so itself when that is the cause ("a password is required"),
    so the two are distinguishable and the guess was never needed.
    """

    class _RunWithStderr(_Run):
        def __init__(self, stderr_for: dict[str, str]) -> None:
            super().__init__()
            self.stderr_for = stderr_for

        def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
            self.calls.append(argv)
            if argv[:2] == ["docker", "info"]:
                return subprocess.CompletedProcess(argv, self.docker_rc, "", "")
            said = self.stderr_for.get(" ".join(argv))
            if said is not None:
                return subprocess.CompletedProcess(argv, 1, "", said)
            return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(platform.sys, "platform", "linux")
    monkeypatch.setattr(platform, "is_steamos", lambda: False)
    report = platform.ensure_docker(
        run=_RunWithStderr(
            {"sudo -n systemctl enable --now docker": "sudo: systemctl: command not found"}
        ),
        which=lambda n: "/usr/bin/apt-get" if n == "apt-get" else None,
        user="dad",
        wait_seconds=0.0,
    )

    assert any("systemctl" in s for s in report.skipped)
    assert not [m for m in report.manual_steps if "password" in m], report.manual_steps
    assert any(
        "systemctl enable --now docker" in m for m in report.manual_steps
    ), report.manual_steps


# Every spelling of "put this user in the docker group". The audit that wrote
# "the native engine does not escalate" into the checklist grepped for the
# string `usermod -aG docker`, which platform.py has never contained — it
# builds the same command as a list. So the net is cast over argv ELEMENTS,
# and over the three commands that can do it, not the one that did.
_GROUP_JOINERS = ("usermod", "gpasswd", "adduser")


def _joins_the_docker_group(calls: list[list[str]]) -> list[list[str]]:
    return [
        argv
        for argv in calls
        if "docker" in argv and any(joiner in argv for joiner in _GROUP_JOINERS)
    ]


def _linux(monkeypatch: pytest.MonkeyPatch, steamos: bool = False) -> None:
    monkeypatch.setattr(platform.sys, "platform", "linux")
    monkeypatch.setattr(platform, "is_steamos", lambda: steamos)


def _which(tool: str) -> Callable[[str], str | None]:
    return lambda n: f"/usr/bin/{tool}" if n == tool else None


class _WithGroups(_Run):
    """`_Run`, but `id -nG` answers with a real group list."""

    def __init__(self, groups: str) -> None:
        super().__init__()
        self.groups = groups

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        if argv[:2] == ["docker", "info"]:
            return subprocess.CompletedProcess(argv, self.docker_rc, "", "")
        if argv[0] == "id":
            return subprocess.CompletedProcess(argv, 0, self.groups, "")
        return subprocess.CompletedProcess(argv, 0, "", "")


@pytest.mark.parametrize(
    ("tool", "steamos"),
    [("pacman", True), ("pacman", False), ("apt-get", False), ("dnf", False), ("zypper", False)],
)
def test_linux_never_joins_the_docker_group_without_consent(
    monkeypatch: pytest.MonkeyPatch, tool: str, steamos: bool
) -> None:
    """Roadmap 6.4.3, asserted where it says to assert it: on the emitted argv.

    Adding a user to the `docker` group is a root-equivalent grant — `docker
    run -v /:/mnt --rm -it alpine chroot /mnt sh` edits any file on the host —
    so the Phase 6 preamble makes it a consented step on *every* install path,
    not only in the bash scripts.

    This is the Python half of `test_installer.py`'s script invariant, and it
    exists because that one could not have found this: the audit that wrote
    "the native engine does not escalate" into `pyplan/checklist.md` grepped
    for the string `usermod -aG docker`, which `platform.py` never contains —
    it spells the same command as a list. So the assertion is made against the
    argv the seam actually receives, which no spelling can hide from, over
    every package manager rather than only the one this developer's box has.

    Nothing here is asked, because nothing here can ask: `ensure_docker()` is
    given no consent seam. The codebase's rule for that case is already
    settled in `make_responder()` — with nobody to ask, a privilege change is
    declined, because refusing one is recoverable and visible while granting
    one silently is neither.
    """
    _linux(monkeypatch, steamos=steamos)
    run = _Run()
    report = platform.ensure_docker(run=run, which=_which(tool), user="pk", wait_seconds=0.0)

    assert not _joins_the_docker_group(run.calls), run.calls
    assert report.docker_group == "not-asked"

    # Forbidden outright rather than merely gated, on this path too: membership
    # already is root, so a NOPASSWD rule buys nothing and is pure attack
    # surface. Same for widening the socket, which grants it to everyone.
    text = [" ".join(argv) for argv in run.calls]
    assert not [c for c in text if "sudoers" in c or "NOPASSWD" in c], text
    assert not [c for c in text if "chmod" in c and "docker.sock" in c], text


def test_linux_asks_before_it_escalates_and_joins_only_on_yes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A yes joins the group exactly once, and the question came first.

    The order is the defect this closes, not a detail. `Installer.preflight()`
    calls `ensure_docker()` before the bash script runs, so the old code
    granted the group and the script's own `docker_group_consent()` then found
    the user already a member and never asked. One question, asked before
    anything privileged runs, is the whole shape.
    """
    _linux(monkeypatch)
    asked: list[str] = []

    def say_yes(question: str) -> str:
        asked.append(question)
        return "y"

    run = _Run()
    report = platform.ensure_docker(
        run=run, which=_which("apt-get"), user="pk", wait_seconds=0.0, ask=say_yes
    )

    joins = _joins_the_docker_group(run.calls)
    assert joins == [["sudo", "-n", "usermod", "-aG", "docker", "pk"]]
    assert report.docker_group == "granted"

    # Asked once, before the group was joined, and the question says what it costs.
    assert len(asked) == 1
    assert run.calls.index(joins[0]) > run.calls.index(["id", "-nG", "pk"])
    assert "root" in asked[0] and "pk" in asked[0]
    assert any("Log out and back in" in m for m in report.manual_steps)


@pytest.mark.parametrize("reply", [None, "", "   ", "n", "no", "nope", "yeah", "1"])
def test_linux_treats_anything_but_a_deliberate_yes_as_no(
    monkeypatch: pytest.MonkeyPatch, reply: str | None
) -> None:
    """A dismissed dialog is not consent, and neither is an ambiguous answer.

    The same reading `make_responder()` applies to the installers' version of
    this question. `yeah` and `1` are in the list because a helpful widening of
    `_explicit_yes()` is exactly the mutation this must catch.
    """
    _linux(monkeypatch)
    run = _Run()
    report = platform.ensure_docker(
        run=run, which=_which("apt-get"), user="pk", wait_seconds=0.0, ask=lambda _q: reply
    )

    assert not _joins_the_docker_group(run.calls), run.calls
    assert report.docker_group == "declined"
    assert any("You said no" in m for m in report.manual_steps)
    assert not [m for m in report.manual_steps if "Log out and back in" in m]
    # Declining the GROUP is not declining DOCKER: the engine still installs.
    assert any("apt-get" in step for step in report.done)


def test_linux_does_not_ask_a_user_who_is_already_a_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An existing member is not asked, and `dockerd` is not `docker`."""
    _linux(monkeypatch)

    def never(_question: str) -> str:
        raise AssertionError("an existing member was asked anyway")

    run = _WithGroups("pk sudo docker")
    report = platform.ensure_docker(
        run=run, which=_which("apt-get"), user="pk", wait_seconds=0.0, ask=never
    )
    assert report.docker_group == "already-member"
    assert not _joins_the_docker_group(run.calls), run.calls

    # A neighbouring group name must not read as membership, or the question is
    # skipped on a machine that never granted anything.
    asked: list[str] = []

    def decline(question: str) -> str:
        asked.append(question)
        return "n"

    near = _WithGroups("pk sudo dockerd docker-users")
    platform.ensure_docker(
        run=near, which=_which("apt-get"), user="pk", wait_seconds=0.0, ask=decline
    )
    assert len(asked) == 1


def test_linux_never_opens_a_dialog_for_a_plan_or_a_cancelled_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`dry_run` shows a plan and a cancelled run is over: neither may ask."""
    _linux(monkeypatch)

    def never(_question: str) -> str:
        raise AssertionError("a dry run or a cancelled run asked the user something")

    run = _Run()
    plan = platform.ensure_docker(
        run=run, which=_which("apt-get"), user="pk", dry_run=True, ask=never
    )
    assert run.calls == [["docker", "info"]]  # not even the harmless `id`
    assert plan.docker_group == "not-asked"
    assert any("(asks first)" in s for s in plan.skipped)

    stop = threading.Event()
    stop.set()
    stopped = platform.ensure_docker(
        run=_Run(), which=_which("apt-get"), user="pk", wait_seconds=0.0, cancel=stop, ask=never
    )
    assert stopped.docker_group == "not-asked"


def test_sudo_user_names_the_person_not_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """Under `sudo yulon`, the dialog must not offer to make root a docker user.

    Invisible while the join was silent; making the question visible made the
    wrong name visible too. `os.geteuid` is monkeypatched rather than read,
    because it does not exist on the Windows box this suite also runs on, and a
    test whose answer depends on the host OS is a trap this project has already
    been caught by once.
    """
    _linux(monkeypatch)
    monkeypatch.setattr(platform.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setenv("SUDO_USER", "pk")
    monkeypatch.setenv("USER", "root")
    asked: list[str] = []

    def decline(question: str) -> str:
        asked.append(question)
        return "n"

    platform.ensure_docker(run=_Run(), which=_which("apt-get"), wait_seconds=0.0, ask=decline)
    assert asked and "'pk'" in asked[0] and "'root'" not in asked[0]


def test_sudo_u_someone_else_names_the_account_that_will_run_docker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`sudo -u alice yulon`: the process IS alice, and SUDO_USER says bob.

    The old chain read `SUDO_USER` unconditionally, so it offered bob
    root-equivalent access he never asked for, joined an account that is not
    the one making the docker calls, and left alice unable to use Docker
    anyway — membership is evaluated against the calling process's own
    credentials, so that join was wrong AND useless.

    Trusting `SUDO_USER` only at euid 0 is the whole fix: the environment is
    what an escalation tool rewrites, `geteuid()` is not. Held by four
    reviewers after the author had held it as too narrow — the `doas` half
    needs a tool this audience lacks, this half needs nothing (2026-08-24).
    """
    import sys as _sys
    import types

    _linux(monkeypatch)
    monkeypatch.setattr(platform.os, "geteuid", lambda: 1001, raising=False)
    monkeypatch.setenv("SUDO_USER", "bob")
    monkeypatch.setenv("USER", "bob")

    passwd = types.ModuleType("pwd")
    passwd.getpwuid = lambda uid: types.SimpleNamespace(pw_name="alice")  # type: ignore[attr-defined]
    monkeypatch.setitem(_sys.modules, "pwd", passwd)

    run = _Run()
    asked: list[str] = []

    def grant(question: str) -> str:
        asked.append(question)
        return "y"

    platform.ensure_docker(run=run, which=_which("apt-get"), wait_seconds=0.0, ask=grant)

    assert asked and "'alice'" in asked[0] and "'bob'" not in asked[0]
    assert _joins_the_docker_group(run.calls) == [
        ["sudo", "-n", "usermod", "-aG", "docker", "alice"]
    ]


def test_doas_is_read_where_sudo_user_is(monkeypatch: pytest.MonkeyPatch) -> None:
    """`doas` exports DOAS_USER, so a doas launch lands on root without it.

    Unverified from this side — nobody here has a `doas` box — so it is a claim
    to check rather than a measured fact. It costs one token in a tuple either
    way, which is why it is in rather than deferred.
    """
    _linux(monkeypatch)
    monkeypatch.setattr(platform.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.delenv("SUDO_USER", raising=False)
    monkeypatch.setenv("DOAS_USER", "pk")
    monkeypatch.setenv("USER", "root")
    asked: list[str] = []

    def decline(question: str) -> str:
        asked.append(question)
        return "n"

    platform.ensure_docker(run=_Run(), which=_which("apt-get"), wait_seconds=0.0, ask=decline)
    assert asked and "'pk'" in asked[0]


def test_the_default_runner_reads_the_machine_in_a_language_we_chose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The locale pin had no test, so deleting it left the suite green.

    Every other test here injects its own runner, so the default lambda — the
    only thing that carries `LC_ALL` — was never exercised. Without it, sudo's
    "a password is required" is translated and the password case falls into the
    generic bucket for everyone outside an English locale.
    """
    _linux(monkeypatch)
    seen: list[object] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(kwargs.get("env"))
        return subprocess.CompletedProcess(argv, 1, "", "")

    monkeypatch.setattr(platform.runner, "run", fake_run)
    platform.ensure_docker(which=_which("apt-get"), user="pk", wait_seconds=0.0)

    envs = [e for e in seen if isinstance(e, dict)]
    assert envs, "the default runner passed no environment at all"
    assert envs[0]["LC_ALL"] == "C"
    assert envs[0]["LANGUAGE"] == ""


class _Refuses(_Run):
    """`_Run`, but the commands whose joined argv contains `fails` come back non-zero."""

    def __init__(self, fails: str, stderr: str = "sudo: a password is required") -> None:
        super().__init__()
        self.fails = fails
        self.stderr = stderr

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        if argv[:2] == ["docker", "info"]:
            return subprocess.CompletedProcess(argv, self.docker_rc, "", "")
        if argv[0] == "id":
            return subprocess.CompletedProcess(argv, 0, "pk sudo", "")
        if self.fails in " ".join(argv):
            return subprocess.CompletedProcess(argv, 1, "", self.stderr)
        return subprocess.CompletedProcess(argv, 0, "", "")


def test_a_yes_whose_join_failed_is_not_reported_as_a_join(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Consent is one event; the command that follows it succeeding is another.

    `usermod` runs under `sudo -n` exactly like the package steps and fails for
    the same reasons — no cached ticket by the time it runs, a sudoers rule
    scoped to `apt-get` but not `usermod`, or no `docker` group at all because
    the engine install itself failed. The report used to print "log out and
    back in ... then click Install again" on the strength of the ANSWER, so a
    user whose join had failed was sent round a loop ending in the identical
    failure with nothing explaining it.

    No test could see this because every fake in this file answers 0 to
    everything after `docker info` (review, 2026-08-24).

    This test used to assert `docker_group == "granted"` here, on the reasoning
    that the user did say yes and that is what was recorded. It pinned the
    residual the same review's follow-up found: the manual steps drew the
    distinction, the machine-readable field did not, and a support JSON reading
    `granted` says the user is in the docker group when they are not. The yes
    is still recorded — it is half of the value's name — but the field answers
    "what happened", which is the question its own docstring says it answers.
    """
    _linux(monkeypatch)
    run = _Refuses("usermod")
    report = platform.ensure_docker(
        run=run, which=_which("apt-get"), user="pk", wait_seconds=0.0, ask=lambda _q: "y"
    )

    assert report.docker_group == "join-failed"  # the yes is in the name; the join is not
    assert any("usermod" in s for s in report.skipped)
    # ...but nothing may claim the group was joined.
    assert not [m for m in report.manual_steps if "Log out and back in" in m]
    assert any("did not work" in m for m in report.manual_steps)


def test_declining_does_not_promise_an_engine_that_was_never_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "Docker Engine is installed" was printed whether or not it was.

    On a box where `sudo -n` has no ticket every package step lands in
    `skipped`, and the report contradicted itself inside one tuple: "Some steps
    needed a password" two lines above "Docker Engine is installed". The
    engine's fate is reported by the steps, which is the only place that knows
    it.
    """
    _linux(monkeypatch)
    run = _Refuses("apt-get")
    report = platform.ensure_docker(
        run=run, which=_which("apt-get"), user="pk", wait_seconds=0.0, ask=lambda _q: "n"
    )

    assert report.docker_group == "declined"
    assert any("needed a password" in m for m in report.manual_steps)
    assert not [m for m in report.manual_steps if "Docker Engine is installed" in m]
    # The decline itself is still explained, and still says how to change it.
    assert any("You said no" in m and "usermod -aG docker pk" in m for m in report.manual_steps)
