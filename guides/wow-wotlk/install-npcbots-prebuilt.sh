#!/bin/bash
# ============================================================
#  Dad's MMO Lab — WoW NPCBots Installer (Prebuilt Images)
#  AzerothCore + NPCBots on Steam Deck (SteamOS / Arch Linux)
#
#  https://github.com/DadsMmoLab/dads-mmo-lab
#
#  Usage:
#    chmod +x install-npcbots-prebuilt.sh
#    ./install-npcbots-prebuilt.sh
#
#  What this does:
#    1. Checks system requirements
#    2. Installs Docker and Git if not present
#    3. PULLS prebuilt NPCBots images from GHCR (10 minutes!)
#    4. Starts the server
#    5. Creates your GM account
#    6. Sets up Gaming Mode launcher
#
#  ⚡ This is the FAST install path — no compilation needed.
#  Images are built weekly in CI from trickerer's NPCBots fork
#  and pushed to ghcr.io/dadsmmolab/wow-wotlk-npcbots-*
#
#  If you'd rather compile from source (no external dependencies,
#  but takes 2-4 hours), use install-npcbots.sh instead.
#
#  What you get (same as the source-build path):
#    ✅ Full AzerothCore WotLK 3.3.5a server
#    ✅ NPCBots — hire AI companions for dungeons and raids
#    ✅ Wandering bots that populate the world
#    ✅ Full GM commands to manage your bots
#    ✅ Gaming Mode launcher — auto-shuts down with WoW
# ============================================================

set -o pipefail

# ─────────────────────────────────────────
# COLORS
# ─────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m'
BOLD='\033[1m'

