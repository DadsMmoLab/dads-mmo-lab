#!/bin/bash
# Dad's MMO Lab — Dragon's Dogma Online client v2.0.0 (Yu'lon install, Deck #2)
#
# The client half of the two-shortcut split. The server is its own shortcut
# ("Dragon's Dogma Online Server", ~/ddo-launcher.sh), which starts the stack,
# waits for THIS process (DDO.exe) to appear, and shuts the server down when it
# exits.
#
# v2.0.0: plays against the server Yu'lon installed. Client folder, account and
# Proton prefix are Yu'lon's own (state.json / ui.json / proton/<game>-<hash>),
# so Gaming Mode and Yu'lon's desktop Play share one account and one prefix.
#
# Load-bearing, do not "simplify":
#  1. Proton through the Steam Linux Runtime (sniper _v2-entry-point), the way
#     Steam itself runs games. DDO.exe is 32-bit and sniper supplies the 32-bit
#     stack; a bare `proton run` gives false verdicts (see ~/edeneternal-client.sh).
#  2. The prefix path must contain a digit: protonfixes takes the last number in it.
#     Yu'lon's prefix name (ddon-<12 hex of sha256(server folder)>) has digits.
#  3. The server address is the Deck's LAN IP, never localhost: the runtime's
#     sandbox has its own loopback. The launch string is ONE argument, exactly as
#     Yu'lon's catalog gives it; token= is the account name (AccountRequired=false
#     creates it the first time).
set -uo pipefail

# ── Steam overlay scrub (the Lab standard, see ~/dml-launcher-lib.sh) ─────────
# Steam starts every shortcut with its overlay shim in LD_PRELOAD; inherited by
# our helpers it floods the output. Drop it and re-exec once into a shell that
# never loaded it. The game gets it back on its own exec line below.
export DML_STEAM_LD_PRELOAD="${DML_STEAM_LD_PRELOAD-${LD_PRELOAD-}}"
if [ -n "${LD_PRELOAD-}" ]; then
    unset LD_PRELOAD
    if [ -f "$0" ]; then exec "${BASH:-/bin/bash}" "$0" "$@"; fi
fi
unset LD_LIBRARY_PATH
export PATH="/usr/bin:/usr/local/bin:/bin:$PATH"

YULON_DATA="$HOME/.local/share/yulon"

# One line per value: server_dir, client_dir, account. Empty where Yu'lon has none.
yulon_values() {
    python3 -I - "$YULON_DATA" <<'PY' 2>/dev/null
import json, sys
data = sys.argv[1]
server = client = account = ""
try:
    for i in json.load(open(data + "/state.json")).get("installs", []):
        if i.get("game") == "ddon":
            server, client = i.get("server_dir") or "", i.get("client_dir") or ""
            break
except Exception:
    pass
try:
    ui = json.load(open(data + "/ui.json")).get("launchers", {})
    account = (ui.get("ddon|" + server) or {}).get("account") or ""
except Exception:
    pass
print(server); print(client); print(account)
PY
}
mapfile -t YV < <(yulon_values)
SERVER_DIR="${DDO_SERVER_DIR:-${YV[0]:-}}"
SERVER_DIR="${SERVER_DIR:-@SERVER_DIR@}"
# Not "${DDO_CLIENT_ROOT:-...Dragon's...}": an apostrophe inside ${...:-} opens a quote.
CLIENT_ROOT="${DDO_CLIENT_ROOT:-${YV[1]:-}}"
[ -n "$CLIENT_ROOT" ] || CLIENT_ROOT="@HOME@/Games/Dragon's Dogma Online"
TOKEN="${DDO_TOKEN:-${YV[2]:-}}"
TOKEN="${TOKEN:-@ACCOUNT@}"
# Yu'lon's prefix for this server: <game>-<first 12 hex of sha256(server folder)>.
PFX_ID=$(printf '%s' "$SERVER_DIR" | sha256sum | cut -c1-12)
PREFIX="${DDO_PREFIX:-$YULON_DATA/proton/ddon-$PFX_ID}"
# The Proton that made the prefix, so the prefix is never opened by another one.
PROTON_NAME=$(cat "$PREFIX/version" 2>/dev/null)
PROTON_NAME="${PROTON_NAME:-@PROTON_NAME@}"
PROTON_DIR="${DDO_PROTON:-@HOME@/.local/share/Steam/compatibilitytools.d/$PROTON_NAME}"
SLR="${DDO_SLR:-@HOME@/.steam/steam/steamapps/common/SteamLinuxRuntime_sniper/_v2-entry-point}"
LOGDIR="${DDO_LOGDIR:-@HOME@/ddo-logs}"
LVER="03.04.003.20181115.0"
RVER="3040008"

