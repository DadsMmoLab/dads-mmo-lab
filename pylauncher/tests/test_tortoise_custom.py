"""T596 step 2 (PR-A): what Tortoise takes from a link or a folder, read without Qt or Docker.

Two kinds install now: a client add-on (a folder whose `.toc` has the folder's
name), and a database package (`.sql` files in `data/sql/auth|character|world`,
the core's own module layout). A server module (C++) is a later Yu'lon: it is
refused BY NAME before any clone (`mod-…`/`tw-mod-…`, the names the shared
Tortoise modules carry) and by CONTENT after one (C++ in `src/`).

The name decides the kind because a link's contents are unknown until it is
cloned and the kind decides where it is cloned to (`apply.CLONE_DIRS`).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from yulon import module_source
from yulon.apply import ApplyRefusal, CompletionRefused
from yulon.controller_wow_tortoise import custom
from yulon.manifest import Manifest
from yulon.module_source import DeriveError

GAME = "wow-tortoise"
TODAY = date(2026, 10, 9)
NOTHING = "Nothing on this machine was changed."
SHIPPED_ADDONS = {"tortoisebotsmanager": "TortoiseBots Manager (client addon)"}


def _link(text: str, shipped: tuple[str, ...] = ()) -> Manifest:
    return module_source.derive_link(
        text, GAME, today=TODAY, shipped_ids=shipped, layout=custom.LAYOUT
    )


def _folder(path: Path) -> Manifest:
    return module_source.derive_folder(path, GAME, today=TODAY, layout=custom.LAYOUT)


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _complete(manifest: Manifest, clone: Path) -> Manifest:
    return custom.complete(manifest, clone, shipped_addons=SHIPPED_ADDONS)


def _refused_completion(manifest: Manifest, clone: Path) -> str:
    with pytest.raises(CompletionRefused) as refused:
        _complete(manifest, clone)
    return str(refused.value)


VANILLA_TOC = "## Interface: 11200\n## Title: MobStats\nMobStats.lua\n"


# ------------------------------------------------------------------ the name


@pytest.mark.parametrize(
    ("link", "item_id", "name"),
    [
        ("refaim/MobStats", "mobstats", "MobStats"),
        ("https://github.com/shagu/pfUI.git", "pfui", "pfUI"),
        ("https://github.com/you/My_Addon.v2/", "my-addon-v2", "My_Addon.v2"),
        ("you/bot-gear-sql", "bot-gear-sql", "bot-gear-sql"),
        ("you/turtle-wow-mods", "turtle-wow-mods", "turtle-wow-mods"),
    ],
)
def test_any_other_name_is_a_mod_cloned_beside_the_shipped_addons(
    link: str, item_id: str, name: str
) -> None:
    manifest = _link(link)
    assert (manifest.type, manifest.id, manifest.name) == ("mod", item_id, name)
    assert manifest.game == GAME
    assert manifest.build.rebuild is False and manifest.build.restart is False
    assert manifest.origin is not None and manifest.origin.kind == "link"
    assert manifest.source is not None
    assert manifest.sql == () and manifest.client == ()


@pytest.mark.parametrize(
    "repo",
    [
        "dr1s/tw-mod-hearthstone-cooldown",
        "trikkizerg/mod-twow-bot-gear",
        "Ildourol/mod-camps-twow",
        "Penqle/tw-mod-autoscale",
        "you/TW-Mod-A",
        "https://github.com/you/Mod-Thing.git",
    ],
)
def test_a_server_module_name_is_refused_for_now_with_nothing_cloned(repo: str) -> None:
    with pytest.raises(DeriveError) as refused:
        _link(repo)
    said = str(refused.value)
    assert "server module" in said and "later Yu'lon" in said
    assert said.endswith(NOTHING)


@pytest.mark.parametrize("repo", ["you/___", "you/" + "a" * 70])
def test_a_name_that_gives_no_usable_id_is_refused(repo: str) -> None:
    with pytest.raises(DeriveError) as refused:
        _link(repo)
    assert str(refused.value).endswith(NOTHING)


def test_a_link_to_a_shipped_mod_says_to_use_its_row() -> None:
    with pytest.raises(DeriveError) as refused:
        _link("Sagiroth/tortoise-bots-manager", shipped=("tortoise-bots-manager",))
    assert "already ships" in str(refused.value)


# ------------------------------------------------------------------ a folder


def test_a_folder_is_read_before_it_is_copied_and_an_addon_folder_is_taken(
    tmp_path: Path,
) -> None:
    folder = _tree(tmp_path / "MobStats", {"MobStats.toc": VANILLA_TOC, "src/a.lua": "x = 1\n"})
    manifest = _folder(folder)
    assert (manifest.type, manifest.id, manifest.source) == ("mod", "mobstats", None)
    assert manifest.origin is not None and manifest.origin.path == str(folder)


@pytest.mark.parametrize(
    ("files", "said"),
    [
        ({"README.md": "hi\n"}, "nothing in"),
        ({"src/core.cpp": "int x;\n", "MobStats.toc": VANILLA_TOC}, "C++"),
        ({"MobStats.toc": "## Interface: 30300\n"}, "30300"),
    ],
)
def test_a_folder_yu_lon_cannot_install_is_refused_before_the_copy(
    tmp_path: Path, files: dict[str, str], said: str
) -> None:
    folder = _tree(tmp_path / "MobStats", files)
    with pytest.raises(DeriveError) as refused:
        _folder(folder)
    assert said in str(refused.value)
    assert str(refused.value).endswith(NOTHING)


def test_a_folder_named_like_a_server_module_is_refused(tmp_path: Path) -> None:
    folder = _tree(tmp_path / "tw-mod-x", {"data/sql/world/a.sql": "DELETE FROM x;\n"})
    with pytest.raises(DeriveError, match="later Yu'lon"):
        _folder(folder)


# ------------------------------------------------------------------ completing


def test_an_addon_at_the_top_is_copied_whole_under_its_toc_name(tmp_path: Path) -> None:
    """MobStats: root `MobStats.toc`, and a Lua `src/` that is not C++ and passes."""
    clone = _tree(tmp_path / "c", {"MobStats.toc": VANILLA_TOC, "src/core.lua": "x = 1\n"})
    done = _complete(_link("refaim/MobStats"), clone)
    assert [(c.src, c.dest, c.name) for c in done.client] == [(".", "addons", "MobStats")]
    assert done.sql == ()
    assert (done.id, done.type, done.game) == ("mobstats", "mod", GAME)


def test_of_two_top_tocs_the_one_for_another_game_version_is_not_the_addon(
    tmp_path: Path,
) -> None:
    clone = _tree(
        tmp_path / "c",
        {"pfUI.toc": "## Interface: 11200\n", "pfUI-tbc.toc": "## Interface: 20400\n"},
    )
    # A repository named otherwise, so the variant rule and not the name picks it.
    done = _complete(_link("shagu/pfui-vanilla-fork"), clone)
    assert [(c.src, c.name) for c in done.client] == [(".", "pfUI")]


def test_a_database_package_runs_each_flat_file_in_the_cores_order_and_records_it(
    tmp_path: Path,
) -> None:
    clone = _tree(
        tmp_path / "c",
        {
            "data/sql/world/b.sql": "DELETE FROM b;\n",
            "data/sql/world/a.sql": "DELETE FROM a;\n",
            "data/sql/character/20260915090000_char.sql": "DELETE FROM c;\n",
            "data/sql/world/old/c.sql": "DELETE FROM c;\n",
            "sql/loose.sql": "DELETE FROM d;\n",
            "conf/bot_gear.conf.dist": "[BotGear]\n",
        },
    )
    done = _complete(_link("you/bot-gear-sql"), clone)
    assert [(s.db, s.path, s.migration_module, s.applied_by) for s in done.sql] == [
        ("characters", "data/sql/character/20260915090000_char.sql", "bot-gear-sql", "direct"),
        ("world", "data/sql/world/a.sql", "bot-gear-sql", "direct"),
        ("world", "data/sql/world/b.sql", "bot-gear-sql", "direct"),
    ]
    assert done.client == ()
    said = " ".join(custom.unused(done))
    assert "data/sql/world/old/c.sql" in said and "sql/loose.sql" in said
    assert "conf/" in said


def test_an_addon_under_addon_beside_world_sql_gives_both(tmp_path: Path) -> None:
    """The camps shape without its C++: `addon/TurtleCamps/TurtleCamps.toc` plus world SQL."""
    clone = _tree(
        tmp_path / "c",
        {
            "addon/TurtleCamps/TurtleCamps.toc": "## Interface: 11200\n",
            "data/sql/world/camps.sql": "DELETE FROM camps;\n",
        },
    )
    done = _complete(_link("you/camps-twow-data"), clone)
    assert [(c.src, c.dest, c.name) for c in done.client] == [
        ("addon/TurtleCamps", "addons", "TurtleCamps")
    ]
    assert [s.path for s in done.sql] == ["data/sql/world/camps.sql"]


def test_an_addon_in_a_top_folder_of_its_own_name_is_found(tmp_path: Path) -> None:
    clone = _tree(tmp_path / "c", {"ShaguTweaks/ShaguTweaks.toc": "## Interface: 11200\n"})
    done = _complete(_link("shagu/ShaguTweaks-pack"), clone)
    assert [(c.src, c.name) for c in done.client] == [("ShaguTweaks", "ShaguTweaks")]


@pytest.mark.parametrize(
    ("files", "said"),
    [
        ({"src/core.cpp": "int x;\n", "data/sql/world/a.sql": "DELETE FROM a;\n"}, "C++"),
        ({"Src/core.CPP": "int x;\n", "data/sql/world/a.sql": "DELETE FROM a;\n"}, "C++"),
        ({"mod.cpp": "int x;\n", "Addon.toc": "## Interface: 11200\n"}, "C++"),
        ({"server/inc/gear.h": "int x;\n", "data/sql/world/a.sql": "DELETE FROM a;\n"}, "C++"),
        ({"Wotlk.toc": "## Interface: 30300\n"}, "30300"),
        ({"sql/only.sql": "DELETE FROM a;\n", "patches/core.patch": "x\n"}, "nothing in"),
        ({}, "nothing in"),
        ({"TortoiseBotsManager.toc": "## Interface: 11200\n"}, "already ships"),
    ],
)
def test_what_tortoise_cannot_take_is_refused_by_the_completion(
    tmp_path: Path, files: dict[str, str], said: str
) -> None:
    clone = _tree(tmp_path / "c", files)
    sentence = _refused_completion(_link("you/some-package"), clone)
    assert said in sentence
    assert "Nothing" not in sentence, "what became of the folder is the applier's to say"


def test_the_completed_manifest_survives_its_own_reload(tmp_path: Path) -> None:
    from yulon.manifest import parse_manifest

    clone = _tree(
        tmp_path / "c",
        {"MobStats.toc": VANILLA_TOC, "data/sql/auth/a.sql": "DELETE FROM a;\n"},
    )
    done = _complete(_link("refaim/MobStats"), clone)
    assert parse_manifest(done.model_dump()) == done


# ------------------------------------------------------------------ the binding


from dataclasses import dataclass  # noqa: E402

from yulon.controller_wow_tortoise import autoupdate  # noqa: E402
from yulon.controller_wow_tortoise import modules as tortoise_modules  # noqa: E402
from yulon.controller_wow_wotlk.maintenance import BackupReport, Dump, backups_dir  # noqa: E402
from yulon.git import CloneSpec  # noqa: E402
from yulon.manifest import Db  # noqa: E402

CHAR_SQL = "DELETE FROM bot_gear WHERE guid = 7;\n"


class _Clone:
    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    def clone(self, spec: CloneSpec) -> None:
        _tree(spec.dest, self.files)
        (spec.dest / ".git").mkdir()


class _Db:
    """Reads like `DockerSql.query()` (an empty migrations ledger) and records every write."""

    def __init__(self) -> None:
        self.sent: list[tuple[Db, str]] = []

    def query(self, db: Db, statement: str) -> str:
        return "0\n" if "information_schema" in statement else ""

    def run_file(self, db: Db, path: Path) -> None:
        self.sent.append((db, path.name))

    def run_statement(self, db: Db, statement: str) -> None:
        self.sent.append((db, statement))


@dataclass
class _Take:
    """`maintenance.backup(only=..., label=...)`: writes the file names a real one would."""

    server_dir: Path
    stamp: str = "20261009_210000"

    def __call__(self, only: object, label: str) -> BackupReport:
        folder = backups_dir(self.server_dir)
        folder.mkdir(parents=True, exist_ok=True)
        dumps = []
        for schema in list(only):  # type: ignore[call-overload]
            path = folder / f"{self.stamp}_{label}_{schema}.sql"
            path.write_text("-- dump\n", encoding="utf-8")
            dumps.append(Dump(database=schema, path=path, size_bytes=8))
        return BackupReport(directory=folder, dumps=tuple(dumps))


def _tortoise_applier(
    server: Path, git: _Clone, sql: _Db, client: Path
) -> autoupdate.GuardedApplier:
    applier = tortoise_modules.applier(
        server,
        sql=sql,
        arming=lambda: autoupdate.Arming(enabled=False),
        world_running=lambda: False,
        git=git,  # type: ignore[arg-type]
        client_dir=client,
        sql_backup=tortoise_modules.OutsideSqlBackup(
            server,
            _Take(server),
            {"auth": "tw_logon", "characters": "tw_char", "world": "tw_world"},
        ),
    )
    return applier


def test_an_outside_package_installs_records_backs_up_and_is_listed(tmp_path: Path) -> None:
    """The whole route over the tab's guarded applier: derive, clone, complete, back up, run."""
    server, client = tmp_path / "server", tmp_path / "client"
    (client / "Interface" / "AddOns").mkdir(parents=True)
    server.mkdir()
    git = _Clone(
        {
            "GearPanel/GearPanel.toc": "## Interface: 11200\n",
            "data/sql/character/20260915090000_char.sql": CHAR_SQL,
            "data/sql/world/sub/old.sql": "DELETE FROM x;\n",
        }
    )
    sql = _Db()
    applier = _tortoise_applier(server, git, sql, client)
    manifest = tortoise_modules.derive_link("https://github.com/you/bot-gear-pack")

    report = tortoise_modules.install_custom(applier)(manifest, None)

    assert report.done[0].startswith("clone ")
    assert any(
        line.startswith("backed up tw_char before bot-gear-pack's database changes: ")
        and "20261009_210000_before-bot-gear-pack_tw_char.sql" in line
        for line in report.done
    ), report.done
    assert [db for db, _ in sql.sent] == ["characters"]
    assert "INSERT INTO `migrations`" in sql.sent[0][1]
    assert (client / "Interface" / "AddOns" / "GearPanel" / "GearPanel.toc").is_file()
    assert any("data/sql/world/sub/old.sql" in line for line in report.skipped), report.skipped
    listed = {m.id: m for m in tortoise_modules.store().load_all("mod")}
    assert listed["bot-gear-pack"].sql[0].migration_module == "bot-gear-pack"

    removed = applier.remove(listed["bot-gear-pack"])
    assert any(
        "20261009_210000_before-bot-gear-pack_tw_char.sql" in line
        and "restoring it" in line
        and "undo" not in line
        and "a table the item added is not in it and stays" in line
        for line in removed.left_behind
    ), removed.left_behind
    assert tortoise_modules.forget(listed["bot-gear-pack"]) is True
    assert "bot-gear-pack" not in {m.id for m in tortoise_modules.store().load_all("mod")}


