"""T706 review round 2: a Rebuild that imports copies the databases first, and says true things.

The owner's decision (2026-10-11): a Rebuild that runs the importer copies the databases
before it, with the servers stopped, and a rollback puts that copy back before the old
build starts -- the T217 copy the update route already takes. So a failed import, a world
that never reports ready, or a Stop in the import window leaves the old build on the old
schema. The rollback's database sentence names the database updates, never "what the new
build wrote on its first start" when the new build never started.

Also here: the importer's own failure names the file and the module (the pinned updater
re-runs a file whose hash changed, so a broken file fails every press), the update route's
import gets the Modules tab's module list (T707), an update's source move leaves the
sources-off record on any exception (T680 round 2), and every production engine that can
import is built with a module list.
"""

from __future__ import annotations

import ast
import threading
from pathlib import Path

import pytest

from tests.support_native import ENTRY, FakeSnapshot, Recorder, engine, install
from tests.test_rebuild import ABORTED_AFTER_READY, _answers
from yulon import docker
from yulon.catalog import native
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.catalog.installer import (
    InstallerError,
    InstallOptions,
    WorldStoppedAfterReadyError,
)

IMPORTER = f"one-shot:{ENTRY.containers.db_import}"
YULON = Path(__file__).resolve().parents[1] / "yulon"
BROKEN = docker.AttachedRun(
    1,
    (
        "ERROR 1050 (42S01) at line 3: Table 'city_bot_poi' already exists",
        "Applying of file '/azerothcore/modules/mod-city-bots/data/sql/db-world/updates/"
        "2026_07_13_01_city_bot_poi.sql' to database 'acore_world' failed! If you are a "
        "user, please pull the latest revision from the repository.",
    ),
)


def finished(tmp_path: Path) -> tuple[Recorder, Path]:
    rec = Recorder(images=True)
    server_dir = tmp_path / "wow"
    install(rec, server_dir)
    rec.calls.clear()
    return rec, server_dir


def copying(rec: Recorder, **overrides: object) -> tuple[AzerothCoreInstaller, FakeSnapshot]:
    made = engine(rec, **overrides)
    fake = FakeSnapshot(rec)
    made._snapshot = fake
    return made, fake


def at(calls: list[str], prefix: str, start: int = 0) -> int:
    for index in range(start, len(calls)):
        if calls[index].startswith(prefix):
            return index
    raise AssertionError(f"{prefix} not in {calls[start:]}")


# -- item 2: the copy ---------------------------------------------------------


def test_the_copy_is_taken_with_the_servers_down_before_the_importer(tmp_path: Path) -> None:
    rec, server_dir = finished(tmp_path)
    made, fake = copying(rec)

    list(made.rebuild(InstallOptions(server_dir=server_dir)))

    calls = rec.calls
    stop, copy = at(calls, "stop_servers"), at(calls, "snapshot:")
    assert stop < copy < calls.index(IMPORTER) < calls.index("recreate"), calls
    assert fake.taken and set(fake.taken[0].databases) == set(made.snapshot_databases())
    assert not fake.put_back_calls, "a rebuild that came up puts nothing back"
    assert calls.index("recreate") < calls.index("prune"), "older copies go after a success"


def test_a_failed_import_puts_the_copy_back_before_the_old_build_starts(tmp_path: Path) -> None:
    rec, server_dir = finished(tmp_path)
    rec.one_shot_result = BROKEN
    made, fake = copying(rec)

    with pytest.raises(InstallerError) as raised:
        list(made.rebuild(InstallOptions(server_dir=server_dir)))

    calls = rec.calls
    put = at(calls, "put-back:")
    assert calls.index(IMPORTER) < put < calls.index("recreate"), calls
    assert calls.count("recreate") == 1, "only the old build is started"
    said = str(raised.value)
    assert "on its first start" not in said, said
    assert "copy taken before the database updates ran" in said, said


def test_a_world_that_never_comes_up_puts_the_copy_back(tmp_path: Path) -> None:
    rec, server_dir = finished(tmp_path)
    made, fake = copying(rec, wait_ready=_answers(False, True))

    with pytest.raises(InstallerError) as raised:
        list(made.rebuild(InstallOptions(server_dir=server_dir)))

    calls = rec.calls
    first, second = [i for i, c in enumerate(calls) if c == "recreate"]
    assert first < at(calls, "put-back:") < second, calls
    assert "copy taken before the database updates ran" in str(raised.value)


def test_a_stop_in_the_import_window_puts_the_copy_back_and_says_so(tmp_path: Path) -> None:
    rec, server_dir = finished(tmp_path)
    cancel = threading.Event()
    real = rec.seams

    def seams(**overrides: object) -> native.Seams:
        from dataclasses import replace

        made = real(**overrides)
        inner = made.one_shot

        def one_shot(service: str, folder: Path, **kwargs: object) -> docker.AttachedRun:
            cancel.set()
            return inner(service, folder, **kwargs)  # type: ignore[arg-type]

        return replace(made, one_shot=one_shot)

    rec.seams = seams  # type: ignore[method-assign]
    made, fake = copying(rec)

    with pytest.raises(native.StoppedAndPutBack) as raised:
        list(made.rebuild(InstallOptions(server_dir=server_dir), cancel=cancel))

    said = str(raised.value)
    assert said.startswith(
        "The rebuild was stopped once the database updates were in, before the new build "
        "started."
    ), said
    assert "copy taken before the database updates ran" in said, said
    assert rec.calls.count("recreate") == 1, rec.calls


