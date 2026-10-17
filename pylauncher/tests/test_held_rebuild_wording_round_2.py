"""T768: what a held Rebuild and a Rebuild with no rollback say, round 2 (T743's cold review).

1. A held press removes the auth and world servers' containers and leaves the database's
   (`_remove_the_old_servers()`), which may be running: under T658's kill record the press
   started it to be asked (`_held_by_an_unfinished_import()`). Its opening, its stop line and
   its removed line spoke of "the old servers" as if the database were one of them.
2. The kill-record and WSL Rebuild questions say the generic "the old one is put back
   automatically ... and the server is started on it again", and their held paragraph did
   not say that a held press puts its rollback back STOPPED.
3. "The rollback names are let go" was the press's own jargon, and only negative tests read
   the kept-rollback line.
4. With no build to keep, "if the compile is stopped, the server is left as it is now" ignored
   T225: a stopped compile can still finish in Docker's background and move the tags, and
   with no `-rollback` name nothing compares them at Start.

Each press is the real `rebuild()` through T739's machine or T225's daemon double.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

import pytest

from tests.support_native import ENTRY, Recorder, engine
from tests.test_ended_import_is_not_trusted import _done, _Server
from tests.test_rebuild import _daemon_for, a_finished_install
from tests.test_stop_mid_compile import _seams_of
from tests.test_unfinished_import_never_starts import (
    AUTH,
    CHARACTERS,
    KILLED,
    SERVICE,
    WORLD,
    _crashed_first_import,
    _machine,
    _no_world_started,
    _press,
)
from yulon import docker
from yulon.catalog import native
from yulon.catalog.installer import InstallerError, InstallOptions, rebuild_confirmation

T225 = "may still finish"


def _paragraph(asked: str, part: str) -> str:
    (found,) = [p for p in asked.split("\n\n") if part in p]
    return found


# -- 1. the database's container is not one of the old servers ----------------------------


@pytest.mark.parametrize(
    "case", ["from-empty", "from-empty-build-gone", "kill-record", "kill-record-db-up"]
)
def test_a_held_press_names_the_servers_it_removes_and_leaves_the_database(
    tmp_path: Path, case: str
) -> None:
    """Every line about removing containers names the auth and world servers, and one says
    the database's container is left as it is -- running, under the kill record."""
    if case.startswith("kill-record"):
        server = _Server(
            {AUTH: _done("account"), CHARACTERS: _done("characters"), WORLD: _done("x")}
        )
        rec, server_dir, made, _fake = _machine(tmp_path, server)
        docker.one_shot_ended_marker(server_dir, SERVICE).write_text(KILLED, encoding="utf-8")
        if case == "kill-record-db-up":
            # Running already, but not answering: the start the check asks for does nothing
            # (`docker.start_database()` answers False), and an unreadable record holds.
            rec.db_started = True
            made._probe = lambda: docker.ImportState("unreadable", "it does not answer")
    else:
        server = _Server({})
        rec, server_dir, made, _fake = _machine(tmp_path, server)
        _crashed_first_import(server_dir, server)
    gone = case == "from-empty-build-gone"
    if gone:
        rec.images = False
    rec.ready = False

    said = _press(made, server_dir, missing_images_ok=gone)

    assert said[-1] == native.held_rebuild_ending(ENTRY.name, server_dir), said[-3:]
    _no_world_started(rec)
    spec = ENTRY.container_spec()
    assert f"remove_service:{spec.service_for(spec.db)}" not in rec.calls, rec.calls
    if case.startswith("kill-record"):
        assert "start-db" in rec.calls and rec.db_started, "the ground: the database runs"
    opening = next(line for line in said if line.startswith("You can stop this"))
    stop, removed = said[said.index("--- recreate") + 1 : -1]
    for line in (opening, stop, removed):
        assert "auth and world servers" in line, line
        assert "database's container" in line, line
    assert "before the old servers" not in opening, opening
    # Round 2: under the kill record the press itself started the database to be asked, so
    # "left running or stopped, as it is" read as if it had not been touched.
    started = "started to read how the last import ended, is left running"
    if case == "kill-record":
        assert started in opening and "running or stopped, as it is" not in opening, opening
    else:
        if case != "kill-record-db-up":
            assert "start-db" not in rec.calls, "the ground: from empty, nothing is asked"
        assert started not in opening and "left running or stopped, as it is" in opening, opening


