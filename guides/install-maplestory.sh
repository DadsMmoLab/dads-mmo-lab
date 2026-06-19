#!/bin/bash
# ============================================================
#  Dad's MMO Lab — MapleStory v83 Server Installer
#  Powered by Cosmic
#
#  https://github.com/DadsMmoLab/dads-mmo-lab
#
#  Version: 1.2.1
#
#  Usage:
#    chmod +x install-maplestory.sh
#    ./install-maplestory.sh
#
#  What this does:
#    1. Installs Docker if needed
#    2. Clones Cosmic from GitHub (its own Dockerfile + docker-compose)
#    3. Patches Cosmic's config.yaml (DB_PASS, PIN/PIC, Scania-only world)
#    4. Starts your MapleStory server via Cosmic's own compose
#    5. Sets up the Gaming Mode launcher
#    6. Walks you through the 3-file client setup
#
#  Powered by Cosmic — https://github.com/P0nk/Cosmic
#  The most complete v83 MapleStory server emulator.
#
#  ⚠️  You need TWO client files (obtain separately):
#  1. Cosmic-client release ZIP — github.com/P0nk/Cosmic-client/releases
#  2. MapleGlobal-v83-setup.exe — Cosmic Discord (community-shared)
#  The installer walks you through both.
# ============================================================

INSTALLER_VERSION="1.2.1"

set -o pipefail

# ─────────────────────────────────────────
# COLORS — Hot pink for MapleStory's iconic aesthetic
# ─────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m'
BOLD='\033[1m'

# MapleStory pink — distinct from all other games
MS='\033[0;35m'
MSB='\033[1;35m'

print_header() {
    clear
    echo ""
    echo -e "${MS}╔══════════════════════════════════════════════════╗${NC}"
    echo -e "${MS}║${WHITE}${BOLD}         🍁 DAD'S MMO LAB                         ${NC}${MS}║${NC}"
    echo -e "${MS}║${WHITE}         MapleStory v83 Server Installer          ${NC}${MS}║${NC}"
    echo -e "${MS}║${BLUE}         Powered by Cosmic                        ${NC}${MS}║${NC}"
    echo -e "${MS}║${YELLOW}         Version ${INSTALLER_VERSION}                              ${NC}${MS}║${NC}"
    echo -e "${MS}╚══════════════════════════════════════════════════╝${NC}"
    echo ""
}

print_step() {
    echo ""
    echo -e "${MS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD} $1${NC}"
    echo -e "${MS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

print_success() { echo -e "${GREEN}✅ $1${NC}"; }
print_warning() { echo -e "${YELLOW}⚠️  $1${NC}"; }
print_error()   { echo -e "${RED}❌ $1${NC}"; }
print_info()    { echo -e "${BLUE}ℹ️  $1${NC}"; }

ask_yes_no() {
    while true; do
        echo -e "${WHITE}$1 (y/n): ${NC}"
        read -r answer
        case $answer in
            [Yy]* ) return 0;;
            [Nn]* ) return 1;;
            * ) echo "Please answer y or n.";;
        esac
    done
}

press_enter() {
    echo ""
    echo -e "${WHITE}Press ENTER to continue...${NC}"
    read -r
}

# ─────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────
SERVER_DIR="$HOME/maplestory-server"
DB_PASSWORD="maple$(date +%s | tail -c 6)"
SERVER_IP="127.0.0.1"

# Cosmic ports — no conflicts with any of our other games
# Login: 8484, Channels: 7575-7577

# ─────────────────────────────────────────
# SYSTEM CHECKS
# ─────────────────────────────────────────
check_system() {
    print_step "Checking System Requirements"

    if [[ "$OSTYPE" != "linux-gnu"* ]]; then
        print_error "This script requires Linux (SteamOS). Are you in Desktop Mode?"
        exit 1
    fi
    print_success "Linux detected"

    AVAILABLE_GB=$(df -BG "$HOME" 2>/dev/null | awk 'NR==2 {print $4}' | sed 's/G//' | tr -d ' ')
    if [ -n "$AVAILABLE_GB" ] && [ "$AVAILABLE_GB" -lt 10 ] 2>/dev/null; then
        print_error "Not enough disk space. You have ${AVAILABLE_GB}GB free, need at least 10GB."
        exit 1
    fi
    print_success "Disk space OK (${AVAILABLE_GB:-unknown}GB available)"

    if ! ping -c 1 github.com &>/dev/null; then
        print_error "No internet connection. Please connect and try again."
        exit 1
    fi
    print_success "Internet connection OK"
}

