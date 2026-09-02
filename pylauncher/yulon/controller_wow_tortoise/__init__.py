"""Tortoise controller — server management for the CMaNGOS-shaped `wow-tortoise` entry.

Sibling of `controller_wow_wotlk/`, and deliberately about a tenth its size. That
package was written when WotLK was the only game, so each of its modules spells
its own facts as Python literals (`ac-database`, `acore_auth`, `AC>`, `ready...`).
Every one of those literals is wrong here, and every one of them already has a
typed home in `catalog.json` — so this package holds NO game literal at all. It
reads the `wow-tortoise` entry (see `game.py`) and binds the shared behaviour to
what the entry says.

What differs from the WotLK package, by module:

* `docker_ctl.py` — the containers are `tortoise-*` and the world port is the
  entry's, both from `CatalogEntry.container_spec()`. The entry names no
  one-shot import service, so this package offers no `repair_import`.
* `controller.py` — `wait_ready()` is overridden, the one lifecycle method that
  had an AzerothCore fact compiled into the base class: it waits for
  `ready...`, which a mangosd never prints. The markers come from
  `install.native.ready`.
* `accounts.py` — the entry's `accounts.scheme` is `mangos_sha`, so the row is
  `sha_pass_hash` and the GM level is `account.rank`. The arithmetic and the
  statements for all three schemes already live in the WotLK module; only the
  binding is here.
* `console.py` — `mangos>`, printed AFTER the answer (an `fgets` console, not a
  readline one). The parser already takes both facts as arguments.
* `maintenance.py` — backup/restore are game-agnostic once they are handed a
  `ContainerSpec` and this core's three schema names; both defaults in the
  shared module are AzerothCore's.
* `repair.py` — the whole probe is different in KIND, not in constants. The
  WotLK probe reads AzerothCore's `updates`/`updates_include` bookkeeping
  tables, which no CMaNGOS core writes. This family records its own completion
  marker (`sqlplan.MARKER_TABLE` in the plan's `marker_db`), and
  `sqlplan.MarkerGate` is the probe that reads it — so this module wires that
  gate rather than re-answering the question.
* there is no `modules.py`. That module binds a manifest tree at
  `manifests/<game>/`, and `manifests/` holds `wow-wotlk` only; the entry says
  `has_manifests` false, and every caller already asks the entry before
  building a store. A copy pointed at a directory that does not exist would be
  a feature that fails at the first click.

Public entry points, for the code that wires the Server tab:

    game.GAME / game.entry() / game.db() / game.schemas() / game.plan_schemas()
              / game.core_databases() / game.ready_markers() / game.sql_plan()
    docker_ctl.SPEC / ready_spec() / ready_spec_from() / wait_server_ready()
                    / wait_db_healthy_ready() / port_conflicts_here()
    controller.TortoiseController / controller.controller_for()
    accounts.scheme() / sql_for() / sql_for_install() / create_account()
    console.send() / attach() / can_send() / NO_TTY_HELP
    maintenance.mysql_for() / backup() / backups_dir() / plan_restore() / restore()
              / interrupted_restore() / forget_interrupted_restore()
    repair.import_probe() / import_state() / marker_gate()

Nothing here has been run against a live Tortoise server by the agent that
wrote it. Every fact it uses is either read from `catalog.json` or already
carried a measurement in the module it is bound to; where neither is true, the
docstring says so in that spelling rather than asserting.
"""
