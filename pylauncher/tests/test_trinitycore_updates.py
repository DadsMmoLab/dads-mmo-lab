"""T179 Task 6: "Update the server to latest…" and "Return to the tested pin…" on TrinityCore.

The owner's decision 2 (T179 spec §3): the code is pulled and rebuilt as on every
family; of the snapshot under `centurion/sql`, only the world's per-table files that
changed are imported again -- after the backup the route offers, as root, with the
database-name renames -- and a change to the characters' layout is refused with a
message naming the file and nothing changed. A change to the server's DBC files or
to a required client pack means the map data must be extracted again: a flag and a
sentence for the Server tab, and `reextract()`, which runs the client-data stage
again through the temporary extraction client and clears the movement maps.

Git is the Recorder's (`diffs`, `diff_lines`), the database `FakeMysql`, the world
server's container a `World` that goes down on a stop and up on a start, and the
movement-map job `FakeMmapsDocker` -- so each test drives the real
`update_to_latest()` and `rebuild()` and reads what landed where.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import BinaryIO, cast

import pytest

from tests.support_trinitycore import (
    AUTH,
    CHARS,
    CHECKOUT,
    SQL_DIR,
    TRINITYCORE,
    WORLD,
    FakeMmapsDocker,
    centurion_like,
)
from tests.test_families_trinitycore import (  # noqa: F401 - fixtures, as pytest resolves them
    ENTRY,
    REV,
    TC,
    Machine,
    context,
    engine,
    install,
    known_password,
    machine,
)
from yulon import docker
from yulon.catalog import native
from yulon.catalog.catalog import load_catalog
from yulon.catalog.families import extract, mmaps, trinitycore
from yulon.catalog.families.trinitycore import needs_reextract
from yulon.catalog.installer import (
    InstallerError,
    InstallOptions,
    RollbackNotDone,
    WorldStoppedAfterReadyError,
)
from yulon.controller import StartRefused
from yulon.controller_wow_centurion.controller import CenturionController
from yulon.install_wiring import (
    reextract_for_app,
    update_to_latest_for_app,
    world_reimport_for_app,
)

OLD = "a" * 40
NEW = "b" * 40
WORLD_SQL = f"{SQL_DIR}/world"
"""The world tables' folder, server-dir-relative, as the plan's globs spell it."""
REPO_SQL = "centurion/sql"
"""The same folder as git names it: relative to the checkout."""
MIN_FILES = 500
TOOL_NAMES = [tool["name"] for tool in TRINITYCORE["extract"]["tools"]]
"""The test entry's extraction tools, in plan order."""


@dataclass
class World:
    """The world server's container: up after the rebuild, down after a stop, up after a start.

    `stop_servers` / `recreate` are the rebuild's two halves (fix round 1: the update
    route imports between them); `stop` / `start` the retry press's.
    """

    calls: list[str]
    running: bool | None = True
    refuse_stop: str = ""
    stays_up: bool = False
    """A world that is up again by the time it is read after its stop."""
    abandon: bool = False
    """The servers' stop is given up during the load wait, before anything was sent."""
    fail_first_stop: bool = False
    """The rebuild's own stop signals, then fails; the rollback's stop then works."""
    explode: bool = False
    """The servers' stop raises something that is not an `InstallerError` (a bug)."""
    stops: int = 0

    def ask(self, container: str) -> bool | None:
        return self.running

    def stop(self, containers: Sequence[str], **_kwargs: object) -> None:
        if self.refuse_stop:
            raise docker.DockerCommandError(self.refuse_stop)
        self.calls.append(f"stop-world:{','.join(containers)}")
        self.running = self.stays_up

    def start(self, spec: docker.ContainerSpec, server_dir: Path) -> bool:
        self.calls.append("start")
        self.running = True
        return True

    def stop_servers(
        self,
        spec: docker.ContainerSpec,
        server_dir: Path,
        control: docker.StopControl | None = None,
        before_signal: Callable[[], None] | None = None,
    ) -> None:
        self.stops += 1
        if self.explode:
            raise RuntimeError("a bug in the stop")
        if self.abandon:
            raise docker.StopAbandoned("the world was still loading")
        if before_signal is not None:
            before_signal()
        if self.fail_first_stop and self.stops == 1:
            raise docker.DockerCommandError("first stop timed out")
        if self.refuse_stop:
            raise docker.DockerCommandError(self.refuse_stop)
        self.calls.append("stop_servers")
        self.running = self.stays_up

    def recreate(
        self,
        spec: docker.ContainerSpec,
        server_dir: Path,
        control: docker.StopControl | None = None,
        before_signal: Callable[[], None] | None = None,
    ) -> bool:
        if before_signal is not None:
            before_signal()
        self.calls.append("recreate")
        self.running = True
        return True


@dataclass
class Box:
    """An installed Centurion server whose checkout is on `OLD`, with `NEW` upstream."""

    m: Machine
    world: World
    sql: list[str] = field(default_factory=list)
    ready: list[bool] = field(default_factory=lambda: [True])
    """What each ready wait answers, in order, then the last for ever."""
    old_files: dict[str, str] = field(default_factory=dict)
    """World files (by name) as the OLD commit has them: written when a source is put back."""
    world_output: native.WorldOutput | None = None
    """What the world printed, when a test needs it to have stopped after its banner."""
    distro: str | None = None
    seams: dict[str, object] = field(default_factory=dict)
    """Further seam overrides (T197: `tag_image`, `docker_ready`), by name."""

    @property
    def server_dir(self) -> Path:
        return self.m.server_dir

    @property
    def checkout(self) -> Path:
        return self.m.server_dir / CHECKOUT

    def changes(self, *pairs: tuple[str, str], old: str = OLD, new: str = NEW) -> None:
        self.m.rec.diffs[(self.checkout, old, new)] = tuple(pairs)

    def engine(self) -> trinitycore.TrinityCoreInstaller:
        def exec_stdin(
            container: str,
            argv: Sequence[str],
            source: BinaryIO,
            *,
            env: Mapping[str, str],
            wsl_distro: str | None = None,
        ) -> object:
            self.m.rec.calls.append("sql")
            return self.m.db.exec_stdin(container, argv, source, env=env, wsl_distro=wsl_distro)

        def wait_ready(spec: object, ready: object) -> bool:
            self.m.rec.calls.append("ready")
            return self.ready.pop(0) if len(self.ready) > 1 else self.ready[0]

        def restore_rev(dest: Path, rev: str) -> None:
            self.m.rec.restore_rev(dest, rev)
            for name, text in self.old_files.items():
                (self.checkout / REPO_SQL / "world" / name).write_text(text, encoding="utf-8")

        return engine(
            self.m,
            world_running=self.world.ask,
            stop_world=self.world.stop,
            start=self.world.start,
            stop_servers=self.world.stop_servers,
            recreate=self.world.recreate,
            wait_ready=wait_ready,
            restore_rev=restore_rev,
            exec_stdin=exec_stdin,
            distro=self.distro,
            **({"world_output": lambda spec: self.world_output} if self.world_output else {}),
            **self.seams,
        )

    def moves_to(self, files: Mapping[str, str]) -> None:
        """World files (by name) as the NEW commit has them: written when the move lands."""
        lay = self.m.rec.on_clone

        def move(dest: Path) -> None:
            if lay is not None:
                lay(dest)
            for name, text in files.items():
                (dest / REPO_SQL / "world" / name).write_text(text, encoding="utf-8")

        self.m.rec.on_clone = move

    def pending(self) -> dict[str, object] | None:
        path = self.server_dir / trinitycore.WORLD_REIMPORT_FILE
        if not path.exists():
            return None
        return cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))

    def leave_pending(self, reimport: Sequence[str] = (), parts: Sequence[str] = ()) -> None:
        (self.server_dir / trinitycore.WORLD_REIMPORT_FILE).write_text(
            json.dumps({"version": 1, "reimport": list(reimport), "parts": list(parts)}),
            encoding="utf-8",
        )

    def finish(self) -> list[str]:
        return list(self.engine().finish_world_reimport(InstallOptions(server_dir=self.server_dir)))

    def press(self, *, to_pin: bool = False) -> list[str]:
        return list(
            self.engine().update_to_latest(
                InstallOptions(server_dir=self.server_dir), to_pin=to_pin
            )
        )

    def streamed(self) -> list[tuple[str | None, str]]:
        """`(schema, first line)` of every SQL stream since the install."""
        return self.m.db.files()

    def head(self) -> str:
        return self.m.rec.heads[self.checkout]


@pytest.fixture
def box(machine: Machine) -> Box:  # noqa: F811 - the fixture imported above
    install(machine)
    rec = machine.rec
    checkout = machine.server_dir / CHECKOUT
    rec.heads[checkout] = OLD
    rec.upstream[checkout] = NEW
    rec.clones.clear()
    rec.calls.clear()
    machine.db.streams.clear()
    return Box(machine, World(rec.calls))


def first_lines(box: Box) -> list[str]:
    return [line for _schema, line in box.streamed()]


def world_conf(box: Box) -> str:
    return (box.server_dir / "etc" / "worldserver.conf").read_text(encoding="utf-8")


# -- the refusals: before anything is built, written or stopped ------------------------------


def test_a_characters_layout_change_refuses_the_update_before_anything_is_built_or_written(
    box: Box,
) -> None:
    """Review Focus 4: the file is named, the sentence is the owner's, and nothing moved."""
    box.changes(
        ("M", f"{REPO_SQL}/characters/characters_schema.sql"),
        ("M", f"{REPO_SQL}/world/creature.sql"),
    )
    jobs_before = dict(box.m.mmaps.jobs)
    with pytest.raises(InstallerError) as refused:
        box.press()
    said = str(refused.value)
    assert said.startswith(
        "Centurion changed its characters database layout "
        f"({SQL_DIR}/characters/characters_schema.sql); Yu'lon can't move your characters "
        "to it safely yet. Nothing was changed."
    )
    assert native.SOURCES_PUT_BACK_NOTE in said
    assert "build" not in box.m.rec.calls, "refused before the compile"
    assert box.streamed() == [], "refused before any database write"
    assert not any(call.startswith("stop-world") for call in box.m.rec.calls)
    assert box.head() == OLD, "the checkout is back on the commit it was built from"
    assert box.m.mmaps.jobs == jobs_before, "the pathfinding job was left running"
    assert needs_reextract(box.server_dir, ENTRY) is None


def test_return_to_the_tested_pin_refuses_a_layout_change_the_same_way(box: Box) -> None:
    """Symmetric: the diff runs from the commit the server is on to the pin."""
    box.changes(("M", f"{REPO_SQL}/characters/characters_schema.sql"), new=REV)
    with pytest.raises(InstallerError, match="characters database layout"):
        box.press(to_pin=True)
    assert "build" not in box.m.rec.calls
    assert box.streamed() == []
    assert box.head() == OLD


def test_an_accounts_layout_change_is_refused_naming_the_file(box: Box) -> None:
    box.changes(("M", f"{REPO_SQL}/auth/auth_schema.sql"))
    with pytest.raises(InstallerError) as refused:
        box.press()
    assert str(refused.value).startswith(
        f"Centurion changed its accounts database layout ({SQL_DIR}/auth/auth_schema.sql); "
        "Yu'lon can't move your accounts to it safely yet. Nothing was changed."
    )
    assert "build" not in box.m.rec.calls and box.streamed() == []


@pytest.mark.parametrize(
    "path",
    [
        f"{REPO_SQL}/auth/auth_bots.sql",
        f"{REPO_SQL}/characters/characters_seed.sql",
        f"{REPO_SQL}/characters/characters_bots.sql",
    ],
)
def test_a_change_to_what_the_accounts_or_characters_start_with_is_refused(
    box: Box, path: str
) -> None:
    """Conservative: those databases hold the player's own rows, and Yu'lon does not merge."""
    box.changes(("M", path))
    with pytest.raises(InstallerError) as refused:
        box.press()
    said = str(refused.value)
    assert f"({CHECKOUT}/{path})" in said and "Nothing was changed." in said
    assert "build" not in box.m.rec.calls and box.streamed() == []


def test_an_auth_data_change_that_touches_only_the_realm_row_is_left_out(box: Box) -> None:
    """Yu'lon owns the realm row (its address is the Networking setting's): left, and said."""
    path = f"{REPO_SQL}/auth/auth_data.sql"
    box.changes(("M", path))
    box.m.rec.diff_lines[(box.checkout, OLD, NEW, path)] = (
        "-INSERT INTO `realmlist` VALUES (1,'Centurion','127.0.0.1');",
        "+INSERT INTO `realmlist` VALUES (1,'Centurion','10.0.0.5');",
    )
    said = box.press()
    assert any(f"{SQL_DIR}/auth/auth_data.sql" in line and "realm" in line for line in said)
    assert box.streamed() == [], "the realm row Yu'lon set is not overwritten"
    assert box.head() == NEW


@pytest.mark.parametrize(
    "lines",
    [
        ("+INSERT INTO `build_info` VALUES (12342);",),
        (
            "-INSERT INTO `realmlist` VALUES (1);",
            "+INSERT INTO `realmlist` VALUES (2);",
            "+INSERT INTO `rbac_permissions` VALUES (9);",
        ),
        (),
        None,
    ],
    ids=["another-table", "realm-row-and-another", "no-readable-lines", "git-could-not-say"],
)
def test_an_auth_data_change_beyond_the_realm_row_is_refused(
    box: Box, lines: tuple[str, ...] | None
) -> None:
    """Anything but realm-row lines -- or no lines anyone could read -- refuses."""
    path = f"{REPO_SQL}/auth/auth_data.sql"
    box.changes(("M", path))
    box.m.rec.diff_lines[(box.checkout, OLD, NEW, path)] = lines
    with pytest.raises(InstallerError, match=r"auth_data\.sql"):
        box.press()
    assert "build" not in box.m.rec.calls and box.streamed() == []