# ─────────────────────────────────────────
# KEYRING HEALTH CHECK
# ─────────────────────────────────────────
check_pacman_keyring() {
    print_info "Checking pacman keyring health..."

    local keyring_broken=false

    if [[ ! -d /etc/pacman.d/gnupg ]] || [[ ! -f /etc/pacman.d/gnupg/pubring.gpg ]]; then
        print_warning "Keyring directory missing or incomplete."
        keyring_broken=true
    fi

    if ! sudo pacman-key --list-keys &>/dev/null; then
        print_warning "pacman-key cannot list keys — keyring may be corrupted."
        keyring_broken=true
    fi

    if ! sudo pacman -Sy &>/dev/null; then
        print_warning "pacman sync failed — possible keyring or signature issue."
        keyring_broken=true
    fi

    if [[ "$keyring_broken" == false ]]; then
        print_success "Keyring healthy — no reset needed."
        return 0
    fi

    echo ""
    echo -e "${RED}╔══════════════════════════════════════════════════╗${NC}"
    echo -e "${RED}║${WHITE}${BOLD}          ⚠️  KEYRING RESET REQUIRED              ${NC}${RED}║${NC}"
    echo -e "${RED}╠══════════════════════════════════════════════════╣${NC}"
    echo -e "${RED}║${NC}  Your pacman keyring appears broken or corrupt.  ${RED}║${NC}"
    echo -e "${RED}║${NC}                                                  ${RED}║${NC}"
    echo -e "${RED}║${NC}  To fix it, the installer needs to:              ${RED}║${NC}"
    echo -e "${RED}║${YELLOW}    • Delete /etc/pacman.d/gnupg               ${NC}${RED}║${NC}"
    echo -e "${RED}║${YELLOW}    • Reinitialize the keyring                 ${NC}${RED}║${NC}"
    echo -e "${RED}║${YELLOW}    • Repopulate Arch + Holo (SteamOS) keys   ${NC}${RED}║${NC}"
    echo -e "${RED}║${NC}                                                  ${RED}║${NC}"
    echo -e "${RED}║${WHITE}  ⚠️  Any custom keys you added manually will   ${NC}${RED}║${NC}"
    echo -e "${RED}║${WHITE}  be removed. Re-add them after installation    ${NC}${RED}║${NC}"
    echo -e "${RED}║${WHITE}  if your system needs them.                    ${NC}${RED}║${NC}"
    echo -e "${RED}║${NC}                                                  ${RED}║${NC}"
    echo -e "${RED}║${GREEN}  This is safe for most standard Steam Decks.  ${NC}${RED}║${NC}"
    echo -e "${RED}╚══════════════════════════════════════════════════╝${NC}"
    echo ""

    echo -e "${WHITE}Type ${GREEN}yes${WHITE} to reset the keyring, or anything else to cancel: ${NC}"
    read -r confirm
    echo ""

    if [[ "$confirm" != "yes" ]]; then
        print_error "Keyring reset cancelled."
        print_info "Fix your keyring manually and re-run the installer."
        exit 1
    fi

    print_info "Resetting keyring..."
    sudo rm -rf /etc/pacman.d/gnupg
    sudo pacman-key --init
    sudo pacman-key --populate archlinux
    sudo pacman-key --populate holo
    print_success "Keyring reset complete."
    echo ""
}

# ─────────────────────────────────────────
# INSTALL DOCKER
# ─────────────────────────────────────────
install_docker() {
    if command -v docker &>/dev/null && docker ps &>/dev/null 2>&1; then
        print_success "Docker already installed and running"
        return 0
    fi

    print_info "Installing Docker..."

    if command -v steamos-readonly &>/dev/null; then
        sudo steamos-readonly disable
    fi

    check_pacman_keyring

    if command -v steamos-devmode &>/dev/null; then
        sudo steamos-devmode enable 2>/dev/null || \
            print_warning "steamos-devmode failed — continuing anyway"
    fi

    print_info "Updating archlinux-keyring..."
    if ! sudo pacman -Sy --noconfirm archlinux-keyring; then
        print_warning "archlinux-keyring update failed — Docker install may fail."
    fi

    if ! sudo pacman -Sy --noconfirm docker docker-compose; then
        print_error "Failed to install Docker. Check your internet connection and keyring."
        exit 1
    fi

    sudo usermod -aG docker "$USER"
    sleep 2

    sudo systemctl daemon-reload 2>/dev/null || \
        print_warning "systemctl daemon-reload failed — may need reboot"
    sudo systemctl enable docker 2>/dev/null || \
        print_warning "Could not enable Docker on boot"

    if ! sudo systemctl start docker 2>/dev/null; then
        print_error "Docker failed to start. Try rebooting and running the installer again."
        exit 1
    fi

    sleep 3

    print_info "Setting up Docker permissions..."
    echo "deck ALL=(ALL) NOPASSWD: /usr/bin/docker, /usr/bin/docker-compose" | \
        sudo tee /etc/sudoers.d/docker-nopasswd > /dev/null 2>&1 || true
    sudo chmod 0440 /etc/sudoers.d/docker-nopasswd 2>/dev/null || true
    sudo chmod 666 /var/run/docker.sock 2>/dev/null || true

    if ! docker ps &>/dev/null 2>&1; then
        if sudo docker ps &>/dev/null 2>&1; then
            function docker() { sudo docker "$@"; }
            export -f docker 2>/dev/null || true
            print_info "Using sudo for Docker — will work normally after next login"
        else
            print_error "Docker failed to start. Try rebooting and running again."
            exit 1
        fi
    fi

    if command -v steamos-readonly &>/dev/null; then
        sudo steamos-readonly enable 2>/dev/null || \
            print_warning "Could not re-enable steamos-readonly"
    fi

    print_success "Docker installed and permissions configured!"
}

