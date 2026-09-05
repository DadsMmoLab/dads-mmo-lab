"""The one place this package reads `catalog.json`, so no module below spells a game fact.

`controller_wow_wotlk` predates the typed catalog: its container names, schema
names, console prompt and ready marker are Python literals, one copy per module.
Repeating that shape for a second game would put the same fact in two files that
can disagree — and the CMaNGOS entries already carry every one of those facts,
typed and validated, because the install engine needs them (`catalog/catalog.py`,
`families/cmangos.py`). So the rule for this package is: a game fact is read
from the entry, here, and passed down.

`entry()` is cached because the answer cannot change inside a run — the file is
bundled with the app — and because four modules ask for it. `entry.cache_clear()`
is the seam a test uses after pointing `load_catalog` somewhere else.

`GAME` is the one string this package does hardcode, and it has to be: it is the
package's identity, the answer to "which entry am I?", not a fact about the
game. Everything else follows from it.
"""

from __future__ import annotations

from functools import lru_cache

from yulon.catalog.catalog import (
    CatalogEntry,
    CmangosData,
    DbFacts,
    NativeInstall,
    ReadyMarkers,
    SqlPlan,
    load_catalog,
)
from yulon.manifest import Db

GAME = "wow-tortoise"

FAMILY = "cmangos"
"""The install family this package's modules assume. Checked, never assumed silently.

`repair.py` reads the `cmangos` block's SQL plan to find the import marker, and
`docker_ctl.py` reads the family's `ready` markers. An entry re-pointed at
another family would still answer `containers` and `databases`, so the wrong
half would keep working and the probe would quietly ask a question about a plan
that no longer describes the install.
"""


class CatalogFactsError(RuntimeError):
    """The `wow-tortoise` entry is missing something this package needs.

    Always a bug in the app's own catalog file rather than anything wrong on the
    machine, and the messages say so: nothing a user can do to `catalog.json`
    from outside the app can reach these, because the file ships inside it.
    """


@lru_cache(maxsize=1)
def entry() -> CatalogEntry:
    """The validated `wow-tortoise` catalog entry.

    Raises:
        CatalogFactsError: the shipped catalog has no entry with this id.
    """
    try:
        return load_catalog().get(GAME)
    except KeyError as exc:
        raise CatalogFactsError(
            f"the catalog has no {GAME} entry, so this package has no server to manage. "
            "That is a catalog error in the app, not something to fix on this machine."
        ) from exc


def db() -> DbFacts:
    """This install's database facts: image, CLIENT BINARY, app user, charset.

    The client is the one this package's own argv-building path names
    (`repair.py` hands it to `docker.sql_query`). It is `mariadb` for every
    CMaNGOS entry and `mysql` for AzerothCore, and the two are not
    interchangeable: MariaDB 11 ships neither `mysql` nor `mysqldump`
    (`apply.mysql_client()` records the measurement). Taken from data for that
    reason, never written out here.

    Raises:
        CatalogFactsError: the entry carries no `install.native` block.
    """
    return _native().db


def cmangos() -> CmangosData:
    """The typed CMaNGOS block: client spec, extract plan, conf table, SQL plan.

    Raises:
        CatalogFactsError: the entry has no `install.native`, is not a
            `cmangos` entry, or names that family without carrying its block.
    """
    native = _native()
    if native.family != FAMILY:
        raise CatalogFactsError(
            f"{GAME} says its install family is {native.family!r}, and this package reads "
            f"{FAMILY!r} data. That is a catalog error in the app, not something to fix on "
            "this machine."
        )
    data = native.cmangos
    if data is None:
        raise CatalogFactsError(
            f"{GAME} says its family is {FAMILY} but carries no {FAMILY} block. That is a "
            "catalog error in the app, not something to fix on this machine."
        )
    return data


def sql_plan() -> SqlPlan:
    """The import plan: the phases, the verify rules, and where the marker row lives."""
    return cmangos().sql


def schemas() -> dict[Db, str]:
    """Manifest `db` key -> this core's schema name, for `apply.DockerSql`.

    `tw_logon`, not `acore_auth`. A `DockerSql` built without this map puts
    AzerothCore's names in argv and every statement dies with
    `ERROR 1049 Unknown database` — the failure `Databases.schema_map()` was
    added to end.
    """
    return entry().schema_map()


def plan_schemas() -> dict[str, str]:
    """The identity mapping over this game's schema NAMES, keyed by name.

    What `sqlplan` looks a plan's `marker_db`, `verify.db` and `player_data.db`
    up in: those spell databases the way `catalog.json` does (`tw_world`), never
    by role (`world`), so `schemas()` answers for none of them. The install
    engine builds the same mapping for the same reason
    (`CmangosInstaller._schemas()`); it is four names off the entry, not a
    behaviour, and `extra` is included because `tw_logs` is one of the plan's
    databases.
    """
    databases = entry().databases
    return {
        name: name
        for name in (
            databases.auth,
            databases.characters,
            databases.world,
            *databases.extra,
        )
    }


def core_databases() -> tuple[str, str, str]:
    """The three schemas whose absence is worth an alarm, in this core's names.

    `maintenance.backup()` defaults to AzerothCore's three, which is how a
    Tortoise backup came to report `acore_auth` missing on a dump that had taken
    everything (the measurement is on `CatalogEntry.core_databases`).
    """
    return entry().core_databases()


def ready_markers() -> ReadyMarkers:
    """What "the server is up" looks like in THIS core's log, and how long to wait.

    A mangosd never prints AzerothCore's `ready...`, which is the marker
    compiled into `docker.azerothcore_ready()` and therefore into the base
    `Controller.wait_ready()`. These markers are declared per game, alongside
    the `fatal` line that ends the wait early and the `regex` flag that says
    whether they are patterns or literals.
    """
    return _native().ready


def _native() -> NativeInstall:
    """The entry's `install.native` block, or a refusal naming what is missing."""
    native = entry().install.native
    if native is None:
        raise CatalogFactsError(
            f"{GAME} carries no `install.native` block, so this package cannot say which "
            "database client, ready markers or import plan belong to it. That is a catalog "
            "error in the app, not something to fix on this machine."
        )
    return native
