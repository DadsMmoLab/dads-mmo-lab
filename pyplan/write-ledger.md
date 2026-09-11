# The write ledger

Every place `pylauncher/yulon/` can change something outside this process: a
database, a file, a directory. One row per `module::function::callee`, which is
what `pylauncher/tests/test_write_ledger.py` enumerates over the syntax tree.

**The test fails in both directions.** A write that is not in this table fails
it, so a new one cannot arrive unnoticed; and a row here that no longer resolves
to a call fails it too, so a deleted write cannot leave a line behind claiming
it still happens. One direction alone rots: the first lets the code outgrow the
table, the second lets the table outlive the code.

**Three shapes this walk could not see until 2026-09-08.** A retrospective audit
found the page above claiming completeness over a walk with three holes in it,
and eight write sites behind them — the table said "every place", the test agreed,
and neither could see the most destructive command the app runs. They were:

* **`os.write`.** A file descriptor is an integer, and the walk was looking for
  paths. Two sites, and one of them is the furthest-reaching write in the
  package: `console.py` puts a GM command into a running worldserver, and
  `.account set gmlevel`, `.character rename` and `.reset level` all change the
  database on the other side of it.
* **An `open()` whose mode is computed.** `_mode_of()` answered `""` for any mode
  that was not a literal and the caller read `""` as "not writing" — so
  `part.open("ab" if resumed else "wb")`, which is how every download lands, was
  a read. An unknown mode is the one thing a walk must not guess about, and it
  now answers `?`.
* **`docker volume rm`.** The destruction is not a Python call: it is an argv
  handed to a subprocess. A volume holds every character on an install and one
  command deletes it with no undo, and the walk's entire vocabulary was `shutil`
  and `Path`. Matched on the argv rather than on the function that runs it, so a
  rename cannot walk past it.

**Why the table is keyed by function and not by line.** A ledger keyed to line
numbers is rewritten by every edit above it, and a table that churns is a table
people stop reading. Two writes of the same kind in one function are one row.

**What counts as a write, and what deliberately does not.** The callee list is
in `tests/write_sites.py` with its reasoning. Two choices worth repeating here:
`mkdir` is not a write, because creating an empty directory puts no content
anywhere and content still needs one of the calls that is listed; and a bare
`.replace(` with exactly one positional argument IS a write, because
`Path.replace` is how five modules save atomically — the first version of the
walker excluded every bare `.replace(` to keep `str.replace` out and silently
lost seven real writes with them.

**"World may be running"** is the column owner answer 7 (2026-09-06) is about:
no Phase 8 feature writes `characters` or `world` while the world server is up.
A row reading "**yes, and unguarded**" is a write that predates that answer and
is named here rather than quietly grandfathered; 8.7 is where the applier's
guard lands.

**The applier's guard landed on 2026-09-09**, and the two `_run_sql` rows below
now say something narrower than "guarded", because that is what is true. Three
things a reader has to carry away from them:

* **The guard is a capability, not yet a defence.** `Applier` takes a
  `world_running` seam and `_refuse_direct_sql_into_a_running_world()` refuses
  the whole action — before the first statement, so the *no rows written* half
  of `checklist.md:2501` is true of the action and not merely of the step that
  tripped it. But **no shipped caller passes the seam yet**: the four
  `controller_<acronym>/modules.py` appliers are built without it, and with the
  seam absent the behaviour is byte for byte what it was
  (`phase8-designs/c-operators-risk.md:345` requires exactly that). Until a
  caller wires it, every row below still reads "yes" in practice.
* **`db-import` is a different route with a different guard.** Those steps write
  nothing here; they are resolved into `ApplyReport.pending_sql` and applied by
  `docker.apply_module_sql()`, which has had the running-world refusal all along
  (`docker.py:2029-2036`). That guard could not be reused — it needs a
  `ContainerSpec`, a compose project and Docker, and `apply.py` touches none of
  them — so there are two enforcement points for one rule, and the applier's
  sentence deliberately echoes Docker's so a user meets one rule and not two.
* **`acore_ale` is not covered, and no page says whether it should be.**
  `apply.WORLD_HELD_DBS` is `{characters, world, playerbots}`: the union of what
  owner answer 7, `checklist.md:2501` and `c-operators-risk.md:90` name. The ALE
  schema lives inside the worldserver process, so the reason the other three are
  guarded plausibly reaches it, and one shipped step targets it
  (`manifests/wow-wotlk/ale/paragon.json`, install-time). **Owner question:**
  does answer 7 extend to `acore_ale`? Left running rather than decided here.