def test_git_that_cannot_say_what_changed_refuses_rather_than_guessing(box: Box) -> None:
    box.m.rec.diffs[(box.checkout, OLD, NEW)] = None
    with pytest.raises(InstallerError, match="could not read what") as refused:
        box.press()
    assert "Nothing was changed." in str(refused.value)
    assert "build" not in box.m.rec.calls and box.head() == OLD


def test_the_route_asks_git_only_about_the_folders_it_reads(box: Box) -> None:
    """A code change elsewhere in the tree is no SQL and no map data: nothing beyond the rebuild."""
    box.changes(("M", "src/server/game/World/World.cpp"), ("M", f"{REPO_SQL}/import.sh"))
    said = box.press()
    assert box.streamed() == []
    assert not any("stop-world" in call for call in box.m.rec.calls)
    assert said[-1] == "Centurion is running on the newest upstream code."


# -- the world tables: imported with the servers down, before the new build starts ----------


def calls_of(box: Box, *names: str) -> list[str]:
    """The calls named (a `stop-world:` call by its prefix), in the order they happened."""
    return [
        call
        for call in box.m.rec.calls
        if call in names or any(name.endswith(":") and call.startswith(name) for name in names)
    ]


def test_the_changed_world_tables_go_in_while_the_servers_are_down_before_the_new_build_starts(
    box: Box,
) -> None:
    """Fix round 1: a build whose code needs a new world column meets it on its first start."""
    added = box.checkout / REPO_SQL / "world" / "arena_season.sql"
    added.write_text("DROP TABLE IF EXISTS arena_season;\n", encoding="utf-8")
    box.changes(
        ("M", f"{REPO_SQL}/world/creature.sql"),
        ("A", f"{REPO_SQL}/world/arena_season.sql"),
    )
    said = box.press()
    assert box.streamed() == [
        (WORLD, "DROP TABLE IF EXISTS arena_season;"),
        (WORLD, "DROP TABLE IF EXISTS creature;"),
    ], "only the two changed files, in the plan's order, into the world database"
    argvs = [argv for argv, _text in box.m.db.streams]
    assert all(argv[:3] == ("mysql", "-u", "root") for argv in argvs), "imported as root"
    assert calls_of(box, "build", "stop_servers", "sql", "recreate", "ready") == [
        "build",
        "stop_servers",
        "sql",
        "sql",
        "recreate",
        "ready",
    ], "after the compile, with the servers stopped, and before the new build starts"
    assert said[-1] == "Centurion is running on the newest upstream code."
    assert any("2 world tables" in line for line in said)
    assert box.pending() is None, "the record goes once the last file is in"


def test_the_routines_are_applied_again_with_the_renames_when_they_changed(box: Box) -> None:
    box.changes(("M", f"{REPO_SQL}/world/_routines.sql"))
    box.press()
    ((argv, text),) = box.m.db.streams
    assert argv == ("mysql", "-u", "root", WORLD)
    assert f"{AUTH}.account" in text and "legionnaireauth" not in text


def test_a_table_split_into_parts_is_imported_again_whole(box: Box) -> None:
    """`broadcast_text_locale.2.sql` only INSERTs: alone it would duplicate, and .1 alone drops .2.

    Facts §2: the one table split across files. Either part changing re-imports both,
    `.1` (DROP + CREATE) first.
    """
    second = box.checkout / REPO_SQL / "world" / "broadcast_text_locale.2.sql"
    second.write_text("INSERT INTO broadcast_text_locale VALUES (2);\n", encoding="utf-8")
    box.changes(("M", f"{REPO_SQL}/world/broadcast_text_locale.2.sql"))
    box.press()
    assert first_lines(box) == [
        "DROP TABLE IF EXISTS broadcast_text_locale;",
        "INSERT INTO broadcast_text_locale VALUES (2);",
    ]


def test_a_split_table_that_lost_a_part_is_left_whole_and_said(box: Box) -> None:
    """Fix round 1: `.1` alone would drop the rows `.2` held; the table is left, never cut."""
    box.changes(
        ("M", f"{REPO_SQL}/world/broadcast_text_locale.1.sql"),
        ("D", f"{REPO_SQL}/world/broadcast_text_locale.2.sql"),
    )
    said = box.press()
    assert box.streamed() == []
    assert any(
        f"{WORLD_SQL}/broadcast_text_locale.2.sql" in line
        and "(broadcast_text_locale) is left" in line
        for line in said
    )


def test_a_split_table_rewritten_as_one_file_is_only_imported_again(box: Box) -> None:
    whole = box.checkout / REPO_SQL / "world" / "broadcast_text_locale.sql"
    whole.write_text("DROP TABLE IF EXISTS broadcast_text_locale; -- whole\n", encoding="utf-8")
    (box.checkout / REPO_SQL / "world" / "broadcast_text_locale.1.sql").unlink()
    box.changes(
        ("A", f"{REPO_SQL}/world/broadcast_text_locale.sql"),
        ("D", f"{REPO_SQL}/world/broadcast_text_locale.1.sql"),
        ("D", f"{REPO_SQL}/world/broadcast_text_locale.2.sql"),
    )
    said = box.press()
    assert first_lines(box) == ["DROP TABLE IF EXISTS broadcast_text_locale; -- whole"]
    assert not [line for line in said if "broadcast_text_locale" in line and "left" in line]


def test_a_removed_world_file_leaves_its_table_and_says_so(box: Box) -> None:
    box.changes(("D", f"{REPO_SQL}/world/old_event.sql"))
    said = box.press()
    assert box.streamed() == []
    assert any(f"{WORLD_SQL}/old_event.sql" in line and "left" in line for line in said)


def test_return_to_the_tested_pin_imports_the_tables_that_differ_from_the_pin(box: Box) -> None:
    """Symmetric: the newer server's tables that the pin does not have are put back."""
    box.changes(("M", f"{REPO_SQL}/world/creature.sql"), new=REV)
    said = box.press(to_pin=True)
    assert first_lines(box) == ["DROP TABLE IF EXISTS creature;"]
    assert box.head() == REV
    assert said[-1] == "Centurion is running on the commit this app was tested against."


def test_a_new_build_that_does_not_come_up_starts_the_old_one_on_its_own_tables(
    box: Box,
) -> None:
    """Fix round 1: the rollback imports the same files again from the old checkout."""
    box.changes(("M", f"{REPO_SQL}/world/creature.sql"))
    box.moves_to({"creature.sql": "DROP TABLE IF EXISTS creature; -- new\n"})
    box.old_files = {"creature.sql": "DROP TABLE IF EXISTS creature; -- old\n"}
    box.ready = [False, True]
    with pytest.raises(InstallerError) as failed:
        box.press()
    assert first_lines(box) == [
        "DROP TABLE IF EXISTS creature; -- new",
        "DROP TABLE IF EXISTS creature; -- old",
    ]
    order = calls_of(box, "sql", "recreate", "ready", "restore:")
    assert order == [
        "sql",
        "recreate",
        "ready",
        "restore:centurion->aaaaaaa",
        "sql",
        "recreate",
        "ready",
    ]
    assert order.count("restore:centurion->aaaaaaa") == 1, "the sources go back once"
    assert box.head() == OLD
    assert "put back and is running again" in str(failed.value)
    assert box.pending() is None
    assert box.world.running is True


def test_a_world_import_that_fails_rolls_back_and_the_record_names_every_file(
    box: Box,
) -> None:
    """The file that failed and the ones after it; the old build was put back regardless."""
    box.changes(("M", f"{REPO_SQL}/world/creature.sql"), ("M", f"{REPO_SQL}/world/version.sql"))
    box.m.db.fail_on = "creature"
    with pytest.raises(InstallerError) as failed:
        box.press()
    said = str(failed.value)
    assert box.pending() == {
        "version": 1,
        "reimport": [f"{WORLD_SQL}/creature.sql", f"{WORLD_SQL}/version.sql"],
        "parts": [],
    }
    assert (
        "Press “Finish the world update” on the Server tab (or “Update the server "
        f"to latest…” under “Server build ▾” again) to import "
        f"{WORLD_SQL}/creature.sql, {WORLD_SQL}/version.sql again."
    ) in said
    assert "If you took the backup offered before the update" in said
    # T179 final round (lead ruling): its tables could not all be put back either,
    # so the old build is put back but NOT started on them.
    assert box.world.running is False, "no start on half-imported world tables"
    assert (
        "The build from before this rebuild was put back, and its servers were left STOPPED: "
        "This server's last update didn't finish importing its world tables. Press “Finish the "
        "world update” first."
    ) in said
    assert box.head() == OLD


def test_a_return_that_fails_says_nothing_of_a_backup_it_never_offered(box: Box) -> None:
    box.changes(("M", f"{REPO_SQL}/world/creature.sql"), new=REV)
    box.m.db.fail_on = "creature"
    with pytest.raises(InstallerError) as failed:
        box.press(to_pin=True)
    said = str(failed.value)
    assert "Finish the world update" in said
    assert "backup" not in said


def test_servers_that_cannot_be_stopped_import_nothing_and_the_record_waits_for_the_new_build(
    box: Box,
) -> None:
    """Fix round 2, as T197 left it: nothing went in, and the new build is what stays.

    The rollback's own stop failed too, so the old build was never put back: the
    tags name the new build and its sources stay. The record then names what that
    build still needs -- the file this update changed and the one waiting from
    before -- and no start is allowed until "Finish the world update" imports them.
    """
    box.leave_pending([f"{WORLD_SQL}/version.sql"])
    box.changes(("M", f"{REPO_SQL}/world/creature.sql"))
    box.world.refuse_stop = "daemon not answering"
    with pytest.raises(RollbackNotDone, match="could not be stopped") as failed:
        box.press()
    assert box.streamed() == []
    assert box.pending() == {
        "version": 1,
        "reimport": [f"{WORLD_SQL}/creature.sql", f"{WORLD_SQL}/version.sql"],
        "parts": [],
    }
    assert box.head() == NEW
    assert box.engine().start_refusal(box.server_dir) == UNFINISHED
    assert str(failed.value).endswith(native.SOURCES_LEFT_NOTE)


def test_a_stop_given_up_during_the_load_wait_leaves_no_record(box: Box) -> None:
    box.changes(("M", f"{REPO_SQL}/world/creature.sql"))
    box.world.abandon = True
    with pytest.raises(InstallerError, match="Nothing was touched"):
        box.press()
    assert box.streamed() == []
    assert box.pending() is None
    assert box.head() == OLD


def test_a_rollback_import_that_fails_after_a_failed_stop_keeps_its_record(box: Box) -> None:
    """Fix round 3 (the reviewer's probe): `back` wrote the record and dropped a table.

    The rebuild's stop signalled and failed, so `forward` never ran; the rollback's
    own stop worked and `back` began importing the old files, and failed part way.
    The record it wrote is what "Finish the world update" works from: it stays.
    """
    box.world.fail_first_stop = True
    box.changes(("M", f"{REPO_SQL}/world/creature.sql"))
    box.m.db.fail_on = "creature"
    with pytest.raises(InstallerError) as failed:
        box.press()
    assert first_lines(box) == ["DROP TABLE IF EXISTS creature;"], "back began importing"
    assert box.pending() == {
        "version": 1,
        "reimport": [f"{WORLD_SQL}/creature.sql"],
        "parts": [],
    }
    assert "Finish the world update" in str(failed.value)


def test_a_press_that_dies_of_a_bug_before_importing_puts_the_record_back(box: Box) -> None:
    """Fix round 3: `settle()` runs on every way out, not only an `InstallerError`."""
    box.leave_pending([f"{WORLD_SQL}/version.sql"])
    before = box.pending()
    box.changes(("M", f"{REPO_SQL}/world/creature.sql"))
    box.world.explode = True
    with pytest.raises(RuntimeError, match="a bug in the stop"):
        box.press()
    assert box.streamed() == []
    assert box.pending() == before


