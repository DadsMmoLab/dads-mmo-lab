# Phase 8 design panel — the maintainer's verdict

> Written 2026-09-06 against the three designs in `scratchpad/designs/{seam,surface,risk}.md`,
> the pages under `C:\Users\perzi\dml-phase8\pyplan\`, and the tree at
> `C:\Users\perzi\dml-phase8\pylauncher\` (tip `7bc5ebd3` for `pylauncher/`). Every claim about a
> design cites its section or line in that design; every claim about the tree cites `path:line`,
> paths relative to `pylauncher/`, each one opened and read. A = `seam.md`, B = `surface.md`,
> C = `risk.md`. My angle is the person who has to live with this for years: does it extend
> style-guide §3 without breaking a row, is every per-tree fact data on the catalog entry, is
> there one implementation of each thing, does "we reuse X" have a call path, and what does a
> fifth server or a fifth feature cost.

---

## 1. Scores

Seven criteria, 1–5, 35 max.

| Criterion | A — seam | B — surface | C — risk |
|---|---|---|---|
| 1. Style-guide fit (§3 table, one job per module, no `if game ==`) | **5** | 5 | 3 |
| 2. DRY — one seam, one implementation of each thing | **5** | 3 | 4 |
| 3. Testability (argv by field, one-rule negative fixtures, second-press) | **5** | 4 | **5** |
| 4. Operator safety on a live server (Q7, serialisation, server states) | 4 | 2 | **5** |
| 5. Per-family correctness (nothing inherited; Tortoise on its own terms) | **5** | 3 | 4 |
| 6. Gate quality (live state, from the launcher, never a CLI or exit code) | 4 | **4** | 3 |
| 7. Blast radius and incremental delivery | **5** | 3 | 3 |
| **Total** | **33** | **24** | **27** |

**Ranked: A (33) › C (27) › B (24).**

---

## 2. The ranked verdict

**A — the server-side seam — chosen.** It is the only design in which a fifth server is a JSON
block and a gate, and a fifth feature is one `CommandSpec` row plus one function in `gm.py`. It is
the only one that carries every per-tree fact — prompt, security level, command text, arity,
fault shape, items-per-mail cap, table and column names, bot marker, and *which channel exists at
all* — as typed `_Strict` data on `CatalogEntry.ops` (A §3), and the `_Strict` base it names is
real (`catalog/catalog.py:30`), as is the `Console` model it inserts after (`catalog.py:966`). Its
reuse claims have call paths: `create_account(sql, name, pw, *, gm_level, scheme)` exists with
exactly the three schemes A cites — `Scheme = Literal["azerothcore", "mangos_srp6", "mangos_sha"]`
(`controller_wow_wotlk/accounts.py:176`, `:312-318`), `MAX_USERNAME = 17` / `MAX_PASSWORD = 16`
are at `accounts.py:93-94` so A §4's `yulon_<install_id>` arithmetic (6 + 8 hex = 14) holds,
`install_id()` is at `composegen.py:135`, `write_plan()`'s marker rule at `composegen.py:692`,
`DEFAULT_WORLD_ENV` at `composegen.py:100` with the merge A proposes to extend at
`composegen.py:361`, `conf.patch()` at `families/conf.py:130` and `apply_table()` at `:243`,
`container_state()` at `docker.py:1883`, `published_bindings()` at `docker.py:2302`. Its §7 is
the only blast-radius section that answers the maintainer's actual question: it routes the SOAP
env through `ops.channels.soap.enable.env` and a new `render_override()` rather than through
`install.native.azerothcore.world_env`, precisely so `tests/data/wotlk-rendered/` and
`tests/data/wotlk-compose-config.json` stay byte-identical and 7.1's fixture assertion keeps
meaning what it meant. It is also right that no CMaNGOS compose-config fixture exists — `tests/data/`
holds only `wotlk-compose-config.json`, `wotlk-compose-config-script.json`, `wotlk-rendered/` and a
PEM.

**C — the operator's risk — second, and the source of most grafts.** It is the best document in
the set on the two things that actually break a live server, and it is the only one that noticed
the Playerbot Command Server is still listening. Its §3 write ledger (17 added/inherited writes
with "Running? / Crash mid-write leaves / Second press / Who asserts") is the artefact a
maintainer wants in five years, and `test_write_ledger.py` (C §8) is the single best test idea in
the three designs: an AST enumeration that fails both on a write site absent from the ledger
**and** on a ledger row that resolves to no site. That is rule 7 done in both directions. It
loses the phase on one thing: `yulon/commands/verbs.py` (C §2) holds the per-tree command texts
in Python.

**B — the user's surface — third, and still owed grafts.** It is the best design on the surface
architecture — call down / signal up applied by name, the two new tabs as sub-views so
`ui/controller_view.py` (1937 lines today) grows by the wiring only, one `OutcomeLabel` widget so
no tab can quietly collapse three outcomes into two, and the only design that says who tears a
tab down when 8.9 finishes. Its test table (B §7) is the only one whose "(**exists**)" citations
all resolve: `tests/test_controller_view.py:1240`, `:1873`, `:664`, `:696`, `:378`, `:1060` and
`tests/test_docs_pins.py:55` are each what B says they are. It loses on the server side, where it
has a fatal gap and two invented-by-omission facts.

---

## 3. The fatal flaw in each

### B — fatal. `SOAP.IP` does not appear in the document.

B §3.1 (`surface.md:150`): "the seam writes the config (AC: `AC_SOAP_ENABLED=1` into the override
env, the same mechanism as `AC_AI_PLAYERBOT_MAX_RANDOM_BOTS`, `catalog.json:57-59`,
`Config.cpp:435-438`; TBC/Vanilla: `SOAP.Enabled = 1` through `families/conf.py`)". That is the
whole mechanism B specifies. A grep of `surface.md` for `SOAP.IP` or `SOAP_IP` returns **zero**
hits; A returns 8 and C returns 4.

The fact it contradicts: the shipped default is `SOAP.IP = "127.0.0.1"` (AC
`worldserver.conf.dist:460`; TBC `mangosd.conf.dist.in:1859` — cited by A §3 and by C §4.2), which
inside the container is the container's own loopback. A published port arrives on the container's
bridge address, so a listener bound to the container's `127.0.0.1` never accepts it. B's 8.1a
therefore ends at "commands: could not ask — connection refused" forever, and B's own DoD
(`surface.md:409`: "one `curl` `server info` with the file's credentials") cannot pass. B §8 lists
eleven risks worth re-reading and this is not one of them.

Secondary, and cheap to fix: B names the helper account `yulon`, a fixed string (§3.1,
`surface.md:163`, `:165`). B §8 (`surface.md:505`) then books the consequence as an accepted risk
— "A user who names their own account `yulon` will not see it listed" — and B §8 (`surface.md:496`)
separately enumerates bug-checklist line 227, two installs of one game sharing container names.
`install_id` is path-derived (`composegen.py:135`); a per-install name costs one f-string and
removes both.

### C — fatal to *this* criterion. The per-tree command table lives in Python.

C §2 (`risk.md:44`): "`yulon/commands/verbs.py` — The closed set of command texts per tree as
data: verb → (text template, min tree level, console allowed, offline allowed, item cap, the DB
read that proves it ran)", with "Must never: … hold a per-game literal outside the table."

A per-game literal *inside* a Python table is still a per-game literal outside `catalog.json`.
The rule it contradicts is the project's own, in two places: style-guide §3's
`catalog/families/cmangos.py` row — "Must never: contain a game literal (**asserted over code via
`ast`**)" — and §3's rule of thumb, "Manifests hold data, code holds behavior. If a piece of
information could be different for a different mod/module/game … it belongs in a JSON manifest …
not in a Python conditional." It is not a conditional; it is the same fact in the same place a
conditional would have been. C §11 confirms the intent by proposing a *new* style-guide row for
`commands/verbs.py` rather than extending the `catalog/catalog.py` row. Price when the fifth
server arrives: a Python edit, a review of Python, and a new test, against A's one JSON block that
`load_catalog()` refuses on a typo.

Second, smaller: C §2 (`risk.md:49`) says "`yulon/apply.py` — **add** a `running:` seam on
`Applier` (after `sql`, `:453`-region)". `apply.py:453` is `@dataclass(frozen=True)` immediately
above `class DockerSql` (`:454`). `Applier` is at `apply.py:771`, `__init__` at `:779`, and its
`sql` parameter at `:784`. The design's one structural edit to the module applier points at the
wrong class.

### A — no fatal flaw. Its worst defect is the bot clause.

A §2 gives `dbreads.py` the function `bot_clause(entry)` — the catalog entry and nothing else —
and A §3 declares `BotMarker.prefix_conf: str | None  # where the live prefix would be read from`.
That comment is the whole of it: nothing in A reads the installed conf. The fact it contradicts
is `phase8-delta.md:64`, which records two live values for the same key on the CMaNGOS lineage
(dist `RNDBOT`, code default `rndbot`, `PBC PlayerbotAIConfig.cpp:939`, `:500`), and the project
memory `bot-population-is-500`, which is the record of a catalog value not being the value in a
running container. A's own validator (`account_prefix: Field(min_length=1)`) checks the *catalog*
value, not the one the server is using. This is rule 6 — a guard that proves the value was
declared, not that it arrived — on the one fact this project already has a memory file about.
Consequence if shipped as drafted: on an install whose `aiplayerbot.conf` carries the other
spelling, `dbreads.list_bots` returns 0 and `dashboard` counts 500 bots as human players, silently.

