"""TBC modules: a manifest set for a core that has no compiled modules (roadmap 8.7b).

AzerothCore modules are git repositories compiled into the worldserver. CMaNGOS
has nothing of the kind, so on `wow-tbc` a "module" is one of exactly two
things the existing schema already expresses:

* a **configuration activation** — `conf[].keys` written into a file the
  install already owns (`etc/mangosd.conf`), which is what `apply._conf()` does;
* a **SQL mod** — `sql[].statement` run against this game's own world schema.

So this file proves the BINDING and the RELATIONSHIPS, not a new engine. The
relationships are where the defects live, and each has no owner on either side:

* a manifest names a conf FILE; the catalog's installer decides which conf
  files exist and where. Nothing on either side compares them.
* a manifest writes a conf KEY; the installer's own `conf` table rewrites its
  keys on every install and repair. A module that picked one of those keys
  would be silently undone by the next run, and neither file can see the other.
* a manifest names a DATABASE (`world`, `playerbots`, ...); the entry decides
  which of those this core actually has. `playerbots` and `ale` are
  AzerothCore's and a TBC manifest naming one would reach `DockerSql` and die
  on `Unknown database`.
* every TBC manifest must ask for a RESTART and never a rebuild — there is no
  binary to rebuild, and mangosd reads `etc/mangosd.conf` once, at startup.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from yulon.apply import Applier, ApplyError
from yulon.catalog.catalog import load_catalog
from yulon.controller_wow_tbc import modules as tbc_modules
from yulon.controller_wow_wotlk import modules as wotlk_modules
from yulon.manifest import Db, Manifest, parse_manifest
from yulon.manifest_store import FAMILY_FILES

ENTRY = load_catalog().get("wow-tbc")
GAME = "wow-tbc"


def _mods() -> list[Manifest]:
    return list(tbc_modules.store().load_all("mod"))


def _server_dir_with_conf(tmp_path: Path) -> Path:
    """A server dir shaped like a finished TBC install: `etc/` with the real conf names.

    The key lines are the ones CMaNGOS ships (read off `~/tbc-7.4c/etc/` on
    m910q, 2026-09-08), so a patch or a key write that only works against a
    file this test invented would not pass here.
    """
    etc = tmp_path / "etc"
    etc.mkdir(parents=True)
    (etc / "mangosd.conf").write_text(
        "[MangosdConf]\n"
        "ConfVersion = 2020032001\n"
        'Motd = "Welcome to the Continued Massive Network Game Object Server."\n'
        "InstantLogout = 1\n"
        "AllFlightPaths = 0\n"
        "AllowTwoSide.Accounts = 0\n"
        "AllowTwoSide.Interaction.Chat = 0\n"
        "AllowTwoSide.Interaction.Channel = 0\n"
        "AllowTwoSide.Interaction.Group = 0\n"
        "AllowTwoSide.Interaction.Guild = 0\n"
        "AllowTwoSide.Interaction.Auction = 0\n"
        "AllowTwoSide.Interaction.Mail = 0\n"
        "AllowTwoSide.WhoList = 0\n"
        "AllowTwoSide.AddFriend = 0\n"
        "Rate.XP.Kill    = 1\n"
        "Rate.XP.Quest   = 1\n"
        "Rate.XP.Explore = 1\n"
        "Rate.XP.Kill.Vanilla  = 1\n"
        "Rate.XP.Kill.BC       = 1\n",
        encoding="utf-8",
        newline="\n",
    )
    (etc / "realmd.conf").write_text("[RealmdConf]\n", encoding="utf-8", newline="\n")
    return tmp_path


class _RecordingSql:
    """A `SqlRunner` that remembers which schema key each statement was aimed at."""

    def __init__(self) -> None:
        self.statements: list[tuple[Db, str]] = []
        self.files: list[tuple[Db, str]] = []

    def run_file(self, db: Db, path: Path) -> None:
        self.files.append((db, path.name))

    def run_statement(self, db: Db, statement: str) -> None:
        self.statements.append((db, statement))


# ------------------------------------------------------------------ binding


def test_the_tbc_store_serves_tbc_manifests_and_not_wotlks() -> None:
    """The store is bound to THIS game: `_no_manifest_store()`'s warning made real.

    A tab handed `wotlk_modules.store()` would offer AzerothCore's C++ modules
    for a CMaNGOS server and every one of them would fail at the clone or the
    rebuild. So the assertion is not "a store exists" but "the ids it serves
    are not the other game's".
    """
    store = tbc_modules.store()
    assert store.game == GAME
    mine = {m.id for m in _mods()}
    assert mine, "the TBC mod index is empty; 8.7b needs at least one real manifest"
    theirs = {m.id for m in wotlk_modules.store().load_all("module")}
    assert mine.isdisjoint(theirs)
    for manifest in _mods():
        assert manifest.game == GAME


def test_every_family_index_exists_so_the_tab_lists_no_error_line() -> None:
    """`controller_view.reload_modules()` walks all four families and prints `!!` for a miss.

    CMaNGOS has no C++ modules, no Eluna-Lua-Engine and therefore no kegs, and
    "there are none" is a fact worth writing down once as an empty index rather
    than leaking out of the UI as "could not load modules: manifest file
    missing".
    """
    store = tbc_modules.store()
    for kind in FAMILY_FILES:
        index = store.load_index(kind)
        assert index.game == GAME and index.type == kind
        if kind != "mod":
            assert index.items == (), f"CMaNGOS has no {kind}s; {kind} index must be empty"


# ------------------------------------------------------- the relationships


def test_every_conf_file_a_manifest_names_is_one_this_install_actually_has() -> None:
    """A manifest's `conf[].file` must be a conf the CMaNGOS installer materialises.

    The installer copies `install.native.cmangos.conf.files` out of the image
    into `<server_dir>/etc/` (`families/cmangos.ETC_DIR`). Anything else — most
    temptingly AzerothCore's `env/dist/etc/worldserver.conf`, which every WotLK
    manifest names — is a path that does not exist on this server, and
    `apply._conf()` answers a missing file by SKIPPING it. The user would press
    Install, be told it succeeded, and nothing would have changed.
    """
    native = ENTRY.install.native
    assert native is not None and native.cmangos is not None
    known = {f"etc/{name}" for name in native.cmangos.conf.files}
    for manifest in _mods():
        for conf in manifest.conf:
            assert conf.file in known, (
                f"{manifest.id} writes {conf.file}, which the TBC installer never creates; "
                f"it makes {sorted(known)}"
            )
            assert conf.template is None, f"{manifest.id}: a sourceless mod has no clone to copy"


def test_no_manifest_writes_a_key_the_installer_rewrites_on_every_run() -> None:
    """The installer's own conf table owns some keys, and it wins.

    `families/conf.apply_table()` patches its keys in place on every install and
    every repair, so a module that set one of them would be undone by the next
    run of the thing that installed it — with nothing reporting a conflict,
    because the manifest cannot see the catalog and the catalog cannot see the
    manifests. The DB connection strings, `DataDir`, `WorldServerPort`, the
    playerbot population and the AH bot's rates are all in that table.
    """
    native = ENTRY.install.native
    assert native is not None and native.cmangos is not None
    owned = {
        (f"etc/{name}", key)
        for name, block in native.cmangos.conf.files.items()
        for key in block.keys
    }
    for manifest in _mods():
        for conf in manifest.conf:
            for entry_key in conf.keys:
                assert (conf.file, entry_key.key) not in owned, (
                    f"{manifest.id} sets {entry_key.key} in {conf.file}, which the installer's "
                    "own conf table rewrites on every install and repair"
                )


def test_no_manifest_names_a_database_this_core_does_not_have() -> None:
    """`playerbots` and `ale` are AzerothCore's schemas; TBC has four others.

    `DockerSql` resolves a manifest `db` key through `CatalogEntry.schema_map()`,
    and a key that is not in the map is refused by name. Catching it here means
    a bad manifest fails in CI rather than half-way through a user's install.
    """
    have = set(ENTRY.schema_map())
    for manifest in _mods():
        for step in manifest.sql:
            assert step.db in have, (
                f"{manifest.id} runs SQL against {step.db!r}, which wow-tbc does not have "
                f"(it has {sorted(have)})"
            )
            assert (
                step.applied_by == "direct"
            ), f"{manifest.id}: `db-import` is AzerothCore's `ac-db-import`; CMaNGOS has none"


def test_no_tbc_manifest_asks_for_a_rebuild_and_all_of_them_ask_for_a_restart() -> None:
    """There is no binary to rebuild, and mangosd reads its conf once, at startup.

    The rebuild half is the interesting one: `build.rebuild` defaults to True on
    the model, so a manifest that simply omitted the block would tell a CMaNGOS
    user to spend an hour recompiling a worldserver that would come back
    identical.
    """
    for manifest in _mods():
        assert manifest.build.rebuild is False, f"{manifest.id}: CMaNGOS compiles no modules"
        assert manifest.build.restart is True, (
            f"{manifest.id}: mangosd reads etc/*.conf at startup and loads the world DB at "
            "startup, so every one of these needs the server restarted to take effect"
        )


# ------------------------------------------------------------ the real ones


def test_the_motd_manifest_writes_the_key_the_console_reports_and_asks_for_a_restart(
    tmp_path: Path,
) -> None:
    """8.7b's definition of done, in the part a test can hold.

    `.server motd` is a real CMaNGOS console command
    (`Chat.cpp` → `HandleServerMotdCommand` → `sWorld.GetMotd()`), so the value
    this install writes into `etc/mangosd.conf` is one the RUNNING server reads
    back out on the console after a restart. Here: the key lands, the old text
    is gone, and the report asks for the restart the gate then performs.
    """
    server_dir = _server_dir_with_conf(tmp_path)
    manifest = tbc_modules.store().load("mod", "motd")
    report = Applier(server_dir, sql=_RecordingSql()).install(manifest, {"motd": "Yulon 8.7b"})

    text = (server_dir / "etc" / "mangosd.conf").read_text(encoding="utf-8")
    assert 'Motd = "Yulon 8.7b"' in text
    assert "Continued Massive Network" not in text
    assert report.skipped == (), report.skipped
    assert report.rebuild_required is False
    assert report.restart_recommended is True, "a conf mod that does not ask for a restart lies"


def test_removing_the_motd_manifest_puts_the_cmangos_default_back(tmp_path: Path) -> None:
    """Remove is the half that gets skipped, and a conf mod has no files to delete.

    So the only thing `remove()` can do for one is put the shipped value back,
    which is a `when: "remove"` patch — and if it is missing, removal silently
    leaves the user's server running the module's setting forever.
    """
    server_dir = _server_dir_with_conf(tmp_path)
    manifest = tbc_modules.store().load("mod", "motd")
    applier = Applier(server_dir, sql=_RecordingSql())
    applier.install(manifest, {"motd": "Yulon 8.7b"})
    applier.remove(manifest)

    text = (server_dir / "etc" / "mangosd.conf").read_text(encoding="utf-8")
    assert 'Motd = "Welcome to the Continued Massive Network Game Object Server."' in text
    assert "Yulon 8.7b" not in text


def test_every_conf_mod_restores_its_shipped_default_on_remove(tmp_path: Path) -> None:
    """Enumerated rather than read: the file above proves `motd`, this proves the set.

    Install every conf-writing mod with its own defaults, remove it, and require
    the file to be byte-identical to the one CMaNGOS ships. A mod that forgets
    one of its `when: "remove"` patches — the easy mistake when a manifest sets
    nine `AllowTwoSide.*` keys — fails here and names itself.
    """
    for manifest in _mods():
        if not manifest.conf:
            continue
        server_dir = _server_dir_with_conf(tmp_path / manifest.id)
        conf = server_dir / "etc" / "mangosd.conf"
        before = conf.read_text(encoding="utf-8")
        applier = Applier(server_dir, sql=_RecordingSql())
        applier.install(manifest)
        assert conf.read_text(encoding="utf-8") != before, f"{manifest.id} changed nothing"
        applier.remove(manifest)
        assert (
            conf.read_text(encoding="utf-8") == before
        ), f"{manifest.id} did not put every key it changed back"


def test_the_sql_mod_runs_inline_against_this_games_world_schema(tmp_path: Path) -> None:
    """A SQL mod on CMaNGOS is inline SQL, because there is no repo to clone it from.

    It must also be reversible without one: the install takes its own backup
    into a table it names, and the removal restores from it. A `remove` that
    only dropped the backup would leave every stack size changed forever.
    """
    server_dir = _server_dir_with_conf(tmp_path)
    manifest = tbc_modules.store().load("mod", "all-stackables")
    assert manifest.source is None, "there is no upstream repo for a hand-written SQL mod"

    sql = _RecordingSql()
    report = Applier(server_dir, sql=sql).install(manifest, {"stack_size": "200"})
    assert sql.files == []
    assert [db for db, _ in sql.statements] == ["world"] * len(sql.statements)
    joined = " ".join(stmt for _, stmt in sql.statements)
    assert "item_template" in joined and "200" in joined
    assert report.restart_recommended is True

    sql.statements.clear()
    Applier(server_dir, sql=sql).remove(manifest)
    restore = " ".join(stmt for _, stmt in sql.statements)
    assert "UPDATE" in restore.upper(), "remove must put the original stack sizes back"


def test_the_world_key_reaches_mariadb_as_mangos_not_acore_world() -> None:
    """The end of the chain the manifest only names half of.

    `sql[].db` is `world` in every game's manifests; what `world` MEANS is the
    entry's. This is the assertion that stops a TBC SQL mod from being run
    against `acore_world` — the failure a whole schema map was added for.
    """
    assert ENTRY.schema_map()["world"] == "mangos"
    assert "playerbots" not in ENTRY.schema_map()


def test_the_checked_in_tbc_manifests_are_the_ones_the_index_lists() -> None:
    """Belt and braces beside `test_manifest.py`'s parametrized walk: no orphans, no ghosts."""
    root = Path(tbc_modules.BUNDLED_MANIFESTS_DIR) / GAME
    listed = set(json.loads((root / "mods.json").read_text(encoding="utf-8"))["items"])
    on_disk = {p.stem for p in (root / "mods").glob("*.json")}
    assert listed == on_disk


