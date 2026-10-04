"""A TrinityCore entry shaped like Centurion, for the tests of the family's templates (T179).

The shipped `wow-centurion` entry (T179 Task 7) is the app's; this one is the
tests' own copy of its shape, so a test can change a value without touching the
catalog. Every value is either a Centurion fact
(`.notes/tickets/T179-centurion-facts.md`, CENTURION @ faac5fc9) or a name this
file chooses, and nothing here is read by the app.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from yulon import docker
from yulon.catalog.catalog import CATALOG_FILE, CatalogEntry, parse_catalog

CHECKOUT = "src/centurion"
CORE_DIR = "/opt/trinitycore"
CMAKE_OPTIONS = (
    "-DCMAKE_BUILD_TYPE=RelWithDebInfo",
    "-DPLAYERBOT=ON",
    "-DTOOLS=ON",
    "-DSCRIPTS=static",
    "-DWITH_WARNINGS=OFF",
)

SQL_DIR = f"{CHECKOUT}/centurion/sql"
"""Where `centurion/sql/import.sh` and the snapshot it loads live in the checkout (facts §2)."""

AUTH, CHARS, WORLD = "centurion_auth", "centurion_characters", "centurion_world"

SQL: dict[str, Any] = {
    # `centurion/sql/import.sh`, as a plan (facts §2): the three databases with
    # its character set and collation (:37-39); then one stream per database, the
    # schema dump first (renamed, :41-43), its data, its bots (BOTS=1, the
    # default); the world's routines (renamed) and every other world table file.
    # No `create`: import.sh makes no user, and the import runs as root (the
    # MySQL proof: a non-SUPER importer fails on the first trigger, ERROR 1419).
    "create": [],
    "phases": [
        {
            "name": "databases",
            "statements": [
                f"CREATE DATABASE IF NOT EXISTS `{{{{{db}}}}}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                for db in ("AUTH_DB", "CHAR_DB", "WORLD_DB")
            ],
        },
        {
            "name": "auth",
            "into": AUTH,
            "files": [
                f"{SQL_DIR}/auth/auth_schema.sql",
                f"{SQL_DIR}/auth/auth_data.sql",
                f"{SQL_DIR}/auth/auth_bots.sql",
            ],
        },
        {
            "name": "characters",
            "into": CHARS,
            "files": [
                f"{SQL_DIR}/characters/characters_schema.sql",
                f"{SQL_DIR}/characters/characters_seed.sql",
                f"{SQL_DIR}/characters/characters_bots.sql",
            ],
        },
        {"name": "world routines", "into": WORLD, "files": [f"{SQL_DIR}/world/_routines.sql"]},
        {
            "name": "world tables",
            "into": WORLD,
            "files": [f"{SQL_DIR}/world/[!_]*.sql"],
            "sort": "name",
        },
    ],
    "verify": [
        {"db": WORLD, "query": "SELECT COUNT(*) FROM version", "min": 1},
        {"db": AUTH, "query": "SELECT COUNT(*) FROM realmlist WHERE id = 1", "min": 1},
    ],
    "player_data": [
        {
            "db": AUTH,
            "table": "account",
            "exclude_usernames": [
                "PLAYERBOTONE",
                "PLAYERBOTTWO",
                "PLAYERBOTTHREE",
                "PLAYERBOTFOUR",
            ],
        }
    ],
    "marker_db": WORLD,
    "renames": [["legionnaireauth", AUTH], ["centurionworld", WORLD]],
    "rename_files": [
        f"{SQL_DIR}/auth/auth_schema.sql",
        f"{SQL_DIR}/characters/characters_schema.sql",
        f"{SQL_DIR}/world/_routines.sql",
    ],
}

CLIENT_ARCHIVES = (
    # A stock 3.3.5a client's archives, which the extraction client keeps; every other
    # `.MPQ` in it is somebody's patch (T179 Task 3 fix round 1, the lead's list).
    "common.MPQ",
    "common-2.MPQ",
    "expansion.MPQ",
    "lichking.MPQ",
    "patch.MPQ",
    "patch-2.MPQ",
    "patch-3.MPQ",
    "{locale}/locale-{locale}.MPQ",
    "{locale}/speech-{locale}.MPQ",
    "{locale}/expansion-locale-{locale}.MPQ",
    "{locale}/lichking-locale-{locale}.MPQ",
    "{locale}/expansion-speech-{locale}.MPQ",
    "{locale}/lichking-speech-{locale}.MPQ",
    "{locale}/patch-{locale}.MPQ",
    "{locale}/patch-{locale}-2.MPQ",
    "{locale}/patch-{locale}-3.MPQ",
)

DB_STRING = '"{{{{DB_HOST}}}};3306;{{{{DB_USER}}}};{{{{DB_PASSWORD}}}};{db}"'

TRINITYCORE: dict[str, Any] = {
    "checkout": CHECKOUT,
    "client": {
        "required_file": "Data/lichking.MPQ",
        "min_mpq": 6,
        "mpq_depth": "recursive",
        "locale_mpq_required": True,
    },
    "sparse_exclude": ["playerbot reference", "centurion/launcher"],
    "dockerfile": {"make_jobs": 2, "cmake_options": list(CMAKE_OPTIONS)},
    "extract": {
        "image": "server",
        "tools": [
            {
                "name": "maps",
                "argv": [f"{CORE_DIR}/bin/mapextractor", "-i", "/client", "-o", "/out", "-e", "1"],
                "produces": {"maps": 100},
            },
            {
                "name": "vmap extract",
                "argv": [f"{CORE_DIR}/bin/vmap4extractor", "-d", "/client/Data/"],
                "produces": {"Buildings": 100},
            },
            {
                "name": "vmap assemble",
                "argv": [f"{CORE_DIR}/bin/vmap4assembler", "Buildings", "vmaps"],
                "produces": {"vmaps": 100},
            },
        ],
        "dbc_overlay_from": "centurion/dbc",
        "client_archives": list(CLIENT_ARCHIVES),
    },
    "mmaps": {
        "argv": [f"{CORE_DIR}/bin/mmaps_generator", "--threads", "{{THREADS}}"],
        "background": True,
    },
    "conf": {
        "source_dir": f"{CORE_DIR}/etc",
        "files": {
            "worldserver.conf": {
                "keys": {
                    "Updates.EnableDatabases": "0",
                    "DataDir": f'"{CORE_DIR}/data"',
                    "LogsDir": '"../logs"',
                    "LoginDatabaseInfo": DB_STRING.format(db="{{AUTH_DB}}"),
                    "WorldDatabaseInfo": DB_STRING.format(db="{{WORLD_DB}}"),
                    "CharacterDatabaseInfo": DB_STRING.format(db="{{CHAR_DB}}"),
                    "WorldServerPort": "{{WORLD_PORT}}",
                    "RealmID": "1",
                    "Console.Enable": "1",
                    "SOAP.Enabled": "1",
                    "SOAP.IP": '"0.0.0.0"',
                    "SOAP.Port": "7878",
                    "mmap.enablePathFinding": "0",
                }
            },
            "authserver.conf": {
                "keys": {
                    "LogsDir": '"../logs"',
                    "LoginDatabaseInfo": DB_STRING.format(db="{{AUTH_DB}}"),
                }
            },
            # The live realm's population settings (centurion/conf/playerbots.conf:27,
            # 77-104); the `.dist` ships every one of them off or empty. The count is
            # what the Bots tab's box reads and writes (`bot_population`).
            "playerbots.conf": {
                "keys": {
                    "Playerbot.Enable": "1",
                    "Playerbot.RandomPopulation.Enable": "1",
                    "Playerbot.RandomPopulation.TargetMin": "150",
                    "Playerbot.RandomPopulation.TargetMax": "150",
                    "Playerbot.RandomPopulation.BotAccountIds": "76,77,78",
                }
            },
        },
        "playerbots_conf": "playerbots.conf",
        # The live realm's AutoBalance.conf, copied whole beside worldserver.conf
        # (README.md:203-204; AutoBalanceConfig.cpp:177-213, 761). T179 Task 8.
        "from_checkout": {"AutoBalance.conf": "centurion/conf/AutoBalance.conf"},
    },
    "sql": SQL,
    "required_maps": [0, 1, 530],
    # What "Update the server to latest…" does with a change to the snapshot (T179
    # Task 6, owner decision 2): each world file is a whole-table dump (DROP + CREATE,
    # facts §2), so a changed one is imported again; the two schema dumps are the
    # layout of the accounts and the characters, and a change there is refused; and
    # the realm row is Yu'lon's, so an auth_data.sql change that touches only it is
    # left out rather than refused.
    "updates": {
        "reimport_phases": ["world routines", "world tables"],
        "layout_files": [
            f"{SQL_DIR}/characters/characters_schema.sql",
            f"{SQL_DIR}/auth/auth_schema.sql",
        ],
        "skip_lines": {f"{SQL_DIR}/auth/auth_data.sql": ["INSERT INTO `realmlist` "]},
    },
}


def centurion_like(
    *, packs: list[dict[str, Any]] | None = None, rev: str | None = None
) -> CatalogEntry:
    """A whole, valid `trinitycore` entry: generated password, SOAP channel published.

    `packs` are the client's packs (T181 shapes, none by default) and `rev` the
    core source's pin, for the install engine's tests (Task 3).
    """
    data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    entry: dict[str, Any] = copy.deepcopy(
        next(game for game in data["games"] if game["id"] == "wow-tbc")
    )
    entry.update(
        {
            "id": "wow-centurion",
            "name": "Centurion",
            "emulator": {
                "name": "Centurion (TrinityCore 3.3.5 fork)",
                "sources": [
                    {
                        "repo": "thomasjteachey/TrinityCore112",
                        "dest": CHECKOUT,
                        "branch": "CENTURION",
                        **({"rev": rev} if rev is not None else {}),
                    }
                ],
            },
            "containers": {
                "db": "centurion-db",
                "auth": "centurion-authserver",
                "world": "centurion-worldserver",
            },
            "databases": {"auth": AUTH, "characters": CHARS, "world": WORLD},
            "realmlist": {
                "table": "realmlist",
                "address_column": "address",
                "local_address_column": "localAddress",
            },
            # TrinityCore's GM level: `account_access(AccountID, SecurityLevel, RealmID)`
            # (auth_schema.sql:49-55), levels 0-3 (`SEC_ADMINISTRATOR`, Common.h:38-44).
            "accounts": {
                "scheme": "trinitycore",
                "level": {
                    "table": "account_access",
                    "account_column": "AccountID",
                    "level_column": "SecurityLevel",
                    "max_level": 3,
                },
            },
            # `CLI_PREFIX` "TC> " (CliRunnable.cpp:42), read with readline and printed
            # again when a command finishes (:92-96, :157): AzerothCore's console code,
            # so the prompt comes in front of the answer as it does there.
            "console": {"prompt": "TC>", "prompt_precedes_answer": True},
            # The Characters tab's verbs, read in the source (each `Console::Yes`):
            # `tele name` (cs_tele.cpp:56), `character level`/`character rename`
            # (cs_character.cpp:72-73); the inventory row carries the item INSTANCE
            # guid and `item_instance.itemEntry` the template, as on AzerothCore;
            # `MAX_MAIL_ITEMS` is 12 (Mail.h:33).
            # Offered only once T179 Task 9 has watched them work
            # (`controller_wow_centurion.characters`).
            "play": {
                "equipped": {
                    "template_column": "itemEntry",
                    "instance_table": "item_instance",
                    "inventory_column": "item",
                },
                "teleport_command": "tele name",
                "mail_item_cap": 12,
                "rename_command": "character rename",
                "set_level_command": "character level",
            },
            # Centurion's bots are four fixed accounts, PLAYERBOTONE..FOUR (ids 76-79,
            # centurion/sql/auth/auth_bots.sql:3-12), with no prefix setting anywhere:
            # the name prefix is the whole marker (T179 Task 5).
            "observability": {"bots": {"account_prefix": "PLAYERBOT"}},
            # Centurion takes no add-on modules: its Modules tab says so (T179 Task 5).
            "has_manifests": False,
            "client": {"version": "3.3.5a", "build": 12342, "packs": packs or []},
        }
    )
    entry["operations"] = {
        **entry["operations"],
        "namespace": "urn:TC",
        "enable_conf": {
            "file": "etc/worldserver.conf",
            "keys": {"SOAP.Enabled": "1", "SOAP.IP": "0.0.0.0", "SOAP.Port": "7878"},
        },
        "must_not_listen": [3443],
    }
    entry["install"]["default_server_dir"] = "yulon-centurion"
    entry["install"]["password"] = {"mode": "generated", "file": ".db_password", "prefix": "tc-"}
    entry["install"]["native"] = {
        "family": "trinitycore",
        "templates": "shared/trinitycore",
        "dockerfile_dir": "wow-centurion/native",
        "image_prefix": "yulon.local/trinitycore-centurion-",
        "images": ["server"],
        "db": {"image": "mysql:8.4", "client": "mysql", "user": "root"},
        "ready": {"world": "World initialized"},
        "update_to_latest": True,
        "trinitycore": copy.deepcopy(TRINITYCORE),
    }
    return parse_catalog({"schema_version": 1, "games": [entry]}).get("wow-centurion")


# -- the movement-map job's Docker (T179 Task 4) ------------------------------------------


@dataclass
class FakeJob:
    """One background container: what it was started with, where it is, what it printed."""

    spec: docker.ContainerRun
    container_id: str
    status: str = "running"
    exit_code: int | None = None
    logs: list[str] = field(default_factory=list)


class FakeMmapsDocker:
    """`mmaps.Runner` over a dict of containers, so no test reaches a real daemon.

    Containers are kept by NAME, as Docker keeps them: a second `run_detached()`
    under a name in use is refused with Docker's own conflict, which is how a
    second job at once would show. `answers=False` is a daemon that does not
    answer an inspect (`ContainerExit()` with nothing in it); `refuse_remove` makes
    every `docker rm -f` fail. The world server's start time is `world_started_at`.
    """

    def __init__(self) -> None:
        self.jobs: dict[str, FakeJob] = {}
        self.calls: list[str] = []
        self.started: list[docker.ContainerRun] = []
        self.world_started_at = "2026-10-02T10:00:00.123456789Z"
        self.answers = True
        self.refuse_run = ""
        self.refuse_remove = ""
        self.mmaps_at_run: list[list[str]] = []
        self.ncpu: int | None = 8
        self.hang = False
        """Every call answers as one that timed out: unanswered reads, refused writes."""
        self.timeouts: list[tuple[str, float]] = []

    # -- `mmaps.Runner` -------------------------------------------------------------

    def run_detached(self, spec: docker.ContainerRun, name: str, *, timeout: float) -> str:
        self.calls.append(f"run:{name}")
        self.timeouts.append(("run", timeout))
        if self.hang:
            raise docker.DockerCommandError("timed out")
        if self.refuse_run:
            raise docker.DockerCommandError(self.refuse_run)
        if name in self.jobs:
            raise docker.DockerCommandError(
                f'Conflict. The container name "/{name}" is already in use'
            )
        out = self.output_dir(spec)
        assert out.is_dir(), "the generator's writable folder must exist before the bind"
        self.mmaps_at_run.append(sorted(path.name for path in out.iterdir()))
        self.started.append(spec)
        container_id = f"{len(self.started):064x}"
        self.jobs[name] = FakeJob(spec, container_id)
        return container_id

    def inspect(self, name: str, *, timeout: float) -> docker.ContainerExit:
        self.calls.append(f"inspect:{name}")
        self.timeouts.append(("inspect", timeout))
        if not self.answers or self.hang:
            return docker.ContainerExit()
        job = self.jobs.get(name)
        if job is None:
            return docker.ContainerExit(missing=True)
        return docker.ContainerExit(job.status, job.exit_code, "", job.container_id)

    def log_tail(self, name: str, lines: int, *, timeout: float) -> str | None:
        self.timeouts.append(("log_tail", timeout))
        job = self.jobs.get(name)
        return None if job is None else "\n".join(job.logs[-lines:])

    def remove(self, name: str, *, timeout: float) -> None:
        self.calls.append(f"remove:{name}")
        self.timeouts.append(("remove", timeout))
        if self.hang:
            raise docker.DockerCommandError("timed out")
        if self.refuse_remove:
            raise docker.DockerCommandError(self.refuse_remove)
        self.jobs.pop(name, None)

    def started_at(self, container: str, *, timeout: float) -> str:
        self.timeouts.append(("started_at", timeout))
        return "" if self.hang else self.world_started_at

    def cpus(self, *, timeout: float) -> int | None:
        self.timeouts.append(("cpus", timeout))
        return None if self.hang else self.ncpu

    # -- driving it -----------------------------------------------------------------

    @staticmethod
    def output_dir(spec: docker.ContainerRun) -> Path:
        (writable,) = [mount.host for mount in spec.mounts if not mount.read_only]
        return writable

    def only(self) -> tuple[str, FakeJob]:
        (item,) = self.jobs.items()
        return item

    def say(self, *lines: str) -> None:
        self.only()[1].logs.extend(lines)

    def write_tiles(self, count: int) -> None:
        out = self.output_dir(self.only()[1].spec)
        for index in range(count):
            (out / f"000{index:04}.mmtile").write_bytes(b"MMAP")

    def finish(self, code: int = 0, *, tiles: int = 0) -> None:
        self.write_tiles(tiles)
        job = self.only()[1]
        job.status, job.exit_code = "exited", code
        job.logs.append("Finished. MMAPS were built in 3h 12m 5s" if code == 0 else "Segfault")

    def vanish(self) -> None:
        self.jobs.pop(self.only()[0])