It is a one-signature fix (`bot_clause(entry, conf_text)`) and both B and C already carry the
correct behaviour, which is why it is a graft and not a disqualification.

---

## 4. The grafts the winner must take

### From C (`risk.md`), by name

1. **The write ledger (C §3) and `test_write_ledger.py` (C §8).** An `ast` walk over `yulon/` that
   enumerates every `run_statement`, `run_file`, `os.replace`, `Path.write_text`/`write_bytes` and
   `open(..., "w")` site and fails on one absent from a committed ledger — *and* fails on a ledger
   row that resolves to no site. A's `test_q7_writes.py` is the one-directional half.
2. **`AiPlayerbot.CommandServerPort = 0` in the same 8.1 press (C §3 A2, §4.2),** with the guard
   that proves it arrived: `ss -ltn` inside the world container showing no 8888. A refuses the
   Playerbot Command Server as a *channel* (A §4, `seam.md:467`) and leaves it listening; the
   delta records it bound `0.0.0.0:8888` inside the compose network at the pinned rev
   (`phase8-delta.md:35`). Zero hits for `8888` in `surface.md`; two in `seam.md`, both refusals.
3. **`Ra.Enable = 0` asserted, not assumed (C §4.2)** — the 8.1 gate checks no listener on 3443.
   Zero hits for `Ra.Enable`/`3443` in both `seam.md` and `surface.md`.
