"""Vanilla modules: the TBC manifest shape pointed at mangos-classic (roadmap 8.7c).

8.7c's box reads "as 8.7b", and the honest reading of that is *the same shape,
this tree's own facts*. The shape is settled and is not re-argued here: CMaNGOS
compiles no modules, so an item is a **configuration activation** into a conf
the install already owns or a **SQL mod** against this game's world schema
(`controller_wow_tbc.modules`, and `test_tbc_modules.py` beside this file).

What this file exists for is the half that is NOT shared. Every value below was
read off `~/vanilla-75b` on m910q on 2026-09-08 — the conf this install actually
has and the `src/mangos-classic` checkout it was built from — and three of them
differ from the TBC sibling that was written the day before:

* **`AllowTwoSide.Interaction.Trade` exists here.** mangos-classic reads it at
  `src/game/World/World.cpp:525`, and `etc/mangosd.conf:928` ships it; the TBC
  manifest's own note says the key is absent on that fork. So cross-faction is
  **ten** pairs on this tree and nine on that one, and a copied manifest would
  leave trading switched off on a realm that had been told it was on.
* **There are no `Rate.XP.Kill.Vanilla` / `.BC` twins.** `World.cpp:422` sets
  `Rate.XP.Kill` and nothing else of that family, so the per-tier caveat in the
  TBC manifest's notes is not a fact about this game.
* **`Rate.Pet.XP.Kill` sits two lines above `Rate.XP.Kill`** in the shipped conf
  (`etc/mangosd.conf:1509-1510`). An unanchored `Rate\\.XP\\.Kill` would match it,
  so the anchoring the TBC manifests use is load-bearing HERE in a way it was
  not there, and `test_the_xp_mod_leaves_the_pet_rate_alone` is the assertion
  that says so.

The relationships this proves are the same four the TBC file proves, because
they are relationships and no single file owns them: a manifest names a conf
FILE the catalog's installer must actually create; a manifest writes a conf KEY
that installer must not rewrite on its next repair; a manifest names a DATABASE
this entry must actually have; and every item must ask for a restart and never
for a rebuild.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from yulon.apply import Applier, ApplyError
from yulon.catalog.catalog import load_catalog
from yulon.controller_wow_tbc import modules as tbc_modules
from yulon.controller_wow_vanilla import modules as vanilla_modules
from yulon.controller_wow_wotlk import modules as wotlk_modules
from yulon.manifest import Db, Manifest, parse_manifest
from yulon.manifest_store import FAMILY_FILES

ENTRY = load_catalog().get("wow-vanilla")
GAME = "wow-vanilla"


def _mods() -> list[Manifest]:
    return list(vanilla_modules.store().load_all("mod"))


def _server_dir_with_conf(tmp_path: Path) -> Path:
    """A server dir shaped like a finished Vanilla install.

    The lines are the ones `~/vanilla-75b/etc/mangosd.conf` actually holds,
    including their shipped spacing and their NEIGHBOURS: `Rate.Pet.XP.Kill`
    above the XP block and `TalentsInspecting` below the two-side block are here
    because a regex that catches them would pass a fixture that omitted them.
    """
    etc = tmp_path / "etc"
    etc.mkdir(parents=True)
    (etc / "mangosd.conf").write_text(
        "[MangosdConf]\n"
        "ConfVersion = 2020032001\n"
        "LongFlightPathsPersistence = 0\n"
        "AllFlightPaths = 0\n"
        "AlwaysMaxSkillForLevel = 0\n"
        "WaitAtStartupError = 0\n"
        'Motd = "Welcome to the Continued Massive Network Game Object Server."\n'
        "PlayerCommands = 1\n"
        "AllowTwoSide.Accounts = 0\n"
        "AllowTwoSide.Interaction.Chat = 0\n"
        "AllowTwoSide.Interaction.Channel = 0\n"
        "AllowTwoSide.Interaction.Group = 0\n"
        "AllowTwoSide.Interaction.Guild = 0\n"
        "AllowTwoSide.Interaction.Trade = 0\n"
        "AllowTwoSide.Interaction.Auction = 0\n"
        "AllowTwoSide.Interaction.Mail = 0\n"
        "AllowTwoSide.WhoList = 0\n"
        "AllowTwoSide.AddFriend = 0\n"
        "TalentsInspecting = 1\n"
        "Rate.Drop.Money = 1\n"
        "Rate.Pet.XP.Kill = 1\n"
        "Rate.XP.Kill    = 1\n"
        "Rate.XP.Quest   = 1\n"
        "Rate.XP.Explore = 1\n"
        "Rate.Rest.InGame = 1\n",
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


def test_the_vanilla_store_serves_vanilla_manifests_and_nobody_elses() -> None:
    """Bound to THIS game, and disjoint from BOTH neighbours.

    The WotLK half is the same assertion `test_tbc_modules.py` makes: those are
    C++ modules and every one would die at the clone. The TBC half is the one
    8.7c adds, and it is the likelier accident — the two trees' manifests look
    almost identical, so a store that quietly served `wow-tbc`'s would pass
    every "a manifest loaded" check while writing a nine-key cross-faction set
    into a ten-key conf.
    """
    store = vanilla_modules.store()
    assert store.game == GAME
    mine = {m.id for m in _mods()}
    assert mine, "the Vanilla mod index is empty; 8.7c needs at least one real manifest"
    assert mine.isdisjoint({m.id for m in wotlk_modules.store().load_all("module")})
    for manifest in _mods():
        assert manifest.game == GAME
    for manifest in tbc_modules.store().load_all("mod"):
        assert manifest.game == "wow-tbc"


def test_every_family_index_exists_so_the_tab_lists_no_error_line() -> None:
    """`controller_view.reload_modules()` walks all four families and prints `!!` for a miss."""
    store = vanilla_modules.store()
    for kind in FAMILY_FILES:
        index = store.load_index(kind)
        assert index.game == GAME and index.type == kind
        if kind != "mod":
            assert index.items == (), f"CMaNGOS has no {kind}s; {kind} index must be empty"


# ------------------------------------------------------- the relationships


def test_every_conf_file_a_manifest_names_is_one_this_install_actually_has() -> None:
    """`apply._conf()` SKIPS a file that is not there, so a wrong path reports success."""
    native = ENTRY.install.native
    assert native is not None and native.cmangos is not None
    known = {f"etc/{name}" for name in native.cmangos.conf.files}
    for manifest in _mods():
        for conf in manifest.conf:
            assert conf.file in known, (
                f"{manifest.id} writes {conf.file}, which the Vanilla installer never "
                f"creates; it makes {sorted(known)}"
            )
            assert conf.template is None, f"{manifest.id}: a sourceless mod has no clone to copy"


def test_no_manifest_writes_a_key_the_installer_rewrites_on_every_run() -> None:
    """The installer's conf table wins, silently, on the next install or repair.

    This entry's table is NOT its TBC sibling's — `aiplayerbot.conf` here also
    owns `AiPlayerbot.SyncLevelWithPlayers`, `.SyncLevelMaxAbove` and
    `.SyncLevelNoPlayer` — so the set is read off this game's own entry.
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
    """`world` here is `mangos`; `acore_world`, `playerbots` and `ale` are not this core's."""
    have = set(ENTRY.schema_map())
    for manifest in _mods():
        for step in manifest.sql:
            assert step.db in have, (
                f"{manifest.id} runs SQL against {step.db!r}, which wow-vanilla does not have "
                f"(it has {sorted(have)})"
            )
            assert (
                step.applied_by == "direct"
            ), f"{manifest.id}: `db-import` is AzerothCore's; CMaNGOS has no import service"