def test_a_flag_that_cannot_be_written_inside_a_wsl_distro_says_where_the_press_is(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    box.distro = "Ubuntu"
    box.changes(("M", "centurion/dbc/Spell.dbc"))
    real_write = Path.write_text

    def refuse(self: Path, *args: object, **kwargs: object) -> int:
        if self.name.startswith(trinitycore.REEXTRACT_FILE):
            raise PermissionError(13, "Permission denied")
        return real_write(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "write_text", refuse)
    said = box.press()
    (warning,) = [line for line in said if line.startswith("warning:")]
    assert "inside a WSL distro" in warning
    assert "on the Server tab once" not in warning


def test_a_new_build_whose_world_stops_after_its_banner_keeps_its_sources_and_its_tables(
    box: Box,
) -> None:
    """Fix round 2: the kept build (T71) runs on the new tables, from the new sources."""
    box.changes(("M", f"{REPO_SQL}/world/creature.sql"), ("M", "centurion/dbc/Spell.dbc"))
    box.moves_to({"creature.sql": "DROP TABLE IF EXISTS creature; -- new\n"})
    box.world_output = native.WorldOutput(
        text="TrinityCore rev. faac5fc9\nWorld initialized in 42 seconds\nABORTED\n",
        restarts=0,
        status="exited",
    )
    with pytest.raises(WorldStoppedAfterReadyError) as raised:
        box.press()
    assert first_lines(box) == ["DROP TABLE IF EXISTS creature; -- new"]
    assert box.pending() is None
    assert box.head() == NEW
    assert not [call for call in box.m.rec.calls if call.startswith("restore:")]
    assert needs_reextract(box.server_dir, ENTRY) is not None
    assert str(raised.value).endswith(native.SOURCES_KEPT_NOTE)
    assert raised.value.sources_kept is True


def test_a_record_that_cannot_be_written_stops_the_press_before_the_world_stops(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    box.changes(("M", f"{REPO_SQL}/world/creature.sql"))
    real_write = Path.write_text

    def refuse(self: Path, *args: object, **kwargs: object) -> int:
        if self.name.startswith(trinitycore.WORLD_REIMPORT_FILE):
            raise PermissionError(13, "Permission denied")
        return real_write(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "write_text", refuse)
    with pytest.raises(InstallerError, match="world server was not stopped"):
        box.press()
    assert "stop_servers" not in box.m.rec.calls
    assert box.streamed() == []
    assert box.world.running is True
    assert box.head() == OLD


def test_a_world_that_is_up_again_before_the_first_table_imports_nothing(box: Box) -> None:
    box.changes(("M", f"{REPO_SQL}/world/creature.sql"))
    box.world.stays_up = True
    with pytest.raises(InstallerError, match="is running again") as failed:
        box.press()
    assert box.streamed() == []
    assert "left stopped" not in str(failed.value)


# -- a world update that did not finish: the next press, and the retry ----------------------


def test_the_next_update_with_no_new_change_imports_the_pending_files(box: Box) -> None:
    box.leave_pending([f"{WORLD_SQL}/creature.sql"])
    box.changes()
    box.press()
    assert first_lines(box) == ["DROP TABLE IF EXISTS creature;"]
    assert box.pending() is None


def test_a_pending_file_upstream_removed_is_left_and_said(box: Box) -> None:
    box.leave_pending([f"{WORLD_SQL}/old_event.sql", f"{WORLD_SQL}/creature.sql"])
    box.changes()
    said = box.press()
    assert first_lines(box) == ["DROP TABLE IF EXISTS creature;"]
    assert any(f"{WORLD_SQL}/old_event.sql" in line and "left" in line for line in said)


def test_a_record_that_cannot_be_read_imports_every_world_table_and_says_so(box: Box) -> None:
    (box.server_dir / trinitycore.WORLD_REIMPORT_FILE).write_text("{not json", encoding="utf-8")
    box.changes()
    said = box.press()
    assert first_lines(box) == [
        "CREATE PROCEDURE p() SELECT * FROM centurion_auth.account;",
        "DROP TABLE IF EXISTS broadcast_text_locale;",
        "DROP TABLE IF EXISTS creature;",
        "DROP TABLE IF EXISTS version;",
    ]
    assert any("could not be read" in line for line in said)


def test_the_tab_says_when_a_world_update_is_waiting(box: Box) -> None:
    assert trinitycore.pending_world_reimport(box.server_dir, ENTRY) is None
    box.leave_pending([f"{WORLD_SQL}/creature.sql"], [f"{WORLD_SQL}/broadcast_text_locale"])
    told = trinitycore.pending_world_reimport(box.server_dir, ENTRY)
    assert told is not None
    assert f"{WORLD_SQL}/creature.sql" in told and "broadcast_text_locale.*.sql" in told
    assert "“Finish the world update”" in told
    elsewhere = trinitycore.pending_world_reimport(box.server_dir, ENTRY, press_here=False)
    assert elsewhere is not None and "Finish the world update" not in elsewhere
    assert "“Update the server to latest…”" in elsewhere


def on_the_built_commit(box: Box) -> None:
    box.m.rec.heads[box.checkout] = REV


def test_finish_imports_only_the_pending_files_and_does_not_build(box: Box) -> None:
    on_the_built_commit(box)
    box.leave_pending([f"{WORLD_SQL}/creature.sql"])
    said = box.finish()
    assert first_lines(box) == ["DROP TABLE IF EXISTS creature;"]
    # A recreate, not a plain start (T179 final round): a rollback that could not put
    # its tables back left the failed build's containers stopped under the old tags.
    assert calls_of(box, "build", "stop-world:", "sql", "start", "recreate", "ready") == [
        f"stop-world:{ENTRY.containers.world}",
        "sql",
        "recreate",
        "ready",
    ]
    assert box.pending() is None
    assert any("world update is finished" in line for line in said)


def test_finish_refuses_a_checkout_on_another_commit_than_the_build(box: Box) -> None:
    box.leave_pending([f"{WORLD_SQL}/creature.sql"])
    with pytest.raises(InstallerError, match="not on the commit the running build") as refused:
        box.finish()
    said = str(refused.value)
    assert "after an interrupted update" in said
    assert "“Update the server to latest…”" in said
    assert box.streamed() == [] and box.world.running is True


def test_finish_with_nothing_waiting_refuses(box: Box) -> None:
    on_the_built_commit(box)
    with pytest.raises(InstallerError, match="waiting"):
        box.finish()


def test_finish_that_fails_leads_with_the_press_and_keeps_the_record(box: Box) -> None:
    on_the_built_commit(box)
    box.leave_pending([f"{WORLD_SQL}/creature.sql"])
    box.m.db.fail_on = "creature"
    with pytest.raises(InstallerError) as failed:
        box.finish()
    said = str(failed.value)
    assert said.startswith(
        "Press “Finish the world update” on the Server tab (or the update again) to "
        f"import {WORLD_SQL}/creature.sql again."
    )
    assert said.endswith("The world server was left stopped.")
    assert box.pending() is not None
    assert box.world.running is False


def test_finish_whose_world_comes_back_up_imports_nothing_and_does_not_say_left_stopped(
    box: Box,
) -> None:
    on_the_built_commit(box)
    box.leave_pending([f"{WORLD_SQL}/creature.sql"])
    box.world.stays_up = True
    with pytest.raises(InstallerError, match="is running again") as failed:
        box.finish()
    assert box.streamed() == []
    assert "left stopped" not in str(failed.value)


def test_finish_whose_world_cannot_be_stopped_imports_nothing(box: Box) -> None:
    on_the_built_commit(box)
    box.leave_pending([f"{WORLD_SQL}/creature.sql"])
    box.world.refuse_stop = "daemon not answering"
    with pytest.raises(InstallerError, match="could not stop") as failed:
        box.finish()
    assert box.streamed() == []
    assert f"{WORLD_SQL}/creature.sql" in str(failed.value)


def test_a_plain_rebuild_replaces_the_containers_in_one_call(box: Box) -> None:
    """No update route, no work between the stop and the start: the rebuild as it always was.

    The record says the build came from `OLD`, where the box's checkout is: a plain
    Rebuild refuses a source off the commit its running build came from (T217).
    """
    state = native.read_state(box.server_dir, valid=())
    assert state is not None
    revs = tuple(
        native.SourceRev(repo=source.repo, built=f"{OLD[:7]} · 2026-09-16")
        for source in box.engine().sources_that_move()
    )
    native.write_state(box.server_dir, replace(state, source_revs=revs))
    list(box.engine().rebuild(InstallOptions(server_dir=box.server_dir)))
    assert "recreate" in box.m.rec.calls
    assert "stop_servers" not in box.m.rec.calls


# -- the realm row ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("before", "after", "note"),
    [
        ("(1,'Centurion','127.0.0.1')", "(1,'Centurion','10.0.0.5')", ""),
        (
            "(1,'Centurion','127.0.0.1','127.0.0.1','255.255.255.0',8085,1,0,1,0,0,12342)",
            "(1,'Centurion','127.0.0.1','127.0.0.1','255.255.255.0',8085,1,0,1,0,0,12343)",
            "gamebuild 12342 -> 12343",
        ),
        ("(1,'Centurion','127.0.0.1')", "(1,'Centurion PvP','127.0.0.1')", "name Centurion ->"),
        (
            "(`id`,`address`,`gamebuild`) VALUES (1,'127.0.0.1',12342)",
            "(`id`,`address`,`gamebuild`) VALUES (1,'10.0.0.5',12343)",
            "gamebuild 12342 -> 12343",
        ),
        (
            "(id, address, gamebuild) VALUES (1,'127.0.0.1',12342)",
            "(id, address, gamebuild) VALUES (1,'10.0.0.5',12342)",
            "",
        ),
    ],
    ids=["address", "gamebuild", "name", "backticked-columns", "bare-columns"],
)
def test_a_realm_row_change_is_left_out_and_what_went_beyond_the_address_is_logged(
    box: Box, before: str, after: str, note: str
) -> None:
    path = f"{REPO_SQL}/auth/auth_data.sql"
    box.changes(("M", path))
    values = "" if "VALUES" in before else "VALUES "
    box.m.rec.diff_lines[(box.checkout, OLD, NEW, path)] = (
        f"-INSERT INTO `realmlist` {values}{before};",
        f"+INSERT INTO `realmlist` {values}{after};",
    )
    said = box.press()
    assert box.streamed() == []
    about = [line for line in said if "auth_data.sql" in line]
    assert len(about) == 1, about
    if note:
        assert "changes beyond the address Yu'lon sets were logged" in about[0]
        assert note in about[0]
    else:
        assert "only the realm row's address" in about[0]


def test_an_update_inside_a_wsl_distro_says_where_the_map_data_press_is(box: Box) -> None:
    box.distro = "Ubuntu"
    box.changes(("M", "centurion/dbc/Spell.dbc"))
    said = box.press()
    assert any("inside a WSL distro" in line for line in said)
    assert not any("Stop the server, then press" in line for line in said)


# -- map data: flagged, and extracted again on a press ---------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "centurion/dbc/Spell.dbc",
        "centurion/patches/patch-Y.zip",
        "centurion/patches/patch-Y.zip.part03",
    ],
)
def test_a_change_to_the_dbc_files_or_a_required_pack_flags_the_map_data(
    box: Box, path: str
) -> None:
    box.changes(("M", path))
    said = box.press()
    told = needs_reextract(box.server_dir, ENTRY)
    assert told is not None and "extracted again" in told
    assert f"{CHECKOUT}/{path}" in told
    assert told in said, "the press says it too"
    assert box.streamed() == []


def test_a_change_to_an_optional_or_unlisted_patch_file_is_not_map_data(box: Box) -> None:
    box.changes(("M", "centurion/patches/patches.md5"), ("M", "centurion/patches/README.md"))
    box.press()
    assert needs_reextract(box.server_dir, ENTRY) is None


def test_return_to_the_tested_pin_flags_the_map_data_too(box: Box) -> None:
    box.changes(("M", "centurion/dbc/Spell.dbc"), new=REV)
    box.press(to_pin=True)
    assert needs_reextract(box.server_dir, ENTRY) is not None


def test_the_press_that_flags_the_map_data_does_not_start_the_movement_maps(box: Box) -> None:
    """Fix round 1: a set made from the map data being replaced would be thrown away."""
    started = len(box.m.mmaps.started)
    box.changes(("M", "centurion/dbc/Spell.dbc"))
    said = box.press()
    assert len(box.m.mmaps.started) == started
    assert any("pathfinding data was not started" in line for line in said)


def test_a_rolled_back_update_puts_the_map_data_flag_back(box: Box) -> None:
    box.changes(("M", "centurion/dbc/Spell.dbc"))
    box.ready = [False, True]
    with pytest.raises(InstallerError):
        box.press()
    assert needs_reextract(box.server_dir, ENTRY) is None


def test_a_refused_update_flags_nothing_even_when_the_dbc_files_changed(box: Box) -> None:
    box.changes(
        ("M", "centurion/dbc/Spell.dbc"),
        ("M", f"{REPO_SQL}/characters/characters_schema.sql"),
    )
    with pytest.raises(InstallerError):
        box.press()
    assert needs_reextract(box.server_dir, ENTRY) is None


def flagged(box: Box) -> None:
    box.changes(("M", "centurion/dbc/Spell.dbc"))
    box.press()
    assert needs_reextract(box.server_dir, ENTRY) is not None


def test_reextract_runs_the_client_data_stage_again_and_clears_the_movement_maps(
    box: Box,
) -> None:
    fake: FakeMmapsDocker = box.m.mmaps
    fake.finish(0, tiles=MIN_FILES)
    assert box.engine().mmaps_status(box.server_dir).state == "done"
    assert "mmap.enablePathFinding = 1" in world_conf(box)
    flagged(box)
    started = len(fake.started)
    box.m.tools.seen.clear()
    box.world.running = False
    said = list(
        box.engine().reextract(
            InstallOptions(server_dir=box.server_dir, client_dir=box.m.client), cancel=None
        )
    )
    assert "mapextractor" in box.m.tools.seen, "extracted again, not vouched for"
    assert "--- client-data" in said
    assert not trinitycore.extraction_client_dir(box.m.client, box.server_dir).exists()
    assert "mmap.enablePathFinding = 0" in world_conf(box), "pathfinding off with the old set"
    assert len(fake.started) == started + 1, "the movement maps are made again"
    assert needs_reextract(box.server_dir, ENTRY) is None


def test_reextract_uses_the_client_the_map_data_was_made_from(box: Box) -> None:
    flagged(box)
    box.m.tools.seen.clear()
    box.world.running = False
    list(box.engine().reextract(InstallOptions(server_dir=box.server_dir), cancel=None))
    assert "mapextractor" in box.m.tools.seen
    assert needs_reextract(box.server_dir, ENTRY) is None


