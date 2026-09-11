# Phase 8 design panel — the operator judge

> Written 2026-09-06 by the judge who runs the live gates. Designs read in full:
> `designs/seam.md` (**A**), `designs/surface.md` (**B**), `designs/risk.md` (**C**).
> Governing pages read: `pyplan/phase8-kickoff.md` §§3–7, `phase8-parity-decisions.md`,
> `phase8-delta.md` ("Facts that govern every row", "Could not ask"),
> `notes/STATE-2026-09-06-morning.md`, `notes/resume-2026-09-05.md`, `phase7-decisions.md`
> "Delivery order and gates" + Appendix B, `checklist.md:2120` (7.9) and `:2331` (7.10),
> `gates/gate-79-controller-surface.py`, `gates/press-driver.py`,
> `gates/bug39-lan-press-2026-09-05/`, `gates/7.10-ubuntu-2026-09-05-rerun/`,
> `phase8-reads/azerothcore.md` §§A/B/F, `phase8-reads/cmangos.md` §§A/B/F (all three trees).
> Load-bearing symbols were resolved against the tree at `C:\Users\perzi\dml-phase8\pylauncher`.
> No box was touched; nothing was run.

---

## 1. Scores

Seven criteria, 1–5, 35 max — the shape `phase7-decisions.md:1116-1123` used.

| # | Criterion | A — seam | B — surface | C — risk |
|---|---|---|---|---|
| 1 | Style-guide fit | 5 | 4 | 4 |
| 2 | DRY, one seam | 5 | 3 | 4 |
| 3 | Testability | 5 | 4 | 5 |
| 4 | **Operator safety on a live server** | 3 | 2 | 5 |
| 5 | Per-family correctness | 5 | 4 | 4 |
| 6 | **Gate quality** | 3 | 3 | 5 |
| 7 | Blast radius and incremental delivery | 5 | 4 | 4 |
| | **Total** | **31** | **24** | **31** |

**Ranked verdict: C first, A a tied-on-points second, B third.**

A and C tie at 31. I break it on the two criteria this seat owns — operator safety (4) and
gate quality (6) — where C leads 10 to 6. Say it plainly for the record: **A is the design I
would rather maintain and C is the design I can actually run.** A's `CatalogEntry.ops` is the
best single idea in the three and C has nothing like it; C's `CommandQueue`, write ledger,
server-state matrix, "capture before the change" rule and negative-fixture DoDs are the four
things that decide whether a gate proves anything, and A has weak versions of all four. If the
orchestrator picks A on the maintainer's and skeptic's axes, **A is only runnable after it takes
C's §3, §4.0, §4.1 and §5 wholesale** — the grafts in §6 below are written to work in either
direction.

Score notes worth keeping:

- **A, criterion 4 = 3.** A's serialisation is one `threading.Lock` inside `SoapChannel`
  (`seam.md:47`). `AttachChannel` is a different object, the Console tab's `_console_pending`
  is a third, and `ui/widgets/job.py:160-165` runs every service call on its own `QThread`. Two
  tabs can each hold a press. And A builds the Tortoise `pending_commands` writer (§2 flaw).
- **B, criterion 4 = 2.** B fires a `server info` probe on a 15 s cadence *while the world is
  starting* (`surface.md:106`), and stores a GM-3 credential inside the server folder
  (`surface.md:161`). Both are §2 flaws.
- **C, criterion 6 = 5.** `risk.md:24` — "every gate captures the live rows it is about to
  change into a file before the change and the same rows after" — is `notes/resume-2026-09-05.md:76-79`
  written into the design, and there is an "Evidence before" row in every §4 subsection. Two DoDs
  carry negative fixtures that violate exactly one rule each (`risk.md:305` wrong-credential-file
  → 401 → Repair; `risk.md:309` prefix blanked in a copy of the conf must show the refusal, not 0).
  Nothing else in the three comes close.
- **C, criterion 1/2 = 4.** `yulon/commands/` is a subpackage where style-guide §3 is a flat
  module table, and `verbs.py` puts per-tree literals in Python instead of in `catalog.json`,
  which is a second home for facts the catalog owns.

---

## 2. The fatal flaw in each

### A — the Tortoise queue is keyed on a field the read says it never opened

`seam.md:342-344` builds `QueueChannel` on `tw_logon.pending_commands` with
`"realm_id": 1`, cited at `seam.md:382` as "`Master.cpp:799` + `mangosd.conf.dist.in:8`
(`RealmID = 1`)", sourced through `phase8-delta.md:34`.

`phase8-reads/cmangos.md`, "Facts I could not establish" **#4**: *"I did not read where `realmID`
is set from (`realmd.conf`/`mangosd.conf` key) — that would need `src/game/World.cpp` /
`Main.cpp` config plumbing I did not open."* The same section's **#3** adds that no ordering or
interleaving guarantee between `pending_commands` and the stdin console is stated anywhere in
the tree, and §A of that read records that **no output is returned to the caller**
(`World.cpp:3946-3948`) on a **60 s** poll (`World.cpp:845`).

