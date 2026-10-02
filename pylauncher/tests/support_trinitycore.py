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

TRINITYCORE: dict[str, Any] = {
    "checkout": CHECKOUT,
    "sparse_exclude": ["playerbot reference", "centurion/launcher"],
    "dockerfile": {"make_jobs": 2, "cmake_options": list(CMAKE_OPTIONS)},
    "extract": {
        "image": "server",
        "tools": [
            {
                "name": "maps",
                "argv": [f"{CORE_DIR}/bin/mapextractor", "-i", "/client", "-o", "/out"],
                "produces": {"maps": 100},
            }
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
                    "SOAP.Enabled": "1",
                    "SOAP.IP": '"0.0.0.0"',
                    "SOAP.Port": "7878",
                }
            },
            "authserver.conf": {"keys": {"LogsDir": '"../logs"'}},
            "playerbots.conf": {"keys": {"Playerbot.Enable": "1"}},
        },
        "playerbots_conf": "playerbots.conf",
    },
    "sql": {
        "create": ["centurion_auth", "centurion_characters", "centurion_world"],
        "phases": [
            {
                "name": "auth schema",
                "into": "centurion_auth",
                "files": [f"{CHECKOUT}/centurion/sql/auth/auth_schema.sql"],
            }
        ],
        "marker_db": "centurion_auth",
        "renames": [["legionnaireauth", "centurion_auth"]],
        "rename_files": [f"{CHECKOUT}/centurion/sql/auth/auth_schema.sql"],
    },
    "required_maps": [0, 1, 530],
}


def centurion_like() -> CatalogEntry:
    """A whole, valid `trinitycore` entry: generated password, SOAP channel published."""
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
                    }
                ],
            },
            "containers": {
                "db": "centurion-db",
                "auth": "centurion-authserver",
                "world": "centurion-worldserver",
            },
            "databases": {
                "auth": "centurion_auth",
                "characters": "centurion_characters",
                "world": "centurion_world",
            },
            "accounts": {"scheme": "trinitycore"},
            "console": {"prompt": "TC>", "prompt_precedes_answer": False},
            "observability": None,
            "client": {"version": "3.3.5a", "build": 12342},
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
