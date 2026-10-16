#!/bin/bash
# Dad's MMO Lab — Dungeon Fighter Online (115US): clean shutdown, for the server Yu'lon
# installed. Adapted from the Deck's own ~/dfo115-stop.sh (Deck #2).
#
#   dfo-stop.sh                   manual stop (refuses while the game is open)
#   dfo-stop.sh --from-launcher   the launcher's automatic stop after the game closed
#   dfo-stop.sh --quiet           tidy up a session that was never stopped (used by the
#                                 launcher before it starts), prints nothing
#
# Order: the game's leftover Wine processes -> the server -> its database lock -> the
# private network's holder. Stop on Yu'lon's DFO tab does the same, in the same order,
# on the same records (run/ and .yulon-process.json), so either may stop what the other
# started.
export DML_STEAM_LD_PRELOAD="${DML_STEAM_LD_PRELOAD-${LD_PRELOAD-}}"
if [ -n "${LD_PRELOAD-}" ]; then
    unset LD_PRELOAD
    [ -f "$0" ] && exec "${BASH:-/bin/bash}" "$0" "$@"
fi
unset LD_LIBRARY_PATH
export PATH="/usr/bin:/usr/local/bin:/bin:$PATH"

YULON_DATA="@CONFIG_DIR@"
STEAM=$HOME/.steam/steam

server_dir() {
    python3 -I - "$YULON_DATA" <<'PY' 2>/dev/null
import json, sys
try:
    for i in json.load(open(sys.argv[1] + "/state.json")).get("installs", []):
        if i.get("game") == "dfo":
            print(i.get("server_dir") or "")
            break
except Exception:
    pass
PY
}
D="${DFO_SERVER_DIR:-${YULON_SERVER_DIR:-$(server_dir)}}"
D="${D:-@SERVER_DIR@}"
RUN=$D/run
RECORD=$D/.yulon-process.json
DB=$D/src/server/work/dfo-lan/runtime/storage/dfolan.sqlite3
GUARD=$DB.admin-guard
PFX_ID=$(printf '%s' "$D" | sha256sum | cut -c1-12)
PREFIX=${DFO_PREFIX:-$YULON_DATA/proton/dfo-$PFX_ID}

MODE=manual
case "${1-}" in
    --from-launcher) MODE=launcher ;;
    --quiet)         MODE=quiet ;;
esac

say() {
    case "$MODE" in
        quiet)    : ;;
        launcher) echo "  $*" ;;
        *)        if command -v zenity >/dev/null && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && [ ! -t 1 ]; then
                      zenity --info --no-wrap --timeout=20 --title="Dungeon Fighter Online" --text="$*" 2>/dev/null
                  else echo "$*"; fi ;;
    esac
}

# Alive AND not a zombie: a process that has exited but is not reaped yet (Yu'lon's own
# child, when Yu'lon started it) keeps its /proc entry and its old name for a while.
live() { local s; s=$(sed -n 's/^.*) \([A-Za-z]\).*/\1/p' "/proc/$1/stat" 2>/dev/null)
         [ -n "$s" ] && [ "$s" != Z ] && [ "$s" != X ]; }
# Same identity rule as the launcher: alive AND still the same program.
pid_is() { local p; p=$({ tr -dc 0-9 < "$1"; } 2>/dev/null)
           [ -n "$p" ] && live "$p" && [ "$(cat "/proc/$p/comm" 2>/dev/null)" = "$2" ] && echo "$p"; }
HP=$(pid_is "$RUN/holder.pid" sleep)
NET=""
[ -n "$HP" ] && NET=$(readlink "/proc/$HP/ns/net" 2>/dev/null)
if [ -z "$NET" ] || [ "$NET" = "$(readlink /proc/self/ns/net)" ]; then HP=""; NET=""; fi

# Every process in the private network but its holder: the server, its gateway, and the
# game with its Wine helpers. Nothing outside the network is ever signalled.
in_network() {
    [ -n "$NET" ] || return 0
    local p
    for p in $(ls /proc | grep -E '^[0-9]+$'); do
        [ "$p" != "$HP" ] && [ "$(readlink "/proc/$p/ns/net" 2>/dev/null)" = "$NET" ] && echo "$p"
    done
}
named_in_network() {   # named_in_network <comm>...
    local p c n
    for p in $(in_network); do
        c=$(cat "/proc/$p/comm" 2>/dev/null)
        for n in "$@"; do [ "$c" = "$n" ] && { echo "$p"; break; }; done
    done
}

