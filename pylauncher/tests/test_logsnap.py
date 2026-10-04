"""Tests for `yulon.logsnap` — the worldserver log saved before every stop (8.1a).

Doubled at `yulon.runner.run`, the same seam `test_docker.py` and
`test_controller.py` use, so nothing here needs a daemon.

What this module is for: when a server is stopped, removed or uninstalled, the
container's log goes with it, and that log is the only account of why the server
was misbehaving. The snapshot is taken *before* the stop and must never be able
to prevent one.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from yulon import docker, logsnap, runner
from yulon.catalog import composegen

SPEC = docker.ContainerSpec(
    db="ac-database", auth="ac-authserver", world="ac-worldserver", ports=(3724, 8085)
)


def _completed(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class _FakeRunner:
    """Records every argv, answers `compose ps -q` with a container id and `logs` with text."""

    def __init__(self, container_id: str = "abc123", log_text: str = "line\n") -> None:
        self.calls: list[list[str]] = []
        self.cwds: list[Path | None] = []
        self.container_id = container_id
        self.log_text = log_text

    def __call__(
        self, cmd: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        self.cwds.append(cwd)
        if cmd[:3] == ["docker", "compose", "ps"]:
            return _completed(0, self.container_id + "\n")
        if cmd[:2] == ["docker", "logs"]:
            return _completed(0, self.log_text)
        return _completed()


def test_the_snapshot_saves_this_installs_world_log_under_a_name_carrying_the_install_id(
    tmp_path: Path, monkeypatch
) -> None:
    """The file is named for the game AND the install, so two installs cannot collide."""
    fake = _FakeRunner(log_text="Avg Diff: 56. Sessions online: 0.\n")
    monkeypatch.setattr(runner, "run", fake)
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    logs_dir = tmp_path / "logs"

    snap = logsnap.capture(SPEC, server_dir, game="wow-wotlk", logs_dir=logs_dir)

    assert snap.problem == ""
    assert snap.path is not None
    assert snap.path.read_text(encoding="utf-8") == "Avg Diff: 56. Sessions online: 0.\n"
    assert snap.path.name.startswith(f"wow-wotlk-{composegen.install_id(server_dir)}-")
    assert snap.path.suffix == ".log"


def test_the_container_is_resolved_through_this_projects_compose_file_not_by_name(
    tmp_path: Path, monkeypatch
) -> None:
    """By project and by service, because two installs of one game share container names."""
    fake = _FakeRunner(container_id="deadbeef")
    monkeypatch.setattr(runner, "run", fake)
    server_dir = tmp_path / "server"
    server_dir.mkdir()

    logsnap.capture(SPEC, server_dir, game="wow-wotlk", logs_dir=tmp_path / "logs")

    resolve = next(c for c in fake.calls if c[:3] == ["docker", "compose", "ps"])
    assert resolve == ["docker", "compose", "ps", "-a", "-q", "ac-worldserver"]
    assert fake.cwds[fake.calls.index(resolve)] == server_dir
    read = next(c for c in fake.calls if c[:2] == ["docker", "logs"])
    assert read == ["docker", "logs", "--tail", "2000", "deadbeef"]


def test_the_log_is_only_given_the_snapshots_name_once_it_is_written(
    tmp_path: Path, monkeypatch
) -> None:
    """A torn write must not be left wearing a finished snapshot's name."""
    fake = _FakeRunner(log_text="two\nlines\n")
    monkeypatch.setattr(runner, "run", fake)
    seen: list[list[str]] = []
    real_replace = logsnap.os.replace

    def _watch(src, dst):  # noqa: ANN001, ANN202 - a spy on one call
        seen.append([Path(src).name, Path(dst).name])
        return real_replace(src, dst)

    monkeypatch.setattr(logsnap.os, "replace", _watch)
    server_dir = tmp_path / "server"
    server_dir.mkdir()

    snap = logsnap.capture(SPEC, server_dir, game="wow-wotlk", logs_dir=tmp_path / "logs")

    assert snap.path is not None
    assert seen == [[snap.path.name + ".partial", snap.path.name]]
    assert list((tmp_path / "logs").glob("*.partial")) == []


