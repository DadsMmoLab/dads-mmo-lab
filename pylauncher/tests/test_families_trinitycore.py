"""Tests for the TrinityCore family engine (`yulon.catalog.families.trinitycore`, T179 Task 3).

The machine is `tests/support_native.py`'s `Recorder`, with two doubles of this
file's own: `FakeMysql`, a database that remembers which schemas, tables and
marker rows exist, so the REAL `MarkerGate` decides the import's branch (Review
Focus 2 is about that gate's reading of a half import, which a canned probe
answer could not show); and `Extractors`, the `docker run` of the three map
tools, which records what the client mount held at the moment each tool ran and
lays map files under the names the world server's start check opens. The entry
is `support_trinitycore.centurion_like()`, with client packs whose zips are
built here, so their checksums are real.

Nothing here proves a Centurion server installs: that is the live proof (Task 9).
It proves the stage order, what each stage hands the machine, and the two Review
Focus properties pinned in this task: the extraction client never holds an
optional pack's file (1), and the import's marker is written only after the last
phase, a half import being cleared and run again from the start (2).
"""

from __future__ import annotations

import errno
import hashlib
import io
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import threading
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

import pytest

from tests.support_case import CASE_SENSITIVE_DISK, needs_case_sensitive_disk
from tests.support_native import Recorder
from tests.support_trinitycore import (
    AUTH,
    CHARS,
    CHECKOUT,
    CORE_DIR,
    SQL_DIR,
    WORLD,
    FakeMmapsDocker,
    centurion_like,
)
from tests.test_purge import Recorder as PurgeRecorder
from yulon import (
    client_packs,
    docker,
    platform,
    play_client,
    purge,
    resources,
    rmtree,
    server_build_presses,
)
from yulon.catalog import native
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.catalog.families import FAMILIES, extract, family_for, trinitycore
from yulon.catalog.families.cmangos import CmangosInstaller
from yulon.catalog.families.trinitycore import (
    EXTRACT_CLIENT_RECORD,
    TrinityCoreInstaller,
    extraction_client_dir,
)
from yulon.catalog.installer import InstallerError, InstallOptions

REV = "faac5fc9b0fe0934c26f08231793ca607d38327d"
PATCHES = f"{CHECKOUT}/centurion/patches"


def _zip(member: str, body: bytes) -> bytes:
    """A pack zip with one member and a fixed date, so its checksum is the same every run."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(zipfile.ZipInfo(member, date_time=(2026, 9, 26, 0, 0, 0)), body)
    return buffer.getvalue()


PATCH_X = b"MPQ\x1a centurion world (patch-X from patch-Y.zip)"
PATCH_A = b"MPQ\x1a centurion locale (patch-enUS-A)"
ZIPS = {
    f"{PATCHES}/patch-Y.zip": _zip("patch-X.MPQ", PATCH_X),
    f"{PATCHES}/patch-enUS-A.zip": _zip("patch-enUS-A.MPQ", PATCH_A),
    f"{PATCHES}/addons.zip": _zip("CenturionUI/CenturionUI.toc", b"## Title: CenturionUI"),
    f"{PATCHES}/client-tweaks.zip": _zip("dinput8.dll", b"MZ client tweaks"),
}

REQUIRED_PACKS: list[dict[str, Any]] = [
    {
        "id": "world",
        "label": "Centurion world",
        "source": {"kind": "checkout", "path": f"{PATCHES}/patch-Y.zip"},
        "md5": hashlib.md5(ZIPS[f"{PATCHES}/patch-Y.zip"], usedforsecurity=False).hexdigest(),
        "install": [{"member": "patch-X.MPQ", "to": "Data/patch-X.MPQ"}],
    },
    {
        "id": "locale",
        "label": "Centurion locale",
        "source": {"kind": "checkout", "path": f"{PATCHES}/patch-enUS-A.zip"},
        "md5": hashlib.md5(ZIPS[f"{PATCHES}/patch-enUS-A.zip"], usedforsecurity=False).hexdigest(),
        "install": [{"member": "patch-enUS-A.MPQ", "to": "Data/enUS/patch-enUS-A.MPQ"}],
    },
]

NON_MAP_PACKS: list[dict[str, Any]] = [
    # Required, from the checkout, and laying nothing under `Data/`: Centurion's addons
    # and its dinput8.dll. No input of the map data (T179 final round).
    {
        "id": "addons",
        "label": "Centurion's addons",
        "source": {"kind": "checkout", "path": f"{PATCHES}/addons.zip"},
        "md5": hashlib.md5(ZIPS[f"{PATCHES}/addons.zip"], usedforsecurity=False).hexdigest(),
        "install": [{"member": "*", "to_dir": "Interface/AddOns"}],
    },
    {
        "id": "client-tweaks",
        "label": "Client tweaks",
        "source": {"kind": "checkout", "path": f"{PATCHES}/client-tweaks.zip"},
        "md5": hashlib.md5(ZIPS[f"{PATCHES}/client-tweaks.zip"], usedforsecurity=False).hexdigest(),
        "install": [{"member": "dinput8.dll", "to": "dinput8.dll"}],
    },
]

OPTIONAL_PACKS: list[dict[str, Any]] = [
    {
        "id": "hd-characters",
        "label": "HD characters",
        "source": {
            "kind": "url",
            "url": "https://centurionpvp.example/hd/patch-F.zip",
            "version_url": "https://centurionpvp.example/hd/patch-F.version",
        },
        "install": [{"member": "patch-F.MPQ", "to": "Data/patch-F.MPQ"}],
        "optional": True,
        "default": True,
    },
    {
        "id": "alt-world",
        "label": "Alternative world terrain",
        "source": {
            "kind": "url",
            "url": "https://centurionpvp.example/hd/alt.zip",
            "version_url": "https://centurionpvp.example/hd/alt.version",
        },
        "install": [{"member": "patch-T.MPQ", "to": "Data/patch-T.MPQ"}],
        "remove_when_off": ["Data/patch-Y.MPQ"],
        "optional": True,
    },
]

ENTRY = centurion_like(packs=[*REQUIRED_PACKS, *OPTIONAL_PACKS], rev=REV)
TC = ENTRY.install.native.trinitycore if ENTRY.install.native is not None else None
assert TC is not None
DB_PASSWORD = "tc-0123456789abcdef"

SERVER_DBC = {"Spell.dbc": b"the server's Spell.dbc", "LiquidType.dbc": b"the server's LiquidType"}

AUTOBALANCE_CONF = (
    b"[worldserver]\r\n# The live realm's AutoBalance settings (centurion/conf/)\r\n"
    b"AutoBalance.Enabled = 1\r\n"
)
"""The checkout's `AutoBalance.conf`, CRLF so a copy that translated line endings would show."""

SQL_FILES: dict[str, str] = {
    "auth/auth_schema.sql": (
        "CREATE TABLE realmlist (id INT);\n"
        "CREATE TRIGGER trg_realmlist_au AFTER UPDATE ON realmlist FOR EACH ROW "
        "INSERT INTO legionnaireauth.audit_dml_log VALUES ('centurionworld');\n"
    ),
    "auth/auth_data.sql": "-- dumped from legionnaireauth, which this file must keep saying\n",
    "auth/auth_bots.sql": "INSERT INTO account VALUES (76, 'PLAYERBOTONE');\n",
    "characters/characters_schema.sql": (
        "CREATE PROCEDURE createTournamentKit() SELECT * FROM centurionworld.x;\n"
    ),
    "characters/characters_seed.sql": "INSERT INTO characters VALUES (1);\n",
    "characters/characters_bots.sql": "INSERT INTO characters VALUES (100955);\n",
    "world/_routines.sql": "CREATE PROCEDURE p() SELECT * FROM legionnaireauth.account;\n",
    "world/creature.sql": "DROP TABLE IF EXISTS creature;\n",
    "world/version.sql": "DROP TABLE IF EXISTS version;\n",
    "world/broadcast_text_locale.1.sql": "DROP TABLE IF EXISTS broadcast_text_locale;\n",
}

WORLD_CONF_DIST = (
    "[worldserver]\n"
    "RealmID = 1\n"
    'DataDir = "."\n'
    'LogsDir = ""\n'
    'LoginDatabaseInfo     = "127.0.0.1;3306;trinity;trinity;auth"\n'
    'WorldDatabaseInfo     = "127.0.0.1;3306;trinity;trinity;world"\n'
    'CharacterDatabaseInfo = "127.0.0.1;3306;trinity;trinity;characters"\n'
    "WorldServerPort = 8085\n"
    "Updates.EnableDatabases = 7\n"
    "Console.Enable = 1\n"
    "SOAP.Enabled = 0\n"
    'SOAP.IP = "127.0.0.1"\n'
    "SOAP.Port = 7878\n"
    "mmap.enablePathFinding = 1\n"
)
AUTH_CONF_DIST = (
    '[authserver]\nLogsDir = ""\nLoginDatabaseInfo = "127.0.0.1;3306;trinity;trinity;auth"\n'
)
PLAYERBOTS_CONF_DIST = "Playerbot.Enable = 0\n"


# -- the database ------------------------------------------------------------------