install_git() {
    if command -v git &>/dev/null; then
        print_success "Git already installed"
        return 0
    fi
    print_info "Installing Git..."
    if sudo pacman -Sy --noconfirm git; then
        print_success "Git installed!"
    elif sudo apt-get install -y git 2>/dev/null; then
        print_success "Git installed!"
    else
        print_warning "Git installation failed."
        print_info "Try manually: sudo pacman -Sy git"
    fi
}

# ─────────────────────────────────────────
# WELCOME SCREEN
# ─────────────────────────────────────────
show_welcome() {
    print_header

    echo -e "${WHITE}Welcome to the MapleStory v83 installer!${NC}"
    echo ""
    echo -e "${WHITE}This sets up a full offline MapleStory server${NC}"
    echo -e "${WHITE}using Cosmic — the gold standard v83 emulator,${NC}"
    echo -e "${WHITE}built on over a decade of community development.${NC}"
    echo ""
    echo -e "${MSB}What is MapleStory v83?${NC}"
    echo -e "${WHITE}Version 83 is the pre-Big Bang era — the golden${NC}"
    echo -e "${WHITE}age of MapleStory. Slower leveling, real grinding,${NC}"
    echo -e "${WHITE}classic classes, and the world as most old-school${NC}"
    echo -e "${WHITE}Maplers remember it. Henesys, Perion, Kerning City,${NC}"
    echo -e "${WHITE}El Nath — all of it, completely offline.${NC}"
    echo ""
    echo -e "${YELLOW}${BOLD}⚠️  Client Files Required (2 separate files):${NC}"
    echo -e "${YELLOW}  1. Cosmic-client release ZIP — patched client + cosmic-wz/${NC}"
    echo -e "${YELLOW}     From: github.com/P0nk/Cosmic-client/releases${NC}"
    echo -e "${YELLOW}  2. MapleGlobal-v83-setup.exe — the base v83 installer${NC}"
    echo -e "${YELLOW}     From: Cosmic Discord (community-shared, ~600MB-1GB)${NC}"
    echo -e "${YELLOW}  The installer walks you through assembling them.${NC}"
    echo ""
    echo -e "${BLUE}ℹ️  Server install time: ~10-15 minutes${NC}"
    echo -e "${BLUE}ℹ️  Client runs via GE-Proton on Steam Deck${NC}"
    echo -e "${BLUE}ℹ️  Auto-register: just type any username at login!${NC}"
    echo ""

    if ! ask_yes_no "Ready to return to Maple World?"; then
        echo "No problem — run this script when you're ready!"
        exit 0
    fi
}

