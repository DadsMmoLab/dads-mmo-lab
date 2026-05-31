#!/bin/bash
# ============================================================
#  Dad's MMO Lab — RuneScape HD Upgrade
#  upgrade-runescape-hd.sh
#
#  Installs the Saradomin Launcher (HD-capable experimental
#  client) and points it at your existing 2009scape singleplayer
#  server. Replaces the bundled SD client with a modern one that
#  scales properly on the Steam Deck.
#
#  Requires: install-runescape.sh already run successfully.
#
#  Usage:
#    chmod +x upgrade-runescape-hd.sh
#    ./upgrade-runescape-hd.sh
#
#  https://github.com/DadsMmoLab/dads-mmo-lab
# ============================================================

UPGRADE_VERSION="1.0.0"
SERVER_DIR="$HOME/runescape-server"
SARADOMIN_FLATPAK="org._2009scape.Launcher"

set -o pipefail

RST='\033[0m'; BOLD='\033[1m'
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; WHITE='\033[1;37m'; CYAN='\033[0;36m'
RS='\033[38;5;208m'
RSB='\033[1;38;5;208m'

print_header() {
    clear
    echo ""
    echo -e "${RS}╔══════════════════════════════════════════════════╗${RST}"
    echo -e "${RS}║${WHITE}${BOLD}   🗡️  DAD'S MMO LAB — RuneScape HD Upgrade      ${RST}${RS}║${RST}"
    echo -e "${RS}║${WHITE}        v${UPGRADE_VERSION}                                    ${RST}${RS}║${RST}"
    echo -e "${RS}╚══════════════════════════════════════════════════╝${RST}"
    echo ""
}

print_step()    { echo ""; echo -e "${RS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"
                   echo -e "${WHITE}${BOLD} $1${RST}"
                   echo -e "${RS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"; }
print_success() { echo -e "${GREEN}✅ $1${RST}"; }
print_warning() { echo -e "${YELLOW}⚠️  $1${RST}"; }
print_error()   { echo -e "${RED}❌ $1${RST}"; }
print_info()    { echo -e "${BLUE}ℹ️  $1${RST}"; }

ask_yes_no() {
    while true; do
        printf "${WHITE}$1 (y/n): ${RST}"
        read -r answer
        case $answer in
            [Yy]*) return 0 ;;
            [Nn]*) return 1 ;;
            *) echo "Please answer y or n." ;;
        esac
    done
}

press_enter() {
    echo ""
    printf "${WHITE}Press ENTER to continue...${RST}"
    read -r
}

# ─────────────────────────────────────────────────────────────
# STEP 1 — PRECHECKS
# ─────────────────────────────────────────────────────────────
check_server_installed() {
    print_step "Checking for an existing RuneScape install"

    if [ ! -d "$SERVER_DIR" ]; then
        print_error "No RuneScape server found at $SERVER_DIR"
        echo ""
        print_info "Run install-runescape.sh first to set up the server."
        print_info "Then come back to this upgrade."
        exit 1
    fi

    if [ ! -f "$SERVER_DIR/server.jar" ] || \
       [ ! -f "$SERVER_DIR/ms.jar" ]; then
        print_error "Server files missing — install looks incomplete."
        print_info "Re-run install-runescape.sh."
        exit 1
    fi

    print_success "Found server at $SERVER_DIR"
    print_success "server.jar and ms.jar are in place"
}

check_internet() {
    print_step "Checking internet connection"

    if ! ping -c 1 -W 3 flathub.org &>/dev/null; then
        print_error "Can't reach flathub.org"
        echo ""
        print_info "The HD upgrade needs internet to:"
        print_info "  1. Install the Saradomin Launcher (Flatpak)"
        print_info "  2. Download the experimental client (first-run only)"
        echo ""
        print_info "Connect to WiFi and try again."
        exit 1
    fi

    print_success "Internet OK"
}

