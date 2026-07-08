#!/bin/bash
# ============================================================
#  Dad's MMO Lab — WoW Playerbots Server Installer
#  AzerothCore WotLK + Playerbots (compiled from source)
#
#  https://github.com/DadsMmoLab/dads-mmo-lab
#
#  Version: 1.3.2 - Fedora
#
#  Usage:
#    chmod +x install-wow.sh
#    ./install-wow.sh
#
#  What this does:
#    1. Installs Docker and Git if needed
#    2. Shows a summary before building
#    3. Compiles AzerothCore + Playerbots (~2-4 hours)
#    4. Waits for the world server to initialize
#    5. Guides you through account creation
#    6. Sets up the Gaming Mode launcher
#
#  Changelog:
#    1.3.2 — Bazzite docker group / sudo fix
#      - Bazzite ships docker-ce (not moby-engine) in @System. After
#        systemctl enable --now docker, the daemon is running but the user
#        is not in the docker group yet, so unprivileged `docker ps` returns
#        permission denied. Script fell through to rpm-ostree install which
#        conflicted with @System packages. Fix: use `sudo docker ps` and
#        `sudo docker compose version` for the immutable early-out check.
#        If both pass, add user to docker group and return 0 — no
#        rpm-ostree install attempted.
#    1.3.1 — Bazzite pre-bundled Docker fix
#      - On immutable systems, Docker is part of the Bazzite base image and
#        is not a layered package. The daemon just isn't started yet. The
#        previous check (docker ps) required the daemon to be running, so it
#        fell through to rpm-ostree install, which fatally conflicted with
#        @System. Fix: on immutable systems, try systemctl enable --now docker
#        first if the binary exists, then re-check before attempting any
#        rpm-ostree install.
#      - Added --idempotent flag to rpm-ostree install calls to avoid
#        conflicts when packages are already provided by the base image.
#      - Compose plugin missing fallback now correctly uses rpm-ostree on
#        immutable systems instead of dnf.
#    1.3.0 — Fedora / Bazzite port
#      - Replaced pacman/Arch package management with dnf (Fedora)
#      - Removed check_pacman_keyring() — not applicable on Fedora
#      - Removed steamos-readonly / steamos-devmode calls
#      - Docker installed via official Docker CE repo for Fedora
#      - install_git() now uses dnf
#      - Removed hardcoded "deck" sudoers entry; uses $USER
#      - Removed Steam Deck hardware-specific messaging
#    1.2.0 — Playerbots-only focus
#      - Removed Base WoW and NPCBots options
#      - Single clear install path: Playerbots, compiled from source
#      - Fixed DB container name discovery (was hardcoded, broke on
#        non-default install dirs)
#      - Replaced sleep 15 DB wait with real connection polling
#    1.1.0 — Error handling overhaul
#      - Keyring reset now checks health first and requires confirmation
#      - install_docker() surfaces real errors instead of silencing them
#      - install_git() no longer reports success on failure
#      - SQL apply loops track and report failures
#      - systemctl start docker exits cleanly on failure
#      - Heredoc launcher synced with standalone launcher scripts
# ============================================================

WIZARD_VERSION="1.3.2 - Fedora"

set -o pipefail

# ─────────────────────────────────────────
# COLORS
# ─────────────────────────────────────────
RST='\033[0m'; BOLD='\033[1m'
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; WHITE='\033[1;37m'; CYAN='\033[0;36m'
MAGENTA='\033[0;35m'; NC='\033[0m'
GOLD='\033[38;5;220m'; DIM='\033[2m'

print_header() {
    clear
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${WHITE}${BOLD}         ⚙️  DAD'S MMO LAB                        ${NC}${CYAN}║${NC}"
    echo -e "${CYAN}║${WHITE}         WoW Playerbots Installer                 ${NC}${CYAN}║${NC}"
    echo -e "${CYAN}║${BLUE}         github.com/DadsMmoLab/dads-mmo-lab       ${NC}${CYAN}║${NC}"
    echo -e "${CYAN}║${YELLOW}         Version ${WIZARD_VERSION}                              ${NC}${CYAN}║${NC}"
    echo -e "${CYAN}╚══════════════════════════════════════════════════╝${NC}"
    echo ""
}

