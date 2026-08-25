#!/bin/bash
# ============================================================
#  Dad's MMO Lab — Tortoise WoW Server Installer  (PERSONAL)
#  Source-compile path (MaNGOS Zero / Turtle-WoW solo fork)
#
#  Upstream: https://github.com/Penqle/tortoise-wow   (AGPL-3.0)
#
#  Version: 0.1.0-tortoise   (DEV — personal use only)
#
#  ⚠️  PERSONAL PROJECT.  Upstream README: "not to be used for
#      profit."  Do NOT ship this to finished-installers or the
#      channel.  No paired HOWTO by request.
#
#  What this does:
#    1. Installs Docker if needed
#    2. Builds an Ubuntu 22.04 compile container (ACE + deps)
#    3. Compiles mangosd + realmd + extractors from source
#    4. Extracts map/dbc/vmap/mmap data from your 1.18.1 client
#    5. Imports the bundled database (4 DBs + 186 world files)
#    6. Starts the server (Dockerised) + creates player/player
#    7. Writes the Gaming Mode launcher
#
#  ⚠️  You MUST own the matching client: patch 1.18.1, build 7272
#      (the custom Turtle-WoW client).  Any other build will not work.
#
#  WHY DOCKERISED (build AND run):
#    mangosd links against Ubuntu 22.04's libACE / libmysqlclient /
#    libssl — those exact .so versions don't exist on SteamOS/Arch,
#    so the server runs in a container too.  Bonus: a SteamOS update
#    can't wipe the toolchain (the recurring pacman-wipe problem).
#
#  PROVEN on dev Deck 2026-06-30 (no client): build OK, DB import OK
#    (24s), mangosd connects to all 4 DBs + halts only on missing
#    maps.  UNVERIFIED until run with a real client: the extraction
#    step (Step 5) and final in-game connect — watch those first.
# ============================================================

INSTALLER_VERSION="0.1.0-tortoise"

set -o pipefail

# ─────────────────────────────────────────
# COLORS  (Tortoise = turtle green)
# ─────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m'
BOLD='\033[1m'
# Tortoise — deep green accent
CL='\033[0;32m'
CLB='\033[1;32m'

print_header() {
    clear
    echo ""
    echo -e "${CL}╔══════════════════════════════════════════════════╗${NC}"
    echo -e "${CL}║${WHITE}${BOLD}         🐢  DAD'S MMO LAB                          ${NC}${CL}║${NC}"
    echo -e "${CL}║${WHITE}         Tortoise WoW Server (source-compile)    ${NC}${CL}║${NC}"
    echo -e "${CL}║${BLUE}         MaNGOS Zero · Turtle-WoW solo fork       ${NC}${CL}║${NC}"
    echo -e "${CL}║${YELLOW}         Version ${INSTALLER_VERSION}                  ${NC}${CL}║${NC}"
    echo -e "${CL}╚══════════════════════════════════════════════════╝${NC}"
    echo ""
}

print_step() {
    echo ""
    echo -e "${CL}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD} $1${NC}"
    echo -e "${CL}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

print_success() { echo -e "${GREEN}✅ $1${NC}"; }
print_warning() { echo -e "${YELLOW}⚠️  $1${NC}"; }
print_error()   { echo -e "${RED}❌ $1${NC}"; }
print_info()    { echo -e "${BLUE}ℹ️  $1${NC}"; }

DOCKER_GROUP_CONSENT_DONE=""

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

# ── Docker group consent ─────────────────────────────────────────────────────
# Ported verbatim from install-wow-wotlk-ubuntu.sh 1.4.3. Adding a user to the
# docker group is a root-equivalent grant — `docker run -v /:/mnt --rm -it
# alpine chroot /mnt sh` edits any file on the host — so it is asked for rather
# than done. This script used to do it silently (roadmap, Phase 6 preamble:
# "no silent escalation of host privileges"; audited and fixed 2026-08-24).
#
# No sudoers rule is written anywhere in this script. A NOPASSWD docker rule
# would be redundant beside the group and pure attack surface.
docker_group_consent() {
    # Ask at most once per run.
    if [[ "$DOCKER_GROUP_CONSENT_DONE" == "1" ]]; then
        return 0
    fi

    # Already an active member of the docker group — nothing to change.
    if id -nG "$USER" 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
        DOCKER_GROUP_CONSENT_DONE=1
        return 0
    fi

    echo ""
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}${BOLD} ⚠️  Docker Group Membership — Please Read${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  To let you run ${CYAN}docker${NC} without typing ${CYAN}sudo${NC} each time,"
    echo -e "  the installer can add your user to the ${WHITE}docker${NC} group."
    echo ""
    echo -e "  ${RED}${BOLD}Heads up:${NC} membership in the docker group is effectively"
    echo -e "  equivalent to full root access on this machine. A docker"
    echo -e "  user can, for example, mount your entire disk inside a"
    echo -e "  container and modify any file."
    echo ""
    echo -e "  For a personal, offline game server on your own device this"
    echo -e "  is the standard, documented approach — but if you keep this"
    echo -e "  machine locked down, you can skip it and run docker with"
    echo -e "  ${CYAN}sudo${NC} instead."
    echo ""
    echo -e "  ${WHITE}This installer does NOT create any passwordless sudo rules.${NC}"
    echo ""
    if ! ask_yes_no "Add '$USER' to the docker group (grants root-equivalent access)?"; then
        echo ""
        print_warning "Skipped docker group membership."
        print_info "You'll need to prefix docker commands with 'sudo'."
        DOCKER_GROUP_CONSENT_DONE=1
        return 1
    fi
    DOCKER_GROUP_CONSENT_DONE=1
    return 0
}

# ─────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────
SERVER_DIR="$HOME/tortoise-wow-server"
CLIENT_DIR=""
DB_PASSWORD="tortoise$(date +%s | tail -c 6)"
CLIENT_BUILD="7272"

SOURCE_REPO="https://github.com/Penqle/tortoise-wow.git"

# LOCAL image (built here, not pulled)
IMAGE="dml/tortoise-wow:local"

DOCKER_CMD="docker"
DO_MMAPS=1            # set 0 to skip the long pathfinding-mesh build
LAN_IP=""

# Detect WSL2 once -- used throughout for environment-specific behaviour
IS_WSL2=0
grep -qi microsoft /proc/version 2>/dev/null && IS_WSL2=1

# ─────────────────────────────────────────
# LAN IP
# Steam Deck: client connects via LAN IP (Proton's isolated loopback
#             can't reach 127.x, so we need the Deck's actual LAN IP).
# WSL2:       localhostForwarding=true in .wslconfig makes 127.0.0.1
#             reliable and stable (WSL2 virtual NIC IP changes on restart).
# ─────────────────────────────────────────
detect_lan_ip() {
    if [ "$IS_WSL2" -eq 1 ]; then
        echo "127.0.0.1"
        return
    fi
    LAN_IP=$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')
    [ -z "$LAN_IP" ] && LAN_IP=$(ip -4 addr show 2>/dev/null | awk '/inet /{print $2}' | grep -vE '^127\.' | head -1 | cut -d/ -f1)
    echo "$LAN_IP"
}

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
    if [ -n "$AVAILABLE_GB" ] && [ "$AVAILABLE_GB" -lt 15 ] 2>/dev/null; then
        print_error "Need ~15GB free (source + build + client data). You have ${AVAILABLE_GB}GB."
        exit 1
    fi
    print_success "Disk space OK (${AVAILABLE_GB:-unknown}GB available)"

    if ! ping -c 1 github.com &>/dev/null; then
        print_error "No internet. Please connect and try again."
        exit 1
    fi
    print_success "Internet connection OK"

    LAN_IP=$(detect_lan_ip)
    if [ -z "$LAN_IP" ]; then
        print_error "Could not detect a LAN IP. Connect to Wi-Fi/Ethernet and retry."
        [ "$IS_WSL2" -eq 0 ] && print_info "(The client can't use 127.0.0.1 under Proton — it needs the Deck's LAN IP.)"
        exit 1
    fi
    print_success "LAN IP detected: $LAN_IP"
}