# -- 2. the questions that may hold say what a held rollback does --------------------------


@pytest.mark.parametrize("where", ["kill-record", "wsl"])
def test_a_question_that_may_hold_says_its_rollback_is_put_back_stopped(
    tmp_path: Path, where: str
) -> None:
    """The generic rollback paragraph says the server "is started on it again"; the held
    paragraph after it is what overrides it, so it must say the held press does not."""
    server = _Server({AUTH: _done("account"), CHARACTERS: _done("characters"), WORLD: _done("x")})
    _rec, server_dir, _made, _fake = _machine(tmp_path, server)
    if where == "kill-record":
        docker.one_shot_ended_marker(server_dir, SERVICE).write_text(KILLED, encoding="utf-8")
    asked = rebuild_confirmation(ENTRY, server_dir, kept_build=where != "wsl")

    assert "started on it again" in asked, "the ground: the generic rollback paragraph"
    held = _paragraph(asked, "does less than said above")
    assert "put back and left stopped" in held, held


def test_a_held_press_that_fails_after_the_compile_puts_its_build_back_stopped(
    tmp_path: Path,
) -> None:
    """What the questions' new sentence says, done: the old server's removal is refused
    after the compile, the kept build goes back on its tags, and nothing is started."""
    server = _Server({})
    rec, server_dir, made, _fake = _machine(tmp_path, server)
    _crashed_first_import(server_dir, server)
    rec.ready = False
    spec = ENTRY.container_spec()
    rec.remove_service_refused = {spec.service_for(spec.world)}

    with pytest.raises(native.ServersLeftStopped) as raised:
        _press(made, server_dir)

    assert "build" in rec.calls, rec.calls
    assert [c for c in rec.calls if c.startswith("tag:") and "rollback->" in c], rec.calls
    _no_world_started(rec)
    assert native.UNFINISHED_IMPORT_REFUSAL in str(raised.value), raised.value


# -- 3. the kept rollback's line says what it is for, in plain words -----------------------


@pytest.mark.parametrize("gone", [False, True], ids=["build-there", "build-gone"])
def test_a_held_press_says_what_its_rollback_is_for(tmp_path: Path, gone: bool) -> None:
    server = _Server({})
    rec, server_dir, made, _fake = _machine(tmp_path, server)
    _crashed_first_import(server_dir, server)
    if gone:
        rec.images = False
    rec.ready = False

    said = _press(made, server_dir, missing_images_ok=gone)

    if gone:
        assert native.HELD_NO_ROLLBACK_KEPT in said, said
    else:
        (kept,) = [line for line in said if line.startswith("Kept the build you have now")]
        assert kept.endswith(native.HELD_ROLLBACK_KEPT), kept
    told = "\n".join(said)
    assert "let go" not in told and "rollback names" not in told, told


# -- 4. with no build to keep, a stopped compile may still land (T225) ---------------------


def test_a_stopped_compile_with_no_rollback_says_it_may_still_land(tmp_path: Path) -> None:
    """No `-rollback` name, so no stopped-build record and no check at Start: the press
    must say the stopped compile may still move the tags, before and after."""
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    rec.images = False  # no build to keep, and no container holds one

    stop = threading.Event()

    def build(server_dir: Path, files: object, **_kw: object) -> docker.AttachedRun:
        rec.calls.append("build")
        stop.set()  # Stop pressed while the compiler runs: compose is killed untagged
        return docker.AttachedRun(docker.CANCELLED_RETURNCODE, ("[42/1838] Building CXX",))

    said: list[str] = []
    made = engine(rec, **{**_seams_of(rec, daemon), "build": build})
    with pytest.raises(InstallerError) as raised:
        for line in made.rebuild(
            InstallOptions(server_dir=server_dir), cancel=stop, missing_images_ok=True
        ):
            said.append(line)
    error = raised.value

    assert native.NO_ROLLBACK_KEPT in said, said
    assert not (server_dir / native.STOPPED_BUILD_FILE).exists(), "nothing checks at Start"
    daemon.compiled()  # BuildKit finishes it in the background (T225, measured live)
    assert daemon.names != daemon.containers, "the ground: the tags moved after the press"
    assert T225 in native.NO_ROLLBACK_KEPT
    assert T225 in str(error), error
    told = "\n".join(said) + str(error)
    assert "fails or is stopped" not in told, told


