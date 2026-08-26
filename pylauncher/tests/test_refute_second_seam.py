"""Refutation attempt: do follow_logs/run_attached really miss the distro on the real paths?"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from yulon import docker, platform, runner
from yulon.catalog.catalog import load_catalog
from yulon.ui.controller_view import ControllerServices

WOTLK = load_catalog().get("wow-wotlk")


@pytest.fixture
def wsl_only_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tester's machine: no Docker Desktop; docker lives inside the distro."""
    monkeypatch.setattr(platform, "docker_program", lambda: None)
    monkeypatch.setattr(
        platform,
        "_which",
        lambda name: r"C:\Windows\System32\wsl.exe" if name == platform.WSL_PROGRAM else None,
    )


# ------------------------------------------------------------------ A. the Console tab's log stream


def test_positive_control_the_seam_can_name_the_distro(wsl_only_host: None) -> None:
    """Prove the fixture is not simply breaking everything: _docker() still works."""
    got = platform.docker_prefix("dml-arch", inside=Path("/home/dml/x"))
    print(f"\ndocker_prefix -> {got}")
    assert got[0].endswith("wsl.exe") and got[-1] == "docker" and "dml-arch" in got


def test_the_log_stream_a_wsl_user_gets(wsl_only_host: None, tmp_path: Path) -> None:
    """Real production wiring: for_wotlk(wsl_distro=...) then pull the log source."""
    services = ControllerServices.for_wotlk(WOTLK, tmp_path, None, wsl_distro="dml-arch")
    with pytest.raises(docker.DockerCliMissingError) as exc:
        list(services.logs_source())
    print(f"\nlogs_source() raised: {str(exc.value)[:120]}")


def test_follow_logs_has_no_parameter_for_it() -> None:
    import inspect

    sig = inspect.signature(docker.follow_logs)
    print(f"\nfollow_logs{sig}")
    assert "wsl_distro" not in sig.parameters


# ------------------------------------------------------------------ B. the Repair button


@pytest.fixture
def fake_docker(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """A docker CLI that exists on Windows, recording every argv anyone builds."""
    monkeypatch.setattr(platform, "docker_program", lambda: "docker")
    monkeypatch.setattr(
        platform,
        "_which",
        lambda name: r"C:\Windows\System32\wsl.exe" if name == platform.WSL_PROGRAM else None,
    )
    seen: list[list[str]] = []

    def fake_run(
        command: list[str], cwd: Path | None = None, **kw: object
    ) -> subprocess.CompletedProcess[str]:
        seen.append(command)
        text = " ".join(command)
        out = ""
        if "--format" in command and "{{.Names}}" in text:
            out = ""  # nothing running
        if "inspect" in command and "Health" in text:
            out = "healthy"
        return subprocess.CompletedProcess(command, 0, out, "")

    def fake_stream(command: list[str], cwd: Path | None = None, **kw: object) -> Iterator[str]:
        seen.append(command)
        print(f"\n>>> STREAMED argv={command}\n>>> STREAMED cwd={cwd}")
        yield "import done"

    monkeypatch.setattr(runner, "run", fake_run)
    monkeypatch.setattr(runner, "stream", fake_stream)
    return seen


def test_the_repair_button_on_a_wsl_resident_install(
    fake_docker: list[list[str]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """repair_import(wsl_distro=...) — which daemon does the actual import go to?"""
    server_dir = tmp_path / "wow-server-playerbots"
    server_dir.mkdir()
    (server_dir / ".env").write_text(f"{docker.PROJECT_NAME_VAR}=acore\n", encoding="utf-8")

    from yulon.controller_wow_wotlk import docker_ctl

    spec = docker_ctl.SPEC
    print(f"\nimport_service={spec.import_service!r}")

    states = iter(
        [
            docker.ImportState("absent", "no schemas"),
            docker.ImportState("imported", "all schemas filled", complete=True),
        ]
    )
    docker.repair_import(
        spec,
        server_dir,
        lambda: next(states),
        wsl_distro="dml-arch",
    )

    streamed = [argv for argv in fake_docker if "compose" in argv and "--no-deps" in argv]
    print("\n--- every argv built during the repair ---")
    for argv in fake_docker:
        print(f"  {argv[0]!r} ... {' '.join(argv[1:6])}")
    one_shot = [a for a in streamed if spec.import_service in a]
    assert one_shot, f"the import one-shot never ran; saw {fake_docker}"
    print(f"\nthe import one-shot argv[0] = {one_shot[0][0]!r}")
    assert one_shot[0][0] == "docker", "if this fails the one-shot DID go through wsl"


def test_the_repair_on_the_testers_actual_host(
    wsl_only_host: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No Docker Desktop at all: docker_program() is None, as on the tester's box."""
    from yulon.controller_wow_wotlk import docker_ctl

    server_dir = tmp_path / "wow-server-playerbots"
    server_dir.mkdir()
    (server_dir / ".env").write_text(f"{docker.PROJECT_NAME_VAR}=acore\n", encoding="utf-8")

    def fake_run(command, cwd=None, **kw):
        out = "healthy" if "inspect" in command else ""
        return subprocess.CompletedProcess(command, 0, out, "")

    monkeypatch.setattr(runner, "run", fake_run)
    states = iter(
        [
            docker.ImportState("absent", "no schemas"),
            docker.ImportState("absent", "still no schemas"),
        ]
    )
    with pytest.raises(docker.DockerCommandError) as exc:
        docker.repair_import(
            docker_ctl.SPEC, server_dir, lambda: next(states), wsl_distro="dml-arch"
        )
    print(f"\nRepair button reports:\n  {exc.value}")
