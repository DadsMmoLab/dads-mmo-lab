#!/bin/bash
# ============================================================
#  Dad's MMO Lab — MU Online Server Installer
#  Powered by OpenMU
#
#  https://github.com/DadsMmoLab/dads-mmo-lab
#
#  Version: 2.0.0
#
#  Usage:
#    chmod +x install-muonline.sh
#    ./install-muonline.sh
#
#  What this does:
#    1. Installs Docker
#    2. Pulls the official OpenMU Docker image + starts the server
#    3. Pins the server + a /etc/hosts redirect to the Deck's LAN IP
#       (Proton's sandbox cannot reach 127.x — see notes below)
#    4. Installs a root-owned helper + tight sudoers rule so the launcher
#       can re-pin the redirect after a DHCP IP change, with no password
#    5. Installs the Gaming Mode launcher + writes MY_SERVER.txt
#    6. Guides client setup (GE-Proton + gamescope launch option)
#
#  WHY THE LAN IP (hard-won): the OpenMU-patched main.exe ignores the
#  registry/CLI connect override and falls back to its hardcoded hostname
#  connect.muonline.webzen.com, which still resolves publicly to a dead
#  WebZen server. We redirect that hostname to the Deck's LAN IP in
#  /etc/hosts, and register the game servers with the same LAN IP, because
#  Proton's pressure-vessel sandbox has an isolated loopback (127.x is NOT
#  the host) but CAN reach the host's LAN IP.
#
#  DEMO LOGIN: the login name is the password — test0/test0, testgm/testgm.
#
#  Admin panel: http://127.0.0.1 (port 80 via nginx proxy)
# ============================================================

INSTALLER_VERSION="2.0.0"

set -o pipefail

RST='\033[0m'; BOLD='\033[1m'; DIM='\033[2m'
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; WHITE='\033[1;37m'; CYAN='\033[0;36m'

MU='\033[0;35m'
MUB='\033[1;35m'

print_header() {
    clear
    echo ""
    echo -e "${MU}╔══════════════════════════════════════════════════╗${RST}"
    echo -e "${MU}║${WHITE}${BOLD}         💎 DAD'S MMO LAB                        ${RST}${MU}║${RST}"
    echo -e "${MU}║${WHITE}         MU Online Installer v${INSTALLER_VERSION}               ${RST}${MU}║${RST}"
    echo -e "${MU}║${BLUE}         Powered by OpenMU                       ${RST}${MU}║${RST}"
    echo -e "${MU}╚══════════════════════════════════════════════════╝${RST}"
    echo ""
}

print_step() {
    echo ""
    echo -e "${MU}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"
    echo -e "${WHITE}${BOLD} $1${RST}"
    echo -e "${MU}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"
}

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

# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────
SERVER_DIR="$HOME/muonline-server"
HELPER_PATH="/etc/dml-mu-hosts.sh"
# NOTE the 'zz-' prefix: /etc/sudoers.d files are read in lexical order and the
# LAST matching rule wins. SteamOS ships a broad "wheel" grant (deck ALL=(ALL)
# ALL, password required) in /etc/sudoers.d/wheel. Our NOPASSWD rule must be
# read AFTER it, so the filename must sort after "wheel" (z > w). Do not rename
# this to something that sorts earlier — the passwordless re-pin will break.
SUDOERS_PATH="/etc/sudoers.d/zz-dml-muonline"
SUDOERS_OLD="/etc/sudoers.d/dml-muonline"
LAN_IP=""   # filled by detect_lan_ip

# WebZen connect hostnames the patched main.exe falls back to.
MU_HOSTS=(connect.muonline.webzen.com connection.muonline.com.tw connect.muonline.com.ph connect.muchina.com)

# ─────────────────────────────────────────
# LAN IP DETECTION
# ─────────────────────────────────────────
detect_lan_ip() {
    local ip
    ip=$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K[0-9.]+' | head -1)
    if [ -z "$ip" ]; then
        ip=$(ip -4 -o addr show 2>/dev/null | grep -oP 'inet \K[0-9.]+' \
            | grep -vE '^127\.|^172\.(1[6-9]|2[0-9]|3[01])\.' \
            | grep -E '^192\.168\.|^10\.' | head -1)
    fi
    echo "$ip"
}

