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
from yulon.git import CloneSpec, RunnerGit
from yulon.manifest import parse_manifest

LUA = "env/dist/etc/modules/lua_scripts"


class _FakeGit:
    """Writes `files` (relative path → text) into the clone dir instead of cloning."""

    def __init__(self, files: dict[str, str]) -> None:
        self.files = files
        self.calls: list[CloneSpec] = []

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


# ------------------------------------------------------- WSL-resident servers


def test_the_sql_runner_reaches_the_distros_own_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    """A WSL-resident server's database answers only to that distro's docker."""
    monkeypatch.setattr(apply_module.platform, "_which", lambda name, path=None: "wsl.exe")
    runner_ = DockerSql("ac-database", "hunter2", wsl_distro="dml-arch")
    argv = runner_._argv("auth")
    assert argv[:5] == ["wsl.exe", "-d", "dml-arch", "--", "docker"]
    assert "exec" in argv and "ac-database" in argv


def test_the_password_still_never_enters_argv_through_wsl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one rule that must survive the crossing.

    `docker exec -e MYSQL_PWD` names the variable and not its value precisely so
    the secret stays out of a command line every local process can read. Routing
    through `wsl.exe` must not quietly turn that into `-e MYSQL_PWD=hunter2`.
    """
    monkeypatch.setattr(apply_module.platform, "_which", lambda name, path=None: "wsl.exe")
    argv = DockerSql("ac-database", "hunter2", wsl_distro="dml-arch")._argv("auth")
    assert "MYSQL_PWD" in argv
    assert not any("hunter2" in part for part in argv), f"the password is in argv: {argv}"


def test_mysql_env_names_the_password_in_wslenv_for_a_distro() -> None:
    """Without WSLENV the variable crosses EMPTY, and mysql reports a bad password.

    Measured 2026-08-26: a variable set on the Windows side arrives as `[]`
    inside the distro unless WSLENV names it. The failure is an authentication
    error, not a missing-setting error, which is the worst kind to debug - so it
    is set in `mysql_env()`, the one place this codebase decides how the
    password is handed over.
    """
    env = apply_module.mysql_env("hunter2", wsl_distro="dml-arch")
    assert env["MYSQL_PWD"] == "hunter2"
    assert "MYSQL_PWD" in env["WSLENV"].split(":")


def test_mysql_env_adds_no_wslenv_for_a_local_install() -> None:
    """Nothing crosses a boundary, so nothing needs announcing."""
    env = apply_module.mysql_env("hunter2")
    assert env["MYSQL_PWD"] == "hunter2"
    assert "WSLENV" not in env or "MYSQL_PWD" not in env.get("WSLENV", "")


def test_the_sql_runner_announces_the_password_through_its_own_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through `DockerSql`, not through `mysql_env` - that is the whole point.

    The first WSLENV test called `mysql_env()` directly and passed, while
    `DockerSql._env()` called it WITHOUT the distro. So the function was right,
    the only caller that matters was wrong, and the password crossed into the
    distro empty: an authentication failure against a healthy database, which is
    the exact symptom the crossing exists to prevent.

    Asserting through the object is the fix for the test as much as for the code.
    """
    monkeypatch.setattr(apply_module.platform, "_which", lambda name, path=None: "wsl.exe")
    env = DockerSql("ac-database", "hunter2", wsl_distro="dml-arch")._env()
    assert env["MYSQL_PWD"] == "hunter2"
    assert "MYSQL_PWD" in env.get("WSLENV", "").split(
        ":"
    ), "the password will arrive EMPTY inside the distro"


def test_a_local_sql_runner_announces_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing crosses a boundary, so nothing needs announcing."""
    env = DockerSql("ac-database", "hunter2")._env()
    assert env["MYSQL_PWD"] == "hunter2"
    assert "MYSQL_PWD" not in env.get("WSLENV", "").split(":")
