"""Tests for the declarative apply engine (`yulon.apply`, roadmap 2.3).

Everything external is a fake: `_FakeGit` materializes a clone from a dict,
`_FakeSql` records statements/files, and the server dir is a tmp tree. The
point is the manifest → steps contract: deploy, patches with templates, conf
activation + key writes, SQL routing (db-import vs direct vs no runner),
client/DBC seams, and the remove path — plus the "nothing silent" rule:
every step that could not run appears in `ApplyReport.skipped`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from yulon.apply import Applier, ApplyError, DockerSql, RunnerGit, _set_conf_key
from yulon.manifest import parse_manifest

LUA = "env/dist/etc/modules/lua_scripts"


class _FakeGit:
    """Writes `files` (relative path → text) into the clone dir instead of cloning."""

    def __init__(self, files: dict[str, str]) -> None:
        self.files = files
        self.calls: list[tuple[str, Path, str | None, str | None]] = []

    def clone(self, url: str, dest: Path, branch: str | None, sparse_path: str | None) -> None:
        self.calls.append((url, dest, branch, sparse_path))
        for rel, text in self.files.items():
            p = dest / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        (dest / ".git").mkdir(exist_ok=True)


class _FakeSql:
    def __init__(self) -> None:
        self.files: list[tuple[str, str]] = []
        self.statements: list[tuple[str, str]] = []

    def run_file(self, db: str, path: Path) -> None:
        self.files.append((db, path.name))

    def run_statement(self, db: str, statement: str) -> None:
        self.statements.append((db, statement))


class _FakeDbc:
    def __init__(self) -> None:
        self.dirs: list[Path] = []

    def copy_dbc_dir(self, src: Path) -> None:
        self.dirs.append(src)


ALE: dict[str, Any] = {
    "id": "sitmeanrest",
    "name": "Sit Means Rest",
    "type": "ale",
    "game": "wow-wotlk",
    "source": {"repo": "Brytenwally/SitMeansRest"},
    "requires": ["mod-ale"],
    "deploy": [{"src": "SitMeansRest.lua", "dest": f"{LUA}/"}],
    "patches": [
        {
            "file": f"{LUA}/SitMeansRest.lua",
            "find": r"(DURATION\s*=\s*)\d+",
            "replace": r"\g<1>{duration}",
            "regex": True,
            "when": "configure",
        }
    ],
    "sql": [{"db": "characters", "path": "sql/tables.sql"}],
    "prompts": [{"key": "duration", "question": "seconds", "kind": "int", "default": "20"}],
}

MODULE: dict[str, Any] = {
    "id": "mod-ah-bot",
    "name": "AH Bot",
    "type": "module",
    "game": "wow-wotlk",
    "source": {"repo": "azerothcore/mod-ah-bot"},
    "build": {"rebuild": True},
    "sql": [{"db": "world", "path": "data/sql/db-world/*.sql", "applied_by": "db-import"}],
    "conf": [
        {
            "file": "env/dist/etc/modules/mod_ahbot.conf",
            "template": "conf/mod_ahbot.conf.dist",
            "keys": [
                {"key": "AuctionHouseBot.GUID", "default": "{bot_guid}"},
                {"key": "AuctionHouseBot.EnableSeller", "default": "1"},
                {"key": "AuctionHouseBot.NewKey", "default": "7"},
            ],
        }
    ],
    "prompts": [{"key": "bot_guid", "question": "guid", "kind": "int"}],
}


def test_ale_install_deploys_patches_on_configure_and_removes(tmp_path: Path) -> None:
    git = _FakeGit({"SitMeansRest.lua": "local DURATION = 5\n", "sql/tables.sql": "CREATE ..."})
    sql = _FakeSql()
    applier = Applier(tmp_path, git=git, sql=sql)
    m = parse_manifest(ALE)

    report = applier.install(m)
    assert git.calls[0][0] == "https://github.com/Brytenwally/SitMeansRest.git"
    assert git.calls[0][1] == tmp_path / "ale_scripts" / "sitmeanrest"
    deployed = tmp_path / LUA / "SitMeansRest.lua"
    assert deployed.read_text(encoding="utf-8") == "local DURATION = 5\n"  # configure-time patch
    assert sql.files == [("characters", "tables.sql")]
    assert report.rebuild_required is False and report.restart_recommended is True
    assert report.skipped == ()

    # configure: prompt default applies when no value is given; explicit value wins.
    applier.configure(m)
    assert deployed.read_text(encoding="utf-8") == "local DURATION = 20\n"
    applier.configure(m, {"duration": "45"})
    assert deployed.read_text(encoding="utf-8") == "local DURATION = 45\n"

    removed = applier.remove(m)
    assert not deployed.exists()
    assert not (tmp_path / "ale_scripts" / "sitmeanrest").exists()
    assert any(step.startswith("rm ") for step in removed.done)


def test_module_install_touches_include_sh_activates_conf_and_writes_keys(tmp_path: Path) -> None:
    git = _FakeGit(
        {
            "conf/mod_ahbot.conf.dist": "[worldserver]\nAuctionHouseBot.GUID = 0\n"
            "AuctionHouseBot.EnableSeller = 0\n",
            "data/sql/db-world/a.sql": "-- a",
        }
    )
    applier = Applier(tmp_path, git=git, sql=_FakeSql())
    report = applier.install(parse_manifest(MODULE), {"bot_guid": "42"})

    clone = tmp_path / "modules" / "mod-ah-bot"
    assert (clone / "include.sh").exists()
    conf = (tmp_path / "env/dist/etc/modules/mod_ahbot.conf").read_text(encoding="utf-8")
    assert "AuctionHouseBot.GUID = 42\n" in conf
    assert "AuctionHouseBot.EnableSeller = 1\n" in conf
    assert conf.endswith("AuctionHouseBot.NewKey = 7\n")  # absent key is appended
    assert report.rebuild_required is True
    assert any("ac-db-import" in step for step in report.done)  # db-import SQL is NOT run here


def test_missing_template_value_is_an_error_not_garbage(tmp_path: Path) -> None:
    git = _FakeGit({"conf/mod_ahbot.conf.dist": "AuctionHouseBot.GUID = 0\n"})
    with pytest.raises(ApplyError, match=r"no value for \{bot_guid\}"):
        Applier(tmp_path, git=git, sql=_FakeSql()).install(parse_manifest(MODULE))


def test_steps_without_a_seam_are_reported_skipped_never_silent(tmp_path: Path) -> None:
    m = parse_manifest(
        {
            "id": "sod",
            "name": "SoD",
            "type": "keg",
            "game": "wow-wotlk",
            "source": {"repo": "DadsMmoLab/dads-mmo-lab", "sparse_path": "kegs/sod"},
            "deploy": [{"src": "kegs/sod/SOD.lua", "dest": f"{LUA}/"}],
            "sql": [{"db": "world", "statement": "SELECT 1"}],
            "client": [{"src": "kegs/sod/Client Files/data", "dest": "data"}],
            "server_dbc": [{"src": "kegs/sod/Server Files/dbc"}],
        }
    )
    git = _FakeGit(
        {
            "kegs/sod/SOD.lua": "-- lua",
            "kegs/sod/Client Files/data/p.MPQ": "x",
            "kegs/sod/Server Files/dbc/Spell.dbc": "y",
        }
    )
    report = Applier(tmp_path, git=git).install(m)  # no sql, no client dir, no dbc
    assert git.calls[0][3] == "kegs/sod"  # sparse path forwarded
    assert (tmp_path / LUA / "SOD.lua").exists()
    assert sorted(report.skipped) == [
        "client kegs/sod/Client Files/data: no client dir configured",
        "server_dbc kegs/sod/Server Files/dbc: no DBC copier configured",
        "sql → world: no SQL runner configured",
    ]

    # With every seam present, nothing is skipped and the right places are written.
    client = tmp_path / "client"
    dbc = _FakeDbc()
    sql = _FakeSql()
    full = Applier(tmp_path, git=git, sql=sql, client_dir=client, dbc=dbc).install(m)
    assert full.skipped == ()
    assert (client / "Data" / "p.MPQ").exists()
    assert dbc.dirs == [tmp_path / "ale_scripts" / "sod" / "kegs/sod/Server Files/dbc"]
    assert sql.statements == [("world", "SELECT 1")]


def test_deploy_dir_with_rename_and_glob_patch(tmp_path: Path) -> None:
    m = parse_manifest(
        {
            "id": "activechat",
            "name": "Chatter",
            "type": "ale",
            "game": "wow-wotlk",
            "source": {"repo": "svey-xyz/ActiveChat"},
            "deploy": [
                {
                    "src": "AzerothChatter/",
                    "dest": f"{LUA}/AzerothChatter/",
                    "rename": [["data/chatter.lua", "data/chatter_data.lua"]],
                }
            ],
            "patches": [
                {
                    "file": f"{LUA}/AzerothChatter/**/*.lua",
                    "find": r'require\((["\'])data\.chatter\1\)',
                    "replace": r"require(\1data.chatter_data\1)",
                    "regex": True,
                }
            ],
        }
    )
    git = _FakeGit(
        {
            "AzerothChatter/data/chatter.lua": "return {}",
            "AzerothChatter/logic/chatter.lua": 'local d = require("data.chatter")\n',
        }
    )
    Applier(tmp_path, git=git).install(m)
    base = tmp_path / LUA / "AzerothChatter"
    assert (base / "data" / "chatter_data.lua").exists()
    assert not (base / "data" / "chatter.lua").exists()
    assert (base / "logic" / "chatter.lua").read_text(encoding="utf-8") == (
        'local d = require("data.chatter_data")\n'
    )


def test_set_conf_key_replaces_or_appends(tmp_path: Path) -> None:
    f = tmp_path / "x.conf"
    f.write_text("A = 1\n  B=2\n", encoding="utf-8")
    assert _set_conf_key(f, "B", "3") == "replace"
    assert _set_conf_key(f, "C", "4") == "append"
    assert f.read_text(encoding="utf-8") == "A = 1\nB = 3\nC = 4\n"


def test_docker_sql_argv_targets_the_right_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """`DockerSql` shells `docker exec -i <db> mysql -uroot -p<pw> <schema>` (wow-manage.sh)."""
    import subprocess

    seen: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    DockerSql("ac-database", "pw").run_statement("characters", "SELECT 1")
    assert seen == [
        [
            "docker",
            "exec",
            "-i",
            "ac-database",
            "mysql",
            "-uroot",
            "-ppw",
            "acore_characters",
            "-e",
            "SELECT 1",
        ]
    ]


def test_runner_git_sparse_clone_sequence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A keg clone is init/remote/sparseCheckout/pull --depth=1, not a plain clone."""
    import subprocess

    from yulon import runner

    seen: list[list[str]] = []

    def fake_run(argv: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(runner, "run", fake_run)
    dest = tmp_path / "ale_scripts" / "bmah"
    RunnerGit().clone("https://github.com/DadsMmoLab/dads-mmo-lab.git", dest, None, "guides/x")
    assert [a[:2] for a in seen] == [
        ["git", "init"],
        ["git", "remote"],
        ["git", "config"],
        ["git", "pull"],
    ]
    assert (dest / ".git" / "info" / "sparse-checkout").read_text(encoding="utf-8") == "guides/x/\n"
    assert seen[-1] == ["git", "pull", "--depth=1", "origin", "HEAD"]


def test_remove_of_a_shared_dir_deploy_leaves_other_scripts_alone(tmp_path: Path) -> None:
    """battlepass deploys `lua_scripts/` INTO the shared lua_scripts dir — remove() must not
    rmtree that dir (review finding 2026-08-21: it deleted every other ALE script)."""
    m = parse_manifest(
        {
            "id": "battlepass",
            "name": "Battle Pass",
            "type": "ale",
            "game": "wow-wotlk",
            "source": {"repo": "x/battlepass"},
            "deploy": [{"src": "lua_scripts/", "dest": f"{LUA}/"}],
        }
    )
    git = _FakeGit(
        {"lua_scripts/battlepass/init.lua": "-- bp", "lua_scripts/lib/CSMH/c.lua": "-- c"}
    )
    applier = Applier(tmp_path, git=git)
    foreign = tmp_path / LUA / "SomeOtherMod.lua"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("-- keep me", encoding="utf-8")
    applier.install(m)
    assert (tmp_path / LUA / "battlepass" / "init.lua").exists()

    report = applier.remove(m)
    assert foreign.exists()  # the shared dir survives
    assert (tmp_path / LUA).is_dir()
    assert not (tmp_path / LUA / "battlepass").exists()
    assert not (tmp_path / LUA / "lib").exists()
    assert report.skipped == ()

    # Clone already gone: a directory deploy cannot be undone safely → skipped, not guessed.
    applier.install(m)
    import shutil

    shutil.rmtree(applier.clone_dir(m))
    report = applier.remove(m)
    assert foreign.exists() and (tmp_path / LUA / "battlepass").exists()
    assert any("left in place" in s for s in report.skipped)


def test_remove_renamed_top_level_file_from_dir_deploy(tmp_path: Path) -> None:
    m = parse_manifest(
        {
            "id": "ren",
            "name": "Ren",
            "type": "ale",
            "game": "wow-wotlk",
            "source": {"repo": "x/ren"},
            "deploy": [{"src": "scripts/", "dest": f"{LUA}/", "rename": [["a.lua", "b.lua"]]}],
        }
    )
    applier = Applier(tmp_path, git=_FakeGit({"scripts/a.lua": "a"}))
    applier.install(m)
    assert (tmp_path / LUA / "b.lua").exists()
    applier.remove(m)
    assert not (tmp_path / LUA / "b.lua").exists() and (tmp_path / LUA).is_dir()