def test_a_log_larger_than_the_byte_cap_keeps_its_end_not_its_beginning(
    tmp_path: Path, monkeypatch
) -> None:
    """The line cap can still be megabytes; what a person reads is the end of it."""
    # The line cap is not a size cap: 2000 lines of a long SQL error is megabytes.
    huge = ("x" * 1499 + "\n") * 2000
    fake = _FakeRunner(log_text=huge + "THE LAST LINE\n")
    monkeypatch.setattr(runner, "run", fake)
    server_dir = tmp_path / "server"
    server_dir.mkdir()

    snap = logsnap.capture(SPEC, server_dir, game="wow-wotlk", logs_dir=tmp_path / "logs")

    assert snap.path is not None
    written = snap.path.read_text(encoding="utf-8")
    assert len(written.encode("utf-8")) <= logsnap.MAX_BYTES
    assert written.endswith("THE LAST LINE\n")


def test_the_prune_never_deletes_the_file_it_just_wrote_even_with_a_clock_that_went_back(
    tmp_path: Path, monkeypatch
) -> None:
    """A resumed VM or an NTP step makes the newest file look oldest by mtime."""
    fake = _FakeRunner()
    monkeypatch.setattr(runner, "run", fake)
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    prefix = f"wow-wotlk-{composegen.install_id(server_dir)}"
    future = 4_000_000_000  # every existing file claims to be newer than the new one
    for i in range(logsnap.KEEP + 5):
        stale = logs_dir / f"{prefix}-2020010{i}T000000Z.log"
        stale.write_text("old\n", encoding="utf-8")
        os.utime(stale, (future + i, future + i))

    snap = logsnap.capture(SPEC, server_dir, game="wow-wotlk", logs_dir=logs_dir)

    assert snap.path is not None
    assert snap.path.exists(), "the prune deleted the snapshot it had just written"
    assert len(list(logs_dir.glob(f"{prefix}-*.log"))) == logsnap.KEEP


def test_the_prune_leaves_another_installs_snapshots_alone(tmp_path: Path, monkeypatch) -> None:
    """Retention is per install, so a busy server cannot evict a quiet one's evidence."""
    fake = _FakeRunner()
    monkeypatch.setattr(runner, "run", fake)
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    theirs = [logs_dir / f"wow-wotlk-otherinst-20200{i}01T000000Z.log" for i in range(9)]
    for path in theirs:
        path.write_text("someone else's server\n", encoding="utf-8")

    for _ in range(logsnap.KEEP + 3):
        logsnap.capture(SPEC, server_dir, game="wow-wotlk", logs_dir=logs_dir)

    assert all(path.exists() for path in theirs)


def test_a_container_the_project_does_not_know_is_reported_and_never_raised(
    tmp_path: Path, monkeypatch
) -> None:
    """A stop must happen whether or not its evidence could be collected."""
    fake = _FakeRunner(container_id="")
    monkeypatch.setattr(runner, "run", fake)
    server_dir = tmp_path / "server"
    server_dir.mkdir()

    snap = logsnap.capture(SPEC, server_dir, game="wow-wotlk", logs_dir=tmp_path / "logs")

    assert snap.path is None
    assert "ac-worldserver" in snap.problem


def test_docker_not_answering_is_not_reported_as_a_container_that_is_not_there(
    tmp_path: Path, monkeypatch
) -> None:
    """T211 5: the live check's Stop said "could not find centurion-worldserver".

    `docker compose ps` had timed out after 30 s while Docker was busy stopping;
    the container was there. The problem says Docker did not answer, with its words.
    """

    def busy(cmd: list[str], cwd: Path | None = None, timeout: float | None = None) -> object:
        if cmd[:3] == ["docker", "compose", "ps"]:
            return _completed(124, "", "timed out after 30.0s")
        return _completed()

    monkeypatch.setattr(runner, "run", busy)
    server_dir = tmp_path / "server"
    server_dir.mkdir()

    snap = logsnap.capture(SPEC, server_dir, game="wow-wotlk", logs_dir=tmp_path / "logs")

    assert snap.path is None
    assert "could not find" not in snap.problem
    assert "did not say which container" in snap.problem
    assert "timed out after 30.0s" in snap.problem


def test_a_logs_directory_that_cannot_be_written_is_reported_and_never_raised(
    tmp_path: Path, monkeypatch
) -> None:
    """The other half of the same rule: the failure is a sentence, not an exception."""
    fake = _FakeRunner()
    monkeypatch.setattr(runner, "run", fake)
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    blocked = tmp_path / "logs"
    blocked.write_text("I am a file, not a directory\n", encoding="utf-8")

    snap = logsnap.capture(SPEC, server_dir, game="wow-wotlk", logs_dir=blocked)

    assert snap.path is None
    assert snap.problem != ""


