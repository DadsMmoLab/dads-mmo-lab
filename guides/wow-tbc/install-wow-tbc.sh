#!/bin/bash
# ============================================================
#  Dad's MMO Lab — Burning Crusade WoW (2.4.3) Server Installer
#  CMaNGOS TBC + Playerbots + AHBot, compiled from source
#
#  https://github.com/DadsMmoLab/dads-mmo-lab
#
#  Version: 1.0.0
#
#  Usage:
#    chmod +x install-wow-tbc.sh
#    ./install-wow-tbc.sh
#
#  What this does (fully automated, ~3-5 hours total):
#    1. Validates your WoW 2.4.3 client before any slow work
#    2. Installs Docker if needed
#    3. Compiles CMaNGOS TBC + Playerbots (~2-4 hours)
#    4. Extracts map/dbc/vmap data from your client (~15-20 min)
#    5. Generates pathfinding mesh files (~30 min)
#    6. Sets up MariaDB with all 4 databases + content + updates
#    7. Imports Playerbots SQL (so bots actually work)
#    8. Starts the compiled server with bots enabled
#    9. Creates default player/player account
#   10. Configures realmlist and Gaming Mode launcher
#
#  Powered by:
#    - cmangos/mangos-tbc   — github.com/cmangos/mangos-tbc
#    - cmangos/playerbots   — github.com/cmangos/playerbots
#    - cmangos/tbc-db       — github.com/cmangos/tbc-db
#
#  Why source compile (and why this is temporary)?
#    No public Linux Docker image currently ships CMaNGOS TBC WITH
#    Playerbots compiled in. Source compile is the most reliable
#    TBC+bots path right now.
#
#    🔜 COMING SOON: Dad's MMO Lab will publish pre-built Docker
#    images via GitHub Actions. When that's live, a future
#    install-wow-tbc-fast.sh will do a 5-minute pull instead
#    of a 3-4 hour compile. This installer is for folks who can't
#    wait — or who want the educational experience of compiling
#    their own server from source.
#
#  ⚠️  Requirements:
#    - WoW Burning Crusade 2.4.3 (build 8606) client folder
#    - 20GB free disk space
#    - Steam Deck plugged in, on a flat hard surface
#    - 3-5 hours of wall-clock time (mostly hands-off)
# ============================================================

INSTALLER_VERSION="1.0.0"

set -o pipefail

# ─────────────────────────────────────────
# COLORS
# ─────────────────────────────────────────
RST='\033[0m'; BOLD='\033[1m'
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; WHITE='\033[1;37m'; CYAN='\033[0;36m'
NC='\033[0m'
GOLD='\033[38;5;220m'; DIM='\033[2m'

# TBC — fel green
TC='\033[0;32m'
TCB='\033[1;32m'

print_header() {
    clear
    echo ""
    echo -e "${TC}╔══════════════════════════════════════════════════╗${NC}"
    echo -e "${TC}║${WHITE}${BOLD}         🔥 DAD'S MMO LAB                           ${NC}${TC}║${NC}"
    echo -e "${TC}║${WHITE}         Burning Crusade Server (source-compile) ${NC}${TC}║${NC}"
    echo -e "${TC}║${BLUE}         CMaNGOS TBC + Playerbots                ${NC}${TC}║${NC}"
    echo -e "${TC}║${YELLOW}         Version ${INSTALLER_VERSION}                   ${NC}${TC}║${NC}"
    echo -e "${TC}╚══════════════════════════════════════════════════╝${NC}"
    echo ""
}