def data_files(box: Box) -> dict[str, bytes]:
    """Every file under the server's `data/`, by relative path: the map data and its record."""
    data = box.server_dir / "data"
    return {
        path.relative_to(data).as_posix(): path.read_bytes()
        for path in sorted(data.rglob("*"))
        if path.is_file()
    }


def finished_with_pathfinding(box: Box) -> None:
    """A finished Centurion install: map data extracted, movement maps made and switched on."""
    box.m.mmaps.finish(0, tiles=MIN_FILES)
    assert box.engine().mmaps_status(box.server_dir).state == "done"
    assert "mmap.enablePathFinding = 1" in world_conf(box)
    data = box.server_dir / "data"
    assert (data / "Buildings" / extract.DIR_BIN).is_file(), "the marker vmap4extractor refuses"
    # Old map data a new extraction does not write byte for byte, so a test comparing
    # `data/` before and after can tell the old data put back from the new data left.
    (data / "maps" / "0003232.map").write_bytes(b"OLD")
    for folder in ("Buildings", "vmaps", "dbc"):
        (data / folder / "from-the-old-run").write_bytes(b"OLD")


def test_reextract_over_a_finished_extraction_replaces_buildings_and_vmaps(box: Box) -> None:
    """T241: vmap4extractor refuses a `Buildings/` holding `dir_bin`, which every finished
    extraction leaves; the press sets the old folders aside instead of dying at the tool."""
    finished_with_pathfinding(box)
    flagged(box)
    data = box.server_dir / "data"
    (data / "Buildings" / "left-by-the-old-run.bin").write_bytes(b"old")
    (data / "vmaps" / "left-by-the-old-run.vmtile").write_bytes(b"old")
    box.m.tools.seen.clear()
    box.world.running = False
    said = list(box.engine().reextract(InstallOptions(server_dir=box.server_dir), cancel=None))
    assert list(box.m.tools.seen) == ["mapextractor", "vmap4extractor", "vmap4assembler"]
    assert not (data / "Buildings" / "left-by-the-old-run.bin").exists()
    assert not (data / "vmaps" / "left-by-the-old-run.vmtile").exists()
    evidence = extract.read_evidence(data)
    assert evidence is not None
    assert [record.name for record in evidence.tools] == TOOL_NAMES
    assert [path.name for path in data.iterdir() if path.name.startswith(".yulon-")] == [
        extract.EVIDENCE_FILE
    ], "nothing set aside is left behind once the new map data is in"
    assert needs_reextract(box.server_dir, ENTRY) is None
    assert said[-1].endswith("Press Start on the Server tab to run the server on it.")


@pytest.mark.parametrize("fails", ["a tool", "the start check"])
def test_a_reextract_that_fails_leaves_the_old_map_data_its_record_and_pathfinding(
    box: Box, fails: str
) -> None:
    """T241: nothing of the old map data goes before the new extraction has succeeded."""
    finished_with_pathfinding(box)
    flagged(box)
    before = data_files(box)
    assert "mmaps/0000000.mmtile" in before and extract.EVIDENCE_FILE in before
    if fails == "a tool":
        box.m.tools.fail_tool = "vmap4assembler"
    else:
        box.m.tools.missing = ("530",)
    box.m.tools.seen.clear()
    box.world.running = False
    with pytest.raises(InstallerError) as failed:
        list(box.engine().reextract(InstallOptions(server_dir=box.server_dir), cancel=None))
    assert "mapextractor" in box.m.tools.seen, "the new extraction ran and wrote over data/"
    assert data_files(box) == before, "the old map data, its record and the movement maps"
    assert "mmap.enablePathFinding = 1" in world_conf(box), "pathfinding stays on"
    assert box.engine().mmaps_status(box.server_dir).state == "done"
    assert needs_reextract(box.server_dir, ENTRY) is not None, "the press is still offered"
    assert str(failed.value).endswith(trinitycore.REEXTRACT_PUT_BACK)
    assert "record was cleared" not in str(failed.value), "the old record is back, not cleared"


def test_a_folder_the_old_map_data_did_not_have_is_not_left_by_a_failed_reextract(
    box: Box,
) -> None:
    """Codex review: nothing set aside for it, so the put-back must still clear the new one."""
    finished_with_pathfinding(box)
    flagged(box)
    shutil.rmtree(box.server_dir / "data" / "vmaps")
    before = data_files(box)
    box.m.tools.missing = ("530",)
    box.world.running = False
    with pytest.raises(InstallerError):
        list(box.engine().reextract(InstallOptions(server_dir=box.server_dir), cancel=None))
    assert "vmap4assembler" in box.m.tools.seen, "the new extraction wrote vmaps/"
    assert data_files(box) == before
    assert not (box.server_dir / "data" / "vmaps").exists()


def test_a_reextract_closed_part_way_puts_the_old_map_data_back(box: Box) -> None:
    """A stream the app stops reading (GeneratorExit) is a way out too: nothing old is lost.

    With the cancel event the Server tab always passes (`_run_upkeep_press`): it is
    what stops the extraction's worker thread before the old data is put back, so
    the two never write `data/` at once. With none, the worker is left running
    (`native.stop_abandoned_worker`), and on Python 3.11 CI it wrote vmaps/ under
    the put-back.
    """
    finished_with_pathfinding(box)
    flagged(box)
    before = data_files(box)
    box.world.running = False
    press = box.engine().reextract(
        InstallOptions(server_dir=box.server_dir), cancel=threading.Event()
    )
    for line in press:
        if line.startswith("vmap assemble: running"):
            break
    assert data_files(box) != before, "the new extraction had written over data/"
    press.close()
    assert data_files(box) == before
    assert box.engine().mmaps_status(box.server_dir).state == "done"


def test_a_reextract_closed_at_the_line_saying_the_data_was_moved_aside_puts_it_back(
    box: Box,
) -> None:
    """Codex review: the line after the move is inside the put-back's reach too."""
    finished_with_pathfinding(box)
    flagged(box)
    before = data_files(box)
    box.world.running = False
    press = box.engine().reextract(InstallOptions(server_dir=box.server_dir), cancel=None)
    for line in press:
        if "was moved aside" in line:
            break
    assert not (box.server_dir / "data" / "maps").exists(), "the old map data was moved"
    press.close()
    assert data_files(box) == before


def test_reextract_says_a_stop_brings_the_old_map_data_back(box: Box) -> None:
    flagged(box)
    box.world.running = False
    said = list(box.engine().reextract(InstallOptions(server_dir=box.server_dir), cancel=None))
    assert trinitycore.REEXTRACT_CANCEL_NOTE in said
    assert trinitycore.CLIENT_DATA_CANCEL_NOTE not in said, "finished tools are not kept here"


def interrupted(box: Box) -> dict[str, bytes]:
    """The state a press that crashed in `vmap extract` leaves: old data aside, new data half in."""
    finished_with_pathfinding(box)
    flagged(box)
    old = data_files(box)
    data = box.server_dir / "data"
    plan = TC.extract
    extract.set_aside(data, extract.replaced_names(plan, also=(plan.dbc_overlay_to,)))
    (data / "maps").mkdir()
    (data / "maps" / "0003232.map").write_bytes(b"HALF")
    evidence = extract.read_evidence(data / extract.PREVIOUS_DIR)
    assert evidence is not None
    extract.write_evidence(data, replace(evidence, tools=evidence.tools[:1]))
    return old


def test_a_reextract_after_one_that_crashed_falls_back_on_the_data_before_the_crash(
    box: Box,
) -> None:
    """T241: what the crashed press set aside is the last whole map data, not the half it left."""
    old = interrupted(box)
    box.m.tools.fail_tool = "vmap4assembler"
    box.world.running = False
    with pytest.raises(InstallerError):
        list(box.engine().reextract(InstallOptions(server_dir=box.server_dir), cancel=None))
    assert data_files(box) == old


def test_a_reextract_after_one_that_crashed_finishes_and_leaves_nothing_aside(box: Box) -> None:
    interrupted(box)
    box.world.running = False
    said = list(box.engine().reextract(InstallOptions(server_dir=box.server_dir), cancel=None))
    assert any("did not finish; the map data from before it was put back" in s for s in said)
    assert not (box.server_dir / "data" / extract.PREVIOUS_DIR).exists()
    assert (box.server_dir / "data" / "maps" / "0003232.map").read_bytes() == b"MAPS"


def visible(files: dict[str, bytes]) -> dict[str, bytes]:
    """The map data the server reads: everything but what a re-extraction keeps aside."""
    return {name: body for name, body in files.items() if not name.startswith(".yulon-previous")}


def test_map_data_superseded_by_a_press_that_finished_is_dropped_not_put_back(box: Box) -> None:
    """A press whose new map data was in, and that could not delete the old: never put back."""
    finished_with_pathfinding(box)
    flagged(box)
    data = box.server_dir / "data"
    (data / extract.PREVIOUS_DIR / "maps").mkdir(parents=True)
    (data / extract.PREVIOUS_DIR / "maps" / "0003232.map").write_bytes(b"STALE")
    (data / extract.SUPERSEDED_MARK).mkdir()
    current = data_files(box)
    box.m.tools.fail_tool = "vmap4assembler"
    box.world.running = False
    with pytest.raises(InstallerError):
        list(box.engine().reextract(InstallOptions(server_dir=box.server_dir), cancel=None))
    assert data_files(box) == visible(current)


