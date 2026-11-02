"""T854: Uninstall and Start over end this install's running movement-map container first.

The movement-map step is a `yulon-extract-*` container that can outlive a Stop and carries
the install's compose project. Left running, `compose down --remove-orphans` signalled it and
waited `STOP_GRACE_SECONDS` (twice, with its by-name fallback) with no line, ~10 minutes on
yulon-win11. The fake daemon below answers `docker ps` by its filters and `docker rm -f` by
name, so these tests also prove another install's tool is never listed or touched.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.test_purge import Recorder, _recorder
from yulon import docker, purge

PROJECT = "yulon-wow-wotlk-deadbeef"
MINE = "yulon-extract-aaaaaaaaaaaa"
THEIRS = "yulon-extract-bbbbbbbbbbbb"
LINE = "Stopping the movement-map job that was still running for this server…"


class _Daemon:
    """`docker._docker`: running containers with their compose project, `ps` filters, `rm -f`."""

    def __init__(self, *, rm_fails: bool = False) -> None:
        self.running: dict[str, str] = {
            MINE: PROJECT,
            THEIRS: "yulon-wow-wotlk-cafef00d",
            "wow-worldserver": PROJECT,
        }
        self.rm_fails = rm_fails
        self.removed: list[str] = []

    def __call__(
        self, argv: list[str], *a: object, **k: object
    ) -> subprocess.CompletedProcess[str]:
        if argv[0] == "ps":
            filters = [argv[i + 1] for i, part in enumerate(argv) if part == "--filter"]
            label = next(f for f in filters if f.startswith("label="))[len("label=") :]
            name_part = next(f for f in filters if f.startswith("name="))[len("name=") :]
            names = [
                name
                for name, project in self.running.items()
                if label == f"{docker.PROJECT_LABEL}={project}" and name_part in name
            ]
            return subprocess.CompletedProcess(argv, 0, "\n".join(names), "")
        if argv[:2] == ["rm", "-f"]:
            if self.rm_fails:
                return subprocess.CompletedProcess(argv, 1, "", "Error: daemon refused")
            self.removed.append(argv[2])
            self.running.pop(argv[2], None)
            return subprocess.CompletedProcess(argv, 0, argv[2], "")
        raise AssertionError(f"unexpected docker call {argv}")


def _uninstaller(
    tmp_path: Path, daemon: _Daemon, monkeypatch: pytest.MonkeyPatch
) -> tuple[Recorder, purge.Uninstaller]:
    monkeypatch.setattr(docker, "_docker", daemon)
    rec = _recorder(tmp_path)
    return rec, rec.uninstaller(
        tool_containers_of=lambda project: _traced(
            rec, "list", docker.project_tool_containers, project
        ),
        end_tool_container=lambda name: _traced(rec, "end", docker.end_tool_container, name),
    )


def _traced(rec: Recorder, what: str, fn: Callable[[Any], Any], arg: str) -> Any:
    rec.order.append(f"tool_{what}:{arg}")
    return fn(arg)


def test_a_running_movement_map_job_of_this_install_is_ended_first_and_said(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    daemon = _Daemon()
    rec, uninstaller = _uninstaller(tmp_path, daemon, monkeypatch)
    with caplog.at_level("INFO"):
        uninstaller.run(keep_characters=False)
    assert daemon.removed == [MINE]
    assert LINE in caplog.text
    order = rec.order
    assert order.index(f"tool_end:{MINE}") < order.index("remove_containers"), order
    assert order.index(f"tool_end:{MINE}") < order.index("snapshot"), order
    assert f"remove_folder:{rec.server_dir}" in order


def test_another_installs_movement_map_job_and_the_servers_are_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    daemon = _Daemon()
    _, uninstaller = _uninstaller(tmp_path, daemon, monkeypatch)
    uninstaller.run(keep_characters=False)
    assert THEIRS in daemon.running
    assert "wow-worldserver" in daemon.running
    assert daemon.removed == [MINE]


def test_with_no_running_job_nothing_is_ended_and_no_line_is_said(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    daemon = _Daemon()
    del daemon.running[MINE]
    _, uninstaller = _uninstaller(tmp_path, daemon, monkeypatch)
    with caplog.at_level("INFO"):
        uninstaller.run(keep_characters=False)
    assert daemon.removed == []
    assert LINE not in caplog.text


def test_a_job_that_cannot_be_stopped_is_said_and_nothing_is_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    daemon = _Daemon(rm_fails=True)
    rec, uninstaller = _uninstaller(tmp_path, daemon, monkeypatch)
    with pytest.raises(purge.PurgeRefusal) as caught:
        uninstaller.run(keep_characters=False)
    message = str(caught.value)
    assert MINE in message
    assert "could not be stopped" in message
    assert "nothing was removed" in message
    assert not any(
        step.startswith(("remove_", "snapshot", "forget")) for step in rec.order
    ), rec.order
    assert rec.removed_volumes == [] and rec.removed_images == []


def test_docker_that_does_not_answer_the_question_leaves_it_to_the_removal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    down = subprocess.CompletedProcess([], 1, "", "no daemon")
    monkeypatch.setattr(docker, "_docker", lambda *a, **k: down)
    assert docker.project_tool_containers(PROJECT) is None