# ─────────────────────────────────────────
# SYSTEM CHECK
# ─────────────────────────────────────────
check_system() {
    print_step "Checking System"
    [[ "$OSTYPE" != "linux-gnu"* ]] && { print_error "Linux required."; exit 1; }
    print_success "Linux detected"
    ping -c 1 github.com &>/dev/null || { print_error "No internet."; exit 1; }
    print_success "Internet OK"

    LAN_IP="$(detect_lan_ip)"
    if [ -z "$LAN_IP" ]; then
        print_error "Could not find a LAN IP. MU Online needs the Deck on a network"
        print_error "(wifi/ethernet) — Proton cannot reach a localhost-only server."
        exit 1
    fi
    print_success "Deck LAN IP: $LAN_IP"
}

check_pacman_keyring() {
    print_info "Checking pacman keyring..."
    local broken=false
    [[ ! -f /etc/pacman.d/gnupg/pubring.gpg ]] && broken=true
    ! sudo pacman -Sy &>/dev/null && broken=true
    [[ "$broken" == false ]] && { print_success "Keyring OK"; return 0; }
    printf "${WHITE}Type ${GREEN}yes${WHITE} to reset keyring: ${RST}"; read -r c
    [[ "$c" != "yes" ]] && exit 1
    sudo rm -rf /etc/pacman.d/gnupg
    sudo pacman-key --init && sudo pacman-key --populate archlinux holo
    print_success "Keyring reset"
}

install_docker() {
    command -v docker &>/dev/null && docker ps &>/dev/null 2>&1 && \
        { print_success "Docker running"; return 0; }
    print_info "Installing Docker..."
    command -v steamos-readonly &>/dev/null && sudo steamos-readonly disable
    check_pacman_keyring
    sudo pacman -Sy --noconfirm docker docker-compose git || \
        { print_error "Docker install failed"; exit 1; }
    sudo usermod -aG docker "$USER"
    sudo systemctl enable --now docker 2>/dev/null || true
    sleep 3
    sudo chmod 666 /var/run/docker.sock 2>/dev/null || true
    if command -v steamos-readonly &>/dev/null; then
        sudo steamos-readonly enable 2>/dev/null || \
            print_warning "Could not re-enable steamos-readonly"
    fi
    print_success "Docker installed"
}

# ─────────────────────────────────────────
# WELCOME
# ─────────────────────────────────────────
show_welcome() {
    print_header
    echo -e "${WHITE}Welcome to the MU Online installer!${RST}"
    echo ""
    echo -e "${MUB}MU Online (Season 6 Episode 3)${RST}"
    echo -e "${WHITE}Webzen's legendary action MMORPG, fully offline${RST}"
    echo -e "${WHITE}on your Steam Deck via the OpenMU server.${RST}"
    echo ""
    echo -e "${MUB}What this sets up:${RST}"
    echo -e "${WHITE}  💎 Official OpenMU Docker server${RST}"
    echo -e "${WHITE}  🌐 Web admin panel at http://127.0.0.1${RST}"
    echo -e "${WHITE}  🔌 Auto-pins the client redirect to your Deck's LAN IP${RST}"
    echo -e "${WHITE}  🎮 Gaming Mode launcher with auto start/stop${RST}"
    echo ""
    echo -e "${YELLOW}⚠️  You supply the MU Online Season 6 Ep3 client (see the${RST}"
    echo -e "${YELLOW}   client steps at the end). The server install is automatic.${RST}"
    echo ""
    ask_yes_no "Ready to hunt wings? 💎" || { echo "Run when ready!"; exit 0; }
}

