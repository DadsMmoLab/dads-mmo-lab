"""Centurion controller -- server management for a `trinitycore` install (T179).

Centurion is a TrinityCore 3.3.5 fork (thomasjteachey/TrinityCore112, branch
`CENTURION`): level-60 PvP with its own bot module built into the worldserver.
It is the first entry of the `trinitycore` family, next to AzerothCore (WotLK)
and CMaNGOS (TBC, Vanilla, Tortoise).

**Every function here takes the catalog entry.** The other per-game packages
read `load_catalog().get(GAME)` at import, because their entry always ships.
This package is built before the `wow-centurion` entry lands in `catalog.json`
(T179 Task 7), so a module-level read would fail the import of every module
that imports this one; and the entry the view holds is the authority on THIS
install anyway (`controller_view._sql_for()` says the same of the containers).

What is this core's own, and where each fact comes from:

* **Containers, schemas, client.** `entry.container_spec()`, `entry.schema_map()`,
  `install.native.db.client` (`mysql`: Centurion's tables use
  `utf8mb4_0900_ai_ci`, which only MySQL has).
* **The ready marker.** `install.native.ready`, through `docker_ctl.ready_spec()`;
  the base controller's `wait_ready()` waits for AzerothCore's `ready...`, which a
  TrinityCore worldserver never prints (`controller.CenturionController`).
* **Accounts.** The shared writer's `trinitycore` scheme: SRP6 `salt`/`verifier`
  and the level in `account_access(AccountID, SecurityLevel, RealmID)`.
* **Console.** `TC>` (`CliRunnable.cpp:42`), read with GNU readline exactly as
  AzerothCore's -- its parent -- is, so the prompt is redisplayed in front of the
  answer; both facts come from the entry's `console` block.
* **Backup.** The shared engine, which dumps with `--routines --triggers --events`:
  Centurion's auth schema carries six triggers and two views, its characters and
  world schemas carry procedures (facts §2), and a dump without them restores a
  server whose realm-list updates no longer audit and whose tournament kit is gone.
* **Characters.** `characters.withheld()`: the verbs T208's live check watched a
  Centurion server run are offered; the rest are not drawn, each with the
  sentence that says why. Revive is offered only on a server whose build carries
  the fork's console fix (T218, `characters.revive_is_fixed()`).

What it does not have, said rather than stubbed: no one-shot import service (the
import is the install engine's marker-gated SQL plan, as on CMaNGOS, so there is
no Repair button to wire and no `repair.py`), no add-on modules (the Modules tab
says so), and no My Party (AzerothCore's Lua bridge; WotLK only).
"""
