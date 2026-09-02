"""TBC controller — the CMaNGOS-shaped siblings of `controller_wow_wotlk/` (roadmap 7.9).

Each server gets its own controller package so game-specific behavior stays
isolated. This one manages the `wow-tbc` catalog entry: CMaNGOS mangos-tbc with
the cmangos/playerbots fork, three containers named `tbc-db` / `tbc-realmd` /
`tbc-mangosd`, four MariaDB schemas (`realmd`, `characters`, `mangos`, `logs`)
and a worldserver console that is not AzerothCore's.

**Almost nothing here is an implementation.** The WotLK package's account
crypto, console transport and backup/restore engine are already game-agnostic —
they take the scheme, the prompt, the container spec and the schema names as
arguments — so this package imports them and supplies THIS game's arguments
from `catalog.json`. What is written out here is only what AzerothCore's
version cannot answer:

* `docker_ctl.ready_spec()` — CMaNGOS prints `Avg Diff:`, never `ready...`, and
  the entry names no auth marker at all, so `docker.azerothcore_ready()` is
  wrong in both halves.
* `controller.TbcController.wait_ready()` — the base class calls
  `azerothcore_ready()` directly, so inheriting it unchanged would poll a
  mangosd log for a line it never prints until the 480s default ran out.
* `repair.py` — AzerothCore's probe reads `updates`/`updates_include`, the two
  tables ITS updater keeps. CMaNGOS's core has no such pair, so the evidence
  here is the `yulon_install` marker row this app's own installer writes
  (`catalog/families/sqlplan.py`), and the one question that evidence cannot
  answer is stated rather than guessed at — see that module.

There is no `modules.py`. Module/manifest management is driven by
`manifests/<game>/`, only `manifests/wow-wotlk/` exists, and the entry's
`has_manifests` is false — so a TBC manifest store would be a directory listing
of nothing wearing the name of a feature.

Public entry points, for the view that dispatches on `entry.id`:

* `controller.TbcController(server_dir, *, wsl_distro=...)`
* `docker_ctl.SPEC`, `.ready_spec()`, `.wait_server_ready()`,
  `.wait_db_healthy_ready()`, `.port_conflicts_here()`, and the shared
  `start`/`start_staged`/`stop_staged`/`remove`/`status`/`health` re-exports
* `accounts.create_account()`, `.sql_for()`, `.SCHEME`
* `console.send_command()`, `.attach()`, `.PROMPT`,
  `.PROMPT_PRECEDES_ANSWER`, plus `ConsoleReply`/`ConsoleError`/`can_send`
* `maintenance.mysql_for()`, `.backup()`, `.plan_restore()`, `.restore()`,
  `.CORE_DATABASES`, plus the shared report types
* `repair.import_state()`, `.import_gate()`, `.db_password()`,
  `.reset_unfinished()` (which raises — read its docstring before wiring it)
"""