# ─────────────────────────────────────────
# STEP 1 — START OPENMU (registered with the LAN IP)
# ─────────────────────────────────────────
start_server() {
    print_header
    print_step "STEP 1/3 — Starting OpenMU"

    install_docker
    mkdir -p "$SERVER_DIR"

    print_info "Writing docker-compose.yml (RESOLVE_IP = $LAN_IP)..."

    # .htpasswd must exist as a FILE or Docker bind-mounts it as a directory
    touch "$SERVER_DIR/.htpasswd"

    # NOTE: RESOLVE_IP MUST be the Deck's LAN IP, not loopback. Proton's
    # pressure-vessel sandbox has an isolated loopback (cannot reach 127.x)
    # but can reach the host LAN IP. The launcher re-pins this on IP change.
    cat > "$SERVER_DIR/docker-compose.yml" << COMPOSE
services:
  nginx-80:
    image: nginx:alpine
    container_name: nginx-80
    ports:
      - "80:80"
    volumes:
      - ./nginx/nginx.dev.conf:/etc/nginx/nginx.conf:ro
      - ./.htpasswd:/etc/nginx/.htpasswd
    depends_on:
      - openmu-startup

  openmu-startup:
    image: munique/openmu
    container_name: openmu-startup
    ports:
      - "8080"
      - "55901:55901"
      - "55902:55902"
      - "55903:55903"
      - "55904:55904"
      - "55905:55905"
      - "55906:55906"
      - "44405:44405"
      - "44406:44406"
      - "55980:55980"
    environment:
      DB_HOST: database
      ASPNETCORE_URLS: http://+:8080
      RESOLVE_IP: ${LAN_IP}
    working_dir: /app/
    volumes:
      - ./.htpasswd:/etc/nginx/.htpasswd
    depends_on:
      - database

  database:
    image: postgres
    container_name: database
    environment:
      POSTGRES_PASSWORD: admin
      POSTGRES_DB: openmu
      POSTGRES_USER: postgres
    ports:
      - "5432"
    volumes:
      - dbdata:/var/lib/postgresql

volumes:
  dbdata:
COMPOSE

    cd "$SERVER_DIR"

    # nginx config for the admin-panel proxy. Nuke+recreate in case a prior
    # failed run left a root-owned directory at the bind-mount path.
    sudo rm -rf "$SERVER_DIR/nginx"
    mkdir -p "$SERVER_DIR/nginx"
    cat > "$SERVER_DIR/nginx/nginx.dev.conf" << 'NGINXCONF'
events {
    worker_connections 1024;
}

http {
    server {
        listen 80;
        server_name localhost;

        location / {
            proxy_pass http://openmu-startup:8080;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        }
    }
}
NGINXCONF

    print_info "Starting OpenMU (first run pulls the image — a few minutes)..."
    if ! docker compose up -d; then
        print_error "Failed to start OpenMU"
        exit 1
    fi

    print_info "Waiting for the game servers to register with $LAN_IP..."
    echo ""
    local timeout=240 elapsed=0
    while [ $elapsed -lt $timeout ]; do
        # grep without -q on purpose: -q + pipefail = SIGPIPE false-negatives
        if docker logs openmu-startup 2>/dev/null \
            | grep -F "has registered with endpoint \"$LAN_IP" > /dev/null; then
            print_success "OpenMU is ready (registered with $LAN_IP)!"
            break
        fi
        printf "."; sleep 10; elapsed=$((elapsed + 10))
    done
    echo ""
    [ $elapsed -ge $timeout ] && print_warning "Timed out waiting; the server may still be initializing."
}

# ─────────────────────────────────────────
# STEP 2 — NETWORK REDIRECT (hosts + helper + sudoers)
# ─────────────────────────────────────────
setup_network_redirect() {
    print_header
    print_step "STEP 2/3 — Network Redirect (LAN IP)"

    # Root-owned helper that re-pins the MU connect hostnames to a given
    # private IP. The launcher calls it via passwordless sudo after a DHCP
    # change. It validates the IP and only ever touches its own lines.
    print_info "Installing redirect helper at $HELPER_PATH..."
    sudo tee "$HELPER_PATH" >/dev/null << 'HELPER'
#!/bin/bash
# Dad's MMO Lab — re-pin MU Online connect hostnames in /etc/hosts.
# Installed root-owned; invoked via sudo by ~/muonline-launcher.sh.
IP="$1"
case "$IP" in
    192.168.*|10.*|172.1[6-9].*|172.2[0-9].*|172.3[01].*) ;;
    *) echo "dml-mu-hosts: refusing non-private IP '$IP'" >&2; exit 1 ;;
