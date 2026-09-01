# Phase 6 gap matrix — what each platform has actually proven

> Built 2026-08-31 from `pyplan/checklist.md` (Phase 6, lines 71–229, plus Cross-cutting), the
> `pyplan/macos-gate-run-sheet.md` step list, and — where the checklist is silent but the code is
> not — docstrings in `pylauncher/yulon/`. Read-only pass; nothing else in the tree was changed.
>
> **The question this was built to answer:** *"Baerthe has done a lot of macOS testing. What has
> macOS done that Windows, Ubuntu, Arch and Fedora have NOT?"*
>
> **The short answer is: nothing, at the feature level.** macOS closed four of the seventeen rows
> below on 2026-08-29 (install, lifecycle, console, accounts) and every one of those four was
> already proven on Linux and/or Windows. macOS remains the *most behind* platform, not the most
> advanced: seven rows on it are unit-tests-only or not done, and three of those (teardown, repair,
> backup/restore) are proven on both other platforms. The 2026-08-29 macOS session was a
> catch-up run, not a lead. The real gaps this matrix exposes are on **Linux** (four rows with no
> gate written down at all) and on **Windows** (two ticked boxes with prose-only evidence).

## Legend

| Mark | Meaning |
|---|---|
| **LIVE** | Run against real hardware/real Docker, with a date and measured evidence in the record |
| **UNIT** | Automated tests only — a fake seam, a dry-run plan, or a mocked `platform_id`. Box is typically **unticked** and the checklist says so |
| **PROSE** | Box is **ticked** but the evidence is a sentence with no measurement, no command, and no committed artifact |
| **—** | Not done anywhere on this platform |
| **N/A** | Out of scope on this platform by design, with the reason recorded |

Column "Linux" means Ubuntu unless a distro is named. Where a gate ran only on Fedora or only on
Arch, that is stated — the record deliberately does not treat the three as one platform.

---

## The matrix — 17 feature areas

