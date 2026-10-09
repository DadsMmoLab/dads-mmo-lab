"""T589: the record of the commits a server's running build was compiled from.

Revive on Centurion (T218) and the movement-map generation stamp (T244) both ask "was the
binary this server runs compiled from code that has the fix?". The checkout cannot answer
that: Update to latest and Return to the tested pin move it BEFORE an hours-long compile, and a
Yu'lon killed in that window (power loss, a forced reboot, an unhandled exception) leaves the old
binary beside a checkout on the new commit. So `.yulon-built-from.json` is written only once a
compile is known to be the build that runs, and a press forgets it before it changes anything.
"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from pathlib import Path

import pytest

from tests.support_native import ENTRY, Recorder, engine, install
from yulon import docker
from yulon.catalog import native
from yulon.catalog.installer import InstallerError, InstallOptions, WorldStoppedAfterReadyError

OLD = "a" * 40
NEW = "b" * 40

ABORTED_AFTER_READY = native.WorldOutput(
    text="ready...\nAvg Diff: 15ms\nWorld server is up and running\n>> ABORTED",
    restarts=0,
    status="exited",
)


def _on_disk(rec: Recorder) -> None:
    """Every clone also writes the commit it landed on into `.git/HEAD`, as a real one does."""

    def write_head(dest: Path) -> None:
        (dest / ".git" / "HEAD").write_text(f"{rec.heads[dest]}\n", encoding="utf-8")

    rec.on_clone = write_head


def _move(server_dir: Path, sha: str) -> None:
    """Every source's checkout on `sha`, as the folder says (`.git/HEAD`)."""
    for source in ENTRY.emulator.sources:
        (server_dir / source.dest / ".git" / "HEAD").write_text(f"{sha}\n", encoding="utf-8")


def _all(sha: str) -> dict[str, str]:
    return {source.repo: sha for source in ENTRY.emulator.sources}


def _installed(tmp_path: Path) -> tuple[Recorder, Path]:
    rec = Recorder()
    _on_disk(rec)
    server_dir = tmp_path / "server"
    install(rec, server_dir)
    return rec, server_dir


def _rebuild(rec: Recorder, server_dir: Path, **overrides: object) -> list[str]:
    return list(engine(rec, **overrides).rebuild(InstallOptions(server_dir=server_dir)))


def _abandoned_at_the_compile(gen: Iterator[str]) -> list[str]:
    """Read a press until its compile starts, then walk away: Yu'lon killed mid-build.

    `close()` is what a reader that went away does to the generator (GeneratorExit), and the
    nearest a test gets to the process dying: whatever the press had not written by then, it
    never writes.
    """
    assert isinstance(gen, Generator)
    said: list[str] = []
    for line in gen:
        said.append(line)
        if line == "--- build":
            break
    else:
        pytest.fail(f"the press never reached its compile: {said}")
    gen.close()
    return said


def test_an_install_records_the_commits_its_compile_was_made_from(tmp_path: Path) -> None:
    _, server_dir = _installed(tmp_path)
    pins = {source.repo: source.rev for source in ENTRY.emulator.sources}
    assert native.read_built_from(server_dir) == pins
    assert (server_dir / native.BUILT_FROM_FILE).is_file()


def test_an_install_that_skips_the_compile_records_nothing(tmp_path: Path) -> None:
    """A resume over a finished build compiles nothing, so it cannot say what was compiled."""
    rec, server_dir = _installed(tmp_path)
    native.forget_built_from(server_dir)
    _move(server_dir, NEW)
    rec.calls.clear()
    install(rec, server_dir)
    assert "build" not in rec.calls, "the resume compiled after all"
    assert native.read_built_from(server_dir) == {}


def test_a_rebuild_records_the_commits_it_compiled(tmp_path: Path) -> None:
    rec, server_dir = _installed(tmp_path)
    _move(server_dir, NEW)
    _rebuild(rec, server_dir)
    assert native.read_built_from(server_dir) == _all(NEW)


