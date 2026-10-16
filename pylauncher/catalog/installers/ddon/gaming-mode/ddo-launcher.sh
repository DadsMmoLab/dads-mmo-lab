#!/bin/bash
# Dad's MMO Lab — Dragon's Dogma Online Server Launcher v2.0.0 (Yu'lon install, Deck #2)
#
# The SERVER app of the Lab's two-shortcut split (see ~/dml-launcher-lib.sh):
#   "Dragon's Dogma Online Server" -> konsole --hold -e bash ~/ddo-launcher.sh
#   "Dragon's Dogma Online"        -> ~/ddo-client.sh
# Start the server, prove it is ready, wait for the client, stay alive while you
# play, then shut the server down by itself when you close the game.
#
# v2.0.0: drives the server Yu'lon installed (~/yulon-ddon), not the old
# hand-built ~/ddo-server. Yu'lon owns that folder; this only starts and stops
# its compose project and rewrites the one column Yu'lon's own Networking screen
# rewrites (GameServerList.csv IpAddress). It never builds: a missing image is a
# "reinstall in Yu'lon" message.
#
# Server specifics:
#   - Arrowgene DDON in Docker, compose project in $SERVER_DIR (docker-compose.yml
#     + docker-compose.override.yml, project name pinned in .env). Container
#     ddon-server, SQLite in a named volume. No 3306.
#   - The login server hands the client the game server's address out of
#     etc/GameServerList.csv (mounted read-only, read once at start). Gaming Mode
#     runs the client inside the Steam Linux Runtime, whose sandbox has its own
#     loopback, so that address MUST be the Deck's LAN IP, never 127.0.0.1 (Yu'lon's
#     desktop Play runs Proton bare, which is why 127.0.0.1 works there). Re-pinned
#     on every launch so a new DHCP lease cannot break it.
#   - Readiness is the server's own "TrySocketListen - Listening on" lines plus a
#     real HTTP answer from the download server. Never a bare connect to 52100:
#     docker-proxy accepts it whether or not the server is up.
export PATH="/usr/bin:/usr/local/bin:/bin:$PATH"
unset LD_LIBRARY_PATH

# Sourced first, before anything forks: it drops Steam's overlay shim and
# re-execs once (see the library). Everything above this line runs twice.
source @HOME@/dml-launcher-lib.sh

# Yu'lon's own record of where it put this server, so a moved install still works.
yulon_server_dir() {
    python3 -I - "$HOME/.local/share/yulon/state.json" <<'PY' 2>/dev/null
import json, sys
for i in json.load(open(sys.argv[1])).get("installs", []):
    if i.get("game") == "ddon" and i.get("server_dir"):
        print(i["server_dir"]); break
PY
}
SERVER_DIR="${DDO_SERVER_DIR:-$(yulon_server_dir)}"
SERVER_DIR="${SERVER_DIR:-@SERVER_DIR@}"
CONTAINER="ddon-server"
LOGDIR="${DDO_LOGDIR:-@HOME@/ddo-logs}"
CSV="$SERVER_DIR/etc/GameServerList.csv"
CLIENT_PROC="DDO.exe"
PORTS="52000 52099 52100"   # game, web/download, login

mkdir -p "$LOGDIR"
LOGFILE="$LOGDIR/launcher-$(date +%Y%m%d-%H%M%S).log"

# Keep the pretty output clean; everything noisy goes to the log.
exec 2>>"$LOGFILE"

dml_init "Dragon's Dogma Online" "$LOGFILE"
dml_header

# No -f: compose auto-loads docker-compose.yml + docker-compose.override.yml and
# reads the project name from .env, exactly as Yu'lon runs it. The build file is
# deliberately left out (it is never auto-loaded, and this never builds).
compose() { ( cd "$SERVER_DIR" && docker compose "$@" ); }

