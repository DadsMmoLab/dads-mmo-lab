#!/bin/bash
# Dad's MMO Lab — WoW Forever Offline client v1.0.0 (Yu'lon install)
#
# The client half of the two-shortcut split. The server is its own shortcut
# ("WoW Forever Offline Server", wowforever-launcher.sh), which starts the stack,
# waits for THIS game (Wow.exe) to appear, and shuts the server down when it exits.
#
# Plays against the server Yu'lon installed, with what Yu'lon's desktop Play set up
# (forever_play / forever_proton): the client folder and server folder from
# state.json, the game's own prefix <server>/wowforever-offline-pfx-1 (its CA trusted
# inside), GE-Proton11-12 in Steam's compatibilitytools.d, the account and
# _forever_/WTF/ForeverLauncher.wtf. So press Play once in Yu'lon on the desktop
# before using this tile.
#
# Load-bearing, do not "simplify" (Steam Deck #1, kit v5):
#  1. Proton through Steam Linux Runtime 4.0 (_v2-entry-point); a bare `proton run`
#     gives false verdicts.
#  2. The prefix folder name has digits (protonfixes).
#  3. The login key: the 70888 client builds its copies of the 12 region keys only
#     when Login is pressed, and the server signs its identity 1.5 s after the world
#     connection opens. The watch below (Yu'lon's forever_keywatch, standard library
#     only) runs beside the game from Linux: /proc/<pid>/mem, the pages the kernel
#     says changed (PAGEMAP_SCAN, soft-dirty) every 0.15 s, every copy of the 12 keys
#     replaced with the server's. On Deck #1 the first Login press gets in.
#  4. Gaming Mode only: PROTON_USE_XALIA=0. xalia.exe's window held focus at the top
#     left on 2026-11-11.
set -uo pipefail

# ── Steam overlay scrub (the Lab standard, see ~/dml-launcher-lib.sh) ─────────
export DML_STEAM_LD_PRELOAD="${DML_STEAM_LD_PRELOAD-${LD_PRELOAD-}}"
if [ -n "${LD_PRELOAD-}" ]; then
    unset LD_PRELOAD
    if [ -f "$0" ]; then exec "${BASH:-/bin/bash}" "$0" "$@"; fi
fi
unset LD_LIBRARY_PATH
export PATH="/usr/bin:/usr/local/bin:/bin:$PATH"

YULON_DATA="$HOME/.local/share/yulon"

# One line per value: server_dir, client_dir. Empty where Yu'lon has none.
yulon_values() {
    python3 -I - "$YULON_DATA" <<'PY' 2>/dev/null
import json, sys
server = client = ""
try:
    for i in json.load(open(sys.argv[1] + "/state.json")).get("installs", []):
        if i.get("game") == "wow-forever":
            server, client = i.get("server_dir") or "", i.get("client_dir") or ""
            break
except Exception:
    pass
print(server); print(client)
PY
}
mapfile -t YV < <(yulon_values)
SERVER_DIR="${WOWFO_SERVER_DIR:-${YV[0]:-}}"
SERVER_DIR="${SERVER_DIR:-@SERVER_DIR@}"
CLIENT_ROOT="${WOWFO_CLIENT:-${YV[1]:-}}"
EXE="$CLIENT_ROOT/_forever_/Wow.exe"
PREFIX="${WOWFO_PREFIX:-$SERVER_DIR/wowforever-offline-pfx-1}"
PORTAL="trinity.actual.battle.net"
LOGDIR="${WOWFO_LOGDIR:-@HOME@/wowforever-logs}"
REST_PORT=$(sed -n 's/^ *REST_PORT: *"\{0,1\}\([0-9][0-9]*\)"\{0,1\} *$/\1/p' \
    "$SERVER_DIR/docker-compose.override.yml" 2>/dev/null | head -n 1)
REST_PORT="${REST_PORT:-8082}"

