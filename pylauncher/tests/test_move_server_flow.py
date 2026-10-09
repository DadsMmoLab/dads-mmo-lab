"""T601 level 2: packing a whole server through the real engine, and building it again from it.

The maintenance engine is real (backup, plan_restore, restore with T217's drop); the database
container, Docker, the install engine, the module applier and the rebuild are doubles. So the
order asserted here (install, rows, answers, modules, one rebuild, confs, data, fix-ups) is the
move's own, and every refusal comes from the move's own guard.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path

import pytest

from tests.test_move_flows import AT, Box, Db, entry
from yulon import apply, move, move_flows, move_server
from yulon.catalog import native
from yulon.catalog.catalog import load_catalog
from yulon.catalog.installer import InstallOptions
from yulon.manifest import Manifest
from yulon.manifest_store import ManifestStore
from yulon.move import PackedModule, PackedSource, PackFile, ServerFacts, ServerSpec
from yulon.move_flows import MoveError
from yulon.resources import manifests_dir

CATALOG = load_catalog()
WOTLK = CATALOG.get("wow-wotlk")
STORE = ManifestStore(manifests_dir(), "wow-wotlk")
OLD_CORE = "a" * 40
TRANSMOG = "c" * 40
PIN_CORE = WOTLK.emulator.sources[0].rev or ""
PIN_BOTS = WOTLK.emulator.sources[1].rev or ""

CONF = (
    b'LoginDatabaseInfo = "old-db;3306;root;{{DB_PASSWORD}};acore_auth"\r\n' b"Rate.XP.Kill = 3\r\n"
)


def whole_facts() -> ServerFacts:
    return ServerFacts(
        spec=ServerSpec(
            sources=(
                PackedSource(
                    repo="mod-playerbots/azerothcore-wotlk",
                    dest=".",
                    commit=OLD_CORE,
                    catalog_pin=PIN_CORE,
                ),
                PackedSource(
                    repo="mod-playerbots/mod-playerbots",
                    dest="modules/mod-playerbots",
                    commit=PIN_BOTS,
                    catalog_pin=PIN_BOTS,
                ),
            ),
            modules=(
                PackedModule(
                    type="module",
                    id="mod-transmog",
                    origin="catalog",
                    repo=STORE.load("module", "mod-transmog").source.repo,  # type: ignore[union-attr]
                    commit=TRANSMOG,
                ),
            ),
        ),
        files=(
            PackFile(kind="conf", target="env/dist/etc/worldserver.conf", data=CONF),
            PackFile(
                kind="answers",
                target=".yulon-module-answers.json",
                data=b'{"version": 1, "modules": {"module/mod-transmog": {"X": "7"}}}',
            ),
            PackFile(
                kind="lua", target="env/dist/etc/modules/lua_scripts/mine.lua", data=b"-- mine\n"
            ),
        ),
    )


def whole_package(tmp_path: Path, realm: str = "Old Realm") -> Path:
    root = tmp_path / "old-computer"
    root.mkdir(parents=True)
    source = Box(root, running_now=("ac-database",), db=Db([], realm=realm))
    folder = tmp_path / "docs"
    folder.mkdir()
    return move_flows.export_package(
        source.world, folder, stop_allowed=False, whole=whole_facts
    ).path


# =============================================================== the pack


def test_a_whole_server_pack_holds_the_world_and_keeps_the_realm_row(tmp_path: Path) -> None:
    root = tmp_path / "old"
    root.mkdir()
    box = Box(root, running_now=("ac-database",))
    folder = tmp_path / "docs"
    folder.mkdir()
    result = move_flows.export_package(box.world, folder, stop_allowed=False, whole=whole_facts)
    m = move.read_package(result.path).manifest
    assert [d.schema_name for d in m.databases] == ["acore_auth", "acore_characters", "acore_world"]
    assert m.kind == "server"
    assert box.db.ignored["acore_auth"] == ()
    assert result.path.name == "yulon-move-server-wow-wotlk-20261009-1530-keep-private.zip"
    assert m.excluded == ()
    assert "Packed the whole server" in result.text()


def test_the_logs_database_is_left_out_of_a_whole_server(tmp_path: Path) -> None:
    root = tmp_path / "old"
    root.mkdir()
    db = Db([], present=("realmd", "characters", "mangos", "logs"), version_table="updates")
    box = Box(root, "wow-tbc", running_now=(entry("wow-tbc").container_spec().db,), db=db)
    folder = tmp_path / "docs"
    folder.mkdir()

    def tbc_facts() -> ServerFacts:
        return ServerFacts(
            spec=ServerSpec(
                sources=tuple(
                    PackedSource(repo=s.repo, dest=s.dest, commit=s.rev or "", catalog_pin=s.rev)
                    for s in entry("wow-tbc").emulator.sources
                )
            ),
            files=(),
        )

    result = move_flows.export_package(box.world, folder, stop_allowed=False, whole=tbc_facts)
    assert [d.schema_name for d in result.manifest.databases] == ["realmd", "characters", "mangos"]
    assert result.manifest.excluded == ("logs (logs)",)


def test_a_refusal_of_the_facts_stops_nothing(tmp_path: Path) -> None:
    root = tmp_path / "old"
    root.mkdir()
    box = Box(root, running_now=("ac-database", "ac-authserver", "ac-worldserver"))

    def refused() -> ServerFacts:
        raise MoveError("a folder module")

    with pytest.raises(MoveError, match="a folder module"):
        move_flows.export_package(box.world, tmp_path, stop_allowed=True, whole=refused)
    assert "stop" not in box.events


def test_a_whole_server_file_is_refused_by_the_accounts_import(tmp_path: Path) -> None:
    path = whole_package(tmp_path)
    target = Box(tmp_path, running_now=("ac-database",))
    plan = move_flows.plan_import(target.world, path)
    assert plan.refusals == (move_flows.WHOLE_SERVER_FILE,)
    assert "db-up" not in target.events


# =============================================================== the plan


def lookup(store: ManifestStore = STORE) -> move_server.ModuleLookup:
    def load(kind: str, item_id: str) -> Manifest | None:
        try:
            return store.load(kind, item_id)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001
            return None

    return move_server.ModuleLookup(
        load=load, shipped=lambda kind, item_id: item_id in store.load_index(kind).items  # type: ignore[arg-type]
    )


def plan_for(
    path: Path,
    server_dir: Path,
    *,
    platform_id: str = "linux",
    known: Mapping[str, bool | None] | None = None,
) -> move_server.ServerImportPlan:
    asked: list[tuple[str, str]] = []

    def commit_known(repo: str, sha: str) -> bool | None:
        asked.append((repo, sha))
        return (known or {}).get(sha)

    plan = move_server.plan_server_import(
        path,
        catalog=CATALOG,
        server_dir_for=lambda _entry: server_dir,
        platform_id=platform_id,
        lookup_for=lambda _entry: lookup(),
        commit_known=commit_known,
    )
    plan_for.asked = asked  # type: ignore[attr-defined]
    return plan


def test_the_plan_pins_the_entry_and_the_modules_at_the_packed_commits(tmp_path: Path) -> None:
    plan = plan_for(whole_package(tmp_path), tmp_path / "new")
    assert plan.allowed, plan.refusals
    assert plan.pinned is not None
    assert plan.pinned.emulator.sources[0].rev == OLD_CORE
    assert [(t.manifest.id, t.manifest.source.rev) for t in plan.modules] == [  # type: ignore[union-attr]
        ("mod-transmog", TRANSMOG)
    ]
    assert plan.modules[0].manifest.source.follow == "branch"  # type: ignore[union-attr]
    assert "not tested with: azerothcore-wotlk aaaaaaa" in plan.text()


def test_a_characters_file_on_the_tile_says_where_it_goes(tmp_path: Path) -> None:
    from tests.test_move_flows import packed

    plan = plan_for(packed(tmp_path), tmp_path / "new")
    assert plan.refusals == (move_server.KIND_CHARACTERS,)


def test_a_folder_holding_a_server_is_never_a_target(tmp_path: Path) -> None:
    folder = tmp_path / "existing"
    folder.mkdir()
    (folder / native.STATE_FILE).write_text("{}", encoding="utf-8")
    plan = plan_for(whole_package(tmp_path), folder)
    assert plan.refusals == (move_server.holds_a_server(folder),)


def test_a_folder_with_files_is_refused(tmp_path: Path) -> None:
    folder = tmp_path / "busy"
    folder.mkdir()
    (folder / "notes.txt").write_text("mine", encoding="utf-8")
    assert plan_for(whole_package(tmp_path), folder).refusals == (move_server.not_empty(folder),)


def test_a_folder_of_this_same_move_carries_on(tmp_path: Path) -> None:
    path = whole_package(tmp_path)
    folder = tmp_path / "new"
    folder.mkdir()
    (folder / native.STATE_FILE).write_text("{}", encoding="utf-8")
    digest = move_server.package_digest(move.read_package(path).manifest)
    move_server.write_marker(folder, digest, ["revs"])
    plan = plan_for(path, folder)
    assert plan.allowed and plan.resuming


def test_another_packages_unfinished_move_is_refused(tmp_path: Path) -> None:
    folder = tmp_path / "new"
    folder.mkdir()
    (folder / native.STATE_FILE).write_text("{}", encoding="utf-8")
    move_server.write_marker(folder, "f" * 64, [])
    plan = plan_for(whole_package(tmp_path), folder)
    assert plan.refusals == (move_server.holds_a_server(folder),)


def test_a_commit_gone_from_github_is_refused_by_name(tmp_path: Path) -> None:
    plan = plan_for(whole_package(tmp_path), tmp_path / "new", known={OLD_CORE: False})
    assert plan.refusals == (
        "The commit this server was built from (aaaaaaa of mod-playerbots/azerothcore-wotlk) is "
        "no longer on GitHub, so it cannot be built again exactly. Install WoW WotLK fresh and "
        "bring the accounts and characters in instead (Pack accounts and characters… on the old "
        "computer).",
    )


def test_github_not_answering_is_not_a_refusal(tmp_path: Path) -> None:
    plan = plan_for(whole_package(tmp_path), tmp_path / "new", known={OLD_CORE: None})
    assert plan.allowed
    assert (("mod-playerbots/azerothcore-wotlk", OLD_CORE)) in plan_for.asked  # type: ignore[attr-defined]


def test_a_pinned_source_is_not_asked_of_github(tmp_path: Path) -> None:
    plan_for(whole_package(tmp_path), tmp_path / "new")
    assert ("mod-playerbots/mod-playerbots", PIN_BOTS) not in plan_for.asked  # type: ignore[attr-defined]


def test_a_platform_the_game_does_not_support_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "old"
    root.mkdir()
    db = Db([], present=("realmd", "characters", "mangos"), version_table="updates")
    box = Box(root, "wow-tbc", running_now=(entry("wow-tbc").container_spec().db,), db=db)
    tbc = entry("wow-tbc")

    def tbc_facts() -> ServerFacts:
        return ServerFacts(
            spec=ServerSpec(
                sources=tuple(
                    PackedSource(repo=s.repo, dest=s.dest, commit=s.rev or "", catalog_pin=s.rev)
                    for s in tbc.emulator.sources
                )
            ),
            files=(),
        )

    path = move_flows.export_package(box.world, tmp_path, stop_allowed=False, whole=tbc_facts).path
    from yulon.catalog.installer import unsupported_platform_message

    plan = plan_for(path, tmp_path / "new", platform_id="macos")
    assert plan.refusals == (unsupported_platform_message(tbc, "macos"),)


def test_an_unlabelled_dump_is_refused(tmp_path: Path) -> None:
    from tests.test_move_package import rewrite

    good = whole_package(tmp_path)
    with __import__("zipfile").ZipFile(good) as z:
        body = z.read("db/acore_world.sql")
    unlabelled = body.split(b"\n", 1)[1]
    raw = __import__("json").loads(__import__("zipfile").ZipFile(good).read(move.MANIFEST_NAME))
    for member in raw["databases"]:
        if member["schema"] == "acore_world":
            member["sha256"] = hashlib.sha256(unlabelled).hexdigest()
            member["bytes"] = len(unlabelled)
    bad = rewrite(
        good, tmp_path / "bad.zip", replace={"db/acore_world.sql": unlabelled}, manifest=raw
    )
    plan = plan_for(bad, tmp_path / "new")
    assert plan.refusals == (move.unlabeled_dump("db/acore_world.sql"),)


def test_a_module_this_yulon_does_not_have_is_refused(tmp_path: Path) -> None:
    path = whole_package(tmp_path)
    empty = move_server.ModuleLookup(load=lambda kind, item_id: None, shipped=lambda k, i: False)
    plan = move_server.plan_server_import(
        path,
        catalog=CATALOG,
        server_dir_for=lambda _e: tmp_path / "new",
        platform_id="linux",
        lookup_for=lambda _e: empty,
        commit_known=lambda repo, sha: None,
    )
    assert plan.refusals == (
        "mod-transmog is not among this Yu'lon's modules, so it cannot be installed again here. "
        "Update Yu'lon on this computer, or remove the module on the old one and pack again.",
    )


# =============================================================== the run


class FakeEngine:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.runs = 0

    def preflight(self, options: object, cancel: object = None, *, ask: object = None) -> None:
        self.events.append("preflight")

    def run(
        self, options: InstallOptions, *, cancel: object = None, ask: object = None
    ) -> Iterator[str]:
        self.runs += 1
        self.events.append("install")
        assert options.server_dir is not None
        options.server_dir.mkdir(parents=True, exist_ok=True)
        (options.server_dir / native.STATE_FILE).write_text("{}", encoding="utf-8")
        conf = options.server_dir / "env" / "dist" / "etc" / "worldserver.conf"
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_bytes(
            b'LoginDatabaseInfo = "ac-database;3306;root;password;acore_auth"\nRate.XP.Kill = 1\n'
        )
        yield "WoW WotLK is installed and running"


class FakeApplier:
    def __init__(self, events: list[str], server_dir: Path) -> None:
        self.events = events
        self.server_dir = server_dir
        self.installed: list[tuple[str, str | None, Mapping[str, str] | None]] = []

    world_up: Callable[[], bool] = staticmethod(lambda: False)  # type: ignore[assignment]

    def install(
        self, manifest: Manifest, values: Mapping[str, str] | None = None
    ) -> apply.ApplyReport:
        if self.world_up():
            # The real applier's refusal of a module's SQL while the world runs.
            raise apply.ApplyRefusal(f"{manifest.id}: the world server is running")
        self.events.append(f"module:{manifest.id}")
        self.installed.append(
            (manifest.id, manifest.source.rev if manifest.source else None, values)
        )
        return apply.ApplyReport(
            action="install",
            item_id=manifest.id,
            family=manifest.type,
            done=("cloned",),
            rebuild_required=True,
        )


class Move:
    """A whole package, a new folder, and every double the run acts through."""

    def __init__(self, tmp_path: Path, *, fresh_db: Db | None = None) -> None:
        self.path = whole_package(tmp_path)
        self.target = Box(
            tmp_path,
            running_now=("ac-database", "ac-authserver", "ac-worldserver"),
            db=fresh_db or Db([], realm="10.0.0.7\t127.0.0.1"),
        )
        # The box made its folder; a new install starts in a folder that is not there yet.
        self.target.server_dir.rmdir()
        self.events = self.target.events
        self.engine = FakeEngine(self.events)
        self.applier = FakeApplier(self.events, self.target.server_dir)
        spec = self.target.spec
        self.applier.world_up = lambda: spec.world in self.target.up  # type: ignore[method-assign]
        self.rows: list[tuple[move_server.SourceRevRow, ...]] = []
        self.rebuilds = 0
        self.plan = plan_for(self.path, self.target.server_dir)
        assert self.plan.allowed, self.plan.refusals

    def server_for(self, server_dir: Path, client_dir: Path | None) -> move_server.MovedInServer:
        def rebuild(cancel: threading.Event | None) -> Iterator[str]:
            self.rebuilds += 1
            self.events.append("rebuild")
            # A real rebuild ends with the server started on what it compiled.
            spec = self.target.spec
            self.target.up[:] = [spec.db, spec.auth, spec.world]
            yield "rebuilt"

        return move_server.MovedInServer(
            world=self.target.world,
            applier=self.applier,
            rebuild=rebuild,
            db_password="password",
            persist_manifest=lambda m: self.events.append(f"persist:{m.id}"),
        )

    def install(self) -> move_server.MovedInInstall:
        def record(server_dir: Path, rows: object) -> bool:
            self.events.append("rows")
            self.rows.append(tuple(rows))  # type: ignore[arg-type]
            return True

        return move_server.MovedInInstall(
            self.plan,
            engine=self.engine,
            server_for=self.server_for,
            record_rows=record,
            head_version=lambda dest: "aaaaaaa · 2026-09-01",
            commits_since=lambda dest, rev: 0,
        )

    def run(self) -> list[str]:
        options = InstallOptions(server_dir=self.target.server_dir)
        return list(self.install().run(options))


def test_the_run_installs_then_puts_everything_in_in_order(tmp_path: Path) -> None:
    mv = Move(tmp_path)
    lines = mv.run()
    order = [
        e
        for e in mv.events
        if e in {"install", "rows", "module:mod-transmog", "rebuild", "stop"}
        or e.startswith("load:")
    ]
    assert order == [
        "install",
        "rows",
        "stop",
        "module:mod-transmog",
        "rebuild",
        "stop",
        "load:acore_auth",
        "load:acore_characters",
        "load:acore_world",
    ]
    assert "It is stopped" in lines[-2]


def test_the_module_goes_back_at_its_commit_with_its_old_answers(tmp_path: Path) -> None:
    mv = Move(tmp_path)
    mv.run()
    assert mv.applier.installed == [("mod-transmog", TRANSMOG, {"X": "7"})]


def test_a_source_behind_the_pin_is_recorded_for_the_server_tab(tmp_path: Path) -> None:
    mv = Move(tmp_path)
    mv.run()
    assert mv.rows == [
        (
            move_server.SourceRevRow(
                repo="mod-playerbots/azerothcore-wotlk",
                built="aaaaaaa · 2026-09-01",
                pin=OLD_CORE,
                ahead=0,
            ),
        )
    ]


def test_the_confs_are_laid_with_this_machines_database_line(tmp_path: Path) -> None:
    mv = Move(tmp_path)
    mv.run()
    conf = mv.target.server_dir / "env" / "dist" / "etc" / "worldserver.conf"
    assert conf.read_bytes() == (
        b'LoginDatabaseInfo = "ac-database;3306;root;password;acore_auth"\r\n'
        b"Rate.XP.Kill = 3\r\n"
    )
    lua = mv.target.server_dir / "env" / "dist" / "etc" / "modules" / "lua_scripts" / "mine.lua"
    assert lua.read_bytes() == b"-- mine\n"


def test_the_realm_keeps_this_computers_address_after_the_load(tmp_path: Path) -> None:
    mv = Move(tmp_path)
    mv.run()
    after_load = mv.target.db.executed[
        [i for i, e in enumerate(mv.target.db.executed) if "localAddress" in e][0]
    ]
    assert "address='10.0.0.7'" in after_load and "localAddress='127.0.0.1'" in after_load
    load_at = mv.events.index("load:acore_auth")
    realm_at = max(i for i, e in enumerate(mv.events) if e == "exec")
    assert realm_at > load_at


def test_a_table_only_the_fresh_install_has_is_dropped_before_its_load(tmp_path: Path) -> None:
    mv = Move(
        tmp_path,
        fresh_db=Db(
            [], realm="10.0.0.7\t127.0.0.1", extra_tables={"acore_world": {"fresh_only": 0}}
        ),
    )
    mv.run()
    drops = [sql for sql in mv.target.db.executed if "DROP TABLE" in sql and "fresh_only" in sql]
    assert drops, mv.target.db.executed


def test_a_fresh_install_at_another_version_is_refused_before_any_load(tmp_path: Path) -> None:
    mv = Move(tmp_path, fresh_db=Db([], realm="10.0.0.7\t127.0.0.1", updates=("2024_01_a",)))
    with pytest.raises(MoveError) as raised:
        mv.run()
    assert str(raised.value).startswith(
        "The new server did not end up at the version the package was made from (acore_auth: "
    )
    assert not [e for e in mv.events if e.startswith("load:")]


def test_the_old_channel_account_is_removed_after_the_load(tmp_path: Path) -> None:
    mv = Move(tmp_path)
    mv.run()
    assert any("YULON_AAAA1111" in sql and "DELETE FROM" in sql for sql in mv.target.db.executed)


def test_the_steps_are_recorded_and_a_second_press_does_them_once(tmp_path: Path) -> None:
    mv = Move(tmp_path)
    mv.run()
    marker = move_server.read_marker(mv.target.server_dir)
    assert marker is not None and marker["done"] == list(move_server.STEPS)
    before = list(mv.events)
    lines = mv.run()
    added = mv.events[len(before) :]
    assert not [e for e in added if e.startswith(("module:", "load:")) or e in ("rebuild", "rows")]
    assert sum(1 for line in lines if line.startswith("Already done:")) == len(move_server.STEPS)


def test_a_press_stopped_after_the_modules_carries_on_with_the_rebuild(tmp_path: Path) -> None:
    mv = Move(tmp_path)
    digest = move_server.package_digest(mv.plan.manifest)  # type: ignore[arg-type]
    mv.target.server_dir.mkdir()
    (mv.target.server_dir / native.STATE_FILE).write_text("{}", encoding="utf-8")
    move_server.write_marker(
        mv.target.server_dir, digest, ["revs", "answers", "modules"], rebuild_needed=True
    )
    mv.run()
    assert mv.rebuilds == 1
    assert "module:mod-transmog" not in mv.events


def test_a_folder_that_became_a_server_since_the_plan_is_refused_before_installing(
    tmp_path: Path,
) -> None:
    mv = Move(tmp_path)
    mv.target.server_dir.mkdir()
    (mv.target.server_dir / native.STATE_FILE).write_text("{}", encoding="utf-8")
    with pytest.raises(MoveError) as raised:
        mv.run()
    assert str(raised.value) == (
        f"{move_server.holds_a_server(mv.target.server_dir)} Nothing was started."
    )
    assert "install" not in mv.events


def test_a_file_changed_since_the_plan_is_refused_before_installing(tmp_path: Path) -> None:
    mv = Move(tmp_path)
    other = whole_package(tmp_path / "again", realm="Another Realm")
    mv.path.unlink()
    other.rename(mv.path)
    with pytest.raises(MoveError, match="The file changed after the plan was shown"):
        mv.run()
    assert "install" not in mv.events


def test_another_folder_than_the_planned_one_is_refused(tmp_path: Path) -> None:
    mv = Move(tmp_path)
    with pytest.raises(MoveError, match="not the one the plan was made for"):
        list(mv.install().run(InstallOptions(server_dir=tmp_path / "elsewhere")))
    assert "install" not in mv.events


def test_the_fresh_servers_safety_copies_are_not_kept(tmp_path: Path) -> None:
    mv = Move(tmp_path)
    mv.run()
    backups = mv.target.server_dir / "sql_scripts" / "backups"
    left = sorted(p.name for p in backups.glob("*.sql")) if backups.is_dir() else []
    assert left == []


_ = AT


def _lookup_with(manifest: Manifest, *, shipped: bool = False) -> move_server.ModuleLookup:
    return move_server.ModuleLookup(
        load=lambda kind, item_id: manifest if item_id == manifest.id else None,
        shipped=lambda kind, item_id: shipped,
    )


def _plan_with(path: Path, folder: Path, lookup: move_server.ModuleLookup):
    return move_server.plan_server_import(
        path,
        catalog=CATALOG,
        server_dir_for=lambda _e: folder,
        platform_id="linux",
        lookup_for=lambda _e: lookup,
        commit_known=lambda repo, sha: None,
    )


def test_a_module_whose_repository_moved_is_refused(tmp_path: Path) -> None:
    shipped = STORE.load("module", "mod-transmog")
    assert shipped.source is not None
    moved = shipped.model_copy(
        update={"source": shipped.source.model_copy(update={"repo": "someone/else"})}
    )
    plan = _plan_with(whole_package(tmp_path), tmp_path / "new", _lookup_with(moved))
    assert plan.refusals == (
        f"{shipped.name} comes from {shipped.source.repo} in the package, and from someone/else "
        "in this Yu'lon, so the packed version cannot be installed. Update Yu'lon on both "
        "computers to the same version, then pack again.",
    )


def test_a_link_module_named_like_a_shipped_one_is_refused(tmp_path: Path) -> None:
    def link_facts() -> ServerFacts:
        base = whole_facts()
        linked = Manifest.model_validate(
            {
                "id": "mod-linked",
                "name": "Linked",
                "type": "module",
                "game": "wow-wotlk",
                "source": {"repo": "someone/mod-linked"},
                "origin": {"kind": "link", "added": "2026-10-01"},
            }
        )
        return ServerFacts(
            spec=ServerSpec(
                sources=base.spec.sources,
                modules=(
                    PackedModule(
                        type="module",
                        id="mod-linked",
                        origin="link",
                        repo="someone/mod-linked",
                        commit="d" * 40,
                    ),
                ),
            ),
            files=(
                PackFile(
                    kind="manifest",
                    target="module/mod-linked",
                    data=linked.model_dump_json().encode(),
                ),
            ),
        )

    root = tmp_path / "old"
    root.mkdir()
    box = Box(root, running_now=("ac-database",))
    path = move_flows.export_package(box.world, tmp_path, stop_allowed=False, whole=link_facts).path
    clash = move_server.ModuleLookup(load=lambda k, i: None, shipped=lambda k, i: True)
    assert _plan_with(path, tmp_path / "new", clash).refusals == (
        "mod-linked was added from a link on the old computer, and this Yu'lon ships a module "
        "of that name, so the two cannot be told apart. Remove it on the old computer and pack "
        "again.",
    )
    fine = move_server.ModuleLookup(load=lambda k, i: None, shipped=lambda k, i: False)
    plan = _plan_with(path, tmp_path / "new2", fine)
    assert plan.allowed, plan.refusals
    assert plan.modules[0].carried is not None
    assert plan.modules[0].manifest.source.rev == "d" * 40  # type: ignore[union-attr]


def test_a_module_this_move_finished_is_not_installed_twice(tmp_path: Path) -> None:
    mv = Move(tmp_path)
    digest = move_server.package_digest(mv.plan.manifest)  # type: ignore[arg-type]
    mv.target.server_dir.mkdir()
    (mv.target.server_dir / native.STATE_FILE).write_text("{}", encoding="utf-8")
    move_server.write_marker(
        mv.target.server_dir,
        digest,
        ["revs", "answers"],
        modules_done=["module/mod-transmog"],
        rebuild_needed=True,
    )
    lines = mv.run()
    assert mv.applier.installed == []
    assert any("is already installed" in line for line in lines)
    assert mv.rebuilds == 1


def test_a_module_that_failed_is_asked_again_and_its_rebuild_is_not_lost(tmp_path: Path) -> None:
    mv = Move(tmp_path)
    real = mv.applier.install
    calls = {"n": 0}

    def fail_once(manifest: Manifest, values: Mapping[str, str] | None = None) -> apply.ApplyReport:
        calls["n"] += 1
        if calls["n"] == 1:
            raise apply.ApplyRefusal("the clone was cut off")
        return real(manifest, values)

    mv.applier.install = fail_once  # type: ignore[method-assign]
    with pytest.raises(MoveError, match="could not be installed again"):
        mv.run()
    marker = move_server.read_marker(mv.target.server_dir)
    assert marker is not None and marker["rebuild_needed"] is True  # declared before the install
    mv.run()
    assert calls["n"] == 2
    assert mv.rebuilds == 1


def test_a_conf_folder_that_is_a_link_is_never_written_through(tmp_path: Path) -> None:
    mv = Move(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    real_run = mv.engine.run

    def run_then_link(options: InstallOptions, **kw: object) -> Iterator[str]:
        yield from real_run(options, **kw)  # type: ignore[arg-type]
        etc = options.server_dir / "env" / "dist"  # type: ignore[operator]
        import shutil

        shutil.rmtree(etc)
        etc.symlink_to(outside, target_is_directory=True)

    mv.engine.run = run_then_link  # type: ignore[method-assign]
    with pytest.raises(MoveError, match="is a link, so Yu'lon will not write through it"):
        mv.run()
    assert list(outside.iterdir()) == []


def test_no_rebuild_when_no_module_needs_one(tmp_path: Path) -> None:
    from dataclasses import replace

    mv = Move(tmp_path)
    mv.plan = replace(mv.plan, modules=())
    lines = mv.run()
    assert mv.rebuilds == 0
    assert "No module needs the server built again." in lines


def test_a_module_that_declares_a_rebuild_gets_one_even_if_its_report_says_none(
    tmp_path: Path,
) -> None:
    mv = Move(tmp_path)
    assert mv.plan.modules[0].manifest.build.rebuild

    def install(manifest: Manifest, values: Mapping[str, str] | None = None) -> apply.ApplyReport:
        return apply.ApplyReport(
            action="install", item_id=manifest.id, family=manifest.type, rebuild_required=False
        )

    mv.applier.install = install  # type: ignore[method-assign]
    mv.run()
    assert mv.rebuilds == 1


def test_the_server_is_stopped_before_a_conf_is_laid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mv = Move(tmp_path)
    real = move_server._write_bytes

    def write(target: Path, data: bytes) -> None:
        mv.events.append(f"write:{target.name}")
        real(target, data)

    monkeypatch.setattr(move_server, "_write_bytes", write)
    mv.run()
    write = mv.events.index("write:worldserver.conf")
    rebuilt = mv.events.index("rebuild")
    assert rebuilt < write and "stop" in mv.events[rebuilt:write]


def test_an_install_that_fails_leaves_a_folder_this_file_carries_on_in(tmp_path: Path) -> None:
    mv = Move(tmp_path)
    real_run = mv.engine.run

    def fails(options: InstallOptions, **kw: object) -> Iterator[str]:
        options.server_dir.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
        # The core's clone into the server folder itself clears it, record and all.
        for child in options.server_dir.iterdir():  # type: ignore[union-attr]
            child.unlink()
        (options.server_dir / native.STATE_FILE).write_text("{}", encoding="utf-8")  # type: ignore[operator]
        yield "Cloning"
        raise native.InstallerError("the compile failed")

    mv.engine.run = fails  # type: ignore[method-assign]
    with pytest.raises(native.InstallerError):
        mv.run()
    again = plan_for(mv.path, mv.target.server_dir)
    assert again.allowed and again.resuming, again.refusals
    mv.engine.run = real_run  # type: ignore[method-assign]
    mv.run()
    assert move_server.read_marker(mv.target.server_dir)["done"] == list(move_server.STEPS)  # type: ignore[index]


def test_a_carried_on_load_uses_the_address_read_before_the_first_load(tmp_path: Path) -> None:
    mv = Move(
        tmp_path, fresh_db=Db([], realm="10.0.0.7\t127.0.0.1", fail_load_of="acore_characters")
    )
    with pytest.raises(MoveError):
        mv.run()
    # The auth load landed: the realm row now holds the OLD computer's address.
    mv.target.db.realm = "192.168.9.9\t192.168.9.9"
    mv.target.db.fail_load_of = None
    time.sleep(1.1)  # the engine's safety copies are named by the second; a real press is later
    mv.run()
    last = [sql for sql in mv.target.db.executed if "localAddress" in sql][-1]
    assert "address='10.0.0.7'" in last and "192.168.9.9" not in last


def test_the_move_in_record_is_one_of_yulons_own_files(tmp_path: Path) -> None:
    """The install's own guard reads a folder holding only the record as empty, so it installs."""
    assert move_server.MOVE_IN_FILE == native.MOVE_IN_FILE
    move_server.write_marker(tmp_path, "f" * 64, [])
    assert not native._listing(tmp_path, ignoring=native.OUR_OWN_FILES)