def test_no_vanilla_manifest_asks_for_a_rebuild_and_all_of_them_ask_for_a_restart() -> None:
    """`build.rebuild` defaults to True on the model, so an omitted block costs an hour."""
    for manifest in _mods():
        assert manifest.build.rebuild is False, f"{manifest.id}: CMaNGOS compiles no modules"
        assert manifest.build.restart is True, (
            f"{manifest.id}: mangosd reads etc/*.conf and loads the world DB at startup, so "
            "every one of these needs the server restarted to take effect"
        )


# ------------------------------------------------------------ the real ones


def test_the_motd_manifest_writes_the_key_the_console_reports_and_asks_for_a_restart(
    tmp_path: Path,
) -> None:
    """8.7c's definition of done, in the part a test can hold.

    `.server motd` is a real mangos-classic console command with `allowConsole`
    true — `src/game/Chat/Chat.cpp:797` binds it to `HandleServerMotdCommand`,
    and `Level0.cpp:280-282` answers with `sWorld.GetMotd()` — so the value this
    writes into `etc/mangosd.conf` is one the RUNNING server states back with no
    client involved. Measured on this tree rather than inherited: the TBC row is
    at `Chat.cpp:816`, a different file in a different checkout.
    """
    server_dir = _server_dir_with_conf(tmp_path)
    manifest = vanilla_modules.store().load("mod", "motd")
    report = Applier(server_dir, sql=_RecordingSql()).install(manifest, {"motd": "Yulon 8.7c"})

    text = (server_dir / "etc" / "mangosd.conf").read_text(encoding="utf-8")
    assert 'Motd = "Yulon 8.7c"' in text
    assert "Continued Massive Network" not in text
    assert report.skipped == (), report.skipped
    assert report.rebuild_required is False
    assert report.restart_recommended is True, "a conf mod that does not ask for a restart lies"


