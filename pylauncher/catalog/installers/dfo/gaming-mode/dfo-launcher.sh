#!/bin/bash
source @HOME@/dml-launcher-lib.sh      # must be first: it re-execs to drop Steam's overlay shim
# Dad's MMO Lab — Dungeon Fighter Online (115US, client 2.38.3.25) server launcher, for the
# server Yu'lon installed. Adapted from the Deck's own ~/dfo115-launcher.sh (Deck #2, played
# in Gaming Mode since 2026-10-11): the same private network and lifecycle, with Yu'lon's
# folders and records.
#
# THE SERVER AND THE GAME SHARE A PRIVATE NETWORK. Upstream's Windows launcher adds a WFP
# firewall rule so the client may only talk to loopback; the Linux equivalent is a network
# namespace with nothing but `lo` in it. This launcher creates it (a tiny `sleep infinity`
# holder keeps it alive), runs the server inside it, and records the holder in run/holder.pid.
# The "Dungeon Fighter Online" shortcut (dfo-client.sh) JOINS that namespace with nsenter, so
# the game reaches the server at 127.0.0.1:7001 / 127.0.0.2 and nothing else. No root anywhere.
#
# The records are Yu'lon's own: run/holder.pid, run/server.pid, run/server.logpath and
# .yulon-process.json in the server folder, so Yu'lon's DFO tab sees a server started here.

YULON_DATA="@CONFIG_DIR@"

# One line per value: server_dir, client_dir, account. Empty where Yu'lon has none.
yulon_values() {
    python3 -I - "$YULON_DATA" <<'PY' 2>/dev/null
import json, sys
data = sys.argv[1]
server = client = account = ""
try:
    for i in json.load(open(data + "/state.json")).get("installs", []):
        if i.get("game") == "dfo":
            server, client = i.get("server_dir") or "", i.get("client_dir") or ""
            break
except Exception:
    pass
try:
    ui = json.load(open(data + "/ui.json")).get("launchers", {})
    account = (ui.get("dfo|" + server) or {}).get("account") or ""
except Exception:
    pass
print(server); print(client); print(account)
PY
}
mapfile -t YV < <(yulon_values)
D="${DFO_SERVER_DIR:-${YULON_SERVER_DIR:-${YV[0]:-}}}"
D="${D:-@SERVER_DIR@}"
CLIENT="${DFO_CLIENT_DIR:-${YULON_CLIENT_DIR:-${YV[1]:-}}}"
ACCOUNT="${DFO_ACCOUNT:-${YULON_ACCOUNT:-${YV[2]:-}}}"
ACCOUNT="${ACCOUNT:-@ACCOUNT@}"
SCRIPTS="@SCRIPTS_DIR@"
ROOT=$D/src
LAN=$ROOT/server/work/dfo-lan
RUN=$D/run
LOGDIR=$D/logs
LOG=$LOGDIR/launcher.log
SERVER_LOG=$D/yulon-server.log
DB=$LAN/runtime/storage/dfolan.sqlite3
GUARD=$DB.admin-guard
PVF_SIZE=760846241               # 2.38.3.25 (Sep 15) Script.pvf — the only build this server accepts

# The same switches as Yu'lon's DFO tab (catalog.json `serve.env`), from upstream's 启动服务端.cmd.
export DFO_SHOP_OPEN_ALL=1 DFO_CONTRACT_PURCHASE_CRASH_FIX=1 DFO_MAX_ITEM_PERIOD=1 \
       DFO_QUEST_VISIBLE_NPC_RELAX=1 DFO_QUEST_NPC_DISTANCE_MULTIPLIER=5

mkdir -p "$RUN" "$LOGDIR"
dml_init "Dungeon Fighter Online" "$LOG"
dml_header

# Alive AND not a zombie: a process that has exited but is not reaped yet (Yu'lon's own
# child, when Yu'lon started it) keeps its /proc entry and its old name for a while.
live() { local s; s=$(sed -n 's/^.*) \([A-Za-z]\).*/\1/p' "/proc/$1/stat" 2>/dev/null)
         [ -n "$s" ] && [ "$s" != Z ] && [ "$s" != X ]; }
