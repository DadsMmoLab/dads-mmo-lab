"""T778: the stdlib script that packs a WotLK server's accounts and characters inside a WSL distro.

The script is a player's program, run without Yu'lon. Here it runs as a real subprocess with
a fake `docker` (and, for one case, fake `mysql`/`mysqldump` programs) first on PATH, so the
command lines, the environment and the file modes it produces are what is checked. Its package
then goes through the real reader (`move.read_package`), the real dump check
(`maintenance.verify_dump`) and the real import planner (`move_flows.plan_import`) against a
fresh server of the same game, version and modules.
"""

from __future__ import annotations

import errno
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import types
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.test_move_flows import Box
from yulon import move, move_flows
from yulon.catalog.catalog import load_catalog
from yulon.controller_wow_wotlk import maintenance

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "archive" / "guides" / "wow-wotlk" / "export-accounts-and-characters-wsl.py"
PASSWORD = "sekrit-test-pw"

FIXTURE: dict[str, Any] = {
    "daemon": True,
    "password": PASSWORD,
    "containers": {
        "ac-database": True,
        "ac-authserver": False,
        "ac-worldserver": False,
    },
    "databases": [
        "information_schema",
        "acore_auth",
        "acore_characters",
        "acore_world",
        "mysql",
        "performance_schema",
        "sys",
    ],
    "tables": {
        "acore_auth": ["account", "account_access", "realmlist", "updates"],
        "acore_characters": ["characters", "character_inventory", "updates"],
        "acore_world": ["creature", "updates"],
    },
    "rows": {
        "acore_auth": {
            "account": ["(1,'ALICE')", "(2,'BOB')", "(3,'RNDBOT1')"],
            "realmlist": ["(1,'Local Realm')"],
        },
        "acore_characters": {
            "characters": [
                "(10,1,'Thrall')",
                "(11,2,'Jaina')",
                "(12,2,'Sylvanas')",
                "(20,3,'RNDBOT1Lok')",
                "(21,3,'RNDBOT1Two')",
            ],
        },
        "acore_world": {"creature": ["(500,'world only')"]},
    },
    "updates": {
        "acore_auth": ["2024_01_a", "2024_01_b"],
        "acore_characters": ["2024_01_c"],
        "acore_world": ["2024_02_w"],
    },
    "module_rows": {"acore_characters": [["mod-transmog", "9F2C"]]},
    "bot_prefix": "RNDBOT",
    "truncate": [],
    "no_banner": [],
    "counts": [2, 3, 1, 2],
    "realm": "Local Realm",
}
"""What the fake server holds: two player accounts with three characters, one bot account with
one bot character, the release names of each ledger, and the one module row."""

FAKE_DB = r"""
import json, os, re, sys, time
from pathlib import Path

FIXTURE = json.loads(Path(os.environ["FAKE_DB_FIXTURE"]).read_text(encoding="utf-8"))
LOG = Path(os.environ["FAKE_DB_LOG"])


def log(**entry):
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def fail(message, code=1):
    sys.stderr.write(message + "\n")
    sys.exit(code)


def dump(database, ignore):
    named = "acore_world" if database in FIXTURE.get("wrong_name", []) else database
    lines = [
        "-- Not a dump" if database in FIXTURE["no_banner"]
        else "-- MySQL dump 10.13  Distrib 8.4.3, for Linux (x86_64)",
        "--",
        f"-- Host: localhost    Database: {database}",
        "-- ------------------------------------------------------",
        "-- Server version\t8.4.3",
        "",
        f"CREATE DATABASE /*!32312 IF NOT EXISTS*/ `{named}` "
        "/*!40100 DEFAULT CHARACTER SET utf8mb4 */;",
        f"USE `{named}`;",
    ]
    if database in FIXTURE.get("statement_first", []):
        lines.insert(0, "DROP DATABASE `other`;")
    for table in FIXTURE["tables"].get(database, []):
        if f"{database}.{table}" in ignore:
            continue
        lines += [
            f"DROP TABLE IF EXISTS `{table}`;",
            f"CREATE TABLE `{table}` (",
            "  `id` int NOT NULL",
            ");",
        ]
        rows = FIXTURE["rows"].get(database, {}).get(table, [])
        if rows:
            lines.append(f"INSERT INTO `{table}` VALUES " + ",".join(rows) + ";")
    if database not in FIXTURE["truncate"]:
        lines.append("-- Dump completed on 2026-10-12 21:00:00")
    return "\n".join(lines) + "\n"


def answer(sql):
    if sql.strip().upper().startswith("SHOW DATABASES"):
        return "".join(f"{name}\n" for name in FIXTURE["databases"])
    if "information_schema.TABLES" in sql:
        schema = re.search(r"TABLE_SCHEMA = '([^']+)'", sql).group(1)
        names = FIXTURE["tables"].get(schema, [])
        wanted = ("updates", "migrations", "character_db_version", "realmd_db_version")
        return "".join(f"{n}\n" for n in wanted if n in names)
    if "`updates` WHERE `state` IN" in sql:
        schema = re.search(r"FROM `(\w+)`\.`updates`", sql).group(1)
        return "".join(f"{n}\n" for n in FIXTURE["updates"].get(schema, []))
    if "`updates` WHERE `state` NOT IN" in sql:
        schema = re.search(r"FROM `(\w+)`\.`updates`", sql).group(1)
        return "".join(f"{n}\t{h}\n" for n, h in FIXTURE["module_rows"].get(schema, []))
    if "SELECT (SELECT COUNT(*)" in sql:
        if f"= '{FIXTURE['bot_prefix'].upper()}'" not in sql:
            fail("unscripted count query: the bot clause is not the server prefix")
        return "\t".join(str(n) for n in FIXTURE["counts"]) + "\n"
    if "FROM `acore_auth`.`realmlist`" in sql:
        return FIXTURE["realm"] + "\n"
    fail("unscripted query: " + sql)


def docker(args):
    if args[:1] in (["info"], ["version"]):
        if not FIXTURE["daemon"]:
            fail("Cannot connect to the Docker daemon", 1)
        return print("27.0.0")
    if args[:1] == ["inspect"]:
        name = args[-1]
        if name not in FIXTURE["containers"]:
            fail(f"Error: No such object: {name}", 1)
        return print("true" if FIXTURE["containers"][name] else "false")
    if args[:1] == ["ps"]:
        return print("\n".join(FIXTURE["containers"]))
    if args[:1] == ["exec"]:
        rest = args[1:]
        if rest[:1] == ["-i"]:
            rest = rest[1:]
        if rest[:1] == ["-e"]:
            rest = rest[2:]
        container, tool, tool_args = rest[0], rest[1], rest[2:]
        if container not in FIXTURE["containers"]:
            fail(f"Error: No such container: {container}", 1)
        if not FIXTURE["containers"][container]:
            fail(f"Error response from daemon: container {container} is not running", 1)
        return client(tool, tool_args, container)
    fail("unscripted docker call: " + " ".join(args))


def client(tool, args, container):
    password = os.environ.get("MYSQL_PWD")
    log(tool=tool, argv=args, container=container, pwd_present=password is not None,
        pwd_right=password == FIXTURE["password"], stdout_mode=oct(os.fstat(1).st_mode & 0o777))
    if password != FIXTURE["password"]:
        fail("ERROR 1045 (28000): Access denied for user 'root'@'localhost'", 1)
    if tool in ("mysql", "mariadb"):
        return sys.stdout.write(answer(sys.stdin.read()))
    if tool in ("mysqldump", "mariadb-dump"):
        for flag in ("--single-transaction", "--routines", "--events", "--triggers"):
            if flag not in args:
                fail("mysqldump was not asked for " + flag, 1)
        if FIXTURE.get("hang_dump"):
            sys.stdout.write("-- MySQL dump 10.13  half a dump\n")
            sys.stdout.flush()
            open(os.environ["FAKE_DB_LOG"] + ".hanging", "w").close()
            time.sleep(60)
        if FIXTURE.get("dump_error"):
            fail("mysqldump: " + FIXTURE["dump_error"], 2)
        database = args[args.index("--databases") + 1]
        ignore = [a.split("=", 1)[1] for a in args if a.startswith("--ignore-table=")]
        return sys.stdout.write(dump(database, ignore))
    fail("unscripted tool: " + tool)


if __name__ == "__main__":
    tool, args = sys.argv[1], sys.argv[2:]
    if tool == "docker":
        docker(args)
    else:
        client(tool, args, None)
"""