# The game: DFO.exe at first, CrBrowserMain once its embedded Chromium renames it.
game_running() { [ -n "$(named_in_network DFO.exe CrBrowserMain)" ]; }

if [ "$MODE" = manual ] && game_running; then
    say "Dungeon Fighter Online is still running — quit the game first."
    exit 1
fi

# Was there anything to stop? (Only changes the closing message.)
WAS=""
[ -n "$(pid_is "$RUN/server.pid" dfolauncher.exe)$HP" ] && WAS=1

# Every Steam library folder, the way Steam resolves them (the SD card's included).
steam_libraries() {
    python3 -I - "$STEAM" "$HOME/.local/share/Steam" <<'PY' 2>/dev/null
import os, re, sys
seen = []
for root in sys.argv[1:]:
    vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
    text = open(vdf, encoding="utf-8", errors="replace").read() if os.path.isfile(vdf) else ""
    for lib in [root] + re.findall(r'"path"\s+"([^"]+)"', text):
        apps = os.path.realpath(os.path.join(lib, "steamapps"))
        if os.path.isdir(apps) and apps not in seen:
            seen.append(apps)
print("\n".join(seen))
PY
}

# 1. The game's helpers (CEF.exe and friends) can outlive DFO.exe. Only this game's
#    prefix is touched, and only once the game itself is gone.
if ! game_running && [ -d "$PREFIX/pfx" ]; then
    while IFS= read -r lib; do
        ws="$lib/common/Proton 11.0/files/bin/wineserver"
        [ -x "$ws" ] && { WINEPREFIX="$PREFIX/pfx" "$ws" -k 2>/dev/null; break; }
    done < <(steam_libraries)
fi

# 2. The server. It has no shutdown handler of its own (upstream stops it with
#    taskkill /F); its saves are committed to SQLite as you play, and the WAL keeps
#    every committed write across a hard stop. SIGINT to the whole process group
#    takes the launcher and its gateway together.
SP=$(pid_is "$RUN/server.pid" dfolauncher.exe)
if [ -n "$SP" ]; then
    kill -INT -- "-$SP" 2>/dev/null || kill -INT "$SP" 2>/dev/null
    for _ in $(seq 1 30); do
        live "$SP" || break
        sleep 0.5
    done
    live "$SP" && kill -TERM -- "-$SP" 2>/dev/null
    sleep 1
fi
# The gateway is the process that holds the database (its name is cut to 15 characters),
# and Wine's helpers may linger: whatever is left in the network goes too.
for p in $(in_network); do [ -n "$NET" ] && kill -TERM "$p" 2>/dev/null; done
for _ in $(seq 1 20); do
    [ -z "$NET" ] || [ -z "$(in_network)" ] && break
    sleep 0.5
done

# 3. The database lock. Upstream's rule: delete it only when the pid written in it is
#    provably gone; otherwise leave it (it expires by itself 60 s after its last refresh).
if [ -e "$GUARD" ]; then
    gp=$({ tr -dc 0-9 < "$GUARD"; } 2>/dev/null)
    if [ -n "$gp" ] && ! live "$gp"; then rm -f "$GUARD"; fi
fi

# 4. The private network: kill its holder; the namespace disappears with its last process.
[ -n "$HP" ] && kill "$HP" 2>/dev/null

# 5. Confirm.
sleep 0.5
left=""
[ -n "$(pid_is "$RUN/server.pid" dfolauncher.exe)" ] && left="$left server"
[ -n "$NET" ] && [ -n "$(named_in_network wireprobe-pvf.e)" ] && left="$left gateway"
[ -n "$(pid_is "$RUN/holder.pid" sleep)" ] && left="$left network"
if [ -n "$left" ]; then
    say "Could not stop:$left. Try again in a moment, or Stop on Yu'lon's DFO tab."
    exit 1
fi
rm -f "$RUN/holder.pid" "$RUN/server.pid" "$RECORD"
if [ "$MODE" != launcher ]; then
    if [ -n "$WAS" ]; then say "Dungeon Fighter Online server stopped."
    else say "The Dungeon Fighter Online server is not running."; fi
fi
exit 0