| # | Feature area | macOS | Ubuntu / Linux | Fedora | Arch | Windows 11 |
|---|---|---|---|---|---|---|
| 1 | **Platform gating** (6.1) — Install disabled with a reason off-platform, `UnsupportedPlatformError` before any subprocess | **LIVE** 2026-08-29 (WotLK Install dispatches to `NativeInstaller`, install ran) | **LIVE** 2026-08-24 (`CatalogView.button_for("wow-wotlk").click()` drove a real install) | **LIVE** 2026-08-25 (AppImage GUI, catalog rendered on clean Fedora 44) | **LIVE** 2026-08-25 (tar.gz GUI on clean Arch + Xfce) | **LIVE** 2026-08-25 (clean box: TBC/Vanilla/Tortoise show "Installer needs Linux", WotLK enabled) |
| 2 | **Interactive input / sudo on a pty** (6.1.5) | N/A — native engine uses no `sudo`; checklist: "not yet exercised: the macOS/Windows variants' own prompts" | **LIVE** 2026-08-24 on the Ubuntu VM with sudo forced to demand a password: pipes → 0 asks + *"sudo: a terminal is required"*; pty + `SUDO_PROMPT` marker → asked with the exact marker, nothing echoed | — | — | N/A — same reason |
| 3 | **Provisioning: install Docker from nothing** | **UNIT** — `test_macos_plan_downloads_dmg_and_copies_the_app` (dry-run plan, `Path.exists` pinned false). No Mac has ever provisioned; the M4 Pro already had Docker Desktop 4.87.0 | **LIVE** 2026-08-26 cold `clean-ssh` Ubuntu 24.04: apt repository, engine, service enable, all unattended, inside the 4010.6 s install | Not separately gated — the 2026-08-25 AppImage run started from a cold `clean-desktop` with **no Docker**, so provisioning ran | Not separately gated — same, cold `clean-desktop` Arch | **LIVE-with-caveat.** 2026-08-23 `yulon.exe --provision` on a never-had-Docker box: `ok:true`, `docker_ready:true`, no manual steps. 2026-08-25 clean box: WSL2 + reboot (exit 3) + silent Docker Desktop → engine 29.7.2. **But** 2026-08-26 needed Microsoft's WSL MSI 2.7.12 by hand and `docker desktop start`, because the inbox `wsl.exe` is a stub. **The record contradicts itself and nobody has reconciled it** |
| 4 | **Privilege transparency** — no silent group join, no `sudoers.d`/NOPASSWD, no socket `chmod` | **UNIT** — `test_macos_provisioning_never_escalates_privileges` (2026-08-24, non-dry, asserts the emitted argv) | **LIVE** 2026-08-24 in a throwaway `ubuntu:24.04` container: three users, three answers, `id -nG` confirmed independently; 8 mutations all died. Re-confirmed on the 2026-08-26 cold gate (nobody to ask → **refused**, Docker installed anyway). *Still open: nobody has seen the dialog on a real screen during a real install* | — | — | **UNIT** — `test_windows_provisioning_never_escalates_privileges` (2026-08-24, non-dry, records the `-Verb RunAs` UAC prompt so the elevated path is traversed) |
| 5 | **Install: cold start → running server** | **LIVE** 2026-08-29, Apple M4 Pro, Docker Desktop 4.87.0 / engine 29.7.2, via `python -m yulon.catalog.installer wow-wotlk`. `ac-db-import` **Exited (0)**, schemas **22 / 111 / 315 / 30**, ~15 min compile of 1829 objects. Found + fixed the `ContainerGit` fresh-mount-race retry | **LIVE** 2026-08-26, `yulon-ubuntu` from `clean-ssh`, single uninterrupted run: **4010.6 s (67 min), exit 0**, 5385 log lines, schemas **22 / 111 / 315 / 30**, worldserver mean diff 50 ms | **LIVE** 2026-08-25, clean Fedora 44 **through the AppImage GUI**, from a cold checkpoint. Took three fixes (picker dead-end, dead `server_dir_problem()` on Linux, reachable `sudo rm -rf /home`) + the SELinux `container_file_t` relabel | **LIVE** 2026-08-25, clean Arch + Xfce, the **default** `install-wow-wotlk.sh` variant no gate had ever run (pacman falls through `script_variants`); compile finished, playerbots 705/1778 logging in, 3724/8085 listening | **LIVE** 2026-08-26, clean box from `clean-nested-virt`, native engine **from the GUI**. `ac-db-import` **Exited (0)**, schemas **22 / 111 / 315 / 30**, image tag `native-6d51a632`, bots 1233 idle / 583 quests / 142 rewarded. *6.3's box stays unticked: three steps needed a human (two reboots, the WSL MSI, `docker desktop start`)* |
| 6 | **Server lifecycle** — start/stop/status/health poll + README §12 port-conflict guard | **LIVE** 2026-08-29 — `stop_staged()` **13.8 s** (containers kept), `start_staged()` **5.8 s**, `status()` correct before/after; `port_conflicts_for()` named our own `ac-worldserver`/`ac-authserver`. Docker Desktop for Mac does **not** reproduce Linux's 300 s drain | **PARTIAL — no lifecycle sub-bullet exists.** `stop_staged()` measured end-to-end 2026-08-23 at 1980 chars online (**90.7 / 73.4 / 58.3 s**, exit 0) and a plain 10 s `compose stop` SIGKILLed the worldserver (exit 137). **Never gated: the README §12 foreign-container guard, `port_conflicts()`, `wait_db_healthy`, `wait_ready` timings** | — | — | **LIVE** 2026-08-23 — 23/23 on `yulon-win11`. `start()` 3.2 s, `wait_db_healthy` 0.1 s, `wait_ready(127.0.0.1, 8085)` 27.7 s, **stop 8.8 s** containers kept. §12 guard: foreign container on 3724 → `PortConflictError` naming `yulon-port-hog`, nothing started, `port_conflicts()` excused our own three. Plus 18 fallback checks (compose file hidden: stop/remove REFUSE; pinned: by-name `docker stop -t 300` in 7.4 s) |
| 7 | **Teardown — remove containers, volumes survive** | **UNIT** 2026-08-29 — "Automated Darwin unit tests verified: `remove_staged` volume preservation". Box **unticked**. Baerthe's step 6 not run | **LIVE** 2026-08-23 on the 650-account playerbots install — all four containers gone, **both volumes present**, stack restarted, `ac-db-import` **not** recreated, the marked row read back byte-identical, 14/14 checks, 4 mutations died | — | — | **LIVE** 2026-08-23 — 18/18. `Controller.remove()` **7.5 s**, all five containers, **no volume at all**; restart from nothing **14.5 s**, `ac-db-import` not recreated, every account row byte-identical |
| 8 | **Repair / re-import, incl. the `partial` interrupted-import case** | **UNIT** 2026-08-29 — "`ImportProbe` state transitions and `repair_import` reset flow". Box **unticked**. Producing the state needs a deliberate `docker kill ac-db-import` on a Mac | **LIVE** 2026-08-23, twice. First `partial` gate found the probe called 3-tables-of-316 `imported` and the repair made the DB **permanently unimportable** (`acore_world` 3→5 tables, `updates` +2671 rows). `reset_unfinished()` then gated against a fresh real interruption: probe `partial`, drop, re-import **195 s**, back to **316 tables**, same container `882faf1762a1`, 10/10 checks | — | — | **LIVE** 2026-08-23 — 29/29 against a REAL interruption (`docker kill ac-db-import` 25 s in, `acore_world` at 26 of ~312, no updater record). No-reset repair **refused**; with reset, **292.3 s**, **same container `ef8ec937fbf0`**, back to 312 tables, other two schemas untouched |
| 9 | **Backup + restore round trip** | **UNIT** 2026-08-29 — "backup and restore plans, MariaDB 10.6 sandbox mode parsing, merge-based restore semantics". Box **unticked**. The open question is a Mac-specific one: multi-hundred-MB dumps through a bind mount is where Docker Desktop for Mac is slowest and a Linux-comfortable timeout may not hold | **LIVE** 2026-08-23 — four schemas, a **292.2 MB** `acore_world` dump, restored value read back, wrong token refused. *Weaker than the Windows run: it checked that the value came back, not that nothing extra survived* | — | — | **LIVE** 2026-08-23 — **and this gate found what Linux missed**: restore is a MERGE, not a replacement. A marker table made after the backup survived a full **306 MB** restore — **313 tables where the backup held 312** — because mysqldump emits per-table `DROP TABLE IF EXISTS` and no `DROP DATABASE`. Behaviour deliberately unchanged; the argv is now pinned |
| 10 | **Server console — attach, send, detach without disturbing the server** | **LIVE** 2026-08-29 — `send_command()` twice against the live playerbots worldserver (`server info`, `account onlinelist` returning hundreds of bot session lines), each reply cut to its own command, `docker inspect` read `RestartCount=0` and the same `State.Pid`/`StartedAt` before and after both cycles | **LIVE — but the checklist does not say so.** The evidence is in `controller_wow_wotlk/console.py`'s `send_command()` docstring: *"~40 attach/detach cycles left the container's `State.Pid` at 69960, `RestartCount` at 0 and `StartedAt` unchanged, `docker logs -f` still streamed afterwards, and no `docker attach` client was left behind"* (2026-08-23), and *"live-verified on the Ubuntu VM, 2026-08-21"* for the pty transport. **6.5's Console line has no Linux sub-bullet, so the record reads macOS-only.** A documentation gap, not a testing gap | — | — | **N/A by design** — `send_command()` refuses on `pty_supported()` first; the Send button and command box are disabled with the reason on the tab (re-scope landed 2026-08-23). Account creation was moved off the console (SRP6) precisely so Windows loses nothing |
| 11 | **Account creation (SRP6 over `DockerSql`)** | **LIVE** 2026-08-29 — non-ASCII password `Café1234` (the exact `str.upper()` vs `fold()` case): first call `created=True`, second `created=False` "already exists", no duplicate row | **LIVE** 2026-08-24 — inside the button-driven install: the server "took an account (and refused the duplicate)". Byte-exactness against server-written verifiers was verified 2026-08-23 | — | — | **PROSE** 2026-08-23 — ticked, and the sentence is specific ("created through the real `accounts.create_account()` over `DockerSql`… non-ASCII password round-tripped; gm level written") but carries **no timing, no row count, no command, no committed transcript**. The Phase 6 preamble itself admits *"two gates were left on the author's word that a reviewer reproduced locally in seconds"* |
| 12 | **Real client login (a WoW client actually connecting)** | — explicitly owed: *"Login through a real client is still owed (needs a client install, out of scope for this pass)"* | — | — | — | — **Not proven on any platform.** The only place it is even scheduled is Phase 7's gate at checklist line 246 |
| 13 | **Networking / LAN auto-setup** — firewall (ufw/firewalld/netsh/ALF), WSL2 portproxy, LAN + public IP detection, realmlist updater + client writer, 0.0.0.0 binding check, CGNAT/DuckDNS/router steps | **UNIT** 2026-08-29 for the read half only (`detect_alf_state()`, `alf_unblock_commands()`). The **apply** half is deliberately not asked for: nothing mutates the Application Firewall because every change needs root and this path never asks for a password | — no gate. Implementation ticked at Phase 3.4; `firewall_commands()` for ufw/firewalld never live-run | — | — | — no gate. Cross-cutting claims *"live on Windows today… the `docker exec … mysql` behind a module apply and the **realmlist UPDATE**"*, but that is a capability sentence with no measurement — see flag 2. `netsh` portproxy never live-run |
| 14 | **Modules / mods** — apply, rebuild, restart, remove; manifest store GitHub refresh + bundled fallback | — Baerthe's step 10, not run. Flagged as "the longest of these by far" (the rebuild compiles AzerothCore); wall-clock on Apple silicon through Docker Desktop is unknown | — no 6.5 sub-bullet on any platform | — | — | **PROSE** — same Cross-cutting sentence as row 13; no ticked box, no numbers |
| 15 | **Self-update check + `config_dir()` placement** | **PARTIAL/UNIT.** `config_dir()` → `~/Library/Application Support/yulon` and `platform.detect()` → `macos` were observed on a **real Darwin interpreter** 2026-08-24 (Baerthe's corporate Mac) and re-asserted by unit tests 2026-08-29 — but **the Gatekeeper question the box was written for is unanswered**: does the update check stall behind Gatekeeper? | — no gate on Linux at all | — | — | **PROSE** 2026-08-23 — ticked on one sentence: *"`platform.detect()` → `windows`, `config_dir()` → `%APPDATA%\yulon`, the update check does not stall"*. No elapsed time for "does not stall", no command, no artifact. **This is the strongest flag-2 row in Phase 6** |
| 16 | **Packaging — run the CI-built artifact, not `python main.py`** | — **the `.dmg` has never been run as a gate.** But the box's own words ("the one artifact nobody has ever launched") are **now wrong**: Discord testers ran the 0.6.53 `.dmg` on real Macs on 2026-08-25, -26 and -27, and got far enough to hit `docker_programs()`, the credential-helper PATH bug (#113), the bind-mount probe bug (#115) and the containerized clone. **What is still unanswered is Gatekeeper**, and that is a shipping decision, not a test result | **LIVE (start-only) on 22.04** — the shipped tarball died with `libm.so.6: version 'GLIBC_2.38' not found` on real Ubuntu 22.04.5; pinned to `ubuntu-22.04` and confirmed to start there. No full install gate through the artifact on Ubuntu | **LIVE** 2026-08-25 — `.AppImage` launched and driven on clean Fedora 44 (Wayland; forcing `xcb` exercises bundled libxcb-cursor) all the way to a running server | **LIVE** 2026-08-25 — the FUSE-less path: Arch has no `fusermount`, so the `.tar.gz` is the only artifact that runs there, and it did. **It also caught the shipped tarball aborting on `libxkbcommon-x11.so.0`** (the "xcb-cursor0 is needed" message is a red herring); `check-bundle-closure.sh` then named five missing sonames | **LIVE, unrecorded.** The 2026-08-25 clean-box run was driven from the shipped `Yulon-v0.6.51Public-windows-x64.zip` — provision, GUI, catalog render, native engine to `build_staged()`. **6.5's Packaging line has no Windows sub-bullet even though this evidence exists** |
| 17 | **Full test suite + type/lint on the platform's own hardware** (6.4) | **LIVE** 2026-08-29 — Apple M4 Pro: `pytest` **1039 passed, 8 skipped**, `mypy` 0 issues / 37 files, `ruff`, `black --check`. Found and fixed one test that leaked real filesystem state | **LIVE** — CI matrix, both Python versions | via CI | via CI | Green on the dev box, **with ~20 skips** (`skipif(sys.platform.startswith("win"))`) — and that skip set is exactly how the SELinux-relabel harness bug reached `Yulon` and made CI red for six hours (Cross-cutting, 2026-08-25). No recorded on-hardware gate |

**Count: 17 feature areas × 5 platforms = 85 cells.**

---

## Flag 1 — features macOS is the ONLY platform to have proven live

**Strictly: none.** Every one of the four rows macOS closed on 2026-08-29 (install, lifecycle,
console, accounts) was already proven on Linux, Windows, or both. The owner's premise is inverted:
macOS is the platform furthest from Phase 6's exit criterion, not the one out in front.

Two near-misses, worth naming because they look like flag-1 items until you read the code:

1. **Server console (row 10) — macOS-only in the written record, not in fact.** 6.5's Console line
   carries a macOS sub-bullet and no Linux one, so the checklist reads as if only macOS has proven
   it. The Linux evidence exists and is stronger (~40 attach/detach cycles vs. 2), but it lives in
   `console.py`'s `send_command()` docstring and in `_open_pty()`'s comment, never in `checklist.md`.
   **Closing this gap is a transcription job plus one confirming run**, not new testing.
2. **On-hardware toolchain gate (row 17).** macOS is the only non-Linux platform with a recorded
   full `pytest` + `mypy` + `ruff` + `black` run on its own hardware. Windows runs the suite daily
   on the dev box but with ~20 platform skips and no gate written down — and that skip set has
   already let one bug through to a red `Yulon`.

The single thing macOS genuinely did that no other platform did is a **measurement, not a feature**:
Docker Desktop for Mac stops a live playerbots worldserver in **13.8 s**, nowhere near Linux's
58–91 s drain that `STOP_GRACE_SECONDS = 300` was sized from. That is a data point for Phase 7's
grace-period work, not a coverage gap on the other platforms.

---

## Flag 2 — ticked boxes whose evidence is prose only

No measured number, no command, no committed artifact. All three are Windows, all three are from
the 2026-08-23 run, and the Phase 6 preamble already half-admits it: *"two gates were left on the
author's word that a reviewer reproduced locally in seconds."*

| Where | Box | What is actually written | What it would take |
|---|---|---|---|
| checklist.md:221 | **6.5 Self-update, Windows** `[x]` | *"`platform.detect()` → `windows`, `config_dir()` → `%APPDATA%\yulon`, the update check does not stall"* | An elapsed time for "does not stall" and the printed `UpdateCheck`. Runs in under a minute, needs no server |
| checklist.md:212 | **6.5 Account creation, Windows** `[x]` | Four clauses, no numbers: created / already-exists / non-ASCII round-tripped / gm level written | The `created=True`→`created=False` transcript and a `SELECT` row count, the way the macOS line at :213 does it |
| checklist.md:986–988 | Cross-cutting capability claim (not a box) | *"Live on Windows today… `docker logs -f`, and the `docker exec … mysql` behind a module apply and the realmlist UPDATE"* | This sentence is the **only** Windows evidence behind rows 13 and 14, and it is a reachability claim, not a gate. The note beside it even warns that *"two successive commit messages claimed more than this and were corrected"* |

Related, and worth reading in the same breath even though the box is **unticked**: the second
Windows clean-box attempt (checklist.md:129–136) carries its own honesty note — *"everything below
about how the box BEHAVED is field observation with no log committed anywhere in this repo."* That
is the model the three rows above should be held to.

One more, self-declared: **6.0** (`[x]`) says the DoD's third verb, *run*, is not re-evidenced after
the script rehome, and rests on `git show --stat fcd95c5` showing pure renames. That is a
defensible argument rather than a measurement, and it says so.

---

## Flag 3 — what needs hardware, and what could close tonight

### Needs Baerthe's Mac (nothing on this side can substitute)

| Gap | Run sheet |
|---|---|
| Teardown (row 7) | `macos-gate-run-sheet.md` step 6, first half: `docker.remove_staged(SPEC, server_dir)`, then `docker volume ls` to confirm both volumes survive, `start_staged()`, confirm `ac-db-import` is **not** recreated and account rows read back identical |
| Repair / `partial` (row 8) | Same step 6, second half. Requires manufacturing the state: start a fresh import, `docker kill ac-db-import` ~20 s in, confirm the probe reads `partial` and `acore_world` is short, then `Controller.repair_import()` and re-count tables. Pin the container ID before and after |
| Backup / restore (row 9) | Step 9. Back up a populated server, restore it, confirm the wrong-token refusal still refuses. **Record the wall-clock** — the dumps move through a bind mount, Docker Desktop for Mac's slowest path, and a Linux-comfortable timeout may not hold |
| Modules (row 14) | Step 10, last because the rebuild compiles AzerothCore. Apply a module → rebuild → restart → confirm live → remove → confirm gone. Record the wall-clock; nobody knows what that build costs on Apple silicon |
| `.dmg` + Gatekeeper (row 16) | Step 2. Needs no server, so it is the cheapest thing on his list. Wanted back: the **exact** wording of any Gatekeeper dialog and the exact click path past it |
| macOS provisioning from nothing (row 3) | Only reproducible on a Mac that has never had Docker Desktop. Probably not worth asking for |
| Update check behind Gatekeeper (row 15) | Step 3, needs no server, ~10 min |

### Closable on an existing VM tonight

Ordered most valuable first.

**1. Linux server console — write the gate down, then re-run it (yulon-ubuntu).**
Value: it is the one row that currently reads as macOS-only, and the evidence already exists.
Needs a running WotLK server (the playerbots install on `yulon-ubuntu`, or the script install
still standing on the Fedora box as of 2026-08-31).

```bash
ssh yulon-ubuntu 'cd ~/dads-mmo-lab/pylauncher && python3 - <<PY
from yulon.controller_wow_wotlk import console, docker_ctl
import json, subprocess
def insp(f): return subprocess.run(["docker","inspect","-f",f,docker_ctl.SPEC.world],
                                   capture_output=True,text=True).stdout.strip()
before = (insp("{{.State.Pid}}"), insp("{{.RestartCount}}"), insp("{{.State.StartedAt}}"))
for cmd in ("server info", "account onlinelist"):
    r = console.send_command(cmd)
    print(cmd, "->", r.text.splitlines()[:5])
after = (insp("{{.State.Pid}}"), insp("{{.RestartCount}}"), insp("{{.State.StartedAt}}"))
print("before", before); print("after ", after); print("UNDISTURBED" if before==after else "DISTURBED")
PY'
```
Then add the resulting transcript as a `- [x] **Linux (date)**` sub-bullet under 6.5's Console
line, and cite `console.py`'s existing 2026-08-21/2026-08-23 measurements beside it.

**2. Windows self-update + accounts, re-evidenced with a committed transcript (yulon-win11).**
Value: closes both flag-2 rows. The update half needs **no server at all**, so it runs in minutes
on a box that is otherwise idle.

```powershell
ssh yulon-win11 "cd C:\yulon\pylauncher; python -c \"import time; from yulon import platform, update; print(platform.detect()); print(platform.config_dir()); t=time.time(); print(update.check_for_update()); print('elapsed', round(time.time()-t,2), 's')\"" | Tee-Object windows-update-gate.txt
```
For the accounts half, with the server up, re-run `accounts.create_account()` twice on one name and
paste the two `created=` values plus a `SELECT COUNT(*) FROM acore_auth.account WHERE username=…`.

**3. Linux lifecycle gate — the README §12 port-conflict guard (yulon-ubuntu).**
Value: row 6 is the only feature proven on **both** other platforms and never gated on Linux, and
the §12 guard is the half nobody has run there. Needs the server up.

```bash
ssh yulon-ubuntu 'docker run -d --name yulon-port-hog -p 3724:3724 busybox sleep 600 && \
  cd ~/dads-mmo-lab/pylauncher && python3 -c "
from yulon import docker
from yulon.controller_wow_wotlk import docker_ctl
print(docker.port_conflicts((3724, 8085)))
print(docker.port_conflicts_for(docker_ctl.SPEC))
"; docker rm -f yulon-port-hog'
```
Expect the foreign container named in the first list and our own three excused in the second.
Follow it with timed `stop_staged()` / `start_staged()` calls so Linux has the same three numbers
Windows and macOS both have.

**Also cheap, and pure bookkeeping (no run needed):**
- Add a **Windows** sub-bullet to 6.5's Packaging line citing the 2026-08-25 clean-box run of
  `Yulon-v0.6.51Public-windows-x64.zip`. The evidence is already in the tree; only the sub-bullet is
  missing (row 16).
- Correct the macOS Packaging box's "the one artifact nobody has ever launched" — three Discord
  testers launched the 0.6.53 `.dmg` between 2026-08-25 and -27. What is unanswered is Gatekeeper,
  which is a narrower and more honest claim.
- Reconcile the two Windows provisioning accounts (row 3): 2026-08-25 says `--provision` installed
  WSL2 unattended; 2026-08-26 says the inbox `wsl.exe` is a stub and only Microsoft's MSI 2.7.12
  makes the printed remediation succeed. One of those two boxes is describing a machine that was
  not as clean as it claimed.

### Needs hardware nobody has scheduled

**Real client login (row 12)** is not proven on any platform and is not on any macOS run sheet. The
clients exist (`~/clients` on m910q, per the WoW-client-downloads record), so this is reachable —
but it needs a client box with a GUI pointed at a VM's LAN address, plus row 13's networking step
in front of it. That is an evening of its own, not a tonight job, and it is the honest blocker on
"100% working feature coverage": a server that no client has ever logged into has not been proven
to work.
