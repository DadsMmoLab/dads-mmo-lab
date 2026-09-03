# yulon-arch round 3 — fresh install + full feature sweep

8 vCPU / 23 GB, branch `test/full-vm-run-2026-08-28` @ c5c7d20a. **Everything below ran against a
server this box installed from scratch**, not an adopted one — which is what makes it different from
Fedora's sweep.

Build: 1829/1829 objects at `-j6`, peak 4.3 GB of 23 GB, **zero swap at every sample** across the
whole session including two full worldserver boots.

## 500 bots — verified four ways, then a fifth after a restore+reboot
env via `docker inspect`; exactly 500 "logged in" lines ending `500/500 Bot Lilealaes logged in`;
`SELECT COUNT(*) ... WHERE online=1` → 500; and the live console `server info` →
"Characters in world: 500." Re-confirmed after a full backup/restore/reboot cycle.

## The two things Fedora could not do, done here
- **Backups and a real restore, end to end.** All 4 schemas dumped (~370 MB) live while the server
  ran, `verify_dump()` passed every file. Then: `plan_restore()` correctly **refused** while
  worldserver/authserver were up (safety gate confirmed), they were stopped for real, `acore_auth`
  restored — an automatic unprompted `pre-restore` safety backup was taken first — stack brought back
  up, worldserver returned to `ready...` and 500/500 bots, and the `YULONQA` account created *before*
  the backup survived the round trip.
- **A live module apply/remove round trip.** Fedora judged this too risky against a shared DB.
  Here the self-contained `buff-mobs` manifest was applied live (`DamageModifier` 1→1.5,
  `HealthModifier` doubled, confirmed by direct query), then removed, and the values reverted exactly
  (1.5→1, doubled halved back). Clean both ways.

## Other features
- **Accounts**: `create_account()` made `YULONQA` (id 101, GM 3); the project's own
  `tests/integration/test_accounts_live.py` passed **4/4**, including the byte-identical-verifier test
  — the strongest available proof the SRP6 math matches what the worldserver itself writes. Same
  result as Fedora, now against a DB the app populated from scratch.
- **Maintenance/repair on a fresh import** (new ground): `repair.import_state()` →
  `state='populated', complete=True, detail='101 rows in acore_auth.account, 1000 rows in
  acore_characters.characters'`. Confirms the updates-bookkeeping check is right immediately after a
  from-scratch import, not just on an adopted install.
- **Console**, **live log streaming** (`docker.follow_logs()`): both working against real output.
- **Self-update**: 404 again. Third box, same cause. Not distro-specific.

## Arch-specific, and a correction to round 2
- **`platform.detect_firewall()` returns `"none"` on Arch** — the box ships neither `ufw` nor
  `firewall-cmd`, only bare `nft`/`iptables`. The app **degrades gracefully**, giving a manual
  "allow inbound TCP by hand" instruction rather than failing. Good behaviour, worth knowing: the LAN
  apply is therefore only partly automatic on Arch.
- **The realmlist step is always an UPDATE, never an INSERT** — the row already exists from
  AzerothCore's own base import. Worth correcting any mental model that treats INSERT as the
  fresh-install path.
- **`is_steamos()` correctly returns `False` on plain Arch** — the detection is not fooled by the
  shared base distro.
- **Round 2's "xdotool clicks do not register" is RETRACTED.** Running natively (not in round 2's
  Docker/X11 container workaround), a real `xdotool` click on "Use existing…" fired the action — the
  window title changed to "Select the folder where WoW WotLK is installed". It was an artifact of the
  workaround, not a launcher defect.

## Curiosity, upstream not ours
`ac-client-data-init` opens its log with
`/azerothcore/apps/installer/includes/functions.sh: line 146: /includes/modules-manager/modules.sh:
No such file or directory` before going on to download and unzip the 1.14 GB client data
successfully. Inside the upstream AzerothCore image, so no file:line here. Appeared on every fresh
install; not checked against Fedora.

## Not tested
Internet-mode networking (LAN only) — a time-budget choice; same code path plus a public-IP probe.
