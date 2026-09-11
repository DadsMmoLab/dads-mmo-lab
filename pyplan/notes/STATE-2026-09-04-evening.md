# Where things stand, 2026-09-04 evening — written for the session that picks this up

The owner is restarting the laptop. Everything below either survives that restart or is recorded
here because it will not. Read this before `resume-2026-09-05.md`, which is now older than it.

## Phase 7: 7 of 12

Ticked: 7.3, 7.4a, 7.4b, 7.4c, 7.5, 7.6, 7.9. Open: 7.1, 7.2, 7.7, 7.8 (macOS, hardware), 7.10.

## Running right now, and it survives the restart

**The 7.1 gate, press 2, on `yulon-ubuntu`.** A full WotLK install from the genuinely clean
`clean-ssh` checkpoint. It is a lingering `systemd --user` unit, so a laptop reboot does not touch
it.

```
ssh yulon-ubuntu "systemctl --user is-active gate71-press2; tail -5 ~/gate71-press2.log"
```

At the time of writing: step 4 of 9, `build`. Expect roughly 1½–2 h from 19:30 CEST.
Checkout at `~/gate71` (branch tip `a13a5de`), venv at `~/gate71/pylauncher/.venv`.

**Press 1 already passed and its transcript is the evidence** (`~/gate71-press1.log`, not yet
copied back). It is the first run to satisfy clauses 1, 3 and 4 together, because this checkpoint
is genuinely clean where `pre-7.2-gate-2026-09-02` is not:

* `state-before` — `docker --version: NOT INSTALLED (no such executable)`, `systemctl is-active
  docker: inactive`, `id -Gn` without `docker`, no `~/wowserver`;
* `answered group x1` — the consent dialog asked and answered;
* the re-login refusal, verbatim;
* `state-after` — Docker 29.1.3 **active**, and `id -Gn` still without `docker`, which is the
  documented behaviour of a process that cannot pick up a new group.

Press 2 runs under `sg docker -c`, and its own `state-before` shows `docker` present in `id -Gn`.
That pair of captures is what the 2026-08-31 run could not distinguish and the audit called for.

**TBC on `yulon-win11-gate`**, a scheduled task, also survives. It was at mmaps ~355 of ~2819 and
moving at ~27 files/min. Counts so far: dbc 185 and maps 3586 identical to Linux, Buildings 5431
(the case-folded count, PREDICTED before the run and confirmed to the file), vmaps 8607 against
Linux's 8099.

## What needs doing when the install finishes

1. **Point the realm at `100.71.125.58`.** Do it by updating the realm row directly.
   **Do NOT run the LAN step** — that is bug §39, which enables `ufw` with only the game ports
   allowed and cuts SSH to the box. There is no console on that VM worth relying on.
2. **Create an account through `ControllerServices`**, so it doubles as clause 13 evidence.
3. **Copy `~/gate71-press1.log` and `~/gate71-press2.log`** back to
   `pyplan/gates/7.1-ubuntu-2026-09-04-clean/` and record clause by clause.

## The client, already prepared

`C:\wow335ahd` on the laptop, WoW 3.3.5a build 12340, known-good (its own `Logs/connection.log`
records a successful AUTH and character login on 8/31).

`interface/loginui.lua` now lists **"Yulon gate (7.1)" at `100.71.125.58` as the first entry**, so
it can be picked at the login screen. 40 servers listed, braces balance, backup at
`loginui.lua.bak-before-yulon-20260904-1940`.
**`data/enus/realmlist.wtf` was deliberately left alone** — it still says `100.99.161.102`. If the
client turns out to read that rather than the Lua list, change it then, so it stays clear which one
mattered.

**Tailscale is installed and enabled on `yulon-ubuntu`** (1.102.3, node `yulon-ubuntu-1`,
`100.71.125.58`). It was needed because the laptop has NO direct route to that VM — `ssh` reaches it
through a `ProxyCommand` that tunnels via the Hyper-V host, and both `172.30.55.119` and the stale
`100.101.205.6` fail from the laptop.

## The display, which blocked the client login

The client would not start: no display attached to the desktop, D3D9 enumerating zero adapters,
`gx.log` 0 bytes. The owner opened the lid and it **half** changed — the desktop began reporting the
real panel geometry (1707x1067, i.e. 2560x1600 at 150%) instead of the 1920x1080 headless fallback,
but both monitors still read WMI `Availability=8` (off line) and the client still failed with the
same `#32770` dialog. Ruled out: session context (the launcher runs in session 1, the console
session, same as `explorer`). Synthetic input and `WM_SYSCOMMAND`/`SC_MONITORPOWER` did not move it.
**The restart is the most likely fix**, and the freeze that prompted it may be the same fault.

## Not finished, and must not be read as done

`pylauncher/yulon/networking.py` and `tests/test_networking.py` carry the **bug §39 fix, unreviewed**.
Both parse and its lane reported a green suite and a mutation pass; I have not reviewed it or run
the suite against it myself. `pyplan/gates/bug39-ssh-lockout/` holds that lane's own captures.
