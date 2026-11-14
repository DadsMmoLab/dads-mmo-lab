"""The compose commands the stack runtime hands Docker (`docker.compose_*` for `SingleStack`).

The argv is the contract: a profile flag in the wrong place pulls nothing for the seed,
and a `--no-deps` on `up` would start the servers before their database.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from yulon import docker


@pytest.fixture
def ran(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], dict[str, Any]]]:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(argv: list[str], cwd: Path | None = None, **kwargs: Any) -> None:
        calls.append((argv, {"cwd": cwd, **kwargs}))

    def fake_attached(argv: list[str], cwd: Path, **kwargs: Any) -> docker.AttachedRun:
        calls.append((argv, {"cwd": cwd, **kwargs}))
        return docker.AttachedRun(0)

    monkeypatch.setattr(docker, "_run", fake_run)
    monkeypatch.setattr(docker, "run_attached", fake_attached)
    return calls


def test_up_all_starts_the_whole_project_in_its_own_order(ran: list[Any], tmp_path: Path) -> None:
    docker.compose_up_all(tmp_path)
    assert ran == [
        (["compose", "up", "-d"], {"cwd": tmp_path, "timeout": None, "wsl_distro": None})
    ]


def test_stop_all_stops_the_project(ran: list[Any], tmp_path: Path) -> None:
    docker.compose_stop_all(tmp_path, timeout=120.0)
    assert ran[0][0] == ["compose", "stop"] and ran[0][1]["timeout"] == 120.0


def test_pull_names_the_seed_profile_before_the_command(ran: list[Any], tmp_path: Path) -> None:
    docker.compose_pull(tmp_path, profiles=("seed",))
    argv, kwargs = ran[0]
    assert argv == ["compose", "--profile", "seed", "--progress", "plain", "pull"]
    assert kwargs["merge_stderr"] is True and kwargs["cwd"] == tmp_path


def test_run_once_removes_its_container_and_starts_nothing_else(
    ran: list[Any], tmp_path: Path
) -> None:
    docker.compose_run_once(tmp_path, "meshes", profiles=("seed",))
    assert ran[0][0] == ["compose", "--profile", "seed", "run", "--rm", "--no-deps", "meshes"]
