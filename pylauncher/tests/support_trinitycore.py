"""A TrinityCore entry shaped like Centurion, for the tests of the family's templates (T179).

The shipped `wow-centurion` entry is a later task's; until it lands, the templates
under `catalog/installers/shared/trinitycore/` and `wow-centurion/native/` are
rendered from this one. Every value is either a Centurion fact
(`.notes/tickets/T179-centurion-facts.md`, CENTURION @ faac5fc9) or a name this
file chooses, and nothing here is read by the app.
"""

from __future__ import annotations

import copy
import json
from typing import Any

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
    },
    "mmaps": {"argv": [f"{CORE_DIR}/bin/mmaps_generator"], "background": True},
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
            "playerbots.conf": {"keys": {"Playerbot.Enable": "1"}},
        },
        "playerbots_conf": "playerbots.conf",
    },
    "sql": SQL,
    "required_maps": [0, 1, 530],
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
            "accounts": {"scheme": "trinitycore"},
            "console": {"prompt": "TC>", "prompt_precedes_answer": False},
            "observability": None,
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
    entry["install"]["default_server_dir"] = "wow-centurion-server"
    entry["install"]["password"] = {"mode": "generated", "file": ".db_password", "prefix": "tc-"}
    entry["install"]["native"] = {
        "family": "trinitycore",
        "templates": "shared/trinitycore",
        "dockerfile_dir": "wow-centurion/native",
        "image_prefix": "yulon.local/trinitycore-centurion-",
        "images": ["server"],
        "db": {"image": "mysql:8.4", "client": "mysql", "user": "root"},
        "ready": {"world": "World initialized"},
        "trinitycore": copy.deepcopy(TRINITYCORE),
    }
    return parse_catalog({"schema_version": 1, "games": [entry]}).get("wow-centurion")