def test_an_install_that_stops_before_saying_anything_still_leaves_the_record(
    tmp_path: Path,
) -> None:
    mv = Move(tmp_path)

    def fails(options: InstallOptions, **kw: object) -> Iterator[str]:
        (options.server_dir / native.STATE_FILE).write_text("{}", encoding="utf-8")  # type: ignore[operator]
        raise native.InstallerError("preflight refused")
        yield ""  # pragma: no cover - a generator that never yields

    mv.engine.run = fails  # type: ignore[method-assign]
    with pytest.raises(native.InstallerError):
        mv.run()
    again = plan_for(mv.path, mv.target.server_dir)
    assert again.allowed and again.resuming, again.refusals


def test_a_second_module_failing_does_not_redo_the_first(tmp_path: Path) -> None:
    from dataclasses import replace

    mv = Move(tmp_path)
    loot = STORE.load("module", "mod-aoe-loot")
    assert loot.source is not None
    second = move_server.ModuleToInstall(
        packed=PackedModule(
            type="module",
            id="mod-aoe-loot",
            origin="catalog",
            repo=loot.source.repo,
            commit="e" * 40,
        ),
        manifest=loot.model_copy(
            update={"source": loot.source.model_copy(update={"rev": "e" * 40})}
        ),
    )
    mv.plan = replace(mv.plan, modules=(*mv.plan.modules, second))
    real = mv.applier.install
    failed = {"once": False}

    def flaky(manifest: Manifest, values: Mapping[str, str] | None = None) -> apply.ApplyReport:
        if manifest.id == "mod-aoe-loot" and not failed["once"]:
            failed["once"] = True
            raise apply.ApplyRefusal("cut off")
        return real(manifest, values)

    mv.applier.install = flaky  # type: ignore[method-assign]
    with pytest.raises(MoveError):
        mv.run()
    mv.run()
    assert [i[0] for i in mv.applier.installed] == ["mod-transmog", "mod-aoe-loot"]


def test_a_rebuild_needed_with_no_rebuild_here_is_refused_not_skipped(tmp_path: Path) -> None:
    from dataclasses import replace

    mv = Move(tmp_path)
    original = mv.server_for
    mv.server_for = lambda d, c: replace(original(d, c), rebuild=None)  # type: ignore[method-assign]
    with pytest.raises(MoveError, match="is not wired for WoW WotLK here"):
        mv.run()