4. **The server-state matrix's "What it never does" column and the `Foreign server on 7878` row
   (C §4.1),** which reads the core name out of `server info`'s first line. A's matrix (A §4) has
   no "another server answered" outcome at all.
5. **The console-pending ↔ typed-command interlock, in both directions (C §2):** a typed command
   is refused while `_console_pending` is true and vice versa, because both reach one world queue
   (`phase8-delta.md:35`, fact 4). The flag is real at `ui/controller_view.py:1341`, `:1345`.
6. **`CommandQueue` with an observable `depth`/`in_flight` (C §2), and the drain-before-stop
   rule.** A has a per-install `threading.Lock` and no visible depth.
7. **`BotClause` reading the live prefix from the installed conf, refusing on an unreadable or
   empty prefix, and requiring `^[A-Za-z0-9_]+$` before it enters SQL (C §2, §4.6).** This is the
   repair for A's defect above. Combine with B's warning: **refuse** when the conf could not be
   read, **warn** when it read and matched nothing while `characters` has rows, list when it
   matched — three outcomes, not two.
8. **File mode asserted on the `os.open` fd flags, not on a later `chmod` (C §8
   `test_credentials.py`),** and **`install_id` in the log-snapshot filename (C §3 A7)** so two
   installs of one game do not overwrite each other's evidence. A's name is
   `world-<ts>-<game>-<reason>.log` and cannot tell them apart.
