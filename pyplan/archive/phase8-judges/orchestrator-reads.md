# A SOAP bind failure is fatal to the worldserver, and how it is fatal differs per tree

Read by the orchestrator on 2026-09-06, in the trees fetched at the catalog's pinned revisions,
after the operator judge raised the AzerothCore half. The three designs each build 8.1 as "write
the key, recreate the container"; none has a row for the bind failing, and the CMaNGOS half of
this was not in any read.

| Tree | What a failed `soap_bind` does | Citation (fetched SHA) | Characters saved? |
|---|---|---|---|
| AzerothCore `413bea61` | `LOG_ERROR` then `World::StopNow(ERROR_EXIT_CODE)` — a **graceful** shutdown; the comment says `exit()` there would destroy `SocketMgr` before `StopNetwork()` and assert | `src/server/apps/worldserver/ACSoap/ACSoap.cpp:41-46` | yes, the normal shutdown path runs |
| mangos-tbc `f82e7d67` | `sLog.outError` then **`exit(-1)`**, called from the SOAP worker thread inside `SOAPThread::Work()` | `src/mangosd/MaNGOSsoap.cpp:43-47` | **no** — the process dies immediately |
| mangos-classic `8ec338a1` | identical `exit(-1)` | `src/mangosd/MaNGOSsoap.cpp:43-47` | **no** |
| tortoise-wow `7c0fb278` | not applicable — no SOAP is compiled in | `src/mangosd/CMakeLists.txt:19-30` | — |

AzerothCore additionally sets `soap.bind_flags = SO_REUSEADDR` with the comment "allow rebinding
while the previous socket is still in TIME_WAIT (e.g. on a quick restart)"
(`ACSoap.cpp:38-39`), which closes the quick-restart route and not the port-taken route. The
CMaNGOS trees set no such flag.

Both compose templates give the world container `restart: unless-stopped`
(`pylauncher/catalog/installers/wow-wotlk/native/base.yml.tmpl:241`;
`shared/cmangos/base.yml.tmpl:97`), so on TBC and Vanilla the failure is a **restart loop**: boot →
bind fails → `exit(-1)` → restart → boot → … Every character logged in at the moment of the first
press loses whatever was unsaved, and keeps losing it on every loop.

**What this requires of 8.1, on any design that wins.**

1. The port is proved free **before** the key is written, not after: a connect probe to the host
   binding and a check that no other container of this project publishes it. A refusal costs the
   user nothing; the press costs them their server.
2. The enable step is ordered so the failure is survivable: write the key, recreate, then **watch**.
   `docker.container_state()` already returns `RestartCount` and `StartedAt` in one inspect
   (`pylauncher/yulon/docker.py:1883`), which is the instrument.
3. 8.1's Definition of done gains a clause the three designs do not have: after the recreate the
   world container is running, `RestartCount` has not grown, and this run's ready marker is in the
   log. A verified round-trip alone does not prove the server survived being changed.
4. On a failure the app rolls the key back and says so. The rollback is the same conf/override
   write in reverse, and it is the only path that returns a user whose server is now looping.
5. The AzerothCore and CMaNGOS halves are recorded separately, because they are different
   failures with different costs, and because the CMaNGOS half is the one that loses data.

---

# Every tree has an environment layer over its config, and all three differ

Written after a first draft of this note asserted that the CMaNGOS lineage has no environment
layer. It does. The machine corrected the artefact, which is the rule this file exists to serve.

