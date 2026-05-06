#!/bin/bash
# ============================================================
#  Dad's MMO Lab — WoW NPCBots Installer
#  AzerothCore + NPCBots on Steam Deck (SteamOS / Arch Linux)
#
#  https://github.com/DadsMmoLab/dads-mmo-lab
#
#  Usage:
#    chmod +x install-npcbots.sh
#    ./install-npcbots.sh
#
#  What this does:
#    1. Checks system requirements
#    2. Installs Docker and Git if not present
#    3. Clones AzerothCore with NPCBots (trickerer fork)
#    4. Compiles the server FROM SOURCE (takes 2-4 hours!)
#    5. Starts the server
#    6. Creates your GM account
#    7. Gives you bot management instructions
#
#  ⚠️  IMPORTANT: This script compiles from source.
#  Leave your Steam Deck plugged in and don't let it sleep!
#  Compilation takes 2-4 hours on a Steam Deck.
#
#  What you get:
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
    echo -e "${CYAN}║${WHITE}         WoW + NPCBots Installer                  ${NC}${CYAN}║${NC}"
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

# ─────────────────────────────────────────
# START
# ─────────────────────────────────────────
print_header

echo -e "${WHITE}This installs WoW with NPCBots — AI companions you can${NC}"
echo -e "${WHITE}hire for dungeons, raids and open world adventuring.${NC}"
echo ""
echo -e "${RED}${BOLD}⚠️  IMPORTANT — READ THIS FIRST:${NC}"
echo -e "${YELLOW}This installer compiles AzerothCore from source code.${NC}"
echo -e "${YELLOW}This takes 2-4 HOURS on a Steam Deck.${NC}"
echo -e "${YELLOW}Please:${NC}"
echo -e "  • Keep your Steam Deck PLUGGED IN"
echo -e "  • Disable sleep mode before starting"
echo -e "  • Don't close this terminal"
echo ""
echo -e "${WHITE}The best time to run this is overnight.${NC}"
echo ""

if ! ask_yes_no "Ready to begin? (Plug in your Steam Deck first!)"; then
    echo "No problem! Run this script again when you're ready."
    exit 0
fi

# ─────────────────────────────────────────
# STEP 1 — SYSTEM CHECK
# ─────────────────────────────────────────
print_step "STEP 1/7 — Checking System"

if [[ "$OSTYPE" != "linux-gnu"* ]]; then
    print_error "This script requires Linux (SteamOS). Are you in Desktop Mode?"
    exit 1
fi
print_success "Linux detected"

AVAILABLE_GB=$(df -BG "$HOME" 2>/dev/null | awk 'NR==2 {print $4}' | sed 's/G//' | tr -d ' ')
if [ -n "$AVAILABLE_GB" ] && [ "$AVAILABLE_GB" -lt 30 ] 2>/dev/null; then
    print_error "Not enough disk space. You have ${AVAILABLE_GB}GB free, need at least 30GB."
    print_info "NPCBots requires more space than standard WoW due to compilation."
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
print_step "STEP 2/7 — Installing Dependencies"

# Docker
if command -v docker &>/dev/null; then
    print_success "Docker already installed: $(docker --version)"
else
    print_info "Installing Docker..."

    if command -v steamos-readonly &>/dev/null; then
        sudo steamos-readonly disable
    fi

    # Fix pacman keyring
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

# Git
if command -v git &>/dev/null; then
    print_success "Git already installed"
else
    print_info "Installing Git..."
    sudo pacman -Sy --noconfirm git 2>/dev/null || \
    sudo apt-get install -y git 2>/dev/null || true
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
# STEP 3 — CLONE NPCBOTS REPO
# ─────────────────────────────────────────
print_step "STEP 3/7 — Downloading NPCBots Source"

print_info "Cloning AzerothCore with NPCBots..."
print_info "Source: github.com/trickerer/AzerothCore-wotlk-with-NPCBots"
print_info "Credit: trickerer — thank you for maintaining this fork!"
echo ""

if [ -d "$INSTALL_DIR" ]; then
    print_warning "Existing install found at $INSTALL_DIR"
    if ask_yes_no "Remove it and start fresh?"; then
        sudo rm -rf "$INSTALL_DIR"
    else
        print_info "Using existing folder — skipping clone"
    fi
fi

if [ ! -d "$INSTALL_DIR" ]; then
    git clone --depth 1 \
        https://github.com/trickerer/AzerothCore-wotlk-with-NPCBots.git \
        "$INSTALL_DIR"

    if [ ! -f "$INSTALL_DIR/docker-compose.yml" ]; then
        print_error "Clone failed. Check your internet connection."
        exit 1
    fi
    print_success "NPCBots source downloaded!"
fi

cd "$INSTALL_DIR" || exit 1