esac
sed -i '/dml-muonline-autopin/d;/connect\.muonline\.webzen\.com/d;/connection\.muonline\.com\.tw/d;/connect\.muonline\.com\.ph/d;/connect\.muchina\.com/d' /etc/hosts
{
    echo "# dml-muonline-autopin"
    echo "$IP connect.muonline.webzen.com"
    echo "$IP connection.muonline.com.tw"
    echo "$IP connect.muonline.com.ph"
    echo "$IP connect.muchina.com"
} >> /etc/hosts
HELPER
    sudo chown root:root "$HELPER_PATH"
    sudo chmod 755 "$HELPER_PATH"

    # Tight passwordless-sudo rule: only this one helper, nothing else.
    # SAFETY: validate a TEMP copy first and only install it if valid — a
    # malformed file in /etc/sudoers.d/ would break sudo system-wide, so it
    # must never land there unvalidated.
    print_info "Installing sudoers rule so the launcher can re-pin silently..."
    local sudo_tmp
    sudo_tmp="$(mktemp)"
    echo "$(id -un) ALL=(root) NOPASSWD: $HELPER_PATH" > "$sudo_tmp"
    if sudo visudo -cf "$sudo_tmp" >/dev/null 2>&1; then
        sudo install -m 440 -o root -g root "$sudo_tmp" "$SUDOERS_PATH"
        sudo rm -f "$SUDOERS_OLD"   # remove pre-zz-prefix name from older installs
        print_success "Sudoers rule installed (validated)."
    else
        print_warning "Sudoers rule failed validation; skipped (sudo left untouched)."
        print_warning "After a DHCP change you'll re-pin manually (launcher shows how)."
    fi
    rm -f "$sudo_tmp"

    # Apply the initial redirect now.
    if sudo "$HELPER_PATH" "$LAN_IP"; then
        print_success "Client redirect pinned: connect.muonline.webzen.com → $LAN_IP"
    else
        print_warning "Could not write /etc/hosts redirect."
    fi

    print_info "Note: SteamOS system updates can wipe /etc — if MU stops"
    print_info "connecting after an update, just re-run this installer."
}