# ─────────────────────────────────────────────────────────────
# STEP 2 — EXPLAIN
# ─────────────────────────────────────────────────────────────
show_welcome() {
    print_header
    echo -e "${WHITE}This upgrade replaces the bundled SD-only client with the${RST}"
    echo -e "${WHITE}Saradomin Launcher, which uses the modern experimental${RST}"
    echo -e "${WHITE}client. The experimental client supports:${RST}"
    echo ""
    echo -e "  ${GREEN}✓ HD graphics that actually work${RST}"
    echo -e "  ${GREEN}✓ Window scaling for Steam Deck's 1280x800 screen${RST}"
    echo -e "  ${GREEN}✓ Plugin system for QoL features${RST}"
    echo -e "  ${GREEN}✓ A way better-looking client overall${RST}"
    echo ""
    echo -e "${WHITE}What this does:${RST}"
    echo -e "  1. Installs Saradomin Launcher via Flatpak (~150MB)"
    echo -e "  2. Creates a new Gaming Mode launcher that runs"
    echo -e "     ${CYAN}server + Saradomin${RST} instead of ${CYAN}server + SD client${RST}"
    echo -e "  3. Walks you through Saradomin's one-time setup"
    echo ""
    echo -e "${YELLOW}⚠️  Honest caveats:${RST}"
    echo -e "${WHITE}  • First launch of Saradomin needs internet — it downloads${RST}"
    echo -e "${WHITE}    the experimental client (~50MB) from 2009scape.org${RST}"
    echo -e "${WHITE}  • After first launch you can play offline forever${RST}"
    echo -e "${WHITE}  • The server profile is pre-configured for localhost —${RST}"
    echo -e "${WHITE}    just click Play on first launch${RST}"
    echo -e "${WHITE}  • Your original SD launcher (~/runescape-launcher.sh) is${RST}"
    echo -e "${WHITE}    not touched — you can switch back any time${RST}"
    echo ""
    echo -e "${BLUE}ℹ️  Install time: ~10 minutes (mostly the flatpak download)${RST}"
    echo ""
    ask_yes_no "Ready to upgrade?" || { echo "Run when ready!"; exit 0; }
}

# ─────────────────────────────────────────────────────────────
# STEP 3 — INSTALL FLATPAK INFRA
# ─────────────────────────────────────────────────────────────
ensure_flatpak() {
    print_step "Making sure flatpak is available"

    if command -v flatpak &>/dev/null; then
        print_success "flatpak already installed"
    else
        print_info "Installing flatpak..."
        if command -v steamos-readonly &>/dev/null; then
            sudo steamos-readonly disable 2>/dev/null || true
        fi
        if ! sudo pacman -Sy --noconfirm flatpak 2>/dev/null; then
            if command -v steamos-readonly &>/dev/null; then
                sudo steamos-readonly enable 2>/dev/null || true
            fi
            print_error "Failed to install flatpak."
            print_info "Try manually: sudo pacman -Sy flatpak"
            exit 1
        fi
        if command -v steamos-readonly &>/dev/null; then
            sudo steamos-readonly enable 2>/dev/null || true
        fi
        print_success "flatpak installed"
    fi

    # Add flathub remote if not present
    if ! flatpak remotes --user 2>/dev/null | grep -q flathub; then
        print_info "Adding Flathub remote..."
        flatpak remote-add --user --if-not-exists flathub \
            https://flathub.org/repo/flathub.flatpakrepo || {
            print_error "Failed to add Flathub. Network problem?"
            exit 1
        }
        print_success "Flathub added"
    else
        print_success "Flathub remote already configured"
    fi
}

# ─────────────────────────────────────────────────────────────
# STEP 4 — INSTALL SARADOMIN LAUNCHER
# ─────────────────────────────────────────────────────────────
install_saradomin() {
    print_step "Installing Saradomin Launcher"

    if flatpak list --user --app 2>/dev/null | grep -q "$SARADOMIN_FLATPAK"; then
        print_success "Saradomin Launcher already installed"
        return 0
    fi

    print_info "Downloading and installing Saradomin Launcher..."
    print_info "(~150MB — this can take 5-10 minutes on slow connections)"
    echo ""

    if ! flatpak install --user -y flathub "$SARADOMIN_FLATPAK"; then
        print_error "Saradomin install failed."
        print_info "Try manually:"
        print_info "  flatpak install --user flathub $SARADOMIN_FLATPAK"
        exit 1
    fi

    print_success "Saradomin Launcher installed!"
}