# ─────────────────────────────────────────
# KEYRING + DOCKER (house pattern)
# ─────────────────────────────────────────
check_pacman_keyring() {
    if ! sudo -n pacman -Sy --noconfirm &>/dev/null; then
        print_info "Pacman keyring may need refresh — handled by Docker install if needed."
    fi
}

install_docker() {
    local has_docker=0
    if command -v docker &>/dev/null && docker ps &>/dev/null; then
        has_docker=1
    fi

    if [ "$IS_WSL2" -eq 1 ]; then
        if [ $has_docker -eq 1 ] && docker compose version &>/dev/null; then
            print_success "Docker + Compose available (Docker Desktop / WSL2)"
            DOCKER_CMD="docker"
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

    if [ $has_docker -eq 1 ] && docker compose version &>/dev/null; then
        print_success "Docker + Compose already installed and working"
        DOCKER_CMD="docker"
        return 0
    fi

    if [ $has_docker -eq 1 ]; then
        print_warning "Docker is installed but 'docker compose' is not working."
        print_info "Will install docker-compose alongside existing Docker."
    else
        print_info "Installing Docker + Compose..."
    fi
    check_pacman_keyring
    if command -v steamos-readonly &>/dev/null; then
        sudo steamos-readonly disable 2>/dev/null || print_info "(root may already be writable)"
    fi

    if ! sudo pacman -Sy --noconfirm docker docker-compose; then
        print_error "Failed to install Docker via pacman."
        print_info "If keyring errors: sudo pacman-key --init && sudo pacman-key --populate archlinux holo"
        if command -v steamos-readonly &>/dev/null; then
            sudo steamos-readonly enable 2>/dev/null || true
        fi
        exit 1
    fi
    if command -v steamos-readonly &>/dev/null; then
        sudo steamos-readonly enable 2>/dev/null || true
    fi

    sudo systemctl enable --now docker
    docker_group_consent && sudo usermod -aG docker "$USER" 2>/dev/null || true
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
        print_warning "Docker group not active in this shell yet."
        print_info "Using sudo for this install. After it finishes: log out/in once."
        DOCKER_CMD="sudo env DOCKER_CONFIG=${DOCKER_CONFIG:-$HOME/.docker} docker"
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
    echo -e "${WHITE}This installs a personal, offline ${BOLD}Tortoise WoW${NC}${WHITE} server${NC}"
    echo -e "${WHITE}(a solo-play fork of Turtle-WoW on MaNGOS Zero),${NC}"
    echo -e "${WHITE}compiled from source and run fully in Docker.${NC}"
    echo ""
    echo -e "${CLB}Solo additions baked into this fork:${NC}"
    echo -e "${WHITE}  • Autoscale (dungeon/raid) · Leech system · talent points${NC}"
    echo -e "${YELLOW}  • Playerbots exist but upstream says 'not ready' — off by default${NC}"
    echo ""
    echo -e "${BLUE}ℹ️  Client required: the custom Turtle-WoW client, ${BOLD}build 7272${NC}"
    echo -e "${BLUE}ℹ️  Default account: ${BOLD}player / player${NC}"
    echo -e "${BLUE}ℹ️  Compile ~20-45 min · map extraction extra (mmaps can be 1-3h)${NC}"
    echo ""
    echo -e "${YELLOW}${BOLD}Personal build:${NC} ${YELLOW}AGPL fork marked not-for-profit upstream.${NC}"
    echo ""
    if ! ask_yes_no "Ready to build your tortoise?"; then
        echo "No problem — come back when you're ready!"
        exit 0
    fi
}