def test_old_map_data_deleted_part_way_after_the_new_is_in_is_never_put_back(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex adversarial review: a deletion of the old data that stops half way (a locked
    file, a crash) must leave nothing a later press would put back over the new map data."""
    finished_with_pathfinding(box)
    flagged(box)
    data = box.server_dir / "data"
    real = extract._remove_tree
    cut: list[Path] = []

    def stops_half_way(path: Path) -> bool:
        if path == data / extract.PREVIOUS_DIR and not cut:
            first = sorted(path.iterdir())[0]
            real(first) if first.is_dir() else first.unlink()
            cut.append(first)
            raise PermissionError(13, "Permission denied")
        return real(path)

    monkeypatch.setattr(extract, "_remove_tree", stops_half_way)
    (data / "maps" / "0003232.map").write_bytes(b"OLD")  # so old and new data differ
    box.world.running = False
    said = list(box.engine().reextract(InstallOptions(server_dir=box.server_dir), cancel=None))
    assert cut and any(line.startswith("warning: the map data from before") for line in said)
    new = visible(data_files(box))
    assert new["maps/0003232.map"] == b"MAPS"
    box.m.tools.fail_tool = "vmap4assembler"
    with pytest.raises(InstallerError):
        list(box.engine().reextract(InstallOptions(server_dir=box.server_dir), cancel=None))
    assert data_files(box) == new, "the press-one map data, with nothing of the half-deleted old"


def test_old_map_data_that_cannot_be_marked_superseded_is_kept_and_named(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No mark, no deletion: deleting what may be the only copy is never the fallback."""
    finished_with_pathfinding(box)
    flagged(box)
    data = box.server_dir / "data"
    old_maps = (data / "maps" / "0003232.map").read_bytes()
    real = Path.mkdir

    def refuse_the_mark(self: Path, *args: object, **kwargs: object) -> None:
        if self.name == extract.SUPERSEDED_MARK:
            raise PermissionError(13, "Permission denied")
        real(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "mkdir", refuse_the_mark)
    box.world.running = False
    said = list(box.engine().reextract(InstallOptions(server_dir=box.server_dir), cancel=None))
    assert (data / extract.PREVIOUS_DIR / "maps" / "0003232.map").read_bytes() == old_maps
    warning = next(line for line in said if line.startswith("warning: the map data from before"))
    assert str(data / extract.PREVIOUS_DIR) in warning
    assert "delete that folder" in warning and "never used again" not in warning
    assert warning.endswith(
        f"a later “{trinitycore.REEXTRACT_BUTTON}” may put it back if the map data in place no "
        "longer matches the server's files by then."
    ), "the press is no longer offered; the warning says only what is true (re-reviews)"
    assert needs_reextract(box.server_dir, ENTRY) is None
    monkeypatch.undo()

    # The FOLLOWING press (cold review of 4d672a26): the unmarked old data must not come
    # back over the new, whether that press finishes or fails.
    new = visible(data_files(box))
    assert new["maps/0003232.map"] == b"MAPS"
    box.m.tools.fail_tool = "vmap4assembler"
    with pytest.raises(InstallerError):
        list(box.engine().reextract(InstallOptions(server_dir=box.server_dir), cancel=None))
    assert data_files(box) == new


@pytest.mark.parametrize("unfinished", ["dbc overlay", "a tool's record", "start check"])
def test_old_map_data_aside_beside_new_data_that_is_not_whole_is_put_back(
    box: Box, unfinished: str
) -> None:
    """The other half of the rule above: new map data a server could not use -- one thing
    the client-data stage does left undone, each fixture breaking exactly one -- means the
    press died, and the old data comes back."""
    finished_with_pathfinding(box)
    flagged(box)
    old = data_files(box)
    data = box.server_dir / "data"
    box.world.running = False
    list(box.engine().reextract(InstallOptions(server_dir=box.server_dir), cancel=None))
    # The state of a press that died in the DBC overlay: the old data aside, unmarked, with
    # its own record; the new data in place, every tool recorded and counted and the start
    # check met -- and the extractor's Spell.dbc still where the server's goes.
    for name, body in old.items():
        (data / extract.PREVIOUS_DIR / name).parent.mkdir(parents=True, exist_ok=True)
        (data / extract.PREVIOUS_DIR / name).write_bytes(body)
    if unfinished == "dbc overlay":
        (data / "dbc" / "Spell.dbc").write_bytes(b"the client's Spell.dbc")
    elif unfinished == "a tool's record":
        record = extract.read_evidence(data)
        assert record is not None
        extract.write_evidence(data, replace(record, tools=record.tools[:-1]))
    else:
        (data / "vmaps" / "530.vmtree").unlink()
    box.m.tools.fail_tool = "vmap4assembler"
    with pytest.raises(InstallerError):
        list(box.engine().reextract(InstallOptions(server_dir=box.server_dir), cancel=None))
    assert visible(data_files(box)) == old


def test_a_put_back_cut_short_that_left_whole_looking_data_is_still_finished(box: Box) -> None:
    """The record decides which half of the rule applies: a put-back restores it FIRST, so an
    aside without its record is a put-back cut short, even when what is in place -- old record,
    old maps, the new run's other folders -- would pass for whole map data."""
    finished_with_pathfinding(box)
    flagged(box)
    old = data_files(box)
    data = box.server_dir / "data"
    box.world.running = False
    list(box.engine().reextract(InstallOptions(server_dir=box.server_dir), cancel=None))
    for name, body in old.items():
        top = name.split("/", 1)[0]
        if top in ("Buildings", "vmaps", "dbc"):
            (data / extract.PREVIOUS_DIR / name).parent.mkdir(parents=True, exist_ok=True)
            (data / extract.PREVIOUS_DIR / name).write_bytes(body)
        elif top in ("maps", extract.EVIDENCE_FILE):
            (data / name).write_bytes(body)
    assert box.engine()._map_data_whole(box.server_dir, data), "it passes for whole"
    box.m.tools.fail_tool = "vmap4assembler"
    with pytest.raises(InstallerError):
        list(box.engine().reextract(InstallOptions(server_dir=box.server_dir), cancel=None))
    folders = ("Buildings/", "vmaps/", "dbc/", "maps/", extract.EVIDENCE_FILE)
    assert {n: b for n, b in data_files(box).items() if n.startswith(folders)} == {
        n: b for n, b in old.items() if n.startswith(folders)
    }, "the old map data, whole: never the new run's folders under the old record"


def test_the_real_put_back_restores_the_record_first_so_a_cut_short_one_is_finished(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scoped re-review of e457b29e: `put_back()` itself, cut short at its SECOND name, after
    a stage that had left whole map data. The record must already be back, so the next press
    puts the rest back rather than taking the aside for a finished press's leftovers."""
    finished_with_pathfinding(box)
    flagged(box)
    old = data_files(box)
    data = box.server_dir / "data"
    box.world.running = False
    press = box.engine().reextract(
        InstallOptions(server_dir=box.server_dir), cancel=threading.Event()
    )
    for line in press:
        if line.startswith("The map data the world server checks at start"):
            break
    assert box.engine()._map_data_whole(box.server_dir, data), "the new data in place is whole"
    real = os.rename
    calls: list[str] = []

    def second_fails(src: object, dst: object) -> None:
        calls.append(Path(str(src)).name)
        if len(calls) == 2:
            raise PermissionError(13, "Permission denied")
        real(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(extract.os, "rename", second_fails)
    press.close()
    monkeypatch.undo()
    assert calls[0] == extract.EVIDENCE_FILE, "the record goes back first"
    assert (data / extract.EVIDENCE_FILE).read_bytes() == old[extract.EVIDENCE_FILE]
    assert not (data / extract.PREVIOUS_DIR / extract.EVIDENCE_FILE).exists()
    box.m.tools.fail_tool = "vmap4assembler"
    with pytest.raises(InstallerError):
        list(box.engine().reextract(InstallOptions(server_dir=box.server_dir), cancel=None))
    assert data_files(box) == visible(old), "the old map data, all of it, not the new"


def test_a_put_back_that_fails_says_what_the_next_press_will_do(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scoped re-review note 2: not "puts it back first" -- the next press keeps whichever map
    data is whole, which may be the new."""

    def fails(data_dir: Path) -> tuple[str, ...]:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(extract, "put_back", fails)
    told = box.engine()._put_the_old_map_data_back(box.server_dir / "data")
    assert "puts it back first" not in told
    assert told.endswith(
        f"pressing “{trinitycore.REEXTRACT_BUTTON}” again settles it first: it keeps the new map "
        "data if that is whole, and puts this back otherwise."
    )


def test_pathfinding_data_that_cannot_be_removed_after_the_new_map_data_does_not_fail_the_press(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scoped re-review note 3: the map data is in; the press says what to do and finishes."""
    finished_with_pathfinding(box)
    flagged(box)

    def refuses(*args: object, **kwargs: object) -> None:
        raise mmaps.MmapsError("data/mmaps could not be emptied (Permission denied)")

    monkeypatch.setattr(mmaps, "discard", refuses)
    box.world.running = False
    said = list(box.engine().reextract(InstallOptions(server_dir=box.server_dir), cancel=None))
    warning = next(line for line in said if line.startswith("warning: the pathfinding data"))
    assert "could not be removed" in warning and "Make the pathfinding data" in warning
    assert said[-1].endswith("Press Start on the Server tab to run the server on it.")
    assert needs_reextract(box.server_dir, ENTRY) is None


def test_a_put_back_cut_short_after_the_record_is_finished_by_the_next_press(box: Box) -> None:
    """Codex adversarial review: the old record back in place does not make the folders still
    aside stale. Only a press whose new map data was in marks them so (`extract.supersede()`)."""
    old = interrupted(box)
    data = box.server_dir / "data"
    os.replace(data / extract.PREVIOUS_DIR / extract.EVIDENCE_FILE, data / extract.EVIDENCE_FILE)
    assert extract.read_evidence(data) is not None, "a finished extraction's record, in place"
    box.m.tools.fail_tool = "vmap4assembler"
    box.world.running = False
    with pytest.raises(InstallerError):
        list(box.engine().reextract(InstallOptions(server_dir=box.server_dir), cancel=None))
    assert data_files(box) == old


@pytest.mark.parametrize("running", [True, None])
def test_reextract_refuses_while_the_world_server_may_be_running(
    box: Box, running: bool | None
) -> None:
    flagged(box)
    box.m.tools.seen.clear()
    box.world.running = running
    with pytest.raises(InstallerError, match="Nothing was changed"):
        list(box.engine().reextract(InstallOptions(server_dir=box.server_dir), cancel=None))
    assert box.m.tools.seen == {}
    assert needs_reextract(box.server_dir, ENTRY) is not None


def test_reextract_with_no_client_to_read_refuses(box: Box) -> None:
    flagged(box)
    (box.server_dir / "data" / extract.EVIDENCE_FILE).unlink()
    box.world.running = False
    with pytest.raises(InstallerError, match="client folder"):
        list(box.engine().reextract(InstallOptions(server_dir=box.server_dir), cancel=None))


def test_a_flag_that_cannot_be_read_still_says_the_map_data_must_be_extracted(
    box: Box,
) -> None:
    (box.server_dir / trinitycore.REEXTRACT_FILE).write_text("{not json", encoding="utf-8")
    told = needs_reextract(box.server_dir, ENTRY)
    assert told is not None and "extracted again" in told


def test_the_app_wiring_offers_the_route_and_the_reextract(box: Box) -> None:
    assert update_to_latest_for_app(ENTRY, box.server_dir) is not None
    assert reextract_for_app(ENTRY, box.server_dir) is not None
    assert reextract_for_app(ENTRY, box.server_dir, wsl_distro="Ubuntu") is None
    assert world_reimport_for_app(ENTRY, box.server_dir) is not None
    assert world_reimport_for_app(ENTRY, box.server_dir, wsl_distro="Ubuntu") is not None


def test_the_app_wiring_offers_no_world_retry_to_another_family(tmp_path: Path) -> None:
    wotlk = load_catalog().get("wow-wotlk")
    assert world_reimport_for_app(wotlk, tmp_path) is None


# -- the catalog: what the route reads is data, and checked ----------------------------------


def with_updates(monkeypatch: pytest.MonkeyPatch, updates: object) -> None:
    block = copy.deepcopy(TRINITYCORE)
    if updates is None:
        block.pop("updates")
    else:
        block["updates"] = updates
    monkeypatch.setattr("tests.support_trinitycore.TRINITYCORE", block)


def test_an_entry_offering_the_update_must_say_what_it_does_with_the_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_updates(monkeypatch, None)
    with pytest.raises(ValueError, match="update_to_latest"):
        centurion_like()


def test_a_reimported_phase_must_write_into_the_world_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Imported again over a server's characters, a dump's DROP TABLE would delete them."""
    updates = copy.deepcopy(TRINITYCORE["updates"])
    updates["reimport_phases"] = ["world tables", "characters"]
    with_updates(monkeypatch, updates)
    with pytest.raises(ValueError, match=f"'characters'.*{CHARS}"):
        centurion_like()


def test_a_reimported_phase_must_be_one_the_plan_has(monkeypatch: pytest.MonkeyPatch) -> None:
    updates = copy.deepcopy(TRINITYCORE["updates"])
    updates["reimport_phases"] = ["world tables", "world tabels"]
    with_updates(monkeypatch, updates)
    with pytest.raises(ValueError, match="world tabels"):
        centurion_like()


@pytest.mark.parametrize("key", ["layout_files", "skip_lines"])
def test_a_named_file_must_be_one_the_plan_imports(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    updates = copy.deepcopy(TRINITYCORE["updates"])
    stray = f"{SQL_DIR}/auth/nowhere.sql"
    updates[key] = [stray] if key == "layout_files" else {stray: ["INSERT INTO `x` "]}
    with_updates(monkeypatch, updates)
    with pytest.raises(ValueError, match="nowhere.sql"):
        centurion_like()


def test_reextract_stops_a_running_job_and_keeps_its_tiles_only_until_the_new_data_is_in(
    box: Box,
) -> None:
    """T209 with T241: the stop keeps the tiles (the old map data may come back), and a
    finished extraction throws them away with the old data, so the new run starts empty."""
    fake: FakeMmapsDocker = box.m.mmaps
    flagged(box)
    assert not fake.jobs, "the update stopped it, and the flag holds the restart"
    box.engine().start_mmaps(box.server_dir)
    fake.write_tiles(30)
    box.world.running = False
    said = list(
        box.engine().reextract(
            InstallOptions(server_dir=box.server_dir, client_dir=box.m.client), cancel=None
        )
    )
    assert (
        "Stopped making the pathfinding data before the extraction; its 30 finished tiles are "
        "kept and pathfinding stays off. If the extraction does not finish, the run continues "
        "from them; once it has, they are removed with the old map data."
    ) in said
    assert len(fake.mmaps_at_run[-1]) == 0, "the new run starts from an empty folder"


def _a_run_that_crashed(box: Box, tiles: int) -> list[str]:
    """A pathfinding run that crashed part way and kept `tiles` whole tiles (T209)."""
    fake: FakeMmapsDocker = box.m.mmaps
    flagged(box)
    box.engine().start_mmaps(box.server_dir)
    fake.write_tiles(tiles)
    fake.finish(139)
    now = box.engine().mmaps_status(box.server_dir)
    assert now.state == "failed" and now.kept == tiles
    return sorted(path.name for path in (box.server_dir / "data" / "mmaps").iterdir())


@pytest.mark.parametrize("running", [False, True], ids=["crashed", "running"])
def test_a_failed_reextract_leaves_a_pathfinding_run_that_continues_from_its_tiles(
    box: Box, running: bool
) -> None:
    """The cold review of 4d672a26: the old map data comes back unchanged -- names, sizes and
    dates, which `mmaps._evidence()` hashes -- so the run made from it continues."""
    fake: FakeMmapsDocker = box.m.mmaps
    if running:
        flagged(box)
        box.engine().start_mmaps(box.server_dir)
        fake.write_tiles(12)
        tiles = sorted(path.name for path in (box.server_dir / "data" / "mmaps").iterdir())
    else:
        tiles = _a_run_that_crashed(box, 12)
    box.m.tools.fail_tool = "vmap4assembler"
    box.world.running = False
    with pytest.raises(InstallerError):
        list(box.engine().reextract(InstallOptions(server_dir=box.server_dir), cancel=None))
    assert box.engine().mmaps_status(box.server_dir).kept == 12
    box.engine().start_mmaps(box.server_dir)
    assert fake.mmaps_at_run[-1] == tiles, "continued from its tiles, not from 0 %"


def test_the_flag_file_is_json_naming_what_changed(box: Box) -> None:
    flagged(box)
    raw = json.loads((box.server_dir / trinitycore.REEXTRACT_FILE).read_text("utf-8"))
    assert raw["changed"] == [f"{CHECKOUT}/centurion/dbc/Spell.dbc"]


# -- the movement maps' route hook (carried from Task 4's review) ----------------------------


def test_a_failed_job_whose_container_is_already_gone_stops_nothing(tmp_path: Path) -> None:
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    (server_dir / mmaps.RECORD_FILE).write_text(
        json.dumps({"state": "failed", "container": "centurion-mmaps-0123abcd"}), "utf-8"
    )
    fake = FakeMmapsDocker()
    assert (
        mmaps.stop_for_route(
            server_dir,
            ENTRY,
            "the rebuild",
            press="Rebuild the server…",
            clear=False,
            runner=fake,
            install_id="0123abcd",
        )
        is None
    )
    assert fake.calls == ["remove:centurion-mmaps-0123abcd"]


@pytest.mark.parametrize("state", ["failed", "running"])
def test_a_route_the_job_holds_up_says_to_check_docker_and_press_it_again(
    tmp_path: Path, state: str
) -> None:
    server_dir = tmp_path / "server"
    (server_dir / "data" / "mmaps").mkdir(parents=True)
    (server_dir / mmaps.RECORD_FILE).write_text(
        json.dumps({"state": state, "container": "centurion-mmaps-0123abcd"}), "utf-8"
    )
    fake = FakeMmapsDocker()
    fake.refuse_remove = "daemon not answering"
    fake.answers = False
    with pytest.raises(mmaps.MmapsError) as refused:
        mmaps.stop_for_route(
            server_dir,
            ENTRY,
            "the update to the newest code",
            press="Update the server to latest…",
            clear=True,
            runner=fake,
            install_id="0123abcd",
        )
    said = str(refused.value)
    assert said.endswith(
        "Check that Docker is running, then press “Update the server to latest…” again."
    )
    if state == "failed":
        assert "did not start" not in said and "that failed" in said


def test_a_record_that_cannot_be_forgotten_is_a_status_and_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A finished set emptied by hand: the record's removal failing is a failed state, said."""
    server_dir = tmp_path / "server"
    (server_dir / "data" / "mmaps").mkdir(parents=True)
    (server_dir / "etc").mkdir()
    (server_dir / "etc" / "worldserver.conf").write_text(
        "mmap.enablePathFinding = 1\n", encoding="utf-8"
    )
    (server_dir / mmaps.RECORD_FILE).write_text(
        json.dumps({"state": "done", "container": "c", "pathfinding_on_at": "x"}), "utf-8"
    )
    real_unlink = Path.unlink

    def refuse(self: Path, missing_ok: bool = False) -> None:
        if self.name == mmaps.RECORD_FILE:
            raise PermissionError(13, "Permission denied")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", refuse)
    status = mmaps.mmaps_status(server_dir, ENTRY, runner=FakeMmapsDocker(), install_id="0123abcd")
    assert status.state == "failed"
    assert "Permission denied" in status.error


# -- no start while a world update is unfinished (T179 final round, lead ruling) ------------

UNFINISHED = (
    "This server's last update didn't finish importing its world tables. Press "
    "“Finish the world update” first."
)


def test_a_rebuild_is_refused_before_anything_while_a_world_update_is_unfinished(
    box: Box,
) -> None:
    """A Rebuild ends in a start and does not finish the tables: refused before the compile."""
    box.leave_pending([f"{WORLD_SQL}/creature.sql"])
    with pytest.raises(InstallerError) as refused:
        list(box.engine().rebuild(InstallOptions(server_dir=box.server_dir)))
    assert str(refused.value) == f"{UNFINISHED} Nothing was changed."
    assert box.m.rec.calls == [], "nothing built, stopped or started"
    assert box.world.running is True


@pytest.mark.parametrize("record", ["names", "unreadable"])
def test_the_engines_own_start_is_refused_while_a_world_update_is_unfinished(
    box: Box, record: str
) -> None:
    """The install's `up` stage: the same door, below every press that ends in it."""
    if record == "names":
        box.leave_pending([f"{WORLD_SQL}/creature.sql"])
    else:
        (box.server_dir / trinitycore.WORLD_REIMPORT_FILE).write_text("{", encoding="utf-8")
    box.world.running = False
    engine = box.engine()
    with pytest.raises(InstallerError) as refused:
        list(engine.stage_named("up").run(context(box.m)))
    assert str(refused.value) == f"{UNFINISHED} The server was not started."
    assert "start" not in box.m.rec.calls and box.world.running is False


def test_finish_is_the_one_start_allowed_and_it_starts_once_the_record_is_gone(box: Box) -> None:
    on_the_built_commit(box)
    box.leave_pending([f"{WORLD_SQL}/creature.sql"])
    box.finish()
    assert box.pending() is None
    assert box.world.running is True


# T197 fix round 8: both records at once. It cannot happen today -- the mixed-tags record
# (`native.START_REFUSED_FILE`) needs a rollback that moved one image tag of several, and
# a TrinityCore server builds ONE image, so its rollback is never part-way -- but nothing
# keeps a TrinityCore server single-image, so each record's way out is bound against the
# other: the Rebuild first (any start on mixed tags, the finish's included, runs two
# builds side by side), then "Finish the world update".


def _both_records(box: Box) -> None:
    on_the_built_commit(box)
    box.leave_pending([f"{WORLD_SQL}/creature.sql"])
    assert native.owe_start(box.server_dir) == ""


def test_with_mixed_tags_the_finish_imports_nothing_and_names_the_rebuild(box: Box) -> None:
    _both_records(box)
    with pytest.raises(InstallerError) as refused:
        box.finish()
    assert str(refused.value) == f"{native.REBUILD_OWED_REFUSAL} Nothing was changed."
    assert box.streamed() == [] and box.world.running is True
    assert box.pending() is not None, "the world update still waits"


def test_with_mixed_tags_the_finishs_own_start_starts_nothing(box: Box) -> None:
    """The belt under the refusal above: the start the finish ends in asks again."""
    _both_records(box)
    box.world.running = False
    with pytest.raises(InstallerError) as refused:
        list(box.engine()._start_after_finish(context(box.m)))
    assert str(refused.value) == (
        f"The world update is finished, but {native.REBUILD_OWED_REFUSAL} The server was not "
        "started."
    )
    assert "recreate" not in box.m.rec.calls and box.world.running is False


def test_with_both_records_the_rebuild_goes_first_and_then_the_finish(box: Box) -> None:
    _both_records(box)
    said = list(box.engine().rebuild(InstallOptions(server_dir=box.server_dir)))
    assert said[-1].endswith(f"was rebuilt and is running in {box.server_dir}")
    assert not (box.server_dir / native.START_REFUSED_FILE).exists()
    assert box.pending() is not None, "the Rebuild finishes no world update"
    with pytest.raises(InstallerError, match=UNFINISHED):
        list(box.engine().rebuild(InstallOptions(server_dir=box.server_dir)))
    box.finish()
    assert box.pending() is None and box.world.running is True


def test_inside_a_wsl_distro_the_engine_names_the_update_press(box: Box) -> None:
    box.leave_pending([f"{WORLD_SQL}/creature.sql"])
    box.distro = "Ubuntu"
    refused = box.engine().start_refusal(box.server_dir)
    assert refused is not None and "“Update the server to latest…”" in refused
    assert "Finish the world update" not in refused


# -- the movement-map job of a server inside a WSL distro (T179 final round) ------------------


def test_inside_a_wsl_distro_the_engines_mmaps_runner_asks_that_distros_docker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`installer_for_app(..., wsl_distro=)` binds every seam to the distro; the job's too."""
    from yulon.install_wiring import installer_for_app

    asked: list[tuple[str, object]] = []

    def record(name: str) -> Callable[..., object]:
        def call(*args: object, **kwargs: object) -> object:
            asked.append((name, kwargs.get("wsl_distro")))
            return docker.ContainerExit(missing=True) if name == "inspect" else None

        return call

    monkeypatch.setattr(docker, "container_exit", record("inspect"))
    monkeypatch.setattr(docker, "remove_container", record("remove"))
    monkeypatch.setattr(docker, "log_tail", record("log_tail"))
    monkeypatch.setattr(docker, "daemon_cpus", record("cpus"))
    made = installer_for_app(ENTRY, wsl_distro="Ubuntu-yulon")
    assert isinstance(made, trinitycore.TrinityCoreInstaller)
    runner = made._mmaps_runner
    assert isinstance(runner, mmaps.DockerRunner)

    runner.inspect("job", timeout=1)
    runner.remove("job", timeout=1)
    runner.log_tail("job", 5, timeout=1)
    runner.cpus(timeout=1)
    assert asked == [
        ("inspect", "Ubuntu-yulon"),
        ("remove", "Ubuntu-yulon"),
        ("log_tail", "Ubuntu-yulon"),
        ("cpus", "Ubuntu-yulon"),
    ]
    monkeypatch.setattr(docker, "run_detached", record("run"))
    with pytest.raises(docker.DockerCommandError, match="inside the WSL distro Ubuntu-yulon"):
        runner.run_detached(docker.ContainerRun(image="i", argv=("x",)), "job", timeout=1)
    assert ("run", None) not in asked, "never started on this host's daemon"


def test_on_this_host_the_engines_mmaps_runner_is_the_local_daemon() -> None:
    from yulon.install_wiring import installer_for_app

    made = installer_for_app(ENTRY)
    assert isinstance(made, trinitycore.TrinityCoreInstaller)
    runner = made._mmaps_runner
    assert isinstance(runner, mmaps.DockerRunner) and runner.wsl_distro is None


# -- a refused update says what is next, and is not offered again (T179 final round) ---------

NEXT_AFTER_UPDATE = (
    "Your server keeps running the version it has. “Return to the tested pin…” stays "
    "available; this update can be taken once Yu'lon supports the change."
)


def test_a_refused_update_says_the_server_keeps_its_version_and_what_stays_available(
    box: Box,
) -> None:
    box.changes(("M", f"{REPO_SQL}/characters/characters_schema.sql"))
    with pytest.raises(InstallerError) as refused:
        box.press()
    assert NEXT_AFTER_UPDATE in str(refused.value)


def test_a_refused_return_says_the_server_keeps_its_version_and_no_more(box: Box) -> None:
    box.changes(("M", f"{REPO_SQL}/characters/characters_schema.sql"), new=REV)
    with pytest.raises(InstallerError) as refused:
        box.press(to_pin=True)
    said = str(refused.value)
    assert "Your server keeps running the version it has." in said
    assert "stays available" not in said
    assert native.read_state(box.server_dir, valid=()).refused_updates == ()  # type: ignore[union-attr]


def _github(upstream_at: str) -> Callable[[str, str], bytes]:
    """GitHub's compare, answered by base: upstream's branch is at `upstream_at`."""

    def get(url: str, accept: str) -> bytes:
        base = url.split("/compare/", 1)[1].split("...", 1)[0]
        ahead = 0 if base == upstream_at else 3
        status = "identical" if ahead == 0 else "ahead"
        return json.dumps({"status": status, "ahead_by": ahead, "behind_by": 0}).encode()

    return get


def _news_line(box: Box, upstream_at: str) -> str:
    from yulon.catalog import upstream

    upstream.forget(box.server_dir)
    made = engine(box.m, upstream_get=_github(upstream_at))
    return upstream.line(made.upstream_news(InstallOptions(server_dir=box.server_dir)))


def test_the_refused_commit_is_remembered_and_not_offered_again(box: Box) -> None:
    repo = ENTRY.emulator.sources[0].repo
    assert "Upstream has new code" in _news_line(box, NEW), "offered before the press"

    box.changes(("M", f"{REPO_SQL}/characters/characters_schema.sql"))
    with pytest.raises(InstallerError):
        box.press()

    state = native.read_state(box.server_dir, valid=())
    assert state is not None and state.refused_updates == ((repo, NEW),)
    assert _news_line(box, NEW) == "", "upstream still on the refused commit: nothing offered"
    assert "Upstream has new code" in _news_line(box, "c" * 40), "a newer commit offers again"


def test_an_update_that_lands_forgets_the_refused_commit(box: Box) -> None:
    repo = ENTRY.emulator.sources[0].repo
    state = native.read_state(box.server_dir, valid=())
    assert state is not None
    native.write_state(box.server_dir, replace(state, refused_updates=((repo, "d" * 40),)))
    box.changes(("M", f"{REPO_SQL}/world/creature.sql"))
    box.press()
    after = native.read_state(box.server_dir, valid=())
    assert after is not None and after.refused_updates == ()


def test_a_rollback_that_leaves_the_servers_stopped_never_says_they_run(box: Box) -> None:
    """The whole sentence, re-review of a073725d: put back, STOPPED, and no "running" in it."""
    box.changes(("M", f"{REPO_SQL}/world/creature.sql"), ("M", f"{REPO_SQL}/world/version.sql"))
    box.m.db.fail_on = "creature"
    with pytest.raises(native.ServersLeftStopped) as failed:
        box.press()
    files = f"{WORLD_SQL}/creature.sql, {WORLD_SQL}/version.sql"
    stopped = (
        f"The import stopped: {WORLD_SQL}/creature.sql failed while loading into "
        f"{WORLD} (ERROR 1419 (HY000) at line 80: You do not have the SUPER). Nothing after "
        f"it was applied. {files} were not imported again."
    )
    assert str(failed.value) == (
        f"{stopped} The build from before this rebuild was put back, and its servers were left "
        f"STOPPED: {UNFINISHED} Before it was replaced, {ENTRY.containers.world} had printed:\n"
        "TrinityCore rev. faac5fc9\nWorld initialized in 42 seconds\n"
        "What the new build wrote into the database on its first start, if anything, is NOT "
        "put back by this -- the lines above say whether its updater ran -- so the old build "
        "will start on the database as the new one left it.\n"
        "Press “Finish the world update” on the Server tab (or “Update the server to latest…” "
        f"under “Server build ▾” again) to import {files} again. The world tables could not all "
        f"be put back for the build from before this update: {stopped} If you took the backup "
        "offered before the update, it has them as they were (Restore on the Maintenance tab). "
        "The source folders were put back on the commits they were on, so what is on disk is "
        "the build that was put back; it stays stopped until its world tables are in."
    )
    assert "running" not in str(failed.value)
    assert box.world.running is False


# -- T197: a rollback that stops early keeps the new build, its sources and its tables ----


def _refuse_the_failed_name(box: Box) -> None:
    """The rollback cannot give the new build its `-failed` name: it stops before any tag moves."""

    def tag_image(src: str, dst: str) -> str:
        box.m.rec.calls.append(f"tag:{src}->{dst}")
        return "read-only layer store" if dst.endswith(native.FAILED_TAG_SUFFIX) else ""

    box.seams["tag_image"] = tag_image


def test_a_rollback_that_stops_early_after_the_import_keeps_the_new_tables_and_sources(
    box: Box,
) -> None:
    """T197: the new tables are in, the new build stays on its tags, so its sources stay too.

    Putting the old checkout back here left the old sources under the new build
    and the new tables, with a sentence saying they agree again.
    """
    box.changes(("M", f"{REPO_SQL}/world/creature.sql"), ("M", "centurion/dbc/Spell.dbc"))
    box.moves_to({"creature.sql": "DROP TABLE IF EXISTS creature; -- new\n"})
    box.old_files = {"creature.sql": "DROP TABLE IF EXISTS creature; -- old\n"}
    box.ready = [False]
    _refuse_the_failed_name(box)
    with pytest.raises(RollbackNotDone) as failed:
        box.press()
    said = str(failed.value)
    assert "could not be given a name to undo onto" in said
    assert first_lines(box) == ["DROP TABLE IF EXISTS creature; -- new"], "no old tables"
    assert box.pending() is None, "every table the new build needs is in"
    assert box.head() == NEW
    assert not [call for call in box.m.rec.calls if call.startswith("restore:")]
    assert needs_reextract(box.server_dir, ENTRY) is not None, "the new build's map data"
    state = native.read_state(box.server_dir, valid=())
    assert state is not None and {rev.built[:7] for rev in state.source_revs} == {NEW[:7]}
    assert said.endswith(native.SOURCES_LEFT_NOTE)
    assert native.SOURCES_PUT_BACK_NOTE not in said and "agree again" not in said
    assert failed.value.sources_kept is True


def test_a_rollback_that_stops_early_after_a_failed_import_keeps_what_was_not_imported(
    box: Box,
) -> None:
    """Fix round 7: `forward()` failed on its first table, then the rollback stopped early.

    `back()` never ran, so the new build stays on the old table: the record keeps the
    name it did not import and every start refuses -- `keep()` must not clear it.
    """
    box.changes(("M", f"{REPO_SQL}/world/creature.sql"))
    box.moves_to({"creature.sql": "DROP TABLE IF EXISTS creature; -- new\n"})
    box.m.db.fail_on = "creature"
    _refuse_the_failed_name(box)
    with pytest.raises(RollbackNotDone) as failed:
        box.press()
    assert "could not be given a name to undo onto" in str(failed.value)
    assert box.pending() == {"version": 1, "reimport": [f"{WORLD_SQL}/creature.sql"], "parts": []}
    assert box.head() == NEW
    assert box.engine().start_refusal(box.server_dir) == UNFINISHED
    with pytest.raises(StartRefused):
        CenturionController(ENTRY, box.server_dir).refuse_start()


def _docker_gone_after_the_compile(box: Box) -> None:
    """Docker answers until the compile is done, then not: the recreate replaces nothing."""
    box.seams["docker_ready"] = lambda: "build" not in box.m.rec.calls


def test_a_rollback_that_stops_early_before_the_servers_stopped_leaves_the_tables_waiting(
    box: Box,
) -> None:
    """T197: nothing was imported, and the new build stays, so its tables are what waits.

    The recreate refused before `prepare()` ran, so `keep()` writes the record and
    flags the map data itself: the new build must not start on the old tables.
    """
    box.changes(("M", f"{REPO_SQL}/world/creature.sql"), ("M", "centurion/dbc/Spell.dbc"))
    _docker_gone_after_the_compile(box)
    _refuse_the_failed_name(box)
    with pytest.raises(RollbackNotDone) as failed:
        box.press()
    assert "stop_servers" not in box.m.rec.calls, "the ground: the servers were never stopped"
    assert box.streamed() == []
    assert box.pending() == {"version": 1, "reimport": [f"{WORLD_SQL}/creature.sql"], "parts": []}
    assert needs_reextract(box.server_dir, ENTRY) is not None
    assert box.head() == NEW
    assert box.engine().start_refusal(box.server_dir) == UNFINISHED
    assert box.world.running is True, "the build from before still runs in its containers"
    assert str(failed.value) == (
        "Docker is not answering, so the containers were not replaced -- the server you have "
        "is still the one that was running before this rebuild. Nothing was touched. Check the "
        "docker daemon is up, then press the same entry under “Server build ▾” on the Modules "
        "tab again. Putting the build from before this rebuild back was not attempted, because "
        "the new build could not be given a name to undo onto (read-only layer store); the tags "
        "still name the new build, all of them. The old images are on the daemon under their "
        "-rollback tags. The source folders were left on the new commits, because the image "
        "tags name the new build made from them. None of your server's containers was "
        "replaced, so it is still running the build from before this update if it is up, and "
        f"Start is refused until this is done: {UNFINISHED}"
    )
    assert "next Start runs" not in str(failed.value)
    assert failed.value.touched is False


def test_a_record_the_kept_build_cannot_write_is_said_and_the_sources_still_stay(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T197: a folder that takes no record at all: said, never in place of the sentence.

    Both the plan's own write and the fallback from the names fail, so nothing can
    refuse a start; the sentence says so rather than claiming a refusal that is not there.
    """
    box.changes(("M", f"{REPO_SQL}/world/creature.sql"), ("M", "centurion/dbc/Spell.dbc"))
    _docker_gone_after_the_compile(box)
    _refuse_the_failed_name(box)
    real_write = Path.write_text

    def refuse(self: Path, *args: object, **kwargs: object) -> int:
        if self.name.startswith(trinitycore.WORLD_REIMPORT_FILE):
            raise PermissionError(13, "Permission denied")
        return real_write(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "write_text", refuse)
    with pytest.raises(RollbackNotDone) as failed:
        box.press()
    said = str(failed.value)
    assert "could not be given a name to undo onto" in said
    assert "Permission denied" in said
    assert "so nothing stops this server starting its new build on the old world tables." in said
    assert said.endswith(
        native.SOURCES_LEFT_UNTOUCHED_NOTE
    ), "nothing refuses, so it says Start runs it"
    assert box.head() == NEW
    assert box.pending() is None, "the ground: no world record could be written"
    assert needs_reextract(box.server_dir, ENTRY) is not None, "the flag went first"


def _plan_fails_once(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """`_reimport_runs()` refuses the FIRST time it is asked (keep's `prepare()`), then works.

    The record cannot come from the plan then, so `keep()` writes it from the names the
    move read; a later "Finish the world update" expands the plan as it always does.
    """
    asked: list[int] = []
    real = trinitycore.TrinityCoreInstaller._reimport_runs

    def runs(self: trinitycore.TrinityCoreInstaller, *args: object, **kwargs: object) -> object:
        asked.append(1)
        if len(asked) == 1:
            raise InstallerError("the plan could not be expanded")
        return real(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(trinitycore.TrinityCoreInstaller, "_reimport_runs", runs)
    return asked


def _kept_without_its_tables(box: Box, monkeypatch: pytest.MonkeyPatch) -> str:
    """An update whose rollback stopped early, untouched, and whose `prepare()` then failed."""
    box.changes(("M", f"{REPO_SQL}/world/creature.sql"))
    box.moves_to({"creature.sql": "DROP TABLE IF EXISTS creature; -- new\n"})
    _docker_gone_after_the_compile(box)
    _refuse_the_failed_name(box)
    _plan_fails_once(monkeypatch)
    with pytest.raises(RollbackNotDone) as failed:
        box.press()
    box.seams.clear()  # Docker answers again, and tags as it should
    return str(failed.value)


def test_a_kept_build_whose_plan_fails_still_records_its_tables_and_refuses_start(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix round 3: T179's own record names what the kept build needs, from the move's names.

    A controller made fresh over the folder -- the app restarted -- refuses, naming the
    press that finishes it, and the press's sentence says the same.
    """
    said = _kept_without_its_tables(box, monkeypatch)
    assert "the plan could not be expanded" in said
    assert box.streamed() == []
    assert box.pending() == {
        "version": 1,
        "reimport": [f"{WORLD_SQL}/creature.sql"],
        "parts": [],
        "required": [f"{WORLD_SQL}/creature.sql"],
    }
    assert said.endswith(f"Start is refused until this is done: {UNFINISHED}")
    with pytest.raises(StartRefused) as refused:
        CenturionController(ENTRY, box.server_dir).refuse_start()
    assert str(refused.value) == UNFINISHED


def test_finishing_the_kept_builds_world_update_imports_its_tables_and_clears_it(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    _kept_without_its_tables(box, monkeypatch)
    box.finish()
    assert first_lines(box) == ["DROP TABLE IF EXISTS creature; -- new"], "from the kept checkout"
    assert box.pending() is None
    CenturionController(ENTRY, box.server_dir).refuse_start()


def test_a_finish_that_fails_keeps_the_kept_builds_record(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    _kept_without_its_tables(box, monkeypatch)
    box.m.db.fail_on = "creature"
    with pytest.raises(InstallerError):
        box.finish()
    assert box.pending() == {
        "version": 1,
        "reimport": [f"{WORLD_SQL}/creature.sql"],
        "parts": [],
        "required": [f"{WORLD_SQL}/creature.sql"],
    }
    with pytest.raises(StartRefused):
        CenturionController(ENTRY, box.server_dir).refuse_start()


def test_an_unrelated_update_folds_the_kept_builds_tables_in_and_clears_only_once_in(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix round 3: a later update that changes another table imports the owed one too.

    The record goes only once every file it names went in; the update first fails on the
    owed table, and the record survives that, then the next one lands.
    """
    _kept_without_its_tables(box, monkeypatch)
    box.m.rec.heads[box.checkout] = NEW
    box.m.rec.upstream[box.checkout] = "c" * 40
    box.changes(("M", f"{REPO_SQL}/world/version.sql"), old=NEW, new="c" * 40)
    box.m.db.fail_on = "creature"
    box.ready = [True]
    with pytest.raises(InstallerError):
        box.press()
    assert f"{WORLD_SQL}/creature.sql" in cast(list[str], (box.pending() or {})["reimport"])
    with pytest.raises(StartRefused):
        CenturionController(ENTRY, box.server_dir).refuse_start()

    box.m.db.fail_on = ""
    box.m.db.streams.clear()
    box.m.rec.heads[box.checkout] = NEW
    said = box.press()
    assert said[-1] == "Centurion is running on the newest upstream code."
    assert set(first_lines(box)) >= {
        "DROP TABLE IF EXISTS creature; -- new",
        "DROP TABLE IF EXISTS version;",
    }, "the owed table went in with the update's own"
    assert box.pending() is None
    CenturionController(ENTRY, box.server_dir).refuse_start()


ARENA = f"{WORLD_SQL}/arena_season.sql"
NEEDED_AND_MISSING = (
    f"{ARENA} is not among the world table files Centurion's import reads from its sources, "
    "and the build this server was left on needs that table, so the world update was not "
    "finished and the server is still refused a start. Nothing was imported. Put it back in "
    "the sources and press “Finish the world update” again, or press “Update the server to "
    "latest…” or “Return to the tested pin…” under “Server build ▾” on the Modules tab, "
    "which builds a new version in place of the one this server was left on."
)


def _kept_with_a_table_missing(box: Box) -> None:
    """A kept build whose update added a world table file the checkout does not have."""
    box.changes(("A", f"{REPO_SQL}/world/arena_season.sql"))
    _docker_gone_after_the_compile(box)
    _refuse_the_failed_name(box)
    with pytest.raises(RollbackNotDone) as failed:
        box.press()
    assert "are not all in the server's sources" in str(failed.value)
    box.seams.clear()
    assert box.pending() == {"version": 1, "reimport": [ARENA], "parts": [], "required": [ARENA]}


def test_finish_will_not_leave_out_a_table_the_kept_build_needs(box: Box) -> None:
    """Fix rounds 4-5: a missing file the kept build needs is never "left": the record stays."""
    _kept_with_a_table_missing(box)
    with pytest.raises(InstallerError) as refused:
        box.finish()
    assert str(refused.value) == NEEDED_AND_MISSING
    assert box.streamed() == []
    assert box.pending() == {"version": 1, "reimport": [ARENA], "parts": [], "required": [ARENA]}
    with pytest.raises(StartRefused):
        CenturionController(ENTRY, box.server_dir).refuse_start()


def test_finish_will_not_leave_out_a_needed_file_the_import_plan_does_not_read(box: Box) -> None:
    """Fix round 5: on disk is not enough; the plan `_expand` returns must read it too."""
    on_the_built_commit(box)
    outside = f"{SQL_DIR}/characters/not_a_world_table.sql"
    (box.server_dir / outside).parent.mkdir(parents=True, exist_ok=True)
    (box.server_dir / outside).write_text("SELECT 1;\n", encoding="utf-8")
    (box.server_dir / trinitycore.WORLD_REIMPORT_FILE).write_text(
        json.dumps({"version": 1, "reimport": [outside], "parts": [], "required": [outside]}),
        encoding="utf-8",
    )
    with pytest.raises(InstallerError, match="not_a_world_table.sql is not among"):
        box.finish()
    assert box.streamed() == []
    assert box.pending() is not None, "the record stays"


def _kept_with_a_new_table(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    """Build B added `arena_season.sql` (on disk) and was kept without its tables imported."""
    box.changes(("A", f"{REPO_SQL}/world/arena_season.sql"))
    box.moves_to({"arena_season.sql": "DROP TABLE IF EXISTS arena_season;\n"})
    _docker_gone_after_the_compile(box)
    _refuse_the_failed_name(box)
    _plan_fails_once(monkeypatch)
    with pytest.raises(RollbackNotDone):
        box.press()
    box.seams.clear()
    assert box.pending() == {"version": 1, "reimport": [ARENA], "parts": [], "required": [ARENA]}

    def gone(dest: Path) -> None:
        (dest / REPO_SQL / "world" / "arena_season.sql").unlink(missing_ok=True)

    box.m.rec.on_clone = gone  # the next move's commit has no such file


def test_an_update_whose_commit_deleted_the_kept_builds_table_lands_and_clears_it(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix round 5: no dead end -- the new build replaces the kept one, so its file is excused."""
    _kept_with_a_new_table(box, monkeypatch)
    box.m.rec.upstream[box.checkout] = "c" * 40
    box.changes(("D", f"{REPO_SQL}/world/arena_season.sql"), old=NEW, new="c" * 40)
    said = box.press()
    assert said[-1] == "Centurion is running on the newest upstream code."
    assert box.pending() is None
    CenturionController(ENTRY, box.server_dir).refuse_start()


def test_a_return_to_a_pin_without_the_kept_builds_table_lands_and_clears_it(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    _kept_with_a_new_table(box, monkeypatch)
    box.changes(("D", f"{REPO_SQL}/world/arena_season.sql"), old=NEW, new=REV)
    said = box.press(to_pin=True)
    assert said[-1] == "Centurion is running on the commit this app was tested against."
    assert box.pending() is None
    CenturionController(ENTRY, box.server_dir).refuse_start()


def test_a_failed_update_after_an_excused_move_puts_the_kept_builds_record_back(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix round 6: C excuses B's owed table, then fails its ready wait and B is put back.

    The record is not removed until C is up, and the rollback puts it back as it was:
    B still needs its table, so every start refuses and the sentence says why.
    """
    _kept_with_a_new_table(box, monkeypatch)
    box.m.rec.upstream[box.checkout] = "c" * 40
    box.changes(("D", f"{REPO_SQL}/world/arena_season.sql"), old=NEW, new="c" * 40)
    box.ready = [False, True]
    with pytest.raises(InstallerError) as failed:
        box.press()
    said = str(failed.value)
    assert box.pending() == {"version": 1, "reimport": [ARENA], "parts": [], "required": [ARENA]}
    assert box.head() == NEW, "the kept build's sources are back"
    assert f"its servers were left STOPPED: {UNFINISHED}" in said
    assert box.world.running is False
    with pytest.raises(StartRefused):
        CenturionController(ENTRY, box.server_dir).refuse_start()


def test_a_failed_update_after_a_partly_excused_move_keeps_what_the_old_build_still_owes(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B owes two tables; C deletes one, keeps the other. C fails; the rollback imports the
    one C kept from B's checkout and the excused one stays owed."""
    box.changes(
        ("A", f"{REPO_SQL}/world/arena_season.sql"), ("M", f"{REPO_SQL}/world/creature.sql")
    )
    box.moves_to(
        {
            "arena_season.sql": "DROP TABLE IF EXISTS arena_season;\n",
            "creature.sql": "DROP TABLE IF EXISTS creature; -- b\n",
        }
    )
    _docker_gone_after_the_compile(box)
    _refuse_the_failed_name(box)
    _plan_fails_once(monkeypatch)
    with pytest.raises(RollbackNotDone):
        box.press()
    box.seams.clear()
    owed = sorted([ARENA, f"{WORLD_SQL}/creature.sql"])
    assert box.pending() == {"version": 1, "reimport": owed, "parts": [], "required": owed}

    def gone(dest: Path) -> None:
        (dest / REPO_SQL / "world" / "arena_season.sql").unlink(missing_ok=True)

    box.m.rec.on_clone = gone
    box.m.rec.upstream[box.checkout] = "c" * 40
    box.changes(("D", f"{REPO_SQL}/world/arena_season.sql"), old=NEW, new="c" * 40)
    box.ready = [False, True]
    with pytest.raises(InstallerError):
        box.press()
    assert box.pending() == {"version": 1, "reimport": [ARENA], "parts": [], "required": [ARENA]}
    with pytest.raises(StartRefused):
        CenturionController(ENTRY, box.server_dir).refuse_start()


def test_a_needed_file_the_new_commit_still_has_is_not_excused_by_a_missing_disk_copy(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix round 6: excused only when the move's commit deleted it, never by the disk alone."""
    _kept_with_a_new_table(box, monkeypatch)  # the next move's checkout lacks the file
    box.m.rec.upstream[box.checkout] = "c" * 40
    box.changes(("M", f"{REPO_SQL}/world/version.sql"), old=NEW, new="c" * 40)
    with pytest.raises(InstallerError) as refused:
        box.press()
    assert f"are not all in the server's sources ({ARENA})" in str(refused.value)
    assert box.pending() == {"version": 1, "reimport": [ARENA], "parts": [], "required": [ARENA]}
    assert box.head() == NEW


def test_a_rebuild_is_refused_while_a_kept_builds_tables_are_owed(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix round 6: a plain Rebuild ends in a start and imports no table: refused first."""
    _kept_with_a_new_table(box, monkeypatch)
    builds = box.m.rec.calls.count("build")
    with pytest.raises(InstallerError) as refused:
        list(box.engine().rebuild(InstallOptions(server_dir=box.server_dir)))
    assert str(refused.value) == f"{UNFINISHED} Nothing was changed."
    assert box.m.rec.calls.count("build") == builds


LOCALE = f"{WORLD_SQL}/broadcast_text_locale"
LOCALE_PARTS = ("broadcast_text_locale.1.sql", "broadcast_text_locale.2.sql")


def _kept_with_a_split_table(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    """Build B changed a split table and was kept without its tables imported (fix round 7)."""
    second = box.checkout / REPO_SQL / "world" / LOCALE_PARTS[1]
    second.write_text("INSERT INTO broadcast_text_locale VALUES (2);\n", encoding="utf-8")
    box.changes(("M", f"{REPO_SQL}/world/broadcast_text_locale.2.sql"))
    _docker_gone_after_the_compile(box)
    _refuse_the_failed_name(box)
    _plan_fails_once(monkeypatch)
    with pytest.raises(RollbackNotDone):
        box.press()
    box.seams.clear()
    assert box.pending() == {"version": 1, "reimport": [], "parts": [LOCALE], "required": [LOCALE]}


def _without_the_parts(folder: Path) -> None:
    for name in LOCALE_PARTS:
        (folder / REPO_SQL / "world" / name).unlink(missing_ok=True)


def test_a_needed_split_table_the_new_commit_still_has_is_not_excused_by_the_disk(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix round 7: round 6's rule for split tables -- missing on disk alone excuses nothing."""
    _kept_with_a_split_table(box, monkeypatch)
    box.m.rec.on_clone = _without_the_parts  # the checkout lacks them; the commit kept them
    box.m.rec.upstream[box.checkout] = "c" * 40
    box.changes(("M", f"{REPO_SQL}/world/version.sql"), old=NEW, new="c" * 40)
    with pytest.raises(InstallerError) as refused:
        box.press()
    assert f"are not all in the server's sources ({LOCALE}.*.sql)" in str(refused.value)
    assert box.pending() == {"version": 1, "reimport": [], "parts": [LOCALE], "required": [LOCALE]}
    assert box.head() == NEW
    with pytest.raises(StartRefused):
        CenturionController(ENTRY, box.server_dir).refuse_start()


def test_an_update_whose_commit_deleted_the_kept_builds_split_table_lands_and_clears_it(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    _kept_with_a_split_table(box, monkeypatch)
    box.m.rec.on_clone = _without_the_parts
    box.m.rec.upstream[box.checkout] = "c" * 40
    box.changes(
        *(("D", f"{REPO_SQL}/world/{name}") for name in LOCALE_PARTS), old=NEW, new="c" * 40
    )
    said = box.press()
    assert said[-1] == "Centurion is running on the newest upstream code."
    assert box.pending() is None
    CenturionController(ENTRY, box.server_dir).refuse_start()


def test_finish_will_not_leave_out_a_split_table_the_kept_build_needs(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    _kept_with_a_split_table(box, monkeypatch)
    _without_the_parts(box.checkout)
    with pytest.raises(InstallerError) as refused:
        box.finish()
    assert str(refused.value).startswith(
        f"{LOCALE}.*.sql is not among the world table files Centurion's import reads"
    )
    assert box.streamed() == []
    assert box.pending() == {"version": 1, "reimport": [], "parts": [LOCALE], "required": [LOCALE]}


def test_finishing_the_kept_builds_split_table_imports_it_whole_and_clears_it(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    _kept_with_a_split_table(box, monkeypatch)
    box.finish()
    assert first_lines(box) == [
        "DROP TABLE IF EXISTS broadcast_text_locale;",
        "INSERT INTO broadcast_text_locale VALUES (2);",
    ]
    assert box.pending() is None
    CenturionController(ENTRY, box.server_dir).refuse_start()


def test_a_failed_update_that_excused_a_split_table_keeps_it_owed_after_the_rollback(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix round 7: B owes a split table and a table; C deletes the split table and fails.

    The rollback imports the table from B's checkout and takes it off the record; the
    split table stays owed, still marked as one B needs.
    """
    second = box.checkout / REPO_SQL / "world" / LOCALE_PARTS[1]
    second.write_text("INSERT INTO broadcast_text_locale VALUES (2);\n", encoding="utf-8")
    box.changes(
        ("M", f"{REPO_SQL}/world/creature.sql"),
        ("M", f"{REPO_SQL}/world/broadcast_text_locale.2.sql"),
    )
    box.moves_to({"creature.sql": "DROP TABLE IF EXISTS creature; -- b\n"})
    _docker_gone_after_the_compile(box)
    _refuse_the_failed_name(box)
    _plan_fails_once(monkeypatch)
    with pytest.raises(RollbackNotDone):
        box.press()
    box.seams.clear()
    creature = f"{WORLD_SQL}/creature.sql"
    assert box.pending() == {
        "version": 1,
        "reimport": [creature],
        "parts": [LOCALE],
        "required": [LOCALE, creature],
    }

    box.m.rec.on_clone = _without_the_parts
    box.m.rec.upstream[box.checkout] = "c" * 40
    box.changes(
        *(("D", f"{REPO_SQL}/world/{name}") for name in LOCALE_PARTS), old=NEW, new="c" * 40
    )
    box.ready = [False, True]
    with pytest.raises(InstallerError):
        box.press()
    assert box.pending() == {"version": 1, "reimport": [], "parts": [LOCALE], "required": [LOCALE]}
    with pytest.raises(StartRefused):
        CenturionController(ENTRY, box.server_dir).refuse_start()


def test_a_new_table_the_old_build_never_had_is_not_owed_after_the_rollback(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix round 6: the rollback puts back the record as it was BEFORE the press.

    C adds a table B never had and fails; the rollback must not leave B owing it (it
    would refuse every start for a file B's checkout does not have). What B owed went in
    from B's own checkout during the rollback, so nothing is owed at all.
    """
    _kept_with_a_new_table(box, monkeypatch)
    box.m.rec.on_clone = None
    team = f"{REPO_SQL}/world/arena_team.sql"
    box.moves_to({"arena_team.sql": "DROP TABLE IF EXISTS arena_team;\n"})
    real_restore = box.m.rec.restore_rev

    def restore(dest: Path, rev: str) -> None:
        real_restore(dest, rev)
        (dest / team.removeprefix(f"{CHECKOUT}/")).unlink(missing_ok=True)
        (box.server_dir / team).unlink(missing_ok=True)

    monkeypatch.setattr(box.m.rec, "restore_rev", restore)
    box.m.rec.upstream[box.checkout] = "c" * 40
    box.changes(("A", team), old=NEW, new="c" * 40)
    box.ready = [False, True]
    with pytest.raises(InstallerError) as failed:
        box.press()
    assert first_lines(box)[-1] == "DROP TABLE IF EXISTS arena_season;", "B's own, from B"
    assert box.pending() is None, f"{team} is not owed by the build put back"
    assert "put back and is running again" in str(failed.value)


def test_the_record_stays_until_the_new_build_is_up_even_when_the_press_dies(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix round 6: `forward()` imported everything, then the press died before any ready
    wait. Nothing confirmed the new build up, so what was owed is still recorded."""
    _kept_with_a_new_table(box, monkeypatch)
    box.m.rec.on_clone = None
    box.m.rec.upstream[box.checkout] = "c" * 40
    box.changes(("M", f"{REPO_SQL}/world/version.sql"), old=NEW, new="c" * 40)

    def boom(*_args: object, **_kwargs: object) -> bool:
        raise RuntimeError("a bug in the recreate")

    box.world.recreate = boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="a bug in the recreate"):
        box.press()
    assert first_lines(box)[-2:] == [
        "DROP TABLE IF EXISTS arena_season;",
        "DROP TABLE IF EXISTS version;",
    ], "the ground: forward() imported every table"
    pending = box.pending()
    assert pending is not None and ARENA in cast(list[str], pending["reimport"])