# ─────────────────────────────────────────
# STEP 3 — GAMING MODE LAUNCHER + REFERENCE
# ─────────────────────────────────────────
setup_launcher() {
    print_header
    print_step "STEP 3/3 — Gaming Mode Launcher"

    cat > "$HOME/muonline-launcher.sh" << 'LAUNCHER'
#!/bin/bash
# Dad's MMO Lab — MU Online Launcher v3.0.0 (OpenMU)
# Auto-pins the server + client redirect to the Deck's CURRENT LAN IP each run,
# because Proton's pressure-vessel sandbox has an isolated loopback (can't reach
# 127.x) but CAN reach the host's LAN IP. See ~/muonline-server/MY_SERVER.txt.
export PATH="/usr/bin:/usr/local/bin:/bin:$PATH"
unset LD_PRELOAD
unset LD_LIBRARY_PATH

LOGFILE="/tmp/mu-launch.log"
> "$LOGFILE"

SERVER_DIR="$HOME/muonline-server"
COMPOSE="$SERVER_DIR/docker-compose.yml"
HELPER="/etc/dml-mu-hosts.sh"

MU_HOSTS=(connect.muonline.webzen.com connection.muonline.com.tw connect.muonline.com.ph connect.muchina.com)

clear
echo ""
echo "  💎 DAD'S MMO LAB — MU Online"
echo "  ══════════════════════════════════════════"
echo "  Powered by OpenMU"
echo "  ══════════════════════════════════════════"
echo ""

# ── 1. Detect current LAN IP (the address Proton can actually reach) ──────────
detect_lan_ip() {
    local ip
    ip=$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K[0-9.]+' | head -1)
    if [ -z "$ip" ]; then
        ip=$(ip -4 -o addr show 2>/dev/null | grep -oP 'inet \K[0-9.]+' \
            | grep -vE '^127\.|^172\.(1[6-9]|2[0-9]|3[01])\.' \
            | grep -E '^192\.168\.|^10\.' | head -1)
    fi
    echo "$ip"
}

LAN_IP="$(detect_lan_ip)"
if [ -z "$LAN_IP" ]; then
    echo "  ⚠️  No LAN IP found. MU needs the Deck on a network (wifi/ethernet)."
    echo "      Proton cannot reach a localhost-only server. Connect and relaunch."
    sleep 12
    exit 1
fi
echo "  Deck LAN IP: $LAN_IP"

# ── 2. Re-pin RESOLVE_IP in docker-compose.yml (no sudo needed) ───────────────
RESOLVE_CHANGED=0
if [ -f "$COMPOSE" ]; then
    CUR=$(grep -oP '^\s*RESOLVE_IP:\s*\K\S+' "$COMPOSE" 2>/dev/null | head -1)
    if [ "$CUR" != "$LAN_IP" ]; then
        sed -i "s|^\(\s*RESOLVE_IP:\s*\).*|\1$LAN_IP|" "$COMPOSE"
        RESOLVE_CHANGED=1
        echo "  Updated server RESOLVE_IP: ${CUR:-?} → $LAN_IP"
    fi
fi

# ── 3. Re-pin /etc/hosts redirect to the LAN IP ───────────────────────────────
hosts_ok() { getent hosts "${MU_HOSTS[0]}" 2>/dev/null | grep -q "^$LAN_IP[[:space:]]"; }
if hosts_ok; then
    echo "  Hosts redirect already → $LAN_IP ✓"
elif [ -x "$HELPER" ] && sudo -n "$HELPER" "$LAN_IP" 2>>"$LOGFILE"; then
    echo "  Hosts redirect re-pinned → $LAN_IP ✓"
else
    echo ""
    echo "  ⚠️  The MU connect hostname needs to point at $LAN_IP."
    echo "      The auto-updater isn't available (run/re-run install-muonline.sh"
    echo "      to restore it). One-time manual fix in Konsole, then relaunch:"
    echo ""
    echo "        sudo sed -i '/connect.muonline.webzen.com/d' /etc/hosts && \\"
    echo "          echo '$LAN_IP connect.muonline.webzen.com' | sudo tee -a /etc/hosts"
    echo ""
    echo "  (Continuing — connection will fail until this is done.)"
    sleep 2
fi

# ── 4. Start (or restart) the server ──────────────────────────────────────────
cd "$SERVER_DIR" || { echo "  ERR: $SERVER_DIR not found"; sleep 10; exit 1; }
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^openmu-startup$' && [ "$RESOLVE_CHANGED" -eq 0 ]; then
    echo "  Server already running."
else
    echo "  Starting OpenMU$( [ "$RESOLVE_CHANGED" -eq 1 ] && echo ' (recreating for new IP)' )..."
    if ! docker compose up -d >>"$LOGFILE" 2>&1; then
        echo "  ERR: Failed to start server. Check $LOGFILE"
        sleep 10
        exit 1
    fi
fi

# ── 5. Wait for the game servers to register with the LAN IP ──────────────────
echo ""
echo "  Waiting for MU world to come online..."
echo ""
TIMEOUT=180; ELAPSED=0; READY=0
while [ $ELAPSED -lt $TIMEOUT ]; do
    if docker logs openmu-startup 2>/dev/null \
        | grep -F "has registered with endpoint \"$LAN_IP" > /dev/null; then
        READY=1; break
    fi
    printf "  ."; sleep 5; ELAPSED=$((ELAPSED + 5))
done
echo ""

echo ""
if [ $READY -eq 1 ]; then
    echo "  ══════════════════════════════════════════"
    echo "  ✅ MU ONLINE IS READY! 💎"
    echo "  ══════════════════════════════════════════"
else
    echo "  ⏳ Still initializing — launch MU shortly"
fi
echo ""
echo "  Server:       $LAN_IP (connect 44405)"
echo "  Admin panel:  http://127.0.0.1"
echo "  Login:        test0 / test0   (GM: testgm / testgm)"
echo ""
echo "  Press STEAM button and launch \"Mu Client 2\""
echo "  Server AUTO-SHUTS DOWN when MU closes"
echo ""

# ── 6. Wait for the client, then shut down on exit ────────────────────────────
# Primary signal: gamescope with our launch params (host-visible even when Wine
# is inside the pressure-vessel container). Fallback: main.exe in the proc list.
DETECT='gamescope.*-w 1024 -h 768|[Mm]ain\.exe'
MU_STARTED=0
for i in $(seq 1 60); do
    if pgrep -f "$DETECT" > /dev/null 2>&1; then MU_STARTED=1; break; fi
    sleep 5
done

if [ $MU_STARTED -eq 1 ]; then
    echo "  MU Online detected! Welcome to the Continent of MU. 💎"
    while pgrep -f "$DETECT" > /dev/null 2>&1; do sleep 3; done
    sleep 5
    echo "  MU Online closed — shutting down server..."
else
    echo "  MU Online not detected — keeping server alive for 3 hours."
    sleep 10800
fi

cd "$SERVER_DIR" && docker compose down >>"$LOGFILE" 2>&1

echo ""
echo "  ✅ Server stopped! Safe to close."
echo "  Thanks for playing! youtube.com/@DadsMmoLab"
echo ""
sleep 5
LAUNCHER

    chmod +x "$HOME/muonline-launcher.sh"
    print_success "Launcher installed: ~/muonline-launcher.sh"

    # Reference card (substitutes the detected LAN IP)
    cat > "$SERVER_DIR/MY_SERVER.txt" << INFO
====================================
  Dad's MMO Lab — MU Online
  OpenMU (Season 6 Episode 3)
====================================

THE CLIENT (Steam shortcut "Mu Client 2", GE-Proton):
  Season 6 Ep3 client + OpenMU patched main.exe + patched ItemTooltip_eng.bmd

LOGIN (demo accounts — the PASSWORD IS THE USERNAME):
  test0 / test0   (normal)    test1/test1 ... test9/test9
  testgm / testgm (GM powers)
  No registration — 20 demo accounts ship with the server.

HOW THE CONNECTION WORKS:
  - Proton's sandbox can't reach 127.0.0.1, only the Deck's LAN IP.
  - Server advertises the LAN IP via RESOLVE_IP in docker-compose.yml.
  - Client connects to hostname connect.muonline.webzen.com, redirected
    in /etc/hosts to the LAN IP. Current LAN IP at install: ${LAN_IP}
  - The launcher auto-re-pins both each run (handles DHCP changes via the
    root-owned helper ${HELPER_PATH} + a passwordless-sudo rule).

  *** IF MU STOPS CONNECTING (new router / SteamOS update): ***
  Just relaunch via the "MU Online Server" shortcut — it re-pins the IP.
  If that can't (sudoers wiped by an update), re-run install-muonline.sh.

Admin Panel: http://127.0.0.1  (port 80, no login for local use)

Gaming Mode:
  Add konsole to Steam -> rename "MU Online Server"
  Launch options:  --hold -e bash ~/muonline-launcher.sh
  Do NOT enable Proton on the launcher shortcut.

Client Steam launch options (on "Mu Client 2", GE-Proton):
  gamescope -w 1024 -h 768 -W 1280 -H 800 -f -- %command%

Commands:
  Start:  cd ${SERVER_DIR} && docker compose up -d
  Stop:   cd ${SERVER_DIR} && docker compose down
  Logs:   docker logs -f openmu-startup
  Admin:  Open browser to http://127.0.0.1
====================================
INFO
    print_success "Reference saved: $SERVER_DIR/MY_SERVER.txt"
}