9. **"Evidence before" per feature (C §4):** the live rows the press is about to change, captured
   before and again after.
10. **Spikes S1 and S4 (C §9).** S1 — set the two keys on yulon-ubuntu's 7.2 install, restart the
    world, `curl server info` from the host — is the owner-gated measurement of A's load-bearing
    `0.0.0.0` claim. S4 — read `TW AccountMgr::CheckPassword` — decides whether Tortoise has a
    set-password at all; A and B both ship one without asking.
11. **The `db-import` finding (C §4.8):** `applied_by: db-import` steps are logged "left to
    ac-db-import on next start" and `start_staged` never runs the one-shot, so on an installed
    server those steps are never applied. A's 8.7 does not know this. `run_one_shot()` exists at
    `docker.py:1357`.

### From B (`surface.md`), by name

1. **`Outcome`, `ChannelState`, and `ui/widgets/outcome.py` (`OutcomeLabel`, `ChannelBanner`)
   (B §2.1, §2.3)** — one widget, every tab, "so the three outcomes cannot be collapsed by a tab
   that forgot one". A types the three-valued `Answer` in the seam and leaves the rendering to
   each tab, which is where the third outcome goes missing.
2. **Sub-views `ui/characters_view.py` and `ui/bots_view.py` that emit `action_failed` and never
   reach up (B §2.1)** — style-guide §5 in the one file that most needs it.
   `ui/controller_view.py` is 1937 lines; A adds two tabs inside it.
3. **`uninstalled` signalled up; `main.py` owns `drop_controller` and `state.forget()` runs last
   (B §2.1, §2.4).** `drop_controller` is at `main.py:189`, `add_controller` at `:216`, the
   `action_failed.connect` B adds after at `:275`, `state.forget()` at `state.py:82`. A's 8.9
   says "as the decisions page" and never says who removes the tab.
4. **Two-press arming for the set-up press when the world is up (B §3.1),** reusing `REMOVE_IDLE`/
   `REMOVE_ARMED` (`ui/controller_view.py:628-629`) and `_disarm_actions()` (`:1160`), with the
   paragraph naming the disconnect and the save drain. A recreates the world container on a
   single press (A §4, 8.1 "How the switch reaches a running install").
5. **The probe-cadence ownership rule (B §2.4):** the poll owns the cadence, the probe owns the
   meaning; asked at most once per three polls while `status.world`, never again once `ready`.
   A says "never on the timer" and then owes a rule for when it *is* asked.
6. **`refresh_status()` once at construction (B §2.1, §3.2)** — one line after
   `ui/controller_view.py:732`, closing bug-checklist line 552.
7. **The user never reads "SOAP" (B §3.1)** — "commands: ready" on the tab, the acronym in the log
   only (style-guide §6), and one tooltip that explains the helper account.
8. **The per-family gate splits `8.1a/b/c`, `8.3a/b/c`, `8.4a/b/c` (B §5)**, so a Tortoise fact
   can never tick a WotLK box, plus the in-client screenshot per group as the visible effect.
9. **B's four owner questions (B §11)** — in particular q3 ("may a fresh install run 8.1 itself at
   the end of `ready`, so a new user never presses Set up?") and q4 ("does an `8.x` box tick on
   Linux alone, with the Windows press its own line, as 7.7 did?"). A asks neither.

### Owed by all three

`yulon/ownership.py` exists and no design mentions it. It is the project's canonical three-outcome
answer to "whose folder is this?" — `Ownership.UNCLAIMED` / `OWNED` / `UNKNOWN` — in a module with
no dependencies, written after a corrupt state file made the installer *more* confident than a
missing one, and its docstring says in as many words: "That is the part that must not be
re-invented with two values" (`yulon/ownership.py:1-13`, `:21-40`). 8.9's `plan()` must return
`Ownership`, not a bool and not a sentence, and the new `Answer`/`Outcome` type should be read
against it before a fourth three-valued enum enters the tree.

---

## 5. Kickoff §6 checklist, run over each design

Failures only, by design and by line.

### A — `seam.md`