# ─────────────────────────────────────────
# LOCATE CLIENT
# ─────────────────────────────────────────
locate_client() {
    print_header
    print_step "STEP 1/7 — Locating Your Client (build $CLIENT_BUILD)"

    echo -e "${WHITE}I need the path to your ${BOLD}Turtle-WoW 1.18.1 (build $CLIENT_BUILD)${NC}${WHITE} folder.${NC}"
    echo -e "${WHITE}It must contain ${CL}WoW.exe${NC}${WHITE} and a ${CL}Data/${NC}${WHITE} folder of .MPQ files.${NC}"
    echo ""
    echo -e "${BLUE}Examples:${NC}  ${CYAN}~/Games/TurtleWoW${NC}   ${CYAN}/run/media/deck/SD/TurtleWoW${NC}"
    echo ""

    while true; do
        echo -e "${WHITE}Enter path to your client folder:${NC}"
        read -r raw_path
        raw_path="${raw_path%\"}"
        raw_path="${raw_path#\"}"
        raw_path="${raw_path%\'}"
        raw_path="${raw_path#\'}"
        CLIENT_DIR="${raw_path/#\~/$HOME}"

        if [ ! -d "$CLIENT_DIR" ]; then
            print_error "Folder doesn't exist: $CLIENT_DIR"; echo ""; continue
        fi
        if [ ! -d "$CLIENT_DIR/Data" ]; then
            print_warning "No Data/ folder inside $CLIENT_DIR"
            ask_yes_no "Use this folder anyway?" && break || continue
        fi
        mpq_count=$(find "$CLIENT_DIR/Data" -maxdepth 2 -iname "*.mpq" 2>/dev/null | wc -l)
        if [ "$mpq_count" -lt 5 ]; then
            print_warning "Only $mpq_count .MPQ files found in Data/. Vanilla-era clients have more."
            ask_yes_no "Continue anyway?" || continue
        fi
        print_success "Client found: $CLIENT_DIR ($mpq_count .MPQ files)"
        break
    done

    echo ""
    print_info "Pathfinding meshes (mmaps) make creatures/bots path properly but"
    print_info "the FULL build can take 1-3 hours on a Deck. The server runs fine"
    print_info "without them (creatures fall back to straight-line movement)."
    if ask_yes_no "Build mmaps now? (No = skip, build later)"; then
        DO_MMAPS=1
    else
        DO_MMAPS=0
        print_info "Skipping mmaps. Re-run extraction later if you want them."
    fi
}

show_summary() {
    print_header
    print_step "STEP 2/7 — Pre-Build Summary"
    echo ""
    echo -e "  ${WHITE}${BOLD}Server:${NC}   ${CL}MaNGOS Zero (Tortoise/Turtle solo fork)${NC}"
    echo -e "  ${WHITE}${BOLD}Client:${NC}   ${CL}$CLIENT_DIR  (build $CLIENT_BUILD)${NC}"
    echo -e "  ${WHITE}${BOLD}Folder:${NC}   ${CL}$SERVER_DIR${NC}"
    echo -e "  ${WHITE}${BOLD}LAN IP:${NC}   ${CL}$LAN_IP${NC}  ${WHITE}(realm address)${NC}"
    echo -e "  ${WHITE}${BOLD}Ports:${NC}    ${CL}realmd 3724 · world 8090 · db 3306${NC}"
    echo -e "  ${WHITE}${BOLD}mmaps:${NC}    $([ "$DO_MMAPS" = 1 ] && echo -e "${GREEN}build${NC}" || echo -e "${YELLOW}skip${NC}")"
    echo -e "  ${WHITE}${BOLD}Account:${NC}  ${CL}player / player${NC}"
    echo ""
    echo -e "${YELLOW}${BOLD}  Only one Docker game server runs at a time${NC}"
    echo -e "${YELLOW}  (they share MariaDB port 3306). Stop others first.${NC}"
    echo ""
    ask_yes_no "Start the build?" || { echo "Come back when you have time!"; exit 0; }
}