| Tree | Env layer? | The env variable's name for `SOAP.Enabled` | Precedence against a key **present** in the file |
|---|---|---|---|
| AzerothCore `413bea61` | yes | `AC_SOAP_ENABLED` — prefix `AC_`, `.`/`-`/space → `_`, lower→UPPER boundaries split, all upper-cased (`src/common/Configuration/Config.cpp:370-394`, `:435-438`) | **env wins** (`:540-552`: the getter consults the env before returning the stored value) |
| mangos-tbc `f82e7d67` | yes | `Mangosd_SOAP_Enabled` — prefix `Mangosd_` from `SetSource(configFile, "Mangosd_")` (`src/mangosd/Main.cpp:156`), `.` → `_`, **case preserved** (`src/shared/Config/Config.cpp:141-153`) | **env wins**, applied at parse time: `Reload()` reads each line and replaces the value when the env var exists (`Config.cpp:75-78`) |
| mangos-classic `8ec338a1` | yes | identical, same prefix and transform | identical (`Config.cpp:75-78`) |
| tortoise-wow `7c0fb278` | **no** | — | — (`src/shared/Config/Config.cpp` contains no `getenv`) |

The CMaNGOS env layer has a second half the AzerothCore one does not need: for a key **absent**
from the file, `GetStringDefault` falls back to the env var and logs `Missing key '%s' in config
file '%s', recovered with environment '%s' value.` (`Config.cpp:99-115`). Realmd uses the prefix
`Realmd_` (`src/realmd/Main.cpp:128`), and the playerbots and AH-bot configs use `Mangosd_` with
their own files (`PlayerbotMgr.cpp:44`, `AuctionHouseBot.cpp:45`).

**What this changes for 8.1.** All three designers assumed the CMaNGOS family could be switched
only by patching `mangosd.conf`, and chose the conf table for that reason. The conf patch does
work and is what the install already owns. But an environment route exists on TBC and Vanilla too,
through the generated compose override the app also owns — and it is the better of the two,
because a conf value can be re-materialised out of the image by `conf.materialise()` while the
compose file is the app's own generated artefact. Whichever is chosen, the name is
`Mangosd_SOAP_Enabled`, **not** `SOAP_ENABLED` and not `AC_`-anything: a per-tree fact that looks
like a sibling of AzerothCore's and is spelled differently in three ways at once.

**What this changes for the bot marker.** All three judges asked that the account prefix be read
from the installed configuration rather than from `catalog.json`, because the same key has two
live spellings on the CMaNGOS lineage (`phase8-delta.md:64`) and because memory
`bot-population-is-500` records a catalog value that was not the running value. Correct — and the
conf file alone is not where the value lives on three of the four trees. The live value of
`AiPlayerbot.RandomBotAccountPrefix` is:

* **AzerothCore:** `AC_AI_PLAYERBOT_RANDOM_BOT_ACCOUNT_PREFIX` if set, else the line in
  `env/dist/etc/modules/playerbots.conf`, else the compiled `"rndbot"`
  (`PB src/PlayerbotAIConfig.cpp:576`). Yu'lon sets no prefix env var today but does set two
  sibling `AC_AI_PLAYERBOT_*` keys in the generated override (`catalog.json:58-60`), so the
  precedence is live in the very install Phase 8 targets.
* **TBC / Vanilla:** `Mangosd_AiPlayerbot_RandomBotAccountPrefix` if set, else the line in
  `etc/aiplayerbot.conf`, else the compiled `"rndbot"` (`PBC PlayerbotAIConfig.cpp:500`) — while
  the shipped dist writes `RNDBOT` (`aiplayerbot.conf.dist.in:57`).
* **Tortoise:** the file, else the compiled `"rndbot"` (`PlayerbotAIConfig.cpp:545`), with the
  dist writing `RNDBOT` (`aiplayerbot.conf.dist.in:63`). No env layer.

So the resolver reads the container's environment (from the `docker inspect` that
`docker.container_state()` already shells) **and** the conf file, applies the tree's own
precedence, and reports which of the three sources answered, so a capture records it. A reader
that opens only the file is a guard that proves a declaration, on the one fact this project
already keeps a memory file about.

Both conf directories are bind-mounted to the host, so the file half needs no container:
`./env/dist/etc:/azerothcore/env/dist/etc` on WotLK
(`catalog/installers/wow-wotlk/native/base.yml.tmpl:269`) and `./etc:{{CORE_DIR}}/etc` on the
CMaNGOS family (`shared/cmangos/base.yml.tmpl:112`).