So the one field that decides whether the row is ever consumed is unverified; the channel returns
nothing; and `seam.md:591` (gate 8.1d) and `seam.md:594` (8.4 on `yulon-win11-gate`) both gate
native-Windows Tortoise on it. Operator consequence: a wrong `realm_id` yields a row nobody
consumes, no output anywhere, and a verify read that answers `no` forever — indistinguishable
from a slow server, and A's own §4 matrix maps that to "queued, not yet seen" (`seam.md:426`).
A question with one outcome is not a question (kickoff §7 rule 2). B and C both refuse the queue
(`surface.md:169`, `:521`; `risk.md:43`, `:380`); they are right.

### B — the channel probe is a poll, and it polls a loading world

`surface.md:106`: *"The probe is asked only when `status.world` is true, at most once per 3 polls
(15 s) … this bounds it to one every 15 s while starting."*

`phase8-reads/cmangos.md` §A (TBC/Vanilla): SOAP *"one request at a time: accept → serve →
destroy in a single loop"* (`MaNGOSsoap.cpp:51-63`), and the call *"blocks until done, 50 ms poll
on the SOAP thread"* (`:127-128`); the command is drained by `World::Update`
(`World.cpp:1748`, `:2229`), which does not run until the world is up. The ready budget is 480 s
(`docker.py:39`). So B's own design fires a probe into the single SOAP slot at second 15 of a map
load, wedges it for the whole load, and stacks ~30 more in the kernel backlog. On AzerothCore the
request queues and waits the same way (`azerothcore.md` §A, `ACSoap.cpp:120-130`).

`surface.md:268` says a press while starting is impossible *because the buttons are disabled* —
the probe is the thing B forgot to disable. C names the exact rule B breaks
(`risk.md:132`: while starting, never *"fire a SOAP command (it would block the SOAP thread until
the first tick, up to the 480 s ready budget)"*).

Two more, either of which stops B's 8.1a gate passing as written:

- `surface.md:150` and `:409` name the mechanism as `AC_SOAP_ENABLED=1` alone. `SOAP.IP` is
  never mentioned in the document. Its dist default is `127.0.0.1` **inside the container**
  (`azerothcore.md` §A, `worldserver.conf.dist:460`; `cmangos.md` §A,
  `mangosd.conf.dist.in:1859`), which a docker publish cannot reach. B's press enables a listener
  nothing can talk to.
- `surface.md:161` puts the GM-3 credential at `<server_dir>/.yulon-commands.json`. That is the
  folder 8.9 deletes, the folder the user is told to back up, and the folder
  `composegen.install_id`'s own docstring (`composegen.py:135-150`) exists because people copy.
  A refuses the server dir by name (`seam.md:448`) and C puts it under `config_dir()`
  (`risk.md:45`); both also record that a 0600 mode is a **measured no-op on Windows**
  (`families/conf.py:378-395`, measured 2026-09-01) — B does not.

### C — the box plan needs two CMaNGOS servers on a box that has never held one

