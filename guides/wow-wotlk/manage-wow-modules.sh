#!/bin/bash
# ============================================================
#  Dad's MMO Lab — WoW Module Manager
#  manage-wow-modules.sh
#
#  Post-install management for AzerothCore WoW servers:
#    - Add/remove modules (AH Bot, Solocraft, Transmog, etc.)
#    - Start / stop / restart / check status of the server
#    - View live logs
#    - Attach to worldserver console (for `account create` etc.)
#    - Configure AH Bot with a bot character
#
#  Works with all three install variants from install-wow.sh:
#    - Base WoW (acore-docker, prebuilt images)
#    - NPCBots (acore-docker with NPCBots SQL)
#    - Playerbots (mod-playerbots fork, already source-built)
#
#  Module operations only work on Playerbots (which is already
#  set up for source build). For Base/NPCBots, the rebuild path
#  is EXPERIMENTAL and clearly marked.
#
#  Usage:
#    chmod +x manage-wow-modules.sh
#    ./manage-wow-modules.sh
#
#  https://github.com/DadsMmoLab/dads-mmo-lab
# ============================================================

MANAGER_VERSION="2.1.0 - ALE Drinker Edition"

set -o pipefail

RST='\033[0m'; BOLD='\033[1m'
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; WHITE='\033[1;37m'; CYAN='\033[0;36m'
GOLD='\033[38;5;220m'; DIM='\033[2m'

# ─────────────────────────────────────────────────────────────
# UI HELPERS
# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────
# LOGO & SCREEN SETUP
# ─────────────────────────────────────────────────────────────
# Layout (1-indexed rows):
#   Row  1    : blank
#   Rows 2-9  : 8 logo lines  (animated during intro, static thereafter)
#   Row  10   : blank
#   Row  11   : separator bar
#   Row  12   : "WoW Module Manager  ✦  vX.Y.Z"
#   Row  13   : separator bar
#   Row  14   : blank
#   Row  15+  : menu content
MENU_START_ROW=15
MENU_INPUT_ROW=24
ANIM_PID=""
_IN_ALT_SCREEN=false

# When true, INT signal exits the script.  Set false during full-screen operations
# (e.g. docker logs -f) so Ctrl+C kills the child but returns to the menu.
_ALLOW_INT_EXIT=true

# Logo lines (shared between static draw and intro animation loop).
_LOGO_L0="                           ▄▄  ▄█                                                                                        "
_LOGO_L1="▀███▀▀▀██▄               ▀███  ██           ▀████▄     ▄███▀████▄     ▄███▀ ▄▄█▀▀██▄     ▀████▀         ██     ▀███▀▀▀██▄"
_LOGO_L2="  ██    ▀██▄               ██  ▀▀             ████    ████   ████    ████ ▄██▀    ▀██▄     ██          ▄██▄      ██    ██"
_LOGO_L3="  ██     ▀██▄█▀██▄    ▄█▀▀███     ▄██▀███     █ ██   ▄█ ██   █ ██   ▄█ ██ ██▀      ▀██     ██         ▄█▀██▄     ██    ██"
_LOGO_L4="  ██      ███   ██  ▄██    ██     ██   ▀▀     █  ██  █▀ ██   █  ██  █▀ ██ ██        ██     ██        ▄█  ▀██     ██▀▀▀█▄▄"
_LOGO_L5="  ██     ▄██▄█████  ███    ██     ▀█████▄     █  ██▄█▀  ██   █  ██▄█▀  ██ ██▄      ▄██     ██     ▄  ████████    ██    ▀█"
_LOGO_L6="  ██    ▄██▀█   ██  ▀██    ██     █▄   ██     █  ▀██▀   ██   █  ▀██▀   ██ ▀██▄    ▄██▀     ██    ▄█ █▀      ██   ██    ▄█"
_LOGO_L7="▄████████▀ ▀████▀██▄ ▀████▀███▄   ██████▀   ▄███▄ ▀▀  ▄████▄███▄ ▀▀  ▄████▄ ▀▀████▀▀     █████████████▄   ▄████▄████████"

# Intro animation loop — runs as a forked subprocess for ~3 seconds at startup.
# Writes to /dev/tty directly. Uses save/restore cursor escape sequences.
# Fire palette: red(196) → orange(202) → amber(208) → gold(214) → yellow(220) → bright(226)
_logo_anim_loop() {
    local -a L=("$1" "$2" "$3" "$4" "$5" "$6" "$7" "$8")
    # Palette index 0 = hot red, ascending = cooler/brighter
    local -a P=(196 196 202 208 214 220 226 220 214 208 202 196)
    local -a S=(0 0 1 2 1 0)   # shimmer nudge per phase
    local plen=12 slen=6 llen=8
    local f=0 ci ph

    while true; do
        # Save cursor, jump to logo row 2, hide cursor
        printf '\033[s\033[2;1H\033[?25l' > /dev/tty
        local i
        for ((i=0; i<llen; i++)); do
            # Invert i so bottom line (i=7) maps to palette index 0 (hottest red).
            # Offset by frame counter f so the heat band rises over time.
            ci=$(( ( (llen - 1 - i) * 2 + f) % plen ))
            ph=$(( (i + f) % slen ))
            ci=$(( (ci + S[ph]) % plen ))
            printf "\033[38;5;%dm%s\033[K\033[0m\n" "${P[$ci]}" "${L[$i]}" > /dev/tty
        done
        # Restore cursor, show cursor
        printf '\033[u\033[?25h' > /dev/tty
        f=$(( (f + 1) % plen ))
        sleep 0.07
    done
}

stop_logo_animation() {
    if [ -n "$ANIM_PID" ]; then
        /bin/kill "$ANIM_PID" 2>/dev/null
        wait "$ANIM_PID" 2>/dev/null
        ANIM_PID=""
    fi
}

# Read a menu choice into global _MENU_INPUT.
# Positions the cursor at the given row; backspace/delete work via the terminal's line editing.
_MENU_INPUT=""
_read_menu_input() {
    local _input_row=${1:-$MENU_INPUT_ROW}
    _MENU_INPUT=""
    printf '\033[%d;3H\033[K' "$_input_row"
    printf "${WHITE}Choice: ${RST}"
    read -r _MENU_INPUT
}

_screen_int_handler() {
    if [ "$_ALLOW_INT_EXIT" = true ]; then
        printf '\033[r\033[?1049l\033[?25h'
        exit 0
    fi
    # Inside with_full_screen: SIGINT already killed the foreground child — just return
}

# Draw the logo + subtitle bar statically (full clear + redraw).
_draw_logo_static() {
    printf '\033[1;1H\033[J'
    printf '\n'
    printf '\033[2m%s\033[K\033[0m\n'       "$_LOGO_L0"
    printf '\033[38;5;220m%s\033[K\033[0m\n' "$_LOGO_L1"
    printf '\033[38;5;220m%s\033[K\033[0m\n' "$_LOGO_L2"
    printf '\033[38;5;214m%s\033[K\033[0m\n' "$_LOGO_L3"
    printf '\033[38;5;214m%s\033[K\033[0m\n' "$_LOGO_L4"
    printf '\033[38;5;208m%s\033[K\033[0m\n' "$_LOGO_L5"
    printf '\033[38;5;202m%s\033[K\033[0m\n' "$_LOGO_L6"
    printf '\033[38;5;196m%s\033[K\033[0m\n' "$_LOGO_L7"
    printf '\n'
    printf '\033[38;5;220m ══════════════════════════════════════════════════════════════════════════════════\033[K\033[0m\n'
    printf '   \033[2mWoW Module Manager\033[0m  ✦  \033[2mv%s\033[0m\033[K\n' "$MANAGER_VERSION"
    printf '\033[38;5;220m ══════════════════════════════════════════════════════════════════════════════════\033[K\033[0m\n'
    printf '\n'
}

# Enter alt screen buffer, set scroll region, draw static logo.
# Safe to call multiple times (idempotent for alt-screen entry).
_setup_screen() {
    if ! $_IN_ALT_SCREEN; then
        printf '\033[?1049h'
        _IN_ALT_SCREEN=true
    fi
    local tlines
    tlines=$(tput lines 2>/dev/null || echo 25)
    printf '\033[%d;%dr' "$MENU_START_ROW" "$tlines"
    printf '\033[?25l'
    _draw_logo_static
    printf '\033[?25h'
}

# Plays the animated intro splash (up to 3 seconds, any key skips),
# then freezes the logo statically for the rest of the session.
start_logo_animation() {
    _setup_screen
    trap 'printf "\033[r\033[?1049l\033[?25h"' EXIT
    trap '_screen_int_handler' INT TERM

    # Start animation subprocess
    _logo_anim_loop \
        "$_LOGO_L0" "$_LOGO_L1" "$_LOGO_L2" "$_LOGO_L3" \
        "$_LOGO_L4" "$_LOGO_L5" "$_LOGO_L6" "$_LOGO_L7" &
    ANIM_PID=$!

    printf '\033[%d;1H\033[K  \033[2mPress any key to skip...\033[0m' "$MENU_START_ROW"

    # Wait up to 3 seconds — any keypress skips immediately
    read -r -s -t 3 2>/dev/null || true

    # Freeze: stop subprocess, redraw logo statically
    stop_logo_animation
    printf '\033[?25l'
    _draw_logo_static
    printf '\033[?25h'
    printf '\033[%d;1H\033[J' "$MENU_START_ROW"
}

# Clear screen, run a function, restore the static logo + scroll region.
# _ALLOW_INT_EXIT=false lets Ctrl+C kill the child but return to the menu.
# Usage: with_full_screen <function_name> [args...]
with_full_screen() {
    printf '\033[r\033[H\033[2J\033[?25h'
    _ALLOW_INT_EXIT=false
    "$@"
    _ALLOW_INT_EXIT=true
    _setup_screen
    printf '\033[%d;1H\033[J' "$MENU_START_ROW"
}

print_header() {
    # Move to the menu content row and clear everything below.
    printf '\033[%d;1H\033[J' "$MENU_START_ROW"
}

print_step()    { echo ""; echo -e "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"
                   echo -e "${WHITE}${BOLD} $1${RST}"
                   echo -e "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"; }
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
    # Erase the prompt line so it doesn't linger when the menu redraws
    printf '\033[1A\033[2K'
}

# ─────────────────────────────────────────────────────────────
# CONFIG — populated by detect_install
# ─────────────────────────────────────────────────────────────
SERVER_DIR=""
SERVER_TYPE=""    # "base" | "npcbots" | "playerbots"
SERVER_NAME=""    # human-readable e.g. "Playerbots"
WORLD_CONTAINER=""
DB_CONTAINER=""
AUTH_CONTAINER=""
DB_ROOT_PASSWORD="password"   # acore-docker default

# Module registry: key|name|repo url|sql dirs (comma-sep)
declare -a MODULE_REGISTRY=(
    "mod-ah-bot|Auction House Bot|https://github.com/azerothcore/mod-ah-bot.git|world"
    "mod-solocraft|Solocraft (solo dungeon/raid scaling)|https://github.com/azerothcore/mod-solocraft.git|world"
    "mod-aoe-loot|AoE Loot|https://github.com/azerothcore/mod-aoe-loot.git|world"
    "mod-learn-spells|Learn Spells on Levelup|https://github.com/azerothcore/mod-learn-spells.git|world"
    "mod-individual-progression|Individual Progression (Vanilla → TBC → WotLK)|https://github.com/ZhengPeiRu21/mod-individual-progression.git|world,characters"
    "mod-autobalance|Auto Balance (dynamic difficulty)|https://github.com/azerothcore/mod-autobalance.git|world"
    "mod-transmog|Transmogrification|https://github.com/azerothcore/mod-transmog.git|world,characters"
    "mod-1v1-arena|1v1 Arena|https://github.com/azerothcore/mod-1v1-arena.git|characters"
    "mod-ale|AzerothCore Lua Engine (ALE)|https://github.com/azerothcore/mod-ale.git|"
)

# ─────────────────────────────────────────────────────────────
# INSTALL DETECTION
# ─────────────────────────────────────────────────────────────
# Find all WoW installs by looking for any wow-server* directory
# that contains a docker-compose.yml. Don't break on first match —
# enumerate all so the user can pick if there are multiple.
detect_install() {
    print_step "Detecting WoW installations"

    local -a found_dirs=()
    local d
    # Use a glob with nullglob behavior — handle "no matches" gracefully
    shopt -s nullglob
    for d in "$HOME"/wow-server*; do
        if [ -d "$d" ] && [ -f "$d/docker-compose.yml" ]; then
            found_dirs+=("$d")
        fi
    done
    shopt -u nullglob

    if [ "${#found_dirs[@]}" -eq 0 ]; then
        print_error "No WoW installation found!"
        print_info "Looked for any \$HOME/wow-server* directory with docker-compose.yml"
        echo ""
        print_info "Run install-wow.sh first."
        exit 1
    fi

    # ── One install: use it ───────────────────────────────
    if [ "${#found_dirs[@]}" -eq 1 ]; then
        SERVER_DIR="${found_dirs[0]}"
        print_success "Found one install: $SERVER_DIR"
    else
        # ── Multiple installs: let user pick ──────────────
        echo ""
        echo -e "${WHITE}Multiple WoW installs found:${RST}"
        echo ""
        local i=1
        for d in "${found_dirs[@]}"; do
            local typ
            typ=$(detect_type_for "$d")
            printf "  ${WHITE}%d) ${CYAN}%-40s${RST} ${DIM}(%s)${RST}\n" "$i" "$d" "$typ"
            i=$((i + 1))
        done
        echo ""
        while true; do
            printf "${WHITE}Choose [1-%d]: ${RST}" "${#found_dirs[@]}"
            read -r choice
            if [[ "$choice" =~ ^[0-9]+$ ]] && \
               [ "$choice" -ge 1 ] && \
               [ "$choice" -le "${#found_dirs[@]}" ]; then
                SERVER_DIR="${found_dirs[$((choice - 1))]}"
                break
            fi
            echo "  Please enter a number 1 to ${#found_dirs[@]}."
        done
    fi

    # Classify the install we picked
    SERVER_TYPE=$(detect_type_for "$SERVER_DIR")
    case "$SERVER_TYPE" in
        base)       SERVER_NAME="Base AzerothCore (WotLK)" ;;
        npcbots)    SERVER_NAME="NPCBots" ;;
        playerbots) SERVER_NAME="Playerbots" ;;
        *)          SERVER_NAME="Unknown" ;;
    esac

    print_success "Server: $SERVER_DIR"
    print_success "Type:   $SERVER_NAME"

    # Check docker is usable
    if ! docker ps &>/dev/null 2>&1; then
        if sudo docker ps &>/dev/null 2>&1; then
            docker() { sudo /usr/bin/docker "$@"; }
            export -f docker
            print_info "Using sudo for docker (no group membership active in this shell)"
        else
            print_error "Docker is not running."
            print_info "Try: sudo systemctl start docker"
            exit 1
        fi
    fi

    # Find running containers (will be empty if server is stopped — that's OK)
    refresh_container_names
}

