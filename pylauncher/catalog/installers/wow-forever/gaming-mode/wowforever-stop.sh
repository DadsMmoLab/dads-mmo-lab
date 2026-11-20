#!/bin/bash
# Dad's MMO Lab — WoW Forever Offline Server Stop v1.0.0 (Yu'lon install)
#
# "WoW Forever Offline Server Stop": a clean stop of the server Yu'lon installed.
# The world gets its compose stop_grace_period (120 s) to save every player and
# bot, then the login server and the database. Refused while the game is running,
# as on Steam Deck #1: log out first, and the server tile stops it by itself.
#   --from-launcher   called by the server tile's watch: no dialogs
set -uo pipefail
export DML_STEAM_LD_PRELOAD="${DML_STEAM_LD_PRELOAD-${LD_PRELOAD-}}"
if [ -n "${LD_PRELOAD-}" ]; then
    unset LD_PRELOAD
    if [ -f "$0" ]; then exec "${BASH:-/bin/bash}" "$0" "$@"; fi
fi
unset LD_LIBRARY_PATH
export PATH="/usr/bin:/usr/local/bin:/bin:$PATH"

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
LOGDIR="${WOWFO_LOGDIR:-@HOME@/wowforever-logs}"
FROM_LAUNCHER=0
[ "${1:-}" = "--from-launcher" ] && FROM_LAUNCHER=1

mkdir -p "$LOGDIR"
LOGFILE="$LOGDIR/stop-$(date +%Y%m%d-%H%M%S).log"

report() {   # report <info|error|warning> <message>
    printf '  %s\n' "$2"
    printf '%s  %s: %s\n' "$(date +%H:%M:%S)" "$1" "$2" >>"$LOGFILE"
    if [ "$FROM_LAUNCHER" = 0 ] && [ ! -t 1 ] && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] \
        && command -v zenity >/dev/null; then
        zenity "--$1" --title="WoW Forever Offline" --width=380 --timeout=10 \
               --text="$2" >/dev/null 2>&1 &
        wait $! 2>/dev/null
    fi
    return 0
}

running() { [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = "true" ]; }
ours() { running wowforever-world || running wowforever-bnet || running wowforever-database; }

if pgrep -x Wow.exe >/dev/null 2>&1; then
    report warning "The game is still running. Log out and close it first: the server then stops by itself."
    exit 1
fi
if ! ours; then
    report info "The WoW Forever Offline server is already stopped."
    exit 0
fi
[ -f "$SERVER_DIR/docker-compose.yml" ] || {
    report error "Could not find the server's folder ($SERVER_DIR). Stop it in Yu'lon instead."
    exit 1
}
( cd "$SERVER_DIR" && docker compose stop ) >>"$LOGFILE" 2>&1
if ours; then
    report error "A WoW Forever container is still running. Press Stop in Yu'lon, or: docker compose stop in $SERVER_DIR"
    exit 1
fi
report info "WoW Forever Offline server stopped. Your characters are saved."
exit 0