# ─────────────────────────────────────────
# CLONE SOURCE
# ─────────────────────────────────────────
clone_source() {
    print_header
    print_step "STEP 3/7 — Fetching source + build container"

    if [ -d "$SERVER_DIR" ]; then
        print_warning "Existing install at $SERVER_DIR"
        if ask_yes_no "Remove it and start fresh?"; then
            ( cd "$SERVER_DIR" && $DOCKER_CMD compose down -v 2>/dev/null ) || true
            sudo rm -rf "$SERVER_DIR"
            print_success "Old install removed"
        else
            print_info "Keeping existing install — exiting."; exit 0
        fi
    fi

    mkdir -p "$SERVER_DIR" "$SERVER_DIR/data"

    # Keep the generated root password somewhere other than the compose file.
    # It is interpolated into MARIADB_ROOT_PASSWORD below and was otherwise
    # unrecoverable, so the launcher had no way to reach this server's database
    # and fell back to a default that was simply wrong. Same shape as the TBC
    # and Vanilla installers: reload it when it already exists, so re-running
    # the script against an existing database does not lock itself out.
    local pw_file="$SERVER_DIR/.db_password"
    local db_password_loaded=false
    if [ -s "$pw_file" ]; then
        # -s, not -f: a zero-byte file is not a password, and treating one as
        # "loaded" would hand MariaDB an empty root password and report success.
        DB_PASSWORD=$(cat "$pw_file")
        db_password_loaded=true
        print_info "Loaded existing database password from previous install."
    else
        # Created empty and locked down BEFORE the secret goes in: writing first
        # and chmod-ing after leaves it world-readable in between.
        rm -f "$pw_file"
        (umask 077; : > "$pw_file")
        chmod 600 "$pw_file"
        echo "$DB_PASSWORD" > "$pw_file"
    fi

    # A generated password is only ever applied to an EMPTY database volume:
    # MariaDB reads MARIADB_ROOT_PASSWORD on first init and never again. So a
    # new password beside a surviving volume is a database nothing can log into.
    if [ "$db_password_loaded" = false ]; then
        local db_volume
        db_volume="$(basename "$SERVER_DIR")_dbdata"
        if docker volume ls -q 2>/dev/null | grep -qx "$db_volume"; then
            print_warning "Found a database volume from an earlier install whose password is"
            print_warning "unknown - removing it so the database re-initialises with this one."
            docker volume rm "$db_volume" 2>/dev/null || true
        fi
    fi
    print_info "Cloning $SOURCE_REPO ..."
    if ! git clone --depth 1 "$SOURCE_REPO" "$SERVER_DIR/src"; then
        print_error "git clone failed."; exit 1
    fi
    print_success "Source cloned to $SERVER_DIR/src"

    # ── Builder/runtime Dockerfile (one image: build deps double as runtime libs) ──
    cat > "$SERVER_DIR/Dockerfile" << 'DOCKERFILE'
FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive TZ=UTC
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake git pkg-config ca-certificates \
    libace-dev default-libmysqlclient-dev libmysqlclient-dev \
    libssl-dev zlib1g-dev libbz2-dev \
    mariadb-client gosu \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/tortoise
DOCKERFILE

    print_info "Building compile/runtime image (apt deps, ~3-5 min)..."
    if ! $DOCKER_CMD build -t "$IMAGE" "$SERVER_DIR" 2>&1 | tee /tmp/tortoise-image.log | tail -3; then
        print_error "Image build failed — see /tmp/tortoise-image.log"; exit 1
    fi
    print_success "Build image ready: $IMAGE"
}

# ─────────────────────────────────────────
# COMPILE
# ─────────────────────────────────────────
do_compile() {
    print_header
    print_step "STEP 4/7 — Compiling mangosd + realmd + tools (~20-45 min)"
    print_info "Logs: /tmp/tortoise-build.log   (tail -f from another Konsole)"
    echo ""

    mkdir -p "$SERVER_DIR/src/_build" "$SERVER_DIR/install"

    # 5-min heartbeat so the user knows it's alive
    ( ELAPSED=0; while sleep 300; do ELAPSED=$((ELAPSED+5)); echo "  ⏳ Still compiling... ${ELAPSED} min. Deck OK? 🌡️"; done ) &
    local HB=$!

    # Build as the invoking user so outputs aren't root-owned.
    # DEBUG_SYMBOLS=OFF slims mangosd (612M -> ~tens of MB).
    # USE_ANTICHEAT=OFF: pointless on a solo offline server and can flag a Proton client.
    if ! $DOCKER_CMD run --rm \
            -u "$(id -u):$(id -g)" \
            -v "$SERVER_DIR/src":/src \
            -v "$SERVER_DIR/install":/install \
            -w /src/_build "$IMAGE" bash -c '
        set -e
        cmake .. \
          -DCMAKE_INSTALL_PREFIX=/install \
          -DUSE_EXTRACTORS=ON -DUSE_SCRIPTS=ON -DUSE_STD_MALLOC=ON \
          -DDEBUG_SYMBOLS=OFF -DUSE_ANTICHEAT=OFF -DALLOW_TURTLE_ADDONS=ON
        make -j4
        make install
    ' 2>&1 | tee /tmp/tortoise-build.log ; then
        kill $HB 2>/dev/null
        print_error "Compile failed — last 30 lines:"; tail -30 /tmp/tortoise-build.log
        exit 1
    fi
    kill $HB 2>/dev/null

    if [ ! -x "$SERVER_DIR/install/bin/mangosd" ] || [ ! -x "$SERVER_DIR/install/bin/realmd" ]; then
        print_error "Binaries missing after build. See /tmp/tortoise-build.log"; exit 1
    fi
    print_success "Compiled: mangosd, realmd, extractors 🐢"
}