class TargetDb:
    """A fresh WotLK server of the same version and modules, answering the planner's questions.

    The planner only reads from it: the databases, their tables, the ledgers, the counts and
    the realm row. An empty server, so nothing it holds would be lost to the import.
    """

    def __init__(self, events: list[str], *, updates: tuple[str, ...] | None = None) -> None:
        self.events: list[str] = events
        self.updates = updates

    def databases(self) -> tuple[str, ...]:
        return tuple(FIXTURE["databases"])

    def dump_into(self, database: str, sink: Any, ignore: tuple[str, ...] = ()) -> None:
        raise AssertionError("the planner never dumps")

    def load_from(self, source: Any) -> None:
        raise AssertionError("the planner never loads")

    def execute(self, sql: str) -> None:
        raise AssertionError("the planner never writes")

    def tables(self, database: str) -> tuple[tuple[str, str], ...]:
        return tuple((name, "BASE TABLE") for name in FIXTURE["tables"].get(database, []))

    def query(self, sql: str) -> str:
        if "information_schema.TABLES" in sql:
            schema = re.search(r"TABLE_SCHEMA = '([^']+)'", sql).group(1)  # type: ignore[union-attr]
            return "updates\n" if "updates" in FIXTURE["tables"].get(schema, []) else ""
        if "`updates` WHERE `state` IN" in sql:
            schema = re.search(r"FROM `(\w+)`\.`updates`", sql).group(1)  # type: ignore[union-attr]
            names = self.updates if self.updates is not None else FIXTURE["updates"][schema]
            return "".join(f"{n}\n" for n in names)
        if "`updates` WHERE `state` NOT IN" in sql:
            schema = re.search(r"FROM `(\w+)`\.`updates`", sql).group(1)  # type: ignore[union-attr]
            return "".join(f"{n}\t{h}\n" for n, h in FIXTURE["module_rows"].get(schema, []))
        if "SELECT (SELECT COUNT(*)" in sql:
            return "0\t0\t0\t0\n"
        if "COUNT(*)" in sql:
            return "0\n"
        if "FROM `acore_auth`.`realmlist`" in sql:
            return "Target Realm\n"
        raise AssertionError(f"unscripted target query: {sql}")


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("wsl_characters_export", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before it runs: the script's dataclasses look their module up by name.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _world(tmp_path: Path, db: TargetDb | None = None) -> Box:
    tmp_path.mkdir(parents=True, exist_ok=True)
    return Box(tmp_path, "wow-wotlk", db=db or TargetDb([]))


def _unpack(package: move.Package, schema: str, root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    return package.extract(schema, root)


def _write_fake_tools(root: Path, names: Iterable[str]) -> Path:
    """`bin/<name>` shims that run the fake database program as that tool."""
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    fake = root / "fake_db.py"
    fake.write_text(FAKE_DB, encoding="utf-8")
    for name in names:
        shim = bin_dir / name
        shim.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{fake}" {name} "$@"\n',
            encoding="utf-8",
        )
        shim.chmod(0o755)
    return bin_dir


def _run(
    tmp_path: Path,
    *,
    tools: Iterable[str] = ("docker",),
    fixture: Mapping[str, Any] | None = None,
    args: Iterable[str] = (),
    conf: str | None = None,
    env_text: str | None = None,
    compose: Mapping[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path]:
    """Run the script; returns (process, server dir, out dir, the fake's log)."""
    root = tmp_path / "run"
    root.mkdir(parents=True, exist_ok=True)
    server = root / "server"
    server.mkdir(exist_ok=True)
    (server / ".env").write_text(
        env_text if env_text is not None else f"MYSQL_ROOT_PASSWORD={PASSWORD}\n",
        encoding="utf-8",
    )
    for name, body in (compose or {}).items():
        (server / name).write_text(body, encoding="utf-8")
    if conf is not None:
        conf_file = server / "env" / "dist" / "etc" / "modules" / "playerbots.conf"
        conf_file.parent.mkdir(parents=True, exist_ok=True)
        conf_file.write_text(conf, encoding="utf-8")
    out = root / "out"
    out.mkdir(exist_ok=True)
    data = {**FIXTURE, **(fixture or {})}
    fixture_path = root / "fixture.json"
    fixture_path.write_text(json.dumps(data), encoding="utf-8")
    log = root / "fake.log"
    bin_dir = _write_fake_tools(root, tools)
    env = {
        "PATH": str(bin_dir),
        "HOME": str(root / "home"),
        "LANG": "C.UTF-8",
        "FAKE_DB_FIXTURE": str(fixture_path),
        "FAKE_DB_LOG": str(log),
    }
    process = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--server-dir",
            str(server),
            "--out",
            str(out),
            *args,
        ],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return process, server, out, log


def _the_package(out: Path) -> Path:
    found = sorted(out.glob("yulon-move-wow-wotlk-*-keep-private.zip"))
    assert len(found) == 1, f"expected one package in {out}, found {found}"
    return found[0]


def _entries(log: Path) -> list[dict[str, Any]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


# ================================================================ the package


def test_docker_mode_packs_accounts_and_characters_that_the_real_planner_accepts(
    tmp_path: Path,
) -> None:
    process, _server, out, _log = _run(tmp_path)
    assert process.returncode == 0, process.stderr
    package = _the_package(out)

    read = move.read_package(package)
    manifest = read.manifest
    assert manifest.kind == "characters"
    assert manifest.game.id == "wow-wotlk"
    assert [m.schema_name for m in manifest.databases] == [
        "acore_auth",
        "acore_characters",
    ]
    assert manifest.counts == move.Counts(
        accounts=2, characters=3, bot_accounts=1, bot_characters=2
    )
    assert manifest.realm_name == "Local Realm"
    assert manifest.channel_account is None
    assert manifest.bot_prefix == "rndbot"
    assert manifest.excluded == (
        "acore_world (not part of accounts and characters)",
        "realmlist of acore_auth (the realm's own address)",
    )
    assert manifest.secrets == move.SECRETS

    # Each dump is read as Yu'lon's own restore reads it, and holds the right characters.
    characters = _unpack(read, "acore_characters", tmp_path / "unpacked")
    maintenance.verify_dump(characters, "acore_characters")
    text = characters.read_text(encoding="utf-8")
    assert "'Thrall'" in text and "'Sylvanas'" in text and "'RNDBOT1Lok'" in text
    assert "world only" not in text

    # The real planner accepts the package on a fresh server of the same game and version.
    box = _world(tmp_path / "target")
    plan = move_flows.plan_import(box.world, package)
    assert plan.refusals == (), plan.refusals
    assert plan.manifest is not None
    assert plan.manifest.counts.characters == 3


def test_the_package_layout_is_the_export_layout_byte_for_byte(tmp_path: Path) -> None:
    process, _server, out, _log = _run(tmp_path)
    assert process.returncode == 0, process.stderr
    package = _the_package(out)

    import zipfile

    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        raw = archive.read(move.MANIFEST_NAME).decode("utf-8")
    assert names == [move.MANIFEST_NAME, "db/acore_auth.sql", "db/acore_characters.sql"]
    # The manifest's text is exactly what Yu'lon's own model writes for the same content.
    model = move.Manifest.model_validate(json.loads(raw))
    assert raw == model.model_dump_json(by_alias=True, indent=2)
    assert json.loads(raw)["format"] == move.FORMAT
    assert json.loads(raw)["server"] is None
    assert package.name.startswith("yulon-move-wow-wotlk-")


def test_the_auth_dump_leaves_the_realm_row_out_and_the_password_stays_out_of_argv(
    tmp_path: Path,
) -> None:
    process, _server, out, log = _run(tmp_path)
    assert process.returncode == 0, process.stderr
    package = _the_package(out)
    read = move.read_package(package)
    auth = _unpack(read, "acore_auth", tmp_path / "unpacked")
    assert "CREATE TABLE `realmlist`" not in auth.read_text(encoding="utf-8")

    entries = _entries(log)
    dumps = [e for e in entries if e["tool"] == "mysqldump"]
    assert ["--ignore-table=acore_auth.realmlist" in e["argv"] for e in dumps] == [
        True,
        False,
    ]
    assert all(e["container"] == "ac-database" for e in entries)
    assert all(e["pwd_present"] and e["pwd_right"] for e in entries)
    combined = json.dumps(entries) + process.stdout + process.stderr
    assert PASSWORD not in combined


def test_the_package_and_its_working_files_are_owner_only(tmp_path: Path) -> None:
    process, _server, out, _log = _run(tmp_path)
    assert process.returncode == 0, process.stderr
    package = _the_package(out)
    assert stat.S_IMODE(package.stat().st_mode) == 0o600
    assert sorted(p.name for p in out.iterdir()) == [package.name]


# ================================================================ refusals


def test_a_running_server_is_refused_in_docker_mode(tmp_path: Path) -> None:
    process, _server, out, _log = _run(
        tmp_path,
        fixture={
            "containers": {
                "ac-database": True,
                "ac-authserver": True,
                "ac-worldserver": True,
            }
        },
    )
    assert process.returncode == 2
    assert "Stop only the game servers" in process.stderr
    assert list(out.iterdir()) == []


def test_a_stopped_database_container_is_refused_in_plain_words(tmp_path: Path) -> None:
    process, _server, out, _log = _run(
        tmp_path,
        fixture={
            "containers": {
                "ac-database": False,
                "ac-authserver": False,
                "ac-worldserver": False,
            }
        },
    )
    assert process.returncode == 2
    assert "database" in process.stderr and "stopped" in process.stderr
    assert list(out.iterdir()) == []


def test_host_mode_needs_the_server_stopped_flag_and_then_packs(tmp_path: Path) -> None:
    refused, _server, out, _log = _run(tmp_path / "a", tools=("mysql", "mysqldump"))
    assert refused.returncode == 2
    assert "--server-stopped" in refused.stderr
    assert list(out.iterdir()) == []

    packed, _server, out, log = _run(
        tmp_path / "b", tools=("mysql", "mysqldump"), args=("--server-stopped",)
    )
    assert packed.returncode == 0, packed.stderr
    assert {e["tool"] for e in _entries(log)} <= {"mysql", "mysqldump"}
    assert all(e["container"] is None for e in _entries(log))
    assert move.read_package(_the_package(out)).manifest.game.id == "wow-wotlk"


def test_no_docker_and_no_mysql_client_is_refused_in_plain_words(
    tmp_path: Path,
) -> None:
    process, _server, out, _log = _run(tmp_path, tools=())
    assert process.returncode == 2
    assert "docker" in process.stderr.lower() and "mysql" in process.stderr.lower()
    assert list(out.iterdir()) == []


def test_a_server_folder_with_no_password_and_no_terminal_is_refused(
    tmp_path: Path,
) -> None:
    root = tmp_path / "nopass"
    root.mkdir()
    server = root / "server"
    server.mkdir()
    out = root / "out"
    out.mkdir()
    fixture_path = root / "fixture.json"
    fixture_path.write_text(json.dumps(FIXTURE), encoding="utf-8")
    bin_dir = _write_fake_tools(root, ("docker",))
    env = {
        "PATH": str(bin_dir),
        "HOME": str(root / "home"),
        "LANG": "C.UTF-8",
        "FAKE_DB_FIXTURE": str(fixture_path),
        "FAKE_DB_LOG": str(root / "fake.log"),
    }
    process = subprocess.run(
        [sys.executable, str(SCRIPT), "--server-dir", str(server), "--out", str(out)],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert process.returncode == 2
    assert "password" in process.stderr.lower()
    assert list(out.iterdir()) == []


# ================================================================ the planner is really asked


def test_the_real_planner_refuses_a_fresh_server_on_another_version(
    tmp_path: Path,
) -> None:
    process, _server, out, _log = _run(tmp_path)
    assert process.returncode == 0, process.stderr
    package = _the_package(out)
    box = _world(tmp_path / "other", TargetDb([], updates=("2024_01_a",)))
    plan = move_flows.plan_import(box.world, package)
    assert any("not at the same version" in refusal for refusal in plan.refusals), plan.refusals


# ================================================================ the script and the app agree


def test_the_script_constants_match_the_catalog_and_the_package_format() -> None:
    script = _script()
    entry = load_catalog().get("wow-wotlk")
    schemas = entry.schema_map()
    assert script.SCHEMA["auth"] == schemas["auth"]
    assert script.SCHEMA["characters"] == schemas["characters"]
    assert script.SCHEMA["playerbots"] == schemas["playerbots"]
    assert script.SCHEMA["ale"] == schemas["ale"]
    assert script.CONTAINERS["db"] == entry.container_spec().db
    assert script.CONTAINERS["auth"] == entry.container_spec().auth
    assert script.CONTAINERS["world"] == entry.container_spec().world
    assert script.REALMLIST_TABLE == entry.realmlist.table
    assert script.REALM_ID == entry.realmlist.realm_id
    assert script.CHARACTERS_TABLE == entry.observability.characters.table
    assert script.CHARACTERS_ACCOUNT == entry.observability.characters.account
    assert script.DEFAULT_BOT_PREFIX == entry.observability.bots.account_prefix
    assert script.BOT_PREFIX_KEYS == (
        "AiPlayerbot.RandomBotAccountPrefix",
        "Playerbots.RandomBotAccountPrefix",
    )
    assert entry.observability.bots.prefix_conf_key in script.BOT_PREFIX_KEYS
    assert script.SECRETS == move.SECRETS
    assert script.FORMAT == move.FORMAT
    assert script.MANIFEST_NAME == move.MANIFEST_NAME
    assert script.GAME_RECORD_TEMPLATE.format(game="wow-wotlk") == "-- yulon-backup: game=wow-wotlk"
    assert re.fullmatch(maintenance.GAME_RECORD.pattern, "-- yulon-backup: game=wow-wotlk")


# ================================================================ guards, one per refusal


def test_the_dumps_are_owner_only_while_they_are_written(tmp_path: Path) -> None:
    process, _server, _out, log = _run(tmp_path)
    assert process.returncode == 0, process.stderr
    dumps = [e for e in _entries(log) if e["tool"] == "mysqldump"]
    assert len(dumps) == 2 and all(e["stdout_mode"] == "0o600" for e in dumps), dumps


def test_a_dump_cut_short_is_refused_and_nothing_is_packed(tmp_path: Path) -> None:
    process, _server, out, _log = _run(tmp_path, fixture={"truncate": ["acore_characters"]})
    assert process.returncode == 2
    assert "cut short" in process.stderr
    assert list(out.iterdir()) == []


def test_a_dump_without_mysqldumps_banner_is_refused(tmp_path: Path) -> None:
    process, _server, out, _log = _run(tmp_path, fixture={"no_banner": ["acore_characters"]})
    assert process.returncode == 2
    assert "does not start like a mysqldump" in process.stderr
    assert list(out.iterdir()) == []


def test_the_bot_prefix_comes_from_the_servers_conf(tmp_path: Path) -> None:
    conf = "[worldserver]\nAiPlayerbot.RandomBotAccountPrefix = XBOT\n"
    process, _server, out, _log = _run(tmp_path, fixture={"bot_prefix": "XBOT"}, conf=conf)
    assert process.returncode == 0, process.stderr
    assert move.read_package(_the_package(out)).manifest.bot_prefix == "XBOT"


def test_a_bot_prefix_set_to_two_values_is_refused(tmp_path: Path) -> None:
    conf = "AiPlayerbot.RandomBotAccountPrefix = XBOT\nPlayerbots.RandomBotAccountPrefix = YBOT\n"
    process, _server, out, _log = _run(tmp_path, conf=conf)
    assert process.returncode == 2
    assert "set more than once" in process.stderr
    assert list(out.iterdir()) == []


def test_bot_and_engine_schemas_travel_when_the_server_has_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(FIXTURE, "databases", [*FIXTURE["databases"], "acore_playerbots"])
    monkeypatch.setitem(
        FIXTURE,
        "tables",
        {
            **FIXTURE["tables"],
            "acore_playerbots": ["playerbots_account_type", "updates"],
        },
    )
    monkeypatch.setitem(
        FIXTURE, "updates", {**FIXTURE["updates"], "acore_playerbots": ["2024_01_p"]}
    )
    process, _server, out, _log = _run(tmp_path)
    assert process.returncode == 0, process.stderr
    package = _the_package(out)
    read = move.read_package(package)
    assert [m.schema_name for m in read.manifest.databases] == [
        "acore_auth",
        "acore_characters",
        "acore_playerbots",
    ]
    assert set(read.manifest.schema_evidence) == {
        "acore_auth",
        "acore_characters",
        "acore_playerbots",
    }
    plan = move_flows.plan_import(_world(tmp_path / "target").world, package)
    assert plan.refusals == (), plan.refusals


def test_an_existing_package_of_the_same_minute_is_never_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _script()
    root = tmp_path / "exists"
    root.mkdir(parents=True)
    server = root / "server"
    server.mkdir()
    (server / ".env").write_text(f"MYSQL_ROOT_PASSWORD={PASSWORD}\n", encoding="utf-8")
    out = root / "out"
    out.mkdir()
    fixture = root / "fixture.json"
    fixture.write_text(json.dumps(FIXTURE), encoding="utf-8")
    monkeypatch.setenv("PATH", str(_write_fake_tools(root, ("docker",))))
    monkeypatch.setenv("FAKE_DB_FIXTURE", str(fixture))
    monkeypatch.setenv("FAKE_DB_LOG", str(root / "fake.log"))

    class Fixed(datetime):
        @classmethod
        def now(cls, tz: object = None) -> Fixed:  # type: ignore[override]
            return cls(2026, 10, 12, 21, 0, 0)

    monkeypatch.setattr(script, "datetime", Fixed)
    existing = out / "yulon-move-wow-wotlk-20261012-2100-keep-private.zip"
    existing.write_bytes(b"an earlier package")
    with pytest.raises(script.Refusal, match="already exists"):
        script.pack(server, out, False)
    assert existing.read_bytes() == b"an earlier package"
    assert sorted(p.name for p in out.iterdir()) == [existing.name]


def test_a_statement_above_the_banner_is_refused(tmp_path: Path) -> None:
    process, _server, out, _log = _run(tmp_path, fixture={"statement_first": ["acore_characters"]})
    assert process.returncode == 2
    assert "does not start like a mysqldump" in process.stderr
    assert list(out.iterdir()) == []


def test_a_server_without_a_characters_database_is_refused(tmp_path: Path) -> None:
    databases = [d for d in FIXTURE["databases"] if d != "acore_characters"]
    process, _server, out, _log = _run(tmp_path, fixture={"databases": databases})
    assert process.returncode == 2
    assert "has no acore_characters" in process.stderr
    assert list(out.iterdir()) == []


def test_a_ledger_that_cannot_be_read_is_refused(tmp_path: Path) -> None:
    updates = {"acore_auth": ["2024_01_a", "2024_01_b"], "acore_characters": []}
    process, _server, out, _log = _run(tmp_path, fixture={"updates": updates})
    assert process.returncode == 2
    assert "database version of acore_characters could not be read" in process.stderr
    assert list(out.iterdir()) == []


def test_docker_that_does_not_answer_is_said_so(tmp_path: Path) -> None:
    process, _server, out, _log = _run(tmp_path, fixture={"daemon": False})
    assert process.returncode == 2
    assert "not answering" in process.stderr
    assert list(out.iterdir()) == []


def test_a_dump_that_names_another_database_is_refused(tmp_path: Path) -> None:
    process, _server, out, _log = _run(tmp_path, fixture={"wrong_name": ["acore_characters"]})
    assert process.returncode == 2
    assert "does not name acore_characters" in process.stderr
    assert list(out.iterdir()) == []


def test_the_written_package_is_read_back_before_it_takes_its_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _script()
    root = tmp_path / "readback"
    root.mkdir(parents=True)
    server = root / "server"
    server.mkdir()
    (server / ".env").write_text(f"MYSQL_ROOT_PASSWORD={PASSWORD}\n", encoding="utf-8")
    out = root / "out"
    out.mkdir()
    fixture = root / "fixture.json"
    fixture.write_text(json.dumps(FIXTURE), encoding="utf-8")
    monkeypatch.setenv("PATH", str(_write_fake_tools(root, ("docker",))))
    monkeypatch.setenv("FAKE_DB_FIXTURE", str(fixture))
    monkeypatch.setenv("FAKE_DB_LOG", str(root / "fake.log"))

    class Fixed(datetime):
        @classmethod
        def now(cls, tz: object = None) -> Fixed:  # type: ignore[override]
            return cls(2026, 10, 12, 21, 0, 0)

    monkeypatch.setattr(script, "datetime", Fixed)
    real_read_back = script._read_back
    seen: list[tuple[str, bool]] = []

    def spy(partial: Path, manifest: str) -> None:
        seen.append((partial.name, partial.exists()))
        real_read_back(partial, manifest)

    monkeypatch.setattr(script, "_read_back", spy)
    dest, _counts = script.pack(server, out, False)
    assert seen == [(dest.name + ".partial", True)]
    assert dest.exists()
    assert not (out / (dest.name + ".partial")).exists()


# ============================================================ round 2: review fixes


def _prepare(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    fixture: Mapping[str, Any] | None = None,
    env_text: str | None = None,
) -> tuple[Path, Path]:
    """A server folder and an out folder for calls to the script's functions, the fake on PATH."""
    root = tmp_path / "prepared"
    root.mkdir(parents=True, exist_ok=True)
    server = root / "server"
    server.mkdir(exist_ok=True)
    text = env_text if env_text is not None else f"MYSQL_ROOT_PASSWORD={PASSWORD}\n"
    (server / ".env").write_text(text, encoding="utf-8")
    out = root / "out"
    out.mkdir(exist_ok=True)
    fixture_path = root / "fixture.json"
    fixture_path.write_text(json.dumps({**FIXTURE, **(fixture or {})}), encoding="utf-8")
    monkeypatch.setenv("PATH", str(_write_fake_tools(root, ("docker",))))
    monkeypatch.setenv("FAKE_DB_FIXTURE", str(fixture_path))
    monkeypatch.setenv("FAKE_DB_LOG", str(root / "fake.log"))
    return server, out


def test_a_failed_read_back_leaves_no_partial_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _script()
    server, out = _prepare(tmp_path, monkeypatch)

    def refuse(partial: Path, manifest: str) -> None:
        raise script.Refusal("The package did not read back as it was written. Nothing was packed.")

    monkeypatch.setattr(script, "_read_back", refuse)
    code = script.main(["--server-dir", str(server), "--out", str(out)])
    assert code == 2
    assert "did not read back" in capsys.readouterr().err
    assert list(out.iterdir()) == []


@pytest.mark.parametrize(
    ("error", "sentence"),
    [(errno.ENOSPC, "not enough free space"), (errno.EIO, "could not be written")],
)
def test_a_write_error_leaves_no_partial_and_says_so(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: int,
    sentence: str,
) -> None:
    script = _script()
    server, out = _prepare(tmp_path, monkeypatch)

    def fail(*args: object, **kwargs: object) -> int:
        raise OSError(error, os.strerror(error))

    monkeypatch.setattr(script.shutil, "copyfileobj", fail)
    code = script.main(["--server-dir", str(server), "--out", str(out)])
    err = capsys.readouterr().err
    assert code == 2
    assert sentence in err and "Nothing was packed" in err
    assert list(out.iterdir()) == []


def test_the_dump_flags_are_the_ones_yulon_dumps_with(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _script()
    monkeypatch.setattr(
        maintenance,
        "mysql_client",
        lambda db_container, tool="mysql", *, client=None: tool,
    )
    dumper = maintenance.DockerMysql.__new__(maintenance.DockerMysql)
    object.__setattr__(dumper, "db_container", "ac-database")
    object.__setattr__(dumper, "client", "mysql")
    argv = dumper._dump_argv("acore_auth", ("realmlist",))
    flags = tuple(
        a for a in argv[2 : argv.index("--databases")] if not a.startswith("--ignore-table=")
    )
    assert argv[1] == "-uroot"
    assert flags == script.DUMP_FLAGS


def test_host_mode_talks_utf8mb4_on_the_port_it_is_given(tmp_path: Path) -> None:
    process, _server, _out, log = _run(
        tmp_path,
        tools=("mysql", "mysqldump"),
        args=("--server-stopped", "--port", "3307"),
    )
    assert process.returncode == 0, process.stderr
    entries = _entries(log)
    assert entries
    for entry in entries:
        assert "--default-character-set=utf8mb4" in entry["argv"], entry
        assert entry["argv"][entry["argv"].index("-P") + 1] == "3307", entry


def test_a_commented_password_line_is_skipped(tmp_path: Path) -> None:
    env = f"# MYSQL_ROOT_PASSWORD=old-value\nMYSQL_ROOT_PASSWORD={PASSWORD}\n"
    process, _server, _out, _log = _run(tmp_path, env_text=env)
    assert process.returncode == 0, process.stderr


def test_a_quoted_password_keeps_its_spaces_and_hash(tmp_path: Path) -> None:
    secret = "pw # two words"
    process, _server, _out, _log = _run(
        tmp_path,
        env_text=f'MYSQL_ROOT_PASSWORD="{secret}"\n',
        fixture={"password": secret},
    )
    assert process.returncode == 0, process.stderr


def test_an_unquoted_password_ends_at_a_comment(tmp_path: Path) -> None:
    env = f"MYSQL_ROOT_PASSWORD={PASSWORD}   # the root password\n"
    process, _server, _out, _log = _run(tmp_path, env_text=env)
    assert process.returncode == 0, process.stderr


STOCK_COMPOSE = (
    "services:\n  ac-database:\n    environment:\n"
    "      MYSQL_ROOT_PASSWORD: ${DOCKER_DB_ROOT_PASSWORD:-password}\n"
)


def test_the_stock_compose_default_is_used_when_nothing_sets_the_variable(
    tmp_path: Path,
) -> None:
    process, _server, _out, _log = _run(
        tmp_path,
        env_text="",
        compose={"docker-compose.yml": STOCK_COMPOSE},
        fixture={"password": "password"},
    )
    assert process.returncode == 0, process.stderr


def test_the_docker_db_root_password_from_the_env_file_is_used(tmp_path: Path) -> None:
    process, _server, _out, _log = _run(
        tmp_path,
        env_text=f"DOCKER_DB_ROOT_PASSWORD={PASSWORD}\n",
        compose={"docker-compose.yml": STOCK_COMPOSE},
    )
    assert process.returncode == 0, process.stderr


def test_a_docker_db_root_password_in_the_env_file_alone_is_used(
    tmp_path: Path,
) -> None:
    process, _server, _out, _log = _run(tmp_path, env_text=f"DOCKER_DB_ROOT_PASSWORD={PASSWORD}\n")
    assert process.returncode == 0, process.stderr


def test_a_refused_password_is_asked_for_once_and_then_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _script()
    server, out = _prepare(
        tmp_path,
        monkeypatch,
        env_text="MYSQL_ROOT_PASSWORD=wrong-pw\n",
        fixture={"password": "typed-pw"},
    )
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    asked: list[str] = []

    def typed(prompt: str = "") -> str:
        asked.append(prompt)
        return "typed-pw"

    monkeypatch.setattr(script, "getpass", types.SimpleNamespace(getpass=typed))
    dest, _counts = script.pack(server, out, False)
    assert len(asked) == 1
    assert dest.exists()


def test_a_typed_password_that_is_also_refused_is_not_asked_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _script()
    server, out = _prepare(
        tmp_path,
        monkeypatch,
        env_text="MYSQL_ROOT_PASSWORD=wrong-pw\n",
        fixture={"password": "right-pw"},
    )
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    asked: list[str] = []

    def typed(prompt: str = "") -> str:
        asked.append(prompt)
        return "also-wrong"

    monkeypatch.setattr(script, "getpass", types.SimpleNamespace(getpass=typed))
    with pytest.raises(script.Refusal, match="refused the password"):
        script.pack(server, out, False)
    assert len(asked) == 1
    assert list(out.iterdir()) == []


def test_a_server_of_another_game_is_named_by_its_containers(tmp_path: Path) -> None:
    process, _server, out, _log = _run(tmp_path, fixture={"containers": {"tbc-db": True}})
    assert process.returncode == 2
    assert "WoW TBC, not WoW WotLK" in process.stderr
    assert list(out.iterdir()) == []


def test_a_server_of_another_game_is_named_by_its_compose_file(tmp_path: Path) -> None:
    compose = {"docker-compose.yml": "services:\n  db:\n    container_name: centurion-db\n"}
    process, _server, out, _log = _run(tmp_path, tools=(), compose=compose)
    assert process.returncode == 2
    assert "Centurion, not WoW WotLK" in process.stderr
    assert list(out.iterdir()) == []


def test_the_other_games_the_script_names_match_the_catalog() -> None:
    script = _script()
    catalog = load_catalog()
    assert {game_id for game_id, _name, _db in script.OTHER_GAMES} == {
        "wow-tbc",
        "wow-vanilla",
        "wow-tortoise",
        "wow-centurion",
        "wow-unbound",
    }
    for game_id, name, database in script.OTHER_GAMES:
        entry = catalog.get(game_id)
        assert entry.name == name
        assert entry.container_spec().db == database


def test_a_port_out_of_range_is_refused(tmp_path: Path) -> None:
    process, _server, out, _log = _run(
        tmp_path, tools=("mysql", "mysqldump"), args=("--server-stopped", "--port", "70000")
    )
    assert process.returncode == 2
    assert "between 1 and 65535" in process.stderr
    assert list(out.iterdir()) == []


def test_an_io_error_while_dumping_is_said_in_plain_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _script()
    server, out = _prepare(tmp_path, monkeypatch)

    def full(*args: object, **kwargs: object) -> Path:
        raise OSError(errno.EIO, os.strerror(errno.EIO))

    monkeypatch.setattr(script, "_dump", full)
    code = script.main(["--server-dir", str(server), "--out", str(out)])
    err = capsys.readouterr().err
    assert code == 2
    assert "could not finish writing the package" in err and "Nothing was packed" in err
    assert list(out.iterdir()) == []


# ================================================================ round 2 (cold review)


def test_a_password_with_equals_signs_is_read_whole(tmp_path: Path) -> None:
    secret = "ab=cd==e"
    process, _server, _out, _log = _run(
        tmp_path, env_text=f"MYSQL_ROOT_PASSWORD={secret}\n", fixture={"password": secret}
    )
    assert process.returncode == 0, process.stderr


def test_a_single_quoted_dollar_is_literal_and_a_double_dollar_is_one_dollar(
    tmp_path: Path,
) -> None:
    for text, secret in (
        ("MYSQL_ROOT_PASSWORD='pa$word'\n", "pa$word"),
        ("MYSQL_ROOT_PASSWORD=pa$$word\n", "pa$word"),
    ):
        process, _server, _out, _log = _run(
            tmp_path / secret.replace("$", "S") / str(len(text)),
            env_text=text,
            fixture={"password": secret},
        )
        assert process.returncode == 0, (text, process.stderr)


def test_no_password_in_the_folder_is_asked_for_when_there_is_a_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _script()
    server, out = _prepare(tmp_path, monkeypatch, env_text="", fixture={"password": "typed-pw"})
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    asked: list[str] = []

    def typed(prompt: str = "") -> str:
        asked.append(prompt)
        return "typed-pw"

    monkeypatch.setattr(script, "getpass", types.SimpleNamespace(getpass=typed))
    dest, _counts = script.pack(server, out, False)
    assert len(asked) == 1
    assert dest.exists()


def test_without_a_server_folder_the_password_is_asked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _script()
    _server, out = _prepare(tmp_path, monkeypatch, fixture={"password": "typed-pw"})
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(
        script, "getpass", types.SimpleNamespace(getpass=lambda prompt="": "typed-pw")
    )
    dest, _counts = script.pack(None, out, False)
    assert dest.exists()


@pytest.mark.parametrize("failure", [RuntimeError("boom"), KeyboardInterrupt()])
def test_any_failure_while_the_package_is_written_removes_the_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    script = _script()
    dump = tmp_path / "acore_auth.sql"
    dump.write_bytes(b"-- dump\n")
    dest = tmp_path / "package.zip"

    def explode(partial: Path, manifest: str) -> None:
        assert partial.exists()
        raise failure

    monkeypatch.setattr(script, "_read_back", explode)
    with pytest.raises(type(failure)):
        script._write_package(dest, "{}", [("acore_auth", dump)])
    assert sorted(p.name for p in tmp_path.iterdir()) == ["acore_auth.sql"]


def test_a_failure_that_is_not_an_oserror_during_the_copy_removes_the_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _script()
    dump = tmp_path / "acore_auth.sql"
    dump.write_bytes(b"-- dump\n")

    def explode(*args: object, **kwargs: object) -> int:
        raise ValueError("bad")

    monkeypatch.setattr(script.shutil, "copyfileobj", explode)
    with pytest.raises(ValueError):
        script._write_package(tmp_path / "package.zip", "{}", [("acore_auth", dump)])
    assert sorted(p.name for p in tmp_path.iterdir()) == ["acore_auth.sql"]


def test_a_full_disk_is_said_so_whichever_step_hit_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _script()
    server, out = _prepare(tmp_path, monkeypatch)

    def full(*args: object, **kwargs: object) -> Path:
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

    monkeypatch.setattr(script, "_dump", full)
    assert script.main(["--server-dir", str(server), "--out", str(out)]) == 2
    err = capsys.readouterr().err
    assert "not enough free space" in err and "--out" in err and "Nothing was packed" in err


def test_a_dump_that_stops_on_a_full_disk_is_said_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _script()
    server, out = _prepare(tmp_path, monkeypatch, fixture={"dump_error": "Got errno 28 on write"})
    assert script.main(["--server-dir", str(server), "--out", str(out)]) == 2
    err = capsys.readouterr().err
    assert "not enough free space" in err and "Nothing was packed" in err
    assert list(out.iterdir()) == []


def test_the_readme_says_how_to_fetch_the_script_and_which_python_it_needs() -> None:
    readme = (SCRIPT.parent / "README.md").read_text(encoding="utf-8")
    section = readme[readme.index("export-accounts-and-characters-wsl.py") :]
    assert "curl" in section and SCRIPT.name in section.split("curl", 1)[1].splitlines()[0]
    assert "Python 3.10" in section


# ================================================================ round 3 (cold review)


@pytest.mark.parametrize(
    ("env_line", "secret"),
    [
        ("DOCKER_DB_ROOT_PASSWORD='pa$word'", "pa$word"),
        ("DOCKER_DB_ROOT_PASSWORD=pa$$word", "pa$word"),
        ('DOCKER_DB_ROOT_PASSWORD="a\\"b"', 'a"b'),
        ('DOCKER_DB_ROOT_PASSWORD="a\\$b"', "a$b"),
        ("DOCKER_DB_ROOT_PASSWORD=a#b", "a#b"),
        ("DOCKER_DB_ROOT_PASSWORD=a b # note", "a b"),
        ("DOCKER_DB_ROOT_PASSWORD=${BASE:-x}1\nBASE=ignored-later", "x1"),
    ],
)
def test_the_stock_compose_layout_gets_the_env_value_in_its_final_form(
    tmp_path: Path, env_line: str, secret: str
) -> None:
    process, _server, _out, _log = _run(
        tmp_path,
        env_text=env_line + "\n",
        compose={"docker-compose.yml": STOCK_COMPOSE},
        fixture={"password": secret},
    )
    assert process.returncode == 0, (env_line, process.stderr)


def test_a_dash_default_applies_only_when_the_variable_is_unset(tmp_path: Path) -> None:
    compose = STOCK_COMPOSE.replace(":-password", "-password")
    unset, _s, _o, _l = _run(
        tmp_path / "unset",
        env_text="",
        compose={"docker-compose.yml": compose},
        fixture={"password": "password"},
    )
    assert unset.returncode == 0, unset.stderr
    empty, _s, out, _l = _run(
        tmp_path / "empty",
        env_text="DOCKER_DB_ROOT_PASSWORD=\n",
        compose={"docker-compose.yml": compose},
        fixture={"password": "password"},
    )
    assert empty.returncode == 2 and "password" in empty.stderr.lower()
    assert list(out.iterdir()) == []


def test_a_running_server_is_told_to_stop_only_the_game_servers(tmp_path: Path) -> None:
    process, _server, _out, _log = _run(
        tmp_path,
        fixture={
            "containers": {"ac-database": True, "ac-authserver": True, "ac-worldserver": False}
        },
    )
    assert process.returncode == 2
    assert "docker compose stop ac-worldserver ac-authserver" in process.stderr
    assert "leave the database running" in process.stderr


def test_a_stopped_database_is_told_to_start_only_the_database(tmp_path: Path) -> None:
    process, _server, _out, _log = _run(
        tmp_path,
        fixture={
            "containers": {"ac-database": False, "ac-authserver": False, "ac-worldserver": False}
        },
    )
    assert process.returncode == 2
    assert "docker compose start ac-database" in process.stderr
    assert "world and auth servers stopped" in process.stderr


def test_the_readme_says_to_stop_only_the_game_servers() -> None:
    readme = (SCRIPT.parent / "README.md").read_text(encoding="utf-8")
    section = readme[readme.index("export-accounts-and-characters-wsl.py") :]
    assert "docker compose stop ac-worldserver ac-authserver" in section
    assert "database running" in section


def test_host_mode_names_another_game_by_its_compose_file(tmp_path: Path) -> None:
    compose = {"docker-compose.yml": "services:\n  db:\n    container_name: tbc-db\n"}
    process, _server, out, _log = _run(
        tmp_path, tools=("mysql", "mysqldump"), args=("--server-stopped",), compose=compose
    )
    assert process.returncode == 2
    assert "WoW TBC, not WoW WotLK" in process.stderr
    assert list(out.iterdir()) == []


@pytest.mark.parametrize("signal_name", ["SIGTERM", "SIGHUP"])
def test_a_closed_window_or_a_kill_mid_dump_leaves_nothing_behind(
    tmp_path: Path, signal_name: str
) -> None:
    import signal as signal_module
    import time

    root = tmp_path / "sig"
    root.mkdir()
    server = root / "server"
    server.mkdir()
    (server / ".env").write_text(f"MYSQL_ROOT_PASSWORD={PASSWORD}\n", encoding="utf-8")
    out = root / "out"
    out.mkdir()
    fixture_path = root / "fixture.json"
    fixture_path.write_text(json.dumps({**FIXTURE, "hang_dump": True}), encoding="utf-8")
    log = root / "fake.log"
    env = {
        "PATH": str(_write_fake_tools(root, ("docker",))),
        "HOME": str(root / "home"),
        "LANG": "C.UTF-8",
        "FAKE_DB_FIXTURE": str(fixture_path),
        "FAKE_DB_LOG": str(log),
    }
    proc = subprocess.Popen(
        [sys.executable, str(SCRIPT), "--server-dir", str(server), "--out", str(out)],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    marker = Path(str(log) + ".hanging")
    try:
        for _ in range(300):
            if marker.exists() or proc.poll() is not None:
                break
            time.sleep(0.1)
        assert marker.exists(), proc.communicate()
        assert any(out.iterdir()), "the work folder should exist mid-dump"
        proc.send_signal(getattr(signal_module, signal_name))
        _stdout, stderr = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
    assert proc.returncode != 0
    assert list(out.iterdir()) == [], stderr


def test_stale_leftovers_of_an_earlier_run_are_swept_and_live_ones_are_kept(
    tmp_path: Path,
) -> None:
    script = _script()
    out = tmp_path / "out"
    out.mkdir()
    old = datetime(2026, 1, 1).timestamp()
    dead_work = out / ".yulon-move-work-999999999-abc"
    dead_work.mkdir()
    (dead_work / "acore_auth.sql").write_text("verifiers", encoding="utf-8")
    live_work = out / f".yulon-move-work-{os.getpid()}-xyz"
    live_work.mkdir()
    stale_partial = out / "yulon-move-wow-wotlk-20260101-0000-keep-private.zip.partial"
    stale_partial.write_bytes(b"x")
    os.utime(stale_partial, (old, old))
    fresh_partial = out / "yulon-move-wow-wotlk-20260101-0001-keep-private.zip.partial"
    fresh_partial.write_bytes(b"x")
    other = out / "notes.partial"
    other.write_bytes(b"x")
    os.utime(other, (old, old))
    script._sweep_leftovers(out, datetime.now().timestamp())
    assert not dead_work.exists() and not stale_partial.exists()
    assert live_work.exists() and fresh_partial.exists() and other.exists()


def test_a_python_older_than_the_minimum_gets_a_plain_sentence(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _script()
    monkeypatch.setattr(script.sys, "version_info", (3, 9, 18, "final", 0))
    assert script.main(["--help"]) == 2
    err = capsys.readouterr().err
    assert "Python 3.10 or newer" in err and "3.9" in err


def test_the_readme_names_the_python_the_script_needs() -> None:
    readme = (SCRIPT.parent / "README.md").read_text(encoding="utf-8")
    section = readme[readme.index("export-accounts-and-characters-wsl.py") :]
    assert "Python 3.10 or newer" in section and "3.11" not in section.split("curl")[0]


def test_a_run_sweeps_the_dump_folder_a_killed_run_left_in_the_output_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _script()
    server, out = _prepare(tmp_path, monkeypatch)
    dead = out / ".yulon-move-work-999999999-abc"
    dead.mkdir()
    (dead / "acore_auth.sql").write_text("verifiers", encoding="utf-8")
    dest, _counts = script.pack(server, out, False)
    assert dest.exists() and not dead.exists()