# ─────────────────────────────────────────
# STEP 4 — COMPILE AND START
# ─────────────────────────────────────────
print_step "STEP 4/7 — Compiling Server (2-4 Hours!)"

echo ""
echo -e "${RED}${BOLD}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${RED}${BOLD}║   ⏰ COMPILATION STARTING — THIS TAKES HOURS!    ║${NC}"
echo -e "${RED}${BOLD}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}Your Steam Deck will now compile AzerothCore from${NC}"
echo -e "${YELLOW}source code. This is completely normal and expected.${NC}"
echo ""
echo -e "${WHITE}During compilation you will see lots of C++ output.${NC}"
echo -e "${WHITE}This is normal — just let it run!${NC}"
echo ""
echo -e "${CYAN}Progress is being saved to: ~/npcbots-build.log${NC}"
echo -e "${CYAN}You can check it anytime in another terminal with:${NC}"
echo -e "${CYAN}  tail -f ~/npcbots-build.log${NC}"
echo ""
print_info "Starting compilation now..."
echo ""

# Build and start — log output to file too
# [PR4] Pin local tag so failed builds cannot silently fall back to upstream :master
echo "DOCKER_IMAGE_TAG=npcbots-local" > .env
docker compose up -d --build 2>&1 | tee ~/npcbots-build.log

BUILD_EXIT=${PIPESTATUS[0]}

if [ $BUILD_EXIT -ne 0 ]; then
    print_error "Build failed! Check the log:"
    print_info "  cat ~/npcbots-build.log | tail -50"
    exit 1
fi

print_success "Compilation complete and server starting!"

# ─────────────────────────────────────────
# STEP 5 — WAIT FOR SERVER
# ─────────────────────────────────────────
print_step "STEP 5/7 — Waiting for Server to Initialize"

print_info "First launch initializes the database..."
print_info "This takes an additional 5-10 minutes after compilation."
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

    printf "."
    sleep 10
    ELAPSED=$((ELAPSED + 10))
done

echo ""

if [ $READY -eq 0 ]; then
    print_warning "Server is taking longer than expected."
    print_info "Check progress: docker logs -f $WORLD_CONTAINER"
    print_info "Wait for 'ready...' then run this script again to create your account."
    exit 0
fi

print_success "Server is LIVE! ⚔️"

# ─────────────────────────────────────────
# STEP 6 — CREATE ACCOUNT
# ─────────────────────────────────────────
print_step "STEP 6/7 — Creating Your Account"

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

WORLD_CONTAINER=$(docker ps --format '{{.Names}}' \
    2>/dev/null | grep -i "worldserver" | head -1)
WORLD_CONTAINER="${WORLD_CONTAINER:-ac-worldserver}"

# Create account via console
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
Logs:    docker logs -f $WORLD_CONTAINER
Console: docker attach $WORLD_CONTAINER
         (exit: Ctrl+P then Ctrl+Q)
====================================
CREDS

print_success "Login details saved to: $INSTALL_DIR/MY_ACCOUNT.txt"

# ─────────────────────────────────────────
# STEP 7 — GAMING MODE LAUNCHER
# ─────────────────────────────────────────
print_step "STEP 7/7 — Setting Up Gaming Mode"

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
    echo "ERR: NPCBots server not found! Run install-npcbots.sh first."
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
echo "  First launch: 5-15 minutes"
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
echo -e "${WHITE}Set your WoW realmlist to: ${GREEN}set realmlist 127.0.0.1${NC}"
echo ""
echo -e "${WHITE}Login: ${CYAN}${WOW_USERNAME}${NC}"
echo ""
echo -e "${WHITE}${BOLD}First thing to do in game:${NC}"
echo -e "  Type: ${CYAN}.npcbot spawn 2${NC} (spawns a Paladin bot)"
echo -e "  Click the bot then: ${CYAN}.npcbot add${NC}"
echo -e "  Install NetherBot addon for easy bot management!"
echo -e "  ${CYAN}github.com/NetherstormX/NetherBot${NC}"
echo ""
echo -e "${WHITE}${BOLD}Add to Steam Gaming Mode:${NC}"
echo -e "  Target:  ${CYAN}/usr/bin/konsole${NC}"
echo -e "  Options: ${CYAN}--hold -e bash ~/wow-npcbots-launcher.sh${NC}"
echo -e "  Proton:  ${CYAN}OFF${NC}"
echo ""
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${WHITE}  📺 youtube.com/@DadsMmoLab${NC}"
echo -e "${WHITE}  📦 github.com/DadsMmoLab/dads-mmo-lab${NC}"
echo -e "${WHITE}  ⭐ Star the repo if NPCBots changed your life!${NC}"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "${GREEN}${BOLD}Welcome to Azeroth. Now you don't have to go alone. ⚔️${NC}"
echo ""
