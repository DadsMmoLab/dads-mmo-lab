"""Vanilla controller — server management for the CMaNGOS `wow-vanilla` install.

Same job as `controller_wow_wotlk/`, a different core. That package is
AzerothCore-shaped; this game is CMaNGOS mangos-classic with the cmangos
playerbots fork, and the differences are the whole reason this package exists
rather than a second entry in a list somewhere.

What is genuinely different, and where each fact comes from
-----------------------------------------------------------
Every one of these is READ from `catalog/catalog.json`'s `wow-vanilla` entry
through `entry()` below — none is spelled again here. A catalog edit therefore
moves this package with it, and a fact that is wrong is wrong in one place.

* **Containers.** `vanilla-db` / `vanilla-realmd` / `vanilla-mangosd`, not
  `ac-*`. `docker_ctl.SPEC` is `entry().container_spec()`.
* **No one-shot import service.** AzerothCore ships `ac-db-import` and
  `docker.repair_import()` re-runs it. A CMaNGOS install has no such compose
  service — the SQL is applied by the installer's own `import` stage
  (`catalog/families/cmangos.py`) — so `docker_ctl` deliberately does NOT
  re-export `repair_import`. See `repair.py` for what replaces it.
* **The database client.** `install.native.db.client` says `mariadb`;
  AzerothCore's entry says `mysql`. `mariadb:11` ships neither `mysql` nor
  `mysqldump`, so a hardcoded client is not a style problem, it is every
  statement failing before it reaches a database (`apply.mysql_client()`
  records the measurement). `docker_ctl.DB_CLIENT` carries the declared value.
* **Schema names.** `realmd` / `characters` / `mangos` / `logs`, not
  `acore_*`. `entry().schema_map()` is what keeps `DockerSql` off the
  AzerothCore names, and `maintenance.CORE_DATABASES` is what keeps a backup
  from reporting three absent `acore_*` schemas on a dump that took everything.
* **The ready marker.** A CMaNGOS worldserver never prints AzerothCore's
  `ready...`; the entry names `Avg Diff:`, and `ready.auth` is null, so there
  is no auth line to wait for at all. `docker_ctl.ready_spec()` builds the
  `ReadySpec` from that block instead of from `docker.azerothcore_ready()`.
* **The console prompt.** `mangos>`, and `prompt_precedes_answer` is FALSE —
  CMaNGOS reads its console with `fgets` and prints the prompt only after the
  command finished, where AzerothCore's readline console redisplays it in
  front. Same delimiter, the answer on the other side of it.
* **Account storage.** `accounts.scheme` is `mangos_srp6`: the same SRP6
  arithmetic as AzerothCore, stored as uppercase hex text in `account.v`/`s`
  with the GM level in `account.gmlevel` and no `account_access` table.
* **Import evidence.** AzerothCore's updater keeps `updates` /
  `updates_include` tables; CMaNGOS has no such bookkeeping. What this install
  has is the `yulon_install` marker row the installer writes after its verify
  rules pass — so the probe is `sqlplan.MarkerGate`, not a copy of
  `controller_wow_wotlk/repair.py`.

What is NOT different, and is therefore not written again
---------------------------------------------------------
The WotLK package's `accounts`, `console` and `maintenance` modules are
already game-agnostic in their bodies — every game-specific fact reaches them
as an argument (`scheme=`, `prompt=`, `spec=`, `core_databases=`). This
package's modules of those names bind THIS game's data to them and re-export
the rest; they reimplement nothing. `docker_ctl` re-exports `yulon.docker`'s
shared operations exactly as WotLK's does, for the same reason: one entry
point per game, no caller reaching past it.

That the shared code lives under a package called `controller_wow_wotlk` is an
accident of which game was written first, and importing it from here is the
alternative to a third copy of the SRP6 arithmetic and the restore machinery.
Giving it a home that is not one game's package is a change to files this
package does not own, and is left undone rather than half-done.

There is no `modules.py`. That module binds a manifest tree, and `wow-vanilla`
has no `has_manifests` in the catalog and no `manifests/wow-vanilla/`
directory — so a module store here would be a store over nothing.
"""

from __future__ import annotations

from functools import lru_cache

from yulon.catalog.catalog import CatalogEntry, load_catalog

GAME_ID = "wow-vanilla"
"""The catalog id every module in this package binds to."""


@lru_cache(maxsize=1)
def entry() -> CatalogEntry:
    """This game's catalog entry — the one source of every per-game fact here.

    Cached because the modules below read it at import time to build their
    defaults (`docker_ctl.SPEC`, `console.PROMPT`), and re-parsing and
    re-validating the whole catalog once per module would be four reads of the
    same file for one answer. The entry is a pydantic model nothing in this
    app mutates, so the shared instance is shared data and not shared state.

    Raises:
        KeyError: `catalog.json` has no `wow-vanilla` entry. Deliberately not
            caught: every module here is bound to that entry, so a package
            that imported without one would be a controller for nothing.
    """
    return load_catalog().get(GAME_ID)