def test_removing_the_motd_manifest_puts_the_mangos_classic_default_back(tmp_path: Path) -> None:
    """A conf mod has no files to delete, so its `when: "remove"` patch is the whole undo."""
    server_dir = _server_dir_with_conf(tmp_path)
    manifest = vanilla_modules.store().load("mod", "motd")
    applier = Applier(server_dir, sql=_RecordingSql())
    applier.install(manifest, {"motd": "Yulon 8.7c"})
    applier.remove(manifest)

    text = (server_dir / "etc" / "mangosd.conf").read_text(encoding="utf-8")
    assert 'Motd = "Welcome to the Continued Massive Network Game Object Server."' in text
    assert "Yulon 8.7c" not in text


def test_every_conf_mod_restores_its_shipped_default_on_remove(tmp_path: Path) -> None:
    """Enumerated, not read: a mod that forgets one of ten remove patches names itself."""
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


def test_cross_faction_covers_the_ten_keys_this_fork_has_and_not_the_nine_tbc_has(
    tmp_path: Path,
) -> None:
    """The per-tree difference, asserted in the direction that fails on a copy.

    `AllowTwoSide.Interaction.Trade` is read by mangos-classic
    (`src/game/World/World.cpp:525`) and shipped in its conf
    (`etc/mangosd.conf:928`); the TBC manifest's own note records that the key
    does not exist on that fork. A cross-faction set copied from `wow-tbc` would
    switch nine things on, leave trading off, and report success — which is why
    this asks the file rather than the manifest: what matters is that no
    `AllowTwoSide.*` line is left at 0 after the install.
    """
    server_dir = _server_dir_with_conf(tmp_path)
    manifest = vanilla_modules.store().load("mod", "cross-faction")
    Applier(server_dir, sql=_RecordingSql()).install(manifest)

    lines = (server_dir / "etc" / "mangosd.conf").read_text(encoding="utf-8").splitlines()
    two_side = [line for line in lines if line.startswith("AllowTwoSide.")]
    assert len(two_side) == 10, two_side
    assert [line for line in two_side if line.endswith("= 0")] == []
    assert "AllowTwoSide.Interaction.Trade = 1" in two_side
    tbc_keys = {
        key.key
        for conf in tbc_modules.store().load("mod", "cross-faction").conf
        for key in conf.keys
    }
    mine = {key.key for conf in manifest.conf for key in conf.keys}
    assert mine - tbc_keys == {"AllowTwoSide.Interaction.Trade"}