# ─────────────────────────────────────────
# STEP 1 — INSTALL COSMIC SERVER
# ─────────────────────────────────────────
install_server() {
    print_header
    print_step "STEP 1/4 — Installing Cosmic Server"

    print_info "Checking dependencies..."
    install_docker
    install_git

    # Remove existing installation if present
    if [ -d "$SERVER_DIR" ]; then
        print_warning "Existing MapleStory installation found at $SERVER_DIR"
        if ask_yes_no "Remove it and start fresh?"; then
            cd "$SERVER_DIR" 2>/dev/null && \
                docker compose down -v 2>/dev/null || true
            sudo rm -rf "$SERVER_DIR"
            print_success "Old installation removed"
        else
            print_info "Keeping existing installation — exiting."
            exit 0
        fi
    fi

    # Clone Cosmic — includes Dockerfile, docker-compose.yml, config.yaml,
    # wz/ XMLs, sql/ schemas, scripts/. We don't reinvent any of this.
    print_info "Cloning Cosmic from GitHub..."
    print_info "This is the full server — about 200MB..."
    if ! git clone --depth 1 \
        https://github.com/P0nk/Cosmic.git \
        "$SERVER_DIR"; then
        print_error "Failed to clone Cosmic. Check your internet connection."
        exit 1
    fi
    print_success "Cosmic cloned!"

    cd "$SERVER_DIR" || { print_error "Cannot cd to $SERVER_DIR"; exit 1; }

    # ────────────────────────────────────────────────────────────────
    # Verify Cosmic ships the files we depend on. If they're missing,
    # upstream changed structure and we should bail rather than guess.
    # ────────────────────────────────────────────────────────────────
    if [ ! -f "$SERVER_DIR/config.yaml" ]; then
        print_error "Cosmic's config.yaml not found! Upstream may have changed."
        print_info "Check: https://github.com/P0nk/Cosmic"
        exit 1
    fi
    if [ ! -f "$SERVER_DIR/docker-compose.yml" ]; then
        print_error "Cosmic's docker-compose.yml not found! Upstream may have changed."
        exit 1
    fi
    if [ ! -f "$SERVER_DIR/Dockerfile" ]; then
        print_error "Cosmic's Dockerfile not found! Upstream may have changed."
        exit 1
    fi
    print_success "Cosmic's Docker config files found"

    # ────────────────────────────────────────────────────────────────
    # Surgically patch Cosmic's config.yaml
    #
    # We make MINIMAL changes — Cosmic's defaults are good for offline
    # play. We only need to:
    #   1. Set DB_PASS to match docker-compose's MYSQL_ROOT_PASSWORD
    #   2. Disable PIN/PIC prompts (extra password screens dads don't want)
    #
    # Everything else (server_ip, ports, rates, auto_register, worlds,
    # CHANNEL_SIZE, etc) stays at Cosmic's defaults.
    # ────────────────────────────────────────────────────────────────
    print_info "Patching Cosmic config.yaml (minimal changes)..."

    # DB_PASS — set to our generated password
    if grep -qE "^\s*DB_PASS:" "$SERVER_DIR/config.yaml"; then
        # Cosmic indents config keys, so we use a permissive regex
        sed -i "s|^\([[:space:]]*\)DB_PASS:.*|\1DB_PASS: \"${DB_PASSWORD}\"|" \
            "$SERVER_DIR/config.yaml"
        print_success "DB_PASS set"
    else
        print_warning "DB_PASS line not found in config.yaml — auth may fail"
        print_info "Cosmic's config schema may have changed — check manually"
    fi

    # ENABLE_PIN — disable PIN prompt
    if grep -qE "^\s*ENABLE_PIN:" "$SERVER_DIR/config.yaml"; then
        sed -i "s|^\([[:space:]]*\)ENABLE_PIN:.*|\1ENABLE_PIN: false|" \
            "$SERVER_DIR/config.yaml"
        print_success "ENABLE_PIN disabled"
    fi

    # ENABLE_PIC — disable PIC prompt
    if grep -qE "^\s*ENABLE_PIC:" "$SERVER_DIR/config.yaml"; then
        sed -i "s|^\([[:space:]]*\)ENABLE_PIC:.*|\1ENABLE_PIC: false|" \
            "$SERVER_DIR/config.yaml"
        print_success "ENABLE_PIC disabled"
    fi

    # WLDLIST_SIZE — cap to 1 world so only Scania appears on world select
    if grep -qE "^\s*WLDLIST_SIZE:" "$SERVER_DIR/config.yaml"; then
        sed -i "s|^\([[:space:]]*\)WLDLIST_SIZE:.*|\1WLDLIST_SIZE: 1                     #Max possible worlds on the server.|" \
            "$SERVER_DIR/config.yaml"
        print_success "WLDLIST_SIZE set to 1 (Scania only)"
    fi

    # Worlds list — remove worlds 1-20, keep only Scania
    # Cosmic ships 21 worlds; solo offline only needs Scania (world 0).
    # awk: skip from the Bera comment to (but not including) "server:"
    if grep -q "#Properties for Bera" "$SERVER_DIR/config.yaml"; then
        awk '
            /^    #Properties for Bera/ { skip=1 }
            /^server:/ { skip=0 }
            !skip { print }
        ' "$SERVER_DIR/config.yaml" > /tmp/cosmic-config-patched.yaml \
            && mv /tmp/cosmic-config-patched.yaml "$SERVER_DIR/config.yaml"
        print_success "World list trimmed to Scania only"
    fi

    # ────────────────────────────────────────────────────────────────
    # Sync MYSQL_ROOT_PASSWORD in Cosmic's docker-compose with our DB_PASS
    #
    # Cosmic's compose has a default password we need to override so it
    # matches what we just put in config.yaml.
    # ────────────────────────────────────────────────────────────────
    if grep -q "MYSQL_ROOT_PASSWORD" "$SERVER_DIR/docker-compose.yml"; then
        print_info "Syncing MySQL password with config.yaml..."
        sed -i "s|MYSQL_ROOT_PASSWORD:.*|MYSQL_ROOT_PASSWORD: ${DB_PASSWORD}|" \
            "$SERVER_DIR/docker-compose.yml"
        print_success "MySQL password synced"
    else
        print_warning "MYSQL_ROOT_PASSWORD not in docker-compose.yml"
        print_info "If server can't reach DB, check both files manually"
    fi

    # ────────────────────────────────────────────────────────────────
    # Build and start Cosmic via its own docker-compose
    # ────────────────────────────────────────────────────────────────
    print_info "Building Cosmic via Docker (Java compile — 3-5 min)..."
    print_info "Coffee time! ☕  Build log: /tmp/maple-build.log"

    docker compose build 2>&1 | tee /tmp/maple-build.log | \
        grep -E "^Step|BUILD|Successfully|error|ERROR|Cosmic" || true
    if [ "${PIPESTATUS[0]}" -ne 0 ]; then
        print_error "Cosmic build failed!"
        print_info "Full log: /tmp/maple-build.log"
        exit 1
    fi
    print_success "Cosmic built!"

    # Start the stack
    print_info "Starting MapleStory server..."
    if ! docker compose up -d; then
        print_error "Failed to start server."
        print_info "Check: docker compose logs"
        exit 1
    fi
    print_success "Server containers started!"

    # ────────────────────────────────────────────────────────────────
    # Wait for Cosmic to be ready
    # Ready signal "Cosmic is now online" confirmed from upstream README
    # Auto-discover the server container name (don't hardcode — Cosmic's
    # compose may use project-prefixed naming like cosmic-server-1)
    # ────────────────────────────────────────────────────────────────
    print_info "Waiting for Cosmic to initialize..."
    print_info "First launch builds the database — takes 3-5 minutes..."
    echo ""

    local maple_container=""
    TIMEOUT=600
    ELAPSED=0
    READY=0

    while [ $ELAPSED -lt $TIMEOUT ]; do
        # Find Cosmic server container — exclude DB-related ones
        maple_container=$(docker ps --format '{{.Names}}' 2>/dev/null | \
            grep -iE "cosmic|maple" | \
            grep -v -iE "db|mysql|adminer|phpmyadmin|maria" | \
            head -1)

        if [ -n "$maple_container" ] && \
           docker logs "$maple_container" 2>/dev/null | \
           grep "Cosmic is now online" > /dev/null; then
            READY=1
            break
        fi
        printf "."
        sleep 10
        ELAPSED=$((ELAPSED + 10))
    done

    echo ""
    echo ""

    if [ $READY -eq 1 ]; then
        print_success "Cosmic is online! 🍁"
        print_info "Server container: $maple_container"
        # Save container name for the launcher to use later
        echo "$maple_container" > "$SERVER_DIR/.dml-server-container"
    else
        print_warning "Server is taking longer than expected."
        print_info "Check: docker compose logs -f"
        print_info "Wait for 'Cosmic is now online' then continue."
        press_enter
    fi
}