container_running() {
    [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" = "true" ]
}

# ── 1. Preflight ──────────────────────────────────────────────────────────────
[ -f "$SERVER_DIR/docker-compose.yml" ] || dml_die "The Yu'lon DDON server is not installed." \
    "Expected: $SERVER_DIR/docker-compose.yml. Install DDON in Yu'lon first."
[ -f "$CSV" ] || dml_die "GameServerList.csv missing." "Expected: $CSV. Repair the install in Yu'lon."
command -v docker >/dev/null 2>&1 || dml_die "docker not found on PATH."
docker info >/dev/null 2>&1      || dml_die "Docker is not responding." "Is the daemon running?"
IMAGE=$(compose config --images 2>>"$LOGFILE" | head -n 1)
[ -n "$IMAGE" ] || dml_die "Could not read the server's compose project." "Folder: $SERVER_DIR"
docker image inspect "$IMAGE" >/dev/null 2>&1 || dml_die "The server image $IMAGE is missing." \
    "Reinstall DDON in Yu'lon (it builds the image); this launcher never builds."
dml_ok "Docker ready"

# ── 2. LAN IP (DHCP-resilient) ────────────────────────────────────────────────
detect_lan_ip() {
    local ip
    ip=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") { print $(i + 1); exit }}')
    if [ -z "$ip" ]; then
        ip=$(ip -4 -o addr show scope global 2>/dev/null | awk '$2 !~ /^(docker|br-|veth)/ { sub(/\/.*/, "", $4); print $4; exit }')
    fi
    printf '%s' "$ip"
}
LAN_IP=$(detect_lan_ip)
[ -n "$LAN_IP" ] || dml_die "No network connection found." \
    "Connect to Wi-Fi first: the game reaches the server through the Deck's network address."

# The data row is "10,<name>,<brief>,<max>,<IP>,52000,...". Rewritten IN PLACE
# (same inode, BOM and line endings kept): the file is a single-file bind mount,
# and a sed -i / rename would leave a running container reading the old inode.
csv_ip() { awk -F, '$1 == "10" { print $5; exit }' "$CSV"; }
REPINNED=0
old_ip=$(csv_ip)
if [ "$old_ip" != "$LAN_IP" ]; then
    python3 -I - "$CSV" "$LAN_IP" <<'PY' 2>>"$LOGFILE" || dml_die "Could not update the server address." "$CSV"
import re, sys
path, ip = sys.argv[1], sys.argv[2]
with open(path, "r+b") as f:
    data = f.read()
    new, n = re.subn(rb"(?m)^(10,[^,\r\n]*,[^,\r\n]*,[^,\r\n]*,)[^,\r\n]*(,)", rb"\g<1>" + ip.encode() + rb"\g<2>", data, count=1)
    if n != 1:
        sys.exit("no server row")
    f.seek(0); f.write(new); f.truncate()
PY
    REPINNED=1
    dml_log "GameServerList.csv: ${old_ip:-<none>} -> $LAN_IP"
fi
# Verify the edit took: a silent miss here is a hang at "entering world".
[ "$(csv_ip)" = "$LAN_IP" ] || dml_die "Could not pin the game server address." "Expected $LAN_IP in $CSV, found '$(csv_ip)'"
if [ "$REPINNED" = 1 ]; then dml_ok "Network ready ($LAN_IP, re-pinned)"; else dml_ok "Network ready ($LAN_IP)"; fi

# ── 3. Start the server ───────────────────────────────────────────────────────
START_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
FRESH_START=1
if container_running; then
    if [ "$REPINNED" = 1 ]; then
        # The server list is read once at startup.
        if compose restart >>"$LOGFILE" 2>&1; then
            dml_ok "Server restarted for the new network address"
        else
            dml_die "Failed to restart the server." "Log: $LOGFILE"
        fi
    else
        FRESH_START=0
        dml_ok "Server already running"
    fi
else
    for p in $PORTS; do
        if ss -Htln "sport = :$p" 2>/dev/null | grep . >/dev/null; then
            dml_die "Port $p is already in use." \
                "Another game server is probably running. Stop it first, then try again."
        fi
    done
    if compose up -d >>"$LOGFILE" 2>&1; then
        dml_ok "Server started"
    else
        dml_die "Failed to start the server." "Log: $LOGFILE"
    fi
fi

# ── 4. Prove it is ready ──────────────────────────────────────────────────────
WEB_URL="http://$LAN_IP:52099/win/versionlist"
web_up() { curl -fsS -o /dev/null --max-time 3 "$WEB_URL" 2>/dev/null; }

server_ready() {
    if ! container_running; then
        return 1
    fi
    if [ "$FRESH_START" = 1 ]; then
        local logs
        logs=$(docker logs --since "$START_TS" "$CONTAINER" 2>&1) || return 1
        # Bash pattern matches, not `| grep -q`: under pipefail grep -q's early
        # exit SIGPIPEs `docker logs` and reads as "not found" (the Lab's oldest bug).
        [[ "$logs" == *"[Login]  TrySocketListen - Listening on"* ]] || return 1
        [[ "$logs" == *"[Game]  TrySocketListen - Listening on"* ]] || return 1
    fi
    web_up
}

server_crashed() {
    ! container_running && return 0
    local logs
    logs=$(docker logs --since "$START_TS" "$CONTAINER" 2>&1) || return 1
    [[ "$logs" == *"Unhandled exception"* ]]
}

ready_or_crashed() { server_ready || server_crashed; }

dml_phase "Waking up Lestania..."
dml_note "Usually about 60 seconds"
dml_wait_until 60 3 ready_or_crashed \
    || dml_die "The server did not come up in 3 minutes." "Log: docker logs $CONTAINER"
if server_crashed; then
    docker logs --tail 40 "$CONTAINER" >>"$LOGFILE" 2>&1
    dml_die "The server stopped while starting." "Its last output is in $LOGFILE"
fi
dml_ok "Login server is up"
dml_ok "Game server is up"
dml_ok "Download server is up"

# ── 5. Stop (called when the client closes) ───────────────────────────────────
stop_server() {
    echo ""
    dml_note "Stopping the server..."
    compose stop >>"$LOGFILE" 2>&1
    if container_running; then
        dml_warn "The server did not stop cleanly — it was left running"
        dml_note "Run the \"Dragon's Dogma Online Server Stop\" shortcut, or: docker stop $CONTAINER"
    else
        dml_ok "Server stopped. Your character is saved."
    fi
    dml_footer "Server stopped! Safe to close."
}

# ── 6. Ready, then watch the client ───────────────────────────────────────────
dml_ready "DRAGON'S DOGMA ONLINE IS READY!"
echo "  Now launch \"Dragon's Dogma Online\" from your library."
echo "  Keyboard: E = confirm, Esc = back (controller works too)"
echo ""
dml_await_and_track "$CLIENT_PROC" "Dragon's Dogma Online" stop_server