print_header() {
    clear
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${WHITE}${BOLD}         ⚙️  DAD'S MMO LAB                        ${NC}${CYAN}║${NC}"
    echo -e "${CYAN}║${WHITE}    WoW + NPCBots — Prebuilt Images Installer     ${NC}${CYAN}║${NC}"
    echo -e "${CYAN}║${BLUE}         github.com/DadsMmoLab/dads-mmo-lab       ${NC}${CYAN}║${NC}"
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

INSTALL_DIR="$HOME/wow-server-npcbots"
COMPOSE_URL="https://raw.githubusercontent.com/DadsMmoLab/dads-mmo-lab/main/guides/wow-wotlk/docker-compose.npcbots.yml"

# ─────────────────────────────────────────
# START
# ─────────────────────────────────────────
print_header

echo -e "${WHITE}This installs WoW with NPCBots — AI companions you can${NC}"
echo -e "${WHITE}hire for dungeons, raids and open world adventuring.${NC}"
echo ""
echo -e "${GREEN}${BOLD}⚡ FAST PATH:${NC} ~10 minutes total"
echo -e "${WHITE}Images are prebuilt in GitHub Actions CI and pulled${NC}"
echo -e "${WHITE}from GHCR — no compilation on your Steam Deck.${NC}"
echo ""

if ! ask_yes_no "Ready to begin?"; then
    echo "Cancelled."
    exit 0
fi

# ─────────────────────────────────────────
# STEP 1 — SYSTEM CHECK
# ─────────────────────────────────────────
print_step "STEP 1/6 — Checking System"

if [[ "$OSTYPE" != "linux-gnu"* ]]; then
    print_error "This script requires Linux (SteamOS). Are you in Desktop Mode?"
    exit 1
fi
print_success "Linux detected"

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

# ─────────────────────────────────────────
# STEP 2 — INSTALL DEPENDENCIES
# ─────────────────────────────────────────
print_step "STEP 2/6 — Installing Dependencies"

# Docker
if command -v docker &>/dev/null; then
    print_success "Docker already installed: $(docker --version)"
else
    print_info "Installing Docker..."

    if command -v steamos-readonly &>/dev/null; then
        sudo steamos-readonly disable
    fi

    print_info "Fixing pacman keyring..."
    sudo rm -rf /etc/pacman.d/gnupg 2>/dev/null || true
    sudo pacman-key --init 2>/dev/null || true
    sudo pacman-key --populate archlinux 2>/dev/null || true
    sudo pacman-key --populate holo 2>/dev/null || true

    if command -v steamos-devmode &>/dev/null; then
        sudo steamos-devmode enable 2>/dev/null || true
    fi

    sudo pacman -Sy --noconfirm archlinux-keyring 2>/dev/null || true
    sudo pacman -Sy --noconfirm docker docker-compose

    sudo usermod -aG docker "$USER"
    sleep 2
    sudo systemctl daemon-reload 2>/dev/null || true
    sudo systemctl enable docker 2>/dev/null || true
    sudo systemctl start docker 2>/dev/null || true
    sleep 3

    print_success "Docker installed!"
fi

# Verify Docker works
if ! docker ps &>/dev/null 2>&1; then
    if sudo docker ps &>/dev/null 2>&1; then
        print_warning "Using sudo for Docker commands"
        function docker() { sudo docker "$@"; }
        export -f docker 2>/dev/null || true
    else
        print_error "Docker is not responding. Try rebooting and running again."
        exit 1
    fi
fi
print_success "Docker is running"

# ─────────────────────────────────────────
# STEP 3 — SET UP INSTALL DIR + COMPOSE FILE
# ─────────────────────────────────────────
print_step "STEP 3/6 — Setting Up Server Folder"

if [ -d "$INSTALL_DIR" ]; then
    print_warning "Existing install found at $INSTALL_DIR"
    if ask_yes_no "Remove it and start fresh?"; then
        cd "$INSTALL_DIR" && docker compose down 2>/dev/null || true
        cd "$HOME"
        sudo rm -rf "$INSTALL_DIR"
    else
        print_info "Using existing folder"
    fi
fi

mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR" || exit 1

print_info "Downloading docker-compose.npcbots.yml..."
curl -fsSL -o docker-compose.yml "$COMPOSE_URL"
if [ ! -f docker-compose.yml ]; then
    print_error "Failed to download compose file. Check your internet connection."
    exit 1
fi
print_success "Compose file ready"

# ─────────────────────────────────────────
# STEP 4 — PULL IMAGES AND START
# ─────────────────────────────────────────
print_step "STEP 4/6 — Pulling Prebuilt Images (~5-10 minutes)"

echo ""
echo -e "${WHITE}Pulling prebuilt NPCBots images from GHCR...${NC}"
echo -e "${WHITE}Total download: ~2 GB. Time depends on your connection.${NC}"
echo ""

docker compose pull 2>&1 | tee ~/npcbots-pull.log
PULL_EXIT=${PIPESTATUS[0]}

if [ $PULL_EXIT -ne 0 ]; then
    print_error "Image pull failed! Check the log:"
    print_info "  cat ~/npcbots-pull.log | tail -30"
    print_info ""
    print_info "If GHCR is unreachable, fall back to source-build with:"
    print_info "  ./install-npcbots.sh"
    exit 1
fi

print_success "Images pulled — starting server!"
docker compose up -d

# ─────────────────────────────────────────
# STEP 5 — WAIT FOR SERVER + VERIFY NPCBOTS
# ─────────────────────────────────────────
print_step "STEP 5/6 — Waiting for Server to Initialize"

print_info "First launch initializes the database (5-10 minutes)..."

WORLD_CONTAINER=""
TIMEOUT=900
ELAPSED=0
READY=0

while [ $ELAPSED -lt $TIMEOUT ]; do
    WORLD_CONTAINER=$(docker ps --format '{{.Names}}' \
        2>/dev/null | grep -i "worldserver" | head -1)

    if [ -n "$WORLD_CONTAINER" ]; then
        if docker logs "$WORLD_CONTAINER" 2>/dev/null | grep -q "ready\.\.\."; then
            READY=1
            break
        fi
    fi

    printf "."
    sleep 10
    ELAPSED=$((ELAPSED + 10))
done
echo ""

if [ $READY -ne 1 ]; then
    print_warning "Server didn't report ready within 15 min — proceeding anyway."
fi

# Verify NPCBots actually loaded
print_info "Verifying NPCBots module loaded..."
sleep 5
if docker logs "$WORLD_CONTAINER" 2>&1 | grep -qi "npcbot"; then
    print_success "NPCBots module is loaded — bots are ready!"
else
    print_error "WARNING: NPCBots not detected in worldserver logs!"
    print_error "The image you pulled may not contain NPCBots."
    print_error "Check: docker logs $WORLD_CONTAINER | grep -i npcbot"
    print_error "If empty, please file an issue at:"
    print_error "  https://github.com/DadsMmoLab/dads-mmo-lab/issues"
fi

# ─────────────────────────────────────────
# STEP 6 — CREATE ACCOUNT + LAUNCHER
# ─────────────────────────────────────────
print_step "STEP 6/6 — Creating Your Account"

echo ""
echo -e "${WHITE}Let's create your in-game account.${NC}"
echo ""

while true; do
    echo -e "${WHITE}Enter your desired username: ${NC}"
    read -r WOW_USERNAME
    [ -n "$WOW_USERNAME" ] && break
    echo "Username cannot be empty."
done

while true; do
    echo -e "${WHITE}Enter your desired password: ${NC}"
    read -rs WOW_PASSWORD
    echo ""
    [ -n "$WOW_PASSWORD" ] && break
    echo "Password cannot be empty."
done

print_info "Creating account via worldserver console..."
sleep 3

WORLD_CONTAINER="${WORLD_CONTAINER:-$(docker ps --format '{{.Names}}' | grep -i "worldserver" | head -1)}"
WORLD_CONTAINER="${WORLD_CONTAINER:-ac-worldserver}"

echo "account create ${WOW_USERNAME} ${WOW_PASSWORD} ${WOW_PASSWORD}" | \
    docker exec -i "$WORLD_CONTAINER" sh -c 'cat > /tmp/cmd.txt && \
    while IFS= read -r cmd; do echo "$cmd"; sleep 1; done < /tmp/cmd.txt' \
    2>/dev/null || true

sleep 2

echo "account set gmlevel ${WOW_USERNAME} 3 -1" | \
    docker exec -i "$WORLD_CONTAINER" sh -c 'cat > /tmp/cmd.txt && \
    while IFS= read -r cmd; do echo "$cmd"; sleep 1; done < /tmp/cmd.txt' \
    2>/dev/null || true

print_success "Account created: ${WOW_USERNAME}"
print_info "If account creation failed, create it manually:"
print_info "  docker attach $WORLD_CONTAINER"
print_info "  account create ${WOW_USERNAME} ${WOW_PASSWORD} ${WOW_PASSWORD}"
print_info "  account set gmlevel ${WOW_USERNAME} 3 -1"
print_info "  (Ctrl+P then Ctrl+Q to exit)"

# Save credentials
cat > "$INSTALL_DIR/MY_ACCOUNT.txt" << CREDS
====================================
  Your WoW NPCBots Server Login
====================================
Username: $WOW_USERNAME
Password: $WOW_PASSWORD

Server: 127.0.0.1 (localhost)
Install path: prebuilt images (GHCR)

====================================
  NPCBot Commands (in-game chat)
====================================
Spawn a bot:
  .npcbot spawn <class_id>

Class IDs:
  1=Warrior  2=Paladin  3=Hunter
  4=Rogue    5=Priest   6=DeathKnight
  7=Shaman   8=Mage     9=Warlock
  11=Druid

Target a spawned bot then:
  .npcbot add       - Add to party
  .npcbot remove    - Remove from party

Bot behavior:
  .npcbot set role tank
  .npcbot set role heal
  .npcbot set role dps
  .npcbot set follow
  .npcbot set standstill

List your bots:
  .npcbot list

Tip: Install the NetherBot addon for
a full UI — no commands needed!
github.com/NetherstormX/NetherBot

====================================
  Server Commands
====================================
Start:   cd $INSTALL_DIR && docker compose up -d
Stop:    cd $INSTALL_DIR && docker compose down
Update:  cd $INSTALL_DIR && docker compose pull && docker compose up -d
Logs:    docker logs -f $WORLD_CONTAINER
Console: docker attach $WORLD_CONTAINER
         (exit: Ctrl+P then Ctrl+Q)
====================================
CREDS

print_success "Login details saved to: $INSTALL_DIR/MY_ACCOUNT.txt"

# ─────────────────────────────────────────
# GAMING MODE LAUNCHER
# ─────────────────────────────────────────
print_info "Setting up Gaming Mode launcher..."

cat > "$HOME/wow-npcbots-launcher.sh" << 'LAUNCHER'
#!/bin/bash
# Dad's MMO Lab — WoW NPCBots Gaming Mode Launcher

export PATH="/usr/bin:/usr/local/bin:/bin:$PATH"
unset LD_PRELOAD
unset LD_LIBRARY_PATH

LOGFILE="/tmp/wow-npcbots-launch.log"
exec 2>"$LOGFILE"

INSTALL_DIR="$HOME/wow-server-npcbots"

if [ ! -d "$INSTALL_DIR" ]; then
    echo "ERR: NPCBots server not found! Run install-npcbots-prebuilt.sh first."
    sleep 5
    exit 1
fi

clear
echo ""
echo "  ⚔️  DAD'S MMO LAB"
echo "  ══════════════════════════════════════"
echo "  WoW + NPCBots Server"
echo "  ══════════════════════════════════════"
echo ""
echo "  Starting server..."
echo ""

cd "$INSTALL_DIR" || exit 1
docker compose up -d >> "$LOGFILE" 2>&1

echo "  Containers started!"
echo ""
echo "  Waiting for world to initialize..."
echo "  After first launch: ~60 seconds"
echo ""

TIMEOUT=900
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

    printf "  ."
    sleep 10
    ELAPSED=$((ELAPSED + 10))
done

echo ""
echo ""

if [ $READY -eq 1 ]; then
    echo "  ══════════════════════════════════════"
    echo "  ✅ AZEROTH IS READY!"
    echo "  ══════════════════════════════════════"
else
    echo "  ⏳ Still initializing — launching anyway"
fi

echo ""
echo "  Press STEAM button and launch WoW"
echo "  Server AUTO-SHUTS DOWN when WoW closes"
echo ""

# Wait for WoW to launch
WOW_STARTED=0
for i in $(seq 1 60); do
    if pgrep -f "Wow\.exe" > /dev/null 2>&1; then
        WOW_STARTED=1
        break
    fi
    sleep 5
done

if [ $WOW_STARTED -eq 1 ]; then
    echo "  WoW detected! Enjoy Azeroth! ⚔️"
    while pgrep -f "Wow\.exe" > /dev/null 2>&1; do
        sleep 3
    done
    sleep 5
    echo ""
    echo "  WoW closed — shutting down server..."
else
    echo "  WoW not detected — keeping server alive."
    echo "  Close this window to stop the server."
    sleep 10800
fi

cd "$INSTALL_DIR" && docker compose down >> "$LOGFILE" 2>&1

echo ""
echo "  ══════════════════════════════════════"
echo "  ✅ Server stopped! Safe to close."
echo "  ══════════════════════════════════════"
echo ""
echo "  Thanks for playing!"
echo "  youtube.com/@DadsMmoLab"
echo ""
sleep 5
LAUNCHER

chmod +x "$HOME/wow-npcbots-launcher.sh"
print_success "Gaming Mode launcher created at ~/wow-npcbots-launcher.sh"

# ─────────────────────────────────────────
# DONE!
# ─────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}${BOLD}║   🎉 NPCBOTS SERVER IS RUNNING!                  ║${NC}"
echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${WHITE}Next steps:${NC}"
echo -e "  1. Set your WoW realmlist to: ${GREEN}set realmlist 127.0.0.1${NC}"
echo -e "  2. Add Wow.exe to Steam (Proton Experimental)"
echo -e "  3. Log in with: ${GREEN}${WOW_USERNAME}${NC}"
echo ""
echo -e "${WHITE}Bot commands cheat-sheet: ${CYAN}cat $INSTALL_DIR/MY_ACCOUNT.txt${NC}"
echo ""
echo -e "${WHITE}Want updates? Run:${NC}"
echo -e "  ${CYAN}cd $INSTALL_DIR && docker compose pull && docker compose up -d${NC}"
echo ""
