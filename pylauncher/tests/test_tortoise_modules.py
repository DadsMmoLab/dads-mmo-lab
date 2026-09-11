"""Tortoise modules, and the one guard this fork needs that no sibling does (roadmap 8.7d).

The first half is 8.7b's shape against this tree's own facts: a "module" here is
a **configuration activation** (`conf[].keys` into `etc/mangosd.conf`) or a
**SQL mod** (`sql[].statement` against `tw_world`), because this fork compiles
no loadable modules either.

The second half is the clause that belongs to this box alone —

    *nothing may run this fork's own database auto-update path while the world
    is up* (`pyplan/checklist.md:2504`)

— and it is not a nicety. Measured on m910q, 2026-09-08, from this fork's own
source at `~/tortoise-server/src/tortoise-wow`:

* `World::SetInitialWorldSettings()` calls `sAutoUpdater.ProcessUpdates()` and
  on a false answer logs `DB AutoUpdater FAILED, cancelling server.` and calls
  `exit(1)` — `src/game/World.cpp:1988-1994`. The updater IS the worldserver,
  at startup, and one bad migration cancels the WHOLE world.
* AzerothCore's `applied_by="db-import"` defers to `ac-db-import`, a SEPARATE
  one-shot container that can fail on its own without taking a world down.
  Spelling the same word on this tree means something else entirely.
* `ProcessUpdates()` is switched by `Database.AutoUpdate.Enabled`
  (`src/shared/Database/AutoUpdater.cpp:493`), read with `GetBoolDefault(...,
  true)` — **an absent key means ENABLED**, which is the trap a guard that
  treats "not written down" as "off" walks into.
* A migration is keyed by `<module>:<sha1-of-the-file>` against the `migrations`
  table of its own database (`AutoUpdater.cpp:83-86`, `:133-172`, `:443`), so
  "what would run at the next start" is computable from outside the server:
  the `*.sql` files in the update folder, minus the hashes the ledger holds.

The night before this box was written, rebuilding this fork onto its own head
crash-looped the worldserver twice on exactly that path
(`pyplan/phase8-owner-answers-2026-09-08.md` §1). The install button is what
asks for the restart that re-enters it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from yulon.apply import Applier, ApplyError
from yulon.catalog.catalog import load_catalog
from yulon.controller_wow_tortoise import autoupdate
from yulon.controller_wow_tortoise import modules as tortoise_modules
from yulon.controller_wow_wotlk import modules as wotlk_modules
from yulon.manifest import Db, Manifest, parse_manifest
from yulon.manifest_store import FAMILY_FILES

ENTRY = load_catalog().get("wow-tortoise")
GAME = "wow-tortoise"


def _mods() -> list[Manifest]:
    return list(tortoise_modules.store().load_all("mod"))


# The lines this fork ships, copied off the image's own
# `/opt/tortoise/etc/mangosd.conf` on m910q 2026-09-08 (line numbers in the
# comments). A patch or a key write that only worked against a file this test
# invented would not pass here.
SHIPPED_CONF = (
    "[MangosdConf]\n"
    "ConfVersion = 2020032001\n"
    "\n"
    "# DB Auto-updater\n"
    "\n"
    "Database.AutoUpdate.Enabled = 1\n"  # :47
    'Database.AutoUpdate.Path = "/opt/tortoise/sql/database_updates/"\n'  # :48
    'Database.AutoUpdate.AuthUpdateName = "auth"\n'  # :49
    'Database.AutoUpdate.CharUpdateName = "character"\n'  # :50
    'Database.AutoUpdate.WorldUpdateName = "world"\n'  # :51
    'Database.AutoUpdate.AllowedModules = "all"\n'  # :56
    "Database.AutoUpdate.SortByName = 1\n"  # :62
    "\n"
    'Motd = "Welcome to Turtle WoW! @/join world to connect with the community around you!"\n'
    "\n"
    "Rate.XP.Kill = 1\n"  # :1757
    "Rate.XP.Quest = 1\n"  # :1758
    "Rate.XP.Explore = 1\n"  # :1759
    "\n"
    "# Performance tracking\n"
    "Perf.Enable = 1\n"  # :2193
    "Perf.ReportInterval = 600\n"  # :2196
)


def _server_dir_with_conf(tmp_path: Path) -> Path:
    etc = tmp_path / "etc"
    etc.mkdir(parents=True)
    (etc / "mangosd.conf").write_text(SHIPPED_CONF, encoding="utf-8", newline="\n")
    (etc / "realmd.conf").write_text("[RealmdConf]\n", encoding="utf-8", newline="\n")
    (etc / "aiplayerbot.conf").write_text("[AiPlayerbot]\n", encoding="utf-8", newline="\n")
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


def _disarmed() -> autoupdate.Arming:
    """The updater as it stood on m910q at gate time: on, and with nothing to do."""
    return autoupdate.Arming(enabled=True, outstanding={"auth": (), "characters": (), "world": ()})


# ------------------------------------------------------------------ binding


def test_the_tortoise_store_serves_tortoise_manifests_and_not_wotlks() -> None:
    """The store is bound to THIS game, which is what `_no_manifest_store()` warned about.

    A tab handed `wotlk_modules.store()` would offer AzerothCore's C++ modules
    for a MaNGOS-Zero fork and every one would fail at the clone or the rebuild.
    """
    store = tortoise_modules.store()
    assert store.game == GAME
    mine = {m.id for m in _mods()}
    assert mine, "the Tortoise mod index is empty; 8.7d needs at least one real manifest"
    theirs = {m.id for m in wotlk_modules.store().load_all("module")}
    assert mine.isdisjoint(theirs)
    for manifest in _mods():
        assert manifest.game == GAME


def test_the_entry_says_this_game_has_manifests_or_the_tab_never_builds_one() -> None:
    """`controller_view._for_tortoise()` gates the store AND the applier on this flag.

    The manifests can be perfect and the tab still empty: `has_manifests`
    defaults to False and is the only thing either side reads.
    """
    assert ENTRY.has_manifests is True


def test_every_family_index_exists_so_the_tab_lists_no_error_line() -> None:
    """`reload_modules()` walks all four families and prints `!!` for a missing file."""
    store = tortoise_modules.store()
    for kind in FAMILY_FILES:
        index = store.load_index(kind)
        assert index.game == GAME and index.type == kind
        if kind != "mod":
            assert index.items == (), f"this fork compiles no {kind}s; the index must be empty"


# ------------------------------------------------------- the relationships


def test_every_conf_file_a_manifest_names_is_one_this_install_actually_has() -> None:
    """`apply._conf()` SKIPS a file that is not there — install would report success."""
    native = ENTRY.install.native
    assert native is not None and native.cmangos is not None
    known = {f"etc/{name}" for name in native.cmangos.conf.files}
    for manifest in _mods():
        for conf in manifest.conf:
            assert conf.file in known, (
                f"{manifest.id} writes {conf.file}, which the Tortoise installer never "
                f"creates; it makes {sorted(known)}"
            )
            assert conf.template is None, f"{manifest.id}: a sourceless mod has no clone to copy"


def test_no_manifest_writes_a_key_the_installer_rewrites_on_every_run() -> None:
    """The installer's own conf table wins, silently, on the next install or repair.

    `Database.AutoUpdate.Path` is IN that table for this entry, which is the
    interesting one: a manifest that tried to move the updater's folder would be
    moved back by the next repair with nothing reporting the conflict.
    """
    native = ENTRY.install.native
    assert native is not None and native.cmangos is not None
    owned = {
        (f"etc/{name}", key)
        for name, block in native.cmangos.conf.files.items()
        for key in block.keys
    }
    assert ("etc/mangosd.conf", "Database.AutoUpdate.Path") in owned, (
        "this assertion's whole point is that the updater's path is installer-owned; "
        "if the entry stopped saying so, the rule below is guarding nothing"
    )
    for manifest in _mods():
        for conf in manifest.conf:
            for key in conf.keys:
                assert (conf.file, key.key) not in owned, (
                    f"{manifest.id} sets {key.key} in {conf.file}, which the installer's own "
                    "conf table rewrites on every install and repair"
                )


def test_no_manifest_names_a_database_this_fork_does_not_have() -> None:
    """`world` here is `tw_world`, not `acore_world` and not `mangos`."""
    have = set(ENTRY.schema_map())
    for manifest in _mods():
        for step in manifest.sql:
            assert step.db in have, (
                f"{manifest.id} runs SQL against {step.db!r}, which wow-tortoise does not "
                f"have (it has {sorted(have)})"
            )
    assert ENTRY.schema_map()["world"] == "tw_world"
    assert "playerbots" not in ENTRY.schema_map()


def test_no_tortoise_manifest_asks_for_a_rebuild_and_a_restart_is_asked_by_whoever_needs_one() -> (
    None
):
    """`build.rebuild` DEFAULTS to True, so an omitted block asks for an hour of compiling.

    `restart` is the other direction and is not universal. It was, while every
    item here wrote a conf key or a `tw_world` row -- mangosd reads `etc/*.conf`
    and loads the world database once, at startup, so an item like that which
    does not ask for a restart lies about when its change takes effect.

    T30 added two items that touch NEITHER: the client addons, whose whole
    content is a `client` step into the user's own `Interface/AddOns`. Restarting
    the worldserver for those would be a lie in the other direction -- it would
    put a running server down for a change no server-side process can see.

    So the rule is derived from what each manifest actually writes rather than
    asserted flat, which is also what stops the next conf item arriving without
    one.
    """
    for manifest in _mods():
        assert manifest.build.rebuild is False, f"{manifest.id}: this fork compiles no modules"
        touches_the_server = bool(
            manifest.conf or manifest.sql or manifest.deploy or manifest.npcs or manifest.patches
        )
        if touches_the_server:
            assert (
                manifest.build.restart is True
            ), f"{manifest.id}: mangosd reads etc/*.conf and loads tw_world once, at startup"
            continue
        assert manifest.client, (
            f"{manifest.id} writes nothing at all -- no conf, no SQL, no deploy, no client "
            "files. An item that changes nothing is a row in a list that does nothing"
        )
        assert manifest.build.restart is False, (
            f"{manifest.id} only copies files into the user's own WoW client; asking for a "
            "worldserver restart would put a running server down for a change no server-side "
            "process can see"
        )


# ------------------------------------------------------------ the real ones


def test_the_perf_manifest_writes_the_key_this_forks_console_reports(tmp_path: Path) -> None:
    """8.7d's definition of done, in the part a test can hold.

    The manifest that proves "its key is reported by the running server" had to
    be chosen for THIS fork rather than inherited from TBC's `motd`: this tree
    has **no `.server motd` command at all** (its `serverCommandTable` is
    `corpses/exit/idlerestart/idleshutdown/info/resetallraids/restart/shutdown`,
    `src/game/Chat/Chat.cpp:700-711`), so TBC's proof does not exist here.

    `.perf intervalreport` with no argument does: it prints
    `Performance report interval is <n>` from
    `sWorld.getConfig(CONFIG_UINT32_PERFORMANCE_REPORT_INTERVAL)`
    (`src/game/Commands/Commands.cpp:19146-19154`), the row is
    `{ "intervalreport", SEC_ADMINISTRATOR, true, ... }` — `true` being
    `allowConsole`, so no client and no logged-in character are needed
    (`Chat.cpp:837`) — and the value is loaded from the conf key
    `Perf.ReportInterval` (`src/game/World.cpp:1575`).
    """
    server_dir = _server_dir_with_conf(tmp_path)
    manifest = tortoise_modules.store().load("mod", "perf-report")
    report = Applier(server_dir, sql=_RecordingSql()).install(manifest, {"interval": "120"})

    text = (server_dir / "etc" / "mangosd.conf").read_text(encoding="utf-8")
    assert "Perf.ReportInterval = 120" in text
    assert "Perf.ReportInterval = 600" not in text
    assert report.skipped == (), report.skipped
    assert report.rebuild_required is False
    assert report.restart_recommended is True, "a conf mod that does not ask for a restart lies"


def test_every_conf_mod_restores_its_shipped_default_on_remove(tmp_path: Path) -> None:
    """Enumerated rather than read: a mod that forgets one `when: "remove"` patch names itself."""
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


def test_the_sql_mod_runs_inline_against_this_forks_world_schema(tmp_path: Path) -> None:
    """A SQL mod here is inline SQL carrying its own undo — there is no repo to clone."""
    server_dir = _server_dir_with_conf(tmp_path)
    manifest = tortoise_modules.store().load("mod", "all-stackables")
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
    assert "UPDATE" in " ".join(s for _, s in sql.statements).upper()


def test_the_checked_in_tortoise_manifests_are_the_ones_the_index_lists() -> None:
    root = Path(tortoise_modules.BUNDLED_MANIFESTS_DIR) / GAME
    listed = set(json.loads((root / "mods.json").read_text(encoding="utf-8"))["items"])
    on_disk = {p.stem for p in (root / "mods").glob("*.json")}
    assert listed == on_disk


@pytest.mark.parametrize("kind", sorted(FAMILY_FILES))
def test_the_fetcher_knows_where_to_refresh_each_family_from(kind: str) -> None:
    files = tortoise_modules.store().relative_files(kind)  # type: ignore[arg-type]
    assert all(f.startswith(f"{GAME}/") for f in files)


# ------------------------------------------ the auto-update guard: reading it


def test_an_absent_enabled_key_reads_as_ENABLED_because_that_is_what_the_fork_does(
    tmp_path: Path,
) -> None:
    """`GetBoolDefault("Database.AutoUpdate.Enabled", true)` — AutoUpdater.cpp:493.

    The whole guard turns on this default. A reader that answered "not written
    down, so off" would wave through the exact configuration the fork runs the
    updater under, and the refusal below would never fire on a stock install.
    """
    etc = tmp_path / "etc"
    etc.mkdir(parents=True)
    (etc / "mangosd.conf").write_text("[MangosdConf]\n", encoding="utf-8", newline="\n")
    settings = autoupdate.read_settings(tmp_path)
    assert settings.enabled is True
    assert settings.declared is False, "the file did not say; the answer is the fork's default"


def test_the_settings_are_read_from_this_installs_own_conf_not_from_a_remembered_default(
    tmp_path: Path,
) -> None:
    server_dir = _server_dir_with_conf(tmp_path)
    settings = autoupdate.read_settings(server_dir)
    assert settings.enabled is True and settings.declared is True
    assert settings.path == "/opt/tortoise/sql/database_updates/"
    # The quoted folder names are this fork's, and they are NOT MaNGOS's
    # compiled defaults (`Logon`/`Char`/`World`, AutoUpdater.cpp:499-501).
    assert settings.folders == {"auth": "auth", "characters": "character", "world": "world"}


def test_a_conf_that_switches_the_updater_off_reads_as_off(tmp_path: Path) -> None:
    server_dir = _server_dir_with_conf(tmp_path)
    conf = server_dir / "etc" / "mangosd.conf"
    conf.write_text(
        conf.read_text(encoding="utf-8").replace(
            "Database.AutoUpdate.Enabled = 1", "Database.AutoUpdate.Enabled = 0"
        ),
        encoding="utf-8",
        newline="\n",
    )
    assert autoupdate.read_settings(server_dir).enabled is False


def test_outstanding_migrations_are_the_files_the_ledger_has_no_hash_for() -> None:
    """The subtraction the fork does at startup, done from outside without starting it.

    Measured on m910q 2026-09-08 against the live pre-rebuild stack: 125 `*.sql`
    files in the image's `world` folder, 158 rows in `tw_world.migrations`, and
    **0 outstanding** — which is exactly why that stack starts. Rows with no
    file are old migrations and are not a problem; files with no row are.
    """
    arming = autoupdate.arming_from(
        enabled=True,
        files={"world": {"a.sql": "aaa", "b.sql": "bbb"}, "auth": {}, "characters": {}},
        applied={"world": {"aaa", "ccc"}, "auth": set(), "characters": set()},
    )
    assert arming.outstanding == {"auth": (), "characters": (), "world": ("b.sql",)}
    assert arming.armed is True

    quiet = autoupdate.arming_from(
        enabled=True,
        files={"world": {"a.sql": "aaa"}, "auth": {}, "characters": {}},
        applied={"world": {"aaa", "ccc"}, "auth": set(), "characters": set()},
    )
    assert quiet.armed is False


def test_a_disabled_updater_is_not_armed_however_many_files_are_waiting() -> None:
    arming = autoupdate.arming_from(
        enabled=False,
        files={"world": {"a.sql": "aaa"}, "auth": {}, "characters": {}},
        applied={"world": set(), "auth": set(), "characters": set()},
    )
    assert arming.armed is False and arming.outstanding == {
        "auth": (),
        "characters": (),
        "world": ("a.sql",),
    }


def test_an_unreadable_updater_is_neither_armed_nor_safe_it_is_UNKNOWN() -> None:
    """Three answers, not two — the shape `PendingSql` and `Ownership` already use here.

    A reader that could not list the folder or could not query `migrations`
    must not answer "nothing outstanding". `migrations` genuinely may not exist
    yet: the updater CREATEs it on its first run (`AutoUpdater.cpp:135`).
    """
    unknown = autoupdate.Arming(enabled=True, outstanding=None, unreadable=("no migrations table",))
    assert unknown.armed is None
    assert unknown.unreadable == ("no migrations table",)


# --------------------------------------- the auto-update guard: refusing with it


def test_no_shipped_tortoise_manifest_defers_sql_to_this_forks_updater() -> None:
    """Checklist 2504, over the set that ships.

    `applied_by="db-import"` on AzerothCore means `ac-db-import`, a separate
    one-shot container. Here the same word means the WORLDSERVER, at startup,
    which `exit(1)`s on one bad file (`World.cpp:1988-1994`).
    """
    for manifest in _mods():
        for step in manifest.sql:
            assert step.applied_by == "direct", (
                f"{manifest.id} defers SQL to this fork's auto-updater; the updater is the "
                "worldserver itself and it cancels the whole world on one failure"
            )


def test_a_manifest_that_defers_to_the_updater_is_refused_at_the_applier(tmp_path: Path) -> None:
    """The rule above holds the SHIPPED set; this holds a refreshed or hand-written one.

    `ManifestFetcher` mirrors these from GitHub into a cache the store then
    reads, so the file the applier is handed at runtime is not necessarily the
    file this checkout ships. A test over the checkout cannot see that one.
    """
    server_dir = _server_dir_with_conf(tmp_path)
    smuggled = parse_manifest(
        {
            "schema_version": 1,
            "id": "smuggled",
            "name": "Smuggled",
            "type": "mod",
            "game": GAME,
            "build": {"rebuild": False, "restart": True},
            "sql": [{"db": "world", "path": "sql/*.sql", "applied_by": "db-import"}],
        }
    )
    applier = tortoise_modules.applier(
        server_dir, sql=_RecordingSql(), arming=lambda: _disarmed(), world_running=lambda: True
    )
    with pytest.raises(ApplyError) as caught:
        applier.install(smuggled)
    assert "auto-update" in str(caught.value).lower()
    assert "smuggled" in str(caught.value)


def test_an_install_is_refused_while_the_updater_is_armed_and_the_world_is_up(
    tmp_path: Path,
) -> None:
    """The clause itself: the restart this install asks for is what runs the updater.

    Ground and refusal in one test, because a refusal that would have fired
    anyway proves nothing: the SAME manifest, the SAME applier and the SAME
    world install once cleanly and are then refused once the updater is armed.
    """
    server_dir = _server_dir_with_conf(tmp_path)
    manifest = tortoise_modules.store().load("mod", "perf-report")
    conf = server_dir / "etc" / "mangosd.conf"

    armed = autoupdate.Arming(
        enabled=True, outstanding={"auth": (), "characters": (), "world": ("2026_a.sql",)}
    )
    state: list[autoupdate.Arming] = [_disarmed()]
    applier = tortoise_modules.applier(
        server_dir, sql=_RecordingSql(), arming=lambda: state[0], world_running=lambda: True
    )

    assert applier.install(manifest, {"interval": "120"}).restart_recommended is True
    applier.remove(manifest)
    settled = conf.read_text(encoding="utf-8")

    state[0] = armed
    with pytest.raises(autoupdate.AutoUpdateRefused) as caught:
        applier.install(manifest, {"interval": "120"})
    message = str(caught.value)
    assert "2026_a.sql" in message, "the refusal must name what would run"
    assert "Database.AutoUpdate.Enabled" in message, "and the switch that stops it"
    assert conf.read_text(encoding="utf-8") == settled, "a refused install must write nothing"


def test_the_same_install_is_allowed_once_the_world_is_down(tmp_path: Path) -> None:
    """ "While the world is up" is the clause's own scope, and it is load-bearing.

    With the world already stopped there is no running world for the updater to
    cancel; the operator is starting one, and the start's own log is where they
    find out. Refusing here would block every module install on a stopped
    server, which is when most of them are done.
    """
    server_dir = _server_dir_with_conf(tmp_path)
    manifest = tortoise_modules.store().load("mod", "perf-report")
    armed = autoupdate.Arming(
        enabled=True, outstanding={"auth": (), "characters": (), "world": ("2026_a.sql",)}
    )
    applier = tortoise_modules.applier(
        server_dir, sql=_RecordingSql(), arming=lambda: armed, world_running=lambda: False
    )
    report = applier.install(manifest, {"interval": "120"})
    assert "Perf.ReportInterval = 120" in (server_dir / "etc" / "mangosd.conf").read_text(
        encoding="utf-8"
    )
    assert any(
        "auto-update" in line.lower() for line in report.done + report.skipped
    ), "the world being down is why this was allowed; the report has to say so"


def test_an_unreadable_updater_refuses_rather_than_assuming_the_best(tmp_path: Path) -> None:
    """A guard that cannot see must not answer "nothing to worry about".

    This is the same rule `pyplan` records as *guards that prove declarations*:
    the refusal has to be over what was measured, and "could not measure" is not
    a measurement of zero.
    """
    server_dir = _server_dir_with_conf(tmp_path)
    manifest = tortoise_modules.store().load("mod", "perf-report")
    unknown = autoupdate.Arming(
        enabled=True, outstanding=None, unreadable=("tw_world.migrations: no such table",)
    )
    applier = tortoise_modules.applier(
        server_dir, sql=_RecordingSql(), arming=lambda: unknown, world_running=lambda: True
    )
    with pytest.raises(autoupdate.AutoUpdateRefused) as caught:
        applier.install(manifest, {"interval": "120"})
    assert "no such table" in str(caught.value)


def test_remove_is_guarded_too_because_it_asks_for_the_same_restart(tmp_path: Path) -> None:
    """`remove()` puts the shipped value back and asks for a restart to load it.

    The install path was guarded first and the removal path was not, which is
    the shape of defect `pyplan` records as *reviews check functions, not call
    sites*: the fix was applied to one of the three entry points on the object
    the tab actually holds.
    """
    server_dir = _server_dir_with_conf(tmp_path)
    manifest = tortoise_modules.store().load("mod", "perf-report")
    armed = autoupdate.Arming(
        enabled=True, outstanding={"auth": (), "characters": (), "world": ("2026_a.sql",)}
    )
    applier = tortoise_modules.applier(
        server_dir, sql=_RecordingSql(), arming=lambda: armed, world_running=lambda: True
    )
    for action in (applier.remove, applier.configure):
        with pytest.raises(autoupdate.AutoUpdateRefused):
            action(manifest, {"interval": "120"})


def test_the_guard_is_on_the_object_the_tortoise_tab_is_actually_handed(tmp_path: Path) -> None:
    """The wiring, not the class: `_for_tortoise()` must hand over the GUARDED applier.

    `controller_view` builds the services the Server tab holds. An applier built
    correctly in `modules.py` and a plain `Applier(...)` passed from there would
    pass every test above and guard nothing a user ever presses.
    """
    from yulon.ui.controller_view import ControllerServices

    services = ControllerServices.for_entry(ENTRY, tmp_path)
    assert services.store is not None and services.store.game == GAME
    assert isinstance(services.applier, autoupdate.GuardedApplier)


def test_a_folder_and_a_completer_handed_to_the_guarded_applier_reach_the_engine_behind_the_guard(
    tmp_path: Path,
) -> None:
    """`Applier.install` grew `folder=` and `complete=` (module-from-link, lane B); the override
    must carry both THROUGH its guard, not drop them and not route around it.

    Custom modules are wow-wotlk-only, so no folder reaches this class in practice --
    which is why dropping the keywords would be cheap and wrong at once: a
    subclass whose `install()` silently ignores a keyword its base accepts is a
    seam that copies nothing and reports an install. Ground first: with the world
    DOWN the same applier takes both keywords and the guard's note is in the
    report beside the copy; then, armed and UP, the same call is refused before
    the copier is ever asked.
    """
    from yulon.apply import FolderSource

    server_dir = _server_dir_with_conf(tmp_path)
    source = tmp_path / "mod-hand-made"
    (source / "src").mkdir(parents=True)
    copied: list[tuple[Path, Path]] = []
    completed: list[Path] = []

    def copier(src: Path, dest: Path) -> None:
        copied.append((src, dest))
        dest.mkdir(parents=True)
        (dest / "src").mkdir()

    def complete(manifest: Manifest, clone: Path) -> Manifest:
        completed.append(clone)
        return manifest

    manifest = parse_manifest(
        {
            "schema_version": 1,
            "id": "mod-hand-made",
            "name": "mod-hand-made",
            "type": "module",
            "game": GAME,
            "origin": {"kind": "folder", "path": str(source), "added": "2026-09-08"},
            "build": {"rebuild": True, "restart": True},
        }
    )
    armed = autoupdate.Arming(
        enabled=True, outstanding={"auth": (), "characters": (), "world": ("2026_a.sql",)}
    )
    world_up = [False]
    applier = tortoise_modules.applier(
        server_dir, sql=_RecordingSql(), arming=lambda: armed, world_running=lambda: world_up[0]
    )

    report = applier.install(manifest, folder=FolderSource(source, copier), complete=complete)

    clone = server_dir / "modules" / "mod-hand-made"
    assert copied == [(source, clone)]
    assert completed == [clone]
    assert any("auto-update" in line.lower() for line in report.done), report.done
    assert any(line.startswith("copy ") for line in report.done), report.done

    applier.remove(manifest)
    world_up[0] = True
    with pytest.raises(autoupdate.AutoUpdateRefused):
        applier.install(manifest, folder=FolderSource(source, copier), complete=complete)
    assert copied == [(source, clone)], "the refusal must come before the seam is asked"


# ------------------- one world reading, two guards, and neither may be asleep (T7)


_WORLD_SQL_MOD: dict[str, object] = {
    "schema_version": 1,
    "id": "world-sql",
    "name": "World SQL",
    "type": "mod",
    "game": GAME,
    "build": {"rebuild": False, "restart": True},
    "sql": [{"db": "world", "statement": "UPDATE item_template SET stackable = 200"}],
}


def test_the_subclass_hands_the_world_reading_to_the_engines_guard_as_well(
    tmp_path: Path,
) -> None:
    """The defect T2's reviewer found, and the reason it was invisible.

    `Applier` keeps its running-world seam PRIVATE (`_world_running`) precisely
    so this subclass's public `world_running` -- which predates it and answers a
    different guard -- could not wire 8.7a live on this game alone. The cost was
    that `GuardedApplier.__init__` swallowed the keyword into its own attribute
    and passed nothing down, so 8.7a's guard sat at `None` here exactly as on
    the other three games, while 2504's guard beside it worked.

    The updater is DISARMED and the item is otherwise installable, so 2504's
    check permits and anything that refuses here is the engine's own guard.
    Both guards read the seam once, which is what pins them to ONE reading: a
    second, separately-wired reader would let this fork's two guards disagree
    about whether the world is up.

    Catches `world_running=world_running` dropped from the `super().__init__`
    call -- the whole of the bug -- and a second seam wired in beside it.
    """
    server_dir = _server_dir_with_conf(tmp_path)
    sql = _RecordingSql()
    asked: list[int] = []

    def world_running() -> bool:
        asked.append(1)
        return True

    applier = tortoise_modules.applier(
        server_dir, sql=sql, arming=lambda: _disarmed(), world_running=world_running
    )

    with pytest.raises(ApplyError) as caught:
        applier.install(parse_manifest(_WORLD_SQL_MOD))

    assert "the world server is running" in str(caught.value)
    assert sql.statements == [] and sql.files == []
    assert len(asked) == 2, "one reading, asked once by each guard"


def test_a_world_that_cannot_be_read_refuses_here_and_still_permits_the_updater_check(
    tmp_path: Path,
) -> None:
    """`None` reaches this game too, and the two guards answer it differently ON PURPOSE.

    8.7a fails CLOSED: *could not ask* is not *not running*, because `False` is
    the answer that lets SQL into a live world's tables. Checklist 2504's scope
    is *while the world is UP*, and an unreadable inspect used to arrive there
    as `False` through `container_state(...).settled`; `_guard()` narrows the
    widened seam back with `is True` so that behaviour is unchanged by T7.

    Catches the narrowing dropped (2504 would start refusing installs on a host
    whose Docker will not answer, which is not its clause) and the engine's
    `None` branch softened to a permit.
    """
    server_dir = _server_dir_with_conf(tmp_path)
    sql = _RecordingSql()
    armed = autoupdate.Arming(
        enabled=True, outstanding={"auth": (), "characters": (), "world": ("2026_a.sql",)}
    )
    applier = tortoise_modules.applier(
        server_dir, sql=sql, arming=lambda: armed, world_running=lambda: None
    )

    with pytest.raises(ApplyError) as caught:
        applier.install(parse_manifest(_WORLD_SQL_MOD))

    message = str(caught.value)
    assert "could not tell whether the world server is running" in message
    assert "auto-update" not in message.lower(), "2504 reads an unreadable world as down"
    assert sql.statements == [] and sql.files == []


# ------------------------------------------------- T30: the two client addons


class _AddonGit:
    """A `Git` that writes an addon checkout into the clone dir instead of cloning.

    Shaped like the real repositories, whose ROOT is the addon: a `.toc`, the
    Lua files it lists, and the things a git checkout carries that WoW does not
    read -- which is the half worth having in a fake, because the `client` step
    copies the whole checkout.
    """

    def __init__(self, toc: str, files: tuple[str, ...]) -> None:
        self.toc = toc
        self.files = files
        self.specs: list[object] = []

    def clone(self, spec: object) -> None:
        self.specs.append(spec)
        dest = Path(spec.dest)  # type: ignore[attr-defined]
        dest.mkdir(parents=True, exist_ok=True)
        (dest / self.toc).write_text("## Interface: 11200\n", encoding="utf-8")
        for name in self.files:
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("-- lua\n", encoding="utf-8")
        (dest / ".git").mkdir(exist_ok=True)
        (dest / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    def is_unmodified(self, dest: Path, relative_path: str) -> bool | None:
        return None

    def no_local_commits(self, dest: Path, branch: str | None) -> bool | None:
        return None


ADDONS = {
    "tortoise-bots-manager": (
        "TortoiseBotsManager",
        "TortoiseBotsManager.toc",
        ("Core.lua", "Comms.lua", "Roster.lua", "UI.lua"),
    ),
    "tortoise-gm-manager": (
        "TortoiseGMManager",
        "TortoiseGMManager.toc",
        ("Core.lua", "Lookup.lua", "ResultsUI.lua", "assets/icon.tga"),
    ),
}
"""Each addon's folder name, its `.toc`, and a few of the files the `.toc` lists.

