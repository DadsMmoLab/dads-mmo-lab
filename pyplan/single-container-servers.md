# Single-container servers: the catalog's second kind of entry

Written 2026-10-12 with the first one, DDON (Arrowgene's Dragon's Dogma Online server).
`pyplan/README.md` §1 puts games outside the four WoW servers after v1; this is the shape
such a game takes, built on a feature branch so it can be weighed against that.

## Why a second kind, and not a wider `CatalogEntry`

`CatalogEntry` is a WoW core's shape: `db`, `auth` and `world` containers, an `auth` and a
`world` port, MySQL schemas, a realmlist row, a client build. Roughly 300 call sites read
those fields (139 container names, 108 schema names, 48 realmlist). DDON is one container
whose one process runs its login, game and web servers on three ports, keeps SQLite in a
volume, and advertises its address in a CSV. Making the WoW fields optional would put a
`None` case at every one of those sites; faking them (one container named three times)
would be a lie every reader trusts.

So `catalog.json` gains a second list, `single`, of `SingleEntry`, and the WoW code never
sees one. `Catalog.get()` still returns only `CatalogEntry`; `get_single()` and
`is_single()` answer for the new list, and both lists share one id space and refuse each
other's container names and host ports.

## What it reuses, and what it adds

| Concern | Reused as is | Added for the second kind |
|---|---|---|
| Catalog | `Emulator`, `EmulatorSource`, `HelpPlace`, the path rules | `SingleEntry`, `ServerPort`, `SingleNative`, `SingleInstall`, `AdvertisedAddress`, `SinglePlay`, `SingleLauncher` (`catalog/catalog.py`) |
| Install | state file and claim (`native.InstallState`), `composegen.install_id/project_name/fill`, `families/dockerfile.py`, `git.ContainerGit`, Docker provisioning, `docker.build_staged`, `native.pump_lines` (lifted out of `StagedInstaller._pump`) | `catalog/single_install.py`: seven recorded, resumable stages (clone, Dockerfile, seed files, compose, build, up, ready) |
| Lifecycle | `docker.container_state/current_run_log/compose_up_service/stop_containers/follow_logs/port_conflicts` | `single_controller.py`: status from this run's ready lines, start, ownership-checked stop |
| Networking | `platform.detect_lan_ip`, `platform.firewall_commands`, `platform.elevated_batch` (one UAC prompt) | `single_network.py`: rewrites one CSV column byte for byte; skips firewall rules that already exist |
| Play | `play_launch` dispatch (Windows / Proton / Wine), lifted into `program_launch_spec()` | `single_play.py`: argument templates, account-name rule, the UAC retry for a client that demands it; `single_launcher.py`: **Add to DDON-Launcher** writes one section of the player's launcher settings, on that press only |
| UI | `DadcraftRealmBadge`, `LogPanel`, `ThreadedJobRunner`, `HelpPlacesBox`, `forgetting` wording | `ui/single_server_view.py`; Catalog tiles; the window's own registry beside `controllers` |

## The second runtime: host programs (DFO, 2026-10-13)

DFO's 115US server (`gitgud.io/fuckworld/115`, Go) cannot be one container. Its gateway
listens on port 7001 (on 0.0.0.0, measured) and hands the client a game server on
127.0.0.2 at a port it picks per run; a published container port covers neither, which
is why the Deck runs it in a network namespace and upstream runs it natively. Only this
computer can reach 127.0.0.2, so only this computer can play. So a
`SingleEntry` now names one of two runtimes in `install`: `native` (one container) or
`process` (`SingleProcess`: host programs). Same list, same tile, same tab; `container`
and `service` belong to `native` alone, and the WoW code still never sees either.

| Concern | Reused as is | Added for the process runtime |
|---|---|---|
| Catalog | `SourcePatch`, `HelpPlace`, `SinglePlay`, the path rules | `SingleProcess`, `VerifiedArchive`, `GoToolchain`, `GoBuild`, `ProcessFile`, `ProcessServe`; `CATALOG_REPO_HOSTS` (gitgud.io for the curated catalog only, never a manifest) |
| Install | the state file and `single_install._read_record()`, `composegen.install_id()`, `families/patch.py` (which learned to create a file), `platform.download_verified()`, `runner.stream()` | `catalog/process_install.py`: four recorded, resumable stages (fetch the source zip, patch, fetch the Go zip, `go build`); no Docker, no git, no `up` |
| Lifecycle | `selfupdate.layout.pid_liveness()`/`pid_is_this_app()`, `play_launch`'s detach flags | `process_controller.py`: Start renders the files the server reads, clears a dead holder's lock, runs `serve.argv` detached into a log and records pid + program; Stop ends that tree and no other |
| UI | the whole tab, through `single_controller.SingleServer` | "this computer only" in place of the network row; Start passes the account and the client; Play refuses an account the running server was not started for |