| §6 check | Failure | Line |
|---|---|---|
| "every 'Yu'lon already has X' resolves to a symbol at the pinned SHA" | The transport table cites `apply.DockerSql.run_statement()` at `apply.py:530-585`; `run_statement` is at **`apply.py:498`** and `:530-585` is `_mysql()`. The same row cites `_mysql()` at `:595-622`; that range is **`_argv()`**. The seam is real, two of three ranges are mislabelled. | `seam.md:67` |
| same | `catalog.json:57-59` for `world_env`/`AC_AI_PLAYERBOT_MAX_RANDOM_BOTS`; the keys are at **`catalog.json:58-60`**. | `seam.md:60`, `:443` |
| same | `maintenance.py:934-946` for `plan_restore()`'s refusal. There is no `yulon/maintenance.py`; it is **`controller_wow_wotlk/maintenance.py`**, one of four per-game copies, and the world/auth-up refusal is at **`:920-932`**. A design that names "`maintenance.restore` is the only characters/world write" must say which of the four. | `seam.md:547`, `:620`, `:649` |
| same | `docker.project_containers(pinned_project_name(...))`, `docker.py:390`, `:804` — the two are **swapped**: `pinned_project_name` is at `:390`, `project_containers` at `:804`. | `seam.md:477` |
| "no step's DoD can be met from a CLI or a script; it is met from the launcher" | 8.1d's gate is "queue dry run — `INSERT` a `server info` row, `docker logs --since` shows …". That is a hand `INSERT`, not a press; the §6 checklist line for 8.1 does not say who queued the row. | `seam.md:591`, `:611` |
| "every DoD names live state" | 8.6's arrival guard is "`yulon_ping` answers **or** `docker logs --since <reload time>` containing `[yulon_ping] loaded`", with "the 8.6 gate decides which one". Two satisfiers, one of them a marker (rule 1). Honest, but the DoD must pin the chosen one before the box ticks. | `seam.md:530`, `:616` |
| "no step assumes WotLK's mechanism holds for a CMaNGOS server" | Vanilla is defined as "the `wow-tbc` block with these deltas … Everything not listed is the TBC value". Self-flagged two lines later, and the design does gate them separately, but the unread Vanilla columns get no `arity_verified`-style marker of their own. | `seam.md:324-333` |
| — | Nothing anywhere closes `AiPlayerbot.CommandServerPort` or asserts `Ra.Enable = 0`. | (absent) |

### B — `surface.md`

| §6 check | Failure | Line |
|---|---|---|
| "every 'needs Y' names a mechanism and where it was verified" | The 8.1 mechanism omits `SOAP.IP` entirely (see §3 above). Fatal. | `surface.md:150` |
| "every 'X cannot happen' claim has been turned into a measured price or an enumerated route" | "attach and SOAP are different transports on the same world queue, so they never share a tty and **cannot** corrupt each other's reply." No price, no route. | `surface.md:108` |
| "every DoD names live state" (rule 1) | 8.1a's DoD leads with the app's own words — "the banner reads 'commands: ready'; a Stop turns it to 'shutting down'". Rescued by the same line's `SELECT`/`curl` readbacks, but the first clause is the artefact. | `surface.md:409` |
| "no step assumes WotLK's mechanism holds for a CMaNGOS server" | §3.4 says "**one item per press on every tree**, so the 12-vs-1 cap … never reaches this button", then the same table prints "Mail set (19 mails)" for Vanilla/Tortoise. The cap does reach the surface; the two rows disagree. | `surface.md:259`, `:264` |
| "every 'Yu'lon already has X' resolves" | `catalog.py:931-943` for "the scheme `accounts.py` already measured": `class Accounts(_Strict)` starts at `:931` but the `scheme` field is at **`catalog.py:946`**. | `surface.md:216` |
| same | `maintenance.py` mis-path, as A. | `surface.md:444` |
| — | No seam-level Q7 guard: B §8 says "the seam should refuse too, so a CLI cannot do what the button will not" and then designs only the view gate. | `surface.md:501`, `:112` |
| — | Nothing anywhere closes 8888 or asserts `Ra.Enable = 0`. | (absent) |

B passes the "no test names cited that do not exist" check cleanly — the six `(**exists**)` rows
in §7 all resolve — and it is the only design that does so by line.