@pytest.mark.parametrize("kind", sorted(FAMILY_FILES))
def test_the_fetcher_knows_where_to_refresh_each_family_from(kind: str) -> None:
    """The refresh URL is per-game data, and a wrong one silently mirrors WotLK's tree."""
    files = tbc_modules.store().relative_files(kind)  # type: ignore[arg-type]
    assert files[0].startswith(f"{GAME}/")
    assert all(f.startswith(f"{GAME}/") for f in files)


# ---------------------------- the running-world guard, through this game's factory (T7)


_WORLD_SQL_MOD: dict[str, object] = {
    "schema_version": 1,
    "id": "world-sql",
    "name": "World SQL",
    "type": "mod",
    "game": GAME,
    "build": {"rebuild": False, "restart": True},
    "sql": [{"db": "world", "statement": "UPDATE item_template SET stackable = 200"}],
}


def test_this_games_applier_refuses_direct_world_sql_while_the_world_runs(tmp_path: Path) -> None:
    """The seam is not merely accepted by the factory -- it arrives at the guard.

    `applier()` grew a required `world_running` in T7 because for one day no
    caller passed one and `Applier._world_running` was `None` on all four games:
    checklist 8.7a's guard returned at its first line, and the Modules tab would
    have written into a live world without a word (T2's press,
    `.notes/gates/8.7a-direct-sql-yulon-ubuntu2-2026-09-09/`).

    Catches the keyword accepted and then dropped on the way to `Applier(...)`,
    which neither a signature check nor the type checker would see.
    """
    sql = _RecordingSql()
    applier = tbc_modules.applier(tmp_path, sql=sql, world_running=lambda: True)

    with pytest.raises(ApplyError) as caught:
        applier.install(parse_manifest(_WORLD_SQL_MOD))

    assert "the world server is running" in str(caught.value)
    assert sql.statements == [] and sql.files == []


def test_this_games_applier_starts_the_database_once_the_world_is_stopped(tmp_path: Path) -> None:
    """The other half of T7, through the same factory: Stop, then install, and it works.

    This game's Stop takes the database down with the world, so a direct SQL
    step on a stopped stack used to die on `container ... is not running`
    (`bug-checklist §46`, which was filed against this family). The seam puts
    the database back alone; the world is not started.

    Catches `start_database` accepted by the factory and dropped, which would
    leave this game with the guard armed and no way to obey it.
    """
    sql = _RecordingSql()
    started: list[int] = []

    def start_db() -> bool:
        started.append(1)
        return True

    applier = tbc_modules.applier(
        tmp_path, sql=sql, world_running=lambda: False, start_database=start_db
    )

    report = applier.install(parse_manifest(_WORLD_SQL_MOD))

    assert started == [1]
    assert "started the database alone; the world server was left stopped" in report.done
    assert sql.statements == [("world", "UPDATE item_template SET stackable = 200")]
