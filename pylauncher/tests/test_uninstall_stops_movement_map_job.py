"""T854: Uninstall and Start over end this install's running movement-map container first.

The movement-map step is a `yulon-extract-*` container that can outlive a Stop. Left running,
`compose down --remove-orphans` signalled it and waited `STOP_GRACE_SECONDS` (twice, with its
by-name fallback) with no line, ~10 minutes on yulon-win11. It is this install's when its
`yulon.writes-ids` label names the install's data folder or it carries one of the install's
compose projects (the label comes from the image, so the first is the surer). The fake daemon
below answers `docker ps` by its name filter and format and `docker rm -f` by name, so these
tests also prove another install's tool is never listed or touched.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.test_purge import Recorder, _recorder
from yulon import docker, purge, start_over

PROJECT = "yulon-wow-wotlk-deadbeef"
OTHER_PROJECT = "yulon-wow-wotlk-cafef00d"
MINE = "yulon-extract-aaaaaaaaaaaa"
THEIRS = "yulon-extract-bbbbbbbbbbbb"
LINE = "Stopping the movement-map job that was still running for this server…"
REAL_LISTING = purge.Uninstaller._real_tool_containers
"""The method the conftest's autouse fixture replaces, kept for the wiring tests below."""


class _Daemon:
    """`docker._docker`: running containers with their labels, `ps` by name, `rm -f` by name."""

    def __init__(self, *, rm_fails: bool = False, ps_fails: bool = False) -> None:
        # name -> (compose project label, writes-ids label)
        self.running: dict[str, tuple[str, str]] = {
            MINE: (PROJECT, "mine-data-id"),
            THEIRS: (OTHER_PROJECT, "their-data-id"),
            "wow-worldserver": (PROJECT, ""),
        }
        self.rm_fails = rm_fails
        self.ps_fails = ps_fails
        self.removed: list[str] = []
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(
        self, argv: list[str], *a: object, **k: object
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, k))
        if argv[0] == "ps":
            if self.ps_fails:
                return subprocess.CompletedProcess(argv, 1, "", "no daemon")
            filters = [argv[i + 1] for i, part in enumerate(argv) if part == "--filter"]
            name_part = next(f for f in filters if f.startswith("name="))[len("name=") :]
            assert docker.PROJECT_LABEL in argv[-1] and docker.WRITES_LABEL in argv[-1], argv
            rows = [
                f"{name}\t{project}\t{writes}"
                for name, (project, writes) in self.running.items()
                if name_part in name
            ]
            return subprocess.CompletedProcess(argv, 0, "\n".join(rows), "")
        if argv[:2] == ["rm", "-f"]:
            # T846: every removal of a container Yu'lon made takes its anonymous volumes.
            assert argv[:3] == ["rm", "-f", "-v"], argv
            if self.rm_fails:
                return subprocess.CompletedProcess(argv, 1, "", "Error: daemon refused")
            self.removed.append(argv[3])
            self.running.pop(argv[3], None)
            return subprocess.CompletedProcess(argv, 0, argv[3], "")
        raise AssertionError(f"unexpected docker call {argv}")


def _data_id(server_dir: Path) -> str:
    """Give the install's data folder its folder id, as a tool's creation does, and return it."""
    data = server_dir / "data"
    data.mkdir(exist_ok=True)
    ident = docker.folder_id(data)
    assert ident is not None
    return ident


def _uninstaller(
    tmp_path: Path, daemon: _Daemon, monkeypatch: pytest.MonkeyPatch, **more: Any
) -> tuple[Recorder, purge.Uninstaller]:
    monkeypatch.setattr(docker, "_docker", daemon)
    rec = _recorder(tmp_path)
    ident = _data_id(rec.server_dir)
    daemon.running[MINE] = (PROJECT, ident)
    return rec, rec.uninstaller(
        tool_containers_of=lambda projects: _traced(
            rec,
            "list",
            lambda found: docker.install_tool_containers(
                found, (rec.server_dir / "data", rec.server_dir)
            ),
            projects,
        ),
        end_tool_container=lambda name: _traced(rec, "end", docker.end_tool_container, name),
        **more,
    )


def _traced(rec: Recorder, what: str, fn: Callable[[Any], Any], arg: Any) -> Any:
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
    _, uninstaller = _uninstaller(tmp_path, daemon, monkeypatch)
    del daemon.running[MINE]
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
    assert docker.install_tool_containers((PROJECT,), ()) is None