# ─────────────────────────────────────────
# STEP 2 — CLIENT FILES GUIDE
# ─────────────────────────────────────────
guide_client_setup() {
    print_header
    print_step "STEP 2/4 — Getting Your Client Files"

    echo ""
    echo -e "${WHITE}${BOLD}MapleStory v83 needs three things to run:${NC}"
    echo ""

    echo -e "${MS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD} FILE 1 — v83 Base Client Installer${NC}"
    echo -e "${MS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  ${WHITE}Filename: ${MS}MapleGlobal-v83-setup.exe${NC}"
    echo -e "  ${WHITE}Get it from the Cosmic GitHub README:${NC}"
    echo -e "  ${CYAN}https://github.com/P0nk/Cosmic${NC}"
    echo -e "  ${BLUE}ℹ️  Look for 'Download MapleGlobal-v83-setup.exe'${NC}"
    echo -e "  ${BLUE}ℹ️  Links to Ponk's Google Drive${NC}"
    echo ""
    echo -e "  ${WHITE}Install it via Proton on Steam Deck, then:${NC}"
    echo -e "  ${YELLOW}DELETE these files from the install folder:${NC}"
    echo -e "  ${RED}  • HShield/ (entire folder)${NC}"
    echo -e "  ${RED}  • ASPLnchr.exe${NC}"
    echo -e "  ${RED}  • MapleStory.exe${NC}"
    echo -e "  ${RED}  • Patcher.exe${NC}"
    echo ""

    echo -e "${MS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD} FILE 2 — Cosmic WZ Files${NC}"
    echo -e "${MS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  ${WHITE}Filename: ${MS}CosmicWZ-[date]-v[version].zip${NC}"
    echo -e "  ${WHITE}Also from the Cosmic GitHub README:${NC}"
    echo -e "  ${CYAN}https://github.com/P0nk/Cosmic${NC}"
    echo -e "  ${BLUE}ℹ️  Look for 'Download CosmicWZ'${NC}"
    echo ""
    echo -e "  ${WHITE}Extract ALL .wz files into your MapleStory install folder.${NC}"
    echo -e "  ${WHITE}Replace existing files when prompted.${NC}"
    echo ""

    echo -e "${MS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD} FILE 3 — Localhost Client Executable${NC}"
    echo -e "${MS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  ${WHITE}Filename: ${MS}HeavenMS-localhost-WINDOW.exe${NC}"
    echo -e "  ${WHITE}Also from the Cosmic GitHub README (hostr.co link)${NC}"
    echo -e "  ${CYAN}https://github.com/P0nk/Cosmic${NC}"
    echo ""
    echo -e "  ${YELLOW}⚠️  Your antivirus WILL flag this file.${NC}"
    echo -e "  ${YELLOW}  This is a FALSE POSITIVE — it's flagged because it's${NC}"
    echo -e "  ${YELLOW}  a reverse-engineered exe. The MapleStory community${NC}"
    echo -e "  ${YELLOW}  has used this file safely for years.${NC}"
    echo -e "  ${YELLOW}  Add an exclusion in your antivirus before downloading.${NC}"
    echo ""
    echo -e "  ${WHITE}Copy this exe into your MapleStory install folder.${NC}"
    echo -e "  ${WHITE}This is the exe you'll use to launch the game!${NC}"
    echo ""

    echo -e "${MS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD} WZ FILES for the Server${NC}"
    echo -e "${MS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  ${WHITE}The server also needs WZ XML files in its wz/ folder.${NC}"
    echo -e "  ${WHITE}These are auto-generated from the Cosmic WZ files.${NC}"
    echo ""
    echo -e "  ${WHITE}After getting the Cosmic WZ zip, the server's wz/ folder${NC}"
    echo -e "  ${WHITE}at ${MS}$SERVER_DIR/wz/${NC}${WHITE} needs to be populated.${NC}"
    echo -e "  ${WHITE}See the Cosmic README for the HaRepacker export step.${NC}"
    echo -e "  ${CYAN}https://github.com/P0nk/Cosmic${NC}"
    echo ""
    echo -e "${MS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "${GREEN}Once all three files are in place, come back here!${NC}"
    echo ""

    press_enter
}