# ─────────────────────────────────────────
# CLIENT GUIDANCE
# ─────────────────────────────────────────
guide_client() {
    print_header
    print_step "Client Setup (you supply the client)"
    echo ""
    echo -e "${WHITE}You need the OpenMU-compatible Season 6 Ep3 client. Get these from${RST}"
    echo -e "${WHITE}the OpenMU community (GitHub releases + Discord #downloads):${RST}"
    echo ""
    echo -e "  ${MUB}1.${RST} ${WHITE}MU Season 6 Episode 3 client${RST} (base game files)"
    echo -e "  ${MUB}2.${RST} ${WHITE}OpenMU patched ${CYAN}main.exe${RST} ${WHITE}(GameGuard off, reads our server)${RST}"
    echo -e "  ${MUB}3.${RST} ${WHITE}Patched ${CYAN}Data/Local/Eng/ItemTooltip_eng.bmd${RST}"
    echo -e "  ${MUB}4.${RST} ${WHITE}OpenMU ClientLauncher (optional):${RST}"
    echo -e "     ${DIM}github.com/MUnique/OpenMU/releases${RST}"
    echo ""
    echo -e "${WHITE}Put the client somewhere like ${CYAN}~/Games/MU Client 1.04d - Season 6E3/${RST}"
    echo -e "${WHITE}then drop the patched ${CYAN}main.exe${RST}${WHITE} and ${CYAN}ItemTooltip_eng.bmd${RST}${WHITE} in.${RST}"
    echo ""
    echo -e "${MU}── Add to Steam ──────────────────────────────${RST}"
    echo -e "  • Add ${CYAN}main.exe${RST} as a Non-Steam game, name it ${WHITE}\"Mu Client 2\"${RST}"
    echo -e "  • Compatibility → force ${WHITE}GE-Proton${RST}"
    echo -e "  • Launch options:"
    echo -e "      ${CYAN}gamescope -w 1024 -h 768 -W 1280 -H 800 -f -- %command%${RST}"
    echo ""
    echo -e "${MU}── First login ───────────────────────────────${RST}"
    echo -e "  • Start ${WHITE}\"MU Online Server\"${RST} (the launcher) → wait for READY"
    echo -e "  • Launch ${WHITE}\"Mu Client 2\"${RST} → pick any server"
    echo -e "  • Username ${WHITE}test0${RST}  Password ${WHITE}test0${RST}   (GM: ${WHITE}testgm / testgm${RST})"
    echo ""
}