def test_docker_that_does_not_answer_is_a_warning_in_the_report_and_the_removal_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    daemon = _Daemon(ps_fails=True)
    rec, uninstaller = _uninstaller(tmp_path, daemon, monkeypatch)
    with caplog.at_level("INFO"):
        report = uninstaller.run(keep_characters=False)
    assert purge.MOVEMENT_MAP_JOB_NOT_CHECKED in report.warnings
    assert purge.MOVEMENT_MAP_JOB_NOT_CHECKED == (
        "Could not check for a running movement-map job; if this takes long, stop it in Docker."
    )
    assert any(
        r.levelname == "WARNING" and purge.MOVEMENT_MAP_JOB_NOT_CHECKED in r.getMessage()
        for r in caplog.records
    )
    assert daemon.removed == []
    assert f"remove_folder:{rec.server_dir}" in rec.order


# -- how a container is found: by what it writes, or by its compose project ----------------


def _found(daemon: _Daemon, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str] | None:
    monkeypatch.setattr(docker, "_docker", daemon)
    server = tmp_path / "server"
    server.mkdir()
    ident = _data_id(server)
    daemon.running = {
        name: (project, writes.replace("<mine>", ident))
        for name, (project, writes) in daemon.running.items()
    }
    return docker.install_tool_containers((PROJECT,), (server / "data", server))


def test_a_container_that_only_names_this_installs_data_folder_is_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The detached/tool argv adds no compose label; only the writes-ids label names the install."""
    daemon = _Daemon()
    daemon.running = {MINE: ("", "<mine>"), THEIRS: ("", "someone-elses-id")}
    assert _found(daemon, monkeypatch, tmp_path) == [MINE]


def test_a_container_that_only_carries_this_installs_project_label_is_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fallback: no writes label (a tool made before it), but the image's project label."""
    daemon = _Daemon()
    daemon.running = {MINE: (PROJECT, ""), THEIRS: (OTHER_PROJECT, "")}
    assert _found(daemon, monkeypatch, tmp_path) == [MINE]


def test_a_container_with_neither_label_of_this_install_is_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    daemon = _Daemon()
    daemon.running = {
        THEIRS: (OTHER_PROJECT, "someone-elses-id,another-id"),
        "yulon-extract-cccccccccccc": ("", ""),
        "not-a-tool": (PROJECT, "<mine>"),
    }
    assert _found(daemon, monkeypatch, tmp_path) == []