# ─────────────────────────────────────────
# STEP 3 — PROTON SETUP GUIDE
# ─────────────────────────────────────────
guide_proton_setup() {
    print_header
    print_step "STEP 3/4 — Steam Deck Client Setup (GE-Proton)"

    echo ""
    echo -e "${WHITE}MapleStory v83 uses old DirectX 8 graphics.${NC}"
    echo -e "${WHITE}On Steam Deck it needs GE-Proton + DXVK to run.${NC}"
    echo -e "${WHITE}This is different from standard Proton!${NC}"
    echo ""

    echo -e "${MS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD} STEP A — Install GE-Proton${NC}"
    echo -e "${MS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  1. Open the ${MS}Discover${NC} app in Desktop Mode"
    echo -e "  2. Search for ${MS}ProtonUp-Qt${NC} and install it"
    echo -e "  3. Open ProtonUp-Qt"
    echo -e "  4. Click ${MS}Add Version${NC}"
    echo -e "  5. Select ${MS}GE-Proton${NC} → choose the latest version"
    echo -e "  6. Click ${MS}Install${NC} and wait"
    echo ""

    echo -e "${MS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD} STEP B — Add Client to Steam${NC}"
    echo -e "${MS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  1. Open Steam in Desktop Mode"
    echo -e "  2. Click ${CYAN}Games${NC} → ${CYAN}Add a Non-Steam Game${NC}"
    echo -e "  3. Click ${CYAN}Browse${NC} → navigate to your MapleStory folder"
    echo -e "  4. Select ${MS}HeavenMS-localhost-WINDOW.exe${NC}"
    echo -e "  5. Click ${CYAN}Add Selected Programs${NC}"
    echo ""

    echo -e "${MS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD} STEP C — Enable GE-Proton for MapleStory${NC}"
    echo -e "${MS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  1. Find ${MS}HeavenMS-localhost-WINDOW${NC} in your Steam library"
    echo -e "  2. Right-click → ${CYAN}Properties${NC}"
    echo -e "  3. Click the ${CYAN}Compatibility${NC} tab"
    echo -e "  4. Check ${GREEN}Force the use of a specific compatibility tool${NC}"
    echo -e "  5. Select ${MS}GE-Proton${NC} (the version you just installed)"
    echo -e "  ${YELLOW}  ⚠️  Do NOT use standard Proton — use GE-Proton!${NC}"
    echo -e "  ${YELLOW}  Standard Proton lacks the D3D8 support MapleStory needs.${NC}"
    echo ""

    echo -e "${MS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD} STEP D — Launch Options${NC}"
    echo -e "${MS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  ${WHITE}MapleStory v83 runs at 800×600. In Gaming Mode, Steam${NC}"
    echo -e "  ${WHITE}scales it to fill the Deck screen for you — so just:${NC}"
    echo ""
    echo -e "  1. Find ${MS}HeavenMS-localhost-WINDOW${NC} → Properties → General"
    echo -e "  2. Set ${CYAN}Launch Options${NC} to:"
    echo ""
    echo -e "  ${GREEN}%command%${NC}"
    echo ""
    echo -e "  ${YELLOW}⚠️  Do NOT add a gamescope option for Gaming Mode play.${NC}"
    echo -e "  ${YELLOW}   Gaming Mode is already a gamescope session; nesting${NC}"
    echo -e "  ${YELLOW}   another inside it can HANG the client at startup${NC}"
    echo -e "  ${YELLOW}   (and Gaming Mode scales 800×600 perfectly on its own).${NC}"
    echo ""
    echo -e "  ${BLUE}ℹ️  DESKTOP MODE only (no compositor to scale for you):${NC}"
    echo -e "     ${DIM}gamescope -w 800 -h 600 -W 1280 -H 800 -f -- %command%${NC}"
    echo ""

    echo -e "${MS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD} STEP E — Creating Your Account${NC}"
    echo -e "${MS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  ${WHITE}${BOLD}MapleStory auto-registers accounts!${NC}"
    echo -e "  ${WHITE}At the login screen, just type:${NC}"
    echo -e "  ${MS}  Any username you want${NC}"
    echo -e "  ${MS}  Any password you want${NC}"
    echo -e "  ${WHITE}The account is created automatically on first login.${NC}"
    echo -e "  ${GREEN}No GM console, no database commands needed!${NC}"
    echo ""

    press_enter
}