### C — `risk.md`

| §6 check | Failure | Line |
|---|---|---|
| "no test names are cited that do not exist … nothing scoped now may name a test that is not in the tree" | The exit-criteria checklist box requires "`test_write_ledger.py` green". That file does not exist at `7bc5ebd3`. | `risk.md:314` |
| "every DoD is unsatisfiable by … an absent capture" | The same exit box requires "every box above ticked **with its gate directory present**" — capture-existence as a satisfier, in the same sentence that forbids it. | `risk.md:314` |
| same | 8.4's DoD: "`Update time diff … max` before/after each verb **is in the capture**". Same shape. | `risk.md:308` |
| "every 'Yu'lon already has X' resolves" | `apply.py:453` for the `Applier` constructor — that line is `@dataclass(frozen=True)` above `class DockerSql`; `Applier.__init__` is at `:779`, `sql` at `:784`. | `risk.md:49` |
| same | `controller.py:174-181` for `wsl.is_running` — the call is at **`controller.py:151`**; `:174-181` is `port_conflicts()`'s docstring. | `risk.md:140` |
| same | `docker.py:2121-2219` for "`ReadySpec.world`" — the ready scan is there, but `ReadySpec` is defined at **`docker.py:2048`**. | `risk.md:132` |
| same | `docker.py:1748` for `compose stop -t 300` — the call is at **`:1750`**; `:1748` is the comment above it. `cmangos.py:161` for `.db_password` — `:161` is `SECRET_FILE_MODE = 0o600`, the docstring is `:162`. | `risk.md:55`, `:154` |
| "no step assumes WotLK's mechanism holds for a CMaNGOS server" | The gate table says "one CMaNGOS box" for 8.2, 8.3, 8.7 and 8.9. TBC and Vanilla are different trees at different revisions (`mangos-tbc f82e7d67`, `mangos-classic 8ec338a1`, `phase8-delta.md:19`); the design insists on Vanilla only where the 1-item mail forces it. | `risk.md:289`, `:290`, `:293`, `:294` |
| — | A11 refuses `playerbots` SQL as well as `characters`/`world`. Q7's words are "characters or world" (`phase8-parity-decisions.md:40`). Stricter, not contrary — but it is a scope extension the owner was not asked for; put it to them rather than shipping it silently. | `risk.md:49`, `:90` |

C's §3 "**Q7 holds**" (`risk.md:98`) reads as a guarantee, which rule 8 forbids — but it is
discharged by `test_write_ledger.py`'s two-directional enumeration, which is exactly rule 8 done
right. It passes.

---

## 6. Where the three contradict each other on a fact