@dataclass
class FakeMysql:
    """A MySQL that remembers schemas, tables and marker rows -- enough for the real gate.

    `CREATE DATABASE` / `DROP DATABASE` / `CREATE TABLE IF NOT EXISTS a.b` and the
    marker's `INSERT` change what it holds; the probe's questions (`SHOW DATABASES`,
    a table's existence, the newest marker) are answered from that. `fail_on` makes
    every stream containing it exit 1 with a client-shaped stderr, as a trigger the
    importer may not create does (the MySQL proof's ERROR 1419).
    """

    databases: set[str] = field(default_factory=set)
    tables: set[tuple[str, str]] = field(default_factory=set)
    markers: dict[str, list[str]] = field(default_factory=dict)
    fail_on: str = ""
    streams: list[tuple[tuple[str, ...], str]] = field(default_factory=list)
    realm_row: str = "127.0.0.1\t127.0.0.1\n"

    def exec_stdin(
        self,
        container: str,
        argv: Sequence[str],
        source: BinaryIO,
        *,
        env: Mapping[str, str],
        wsl_distro: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        text = source.read().decode("utf-8")
        self.streams.append((tuple(argv), text))
        assert env == {"MYSQL_PWD": DB_PASSWORD}, "the password travels in the environment"
        if self.fail_on and self.fail_on in text:
            return subprocess.CompletedProcess(
                list(argv), 1, "", "ERROR 1419 (HY000) at line 80: You do not have the SUPER"
            )
        for name in re.findall(r"CREATE DATABASE IF NOT EXISTS `([^`]+)`", text):
            self.databases.add(name)
        for name in re.findall(r"DROP DATABASE IF EXISTS `([^`]+)`", text):
            self.databases.discard(name)
            self.tables = {table for table in self.tables if table[0] != name}
            self.markers.pop(name, None)
        for schema, table in re.findall(r"CREATE TABLE IF NOT EXISTS `([^`]+)`\.`([^`]+)`", text):
            self.tables.add((schema, table))
        for schema, plan_hash in re.findall(
            r"INSERT INTO `([^`]+)`\.`yulon_install` \(plan_hash, finished_unix\) "
            r"VALUES \('([0-9a-f]+)'",
            text,
        ):
            self.markers.setdefault(schema, []).append(plan_hash)
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    def sql_query(
        self,
        container: str,
        client: str,
        password: str,
        schema: str | None,
        statement: str,
        *,
        wsl_distro: str | None = None,
    ) -> str:
        if statement == "SHOW DATABASES":
            return "".join(f"{name}\n" for name in sorted(self.databases))
        exists = re.search(r"table_schema='([^']+)' AND table_name='([^']+)'", statement)
        if exists:
            return "1\n" if (exists[1], exists[2]) in self.tables else "0\n"
        marker = re.search(r"SELECT plan_hash FROM `([^`]+)`", statement)
        if marker:
            rows = self.markers.get(marker[1], [])
            return f"{rows[-1]}\n" if rows else ""
        if statement.startswith("SELECT COUNT(*)"):
            return "1\n"
        if "realmlist" in statement:
            return self.realm_row
        raise AssertionError(f"FakeMysql was asked something it does not model: {statement}")

    def files(self) -> list[tuple[str | None, str]]:
        """`(schema, first line)` of every stream, in order: what landed where."""
        return [
            (argv[3] if len(argv) > 3 else None, text.splitlines()[0] if text else "")
            for argv, text in self.streams
        ]

    def marker_streams(self) -> list[int]:
        """The indexes of the streams that wrote a marker row."""
        return [
            index for index, (_, text) in enumerate(self.streams) if "`yulon_install` (" in text
        ]


# -- the extractors ------------------------------------------------------------------


MAP_NAMES = ("0003232.map", "0013232.map", "5303232.map")
VMAP_TREES = ("000.vmtree", "001.vmtree", "530.vmtree")


@dataclass
class Extractors:
    """The three map tools' `docker run`: what the client held, and the files they leave.

    `seen` maps each tool's program to the client mount's files at the moment it
    ran, relative and `/`-separated, with each file's bytes -- read DURING the run,
    because the copy is gone by the time a test could look. The maps tool lays the
    three start maps (unless `missing` names one) and an extracted `dbc/` the
    overlay must win over; the assembler lays the three `.vmtree` files.
    """

    rec: Recorder
    missing: tuple[str, ...] = ()
    fail_tool: str = ""
    seen: dict[str, dict[str, bytes]] = field(default_factory=dict)
    client_mounts: list[docker.Mount] = field(default_factory=list)

    def __call__(
        self,
        spec: docker.ContainerRun,
        *,
        sink: docker.OutputSink,
        cancel: threading.Event | None = None,
    ) -> docker.AttachedRun:
        program = spec.argv[0].rsplit("/", 1)[-1]
        client = next(mount for mount in spec.mounts if mount.guest == extract.CLIENT_MOUNT)
        self.client_mounts.append(client)
        self.seen[program] = {
            path.relative_to(client.host).as_posix(): path.read_bytes()
            for path in sorted(client.host.rglob("*"))
            if path.is_file()
        }
        if program == self.fail_tool:
            self.rec.container_runs.append(spec)
            return docker.AttachedRun(139, ("Segmentation fault",))
        run = self.rec.run_container(spec, sink=sink, cancel=cancel)
        out = next(mount.host for mount in spec.mounts if mount.guest == extract.OUT_MOUNT)
        if program == "mapextractor":
            for name in MAP_NAMES:
                if not any(name.startswith(f"{gone:03}") for gone in self.missing_ids()):
                    (out / "maps" / name).write_bytes(b"MAPS")
            (out / "dbc").mkdir(exist_ok=True)
            (out / "dbc" / "Spell.dbc").write_bytes(b"the client's Spell.dbc")
            (out / "dbc" / "Map.dbc").write_bytes(b"the client's Map.dbc")
        if program == "vmap4assembler":
            for name in VMAP_TREES:
                (out / "vmaps" / name).write_bytes(b"VMAP_4.8")
        return run

    def missing_ids(self) -> tuple[int, ...]:
        return tuple(int(name) for name in self.missing)


# -- the machine -----------------------------------------------------------------------


def player_client(tmp_path: Path) -> Path:
    """A 3.3.5a-shaped client folder that ALSO holds archives that are not stock.

    `Data/patch-F.MPQ` is the HD pack the player switched on for their own
    ready-to-play client, `Data/patch-Y.MPQ` the file the alternative-terrain pack
    deletes when off, and `Data/patch-4.MPQ` and `Data/enUS/Patch-enUS-5.MPQ` patches
    the player installed for some other server -- every one would be read into the
    map data if the copy kept it (Review Focus 1).
    """
    client = tmp_path / "World of Warcraft 3.3.5a"
    (client / "Data" / "enUS").mkdir(parents=True)
    for name in ("common", "common-2", "expansion", "lichking", "patch", "patch-2", "patch-3"):
        (client / "Data" / f"{name}.MPQ").write_bytes(f"MPQ {name}".encode())
    # `Patch-enUS` in another case than the catalog's `patch-{locale}.MPQ`: a stock name
    # spelled as some client zips spell it, which must be KEPT (fix round 2).
    for name in ("locale-enUS", "Patch-enUS"):
        (client / "Data" / "enUS" / f"{name}.MPQ").write_bytes(f"MPQ {name}".encode())
    (client / "Data" / "patch-F.MPQ").write_bytes(b"MPQ an HD pack the player switched on")
    (client / "Data" / "patch-Y.MPQ").write_bytes(b"MPQ the stock Alt-World terrain")
    (client / "Data" / "patch-4.MPQ").write_bytes(b"MPQ another server's patch")
    (client / "Data" / "enUS" / "Patch-enUS-5.MPQ").write_bytes(b"MPQ another server's locale")
    (client / "Wow.exe").write_bytes(b"MZ stock 12340")
    (client / "WTF").mkdir()
    (client / "WTF" / "Config.wtf").write_text('SET realmList "logon.example"\n')
    return client


def snapshot(folder: Path) -> dict[str, tuple[bytes, int, int]]:
    """Every file under `folder`: its bytes, its modification time and its mode."""
    found: dict[str, tuple[bytes, int, int]] = {}
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            stat = path.stat()
            found[path.relative_to(folder).as_posix()] = (
                path.read_bytes(),
                stat.st_mtime_ns,
                stat.st_mode,
            )
    return found


def lay_checkout(dest: Path) -> None:
    """What a clone of CENTURION leaves that the stages read: SQL, DBCs, AutoBalance.conf, zips."""
    for rel, text in SQL_FILES.items():
        path = dest / "centurion" / "sql" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for name, body in SERVER_DBC.items():
        path = dest / "centurion" / "dbc" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    autobalance = dest / "centurion" / "conf" / "AutoBalance.conf"
    autobalance.parent.mkdir(parents=True, exist_ok=True)
    autobalance.write_bytes(AUTOBALANCE_CONF)
    server_dir = dest.parents[1]
    for rel, body in ZIPS.items():
        path = server_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)


@dataclass
class Machine:
    rec: Recorder
    db: FakeMysql
    tools: Extractors
    server_dir: Path
    client: Path
    mmaps: FakeMmapsDocker = field(default_factory=FakeMmapsDocker)
    """The background movement-map job's Docker (Task 4): an install ends by starting it."""


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Machine:
    """A Recorder machine with this file's database and extractors, and a pack cache in tmp."""
    monkeypatch.setattr(client_packs, "cache_dir", lambda: tmp_path / "pack-cache")
    rec = Recorder()
    rec.produce = {"maps": 100, "Buildings": 100, "vmaps": 100}
    rec.conf_dist = {
        "worldserver.conf.dist": WORLD_CONF_DIST,
        "authserver.conf.dist": AUTH_CONF_DIST,
        "playerbots.conf.dist": PLAYERBOTS_CONF_DIST,
    }
    rec.world_output = native.WorldOutput(
        text="TrinityCore rev. faac5fc9\nWorld initialized in 42 seconds\n",
        restarts=0,
        status="running",
    )
    rec.on_clone = lay_checkout
    return Machine(
        rec=rec,
        db=FakeMysql(),
        tools=Extractors(rec),
        server_dir=tmp_path / "wow-centurion-server",
        client=player_client(tmp_path),
    )


def engine(m: Machine, *, entry: CatalogEntry = ENTRY, **overrides: object) -> TrinityCoreInstaller:
    return TrinityCoreInstaller(
        entry,
        installers_root=resources.installers_dir(),
        mmaps_runner=m.mmaps,
        seams=m.rec.seams(
            **{
                "platform_id": lambda: "linux",
                "exec_stdin": m.db.exec_stdin,
                "sql_query": m.db.sql_query,
                "run_container": m.tools,
                **overrides,
            }
        ),
    )


def install(m: Machine, *, entry: CatalogEntry = ENTRY, **overrides: object) -> list[str]:
    return list(
        engine(m, entry=entry, **overrides).run(
            InstallOptions(server_dir=m.server_dir, client_dir=m.client)
        )
    )


@pytest.fixture(autouse=True)
def known_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """The generated password, fixed, so the database double can check it travels in env."""
    monkeypatch.setattr(
        native.StagedInstaller,
        "resolve_secrets",
        lambda self, server_dir: native.Secrets(db_password=DB_PASSWORD),
    )


def context(m: Machine, *, completed: Sequence[str] = ()) -> native.StageContext:
    return native.StageContext(
        server_dir=m.server_dir,
        client_dir=m.client,
        state=native.InstallState(
            game_id=ENTRY.id, install_id="test", family="trinitycore", completed=tuple(completed)
        ),
        cancel=None,
        secrets=native.Secrets(db_password=DB_PASSWORD),
    )


def run_stage(
    m: Machine, name: str, *, entry: CatalogEntry = ENTRY, **overrides: object
) -> list[str]:
    """One stage body, called directly with a fresh context, through `_stream` and all."""
    eng = engine(m, entry=entry, **overrides)
    return list(eng.stage_named(name).run(context(m)))


def copy_dir(m: Machine) -> Path:
    """Where this machine's extraction client is made: beside the player's client."""
    return extraction_client_dir(m.client, m.server_dir)


def lay_for_client_data(m: Machine) -> None:
    """The checkout `client-data` reads, without running the stages before it."""
    lay_checkout(m.server_dir / CHECKOUT)


def clear_buildings_as_the_refusal_asks(m: Machine) -> None:
    """Delete `data/Buildings`, as the refusal of a second extraction over it tells the player.

    `vmap4extractor` will not start over the `dir_bin` a finished extraction
    leaves (T241, `extract.DIRTY_OUTPUT_TOOLS`), so an Install press whose
    evidence names another client or pack set stops before any container and
    names that folder. `reextract()` sets it aside instead.
    """
    shutil.rmtree(m.server_dir / "data" / extract.BUILDINGS_DIR)


# -- identity -----------------------------------------------------------------------------


STAGES = (
    "clone-sources",
    "db-password",
    "write-dockerfile",
    "generate-compose",
    "build",
    "client-data",
    "conf",
    "start-db",
    "import",
    "up",
    "ready",
)