Facts that shaped it, measured on Windows 11 on 2026-10-13: the pinned archive's sha256
was the same on two downloads hours apart; with the two Deck patches upstream's own
`go test ./...` passes 49 packages and the build takes about 100 s; Install through the
harness took 44 s and a second press 0.4 s; the first Start was up in 45 s (the inner PVF,
byte-identical to upstream's 2.38.3.25 baseline, in 2 s) and a second in 30 s; Stop took
0.1 s and cleared the lease the kill left. Upstream's `stop` kills every
`probe.exe`/`wireprobe*.exe` on the machine, so Stop does not call it. The launcher's own
output is a few lines; the server's log is the gateway's `gateway.err` in a per-run
folder the launcher names (`Logs: <folder>`), which the console follows from that line.

## Steam Deck Gaming Mode (2026-10-16)

**Add to Steam Deck (Gaming Mode)…** (`gaming_mode.py`, Linux only) gives a single server the Lab's
Gaming Mode launchers: three scripts from `catalog/installers/<game>/gaming-mode/` and three Steam
shortcuts through `steam.py`'s codec, with no compatibility tool. The scripts are the Steam Deck
session's, measured on Deck #2; a test holds the templates to those bytes. DDON's launcher pins
the Deck's LAN address into the advertised-address file on every start. Its comment gives the
reason as the Steam Linux Runtime having a loopback of its own, but Deck #2 measured on
2026-10-16 that a process run through `_v2-entry-point` shares the host's network namespace and
reaches host listeners on 127.0.0.1. So the pin is more likely needed for the address the server
hands the client than for the sandbox; it works, and is kept as measured. DDON's entry carries
`play.gaming_mode`; another game joins by adding its own block and scripts. The catalog
names each script without its `.sh` (`catalog.json` names no bash file at all, F.2's
guard), and only the markers a game's scripts use are filled and checked.

**WoW in Gaming Mode (2026-10-16).** A WoW tab's **Add to Steam…** (8.8, Linux only) now
writes the same three tiles: "<game> Server" runs the Lab's launcher in Konsole, "<game>" is
8.8's game tile unchanged (the client's own executable through the compatibility tool), and
"<game> Server Stop". The scripts (`catalog/installers/wow/gaming-mode/`, `gaming_mode.wow_scripts()`)
do none of the server's work themselves. A WoW Start reserves the server against every
other Yu'lon, refuses a missing image or database and an ended import, keeps the realm's
port right and starts three services staged so the database import never runs again; a
bare `docker compose up` would skip all of it. So they call `yulon --server start|stop|status`
(`server_cli.py`), which runs the tab's own controller (`ControllerServices.for_entry()`)
and ready wait headless, before Qt starts an application and without the single-instance
claim. One game at a time, as the Lab's launchers have always done: a server holding the
ports is stopped first, and named. The game watch takes `Wow.exe WoW.exe` (`pgrep -x` is
case-sensitive and the clients differ). The realmlist stays 127.0.0.1, as Play's does: Deck #2
measured that the runtime shares the host's loopback. 8.8's two tiles become the three in
place, keeping their appids, artwork and play time. Not yet measured on a Deck: it needs a
Yu'lon WoW install there.

## DFO on Linux: the private network (2026-10-16)

DFO's catalog entry lists Linux beside Windows. Upstream's Windows launcher fences the
client in with a WFP rule (loopback only); the Linux equivalent, which the Steam Deck
session built and has played on Deck #2 since 2026-10-11, is a network namespace holding
only `lo`, made with unprivileged user namespaces, so nothing needs root.

| Concern | What changed |
|---|---|
| Catalog | `SourcePatch.platforms` (empty = every platform) and patch 0004, Linux only, so `dfolauncher` builds off Windows; `SingleProcess.toolchain_by_platform` (Go 1.26.5's linux-amd64 .tar.gz, same `dest`); DFO's `play.gaming_mode` with `desktop_play` |
| Install | the patch stage skips another platform's patches; the toolchain stage takes this platform's Go; `_unpack_tar()` keeps execute bits and refuses links, devices and paths that leave the root; `go_env()` starts from `runner.child_env()` |
| Lifecycle | `process_controller.py` on Linux: a holder (`unshare -rn`, `sleep infinity`) and the server inside it (`nsenter` + a nested `unshare` mapping back to the player's own uid), recorded in `run/` exactly as the Deck's scripts record them; names, not `/proc/<pid>/exe`, identify the processes; Stop refuses while the game runs, SIGINTs the server's group, waits for everything in the network (the gateway holds the save database's lock), then the lock rule, then the holder; a gateway that outlives a crashed server is swept by Stop and by the next Start; Start waits for the server's own pid record, so a refused `nsenter` is a failed Start; Yu'lon reaps its own exited children (a zombie reads as alive to `kill(pid, 0)`); no host port check |
| Play | on Linux, Play runs DFO's Gaming Mode client script through `bash -c` (`gaming_mode.play_spec()`), because only the script joins the network; the tab's account and client folder travel as `YULON_*` variables |
| Gaming Mode | DFO's three scripts, adapted from the Deck's: Yu'lon's folders, records and Proton prefix (`proton/dfo-<12 hex>`), Proton 11 and SteamLinuxRuntime_4 found through `libraryfolders.vdf` |

What the Deck session measured on Deck #2 (2026-10-16), with Yu'lon's recipe outside
Yu'lon: the `CGO_ENABLED=0` build in user space passes and is static; a cold Start reaches
READY in 91 s; the inner PVF matches upstream's baseline (c3801215); the AppImage's
`LD_LIBRARY_PATH` breaks SteamOS's `/bin/sh` unless every child gets `runner.child_env()`,
and a child whose working folder is inside the AppImage's mount keeps that mount busy.
Not yet measured: this code end to end on a Deck (install, Start, Play, Stop, the button).

## Not done yet

Rebuild and update to latest, backup and restore, "Find in WSL…" and "Bring from another
computer…" on the tile, the tray's server list, and a Windows support file section. Each
WoW version of those reads the WoW shape; each would be its own change. For DFO: playing
from another computer (upstream's server is loopback only today), moving the pin, and
bringing a save from the Deck's own `~/dfo115` install (its `dfolan.sqlite3` with `-wal`
and `-shm`, copied with both servers stopped).