print_step() {
    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD} $1${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
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
SERVER_DIR="$HOME/wow-server-playerbots"
# Terminal detection — set globally so setup_gaming_mode and show_completion share state
TERM_BIN=""
TERM_ARGS=""

# ─────────────────────────────────────────
# SYSTEM CHECKS
# ─────────────────────────────────────────
check_system() {
    print_step "Checking System Requirements"

    if [[ "$OSTYPE" != "linux-gnu"* ]]; then
        print_error "This script supports Fedora-based Linux only (Fedora, Bazzite)."
        exit 1
    fi
    print_success "Linux detected"

    # Verify this is a Fedora-family distro
    if [[ -f /etc/os-release ]]; then
        source /etc/os-release
        if [[ "$ID" != "fedora" && "$ID_LIKE" != *"fedora"* && "$ID" != "bazzite" ]]; then
            print_error "Unsupported distro: $PRETTY_NAME"
            print_info "This script is for Fedora or Fedora-based distros (e.g., Bazzite)."
            exit 1
        fi
        print_success "Fedora-family distro detected: ${PRETTY_NAME:-$ID}"
    else
        print_warning "Could not read /etc/os-release — proceeding at your own risk."
    fi

    # Detect immutable/Bazzite (rpm-ostree)
    if command -v rpm-ostree &>/dev/null; then
        FEDORA_IMMUTABLE=true
        print_info "Immutable Fedora (Bazzite / rpm-ostree) detected."
    else
        FEDORA_IMMUTABLE=false
    fi

    # ── Confirm detected package manager path with user ───────────────
    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD} Detected System Type${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    if [[ "$FEDORA_IMMUTABLE" == "true" ]]; then
        echo -e "  ${GREEN}✅ Bazzite / Immutable Fedora${NC}"
        echo -e "  ${WHITE}Package manager: ${CYAN}rpm-ostree${NC}"
        echo -e "  ${DIM}Docker will be layered via rpm-ostree (requires reboot)${NC}"
    else
        echo -e "  ${GREEN}✅ Standard Fedora${NC}"
        echo -e "  ${WHITE}Package manager: ${CYAN}dnf${NC}"
        echo -e "  ${DIM}Docker will be installed via dnf + Docker CE repo${NC}"
    fi
    echo ""
    echo -e "  ${YELLOW}Is this correct?${NC}"
    echo -e "  ${DIM}(If wrong, press Ctrl+C to exit and check your distro)${NC}"
    echo ""
    if ! ask_yes_no "Continue with the detected system type?"; then
        echo ""
        print_error "Aborted. Re-run once you've confirmed your distro."
        print_info "Expected: Fedora (dnf) or Bazzite/Immutable Fedora (rpm-ostree)"
        exit 1
    fi

    AVAILABLE_GB=$(df -BG "$HOME" 2>/dev/null | awk 'NR==2 {print $4}' | sed 's/G//' | tr -d ' ')
    if [ -n "$AVAILABLE_GB" ] && [ "$AVAILABLE_GB" -lt 15 ] 2>/dev/null; then
        print_error "Not enough disk space. You have ${AVAILABLE_GB}GB free, need at least 15GB."
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
# INSTALL DOCKER
# ─────────────────────────────────────────
install_docker() {
    # ── On immutable systems (Bazzite), Docker CE is pre-bundled in the base
    #    OS image as part of @System — it's not a layered package. The binary
    #    exists but the daemon hasn't been started and the user isn't in the
    #    docker group yet. Start the service and verify with sudo before
    #    attempting any rpm-ostree install (which would conflict with @System).
    if [[ "${FEDORA_IMMUTABLE:-false}" == "true" ]] && command -v docker &>/dev/null; then
        print_info "Docker binary found on immutable system — enabling and starting service..."
        sudo systemctl enable --now docker 2>/dev/null || true
        sleep 3
        # Use sudo: user isn't in docker group yet so unprivileged docker ps fails
        if sudo docker ps &>/dev/null 2>&1 && sudo docker compose version &>/dev/null 2>&1; then
            print_success "Docker is pre-installed and running (Bazzite base image)."
            sudo usermod -aG docker "$USER" 2>/dev/null || true
            return 0
        fi
    fi

    # Check for working Docker with Compose plugin
    if command -v docker &>/dev/null && docker ps &>/dev/null 2>&1; then
        if docker compose version &>/dev/null 2>&1; then
            print_success "Docker (with Compose plugin) already installed and running"
            return 0
        else
            print_warning "Docker is running but the Compose plugin is missing."
            print_info "Attempting to install docker-compose-plugin..."
            # On immutable systems use rpm-ostree; on plain Fedora use dnf
            if [[ "${FEDORA_IMMUTABLE:-false}" == "true" ]]; then
                if sudo rpm-ostree install -y --idempotent docker-compose-plugin 2>/dev/null || \
                   sudo rpm-ostree install -y --idempotent docker-compose 2>/dev/null; then
                    print_success "docker-compose-plugin layered. Rebooting in 10 seconds — re-run this script after reboot."
                    sleep 10
                    sudo systemctl reboot
                    exit 0
                else
                    print_error "Could not install docker-compose-plugin via rpm-ostree."
                    exit 1
                fi
            else
                if sudo dnf -y install docker-compose-plugin; then
                    print_success "docker-compose-plugin installed!"
                    return 0
                else
                    print_error "Could not install docker-compose-plugin. Check your Docker CE repo setup."
                    exit 1
                fi
            fi
        fi
    fi

    print_info "Installing Docker..."

    if [[ "${FEDORA_IMMUTABLE:-false}" == "true" ]]; then
        # ── Bazzite / immutable Fedora path (rpm-ostree) ─────────────────
        # Only reached if docker binary was not found above (truly not installed).
        print_info "Immutable system detected — installing Docker via rpm-ostree..."
        print_warning "This will require a REBOOT to take effect."
        echo ""
        echo -e "${YELLOW}  rpm-ostree will layer Docker onto your system image.${NC}"
        echo -e "${YELLOW}  After installation you MUST reboot, then re-run this script.${NC}"
        echo ""
        if ! ask_yes_no "Install Docker via rpm-ostree and reboot now?"; then
            print_info "Skipped. Re-run after manually installing Docker."
            exit 0
        fi

        if ! sudo rpm-ostree install -y --idempotent docker docker-compose; then
            print_error "rpm-ostree Docker install failed. Check your connection and try again."
            exit 1
        fi

        print_success "Docker layered. Rebooting in 10 seconds — re-run this script after reboot."
        sleep 10
        sudo systemctl reboot
        exit 0
    else
        # ── Plain Fedora path (dnf) ───────────────────────────────────────
        # Remove conflicting packages (e.g. podman-docker, moby-engine) before installing CE
        print_info "Removing any conflicting Docker packages..."
        for pkg in docker docker-client docker-client-latest docker-common \
                   docker-latest docker-latest-logrotate docker-logrotate \
                   docker-selinux docker-engine-selinux docker-engine moby-engine; do
            sudo dnf -y remove "$pkg" 2>/dev/null || true
        done

        print_info "Installing dnf-plugins-core..."
        if ! sudo dnf -y install dnf-plugins-core; then
            print_error "Failed to install dnf-plugins-core."
            exit 1
        fi

        # Add Docker CE repo — use direct curl download so it works on both
        # dnf4 (Fedora ≤40) and dnf5 (Fedora 41+, where config-manager syntax changed)
        print_info "Adding Docker CE repository..."
        if ! sudo curl -fsSL \
                https://download.docker.com/linux/fedora/docker-ce.repo \
                -o /etc/yum.repos.d/docker-ce.repo; then
            print_error "Failed to download Docker CE repo file. Check your internet connection."
            exit 1
        fi

        print_info "Installing Docker CE..."
        if ! sudo dnf -y install \
                docker-ce docker-ce-cli containerd.io \
                docker-buildx-plugin docker-compose-plugin; then
            print_error "Failed to install Docker. Check your internet connection."
            exit 1
        fi
    fi

    sudo usermod -aG docker "$USER"
    sleep 2

    sudo systemctl daemon-reload 2>/dev/null || \
        print_warning "systemctl daemon-reload failed — may need reboot"
    sudo systemctl enable docker 2>/dev/null || \
        print_warning "Could not enable Docker on boot — start manually if needed"

    if ! sudo systemctl start docker 2>/dev/null; then
        print_error "Docker failed to start. Try rebooting and running the installer again."
        exit 1
    fi

    sleep 3

    # Add passwordless sudo for docker so it works immediately
    # without requiring logout — fixes "permission denied" on docker socket
    print_info "Setting up Docker permissions..."
    echo "$USER ALL=(ALL) NOPASSWD: /usr/bin/docker" | \
        sudo tee /etc/sudoers.d/docker-nopasswd > /dev/null 2>&1 || true
    sudo chmod 0440 /etc/sudoers.d/docker-nopasswd 2>/dev/null || true

    # If docker still not accessible without sudo — wrap it
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

    print_success "Docker installed and permissions configured!"
}

install_git() {
    if command -v git &>/dev/null; then
        print_success "Git already installed"
        return 0
    fi
    print_info "Installing Git..."

    if [[ "${FEDORA_IMMUTABLE:-false}" == "true" ]]; then
        if sudo rpm-ostree install -y git; then
            print_success "Git layered via rpm-ostree — reboot required before first use."
        else
            print_warning "Git installation failed — some features may not work."
            print_info "Try manually: sudo rpm-ostree install git"
        fi
    else
        if sudo dnf -y install git; then
            print_success "Git installed!"
        else
            print_warning "Git installation failed — some features may not work."
            print_info "Try manually: sudo dnf install -y git"
        fi
    fi
}

# ─────────────────────────────────────────
# STEP 1 — SUMMARY AND CONFIRM
# ─────────────────────────────────────────
show_summary() {
    print_header
    print_step "STEP 1/4 — What We're Building"

    echo ""
    echo -e "  ${WHITE}${BOLD}Server:${NC}   ${CYAN}WoW Playerbots (AzerothCore WotLK)${NC}"
    echo -e "  ${WHITE}${BOLD}Folder:${NC}   ${CYAN}$SERVER_DIR${NC}"
    echo -e "  ${WHITE}${BOLD}Install:${NC}  ${YELLOW}Compile from source (2-4 hours)${NC}"
    echo ""
    echo -e "  ${WHITE}${BOLD}What you get:${NC}"
    echo -e "    ${GREEN}✅${NC} Hundreds of AI players roaming the world"
    echo -e "    ${GREEN}✅${NC} Bots quest, dungeon, raid alongside you"
    echo -e "    ${GREEN}✅${NC} Azeroth feels truly alive — solo or co-op"
    echo ""
    echo -e "${YELLOW}  ⚠️  COMPILATION WARNING:${NC}"
    echo -e "  This will take 2-4 hours on your machine."
    echo -e "  Keep it cool and connected to power."
    echo -e "  The fan will be loud. That's normal."
    echo ""

    if ! ask_yes_no "Ready to build your Playerbots server?"; then
        echo ""
        echo -e "${WHITE}No problem! Run this script again when you're ready.${NC}"
        exit 0
    fi
}

# ─────────────────────────────────────────
# STEP 2 — INSTALL SERVER
# ─────────────────────────────────────────
install_server() {
    print_header
    print_step "STEP 2/4 — Building Playerbots Server (2-4 hours)"

    # Install dependencies
    print_info "Checking dependencies..."
    install_docker
    install_git

    # ── Skip clone+compile if images already built ───────────────────
    # AzerothCore's compose setup builds and manages its own images.
    # If they already exist in $SERVER_DIR, skip the 2-4 hour compile
    # and just start the server — the rest of the install continues
    # normally (account creation, launcher setup, etc.).
    if [ -d "$SERVER_DIR" ] && \
       (cd "$SERVER_DIR" && docker compose images 2>/dev/null | grep -qi "worldserver"); then
        print_success "Compiled images already found in $SERVER_DIR"
        print_info "Skipping compile — reusing your existing build."
        print_info "To force a fresh compile, remove the server folder:"
        print_info "  sudo rm -rf $SERVER_DIR"
        cd "$SERVER_DIR" || exit 1
        docker compose up -d 2>&1 | tail -5
        return 0
    fi

    # Images not found — handle existing folder before cloning
    if [ -d "$SERVER_DIR" ]; then
        print_warning "Existing folder found at $SERVER_DIR (no compiled images present)"
        if ask_yes_no "Remove it and start fresh?"; then
            docker compose -f "$SERVER_DIR/docker-compose.yml" down -v 2>/dev/null || true
            sudo rm -rf "$SERVER_DIR"
            print_success "Old install removed"
        else
            print_info "Keeping existing install — exiting."
            exit 0
        fi
    fi

    print_info "Cloning Playerbots source..."
    print_info "Using official mod-playerbots fork"
    print_warning "This will take 2-4 hours to compile!"
    print_info "Keep your computer plugged in during the build!"

    git clone \
        https://github.com/mod-playerbots/azerothcore-wotlk.git \
        --branch=Playerbot \
        "$SERVER_DIR"

    if [ ! -d "$SERVER_DIR" ]; then
        print_error "Clone failed. Check your internet connection."
        exit 1
    fi

    mkdir -p "$SERVER_DIR/modules"

    print_info "Cloning mod-playerbots module..."
    if git clone --depth 1 \
        https://github.com/mod-playerbots/mod-playerbots.git \
        --branch=master \
        "$SERVER_DIR/modules/mod-playerbots"; then
        print_success "mod-playerbots module cloned!"
    else
        print_warning "mod-playerbots clone failed — check your connection."
        print_info "You can add it manually later: git clone ... $SERVER_DIR/modules/mod-playerbots"
    fi

    cat > "$SERVER_DIR/docker-compose.override.yml" << 'OVERRIDE'
services:
  ac-worldserver:
    build:
      context: .
      target: worldserver
    volumes:
      - ./modules:/azerothcore/modules:Z
    environment:
      AC_PLAYERBOTS_UPDATES_ENABLE_DATABASES: "1"
      AC_AI_PLAYERBOT_RANDOM_BOT_AUTOLOGIN: "1"
      AC_AI_PLAYERBOT_MIN_RANDOM_BOTS: "1600"
      AC_AI_PLAYERBOT_MAX_RANDOM_BOTS: "2000"
  ac-authserver:
    build:
      context: .
      target: authserver
  ac-db-import:
    build:
      context: .
      target: db-import
  ac-client-data-init:
    build:
      context: .
      target: client-data
OVERRIDE

    print_info "Compiling Playerbots server (2-4 hours)..."
    print_info "Progress saved to: ~/playerbots-build.log"
    print_info "Go make a coffee — this will take a while! ☕"

    cd "$SERVER_DIR"
    docker compose up -d --build 2>&1 | tee ~/playerbots-build.log

    if [ ${PIPESTATUS[0]} -ne 0 ]; then
        print_error "Compilation failed. Check ~/playerbots-build.log"
        exit 1
    fi

    print_success "Playerbots server compiled!"
}

# ─────────────────────────────────────────
# WAIT FOR SERVER READY
# ─────────────────────────────────────────
wait_for_server() {
    print_info "Waiting for world server to initialize..."
    print_info "First launch after compilation may take 10-15 minutes."
    echo ""

    TIMEOUT=1800
    ELAPSED=0
    READY=0
    WORLD_CONTAINER=""

    while [ $ELAPSED -lt $TIMEOUT ]; do
        WORLD_CONTAINER=$(docker ps --format '{{.Names}}' \
            2>/dev/null | grep -i "worldserver" | head -1)

        if [ -n "$WORLD_CONTAINER" ]; then
            if docker logs "$WORLD_CONTAINER" \
                2>/dev/null | grep -q "ready\.\.\."; then
                READY=1
                break
            fi
        fi

        printf "."
        sleep 10
        ELAPSED=$((ELAPSED + 10))
    done

    echo ""
    echo ""

    if [ $READY -eq 1 ]; then
        print_success "Server is READY! ⚔️"
    else
        print_warning "Server is taking longer than expected."
        print_info "Check progress: docker logs -f $WORLD_CONTAINER"
        print_info "Wait for 'ready...' then create accounts manually."
    fi
}

# ─────────────────────────────────────────
# STEP 3 — CREATE ACCOUNTS
# ─────────────────────────────────────────
create_accounts() {
    print_header
    print_step "STEP 3/4 — Create Your Accounts"

    echo ""
    echo -e "${GREEN}${BOLD}Your server is running!${NC}"
    echo ""
    echo -e "${WHITE}Now create your account. Open a new terminal window${NC}"
    echo -e "${WHITE}and run these three steps:${NC}"
    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "${WHITE}${BOLD}1. Open the GM Console:${NC}"
    echo -e "   ${CYAN}docker attach \$(docker ps --format '{{.Names}}' | grep worldserver | head -1)${NC}"
    echo ""
    echo -e "${WHITE}${BOLD}2. Create your account (replace USERNAME and PASSWORD):${NC}"
    echo -e "   ${GREEN}account create USERNAME PASSWORD${NC}"
    echo -e "   ${GREEN}account set gmlevel USERNAME 3 -1${NC}"
    echo ""
    echo -e "${WHITE}${BOLD}3. Exit the console safely:${NC}"
    echo -e "   ${YELLOW}Ctrl+P then Ctrl+Q${NC}"
    echo -e "   ${RED}Never press Ctrl+C — that stops the server!${NC}"
    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "${WHITE}Press ENTER when done creating accounts...${NC}"
    read -r
}

# ─────────────────────────────────────────
# STEP 4 — GAMING MODE SETUP
# ─────────────────────────────────────────
setup_gaming_mode() {
    print_step "STEP 4/4 — Setting Up Steam / Gaming Launcher"

    local launcher_path="$HOME/wow-playerbots-launcher.sh"
    local server_dir="$SERVER_DIR"

    # Detect available terminal emulator (global — also used by show_completion)
    TERM_BIN=""
    TERM_ARGS=""
    if command -v konsole &>/dev/null; then
        TERM_BIN="/usr/bin/konsole"
        TERM_ARGS="--hold -e bash ~/wow-playerbots-launcher.sh"
    elif command -v gnome-terminal &>/dev/null; then
        TERM_BIN="/usr/bin/gnome-terminal"
        TERM_ARGS="-- bash -c 'bash ~/wow-playerbots-launcher.sh; read -r'"
    elif command -v xterm &>/dev/null; then
        TERM_BIN="/usr/bin/xterm"
        TERM_ARGS="-hold -e bash ~/wow-playerbots-launcher.sh"
    fi

    cat > "$launcher_path" << LAUNCHER
#!/bin/bash
# Dad's MMO Lab — WoW Playerbots Launcher v${WIZARD_VERSION}
export PATH="/usr/bin:/usr/local/bin:/bin:\$PATH"
unset LD_PRELOAD
unset LD_LIBRARY_PATH

LOGFILE="/tmp/wow-launch.log"
exec 2>"\$LOGFILE"

clear
echo ""
printf "${GOLD} ══════════════════════════════════════════════════════════════════════════════════${NC}\n"
printf "   ${DIM}Dad's MMO Lab${NC}  ✦  ${DIM}WoW Playerbots${NC}\n"
printf "${GOLD} ══════════════════════════════════════════════════════════════════════════════════${NC}\n"
echo ""
echo -e "  ${WHITE}${BOLD}Starting server...${NC}"
echo ""

# Stop any other running WoW servers first
# Only stops AzerothCore containers — never touches other Docker services
WOW_CONTAINERS=\$(docker ps --format '{{.Names}}' 2>/dev/null | \
    grep -iE "worldserver|authserver|ac-database|ac-eluna|ac-client|ac-db-import" || true)

if [ -n "\$WOW_CONTAINERS" ]; then
    echo -e "  ${YELLOW}⚠️  Stopping any running WoW servers first...${NC}"
    echo "\$WOW_CONTAINERS" | xargs docker stop >> "\$LOGFILE" 2>&1 || true
    sleep 5
    echo -e "  ${GREEN}✅ All clear!${NC}"
    echo ""
fi

cd "${server_dir}" || exit 1

if docker compose up -d --scale phpmyadmin=0 >> "\$LOGFILE" 2>&1; then
    echo -e "  ${GREEN}✅ Containers started!${NC}"
elif docker compose up -d >> "\$LOGFILE" 2>&1; then
    echo -e "  ${GREEN}✅ Containers started (phpmyadmin fallback used)${NC}"
else
    echo -e "  ${RED}❌ Failed to start server.${NC}"
    echo -e "  ${DIM}Check: \$LOGFILE${NC}"
    sleep 10
    exit 1
fi

echo ""
printf "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
echo -e "${WHITE}${BOLD} Waiting for Azeroth to wake up...${NC}"
printf "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
echo ""
echo -e "  ${DIM}First launch: 5-15 minutes${NC}"
echo -e "  ${DIM}After first launch: ~30 seconds${NC}"
echo ""

TIMEOUT=900
ELAPSED=0
READY=0
WORLD_CONTAINER=""

while [ \$ELAPSED -lt \$TIMEOUT ]; do
    WORLD_CONTAINER=\$(docker ps --format '{{.Names}}' 2>/dev/null | grep -i "worldserver" | head -1)
    if [ -n "\$WORLD_CONTAINER" ]; then
        if docker logs "\$WORLD_CONTAINER" 2>/dev/null | grep -q "ready\.\.\."; then
            READY=1
            break
        fi
    fi
    printf "  ${GOLD}.${NC}"
    sleep 5
    ELAPSED=\$((ELAPSED + 5))
done

echo ""
echo ""

if [ \$READY -eq 1 ]; then
    printf "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
    echo -e "${GREEN}${BOLD}  ✅ AZEROTH IS READY!${NC}"
    printf "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
else
    echo -e "  ${YELLOW}⏳ Still initializing — launch WoW soon${NC}"
fi

echo ""
echo -e "  ${WHITE}${BOLD}Launch WoW from Steam or your desktop${NC}"
echo -e "  ${DIM}Server AUTO-SHUTS DOWN when WoW closes${NC}"
echo -e "  ${DIM}── or press ENTER to shut down manually ──${NC}"
echo ""

MANUAL_SHUTDOWN=0
WOW_STARTED=0
for i in \$(seq 1 60); do
    if pgrep -fi "Wow\\.exe|wine.*[Ww]o[Ww]" > /dev/null 2>&1; then
        WOW_STARTED=1
        break
    fi
    if read -r -t 5 2>/dev/null; then
        MANUAL_SHUTDOWN=1
        break
    fi
done

if [ \$MANUAL_SHUTDOWN -eq 0 ]; then
    if [ \$WOW_STARTED -eq 1 ]; then
        echo -e "  ${GREEN}⚔️  WoW detected! Enjoy Azeroth!${NC}"
        while pgrep -fi "Wow\\.exe|wine.*[Ww]o[Ww]" > /dev/null 2>&1; do
            if read -r -t 3 2>/dev/null; then
                MANUAL_SHUTDOWN=1
                break
            fi
        done
        if [ \$MANUAL_SHUTDOWN -eq 0 ]; then
            sleep 5
            echo -e "  ${YELLOW}WoW closed — shutting down...${NC}"
        fi
    else
        echo -e "  ${DIM}WoW not detected — press ENTER to shut down.${NC}"
        read -r
    fi
fi

if [ \$MANUAL_SHUTDOWN -eq 1 ]; then
    echo -e "  ${YELLOW}Manual shutdown — shutting down...${NC}"
fi

cd "${server_dir}" && docker compose down >> "\$LOGFILE" 2>&1

echo ""
printf "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
echo -e "${GREEN}${BOLD}  ✅ Server stopped! Safe to close.${NC}"
echo -e "  ${DIM}Thanks for playing! youtube.com/@DadsMmoLab${NC}"
printf "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
echo ""
sleep 5
LAUNCHER

    chmod +x "$launcher_path"
    print_success "Steam / Gaming Mode launcher created: ~/wow-playerbots-launcher.sh"

    # Save server info — build the Steam launcher line dynamically
    local steam_target_line=""
    if [[ -n "$TERM_BIN" ]]; then
        steam_target_line="    Target:  ${TERM_BIN}
    Options: ${TERM_ARGS}
    Proton:  OFF (launcher needs no Proton)"
    else
        steam_target_line="    Run directly: bash ~/wow-playerbots-launcher.sh"
    fi

    cat > "$SERVER_DIR/MY_SERVER.txt" << INFO
====================================
  Dad's MMO Lab — WoW Playerbots
  AzerothCore WotLK + Playerbots
====================================

SERVER:
  Folder:    ${SERVER_DIR}
  Realmlist: 127.0.0.1
  Account:   create via worldserver console (see below)

LAUNCHER:
  Path: ~/wow-playerbots-launcher.sh
  Add to Steam (optional):
${steam_target_line}

REALMLIST (in your WoW client folder):
  Edit:  realmlist.wtf
  Set to: set realmlist 127.0.0.1

USEFUL COMMANDS:
  Start:   cd ${SERVER_DIR} && docker compose up -d
  Stop:    cd ${SERVER_DIR} && docker compose down
  Logs:    cd ${SERVER_DIR} && docker compose logs -f
  Console: docker attach \$(docker ps --format '{{.Names}}' | grep worldserver | head -1)
    (Exit safely: Ctrl+P then Ctrl+Q. NOT Ctrl+C.)

CREATE ACCOUNTS:
  docker attach \$(docker ps --format '{{.Names}}' | grep worldserver | head -1)
  account create USERNAME PASSWORD
  account set gmlevel USERNAME 3 -1   (optional: makes GM)
  [Ctrl+P then Ctrl+Q to exit safely]
INFO

    print_success "Server info saved to: $SERVER_DIR/MY_SERVER.txt"
}

# ─────────────────────────────────────────
# DONE
# ─────────────────────────────────────────
show_completion() {
    echo ""
    echo -e "${GOLD}${BOLD}╔══════════════════════════════════════════════════╗${NC}"
    echo -e "${GOLD}${BOLD}║   🎉 YOUR PLAYERBOTS SERVER IS READY!            ║${NC}"
    echo -e "${GOLD}${BOLD}╚══════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  ${WHITE}${BOLD}Server:${NC}   ${CYAN}WoW Playerbots (AzerothCore WotLK)${NC}"
    echo -e "  ${WHITE}${BOLD}Folder:${NC}   ${CYAN}$SERVER_DIR${NC}"
    echo -e "  ${WHITE}${BOLD}Launcher:${NC} ${CYAN}~/wow-playerbots-launcher.sh${NC}"
    echo ""

    echo -e "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD} STEP A — Set Your WoW Realmlist${NC}"
    echo -e "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  1. Open your WoW client folder in the file manager"
    echo -e "  2. Find and open: ${CYAN}realmlist.wtf${NC}"
    echo -e "  3. Make sure it says exactly: ${GREEN}set realmlist 127.0.0.1${NC}"
    echo -e "  4. Save the file"
    echo ""

    echo -e "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD} STEP B — Add to Steam / Gaming Mode${NC}"
    echo -e "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  Your launcher was created here:"
    echo ""
    echo -e "  ${GREEN}${BOLD}~/wow-playerbots-launcher.sh${NC}"
    echo ""
    if [[ -n "$TERM_BIN" ]]; then
        local term_name
        term_name=$(basename "$TERM_BIN")
        echo -e "  Add it to Steam (optional, for Gaming Mode):"
        echo -e "  1. Open Steam in Desktop Mode"
        echo -e "  2. Click ${CYAN}Games${NC} → ${CYAN}Add a Non-Steam Game${NC}"
        echo -e "  3. Click ${CYAN}Browse${NC} → navigate to ${CYAN}/usr/bin/${NC}"
        echo -e "  4. Select ${CYAN}${term_name}${NC} → click ${CYAN}Add Selected Programs${NC}"
        echo -e "  5. Find ${CYAN}${term_name}${NC} in your library → right-click → ${CYAN}Properties${NC}"
        echo -e "  6. Rename it to: ${GREEN}WoW Playerbots Server${NC}"
        echo -e "  7. Set Launch Options to exactly:"
        echo ""
        echo -e "  ${GREEN}${TERM_ARGS}${NC}"
        echo ""
        echo -e "  8. Under Compatibility — ${RED}do NOT enable Proton${NC}"
    else
        echo -e "  No supported terminal found. Run the launcher directly:"
        echo -e "  ${GREEN}bash ~/wow-playerbots-launcher.sh${NC}"
    fi
    echo ""

    echo -e "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD} STEP C — Play!${NC}"
    echo -e "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  1. Launch ${CYAN}WoW Playerbots Server${NC} from Steam or your terminal"
    echo -e "  2. Watch the dots... wait for ${GREEN}AZEROTH IS READY!${NC}"
    echo -e "  3. Launch WoW"
    echo -e "  4. Login with the account you created"
    echo -e "  5. Play! Bots populate within 5-10 min — be patient!"
    echo -e "  6. Close WoW → server shuts down automatically ✅"
    echo ""
    echo -e "  ${YELLOW}Server info saved at: $SERVER_DIR/MY_SERVER.txt${NC}"
    echo ""
    echo -e "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}  📺 youtube.com/@DadsMmoLab${NC}"
    echo -e "${WHITE}  📦 github.com/DadsMmoLab/dads-mmo-lab${NC}"
    echo -e "${WHITE}  ☕ ko-fi.com/dadsmmolab${NC}"
    echo -e "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "${GREEN}${BOLD}Welcome to Azeroth. It's yours now. Forever. ⚔️${NC}"
    echo ""
    echo -e "${YELLOW}  ℹ️  Your server is still running right now!${NC}"
    echo -e "${YELLOW}  To stop it: ${CYAN}cd $SERVER_DIR && docker compose down${NC}"
    echo -e "${YELLOW}  Or just use the Steam / Gaming Mode launcher next time.${NC}"
    echo ""
    if ask_yes_no "Would you like to stop the server now?"; then
        print_info "Stopping server..."
        cd "$SERVER_DIR" && docker compose down
        print_success "Server stopped! Use the Steam / Gaming Mode launcher to start it next time."
    else
        print_info "Server left running — enjoy Azeroth! ⚔️"
    fi
    echo ""
}

# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
print_header

echo -e "${WHITE}Welcome to the WoW Playerbots installer!${NC}"
echo -e "${WHITE}Hundreds of AI players will roam your Azeroth,${NC}"
echo -e "${WHITE}quest, run dungeons, and make the world feel alive.${NC}"
echo ""
echo -e "${BLUE}This takes about 5 minutes to set up, then${NC}"
echo -e "${BLUE}compiles itself over 2-4 hours. Plug in and walk away.${NC}"
echo ""

if ! ask_yes_no "Ready to begin?"; then
    echo "No problem — run this script when you're ready!"
    exit 0
fi

check_system

echo ""
echo -e "\033[1;33m⚠️  This installer needs sudo access for:\033[0m"
echo -e "\033[1;33m   • Installing Docker (if not present)\033[0m"
echo -e "\033[1;33m   • Fixing file ownership after build\033[0m"
echo ""
echo -e "\033[1;37mPlease enter your password if prompted:\033[0m"
if ! sudo -v; then
    echo -e "\033[0;31m❌ Could not cache sudo credentials. Aborting.\033[0m"
    exit 1
fi
( while true; do sudo -n true; sleep 60; done ) 2>/dev/null &
SUDO_KEEPALIVE_PID=$!
trap "kill $SUDO_KEEPALIVE_PID 2>/dev/null; exit" EXIT INT TERM

show_summary
install_server
wait_for_server
create_accounts
setup_gaming_mode
show_completion