# ─────────────────────────────────────────
# EXTRACT CLIENT DATA
# ─────────────────────────────────────────
extract_client_data() {
    print_header
    print_step "STEP 5/7 — Extracting map data from your client"
    print_warning "This is the one step not yet verified against a real client."
    print_info "If it errors, your client build is probably not $CLIENT_BUILD."
    print_info "Logs: /tmp/tortoise-extract.log"
    echo ""

    mkdir -p "$SERVER_DIR/data"

    # Tools run in the image; client mounted read-only, output to data/.
    # Extractors are built FROM this repo for build 7272, so DBC formats match.
    local mmap_cmd=":"
    if [ "$DO_MMAPS" = 1 ]; then
        mmap_cmd='/install/bin/MoveMapGen --silent --doNotFilterDeepWater --offMeshInput /src/tools/mmap/offmesh.txt --settingsInput /src/tools/mmap/mmapSettings.txt'
    fi

    if ! $DOCKER_CMD run --rm \
            -u "$(id -u):$(id -g)" \
            -v "$CLIENT_DIR":/client:ro \
            -v "$SERVER_DIR/data":/out \
            -v "$SERVER_DIR/src":/src \
            -v "$SERVER_DIR/install":/install \
            -w /out "$IMAGE" bash -c "
        set -e
        echo '=== mapextractor (maps + dbc) ==='
        /install/bin/mapextractor -i /client -o /out -e 3
        echo '=== vmapextractor (raw building models) ==='
        /install/bin/vmapextractor -d /client/Data/
        echo '=== vmap_assembler (assemble vmaps) ==='
        mkdir -p /out/vmaps
        /install/bin/vmap_assembler /out/Buildings /out/vmaps
        echo '=== mmaps ==='
        ${mmap_cmd}
        echo '=== outputs ==='
        ls -la /out
    " 2>&1 | tee /tmp/tortoise-extract.log ; then
        print_warning "Extraction reported errors — check /tmp/tortoise-extract.log"
        ask_yes_no "Continue anyway? (server may not boot without maps)" || exit 1
    fi

    # Sanity: maps + dbc are mandatory
    local nmaps ndbc
    nmaps=$(find "$SERVER_DIR/data/maps" -name '*.map' 2>/dev/null | wc -l)
    ndbc=$(find "$SERVER_DIR/data/dbc"  -iname '*.dbc' 2>/dev/null | wc -l)
    if [ "$nmaps" -lt 1 ] || [ "$ndbc" -lt 1 ]; then
        print_error "No maps ($nmaps) or dbc ($ndbc) produced — extraction did not work."
        print_info  "Almost always means the client isn't build $CLIENT_BUILD."
        ask_yes_no "Continue and try to start anyway?" || exit 1
    else
        print_success "Extracted: $nmaps maps, $ndbc dbc files"
    fi
}

# ─────────────────────────────────────────
# COMPOSE + CONFIGS
# ─────────────────────────────────────────
write_compose_and_configs() {
    print_step "STEP 6/7 — Writing compose + configs + database"

    mkdir -p "$SERVER_DIR/etc" "$SERVER_DIR/logs"

    # Generate confs from the compiled .dist templates, with CORRECT DB names.
    # NOTE: the shipped default points CharacterDatabase.Info at "tw_chars"
    # (plural) but the DB created is "tw_char" (singular) — we fix that here.
    $DOCKER_CMD run --rm -u "$(id -u):$(id -g)" \
        -v "$SERVER_DIR/install":/install -v "$SERVER_DIR/etc":/etc_out "$IMAGE" bash -c '
        cd /install/etc
        sed -E \
          -e "s#^LoginDatabase\.Info.*#LoginDatabase.Info = \"db;3306;mangos;'"$DB_PASSWORD"';tw_logon\"#" \
          -e "s#^WorldDatabase\.Info.*#WorldDatabase.Info = \"db;3306;mangos;'"$DB_PASSWORD"';tw_world\"#" \
          -e "s#^CharacterDatabase\.Info.*#CharacterDatabase.Info = \"db;3306;mangos;'"$DB_PASSWORD"';tw_char\"#" \
          -e "s#^LogsDatabase\.Info.*#LogsDatabase.Info = \"db;3306;mangos;'"$DB_PASSWORD"';tw_logs\"#" \
          -e "s#^Database\.AutoUpdate\.Path.*#Database.AutoUpdate.Path = \"/opt/tortoise/sql/\"#" \
          -e "s#^DataDir.*#DataDir = \"/opt/tortoise/data\"#" \
          -e "s#^AutoHonorRestart.*#AutoHonorRestart = 0#" \
          -e "s#^AutoRestart\.MaxServerUptime.*#AutoRestart.MaxServerUptime = 0#" \
          -e "s#^GameType.*#GameType = 0#" \
          -e "s#^GM\.LoginState.*#GM.LoginState = 0#" \
          -e "s#^GM\.StartLevel.*#GM.StartLevel = 1#" \
          mangosd.conf.dist > /etc_out/mangosd.conf
        sed -E "s#^LoginDatabaseInfo.*#LoginDatabaseInfo = \"db;3306;mangos;'"$DB_PASSWORD"';tw_logon\"#" \
          realmd.conf.dist \
          | sed -E "s#^ForcePinAccountRank.*#ForcePinAccountRank = 10#" \
          > /etc_out/realmd.conf
    '
    print_success "Configs written (DB names fixed: tw_char, not tw_chars)"

    # docker-compose: db (restart:no per port-collision rule) + realmd + mangosd
    cat > "$SERVER_DIR/docker-compose.yml" << EOF
services:
  db:
    image: mariadb:10.6
    container_name: tortoise-db
    restart: "no"
    environment:
      MARIADB_ROOT_PASSWORD: ${DB_PASSWORD}
      MARIADB_USER: mangos
      MARIADB_PASSWORD: ${DB_PASSWORD}
    ports:
      - "3306:3306"
    volumes:
      - dbdata:/var/lib/mysql
    networks: [tortoise-net]
    healthcheck:
      test: ["CMD", "healthcheck.sh", "--connect", "--innodb_initialized"]
      interval: 10s
      timeout: 5s
      retries: 40
      start_period: 60s

  realmd:
    image: ${IMAGE}
    container_name: tortoise-realmd
    restart: "no"
    depends_on:
      db: { condition: service_healthy }
    ports:
      - "3724:3724"
    volumes:
      - ./install:/opt/tortoise
      - ./etc/realmd.conf:/opt/tortoise/bin/realmd.conf:ro
      - ./logs:/opt/tortoise/logs
    working_dir: /opt/tortoise/bin
    command: ["./realmd", "-c", "/opt/tortoise/bin/realmd.conf"]
    networks: [tortoise-net]

  mangosd:
    image: ${IMAGE}
    container_name: tortoise-mangosd
    restart: "no"
    depends_on:
      db: { condition: service_healthy }
    ports:
      - "8090:8090"
    volumes:
      - ./install:/opt/tortoise
      - ./etc/mangosd.conf:/opt/tortoise/bin/mangosd.conf:ro
      - ./data:/opt/tortoise/data
      - ./src/sql:/opt/tortoise/sql
      - ./logs:/opt/tortoise/logs
    working_dir: /opt/tortoise/bin
    command: ["./mangosd", "-c", "/opt/tortoise/bin/mangosd.conf"]
    stdin_open: true
    tty: true
    networks: [tortoise-net]

volumes:
  dbdata:

networks:
  tortoise-net:
    driver: bridge
EOF
    print_success "docker-compose.yml written"
}