One correction to the record while these rows were rewritten: the count of
direct SQL steps across the shipped manifests is **44**, in 30 files across all
four games — not the one `mod-arac.json` step an 8.7a brief named. `applied_by`
defaults to `"direct"` (`manifest.py:136`), so every `sql` step that names no
route is one, and `all-stackables` alone ships three on install and two on
remove for TBC, Tortoise and Vanilla. Measured by loading every manifest through
`parse_manifest`, not by grepping for the string.

**A second button reached the character database on 2026-09-09, and this walk
cannot see it.** T14 wired the Modules tab's *"Apply pending database
updates…"* to `native.StagedInstaller.update_databases()`, which streams the
install plan's `rerun_on_marked` phases into the databases through
`sqlplan.apply()` — and that goes out on `Seams.exec_stdin`, a `docker exec`
with the SQL on stdin. **No row below covers it**: the callee list is
`run_statement`/`run_file` plus the `Path`/`os`/`shutil` calls, and this write
is an argv handed to a subprocess, the third shape the audit above found itself
blind to. So the walk answers "no new write" for a press that writes DDL into a
database with somebody's characters in it, and the honest place to record that
is here rather than in a row the test would then call stale. **The whole import
path has the same hole** — it is not new with this button, only newly reachable
from one.

What is true of the press: it refuses unless `docker.world_running()` answers an
explicit `False`, before the database is started and again after (a Start
pressed during the health wait would otherwise put a live world behind the
statements); it refuses unless the databases already read as a finished import,
which is what keeps it out of the two arms of `stage_import()`'s table that
would import everything (`absent`) or `DROP DATABASE` over every schema the plan
names (`partial`) — the ordinary import is unreachable on this route rather than
guarded on it; and it writes no completion marker and re-asks no `verify` rule.
In this column it reads **no — the press refuses while the world is running,
twice asked**. Closing the walk's blindness means teaching it `exec_stdin`, which
is bigger than this ticket and is named rather than done.

**T26's account link is the fourth shape this walk cannot see, and it is one
this ticket CHOSE** (2026-09-10, round 2). "Link an account…" in My Party writes
two rows into `acore_playerbots.playerbots_account_links` — `(master's account,
the other account)` and the reverse — and it goes out through
`apply.DockerSql.query`, the read half's own method, so the callee list above
does not count it and there is no row below to find. The reason is the review
that asked for it: the write has to be transactional AND read back, and
`DockerSql` runs one statement per `docker exec`, i.e. one mysql session — so
`START TRANSACTION`, the `INSERT IGNORE` of both rows, `SELECT ROW_COUNT()`, the
readback of both directional rows and `COMMIT` are one script, and only the
returning runner can carry its answer back. Sent through `run_statement()` the
script would still write, and the app would have no way to tell "this press
wrote them" from "somebody else's did" or from "neither row is there" — which
is the false success the review found. A row here naming `run_statement` would
now resolve to no call and fail this page's other direction, so the honest place
is this paragraph.

What is true of that write: it is refused before it starts unless the account
name matches `party.ACCOUNT_SHAPE`, the master's own account resolves, the other
account exists in `acore_auth.account`, and the two are not already linked in
both directions (one row is repaired, not refused); it never updates and never
deletes; the ids come from `acore_auth` and nothing the user typed reaches a
query unescaped; and it is reported as linked only when the readback inside the
transaction saw both rows. It arrives through `party.SqlWriter`, a seam of its
own that one function holds — `dbreads.SqlReader`, which every read in `party.py`
goes through, still cannot write, and that is what the split is for. **World may
be running: yes**, and the argument is the module's own: the running world writes
this same table through `.playerbots account link`
(`mod-playerbots PlayerbotMgr.cpp:1840-1885`, the same `INSERT IGNORE` in both
directions) and reads it back with a fresh `SELECT 1` on every add
(`IsAccountLinked`, `:191-196`), so there is no cached copy for a row written
beside it to fall out of step with. **Owner question**, left running as
`acore_ale`'s is: does answer 7 extend to a `playerbots` table the server writes
live through its own command?

