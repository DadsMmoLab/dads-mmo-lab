"""A second AzerothCore entry beside `wow-wotlk`, for the tests that need two at once (T552).

Built from the shipped `wow-wotlk` entry's own JSON, so it carries every field
that entry does and differs only in what makes it a server of its own: its id,
folder, image prefix, container names and host ports. That is the shape the
coming `wow-unbound` entry takes (design 2026-10-07 §2.2).
"""

from __future__ import annotations

import copy
import json
from typing import Any

from yulon.catalog.catalog import CATALOG_FILE, CatalogEntry

SECOND_ID = "wow-second-ac"
SECOND_PREFIX = "ub-"
SECOND_CONTAINERS = {
    "db": "ub-database",
    "auth": "ub-authserver",
    "world": "ub-worldserver",
    "db_import": "ub-db-import",
    "client_data": "ub-client-data-init",
}
SECOND_PORTS = {"auth": 3725, "world": 8086, "db": 3307}
SECOND_SOAP = 7879


def wotlk_json() -> dict[str, Any]:
    """The shipped `wow-wotlk` entry as the catalog file spells it."""
    with CATALOG_FILE.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return next(g for g in data["games"] if g["id"] == "wow-wotlk")


def second_ac_json(**changes: Any) -> dict[str, Any]:
    """`wow-wotlk`'s JSON made into a second server; `changes` replace top-level keys."""
    raw = copy.deepcopy(wotlk_json())
    raw["id"] = SECOND_ID
    raw["name"] = "WoW Second AzerothCore"
    raw["install"]["default_server_dir"] = "yulon-second-ac"
    raw["install"]["native"]["image_prefix"] = "yulon.local/ac-second-"
    raw["install"]["native"]["soap_port"] = SECOND_SOAP
    raw["operations"]["port"] = SECOND_SOAP
    raw["containers"] = dict(SECOND_CONTAINERS)
    raw["ports"] = dict(SECOND_PORTS)
    raw.update(changes)
    return raw


def second_ac_entry(**changes: Any) -> CatalogEntry:
    """`second_ac_json()` validated, so it is an entry the app could load."""
    return CatalogEntry.model_validate(second_ac_json(**changes))
