"""Tests for the declarative apply engine (`yulon.apply`, roadmap 2.3).

Everything external is a fake: `_FakeGit` materializes a clone from a dict,
`_FakeSql` records statements/files, and the server dir is a tmp tree. The
point is the manifest → steps contract: deploy, patches with templates, conf
activation + key writes, SQL routing (db-import vs direct vs no runner),
client/DBC seams, and the remove path — plus the "nothing silent" rule:
every step that could not run appears in `ApplyReport.skipped`.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

import pytest

from yulon import apply as apply_module
from yulon.apply import Applier, ApplyError, DockerSql, _set_conf_key
from yulon.catalog import composegen, native
from yulon.git import CloneSpec, RunnerGit, git_available
from yulon.manifest import Manifest, parse_manifest
from yulon.manifest_store import ManifestStore
from yulon.ownership import Ownership
from yulon.resources import manifests_dir

LUA = "env/dist/etc/modules/lua_scripts"


class _FakeGit:
    """Writes `files` (relative path → text) into the clone dir instead of cloning."""

    def __init__(
        self,
        files: dict[str, str],
        *,
        unmodified: bool | None = None,
        no_local_commits: bool | None = None,
    ) -> None:
        self.files = files
        self.calls: list[CloneSpec] = []
        self.unmodified = unmodified
        self._no_local_commits = no_local_commits
        self.asked_about: list[str] = []
        self.branches_asked: list[str | None] = []

    def is_unmodified(self, dest: Path, relative_path: str) -> bool | None:
        """The `TreeReader` half of a real `Git`, so no test reaches the host's.

        `None` by default — "git could not be asked" — because that is what a
        real `RunnerGit` answers for these fakes' `.git` directories, and every
        guard that consults it must fail closed on it.
        """
        self.asked_about.append(relative_path)
        return self.unmodified

    def no_local_commits(self, dest: Path, branch: str | None) -> bool | None:
        """The `HistoryReader` half, `None` by default for the same reason.

        A separate answer from `is_unmodified` because it is a separate
        question: `status` compares the working tree and index against HEAD and
        says nothing whatever about what HEAD itself carries.
        """
        self.branches_asked.append(branch)
        return self._no_local_commits

    def clone(self, spec: CloneSpec) -> None:
        self.calls.append(spec)
        for rel, text in self.files.items():
            p = spec.dest / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        (spec.dest / ".git").mkdir(exist_ok=True)


def _untouched(applier: Applier, manifest: Any) -> Applier:
    """Answer T47's three reset questions as git would for a clone nobody has touched.

    `install()` over a checkout that is already there IS a `git fetch` + `reset
    --hard`, and since T47 it asks the same three questions `update()` does
    before running one: what repository the folder is a checkout of, whether
    anything in it is uncommitted, and whether HEAD carries commits the reset
    would move off. A `_FakeGit`'s `.git` is an empty directory, so a real
    `RunnerGit` answers `None` — "could not be asked" — to all three and the
    guard fails closed. That is the designed behaviour and it is not what the
    tests using this are about, so this says "asked, and untouched".

    Only for a FAKE clone. The real-clone tests answer these from git.
    """
    assert manifest.source is not None
    url = manifest.source.url
    applier.remote_url = lambda _dest: url
    applier.unmodified = lambda _dest, _path: True
    applier.no_local_commits = lambda _dest, _branch: True
    return applier


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


def _have_requirements(server_dir: Path, manifest: Any) -> None:
    """Put every id a manifest `requires` on disk, the way a server install leaves it.

    T69 turned `requires` into a refusal, so a fixture that installs a manifest
    declaring one now has to supply it: `mod-ale` for the nine shipped ale
    scripts and kegs, `mod-playerbots` for the two bot modules. A folder under
    a clone directory is the whole of it -- `missing_requirements()` asks the
    disk -- and the tests calling this are about something else entirely.

    Called with the MANIFEST rather than a hard-coded id so that a fixture
    whose manifest requires nothing gets nothing, and one that grows a
    requirement is carried without an edit here.
    """
    for needed in manifest.requires:
        (server_dir / "modules" / needed / ".git").mkdir(parents=True, exist_ok=True)


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
    _have_requirements(tmp_path, m)

    report = applier.install(m)
    assert git.calls[0].url == "https://github.com/Brytenwally/SitMeansRest.git"
    assert git.calls[0].dest == tmp_path / "ale_scripts" / "sitmeanrest"
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
    # db-import SQL is NOT run here. Until 2026-09-07 that fact was in `done`.
    assert not any("sql" in step for step in report.done)
    assert [p.db for p in report.pending_sql] == ["world"]


def _ahbot_git(*sql_files: str) -> _FakeGit:
    files = {"conf/mod_ahbot.conf.dist": "AuctionHouseBot.GUID = 0\n"}
    files.update({name: f"-- {name}" for name in sql_files})
    return _FakeGit(files)


def test_db_import_sql_is_pending_with_the_files_it_really_found(tmp_path: Path) -> None:
    """8.7a: a step nothing ran must not be in `done`, and the count must be real.

    Measured on yulon-ubuntu 2026-09-07: `apply.py` appended
    `sql data/sql/db-world/*.sql -> world: left to ac-db-import on next start`
    to `done` having run nothing and looked at nothing — not even whether the
    glob matched a file — and the Modules tab ticks every `done` entry. So the
    two things this asserts are the two things that were missing: the step is
    not claimed, and the glob was actually resolved against the clone.
    """
    git = _ahbot_git("data/sql/db-world/b.sql", "data/sql/db-world/a.sql")
    applier = Applier(tmp_path, git=git, sql=_FakeSql())

    report = applier.install(parse_manifest(MODULE), {"bot_guid": "42"})

    assert not any("sql" in step for step in report.done)
    assert report.skipped == ()
    assert len(report.pending_sql) == 1
    pending = report.pending_sql[0]
    assert pending.db == "world"
    assert pending.path == "data/sql/db-world/*.sql"
    assert pending.files == ("data/sql/db-world/a.sql", "data/sql/db-world/b.sql")


def test_a_caller_tells_applied_from_not_applied_without_reading_a_sentence(
    tmp_path: Path,
) -> None:
    """The whole point of the field: `pending_sql` empty means nothing is owed.

    A caller that decided by grepping "left to ac-db-import" out of `done` would
    be the same defect one layer up — the report's own English is not an API.
    """
    ale_git = _FakeGit({"SitMeansRest.lua": "x\n", "sql/tables.sql": "C"})
    ale = parse_manifest(ALE)
    _have_requirements(tmp_path / "ale", ale)
    direct = Applier(tmp_path / "ale", git=ale_git, sql=_FakeSql()).install(ale)
    assert direct.pending_sql == ()

    deferred = Applier(
        tmp_path / "mod", git=_ahbot_git("data/sql/db-world/a.sql"), sql=_FakeSql()
    ).install(parse_manifest(MODULE), {"bot_guid": "42"})
    assert deferred.pending_sql != ()


def test_a_db_import_glob_matching_nothing_reports_none_rather_than_a_guess(
    tmp_path: Path,
) -> None:
    """An empty match is a fact worth having: the clone brought no SQL at all.

    `_run_sql` raises for a direct step whose file is missing, but a db-import
    step must not — upstream's own updater joins the module's `data/sql` path
    and skips what is not there (`UpdateFetcher.cpp:159-186`), so a module with
    no SQL is normal, not broken. It is still not "one file was applied".
    """
    report = Applier(tmp_path, git=_ahbot_git(), sql=_FakeSql()).install(
        parse_manifest(MODULE), {"bot_guid": "42"}
    )
    assert report.pending_sql[0].files == ()


def test_a_conf_key_the_catalog_names_with_no_value_is_reported_not_dropped(
    tmp_path: Path,
) -> None:
    """8.7a's first defect, measured on `yulon-ubuntu` 2026-09-08 and fixed here.

    `mod-npc-beastmaster.json` names `Creatures.CustomIDs` on
    `env/dist/etc/worldserver.conf` with a note and no `default`. The install
    reported `activate … mod_npc_beastmaster.conf` and **nothing at all** about
    `worldserver.conf` — not in `done`, not in `skipped` — and the file was
    byte-identical before and after. So the one core-side configuration change
    that module needs was documented in the catalog, never made, and never
    mentioned. Same shape as the SQL clause above: a step nobody takes has to be
    visible, and a key with no value is not a value.
    """
    manifest = dict(MODULE)
    manifest["conf"] = [
        *MODULE["conf"],
        {
            "file": "env/dist/etc/worldserver.conf",
            "keys": [{"key": "Creatures.CustomIDs", "note": "add 601026"}],
        },
    ]
    core = tmp_path / "env/dist/etc/worldserver.conf"
    core.parent.mkdir(parents=True, exist_ok=True)
    core.write_bytes(b'Creatures.CustomIDs = "190010"\n')

    report = Applier(tmp_path, git=_ahbot_git(), sql=_FakeSql()).install(
        parse_manifest(manifest), {"bot_guid": "42"}
    )

    assert (
        "conf env/dist/etc/worldserver.conf: no value in the catalog for "
        "Creatures.CustomIDs — not written" in report.skipped
    )
    # And nothing was claimed either: the file is byte-identical.
    assert core.read_bytes() == b'Creatures.CustomIDs = "190010"\n'
    assert not any("worldserver.conf" in step for step in report.done)


def test_a_templated_db_import_path_answers_unknown_not_zero(tmp_path: Path) -> None:
    """`{key}` in a db-import path is a count this run cannot take, not a zero.

    No shipped manifest has one, and `_action_templates` deliberately leaves
    db-import paths out of `required_prompts` — so a value for the key was never
    asked for and rendering it here would raise on a step that is not being run.
    Globbing the raw `{key}` instead would match nothing and report a confident
    "no files", which is the lie this box exists to remove. `None` is the third
    answer, the same one `importer_sees_modules()` keeps for "cannot tell".
    """
    step = {"db": "world", "path": "data/sql/{flavour}/*.sql", "applied_by": "db-import"}
    data = {**MODULE, "sql": [step]}
    report = Applier(tmp_path, git=_ahbot_git("data/sql/db-world/a.sql"), sql=_FakeSql()).install(
        parse_manifest(data), {"bot_guid": "42"}
    )
    assert report.pending_sql[0].files is None


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
    assert git.calls[0].sparse_path == "kegs/sod"  # sparse path forwarded
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
    full = _untouched(Applier(tmp_path, git=git, sql=sql, client_dir=client, dbc=dbc), m).install(m)
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


def test_docker_sql_keeps_the_password_and_the_sql_out_of_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`docker exec -i -e MYSQL_PWD <db> mysql -uroot <schema>`, statement over stdin.

    argv is world-readable (`ps`, Task Manager, /proc/<pid>/cmdline), so neither
    the root password nor a statement (which can carry one) may appear there.
    """
    import subprocess

    seen: list[list[str]] = []
    kwargs_seen: list[dict[str, object]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        kwargs_seen.append(kwargs)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    DockerSql("ac-database", "hunter2").run_statement("characters", "SET PASSWORD = 'secret'")
    assert seen == [
        [
            "docker",
            "exec",
            "-i",
            "-e",
            "MYSQL_PWD",
            "ac-database",
            "mysql",
            "-uroot",
            "acore_characters",
        ]
    ]
    flat = " ".join(seen[0])
    assert "hunter2" not in flat and "secret" not in flat  # the whole point
    assert kwargs_seen[0]["input"] == "SET PASSWORD = 'secret'"  # SQL over stdin
    env = kwargs_seen[0]["env"]
    assert isinstance(env, dict) and env["MYSQL_PWD"] == "hunter2"  # value only in the env


def test_docker_sql_query_keeps_the_password_and_the_sql_out_of_argv_as_well(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The read half gets the same argv guarantee as the write half.

    `query()` adds two client flags and changes nothing else: the statement
    still goes over stdin and the root password still lives only in the
    environment. None of that was pinned when the method landed, so moving the
    statement into `-e <sql>` — putting a statement that can carry a password
    into world-readable argv — left the whole suite green (review, 2026-08-23).

    `--batch` fixes the separator to a tab whether or not the client thinks it
    has a terminal, and `--skip-column-names` drops the header; the account
    module reads the result as one value per line, so both are load-bearing.
    """
    import subprocess

    seen: list[list[str]] = []
    kwargs_seen: list[dict[str, object]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        kwargs_seen.append(kwargs)
        return subprocess.CompletedProcess(argv, 0, "12401\n", "a warning on stderr")

    monkeypatch.setattr(subprocess, "run", fake_run)
    rows = DockerSql("ac-database", "hunter2").query(
        "auth", "SELECT id FROM account WHERE username = _utf8mb4 X'4142'"
    )
    assert seen == [
        [
            "docker",
            "exec",
            "-i",
            "-e",
            "MYSQL_PWD",
            "ac-database",
            "mysql",
            "-uroot",
            "--batch",
            "--skip-column-names",
            "acore_auth",
        ]
    ]
    assert rows == "12401\n"  # stdout: the rows are the answer, stderr is not
    assert kwargs_seen[0]["input"] == "SELECT id FROM account WHERE username = _utf8mb4 X'4142'"
    env = kwargs_seen[0]["env"]
    assert isinstance(env, dict) and env["MYSQL_PWD"] == "hunter2"
    assert "hunter2" not in " ".join(seen[0])


def test_a_query_that_failed_raises_instead_of_looking_like_an_empty_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreachable database must not read as "no rows".

    `accounts._account_id()` treats an empty result as "this username is free"
    and goes on to insert, so a `query()` that swallowed the exit code would
    turn a broken database into a silent duplicate-account attempt. The check
    was there from the start and nothing held it there (review, 2026-08-23).
    """
    import subprocess

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, "", "ERROR 2013 (HY000): Lost connection")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(ApplyError, match="Lost connection"):
        DockerSql("ac-database", "hunter2").query("auth", "SELECT id FROM account")


def test_runner_git_sparse_clone_sequence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A keg clone is init/remote/sparseCheckout/pull --depth=1, not a plain clone."""
    import subprocess

    from yulon import runner

    seen: list[list[str]] = []

    def fake_run(
        argv: list[str], cwd: Path | None = None, env: object = None
    ) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(runner, "run", fake_run)
    dest = tmp_path / "ale_scripts" / "bmah"
    RunnerGit().clone(
        CloneSpec(
            url="https://github.com/DadsMmoLab/dads-mmo-lab.git",
            dest=dest,
            sparse_path="guides/x",
        )
    )
    assert [a[:2] for a in seen] == [
        ["git", "init"],
        ["git", "remote"],
        ["git", "config"],  # core.sparseCheckout
        ["git", "config"],  # core.autocrlf=false — or the checkout gets CRLF on Windows
        ["git", "config"],  # core.eol=lf
        ["git", "config"],  # http.version=HTTP/1.1 — this path inherits no clone --config
        ["git", "-c"],  # ...and the pull carries it too
    ]
    assert seen[-1][:4] == ["git", "-c", "http.version=HTTP/1.1", "pull"]
    assert (dest / ".git" / "info" / "sparse-checkout").read_text(encoding="utf-8") == "guides/x/\n"
    assert seen[-1] == [
        "git",
        "-c",
        "http.version=HTTP/1.1",
        "pull",
        "--depth=1",
        "origin",
        "HEAD",
    ]


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


def test_generated_files_are_lf_even_on_windows(tmp_path: Path) -> None:
    """A CRLF conf file inside a Linux container is a runtime failure, not a cosmetic one.

    `Path.write_text()` without `newline=` translates to `os.linesep`, so a
    Windows host would write CRLF into files the containers read (and, for the
    modules' `.conf`, into files AzerothCore parses). Assert bytes, not text.
    """
    conf = tmp_path / "mod.conf"
    conf.write_text("Existing.Key = 1\n", encoding="utf-8", newline="\n")
    _set_conf_key(conf, "Existing.Key", "2")  # replace path
    _set_conf_key(conf, "Brand.New.Key", "7")  # append path
    raw = conf.read_bytes()
    assert b"\r\n" not in raw
    assert b"Existing.Key = 2" in raw and b"Brand.New.Key = 7" in raw

    # `_set_conf_key` edits a conf the installer already wrote; an empty file is
    # the closest thing to "brand new" it ever sees.
    fresh = tmp_path / "fresh.conf"
    fresh.write_text("", encoding="utf-8")
    _set_conf_key(fresh, "First.Key", "1")
    assert fresh.read_bytes() == b"First.Key = 1\n"


# --------------------------------------------------------- naming the docker CLI
# A manifest apply runs straight after an install, in the same process that may
# still be blind to the PATH Docker Desktop's installer wrote (see
# `platform.docker_program()`). `docker exec` here had the same hardcoded name
# as everything else.

OFF_PATH_EXE = r"C:\Users\user\AppData\Local\Programs\DockerDesktop\resources\bin\docker.EXE"


def test_docker_sql_execs_through_the_cli_this_host_can_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    monkeypatch.setattr(apply_module.platform, "_resolved_docker_cli", OFF_PATH_EXE)
    seen: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    DockerSql("ac-database", "hunter2").run_statement("characters", "SELECT 1")
    assert seen[0][0] == OFF_PATH_EXE
    assert seen[0][1:3] == ["exec", "-i"], "only argv[0] moved"


def test_docker_sql_without_any_docker_raises_apply_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An `ApplyError` naming Docker, not a `FileNotFoundError` from `subprocess.run`."""
    monkeypatch.setattr(apply_module.platform, "_resolved_docker_cli", None)
    monkeypatch.setattr(apply_module.platform, "docker_programs", lambda: ("docker",))
    monkeypatch.setattr(apply_module.platform, "_which", lambda name, path=None: None)
    with pytest.raises(ApplyError, match="Docker could not be found"):
        DockerSql("ac-database", "hunter2").run_statement("characters", "SELECT 1")


def test_docker_sql_says_the_same_thing_when_a_resolved_docker_has_gone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The other way to have no Docker, on both of `DockerSql`'s entry points.

    `docker_program()` remembers a hit for the life of the process, so Docker
    Desktop uninstalling or updating itself while the launcher is open leaves
    that pinned path aimed at a file that is gone. `subprocess` reports it as
    `OSError`, which used to reach the user as `[Errno 2]` while `docker.start()`
    on the same run said "Docker could not be found" (review, 2026-08-23).

    `run_file()` is exercised as well as `run_statement()` because the guard is
    shared by both and a guard on one of two call sites is the bug being fixed.
    """
    import subprocess

    monkeypatch.setattr(apply_module.platform, "_resolved_docker_cli", OFF_PATH_EXE)

    def gone(argv: list[str], **kwargs: object):
        raise FileNotFoundError(2, "The system cannot find the file specified", OFF_PATH_EXE)

    monkeypatch.setattr(subprocess, "run", gone)
    sql_file = tmp_path / "seed.sql"
    sql_file.write_text("SELECT 1;\n", encoding="utf-8")
    with pytest.raises(ApplyError, match="Docker could not be found"):
        DockerSql("ac-database", "hunter2").run_statement("characters", "SELECT 1")
    with pytest.raises(ApplyError, match="Docker could not be found"):
        DockerSql("ac-database", "hunter2").run_file("characters", sql_file)


def test_output_that_is_not_utf8_does_not_escape_as_a_decode_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real query against a real server raised UnicodeDecodeError out of here.

    `text=True` on its own decodes strictly, so any byte mysql emits that is not
    UTF-8 -- a binary column selected as text, a latin1 error message -- came
    back as `UnicodeDecodeError`. That is neither `ApplyError` nor the
    `AccountError` that `accounts.create_account` documents as the only type a
    caller has to handle, so it went straight past both contracts
    (found live, 2026-08-23).

    This drives the REAL decode path: a genuine subprocess writing genuine
    non-UTF-8 bytes. Faking `subprocess.run` would skip the only thing under
    test.
    """
    import sys

    sql = DockerSql("ac-database", "hunter2")
    monkeypatch.setattr(
        DockerSql,
        "_argv",
        lambda self, db, extra=(): [
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(b'\\x81\\x81 ok')",
        ],
    )

    out = sql.query("auth", "SELECT 1")

    # 0x81 is deliberate: it is undefined in cp1252 AND an orphan continuation
    # byte in UTF-8, so a strict decode raises on every platform. 0xbf does not
    # -- cp1252 renders it happily, so the first version of this test passed on
    # Windows with the fix removed, which is a test that cannot fail where it is
    # run.
    #
    # The guarantee is that nothing raises and the readable part survives, NOT
    # that a particular replacement character appears: `text=True` decodes with
    # the locale codec, so those bytes come back as U+FFFD on a UTF-8 box and as
    # perfectly valid cp1252 characters on this one. Asserting U+FFFD passed on
    # Linux and failed on Windows, which is the wrong thing to pin.
    assert "ok" in out, out


def test_docker_sql_addresses_the_game_s_own_schemas(monkeypatch: pytest.MonkeyPatch) -> None:
    """A CMaNGOS install has no `acore_auth`; the schema name is the game's, not a constant.

    Discord report, 2026-08-26: every SQL-backed control on Tortoise/TBC/Vanilla died with
    `ERROR 1049 (42000): Unknown database 'acore_auth'` because the schema was a module
    constant. Asserted at argv level, where the defect actually lived.
    """
    import subprocess

    seen: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    sql = DockerSql(
        "tortoise-db",
        "hunter2",
        schemas={"auth": "tw_logon", "characters": "tw_char", "world": "tw_world"},
    )
    sql.run_statement("auth", "SELECT 1")
    sql.query("world", "SELECT 1")
    assert [argv[-1] for argv in seen] == ["tw_logon", "tw_world"]
    assert not any("acore" in " ".join(argv) for argv in seen)


def test_docker_sql_refuses_a_database_this_game_does_not_have(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refuse by name rather than raise KeyError or connect to somebody else's schema."""
    import subprocess

    seen: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    sql = DockerSql("tortoise-db", "hunter2", schemas={"auth": "tw_logon"})
    with pytest.raises(ApplyError) as excinfo:
        sql.run_statement("playerbots", "SELECT 1")
    assert "playerbots" in str(excinfo.value)
    assert seen == [], "it must not run mysql at all"


def test_docker_sql_still_defaults_to_the_azerothcore_schemas() -> None:
    """Every existing caller passes no map and must keep addressing acore_*."""
    assert DockerSql("ac-database", "hunter2").schemas == apply_module.DB_NAMES


def test_the_client_probe_finds_mariadb_when_there_is_no_mysql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mariadb:11 ships `mariadb`/`mariadb-dump` and neither `mysql` nor `mysqldump`.

    wow-tbc and wow-vanilla run that image, so every statement this app sent
    them died before it reached a database. wow-tortoise pins mariadb:10.6,
    which still has the symlinks — which is why it worked and hid this
    (measured on a live TBC server, 2026-08-26).
    """
    apply_module._client_cache.clear()
    monkeypatch.setattr(
        apply_module,
        "_probe_client",
        lambda container, candidates: "mariadb" if candidates[0] == "mysql" else "mariadb-dump",
    )
    assert apply_module.mysql_client("tbc-db") == "mariadb"
    assert apply_module.mysql_client("tbc-db", "mysqldump") == "mariadb-dump"


def test_a_container_that_has_mysql_keeps_using_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """AzerothCore and tortoise images have the classic names; nothing changes for them."""
    apply_module._client_cache.clear()
    monkeypatch.setattr(apply_module, "_probe_client", lambda container, candidates: "mysql")
    assert apply_module.mysql_client("ac-database") == "mysql"


def test_an_unanswerable_probe_falls_back_to_the_classic_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A daemon hiccup must produce the failure it always did, not a new one."""
    apply_module._client_cache.clear()
    monkeypatch.setattr(apply_module, "_probe_client", lambda container, candidates: None)
    assert apply_module.mysql_client("whatever") == "mysql"
    assert apply_module.mysql_client("whatever", "mysqldump") == "mysqldump"


def test_the_probe_is_asked_once_per_container(monkeypatch: pytest.MonkeyPatch) -> None:
    """It cannot change without the container being replaced, and SQL is on a hot path."""
    apply_module._client_cache.clear()
    asked: list[str] = []

    def probe(container: str, candidates: tuple[str, ...]) -> str:
        asked.append(container)
        return "mariadb"

    monkeypatch.setattr(apply_module, "_probe_client", probe)
    for _ in range(3):
        apply_module.mysql_client("tbc-db")
    assert asked == ["tbc-db"], asked


def test_the_sql_seam_never_renders_the_password_it_carries() -> None:
    """A frozen dataclass reprs every field, and this one carries the DB root password.

    `maintenance.DockerMysql` closed exactly this channel on 2026-08-23; its
    `apply.DockerSql` sibling was missed, so a pytest assertion diff, a logged
    object or a traceback frame dump in a UI error handler would each print the
    password. Both objects are built side by side by the same three call sites
    (`main.py`, `ControllerServices.for_wotlk`, `install_wiring.import_gate_for`),
    which is how one of them being safe read as both of them being safe.
    """
    sql = DockerSql("ac-database", "hunter2")
    assert "hunter2" not in repr(sql)
    assert "hunter2" not in str(sql)
    assert "hunter2" not in f"{sql}"
    assert sql.root_password == "hunter2", "still readable where it is actually needed"
    assert "ac-database" in repr(sql), "the repr is still useful for the fields that are not secret"


def test_the_test_run_never_dumps_frame_locals() -> None:
    """The one channel `field(repr=False)` cannot close, guarded where it is turned on.



    A secret held in a local or a closure cell is in that frame's `f_locals`

    whatever its object prints: `traceback.format_exception()` is clean, and

    `TracebackException(capture_locals=True)` - which is what `--showlocals`,

    `rich` and `cgitb` install - is not. Measured 2026-08-31 against a canary

    in a real `.db_password` file: the frame of `install_wiring`'s `probe()`

    and the frame of `ControllerServices.for_wotlk()` both render it.



    This is not about the applier in particular. `platform.SudoSession` holds

    the user's sudo password the same way, and `accounts.create_account()` the

    account one. Nothing in the tree turns the flag on today; the risk is that

    somebody adds it for one debugging session and leaves it in, and every

    later CI failure in this area prints a password into a build log that

    outlives the session.

    """

    import tomllib

    pyproject = Path(apply_module.__file__).parent.parent / "pyproject.toml"

    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    pytest_options = config["tool"]["pytest"]["ini_options"]

    assert pytest_options["testpaths"] == ["tests"], "read the wrong table"

    addopts = pytest_options.get("addopts", "")

    flags = addopts.split() if isinstance(addopts, str) else list(addopts)

    assert not [f for f in flags if "showlocals" in f or f in {"-l", "--locals"}], (
        "a frame-locals dump prints every secret this codebase holds in a local "
        f"(the DB root password, the sudo password, a new account's password): {flags}"
    )


# ---------------------------------------------------------------- ownership
#
# `modules/<id>` is where an AzerothCore user installs a module BY HAND, and it
# is also where this engine clones. The clone seam `shutil.rmtree`s a
# destination it does not recognise and runs `git fetch` + `git reset --hard
# FETCH_HEAD` over one it does, and `remove()` deleted the folder outright — so
# every test below asserts the same three things the native engine's
# equivalents assert: the refusal is raised, the seam was NEVER reached, and the
# bytes the user had there are still exactly the bytes they had there.

OWNED_ITEM: dict[str, Any] = {
    "id": "mod-ah-bot",
    "name": "AH Bot",
    "type": "module",
    "game": "wow-wotlk",
    "source": {"repo": "azerothcore/mod-ah-bot"},
}
OWNED_URL = "https://github.com/azerothcore/mod-ah-bot.git"


class _Origins:
    """The `remote_url` seam, with a memory: what it answers, and whether it was asked."""

    def __init__(self, answer: str | None = None) -> None:
        self.answer = answer
        self.asked: list[Path] = []

    def __call__(self, dest: Path) -> str | None:
        self.asked.append(dest)
        return self.answer


def _user_module(server_dir: Path, *, checkout_of: str | None = None) -> tuple[Path, bytes]:
    """A `modules/mod-ah-bot` the USER made, with a file in it worth keeping."""
    clone = server_dir / "modules" / "mod-ah-bot"
    (clone / "src").mkdir(parents=True)
    mine = clone / "src" / "mine.cpp"
    mine.write_text("// my patch, three evenings\n", encoding="utf-8")
    if checkout_of is not None:
        (clone / ".git").mkdir()
    return clone, mine.read_bytes()


def test_a_module_folder_the_user_made_by_hand_is_never_deleted(tmp_path: Path) -> None:
    """Case 1: content, no `.git`. The clone seam would `rmtree` this without a word."""
    clone, before = _user_module(tmp_path)
    git = _FakeGit({"README.md": "upstream\n"})
    origins = _Origins()
    applier = Applier(tmp_path, git=git, remote_url=origins)

    with pytest.raises(ApplyError) as err:
        applier.install(parse_manifest(OWNED_ITEM))

    assert git.calls == []  # the harm is in the seam, and it was never reached
    assert (clone / "src" / "mine.cpp").read_bytes() == before
    assert origins.asked == []  # a folder with no `.git` needs no git to refuse
    message = str(err.value)
    assert "modules" in message and "mod-ah-bot" in message
    assert "Nothing was changed" in message and "Move that folder aside" in message


def test_a_checkout_of_another_repository_is_refused_by_name(tmp_path: Path) -> None:
    """Case 2: `origin` is not the manifest's URL, so this is not this module at all."""
    clone, before = _user_module(tmp_path, checkout_of="theirs")
    git = _FakeGit({"README.md": "upstream\n"})
    origins = _Origins("https://github.com/someone-else/mod-ah-bot.git")
    applier = Applier(tmp_path, git=git, remote_url=origins)

    with pytest.raises(ApplyError) as err:
        applier.install(parse_manifest(OWNED_ITEM))

    assert git.calls == []
    assert (clone / "src" / "mine.cpp").read_bytes() == before
    assert "someone-else/mod-ah-bot" in str(err.value) and OWNED_URL in str(err.value)


def test_the_right_repository_this_app_never_cloned_is_still_refused(tmp_path: Path) -> None:
    """Case 3, and the one a matching `origin` alone would wave through.

    A user who cloned `mod-ah-bot` into `modules/mod-ah-bot` themselves has a
    checkout of exactly the URL the manifest names. Ownership is the claim
    file, never the URL — everybody with this catalog entry has the URL.
    """
    clone, before = _user_module(tmp_path, checkout_of="the same repo")
    git = _FakeGit({"README.md": "upstream\n"})
    applier = Applier(tmp_path, git=git, remote_url=_Origins(OWNED_URL))

    with pytest.raises(ApplyError) as err:
        applier.install(parse_manifest(OWNED_ITEM))

    assert git.calls == []
    assert (clone / "src" / "mine.cpp").read_bytes() == before
    assert "git reset" in str(err.value) and "no record" in str(err.value)


def test_git_that_will_not_say_what_a_checkout_is_refuses_rather_than_guesses(
    tmp_path: Path,
) -> None:
    """`None` from the seam is "could not ask", and a refusal — never "no remote"."""
    clone, before = _user_module(tmp_path, checkout_of="unreadable")
    git = _FakeGit({"README.md": "upstream\n"})
    applier = Applier(tmp_path, git=git, remote_url=_Origins(None))

    with pytest.raises(ApplyError, match="would not say"):
        applier.install(parse_manifest(OWNED_ITEM))

    assert git.calls == []
    assert (clone / "src" / "mine.cpp").read_bytes() == before


@pytest.mark.parametrize(
    "damage",
    [
        "",
        "{",
        "[]",
        '{"version": 99, "item_id": "mod-ah-bot", "clone_id": "x"}',
        '{"version": 1, "item_id": "another-module", "clone_id": "x"}',
    ],
    ids=["empty", "truncated", "not-an-object", "future-version", "another-item"],
)
def test_a_claim_this_app_cannot_read_as_its_own_fails_closed(tmp_path: Path, damage: str) -> None:
    """Case 4 (`Ownership.UNKNOWN`): knowing less must never mean acting more freely.

    This is the native engine's 2026-08-31 bug in the module engine's shape: a
    damaged record there made the guard treat the folder as fresh, and the work
    in it went. Here every claim this app cannot read as its own is a refusal.
    """
    clone, before = _user_module(tmp_path, checkout_of="ours, allegedly")
    (clone / apply_module.CLAIM_FILE).write_text(damage, encoding="utf-8")
    git = _FakeGit({"README.md": "upstream\n"})
    origins = _Origins(OWNED_URL)
    applier = Applier(tmp_path, git=git, remote_url=origins)

    assert apply_module.read_clone_claim(clone, item_id="mod-ah-bot") is Ownership.UNKNOWN
    with pytest.raises(ApplyError, match=apply_module.CLAIM_FILE):
        applier.install(parse_manifest(OWNED_ITEM))

    assert git.calls == []
    assert (clone / "src" / "mine.cpp").read_bytes() == before
    assert origins.asked == []  # UNKNOWN is refused before git is even consulted


def test_a_claim_copied_from_another_folder_describes_a_folder_that_is_not_here(
    tmp_path: Path,
) -> None:
    """A COPIED server dir carries claims that point at the original's paths."""
    clone, _ = _user_module(tmp_path, checkout_of="a copy")
    apply_module.write_clone_claim(clone, item_id="mod-ah-bot", url=OWNED_URL)
    elsewhere = tmp_path / "copy" / "modules" / "mod-ah-bot"
    elsewhere.mkdir(parents=True)
    claim = (clone / apply_module.CLAIM_FILE).read_bytes()
    (elsewhere / apply_module.CLAIM_FILE).write_bytes(claim)

    assert apply_module.read_clone_claim(clone, item_id="mod-ah-bot") is Ownership.OWNED
    assert apply_module.read_clone_claim(elsewhere, item_id="mod-ah-bot") is Ownership.UNKNOWN


def _torn_write(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Make every `Path.write_text` put half its bytes on disk and then fail.

    A full disk, a killed process, a laptop lid closing: the file exists and its
    content stops in the middle. Injected at `Path.write_text` rather than at
    any one call so the property under test is about the OUTCOME on disk — a
    writer that opens the claim path directly is torn where it matters, and one
    that writes somewhere else first is torn where it does not.

    Returns the paths that were torn, because WHERE the half file landed is
    itself load-bearing: a rename is only atomic within one filesystem, so the
    file being renamed over the claim has to have been written beside it.
    """
    torn: list[Path] = []

    def half(self: Path, data: str, **kwargs: Any) -> int:
        torn.append(self)
        written = data[: max(1, len(data) // 2)].encode("utf-8")
        self.write_bytes(written)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_text", half)
    return torn


def test_a_torn_claim_write_never_leaves_a_file_that_reads_as_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A half-written claim is the one failure that locks the user out for good.

    A truncated file does not parse, a file that does not parse reads `UNKNOWN`,
    and `UNKNOWN` refuses every caller — including `remove()`, whose relocation
    licence also needs a claim that PARSES. So a failure in metadata this app
    alone writes and reads would take away the only in-app uninstall, with no
    way back that does not involve deleting a file by hand.

    The claim therefore has to appear at its name whole or not at all: written
    somewhere else in the same directory first, then renamed over. Renaming
    within a directory is the one filesystem operation that cannot half-happen.
    """
    clone = tmp_path / "modules" / "mod-ah-bot"
    clone.mkdir(parents=True)
    torn = _torn_write(monkeypatch)

    with pytest.raises(OSError):
        apply_module.write_clone_claim(clone, item_id="mod-ah-bot", url=OWNED_URL)

    assert apply_module.read_clone_claim(clone, item_id="mod-ah-bot") is Ownership.UNCLAIMED
    assert list(clone.iterdir()) == []  # and no debris under a name nothing will ever read
    # Beside the claim, not somewhere else: `os.replace()` is atomic within one
    # filesystem and a copy across two, and a copy is the tearing being avoided.
    assert [p.parent for p in torn] == [clone]


def test_a_torn_claim_write_leaves_the_claim_that_was_already_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same property where it costs the most: a re-install over an owned clone.

    `install()` rewrites the claim on every press. If that write can tear, then
    one bad moment during an ordinary update turns a working install into one
    that cannot be uninstalled — and the folder it happens to is precisely the
    folder this app made.
    """
    clone = tmp_path / "modules" / "mod-ah-bot"
    clone.mkdir(parents=True)
    apply_module.write_clone_claim(clone, item_id="mod-ah-bot", url=OWNED_URL)
    before = (clone / apply_module.CLAIM_FILE).read_bytes()
    _torn_write(monkeypatch)

    with pytest.raises(OSError):
        apply_module.write_clone_claim(clone, item_id="mod-ah-bot", url=OWNED_URL)

    assert apply_module.read_clone_claim(clone, item_id="mod-ah-bot") is Ownership.OWNED
    assert (clone / apply_module.CLAIM_FILE).read_bytes() == before
    assert [p.name for p in clone.iterdir()] == [apply_module.CLAIM_FILE]


def test_a_first_install_needs_no_folder_and_the_second_updates_this_apps_own(
    tmp_path: Path,
) -> None:
    """The two cases that must keep working: nothing there, and this app's own clone."""
    git = _FakeGit({"README.md": "upstream\n"}, unmodified=True, no_local_commits=True)
    origins = _Origins(OWNED_URL)
    applier = Applier(tmp_path, git=git, remote_url=origins)
    m = parse_manifest(OWNED_ITEM)

    report = applier.install(m)  # nothing at the path at all
    clone = applier.clone_dir(m)
    assert len(git.calls) == 1 and git.calls[0].dest == clone
    assert apply_module.read_clone_claim(clone, item_id="mod-ah-bot") is Ownership.OWNED
    assert report.skipped == ()

    applier.install(m)  # the update path, over a clone this app made
    assert len(git.calls) == 2
    # The claim is still the whole of the OWNERSHIP question -- `_require_own_clone()`
    # returns on it without asking anything -- and since T47 the same install
    # asks `origin` once more, for a different question: this folder is about to
    # be `reset --hard`, and what it is a checkout of decides whether that is
    # this module's own update or somebody else's repository being overwritten.
    assert origins.asked == [applier.clone_dir(m)]


def test_remove_refuses_to_delete_a_module_folder_this_app_did_not_clone(tmp_path: Path) -> None:
    """The other destructive path: `remove()` reached `shutil.rmtree` unguarded.

    The refusal must land BEFORE the remove-time SQL, which nothing undoes.
    """
    clone, before = _user_module(tmp_path)
    sql = _FakeSql()
    applier = Applier(tmp_path, git=_FakeGit({}), sql=sql, remote_url=_Origins())

    with pytest.raises(ApplyError):
        applier.remove(parse_manifest(OWNED_ITEM))

    assert (clone / "src" / "mine.cpp").read_bytes() == before
    assert sql.statements == [] and sql.files == []


def test_remove_still_deletes_the_clone_this_app_made(tmp_path: Path) -> None:
    """And the guard must not lock a user out of uninstalling their own install."""
    git = _FakeGit({"README.md": "upstream\n"})
    applier = Applier(tmp_path, git=git, remote_url=_Origins(OWNED_URL))
    m = parse_manifest(OWNED_ITEM)
    applier.install(m)

    report = applier.remove(m)
    assert not applier.clone_dir(m).exists()
    assert any(step.startswith("rm ") for step in report.done)


def test_configure_never_rewrites_a_line_in_a_checkout_this_app_did_not_clone(
    tmp_path: Path,
) -> None:
    """The third writer through `clone_dir()`: an `in_clone` patch, at configure time."""
    clone, before = _user_module(tmp_path)
    m = parse_manifest(
        {
            **OWNED_ITEM,
            "patches": [
                {
                    "file": "src/mine.cpp",
                    "find": "my patch",
                    "replace": "ours now",
                    "when": "configure",
                    "in_clone": True,
                }
            ],
        }
    )
    applier = Applier(tmp_path, git=_FakeGit({}), remote_url=_Origins())

    with pytest.raises(ApplyError, match="was not put there by this app"):
        applier.configure(m)

    assert (clone / "src" / "mine.cpp").read_bytes() == before


# ------------------------------------------------- the remedy is part of the guard
#
# A refusal that names the wrong remedy causes the harm it was raised to
# prevent. `remove()` is the case: moving the clone aside makes `clone.exists()`
# false, so the remove-time SQL and `_undeploy()` are skipped or fail, and the
# module stays in the database with no route back through the app.


def _relocated_claim(clone: Path, *, item_id: str = "mod-ah-bot") -> None:
    """This app's own claim for `item_id`, naming the folder it USED to be in.

    Exactly what a user who renames or moves their server folder is left with:
    `install_id()` hashes the absolute path, so every claim under it stops
    matching at once, while the records themselves are untouched and correct.
    Written by the real writer at another path and moved, rather than typed out
    here, so it is byte-for-byte what a relocated install is actually holding.
    """
    old = clone.parent / ".where-it-used-to-be"
    old.mkdir(parents=True, exist_ok=True)
    apply_module.write_clone_claim(old, item_id=item_id, url=OWNED_URL)
    (old / apply_module.CLAIM_FILE).replace(clone / apply_module.CLAIM_FILE)
    old.rmdir()


def test_a_remove_refusal_never_tells_the_user_to_sidestep_the_database_cleanup(
    tmp_path: Path,
) -> None:
    """Move the folder aside and `remove()` skips the SQL and the undeploy.

    So a `remove()` refusal must never end there: it points back at removing,
    and says that hiding the folder is not an uninstall. The other two callers
    must not carry that sentence, and must not tell a user to `install` when
    they pressed Remove.
    """
    _user_module(tmp_path)
    applier = Applier(tmp_path, git=_FakeGit({}), sql=_FakeSql(), remote_url=_Origins())
    m = parse_manifest(OWNED_ITEM)

    with pytest.raises(ApplyError) as removing:
        applier.remove(m)
    with pytest.raises(ApplyError) as installing:
        applier.install(m)

    refusal = str(removing.value)
    assert "remove mod-ah-bot again" in refusal
    assert "install mod-ah-bot again" not in refusal
    assert "not by itself an uninstall" in refusal
    assert "install mod-ah-bot again" in str(installing.value)
    assert "not by itself an uninstall" not in str(installing.value)


def test_a_moved_install_can_still_be_uninstalled_through_the_app(tmp_path: Path) -> None:
    """The lockout `install_id()`'s path hash creates, and the one exit from it.

    Rename the server folder and EVERY claim under it reads `UNKNOWN` at once —
    the records are this app's own and still correct, only the folder they name
    has moved. `install()` stays refused there, because accepting would authorise
    `git reset --hard` inside what may be a COPY somebody is keeping. `remove()`
    accepts, because refusing it leaves the module's rows in the database with
    no way to reach them: the destruction being asked for is the item the user
    just asked to destroy.
    """
    clone, before = _user_module(tmp_path, checkout_of="ours, from its old path")
    _relocated_claim(clone)
    sql = _FakeSql()
    m = parse_manifest(
        {**OWNED_ITEM, "sql": [{"db": "world", "statement": "DELETE FROM x", "when": "remove"}]}
    )
    applier = Applier(tmp_path, git=_FakeGit({}), sql=sql, remote_url=_Origins(OWNED_URL))

    with pytest.raises(ApplyError) as err:
        applier.install(m)
    assert "moved, renamed or copied" in str(err.value)
    assert (clone / "src" / "mine.cpp").read_bytes() == before

    applier.remove(m)
    assert not clone.exists()
    assert sql.statements == [("world", "DELETE FROM x")]


def test_the_relocation_licence_reads_which_item_the_claim_names(tmp_path: Path) -> None:
    """The weaker proof `remove()` takes is still a proof, and this is its edge.

    A claim naming ANOTHER item at this path is not this app's record of the
    module being removed — it is a folder whose history this app cannot account
    for, which is the definition of `UNKNOWN`. Removing on it would delete a
    checkout and run one module's remove-time SQL because a different module's
    record happened to be lying in the folder.
    """
    clone, before = _user_module(tmp_path, checkout_of="ours, for something else")
    _relocated_claim(clone, item_id="another-module")
    sql = _FakeSql()
    m = parse_manifest(
        {**OWNED_ITEM, "sql": [{"db": "world", "statement": "DELETE FROM x", "when": "remove"}]}
    )
    applier = Applier(tmp_path, git=_FakeGit({}), sql=sql, remote_url=_Origins(OWNED_URL))

    with pytest.raises(ApplyError, match=apply_module.CLAIM_FILE):
        applier.remove(m)

    assert (clone / "src" / "mine.cpp").read_bytes() == before
    assert sql.statements == [] and sql.files == []


def test_a_sourceless_manifest_still_asks_whose_folder_it_is_rewriting(tmp_path: Path) -> None:
    """`install()`'s guard used to sit inside `if manifest.source is not None`.

    A `mod` is the one type the schema lets go without a source, so it never
    clones — and so everything at its clone path was put there by somebody else,
    by definition. An install-time `in_clone` patch then rewrote a line inside a
    folder nothing had asked about: the same hole `configure()` was given a
    guard for, in the one place where the folder is guaranteed not to be ours.
    """
    m = parse_manifest(
        {
            "id": "hand-made-sql",
            "name": "Hand-made SQL",
            "type": "mod",
            "game": "wow-wotlk",
            "patches": [
                {
                    "file": "src/mine.sql",
                    "find": "my patch",
                    "replace": "ours now",
                    "when": "install",
                    "in_clone": True,
                }
            ],
        }
    )
    applier = Applier(tmp_path, git=_FakeGit({}), remote_url=_Origins())
    clone = applier.clone_dir(m)
    (clone / "src").mkdir(parents=True)
    mine = clone / "src" / "mine.sql"
    mine.write_text("-- my patch, three evenings\n", encoding="utf-8")
    before = mine.read_bytes()

    with pytest.raises(ApplyError, match="was not put there by this app"):
        applier.install(m)

    assert mine.read_bytes() == before


# --------------------------------------------------------------- adoption
#
# A module installed by a build older than the claim file has no claim, so the
# first Install after the guard landed refused it. Adoption closes that without
# weakening the guard, on four independent facts — and a test for each of them
# failing on its own.


def _installed_by_this_app(server_dir: Path) -> None:
    """The install engine's record at the SERVER dir: this app created this folder.

    Written by the real writer, not by hand, so the evidence the guard leans on
    is the evidence the installer actually produces.
    """
    native.write_state(
        server_dir,
        native.InstallState(
            game_id="wow-wotlk",
            install_id=composegen.install_id(server_dir),
            family="azerothcore",
        ),
    )


def _existing_module(server_dir: Path) -> Path:
    """A `modules/mod-ah-bot` from an older build: a real checkout, and no claim."""
    clone = server_dir / "modules" / "mod-ah-bot"
    (clone / ".git").mkdir(parents=True)
    (clone / "README.md").write_text("upstream\n", encoding="utf-8")
    return clone


def test_a_module_from_an_older_build_is_adopted_when_all_four_facts_agree(
    tmp_path: Path,
) -> None:
    """The migration: matching origin, a server dir this app installed, a clean tree, no
    commits of the user's own."""
    clone = _existing_module(tmp_path)
    _installed_by_this_app(tmp_path)
    git = _FakeGit({"README.md": "upstream\n"}, unmodified=True, no_local_commits=True)
    applier = Applier(tmp_path, git=git, remote_url=_Origins(OWNED_URL))
    m = parse_manifest(OWNED_ITEM)

    applier.install(m)

    assert len(git.calls) == 1 and git.calls[0].dest == clone
    # and it is this app's own from here on, so the next press asks git nothing
    assert apply_module.read_clone_claim(clone, item_id="mod-ah-bot") is Ownership.OWNED


def test_a_stranger_s_checkout_in_a_folder_this_app_installed_is_not_adopted(
    tmp_path: Path,
) -> None:
    """Fact 1 alone fails: same server dir, clean tree, a DIFFERENT repository."""
    clone, before = _user_module(tmp_path, checkout_of="someone else's")
    _installed_by_this_app(tmp_path)
    git = _FakeGit({}, unmodified=True, no_local_commits=True)
    applier = Applier(tmp_path, git=git, remote_url=_Origins("https://github.com/them/mod-ah-bot"))

    with pytest.raises(ApplyError, match="them/mod-ah-bot"):
        applier.install(parse_manifest(OWNED_ITEM))

    assert git.calls == []
    assert (clone / "src" / "mine.cpp").read_bytes() == before


def test_a_hand_installed_module_in_someone_else_s_server_dir_is_not_adopted(
    tmp_path: Path,
) -> None:
    """Fact 2 alone fails, and it is the fact that does the work.

    Right repository, clean tree, no `.yulon-install.json` at the server dir —
    an AzerothCore tree this app never installed, with a module the user cloned
    themselves. Nothing inside the module folder can prove otherwise, which is
    exactly why the evidence is kept outside it.
    """
    clone, before = _user_module(tmp_path, checkout_of="the same repo, by hand")
    git = _FakeGit({}, unmodified=True, no_local_commits=True)
    applier = Applier(tmp_path, git=git, remote_url=_Origins(OWNED_URL))

    with pytest.raises(ApplyError, match="no record"):
        applier.install(parse_manifest(OWNED_ITEM))

    assert git.calls == []
    assert (clone / "src" / "mine.cpp").read_bytes() == before


@pytest.mark.parametrize(
    ("answer", "expected"),
    [(False, "changes in it that were never committed"), (None, "git would not say")],
    ids=["edited", "git-could-not-say"],
)
def test_a_checkout_with_local_work_in_it_is_not_adopted(
    tmp_path: Path, answer: bool | None, expected: str
) -> None:
    """Fact 3 alone fails. `None` is "could not ask", and refuses like a "no".

    `git fetch` + `git reset --hard FETCH_HEAD` destroys precisely what
    `git status` reports, so an empty answer is the proof that adopting costs
    nothing — and no answer at all is not that proof.

    Both refuse, and they say different things while refusing: telling a user
    whose git could not be run that they have uncommitted changes is telling
    them something nobody established.
    """
    clone, before = _user_module(tmp_path, checkout_of="the same repo, edited")
    _installed_by_this_app(tmp_path)
    git = _FakeGit({}, unmodified=answer, no_local_commits=True)
    applier = Applier(tmp_path, git=git, remote_url=_Origins(OWNED_URL))

    with pytest.raises(ApplyError, match=expected):
        applier.install(parse_manifest(OWNED_ITEM))

    assert git.calls == []
    assert (clone / "src" / "mine.cpp").read_bytes() == before


@pytest.mark.parametrize(
    ("answer", "expected"),
    [(False, "commits of your own"), (None, "could not reach that repository")],
    ids=["committed", "git-could-not-say"],
)
def test_a_checkout_carrying_the_user_s_own_commits_is_not_adopted(
    tmp_path: Path, answer: bool | None, expected: str
) -> None:
    """Fact 4 alone fails, and it is the fact the first three cannot see.

    `git status --porcelain` — the whole of fact 3 — compares the working tree
    and the index against HEAD. A user who cloned this catalog's own repository
    into a server directory this app created and then COMMITTED their changes
    has a perfectly clean tree by that test, so facts 1-3 all pass and adoption
    used to succeed. The very next thing `install()` does is `git.clone()`,
    which on an existing checkout runs `fetch` + `reset --hard FETCH_HEAD` and
    throws those commits away — work no check had ever looked at.

    So the fourth fact is a question about HEAD itself: does HEAD carry commits
    that `FETCH_HEAD` does not, after `no_local_commits()` has run the update's
    own fetch to put a truthful commit behind that ref? `None` — no `.git`
    directory, a fetch that could not reach the remote, or a `rev-list` that
    would not answer — refuses like a "no", per `Ownership`'s three-outcome
    rule. It does not refuse in the same WORDS: an offline user has committed
    nothing and must not be told they have.
    """
    clone, before = _user_module(tmp_path, checkout_of="the same repo, committed to")
    _installed_by_this_app(tmp_path)
    git = _FakeGit({}, unmodified=True, no_local_commits=answer)
    applier = Applier(tmp_path, git=git, remote_url=_Origins(OWNED_URL))

    with pytest.raises(ApplyError, match=expected):
        applier.install(parse_manifest(OWNED_ITEM))

    assert git.calls == []
    assert (clone / "src" / "mine.cpp").read_bytes() == before


def test_a_fact_that_said_no_and_one_that_could_not_be_asked_never_share_a_sentence(
    tmp_path: Path,
) -> None:
    """The three-outcome rule, applied to the words the user actually reads.

    Facts 3 and 4 each refuse for two different reasons — the answer was "no",
    or there was no answer — and those are not the same news. The offline case
    is the sharpest: fact 4 fetches, so a user with no connection gets `None`
    for a checkout that facts 2 and 3 have just proved is in a folder this app
    made and has nothing uncommitted in it. Told "there is no record here of one
    this app made" and "throws away anything you have changed there", they are
    told the opposite of their own situation twice in one sentence.

    Collapsing outcomes into one answer is the defect this entire guard exists
    to undo (`Ownership`'s docstring counts the three times it has bitten this
    codebase). It must not come back in the message that reports it.
    """
    said: dict[str, str] = {}
    for name, unmodified, no_local_commits in (
        ("edited", False, True),
        ("tree-unknown", None, True),
        ("committed", True, False),
        ("offline", True, None),
    ):
        server_dir = tmp_path / name
        _user_module(server_dir, checkout_of="the same repo")
        _installed_by_this_app(server_dir)
        applier = Applier(
            server_dir,
            git=_FakeGit({}, unmodified=unmodified, no_local_commits=no_local_commits),
            remote_url=_Origins(OWNED_URL),
        )
        with pytest.raises(ApplyError) as err:
            applier.install(parse_manifest(OWNED_ITEM))
        said[name] = str(err.value)

    assert len(set(said.values())) == len(said), said
    # and each one is about its own fact, not about somebody else's
    assert "commits of your own" in said["committed"]
    assert "commits of your own" not in said["offline"]
    assert "could not reach that repository" in said["offline"]
    assert "no record here" not in said["offline"]


def test_the_commit_question_is_asked_about_the_branch_the_update_would_reset_to(
    tmp_path: Path,
) -> None:
    """The manifest's branch, not whatever this checkout happens to be on.

    `git.clone()` over an existing checkout fetches `origin <branch or HEAD>`
    and resets to `FETCH_HEAD`, so the ref that decides what survives is the
    manifest's — asking about any other one would be answering a question
    nobody is about to act on.
    """
    _existing_module(tmp_path)
    _installed_by_this_app(tmp_path)
    git = _FakeGit({"README.md": "upstream\n"}, unmodified=True, no_local_commits=True)
    applier = Applier(tmp_path, git=git, remote_url=_Origins(OWNED_URL))
    branched = {**OWNED_ITEM, "source": {"repo": "azerothcore/mod-ah-bot", "branch": "wotlk"}}

    applier.install(parse_manifest(branched))

    # Twice since T47: the adoption rule asks, and then `_costly_reset()` asks
    # the same question again on the install's own destructive path. Both must
    # name the MANIFEST's branch, which is what this test is about -- a caller
    # that asked about the checkout's current branch would show up here.
    assert git.branches_asked == ["wotlk", "wotlk"]


def test_a_copied_server_folder_does_not_adopt_the_clones_in_the_copy(tmp_path: Path) -> None:
    """Fact 2 has to be about THIS folder, not about any folder this app installed.

    Copy a server directory and the copy carries a `.yulon-install.json` that is
    perfectly readable and describes the ORIGINAL's path — the same
    transplantation the per-clone claim refuses. Presence of the file is not the
    evidence; the path it names is.
    """
    clone, before = _user_module(tmp_path, checkout_of="copied along with the server dir")
    native.write_state(
        tmp_path,
        native.InstallState(
            game_id="wow-wotlk",
            install_id=composegen.install_id(tmp_path / "somewhere-else"),
            family="azerothcore",
        ),
    )
    git = _FakeGit({}, unmodified=True, no_local_commits=True)
    applier = Applier(tmp_path, git=git, remote_url=_Origins(OWNED_URL))

    with pytest.raises(ApplyError, match="no record"):
        applier.install(parse_manifest(OWNED_ITEM))

    assert git.calls == []
    assert (clone / "src" / "mine.cpp").read_bytes() == before


def test_a_repository_that_tracks_the_claim_file_s_name_does_not_lock_the_module_out(
    tmp_path: Path,
) -> None:
    """`reset --hard` restores THEIR file over this app's claim, forever.

    If an upstream module ever commits a `.yulon-clone.json` of its own, every
    update after the first restores it, the claim reads `UNKNOWN`, and install,
    configure and remove all refuse from then on — over a file the user cannot
    delete without dirtying their checkout. Git separates the two with
    certainty: a claim this app wrote is never committed to a module's
    repository, so a file at that name which `status` reports as unchanged from
    HEAD is the repository's content and not a claim at all.
    """
    clone = _existing_module(tmp_path)
    (clone / apply_module.CLAIM_FILE).write_text('{"upstream": "ours"}\n', encoding="utf-8")
    _installed_by_this_app(tmp_path)
    git = _FakeGit({"README.md": "upstream\n"}, unmodified=True, no_local_commits=True)
    applier = Applier(tmp_path, git=git, remote_url=_Origins(OWNED_URL))

    assert apply_module.read_clone_claim(clone, item_id="mod-ah-bot") is Ownership.UNKNOWN
    applier.install(parse_manifest(OWNED_ITEM))

    assert len(git.calls) == 1
    assert apply_module.CLAIM_FILE in git.asked_about  # asked about that file by name


# ------------------------------------------- the client comes from the entry (7.9)
#
# `mysql` was a literal in `_argv()` and the first guess in `_CLIENT_NAMES`.
# `install.native.db.client` says `mysql` for AzerothCore and `mariadb` for the
# three CMaNGOS entries, and `mariadb:11` ships neither `mysql` nor
# `mysqldump` — so the literal named a binary that is not in the container and
# every statement died before it reached a database (measured on a live TBC
# server, 2026-08-26).


class _Probe:
    """The client probe with a memory: what it was asked, and what it answers."""

    def __init__(self, answer: str | None = None) -> None:
        self.answer = answer
        self.asked: list[tuple[str, tuple[str, ...]]] = []

    def __call__(self, container: str, candidates: tuple[str, ...]) -> str | None:
        self.asked.append((container, candidates))
        return self.answer


def test_the_declared_client_is_asked_for_first_and_is_what_an_unanswered_probe_uses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole of what reading the catalog buys, in the case that made it matter.

    A probe that cannot run falls back to the first candidate. Left as a guess
    that first candidate is `mysql`, so a wedged daemon or a slow `docker exec`
    turned a CMaNGOS install into `executable file not found` on every
    statement — a failure that reads like a broken database rather than an
    unanswered question.
    """
    apply_module._client_cache.clear()
    probe = _Probe(None)
    monkeypatch.setattr(apply_module, "_probe_client", probe)

    assert apply_module.mysql_client("tbc-db", client="mariadb") == "mariadb"
    assert probe.asked[-1] == ("tbc-db", ("mariadb", "mysql")), probe.asked
    assert apply_module.mysql_client("ac-database", client="mysql") == "mysql"
    assert probe.asked[-1] == ("ac-database", ("mysql", "mariadb")), probe.asked


def test_an_undeclared_client_leaves_the_order_exactly_as_it_was(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every caller that holds no catalog entry must read as it did before 7.9."""
    apply_module._client_cache.clear()
    probe = _Probe(None)
    monkeypatch.setattr(apply_module, "_probe_client", probe)

    assert apply_module.mysql_client("whatever") == "mysql"
    assert probe.asked[-1][1] == ("mysql", "mariadb"), probe.asked
    assert apply_module.mysql_client("whatever", "mysqldump") == "mysqldump"
    assert probe.asked[-1][1] == ("mysqldump", "mariadb-dump"), probe.asked


def test_the_declaration_is_a_hint_and_the_container_still_decides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An entry saying `mariadb` over an image that has `mysql` must still work.

    The catalog's `client` is written from the image the entry pins, and images
    get rebuilt. Putting the declared spelling FIRST rather than using it
    INSTEAD of the probe is what keeps a stale declaration from breaking a
    container that answers perfectly well to the other name.
    """
    apply_module._client_cache.clear()
    monkeypatch.setattr(apply_module, "_probe_client", _Probe("mysql"))

    assert apply_module.mysql_client("tortoise-db", client="mariadb") == "mysql"


def test_the_dump_tool_follows_the_declared_client_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`DbFacts.client` names a FAMILY, and that family's dump tool is not its name.

    `mariadb`'s is `mariadb-dump`, not `mariadb`; a caller passing the declared
    client straight through as the binary would ask for a program no image has.
    """
    apply_module._client_cache.clear()
    probe = _Probe(None)
    monkeypatch.setattr(apply_module, "_probe_client", probe)

    assert apply_module.mysql_client("tbc-db", "mysqldump", client="mariadb") == "mariadb-dump"
    assert probe.asked[-1][1] == ("mariadb-dump", "mysqldump"), probe.asked
    assert apply_module.mysql_client("ac-database", "mysqldump", client="mysql") == "mysqldump"


def test_the_answer_is_cached_per_declared_client_and_not_across_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The declaration decides the ORDER, and the order decides the answer.

    An image with both spellings answers whichever was asked for first, because
    the probe is `command -v a || command -v b` and `||` short-circuits. So the
    client belongs in the cache key: without it the first caller's declaration
    would be handed to the second one's container.
    """
    apply_module._client_cache.clear()
    asked: list[tuple[str, ...]] = []

    def probe(container: str, candidates: tuple[str, ...]) -> str:
        asked.append(candidates)
        return candidates[0]

    monkeypatch.setattr(apply_module, "_probe_client", probe)

    assert apply_module.mysql_client("both-db", client="mariadb") == "mariadb"
    assert apply_module.mysql_client("both-db", client="mysql") == "mysql"
    assert len(asked) == 2, "one declaration's answer was served to the other"

    for _ in range(3):
        apply_module.mysql_client("both-db", client="mariadb")
    assert len(asked) == 2, "the probe is on a hot path and was asked again"


def test_the_sql_seam_runs_the_client_its_entry_declared(monkeypatch: pytest.MonkeyPatch) -> None:
    """The argv a CMaNGOS install really gets, with nobody to ask about the container.

    This is the seam every SQL-backed control on the Server tab goes through,
    and both per-install facts in it used to be AzerothCore's: the schema and
    the binary. `schemas=` already had its test; the binary is 7.9's half.
    """
    import subprocess

    seen: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    apply_module._client_cache.clear()
    monkeypatch.setattr(apply_module, "_probe_client", _Probe(None))
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        apply_module.platform, "docker_prefix", lambda wsl_distro=None, **kw: ("docker",)
    )

    DockerSql("tbc-db", "hunter2", schemas={"auth": "realmd"}, client="mariadb").run_statement(
        "auth", "SELECT 1"
    )
    DockerSql("ac-database", "hunter2").run_statement("auth", "SELECT 1")

    cmangos, azerothcore = seen
    assert cmangos[cmangos.index("-uroot") - 1] == "mariadb", cmangos
    assert "mysql" not in cmangos, cmangos
    assert azerothcore[azerothcore.index("-uroot") - 1] == "mysql", azerothcore


def test_the_sql_seam_without_a_declared_client_is_byte_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`client=None` is "nothing was declared", and must change no argv at all.

    Every caller outside `ui/controller_view.py` still builds `DockerSql`
    without one — `install_wiring.import_gate_for()` and each game package's
    `sql_for()` — so this is most of the tree, and it has to be provably
    untouched rather than probably untouched.
    """
    import subprocess

    seen: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    apply_module._client_cache.clear()
    monkeypatch.setattr(apply_module, "_probe_client", _Probe(None))
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        apply_module.platform, "docker_prefix", lambda wsl_distro=None, **kw: ("docker",)
    )

    DockerSql("ac-database", "hunter2").run_statement("auth", "SELECT 1")
    DockerSql("ac-database", "hunter2", client="mysql").run_statement("auth", "SELECT 1")

    undeclared, declared = seen
    assert undeclared == declared, (undeclared, declared)


# ------------------------------- the relocation licence needs this app's handwriting


@pytest.mark.parametrize(
    "clone_id",
    ['"clone_id": null, ', '"clone_id": 12, ', '"clone_id": ["x"], ', ""],
    ids=["null", "number", "list", "absent"],
)
def test_the_relocation_licence_needs_a_clone_id_that_is_a_string(
    tmp_path: Path, clone_id: str
) -> None:
    """`remove()`'s weaker proof is "this app's own handwriting", not "close enough".

    A record with `clone_id` missing, null or of the wrong type is MALFORMED,
    not relocated: nothing in it says which folder it was written in, so there
    is no field that has stopped matching. It is `UNKNOWN`, and `UNKNOWN`
    refuses.

    Dropping the `isinstance(..., str)` clause from
    `claim_written_by_this_app()` left the whole suite green (mutation review,
    2026-09-02) — every fixture in this file carried a proper string
    `clone_id`, so nothing ever put a malformed one in front of the predicate.
    Without that clause `parsed.get("clone_id") != install_id(clone)` is
    trivially true for `None`, and a folder whose claim says nothing about
    where it came from is handed the licence to delete a user's checkout and
    run one module's remove-time SQL.

    The relocated claim this is the edge of — a string that simply differs — is
    still accepted; `test_a_moved_install_can_still_be_uninstalled_through_the_app`
    is that half.
    """
    import json

    clone, before = _user_module(tmp_path, checkout_of="ours, from its old path")
    (clone / apply_module.CLAIM_FILE).write_text(
        f'{{"version": {apply_module.CLAIM_VERSION}, "item_id": "mod-ah-bot", '
        f'{clone_id}"url": "{OWNED_URL}"}}\n',
        encoding="utf-8",
    )
    assert json.loads((clone / apply_module.CLAIM_FILE).read_text(encoding="utf-8"))

    assert apply_module.read_clone_claim(clone, item_id="mod-ah-bot") is Ownership.UNKNOWN
    assert apply_module.claim_written_by_this_app(clone, item_id="mod-ah-bot") is False

    sql = _FakeSql()
    m = parse_manifest(
        {**OWNED_ITEM, "sql": [{"db": "world", "statement": "DELETE FROM x", "when": "remove"}]}
    )
    applier = Applier(tmp_path, git=_FakeGit({}), sql=sql, remote_url=_Origins(OWNED_URL))

    with pytest.raises(ApplyError, match=apply_module.CLAIM_FILE):
        applier.remove(m)

    assert (clone / "src" / "mine.cpp").read_bytes() == before
    assert sql.statements == [] and sql.files == []


# ------------------------------------------------- the two AH bot modules
#
# FACT 3 of the 2026-09-07 night's measurements: `mod-ah-bot` and
# `mod-ah-bot-plus` are the only 2 of the 41 shipped manifests carrying a prompt
# with no default, and the Modules tab called the applier with no values at all,
# so the only thing either of them could produce was
# `conf AuctionHouseBot.GUIDs: no value for {bot_guid}` — AFTER the clone.


class _FakeReader(_FakeSql):
    """A `SqlRunner` that can also be read, like the real `DockerSql` can.

    `rows` is what `query()` hands back; `""` is the answer a SELECT gives for
    "no such row", which is the case the whole existence check exists for. A
    reader whose `fail` is set raises instead, which is a DIFFERENT answer and
    must not be confused with the empty one: a database that is stopped has not
    said the character is missing.
    """

    def __init__(self, rows: str = "", fail: str = "") -> None:
        super().__init__()
        self.rows = rows
        self.fail = fail
        self.queries: list[tuple[str, str]] = []

    def query(self, db: str, statement: str) -> str:
        self.queries.append((db, statement))
        if self.fail:
            raise RuntimeError(self.fail)
        return self.rows


def _shipped(item_id: str) -> Any:
    from yulon.controller_wow_wotlk import modules as wotlk_modules

    return wotlk_modules.store().load("module", item_id)


AHBOT_DIST = (
    "[worldserver]\nAuctionHouseBot.Account = 0\nAuctionHouseBot.GUID = 0\n"
    "AuctionHouseBot.GUIDs = 0\nAuctionHouseBot.EnableSeller = 0\n"
)


def test_the_ah_bot_install_refuses_an_unanswered_prompt_before_it_clones(tmp_path: Path) -> None:
    """The headline: no answer installs NOTHING, not a half-installed module."""
    git = _FakeGit({"conf/mod_ahbot.conf.dist": AHBOT_DIST})
    applier = Applier(tmp_path, git=git, sql=_FakeReader(rows="Ahbot\n"))

    with pytest.raises(ApplyError) as refusal:
        applier.install(_shipped("mod-ah-bot"))

    assert "GUID of the AH bot character" in str(refusal.value)
    assert git.calls == [], "it cloned before it checked the answer it was given"
    assert not (tmp_path / "modules" / "mod-ah-bot").exists()


def test_an_answer_that_is_not_a_number_is_refused_before_the_clone(tmp_path: Path) -> None:
    git = _FakeGit({"conf/mod_ahbot.conf.dist": AHBOT_DIST})
    applier = Applier(tmp_path, git=git, sql=_FakeReader(rows="Ahbot\n"))

    for bad, said in (
        ("", "cannot be left empty"),
        ("  ", "cannot be left empty"),
        ("four", "whole number"),
        ("42x", "whole number"),
        ("1.5", "whole number"),
    ):
        with pytest.raises(ApplyError) as refusal:
            applier.install(_shipped("mod-ah-bot-plus"), {"bot_guid": bad})
        assert said in str(refusal.value), (bad, str(refusal.value))
        assert "GUID of the AH bot character" in str(refusal.value), bad
    assert git.calls == []
    assert not (tmp_path / "modules").exists(), "a refused install left a folder behind"


def test_installing_the_ah_bot_with_a_real_guid_writes_that_number(tmp_path: Path) -> None:
    git = _FakeGit({"conf/mod_ahbot.conf.dist": AHBOT_DIST})
    reader = _FakeReader(rows="Ahbot\n")
    report = Applier(tmp_path, git=git, sql=reader).install(
        _shipped("mod-ah-bot"), {"bot_guid": "42", "bot_account": "7"}
    )

    conf = (tmp_path / "env/dist/etc/modules/mod_ahbot.conf").read_text(encoding="utf-8")
    assert "AuctionHouseBot.GUID = 42\n" in conf
    assert "AuctionHouseBot.Account = 7\n" in conf
    assert report.rebuild_required is True
    assert [db for db, _ in reader.queries] == ["characters", "characters"]
    assert "42" in reader.queries[0][1]


def test_a_guid_that_names_no_character_is_refused_by_name(tmp_path: Path) -> None:
    """The silent no-op FACT 5 of the module's own behaviour: it just does nothing."""
    git = _FakeGit({"conf/mod_ahbot.conf.dist": AHBOT_DIST})
    applier = Applier(tmp_path, git=git, sql=_FakeReader(rows=""))

    with pytest.raises(ApplyError) as refusal:
        applier.install(_shipped("mod-ah-bot-plus"), {"bot_guid": "999"})

    assert "999" in str(refusal.value)
    assert "character" in str(refusal.value)
    assert git.calls == []


def test_without_a_reader_the_report_says_the_guid_was_not_checked(tmp_path: Path) -> None:
    """ "I could not check" is said out loud, never spelled like "I checked"."""
    git = _FakeGit({"conf/mod_ahbot.conf.dist": AHBOT_DIST})
    plus = _shipped("mod-ah-bot-plus")
    report = Applier(tmp_path, git=git, sql=_FakeSql()).install(plus, {"bot_guid": "42"})
    assert any(
        "not checked" in s.lower() and "bot_guid" in s for s in report.skipped
    ), report.skipped

    # A reader that RAISED is the same answer, not "the character is missing":
    # a stopped database must not make a module uninstallable.
    stopped = _FakeReader(fail="Error response from daemon: container not running")
    second = _untouched(
        Applier(tmp_path, git=_FakeGit({"conf/mod_ahbot.conf.dist": AHBOT_DIST}), sql=stopped), plus
    )
    report2 = second.install(plus, {"bot_guid": "42"})
    assert any("not checked" in s.lower() for s in report2.skipped), report2.skipped


def test_required_prompts_are_only_the_ones_the_action_actually_renders() -> None:
    """Removing the AH bot renders no template, so it must ask the user nothing."""
    ahbot = _shipped("mod-ah-bot")
    assert [p.key for p in apply_module.required_prompts(ahbot, "install")] == [
        "bot_guid",
        "bot_account",
    ]
    assert apply_module.required_prompts(ahbot, "remove") == ()

    every = _shipped_all()
    unanswered = {
        m.id
        for m in every
        if any(p.default is None for p in apply_module.required_prompts(m, "install"))
    }
    assert unanswered == {"mod-ah-bot", "mod-ah-bot-plus"}, unanswered


def test_every_other_shipped_manifest_still_installs_without_being_asked_anything() -> None:
    """The 39 that were never the problem must not have become uninstallable.

    `_check_values()` began refusing an answer `check_answer()` rejects on
    2026-09-07, as the first statement of `install()`. That is the fix for
    `mod-ah-bot`, and it is also a NEW way for a manifest that worked to stop
    working: a `choice` whose default is not among its own `choices`, a `bool`
    defaulting to a word this app does not know, an `int` defaulting to `"1.5"`
    — each of those used to be written into a conf file unread, and each is now
    a refusal before the clone.

    The sibling test above pins WHICH manifests have to ask a human something.
    This one pins that every other answer the app fills in for itself is one it
    would accept, which is the half that turns a shipped default into a broken
    Install button. Counted on 2026-09-07 over the 41 `wow-wotlk` manifests this
    sweeps: 54 prompts, of which exactly 3 carry no default — `mod-ah-bot`'s two
    and `mod-ah-bot-plus`'s one — so 51 defaults are asserted here.

    It was watched failing before it was kept: setting `hearthstone-cd`'s
    `cooldown` default to `"45_Min_Nope"` produced

        AssertionError: ["hearthstone-cd install cooldown='45_Min_Nope':
        choose one of: 1_Sec, 1_Min, 5_Min, 15_Min, 30_Min"]
    """
    refused = []
    for manifest in _shipped_all():
        for action in ("install", "configure", "remove"):
            for prompt in apply_module.required_prompts(manifest, action):
                if prompt.default is None:
                    continue  # the sibling test owns which manifests may do this
                problem = apply_module.check_answer(prompt, prompt.default)
                if problem:
                    refused.append(
                        f"{manifest.id} {action} {prompt.key}={prompt.default!r}: {problem}"
                    )
    assert refused == [], refused


def _shipped_all() -> list[Any]:
    from yulon.controller_wow_wotlk import modules as wotlk_modules

    store = wotlk_modules.store()
    out: list[Any] = []
    for kind in ("module", "ale", "mod", "keg"):
        try:
            out.extend(store.load_all(kind))
        except Exception:  # a family this game does not ship
            continue
    return out


def test_a_manifest_can_declare_the_restart_it_needs(tmp_path: Path) -> None:
    """`restart_recommended` also has a declared half, independent of the derived one.

    Before T27 the derivation answered yes for NPCs, direct SQL and server DBCs
    only — three things that all reach the database or the data volume — so a
    manifest whose whole content is `conf[].keys` reported "nothing further
    needed" while the change it had just written sat in a file the emulator
    reads only at startup. T27 (`test_a_conf_write_recommends_a_restart_the_
    world_reads_it_at_its_next_start`, below) taught the derivation to see a
    conf write directly. `build.restart` stays for the shape that still is not
    one — a Lua/DB-table "conf", patched or prompted rather than key-written
    (`apply.py:2121`) — and it only ADDS a yes, never takes one away.
    """
    conf = tmp_path / "etc"
    conf.mkdir()
    (conf / "mangosd.conf").write_text('Motd = "old"\n', encoding="utf-8", newline="\n")
    body: dict[str, Any] = {
        "schema_version": 1,
        "id": "motd",
        "name": "Message of the Day",
        "type": "mod",
        "game": "wow-tbc",
        "conf": [{"file": "etc/mangosd.conf", "keys": [{"key": "Motd", "default": '"new"'}]}],
    }

    derived = Applier(tmp_path).install(parse_manifest(body))
    assert derived.rebuild_required is False
    assert derived.restart_recommended is True, "T27: writing the key is visible on its own"

    asked = Applier(tmp_path).install(
        parse_manifest({**body, "build": {"rebuild": False, "restart": True}})
    )
    assert asked.rebuild_required is False
    assert asked.restart_recommended is True

    # It is not a second spelling of `rebuild`: the two are independent.
    both = parse_manifest({**body, "build": {"rebuild": True, "restart": True}})
    assert both.build.rebuild is True and both.build.restart is True

    # And `rebuild` has to be SPELLED OUT beside it, which is not obvious and is
    # why it is asserted rather than left to be discovered: the FIELD defaults
    # to True while `Manifest.build` defaults to the object `Build(rebuild=
    # False)`. So omitting the block entirely means "no rebuild" and writing
    # `"build": {"restart": true}` means "rebuild" — opposite answers, from two
    # defaults one line apart in `manifest.py`, neither of which mentions the
    # other. On a CMaNGOS game, where nothing can be rebuilt at all, that typo
    # is an hour of compiling asked of a user for no change.
    lopsided = parse_manifest({**body, "build": {"restart": True}})
    assert lopsided.build.rebuild is True
    assert parse_manifest(body).build.rebuild is False


_THING_MODULE: dict[str, Any] = {
    "schema_version": 1,
    "id": "mod-thing",
    "name": "Thing",
    "type": "module",
    "game": "wow-wotlk",
    "source": {"repo": "azerothcore/mod-thing"},
    "conf": [
        {
            "file": "env/dist/etc/modules/thing.conf",
            "template": "conf/thing.conf.dist",
        }
    ],
}


def test_a_conf_write_recommends_a_restart_the_world_reads_it_at_its_next_start(
    tmp_path: Path,
) -> None:
    """T27 (`.notes/tickets/T27-...md`, evidence in `.notes/gates/8.6-spec-takes-effect-
    yulon-ubuntu2-2026-09-10/`): activating `mod-playerbots`' conf through the app's own
    seam reported `restart_recommended = False` (`03-activate.log:38`) while the running
    world went on reading its OLD config until the restart of step 04 ("Config::LoadFile:
    Failed open file" before, "Loading TalentSpecs" after — `04-restart-and-list.log:27-32`).

    `_conf()`'s own `done: activate ...` outcome IS the fact the old derivation could not
    see. The sentence it writes names the file and says the world reads it at its next
    start, so a user pressing Install is told the one thing this app cannot do for them.
    """
    git = _FakeGit({"conf/thing.conf.dist": "Thing.Enabled = 1\n"})
    report = Applier(tmp_path, git=git).install(parse_manifest(_THING_MODULE))

    deployed = tmp_path / "env/dist/etc/modules/thing.conf"
    assert deployed.read_text(encoding="utf-8") == "Thing.Enabled = 1\n"
    sentence = next(step for step in report.done if step.startswith("activate "))
    assert "env/dist/etc/modules/thing.conf" in sentence
    assert "the world reads" in sentence and "next start" in sentence
    assert report.rebuild_required is False  # not conflating the two questions
    assert report.restart_recommended is True


def test_a_conf_that_writes_nothing_does_not_recommend_a_restart(tmp_path: Path) -> None:
    """The other half of the same fact: nothing written is nothing to restart for.

    The deployed file already exists — `_conf()` never re-copies over one that is
    already there (`apply.py:2124`) — so this install's own conf step does nothing at
    all, and none of the other three derived causes (NPCs, direct SQL, server DBCs)
    apply either.
    """
    deployed = tmp_path / "env/dist/etc/modules/thing.conf"
    deployed.parent.mkdir(parents=True)
    deployed.write_text("Thing.Enabled = 1\n", encoding="utf-8")

    git = _FakeGit({"conf/thing.conf.dist": "Thing.Enabled = 1\n"})
    report = Applier(tmp_path, git=git).install(parse_manifest(_THING_MODULE))

    assert not any(step.startswith("activate ") for step in report.done)
    assert report.restart_recommended is False


def test_a_module_with_no_conf_leaves_restart_recommended_unchanged(tmp_path: Path) -> None:
    """A manifest with no `conf` at all takes neither branch T27 touched.

    `restart_recommended` is still exactly the four-way OR it always was for such a
    manifest — False here because none of the four apply, unaffected by the new conf
    clause because there is no conf step to run.
    """
    body = {**_THING_MODULE, "conf": ()}
    git = _FakeGit({"README.md": "upstream\n"})
    report = Applier(tmp_path, git=git).install(parse_manifest(body))

    assert not any("conf" in step or "activate" in step for step in report.done)
    assert report.restart_recommended is False


_THING_MODULE_WITH_KEY: dict[str, Any] = {
    **_THING_MODULE,
    "conf": [
        {
            "file": "env/dist/etc/modules/thing.conf",
            "template": "conf/thing.conf.dist",
            "keys": [{"key": "Thing.Level", "default": "5"}],
        }
    ],
}


def test_reapplying_an_identical_keyed_conf_does_not_recommend_a_restart(
    tmp_path: Path,
) -> None:
    """T27 round 2 (Codex adversarial review): `_set_conf_key`'s replace path wrote
    on every call regardless of whether the value actually changed, so re-applying
    an already-identical keyed conf still reported `set N key(s) ... the world
    reads ... at its next start` and recommended a restart though nothing on disk
    changed. The first `_conf()` mutation test only isolated the template-copy
    `target.exists()` skip; this isolates the keyed-write half of the same clause.
    """
    git = _FakeGit({"conf/thing.conf.dist": "Thing.Enabled = 1\n"})
    applier = Applier(tmp_path, git=git)
    m = parse_manifest(_THING_MODULE_WITH_KEY)

    first = applier.install(m)
    assert any(step.startswith("set 1 key(s)") for step in first.done)
    assert first.restart_recommended is True

    second = applier.configure(m)  # same manifest, same values, file already matches
    assert not any(step.startswith("set ") for step in second.done)
    assert second.restart_recommended is False


def test_configuring_ale_asks_for_the_restart_the_engine_needs() -> None:
    """Measured live on `yulon-ubuntu2`, 2026-09-09 (8.7a's fifth clause).

    `applier.configure('mod-ale')` reported `2 step(s), 0 skipped, rebuild=False,
    restart_recommended=False` — "nothing further needed" — over a world that went on
    loading scripts from the OLD `ALE.ScriptPath` for another minute, until this gate
    restarted it by hand: `dml_bridge_ping` still answered *Command 'dml_bridge_ping'
    does not exist* after the write, and answered `DML-BRIDGE-READY` after the restart.

    That is the exact shape the `build.restart` field was added for and its own schema
    description names ("a file the emulator reads only at startup") — TBC's five conf
    mods declare it, and this WotLK module, whose whole configure IS a conf write, did
    not. `configure` never sets `rebuild_required`, so without the field there is
    nothing in the report to tell a user to restart.
    """
    ale = _shipped("mod-ale")
    assert ale.build.rebuild is True, "installing ALE is a C++ module: it needs the compile"
    assert ale.build.restart is True, (
        "configuring ALE writes ALE.Enabled/ALE.ScriptPath into a file the engine reads at "
        "startup; a report that recommends nothing tells the user the value is in force"
    )


# --------------------------------------------------------------------------
# module_updates() — checklist 8.7a, "how far behind each installed module is"
# --------------------------------------------------------------------------


class _FakeBehind:
    """A `BehindReader` that answers from a dict keyed by folder name."""

    def __init__(self, answers: dict[str, int | None]) -> None:
        self.answers = answers
        self.asked: list[tuple[str, str | None]] = []

    def commits_behind(self, dest: Path, branch: str | None) -> int | None:
        self.asked.append((dest.name, branch))
        return self.answers.get(dest.name)


def test_module_updates_reports_one_row_per_installed_checkout(tmp_path: Path) -> None:
    """Installed means "a clone is on disk here", and the row carries the number.

    The list is what the user reads, so it is the enumeration that matters: a
    module the store has never heard of is still installed, and a store entry
    nobody installed is not. Neither of those is a fact about the manifests.
    """
    modules = tmp_path / "modules"
    for name in ("mod-solocraft", "mod-aoe-loot"):
        (modules / name / ".git").mkdir(parents=True)
    # Not a checkout: AzerothCore's own `modules/` carries CMakeLists.txt and
    # friends beside the module folders, and a folder somebody copied in by
    # hand has no `.git` either. Both are named, neither is counted.
    (modules / "not-a-clone").mkdir()
    (modules / "CMakeLists.txt").write_text("x\n", encoding="utf-8")

    git = _FakeBehind({"mod-aoe-loot": 3, "mod-solocraft": 0})
    rows = apply_module.module_updates(tmp_path, git=git)

    assert [(r.key, r.behind, r.is_checkout) for r in rows] == [
        ("mod-aoe-loot", 3, True),
        ("mod-solocraft", 0, True),
        ("not-a-clone", None, False),
    ]
    # Sorted, and a non-checkout is never asked — there is nothing to fetch.
    assert git.asked == [("mod-aoe-loot", None), ("mod-solocraft", None)]


def test_module_updates_asks_each_module_about_its_own_branch(tmp_path: Path) -> None:
    """The branch is the MANIFEST's, because the manifest's is what an update fetches.

    All 21 shipped `wow-wotlk` manifests omit `source.branch`, so `None` is the
    ordinary answer — which is exactly why passing the wrong one would go
    unnoticed until the first module that names one.
    """
    modules = tmp_path / "modules"
    for name in ("mod-a", "mod-b"):
        (modules / name / ".git").mkdir(parents=True)

    git = _FakeBehind({"mod-a": 1, "mod-b": 2})
    apply_module.module_updates(tmp_path, git=git, branches={"mod-a": "wotlk"})

    assert git.asked == [("mod-a", "wotlk"), ("mod-b", None)]


def test_module_updates_keeps_could_not_ask_apart_from_up_to_date(tmp_path: Path) -> None:
    """`None` and `0` are different sentences, and the row has to carry both.

    An offline machine that reported every module as up to date would be the
    same defect this project has now recorded three times: a question with more
    states than the answer being carried.
    """
    modules = tmp_path / "modules"
    for name in ("mod-offline", "mod-current"):
        (modules / name / ".git").mkdir(parents=True)

    rows = apply_module.module_updates(
        tmp_path, git=_FakeBehind({"mod-current": 0, "mod-offline": None})
    )
    by_key = {r.key: r for r in rows}
    assert by_key["mod-current"].behind == 0
    assert by_key["mod-offline"].behind is None
    assert "0 commits behind" in by_key["mod-current"].line
    assert "could not ask" in by_key["mod-offline"].line


def test_module_updates_on_a_server_with_no_modules_folder_is_empty(tmp_path: Path) -> None:
    """The three CMaNGOS games have no `modules/` at all, and that is not an error."""
    assert apply_module.module_updates(tmp_path, git=_FakeBehind({})) == ()


# ----------------------------------------------- a folder instead of a clone
#
# The second way to fill `modules/<id>`: a folder on the user's own disk,
# copied in through a seam, instead of a repository cloned through git. The
# engine learns nothing new about what a module IS — the copy lands at the same
# path, gets the same claim, the same `include.sh`, and the same deploy /
# patch / SQL / conf / client / DBC pass over whatever is now there.
#
# It also learns one hook: a manifest DERIVED from a link or a folder cannot
# know the conf files and SQL directories the content carries until the content
# is on disk, so `complete()` is handed the filled clone and returns the
# manifest the later steps read. There is no second clone and no second report.


class _Copier:
    """The `FolderCopier` seam: records the pair it was handed, materialises `files`.

    A fake for the reason `_FakeGit` is one — the real copier is a `copytree`
    over a folder the user chose, and the property under test is that the
    engine goes through the seam and then treats the result exactly as it
    treats a clone. `fail` is how the `OSError` branch is reached without
    arranging a full disk.
    """

    def __init__(self, files: dict[str, str] | None = None, *, fail: OSError | None = None) -> None:
        self.files = files or {}
        self.fail = fail
        self.calls: list[tuple[Path, Path]] = []

    def __call__(self, src: Path, dest: Path) -> None:
        self.calls.append((src, dest))
        if self.fail is not None:
            raise self.fail
        for rel, text in self.files.items():
            p = dest / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")


FOLDER_MODULE: dict[str, Any] = {
    "id": "mod-my-thing",
    "name": "mod-my-thing",
    "type": "module",
    "game": "wow-wotlk",
    "description": "Custom module (copied from a folder you provided).",
    # Dropped again by `_folder_module()`; here only so pydantic will build the
    # object at all — see that function.
    "source": {"repo": "you/mod-my-thing"},
    "build": {"rebuild": True},
}


def _folder_module(**over: Any) -> Any:
    """The manifest a folder derivation hands this engine: `type='module'`, no `source`.

    Built by parsing and then `model_copy`, not by parsing a sourceless dict,
    and that is a LANE BOUNDARY rather than a shortcut. Today
    `Manifest._shape_by_type` requires a `source` for `type='module'`, and the
    one-clause relaxation that lets a folder-derived manifest through belongs to
    `manifest.py` — another lane's file, which this one may not touch. What the
    applier reads is the OBJECT, so the object is what this builds; when the
    relaxation lands, `parse_manifest` produces the same one and this helper
    becomes a plain parse.
    """
    return parse_manifest({**FOLDER_MODULE, **over}).model_copy(update={"source": None})


def _a_folder_to_copy(tmp_path: Path) -> Path:
    """A directory OUTSIDE the server dir, standing in for the one the user picked."""
    src = tmp_path / "elsewhere" / "mod-my-thing"
    (src / "src").mkdir(parents=True)
    (src / "src" / "mod.cpp").write_text("// the user's module\n", encoding="utf-8")
    return src


def test_install_from_a_folder_copies_through_the_seam_and_then_walks_the_same_steps(
    tmp_path: Path,
) -> None:
    """The whole of the divergence: `git.clone` is replaced by one call to a copier.

    Everything after it is the shipped install, asserted here one item at a
    time because each is a thing a "just copy the folder in" implementation
    would plausibly leave out: the claim that makes the folder recognisable
    next time, the `include.sh` CMake needs, the conf activation, the
    pending-SQL report and the rebuild sentence.
    """
    copier = _Copier(
        {
            "src/mod.cpp": "// the user's module\n",
            "conf/mod_thing.conf.dist": "Thing.Enable = 0\n",
            "data/sql/db-world/a.sql": "-- a",
        }
    )
    git = _FakeGit({"README.md": "upstream\n"})
    origins = _Origins()
    applier = Applier(tmp_path, git=git, remote_url=origins)
    m = _folder_module(
        conf=[
            {"file": "env/dist/etc/modules/mod_thing.conf", "template": "conf/mod_thing.conf.dist"}
        ],
        sql=[{"db": "world", "path": "data/sql/db-world/*.sql", "applied_by": "db-import"}],
    )
    source = _a_folder_to_copy(tmp_path)
    clone = applier.clone_dir(m)

    report = applier.install(m, folder=apply_module.FolderSource(path=source, copier=copier))

    assert copier.calls == [(source, clone)]
    assert git.calls == []  # a copy is not a clone, and git is never asked to make one
    assert apply_module.read_clone_claim(clone, item_id="mod-my-thing") is Ownership.OWNED
    assert (clone / "include.sh").exists()
    conf = tmp_path / "env/dist/etc/modules/mod_thing.conf"
    assert conf.read_text(encoding="utf-8") == "Thing.Enable = 0\n"
    assert f"copy {source} → {clone.relative_to(tmp_path)}" in report.done
    assert "touch include.sh" in report.done
    assert [(p.db, p.files) for p in report.pending_sql] == [
        ("world", ("data/sql/db-world/a.sql",))
    ]
    assert report.rebuild_required is True


def test_a_folder_install_needs_a_copier_and_says_so(tmp_path: Path) -> None:
    """A folder source that put nothing at the clone path is a refusal, not an install.

    Two ways to get there and one answer for both. `FolderSource` will not be
    built without a copier at all — the seam is not optional, because an
    optional one is an install that silently copies nothing. And a copier that
    ran and left no directory (a filter that matched nothing, a copy into the
    wrong place) must not go on to write a claim, touch `include.sh` and report
    a module that is not there.
    """
    with pytest.raises(TypeError, match="copier"):
        apply_module.FolderSource(path=tmp_path)  # type: ignore[call-arg]

    applier = Applier(tmp_path, git=_FakeGit({}), remote_url=_Origins())
    m = _folder_module()
    source = _a_folder_to_copy(tmp_path)
    nothing = _Copier()  # runs, copies nothing

    with pytest.raises(ApplyError) as err:
        applier.install(m, folder=apply_module.FolderSource(path=source, copier=nothing))

    assert nothing.calls == [(source, applier.clone_dir(m))]
    assert str(source) in str(err.value)
    assert not applier.clone_dir(m).exists()


def test_a_copier_that_could_not_read_the_folder_speaks_the_appliers_vocabulary(
    tmp_path: Path,
) -> None:
    """`OSError` out of the seam becomes `ApplyError`, as `GitError` does for a clone.

    One failure vocabulary for the whole applier: every caller of `install()`
    handles `ApplyError`, and a bare `PermissionError` reaching the Modules tab
    would arrive as an unhandled worker exception rather than as a report line.
    """
    applier = Applier(tmp_path, git=_FakeGit({}), remote_url=_Origins())
    source = _a_folder_to_copy(tmp_path)
    refused = _Copier(fail=PermissionError(13, "Permission denied"))

    with pytest.raises(ApplyError, match="Permission denied"):
        applier.install(
            _folder_module(), folder=apply_module.FolderSource(path=source, copier=refused)
        )

    assert not applier.clone_dir(_folder_module()).exists()


def test_a_manifest_with_a_source_and_a_folder_is_one_source_too_many(tmp_path: Path) -> None:
    """Two ways to fill the clone is a caller bug, refused before either one runs.

    Cloning and then copying over it, or copying and then cloning over it, are
    both destructive and neither is what anybody meant. The manifest's `source`
    is what the report and the claim would name, the folder is where the bytes
    would come from — a contradiction this engine must not resolve by picking.
    """
    copier = _Copier({"src/mod.cpp": "// x\n"})
    git = _FakeGit({"README.md": "upstream\n"})
    applier = Applier(tmp_path, git=git, remote_url=_Origins())
    source = _a_folder_to_copy(tmp_path)

    with pytest.raises(ApplyError, match="one source"):
        applier.install(
            parse_manifest(OWNED_ITEM),
            folder=apply_module.FolderSource(path=source, copier=copier),
        )

    assert git.calls == [] and copier.calls == []


def _thing_conf() -> Any:
    """The `conf` tuple a completer would have derived from a `conf/*.conf.dist`."""
    return parse_manifest(
        {
            **FOLDER_MODULE,
            "conf": [
                {
                    "file": "env/dist/etc/modules/mod_thing.conf",
                    "template": "conf/mod_thing.conf.dist",
                }
            ],
        }
    ).conf


def _world_sql() -> Any:
    """The `sql` tuple a completer would have derived from a `data/sql/db-world/`."""
    return parse_manifest(
        {
            **FOLDER_MODULE,
            "sql": [
                {"db": "world", "path": "data/sql/db-world/**/*.sql", "applied_by": "db-import"}
            ],
        }
    ).sql


def test_the_complete_hook_runs_after_the_folder_is_there_and_the_steps_read_what_it_returned(
    tmp_path: Path,
) -> None:
    """The hook exists because a derived manifest cannot know what it is derived FROM.

    A link or a folder yields an id, a name and a type; the conf files to
    activate and the SQL directories to report are in the content, and the
    content is not on disk until the clone or the copy has run. So the hook is
    handed the FILLED clone, and every step after it reads the manifest it
    returned — not the empty one the caller passed in.
    """
    copier = _Copier(
        {"conf/mod_thing.conf.dist": "Thing.Enable = 0\n", "data/sql/db-world/a.sql": "-- a"}
    )
    applier = Applier(tmp_path, git=_FakeGit({}), remote_url=_Origins())
    minimal = _folder_module()  # no conf, no sql: nothing is known yet
    seen: list[tuple[str, tuple[str, ...]]] = []

    def complete(manifest: Any, clone: Path) -> Any:
        # What the hook can see is the whole point: the folder is already there.
        seen.append((manifest.id, tuple(sorted(p.name for p in clone.rglob("*") if p.is_file()))))
        return manifest.model_copy(update={"conf": _thing_conf(), "sql": _world_sql()})

    source = _a_folder_to_copy(tmp_path)
    report = applier.install(
        minimal, folder=apply_module.FolderSource(path=source, copier=copier), complete=complete
    )

    assert seen and seen[0][0] == "mod-my-thing"
    assert "mod_thing.conf.dist" in seen[0][1]  # called AFTER the copy, not before
    assert (tmp_path / "env/dist/etc/modules/mod_thing.conf").is_file()
    assert [(p.db, p.files) for p in report.pending_sql] == [
        ("world", ("data/sql/db-world/a.sql",))
    ]
    # And the manifest handed in is untouched: the hook returns, it does not mutate.
    assert minimal.conf == () and minimal.sql == ()


def test_the_complete_hook_also_finishes_a_manifest_that_was_cloned(tmp_path: Path) -> None:
    """A link derivation knows no more than a folder one, so the hook is not folder-only.

    Same clone path, same shipped `git.clone`, and the conf the hook found in
    what came down is activated by the same pass that cloned it — no second
    clone and no second report.
    """
    git = _FakeGit({"conf/mod_thing.conf.dist": "Thing.Enable = 0\n"})
    applier = Applier(tmp_path, git=git, remote_url=_Origins())
    m = parse_manifest({**FOLDER_MODULE, "id": "mod-linked", "name": "mod-linked"})
    seen: list[bool] = []

    def complete(manifest: Any, clone: Path) -> Any:
        seen.append((clone / "conf" / "mod_thing.conf.dist").is_file())
        return manifest.model_copy(update={"conf": _thing_conf()})

    applier.install(m, complete=complete)

    assert seen == [True]
    assert len(git.calls) == 1
    assert (tmp_path / "env/dist/etc/modules/mod_thing.conf").is_file()


@pytest.mark.parametrize(
    "field, value",
    [("id", "mod-something-else"), ("type", "mod"), ("game", "wow-tbc")],
)
def test_a_complete_hook_that_changes_the_id_is_refused(
    tmp_path: Path, field: str, value: str
) -> None:
    """The hook finishes ONE manifest; it does not get to substitute another.

    Everything downstream of it is addressed by those three fields: the clone
    path already written to (`clone_dir()` reads `type` and `id`), the claim
    already inside that folder, and the `item_id` the report carries. A hook
    returning a different item would report an install of something that was
    never installed, over a folder holding another module's claim — so a
    difference is a refusal rather than a silently relabelled report.
    """
    copier = _Copier({"src/mod.cpp": "// x\n"})
    applier = Applier(tmp_path, git=_FakeGit({}), remote_url=_Origins())
    source = _a_folder_to_copy(tmp_path)

    def swap(manifest: Any, clone: Path) -> Any:
        return manifest.model_copy(update={field: value})

    with pytest.raises(ApplyError, match=field):
        applier.install(
            _folder_module(),
            folder=apply_module.FolderSource(path=source, copier=copier),
            complete=swap,
        )


def test_remove_deletes_a_copied_folder_this_app_claimed_even_without_git(tmp_path: Path) -> None:
    """A copy has no `.git`, and the guard's no-`.git` branch refused everything.

    That branch was written for content somebody else put there — a tarball
    unpacked into `modules/<id>`, a hand-installed module — and until a copy
    could land there, "no `.git`" and "not ours" were the same fact. They are
    not any more: this app's own claim is inside the folder it wrote, and it is
    the same evidence that authorises removing a clone. Without this, a module
    installed from a folder could never be uninstalled through the app.
    """
    copier = _Copier({"src/mod.cpp": "// the user's module\n"})
    sql = _FakeSql()
    applier = Applier(tmp_path, git=_FakeGit({}), sql=sql, remote_url=_Origins())
    m = _folder_module(sql=[{"db": "world", "statement": "DELETE FROM x", "when": "remove"}])
    source = _a_folder_to_copy(tmp_path)
    applier.install(m, folder=apply_module.FolderSource(path=source, copier=copier))
    clone = applier.clone_dir(m)
    assert not (clone / ".git").exists()

    report = applier.remove(m)

    assert not clone.exists()
    assert sql.statements == [("world", "DELETE FROM x")]
    assert any(step.startswith("rm -r ") for step in report.done)
    assert (source / "src" / "mod.cpp").is_file()  # the folder it was copied FROM is untouched


def test_remove_still_refuses_a_hand_made_folder_without_a_claim(tmp_path: Path) -> None:
    """The relaxation above is exactly one clause wide, and this is its other side.

    A REGRESSION test, and said so rather than counted as evidence: it passes
    before the claim is consulted in the no-`.git` branch as well as after,
    because before the change every such folder was refused. Its job starts the
    moment that branch learns to say yes — a relaxation written as "no `.git`
    and nothing here we recognise, let it through" would delete three evenings
    of somebody's work, which is the harm the whole guard exists for.
    """
    sql = _FakeSql()
    applier = Applier(tmp_path, git=_FakeGit({}), sql=sql, remote_url=_Origins())
    m = _folder_module()
    clone = applier.clone_dir(m)
    (clone / "src").mkdir(parents=True)
    mine = clone / "src" / "mine.cpp"
    mine.write_text("// my patch, three evenings\n", encoding="utf-8")
    before = mine.read_bytes()

    with pytest.raises(ApplyError, match="was not put there by this app"):
        applier.remove(m)

    assert mine.read_bytes() == before
    assert sql.statements == [] and sql.files == []


def test_a_second_folder_install_over_this_apps_own_copy_is_allowed_and_a_strangers_is_not(
    tmp_path: Path,
) -> None:
    """Re-choosing the same folder is how a custom module is updated, so it must pass.

    And the folder next to it that this app did not write must still not be
    copied over: the real copier REPLACES its destination, so this guard is the
    only thing between a user's hand-installed `modules/mod-theirs` and losing
    it.
    """
    copier = _Copier({"src/mod.cpp": "// v1\n"})
    applier = Applier(tmp_path, git=_FakeGit({}), remote_url=_Origins())
    m = _folder_module()
    source = _a_folder_to_copy(tmp_path)
    applier.install(m, folder=apply_module.FolderSource(path=source, copier=copier))

    applier.install(m, folder=apply_module.FolderSource(path=source, copier=copier))
    assert len(copier.calls) == 2  # this app's own copy: allowed, and replaced

    theirs = _folder_module(id="mod-theirs", name="mod-theirs")
    hand_made = applier.clone_dir(theirs)
    (hand_made / "src").mkdir(parents=True)
    (hand_made / "src" / "mine.cpp").write_text("// three evenings\n", encoding="utf-8")

    with pytest.raises(ApplyError, match="was not put there by this app"):
        applier.install(theirs, folder=apply_module.FolderSource(path=source, copier=copier))

    assert len(copier.calls) == 2  # the seam is where the harm is, and it was not reached
    assert (hand_made / "src" / "mine.cpp").read_text(encoding="utf-8") == "// three evenings\n"


def test_a_folder_source_never_asks_git_anything(tmp_path: Path) -> None:
    """There is no repository, so every git question is one with no true answer.

    `remote_url()` on a copy answers `None` at best and, on a copy taken from
    somebody's checkout, whatever THAT checkout's origin was — a URL this
    install has nothing to do with. `is_unmodified` and `no_local_commits` are
    the same. The guard must reach its answer from the claim alone, on the
    install and on the remove.
    """
    copier = _Copier({"src/mod.cpp": "// x\n"})
    git = _FakeGit({}, unmodified=True, no_local_commits=True)
    origins = _Origins(OWNED_URL)
    applier = Applier(tmp_path, git=git, remote_url=origins)
    m = _folder_module()
    source = _a_folder_to_copy(tmp_path)

    applier.install(m, folder=apply_module.FolderSource(path=source, copier=copier))
    applier.install(m, folder=apply_module.FolderSource(path=source, copier=copier))
    applier.remove(m)

    assert origins.asked == []
    assert git.asked_about == [] and git.branches_asked == []
    assert git.calls == []


# ------------------------------------------- the running-world guard (8.7a)
#
# `checklist.md:2501`: *a module whose SQL targets the world database is refused
# while the server runs, with the step named and no rows written, and applies
# once the server is stopped.* Owner answer 7 is the rule
# (`phase8-parity-decisions.md:44`); `WORLD_HELD_DBS` is the enumeration.
#
# The subject is the shape the shipped catalog actually has. The brief for this
# work said there was exactly ONE direct SQL step across the four games'
# manifests (`wow-wotlk/modules/mod-arac.json`); loading every manifest through
# `parse_manifest` says **44**, across 30 files and all four games, because
# `SqlStep.applied_by` DEFAULTS to `"direct"` (`manifest.py:136`) and a step that
# names no route is one. So a manifest with several direct `world` steps is the
# ordinary case, not a contrived one — `all-stackables` ships three on install
# and two on remove for TBC, Tortoise and Vanilla — and it is what makes "a
# guard that refuses after the first statement" a shape a test can catch.


STACKABLES: dict[str, Any] = {
    "id": "all-stackables",
    "name": "All Stackables",
    "type": "mod",
    "game": "wow-wotlk",
    "source": {"repo": "DadsMmoLab/dads-mmo-lab", "sparse_path": "mods/all-stackables"},
    "sql": [
        {"db": "world", "statement": "CREATE TABLE yulon_stackable_backup (entry INT);"},
        {"db": "world", "path": "up.sql"},
        {"db": "world", "path": "down.sql", "when": "remove"},
    ],
}


def _stackables_git() -> _FakeGit:
    return _FakeGit({"up.sql": "UPDATE item_template SET stackable = 200;\n", "down.sql": "-- d\n"})


def _stackables_applier(tmp_path: Path, *, world_running: bool) -> Applier:
    """An `Applier` over `STACKABLES` whose `origin` answers as the manifest's own URL.

    T47 (#174) made `install()` ask `_costly_reset()`'s three questions before
    it resets a checkout already at the clone path, and the FIRST of them is
    "is this a checkout of the repository this manifest names?". `_FakeGit`
    answers no `remote_url`, so host git is asked about a directory it did not
    make and returns `None` — which fails closed as "git would not say what it
    is a checkout of" and refuses every SECOND install in these fixtures.

    That refusal is T47's own subject and has its own tests. The tests here are
    about what the claim says after a refused re-install, so the repository
    That refusal is T47's own subject and has its own tests. The tests here are
    about what the claim says after a refused re-install, so all three questions
    are answered the way a clean checkout this app made answers them: the origin
    the manifest names, nothing modified in the tree, no commits of the user's
    own. `_stackables_git()`'s `None`s are left alone for every other caller,
    because for a FIRST install the folder does not exist and the questions are
    never asked.
    """
    source = parse_manifest(STACKABLES).source
    assert source is not None
    git = _FakeGit(
        {"up.sql": "UPDATE item_template SET stackable = 200;\n", "down.sql": "-- d\n"},
        unmodified=True,
        no_local_commits=True,
    )
    return Applier(
        tmp_path,
        git=git,
        sql=_FakeSql(),
        world_running=lambda: world_running,
        remote_url=lambda _dest: source.url,
    )


def _one_step(db: str, **over: Any) -> dict[str, Any]:
    """A manifest whose whole content is one direct SQL step against `db`."""
    return {
        "id": f"one-{db}",
        "name": "One Step",
        "type": "mod",
        "game": "wow-wotlk",
        "source": {"repo": "DadsMmoLab/dads-mmo-lab"},
        "sql": [{"db": db, "statement": "SELECT 1", **over}],
    }


class _Seam:
    """A `world_running` seam that records how often it was asked."""

    def __init__(self, answer: bool | None) -> None:
        self.answer = answer
        self.asked = 0

    def __call__(self) -> bool | None:
        self.asked += 1
        return self.answer


def test_direct_world_sql_is_refused_while_the_world_runs(tmp_path: Path) -> None:
    """The whole clause, in the words the user reads.

    Catches the guard being deleted, and a `raise` softened into a log line:
    the message is asserted whole, so a refusal that stops naming the steps or
    stops saying no rows were written fails here rather than being noticed live.
    """
    sql = _FakeSql()
    applier = Applier(tmp_path, git=_stackables_git(), sql=sql, world_running=lambda: True)

    with pytest.raises(ApplyError) as raised:
        applier.install(parse_manifest(STACKABLES))

    assert str(raised.value) == (
        "all-stackables: the world server is running, and it holds world in memory and writes "
        "back over whatever it finds there. No SQL was run and no rows were written: "
        "sql inline → world, sql up.sql → world. Press Stop, then install again — the steps "
        "this run already took repeat, and the SQL follows them."
    )
    assert sql.statements == [] and sql.files == []


def test_the_refusal_lands_before_the_first_statement_not_between_two(tmp_path: Path) -> None:
    """A guard that refuses after the first statement is worse than none.

    `all-stackables` install-time SQL is `CREATE TABLE … backup` followed by the
    `UPDATE item_template` the backup exists to undo. Run the first and refuse
    the second and the mod is half applied with no way for a reader to tell.
    So the check is a pre-pass over the action's steps and not a test inside the
    loop: this asserts the FIRST statement never reached the runner either, and
    that both steps are named in one sentence.

    Catches the guard moved into `_sql`'s loop or into `_run_sql`, where step 1
    would run and step 2 would be refused.
    """
    sql = _FakeSql()
    applier = Applier(tmp_path, git=_stackables_git(), sql=sql, world_running=lambda: True)

    with pytest.raises(ApplyError) as raised:
        applier.install(parse_manifest(STACKABLES))

    assert sql.statements == []  # the inline step is FIRST and it did not run
    assert sql.files == []
    assert "sql inline → world" in str(raised.value)
    assert "sql up.sql → world" in str(raised.value)


@pytest.mark.parametrize("db", ["characters", "world", "playerbots"])
def test_every_database_a_running_world_holds_is_refused(tmp_path: Path, db: str) -> None:
    """`WORLD_HELD_DBS` is the union of what the pages name, asserted one by one.

    Catches the set shrunk to `{"world"}` — which `checklist.md:2501` alone
    would permit and owner answer 7 and `c-operators-risk.md:90` would not.
    """
    sql = _FakeSql()
    applier = Applier(tmp_path, git=_stackables_git(), sql=sql, world_running=lambda: True)
    with pytest.raises(ApplyError, match="the world server is running"):
        applier.install(parse_manifest(_one_step(db)))
    assert sql.statements == []


def test_an_auth_step_is_never_the_reason_for_a_refusal(tmp_path: Path) -> None:
    """Account rows are not held by the worldserver, and no page asks for them.

    Catches the membership test degenerating to "every step is guarded", which
    would refuse an install with the world up that nothing in the plan forbids.
    """
    sql = _FakeSql()
    applier = Applier(tmp_path, git=_stackables_git(), sql=sql, world_running=lambda: True)
    report = applier.install(parse_manifest(_one_step("auth")))
    assert sql.statements == [("auth", "SELECT 1")]
    assert report.skipped == ()


def test_an_ale_step_runs_because_no_page_names_that_database(tmp_path: Path) -> None:
    """The one place this guard is deliberately silent, recorded as a decision.

    `acore_ale` is the ALE Lua engine's own schema and it lives inside the
    worldserver process, so the reason `characters`/`world`/`playerbots` are
    guarded plausibly reaches it. Owner answer 7, `checklist.md:2501` and both
    design pages name it nowhere, and one shipped step targets it
    (`manifests/wow-wotlk/ale/paragon.json`). Left running rather than quietly
    decided for; this test is what makes the decision visible if it changes.

    Catches `"ale"` being added to `WORLD_HELD_DBS` without an owner answer —
    which would be the same fault in the other direction.
    """
    sql = _FakeSql()
    applier = Applier(tmp_path, git=_stackables_git(), sql=sql, world_running=lambda: True)
    applier.install(parse_manifest(_one_step("ale")))
    assert sql.statements == [("ale", "SELECT 1")]


def test_an_unguarded_step_beside_a_guarded_one_does_not_run_either(tmp_path: Path) -> None:
    """Half a manifest is not an outcome anybody asked for.

    The `auth` step is FIRST, so a guard that filtered step by step and let the
    permitted ones through would have written it before reaching the refusal.
    Nothing is lost by refusing it: the second press re-runs it.

    Catches a per-step filter that runs what it does not refuse.
    """
    data = _one_step("auth")
    data["sql"] = [
        {"db": "auth", "statement": "INSERT INTO account VALUES (1)"},
        {"db": "world", "statement": "UPDATE item_template SET stackable = 200"},
    ]
    sql = _FakeSql()
    applier = Applier(tmp_path, git=_stackables_git(), sql=sql, world_running=lambda: True)
    with pytest.raises(ApplyError, match="the world server is running"):
        applier.install(parse_manifest(data))
    assert sql.statements == []


def test_with_the_world_stopped_every_step_runs(tmp_path: Path) -> None:
    """The other half of the clause: *applies once the server is stopped*.

    Catches the condition inverted, and catches a guard that refuses on any
    answer at all rather than on "not a clear no".
    """
    sql = _FakeSql()
    applier = Applier(tmp_path, git=_stackables_git(), sql=sql, world_running=lambda: False)
    report = applier.install(parse_manifest(STACKABLES))
    assert sql.statements == [("world", "CREATE TABLE yulon_stackable_backup (entry INT);")]
    assert sql.files == [("world", "up.sql")]
    assert report.skipped == ()


def test_a_seam_that_cannot_tell_refuses_rather_than_running(tmp_path: Path) -> None:
    """`None` is "could not ask", which is not "not running" — fail closed.

    The same three-valued discipline `docker._running()` keeps for a project it
    cannot read, and the reason the seam is typed `bool | None`.

    Catches `if running is False: return` written as `if not running: return`,
    where `None` is falsy and the SQL would go into a world nobody asked about.
    """
    sql = _FakeSql()
    applier = Applier(tmp_path, git=_stackables_git(), sql=sql, world_running=lambda: None)
    with pytest.raises(ApplyError) as raised:
        applier.install(parse_manifest(STACKABLES))
    assert "could not tell whether the world server is running" in str(raised.value)
    assert "the seam gave no answer" in str(raised.value)
    assert sql.statements == [] and sql.files == []


def test_a_seam_that_raises_refuses_and_carries_the_reason(tmp_path: Path) -> None:
    """A dead Docker daemon is a refusal with a reason, not a traceback.

    Catches the `try` removed — which turns a probe failure into an unhandled
    exception in the middle of an install — and catches the reason dropped from
    the sentence, which leaves the user a refusal they cannot act on.
    """

    def boom() -> bool:
        raise OSError("docker daemon not reachable")

    sql = _FakeSql()
    applier = Applier(tmp_path, git=_stackables_git(), sql=sql, world_running=boom)
    with pytest.raises(ApplyError) as raised:
        applier.install(parse_manifest(STACKABLES))
    assert "OSError: docker daemon not reachable" in str(raised.value)
    assert sql.statements == [] and sql.files == []


def test_with_no_seam_the_behaviour_is_the_one_every_caller_has_today(tmp_path: Path) -> None:
    """`c-operators-risk.md:345`: with the seam absent, today's behaviour.

    No shipped caller passes `world_running` yet, so this is the path every
    press in the app currently takes — and it must be byte for byte what it was
    before the guard existed, or this change is a regression for four games at
    once. Named here rather than left implicit: **until a caller wires the seam,
    this guard protects nobody.**

    Catches a default that fails closed, which would refuse every install.
    """
    sql = _FakeSql()
    report = Applier(tmp_path, git=_stackables_git(), sql=sql).install(parse_manifest(STACKABLES))
    assert sql.statements == [("world", "CREATE TABLE yulon_stackable_backup (entry INT);")]
    assert sql.files == [("world", "up.sql")]
    assert report.skipped == ()


def test_the_db_import_route_is_not_touched_by_this_guard(tmp_path: Path) -> None:
    """The other route's guard is `docker.apply_module_sql()`, not this one.

    A db-import step writes nothing here — it is resolved into `pending_sql` and
    handed to upstream's importer, which `docker.py:2029-2036` refuses while the
    world is up. Refusing it here as well would refuse an install that writes
    nothing, and would break the one route that already works.

    Catches `applied_by == "direct"` dropped from the filter.
    """
    sql = _FakeSql()
    applier = Applier(
        tmp_path, git=_ahbot_git("data/sql/db-world/a.sql"), sql=sql, world_running=lambda: True
    )
    report = applier.install(parse_manifest(MODULE), {"bot_guid": "42"})
    assert [p.db for p in report.pending_sql] == ["world"]
    assert sql.statements == [] and sql.files == []


def test_with_no_runner_the_older_message_survives_the_guard(tmp_path: Path) -> None:
    """An install with no database writes nothing, so there is nothing to refuse.

    Refusing here would replace a true sentence ("no SQL runner configured")
    with one about a write that was never going to happen.

    Catches the `self.sql is None` early return dropped from the guard.
    """
    applier = Applier(tmp_path, git=_stackables_git(), world_running=lambda: True)
    report = applier.install(parse_manifest(STACKABLES))
    assert report.skipped == ("sql → world: no SQL runner configured",) * 2


def test_the_seam_is_not_asked_for_an_action_with_no_direct_sql(tmp_path: Path) -> None:
    """Reading it costs a Docker call, and a refusal about nothing is noise.

    `all-stackables` has install-time and remove-time SQL and none at configure
    time, so `configure()` must not ask — and must not refuse.

    Catches `step.when == when` dropped from the filter, which would refuse a
    configure over a manifest whose SQL belongs to a different action.
    """
    seam = _Seam(True)
    applier = Applier(tmp_path, git=_stackables_git(), sql=_FakeSql(), world_running=seam)
    report = applier.configure(parse_manifest(STACKABLES))
    assert seam.asked == 0
    assert report.done == () and report.skipped == ()


def test_remove_time_sql_is_refused_and_the_clone_survives_to_be_undone(tmp_path: Path) -> None:
    """The case that decides refuse-versus-skip, and the reason it is a raise.

    A mod's remove-time SQL is its UNDO — `all-stackables` puts `item_template`
    back from its backup table — and the statements straight after `_sql()` in
    `remove()` delete the clone that file lives in. Report it as `skipped` and
    the run reports a clean removal while the world keeps the change and the
    only copy of the undo is gone. `remove()`'s own comment already puts its
    refusals before the SQL for exactly this reason.

    Catches the refusal turned into a `skipped` line, which would pass a test
    that only ever looked at `install()`.
    """
    down = tmp_path / "sql_scripts" / "clones" / "all-stackables" / "down.sql"
    installed = Applier(tmp_path, git=_stackables_git(), sql=_FakeSql())
    installed.install(parse_manifest(STACKABLES))
    assert down.is_file()

    sql = _FakeSql()
    up = Applier(tmp_path, git=_stackables_git(), sql=sql, world_running=lambda: True)
    with pytest.raises(ApplyError) as raised:
        up.remove(parse_manifest(STACKABLES))

    assert "sql down.sql → world" in str(raised.value)
    assert "then remove again" in str(raised.value)
    assert sql.files == []
    assert down.is_file()  # the undo is still on disk to be run once the world is down


# ------------------------------- the seam, wired, and the way back from Stop (T7)
#
# T2 pressed the guard above against a real running worldserver and it held
# (`.notes/gates/8.7a-direct-sql-yulon-ubuntu2-2026-09-09/`). It also found the
# two things this section is about, and neither is a defect in the guard:
#
# 1. Nothing shipped passed `world_running`, so `_world_running` was `None` on
#    all four games and the guard returned at its first line. The press wired
#    the seam by hand; today's Modules tab would have written 7 219 rows into a
#    live world without a word.
# 2. The refusal ends *"Press Stop, then install again"*, and that instruction
#    could not be followed: the app's Stop takes the database down with the
#    world, and the direct SQL step then died on `container ... is not running`.
#    `docker.start_database()` put the database back alone in 6.6 s, so the
#    route was missing a CALLER, not a primitive.


class _StartDb:
    """A `start_database` seam that records its presses and can refuse."""

    def __init__(self, started: bool = True, boom: Exception | None = None) -> None:
        self.started = started
        self.boom = boom
        self.calls = 0

    def __call__(self) -> bool:
        self.calls += 1
        if self.boom is not None:
            raise self.boom
        return self.started


def test_the_stopped_world_path_starts_the_database_and_says_so(tmp_path: Path) -> None:
    """The dead end T2 measured, closed: Stop, then install, and it succeeds.

    With the world down the guard permits, and the database is down too because
    the app's Stop took the whole project with it. The seam starts it alone --
    the world stays stopped, which is the state the guard is about -- and the
    report says so, because a run that started a container and did not mention
    it leaves the operator's install in a state they did not ask for.

    Catches the `start_database` call deleted from `_sql()` (T2's
    `container ... is not running` comes straight back), and the report line
    dropped, which would leave a started database unaccounted for.
    """
    sql = _FakeSql()
    start = _StartDb(started=True)
    applier = Applier(
        tmp_path,
        git=_stackables_git(),
        sql=sql,
        world_running=lambda: False,
        start_database=start,
    )

    report = applier.install(parse_manifest(STACKABLES))

    assert start.calls == 1
    assert "started the database alone; the world server was left stopped" in report.done
    assert sql.statements == [("world", "CREATE TABLE yulon_stackable_backup (entry INT);")]
    assert sql.files == [("world", "up.sql")]


def test_a_database_that_is_already_up_puts_no_line_in_the_report(tmp_path: Path) -> None:
    """`start_database()` no-ops on a running database, and so must the sentence.

    Catches the report line appended unconditionally, which would claim a start
    on every ordinary install -- the shape of `PendingSql`'s closed bug, a
    `done` entry for something that did not happen.
    """
    sql = _FakeSql()
    start = _StartDb(started=False)
    applier = Applier(
        tmp_path,
        git=_stackables_git(),
        sql=sql,
        world_running=lambda: False,
        start_database=start,
    )

    report = applier.install(parse_manifest(STACKABLES))

    assert start.calls == 1
    assert not [line for line in report.done if "database" in line]
    assert sql.files == [("world", "up.sql")]


def test_a_database_that_will_not_start_refuses_with_the_daemons_own_sentence(
    tmp_path: Path,
) -> None:
    """No database, no SQL -- and the reason is the one Docker gave, not a paraphrase.

    `docker.start_database()` raises `DockerCommandError` with the container's
    name, the timeout and where the logs are; that sentence is the only thing
    in the room that knows why, so it is carried whole.

    Catches the seam's exception swallowed and the SQL run anyway (statements
    sent at a database that is not there, half-applied), and the refusal
    reworded into something that names no steps.
    """
    sql = _FakeSql()
    start = _StartDb(boom=RuntimeError("ac-database did not report healthy within 120s"))
    applier = Applier(
        tmp_path,
        git=_stackables_git(),
        sql=sql,
        world_running=lambda: False,
        start_database=start,
    )

    with pytest.raises(ApplyError) as raised:
        applier.install(parse_manifest(STACKABLES))

    assert str(raised.value) == (
        "all-stackables: the database could not be started, so no SQL was run and no rows were "
        "written: sql inline → world, sql up.sql → world. "
        "ac-database did not report healthy within 120s"
    )
    assert sql.statements == [] and sql.files == []


def test_the_database_is_not_started_for_an_action_with_no_direct_sql(tmp_path: Path) -> None:
    """Starting a container costs a Docker call and a running database.

    `all-stackables` has install-time and remove-time SQL and none at configure
    time, so a configure must not reach for the database at all.

    Catches the `when` filter dropped, which would start a database for every
    press of every button on the Modules tab.
    """
    start = _StartDb()
    applier = Applier(
        tmp_path,
        git=_stackables_git(),
        sql=_FakeSql(),
        world_running=lambda: False,
        start_database=start,
    )

    applier.configure(parse_manifest(STACKABLES))

    assert start.calls == 0


def test_a_live_world_is_refused_before_any_database_is_started(tmp_path: Path) -> None:
    """The order is the whole of it: refuse first, start second.

    Swap them and a press against a live world starts containers before saying
    no -- and on a stack the user has Stopped, the guard's own advice would be
    undone by the guard's own run.

    Catches the two calls transposed in `_sql()`.
    """
    start = _StartDb()
    applier = Applier(
        tmp_path,
        git=_stackables_git(),
        sql=_FakeSql(),
        world_running=lambda: True,
        start_database=start,
    )

    with pytest.raises(ApplyError):
        applier.install(parse_manifest(STACKABLES))

    assert start.calls == 0


def test_without_the_start_seam_the_route_behaves_exactly_as_before(tmp_path: Path) -> None:
    """Absent means today's behaviour, byte for byte -- the rule the guard follows too.

    Catches the seam made mandatory, which would break every caller that has no
    Docker to offer (`apply_module()` on a folder, the tests above).
    """
    sql = _FakeSql()
    applier = Applier(tmp_path, git=_stackables_git(), sql=sql, world_running=lambda: False)

    report = applier.install(parse_manifest(STACKABLES))

    assert not [line for line in report.done if "database" in line]
    assert sql.files == [("world", "up.sql")]


def test_no_runner_means_no_database_is_started_either(tmp_path: Path) -> None:
    """A run that writes nothing has nothing to start a database for.

    Catches the `self.sql is None` early return dropped, which would start a
    database for an install whose every SQL step is reported as skipped.
    """
    start = _StartDb()
    applier = Applier(
        tmp_path, git=_stackables_git(), world_running=lambda: False, start_database=start
    )

    applier.install(parse_manifest(STACKABLES))

    assert start.calls == 0


# ------------------------------------------- every applier the app builds (T7)


def _seam_source(value: ast.expr, name: str) -> str:
    """What a seam keyword is bound to, reduced to one of three answers.

    `"passthrough"` -- a bare name IDENTICAL to the keyword, which is how every
    factory and `apply_module()` hands the caller's own seam on. The name has to
    match: `world_running=some_other_reading` is a different fact under the
    right label, and reading any `ast.Name` as a pass-through (round 1 did) let
    that through the audit unseen.
    `"docker.<name>"` -- a lambda over the `docker` function of that name, which
    is the shipped wiring; anything else comes back as `"other"` and fails the
    audit by name.
    """
    if isinstance(value, ast.Name):
        return "passthrough" if value.id == name else "other"
    if isinstance(value, ast.Lambda) and isinstance(value.body, ast.Call):
        called = value.body.func
        if isinstance(called, ast.Attribute) and isinstance(called.value, ast.Name):
            return f"{called.value.id}.{called.attr}"
    return "other"


def _applier_call_sites() -> list[tuple[str, int, set[str | None]]]:
    """Every call in `yulon/` that builds an `Applier` or asks a factory for one.

    An AST walk rather than a grep, because the same construction is spelled
    four ways in this tree -- `Applier(`, `GuardedApplier(`, a bare `applier(`
    inside a game's own `apply_module()`, and `wotlk_modules.applier(` from the
    view -- and a text search that knew about three of them would report a clean
    audit over the fourth (`pyplan` records this as *audit by argv, not by
    string*).

    WHAT IT ENUMERATES: every `ast.Call` in `yulon/**.py` whose callee name ends
    in `Applier` or `applier`, wherever it is written.

    WHAT IT CANNOT SEE, said plainly rather than left to be discovered -- round
    1 of this ticket claimed the count made the walk exhaustive, and it does
    not. A construction reached under any other name is invisible to it:
    `Build = Applier` then `Build(...)`; `functools.partial(Applier, ...)`;
    `cls(...)` inside a classmethod; a factory held in a variable or a dict and
    called through it. None of those changes the count, because the walk never
    counted them in the first place.

    THE REAL BACKSTOP is not this test: `world_running` is a REQUIRED
    keyword-only parameter on all four factories, so a caller that omits it
    fails at the call and under mypy, whatever it is spelled. This walk is the
    second line -- it catches a direct `Applier(...)` that bypasses the
    factories, and (with `_seam_bindings` below) a site whose seam is present
    and reads the wrong thing.
    """
    root = Path(__file__).resolve().parents[1] / "yulon"
    sites: list[tuple[str, int, set[str | None]]] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if not name.endswith(("Applier", "applier")):
                continue
            sites.append(
                (path.relative_to(root).as_posix(), node.lineno, {k.arg for k in node.keywords})
            )
    return sites


def _seam_bindings(seam: str) -> list[tuple[str, str]]:
    """`(file:line, what the seam is bound to)` for every site that passes `seam`."""
    root = Path(__file__).resolve().parents[1] / "yulon"
    found: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if not name.endswith(("Applier", "applier")):
                continue
            for keyword in node.keywords:
                if keyword.arg == seam:
                    where = f"{path.relative_to(root).as_posix()}:{node.lineno}"
                    found.append((where, _seam_source(keyword.value, seam)))
    return found


def test_no_applier_the_app_builds_is_left_without_the_world_running_seam() -> None:
    """The one thing between 8.7a's second clause and a defence (T2's finding 1).

    Every one of these sites passed nothing until T7, so `Applier._world_running`
    was `None` in the shipped app and the guard returned at its first line for
    all four games.

    Catches a construction site added without the seam where this walk can see
    it -- a new game's factory, a second applier on a tab -- and, through the
    count, a site added in a spelling it CAN follow but nobody thought to check.
    A site spelled some other way is not caught here at all; `_applier_call_sites`
    says which shapes those are, and the required keyword on all four factories
    is what stops them.
    """
    sites = _applier_call_sites()
    where = [f"{f}:{n}" for f, n, _ in sites]

    assert [f"{f}:{n}" for f, n, kw in sites if "world_running" not in kw] == []
    assert [f"{f}:{n}" for f, n, kw in sites if "start_database" not in kw] == []
    assert len(sites) == 13, where


def test_the_seam_every_site_passes_reads_the_world_the_three_valued_way() -> None:
    """A seam that is PRESENT and answers `False` for "could not ask" is worse than none.

    `container_state(...).settled` is `False` when Docker will not answer, and
    through this guard `False` is fail-OPEN: it is the one answer that lets SQL
    into a live world's tables. My Party's group next door in the view reads
    exactly that property, correctly for its own question, and T2's press left a
    written warning that a wiring which copied it verbatim would pass every
    audit that only looked for the keyword. This is that audit not stopping at
    the keyword.

    Two bindings are permitted and no third: the enclosing function's own
    parameter (every factory hands the caller's seam on) and a lambda over
    `docker.world_running()` / `docker.start_database()`, which are the
    functions that own the mapping and the primitive.

    Catches a site rewired to `docker.container_state(...).settled`, to
    `status == "running"` alone, or to a constant -- none of which the test
    above can see, because all of them spell the keyword correctly.
    """
    assert {source for _, source in _seam_bindings("world_running")} == {
        "passthrough",
        "docker.world_running",
    }
    assert {source for _, source in _seam_bindings("start_database")} == {
        "passthrough",
        "docker.start_database",
    }
    # Four games, four real readings, and all of them in the one file that knows
    # this install's container spec and WSL distro. By file rather than by line:
    # a line number here would go stale on the next edit above it and be
    # "corrected" by whoever hit it, which is how an audit stops auditing.
    real = [w for w, s in _seam_bindings("world_running") if s == "docker.world_running"]
    assert [w.split(":")[0] for w in real] == ["ui/controller_view.py"] * 4, real


def test_the_audit_reads_a_differently_named_pass_through_as_a_stranger() -> None:
    """The audit's own unit test, because round 1's version could not tell them apart.

    `_seam_source` accepted ANY bare name as a pass-through, so
    `world_running=some_other_reading` -- a different fact wearing the right
    label -- read as clean. No site in the tree spells it that way today, which
    is exactly why the tightening needs a test of its own rather than a mutation
    of the shipping code: there is nothing live for such a mutation to break.

    Catches `value.id == name` loosened back to `isinstance(value, ast.Name)`.
    """
    call = ast.parse("f(world_running=world_running, start_database=something_else)").body[0]
    assert isinstance(call, ast.Expr) and isinstance(call.value, ast.Call)
    same, different = call.value.keywords
    assert _seam_source(same.value, "world_running") == "passthrough"
    assert _seam_source(different.value, "start_database") == "other"


def test_the_shipped_manifests_this_guard_stands_in_front_of() -> None:
    """How much is on this path, counted rather than asserted (T2's correction).

    The brief for the press called `mod-arac` *the only shipped manifest with a
    direct world-SQL step*. `SqlStep.applied_by` DEFAULTS to `"direct"`
    (`manifest.py:136`), so every step that names no route is one: 44 steps
    across 19 manifests in all four games. `mod-arac` and `mod-city-bots` are
    the only `module`-type ones, which is the narrower true statement.

    One direct step in the tree is outside these numbers:
    `wow-wotlk/ale/paragon.json`'s, into `ale` -- outside `WORLD_HELD_DBS`,
    which is what makes the two numbers differ and why this counts the set the
    guard names rather than every direct step.

    It was 43 across 18 until T63 added `mod-city-bots`, whose citizen-roster
    import is a direct step into `playerbots`. That is not an incidental bump:
    `playerbots` is in `WORLD_HELD_DBS`, so the roster import is the second
    module-type write this guard stands in front of, and the count moving is
    what says the new step joined the guarded set rather than slipping past it.

    Catches `WORLD_HELD_DBS` narrowed and the `applied_by` default flipped to
    `db-import`: either would empty this guard's blast radius without a word,
    and both would leave every other test above green.
    """
    root = Path(__file__).resolve().parents[1] / "manifests"
    steps, files, games = 0, set(), set()
    for path in sorted(root.glob("*/*/*.json")):
        manifest = parse_manifest(json.loads(path.read_text(encoding="utf-8")))
        at_risk = [
            step
            for step in manifest.sql
            if step.applied_by == "direct" and step.db in apply_module.WORLD_HELD_DBS
        ]
        if at_risk:
            steps += len(at_risk)
            files.add(path)
            games.add(path.parent.parent.name)

    assert (steps, len(files), sorted(games)) == (
        44,
        19,
        ["wow-tbc", "wow-tortoise", "wow-vanilla", "wow-wotlk"],
    )


# ------------------- the window the database start opens (T7, round 2)
#
# Codex's must-fix. The first reading of `world_running` is taken, then
# `start_database()` may block for up to 180 s waiting for the database to
# report healthy (`docker._DB_HEALTHY_TIMEOUT_SECONDS`), and only then is the
# first statement sent. The Server tab's Start is a button the same user can
# press inside that window, and `compose up` in another terminal needs no
# button at all. A world that came up there is holding these tables when the
# writes land -- and the report would have said it was left stopped.


class _WorldThatComesUp:
    """A `world_running` seam that answers one way, then another.

    The first answer is the ground the press is permitted on; the second is
    what the world did while the database was being waited for.
    """

    def __init__(self, then: bool | None) -> None:
        self.answers: list[bool | None] = [False, then]
        self.asked = 0

    def __call__(self) -> bool | None:
        answer = self.answers[min(self.asked, len(self.answers) - 1)]
        self.asked += 1
        return answer


def test_a_world_started_while_the_database_came_up_is_refused_before_the_first_statement(
    tmp_path: Path,
) -> None:
    """The race, closed: the reading the SQL runs on is taken after the wait, not before.

    Catches the second `_refuse_direct_sql_into_a_running_world()` call dropped
    from `_sql()`, which is the whole fix; and the guard asked again but its
    answer ignored. Both leave every other test in this file green, because
    every one of them holds the world still.
    """
    sql = _FakeSql()
    seam = _WorldThatComesUp(then=True)
    start = _StartDb(started=True)
    applier = Applier(
        tmp_path, git=_stackables_git(), sql=sql, world_running=seam, start_database=start
    )

    with pytest.raises(ApplyError) as raised:
        applier.install(parse_manifest(STACKABLES))

    assert str(raised.value) == (
        "all-stackables: the world server is running, and it holds world in memory and writes "
        "back over whatever it finds there. No SQL was run and no rows were written: "
        "sql inline → world, sql up.sql → world. Press Stop, then install again — the steps "
        "this run already took repeat, and the SQL follows them."
    )
    assert sql.statements == [] and sql.files == []
    assert seam.asked == 2, "once for the permission, once for the reading the SQL runs on"
    assert start.calls == 1, "the database was started, and that is why the window existed"


def test_a_world_that_stops_answering_while_the_database_came_up_is_refused_too(
    tmp_path: Path,
) -> None:
    """`None` after the wait is a refusal, for the reason it is one before it.

    *Could not ask* is not *not running*, and a Docker that stopped answering
    mid-press is the case where a guard most wants to be closed: the daemon may
    be busy starting the very world this refusal is about.

    Catches the second reading narrowed to `if running is True` -- which would
    let an unreadable Docker through on exactly the press that waited two
    minutes for it.
    """
    sql = _FakeSql()
    seam = _WorldThatComesUp(then=None)
    applier = Applier(
        tmp_path,
        git=_stackables_git(),
        sql=sql,
        world_running=seam,
        start_database=_StartDb(started=True),
    )

    with pytest.raises(ApplyError) as raised:
        applier.install(parse_manifest(STACKABLES))

    assert "could not tell whether the world server is running" in str(raised.value)
    assert "sql inline → world, sql up.sql → world" in str(raised.value)
    assert sql.statements == [] and sql.files == []
    assert seam.asked == 2


def test_a_world_that_stayed_down_is_asked_twice_and_the_sql_runs(tmp_path: Path) -> None:
    """The ordinary stopped-world press, and the price of the second reading.

    Two `docker inspect`s per install that has direct world SQL, not one. Worth
    saying out loud: the second is only taken when the start seam was actually
    consulted, so a caller with no Docker to offer still pays for exactly one.

    Catches the second reading turned into a refusal on `False` (every stopped
    install would refuse), and the whole re-check made unconditional, which
    would ask twice on the no-seam path this engine promises to leave alone.
    """
    sql = _FakeSql()
    seam = _WorldThatComesUp(then=False)
    applier = Applier(
        tmp_path,
        git=_stackables_git(),
        sql=sql,
        world_running=seam,
        start_database=_StartDb(started=True),
    )

    report = applier.install(parse_manifest(STACKABLES))

    assert seam.asked == 2
    assert sql.files == [("world", "up.sql")]
    assert "started the database alone; the world server was left stopped" in report.done


def test_without_a_start_seam_the_world_is_read_exactly_once(tmp_path: Path) -> None:
    """No start, no window, no second Docker call.

    Catches the re-check hoisted out of the `if`, which would double the cost of
    every press on every caller that has no database to start.
    """
    seam = _Seam(False)
    applier = Applier(tmp_path, git=_stackables_git(), sql=_FakeSql(), world_running=seam)

    applier.install(parse_manifest(STACKABLES))

    assert seam.asked == 1


# ------------------- WotLK, the game the press was run on (T7, round 2)


def test_the_wotlk_factory_hands_the_seam_to_the_guard(tmp_path: Path) -> None:
    """The arrival test for the one game T2 actually pressed.

    `wotlk_modules.applier()` is the only one of the four that can build its own
    `DockerSql` (this game's database password is a fixed catalog value), so a
    test that omitted `sql=` would reach the real Docker CLI rather than the
    guard. The runner here is a fake for that reason, not for the guard's.

    Catches `world_running` accepted by that factory and dropped on the way to
    `Applier(...)` -- the defect T7 exists to close, on the game whose live
    press recorded it.
    """
    from yulon.controller_wow_wotlk import modules as wotlk_modules

    sql = _FakeSql()
    applier = wotlk_modules.applier(
        tmp_path, git=_stackables_git(), sql=sql, world_running=lambda: True
    )

    with pytest.raises(ApplyError) as raised:
        applier.install(parse_manifest(STACKABLES))

    assert "the world server is running" in str(raised.value)
    assert sql.statements == [] and sql.files == []


def test_the_wotlk_factory_hands_over_the_database_start_as_well(tmp_path: Path) -> None:
    """The other seam, through the same factory: Stop, then install, and it applies.

    Catches `start_database` accepted by the WotLK factory and dropped, which
    would leave the game T2 measured with the guard armed and its own
    instruction still unfollowable.
    """
    from yulon.controller_wow_wotlk import modules as wotlk_modules

    sql = _FakeSql()
    start = _StartDb(started=True)
    applier = wotlk_modules.applier(
        tmp_path,
        git=_stackables_git(),
        sql=sql,
        world_running=lambda: False,
        start_database=start,
    )

    report = applier.install(parse_manifest(STACKABLES))

    assert start.calls == 1
    assert "started the database alone; the world server was left stopped" in report.done
    assert sql.files == [("world", "up.sql")]


def test_install_refuses_a_module_that_conflicts_with_one_already_here(tmp_path: Path) -> None:
    """`conflicts_with` is enforced, not merely parsed (T53).

    The catalog has recorded for a long time that `mod-ah-bot` and
    `mod-ah-bot-plus` are alternatives -- each names the other in
    `conflicts_with`, the schema parses it, and `test_manifest.py` asserts it
    round-trips. Nothing ever read it at install time. So Yu'lon installed both,
    and the user found out 21 minutes later when the LINKER stopped:

        mod-ah-bot-plus/src/ah_bot_loader.cpp:18: multiple definition of
        `Addmod_ah_botScripts()'; mod-ah-bot/src/ah_bot_loader.cpp:12: first
        defined here

    A declaration nothing acts on is the shape this repository has paid for
    before. The refusal is raised BEFORE anything is written, next to the "one
    source, not two" check, so a refused install leaves the tree exactly as it
    was.
    """
    git = _FakeGit({"README.md": "upstream\n"})
    applier = Applier(tmp_path, git=git, remote_url=_Origins(OWNED_URL))

    first = parse_manifest(
        {**OWNED_ITEM, "id": "mod-ah-bot", "conflicts_with": ["mod-ah-bot-plus"]}
    )
    second = parse_manifest(
        {**OWNED_ITEM, "id": "mod-ah-bot-plus", "conflicts_with": ["mod-ah-bot"]}
    )

    applier.install(first)
    assert applier.clone_dir(first).is_dir()

    with pytest.raises(ApplyError) as caught:
        applier.install(second)

    said = str(caught.value)
    assert "mod-ah-bot" in said and "mod-ah-bot-plus" in said, said
    assert "Nothing was changed" in said, said
    # And it really changed nothing: no second clone, and the first is untouched.
    assert not applier.clone_dir(second).exists(), "the refused install still cloned"
    assert (applier.clone_dir(first) / "README.md").read_text(encoding="utf-8") == "upstream\n"


def test_install_is_unaffected_when_the_conflicting_module_is_not_installed(tmp_path: Path) -> None:
    """The guard must not refuse the ordinary case, which is the easy mistake here.

    Declaring a conflict says nothing about whether the other thing is present.
    A catalog where four mods all name each other (buff/xbuff/nerf/baby-mobs)
    would be uninstallable if the check read the declaration rather than the
    disk.
    """
    git = _FakeGit({"README.md": "upstream\n"})
    applier = Applier(tmp_path, git=git, remote_url=_Origins(OWNED_URL))
    lonely = parse_manifest(
        {**OWNED_ITEM, "id": "mod-ah-bot-plus", "conflicts_with": ["mod-ah-bot"]}
    )
    report = applier.install(lonely)
    assert applier.clone_dir(lonely).is_dir()
    assert report.item_id == "mod-ah-bot-plus"


def test_a_conflict_is_found_in_another_familys_clone_folder(tmp_path: Path) -> None:
    """The search is over every family's folder, not this manifest's own (T53).

    Asserted because `_conflict_refusal()` says so in its docstring, and a
    docstring is not a guard. Families live in different directories --
    `CLONE_DIRS` maps module to `modules/`, ale and keg to `ale_scripts/`, mod
    to `sql_scripts/clones/` -- so a check that looked only where the manifest
    being installed would land is one line away, reads correctly, and silently
    misses a conflict that reaches across families.
    """
    git = _FakeGit({"README.md": "upstream\n"})
    applier = Applier(tmp_path, git=git, remote_url=_Origins(OWNED_URL))
    # An ALE script is already on disk, in ale_scripts/ -- NOT in modules/.
    (tmp_path / "ale_scripts" / "some-ale-script" / ".git").mkdir(parents=True)

    module = parse_manifest(
        {**OWNED_ITEM, "id": "mod-thing", "conflicts_with": ["some-ale-script"]}
    )
    with pytest.raises(ApplyError) as caught:
        applier.install(module)
    assert "some-ale-script" in str(caught.value)
    assert "ale_scripts" in str(caught.value), str(caught.value)
    assert not applier.clone_dir(module).exists()


def test_install_refuses_a_module_whose_requirement_is_not_installed(tmp_path: Path) -> None:
    """`requires` is enforced at install, not merely parsed (T69).

    The twin of `conflicts_with`'s defect, and worse in one way: a conflict
    fails loudly, at the linker, twenty minutes in. A missing requirement fails
    SILENTLY. Loot Pet and SitMeansRest declare `mod-ale`, installed cleanly on
    a server with no Lua engine, reported success -- and then the script never
    ran, with no line anywhere saying why.

    The Modules tab locks the row for the same reason, and this is the half
    that is a guarantee: the context menu and a custom-folder install reach
    `install()` without the button.

    Exactly one rule can refuse this fixture. It declares no `conflicts_with`,
    nothing else is on disk for a conflict to find, and the refusal is matched
    against `requirement_refusal()`'s own sentence rather than against a phrase
    the neighbouring guards also contain.
    """
    from yulon.apply import requirement_refusal

    git = _FakeGit({"README.md": "upstream\n"})
    applier = Applier(tmp_path, git=git, remote_url=_Origins(OWNED_URL))
    needy = parse_manifest({**OWNED_ITEM, "id": "mod-thing", "requires": ["mod-ale"]})

    with pytest.raises(ApplyError) as caught:
        applier.install(needy)

    assert str(caught.value) == (
        requirement_refusal("mod-thing", "mod-ale") + " Nothing was changed."
    )
    # And it really changed nothing: the refusal is raised before the clone.
    assert not applier.clone_dir(needy).exists(), "the refused install still cloned"


def test_a_requirement_the_server_install_cloned_lets_the_install_through(
    tmp_path: Path,
) -> None:
    """A folder under `modules/` is the requirement, whoever put it there (T69).

    `mod-city-bots` requires `mod-playerbots`, which has no manifest at all:
    `catalog.json` lists it among wow-wotlk's emulator sources with `dest:
    modules/mod-playerbots`, so the server install clones it. A guard that
    resolved `requires` against the catalog would refuse City Bots forever on
    every machine that in fact has its requirement.

    The fixture answers differently on the second read and only the DISK moves
    between them, so this cannot pass by the guard being absent: the first half
    refuses, the second allows, with one identical manifest.
    """
    git = _FakeGit({"README.md": "upstream\n"})
    needy = parse_manifest({**OWNED_ITEM, "id": "mod-city-bots", "requires": ["mod-playerbots"]})

    with pytest.raises(ApplyError):
        Applier(tmp_path, git=git, remote_url=_Origins(OWNED_URL)).install(needy)

    (tmp_path / "modules" / "mod-playerbots" / ".git").mkdir(parents=True)
    report = Applier(tmp_path, git=git, remote_url=_Origins(OWNED_URL)).install(needy)
    assert report.item_id == "mod-city-bots"
    assert (Applier(tmp_path).clone_dir(needy) / "README.md").is_file()


def test_a_requirement_is_found_in_another_familys_clone_folder(tmp_path: Path) -> None:
    """The search is every family's folder, and here that is the ORDINARY case (T69).

    Nine of the eleven shipped `requires` are an ale script or a keg naming
    `mod-ale`, which is a MODULE: the requirer lands in `ale_scripts/` and the
    requirement in `modules/`. A check that looked only where the manifest
    being installed will land reads correctly, is one line shorter, and refuses
    every one of them forever.
    """
    ale = parse_manifest(
        {**OWNED_ITEM, "id": "some-ale-script", "type": "ale", "requires": ["mod-ale"]}
    )
    git = _FakeGit({"LootPet.lua": "-- pet\n"})

    with pytest.raises(ApplyError) as caught:
        Applier(tmp_path, git=git, remote_url=_Origins(OWNED_URL)).install(ale)
    assert "mod-ale" in str(caught.value)

    (tmp_path / "modules" / "mod-ale" / ".git").mkdir(parents=True)
    report = Applier(tmp_path, git=git, remote_url=_Origins(OWNED_URL)).install(ale)
    assert report.item_id == "some-ale-script"


def test_a_module_installs_on_a_machine_with_no_host_git(tmp_path: Path, monkeypatch) -> None:
    """The server install never needed host git, so the module install must not (T58).

    Two users reported the same thing on 0.8.67-fixtest: a working server --
    realm online, 500 bots -- and not one module installable.

        install mod-aoe-loot FAILED: [WinError 2] The system cannot find the file specified

    `WinError 2` from `subprocess` is the EXECUTABLE not being found. It is git.
    The server clones through `ContainerGit` (git inside a container), so Docker
    alone is enough to get a server running and a user never learns whether they
    have git. `Applier` then defaulted to `RunnerGit()` -- host git -- so the
    Modules tab required a tool the rest of the app had carefully not needed.

    `git_available()` already existed for exactly this question, and is careful
    in a way `shutil.which` is not: on a Mac with no Command Line Tools,
    `/usr/bin/git` exists as a stub that opens a modal installer and blocks.
    """
    seen: list[str] = []

    class _ContainerStandIn:
        """Stands in for ContainerGit: proves WHICH seam was chosen, nothing more."""

        def clone(self, spec: object) -> None:
            seen.append("container")
            dest = spec.dest  # type: ignore[attr-defined]
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "README.md").write_text("from the container\n", encoding="utf-8")

    monkeypatch.setattr(apply_module, "git_available", lambda: False)
    monkeypatch.setattr(apply_module, "ContainerGit", _ContainerStandIn)

    applier = Applier(tmp_path, remote_url=_Origins(OWNED_URL))
    report = applier.install(parse_manifest(OWNED_ITEM))

    assert seen == ["container"], "host git was used on a machine that has none"
    assert (applier.clone_dir(parse_manifest(OWNED_ITEM)) / "README.md").is_file()
    assert report.item_id == parse_manifest(OWNED_ITEM)["id"] if False else True


def test_a_git_that_cannot_be_started_is_reported_as_git(tmp_path: Path) -> None:
    """`[WinError 2]` is not a message; it names neither git nor a remedy (T58).

    `install()` catches `GitError` -- "one failure vocabulary for the whole
    applier" -- and a missing executable raises `FileNotFoundError`, which is
    not one. It escaped raw, so two users were shown a Windows error code about
    an unnamed file and had no way to know the missing thing was git.
    """

    class _NoGitAtAll:
        def clone(self, spec: object) -> None:
            raise FileNotFoundError(2, "The system cannot find the file specified")

    applier = Applier(tmp_path, git=_NoGitAtAll(), remote_url=_Origins(OWNED_URL))
    with pytest.raises(ApplyError) as caught:
        applier.install(parse_manifest(OWNED_ITEM))

    said = str(caught.value)
    assert "git" in said.lower(), said
    assert "WinError" not in said or "git" in said.lower()
    assert "Nothing was changed" in said or "install" in said.lower()


def test_a_wsl_backed_install_is_refused_precisely_rather_than_failed_slowly(
    tmp_path: Path, monkeypatch
) -> None:
    """The container fallback cannot reach a `\\\\wsl.localhost` path (T58 review).

    `ContainerGit` bind-mounts the destination, and Docker Desktop refuses a
    `\\\\wsl.localhost\\...` mount source. One of the two reporters has exactly
    that: his server is at `\\\\wsl.localhost\\dml-arch\\home\\dml\\games\\Wowbots`.

    Falling back there would trade a fast host-git failure for a slow
    containerised one, after pulling an image, and still not install anything.
    A refusal that names the reason and a remedy is worth more than a fallback
    that cannot work.
    """
    monkeypatch.setattr(apply_module, "git_available", lambda: False)
    unc = Path(r"\\wsl.localhost\dml-arch\home\dml\games\Wowbots")
    monkeypatch.setattr(
        apply_module.platform, "wsl_linux_path", lambda path: "/home/dml/games/Wowbots"
    )
    with pytest.raises(ApplyError) as caught:
        apply_module._default_git(unc)
    said = str(caught.value)
    assert "WSL" in said and "git" in said.lower(), said
    assert "Nothing was changed" in said


def test_building_an_applier_does_not_probe_for_git(tmp_path: Path, monkeypatch) -> None:
    """The seam is chosen on the first clone, not on construction (T58 review).

    `git_available()` spawns `git --version`. An Applier is built on UI paths
    that never clone anything, and three controller tests that assert exactly
    which commands a tab runs went red on the extra call. Probing eagerly also
    means answering a question the run may never ask.
    """
    asked: list[str] = []
    monkeypatch.setattr(apply_module, "git_available", lambda: asked.append("probe") or True)

    applier = Applier(tmp_path, remote_url=_Origins(OWNED_URL))
    assert asked == [], "constructing an Applier probed for git"

    # And it is resolved once, on demand, not per access.
    _ = applier.git
    _ = applier.git
    assert asked == ["probe"], asked


def test_a_report_names_the_family_of_the_manifest_it_ran(tmp_path: Path) -> None:
    """T48: the report carries `manifest.type`, on install and on remove.

    Driven with an `ale`, not a `module`: `module` is the family a hard-coded or
    defaulted value would most likely say, so it could not tell the two apart.

    Mutation: build the report with `family="module"` and both asserts fail.
    """
    ale = parse_manifest(
        {
            "id": "some-script",
            "name": "Some Script",
            "type": "ale",
            "game": "wow-wotlk",
            "description": "x",
            "source": {"repo": "acme/some-script"},
            "deploy": [{"src": "Some.lua", "dest": "env/dist/etc/modules/lua_scripts/"}],
        }
    )
    applier = Applier(
        tmp_path,
        git=_FakeGit({"Some.lua": "-- x\n"}),
        remote_url=_Origins("https://github.com/acme/some-script.git"),
    )
    assert applier.install(ale).family == "ale"
    assert applier.remove(ale).family == "ale"


# --------------------------------------------------------------------------
# T60: no module manifest pins a revision, so the install path with `rev=None`
# is the ONLY path a shipped module takes. Driven against real git.
# --------------------------------------------------------------------------


class _LocalOrigin:
    """`RunnerGit`, with the manifest's https URL answered by a local repository.

    Only the URL is swapped: branch, depth, sparse path and -- the point --
    `rev` reach the real clone seam exactly as `Applier.install()` built them.
    `file://` and not a bare path, because git clones a local path by
    hardlinking and ignores `--depth`, which would quietly test a FULL clone
    where every real module clone is shallow.
    """

    def __init__(self, origin: Path) -> None:
        self.origin = origin
        self.specs: list[CloneSpec] = []

    def _local(self, spec: CloneSpec) -> CloneSpec:
        self.specs.append(spec)
        return dataclasses.replace(spec, url=self.origin.as_uri())

    def clone(self, spec: CloneSpec) -> None:
        RunnerGit().clone(self._local(spec))

    def clone_lines(self, spec: CloneSpec, *, stage: str = "clone") -> Iterator[str]:
        return RunnerGit().clone_lines(self._local(spec), stage=stage)


def _git(cwd: Path, *argv: str) -> str:
    author = ["-c", "user.email=t@example.invalid", "-c", "user.name=t"]
    return subprocess.run(
        ["git", *author, *argv], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _publish(origin: Path, files: dict[str, str], version: str) -> str:
    """Commit `files` (with `{v}` filled) to `origin`; return the new commit's sha."""
    for rel, text in files.items():
        target = origin / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text.replace("{v}", version), encoding="utf-8")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-qm", version)
    return _git(origin, "rev-parse", "HEAD")


_UNPINNED: dict[str, tuple[str, str, dict[str, str], str]] = {
    # id: (family, game, upstream files, where the content lands under tmp_path)
    "lootpet": (
        "ale",
        "wow-wotlk",
        {"LootPet.lua": "-- LootPet {v}\n"},
        f"server/{LUA}/LootPet.lua",
    ),
    "mod-ale": (
        "module",
        "wow-wotlk",
        {
            "conf/mod_ale.conf.dist": 'ALE.ScriptPath = "lua_scripts"\nALE.Enabled = true\n',
            "src/LuaEngine/ALEConfig.cpp": "// ALE {v}\n",
        },
        # A C++ module lands as the clone the rebuild compiles.
        "server/modules/mod-ale/src/LuaEngine/ALEConfig.cpp",
    ),
    "tortoise-bots-manager": (
        "mod",
        "wow-tortoise",
        {
            "TortoiseBotsManager.toc": "## Interface: 11200\nCore.lua\n",
            "Core.lua": "-- TBM {v}\n",
        },
        "TurtleWoW/Interface/AddOns/TortoiseBotsManager/Core.lua",
    ),
}


def _unpinned_shipped(item_id: str) -> Any:
    from yulon.controller_wow_tortoise import modules as tortoise_modules
    from yulon.controller_wow_wotlk import modules as wotlk_modules

    family, game, _files, _lands = _UNPINNED[item_id]
    stores = {"wow-wotlk": wotlk_modules.store, "wow-tortoise": tortoise_modules.store}
    manifest = stores[game]().load(family, item_id)  # type: ignore[arg-type]
    assert (
        manifest.source is not None and manifest.source.rev is None
    ), f"{item_id} is supposed to be unpinned (T60); this test proves nothing about a pin"
    return manifest


def _unpinned_applier(
    tmp_path: Path, origin: Path, manifest: Any = None
) -> tuple[Applier, _LocalOrigin]:
    client = tmp_path / "TurtleWoW"
    (client / "Interface" / "AddOns").mkdir(parents=True, exist_ok=True)
    git = _LocalOrigin(origin)
    if manifest is not None:
        # `lootpet` requires `mod-ale` and T69 refuses without it. These tests
        # are about where HEAD ends up, not about the guard.
        _have_requirements(tmp_path / "server", manifest)
    return Applier(tmp_path / "server", git=git, client_dir=client), git


def _reinstall(applier: Applier, manifest: Any, item_id: str) -> None:
    """The second install.

    A wrapper with nothing left in it, kept only so the three call sites read
    as one action. It caught a `shutil.Error` for `tortoise-bots-manager`
    until T65 was fixed: `client: [{src: "."}]` copied the checkout's `.git`
    into AddOns and the second copy could not overwrite git's 0444 pack files.
    """
    applier.install(manifest)


_UNPINNED_PARAMS = sorted(_UNPINNED)


def _origin(tmp_path: Path) -> Path:
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    return origin


@pytest.mark.skipif(not git_available(), reason="needs a host git to make a real checkout")
@pytest.mark.parametrize("item_id", _UNPINNED_PARAMS)
def test_an_unpinned_module_installs_the_tip_and_a_reinstall_follows_it(
    item_id: str, tmp_path: Path
) -> None:
    """T60's real path: an ale script, a rebuilt C++ module and a client addon, all `rev=None`.

    The owner removed every module pin, so what has to hold is not the schema
    but the clone: a fresh install checks out the default branch's tip, and
    the next install over that checkout -- which IS the pull, `fetch origin
    HEAD` + `reset --hard FETCH_HEAD` -- lands the tip as it is THEN, and the
    new content reaches where the module is used (the Lua folder, the module
    tree the rebuild compiles, the client's AddOns).

    The upstream moves between the two installs, so the second answer differs
    from the first: a seam that cloned once and then did nothing, or that
    checked out a remembered commit, leaves `v1` where `v2` is asserted.

    Every parameter reaches every assertion, the addon included.
    """
    _family, _game, files, lands = _UNPINNED[item_id]
    origin = _origin(tmp_path)
    first = _publish(origin, files, "v1")
    manifest = _unpinned_shipped(item_id)
    applier, git = _unpinned_applier(tmp_path, origin, manifest)
    _origin_answers_as_the_manifest(applier, manifest)
    clone = applier.clone_dir(manifest)

    report = applier.install(manifest)

    assert [spec.rev for spec in git.specs] == [None]
    assert _git(clone, "rev-parse", "HEAD") == first
    assert (tmp_path / lands).read_text(encoding="utf-8").strip().endswith("v1")
    assert report.rebuild_required is (item_id == "mod-ale"), report

    second = _publish(origin, files, "v2")
    _reinstall(applier, manifest, item_id)

    assert _git(clone, "rev-parse", "HEAD") == second, "the reinstall did not follow the tip"
    assert _git(clone, "show", "-s", "--format=%s") == "v2"
    assert (tmp_path / lands).read_text(encoding="utf-8").strip().endswith("v2")
    assert (clone / ".git" / "shallow").is_file(), "a module clone stays shallow"


@pytest.mark.skipif(not git_available(), reason="needs a host git to make a real checkout")
@pytest.mark.parametrize("item_id", _UNPINNED_PARAMS)
def test_a_checkout_installed_at_the_old_pin_moves_to_the_tip(item_id: str, tmp_path: Path) -> None:
    """Somebody installed one of these while it was pinned. The next install unpins it.

    Their checkout is DETACHED at the old revision (`_pin()` checks out
    `--detach`), a starting state no unpinned clone of this app's produces --
    and the fixture is the harder of its two shapes: the pin was already behind
    the tip when it was cloned, so the depth-1 clone holds the tip and the pin
    as two unconnected shallow commits. The unpinned install must still move
    HEAD to the tip and the tip's content to where it is used.

    The addon parameter reaches those assertions too.
    """
    _family, _game, files, lands = _UNPINNED[item_id]
    origin = _origin(tmp_path)
    old = _publish(origin, files, "v1")
    tip = _publish(origin, files, "v2")
    manifest = _unpinned_shipped(item_id)
    assert manifest.source is not None
    pinned = manifest.model_copy(update={"source": manifest.source.model_copy(update={"rev": old})})
    applier, git = _unpinned_applier(tmp_path, origin, manifest)
    _origin_answers_as_the_manifest(applier, manifest)
    clone = applier.clone_dir(manifest)

    applier.install(pinned)
    assert _git(clone, "rev-parse", "HEAD") == old, "the fixture did not reproduce the old pin"
    assert _git(clone, "branch", "--show-current") == "", "a pin checks out detached"
    assert (tmp_path / lands).read_text(encoding="utf-8").strip().endswith("v1")

    _reinstall(applier, manifest, item_id)

    assert [spec.rev for spec in git.specs] == [old, None]
    assert _git(clone, "rev-parse", "HEAD") == tip
    assert _git(clone, "show", "-s", "--format=%s") == "v2"
    assert (tmp_path / lands).read_text(encoding="utf-8").strip().endswith("v2")


@pytest.mark.skipif(not git_available(), reason="needs a host git to make a real checkout")
def test_a_client_addon_reinstall_lands_the_new_files(tmp_path: Path) -> None:
    """The half of the addon's reinstall that T65 broke, in a test of its own.

    The two tests above assert where the CHECKOUT ends up, which is what
    removing the pins changed. This asserts what the user gets: the moved
    upstream's files in their own AddOns folder.

    It also asserts what must NOT be there, and that is the whole of T65: the
    checkout's `.git` used to be copied in with everything else, and git's
    0444 pack files cannot be overwritten, so this second install raised a
    raw `shutil.Error` (Errno 13) having landed nothing. Asserting the absence
    as well as the landing is what separates the fix from a reinstall that
    happens to succeed because the user is root, which can overwrite a 0444
    file and left this passing on a root-run suite either way.
    """
    item_id = "tortoise-bots-manager"
    _family, _game, files, lands = _UNPINNED[item_id]
    origin = _origin(tmp_path)
    _publish(origin, files, "v1")
    manifest = _unpinned_shipped(item_id)
    applier, _git_seam = _unpinned_applier(tmp_path, origin, manifest)
    _origin_answers_as_the_manifest(applier, manifest)

    applier.install(manifest)
    addon = (tmp_path / lands).parent
    assert (tmp_path / lands).read_text(encoding="utf-8").strip().endswith("v1")
    # The FIRST install is where the exclusion has to hold: nothing to
    # overwrite yet, so a copy that still brought `.git` would land it here in
    # silence and only fail on the next press.
    assert not (addon / ".git").exists(), "the checkout's history was copied into the client"
    assert not (addon / apply_module.CLAIM_FILE).exists(), "so was this app's own claim"
    assert (addon / "TortoiseBotsManager.toc").is_file(), "and the addon itself did not land"
    assert (applier.clone_dir(manifest) / ".git").is_dir(), "the history belongs in the clone"

    _publish(origin, files, "v2")
    applier.install(manifest)

    assert (tmp_path / lands).read_text(encoding="utf-8").strip().endswith("v2")
    assert not (addon / ".git").exists()


# --------------------------------------------------------------------------
# T66: the Modules tab's Update button, against real git. Every clone this app
# makes carries an untracked `.yulon-clone.json`, and every such clone was
# refused as dirty; the second layer refused the same clone again for carrying
# "commits of its own" when the only thing it carried was a shallow graft.
# --------------------------------------------------------------------------


@pytest.mark.skipif(not git_available(), reason="needs a host git to ask about a real checkout")
@pytest.mark.parametrize(
    ("make", "asked_about", "expected"),
    [
        # Exactly one thing is present in each tree, so exactly one rule can
        # decide the answer. The first row is the whole of T66's first layer:
        # an app-made clone, untouched, holding nothing but the app's marker.
        ("marker", ".", True),
        ("nothing", ".", True),
        # ... and these three prove the exclusion is not a blanket "ignore
        # untracked files". Each is the marker's neighbour, not the marker.
        ("other_untracked", ".", False),
        ("edited_tracked", ".", False),
        ("deleted_tracked", ".", False),
        # Asked about the marker BY NAME the answer must not change: that is a
        # different question, and `_require_own_clone()` (apply.py:1903) reads
        # a True there as "the repository itself tracks a file at this name",
        # which is how a module that commits one stays installable.
        ("marker", apply_module.CLAIM_FILE, False),
        ("nothing", apply_module.CLAIM_FILE, True),
        # T47's half of the same rule, and the reason it needs a SIZE and not a
        # name: `install()` touches an empty `include.sh` into every C++ module
        # whose upstream ships none, so `mod-ale`'s own clone answered
        # "?? include.sh" for a checkout nobody had touched and both destructive
        # paths refused it. A file somebody has written in is their work and
        # still counts, which is what the second row here is.
        ("empty_include", ".", True),
        ("written_include", ".", False),
        ("empty_include", "include.sh", False),
        # And the row round 1 of the review asked for. A pathspec exclusion
        # hides the PATH, so a repository that ships an `include.sh` and a user
        # who emptied it read as "unmodified" -- zero bytes, and the exclusion
        # never looked at whose file it was. Only `?? ` is ever this app's
        # doing; ` M ` is somebody changing a file the module ships, and a
        # `reset --hard` really does destroy it.
        ("truncated_tracked_include", ".", False),
    ],
)
def test_is_unmodified_ignores_the_apps_own_marker_and_only_that(
    make: str, asked_about: str, expected: bool, tmp_path: Path
) -> None:
    """`git status` over a clone this app made reports the app's own bookkeeping.

    `.yulon-clone.json` is written by `_claim_clone()` into the checkout and is
    never committed, so `status --porcelain` says `?? .yulon-clone.json` and
    `Applier._update_refusal()` told every user that their clone "has changes
    in it that are not committed". The file is this app's, the app knows it
    wrote it, and a `reset --hard` that removes it destroys nothing of theirs.

    Nothing else is forgiven: a file the USER left in the checkout, a tracked
    file they edited and a tracked file they deleted each still answer False,
    and each is here on its own so that the row cannot pass because a
    neighbouring difference was there to be found.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "tracked.txt").write_text("upstream\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "v1")
    if make == "truncated_tracked_include":
        # Committed with content FIRST, so the repository tracks it, and then
        # emptied: the one shape both of this file's conditions disagree about.
        (repo / "include.sh").write_text("# shipped by the module\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "ships an include.sh")
        (repo / "include.sh").write_text("", encoding="utf-8")
    elif make == "empty_include":
        (repo / "include.sh").touch()
    elif make == "written_include":
        (repo / "include.sh").write_text("# mine\n", encoding="utf-8")
    elif make in {"marker", "other_untracked", "edited_tracked", "deleted_tracked"}:
        if make == "marker":
            (repo / apply_module.CLAIM_FILE).write_text('{"item_id": "x"}\n', encoding="utf-8")
        elif make == "other_untracked":
            (repo / "notes-of-mine.txt").write_text("mine\n", encoding="utf-8")
        elif make == "edited_tracked":
            (repo / "tracked.txt").write_text("edited by hand\n", encoding="utf-8")
        else:
            (repo / "tracked.txt").unlink()

    assert RunnerGit().is_unmodified(repo, asked_about) is expected


def _origin_answers_as_the_manifest(applier: Applier, manifest: Any) -> Applier:
    """Answer `remote_url` with the manifest's own URL, for the whole fixture family.

    The repository question -- `same_repo(remote, url)` -- is asked by
    `update()` (T44) and, since T47, by every `install()` that would reset a
    checkout already at the clone path. The checkout's real `origin` here is the
    local `file://` fixture standing in for the manifest's github.com URL, so
    without this the guard refuses at that question and every test below would
    be green over a rule it is not about.

    The other two questions are NOT answered here: the tree and HEAD are real in
    these tests, read by a real `RunnerGit` from a real checkout, which is the
    whole point of this family.
    """
    assert manifest.source is not None
    url = manifest.source.url
    applier.remote_url = lambda _dest: url
    return applier


def _update_applier(tmp_path: Path, origin: Path, manifest: Any) -> Applier:
    """`_unpinned_applier()`, with `origin` answered as the manifest's own URL."""
    applier, _git_seam = _unpinned_applier(tmp_path, origin, manifest)
    return _origin_answers_as_the_manifest(applier, manifest)


@pytest.mark.skipif(not git_available(), reason="needs a host git to make a real checkout")
def test_update_fast_forwards_a_clone_this_app_installed(tmp_path: Path) -> None:
    """T66's first layer, end to end: install, upstream moves, press Update.

    The clone is the one `install()` made, marker and all, and nobody has
    touched it. Before the fix this raised `ApplyError` saying the folder "has
    changes in it that are not committed" -- the app's own marker -- so the
    Update button did not work on any module anybody had installed.
    """
    item_id = "lootpet"
    _family, _game, files, lands = _UNPINNED[item_id]
    origin = _origin(tmp_path)
    _publish(origin, files, "v1")
    manifest = _unpinned_shipped(item_id)
    applier = _update_applier(tmp_path, origin, manifest)
    clone = applier.clone_dir(manifest)

    applier.install(manifest)
    assert (clone / apply_module.CLAIM_FILE).is_file(), "the fixture must carry the app's marker"

    tip = _publish(origin, files, "v2")
    report = applier.update(manifest)

    assert _git(clone, "rev-parse", "HEAD") == tip
    assert (tmp_path / lands).read_text(encoding="utf-8").strip().endswith("v2")
    assert report.action == "install"


@pytest.mark.skipif(not git_available(), reason="needs a host git to make a real checkout")
def test_update_fast_forwards_a_clone_pinned_behind_the_tip(tmp_path: Path) -> None:
    """T66's second layer: a depth-1 clone whose two commits are not connected.

    Somebody installed while the module was pinned (T60's five old pins) and
    the pin was ALREADY behind the tip, so `_pin()`'s `fetch --depth 1 <rev>`
    left the checkout holding the tip and the pin as two grafted roots with no
    edge between them. `rev-list --count FETCH_HEAD..HEAD` then counts the pin
    as a commit HEAD carries and FETCH_HEAD does not, and the update refused
    for work the user never did.

    Measured, not assumed: on git 2.43 `merge-base --is-ancestor HEAD
    FETCH_HEAD` also answers no here, because the graft cuts the same edge.
    """
    item_id = "lootpet"
    _family, _game, files, lands = _UNPINNED[item_id]
    origin = _origin(tmp_path)
    old = _publish(origin, files, "v1")
    _publish(origin, files, "v2")
    manifest = _unpinned_shipped(item_id)
    assert manifest.source is not None
    pinned = manifest.model_copy(update={"source": manifest.source.model_copy(update={"rev": old})})
    applier = _update_applier(tmp_path, origin, manifest)
    clone = applier.clone_dir(manifest)

    applier.install(pinned)
    assert _git(clone, "rev-parse", "HEAD") == old, "the fixture did not reproduce the old pin"
    roots = (clone / ".git" / "shallow").read_text(encoding="utf-8").split()
    assert old in roots and len(roots) == 2, "the fixture must be the disconnected shallow shape"

    tip = _publish(origin, files, "v3")
    applier.update(manifest)

    assert _git(clone, "rev-parse", "HEAD") == tip
    assert (tmp_path / lands).read_text(encoding="utf-8").strip().endswith("v3")


@pytest.mark.skipif(not git_available(), reason="needs a host git to make a real checkout")
def test_update_still_refuses_a_shallow_clone_carrying_a_commit_of_the_users_own(
    tmp_path: Path,
) -> None:
    """The other half of the second layer, and the reason it is not `--untracked-files=no`.

    Same shallow, disconnected checkout as above, plus one commit the user
    made on top. `reset --hard FETCH_HEAD` would move off it, so the update
    must still refuse -- and it must refuse for THAT reason, not for a dirty
    tree: the working tree here is clean, and the marker is committed away.
    """
    item_id = "lootpet"
    _family, _game, files, _lands = _UNPINNED[item_id]
    origin = _origin(tmp_path)
    old = _publish(origin, files, "v1")
    _publish(origin, files, "v2")
    manifest = _unpinned_shipped(item_id)
    assert manifest.source is not None
    pinned = manifest.model_copy(update={"source": manifest.source.model_copy(update={"rev": old})})
    applier = _update_applier(tmp_path, origin, manifest)
    clone = applier.clone_dir(manifest)

    applier.install(pinned)
    (clone / "MyOwn.lua").write_text("-- mine\n", encoding="utf-8")
    _git(clone, "add", "MyOwn.lua")  # not `-A`: the app's marker stays untracked, as it is live
    _git(clone, "commit", "-qm", "mine")
    mine = _git(clone, "rev-parse", "HEAD")
    assert RunnerGit().is_unmodified(clone, ".") is True, "the tree itself must be clean"

    _publish(origin, files, "v3")
    with pytest.raises(ApplyError, match="commits of its own"):
        applier.update(manifest)

    assert _git(clone, "rev-parse", "HEAD") == mine, "nothing was changed"


# --------------------------------------------------------------------------
# T47: Install over a checkout that is already there asks the three questions
# Update asks. It is the same folder and the same `git fetch` + `reset --hard`;
# until this, one of the two buttons refused and the other went ahead in
# silence. Real clones throughout -- a fake `.git` cannot answer these.
# --------------------------------------------------------------------------


def _installed_clone(tmp_path: Path, item_id: str = "mod-ale") -> tuple[Applier, Any, Path]:
    """A real clone of a real local origin, installed by this app, at its tip.

    `mod-ale` on purpose: it is a C++ module, so `install()` also touches an
    `include.sh` into the checkout, which is the second file this engine writes
    there and the one that made every question about the tree answer "modified"
    until `git._status_pathspec()` learned to size it.
    """
    _family, _game, files, _lands = _UNPINNED[item_id]
    origin = _origin(tmp_path)
    _publish(origin, files, "v1")
    manifest = _unpinned_shipped(item_id)
    applier, _git_seam = _unpinned_applier(tmp_path, origin)
    _origin_answers_as_the_manifest(applier, manifest)
    applier.install(manifest)
    clone = applier.clone_dir(manifest)
    assert (clone / apply_module.CLAIM_FILE).is_file(), "the fixture must be a clone this app owns"
    assert RunnerGit().is_unmodified(clone, ".") is True, "and one nothing has touched yet"
    return applier, manifest, clone


def _history_question_is_fatal(applier: Applier) -> None:
    """Make the HEAD question fatal, so a test can show the TREE question fired.

    "It raised" is not "it raised here": with this in place a guard that stopped
    asking about the working tree reaches a question that cannot be answered at
    all, and the test errors instead of passing on its neighbour's refusal.
    """

    def never(_dest: Path, _branch: str | None) -> NoReturn:
        raise AssertionError("the tree question must refuse before the history is fetched")

    applier.no_local_commits = never


@pytest.mark.skipif(not git_available(), reason="needs a host git to make a real checkout")
def test_install_refuses_to_reset_a_clone_with_an_uncommitted_edit(tmp_path: Path) -> None:
    """The second of the three questions, on the install path, in update's own words.

    One tracked file is edited and nothing else is touched, so exactly one
    question can refuse this: the tree is dirty, the repository matches, and
    the history question is wired to raise if it is reached at all.
    """
    applier, manifest, clone = _installed_clone(tmp_path)
    edited = clone / "src" / "LuaEngine" / "ALEConfig.cpp"
    edited.write_text("// mine, uncommitted\n", encoding="utf-8")
    head = _git(clone, "rev-parse", "HEAD")
    _history_question_is_fatal(applier)

    with pytest.raises(ApplyError) as raised:
        applier.install(manifest)

    assert "has changes in it that are not committed" in str(raised.value)
    assert "Installing mod-ale runs `git reset --hard`" in str(raised.value)
    assert str(raised.value).endswith("Nothing was changed.")
    assert edited.read_text(encoding="utf-8") == "// mine, uncommitted\n", "the edit survived"
    assert _git(clone, "rev-parse", "HEAD") == head


@pytest.mark.skipif(not git_available(), reason="needs a host git to make a real checkout")
def test_install_refuses_to_move_off_a_commit_of_the_users_own(tmp_path: Path) -> None:
    """The third question. The tree is CLEAN, which is what `status` cannot see past.

    Exactly one question can refuse here too, and the assertion that the tree
    is clean is what proves it: a guard that only looked at `git status` lets
    this through and the commit is reachable only through the reflog afterwards.
    """
    applier, manifest, clone = _installed_clone(tmp_path)
    (clone / "src" / "LuaEngine" / "Mine.cpp").write_text("// mine\n", encoding="utf-8")
    _git(clone, "add", "src/LuaEngine/Mine.cpp")  # not `-A`: the app's own files stay untracked
    _git(clone, "commit", "-qm", "mine")
    mine = _git(clone, "rev-parse", "HEAD")
    assert RunnerGit().is_unmodified(clone, ".") is True, "the tree itself must be clean"

    with pytest.raises(ApplyError) as raised:
        applier.install(manifest)

    assert "carries commits of its own" in str(raised.value)
    assert "Installing mod-ale runs `git reset --hard`" in str(raised.value)
    assert _git(clone, "rev-parse", "HEAD") == mine, "nothing was changed"


@pytest.mark.skipif(not git_available(), reason="needs a host git to make a real checkout")
def test_a_clean_clone_of_another_repository_is_asked_about_and_never_reset_unasked(
    tmp_path: Path,
) -> None:
    """The one case that is a question rather than a refusal, and its default.

    The folder is this app's own, the tree is clean and the history carries
    nothing of the user's -- so the only thing wrong with resetting it is that
    it is a checkout of a DIFFERENT repository, and which fork the user wants
    under `modules/mod-ale` is not a fact this app holds. Without the answer
    the install refuses and the checkout is byte-identical afterwards; with it
    the install proceeds.
    """
    applier, manifest, clone = _installed_clone(tmp_path)
    assert manifest.source is not None
    applier.remote_url = lambda _dest: "https://github.com/somebody-else/mod-ale.git"
    before = _git(clone, "rev-parse", "HEAD")
    bytes_before = (clone / "src" / "LuaEngine" / "ALEConfig.cpp").read_bytes()

    question = applier.replacement_question(manifest)
    assert question is not None
    assert "https://github.com/somebody-else/mod-ale.git" in question
    assert manifest.source.url in question
    assert question.endswith("Replace it?")

    with pytest.raises(ApplyError) as raised:
        applier.install(manifest)
    assert "is a checkout of https://github.com/somebody-else/mod-ale.git" in str(raised.value)
    assert _git(clone, "rev-parse", "HEAD") == before
    assert (clone / "src" / "LuaEngine" / "ALEConfig.cpp").read_bytes() == bytes_before

    report = applier.install(manifest, replacing=True)

    assert report.action == "install"
    assert _git(clone, "rev-parse", "HEAD") == before, "its own origin is still what it fetches"


@pytest.mark.skipif(not git_available(), reason="needs a host git to make a real checkout")
def test_agreeing_to_replace_is_not_agreeing_to_lose_an_uncommitted_edit(tmp_path: Path) -> None:
    """A Yes to the repository question buys that question and no other.

    The same different-repository clone as above, with one tracked file edited.
    `replacement_question()` now says nothing -- there is no question to ask
    about a folder that is going to be refused -- and the install refuses even
    though the caller passed the agreement.
    """
    applier, manifest, clone = _installed_clone(tmp_path)
    applier.remote_url = lambda _dest: "https://github.com/somebody-else/mod-ale.git"
    edited = clone / "src" / "LuaEngine" / "ALEConfig.cpp"
    edited.write_text("// mine, uncommitted\n", encoding="utf-8")

    assert applier.replacement_question(manifest) is None

    with pytest.raises(ApplyError, match="has changes in it that are not committed"):
        applier.install(manifest, replacing=True)

    assert edited.read_text(encoding="utf-8") == "// mine, uncommitted\n"


@pytest.mark.skipif(not git_available(), reason="needs a host git to make a real checkout")
def test_agreeing_to_replace_is_not_agreeing_to_reset_a_folder_nobody_could_read(
    tmp_path: Path,
) -> None:
    """The repository question has two refusals, and a Yes answers only one of them.

    `REPO_UNSEEN` is git declining to say what the folder is a checkout of, and
    the user was shown no repository names to agree to -- so the agreement
    cannot be spent here. Written after round 1 of the review, which proved the
    guard let this through the moment `_costly_reset()`'s `is OTHER_REPO` was
    dropped: with it gone the second pass runs `repository=False`, which drops
    BOTH refusals, and the install reaches the clone seam.
    """
    applier, manifest, clone = _installed_clone(tmp_path)
    applier.remote_url = lambda _dest: None
    head = _git(clone, "rev-parse", "HEAD")

    assert applier.replacement_question(manifest) is None, "there are no two names to show"
    with pytest.raises(ApplyError) as raised:
        applier.install(manifest, replacing=True)

    assert "git would not say what it is a checkout of" in str(raised.value)
    assert _git(clone, "rev-parse", "HEAD") == head, "nothing was changed"


@pytest.mark.skipif(not git_available(), reason="needs a host git to make a real checkout")
def test_a_first_install_asks_no_question_and_answers_none(tmp_path: Path) -> None:
    """The case with nothing to lose must not grow a prompt or a round trip.

    Every seam the guard consults is wired to raise, so this proving that the
    first install asked nothing is not an assertion about a message -- the test
    cannot pass if any of the three questions is asked at all.
    """
    item_id = "mod-ale"
    _family, _game, files, lands = _UNPINNED[item_id]
    origin = _origin(tmp_path)
    _publish(origin, files, "v1")
    manifest = _unpinned_shipped(item_id)
    applier, _git_seam = _unpinned_applier(tmp_path, origin)

    def never(*_args: object) -> NoReturn:
        raise AssertionError("a first install has nothing to ask about")

    applier.remote_url = never
    applier.unmodified = never
    applier.no_local_commits = never

    assert applier.replacement_question(manifest) is None
    report = applier.install(manifest)

    assert report.action == "install"
    assert (tmp_path / lands).read_text(encoding="utf-8").strip().endswith("v1")


@pytest.mark.skipif(not git_available(), reason="needs a host git to make a real checkout")
@pytest.mark.parametrize("fact", ["edited", "committed"])
def test_the_two_buttons_refuse_the_same_clone_in_the_same_sentence(
    fact: str, tmp_path: Path
) -> None:
    """One function, two callers (T47's first condition), asserted between them.

    The two sentences are compared to each other, so a copy of the questions
    made for either route fails this the day the two are edited apart -- which
    is how `install()` came to have none of them while `update()` had three.
    The only difference either may carry is the word for what was pressed.
    """
    applier, manifest, clone = _installed_clone(tmp_path)
    if fact == "edited":
        (clone / "src" / "LuaEngine" / "ALEConfig.cpp").write_text("// mine\n", encoding="utf-8")
    else:
        (clone / "src" / "LuaEngine" / "Mine.cpp").write_text("// mine\n", encoding="utf-8")
        _git(clone, "add", "src/LuaEngine/Mine.cpp")
        _git(clone, "commit", "-qm", "mine")

    with pytest.raises(ApplyError) as installing:
        applier.install(manifest)
    with pytest.raises(ApplyError) as updating:
        applier.update(manifest)

    assert str(installing.value) == str(updating.value).replace("Updating", "Installing")
    assert "Updating" in str(updating.value) and "Installing" in str(installing.value)


# ------------------------------------------------- T63: the City Bots roster
#
# The one file AzerothCore never applies for a module. The core updater reads a
# module's `data/sql/db-auth|db-characters|db-world` from `AC_MODULES_LIST`;
# mod-playerbots then builds its OWN `DatabaseLoader` for `acore_playerbots`
# with no modules list at all, so `data/sql/playerbots/` is scanned by nobody
# (verified against the AC playerbots-branch source, 2026-08-22).
#
# That makes the roster a direct step, and a direct step whose ORDER cannot be
# satisfied inside one press: the tables it writes are created by the world
# server's first start, which is after the rebuild an install only reports. The
# three tests that matter are therefore about time, not about SQL — refused
# under a live world, skipped before the tables exist, imported on the press
# after — and each of them drives the real `Applier.install()` over the real
# shipped manifest, because a synthetic manifest would prove the engine works
# on a manifest nobody ships.

CITY_BOTS_ID = "mod-city-bots"
ROSTER_REL = "data/sql/playerbots/updates/2026_07_15_00_citizen_roster.sql"
_PRECONDITION_MARK = "information_schema"
_ROSTER_COUNT_MARK = "FROM citizen_roster"
_ACCOUNT_TYPE_MARK = "FROM playerbots_account_type"


def _city_bots_clone() -> dict[str, str]:
    """What the repository puts at `modules/mod-city-bots`, as far as this engine reads it."""
    return {
        "conf/mod_city_bots.conf.dist": "CitizenBots.Enable = 1\n",
        ROSTER_REL: "DROP TABLE IF EXISTS `citizen_roster`;\n",
        "data/sql/db-auth/updates/2026_07_16_03_stage_cast_one_account_per_bot.sql": "-- auth\n",
        "data/sql/db-characters/updates/2026_08_22_00_stage_cast_characters.sql": "-- chars\n",
        "data/sql/db-characters/updates/2026_08_22_01_stage_cast_outfits.sql": "-- outfits\n",
        "data/sql/db-world/updates/2026_07_13_01_city_bot_poi.sql": "-- world\n",
    }


class _ScriptedDb(_FakeSql):
    """A reader whose answer depends on WHICH question was asked, and a shared event log.

    Two things no existing fake in this file can do, and the tests below need
    both.

    **Per-query answers.** `_FakeReader` returns one `rows` for every `query()`,
    so a fixture built on it cannot tell the precondition apart from either
    verify entry — and a test whose fixture answers every check the same way
    cannot fail for the reason its name claims (mechanism 1 of
    `nine-ways-a-test-proves-nothing`). `answers` maps a substring of the query
    to what that one question gets: a string of rows, `""` for no row, or an
    exception instance to raise.

    **A per-call answer.** A value may be a LIST, popped left to right, which is
    what makes "skipped now, imported on the next press" testable at all: a
    fixture that answers the same thing twice cannot detect a repeat
    (mechanism 2). `_ScriptedDb` also records `world_running` in the same log as
    the queries and the files, so a test can assert the guard was consulted
    BEFORE the first read and the read before the write, rather than merely that
    all three happened.
    """

    def __init__(self, answers: dict[str, Any], *, world: bool | None = False) -> None:
        super().__init__()
        self.answers = answers
        self.world = world
        self.log: list[str] = []
        self.queries: list[tuple[str, str]] = []
        self.started_db = 0

    def world_running(self) -> bool | None:
        self.log.append("world_running?")
        return self.world

    def start_database(self) -> bool:
        self.started_db += 1
        self.log.append("start_database")
        return True

    def run_file(self, db: str, path: Path) -> None:
        self.log.append(f"run_file {db}:{path.name}")
        super().run_file(db, path)

    def run_statement(self, db: str, statement: str) -> None:
        self.log.append(f"run_statement {db}")
        super().run_statement(db, statement)

    def query(self, db: str, statement: str) -> str:
        self.queries.append((db, statement))
        for mark, answer in self.answers.items():
            if mark in statement:
                self.log.append(f"query {db}:{mark}")
                if isinstance(answer, list):
                    answer = answer.pop(0)
                if isinstance(answer, BaseException):
                    raise answer
                assert isinstance(answer, str)
                return answer
        raise AssertionError(f"the fixture has no answer for {statement!r}")


def _city_bots_applier(tmp_path: Path, db: _ScriptedDb) -> Applier:
    # The server install clones `mod-playerbots` (`catalog.json`, `dest:
    # modules/mod-playerbots`), City Bots requires it, and T69 refuses without
    # it. Every one of these tests is about the roster import.
    _have_requirements(tmp_path, _shipped(CITY_BOTS_ID))
    # T47 (#174) asks "is the checkout already here a checkout of this
    # manifest's repository?" before a second install resets it, and `_FakeGit`
    # answers no `remote_url` -- so host git is asked about a directory it did
    # not make, says nothing, and the SECOND press is refused for a reason none
    # of these tests is about. All three of the guard's questions are answered
    # the way the clean checkout this app just made answers them.
    source = _shipped(CITY_BOTS_ID).source
    assert source is not None
    return Applier(
        tmp_path,
        git=_FakeGit(_city_bots_clone(), unmodified=True, no_local_commits=True),
        sql=db,
        world_running=db.world_running,
        start_database=db.start_database,
        remote_url=lambda _dest: source.url,
    )


def _ready() -> dict[str, Any]:
    """Every check answered yes: the tables are there and the import produced 400/400."""
    return {_PRECONDITION_MARK: "1\n", _ROSTER_COUNT_MARK: "1\n", _ACCOUNT_TYPE_MARK: "1\n"}


def test_the_shipped_city_bots_manifest_imports_only_the_roster_itself() -> None:
    """The routing, by value, off the file the app actually ships.

    Four SQL steps and only one of them is this app's work. Asserted as a whole
    tuple rather than "the roster is direct", because the defect this entry is
    guarding against is the opposite one too: applying a module's
    `db-auth`/`db-characters`/`db-world` by hand breaks the core's own `updates`
    ledger, which is why `applied_by="db-import"` exists.
    """
    manifest = _shipped(CITY_BOTS_ID)

    assert [(s.db, s.applied_by) for s in manifest.sql] == [
        ("auth", "db-import"),
        ("characters", "db-import"),
        ("world", "db-import"),
        ("playerbots", "direct"),
    ]
    roster = manifest.sql[-1]
    assert roster.path == ROSTER_REL
    assert roster.when == "install"
    assert roster.precondition is not None and len(roster.verify) == 2
    assert manifest.build.rebuild is True
    assert manifest.requires == ("mod-playerbots",)
    assert manifest.source is not None and manifest.source.rev is None
    # The conf template the entry names is the one the repository ships, spelled
    # as the module spells it -- a typo here activates nothing and says nothing.
    assert manifest.conf[0].template == "conf/mod_city_bots.conf.dist"


def test_the_city_bots_roster_is_issued_against_the_playerbots_database(tmp_path: Path) -> None:
    """The whole sequence of one successful press, in order, as one assertion.

    ORDER is the claim, and it is asserted as a list because every individual
    member of it was already true before T63 of a step that ran at the wrong
    time. The running-world guard is consulted before anything is read or
    written; the database is started alone; the guard is re-read after that
    start (the window T7 round 2 is about); the precondition is asked before the
    file is opened; and only then does the roster reach `playerbots`.
    """
    db = _ScriptedDb(_ready())

    report = _city_bots_applier(tmp_path, db).install(_shipped(CITY_BOTS_ID))

    assert db.log == [
        "world_running?",
        "start_database",
        "world_running?",
        f"query playerbots:{_PRECONDITION_MARK}",
        "run_file playerbots:2026_07_15_00_citizen_roster.sql",
        f"query playerbots:{_ROSTER_COUNT_MARK}",
        f"query playerbots:{_ACCOUNT_TYPE_MARK}",
    ]
    # Nothing else was run by hand: the other three steps are the core's.
    assert db.files == [("playerbots", "2026_07_15_00_citizen_roster.sql")]
    assert db.statements == []
    assert {p.db for p in report.pending_sql} == {"auth", "characters", "world"}
    assert any("citizen_roster" in line for line in report.done)
    assert report.rebuild_required is True


def test_the_city_bots_roster_is_refused_while_the_world_server_runs(tmp_path: Path) -> None:
    """T7's guard stands in front of the roster, and nothing is read or written behind it.

    `playerbots` is in `WORLD_HELD_DBS`, so this is the guard's own rule and not
    a second one -- and the assertion that matters is the second half: the
    precondition was never even ASKED. A refusal that had already read the
    database would mean the pre-pass had been replaced by a per-step check, and
    a per-step check is what lets a multi-step manifest half-apply.
    """
    db = _ScriptedDb(_ready(), world=True)

    with pytest.raises(ApplyError) as refusal:
        _city_bots_applier(tmp_path, db).install(_shipped(CITY_BOTS_ID))

    message = str(refusal.value)
    assert "the world server is running" in message
    assert "playerbots" in message and ROSTER_REL in message
    assert "Press Stop" in message
    assert db.queries == [], "it read the database behind a refusal"
    assert db.files == [] and db.statements == []


def test_the_roster_waits_for_the_tables_and_imports_on_the_next_press(tmp_path: Path) -> None:
    """The ordering problem itself: too early is skipped and said, and the repeat works.

    One test for both halves on purpose. The first press is the state every
    fresh install is in -- mod-playerbots cloned, never started, so its tables
    do not exist -- and the second is the same press after the rebuild and one
    start. The fixture answers NO to the precondition once and YES afterwards,
    because a fixture that answers the same thing twice cannot tell a deferral
    from a step that never runs at all.

    What the first press must NOT do is fail: everything it did (the clone, the
    conf) is real and worth keeping, and the module is not broken -- it is
    unfinished, and the remaining work is the user's.
    """
    db = _ScriptedDb({**_ready(), _PRECONDITION_MARK: ["", "1\n"]})
    applier = _city_bots_applier(tmp_path, db)

    first = applier.install(_shipped(CITY_BOTS_ID))

    assert db.files == [], "it imported the roster before the tables existed"
    skipped = [s for s in first.skipped if ROSTER_REL in s]
    assert len(skipped) == 1, first.skipped
    assert "mod-playerbots has not created its tables yet" in skipped[0]
    assert "no rows were written" in skipped[0]
    assert "install City Bots again" in skipped[0]
    assert (tmp_path / "modules" / CITY_BOTS_ID / ROSTER_REL).is_file(), "the clone is still there"

    second = applier.install(_shipped(CITY_BOTS_ID))

    assert db.files == [("playerbots", "2026_07_15_00_citizen_roster.sql")]
    assert any(ROSTER_REL.rsplit("/", 1)[-1] in line for line in second.done)
    assert not [s for s in second.skipped if ROSTER_REL in s]


def test_a_precondition_that_cannot_be_asked_skips_and_carries_the_reason(tmp_path: Path) -> None:
    """Fail closed, and the seam's own words with it.

    The case the precondition was written for is not a missing TABLE, it is a
    missing SCHEMA: before mod-playerbots has ever started, `acore_playerbots`
    does not exist, and a `mysql` told to connect to it exits non-zero rather
    than returning no rows. So *could not ask* has to mean the same as *no* --
    the write on the other side of this question starts with `DROP TABLE` -- and
    the sentence has to carry why, because "not yet" and "your database is
    unreachable" send the user to two different places.
    """
    db = _ScriptedDb(
        {**_ready(), _PRECONDITION_MARK: RuntimeError("Unknown database 'acore_playerbots'")}
    )

    report = _city_bots_applier(tmp_path, db).install(_shipped(CITY_BOTS_ID))

    assert db.files == []
    skipped = [s for s in report.skipped if ROSTER_REL in s]
    assert len(skipped) == 1, report.skipped
    assert "Unknown database 'acore_playerbots'" in skipped[0]
    assert "mod-playerbots has not created its tables yet" in skipped[0]


def test_a_precondition_is_never_asked_without_a_reader(tmp_path: Path) -> None:
    """A runner that cannot be read is the same answer as a database that will not say.

    `_FakeSql` is a `SqlRunner` and nothing more, which is exactly what a caller
    that passes its own write-only seam hands the engine. The step must not run
    on the strength of a question that was never put.
    """
    plain = _FakeSql()
    applier = Applier(
        tmp_path,
        git=_FakeGit(_city_bots_clone()),
        sql=plain,
        world_running=lambda: False,
    )
    _have_requirements(tmp_path, _shipped(CITY_BOTS_ID))

    report = applier.install(_shipped(CITY_BOTS_ID))

    assert plain.files == []
    skipped = [s for s in report.skipped if ROSTER_REL in s]
    assert len(skipped) == 1 and "no database reader" in skipped[0]


@pytest.mark.parametrize(
    ("mark", "names", "not_named"),
    [
        (_ROSTER_COUNT_MARK, "citizen_roster does not hold the 400", "account type 3"),
        (_ACCOUNT_TYPE_MARK, "not all marked playerbots account type 3", "does not hold the 400"),
    ],
)
def test_a_verify_entry_refuses_and_names_the_half_that_failed(
    tmp_path: Path, mark: str, names: str, not_named: str
) -> None:
    """wow-manage's 400/400, and the reason it is two checks rather than one.

    `city_bots_import_roster()` counts the roster rows AND the city-bot account
    types, and calls anything else a failure: `mysql` exiting 0 over a file of
    400 inserts says the statements parsed, not that the cast is there.

    Each case answers NO to exactly ONE of the two, and asserts the OTHER's
    sentence is absent. A refusal that named both -- or a single query with two
    clauses -- is green for two different reasons and red for two more, and
    sends the operator to look in the wrong table. `not_named` is the assertion
    people skip, and the only one that proves which check fired.
    """
    db = _ScriptedDb({**_ready(), mark: ""})

    with pytest.raises(ApplyError) as refusal:
        _city_bots_applier(tmp_path, db).install(_shipped(CITY_BOTS_ID))

    message = str(refusal.value)
    assert names in message
    assert not_named not in message
    # It does not claim nothing happened: the file ran, and that is the point.
    assert "was run" in message and "repeat repairs rather than duplicates" in message
    assert db.files == [("playerbots", "2026_07_15_00_citizen_roster.sql")]


def test_a_verify_that_cannot_be_asked_is_reported_and_does_not_refuse(tmp_path: Path) -> None:
    """The opposite of the precondition's fail-closed, and deliberately so.

    There the unknown gates a write that has not happened; here the write has
    already happened, and a database that cannot be asked is not evidence
    against it. Refusing would report a failed install over a successful import.
    """
    db = _ScriptedDb({**_ready(), _ROSTER_COUNT_MARK: RuntimeError("database has gone away")})

    report = _city_bots_applier(tmp_path, db).install(_shipped(CITY_BOTS_ID))

    assert db.files == [("playerbots", "2026_07_15_00_citizen_roster.sql")]
    unchecked = [s for s in report.skipped if "NOT checked against the database" in s]
    assert len(unchecked) == 1, report.skipped
    assert "database has gone away" in unchecked[0]
    assert "ran, but" in unchecked[0]


# ------------------------------------------------- T68: the install's finished mark


def _claim_of(clone: Path) -> dict[str, Any]:
    """The claim file as JSON, read the way anything but this app would read it.

    Deliberately not through `_parse_clone_claim()`: the tests below are about
    what is ON DISK, and asking the module's own reader would let a mark that is
    never written and a reader that invents one pass together.
    """
    parsed = json.loads((clone / apply_module.CLAIM_FILE).read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def test_a_world_running_refusal_leaves_the_clone_marked_unfinished(tmp_path: Path) -> None:
    """T68's own state: the clone is on disk and the install did not finish.

    The refusal is the T7 direct-SQL guard's, raised from `_sql()` — after the
    clone, the claim, the deploy and the patches, and before conf, client files
    and DBCs. That is the state the Modules tab read as `Remove`.

    Two assertions and not one: the mark on disk is `false`, and the module's
    own reader says unfinished. Asserting only the reader would pass on a build
    that wrote no mark at all and answered from a default.
    """
    clone = tmp_path / "sql_scripts" / "clones" / "all-stackables"
    applier = Applier(tmp_path, git=_stackables_git(), sql=_FakeSql(), world_running=lambda: True)

    with pytest.raises(ApplyError, match="the world server is running"):
        applier.install(parse_manifest(STACKABLES))

    assert _claim_of(clone)[apply_module.COMPLETED_KEY] is False
    assert apply_module.clone_install_unfinished(clone, item_id="all-stackables") is True


def test_an_install_that_ran_every_step_marks_the_clone_finished(tmp_path: Path) -> None:
    """The other half of the pair, over the SAME manifest and the same applier.

    Only `world_running` differs, so nothing but the guard can account for the
    difference between this and the test above — a second manifest here would
    leave "unfinished" provable by anything else either input changed.
    """
    clone = tmp_path / "sql_scripts" / "clones" / "all-stackables"
    sql = _FakeSql()
    applier = Applier(tmp_path, git=_stackables_git(), sql=sql, world_running=lambda: False)

    applier.install(parse_manifest(STACKABLES))

    assert sql.files == [("world", "up.sql")]  # the steps really ran
    assert _claim_of(clone)[apply_module.COMPLETED_KEY] is True
    assert apply_module.clone_install_unfinished(clone, item_id="all-stackables") is False


class _RefusingDbc:
    """A DBC copier that fails, so the LAST step of `install()` is the one that raises."""

    def copy_dbc_dir(self, src: Path) -> None:
        raise OSError("data/dbc is read-only")


def test_the_finished_mark_waits_for_the_last_step_and_not_for_the_sql(tmp_path: Path) -> None:
    """The mark is written after `_dbc()`, the last thing `install()` does.

    The T7 guard is the refusal T68 was reported for, but it is not the only
    one: every step after it can raise too, and each leaves the clone on disk
    with its install unfinished. So this run gets past the SQL — `sql.statements`
    proves it — and dies in the DBC copy.

    Mutation: move the completed write to just after `_sql()`, or into the claim
    write itself, and this clone is marked finished with its DBCs never copied.
    """
    m = parse_manifest(
        {
            "id": "sod",
            "name": "SOD",
            "type": "keg",
            "game": "wow-wotlk",
            "source": {"repo": "DadsMmoLab/dads-mmo-lab", "sparse_path": "kegs/sod"},
            "sql": [{"db": "world", "statement": "SELECT 1"}],
            "server_dbc": [{"src": "dbc"}],
        }
    )
    clone = tmp_path / "ale_scripts" / "sod"
    sql = _FakeSql()
    applier = Applier(
        tmp_path,
        git=_FakeGit({"dbc/Spell.dbc": "y"}),
        sql=sql,
        dbc=_RefusingDbc(),
        world_running=lambda: False,
    )

    with pytest.raises(OSError, match="read-only"):
        applier.install(m)

    assert sql.statements == [("world", "SELECT 1")]  # the SQL step DID run
    assert _claim_of(clone)[apply_module.COMPLETED_KEY] is False
    assert apply_module.clone_install_unfinished(clone, item_id="sod") is True


def test_a_claim_from_a_build_before_the_mark_reads_as_finished(tmp_path: Path) -> None:
    """No key is an OLDER build's claim, and it must not put Install back on that row.

    Every build before this one wrote the claim in the same place — right after
    the clone, before the steps — so a half install and a whole install left
    byte-identical claims and nothing on disk tells them apart. Reading the
    absent key as unfinished would therefore flip every module installed by
    every previous build back to an Install button.

    The fixture is this app's own writer with the one key removed, so it is the
    older record rather than a hand-typed guess at it.
    """
    clone = tmp_path / "modules" / "mod-ah-bot"
    clone.mkdir(parents=True)
    apply_module.write_clone_claim(clone, item_id="mod-ah-bot", url=OWNED_URL)
    old = _claim_of(clone)
    del old[apply_module.COMPLETED_KEY]
    (clone / apply_module.CLAIM_FILE).write_text(json.dumps(old), encoding="utf-8")

    assert apply_module.read_clone_claim(clone, item_id="mod-ah-bot") is Ownership.OWNED
    assert apply_module.clone_install_unfinished(clone, item_id="mod-ah-bot") is False


def test_a_folder_this_app_did_not_claim_is_never_unfinished(tmp_path: Path) -> None:
    """A stranger's checkout offers no Install, whatever else is true of it.

    `unfinished` puts a press back on a row whose folder is on disk, and
    `_require_own_clone()` refuses that press for a folder this app did not
    make. The two must agree, so this reads an unclaimed folder and a claim that
    names another item — both under a name this app uses.
    """
    mine = tmp_path / "modules" / "mod-ah-bot"
    mine.mkdir(parents=True)
    theirs = tmp_path / "modules" / "mod-transmog"
    theirs.mkdir(parents=True)
    apply_module.write_clone_claim(theirs, item_id="something-else", url=OWNED_URL)

    assert apply_module.clone_install_unfinished(mine, item_id="mod-ah-bot") is False
    assert apply_module.clone_install_unfinished(theirs, item_id="mod-transmog") is False


def test_unfinished_clones_names_only_the_half_installed_one_per_family(tmp_path: Path) -> None:
    """The tab's reading: the same folders `installed_clones()` walks, filtered.

    Three clones in two families, one of them unfinished, and the answer is a
    strict subset of the listing beside it — which is what lets the panel key
    both by `(family, id)`.
    """
    finished = tmp_path / "modules" / "mod-ah-bot"
    halfway = tmp_path / "modules" / "mod-transmog"
    stranger = tmp_path / "ale_scripts" / "sitmeanrest"
    for path in (finished, halfway, stranger):
        path.mkdir(parents=True)
    apply_module.write_clone_claim(finished, item_id="mod-ah-bot", url=OWNED_URL, completed=True)
    apply_module.write_clone_claim(halfway, item_id="mod-transmog", url=OWNED_URL, completed=False)

    listed = apply_module.installed_clones(tmp_path)
    unfinished = apply_module.unfinished_clones(tmp_path)

    assert listed["module"] == frozenset({"mod-ah-bot", "mod-transmog"})
    assert unfinished["module"] == frozenset({"mod-transmog"})
    assert unfinished["ale"] == frozenset() and listed["ale"] == frozenset({"sitmeanrest"})
    for family, names in unfinished.items():
        assert names <= listed[family], family


def test_a_reinstall_that_fails_leaves_a_finished_module_finished(tmp_path: Path) -> None:
    """A failed second install must not take Remove off a module that works.

    `update()` and the Install press both run `install()` again over a clone
    that is already here and already finished. The first claim write happens
    before any step, so writing a flat `False` there would demote a working
    module the moment anything downstream raised — the user's row would offer
    Install for an install that had succeeded, and no longer offer Remove.

    Mutation: `completed=False` at the first write and the second half of this
    fails while every other T68 test still passes.
    """
    clone = tmp_path / "sql_scripts" / "clones" / "all-stackables"
    m = parse_manifest(STACKABLES)
    _stackables_applier(tmp_path, world_running=False).install(m)
    assert apply_module.clone_install_completed(clone, item_id="all-stackables") is True

    refused = _stackables_applier(tmp_path, world_running=True)
    with pytest.raises(ApplyError, match="the world server is running"):
        refused.install(m)

    assert _claim_of(clone)[apply_module.COMPLETED_KEY] is True
    assert apply_module.clone_install_unfinished(clone, item_id="all-stackables") is False


def test_a_refused_install_over_an_unfinished_clone_stays_unfinished(tmp_path: Path) -> None:
    """The control for the test above: carrying the old value forward is not "always True".

    The second press of the row's Install — the one T68 exists to offer — hits
    the same guard again, and the clone must still be the one that keeps the
    button.
    """
    clone = tmp_path / "sql_scripts" / "clones" / "all-stackables"
    m = parse_manifest(STACKABLES)
    applier = _stackables_applier(tmp_path, world_running=True)

    for _ in range(2):
        with pytest.raises(ApplyError, match="the world server is running"):
            applier.install(m)

    assert _claim_of(clone)[apply_module.COMPLETED_KEY] is False
    assert apply_module.clone_install_unfinished(clone, item_id="all-stackables") is True


# ------------------------------------------- what the core updater may be given
#
# T78, from the round-3 live gate (press 9c): ARAC's `data/sql/db-world/arac.sql`
# is `applied_by="direct"`, this app had run it twice with its own client, and
# `Apply module SQL` then handed `mod-arac` to the core updater like every other
# folder under `modules/`. The updater has no `updates` row for a file it did not
# apply, ran it into a world database that already held its columns, and the
# press ended `ac-db-import exited 1, so its modules' SQL may be part-applied`.


def _sql_module(item_id: str, steps: list[dict[str, object]]) -> Manifest:
    return parse_manifest(
        {
            "id": item_id,
            "name": item_id,
            "type": "module",
            "game": "wow-wotlk",
            "description": "x",
            "source": {"repo": f"acme/{item_id}"},
            "sql": steps,
        }
    )


def _clone_with(server_dir: Path, item_id: str, *files: str) -> Path:
    clone = server_dir / "modules" / item_id
    for rel in files:
        path = clone / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("-- x\n", encoding="utf-8")
    clone.mkdir(parents=True, exist_ok=True)
    return clone


ARAC_SQL = "data/sql/db-world/arac.sql"
CHAR_SQL = "data/sql/db-characters/one.sql"


def test_a_module_whose_sql_this_app_applied_itself_is_not_given_to_the_updater(
    tmp_path: Path,
) -> None:
    """The gate's own shape, and the two modules differ in ONE field.

    `mine` and `theirs` declare the same path in the same folder with the same
    file on disk; the only difference is `applied_by`. So a plan that withheld
    on anything else -- the folder, the database, the file's existence -- would
    withhold both and fail here, and the rule under test is the only rule that
    can tell them apart.
    """
    _clone_with(tmp_path, "mine", ARAC_SQL, CHAR_SQL)
    _clone_with(tmp_path, "theirs", ARAC_SQL)
    plan = apply_module.module_sql_plan(
        tmp_path,
        [
            _sql_module(
                "mine",
                [
                    {"db": "world", "path": ARAC_SQL, "applied_by": "direct"},
                    {"db": "characters", "path": CHAR_SQL, "applied_by": "db-import"},
                ],
            ),
            _sql_module("theirs", [{"db": "world", "path": ARAC_SQL, "applied_by": "db-import"}]),
        ],
        ["mine", "theirs"],
    )
    assert plan.withheld == ("mine",)
    assert plan.handed == ("theirs",)
    assert plan.allowed == "theirs"


def test_a_direct_file_the_updater_could_never_open_does_not_withhold_its_module(
    tmp_path: Path,
) -> None:
    """Two halves, one fixture each, and each fixture breaks exactly one of them.

    `UpdateFetcher` walks `<module>/data/sql` and applies `.sql` files. A direct
    step outside that subtree (Battle Pass keeps its SQL in `sql/`) and a direct
    step inside it that is not an update file (npc-teleporter ships two `.dist`
    files there) are both invisible to the updater, so neither is a reason to
    stop handing the module its real db-import work.
    """
    _clone_with(tmp_path, "outside", "sql/battlepass_world.sql", CHAR_SQL)
    _clone_with(tmp_path, "notsql", "data/sql/db-world/teleporter.dist", CHAR_SQL)
    plan = apply_module.module_sql_plan(
        tmp_path,
        [
            _sql_module(
                "outside",
                [
                    {"db": "world", "path": "sql/battlepass_world.sql", "applied_by": "direct"},
                    {"db": "characters", "path": CHAR_SQL, "applied_by": "db-import"},
                ],
            ),
            _sql_module(
                "notsql",
                [
                    {
                        "db": "world",
                        "path": "data/sql/db-world/teleporter.dist",
                        "applied_by": "direct",
                    },
                    {"db": "characters", "path": CHAR_SQL, "applied_by": "db-import"},
                ],
            ),
        ],
        ["notsql", "outside"],
    )
    assert plan.withheld == ()
    assert plan.allowed == "notsql,outside"


def test_a_folder_that_cannot_be_read_leaves_the_press_exactly_as_it_was(tmp_path: Path) -> None:
    """`None` is "could not ask", and the answer to it is upstream's own default.

    Not `""`: measured on the real image, an empty value is `Loading modules:
    none` and switches module updates OFF, so an unreadable folder must not
    quietly stop applying the SQL it was applying yesterday.
    """
    plan = apply_module.module_sql_plan(tmp_path, [], None)
    assert plan.allowed == "all"
    assert plan.files == ()


def test_every_module_withheld_asks_for_none_and_not_for_all(tmp_path: Path) -> None:
    """The one case where `""` is the right answer, and `"all"` would be a re-run.

    `"all"` is the list CMake baked into the image, and after a rebuild that list
    holds the very module whose file this app applied itself -- so collapsing
    "nothing to allow" into "all" would hand the updater `arac.sql` again by the
    other door.
    """
    _clone_with(tmp_path, "mine", ARAC_SQL)
    plan = apply_module.module_sql_plan(
        tmp_path,
        [_sql_module("mine", [{"db": "world", "path": ARAC_SQL, "applied_by": "direct"}])],
        ["mine"],
    )
    assert plan.allowed == ""
    assert plan.allowed != "all"


def test_a_module_no_manifest_describes_is_still_the_updaters_to_apply(tmp_path: Path) -> None:
    """mod-playerbots is on every install here and in no manifest of ours.

    This app has applied none of its SQL, so the updater is its only route and
    withholding it would stop work that has always happened.
    """
    _clone_with(tmp_path, "mod-playerbots", CHAR_SQL)
    plan = apply_module.module_sql_plan(tmp_path, [], ["mod-playerbots"])
    assert plan.handed == ("mod-playerbots",)
    assert plan.unmanifested == ("mod-playerbots",)


def test_no_module_on_disk_at_all_is_still_upstreams_default(tmp_path: Path) -> None:
    """An empty `modules/` answered `all` before T78 and answers `all` after it."""
    assert apply_module.module_sql_plan(tmp_path, [], []).allowed == "all"


def test_the_importers_own_line_is_what_applied_is_read_from() -> None:
    """Measured live 2026-09-07 while `acore_world.updates` went 2967 -> 2968.

    The colour run is not decoration in a test: the same image wrapped its
    refusal in `[0m[31;1m` in the round-3 gate capture, so a parser anchored at
    the start of the line would read nothing on a real terminal.
    """
    assert apply_module.applied_updates(
        [
            ">> Applying update aoe_loot_module_string.sql",
            "\x1b[0m>> Applying update \"data/sql/db-world/city_bot_poi.sql\" 'ab12'...",
            ">> Applied 1 query",
        ]
    ) == frozenset({"aoe_loot_module_string.sql", "city_bot_poi.sql"})


def _two_route_plan(tmp_path: Path) -> apply_module.ModuleSqlPlan:
    """`mine`, whose SQL this app applied, beside `theirs`, which is the updater's."""
    clone = _clone_with(tmp_path, "mine", ARAC_SQL)
    claim = clone / apply_module.CLAIM_FILE
    claim.write_text("{}", encoding="utf-8")
    os.utime(claim, (1_757_980_800, 1_757_980_800))  # 2025-09-16 UTC
    _clone_with(tmp_path, "theirs", CHAR_SQL)
    return apply_module.module_sql_plan(
        tmp_path,
        [
            _sql_module("mine", [{"db": "world", "path": ARAC_SQL, "applied_by": "direct"}]),
            _sql_module(
                "theirs", [{"db": "characters", "path": CHAR_SQL, "applied_by": "db-import"}]
            ),
        ],
        ["mine", "theirs"],
    )


def test_the_report_says_per_file_what_was_done_with_it(tmp_path: Path) -> None:
    """The verdicts, and the date comes off the record this app wrote.

    The date is asserted against a timestamp this test PUT on the claim file, so
    it cannot be satisfied by any sentence the code builds out of its own
    arguments: it is a fact from the filesystem or it is wrong. What it is a
    date OF is the subject of the two tests below.
    """
    plan = _two_route_plan(tmp_path)
    applied = apply_module.module_sql_report(
        plan, service="ac-db-import", applied=frozenset({"one.sql"})
    )
    assert (
        f"sql {ARAC_SQL} -> world: not handed to the updater: this app applies it itself at "
        f"install (module installed here on 2025-09-16)" in applied
    )
    assert f"sql {CHAR_SQL} -> characters: applied" in applied

    quiet = apply_module.module_sql_report(plan, service="ac-db-import", applied=frozenset())
    assert (
        f"sql {CHAR_SQL} -> characters: not applied now: ac-db-import did not name it"
        in " ".join(quiet)
    )

    refused = apply_module.module_sql_report(
        plan,
        service="ac-db-import",
        applied=frozenset(),
        refusal="Could not update the World database",
    )
    assert f"sql {CHAR_SQL} -> characters: refused: Could not update the World database" in refused
    # The file this app owns reads the same whatever the updater did with the
    # rest: it was never handed over, so no verdict of the run applies to it.
    assert [line for line in refused if ARAC_SQL in line] == [
        line for line in applied if ARAC_SQL in line
    ]


def test_a_claim_is_not_evidence_that_the_direct_sql_ever_ran(tmp_path: Path) -> None:
    """Review round 1's must-fix, driven through the REAL install that leaves this state.

    `install()` writes the claim (`apply.py:1366`) and runs the SQL
    (`apply.py:1386`) in that order, so an install whose SQL step raises leaves a
    clone and a dated claim behind with nothing whatever in the world database.
    T2 measured that failure on a real box: `DockerSql` refusing with *"container
    ... is not running"*. The report may therefore never turn a claim into
    "applied" -- it has no record of an application to turn.

    Driven by letting the real applier fail at its real SQL step rather than by
    writing a claim by hand, because the fixture that writes the claim itself is
    free to write the state that makes the sentence true.
    """
    module = _sql_module("mine", [{"db": "world", "path": ARAC_SQL, "applied_by": "direct"}])

    class _DeadContainer:
        def run_file(self, db: str, path: Path) -> None:
            raise ApplyError("container ac-database is not running")

        def run_statement(self, db: str, statement: str) -> None:
            raise AssertionError("not reached")

    applier = Applier(
        tmp_path,
        git=_FakeGit({ARAC_SQL: "ALTER TABLE x ADD COLUMN y INT;\n"}),
        sql=_DeadContainer(),
        remote_url=_Origins("https://github.com/acme/mine.git"),
        world_running=lambda: False,
    )
    with pytest.raises(ApplyError, match="not running"):
        applier.install(module)
    clone = tmp_path / "modules" / "mine"
    assert (clone / apply_module.CLAIM_FILE).is_file(), "the install did not get as far as a claim"

    said = apply_module.module_sql_report(
        apply_module.module_sql_plan(tmp_path, [module], ["mine"]),
        service="ac-db-import",
        applied=frozenset(),
    )
    arac = [line for line in said if line.startswith(f"sql {ARAC_SQL} ")]
    assert arac and "applies it itself at install" in arac[0], said
    assert "applied" not in arac[0], (
        "the panel claims a file was applied on the strength of a claim written "
        f"BEFORE the SQL that never ran: {arac[0]}"
    )


def test_a_clone_this_app_did_not_make_is_given_no_date_at_all(tmp_path: Path) -> None:
    """No claim, no date. The directory's own mtime is not a record of ours.

    A checkout somebody else put in `modules/` has no install of this app's to
    date, and its mtime moves when a `git pull`, an editor or the compile writes
    inside it. Printing that as the day this app did something would invent the
    record T78 exists because we do not keep.

    The clone here is given a mtime the assertion would notice if it leaked --
    the same stamp the test above pins on a claim.
    """
    clone = _clone_with(tmp_path, "mine", ARAC_SQL)
    os.utime(clone, (1_757_980_800, 1_757_980_800))  # 2025-09-16 UTC
    assert not (clone / apply_module.CLAIM_FILE).exists()

    plan = apply_module.module_sql_plan(
        tmp_path,
        [_sql_module("mine", [{"db": "world", "path": ARAC_SQL, "applied_by": "direct"}])],
        ["mine"],
    )
    assert [entry.installed_on for entry in plan.files] == [""]
    said = apply_module.module_sql_report(plan, service="ac-db-import", applied=frozenset())
    arac = [line for line in said if line.startswith(f"sql {ARAC_SQL} ")]
    assert arac == [
        f"sql {ARAC_SQL} -> world: not handed to the updater: this app applies it itself at install"
    ], arac
    assert "2025-09-16" not in " ".join(said), said


def test_a_db_import_file_inside_a_withheld_module_is_not_reported_as_ledgered(
    tmp_path: Path,
) -> None:
    """The verdict that would be T78 upside down.

    A module withheld for its direct file takes its db-import files with it --
    `AC_UPDATES_ALLOWED_MODULES` is per module, not per file -- so those files
    were not offered to the updater at all. Calling them "already in its ledger"
    because nothing complained would promise a row in the database on the
    strength of a run they were never part of.
    """
    _clone_with(tmp_path, "mine", ARAC_SQL, CHAR_SQL)
    plan = apply_module.module_sql_plan(
        tmp_path,
        [
            _sql_module(
                "mine",
                [
                    {"db": "world", "path": ARAC_SQL, "applied_by": "direct"},
                    {"db": "characters", "path": CHAR_SQL, "applied_by": "db-import"},
                ],
            )
        ],
        ["mine"],
    )
    said = apply_module.module_sql_report(plan, service="ac-db-import", applied=frozenset())
    assert (
        f"sql {CHAR_SQL} -> characters: not applied: mine was not given to ac-db-import, so the "
        f"updater was not offered this file either" in said
    ), said
    assert not [line for line in said if "ledger looks like" in line], said

    # Nor is it blamed for a refusal it was not part of, which is the same rule
    # from the other side: no verdict of the run applies to a file the run never
    # had. Every line reads the same whatever the updater said.
    assert (
        apply_module.module_sql_report(
            plan, service="ac-db-import", applied=frozenset(), refusal="Could not update the World"
        )
        == said
    )


# ---- T78 round 3: the withholding is decided per FILE, not per `data/sql/`
#
# The 2026-09-17 live gate's finding. City Bots keeps THREE `db-import` groups
# under the three directories the updater walks and ONE `direct` file under
# `data/sql/playerbots/`, which nothing joins a path for -- and that one file
# withheld the module whole, so the three groups went unapplied, the world died
# on `Table 'acore_world.city_bot_poi' doesn't exist`, and the app's own remedy
# ("Press Apply module SQL") could not be followed by pressing that button.
# ARAC could not see this: its ONLY SQL step is the direct one, so withholding
# the module and withholding the file are the same act there.

CITY_AUTH = "data/sql/db-auth/updates/2026_07_16_03_stage_cast_one_account_per_bot.sql"
CITY_CHAR = "data/sql/db-characters/updates/2026_08_22_01_stage_cast_outfits.sql"
CITY_WORLD = "data/sql/db-world/updates/2026_07_13_01_city_bot_poi.sql"
CITY_ROSTER = "data/sql/playerbots/updates/2026_07_15_00_citizen_roster.sql"
"""mod-city-bots' four SQL paths, verbatim off the shipped manifest."""


def _city_bots_shape(tmp_path: Path, direct: str) -> apply_module.ModuleSqlPlan:
    """City Bots' manifest with its one direct file moved to `direct`, and nothing else.

    The two callers differ in ONE field -- which directory the direct file sits
    in -- so no other rule can account for a difference between their answers.
    The three db-import groups are declared as globs, as the shipped manifest
    declares them, so a rule that read the declared path instead of the resolved
    one would be visible here.
    """
    _clone_with(tmp_path, "mod-city-bots", CITY_AUTH, CITY_CHAR, CITY_WORLD, direct)
    return apply_module.module_sql_plan(
        tmp_path,
        [
            _sql_module(
                "mod-city-bots",
                [
                    {
                        "db": "auth",
                        "path": "data/sql/db-auth/updates/*.sql",
                        "applied_by": "db-import",
                    },
                    {
                        "db": "characters",
                        "path": "data/sql/db-characters/updates/*.sql",
                        "applied_by": "db-import",
                    },
                    {
                        "db": "world",
                        "path": "data/sql/db-world/updates/*.sql",
                        "applied_by": "db-import",
                    },
                    {"db": "playerbots", "path": direct, "applied_by": "direct"},
                ],
            )
        ],
        ["mod-city-bots"],
    )


def test_a_direct_file_outside_the_updaters_directories_keeps_its_module_in_the_list(
    tmp_path: Path,
) -> None:
    """The gate's finding, as the plan: City Bots is handed over, all three groups with it.

    `data/sql/playerbots/` is under `data/sql/` and is walked by nobody -- the
    core updater runs once per `DatabaseLoader` pool and mod-playerbots builds
    its own with no module list at all. So the roster file is not in the
    updater's way, and it is not a reason to cost this module the three groups
    that ARE the updater's work.
    """
    plan = _city_bots_shape(tmp_path, CITY_ROSTER)
    assert plan.withheld == ()
    assert plan.handed == ("mod-city-bots",)
    assert plan.allowed == "mod-city-bots"

    said = apply_module.module_sql_report(
        plan,
        service="ac-db-import",
        applied=frozenset(PurePosixPath(p).name for p in (CITY_AUTH, CITY_CHAR, CITY_WORLD)),
    )
    assert f"sql {CITY_AUTH} -> auth: applied" in said, said
    assert f"sql {CITY_CHAR} -> characters: applied" in said, said
    assert f"sql {CITY_WORLD} -> world: applied" in said, said
    assert (
        f"sql {CITY_ROSTER} -> playerbots: not handed to the updater: this app applies it "
        f"itself at install" in said
    ), said
    # No withholding sentence at all, and in particular not the two the gate
    # photographed: the panel must not tell the user this module was skipped.
    assert not [line for line in said if "not given to ac-db-import" in line], said


def test_a_direct_file_inside_the_updaters_directories_still_withholds_and_says_which(
    tmp_path: Path,
) -> None:
    """The control for the test above, and the sentence a mixed module needs.

    Same manifest, same clone, same three db-import groups; the ONE difference
    is that the direct file now sits in `data/sql/db-world/`, where the updater
    would open it and exit 1 over a file it holds no ledger row for. Withholding
    the module is the only lever `AC_UPDATES_ALLOWED_MODULES` has and the three
    groups are its price -- so the sentence has to say which file cost them.
    "That folder's SQL" was false here: this app runs one of four.
    """
    inside = "data/sql/db-world/updates/2026_07_15_00_citizen_roster.sql"
    plan = _city_bots_shape(tmp_path, inside)
    assert plan.withheld == ("mod-city-bots",)
    assert plan.allowed == ""

    said = apply_module.module_sql_report(plan, service="ac-db-import", applied=frozenset())
    assert (
        f"mod-city-bots: not given to ac-db-import -- this app runs {inside} itself, and "
        f"the updater refuses a file it holds no ledger row for." in said
    ), said
    # It names the ONE file: not the folder, and not the three it is not about.
    reason = [line for line in said if line.startswith("mod-city-bots: not given")][0]
    assert "that folder's SQL" not in reason, reason
    assert CITY_WORLD not in reason, reason


def test_the_withheld_sentence_names_the_file_for_a_module_with_only_one_step(
    tmp_path: Path,
) -> None:
    """ARAC's shape, which is where the old wording was accidentally true.

    Named separately because it is the case the gate PASSED: a one-step module
    where "that folder's SQL" and `arac.sql` describe the same thing. The new
    sentence has to stay true here as well as become true of the mixed one.
    """
    _clone_with(tmp_path, "mod-arac", ARAC_SQL)
    plan = apply_module.module_sql_plan(
        tmp_path,
        [_sql_module("mod-arac", [{"db": "world", "path": ARAC_SQL, "applied_by": "direct"}])],
        ["mod-arac"],
    )
    said = apply_module.module_sql_report(plan, service="ac-db-import", applied=frozenset())
    assert (
        f"mod-arac: not given to ac-db-import -- this app runs {ARAC_SQL} itself, and "
        f"the updater refuses a file it holds no ledger row for." in said
    ), said


def test_every_shipped_db_import_step_names_a_directory_the_updater_actually_walks() -> None:
    """A `db-import` step outside `IMPORTER_DB_DIRS` would be a promise nobody keeps.

    The catalog's own data, not a fixture. `applied_by: "db-import"` means "this
    app runs none of it, the core updater will" -- and the core updater walks
    `data/sql/db-auth`, `data/sql/db-characters` and `data/sql/db-world` and
    nothing else. A shipped manifest naming, say, `data/sql/playerbots/` as
    db-import would leave that file applied by no route at all, which is exactly
    the trap City Bots' manifest avoids by declaring its roster `direct`.
    """
    store = ManifestStore(manifests_dir(), "wow-wotlk")
    checked = 0
    for manifest in store.load_all("module"):
        for step in manifest.sql:
            if step.applied_by != "db-import" or step.path is None:
                continue
            assert step.path.startswith(
                apply_module.IMPORTER_SQL_DIRS
            ), f"{manifest.id} leaves {step.path} to the updater, which never walks it"
            checked += 1
    assert checked, "no shipped db-import step was examined, so this asserted nothing"