| # | The dispute | A | B | C | Which is right, and why |
|---|---|---|---|---|---|
| 1 | Where the channel credentials live | `config_dir()/channels/<game>-<install_id>.json`, 0600 (A §2, §4) | `<server_dir>/.yulon-commands.json`, 0600, "beside `.db_password`" (B §3.1, `surface.md:161`) | `config_dir()/credentials/<game>-<install_id>.json`, 0600 via `os.open` (C §2, §3 A5) | **A and C.** `pyplan/README.md` §11 is the app's local-data contract and `config_dir()` is real at `platform.py:68`. The server dir is what 8.9 deletes, and on Windows it may be WSL-resident (`KnownInstall.wsl_distro`, `state.py:24-39`), where a host-side 0600 is not the host's to set. Only the `config_dir()` form keys by `install_id` (`composegen.py:135`), which is what makes two installs of one game distinguishable — the exact case bug-checklist line 227 records. Take C's fd-flag assertion. |
| 2 | Does the Console tab move to SOAP? | Yes, when the SOAP channel is verified; attach otherwise (A §2, `seam.md:60`) | No — Send stays on attach with its own flag (B §2.4) | No, flatly: "No SOAP Console tab … Moving free text onto a channel with no level check is a surface decision" (C §10, `risk.md:379`) | **A on the fact; C on the ordering.** C's stated reason is not a reason: the console it keeps free text on has no level check either. `phase8-delta.md:34` (fact 3) records that AC "runs the command with **no** level check because a console handler has no session" and that CMaNGOS SOAP "runs at `SEC_CONSOLE` regardless of the caller" — and the stdin console is account id 0, i.e. the same `SEC_CONSOLE`. SOAP adds no privilege the tab does not already have, and it adds one thing nothing else can: a console on native Windows, where `can_send()` is False (`controller_wow_wotlk/console.py:156`) and the tab disables Send (`ui/controller_view.py:1302`). But it changes a Phase 7 surface that has a live gate (7.9), so it belongs behind `capability()` in its own sub-step, not inside 8.1a's DoD as A has it. |
| 3 | Must `SOAP.IP` be `0.0.0.0` inside the container? | Yes — a published port delivers to the container's bridge address, so a listener on the container's own `127.0.0.1` never accepts it; the loopback pin is the **host** binding at `base.yml.tmpl:264`, and `published_bindings()` refuses to persist credentials while 7878 is published anywhere else (A §3, §9) | Silent — zero mentions | Yes, and "load-bearing, **unverified**" with three enumerated routes and spike S1 (C §4.2, §9) | **A on the mechanism, C on the epistemics, B is a defect not a third position.** The dist defaults are `SOAP.IP = "127.0.0.1"` (`worldserver.conf.dist:460`, `mangosd.conf.dist.in:1859`) and the host pin already exists in the template (`catalog/installers/wow-wotlk/native/base.yml.tmpl:264`, confirmed at the tree; the cmangos template publishes no SOAP port at all — confirmed, zero matches in `catalog/installers/shared/cmangos/base.yml.tmpl`). Ship A's `0.0.0.0` + host `127.0.0.1` + A's refuse-to-persist guard, and run C's S1 before 8.1a's box ticks. |
| 4 | Is Tortoise's `pending_commands` used? | Yes — `QueueChannel`, only where no pty exists, refused unless the world is running and ready, its own unconsumed row deleted after `2 × poll_s`, every write paired with a verify read (A §2, §3, §4) | No — "a button whose answer arrives a minute later with no text is a question with one outcome" (B §3.1, `surface.md:169`) | No, ever, in this phase; `attach.py` "Must never … write to `tw_logon.pending_commands`" (C §2, §10) | **A, on B's own terms.** B's reason is wrong: a queued write paired with a verify read has three outcomes (the row appeared / it did not / the read could not be taken), and B itself uses verify-by-read for every other offline action. C's *hazard* is right — `TW World.cpp:2724` filters by time, not liveness, so a row landing on a stopped world runs at the next boot — and A is the only design that engages it and answers it. The queue is also the only channel Tortoise has on native Windows, which B raises as an open question (B §11 q1) and C leaves unanswered. Keep it, gated exactly as A specifies, delivered as A's own sub-step 8.1d so the owner can cut it whole if the answer to B's q1 is "Tortoise is Linux-only for v1". |
| 5 | Bot marker: fail-open, fail-closed, or from where? | `bot_clause(entry)` — the catalog value, with a non-empty validator on it (A §2, §3) | Read from the installed conf, "never assumed"; zero bots with characters present is a **warning** (B §3.5) | Read from the installed conf; **refuse to answer** on an unreadable or empty prefix; `^[A-Za-z0-9_]+$` before SQL (C §2, §4.6) | **B and C; A is wrong.** `phase8-delta.md:64` records two live values for the key and memory `bot-population-is-500` records that a catalog value is not the running value. A's `prefix_conf` is a comment about where the prefix *would* be read from, not a read — rule 6. The correct shape is all three outcomes: refuse when the conf could not be read (C), warn when it read and matched nothing while `characters` has rows (B), list when it matched (A). |
| 6 | Is a `server info` on a timer acceptable? | Never on the timer; the dashboard is `docker inspect` + MySQL, the probe is on demand (A §4, §10) | At most once per three polls while not ready, zero once `ready` (B §2.4) | Every 30 s, forever, priced at 1 command per ~190 world ticks (C §4.2) | **A, with B's ownership rule.** Every command channel queues on the single world thread (`phase8-delta.md:35`, fact 4); the dashboard's players and bots come from MySQL reads that bypass it, so a periodic command buys only liveness. C prices it honestly and its skip-if-overrunning budget rule is a good graft **for the reads**, but a forever-timer world-thread command on a 500-bot server is the thing not to add. C's `Update time diff` capture survives as gate evidence taken by hand around each verb, not as a UI poll. |
| 7 | What the app account is called | `yulon_<install_id>` | `yulon`, fixed | `YULON_<install_id>` | **A and C.** `install_id` is path-derived (`composegen.py:135`) so two installs of one game get different names; `yulon_` + 8 hex = 14 ≤ `MAX_USERNAME` 17 (`accounts.py:93`), verified. B's fixed name collides with the case B itself enumerates in §8 and with a user's own account, for nothing. Compare `UPPER(username)` on read either way — the prefix is compared upper-cased on every tree. |
| 8 | Where does the applier's Q7 guard live? | A refusal in `Applier.install()`/`remove()` before `_sql()` (A §4, 8.7) | A **view** gate — Install disabled while `status.world` is true (B §2.4, §3.7) — with §8 conceding the seam should refuse too | A `running:` seam on `Applier`, checked **inside** `_sql()` (C §3 A11, §8) | **C's placement.** `_sql()` is at `apply.py:1359` and delegates to `_run_sql` at `:1373`; it is the one point every caller passes through, so A's placement in `install()`/`remove()` misses any other `_sql()` caller and B's view gate is bypassed by anything that is not a button. B's own §8 says so. Note the scope question in §5 above: C guards `playerbots` too, which is stricter than Q7's words. |

