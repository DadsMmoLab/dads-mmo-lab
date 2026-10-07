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

import os
import re
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from tests import test_extract
from tests.conftest import HANG_BOUND
from tests.support_fake_docker import calls as fake_calls
from tests.support_fake_docker import containers as fake_containers
from tests.support_fake_docker import end_fake_containers, finish_late_create, lay_fake_docker
from yulon import container_end, docker, platform
from yulon.after_stop import TrueAfterStop, stop_took_effect
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
    while not any(call.startswith("start -a ") for call in fake_calls(state)):
        assert time.monotonic() < deadline, "the tool's docker CLI never started"
        time.sleep(0.01)
    name = _created(state)
    while (state / "containers" / name).read_text(encoding="utf-8") == "created":
        assert time.monotonic() < deadline, "the tool's container never started"
        time.sleep(0.01)
    return worker, got, name


def _created(state: Path) -> str:
    """The name the one `docker create` gave its container (T321)."""
    (create,) = [call.split() for call in fake_calls(state) if call.startswith("create ")]
    return create[create.index("--name") + 1]


class _Sleeps:
    """`container_end.time` with its sleeps recorded (and `during` run) instead of slept."""

    def __init__(self, during: Callable[[], None] = lambda: None) -> None:
        self.slept: list[float] = []
        self._during = during

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self._during()

    def __getattr__(self, attr: str) -> object:
        return getattr(time, attr)


def test_each_tool_container_is_named_by_yulon() -> None:
    """`--name yulon-extract-<12 hex>`, before the image: a Stop needs a name to end it by."""
    argv = SPEC.to_argv(name="yulon-extract-0123456789ab")
    assert argv[:4] == ["run", "--rm", "--name", "yulon-extract-0123456789ab"]
    assert argv.index("--name") < argv.index(SPEC.image)
    # T321: what `run_container()` runs is the same container, created and then started.
    # Labelled with whose Yu'lon made it (cold review); SPEC writes nowhere, so no writes label.
    created = SPEC.to_create_argv(name="yulon-extract-0123456789ab")
    owner = ["--label", f"{docker.OWNER_LABEL}={docker.owner_id()}"]
    assert created == ["create", *owner, *argv[1:]]


def test_a_tool_container_is_created_before_it_is_started(
    fake_docker: tuple[Path, Path],
) -> None:
    """T321: `docker create --rm --name`, then `docker start -a`, never one `docker run`."""
    _cli, state = fake_docker
    cancel = threading.Event()
    worker, _got, name = _tool_on_a_worker(cancel, [], state)
    cancel.set()
    worker.join(HANG_BOUND)

    made = [call for call in fake_calls(state) if not call.startswith("rm ")]
    assert made == [
        " ".join(SPEC.to_create_argv(name=name)),
        f"start -a {name}",
    ], made