def test_a_shipped_tortoise_mod_takes_no_backup_and_names_none() -> None:
    backup = tortoise_modules.OutsideSqlBackup(Path("/nowhere"), _Take(Path("/nowhere")), {})
    shipped = next(m for m in tortoise_modules.store().load_all("mod") if m.sql)
    assert shipped.origin is None
    assert backup.before(shipped, ("world",)) is None
    assert backup.named(shipped) is None


def test_a_package_carrying_a_shipped_addon_is_refused_and_its_first_clone_taken_back(
    tmp_path: Path,
) -> None:
    server, client = tmp_path / "server", tmp_path / "client"
    server.mkdir()
    git = _Clone({"TortoiseBotsManager.toc": "## Interface: 11200\n"})
    applier = _tortoise_applier(server, git, _Db(), client)
    manifest = tortoise_modules.derive_link("you/TortoiseBotsManager-fork")
    with pytest.raises(CompletionRefused) as refused:
        tortoise_modules.install_custom(applier)(manifest, None)
    assert "already ships" in str(refused.value)
    assert str(refused.value).endswith("Nothing was changed.")
    assert not (server / "sql_scripts" / "clones" / manifest.id).exists()
    assert manifest.id not in {m.id for m in tortoise_modules.store().load_all("mod")}


def test_remove_says_when_no_backup_can_be_found(tmp_path: Path) -> None:
    backup = tortoise_modules.OutsideSqlBackup(tmp_path, _Take(tmp_path), {})
    manifest = _link("you/bot-gear-pack")
    said = backup.named(manifest)
    assert said is not None and said.startswith("no backup taken before its database changes")