# ─────────────────────────────────────────
# STEP 4 — GAMING MODE LAUNCHER
# ─────────────────────────────────────────
setup_gaming_mode() {
    print_header
    print_step "STEP 4/4 — Setting Up Gaming Mode Launcher"

    local launcher_path="$HOME/maplestory-launcher.sh"
    local server_dir="$SERVER_DIR"

    cat > "$launcher_path" << LAUNCHER
#!/bin/bash
# Dad's MMO Lab — MapleStory v83 Launcher v${INSTALLER_VERSION}
export PATH="/usr/bin:/usr/local/bin:/bin:\$PATH"
unset LD_PRELOAD
unset LD_LIBRARY_PATH

LOGFILE="/tmp/maple-launch.log"
exec 2>"\$LOGFILE"

clear
echo ""
echo "  🍁 DAD'S MMO LAB — MapleStory v83"
echo "  ══════════════════════════════════════"
echo "  Powered by Cosmic"
echo "  ══════════════════════════════════════"
echo ""
echo "  Starting server..."
echo ""

# Stop any existing MapleStory containers (Cosmic uses project-prefixed names)
MS_CONTAINERS=\$(docker ps --format '{{.Names}}' 2>/dev/null | \
    grep -iE "cosmic|maple" || true)

if [ -n "\$MS_CONTAINERS" ]; then
    echo "  Stopping existing MapleStory server..."
    echo "\$MS_CONTAINERS" | xargs docker stop >> "\$LOGFILE" 2>&1 || true
    sleep 3
    echo "  All clear!"
    echo ""
fi

cd "${server_dir}" || exit 1

if docker compose up -d >> "\$LOGFILE" 2>&1; then
    echo "  Containers started!"
else
    echo "  ERR: Failed to start server."
    echo "  Check: \$LOGFILE"
    sleep 10
    exit 1
fi

echo ""
echo "  Waiting for Maple World to open..."
echo "  First launch: 3-5 minutes"
echo "  After first launch: ~30 seconds"
echo ""

TIMEOUT=300
ELAPSED=0
READY=0
MAPLE_C=""

while [ \$ELAPSED -lt \$TIMEOUT ]; do
    # Auto-discover Cosmic server container (saved name from install OR fresh detect)
    if [ -f "${server_dir}/.dml-server-container" ]; then
        MAPLE_C=\$(cat "${server_dir}/.dml-server-container" 2>/dev/null)
    fi
    if [ -z "\$MAPLE_C" ] || ! docker ps --format '{{.Names}}' | grep -q "^\${MAPLE_C}\$"; then
        MAPLE_C=\$(docker ps --format '{{.Names}}' 2>/dev/null | \
            grep -iE "cosmic|maple" | \
            grep -v -iE "db|mysql|adminer|phpmyadmin|maria" | head -1)
    fi

    if [ -n "\$MAPLE_C" ] && docker logs "\$MAPLE_C" 2>/dev/null | \
        grep -q "Cosmic is now online"; then
        READY=1
        break
    fi
    printf "  ."
    sleep 5
    ELAPSED=\$((ELAPSED + 5))
done

echo ""
echo ""

if [ \$READY -eq 1 ]; then
    echo "  ══════════════════════════════════════"
    echo "  ✅ MAPLE WORLD IS OPEN! 🍁"
    echo "  ══════════════════════════════════════"
else
    echo "  ⏳ Still initializing — launch MapleStory soon"
fi

echo ""
echo "  Server: 127.0.0.1:8484"
echo "  Just type any username + password to register!"
echo ""
echo "  Press STEAM button and launch MapleStory"
echo "  (HeavenMS-localhost-WINDOW via GE-Proton)"
echo "  Server AUTO-SHUTS DOWN when MapleStory closes"
echo ""

# Wait for MapleStory client process. Dual signal:
#  - HeavenMS-localhost-WINDOW: the client exe — primary for Gaming Mode play
#    (no gamescope launch option), confirmed host-visible under GE-Proton.
#  - gamescope w/ our params: covers Desktop Mode play (where a gamescope launch
#    option IS used to scale). Either match works.
MS_STARTED=0
for i in \$(seq 1 60); do
    if pgrep -f "gamescope.*-w 800 -h 600|HeavenMS-localhost-WINDOW" \
        > /dev/null 2>&1; then
        MS_STARTED=1
        break
    fi
    sleep 5
done

if [ \$MS_STARTED -eq 1 ]; then
    echo "  MapleStory detected! Welcome back to Maple World! 🍁"
    while pgrep -f "gamescope.*-w 800 -h 600|HeavenMS-localhost-WINDOW" \
        > /dev/null 2>&1; do
        sleep 3
    done
    sleep 5
    echo "  MapleStory closed — shutting down server..."
else
    echo "  MapleStory not detected — keeping server alive for 3 hours."
    echo "  (If you see this, the launcher couldn't detect your client."
    echo "   Common cause: client EXE was renamed — see github issues.)"
    sleep 10800
fi

cd "${server_dir}" && docker compose down >> "\$LOGFILE" 2>&1

echo ""
echo "  ✅ Server stopped! Safe to close."
echo "  Thanks for playing! youtube.com/@DadsMmoLab"
echo ""
sleep 5
LAUNCHER

    chmod +x "$launcher_path"
    print_success "Gaming Mode launcher created: ~/maplestory-launcher.sh"

    # Save info file
    cat > "$SERVER_DIR/MY_SERVER.txt" << INFO
====================================
  Dad's MMO Lab — MapleStory v83
  Powered by Cosmic
====================================

SERVER:
  IP:         127.0.0.1
  Login Port: 8484
  Channels:   7575-7577

ACCOUNTS:
  Auto-register is ON!
  Just type any username + password
  at the login screen — done!

CLIENT:
  Executable:     HeavenMS-localhost-WINDOW.exe
  Proton:         GE-Proton (NOT standard Proton)
  D3D8:           Handled automatically by DXVK in GE-Proton
  Launch Options: %command%
                  (Gaming Mode scales 800x600 to the Deck screen on its own.
                   Do NOT add a gamescope option in Gaming Mode — nesting it
                   inside Gaming Mode's compositor can hang the client.
                   Desktop Mode only, to scale there:
                   gamescope -w 800 -h 600 -W 1280 -H 800 -f -- %command%)

====================================
  Useful Commands
====================================
Start:  cd ${SERVER_DIR} && docker compose up -d
Stop:   cd ${SERVER_DIR} && docker compose down
Logs:   cd ${SERVER_DIR} && docker compose logs -f
Status: docker ps

====================================
  Gaming Mode Setup
====================================
Add to Steam:
  Target:  /usr/bin/konsole
  Options: --hold -e bash ~/maplestory-launcher.sh
  Proton:  OFF (server launcher needs no Proton)

Add MapleStory client separately:
  Target:  HeavenMS-localhost-WINDOW.exe
  Proton:  GE-Proton (required!)
====================================
INFO

    print_success "Server info saved: $SERVER_DIR/MY_SERVER.txt"
}