setup_database() {
    print_info "Starting database + importing schema (4 DBs + 186 world files)..."
    cd "$SERVER_DIR" || exit 1

    $DOCKER_CMD compose up -d db
    print_info "Waiting for MariaDB to be healthy..."
    local tries=0
    until [ "$($DOCKER_CMD inspect -f '{{.State.Health.Status}}' tortoise-db 2>/dev/null)" = "healthy" ]; do
        sleep 5; tries=$((tries+1))
        [ $tries -gt 60 ] && { print_error "MariaDB never became healthy."; exit 1; }
    done
    print_success "MariaDB healthy"

    # Import schema + base content + grants. We share the db container's network
    # namespace (--network container:tortoise-db) so the DB is reachable at
    # 127.0.0.1 — avoids guessing the compose-generated network name.
    if ! $DOCKER_CMD run --rm --network "container:tortoise-db" \
            -v "$SERVER_DIR/src/sql":/sql:ro "$IMAGE" bash -c "
        set -e
        echo '=== schema (create_databases.sql) ==='
        mariadb -h127.0.0.1 -uroot -p'${DB_PASSWORD}' < /sql/create_databases.sql
        echo '=== grants for mangos on all 4 DBs ==='
        for d in tw_world tw_char tw_logon tw_logs; do
          mariadb -h127.0.0.1 -uroot -p'${DB_PASSWORD}' -e \"GRANT ALL ON \\\`\$d\\\`.* TO 'mangos'@'%'; FLUSH PRIVILEGES;\"
        done
        echo '=== 186 base world files ==='
        n=0; for f in \$(ls /sql/base/*.sql | sort); do
          mariadb -h127.0.0.1 -uroot -p'${DB_PASSWORD}' tw_world < \"\$f\" || { echo FAIL \$f; exit 1; }
          n=\$((n+1))
        done
        echo \"imported \$n base files\"
    " 2>&1 | tee /tmp/tortoise-dbimport.log | tail -6 ; then
        print_error "Database import failed — see /tmp/tortoise-dbimport.log"; exit 1
    fi
    print_success "Database imported (mangosd auto-applies updates on first boot)"

    # Seed the realm row: address = LAN IP, world port 8090, build 7272.
    # (No realmlist row ships in the dump.)
    $DOCKER_CMD run --rm --network "container:tortoise-db" "$IMAGE" \
        mariadb -h127.0.0.1 -uroot -p"${DB_PASSWORD}" tw_logon -e \
        "REPLACE INTO realmlist (id,name,address,port,icon,realmflags,timezone,allowedSecurityLevel,population,realmbuilds)
         VALUES (1,'Tortoise WoW','${LAN_IP}',8090,0,0,0,0,0,'${CLIENT_BUILD}');"
    print_success "Realm row set → ${LAN_IP}:8090 (build ${CLIENT_BUILD})"
}

# ─────────────────────────────────────────
# START + READY WAIT
# ─────────────────────────────────────────
start_server() {
    print_header
    print_step "STEP 7/7 — Starting the server (first boot applies DB updates)"
    cd "$SERVER_DIR" || exit 1

    $DOCKER_CMD compose up -d
    print_info "Waiting for the world to come up..."
    local TIMEOUT=600 ELAPSED=0 READY=0
    while [ $ELAPSED -lt $TIMEOUT ]; do
        # NOTE: grep WITHOUT -q (pipefail + grep -q SIGPIPEs docker logs → false negatives)
        if $DOCKER_CMD logs tortoise-mangosd 2>&1 | grep -E "World initialized|MaNGOS.*started up successfully|Ready to login" >/dev/null; then
            READY=1; break
        fi
        if $DOCKER_CMD logs tortoise-mangosd 2>&1 | grep -E "Correct \*.map files not found|Could not open|Database .* not found" >/dev/null; then
            echo ""; print_error "mangosd failed to start cleanly:"
            $DOCKER_CMD logs --tail 15 tortoise-mangosd 2>&1
            return 1
        fi
        printf "."; sleep 10; ELAPSED=$((ELAPSED+10))
    done
    echo ""
    if [ $READY -eq 1 ]; then
        print_success "World server is online! 🐢"
    else
        print_warning "Server slow to report ready. Check: docker logs -f tortoise-mangosd"
    fi
}

create_default_account() {
    print_info "Creating default account player/player..."
    sleep 8
    # MaNGOS console: account create <user> <pass>; gmlevel 3 for solo-GM convenience.
    if printf 'account create player player\naccount set gmlevel player 3 -1\n' \
        | timeout 20 $DOCKER_CMD attach tortoise-mangosd >/dev/null 2>&1; then
        print_success "Account player/player created (GM level 3)"
    else
        print_warning "Auto account-create didn't confirm. Create it manually:"
        print_info "  docker attach tortoise-mangosd"
        print_info "  account create player player"
        print_info "  account set gmlevel player 3 -1"
        print_info "  (exit safely: Ctrl+P then Ctrl+Q — NOT Ctrl+C)"
    fi
}

# ─────────────────────────────────────────
# GAMING MODE LAUNCHER  (Steam Deck only)
# WSL2: skip launcher -- DML Launcher tray handles start/stop.
# ─────────────────────────────────────────
setup_gaming_mode() {
    if [ "$IS_WSL2" -eq 1 ]; then
        print_step "Writing server info (WSL2 / Windows)"
        cat > "$SERVER_DIR/MY_SERVER.txt" << INFO
====================================
  Dad's MMO Lab — Tortoise WoW  (personal)
  MaNGOS Zero / Turtle-WoW solo fork
====================================

SERVER:
  Folder:    ${SERVER_DIR}
  Realm IP:  127.0.0.1   (Windows localhost via WSL2 forwarding)
  World:     127.0.0.1:8090
  Login:     127.0.0.1:3724
  Account:   player / player   (GM level 3)
  Client:    build ${CLIENT_BUILD}

CLIENT (realmlist.wtf in your WoW client folder on Windows):
  set realmlist 127.0.0.1

START / STOP:
  Right-click the DML Launcher tray icon -> tortoise-wow-server -> Start / Stop
  (Or from inside dml-arch: dml start tortoise-wow-server)

MANUAL COMMANDS (from inside dml-arch):
  Start:   cd ${SERVER_DIR} && docker compose up -d
  Stop:    cd ${SERVER_DIR} && docker compose down
  Logs:    docker logs -f tortoise-mangosd
  Console: docker attach tortoise-mangosd   (exit: Ctrl+P then Ctrl+Q)

NOTE: only one DML game server runs at a time (shared port 3306).
      Stop others before starting Tortoise WoW.
INFO
        print_success "MY_SERVER.txt written to $SERVER_DIR"

        # Register in games dir so DML Launcher tray app sees it
        local games_dir="$HOME/games"
        local link_name
        link_name=$(basename "$SERVER_DIR")
        mkdir -p "$games_dir"
        if [ ! -e "$games_dir/$link_name" ]; then
            ln -s "$SERVER_DIR" "$games_dir/$link_name"
            print_success "Registered in DML Launcher: $games_dir/$link_name"
        fi
        return
    fi

    print_step "Writing Gaming Mode launcher"
    local launcher="$HOME/tortoise-wow-launcher.sh"

    cat > "$launcher" << LAUNCHER
#!/bin/bash
# Dad's MMO Lab — Tortoise WoW Launcher (re-pins LAN IP each run)
unset LD_PRELOAD; unset LD_LIBRARY_PATH
export PATH=/usr/bin:/bin:/usr/local/bin:\$PATH
SERVER_DIR="${SERVER_DIR}"
LOG=/tmp/tortoise-wow-launcher.log
echo "=== Tortoise WoW launcher \$(date) ===" > "\$LOG"

echo ""
echo "  🐢  Tortoise WoW server starting up..."
echo ""

# Detect current LAN IP (DHCP-resilient)
LAN_IP=\$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if(\$i=="src"){print \$(i+1); exit}}')
[ -z "\$LAN_IP" ] && LAN_IP=\$(ip -4 addr show 2>/dev/null | awk '/inet /{print \$2}' | grep -vE '^127\\.' | head -1 | cut -d/ -f1)
if [ -z "\$LAN_IP" ]; then echo "  ERR: no LAN IP — connect to a network."; sleep 15; exit 1; fi
echo "  LAN IP: \$LAN_IP"

# One Docker game at a time — stop other DML stacks holding port 3306
OTHER=\$(docker ps --format '{{.Names}}' 2>/dev/null | grep -iE 'mariadb|ac-database|maplestory|muonline|l2j|akk-stack' | grep -v tortoise || true)
[ -n "\$OTHER" ] && { echo "  Stopping other game DBs: \$OTHER"; echo "\$OTHER" | xargs docker stop >>"\$LOG" 2>&1 || true; }

cd "\$SERVER_DIR" || exit 1
docker compose up -d db >>"\$LOG" 2>&1
# wait db healthy
for i in \$(seq 1 60); do
  [ "\$(docker inspect -f '{{.State.Health.Status}}' tortoise-db 2>/dev/null)" = healthy ] && break
  sleep 3
done
# Re-pin the realm address to the current LAN IP
docker exec tortoise-db mariadb -uroot -p"${DB_PASSWORD}" tw_logon \
  -e "UPDATE realmlist SET address='\$LAN_IP', port=8090 WHERE id=1;" >>"\$LOG" 2>&1 \
  && echo "  Realm address re-pinned to \$LAN_IP:8090"

docker compose up -d >>"\$LOG" 2>&1 || { echo "  ERR: start failed (see \$LOG)"; sleep 10; exit 1; }

echo ""
echo "  Waiting for the world to open..."
READY=0
for i in \$(seq 1 120); do
  if docker logs tortoise-mangosd 2>&1 | grep -E "World initialized|started up successfully|Ready to login" >/dev/null; then READY=1; break; fi
  sleep 5
done
echo ""
if [ \$READY -eq 1 ]; then
  echo "  ╔════════════════════════════════════════╗"
  echo "  ║   🐢  TORTOISE WOW IS READY!            ║"
  echo "  ╚════════════════════════════════════════╝"
  echo "    Login:     player / player"
  echo "    Realmlist: set realmlist \$LAN_IP"
else
  echo "  ⏳ Still warming up — check \$LOG if it stalls."
fi
echo ""
echo "  Press STEAM and launch the WoW client. Server stops when it closes."
echo ""

# Watch for the client; shut down when it exits
STARTED=0
for i in \$(seq 1 120); do
  if pgrep -fi "WoW\\.exe|wine.*[Ww]o[Ww]" >/dev/null 2>&1; then STARTED=1; break; fi
  sleep 5
done
if [ \$STARTED -eq 1 ]; then
  echo "  Client detected — enjoy! 🐢"
  while pgrep -fi "WoW\\.exe|wine.*[Ww]o[Ww]" >/dev/null 2>&1; do sleep 4; done
  echo "  Client closed — shutting down server..."
else
  echo "  Client not detected — keeping server up for this session."
  while pgrep -fi "WoW\\.exe|wine.*[Ww]o[Ww]" >/dev/null 2>&1; do sleep 4; done
fi

cd "\$SERVER_DIR" && docker compose down >>"\$LOG" 2>&1
echo ""
echo "  ✅ Server stopped. Thanks for playing! 🐢"
sleep 4
LAUNCHER
    chmod +x "$launcher"
    print_success "Launcher: ~/tortoise-wow-launcher.sh"

    cat > "$SERVER_DIR/MY_SERVER.txt" << INFO
====================================
  Dad's MMO Lab — Tortoise WoW  (personal)
  MaNGOS Zero / Turtle-WoW solo fork
====================================

SERVER:
  Folder:    ${SERVER_DIR}
  Realm IP:  ${LAN_IP}   (re-pinned each launch)
  World:     ${LAN_IP}:8090
  Login:     ${LAN_IP}:3724
  Account:   player / player   (GM level 3)
  Client:    build ${CLIENT_BUILD}

CLIENT realmlist.wtf (in your client folder):
  set realmlist ${LAN_IP}

LAUNCHER (Steam → Add Non-Steam Game):
  Target:  /usr/bin/konsole
  Options: --hold -e bash ~/tortoise-wow-launcher.sh
  Proton:  OFF  (the WoW client itself uses GE-Proton)

COMMANDS:
  Start:   cd ${SERVER_DIR} && docker compose up -d
  Stop:    cd ${SERVER_DIR} && docker compose down
  Logs:    docker logs -f tortoise-mangosd
  Console: docker attach tortoise-mangosd   (exit: Ctrl+P then Ctrl+Q)

NOTE: only one DML Docker game runs at a time (shared port 3306).
INFO
    print_success "MY_SERVER.txt written"
}

show_completion() {
    print_header
    echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}${BOLD}║         🐢  TORTOISE WOW INSTALLED!  🐢            ║${NC}"
    echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  ${WHITE}${BOLD}Folder:${NC}   ${CL}$SERVER_DIR${NC}"
    echo -e "  ${WHITE}${BOLD}Launcher:${NC} ${CL}~/tortoise-wow-launcher.sh${NC}"
    echo -e "  ${WHITE}${BOLD}Account:${NC}  ${CL}player / player${NC}"
    echo -e "  ${WHITE}${BOLD}Realm IP:${NC} ${CL}$LAN_IP:8090${NC}"
    echo ""
    echo -e "${CLB}NEXT STEPS:${NC}"
    if [ "$IS_WSL2" -eq 1 ]; then
        echo -e "${WHITE}A. Set your client realmlist:${NC} open ${CL}realmlist.wtf${NC} in your WoW folder"
        echo -e "   and set it to exactly: ${CL}set realmlist 127.0.0.1${NC}"
        echo -e "${WHITE}B. Start the server:${NC} right-click the ${CL}DML Launcher${NC} tray icon"
        echo -e "   ${CL}-> tortoise-wow-server -> Start${NC}"
        echo -e "${WHITE}C. Launch WoW.exe${NC} directly from Windows — no Proton needed."
    else
        echo -e "${WHITE}A. Point your client:${NC} edit ${CL}$CLIENT_DIR/realmlist.wtf${NC}"
        echo -e "   to exactly: ${CL}set realmlist $LAN_IP${NC}"
        echo -e "${WHITE}B. Add launcher to Steam:${NC} /usr/bin/konsole, Proton OFF,"
        echo -e "   Options: ${CL}--hold -e bash ~/tortoise-wow-launcher.sh${NC}"
        echo -e "${WHITE}C. Add WoW.exe to Steam${NC} with ${GREEN}GE-Proton${NC}, then play."
    fi
    echo ""
    echo -e "${BLUE}ℹ️  Full info: $SERVER_DIR/MY_SERVER.txt${NC}"
    echo -e "${YELLOW}ℹ️  Build/extract logs: /tmp/tortoise-build.log, /tmp/tortoise-extract.log${NC}"
    echo ""
}

# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
check_system
show_welcome
locate_client
show_summary
install_docker
clone_source
do_compile
extract_client_data
write_compose_and_configs
setup_database
start_server
create_default_account
setup_gaming_mode
show_completion