# ─────────────────────────────────────────────────────────────
# STEP 5 — PRE-CONFIGURE SARADOMIN (localhost IP + resolution)
# ─────────────────────────────────────────────────────────────
write_saradomin_config() {
    print_step "Pre-configuring Saradomin (localhost + 1280x720)"

    local CONFIG_DIR="$HOME/.var/app/${SARADOMIN_FLATPAK}/data/2009scape"
    local CONFIG_FILE="$CONFIG_DIR/config.json"

    mkdir -p "$CONFIG_DIR"

    cat > "$CONFIG_FILE" << 'SARACONFIG'
{
  "ip_management": "localhost",
  "ip_address": "localhost",
  "world": 1,
  "server_port": 43594,
  "wl_port": 43595,
  "js5_port": 43595,
  "ui_scale": 1,
  "fps": 0,
  "width": 1280,
  "height": 720
}
SARACONFIG

    if [ -f "$CONFIG_FILE" ]; then
        print_success "Saradomin pre-configured: localhost IP + 1280x720 resolution"
        print_info "Saradomin will connect to your local server automatically on first launch."
    else
        print_warning "Couldn't write Saradomin config."
        print_info "On first launch, go to Settings → Server Profile → select 'local'."
    fi
}

# ─────────────────────────────────────────────────────────────
# STEP 6 — WRITE HD LAUNCHER SCRIPT
# ─────────────────────────────────────────────────────────────
write_hd_launcher() {
    print_step "Creating HD launcher for Gaming Mode"

    cat > "$HOME/runescape-hd-launcher.sh" << LAUNCHER
#!/bin/bash
# Dad's MMO Lab — RuneScape 2009 HD Launcher v${UPGRADE_VERSION}
# Uses Saradomin Launcher (Flatpak) for HD experimental client.
# Server backend is unchanged from the original install.

export PATH="/usr/bin:/usr/local/bin:/bin:\$PATH"
unset LD_PRELOAD LD_LIBRARY_PATH
LOGFILE="/tmp/rs-hd-launch.log"
> "\$LOGFILE"

SERVER_DIR="${SERVER_DIR}"

# ── Trap-based cleanup ───────────────────────────────────────
cleanup() {
    echo ""
    echo "  Shutting down (saving character data — please wait)..."
    flatpak kill ${SARADOMIN_FLATPAK} 2>/dev/null || true
    pkill -TERM -f "\$SERVER_DIR/server.jar"   2>/dev/null || true
    local WAITED=0
    while [ \$WAITED -lt 30 ]; do
        pgrep -f "\$SERVER_DIR/server.jar" > /dev/null 2>&1 || break
        sleep 1; WAITED=\$((WAITED + 1))
        [ \$((WAITED % 3)) -eq 0 ] && printf "."
    done
    echo ""
    pkill -KILL -f "\$SERVER_DIR/server.jar"   2>/dev/null || true
    pkill -TERM -f "\$SERVER_DIR/ms.jar"       2>/dev/null || true
    sleep 2
    pkill -KILL -f "\$SERVER_DIR/ms.jar"       2>/dev/null || true
    pkill -TERM -f "\$SERVER_DIR/database/bin/mysqld" 2>/dev/null || true
    sleep 2
    pkill -KILL -f "\$SERVER_DIR/database/bin/mysqld" 2>/dev/null || true
    pkill -KILL -u "\$(id -u)" mysqld 2>/dev/null || true
    if command -v fuser &>/dev/null; then
        fuser -KILL "\$SERVER_DIR/database/data/ibdata1"         2>/dev/null || true
        fuser -KILL "\$SERVER_DIR/database/data/aria_log_control" 2>/dev/null || true
    fi
    rm -f "\$SERVER_DIR/database/data/"*.pid 2>/dev/null || true
    rm -f "\$SERVER_DIR/database/data/"*.sock* 2>/dev/null || true
    rm -f /tmp/mysql.sock /tmp/mysql.sock.lock 2>/dev/null || true
    echo "  ✅ Done! youtube.com/@DadsMmoLab"
}
trap cleanup EXIT INT TERM

clear
echo ""
echo "  🗡️  DAD'S MMO LAB — RuneScape 2009 HD"
echo "  ══════════════════════════════════════════"
echo "  Saradomin Launcher + Local Server"
echo "  ══════════════════════════════════════════"
echo ""

cd "\$SERVER_DIR" || {
    echo "  ❌ Server dir not found: \$SERVER_DIR"
    sleep 10; exit 1
}

# Bundled MySQL needs this
export LD_LIBRARY_PATH="\$SERVER_DIR/database/lib"

# ── Pre-flight cleanup ───────────────────────────────────────
echo "  Cleaning up leftover processes..."
pkill -TERM -f "\$SERVER_DIR/database/bin/mysqld" 2>/dev/null || true
pkill -TERM -f "\$SERVER_DIR/ms.jar"       2>/dev/null || true
pkill -TERM -f "\$SERVER_DIR/server.jar"   2>/dev/null || true
pkill -TERM -f "\$SERVER_DIR/client.jar"   2>/dev/null || true
sleep 2
pkill -KILL -f "\$SERVER_DIR/database/bin/mysqld" 2>/dev/null || true
pkill -KILL -f "\$SERVER_DIR/ms.jar"       2>/dev/null || true
pkill -KILL -f "\$SERVER_DIR/server.jar"   2>/dev/null || true
pkill -KILL -f "\$SERVER_DIR/client.jar"   2>/dev/null || true
# Short-name kills catch stale processes from a previous session where
# the process was started with a different working dir and the full
# path pattern no longer matches.
pkill -KILL -f "ms.jar"     2>/dev/null || true
pkill -KILL -f "server.jar" 2>/dev/null || true
pkill -KILL -f "client.jar" 2>/dev/null || true
pkill -KILL -u "\$(id -u)" mysqld 2>/dev/null || true
if command -v fuser &>/dev/null; then
    fuser -KILL "\$SERVER_DIR/database/data/ibdata1"         2>/dev/null || true
    fuser -KILL "\$SERVER_DIR/database/data/aria_log_control" 2>/dev/null || true
fi
KILL_WAIT=0
while pgrep -u "\$(id -u)" mysqld > /dev/null 2>&1; do
    sleep 1; KILL_WAIT=\$((KILL_WAIT + 1))
    [ \$KILL_WAIT -ge 15 ] && break
done
rm -f "\$SERVER_DIR/database/data/"*.pid 2>/dev/null || true
rm -f "\$SERVER_DIR/database/data/"*.sock* 2>/dev/null || true
rm -f /tmp/mysql.sock /tmp/mysql.sock.lock 2>/dev/null || true

# ── Start bundled MySQL ──────────────────────────────────────
echo "  Starting database..."
cd "\$SERVER_DIR/database"
bin/mysqld --console --skip-grant-tables \\
    --lc-messages-dir="./share/" \\
    --datadir="./data" \\
    >> "\$LOGFILE" 2>&1 &
MYSQL_PID=\$!

echo "  Waiting for database to accept connections..."
DB_READY=false
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do
    if "\$SERVER_DIR/database/bin/mysql" -u root \\
        -e "SELECT 1" >/dev/null 2>&1; then
        DB_READY=true
        break
    fi
    if ! kill -0 \$MYSQL_PID 2>/dev/null; then
        echo "  ❌ Database died during startup!"
        tail -30 "\$LOGFILE"
        sleep 15; exit 1
    fi
    sleep 2
done
[ "\$DB_READY" != "true" ] && { echo "  ❌ DB never accepted connections within 60 seconds"; tail -30 "\$LOGFILE"; sleep 15; exit 1; }
echo "  Database ready!"

# ── Pin Java 11 (Nashorn required for character saves) ───────
# Nashorn was removed in Java 15. Without it, server.jar silently
# fails to write player saves. Pin to 11 regardless of system default.
JAVA_11_HOME="/usr/lib/jvm/java-11-openjdk"
if [ -x "\$JAVA_11_HOME/bin/java" ]; then
    export JAVA_HOME="\$JAVA_11_HOME"
    export PATH="\$JAVA_HOME/bin:\$PATH"
    echo "  Using Java 11 (character saves enabled)"
else
    echo ""
    echo "  ❌ Java 11 not found at \$JAVA_11_HOME"
    echo "     Character saves will NOT work without it."
    echo "     Fix: sudo steamos-readonly disable"
    echo "          sudo pacman -Sy jre11-openjdk"
    echo "          sudo steamos-readonly enable"
    echo "  Window stays open 30s — copy this message first."
    sleep 30
    exit 1
fi

# ── Start management server ──────────────────────────────────
echo "  Starting management server..."
cd "\$SERVER_DIR"
java -jar ms.jar >> "\$LOGFILE" 2>&1 &
MS_PID=\$!
sleep 5
if ! kill -0 \$MS_PID 2>/dev/null; then
    echo "  ❌ Management server failed!"
    tail -20 "\$LOGFILE"
    sleep 15; exit 1
fi

# ── Start game server ────────────────────────────────────────
echo "  Starting game server..."
java -jar server.jar >> "\$LOGFILE" 2>&1 &
SERVER_PID=\$!

echo "  Waiting for Gielinor to open..."
SERVER_READY=false
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25; do
    if grep -qiE "listening|ready|started|game world is open|world is now online" \\
        "\$LOGFILE" 2>/dev/null; then
        SERVER_READY=true
        break
    fi
    if ! kill -0 \$SERVER_PID 2>/dev/null; then
        echo "  ❌ Game server died during startup!"
        tail -30 "\$LOGFILE"
        sleep 15; exit 1
    fi
    sleep 1
done
sleep 3

echo ""
echo "  ══════════════════════════════════════════"
echo "  ✅ SERVER READY — Launching Saradomin"
echo "  ══════════════════════════════════════════"
echo ""
echo "  ⚠️  FIRST LAUNCH ONLY:"
echo "     • Your server is pre-configured for localhost."
echo "       Just click PLAY in Saradomin's window."
echo "     • The experimental client downloads once (~50MB)."
echo "     • If Saradomin shows 'stable' server instead:"
echo "       Settings (gear) → Server Profile → select 'local'"
echo ""
echo "  After the first login, Saradomin remembers the"
echo "  local profile — next launch is one click."
echo ""

# ── Launch Saradomin (HD client) ─────────────────────────────
flatpak run ${SARADOMIN_FLATPAK} >> "\$LOGFILE" 2>&1 &
SARA_PID=\$!

# ── Resize Saradomin window to 1280x720 ──────────────────────
# The launcher may open smaller. wmctrl forces it to 1280x720 so
# the user can navigate the UI comfortably. config.json handles
# the experimental client's render resolution separately.
if command -v wmctrl &>/dev/null && command -v xdotool &>/dev/null; then
    echo "  Waiting for client window to appear..."
    for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
        WIN_ID=\$(wmctrl -l 2>/dev/null | grep -iE "runescape|2009scape|jagex|saradomin" | \\
                 head -1 | awk '{print \$1}')
        if [ -n "\$WIN_ID" ]; then
            wmctrl -i -r "\$WIN_ID" -e "0,0,0,1280,720" 2>/dev/null || true
            echo "  Client window resized to 1280x720"
            break
        fi
        sleep 2
    done
fi

# Saradomin is a launcher — when you click Play it spawns the actual
# experimental client as a new process and exits itself. If we only
# wait on SARA_PID we clean up the server the moment Play is clicked.
# Instead: after the initial process exits, keep alive as long as the
# flatpak app is still running in its sandbox (flatpak ps tracks this).
wait \$SARA_PID 2>/dev/null

RS_WAIT=0
while flatpak ps 2>/dev/null | grep -q "${SARADOMIN_FLATPAK}"; do
    sleep 5
    RS_WAIT=\$((RS_WAIT + 5))
    [ \$RS_WAIT -ge 7200 ] && break  # 2-hour safety cap
done

# Trap handles cleanup on exit
LAUNCHER

    chmod +x "$HOME/runescape-hd-launcher.sh"
    print_success "HD launcher written: ~/runescape-hd-launcher.sh"
}