# A pid from a run file is only "ours" if it is alive AND still the same program —
# pids get reused, and a stale file must never make us signal a stranger.
pid_is() {   # pid_is <pidfile> <comm>
    local p; p=$({ tr -dc 0-9 < "$1"; } 2>/dev/null)
    [ -n "$p" ] && live "$p" && [ "$(cat "/proc/$p/comm" 2>/dev/null)" = "$2" ] && echo "$p"
}
holder_pid() {
    local p n; p=$(pid_is "$RUN/holder.pid" sleep) || return 1
    # …and it must really be in a different network namespace from us.
    n=$(readlink "/proc/$p/ns/net" 2>/dev/null)
    [ -n "$n" ] && [ "$n" != "$(readlink /proc/self/ns/net)" ] && echo "$p"
}
server_pid() { pid_is "$RUN/server.pid" dfolauncher.exe; }
server_log() { cat "$RUN/server.logpath" 2>/dev/null; }
dfo_ready() {
    server_pid >/dev/null || return 1
    grep -q '^Server listening on' "$(server_log)" 2>/dev/null
}

# ── Already up? (Launching the Server shortcut twice must not start two.) ────
if holder_pid >/dev/null && server_pid >/dev/null; then
    dml_ok "Server already running (pid $(server_pid))"
else
    # ── Preflight ─────────────────────────────────────────────────────────────
    [ -x "$LAN/bin/dfolauncher.exe" ] || dml_die "The Yu'lon DFO server is not installed." \
        "Expected: $LAN/bin/dfolauncher.exe. Install DFO in Yu'lon first."
    if [ -z "$CLIENT" ] || [ ! -d "$CLIENT" ]; then
        dml_die "No game client is chosen for DFO." \
            "Choose your client folder on Yu'lon's DFO tab: the server reads Script.pvf from it."
    fi
    CLIENT=$(readlink -f "$CLIENT")
    dml_ok "Game client: $CLIENT"
    pvf=$(stat -c %s "$CLIENT/Script.pvf" 2>/dev/null || echo 0)
    [ "$pvf" = "$PVF_SIZE" ] || dml_die "Script.pvf is not the 2.38.3.25 (Sep 15) file (size $pvf)." \
        "This server only accepts that exact build."

    # Tell the server where the client is (upstream reads it from server/launcher.local.json),
    # and where its saves live: the same two files Yu'lon's Start writes. Other keys are kept.
    python3 -I - "$ROOT/server/launcher.local.json" "$CLIENT" "$LAN/runtime/storage/local.json" "$DB" <<'PY' >>"$LOG" 2>&1 \
        || dml_die "Could not write the server's settings." "Log: $LOG"
import json, os, sys
path, client, storage, db = sys.argv[1:5]
def write(target, data):
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".yulon-new"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, target)
try:
    cfg = json.load(open(path, encoding="utf-8"))
except (OSError, ValueError):
    cfg = {"channel_identity": False, "server_binary": "work/dfo-lan/bin/wireprobe-pvf.exe"}
if cfg.get("client_dir") != client:
    cfg["client_dir"] = client
    write(path, cfg)
    print("client_dir ->", client)
if not os.path.exists(storage):
    write(storage, {"driver": "sqlite", "sqlite_path": db, "sqlite_busy_timeout_ms": 5000, "max_connections": 4})
    print("storage ->", db)