# ─────────────────────────────────────────
# COMPLETION
# ─────────────────────────────────────────
show_completion() {
    echo ""
    echo -e "${MSB}╔══════════════════════════════════════════════════╗${NC}"
    echo -e "${MSB}║   🍁 MAPLE WORLD AWAITS YOU!                     ║${NC}"
    echo -e "${MSB}╚══════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  ${WHITE}${BOLD}Server:${NC}   ${MS}Cosmic MapleStory v83${NC}"
    echo -e "  ${WHITE}${BOLD}Folder:${NC}   ${MS}$SERVER_DIR${NC}"
    echo -e "  ${WHITE}${BOLD}Launcher:${NC} ${MS}~/maplestory-launcher.sh${NC}"
    echo ""

    echo -e "${MS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD} QUICK CHECKLIST${NC}"
    echo -e "${MS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  ${WHITE}Before playing, make sure you have:${NC}"
    echo ""
    echo -e "  ${YELLOW}□${NC} Downloaded + installed MapleGlobal-v83-setup.exe"
    echo -e "  ${YELLOW}□${NC} Deleted HShield/, ASPLnchr.exe, MapleStory.exe, Patcher.exe"
    echo -e "  ${YELLOW}□${NC} Replaced WZ files with Cosmic WZ files"
    echo -e "  ${YELLOW}□${NC} Copied HeavenMS-localhost-WINDOW.exe to MapleStory folder"
    echo -e "  ${YELLOW}□${NC} Populated ${MS}$SERVER_DIR/wz/${NC} with server WZ XMLs"
    echo -e "  ${YELLOW}□${NC} Installed GE-Proton via ProtonUp-Qt"
    echo -e "  ${YELLOW}□${NC} Added HeavenMS-localhost-WINDOW.exe to Steam with GE-Proton"
    echo -e "  ${YELLOW}□${NC} Set Launch Options on HeavenMS-localhost-WINDOW to ${GREEN}%command%${NC}"
    echo -e "     ${DIM}(no gamescope in Gaming Mode — it can hang the client)${NC}"
    echo ""
    echo -e "  ${WHITE}All files from: ${CYAN}https://github.com/P0nk/Cosmic${NC}"
    echo ""

    echo -e "${MS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD} GAMING MODE SERVER LAUNCHER${NC}"
    echo -e "${MS}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  1. Open Steam in Desktop Mode"
    echo -e "  2. Click ${CYAN}Games${NC} → ${CYAN}Add a Non-Steam Game${NC}"
    echo -e "  3. Browse to ${CYAN}/usr/bin/${NC} → select ${CYAN}konsole${NC}"
    echo -e "  4. Find konsole → right-click → Properties"
    echo -e "  5. Rename: ${GREEN}MapleStory Server${NC}"
    echo -e "  6. Launch Options:"
    echo ""
    echo -e "  ${GREEN}--hold -e bash ~/maplestory-launcher.sh${NC}"
    echo ""
    echo -e "  7. ${RED}Do NOT enable Proton${NC} for the server launcher"
    echo -e "     ${YELLOW}(GE-Proton goes on MapleStory.exe, not the launcher)${NC}"
    echo ""

    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}  📺 youtube.com/@DadsMmoLab${NC}"
    echo -e "${WHITE}  📦 github.com/DadsMmoLab/dads-mmo-lab${NC}"
    echo -e "${WHITE}  ☕ ko-fi.com/dadsmmolab${NC}"
    echo -e "${WHITE}  🍁 Cosmic: github.com/P0nk/Cosmic${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "${MSB}Welcome back to Maple World! 🍁${NC}"
    echo ""

    echo -e "${WHITE}Would you like to stop the server now? (y/n): ${NC}"
    read -r STOP_NOW
    if [[ "$STOP_NOW" =~ ^[Yy]$ ]]; then
        print_info "Stopping server..."
        cd "$SERVER_DIR" && docker compose down
        print_success "Server stopped! Use Gaming Mode launcher to start it next time."
    else
        print_info "Server left running — Maple World is open! 🍁"
    fi
    echo ""
}

# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
check_system
show_welcome
install_server
guide_client_setup
guide_proton_setup
setup_gaming_mode
show_completion