mkdir -p "$LOGDIR" "$PREFIX"
LOGFILE="$LOGDIR/client-$(date +%Y%m%d-%H%M%S).log"

say()  { printf '  %s\n' "$*"; }
fail() {
    printf '  [ERROR] %s\n' "$1" >&2
    [ -n "${2:-}" ] && printf '  %s\n' "$2" >&2
    printf '%s  FAIL: %s %s\n' "$(date +%H:%M:%S)" "$1" "${2:-}" >>"$LOGFILE"
    # Launched from a Steam shortcut there is no terminal, so explain it in a
    # dialog rather than bouncing silently back to the library.
    if [ ! -t 1 ] && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v zenity >/dev/null; then
        zenity --error --title="Dragon's Dogma Online" --width=420 --timeout=20 \
               --text="$1

${2:-}" >/dev/null 2>&1 &
        wait $! 2>/dev/null
    fi
    sleep 8
    exit 1
}

IP="${DDO_IP:-$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") { print $(i + 1); exit }}')}"
[ -n "$IP" ] || fail "No network connection found." "Connect to Wi-Fi first."

[ -f "$CLIENT_ROOT/DDO.exe" ] || fail "Client not found." "Expected: $CLIENT_ROOT/DDO.exe"
[ -x "$PROTON_DIR/proton" ]   || fail "Proton not found." "Expected: $PROTON_DIR"
[ -x "$SLR" ]                 || fail "Steam Linux Runtime (sniper) not found." "Install it from Steam: it ships with any Proton game."
# Exact process name, never `pgrep -f <path>` (self-matches wrapper command lines).
if pgrep -x DDO.exe >/dev/null 2>&1; then fail "Dragon's Dogma Online is already running."; fi
# A real HTTP answer, not a bare connect: docker-proxy accepts connections even
# when the server is down, and the login server logs a probe as an exception.
curl -fsS -o /dev/null --max-time 3 "http://$IP:52099/win/versionlist" 2>/dev/null \
    || fail "The Dragon's Dogma Online server is not running." \
            "Launch \"Dragon's Dogma Online Server\" first and wait for DRAGON'S DOGMA ONLINE IS READY."

export STEAM_COMPAT_CLIENT_INSTALL_PATH="${STEAM_COMPAT_CLIENT_INSTALL_PATH:-@HOME@/.steam/steam}"
export STEAM_COMPAT_DATA_PATH="$PREFIX"
run_slr() { "$SLR" --verb=waitforexitandrun -- "$PROTON_DIR/proton" waitforexitandrun "$@"; }

ARGS="addr=$IP port=52100 token=$TOKEN DL=http://$IP:52099/win/ LVer=$LVER RVer=$RVER"
say "Launching Dragon's Dogma Online -> $IP (account: $TOKEN)"
say "Log: $LOGFILE"
printf '%s  prefix=%s proton=%s client=%s\n' "$(date +%H:%M:%S)" "$PREFIX" "$PROTON_NAME" "$CLIENT_ROOT" >>"$LOGFILE"
cd "$CLIENT_ROOT" || fail "Cannot enter $CLIENT_ROOT"
# Hand the Steam overlay back to the game itself (scrubbed at the top).
LD_PRELOAD="${DML_STEAM_LD_PRELOAD-}" run_slr "$CLIENT_ROOT/DDO.exe" "$ARGS" >>"$LOGFILE" 2>&1
EXIT=$?
say "Client exited with code $EXIT"
exit "$EXIT"
