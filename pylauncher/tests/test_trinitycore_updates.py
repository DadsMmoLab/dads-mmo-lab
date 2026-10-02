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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

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
    engine,
    install,
    known_password,
    machine,
)
from yulon import docker
from yulon.catalog import native
from yulon.catalog.families import extract, mmaps, trinitycore
from yulon.catalog.families.trinitycore import needs_reextract
from yulon.catalog.installer import InstallerError, InstallOptions
from yulon.install_wiring import reextract_for_app, update_to_latest_for_app

OLD = "a" * 40
NEW = "b" * 40
WORLD_SQL = f"{SQL_DIR}/world"
"""The world tables' folder, server-dir-relative, as the plan's globs spell it."""
REPO_SQL = "centurion/sql"
"""The same folder as git names it: relative to the checkout."""
MIN_FILES = 500


@dataclass
class World:
    """The world server's container: up after the rebuild, down after a stop, up after a start."""

    calls: list[str]
    running: bool | None = True
    refuse_stop: str = ""

    def ask(self, container: str) -> bool | None:
        return self.running

    def stop(self, containers: Sequence[str], **_kwargs: object) -> None:
        if self.refuse_stop:
            raise docker.DockerCommandError(self.refuse_stop)
        self.calls.append(f"stop-world:{','.join(containers)}")
        self.running = False

    def start(self, spec: docker.ContainerSpec, server_dir: Path) -> bool:
        self.calls.append("start")
        self.running = True
        return True


@dataclass
class Box:
    """An installed Centurion server whose checkout is on `OLD`, with `NEW` upstream."""

    m: Machine
    world: World
    sql: list[str] = field(default_factory=list)

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

        return engine(
            self.m,
            world_running=self.world.ask,
            stop_world=self.world.stop,
            start=self.world.start,
            exec_stdin=exec_stdin,
        )

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


# -- the world tables: re-imported after the rebuild, only those, as root ------------------


def test_only_the_changed_world_tables_are_imported_again_as_root_after_the_rebuild(
    box: Box,
) -> None:
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
    calls = box.m.rec.calls
    order = [c for c in calls if c in ("build", "sql", "start") or c.startswith("stop-world")]
    assert order == [
        "build",
        f"stop-world:{ENTRY.containers.world}",
        "sql",
        "sql",
        "start",
    ], "after the rebuild, with the world server stopped, and started again after"
    assert said[-1] == "Centurion is running on the newest upstream code."
    assert any("2 world tables" in line for line in said)


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


def test_a_world_import_that_fails_leaves_the_world_stopped_and_names_what_was_not_imported(
    box: Box,
) -> None:
    box.changes(("M", f"{REPO_SQL}/world/creature.sql"), ("M", f"{REPO_SQL}/world/version.sql"))
    box.m.db.fail_on = "creature"
    with pytest.raises(InstallerError) as failed:
        box.press()
    said = str(failed.value)
    assert f"{WORLD_SQL}/creature.sql" in said and f"{WORLD_SQL}/version.sql" in said
    assert "world server was left stopped" in said
    assert "backup" in said
    assert box.world.running is False
    assert box.head() == NEW, "the build is the new one; its sources stay with it"


def test_a_world_server_that_cannot_be_stopped_imports_nothing(box: Box) -> None:
    box.changes(("M", f"{REPO_SQL}/world/creature.sql"))
    box.world.refuse_stop = "daemon not answering"
    with pytest.raises(InstallerError, match="could not stop") as failed:
        box.press()
    assert box.streamed() == []
    assert f"{WORLD_SQL}/creature.sql" in str(failed.value)


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
    flagged(box)
    fake: FakeMmapsDocker = box.m.mmaps
    fake.finish(0, tiles=MIN_FILES)
    assert box.engine().mmaps_status(box.server_dir).state == "done"
    assert "mmap.enablePathFinding = 1" in world_conf(box)
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