def test_a_log_that_could_not_be_read_is_reported_rather_than_saved_empty(
    tmp_path: Path, monkeypatch
) -> None:
    """A wedged log driver must not become a saved snapshot of nothing.

    `docker logs` that fails -- or times out, which `_docker()` reports in the
    same non-zero shape -- used to come back as `""`, and `capture()` wrote that
    empty string to disk and answered `Snapshot(path=...)`: a zero-byte file the
    user was told was saved (retrospective audit, 2026-09-08). That is the exact
    failure the box's clause "a snapshot that fails or hangs is reported" exists
    for, and it shipped on four ticked boxes.
    """

    class _LogsFail(_FakeRunner):
        def __call__(self, cmd, cwd=None, timeout=None):
            if cmd[:2] == ["docker", "logs"]:
                return _completed(1, "", "Error response from daemon: wedged")
            return super().__call__(cmd, cwd, timeout)

    monkeypatch.setattr(runner, "run", _LogsFail())
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    logs_dir = tmp_path / "logs"

    snap = logsnap.capture(SPEC, server_dir, game="wow-wotlk", logs_dir=logs_dir)

    assert snap.path is None
    assert "ac-worldserver" in snap.problem and "read" in snap.problem
    assert not logs_dir.exists() or not any(logs_dir.iterdir()), "an empty snapshot was saved"


# -- the recorder the controller is given ----------------------------------
#
# `Controller.pre_stop` is a plain callable, so the controller never learns
# where a snapshot goes. The tab, on the other hand, wants to name the file it
# just wrote — so the callable it is given remembers its own last answer.


def test_the_recorder_captures_when_called_and_remembers_where_it_put_it(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(runner, "run", _FakeRunner(log_text="the last words\n"))
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    recorder = logsnap.Recorder(SPEC, server_dir, game="wow-wotlk", logs_dir=tmp_path / "logs")

    assert recorder.last is None
    recorder()

    assert recorder.last is not None
    assert recorder.last.path is not None
    assert recorder.last.path.read_text(encoding="utf-8") == "the last words\n"


def test_the_recorders_last_answer_is_the_most_recent_one(tmp_path: Path, monkeypatch) -> None:
    """Two stops in a session leave two files, and the tab names the second."""
    monkeypatch.setattr(runner, "run", _FakeRunner())
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    recorder = logsnap.Recorder(SPEC, server_dir, game="wow-wotlk", logs_dir=tmp_path / "logs")

    recorder()
    first = recorder.last
    recorder()

    assert first is not None and recorder.last is not None
    assert recorder.last is not first


def test_a_recorder_whose_capture_fails_remembers_the_problem_and_does_not_raise(
    tmp_path: Path, monkeypatch
) -> None:
    """It is handed to `Controller.pre_stop`, which must never be able to block a stop."""
    monkeypatch.setattr(runner, "run", _FakeRunner(container_id=""))
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    recorder = logsnap.Recorder(SPEC, server_dir, game="wow-wotlk", logs_dir=tmp_path / "logs")

    recorder()

    assert recorder.last is not None
    assert recorder.last.path is None
    assert recorder.last.problem != ""


def test_the_cleanup_after_a_failed_write_cannot_itself_raise(tmp_path: Path, monkeypatch) -> None:
    """The bug CI found on Linux while this machine was green (2026-09-06).

    When the logs directory is a FILE, the partial's parent is that file, and
    Linux answers the tidy-up unlink with `NotADirectoryError` — which is not
    the `FileNotFoundError` that `missing_ok=True` swallows. The exception then
    escaped from inside the handler, out of a function whose whole contract is
    that it does not raise, and into a Stop the user had pressed.

    Pinned here without depending on either platform's errno: the write and the
    unlink are both made to fail, and `capture()` must still answer.
    """
    monkeypatch.setattr(runner, "run", _FakeRunner())

    def refuse(*_args, **_kwargs):
        raise OSError("this filesystem says no")

    monkeypatch.setattr(Path, "write_text", refuse)
    monkeypatch.setattr(Path, "unlink", refuse)
    server_dir = tmp_path / "server"
    server_dir.mkdir()

    snap = logsnap.capture(SPEC, server_dir, game="wow-wotlk", logs_dir=tmp_path / "logs")

    assert snap.path is None
    assert "this filesystem says no" in snap.problem