def test_the_questions_no_rollback_paragraph_says_a_stopped_compile_may_land(
    tmp_path: Path,
) -> None:
    server_dir = tmp_path / "wow"
    server_dir.mkdir()
    paragraph = _paragraph(rebuild_confirmation(ENTRY, server_dir), "lost their names")

    assert T225 in paragraph, paragraph
    assert "fails or is stopped" not in paragraph, paragraph


def test_a_held_press_with_no_rollback_says_a_stopped_compile_may_land(tmp_path: Path) -> None:
    server = _Server({})
    rec, server_dir, made, _fake = _machine(tmp_path, server)
    _crashed_first_import(server_dir, server)
    rec.images = False
    rec.ready = False

    said = _press(made, server_dir, missing_images_ok=True)

    (line,) = [line for line in said if "no build to keep as a rollback" in line]
    assert T225 in line, line
    assert "is stopped, the server is left as it is now" not in line, line


# -- round 2 (cold review): the stopped note under the record, and a failure is not a stop ---


def _held_with_no_rollback(tmp_path: Path, exit_code: int) -> tuple[list[str], InstallerError]:
    """A held press with the build gone whose compile ends with `exit_code`."""
    server = _Server({})
    rec, server_dir, made, _fake = _machine(tmp_path, server)
    _crashed_first_import(server_dir, server)
    rec.images = False
    rec.ready = False
    stop = threading.Event()

    def build(server_dir: Path, files: object, **_kw: object) -> docker.AttachedRun:
        rec.calls.append("build")
        if exit_code == docker.CANCELLED_RETURNCODE:
            stop.set()  # Stop pressed while the compiler runs
        return docker.AttachedRun(exit_code, ("[42/1838] Building CXX",))

    made._seams = replace(made._seams, build=build)
    said: list[str] = []
    with pytest.raises(InstallerError) as raised:
        for line in made.rebuild(
            InstallOptions(server_dir=server_dir), cancel=stop, missing_images_ok=True
        ):
            said.append(line)
    assert "build" in rec.calls, rec.calls
    _no_world_started(rec)
    return said, raised.value


def test_a_held_stopped_compile_with_no_rollback_names_repair_not_the_next_start(
    tmp_path: Path,
) -> None:
    """Under the record Start refuses, so "which the next Start then runs" was false, and
    contradicted `HELD_NO_ROLLBACK_KEPT` ("which Repair and Start then use") on one panel."""
    said, error = _held_with_no_rollback(tmp_path, docker.CANCELLED_RETURNCODE)

    assert native.HELD_NO_ROLLBACK_KEPT in said, said
    told = str(error)
    assert native.NO_ROLLBACK_NOT_BUILT in told, told
    assert told.endswith(native.HELD_NO_ROLLBACK_STOPPED_NOTE), told
    assert T225 in told and "Repair and Start" in told, told
    assert "next Start" not in told, told


@pytest.mark.parametrize("held", [False, True], ids=["plain", "held"])
def test_a_failed_compile_with_no_rollback_does_not_say_it_was_stopped(
    tmp_path: Path, held: bool
) -> None:
    """The other side of the stopped note's `if`: a compile that failed (a non-zero exit,
    not `CANCELLED_RETURNCODE`) finished nothing that can land later."""
    if held:
        _said, error = _held_with_no_rollback(tmp_path, 2)
    else:
        rec = Recorder(images=True)
        server_dir = a_finished_install(rec, tmp_path)
        rec.images = False  # no build to keep, and no container holds one
        rec.build_result = docker.AttachedRun(2, ("make: *** [all] Error 2",))
        with pytest.raises(InstallerError) as raised:
            list(engine(rec).rebuild(InstallOptions(server_dir=server_dir), missing_images_ok=True))
        error = raised.value
        assert "build" in rec.calls, rec.calls

    told = str(error)
    assert native.NO_ROLLBACK_NOT_BUILT in told, "the ground: the no-rollback failure branch"
    assert native.NO_ROLLBACK_STOPPED_NOTE not in told, told
    assert native.HELD_NO_ROLLBACK_STOPPED_NOTE not in told, told
    assert "compile was stopped" not in told, told