def test_remove_names_the_first_backup_and_counts_the_later_ones(tmp_path: Path) -> None:
    manifest = _link("you/bot-gear-pack")
    _Take(tmp_path, "20261009_210000")(["tw_char"], "before-bot-gear-pack")
    _Take(tmp_path, "20261010_090000")(["tw_char", "tw_world"], "before-bot-gear-pack")
    _Take(tmp_path, "20261008_090000")(["tw_char"], "before-bot-gear-pack-two")
    backup = tortoise_modules.OutsideSqlBackup(tmp_path, _Take(tmp_path), {})
    said = backup.named(manifest)
    assert said is not None
    assert "20261009_210000_before-bot-gear-pack_tw_char.sql" in said
    assert "20261010" not in said and "-two" not in said
    assert "1 later backup(s)" in said


def test_a_folder_carrying_a_shipped_addon_is_refused_before_the_copy(tmp_path: Path) -> None:
    folder = _tree(tmp_path / "TBM-fork", {"TortoiseBotsManager.toc": "## Interface: 11200\n"})
    with pytest.raises(DeriveError) as refused:
        tortoise_modules.derive_folder(folder)
    assert "already ships" in str(refused.value) and str(refused.value).endswith(NOTHING)


def test_a_first_install_refused_by_a_running_world_leaves_no_folder_and_no_record(
    tmp_path: Path,
) -> None:
    """Codex review: the record `complete()` persisted goes with the folder taken back."""
    server, client = tmp_path / "server", tmp_path / "client"
    server.mkdir()
    git = _Clone({"data/sql/character/20260915090000_char.sql": CHAR_SQL})
    sql = _Db()
    applier = tortoise_modules.applier(
        server,
        sql=sql,
        arming=lambda: autoupdate.Arming(enabled=False),
        world_running=lambda: True,
        git=git,  # type: ignore[arg-type]
        client_dir=client,
    )
    manifest = tortoise_modules.derive_link("you/bot-gear-pack")
    with pytest.raises(ApplyRefusal) as refused:
        tortoise_modules.install_custom(applier)(manifest, None)
    assert "world server is running" in str(refused.value)
    assert sql.sent == []
    assert not (server / "sql_scripts" / "clones" / "bot-gear-pack").exists()
    assert "bot-gear-pack" not in {m.id for m in tortoise_modules.store().load_all("mod")}


def test_a_failed_install_keeps_a_tortoise_record_from_an_earlier_press(tmp_path: Path) -> None:
    from yulon.apply import ApplyError
    from yulon.git import GitError

    class _Unreachable:
        def clone(self, spec: CloneSpec) -> None:
            raise GitError("could not reach github.com")

    server = tmp_path / "server"
    server.mkdir()
    manifest = tortoise_modules.derive_link("you/bot-gear-pack")
    module_source.persist(
        tortoise_modules.user_manifests_dir(), manifest, shipped_ids=tortoise_modules.shipped_ids()
    )
    applier = _tortoise_applier(server, _Unreachable(), _Db(), tmp_path / "client")  # type: ignore[arg-type]
    with pytest.raises(ApplyError):
        tortoise_modules.install_custom(applier)(manifest, None)
    assert "bot-gear-pack" in {m.id for m in tortoise_modules.store().load_all("mod")}
