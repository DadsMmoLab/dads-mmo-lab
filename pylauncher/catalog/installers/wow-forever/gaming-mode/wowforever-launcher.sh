#!/bin/bash
# Dad's MMO Lab — WoW Forever Offline Server Launcher v1.0.0 (Yu'lon install)
#
# The SERVER app of the Lab's two-shortcut split (see ~/dml-launcher-lib.sh):
#   "WoW Forever Offline Server" -> konsole --hold -e bash <this script>
#   "WoW Forever Offline"        -> wowforever-client.sh
# Start the server, prove it is ready, wait for the game, stay alive while you
# play, then shut the server down by itself when you close the game.
#
# Drives the server Yu'lon installed (Karrz's ForeverCore with his PlayerbotV2
# bots): its compose project in $SERVER_DIR (docker-compose.yml +
# docker-compose.override.yml, project name pinned in .env), containers
# wowforever-database / -world / -bnet. It never builds and never installs: a
# missing image or folder is a "use Yu'lon" message. Steam Deck #1's launcher
# (kit v5, 2026-11-14) is where the readiness rules below were measured:
#   - World: "(worldserver-daemon) ready" in the logs of THIS start, so an old
#     line cannot fake it. The first start after an install or update imports the
#     world database and every update: about 16 minutes.
#   - Login: bnetserver prints no ready line, and docker-proxy answers any
#     published port whether or not the server is up, so the check is a real TLS
#     GET of /bnetserver/login/ with the install's own CA, under the same name and
#     port the game uses.
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
    if i.get("game") == "wow-forever" and i.get("server_dir"):
        print(i["server_dir"]); break
PY
}
SERVER_DIR="${WOWFO_SERVER_DIR:-$(yulon_server_dir)}"
SERVER_DIR="${SERVER_DIR:-@SERVER_DIR@}"
WORLD="wowforever-world"
BNET="wowforever-bnet"
PORTAL="trinity.actual.battle.net"
CLIENT_PROC="Wow.exe"
LOGDIR="${WOWFO_LOGDIR:-@HOME@/wowforever-logs}"
# The login web service's port, as Yu'lon rendered it (8082: Steam holds 8081 on SteamOS).
REST_PORT=$(sed -n 's/^ *REST_PORT: *"\{0,1\}\([0-9][0-9]*\)"\{0,1\} *$/\1/p' \
    "$SERVER_DIR/docker-compose.override.yml" 2>/dev/null | head -n 1)
REST_PORT="${REST_PORT:-8082}"

mkdir -p "$LOGDIR"
LOGFILE="$LOGDIR/launcher-$(date +%Y%m%d-%H%M%S).log"

# Keep the pretty output clean; everything noisy goes to the log.
exec 2>>"$LOGFILE"

dml_init "WoW Forever Offline" "$LOGFILE"
dml_header

# No -f: compose auto-loads docker-compose.yml + docker-compose.override.yml and
# reads the project name from .env, exactly as Yu'lon runs it.
compose() { ( cd "$SERVER_DIR" && docker compose "$@" ); }

running() { [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = "true" ]; }

# ── 1. Preflight ──────────────────────────────────────────────────────────────
[ -f "$SERVER_DIR/docker-compose.yml" ] || dml_die "WoW Forever is not installed in Yu'lon." \
    "Expected: $SERVER_DIR/docker-compose.yml. Install WoW Forever in Yu'lon first."
[ -f "$SERVER_DIR/certs/ca.pem" ] || dml_die "The server's certificate is missing." \
    "Expected: $SERVER_DIR/certs/ca.pem. Start WoW Forever once in Yu'lon."
command -v docker >/dev/null 2>&1 || dml_die "docker not found on PATH."
docker info >/dev/null 2>&1      || dml_die "Docker is not responding." "Is the daemon running?"
IMAGE=$(compose config --images 2>>"$LOGFILE" | head -n 1)
[ -n "$IMAGE" ] || dml_die "Could not read the server's compose project." "Folder: $SERVER_DIR"
docker image inspect "$IMAGE" >/dev/null 2>&1 || dml_die "The server image is missing." \
    "Reinstall WoW Forever in Yu'lon; this launcher never downloads or builds."