def test_the_container_a_movement_map_run_creates_is_discoverable_by_its_real_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Build the real `docker create` argv for a run_mmaps-shaped run and read its labels back."""
    server = tmp_path / "server"
    data = server / "data"
    data.mkdir(parents=True)
    other = tmp_path / "other" / "data"
    other.mkdir(parents=True)
    ours = docker.ContainerRun(
        image="yulon.local/ac-wotlk-worldserver:test",
        argv=("mmaps-generator",),
        mounts=(docker.Mount(data, "/work"),),  # writable, as `extract.run_mmaps` mounts it
        workdir="/work",
    ).to_create_argv(name=MINE)
    theirs = docker.ContainerRun(
        image="yulon.local/ac-wotlk-worldserver:test",
        argv=("mmaps-generator",),
        mounts=(docker.Mount(other, "/work"),),
        workdir="/work",
    ).to_create_argv(name=THEIRS)

    def labels(argv: list[str]) -> dict[str, str]:
        pairs = [argv[i + 1] for i, part in enumerate(argv) if part == "--label"]
        return dict(pair.split("=", 1) for pair in pairs)

    assert docker.WRITES_LABEL in labels(ours)
    assert docker.PROJECT_LABEL not in labels(ours)  # nothing but the image can add it
    daemon = _Daemon()
    daemon.running = {
        MINE: ("", labels(ours)[docker.WRITES_LABEL]),
        THEIRS: ("", labels(theirs)[docker.WRITES_LABEL]),
    }
    monkeypatch.setattr(docker, "_docker", daemon)
    assert docker.install_tool_containers((PROJECT,), (data, server)) == [MINE]


def test_listing_the_tools_only_reads_the_folder_ids_and_writes_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(docker, "_docker", _Daemon())
    server = tmp_path / "server"
    (server / "data").mkdir(parents=True)
    docker.install_tool_containers((PROJECT,), (server / "data", server))
    assert not (server / "data" / docker.FOLDER_ID_FILE).exists()
    assert not (server / docker.FOLDER_ID_FILE).exists()


# -- several containers, several projects, the order, and the distro ------------------------


def test_with_several_jobs_a_later_one_that_fails_says_how_many_were_ended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    daemon = _Daemon()
    rec, uninstaller = _uninstaller(tmp_path, daemon, monkeypatch)
    second = "yulon-extract-zzzzzzzzzzzz"
    daemon.running[second] = (PROJECT, "")
    real_rm = daemon.__call__

    def refuse_the_second(
        argv: list[str], *a: object, **k: object
    ) -> subprocess.CompletedProcess[str]:
        if argv[:4] == ["rm", "-f", "-v", second]:
            return subprocess.CompletedProcess(argv, 1, "", "Error: daemon refused")
        return real_rm(argv, *a, **k)

    monkeypatch.setattr(docker, "_docker", refuse_the_second)
    with pytest.raises(purge.PurgeRefusal) as caught:
        uninstaller.run(keep_characters=False)
    message = str(caught.value)
    assert "stopped 1 of 2 movement-map jobs" in message
    assert second in message and "could not be stopped" in message
    assert "so nothing else was removed" in message
    assert daemon.removed == [MINE]
    assert not any(step.startswith(("remove_", "snapshot", "forget")) for step in rec.order)


def test_the_job_is_ended_before_the_backups_are_moved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    daemon = _Daemon()
    rec, uninstaller = _uninstaller(tmp_path, daemon, monkeypatch)
    monkeypatch.setattr(
        purge, "prepare_backups", lambda found, state: rec.order.append("prepare_backups")
    )
    uninstaller.run(keep_characters=False)
    order = rec.order
    assert order.index(f"tool_end:{MINE}") < order.index("prepare_backups"), order


def test_a_job_that_cannot_be_stopped_leaves_the_backups_where_they_were(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    daemon = _Daemon(rm_fails=True)
    rec, uninstaller = _uninstaller(tmp_path, daemon, monkeypatch)
    moved: list[str] = []
    monkeypatch.setattr(purge, "prepare_backups", lambda found, state: moved.append("moved"))
    with pytest.raises(purge.PurgeRefusal):
        uninstaller.run(keep_characters=False)
    assert moved == []


def test_every_project_the_install_may_have_is_asked_not_only_the_resolved_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    daemon = _Daemon()
    asked: list[tuple[str, ...]] = []
    rec, uninstaller = _uninstaller(
        tmp_path, daemon, monkeypatch, projects_of=lambda: (PROJECT, OTHER_PROJECT)
    )

    def listing(projects: tuple[str, ...]) -> list[str] | None:
        asked.append(projects)
        return docker.install_tool_containers(projects, ())

    uninstaller._tool_containers_of = listing
    daemon.running[MINE] = (OTHER_PROJECT, "")  # carries only the planned project's label
    daemon.running[THEIRS] = ("yulon-wow-wotlk-11111111", "their-data-id")
    assert docker.install_tool_containers((PROJECT,), ()) == []  # one project alone misses it
    uninstaller.run(keep_characters=False)
    assert asked == [(PROJECT, OTHER_PROJECT)]
    assert daemon.removed == [MINE]


def test_start_over_hands_its_uninstall_every_project_it_knows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from tests.test_start_over import WOTLK
    from yulon.catalog import composegen

    seen: dict[str, Any] = {}

    class Spy:
        def __init__(self, **kwargs: Any) -> None:
            seen.update(kwargs)

    monkeypatch.setattr(purge, "Uninstaller", Spy)
    monkeypatch.setattr(docker, "install_project", lambda spec, folder, **k: "resolved-project")
    server = tmp_path / "server"
    server.mkdir()
    world = SimpleNamespace(
        wsl_distro=None, spec=WOTLK.container_spec(), mysql=object(), stop_server=None, running=dict
    )
    start_over.app_seams(WOTLK, server, world=world, image_refs=())  # type: ignore[arg-type]
    planned = composegen.project_name(WOTLK.id, server)
    assert seen["projects_of"]() == ("resolved-project", planned)


def test_the_real_wiring_asks_and_ends_in_the_installs_wsl_distro(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    daemon = _Daemon()
    monkeypatch.setattr(docker, "_docker", daemon)
    rec = _recorder(tmp_path)
    uninstaller = rec.uninstaller(wsl_distro="Ubuntu-24.04")
    REAL_LISTING(uninstaller, (PROJECT,))
    assert uninstaller._real_end_tool(MINE) is None
    asks = {argv[0]: kwargs.get("wsl_distro") for argv, kwargs in daemon.calls}
    assert asks == {"ps": "Ubuntu-24.04", "rm": "Ubuntu-24.04"}, daemon.calls
