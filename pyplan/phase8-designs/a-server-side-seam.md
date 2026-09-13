# Phase 8 design — the server-side seam

> One of three independent designs for `pyplan/phase8-parity-decisions.md`, written 2026-09-06
> from the server-side angle: one typed command/query layer both emulator families share, with
> every family difference as data on the catalog entry. Inputs: `pyplan/archive/phase8-kickoff.md`,
> `phase8-parity-decisions.md` (the eight answers and the nine-step cut — not re-litigated),
> `phase8-delta.md` (facts 1–9), the five reads under `phase8-reads/`, and the tree
> `C:\Users\perzi\dml-phase8` at `82241478`, whose `pylauncher/` is byte-identical to `7bc5ebd3`
> (`git diff --stat 7bc5ebd3 HEAD -- pylauncher` is empty), so every `path:line` below is a line
> at the pinned SHA. Paths are relative to `pylauncher/`; `yulon/` is dropped where obvious.
> `AC`/`PB`/`ALE`/`TBC`/`VAN`/`PBC`/`TW`/`RUST` are the reads' prefixes. Nothing here was run.

---

## 1. The decision

**Every Phase 8 feature asks a server exactly one of two things — a question of its database
(a read) or a command of its world thread — through one typed seam: `commands.py` builds a
`Command` from a per-tree template, `channel.py` delivers it over whichever channel the entry
declares (SOAP on loopback for WotLK/TBC/Vanilla, the attach console everywhere a pty exists,
Tortoise's `pending_commands` queue where nothing else can), `soap.py` is the wire, and
`dbreads.py` is the read side over the `DockerSql` seam that already exists. Every answer has
three outcomes (`yes`, `no`, `could_not_ask`) and every feature draws its buttons from
`Capability`, which asks the same three-valued question before the first press. The prompt, the
security level, the command text and arity, the fault shape, the items-per-mail cap, the table
and column names, the bot marker, and which channel exists at all are fields on a new
`CatalogEntry.ops` block — typed, `_Strict`, four JSON values, no `if game ==` anywhere.** 8.1
turns SOAP on per tree through the mechanisms the install already owns (`world_env` for
AzerothCore, the conf table for CMaNGOS), makes the app's GM-3 account through
`accounts.create_account` (the SRP6 row Yu'lon already writes), and persists its credentials
under `config_dir()` only after a real round-trip has answered. Writes obey Q7 by construction:
the seam's SQL writer can be pointed at `auth` only, and every `characters`/`world` mutation is
a server command the server performs on its own thread.

---

## 2. Architecture and module layout

Extends the style-guide §3 table; nothing in it is replaced. No UI in any seam module; no docker
argv in `ui/`.

| Module | Owns | Must never |
|---|---|---|
| `catalog/catalog.py` (**changed**) | The `Ops` models below (`Channels`, `Soap`, `SoapEnable`, `PendingCommands`, `GmLevel`, `Commands`/`CommandSpec`, `BotMarker`, `Tables`, `LuaBridge`) on `CatalogEntry.ops`, and a model validator (`bridge` requires `soap`; `queue` requires `channels.attach`; a `Commands` entry may be `null` only where the read says the row is absent) | Hold a value (they are `catalog.json`); know how a command is sent |
| `catalog/composegen.py` (**changed**) | One new token `SOAP_PUBLISH`, rendered from `entry.ops.channels.soap` (the loopback publish line, or a comment line when the core has no listener); one new pure function `render_override(entry, server_dir, ...)` that re-renders only the override file from the same template and data `render()` uses | Run a subprocess; add a port anywhere but the base file |
| `soap.py` (**new**) | The wire and nothing else: `envelope(namespace, command)`, `parse(body) -> WireResult` (result text, or fault text, or HTTP status), `SoapClient(url, user, password, connect_s, read_s).execute(text) -> WireResult` over stdlib `http.client` with Basic auth; entity decoding of the reply | Build command text; hold a lock; know a game; log the password |
| `channel.py` (**new**) | `Answer`, `Channel` (Protocol: `ask(Command) -> Answer`, `probe() -> Answer`, `kind`), the three implementations — `SoapChannel` (wraps `SoapClient` + one per-install `threading.Lock`), `AttachChannel` (wraps `controller_wow_wotlk.console.send_command` with the entry's prompt facts), `QueueChannel` (one `INSERT` into `pending_commands` through `SqlSeam.run_statement("auth", …)`, then the caller's verify read) — and `channel_for(entry, install, credentials) -> Channel` ranking them from data; maps transport failures to `could_not_ask` reasons using `docker.container_state()` | Contain command text; contain SQL other than the queue `INSERT`/`DELETE`; retry a write on timeout; contain UI |
| `commands.py` (**new**) | `Command` (id, text with the password masked for logs, `kind: read|write`, `verify: Read | None`), one builder per `CommandSpec` that fills the entry's template, the quoting rule (strip `"`, CR/LF → space), argument validation (name charset, level ≤ `level_max`, ≤ `mail_items_max` items per command, copper cap), and `capability(entry, command_id, probe: Answer | None) -> Capability` | Send anything; know a container name |
| `dbreads.py` (**new**) | The typed reads over `SqlSeam.query`: `bot_clause(entry)`, `online_counts`, `search_items`, `teleport_targets`, `characters_named`, `list_accounts`, `list_bots`, `mail_count`, `group_members`, `character_position`; TSV → frozen dataclasses; every schema and column name from `entry.databases`/`entry.ops.tables` | Call `run_statement` (a unit test asserts the seam's writer is never reached from this module); splice user text unescaped |
| `channel_setup.py` (**new**, 8.1) | `plan(entry, install)` (what would change, read-only), `enable(entry, install)` (the per-tree switch: `render_override` + recreate, or `conf.apply_table` + `SOAP_PUBLISH` re-render + recreate), `account(entry, install, sql, channel)` (the create → verify → persist state machine), `Credentials` file I/O under `config_dir()/channels/`, `reset_password` for the app-owned name only | Contain SRP6 (that is `accounts.py`); contain the wire (`soap.py`); persist before a round-trip has answered |
| `controller_wow_wotlk/accounts.py` (**changed**, additive) | Gains `reset_password(sql, username, password, scheme)` — rewrites salt/verifier (or `v`/`s`, or `sha_pass_hash`) for ONE existing account; `create_account` keeps its never-rewrite rule | Be called for a name the caller has not proved is the app's own or the user's explicit choice (8.3) |
| `logsnap.py` (**new**, 8.2) | `snapshot(install, reason, sink_dir, keep)`: resolve this install's world container through its compose project (`docker.project_containers`), `docker.log_tail()`, write `world-<ts>-<game>-<reason>.log`, prune | Stop anything; read another project's container |
| `docker.py` (**changed**, additive) | `log_tail(container, lines, *, wsl_distro) -> str` beside `_logs()` | — |
| `controller.py` (**changed**, additive) | `Controller.__init__` gains `log_snapshot: Callable[[str], None] | None = None`; `stop()` and `remove()` call it (reason `"stop"`/`"remove"`) before `docker.stop_staged`/`remove_staged`; a snapshot failure is logged, never blocks the stop | Anything else |
| `dashboard.py` (**new**, 8.2) | Pure `verdict(states, counts, probe) -> Verdict` (`online | starting | channel_down | stopped | crash_loop`) plus `gather(install, sql, channel | None)`; the per-tick path is `docker.container_state` + `dbreads.online_counts` only | Fire a world-thread command on the timer |
| `gm.py` (**new**, 8.4) | `teleport`, `mail_items` (chunked by `mail_items_max`), `send_money`, `revive`, `set_level`, `rename`, `gear_set_read`/`gear_set_mail`: each `capability → Command → channel.ask → verify read` | Build SQL; touch a client folder |
| `party.py` (**new**, 8.6) | `bridge_status`, `bridge_deploy` (copy `pylauncher/lua/wow-wotlk/*.lua` under the server dir, patch `mod_ale.conf` through the applier's conf primitive, `reload ale`, `yulon_ping`), `add_bot`, `kick`, `dismiss_all`, `botcmd`, presets under `config_dir()/party/` | Write into a client folder; spell a `.playerbots` command from the console |
| `purge.py` (**new**, 8.9) | As `pyplan/phase8-decisions.md` — not redesigned here | — |
| `steam.py` (**new**, 8.8) | `shortcuts.vdf` read/write, artwork, compat-tool mapping — Linux only | Touch a server; run on another platform |
| `ui/controller_view.py` (**changed**, additive) | `ControllerServices` gains `channel`, `channel_setup`, `dashboard`, `gm`, `bots`, `party`, `purge`; the Server tab gains a "Command channel" group and the verdict line; new tabs **Play** (8.4) and **Bots** (8.5/8.6); the Accounts tab gains list/password/level (8.3); the Console tab's Send uses `channel.ask(Command.raw(text))` when the SOAP channel is verified and attach otherwise | Build argv; decide a mechanism |

Every seam call reaches a server through one of three existing transports, and the design adds
no fourth:

| Transport | Existing function | Reached from the launcher today by |
|---|---|---|
| `docker exec … mysql` (reads, auth writes) | `apply.DockerSql.query()` / `run_statement()` → `_mysql()` → `subprocess.run([*docker_prefix, "exec", "-i", "-e", "MYSQL_PWD", <db>, <client>, "-uroot", …])` (`apply.py:504-528`, `:530-585`, `:595-622`) | `ui/controller_view.py:222 _sql_for()` builds one `DockerSql` per install; `_for_wotlk` hands it to `create_account` and `network_apply` (`:334-425`) |
| `docker attach` (one command, a window) | `controller_wow_wotlk.console.send_command()` (`console.py:228-336`), parsed by `_parse_reply` (`:468-527`) | `ControllerServices.send_console` ← `_for_wotlk` (`controller_view.py:388-394`) ← `_send()` (`:1323-1351`); TBC/Vanilla/Tortoise wrap the same function (`controller_wow_tbc/console.py:63-87`, `controller_wow_vanilla/console.py:76-106`, `controller_wow_tortoise/console.py:56-82`) |
| loopback HTTP (SOAP) | **new** `soap.SoapClient` (stdlib) | **new** `channel.SoapChannel`, built by `channel_for()` from `ControllerServices.channel` |

---

## 3. The per-family data

New models on `catalog/catalog.py`, all `_Strict` (extra keys forbidden, frozen), added after
`Console` (`catalog.py:966`). Field names are final; the JSON uses them.

```python
class SoapEnable(_Strict):
    via: Literal["world_env", "conf"]
    env: dict[str, str] = {}          # world_env: keys merged into the override environment
    file: str | None = None           # conf: file under <server_dir>/etc
    keys: dict[str, str] = {}         # conf: Key = value lines
    arrives_by: Literal["recreate"]   # both: the world container is recreated (`compose up -d`)

class Soap(_Strict):
    namespace: str                    # the executeCommand namespace in the envelope
    container_port: int = 7878        # host side is 127.0.0.1:<install.native.soap_port>
    min_gm_level: int                 # what the login needs; the command then runs as console
    enable: SoapEnable
    shutting_down_fault: str | None   # fault text mapped to could_not_ask(shutting_down)
    probe_command: str = "server info"
    probe_expect: str | None          # regex a verified reply must match; None = any non-fault reply

class PendingCommands(_Strict):       # Tortoise only
    db: Db = "auth"; table: str = "pending_commands"
    command_column: str = "command"; realm_column: str = "realm_id"; run_at_column: str = "run_at_time"
    realm_id: int = 1; poll_s: int = 60; max_chars: int = 250; returns_output: Literal[False] = False

class Channels(_Strict):
    soap: Soap | None
    attach: bool = True               # a stdin console exists on every tree
    queue: PendingCommands | None = None

class GmLevel(_Strict):
    store: Literal["account_access", "column"]; column: str | None
    max: int; cached_in_memory: bool; set_via: Literal["command", "sql"]

class CommandSpec(_Strict):
    text: str                         # template; {player} {tele} {subject} {body} {items} {copper} {level} {account} {password}
    level: int                        # shipped source/seed default — a DB question at runtime (delta fact 8)
    offline: bool                     # a named offline target is accepted
    arity_verified: bool = True       # False: the argument shape is read from the row, not the handler; the gate records it

class Commands(_Strict):
    server_info: CommandSpec; tele_name: CommandSpec; send_items: CommandSpec; send_money: CommandSpec
    revive: CommandSpec; character_level: CommandSpec | None; character_rename: CommandSpec
    account_set_password: CommandSpec; account_set_gmlevel: CommandSpec; reload_config: CommandSpec | None
    mail_items_max: int; level_max: int

class BotRegistry(_Strict):
    db: Db = "playerbots"; table: str; column: str; types: tuple[int, ...]

class BotMarker(_Strict):
    account_prefix: str = Field(min_length=1)   # compared as UPPER(username) LIKE 'PREFIX%'
    registry: BotRegistry | None
    prefix_conf: str | None                     # where the live prefix would be read from

class Columns(_Strict):  # one per table; names below are the JSON keys
    ...
class Tables(_Strict):
    item_template: ItemColumns; game_tele: TeleColumns; characters: CharacterColumns
    mail: MailColumns; group_member: GroupColumns | None

class LuaBridge(_Strict):             # WotLK only
    engine: Literal["mod-ale"]; manifest: str = "mod-ale"
    scripts_dir: str = "env/dist/etc/modules/lua_scripts"
    conf_file: str = "env/dist/etc/modules/mod_ale.conf"
    enabled_key: str = "ALE.Enabled"; script_path_key: str = "ALE.ScriptPath"
    reload_command: str = "reload ale"
    ping: CommandSpec                 # "yulon_ping" → expects "yulon-bridge"
    commands: dict[str, str]          # addclass, whisper, uninvite, login
    spec_conf: str = "env/dist/etc/modules/playerbots.conf"
    spec_key_prefix: str = "AiPlayerbot.PremadeSpecName"

class Ops(_Strict):
    channels: Channels; gm_level: GmLevel; commands: Commands; bots: BotMarker; tables: Tables
    bridge: LuaBridge | None = None
```

The model validator: `bridge` only with `channels.soap`; `queue` only with `attach`;
`account_prefix` non-empty (the `LIKE '%'` hazard, `RUST botid.rs:33-38`); `mail_items_max ≥ 1`.

### `wow-wotlk`

```json
"ops": {
  "channels": {
    "soap": {
      "namespace": "urn:AC",
      "container_port": 7878,
      "min_gm_level": 3,
      "enable": {"via": "world_env",
                 "env": {"AC_SOAP_ENABLED": "1", "AC_SOAP_IP": "0.0.0.0", "AC_SOAP_PORT": "7878"},
                 "arrives_by": "recreate"},
      "shutting_down_fault": "Server is shutting down",
      "probe_command": "server info",
      "probe_expect": "Connected players:"
    },
    "attach": true,
    "queue": null
  },
  "gm_level": {"store": "account_access", "column": null, "max": 3, "cached_in_memory": false, "set_via": "command"},
  "commands": {
    "server_info":          {"text": "server info", "level": 0, "offline": true},
    "tele_name":            {"text": "teleport name {player} {tele}", "level": 2, "offline": true},
    "send_items":           {"text": "send items {player} \"{subject}\" \"{body}\" {items}", "level": 2, "offline": true},
    "send_money":           {"text": "send money {player} \"{subject}\" \"{body}\" {copper}", "level": 2, "offline": true},
    "revive":               {"text": "revive {player}", "level": 2, "offline": true},
    "character_level":      {"text": "character level {player} {level}", "level": 2, "offline": true},
    "character_rename":     {"text": "character rename {player}", "level": 2, "offline": true},
    "account_set_password": {"text": "account set password {account} {password} {password}", "level": 3, "offline": true},
    "account_set_gmlevel":  {"text": "account set gmlevel {account} {level} -1", "level": 3, "offline": true},
    "reload_config":        {"text": "reload config", "level": 3, "offline": true},
    "mail_items_max": 12,
    "level_max": 80
  },
  "bots": {"account_prefix": "rndbot",
           "registry": {"db": "playerbots", "table": "playerbots_account_type", "column": "account_type", "types": [1, 2, 3]},
           "prefix_conf": "env/dist/etc/modules/playerbots.conf:AiPlayerbot.RandomBotAccountPrefix"},
  "tables": {
    "item_template": {"entry": "entry", "name": "name", "quality": "Quality", "item_level": "ItemLevel",
                      "required_level": "RequiredLevel", "class": "class", "subclass": "subclass",
                      "inventory_type": "InventoryType", "display_id": "displayid"},
    "game_tele": {"id": "id", "name": "name", "map": "map", "x": "position_x", "y": "position_y", "z": "position_z"},
    "characters": {"guid": "guid", "account": "account", "name": "name", "level": "level", "class": "class",
                   "race": "race", "gender": "gender", "online": "online", "zone": "zone", "map": "map",
                   "x": "position_x", "y": "position_y", "z": "position_z"},
    "mail": {"table": "mail", "receiver": "receiver"},
    "group_member": {"table": "group_member", "group": "guid", "member": "memberGuid"}
  },
  "bridge": {
    "engine": "mod-ale", "manifest": "mod-ale",
    "scripts_dir": "env/dist/etc/modules/lua_scripts",
    "conf_file": "env/dist/etc/modules/mod_ale.conf",
    "enabled_key": "ALE.Enabled", "script_path_key": "ALE.ScriptPath",
    "reload_command": "reload ale",
    "ping": {"text": "yulon_ping", "level": 0, "offline": true},
    "commands": {"addclass": "dml_addclass {player} {class}", "whisper": "dml_whisper {player} {bot} {text}",
                 "uninvite": "dml_uninvite {bot}", "login": "dml_login {player} {bot}"},
    "spec_conf": "env/dist/etc/modules/playerbots.conf",
    "spec_key_prefix": "AiPlayerbot.PremadeSpecName"
  }
}
```

Citations, `wow-wotlk`: namespace `urn:AC` — `RUST soap.rs:127-132` (`phase8-reads/hypeer.md:46-49`),
the one SOAP transport that ran against a live AzerothCore. `AC_SOAP_ENABLED`/`AC_SOAP_IP`/
`AC_SOAP_PORT` — `AC Config.cpp:435-438`, `:510-527`, `:540-552` (`azerothcore.md:120-124`): every
ini key is overridable from `AC_<UPPER_SNAKE>`; `worldserver.conf.dist:453`, `:460`, `:467` are the
dist defaults (`SOAP.Enabled = 0`, `SOAP.IP = "127.0.0.1"`, `SOAP.Port = 7878`). `0.0.0.0` inside the
container is not a relaxation: a published port delivers to the container's bridge address, so a
listener bound to the container's own `127.0.0.1` never accepts it; the loopback pin is the HOST
binding `127.0.0.1:7878` that `base.yml.tmpl:264` already writes and that `docker.published_bindings()`
(`docker.py:2302`) can read back. Min level 3 and `account_access` — `ACSoap.cpp:103-107`,
`AccountMgr.cpp:257-264`, `LoginDatabase.cpp:101` (`azerothcore.md:127-128`); the command then runs
with no level check — `Chat.cpp:52-56`, `ChatCommand.cpp:513-517` (`:129`). Shutdown fault text —
`ACSoap.cpp:125-130` (`:131`). `server info` prints `Connected players:` — `cs_server.cpp:275-294`
(`:225-227`). Command rows: `cs_tele.cpp:46`, `:146-156` (tele name, offline write);
`cs_send.cpp:40`, `:43`, `:110-114`, `Mail.h:33` (send items/money, 12); `cs_misc.cpp:115`,
`:1217-1221` (revive offline); `cs_character.cpp:74-75`, `:447` (level/rename);
`cs_account.cpp:58-59`, `:939-944`, `:1044-1055` (three-arg password, three-arg gmlevel);
`cs_reload.cpp:91` (`azerothcore.md:187-220`). Console strips a leading `.` — `Chat.cpp:981-991`
(`:161-175`), so templates carry none. Bot marker — `PB playerbots_account_type.sql:1-7`,
`playerbots.conf.dist:106` (`:316-327`, `:300`), the two-signal clause and its incident —
`RUST botid.rs:4-17`, `:96-99`, `:112-113`, `:133-134` (`hypeer.md:492-528`). Tables —
`db_world/item_template.sql:23-40`, `game_tele.sql:23-32`, `characters.sql:24-49`
(`azerothcore.md:387-430`); `group_member` — `RUST party.rs:267-268`. Bridge — `mod-ale.json:9-11`,
`:17-19`, `:29-35`; `ALE conf/mod_ale.conf.dist:63-71`, `ALEConfig.cpp:20`, `PlayerHooks.cpp:44-54`,
`ALE_SC.cpp:370-382` (`azerothcore.md:531-541`); the four command texts — `RUST party.rs:175-202`
(`hypeer.md:541-552`) and the scripts `rust-main:cli/lua/party/{dml_addclass,dml_whisper,
dml_uninvite,dml_login}.lua`. Spec keys — `playerbots.conf.dist:1764-1768` (`azerothcore.md:308-311`).

### `wow-tbc`

```json
"ops": {
  "channels": {
    "soap": {
      "namespace": "urn:MaNGOS",
      "container_port": 7878,
      "min_gm_level": 3,
      "enable": {"via": "conf", "file": "mangosd.conf",
                 "keys": {"SOAP.Enabled": "1", "SOAP.IP": "0.0.0.0", "SOAP.Port": "7878"},
                 "arrives_by": "recreate"},
      "shutting_down_fault": null,
      "probe_command": "server info",
      "probe_expect": null
    },
    "attach": true,
    "queue": null
  },
  "gm_level": {"store": "column", "column": "gmlevel", "max": 3, "cached_in_memory": false, "set_via": "command"},
  "commands": {
    "server_info":          {"text": "server info", "level": 0, "offline": true},
    "tele_name":            {"text": "tele name {player} {tele}", "level": 1, "offline": true},
    "send_items":           {"text": "send items {player} \"{subject}\" \"{body}\" {items}", "level": 3, "offline": true},
    "send_money":           {"text": "send money {player} \"{subject}\" \"{body}\" {copper}", "level": 3, "offline": true},
    "revive":               {"text": "revive {player}", "level": 3, "offline": true},
    "character_level":      {"text": "character level {player} {level}", "level": 3, "offline": true},
    "character_rename":     {"text": "character rename {player}", "level": 2, "offline": true},
    "account_set_password": {"text": "account set password {account} {password} {password}", "level": 4, "offline": true, "arity_verified": false},
    "account_set_gmlevel":  {"text": "account set gmlevel {account} {level}", "level": 4, "offline": true},
    "reload_config":        null,
    "mail_items_max": 12,
    "level_max": 70
  },
  "bots": {"account_prefix": "RNDBOT", "registry": null,
           "prefix_conf": "etc/aiplayerbot.conf:AiPlayerbot.RandomBotAccountPrefix"},
  "tables": {
    "item_template": {"entry": "entry", "name": "name", "quality": "Quality", "item_level": "ItemLevel",
                      "required_level": "RequiredLevel", "class": "class", "subclass": "subclass",
                      "inventory_type": "InventoryType", "display_id": "displayid"},
    "game_tele": {"id": "id", "name": "name", "map": "map", "x": "position_x", "y": "position_y", "z": "position_z"},
    "characters": {"guid": "guid", "account": "account", "name": "name", "level": "level", "class": "class",
                   "race": "race", "gender": "gender", "online": "online", "zone": "zone", "map": "map",
                   "x": "position_x", "y": "position_y", "z": "position_z"},
    "mail": {"table": "mail", "receiver": "receiver"},
    "group_member": null
  },
  "bridge": null
}
```

Citations, `wow-tbc` (`cmangos.md` § TBC): SOAP compiled in, key, defaults, auth, level column,
`SEC_CONSOLE` execution, world thread, output-or-fault — `MaNGOSsoap.cpp:75-140`, `MaNGOSsoap.h:49`,
`Master.cpp:248-249`, `mangosd.conf.dist.in:1858-1860`, `AccountMgr.cpp:229`, `World.cpp:1748`,
`:2229-2240` (`cmangos.md:120-135`). One request at a time — `MaNGOSsoap.cpp:51-63` (`:135`).
**`urn:MaNGOS` is the expected namespace and is UNVERIFIED at the pinned tree** — the reader could
not read the generated `soapC.cpp` (`cmangos.md:604-609`); the 8.1 TBC gate reads
`src/mangosd/MaNGOSsoap.h`'s `//gsoap` directive and pins whatever it says. `SOAP.IP = 0.0.0.0` for
the same published-port reason as WotLK. `shutting_down_fault: null` — the SOAP loop tests
`World::IsStopped()` between requests (`:51-63`), and what a request in flight sees during a stop
is unread; the seam's timeout is the answer there. Command rows — `Chat.cpp:829`, `:734`, `:737`,
`:1003`, `:162-163`, `:74-75`, `:814`; handlers `Level1.cpp:1503`, `:1544-1552`; `Level3.cpp:6557`,
`:6534`, `Mail.h:49` (12); `Level3.cpp:3407`, `:3419-3421`; `:3934`, `:3961`; `Level2.cpp:3627`,
`:3645-3654`; `Level3.cpp:1080`, `:1098`, `:1112`, `:1126`; `Level0.cpp:89`, `:104-107`
(`cmangos.md:181-207`). `account set password` — the row (`Chat.cpp:75`, `Level3.cpp:1132`) was
"read by hand after the report" (`phase8-delta.md:21-22`, `:77`); its argument count was not, so
`arity_verified: false` and the 8.3 gate records it. `reload_config: null` — "no `reload config`
row was asked" (`phase8-delta.md:80`). `SEC_*` numbering — `Common.h:124-128` (`:178-179`).
Bot marker — the username prefix is the only durable marker (`realmd.sql:45`,
`PlayerbotAIConfig.cpp:939`, `aiplayerbot.conf.dist.in:57` `RNDBOT`, code default `rndbot`
`:500`; `cmangos.md:265-275`, `:296-298`); compared upper-cased on every tree. Tables — `mangos.sql:2962`,
`:2967`, `:1983`, `:1990`; `characters.sql:791-811` (`:283-288`). The `item_template`,
`game_tele` and `characters` column names other than the cited `name`/`level`/`online`/`account`
are the MaNGOS-lineage spelling and **not read at this tree**: `dbreads` never guesses at runtime
— a wrong column is a `DockerCommandError` from the client (`docker.py:3421` documents the three
answers), reported as `could_not_ask`, and the 8.4/8.5 TBC gate's first query is where each name
is proved. `mail.receiver` likewise.

### `wow-vanilla`

The `wow-tbc` block with these deltas, every one measured on `mangos-classic` @ `8ec338a1`
(`cmangos.md` § Vanilla): `mangosd.conf.dist.in:1724-1726` for the SOAP keys (`:321`);
`"mail_items_max": 1` — `Mail.h:49` `#define MAX_MAIL_ITEMS 1`, enforced `Level3.cpp:6393`
(`:357`); `"level_max": 60`; command rows `Chat.cpp:810`, `:724`, `:727`, `:962`, `:162-163`,
`:74-75`, `:795` and handlers `Level1.cpp:1438`, `:1478-1487`; `Level3.cpp:6416`, `:3281`,
`:3293-3295`, `:3795`, `:1041`, `:1087`; `Level2.cpp:3609`; `Level0.cpp:88` (`:351-373`);
tables `mangos.sql:2658`, `:1875`, `characters.sql:640`, `:659`, `realmd.sql:43-46` (`:391-400`).
No `account_access`, no registry (`:402`). Everything not listed is the TBC value because the
same `cmangos/playerbots` SHA is built in (`:377`) — and where the TBC value is itself unverified,
so is this one; the gate that proves it for TBC proves it for Vanilla separately (rule 9).

### `wow-tortoise`

```json
"ops": {
  "channels": {
    "soap": null,
    "attach": true,
    "queue": {"db": "auth", "table": "pending_commands", "command_column": "command",
              "realm_column": "realm_id", "run_at_column": "run_at_time",
              "realm_id": 1, "poll_s": 60, "max_chars": 250, "returns_output": false}
  },
  "gm_level": {"store": "column", "column": "rank", "max": 4, "cached_in_memory": true, "set_via": "command"},
  "commands": {
    "server_info":          {"text": "server info", "level": 0, "offline": true},
    "tele_name":            {"text": "tele name {player} {tele}", "level": 3, "offline": true},
    "send_items":           {"text": "send items {player} \"{subject}\" \"{body}\" {items}", "level": 3, "offline": true},
    "send_money":           {"text": "send money {player} \"{subject}\" \"{body}\" {copper}", "level": 3, "offline": true},
    "revive":               {"text": "revive {player}", "level": 3, "offline": true},
    "character_level":      null,
    "character_rename":     {"text": "rename {player}", "level": 2, "offline": true},
    "account_set_password": {"text": "account set password {account} {password} {password}", "level": 3, "offline": true, "arity_verified": false},
    "account_set_gmlevel":  {"text": "account set gmlevel {account} {level}", "level": 4, "offline": true},
    "reload_config":        null,
    "mail_items_max": 1,
    "level_max": 60
  },
  "bots": {"account_prefix": "RNDBOT", "registry": null,
           "prefix_conf": "etc/aiplayerbot.conf:AiPlayerbot.RandomBotAccountPrefix"},
  "tables": {
    "item_template": {"entry": "entry", "name": "name", "quality": "quality", "item_level": "item_level",
                      "required_level": "required_level", "class": "class", "subclass": "subclass",
                      "inventory_type": "inventory_type", "display_id": "display_id"},
    "game_tele": {"id": "id", "name": "name", "map": "map", "x": "position_x", "y": "position_y", "z": "position_z"},
    "characters": {"guid": "guid", "account": "account", "name": "name", "level": "level", "class": "class",
                   "race": "race", "gender": "gender", "online": "online", "zone": "zone", "map": "map",
                   "x": "position_x", "y": "position_y", "z": "position_z"},
    "mail": {"table": "mail", "receiver": "receiver"},
    "group_member": null
  },
  "bridge": null
}
```

Citations, `wow-tortoise` (`cmangos.md` § Tortoise, `tortoise-wow` @ `7c0fb278`): **no SOAP, no
RA** — `src/mangosd/CMakeLists.txt:19-30` and `dep/src/CMakeLists.txt:19-25`, both complete lists
(`:425-428`, `:651-655`); the queue — `World.cpp:2724`, `:845` (60 s), `:3946-3948` (no output,
row deleted after queueing), `create_databases.sql:2572-2578` (250 chars, `realm_id`,
`run_at_time`), `Master.cpp:799` + `mangosd.conf.dist.in:8` (`RealmID = 1`) (`:429-435`;
`phase8-delta.md:34`). `AccountTypes` 0–6 with `SEC_DEVELOPER = 3`, `SEC_ADMINISTRATOR = 4`,
`SEC_CONSOLE = 6` — `Common.h:184-193` (`:464-476`); the level column is `account.rank`
(`create_databases.sql:2093`), written by `AccountMgr::SetSecurity` and **cached in memory**
(`AccountMgr.cpp:250-256`, `:261`; `:505`, `:572-575`) — hence `set_via: command` is the only
route that the running server sees. Command rows — `Chat.cpp:716`, `:663`, `:666`, `:894`,
`:151-165` (no `character level`), `:850` (top-level `rename`), `:69-70`, `:705`; handlers
`Commands.cpp:7993`, `:8033-8042`; `:5521`, `Mail/Mail.h:51` (1 item); `:3024`, `:3037-3043`;
`:12564`; `:275`, `:293`, `:307`, `:321`; `:6355-6369` (`:481-513`). `account set password` on
Tortoise is `SEC_DEVELOPER`, read by hand (`phase8-delta.md:77`); arity unread. `server info`
from a console caller prints `Players online: N. Max online: M.` and uptime (`Commands.cpp:6357-6369`).
Tables — `tw_world_item_template.sql:26-32` (`display_id`, a `description` column), `:30`
(`name`); `tw_world_game_tele.sql:26`, `:33`; `create_databases.sql:963-982` (`characters`,
`online`, `account`, `name`) (`:553-560`). The other `item_template` column spellings are
**inferred from `display_id`'s snake_case and unread** — the same runtime rule as TBC applies, and
the 8.4 Tortoise gate's first query proves or fails them. Bot marker — `aiplayerbot.conf.dist.in:63`
`RNDBOT`, `PlayerbotAIConfig.cpp:545` (`:544`). Eluna is compiled in and enabled
(`docs/ELUNA.md:3-14`, `mangosd.conf.dist.in:2304`; `:581-590`) — **not used by this design**
(Q4: My Party is WotLK-only; nothing is written for Eluna).

---

## 4. Per-feature detail, per step 8.1–8.9

Conventions for every step below. **World thread**: every command channel queues onto the one
`World::Update` drain and blocks the caller until the handler returns — `AC World.cpp:1341`,
`:1641-1657`, `ACSoap.cpp:120-130`; `TBC World.cpp:1748`, `:2229-2240`, `MaNGOSsoap.cpp:112-128`;
`TW World.cpp:3079`, `:3913-3922` (`phase8-delta.md:35`). What the world thread *pays* is the
handler's own body (a `.tele name` on an offline character is one `SavePositionInDB`); what the
*launcher* pays is the wait, bounded by the seam's timeouts: SOAP connect 5 s / read 30 s (the
numbers `RUST soap.rs:246-248` ran live with), attach 0.6 + 3.0 s per command (`console.py:45`,
`:76`), queue ≤ 60 s + the verify read. Measured floor: `server info`'s nine lines came back
inside a 3 s attach window on a server with 1845 bots (`console.py:1-30`). A timeout never
un-queues a command, so **the seam never retries a write on `could_not_ask(timeout)`**; reads
may be retried. **Server states** the seam distinguishes by asking `docker.container_state(world)`
(`docker.py:1883-1907`: status, `StartedAt`, `RestartCount` in one inspect) and, for a stopped
or missing container, nothing else:

| Situation | SOAP | attach | queue |
|---|---|---|---|
| world container absent/exited | connect refused → `could_not_ask(stopped)` | `ConsoleError` (docker's own words) → `could_not_ask(stopped)` | refused before the `INSERT` (`could_not_ask(stopped)`): a row would run at the next boot |
| starting (running, no ready marker in `logs --since StartedAt`) | refused → `could_not_ask(starting)` | window without a prompt (`prompted=False`, `console.py:98-107`) → `could_not_ask(starting)` | refused before the `INSERT` |
| ready, listener absent | refused after ready → `could_not_ask(soap_off)` — the 8.1 verdict "enabled but not arrived" | n/a | n/a |
| shutting down | AC: receiver fault `shutting_down_fault` → `could_not_ask(shutting_down)`; CMaNGOS: timeout | prompt may not return → `prompted=False` | the `INSERT` lands; the row is deleted by the seam if unconsumed after `2 × poll_s` |
| slow (queue behind a long handler, e.g. `.saveall` on ~2000 characters, which the stop path measured at 58–91 s of saves, `docker.py:938`) | read timeout → `could_not_ask(timeout)`; the command may still run | window elapses → `prompted=True, lines=()` (an honest empty) | verify read answers `no` until it answers `yes`; the UI shows "queued, not yet seen" |
| refused by the server (bad name, level from the DB `command` table, `Console::No`) | sender fault with the server's words → `no` | the server's words in `lines` → `no` | never visible: `returns_output: false`; only the verify read can say `no` |
| the daemon cannot be asked (no docker CLI, distro down) | n/a — the seam checks `container_state` first | `DockerCliMissingError` → `could_not_ask(docker)` | `ApplyError` → `could_not_ask(docker)` |

**Capability before a button.** `commands.capability(entry, id, probe)` answers `yes` when
`entry.ops.commands.<id>` is not `null` and the last probe answered `yes`; `no` when the entry
says the command does not exist on this core (Tortoise `character_level`) — the button is not
drawn, and the tab says why in one line; `could_not_ask` when the probe could not be asked — the
button is drawn disabled with the probe's reason. The probe is `Soap.probe_command` (or `server
info` over attach), fired on demand (Refresh, after Start reaches ready, before a write), **never
on the 5 s status timer**: a probe is a world-thread slot, and the dashboard reads `docker` and
MySQL instead.

### 8.1 — The command channel

| | WotLK | TBC / Vanilla | Tortoise |
|---|---|---|---|
| Mechanism | SOAP on `127.0.0.1:7878`; enabled by adding `AC_SOAP_ENABLED=1`, `AC_SOAP_IP=0.0.0.0`, `AC_SOAP_PORT=7878` to the override's `environment:`, the mechanism `catalog.json:57-59` already uses for the bot count | SOAP on `127.0.0.1:7878`; enabled by patching `SOAP.Enabled = 1`, `SOAP.IP = 0.0.0.0`, `SOAP.Port = 7878` into `<server_dir>/etc/mangosd.conf`, the file the conf stage patches in place (`families/cmangos.py:992-1071`, `ETC_DIR = "etc"` `:150`), bind-mounted at `{{CORE_DIR}}/etc` (`shared/cmangos/base.yml.tmpl:112`); and publishing the port | No SOAP. The channel is `attach` where a pty exists (Linux, macOS, WSL-resident) and `queue` (`tw_logon.pending_commands`) where none does (native Windows) — both exist today or are one auth-DB `INSERT` |
| How the switch reaches a running install | `composegen.render_override()` (new, same template `override.yml.tmpl:13-23` and `{**DEFAULT_WORLD_ENV, **world_env, **soap.enable.env}`), written through `write_plan()`'s marker rule (`composegen.py:692-739`: a marked file is ours; unchanged bytes are not rewritten), then `docker compose up -d <world>` recreates the container — a running container keeps its old environment until recreated (memory `bot-population-is-500`) | `conf.apply_table(ConfPatchTable(source_dir=…, files={"mangosd.conf": ConfPatch(keys=soap.enable.keys)}), server_dir / "etc", tokens={})` (`conf.py:243-262`: in place, written only on change); `render()`'s base gains the `{{SOAP_PUBLISH}}` line, `write_plan()` rewrites the marked base; `compose up -d <world>` recreates mangosd (the SOAP thread starts in `Master.cpp:248` at boot) | Nothing to switch on. `attach` is `Console.Enable = 1` (`mangosd.conf.dist.in:2042`); the queue table ships in `create_databases.sql:2572` |
| On an install that is not running | The override/conf/base are written; the next Start (`docker.start_staged`, unchanged) brings the value up. `plan()` reports "will arrive at the next start" | same | n/a |
| The app account | `accounts.create_account(sql, "yulon_<install_id>", pw, gm_level=3, scheme="azerothcore")` — the SRP6 row + `account_access (id, 3, -1)` (`accounts.py:312-397`, `:437`, `:507-509`); `install_id` from `composegen.install_id(server_dir)` (`composegen.py:135`, 8 hex → 14 chars ≤ `MAX_USERNAME` 17, `accounts.py:93`); password 16 of `[A-Za-z0-9]` from `secrets` (≤ `MAX_PASSWORD` 16, `:94`) | same call, `scheme="mangos_srp6"` → `v`/`s` row and `UPDATE account SET gmlevel = 3` (`accounts.py:420`, `:484-500`); `min_gm_level` 3 = `SEC_ADMINISTRATOR` (`MaNGOSsoap.h:49`) | none — no channel authenticates |
| Verified before persisted | `SoapChannel.probe()` = `server info`; `yes` only when the HTTP status is 200, the body is a result (not a fault), and `probe_expect` matches. 401/403 → `could_not_ask(auth)`: the row exists but the server does not accept it — nothing is persisted | same, `probe_expect: null` → any non-fault reply with ≥ 1 line; the gate records the line and pins it | the attach probe (`server info`, `prompted=True`) is recorded; nothing is persisted |
| Where persisted | `config_dir()/channels/<game>-<install_id>.json`, mode 0600 (`platform.config_dir()`, `platform.py:68`; the file holds `game, install_id, server_dir, username, password, host_port, verified_at, verified_reply`); **never** in `state.json`, which `state.py:24-48` types as install identity and nothing else; **never** under the server dir | same | a record with `channel: attach|queue` and no secret |
| Second press | `plan()` reads the file, probes: `yes` → "set up; verified just now" and the file's `verified_at` is refreshed; `could_not_ask(auth)` with the file present → the file is stale (the password was changed by hand, or the row was recreated) → the tab offers **Reset the app account**, which calls the new `accounts.reset_password()` for that one name (the seam refuses any other), re-probes, and persists only on `yes`; row present but no file (a reinstalled app, a wiped `config_dir`) → the same reset path, because the password is unrecoverable by design (`accounts.py:339-341` never rewrites one); `create_account` itself converges on every call and never makes a second account (`:327-337`) | same | idempotent record |
| World thread | The probe: one `server info` (PLAYER, `Console::Yes`, `cs_server.cpp:89`; `SEC_PLAYER` `Chat.cpp:814`/`:795`). The recreate is a stop/start of the world (300 s grace, `docker.py:938`) — named in the tab before the press | same | none |
| Reads / writes | Reads: `SELECT id FROM account` (auth), `container_state`, `published_bindings` (7878 must read `127.0.0.1`, else the seam refuses to persist — the `.env` `DOCKER_SOAP_EXTERNAL_PORT` remedy is a capability). Writes: `auth.account`, `auth.account_access`/`account.gmlevel` (Q7-legal; the path that already runs while the server is up, `phase8-reads/yulon.md:356-361`); the override / `etc/mangosd.conf` / base file on the host | | none |
| Surface | Server tab, group **Command channel**: one line of state (`not set up` / `set up, verified <time>` / `set up, not answering: <reason>` / `this server has no remote channel; console commands use the attach console (Linux/macOS/WSL) or the 60 s queue (Windows)`), one button **Set up** (idempotent), one **Reset the app account** shown only in the stale case. Phase 9 polishes copy and placement | | |
| Proven for real | **yulon-ubuntu** (WotLK 7.2 install, Off at baseline): Start → ready; press **Set up** → the tab shows `set up, verified <time>`; capture `pyplan/gates/8.1-yulon-ubuntu-wotlk/`: `docker inspect ac-worldserver --format '{{.Config.Env}}'` showing the three `AC_SOAP_*` values **in the running container**, `docker ps --format '{{.Ports}}'` showing `127.0.0.1:7878->7878/tcp`, the seam's log line with the verified reply, `SELECT id, username FROM acore_auth.account WHERE username LIKE 'YULON_%'` and its `account_access` row, `ls -l ~/.local/share/yulon/channels/` showing mode `-rw-------`, a second press's log ("verified just now", no second row), and a connect attempt to `<LAN ip>:7878` refused. **Visible in-game effect:** none by itself — the channel is proved by the next step's effects; the operator-visible proof is `server info`'s `Connected players:` line arriving in the tab. **yulon-win11-gate** (WotLK, native Windows): the same press; this is the first command channel that has ever answered on native Windows (`console.can_send()` is False there, `console.py:156-164`; `controller_view.py:1292-1300`). **m910q** (TBC, with Tortoise stopped — shared 3724): the same press; capture `8.1-m910q-tbc/` with `grep -n '^SOAP\.' etc/mangosd.conf`, the base file's publish line, the recreate, the verified reply line (pins `probe_expect`), and the `MaNGOSsoap.h` `//gsoap` namespace line (pins `namespace`). **m910q** (Tortoise, running): the attach probe answers `Players online:`; the queue's dry run — `INSERT` a `server info` row, `docker logs --since` shows `Loaded command <id> from database: server info` (`World.cpp:3946`) within 60 s and the row is gone — capture `8.1-m910q-tortoise/`. | | |

**Relationships 8.1 asserts, and who asserts them** (rule 7): env declared ↔ env in the running
container (`docker inspect`, asserted by `channel_setup.enable`'s post-recreate read, not by the
file); conf key ↔ listener (a probe, asserted by `account()`); account row ↔ login accepted
(the probe's HTTP 200, not the row); host binding ↔ loopback (`published_bindings`, asserted
before persisting); credentials file ↔ this install (`install_id` in the file name and the
body, asserted on read); SOAP port ↔ this install (a second WotLK install cannot publish 7878 —
bug-checklist line 227 — so the file records the port `published_bindings` returned, never the
catalog default).

**What 8.1 does NOT do**: it does not change the install engine's stage list (`phase8-parity-decisions.md`
"What this overturns", row 4); it does not enable RA (`Ra.*` stays off; RA caps at the account's
level under `Ra.Restricted = 1`, `RASocket.cpp:153-154`, and SOAP does not); it does not touch the
Playerbot Command Server (refused, `phase8-delta.md:50`).

### 8.2 — Live dashboard and the log snapshot before stop

| | All four families |
|---|---|
| Dashboard mechanism | `dashboard.gather()` per tick: `docker.container_state(world)` (status, `StartedAt`, `RestartCount`); `dbreads.online_counts(sql)` = `SELECT COUNT(*) FROM <characters>.<characters> WHERE <online>=1 AND <bot_clause>` and its `NOT` twin; uptime = now − `StartedAt`. `verdict()`: `crash_loop` when `RestartCount` grew across ticks or `settled` is False (`docker.py:1870-1880`) — closes bug-checklist line 499; `starting` when running without the ready marker; `online` otherwise; `channel_down` is a separate flag from the last probe, never from the timer. The first tick fires at construction — closes line 552 |
| Per-family data used | `ops.bots` (marker), `ops.tables.characters.online`, `databases.characters`/`auth`/`playerbots` |
| World thread | **Not blocked** — reads and `docker inspect` only. `server info` is not on the timer |
| Reads / writes | Reads only |
| Log snapshot mechanism | `Controller.stop()`/`remove()` call the injected `log_snapshot("stop")` before `docker.stop_staged` (`controller.py:272-306`, add-after); `logsnap.snapshot()` resolves the world container by project (`docker.project_containers(pinned_project_name(server_dir))`, `docker.py:390`, `:804` — a service, not a name, for the reason `RUST logsnap.rs:77-84` records), reads `docker.log_tail(container, 2000)` (new, beside `_logs()` `docker.py:1913`), writes `config_dir()/logs/world-<YYYYMMDD-HHMMSS>-<game>-<reason>.log`, keeps the newest 10, excludes the file just written from the prune (`RUST logsnap.rs:183-190`) |
| Server states | stopped: the snapshot is skipped with a log line; starting: taken anyway (a boot log is evidence too); docker unreachable: logged, the stop proceeds |
| Surface | Server tab: one verdict line above the three up/down words (`online — 3 players, 497 bots, up 2h14m` / `starting` / `restart loop: 4 restarts since <time>` / `stopped`); the snapshot is one log line in the panel naming the file. Phase 9 polishes |
| Proven for real | **yulon-ubuntu** (WotLK): with 500 bots logged in, the tab's counts equal `SELECT COUNT(*) … online=1` split by the bot clause run by hand; a `docker kill ac-worldserver` twice in a row shows `restart loop` within two ticks; Stop writes `~/.local/share/yulon/logs/world-…-wow-wotlk-stop.log` whose last line is the worldserver's own last line before the stop (`docker logs --tail 1` captured a second before). Capture `8.2-yulon-ubuntu-wotlk/`. **m910q** (TBC, Vanilla, Tortoise, one at a time): the same three checks; Tortoise's count uses `tw_char.characters` and the `RNDBOT` prefix. **Visible in-game effect:** the player count on the tab changes when the tester logs a character in and out |

### 8.3 — Accounts: list, set password, GM level

| | WotLK | TBC / Vanilla | Tortoise |
|---|---|---|---|
| List | `dbreads.list_accounts(sql)`: `account ⋈ account_access ⋈ characters` (`RUST pages.rs:296-306`) minus bots (`bot_clause`) minus the app's own `yulon_<install_id>` | `account.gmlevel` in place of the join | `account.rank` |
| Set password | Command `account set password {a} {p} {p}` over the channel (ADMINISTRATOR, `Console::Yes`, three args, `cs_account.cpp:1044-1055`) — the server writes its own row. When the channel is `could_not_ask`, the tab offers the direct SRP6 write (`accounts.reset_password`, auth only, Q7-legal) with the sentence "the server is not answering; this writes the row directly, which the server reads at the next login" | same command at `SEC_CONSOLE` (`Level3.cpp:1132` / VAN); `arity_verified: false` → the 8.3 gate records the usage line the server prints and the catalog is corrected before the box ticks | attach or queue; the SRP6 fallback is `mangos_sha` (`accounts.py:430`) |
| GM level | `account set gmlevel {a} {n} -1` (`cs_account.cpp:939-944`) | `account set gmlevel {a} {n}`; 0–3; the caller must outrank — SOAP runs at `SEC_CONSOLE` = 4 so 3 is grantable (`Level3.cpp:1098`, `:1112`) | `account set gmlevel {a} {n}`, 0–4, `>` not `>=` (`Commands.cpp:293`, `:307`); **command only** — `rank` is cached (`AccountMgr.cpp:250-256`), a direct `UPDATE` would not be seen; `set_via: command` refuses the SQL fallback here |
| World thread | one command each | | |
| Reads / writes | Reads: auth + characters. Writes: the server's own (`auth`), or the SRP6 fallback (`auth`) | | the queue `INSERT` (auth) |
| Surface | Accounts tab: a table (name, level, characters, online), row actions **Set password…**, **GM level ▾**; the existing Create group untouched (`controller_view.py:1401-1445`) | | |
| Proven for real | **yulon-ubuntu**: set the tester's password from the tab; **visible in-game effect: the client logs in with the new password and fails with the old**; set GM level 3, `.gm on` works in-game (the guides' own check). Capture `8.3-yulon-ubuntu-wotlk/` with the channel log lines and `SELECT gmlevel FROM account_access`. **m910q** TBC the same; Tortoise: the queue path — the row is gone after ≤ 60 s and the client logs in with the new password |

Closes bug-checklist line 239 (the tab is create-only).

### 8.4 — Teleport, item search, item mail, revive, set level, rename, mailed money, gear sets

| | WotLK | TBC / Vanilla | Tortoise |
|---|---|---|---|
| Item search | `dbreads.search_items(sql, text, filters)` on `<world>.item_template` (`db_world/item_template.sql:23-40`), bound-escaped `LIKE`, limit 50, `ESCAPE '!'` (`RUST pages.rs:167-181`) | `mangos.item_template` (`mangos.sql:2962`/`:2658`) | `tw_world.item_template`, `display_id` (`tw_world_item_template.sql:32`) |
| Teleport list | `game_tele` (`db_world/game_tele.sql:23-32`) | `mangos.game_tele` (`mangos.sql:1983`/`:1875`) | `tw_world.game_tele` (`:26`) |
| Teleport | `teleport name {player} {tele}` — GAMEMASTER, `Console::Yes`, offline → `Player::SavePositionInDB` (`cs_tele.cpp:46`, `:156`). Verify read: `character_position` before/after | `tele name` `SEC_MODERATOR`, offline (`Level1.cpp:1544-1552` / `:1478-1487`) | `tele name` `SEC_DEVELOPER`, offline (`Commands.cpp:8033-8042`); attach, or queue + the verify read |
| Item mail | `send items {player} "{subject}" "{body}" {id:count …}` — 12 per command (`Mail.h:33`); `commands.py` strips `"` and CR/LF (AC #2695, `RUST soap_cmds.rs:249-252`); sender is the recipient from a console (`cs_send.cpp:117-118`). Verify read: `mail_count(receiver)` grew | 12 (`TBC Mail.h:49`); **1 on Vanilla** (`VAN Mail.h:49`) — `gm.mail_items` chunks by `mail_items_max` and says "N mails" before the press | 1 (`TW Mail/Mail.h:51`); attach or queue |
| Money | `send money` (`cs_send.cpp:43`, `:192`) — `.modify money` is `Console::No` (`cs_modify.cpp:54`) | `SEC_ADMINISTRATOR` (`Chat.cpp:737`/`:727`) | `SEC_DEVELOPER` (`Chat.cpp:666`) |
| Revive | `revive {player}` offline (`cs_misc.cpp:1217-1221`) — the stock command, not the Rust bridge's `dml_gm_revive` | offline (`Level3.cpp:3419-3421` / `:3293-3295`) | offline with its own message (`Commands.cpp:3037-3043`) |
| Set level | `character level {player} {n}` (`cs_character.cpp:447`), 1–80 | 1–70 / 1–60 (`Level3.cpp:3934` / `:3795`) | **no such command** (`Chat.cpp:151-165`; `.levelup` console false `:923`) → `character_level: null` → no button, one sentence |
| Rename | `character rename {player}` (`cs_character.cpp:332`) → `at_login` flag; visible at next login | `character rename` (`Level2.cpp:3627` / `:3609`) | `rename {player}` (`Chat.cpp:850`) |
| Gear sets | read: `character_inventory ⋈ item_instance ⋈ item_template`, bag 0, slot < 19 (`character_inventory.sql:23-31`, `item_instance.sql:24-26`, `Player.h:681`; the `< 19` bound is the reader's derivation, `azerothcore.md:584-585`); mail: the item-mail path, fresh copies, no enchants (`RUST gearsets.svelte.ts:6-10`); presets under `config_dir()/gearsets/` | inventory schema **not read** (`phase8-delta.md:62`) → `tables.gear: null` on the three CMaNGOS entries until an 8.4 read adds it; mailing a saved set works everywhere the mail path does (19 mails on Vanilla) | same |
| Refused, by the owner or the tree | coordinate teleport (Q7: every `.go` is `Console::No`, `cs_go.cpp:43-55`, and the only route is a `characters` write); `.additem` (online-only, `cs_misc.cpp:1747`); heal and summon-NPC (Q2a: only via the bridge, see 8.6) | `.go *` console false (`Chat.cpp:310-325`); `.additem` false | `.go xy` absent; `.additem` false |
| World thread | one command per action; a 19-piece Vanilla set is 19 commands, serialised, ~19 × (handler + wait) | | |
| Reads / writes | Reads: world (`item_template`, `game_tele`), characters (`characters`, `mail`, inventory). **Writes: none by the app** — every mutation of `characters` is the server's own handler on its own thread; the queue `INSERT` is auth | | |
| Surface | **Play** tab: a character picker (from `dbreads.characters_named`, humans only), a teleport search + Go, an item search + Mail (with count), Revive / Set level / Rename / Send gold buttons drawn from `capability()`, a Gear sets group (Save from <char>, Mail to <char>). Phase 9 polishes | | |
| Proven for real | **yulon-ubuntu** (WotLK): with the tester's character **online**, Teleport to `stormwind` → **visible: the loading screen and the character standing in Stormwind**; mail Hearthstone ×1 + 11 others → **visible: the mailbox icon and the twelve items**; Revive after `.die`; Set level 20 → the level on the character frame; Rename → the rename prompt at next login; 100 gold in the mailbox. With the character **offline**: the same teleport, and the position read from `characters` before login matches Stormwind's `game_tele` row; the mail waits at login. Capture `8.4-yulon-ubuntu-wotlk/`: every command's `Answer` line and each verify read's before/after. **m910q** TBC: the same set at level ≤ 70; **Vanilla: a 2-item mail arrives as 2 mails** (the cap-1 proof) — `8.4-m910q-vanilla/`; **Tortoise**: teleport + a 1-item mail over attach, then the same over the queue on the Windows box (`yulon-win11-gate`, Tortoise installed there 2026-09-05) — the row consumed and the mail row present within 60 s; Set level shows the "not on this server" sentence |

### 8.5 — Browse Bots

| | WotLK | TBC / Vanilla | Tortoise |
|---|---|---|---|
| Mechanism | `dbreads.list_bots(sql, page, filters)`: `SELECT guid,name,class,race,gender,level,online,zone FROM characters c WHERE <bot_clause> ORDER BY name LIMIT 50 OFFSET n` + a `COUNT(*)` (`RUST pages.rs:202-213`); `bot_clause` = `(c.account IN (SELECT account_id FROM <playerbots>.playerbots_account_type WHERE account_type IN (1,2,3)) OR c.account IN (SELECT id FROM <auth>.account WHERE UPPER(username) LIKE 'RNDBOT%'))` — both signals, because the registry can be empty with 1000 bots playing (`RUST botid.rs:4-17`) | prefix arm only: `c.account IN (SELECT id FROM realmd.account WHERE UPPER(username) LIKE 'RNDBOT%')` (`PlayerbotAIConfig.cpp:939`) | prefix arm on `tw_logon.account` |
| World thread | not blocked (reads) | | |
| Surface | **Bots** tab: a paged table with name/class/race/level/online/zone, a name filter, a class filter, the total. Phase 9 polishes | | |
| Proven for real | **yulon-ubuntu**: the total equals `SELECT COUNT(*) … <bot_clause>` by hand and equals the bot count `dashboard` shows as online + offline; filtering `class = 8` (mage) lists only mages; **visible in-game effect: `/who <a listed online bot>` in the client finds it**. **m910q**: TBC, Vanilla, Tortoise the same (the Tortoise column names are proved or the query fails loudly — captured either way). Captures `8.5-<box>-<game>/` |

### 8.6 — My Party (WotLK only, server-side route)

| Item | Detail |
|---|---|
| Why a bridge | `.playerbots bot add/addclass` is `SEC_PLAYER, Console::No` (`PB PlayerbotCommandScript.cpp:36`) with the session guard at `PlayerbotMgr.cpp:870-875`; the master is a live session (`:98-99`); gearing and spec are bot chat actions reachable only from a `Player&` (`PlayerbotAI.cpp:600`); addon traffic is rejected (`:611-617`). No `.playerbots` command builds a party from SOAP (`azerothcore.md:269-283`, MEM-0715 agrees). Q5 forbids the addon relay. The one route with a recorded live run is the mod-ale Lua bridge over SOAP (`RUST bridge.rs:189-195`, 2026-08-20) |
| What Yu'lon ships | Its own copies of `rust-main:cli/lua/party/{dml_addclass,dml_whisper,dml_uninvite,dml_login}.lua` (the project's own AGPL code, no client asset, no client write) under `pylauncher/lua/wow-wotlk/party/`, plus one new `yulon_ping.lua`. How they work, read from the scripts: each registers `PLAYER_EVENT_ON_COMMAND` (42), matches only when `player == nil` (console/SOAP origin), resolves the player by name, and calls `Player:RunCommand("playerbots bot addclass <class>")` / `Player:Whisper(msg, 0, bot)` / `bot:RemoveFromGroup()` — i.e. the player's own session runs the `.playerbots` command, which is exactly what the guard requires. **The player must be online** (`GetPlayerByName`); every script `print`s to the server log and returns `false`, so the SOAP reply is empty on success and on "player offline" alike |
| How the app knows they arrived | `yulon_ping.lua` answers through the handler, not the log: the hook's fourth argument (`chatHandler` in Eluna's `PLAYER_EVENT_ON_COMMAND`) → `SendSysMessage("yulon-bridge 1")`, which lands in SOAP's print buffer (`ACSoap.cpp:133-137`). **Whether ALE's hook passes that argument is unread** (`ALE` @ `c3de7942` is unpinned and `PlayerHooks.cpp` was read only for `reload ale`); the fallback guard is `docker logs --since <reload time>` containing `[yulon_ping] loaded`. The 8.6 gate decides which one is the guard; a bridge whose only evidence is a deploy report is the silent-bridge bug (`RUST bridge.rs:60-65`, `:189-195`) |
| How they get into the server | `party.bridge_deploy()`: (1) `mod-ale` must be installed through the Modules tab (`apply.Applier.install`, `apply.py:858`) — it is a C++ module with `build.rebuild: true` (`mod-ale.json:12-14`), which the applier does not act on (`phase8-reads/yulon.md:285`): **a worldserver rebuild is owed on any install that lacks it**, and the owner runs rebuilds (§12, question 1); (2) the manifest is corrected: `ALE.EnableLuaEngine` → `ALE.Enabled = true` (the key that exists, `mod_ale.conf.dist:63`; compiled default `false`, `ALEConfig.cpp:20`), and `source.rev` pinned (`manifest.py:71` already has the field) to the SHA the gate passes on; (3) the scripts are copied into `<server_dir>/env/dist/etc/modules/lua_scripts/` (host files; the directory is bind-mounted at `/azerothcore/env/dist/etc`, `base.yml.tmpl:269`; `ALE.ScriptPath` is the absolute container path the manifest already patches, `mod-ale.json:17-19`, `:29-35`); (4) `reload ale` over the channel (string-matched by the ALE hook from a console caller, `PlayerHooks.cpp:44-54`, via `ChatCommand.cpp:326`); (5) `yulon_ping` |
| Party operations | `add_bot(player, class)`: `capability(bridge)` → `dbreads.characters_named(player, online=1)` (refuse offline with the sentence, before any command) → snapshot `group_members(player)` → `dml_addclass` → poll `group_members` 12 × 500 ms (`RUST party.rs:321-333`) → the new member's name → `dml_whisper … talents autopick`, `dml_whisper … autogear`. `kick(bot)`: `dml_uninvite` + `dml_whisper … logout`. `dismiss_all`: the members read (bot clause) then `kick` each. Presets: `config_dir()/party/<name>.json` (class list + spec names from `spec_conf`). Class ids 1–9, 11 (no death knight, `RUST party_specs.rs:18-31`) |
| World thread | Each bridge command runs the player's `.playerbots bot addclass` on the world thread — the bot character load and group join a player typing it would cost — plus `autogear`/`talents` as bot AI actions. Serialised by the seam's lock; the poll is reads |
| Reads / writes | Reads: `characters`, `group_member`, `playerbots_account_type`. **Writes: none by the app**; the module writes what it always writes when a player adds a bot. Host files: the scripts and `mod_ale.conf` under the server dir |
| Server states | stopped/starting → the Bots tab's party group is disabled with the verdict's reason; bridge not loaded (`ping` answers `Command does not exist` or nothing) → **Enable My Party** button (deploy + reload + ping) and no party buttons |
| Surface | **Bots** tab, group **My Party**: player picker (online humans), class picker, **Add**, member list with **Kick**, **Dismiss all**, presets (Save / Load / Delete). Phase 9 polishes |
| Proven for real | **yulon-ubuntu**, after the owner's rebuild with mod-ale: Enable My Party → `yulon_ping` answers (or the log line — recorded which); with the tester online, Add mage → **visible: a new party member in the client's party frame within 6 s, with talents and gear on inspect**; Kick → **the member leaves the party frame**; Load a 4-class preset → four members. Capture `8.6-yulon-ubuntu-wotlk/`: `mod_ale.conf` as read back, `ls lua_scripts/`, the `reload ale` answer, the ping answer, each `Answer` line, and `SELECT memberGuid FROM group_member WHERE guid = …` before/after |

Inherits from the delta: could-not-ask 3 (the bridge on a Yu'lon install) is the box's first
question, not a prerequisite settled elsewhere.

### 8.7 — Module update checks; manifests for TBC, Vanilla, Tortoise

| Item | Detail |
|---|---|
| Update checks | `modules.refresh()` (`controller_wow_wotlk/modules.py:65`) already re-mirrors the manifest tree with ETags (`manifest_store.py:35-59`) and has no UI caller; 8.7 wires **Check for updates** to it and adds `git.commits_behind(dest, branch)` on both `RunnerGit` and `ContainerGit` (`git.py:409`, `:649`; a `fetch` + `rev-list --count`), shown per installed module. A module clone is under the server dir (`apply.py:854 clone_dir`) |
| The Q7 hole 8.7 must close | `Applier._sql()` writes into any of `auth/characters/world/playerbots` on a running server with no guard (`apply.py:1359-1387`; `phase8-reads/yulon.md:265-269`). 8.7 adds a refusal (not a stop) in `Applier.install/remove` for `SqlStep`s whose `db` is `characters` or `world` while `docker.status()` lists the world container — the same shape as `plan_restore()`'s refusal (`maintenance.py:934-946`) |
| CMaNGOS manifests | The three cores have flat confs under `<server_dir>/etc/` (`shared/cmangos/base.yml.tmpl:81`, `:112`) and no module directory; a "module" there is a conf activation (`ConfFile.file` is relative to the server dir, `manifest.py:143-154`) or a SQL mod. `has_manifests` becomes true on the three entries (`catalog.py:1013`) and each gets a `controller_<acronym>/modules.py` binding the shared store/applier — the one per-game binding the style guide allows. **Which items** are the first manifests is the owner's (§12, question 3) |
| World thread | `reload config` only where the entry has it (`reload_config` is `null` on the three CMaNGOS entries); otherwise the report says "restart to apply" |
| Proven for real | **yulon-ubuntu**: Check for updates lists `mod-playerbots` with a non-zero commits-behind number that equals `git -C modules/mod-playerbots rev-list --count HEAD..origin/master` by hand; installing a SQL-mod while the world runs is refused with the sentence, and succeeds after Stop. **m910q**: one conf manifest applied on TBC, the key read back from `etc/`. Captures `8.7-*` |

Inherits bug-checklist line 394 (nothing selected, both buttons enabled).

### 8.8 — Steam integration (Linux / Steam Deck only)

Outside the seam: no server is asked anything. `steam.py` writes two Non-Steam shortcuts
(`shortcuts.vdf`, binary VDF), grid artwork, and the compat-tool mapping for the client — the
manual procedure `archive/guides/wow-wotlk/WoW-WotLK-HOWTO.md:202-226` records. The file
format is **UNVERIFIED in this repository** (`phase8-delta.md:134-135`); the box starts with a read
of a real `userdata/<id>/config/` on a machine that has Steam (§12, question 4). Gate: the two
entries appear in Steam's library on that machine and launch. Not proven on any box this design
can name.

### 8.9 — Uninstall / purge

As `pyplan/phase8-decisions.md` (2026-08-31): `purge.plan()`/`run()`, ownership from the pinned
project, a new docker function rather than a flag on `remove_staged()`, `state.forget()` last,
the Windows read-only bit, WSL-resident folders. Two seam touches only: `purge.run()` calls
`logsnap.snapshot("purge")` first, and deletes the install's `config_dir()/channels/…json` and
`party/`/`gearsets/` records **after** the folder — the app's own state record is what the owner
said purge removes. Gate as that page: WotLK on yulon-ubuntu and one CMaNGOS game on m910q,
install → uninstall (kept) → reinstall → characters present; install → uninstall (unticked) →
nothing of the project remains (`docker ps -a`, `docker volume ls`, `docker image ls`, the folder,
`state.json`, the channels file).

---

## 5. Delivery order and gates

One box per family (Q3): **yulon-ubuntu** for WotLK, **m910q** for the CMaNGOS lineage (TBC as
the family's full gate; Vanilla and Tortoise for every row where their data differs — rule 9),
**yulon-win11-gate** for the two Windows-only facts (SOAP answers natively; Tortoise's queue is
the only route there), **yulon-fedora** spare. Every gate names live state; none is satisfied by a
capture's existence.

| Step | What lands | Gate (live state, on the box named) |
|---|---|---|
| **8.1a** | `Ops` models + four JSON blocks; `soap.py`, `channel.py`, `commands.py`, `dbreads.py`, `channel_setup.py`; `accounts.reset_password`; `composegen.render_override` + `SOAP_PUBLISH`; the Server tab's Command channel group; the Console tab's SOAP route | yulon-ubuntu, WotLK: after **Set up**, `docker inspect ac-worldserver` shows the three `AC_SOAP_*` in `.Config.Env`; `docker ps` shows `127.0.0.1:7878->7878/tcp`; the tab shows `verified <time>` and the `Connected players:` line; the account row and its `account_access (id,3,-1)` exist; the channels file is 0600; a second press makes no second row; the Console tab's `server info` answers **through SOAP** with no attach client spawned (`ps` shows none) |
| **8.1b** | nothing new | yulon-win11-gate, WotLK: the same press; the Console tab sends and answers on native Windows for the first time; `netstat -an` shows `127.0.0.1:7878 LISTENING` and nothing on the LAN address |
| **8.1c** | `shared/cmangos/base.yml.tmpl` gains the `{{SOAP_PUBLISH}}` line; the TBC/Vanilla conf tables gain the `SOAP.*` keys | m910q, TBC (Tortoise stopped): `etc/mangosd.conf` reads `SOAP.Enabled = 1`, `SOAP.IP = 0.0.0.0` after the press; the base file's publish line; the mangosd recreate; the probe answers; the `//gsoap` namespace line and the `server info` reply line are recorded and the catalog is pinned to them before the box ticks. Then Vanilla: the same on its own install |
| **8.1d** | `QueueChannel` | m910q, Tortoise: attach probe answers `Players online:`; queue dry run — the `Loaded command … from database` log line within 60 s, the row gone, no output anywhere (the fact, recorded) |
| **8.2** | `dashboard.py`, `logsnap.py`, `docker.log_tail`, `Controller(log_snapshot=)`, the verdict line | yulon-ubuntu: counts equal the hand query at 500 bots; two `docker kill`s → `restart loop`; Stop writes the snapshot whose last line matches `docker logs --tail 1`. m910q: TBC, Vanilla, Tortoise each |
| **8.3** | `list_accounts`, set password, GM level on the Accounts tab; `arity_verified` corrected | yulon-ubuntu: the client logs in with the new password and not the old; `.gm on` works at level 3. m910q: TBC the same; Tortoise via attach and, on yulon-win11-gate, via the queue (row consumed, login works) |
| **8.4** | `gm.py`, the Play tab, gear sets | yulon-ubuntu: the six in-game effects listed in §4 with the character online, then teleport + mail with it offline. m910q: TBC the set; **Vanilla a 2-item mail arrives as 2 mails**; Tortoise teleport + 1-item mail, "no set level" sentence. yulon-win11-gate: Tortoise mail over the queue |
| **8.5** | `list_bots`, the Bots tab's browse half | yulon-ubuntu: total = hand count; `/who` finds a listed online bot. m910q: three cores, the Tortoise column names proved |
| **8.6** | `party.py`, the Lua scripts, the corrected + pinned `mod-ale` manifest, the Bots tab's party half | yulon-ubuntu **after the owner's rebuild with mod-ale**: ping answers; Add mage → the party frame gains a geared, specced member in ≤ 6 s; Kick removes it; a preset loads four |
| **8.7** | Check for updates, `git.commits_behind`, the Q7 refusal in the applier, three `modules.py` bindings, the first CMaNGOS manifests | yulon-ubuntu: the commits-behind number equals `git rev-list --count` by hand; a SQL-mod is refused while running and applies when stopped. m910q: one conf manifest applied on TBC, read back from `etc/` |
| **8.8** | `steam.py` | a Linux box with Steam (§12): two library entries, both launch |
| **8.9** | `purge.py` as decided | yulon-ubuntu WotLK and m910q TBC: the two runs of `phase8-decisions.md` "Cost and gates" |

8.1a → 8.1b/8.1c/8.1d in any order; 8.2 after 8.1a; 8.3–8.5 after 8.2 and independent of each
other; 8.6 after 8.5 and the rebuild; 8.7 independent after 8.1a; 8.8 independent; 8.9 last (it
deletes the channels record 8.1 creates).

---

## 6. Proposed `8.x` checklist lines

Top-level, never nested (the shape `test_docs_pins.py` matches).

- [ ] 8.1 Command channel — `catalog.CatalogEntry.ops` (channels, gm_level, commands, bots, tables, bridge) with the four JSON blocks; `soap.py` (stdlib wire), `channel.py` (`SoapChannel`/`AttachChannel`/`QueueChannel`, three-outcome `Answer`), `commands.py` (typed builders + `capability()`), `dbreads.py`; `channel_setup.py` (enable per tree, the app account through `accounts.create_account`, verify-then-persist under `config_dir()/channels/`); `composegen.render_override` + `SOAP_PUBLISH`; `accounts.reset_password`; the Server tab's Command channel group; the Console tab's SOAP route. **Families/platforms:** SOAP on WotLK (Linux, Windows, macOS when a Mac exists), TBC, Vanilla (Linux); Tortoise attach + `pending_commands` (Linux attach; Windows queue). **DoD:** on each family's box, pressing **Set up** on a running server ends with the tab reading `set up, verified <time>` and a `server info` reply in the Console tab that arrived over SOAP (no attach client), the `AC_SOAP_*`/`SOAP.*` values present in the *running* container/conf, `7878` published on `127.0.0.1` only, exactly one `yulon_<install_id>` account with level 3, a 0600 credentials file, and a second press making no second account; on Tortoise the attach probe answers and a queued `server info` row is consumed within 60 s with the log line to show for it. **Gate:** yulon-ubuntu (WotLK), yulon-win11-gate (WotLK native Windows), m910q (TBC, Vanilla, Tortoise) — `pyplan/gates/8.1-<box>-<game>/`; visible effect: the Console tab answers `server info` on a box where it never could before (native Windows).
- [ ] 8.2 Live dashboard and the pre-stop log snapshot — `dashboard.py` (`docker.container_state` + `dbreads.online_counts` per tick, `verdict()` with restart-loop detection, first tick at construction), `logsnap.py` + `docker.log_tail`, `Controller.stop()/remove()` calling the injected snapshot first. **Families/platforms:** all four, every platform the install passed on. **DoD:** the Server tab shows players, bots, uptime and the verdict; the counts equal the hand query at 500 bots; two forced world kills turn the verdict to `restart loop` within two ticks; every Stop writes `config_dir()/logs/world-<ts>-<game>-stop.log` whose last line is the server's last logged line. **Gate:** yulon-ubuntu (WotLK), m910q (TBC, Vanilla, Tortoise) — `pyplan/gates/8.2-<box>-<game>/`; visible effect: the player count moves when a client logs in and out. Closes bug-checklist lines 499 and 552.
- [ ] 8.3 Accounts — list (`dbreads.list_accounts`, bots and the app account excluded), set password and GM level over the channel (`account set password`, `account set gmlevel` with the per-tree arity), the direct SRP6 fallback only when the channel cannot be asked and never for Tortoise's cached `rank`. **Families/platforms:** all four. **DoD:** from the Accounts tab, a password set for the tester's account logs the client in with the new one and refuses the old; a level-3 grant makes `.gm on` work in-game; the tab's list matches `SELECT username FROM account` minus bots by hand; `arity_verified` is true on every entry before the box ticks. **Gate:** yulon-ubuntu, m910q, yulon-win11-gate (Tortoise queue) — `pyplan/gates/8.3-<box>-<game>/`; visible effect: the login screen accepts the new password.
- [ ] 8.4 Play tab — teleport (named), item search + item mail (chunked by the per-tree cap), mailed money, revive, set level, rename, gear sets, every button drawn from `capability()`. **Families/platforms:** all four; Tortoise has no set level; Vanilla and Tortoise mail one item per message. **DoD:** with the tester's character online each action's in-game effect is seen (loading screen into the target; the mailbox with the items; alive after `.die`; the level on the frame; the rename prompt; gold in the mail); with it offline, teleport moves the stored position and the mail waits at login; no `UPDATE`/`INSERT` into `characters` or `world` is issued by the app (asserted by the seam test). **Gate:** yulon-ubuntu (WotLK), m910q (TBC; Vanilla's 2-mail proof; Tortoise attach), yulon-win11-gate (Tortoise queue) — `pyplan/gates/8.4-<box>-<game>/`; visible effect: the character standing in Stormwind (or Orgrimmar) and the mailbox icon lit.
- [ ] 8.5 Browse Bots — `dbreads.list_bots` with the two-signal clause on WotLK and the prefix clause on the CMaNGOS lineage, paged and filtered, on the Bots tab. **Families/platforms:** all four. **DoD:** the tab's total equals the hand count on each core; filtering by class lists only that class; a listed online bot answers `/who` in the client. **Gate:** yulon-ubuntu, m910q — `pyplan/gates/8.5-<box>-<game>/`; visible effect: `/who <name>` finds the bot.
- [ ] 8.6 My Party — WotLK only: the project's Lua bridge (`pylauncher/lua/wow-wotlk/party/*.lua` + `yulon_ping.lua`) deployed under the server dir, the `mod-ale` manifest corrected (`ALE.Enabled`) and pinned (`source.rev`), `reload ale` + `yulon_ping` as the arrival guard, `party.py` (add by class, kick, dismiss all, presets) with the online-player refusal before any command and the `group_member` poll after. **Families/platforms:** WotLK, every platform 8.1 passed on. **DoD:** on an install with mod-ale, Enable My Party ends with the ping answered (or the load line seen — recorded which); Add mage puts a new member in the client's party frame within 6 s wearing gear and talents; Kick removes it; a preset loads four; no file is written into any client folder. **Gate:** yulon-ubuntu after the owner's rebuild with mod-ale — `pyplan/gates/8.6-yulon-ubuntu-wotlk/`; visible effect: the party frame.
- [ ] 8.7 Modules — Check for updates (`modules.refresh` wired, `git.commits_behind`), the Q7 refusal for `characters`/`world` SQL while the world runs, `has_manifests` + `modules.py` for TBC, Vanilla, Tortoise with the first conf manifests the owner names. **Families/platforms:** all four. **DoD:** the per-module commits-behind equals `git rev-list --count` by hand; a SQL-mod install is refused with a sentence while running and applies when stopped; a conf manifest applied on a CMaNGOS server reads back from `etc/`. **Gate:** yulon-ubuntu, m910q — `pyplan/gates/8.7-<box>-<game>/`; visible effect: the module's in-game effect (per manifest `npcs`/notes).
- [ ] 8.8 Steam — `steam.py` writes the two Non-Steam shortcuts, artwork and the client's compat tool on Linux. **Families/platforms:** all four, Linux/Steam Deck only. **DoD:** both entries appear in Steam's library on a real Steam install and launch the server and the client. **Gate:** the box §12 question 4 names — `pyplan/gates/8.8-<box>/`; visible effect: the two library entries.
- [ ] 8.9 Uninstall / purge — as `pyplan/phase8-decisions.md`; plus the channels/party/gearsets records under `config_dir()` and a pre-purge log snapshot. **Families/platforms:** all four, gated on WotLK and one CMaNGOS game. **DoD:** that page's — kept characters survive a reinstall to the same folder; an unticked purge leaves no container, volume, image, folder, `state.json` record or channels file; unproved ownership refuses. **Gate:** yulon-ubuntu (WotLK), m910q (TBC) — `pyplan/gates/8.9-<box>-<game>/`; visible effect: the tile is back to Install, and after the kept-reinstall the characters are on the login screen.
- [ ] **Phase 8 exit criteria met** — every box above ticked on its named boxes; every `arity_verified: false` and `probe_expect: null` in `catalog.json` replaced by a measured value; no `characters`/`world` write anywhere in `yulon/` outside `maintenance.restore` and the applier's now-guarded SQL (asserted by a test); README §9's three lines struck.

---

## 7. Blast radius on the proven install and controller paths

- `catalog.json`: every entry gains an `ops` block (additive; `_Strict` refuses a typo at
  `load_catalog()`); `wow-wotlk`'s `install.native.azerothcore.world_env` is **not** changed —
  the SOAP env is `ops.channels.soap.enable.env` and reaches the override only through
  `render_override()`, so `render()`'s output and the committed fixtures
  (`tests/data/wotlk-compose-config.json`, `tests/data/wotlk-rendered/`) are byte-identical.
  Named so the 7.1 "matches the fixture" assertion keeps meaning what it meant.
- `catalog/installers/shared/cmangos/base.yml.tmpl`: one added line under mangosd `ports:`
  (`{{SOAP_PUBLISH}}`). TBC/Vanilla render one more publish line; Tortoise renders a comment.
  No CMaNGOS compose-config fixture exists; the 8.1c gate captures `docker compose config`
  before and after and the diff is that one line. "Ports in exactly one file" holds.
- `catalog/installers/wow-wotlk/native/*.tmpl`: **not touched**.
- `composegen.py`: one token, one new function; `render()`/`write_plan()`/`fill()` unchanged.
- `controller.py`: one optional constructor argument and two one-line calls; every existing test
  constructs without it and sees the same behaviour.
- `docker.py`: `log_tail()` added; nothing changed.
- `accounts.py`: `reset_password()` added; `create_account()` unchanged.
- `apply.py`: the 8.7 refusal in `install()`/`remove()` before `_sql()` — a behaviour change
  on the proven Modules tab (a running server now refuses characters/world SQL). Named, wanted.
- `ui/controller_view.py`: `ControllerServices` gains fields (every `_for_*` binder gains the
  same lines); tabs are added; the Console tab's Send picks a route — the attach route is the
  code that exists today, unchanged.
- **Not touched:** the install engine (`native.py`, `families/*`), `networking.py`, `state.py`,
  `maintenance.py`, `repair.py`, `console.py`, `start_staged`/`stop_staged`/`remove_staged`,
  the repair button, the `.env` port-remedy contract, the three CMaNGOS `console.py`/`accounts.py`
  wrappers.

---

## 8. Tests

Unit (no daemon, the shape `test_console.py`/`test_accounts.py`/`test_networking.py` use — a
`Recorder` seam, the real catalog, the real templates), all **new**:

- `test_ops_catalog.py` — parameterised over the four entries: `ops` validates; `bridge` only
  with `soap`; `queue` only with `attach`; every `CommandSpec.text` fills with its named
  placeholders and no other; `mail_items_max ≥ 1`; `account_prefix` non-empty; the two
  `arity_verified: false` and the `probe_expect: null` are enumerated by name so ticking 8.3/8.1c
  turns them red until the catalog is corrected (the citation-pass pattern of `test_docs_pins.py`).
- `test_soap.py` — envelope bytes for `urn:AC` and `urn:MaNGOS`; `parse()` on a result, a
  sender fault with detail, a receiver fault, HTTP 401/403/500, an empty body; entity decoding;
  a fake `http.client` connection asserting Basic auth and the timeouts **by field**; the
  password absent from every `repr`/log line.
- `test_channel.py` — `Answer` three outcomes; `SoapChannel` maps refused/timeout/401/403/fault/
  ok, consults a fake `container_state` and never retries a `write` on timeout (a fixture that
  answers differently on the second call proves it); `AttachChannel` maps `prompted=False`,
  empty lines, and `ConsoleError`; `QueueChannel` refuses to `INSERT` when the world is not
  running, inserts one row with `realm_id` and ≤ 250 chars, and its `run_statement` calls are
  asserted `db == "auth"` **only**; the lock serialises two concurrent `ask()`s.
- `test_commands.py` — every builder against the four entries; the quoting rule (a `"` and a
  CR/LF in a subject); level clamps; the 12/12/1/1 chunking; `character_level` refused on
  Tortoise with the sentence; `capability()` returns each of the three outcomes for each of the
  three probe states.
- `test_dbreads.py` — SQL asserted by substring per entry (schema names, column names, the
  two-signal clause on WotLK and the prefix-only clause elsewhere, `UPPER(...) LIKE`,
  `ESCAPE '!'`); TSV parsing including the empty-string-row case `docker.py:3421`'s docstring
  names; the seam's `run_statement` is never called (the Recorder raises on it).
- `test_channel_setup.py` — the state machine: create → verify fails → nothing persisted and
  no second `INSERT` on the next call; verify ok → the file, 0600, with the port
  `published_bindings` returned; stale file → the reset path only for the app's own name; the
  enable step's env dict and conf keys asserted by field; `published_bindings` returning
  `0.0.0.0` for 7878 → refuse to persist.
- `test_logsnap.py` — file name, tail size, retention 10 excluding the just-written file,
  the project-not-name resolution, a docker failure logged and swallowed; and in
  `test_controller.py` a new case: `stop()` calls the injected snapshot **before**
  `stop_staged` (order asserted on the Recorder).
- `test_dashboard.py` — `verdict()` over every `(status, restart_count delta, marker, probe)`
  combination; the timer path never fires a command (the Recorder's channel raises).
- `test_gm.py`, `test_party.py` — each action = capability → command text → channel → verify
  read, on a Recorder; the offline-player refusal before any command in `party`; `dismiss_all`
  reads the members through the bot clause.
- A **static** test, `test_q7_writes.py`: every `run_statement(` call site under `yulon/` outside
  `maintenance.py`, `apply.py` and `repair.py` passes the literal `"auth"` (asserted over the
  AST, not by grep — rule "audit by argv"); and `apply.Applier` refuses a `characters`/`world`
  step while a fake `status()` lists the world.
- Existing tests touched: `test_composegen.py` gains `render_override()` and `SOAP_PUBLISH`
  cases and keeps every byte assertion; `test_catalog_invariants.py` gains "the base template
  names `SOAP_PUBLISH` exactly once for the cmangos family and never for azerothcore";
  `test_controller_view.py` gains the new tabs' enablement from `capability()`.

Integration (a daemon, throwaway containers, never a real server — "gate the tool, not the
payload"): `test_soap_live.py` (new) runs `SoapClient` against a busybox `httpd` serving a canned
result and a canned fault, proving the wire and the timeouts; `test_queue_live.py` (new) inserts
into a throwaway mariadb:11 `pending_commands` table and reads it back through `DockerSql.query`.

Live gates: the table in §5, one per step per box.

Mutation discipline: purge `__pycache__` on both sides of every mutation (memory
`mutation-testing-pycache-trap`); N/N is a claim, not evidence.

---

## 9. Risks worth re-reading

- **A timeout does not un-queue a command.** SOAP waits on a future the world thread completes
  (`ACSoap.cpp:125-130`; `MaNGOSsoap.cpp:127-128`); if the launcher's 30 s expires first, the
  command still runs. Price: a teleport or mail may land after the tab said `could_not_ask`.
  Route: writes are never retried automatically; every write has a verify read; the UI says
  "may still arrive". The 30 s is Rust's number, not this project's — 8.1a records the longest
  `server info` wait seen on a 500-bot server and the timeout is set from that.
- **`SOAP.IP = 0.0.0.0` inside the container** is reachable from every container on the compose
  network (`ac-database`, `ac-authserver`; the CMaNGOS `db`/`realmd`). Enumerated and accepted:
  they are this install's own images; the host binding is loopback; the LAN step never touches
  7878. The route that breaks it is a hand-edited `.env` `DOCKER_SOAP_EXTERNAL_PORT=0.0.0.0:7878`
  — `channel_setup` reads `published_bindings()` and refuses to persist credentials while 7878 is
  published anywhere but `127.0.0.1`; that refusal is tested.
- **Security levels are a database question** (`ChatCommand.cpp:118-124`; `TBC Chat.cpp:1071`;
  `VAN Chat.cpp:1028`): the `level` in `CommandSpec` is the shipped default, and a `command`
  table row can raise it. Over SOAP AC skips the check (`Chat.cpp:52-56`) and CMaNGOS runs at
  `SEC_CONSOLE`, so the design does not depend on the number; it is data for the reader and for
  Tortoise's attach console (also `SEC_CONSOLE`). Price if wrong: a `no` with the server's words.
- **The world thread during a save storm.** A `.saveall` or a shutdown drains 7400–7700 saves
  (`base.yml.tmpl:242-246`); a command queued behind it waits. Route: no probe on the timer; the
  dashboard is reads; a write pressed during a stop is refused by `container_state` before it is
  sent.
- **CMaNGOS SOAP serves one request at a time** (`MaNGOSsoap.cpp:51-63`); a second connection
  queues in the kernel backlog. The per-install lock is the route; two launcher processes are
  not (single-instance is Phase 9).
- **The attach console is single-writer** (`console.py:57-73`): a human attached in a terminal
  corrupts the app's window. Unchanged risk, now smaller — SOAP replaces attach wherever it exists.
- **Tortoise's queue runs a row at the next boot** if the world is down when it lands
  (`World.cpp:2724` filters by time, not by liveness). Route: the seam refuses to enqueue unless
  the world is running and ready, and deletes its own unconsumed row after `2 × poll_s`. A row
  the app did not write is not touched. `realm_id` is `RealmID` from `mangosd.conf` (dist 1); if
  an install ever sets another, the row is never consumed — the verify read reports `no`, the
  stale-row delete cleans up, and the mismatch is visible in the log.
- **`characters.name` is `utf8mb4_bin` on AC** (`characters.sql:26`): `Testa` ≠ `testa`
  (`RUST paperdoll.rs:202-204`). Every name a user types is looked up with `dbreads.characters_named`
  first and the stored spelling is what goes into the command. CMaNGOS/Tortoise collations are
  unread; the lookup rule is the same, the gate proves it.
- **mod-ale is unpinned and needs a rebuild.** `manifest.py:71` can pin it; the rebuild is the
  owner's (§12). Until 8.6's gate, My Party's route has one recorded live run (2026-08-20) on a
  tree nobody pinned. The ping guard is what turns "deployed" into "arrived".
- **The credentials file is a secret on disk** (0600 on Linux/macOS; ACL-inherited under
  `%APPDATA%` on Windows, the same class as `.db_password`, `phase7-decisions.md` "Risks"). Who can
  read it: the user, root/Administrator, anything running as the user. What it grants: a GM-3 SOAP
  login on loopback. Route to limit it: the account is per install and has no characters; 8.9
  deletes it; 8.3 can reset it.
- **Two installs of one game** (bug-checklist line 227) share 7878 as they share 3724/8085 — the
  guard that refuses the second start today (`controller.py:194-214`) covers this; the channels
  file records the port the daemon actually published.
- **Unread column names on the CMaNGOS lineage** (`tables.*` beyond the cited ones): a wrong name
  is a loud `DockerCommandError`, never an empty list; the first gate query per core is where each
  is proved. The design does not inherit AC's names silently — it declares them per entry and
  says which are unverified.
- **`server info`'s CMaNGOS reply text is unrecorded** (`probe_expect: null`); until 8.1c pins it,
  "any non-fault reply" is the verification, which a wrong-but-answering server would satisfy.
  Price: one gate. Route: `test_ops_catalog.py` keeps the null red until the value lands.

---

## 10. What the implementer should NOT build yet

- **No fourth transport.** No RA/telnet (`Ra.*` stays 0), no Playerbot Command Server, no
  `docker exec` into the world container to speak HTTP, no Eluna bridge on Tortoise.
- **No settings surface, no override YAML writer.** `render_override()` re-renders from the
  template; the "settings surface" the override header names is Phase 9.
- **No probe on the status timer.** The dashboard is `docker inspect` + MySQL.
- **No automatic retry of a write**, on any channel, for any reason.
- **No `characters`/`world` SQL write**, including "just for the offline case" — the Rust
  launcher's third and fourth sanctioned writes (`RUST lib.rs:3800-3801`, `moduletail.rs:240-244`)
  are refused by Q7.
- **No heal, no summon-NPC, no `.additem`** (refused in the cut); no coordinate teleport.
- **No CMaNGOS bot party** (`.rndbot spoof/add/p`, could-not-ask 1) — plausible from source,
  unproven, and out of Q4.
- **No mod-ale in the default WotLK install** unless §12 question 1 says so.
- **No pinning of mod-ale before its gate** — pin the SHA the gate passes on.
- **No Windows Steam.** Linux/Steam Deck only (Q2d).
- **No keep-awake while the server runs, no auto-stop, no autostart** (Q2e, Q8iv).

---

## 11. Doc changes this phase makes

- `pyplan/style-guide.md` §3 — rows for `soap.py`, `channel.py`, `commands.py`, `dbreads.py`,
  `channel_setup.py`, `logsnap.py`, `dashboard.py`, `gm.py`, `party.py`, `purge.py`, `steam.py`
  (each added the day the module exists, as Phase 7 did); the `controller.py` row gains "and the
  pre-stop log snapshot through an injected seam"; the `catalog/catalog.py` row gains "`ops`:
  every per-tree command/channel/schema fact"; the `controller_<acronym>/console.py` row gains
  "the attach half of `channel.AttachChannel`".
- `pyplan/README.md` — §5 tree (the new modules, `pylauncher/lua/wow-wotlk/`, the new tests);
  §9 the three lines struck through with the date (Q2a/Q2b); §11 a line for `channels/`,
  `logs/`, `party/`, `gearsets/` under `config_dir()`.
- `pyplan/checklist.md` — the `8.x` lines of §6, replacing the placeholder under the kept
  2026-08-21 identification box.
- `pyplan/bug-checklist.md` — 239, 499, 552 closed by 8.3/8.2 with their gate paths; 501 and 795
  annotated "moot: the bash paths are deleted (7.2); the Python engine's binding is proved by
  8.1's `published_bindings` capture"; 394 and 227 annotated as inherited by 8.7/8.1.
- `pylauncher/manifests/wow-wotlk/modules/mod-ale.json` — `ALE.Enabled` in place of
  `ALE.EnableLuaEngine`; `source.rev` when 8.6's gate passes.
- `pylauncher/README.md` — the capability table gains "Command channel", "Play", "Bots" rows per
  platform as each gate records what ran.
- `pyplan/roadmap.md` — **not edited**; Appendix A of the decisions page carries §8.

---

## 12. Open questions for the owner

1. **mod-ale and the rebuild.** 8.6's gate needs mod-ale compiled into the WotLK image on
   yulon-ubuntu (2–4 h, and rebuilds are yours). Two ways to get there: (a) a one-off rebuild on
   that box for the gate, mod-ale staying an optional module every user must install and rebuild
   for before My Party works; or (b) `mod-ale` joins the default WotLK sources in `catalog.json`
   so every new install has the bridge and My Party is one press. (b) changes what 7.1 proved and
   adds a build dependency on an unpinned repo (pinned the day it passes). Recommend (a) for the
   gate and (b) as a Phase 9 question once the pin exists.
2. **Tortoise on Windows: a command that answers in ≤ 60 s with no text.** The queue is the only
   route there; the app verifies each effect by a read (the mail row, the position, the rank) and
   shows "queued — seen" or "queued — not seen after 2 minutes". Is that an acceptable Windows
   experience for Tortoise, or should Tortoise's Play/Accounts actions be Linux/macOS-only (attach)
   for v1?
3. **The first CMaNGOS manifests.** A CMaNGOS "module" is a conf activation or a SQL mod, not a
   directory. Which three items (per core) should 8.7 ship first — e.g. `ahbot.conf` tuning,
   `aiplayerbot.conf` population, a SQL mod you use — so the box has something real to gate?
4. **A machine with Steam.** No test box this side has Steam installed; 8.8's gate needs one
   (your Deck, or a Steam install on yulon-ubuntu/m910q). Which?