first_dir() { local d; for d in "$@"; do [ -d "$d" ] && { printf '%s\n' "$d"; return 0; }; done; return 1; }
STEAM_ROOT=$(first_dir "@HOME@/.local/share/Steam" "@HOME@/.steam/steam") || STEAM_ROOT="@HOME@/.local/share/Steam"
PROTON_DIR="${WOWFO_PROTON:-$(first_dir "@HOME@/.local/share/Steam/compatibilitytools.d/GE-Proton11-12-x86_64" \
    "@HOME@/.steam/steam/compatibilitytools.d/GE-Proton11-12-x86_64")}"
find_slr() {   # the runtime may sit in any Steam library, an SD card's included
    local lib
    for lib in "$STEAM_ROOT/steamapps" /run/media/*/*/steamapps /run/media/*/steamapps; do
        [ -x "$lib/common/SteamLinuxRuntime_4/_v2-entry-point" ] \
            && { printf '%s\n' "$lib/common/SteamLinuxRuntime_4/_v2-entry-point"; return 0; }
    done
    return 1
}
SLR="${WOWFO_SLR:-$(find_slr)}"

mkdir -p "$LOGDIR"
LOGFILE="$LOGDIR/client-$(date +%Y%m%d-%H%M%S).log"

say()  { printf '  %s\n' "$*"; }
fail() {
    printf '  [ERROR] %s\n' "$1" >&2
    [ -n "${2:-}" ] && printf '  %s\n' "$2" >&2
    printf '%s  FAIL: %s %s\n' "$(date +%H:%M:%S)" "$1" "${2:-}" >>"$LOGFILE"
    # Launched from a Steam shortcut there is no terminal, so explain it in a
    # dialog rather than bouncing silently back to the library.
    if [ ! -t 1 ] && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v zenity >/dev/null; then
        zenity --error --title="WoW Forever Offline" --width=420 --timeout=20 \
               --text="$1

${2:-}" >/dev/null 2>&1 &
        wait $! 2>/dev/null
    fi
    sleep 8
    exit 1
}

FIRST_PLAY="Press Play on WoW Forever's tab in Yu'lon once, on the desktop: it sets up GE-Proton, the game's prefix and your account. Then use this tile."
[ -n "$CLIENT_ROOT" ] && [ -f "$EXE" ] || fail "The WoW Forever client was not found." \
    "Expected: ${EXE}. $FIRST_PLAY"
[ -d "$PREFIX/pfx/drive_c/windows" ] || fail "The game's Proton prefix does not exist yet." "$FIRST_PLAY"
[ -f "$CLIENT_ROOT/_forever_/WTF/ForeverLauncher.wtf" ] || fail "The game's login settings are missing." "$FIRST_PLAY"
[ -n "$PROTON_DIR" ] && [ -x "$PROTON_DIR/proton" ] || fail "GE-Proton11-12 was not found." "$FIRST_PLAY"
[ -n "$SLR" ] || fail "Steam Linux Runtime 4.0 is not installed." \
    "In Steam: Library, search for \"Steam Linux Runtime 4.0\", Install. Then launch again."
getent hosts "$PORTAL" | grep -q '^127\.0\.0\.1' || fail "The login server's name is not on this Deck yet." \
    "In Konsole run once:  echo '127.0.0.1 $PORTAL' | sudo tee -a /etc/hosts"
# Exact process name, never `pgrep -f <path>` (self-matches wrapper command lines), and
# scoped to THIS client folder (T888): another install's game on the same Deck is not
# this tile's, and a global pgrep refused or skipped the wrong copy. The pids printed here
# are the ones already running BEFORE this launch; the key watch below gets them and
# attaches only to a pid that is not among them.
client_pids() {
    python3 -I - "$CLIENT_ROOT" <<'PYEOF' 2>/dev/null || true
import os, re, sys
client = os.path.realpath(sys.argv[1])
game = re.compile(rb"\\_forever_\\wowb?\.exe(?: |$)", re.I)
for d in os.listdir("/proc"):
    if not d.isdigit():
        continue
    try:
        if os.stat("/proc/" + d).st_uid != os.getuid():
            continue
        argv0 = open("/proc/%s/cmdline" % d, "rb").read().split(b"\0", 1)[0]
    except OSError:
        continue
    text = argv0.decode("utf-8", "replace").strip().strip('"')
    if len(text) < 2 or text[1] != ":" or text[0].upper() != "Z":
        continue
    path = os.path.realpath("/" + text[2:].replace("\\", "/").lstrip("/"))
    if game.search(argv0) and (path == client or path.startswith(client.rstrip("/") + "/")):
        print(d)
PYEOF
}
BEFORE="$(client_pids | tr '\n' ' ')"
case "$BEFORE" in *[0-9]*) fail "WoW Forever is already running." "Close it, then launch again.";; esac
# A real TLS answer, not a bare connect: docker-proxy accepts connections even when
# the server is down.
curl -sf -m 4 -o /dev/null --cacert "$SERVER_DIR/certs/ca.pem" "https://$PORTAL:$REST_PORT/bnetserver/login/" \
    || fail "The WoW Forever Offline server is not running." \
            "Launch \"WoW Forever Offline Server\" first and wait for WOW FOREVER OFFLINE IS READY."

export STEAM_COMPAT_CLIENT_INSTALL_PATH="$STEAM_ROOT"
export STEAM_COMPAT_DATA_PATH="$PREFIX"
export STEAM_COMPAT_INSTALL_PATH="$CLIENT_ROOT"
export STEAM_COMPAT_LIBRARY_PATHS="$STEAM_ROOT:$CLIENT_ROOT:$SERVER_DIR"
export STEAM_COMPAT_MOUNTS="$CLIENT_ROOT:$SERVER_DIR"
export WINE_SIMULATE_WRITECOPY=1
if [ -n "${GAMESCOPE_WAYLAND_DISPLAY:-}" ] || [ "${XDG_CURRENT_DESKTOP:-}" = gamescope ]; then
    [ "${WOWFO_XALIA:-0}" = 1 ] || export PROTON_USE_XALIA=0
fi
run_slr() { "$SLR" --verb=waitforexitandrun -- "$PROTON_DIR/proton" waitforexitandrun "$@"; }

# ── The prefix's trust follows the CA (T888) ──────────────────────────────────
# A desktop Play remakes the server's certificate when it has to, and a remade CA is a
# root this prefix does not have: the game's TLS check would fail on the first login.
# The thumbprint the prefix last imported is recorded in it (by Play, or by the import
# below); a different one on certs/ca.pem means the new CA has to go in before the game
# starts. The import script deletes every superseded CA of ours from the prefix's own
# store first, then adds this one.
CA_PEM="$SERVER_DIR/certs/ca.pem"
CA_THUMB=$(python3 -I - "$CA_PEM" <<'PYEOF' 2>/dev/null || true
import base64, hashlib, re, sys
try:
    text = open(sys.argv[1], encoding="ascii", errors="replace").read()
except OSError:
    raise SystemExit
m = re.search(r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", text, re.S)
if m:
    print(hashlib.sha1(base64.b64decode("".join(m.group(1).split()))).hexdigest().upper())
PYEOF
)
PREFIX_PY="$PREFIX/pfx/drive_c/python313/python.exe"
winpath() { printf 'Z:%s' "$1" | sed 's|/|\\|g'; }
if [ -n "$CA_THUMB" ] && [ "$CA_THUMB" != "$(cat "$PREFIX/.yulon-ca-thumbprint" 2>/dev/null || true)" ] \
    && [ -f "$PREFIX/import_ca.py" ] && [ -f "$PREFIX_PY" ]; then
    say "The game's prefix is taking the server's certificate..."
    rm -f "$PREFIX/import_ca-report.txt"
    ( cd "$PREFIX" && LD_PRELOAD="${DML_STEAM_LD_PRELOAD-}" run_slr \
        "$PREFIX_PY" -I "$(winpath "$PREFIX/import_ca.py")" \
        "$(winpath "$CA_PEM")" "$(winpath "$PREFIX/import_ca-report.txt")" ) >>"$LOGFILE" 2>&1
    if grep -q 'CA in LocalMachine.Root: yes' "$PREFIX/import_ca-report.txt" 2>/dev/null; then
        printf '%s\n' "$CA_THUMB" > "$PREFIX/.yulon-ca-thumbprint"
    else
        fail "The game's prefix did not take the server's certificate." \
            "Press Play on WoW Forever's tab in Yu'lon once, on the desktop, then launch again."
    fi
fi

# ── The login key, from Linux beside the game (stops with it) ─────────────────
setsid python3 -I - "${WOWFO_WORLD_PORT:-8085}" "$$" "$CLIENT_ROOT" "$BEFORE" >>"$LOGFILE" 2>&1 <<'PY' &
import ctypes, os, re, sys, time
from bisect import bisect_right

KEYS = [bytes.fromhex(h) for h in (
    "9b0671c815dff513bfd4a2b26ae1f84ec9106841b2fb620db65f6ade7c21ad06",
    "112451b9843c2799e4750aa3d9b9afabf536a645b863c4adb2078b932b354e04",
    "a6b858485748bf38be193517ab90f8de169eff0989ea9360db346a378b0ffe15",
    "50ffdeb807f8ff73a299a16000aa6575c5945be875adfc8795374ed3415249ac",
    "9f842d078755647c008e5fe3e12b839a981de02a1f520cb2545cc04cd2323e0e",
    "b8cc75864d8ef461d8dd6aa7425e2c63c24d3982066b8d773a1549bfe24e05ee",
    "76bb7bbdd9f34e124a4573c3aa227e3c87cc603506697d054f6fdec342e56ebf",
    "1fd6dd8fa0ec30d39e3f72e755b8a045bde0f70449dc71008b767c2eaa89fb9f",
    "15d618bd7db577bd9a8d45769c59e4fc631633bf447398a4b489b4c26fbc03ad",
    "b34ec592d2ac4993f5ffc85b15c3da9078694051cb224345592af7136d796c99",
    "9e91586dd4b115ab0568b21959e181f6545910bc5e2f30b8975ca09d7bc3efce",
    "769c824a1dadd19aeefa420414d1732daf44537f98aa229fc5477bca55f9d59a")]
SERVER = bytes.fromhex("02596F0D0C061A8B30745988FD72C59E29EC367FB0F341F28E0F08D037BAFC69")
GROUP = KEYS[8 - 1]  # Network.EnterEncryptedModeRegionGroup
PORT, PARENT = int(sys.argv[1]), int(sys.argv[2])
CLIENT = os.path.normpath(sys.argv[3])
PAGE, BATCH, SEP = 4096, 16 << 20, bytes(32)
PRESENT, SWAPPED, DIRTY, SCAN = 1 << 3, 1 << 4, 1 << 7, 0xC0606610
GAME = re.compile(rb"\\_forever_\\wowb?\.exe(?: |$)", re.I)
SKIP_P = (b"/dev/", b"[vvar", b"[vdso", b"[vsyscall", b"anon_inode:", b"/SYSV")
SKIP_S = (b".so", b".exe", b".dll", b".drv", b".sys", b".ocx", b".cpl", b".ax")

def say(msg):
    print(time.strftime("%H:%M:%S"), "keywatch:", msg, flush=True)

class Region(ctypes.Structure):
    _fields_ = [("start", ctypes.c_uint64), ("end", ctypes.c_uint64), ("categories", ctypes.c_uint64)]

class ScanArg(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in ("size", "flags", "start", "end", "walk_end", "vec", "vec_len",
                                              "max_pages", "category_inverted", "category_mask",
                                              "category_anyof_mask", "return_mask")]

libc = ctypes.CDLL(None, use_errno=True)
libc.ioctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p]

def under_client(argv0):
    # Wine's argv[0] is the Windows path of a file on this filesystem: Z: is /. Only a game
    # running from THIS client folder is this server's game, and only a pid that was not
    # there before the launch is the one this tile started (T888).
    t = argv0.decode("utf-8", "replace").strip().strip('"')
    if len(t) < 2 or t[1] != ":" or t[0].upper() != "Z":
        return False
    p = os.path.normpath("/" + t[2:].replace("\\", "/").lstrip("/"))
    return p == CLIENT or p.startswith(CLIENT.rstrip("/") + "/")

def game_pids():
    out = []
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                if os.stat("/proc/" + d).st_uid == os.getuid():
                    argv0 = open("/proc/%s/cmdline" % d, "rb").read().split(b"\0", 1)[0]
                    if GAME.search(argv0) and under_client(argv0):
                        out.append(int(d))
            except OSError:
                pass
    return out

BEFORE = {int(x) for x in sys.argv[4].split()}  # taken by the shell before the launch, and
# before this watcher started: an older copy that was already running is not ours.

def find_game():
    return next((p for p in game_pids() if p not in BEFORE), None)

def mappings(pid):
    out = []
    for line in open("/proc/%d/maps" % pid, "rb").read().splitlines():
        p = line.split(None, 5)
        if len(p) < 2 or p[1][:2] != b"rw":
            continue
        path = p[5].strip() if len(p) > 5 else b""
        low = (path[:-10] if path.endswith(b" (deleted)") else path).lower()
        if path.startswith(SKIP_P) or low.endswith(SKIP_S) or b".so." in low:
            continue
        a, b = p[0].split(b"-")
        out.append((int(a, 16), int(b, 16)))
    return out

vec = (Region * 8192)()
def pages(fd, mask, anyof):
    arg = ScanArg(); arg.size = ctypes.sizeof(arg); arg.vec = ctypes.addressof(vec); arg.vec_len = len(vec)
    arg.category_mask, arg.category_anyof_mask, arg.return_mask = mask, anyof, mask | anyof
    out, pos, end = [], 0, 0x7FFFFFFFF000
    while pos < end:
        arg.start, arg.end, arg.walk_end = pos, end, 0
        n = libc.ioctl(fd, SCAN, ctypes.byref(arg))
        if n < 0:
            raise OSError(ctypes.get_errno(), "PAGEMAP_SCAN failed")
        out.extend((vec[i].start, vec[i].end) for i in range(n))
        if arg.walk_end <= pos:
            break
        pos = arg.walk_end
    return out

def clip(ranges, maps):
    out, c = [], 0
    for s, e in ranges:
        while c < len(maps) and maps[c][1] <= s:
            c += 1
        j = c
        while j < len(maps) and maps[j][0] < e:
            out.append((max(s, maps[j][0]), min(e, maps[j][1]), maps[j][0], maps[j][1])); j += 1
    return out

def read(mem, s, e):
    try:
        b = os.pread(mem, e - s, s)
        if len(b) == e - s:
            return b
    except OSError:
        pass
    out = bytearray()
    for p in range(s, e, PAGE):
        n = min(PAGE - p % PAGE, e - p)
        try:
            b = os.pread(mem, n, p)
        except OSError:
            b = b""
        out += b if len(b) == n else bytes(n)
    return bytes(out)

def search(mem, pieces, keys):
    hits = set()
    for s, e, ms, me in pieces:
        s, e = max(ms, s - 31), min(me, e + 31)
        while s < e:
            t = min(e, s + BATCH)
            data = read(mem, s, t)
            for k in keys:
                i = data.find(k)
                while i != -1:
                    hits.add(s + i); i = data.find(k, i + 1)
            s = t - 31 if t < e else t
    return hits

def replace(mem, hits):
    done = group = 0
    for at in sorted(hits):
        try:
            was = os.pread(mem, 32, at)
            if was in KEYS and os.pwrite(mem, SERVER, at) == 32 and os.pread(mem, 32, at) == SERVER:
                done += 1; group += was == GROUP
        except OSError:
            pass
    return done, group

def connected():
    want = ":%04X" % PORT
    for fn in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            for line in open(fn).read().splitlines()[1:]:
                p = line.split()
                if len(p) > 3 and p[3] == "01" and p[2].endswith(want):
                    ip = p[2][:-5]
                    if (len(ip) == 8 and ip.endswith("7F")) or ip == "00000000000000000000000001000000" or (
                            len(ip) == 32 and ip.startswith("0000000000000000FFFF0000") and ip.endswith("7F")):
                        return True
        except OSError:
            pass
    return False

def main():
    t0 = time.monotonic()
    pid = find_game()
    while pid is None:
        if not os.path.exists("/proc/%d" % PARENT) or time.monotonic() - t0 > 600:
            say("the game did not start; no login key written."); return
        time.sleep(0.25); pid = find_game()
    for _ in range(120):  # Wine allows ptrace a moment after the process starts
        try:
            mem = os.open("/proc/%d/mem" % pid, os.O_RDWR); break
        except PermissionError:
            time.sleep(0.25)
    else:
        say("cannot open the game's memory; no login key written."); return
    pm, cr = os.open("/proc/%d/pagemap" % pid, os.O_RDONLY), os.open("/proc/%d/clear_refs" % pid, os.O_WRONLY)
    argv0 = open("/proc/%d/cmdline" % pid, "rb").read().split(b"\0", 1)[0]
    try:
        pages(pm, DIRTY, PRESENT); dirty = True
    except OSError:
        dirty = False; say("this kernel cannot list changed pages: every pass sweeps instead.")
    say("watching Wow.exe (pid %d)" % pid)
    written, all_keys, conn, sweep, si, total = False, False, None, [], 0, 0
    while True:
        try:
            if open("/proc/%d/cmdline" % pid, "rb").read().split(b"\0", 1)[0] != argv0:
                break
        except OSError:
            break
        if connected():
            conn = conn or time.monotonic()
            if time.monotonic() - conn >= 10 and not all_keys:
                time.sleep(0.5); continue
        else:
            conn = None
        t = time.monotonic(); maps = mappings(pid); hits = set()
        if dirty:
            rng = pages(pm, DIRTY, PRESENT | SWAPPED); os.pwrite(cr, b"4", 0)
            hits |= search(mem, clip(rng, maps), [GROUP])
        if si >= len(sweep):
            all_keys = False
            sweep, si = clip(pages(pm, 0, PRESENT) if dirty else maps, maps), 0
        budget, part = (64 << 20) if dirty else 1 << 62, []
        while si < len(sweep) and budget > 0:
            s, e, ms, me = sweep[si]
            if e - s > budget:
                part.append((s, s + budget, ms, me)); sweep[si] = (s + budget, e, ms, me); budget = 0
            else:
                part.append(sweep[si]); budget -= e - s; si += 1
        hits |= search(mem, part, KEYS if all_keys else [GROUP])
        if hits:
            done, group = replace(mem, hits); total += done
            if group:
                say("replaced %d copies of the game's region keys." % done)
                if not written:
                    written = True; say("The server's login key is in the game.")
                all_keys = True
                sweep, si = clip(pages(pm, 0, PRESENT) if dirty else maps, maps), 0
        time.sleep(max(0.0, 0.15 - (time.monotonic() - t)))
    say("the game was closed: %d key copies replaced." % total)

main()
PY

say "Launching WoW Forever Offline (account: mmodad, password: pass)"
say "Log: $LOGFILE"
printf '%s  prefix=%s proton=%s client=%s slr=%s\n' "$(date +%H:%M:%S)" "$PREFIX" "$PROTON_DIR" "$CLIENT_ROOT" "$SLR" >>"$LOGFILE"
cd "$CLIENT_ROOT/_forever_" || fail "Cannot enter $CLIENT_ROOT/_forever_"
# Hand the Steam overlay back to the game itself (scrubbed at the top).
LD_PRELOAD="${DML_STEAM_LD_PRELOAD-}" run_slr "$EXE" -config ForeverLauncher.wtf >>"$LOGFILE" 2>&1
EXIT=$?
say "The game exited with code $EXIT"
exit "$EXIT"
