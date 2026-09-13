# Phase 8 — Lab and Hypeer parity: decisions and why

> Scoped 2026-09-06 on branch `yulon-phase8`, before Phase 7 exits, on the owner's decision of
> 2026-09-04 that Phase 8 is scoped now and its code waits for this scoping to be reviewed
> (memory record `phase8-kickoff-prompt.md`; the brief is `pyplan/archive/phase8-kickoff.md`). Companion to
> `pyplan/roadmap.md` §8, which is not edited; Appendix A holds the proposed §8 text. Sibling of
> `pyplan/phase8-decisions.md`, the uninstall/purge page of 2026-08-31, whose two owner answers are
> copied verbatim below as group (g) and which this page does not reopen.
>
> Method, the one `phase7-decisions.md` used: five read-only source reads produced
> `pyplan/phase8-delta.md` (one row per feature, mechanism measured per emulator tree at the
> catalog's pinned revisions; reports in `pyplan/phase8-reads/`); the owner answered the eight
> questions 1 to 8 below before any design existed; questions 9 and 10 were raised by the design
> round and answered on 2026-09-06 after the judges reported — answer 10 narrowed the winning
> design's channel list after it had won, and where the page still named the third channel is
> recorded under "Review findings". Three designs were written independently from
> three angles (`pyplan/phase8-designs/`) and scored by three judges (`pyplan/archive/phase8-judges/`).
> Every source read, design and verdict is committed beside this page, so what was rejected can be
> read rather than summarised.

---

## The owner's answers (2026-09-06)

Asked one at a time with `AskUserQuestion`, each with a recommendation first, before any design.
They are decisions, not preferences, and the rest of this page does not re-litigate them. The
option text is the owner's answer verbatim; where the owner typed a free answer, that text is
quoted first.

| # | Question | Answer |
|---|---|---|
| 1 | **Timing.** Does Phase 8 *code* wait for the Phase 7 exit box (Baerthe's), or may `8.1` start when its own prerequisites are met? | **"8.1 may start now, on yulon-phase8 stacked on #143"** |
| 2a | **Feature cut, group (a):** teleport, item mail, GM tools, gear sets. | **"v1 Phase 8"** — heal, summon-NPC and coordinate teleport were named in the question as refused unless Q7 or a Lua bridge changes that. |
| 2b | **Group (b):** My Party and Browse Bots. | **"v1 Phase 8, WotLK first"** |
| 2c | **Group (c):** character sheet (gear, 3D paperdoll, talent trees, achievements). | **"Phase 9"** |
| 2d | **Group (d):** Steam integration. | **"v1 Phase 8, Linux/Steam Deck only"** |
| 2e | **Group (e):** auto-stop when the client exits. | **"Later (after v1)"** |
| 2f | **Group (f):** doctor and shell. | **"Doctor in Phase 9; shell refused"** |
| 2g | **Group (g):** uninstall/purge, decided 2026-08-31 — does it stay in Phase 8? | **"Stays in Phase 8"** |
| 3 | **Family coverage.** All four servers before a box ticks, or WotLK first with the CMaNGOS-lineage servers as their own steps? | **"Mechanism verified per family before design; WotLK first in delivery; one box per family"** |
| 4 | **Bots on the CMaNGOS family.** Are bot features WotLK-only for v1? | **"My Party WotLK-only; Browse Bots on all four"** |
| 5 | **Client-side writes.** May a Phase 8 feature write into the client folder (an addon under `Interface/AddOns`, `realmlist.wtf`)? | **"Not allowed; server-side routes only"** |
| 6 | **Server-side channel.** Is SOAP on loopback plus an app-owned GM-3 account an acceptable prerequisite (WotLK, TBC, Vanilla; Tortoise has no SOAP)? | **"Yes: SOAP on loopback + an app account, as 8.1"** |
| 7 | **Database writes.** May a Phase 8 feature write to `characters` or `world` while the worldserver runs? | **"No direct writes to characters/world while running; reads are fine"** |
| 8i | **Added by the delta table — Hypeer-only rows:** live dashboard and the log snapshot before stop. | **"v1 Phase 8"** |
| 8ii | Accounts list, set password, GM level. | **"v1 Phase 8"** |
| 8iii | Module management beyond install/remove (update checks, manifests for the three CMaNGOS games, tuning knobs, config editor, settings page, account-wide sharing). | First **"Drop the account wide"**; on the clarifying question, **"Update checks + CMaNGOS manifests in Phase 8; knobs, editor, settings in Phase 9; account-wide refused"** |
| 8iv | Automatic backups, self-update of the server sources, single-instance guard, autostart, adopting a server folder the app did not create. | **"None in Phase 8: single-instance in Phase 9, the rest later or refused"** |
| 9 | **Vanilla's gates.** How are they met, given that the engine's `patch-sources` stage refuses a second press on any folder built before the doodad patch? | **"Fresh throwaway install on m910q, folded into 8.9's install"** — **the premise this question was put on was too strong, and the correction is recorded rather than the answer changed.** I told the owner no Vanilla install could reach ready anywhere. Checked on the box on 2026-09-06: a Vanilla server *is* running and ready on the test box at `/home/pk/vanilla-75b` (`vanilla-mangosd` started 08:19Z, restarts=1, logging `Avg Diff: 56. Sessions online: 0.`). What the engine refuses is a second *install press* on a pre-patch folder; that is not the same as the server being unusable. The answer still stands on its own terms — 8.9b needs a throwaway install regardless, so one compile still serves two boxes — but Vanilla's other gates could run against the server that is already up, and that is the owner's to choose. |
| 11 | **What "8.1" released.** The owner answered when the command channel was numbered 8.1; the review round then put observability first, so the channel is 8.2. Which was released? | **"The phase's first step, whatever it is"** — so work starts on observability and the channel follows, which is the ordering the reordering's own reason supports. |
| 12 | **Vanilla's gates, re-asked.** Question 9 was put on my claim that no Vanilla install could reach ready anywhere. Measured on the box on 2026-09-06, that was too strong: a Vanilla server is running and ready at `/home/pk/vanilla-75b`, and what the engine refuses is a second install *press* on a pre-patch folder. Does answer 9 stand? | **"Gate against the running server; keep the throwaway for 8.9b only"** — which removes a compile from six boxes and leaves the throwaway install where uninstall actually needs one to destroy. |
| 10 | **Tortoise's reach.** It has no SOAP and no remote console; on Linux its actions go through the attach console, on native Windows only through a 60-second queue that returns nothing. Which is v1? | **"Linux and macOS only for v1; Windows says why"** |
| 13 | **8.8's gate box.** Steam was the one step carried as `[blocked]`: no machine on this side had Steam, so neither the shortcuts-file read nor the gate could happen. Which machine gates it? | **"baerthe and daddycool have steamdeck with steamos"** (2026-09-06) — two machines, named. 8.8 stops being blocked on hardware and becomes an **assigned** box, the shape §7.8's macOS box already has. Neither Deck is reachable from this side, so *who* runs it is the owner's to assign; the hardware question is closed. |

### Group (g), copied verbatim from `pyplan/phase8-decisions.md` (2026-08-31)

| # | Question | Answer |
|---|---|---|
| 1 | Beyond the server folder and its Docker objects, what else should purge remove? | **Only the launcher's own state record.** Not firewall rules, not module files in the WoW client, not Docker itself. |
| 2 | Should characters survive an uninstall? | **One button with a "Keep my characters" checkbox**, unticked by default. Not two buttons; not an unconditional wipe. |

---

## The cut

What the answers put into Phase 8, in the step order the delta table proposes (the design round
may reorder; it may not add or remove a feature without a new owner answer):

| Step | Delivers | Families and platforms | Answer |
|---|---|---|---|
| 8.1a–d | Live dashboard (players, bots, uptime, restart-loop), the worldserver log snapshot before every stop, the bot-marker resolver and the write ledger — **first, because it is what makes the next step's failure legible** | all four; one box per family | Q8i, Q3 |
| 8.2a–e | The command channel: the listener bound to `0.0.0.0` **inside** the container and published on the host at `127.0.0.1` only (binding the container's own loopback is the defect this page rejects design B for), an app-owned GM-3 account verified by a round-trip **from the host**, a typed command layer with the per-tree facts as data | WotLK, TBC, Vanilla over SOAP; Tortoise stays on the attach console + MySQL reads (it has no SOAP) | Q6 |
| 8.3a–d | Accounts: list, set password, GM level | all four; one box per family | Q8ii, Q3 |
| 8.4a–d | Named teleport, item search, item mail, revive, set level, rename, mailed money, gear-set presets | all four; one box per family; Tortoise has no console route to an arbitrary level (searched exhaustively 2026-09-06), so that one control is not drawn there; Tortoise has no set-level and Vanilla/Tortoise mail one item per message | Q2a, Q3 |
| 8.5a–d | Browse Bots | all four; one box per family | Q4 |
| 8.6 | My Party | WotLK only, by a server-side route — the mod-ale Lua bridge over SOAP, which is the only route owner answer 5 leaves and which **has never been recorded working**: the one live note about it (`bridge.rs:189-195`) records it failing on 2026-08-20 with the deploy reporting success. 8.6's first question is whether the route works at all; no client addon | Q2b, Q4, Q5 |
| 8.7a–d | Module update checks; module manifests for TBC, Vanilla and Tortoise | all four | Q8iii |
| 8.8 | Steam integration | Linux / Steam Deck only | Q2d |
| 8.9a–b | Uninstall / purge, as `phase8-decisions.md` | all four; gated on WotLK and one CMaNGOS game | Q2g |

**Phase 9:** character sheet (Q2c); doctor (Q2f); tuning knobs, config editor, settings page
(Q8iii); single-instance guard (Q8iv); console history and autocomplete; a read-only realmlist
check.

**Later, after v1:** auto-stop when the client exits and keep-awake while the server runs (Q2e);
automatic backups, self-update of the server sources, autostart, adopting a foreign install (Q8iv).

**Refused:** a shell (Q2f); account-wide sharing (Q8iii); coordinate teleport (Q7); any client
folder write (Q5); heal and summon-NPC unless the bridge 8.6 adopts covers them (Q2a); `.additem`
(online-only on every tree); the Playerbot Command Server as a channel (unauthenticated, off the
world thread); Play Together (a third-party service).

**8.1 may start now** — answer 1 verbatim is "8.1 may start now, on yulon-phase8 stacked on #143",
which released that step and not the whole phase, and which declined the recommendation that it
wait for #143 to merge. It waits neither for that merge nor for the Phase 7 exit box; it still
waits for this scoping's review cycle (the 2026-09-04 decision), and the rest of Phase 8 follows
8.1.

---

## What this overturns, by name

| Where | What it said | Now |
|---|---|---|
| `pyplan/README.md` §9 | "My Party / bot group builder", "Item database + in-game mail", "Teleport / GM in-game tools" — out of scope for v1, "deferred, not refused" | Deliberate v1 expansion (Q2a, Q2b): each is an `8.x` step with its own definition of done. Struck through on that page with the date, the way the Linux-native line was. |
| `pyplan/roadmap.md` "Out of scope (do not start these in v1)" | the same three features, plus "Full native reimplementation of installers on Linux" (already overturned by Phase 7 and never struck there) | **Not edited.** Appendix A carries the replacement text for §8 and for that list. |
| `pyplan/roadmap.md` §8 "Ordering rule: Phase 8 must not begin until Phase 7 exits" | | Overturned by Q1 for the code (2026-09-06) and by the 2026-09-04 decision for the scoping. Appendix A rewords it. |
| `pyplan/phase7-decisions.md` "What the installer does not do" — "No account creation, no SOAP, no realmlist writer in the engine" | | Still true of the **install engine**. 8.2a enables the listener in the installed config and creates the app's account from the controller side; the engine's stage list does not change. |
| `pyplan/phase6-decisions.md` "Account creation — the finding that changes the plan": SOAP cannot create the first account | | Still true and load-bearing: 8.2a's app account is written through `accounts.py`'s SRP6 row exactly because of it. |
| `pyplan/checklist.md` Phase 8 preamble "[blocked] on Phase 7 … Scope still TBD" | | Replaced by the numbered `8.x` boxes; the ticked 2026-08-21 identification box is kept. |

---

## The decision

**Every Phase 8 feature asks a server one of two things — a question of its database, or a command
of its world thread — through one typed seam whose per-tree differences are data on the catalog
entry. `soap.py` is the wire; `channel.py` delivers a command over whichever channel the entry
declares — SOAP for WotLK, TBC and Vanilla; the attach console on Tortoise, where a pseudo-terminal
exists (owner answer 10; its command queue is refused, not built); `commands.py` builds the text from a
per-tree template and answers "can this server do this at all?" before a button is drawn;
`dbreads.py` is the read side over the `DockerSql` seam that already exists. Every answer has
three outcomes — yes, no, could-not-ask — and no feature writes to `characters` or `world` itself:
the server performs those writes through its own commands, on its own thread.**

Design **A** (`phase8-designs/a-server-side-seam.md`) is that design and is chosen. The surface is
design **B**'s, grafted nearly whole: two new tabs as sub-views that signal up, one `OutcomeLabel`
widget so no tab can quietly collapse three outcomes into two, and the two-press arm before
anything restarts a running world. The operating discipline is design **C**'s: a write ledger with
a test that enumerates every write site in the tree, the applier's missing guard on module SQL, a
bounded pre-stop log snapshot, and the rule that a gate captures the rows it is about to change
before it changes them.

---

## Why, and what was rejected

Three designs, three judges, seven criteria each scored 1–5. The scores are in Appendix B; the
reasoning that matters is here.

**B — the user's surface — rejected as the shape, kept as the surface.** It has the best surface
discipline in the set: at `7bc5ebd3` `ui/controller_view.py` was 1937 lines, and B was the only design that did
not add two more tabs inside it, the only one that says who tears a tab down when an uninstall
finishes (`main.py:189` keeps `drop_controller`; the view signals up), and the only one whose
"(exists)" test citations all resolve. What sank it as the shape is that its channel model is
built on a transport behaviour it never verified — **and the rebuttal this page first gave was
itself wrong, which the spike later caught.** B derives "the server is starting" from a connection
that "connects and blocks". The first version of this paragraph answered that the connect is
*refused*, because the SOAP thread is created only after `SetInitialWorldSettings()` returns
(`AC Main.cpp:315`, `:337-339`; `TBC/VAN Master.cpp:126`, `:248`). Those citations are right and the
inference was not. Measured 2026-09-06 with a container up, its port published and nothing listening
inside: the connection **succeeds and returns nothing**
(`pyplan/gates/8-spikes/published-port-vs-container-loopback/`). B's description was closer to the
truth than the rebuttal. What is true, and is why B is still not the shape, is that a connection
tells you nothing here at all — a misconfigured bind, a world still loading and a healthy-but-slow
server are one observation, and only the container's ready marker separates them — so a channel
model that derives "starting" from the connection cannot work, whichever way the connection goes. Two more: it omits `SOAP.IP` entirely, whose shipped default binds the container's own
loopback, which a published port does not serve — measured on 2026-09-06, and the failure is worse
than "cannot reach": Docker accepts the connection and relays nothing, so the misconfiguration
answers with silence rather than a refusal
(`pyplan/gates/8-spikes/published-port-vs-container-loopback/`); and it puts a GM-3 credential in the server folder, which is the folder the uninstall
deletes, the folder users copy, and on Windows may live inside a WSL distro where a host-side
0600 is not the host's to set.

**C — the operator's risk — rejected as the shape, kept as the discipline.** It is the best
document in the set on what breaks a live server, and it produced the single best test idea of the
three: a write ledger with an AST walk that fails both on a write site absent from the ledger and
on a ledger row that resolves to no site — a relationship asserted in both directions. It was not
chosen for two reasons. The first is placement: its `verbs.py` holds the per-tree command texts in
Python, which is the same fact in the same place a conditional would have been, against
style-guide §3's "manifests hold data, code holds behavior" and against the `families/cmangos.py`
row that forbids a game literal and asserts it over the AST. The second is a cost it pays forever:
C polls `server info` over SOAP every 30 seconds, on the three trees that have SOAP — it exempts
Tortoise, whose dashboard it makes reads-only. Every command channel queues on the single world
thread, and at this revision AzerothCore's SOAP loop accepts and processes one request inline
(`ACSoap.cpp:51-60`); C describes that loop as one thread per request, which is the one row of its
own concurrency table that is wrong, and its next row has TBC and Vanilla right. So a dashboard
tick queued behind a save drain holds the only accept loop while the user's next teleport waits in
the kernel backlog. A and B both refused a periodic world-thread
command, and they were right: players, bots and uptime come from database reads that bypass the
world thread entirely.

**A — the server-side seam — chosen.** It is the only design whose server-state model matches what
the trees do, the only one that carries every per-tree fact as typed data the catalog validator
refuses on a typo, and the only one in which a fifth server is a JSON block and a gate rather than
a Python edit. Its own fatal flaw was found and is fixed by a graft, not a redesign. The honest
cost, named by the maintainer judge: `ControllerServices` gains seven grouped seams wired in four
factories — the right granularity, and at `7bc5ebd3` `tests/test_controller_packages_agree.py`
already existed to keep the four in step.

### What the panel required of A before a line of code

Nine corrections. Each rests on a fact a judge or the orchestrator read in a tree, not on a
preference.

| # | A as drafted | The correction, and the fact behind it |
|---|---|---|
| 1 | The enable step recreates the world with a bare `docker compose up -d <world>` | It calls **`docker.start_staged()`** (`docker.py:702`). The worldserver declares `depends_on: ac-db-import: condition: service_completed_successfully` (`base.yml.tmpl:276-277`), so a bare `up -d` evaluates that dependency; the docstring records what that did before it was fixed — "was killing the database" (`docker.py:707-711`). |
| 2 | One press recreates a running world after a sentence | **Two presses, armed**, reusing the tab's own idiom (`ui/controller_view.py:628-646`), with the paragraph naming the disconnect and the up-to-300-second save drain. One press only when the world is down. Grafted from B. |
| 3 | The press writes the configuration and recreates a running world, guarded by a probe that the port is free | **A failed SOAP bind is fatal, and differently per tree.** AzerothCore logs and calls `World::StopNow(ERROR_EXIT_CODE)` — a graceful shutdown (`ACSoap.cpp:41-46`). TBC and Vanilla call **`exit(-1)` from the SOAP worker thread** (`MaNGOSsoap.cpp:43-47`), killing the process with no character saves; both templates set `restart: unless-stopped`, so that is a restart loop. A probe cannot make this safe: between checking the port and binding it there is a window, and a bind fails for reasons a probe cannot see. So **the enable press requires the world stopped** — the adversarial review's remedy, and it is smaller than the guard it replaces. The configuration is written while the server is down, the user's ordinary Start brings it up through `docker.start_staged()`, and the failure becomes "the server I just started did not come up", which the app already knows how to show and which costs no running player their session. The Definition of done gains: after the start the world is running, the restart count has not grown, and this run's ready marker is in the log; on failure the app rolls the configuration back and says so. The two-press arm is then unnecessary, because there is nothing running to interrupt. |
| 4 | The published-bindings read proves 7878 is loopback-pinned | At `7bc5ebd3` it was a **global** scan of every container's ports (`docker.py:2302`). It is filtered by compose project — the ownership proof the install guard already uses — before it can refuse anything. |
| 5 | The bot clause takes the catalog value, with a comment about where the live one would be | The prefix is **resolved the way the server resolves it**, and the three trees differ. AzerothCore consults `AC_AI_PLAYERBOT_RANDOM_BOT_ACCOUNT_PREFIX` in the environment and it **wins over** the file (`AC Config.cpp:540-552`). TBC and Vanilla consult `Mangosd_AiPlayerbot_RandomBotAccountPrefix` — prefix `Mangosd_` from `SetSource(configFile, "Mangosd_")` (`TBC Main.cpp:156`), dots to underscores, **case preserved** — and it also wins, applied at parse time (`Config.cpp:75-78`). Tortoise has no environment layer at all. The resolver reports which source answered, refuses an unreadable or empty prefix rather than answering, and requires a safe character set before the value reaches SQL. Grafted from B and C, and extended by the read. |
| 6 | The CMaNGOS `account set password` rows are marked unverified | **Verified today.** All three CMaNGOS-lineage trees take three arguments: two password arguments extracted and both required (`TBC Level3.cpp:1132`, `VAN :1093`, and Tortoise's handler). The unverified flag stays in the model for what is still unread. |
| 7 | The MaNGOS SOAP namespace is marked unverified and pinned by a gate | **Read.** `MaNGOSsoap.cpp:156` carries the `urn:MaNGOS` namespace on mangos-tbc and mangos-classic alike. No gate is spent on it. |
| 8 | The Console tab's attach sits outside the channel's lock | One lock per install covers **every** attach, the Console tab's included. The prompt delimiter "is a single-writer property, not a property of the console" (`console.py:57-73`); two writers put foreign prompts in each other's windows. |
| 9 | A fourth three-valued type enters the tree | At `7bc5ebd3` `yulon/ownership.py` already answered "whose folder is this?" in three values, and its module docstring said why: "That is the part that must not be re-invented with two values." The uninstall's plan returns that type, and the new answer type is read against it before it is written. |

### Facts settled by reading after the panel reported, which the designs had booked as spikes

Recorded because each removes work, and because a spike a read can answer is a spike this project
does not run. **The reads themselves are committed at `pyplan/archive/phase8-judges/panel-reads.md`** — with
the command and the lines as they came back — because the first version of this table asserted six
facts that no committed reader had produced, and one of the six was wrong. That is the failure this
table exists to prevent, committed by the page enforcing the rule; the artefact is the remedy.

| Fact | Where | What it removes |
|---|---|---|
| Bots added to a party are logged out with their master | `PB src/Script/Playerbots.cpp:450-460` — the logout hook calls `LogoutAllBots()` when the player is not itself a bot | A spike on what a bot left behind costs |
| Tortoise does not cache passwords; only the rank is cached | `TW AccountMgr.cpp:299-311` reads the stored hash on every check; `:250-256` caches the rank | A spike, and Tortoise keeps a set-password path |
| The Lua engine's command hook hands the script the chat handler | `ALE PlayerHooks.cpp:57-60` pushes the player, the text and the handler | The bridge's arrival can be proved by the script's own reply, not only by a log line — at the revision read, which is unpinned |
| The CMaNGOS and Tortoise column names the design declared unread are the ones it wrote | `TBC mangos.sql:2968-2978`, `characters.sql:791+`; `TW tw_world_item_template.sql`, `create_databases.sql:999` | The per-tree table blocks are verified data, not guesses |
| The AzerothCore **worldserver** image installs neither `iproute2` nor `curl` | The `worldserver` stage is built `FROM runtime` (`apps/docker/Dockerfile:175`, `:112`), whose only apt install is `:122-126` — `libmysqlclient21 libreadline8 libicu74 libncurses5-dev gettext-base default-mysql-client adduser`. The `curl` at `:68` is the `build` stage and the one at `:229` is `client-data`; neither reaches the worldserver | Neither `ss` **nor** `curl` is a gate instrument inside that container. This row first said the image ships `curl`, written from a grep of the apt lines rather than a read of the stage graph, and both judges had recorded the question as unsettled; the correction is why the round-trip is taken **from the host through the published port**, which is what the feature needs to work anyway. `/proc/net/tcp` needs nothing installed and is the fallback |
| An app account named `YULON` already exists on the TBC gate box | `checklist.md:1452-1461` — the 7.9 gate wrote that account at GM level 3 and logged a client in through it | A fixed account name would have collided on the first gate box; the per-install name does not |

---

## Architecture and module layout

Extends the style-guide §3 table. Every row is added the day its module exists, not before — a row
for a file that is not there teaches a reader to discount the table.

| Layer | Module | Owns | Must never |
|---|---|---|---|
| Data | `catalog/catalog.py` (changed) | The per-entry operations block: channels, GM-level shape, command templates, caps, bot marker, table and column names, and the optional Lua bridge — typed, frozen, extra keys forbidden | Hold a value that belongs to one install; know how a command is sent |
| Wire | `soap.py` (new) | One command envelope over the standard library with Basic auth and bounded timeouts; the reply parsed into result, fault, or transport status | Build command text; hold a lock; know a game; let a password reach a log, a repr or an exception |
| Delivery | `channel.py` (new) | The answer type, the channel protocol, its **two** implementations — SOAP and attach (owner answer 10 refused the third) — and the ranking that picks one from the entry; one lock per install covering both transports, the Console tab's attach included; mapping a transport failure to a reason from container state. **A mutation on the attach transport is indeterminate until its own verify read answers**: that transport cannot separate a reply from the server's own asynchronous output (`console.py:10-12`, `:522-527`), so an action with no verifier is not offered there | Contain command text; retry a write after a timeout; contain UI |
| Text | `commands.py` (new) | The command built from the entry's template; the quoting rule; **the leading-dot rule — the console parser strips a leading `.` or `!`, and the prior art spells some commands with one and some without, so templates carry none and the rule lives here**; argument validation; and the one predicate that both decides whether a control is drawn and supplies the sentence when it is not | Send anything; know a container name |
| Reads | `dbreads.py` (new) | The typed reads over the existing SQL seam (`apply.py:504`): bot clause, online counts, item search, teleport targets, characters, accounts, bots, mail counts, group members | Call the write seam — asserted over the AST, not by grep |
| Setup | `channel_setup.py` (new) | Per-tree enable **through `docker.start_staged()`, never a bare compose recreate** (correction 1); the app account's create-verify-persist state machine; the credential file under the app's config directory; and the rollback when the world does not come back | Persist before a round-trip has answered; rewrite the password of any account but its own |
| Evidence | `logsnap.py` (new) | The pre-stop log snapshot: this install's world container resolved through its own compose project, a bounded tail, a partial file renamed on success, retention that never prunes the file just written | Block a stop, on failure or on a hang |
| Verdict | `dashboard.py` (new) | Composing container state and the two counts into a verdict with three-valued fields; the poll budget | Fire a world-thread command on a timer |
| Features | `gm.py`, `party.py`, `purge.py`, `steam.py` (new) | One feature family each, every action shaped capability, command, channel, verify read; `purge.plan()` returns the existing three-valued ownership type rather than a fourth one (correction 9) | Build SQL; write into a client folder |
| Surface | `ui/widgets/outcome.py`, `ui/characters_view.py`, `ui/bots_view.py`, `ui/uninstall_dialog.py` (new) | Rendering an answer in all three outcomes; the two new tabs as sub-views that signal up and never reach up | Contain business logic, SQL, command text or a subprocess |
| Changed | `controller.py`, `docker.py`, `apply.py`, `accounts.py`, `composegen.py`, `ui/controller_view.py`, `main.py` | Additive only: a pre-stop snapshot hook; a bounded log tail; **the published-bindings read filtered by compose project before it can refuse anything (correction 4) — unfiltered it answers for whichever container holds the port**; a running-state seam checked inside the SQL step; a password reset for one named account; one render token and an override re-render; new service fields and tabs; one signal connection | — |

The Q7 guard goes **inside** the applier's SQL step (`apply.py:1359`), the one point every caller
passes through — not in install and remove, which miss any other caller, and not in the view,
which anything that is not a button bypasses.

---

## Per-family data, and what each tree costs

The full typed model and the four catalog blocks are in `phase8-designs/a-server-side-seam.md` §3,
with a citation per value. What matters at this level is the shape of the differences:

| | WotLK (AzerothCore) | TBC / Vanilla (CMaNGOS) | Tortoise |
|---|---|---|---|
| Channel | SOAP, loopback | SOAP, loopback | **none** — the attach console, where a pty exists (answer 10: Linux and macOS for v1) |
| Turned on by | a key in the generated override's environment | the same, under a different variable name, **or** the conf table; the environment wins over the file | — |
| Login needs | GM level 3 in the access table | GM level 3 in the account row | — |
| The command then runs as | console, no level check | console level, regardless of the caller | console level, account id 0 |
| A failed bind | graceful shutdown | **immediate exit, no saves** | — |
| Items per mail | 12 | TBC 12, **Vanilla 1** | **1** |
| Set a character's level | yes | yes | **no console route to an arbitrary level** — the whole command table was searched on 2026-09-06: no `character level` row, and `.levelup` is console-false. What this tree does have is `reset level <player>`, console-legal at administrator level, which sets the *configured starting* level and takes a live player. The control is not drawn, and the sentence in its place names what does exist rather than implying nothing does |
| Rename | a subcommand | a subcommand | a top-level command |
| GM level lives in | the access table | the account row | the account row, **cached in memory** — the command route is the only one a running server sees |
| Bot marker | registry types **or** account prefix | account prefix only | account prefix only |
| Bot party | the Lua bridge | out of scope (Q4) | out of scope |

Tortoise is not a CMaNGOS server for any of these purposes, and TBC's facts are not Vanilla's. Each
tree's block is gated on its own box.

---

## Delivery order and gates

WotLK first on every step (Q3), one box per family, each box lettered so a Tortoise fact can never
tick a WotLK box. Every gate captures the live rows it is about to change **before** it changes
them, and again after. Boxes: the Ubuntu VM holds the finished 7.2 WotLK install and is the WotLK
box; the test box holds all four clients and a running Tortoise and is the CMaNGOS box, one game at
a time; the Windows gate box takes the two Windows-only facts; a 3.3.5a client on the Hyper-V host
provides the in-game half for WotLK after the LAN step.

| Step | Delivers | Gate |
|---|---|---|
| **8.1a–d** | **Observability first**: the pre-stop log snapshot, the restart-loop verdict, the dashboard's reads and the bot-marker resolver, and the interlock that disables commands on an unstable server. None of it needs the channel — the counts are database reads and the snapshot is two docker commands — and all of it is what the next step needs if that step goes wrong. The write ledger and its test land here too, with the first new write sites | All four, one box per family; the crash-loop rendering forced once |
| **8.2a–e** | The channel: the operations model and four catalog blocks; the wire, delivery, text and setup modules; the password reset; the override re-render; the Server tab's channel group. **The enable press requires the world stopped** | WotLK on the Ubuntu VM, then native Windows on the gate box; TBC on the test box; **Vanilla on a fresh throwaway install on the test box (answer 9), the same install 8.9 needs, so one compile serves both**; Tortoise's attach-only sentence |
| **8.3a–d** | Accounts: list, set password, GM level | All four; the client logs in with the new password and is refused the old |
| **8.4a–d** | Named teleport, item search, item mail, mailed money, revive, set level, rename, gear sets | All four; every verb once offline and once online, each with its in-game effect on screen |
| **8.5a–d** | Browse Bots | All four; the count equals the hand query and the in-game who-list finds a listed bot |
| **8.6** | My Party, WotLK only, over the Lua bridge | The Ubuntu VM after the owner's rebuild; a bot in the party frame |
| **8.7a–d** | Module update checks; manifests for the three CMaNGOS games; the applier's guard | All four, one box per family |
| **8.8** | Steam, Linux and Steam Deck only | A Steam Deck running SteamOS — Baerthe's or DaddyCool's (owner answer 13); assigned, not reachable from here |
| **8.9a–b** | Uninstall and purge, as `phase8-decisions.md` | WotLK on a **throwaway** install, never the 7.2 one; one CMaNGOS game |

8.1 (observability) first, because it is what makes 8.2's failure legible; then 8.2a for WotLK and
its lettered siblings per family; 8.3 and 8.5 after that and independent of each other; 8.4 after
8.3; 8.7 independent once 8.2a exists; 8.6 after 8.5 and the rebuild. **No family's 8.3 to 8.7 half
runs before that family's own 8.2 letter.** 8.9 is not last: its WotLK half runs early, on its own
throwaway install, because a destructive recovery path validated after eight steps have mutated
installs is validated too late; its Vanilla half consumes the shared throwaway install at the end
of that box's sequence. Phase 7's controller-surface gate and its regression pass are re-run before the exit box,
because the base controller and the service assembly both change.

---

## Blast radius on the proven install and controller paths

- `catalog.json`: every entry gains an operations block. Additive, and the strict models refuse a
  typo when the catalog loads.
- **The SOAP environment goes in that block, not in the install's world environment**, and reaches
  the override through a new re-render rather than through the install-time render. This is the
  decision that keeps the committed rendered-compose fixtures byte-identical, so Phase 7.1's
  "matches the fixture" assertion keeps meaning what it meant.
- The shared CMaNGOS base template gains one publish line. The "ports in exactly one file"
  invariant holds. No CMaNGOS compose-config fixture exists, so the 8.2c and 8.2d gates capture the rendered
  config before and after and the diff is that line — and the committed gate captures for 7.4c,
  7.5 and 7.6 are stale on that block, which is named here rather than discovered later.
- `controller.py` gains one optional constructor argument and two one-line calls; every existing
  caller constructs without it and sees today's behaviour.
- `apply.py` gains a running-state seam checked inside the SQL step. **This changes behaviour on
  the proven Modules tab**: a running server now refuses module SQL aimed at the character or world
  database. Named, wanted, and the reason Q7 exists.
- `ui/controller_view.py` gains service fields and two tab builders that compose sub-views; no
  existing builder is rewritten.
- Not touched: the install engine, the networking module, the state file, maintenance, repair, the
  console transport, the staged start, stop and remove paths, the repair button, and the port-remedy
  contract.

---

## Tests

Unit, no daemon, in the shapes this project already trusts — a recorder double, the real catalog,
the real templates. Every name below is new; nothing here cites a test that does not exist.

- The catalog-operations test: the four entries validate; a bridge without a channel, a queue
  without an attach console, an empty bot prefix and a mail cap below one each fail; and the values
  still marked unverified are enumerated **by name**, so ticking their box turns them red until a
  gate measures them.
- The wire test: the envelope by field for both namespaces; every reply shape mapped to its
  outcome against a stub server; the password absent from every repr, log record and exception.
- The delivery test: the three outcomes; a write is never retried after a timeout, proved by a
  fixture that answers differently the second time; the lock serialises two concurrent asks, the
  Console tab's attach included.
- The text test: every builder against all four entries; the quoting rule; the per-tree caps
  splitting a nineteen-piece set into two, two, nineteen and nineteen commands; the capability
  predicate returning each of the three outcomes.
- The read test: the SQL per entry, asserted by field; the write seam raises if reached.
- The setup test: create, verify-fails, nothing persisted, and no second account on the next press;
  the rollback when the world does not come back; the file's mode asserted on the open flags, not
  on a later change.
- The bot-marker test: both arms on WotLK, prefix only elsewhere; the three-source precedence per
  tree; a fixture whose registry is empty with bots present, and one with citizens and no prefix.
- **The write ledger test**, grafted from C and the best test idea in the three designs: an AST walk
  over the package enumerating every write site, failing on one absent from the committed ledger
  **and** on a ledger row that resolves to no site.
- The applier-guard test: module SQL into the character or world database refused while the world
  is up, with the step named; authentication-database steps unaffected; with the seam absent,
  today's behaviour byte for byte.
- One test file per feature module, each negative fixture violating exactly one rule.
- Existing files extended: the compose generator's (the new token and re-render, keeping every byte
  assertion), the catalog invariants (the publish line renders once per CMaNGOS game and never for
  AzerothCore), the controller's (the stop calls the snapshot once, before the staged stop, and
  still stops when it fails), the controller view's and the package-agreement test.

Integration, against throwaway containers and never a real server: the wire against a stub server
in a container, the bot-clause SQL against a throwaway database, the snapshot against a container
that prints more than the tail and exits.

Mutation discipline: purge the bytecode cache on both sides of every mutation, and a full score is
a claim.

---

## Risks worth re-reading before 8.2a

- **Turning SOAP on can stop the server, and worse on the CMaNGOS family than on WotLK.** The
  measured behaviours are in the correction table above. The guard is not a probe — a connect probe
  passes on the broken configuration, measured — it is that **the world must be down for the press**:
  one stop and one start the user makes anyway. The failure is then caught after their own Start, by
  the restart count and this run's ready marker, on a server that had no players on it. The price of
  skipping it is a restart loop on a server whose characters were not saved.
- **A timeout does not un-queue a command.** SOAP waits on the world thread; if the launcher's
  deadline expires first the command still runs. No write is ever retried automatically, every
  write has a verify read, and the app says the command may still arrive rather than that it failed.
- **The listener's bind address inside the container is measured**, on a busybox stand-in rather
  than a server (`pyplan/gates/8-spikes/published-port-vs-container-loopback/`, 2026-09-06). A
  listener on the container's own loopback does not serve a published port — and it does not refuse
  either: Docker accepts the connection on the host side and relays nothing, so a connect probe
  **passes on the broken configuration**, and only a round-trip that receives a reply tells the
  three states apart. Refused, silent and answering are the same three outcomes the seam types, and
  "connected and silent" is also what a still-loading world looks like, which is why the ready
  marker and not the connection is what separates them. What remains a deduction is only that the
  emulators bind the way everything else does; the first per-family gate proves that.
- **Command security levels are a database question**, not a source question: AzerothCore overrides
  them from a world-database table at load with only a warning, and both CMaNGOS trees read an
  equivalent table. The design does not depend on the numbers, because SOAP runs at console level
  on every tree; they are data for the reader.
- **The Lua engine module is unpinned**, its compiled default is off while its own shipped comment
  says on, and the manifest patches a key the module does not have. My Party rests on it, and the
  bridge's arrival is proved by the script answering, never by the deploy reporting success.
- **The credential file is a secret on disk**: restricted where that means something, and inheriting
  the per-user directory's protection on Windows, which is the same class as the database password
  the install already writes. What it grants is a console-level login on loopback; what it does not
  grant is anything the database root password in the same install does not already.
- **Two installs of one game** still collide on container names and now on the SOAP port. The
  existing guard refuses the second start; the credential file records the port the daemon actually
  published.
- **Module SQL marked for the import one-shot is reported as done and never applied.** Found by two
  judges, in the method 8.7 extends: the applier appends such a step to its done list and continues
  (`apply.py:1365-1367`), and the staged start never runs the one-shot (`docker.py:715-717`), so on
  any installed server those steps silently do nothing. Seventeen shipped WotLK module manifests
  carry such steps. 8.7 either runs the one-shot with the world stopped or reports the step as not
  applied; what it may not do is keep logging it as done.
- **Item mail is not idempotent.** Every other verb converges; a second press sends a second mail.
  The queue disables the button while one is in flight, and what the app forgets across a restart it
  reads back from the mail table.

---

## What the implementer should NOT build yet

- No fourth transport: no playerbot command server and no Lua bridge on
  Tortoise, **and no remote-access console** — `Ra.Enable` stays 0 and 8.2a's gate asserts nothing
  is listening on 3443, because a second remote channel left at its shipped default while the first
  is deliberately opened is the same class of finding as the playerbot command server; **and no
  writer for Tortoise's command queue** — answer 10 makes its Phase 8 actions
  Linux and macOS only, so on native Windows the tab carries the reason instead of a slower route.
  The playerbot command server is closed in the same press that opens SOAP, and its closure is
  proved from inside the container.
- No settings surface and no YAML writer for the override: the re-render comes from the template.
- No periodic world-thread command, on any timer, for any reason.
- No automatic retry of a write.
- No write to the character or world database from the app, including "just for the offline case".
- No coordinate teleport, no add-item, no heal, no summon — refused in the cut.
- No bot party on the CMaNGOS family, and no console-driven party experiments outside a named spike.
- No character sheet, doctor, tuning knobs, config editor, settings page or single-instance guard:
  those are Phase 9.
- No auto-stop, keep-awake-while-running, autostart, automatic backups or server self-update: later.
- No client-folder write of any kind.

---

## Doc changes this phase makes

- `pyplan/style-guide.md` §3 — a row per new module, each added the day the module exists; the
  base controller's row gains the pre-stop snapshot; the applier's row gains the running seam; the
  catalog's row gains the operations block.
- `pyplan/README.md` §9 — the three lines struck through with the date, the way the Linux-native
  line was; §11 gains the new files under the app's config directory.
- `pyplan/checklist.md` — the `8.x` boxes below the kept 2026-08-21 identification box.
- `pyplan/bug-checklist.md` — the boxes for the create-only Accounts tab, the crash-loop status, the
  first-poll unknown and the Modules tab's selection each annotated with the step that closes them;
  they tick on their own evidence, not by side effect.
- The Lua engine's manifest — the conf key corrected to the one the module has, and a revision
  pinned the day 8.6's gate passes.
- `pylauncher/README.md` — the capability table gains a row per new surface as each gate records
  what ran live.
- `pyplan/roadmap.md` — **not edited**. Appendix A is the proposed §8.

---

---

## Review findings and what was done with them

Three reviews of these four documents, run 2026-09-06 after the panel reported: an adversarial
review by a different model family (Codex, five findings), a superpowers review (twenty-three), and
an independent third review of the process and the record (twenty-one). Each reviewer's findings
were then put to the other two rather than concatenated, and what follows is the reconciliation.
**Forty-nine findings, forty-six applied, three refuted with the reason, none left unanswered.**

**The reviews did not read one artefact.** The first two read `c1318c97`/`e125be06`; the third began
at that tip and finished after `35a99953` landed fifteen corrections mid-read, which it noticed and
said so. So a finding that reads as refuted below may instead have been fixed before it was raised;
where that happened it is marked *closed before raised* rather than refuted.

### Where the three agreed, and what it changed

Three findings arrived independently from more than one reviewer. Each is applied.

| Finding | Raised by | What changed |
|---|---|---|
| The listener's bind address was described as the container's loopback, which is the exact defect this page rejects design B for | superpowers; implied by the adversarial review's port analysis | "The cut" and Appendix A now say bound to all interfaces **inside** the container and published on the host's loopback only, and 8.2a's proof is a round-trip **from the host** |
| Gear sets are promised on four families on an inventory join read on one | adversarial; third | 8.4b, 8.4c and 8.4d carry the read as a prerequisite; until it is done, gear sets are WotLK's alone |
| The Steam box is mandatory for the exit and its machine does not exist | adversarial; superpowers | 8.8 carried `[blocked]` on hardware and the exit line named it as a carve-out, the way Phase 7's exit line names macOS. **Superseded 2026-09-06:** the owner named two Steam Decks (answer 13), so the finding's premise no longer holds; the box is assigned rather than blocked |

### The three findings that changed the shape of the phase

**The observability step now comes before the command channel** (adversarial review, finding 3).
As written, 8.1 recreated servers and could produce a save-less restart loop on two of four trees,
while restart-loop detection, the command interlock and the pre-stop log snapshot all arrived in
8.2 — so the first live gate would have run the highest-risk change with none of the instruments
this page says are needed, and a rollback could have destroyed the container log that would explain
the failure. The two steps swapped. It costs nothing: the dashboard's counts are database reads and
the snapshot is two docker commands, and neither needs the channel.

**The enable press requires the world stopped** (adversarial review, finding 2). The page had a
probe that the port is free before writing. That is check-then-act: there is a window between the
probe and the bind, and a bind fails for reasons a probe cannot see. Requiring the world stopped
removes the hazard instead of warning about it, and it is *smaller* than the guard it replaced —
the configuration is written while the server is down, the user's ordinary Start brings it up
through the staged start that Phase 7 already proved, and the two-press arm becomes unnecessary
because there is nothing running to interrupt.

**Every step is now lettered per family** (third review, finding 3). Owner answer 3 said "one box
per family"; it had been applied to the channel step and to no other, so six steps were single
boxes reading "all four". The operator judge had given the operational reason and it is the
deciding one: a coarse box cannot tick until the slowest family passes, and one family is blocked
behind a recompile. Twenty-nine boxes, each tickable on its own evidence.

### The three findings that were about this page telling an untruth

Each is a correction to the record, not to the plan, and each was mine.

1. **The Lua bridge was described as having run live; the note records it failing.** The comment
   cited says the *silent-bridge bug* was found live on 2026-08-20 — the deploy reporting success
   while every bridge command answered "Command does not exist". I wrote that the notes record it
   "running live". That sentence was the only live evidence behind the one route owner answer 5
   leaves for My Party. The delta and 8.6 now say what the note says, and 8.6's first question is
   whether the route works at all, with a negative answer named as a legitimate outcome.
2. **A could-not-ask was published in a table headed "settled by reading".** The row said the
   AzerothCore image ships `curl` and no `iproute2`; I wrote it from a grep of the apt lines rather
   than a read of the stage graph, while two judges had recorded that exact question as unsettled.
   Read properly, the worldserver stage installs neither — the `curl` is in two other stages. Both
   halves were wrong and the substitute instrument the row prescribed did not exist. Corrected at
   `35a99953`; the third review re-read the stage graph and confirmed the correction.
3. **Owner answer 1 was widened in one document and narrowed in two.** The owner released `8.1`,
   on the branch stacked on the Phase 7 pull request, declining the recommendation that it wait for
   that merge. One document released the whole phase; two re-imposed the merge condition, including
   the text that would become the roadmap. All three now say what the owner said.

### Applied without further comment

Dropped judge requirements restored: the remote-access console asserted absent on 3443 alongside
the playerbot command server (two judges had required it; only the second had landed); the
Console-tab route removed from 8.2a and 8.2b, because the maintainer required it become its own
sub-step behind the capability predicate and that reversal had gone unrecorded; corrections 1
and 9 carried into the architecture table rather than living only in the table of corrections.
**Correction 4 was claimed here in the first round and not carried; it is carried now, on the docker
row.**

Hedges restored to their sources: Tortoise's missing set-level command is now "no console route
found", because the read searched two handler names and not that tree's whole table; the reads
behind the six "settled by reading" facts were committed rather than summarised
(`pyplan/archive/phase8-judges/panel-reads.md`).

Citation and record fixes: four citations off by a line or a range — claimed in the first round and
actually made in the second, recorded here rather than smoothed over; the delta's declared path
root, which said `pylauncher/` while most citations resolve under the package inside it and
`catalog/` resolves under both, now stated per prefix; the method
note that said eight questions when the record holds ten, two of them answered after the judging;
an appendix that reported an answered question as open; both stated reasons for rejecting design C,
each wider than that design's own text; and present-tense assertions about the tree pinned to the
revision they were measured at.

Scope and gate fixes: the write ledger belongs to 8.1a, where the first new write sites appear,
rather than only to the exit criterion; every gate line names its host, because this project has
already paid once for "the Windows box" being ambiguous between two Windows boxes; no gate restores
a checkpoint on the VM holding the 7.2 install that six later boxes are gated against; two
definitions of done that proved a value was declared now prove it arrived; the delivery order names
the lettered boxes and states that no family's later half runs before its own channel box; and the
shared throwaway install's lifetime is stated across the seven boxes that need it rather than the
two that were named.

Evidence collected and then unused, now carried: the case-sensitive character-name lookup that
answered "not online" for an online character in the prior art, and the escape character that
survives a stricter SQL mode. Both land in 8.4a. **A third — the leading-dot rule for command text,
which the console parser strips and which the prior art spells inconsistently — was claimed here in
the first round and never carried; it is carried now, on the command-text module's row.**

### Refuted, with the reason

Three findings do not hold as stated.

1. **"The bot-marker warning is a two-outcome collapse."** The third review read the marker's
   three outcomes as two. They are three and are gated: refuse when the live value cannot be read,
   warn when it reads and matches nothing while characters exist, list when it matches — 8.1a's
   Definition of done exercises the first two with a fixture that violates one rule each. The
   finding was raised against the text before `35a99953`; the middle outcome was thin there and is
   explicit now.
2. **"Appendix B claims drain-before-stop, which lands nowhere."** Correct that it landed nowhere,
   and it is removed from Appendix B rather than added to the design: a command in flight delaying
   a stop the user asked for is a worse failure than a command lost to a stop, and no reviewer
   argued otherwise. The claim was the error, not the omission.
3. **"The interlock named in Appendix B is the one the skeptic replaced."** Half right: Appendix B
   named the maintainer's version and the body took the skeptic's. Appendix B is corrected. The
   underlying mechanism was never in doubt and did not change.

### Still open, and named as open

- **The bind address inside the container is a deduction, not a measurement.** No Yu'lon install
  has ever run this listener. The owner's live server has, and a read of its configuration would
  settle it without touching a gate box — but that server is the owner's and any command on it
  needs their explicit yes, so it is an owner question, recorded in the summary and on this line,
  not a spike this session ran.
- **8.8's hardware gap is CLOSED (2026-09-06, owner answer 13):** Baerthe and DaddyCool each have a
  Steam Deck running SteamOS. What is left is an assignment rather than a blockage — neither Deck is
  reachable from this side, so both the `shortcuts.vdf` read and the gate are run by whoever the
  owner names, the way §7.8's macOS box is. Until a name is on the line, 8.8 is unstarted, not blocked.
- **Owner answer 10's second half has no gate**: no box runs Tortoise on native Windows, so "the
  tab carries the reason" is unprovable here.
- **My Party's route has never been recorded working.** 8.6 asks that question first.

---

## Appendix A — proposed `roadmap.md` §8 (not applied)

To replace the Phase 8 block, from its `## Phase 8` heading through the separator before
`## Phase 9`, on the owner's explicit word, in the roadmap's own shape: headers, numbered items,
definitions of done, no narrative. The "Out of scope" list at the end of the roadmap needs the
matching edit, given below it.

```
## Phase 8 — Feature parity with The Lab + Hypeer Launcher

> A *feature* phase, not the UI/UX pass (that is Phase 9): it folds the capabilities of two
> companion tools into Yu'lon so users need one app. Features still need a surface, because on
> every platform the launcher is the product — minimal functional surfaces here, polish in 9.
> Scoped 2026-09-06 in `pyplan/phase8-parity-decisions.md`, which records the owner's cut and the
> mechanism measured per emulator tree; the uninstall feature was decided separately in
> `pyplan/phase8-decisions.md`. My Party, item mail and teleport were out of v1 scope in
> README §9 and are a deliberate expansion, each with its own step.
>
> **Ordering:** the phase's first step may start now, on the branch stacked on Phase 7's pull
> request — the owner declined the recommendation that it wait for that merge. It waits neither for
> the merge nor for the Phase 7 exit box (owner decision, 2026-09-06); the rest follows it. Delivery
> is WotLK first, one box per emulator family, the way Phase 7 ran. (The owner answered "8.1 may
> start now" when the command channel was numbered 8.1; the review round that followed put
> observability first, so that step is now 8.2. The release is read as "the phase's first step",
> which the owner confirmed on 2026-09-06 when asked which of the two steps the answer had released.)

### 8.1 Observability
1. Save the worldserver log before every stop, remove and uninstall — this install's own container,
   a bounded tail, the install id in the filename, retention that never prunes the file just
   written.
2. Show players, bots, uptime and a restart-loop verdict from database reads and container state,
   never from a command on a timer; resolve the bot marker the way each server resolves it and
   report which source answered.
3. First, because it is what makes 8.2's failure legible: enabling a listener can stop a server,
   and on two of the four trees it does so without saving characters.
4. _Definition of done:_ the counts equal the same query run by hand in the same minute; a forced
   crash reads as a restart loop rather than as up; every stop leaves a log file the user can open;
   an unreadable bot marker refuses to answer rather than reporting zero.

### 8.2 The command channel
1. Add a per-entry operations block to `catalog.json` carrying every per-tree fact: which channels
   exist, how each is enabled, the GM-level shape, the command templates and their caps, the bot
   marker, and the table and column names. **[style]**
2. Build the seam: the wire, the delivery layer with its three-outcome answer, the command
   builders with their capability predicate, and the typed reads over the existing SQL seam.
3. Enable the listener in the installed configuration — bound to all interfaces **inside** the
   container, published on the host's loopback only — and create an app-owned administrator
   account, verified by a real round-trip **from the host through the published port** before its
   credentials are stored. **The press requires the world stopped:** a connect probe cannot make a
   bind atomic, and it passes on the broken configuration anyway.
4. _Definition of done:_ on each family's box the Server tab reports the channel verified, with the
   reply text captured from the host; the app account exists at the level that tree's own schema
   holds it in; the port is published on the host's loopback only; after the user's own Start the
   world container is running with an unchanged restart count and this run's ready marker; a
   deliberately wrong credential is refused and the repair path restores it without creating a
   second account.

### 8.3 Accounts: list, set password, GM level
1. List human accounts, excluding bots and the app's own; set a password and a GM level through
   the server's own commands.
2. _Definition of done:_ a client logs in with the changed password and is refused the old one;
   the level reads back from the row; the app's own account cannot be edited from the tab.

### 8.4 Teleport, item search, item mail, GM actions, gear sets
1. Named teleport, item search, item mail, mailed money, revive, set level and rename, each drawn
   only where the tree has the command, each performed by the server.
2. Gear sets saved from a character and mailed back, chunked by the tree's items-per-mail cap.
3. _Definition of done:_ every action, once on an offline character and once online, changes the
   named row and shows its effect in the game client.

### 8.5 Browse Bots
1. A paged, filtered list of bots, resolved by the tree's own marker read from the live
   configuration.
2. _Definition of done:_ the count equals the same query run by hand on each family; an
   unreadable marker refuses to answer rather than reporting zero.

### 8.6 My Party — WotLK only
1. Deploy the project's own Lua bridge scripts into the server folder and prove they answer.
2. Add a bot by class, spec and level to an online character's party; kick; dismiss all.
3. _Definition of done:_ the chosen bot is in the party frame in the game client, geared and
   specced; a missing bridge says so instead of reporting success.

### 8.7 Module update checks, and manifests for TBC, Vanilla and Tortoise
1. Report how far behind each installed module is; apply updates.
2. Refuse module SQL aimed at the character or world database while the world is running.
3. _Definition of done:_ the commits-behind figure equals the same query run by hand; the refusal
   names the step and writes nothing; a manifest applied to a stopped server reads back from its
   configuration file.

### 8.8 Steam integration — Linux and Steam Deck only, gated on a Steam Deck
1. Add the server launcher and the game client to the Steam library with artwork and the
   compatibility tool. The shortcuts file format is recorded nowhere in this repository and must be
   read from a real Steam profile first.
2. _Definition of done:_ both entries appear in a real Steam library and launch. Gated on a Steam
   Deck running SteamOS (owner answer 13); the box is assigned, not held on this side.

### 8.9 Uninstall and purge
1. As `pyplan/phase8-decisions.md`: one action scoped to the server folder, that install's Docker
   project and the launcher's own record, with a "Keep my characters" checkbox.
2. _Definition of done:_ the kept characters survive a reinstall to the same folder; an unticked
   purge leaves nothing of the project; an install whose ownership cannot be proved is refused.

**Phase 8 exit criteria:** every step above passes its live gate on the families and platforms its
line names, with the evidence committed under `pyplan/gates/8.x-*`; no definition of done is
satisfied by a skip, an absent capture, a stale marker or an exit code; no capability is reachable
only from a command line; and Phase 7's controller-surface and cross-server regression gates are
re-run green on the merged tip. **One carve-out, named rather than hidden:** the
sentence Tortoise shows on native Windows, where it has no pseudo-terminal, has no gate box. 8.8 is
no longer a carve-out — the owner named the hardware on 2026-09-06 (answer 13) — but its box runs on
a Steam Deck nobody on this side can reach, so it is **assigned**, the way §7.8 is.
```

And in the roadmap's closing "Out of scope (do not start these in v1)" list, the first line is
replaced, keeping the reversal visible the way Phase 7's was:

```
- ~~My Party / bot group builder, item database + in-game mail, teleport/GM in-game tools~~ —
  **no longer out of scope.** Expanded into v1 by the owner on 2026-09-06: Phase 8 gives each its
  own step and definition of done (`pyplan/phase8-parity-decisions.md`). Kept struck through so
  the reversal is visible.
```

---

## Appendix B — the judge panel (2026-09-06)

Three designs, written independently from three angles by `fable` and committed unchanged under
`pyplan/phase8-designs/`; three judges, two on `opus` and the skeptic on `fable`, committed under
`pyplan/archive/phase8-judges/`. Seven criteria each scored 1–5: style-guide fit, DRY and one seam,
testability, operator safety on a live server, per-family correctness, gate quality, and blast
radius with incremental delivery. 35 maximum.

| | A — server-side seam | B — user's surface | C — operator's risk |
|---|---|---|---|
| Maintainer | **33** | 24 | 27 |
| Operator | 31 | 24 | **31** |
| Skeptic | **30** | 27 | 25 |
| **Total** | **94** | 75 | 83 |

The maintainer and the skeptic ranked A first. The operator scored A and C level and broke the tie
for C on the two criteria that seat owns, operator safety and gate quality, while writing the
sentence that decided the phase: *"A is the design I would rather maintain and C is the design I
can actually run"* — and that A becomes runnable by taking C's write ledger, its state matrix, its
capture-before-the-change rule and its gate discipline. That is what the grafts do, so the panel
does not disagree about the outcome; it disagrees about which half was the harder half to write.

**Grafts adopted from B:** the two new tabs as sub-views that signal up; one outcome widget so no
tab can collapse three answers into two; the two-press arm before anything restarts a running
world; the first status asked at construction; the zero-bots-with-characters warning; a timeout
reported as a refusal with its cause, never as success; selection-gated module buttons; the
uninstall signalled up so the window drops its own tab; per-family lettered gate boxes; and the
in-client screenshot as the visible effect.

**Grafts adopted from C:** the write ledger and the test that enumerates every write site in both
directions; the applier's missing guard, placed inside the SQL step; closing the playerbot command
server in the same press that opens SOAP, and the remote-access console asserted absent beside it;
the one lock per install that every attach takes, the Console tab's included (the skeptic's shape,
not a seam reading a view flag); the bounded log snapshot; the
deliberately-wrong-credential gate step; the bot marker's refusal semantics; and "evidence before"
on every gate.

**Dropped from A as drafted:** the bare compose recreate; the unfiltered published-bindings read;
the catalog-only bot prefix; the unverified namespace and password arity, both settled by reading;
and a fourth three-valued type where the tree already has one.

**Rejected outright, by name:** the playerbot command server as a channel (unauthenticated, off
the world thread, and it reaches only an already-loaded bot); a periodic world-thread command on
any timer; a credential file inside the server folder; a fixed app-account name; and writing a
command row into Tortoise's queue for anything the attach console can carry.

The panel also settled six facts by reading trees the designs had booked as spikes, listed above
under "Facts the panel settled by reading". Two questions came out of the design round rather than
from the panel itself. The first — Tortoise's reach — **was put to the owner and answered** as
question 10 above: Linux and macOS for v1, and where no pseudo-terminal exists the tab carries the
reason. The second, which machine has Steam for 8.8's gate, was **answered on 2026-09-06** as
question 13: two Steam Decks running SteamOS. Both design-round questions are now closed, and
neither was closed by a verdict — one by the owner's reach decision, one by the owner's hardware.
