# 7.1 clause 14 (client login) and clause 1 (clean checkpoint) — Lane C, 2026-09-04

Two of the 7.1 clauses the 09-04 run left open. One is still open and says why; the other
is **closed**, and unblocks 7.2 as well.

---

## Clause 14 — the client login: **STILL NOT MET.** A client exists; it cannot be run.

**Verdict in one line:** a real 3.3.5a client (Wow.exe 3.3.5.12340) is on this laptop at
`C:\wow335ahd`, but the laptop currently has **no display attached to the desktop**, so
the client's D3D9 adapter enumeration returns nothing and it exits at startup with a
dialog. Everything else was staged and proven; only a human opening the lid or plugging a
monitor in is missing. Full account, with the readings, in
**`client-and-display-facts.txt`**.

The short version:

* **Where the client is.** `C:\wow335ahd` on the laptop — 27,186 MB of data, HD patches,
  DXVK 2.4.1. Its own `Logs\connection.log` records a completed `COP_AUTHENTICATE`,
  `COP_CREATE_CHARACTER` and `COP_LOGIN_CHARACTER` on 8/31, so it is known-good on this
  hardware. The other two client stores hold no 3.3.5a: `vmhost:C:\clients` has TurtleWoW,
  TBC 2.4.3 and Vanilla 1.12.1; `m910q:~/clients` has 1.12.1 and 2.4.3.
* **Why it will not start.** `EnumDisplayDevices` reports `attachedToDesktop=False` on all
  six entries (two AMD 610M, four RTX 4070). Every screen capture is uniformly black with
  the game minimised — the desktop, not the window. The session is the console session and
  is not locked. Two DXVK configurations and two `SetDisplayConfig` topology forces later,
  nothing changed: there is no panel to attach.
* **Why it was not moved to a machine with a screen.** `vmhost` has a real display, 139 GB
  free and a proven route to `172.30.55.119` — but the laptop→vmhost link measured
  **0.84 MB/s** (502,394,880 bytes of a 611 MB file in 600 s), which puts a minimum
  bootable 15.4 GB client set at ~5.1 hours. `m910q` is the fast link but has **no Wine**.
* **What was staged and proven anyway**, so the run is five minutes' work once a screen
  exists: the credentials (`GATE0904` / `gate0904pw`, found in
  `/home/pk/gate0904/gate-account-lan.py`, **not** in `account-lan.log` as the brief
  assumed), a working `ssh -L 3724/8085` tunnel, and the realm hand-off rule read out of
  this box's own `Realm.cpp:28-41` that makes a tunnelled client work.
* **Nothing is offered as a substitute.** `server-side-after-attempt.txt` records that
  account 101 has `session_key` NULL and `last_login` NULL, that **zero** of the 101
  accounts on this realm has ever completed an SRP6 exchange, that account 101 owns no
  characters, and that the authserver log ends at `Added realm "AzerothCore" at
  172.30.55.119:8085` with no connection after it. The 500 rows at `online=1` are the
  playerbot roster — server-side bot sessions, not client logins.

**Everything this lane changed to try it has been put back**: the realm's `localAddress` is
`172.30.55.119` again (`address` was never touched), `WTF\Config.wtf` and
`data\enus\realmlist.wtf` are back to their originals (backups kept as `*.lanec-bak`), and
DXVK's `d3d9.dll` is in place. The 502 MB partial on `vmhost` was deleted — its
`sftp-server` had to be killed first to release the file.

### Files

| File | What it is |
|---|---|
| `client-and-display-facts.txt` | the whole finding: where the client is, why it will not start, what was measured, how to finish |
| `server-side-after-attempt.txt` | the three server-side witnesses, all negative, taken after the attempt |
| `wowdrive.ps1` | the driver written to launch and key the client (unused past launch) |
| `01-login-screen.png`, `01b-login-screen.png` | the two capture attempts — uniformly black |
| `control-desktop-wow-minimized.png`, `control2-after-wake.png`, `control3-desktop-after-monitorpower.png`, `control4-unsandboxed.png` | the four controls that prove the DESKTOP is black, not the game window: with the game minimised, after injected keyboard/mouse activity, after `SC_MONITORPOWER` on, and outside the tool sandbox. All five PNGs are 10,143 bytes — the same solid-black frame every time |

---

## Clause 1 — a clean checkpoint: **CLOSED. `clean-ssh` is one, and the restore was not refused.**

The brief expected the harness to block the restore. It did not. The command ran as given:

```
ssh vmhost "Restore-VMSnapshot -VMName yulon-ubuntu -Name clean-ssh -Confirm:$false; Start-VM yulon-ubuntu"
```

It returned no output, the VM came back with `Uptime 00:00:08`, and ssh answered within
about 10 s. All three checkpoints survive — `clean-ssh` (2026-08-28) is the root, with
`post-hunt-cleaned-2026-08-31` and `pre-7.2-gate-2026-09-02` as its children — so nothing
was lost but the 09-04 install, which this job existed to discard.

**`clean-ssh` is genuinely clean**, and by every question `state-as-restored.txt` asked of
`pre-7.2-gate-2026-09-02` and got the wrong answer to. Read
`checkpoint-clean-ssh-as-restored.txt`; the summary:

| Question | `pre-7.2-gate-2026-09-02` (why 7.1.1 and 7.2 were blocked) | `clean-ssh` |
|---|---|---|
| an install at `~/wowserver`? | a COMPLETE 08-31 install, state file `completed: [clone-core … import]` | **no such directory**, and no `~/gate*` or `~/*server*` either |
| a running stack? | `ac-worldserver` at 5.6 GB RSS, `ac-authserver`, `ac-database` | **docker is not even installed** — `docker: command not found` |
| ports held? | 3724 / 8085 / 3306 | only 22, and loopback 631/53 |
| free space? | 43 GB, and press 1 refused on the 48 GB floor | **78 GB** |
| ufw? | active, and the lockout's hand-added port-22 rule still in it | **inactive** |

It is better than merely empty. Because docker is absent **and there is no `docker` group
at all** (`getent group docker` → nothing; `pk` is in `sudo`, not `docker`), a press from
here has to provision Docker — which means clauses **3 (consent dialog)**, **4 (re-login
report)** and **5 (re-login)**, the three the 09-04 run recorded as *"not exercised
(expected)"* because that checkpoint already had Docker, are all reachable for the first
time. Two things to know before pressing: `ccache` is not installed either (so clause 8's
resume evidence starts from a genuinely cold cache), and `sudo` is **passwordless**, so the
sudo-password question the press driver handles will not fire.

Also on the box as restored: Ubuntu 24.04.4, kernel 7.0.0-30, 15 CPUs, 19 GB RAM + 7 GB
swap, git / python3 3.12.3 / curl / ufw / systemd-inhibit present. **`claude-say` is NOT
installed and `~/claude-activity.log` does not exist** on this checkpoint — the announce
helper has to be put back before the next lane starts.

No new checkpoint was taken: `clean-ssh` already is the clean one, and a second copy of the
same state would only add a name. The VM was **left running** in the restored clean state,
so the next lane can press Install straight away.

### Files

| File | What it is |
|---|---|
| `checkpoint-clean-ssh-as-restored.txt` | the whole probe of the restored box, taken before anything was touched |

---

## One caution about provenance

`pyplan/gates/7.10-gaps/widget-run.log` was pulled off `yulon-ubuntu`'s journal **before**
this restore. That journal no longer exists — the VM has been reverted past it — so the
copy on this laptop is the only record of that run, and it cannot be re-checked against the
box the way the 7.1 and 7.10 sets had their line counts compared on both machines.