# ─────────────────────────────────────────────────────────────
# STEP 7 — UPDATE MY_SERVER.txt
# ─────────────────────────────────────────────────────────────
update_info_file() {
    print_step "Updating MY_SERVER.txt with HD info"

    cat > "$SERVER_DIR/MY_SERVER_HD.txt" << INFO
====================================
  Dad's MMO Lab — RuneScape HD Mode
  Saradomin Launcher + Local Server
====================================

TWO LAUNCHERS NOW AVAILABLE:

  SD (original, rock-solid):
    ~/runescape-launcher.sh

  HD (new, scales properly on Deck):
    ~/runescape-hd-launcher.sh

You can use either one — they share the same server.
Just don't run both at the same time.

====================================
  First Run of HD Launcher
====================================

When Saradomin opens:

  1. Click PLAY — the server profile is pre-configured for
     localhost. No manual setup needed.
  2. The first time you click Play, the experimental client
     downloads (~50MB). Needs internet — once only.
  3. Log in with any username + password (auto-creates account)

If Saradomin shows "stable" server instead of local:
  Settings (gear icon) → Server Profile → select "local"
  (or manually set the IP to: 127.0.0.1)

After the first login, the local profile is saved and you
can play offline forever.

====================================
  Gaming Mode Setup (HD)
====================================

Add konsole to Steam:
  Target:  /usr/bin/konsole
  Options: --hold -e bash ~/runescape-hd-launcher.sh
  Proton:  OFF

====================================
  Switching Back to SD
====================================

The original launcher is untouched:
  ~/runescape-launcher.sh

Just run it instead of the HD one. No reinstall needed.

====================================
  Troubleshooting
====================================

"Error: js5connect" in Saradomin:
  Your server profile is set to "stable" (online) but
  Saradomin can't reach it. Switch to "local" profile.

"Connecting, this may take a long time..." then nothing:
  Server probably isn't running. Quit Saradomin and re-launch
  via the HD launcher, which starts the server first.

Saradomin won't launch at all:
  Test in a terminal:
    flatpak run ${SARADOMIN_FLATPAK}
  Common cause: first launch needs internet to fetch the client.

Logs: /tmp/rs-hd-launch.log
====================================
INFO

    print_success "Wrote $SERVER_DIR/MY_SERVER_HD.txt"
}

