#!/bin/bash
# Dad's MMO Lab — Dragon's Dogma Online server stop v2.0.0 (Yu'lon install, Deck #2)
#
# The explicit "I'm done" button, wired to its own Gaming Mode shortcut. The
# launcher already stops the server when the game closes; this covers the cases
# where it didn't (launcher window closed early, client never started, etc.).
# Closes the game first if it is still open, then stops the container. The
# character data lives in the SQLite volume and survives this.
#
# v2.0.0: stops the server Yu'lon installed (~/yulon-ddon compose project).
#
# Gaming Mode has no terminal, so the result is reported in a dialog too.
set -uo pipefail

# ── Steam overlay scrub (the Lab standard, see ~/dml-launcher-lib.sh) ─────────
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
    if i.get("game") == "ddon" and i.get("server_dir"):
        print(i["server_dir"]); break
PY
}
SERVER_DIR="${DDO_SERVER_DIR:-$(yulon_server_dir)}"
SERVER_DIR="${SERVER_DIR:-@SERVER_DIR@}"
CONTAINER="ddon-server"
LOGDIR="${DDO_LOGDIR:-@HOME@/ddo-logs}"
mkdir -p "$LOGDIR"
LOGFILE="$LOGDIR/stop-$(date +%Y%m%d-%H%M%S).log"

report() {   # report <info|error> <message>
    printf '  %s\n' "$2"
    printf '%s  %s: %s\n' "$(date +%H:%M:%S)" "$1" "$2" >>"$LOGFILE"
    if [ ! -t 1 ] && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v zenity >/dev/null; then
        zenity "--$1" --title="Dragon's Dogma Online" --width=380 --timeout=10 \
               --text="$2" >/dev/null 2>&1 &
        wait $! 2>/dev/null
    fi
    # zenity returns 5 when its --timeout fires; that is not our result.
    return 0
}

running() { [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" = "true" ]; }

if pgrep -x DDO.exe >/dev/null 2>&1; then
    pkill -x DDO.exe
    for _ in $(seq 1 20); do pgrep -x DDO.exe >/dev/null 2>&1 || break; sleep 1; done
fi

if ! running; then
    report info "The Dragon's Dogma Online server is already stopped."
    exit 0
fi

( cd "$SERVER_DIR" && docker compose stop ) >>"$LOGFILE" 2>&1
if running; then
    report error "The server did not stop. Log: $LOGFILE"
    exit 1
fi
report info "Dragon's Dogma Online server stopped. Your character is saved."
exit 0