`risk.md:281` assigns TBC and Vanilla to **yulon-fedora** ("Py 3.13, SELinux Enforcing, Off at
baseline"). No WoW server has ever been installed there. `notes/STATE-2026-09-06-morning.md:41-42` has
it "Off at baseline after the final gate", and that gate was the `--checks` matrix
(`notes/STATE-2026-09-06-morning.md:23-25`), not an install; memory `test-boxes-include-the-vms`
records it as "Py 3.13, SELinux Enforcing, clone + venv" since 2026-09-05.

So C's 8.1/8.2/8.3/8.4/8.7 CMaNGOS gates each require **two fresh CMaNGOS installs**, each a
source compile — the only measured figure in the record for one is 10800 s (Windows Tortoise,
memory `windows-gate-box-recipes`) — on a Hyper-V VM sharing a host with `yulon-ubuntu`, whose
worldserver is 4–5 GB RSS and whose compile jobs are ~2 GB each (memory
`server-and-build-memory-budget`), under **SELinux Enforcing**, which no Yu'lon install has been
proved against. C states the rule "never a compile beside a running server" (`risk.md:282`) and
then plans two compiles it never prices. Fixable — move to `m910q` with Tortoise stopped, as A
and B do — but as written it is the most expensive and least executable plan of the three.

Second, smaller: `risk.md:305`'s 8.1 DoD requires *"inside the world container `ss -ltn` lists
7878 and not 8888 nor 3443"*. `iproute2` is not established to exist in the AzerothCore runtime
image. A DoD that cannot be executed gets quietly swapped for a weaker one at gate time, which is
the failure mode the whole rule set exists to prevent.

### The flaw all three share: enabling SOAP can stop the worldserver

`phase8-reads/azerothcore.md` §A, verbatim, `ACSoap.cpp:41-46`:

```
    if (!soap_valid_socket(soap_bind(&soap, host.c_str(), port, 100)))
    {
        LOG_ERROR("network.soap", "ACSoap: couldn't bind to {}:{}", host, port);
        World::StopNow(ERROR_EXIT_CODE);
```

A bind failure **stops the world with an error exit code**. 8.1 is the press that turns SOAP on
and recreates the container; if the bind fails — port taken inside the container, a bad `SOAP.IP`,
a second install — the press stops the user's server, and on a `restart: unless-stopped` policy
it stops it repeatedly. Not one of the three has a row for it in its state matrix
(A `seam.md:422-428`; B `surface.md:138-146`; C `risk.md:130-140`), none names the rollback
(remove the keys, recreate), and none makes *"the world came back up after the recreate"* part of
8.1's Definition of done. On TBC/Vanilla the equivalent behaviour at `Master.cpp:248-249` is
unread — rule 9: measure it per tree, do not inherit it from AzerothCore.

**This is the single change I require of whichever design wins.** 8.1's DoD gains one clause:
after the recreate, the world container is `running`, `RestartCount` is unchanged, and the ready
marker is seen this run.

### The cost all three ignore: there is no Vanilla install anywhere

`notes/STATE-2026-09-06-morning.md:34-36`: *"the CMaNGOS engine's `patch-sources` stage now refuses a
second press on any folder built before the doodad patch (m910q Vanilla, win11 TBC), so those
installs cannot reach `ready` again without a recompile."*

Every Vanilla gate in all three designs is therefore blocked behind a recompile that is the
owner's to start: A `seam.md:590` (8.1c), `:594` (8.4); B `surface.md:387`, `:390`, `:391`,
`:392`, `:396` (five rows); C `risk.md:288` (yulon-fedora, which has none at all). Nobody names
it. It is the largest un-priced item in the phase and it is what makes B's per-family lettered
boxes (§6) load-bearing rather than cosmetic.

---

## 3. Kickoff §6 checklist — every failure, by design and line

The seven checks the reviewers are briefed to make, run here over the three designs.

### A — `seam.md`

| Check | Verdict | Line and why |
|---|---|---|
| (i) DoD unsatisfiable by a skip / absent capture / stale marker / exit code | **FAIL ×2** | `:616` — 8.6's DoD is *"the ping answered **(or the load line seen — recorded which)**"*. An either/or DoD is satisfied by its weaker half, and the weaker half is a log line. `:530` concedes the ping's own mechanism is unread. — And `:611`, `:612`, `:613`, `:614`, `:615`, `:617`: every DoD is after-state only. `notes/resume-2026-09-05.md:76-79` requires the before-half in the same breath. |
| (ii) "already has X" resolves; "needs Y" names a mechanism and where verified | **PASS** | Spot-checked live: `docker.published_bindings` (`docker.py:2302`), `container_state` (`:1883`), `project_containers` (`:804`), `run_one_shot` (`:1357`), `conf.apply_table` (`conf.py:243`), `composegen.install_id` (`:135`), `write_plan` (`:692`), `accounts.MAX_USERNAME/MAX_PASSWORD` (`accounts.py:93-94`) all exist. |
| (iii) no WotLK mechanism assumed for a CMaNGOS tree | **PASS, with a caveat** | `:333` declares the Vanilla block *is* the TBC block "because the same `cmangos/playerbots` SHA is built in", then rescues itself: "the gate that proves it for TBC proves it for Vanilla separately (rule 9)". Keep the rescue sentence in the plan, not only in the design. |
| (iv) no DoD met from a CLI or a script | **PASS** | Every gate row is a press on a tab. |
| (v) no test names cited that do not exist | **PASS** | All new files marked new; the existing ones it extends (`test_composegen.py`, `test_catalog_invariants.py`, `test_controller_view.py`, `test_console.py`, `test_accounts.py`, `test_networking.py`, `test_controller.py`) all exist. |
| (vi) every "X cannot happen" is a measured price or an enumerated route | **FAIL ×2** | `:221-224` — *"`0.0.0.0` inside the container is not a relaxation: a published port delivers to the container's bridge address, so a listener bound to the container's own `127.0.0.1` never accepts it."* A deduction stated as a fact; nobody measured it (see §5.1). — `:765` — *"the guard that refuses the second start today (`controller.py:194-214`) covers this"* for two installs sharing 7878. That guard covers this app's own containers, not a foreign listener already on the port. |
| (vii) past tense; "how the code IS" belongs in a test | **PASS** | §2's tables are a design spec, the sanctioned place. |

Additional operator finding, not a §6 check: A never says how a client reaches `yulon-ubuntu`.
Its 8.3/8.4/8.5/8.6 gates all require an in-game effect, which needs the LAN step and the
`vmhost` 3.3.5a client (`gates/bug39-lan-press-2026-09-05/`, README §6, client
`LOGIN_OK`/`AUTH_OK` at `client-connection-20260906-0131.log:2-12`). B (`surface.md:446`) and C
(`risk.md:278`) both name it; A does not, and the kickoff §0 forbids the LAN step on any box
without an announcement and a checkpoint.

### B — `surface.md`

| Check | Verdict | Line and why |
|---|---|---|
| (i) DoD names live state | **FAIL ×3** | `:409` (8.1a) — *"after the press the banner reads 'commands: ready'; a Stop turns it to 'shutting down'…"* is the app's own line end to end. The Gate row adds the shell readbacks and a `curl`; the DoD does not. — `:411` (8.1c) — "the banner text; no set-up button anywhere on the tab" is app state; only "a Revive … answers within 5 s" is live. — `:424` (8.7b) — *"the key read back from the installed `.conf` on the box after Install"*. A file readback is not proof the **server** took the value: memory `bot-population-is-500` is exactly this, and C requires the boot log instead (`risk.md:311`). |
| (ii) "needs Y" names the whole mechanism | **FAIL** | `:150`, `:409` name `AC_SOAP_ENABLED=1` and never `SOAP.IP`. The mechanism as stated cannot work (§2). |
| (iii) no WotLK mechanism inherited | **PARTIAL FAIL** | `:216` renders `.account set password` as reachable over the channel on TBC/Vanilla with no note that the **argument count is unread**. `phase8-delta.md:21-22` and `:77` record that row as "read by hand after the report"; A carries `arity_verified: false` for precisely this (`seam.md:272`, `:355`). B inherits an argument shape nobody measured. |
| (iv) no DoD met from a CLI | **PASS — best of the three** | Every gate is a press through the real widget with a client screenshot per group (`:270`, `:294`, `:328`). |
| (v) no non-existent test names | **PASS** | Existing tests are marked (**exists**) with line numbers; the `test_docs_pins.py:55` id regex `^- \[x\] (\d+\.\d+[a-z]?) ` is quoted correctly — verified at `tests/test_docs_pins.py:55`, and it is the only design that checked its own box ids against it. |
| (vi) no "X cannot happen" | **FAIL** | `:108` — *"attach and SOAP are different transports on the same world queue, so they never share a tty and **cannot corrupt each other's reply**."* True of the tty, false of what matters: `azerothcore.md` §F has all three paths queueing into one `_cliCmdQueue` (`World.h:221`) drained once per `World::Update`, and `console.py:57-73` is single-writer — on Tortoise they *do* share the pty. C builds the interlock B declares unnecessary (`risk.md:56`). |
| (vii) past tense | **PASS** | |

### C — `risk.md`

| Check | Verdict | Line and why |
|---|---|---|
| (i) DoD names live state | **FAIL ×1 (executability)** | `:305` — `ss -ltn` inside the world container; not established to exist in the image. Otherwise the strongest set in the three: `:305` wrong-credential-file → 401 → Repair → verified; `:309` the blanked prefix must show the refusal, not 0; `:306` the snapshot file *ends with the halt line* and is ≤ 2 MiB. |
| (ii) "already has X" resolves | **PASS** | `apply.DockerSql.query` (`apply.py:504-528`), `families/conf.patch` (`conf.py:130`) / `apply_table` (`:243`), `docker.container_state` (`:1883`), `_logs` (`:1913`), `run_one_shot` (`:1357`) all exist; `accounts.py:536-548` really does look the id up **by name, never by `LAST_INSERT_ID()`** as `:72` claims. |
| (iii) no WotLK mechanism inherited | **PASS — best of the three** | `:42` refuses to name the MaNGOS namespace until it is measured; the read it draws on says the same (`cmangos.md` could-not-establish #2). |
| (iv) no DoD met from a CLI | **PARTIAL FAIL** | `:288`–`:296` mix tab presses with shell probes; `:296` (8.8) names no box at all. The presses are named, so this is a wording fix, not a design fault. |
| (v) no non-existent test names | **FAIL** | `:335` — *"Unit, no daemon, in the shapes `test_native.py`/`test_families_cmangos.py` use"*, and `:336` claims "nothing here names a test that does not exist at 7bc5ebd3 except those [marked new]". **`tests/test_native.py` does not exist**; the tree has `support_native.py`, `test_install_wiring.py`, `test_installer.py`. This is exactly the class `test_docs_pins.py` turns red the day a box ticks. |
| (vi) no "X cannot happen" | **PASS — best of the three** | §9's column header is literally "Measured price or enumerated routes", and `:372` prices the one claim a reviewer would attack ("the GM-3 account is a remote shell in front of the server") against what `.env`'s DB root already grants. |
| (vii) past tense | **PASS — best of the three** | §3's ledger and §4.0's fact table are dated measurements. |

---

## 4. What each design gets right that the others do not

Recorded so the winner does not lose it.

- **A:** `CatalogEntry.ops` with four complete JSON blocks (`seam.md:78-375`) — every per-tree
  command, column, cap, level and channel as `_Strict` data that `load_catalog()` refuses on a
  typo, plus `arity_verified: false` / `probe_expect: null` as **typed unverified markers** that
  `test_ops_catalog.py` keeps red until a gate measures them (`:112`, `:662-663`). And the
  blast-radius decision of the phase: the SOAP env goes in `ops`, not in `world_env`, so
  `tests/data/wotlk-rendered/` stays byte-identical and 7.1's fixture assertion keeps meaning what
  it meant (`:626-631`).
- **B:** one `Outcome`/`ChannelState` type and one `OutcomeLabel`/`ChannelBanner` widget so *"the
  three outcomes cannot be collapsed by a tab that forgot one"* (`surface.md:41`, `:88-92`); the
  three-shapes rule — the tree cannot → **not drawn**, the host cannot → drawn-disabled-with-reason,
  the server state forbids → drawn-disabled-with-the-state (`:26-30`); the per-family **lettered**
  boxes (`:409-427`), verified legal against `test_docs_pins.py:55`; and the only piece of gate
  reasoning in the three that *removes* work with a justification (`:446`, why bug §39's LAN press
  is not re-run).
- **C:** the `CommandQueue` with observable depth and the **console-pending interlock in both
  directions** (`risk.md:41`, `:56`); the write ledger (§3) and `test_write_ledger.py` (`:346`),
  which enumerates every write site by AST against a committed table *and* asserts the reverse —
  a deleted write cannot leave a stale ledger line; §4.0's facts table, the only place in the
  three where the world thread is priced with numbers and an instrument (`server info`'s
  `Update time diff` mean/median/p95/p99/max, `AC cs_server.cpp:275-294`) captured before and
  after each verb; the poll budget expressed as *1 command per 30 s on a thread that ticks ~6×/s
  = 1 in ~190 ticks* (`:160-167`); the complete server-state matrix including **foreign server on
  7878** and **WSL-resident** (`:130-140`); `AiPlayerbot.CommandServerPort = 0` closed in the same
  press as SOAP is opened, with `ss` asserting it arrived (`:152`); and 8.9 gated **on a throwaway
  install, never the 7.2 one** (`:294`) — the only design that protects the box's existing evidence.

---

## 5. Where the designs contradict each other on a fact

| # | The contradiction | Who is right, and the citation |
|---|---|---|
| 1 | **`SOAP.IP` inside the container.** A asserts `0.0.0.0` is required and "not a relaxation" (`seam.md:221-224`). C calls it "load-bearing, **unverified**" and books spike S1 (`risk.md:148`, `:358`). B never mentions the key (`surface.md:150`). | **C.** The reads record only the dist default `127.0.0.1` (`azerothcore.md` §A `worldserver.conf.dist:460`; `cmangos.md` §A `mangosd.conf.dist.in:1859`); nobody measured it. A's conclusion is very probably correct and is still a deduction — rule 1. **B is simply missing a required key** and its 8.1a gate cannot pass as written. |
| 2 | **Tortoise `pending_commands` as a channel.** A builds `QueueChannel` and gates Windows Tortoise on it (`seam.md:342-344`, `:591`, `:594`). C refuses it outright (`risk.md:43`, `:380`). B refuses it as a surface (`surface.md:169`, `:521`). | **C and B.** `cmangos.md` §A: no output returned to the caller (`World.cpp:3946-3948`), 60 s poll (`:845`), 250-char cap; could-not-establish **#4** says the `realm_id` source was never read and **#3** says no ordering guarantee against the stdin console exists. A one-outcome channel keyed on an unverified field is not a channel. |
| 3 | **The MaNGOS SOAP namespace.** A pins `urn:MaNGOS` as UNVERIFIED, to be measured at the 8.1c gate (`seam.md:301`). C refuses to name it until measured (`risk.md:42`). B omits it. | **C on the rule, A on the mechanism.** `cmangos.md` could-not-establish #2: `soapC.cpp` is generated and unread. Combine them: C's refusal to inherit, carried as A's typed unverified marker that a test keeps red. |
| 4 | **Where the channel credentials live.** A: `config_dir()/channels/`, "**never** under the server dir" (`seam.md:448`). C: `config_dir()/credentials/` (`risk.md:45`). B: `<server_dir>/.yulon-commands.json` (`surface.md:161`). | **A and C.** The server dir is what 8.9 deletes, what the user backs up, and what `composegen.install_id`'s docstring (`composegen.py:135-150`) exists because people copy. Both also carry the measured fact that a 0600 mode is a **no-op on Windows** (`families/conf.py:378-395`, measured 2026-09-01); B does not. |
| 5 | **Whether attach and SOAP can interfere.** B: they "cannot corrupt each other's reply" (`surface.md:108`). C: they reach the same world queue and, on Tortoise, the same pty, so a typed command is refused while `_console_pending` is true and vice versa (`risk.md:56`). | **C.** `azerothcore.md` §F: console, RA and SOAP all `QueueCliCommand` into one `_cliCmdQueue` (`World.h:221`), drained once per `World::Update` (`World.cpp:1341`). `console.py:57-73` is single-writer. |
| 6 | **Whether 8.2 closes bug line 552.** A: "the first tick fires at construction — closes line 552" (`seam.md:473`). B: same (`surface.md:186`). C: *"the dashboard is a second job on a slower timer, not a change to this one (bug line 552 stays a bug line; 8.2 does not fix it by side effect)"* (`risk.md:54`). | **Both, on different questions.** A/B are right that the one-line fix closes it; C is right that a Phase 8 box must not tick a bug box by side effect. Take the fix from A/B and C's discipline: the fix lands, the bug box ticks on its own evidence. |
| 7 | **Whether the CMaNGOS SOAP publish line invalidates Phase 7 evidence.** A: "No CMaNGOS compose-config fixture exists", so nothing breaks (`seam.md:632-635`). C: 7.4c/7.5/7.6 compose-config **captures** are stale for the `ports` block, plus 7.9 on each box and 7.10 (`risk.md:323`). | **Both are true; C's is the operator's answer.** There is no committed test fixture *and* there are committed gate captures. A answered the test question and called it the whole answer. |
| 8 | **Item mail idempotence.** C: mail is not idempotent — the button disables while any mail for that target is queued, sent mails are listed with their `mail.id`, and "the price of a press after an app restart is one extra mail: named, accepted" (`risk.md:199`). B: *"Every action is one press: none destroys or overwrites data … mail is additive"* (`surface.md:251`). A: silent — 8.4 has no second-press row (`seam.md:496-513`). | **C.** "Additive" *is* the non-idempotence, and kickoff §7 rule 5 requires the fixture that answers differently the second time. B calls the hazard a safety property; A does not ask. |

---

## 6. The grafts

Written for C as the winner. If the panel promotes A instead, the A-column grafts become A's own
content and **every C graft below becomes mandatory, not optional** — A scores 3/3 on the two
criteria that decide whether a gate proves anything.

### C must take from A, by name

| # | Graft | Where | Why the operator needs it |
|---|---|---|---|
| A1 | **`CatalogEntry.ops`** — the whole `Channels`/`Soap`/`GmLevel`/`Commands`/`BotMarker`/`Tables`/`LuaBridge` model and the four JSON blocks | `seam.md:78-149`, `:156-375` | C's `verbs.py` puts per-tree literals in Python. Per-tree facts belong where `_Strict` refuses a typo at `load_catalog()`, not in a module a reviewer has to read. |
| A2 | **`arity_verified: false` / `probe_expect: null` as typed unverified markers, enumerated by name in `test_ops_catalog.py`** | `seam.md:112`, `:662-663` | C has the right instinct ("measured on the first TBC gate, not inherited") and no mechanism to keep an unmeasured value red until a gate measures it. This is the `test_docs_pins.py` pattern applied to data. |
| A3 | **The `ops`-not-`world_env` placement, so `tests/data/wotlk-rendered/` stays byte-identical** | `seam.md:626-631` | C's A6 voluntarily invalidates the 7.4c/7.5/7.6 compose captures. Try A's placement for the CMaNGOS publish line before accepting three re-runs; if it cannot be done, the re-runs go in the cost table with their price. |
| A4 | **`dbreads.py` with per-tree column names on the entry, and the rule that a wrong name is a loud `DockerCommandError`, never an empty list** | `seam.md:49`, `:320`, `:766-769` | C never names a column per tree. Tortoise's `display_id` vs `displayid` and the unread CMaNGOS `characters` spellings are defects that would land in a live gate instead of in a unit test. |
| A5 | **`published_bindings()` read from the daemon and the refusal to persist while 7878 is published anywhere but `127.0.0.1`** | `seam.md:451`, `:730`; `docker.py:2302` (verified) | C asserts the host pin from the template. Rule 6: assert the value **arrives**, from the daemon, not from the file that declared it. |
| A6 | **`capability(entry, command_id, probe)` as the one predicate feeding both the button's absence and the sentence in its place** | `seam.md:430-437` | C draws a reason sentence but from a different place than the drawing decision — two places that can disagree. |
| A7 | **The negative assertion in 8.1's DoD: `server info` answers through SOAP with no attach client spawned (`ps` shows none)** | `seam.md:588` | Proves the new channel is the one that answered, not the old one. |

### C must take from B, by name

| # | Graft | Where | Why the operator needs it |
|---|---|---|---|
| B1 | **Per-family lettered boxes (8.1a / 8.1b / 8.1c …)** | `surface.md:409-427`; legal under `tests/test_docs_pins.py:55` | Load-bearing, not cosmetic: Vanilla is blocked behind a recompile nobody has scheduled (§2). A coarse `8.1` box cannot tick until Vanilla does; a lettered one lets WotLK, TBC and Tortoise tick on their own evidence. |
| B2 | **One `Outcome`/`ChannelState` type and one `OutcomeLabel`/`ChannelBanner` widget** | `surface.md:41`, `:88-92` | C's `Outcome` is a controller type with no rendering contract. One widget is what stops a tab collapsing the third outcome into one of the first two. |
| B3 | **The seven-state banner table with a distinct sentence per state, and `test_the_commands_banner_renders_all_seven_states`** | `surface.md:138-146`, `:456` | C's §4.1 matrix has the states and no proof each renders differently. |
| B4 | **The three shapes: the tree cannot → not drawn; the host cannot → drawn, disabled, with its reason; the server state forbids → drawn, disabled, state sentence** | `surface.md:26-30` | C says "never a greyed button with no sentence" without distinguishing the three, and the distinction is what a user needs to know whether to wait or to stop waiting. |
| B5 | **`uninstalled` signalled up; the window drops the tab and resets the Catalog tile; `state.forget()` last** | `surface.md:46`, `:113`, `:372` | C's 8.9 says nothing about who removes the tab, and a view that tears itself down is the crash nobody catches. |
| B6 | **`_set_busy()` widened to lock every Phase 8 button — and the reverse rule, that a command button never locks the Server tab** | `surface.md:107` | An operator must be able to press Stop while a teleport is in flight. |
| B7 | **The reasoning that bug §39's LAN press is not re-run, and why** | `surface.md:446` | The only place in the three where gate scope is *reduced* with a justification. Copy the form as well as the conclusion. |
| B8 | **In-game evidence per group, in the client, beside the tab's outcome and the row readback** | `surface.md:270` | C proves 8.4 "by rows" on Tortoise (`risk.md:202`). A row is the server's opinion of itself; the mailbox on screen is the feature. |

### What C must fix on its own, before the plan is written

1. **Move TBC and Vanilla off `yulon-fedora`** to `m910q` with Tortoise stopped and restored
   (announced), as A and B do — or price two fresh CMaNGOS installs under SELinux Enforcing on a
   VM sharing a host with a 4–5 GB worldserver, and get the owner's yes for the compiles.
2. **Add the `ACSoap.cpp:41-46` bind-failure row** to §4.1 and the clause to 8.1's DoD: after the
   recreate the world is `running`, `RestartCount` unchanged, ready marker seen this run. Measure
   the TBC/Vanilla equivalent at `Master.cpp:248-249` at the 8.1b gate rather than inheriting it.
3. **Replace `ss -ltn` inside the container** with `docker port` on the host plus a connect
   attempt from a second container on the compose network — or prove `iproute2` is in the image
   first, in the same breath.
4. **Fix `test_native.py`** (`risk.md:335`) — it does not exist; the shapes are
   `test_installer.py` / `test_install_wiring.py` / `support_native.py`.
5. **Book spike S1's owner yes now** (`risk.md:407`), not at gate time: it needs two conf keys and
   one worldserver restart on the 7.2 install, which kickoff §4 forbids without an explicit yes,
   and every other design's 8.1 depends on its answer.

### Rejected, by name

- **A's `QueueChannel` / Tortoise `pending_commands` writer** (`seam.md:342-344`, gates `:591`,
  `:594`) — §2. Native-Windows Tortoise has no command channel in Phase 8; say so on the tab
  (B's `NO_TTY_HELP` shape, `surface.md:145`) and let the row routes carry 8.3.
- **B's periodic channel probe** (`surface.md:106`) — §2. The probe is on demand: Refresh, after
  Start reaches ready, and before a write (A's rule, `seam.md:437`).
- **B's credential location** (`surface.md:161`) — §5.4.
- **A's assertion that `SOAP.IP = 0.0.0.0` needs no measurement** (`seam.md:221-224`) — §5.1.
  Keep A's conclusion as the design's *intent* and C's spike as the thing that makes it a fact.

---

## 7. Gate cost table

What each step costs on the boxes it names, after the grafts. **Machine** is unattended wall-clock
on the box; **human** is time at a keyboard or a client. Grounding, stated once:

- `yulon-ubuntu` holds the finished 7.2 WotLK install, Off at baseline
  (`notes/STATE-2026-09-06-morning.md:40-41`); a Start's ready budget is 480 s (`docker.py:39`) and a
  Stop's measured drains were 90.7 / 73.4 / 58.3 s at ~1980 characters (`docker.py:938-980`).
- A CMaNGOS install from source is a compile. The only measured figure in the record is
  **10800 s** (Windows Tortoise, memory `windows-gate-box-recipes`); Linux is faster and has never
  been timed end to end for TBC or Vanilla, so "hours" is the honest number.
- A worldserver **rebuild is 2–4 h and the owner starts it** (kickoff §0, memory
  `ask-before-any-rebuild`).
- An in-game check on `yulon-ubuntu` needs the LAN step plus the `vmhost` 3.3.5a client
  (`gates/bug39-lan-press-2026-09-05/`, whole press window 22:59→23:36 UTC, the two presses
  ≈1 s each). On `m910q` it needs a TeamViewer session against its own clients (memory
  `ubuntu-test-box-m910q`, headless HDMI forced on 2026-08-23).
- The 7.9 re-run is `gates/gate-79-controller-surface.py` driven through
  `ControllerServices.for_entry()`; the 7.10 re-run was 88 checks whose longest driver was 101 s
  (`gates/7.10-ubuntu-2026-09-05-rerun/README.md:26-37`).

| Step | Boxes | Machine | Human presses (client login / in-game check) | Rebuilds |
|---|---|---|---|---|
| **8.1a** WotLK | `yulon-ubuntu` + `vmhost` client | ~30 min: baseline capture, Set up, recreate (stop 60–90 s + ready ≤ 480 s), probe, wrong-credential → Repair → verified | LAN step (announced, from checkpoint) + 1 client login to prove the channel did not break auth | 0 |
| **8.1b** TBC | `m910q`, Tortoise stopped and restored | ~45 min **if** the exited `tbc-*` install still reaches `ready`; unknown if it hit the `patch-sources` refusal | none (the probe reply is the proof); the `//gsoap` namespace line and the `server info` reply are recorded and the catalog pinned to them | 0 — **or 1 (hours) if TBC is in the same state as Vanilla** |
| **8.1c** Vanilla | `m910q` | **BLOCKED** — `notes/STATE-2026-09-06-morning.md:34-36`: the install cannot reach `ready` again | — | **1 CMaNGOS recompile, owner's, hours** |
| **8.1d** Tortoise | `m910q` (running since 2026-08-26) | ~20 min: attach probe + the "no remote channel" sentence | none | 0 |
| **8.1e** WotLK, native Windows | `yulon-win11-gate` | ~40 min + Docker Desktop's interactive-session dance (memory `windows-gate-box-recipes`) | none | 0 |
| **8.2** dashboard + snapshot | all four | ~20 min per game: hand counts, two forced `docker kill`s, Stop → snapshot file | 1 client login per game to move the player count | 0 |
| **8.3** accounts | `yulon-ubuntu` + `vmhost`; `m910q` ×2–3 | ~20 min per game | **1 client login per game with the new password, and one refused with the old** — the DoD | 0 |
| **8.4** Play tab | `yulon-ubuntu` + `vmhost`; `m910q` ×2–3 | ~60 min per game (8 verbs × online + offline, each with a before/after row read and a `server info` diff summary) | **~8 in-game checks per game** — the largest human cost in the phase; on `m910q` each is a TeamViewer session | 0 |
| **8.5** Browse Bots | all four | ~15 min per game | 1 `/who` per game | 0 |
| **8.6** My Party | `yulon-ubuntu` + `vmhost` | ~45 min after the image exists | 1 client login + party-frame screenshot; `dismiss all` | **1 rebuild with mod-ale, 2–4 h, owner's** |
| **8.7** modules | `yulon-ubuntu`; one CMaNGOS box | ~30 min WotLK + ~20 min CMaNGOS | none, unless the manifest's own in-game effect is claimed | 0 for a conf manifest; **1 if a `build.rebuild` module is gated** |
| **8.8** Steam | **no box exists** | — | — | 0 — blocked on the owner naming a machine with Steam |
| **8.9** purge | `yulon-ubuntu` **throwaway** install; one CMaNGOS box | ~2 h per box: install → purge(keep) → reinstall → install → purge(unticked) | 1 client login to see the kept character on the login screen | 0 on WotLK (image cached); **1 on any CMaNGOS box without a usable install** |
| **7.9 re-run** (required: `Controller.stop()` gains the snapshot hook) | all four | ~20 min per game, `gate-79-controller-surface.py` | none | 0 |
| **7.10 re-run** (required before the exit box) | `yulon-ubuntu` | ~30 min, 88 checks | none | 0 |

**Totals as the designs stand:** ≥ 2 owner-run builds are unavoidable (one mod-ale rebuild for
8.6; at least one CMaNGOS recompile for Vanilla), and a third is likely (TBC on `m910q`, state
unknown). C as written adds two more (the fedora installs) for no gate that `m910q` cannot give.
Roughly **35–40 in-game checks by a human**, about two thirds of them on `m910q` over TeamViewer.
The unattended machine time is not the constraint; the builds and the client sessions are.

**One thing the owner can do in under two minutes to reduce it:** say whether Vanilla's gates may
be met on a **fresh throwaway install** on `m910q` (which 8.9 needs anyway) rather than on the
folder that can no longer reach `ready` — that removes one recompile from every Vanilla row and
folds 8.9b's install into 8.1c's.