# Classify an install by looking at directory name AND, if needed,
# at the compose file contents. The dir name is the cheapest signal.
detect_type_for() {
    local d="$1"
    case "$d" in
        *-playerbots)   echo "playerbots"; return ;;
        *-npcbots)      echo "npcbots"; return ;;
    esac
    # For dirs not named with a suffix, peek at the compose / override
    # for telltale strings.
    if [ -f "$d/docker-compose.override.yml" ] && \
       grep -qi "playerbot\|AC_AI_PLAYERBOT" "$d/docker-compose.override.yml" 2>/dev/null; then
        echo "playerbots"; return
    fi
    if [ -d "$d/modules/mod-playerbots" ]; then
        echo "playerbots"; return
    fi
    if [ -d "$d/data/sql/custom/db_world" ] && \
       ls "$d/data/sql/custom/db_world"/*npcbot* &>/dev/null; then
        echo "npcbots"; return
    fi
    echo "base"
}

# Find the actual running container names by docker label.
# Containers may not exist (server stopped) — that's not an error.
refresh_container_names() {
    WORLD_CONTAINER=$(docker ps -a --format '{{.Names}}' 2>/dev/null | \
        grep -iE "worldserver" | head -1)
    DB_CONTAINER=$(docker ps -a --format '{{.Names}}' 2>/dev/null | \
        grep -iE "ac-database|wow.*database" | head -1)
    AUTH_CONTAINER=$(docker ps -a --format '{{.Names}}' 2>/dev/null | \
        grep -iE "authserver" | head -1)
}

# Is a given container actually running (not just defined)?
container_running() {
    local name="$1"
    [ -z "$name" ] && return 1
    docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${name}$"
}

# ─────────────────────────────────────────────────────────────
# SERVER LIFECYCLE
# ─────────────────────────────────────────────────────────────
server_status() {
    print_step "Server Status"
    refresh_container_names

    local any_running=false
    local all=$(docker ps -a --format '{{.Names}}\t{{.Status}}' 2>/dev/null)

    # Filter to just THIS install's containers — use the project name (dir name)
    local project
    project=$(basename "$SERVER_DIR" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9-')

    echo ""
    echo -e "${WHITE}Containers for this install:${RST}"
    echo ""
    if [ -z "$all" ]; then
        echo "  (no containers found)"
    else
        # Show all WoW-related containers regardless of project, since users
        # may run multiple installs. Mark each with running/stopped status.
        local saw_one=false
        while IFS=$'\t' read -r name status; do
            [ -z "$name" ] && continue
            if echo "$name" | grep -qiE "worldserver|authserver|ac-database|ac-client|ac-eluna|ac-db-import|ac-tools"; then
                saw_one=true
                if echo "$status" | grep -qi "^up"; then
                    any_running=true
                    printf "  ${GREEN}●${RST} %-35s ${DIM}%s${RST}\n" "$name" "$status"
                else
                    printf "  ${DIM}○${RST} %-35s ${DIM}%s${RST}\n" "$name" "$status"
                fi
            fi
        done <<< "$all"
        [ "$saw_one" = false ] && echo "  (no WoW containers found)"
    fi

    echo ""
    if [ "$any_running" = "true" ]; then
        print_success "Server is RUNNING"
        if [ -n "$WORLD_CONTAINER" ] && container_running "$WORLD_CONTAINER"; then
            print_info "Worldserver: $WORLD_CONTAINER"
            # Show last few lines of worldserver log
            echo ""
            echo -e "${WHITE}Recent worldserver activity:${RST}"
            docker logs --tail 5 "$WORLD_CONTAINER" 2>&1 | sed 's/^/  /'
        fi
    else
        print_warning "Server is STOPPED"
    fi
}

server_start() {
    print_step "Starting Server"
    cd "$SERVER_DIR" || { print_error "Can't cd to $SERVER_DIR"; return 1; }

    # ── Playerbots-specific: fix UID/GID mismatch on env/dist ─────────
    # Per azerothcore/azerothcore-wotlk#17656: AzerothCore containers
    # are hardcoded to run as acore:1000:1000. The volume-mounted
    # paths env/dist/etc and env/dist/logs MUST be owned by 1000:1000
    # or ac-db-import fails with "Permission denied" → exit 1.
    #
    # Important: these directories may not exist before the first
    # build. We create them with correct ownership up-front so the
    # volume mounts pick up the right perms from the very first run.
    if [ "$SERVER_TYPE" = "playerbots" ] || [ "$SERVER_TYPE" = "npcbots" ]; then
        local fix_dirs=("env/dist/etc" "env/dist/logs")
        local need_action=false
        local d
        for d in "${fix_dirs[@]}"; do
            if [ ! -d "$d" ]; then
                need_action=true
                break
            fi
            local owner
            owner=$(stat -c '%u:%g' "$d" 2>/dev/null)
            if [ "$owner" != "1000:1000" ]; then
                need_action=true
                break
            fi
        done

        if [ "$need_action" = "true" ]; then
            print_info "Ensuring env/dist ownership is 1000:1000 (AzerothCore requirement)..."
            # Create dirs first — they may not exist on a brand-new install
            sudo mkdir -p env/dist/etc env/dist/logs
            # chown errors are SHOWN, not silenced, so user knows if sudo failed
            if sudo chown -R 1000:1000 env/dist/etc env/dist/logs; then
                print_success "Ownership fixed (env/dist/etc, env/dist/logs → 1000:1000)"
            else
                print_warning "chown failed — server may fail with ac-db-import error"
                print_info "If prompted for sudo password and you didn't provide it,"
                print_info "run manually: sudo chown -R 1000:1000 env/dist/etc env/dist/logs"
            fi
        fi
    fi

    # ── Detect whether phpmyadmin service exists before scaling ───────
    # Base/NPCBots installs ship docker-compose.override.yml with phpmyadmin.
    # Playerbots does NOT — so --scale phpmyadmin=0 errors out with
    # "no such service: phpmyadmin: not found". Detect first and pick
    # the right command.
    local has_phpmyadmin=false
    if docker compose config --services 2>/dev/null | grep -qx "phpmyadmin"; then
        has_phpmyadmin=true
    fi

    print_info "Bringing up containers..."
    local up_log="/tmp/wow-server-start.log"
    local up_rc
    if [ "$has_phpmyadmin" = "true" ]; then
        docker compose up -d --scale phpmyadmin=0 > "$up_log" 2>&1
        up_rc=$?
    else
        docker compose up -d > "$up_log" 2>&1
        up_rc=$?
    fi

    if [ "$up_rc" -ne 0 ]; then
        print_error "Failed to start server (exit code: $up_rc)"
        echo ""
        print_info "Last 20 lines of /tmp/wow-server-start.log:"
        tail -20 "$up_log" 2>/dev/null | sed 's/^/    /'
        echo ""
        # Diagnose the most common failure modes
        if grep -q "didn't complete successfully" "$up_log" 2>/dev/null && \
           grep -q "ac-db-import" "$up_log" 2>/dev/null; then
            print_warning "DIAGNOSIS: ac-db-import failed."
            print_info "Check the real error with:"
            print_info "  docker compose logs ac-db-import | tail -50"
            print_info ""
            print_info "Most common causes and fixes:"
            print_info ""
            print_info "  • ${CYAN}'Table X already exists' errors${RST}: a previous module install"
            print_info "    corrupted update tracking. Use menu option 12 (Repair install state)."
            print_info ""
            print_info "  • ${CYAN}'Permission denied' errors${RST}: UID/GID mismatch on env/dist."
            print_info "    Run: sudo chown -R 1000:1000 env/dist/etc env/dist/logs"
            print_info ""
            print_info "  • ${CYAN}'No such file or directory' on dbimport binary${RST}: build problem."
            print_info "    Try: docker compose build --no-cache ac-db-import"
        elif grep -qi "address already in use\|port is already allocated" "$up_log" 2>/dev/null; then
            print_warning "DIAGNOSIS: A port is already in use."
            print_info "Check what's using the conflicting port:"
            print_info "  sudo ss -tlnp | grep -E '3306|3724|8085'"
        elif grep -qi "no space left on device" "$up_log" 2>/dev/null; then
            print_warning "DIAGNOSIS: Disk full."
        else
            print_info "Full logs: docker compose logs"
        fi
        return 1
    fi

    print_success "Containers started"

    print_info "Waiting for worldserver to be ready..."
    refresh_container_names
    if [ -z "$WORLD_CONTAINER" ]; then
        print_warning "Couldn't identify worldserver container — server may still be starting"
        return 0
    fi

    # Poll worldserver logs for ready signal (up to 90s)
    local i
    for i in $(seq 1 18); do
        if docker logs "$WORLD_CONTAINER" 2>&1 | \
           grep -qiE "World initialized|Loading World|Loading complete"; then
            print_success "Worldserver is ready! ⚔️"
            return 0
        fi
        sleep 5
    done
    print_warning "Worldserver didn't signal ready within 90s — may still be loading"
    print_info "Use 'View logs' to check progress."
}

server_stop() {
    print_step "Stopping Server"
    cd "$SERVER_DIR" || { print_error "Can't cd to $SERVER_DIR"; return 1; }

    print_info "Stopping containers (graceful shutdown)..."
    if docker compose down; then
        print_success "Server stopped"
    else
        print_warning "docker compose down had non-zero exit — checking state..."
        if ! docker ps --format '{{.Names}}' | grep -qE "worldserver|authserver"; then
            print_success "Containers are gone — stop was effective"
        else
            print_error "Some containers may still be running"
        fi
    fi
}

server_restart() {
    server_stop
    echo ""
    sleep 3
    server_start
}

server_logs() {
    print_step "Server Logs"
    refresh_container_names

    if [ -z "$WORLD_CONTAINER" ]; then
        print_error "Worldserver container not found"
        print_info "Start the server first."
        return 1
    fi
    if ! container_running "$WORLD_CONTAINER"; then
        print_warning "Worldserver isn't running. Showing last lines from when it last ran:"
        echo ""
        docker logs --tail 50 "$WORLD_CONTAINER" 2>&1 | sed 's/^/  /'
        return 0
    fi

    echo ""
    print_info "Following worldserver log (Ctrl+C to exit)..."
    print_info "This won't stop the server — only stops following the log."
    echo ""
    sleep 2
    docker logs -f --tail 30 "$WORLD_CONTAINER"
}

server_attach() {
    print_step "Attach to Worldserver Console"
    refresh_container_names

    if ! container_running "$WORLD_CONTAINER"; then
        print_error "Worldserver isn't running."
        print_info "Start the server first."
        return 1
    fi

    echo ""
    echo -e "${YELLOW}⚠️  You're about to attach to the worldserver console.${RST}"
    echo ""
    echo -e "${WHITE}Use this to run server commands like:${RST}"
    echo -e "  ${CYAN}account create USERNAME PASSWORD${RST}"
    echo -e "  ${CYAN}account set gmlevel USERNAME 3 -1${RST}"
    echo ""
    echo -e "${RED}${BOLD}CRITICAL — How to detach safely:${RST}"
    echo -e "${WHITE}  Press ${BOLD}Ctrl+P then Ctrl+Q${RST}${WHITE} (in sequence)${RST}"
    echo -e "${WHITE}  This detaches without stopping the server.${RST}"
    echo ""
    echo -e "${RED}${BOLD}DO NOT press Ctrl+C — that STOPS the server!${RST}"
    echo ""
    ask_yes_no "Ready to attach?" || return 0

    docker attach "$WORLD_CONTAINER"
    echo ""
    print_info "Detached from worldserver console."
}

# ─────────────────────────────────────────────────────────────
# REPAIR INSTALL STATE
# ─────────────────────────────────────────────────────────────
# AzerothCore's auto-update system tracks applied SQL files in
# an `updates` table per database. When that tracking gets out
# of sync with actual schema state, ac-db-import fails with
# errors like "Table X already exists" — AC sees the SQL needs
# applying (no `updates` row) but the table already exists, so
# the CREATE TABLE blows up.
#
# DESIGN PHILOSOPHY: This function NEVER drops tables. It only
# clears rows from the `updates` tracking table. AzerothCore's
# auto-update on next start will then re-detect the SQL files
# as needing application and run them. Module SQL uses
# CREATE TABLE IF NOT EXISTS / INSERT IGNORE semantics, so
# re-application is safe whether the table exists or not.
#
# Why this matters: an earlier version of this function dropped
# tables based on a hand-coded module-to-table map. That conflated
# "tables a module reads from" with "tables a module owns" — and
# dropped `character_arena_stats` (a base AzerothCore schema table
# that mod-1v1-arena merely READS from). That broke worldserver's
# prepared-statement initialization and required manually restoring
# the table from the base SQL file. This version cannot have that
# class of bug because it doesn't touch tables at all.

# Per-module SQL filename registry. These are the EXACT strings as
# they appear in `updates.name` column. To find these for a new
# module: ls modules/<mod>/data/sql/db-<dbname>/
# Format: "module-key|database|filename1.sql filename2.sql ..."
declare -a MODULE_UPDATE_FILES=(
    "mod-ah-bot|acore_world|auctionhousebot_professionItems.sql mod_auctionhousebot.sql"
    "mod-transmog|acore_characters|trasmorg.sql"
    "mod-1v1-arena|acore_characters|"
    "mod-solocraft|acore_world|"
    "mod-aoe-loot|acore_world|"
    "mod-learn-spells|acore_world|"
    "mod-individual-progression|acore_world|"
    "mod-autobalance|acore_world|"
    "mod-ale|acore_world|"
)

# ALE Lua Script registry.
# These are Lua scripts that run on the ALE engine — NOT compiled C++ modules.
# Clones stored in $SERVER_DIR/ale_scripts/<key>/
# Deployed to  $SERVER_DIR/env/dist/etc/modules/lua_scripts/
# Format: "key|display name|git url"
# Special install steps (SQL, client addons, config) are handled per-key
# inside ale_script_install() and the configure_ale_* functions.
declare -a ALE_SCRIPT_REGISTRY=(
    "accountwide|Accountwide Systems (achievements, currency, mounts, pets)|https://github.com/Aldori15/azerothcore-eluna-accountwide.git"
    "levelupreward|Level Up Reward (mail gold/items on level-up)|https://github.com/55Honey/Acore_LevelUpReward.git"
    "exchangenpc|Exchange NPC (configurable item-exchange vendor NPC)|https://github.com/55Honey/Acore_ExchangeNpc.git"
    "activechat|Active Chat (simulated world/guild chat for immersion)|https://github.com/Day36512/ActiveChat.git"
    "battlepass|Battle Pass System (XP progression + rewards + client addon)|https://github.com/Shonik/lua-battlepass.git"
    "paragon|Paragon Anniversary (endless post-80 stat progression + client addon)|https://github.com/Grim-Batol/Paragon-Anniversary.git"
    "bmah|Black Market Auction House (MoP-style BMAH + client addon)|https://github.com/Youpeoples/Black-Market-Auction-House.git"
    "lootpet|Loot Pet (vanity pet auto-loots nearby corpses)|https://github.com/Brytenwally/Lootpet.git"
    "sod|Season of Discovery Buffs (phased leveling XP rate bonus)|https://github.com/notepadguyOfficial/acore_sod.git"
)

# Discover the actual SQL filenames in a module's sql dir.
# This is what AC's auto-update will use as the `updates.name` value.
# Returns space-separated filenames, or empty if dir doesn't exist.
discover_module_sql_files() {
    local key="$1" db_short="$2"  # db_short is "world", "characters", etc.
    local sql_dir="$SERVER_DIR/modules/$key/data/sql/db-${db_short}"
    [ ! -d "$sql_dir" ] && sql_dir="$SERVER_DIR/modules/$key/sql/${db_short}"
    [ ! -d "$sql_dir" ] && return 0
    # Find .sql files at top level only (subdirs are usually versioned variants)
    (cd "$sql_dir" && ls *.sql 2>/dev/null | tr '\n' ' ')
}

# Run a DELETE on the updates table for a given database and SQL file name.
# Returns the number of rows affected (0 if nothing matched, useful diagnostic).
clear_update_tracking_row() {
    local db_full="$1" sql_filename="$2"
    # Count rows first so we can report success accurately
    local rows
    rows=$(docker exec "$DB_CONTAINER" mysql -uroot -p"$DB_ROOT_PASSWORD" -N \
        "$db_full" \
        -e "SELECT COUNT(*) FROM updates WHERE name = '$sql_filename';" \
        2>/dev/null | tr -d '[:space:]')
    if [ -z "$rows" ] || [ "$rows" = "0" ]; then
        return 1  # Nothing to clear
    fi
    docker exec "$DB_CONTAINER" mysql -uroot -p"$DB_ROOT_PASSWORD" \
        "$db_full" \
        -e "DELETE FROM updates WHERE name = '$sql_filename';" 2>/dev/null
    return 0
}

# Show what's currently tracked in the updates table for a given module.
# Useful for diagnosis — users can SEE what AC thinks has been applied.
show_module_tracking() {
    local key="$1"
    echo ""
    echo -e "${WHITE}Currently tracked updates that mention '${key}' or related terms:${RST}"
    local stripped="${key#mod-}"  # mod-ah-bot → ah-bot
    local term1="${stripped//-/_}"  # ah-bot → ah_bot (covers underscored names)
    local rows_world rows_chars rows_auth
    rows_world=$(docker exec "$DB_CONTAINER" mysql -uroot -p"$DB_ROOT_PASSWORD" -N \
        acore_world \
        -e "SELECT name FROM updates WHERE name LIKE '%${stripped}%' \
            OR name LIKE '%${term1}%';" 2>/dev/null)
    rows_chars=$(docker exec "$DB_CONTAINER" mysql -uroot -p"$DB_ROOT_PASSWORD" -N \
        acore_characters \
        -e "SELECT name FROM updates WHERE name LIKE '%${stripped}%' \
            OR name LIKE '%${term1}%';" 2>/dev/null)
    rows_auth=$(docker exec "$DB_CONTAINER" mysql -uroot -p"$DB_ROOT_PASSWORD" -N \
        acore_auth \
        -e "SELECT name FROM updates WHERE name LIKE '%${stripped}%' \
            OR name LIKE '%${term1}%';" 2>/dev/null)
    if [ -n "$rows_world" ]; then
        echo -e "  ${DIM}acore_world:${RST}"
        echo "$rows_world" | sed 's/^/    /'
    fi
    if [ -n "$rows_chars" ]; then
        echo -e "  ${DIM}acore_characters:${RST}"
        echo "$rows_chars" | sed 's/^/    /'
    fi
    if [ -n "$rows_auth" ]; then
        echo -e "  ${DIM}acore_auth:${RST}"
        echo "$rows_auth" | sed 's/^/    /'
    fi
    if [ -z "$rows_world$rows_chars$rows_auth" ]; then
        echo -e "  ${DIM}(no matching rows in any database)${RST}"
    fi
}

# Repair flow for a single module — clear its tracking rows.
# Tries the known filename list first, then offers auto-discovery from
# the module's SQL directory, then offers manual filename entry.
repair_module() {
    local key="$1" db_full="$2" known_files="$3"

    print_step "Repairing: $key"
    show_module_tracking "$key"
    echo ""

    # Determine which SQL filenames to clear
    local files_to_clear=""

    # 1. Try the known list first
    if [ -n "$known_files" ]; then
        echo -e "${WHITE}Known SQL files to clear from ${db_full}.updates:${RST}"
        local f
        for f in $known_files; do
            echo -e "  ${CYAN}$f${RST}"
        done
        echo ""
        if ask_yes_no "Clear tracking rows for these files?"; then
            files_to_clear="$known_files"
        fi
    fi

    # 2. If no known list or user declined, offer auto-discovery
    if [ -z "$files_to_clear" ]; then
        # Map db_full back to db_short for the sql dir
        local db_short="${db_full#acore_}"
        local discovered
        discovered=$(discover_module_sql_files "$key" "$db_short")
        if [ -n "$discovered" ]; then
            echo -e "${WHITE}Auto-discovered SQL files in module's sql dir:${RST}"
            local f
            for f in $discovered; do
                echo -e "  ${CYAN}$f${RST}"
            done
            echo ""
            if ask_yes_no "Clear tracking rows for these auto-discovered files?"; then
                files_to_clear="$discovered"
            fi
        fi
    fi

    # 3. Final fallback: manual entry
    if [ -z "$files_to_clear" ]; then
        echo ""
        echo -e "${WHITE}Enter SQL filenames manually (space-separated)${RST}"
        echo -e "${DIM}Example: foo.sql bar.sql${RST}"
        echo -e "${DIM}Or just press ENTER to skip this module.${RST}"
        printf "${WHITE}Files: ${RST}"
        read -r files_to_clear
        [ -z "$files_to_clear" ] && { print_info "Skipped."; return 0; }
    fi

    # Apply the clears, report per-file
    echo ""
    local cleared=0 missing=0
    local f
    for f in $files_to_clear; do
        if clear_update_tracking_row "$db_full" "$f"; then
            echo -e "  ${GREEN}✓${RST} Cleared: $f"
            cleared=$((cleared + 1))
        else
            echo -e "  ${DIM}○${RST} Not found in updates: $f"
            missing=$((missing + 1))
        fi
    done
    echo ""
    if [ "$cleared" -gt 0 ]; then
        print_success "Cleared $cleared tracking row(s) for $key"
        print_info "AzerothCore will re-apply this SQL on next server start."
    fi
    if [ "$cleared" -eq 0 ] && [ "$missing" -gt 0 ]; then
        print_info "All filenames searched were already absent from updates table."
        print_info "This could mean:"
        print_info "  • The filenames don't exactly match what AC tracked"
        print_info "  • The module's SQL was never applied (fresh install case)"
        print_info "  • The repair was already run successfully before"
    fi
}

repair_install_state() {
    print_step "Repair Install State"

    echo ""
    echo -e "${WHITE}Use this when ac-db-import fails with errors like:${RST}"
    echo -e "${WHITE}  • ${CYAN}ERROR 1050: Table 'X' already exists${RST}"
    echo -e "${WHITE}  • ${CYAN}ac-db-import: didn't complete successfully: exit 1${RST}"
    echo ""
    echo -e "${WHITE}${BOLD}How this works:${RST}"
    echo -e "${WHITE}  This clears rows from AzerothCore's ${CYAN}updates${WHITE} tracking table${RST}"
    echo -e "${WHITE}  for selected modules. On next server start, AC will detect${RST}"
    echo -e "${WHITE}  the SQL files as needing application and run them. Module SQL${RST}"
    echo -e "${WHITE}  uses IF NOT EXISTS semantics, so re-apply is safe even when${RST}"
    echo -e "${WHITE}  the tables already exist.${RST}"
    echo ""
    echo -e "${GREEN}This function does NOT drop tables — it's safe and non-destructive.${RST}"
    echo ""

    # Need DB running
    refresh_container_names
    if ! container_running "$DB_CONTAINER"; then
        print_info "Starting database container..."
        (cd "$SERVER_DIR" && docker compose up -d ac-database 2>/dev/null) || true
        refresh_container_names
        local i
        for i in $(seq 1 15); do
            if docker exec "$DB_CONTAINER" mysqladmin ping \
                -uroot -p"$DB_ROOT_PASSWORD" &>/dev/null 2>&1; then
                break
            fi
            sleep 2
        done
        if ! container_running "$DB_CONTAINER"; then
            print_error "Couldn't start database — can't repair"
            return 1
        fi
    fi

    # Build menu of installed modules from the registry
    local -a repair_keys=()
    local -a repair_dbs=()
    local -a repair_files=()
    local entry key db files
    for entry in "${MODULE_UPDATE_FILES[@]}"; do
        IFS='|' read -r key db files <<< "$entry"
        if module_is_installed "$key"; then
            repair_keys+=("$key")
            repair_dbs+=("$db")
            repair_files+=("$files")
        fi
    done

    # Also include any modules in the modules dir that we DON'T have
    # in the registry — let user repair them via manual filename entry
    local d dn in_registry
    for d in "$SERVER_DIR/modules"/*/; do
        [ -d "$d" ] || continue
        dn=$(basename "$d")
        # Skip the bundled-with-source mod-playerbots — it's special
        [ "$dn" = "mod-playerbots" ] && continue
        in_registry=false
        for entry in "${MODULE_UPDATE_FILES[@]}"; do
            IFS='|' read -r key _ _ <<< "$entry"
            if [ "$key" = "$dn" ]; then
                in_registry=true
                break
            fi
        done
        if [ "$in_registry" = false ]; then
            repair_keys+=("$dn")
            repair_dbs+=("")  # Unknown DB — manual entry will handle
            repair_files+=("")  # Unknown files — manual or auto-discover
        fi
    done

    if [ "${#repair_keys[@]}" -eq 0 ]; then
        print_info "No modules installed — nothing to repair."
        return 0
    fi

    # Show menu
    echo -e "${WHITE}Installed modules:${RST}"
    echo ""
    local i=1
    for ((i=0; i<${#repair_keys[@]}; i++)); do
        local marker=""
        if [ -z "${repair_files[$i]}" ]; then
            marker=" ${DIM}(manual filename entry needed)${RST}"
        fi
        printf "  %2d) %s%b\n" "$((i + 1))" "${repair_keys[$i]}" "$marker"
    done
    echo ""
    echo -e "${WHITE}  A) Repair ALL listed modules${RST}"
    echo -e "${WHITE}  S) Show update-tracking state for all modules (diagnostic only)${RST}"
    echo -e "${WHITE}  ENTER to cancel${RST}"
    echo ""
    printf "${WHITE}Choice: ${RST}"
    read -r choice

    case "${choice,,}" in
        "")
            return 0
            ;;
        a)
            for ((i=0; i<${#repair_keys[@]}; i++)); do
                local db="${repair_dbs[$i]}"
                # If we don't know the DB for an unregistered module, try
                # acore_world as a default — most module SQL lives there
                [ -z "$db" ] && db="acore_world"
                repair_module "${repair_keys[$i]}" "$db" "${repair_files[$i]}"
            done
            ;;
        s)
            for ((i=0; i<${#repair_keys[@]}; i++)); do
                show_module_tracking "${repair_keys[$i]}"
            done
            ;;
        *)
            if [[ "$choice" =~ ^[0-9]+$ ]] && \
               [ "$choice" -ge 1 ] && \
               [ "$choice" -le "${#repair_keys[@]}" ]; then
                local idx=$((choice - 1))
                local db="${repair_dbs[$idx]}"
                [ -z "$db" ] && db="acore_world"
                repair_module "${repair_keys[$idx]}" "$db" "${repair_files[$idx]}"
            else
                print_warning "Invalid choice."
            fi
            ;;
    esac

    echo ""
    print_info "Done. Start the server (menu option 7) for AC to re-apply cleared SQL."
}

# ─────────────────────────────────────────────────────────────
# MODULE OPERATIONS
# ─────────────────────────────────────────────────────────────
module_is_installed() {
    local key="$1"
    [ -d "$SERVER_DIR/modules/$key" ]
}

# Source-build state — is the worldserver service set up to build from source?
# For Playerbots, this is ALWAYS true (install-wow sets it up that way).
# For Base/NPCBots, this is false by default (uses prebuilt image).
worldserver_is_source_build() {
    if [ "$SERVER_TYPE" = "playerbots" ]; then
        return 0
    fi
    local override="$SERVER_DIR/docker-compose.override.yml"
    [ -f "$override" ] && \
        grep -qE "^\s*build:" "$override" && \
        grep -qE "ac-worldserver:" "$override"
}

# Clone a module into the install's modules/ directory.
# Module SQL is NOT applied manually — AzerothCore's auto-update system
# (via ac-db-import on next server start) handles SQL automatically and
# tracks which files have been applied in the 'updates' table.
#
# Important: manually applying SQL via `docker exec mysql < file.sql` BREAKS
# AzerothCore's update tracking. The table exists but the update isn't
# recorded, so on next start AC tries to apply the SQL again, hits the
# existing table, and aborts the entire db-import step.
# (Confirmed in real-world testing: a previous version of this manager did
# this and caused the "ac-db-import: didn't complete successfully" error
# with "Table 'auctionhousebot_professionItems' already exists".)
module_install() {
    local key="$1" name="$2" url="$3" sql_dirs="$4"

    print_step "Installing: $name"

    if module_is_installed "$key"; then
        print_info "$name is already cloned — pulling latest"
        (cd "$SERVER_DIR/modules/$key" && git pull --depth 1 2>/dev/null) || \
            print_warning "git pull failed — using existing copy"
    else
        mkdir -p "$SERVER_DIR/modules"
        if ! git clone --depth 1 "$url" "$SERVER_DIR/modules/$key"; then
            print_error "Clone failed for $name!"
            return 1
        fi
        print_success "Cloned $name"
    fi

    # SQL is applied automatically on next worldserver start. No manual import.
    if [ -n "$sql_dirs" ]; then
        print_info "Module SQL will be auto-applied on next server start"
        print_info "(AzerothCore's update system handles this — no manual import needed.)"
    fi
    return 0
}

module_remove() {
    local key="$1" name="$2"

    print_step "Removing: $name"

    if ! module_is_installed "$key"; then
        print_info "$name was not installed — nothing to do"
        return 0
    fi

    if ask_yes_no "  Remove module files from $SERVER_DIR/modules/$key?"; then
        rm -rf "$SERVER_DIR/modules/$key"
        print_success "Module files removed"
        print_info "(Database tables/rows from this module are kept — removing"
        print_info " them risks data loss and they're harmless to leave.)"
    fi
}

# ─────────────────────────────────────────────────────────────
# REBUILD
# ─────────────────────────────────────────────────────────────
rebuild_worldserver() {
    print_step "Rebuilding worldserver"
    cd "$SERVER_DIR" || { print_error "Can't cd to $SERVER_DIR"; return 1; }

    case "$SERVER_TYPE" in
        playerbots)
            # Playerbots is ALREADY source-build — install-wow set it up
            # that way with the mod-playerbots fork. Rebuilding just means
            # `docker compose up -d --build` to pick up new modules.
            echo ""
            echo -e "${WHITE}Playerbots is already configured for source build.${RST}"
            echo -e "${WHITE}Rebuilding will recompile worldserver with any new modules.${RST}"
            echo ""
            echo -e "${YELLOW}⚠️  Expected time: 30-90 minutes on a Steam Deck.${RST}"
            echo -e "${YELLOW}   Keep the Deck plugged in and on a flat surface.${RST}"
            echo ""
            if ! ask_yes_no "Start the rebuild now?"; then
                print_info "Skipped."
                return 0
            fi

            print_info "Stopping worldserver before rebuild..."
            docker compose stop ac-worldserver 2>/dev/null || true

            print_info "Building... (output below — full log: /tmp/wow-modules-build.log)"
            echo ""
            if docker compose up -d --build 2>&1 | \
                tee /tmp/wow-modules-build.log | \
                grep -E "Step|Building|Compiling|Linking|Successfully|ERROR|error:|Created"; then
                print_success "Rebuild complete!"
            else
                print_warning "Build had non-zero exit — check /tmp/wow-modules-build.log"
                return 1
            fi
            ;;

        base|npcbots)
            # Base/NPCBots use prebuilt images by default. To add modules
            # we'd need to switch to source-build, which means cloning
            # azerothcore-wotlk (NOT acore-docker, which has no Dockerfile)
            # and reworking the compose. This is genuinely hard to do
            # cleanly without breaking the existing install.
            echo ""
            print_warning "Rebuild is not supported for $SERVER_NAME installs."
            echo ""
            echo -e "${WHITE}Why: $SERVER_NAME uses prebuilt Docker images from azerothcore-docker.${RST}"
            echo -e "${WHITE}To add modules, the worldserver must be compiled from source —${RST}"
            echo -e "${WHITE}but the prebuilt-image setup doesn't include the source or Dockerfile.${RST}"
            echo ""
            echo -e "${WHITE}${BOLD}Recommended path:${RST}"
            echo -e "${WHITE}  1. Install Playerbots variant instead (re-run install-wow.sh,${RST}"
            echo -e "${WHITE}     pick option 3 — Playerbots).${RST}"
            echo -e "${WHITE}  2. Playerbots is already source-build, so modules work immediately.${RST}"
            echo -e "${WHITE}  3. The module manager will fully support it.${RST}"
            echo ""
            echo -e "${DIM}If you really want to attempt rebuild on $SERVER_NAME, it would${RST}"
            echo -e "${DIM}require manually swapping the compose file to use azerothcore-wotlk${RST}"
            echo -e "${DIM}source with target: worldserver-local. Out of scope for this tool.${RST}"
            return 1
            ;;
    esac
}

# ─────────────────────────────────────────────────────────────
# AH BOT CONFIGURATION
# ─────────────────────────────────────────────────────────────
list_characters() {
    refresh_container_names
    if ! container_running "$DB_CONTAINER"; then
        return 1
    fi
    docker exec "$DB_CONTAINER" mysql -uroot -p"$DB_ROOT_PASSWORD" \
        -e "SELECT guid, name, account FROM acore_characters.characters \
            ORDER BY guid;" 2>/dev/null | tail -n +2
}

configure_ahbot() {
    print_step "Configuring Auction House Bot"

    if ! module_is_installed "mod-ah-bot"; then
        print_error "mod-ah-bot is not installed yet!"
        print_info "Add it first via the main menu (Add modules)."
        return 1
    fi

    echo ""
    echo -e "${WHITE}The Auction House Bot needs a player account and character${RST}"
    echo -e "${WHITE}to act as. The bot uses this character to list items.${RST}"
    echo ""
    echo -e "${WHITE}${BOLD}Required steps:${RST}"
    echo -e "${WHITE}  1. From the main menu, attach to worldserver console${RST}"
    echo -e "${WHITE}  2. Run: ${CYAN}account create AHBOT YourPasswordHere${RST}"
    echo -e "${WHITE}  3. Detach: ${BOLD}Ctrl+P Ctrl+Q${RST}"
    echo -e "${WHITE}  4. Log in with WoW client using that account${RST}"
    echo -e "${WHITE}  5. Create ONE character (race/class/faction don't matter)${RST}"
    echo -e "${WHITE}  6. Log out of WoW completely${RST}"
    echo -e "${WHITE}  7. Come back here${RST}"
    echo ""
    echo -e "${YELLOW}⚠️  The bot character should NOT be used for play.${RST}"
    echo -e "${YELLOW}   It will be busy listing items 24/7.${RST}"
    echo ""

    if ! ask_yes_no "Have you completed steps 1-6 above?"; then
        print_info "OK — run me again when ready."
        return 0
    fi

    echo ""
    print_info "Characters found in your database:"
    echo ""
    local chars
    chars=$(list_characters)
    if [ -z "$chars" ]; then
        print_error "No characters found in the database!"
        print_info "Did you log in with the WoW client and create one?"
        print_info "(The database must be running too — check Server Status.)"
        return 1
    fi
    printf "  %-6s | %-20s | %-10s\n" "GUID" "Name" "Account ID"
    echo "  -------|----------------------|----------"
    echo "$chars" | while IFS=$'\t' read -r guid name account; do
        printf "  %-6s | %-20s | %-10s\n" "$guid" "$name" "$account"
    done
    echo ""

    printf "${WHITE}Enter the GUID of the bot character: ${RST}"
    read -r bot_guid
    if ! [[ "$bot_guid" =~ ^[0-9]+$ ]]; then
        print_error "Not a number — aborting."
        return 1
    fi

    local bot_info
    bot_info=$(echo "$chars" | awk -v g="$bot_guid" -F'\t' '$1 == g')
    if [ -z "$bot_info" ]; then
        print_error "GUID $bot_guid not found in the character list."
        return 1
    fi
    local bot_account=$(echo "$bot_info" | cut -f3)
    local bot_name=$(echo "$bot_info" | cut -f2)
    print_success "Selected: $bot_name (GUID $bot_guid, account $bot_account)"

    local conf_dist="$SERVER_DIR/modules/mod-ah-bot/conf/mod_ahbot.conf.dist"
    if [ ! -f "$conf_dist" ]; then
        print_error "Couldn't find $conf_dist"
        return 1
    fi

    mkdir -p "$SERVER_DIR/conf/modules"
    local conf_active="$SERVER_DIR/conf/modules/mod_ahbot.conf"
    cp "$conf_dist" "$conf_active"

    sed -i \
        -e "s|^AuctionHouseBot.Account *=.*|AuctionHouseBot.Account = ${bot_account}|" \
        -e "s|^AuctionHouseBot.GUID *=.*|AuctionHouseBot.GUID = ${bot_guid}|" \
        -e "s|^AuctionHouseBot.GUIDs *=.*|AuctionHouseBot.GUIDs = \"${bot_guid}\"|" \
        -e "s|^AuctionHouseBot.EnableSeller *=.*|AuctionHouseBot.EnableSeller = 1|" \
        -e "s|^AuctionHouseBot.EnableBuyer *=.*|AuctionHouseBot.EnableBuyer = 1|" \
        -e "s|^AHBot.enabled *=.*|AHBot.enabled = 1|" \
        "$conf_active"

    print_success "Wrote $conf_active"

    refresh_container_names
    if container_running "$WORLD_CONTAINER"; then
        docker cp "$conf_active" \
            "${WORLD_CONTAINER}:/azerothcore/env/dist/etc/modules/mod_ahbot.conf" \
            2>/dev/null || true
        print_info "Conf pushed to running worldserver"
        print_info "Restart worldserver from the main menu (Restart Server) to activate."
    fi

    echo ""
    print_info "AH Bot will start populating auctions on next worldserver start."
    print_info "It adds ~75 items per cycle — full population takes hours."
}

# ─────────────────────────────────────────────────────────────
# ALE CONFIGURATION
# ─────────────────────────────────────────────────────────────
# Post-install setup for mod-ale (AzerothCore Lua Engine):
#   1. Creates the lua_scripts directory in env/dist/etc/modules/
#   2. Copies mod_ale.conf.dist → mod_ale.conf (skip if already exists)
#   3. Patches ALE.ScriptPath to the container-visible absolute path
#
# env/dist/etc/ is volume-mounted to /azerothcore/env/dist/etc/ inside
# the container, so writing here is equivalent to writing inside the
# container — no docker cp needed.
configure_ale() {
    print_step "Configuring AzerothCore Lua Engine (ALE)"

    if ! module_is_installed "mod-ale"; then
        print_error "mod-ale is not installed yet!"
        print_info "Add it first via the main menu (Add modules)."
        return 1
    fi

    # ── Create the lua_scripts directory ─────────────────────
    local lua_scripts_dir="$SERVER_DIR/env/dist/etc/modules/lua_scripts"
    print_info "Creating lua_scripts directory..."
    if mkdir -p "$lua_scripts_dir"; then
        print_success "Created: $lua_scripts_dir"
    else
        print_error "Failed to create lua_scripts directory."
        return 1
    fi

    # ── Copy dist conf if no active conf exists yet ───────────
    local conf_dist="$SERVER_DIR/modules/mod-ale/conf/mod_ale.conf.dist"
    local conf_active="$SERVER_DIR/env/dist/etc/modules/mod_ale.conf"

    if [ ! -f "$conf_dist" ]; then
        print_error "Couldn't find $conf_dist"
        print_info "The module may not have cloned correctly."
        return 1
    fi

    mkdir -p "$SERVER_DIR/env/dist/etc/modules"
    if [ -f "$conf_active" ]; then
        print_info "Active conf already exists — keeping it, updating ScriptPath only."
    else
        cp "$conf_dist" "$conf_active"
        print_success "Copied conf to: $conf_active"
    fi

    # ── Patch ALE.ScriptPath to the container-visible path ───
    # Always applied so the path is correct whether this is a fresh copy
    # or an existing file (e.g. after a server directory move).
    sed -i \
        's|^[[:space:]]*ALE\.ScriptPath[[:space:]]*=.*$|ALE.ScriptPath = "/azerothcore/env/dist/etc/modules/lua_scripts"|' \
        "$conf_active"
    print_success "Set ALE.ScriptPath = \"/azerothcore/env/dist/etc/modules/lua_scripts\""

    echo ""
    print_info "ALE configuration complete."
    print_info "Place your Lua scripts in:"
    print_info "  $lua_scripts_dir"
    print_info "Restart the worldserver for changes to take effect."
}

# ─────────────────────────────────────────────────────────────
# ALE LUA SCRIPT MANAGEMENT
# ─────────────────────────────────────────────────────────────
# Lua scripts that extend gameplay via the ALE engine.
# Clones:   $SERVER_DIR/ale_scripts/<key>/
# Deployed: $SERVER_DIR/env/dist/etc/modules/lua_scripts/
#           = /azerothcore/env/dist/etc/modules/lua_scripts/ in-container

ale_script_clone_dir()    { echo "$SERVER_DIR/ale_scripts/$1"; }
ale_script_is_installed() { [ -d "$SERVER_DIR/ale_scripts/$1/.git" ]; }
ale_lua_scripts_dir()     { echo "$SERVER_DIR/env/dist/etc/modules/lua_scripts"; }

# Check whether a script's Lua files are present in the lua_scripts deploy dir.
# Uses the same per-key path knowledge as ale_deploy_lua_files().
ale_lua_is_deployed() {
    local key="$1"
    local lua_dir
    lua_dir=$(ale_lua_scripts_dir)
    case "$key" in
        accountwide)   [ -d "$lua_dir/accountwide" ] && \
                       ls "$lua_dir/accountwide"/*.lua &>/dev/null ;;
        levelupreward) ls "$lua_dir"/LevelUpReward*.lua &>/dev/null 2>&1 || \
                       ls "$lua_dir"/levelup*.lua &>/dev/null 2>&1 || \
                       ls "$lua_dir"/LevelUp*.lua &>/dev/null 2>&1 ;;
        exchangenpc)   ls "$lua_dir"/Exchange*.lua &>/dev/null 2>&1 || \
                       ls "$lua_dir"/exchange*.lua &>/dev/null 2>&1 ;;
        activechat)    [ -d "$lua_dir/activechat" ] ;;
        battlepass)    [ -d "$lua_dir/battlepass" ] ;;
        paragon)       [ -d "$lua_dir/paragon" ] ;;
        bmah)          [ -f "$lua_dir/bmah_server.lua" ] ;;
        lootpet)       [ -f "$lua_dir/LootPet.lua" ] ;;
        sod)           ls "$lua_dir"/sod*.lua &>/dev/null 2>&1 || \
                       ls "$lua_dir"/SoD*.lua &>/dev/null 2>&1 || \
                       ls "$lua_dir"/season*.lua &>/dev/null 2>&1 ;;
        *)             false ;;
    esac
}

# Ensure the database container is up. Returns 1 if it cannot start.
ensure_db_running() {
    refresh_container_names
    if container_running "$DB_CONTAINER"; then
        return 0
    fi
    print_info "Starting database container..."
    (cd "$SERVER_DIR" && docker compose up -d ac-database 2>/dev/null) || true
    refresh_container_names
    local i
    for i in $(seq 1 15); do
        if docker exec "$DB_CONTAINER" mysqladmin ping \
            -uroot -p"$DB_ROOT_PASSWORD" &>/dev/null 2>&1; then
            print_success "Database is up."
            return 0
        fi
        sleep 2
    done
    print_error "Database did not become ready."
    return 1
}

# Run a SQL file against a named database. DB must be running.
# Usage: ale_run_sql_file <db_name> <path_to_sql_file>
ale_run_sql_file() {
    local db_name="$1" sql_file="$2"
    if [ ! -f "$sql_file" ]; then
        print_warning "SQL file not found: $sql_file"
        return 1
    fi
    print_info "Applying SQL: $(basename "$sql_file") → $db_name"
    if docker exec -i "$DB_CONTAINER" mysql \
        -uroot -p"$DB_ROOT_PASSWORD" "$db_name" < "$sql_file" 2>&1; then
        print_success "SQL applied: $(basename "$sql_file")"
    else
        print_warning "SQL had errors — table may already exist, which is usually safe."
    fi
}

# ── Per-script post-install configuration ────────────────────

configure_ale_battlepass() {
    local clone_dir
    clone_dir=$(ale_script_clone_dir "battlepass")

    print_step "Battle Pass — SQL & Configuration"

    # Apply SQL
    print_info "Applying Battle Pass SQL (requires database to be running)..."
    if ensure_db_running; then
        ale_run_sql_file "acore_world"      "$clone_dir/sql/battlepass_world.sql"
        ale_run_sql_file "acore_characters" "$clone_dir/sql/battlepass_characters.sql"
    else
        print_warning "Skipping SQL — apply manually when the database is running:"
        print_info "  $clone_dir/sql/battlepass_world.sql      → acore_world"
        print_info "  $clone_dir/sql/battlepass_characters.sql → acore_characters"
    fi

    # Interactive config updates to battlepass_config table
    if container_running "$DB_CONTAINER"; then
        echo ""
        print_info "Configure Battle Pass settings (press ENTER to keep defaults):"
        echo ""
        local max_level exp_per_level debug_mode
        printf "${WHITE}  Max Battle Pass level     [100]: ${RST}"; read -r max_level
        printf "${WHITE}  Base XP required per level [1000]: ${RST}"; read -r exp_per_level
        printf "${WHITE}  Enable debug logging  (0=off/1=on) [0]: ${RST}"; read -r debug_mode
        max_level=${max_level:-100}
        exp_per_level=${exp_per_level:-1000}
        debug_mode=${debug_mode:-0}

        if docker exec "$DB_CONTAINER" mysql -uroot -p"$DB_ROOT_PASSWORD" acore_world \
            -e "UPDATE battlepass_config SET value='$max_level'    WHERE \`key\`='max_level';
                UPDATE battlepass_config SET value='$exp_per_level' WHERE \`key\`='exp_per_level';
                UPDATE battlepass_config SET value='$debug_mode'   WHERE \`key\`='debug_mode';" \
            2>/dev/null; then
            print_success "Battle Pass config applied."
        else
            print_warning "Config update failed — the battlepass_config table may not exist yet."
            print_info "Run the SQL files above first, then reconfigure via option C."
        fi
    fi

    # Client addon
    echo ""
    print_step "Battle Pass — Client Addon Required"
    echo -e "${WHITE}The Battle Pass system includes a WoW client addon for the in-game UI.${RST}"
    echo ""
    echo -e "${WHITE}${BOLD}Copy this folder to your WoW client's AddOns directory:${RST}"
    echo -e "  ${CYAN}Source:${RST}      $clone_dir/BattlePass/"
    echo -e "  ${CYAN}Destination:${RST} <WoW_Client>/Interface/AddOns/BattlePass/"
    echo ""
    echo -e "${WHITE}Use ${CYAN}/bp${WHITE} or ${CYAN}/battlepass${WHITE} in-game to open the Battle Pass frame.${RST}"
    echo -e "${WHITE}Player commands: ${CYAN}.bp${WHITE} | ${CYAN}.bp rewards${WHITE} | ${CYAN}.bp claim <level>${RST}"
}

configure_ale_paragon() {
    local clone_dir
    clone_dir=$(ale_script_clone_dir "paragon")

    print_step "Paragon Anniversary — SQL Migrations Required"
    echo ""
    echo -e "${WHITE}Paragon Anniversary requires SQL files to be applied BEFORE first startup.${RST}"
    echo -e "${WHITE}Tables are NOT auto-created — you must run these migrations manually.${RST}"
    echo ""
    if ensure_db_running; then
        local paragon_sql_dir="$clone_dir/sql"
        local sql_files
        sql_files=$(find "$paragon_sql_dir" -name "*.sql" 2>/dev/null | sort)
        if [ -n "$sql_files" ]; then
            print_info "Found SQL files in $paragon_sql_dir:"
            echo "$sql_files" | while read -r f; do echo "    - $(basename "$f")"; done
            echo ""
            if ask_yes_no "Apply all Paragon SQL files to acore_world now?"; then
                echo "$sql_files" | while read -r f; do
                    ale_run_sql_file "acore_world" "$f"
                done
            else
                print_warning "Apply SQL manually before starting the server:"
                echo "$sql_files" | while read -r f; do
                    print_info "  mysql acore_world < $f"
                done
            fi
        else
            print_warning "No SQL files found in $paragon_sql_dir"
            print_info "Check the repo's sql/ directory and apply required migrations."
        fi
    else
        print_warning "Database not available. Apply SQL files from $clone_dir/sql/ manually."
    fi

    echo ""
    print_step "Paragon Anniversary — Configuration Guide"
    echo ""
    echo -e "${WHITE}${BOLD}Key settings — edit in the ${CYAN}paragon_config${WHITE}${BOLD} database table:${RST}"
    echo ""
    printf "  ${CYAN}%-38s${RST} ${WHITE}%s${RST}\n" "LEVEL_LINKED_TO_ACCOUNT" "0 = per-character  |  1 = account-wide shared XP"
    printf "  ${CYAN}%-38s${RST} ${WHITE}%s${RST}\n" "PARAGON_LEVEL_CAP"        "Max paragon level (0 = unlimited)"
    printf "  ${CYAN}%-38s${RST} ${WHITE}%s${RST}\n" "BASE_MAX_EXPERIENCE"      "XP needed per level (multiplied by paragon level)"
    printf "  ${CYAN}%-38s${RST} ${WHITE}%s${RST}\n" "POINTS_PER_LEVEL"         "Stat points awarded each paragon level"
    printf "  ${CYAN}%-38s${RST} ${WHITE}%s${RST}\n" "UNIVERSAL_CREATURE_EXPERIENCE" "Default XP per creature kill (default: 50)"
    echo ""
    echo -e "${DIM}Full install guide: $clone_dir/doc/INSTALL.md${RST}"

    # Client addon
    echo ""
    print_step "Paragon Anniversary — Client Addon Required"
    echo -e "${WHITE}Paragon includes a WoW client addon for the in-game progression UI.${RST}"
    echo ""
    echo -e "${WHITE}${BOLD}Find the addon folder in the cloned repo and copy it to AddOns:${RST}"
    echo -e "  ${CYAN}Look in:${RST}     $clone_dir/"
    echo -e "  ${CYAN}Destination:${RST} <WoW_Client>/Interface/AddOns/ParagonAnniversary/"
    echo ""
    echo -e "${DIM}The addon communicates with the server via the ParagonAnniversary protocol.${RST}"
}

# Return 0 if $1 is already in the remaining args; used for dedup in
# configure_ale_bmah without associative arrays (Bash 3 / macOS compatible).
_bmah_in_list() {
    local needle="$1"; shift
    local item
    for item in "$@"; do [ "$item" = "$needle" ] && return 0; done
    return 1
}

configure_ale_bmah() {
    local clone_dir
    clone_dir=$(ale_script_clone_dir "bmah")
    local lua_dir
    lua_dir=$(ale_lua_scripts_dir)
    local deployed_file="$lua_dir/bmah_server.lua"

    # Parallel arrays — index-aligned (Bash 3 compatible; no associative arrays).
    local -a BNPC_IDS=(   45281   2496  11183   8921   3162  14740  11504  10834)
    local -a BNPC_NAMES=( "Slytter"
                          "Krazek"
                          "Dirge Quikcleave"
                          "Ravenholdt Guards"
                          "Slyres Notrash"
                          "Stonard Smuggler"
                          "Baron Vardus"
                          "Count Remo" )


    print_step "Black Market AH — NPC Vendor Configuration"
    echo ""
    echo -e "${WHITE}The BMAH opens when a player interacts with any gossip-enabled NPC whose${RST}"
    echo -e "${WHITE}entry ID is listed in ${CYAN}BMAH_VENDOR_NPCs${WHITE} inside bmah_server.lua.${RST}"
    echo ""

    # ── Missing file guard ────────────────────────────────────
    if [ ! -f "$deployed_file" ]; then
        print_warning "bmah_server.lua not found at:"
        print_info "  $deployed_file"
        print_info "Deploy the script first (install from the ALE Scripts menu), then reconfigure."
        echo ""
        print_step "Black Market AH — Client Addon Required"
        echo -e "${WHITE}BMAH includes a WoW addon that recreates the Mists of Pandaria BMAH UI.${RST}"
        echo ""
        echo -e "${WHITE}${BOLD}Copy this folder to your WoW client's AddOns directory:${RST}"
        echo -e "  ${CYAN}Source:${RST}      $clone_dir/BlackMarketUI/"
        echo -e "  ${CYAN}Destination:${RST} <WoW_Client>/Interface/AddOns/BlackMarketUI/"
        echo ""
        echo -e "${WHITE}After copying, run ${CYAN}/reload${WHITE} or restart the WoW client.${RST}"
        return 1
    fi

    # ── Show current IDs extracted from the file ──────────────
    local current_ids
    current_ids=$(awk '
        /^[[:space:]]*local BMAH_VENDOR_NPCs[[:space:]]*=/ { found=1 }
        found {
            tmp = $0
            gsub(/--[^\n]*/, "", tmp)   # strip line comments
            while (match(tmp, /[0-9]+/)) {
                print substr(tmp, RSTART, RLENGTH)
                tmp = substr(tmp, RSTART + RLENGTH)
            }
        }
        found && /\}/ { exit }
    ' "$deployed_file" | sort -u | tr '\n' ' ' | sed 's/[[:space:]]*$//')

    if [ -n "$current_ids" ]; then
        echo -e "${WHITE}Currently configured NPC IDs:${RST} ${CYAN}${current_ids}${RST}"
        echo ""
    fi

    # ── NPC selection menu ────────────────────────────────────
    echo -e "  ${GOLD}──  Suggested Vendor NPCs  ──────────────────────────────────────────────${RST}"
    echo ""
    echo -e "  ${DIM}Shady / Neutral Factions${RST}"
    printf "  ${WHITE}%2s)${RST} %-20s ${CYAN}(%5s)${RST}  %s\n" \
        1 "Slytter"          45281 "Goblin rogue in Booty Bay; coastal smuggler theme" \
        2 "Krazek"            2496 "Booty Bay goblin with shady business connections" \
        3 "Dirge Quikcleave" 11183 "Gadgetzan butcher; deals in rare exotic goods"
    echo ""
    echo -e "  ${DIM}Criminal Underworld / Rogue Themes${RST}"
    printf "  ${WHITE}%2s)${RST} %-20s ${CYAN}(%5s)${RST}  %s\n" \
        4 "Ravenholdt Guards"  8921 "Ravenholdt Manor; gate BMAH behind rogue faction" \
        5 "Slyres Notrash"     3162 "Dalaran Underbelly; illicit underground zone" \
        6 "Stonard Smuggler"  14740 "Out-of-the-way NPC; hidden underground trade network"
    echo ""
    echo -e "  ${DIM}High Society / Wealthy Elites${RST}"
    printf "  ${WHITE}%2s)${RST} %-20s ${CYAN}(%5s)${RST}  %s\n" \
        7 "Baron Vardus"      11504 "Corrupt noble; wealthy elites buying illegal artifacts" \
        8 "Count Remo"        10834 "High-standing noble; hidden high-end auction ring"
    echo ""
    echo -e "  ${GOLD}────────────────────────────────────────────────────────────────────────${RST}"
    echo ""
    printf "${WHITE}Select NPCs by number (e.g. 1 3 7), or \"all\". Leave blank to keep current IDs: ${RST}"
    read -r _bsel

    # ── Parse numbered selection ──────────────────────────────
    local -a sel_ids=()
    local -a sel_names=()

    local _bsel_lower
    _bsel_lower=$(printf '%s' "$_bsel" | tr '[:upper:]' '[:lower:]')
    if [ "$_bsel_lower" = "all" ]; then
        sel_ids=("${BNPC_IDS[@]}")
        sel_names=("${BNPC_NAMES[@]}")
    elif [ -n "$_bsel" ]; then
        local tok
        for tok in $_bsel; do
            if [[ "$tok" =~ ^[0-9]+$ ]] && \
               [ "$tok" -ge 1 ] && [ "$tok" -le "${#BNPC_IDS[@]}" ]; then
                sel_ids+=("${BNPC_IDS[$((tok - 1))]}")
                sel_names+=("${BNPC_NAMES[$((tok - 1))]}")
            else
                print_warning "  Skipping invalid selection: $tok (valid range: 1-${#BNPC_IDS[@]})"
            fi
        done
    fi

    # ── Optional extra custom NPC ID ─────────────────────────
    echo ""
    printf "${WHITE}Add a custom NPC entry ID? (leave blank to skip): ${RST}"
    read -r _bcustom
    if [[ "$_bcustom" =~ ^[0-9]+$ ]]; then
        sel_ids+=("$_bcustom")
        sel_names+=("Custom NPC")
    elif [ -n "$_bcustom" ]; then
        print_warning "  '$_bcustom' is not a valid numeric entry ID — skipping."
    fi

    # ── Decide add vs replace ─────────────────────────────────
    local _bmode="add"
    if [ "${#sel_ids[@]}" -gt 0 ] && [ -n "$current_ids" ]; then
        echo ""
        printf "${WHITE}Apply as: [a] Add to existing list  [r] Replace list entirely [a]: ${RST}"
        read -r _bmode_raw
        case "$_bmode_raw" in [Rr]) _bmode="replace" ;; esac
    fi

    # ── Build final deduped ID list ───────────────────────────
    local -a final_ids=()

    if [ "$_bmode" = "add" ] && [ -n "$current_ids" ]; then
        local id
        for id in $current_ids; do
            [[ "$id" =~ ^[0-9]+$ ]] && final_ids+=("$id")
        done
    fi

    local i
    for (( i=0; i<${#sel_ids[@]}; i++ )); do
        local sid="${sel_ids[$i]}"
        if ! _bmah_in_list "$sid" "${final_ids[@]}"; then
            final_ids+=("$sid")
        fi
    done

    # ── Patch the file (or skip if nothing to change) ─────────
    if [ "${#final_ids[@]}" -eq 0 ] && [ -z "$_bsel" ] && [ -z "$_bcustom" ]; then
        print_info "No changes to NPC list."
    elif [ "${#final_ids[@]}" -eq 0 ]; then
        print_warning "Resulting NPC list is empty — skipping file patch."
        print_info "Add at least one NPC ID, or edit ${deployed_file} manually."
    else
        # Write one NPC entry per line to a temp file; awk reads it with
        # getline so embedded newlines never hit the -v variable limit.
        local ids_file tmpfile j label
        ids_file=$(mktemp "${TMPDIR:-/tmp}/bmah_ids_XXXXXX") || {
            print_error "Could not create temp file — aborting NPC patch."
            return 1
        }
        tmpfile=$(mktemp "${TMPDIR:-/tmp}/bmah_server_XXXXXX.lua") || {
            rm -f "$ids_file"
            print_error "Could not create temp file — aborting NPC patch."
            return 1
        }
        for id in "${final_ids[@]}"; do
            label=""
            for (( j=0; j<${#BNPC_IDS[@]}; j++ )); do
                [ "${BNPC_IDS[$j]}" = "$id" ] && label=" --${BNPC_NAMES[$j]}" && break
            done
            printf "  %s,%s\n" "$id" "$label" >> "$ids_file"
        done

        # awk replaces the BMAH_VENDOR_NPCs block — handles both inline and
        # multiline forms.  Anchored to line-start so a commented-out example
        # line cannot trigger the replacement.  Exits 1 if the pattern was
        # never matched so we detect a failed patch before overwriting the file.
        awk -v ids_file="$ids_file" '
            /^[[:space:]]*local BMAH_VENDOR_NPCs[[:space:]]*=/ {
                print "local BMAH_VENDOR_NPCs = {"
                while ((getline line < ids_file) > 0) { print line }
                close(ids_file)
                replaced++
                if (/\}/) { print "}"; next }   # inline closing brace on same line
                skip = 1
                next
            }
            skip {
                if (/^[[:space:]]*\}/) { print "}"; skip = 0 }
                next
            }
            { print }
            END { if (replaced == 0) exit 1 }
        ' "$deployed_file" > "$tmpfile"
        local awk_rc=$?
        rm -f "$ids_file"

        if [ $awk_rc -eq 0 ] && [ -s "$tmpfile" ]; then
            if mv "$tmpfile" "$deployed_file"; then
                echo ""
                print_success "BMAH_VENDOR_NPCs updated with ${#final_ids[@]} NPC(s):"
                for id in "${final_ids[@]}"; do
                    label=""
                    for (( j=0; j<${#BNPC_IDS[@]}; j++ )); do
                        [ "${BNPC_IDS[$j]}" = "$id" ] && label=" — ${BNPC_NAMES[$j]}" && break
                    done
                    print_info "  • ${id}${label}"
                done
            else
                rm -f "$tmpfile"
                print_error "Could not write updated file — check permissions on: $deployed_file"
            fi
        else
            rm -f "$tmpfile"
            print_error "Could not locate BMAH_VENDOR_NPCs block in bmah_server.lua."
            print_info "Edit manually: $deployed_file"
            print_info "Look for: local BMAH_VENDOR_NPCs = { ... } and add your IDs."
        fi
    fi

    # ── Pricing & timing reference ────────────────────────────
    echo ""
    echo -e "${WHITE}Other configurable values (edit directly in bmah_server.lua):${RST}"
    echo ""
    printf "  ${CYAN}%-32s${RST} ${WHITE}%s${RST}\n" \
        "common/rare/ultraRare_*_price"  "Starting bids per item category and tier" \
        "FillRateCommon / Rare / Ultra"  "Rarity probabilities (default: 85%% / 10%% / 5%%)" \
        "MinBidIncrementG"               "Minimum gold increment per bid (default: 10g)" \
        "AutoFillChance"                 "Chance to restock when empty (default: 0.50)" \
        "PotentialDurations"             "Auction lengths in minutes (default: 720, 1440)"
    echo ""
    echo -e "${WHITE}GM commands: ${CYAN}/bmah flush${WHITE} (expire all) | ${CYAN}/bmah fill${WHITE} (refill immediately)${RST}"

    # ── Client addon ─────────────────────────────────────────
    echo ""
    print_step "Black Market AH — Client Addon Required"
    echo -e "${WHITE}BMAH includes a WoW addon that recreates the Mists of Pandaria BMAH UI.${RST}"
    echo ""
    echo -e "${WHITE}${BOLD}Copy this folder to your WoW client's AddOns directory:${RST}"
    echo -e "  ${CYAN}Source:${RST}      $clone_dir/BlackMarketUI/"
    echo -e "  ${CYAN}Destination:${RST} <WoW_Client>/Interface/AddOns/BlackMarketUI/"
    echo ""
    echo -e "${WHITE}After copying, run ${CYAN}/reload${WHITE} or restart the WoW client.${RST}"
}

# ─────────────────────────────────────────────────────────────
# ACCOUNTWIDE CONFIGURATION
# ─────────────────────────────────────────────────────────────
# Patches ENABLE_* flags in the deployed Accountwide Lua scripts.
# Each system is opt-in — all flags default to false upstream.
# Handles the dual reputation variant by prompting which to keep
# and removing the other so both aren't loaded simultaneously.
#
# Tolerant sed pattern:  s/\(local FLAG\)[[:space:]]*=[[:space:]]*false/\1 = true/
# Verifies each patch landed; warns if the file was not changed.

# Patch one ENABLE flag from false → true in a deployed Lua file.
# Usage: _aw_enable <file> <FLAG_NAME>
# Returns 1 and prints a warning if the patch cannot be verified.
# Uses a temp-file rewrite instead of sed -i so it works on both
# macOS (BSD sed) and Linux (GNU sed) without a backup-suffix dance.
_aw_enable() {
    local file="$1" flag="$2"
    if [ ! -f "$file" ]; then
        print_warning "  File not found: $(basename "$file") — skipping."
        return 1
    fi
    local _aw_tmp
    _aw_tmp=$(mktemp "${TMPDIR:-/tmp}/aw_enable_XXXXXX") || {
        print_warning "  mktemp failed; cannot patch ${flag}."
        return 1
    }
    # Anchor to line-start so commented-out lines (-- local FLAG = false) are skipped.
    sed "s/^\([[:space:]]*local ${flag}\)[[:space:]]*=[[:space:]]*false/\1 = true/" "$file" > "$_aw_tmp"
    if grep -q "^[[:space:]]*local ${flag} = true" "$_aw_tmp"; then
        mv "$_aw_tmp" "$file"
        return 0
    fi
    rm -f "$_aw_tmp"
    print_warning "  Could not patch ${flag} in $(basename "$file") — edit manually."
    return 1
}

configure_ale_accountwide() {
    local lua_dir
    lua_dir=$(ale_lua_scripts_dir)
    local aw_dir="$lua_dir/accountwide"

    print_step "Configuring Accountwide Systems"

    if [ ! -d "$aw_dir" ]; then
        print_error "Accountwide scripts not found at: $aw_dir"
        print_info "Install the script first from the ALE Scripts menu."
        return 1
    fi

    echo ""
    echo -e "${WHITE}Each system is enabled independently — answer Y to activate each one.${RST}"
    echo -e "${WHITE}All systems are disabled by default; only enable what you want.${RST}"
    echo -e "${DIM}The characters DB tables must be applied before starting the server.${RST}"
    echo ""

    # ── Achievements ──────────────────────────────────────────
    local f_ach="$aw_dir/AccountAchievements.lua"
    if [ -f "$f_ach" ]; then
        echo -e "${GOLD}Achievements${RST}"
        if ask_yes_no "Enable Accountwide Completed Achievements (sync earned achievements across alts)?"; then
            _aw_enable "$f_ach" "ENABLE_ACCOUNTWIDE_COMPLETED_ACHIEVEMENTS" && \
                print_success "  Completed Achievements enabled."
        fi
        if ask_yes_no "Enable Accountwide Achievement Criteria Progress (sync partial criteria)?"; then
            _aw_enable "$f_ach" "ENABLE_ACCOUNTWIDE_CRITERIA_PROGRESS" && \
                print_success "  Criteria Progress enabled."
        fi
        echo ""
    fi

    # ── Currency ─────────────────────────────────────────────
    local f_cur="$aw_dir/AccountCurrency.lua"
    if [ -f "$f_cur" ]; then
        echo -e "${GOLD}Currency${RST}"
        if ask_yes_no "Enable Accountwide Currency (shared badge/token counts across all characters)?"; then
            _aw_enable "$f_cur" "ENABLE_ACCOUNTWIDE_CURRENCY" && \
                print_success "  Currency enabled."
        fi
        echo ""
    fi

    # ── Money ────────────────────────────────────────────────
    local f_mon="$aw_dir/AccountMoney.lua"
    if [ -f "$f_mon" ]; then
        echo -e "${GOLD}Money${RST}"
        if ask_yes_no "Enable Accountwide Money (shared gold pool across all characters)?"; then
            if _aw_enable "$f_mon" "ENABLE_ACCOUNTWIDE_MONEY"; then
                print_success "  Money enabled."
                if ask_yes_no "  Enable real-time gold tick (syncs gold every ~5 s while online)?"; then
                    if _aw_enable "$f_mon" "ENABLE_REALTIME_TICK"; then
                        print_success "  Realtime tick enabled."
                        if ask_yes_no "  Also enable realtime tick for Altbots?"; then
                            _aw_enable "$f_mon" "ENABLE_ALTBOT_REALTIME_TICK" && \
                                print_success "  Altbot realtime tick enabled."
                        fi
                    fi
                fi
            fi
        fi
        echo ""
    fi

    # ── Mounts ───────────────────────────────────────────────
    local f_mnt="$aw_dir/AccountMounts.lua"
    if [ -f "$f_mnt" ]; then
        echo -e "${GOLD}Mounts${RST}"
        if ask_yes_no "Enable Accountwide Mounts (shared learned mounts across all characters)?"; then
            _aw_enable "$f_mnt" "ENABLE_ACCOUNTWIDE_MOUNTS" && \
                print_success "  Mounts enabled."
        fi
        echo ""
    fi

    # ── Pets ─────────────────────────────────────────────────
    local f_pet="$aw_dir/AccountPets.lua"
    if [ -f "$f_pet" ]; then
        echo -e "${GOLD}Pets${RST}"
        if ask_yes_no "Enable Accountwide Pets (shared companion pets across all characters)?"; then
            _aw_enable "$f_pet" "ENABLE_ACCOUNTWIDE_PETS" && \
                print_success "  Pets enabled."
        fi
        echo ""
    fi

    # ── Playtime ─────────────────────────────────────────────
    local f_play="$aw_dir/AccountPlaytime.lua"
    if [ -f "$f_play" ]; then
        echo -e "${GOLD}Playtime${RST}"
        if ask_yes_no "Enable Accountwide Playtime (.playtime command for total account play time)?"; then
            _aw_enable "$f_play" "ENABLE_ACCOUNTWIDE_PLAYTIME" && \
                print_success "  Playtime enabled."
        fi
        echo ""
    fi

    # ── PvP Rank ─────────────────────────────────────────────
    local f_pvp="$aw_dir/AccountPvPRank.lua"
    if [ -f "$f_pvp" ]; then
        echo -e "${GOLD}PvP Rank${RST}"
        print_info "  RUN_INIT_SEED_ON_STARTUP is true by default — this seeds existing PvP"
        print_info "  data on first server start. Set it to false in AccountPvPRank.lua after."
        if ask_yes_no "Enable Accountwide PvP Rank (sync honor kills, honor/arena points)?"; then
            _aw_enable "$f_pvp" "ENABLE_ACCOUNTWIDE_PVP_RANK" && \
                print_success "  PvP Rank enabled."
        fi
        echo ""
    fi

    # ── Reputation ───────────────────────────────────────────
    # Two variants ship in the repo. Both get copied by the deploy step.
    # Only one should be loaded — the other must be removed.
    local f_rep_default f_rep_other f_rep_target
    f_rep_default=$(find "$aw_dir" -name "AccountReputation*default*" 2>/dev/null | head -1)
    f_rep_other=$(find "$aw_dir" -name "AccountReputation*.lua" ! -name "*default*" 2>/dev/null | head -1)

    echo -e "${GOLD}Reputation${RST}"
    if [ -n "$f_rep_default" ] && [ -n "$f_rep_other" ]; then
        print_info "  Two reputation variants are deployed — only one can be active:"
        print_info "  1) Default AC-WotLK  (standard AzerothCore factions)"
        print_info "  2) $(basename "$f_rep_other" .lua)  (custom server modifications, Offline doesn't use this.)"
        printf "${WHITE}  Choose variant [1/2, default=1]: ${RST}"
        read -r _rep_choice
        if [ "$_rep_choice" = "2" ]; then
            f_rep_target="$f_rep_other"
            if rm -f "$f_rep_default" && [ ! -f "$f_rep_default" ]; then
                print_success "  Removed default variant, keeping: $(basename "$f_rep_other")"
            else
                print_warning "  Could not remove default variant — both files may load. Remove manually:"
                print_info "    $f_rep_default"
            fi
        else
            f_rep_target="$f_rep_default"
            if rm -f "$f_rep_other" && [ ! -f "$f_rep_other" ]; then
                print_success "  Removed custom variant, keeping: $(basename "$f_rep_default")"
            else
                print_warning "  Could not remove custom variant — both files may load. Remove manually:"
                print_info "    $f_rep_other"
            fi
        fi
    elif [ -n "$f_rep_default" ]; then
        f_rep_target="$f_rep_default"
    elif [ -n "$f_rep_other" ]; then
        f_rep_target="$f_rep_other"
    fi

    if [ -n "$f_rep_target" ]; then
        if ask_yes_no "Enable Accountwide Reputation (shared faction rep, faction-gated by Horde/Alliance)?"; then
            _aw_enable "$f_rep_target" "ENABLE_ACCOUNTWIDE_REPUTATION" && \
                print_success "  Reputation enabled ($(basename "$f_rep_target"))."
        fi
    else
        print_warning "  No AccountReputation*.lua found in $aw_dir — skipping."
    fi
    echo ""

    # ── Taxi Paths ───────────────────────────────────────────
    local f_taxi="$aw_dir/AccountTaxiPaths.lua"
    if [ -f "$f_taxi" ]; then
        echo -e "${GOLD}Taxi Paths${RST}"
        print_info "  Requires Aldori15's custom mod-ale fork with updated C++ bindings."
        print_info "  Skip this unless you're running that specific fork."
        if ask_yes_no "Enable Accountwide Taxi Paths (shared flight paths per faction)?"; then
            _aw_enable "$f_taxi" "ENABLE_ACCOUNTWIDE_TAXI_PATHS" && \
                print_success "  Taxi Paths enabled."
        fi
        echo ""
    fi

    # ── Titles ───────────────────────────────────────────────
    local f_ttl="$aw_dir/AccountTitles.lua"
    if [ -f "$f_ttl" ]; then
        echo -e "${GOLD}Titles${RST}"
        if ask_yes_no "Enable Accountwide Titles (share earned titles across all characters)?"; then
            _aw_enable "$f_ttl" "ENABLE_ACCOUNTWIDE_TITLES" && \
                print_success "  Titles enabled."
        fi
        echo ""
    fi

    echo ""
    print_info "Accountwide configuration complete."
    print_info "Ensure create_accountwide_tables.sql has been applied to acore_characters."
    print_info "Restart the worldserver or run ${CYAN}.reload ale${RST} in-game to activate changes."
}

# ── Lua file deployment (per-script copy strategy) ───────────
# Each script has its own repo layout; this handles the mapping.
ale_deploy_lua_files() {
    local key="$1" clone_dir="$2"
    local lua_dir
    lua_dir=$(ale_lua_scripts_dir)
    mkdir -p "$lua_dir"

    case "$key" in
        accountwide)
            # Upstream layout: lua_scripts/AccountWide/*.lua
            local src="$clone_dir/lua_scripts/AccountWide"
            if [ -d "$src" ]; then
                mkdir -p "$lua_dir/accountwide"
                cp "$src"/*.lua "$lua_dir/accountwide/" && \
                    print_success "Deployed → lua_scripts/accountwide/" || \
                    print_warning "Copy failed — check $src"
            else
                print_warning "Expected directory not found: $src"
                print_info "Manually copy .lua files to: $lua_dir/accountwide/"
            fi
            ;;
        levelupreward)
            local count=0
            if cp "$clone_dir"/*.lua "$lua_dir/" 2>/dev/null; then
                count=$(ls "$clone_dir"/*.lua 2>/dev/null | wc -l | tr -d ' ')
                print_success "Deployed $count file(s) → lua_scripts/"
            else
                print_warning "No .lua files found in clone root — check $clone_dir"
            fi
            ;;
        exchangenpc)
            local count=0
            if cp "$clone_dir"/*.lua "$lua_dir/" 2>/dev/null; then
                count=$(ls "$clone_dir"/*.lua 2>/dev/null | wc -l | tr -d ' ')
                print_success "Deployed $count file(s) → lua_scripts/"
            else
                print_warning "No .lua files found in clone root — check $clone_dir"
            fi
            ;;
        activechat)
            # Upstream layout: ActiveChat/ subdirectory
            local src="$clone_dir/ActiveChat"
            if [ -d "$src" ]; then
                mkdir -p "$lua_dir/activechat"
                cp -r "$src"/. "$lua_dir/activechat/" && \
                    print_success "Deployed → lua_scripts/activechat/" || \
                    print_warning "Copy failed — check $src"
            else
                print_warning "Expected directory not found: $src"
                print_info "Manually copy ActiveChat/ contents to: $lua_dir/activechat/"
            fi
            ;;
        battlepass)
            if [ -d "$clone_dir/lua_scripts" ]; then
                cp -r "$clone_dir/lua_scripts/." "$lua_dir/" && \
                    print_success "Deployed → lua_scripts/ (lib/CSMH + battlepass/)" || \
                    print_warning "Copy failed — check $clone_dir/lua_scripts"
            else
                print_warning "lua_scripts/ dir not found in clone — check $clone_dir manually."
            fi
            ;;
        paragon)
            # Upstream layout: serverside/paragon/
            local src="$clone_dir/serverside/paragon"
            if [ -d "$src" ]; then
                cp -r "$src" "$lua_dir/" && \
                    print_success "Deployed → lua_scripts/paragon/" || \
                    print_warning "Copy failed — check $src"
            else
                print_warning "Expected directory not found: $src"
                print_info "Manually copy paragon/ contents to: $lua_dir/paragon/"
            fi
            ;;
        bmah)
            # Upstream layout: Server Files/lua_scripts/bmah_server.lua
            local src="$clone_dir/Server Files/lua_scripts/bmah_server.lua"
            if [ -f "$src" ]; then
                cp "$src" "$lua_dir/" && \
                    print_success "Deployed bmah_server.lua → lua_scripts/" || \
                    print_warning "Copy failed — check '$src'"
            else
                print_warning "Expected file not found: $src"
                print_info "Manually copy bmah_server.lua to: $lua_dir/"
            fi
            ;;
        lootpet)
            if [ -f "$clone_dir/LootPet.lua" ]; then
                cp "$clone_dir/LootPet.lua" "$lua_dir/" && \
                    print_success "Deployed LootPet.lua → lua_scripts/" || \
                    print_warning "Copy failed"
            else
                print_warning "LootPet.lua not found in $clone_dir"
            fi
            ;;
        sod)
            local count=0
            if cp "$clone_dir"/*.lua "$lua_dir/" 2>/dev/null; then
                count=$(ls "$clone_dir"/*.lua 2>/dev/null | wc -l | tr -d ' ')
                print_success "Deployed $count file(s) → lua_scripts/"
            else
                print_warning "No .lua files found in clone root — check $clone_dir"
            fi
            ;;
        *)
            if cp "$clone_dir"/*.lua "$lua_dir/" 2>/dev/null; then
                print_success "Deployed → lua_scripts/ (generic copy)"
            else
                print_warning "No .lua files found in $clone_dir — check repo layout manually."
            fi
            ;;
    esac
}

# Clone/update, deploy Lua files, run per-script extras.
ale_script_install() {
    local key="$1" name="$2" url="$3"
    local clone_dir
    clone_dir=$(ale_script_clone_dir "$key")

    print_step "Installing ALE script: $name"

    mkdir -p "$SERVER_DIR/ale_scripts"
    if ale_script_is_installed "$key"; then
        print_info "Already cloned — pulling latest..."
        (cd "$clone_dir" && git pull --depth 1 2>/dev/null) || \
            print_warning "git pull failed — using existing copy"
    else
        if ! git clone --depth 1 "$url" "$clone_dir"; then
            print_error "Clone failed for $name!"
            return 1
        fi
        print_success "Cloned $name"
    fi

    ale_deploy_lua_files "$key" "$clone_dir"

    # Per-script extra steps
    case "$key" in
        accountwide)
            echo ""
            print_warning "Install on a FRESH server is strongly recommended."
            print_info "00_AccountWideUtils.lua is required alongside all other Accountwide"
            print_info "scripts — it has been deployed with the rest in lua_scripts/accountwide/."
            echo ""
            print_info "Accountwide requires a characters DB schema."
            if ask_yes_no "Apply Accountwide characters SQL now?"; then
                if ensure_db_running; then
                    local sql_file="$clone_dir/sql/create_accountwide_tables.sql"
                    if [ -f "$sql_file" ]; then
                        ale_run_sql_file "acore_characters" "$sql_file"
                    else
                        # Fallback: search for the file if name changed upstream
                        local found
                        found=$(find "$clone_dir/sql" -name "*.sql" 2>/dev/null | head -1)
                        if [ -n "$found" ]; then
                            ale_run_sql_file "acore_characters" "$found"
                        else
                            print_warning "SQL file not found in $clone_dir/sql/"
                        fi
                    fi
                fi
            else
                print_info "Apply manually: mysql acore_characters < $clone_dir/sql/create_accountwide_tables.sql"
            fi
            echo ""
            if ask_yes_no "Configure Accountwide systems (enable individual scripts) now?"; then
                configure_ale_accountwide
            else
                print_info "Reconfigure anytime from the ALE Scripts menu → c on Accountwide."
            fi
            ;;
        exchangenpc)
            echo ""
            print_info "Exchange NPC requires a world SQL file to be applied."
            if ask_yes_no "Apply Exchange NPC world SQL now?"; then
                if ensure_db_running; then
                    # Prefer the *_Up.sql (install) variant; avoid Down/Revert scripts
                    local sql_file
                    sql_file=$(find "$clone_dir" -name "*_Up.sql" 2>/dev/null | head -1)
                    if [ -z "$sql_file" ]; then
                        # Fallback: any SQL that isn't a rollback
                        sql_file=$(find "$clone_dir" -name "*.sql" 2>/dev/null \
                            | grep -v -i "down\|revert" | head -1)
                    fi
                    if [ -n "$sql_file" ]; then
                        ale_run_sql_file "acore_world" "$sql_file"
                    else
                        print_warning "No install SQL file found in $clone_dir"
                        print_info "Manually apply the *_Up.sql file from the repo."
                    fi
                fi
            fi
            echo ""
            print_info "After restart, teleport to the NPC: ${CYAN}.go zonexy 51.1 27.7 976${RST}"
            ;;
        battlepass)
            echo ""
            if ask_yes_no "Configure Battle Pass SQL and settings now?"; then
                configure_ale_battlepass
            else
                print_info "Run option 4 → c5 to reconfigure Battle Pass later."
                print_info "Remember to apply SQL manually from:"
                print_info "  $clone_dir/sql/"
            fi
            ;;
        paragon)
            echo ""
            if ask_yes_no "Show Paragon Anniversary configuration guide now?"; then
                configure_ale_paragon
            fi
            ;;
        bmah)
            echo ""
            if ask_yes_no "Configure Black Market AH (NPC ID + client addon info) now?"; then
                configure_ale_bmah
            fi
            ;;
    esac

    echo ""
    print_info "Reload Lua scripts in-game with: ${CYAN}.reload ale${RST}"
    print_info "Or restart the worldserver from the main menu."
    return 0
}

ale_script_remove() {
    local key="$1" name="$2"
    local clone_dir
    clone_dir=$(ale_script_clone_dir "$key")
    local lua_dir
    lua_dir=$(ale_lua_scripts_dir)

    print_step "Removing ALE script: $name"

    if ! ale_script_is_installed "$key"; then
        print_info "$name is not installed — nothing to do."
        return 0
    fi

    # For scripts whose deployed filenames come from the clone, collect them BEFORE removal
    local -a generic_deployed_files=()
    case "$key" in
        levelupreward|exchangenpc|sod)
            while IFS= read -r f; do
                generic_deployed_files+=("$(basename "$f")")
            done < <(find "$clone_dir" -maxdepth 1 -name "*.lua" 2>/dev/null)
            ;;
    esac

    if ask_yes_no "Remove clone at $clone_dir?"; then
        rm -rf "$clone_dir"
        print_success "Clone removed."
    fi

    # Offer to remove deployed Lua files
    local deployed_hint
    case "$key" in
        accountwide) deployed_hint="$lua_dir/accountwide/" ;;
        activechat)  deployed_hint="$lua_dir/activechat/" ;;
        battlepass)  deployed_hint="$lua_dir/battlepass/  and  $lua_dir/lib/" ;;
        paragon)     deployed_hint="$lua_dir/paragon/" ;;
        bmah)        deployed_hint="$lua_dir/bmah_server.lua" ;;
        lootpet)     deployed_hint="$lua_dir/LootPet.lua" ;;
        *)           deployed_hint="$lua_dir/ (search for files from this script)" ;;
    esac

    echo ""
    print_info "Deployed files: $deployed_hint"
    if ask_yes_no "Also remove deployed Lua files from lua_scripts/?"; then
        case "$key" in
            accountwide) rm -rf "$lua_dir/accountwide" ;;
            activechat)  rm -rf "$lua_dir/activechat" ;;
            battlepass)  rm -rf "$lua_dir/battlepass" "$lua_dir/lib" ;;
            paragon)     rm -rf "$lua_dir/paragon" ;;
            bmah)        rm -f  "$lua_dir/bmah_server.lua" ;;
            lootpet)     rm -f  "$lua_dir/LootPet.lua" ;;
            levelupreward|exchangenpc|sod)
                local f
                for f in "${generic_deployed_files[@]}"; do
                    rm -f "$lua_dir/$f" 2>/dev/null || true
                done
                ;;
        esac
        print_success "Deployed files removed."
    fi

    print_info "(Database tables created by this script are kept — removing them risks data loss.)"
}

# ── ALE Scripts submenu ───────────────────────────────────────
menu_ale_scripts() {
    local page_start=0
    while true; do
        local tlines; tlines=$(tput lines 2>/dev/null || echo 24)

        # Clear menu area
        printf '\033[%d;1H\033[J' "$MENU_START_ROW"

        if ! module_is_installed "mod-ale"; then
            printf "  ${RED}✗ mod-ale (ALE Lua Engine) is not installed.${RST}\n"
            printf "  ${WHITE}Install via main menu option 1, then configure via option 3.${RST}\n"
            printf "\n  ${DIM}Press ENTER to return...${RST}\n"
            read -r _
            return
        fi

        # Build full list with status markers
        local -a available_entries=()
        local -a markers=()
        local entry key name url cloned deployed marker

        for entry in "${ALE_SCRIPT_REGISTRY[@]}"; do
            IFS='|' read -r key name url <<< "$entry"
            cloned=false; deployed=false
            ale_script_is_installed "$key" && cloned=true
            ale_lua_is_deployed     "$key" && deployed=true
            if $deployed && $cloned; then
                marker="${GREEN}✓ Installed${RST}"
            elif $deployed; then
                marker="${CYAN}◑ Deployed only${RST}"
            elif $cloned; then
                marker="${YELLOW}◐ Cloned only${RST}"
            else
                marker="${DIM}○ Not installed${RST}"
            fi
            available_entries+=("$entry")
            markers+=("$marker")
        done

        local total=${#available_entries[@]}

        # Fixed rows: header + col-header + top-div + bottom-div + help + page-bar = 6
        local avail=$(( tlines - MENU_START_ROW - 1 ))
        local page_size=$(( avail - 6 ))
        [ "$page_size" -lt 3 ] && page_size=3

        local max_start=$(( total - page_size ))
        [ "$max_start" -lt 0 ] && max_start=0
        [ "$page_start" -gt "$max_start" ] && page_start=$max_start
        [ "$page_start" -lt 0 ] && page_start=0

        local page_end=$(( page_start + page_size ))
        [ "$page_end" -gt "$total" ] && page_end=$total
        local total_pages=$(( (total + page_size - 1) / page_size ))
        local current_page=$(( page_start / page_size + 1 ))

        printf "  ${GOLD}── ALE Lua Scripts ──────────────────────────────${RST}\n"
        printf "  ${DIM}%-4s %-38s %s${RST}\n" "Num" "Script" "Status"
        printf "  ${GOLD}──────────────────────────────────────────────────${RST}\n"

        local idx
        for (( idx=page_start; idx<page_end; idx++ )); do
            IFS='|' read -r key name url <<< "${available_entries[$idx]}"
            printf "  ${WHITE}%2d)${RST} %-38s %b\n" "$(( idx + 1 ))" "$name" "${markers[$idx]}"
        done

        printf "  ${GOLD}──────────────────────────────────────────────────${RST}\n"
        if [ "$total_pages" -gt 1 ]; then
            local nav="  ${DIM}Page $current_page/$total_pages${RST}"
            [ "$current_page" -gt 1 ]              && nav+="   ${WHITE}< prev${RST}"
            [ "$current_page" -lt "$total_pages" ]  && nav+="   ${WHITE}> next${RST}"
            printf "%b\n" "$nav"
        fi
        printf "  ${WHITE}i<nums>${RST} Install   ${WHITE}r<num>${RST} Remove   ${WHITE}c<num>${RST} Config   ${WHITE}ENTER${RST} Back\n"

        _read_menu_input "$(( tlines - 1 ))"
        local raw_choice="$_MENU_INPUT"

        [ -z "$raw_choice" ] && return

        local action nums c
        action="${raw_choice:0:1}"
        nums="${raw_choice:1}"

        case "${action,,}" in
            '<')
                page_start=$(( page_start - page_size ))
                [ "$page_start" -lt 0 ] && page_start=0
                ;;
            '>')
                page_start=$(( page_start + page_size ))
                [ "$page_start" -gt "$max_start" ] && page_start=$max_start
                ;;
            i)
                local -a num_tokens=()
                if [[ "$nums" =~ ^[0-9]+$ ]] && [ "${#nums}" -gt 1 ]; then
                    local ch
                    for (( ch=0; ch<${#nums}; ch++ )); do
                        num_tokens+=("${nums:$ch:1}")
                    done
                else
                    read -r -a num_tokens <<< "$nums"
                fi
                local -a to_install=()
                for c in "${num_tokens[@]}"; do
                    if [[ "$c" =~ ^[0-9]+$ ]] && \
                       [ "$c" -ge 1 ] && [ "$c" -le "$total" ]; then
                        to_install+=("${available_entries[$((c - 1))]}")
                    fi
                done
                if [ "${#to_install[@]}" -eq 0 ]; then
                    print_warning "No valid script numbers — e.g. i135"
                    press_enter; continue
                fi
                for entry in "${to_install[@]}"; do
                    IFS='|' read -r key name url <<< "$entry"
                    ale_script_install "$key" "$name" "$url" || true
                    echo ""
                done
                press_enter
                ;;
            r)
                local rnum; rnum=$(echo "$nums" | tr -d ' ')
                if ! [[ "$rnum" =~ ^[0-9]+$ ]] || \
                   [ "$rnum" -lt 1 ] || [ "$rnum" -gt "$total" ]; then
                    print_warning "Invalid script number — e.g. r2"
                    press_enter; continue
                fi
                IFS='|' read -r key name url <<< "${available_entries[$((rnum - 1))]}"
                ale_script_remove "$key" "$name"
                press_enter
                ;;
            c)
                local cnum; cnum=$(echo "$nums" | tr -d ' ')
                if ! [[ "$cnum" =~ ^[0-9]+$ ]] || \
                   [ "$cnum" -lt 1 ] || [ "$cnum" -gt "$total" ]; then
                    print_warning "Invalid script number — e.g. c5"
                    press_enter; continue
                fi
                IFS='|' read -r key name url <<< "${available_entries[$((cnum - 1))]}"
                case "$key" in
                    accountwide) configure_ale_accountwide ;;
                    battlepass) configure_ale_battlepass ;;
                    paragon)    configure_ale_paragon ;;
                    bmah)       configure_ale_bmah ;;
                    *) print_info "No dedicated reconfigure for $name." ;;
                esac
                press_enter
                ;;
            *)
                print_warning "Unknown command. Use i<nums>, r<num>, c<num>, or ENTER."
                press_enter
                ;;
        esac
    done
}

# ─────────────────────────────────────────────────────────────
# MAIN MENUS
# ─────────────────────────────────────────────────────────────

# ── Unified module browser ────────────────────────────────────
# i <nums>  Install one or more (space-separated)
# r <num>   Remove one
# ENTER     Return to main menu
menu_modules() {
    local page_start=0
    while true; do
        local tlines; tlines=$(tput lines 2>/dev/null || echo 24)

        # Build full registry list (always done fresh for current status)
        local -a available_entries=()
        local -a markers=()
        local entry key name url sql_dirs marker

        for entry in "${MODULE_REGISTRY[@]}"; do
            IFS='|' read -r key name url sql_dirs <<< "$entry"
            if module_is_installed "$key"; then
                marker="${GREEN}✓ Installed${RST}"
            else
                marker="${DIM}○ Not installed${RST}"
            fi
            available_entries+=("$entry")
            markers+=("$marker")
        done

        local total=${#available_entries[@]}

        # Collect unregistered modules (read-only info section)
        local -a other_modules=()
        local -a other_notes=()
        if [ -d "$SERVER_DIR/modules" ]; then
            local d dn in_registry
            for d in "$SERVER_DIR/modules"/*/; do
                [ -d "$d" ] || continue
                dn=$(basename "$d")
                in_registry=false
                for entry in "${MODULE_REGISTRY[@]}"; do
                    IFS='|' read -r key _ _ _ <<< "$entry"
                    [ "$key" = "$dn" ] && { in_registry=true; break; }
                done
                if [ "$in_registry" = false ]; then
                    local note="manually added"
                    [ "$dn" = "mod-playerbots" ] && note="bundled"
                    other_modules+=("$dn")
                    other_notes+=("$note")
                fi
            done
        fi

        # Fixed rows: header(1) + col-header(1) + top-div(1) + bottom-div(1) + help(1) + page-bar(1) = 6
        # Reserve extra rows for "other" section if present: divider(1) + label(1) + items
        local other_count=${#other_modules[@]}
        local other_rows=$(( other_count > 0 ? other_count + 2 : 0 ))
        local avail=$(( tlines - MENU_START_ROW - 1 ))
        local page_size=$(( avail - 6 - other_rows ))
        [ "$page_size" -lt 3 ] && page_size=3
        # If "other" section doesn't fit, drop it from the calculation
        if [ "$page_size" -lt 3 ]; then
            other_rows=0
            page_size=$(( avail - 6 ))
            [ "$page_size" -lt 3 ] && page_size=3
        fi

        local max_start=$(( total - page_size ))
        [ "$max_start" -lt 0 ] && max_start=0
        [ "$page_start" -gt "$max_start" ] && page_start=$max_start
        [ "$page_start" -lt 0 ] && page_start=0

        local page_end=$(( page_start + page_size ))
        [ "$page_end" -gt "$total" ] && page_end=$total
        local total_pages=$(( (total + page_size - 1) / page_size ))
        local current_page=$(( page_start / page_size + 1 ))

        # Clear and draw
        printf '\033[%d;1H\033[J' "$MENU_START_ROW"
        printf "  ${GOLD}── Modules ──────────────────────────────────────${RST}\n"
        printf "  ${DIM}%-4s %-42s %s${RST}\n" "Num" "Module" "Status"
        printf "  ${GOLD}──────────────────────────────────────────────────${RST}\n"

        local idx
        for (( idx=page_start; idx<page_end; idx++ )); do
            IFS='|' read -r key name url sql_dirs <<< "${available_entries[$idx]}"
            printf "  ${WHITE}%2d)${RST} %-42s %b\n" "$(( idx + 1 ))" "$name" "${markers[$idx]}"
        done

        # Show unregistered modules if space allows
        if [ "$other_rows" -gt 0 ] && [ "${#other_modules[@]}" -gt 0 ]; then
            printf "  ${DIM}──────────────────────────────────────────────────${RST}\n"
            printf "  ${DIM}Other installed:${RST}\n"
            local oi
            for (( oi=0; oi<${#other_modules[@]}; oi++ )); do
                printf "  ${DIM}     %-42s (%s)${RST}\n" "${other_modules[$oi]}" "${other_notes[$oi]}"
            done
        fi

        printf "  ${GOLD}──────────────────────────────────────────────────${RST}\n"
        if [ "$total_pages" -gt 1 ]; then
            local nav="  ${DIM}Page $current_page/$total_pages${RST}"
            [ "$current_page" -gt 1 ]              && nav+="   ${WHITE}< prev${RST}"
            [ "$current_page" -lt "$total_pages" ]  && nav+="   ${WHITE}> next${RST}"
            printf "%b\n" "$nav"
        fi
        printf "  ${WHITE}i <nums>${RST} Install   ${WHITE}r <num>${RST} Remove   ${WHITE}ENTER${RST} Back\n"

        _read_menu_input "$(( tlines - 1 ))"
        local raw_choice="$_MENU_INPUT"

        [ -z "$raw_choice" ] && return

        local action nums
        action="${raw_choice:0:1}"
        nums="${raw_choice:1}"
        nums="${nums# }"

        case "${action,,}" in
            '<')
                page_start=$(( page_start - page_size ))
                [ "$page_start" -lt 0 ] && page_start=0
                ;;
            '>')
                page_start=$(( page_start + page_size ))
                [ "$page_start" -gt "$max_start" ] && page_start=$max_start
                ;;
            i)
                if [ -z "$nums" ]; then
                    print_warning "Specify numbers — e.g. i 1 3"
                    press_enter; continue
                fi

                if [ "$SERVER_TYPE" != "playerbots" ]; then
                    printf '\033[%d;1H\033[J' "$MENU_START_ROW"
                    print_warning "Module installs on $SERVER_NAME are experimental."
                    print_info "Modules will be cloned but rebuilding is not supported on this install type."
                    print_info "Recommended: reinstall as Playerbots for full module support."
                    echo ""
                    if ! ask_yes_no "Continue anyway?"; then continue; fi
                fi

                local -a to_install=()
                local c
                for c in $nums; do
                    if [[ "$c" =~ ^[0-9]+$ ]] && \
                       [ "$c" -ge 1 ] && [ "$c" -le "$total" ]; then
                        to_install+=("${available_entries[$((c - 1))]}")
                    fi
                done

                if [ "${#to_install[@]}" -eq 0 ]; then
                    print_warning "No valid module numbers — e.g. i 1 3"
                    press_enter; continue
                fi

                printf '\033[%d;1H\033[J' "$MENU_START_ROW"
                for entry in "${to_install[@]}"; do
                    IFS='|' read -r key name url sql_dirs <<< "$entry"
                    module_install "$key" "$name" "$url" "$sql_dirs" || true
                    echo ""
                done
                print_info "Modules cloned and SQL imported."

                if [ "$SERVER_TYPE" = "playerbots" ]; then
                    print_info "Rebuild the worldserver to compile the new modules in."
                    echo ""
                    if ask_yes_no "Rebuild the worldserver now?"; then
                        rebuild_worldserver
                    fi
                else
                    print_info "(Skipping rebuild — not supported on this install type.)"
                fi

                for entry in "${to_install[@]}"; do
                    IFS='|' read -r key name _ _ <<< "$entry"
                    if [ "$key" = "mod-ah-bot" ]; then
                        echo ""
                        print_info "AH Bot installed — configure a bot character?"
                        if ask_yes_no "Configure AH Bot now?"; then configure_ahbot; fi
                    fi
                    if [ "$key" = "mod-ale" ]; then
                        echo ""
                        print_info "ALE requires post-install setup (lua_scripts dir + conf)."
                        if ask_yes_no "Configure ALE now?"; then configure_ale; fi
                    fi
                done
                press_enter
                ;;
            r)
                local rnum; rnum=$(echo "$nums" | tr -d ' ')
                if ! [[ "$rnum" =~ ^[0-9]+$ ]] || \
                   [ "$rnum" -lt 1 ] || [ "$rnum" -gt "$total" ]; then
                    print_warning "Invalid module number — e.g. r2"
                    press_enter; continue
                fi
                IFS='|' read -r key name _ _ <<< "${available_entries[$((rnum - 1))]}"
                if ! module_is_installed "$key"; then
                    print_warning "$name is not installed."
                    press_enter; continue
                fi
                printf '\033[%d;1H\033[J' "$MENU_START_ROW"
                module_remove "$key" "$name"
                if [ "$SERVER_TYPE" = "playerbots" ]; then
                    echo ""
                    print_info "Rebuild needed for module removal to take effect."
                    if ask_yes_no "Rebuild the worldserver now?"; then
                        rebuild_worldserver
                    fi
                fi
                press_enter
                ;;
            *)
                print_warning "Unknown command. Use i <nums>, r <num>, or ENTER."
                press_enter
                ;;
        esac
    done
}

# ─────────────────────────────────────────────────────────────
# FIRST-RUN WELCOME
# ─────────────────────────────────────────────────────────────
# Shown on the very first launch of this manager against an install.
# Drops a marker file so it only displays once per install. The goal
# is to ease first-time-user nerves: explain that read-only menu
# options are safe, that nothing changes unless they explicitly act,
# and that the manager doesn't run anything destructive without asking.
show_first_run_welcome() {
    local marker="$SERVER_DIR/.dml-manager-seen"
    # Returning user: clear the detect_install output and go straight to the menu
    if [ -f "$marker" ]; then
        printf '\033[%d;1H\033[J' "$MENU_START_ROW"
        return 0
    fi

    # New user — show the full welcome screen (use whole alt-screen, no logo)
    printf '\033[r\033[H\033[2J\033[?25h'

    # Detect "this looks fresh" — user-installed modules count.
    # mod-playerbots is bundled with the install so doesn't count.
    local user_module_count=0
    if [ -d "$SERVER_DIR/modules" ]; then
        local d dn
        for d in "$SERVER_DIR/modules"/*/; do
            [ -d "$d" ] || continue
            dn=$(basename "$d")
            [ "$dn" = "mod-playerbots" ] && continue
            user_module_count=$((user_module_count + 1))
        done
    fi

    echo ""
    echo -e "${GOLD}╔══════════════════════════════════════════════════╗${RST}"
    echo -e "${GOLD}║${WHITE}${BOLD}    👋  Welcome to the WoW Module Manager        ${RST}${GOLD}║${RST}"
    echo -e "${GOLD}╚══════════════════════════════════════════════════╝${RST}"
    echo ""
    echo -e "${WHITE}This is your first time running the manager on:${RST}"
    echo -e "  ${CYAN}$SERVER_DIR${RST}"
    echo ""

    if [ "$user_module_count" -eq 0 ]; then
        echo -e "${WHITE}Looks like a ${BOLD}fresh install${RST}${WHITE} — no user-added modules yet.${RST}"
    else
        echo -e "${WHITE}You have ${BOLD}$user_module_count user-added module(s)${RST}${WHITE} already installed.${RST}"
    fi
    echo ""
    echo -e "${WHITE}${BOLD}A few things to know:${RST}"
    echo ""
    echo -e "${GREEN}  ✓${RST} ${WHITE}Nothing changes until you explicitly choose an action.${RST}"
    echo -e "${WHITE}    Option 6 (Server status) and 10 (View logs) are read-only${RST}"
    echo -e "${WHITE}    — safe to poke around and see what your install looks like.${RST}"
    echo ""
    echo -e "${GREEN}  ✓${RST} ${WHITE}You'll be asked before anything destructive.${RST}"
    echo -e "${WHITE}    Adding/removing modules, rebuilding the worldserver, and${RST}"
    echo -e "${WHITE}    the repair function all ask for confirmation first.${RST}"
    echo ""
    echo -e "${GREEN}  ✓${RST} ${WHITE}Adding any module triggers a worldserver rebuild.${RST}"
    echo -e "${WHITE}    On Steam Deck this takes 30-90 minutes. Plug in and${RST}"
    echo -e "${WHITE}    keep the device on a flat surface for airflow.${RST}"
    echo ""
    echo -e "${GREEN}  ✓${RST} ${WHITE}The repair function (option 12) only clears SQL update${RST}"
    echo -e "${WHITE}    tracking rows. It never drops database tables.${RST}"
    echo ""
    if [ "$user_module_count" -eq 0 ]; then
        echo -e "${WHITE}${BOLD}Suggested first steps for a fresh install:${RST}"
        echo -e "${WHITE}  1. Option ${CYAN}6${WHITE} (Server status) — see what containers are running${RST}"
        echo -e "${WHITE}  2. Option ${CYAN}1${WHITE} (Modules) — browse and install modules${RST}"
    else
        echo -e "${WHITE}${BOLD}Useful options for an existing install:${RST}"
        echo -e "${WHITE}  • Option ${CYAN}1${WHITE} (Modules) — browse installed and available modules${RST}"
        echo -e "${WHITE}  • Option ${CYAN}6${WHITE} (Server status) — check container state${RST}"
        echo -e "${WHITE}  • Option ${CYAN}12${WHITE} (Repair) — if ac-db-import is failing${RST}"
    fi
    echo ""
    echo -e "${DIM}This welcome shows once per install. The marker file at${RST}"
    echo -e "${DIM}$marker tracks this.${RST}"
    echo ""
    press_enter

    # Drop the marker — silent failure is OK, the welcome just shows again next time
    touch "$marker" 2>/dev/null || true
    # Restore static logo now that the welcome screen is done
    _setup_screen
    printf '\033[%d;1H\033[J' "$MENU_START_ROW"
}

main_menu() {
    while true; do
        refresh_container_names
        local state_str build_str
        if container_running "$WORLD_CONTAINER"; then
            state_str="${GREEN}● Running${RST}"
        else
            state_str="${DIM}○ Stopped${RST}"
        fi
        if [ "$SERVER_TYPE" = "playerbots" ]; then
            build_str="${GREEN}source${RST}"
        else
            build_str="${YELLOW}prebuilt${RST}"
        fi

        # Clear from menu area downward, then print single-column menu
        printf '\033[%d;1H\033[J' "$MENU_START_ROW"

        printf "  ${WHITE}Server:${RST} ${CYAN}%s${RST}  ${GOLD}✦${RST}  ${WHITE}State:${RST} %b  ${GOLD}✦${RST}  ${WHITE}Build:${RST} %b\n" \
            "$(basename "$SERVER_DIR")" "$state_str" "$build_str"
        printf "\n  ${GOLD}${BOLD}Server Modifications${RST}\n"
        printf "  ${GOLD}──────────────────────────────────────────────────${RST}\n"
        printf "  ${WHITE}1)${RST} Manage Modules\n"
        printf "  ${WHITE}2)${RST} Manage ALE Lua Mods\n"
        printf "  ${WHITE}3)${RST} Configure AH Bot\n"
        printf "  ${WHITE}4)${RST} Configure ALE\n"
        printf "  ${WHITE}5)${RST} Rebuild worldserver\n"
        printf "\n  ${GOLD}${BOLD}Server Controls${RST}\n"
        printf "  ${GOLD}──────────────────────────────────────────────────${RST}\n"
        printf "  ${WHITE}6)${RST} Server status\n"
        printf "  ${WHITE}7)${RST} Start server\n"
        printf "  ${WHITE}8)${RST} Stop server\n"
        printf "  ${WHITE}9)${RST} Restart server\n"
        printf "  ${WHITE}10)${RST} View logs\n"
        printf "  ${WHITE}11)${RST} Attach to console\n"
        printf "  ${WHITE}12)${RST} Repair install state\n"
        printf "  ${GOLD}──────────────────────────────────────────────────${RST}\n"
        printf "  ${GOLD} Q)${RST} Quit\n"

        # Input at second-to-last terminal row so it's always visible
        local _tlines; _tlines=$(tput lines 2>/dev/null || echo 24)
        local _irow=$(( _tlines - 1 ))
        _read_menu_input "$_irow"
        local choice="${_MENU_INPUT,,}"

        case "$choice" in
            1)  menu_modules ;;
            2)  menu_ale_scripts ;;
            3)  configure_ahbot; press_enter ;;
            4)  configure_ale; press_enter ;;
            5)  rebuild_worldserver; press_enter ;;
            6)  server_status; press_enter ;;
            7)  server_start; press_enter ;;
            8)  server_stop; press_enter ;;
            9)  server_restart; press_enter ;;
            10) with_full_screen server_logs ;;
            11) with_full_screen server_attach ;;
            12) repair_install_state; press_enter ;;
            q)  echo ""; print_info "Goodbye!"; exit 0 ;;
        esac
    done
}

# ─────────────────────────────────────────────────────────────
# ENTRYPOINT
# ─────────────────────────────────────────────────────────────
start_logo_animation
detect_install
show_first_run_welcome
main_menu
