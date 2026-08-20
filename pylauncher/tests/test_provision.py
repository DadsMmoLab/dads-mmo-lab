"""Tests for Docker/WSL2 provisioning (`yulon.platform`, roadmap 5.1) through its seams.

Nothing here installs anything: `run`, `which` and `download` are fakes, and
`detect()` is pinned via `sys.platform`. The assertions are about the plans
(exact commands per OS), the honesty rules (reboot/re-login reported, nothing
silent), and the early exit when a daemon already answers.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from yulon import platform


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


def test_already_running_docker_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _Run(docker_rc=0)
    report = platform.ensure_docker(run=run)
    assert report.docker_ready and report.ok and report.done == ("docker already running",)
    assert run.calls == [["docker", "info"]]


def test_linux_engine_plan_per_package_manager() -> None:
    assert platform.docker_engine_commands("pacman", steamos=True, user="deck") == [
        ["steamos-readonly", "disable"],
        ["pacman", "-Sy", "--noconfirm", "docker", "docker-compose"],
        ["steamos-readonly", "enable"],
        ["systemctl", "enable", "--now", "docker"],
        ["usermod", "-aG", "docker", "deck"],
    ]
    apt = platform.docker_engine_commands("apt", steamos=False, user="pk")
    assert apt[0] == ["apt-get", "update"] and "docker.io" in apt[1]
    assert "docker-buildx" in apt[1]  # compose build needs BuildKit; docker.io lacks it
    assert platform.docker_engine_commands("dnf", steamos=False, user="u")[0][:3] == [
        "dnf",
        "-y",
        "install",
    ]
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
    assert run.calls[1] == ["sudo", "-n", "apt-get", "update"]
    assert any(s.startswith("apt-get update: exit 1") for s in report.skipped)
    assert any("needed a password" in m for m in report.manual_steps)
    assert any("Log out and back in" in m for m in report.manual_steps)
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
    monkeypatch.setattr(platform, "config_dir", lambda: tmp_path)
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
            if "Start-Process 'Docker Desktop'" in " ".join(argv):
                self.docker_after_start = True
            return subprocess.CompletedProcess(argv, 0, "", "")

    run = _WinRun()
    report = platform.ensure_docker(
        run=run, which=lambda n: None, download=download, wait_seconds=1.0
    )
    assert downloads[0][0] == platform.DOCKER_DESKTOP_WINDOWS_URL
    assert downloads[0][1] == tmp_path / "downloads" / "Docker Desktop Installer.exe"
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
    run = _Run()
    report = platform.ensure_docker(run=run, download=lambda u, d: d, dry_run=True)
    assert report.platform == "macos"
    assert report.skipped[0].startswith("(dry run) download https://desktop.docker.com/mac/main/")
    assert any("hdiutil attach" in s for s in report.skipped)
    assert any("cp -R /Volumes/YulonDocker/Docker.app /Applications/" in s for s in report.skipped)
    assert run.calls == [["docker", "info"]]


def test_ensure_wsl2_is_a_noop_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform.sys, "platform", "linux")
    report = platform.ensure_wsl2(run=_Run(docker_rc=0))
    assert report.done == ("WSL2 not needed on this OS",) and report.docker_ready is True