Read off the two repositories at the revisions this catalog pins, on
`yulon-arch` 2026-09-11 (T30 Half 1, `10-addons.txt`). `assets/icon.tga` stands
for the one SUBDIRECTORY either addon has, which is why the manifests copy the
checkout rather than a list of files.
"""


@pytest.mark.parametrize("item", sorted(ADDONS))
def test_each_addon_lands_in_the_clients_own_addons_folder_under_its_toc_name(
    item: str, tmp_path: Path
) -> None:
    """The whole of the addons' delivery, through the shipped manifest and nothing else.

    The folder name matters and is not the repository name by accident: WoW
    loads `Interface/AddOns/<Name>/<Name>.toc` and ignores a directory whose
    `.toc` does not match it. The manifest states it (`client[].name`), which is
    why a `src: "."` copy -- whose source directory is called `modules/mod/<id>`
    -- arrives under the right one.

    The subdirectory is asserted because it is the reason these manifests copy
    the checkout instead of listing files: a `client` step whose `src` is a
    directory `copytree`s it, and `assets/` has to arrive as `assets/`.

    Catches the manifests losing `name`, the step pointed at a subdirectory of
    the checkout, and a `dest` other than `addons` (which would put the files in
    `Interface/` or `Data/`, where nothing loads them).
    """
    folder, toc, files = ADDONS[item]
    manifest = tortoise_modules.store().load("mod", item)
    client = tmp_path / "TurtleWoW"
    (client / "Interface" / "AddOns").mkdir(parents=True)
    report = Applier(
        tmp_path / "server",
        git=_AddonGit(toc, files),
        sql=_RecordingSql(),
        client_dir=client,
    ).install(manifest)

    addon = client / "Interface" / "AddOns" / folder
    assert (addon / toc).is_file(), f"{folder} has no .toc; WoW will not load it"
    for name in files:
        assert (addon / name).is_file(), f"{name} did not arrive under {folder}"
    assert report.skipped == (), report.skipped
    assert report.rebuild_required is False
    assert report.restart_recommended is False, (
        "nothing server-side changed; asking for a restart would take a running world down "
        "for files in somebody's game folder"
    )


@pytest.mark.parametrize("item", sorted(ADDONS))
def test_an_addon_install_on_a_tab_with_no_client_dir_is_refused_by_name(
    item: str, tmp_path: Path
) -> None:
    """A record with no client folder skips the step and SAYS which step it skipped.

    `_ApplyEngine._client()` reports "no client dir configured" rather than
    raising, which is right -- the rest of an item still installs -- and is
    exactly why the sentence has to name the source: an addon whose entire
    content is one `client` step would otherwise report a successful install of
    nothing, and the user would go looking for `/tbm` in a client that has never
    had it.

    This is the state a Tortoise tab was in for EVERY manifest until T30: the
    controller built the applier without `client_dir`, so the step could not
    have run on any item. The test that the wiring passes it now lives in
    `test_controller_view.py`.
    """
    _folder, toc, files = ADDONS[item]
    manifest = tortoise_modules.store().load("mod", item)
    report = Applier(tmp_path / "server", git=_AddonGit(toc, files), sql=_RecordingSql()).install(
        manifest
    )
    assert any("no client dir configured" in line for line in report.skipped), report.skipped
    assert any("client" in line for line in report.skipped), report.skipped


@pytest.mark.parametrize("item", sorted(ADDONS))
def test_each_addon_is_pinned_and_carries_no_server_side_step(item: str) -> None:
    """What these two manifests may and may not contain, asserted from the data.

    * **Pinned.** The `.toc`, the file list and the protocol the bots addon
      speaks were all read at one commit; a manifest on a branch tip installs
      whatever those repositories publish next into somebody's game client.
    * **Nothing server-side.** No `sql`, no `conf`, no `deploy`, no `npcs`.
      These are files for the user's own client, and an addon manifest that
      quietly grew a `sql` step would be running SQL against `tw_world` under a
      name that says "client addon".
    * **README §3a**, the piracy fence: the content comes from the item's own
      open-source repository, so `source` is required and is a repo rather than
      anything this app ships.
    """
    manifest = tortoise_modules.store().load("mod", item)
    assert manifest.source is not None, "README §3a: client files come from their own repo"
    assert manifest.source.rev is not None and len(manifest.source.rev) == 40, (
        f"{item} is cloned from {manifest.source.rev!r}; a moving ref puts whatever those "
        "repositories publish next into a user's game client"
    )
    assert manifest.client, f"{item} declares no client files at all"
    assert not manifest.sql and not manifest.conf and not manifest.deploy and not manifest.npcs, (
        f"{item} is a client addon and touches the server: "
        f"sql={manifest.sql} conf={manifest.conf} deploy={manifest.deploy} npcs={manifest.npcs}"
    )
    assert all(step.dest == "addons" for step in manifest.client)