show_completion() {
    echo ""
    echo -e "${MUB}╔══════════════════════════════════════════════════╗${RST}"
    echo -e "${MUB}║   💎 MU ONLINE SERVER IS READY!                  ║${RST}"
    echo -e "${MUB}╚══════════════════════════════════════════════════╝${RST}"
    echo ""
    echo -e "  ${WHITE}Server (LAN IP):${RST}  ${MU}${LAN_IP}${RST}  (connect 44405)"
    echo -e "  ${WHITE}Admin Panel:${RST}     ${MU}http://127.0.0.1${RST}"
    echo -e "  ${WHITE}Login:${RST}           ${MU}test0 / test0${RST}   (GM: testgm / testgm)"
    echo -e "  ${WHITE}Reference:${RST}       ${SERVER_DIR}/MY_SERVER.txt"
    echo ""
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"
    echo -e "${WHITE}  📺 youtube.com/@DadsMmoLab${RST}"
    echo -e "${WHITE}  📦 github.com/DadsMmoLab/dads-mmo-lab${RST}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"
    echo ""
    echo -e "${MUB}Rise, Adventurer! The world of MU awaits. 💎${RST}"
    echo ""
}

check_system
show_welcome
start_server
setup_network_redirect
setup_launcher
guide_client
show_completion