print_step() {
    echo ""
    echo -e "${TC}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD} $1${NC}"
    echo -e "${TC}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
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
SERVER_DIR="$HOME/wow-tbc-server"
CLIENT_DIR=""
DB_PASSWORD="tbc$(openssl rand -hex 8)"
DB_PASSWORD_LOADED=false   # set to true when loaded from .db_password file

CMANGOS_CORE_REPO="https://github.com/cmangos/mangos-tbc.git"
CMANGOS_BOTS_REPO="https://github.com/cmangos/playerbots.git"

BUILDER_IMAGE="dml/cmangos-tbc-builder:local"
SERVER_IMAGE="dml/cmangos-tbc-server:local"

# ─────────────────────────────────────────
# SYSTEM CHECKS
# ─────────────────────────────────────────
check_system() {
    print_step "Checking System Requirements"

    if [[ "$OSTYPE" != "linux-gnu"* ]]; then
        print_error "Requires Linux (SteamOS). Are you in Desktop Mode?"
        exit 1
    fi
    print_success "Linux detected"

    AVAILABLE_GB=$(df -BG "$HOME" 2>/dev/null | awk 'NR==2 {print $4}' | sed 's/G//' | tr -d ' ')
    if [ -n "$AVAILABLE_GB" ] && [ "$AVAILABLE_GB" -lt 20 ] 2>/dev/null; then
        print_error "Need 20GB free for compile + client data. You have ${AVAILABLE_GB}GB."
        exit 1
    fi
    print_success "Disk space OK (${AVAILABLE_GB:-unknown}GB available)"

    if ! ping -c 1 github.com &>/dev/null; then
        print_error "No internet. Please connect and try again."
        exit 1
    fi
    print_success "Internet connection OK"

    TOTAL_RAM_MB=$(free -m | awk 'NR==2 {print $2}')
    if [ -n "$TOTAL_RAM_MB" ] && [ "$TOTAL_RAM_MB" -lt 6000 ]; then
        print_warning "RAM is low (${TOTAL_RAM_MB}MB). Compile may swap heavily."
        print_info "Steam Deck should have 16GB — verify nothing else is hogging RAM."
    else
        print_success "RAM OK (${TOTAL_RAM_MB}MB total)"
    fi
}

# ─────────────────────────────────────────
# KEYRING HEALTH (SteamOS pacman drift fix)
# ─────────────────────────────────────────
check_pacman_keyring() {
    if ! sudo -n pacman -Sy --noconfirm &>/dev/null; then
        print_warning "Pacman keyring may need refresh — handled by Docker install if needed."
    fi
}

# ─────────────────────────────────────────
# DOCKER INSTALL
#
# Hardened against three failure modes:
#   1. Podman masquerading as docker — docker-compose fails hours later
#   2. docker installed without docker-compose — compose subcommand missing
#   3. Broken ~/.docker/cli-plugins/docker-compose — exec format error
# Also handles WSL2 (Docker Desktop) and the post-install group refresh.
# ─────────────────────────────────────────
install_docker() {
    local has_docker=0
    if command -v docker &>/dev/null && docker ps &>/dev/null; then
        has_docker=1
    fi

    # ── WSL2: Docker must come from Docker Desktop ────────────────────
    if grep -qi microsoft /proc/version 2>/dev/null || \
       grep -qi wsl /proc/version 2>/dev/null; then
        if [ $has_docker -eq 1 ] && docker compose version &>/dev/null; then
            print_success "Docker + Compose available (Docker Desktop / WSL2)"
            return 0
        fi
        print_error "Docker not available in WSL."
        print_info ""
        print_info "On Windows/WSL2, install Docker via Docker Desktop:"
        print_info "  1. Install Docker Desktop for Windows"
        print_info "  2. Settings → Resources → WSL Integration"
        print_info "     → Enable integration for your distro"
        print_info "  3. Re-run this installer"
        exit 1
    fi

    # ── Reject podman masquerading as docker ──────────────────────────
    if [ $has_docker -eq 1 ] && docker --version 2>&1 | grep -qi podman; then
        print_error "Detected podman pretending to be docker."
        print_info ""
        print_info "Podman's docker-compose shim causes 'Failed to start db container'."
        print_info ""
        print_info "Run our uninstaller first — it has a 'Clean Docker' step:"
        print_info "  bash ~/Downloads/uninstall.sh"
        print_info "  (choose option D — Clean Docker environment)"
        print_info ""
        print_info "Then re-run this installer."
        exit 1
    fi

    # ── If real Docker is present AND compose works, we're done ──────
    if [ $has_docker -eq 1 ] && docker compose version &>/dev/null; then
        print_success "Docker + Compose already installed and working"
        return 0
    fi

    if [ $has_docker -eq 1 ]; then
        print_warning "Docker is installed but 'docker compose' is not working."
        print_info "Will install docker-compose alongside existing Docker."
    else
        print_info "Installing Docker + Compose..."
    fi
    check_pacman_keyring

    # ── Wipe any broken cli-plugin before installing ──────────────────
    if [ -f "$HOME/.docker/cli-plugins/docker-compose" ] && \
       ! "$HOME/.docker/cli-plugins/docker-compose" version &>/dev/null; then
        print_info "Removing broken ~/.docker/cli-plugins/docker-compose..."
        rm -f "$HOME/.docker/cli-plugins/docker-compose"
    fi

    if ! sudo steamos-readonly disable 2>/dev/null; then
        print_warning "steamos-readonly disable failed — may already be writable"
    fi

    if ! sudo pacman -Sy --noconfirm docker docker-compose; then
        print_error "Failed to install Docker via pacman."
        print_info "If keyring errors: sudo pacman-key --init && sudo pacman-key --populate"
        sudo steamos-readonly enable 2>/dev/null || true
        exit 1
    fi

    sudo steamos-readonly enable 2>/dev/null || true

    sudo systemctl enable --now docker
    sudo usermod -aG docker "$USER"
    print_success "Docker + Compose installed"

    if ! sudo docker compose version &>/dev/null; then
        if command -v docker-compose &>/dev/null; then
            print_info "Shimming docker-compose into ~/.docker/cli-plugins/..."
            mkdir -p "$HOME/.docker/cli-plugins"
            cat > "$HOME/.docker/cli-plugins/docker-compose" <<'SHIM'
#!/bin/bash
exec /usr/bin/docker-compose "$@"
SHIM
            chmod +x "$HOME/.docker/cli-plugins/docker-compose"
        else
            print_error "docker-compose binary missing after install — bailing."
            print_info "Try: sudo pacman -S docker-compose"
            exit 1
        fi
    fi

    if ! docker ps &>/dev/null; then
        print_warning "Docker group not yet active in this shell."
        print_info "Run: newgrp docker  — or log out and back in, then re-run installer."
        print_info "Continuing with sudo for this install..."
        DOCKER_CMD="sudo docker"
    else
        DOCKER_CMD="docker"
    fi

    if ! $DOCKER_CMD compose version &>/dev/null; then
        print_error "'docker compose' still not working after install."
        print_info "Run for diagnosis: $DOCKER_CMD compose version"
        exit 1
    fi
    print_success "'docker compose' verified working"
}

# ─────────────────────────────────────────
# WELCOME
# ─────────────────────────────────────────
show_welcome() {
    print_header

    echo -e "${WHITE}Welcome to the Burning Crusade WoW installer!${NC}"
    echo ""
    echo -e "${WHITE}This installs a full offline World of Warcraft${NC}"
    echo -e "${WHITE}Burning Crusade (2.4.3) server using CMaNGOS TBC${NC}"
    echo -e "${WHITE}compiled from source with Playerbots enabled.${NC}"
    echo ""
    echo -e "${GREEN}${BOLD}🤖 Playerbots + AHBot are included.${NC}"
    echo -e "${WHITE}AI players roam Outland and Azeroth, form parties,${NC}"
    echo -e "${WHITE}run dungeons, and populate the auction house.${NC}"
    echo -e "${WHITE}You're never alone in the Burning Crusade.${NC}"
    echo ""
    echo -e "${RED}${BOLD}⚠️  HONEST TIME COMMITMENT:${NC}"
    echo -e "${YELLOW}  • Total time: ${BOLD}3-5 hours${NC}${YELLOW} (mostly hands-off)${NC}"
    echo -e "${YELLOW}  • Compile: ${BOLD}2-4 hours${NC}${YELLOW} (fan loud, Deck hot)${NC}"
    echo -e "${YELLOW}  • Extraction: ${BOLD}15-20 minutes${NC}"
    echo -e "${YELLOW}  • Pathfinding mesh gen: ${BOLD}30 minutes${NC}"
    echo -e "${YELLOW}  • You can walk away — heartbeat shows progress${NC}"
    echo -e "${YELLOW}  • First run only — future starts are seconds${NC}"
    echo ""
    echo -e "${RED}${BOLD}⚠️  Plug Deck in. Use a flat hard surface.${NC}"
    echo ""
    echo -e "${TCB}${BOLD}🔜 COMING SOON — the fast path:${NC}"
    echo -e "${WHITE}Dad's MMO Lab is building a pre-built Docker image${NC}"
    echo -e "${WHITE}publishing pipeline. When it ships, a separate${NC}"
    echo -e "${WHITE}install-wow-tbc-fast.sh will do a 5-minute pull${NC}"
    echo -e "${WHITE}instead of compiling.${NC}"
    echo ""
    echo -e "${BLUE}ℹ️  Client required: WoW Burning Crusade 2.4.3 (build 8606)${NC}"
    echo -e "${BLUE}ℹ️  Default account: ${BOLD}player / player${NC}"
    echo -e "${BLUE}ℹ️  Build log: /tmp/wow-tbc-build.log${NC}"
    echo ""

    if ! ask_yes_no "Ready to open the Dark Portal?"; then
        echo "No problem — come back when you're ready!"
        exit 0
    fi
}

# ─────────────────────────────────────────
# LOCATE CLIENT
# ─────────────────────────────────────────
locate_client() {
    print_header
    print_step "STEP 1/5 — Locating & Validating Your WoW TBC Client"

    echo -e "${WHITE}I need the path to your ${BOLD}Burning Crusade 2.4.3${NC}${WHITE} client folder.${NC}"
    echo -e "${WHITE}The folder must contain:${NC}"
    echo -e "  • ${TC}WoW.exe${NC} (or wow.exe — case varies)"
    echo -e "  • ${TC}Data/${NC} folder with .MPQ files inside"
    echo -e "  • ${TC}Data/expansion.MPQ${NC} (the TBC expansion archive — required)"
    echo ""
    echo -e "${BLUE}Examples of valid paths:${NC}"
    echo -e "  ${CYAN}~/Games/WoWTBC${NC}"
    echo -e "  ${CYAN}~/Games/\"Burning Crusade\"${NC} (with quotes if spaces)"
    echo -e "  ${CYAN}/run/media/deck/SD/WoW-2.4.3${NC}"
    echo ""

    while true; do
        echo -e "${WHITE}Enter path to your WoW TBC client folder:${NC}"
        read -r raw_path

        raw_path="${raw_path%\"}"
        raw_path="${raw_path#\"}"
        raw_path="${raw_path%\'}"
        raw_path="${raw_path#\'}"

        CLIENT_DIR="${raw_path/#\~/$HOME}"

        if [ ! -d "$CLIENT_DIR" ]; then
            print_error "Folder doesn't exist: $CLIENT_DIR"
            print_info "Try the full path: /home/deck/Games/YourFolder"
            echo ""
            continue
        fi

        if [ ! -d "$CLIENT_DIR/Data" ]; then
            print_error "No Data/ folder inside $CLIENT_DIR"
            print_info "This doesn't look like a WoW client. The Data/ folder"
            print_info "is where all the .MPQ game files live."
            echo ""
            continue
        fi

        # Count MPQs recursively — TBC puts ~4 in Data/ root and ~8-10
        # more in Data/enUS/ (or other locale), so maxdepth 1 only sees 4.
        mpq_count=$(find "$CLIENT_DIR/Data" -iname "*.mpq" 2>/dev/null | wc -l)
        if [ "$mpq_count" -lt 6 ]; then
            print_error "Only $mpq_count .MPQ files found under Data/."
            print_info "Burning Crusade 2.4.3 typically has 12-16 total"
            print_info "(~4 in Data/ root, ~8-10 in Data/enUS/ or similar locale folder)."
            print_info "If you have fewer, this might be:"
            print_info "  • A wrong WoW version (Vanilla, WotLK, Retail)"
            print_info "  • An incomplete download"
            print_info "  • A non-standard repack"
            echo ""
            if ! ask_yes_no "Continue anyway? (NOT recommended — extraction will likely fail)"; then
                continue
            fi
        fi

        # ── TBC-specific check: expansion.MPQ ─────────────────────────
        # expansion.MPQ is the definitive TBC file. Vanilla doesn't have
        # it. Without it the 'ad' extractor can't pull Outland content
        # and the server will crash loading TBC zones.
        if [ ! -f "$CLIENT_DIR/Data/expansion.MPQ" ] && \
           [ ! -f "$CLIENT_DIR/Data/expansion.mpq" ]; then
            print_error "Data/expansion.MPQ is MISSING."
            print_info ""
            print_info "This file is the core Burning Crusade expansion data."
            print_info "Without it, this is not a TBC client (or it's damaged)."
            print_info ""
            print_info "Likely causes:"
            print_info "  • This is a Vanilla client — wrong version"
            print_info "  • Stripped repack that removed expansion.MPQ"
            print_info "  • Incomplete download"
            print_info ""
            print_info "Find a complete TBC 2.4.3 client (~8GB) and try again."
            echo ""
            if ! ask_yes_no "Continue anyway? (NOT recommended)"; then
                continue
            fi
        fi

        # ── Locale MPQ check ─────────────────────────────────────────────
        # TBC does NOT have dbc.MPQ — DBC data lives inside locale MPQ archives
        # (e.g., Data/enUS/locale-enUS.MPQ). Check that at least one locale
        # subfolder with MPQs exists; without it the 'ad' extractor can't pull
        # DBC data and the server will be missing game tables.
        local locale_mpq_count
        locale_mpq_count=$(find "$CLIENT_DIR/Data" -mindepth 2 -maxdepth 2 \
            -iname "*.mpq" 2>/dev/null | wc -l)
        if [ "$locale_mpq_count" -eq 0 ]; then
            print_error "No locale MPQ files found under Data/enUS/ (or similar)."
            print_info "TBC stores DBC data in locale-specific archives like:"
            print_info "  Data/enUS/locale-enUS.MPQ"
            print_info "  Data/enUS/expansion-locale-enUS.MPQ"
            print_info "Without them, data extraction will produce an incomplete server."
            print_info ""
            print_info "Likely causes:"
            print_info "  • Stripped repack — locale folder was removed"
            print_info "  • Incomplete download — locale folder missing"
            echo ""
            if ! ask_yes_no "Continue anyway? (NOT recommended)"; then
                continue
            fi
        fi

        local client_disk
        client_disk=$(df -BG "$CLIENT_DIR" 2>/dev/null | awk 'NR==2 {print $4}' | sed 's/G//')
        if [ -n "$client_disk" ] && [ "$client_disk" -lt 8 ] 2>/dev/null; then
            print_warning "Only ${client_disk}GB free where client lives."
            print_warning "Extraction may write temp files to client folder (needs ~5GB)."
        fi

        local repack_signals=0
        [ -f "$CLIENT_DIR/realmlist.wtf" ] && repack_signals=$((repack_signals + 1))
        [ ! -d "$CLIENT_DIR/Data/enUS" ] && [ ! -d "$CLIENT_DIR/Data/enGB" ] && \
            repack_signals=$((repack_signals + 1))

        if [ $repack_signals -ge 2 ]; then
            print_info "This client looks like a community repack."
            print_info "That's USUALLY fine as long as expansion.MPQ and locale MPQs exist."
        fi

        print_success "WoW TBC client validated: $CLIENT_DIR"
        print_success "Found $mpq_count .MPQ files including expansion.MPQ"
        break
    done
}

# ─────────────────────────────────────────
# SHOW SUMMARY BEFORE COMPILE
# ─────────────────────────────────────────
show_summary() {
    print_header
    print_step "STEP 2/5 — Pre-Compile Summary"

    echo ""
    echo -e "  ${WHITE}${BOLD}Expansion:${NC} ${TC}Burning Crusade (2.4.3, build 8606)${NC}"
    echo -e "  ${WHITE}${BOLD}Build:${NC}     ${TC}Source compile (CMaNGOS TBC + Playerbots)${NC}"
    echo -e "  ${WHITE}${BOLD}Folder:${NC}    ${TC}$SERVER_DIR${NC}"
    echo -e "  ${WHITE}${BOLD}Client:${NC}    ${TC}$CLIENT_DIR${NC}"
    echo ""
    echo -e "  ${WHITE}${BOLD}Bots:${NC}"
    echo -e "    ${GREEN}✅${NC} Playerbots — AI players roam Azeroth + Outland"
    echo -e "    ${GREEN}✅${NC} AHBot — populates the Auction House"
    echo ""
    echo -e "  ${WHITE}${BOLD}Default account:${NC} ${TC}player / player${NC}"
    echo ""
    echo -e "${YELLOW}${BOLD}  The installer will:${NC}"
    echo -e "${YELLOW}  1. Build a compile container (~5 min)${NC}"
    echo -e "${YELLOW}  2. Clone CMaNGOS TBC + Playerbots source (~5 min)${NC}"
    echo -e "${YELLOW}  3. Compile mangosd + realmd + tools (${BOLD}2-4 HOURS${NC}${YELLOW})${NC}"
    echo -e "${YELLOW}  4. Extract maps from your client (~15-20 min)${NC}"
    echo -e "${YELLOW}  5. Generate pathfinding mesh files (~30 min)${NC}"
    echo -e "${YELLOW}  6. Set up databases and content (~5 min)${NC}"
    echo -e "${YELLOW}  7. Start the server (~1-2 min first boot)${NC}"
    echo ""
    echo -e "${RED}${BOLD}  Plug your Deck in. Use a flat hard surface.${NC}"
    echo -e "${RED}${BOLD}  The fan will be loud. This is normal.${NC}"
    echo ""

    if ! ask_yes_no "Start the build?"; then
        echo "No problem — come back when you have time!"
        exit 0
    fi
}

# ─────────────────────────────────────────
# CREATE BUILD CONTAINER + COMPILE
# ─────────────────────────────────────────
do_compile() {
    print_header
    print_step "STEP 3/5 — Compiling CMaNGOS TBC (2-4 hours)"

    # ── Check for existing install ───────────────────────────────────
    local image_exists=false
    $DOCKER_CMD image inspect "$SERVER_IMAGE" &>/dev/null && image_exists=true

    if [ -d "$SERVER_DIR" ] && [ "$image_exists" = true ]; then
        # Both the server dir and compiled image exist. Show the user
        # their options — default is to reuse, but give a clear path
        # to wipe everything and start over.
        print_success "Compiled image found: $SERVER_IMAGE"
        print_info "Skipping the 2-4 hour compile — previous build is ready."
        echo ""
        if ask_yes_no "Start completely fresh instead? (wipes all files + re-compiles, ~2-4 hrs)"; then
            print_info "Removing existing install and compiled image..."
            cd "$SERVER_DIR" 2>/dev/null && \
                $DOCKER_CMD compose down -v 2>/dev/null || true
            $DOCKER_CMD rmi "$SERVER_IMAGE" 2>/dev/null || true
            sudo rm -rf "$SERVER_DIR"
            print_success "Wiped — starting fresh compile"
            image_exists=false
            # Fall through to full compile below
        else
            print_info "Keeping existing build — skipping compile."
            mkdir -p "$SERVER_DIR/data"
            cd "$SERVER_DIR" || exit 1
            return 0
        fi

    elif [ -d "$SERVER_DIR" ]; then
        # Server dir exists but compiled image is missing — partial install.
        print_warning "Existing install folder found at $SERVER_DIR (no compiled image present)"
        if ask_yes_no "Remove it and start fresh?"; then
            cd "$SERVER_DIR" 2>/dev/null && \
                $DOCKER_CMD compose down -v 2>/dev/null || true
            sudo rm -rf "$SERVER_DIR"
            print_success "Old install removed"
        else
            print_info "Keeping existing install — exiting."
            exit 0
        fi

    elif [ "$image_exists" = true ]; then
        # Image exists but server dir is gone — skip compile, no wipe needed.
        print_success "Compiled image found: $SERVER_IMAGE"
        print_info "Skipping compile — reusing existing build."
        mkdir -p "$SERVER_DIR" "$SERVER_DIR/source" "$SERVER_DIR/build" "$SERVER_DIR/data"
        cd "$SERVER_DIR" || exit 1
        return 0
    fi

    mkdir -p "$SERVER_DIR"
    cd "$SERVER_DIR" || exit 1
    mkdir -p "$SERVER_DIR/source" "$SERVER_DIR/build" "$SERVER_DIR/data"

    print_info "Writing Dockerfile..."
    cat > "$SERVER_DIR/Dockerfile" << 'DOCKERFILE'
# ──────────────────────────────────────────────────────────────
# Stage 1: Build CMaNGOS TBC + Playerbots from source
# ──────────────────────────────────────────────────────────────
FROM ubuntu:22.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    cmake \
    g++-12 \
    gcc-12 \
    git \
    libssl-dev \
    default-libmysqlclient-dev \
    libace-dev \
    libtbb-dev \
    libboost-all-dev \
    libreadline-dev \
    zlib1g-dev \
    openssl \
    p7zip-full \
    && rm -rf /var/lib/apt/lists/*

ENV CC=/usr/bin/gcc-12
ENV CXX=/usr/bin/g++-12

WORKDIR /src

# Clone CMaNGOS TBC core
RUN git clone --depth 1 https://github.com/cmangos/mangos-tbc.git /src/mangos-tbc

# Clone Playerbots into modules folder
RUN git clone --depth 1 https://github.com/cmangos/playerbots.git \
    /src/mangos-tbc/src/modules/Bots

# Clone tbc-db (world content database)
RUN git clone --depth 1 https://github.com/cmangos/tbc-db.git /src/tbc-db

# Clone playerbots repo separately to get its sql/ tree
RUN git clone --depth 1 https://github.com/cmangos/playerbots.git /src/playerbots-fresh

WORKDIR /src/mangos-tbc
RUN mkdir -p build && cd build && \
    cmake .. \
        -DCMAKE_INSTALL_PREFIX=/opt/mangos \
        -DBUILD_PLAYERBOTS=1 \
        -DBUILD_AHBOT=1 \
        -DBUILD_EXTRACTORS=1 \
        -DBUILD_GAME_SERVER=1 \
        -DBUILD_LOGIN_SERVER=1 \
        -DBUILD_TOOLS=1 \
        -DPCH=1

# Compile — -j2 to avoid OOM kills on the Deck's 16GB RAM
RUN cd build && make -j2 && make install

# Preserve SQL files before stage 2 strips them
RUN mkdir -p /opt/mangos/sql && \
    cp -r /src/mangos-tbc/sql/* /opt/mangos/sql/ && \
    mkdir -p /opt/mangos/tbc-db && \
    cp -r /src/tbc-db/* /opt/mangos/tbc-db/ && \
    mkdir -p /opt/mangos/playerbots-sql && \
    cp -r /src/playerbots-fresh/sql/* /opt/mangos/playerbots-sql/

# ──────────────────────────────────────────────────────────────
# Stage 2: Runtime — minimal image with binaries + SQL bundled
# ──────────────────────────────────────────────────────────────
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

RUN apt-get update && apt-get install -y --no-install-recommends \
    libssl3 \
    libmysqlclient21 \
    libace-dev \
    libtbb12 \
    libboost-system1.74.0 \
    libboost-filesystem1.74.0 \
    libboost-program-options1.74.0 \
    libreadline8 \
    netcat-openbsd \
    mariadb-client \
    gzip \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/mangos /opt/mangos

WORKDIR /opt/mangos/bin

CMD ["./mangosd"]
DOCKERFILE
    print_success "Dockerfile written"

    print_info "Starting compile. Logs streaming to /tmp/wow-tbc-build.log"
    print_info "You can tail it from another Konsole: tail -f /tmp/wow-tbc-build.log"
    print_info ""
    print_warning "Expected duration: 2-4 hours. Plug Deck in. Walk away if you need to."
    print_info ""

    (
        ELAPSED=0
        while sleep 300; do
            ELAPSED=$((ELAPSED + 5))
            echo "  ⏳ Still compiling... ${ELAPSED} minutes elapsed. Deck OK? 🌡️"
        done
    ) &
    HEARTBEAT_PID=$!

    if ! $DOCKER_CMD build -t "$SERVER_IMAGE" "$SERVER_DIR" 2>&1 | \
        tee /tmp/wow-tbc-build.log; then
        kill $HEARTBEAT_PID 2>/dev/null
        print_error "Compile failed!"
        print_info "Last 30 lines of build log:"
        tail -30 /tmp/wow-tbc-build.log
        print_info ""
        print_info "Full log: /tmp/wow-tbc-build.log"
        print_info "Common causes:"
        print_info "  • Out of disk space (compile produces 5+ GB of artifacts)"
        print_info "  • Network drop during dependency fetch (re-run the installer)"
        print_info "  • Steam Deck overheated and OOM-killed gcc"
        exit 1
    fi

    kill $HEARTBEAT_PID 2>/dev/null
    print_success "CMaNGOS TBC compiled with Playerbots! 🎉"
}

# ─────────────────────────────────────────
# EXTRACT CLIENT DATA
# ─────────────────────────────────────────
extract_client_data() {
    print_header
    print_step "STEP 4/5 — Extracting Map Data (15-20 minutes)"

    print_info "Running the extractors compiled into our server image..."
    print_info "This reads your WoW TBC client and extracts:"
    print_info "  • Map data (dbc + maps) — includes Outland maps"
    print_info "  • Vmaps (visual obstructions for line-of-sight)"
    print_info "  • Mmaps (movement pathfinding — required for Playerbots)"
    echo ""

    mkdir -p "$SERVER_DIR/data"

    print_info "Running extraction (this takes 15-30 min on Steam Deck)..."
    print_info "Extraction logs: /tmp/wow-tbc-extract.log"
    print_warning "NOTE: Extraction writes temp folders into your client folder."
    print_warning "      These get moved out automatically when extraction finishes."
    echo ""

    if ! $DOCKER_CMD run --rm \
        -v "$CLIENT_DIR:/client" \
        -v "$SERVER_DIR/data:/extracted" \
        --entrypoint /bin/bash \
        "$SERVER_IMAGE" -c '
            set -e
            echo "=== Extraction starting at $(date) ==="
            cd /client
            echo "Working directory: $(pwd)"
            echo ""
            echo "=== Running ExtractResources.sh a (non-interactive, all data) ==="
            /opt/mangos/bin/tools/ExtractResources.sh a || true
            echo ""
            echo "=== Moving outputs to /extracted ==="
            for d in dbc maps vmaps Buildings Cameras CreatureModels; do
                if [ -d "/client/$d" ]; then
                    echo "Moving /client/$d -> /extracted/$d"
                    rm -rf "/extracted/$d" 2>/dev/null
                    mv "/client/$d" "/extracted/$d"
                fi
            done
            for f in /client/MaNGOSExtractor.log /client/MaNGOSExtractor_detailed.log; do
                [ -f "$f" ] && mv "$f" /extracted/
            done
            echo "=== Extraction phase complete! ==="
        ' 2>&1 | tee /tmp/wow-tbc-extract.log; then
        print_warning "Extraction reported errors — checking output anyway"
    fi

    print_info "Fixing file ownership..."
    sudo chown -R "$USER:$USER" "$SERVER_DIR/data" 2>/dev/null || true

    local maps_count dbc_count vmaps_count
    maps_count=$(ls "$SERVER_DIR/data/maps" 2>/dev/null | wc -l)
    dbc_count=$(ls "$SERVER_DIR/data/dbc" 2>/dev/null | wc -l)
    vmaps_count=$(ls "$SERVER_DIR/data/vmaps" 2>/dev/null | wc -l)

    print_info "  Extraction outputs:"
    print_info "    maps:  $maps_count files (expect ~3000-6000 including Outland)"
    print_info "    dbc:   $dbc_count files (expect ~150+)"
    print_info "    vmaps: $vmaps_count files (expect ~2000+)"

    if [ "$maps_count" -lt 100 ] || [ "$dbc_count" -lt 100 ] || [ "$vmaps_count" -lt 100 ]; then
        print_error "Extraction did not produce enough output files!"
        print_info "Likely causes:"
        print_info "  • Client missing Data/expansion.MPQ or locale MPQs (Data/enUS/)"
        print_info "  • Wrong WoW version (must be 2.4.3)"
        print_info "  • Disk full mid-extraction"
        print_info "Full log: /tmp/wow-tbc-extract.log"
        if ! ask_yes_no "Continue anyway? (server WILL fail to load maps)"; then
            exit 1
        fi
    else
        print_success "Extraction outputs validated!"
    fi

    # ── MMAP GENERATION ──────────────────────────────────────────────
    # Required for Playerbots pathfinding. TBC has more maps than
    # Vanilla (Outland + base continents), so allow extra time.
    print_step "Generating mmap pathfinding (~30-50 min — last big wait!)"
    print_info "Progress streams to your terminal. Walk away if you want."
    print_info "When you see '=== Generation done ===', it's finished."
    echo ""

    rm -rf "$SERVER_DIR/data/mmaps"
    mkdir -p "$SERVER_DIR/data/mmaps"

    if ! $DOCKER_CMD run --rm \
        -v "$SERVER_DIR/data:/data" \
        --entrypoint /bin/bash \
        "$SERVER_IMAGE" -c '
            cd /data
            /opt/mangos/bin/tools/MoveMapGen --silent --threads 2
            echo ""
            echo "=== Generation done at $(date) ==="
            echo "Final mmap file count: $(ls /data/mmaps | wc -l)"
        ' 2>&1 | tee /tmp/wow-tbc-mmap-gen.log; then
        print_warning "Mmap generation reported errors — checking output anyway"
    fi

    sudo chown -R "$USER:$USER" "$SERVER_DIR/data/mmaps" 2>/dev/null || true

    local mmap_count
    mmap_count=$(ls "$SERVER_DIR/data/mmaps" 2>/dev/null | wc -l)
    print_info "  mmap files generated: $mmap_count (expect ~3000+ including Outland)"

    if [ "$mmap_count" -lt 500 ]; then
        print_warning "Mmap count is lower than expected."
        print_warning "Server may still boot, but bots will pathfind poorly."
    else
        print_success "Mmaps generated! ($mmap_count files)"
    fi

    print_success "Client data extraction complete!"
}

# ─────────────────────────────────────────
# WRITE COMPOSE + CONFIGS
# ─────────────────────────────────────────
write_compose_and_configs() {
    print_step "Writing compose.yml and configs"

    # ── Persist DB password across re-runs ───────────────────────────
    # DB_PASSWORD is generated fresh each time the script launches.
    # If the MariaDB volume already exists from a previous run, it was
    # initialized with a different password — any new random value will
    # be denied with "Access denied for root". Loading the saved password
    # ensures we always connect to the existing volume with the right
    # credentials. When SERVER_DIR is wiped (fresh install), this file
    # goes with it and a new password is generated and saved cleanly.
    local pw_file="$SERVER_DIR/.db_password"
    if [ -f "$pw_file" ]; then
        DB_PASSWORD=$(cat "$pw_file")
        DB_PASSWORD_LOADED=true
        print_info "Loaded existing database password from previous install."
    else
        echo "$DB_PASSWORD" > "$pw_file"
        chmod 600 "$pw_file"
    fi

    cat > "$SERVER_DIR/compose.yml" << EOF
services:
  db:
    image: mariadb:11
    container_name: tbc-db
    restart: unless-stopped
    environment:
      MARIADB_ROOT_PASSWORD: ${DB_PASSWORD}
      MARIADB_DATABASE: characters
    volumes:
      - db-data:/var/lib/mysql
    networks:
      - tbc-net
    healthcheck:
      test: ["CMD", "mariadb", "-u", "root", "--password=${DB_PASSWORD}", "-e", "SELECT 1"]
      interval: 10s
      timeout: 5s
      retries: 30
      start_period: 60s

  realmd:
    image: ${SERVER_IMAGE}
    container_name: tbc-realmd
    restart: unless-stopped
    depends_on:
      db:
        condition: service_healthy
    ports:
      - "3724:3724"
    volumes:
      - ./etc:/opt/mangos/etc
      - ./data:/opt/mangos/data
    networks:
      - tbc-net
    command: ["./realmd"]

  mangosd:
    image: ${SERVER_IMAGE}
    container_name: tbc-mangosd
    restart: unless-stopped
    depends_on:
      db:
        condition: service_healthy
    ports:
      - "8085:8085"
    volumes:
      - ./etc:/opt/mangos/etc
      - ./data:/opt/mangos/data
    networks:
      - tbc-net
    stdin_open: true
    tty: true
    command: ["./mangosd"]

volumes:
  db-data:

networks:
  tbc-net:
    driver: bridge
EOF

    mkdir -p "$SERVER_DIR/etc"
    print_info "Extracting config templates from compiled image..."
    $DOCKER_CMD run --rm \
        -v "$SERVER_DIR/etc:/out" \
        --entrypoint /bin/bash \
        "$SERVER_IMAGE" -c '
            cp /opt/mangos/etc/mangosd.conf.dist /out/mangosd.conf 2>/dev/null || true
            cp /opt/mangos/etc/realmd.conf.dist /out/realmd.conf 2>/dev/null || true
            cp /opt/mangos/etc/ahbot.conf.dist /out/ahbot.conf 2>/dev/null || true
            cp -r /opt/mangos/etc/*aiplayerbot*.dist /out/ 2>/dev/null || true
            cd /out
            for f in *.dist; do
                [ -f "$f" ] && mv "$f" "${f%.dist}"
            done
        '

    if [ -f "$SERVER_DIR/etc/mangosd.conf" ]; then
        sed -i "s|^LoginDatabaseInfo.*|LoginDatabaseInfo = \"db;3306;mangos;${DB_PASSWORD};realmd\"|" \
            "$SERVER_DIR/etc/mangosd.conf"
        sed -i "s|^WorldDatabaseInfo.*|WorldDatabaseInfo = \"db;3306;mangos;${DB_PASSWORD};mangos\"|" \
            "$SERVER_DIR/etc/mangosd.conf"
        sed -i "s|^CharacterDatabaseInfo.*|CharacterDatabaseInfo = \"db;3306;mangos;${DB_PASSWORD};characters\"|" \
            "$SERVER_DIR/etc/mangosd.conf"
        sed -i "s|^LogsDatabaseInfo.*|LogsDatabaseInfo = \"db;3306;mangos;${DB_PASSWORD};logs\"|" \
            "$SERVER_DIR/etc/mangosd.conf"
        sed -i "s|^DataDir\s*=.*|DataDir = \"/opt/mangos/data\"|" \
            "$SERVER_DIR/etc/mangosd.conf"

        local patches_ok=0
        for pattern in "LoginDatabaseInfo.*${DB_PASSWORD}" \
                       "WorldDatabaseInfo.*${DB_PASSWORD}" \
                       "CharacterDatabaseInfo.*${DB_PASSWORD}" \
                       "LogsDatabaseInfo.*${DB_PASSWORD}" \
                       "DataDir.*/opt/mangos/data"; do
            if grep -q "^${pattern%%.*}" "$SERVER_DIR/etc/mangosd.conf" 2>/dev/null && \
               grep -qE "$pattern" "$SERVER_DIR/etc/mangosd.conf" 2>/dev/null; then
                patches_ok=$((patches_ok + 1))
            fi
        done

        if [ $patches_ok -eq 5 ]; then
            print_success "mangosd.conf patched (all 5/5 verified)"
        else
            print_warning "mangosd.conf patching incomplete — only $patches_ok/5 verified."
            print_warning "Server will likely fail to connect to the database."
            print_info "Check $SERVER_DIR/etc/mangosd.conf before starting."
        fi
    else
        print_error "mangosd.conf not extracted from image!"
        exit 1
    fi

    if [ -f "$SERVER_DIR/etc/realmd.conf" ]; then
        sed -i "s|^LoginDatabaseInfo.*|LoginDatabaseInfo = \"db;3306;mangos;${DB_PASSWORD};realmd\"|" \
            "$SERVER_DIR/etc/realmd.conf"
        print_success "realmd.conf patched"
    fi

    # ── Playerbots: 1600–2000 random bots ────────────────────────────
    if [ -f "$SERVER_DIR/etc/aiplayerbot.conf" ]; then
        sed -i "s|^AiPlayerbot\.MinRandomBots .*|AiPlayerbot.MinRandomBots = 1600|" \
            "$SERVER_DIR/etc/aiplayerbot.conf"
        sed -i "s|^AiPlayerbot\.MaxRandomBots .*|AiPlayerbot.MaxRandomBots = 2000|" \
            "$SERVER_DIR/etc/aiplayerbot.conf"
        sed -i "s|^AiPlayerbot\.RandomBotAccountCount .*|AiPlayerbot.RandomBotAccountCount = 400|" \
            "$SERVER_DIR/etc/aiplayerbot.conf"
        print_success "aiplayerbot.conf patched (1600–2000 bots, 400 accounts)"
    fi

    # ── AHBot: high-volume auction house (~15k items target) ─────────
    if [ -f "$SERVER_DIR/etc/ahbot.conf" ]; then
        sed -i "s|^AuctionHouseBot\.Chance\.Sell .*|AuctionHouseBot.Chance.Sell = 75|" \
            "$SERVER_DIR/etc/ahbot.conf"
        sed -i "s|^AuctionHouseBot\.Loot\.Creature\.Normal .*|AuctionHouseBot.Loot.Creature.Normal    =   90, 100, 30, 40|" \
            "$SERVER_DIR/etc/ahbot.conf"
        sed -i "s|^AuctionHouseBot\.Loot\.Creature\.Rare .*|AuctionHouseBot.Loot.Creature.Rare      =    0,  30,  1,  2|" \
            "$SERVER_DIR/etc/ahbot.conf"
        sed -i "s|^AuctionHouseBot\.Loot\.Creature\.Elite .*|AuctionHouseBot.Loot.Creature.Elite     =   80,  90,  4,  6|" \
            "$SERVER_DIR/etc/ahbot.conf"
        sed -i "s|^AuctionHouseBot\.Loot\.Creature\.RareElite .*|AuctionHouseBot.Loot.Creature.RareElite =    0,  15,  1,  2|" \
            "$SERVER_DIR/etc/ahbot.conf"
        sed -i "s|^AuctionHouseBot\.Loot\.Creature\.WorldBoss .*|AuctionHouseBot.Loot.Creature.WorldBoss =  -10,   2,  1,  1|" \
            "$SERVER_DIR/etc/ahbot.conf"
        sed -i "s|^AuctionHouseBot\.Loot\.Disenchant .*|AuctionHouseBot.Loot.Disenchant =   40,  50,  2,  3|" \
            "$SERVER_DIR/etc/ahbot.conf"
        sed -i "s|^AuctionHouseBot\.Loot\.Fishing .*|AuctionHouseBot.Loot.Fishing    =   12,  18,100,150|" \
            "$SERVER_DIR/etc/ahbot.conf"
        sed -i "s|^AuctionHouseBot\.Loot\.Gameobject .*|AuctionHouseBot.Loot.Gameobject =   50,  60, 30, 45|" \
            "$SERVER_DIR/etc/ahbot.conf"
        sed -i "s|^AuctionHouseBot\.Loot\.Skinning .*|AuctionHouseBot.Loot.Skinning   =   12,  18,200,250|" \
            "$SERVER_DIR/etc/ahbot.conf"
        sed -i "s|^AuctionHouseBot\.Items\.Profession .*|AuctionHouseBot.Items.Profession = 250, 300, 0, 50|" \
            "$SERVER_DIR/etc/ahbot.conf"
        print_success "ahbot.conf patched (Chance.Sell=75, high-volume loot)"
    fi

    print_success "Configs written and patched"
}

# ─────────────────────────────────────────
# SETUP DATABASE
# ─────────────────────────────────────────
setup_database() {
    print_header
    print_step "Setting up databases (~2-5 min on Steam Deck SSD)"

    cd "$SERVER_DIR" || exit 1

    $DOCKER_CMD rm -f tbc-db 2>/dev/null || true

    # ── Stale volume detection ────────────────────────────────────────
    # If we generated a NEW password (DB_PASSWORD_LOADED=false) but a DB
    # volume already exists from a previous run, that volume was initialized
    # with a different password — connecting will fail with "Access denied".
    # Safe fix: wipe the stale volume so MariaDB re-initializes cleanly.
    local compose_project
    compose_project=$(basename "$SERVER_DIR")
    local db_volume="${compose_project}_db-data"
    if [ "${DB_PASSWORD_LOADED}" = false ]; then
        if $DOCKER_CMD volume ls -q 2>/dev/null | grep -qx "${db_volume}"; then
            print_warning "Found stale MariaDB volume with unknown password — removing so DB re-initializes cleanly..."
            $DOCKER_CMD volume rm "${db_volume}" 2>/dev/null || true
        fi
    fi

    # Pre-pull the MariaDB image so the download doesn't eat into the
    # startup timer below. If the image is already cached this is instant.
    print_info "Pulling MariaDB image..."
    $DOCKER_CMD pull mariadb:11 2>&1 | tail -3 || \
        print_warning "Image pull had warnings — continuing"

    print_info "Starting MariaDB container..."
    if ! $DOCKER_CMD compose up -d db; then
        print_error "Failed to start db container."
        print_info "Check: $DOCKER_CMD compose logs db"
        exit 1
    fi

    # ── Wait for MariaDB to be ready (up to 5 minutes) ───────────────
    # First-run initialization (InnoDB setup, system tables, root account)
    # regularly takes 2-3 minutes on Steam Deck hardware. We watch Docker's
    # own healthcheck status so we exit the moment the server is ready,
    # and bail fast if Docker marks it unhealthy.
    print_info "Waiting for MariaDB to be ready (up to 5 min on first run)..."
    local elapsed=0
    while [ $elapsed -lt 300 ]; do
        local health
        health=$($DOCKER_CMD inspect \
            --format='{{.State.Health.Status}}' tbc-db 2>/dev/null || echo "unknown")

        if [ "$health" = "healthy" ]; then
            break
        fi

        if [ "$health" = "unhealthy" ]; then
            echo ""
            print_error "MariaDB healthcheck failed — container is unhealthy."
            print_info "Last 20 lines from the container:"
            $DOCKER_CMD logs tbc-db --tail 20 2>&1 || true
            print_info "Full logs: $DOCKER_CMD logs tbc-db"
            exit 1
        fi

        # Fallback: also try a direct connection in case healthcheck is slow
        if $DOCKER_CMD exec -e MYSQL_PWD="${DB_PASSWORD}" tbc-db \
            mariadb -u root -e "SELECT 1" >/dev/null 2>&1; then
            break
        fi

        printf "."
        sleep 5
        elapsed=$((elapsed + 5))
    done
    echo ""

    if [ $elapsed -ge 300 ]; then
        print_error "MariaDB didn't respond within 5 minutes."
        print_info "Last 20 lines from the container:"
        $DOCKER_CMD logs tbc-db --tail 20 2>&1 || true
        print_info "Full logs: $DOCKER_CMD logs tbc-db"
        exit 1
    fi
    print_success "MariaDB ready"

    # ── Credential check ─────────────────────────────────────────────────
    # mariadb-admin ping exits 0 even on auth failure, so "ready" above is
    # liveness only. Verify the password actually works; if not, the volume
    # was initialized with a different password — wipe it and reinitialize.
    if ! $DOCKER_CMD exec -e MYSQL_PWD="${DB_PASSWORD}" tbc-db \
        mariadb -u root -e "SELECT 1" >/dev/null 2>&1; then
        print_warning "Password mismatch — volume initialized with a different password."
        print_warning "Removing stale volume and reinitializing (data will be re-imported)..."
        $DOCKER_CMD compose down 2>/dev/null || true
        $DOCKER_CMD volume rm "${db_volume}" 2>/dev/null || true
        print_info "Restarting MariaDB with correct password..."
        if ! $DOCKER_CMD compose up -d db; then
            print_error "Failed to restart db container after volume reset."
            exit 1
        fi
        local reinit_elapsed=0
        while [ $reinit_elapsed -lt 300 ]; do
            if $DOCKER_CMD exec -e MYSQL_PWD="${DB_PASSWORD}" tbc-db \
                mariadb -u root -e "SELECT 1" >/dev/null 2>&1; then
                break
            fi
            printf "."
            sleep 5
            reinit_elapsed=$((reinit_elapsed + 5))
        done
        echo ""
        if [ $reinit_elapsed -ge 300 ]; then
            print_error "MariaDB didn't respond within 5 minutes after volume reset."
            $DOCKER_CMD logs tbc-db --tail 20 2>&1 || true
            exit 1
        fi
        print_success "MariaDB reinitialized with correct password"
    fi

    # Phase 1: Create databases + mangos user
    print_info "Creating mangos database user with grants..."
    if ! $DOCKER_CMD exec -i -e MYSQL_PWD="${DB_PASSWORD}" tbc-db \
        mariadb -u root <<EOF 2>&1 | tail -3
CREATE DATABASE IF NOT EXISTS mangos CHARACTER SET utf8mb4;
CREATE DATABASE IF NOT EXISTS realmd CHARACTER SET utf8mb4;
CREATE DATABASE IF NOT EXISTS characters CHARACTER SET utf8mb4;
CREATE DATABASE IF NOT EXISTS logs CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS 'mangos'@'%' IDENTIFIED BY '${DB_PASSWORD}';
GRANT ALL PRIVILEGES ON mangos.* TO 'mangos'@'%';
GRANT ALL PRIVILEGES ON realmd.* TO 'mangos'@'%';
GRANT ALL PRIVILEGES ON characters.* TO 'mangos'@'%';
GRANT ALL PRIVILEGES ON logs.* TO 'mangos'@'%';
FLUSH PRIVILEGES;
EOF
    then
        print_error "Failed to create mangos user"
        exit 1
    fi
    print_success "Created 4 databases + mangos@% user"

    local compose_net
    compose_net=$($DOCKER_CMD network ls --format '{{.Name}}' 2>/dev/null \
                  | grep "tbc-net" | head -1)
    if [ -z "$compose_net" ]; then
        print_error "Couldn't find the tbc-net Docker network."
        print_info "Run: $DOCKER_CMD network ls"
        exit 1
    fi
    print_info "Using Docker network: $compose_net"

    # Phase 2: Import base schemas (realmd, characters, logs)
    print_info "Importing base schemas (realmd, characters, logs)..."
    for schema in realmd characters logs; do
        if ! $DOCKER_CMD run --rm --network "$compose_net" \
            -e MYSQL_PWD="${DB_PASSWORD}" \
            "$SERVER_IMAGE" sh -c "
                mariadb -h db -u root ${schema} < /opt/mangos/sql/base/${schema}.sql
            " 2>&1 | tail -3; then
            print_warning "Schema ${schema}.sql had errors (may be OK if already imported)"
        else
            print_success "  ${schema} schema imported"
        fi
    done

    # Phase 3: Import tbc-db Full World Database
    print_info "Importing TBC world content (creatures/items/quests/Outland — fast on Deck SSD)..."
    if ! $DOCKER_CMD run --rm --network "$compose_net" \
        -e MYSQL_PWD="${DB_PASSWORD}" \
        "$SERVER_IMAGE" sh -c "
            gunzip -c /opt/mangos/tbc-db/Full_DB/TBCDB_*.sql.gz | \
                mariadb -h db -u root mangos
        " 2>&1 | tail -3; then
        print_error "Full DB import failed"
        exit 1
    fi
    print_success "TBC world content imported"

    # Phase 4: Apply tbc-db content Updates
    print_info "Applying tbc-db content updates..."
    $DOCKER_CMD run --rm --network "$compose_net" \
        -e MYSQL_PWD="${DB_PASSWORD}" \
        "$SERVER_IMAGE" sh -c '
            cd /opt/mangos/tbc-db/Updates
            for sql in $(ls -v *.sql 2>/dev/null); do
                mariadb -h db -u root mangos < "$sql" 2>/dev/null
            done
            echo "Done"
        ' 2>&1 | tail -2
    print_success "Content updates applied"

    # Phase 5: Apply ACID scripts
    print_info "Importing ACID creature AI scripts..."
    $DOCKER_CMD run --rm --network "$compose_net" \
        -e MYSQL_PWD="${DB_PASSWORD}" \
        "$SERVER_IMAGE" sh -c '
            for f in /opt/mangos/tbc-db/ACID/*.sql; do
                [ -f "$f" ] && mariadb -h db -u root mangos < "$f" 2>/dev/null
            done
            echo "Done"
        ' 2>&1 | tail -2 || print_warning "ACID had errors (may be OK)"
    print_success "ACID scripts imported"

    # Phase 6: Import DBC-derived tables (instance_dungeon_encounters, spell, world_state_expression)
    # These live in sql/base/dbc/ and are NOT included in the Full_DB dump.
    # Missing tables cause mangosd to crash on startup with "Table doesn't exist".
    print_info "Importing DBC-derived tables (dungeon encounters, spells, world state)..."
    $DOCKER_CMD run --rm --network "$compose_net" \
        -e MYSQL_PWD="${DB_PASSWORD}" \
        "$SERVER_IMAGE" sh -c '
            for sql in /opt/mangos/sql/base/dbc/original_data/*.sql; do
                [ -f "$sql" ] && mariadb -h db -u root mangos < "$sql" 2>/dev/null
            done
            for sql in /opt/mangos/sql/base/dbc/cmangos_fixes/*.sql; do
                [ -f "$sql" ] && mariadb -h db -u root mangos < "$sql" 2>/dev/null
            done
            echo "Done"
        ' 2>&1 | tail -2
    print_success "DBC-derived tables imported"

    # Phase 7: Apply core schema updates
    print_info "Applying core schema updates (mangos, realmd, characters, logs)..."
    $DOCKER_CMD run --rm --network "$compose_net" \
        -e MYSQL_PWD="${DB_PASSWORD}" \
        "$SERVER_IMAGE" sh -c '
            for db in mangos realmd characters logs; do
                cd /opt/mangos/sql/updates/$db 2>/dev/null || continue
                for sql in $(ls -v *.sql 2>/dev/null); do
                    mariadb -h db -u root "$db" < "$sql" 2>/dev/null
                done
            done
            echo "Done"
        ' 2>&1 | tail -2
    print_success "Core schema updates applied"

    # Phase 8: Apply spell_template column extensions
    # Same columns needed as Classic — CMaNGOS TBC shares this codebase
    # quirk where update scripts fail via stdin pipe before the ALTER.
    # ADD COLUMN IF NOT EXISTS is idempotent — safe to run always.
    print_info "Applying spell_template column extension (6 columns)..."
    $DOCKER_CMD exec -i -e MYSQL_PWD="${DB_PASSWORD}" tbc-db \
        mariadb -u root mangos <<'EOF' 2>&1 | tail -3 || true
ALTER TABLE spell_template
    ADD COLUMN IF NOT EXISTS EffectBonusCoefficient1 FLOAT NOT NULL DEFAULT '0' AFTER RequiredAuraVision,
    ADD COLUMN IF NOT EXISTS EffectBonusCoefficient2 FLOAT NOT NULL DEFAULT '0' AFTER EffectBonusCoefficient1,
    ADD COLUMN IF NOT EXISTS EffectBonusCoefficient3 FLOAT NOT NULL DEFAULT '0' AFTER EffectBonusCoefficient2,
    ADD COLUMN IF NOT EXISTS EffectBonusCoefficientFromAP1 FLOAT NOT NULL DEFAULT '0' AFTER EffectBonusCoefficient3,
    ADD COLUMN IF NOT EXISTS EffectBonusCoefficientFromAP2 FLOAT NOT NULL DEFAULT '0' AFTER EffectBonusCoefficientFromAP1,
    ADD COLUMN IF NOT EXISTS EffectBonusCoefficientFromAP3 FLOAT NOT NULL DEFAULT '0' AFTER EffectBonusCoefficientFromAP2;
EOF
    print_success "spell_template extended"

    # Phase 9: Import Playerbots SQL
    # Characters DB: shared tables
    # World DB: shared tables + TBC-specific tables (sql/world/tbc/)
    print_info "Importing Playerbots SQL..."
    $DOCKER_CMD run --rm --network "$compose_net" \
        -e MYSQL_PWD="${DB_PASSWORD}" \
        "$SERVER_IMAGE" sh -c '
            for sql in /opt/mangos/playerbots-sql/characters/*.sql; do
                [ -f "$sql" ] && mariadb -h db -u root characters < "$sql" 2>/dev/null
            done
            for sql in /opt/mangos/playerbots-sql/world/*.sql; do
                [ -f "$sql" ] && mariadb -h db -u root mangos < "$sql" 2>/dev/null
            done
            for sql in /opt/mangos/playerbots-sql/world/tbc/*.sql; do
                [ -f "$sql" ] && mariadb -h db -u root mangos < "$sql" 2>/dev/null
            done
            echo "Done"
        ' 2>&1 | tail -2
    print_success "Playerbots SQL imported"

    # Phase 10: Verify
    print_info "Verifying database setup..."
    local item_count
    item_count=$($DOCKER_CMD exec -i -e MYSQL_PWD="${DB_PASSWORD}" tbc-db \
        mariadb -u root mangos -sN -e \
        "SELECT COUNT(*) FROM item_template" 2>/dev/null)
    local bot_tables
    bot_tables=$($DOCKER_CMD exec -i -e MYSQL_PWD="${DB_PASSWORD}" tbc-db \
        mariadb -u root mangos -sN -e \
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='mangos' AND table_name LIKE 'ai_playerbot%'" 2>/dev/null)

    print_info "  item_template rows: ${item_count:-unknown} (expect ~20,000+ for TBC)"
    print_info "  ai_playerbot_* tables: ${bot_tables:-unknown} (expect ~12)"

    if [ "${item_count:-0}" -ge 10000 ] 2>/dev/null && [ "${bot_tables:-0}" -ge 10 ] 2>/dev/null; then
        print_success "Database setup looks healthy!"
    else
        print_warning "Database setup may be incomplete — see warnings above"
        print_info "You can proceed, but mangosd may fail to boot."
    fi

    # Phase 11: Set account expansion default to TBC (1)
    # The realmd schema defaults expansion=0 (Vanilla), which blocks Blood Elf
    # and Draenei from the character creation screen. Flip the default to 1 so
    # every account created via "account create" is TBC-enabled, and update any
    # accounts the schema pre-seeded (ADMINISTRATOR, GAMEMASTER, MODERATOR).
    print_info "Setting account expansion default to TBC (enables Blood Elf + Draenei)..."
    $DOCKER_CMD exec -i -e MYSQL_PWD="${DB_PASSWORD}" tbc-db \
        mariadb -u root realmd <<'EOF' 2>&1 | tail -3 || true
ALTER TABLE account MODIFY COLUMN expansion TINYINT(3) UNSIGNED NOT NULL DEFAULT 1;
UPDATE account SET expansion = 1;
EOF
    print_success "Account expansion default set to TBC (Blood Elf + Draenei unlocked)"
}

# ─────────────────────────────────────────
# FIRST START + READY WAIT
# ─────────────────────────────────────────
start_server() {
    print_header
    print_step "Starting world & login servers"

    cd "$SERVER_DIR" || exit 1

    print_info "Starting mangosd (world) and realmd (login)..."
    if ! $DOCKER_CMD compose up -d mangosd realmd; then
        print_error "Failed to start mangosd/realmd."
        print_info "Check: cd $SERVER_DIR && $DOCKER_CMD compose logs"
        exit 1
    fi

    print_info "Containers started. Waiting for world server to be ready..."
    print_info "(TBC + Playerbots loads 200+ bots — first boot takes 3-8 min on Steam Deck.)"
    echo ""

    # Snapshot the restart count NOW so we only fail on NEW restarts this session,
    # not on leftover counts from a previous crashed run.
    local base_restart_count
    base_restart_count=$($DOCKER_CMD inspect --format '{{.RestartCount}}' \
        tbc-mangosd 2>/dev/null || echo "0")

    TIMEOUT=600
    ELAPSED=0
    READY=0
    RESTART_DETECTED=0

    while [ $ELAPSED -lt $TIMEOUT ]; do
        # Ready signal: mangosd prints "Avg Diff:" once it enters its main update loop.
        # "World initialized" doesn't appear in CMaNGOS TBC output.
        if $DOCKER_CMD logs tbc-mangosd --since 15m 2>&1 | \
            grep -q "Avg Diff:"; then
            READY=1
            break
        fi

        local restart_count
        restart_count=$($DOCKER_CMD inspect --format '{{.RestartCount}}' \
            tbc-mangosd 2>/dev/null || echo "0")

        # Only declare a restart loop if we see 4+ NEW restarts since this wait started.
        # A single transient restart during DB stabilization is normal; a loop is not.
        local new_restarts=$(( restart_count - base_restart_count ))
        if [ "$new_restarts" -ge 4 ] 2>/dev/null; then
            RESTART_DETECTED=1
            break
        fi

        printf "."
        sleep 10
        ELAPSED=$((ELAPSED + 10))
    done
    echo ""
    echo ""

    if [ $READY -eq 1 ]; then
        print_success "World server is online!"
        print_info "  Port 8085 (world) and 3724 (login) are listening"
    elif [ $RESTART_DETECTED -eq 1 ]; then
        print_error "mangosd is in a restart loop — boot failed."
        print_info "Check: $DOCKER_CMD logs tbc-mangosd --tail 100"
        print_info ""
        print_info "Most common causes at this point:"
        print_info "  • A schema update didn't apply (check spell_template column count)"
        print_info "  • Playerbots SQL didn't import (check ai_playerbot_* tables)"
        print_info "  • Mmap files missing (check data/mmaps/ has 3000+ files)"
        print_info ""
        if ! ask_yes_no "Continue to account creation anyway? (server won't be usable)"; then
            exit 1
        fi
    else
        print_warning "Server taking longer than 10 min to initialize."
        print_info "It may still be loading — check: $DOCKER_CMD logs tbc-mangosd -f"
        print_info "Look for 'Avg Diff:' to confirm it's running."
    fi
}

# ─────────────────────────────────────────
# CREATE DEFAULT ACCOUNT
# ─────────────────────────────────────────
create_default_account() {
    print_step "Account creation — quick post-install step"

    print_info "Account creation is a quick manual step after install completes."
    print_info "See the next screen for the exact commands (it's two lines)."
    print_info "The mangosd console handles SRP6 password hashing correctly."
}

# ─────────────────────────────────────────
# SETUP GAMING MODE LAUNCHER
# ─────────────────────────────────────────
setup_gaming_mode() {
    print_step "Setting up Gaming Mode launcher"

    local launcher_path="$HOME/wow-tbc-launcher.sh"
    local server_dir="$SERVER_DIR"

    cat > "$launcher_path" << LAUNCHER
#!/bin/bash
# Dad's MMO Lab — Burning Crusade Launcher

unset LD_PRELOAD
unset LD_LIBRARY_PATH

GOLD='\033[38;5;220m'; DIM='\033[2m'
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
WHITE='\033[1;37m'; BOLD='\033[1m'; NC='\033[0m'

LOGFILE=/tmp/wow-tbc-launcher.log
echo "=== TBC Launcher started \$(date) ===" > "\$LOGFILE"

clear
echo ""
printf "${GOLD} ══════════════════════════════════════════════════════════════════════════════════${NC}\n"
printf "   ${DIM}Dad's MMO Lab${NC}  ✦  ${DIM}Burning Crusade${NC}\n"
printf "${GOLD} ══════════════════════════════════════════════════════════════════════════════════${NC}\n"
echo ""
echo -e "  ${WHITE}${BOLD}Starting server...${NC}"
echo ""

# Stop any conflicting WoW servers from other expansions
OTHER=\$(docker ps --format '{{.Names}}' 2>/dev/null | grep -iE "vanilla-|wotlk|azeroth" || true)
if [ -n "\$OTHER" ]; then
    echo -e "  ${YELLOW}⚠️  Stopping other expansion containers...${NC}"
    echo "\$OTHER" | xargs docker stop >> "\$LOGFILE" 2>&1 || true
    sleep 2
fi

cd "${server_dir}" || exit 1

if docker compose up -d >> "\$LOGFILE" 2>&1; then
    echo -e "  ${GREEN}✅ Containers started!${NC}"
else
    echo -e "  ${RED}❌ Failed to start. Check: \$LOGFILE${NC}"
    sleep 10
    exit 1
fi

echo ""
printf "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
echo -e "${WHITE}${BOLD} Waiting for Outland to open...${NC}"
printf "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
echo ""

MANUAL_SHUTDOWN=0
TIMEOUT=480
ELAPSED=0
READY=0
while [ \$ELAPSED -lt \$TIMEOUT ]; do
    if docker logs tbc-mangosd --since 15m 2>&1 | \
        grep -q "Avg Diff:"; then
        READY=1
        break
    fi
    if read -r -t 5 2>/dev/null; then
        MANUAL_SHUTDOWN=1
        break
    fi
    printf "  ${GOLD}.${NC}"
    ELAPSED=\$((ELAPSED + 5))
done

echo ""
echo ""

if [ \$MANUAL_SHUTDOWN -eq 0 ]; then
    if [ \$READY -eq 1 ]; then
        printf "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
        echo -e "${GREEN}${BOLD}  🔥 OUTLAND IS READY!${NC}"
        printf "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
        echo ""
        echo -e "  ${DIM}Login: player / player  •  Realmlist: 127.0.0.1${NC}"
        echo ""
    else
        echo -e "  ${YELLOW}⏳ Server still warming up. Check logs if needed.${NC}"
    fi

    echo -e "  ${WHITE}${BOLD}Press STEAM button and launch WoW${NC}"
    echo -e "  ${DIM}Server AUTO-SHUTS DOWN when WoW closes${NC}"
    echo -e "  ${DIM}── or press ENTER to shut down manually ──${NC}"
    echo ""

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
            echo -e "  ${GREEN}🔥 WoW detected! Enjoy Outland!${NC}"
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
    print_success "Launcher created: ~/wow-tbc-launcher.sh"

    cat > "$SERVER_DIR/MY_SERVER.txt" << INFO
====================================
  Dad's MMO Lab — Burning Crusade
  CMaNGOS TBC + Playerbots
  (source compile)
====================================

SERVER:
  Folder:    ${SERVER_DIR}
  Realmlist: 127.0.0.1
  World:     127.0.0.1:8085
  Login:     127.0.0.1:3724
  Account:   player / player (create in step A below)

LAUNCHER:
  Path: ~/wow-tbc-launcher.sh
  Add to Steam:
    Target:  /usr/bin/konsole
    Options: --hold -e bash ~/wow-tbc-launcher.sh
    Proton:  OFF (launcher needs no Proton; WoW client itself uses Proton)

REALMLIST (in your TBC client folder):
  Edit:  realmlist.wtf
  Set to: set realmlist 127.0.0.1
  Then lock: chmod 444 [path]/realmlist.wtf

USEFUL COMMANDS:
  Start:   cd ${SERVER_DIR} && docker compose up -d
  Stop:    cd ${SERVER_DIR} && docker compose down
  Logs:    cd ${SERVER_DIR} && docker compose logs -f
  Status:  docker ps | grep tbc
  Console: docker attach tbc-mangosd
    (Exit safely: Ctrl+P then Ctrl+Q. NOT Ctrl+C.)

CREATE MORE ACCOUNTS:
  docker attach tbc-mangosd
  account create USERNAME PASSWORD
  account set gmlevel USERNAME 3 -1   (optional: makes account GM)
  [Ctrl+P then Ctrl+Q to exit safely]
INFO

    print_success "MY_SERVER.txt saved to $SERVER_DIR"
}

# ─────────────────────────────────────────
# COMPLETION
# ─────────────────────────────────────────
show_completion() {
    print_header

    local realmlist_path="$CLIENT_DIR/realmlist.wtf"
    local realmlist_written=0

    for _locale in enUS enGB deDE frFR esES esMX ruRU; do
        if [ -f "$CLIENT_DIR/Data/$_locale/realmlist.wtf" ]; then
            realmlist_path="$CLIENT_DIR/Data/$_locale/realmlist.wtf"
            break
        fi
    done

    if [ -e "$realmlist_path" ] || [ -d "$(dirname "$realmlist_path")" ]; then
        chmod 644 "$realmlist_path" 2>/dev/null || true
        echo "set realmlist 127.0.0.1" > "$realmlist_path" 2>/dev/null && {
            chmod 444 "$realmlist_path" 2>/dev/null
            realmlist_written=1
        }
    fi

    echo ""
    echo -e "${GOLD}${BOLD}╔══════════════════════════════════════════════════╗${NC}"
    echo -e "${GOLD}${BOLD}║      🔥 BURNING CRUSADE INSTALLED! 🔥             ║${NC}"
    echo -e "${GOLD}${BOLD}╚══════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${WHITE}${BOLD}You compiled CMaNGOS TBC from source on your Steam Deck.${NC}"
    echo -e "${WHITE}${BOLD}The Dark Portal is open. Outland is yours.${NC}"
    echo ""
    echo -e "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "  ${WHITE}${BOLD}Expansion:${NC}  ${TC}Burning Crusade (2.4.3)${NC}"
    echo -e "  ${WHITE}${BOLD}Server dir:${NC} ${TC}$SERVER_DIR${NC}"
    echo -e "  ${WHITE}${BOLD}Launcher:${NC}   ${TC}~/wow-tbc-launcher.sh${NC}"
    echo -e "  ${WHITE}${BOLD}Bots:${NC}       ${GREEN}Playerbots + AHBot active${NC}"
    echo -e "  ${WHITE}${BOLD}Account:${NC}    ${YELLOW}player / player${NC} (create in step A below)"
    if [ $realmlist_written -eq 1 ]; then
        echo -e "  ${WHITE}${BOLD}Realmlist:${NC}  ${GREEN}auto-configured (locked to 127.0.0.1)${NC}"
    else
        echo -e "  ${WHITE}${BOLD}Realmlist:${NC}  ${YELLOW}NOT auto-written — see step A below${NC}"
    fi
    echo -e "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "${TCB}${BOLD}NEXT STEPS — finish setup (~10 min):${NC}"
    echo ""

    echo -e "${GREEN}${BOLD}A. Create your player account (REQUIRED — 30 seconds):${NC}"
    echo ""
    echo -e "   Open Konsole and run:"
    echo -e "      ${TC}docker attach tbc-mangosd${NC}"
    echo ""
    echo -e "   Type each of these and press Enter:"
    echo -e "      ${TC}account create player player${NC}"
    echo -e "      ${TC}account set gmlevel player 3 -1${NC}"
    echo ""
    echo -e "   To exit safely: ${BOLD}Ctrl+P then Ctrl+Q${NC} (sequential)."
    echo -e "   ${RED}${BOLD}⚠️  NEVER press Ctrl+C — that kills the server!${NC}"
    echo ""

    if [ $realmlist_written -ne 1 ]; then
        echo -e "${WHITE}${BOLD}B. Set up realmlist (auto-write failed):${NC}"
        echo -e "   Edit ${TC}$realmlist_path${NC}"
        echo -e "   Contents: ${TC}set realmlist 127.0.0.1${NC}"
        echo -e "   Lock it:  ${TC}chmod 444 $realmlist_path${NC}"
        echo ""
    fi

    echo -e "${WHITE}${BOLD}C. Add the server launcher to Steam:${NC}"
    echo -e "   Steam → Add a Non-Steam Game → /usr/bin/konsole"
    echo -e "   Rename to: ${TC}Burning Crusade Server${NC}"
    echo -e "   Right-click → Properties → Launch Options:"
    echo -e "      ${TC}--hold -e bash ~/wow-tbc-launcher.sh${NC}"
    echo -e "   Compatibility: ${YELLOW}Proton OFF${NC} (this is a Linux script)"
    echo ""

    echo -e "${WHITE}${BOLD}D. Add the WoW TBC client to Steam:${NC}"
    echo -e "   Steam → Add a Non-Steam Game → WoW.exe (in $CLIENT_DIR)"
    echo -e "   Rename to: ${TC}Burning Crusade WoW${NC}"
    echo -e "   Compatibility: ${GREEN}Force GE-Proton${NC} (latest)"
    echo ""

    echo -e "${WHITE}${BOLD}E. Play in Gaming Mode:${NC}"
    echo -e "   1. Launch ${TC}Burning Crusade Server${NC} from your library"
    echo -e "   2. Wait for ${GREEN}OUTLAND IS READY!${NC}"
    echo -e "   3. Launch ${TC}Burning Crusade WoW${NC} — login: ${TC}player / player${NC}"
    echo -e "   4. Bots populate the world within 5-10 min — be patient!"
    echo ""

    echo -e "${BLUE}ℹ️  Server info: $SERVER_DIR/MY_SERVER.txt${NC}"
    echo -e "${BLUE}ℹ️  Build log: /tmp/wow-tbc-build.log${NC}"
    echo -e "${BLUE}ℹ️  Pre-built Docker images coming soon!${NC}"
    echo -e "${BLUE}   Watch: github.com/DadsMmoLab/dads-mmo-lab${NC}"
    echo ""
}

# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
DOCKER_CMD="docker"

check_system
show_welcome

echo ""
echo -e "\033[1;33m⚠️  This installer needs sudo access for:\033[0m"
echo -e "\033[1;33m   • Installing Docker (if not present)\033[0m"
echo -e "\033[1;33m   • Fixing file ownership after extraction\033[0m"
echo -e "\033[1;33m   • Cleaning up an existing install (if any)\033[0m"
echo ""
echo -e "\033[1;37mPlease enter your password if prompted:\033[0m"
if ! sudo -v; then
    echo -e "\033[0;31m❌ Could not cache sudo credentials. Aborting.\033[0m"
    exit 1
fi
( while true; do sudo -n true; sleep 60; done ) 2>/dev/null &
SUDO_KEEPALIVE_PID=$!
trap "kill $SUDO_KEEPALIVE_PID 2>/dev/null; exit" EXIT INT TERM

locate_client
show_summary
install_docker
do_compile
extract_client_data
write_compose_and_configs
setup_database
start_server
create_default_account
setup_gaming_mode
show_completion
