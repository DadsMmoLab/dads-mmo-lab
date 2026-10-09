"""T596 step 2: what Tortoise takes from a link or a folder, read without Qt or Docker.

Three kinds install: a client add-on (a folder whose `.toc` has the folder's
name), a database package (`.sql` files in `data/sql/auth|character|world`, the
core's own module layout), and (PR-B) a server module: a repository named
exactly `mod-<x>` or `tw-mod-<x>` (lower case, the names the shared Tortoise
modules carry), cloned to `modules/<name>`, compiled into the server at the next
Rebuild. Its `conf/*.conf.dist` is put at `etc/modules/<n>.conf` at once, because
a rebuilt world that finds no such file does not start.

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
        "https://github.com/you/mod-a.git",
        "you/tw-mod-" + "a" * 64,
    ],
)
def test_the_exact_server_module_names_are_a_module_cloned_into_modules(repo: str) -> None:
    manifest = _link(repo)
    name = repo.rstrip("/").removesuffix(".git").rsplit("/", 1)[-1]
    assert (manifest.type, manifest.id, manifest.name) == ("module", name, name)
    assert manifest.game == GAME
    assert manifest.build.rebuild is True and manifest.build.restart is False
    assert manifest.source is not None and manifest.sql == () and manifest.client == ()
    assert manifest.conf == ()


@pytest.mark.parametrize(
    "repo",
    [
        "you/TW-Mod-A",
        "https://github.com/you/Mod-Thing.git",
        "you/tw-mod-",
        "you/mod-",
        "you/mod-Foo",
        "you/mod-a_b",
        "you/tw-mod-a.b",
        "you/Tw-mod-a",
        "you/mod-" + "a" * 65,
    ],
)
def test_a_name_that_is_nearly_a_server_module_name_is_refused_not_taken_as_an_addon(
    repo: str,
) -> None:
    """The loader name comes from the folder (`Add<folder>Scripts`): a renamed one does not link."""
    with pytest.raises(DeriveError) as refused:
        _link(repo)
    said = str(refused.value)
    assert "mod-" in said and "lower case" in said and "tw-mod-" in said
    assert said.endswith(NOTHING)


@pytest.mark.parametrize("repo", ["you/modest-addon", "you/model-pack", "you/stw-mod-a"])
def test_a_name_that_only_starts_like_mod_is_an_addon_name(repo: str) -> None:
    assert _link(repo).type == "mod"


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


def test_a_folder_named_for_a_server_module_is_read_as_one_before_the_copy(
    tmp_path: Path,
) -> None:
    folder = _tree(
        tmp_path / "tw-mod-x",
        {"src/a.cpp": "int x;\n", "conf/tw-mod-x.conf.dist": "[X]\nX.On = 1\n"},
    )
    manifest = _folder(folder)
    assert (manifest.type, manifest.id, manifest.source) == ("module", "tw-mod-x", None)
    assert manifest.build.rebuild is True


def test_a_folder_named_like_a_server_module_with_a_bad_settings_file_is_refused(
    tmp_path: Path,
) -> None:
    folder = _tree(
        tmp_path / "tw-mod-x", {"src/a.cpp": "int x;\n", "conf/x.conf.dist": "X.On = 1\n"}
    )
    with pytest.raises(DeriveError, match=r"x\.conf\.dist") as refused:
        _folder(folder)
    assert str(refused.value).endswith(NOTHING)


def test_a_folder_with_the_wrong_case_of_a_module_name_is_refused(tmp_path: Path) -> None:
    folder = _tree(tmp_path / "TW-Mod-x", {"src/a.cpp": "int x;\n"})
    with pytest.raises(DeriveError, match="lower case"):
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
        ({"src/core.cpp": "int x;\n", "data/sql/world/a.sql": "DELETE FROM a;\n"}, "tw-mod-"),
        ({"Src/core.CPP": "int x;\n", "data/sql/world/a.sql": "DELETE FROM a;\n"}, "tw-mod-"),
        ({"mod.cpp": "int x;\n", "Addon.toc": "## Interface: 11200\n"}, "tw-mod-"),
        ({"server/inc/gear.h": "int x;\n", "data/sql/world/a.sql": "DELETE FROM a;\n"}, "tw-mod-"),
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


# ------------------------------------------------------------------ a server module

HEARTH_CONF = "# Hearthstone cooldown\n[HearthstoneCooldown]\nHearthstone.Cooldown = 60\n"


def _module_link(name: str) -> Manifest:
    return _link(f"you/{name}")


def test_a_server_module_with_its_settings_file_is_rebuilt_and_gets_the_conf_in_place(
    tmp_path: Path,
) -> None:
    """The `dr1s/tw-mod-hearthstone-cooldown` shape: `src/` + `conf/<name>.conf.dist`."""
    clone = _tree(
        tmp_path / "c",
        {
            "src/Hearth.cpp": "int x;\n",
            "src/Hearth.h": "int y;\n",
            "conf/tw-mod-hearthstone-cooldown.conf.dist": HEARTH_CONF,
            "tw-mod-hearthstone-cooldown.cmake": "# x\n",
        },
    )
    done = _complete(_module_link("tw-mod-hearthstone-cooldown"), clone)
    assert (done.type, done.id) == ("module", "tw-mod-hearthstone-cooldown")
    assert [(c.file, c.template, c.keys) for c in done.conf] == [
        (
            "etc/modules/tw-mod-hearthstone-cooldown.conf",
            "conf/tw-mod-hearthstone-cooldown.conf.dist",
            (),
        )
    ]
    assert done.build.rebuild is True and done.build.restart is False
    assert done.sql == () and done.client == ()
    assert custom.unused(done) == ()


def test_each_top_level_conf_dist_is_a_step_named_for_its_own_file(tmp_path: Path) -> None:
    clone = _tree(
        tmp_path / "c",
        {
            "src/a.cpp": "int x;\n",
            "conf/one.conf.dist": "[One]\n",
            "conf/two_b.conf.dist": "[Two]\n",
            "conf/sub/three.conf.dist": "[Three]\n",
            "conf/readme.txt": "hi\n",
        },
    )
    done = _complete(_module_link("mod-two-confs"), clone)
    assert sorted(c.file for c in done.conf) == ["etc/modules/one.conf", "etc/modules/two_b.conf"]


@pytest.mark.parametrize(
    "text",
    ["", "X.On = 1\n", "; only a comment\n", "[unclosed\nX = 1\n", "[]\n", "X = [a]\n"],
)
def test_a_settings_file_with_no_section_line_is_refused_because_the_world_would_not_start(
    tmp_path: Path, text: str
) -> None:
    """`Config::LoadModulesConfigs()` fails a conf with no `[Section]`; mangosd then exits 1."""
    clone = _tree(tmp_path / "c", {"src/a.cpp": "int x;\n", "conf/x.conf.dist": text})
    said = _refused_completion(_module_link("mod-x"), clone)
    assert "conf/x.conf.dist" in said and "[" in said and "start" in said
    assert "Nothing" not in said


@pytest.mark.parametrize("text", ["[S]\r\nX = 1\r\n", "\ufeff[S]\nX = 1\n", "  [S]  \nX=1\n"])
def test_a_section_line_with_windows_endings_a_bom_or_spaces_counts(
    tmp_path: Path, text: str
) -> None:
    clone = _tree(tmp_path / "c", {"src/a.cpp": "int x;\n", "conf/x.conf.dist": text})
    assert [c.file for c in _complete(_module_link("mod-x"), clone).conf] == ["etc/modules/x.conf"]


def test_a_server_module_with_its_own_sql_and_addon_gives_all_three(tmp_path: Path) -> None:
    """The `Ildourol/mod-camps-twow` shape: C++, world SQL and a client add-on."""
    clone = _tree(
        tmp_path / "c",
        {
            "src/Camps.cpp": "int x;\n",
            "conf/mod-camps-twow.conf.dist": "[Camps]\n",
            "data/sql/world/camps.sql": "DELETE FROM camps;\n",
            "addon/TurtleCamps/TurtleCamps.toc": "## Interface: 11200\n",
        },
    )
    done = _complete(_module_link("mod-camps-twow"), clone)
    assert [(s.db, s.path, s.migration_module) for s in done.sql] == [
        ("world", "data/sql/world/camps.sql", "mod-camps-twow")
    ]
    assert [(c.src, c.name) for c in done.client] == [("addon/TurtleCamps", "TurtleCamps")]
    assert [c.file for c in done.conf] == ["etc/modules/mod-camps-twow.conf"]
    assert done.build.rebuild is True


def test_c_plus_plus_outside_src_does_not_stop_a_module_the_core_builds_from_src(
    tmp_path: Path,
) -> None:
    clone = _tree(
        tmp_path / "c", {"src/a.cpp": "int x;\n", "tools/gen.cpp": "int y;\n", "docs/x.h": "z\n"}
    )
    assert _complete(_module_link("mod-tools"), clone).build.rebuild is True


@pytest.mark.parametrize("where", ["Src/a.cpp", "server/a.cpp", "a.cpp", "source/inc/a.h"])
def test_a_module_whose_c_plus_plus_is_not_under_src_is_refused_the_core_would_not_build_it(
    tmp_path: Path, where: str
) -> None:
    """`GetModuleSourceList` takes only `modules/<name>/src/` (`ConfigureModules.cmake:30-52`)."""
    clone = _tree(tmp_path / "c", {where: "int x;\n", "conf/x.conf.dist": "[X]\n"})
    said = _refused_completion(_module_link("mod-x"), clone)
    assert where in said and "src/" in said and "C++" in said


def test_a_module_named_repository_with_no_c_plus_plus_is_a_data_package_not_a_rebuild(
    tmp_path: Path,
) -> None:
    """No `src/` C++ means the core ignores the folder, so nothing is rebuilt for it."""
    clone = _tree(
        tmp_path / "c",
        {
            "data/sql/world/a.sql": "DELETE FROM a;\n",
            "src/gear.lua": "x = 1\n",
            "conf/x.conf.dist": "[X]\n",
        },
    )
    done = _complete(_module_link("mod-bot-gear-sql"), clone)
    assert done.type == "module" and done.build.rebuild is False
    assert done.conf == ()
    assert [s.path for s in done.sql] == ["data/sql/world/a.sql"]
    assert any("conf/" in line for line in custom.unused(done))


def test_a_module_named_repository_with_nothing_yu_lon_reads_is_refused(tmp_path: Path) -> None:
    clone = _tree(tmp_path / "c", {"README.md": "hi\n", "sql/loose.sql": "DELETE FROM a;\n"})
    said = _refused_completion(_module_link("mod-nothing"), clone)
    assert "nothing in" in said and "server module" in said


def test_a_module_cannot_carry_a_shipped_addon_either(tmp_path: Path) -> None:
    clone = _tree(
        tmp_path / "c",
        {"src/a.cpp": "int x;\n", "TortoiseBotsManager.toc": "## Interface: 11200\n"},
    )
    assert "already ships" in _refused_completion(_module_link("mod-fork"), clone)


def test_the_module_manifest_survives_its_own_reload(tmp_path: Path) -> None:
    from yulon.manifest import parse_manifest

    clone = _tree(
        tmp_path / "c",
        {
            "src/a.cpp": "int x;\n",
            "conf/x.conf.dist": "[X]\n",
            "data/sql/character/a.sql": "DELETE FROM a;\n",
        },
    )
    done = _complete(_module_link("mod-x"), clone)
    assert parse_manifest(done.model_dump()) == done


def test_what_is_read_names_all_three_kinds() -> None:
    said = custom.WHAT_IS_READ
    assert "server module" in said and "mod-" in said and "add-on" in said and "data/sql" in said


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


# ------------------------------------------------------------------ a server module, installed

HEARTH = "tw-mod-hearthstone-cooldown"
HEARTH_FILES = {
    "src/Hearth.cpp": "int x;\n",
    f"conf/{HEARTH}.conf.dist": HEARTH_CONF,
}


def test_a_server_module_from_a_link_lands_in_modules_with_its_conf_and_asks_for_a_rebuild(
    tmp_path: Path,
) -> None:
    server, client = tmp_path / "server", tmp_path / "client"
    server.mkdir()
    sql = _Db()
    applier = _tortoise_applier(server, _Clone(HEARTH_FILES), sql, client)
    manifest = tortoise_modules.derive_link(f"https://github.com/dr1s/{HEARTH}")

    report = tortoise_modules.install_custom(applier)(manifest, None)

    assert (server / "modules" / HEARTH / "src" / "Hearth.cpp").is_file()
    conf = server / "etc" / "modules" / f"{HEARTH}.conf"
    assert conf.read_text(encoding="utf-8").startswith("# Hearthstone cooldown")
    assert report.rebuild_required is True
    assert any(f"etc/modules/{HEARTH}.conf" in line for line in report.done), report.done
    assert sql.sent == []
    listed = {m.id: m for m in tortoise_modules.store().load_all("module")}
    assert listed[HEARTH].type == "module"
    assert listed[HEARTH].conf[0].file == f"etc/modules/{HEARTH}.conf"


def test_remove_takes_the_clone_and_the_record_and_keeps_the_conf_and_says_so(
    tmp_path: Path,
) -> None:
    server, client = tmp_path / "server", tmp_path / "client"
    server.mkdir()
    applier = _tortoise_applier(server, _Clone(HEARTH_FILES), _Db(), client)
    manifest = tortoise_modules.derive_link(f"https://github.com/dr1s/{HEARTH}")
    tortoise_modules.install_custom(applier)(manifest, None)
    listed = {m.id: m for m in tortoise_modules.store().load_all("module")}

    removed = applier.remove(listed[HEARTH])

    assert not (server / "modules" / HEARTH).exists()
    assert (server / "etc" / "modules" / f"{HEARTH}.conf").is_file(), "never removed by Remove"
    assert removed.rebuild_required is True
    said = " ".join(removed.left_behind)
    assert f"etc/modules/{HEARTH}.conf" in said
    assert "Rebuild" in said and "kept" in said
    assert tortoise_modules.forget(listed[HEARTH]) is True
    assert HEARTH not in {m.id for m in tortoise_modules.store().load_all("module")}


def test_remove_of_a_shipped_style_item_with_no_conf_adds_no_conf_line(tmp_path: Path) -> None:
    server, client = tmp_path / "server", tmp_path / "client"
    server.mkdir()
    applier = _tortoise_applier(server, _Clone({"src/a.cpp": "int x;\n"}), _Db(), client)
    manifest = tortoise_modules.derive_link("https://github.com/you/mod-no-conf")
    tortoise_modules.install_custom(applier)(manifest, None)
    listed = {m.id: m for m in tortoise_modules.store().load_all("module")}
    removed = applier.remove(listed["mod-no-conf"])
    assert not any("etc/modules" in line for line in removed.left_behind)


def test_a_module_with_a_conf_that_has_no_section_is_taken_back_with_its_folder(
    tmp_path: Path,
) -> None:
    server, client = tmp_path / "server", tmp_path / "client"
    server.mkdir()
    git = _Clone({"src/a.cpp": "int x;\n", "conf/x.conf.dist": "X.On = 1\n"})
    applier = _tortoise_applier(server, git, _Db(), client)
    manifest = tortoise_modules.derive_link("https://github.com/you/mod-x")
    with pytest.raises(CompletionRefused) as refused:
        tortoise_modules.install_custom(applier)(manifest, None)
    assert str(refused.value).endswith("Nothing was changed.")
    assert not (server / "modules" / "mod-x").exists()
    assert not (server / "etc" / "modules").exists()
    assert "mod-x" not in {m.id for m in tortoise_modules.store().load_all("module")}


def test_a_module_with_sql_is_backed_up_ledgered_and_conf_activated_in_one_press(
    tmp_path: Path,
) -> None:
    server, client = tmp_path / "server", tmp_path / "client"
    server.mkdir()
    git = _Clone(
        {
            "src/a.cpp": "int x;\n",
            "conf/mod-twow-bot-gear.conf.dist": "[BotGear]\n",
            "data/sql/character/20260915090000_char.sql": CHAR_SQL,
        }
    )
    sql = _Db()
    applier = _tortoise_applier(server, git, sql, client)
    manifest = tortoise_modules.derive_link("https://github.com/trikkizerg/mod-twow-bot-gear")
    report = tortoise_modules.install_custom(applier)(manifest, None)
    assert [db for db, _ in sql.sent] == ["characters"]
    assert "'mod-twow-bot-gear'" in sql.sent[0][1]
    assert any(
        line.startswith("backed up tw_char before mod-twow-bot-gear") for line in report.done
    )
    assert (server / "etc" / "modules" / "mod-twow-bot-gear.conf").is_file()


def test_a_module_whose_settings_file_another_installed_module_owns_is_refused(
    tmp_path: Path,
) -> None:
    """Codex review: two modules shipping the same `conf/<n>.conf.dist` would share one file."""
    server, client = tmp_path / "server", tmp_path / "client"
    server.mkdir()
    shared = {"src/a.cpp": "int x;\n", "conf/shared.conf.dist": "[Shared]\nOn = 1\n"}
    first = _tortoise_applier(server, _Clone(shared), _Db(), client)
    tortoise_modules.install_custom(first)(tortoise_modules.derive_link("you/mod-first"), None)

    second = _tortoise_applier(server, _Clone(shared), _Db(), client)
    with pytest.raises(CompletionRefused) as refused:
        tortoise_modules.install_custom(second)(
            tortoise_modules.derive_link("you/mod-second"), None
        )

    said = str(refused.value)
    assert "etc/modules/shared.conf" in said and "mod-first" in said
    assert said.endswith("Nothing was changed.")
    assert not (server / "modules" / "mod-second").exists()
    assert "mod-second" not in {m.id for m in tortoise_modules.store().load_all("module")}
    assert (
        (server / "etc" / "modules" / "shared.conf")
        .read_text(encoding="utf-8")
        .startswith("[Shared]")
    )


def test_removing_a_module_and_installing_it_again_keeps_its_edited_conf_and_says_so(
    tmp_path: Path,
) -> None:
    """Remove keeps the file, so the second install meets it: not a clash, and not silent."""
    server, client = tmp_path / "server", tmp_path / "client"
    server.mkdir()
    files = {"src/a.cpp": "int x;\n", "conf/mod-again.conf.dist": "[Again]\nOn = 1\n"}
    applier = _tortoise_applier(server, _Clone(files), _Db(), client)
    manifest = tortoise_modules.derive_link("you/mod-again")
    tortoise_modules.install_custom(applier)(manifest, None)
    conf = server / "etc" / "modules" / "mod-again.conf"
    conf.write_text("[Again]\nOn = 0\n", encoding="utf-8")
    listed = {m.id: m for m in tortoise_modules.store().load_all("module")}
    applier.remove(listed["mod-again"])
    tortoise_modules.forget(listed["mod-again"])

    again = tortoise_modules.install_custom(applier)(
        tortoise_modules.derive_link("you/mod-again"), None
    )

    assert conf.read_text(encoding="utf-8") == "[Again]\nOn = 0\n", "never replaced"
    assert any("etc/modules/mod-again.conf was already there" in line for line in again.skipped)


def test_a_settings_file_nobody_owns_is_kept_and_said(tmp_path: Path) -> None:
    """A hand-made file, or one an earlier Remove kept: not a refusal, but never silent."""
    server, client = tmp_path / "server", tmp_path / "client"
    (server / "etc" / "modules").mkdir(parents=True)
    (server / "etc" / "modules" / "mod-x.conf").write_text("[Mine]\n", encoding="utf-8")
    files = {"src/a.cpp": "int x;\n", "conf/mod-x.conf.dist": "[X]\n"}
    applier = _tortoise_applier(server, _Clone(files), _Db(), client)
    report = tortoise_modules.install_custom(applier)(
        tortoise_modules.derive_link("you/mod-x"), None
    )
    assert (server / "etc" / "modules" / "mod-x.conf").read_text(encoding="utf-8") == "[Mine]\n"
    assert any("was already there" in line and "never replaced" in line for line in report.skipped)


def test_a_recorded_module_whose_folder_is_gone_does_not_hold_its_settings_file(
    tmp_path: Path,
) -> None:
    import shutil

    server, client = tmp_path / "server", tmp_path / "client"
    server.mkdir()
    shared = {"src/a.cpp": "int x;\n", "conf/shared.conf.dist": "[Shared]\n"}
    first = _tortoise_applier(server, _Clone(shared), _Db(), client)
    tortoise_modules.install_custom(first)(tortoise_modules.derive_link("you/mod-first"), None)
    shutil.rmtree(server / "modules" / "mod-first")

    second = _tortoise_applier(server, _Clone(shared), _Db(), client)
    done = tortoise_modules.install_custom(second)(
        tortoise_modules.derive_link("you/mod-second"), None
    )
    assert done.family == "module" and (server / "modules" / "mod-second").is_dir()