def test_a_stop_during_a_tool_ends_its_container_within_seconds(
    fake_docker: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fake tool goes quiet after its first lines, as `vmap4assembler` does for minutes.

    So the run cannot wait for a next line to notice the Stop: it must end the CLI
    itself, and then the container by its name.
    """
    _cli, state = fake_docker
    sleeps = _Sleeps()
    monkeypatch.setattr(container_end, "time", sleeps)
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
    assert [call for call in fake_calls(state) if call.startswith("rm ")] == [f"rm -f {name}"]
    # T321: a container that was created before it started needs no second look.
    assert sleeps.slept == [], "a created container was asked about again"


def test_a_stopped_tool_whose_removal_is_already_in_progress_is_not_asked_about_again(
    fake_docker: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """T321: `--rm` got there first ("removal ... already in progress"): the container was
    created before it started, so that "gone" is the end of it, with no settle and no
    second `rm -f`."""
    _cli, state = fake_docker
    (state / "rm-in-progress").write_text("", encoding="utf-8")
    sleeps = _Sleeps()
    monkeypatch.setattr(container_end, "time", sleeps)
    cancel = threading.Event()
    worker, got, name = _tool_on_a_worker(cancel, [], state)

    cancel.set()
    worker.join(HANG_BOUND)

    assert not worker.is_alive(), "the stopped tool did not end"
    assert [run.returncode for run in got] == [docker.CANCELLED_RETURNCODE]
    assert got[0].container_left == ""
    assert [call for call in fake_calls(state) if call.startswith("rm ")] == [f"rm -f {name}"]
    assert sleeps.slept == [], "a created container's removal in progress was asked again"


def test_a_stop_during_the_create_waits_for_it_and_removes_what_it_made(
    fake_docker: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """T321: a Stop while the daemon is still creating cannot lose the container.

    The create is not ended by the Stop, so its answer arrives; the container it made
    is removed, once, and nothing is started.
    """
    _cli, state = fake_docker
    sleeps = _Sleeps()
    monkeypatch.setattr(container_end, "time", sleeps)
    (state / "slow-create").write_text("", encoding="utf-8")
    cancel = threading.Event()
    got: list[docker.AttachedRun] = []
    worker = threading.Thread(
        target=lambda: got.append(docker.run_container(SPEC, sink=lambda _l: None, cancel=cancel))
    )
    worker.start()
    deadline = time.monotonic() + HANG_BOUND
    while not (state / "create-asked").exists():
        assert time.monotonic() < deadline, "the create was never asked"
        time.sleep(0.01)

    cancel.set()
    worker.join(0.5)
    assert worker.is_alive(), "the run did not wait for the create it had asked for"
    assert fake_containers(state) == [], "the ground: the daemon has not created it yet"
    (state / "slow-create").unlink()
    worker.join(HANG_BOUND)

    assert not worker.is_alive(), "the stopped tool did not end"
    name = _created(state)
    assert [run.returncode for run in got] == [docker.CANCELLED_RETURNCODE]
    assert not [call for call in fake_calls(state) if call.startswith("start ")], "started"
    assert [call for call in fake_calls(state) if call.startswith("rm ")] == [f"rm -f {name}"]
    assert fake_containers(state) == [], "the created container is still there"
    assert sleeps.slept == []


def test_a_stop_before_a_tool_starts_starts_nothing(fake_docker: tuple[Path, Path]) -> None:
    _cli, state = fake_docker
    cancel = threading.Event()
    cancel.set()

    run = docker.run_container(SPEC, sink=lambda _line: None, cancel=cancel)

    assert run.returncode == docker.CANCELLED_RETURNCODE
    assert fake_calls(state) == [], "a docker command was run after the Stop"


def test_a_stop_during_a_create_that_then_fails_is_still_the_stop(
    fake_docker: tuple[Path, Path],
) -> None:
    """T321 cold review: the run says what the player did, not what the create answered."""
    _cli, state = fake_docker
    (state / "slow-create").write_text("", encoding="utf-8")
    (state / "create-refused").write_text("", encoding="utf-8")
    cancel = threading.Event()
    got: list[docker.AttachedRun] = []
    worker = threading.Thread(
        target=lambda: got.append(docker.run_container(SPEC, sink=lambda _l: None, cancel=cancel))
    )
    worker.start()
    deadline = time.monotonic() + HANG_BOUND
    while not (state / "create-asked").exists():
        assert time.monotonic() < deadline, "the create was never asked"
        time.sleep(0.01)
    cancel.set()
    (state / "slow-create").unlink()
    worker.join(HANG_BOUND)

    assert not worker.is_alive(), "the stopped tool did not end"
    assert [run.returncode for run in got] == [docker.CANCELLED_RETURNCODE], got
    assert "pull access denied" in got[0].tail[-1], "Docker's words are still kept"


def test_a_create_that_timed_out_is_looked_for_twice_and_its_late_container_removed(
    fake_docker: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """T240's second look, kept by T321 for the one case still open to a late create.

    A create that does not answer in time is ended by its timeout, and the daemon may
    still make the container after the first `rm -f` found nothing. The daemon finishes
    the create during the settle, at the moment the test lays it (T305's way:
    `finish_late_create()`), not on a real second's race.
    """
    _cli, state = fake_docker
    (state / "late-create").write_text("", encoding="utf-8")
    monkeypatch.setattr(container_end, "CREATE_TIMEOUT", 0.5)
    late: list[str] = []

    def the_daemon_finishes_the_create() -> None:
        name = _created(state)
        assert [call for call in fake_calls(state) if call.startswith("rm -f ")] == [
            f"rm -f {name}"
        ], "the ground: the first look came before the create"
        assert fake_containers(state) == []
        late.append(finish_late_create(state))

    sleeps = _Sleeps(during=the_daemon_finishes_the_create)
    monkeypatch.setattr(container_end, "time", sleeps)

    run = docker.run_container(SPEC, sink=lambda _line: None, cancel=threading.Event())

    name = _created(state)
    assert sleeps.slept == [container_end.LATE_CREATE_SETTLE]
    assert late == [name]
    assert run.returncode not in (0, docker.CANCELLED_RETURNCODE), run
    assert any("did not answer" in line for line in run.tail), run.tail
    assert not [call for call in fake_calls(state) if call.startswith("start ")], "started"
    removals = [call for call in fake_calls(state) if call.startswith("rm -f ")]
    assert removals == [f"rm -f {name}", f"rm -f {name}"], removals
    assert fake_containers(state) == [], "the late container is still there"


def test_a_create_docker_refused_is_the_tools_failure_in_dockers_words(
    fake_docker: tuple[Path, Path], tmp_path: Path
) -> None:
    """T321: what `docker run` said for a missing image, `docker create` now says; it is
    returned as the run's failure, and nothing is started, removed or remembered."""
    _cli, state = fake_docker
    (state / "create-refused").write_text("", encoding="utf-8")
    spec = docker.ContainerRun(
        image=SPEC.image, argv=SPEC.argv, mounts=(docker.Mount(tmp_path, "/out"),)
    )
    heard: list[str] = []

    run = docker.run_container(spec, sink=heard.append, cancel=threading.Event())

    assert run.returncode == 125
    assert "pull access denied" in run.tail[-1], run.tail
    assert heard == list(run.tail), "Docker's words reach the log as `docker run`'s did"
    assert [call.split()[0] for call in fake_calls(state)] == ["create"]
    remembered = [writes for writes in docker._UNENDED.values() if tmp_path in writes]
    assert remembered == [], "a container that was never made is remembered"


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
    assert said[-1].endswith(f"\ndocker rm -f {name}"), "the command on a line of its own"
    assert got[0].container_left == name, "the caller must know the tool may still write"


def test_an_abandoned_tool_run_ends_its_container(fake_docker: tuple[Path, Path]) -> None:
    """Anything that takes the run away mid-tool (here an interrupt from the sink) ends it too."""
    _cli, state = fake_docker

    def interrupted(_line: str) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        docker.run_container(SPEC, sink=interrupted, cancel=threading.Event())

    name = _created(state)
    assert fake_containers(state) == []
    assert f"rm -f {name}" in fake_calls(state)


def test_an_abandoned_tool_whose_container_will_not_go_is_known_to_write_into_its_folder(
    fake_docker: tuple[Path, Path], tmp_path: Path
) -> None:
    """Codex adversarial review, round 2: an abandoned run re-raises what abandoned it, so a
    refused removal cannot travel in its result. It is kept where a caller about to write
    into the same folder can ask (`tool_containers_writing_into()`)."""
    _cli, state = fake_docker
    (state / "refuse-rm").write_text("", encoding="utf-8")
    out = tmp_path / "data"
    out.mkdir()
    spec = docker.ContainerRun(
        image=SPEC.image, argv=SPEC.argv, mounts=(docker.Mount(out, "/out"),)
    )

    def interrupted(_line: str) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        docker.run_container(spec, sink=interrupted, cancel=threading.Event())

    (name,) = fake_containers(state)
    assert docker.tool_containers_writing_into(out) == (name,)
    assert docker.tool_containers_writing_into(tmp_path / "elsewhere") == ()


@pytest.mark.parametrize(
    ("docker_says", "still"),
    [("nothing", True), ("it is gone", False)],
    ids=("unanswered-is-kept", "gone-is-dropped"),
)
def test_a_remembered_tool_container_is_asked_about_again_and_kept_unless_docker_says_gone(
    fake_docker: tuple[Path, Path], tmp_path: Path, docker_says: str, still: bool
) -> None:
    """Cold review: Docker is asked again before the memory is trusted; a Docker that does
    not answer keeps the name, which is the safe side."""
    _cli, state = fake_docker
    (state / "refuse-rm").write_text("", encoding="utf-8")
    out = tmp_path / "data"
    out.mkdir()
    spec = docker.ContainerRun(
        image=SPEC.image, argv=SPEC.argv, mounts=(docker.Mount(out, "/out"),)
    )

    def interrupted(_line: str) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        docker.run_container(spec, sink=interrupted, cancel=threading.Event())
    (name,) = fake_containers(state)
    if docker_says == "nothing":
        (state / "no-answer").write_text("", encoding="utf-8")
    else:
        end_fake_containers(state)

    assert docker.tool_containers_writing_into(out) == ((name,) if still else ())
    (state / "no-answer").unlink(missing_ok=True)
    end_fake_containers(state)
    assert docker.tool_containers_writing_into(out) == (), "and dropped once Docker says gone"


def test_a_tool_whose_container_was_removed_is_not_counted_as_writing(
    fake_docker: tuple[Path, Path], tmp_path: Path
) -> None:
    out = tmp_path / "data"
    out.mkdir()
    spec = docker.ContainerRun(
        image=SPEC.image, argv=SPEC.argv, mounts=(docker.Mount(out, "/out"),)
    )

    def interrupted(_line: str) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        docker.run_container(spec, sink=interrupted, cancel=threading.Event())

    assert docker.tool_containers_writing_into(out) == ()


def test_a_tool_that_finishes_on_its_own_is_left_to_its_rm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No Stop, no `docker rm`: `--rm` removed what it ran."""
    ran: list[list[str]] = []

    def finished(argv: list[str], *_args: object, **_kwargs: object) -> docker.AttachedRun:
        ran.append(argv)
        return docker.AttachedRun(0, ("done",))

    monkeypatch.setattr(docker, "run_attached", finished)
    monkeypatch.setattr(
        container_end,
        "create",
        lambda launcher, argv, name, **_kw: (
            subprocess.CompletedProcess(argv, 0, "id\n", ""),
            None,
        ),
    )
    removed: list[str] = []
    monkeypatch.setattr(
        container_end, "end_container", lambda *args, **kwargs: removed.append("rm") or None
    )

    run = docker.run_container(SPEC, sink=lambda _line: None, cancel=threading.Event())

    assert run == docker.AttachedRun(0, ("done",))
    assert len(ran) == 1 and ran[0][:2] == ["start", "-a"], ran
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
    # T250 on Yulon: only a failure marked as the Stop taking effect reads "cancelled".
    assert stop_took_effect(stopped.value), "the Stop before a tool would be shown as a failure"


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
    assert "docker rm" not in said, "a command goes in the log, not on the player's line (T248)"
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


def test_a_tool_container_an_earlier_app_left_running_is_counted_as_writing(
    fake_docker: tuple[Path, Path], tmp_path: Path, real_left_tool_read: None
) -> None:
    """Codex review of the stop-paths branch: what this process remembers dies with it. A
    tool container an earlier Yu'lon left running (a refused removal, then a restart) is
    asked of Docker by its name prefix, and counted as writing: its mounts are not read."""
    _cli, state = fake_docker
    out = tmp_path / "data"
    out.mkdir()
    boxes = state / "containers"
    (boxes / "yulon-extract-0123456789ab").write_text("4242", encoding="utf-8")
    (boxes / "yulon-extract-created00000").write_text("created", encoding="utf-8")
    (boxes / "yulon-git-0123456789ab").write_text("4243", encoding="utf-8")
    # Docker's name filter matches anywhere in the name; only the prefix is ours.
    (boxes / "mine-yulon-extract-copy").write_text("4244", encoding="utf-8")

    assert docker.tool_containers_writing_into(out) == ("yulon-extract-0123456789ab",)

    # One this process started, into another folder, whose removal was refused: known
    # here, so it is counted for its own folder only and not as an earlier run's.
    (state / "refuse-rm").write_text("", encoding="utf-8")
    other = tmp_path / "other-data"
    other.mkdir()
    spec = docker.ContainerRun(
        image=SPEC.image, argv=SPEC.argv, mounts=(docker.Mount(other, "/out"),)
    )

    def interrupted(_line: str) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        docker.run_container(spec, sink=interrupted, cancel=threading.Event())
    (ours,) = set(fake_containers(state)) - {
        "yulon-extract-0123456789ab",
        "yulon-extract-created00000",
        "yulon-git-0123456789ab",
        "mine-yulon-extract-copy",
    }
    assert docker.tool_containers_writing_into(out) == ("yulon-extract-0123456789ab",)
    assert docker.tool_containers_writing_into(other) == (ours, "yulon-extract-0123456789ab")

    (boxes / "yulon-extract-0123456789ab").unlink()
    assert docker.tool_containers_writing_into(out) == (), "gone is gone"


def test_a_docker_that_will_not_list_its_containers_adds_none(
    fake_docker: tuple[Path, Path], tmp_path: Path, real_left_tool_read: None
) -> None:
    """No answer is no name: a daemon that is down runs nothing, and the press says so next."""
    _cli, state = fake_docker
    (state / "containers" / "yulon-extract-0123456789ab").write_text("4242", encoding="utf-8")
    (state / "no-answer").write_text("", encoding="utf-8")

    assert docker.tool_containers_writing_into(tmp_path) == ()


@pytest.mark.parametrize(
    ("platform_name", "desktop"),
    [("win32", True), ("darwin", True), ("linux", False)],
)
def test_a_left_container_is_removed_where_this_platform_removes_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform_name: str, desktop: bool
) -> None:
    """Live on yulon-ubuntu2 (docker.io): the sentences sent a Linux player to Docker
    Desktop's Containers list, which a docker.io engine does not have. The command does."""
    from yulon import git

    monkeypatch.setattr(container_end.sys, "platform", platform_name)
    name = "yulon-extract-0123456789ab"
    lines = [
        docker.tool_container_left_line(name, "refused"),
        git.container_left_line("yulon-git-0123456789ab", tmp_path, "refused"),
    ]
    with pytest.raises(extract.ContainerLeftRunning) as left:
        test_extract.run(
            test_extract.PLAN, LeftRunning(test_extract.FULL), tmp_path, cancel=threading.Event()
        )
    lines.append(str(left.value))

    for line in lines:
        assert ("Docker Desktop's Containers list" in line) is desktop, line
    assert f"docker rm -f {name}" in lines[0]
    assert "docker rm -f yulon-git-0123456789ab" in lines[1]
    assert "docker rm" not in lines[2], "a command goes in the log, not on the player's line"


def test_a_tool_docker_would_not_start_has_its_created_container_removed(
    fake_docker: tuple[Path, Path], tmp_path: Path
) -> None:
    """Codex review round 3: `--rm` acts on a container that ran. One the daemon would not
    start stays created, so the failed run removes it rather than forgetting it."""
    _cli, state = fake_docker
    (state / "start-refused").write_text("", encoding="utf-8")
    out = tmp_path / "data"
    out.mkdir()
    spec = docker.ContainerRun(
        image=SPEC.image, argv=SPEC.argv, mounts=(docker.Mount(out, "/out"),)
    )
    said: list[str] = []

    run = docker.run_container(spec, sink=said.append, cancel=threading.Event())

    name = _created(state)
    assert run.returncode not in (0, docker.CANCELLED_RETURNCODE), run
    assert fake_containers(state) == [], "the created container was left behind"
    assert f"rm -f {name}" in fake_calls(state)
    assert docker.tool_containers_writing_into(out) == ()


def test_a_tool_docker_would_not_start_and_would_not_remove_is_still_known_to_write(
    fake_docker: tuple[Path, Path], tmp_path: Path
) -> None:
    _cli, state = fake_docker
    (state / "start-refused").write_text("", encoding="utf-8")
    (state / "refuse-rm").write_text("", encoding="utf-8")
    out = tmp_path / "data"
    out.mkdir()
    spec = docker.ContainerRun(
        image=SPEC.image, argv=SPEC.argv, mounts=(docker.Mount(out, "/out"),)
    )
    said: list[str] = []

    run = docker.run_container(spec, sink=said.append, cancel=None)

    name = _created(state)
    assert run.container_left == name, run
    assert said[-1].startswith(f"The extraction tool's container {name} could not be removed")
    assert "after Stop" not in said[-1], "nobody pressed Stop (cold review)"
    assert docker.tool_containers_writing_into(out) == (name,)


def test_a_tool_container_another_yulon_made_on_the_same_daemon_counts_only_for_its_folder(
    fake_docker: tuple[Path, Path], tmp_path: Path, real_left_tool_read: None
) -> None:
    """Cold review: a second Linux user, or a Windows and a WSL Yu'lon, share one daemon. A
    running tool labelled by another Yu'lon is that one's live run, not one left by ours, and
    it is left out -- unless it writes into this very folder (Codex review of that fix: two
    Yu'lons may share a server folder), or says nothing of where it writes. An unlabelled one
    (made before the labels) still counts, so one user is no less guarded."""
    _cli, state = fake_docker
    out, other = tmp_path / "data", tmp_path / "other"
    out.mkdir()
    other.mkdir()
    boxes, labels = state / "containers", state / "labels"
    labels.mkdir(exist_ok=True)
    named_here, named_elsewhere = docker.folder_labels(out), docker.folder_labels(other)
    assert named_here is not None and named_elsewhere is not None
    here, elsewhere = ",".join(named_here), ",".join(named_elsewhere)
    for name, given in (
        ("yulon-extract-aaaaaaaaaaaa", ["yulon.owner=someone-else", f"yulon.writes={elsewhere}"]),
        ("yulon-extract-bbbbbbbbbbbb", [f"yulon.owner={docker.owner_id()}"]),
        ("yulon-extract-cccccccccccc", None),
        ("yulon-extract-dddddddddddd", ["yulon.owner=someone-else", f"yulon.writes={here}"]),
        ("yulon-extract-eeeeeeeeeeee", ["yulon.owner=someone-else"]),
    ):
        (boxes / name).write_text("4242", encoding="utf-8")
        if given is not None:
            (labels / name).write_text("\n".join(given), encoding="utf-8")

    assert docker.tool_containers_writing_into(out) == (
        "yulon-extract-bbbbbbbbbbbb",
        "yulon-extract-cccccccccccc",
        "yulon-extract-dddddddddddd",
        "yulon-extract-eeeeeeeeeeee",
    )


def test_a_tool_container_is_created_with_this_yulons_owner_label(
    fake_docker: tuple[Path, Path], tmp_path: Path
) -> None:
    _cli, state = fake_docker
    out = tmp_path / "data"
    out.mkdir()
    spec = docker.ContainerRun(
        image=SPEC.image, argv=SPEC.argv, mounts=(docker.Mount(out, "/out"),)
    )

    def interrupted(_line: str) -> None:
        raise KeyboardInterrupt  # the create is what is read; the fake tool need not run on

    with pytest.raises(KeyboardInterrupt):
        docker.run_container(spec, sink=interrupted, cancel=None)

    (create,) = [call for call in fake_calls(state) if call.startswith("create ")]
    assert f"--label {docker.OWNER_LABEL}={docker.owner_id()}" in create, create
    labelled = docker.folder_labels(out)
    assert labelled is not None
    assert f"--label {docker.WRITES_LABEL}={','.join(labelled)}" in create, create


def test_another_yulon_on_this_machine_has_another_owner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Per config folder: a second user, or a WSL Yu'lon beside a Windows one, has its own."""
    monkeypatch.setattr(platform, "config_dir", lambda: tmp_path / "one")
    one = docker.owner_id()
    monkeypatch.setattr(platform, "config_dir", lambda: tmp_path / "two")
    assert docker.owner_id() != one
    monkeypatch.setattr(platform, "config_dir", lambda: tmp_path / "one")
    assert docker.owner_id() == one


@pytest.mark.parametrize("change", ["ended", "started"])
def test_a_run_of_this_app_changing_while_docker_is_asked_is_not_called_left(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, real_left_tool_read: None, change: str
) -> None:
    """Cold review: what this process knows is read before AND after the `ps`. A run that
    ended (or started) while Docker answered is this app's own, not one left behind."""
    name = "yulon-extract-dddddddddddd"
    monkeypatch.setattr(docker, "_UNENDED", {name: (tmp_path,)} if change == "ended" else {})

    def ps(argv: list[str], *_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        if change == "ended":
            docker._UNENDED.pop(name, None)
        else:
            docker._UNENDED[name] = (tmp_path / "other",)
        return subprocess.CompletedProcess(argv, 0, f"{name}\t{docker.owner_id()}\n", "")

    monkeypatch.setattr(docker, "_docker", ps)

    assert docker.tool_containers_left_running(tmp_path) == ()


# ------------------------------------- T536: one folder, two spellings, one identity


def _another_yulons_tool(state: Path, name: str, writes: str | None) -> None:
    """A running tool container another Yu'lon on this daemon made, labelled `writes`."""
    labels = state / "labels"
    labels.mkdir(exist_ok=True)
    (state / "containers" / name).write_text("4242", encoding="utf-8")
    given = ["yulon.owner=someone-else"]
    if writes is not None:
        given.append(f"{docker.WRITES_LABEL}={writes}")
    (labels / name).write_text("\n".join(given), encoding="utf-8")


def _writes_label(spec: docker.ContainerRun) -> str | None:
    """The `yulon.writes` value `docker create` would carry for `spec`; None without one."""
    argv = spec.to_create_argv(name="yulon-extract-0123456789ab")
    values = [
        argv[i + 1].split("=", 1)[1]
        for i, arg in enumerate(argv)
        if arg == "--label" and argv[i + 1].startswith(f"{docker.WRITES_LABEL}=")
    ]
    return values[0] if values else None


def test_one_server_folder_reached_by_two_spellings_is_one_writer(
    fake_docker: tuple[Path, Path], tmp_path: Path, real_left_tool_read: None
) -> None:
    """T536: a Windows Yu'lon names a folder `C:\\...` and a WSL one `/mnt/c/...`; a symlink
    alias is the same thing on one host. The path hashes differ, so another Yu'lon extracting
    into the folder under the other spelling was left out and both wrote into one `data/`.
    The folder's own identity file names it the same way under every spelling."""
    _cli, state = fake_docker
    real = tmp_path / "srv" / "data"
    real.mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path / "srv", target_is_directory=True)
    other = docker.ContainerRun(
        image=SPEC.image, argv=SPEC.argv, mounts=(docker.Mount(alias / "data", "/out"),)
    )
    writes = _writes_label(other)
    assert writes is not None
    _another_yulons_tool(state, "yulon-extract-aaaaaaaaaaaa", writes)

    assert docker.tool_containers_writing_into(real) == ("yulon-extract-aaaaaaaaaaaa",)


def test_another_yulons_tool_writing_a_different_folder_is_still_left_out(
    fake_docker: tuple[Path, Path], tmp_path: Path, real_left_tool_read: None
) -> None:
    """The identity does not turn every other Yu'lon's run into ours: another folder, another id."""
    _cli, state = fake_docker
    here, there = tmp_path / "a" / "data", tmp_path / "b" / "data"
    here.mkdir(parents=True)
    there.mkdir(parents=True)
    spec = docker.ContainerRun(
        image=SPEC.image, argv=SPEC.argv, mounts=(docker.Mount(there, "/out"),)
    )
    _another_yulons_tool(state, "yulon-extract-aaaaaaaaaaaa", _writes_label(spec))

    assert docker.tool_containers_writing_into(here) == ()


def test_the_folder_id_is_made_once_and_then_read(tmp_path: Path) -> None:
    """Created on first use (an older install has none), and every later ask reads that one."""
    assert not (tmp_path / docker.FOLDER_ID_FILE).exists()
    first = docker.folder_id(tmp_path)
    assert first is not None and re.fullmatch(r"[0-9a-f]{32}", first), first
    assert (tmp_path / docker.FOLDER_ID_FILE).read_text(encoding="utf-8").strip() == first
    if os.name != "nt":  # another user's Yu'lon on the daemon has to read it
        assert (tmp_path / docker.FOLDER_ID_FILE).stat().st_mode & 0o044 == 0o044
    assert docker.folder_id(tmp_path) == first
    assert [p.name for p in tmp_path.iterdir()] == [docker.FOLDER_ID_FILE], "no temp file left"


def test_a_folder_id_made_by_the_other_yulon_meanwhile_is_the_one_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two Yu'lons making the file at once end with one id: the publish never replaces."""
    theirs = "f" * 32
    real_link = docker.os.link

    def raced(src: str, dst: str) -> None:
        (tmp_path / docker.FOLDER_ID_FILE).write_text(theirs + "\n", encoding="utf-8")
        real_link(src, dst)

    monkeypatch.setattr(docker.os, "link", raced)
    assert docker.folder_id(tmp_path) == theirs
    assert [p.name for p in tmp_path.iterdir()] == [docker.FOLDER_ID_FILE], "no temp file left"


@pytest.mark.parametrize("why", ["missing", "garbage", "no-link", "read-only"])
def test_a_folder_without_a_readable_id_falls_back_to_the_safe_side(
    fake_docker: tuple[Path, Path],
    tmp_path: Path,
    real_left_tool_read: None,
    monkeypatch: pytest.MonkeyPatch,
    why: str,
) -> None:
    """No id to read or make: the writer says nothing of where it writes (every guard counts
    it), and the guard leaves out no other Yu'lon's run, whatever its label names."""
    _cli, state = fake_docker
    folder = tmp_path / "data"
    if why != "missing":
        folder.mkdir()
    if why == "garbage":
        (folder / docker.FOLDER_ID_FILE).write_text("not an id\n", encoding="utf-8")
    if why == "no-link":

        def refused(*_a: object) -> None:
            raise PermissionError(1, "Operation not permitted")  # vfat

        monkeypatch.setattr(docker.os, "link", refused)
    if why == "read-only":
        monkeypatch.setattr(docker.tempfile, "mkstemp", _read_only_mkstemp)
    assert docker.folder_id(folder) is None
    spec = docker.ContainerRun(
        image=SPEC.image, argv=SPEC.argv, mounts=(docker.Mount(folder, "/out"),)
    )
    assert _writes_label(spec) is None
    _another_yulons_tool(state, "yulon-extract-aaaaaaaaaaaa", "0" * 32)

    assert docker.tool_containers_writing_into(folder) == ("yulon-extract-aaaaaaaaaaaa",)


def _read_only_mkstemp(*_a: object, **_k: object) -> tuple[int, str]:
    raise PermissionError(13, "Permission denied")


def test_an_older_yulons_path_label_still_counts_and_ours_still_carries_one(
    fake_docker: tuple[Path, Path], tmp_path: Path, real_left_tool_read: None
) -> None:
    """Mixed versions on one daemon: an older Yu'lon labels `writes` with the path hash only.
    The guard still matches it by the same spelling, and the label written here keeps the path
    hash beside the id, so an older Yu'lon's guard still matches ours."""
    _cli, state = fake_docker
    folder = tmp_path / "data"
    folder.mkdir()
    _another_yulons_tool(state, "yulon-extract-aaaaaaaaaaaa", docker.folder_label(folder))

    assert docker.tool_containers_writing_into(folder) == ("yulon-extract-aaaaaaaaaaaa",)
    spec = docker.ContainerRun(
        image=SPEC.image, argv=SPEC.argv, mounts=(docker.Mount(folder, "/out"),)
    )
    writes = _writes_label(spec)
    assert writes is not None
    assert docker.folder_label(folder) in writes.split(","), writes
    assert docker.folder_id(folder) in writes.split(","), writes


def test_a_tool_writing_two_folders_names_neither_when_one_has_no_id(tmp_path: Path) -> None:
    """A label naming only the folder with an id would say the tool writes there alone, and
    a guard on the other folder would leave it out. So it names none: every guard counts it."""
    named, missing = tmp_path / "data", tmp_path / "gone"
    named.mkdir()
    spec = docker.ContainerRun(
        image=SPEC.image,
        argv=SPEC.argv,
        mounts=(docker.Mount(named, "/out"), docker.Mount(missing, "/more")),
    )
    assert docker.folder_id(named) is not None
    assert _writes_label(spec) is None