**A third button reached the same seam on 2026-09-10, and this one HAS a row.**
T19 wired the Modules tab's *"Adopt as imported…"* to
`native.StagedInstaller.adopt_as_imported()`, which writes the install's
completion marker through `sqlplan.write_marker()` — the same `docker exec` with
SQL on its stdin, and so the same blind spot. The difference is what the write
IS. The paragraph above is about a press that streams whatever files a plan
names, and teaching the walk to see that means teaching it the whole seam. This
one is a single function with a single spelling, and what it writes is the row
every later press reads as *this import finished* — written here on a person's
word rather than after an import. A ledger claiming "every place" while missing
the one write a user can now ask for by name would be claiming the wrong thing
about the most consequential row in the app. So `tests/write_sites.py` learned
the name `write_marker`, exactly as it learned `docker volume rm` by its argv,
and `catalog/families/cmangos.py::write_import_marker::write_marker` is below.
One row and not two, because both routes that record a finished import — the
ordinary one and the adopt press — go through that one function; a second call
site would be a second spelling of a row the probe reads in one shape only. The
rest of `exec_stdin` stays open and stays named.

Generated rows are checked against the tree by the test, not by hand. The
descriptions are written by hand.

| site | writes | world may be running? |
|---|---|---|
| `apply.py::_apply_patch::write_text` | a patched source file in the clone | install time |
| `apply.py::_client::shutil.copy2` | one file into the user's WoW client folder | yes |
| `apply.py::_client::shutil.copytree` | a directory into the user's WoW client folder, at `Interface/AddOns/<name>`. Reached by `wow-wotlk`'s BMAH keg and, since T30, by `wow-tortoise`'s two Turtle addons -- that game's tab passes no client dir at all until T30, and passes one only for a folder that already has an `Interface/` (`controller_view._client_dir_for_addons`) | yes |
| `apply.py::_conf::shutil.copy2` | a module's conf template into the server's etc | yes |
| `apply.py::_deploy::replace` | a deployed file renamed onto its final name | yes |
| `apply.py::_deploy::shutil.copy2` | one deployed module file into the server dir | yes |
| `apply.py::_deploy::shutil.copytree` | a deployed module directory into the server dir | yes |
| `apply.py::_rm::shutil.rmtree` | a directory a manifest's `rm` step names | yes |
| `apply.py::_rm::unlink` | a file a manifest's `rm` step names | yes |
| `apply.py::_run_sql::run_file` | a module manifest's `.sql` file, into the database its step names | **guardable since 8.7a, and unguarded for every caller shipped today** — `_refuse_direct_sql_into_a_running_world()` refuses the action when a `world_running` seam says the world is up (or cannot say) and any of the action's direct steps names `characters`, `world` or `playerbots`; no caller passes that seam yet, so in the app as it ships this is still **yes**. `auth` and `acore_ale` are outside the guard. A crash still leaves a multi-file step half applied — the refusal prevents starting, not tearing |
| `apply.py::_run_sql::run_statement` | a module manifest's inline SQL, into the database its step names | **as the row above** — the refusal is a pre-pass over the action's steps, so an inline statement that is FIRST never reaches the runner either; this is the site 8.7a's clause is written about, and `all-stackables` sends three of these to `world` on one install |
| `apply.py::_set_conf_key::write_text` | one key in a server conf file, byte-preserving elsewhere | yes — the file changes now, the value arrives at the next world start |
| `apply.py::install::touch` | the marker that records a module as installed | yes |
| `apply.py::remove::shutil.rmtree` | the clone of a module being removed | yes |
| `apply.py::write_clone_claim::os.replace` | the claim file renamed into place | yes |
| `apply.py::write_clone_claim::unlink` | the temp claim file after a failure | yes |
| `apply.py::write_clone_claim::write_text` | the clone-claim file, to a temp name | yes |
| `catalog/composegen.py::write_dotenv::os.replace` | `.env` renamed into place | install time |
| `catalog/composegen.py::write_dotenv::unlink` | the temp `.env` after a failure | install time |
| `catalog/composegen.py::write_dotenv::write_text` | the merged `.env`, to a temp name | install time |
| `channel_setup.py::save_credential::os.open` | **new (8.2a)** this install's channel credential, created owner-only by its open flags rather than by a later chmod | yes — it is written after the round trip answers, which is after the world is up |
| `channel_setup.py::enable::write_text` | **new (8.2a)** the generated override, rewritten with the keys that turn this install's command channel on | **no — the press refuses while the world is running**, which is the whole shape of that step |
| `channel_setup.py::roll_back::write_text` | **new (8.2a)** the override put back exactly as it was before the first press, out of the copy that press kept | **no** — it undoes a press that itself refuses while the world runs, and it is reached from a start that failed |
| `channel_setup.py::roll_back::unlink` | **new (8.2a)** that copy, removed once it has been restored, so the next press backs up the state the next press finds | **no** — same moment |
| `channel_setup.py::_write_the_conf::write_text` | **new (8.2c)** two writes: this install's own `etc/mangosd.conf` with the keys that turn SOAP on, and — once, on the first press — a `.before-channel` copy of it beside it. The CMaNGOS lineage reads no environment, so on those trees this file IS the channel | **no — the same press, and the same refusal**: the world reads this file on the way up, and a failed SOAP bind is `exit(-1)` with no character saves |
| `channel_setup.py::_restore_the_conf::write_text` | **new (8.2c)** an inverse PATCH of the keys this app owns -- each set back to what the backup says, and one the install never had removed -- leaving every other line of a 70 kB user-editable file exactly as it is. It restored the whole file until an adversarial review pointed out that the backup can be arbitrarily old, which is the argument already written down for the override | **no** — it undoes a press that refuses while the world runs |
| `channel_setup.py::_forget_the_conf_backup::unlink` | **new (8.2c)** the `.before-channel` copy of the conf, removed **last** -- after the conf, the override and the `.env` have all been put back. It used to be unlinked inside `_restore_the_conf()`, which left a failure on either later step with nothing to retry from; the override's copy already made this argument and the conf's did not (adversarial review, 2026-09-07) | **no** -- it finishes an undo of a press that itself refuses while the world runs |
| `controller_wow_wotlk/accounts.py::reset_own_password::run_statement` | **new (8.2a, shared since 8.2d)** a new credential for THIS APP'S OWN account, in the columns the SCHEME names -- `salt`/`verifier` for AzerothCore, `v`/`s` for the CMaNGOS trees, which bind this same function rather than copying it, refusing every name that does not carry the app's prefix | yes — it is the `auth` database, not `characters` or `world`, and the account it rewrites is one nobody plays |
| `catalog/composegen.py::write_plan::write_text` | the rendered compose files in the server dir | install time; a running stack keeps what it started with |
| `catalog/families/conf.py::_clear::shutil.rmtree` | a staging directory being cleared | install time |
| `catalog/families/conf.py::_clear::unlink` | a staged file being cleared | install time |
| `catalog/families/cmangos.py::write_import_marker::write_marker` | **new (T19)** the install's COMPLETION MARKER: one row in `<marker_db>.yulon_install`, carrying this install plan's hash and the time, plus the `CREATE TABLE IF NOT EXISTS` that makes the table on an install that has none. Two callers and one spelling: the ordinary import writes it at the end of a successful one, after `verify()` passed; the Modules tab's *"Adopt as imported…"* press writes it on the person's word, for a server this app did not install. It is the smallest write in this table and the furthest-reaching after `console.py`'s -- every later press reads it as *this import finished*, and nothing removes it. **Invisible to this walk until T19**, for the reason the page's own prose gives: it leaves this process as an argv with SQL on its stdin. The walk was taught this one function's name rather than the whole `exec_stdin` seam, which is bigger than one ticket; the rest of that seam is still open and still recorded above | **no -- both callers refuse while the world is running.** The import route reaches it at install time, before `up`; the adopt press reads `docker.world_running()` twice, before the database is started and again immediately before the row goes out, and refuses on anything but an explicit `False` |
| `catalog/families/cmangos.py::_write_secret::os.open` | **the install's generated database password**, into `<server_dir>/.env`'s companion file, owner-only at creation. Invisible to this ledger until 2026-09-07, when the walk widened | install time |
| `catalog/families/conf.py::_write::os.open` | the replacement conf, to a temporary file beside it, owner-only at creation — the rename is the row below | yes |
| `catalog/families/conf.py::_write::os.replace` | a conf file renamed into place | yes |
| `catalog/families/conf.py::_write::unlink` | the temp conf file after a failure | yes |
| `catalog/families/conf.py::materialise::os.chmod` | that conf file's mode | install time |
| `catalog/families/conf.py::materialise::shutil.move` | a `.dist` conf copied out of the image, `.dist` stripped | install time |
| `catalog/families/conf.py::materialise::shutil.rmtree` | the staging directory afterwards | install time |
| `catalog/families/dockerfile.py::write::write_text` | a generated Dockerfile in the clone | install time |
| `catalog/families/extract.py::_remove_tree::shutil.rmtree` | an extraction directory being replaced | install time |
| `catalog/families/extract.py::write_evidence::os.replace` | that evidence file renamed into place | install time |
| `catalog/families/extract.py::write_evidence::unlink` | the temp evidence file after a failure | install time |
| `catalog/families/extract.py::write_evidence::write_text` | the extraction evidence file, to a temp name | install time |
| `catalog/families/patch.py::apply::write_bytes` | a patched file in the clone | install time |
| `catalog/native.py::_put_recipe_back::write_bytes` | **new (T8)** the `Dockerfile` and `.dockerignore` PUT BACK exactly as this press found them, when a rebuild is stopped or fails before the containers are replaced. It writes no new content: the only bytes it can write are the bytes it read out of those two files before the first stage ran, so its whole effect is undoing `dockerfile.write()`'s row above | **yes** — the world is running throughout this window, and that is the point of the row: nothing reads these two files except `docker build`, so putting them back changes nothing the server is using |
| `catalog/native.py::_put_recipe_back::unlink` | **new (T8)** one of those two files removed, in the one case where the ground had no such file and the re-render created it. Same undo, same window | **yes** — as above |
| `catalog/native.py::write_state::os.replace` | that record renamed into place | install time |
| `catalog/native.py::write_state::unlink` | the temp record after a failure | install time |
| `catalog/native.py::write_state::write_text` | the install's own stage record, to a temp name | install time |
| `controller_wow_wotlk/console.py::send_command::os.write` | **found 2026-09-08** one GM command line, into the pty the worldserver's console is attached to. **The furthest-reaching write in this table, and it names no file at all**: what goes down this descriptor is whatever the caller built, and 8.3–8.5's commands (`.account set gmlevel`, `.character rename`, `.reset level`, `.send items`) each change the `auth` or `characters` database from inside the server. The row exists here because the ledger is about what can change outside this process, and a descriptor is as much outside it as a path is | **yes, and necessarily** — there is no console to write to unless the world is up, which is the opposite of every other row's argument. The safety is not a refusal but the server's own: the world thread applies the command under its own locks, which is exactly why owner answer 7 sends changes through the console instead of through SQL |
| `runner.py::_write::os.write` | **found 2026-09-08** bytes into the stdin of whatever child this runner is driving, when that child is on a pseudo-terminal. The generic half of the row above — this is the transport, and `send_command()` is one caller of the shape. What it can change is therefore whatever the child does with the bytes | **yes** — the subprocesses this drives include an attached console on a running server |
| `docker.py::remove_volume::docker volume rm` | **new (8.9a), and invisible to this ledger until 2026-09-08** — the single most destructive thing in the package. It deletes a named volume: **every character on that install**, in one command, with no undo, and with nothing left on the filesystem to recover from. Deliberately not a flag on `remove_staged()` and deliberately not `compose down -v`, both argued at the function; the name must come from `project_volumes()`, and the removal is confirmed by re-asking rather than by an exit code | **no — the purge refuses while any container of the project is running**, asked of `docker.running_census().ours` before any command is issued. It is also reached only from an uninstall the user has confirmed, and only for the volume the "keep my characters" answer did not spare |
| `docker.py::remove_image::docker image rm` | **new (8.9a)** one built image by its exact reference, one call per ref, never a compose flag. `--rmi all` would take `mysql:8.4`/`mariadb:11` with it and those are shared with a neighbouring install, which is why the refs are enumerated from `composegen.built_image_refs()` instead. A refusal is a warning and not an error here | **no** — same press, after the same refusal. Nothing a player made is in an image |
| `docker.py::remove_staged::docker compose down` | this install's containers, removed. **No `-v`, ever** — the volumes are untouched and the button's copy says so — and `-t STOP_GRACE_SECONDS`, because at Docker's 10 s default a populated worldserver is SIGKILLed mid-drain and the save queue is what is lost | **yes, and that is the point**: it is offered on a RUNNING server under copy promising the characters survive. The grace is what makes that copy true |
| `docker.py::remove_staged::docker rm` | the by-name fallback for containers `compose down` left behind, and only names the project-label census already proved are ours. Each is stopped with the full grace FIRST: `rm -f` on its own is a SIGKILL with no drain at all, which would leave a hard-kill path reachable from the button whose copy promises the characters survive | **yes** — as above |
| `docker.py::copy_from_image::docker rm` | the throwaway container this function created a moment earlier to copy a file out of an image, removed in a `finally`. It writes nothing of the user's and it is the only row here about a container this app made itself; its own failure is logged rather than raised, because the copy's error is the one that explains anything | n/a — the container is this function's own, and no install's server is inside it |
| `platform.py::_download_urllib::open(?)` | **invisible until 2026-09-08** — the downloaded body, into the `.part` file, appended when the server honoured a `Range` request and truncated when it did not. The mode is a conditional rather than a literal, which is exactly why the walk could not see it. Every client and emulator archive this app fetches in-process lands through this call | n/a — a download into the app's own cache, before anything is installed |
| `controller_wow_wotlk/accounts.py::_account_row::run_statement` | an account row in the auth database (create, and its counters) | yes |
| `controller_wow_wotlk/accounts.py::_run::run_statement` | the auth database, for the account statements this module builds | yes |
| `controller_wow_wotlk/maintenance.py::_dump_one::open(wb)` | one database dump, to a `.partial` | yes — a hot backup |
| `controller_wow_wotlk/maintenance.py::_dump_one::os.replace` | that dump renamed once it verifies | yes |
| `controller_wow_wotlk/maintenance.py::_dump_one::unlink` | the `.partial` after a failed dump | yes |
| `controller_wow_wotlk/maintenance.py::_write_marker::os.replace` | that marker renamed into place | no — as above |
| `controller_wow_wotlk/maintenance.py::_write_marker::unlink` | the temp marker after a failure | no — as above |
| `controller_wow_wotlk/maintenance.py::_write_marker::write_text` | the interrupted-restore marker, to a temp name | no — restore refuses while the server is up |
| `controller_wow_wotlk/maintenance.py::forget_interrupted_restore::unlink` | the marker, when the user chooses to forget it | yes — it only removes the record |
| `controller_wow_wotlk/maintenance.py::restore::unlink` | the marker, once the restore finished | no — as above |
| `controller_wow_wotlk/repair.py::reset_unfinished::run_statement` | `DROP DATABASE` on a half-imported schema | no — refused outright on a populated database |
| `dbsecret.py::remember::os.open` | **new (8.9a)** the database password of an install being uninstalled with "keep my characters" ticked, into Yu'lon's own config directory, owner-only at creation. It is a COPY of `<server_dir>/.db_password`, made because the same action deletes the folder that file is in and keeps the volume it opens; a reinstall to the same folder is filed under the same `<game>-<install id>` and reads it back. Nothing removes it | **no — the purge refuses while any container of the project is running**, and this write happens before the first destructive step of one |
| `docker.py::pin_project_name::os.replace` | `.env` renamed into place | yes |
| `docker.py::pin_project_name::unlink` | the temp `.env` after a failure | yes |
| `docker.py::pin_project_name::write_bytes` | the compose project pin appended to `.env`, to a temp name | yes |
| `git.py::_sparse_clone::write_text` | the clone's sparse-checkout list | install time |
| `git.py::clone::shutil.rmtree` | a non-git leftover at the clone destination | install time |
| `logsnap.py::_prune::unlink` | **new (8.1a)** older snapshots of this install, never the one just written | yes — as above |
| `logsnap.py::capture::os.replace` | **new (8.1a)** that snapshot renamed once the bytes are down | yes — as above |
| `logsnap.py::_discard::unlink` | **new (8.1a)** the `.partial` after a failed snapshot, through a helper that cannot itself raise | yes — as above |
| `logsnap.py::capture::write_text` | **new (8.1a)** the worldserver log snapshot, to a `.partial` | yes — it runs immediately before the stop |
| `manifest_store.py::_fetch_one::replace` | that manifest renamed into place | n/a |
| `manifest_store.py::_fetch_one::unlink` | the ETag file when the server answers without one | n/a |
| `manifest_store.py::_fetch_one::write_bytes` | a downloaded manifest, to a temp name | n/a |
| `manifest_store.py::_fetch_one::write_text` | the manifest's ETag file | n/a |
| `module_source.py::_write_atomically::write_text` | **new (8.7)** a manifest this app DERIVED from a link or a folder the user supplied, or that family's index — to a temp name beside the real one. One function rather than two copies of the tmp/rename dance, because `persist()`, `_rewrite_index()` and the rewrite `forget()` triggers all need it and a second spelling of an atomic write is the duplicate style-guide §4 forbids. It is the app's own config directory, never a server dir and never a database | n/a — the file lives under `config_dir()`, and it is read at the next list, not by a running server |
| `module_source.py::_write_atomically::os.replace` | **new (8.7)** that temp file renamed over the real name. The whole reason the function exists: this app writes these files and reads them back on every start, and a HALF one does not parse — a user index or item that does not parse is a `ManifestError`, which the Modules tab draws as `!! could not load modules: …` with **no list at all**, so one torn custom file takes every shipped module off the screen and the only way back is deleting a JSON file by hand. `write_clone_claim()` made this argument first | n/a — as above |
| `module_source.py::_write_atomically::unlink` | **new (8.7)** that temp file after a failed write, so a refusal leaves no debris under a name nothing will ever read | n/a — as above |
| `module_source.py::forget::unlink` | **new (8.7)** the persisted manifest of a custom module being removed. A shipped manifest is an OFFER and stays listed whether or not it is installed; a custom one is a RECORD of something the user brought, and a record of a folder that is gone would be a list entry whose Install re-clones a link the user already decided against. It runs **after** `Applier.remove()` returned, never before — a refused remove keeps the record, so the module is still reachable — the ordering `purge.py` uses for `state.forget()` | n/a — the app's own record; the module's folder was already removed by the applier |
| `module_source.py::copy_folder::shutil.copytree` | **new (8.7)** the module folder the user chose, copied to `modules/<id>` — the applier's second way to fill that directory, where a shipped module gets a clone. `.git` is excluded deliberately: a copy is a SNAPSHOT, and one carrying the author's `.git` would make 8.7a's update check report a commit count against a remote the user never chose. It refuses a source inside the destination's own `modules/` | yes — it is the same moment as a clone, and nothing the server has open is written; the module does nothing until the rebuild the report asks for |
| `module_source.py::copy_folder::shutil.rmtree` | **new (8.7)** whatever was at `modules/<id>` before that copy. The destination is REPLACED rather than merged into, because choosing the same folder again is how a newer version is brought over and a merge would leave files the newer version deleted sitting in the module for the next rebuild to compile. Only reached after the applier's own ownership check said this app put that folder there | yes — as above |
| `networking.py::apply::run_statement` | the realmlist row: the address a client is sent to | yes |
| `networking.py::record_network_intent::os.replace` | that record renamed into place | yes |
| `networking.py::record_network_intent::unlink` | the temp record after a failure | yes |
| `networking.py::record_network_intent::write_text` | the network-intent record, to a temp name | yes |
| `networking.py::write_client_realmlist::write_text` | `realmlist.wtf` in the user's client folder | yes |
| `party.py::_save::write_text` | **new (T26 live half)** `AltbotMemory`'s own file, `party-altbots.json` under `platform.config_dir()` — the names My Party added as bots through "Add this character", per install and per master. NOTHING of the user's is in it and nothing outside this app reads it: the server keeps no record of a `bot add` anywhere an app can read (measured 2026-09-11, `pyplan/gates/8.6-altbot-live-yulon-ubuntu2-2026-09-11/22-discriminator.log`), so without this file the party list cannot contain what the control just put in it. It is in the app's config directory and NOT in the server folder, so an uninstall has nothing extra to purge. A write that fails is logged and the record reads empty — the panel draws either way | yes — it is this app's own bookkeeping and touches no game data at all |
| `party.py::deploy::shutil.copy2` | **new (8.6)** My Party's Lua bridge scripts, into `env/dist/etc/modules/lua_scripts` under the server folder. Six files the app ships since T26 added `dml_botadd.lua` (five before it); nothing of the user's is read or overwritten, since the destination is a directory only this feature writes | yes — and deliberately: the copy is safe while the world runs because the Lua engine reads that directory when it STARTS, which is why `deploy()` returns `changed` and the caller owes a restart |
| `platform.py::_download_curl::unlink` | the `.part` file after a failed download | n/a |
| `platform.py::download_verified::replace` | a verified download renamed onto its final name | n/a |
| `purge.py::remove_tree::shutil.rmtree` | **new (8.9a)** the whole server folder of the install being uninstalled, and the largest single write in this table. Twice in one function is one row: the plain delete, then the retry after `_clear_read_only()`. It NEVER swallows a failure - the Rust prior art's `let _ = remove_dir_all(...)` reports a successful uninstall on Windows having deleted nothing | **no - the purge refuses while any container of the project is running**, asked of `docker.running_census().ours` before any command is issued |
| `purge.py::_clear_read_only::os.chmod` | **new (8.9a)** the write bit, back onto every file and directory under the folder the delete has already failed on once. Git writes packs and loose objects read-only and Windows honours that attribute, so a bare `rmtree` stops partway and leaves a checkout that is neither an install nor absent | **no** - same moment, after the same refusal |
| `purge.py::_remove_unenterable::os.rmdir` | **new (8.9a, Windows half, 2026-09-08)** one directory entry at a time under the folder the delete has already failed on: a reparse point Python cannot enter (WSL-made symlink, `IO_REPARSE_TAG_LX_SYMLINK`, measured on yulon-win11) or a directory whose mode refuses `scandir`. `rmdir` removes the link and never its target, and refuses a non-empty directory, so the worst it can do is nothing | **no** - same moment, after the same refusal |
| `state.py::load_state::replace` | an unreadable `state.json` moved aside to a backup | n/a |
| `state.py::save_state::replace` | `state.json` renamed into place | n/a |
| `state.py::save_state::write_text` | `state.json`, to a temp name | n/a — the app's own record |
| `steam.py::_backup::shutil.copy2` | **new (8.8)** a copy of `shortcuts.vdf` — and of Steam's own `config/config.vdf` — beside the original as `<name>.yulon-bak-<stamp>`, before either is touched. Two files through one function is one row. Both belong to the Steam account signed in on the machine and neither was written by this app; the `shortcuts.vdf` one may hold shortcuts a person added by hand years ago, and Steam offers no undo | n/a — the Steam profile, not the server. The process this write refuses under is **Steam**, asked of `pgrep -x steam` before anything is copied |
| `steam.py::_write_artwork::write_bytes` | **new (8.8)** six PNGs into `userdata/<id>/config/grid/`: three of Steam's four slots (`<appid>.png`, `<appid>p.png`, `<appid>_hero.png`) for each of the two entries. `_logo` is deliberately not written — Steam draws it instead of the entry's NAME, measured on `yulon-arch` — and the two entries get differently coloured marks, because the library grid draws no name at all. The bytes are drawn by `icon_png()` rather than read from a file, since this app ships no images anywhere; the file names are the appids, which is why an upsert must not recompute one it is replacing. Two calls in one function is one row | n/a — as above |
| `steam.py::_write_compat::write_text` | **new (8.8)** Steam's global `config/config.vdf`, with one `CompatToolMapping/<appid>` block inserted or its `name` changed and **every other byte preserved** (`compat_mapping()` returns the block it inserted so the test can take it back out and compare). 17,977 bytes of Steam's own state on the box this was read from — websocket tables, shader-cache buckets, the depot list — none of which this app understands well enough to reformat. Written to a `.yulon-tmp` sibling, and with `errors="surrogateescape"` and `newline=""` because that is how it was READ: one byte that is not UTF-8 raised on a strict write, and universal newlines would have rewritten a CRLF file LF-only, which is every line of it and would make this row's own promise false | n/a — as above |
| `steam.py::_write_compat::replace` | **new (8.8)** that temp file renamed onto Steam's global config. The rename is the write; everything above it is preparation. A truncate-then-write interrupted by ENOSPC or a SIGKILL leaves Steam's whole configuration empty, and this app would have done it while adding three lines | n/a — as above |
| `steam.py::_write_shortcuts::write_bytes` | **new (8.8)** `userdata/<id>/config/shortcuts.vdf`: the whole binary document, rewritten with this install's two entries upserted by `AppName` and everyone else's left where they were, into a `.yulon-tmp` sibling. Written last, after both backups and after the payload has been round-tripped through this module's own parser and its `08 08` terminator checked — a document missing that second byte makes Steam drop **every** non-Steam shortcut in the library | n/a — as above |
| `steam.py::_write_shortcuts::replace` | **new (8.8)** that temp file renamed onto `shortcuts.vdf`. The reason this is not a plain `write_bytes` is the reason the terminator is checked: a **truncated** shortcuts file does the same damage as a mis-terminated one — Steam drops every non-Steam shortcut, the user's hand-made ones included — and `write_bytes` truncates before it writes. A rename cannot leave that state. `state.py:126-131` is the same pattern | n/a — as above |