def test_the_trinitycore_build_stage_says_this_platforms_cancel_note(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On Windows (T246) and Linux (T298) a Stop ends the build at once; on macOS Docker
    finishes the step."""
    monkeypatch.setattr(native.sys, "platform", "win32")
    notes = {s.name: s.cancel_note for s in engine(machine).stages()}
    assert notes["build"] == native.BUILD_CANCEL_NOTE_ENDS
    monkeypatch.setattr(native.sys, "platform", "darwin")
    notes = {s.name: s.cancel_note for s in engine(machine).stages()}
    assert notes["build"] == native.BUILD_CANCEL_NOTE


def test_the_stage_tuple_is_the_specs_and_the_names_agree(machine: Machine) -> None:
    """T179 spec §1 steps 1-9; mmaps (step 10) is a background job after ready (Task 4)."""
    assert TrinityCoreInstaller.STAGE_NAMES == STAGES
    assert engine(machine).stage_names() == STAGES
    eng = engine(machine)
    recorded = {stage.name: stage.recorded for stage in eng.stages()}
    assert [name for name, kept in recorded.items() if not kept] == [
        "db-password",
        "start-db",
        "up",
        "ready",
    ]
    assert eng.stage_named("client-data").cancel_note.endswith(
        "The temporary copy of your client is removed either way."
    )


def test_a_trinitycore_entry_dispatches_here_and_is_its_own_family() -> None:
    assert FAMILIES["trinitycore"] is TrinityCoreInstaller
    assert family_for(ENTRY) is TrinityCoreInstaller
    assert TrinityCoreInstaller.family == "trinitycore" != CmangosInstaller.family


def test_the_inherited_bodies_read_this_block_and_no_cmangos_patches(machine: Machine) -> None:
    """`_data()` is a view of the TrinityCore block: the same objects, and no source patches."""
    data = engine(machine)._data()
    assert data.sql is TC.sql and data.conf is TC.conf and data.extract is TC.extract
    assert data.client is TC.client and data.patches == ()


# -- a whole install ------------------------------------------------------------------------


def test_a_whole_install_runs_every_stage_in_order_and_ends_running(machine: Machine) -> None:
    said = install(machine)
    assert [line[4:] for line in said if line.startswith("--- ")] == list(STAGES)
    assert said[-1] == f"{ENTRY.name} is installed and running in {machine.server_dir}"
    assert not (copy_dir(machine)).exists()


def test_the_clone_is_sparse_on_the_named_branch_at_the_pin(machine: Machine) -> None:
    """`playerbot reference/` and `centurion/launcher/` never reach the disk (facts, repo-level)."""
    said = install(machine)
    (spec,) = machine.rec.clones
    assert spec.dest == machine.server_dir / CHECKOUT
    assert spec.sparse_exclude == ("playerbot reference", "centurion/launcher")
    assert spec.branch == "CENTURION", "the repository's default branch is an old master"
    assert spec.rev == REV
    assert spec.sparse_path is None
    assert (
        f"The {CHECKOUT} checkout leaves out playerbot reference, centurion/launcher: nothing "
        "in them is compiled." in said
    )


# -- client-data: the temporary extraction client (Review Focus 1) ----------------------------


def test_the_extraction_client_holds_the_required_packs_and_no_optional_pack_file(
    machine: Machine,
) -> None:
    """Required packs in, every optional pack's file out, whatever the player's client held."""
    lay_for_client_data(machine)
    run_stage(machine, "client-data")
    assert set(machine.tools.seen) == {"mapextractor", "vmap4extractor", "vmap4assembler"}
    for program, files in machine.tools.seen.items():
        assert files["Data/patch-X.MPQ"] == PATCH_X, program
        assert files["Data/enUS/patch-enUS-A.MPQ"] == PATCH_A, program
        for optional in ("Data/patch-F.MPQ", "Data/patch-Y.MPQ", "Data/patch-T.MPQ"):
            assert optional not in files, (program, optional)
        assert files["Data/lichking.MPQ"] == b"MPQ lichking", "the player's own archives are read"


STOCK = {
    *(f"Data/{name}.MPQ" for name in ("common", "common-2", "expansion", "lichking")),
    *(f"Data/{name}.MPQ" for name in ("patch", "patch-2", "patch-3")),
    "Data/enUS/locale-enUS.MPQ",
    # The player's `Patch-enUS.MPQ` is kept and reaches the extractors under the
    # catalog's `patch-enUS.MPQ`, the name map_extractor opens (T227). A disk that
    # ignores case reaches it by either name, so the copy keeps the player's.
    "Data/enUS/patch-enUS.MPQ" if CASE_SENSITIVE_DISK else "Data/enUS/Patch-enUS.MPQ",
}


def test_the_extraction_client_keeps_only_the_stock_archives_and_the_required_packs(
    machine: Machine,
) -> None:
    """Fix round 1, R3: an allow-list from the catalog, not a list of what to drop."""
    lay_for_client_data(machine)
    said = run_stage(machine, "client-data")
    for program, files in machine.tools.seen.items():
        archives = {
            rel for rel in files if rel.startswith("Data/") and rel.casefold().endswith(".mpq")
        }
        assert archives == STOCK | {"Data/patch-X.MPQ", "Data/enUS/patch-enUS-A.MPQ"}, program
    assert (
        "Left out of the copy, because this server's map data is made from the stock archives "
        "and its own packs only: Data/enUS/Patch-enUS-5.MPQ, Data/patch-4.MPQ, Data/patch-F.MPQ, "
        "Data/patch-Y.MPQ." in said
    )
    for rel in ("Data/patch-4.MPQ", "Data/patch-Y.MPQ", "Data/enUS/Patch-enUS-5.MPQ"):
        assert (machine.client / rel).is_file(), f"{rel} went from the player's own client"


def _lowercase_every_name_under_data(client: Path) -> None:
    """The player's client as some zips and copies from Windows leave it (T227)."""
    data = client / "Data"
    for folder, dirs, files in os.walk(data, topdown=False):
        for name in [*files, *dirs]:
            if name != name.lower():
                os.rename(Path(folder) / name, Path(folder) / name.lower())


@needs_case_sensitive_disk
def test_a_lowercase_client_reaches_the_extractors_under_the_names_they_open(
    machine: Machine,
) -> None:
    """T227: the extractors open `Data/lichking.MPQ` and `Data/enUS/locale-enUS.MPQ` exactly.

    On a case-sensitive disk a client named in lower case gave them nothing to
    open (map_extractor System.cpp:114-124, :1152-1218 spell every name). The copy
    is Yu'lon's, so its archives are renamed there to the catalog's spelling; the
    player's own client keeps its names.
    """
    _lowercase_every_name_under_data(machine.client)
    for rel in ("Data/lichking.mpq", "Data/enus/locale-enus.mpq"):  # T196/T198: flags kept
        (machine.client / rel).chmod(0o444)
    before = snapshot(machine.client)
    lay_for_client_data(machine)

    run_stage(machine, "client-data")

    for program, files in machine.tools.seen.items():
        archives = {
            rel for rel in files if rel.startswith("Data/") and rel.casefold().endswith(".mpq")
        }
        assert archives == STOCK | {"Data/patch-X.MPQ", "Data/enUS/patch-enUS-A.MPQ"}, program
        assert files["Data/lichking.MPQ"] == b"MPQ lichking", program
    assert snapshot(machine.client) == before, "the player's client was changed"


@needs_case_sensitive_disk
def test_a_client_whose_data_folder_is_lowercase_reaches_the_extractors_as_data(
    machine: Machine,
) -> None:
    """T261: `data/` itself, as `unzip -LL` leaves it. The extractors open `Data/...` only
    (`-i /client`, `-d /client/Data/`), so the copy's folder is renamed there too; the
    player's client keeps its names, bytes and read-only flags."""
    _lowercase_every_name_under_data(machine.client)
    (machine.client / "Data").rename(machine.client / "data")
    for rel in ("data/lichking.mpq", "data/enus/locale-enus.mpq"):  # T196/T198: flags kept
        (machine.client / rel).chmod(0o444)
    before = snapshot(machine.client)
    lay_for_client_data(machine)

    said = run_stage(machine, "client-data")

    for program, files in machine.tools.seen.items():
        archives = {
            rel for rel in files if rel.startswith("Data/") and rel.casefold().endswith(".mpq")
        }
        assert archives == STOCK | {"Data/patch-X.MPQ", "Data/enUS/patch-enUS-A.MPQ"}, program
        assert not any(rel.startswith("data/") for rel in files), program
    assert snapshot(machine.client) == before, "the player's client was changed"
    assert "Removed the temporary copy of your client." in said


@needs_case_sensitive_disk
def test_an_archive_already_under_the_catalog_s_name_is_never_replaced_by_its_case_twin(
    machine: Machine,
) -> None:
    """Two names that differ only in case are two files here; the catalog's one is read.

    A rename onto an existing name replaces it on POSIX, so the copy's own
    `lichking.MPQ` would silently become the bytes of the other file.
    """
    (machine.client / "Data" / "lichking.mpq").write_bytes(b"MPQ a case twin")
    lay_for_client_data(machine)

    run_stage(machine, "client-data")

    for program, files in machine.tools.seen.items():
        assert files["Data/lichking.MPQ"] == b"MPQ lichking", program


@needs_case_sensitive_disk
def test_a_lowercase_client_s_extraction_is_vouched_for_on_the_next_press(
    machine: Machine,
) -> None:
    """T227: the evidence measures `Data/lichking.mpq`, so a resume skips the hour again."""
    _lowercase_every_name_under_data(machine.client)
    lay_for_client_data(machine)
    run_stage(machine, "client-data")
    runs = len(machine.rec.container_runs)

    said = run_stage(machine, "client-data")

    assert len(machine.rec.container_runs) == runs, "the extractors ran a second time"
    assert said[0].startswith(f"The map data in {machine.server_dir / 'data'} was already made")


def test_packs_that_lay_nothing_under_data_are_no_input_of_the_map_data(machine: Machine) -> None:
    """T179 final round: addons and a dinput8.dll are not laid into the extraction copy."""
    with_extras = centurion_like(packs=[*REQUIRED_PACKS, *NON_MAP_PACKS, *OPTIONAL_PACKS], rev=REV)
    lay_for_client_data(machine)
    run_stage(machine, "client-data", entry=with_extras)
    for program, files in machine.tools.seen.items():
        assert "dinput8.dll" not in files, program
        assert not any(rel.startswith("Interface/AddOns/") for rel in files), program
    salted = extract.read_evidence(machine.server_dir / "data")
    made = engine(machine, entry=with_extras)
    assert [pack.id for pack in made._map_packs()] == ["world", "locale"]
    assert trinitycore._packs_salt(made._map_inputs(), machine.server_dir) == (
        trinitycore._packs_salt(engine(machine)._map_inputs(), machine.server_dir)
    ), "the same salt as without them: adding them asks for no new extraction"
    assert salted is not None


def test_a_changed_addons_or_tweaks_zip_never_flags_the_map_data(machine: Machine) -> None:
    with_extras = engine(
        machine, entry=centurion_like(packs=[*REQUIRED_PACKS, *NON_MAP_PACKS], rev=REV)
    )
    assert with_extras._is_map_data(f"{PATCHES}/patch-Y.zip")
    assert with_extras._is_map_data(f"{PATCHES}/patch-Y.zip.part03")
    assert not with_extras._is_map_data(f"{PATCHES}/addons.zip")
    assert not with_extras._is_map_data(f"{PATCHES}/client-tweaks.zip")
    assert not with_extras._is_map_data(f"{PATCHES}/client-tweaks.zip.part01")


@pytest.mark.parametrize(
    ("install", "lays"),
    [
        ({"member": "patch-X.MPQ", "to": "Data/patch-X.MPQ"}, True),
        ({"member": "patch-enUS-6.MPQ", "to": "data/enUS/patch-enUS-6.mpq"}, True),
        ({"member": "*", "to_dir": "Data/enUS"}, True),
        ({"member": "*", "to_dir": "Interface/AddOns"}, False),
        ({"member": "dinput8.dll", "to": "dinput8.dll"}, False),
        ({"member": "notes.txt", "to": "Data/notes.txt"}, False),
    ],
)
def test_a_pack_is_a_map_input_when_it_lays_an_archive_under_data(
    install: dict[str, str], lays: bool
) -> None:
    pack = centurion_like(
        packs=[{**NON_MAP_PACKS[0], "id": "p", "install": [install]}]
    ).client.packs[0]
    assert trinitycore._lays_archives(pack) is lays


def test_the_extractors_read_the_copy_read_only_and_never_the_players_client(
    machine: Machine,
) -> None:
    lay_for_client_data(machine)
    run_stage(machine, "client-data")
    assert machine.tools.client_mounts, "no extractor ran"
    for mount in machine.tools.client_mounts:
        assert mount.host == copy_dir(machine)
        assert mount.read_only


def test_the_temporary_client_is_gone_after_a_successful_extraction(machine: Machine) -> None:
    lay_for_client_data(machine)
    said = run_stage(machine, "client-data")
    assert not os.path.lexists(copy_dir(machine))
    assert "Removed the temporary copy of your client." in said


def test_the_temporary_client_is_gone_after_a_failed_extraction(machine: Machine) -> None:
    machine.tools.fail_tool = "vmap4extractor"
    lay_for_client_data(machine)
    with pytest.raises(InstallerError, match="vmap extract failed \\(exit 139\\)"):
        run_stage(machine, "client-data")
    assert not os.path.lexists(copy_dir(machine))


def test_the_temporary_client_is_gone_after_a_pack_that_fails_its_checksum(
    machine: Machine,
) -> None:
    lay_for_client_data(machine)
    (machine.server_dir / PATCHES / "patch-enUS-A.zip").write_bytes(_zip("x.MPQ", b"other"))
    with pytest.raises(InstallerError, match="does not match its published checksum"):
        run_stage(machine, "client-data")
    assert not os.path.lexists(copy_dir(machine))
    assert machine.tools.seen == {}, "nothing was extracted from a copy missing a pack"


def test_the_players_own_client_is_byte_for_byte_what_it_was(machine: Machine) -> None:
    """Bytes, modification times and modes of every file, and no file added or removed."""
    before = snapshot(machine.client)
    install(machine)
    assert snapshot(machine.client) == before


def test_the_players_client_is_untouched_by_a_failed_extraction_too(machine: Machine) -> None:
    before = snapshot(machine.client)
    machine.tools.fail_tool = "vmap4assembler"
    lay_for_client_data(machine)
    with pytest.raises(InstallerError):
        run_stage(machine, "client-data")
    assert snapshot(machine.client) == before


def test_a_resume_the_evidence_vouches_for_makes_no_copy_and_runs_no_tool(
    machine: Machine,
) -> None:
    lay_for_client_data(machine)
    run_stage(machine, "client-data")
    runs = len(machine.rec.container_runs)
    said = run_stage(machine, "client-data")
    assert len(machine.rec.container_runs) == runs
    assert not any("Making a temporary copy" in line for line in said)
    assert said[0].startswith(f"The map data in {machine.server_dir / 'data'} was already made")


def test_the_evidence_names_the_players_client_and_a_changed_pack_extracts_again(
    machine: Machine,
) -> None:
    lay_for_client_data(machine)
    run_stage(machine, "client-data")
    evidence = extract.read_evidence(machine.server_dir / "data")
    assert evidence is not None
    assert evidence.client_path == str(machine.client.resolve()), "not the deleted copy"
    newer = _zip("patch-enUS-A.MPQ", b"MPQ\x1a centurion locale, a later version")
    (machine.server_dir / PATCHES / "patch-enUS-A.zip").write_bytes(newer)
    changed = [dict(pack) for pack in REQUIRED_PACKS]
    changed[1]["md5"] = hashlib.md5(newer, usedforsecurity=False).hexdigest()
    other = centurion_like(packs=[*changed, *OPTIONAL_PACKS], rev=REV)
    machine.tools.seen.clear()
    clear_buildings_as_the_refusal_asks(machine)
    said = run_stage(machine, "client-data", entry=other)
    assert "the extracted data is for another client or plan; extracting everything again" in said
    seen = machine.tools.seen["mapextractor"]
    assert seen["Data/enUS/patch-enUS-A.MPQ"] == b"MPQ\x1a centurion locale, a later version"


# The shipped entry's checksum source (T179 Task 7, the lead's ruling): each required pack's
# md5 is read from the checkout's own `patches.md5`, so a pack the server's makers update
# with its line is taken without a catalog change.
MD5_FILE = f"{PATCHES}/patches.md5"
MD5_PACKS: list[dict[str, Any]] = [
    {key: value for key, value in pack.items() if key != "md5"} | {"md5_file": MD5_FILE}
    for pack in REQUIRED_PACKS
]


def _lay_md5_file(m: Machine) -> None:
    lines = [
        f"{hashlib.md5(m.server_dir.joinpath(rel).read_bytes(), usedforsecurity=False).hexdigest()}"
        f"  {Path(rel).name}\n"
        for rel in sorted(ZIPS)
    ]
    (m.server_dir / MD5_FILE).write_text("".join(lines), encoding="utf-8")


def _update_locale_pack(m: Machine) -> None:
    """The server's makers ship a new patch-enUS-A.zip and its line in patches.md5."""
    newer = _zip("patch-enUS-A.MPQ", b"MPQ\x1a centurion locale, 1.00155")
    (m.server_dir / PATCHES / "patch-enUS-A.zip").write_bytes(newer)
    _lay_md5_file(m)


def test_a_pack_updated_with_its_md5_line_is_extracted_by_the_re_extraction(
    machine: Machine,
) -> None:
    """Update to latest flags the map data; the re-extraction sets the old map data and its
    evidence aside and runs `client-data` again (`TrinityCoreInstaller.reextract`), which takes
    the new zip on the checkout's word -- with a pinned md5 it would be refused as a changed
    file."""
    entry = centurion_like(packs=[*MD5_PACKS, *OPTIONAL_PACKS], rev=REV)
    lay_for_client_data(machine)
    _lay_md5_file(machine)
    run_stage(machine, "client-data", entry=entry)
    _update_locale_pack(machine)
    data = machine.server_dir / "data"
    plan = entry.install.native.trinitycore.extract  # type: ignore[union-attr]
    extract.set_aside(data, extract.replaced_names(plan, also=(plan.dbc_overlay_to,)))  # T241
    machine.tools.seen.clear()

    run_stage(machine, "client-data", entry=entry)

    seen = machine.tools.seen["mapextractor"]
    assert seen["Data/enUS/patch-enUS-A.MPQ"] == b"MPQ\x1a centurion locale, 1.00155"


def test_a_pack_that_does_not_match_the_checkouts_md5_file_stops_the_extraction(
    machine: Machine,
) -> None:
    entry = centurion_like(packs=[*MD5_PACKS, *OPTIONAL_PACKS], rev=REV)
    lay_for_client_data(machine)
    _lay_md5_file(machine)
    (machine.server_dir / PATCHES / "patch-enUS-A.zip").write_bytes(
        _zip("patch-enUS-A.MPQ", b"MPQ\x1a changed without its line")
    )

    with pytest.raises(InstallerError, match="does not match its published checksum"):
        run_stage(machine, "client-data", entry=entry)
    assert not os.path.lexists(copy_dir(machine))


def test_a_pack_updated_with_its_md5_line_is_noticed_by_the_evidence_without_a_press(
    machine: Machine,
) -> None:
    """As `test_the_evidence_names_the_players_client_and_a_changed_pack_extracts_again`, for
    the md5 the checkout gives: the evidence's salt must change with the pack."""
    entry = centurion_like(packs=[*MD5_PACKS, *OPTIONAL_PACKS], rev=REV)
    lay_for_client_data(machine)
    _lay_md5_file(machine)
    run_stage(machine, "client-data", entry=entry)
    _update_locale_pack(machine)
    machine.tools.seen.clear()
    clear_buildings_as_the_refusal_asks(machine)

    said = run_stage(machine, "client-data", entry=entry)

    assert "the extracted data is for another client or plan; extracting everything again" in said
    seen = machine.tools.seen["mapextractor"]
    assert seen["Data/enUS/patch-enUS-A.MPQ"] == b"MPQ\x1a centurion locale, 1.00155"


def test_the_salt_follows_a_packs_line_in_the_checkouts_md5_file(machine: Machine) -> None:
    """The salt is the checksum the pack must have NOW: for an `md5_file` pack, its line in
    the checkout's file, so the evidence cannot vouch for maps made from the old pack."""
    entry = centurion_like(packs=[*MD5_PACKS, *OPTIONAL_PACKS], rev=REV)
    packs = [pack for pack in entry.client.packs if not pack.optional]
    lay_for_client_data(machine)
    _lay_md5_file(machine)
    before = trinitycore._packs_salt(packs, machine.server_dir)

    _update_locale_pack(machine)
    after = trinitycore._packs_salt(packs, machine.server_dir)

    assert after != before
    newer = (machine.server_dir / PATCHES / "patch-enUS-A.zip").read_bytes()
    assert hashlib.md5(newer, usedforsecurity=False).hexdigest() in after


def test_a_resume_whose_md5_file_is_gone_names_the_pack_and_the_next_step(
    machine: Machine,
) -> None:
    entry = centurion_like(packs=[*MD5_PACKS, *OPTIONAL_PACKS], rev=REV)
    lay_for_client_data(machine)
    _lay_md5_file(machine)
    run_stage(machine, "client-data", entry=entry)
    (machine.server_dir / MD5_FILE).unlink()
    first = next(pack for pack in entry.client.packs if not pack.optional)

    with pytest.raises(InstallerError) as raised:
        run_stage(machine, "client-data", entry=entry)

    said = str(raised.value)
    assert first.label in said
    assert server_build_presses.UPDATE_TO_LATEST in said
    assert "The map data was not extracted." in said


def test_a_leftover_copy_from_an_interrupted_press_is_removed_first(machine: Machine) -> None:
    lay_for_client_data(machine)
    leftover = copy_dir(machine)
    play_client.create(
        machine.client,
        leftover,
        game=ENTRY.id,
        server_dir=machine.server_dir,
        allow_full_copy=False,
    )
    (leftover / "Data" / "left-by-the-crash.MPQ").write_bytes(b"MPQ")
    run_stage(machine, "client-data")
    assert not os.path.lexists(leftover)
    assert "Data/left-by-the-crash.MPQ" not in machine.tools.seen["mapextractor"]


def test_an_unfinished_copy_a_crash_inside_create_left_is_removed_first(machine: Machine) -> None:
    """R1(a): a process that died inside `create()` leaves `<copy>.yulon-partial`."""
    lay_for_client_data(machine)
    target = copy_dir(machine)
    play_client.create(
        machine.client,
        target,
        game=ENTRY.id,
        server_dir=machine.server_dir,
        allow_full_copy=False,
    )
    partial = target.with_name(target.name + play_client.PARTIAL_SUFFIX)
    target.rename(partial)
    run_stage(machine, "client-data")
    assert not os.path.lexists(partial) and not os.path.lexists(target)
    assert machine.tools.seen, "the extraction ran over a fresh copy"


def test_a_folder_in_the_copys_place_that_is_not_this_installs_is_refused(
    machine: Machine,
) -> None:
    lay_for_client_data(machine)
    stranger = copy_dir(machine)
    stranger.mkdir(parents=True)
    (stranger / "notes.txt").write_text("mine")
    with pytest.raises(InstallerError, match="Yu'lon did not make it, so it was left as it was"):
        run_stage(machine, "client-data")
    assert (stranger / "notes.txt").read_text() == "mine"


def test_no_client_folder_is_a_refusal_naming_it(machine: Machine) -> None:
    eng = engine(machine)
    ctx = native.StageContext(
        server_dir=machine.server_dir,
        client_dir=None,
        state=context(machine).state,
        cancel=None,
        secrets=native.Secrets(db_password=DB_PASSWORD),
    )
    with pytest.raises(InstallerError, match="no client folder was given"):
        list(eng.stage_named("client-data").run(ctx))


def test_a_required_pack_from_a_download_is_a_catalog_refusal(machine: Machine) -> None:
    downloaded = {**OPTIONAL_PACKS[0], "id": "hd-required", "optional": False, "default": False}
    downloaded["install"] = [{"member": "patch-H.MPQ", "to": "Data/patch-H.MPQ"}]
    entry = centurion_like(packs=[*REQUIRED_PACKS, downloaded], rev=REV)
    lay_for_client_data(machine)
    with pytest.raises(InstallerError, match="makes HD characters a required client pack from"):
        list(engine(machine, entry=entry).stage_named("client-data").run(context(machine)))
    assert machine.tools.seen == {}


# -- client-data: the DBC overlay and the start check ------------------------------------------


def test_the_servers_dbc_files_replace_the_extracted_ones(machine: Machine) -> None:
    """README.md:174-180: use Centurion's DBCs, not the ones the extractor writes."""
    lay_for_client_data(machine)
    run_stage(machine, "client-data")
    dbc = machine.server_dir / "data" / "dbc"
    assert (dbc / "Spell.dbc").read_bytes() == SERVER_DBC["Spell.dbc"]
    assert (dbc / "LiquidType.dbc").read_bytes() == SERVER_DBC["LiquidType.dbc"]
    assert (dbc / "Map.dbc").read_bytes() == b"the client's Map.dbc", "an overlay, not a wipe"


@pytest.mark.parametrize("gone", ["530", "0", "1"])
def test_a_missing_start_map_is_refused_naming_the_step_to_run_again(
    machine: Machine, gone: str
) -> None:
    machine.tools.missing = (gone,)
    lay_for_client_data(machine)
    with pytest.raises(InstallerError) as caught:
        run_stage(machine, "client-data")
    message = str(caught.value)
    assert f"map {int(gone)}: no maps/{int(gone):03}????.map" in message
    assert "Unable to load critical files" in message
    assert "pressing Install again runs client-data again" in message
    assert extract.read_evidence(machine.server_dir / "data") is None, "the next press extracts"


def test_a_missing_start_map_stops_the_install_before_anything_is_started(
    machine: Machine,
) -> None:
    machine.tools.missing = ("530",)
    with pytest.raises(InstallerError, match="map 530"):
        install(machine)
    assert "start-db" not in machine.rec.calls and "start" not in machine.rec.calls


# -- conf --------------------------------------------------------------------------------------


def conf_value(text: str, key: str) -> str:
    (line,) = [line for line in text.splitlines() if line.split("=", 1)[0].strip() == key]
    return line.split("=", 1)[1].strip()


def test_the_conf_stage_writes_the_tables_keys_over_the_images_dist(machine: Machine) -> None:
    install(machine)
    etc = machine.server_dir / "etc"
    world = (etc / "worldserver.conf").read_text(encoding="utf-8")
    assert conf_value(world, "Updates.EnableDatabases") == "0", "the .dist ships 7"
    assert conf_value(world, "DataDir") == f'"{CORE_DIR}/data"'
    assert conf_value(world, "LogsDir") == '"../logs"'
    assert (
        conf_value(world, "LoginDatabaseInfo") == f'"centurion-db;3306;root;{DB_PASSWORD};{AUTH}"'
    )
    assert (
        conf_value(world, "WorldDatabaseInfo") == f'"centurion-db;3306;root;{DB_PASSWORD};{WORLD}"'
    )
    assert (
        conf_value(world, "CharacterDatabaseInfo")
        == f'"centurion-db;3306;root;{DB_PASSWORD};{CHARS}"'
    )
    assert conf_value(world, "SOAP.Enabled") == "1"
    assert conf_value(world, "SOAP.Port") == "7878"
    assert conf_value(world, "RealmID") == "1"
    assert conf_value(world, "mmap.enablePathFinding") == "0", "until mmaps exist (Task 4)"
    auth = (etc / "authserver.conf").read_text(encoding="utf-8")
    assert conf_value(auth, "LogsDir") == '"../logs"'


def test_playerbots_conf_is_written_beside_worldserver_conf(machine: Machine) -> None:
    """worldserver/Main.cpp:242-250 reads it from that folder and nowhere else (facts §4)."""
    said = install(machine)
    world = machine.server_dir / "etc" / "worldserver.conf"
    bots = machine.server_dir / "etc" / "playerbots.conf"
    assert bots.parent == world.parent and bots.is_file()
    assert conf_value(bots.read_text(encoding="utf-8"), "Playerbot.Enable") == "1"
    assert (
        f"playerbots.conf is beside worldserver.conf in {machine.server_dir / 'etc'}, the one "
        "place the world server reads it." in said
    )


# -- import (Review Focus 2) -------------------------------------------------------------------


def renamed(text: str) -> str:
    """`import.sh:41-43`'s sed over one file: the live realm's names, this install's instead."""
    return text.replace("legionnaireauth", AUTH).replace("centurionworld", WORLD)


def test_the_import_follows_import_sh_order_as_root(machine: Machine) -> None:
    install(machine)
    landed = [
        (argv[3] if len(argv) > 3 else None, text)
        for argv, text in machine.db.streams
        if "yulon_install" not in text and "realmlist SET" not in text
    ]
    creates = [text for schema, text in landed[:3]]
    assert [re.search(r"`([^`]+)`", text)[1] for text in creates] == [AUTH, CHARS, WORLD]  # type: ignore[index]
    assert all("COLLATE utf8mb4_unicode_ci" in text for text in creates), "import.sh:37-39"
    order = [(schema, text) for schema, text in landed[3:]]
    assert order == [
        (AUTH, renamed(SQL_FILES["auth/auth_schema.sql"])),
        (AUTH, SQL_FILES["auth/auth_data.sql"]),
        (AUTH, SQL_FILES["auth/auth_bots.sql"]),
        (CHARS, renamed(SQL_FILES["characters/characters_schema.sql"])),
        (CHARS, SQL_FILES["characters/characters_seed.sql"]),
        (CHARS, SQL_FILES["characters/characters_bots.sql"]),
        (WORLD, renamed(SQL_FILES["world/_routines.sql"])),
        (WORLD, SQL_FILES["world/broadcast_text_locale.1.sql"]),
        (WORLD, SQL_FILES["world/creature.sql"]),
        (WORLD, SQL_FILES["world/version.sql"]),
    ], "the routines once, first; then every other world table file"
    for argv, _ in machine.db.streams:
        assert list(argv[:3]) == ["mysql", "-u", "root"], argv


def test_the_renames_reach_the_listed_files_and_no_other(machine: Machine) -> None:
    install(machine)
    texts = [text for _, text in machine.db.streams]
    schema = next(text for text in texts if "trg_realmlist_au" in text)
    assert f"INSERT INTO {AUTH}.audit_dml_log VALUES ('{WORLD}')" in schema
    assert f"FROM {WORLD}.x" in next(text for text in texts if "createTournamentKit" in text)
    assert f"FROM {AUTH}.account" in next(text for text in texts if "CREATE PROCEDURE p()" in text)
    assert SQL_FILES["auth/auth_data.sql"] in texts, "an unlisted file streams as it lies"
    assert not any(
        "legionnaireauth" in text or "centurionworld" in text
        for text in texts
        if text != SQL_FILES["auth/auth_data.sql"]
    )
    on_disk = machine.server_dir / SQL_DIR / "auth" / "auth_schema.sql"
    assert on_disk.read_text(encoding="utf-8") == SQL_FILES["auth/auth_schema.sql"]


def test_the_marker_is_written_once_and_after_the_last_phase(machine: Machine) -> None:
    install(machine)
    (index,) = machine.db.marker_streams()
    last_world = max(
        i for i, (_, text) in enumerate(machine.db.streams) if text.startswith("DROP TABLE")
    )
    assert index > last_world
    assert machine.db.markers == {WORLD: [TC.sql.plan_hash()]}


@pytest.mark.parametrize(
    "fails_on",
    ["trg_realmlist_au", "DROP TABLE IF EXISTS version"],
    ids=["a trigger in the first phase", "the last world file"],
)
def test_a_half_import_writes_no_marker_and_the_next_press_clears_and_starts_over(
    machine: Machine, fails_on: str
) -> None:
    machine.db.fail_on = fails_on
    with pytest.raises(InstallerError, match="The import stopped"):
        install(machine)
    assert machine.db.marker_streams() == [], "no marker over a half import"
    assert machine.db.markers == {}
    assert machine.db.databases == {AUTH, CHARS, WORLD}, "the half-written schemas are there"

    machine.db.fail_on = ""
    machine.db.streams.clear()
    said = install(machine)
    assert any(line.startswith("The databases read as partial") for line in said)
    assert any(line.startswith("Cleared ") for line in said)
    drop = next(i for i, (_, text) in enumerate(machine.db.streams) if "DROP DATABASE" in text)
    dropped = re.findall(r"DROP DATABASE IF EXISTS `([^`]+)`", machine.db.streams[drop][1])
    assert sorted(dropped) == sorted([AUTH, CHARS, WORLD])
    after = [text for _, text in machine.db.streams[drop + 1 :]]
    assert after[0].startswith(f"CREATE DATABASE IF NOT EXISTS `{AUTH}`"), "from the start"
    assert any("trg_realmlist_au" in text for text in after)
    assert machine.db.markers == {WORLD: [TC.sql.plan_hash()]}


def test_a_finished_import_is_left_alone_on_the_next_press(machine: Machine) -> None:
    install(machine)
    machine.db.streams.clear()
    said = install(
        machine,
        ask_world_running=lambda container: False,
    )
    assert any(line.startswith("The databases read as imported") for line in said)
    assert not any(text.startswith("CREATE DATABASE") for _, text in machine.db.streams)


# -- ready -------------------------------------------------------------------------------------


def test_the_realm_row_gets_the_lan_address_and_keeps_its_build(machine: Machine) -> None:
    """`gamebuild 12342` stays: a stock-build realm shows offline to a 12342 client (facts §7)."""
    said = install(machine)
    updates = [text for _, text in machine.db.streams if "SET address" in text]
    assert updates == [
        f"UPDATE {AUTH}.realmlist SET address='192.168.1.25', "
        "localAddress='192.168.1.25' WHERE id=1;"
    ]
    assert "gamebuild" not in updates[0]
    assert any(line.startswith("The realm now advertises 192.168.1.25") for line in said)


def test_ready_waits_for_the_entrys_own_world_marker(machine: Machine) -> None:
    install(machine)
    assert machine.rec.ready_specs, "ready never asked"
    assert machine.rec.ready_specs[0].world == re.escape("World initialized")


# -- the spine's conf repair reads this family's table too ------------------------------------


def test_a_compose_repair_offers_logs_dir_to_a_trinitycore_conf_left_at_upstreams_empty(
    machine: Machine,
) -> None:
    """`_conf_edits()` asks `composegen.built_here()`, so a TrinityCore install is not skipped.

    Before Task 3 it read `native.cmangos` alone and answered nothing for this family;
    the conf table states `LogsDir = "../logs"` in both servers' confs (Task 2's binds).
    """
    etc = machine.server_dir / "etc"
    etc.mkdir(parents=True)
    (etc / "worldserver.conf").write_text(WORLD_CONF_DIST, encoding="utf-8")
    (etc / "authserver.conf").write_text(AUTH_CONF_DIST, encoding="utf-8")
    edits, kept = engine(machine)._conf_edits(machine.server_dir, "services: {}\n")
    assert kept == ()
    assert {edit.path.name: edit.settings for edit in edits} == {
        "worldserver.conf": ('LogsDir = "../logs" in etc/worldserver.conf',),
        "authserver.conf": ('LogsDir = "../logs" in etc/authserver.conf',),
    }


def test_the_copy_is_made_beside_the_players_client_and_never_in_the_server_folder(
    machine: Machine,
) -> None:
    """Fix round 1, R2: the same parent is the same drive, so the archives are shared.

    And nothing that relabels or deletes the server folder (SELinux `chcon -R`,
    Uninstall's tree removal) can reach the player's files through the copy's links.
    """
    lay_for_client_data(machine)
    run_stage(machine, "client-data")
    target = copy_dir(machine)
    assert target.parent == machine.client.parent
    assert target.name.startswith(
        f"{machine.client.name} (Yu'lon map data for {machine.server_dir.name}, temporary "
    )
    assert not target.is_relative_to(machine.server_dir)
    assert {mount.host for mount in machine.tools.client_mounts} == {target}


def test_a_drive_that_cannot_share_the_archives_is_refused_and_never_copied_in_full(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FAT32/exFAT: links fail with EPERM; a full copy would be ~17 GB of a 3.3.5a client."""
    asked: list[bool] = []
    real_create = play_client.create

    def cannot_link(src: Path, dst: Path) -> None:
        raise OSError(errno.EPERM, "Operation not permitted", str(dst))

    def create(*args: Any, **kwargs: Any) -> play_client.Marker:
        asked.append(kwargs["allow_full_copy"])
        return real_create(*args, link=cannot_link, reflink=lambda src, dst: False, **kwargs)

    monkeypatch.setattr(play_client, "create", create)
    lay_for_client_data(machine)
    with pytest.raises(InstallerError) as caught:
        run_stage(machine, "client-data")
    message = str(caught.value)
    assert asked == [False], "never a full copy"
    assert "could not be made ([Errno 1] Operation not permitted" in message
    assert "ordinary folder on an NTFS or ext4 drive" in message
    assert "not under Program Files and not on a FAT32 or exFAT drive" in message
    assert "your client was not changed" in message
    target = copy_dir(machine)
    assert not os.path.lexists(target)
    assert not os.path.lexists(target.with_name(target.name + play_client.PARTIAL_SUFFIX))
    assert not (machine.server_dir / EXTRACT_CLIENT_RECORD).exists()
    assert machine.tools.seen == {}


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="a POSIX folder mode, not root")
def test_a_client_the_app_may_not_write_beside_is_refused_saying_what_to_do(
    machine: Machine, tmp_path: Path
) -> None:
    """Program Files on Windows; a folder the user cannot write in elsewhere."""
    lay_for_client_data(machine)
    locked = tmp_path / "Program Files"
    locked.mkdir()
    machine.client = Path(shutil.move(str(machine.client), str(locked)))
    before = snapshot(machine.client)
    os.chmod(locked, 0o555)
    try:
        with pytest.raises(InstallerError, match="run Yu'lon with the rights to write beside it"):
            run_stage(machine, "client-data")
    finally:
        os.chmod(locked, 0o755)
    assert sorted(path.name for path in locked.iterdir()) == [machine.client.name]
    assert snapshot(machine.client) == before


def test_the_record_names_the_copy_while_it_exists_and_goes_with_it(machine: Machine) -> None:
    """Written before the copy is made, so a crash anywhere leaves the way back to it."""
    recorded: list[dict[str, Any]] = []
    tools = machine.tools

    def run(spec: docker.ContainerRun, **kwargs: Any) -> docker.AttachedRun:
        record = machine.server_dir / EXTRACT_CLIENT_RECORD
        recorded.append(json.loads(record.read_text(encoding="utf-8")))
        return tools(spec, **kwargs)

    lay_for_client_data(machine)
    run_stage(machine, "client-data", run_container=run)
    assert recorded[0] == {
        "version": 1,
        "target": os.fspath(copy_dir(machine)),
        "original": os.fspath(machine.client),
    }
    assert not (machine.server_dir / EXTRACT_CLIENT_RECORD).exists()


# -- Uninstall and a copy a crash left behind (fix round 1, R1) -------------------------


def leftover_copy(machine: Machine) -> Path:
    """The state a press that died after making its copy leaves: the record and the copy."""
    lay_for_client_data(machine)
    target = copy_dir(machine)
    trinitycore._write_record(machine.server_dir, target, machine.client)
    play_client.create(
        machine.client,
        target,
        game=ENTRY.id,
        server_dir=machine.server_dir,
        allow_full_copy=False,
    )
    return target


@pytest.mark.skipif(os.name == "nt", reason="the hard link and the mode spy are POSIX here")
def test_uninstall_removes_a_leftover_copy_safely_before_the_server_folder(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through `play_client.remove_folder()`, never `rmtree`, and before the record goes."""
    archive = machine.client / "Data" / "common.MPQ"
    os.chmod(archive, 0o444)
    target = leftover_copy(machine)
    assert os.path.samefile(target / "Data" / "common.MPQ", archive), "the copy shares it"
    before = snapshot(machine.client)
    chmodded: list[Path] = []
    real_chmod = os.chmod

    def chmod(path: Any, mode: int, *args: Any, **kwargs: Any) -> None:
        chmodded.append(Path(path))
        real_chmod(path, mode, *args, **kwargs)

    trees: list[Path] = []
    real_remove_tree = rmtree.remove_tree

    def remove_tree(path: Path) -> None:
        trees.append(path)
        real_remove_tree(path)

    monkeypatch.setattr(os, "chmod", chmod)
    monkeypatch.setattr(rmtree, "remove_tree", remove_tree)
    rec = PurgeRecorder(machine.server_dir, remove_folder=purge.remove_tree)
    report = rec.uninstaller(game=ENTRY.id).run(keep_characters=False)
    assert report.warnings == ()
    assert not os.path.lexists(target)
    assert not machine.server_dir.exists()
    assert trees == [machine.server_dir], "rmtree never pointed at the copy"
    assert not [path for path in chmodded if path.is_relative_to(machine.client)]
    assert snapshot(machine.client) == before, "bytes, times and the read-only mode kept"


def test_uninstall_leaves_a_folder_at_the_recorded_path_that_is_not_this_installs(
    machine: Machine,
) -> None:
    lay_for_client_data(machine)
    stranger = copy_dir(machine)
    stranger.mkdir()
    (stranger / "notes.txt").write_text("mine")
    trinitycore._write_record(machine.server_dir, stranger, machine.client)
    rec = PurgeRecorder(machine.server_dir, remove_folder=purge.remove_tree)
    report = rec.uninstaller(game=ENTRY.id).run(keep_characters=False)
    assert (stranger / "notes.txt").read_text() == "mine"
    (warning,) = report.warnings
    assert warning == (
        f"{stranger} is where this server would keep a temporary copy of your client, but "
        "Yu'lon did not make the folder that is there, so it was left alone. Do not delete it "
        "unless you know what it is."
    )
    assert "delete that folder yourself" not in warning


def test_uninstall_asks_for_the_copy_before_it_removes_the_server_folder(
    machine: Machine,
) -> None:
    rec = PurgeRecorder(machine.server_dir)
    machine.server_dir.mkdir(parents=True)
    rec.uninstaller(
        game=ENTRY.id, remove_extraction_client=lambda: rec.order.append("copy") or ""
    ).run(keep_characters=False)
    assert rec.order.index("copy") < rec.order.index(f"remove_folder:{machine.server_dir}")


def test_the_record_is_on_disk_before_the_copy_is_begun(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A process that dies INSIDE `create()` leaves a partial copy; the record must name it."""
    on_disk: list[bool] = []
    real_create = play_client.create

    def create(*args: Any, **kwargs: Any) -> play_client.Marker:
        on_disk.append((machine.server_dir / EXTRACT_CLIENT_RECORD).is_file())
        return real_create(*args, **kwargs)

    monkeypatch.setattr(play_client, "create", create)
    lay_for_client_data(machine)
    run_stage(machine, "client-data")
    assert on_disk == [True]


def test_uninstall_removes_an_unfinished_copy_a_crash_inside_create_left(
    machine: Machine,
) -> None:
    target = leftover_copy(machine)
    partial = target.with_name(target.name + play_client.PARTIAL_SUFFIX)
    target.rename(partial)
    rec = PurgeRecorder(machine.server_dir, remove_folder=purge.remove_tree)
    report = rec.uninstaller(game=ENTRY.id).run(keep_characters=False)
    assert report.warnings == ()
    assert not os.path.lexists(partial)
    assert (
        sorted(path.name for path in machine.client.parent.iterdir() if "Yu'lon" in path.name) == []
    )


# -- fix round 2 ---------------------------------------------------------------------


def failing_removal(target: Path) -> Callable[..., None]:
    """`play_client.remove_folder` that refuses `target` (a file held open) and removes others."""
    real = play_client.remove_folder

    def remove_folder(folder: Path, **kwargs: Any) -> None:
        if folder == target:
            raise PermissionError(errno.EACCES, "the file is open in another program")
        real(folder, **kwargs)

    return remove_folder


def test_uninstall_offers_our_own_copy_for_deleting_only_when_it_could_not_remove_it(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = leftover_copy(machine)
    monkeypatch.setattr(play_client, "remove_folder", failing_removal(target))
    rec = PurgeRecorder(machine.server_dir, remove_folder=purge.remove_tree)
    (warning,) = rec.uninstaller(game=ENTRY.id).run(keep_characters=False).warnings
    assert warning.startswith(
        f"A temporary copy of your game client at {target} could not be removed yet ("
    )
    assert warning.endswith(
        "Yu'lon will remove it safely the next time it starts; don't delete it yourself -- "
        "that can change your own client's files."
    )
    assert "delete that folder yourself" not in warning


@pytest.mark.skipif(os.name == "nt", reason="the hard link and the mode spy are POSIX here")
def test_an_unlisted_read_only_archive_is_moved_aside_and_no_flag_is_touched(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lead's ruling: name-only on every platform -- a rename, never a delete that may
    have to clear a read-only flag the copy's hard link shares with the player's file."""
    archive = machine.client / "Data" / "patch-4.MPQ"
    os.chmod(archive, 0o444)
    before = snapshot(machine.client)
    chmodded: list[Path] = []
    real_chmod = os.chmod

    def chmod(path: Any, mode: int, *args: Any, **kwargs: Any) -> None:
        chmodded.append(Path(path))
        real_chmod(path, mode, *args, **kwargs)

    aside: list[bool] = []
    tools = machine.tools

    def run(spec: docker.ContainerRun, **kwargs: Any) -> docker.AttachedRun:
        moved = copy_dir(machine) / trinitycore.LEFT_OUT_DIR / "Data" / "patch-4.MPQ"
        aside.append(moved.is_file() and os.path.samefile(moved, archive))
        return tools(spec, **kwargs)

    monkeypatch.setattr(os, "chmod", chmod)
    lay_for_client_data(machine)
    run_stage(machine, "client-data", run_container=run)
    assert aside and all(aside), "moved aside within the copy, the same file, not deleted"
    assert machine.tools.seen, "no extractor ran"
    for program, files in machine.tools.seen.items():
        data = [rel for rel in files if rel.endswith("patch-4.MPQ") and rel.startswith("Data")]
        assert data == [], program
    assert not [path for path in chmodded if path.name == "patch-4.MPQ"], "no flag on any name"
    assert not [path for path in chmodded if path.is_relative_to(machine.client)]
    assert snapshot(machine.client) == before
    assert not os.path.lexists(copy_dir(machine))


def test_a_record_pointing_at_this_servers_ready_to_play_client_cannot_remove_it(
    machine: Machine,
) -> None:
    """Its marker is identical (this game, this server), its place is not the copy's."""
    lay_for_client_data(machine)
    playable = machine.client.parent / f"{machine.client.name} (Yu'lon – Centurion)"
    play_client.create(
        machine.client,
        playable,
        game=ENTRY.id,
        server_dir=machine.server_dir,
        allow_full_copy=False,
    )
    trinitycore._write_record(machine.server_dir, playable, machine.client)
    rec = PurgeRecorder(machine.server_dir, remove_folder=purge.remove_tree)
    (warning,) = rec.uninstaller(game=ENTRY.id).run(keep_characters=False).warnings
    assert play_client.read_marker(playable) is not None, "the player's ready-to-play client"
    assert (playable / "Data" / "common.MPQ").is_file()
    assert warning.startswith(f"{playable} is where this server would keep a temporary copy")


@pytest.mark.parametrize("named", ["/", "."])
def test_a_record_naming_no_folder_is_a_warning_and_the_uninstall_goes_on(
    machine: Machine, named: str
) -> None:
    lay_for_client_data(machine)
    trinitycore._write_record(machine.server_dir, Path(named), machine.client)
    rec = PurgeRecorder(machine.server_dir, remove_folder=purge.remove_tree)
    report = rec.uninstaller(game=ENTRY.id).run(keep_characters=False)
    (warning,) = report.warnings
    assert warning == (
        f"This server's note of its temporary client copy names {Path(named)}, which is not a "
        "folder Yu'lon makes, so nothing was removed there."
    )
    assert not machine.server_dir.exists(), "the uninstall went on"


def link_failing_with(monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    """Every `create()` link refused with `code`, through the real `create()`."""
    real_create = play_client.create

    def refusing(src: Path, dst: Path) -> None:
        raise OSError(code, os.strerror(code), str(dst))

    def create(*args: Any, **kwargs: Any) -> play_client.Marker:
        return real_create(*args, link=refusing, reflink=lambda src, dst: False, **kwargs)

    monkeypatch.setattr(play_client, "create", create)


def test_a_full_drive_is_told_to_free_the_space_the_copy_needs(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    link_failing_with(monkeypatch, errno.ENOSPC)
    lay_for_client_data(machine)
    with pytest.raises(InstallerError) as caught:
        run_stage(machine, "client-data")
    message = str(caught.value)
    assert "the drive ran out of space while it was being made" in message
    assert re.search(r"Free about \d+\.\d GB on that drive, then press Install again\.", message)
    assert "NTFS" not in message and "Program Files" not in message


def test_a_client_folder_the_copy_refuses_says_its_own_reason(machine: Machine) -> None:
    """`play_client.plan()`'s refusal has no OS cause: a `Data` that is a link elsewhere."""
    lay_for_client_data(machine)
    real_data = machine.client.parent / "elsewhere-Data"
    (machine.client / "Data").rename(real_data)
    (machine.client / "Data").symlink_to(real_data, target_is_directory=True)
    with pytest.raises(InstallerError) as caught:
        run_stage(machine, "client-data")
    message = str(caught.value)
    assert "that copy could not be made: The Data folder of" in message
    assert "is a link to another folder" in message
    assert "NTFS" not in message and "Free " not in message


def test_a_copy_that_cannot_be_removed_after_a_success_says_uninstall_removes_it(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(play_client, "remove_folder", failing_removal(copy_dir(machine)))
    lay_for_client_data(machine)
    said = run_stage(machine, "client-data")
    (warning,) = [line for line in said if line.startswith("warning:")]
    assert warning.startswith(
        f"warning: a temporary copy of your game client at {copy_dir(machine)} could not be"
    )
    assert "don't delete it yourself" in warning
    assert warning.endswith("Uninstalling this server removes it safely.")
    assert "next press" not in warning


# -- fix round 3: a moved-aside archive goes home before the copy is removed -----------------


def windows_like_unlink(path: Any) -> None:
    """Windows' rule on any platform: a read-only file (a flag every hard link shares) refuses
    a delete. Linux deletes it by its folder's permission, which would hide the defect."""
    if not os.lstat(path).st_mode & stat.S_IWRITE:
        raise PermissionError(errno.EACCES, "Access is denied", str(path))
    os.unlink(path)


@pytest.fixture
def windows_like_removal(monkeypatch: pytest.MonkeyPatch) -> None:
    """`play_client.remove_folder` deleting the way Windows does, everything else as is."""
    real = play_client.remove_folder

    def remove_folder(folder: Path, **kwargs: Any) -> None:
        real(folder, unlink=windows_like_unlink, **kwargs)

    monkeypatch.setattr(play_client, "remove_folder", remove_folder)


def read_only_patch(machine: Machine) -> Path:
    """The player's self-installed, read-only `Data/patch-4.MPQ`, which the copy leaves out."""
    archive = machine.client / "Data" / "patch-4.MPQ"
    os.chmod(archive, 0o444)
    return archive


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.mark.usefixtures("windows_like_removal")
def test_a_left_out_read_only_archive_keeps_its_flag_after_a_successful_extraction(
    machine: Machine,
) -> None:
    archive = read_only_patch(machine)
    before = snapshot(machine.client)
    lay_for_client_data(machine)
    said = run_stage(machine, "client-data")
    assert "Removed the temporary copy of your client." in said
    assert not os.path.lexists(copy_dir(machine))
    assert mode(archive) == 0o444, "the player's own file left writable"
    assert snapshot(machine.client) == before


@pytest.mark.usefixtures("windows_like_removal")
def test_a_left_out_read_only_archive_keeps_its_flag_after_a_failed_extraction(
    machine: Machine,
) -> None:
    archive = read_only_patch(machine)
    before = snapshot(machine.client)
    machine.tools.fail_tool = "vmap4extractor"
    lay_for_client_data(machine)
    with pytest.raises(InstallerError, match="vmap extract failed"):
        run_stage(machine, "client-data")
    assert not os.path.lexists(copy_dir(machine))
    assert mode(archive) == 0o444
    assert snapshot(machine.client) == before


STOCK_COMMON = b"MPQ\x1a centurion's own common.MPQ"
OVER_STOCK = {
    # A required pack laying a file under a name the copy shares with the player's client.
    "id": "over-stock",
    "label": "Centurion common",
    "source": {"kind": "checkout", "path": f"{PATCHES}/common.zip"},
    "md5": hashlib.md5(_zip("common.MPQ", STOCK_COMMON), usedforsecurity=False).hexdigest(),
    "install": [{"member": "common.MPQ", "to": "Data/common.MPQ"}],
}


@pytest.mark.skipif(os.name == "nt", reason="the fake stands in for Windows' read-only rule")
@pytest.mark.usefixtures("windows_like_removal")
def test_a_pack_over_a_read_only_stock_archive_leaves_the_players_flag_after_extraction(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T196: the pack's swap moves the player's read-only `common.MPQ` aside in the copy.

    The install cannot delete that aside (Windows: read-only, and its flag is the
    player's too), so the copy's removal does, and puts the flag back on the player's
    own `Data/common.MPQ`, the name it was moved from.
    """
    import pathlib

    archive = machine.client / "Data" / "common.MPQ"
    os.chmod(archive, 0o444)
    lay_for_client_data(machine)
    (machine.server_dir / PATCHES / "common.zip").write_bytes(_zip("common.MPQ", STOCK_COMMON))
    before = snapshot(machine.client)
    real_unlink = pathlib.Path.unlink
    asides: list[Path] = []

    def unlink(self: Path, missing_ok: bool = False) -> None:
        if os.path.isfile(self) and not os.lstat(self).st_mode & stat.S_IWRITE:
            asides.append(self)
            raise PermissionError(errno.EACCES, "Access is denied", str(self))
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(pathlib.Path, "unlink", unlink)
    entry = centurion_like(packs=[*REQUIRED_PACKS, OVER_STOCK, *OPTIONAL_PACKS], rev=REV)

    said = run_stage(machine, "client-data", entry=entry)

    for program, files in machine.tools.seen.items():
        assert files["Data/common.MPQ"] == STOCK_COMMON, program
    assert [p.name for p in asides] == ["common.MPQ" + client_packs._ASIDE], "never left aside"
    assert "Removed the temporary copy of your client." in said
    assert not os.path.lexists(copy_dir(machine))
    assert mode(archive) == 0o444, "the player's own file left writable"
    assert snapshot(machine.client) == before


# -- T198: a read-only flag that could not be put back is told to the player ----------------


def put_back_refused(monkeypatch: pytest.MonkeyPatch, target: Path, times: int) -> None:
    """`os.chmod` on the player's `target` refused its first `times` calls (Windows: in use)."""
    real = os.chmod
    calls: list[int] = []

    def chmod(path: Any, mode: int, **kwargs: Any) -> None:
        if Path(path) == target:
            calls.append(mode)
            if len(calls) <= times:
                raise PermissionError(errno.EACCES, "Access is denied", str(path))
        real(path, mode, **kwargs)

    monkeypatch.setattr(os, "chmod", chmod)


def read_only_common(machine: Machine) -> Path:
    """The player's read-only `Data/common.MPQ`, a stock archive the copy shares."""
    archive = machine.client / "Data" / "common.MPQ"
    os.chmod(archive, 0o444)
    return archive


LOST = "no longer read-only"


@pytest.mark.skipif(os.name == "nt", reason="the fake stands in for Windows' read-only rule")
@pytest.mark.usefixtures("windows_like_removal")
@pytest.mark.parametrize("refused", [0, 2])
def test_an_extraction_says_which_file_lost_its_flag_and_only_then(
    machine: Machine, monkeypatch: pytest.MonkeyPatch, refused: int
) -> None:
    archive = read_only_common(machine)
    stock = archive.read_bytes()
    lay_for_client_data(machine)
    put_back_refused(monkeypatch, archive, refused)

    said = run_stage(machine, "client-data")

    assert "Removed the temporary copy of your client." in said
    assert not os.path.lexists(copy_dir(machine))
    assert archive.read_bytes() == stock
    told = [line for line in said if LOST in line]
    if refused:
        assert len(told) == 1 and told[0].startswith("warning: ") and str(archive) in told[0]
    else:
        assert told == []
        assert mode(archive) == 0o444


@pytest.mark.skipif(os.name == "nt", reason="the fake stands in for Windows' read-only rule")
@pytest.mark.usefixtures("windows_like_removal")
def test_a_failed_extraction_says_which_file_lost_its_flag(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = read_only_common(machine)
    machine.tools.fail_tool = "vmap4extractor"
    lay_for_client_data(machine)
    put_back_refused(monkeypatch, archive, 2)

    with pytest.raises(InstallerError, match="vmap extract failed") as info:
        run_stage(machine, "client-data")

    assert LOST in str(info.value) and str(archive) in str(info.value)
    assert not os.path.lexists(copy_dir(machine))


@pytest.mark.skipif(os.name == "nt", reason="the fake stands in for Windows' read-only rule")
@pytest.mark.usefixtures("windows_like_removal")
def test_uninstall_says_which_file_lost_its_flag(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = read_only_common(machine)
    target = leftover_copy(machine)
    put_back_refused(monkeypatch, archive, 2)
    rec = PurgeRecorder(machine.server_dir, remove_folder=purge.remove_tree)

    report = rec.uninstaller(game=ENTRY.id).run(keep_characters=False)

    assert not os.path.lexists(target)
    (told,) = [warning for warning in report.warnings if LOST in warning]
    assert str(archive) in told


@pytest.mark.skipif(os.name == "nt", reason="the fake stands in for Windows' read-only rule")
@pytest.mark.usefixtures("windows_like_removal")
@pytest.mark.parametrize("refused", [0, 2])
def test_the_startup_sweep_says_which_file_lost_its_flag_and_only_then(
    machine: Machine, monkeypatch: pytest.MonkeyPatch, config_dir: Path, refused: int
) -> None:
    archive = read_only_common(machine)
    target = leftover_copy(machine)
    noted_list(
        config_dir,
        [
            {
                "target": os.fspath(target),
                "game": ENTRY.id,
                "server_dir": os.fspath(machine.server_dir),
            }
        ],
    )
    put_back_refused(monkeypatch, archive, refused)

    warnings = trinitycore.remove_recorded_leftovers()

    assert not os.path.lexists(target)
    assert not (config_dir / trinitycore.LEFTOVERS_FILE).exists(), "the entry was dropped"
    if refused:
        (told,) = warnings
        assert LOST in told and str(archive) in told
    else:
        assert warnings == []
        assert mode(archive) == 0o444


# -- T198 fix round 1 -------------------------------------------------------------------------


def run_until_raised(
    machine: Machine, name: str, **overrides: object
) -> tuple[list[str], BaseException]:
    """A stage's lines up to the exception it ends with, and that exception."""
    said: list[str] = []
    try:
        for line in engine(machine, **overrides).stage_named(name).run(context(machine)):
            said.append(line)
    except BaseException as exc:  # noqa: BLE001 - the test inspects whatever ends it
        return said, exc
    raise AssertionError(f"{name} did not raise")


@pytest.mark.skipif(os.name == "nt", reason="the fake stands in for Windows' read-only rule")
@pytest.mark.usefixtures("windows_like_removal")
@pytest.mark.parametrize("ending", ["InstallerError", "a subclass", "another error"])
def test_an_extraction_that_fails_says_the_lost_flag_first_whatever_ends_it(
    machine: Machine, monkeypatch: pytest.MonkeyPatch, ending: str
) -> None:
    from yulon.catalog.installer import DockerUnavailableError

    archive = read_only_common(machine)
    lay_for_client_data(machine)
    if ending == "InstallerError":
        machine.tools.fail_tool = "vmap4extractor"
    elif ending == "a subclass":

        def run_plan(*args: object, **kwargs: object) -> None:
            assert os.path.lexists(copy_dir(machine)), "the copy exists when it fails"
            raise DockerUnavailableError("Docker went away")

        monkeypatch.setattr(extract, "run_plan", run_plan)
    else:  # the extractors' own errors are said as InstallerError: a bug in between is not

        def image_ref(*args: object, **kwargs: object) -> str:
            assert os.path.lexists(copy_dir(machine)), "the copy exists when it fails"
            raise ValueError("a bug")

        monkeypatch.setattr(TrinityCoreInstaller, "_image_ref", image_ref)
    put_back_refused(monkeypatch, archive, 2)

    said, failure = run_until_raised(machine, "client-data")

    told = [line for line in said if LOST in line]
    assert len(told) == 1 and told[0].startswith("warning: ") and str(archive) in told[0]
    assert not os.path.lexists(copy_dir(machine))
    if ending == "InstallerError":
        assert type(failure) is InstallerError and str(archive) in str(failure)
    elif ending == "a subclass":
        assert type(failure) is DockerUnavailableError, "its type is kept"
        assert str(failure).startswith("Docker went away") and str(archive) in str(failure)
    else:
        assert type(failure) is ValueError and str(failure) == "a bug"


@pytest.mark.skipif(os.name == "nt", reason="the fake stands in for Windows' read-only rule")
@pytest.mark.usefixtures("windows_like_removal")
def test_a_copy_that_could_not_be_made_says_the_lost_flag_of_its_unfinished_folder(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = read_only_common(machine)
    lay_for_client_data(machine)

    def broken_copy(src: object, dst: object, **kw: object) -> object:
        raise OSError(errno.EIO, "I/O error")  # after the archives were shared

    monkeypatch.setattr(play_client.shutil, "copy2", broken_copy)
    put_back_refused(monkeypatch, archive, 2)

    with pytest.raises(InstallerError) as info:
        run_stage(machine, "client-data")

    assert str(archive) in str(info.value) and LOST in str(info.value)
    assert "your client was not changed" not in str(info.value)
    assert archive.read_bytes() == b"MPQ common"


@pytest.mark.skipif(os.name == "nt", reason="the fake stands in for Windows' read-only rule")
@pytest.mark.usefixtures("windows_like_removal")
def test_two_leftover_copies_sharing_one_file_name_it_once(
    machine: Machine, monkeypatch: pytest.MonkeyPatch, config_dir: Path
) -> None:
    archive = read_only_common(machine)
    first = leftover_copy(machine)
    other_server = machine.server_dir.with_name("another-server")
    second = extraction_client_dir(machine.client, other_server)
    play_client.create(
        machine.client, second, game=ENTRY.id, server_dir=other_server, allow_full_copy=False
    )
    noted_list(
        config_dir,
        [
            {
                "target": os.fspath(first),
                "game": ENTRY.id,
                "server_dir": os.fspath(machine.server_dir),
            },
            {"target": os.fspath(second), "game": ENTRY.id, "server_dir": os.fspath(other_server)},
        ],
    )
    put_back_refused(monkeypatch, archive, 99)

    (told,) = trinitycore.remove_recorded_leftovers()

    assert not os.path.lexists(first) and not os.path.lexists(second)
    assert told.count(str(archive)) == 1
    assert told.startswith(f"Your own client's file {archive} is no longer read-only")


@pytest.mark.skipif(os.name == "nt", reason="the fake stands in for Windows' read-only rule")
@pytest.mark.usefixtures("windows_like_removal")
def test_an_uninstall_whose_folder_cannot_go_still_says_the_lost_flag(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = read_only_common(machine)
    target = leftover_copy(machine)
    put_back_refused(monkeypatch, archive, 2)

    def explode(path: Path) -> None:
        raise purge.PurgeError(f"{path} could not be deleted")

    rec = PurgeRecorder(machine.server_dir, remove_folder=explode)

    with pytest.raises(purge.PurgeError) as info:
        rec.uninstaller(game=ENTRY.id).run(keep_characters=False)

    assert not os.path.lexists(target)
    assert "could not be deleted" in str(info.value)
    assert str(archive) in str(info.value) and LOST in str(info.value)


def crashed_after_moving_aside(machine: Machine) -> Path:
    """The copy a press that died after `_drop_unlisted_archives()` leaves, and its record."""
    target = leftover_copy(machine)
    aside = target / trinitycore.LEFT_OUT_DIR / "Data" / "patch-4.MPQ"
    aside.parent.mkdir(parents=True)
    os.rename(target / "Data" / "patch-4.MPQ", aside)
    return target


@pytest.mark.usefixtures("windows_like_removal")
def test_uninstall_of_a_leftover_copy_keeps_the_flag_on_a_left_out_archive(
    machine: Machine,
) -> None:
    archive = read_only_patch(machine)
    target = crashed_after_moving_aside(machine)
    before = snapshot(machine.client)
    rec = PurgeRecorder(machine.server_dir, remove_folder=purge.remove_tree)
    report = rec.uninstaller(game=ENTRY.id).run(keep_characters=False)
    assert report.warnings == ()
    assert not os.path.lexists(target)
    assert mode(archive) == 0o444
    assert snapshot(machine.client) == before


@pytest.mark.usefixtures("windows_like_removal")
def test_an_archive_that_cannot_go_home_keeps_the_copy_and_says_so(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lead's ruling: no removal at all then -- leave it, keep the record, warn."""
    archive = read_only_patch(machine)
    target = crashed_after_moving_aside(machine)
    real_rename = os.rename

    def rename(src: Any, dst: Any) -> None:
        if trinitycore.LEFT_OUT_DIR in Path(src).parts:
            raise PermissionError(errno.EACCES, "The process cannot access the file", str(src))
        real_rename(src, dst)

    monkeypatch.setattr(os, "rename", rename)
    rec = PurgeRecorder(machine.server_dir, remove_folder=purge.remove_tree)
    (warning,) = rec.uninstaller(game=ENTRY.id).run(keep_characters=False).warnings
    assert (target / trinitycore.LEFT_OUT_DIR / "Data" / "patch-4.MPQ").is_file(), "left"
    assert (target / "Data" / "common.MPQ").is_file(), "nothing of the copy removed"
    assert mode(archive) == 0o444
    assert warning.startswith(f"A temporary copy of your game client at {target} could not be")
    assert "an archive it had set aside could not be put back first" in warning
    assert warning.endswith("don't delete it yourself -- that can change your own client's files.")


def test_a_move_aside_refused_by_a_program_holding_the_file_says_to_close_it(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_rename = os.rename

    def rename(src: Any, dst: Any) -> None:
        if Path(src).name == "patch-4.MPQ" and trinitycore.LEFT_OUT_DIR in Path(dst).parts:
            raise sharing_violation(src)
        real_rename(src, dst)

    monkeypatch.setattr(os, "rename", rename)
    lay_for_client_data(machine)
    with pytest.raises(InstallerError) as caught:
        run_stage(machine, "client-data")
    message = str(caught.value)
    assert message.endswith(
        "Close World of Warcraft (and any program using the client's files), then press "
        "Install again."
    )
    assert "NTFS" not in message and "Program Files" not in message
    assert not os.path.lexists(copy_dir(machine)), "what was moved went home, then the copy"


# -- fix round 4 -------------------------------------------------------------------


@pytest.fixture(autouse=True)
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Yu'lon's own config folder, in tmp: Uninstall may note a copy it must retry there."""
    folder = tmp_path / "yulon-config"
    monkeypatch.setattr(platform, "config_dir", lambda: folder)
    return folder


def sharing_violation(path: Any) -> PermissionError:
    """What Windows says when another program has the file open (ERROR_SHARING_VIOLATION)."""
    exc = PermissionError(errno.EACCES, "The process cannot access the file", str(path))
    exc.winerror = 32  # type: ignore[attr-defined]
    return exc


def test_uninstall_hands_a_copy_it_cannot_remove_to_yulon_and_the_next_start_removes_it(
    machine: Machine, monkeypatch: pytest.MonkeyPatch, config_dir: Path
) -> None:
    target = leftover_copy(machine)
    real_remove = play_client.remove_folder
    monkeypatch.setattr(play_client, "remove_folder", failing_removal(target))
    rec = PurgeRecorder(machine.server_dir, remove_folder=purge.remove_tree)
    rec.uninstaller(game=ENTRY.id).run(keep_characters=False)
    assert not machine.server_dir.exists(), "the uninstall went on"
    noted = json.loads((config_dir / trinitycore.LEFTOVERS_FILE).read_text(encoding="utf-8"))
    assert noted == [
        {"target": os.fspath(target), "game": ENTRY.id, "server_dir": os.fspath(machine.server_dir)}
    ]
    monkeypatch.setattr(play_client, "remove_folder", real_remove)
    assert trinitycore.remove_recorded_leftovers() == []
    assert not os.path.lexists(target)
    assert not (config_dir / trinitycore.LEFTOVERS_FILE).exists(), "the entry was dropped"


def test_a_held_open_file_at_uninstall_says_to_close_the_game_and_never_to_press_install(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = crashed_after_moving_aside(machine)
    real_rename = os.rename

    def rename(src: Any, dst: Any) -> None:
        if trinitycore.LEFT_OUT_DIR in Path(src).parts:
            raise sharing_violation(src)
        real_rename(src, dst)

    monkeypatch.setattr(os, "rename", rename)
    rec = PurgeRecorder(machine.server_dir, remove_folder=purge.remove_tree)
    (warning,) = rec.uninstaller(game=ENTRY.id).run(keep_characters=False).warnings
    assert os.path.lexists(target)
    assert (
        "Close World of Warcraft (and any program using the client's files). Yu'lon will" in warning
    )
    assert "press Install" not in warning


def test_the_retry_on_start_keeps_a_folder_that_is_not_ours_and_says_so(
    machine: Machine, config_dir: Path
) -> None:
    lay_for_client_data(machine)
    stranger = copy_dir(machine)
    stranger.mkdir()
    (stranger / "notes.txt").write_text("mine")
    entry = {"target": os.fspath(stranger), "game": ENTRY.id, "server_dir": "/elsewhere/srv"}
    config_dir.mkdir()
    (config_dir / trinitycore.LEFTOVERS_FILE).write_text(json.dumps([entry]), encoding="utf-8")
    (warning,) = trinitycore.remove_recorded_leftovers()
    assert warning.startswith(f"{stranger} is where this server would keep a temporary copy")
    assert (stranger / "notes.txt").read_text() == "mine"
    kept = json.loads((config_dir / trinitycore.LEFTOVERS_FILE).read_text(encoding="utf-8"))
    assert kept == [entry]


def test_a_copy_the_install_cannot_clear_first_says_never_to_delete_it_by_hand(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = leftover_copy(machine)
    monkeypatch.setattr(play_client, "remove_folder", failing_removal(target))
    with pytest.raises(InstallerError) as caught:
        run_stage(machine, "client-data")
    message = str(caught.value)
    assert message.startswith(f"A temporary copy of your game client at {target} could not be")
    assert "don't delete it yourself -- that can change your own client's files" in message
    assert message.endswith(
        "The next press of Install, or Uninstalling this server, removes it safely. Nothing "
        "was extracted."
    )
    assert "yourself, then" not in message


def test_a_left_out_folder_that_is_a_link_is_never_walked_and_the_copy_is_kept(
    machine: Machine, tmp_path: Path
) -> None:
    target = leftover_copy(machine)
    elsewhere = tmp_path / "somebody-else"
    (elsewhere / "Data").mkdir(parents=True)
    (elsewhere / "Data" / "theirs.MPQ").write_bytes(b"theirs")
    (target / trinitycore.LEFT_OUT_DIR).symlink_to(elsewhere, target_is_directory=True)
    rec = PurgeRecorder(machine.server_dir, remove_folder=purge.remove_tree)
    (warning,) = rec.uninstaller(game=ENTRY.id).run(keep_characters=False).warnings
    assert warning.startswith(
        f"The temporary copy of your game client at {target} holds a link Yu'lon did not make"
    )
    assert warning.endswith("so Yu'lon will not remove the copy through it; it was left as it is.")
    assert "next time it starts" not in warning
    assert (elsewhere / "Data" / "theirs.MPQ").read_bytes() == b"theirs"
    assert not (target / "Data" / "theirs.MPQ").exists(), "nothing renamed out of the link"
    assert (target / "Data" / "common.MPQ").is_file(), "the copy kept"


def _linked_left_out(machine: Machine, tmp_path: Path) -> tuple[Path, Path]:
    """A leftover copy whose `.yulon-left-out` is a link to somebody else's folder."""
    target = leftover_copy(machine)
    elsewhere = tmp_path / "somebody-else"
    (elsewhere / "Data").mkdir(parents=True)
    (elsewhere / "Data" / "theirs.MPQ").write_bytes(b"theirs")
    (target / trinitycore.LEFT_OUT_DIR).symlink_to(elsewhere, target_is_directory=True)
    return target, elsewhere


def test_install_refuses_over_a_copy_whose_left_out_folder_is_a_link(
    machine: Machine, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Task 3 gap: Install says what Uninstall says, logs it, and extracts nothing."""
    target, elsewhere = _linked_left_out(machine, tmp_path)
    with caplog.at_level(logging.WARNING, logger="yulon.catalog.families.trinitycore"):
        with pytest.raises(InstallerError) as caught:
            run_stage(machine, "client-data")
    message = str(caught.value)
    assert message.startswith(
        f"The temporary copy of your game client at {target} holds a link Yu'lon did not make"
    )
    assert message.endswith("it was left as it is. Nothing was extracted.")
    assert machine.tools.seen == {}, "no extractor ran"
    assert (elsewhere / "Data" / "theirs.MPQ").read_bytes() == b"theirs"
    assert (target / "Data" / "common.MPQ").is_file(), "the copy kept"
    (record,) = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert record.getMessage() == (
        f"{target / trinitycore.LEFT_OUT_DIR} is a link Yu'lon did not make; the copy {target} "
        "is not removed through it"
    )


def test_a_copy_with_a_linked_left_out_folder_is_never_noted_for_a_retry(
    machine: Machine, tmp_path: Path, config_dir: Path
) -> None:
    """Task 3 gap: the app's next start must not walk it either, so it is not on the list."""
    target, _elsewhere = _linked_left_out(machine, tmp_path)
    rec = PurgeRecorder(machine.server_dir, remove_folder=purge.remove_tree)
    (warning,) = rec.uninstaller(game=ENTRY.id).run(keep_characters=False).warnings
    assert "holds a link Yu'lon did not make" in warning
    assert not (config_dir / trinitycore.LEFTOVERS_FILE).exists()
    assert trinitycore.recorded_leftover_targets() == []
    assert os.path.lexists(target)


def test_a_plain_permission_refusal_is_not_called_a_file_held_open(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a Windows sharing/lock violation or EBUSY says another program holds the file."""
    real_rename = os.rename

    def rename(src: Any, dst: Any) -> None:
        if Path(src).name == "patch-4.MPQ" and trinitycore.LEFT_OUT_DIR in Path(dst).parts:
            raise PermissionError(errno.EACCES, "Permission denied", str(src))
        real_rename(src, dst)

    monkeypatch.setattr(os, "rename", rename)
    lay_for_client_data(machine)
    with pytest.raises(InstallerError) as caught:
        run_stage(machine, "client-data")
    assert "Permission denied" in str(caught.value)
    assert "Close World of Warcraft" not in str(caught.value)


# -- fix round 5 -------------------------------------------------------------------


def noted_list(config_dir: Path, entries: list[dict[str, str]]) -> Path:
    path = config_dir / trinitycore.LEFTOVERS_FILE
    config_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


ENTRY_NOTE = {"target": "/somewhere/copy", "game": "wow-centurion", "server_dir": "/srv/x"}


def refuse_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    """The list is there and will not open: a sharing violation, a permission."""
    real_read = Path.read_text

    def read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if self.name == trinitycore.LEFTOVERS_FILE:
            raise sharing_violation(self)
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)


def test_a_list_that_cannot_be_read_is_left_untouched_with_one_warning(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = noted_list(config_dir, [ENTRY_NOTE])
    before = path.read_bytes()
    refuse_reading(monkeypatch)
    (warning,) = trinitycore.remove_recorded_leftovers()
    assert warning.startswith("Yu'lon could not read its list of temporary client copies")
    assert path.read_bytes() == before, "never rewritten from nothing"


def test_a_list_that_does_not_parse_is_kept_aside_and_never_dropped(config_dir: Path) -> None:
    path = config_dir / trinitycore.LEFTOVERS_FILE
    config_dir.mkdir()
    path.write_text('[{"target": "/half', encoding="utf-8")
    assert trinitycore.remove_recorded_leftovers() == []
    (aside,) = config_dir.glob(f"{trinitycore.LEFTOVERS_FILE}.corrupt-*")
    assert aside.read_text(encoding="utf-8") == '[{"target": "/half'
    assert not path.exists()


def test_a_list_that_is_not_ours_in_shape_is_kept_aside_too(config_dir: Path) -> None:
    path = noted_list(config_dir, [ENTRY_NOTE])
    path.write_text(json.dumps([ENTRY_NOTE, {"target": 3}]), encoding="utf-8")
    assert trinitycore.remove_recorded_leftovers() == []
    (aside,) = config_dir.glob(f"{trinitycore.LEFTOVERS_FILE}.corrupt-*")
    assert json.loads(aside.read_text(encoding="utf-8"))[0] == ENTRY_NOTE


def uninstall_that_cannot_note(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, PurgeRecorder, str]:
    target = leftover_copy(machine)
    monkeypatch.setattr(play_client, "remove_folder", failing_removal(target))
    rec = PurgeRecorder(machine.server_dir, remove_folder=purge.remove_tree)
    with pytest.raises(purge.PurgeError) as caught:
        rec.uninstaller(game=ENTRY.id).run(keep_characters=False)
    return target, rec, str(caught.value)


def test_uninstall_keeps_the_server_folder_when_it_cannot_note_the_copy(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lead's ruling: the record in the folder is then the only way back to the copy."""

    def cannot_write(path: Path, entries: Any) -> None:
        raise PermissionError(errno.EACCES, "Access is denied", str(path))

    monkeypatch.setattr(trinitycore, "_write_leftovers", cannot_write)
    target, rec, message = uninstall_that_cannot_note(machine, monkeypatch)
    assert machine.server_dir.is_dir()
    assert (machine.server_dir / EXTRACT_CLIENT_RECORD).is_file()
    assert os.path.lexists(target)
    assert f"remove_folder:{machine.server_dir}" not in rec.order
    assert rec.forgotten == 0, "the install stays recorded, so Uninstall can be pressed again"
    assert any(step.startswith("remove_image:") for step in rec.order), "after the images"
    assert message.startswith(f"The server folder {machine.server_dir} was kept: a temporary")
    assert f"copy of your game client at {target} could not be removed yet" in message
    assert "then press Uninstall again" in message
    assert "don't delete it yourself" in message


def test_uninstall_keeps_the_folder_when_the_list_is_there_and_unreadable(
    machine: Machine, monkeypatch: pytest.MonkeyPatch, config_dir: Path
) -> None:
    path = noted_list(config_dir, [ENTRY_NOTE])
    before = path.read_bytes()
    refuse_reading(monkeypatch)
    target, rec, message = uninstall_that_cannot_note(machine, monkeypatch)
    assert machine.server_dir.is_dir() and rec.forgotten == 0
    assert path.read_bytes() == before, "the other copies it notes are not lost"
    assert "could not note it anywhere else" in message


def test_a_failed_write_of_the_list_leaves_no_temporary_file(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(src: Any, dst: Any) -> None:
        raise PermissionError(errno.EACCES, "Access is denied", str(dst))

    monkeypatch.setattr(os, "replace", refuse)
    path = config_dir / trinitycore.LEFTOVERS_FILE
    with pytest.raises(PermissionError):
        trinitycore._write_leftovers(path, [ENTRY_NOTE])
    assert list(config_dir.iterdir()) == []


def test_the_crash_path_log_names_install_and_uninstall_not_the_next_start(
    machine: Machine, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    machine.tools.fail_tool = "vmap4extractor"
    monkeypatch.setattr(play_client, "remove_folder", failing_removal(copy_dir(machine)))
    lay_for_client_data(machine)
    with caplog.at_level(logging.WARNING, logger="yulon.catalog.families.trinitycore"):
        with pytest.raises(InstallerError, match="vmap extract failed"):
            run_stage(machine, "client-data")
    (record,) = [r for r in caplog.records if "could not be removed yet" in r.getMessage()]
    assert record.getMessage().endswith(
        "The next press of Install, or Uninstalling this server, removes it safely."
    )
    assert "next time it starts" not in record.getMessage()


def test_the_shortfall_names_the_players_stock_client_not_the_realms_build() -> None:
    """T179 final round: the map data is made from YOUR 3.3.5a 12340 client; 12342 is the copy's."""
    shipped = load_catalog().get("wow-centurion")
    made = TrinityCoreInstaller(shipped, installers_root=resources.installers_dir())
    assert made._players_client() == (
        "your 3.3.5a client, build 12340 (the build 12342 is what Centurion's patches make its "
        "ready-to-play copy report)"
    )
    assert TrinityCoreInstaller(ENTRY)._players_client() is None, "no exe patch: its own build"