def test_a_rebuild_whose_compile_fails_leaves_no_record(tmp_path: Path) -> None:
    """The old build still runs, but nothing says which commit it was: unknown, not a guess."""
    rec, server_dir = _installed(tmp_path)
    assert native.read_built_from(server_dir)
    rec.build_result = docker.AttachedRun(2, ("error: the compiler broke",))
    with pytest.raises(InstallerError):
        _rebuild(rec, server_dir)
    assert native.read_built_from(server_dir) == {}


def test_a_rebuild_put_back_after_its_compile_leaves_no_record(tmp_path: Path) -> None:
    """The new build never came up and the old one is back: the compile is not what runs."""
    rec, server_dir = _installed(tmp_path)
    _move(server_dir, NEW)
    answers = [False, True]

    def wait_ready(spec: object, ready: object) -> bool:
        return answers.pop(0) if len(answers) > 1 else answers[0]

    with pytest.raises(native.RebuildChangedTheServer):
        _rebuild(rec, server_dir, wait_ready=wait_ready)
    assert "build" in rec.calls
    assert native.read_built_from(server_dir) == {}


def test_a_rebuild_abandoned_mid_compile_leaves_no_record(tmp_path: Path) -> None:
    rec, server_dir = _installed(tmp_path)
    assert native.read_built_from(server_dir)
    _abandoned_at_the_compile(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert native.read_built_from(server_dir) == {}


def test_an_update_killed_after_moving_the_checkout_leaves_no_record_of_the_new_commit(
    tmp_path: Path,
) -> None:
    """The ticket's case: the checkout is on the new commit, the binary is the old one."""
    rec, server_dir = _installed(tmp_path)
    for source in ENTRY.emulator.sources:
        rec.upstream[server_dir / source.dest] = NEW
    press = engine(rec).update_to_latest(InstallOptions(server_dir=server_dir))
    _abandoned_at_the_compile(press)
    assert native.read_head_file(server_dir / ENTRY.emulator.sources[0].dest) == NEW
    assert native.read_built_from(server_dir) == {}


def test_an_update_that_lands_records_the_commits_it_moved_to(tmp_path: Path) -> None:
    rec, server_dir = _installed(tmp_path)
    for source in ENTRY.emulator.sources:
        rec.upstream[server_dir / source.dest] = NEW
    list(engine(rec).update_to_latest(InstallOptions(server_dir=server_dir)))
    assert native.read_built_from(server_dir) == _all(NEW)


def test_a_kept_build_records_what_it_was_made_from(tmp_path: Path) -> None:
    """T71: it came up and then stopped on its data, and it is what the tags name."""
    rec, server_dir = _installed(tmp_path)
    _move(server_dir, NEW)
    with pytest.raises(WorldStoppedAfterReadyError):
        _rebuild(rec, server_dir, world_output=lambda spec: ABORTED_AFTER_READY)
    assert native.read_built_from(server_dir) == _all(NEW)


def test_a_checkout_that_cannot_be_read_is_left_out(tmp_path: Path) -> None:
    rec, server_dir = _installed(tmp_path)
    first, *rest = ENTRY.emulator.sources
    (server_dir / first.dest / ".git" / "HEAD").unlink()
    _rebuild(rec, server_dir)
    assert set(native.read_built_from(server_dir)) == {source.repo for source in rest}


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        '{"version": 1, "sources": []}',
        '{"version": 1, "sources": {"a/b": 7, "c/d": ""}}',
    ],
)
def test_a_damaged_record_reads_as_none(tmp_path: Path, text: str) -> None:
    (tmp_path / native.BUILT_FROM_FILE).write_text(text, encoding="utf-8")
    assert native.read_built_from(tmp_path) == {}


def test_the_record_is_outside_the_build_fingerprint() -> None:
    """Writing it after a build must not change the kept-build answer (T224/T230)."""
    assert native.BUILT_FROM_FILE.startswith(".yulon")