PY

    # First start: the server builds its own decrypted copy of Script.pvf
    # (server/work/client-build) and its caches. Several minutes, once.
    FIRST_START=0
    [ -f "$ROOT/server/work/client-build/Script.inner.pvf" ] || FIRST_START=1

    # Leftovers of a session that was not stopped.
    "$SCRIPTS/dfo-stop.sh" --quiet >/dev/null 2>&1

    # The database lock (<db>.admin-guard). A hard stop leaves it behind; the server
    # refuses to start for 60 s after its last refresh. Same rule as upstream's own
    # stop chain: remove it only when the pid recorded in it is provably gone.
    if [ -e "$GUARD" ]; then
        gp=$({ tr -dc 0-9 < "$GUARD"; } 2>/dev/null)
        if [ -n "$gp" ] && ! live "$gp"; then
            rm -f "$GUARD" && dml_ok "Cleared the last session's database lock"
        else
            dml_phase "Waiting for the last session's database lock to expire..."
            lease_old() { [ ! -e "$GUARD" ] || [ $(( $(date +%s) - $(stat -c %Y "$GUARD") )) -ge 65 ]; }
            dml_wait_until 30 3 lease_old || dml_die "The database is in use by another DFO server (pid $gp)." \
                "Close the other server, then launch this again."
        fi
    fi

    # ── Start: the private network, then the server inside it ────────────────
    rm -f "$RUN/holder.pid" "$RUN/server.pid"
    echo "$SERVER_LOG" > "$RUN/server.logpath"

    # The outer namespace maps us to root, which is what lets `ip link set lo up` work.
    # Run from / so nothing keeps another folder (an AppImage's mount) open.
    ( cd / && setsid -f unshare -rn sh -c 'ip link set lo up && echo $$ > "$1" && exec sleep infinity' \
        sh "$RUN/holder.pid" </dev/null >>"$LOG" 2>&1 )
    hp_ready() { holder_pid >/dev/null; }
    dml_wait_until 10 1 hp_ready || dml_die "Could not create the private network." \
        "Unprivileged user namespaces must be enabled (they are on SteamOS). Log: $LOG"
    HOLDER=$(holder_pid)
    dml_ok "Private network up (no internet inside — on purpose)"

    # Inside, a nested namespace maps us back to our own uid so every file keeps its owner.
    # nsenter and unshare both exec in place, so $$ is the server's own pid.
    ( cd "$ROOT" && setsid -f nsenter -t "$HOLDER" -U -n --preserve-credentials -- \
        unshare --user --map-user="$(id -u)" --map-group="$(id -g)" -- \
        sh -c 'echo $$ > "$1"; exec "$2" launch --server-only --root "$3" --account "$4"' \
        sh "$RUN/server.pid" "$LAN/bin/dfolauncher.exe" "$ROOT" "$ACCOUNT" </dev/null >"$SERVER_LOG" 2>&1 )
    sp_ready() { server_pid >/dev/null; }
    dml_wait_until 10 1 sp_ready || dml_die "The server process did not start." "Log: $SERVER_LOG"
    # Yu'lon's own record, so its DFO tab shows this server as running and can stop it.
    python3 -I - "$D/.yulon-process.json" "$(server_pid)" "$LAN/bin/dfolauncher.exe" "$ACCOUNT" <<'PY' >>"$LOG" 2>&1
import datetime, json, os, sys
path, pid, program, account = sys.argv[1:5]
record = {"pid": int(pid), "program": program, "account": account,
          "started": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
tmp = path + ".yulon-new"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(record, f, indent=2)
    f.write("\n")
os.replace(tmp, path)
PY
    dml_ok "Server starting (pid $(server_pid))"
fi

# ── Prove it is ready ────────────────────────────────────────────────────────
# Not a port check: the launcher prints "Server listening on" only after the gateway
# has loaded all the game data and written ready.json. ~30-90 s on a Deck; the first start
# also builds client-build/ and runtime/pvf-cache/.
dml_phase "Loading Arad (reading 760 MB of game data)..."
if [ "${FIRST_START:-0}" = 1 ]; then
    dml_note "First start: the server is preparing its copy of the game data."
    dml_note "This happens once and takes a few minutes. Later starts take about a minute."
fi
dfo_alive_or_ready() {
    dfo_ready && return 0
    if ! server_pid >/dev/null; then
        echo ""
        dml_die "The server stopped during startup." \
            "$(tail -3 "$(server_log)" 2>/dev/null | cut -c1-200)"
    fi
    return 1
}
dml_wait_until 200 3 dfo_alive_or_ready || dml_die "The server did not become ready in 10 minutes." \
    "Log: $(server_log)"
dml_ok "$(grep -m1 '^Server listening on' "$(server_log)")"

dml_ready "DUNGEON FIGHTER ONLINE IS READY!"
echo "  ----------------------------------------------"
echo "  How to log in"
echo ""
echo "     Username       $ACCOUNT   (automatic -- the game signs you in)"
echo "     Password       none     (this server has no passwords)"
echo ""


dml_note "Server 115US  •  client 2.38.3.25  •  saves: SQLite"

# ── Watch the client, stop the server when they are done ─────────────────────
# Under Proton the game's process is NOT called DFO.exe for long: its embedded
# Chromium (CEF) renames the main thread to CrBrowserMain, and that is the process
# name for the whole session. List both.
dfo_stop() {
    "$SCRIPTS/dfo-stop.sh" --from-launcher \
        && dml_footer "Server stopped! Safe to close."
}

dml_await_and_track "DFO.exe CrBrowserMain" "Dungeon Fighter Online" dfo_stop
