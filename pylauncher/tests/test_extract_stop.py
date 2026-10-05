"""T303: a Stop during an extraction starts no further tool and ends the tool's container.

Seen on yulon-win11 2026-10-05 (`.notes/gates/live-308-307-yulon-win11-2026-10-05`,
~13:55Z): Stop was pressed in "Re-extract map data" while the client packs were
being laid into the temporary copy. The packs went on for 37 s, then mapextractor
started in an unnamed `docker run --rm` container writing into the server's
`data/`, the run said "--- cancelled", and the container went on extracting until
it was removed by hand.

The docker CLI here is `support_fake_docker`'s: its container is a file that
outlives the CLI, exactly as Docker Desktop's does, and only `rm -f <name>`
ends it.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests import test_extract
from tests.conftest import HANG_BOUND
from tests.support_fake_docker import calls as fake_calls
from tests.support_fake_docker import containers as fake_containers
from tests.support_fake_docker import end_fake_containers, lay_fake_docker
from yulon import container_end, docker, platform
from yulon.after_stop import TrueAfterStop
from yulon.catalog.families import extract
from yulon.catalog.installer import InstallerError

SPEC = docker.ContainerRun(image="yulon/centurion:native", argv=("/opt/bin/mapextractor",))
"""One extraction tool's container, as `extract.tool_run()` describes it."""

ENDS_WITHIN = 10.0
""""Within seconds": a Stop's whole cost here, the late-create second look included.

The fake container runs for ten minutes on its own (`CONTAINER_LASTS`), so a run that
waited for it instead of ending it fails this by minutes, not by a margin.
"""


@pytest.fixture
def fake_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Path, Path]]:
    """The fake CLI as this machine's docker; every container left is ended after."""
    cli, state = lay_fake_docker(tmp_path)
    monkeypatch.setattr(platform, "docker_program", lambda: str(cli))
    yield cli, state
    end_fake_containers(state)


def _tool_on_a_worker(
    cancel: threading.Event, said: list[str], state: Path
) -> tuple[threading.Thread, list[docker.AttachedRun], str]:
    """`run_container()` on a worker thread, as the extract stage runs it; its container's name."""
    got: list[docker.AttachedRun] = []

    def tool() -> None:
        got.append(docker.run_container(SPEC, sink=said.append, cancel=cancel))

    worker = threading.Thread(target=tool)
    worker.start()
    deadline = time.monotonic() + HANG_BOUND
    while not any(call.startswith("run ") for call in fake_calls(state)):
        assert time.monotonic() < deadline, "the tool's docker CLI never started"
        time.sleep(0.01)
    while not fake_containers(state) and (state / "late-create").exists() is False:
        assert time.monotonic() < deadline, "the tool's container never started"
        time.sleep(0.01)
    (run,) = [call for call in fake_calls(state) if call.startswith("run ")]
    argv = run.split()
    return worker, got, argv[argv.index("--name") + 1]


def test_each_tool_container_is_named_by_yulon() -> None:
    """`--name yulon-extract-<12 hex>`, before the image: a Stop needs a name to end it by."""
    argv = SPEC.to_argv(name="yulon-extract-0123456789ab")
    assert argv[:4] == ["run", "--rm", "--name", "yulon-extract-0123456789ab"]
    assert argv.index("--name") < argv.index(SPEC.image)


def test_a_stop_during_a_tool_ends_its_container_within_seconds(
    fake_docker: tuple[Path, Path],
) -> None:
    """The fake tool goes quiet after its first lines, as `vmap4assembler` does for minutes.

    So the run cannot wait for a next line to notice the Stop: it must end the CLI
    itself, and then the container by its name.
    """
    _cli, state = fake_docker
    cancel = threading.Event()
    said: list[str] = []
    worker, got, name = _tool_on_a_worker(cancel, said, state)
    assert re.fullmatch(r"yulon-extract-[0-9a-f]{12}", name), name
    assert fake_containers(state) == [name]

    stopped = time.monotonic()
    cancel.set()
    worker.join(HANG_BOUND)

    assert not worker.is_alive(), "the stopped tool did not end"
    assert time.monotonic() - stopped < ENDS_WITHIN
    assert [run.returncode for run in got] == [docker.CANCELLED_RETURNCODE]
    assert fake_containers(state) == [], "the tool's container is still running"
    assert f"rm -f {name}" in fake_calls(state)


def test_a_stop_before_a_tool_starts_starts_nothing(fake_docker: tuple[Path, Path]) -> None:
    _cli, state = fake_docker
    cancel = threading.Event()
    cancel.set()

    run = docker.run_container(SPEC, sink=lambda _line: None, cancel=cancel)

    assert run.returncode == docker.CANCELLED_RETURNCODE
    assert fake_calls(state) == [], "a docker command was run after the Stop"


def test_a_tool_container_the_daemon_creates_after_the_stop_is_removed_too(
    fake_docker: tuple[Path, Path],
) -> None:
    """T240's second look: the Stop ended the CLI before the daemon had the name."""
    _cli, state = fake_docker
    (state / "late-create").write_text("", encoding="utf-8")
    cancel = threading.Event()
    worker, got, name = _tool_on_a_worker(cancel, [], state)

    cancel.set()
    worker.join(HANG_BOUND)

    assert not worker.is_alive(), "the stopped tool did not end"
    assert [run.returncode for run in got] == [docker.CANCELLED_RETURNCODE]
    removals = [call for call in fake_calls(state) if call.startswith("rm -f ")]
    assert removals == [f"rm -f {name}", f"rm -f {name}"], removals
    assert fake_containers(state) == [], "the late container is still there"


