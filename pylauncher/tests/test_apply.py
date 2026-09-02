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

from yulon import apply as apply_module
from yulon.apply import Applier, ApplyError, DockerSql, _set_conf_key
from yulon.catalog import composegen, native
from yulon.git import CloneSpec, RunnerGit
from yulon.manifest import parse_manifest
from yulon.ownership import Ownership

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

OFF_PATH_EXE = r"C:\Users\pk\AppData\Local\Programs\DockerDesktop\resources\bin\docker.EXE"


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
    git = _FakeGit({"README.md": "upstream\n"})
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
    assert origins.asked == []  # the per-clone claim IS the corroboration


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

    assert git.branches_asked == ["wotlk"]


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
