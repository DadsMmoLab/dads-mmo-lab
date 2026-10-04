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
from yulon.catalog.installer import InstallerError, InstallOptions, WorldStoppedAfterReadyError
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


def test_servers_that_cannot_be_stopped_import_nothing_and_leave_the_record_as_it_was(
    box: Box,
) -> None:
    """Fix round 2: nothing went in, so nothing more is waiting than before the press."""
    box.leave_pending([f"{WORLD_SQL}/version.sql"])
    before = box.pending()
    box.changes(("M", f"{REPO_SQL}/world/creature.sql"))
    box.world.refuse_stop = "daemon not answering"
    with pytest.raises(InstallerError, match="could not be stopped"):
        box.press()
    assert box.streamed() == []
    assert box.pending() == before


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
    """No update route, no work between the stop and the start: the rebuild as it always was."""
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


def test_reextract_stops_a_running_job_and_says_its_tiles_were_removed(box: Box) -> None:
    """T209: a stop for the extraction never keeps tiles, and never says it did."""
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
        "Stopped making the pathfinding data before the extraction; what it had made so far "
        "was removed and pathfinding stays off. It starts again from the beginning once the "
        "server has been rebuilt, or from the Server tab."
    ) in said
    assert not [line for line in said if "tiles are kept" in line]
    assert len(fake.mmaps_at_run[-1]) == 0, "the new run starts from an empty folder"


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