def test_a_tool_container_that_will_not_go_is_named_in_the_run_log(
    fake_docker: tuple[Path, Path],
) -> None:
    """The run still ends as a Stop; the player is told which container to remove, and how."""
    _cli, state = fake_docker
    (state / "refuse-rm").write_text("", encoding="utf-8")
    cancel = threading.Event()
    said: list[str] = []
    worker, got, name = _tool_on_a_worker(cancel, said, state)

    cancel.set()
    worker.join(HANG_BOUND)

    assert not worker.is_alive(), "the stopped tool did not end"
    assert [run.returncode for run in got] == [docker.CANCELLED_RETURNCODE]
    assert fake_containers(state) == [name], "the ground: the daemon refused the removal"
    assert said[-1] == docker.tool_container_left_line(
        name, "Error response from daemon: the daemon is shutting down"
    ), said[-1]
    assert f"docker rm -f {name}" in said[-1]
    assert got[0].container_left == name, "the caller must know the tool may still write"


def test_an_abandoned_tool_run_ends_its_container(fake_docker: tuple[Path, Path]) -> None:
    """Anything that takes the run away mid-tool (here an interrupt from the sink) ends it too."""
    _cli, state = fake_docker

    def interrupted(_line: str) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        docker.run_container(SPEC, sink=interrupted, cancel=threading.Event())

    (run,) = [call for call in fake_calls(state) if call.startswith("run ")]
    argv = run.split()
    name = argv[argv.index("--name") + 1]
    assert fake_containers(state) == []
    assert f"rm -f {name}" in fake_calls(state)


def test_a_tool_that_finishes_on_its_own_is_left_to_its_rm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No Stop, no `docker rm`: `--rm` removed what it ran."""
    ran: list[list[str]] = []

    def finished(argv: list[str], *_args: object, **_kwargs: object) -> docker.AttachedRun:
        ran.append(argv)
        return docker.AttachedRun(0, ("done",))

    monkeypatch.setattr(docker, "run_attached", finished)
    removed: list[str] = []
    monkeypatch.setattr(
        container_end, "end_container", lambda *args, **kwargs: removed.append("rm") or None
    )

    run = docker.run_container(SPEC, sink=lambda _line: None, cancel=threading.Event())

    assert run == docker.AttachedRun(0, ("done",))
    assert len(ran) == 1 and "--name" in ran[0]
    assert removed == []


# -- extract.run_plan(): no tool after a Stop -------------------------------------------------


def test_a_stop_before_the_first_tool_runs_no_tool(tmp_path: Path) -> None:
    """The live case: Stop landed while the packs were laid, before mapextractor began.

    The seam here is a recorder, so this is the plan's own check and not
    `run_container()`'s (which the tests above pin on their own).
    """
    runner = test_extract.Runner(test_extract.FULL)
    cancel = threading.Event()
    cancel.set()
    said: list[str] = []

    with pytest.raises(InstallerError) as stopped:
        said.extend(test_extract.driven(test_extract.PLAN, runner, tmp_path, cancel=cancel))

    assert runner.names() == [], "a tool was started after the Stop"
    assert not any(": running " in line for line in said), said
    assert str(stopped.value).startswith(
        f"Stop was pressed before {test_extract.AD.name} started, so it was not run."
    )
    assert extract.EXTRACT_CANCEL_NOTE in str(stopped.value)


class LeftRunning(test_extract.Runner):
    """A tool Stop ended whose container Docker would not remove."""

    def __call__(
        self,
        spec: docker.ContainerRun,
        *,
        sink: docker.OutputSink,
        cancel: threading.Event | None,
    ) -> docker.AttachedRun:
        self.specs.append(spec)
        if cancel is not None:
            cancel.set()
        return docker.AttachedRun(
            docker.CANCELLED_RETURNCODE, (), container_left="yulon-extract-0123456789ab"
        )


def test_a_stopped_tool_whose_container_was_not_removed_is_a_refusal_true_after_stop(
    tmp_path: Path,
) -> None:
    """Codex adversarial review: the caller must not treat it as a clean Stop -- a stopped
    Re-extract would put the old map data back under a tool still writing into `data/`."""

    runner = LeftRunning(test_extract.FULL)

    with pytest.raises(extract.ContainerLeftRunning) as left:
        test_extract.run(test_extract.PLAN, runner, tmp_path, cancel=threading.Event())

    assert isinstance(left.value, TrueAfterStop), "said under Stopped, not swallowed by it"
    said = str(left.value)
    assert "yulon-extract-0123456789ab" in said and str(tmp_path / "server" / "data") in said
    assert "docker rm -f yulon-extract-0123456789ab" in said
    assert runner.names() == ["ad"], "nothing after it"


def test_a_stopped_map_generation_whose_container_was_not_removed_says_so_too(
    tmp_path: Path,
) -> None:
    test_extract.run(test_extract.PLAN, test_extract.Runner(test_extract.FULL), tmp_path)

    with pytest.raises(extract.ContainerLeftRunning) as left:
        test_extract.mmaps(
            test_extract.MMAPS, LeftRunning(test_extract.MMAPS_WRITES), tmp_path, cancel=None
        )

    assert str(left.value).startswith("map generation was stopped, but its container")