---

## 7. What the fifth thing costs, under the winner

The question I score on, answered for A with the grafts applied.

- **A fifth server** (say a second WotLK fork): one `ops` block in `catalog.json` — channels,
  `gm_level`, `commands`, `bots`, `tables`, optionally `bridge` — validated by `_Strict` at
  `load_catalog()`, plus a `controller_<acronym>/` package that already has to exist for Phase 7,
  plus its own gate row. No Python in `soap.py`, `channel.py`, `commands.py`, `dbreads.py` or
  `gm.py` changes. Under C, add a `verbs.py` edit and a code review of Python; under B, add a
  `capabilities` block plus the same lines in a fourth `_for_*` factory for each of ~24 service
  fields.
- **A fifth feature** (say "set a character's guild"): one `CommandSpec` row per entry that has
  the command and `null` on the entries that do not, one function in `gm.py` shaped
  `capability → Command → channel.ask → verify read`, one group on the Characters sub-view, one
  row in the write ledger, one test in `test_commands.py` and one in `test_gm.py`. The `null`
  case draws the reason sentence with no extra code, because B's `not_drawn(reason)` and A's
  `capability()` are the same predicate.

The one place a fifth thing is *not* cheap under A is `ControllerServices`: it gains `channel`,
`channel_setup`, `dashboard`, `gm`, `bots`, `party`, `purge` — seven grouped seams, wired in four
factories. That is the right granularity (B's flat 24 fields is not), and
`tests/test_controller_packages_agree.py` already exists to police the four factories staying in
step.

---

## 8. Conditions on the winner before a line of Phase 8 code is written

1. `bot_clause` takes the installed conf's prefix and returns three outcomes (graft C-7 + B-8).
2. `SOAP.IP = 0.0.0.0` stays, and C's spike S1 runs with the owner's yes before 8.1a's box ticks.
3. `AiPlayerbot.CommandServerPort = 0` and the `Ra.Enable = 0` assertion join 8.1's press and gate.
4. The Q7 guard moves into `apply.Applier._sql()` (`apply.py:1359`), not `install()`/`remove()`.
5. The Console-tab SOAP route leaves 8.1a and becomes its own sub-step behind `capability()`.
6. The two new tabs become sub-views that signal up; `main.py` keeps `drop_controller`.
7. `purge.plan()` returns `yulon.ownership.Ownership`, not a fourth three-valued type.
8. Every `path:line` in §5's failure tables is corrected before the decisions page quotes it —
   in particular `apply.py:498` for `run_statement`, `apply.py:779`/`:784` for `Applier`,
   `controller_wow_wotlk/maintenance.py:920-932` for the restore refusal, and
   `catalog.json:58-60` for `world_env`.
