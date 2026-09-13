# Tortoise: preparing for the move from Shyalya's fork to Penqle's tree

Read on 2026-09-08 from https://github.com/Shyalya/tortoise-wow (README at `9c1e826`, "announce
project wind-down and archival"): **the fork we install is being retired.** Through 30 September
2026 it is kept in sync only with upstream [Penqle/tortoise-wow](https://github.com/Penqle/tortoise-wow)
— no further work of their own — and after that it is archived read-only. The owner will say when
"they have merged"; this note is what Yu'lon does on that day, and what is already true.

## What Yu'lon installs today

`catalog.json` → `wow-tortoise` → `emulator.sources[0]`: `https://github.com/Shyalya/tortoise-wow`,
branch `playerbots-integration-gh`. One source; playerbots live IN that tree
(`modules/mod-playerbots`, built with `-DMODULE_MOD_PLAYERBOTS=static -DBUILD_PLAYERBOTS=ON`),
and so does the SOAP interface (`src/mangosd/MaNGOSsoap.cpp`, `src/mangosd/soap/`, commit
`3f9a062`) and the account-lockout fix (`3a8472e`). The image template also copies `sql/` and
`tools/mmap` out of the clone and, since `3a1ed6ee`, rewrites their one self-colliding migration
to `INSERT IGNORE` (see the rehearsal folder).

## What Penqle's tree has, and has not (2026-09-08, `main` at `c478cda`)

| | Shyalya `playerbots-integration-gh` | Penqle `main` |
|---|---|---|
| module system (`modules/`, `MODULE_<NAME>` cache options) | yes | yes — same layout, `templates/`, `create_module.sh` |
| `modules/mod-playerbots` | in-tree | **absent** (only `.gitkeep`, `templates`) |
| `modules/mod-dungeon-clear` | in-tree (disabled in our build) | absent |
| SOAP (`MaNGOSsoap.cpp`, `soap/`) | yes (re-added 2026-09-07) | **absent** from `src/mangosd/` |
| the lockout fix (`AccountMgr::GetName` empty cached username) | yes (`3a8472e`) | not seen on `main`; check `git log -S "Username.empty()" -- src/game/Accounts` on the day |
| `sql/base` + `sql/database_updates` + hash-keyed `AutoUpdater` | yes | same shape (`2cd5bba` "stale spell_proc_event and spell_chain cleanup to migration") |
| open PRs of note | #29 runtime architecture redesign (T-imothy, +11k, not mergeable), #28 flight cache | #474 module chat/headless packet paths, #473 PCH header deps, #454 Windows/MSVC build, #456 Windows testlab |

So "merged" can mean three different days, and each moves a different line of the catalog:

1. **Playerbots reach Penqle** (in-tree, or as a module repository). Then `sources` becomes
   Penqle `main` plus, if the module lives outside, a second source cloned into `modules/` —
   the AzerothCore entry already does exactly this (`clone-core` + `clone-modules`), so the
   engine needs nothing new; the Tortoise Dockerfile's CMake flags stay if the module keeps its
   name (`MODULE_MOD_PLAYERBOTS`), and the `playerbots characters` / `playerbots world` SQL
   phases keep their paths if the module keeps `sql/characters` and `sql/world`.
2. **SOAP reaches Penqle.** Then the `operations` block moves from `attach` to a real channel
   (the same block WotLK/TBC/Vanilla use), `mangosd.conf` gains `SOAP.Enabled = 1`,
   `SOAP.IP`, `SOAP.Port`, and the compose template publishes the port on loopback. Until then
   the console is the only route on this tree (8.2e's finding).
3. **The lockout fix reaches Penqle.** Then the 8.3d warning on `account set password` goes,
   and the memory note `tortoise-password-change-locks-accounts-out` closes.

## Checks to run on the day, before the catalog moves (each measured, none inherited)

- `git ls-tree` of Penqle's `modules/` and `src/mangosd/` for the three items above.
- Their `sql/base/tw_world_migrations.sql` still ships empty? and does
  `sql/database_updates/world/20260903063722_world.sql` still exist unchanged (SHA1
  `34F86966897E9206E13773D73C2232677DA2FFED`)? If it is fixed or gone, drop the `sed` line in
  `Dockerfile.tmpl` and its test.
- `dbc_verifier.py` (`tools/dbc_verification/`, manifest from the launcher API 2026-07-14): the
  client must be `1.18.1.7272` with the 2026-04-12 hotfixes — the host's
  `C:\clients\TurtleWoW-1.18.1-7272-Hotfix-2026-04-12.zip` is that. Worth running once against
  an extracted `dbc/` folder and, if it is cheap, from the extract stage.
- A fresh install on a VM from the new source, through the app's own engine, with SOAP asked
  and `account set password` pressed on an account that has logged in (the 8.3d shape).
- The existing installs (m910q's `tw_world` at their old base) get the reimport treatment
  again, rehearsed on a copy first, because base data changes between snapshots.

## What does NOT change

The image base (`ubuntu:24.04`, for gsoap's glibc), the extract tools and their argv, the conf
keys, the two-database character/logon split — all measured on this fork and all upstream
MaNGOS-shaped. And the rule that every per-tree fact is re-measured on the new tree, not carried.