getent hosts "$PORTAL" | grep -q '^127\.0\.0\.1' || dml_die "The login server's name is not on this Deck yet." \
    "In Konsole run once:  echo '127.0.0.1 $PORTAL' | sudo tee -a /etc/hosts"
dml_ok "Docker ready"

# ── 2. Start the server ───────────────────────────────────────────────────────
START_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
FRESH_START=1
if running "$WORLD"; then
    FRESH_START=0
    dml_ok "Server already running"
else
    # CoA, Turtle, Unbound and the Lab's other WoW servers use the world port 8085 too.
    for p in 8085 1119 "$REST_PORT"; do
        if ss -Htln "sport = :$p" 2>/dev/null | grep . >/dev/null; then
            dml_die "Port $p is already in use." \
                "Another WoW server is probably running. Stop it first, then try again."
        fi
    done
    if compose up -d >>"$LOGFILE" 2>&1; then
        dml_ok "Database, world and login server starting"
    else
        dml_die "Failed to start the server." "Log: $LOGFILE"
    fi
fi

# ── 3. Prove it is ready ──────────────────────────────────────────────────────
world_ready() {
    running "$WORLD" || return 1
    [ "$FRESH_START" = 0 ] && return 0
    local logs
    logs=$(docker logs --since "$START_TS" "$WORLD" 2>&1) || return 1
    # Bash pattern matches, not `| grep -q`: under pipefail grep -q's early
    # exit SIGPIPEs `docker logs` and reads as "not found" (the Lab's oldest bug).
    [[ "$logs" == *"(worldserver-daemon) ready"* ]]
}
world_gone() { ! running "$WORLD"; }
world_ready_or_gone() { world_ready || world_gone; }

login_ready() {
    running "$BNET" && curl -sf -m 4 -o /dev/null --cacert "$SERVER_DIR/certs/ca.pem" \
        "https://$PORTAL:$REST_PORT/bnetserver/login/"
}

dml_phase "Loading the world..."
dml_note "A minute or two; the first start after an install or update takes about 16 minutes"
dml_wait_until 360 5 world_ready_or_gone \
    || dml_die "The world never finished loading (30 minutes)." "Log: docker logs --tail 60 $WORLD"
if world_gone; then
    docker logs --tail 40 "$WORLD" >>"$LOGFILE" 2>&1
    dml_die "The world server stopped while loading." "Its last output is in $LOGFILE"
fi
dml_ok "World loaded"
# bnetserver restarts itself until the world has built the auth tables.
dml_wait_until 60 5 login_ready || dml_die "The login server did not come up." \
    "Log: docker logs --tail 40 $BNET"
dml_ok "Login server up, realm online"

# ── 4. Stop (called when the game closes) ─────────────────────────────────────
stop_server() {
    echo ""
    dml_note "Stopping the server (the world saves everyone first)..."
    compose stop >>"$LOGFILE" 2>&1
    if running "$WORLD" || running "$BNET"; then
        dml_warn "The server did not stop cleanly — it was left running"
        dml_note "Run the \"WoW Forever Offline Server Stop\" shortcut, or press Stop in Yu'lon."
    else
        dml_ok "Server stopped. Your characters are saved."
    fi
    dml_footer "Server stopped! Safe to close."
}

# ── 5. Ready, then watch the game ─────────────────────────────────────────────
dml_ready "WOW FOREVER OFFLINE IS READY!"
echo "  ----------------------------------------------"
echo "  How to log in"
echo ""
echo "     Account        mmodad"
echo "     Password       pass"
echo ""
echo "     If a login fails, press Login again. Don't close the game."
echo "     Keyboard in Gaming Mode: Steam + X"
echo ""
dml_note "Bots appear a few minutes after you log in."
dml_note "Now launch \"WoW Forever Offline\" from your library."
echo ""
# 15 minutes: the first launch after Play sets up the game's Proton prefix takes a while.
dml_await_and_track "$CLIENT_PROC" "WoW Forever Offline" stop_server 15