def test_a_kept_build_keeps_the_copy_and_does_not_put_it_back(tmp_path: Path) -> None:
    rec, server_dir = finished(tmp_path)
    made, fake = copying(rec, world_output=lambda spec: ABORTED_AFTER_READY)

    with pytest.raises(WorldStoppedAfterReadyError) as raised:
        list(made.rebuild(InstallOptions(server_dir=server_dir)))

    assert not fake.put_back_calls
    assert native.copy_kept_note(fake.taken[0]) in str(raised.value)


# -- item 1: words without a copy ---------------------------------------------


def test_a_failed_import_with_no_copy_never_says_the_new_build_started(tmp_path: Path) -> None:
    rec, server_dir = finished(tmp_path)
    rec.one_shot_result = BROKEN

    with pytest.raises(InstallerError) as raised:
        list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))

    said = str(raised.value)
    assert "on its first start" not in said, said
    assert native.REBUILD_UPDATES_NOT_PUT_BACK.strip() in said, said


# -- item 3: the importer's failure ---------------------------------------------


def test_an_update_that_fails_is_named_with_its_module_and_the_way_past(tmp_path: Path) -> None:
    rec, server_dir = finished(tmp_path)
    rec.one_shot_result = BROKEN

    with pytest.raises(InstallerError) as raised:
        list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))

    said = str(raised.value)
    assert "2026_07_13_01_city_bot_poi.sql" in said, said
    assert "mod-city-bots" in said, said
    assert "“Apply module SQL”" in said and "remove" in said, said
    assert "Table 'city_bot_poi' already exists" in said, said


# -- T707: the update route's import gets the module list -----------------------


def test_the_update_routes_import_is_given_the_modules_tabs_list(tmp_path: Path) -> None:
    rec = Recorder()
    server_dir = tmp_path / "server"
    install(rec, server_dir)
    for source in ENTRY.emulator.sources:
        rec.heads[server_dir / source.dest] = "a" * 40
        rec.upstream[server_dir / source.dest] = "b" * 40
    rec.calls.clear()
    rec.one_shot_modules.clear()
    made = AzerothCoreInstaller(
        ENTRY,
        installers_root=engine(rec).installers_root,
        import_probe=rec.probe,
        reset_unfinished=rec.reset,
        seams=rec.seams(),
        importer_modules=lambda folder: "mod-a,mod-b",
    )

    list(made.update_to_latest(InstallOptions(server_dir=server_dir)))

    assert rec.calls.count(IMPORTER) == 1, rec.calls
    assert rec.one_shot_modules == ["mod-a,mod-b"], rec.one_shot_modules


# -- T680 round 2: a move that raises anything leaves the record -----------------


def test_a_fetch_that_raises_after_its_head_moved_leaves_the_record(tmp_path: Path) -> None:
    rec = Recorder()
    server_dir = tmp_path / "server"
    install(rec, server_dir)
    core = server_dir / ENTRY.emulator.sources[0].dest
    for source in ENTRY.emulator.sources:
        dest = server_dir / source.dest
        rec.heads[dest] = "a" * 40
        rec.upstream[dest] = "b" * 40
        (dest / ".git").mkdir(parents=True, exist_ok=True)
        (dest / ".git" / "HEAD").write_text("a" * 40 + "\n")
    rec.calls.clear()

    def moved_then_died(dest: Path) -> None:
        (dest / ".git" / "HEAD").write_text(rec.heads[dest] + "\n")
        raise RuntimeError("the process was killed half way through the fetch")

    rec.on_clone = moved_then_died

    with pytest.raises(RuntimeError):
        list(engine(rec).update_to_latest(InstallOptions(server_dir=server_dir)))

    off = native.sources_still_off(server_dir)
    assert [row.dest for row in off] == [core], off


# -- item 5: every production engine that can import has a module list -----------


def plain_callers() -> set[tuple[str, str]]:
    """(file, innermost function) of every production call of the bare `installer_for()`."""

    class Calls(ast.NodeVisitor):
        def __init__(self, file: str) -> None:
            self.file, self.stack, self.found = file, ["<module>"], set[tuple[str, str]]()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

        def visit_Call(self, node: ast.Call) -> None:
            if isinstance(node.func, ast.Name) and node.func.id == "installer_for":
                self.found.add((self.file, self.stack[-1]))
            self.generic_visit(node)

    found: set[tuple[str, str]] = set()
    for path in [*YULON.rglob("*.py"), YULON.parent / "main.py"]:
        calls = Calls(path.name)
        calls.visit(ast.parse(path.read_text(encoding="utf-8")))
        found |= calls.found
    return found


ALLOWED_PLAIN_CALLERS = {
    ("install_wiring.py", "installer_for_app"),  # the one that passes the list
    ("install_wiring.py", "_catalog_pins"),  # the moving sources' pins
    ("install_wiring.py", "ask"),  # the Rebuild refusal asked before its question
    ("install_wiring.py", "confirmation"),  # the update's databases, for its question
    ("install_wiring.py", "news"),  # upstream news
    ("install_wiring.py", "update_to_latest_for_app"),  # the moving sources, for its offer
    ("reset_defaults.py", "_from_templates"),  # a conf reset
}
"""Production calls of the bare `installer_for()` that build an engine with no module list.
None of them rebuilds, updates or imports; a new one must be reviewed and named here: an
engine that imports is built by `installer_for_app()`, which passes the list (T706)."""


def test_only_reviewed_production_callers_build_an_engine_without_a_module_list() -> None:
    assert plain_callers() == ALLOWED_PLAIN_CALLERS
