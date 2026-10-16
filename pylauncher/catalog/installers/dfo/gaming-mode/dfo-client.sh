#!/bin/bash
# Dad's MMO Lab — Dungeon Fighter Online (115US) game client, under Proton 11, for the
# server Yu'lon installed. Adapted from the Deck's own ~/dfo115-client.sh (Deck #2).
#
# The game runs INSIDE the private network the server made (dfo-launcher.sh, or Start on
# Yu'lon's DFO tab): it joins that namespace with nsenter, so 127.0.0.1:7001 is our server
# and there is no route to the internet. Start the server first; this script says so if not.
# Yu'lon's desktop Play on Linux runs this same script.
#
# The client folder and the account are Yu'lon's (state.json / ui.json), and so is the
# Proton prefix (proton/dfo-<12 hex of sha256(server folder)>), shared with Play.
#
#   DFO_ACCOUNT=<name>   account the game signs in as
#   DFO_CLIENT_DIR / DFO_SERVER_DIR   the client and server folders
#   (Play on Yu'lon's tab passes YULON_ACCOUNT, YULON_CLIENT_DIR and YULON_SERVER_DIR:
#   what the tab shows, rather than what state.json last saved.)
#   DFO_PROTON / DFO_SLR / DFO_PREFIX   override Proton, the runtime, or the prefix
export DML_STEAM_LD_PRELOAD="${DML_STEAM_LD_PRELOAD-${LD_PRELOAD-}}"
if [ -n "${LD_PRELOAD-}" ]; then
    unset LD_PRELOAD
    [ -f "$0" ] && exec "${BASH:-/bin/bash}" "$0" "$@"
fi
unset LD_LIBRARY_PATH
export PATH="/usr/bin:/usr/local/bin:/bin:$PATH"

YULON_DATA="@CONFIG_DIR@"
STEAM=$HOME/.steam/steam

fail() {   # visible both in a terminal and from a Gaming Mode tile
    echo "$1" >&2; [ -n "${2:-}" ] && echo "$2" >&2
    if [ ! -t 2 ] && command -v zenity >/dev/null && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
        zenity --error --title="Dungeon Fighter Online" --width=440 --timeout=30 \
               --text="$1${2:+

$2}" >/dev/null 2>&1
    fi
    exit 1
}

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
RUN=$D/run
LOGDIR=$D/logs
PFX_ID=$(printf '%s' "$D" | sha256sum | cut -c1-12)
PREFIX=${DFO_PREFIX:-$YULON_DATA/proton/dfo-$PFX_ID}

# Every Steam library folder, the way Steam resolves them: its own steamapps first, then
# each "path" in libraryfolders.vdf (the SD card's included).
steam_libraries() {
    python3 -I - "$STEAM" "$HOME/.local/share/Steam" <<'PY' 2>/dev/null
import os, re, sys
seen = []
for root in sys.argv[1:]:
    for lib in [root] + re.findall(r'"path"\s+"([^"]+)"', open(os.path.join(root, "steamapps", "libraryfolders.vdf"), encoding="utf-8", errors="replace").read() if os.path.isfile(os.path.join(root, "steamapps", "libraryfolders.vdf")) else ""):
        apps = os.path.realpath(os.path.join(lib, "steamapps"))
        if os.path.isdir(apps) and apps not in seen:
            seen.append(apps)
print("\n".join(seen))
PY
}
# Proton 11 and the runtime it requires (SteamLinuxRuntime_4) are Steam downloads;
# they may sit in any Steam library folder.
find_steam_tool() {
    local name="$1" lib
    while IFS= read -r lib; do
        [ -n "$lib" ] && [ -d "$lib/common/$name" ] && { printf '%s\n' "$lib/common/$name"; return 0; }
    done < <(steam_libraries)
    return 1
}

if [ -z "$CLIENT" ] || [ ! -f "$CLIENT/DFO.exe" ]; then
    fail "Cannot find the Dungeon Fighter Online client." \
        "Choose your client folder on Yu'lon's DFO tab (the one with DFO.exe in it)."
fi
CLIENT=$(readlink -f "$CLIENT")
LIB_ROOT=$(dirname "$(dirname "$CLIENT")")

PROTON=${DFO_PROTON:-$(find_steam_tool "Proton 11.0")} || true
SLR=${DFO_SLR:-$(find_steam_tool "SteamLinuxRuntime_4")} || true
[ -x "$PROTON/proton" ] || fail "Proton 11.0 is not installed." \
    "In Steam: Library > search 'Proton 11.0' > Install. Then launch again."
[ -x "$SLR/_v2-entry-point" ] || fail "Steam Linux Runtime 4.0 is not installed." \
    "In Steam: Library > search 'Steam Linux Runtime 4.0' > Install. Then launch again."

# Alive AND not a zombie: a process that has exited but is not reaped yet (Yu'lon's own
# child, when Yu'lon started it) keeps its /proc entry and its old name for a while.
live() { local s; s=$(sed -n 's/^.*) \([A-Za-z]\).*/\1/p' "/proc/$1/stat" 2>/dev/null)
         [ -n "$s" ] && [ "$s" != Z ] && [ "$s" != X ]; }
# The server's private network. Same identity checks as the launcher: alive, still a
# `sleep`, and really in another network namespace.
HP=$({ tr -dc 0-9 < "$RUN/holder.pid"; } 2>/dev/null)
HN=$(readlink "/proc/$HP/ns/net" 2>/dev/null)
if [ -z "$HP" ] || ! live "$HP" || [ "$(cat "/proc/$HP/comm" 2>/dev/null)" != sleep ] \
   || [ -z "$HN" ] || [ "$HN" = "$(readlink /proc/self/ns/net)" ]; then
    fail "The server is not running." \
         "Launch 'Dungeon Fighter Online Server' (or Start on Yu'lon's DFO tab) first, wait for READY, then start the game."
fi
if ! grep -q '^Server listening on' "$(cat "$RUN/server.logpath" 2>/dev/null)" 2>/dev/null; then
    fail "The server is still starting." "Wait for READY, then launch again."
fi

mkdir -p "$LOGDIR" "$PREFIX"
export STEAM_COMPAT_CLIENT_INSTALL_PATH="$STEAM"
export STEAM_COMPAT_DATA_PATH="$PREFIX"
export STEAM_COMPAT_INSTALL_PATH="$CLIENT"
export STEAM_COMPAT_LIBRARY_PATHS="$LIB_ROOT"

# The game's login payload, exactly as upstream's launcher builds it (clientrun.go):
# mode 3, gateway 127.0.0.1:7001, account name, an all-zero token, fixed tail.
PAYLOAD="3?127.0.0.1?7001?$ACCOUNT?00000000000000000000000000000000?0?0?30?0?0?0"

cd "$CLIENT" || fail "Cannot enter $CLIENT"
# nsenter + unshare both exec in place, so Steam keeps tracking this pid. The overlay
# goes back on for the game itself (env), not for nsenter/unshare.
exec nsenter -t "$HP" -U -n --preserve-credentials -- \
    unshare --user --map-user="$(id -u)" --map-group="$(id -g)" -- \
    env LD_PRELOAD="${DML_STEAM_LD_PRELOAD-}" \
    "$SLR/_v2-entry-point" --verb=waitforexitandrun -- \
    "$PROTON/proton" waitforexitandrun "$CLIENT/DFO.exe" "$PAYLOAD" \
    >"$LOGDIR/client-$(date +%Y%m%d-%H%M%S).log" 2>&1
