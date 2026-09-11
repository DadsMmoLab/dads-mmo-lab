# Where things stand, 2026-09-06 08:30 CEST — after the night that finished Phase 7's lanes

`yulon-phase7` is at the merge commit **8385f9bf** plus this note, pushed, on PR **#143 → `DadsMmoLab:Yulon`**,
which is out of draft since 04:50 CEST with a description of the whole branch. Every lane worktree and
`lane/*` branch is deleted, locally and on the fork. The tree is clean.

## Phase 7: 11 of 12 ticked

Open: **7.8** (macOS — Baerthe's, on their own Mac) and the **Phase 7 exit-criteria box** (Baerthe's to tick).

What the night added, in merge order, each lane fix → adversarial review → review-of-review and then
closed by hand under the owner's 2026-09-06 rule (two consecutive rounds with LINE findings only, or the
round-10 pattern of a false framing sentence per round):

| Lane | Merge | What ticked or closed | Evidence |
|---|---|---|---|
| b43 | `ed642bbd` | bug-checklist §43 CLOSED — `keep_awake()` refuses the declared GUI thread, not the main thread; the headless TBC press on `yulon-win11-gate` logged the assertion taken | `pyplan/gates/bug43-keepawake-win11-2026-09-05/` |
| 710 | `2b6a9c6b` | **7.10 ticked** — 88 checks on the merged engine against the live 7.2 install; the 09-04 SIGABRT and the ufw enable both gone, each attributed to its commit | `pyplan/gates/7.10-ubuntu-2026-09-05-rerun/` |
| b39 | `f16104e4` | bug-checklist §39 CLOSED — the LAN step pressed through the real Networking tab on `yulon-ubuntu`; a 3.3.5a client on `vmhost` logged in at 2026-09-05 23:31:24 UTC, `last_ip 172.30.48.1`; 7.1 clause 15 MET | `pyplan/gates/bug39-lan-press-2026-09-05/` |
| b71 | `249d0b94` | **7.1 ticked** on the owner's re-scope of its Fedora/Arch sub-gate (Appendix E); the citation pass fixed 13 dead test names on the 7.1 plan page | `pyplan/gates/7.1-tick-2026-09-06/` |
| b41 | `8385f9bf` | bug-checklist §41 CLOSED — third `networking.Mode` (`loopback`), intent in `.yulon-network.json`, `ready` reads it before it decides; second press on the finished WotLK install left the chosen row alone and said why | `pyplan/gates/bug41-loopback-2026-09-05/` |

Gates on the tip: `--checks` ALL GREEN on `yulon-fedora` (Py 3.13) and `m910q` (Py 3.11, 2810 passed /
4 skipped), transcripts in the session scratch; CI green by SHA on every merge (`ed642bbd`, `2b6a9c6b`,
`f16104e4`, `249d0b94`) and on this tip once its run lands.

## Owner decisions — answered 2026-09-06 ~08:45 CEST (Appendix F)

1. **7.8 macOS** — "7.8 will be done by baerthe": stays open, Baerthe's, on their own Mac.
2. **The CMaNGOS doodad issue** — "post it": posted as https://github.com/cmangos/issues/issues/4286.
3. **The Phase 7 exit-criteria box** — "barthe decide to tick it": stays `- [ ]`, Baerthe's to tick.
4. Two facts surfaced by lanes and left for the owner, not filed: a 5.8 GB `mangosd` core dump on
   `yulon-win11-gate` written 54 minutes after the 7.7 TBC press reported `The server is up.` (deleted
   for space, cause not investigated); and the CMaNGOS engine's `patch-sources` stage now refuses a
   second press on any folder built before the doodad patch (m910q Vanilla, win11 TBC), so those installs
   cannot reach `ready` again without a recompile.

## Boxes

`yulon-ubuntu` Off at baseline (the 7.2 install intact: three `ac-*` up before shutdown, ufw inactive, realm
`172.30.55.119`, 102 accounts); `yulon-win11-gate` Off at baseline; `yulon-fedora` Off at baseline after the
final gate; `m910q` untouched apart from the runner's scratch checkouts. The Hyper-V host now holds a
minimal WotLK 3.3.5a client at `C:\clients\WoW-WotLK-3.3.5a-min` (with the speech MPQs), which is what
made the login from another machine possible. m910q still carries stale `http.server` processes from
earlier sessions (read 2026-09-06 08:30 CEST: three on port 22, five on 8099, one on 8765 — all bound to
127.0.0.1 or serving `/tmp`) and the §39 stand-in containers `r6` (Up), `r5`, `r5refute`, `refute5c`,
`fw5` (Exited) beside the exited `tbc-*` set — nobody removed them.

## What stays OPEN in the bug checklist

§28 (gaming-mode script from the artifact), §31 (`wsl.find_servers()` wording), §36 (a shipped SQL-plan fix
and an install with a marker). None was in tonight's scope.