# ─────────────────────────────────────────────────────────────
# STEP 8 — FIRST-RUN GUIDANCE
# ─────────────────────────────────────────────────────────────
show_completion() {
    print_header
    echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════╗${RST}"
    echo -e "${GREEN}${BOLD}║   🗡️  HD UPGRADE COMPLETE!                       ║${RST}"
    echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════╝${RST}"
    echo ""
    echo -e "  ${WHITE}${BOLD}HD launcher:${RST} ${CYAN}~/runescape-hd-launcher.sh${RST}"
    echo -e "  ${WHITE}${BOLD}SD launcher:${RST} ${CYAN}~/runescape-launcher.sh${RST} (unchanged)"
    echo ""
    echo -e "${RS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"
    echo -e "${WHITE}${BOLD} ⚠️  FIRST RUN — Important Steps${RST}"
    echo -e "${RS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"
    echo ""
    echo -e "${WHITE}Your server is pre-configured for localhost — just click Play.${RST}"
    echo ""
    echo -e "${WHITE}${BOLD}When Saradomin opens:${RST}"
    echo -e "${WHITE}  1. Click ${CYAN}Play${RST}${WHITE} — server profile is already set to localhost${RST}"
    echo -e "${WHITE}  2. Experimental client downloads (~50MB, one-time)${RST}"
    echo -e "${WHITE}  3. Log in with any username + password — account auto-creates${RST}"
    echo ""
    echo -e "${WHITE}If Saradomin shows 'stable' server instead of local:${RST}"
    echo -e "${WHITE}  Settings (gear icon) → ${CYAN}Server Profile${RST}${WHITE} → select ${CYAN}local${RST}${WHITE}${RST}"
    echo -e "${WHITE}  (or manually set the IP to ${GREEN}127.0.0.1${RST}${WHITE})${RST}"
    echo ""
    echo -e "${RS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"
    echo -e "${WHITE}${BOLD} 🎮  Gaming Mode Setup (HD)${RST}"
    echo -e "${RS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"
    echo ""
    echo -e "${WHITE}If you want HD in Gaming Mode (recommended):${RST}"
    echo -e "${WHITE}  1. Right-click your existing RuneScape entry in Steam${RST}"
    echo -e "${WHITE}  2. Properties → Launch Options${RST}"
    echo -e "${WHITE}  3. Change the bash path to:${RST}"
    echo ""
    echo -e "  ${GREEN}--hold -e bash ~/runescape-hd-launcher.sh${RST}"
    echo ""
    echo -e "${WHITE}Or add a new Steam entry for HD and keep both available.${RST}"
    echo ""
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"
    echo -e "${WHITE}  📺 youtube.com/@DadsMmoLab${RST}"
    echo -e "${WHITE}  📦 github.com/DadsMmoLab/dads-mmo-lab${RST}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"
    echo ""
    echo -e "${WHITE}Detailed instructions saved at:${RST}"
    echo -e "${CYAN}  $SERVER_DIR/MY_SERVER_HD.txt${RST}"
    echo ""

    if ask_yes_no "Want to test the HD launcher right now?"; then
        print_info "Launching... (Ctrl+C to abort)"
        sleep 2
        bash "$HOME/runescape-hd-launcher.sh"
    else
        print_info "All set! Run ~/runescape-hd-launcher.sh whenever you want."
    fi
}

# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
print_header
check_server_installed
check_internet
show_welcome
ensure_flatpak
install_saradomin
write_saradomin_config
write_hd_launcher
update_info_file
show_completion