def test_the_xp_mod_leaves_the_pet_rate_alone(tmp_path: Path) -> None:
    """`Rate.Pet.XP.Kill` ships two lines above `Rate.XP.Kill` in THIS conf.

    An unanchored `Rate\\.XP\\.Kill` matches it, and a pet XP rate silently
    changed by an XP mod is the kind of defect nobody attributes to the launcher.
    The anchoring is in the manifest; this is the test that keeps it there.
    """
    server_dir = _server_dir_with_conf(tmp_path)
    manifest = vanilla_modules.store().load("mod", "xp-rates")
    applier = Applier(server_dir, sql=_RecordingSql())
    applier.install(manifest, {"kill": "5", "quest": "5", "explore": "5"})

    text = (server_dir / "etc" / "mangosd.conf").read_text(encoding="utf-8")
    assert "Rate.Pet.XP.Kill = 1" in text, "the pet rate was rewritten by the XP mod"
    assert "Rate.XP.Kill    = 5" in text or "Rate.XP.Kill = 5" in text

    applier.remove(manifest)
    assert "Rate.Pet.XP.Kill = 1" in (server_dir / "etc" / "mangosd.conf").read_text(
        encoding="utf-8"
    )


def test_the_sql_mod_runs_inline_against_this_games_world_schema(tmp_path: Path) -> None:
    """A SQL mod on CMaNGOS is inline SQL, and it carries its own undo.

    `item_template.stackable` is `smallint(5) unsigned` in this tree's own base
    schema (`src/mangos-classic/sql/base/mangos.sql:2682`), so the column the
    statement names is this fork's and not a sibling's.
    """
    server_dir = _server_dir_with_conf(tmp_path)
    manifest = vanilla_modules.store().load("mod", "all-stackables")
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
    """`sql[].db` says `world`; what `world` MEANS is this entry's, and it is `mangos`."""
    assert ENTRY.schema_map()["world"] == "mangos"
    assert "playerbots" not in ENTRY.schema_map()


def test_the_checked_in_vanilla_manifests_are_the_ones_the_index_lists() -> None:
    """No orphans, no ghosts."""
    root = Path(vanilla_modules.BUNDLED_MANIFESTS_DIR) / GAME
    listed = set(json.loads((root / "mods.json").read_text(encoding="utf-8"))["items"])
    on_disk = {p.stem for p in (root / "mods").glob("*.json")}
    assert listed == on_disk


@pytest.mark.parametrize("kind", sorted(FAMILY_FILES))
def test_the_fetcher_knows_where_to_refresh_each_family_from(kind: str) -> None:
    """The refresh URL is per-game data, and a wrong one silently mirrors TBC's tree."""
    files = vanilla_modules.store().relative_files(kind)  # type: ignore[arg-type]
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
    applier = vanilla_modules.applier(tmp_path, sql=sql, world_running=lambda: True)

    with pytest.raises(ApplyError) as caught:
        applier.install(parse_manifest(_WORLD_SQL_MOD))

    assert "the world server is running" in str(caught.value)
    assert sql.statements == [] and sql.files == []


def test_this_games_applier_starts_the_database_once_the_world_is_stopped(tmp_path: Path) -> None:
    """The other half of T7, through the same factory: Stop, then install, and it works.

    This game's Stop takes the database down with the world, so a direct SQL
    step on a stopped stack used to die on `container ... is not running`
    (`bug-checklist §46`, filed against this family). The seam puts
    the database back alone; the world is not started.

    Catches `start_database` accepted by the factory and dropped, which would
    leave this game with the guard armed and no way to obey it.
    """
    sql = _RecordingSql()
    started: list[int] = []

    def start_db() -> bool:
        started.append(1)
        return True

    applier = vanilla_modules.applier(
        tmp_path, sql=sql, world_running=lambda: False, start_database=start_db
    )

    report = applier.install(parse_manifest(_WORLD_SQL_MOD))

    assert started == [1]
    assert "started the database alone; the world server was left stopped" in report.done
    assert sql.statements == [("world", "UPDATE item_template SET stackable = 200")]
