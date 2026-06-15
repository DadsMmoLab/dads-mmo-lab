#!/bin/bash
# ============================================================
#  Dad's MMO Lab — WoW Module Manager
#  wow-manage.sh
# ------------------------------------------------------------
#  Mostly written by Baerthe (https://github.com/Baerthe)
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
#    chmod +x wow-manage.sh
#    ./wow-manage.sh
#
#  https://github.com/DadsMmoLab/dads-mmo-lab
# ============================================================

MANAGER_VERSION="2.2.1 - ALE House Edition"

set -o pipefail

RST='\033[0m'; BOLD='\033[1m'
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; WHITE='\033[1;37m'; CYAN='\033[0;36m'
GOLD='\033[38;5;220m'; DIM='\033[2m'

# ─────────────────────────────────────────────────────────────
# SCREEN SETUP & UI HELPERS
# ─────────────────────────────────────────────────────────────
# Layout modes (1-indexed rows):
#
#   Full-logo mode  (_IN_MENU=false, e.g. intro / first-run):
#     Rows 1–9  : 8-line logo + animation   →  MENU_START_ROW=15
#
#   Slim-banner mode  (_IN_MENU=true, i.e. any menu screen):
#     Rows 1–4  : 4-row compact banner      →  MENU_START_ROW=5
#
#   Narrow-fallback mode  (terminal width < 80 cols):
#     Rows 1–5  : minimal header            →  MENU_START_ROW=6
#
MENU_START_ROW=15
_TERM_LINES=24
_TERM_COLS=80
_RESIZE_NEEDED=false
_IN_MENU=false   # true once we enter main_menu; switches to the slim banner
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

_get_term_size() {
    _TERM_LINES=$(tput lines 2>/dev/null || echo 24)
    _TERM_COLS=$(tput cols  2>/dev/null || echo 80)
}

# Lightweight SIGWINCH handler — keeps the trap body minimal to avoid
# interleaved output while other code may be printing.
# Full logo/header redraw is deferred to the next menu loop iteration.
_handle_resize() {
    _RESIZE_NEEDED=true
    _get_term_size
    if [ "$_IN_MENU" = true ]; then
        MENU_START_ROW=5
    elif [ "${_TERM_COLS:-80}" -ge 80 ]; then
        MENU_START_ROW=15
    else
        MENU_START_ROW=6
    fi
    # Update scroll region immediately so new output stays below the header.
    printf '\033[%d;%dr' "$MENU_START_ROW" "$_TERM_LINES" 2>/dev/null || true
}

# Read a menu choice into global _MENU_INPUT.
# Positions the cursor at the given row; backspace/delete work via the terminal's line editing.
_MENU_INPUT=""
_read_menu_input() {
    local _input_row=${1:-$(( ${_TERM_LINES:-24} - 1 ))}
    _MENU_INPUT=""
    printf '\033[%d;3H\033[K' "$_input_row"
    printf "${WHITE}Choice: ${RST}"
    read -r _MENU_INPUT
    local _rc=$?
    if [ $_rc -ne 0 ]; then
        # SIGWINCH interrupts read and sets _RESIZE_NEEDED=true — that is a resize event.
        # Anything else (Ctrl-D, closed stdin) is treated as EOF → callers should exit.
        [ "$_RESIZE_NEEDED" = true ] && return 2   # resize — caller should continue/redraw
        return 3                                    # EOF / broken stdin — caller should exit
    fi
}

_PARSED_INDEX=""
_parse_single_index() {
    local raw="$1" max="$2"
    # Allow surrounding whitespace but reject embedded spaces (e.g. "1 2" must not become "12").
    if ! [[ "$raw" =~ ^[[:space:]]*([0-9]+)[[:space:]]*$ ]]; then
        return 1
    fi
    local idx="${BASH_REMATCH[1]}"
    if [ "$idx" -lt 1 ] || [ "$idx" -gt "$max" ]; then
        return 1
    fi
    _PARSED_INDEX="$idx"
}

# Keep only the $3 (default: 2) most-recent routine backup files matching glob
# $2 inside directory $1.  Safety/pre-restore backups (names containing
# "_pre_restore_") are never touched.
_prune_backup_files() {
    local dir="$1" pattern="$2" keep="${3:-2}"
    local -a files=()
    while IFS= read -r f; do
        [[ "$(basename "$f")" == *_pre_restore_* ]] && continue
        files+=("$f")
    done < <(ls -t "$dir"/$pattern 2>/dev/null)
    local i
    for (( i=keep; i<${#files[@]}; i++ )); do
        rm -f "${files[$i]}"
    done
}

# Returns 0 if running on a Steam Deck (SteamOS).
_is_steam_deck() {
    grep -qi 'ID.*steamos\|ID_LIKE.*steamos' /etc/os-release 2>/dev/null
}

# Open a text file for editing.
# On Steam Deck: nano has no Ctrl key to exit, so show the path and offer Kate.
# Elsewhere: open nano directly.
_open_text_file() {
    local filepath="$1"
    if _is_steam_deck; then
        echo ""
        print_info "File location:"
        echo -e "  ${CYAN}${filepath}${RST}"
        echo ""
        if command -v kate &>/dev/null; then
            if ask_yes_no "Open in Kate (graphical text editor)?"; then
                kate "$filepath" &>/dev/null &
            fi
        else
            print_info "Open this file with your preferred text editor."
        fi
    else
        nano "$filepath"
    fi
}

_screen_int_handler() {
    if [ "$_ALLOW_INT_EXIT" = true ]; then
        printf '\033[r\033[?1049l\033[?25h'
        exit 0
    fi
    # Inside with_full_screen: SIGINT already killed the foreground child — just return
}

_screen_term_handler() {
    # SIGTERM always restores the terminal and exits cleanly.
    printf '\033[r\033[?1049l\033[?25h'
    exit 0
}

# Draw the logo + subtitle bar statically (full clear + redraw).
_draw_logo_static() {
    printf '\033[1;1H\033[J'
    tput rmam 2>/dev/null || true  # disable line wrap — logo/bars truncate cleanly on narrow terminals
    if [ "$_IN_MENU" = true ]; then
        # Slim in-menu banner: 4 rows → MENU_START_ROW=5.
        # Keeps vertical space for menu content on short terminals (e.g. Steam Deck 30 rows).
        printf '\n'
        printf '\033[38;5;220m ════════════════════════════════════════════════════════════════════════════════\033[K\033[0m\n'
        printf '   \033[38;5;220m⚔︎\033[0m  \033[1mDad'"'"'s MMO Lab\033[0m  \033[2m✦  WotLK Server Manager  ✦  v%s\033[0m\033[K\n' "$MANAGER_VERSION"
        printf '\033[38;5;220m ════════════════════════════════════════════════════════════════════════════════\033[K\033[0m\n'
    elif [ "${_TERM_COLS:-80}" -ge 80 ]; then
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
        printf '   \033[2m⚔︎ WotLK Mod and Server Manager\033[0m  ✦  \033[2mv%s\033[0m\033[K\n' "$MANAGER_VERSION"
        printf '\033[38;5;220m ══════════════════════════════════════════════════════════════════════════════════\033[K\033[0m\n'
        printf '\n'
    else
        # Compact header for narrow terminals (< 80 cols) — fits in 5 rows (MENU_START_ROW=6).
        printf '\n'
        printf '\033[38;5;220m ══ Dad'"'"'s MMO Lab ══\033[K\033[0m\n'
        printf '   \033[2m⚔︎ WoW Mgr\033[0m  v%s\033[K\n' "$MANAGER_VERSION"
        printf '\033[38;5;220m ═══════════════════\033[K\033[0m\n'
        printf '\n'
    fi
    tput smam 2>/dev/null || true  # re-enable line wrap
}

# Enter alt screen buffer, set scroll region, draw static logo.
# Safe to call multiple times (idempotent for alt-screen entry).
_setup_screen() {
    if ! $_IN_ALT_SCREEN; then
        printf '\033[?1049h'
        _IN_ALT_SCREEN=true
    fi
    _get_term_size
    if [ "$_IN_MENU" = true ]; then
        MENU_START_ROW=5
    elif [ "${_TERM_COLS:-80}" -ge 80 ]; then
        MENU_START_ROW=15
    else
        MENU_START_ROW=6
    fi
    printf '\033[%d;%dr' "$MENU_START_ROW" "$_TERM_LINES"
    printf '\033[?25l'
    _draw_logo_static
    printf '\033[?25h'
}

# Plays the animated intro splash (up to 3 seconds, any key skips),
# then freezes the logo statically for the rest of the session.
start_logo_animation() {
    _setup_screen
    trap 'tput smam 2>/dev/null || true; printf "\033[r\033[?1049l\033[?25h"' EXIT
    trap '_screen_int_handler' INT
    trap '_screen_term_handler' TERM
    trap '_handle_resize' WINCH

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
    print_header
}

# Clear screen, run a function, restore the static logo + scroll region.
# _ALLOW_INT_EXIT=false lets Ctrl+C kill the child but return to the menu.
# Usage: with_full_screen <function_name> [args...]
with_full_screen() {
    printf '\033[r\033[H\033[2J\033[?25h'
    _ALLOW_INT_EXIT=false
    trap '' WINCH  # suppress resize redraws while a full-screen child is running
    "$@"
    trap '_handle_resize' WINCH
    _ALLOW_INT_EXIT=true
    _setup_screen
    print_header
}

print_header() {
    # Move to the menu content row and clear everything below.
    printf '\033[%d;1H\033[J' "$MENU_START_ROW"
}

print_install_info() {
    refresh_container_names
    local state_str build_str _client_str
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
    printf "  ${WHITE}Server:${RST} ${CYAN}%s${RST}  ${GOLD}✦${RST}  ${WHITE}State:${RST} %b  ${GOLD}✦${RST}  ${WHITE}Build:${RST} %b\n" \
        "$(basename "$SERVER_DIR")" "$state_str" "$build_str"
    local _client_cache="$SERVER_DIR/.wow_client_dir"
    if [ -n "$WOW_CLIENT_DIR" ]; then
        _client_str="${GREEN}● Set${RST}  ${DIM}$(basename "$WOW_CLIENT_DIR")${RST}"
    elif [ -f "$_client_cache" ] && [ -d "$(cat "$_client_cache")" ]; then
        WOW_CLIENT_DIR=$(cat "$_client_cache")
        _client_str="${GREEN}● Set${RST}  ${DIM}$(basename "$WOW_CLIENT_DIR")${RST}"
    else
        _client_str="${DIM}○ Not set${RST}"
    fi
    printf "  ${WHITE}WoW Client:${RST} %b  ${GOLD}✦${RST}  ${WHITE}Version:${RST} ${DIM}WotLK 3.3.5a${RST}\n" "$_client_str"
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
# _offer_npc_in_capitals  npc_entry  npc_name  [timing_note]
#   Shows GM commands (in-game and console forms) to spawn an NPC
#   in Stormwind and Orgrimmar.  Uses per-entry deterministic offsets
#   so repeated calls never stack NPCs on the same coordinates.
#   NPC spawn commands are recorded to ingame-commands.txt on mod install.
#   timing_note: extra line shown before coordinates (e.g. "run after rebuild")
# ─────────────────────────────────────────────────────────────
_offer_npc_in_capitals() {
    local npc_entry="$1"
    local npc_name="$2"
    local timing_note="${3:-}"

    # Deterministic slot per known entry; unknown entries use session counter
    local slot
    case "$npc_entry" in
        190010)  slot=0 ;;
        999991)  slot=1 ;;
        601026)  slot=2 ;;
        *)       slot="${_NPC_SPAWN_IDX:-0}" ;;
    esac
    _NPC_SPAWN_IDX=$((_NPC_SPAWN_IDX + 1))

    # Offset x by +3 and y by +2 per slot to prevent NPC overlap
    local sw_x sw_y og_x og_y
    sw_x=$(LC_ALL=C awk -v s="$slot" 'BEGIN{printf "%.1f", -8831.3 + s*3}')
    sw_y=$(LC_ALL=C awk -v s="$slot" 'BEGIN{printf "%.1f",   628.2 + s*2}')
    og_x=$(LC_ALL=C awk -v s="$slot" 'BEGIN{printf "%.1f",  1597.2 + s*3}')
    og_y=$(LC_ALL=C awk -v s="$slot" 'BEGIN{printf "%.1f", -4415.7 + s*2}')

    echo ""
    print_info "📍 $npc_name (entry $npc_entry) is in the database but not yet placed in the world."
    [ -n "$timing_note" ] && print_info "   $timing_note"
    echo ""
    echo -e "  ${GOLD}Stormwind, Alliance (map 0):${RST}"
    echo -e "  ${WHITE}In-game GM:${RST}  ${CYAN}.npc add $npc_entry 0 $sw_x $sw_y 94.1 3.7${RST}"
    echo -e "  ${WHITE}WS console:${RST}  ${CYAN}npc add $npc_entry 0 $sw_x $sw_y 94.1 3.7${RST}"
    echo ""
    echo -e "  ${GOLD}Orgrimmar, Horde (map 1):${RST}"
    echo -e "  ${WHITE}In-game GM:${RST}  ${CYAN}.npc add $npc_entry 1 $og_x $og_y 17.5 4.5${RST}"
    echo -e "  ${WHITE}WS console:${RST}  ${CYAN}npc add $npc_entry 1 $og_x $og_y 17.5 4.5${RST}"
    echo ""
    print_info "Access the worldserver console via option 13 from the main menu."
    print_info "If the NPC lands in a bad spot, use .npc delete (in-game) or"
    print_info "npc delete (console) then re-place with your own coordinates."
    echo ""
}

# ─────────────────────────────────────────────────────────────
# IN-GAME COMMANDS FILE HELPERS
#   _cmd_block_for KEY      — print static command data block for a mod key
#   upsert_mod_commands KEY — write/replace === key === section in ingame-commands.txt
#   remove_mod_commands KEY — remove === key === section from ingame-commands.txt
#   show_ingame_commands    — display ingame-commands.txt with colour formatting
# ─────────────────────────────────────────────────────────────

_cmd_block_for() {
    local key="$1"
    case "$key" in
        mod-1v1-arena)
            printf '%s\n' \
                '1v1 Arena' \
                'Private one-on-one arena duels. Players can queue from anywhere, challenge others, and track wins/losses. Fully automatic — requires no GM setup beyond placing the Battlemaster NPC.' \
                '' \
                'Commands:' \
                '.q1v1 rated              — Queue for a rated 1v1 arena match' \
                '.q1v1 unrated            — Queue for an unrated 1v1 arena match' \
                '.q1v1 stats              — View your personal 1v1 win/loss statistics' \
                '' \
                'NPC Spawn Commands (worldserver console; prefix with . for in-game GM):' \
                'npc add 999991 0 -8828.3 630.2 94.1 3.7   — Stormwind Arena Battlemaster (Alliance)' \
                'npc add 999991 1 1600.2 -4413.7 17.5 4.5  — Orgrimmar Arena Battlemaster (Horde)'
            ;;
        mod-arac)
            printf '%s\n' \
                'All Races All Classes (ARAC)' \
                'Unlocks all race/class combinations not normally available — Night Elf Warrior, Undead Paladin, etc. DATA-ONLY: no worldserver rebuild required. Requires a world DB SQL import and updated DBC files on both server and client.' \
                '' \
                'Install requires three steps (configure handles all automatically):' \
                '  1. Apply arac.sql to acore_world database' \
                '  2. Copy DBFilesContent DBC files to server data/dbc/ directory' \
                '  3. Copy Patch-A.MPQ to WoW client Data/ directory' \
                '' \
                'WARNING: Back up your database before applying ARAC SQL.' \
                'Commands: (none — all race/class combos unlocked at character creation)'
            ;;
        mod-dungeon-master)
            printf '%s\n' \
                'Dungeon Master' \
                'Procedural roguelike dungeon challenge system. Talk to the Dungeon Master NPC (entry 500000), pick a difficulty tier (Novice → Grandmaster), creature theme, and dungeon — then enter a repopulated instance scaled to your level. Roguelike mode chains dungeons with escalating difficulty, Mythic+-style affixes, and stacking stat buffs.' \
                '' \
                '37 dungeons, 9 creature themes, 6 difficulty tiers, party + solo support.' \
                'NPC auto-spawns in all major cities on server start. SQL auto-applied on next start.' \
                '' \
                'GM Commands:' \
                '[GM] .dm reload           — Reload Dungeon Master config' \
                '[GM] .dm status           — Show active dungeon sessions' \
                '[GM] .dm list             — List available dungeons' \
                '[GM] .dm end              — Force-end active session' \
                '[GM] .dm clearcooldown    — Clear per-character run cooldown' \
                '' \
                'NPC Spawn Commands (worldserver console; prefix with . for in-game GM):' \
                'npc add 500000            — Dungeon Master NPC (auto-placed in all capitals at start)'
            ;;
        mod-ah-bot)
            printf '%s\n' \
                'Auction House Bot' \
                'Populates the Auction House with NPC-driven listings so players always have items to buy and sell. Configurable pricing, quantity, and categories. Requires a dedicated bot character.' \
                '' \
                'Commands (GM Rank 3+ only):' \
                '[GM] .ahbotoptions minprice <n>     — Set minimum item price' \
                '[GM] .ahbotoptions maxprice <n>     — Set maximum item price' \
                '[GM] .ahbotoptions mintime <n>      — Set minimum auction duration (hours)' \
                '[GM] .ahbotoptions maxtime <n>      — Set maximum auction duration (hours)' \
                '[GM] .ahbotoptions minbidprice <n>  — Minimum bid price multiplier' \
                '[GM] .ahbotoptions maxbidprice <n>  — Maximum bid price multiplier' \
                '[GM] .ahbotoptions maxstack <n>     — Max stack size per listing' \
                '[GM] .ahbotoptions minitem <n>      — Minimum different items in AH' \
                '[GM] .ahbotoptions maxitem <n>      — Maximum different items in AH' \
                '[GM] .ahbotoptions buyprice <n>     — Buyout price multiplier' \
                '[GM] .ahbotoptions seller <n>       — Enable/disable bot as seller (1/0)' \
                '[GM] .ahbotoptions buyer <n>        — Enable/disable bot as buyer (1/0)' \
                '[GM] .ahbotoptions allfaction <n>   — Apply to all factions (1/0)' \
                '[GM] .ahbotoptions alliance <n>     — Configure Alliance AH separately' \
                '[GM] .ahbotoptions horde <n>        — Configure Horde AH separately'
            ;;
        mod-autobalance)
            printf '%s\n' \
                'AutoBalance' \
                'Dynamically scales dungeon and raid difficulty based on player count, so solo or small-group play is viable. Supports per-dungeon overrides and level scaling.' \
                '' \
                'Commands (GM only):' \
                '[GM] .autobalance getoffset     — Get difficulty offset for current map' \
                '[GM] .autobalance setoffset <n> — Set difficulty offset for current map' \
                '[GM] .autobalance mapstat       — Show AutoBalance stats for current map' \
                '[GM] .autobalance creaturestat  — Show AutoBalance stats for target creature' \
                '' \
                'Aliases: .ab getoffset / .ab setoffset / .ab mapstat / .ab creaturestat'
            ;;
        mod-challenge-modes)
            printf '%s\n' \
                'Challenge Modes' \
                'Adds optional self-imposed difficulty rules — Hardcore (death = permadeath), Semi-Hardcore, Ironman, and more. Players opt-in via an NPC (Shrine of Challenge) or the game settings menu. Requires EnablePlayerSettings = 1 in worldserver.conf.' \
                '' \
                'Source: nl-saw fork — OnPlayerResurrect signature patched automatically on install.' \
                '' \
                'Commands: (none — all functionality is accessed through the Shrine of Challenge NPC or in-game Settings menu)' \
                '' \
                'Configuration: edit challenge_modes.conf after a worldserver rebuild.'
            ;;
        mod-solocraft)
            printf '%s\n' \
                'SoloCraft' \
                'Automatically buffs solo players in group content (dungeons, raids) to make progression viable. Scales stats dynamically — fully automatic, no player commands needed.' \
                '' \
                'Commands: (none — fully automatic)'
            ;;
        mod-transmog)
            printf '%s\n' \
                'Transmogrification' \
                'Lets players change the visual appearance of gear without changing stats. Requires a Transmogrifier NPC placed in the world (entry 190010). Optionally portable and/or toggleable via commands.' \
                '' \
                'Commands:' \
                '.transmog                — Show current transmog status' \
                '.transmog sync           — Sync transmog visuals' \
                '.transmog portable       — Toggle portable transmog (interact from anywhere)' \
                '.transmog interface      — Open transmog UI without the NPC' \
                '.transmog disclaimer     — Show transmog usage disclaimer' \
                '[GM] .transmog add <id>  — Add item to transmog collection by item ID' \
                '[GM] .transmog check     — Check transmog database integrity' \
                '[GM] .transmog reload    — Reload transmog script' \
                '' \
                'NPC Spawn Commands (worldserver console; prefix with . for in-game GM):' \
                'npc add 190010 0 -8831.3 628.2 94.1 3.7   — Transmogrifier NPC, Stormwind (Alliance)' \
                'npc add 190010 1 1597.2 -4415.7 17.5 4.5  — Transmogrifier NPC, Orgrimmar (Horde)'
            ;;
        mod-talentbutton)
            printf '%s\n' \
                'Talent Button' \
                'Enables Dual Talent Specialization at level 10 (retail: level 40). Adds a button to unlearn talents from anywhere in the world — no class trainer visit required. Uses server-side script injection to provide seamless in-game UI integration.' \
                '' \
                'IMPORTANT: Requires an UNPATCHED WoW 3.3.5a client. Clients patched with tools' \
                'like RCEPatcher will block the script injection and the button will not appear.' \
                '' \
                'Configuration: TalentButton.Enable = 1 in mod_talentbutton.conf' \
                '' \
                'Commands: (none — talent reset button available in the in-game talent UI)'
            ;;
        mod-individual-progression)
            printf '%s\n' \
                'Individual Progression' \
                'NOTICE: This module repository could not be verified on GitHub — installation may fail.' \
                'Tracks each player'"'"'s individual content progression, unlocking new tiers as they complete content.' \
                '' \
                'Commands: (check module conf after install if the module is available)'
            ;;
        mod-player-bot-level-brackets)
            printf '%s\n' \
                'Player Bot Level Brackets' \
                'Restricts AI PlayerBots to operate within configured level brackets, preventing high-level bots from trivialising lower-level content. Requires the Playerbots module.' \
                '' \
                'Commands:' \
                '[ADMIN] .reload          — Reload server scripts (applies bracket config changes without restart)'
            ;;
        mod-npc-beastmaster)
            printf '%s\n' \
                'NPC Beastmaster' \
                'Adds a Beastmaster NPC that allows Hunters to tame any pet, including exotic and normally-untameable creatures. Can be summoned anywhere via .beastmaster or placed permanently in capitals (entry 601026).' \
                '' \
                'Commands:' \
                '.beastmaster             — Summon the Beastmaster NPC to your current location' \
                '.petname rename <name>   — Rename your current pet' \
                '.petname cancel          — Cancel a pending pet rename' \
                '' \
                'NPC Spawn Commands (worldserver console; prefix with . for in-game GM):' \
                'npc add 601026 0 -8825.3 632.2 94.1 3.7   — White Fang (Beastmaster), Stormwind (Alliance)' \
                'npc add 601026 1 1603.2 -4411.7 17.5 4.5  — White Fang (Beastmaster), Orgrimmar (Horde)'
            ;;
        mod-quest-loot-party)
            printf '%s\n' \
                'Quest Loot Party' \
                'Distributes quest item loot to ALL eligible party members when any one member' \
                'loots the item — eliminating repeated boss kills when questing in a group.' \
                '' \
                'Configuration: QuestParty.Enable (on/off), QuestParty.Message (notify players)' \
                '' \
                'Commands: (none — fully automatic)'
            ;;
        mod-aoe-loot)
            printf '%s\n' \
                'AoE Loot' \
                'Allows players to loot all nearby corpses simultaneously with a single loot action. Toggleable per-player. Significantly speeds up grinding and farming. No GM configuration needed.' \
                '' \
                'Commands:' \
                '.aoeloot on              — Enable AoE looting for yourself' \
                '.aoeloot off             — Disable AoE looting for yourself'
            ;;
        mod-learn-spells)
            printf '%s\n' \
                'Learn Spells on Level Up' \
                'Automatically teaches players all class spells when they level up, eliminating the need to visit trainers. Configurable to include/exclude specific spell types.' \
                '' \
                'Commands: (none — fully automatic on level-up)'
            ;;
        mod-junk-to-gold)
            printf '%s\n' \
                'Junk to Gold' \
                'Automatically sells all grey (junk) items in a player'"'"'s bags when they interact with any vendor. Saves time and removes inventory clutter without manual selling.' \
                '' \
                'Commands: (none — automatic when visiting any vendor)'
            ;;
        battlepass)
            printf '%s\n' \
                'Battle Pass (ALE)' \
                'A complete seasonal XP progression system. Players earn XP from kills, quests,' \
                'PvP, dungeons, and daily logins, then claim rewards (items, gold, titles, spells).' \
                'Requires the Battle Pass NPC (entry 90100) placed in the world and the client' \
                'addon installed in WoW Interface/AddOns/BattlePass/.' \
                '' \
                'Commands:' \
                '.bp                      — Show your current Battle Pass progress' \
                '.bp rewards              — List available rewards' \
                '.bp claim <level>        — Claim reward for a specific level' \
                '.bp claimall             — Claim all currently available rewards' \
                '.bp preview [level]      — Preview upcoming rewards' \
                '[GM] .bpadmin addxp <amount> [player]   — Grant XP to a player' \
                '[GM] .bpadmin setlevel <level> [player] — Set a player'"'"'s level' \
                '[GM] .bpadmin unclaim <level> [player]  — Un-claim a reward' \
                '[GM] .bpadmin reset [player]            — Reset a player'"'"'s progress' \
                '[GM] .bpadmin reload                    — Reload Battle Pass config' \
                '[GM] .bpadmin stats                     — Show server-wide stats' \
                '' \
                'NPC Spawn Commands (worldserver console; prefix with . for in-game GM):' \
                'npc add 90100 0 -8819.3 636.2 94.1 3.7   — Battle Pass NPC, Stormwind (Alliance)' \
                'npc add 90100 1 1609.2 -4407.7 17.5 4.5  — Battle Pass NPC, Orgrimmar (Horde)'
            ;;
        paragon)
            printf '%s\n' \
                'Paragon Anniversary (ALE)' \
                'A Paragon reputation and anniversary reward system. Players earn paragon points via reputation grinds and receive bonus rewards on server anniversary dates.' \
                '' \
                'Commands:' \
                '.test                    — Debug command (WARNING: no GM guard — any player can use this; appears to be a debug leftover in source code; monitor for abuse)'
            ;;
        bmah)
            printf '%s\n' \
                'Black Market Auction House (ALE)' \
                'Adds a Black Market Auction House NPC that lists rare and exclusive items at auction. Requires a companion client addon (AzerothCore-wotlk-client-modifications). NPC entry: 2069430.' \
                '' \
                'Commands: (none — all interaction is through the NPC gossip menu)' \
                '' \
                'NPC Spawn Commands (worldserver console; prefix with . for in-game GM):' \
                'npc add 2069430 0 -8816.3 638.2 94.1 3.7   — Black Market AH Auctioneer, Stormwind (Alliance)' \
                'npc add 2069430 1 1612.2 -4405.7 17.5 4.5  — Black Market AH Auctioneer, Orgrimmar (Horde)'
            ;;
        lootpet)
            printf '%s\n' \
                'Loot Pet (ALE)' \
                'Summons a companion pet that automatically picks up nearby loot. Combines well with AoE Loot. Fully automatic once the Lua script is deployed. Uses creature entry 34587 internally.' \
                '' \
                'Commands: (none — the pet is summoned and loots automatically)'
            ;;
        accountwide)
            printf '%s\n' \
                'Account Wide (ALE)' \
                'Synchronises playtime, reputation, achievements, currencies, and other data across all characters on the same account. Requires a characters DB schema. Individual systems are enabled per-config.' \
                '' \
                'Commands:' \
                '.playtime                — Show total account-wide play time' \
                '.played                  — Show account-wide played time' \
                '.awplaytime              — Show account-wide play time (primary alias)' \
                '.accountplaytime         — Show account-wide play time (alternate alias)' \
                '.awplayed                — Show account-wide played time (alternate alias)'
            ;;
        activechat)
            printf '%s\n' \
                'Azeroth Chatter (ALE)' \
                'Fills world chat with ambient, lore-grounded RP chatter from a roster of recurring named residents — each with a faction, role, and personality. Time-of-day, seasonal, and event-aware. Far richer than the original ActiveChat.' \
                '' \
                'Commands: (none — chat fires on server timers automatically)'
            ;;
        levelupreward)
            printf '%s\n' \
                'Level Up Reward (ALE)' \
                'Awards a random class-appropriate equippable item on every level-up. Quality is rolled (10% epic / 25% rare / 65% uncommon) with a fallback chain. Armor type scales with level (e.g. Mail → Plate at 40). First level-up also teaches all class weapon proficiencies.' \
                '' \
                'Commands: (none — reward is granted automatically on level-up)'
            ;;
        sod)
            printf '%s\n' \
                'Season of Discovery Buff (ALE)' \
                'Tiered Discoverer'"'"'s Delight XP bonus that scales down as players level. Awards +300% at levels 1-10, stepping down to +50% at 71-79. Level 80 gets no buff. Auto-updates on level-up. Requires server DBC files and client MPQ patches — installed automatically. Sourced from the Dad'"'"'s MMO Lab ALE-Kegs collection.' \
                '' \
                'Files required: Server Files/dbc/*.dbc (server), Client Files/data/Patch-Z.MPQ + enUS/patch-enUS-3.MPQ + Interface/Icons/Buff_SoD.blp (client)' \
                '' \
                'Commands: (none — buff applied automatically on login and level-up)'
            ;;
        sitmeanrest)
            printf '%s\n' \
                'Sit Means Rest (ALE)' \
                'Automatically applies the Rested XP bonus whenever a player sits down, mimicking inn-style resting anywhere. Duration and regen spell are configurable.' \
                '' \
                'Commands: (none — triggered automatically by the /sit emote or sitting action)'
            ;;
        unlimitedammo)
            printf '%s\n' \
                'Unlimited Ammo (ALE)' \
                'Allows Hunters and other ranged classes to shoot without consuming ammo. Toggle is per-session. Ships with ENABLED = false and must be configured to activate.' \
                '' \
                'Commands:' \
                '.ua                      — Enable unlimited ammo for yourself (current session only; no .ua off)'
            ;;
        portals-capitals)
            printf '%s\n' \
                'Portals in All Capitals (SQL Mod)' \
                'Adds portal game objects to every faction capital city, allowing quick travel between Stormwind, Orgrimmar, and other capitals without a Mage.' \
                '' \
                'Commands: (none — portals are world objects; interact with them directly in game)'
            ;;
        hearthstone-cd)
            printf '%s\n' \
                'Hearthstone Cooldown (SQL Mod)' \
                'Reduces the Hearthstone cooldown from the WotLK default of 30 minutes. Options: 1 second, 1 minute, 5 minutes, 15 minutes, or 30 minutes (WotLK default).' \
                '' \
                'Commands: (none — takes effect after a server restart or .reload spells in-game)'
            ;;
        rare-drops)
            printf '%s\n' \
                'Rare Drops (SQL Mod)' \
                'Increases the drop rates of rare-quality items from mobs and bosses, making gear progression feel more rewarding for private servers.' \
                '' \
                'Commands: (none — passive database modification, no restart needed)'
            ;;
        lvl1-mounts)
            printf '%s\n' \
                'Level 1 Mounts (SQL Mod)' \
                'Lowers the level requirement on all mounts to level 1, so players can ride from the very start of the game. No restart needed.' \
                '' \
                'Commands: (none — passive database change, effective immediately)'
            ;;
        all-stackables)
            printf '%s\n' \
                'All Stackables (SQL Mod)' \
                'Increases the maximum stack size on all stackable items (potions, reagents, trade goods, etc.) to a configurable amount (default 200).' \
                '' \
                'Commands: (none — passive database change, takes effect after server restart)'
            ;;
        buff-mobs)
            printf '%s\n' \
                'Buff Mobs (SQL Mod)' \
                'Increases all creature HP, damage, armor, and attack speed (HP×2, DMG×1.5, ARM×1.5) for a more challenging experience. Mutually exclusive with Nerf Mobs, XBuff Mobs, and Baby Mobs.' \
                '' \
                'Commands: (none — database multipliers applied to creature_template)'
            ;;
        xbuff-mobs)
            printf '%s\n' \
                'XBuff Mobs — Extreme Difficulty (SQL Mod)' \
                'Significantly increases all creature stats (HP×4, DMG×2, ARM×2). The hardest mob difficulty option. Mutually exclusive with all other mob tweak SQL mods.' \
                '' \
                'Commands: (none — database multipliers applied to creature_template)'
            ;;
        nerf-mobs)
            printf '%s\n' \
                'Nerf Mobs (SQL Mod)' \
                'Reduces all creature stats (HP×0.5, DMG×0.75, ARM×0.75) for an easier experience. Mutually exclusive with Buff Mobs, XBuff Mobs, and Baby Mobs.' \
                '' \
                'Commands: (none — database multipliers applied to creature_template)'
            ;;
        baby-mobs)
            printf '%s\n' \
                'Baby Mobs — Trivial Difficulty (SQL Mod)' \
                'Drastically reduces all creature stats (HP×0.25, DMG×0.25, ARM×0.25). Best for testing or casual play. Mutually exclusive with all other mob tweak SQL mods.' \
                '' \
                'Commands: (none — database multipliers applied to creature_template)'
            ;;
        npc-teleporter)
            printf '%s\n' \
                'NPC Teleporter (SQL Mod)' \
                'Adds a teleporter NPC to capital cities and/or starting zones, allowing players to fast-travel to major locations including raids and dungeons. ONY_LEVEL controls Onyxia level requirement.' \
                '' \
                'Commands: (none — all interaction through the NPC gossip teleport menu)'
            ;;
        xp-rates)
            printf '%s\n' \
                'XP Rates (SQL Mod)' \
                'Adjusts kill, quest, and exploration XP multipliers in worldserver.conf. Requires a reload config or server restart to apply changes.' \
                '' \
                'Commands: (none — edits worldserver.conf; use .reload config in-game to apply without a full restart)'
            ;;
        mod-custom-login)
            printf '%s\n' \
                'Custom Login (SQL Mod / Module)' \
                'Gives new characters starter items, spells, or buffs on first login. Configurable via mod_customlogin.conf.' \
                '' \
                'Commands: (none — triggers automatically on first character login)'
            ;;
        mod-ale)
            printf '%s\n' \
                'AzerothCore Lua Engine (ALE)' \
                'The core C++ module that enables Lua scripting on AzerothCore. Required by all ALE Lua Mods. Exposes server events to Lua scripts placed in the lua_scripts/ directory.' \
                '' \
                'Commands:' \
                '[GM] .reload ale         — Reload all Lua scripts without restarting the worldserver'
            ;;
    esac
}

# Rebuild the "=== NPC Spawn Quick Reference ===" block at the top of
# INGAME_COMMANDS_FILE by scanning all mod sections for "npc add" lines.
# Uses [mod-key] labels (not === markers) to avoid confusing the section parser.
_rebuild_npc_spawn_header() {
    local outfile="$INGAME_COMMANDS_FILE"
    [ -z "$outfile" ] || [ ! -f "$outfile" ] && return 0

    local header_marker="=== NPC Spawn Quick Reference ==="
    local -a spawn_entries=()
    local current_section="" in_skip=false

    while IFS= read -r line; do
        if [[ "$line" =~ ^"=== "(.+)" ===" ]]; then
            local sec="${BASH_REMATCH[1]}"
            if [ "$sec" = "NPC Spawn Quick Reference" ]; then
                in_skip=true
                current_section=""
            else
                in_skip=false
                current_section="$sec"
            fi
        elif [ "$in_skip" = false ] && [[ "$line" == "npc add "* ]]; then
            spawn_entries+=("${current_section}|${line}")
        fi
    done < "$outfile"

    if [ ${#spawn_entries[@]} -eq 0 ]; then
        # No spawn commands — remove the header block if present
        if grep -Fxq "$header_marker" "$outfile" 2>/dev/null; then
            local tmpout; tmpout=$(mktemp)
            awk -v marker="$header_marker" '
            $0 == marker { skip=1; next }
            skip && /^=== .+ ===$/ { skip=0 }
            !skip { print }
            ' "$outfile" > "$tmpout" && mv "$tmpout" "$outfile"
        fi
        return 0
    fi

    # Build the new header block
    local tmpblock; tmpblock=$(mktemp)
    {
        printf '%s\n' "$header_marker"
        printf '%s\n' "Consolidated NPC spawn commands for all installed mods that require NPCs."
        printf '%s\n' "  Worldserver console: npc add <entry> ...   |   In-game GM: .npc add <entry> ..."
        printf '\n'
        local prev_section=""
        for entry in "${spawn_entries[@]}"; do
            local sec="${entry%%|*}"
            local cmd="${entry#*|}"
            if [ "$sec" != "$prev_section" ]; then
                [ -n "$prev_section" ] && printf '\n'
                printf '  [%s]\n' "$sec"
                prev_section="$sec"
            fi
            printf '%s\n' "  $cmd"
        done
        printf '\n'
    } > "$tmpblock"

    # Place at the very top of the file (replace if header exists, otherwise prepend)
    local tmpout; tmpout=$(mktemp)
    if grep -Fxq "$header_marker" "$outfile" 2>/dev/null; then
        awk -v marker="$header_marker" -v newfile="$tmpblock" '
        $0 == marker {
            while ((getline line < newfile) > 0) print line
            close(newfile)
            skip=1; next
        }
        skip && /^=== .+ ===$/ { skip=0 }
        !skip { print }
        ' "$outfile" > "$tmpout" && mv "$tmpout" "$outfile"
    else
        cat "$tmpblock" "$outfile" > "$tmpout" && mv "$tmpout" "$outfile"
    fi
    rm -f "$tmpblock"
}

# Write or replace the === key === section in INGAME_COMMANDS_FILE.
# Uses unique temp files + atomic mv to avoid partial writes.
# Pass --quiet as second argument to suppress the print_info notification.
upsert_mod_commands() {
    local key="$1" quiet="${2:-}"
    local content; content=$(_cmd_block_for "$key")
    [ -z "$content" ] && return 0

    local outfile="$INGAME_COMMANDS_FILE"
    [ -z "$outfile" ] && return 0
    local marker="=== ${key} ==="

    # Each section ends with a trailing blank line for readability
    local tmpblock; tmpblock=$(mktemp)
    printf '%s\n%s\n\n' "$marker" "$content" > "$tmpblock"

    if [ ! -f "$outfile" ]; then
        mv "$tmpblock" "$outfile"
        _rebuild_npc_spawn_header
        [ "$quiet" != "--quiet" ] && print_info "📋 In-game commands reference created: $outfile"
        return 0
    fi

    if grep -Fxq "$marker" "$outfile" 2>/dev/null; then
        local tmpout; tmpout=$(mktemp)
        awk -v marker="$marker" -v newfile="$tmpblock" '
        $0 == marker {
            while ((getline line < newfile) > 0) print line
            close(newfile)
            skip=1; next
        }
        skip && /^=== .+ ===$/ { skip=0 }
        !skip { print }
        ' "$outfile" > "$tmpout" && mv "$tmpout" "$outfile"
    else
        cat "$tmpblock" >> "$outfile"
    fi
    rm -f "$tmpblock"
    _rebuild_npc_spawn_header
    [ "$quiet" != "--quiet" ] && print_info "📋 In-game commands reference updated: $outfile"
}

# Remove the === key === section from INGAME_COMMANDS_FILE.
remove_mod_commands() {
    local key="$1"
    local outfile="$INGAME_COMMANDS_FILE"
    [ -z "$outfile" ] || [ ! -f "$outfile" ] && return 0
    local marker="=== ${key} ==="
    grep -Fxq "$marker" "$outfile" 2>/dev/null || return 0

    local tmpout; tmpout=$(mktemp)
    awk -v marker="$marker" '
    $0 == marker { skip=1; next }
    skip && /^=== .+ ===$/ { skip=0 }
    !skip { print }
    ' "$outfile" > "$tmpout" && mv "$tmpout" "$outfile"
    _rebuild_npc_spawn_header
    print_info "📋 Removed $key from in-game commands reference."
}

# Print INGAME_COMMANDS_FILE to the terminal with basic colour formatting.
show_ingame_commands() {
    local outfile="$INGAME_COMMANDS_FILE"
    print_header
    if [ -z "$outfile" ] || [ ! -f "$outfile" ] || [ ! -s "$outfile" ]; then
        echo ""
        print_info "No in-game commands recorded yet."
        print_info "Install any mod (Modules, ALE Lua Mods, or SQL Mods) to populate this file."
        echo ""
        press_enter
        return
    fi

    if _is_steam_deck; then
        echo ""
        print_info "In-game commands file:"
        echo -e "  ${CYAN}${outfile}${RST}"
        echo ""
        if command -v kate &>/dev/null; then
            if ask_yes_no "Open in Kate (graphical text editor)?"; then
                kate "$outfile" &>/dev/null &
            fi
        else
            print_info "Open this file with your preferred text editor."
        fi
        press_enter
    else
        with_full_screen nano "$outfile"
    fi
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
INGAME_COMMANDS_FILE=""       # set to $SERVER_DIR/ingame-commands.txt after detect_install
WOW_CLIENT_DIR=""             # set by detect_wow_client; saved to $SERVER_DIR/.wow_client_dir

# Session counter for NPC spawn coordinate staggering (deterministic per known entry)
_NPC_SPAWN_IDX=0

# Module registry: key|name|repo url|sql dirs (comma-sep)
declare -a MODULE_REGISTRY=(
    "mod-1v1-arena|1v1 Arena|https://github.com/azerothcore/mod-1v1-arena.git|characters"
    "mod-aoe-loot|AoE Loot|https://github.com/azerothcore/mod-aoe-loot.git|world"
    "mod-ah-bot|Auction House Bot|https://github.com/azerothcore/mod-ah-bot.git|world"
    "mod-autobalance|Auto Balance (dynamic difficulty)|https://github.com/azerothcore/mod-autobalance.git|world"
    "mod-ale|AzerothCore Lua Engine (ALE)|https://github.com/azerothcore/mod-ale.git|"
    "mod-player-bot-level-brackets|Bot Level Brackets (Playerbot distribution)|https://github.com/DustinHendrickson/mod-player-bot-level-brackets.git|characters"
    "mod-challenge-modes|Challenge Modes (Hardcore, Iron Man, etc.)|https://github.com/nl-saw/mod-challenge-modes.git|world,characters"
    "mod-individual-progression|Individual Progression (Vanilla → TBC → WotLK)|https://github.com/ZhengPeiRu21/mod-individual-progression.git|world,characters"
    "mod-junk-to-gold|Junk to Gold (auto-sell gray items)|https://github.com/noisiver/mod-junk-to-gold.git|world"
    "mod-learn-spells|Learn Spells on Levelup|https://github.com/azerothcore/mod-learn-spells.git|world"
    "mod-npc-beastmaster|NPC Beastmaster (pets for all classes)|https://github.com/azerothcore/mod-npc-beastmaster.git|world,characters"
    "mod-quest-loot-party|Quest Loot Party (quest items drop for all eligible party members)|https://github.com/pangolp/mod-quest-loot-party.git|world"
    "mod-arac|All Races All Classes (ARAC — data mod: SQL + DBC + MPQ)|https://github.com/heyitsbench/mod-arac.git|world"
    "mod-dungeon-master|Dungeon Master (roguelike dungeon challenge system)|https://github.com/InstanceForge/mod-dungeon-master.git|world,characters"
    "mod-solocraft|Solocraft (solo dungeon/raid scaling)|https://github.com/azerothcore/mod-solocraft.git|world"
    "mod-talentbutton|Talent Button (dual-spec at 10 + anywhere talent reset)|https://github.com/brian8544/mod-talentbutton.git|"
    "mod-transmog|Transmogrification|https://github.com/azerothcore/mod-transmog.git|world,characters"
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
    INGAME_COMMANDS_FILE="$SERVER_DIR/ingame-commands.txt"

    # Self-heal a known mis-named module dir before any rebuild can hit it.
    heal_legacy_module_dirs
}

# Rename/clean legacy mis-named C++ module directories so the worldserver
# can compile. AzerothCore derives each module's script-loader call from the
# module's DIRECTORY name (dashes -> underscores): a dir named custom-login
# makes it emit Addcustom_loginScripts(), but mod-custom-login only defines
# Addmod_custom_loginScripts() — an "undefined reference" link error that
# fails every worldserver rebuild. Older runs of this script cloned the
# module into modules/custom-login (the registry key used to drop the mod-
# prefix); rename it to the correct mod- prefixed dir. Silent no-op when
# there's nothing to fix.
heal_legacy_module_dirs() {
    [ -n "$SERVER_DIR" ] || return 0
    local legacy="$SERVER_DIR/modules/custom-login"
    local correct="$SERVER_DIR/modules/mod-custom-login"
    [ -d "$legacy" ] || return 0
    if [ -d "$correct" ]; then
        print_warning "Removing stale mis-named module dir modules/custom-login"
        print_info    "(superseded by modules/mod-custom-login — fixes the worldserver build)"
        rm -rf "$legacy"
    else
        print_warning "Fixing mis-named Custom Login module so the worldserver can compile"
        print_info    "Renaming modules/custom-login -> modules/mod-custom-login"
        mv "$legacy" "$correct"
    fi
}

# ─────────────────────────────────────────────────────────────
# WOW CLIENT DETECTION
# ─────────────────────────────────────────────────────────────
# Probe common SteamOS/Ubuntu paths for a WoW WotLK client.
# Saves the found path to $SERVER_DIR/.wow_client_dir for reuse.
# Sets WOW_CLIENT_DIR; returns 0 on success, 1 if not found/skipped.
detect_wow_client() {
    [ -n "$WOW_CLIENT_DIR" ] && return 0
    local _cache="$SERVER_DIR/.wow_client_dir"
    if [ -f "$_cache" ]; then
        local _saved; _saved=$(cat "$_cache")
        if [ -d "$_saved" ]; then
            WOW_CLIENT_DIR="$_saved"
            return 0
        fi
    fi
    print_step "Detecting WoW client install"
    # Known parent directories that may contain a WoW client as a subdirectory
    local -a _parent_dirs=(
        "$HOME/.steam/steam/steamapps/common"
        "$HOME/Steam/steamapps/common"
        "$HOME/.local/share/Steam/steamapps/common"
        "$HOME/.var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/common"
        "$HOME/wow wotlk"
        "$HOME/Games"
        "$HOME"
    )
    # Known exact client folder names (direct children of above parents, or absolute)
    local -a _names=(
        "World of Warcraft"
        "wow wotlk"
        "wotlk"
        "ChromieCraft_3.3.5a"
        "wow 3.3.5a"
        "wow-client-3.3.5a"
        "wow-client"
        "wow-wotlk-client"
    )
    local _wow_heuristic='[ -f "$p/Wow.exe" ] || [ -f "$p/wow.exe" ] || [ -f "$p/WowT.exe" ] || [ -d "$p/Interface" ]'
    local pd n p
    # 1) Scan every parent × name combination
    for pd in "${_parent_dirs[@]}"; do
        [ -d "$pd" ] || continue
        for n in "${_names[@]}"; do
            p="$pd/$n"
            if [ -d "$p" ] && \
                ( [ -f "$p/Wow.exe" ] || [ -f "$p/wow.exe" ] || \
                  [ -f "$p/WowT.exe" ] || [ -d "$p/Interface" ] ); then
                WOW_CLIENT_DIR="$p"
                print_success "WoW client found: $WOW_CLIENT_DIR"
                echo "$WOW_CLIENT_DIR" > "$_cache"
                return 0
            fi
        done
    done
    # 2) Broad scan: any subdir of parent dirs that looks like a WoW install
    for pd in "${_parent_dirs[@]}"; do
        [ -d "$pd" ] || continue
        local sub
        while IFS= read -r sub; do
            p="$pd/$sub"
            if [ -d "$p" ] && \
                ( [ -f "$p/Wow.exe" ] || [ -f "$p/wow.exe" ] || \
                  [ -f "$p/WowT.exe" ] || [ -d "$p/Interface" ] ); then
                WOW_CLIENT_DIR="$p"
                print_success "WoW client found: $WOW_CLIENT_DIR"
                echo "$WOW_CLIENT_DIR" > "$_cache"
                return 0
            fi
        done < <(ls -1 "$pd" 2>/dev/null)
    done
    print_warning "WoW client not found automatically."
    echo ""
    printf "${WHITE}Enter full path to WoW client folder (leave blank to skip): ${RST}"
    read -r _manual
    # Expand ~ and strip surrounding quotes the user may have typed
    _manual="${_manual#\"}" ; _manual="${_manual%\"}"
    _manual="${_manual#\'}" ; _manual="${_manual%\'}"
    _manual="${_manual/#\~/$HOME}"
    if [ -n "$_manual" ] && [ -d "$_manual" ]; then
        WOW_CLIENT_DIR="$_manual"
        echo "$WOW_CLIENT_DIR" > "$_cache"
        print_success "WoW client set to: $WOW_CLIENT_DIR"
        return 0
    elif [ -n "$_manual" ]; then
        print_warning "Directory not found: $_manual"
        print_info "Check the path and try again via option 16 in the main menu."
    fi
    print_warning "WoW client path not set — addon and client data auto-install skipped."
    return 1
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
            print_info "    corrupted update tracking. Use option 14 → Server Maintenance → Repair install state."
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
    "mod-dungeon-master|acore_world|dm_setup.sql"
    "mod-dungeon-master|acore_characters|dm_characters_setup.sql"
    "mod-ah-bot|acore_world|auctionhousebot_professionItems.sql mod_auctionhousebot.sql"
    "mod-npc-beastmaster|acore_world|beastmaster_tames.sql beastmaster_tames_inserts.sql"
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
    "activechat|Azeroth Chatter (lore-grounded ambient world RP chat)|https://github.com/svey-xyz/ActiveChat.git"
    "battlepass|Battle Pass System (XP progression + rewards + client addon)|https://github.com/Shonik/lua-battlepass.git"
    "bmah|Black Market Auction House (MoP-style BMAH + client addon)|https://github.com/DadsMmoLab/dads-mmo-lab.git"
    "lootpet|Loot Pet (vanity pet auto-loots nearby corpses)|https://github.com/Brytenwally/Lootpet.git"
    "paragon|Paragon Anniversary (endless post-80 stat progression + client addon)|https://github.com/Grim-Batol/Paragon-Anniversary.git"
    "sitmeanrest|Sit Means Rest (regen buff on /sit; strips on movement)|https://github.com/Brytenwally/SitMeansRest.git"
    "sod|Season of Discovery Buffs (phased leveling XP rate bonus)|https://github.com/DadsMmoLab/dads-mmo-lab.git"
    "unlimitedammo|Unlimited Ammo (auto-refills Hunter arrows/bullets)|https://github.com/Day36512/Acore_Lua_Unlimited_Ammo.git"
)

# SQL Mod registry — key|name|github_url|install_type
# install_type values:
#   clone_sql         — clone repo, run up.sql / down.sql via docker exec
#   clone_sql_norevert— clone_sql but no down.sql exists
#   clone_sql_pick    — clone repo, user picks which SQL variant to apply
#   clone_dist        — clone repo, .dist files → .sql, apply with optional config
#   conf_module       — C++ module: clone to modules/, copy .conf.dist; needs rebuild
#   tweak_world       — inline SQL tweaks on acore_world (no clone needed)
#   conf_xp           — edits worldserver.conf XP rate settings (no DB change)
declare -a SQL_MOD_REGISTRY=(
    "all-stackables|All Stackables to 200|https://github.com/AsgavinYT/azerothcore-all-stackables-200.git|clone_sql"
    "baby-mobs|Baby Mobs (HP×0.25 / DMG×0.25 / ARM×0.25)||tweak_world"
    "buff-mobs|Buff Mobs (HP×2 / DMG×1.5 / ARM×1.5)||tweak_world"
    "mod-custom-login|Custom Login (starter gear + rep on first login)|https://github.com/azerothcore/mod-custom-login.git|conf_module"
    "xbuff-mobs|Extreme Buff Mobs (HP×4 / DMG×2 / ARM×2)||tweak_world"
    "hearthstone-cd|Hearthstone Cooldown Tweaks|https://github.com/AsgavinYT/hearthstone-cooldowns.git|clone_sql_pick"
    "lvl1-mounts|Level One Mounts (ride at level 1)|https://github.com/tomcoffingiii/mod-level-one-mounts.git|clone_sql"
    "nerf-mobs|Nerf Mobs (HP×0.5 / DMG×0.75 / ARM×0.75)||tweak_world"
    "npc-teleporter|NPC Teleporter (capital + starting zones)|https://github.com/Zoidwaffle/sql-npc-teleporter.git|clone_dist"
    "portals-capitals|Portals in All Capitals|https://github.com/azerothcore/portals-in-all-capitals.git|clone_sql"
    "rare-drops|Rare Drops (450 Classic rares loot)|https://github.com/StraysFromPath/mod-rare-drops.git|clone_sql_norevert"
    "xp-rates|XP Rate Customization (Kill/Quest/Explore)||conf_xp"
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

# Compute SHA1 hash of a SQL file the same way AC's UpdateFetcher does.
# Returns uppercase hex string; empty string on failure.
compute_sql_hash() {
    local file="$1"
    if command -v sha1sum >/dev/null 2>&1; then
        sha1sum "$file" 2>/dev/null | awk '{print toupper($1)}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 1 "$file" 2>/dev/null | awk '{print toupper($1)}'
    else
        # Fall back to computing inside the DB container
        local bname; bname=$(basename "$file")
        docker cp "$file" "$DB_CONTAINER:/tmp/$bname" 2>/dev/null
        docker exec "$DB_CONTAINER" sha1sum "/tmp/$bname" 2>/dev/null \
            | awk '{print toupper($1)}'
        docker exec "$DB_CONTAINER" rm -f "/tmp/$bname" 2>/dev/null
    fi
}
# Insert or update the updates-table row for a SQL file so AC will skip it.
# Use this when the table already exists in the DB but AC has no record of
# the file — which causes ac-db-import to fail with "Table X already exists".
mark_sql_applied() {
    local db_full="$1" sql_filename="$2"
    local sql_file
    sql_file=$(find "$SERVER_DIR/modules" -name "$sql_filename" 2>/dev/null | head -1)
    if [ -z "$sql_file" ]; then
        print_error "Cannot find '$sql_filename' in $SERVER_DIR/modules"
        return 1
    fi
    local hash; hash=$(compute_sql_hash "$sql_file")
    if [ -z "$hash" ]; then
        print_error "Could not compute hash for $sql_filename"
        return 1
    fi
    print_info "  File: $sql_file"
    print_info "  Hash: ${hash:0:7}... (SHA1)"
    docker exec "$DB_CONTAINER" mysql -uroot -p"$DB_ROOT_PASSWORD" \
        "$db_full" \
        -e "INSERT INTO updates (name, hash, state, timestamp, speed)
            VALUES ('$sql_filename', '$hash', 'RELEASED', NOW(), 0)
            ON DUPLICATE KEY UPDATE hash='$hash', state='RELEASED';" 2>/dev/null && \
        echo -e "  ${GREEN}✓${RST} Marked as applied: $sql_filename" || \
        { print_error "Failed to write to $db_full.updates"; return 1; }
}
# Repair flow for a single module.
# Two modes:
#   MARK   — inserts the file's hash into updates so AC skips it.
#            Use when: table exists in DB but no/wrong tracking row.
#            Symptom: ac-db-import "Table X already exists" AND the
#            SQL file does NOT use CREATE TABLE IF NOT EXISTS.
#   CLEAR  — deletes the tracking row so AC re-applies the SQL.
#            Use when: the module SQL file changed (hash mismatch) AND
#            the SQL uses CREATE TABLE IF NOT EXISTS / INSERT IGNORE.
repair_module() {
    local key="$1" db_full="$2" known_files="$3"
    print_step "Repairing: $key"
    show_module_tracking "$key"
    echo ""
    # Resolve file list (known → auto-discover → manual)
    local files_to_fix=""
    if [ -n "$known_files" ]; then
        echo -e "${WHITE}Known SQL files for ${db_full}:${RST}"
        local f
        for f in $known_files; do echo -e "  ${CYAN}$f${RST}"; done
        echo ""
        if ask_yes_no "Use these files?"; then files_to_fix="$known_files"; fi
    fi
    if [ -z "$files_to_fix" ]; then
        local db_short="${db_full#acore_}"
        local discovered; discovered=$(discover_module_sql_files "$key" "$db_short")
        if [ -n "$discovered" ]; then
            echo -e "${WHITE}Auto-discovered SQL files:${RST}"
            local f
            for f in $discovered; do echo -e "  ${CYAN}$f${RST}"; done
            echo ""
            if ask_yes_no "Use these auto-discovered files?"; then
                files_to_fix="$discovered"
            fi
        fi
    fi
    if [ -z "$files_to_fix" ]; then
        echo ""
        echo -e "${WHITE}Enter SQL filenames manually (space-separated, ENTER to skip):${RST}"
        printf "${WHITE}Files: ${RST}"
        read -r files_to_fix
        [ -z "$files_to_fix" ] && { print_info "Skipped."; return 0; }
    fi
    # Choose repair mode
    echo ""
    echo -e "${WHITE}${BOLD}Choose repair mode:${RST}"
    echo -e "  ${CYAN}M)${RST} Mark as applied   — table exists in DB, AC has no record of it"
    echo -e "     ${DIM}(use when ac-db-import says 'Table X already exists')${RST}"
    echo -e "  ${CYAN}C)${RST} Clear tracking    — force AC to re-apply the SQL on next start"
    echo -e "     ${DIM}(safe only if the SQL uses CREATE TABLE IF NOT EXISTS / INSERT IGNORE)${RST}"
    echo ""
    local mode=""
    while [ -z "$mode" ]; do
        printf "${WHITE}Choice [M/C]: ${RST}"
        read -r mode
        case "${mode,,}" in
            m) mode="mark" ;;
            c) mode="clear" ;;
            *) mode=""; echo "Please enter M or C." ;;
        esac
    done
    echo ""
    local ok=0 fail=0
    local f
    for f in $files_to_fix; do
        if [ "$mode" = "mark" ]; then
            mark_sql_applied "$db_full" "$f" && ok=$((ok + 1)) || fail=$((fail + 1))
        else
            if clear_update_tracking_row "$db_full" "$f"; then
                echo -e "  ${GREEN}✓${RST} Cleared: $f"
                ok=$((ok + 1))
            else
                echo -e "  ${DIM}○${RST} Not found in updates: $f"
                fail=$((fail + 1))
            fi
        fi
    done
    echo ""
    if [ "$ok" -gt 0 ] && [ "$mode" = "mark" ]; then
        print_success "$ok file(s) marked as applied — ac-db-import will skip them."
        print_info "Restart the server now; no further SQL action needed."
    elif [ "$ok" -gt 0 ]; then
        print_success "Cleared $ok tracking row(s) — AC will re-apply SQL on next start."
    fi
    if [ "$fail" -gt 0 ] && [ "$mode" = "mark" ]; then
        print_warning "$fail file(s) could not be marked — check $SERVER_DIR/modules for the files."
    elif [ "$fail" -gt 0 ]; then
        print_info "$fail file(s) were not in the updates table (already clear or never tracked)."
    fi
}

repair_install_state() {
    print_step "Repair Install State"

    echo ""
    echo -e "${WHITE}Use this when ac-db-import fails with errors like:${RST}"
    echo -e "${WHITE}  • ${CYAN}ERROR 1050: Table 'X' already exists${RST}"
    echo -e "${WHITE}  • ${CYAN}ac-db-import: didn't complete successfully: exit 1${RST}"
    echo ""
    echo -e "${WHITE}${BOLD}Two repair modes (you choose per-module):${RST}"
    echo -e "${WHITE}  ${CYAN}Mark as applied${RST}${WHITE} — inserts the file's hash so AC skips it.${RST}"
    echo -e "${WHITE}    Use when: table already exists, no tracking row, SQL lacks IF NOT EXISTS.${RST}"
    echo -e "${WHITE}    (e.g. mod-ah-bot's auctionhousebot_professionItems.sql)${RST}"
    echo ""
    echo -e "${WHITE}  ${CYAN}Clear tracking${RST}${WHITE} — deletes the row so AC re-applies the SQL.${RST}"
    echo -e "${WHITE}    Use when: the SQL file changed (hash mismatch) and it uses${RST}"
    echo -e "${WHITE}    CREATE TABLE IF NOT EXISTS / INSERT IGNORE semantics.${RST}"
    echo ""
    echo -e "${GREEN}Neither mode drops tables — both are safe and non-destructive.${RST}"
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
        local db_label=""
        [ -n "${repair_dbs[$i]}" ] && db_label=" ${DIM}(${repair_dbs[$i]})${RST}"
        printf "  %2d) %s%b%b\n" "$((i + 1))" "${repair_keys[$i]}" "$db_label" "$marker"
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
    print_info "Done. Restart the server (menu option 9) to apply changes."
}

# ─────────────────────────────────────────────────────────────
# MODULE OPERATIONS
# ─────────────────────────────────────────────────────────────
module_is_installed() {
    local key="$1"
    [ -d "$SERVER_DIR/modules/$key/.git" ]
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
        if [ -d "$SERVER_DIR/modules/$key" ] && [ ! -d "$SERVER_DIR/modules/$key/.git" ]; then
            print_warning "Removing incomplete clone at modules/$key"
            rm -rf "$SERVER_DIR/modules/$key"
        fi
        if ! git clone --depth 1 "$url" "$SERVER_DIR/modules/$key"; then
            rm -rf "$SERVER_DIR/modules/$key"
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

    if [ "$key" = "mod-arac" ]; then
        print_warning "mod-arac is data-only. Removing the clone does NOT revert:"
        print_info "  • arac.sql data already imported into acore_world"
        print_info "  • DBC files already copied to the server data volume"
        print_info "  • Patch-A.MPQ already installed in your WoW client Data/"
        print_info "To fully uninstall ARAC, those must be reverted manually."
        echo ""
    fi

    if ask_yes_no "  Remove module files from $SERVER_DIR/modules/$key?"; then
        rm -rf "$SERVER_DIR/modules/$key"
        print_success "Module files removed"
        if [ "$key" != "mod-arac" ]; then
            print_info "(Database tables/rows from this module are kept — removing"
            print_info " them risks data loss and they're harmless to leave.)"
        fi
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
        print_info "Add it first via main menu option 1 (Manage AzerothCore Modules)."
        return 1
    fi

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

    mkdir -p "$SERVER_DIR/env/dist/etc/modules"
    local conf_active="$SERVER_DIR/env/dist/etc/modules/mod_ahbot.conf"
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
        print_info "Add it first via main menu option 1 (Manage AzerothCore Modules)."
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
# configure_module_challenge_modes
#   Copies challenge_modes.conf.dist → challenge_modes.conf and opens
#   it in the editor.  Also reminds user about EnablePlayerSettings.
#   Note: if no conf.dist is present, a worldserver rebuild is needed
#   first so the build system produces the conf files.
# ─────────────────────────────────────────────────────────────
configure_module_challenge_modes() {
    print_step "Configuring Challenge Modes"

    local module_dir="$SERVER_DIR/modules/mod-challenge-modes"
    if [ ! -d "$module_dir" ]; then
        print_error "Challenge Modes module not installed (expected at $module_dir)."
        return 1
    fi

    local conf_dist="$module_dir/conf/challenge_modes.conf.dist"
    local conf_dest="$SERVER_DIR/env/dist/etc/modules/challenge_modes.conf"
    mkdir -p "$SERVER_DIR/env/dist/etc/modules"

    if [ ! -f "$conf_dest" ]; then
        if [ -f "$conf_dist" ]; then
            cp "$conf_dist" "$conf_dest"
            print_success "Created $conf_dest"
        else
            print_warning "conf.dist not found at $conf_dist"
            print_info "The worldserver must be rebuilt once before conf files are generated."
            print_info "After rebuilding, re-run this configure option."
            return 0
        fi
    fi

    print_info "⚠  Challenge Modes requires  EnablePlayerSettings = 1  in worldserver.conf."
    echo ""
    _open_text_file "$conf_dest"
    echo ""
    print_info "Restart the worldserver for conf changes to take effect."
}

# ─────────────────────────────────────────────────────────────
# configure_module_bot_level_brackets
#   Copies mod_player_bot_level_brackets.conf.dist and opens it.
#   Reminds user that the Playerbots module is required.
# ─────────────────────────────────────────────────────────────
configure_module_bot_level_brackets() {
    print_step "Configuring Bot Level Brackets"

    local module_dir="$SERVER_DIR/modules/mod-player-bot-level-brackets"
    if [ ! -d "$module_dir" ]; then
        print_error "Bot Level Brackets module not installed (expected at $module_dir)."
        return 1
    fi

    local conf_dist="$module_dir/conf/mod_player_bot_level_brackets.conf.dist"
    local conf_dest="$SERVER_DIR/env/dist/etc/modules/mod_player_bot_level_brackets.conf"
    mkdir -p "$SERVER_DIR/env/dist/etc/modules"

    if [ ! -f "$conf_dest" ]; then
        if [ -f "$conf_dist" ]; then
            cp "$conf_dist" "$conf_dest"
            print_success "Created $conf_dest"
        else
            print_warning "conf.dist not found at $conf_dist"
            print_info "The worldserver must be rebuilt once before conf files are generated."
            print_info "After rebuilding, re-run this configure option."
            return 0
        fi
    fi

    print_info "⚠  Bot Level Brackets requires the Playerbots module to function."
    echo ""
    _open_text_file "$conf_dest"
    echo ""
    print_info "Restart the worldserver for conf changes to take effect."
}

# ─────────────────────────────────────────────────────────────
# configure_module_quest_loot_party
#   Copies mod-quest-loot-party.conf.dist and opens it.
#   Two settings: QuestParty.Enable and QuestParty.Message
# ─────────────────────────────────────────────────────────────
configure_module_quest_loot_party() {
    print_step "Configuring Quest Loot Party"
    local module_dir="$SERVER_DIR/modules/mod-quest-loot-party"
    if [ ! -d "$module_dir" ]; then
        print_error "Quest Loot Party module not installed (expected at $module_dir)."
        return 1
    fi
    local conf_dist="$module_dir/conf/mod-quest-loot-party.conf.dist"
    local conf_dest="$SERVER_DIR/env/dist/etc/modules/mod-quest-loot-party.conf"
    mkdir -p "$SERVER_DIR/env/dist/etc/modules"
    if [ ! -f "$conf_dest" ]; then
        if [ -f "$conf_dist" ]; then
            cp "$conf_dist" "$conf_dest"
            print_success "Created $conf_dest"
        else
            print_warning "conf.dist not found at $conf_dist"
            print_info "The worldserver must be rebuilt once before conf files are generated."
            print_info "After rebuilding, re-run this configure option."
            return 0
        fi
    fi
    echo ""
    echo -e "${WHITE}Settings:${RST}"
    printf "  ${CYAN}%-30s${RST} ${WHITE}%s${RST}\n" "QuestParty.Enable" "true = on / false = off (default: true)"
    printf "  ${CYAN}%-30s${RST} ${WHITE}%s${RST}\n" "QuestParty.Message" "true = notify players / false = silent (default: true)"
    echo ""
    _open_text_file "$conf_dest"
    echo ""
    print_info "Restart the worldserver for conf changes to take effect."
}

# ─────────────────────────────────────────────────────────────
# configure_module_npc_beastmaster
#   Copies mod_npc_beastmaster.conf.dist and opens it.
#   Reminds user about the Creatures.CustomIDs worldserver.conf tip.
# ─────────────────────────────────────────────────────────────
configure_module_npc_beastmaster() {
    print_step "Configuring NPC Beastmaster"

    local module_dir="$SERVER_DIR/modules/mod-npc-beastmaster"
    if [ ! -d "$module_dir" ]; then
        print_error "NPC Beastmaster module not installed (expected at $module_dir)."
        return 1
    fi

    local conf_dist="$module_dir/conf/mod_npc_beastmaster.conf.dist"
    local conf_dest="$SERVER_DIR/env/dist/etc/modules/mod_npc_beastmaster.conf"
    mkdir -p "$SERVER_DIR/env/dist/etc/modules"

    if [ ! -f "$conf_dest" ]; then
        if [ -f "$conf_dist" ]; then
            cp "$conf_dist" "$conf_dest"
            print_success "Created $conf_dest"
        else
            print_warning "conf.dist not found at $conf_dist"
            print_info "The worldserver must be rebuilt once before conf files are generated."
            print_info "After rebuilding, re-run this configure option."
            return 0
        fi
    fi

    print_info "Tip: Add 601026 to Creatures.CustomIDs in worldserver.conf to suppress"
    print_info "     a harmless gossip-menu warning in server logs."
    echo ""
    _open_text_file "$conf_dest"
    echo ""
    print_info "You must enable non-hunters in the conf to allow other classes to get pets."
    print_info "Restart the worldserver for conf changes to take effect."
}

# ─────────────────────────────────────────────────────────────
# configure_module_dungeon_master
#   Copies mod_dungeon_master.conf.dist → mod_dungeon_master.conf
#   and opens it for editing.
# ─────────────────────────────────────────────────────────────
configure_module_dungeon_master() {
    print_step "Configuring Dungeon Master"
    local module_dir="$SERVER_DIR/modules/mod-dungeon-master"
    if [ ! -d "$module_dir" ]; then
        print_error "Dungeon Master module not installed (expected at $module_dir)."
        return 1
    fi
    local conf_dist="$module_dir/conf/mod_dungeon_master.conf.dist"
    local conf_dest="$SERVER_DIR/env/dist/etc/modules/mod_dungeon_master.conf"
    mkdir -p "$SERVER_DIR/env/dist/etc/modules"
    if [ ! -f "$conf_dest" ]; then
        if [ -f "$conf_dist" ]; then
            cp "$conf_dist" "$conf_dest"
            print_success "Created $conf_dest"
        else
            print_warning "conf.dist not found at $conf_dist"
            print_info "The worldserver must be rebuilt once before conf files are generated."
            print_info "After rebuilding, re-run this configure option."
            return 0
        fi
    fi
    echo ""
    print_info "Key settings: Scaling.LevelBand, Rewards.BaseGold, Roguelike.Enable"
    print_info "NPC entry 500000 spawns automatically in all major cities on server start."
    echo ""
    _open_text_file "$conf_dest"
    echo ""
    print_info "Restart the worldserver for conf changes to take effect."
}

# ─────────────────────────────────────────────────────────────
# configure_module_talentbutton
#   Copies mod_talentbutton.conf.dist → mod_talentbutton.conf
#   and opens it for editing.
# ─────────────────────────────────────────────────────────────
configure_module_talentbutton() {
    print_step "Configuring Talent Button"
    local module_dir="$SERVER_DIR/modules/mod-talentbutton"
    if [ ! -d "$module_dir" ]; then
        print_error "Talent Button module not installed (expected at $module_dir)."
        return 1
    fi
    local conf_dist="$module_dir/conf/mod_talentbutton.conf.dist"
    local conf_dest="$SERVER_DIR/env/dist/etc/modules/mod_talentbutton.conf"
    mkdir -p "$SERVER_DIR/env/dist/etc/modules"
    if [ ! -f "$conf_dest" ]; then
        if [ -f "$conf_dist" ]; then
            cp "$conf_dist" "$conf_dest"
            print_success "Created $conf_dest"
        else
            print_warning "conf.dist not found at $conf_dist"
            print_info "The worldserver must be rebuilt once before conf files are generated."
            print_info "After rebuilding, re-run this configure option."
            return 0
        fi
    fi
    echo ""
    print_warning "Requires an UNPATCHED WoW 3.3.5a client — RCEPatcher clients will not see the button."
    echo ""
    _open_text_file "$conf_dest"
    echo ""
    print_info "Restart the worldserver for conf changes to take effect."
}

# ─────────────────────────────────────────────────────────────
# configure_module_learn_spells
#   Copies mod_learnspells.conf.dist → mod_learnspells.conf.
#   Falls back to writing minimal defaults inline when the
#   conf.dist is not yet present (pre-rebuild).
# ─────────────────────────────────────────────────────────────
configure_module_learn_spells() {
    print_step "Configuring Learn Spells on Levelup"
    local module_dir="$SERVER_DIR/modules/mod-learn-spells"
    if [ ! -d "$module_dir" ]; then
        print_error "mod-learn-spells is not installed (expected at $module_dir)."
        return 1
    fi
    local conf_dest="$SERVER_DIR/env/dist/etc/modules/mod_learnspells.conf"
    mkdir -p "$SERVER_DIR/env/dist/etc/modules"
    if [ -f "$conf_dest" ]; then
        print_success "mod_learnspells.conf already active: $conf_dest"
        print_info "Edit directly to change settings."
        return 0
    fi
    local conf_dist="$module_dir/conf/mod_learnspells.conf.dist"
    if [ -f "$conf_dist" ]; then
        cp "$conf_dist" "$conf_dest"
        print_success "Created $conf_dest from conf.dist"
    else
        # conf.dist not present yet (pre-rebuild) — write minimal defaults
        printf '[worldserver]\nLearnSpells.Enable = 1\nLearnSpells.Announce = 1\nLearnSpells.OnFirstLogin = 0\nLearnSpells.MaxLevel = 80\n' > "$conf_dest"
        print_success "Created $conf_dest with default settings"
        print_info "(conf.dist not found — defaults written inline; rebuild will not override this file)"
    fi
    echo ""
    print_info "Restart the worldserver for the new conf to take effect."
}

# ─────────────────────────────────────────────────────────────
# configure_mod_arac
#   Applies ARAC SQL, copies server DBC files, and offers to
#   install Patch-A.MPQ to the WoW client Data/ directory.
#   mod-arac is data-only — no worldserver rebuild required.
# ─────────────────────────────────────────────────────────────
configure_mod_arac() {
    print_step "Configuring All Races All Classes (ARAC)"
    local module_dir="$SERVER_DIR/modules/mod-arac"
    if [ ! -d "$module_dir" ]; then
        print_error "mod-arac not installed (expected at $module_dir)."
        return 1
    fi
    refresh_container_names
    local arac_marker="$SERVER_DIR/modules/mod-arac/.arac_sql_applied"
    # ── Step 1: SQL ───────────────────────────────────────────
    local arac_sql="$module_dir/data/sql/db-world/arac.sql"
    if [ ! -f "$arac_sql" ]; then
        print_error "arac.sql not found at: $arac_sql"
        return 1
    fi
    echo ""
    print_step "ARAC — Step 1: Apply SQL to acore_world"
    if [ -f "$arac_marker" ]; then
        print_info "ARAC SQL was previously applied (marker file present)."
        if ! ask_yes_no "Re-apply arac.sql anyway? (only needed after a DB wipe)"; then
            print_info "SQL step skipped."
        else
            if ! ensure_db_running; then
                print_error "Database is not available — start the server first."
                return 1
            fi
            if ale_run_sql_file "acore_world" "$arac_sql"; then
                touch "$arac_marker"
                print_success "ARAC SQL re-applied."
            else
                print_error "SQL apply failed — check DB logs."
                return 1
            fi
        fi
    else
        print_warning "Back up your database before applying ARAC SQL (it modifies race/class unlock data)."
        echo ""
        if ask_yes_no "Apply arac.sql to acore_world database now?"; then
            if ! ensure_db_running; then
                print_error "Database is not available — start the server first."
                return 1
            fi
            if ale_run_sql_file "acore_world" "$arac_sql"; then
                touch "$arac_marker"
                print_success "ARAC SQL applied to acore_world."
            else
                print_error "SQL apply failed — check DB logs."
                return 1
            fi
        else
            print_info "SQL skipped. Run manually: docker exec -i \$DB_CONTAINER mysql -uroot -p... acore_world < arac.sql"
        fi
    fi
    # ── Step 2: Server DBC files ──────────────────────────────
    echo ""
    print_step "ARAC — Step 2: Copy DBC files to server"
    local dbc_src="$module_dir/patch-contents/DBFilesContent"
    if [ -d "$dbc_src" ]; then
        if ask_yes_no "Copy ARAC DBC files to server data/dbc/ now?"; then
            copy_server_dbc "$dbc_src" "ARAC server DBC files"
        else
            print_info "DBC copy skipped. Copy manually:"
            print_info "  docker run --rm -v ac-client-data:/data -v $dbc_src:/src:ro alpine sh -c 'cp /src/*.dbc /data/dbc/'"
        fi
    else
        print_warning "DBC source not found at: $dbc_src"
        print_info "Try re-installing to refresh the clone."
    fi
    # ── Step 3: Client Patch-A.MPQ ────────────────────────────
    echo ""
    print_step "ARAC — Step 3: Install Patch-A.MPQ to WoW client"
    local mpq_src="$module_dir/Patch-A.MPQ"
    if [ -f "$mpq_src" ]; then
        echo -e "${WHITE}Patch-A.MPQ must be placed in your WoW client Data/ directory.${RST}"
        if ask_yes_no "Auto-install Patch-A.MPQ to WoW client Data/ now?"; then
            if detect_wow_client; then
                local mpq_dest="$WOW_CLIENT_DIR/Data/Patch-A.MPQ"
                if cp "$mpq_src" "$mpq_dest"; then
                    print_success "Patch-A.MPQ installed → $mpq_dest"
                else
                    print_error "Copy failed — check permissions on $WOW_CLIENT_DIR/Data/"
                fi
            fi
        else
            print_info "Manual install: copy $mpq_src"
            print_info "  to your WoW client Data/ directory."
        fi
    else
        print_warning "Patch-A.MPQ not found at: $mpq_src"
        print_info "Download it manually from: https://github.com/heyitsbench/mod-arac"
    fi
    echo ""
    print_info "mod-arac is data-only — no worldserver rebuild is required."
    print_info "Restart the worldserver to load the new race/class combinations."
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
        activechat)    [ -d "$lua_dir/AzerothChatter" ] ;;
        battlepass)    [ -d "$lua_dir/battlepass" ] ;;
        paragon)       [ -d "$lua_dir/paragon" ] ;;
        bmah)          [ -f "$lua_dir/BMAH.lua" ] ;;
        lootpet)       [ -f "$lua_dir/LootPet.lua" ] ;;
        sod)           [ -f "$lua_dir/SOD.lua" ] ;;
        sitmeanrest)   [ -f "$lua_dir/SitMeansRest.lua" ] ;;
        unlimitedammo) [ -f "$lua_dir/UnlimitedAmmo.lua" ] ;;
        *)             false ;;
    esac
}

# Ensure the database container is up. Returns 1 if it cannot start.
ensure_db_running() {
    refresh_container_names
    if ! container_running "$DB_CONTAINER"; then
        print_info "Starting database container..."
        (cd "$SERVER_DIR" && docker compose up -d ac-database 2>/dev/null) || true
        refresh_container_names
    fi
    # Always validate MySQL is accepting connections, even if container was already up
    local i
    for i in $(seq 1 15); do
        if docker exec "$DB_CONTAINER" mysqladmin ping \
            -uroot -p"$DB_ROOT_PASSWORD" &>/dev/null 2>&1; then
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
        return 0
    else
        print_warning "SQL apply failed: $(basename "$sql_file") — check database logs."
        return 1
    fi
}

# Copy a WoW addon folder into the client's Interface/AddOns/.
# Usage: copy_client_addon <src_dir> <addon_name> [description]
# Calls detect_wow_client if WOW_CLIENT_DIR is not yet set.
copy_client_addon() {
    local src_dir="$1" addon_name="$2" desc="${3:-$2}"
    if ! detect_wow_client; then
        print_info "Manual install: copy ${CYAN}$src_dir${RST} → <WoW>/Interface/AddOns/$addon_name/"
        return 1
    fi
    local dest="$WOW_CLIENT_DIR/Interface/AddOns/$addon_name"
    if [ ! -d "$src_dir" ]; then
        print_warning "Addon source not found: $src_dir"
        print_info "Manual: cp -r \"$src_dir\" \"$dest\""
        return 1
    fi
    mkdir -p "$dest"
    if cp -r "$src_dir/." "$dest/"; then
        print_success "$desc installed → $dest"
        return 0
    else
        print_warning "Copy failed — install manually:"
        print_info "  cp -r \"$src_dir/\" \"$dest/\""
        return 1
    fi
}

# Merge a full Interface/ tree into the client's Interface/ directory.
# Used for mods (e.g. Paragon) that ship custom Interface subfolders
# rather than a self-contained AddOns entry.
# Usage: copy_client_interface <src_interface_dir> [description]
copy_client_interface() {
    local src_dir="$1" desc="${2:-Interface files}"
    if ! detect_wow_client; then
        print_info "Manual install: merge ${CYAN}$src_dir${RST} → <WoW>/Interface/"
        return 1
    fi
    local dest="$WOW_CLIENT_DIR/Interface"
    if [ ! -d "$src_dir" ]; then
        print_warning "Interface source not found: $src_dir"
        print_info "Manual: cp -r \"$src_dir/.\" \"$dest/\""
        return 1
    fi
    mkdir -p "$dest"
    if cp -r "$src_dir/." "$dest/"; then
        print_success "$desc merged → $dest"
        return 0
    else
        print_warning "Copy failed — install manually:"
        print_info "  cp -r \"$src_dir/.\" \"$dest/\""
        return 1
    fi
}

# Copy files from a Data/ source tree into the WoW client's Data/ directory.
# Handles MPQ patches and loose Data files (NOT Interface/ content).
# Usage: copy_client_data <src_data_dir> [description]
# src_data_dir should contain items like Patch-Z.MPQ, enUS/, etc.
copy_client_data() {
    local src_dir="$1" desc="${2:-Data files}"
    if ! detect_wow_client; then
        print_info "Manual install: merge ${CYAN}$src_dir${RST} → <WoW>/Data/"
        return 1
    fi
    local dest="$WOW_CLIENT_DIR/Data"
    if [ ! -d "$src_dir" ]; then
        print_warning "Data source not found: $src_dir"
        print_info "Manual: cp -r \"$src_dir/.\" \"$dest/\""
        return 1
    fi
    mkdir -p "$dest"
    if cp -r "$src_dir/." "$dest/"; then
        print_success "$desc copied → $dest"
        return 0
    else
        print_warning "Copy failed — install manually:"
        print_info "  cp -r \"$src_dir/.\" \"$dest/\""
        return 1
    fi
}

# Copy custom DBC files into the AzerothCore server data volume.
# The ac-client-data volume is mounted :ro on the worldserver, so docker cp
# into the worldserver container won't work. Instead we spin up a temporary
# alpine container with the volume mounted read-write, copy the files, then
# remove the helper container.
# Usage: copy_server_dbc <src_dbc_dir> [description]
copy_server_dbc() {
    local src_dir="$1" desc="${2:-DBC files}"
    if [ ! -d "$src_dir" ]; then
        print_warning "DBC source not found: $src_dir"
        return 1
    fi
    local -a _dbc_files=("$src_dir"/*.dbc)
    if [ ! -f "${_dbc_files[0]}" ]; then
        print_warning "No .dbc files found in: $src_dir"
        return 1
    fi
    print_info "Detecting data volume name..."
    # Inspect worldserver mounts to find the ac-client-data volume name
    local _vol_name=""
    if [ -n "$WORLD_CONTAINER" ]; then
        _vol_name=$(docker inspect "$WORLD_CONTAINER" \
            --format '{{range .Mounts}}{{if eq .Destination "/azerothcore/env/dist/data"}}{{.Name}}{{end}}{{end}}' \
            2>/dev/null)
    fi
    # Fall back to the default volume name if inspect fails
    [ -z "$_vol_name" ] && _vol_name="ac-client-data"
    print_info "Using data volume: $_vol_name"
    # Spin up a temporary alpine container with the volume mounted rw
    local _ok=true
    local f
    for f in "${_dbc_files[@]}"; do
        [ -f "$f" ] || continue
        if ! docker run --rm \
                -v "$_vol_name:/data" \
                -v "$f:/src/$(basename "$f"):ro" \
                alpine \
                cp "/src/$(basename "$f")" "/data/dbc/$(basename "$f")"; then
            print_warning "Failed to copy $(basename "$f") into volume"
            _ok=false
        fi
    done
    if [ "$_ok" = true ]; then
        print_success "$desc installed → $_vol_name:/azerothcore/env/dist/data/dbc/"
        print_info "Restart the worldserver for DBC changes to take effect."
        return 0
    else
        print_warning "Some DBC files failed. Manual steps:"
        print_info "  docker run --rm -v $_vol_name:/data -v \$(pwd):/src alpine \\"
        print_info "    sh -c 'cp /src/*.dbc /data/dbc/'"
        return 1
    fi
}

# Interactive: set or change the WoW client folder.
# Called from the main menu (option 16) and optionally from first-run.
configure_wow_client() {
    echo ""
    print_step "WoW Client Folder"
    if [ -n "$WOW_CLIENT_DIR" ]; then
        echo -e "${WHITE}Current client path:${RST}  ${CYAN}$WOW_CLIENT_DIR${RST}"
        echo ""
        if ! ask_yes_no "Detect/change the WoW client folder?"; then
            return 0
        fi
    fi
    # Clear cached value so detect_wow_client re-probes
    WOW_CLIENT_DIR=""
    local _cache="$SERVER_DIR/.wow_client_dir"
    rm -f "$_cache" 2>/dev/null
    if detect_wow_client; then
        echo ""
        print_success "WoW client folder set to: $WOW_CLIENT_DIR"
        echo -e "${DIM}Path saved — addon auto-install will use this location.${RST}"
    else
        echo ""
        print_warning "No WoW client folder set. Addon auto-install will offer manual paths."
    fi
}

# ── Per-script post-install configuration ────────────────────

configure_ale_battlepass() {
    local clone_dir
    clone_dir=$(ale_script_clone_dir "battlepass")

    print_step "Battle Pass — SQL & Configuration"

    # Apply SQL — track success of both required files
    local _bp_world_ok=false _bp_chars_ok=false
    print_info "Applying Battle Pass SQL (requires database to be running)..."
    if ensure_db_running; then
        ale_run_sql_file "acore_world"      "$clone_dir/sql/battlepass_world.sql"      && _bp_world_ok=true
        ale_run_sql_file "acore_characters" "$clone_dir/sql/battlepass_characters.sql" && _bp_chars_ok=true
        # Create the Battle Pass vendor NPC (entry 90100) — not included in upstream SQL
        if [ "$_bp_world_ok" = true ]; then
            print_info "Battle Pass world SQL applied — NPC will be created at end of configure."
        fi
        if [ "$_bp_world_ok" = false ] || [ "$_bp_chars_ok" = false ]; then
            print_warning "Battle Pass install incomplete — one or more SQL files failed:"
            [ "$_bp_world_ok"  = false ] && print_info "  FAILED: $clone_dir/sql/battlepass_world.sql → acore_world"
            [ "$_bp_chars_ok"  = false ] && print_info "  FAILED: $clone_dir/sql/battlepass_characters.sql → acore_characters"
            print_info "Resolve SQL errors, then reconfigure via ALE Scripts menu → c on Battle Pass."
        fi
    else
        print_warning "Skipping SQL — apply manually when the database is running:"
        print_info "  $clone_dir/sql/battlepass_world.sql      → acore_world"
        print_info "  $clone_dir/sql/battlepass_characters.sql → acore_characters"
    fi

    # Interactive config — only when world schema exists (config table lives in acore_world)
    if [ "$_bp_world_ok" = true ] && container_running "$DB_CONTAINER"; then
        echo ""
        print_info "Configure Battle Pass settings (press ENTER to keep defaults):"
        echo ""
        local enabled max_level exp_per_level exp_scaling npc_entry debug_mode
        printf "${WHITE}  Enable Battle Pass        (1=on/0=off) [1]: ${RST}"; read -r enabled
        printf "${WHITE}  Max Battle Pass level               [100]: ${RST}"; read -r max_level
        printf "${WHITE}  Base XP required per level         [1000]: ${RST}"; read -r exp_per_level
        printf "${WHITE}  XP scaling factor per level         [1.1]: ${RST}"; read -r exp_scaling
        printf "${WHITE}  Battle Pass NPC entry ID          [90100]: ${RST}"; read -r npc_entry
        printf "${WHITE}  Enable debug logging    (0=off/1=on)  [0]: ${RST}"; read -r debug_mode
        enabled=${enabled:-1}
        max_level=${max_level:-100}
        exp_per_level=${exp_per_level:-1000}
        exp_scaling=${exp_scaling:-1.1}
        npc_entry=${npc_entry:-90100}
        debug_mode=${debug_mode:-0}
        # Validate inputs before sending to MySQL
        local _valid=true
        [[ "$enabled"       =~ ^[01]$                  ]] || { print_warning "Invalid enabled value (must be 0 or 1).";       _valid=false; }
        [[ "$max_level"     =~ ^[0-9]+$                ]] || { print_warning "Invalid max_level (must be integer).";          _valid=false; }
        [[ "$exp_per_level" =~ ^[0-9]+$                ]] || { print_warning "Invalid exp_per_level (must be integer).";      _valid=false; }
        [[ "$exp_scaling"   =~ ^[0-9]+([.][0-9]+)?$   ]] || { print_warning "Invalid exp_scaling (must be number e.g. 1.1)"; _valid=false; }
        [[ "$npc_entry"     =~ ^[0-9]+$                ]] || { print_warning "Invalid npc_entry (must be integer).";          _valid=false; }
        [[ "$debug_mode"    =~ ^[01]$                  ]] || { print_warning "Invalid debug_mode (must be 0 or 1).";          _valid=false; }
        if [ "$_valid" = true ]; then
            if docker exec "$DB_CONTAINER" mysql -uroot -p"$DB_ROOT_PASSWORD" acore_world \
                -e "UPDATE battlepass_config SET config_value='$enabled'       WHERE config_key='enabled';
                    UPDATE battlepass_config SET config_value='$max_level'     WHERE config_key='max_level';
                    UPDATE battlepass_config SET config_value='$exp_per_level' WHERE config_key='exp_per_level';
                    UPDATE battlepass_config SET config_value='$exp_scaling'   WHERE config_key='exp_scaling';
                    UPDATE battlepass_config SET config_value='$npc_entry'     WHERE config_key='npc_entry';
                    UPDATE battlepass_config SET config_value='$debug_mode'    WHERE config_key='debug_mode';" \
                2>/dev/null; then
                local found_keys
                found_keys=$(docker exec "$DB_CONTAINER" mysql -uroot -p"$DB_ROOT_PASSWORD" acore_world \
                    -sN -e "SELECT COUNT(*) FROM battlepass_config WHERE config_key IN \
                            ('enabled','max_level','exp_per_level','exp_scaling','npc_entry','debug_mode');" \
                    2>/dev/null)
                if [ "${found_keys:-0}" -ge 6 ]; then
                    print_success "Battle Pass config applied (all 6 keys updated)."
                else
                    print_warning "Only ${found_keys:-0}/6 config keys found in battlepass_config."
                    print_info "The table may be from an older install. Re-run the SQL files, then reconfigure."
                fi
            else
                print_warning "Config update failed — the battlepass_config table may not exist yet."
                print_info "Run the SQL files above first, then reconfigure via option C."
            fi
        else
            print_info "Config not saved due to invalid input. Reconfigure via ALE Scripts menu → c on Battle Pass."
        fi
    fi

    # Client addon
    echo ""
    print_step "Battle Pass — Client Addon"
    echo -e "${WHITE}The Battle Pass system includes a WoW client addon for the in-game UI.${RST}"
    echo ""
    if ask_yes_no "Auto-install BattlePass addon to WoW client now?"; then
        copy_client_addon "$clone_dir/BattlePass" "BattlePass" "BattlePass addon"
    else
        print_info "Manual: cp -r \"$clone_dir/BattlePass\" <WoW>/Interface/AddOns/BattlePass"
    fi
    echo -e "${WHITE}Use ${CYAN}/bp${WHITE} or ${CYAN}/battlepass${WHITE} in WoW chat to open the Battle Pass frame.${RST}"
    echo -e "${WHITE}Server commands: ${CYAN}.bp${WHITE} | ${CYAN}.bp rewards${WHITE} | ${CYAN}.bp claim <level>${WHITE} | ${CYAN}.bp claimall${RST}"
    echo -e "${WHITE}Admin commands:  ${CYAN}.bpadmin addxp${WHITE} | ${CYAN}.bpadmin setlevel${WHITE} | ${CYAN}.bpadmin reset${WHITE} | ${CYAN}.bpadmin reload${RST}"
    # Ensure the NPC creature_template entry exists regardless of whether world SQL succeeded
    echo ""
    fix_battlepass_npc
}

configure_ale_paragon() {
    local clone_dir
    clone_dir=$(ale_script_clone_dir "paragon")
    local paragon_sql_dir="$clone_dir/sql"

    print_step "Paragon Anniversary — SQL Migrations Required"
    echo ""
    echo -e "${WHITE}Paragon Anniversary requires SQL files applied BEFORE first startup.${RST}"
    echo -e "${WHITE}Tables are NOT auto-created — you must run these migrations.${RST}"
    echo ""
    if ensure_db_running; then
        # Collect schema migration files (numbered 0x_*.sql); exclude example/data files
        local -a schema_files=()
        local f
        while IFS= read -r f; do
            local bn; bn=$(basename "$f")
            # Skip date-prefixed example data files (e.g. 11-13-2026_Example_Data.sql)
            if [[ "$bn" =~ ^[0-9]{2}-[0-9]{2} ]] || [[ "$bn" =~ [Ee]xample ]]; then
                continue
            fi
            schema_files+=("$f")
        done < <(find "$paragon_sql_dir" -name "*.sql" 2>/dev/null | sort)

        if [ "${#schema_files[@]}" -gt 0 ]; then
            print_info "Schema migration files (in order):"
            for f in "${schema_files[@]}"; do echo "    - $(basename "$f")"; done
            echo ""
            if ask_yes_no "Apply Paragon schema migrations now?"; then
                local _paragon_schema_ok=true
                # 01_create_database.sql creates the acore_ale DB — run via acore_world
                local db_create="$paragon_sql_dir/01_create_database.sql"
                if [ -f "$db_create" ]; then
                    if ! ale_run_sql_file "acore_world" "$db_create"; then
                        print_warning "Database creation failed — aborting remaining migrations."
                        _paragon_schema_ok=false
                    fi
                fi
                # Remaining migrations run against acore_ale (only if 01 succeeded)
                if [ "$_paragon_schema_ok" = true ]; then
                    for f in "${schema_files[@]}"; do
                        [[ "$(basename "$f")" == "01_create_database.sql" ]] && continue
                        if ! ale_run_sql_file "acore_ale" "$f"; then
                            print_warning "Migration failed — aborting remaining migrations."
                            _paragon_schema_ok=false
                            break
                        fi
                    done
                fi
            else
                local _paragon_schema_ok=false
                print_warning "Apply migrations manually before starting the server:"
                print_info "  mysql acore_world < $paragon_sql_dir/01_create_database.sql"
                for f in "${schema_files[@]}"; do
                    [[ "$(basename "$f")" == "01_create_database.sql" ]] && continue
                    print_info "  mysql acore_ale   < $f"
                done
            fi
            # Offer example/sample data only if schema migrations succeeded
            if [ "${_paragon_schema_ok:-false}" = true ]; then
                local example_file
                example_file=$(find "$paragon_sql_dir" -name "*.sql" 2>/dev/null \
                    | grep -E '[0-9]{2}-[0-9]{2}|[Ee]xample' | sort | head -1)
                if [ -n "$example_file" ]; then
                    echo ""
                    print_info "Optional example data: $(basename "$example_file")"
                    if ask_yes_no "Apply example data to acore_ale? (optional — safe to skip)"; then
                        ale_run_sql_file "acore_ale" "$example_file"
                    fi
                fi
            fi
        else
            print_warning "No schema SQL files found in $paragon_sql_dir"
            print_info "Check the repo's sql/ directory and apply required migrations."
        fi
    else
        print_warning "Database not available. Apply SQL files manually:"
        print_info "  mysql acore_world < $paragon_sql_dir/01_create_database.sql"
        print_info "  mysql acore_ale   < $paragon_sql_dir/02_create_config_tables.sql"
        print_info "  mysql acore_ale   < $paragon_sql_dir/03_create_experience_tables.sql"
        print_info "  mysql acore_ale   < $paragon_sql_dir/04_create_paragon_tables.sql"
        print_info "  mysql acore_ale   < $paragon_sql_dir/05_create_triggers.sql"
        print_info "  mysql acore_ale   < $paragon_sql_dir/06_insert_default_config.sql"
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

    # Client files — Paragon ships a full Interface/ subtree, not just an AddOn
    echo ""
    print_step "Paragon Anniversary — Client Files"
    echo -e "${WHITE}Paragon ships custom Interface files for the in-game progression UI.${RST}"
    echo -e "${DIM}These are merged into your WoW client's Interface/ directory.${RST}"
    echo ""
    if ask_yes_no "Auto-install Paragon client files to WoW Interface now?"; then
        copy_client_interface "$clone_dir/clientside/Interface" "Paragon client files"
    else
        print_info "Manual: cp -r \"$clone_dir/clientside/Interface/.\" <WoW>/Interface/"
    fi
    echo -e "${DIM}Full install guide: $clone_dir/doc/INSTALL.md${RST}"
}

# ─────────────────────────────────────────────────────────────
# SIT MEANS REST CONFIGURATION
# ─────────────────────────────────────────────────────────────

# Generic single-expression sed patcher using a temp-file rewrite.
# Portable across macOS (BSD sed) and Linux (GNU sed).
# Usage: _sed_patch_config <file> <sed_expression> <description>
_sed_patch_config() {
    local file="$1" expr="$2" desc="${3:-config value}"
    local _spc_tmp
    _spc_tmp=$(mktemp "${TMPDIR:-/tmp}/ale_cfg_XXXXXX") || {
        print_warning "  mktemp failed; cannot patch ${desc}."
        return 1
    }
    if sed "$expr" "$file" > "$_spc_tmp" && [ -s "$_spc_tmp" ]; then
        mv "$_spc_tmp" "$file"
        return 0
    fi
    rm -f "$_spc_tmp"
    print_warning "  Could not patch ${desc} in $(basename "$file") — edit manually."
    return 1
}

configure_ale_sitmeanrest() {
    local lua_dir
    lua_dir=$(ale_lua_scripts_dir)
    local deployed="$lua_dir/SitMeansRest.lua"

    print_step "Sit Means Rest — Configuration"

    if [ ! -f "$deployed" ]; then
        print_error "SitMeansRest.lua not found at: $deployed"
        print_info "Install first from the ALE Scripts menu."
        return 1
    fi

    echo ""
    echo -e "${WHITE}Players type ${CYAN}/sit${WHITE} out of combat to receive a regen buff.${RST}"
    echo -e "${WHITE}Moving or standing up removes the buff immediately.${RST}"
    echo ""

    echo -e "${DIM}Current CONFIG values in SitMeansRest.lua:${RST}"
    grep -E "DURATION|REGEN_AURA" "$deployed" | grep -v "^[[:space:]]*--" | sed 's/^/  /'
    echo ""

    # DURATION
    printf "${WHITE}Rest duration in seconds [default 20, ENTER to keep]: ${RST}"
    read -r _smr_dur
    if [[ "$_smr_dur" =~ ^[0-9]+$ ]] && [ "$_smr_dur" -gt 0 ]; then
        _sed_patch_config "$deployed" \
            "s/\(DURATION[[:space:]]*=[[:space:]]*\)[0-9]*/\1${_smr_dur}/" \
            "DURATION" && print_success "  DURATION → ${_smr_dur}s"
    elif [ -n "$_smr_dur" ]; then
        print_warning "  '${_smr_dur}' is not a valid positive integer — keeping current."
    fi

    # REGEN_AURA
    echo ""
    echo -e "${WHITE}Regen aura spell ID applied while resting.${RST}"
    echo -e "${DIM}Default 25990 = Graccu's Fruitcake (restores health + mana for all levels).${RST}"
    printf "${WHITE}Spell ID [ENTER to keep current]: ${RST}"
    read -r _smr_aura
    if [[ "$_smr_aura" =~ ^[0-9]+$ ]] && [ "$_smr_aura" -gt 0 ]; then
        _sed_patch_config "$deployed" \
            "s/\(REGEN_AURA[[:space:]]*=[[:space:]]*\)[0-9]*/\1${_smr_aura}/" \
            "REGEN_AURA" && print_success "  REGEN_AURA → ${_smr_aura}"
    elif [ -n "$_smr_aura" ]; then
        print_warning "  '${_smr_aura}' is not a valid spell ID — keeping current."
    fi

    echo ""
    print_info "No SQL required — drop-in script."
    print_info "Reload with ${CYAN}.reload ale${RST} in-game or restart the worldserver."
}

# ─────────────────────────────────────────────────────────────
# UNLIMITED AMMO CONFIGURATION
# ─────────────────────────────────────────────────────────────

configure_ale_unlimitedammo() {
    local lua_dir
    lua_dir=$(ale_lua_scripts_dir)
    local deployed="$lua_dir/UnlimitedAmmo.lua"

    print_step "Unlimited Ammo — Configuration"

    if [ ! -f "$deployed" ]; then
        print_error "UnlimitedAmmo.lua not found at: $deployed"
        print_info "Install first from the ALE Scripts menu."
        return 1
    fi

    echo ""
    echo -e "${WHITE}Auto-refills arrows/bullets for Hunters when ammo drops below the threshold.${RST}"
    echo -e "${WHITE}Supports all ammo types for bows, guns, and crossbows.${RST}"
    echo -e "${GOLD}The script ships with ENABLED = false — it must be enabled to function.${RST}"
    echo ""

    echo -e "${DIM}Current configuration in UnlimitedAmmo.lua:${RST}"
    grep -E "^UnlimitedAmmoNamespace\.(ENABLED|MAX_AMMO|MIN_AMMO)" "$deployed" | sed 's/^/  /'
    echo ""

    # ENABLED
    if ask_yes_no "Enable Unlimited Ammo by default (set ENABLED = true in the file)?"; then
        _sed_patch_config "$deployed" \
            "s/\(UnlimitedAmmoNamespace\.ENABLED[[:space:]]*=[[:space:]]*\)false/\1true/" \
            "ENABLED" && print_success "  ENABLED → true"
    fi

    # MAX_AMMO
    echo ""
    printf "${WHITE}Maximum ammo to maintain [default 1000, ENTER to keep]: ${RST}"
    read -r _ua_max
    if [[ "$_ua_max" =~ ^[0-9]+$ ]] && [ "$_ua_max" -gt 0 ]; then
        _sed_patch_config "$deployed" \
            "s/\(UnlimitedAmmoNamespace\.MAX_AMMO[[:space:]]*=[[:space:]]*\)[0-9]*/\1${_ua_max}/" \
            "MAX_AMMO" && print_success "  MAX_AMMO → ${_ua_max}"
    elif [ -n "$_ua_max" ]; then
        print_warning "  '${_ua_max}' is not a valid positive integer — keeping current."
    fi

    # MIN_AMMO_THRESHOLD
    echo ""
    printf "${WHITE}Refill threshold — top up when ammo drops below this [default 52, ENTER to keep]: ${RST}"
    read -r _ua_min
    if [[ "$_ua_min" =~ ^[0-9]+$ ]] && [ "$_ua_min" -gt 0 ]; then
        _sed_patch_config "$deployed" \
            "s/\(UnlimitedAmmoNamespace\.MIN_AMMO_THRESHOLD[[:space:]]*=[[:space:]]*\)[0-9]*/\1${_ua_min}/" \
            "MIN_AMMO_THRESHOLD" && print_success "  MIN_AMMO_THRESHOLD → ${_ua_min}"
    elif [ -n "$_ua_min" ]; then
        print_warning "  '${_ua_min}' is not a valid positive integer — keeping current."
    fi

    echo ""
    print_info "GM command ${CYAN}.ua${RST} enables the script at runtime (resets to file default on restart)."
    print_info "No SQL required — drop-in script."
    print_info "Reload with ${CYAN}.reload ale${RST} in-game or restart the worldserver."
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
    local deployed_file="$lua_dir/BMAH.lua"

    print_step "Black Market AH — Configuration"
    echo ""

    # ── Re-deploy base file to pick up latest fixes ──────────
    local _base_src="$clone_dir/guides/wow-wotlk/ALE-Kegs/BlackMarketAuctionHouse/BMAH.lua"
    if [ -f "$_base_src" ]; then
        cp "$_base_src" "$deployed_file" && \
            print_success "Re-deployed base BMAH.lua (latest fixes applied)." || \
            print_warning "Could not re-deploy base file — working with existing deployed copy."
    fi
    # ── Missing file guard ────────────────────────────────────
    if [ ! -f "$deployed_file" ]; then
        print_warning "BMAH.lua not found at:"
        print_info "  $deployed_file"
        print_info "Deploy the script first (install from the ALE Scripts menu), then reconfigure."
        echo ""
        print_step "Black Market AH — Client Addon"
        echo -e "${WHITE}BMAH includes a WoW addon that recreates the Mists of Pandaria BMAH UI.${RST}"
        echo ""
        if ask_yes_no "Auto-install BlackMarketUI addon to WoW client now?"; then
            copy_client_addon "$clone_dir/guides/wow-wotlk/ALE-Kegs/BlackMarketAuctionHouse/Client Files/AddOns/BlackMarketUI" "BlackMarketUI" "BlackMarketUI addon"
        else
            print_info "Manual: cp -r \"$clone_dir/guides/wow-wotlk/ALE-Kegs/BlackMarketAuctionHouse/Client Files/AddOns/BlackMarketUI\" <WoW>/Interface/AddOns/BlackMarketUI"
        fi
        return 1
    fi

    # ── Re-apply BMAH_Up.sql to ensure NPC model is correct ──
    local _bmah_sql="$clone_dir/guides/wow-wotlk/ALE-Kegs/BlackMarketAuctionHouse/sql/BMAH_Up.sql"
    if [ -f "$_bmah_sql" ] && container_running "$DB_CONTAINER"; then
        print_info "Re-applying BMAH_Up.sql (fixes NPC model)..."
        docker exec "$DB_CONTAINER" mysql -uroot -p"$DB_ROOT_PASSWORD" acore_world \
            < "$_bmah_sql" 2>/dev/null && \
            print_success "BMAH SQL applied — NPC model set." || \
            print_warning "BMAH SQL apply failed — check DB container logs."
    fi

    # ── Spawn instructions ────────────────────────────────────
    echo ""
    print_step "Black Market AH — Spawn the Broker NPC"
    echo -e "${WHITE}The BMAH broker NPC (entry 2069430) must be placed in the world.${RST}"
    _offer_npc_in_capitals 2069430 "Black Market Broker" \
        "Run after restarting the worldserver."

    # ── Pricing & timing reference ────────────────────────────
    echo ""
    echo -e "${WHITE}Other configurable values (edit directly in BMAH.lua):${RST}"
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
    print_step "Black Market AH — Client Addon"
    echo -e "${WHITE}BMAH includes a WoW addon that recreates the Mists of Pandaria BMAH UI.${RST}"
    echo ""
    if ask_yes_no "Auto-install BlackMarketUI addon to WoW client now?"; then
        copy_client_addon "$clone_dir/guides/wow-wotlk/ALE-Kegs/BlackMarketAuctionHouse/Client Files/AddOns/BlackMarketUI" "BlackMarketUI" "BlackMarketUI addon"
    else
        print_info "Manual: cp -r \"$clone_dir/guides/wow-wotlk/ALE-Kegs/BlackMarketAuctionHouse/Client Files/AddOns/BlackMarketUI\" <WoW>/Interface/AddOns/BlackMarketUI"
    fi
    echo -e "${WHITE}After installing, run ${CYAN}/reload${WHITE} or restart the WoW client.${RST}"
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

configure_ale_activechat() {
    local lua_dir clone_dir _ac_dest
    lua_dir=$(ale_lua_scripts_dir)
    clone_dir=$(ale_script_clone_dir "activechat")
    _ac_dest="$lua_dir/AzerothChatter"
    print_step "Configuring Azeroth Chatter"
    if [ ! -d "$_ac_dest" ]; then
        print_error "AzerothChatter not yet deployed — install it first (i<num> in ALE Scripts menu)."
        return 1
    fi
    # Re-copy from clone if available
    if [ -d "$clone_dir/AzerothChatter" ]; then
        cp -r "$clone_dir/AzerothChatter"/. "$_ac_dest/"
        print_success "Re-synced from clone."
    fi
    # Fix duplicate-basename collision: ALE detects dupes by basename across all subdirs.
    # data/chatter.lua clashes with logic/chatter.lua; same for context.lua.
    local _fixed=false
    [ -f "$_ac_dest/data/chatter.lua" ] && mv "$_ac_dest/data/chatter.lua" "$_ac_dest/data/chatter_data.lua" && _fixed=true
    [ -f "$_ac_dest/data/context.lua" ] && mv "$_ac_dest/data/context.lua" "$_ac_dest/data/context_data.lua" && _fixed=true
    if $_fixed; then
        find "$_ac_dest" -name "*.lua" -exec \
            sed -i '' \
                -e 's/require("data\.chatter")/require("data.chatter_data")/g' \
                -e "s/require('data\.chatter')/require('data.chatter_data')/g" \
                -e 's/require("data\.context")/require("data.context_data")/g' \
                -e "s/require('data\.context')/require('data.context_data')/g" \
            {} \;
        print_success "Duplicate filename collision fixed (data/chatter→chatter_data, data/context→context_data)."
    else
        print_info "No duplicate files found — already fixed or not present."
    fi
    print_info "Run ${CYAN}.reload ale${RST} in-game to apply."
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
        activechat)
            # Upstream layout: AzerothChatter/ subdirectory
            # ALE detects duplicate basenames across subdirs — data/chatter.lua vs logic/chatter.lua
            # and data/context.lua vs logic/context.lua both collide. Fix: rename data/ files and
            # patch require("data.chatter") → require("data.chatter_data") etc. after copy.
            local src="$clone_dir/AzerothChatter"
            if [ -d "$src" ]; then
                mkdir -p "$lua_dir/AzerothChatter"
                cp -r "$src"/. "$lua_dir/AzerothChatter/"
                local _ac_dest="$lua_dir/AzerothChatter"
                # Rename conflicting data/ files
                [ -f "$_ac_dest/data/chatter.lua"  ] && mv "$_ac_dest/data/chatter.lua"  "$_ac_dest/data/chatter_data.lua"
                [ -f "$_ac_dest/data/context.lua"  ] && mv "$_ac_dest/data/context.lua"  "$_ac_dest/data/context_data.lua"
                # Patch all require("data.chatter") / require("data.context") references
                find "$_ac_dest" -name "*.lua" -exec \
                    sed -i '' \
                        -e 's/require("data\.chatter")/require("data.chatter_data")/g' \
                        -e "s/require('data\.chatter')/require('data.chatter_data')/g" \
                        -e 's/require("data\.context")/require("data.context_data")/g' \
                        -e "s/require('data\.context')/require('data.context_data')/g" \
                    {} \;
                print_success "Deployed → lua_scripts/AzerothChatter/ (duplicate filenames resolved)"
            else
                print_warning "Expected directory not found: $src"
                print_info "Manually copy AzerothChatter/ contents to: $lua_dir/AzerothChatter/"
            fi
            ;;
        battlepass)
            if [ -d "$clone_dir/lua_scripts" ]; then
                cp -r "$clone_dir/lua_scripts/." "$lua_dir/" && \
                    print_success "Deployed → lua_scripts/ (battlepass/ + lib/CSMH)" || \
                    { print_warning "Copy failed — check $clone_dir/lua_scripts"; break; }
                # ALE auto-loads all .ext files BEFORE any .lua scripts run.
                # So by the time 05_BP_Communication.lua executes, RegisterClientRequests
                # and Player:SendServerResponse are already defined by CSMH_SMH.ext.
                # The require("lib.CSMH.CSMH_SMH") call in the upstream file causes
                # double-loading which corrupts CSMH's internal state and crashes the server.
                # Fix: remove the redundant require so CSMH loads exactly once.
                local _comm="$lua_dir/battlepass/05_BP_Communication.lua"
                if [ -f "$_comm" ]; then
                    sed -i 's|^require("lib\.CSMH\.CSMH_SMH")|-- require removed: CSMH_SMH.ext is auto-loaded by ALE before .lua scripts run|' "$_comm"
                    print_success "Patched 05_BP_Communication.lua — removed duplicate require (double-load fix)"
                fi
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
            # ALE-Kegs fork: BMAH.lua lives at the root of BlackMarketAuctionHouse/
            local src="$clone_dir/guides/wow-wotlk/ALE-Kegs/BlackMarketAuctionHouse/BMAH.lua"
            if [ -f "$src" ]; then
                cp "$src" "$lua_dir/" && \
                    print_success "Deployed BMAH.lua → lua_scripts/" || \
                    print_warning "Copy failed — check '$src'"
            else
                print_warning "Expected file not found: $src"
                print_info "Manually copy BMAH.lua to: $lua_dir/"
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
            # Script lives in a subdirectory of the dads-mmo-lab repo
            local sod_src="$clone_dir/guides/wow-wotlk/ALE-Kegs/SeasonOfDiscovery/SOD.lua"
            if [ -f "$sod_src" ]; then
                cp "$sod_src" "$lua_dir/" && \
                    print_success "Deployed SOD.lua → lua_scripts/" || \
                    print_warning "Copy failed — check $sod_src"
                # Remove stale sub-directory copy that causes duplicate-load error
                if [ -f "$lua_dir/sod/SOD.lua" ]; then
                    rm -f "$lua_dir/sod/SOD.lua" && \
                        print_info "Removed stale lua_scripts/sod/SOD.lua (duplicate)"
                fi
            else
                print_warning "SOD.lua not found at expected path: $sod_src"
            fi
            ;;
        sitmeanrest)
            if [ -f "$clone_dir/SitMeansRest.lua" ]; then
                cp "$clone_dir/SitMeansRest.lua" "$lua_dir/" && \
                    print_success "Deployed SitMeansRest.lua → lua_scripts/" || \
                    print_warning "Copy failed"
            else
                print_warning "SitMeansRest.lua not found in $clone_dir"
            fi
            ;;
        unlimitedammo)
            if [ -f "$clone_dir/UnlimitedAmmo.lua" ]; then
                cp "$clone_dir/UnlimitedAmmo.lua" "$lua_dir/" && \
                    print_success "Deployed UnlimitedAmmo.lua → lua_scripts/" || \
                    print_warning "Copy failed"
            else
                print_warning "UnlimitedAmmo.lua not found in $clone_dir"
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
    local key="$1" name="$2" url="$3" branch="${4:-HEAD}"
    local clone_dir
    clone_dir=$(ale_script_clone_dir "$key")

    print_step "Installing ALE script: $name"

    mkdir -p "$SERVER_DIR/ale_scripts"

    # For scripts sourced from a subfolder of dads-mmo-lab, check for the
    # actual source file rather than relying on .git presence.
    local _dml_src=""
    case "$key" in
        sod)         _dml_src="$clone_dir/guides/wow-wotlk/ALE-Kegs/SeasonOfDiscovery/SOD.lua" ;;
        bmah)        _dml_src="$clone_dir/guides/wow-wotlk/ALE-Kegs/BlackMarketAuctionHouse/BMAH.lua" ;;
    esac

    if [ -n "$_dml_src" ]; then
        # dads-mmo-lab subfolder script
        if [ -f "$_dml_src" ]; then
            print_info "Staged files found — updating from remote if possible..."
            if [ -d "$clone_dir/.git" ]; then
                git -C "$clone_dir" pull --depth=1 origin "$branch" --quiet 2>/dev/null || \
                    print_warning "git pull failed — using existing staged files"
            fi
        else
            # No files yet — sparse clone
            [ -d "$clone_dir" ] && rm -rf "$clone_dir"
            local _sparse_path
            case "$key" in
                sod)         _sparse_path="guides/wow-wotlk/ALE-Kegs/SeasonOfDiscovery" ;;
                bmah)        _sparse_path="guides/wow-wotlk/ALE-Kegs/BlackMarketAuctionHouse" ;;
            esac
            mkdir -p "$clone_dir"
            if ! git -C "$clone_dir" init -q || \
               ! git -C "$clone_dir" remote add origin "$url"; then
                rm -rf "$clone_dir"
                print_error "Clone init failed for $name!"
                return 1
            fi
            git -C "$clone_dir" config core.sparseCheckout true
            mkdir -p "$clone_dir/.git/info"
            printf '%s/\n' "$_sparse_path" > "$clone_dir/.git/info/sparse-checkout"
            if ! git -C "$clone_dir" pull --depth=1 origin "$branch" --quiet; then
                rm -rf "$clone_dir"
                print_error "Sparse fetch failed for $name!"
                print_info "Ensure the files have been pushed to: $url"
                return 1
            fi
            print_success "Fetched $name"
        fi
    elif ale_script_is_installed "$key"; then
        print_info "Already cloned — pulling latest..."
        (cd "$clone_dir" && git pull --depth=1 origin "$branch" --quiet 2>/dev/null) || \
            print_warning "git pull failed — using existing copy"
    else
        if [ -d "$clone_dir" ] && [ ! -d "$clone_dir/.git" ]; then
            print_warning "Removing incomplete clone at $clone_dir"
            rm -rf "$clone_dir"
        fi
        if ! git clone --depth 1 "$url" "$clone_dir"; then
            rm -rf "$clone_dir"
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
            print_warning "Accountwide REQUIRES a characters DB schema — the system will not work without it."
            if ask_yes_no "Apply Accountwide characters SQL now? (required)"; then
                local sql_file="$clone_dir/sql/create_accountwide_tables.sql"
                if ensure_db_running; then
                    if [ -f "$sql_file" ]; then
                        ale_run_sql_file "acore_characters" "$sql_file"
                    else
                        print_warning "Expected SQL file not found: $sql_file"
                        print_info "Locate create_accountwide_tables.sql in $clone_dir/sql/ and apply manually:"
                        print_info "  mysql acore_characters < <path/to/create_accountwide_tables.sql>"
                    fi
                else
                    print_warning "Database not available. Apply SQL manually when DB is running:"
                    print_info "  mysql acore_characters < $sql_file"
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
        battlepass)
            echo ""
            print_warning "Battle Pass requires SQL applied before the server starts — tables must exist."
            if ask_yes_no "Apply Battle Pass SQL and configure settings now?"; then
                configure_ale_battlepass
            else
                print_warning "SQL not applied. Apply manually before running the server:"
                print_info "  acore_world:      $clone_dir/sql/battlepass_world.sql"
                print_info "  acore_characters: $clone_dir/sql/battlepass_characters.sql"
                print_info "Reconfigure anytime from the ALE Scripts menu → c on Battle Pass."
                echo ""
                fix_battlepass_npc
            fi
            echo ""
            print_info "Battle Pass Ticker (entry 90100) needs to be placed in the world."
            _offer_npc_in_capitals 90100 "Battle Pass Ticker" \
                "Run after reloading ALE scripts or restarting the worldserver."
            ;;
        paragon)
            echo ""
            print_warning "Paragon Anniversary requires SQL migrations before first use — server will crash without them."
            if ask_yes_no "Apply Paragon SQL migrations and view configuration guide now?"; then
                configure_ale_paragon
            else
                print_warning "SQL not applied. Paragon will not function until these files are run:"
                print_info "  Apply all .sql files (in order) from: $clone_dir/sql/"
                print_info "  Note: 01_create_database.sql creates the acore_ale database."
                print_info "Reconfigure anytime from the ALE Scripts menu → c on Paragon."
            fi
            echo ""
            print_step "Paragon Anniversary — Client Files"
            echo -e "${WHITE}Paragon ships custom Interface files for the in-game progression UI.${RST}"
            if ask_yes_no "Auto-install Paragon client files to WoW Interface now?"; then
                copy_client_interface "$clone_dir/clientside/Interface" "Paragon client files"
            else
                print_info "Manual: cp -r \"$clone_dir/clientside/Interface/.\" <WoW>/Interface/"
            fi
            ;;
        bmah)
            echo ""
            # Apply world SQL (creates creature_template, model, and npc_text entries)
            local _bmah_sql="$clone_dir/guides/wow-wotlk/ALE-Kegs/BlackMarketAuctionHouse/sql/BMAH_Up.sql"
            if [ -f "$_bmah_sql" ]; then
                print_step "BMAH — Applying NPC setup SQL (acore_world)..."
                if ale_run_sql_file "acore_world" "$_bmah_sql"; then
                    print_success "BMAH NPC (entry 2069430) created/updated in acore_world."
                    echo ""
                    print_warning "⚠  The worldserver MUST be restarted for the new creature template to load."
                    print_info "AC does not hot-load new creature_template rows — a reload is not sufficient."
                    print_info "Restart command:  docker restart \$WORLD_CONTAINER  (or use Main menu → Restart Server)"
                    print_info "After restart:    .npc add 2069430    (in-game, as GM)"
                    print_info "Verify state:     whisper 'bmah_diag' to yourself in-game"
                else
                    print_warning "SQL failed — run manually:"
                    print_info "  docker exec -i \"\$DB_CONTAINER\" mysql -uroot -p\"\$DB_ROOT_PASSWORD\" acore_world < \"$_bmah_sql\""
                fi
            else
                print_warning "BMAH_Up.sql not found — NPC may not be gossip-enabled."
                print_info "Manual: UPDATE creature_template SET npcflag = npcflag | 1, faction = 35 WHERE entry = 2069430;"
            fi
            echo ""
            print_step "BMAH — Client Addon"
            echo -e "${WHITE}BMAH includes the BlackMarketUI addon for the in-game auction UI.${RST}"
            if ask_yes_no "Auto-install BlackMarketUI addon to WoW client now?"; then
                copy_client_addon "$clone_dir/guides/wow-wotlk/ALE-Kegs/BlackMarketAuctionHouse/Client Files/AddOns/BlackMarketUI" "BlackMarketUI" "BlackMarketUI addon"
            else
                print_info "Manual: cp -r \"$clone_dir/guides/wow-wotlk/ALE-Kegs/BlackMarketAuctionHouse/Client Files/AddOns/BlackMarketUI\" <WoW>/Interface/AddOns/BlackMarketUI"
            fi
            echo ""
            if ask_yes_no "Configure BMAH (NPC IDs, prices, etc.) now?"; then
                configure_ale_bmah
            fi
            echo ""
            print_info "Black Market AH Auctioneer (entry 2069430) needs to be placed in the world."
            _offer_npc_in_capitals 2069430 "Black Market AH Auctioneer" \
                "Only works AFTER worldserver restart post-SQL. Use 'bmah_diag' whisper to verify state."
            ;;
        sod)
            echo ""
            print_step "Season of Discovery — File Installation"
            local _sod_base="$clone_dir/guides/wow-wotlk/ALE-Kegs/SeasonOfDiscovery"
            # ── Server DBC files ─────────────────────────────────
            echo -e "${WHITE}SoD uses custom spells (IDs 80865-80870) that require server DBC files.${RST}"
            echo ""
            if ask_yes_no "Auto-install SoD DBC files to worldserver now?"; then
                copy_server_dbc "$_sod_base/Server Files/dbc" "SoD server DBC files"
            else
                print_info "Manual: copy Server Files/dbc/*.dbc into your server's dbc/ folder"
                print_info "  docker cp <file>.dbc $WORLD_CONTAINER:/azerothcore/env/dist/data/dbc/"
            fi
            echo ""
            # ── Client Data files (MPQ patches) ──────────────────
            echo -e "${WHITE}SoD also requires client-side MPQ patches for spell visuals and icons.${RST}"
            echo -e "${DIM}Files: Patch-Z.MPQ, enUS/patch-enUS-3.MPQ${RST}"
            echo ""
            if ask_yes_no "Auto-install SoD client Data files to WoW client now?"; then
                copy_client_data "$_sod_base/Client Files/data" "SoD client data (MPQ patches)"
            else
                print_info "Manual: copy Client Files/data/ contents → <WoW>/Data/"
                print_info "  Patch-Z.MPQ → <WoW>/Data/Patch-Z.MPQ"
                print_info "  enUS/patch-enUS-3.MPQ → <WoW>/Data/enUS/patch-enUS-3.MPQ"
            fi
            echo ""
            # ── Client Interface icon ─────────────────────────────
            echo -e "${WHITE}SoD also includes a custom buff icon for the client.${RST}"
            if ask_yes_no "Auto-install SoD buff icon to WoW Interface/Icons/?"; then
                copy_client_interface "$_sod_base/Client Files/Interface" "SoD buff icon"
            else
                print_info "Manual: copy Client Files/Interface/Icons/Buff_SoD.blp → <WoW>/Interface/Icons/"
            fi
            ;;
        sitmeanrest)
            echo ""
            if ask_yes_no "Configure Sit Means Rest (duration, regen spell) now?"; then
                configure_ale_sitmeanrest
            else
                print_info "Reconfigure anytime from the ALE Scripts menu → c on Sit Means Rest."
            fi
            ;;
        unlimitedammo)
            echo ""
            print_warning "The script ships with ENABLED = false — configure to activate it."
            if ask_yes_no "Configure Unlimited Ammo (enable + ammo thresholds) now?"; then
                configure_ale_unlimitedammo
            else
                print_info "Reconfigure anytime from the ALE Scripts menu → c on Unlimited Ammo."
                print_info "Or use the in-game GM command ${CYAN}.ua${RST} to enable at runtime."
            fi
            ;;
        levelupreward)
            echo ""
            print_warning "Level Up Reward writes prestige tokens to acore_characters.account_prestige."
            print_warning "This table MUST exist before the server starts — missing it causes a hard crash."
            local _lur_sql="CREATE TABLE IF NOT EXISTS \`account_prestige\` (\`account_id\` INT UNSIGNED NOT NULL, \`prestige_tokens\` INT UNSIGNED NOT NULL DEFAULT 0, PRIMARY KEY (\`account_id\`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;"
            if ensure_db_running; then
                if sqlmod_run_sql "acore_characters" "$_lur_sql" >/dev/null 2>&1; then
                    print_success "account_prestige table created in acore_characters."
                else
                    print_error "Failed to create account_prestige — apply manually:"
                    print_info "  USE acore_characters;"
                    print_info "  $_lur_sql"
                fi
            else
                print_warning "Database not running. Apply this SQL manually before starting the server:"
                print_info "  USE acore_characters;"
                print_info "  $_lur_sql"
            fi
            ;;
    esac

    echo ""
    print_info "Reload Lua scripts in-game with: ${CYAN}.reload ale${RST}"
    print_info "Or restart the worldserver from the main menu."
    upsert_mod_commands "$key"
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
        levelupreward)
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
        activechat)  deployed_hint="$lua_dir/AzerothChatter/" ;;
        battlepass)  deployed_hint="$lua_dir/battlepass/  and  $lua_dir/lib/CSMH/" ;;
        paragon)     deployed_hint="$lua_dir/paragon/" ;;
        bmah)        deployed_hint="$lua_dir/BMAH.lua" ;;
        lootpet)     deployed_hint="$lua_dir/LootPet.lua" ;;
        sitmeanrest)  deployed_hint="$lua_dir/SitMeansRest.lua" ;;
        sod)         deployed_hint="$lua_dir/SOD.lua" ;;
        unlimitedammo) deployed_hint="$lua_dir/UnlimitedAmmo.lua" ;;
        *)           deployed_hint="$lua_dir/ (search for files from this script)" ;;
    esac

    echo ""
    print_info "Deployed files: $deployed_hint"
    if ask_yes_no "Also remove deployed Lua files from lua_scripts/?"; then
        case "$key" in
            accountwide) rm -rf "$lua_dir/accountwide" ;;
            activechat)  rm -rf "$lua_dir/AzerothChatter" ;;
            battlepass)  rm -rf "$lua_dir/battlepass" "$lua_dir/lib/CSMH" ;;
            paragon)     rm -rf "$lua_dir/paragon" ;;
            bmah)        rm -f  "$lua_dir/BMAH.lua" ;;
            lootpet)     rm -f  "$lua_dir/LootPet.lua" ;;
            sitmeanrest)   rm -f "$lua_dir/SitMeansRest.lua" ;;
            sod)           rm -f "$lua_dir/SOD.lua" ;;
            unlimitedammo) rm -f "$lua_dir/UnlimitedAmmo.lua" ;;
            levelupreward)
                local f
                for f in "${generic_deployed_files[@]}"; do
                    rm -f "$lua_dir/$f" 2>/dev/null || true
                done
                ;;
        esac
        print_success "Deployed files removed."
    fi

    print_info "(Database tables created by this script are kept — removing them risks data loss.)"
    remove_mod_commands "$key"
}

# ─────────────────────────────────────────────────────────────
# SQL MODS — helpers
# ─────────────────────────────────────────────────────────────

SQLMOD_BASE_DIR=""
SQLMOD_MARKER_DIR=""
SQLMOD_CLONE_DIR=""
SQLMOD_CONFIG_DIR=""
SQLMOD_BACKUP_DIR=""

sqlmod_init() {
    [ -n "$SQLMOD_BASE_DIR" ] && return 0   # already initialised
    SQLMOD_BASE_DIR="$SERVER_DIR/sql_scripts"
    SQLMOD_MARKER_DIR="$SQLMOD_BASE_DIR/installed"
    SQLMOD_CLONE_DIR="$SQLMOD_BASE_DIR/clones"
    SQLMOD_CONFIG_DIR="$SQLMOD_BASE_DIR/config"
    SQLMOD_BACKUP_DIR="$SQLMOD_BASE_DIR/backups"
    mkdir -p "$SQLMOD_MARKER_DIR" "$SQLMOD_CLONE_DIR" "$SQLMOD_CONFIG_DIR" "$SQLMOD_BACKUP_DIR"
}

sqlmod_is_installed() {
    local key="$1"
    sqlmod_init
    local entry t
    for entry in "${SQL_MOD_REGISTRY[@]}"; do
        IFS='|' read -r k _ _ t <<< "$entry"
        if [ "$k" = "$key" ] && [ "$t" = "conf_module" ]; then
            [ -d "$SERVER_DIR/modules/$key/.git" ] && return 0 || return 1
        fi
    done
    [ -f "$SQLMOD_MARKER_DIR/$key.installed" ]
}

sqlmod_backup_world() {
    sqlmod_init
    if ! container_running "$DB_CONTAINER"; then
        print_error "Database container is not running. Cannot back up."
        return 1
    fi
    local ts; ts=$(date +%Y%m%d_%H%M%S)
    local bfile="$SQLMOD_BACKUP_DIR/${ts}_acore_world.sql.gz"
    print_info "Backing up acore_world → $(basename "$bfile") ..."
    if ( set -o pipefail
         docker exec "$DB_CONTAINER" mysqldump -uroot -p"$DB_ROOT_PASSWORD" acore_world \
             2>/dev/null | gzip > "$bfile"
    ); then
        print_success "Backup saved: $bfile"
        _prune_backup_files "$SQLMOD_BACKUP_DIR" "*_acore_world.sql.gz" 2
        return 0
    else
        rm -f "$bfile"
        print_error "Backup failed! Aborting."
        return 1
    fi
}

sqlmod_run_sql() {
    local db="$1" sql="$2"
    docker exec "$DB_CONTAINER" mysql -uroot -p"$DB_ROOT_PASSWORD" "$db" -e "$sql" 2>&1
}

sqlmod_run_sql_file() {
    local db="$1" filepath="$2"
    docker exec -i "$DB_CONTAINER" mysql -uroot -p"$DB_ROOT_PASSWORD" "$db" \
        < "$filepath" 2>&1
}

# ── Clone helper ──────────────────────────────────────────────
_sqlmod_clone_or_update() {
    local key="$1" url="$2"
    local target="$SQLMOD_CLONE_DIR/$key"
    if [ -d "$target/.git" ]; then
        print_info "Updating $key..."
        git -C "$target" pull --ff-only -q 2>&1 || true
    else
        print_info "Cloning $key..."
        if [ -d "$target" ]; then
            print_warning "Removing incomplete clone at $target"
            rm -rf "$target"
        fi
        git clone --depth=1 -q "$url" "$target" 2>&1 || {
            rm -rf "$target"
            print_error "Clone failed for $url"
            return 1
        }
    fi
}

# ── Install dispatcher ────────────────────────────────────────
sqlmod_install() {
    local key="$1" name="$2" url="$3" type="$4"
    sqlmod_init

    # These types don't touch the DB — handle separately
    case "$type" in
        conf_xp)     configure_sqlmod_xprates; return 0 ;;
        conf_module) _sqlmod_install_conf_module "$key" "$name" "$url"; return ;;
    esac

    if sqlmod_is_installed "$key"; then
        print_warning "$name is already installed."
        return 0
    fi

    if ! container_running "$DB_CONTAINER"; then
        print_error "Database container ($DB_CONTAINER) is not running."
        print_info "Start the server first, then retry."
        return 1
    fi

    print_header
    printf "  ${YELLOW}⚠  WARNING: SQL Mods modify the world database.${RST}\n"
    printf "  ${DIM}These changes are unlikely to corrupt the server, but a backup${RST}\n"
    printf "  ${DIM}will be taken automatically before proceeding.${RST}\n\n"

    if ! ask_yes_no "Install $name?"; then return 0; fi

    sqlmod_backup_world || return 1
    echo ""

    case "$type" in
        clone_sql|clone_sql_norevert) _sqlmod_install_clone_sql "$key" "$name" "$url" ;;
        clone_sql_pick)               _sqlmod_install_hearthstone "$key" "$name" "$url" ;;
        clone_dist)                   _sqlmod_install_clone_dist "$key" "$name" "$url" ;;
        tweak_world)                  _sqlmod_install_tweak "$key" "$name" ;;
    esac
}

_sqlmod_install_clone_sql() {
    local key="$1" name="$2" url="$3"
    _sqlmod_clone_or_update "$key" "$url" || return 1
    local clone_dir="$SQLMOD_CLONE_DIR/$key"
    local up_sql="" tmp_sql

    case "$key" in
        portals-capitals)
            up_sql="$clone_dir/portals-in-all-capitals.up.sql"
            local cfg_file="$SQLMOD_CONFIG_DIR/portals-capitals.conf"
            if [ -f "$cfg_file" ]; then
                # shellcheck source=/dev/null
                source "$cfg_file"
                local go_tpl="${PORTALS_GO_TEMPLATE:-500000}"
                local go_spn="${PORTALS_GO_SPAWN:-2000000}"
                tmp_sql="$SQLMOD_CLONE_DIR/${key}_configured.sql"
                sed \
                    -e "s/SET @GO_TEMPLATE = [0-9]*/SET @GO_TEMPLATE = $go_tpl/" \
                    -e "s/SET @GO_SPAWN = [0-9]*/SET @GO_SPAWN = $go_spn/" \
                    "$up_sql" > "$tmp_sql"
                up_sql="$tmp_sql"
            fi
            ;;
        rare-drops)
            up_sql="$clone_dir/data/sql/db-world/updates/mod rare drops final.sql"
            ;;
        lvl1-mounts)
            up_sql="$clone_dir/level-one-mounts.sql"
            ;;
        all-stackables)
            up_sql="$clone_dir/All_Stackables_200_Up.sql"
            local cfg_file="$SQLMOD_CONFIG_DIR/all-stackables.conf"
            if [ -f "$cfg_file" ]; then
                # shellcheck source=/dev/null
                source "$cfg_file"
                local stack="${STACKABLES_SIZE:-200}"
                tmp_sql="$SQLMOD_CLONE_DIR/${key}_configured.sql"
                sed \
                    -e "s/stackable=[0-9]*/stackable=$stack/g" \
                    -e "s/maxcount=[0-9]*/maxcount=$stack/g" \
                    "$up_sql" > "$tmp_sql"
                up_sql="$tmp_sql"
            fi
            ;;
    esac

    if [ -z "$up_sql" ] || [ ! -f "$up_sql" ]; then
        print_error "Could not find install SQL for $name"
        return 1
    fi

    print_info "Applying SQL to acore_world..."
    if sqlmod_run_sql_file "acore_world" "$up_sql"; then
        touch "$SQLMOD_MARKER_DIR/$key.installed"
        print_success "$name installed successfully!"
        upsert_mod_commands "$key"
    else
        print_error "SQL apply failed for $name"
        return 1
    fi
}

_sqlmod_install_hearthstone() {
    local key="$1" name="$2" url="$3"
    _sqlmod_clone_or_update "$key" "$url" || return 1
    local clone_dir="$SQLMOD_CLONE_DIR/$key"

    local cfg_file="$SQLMOD_CONFIG_DIR/hearthstone-cd.conf"
    local cooldown_choice="30min"
    if [ -f "$cfg_file" ]; then
        # shellcheck source=/dev/null
        source "$cfg_file"
        cooldown_choice="${HEARTHSTONE_COOLDOWN:-30min}"
    fi

    local sql_file
    case "$cooldown_choice" in
        1sec|1s)   sql_file="$clone_dir/Hearthstone_1_Sec.sql" ;;
        1min)      sql_file="$clone_dir/Hearthstone_1_Min.sql" ;;
        5min)      sql_file="$clone_dir/Hearthstone_5_Min.sql" ;;
        15min)     sql_file="$clone_dir/Hearthstone_15_Min.sql" ;;
        30min|*)   sql_file="$clone_dir/Hearthstone_30_Min.sql" ;;
    esac

    print_info "Applying Hearthstone cooldown ($cooldown_choice) to acore_world..."
    if sqlmod_run_sql_file "acore_world" "$sql_file"; then
        echo "HEARTHSTONE_COOLDOWN=$cooldown_choice" > "$SQLMOD_MARKER_DIR/$key.installed"
        print_success "$name installed ($cooldown_choice)!"
        upsert_mod_commands "$key"
    else
        print_error "SQL apply failed"
        return 1
    fi
}

_sqlmod_install_clone_dist() {
    local key="$1" name="$2" url="$3"
    _sqlmod_clone_or_update "$key" "$url" || return 1
    local clone_dir="$SQLMOD_CLONE_DIR/$key"

    local cfg_file="$SQLMOD_CONFIG_DIR/npc-teleporter.conf"
    local ony_level=60 install_capital=true install_startzone=true
    if [ -f "$cfg_file" ]; then
        # shellcheck source=/dev/null
        source "$cfg_file"
        ony_level="${NPC_TELEPORTER_ONY_LEVEL:-60}"
        install_capital="${NPC_TELEPORTER_CAPITAL:-true}"
        install_startzone="${NPC_TELEPORTER_STARTZONE:-true}"
    fi

    local dist_dir="$clone_dir/data/sql/db-world"
    local applied=false
    if [ "$install_capital" = "true" ] && [ -f "$dist_dir/teleporter_capital.dist" ]; then
        local cap_sql="$SQLMOD_CLONE_DIR/${key}_capital.sql"
        sed "s/@ONY_LEVEL := [0-9]*/@ONY_LEVEL := $ony_level/" \
            "$dist_dir/teleporter_capital.dist" > "$cap_sql"
        print_info "Applying capital teleporter SQL (ONY_LEVEL=$ony_level)..."
        sqlmod_run_sql_file "acore_world" "$cap_sql" || {
            print_error "Capital teleporter SQL failed"; return 1
        }
        applied=true
    fi

    if [ "$install_startzone" = "true" ] && [ -f "$dist_dir/teleporter_starting_zone.dist" ]; then
        local sz_sql="$SQLMOD_CLONE_DIR/${key}_startzone.sql"
        sed "s/@ONY_LEVEL := [0-9]*/@ONY_LEVEL := $ony_level/" \
            "$dist_dir/teleporter_starting_zone.dist" > "$sz_sql"
        print_info "Applying starting zone teleporter SQL..."
        sqlmod_run_sql_file "acore_world" "$sz_sql" || {
            print_error "Starting zone teleporter SQL failed"; return 1
        }
        applied=true
    fi

    if ! $applied; then
        print_warning "No SQL was applied — both capital and starting zone NPCs are disabled in config."
        return 1
    fi

    touch "$SQLMOD_MARKER_DIR/$key.installed"
    print_success "$name installed successfully!"
    upsert_mod_commands "$key"
}

_sqlmod_install_conf_module() {    local key="$1" name="$2" url="$3"
    local module_dir="$SERVER_DIR/modules/$key"
    if [ -d "$module_dir/.git" ]; then
        print_info "Updating $key module..."
        git -C "$module_dir" pull --ff-only -q 2>&1 || true
    else
        print_info "Cloning $key into modules/..."
        if [ -d "$module_dir" ]; then
            print_warning "Removing incomplete clone at $module_dir"
            rm -rf "$module_dir"
        fi
        git clone --depth=1 -q "$url" "$module_dir" 2>&1 || {
            rm -rf "$module_dir"
            print_error "Clone failed"; return 1
        }
    fi

    local conf_dist="$module_dir/conf/mod_customlogin.conf.dist"
    local conf_dest="$SERVER_DIR/env/dist/etc/modules/mod_customlogin.conf"
    if [ -f "$conf_dist" ] && [ ! -f "$conf_dest" ]; then
        mkdir -p "$(dirname "$conf_dest")"
        cp "$conf_dist" "$conf_dest"
        print_success "Created default config: $conf_dest"
    fi

    print_success "$name cloned to modules/!"
    print_warning "A worldserver rebuild is required to activate this module."
    print_info "Use 'Rebuild worldserver' from the main menu to compile."
    upsert_mod_commands "$key"
}

_sqlmod_install_tweak() {
    local key="$1" name="$2"
    local cfg_file="$SQLMOD_CONFIG_DIR/${key}.conf"
    local h_mult d_mult a_mult spd_mult

    # Mob tweaks are mutually exclusive — auto-remove any active sibling first
    local siblings=("buff-mobs" "xbuff-mobs" "nerf-mobs" "baby-mobs")
    for sibling in "${siblings[@]}"; do
        if [ "$sibling" != "$key" ] && [ -f "$SQLMOD_MARKER_DIR/$sibling.installed" ]; then
            print_warning "Removing conflicting tweak '$sibling' before applying '$key'..."
            _sqlmod_remove_tweak "$sibling" "$sibling" || {
                print_error "Failed to remove '$sibling'. Aborting to avoid stacking tweaks."
                return 1
            }
        fi
    done

    case "$key" in
        buff-mobs)  h_mult=2;    d_mult=1.5;  a_mult=1.5;  spd_mult=0.8 ;;
        xbuff-mobs) h_mult=4;    d_mult=2;    a_mult=2;    spd_mult=0.5 ;;
        nerf-mobs)  h_mult=0.5;  d_mult=0.75; a_mult=0.75; spd_mult=1.2 ;;
        baby-mobs)  h_mult=0.25; d_mult=0.25; a_mult=0.25; spd_mult=1.5 ;;
    esac

    if [ -f "$cfg_file" ]; then
        # shellcheck source=/dev/null
        source "$cfg_file"
        h_mult="${TWEAK_HP_MULT:-$h_mult}"
        d_mult="${TWEAK_DMG_MULT:-$d_mult}"
        a_mult="${TWEAK_ARM_MULT:-$a_mult}"
        spd_mult="${TWEAK_SPD_MULT:-$spd_mult}"
    fi

    print_info "Applying $name (HP×${h_mult} / DMG×${d_mult} / ARM×${a_mult} / SPD×${spd_mult})..."
    local sql
    sql="UPDATE creature_template SET HealthModifier = HealthModifier * ${h_mult};"
    sql+=" UPDATE creature_template SET DamageModifier = DamageModifier * ${d_mult};"
    sql+=" UPDATE creature_template SET BaseAttackTime = BaseAttackTime * ${spd_mult}, RangeAttackTime = RangeAttackTime * ${spd_mult};"
    sql+=" UPDATE creature_template SET ArmorModifier = ArmorModifier * ${a_mult};"

    if sqlmod_run_sql "acore_world" "$sql"; then
        printf 'APPLIED_HP_MULT=%s\nAPPLIED_DMG_MULT=%s\nAPPLIED_ARM_MULT=%s\nAPPLIED_SPD_MULT=%s\n' \
            "$h_mult" "$d_mult" "$a_mult" "$spd_mult" > "$SQLMOD_MARKER_DIR/$key.installed"
        print_success "$name applied!"
        print_warning "Applying again stacks multipliers. Use Remove to reverse."
        upsert_mod_commands "$key"
    else
        print_error "SQL failed for $name"
        return 1
    fi
}

# ── Remove dispatcher ─────────────────────────────────────────
sqlmod_remove() {
    local key="$1" name="$2" url="$3" type="$4"
    sqlmod_init

    case "$type" in
        conf_module)
            if ! sqlmod_is_installed "$key"; then
                print_warning "$name is not installed."; return 0
            fi
            if ! ask_yes_no "Remove $name module directory? A rebuild will be required."; then return 0; fi
            rm -rf "$SERVER_DIR/modules/$key"
            print_success "$name removed. Rebuild worldserver to deactivate."
            remove_mod_commands "$key"
            return 0
            ;;
        conf_xp)
            if ! sqlmod_is_installed "$key"; then
                print_warning "No XP rate customization is applied."; return 0
            fi
            if ! ask_yes_no "Reset XP rates to 1.0 (server default)?"; then return 0; fi
            _sqlmod_remove_xprates
            return 0
            ;;
    esac

    if ! sqlmod_is_installed "$key"; then
        print_warning "$name is not installed."; return 0
    fi
    if ! container_running "$DB_CONTAINER"; then
        print_error "Database container ($DB_CONTAINER) is not running."
        return 1
    fi

    case "$type" in
        clone_sql)
            if ! ask_yes_no "Remove $name? (runs down.sql)"; then return 0; fi
            sqlmod_backup_world || return 1
            _sqlmod_remove_clone_sql "$key" "$name"
            ;;
        clone_sql_norevert)
            print_warning "$name has no automated reversal SQL."
            print_info "To undo: restore a backup from: $SQLMOD_BACKUP_DIR"
            press_enter; return 0
            ;;
        clone_sql_pick)
            if ! ask_yes_no "Remove $name? (Hearthstone resets to 30-min WotLK default)"; then return 0; fi
            sqlmod_backup_world || return 1
            _sqlmod_remove_hearthstone "$key" "$name"
            ;;
        clone_dist)
            if ! ask_yes_no "Remove $name? (NPC deleted from world)"; then return 0; fi
            sqlmod_backup_world || return 1
            _sqlmod_remove_npc_teleporter "$key" "$name"
            ;;
        tweak_world)
            if ! ask_yes_no "Reverse $name? (applies inverse multipliers to creature_template)"; then return 0; fi
            sqlmod_backup_world || return 1
            _sqlmod_remove_tweak "$key" "$name"
            ;;
    esac
}

_sqlmod_remove_clone_sql() {
    local key="$1" name="$2"
    local clone_dir="$SQLMOD_CLONE_DIR/$key"
    local down_sql="" tmp_sql

    case "$key" in
        portals-capitals)
            down_sql="$clone_dir/portals-in-all-capitals.down.sql"
            # Apply same config substitution used at install time
            local cfg_file="$SQLMOD_CONFIG_DIR/portals-capitals.conf"
            if [ -f "$cfg_file" ] && [ -f "$down_sql" ]; then
                # shellcheck source=/dev/null
                source "$cfg_file"
                local go_tpl="${PORTALS_GO_TEMPLATE:-500000}"
                local go_spn="${PORTALS_GO_SPAWN:-2000000}"
                tmp_sql="$SQLMOD_CLONE_DIR/${key}_down_configured.sql"
                sed \
                    -e "s/SET @GO_TEMPLATE = [0-9]*/SET @GO_TEMPLATE = $go_tpl/" \
                    -e "s/SET @GO_SPAWN = [0-9]*/SET @GO_SPAWN = $go_spn/" \
                    "$down_sql" > "$tmp_sql"
                down_sql="$tmp_sql"
            fi
            ;;
        lvl1-mounts)  down_sql="$clone_dir/level-twenty-mounts.sql" ;;
        all-stackables) down_sql="$clone_dir/All_Stackables_200_Down.sql" ;;
    esac

    if [ -z "$down_sql" ] || [ ! -f "$down_sql" ]; then
        print_error "Remove SQL not found for $name (missing clone?)"
        return 1
    fi

    print_info "Applying down.sql for $name..."
    if sqlmod_run_sql_file "acore_world" "$down_sql"; then
        rm -f "$SQLMOD_MARKER_DIR/$key.installed"
        print_success "$name removed!"
        remove_mod_commands "$key"
    else
        print_error "SQL remove failed"
        return 1
    fi
}

_sqlmod_remove_hearthstone() {
    local key="$1" name="$2"
    # Reset to WotLK default: 30 minutes (1800000 ms)
    local sql="UPDATE spell_dbc SET RecoveryTime = 1800000, CategoryRecoveryTime = 1800000 WHERE Id = 8690;"
    print_info "Resetting Hearthstone to 30-minute cooldown..."
    if sqlmod_run_sql "acore_world" "$sql"; then
        rm -f "$SQLMOD_MARKER_DIR/$key.installed"
        print_success "$name removed (30-min cooldown restored)!"
        remove_mod_commands "$key"
    else
        print_error "SQL reset failed"; return 1
    fi
}

_sqlmod_remove_npc_teleporter() {
    local key="$1" name="$2"
    local sql
    sql="DELETE FROM creature WHERE id1 IN (190000, 190001);"
    sql+=" DELETE FROM creature_template WHERE entry IN (190000, 190001);"
    print_info "Removing NPC Teleporter from world..."
    if sqlmod_run_sql "acore_world" "$sql"; then
        rm -f "$SQLMOD_MARKER_DIR/$key.installed"
        print_success "$name removed!"
        remove_mod_commands "$key"
    else
        print_error "SQL removal failed"; return 1
    fi
}

_sqlmod_remove_tweak() {    local key="$1" name="$2"
    local marker_file="$SQLMOD_MARKER_DIR/$key.installed"

    # Known defaults per tweak (fallback if marker lacks APPLIED_* values)
    local def_h def_d def_a def_spd
    case "$key" in
        buff-mobs)  def_h=2;    def_d=1.5;  def_a=1.5;  def_spd=0.8 ;;
        xbuff-mobs) def_h=4;    def_d=2;    def_a=2;    def_spd=0.5 ;;
        nerf-mobs)  def_h=0.5;  def_d=0.75; def_a=0.75; def_spd=1.2 ;;
        baby-mobs)  def_h=0.25; def_d=0.25; def_a=0.25; def_spd=1.5 ;;
        *) def_h=1; def_d=1; def_a=1; def_spd=1 ;;
    esac

    local h_mult="$def_h" d_mult="$def_d" a_mult="$def_a" spd_mult="$def_spd"
    if [ -f "$marker_file" ]; then
        # shellcheck source=/dev/null
        source "$marker_file"
        h_mult="${APPLIED_HP_MULT:-$def_h}"
        d_mult="${APPLIED_DMG_MULT:-$def_d}"
        a_mult="${APPLIED_ARM_MULT:-$def_a}"
        spd_mult="${APPLIED_SPD_MULT:-$def_spd}"
    fi

    local inv_h inv_d inv_a inv_spd
    inv_h=$(awk   "BEGIN{printf \"%.6f\", 1/$h_mult}")
    inv_d=$(awk   "BEGIN{printf \"%.6f\", 1/$d_mult}")
    inv_a=$(awk   "BEGIN{printf \"%.6f\", 1/$a_mult}")
    inv_spd=$(awk "BEGIN{printf \"%.6f\", 1/$spd_mult}")

    print_info "Reversing $name (HP×${inv_h} / DMG×${inv_d} / ARM×${inv_a})..."
    local sql
    sql="UPDATE creature_template SET HealthModifier = HealthModifier * ${inv_h};"
    sql+=" UPDATE creature_template SET DamageModifier = DamageModifier * ${inv_d};"
    sql+=" UPDATE creature_template SET BaseAttackTime = BaseAttackTime * ${inv_spd}, RangeAttackTime = RangeAttackTime * ${inv_spd};"
    sql+=" UPDATE creature_template SET ArmorModifier = ArmorModifier * ${inv_a};"

    if sqlmod_run_sql "acore_world" "$sql"; then
        rm -f "$SQLMOD_MARKER_DIR/$key.installed"
        print_success "$name reversed!"
        remove_mod_commands "$key"
    else
        print_error "SQL failed"; return 1
    fi
}

_sqlmod_remove_xprates() {    local conf_path="$SERVER_DIR/env/dist/etc/worldserver.conf"
    if [ ! -f "$conf_path" ]; then
        print_error "worldserver.conf not found at $conf_path"; return 1
    fi
    local tmpf; tmpf=$(mktemp)
    sed \
        -e 's/^Rate\.XP\.Kill *= *[0-9.]*/Rate.XP.Kill = 1/' \
        -e 's/^Rate\.XP\.Quest *= *[0-9.]*/Rate.XP.Quest = 1/' \
        -e 's/^Rate\.XP\.Explore *= *[0-9.]*/Rate.XP.Explore = 1/' \
        "$conf_path" > "$tmpf" && mv "$tmpf" "$conf_path"
    rm -f "$SQLMOD_MARKER_DIR/xp-rates.installed" 2>/dev/null
    print_success "XP rates reset to 1.0 in worldserver.conf"
    print_info "Run '.reload config' in-game or restart the world server to apply."
}

# ── Reapply helper (remove + reinstall without user prompts) ─
# Used by configure functions to immediately apply config changes.
# Usage: _sqlmod_reapply <key>
_sqlmod_reapply() {
    local key="$1"
    local name="" url="" type=""
    local entry
    for entry in "${SQL_MOD_REGISTRY[@]}"; do
        IFS='|' read -r k n u t <<< "$entry"
        if [ "$k" = "$key" ]; then name="$n"; url="$u"; type="$t"; break; fi
    done
    [ -z "$name" ] && return 1

    if ! sqlmod_is_installed "$key"; then return 0; fi

    if ! container_running "$DB_CONTAINER"; then
        print_error "Database container is not running. Cannot reapply $name."
        print_info "Start the server, then reconfigure to apply changes."
        return 1
    fi

    echo ""
    print_info "Reapplying $name with new configuration..."
    sqlmod_backup_world || return 1

    case "$type" in
        clone_sql)      _sqlmod_remove_clone_sql "$key" "$name" ;;
        clone_sql_pick) _sqlmod_remove_hearthstone "$key" "$name" ;;
        clone_dist)     _sqlmod_remove_npc_teleporter "$key" "$name" ;;
        tweak_world)    _sqlmod_remove_tweak "$key" "$name" ;;
        clone_sql_norevert)
            rm -f "$SQLMOD_MARKER_DIR/$key.installed"
            _sqlmod_install_clone_sql "$key" "$name" "$url"
            return
            ;;
    esac

    case "$type" in
        clone_sql|clone_sql_norevert) _sqlmod_install_clone_sql "$key" "$name" "$url" ;;
        clone_sql_pick)               _sqlmod_install_hearthstone "$key" "$name" "$url" ;;
        clone_dist)                   _sqlmod_install_clone_dist "$key" "$name" "$url" ;;
        tweak_world)                  _sqlmod_install_tweak "$key" "$name" ;;
    esac
}

# ── Configure dispatcher ──────────────────────────────────────
sqlmod_configure() {
    local key="$1" name="$2"
    sqlmod_init
    case "$key" in
        portals-capitals) configure_sqlmod_portals ;;
        all-stackables)   configure_sqlmod_stackables ;;
        npc-teleporter)   configure_sqlmod_npc_teleporter ;;
        hearthstone-cd)   configure_sqlmod_hearthstone ;;
        mod-custom-login) configure_sqlmod_custom_login ;;
        buff-mobs|xbuff-mobs|nerf-mobs|baby-mobs)
                          configure_sqlmod_tweak "$key" "$name" ;;
        xp-rates)         configure_sqlmod_xprates ;;
        *)  print_info "No dedicated configuration for $name." ;;
    esac
}

configure_sqlmod_portals() {
    print_header
    printf "  ${GOLD}── Configure: Portals in All Capitals ──${RST}\n\n"
    printf "  ${DIM}Adjust GO_TEMPLATE/GO_SPAWN base IDs if they conflict with other mods.${RST}\n\n"

    local cfg_file="$SQLMOD_CONFIG_DIR/portals-capitals.conf"
    local cur_tpl=500000 cur_spn=2000000
    if [ -f "$cfg_file" ]; then
        # shellcheck source=/dev/null
        source "$cfg_file"
        cur_tpl="${PORTALS_GO_TEMPLATE:-500000}"
        cur_spn="${PORTALS_GO_SPAWN:-2000000}"
    fi

    printf "  GO_TEMPLATE base ID (current: %s): " "$cur_tpl"; local new_tpl; read -r new_tpl
    printf "  GO_SPAWN base ID    (current: %s): " "$cur_spn"; local new_spn; read -r new_spn
    [ -z "$new_tpl" ] && new_tpl="$cur_tpl"
    [ -z "$new_spn" ] && new_spn="$cur_spn"

    if ! [[ "$new_tpl" =~ ^[0-9]+$ ]] || ! [[ "$new_spn" =~ ^[0-9]+$ ]]; then
        print_error "ID values must be positive integers."; press_enter; return
    fi

    printf 'PORTALS_GO_TEMPLATE=%s\nPORTALS_GO_SPAWN=%s\n' "$new_tpl" "$new_spn" > "$cfg_file"
    print_success "Configuration saved."
    if sqlmod_is_installed "portals-capitals"; then
        _sqlmod_reapply "portals-capitals"
    else
        print_info "Install Portals in All Capitals to apply these settings."
    fi
    press_enter
}

configure_sqlmod_stackables() {
    print_header
    printf "  ${GOLD}── Configure: All Stackables ──${RST}\n\n"
    printf "  ${DIM}Set the maximum stack size. Default is 200.${RST}\n\n"

    local cfg_file="$SQLMOD_CONFIG_DIR/all-stackables.conf"
    local cur_size=200
    if [ -f "$cfg_file" ]; then
        # shellcheck source=/dev/null
        source "$cfg_file"; cur_size="${STACKABLES_SIZE:-200}"
    fi

    printf "  Max stack size (current: %s): " "$cur_size"; local new_size; read -r new_size
    [ -z "$new_size" ] && new_size="$cur_size"
    if ! [[ "$new_size" =~ ^[0-9]+$ ]]; then print_error "Invalid value."; press_enter; return; fi

    printf 'STACKABLES_SIZE=%s\n' "$new_size" > "$cfg_file"
    print_success "Configuration saved (stack size = $new_size)."
    if sqlmod_is_installed "all-stackables"; then
        _sqlmod_reapply "all-stackables"
    else
        print_info "Install All Stackables to apply this setting."
    fi
    press_enter
}

configure_sqlmod_npc_teleporter() {
    print_header
    printf "  ${GOLD}── Configure: NPC Teleporter ──${RST}\n\n"

    local cfg_file="$SQLMOD_CONFIG_DIR/npc-teleporter.conf"
    local cur_ony=60 cur_capital=true cur_startzone=true
    if [ -f "$cfg_file" ]; then
        # shellcheck source=/dev/null
        source "$cfg_file"
        cur_ony="${NPC_TELEPORTER_ONY_LEVEL:-60}"
        cur_capital="${NPC_TELEPORTER_CAPITAL:-true}"
        cur_startzone="${NPC_TELEPORTER_STARTZONE:-true}"
    fi

    printf "  Onyxia Level (60 = Vanilla, 80 = WotLK) [current: %s]: " "$cur_ony"
    local new_ony; read -r new_ony
    [ -z "$new_ony" ] && new_ony="$cur_ony"
    ( [ "$new_ony" = "60" ] || [ "$new_ony" = "80" ] ) || {
        print_error "Must be 60 or 80."; press_enter; return
    }
    printf "  Install Capital Teleporter NPC?      (true/false) [current: %s]: " "$cur_capital"
    local new_capital; read -r new_capital
    [ -z "$new_capital" ] && new_capital="$cur_capital"
    printf "  Install Starting Zone Teleporter NPC? (true/false) [current: %s]: " "$cur_startzone"
    local new_startzone; read -r new_startzone
    [ -z "$new_startzone" ] && new_startzone="$cur_startzone"

    printf 'NPC_TELEPORTER_ONY_LEVEL=%s\nNPC_TELEPORTER_CAPITAL=%s\nNPC_TELEPORTER_STARTZONE=%s\n' \
        "$new_ony" "$new_capital" "$new_startzone" > "$cfg_file"
    print_success "Configuration saved."
    if sqlmod_is_installed "npc-teleporter"; then
        _sqlmod_reapply "npc-teleporter"
    else
        print_info "Install NPC Teleporter to apply these settings."
    fi
    press_enter
}

configure_sqlmod_hearthstone() {
    print_header
    printf "  ${GOLD}── Configure: Hearthstone Cooldowns ──${RST}\n\n"
    printf "  ${DIM}Select the Hearthstone cooldown.${RST}\n\n"
    printf "  1) 30 minutes (WotLK default)\n"
    printf "  2) 15 minutes\n"
    printf "  3) 5 minutes\n"
    printf "  4) 1 minute\n"
    printf "  5) 1 second (instant)\n\n"

    local cfg_file="$SQLMOD_CONFIG_DIR/hearthstone-cd.conf"
    local cur="30min"
    if [ -f "$cfg_file" ]; then
        # shellcheck source=/dev/null
        source "$cfg_file"; cur="${HEARTHSTONE_COOLDOWN:-30min}"
    fi

    printf "  Your choice [current: %s]: " "$cur"; local ans; read -r ans
    local sel
    case "$ans" in
        1) sel="30min" ;; 2) sel="15min" ;; 3) sel="5min" ;;
        4) sel="1min"  ;; 5) sel="1sec"  ;; *) sel="$cur" ;;
    esac
    printf 'HEARTHSTONE_COOLDOWN=%s\n' "$sel" > "$cfg_file"
    print_success "Configuration saved ($sel)."
    if sqlmod_is_installed "hearthstone-cd"; then
        _sqlmod_reapply "hearthstone-cd"
    else
        print_info "Install Hearthstone Cooldown Tweaks to apply this setting."
    fi
    press_enter
}

configure_sqlmod_custom_login() {
    sqlmod_init
    local conf_dest="$SERVER_DIR/env/dist/etc/modules/mod_customlogin.conf"
    if [ ! -f "$conf_dest" ]; then
        local module_dir="$SERVER_DIR/modules/mod-custom-login"
        local conf_dist="$module_dir/conf/mod_customlogin.conf.dist"
        if [ -f "$conf_dist" ]; then
            mkdir -p "$(dirname "$conf_dest")"
            cp "$conf_dist" "$conf_dest"
            print_success "Created $conf_dest"
        else
            print_error "Config not found. Install mod first."; press_enter; return
        fi
    fi
    print_info "Opening mod_customlogin.conf..."
    _open_text_file "$conf_dest"
}

configure_sqlmod_tweak() {
    local key="$1" name="$2"
    print_header
    printf "  ${GOLD}── Configure: %s ──${RST}\n\n" "$name"
    printf "  ${DIM}Adjust creature_template multipliers. Leave blank to keep current.${RST}\n"
    printf "  ${DIM}Values must be positive numbers > 0. Changes apply immediately if installed.${RST}\n\n"

    local def_h def_d def_a def_spd
    case "$key" in
        buff-mobs)  def_h=2;    def_d=1.5;  def_a=1.5;  def_spd=0.8 ;;
        xbuff-mobs) def_h=4;    def_d=2;    def_a=2;    def_spd=0.5 ;;
        nerf-mobs)  def_h=0.5;  def_d=0.75; def_a=0.75; def_spd=1.2 ;;
        baby-mobs)  def_h=0.25; def_d=0.25; def_a=0.25; def_spd=1.5 ;;
    esac

    local cfg_file="$SQLMOD_CONFIG_DIR/${key}.conf"
    local cur_h="$def_h" cur_d="$def_d" cur_a="$def_a" cur_spd="$def_spd"
    if [ -f "$cfg_file" ]; then
        # shellcheck source=/dev/null
        source "$cfg_file"
        cur_h="${TWEAK_HP_MULT:-$def_h}"; cur_d="${TWEAK_DMG_MULT:-$def_d}"
        cur_a="${TWEAK_ARM_MULT:-$def_a}"; cur_spd="${TWEAK_SPD_MULT:-$def_spd}"
    fi

    local new_h new_d new_a new_spd
    printf "  HP multiplier     [current: %s]: " "$cur_h";   read -r new_h
    printf "  Damage multiplier [current: %s]: " "$cur_d";   read -r new_d
    printf "  Armor multiplier  [current: %s]: " "$cur_a";   read -r new_a
    printf "  Attack speed mult [current: %s]: " "$cur_spd"; read -r new_spd
    [ -z "$new_h" ]   && new_h="$cur_h"
    [ -z "$new_d" ]   && new_d="$cur_d"
    [ -z "$new_a" ]   && new_a="$cur_a"
    [ -z "$new_spd" ] && new_spd="$cur_spd"

    # Validate: must be positive numeric literals (regex + value check)
    local invalid=false
    for v in "$new_h" "$new_d" "$new_a" "$new_spd"; do
        if ! [[ "$v" =~ ^[0-9]+([.][0-9]+)?$ ]] || \
            ! awk "BEGIN{exit !($v > 0)}" 2>/dev/null; then invalid=true; fi
    done
    if $invalid; then
        print_error "All multipliers must be positive numbers greater than 0."
        press_enter; return
    fi

    printf 'TWEAK_HP_MULT=%s\nTWEAK_DMG_MULT=%s\nTWEAK_ARM_MULT=%s\nTWEAK_SPD_MULT=%s\n' \
        "$new_h" "$new_d" "$new_a" "$new_spd" > "$cfg_file"
    print_success "Configuration saved."
    if sqlmod_is_installed "$key"; then
        _sqlmod_reapply "$key"
    else
        print_info "Install $name to apply these settings."
    fi
    press_enter
}

configure_sqlmod_xprates() {
    sqlmod_init
    print_header
    printf "  ${GOLD}── Configure: XP Rates ──${RST}\n\n"

    local conf_path="$SERVER_DIR/env/dist/etc/worldserver.conf"
    if [ ! -f "$conf_path" ]; then
        print_error "worldserver.conf not found at $conf_path"
        print_info "Make sure your AzerothCore install is complete."
        press_enter; return
    fi

    local cur_kill cur_quest cur_explore
    cur_kill=$(grep -m1 '^Rate\.XP\.Kill' "$conf_path" | awk -F'=' '{print $2}' | tr -d ' ' 2>/dev/null)
    cur_quest=$(grep -m1 '^Rate\.XP\.Quest' "$conf_path" | awk -F'=' '{print $2}' | tr -d ' ' 2>/dev/null)
    cur_explore=$(grep -m1 '^Rate\.XP\.Explore' "$conf_path" | awk -F'=' '{print $2}' | tr -d ' ' 2>/dev/null)
    [ -z "$cur_kill" ]    && cur_kill="1"
    [ -z "$cur_quest" ]   && cur_quest="1"
    [ -z "$cur_explore" ] && cur_explore="1"

    printf "  ${DIM}File: %s${RST}\n\n" "$conf_path"
    printf "  Kill XP multiplier    [current: %s]: " "$cur_kill";    local new_kill;    read -r new_kill
    printf "  Quest XP multiplier   [current: %s]: " "$cur_quest";   local new_quest;   read -r new_quest
    printf "  Explore XP multiplier [current: %s]: " "$cur_explore"; local new_explore; read -r new_explore
    [ -z "$new_kill" ]    && new_kill="$cur_kill"
    [ -z "$new_quest" ]   && new_quest="$cur_quest"
    [ -z "$new_explore" ] && new_explore="$cur_explore"

    # Validate: must be positive numeric literals (regex + value check)
    local invalid=false
    for v in "$new_kill" "$new_quest" "$new_explore"; do
        if ! [[ "$v" =~ ^[0-9]+([.][0-9]+)?$ ]] || \
            ! awk "BEGIN{exit !($v > 0)}" 2>/dev/null; then invalid=true; fi
    done
    if $invalid; then
        print_error "XP multipliers must be positive numbers greater than 0."
        press_enter; return
    fi

    local tmpf; tmpf=$(mktemp)
    sed \
        -e "s/^Rate\.XP\.Kill *= *[0-9.]*/Rate.XP.Kill = $new_kill/" \
        -e "s/^Rate\.XP\.Quest *= *[0-9.]*/Rate.XP.Quest = $new_quest/" \
        -e "s/^Rate\.XP\.Explore *= *[0-9.]*/Rate.XP.Explore = $new_explore/" \
        "$conf_path" > "$tmpf" && mv "$tmpf" "$conf_path"
    touch "$SQLMOD_MARKER_DIR/xp-rates.installed" 2>/dev/null
    print_success "XP rates saved: Kill=$new_kill  Quest=$new_quest  Explore=$new_explore"
    print_info "Run '.reload config' in-game or restart the world server to apply."
    press_enter
}

# ─────────────────────────────────────────────────────────────
# ABOUT PANELS
# ─────────────────────────────────────────────────────────────

_get_about_text() {
    local key="$1"
    case "$key" in
        mod-ah-bot)
            printf '%s\n' \
                'An Auction House bot that populates faction AH listings' \
                'by automatically placing and bidding on auctions. Assign' \
                'a dedicated bot character via account and character IDs,' \
                'then enable seller, buyer, or both modes. Item quotas per' \
                'quality tier are adjustable in config or the database table.'
            ;;
        mod-solocraft)
            printf '%s\n' \
                'Scales player stats inside dungeons and raids based on group' \
                'size, making solo play viable at appropriate difficulty.' \
                'Provides configurable per-instance difficulty settings, a' \
                'spellpower buff, and an optional debuff for overpowered' \
                'groups. Level thresholds prevent over-buffing characters.'
            ;;
        mod-aoe-loot)
            printf '%s\n' \
                'Enables Area-of-Effect looting -- interacting with one corpse' \
                'automatically collects loot from all nearby corpses in a' \
                'single window. Players toggle per-character via .aoeloot' \
                'on/off. Configurable range, group support, and a 15-item cap' \
                'per window keep server load stable.'
            ;;
        mod-learn-spells)
            printf '%s\n' \
                'Automatically teaches all available class spells on level-up,' \
                'mimicking the Cataclysm auto-learn system. New abilities' \
                'appear in the spellbook the moment a level is reached --' \
                'no more visiting class trainers to unlock new skills.'
            ;;
        mod-individual-progression)
            printf '%s\n' \
                'Simulates a per-player Vanilla to TBC to WotLK content' \
                'journey. NPCs, world objects, and quests phase in based on' \
                'individual progression stage. Catch-up mechanics are removed' \
                'to make each character journey meaningful. Works great' \
                'alongside Playerbots or NPCBots.'
            ;;
        mod-autobalance)
            printf '%s\n' \
                'Dynamically scales dungeon and raid mob health, mana, and' \
                'damage based on player count. Use .ab mapstat and' \
                '.ab creaturestat in-game to inspect current multipliers.' \
                'A GM .ab setoffset command tweaks server-wide difficulty.' \
                'Config hot-reloads via .reload config.'
            ;;
        mod-transmog)
            printf '%s\n' \
                'Adds a Transmogrification NPC (entry 190010) letting players' \
                'change the visual appearance of their gear. Based on the' \
                'Rochet2 transmog script. After installing and rebuilding,' \
                'spawn the NPC in-game or via console: .npc add 190010.' \
                'The install flow will offer to provide capital-city coordinates.'
            ;;
        mod-1v1-arena)
            printf '%s\n' \
                'Introduces solo 1v1 arena brackets so players can queue for' \
                'ranked matches without needing a partner. Adds its own arena' \
                'season structure and rating/reward system alongside the' \
                'standard 2v2, 3v3, and 5v5 brackets. Requires NPC entry' \
                '999991 (Arena Battlemaster 1v1) to be manually spawned in' \
                'the world after rebuilding — install flow provides coordinates.'
            ;;
        mod-ale)
            printf '%s\n' \
                'AzerothCore Lua Engine -- a powerful AzerothCore-specific' \
                'Lua scripting runtime. Enables custom gameplay features,' \
                'events, and mechanics without modifying core C++ code.' \
                'Diverged from original Eluna; ALE scripts are NOT' \
                'interchangeable with standard Eluna scripts. Supports' \
                'LuaJIT (recommended), Lua 5.2, 5.3, and 5.4.'
            ;;
        mod-player-bot-level-brackets)
            printf '%s\n' \
                'Distributes Playerbot random bots across configurable level brackets' \
                '(e.g. 12% in 1-9, 11% in 10-19) and auto-rebalances bots from' \
                'overpopulated brackets to deficit ones. Supports dynamic distribution' \
                'weighted by real player activity, guild/friend exclusions, and a Death' \
                'Knight level safeguard (min 55). Full and lite debug logging included.' \
                'Requires the Playerbots module and a worldserver rebuild.'
            ;;
        mod-challenge-modes)
            printf '%s\n' \
                'Adds opt-in per-character challenge modes selected at level 1 via a' \
                'Shrine of Challenge NPC near each starting zone. Modes: Hardcore' \
                '(permanent ghost on death), Semi-Hardcore (lose gear/gold on death),' \
                'Self-Crafted, Item-Quality Level, Slow/Very Slow XP, Quest-XP Only,' \
                'and Iron Man. Configurable rewards (items, titles, XP rate bonus).' \
                'Requires EnablePlayerSettings = 1 in worldserver.conf.' \
                'Source: nl-saw fork. OnPlayerResurrect signature auto-patched on install' \
                'to match the current AzerothCore API (bool& + commented param names).'
            ;;
        mod-junk-to-gold)
            printf '%s\n' \
                'Automatically sells gray (vendor-junk) items directly to vendor price' \
                'when looted by the player, keeping bags free of clutter. No in-game' \
                'toggle or configuration file required -- install, rebuild, and it works.'
            ;;
        mod-npc-beastmaster)
            printf '%s\n' \
                'Lets all classes (not just Hunters) adopt and use hunter pets via a' \
                'special NPC. Provides pet adoption (normal, rare, exotic), Hunter' \
                'skills for non-hunters, a pet food vendor, stables, and a tracked-pets' \
                'system (summon, rename, delete). Players summon the NPC anywhere via' \
                '.beastmaster. NPC entry: 601026 (White Fang). You can also place it' \
                'permanently in capitals — install flow provides coordinates. Add 601026' \
                'to Creatures.CustomIDs in worldserver.conf to silence a harmless warning.'
            ;;
        mod-quest-loot-party)
            printf '%s\n' \
                'When any party member loots a normal-quality quest item, all party' \
                'members with the same quest active automatically receive the item.' \
                'Eliminates repeated boss kills when questing as a group. Fully' \
                'automatic — no player commands needed.'
            ;;
        mod-arac)
            printf '%s\n' \
                'All Races All Classes (ARAC) — unlocks every race/class combination' \
                'not normally available (Night Elf Warrior, Undead Paladin, etc.).' \
                'DATA-ONLY mod: no worldserver rebuild required. Configure applies three' \
                'steps automatically: arac.sql → acore_world, server DBC patch, and' \
                'client Patch-A.MPQ. Back up your database before applying SQL.'
            ;;
        mod-dungeon-master)
            printf '%s\n' \
                'Procedural roguelike dungeon challenge system. Talk to the Dungeon' \
                'Master NPC (entry 500000) to pick a difficulty tier, creature theme,' \
                'and dungeon — then enter a repopulated scaled instance. 37 dungeons,' \
                '9 themes, 6 difficulty tiers, party and solo support. The NPC' \
                'auto-spawns in all major cities; SQL is auto-applied on server start.'
            ;;
        mod-talentbutton)
            printf '%s\n' \
                'Enables Dual Talent Specialization at level 10 and adds an anywhere' \
                'talent reset — no class trainer visit required. Uses server-side' \
                'script injection for the in-game button. IMPORTANT: requires an' \
                'unpatched WoW 3.3.5a client; tools like RCEPatcher break the injection.' \
                'Activate by setting TalentButton.Enable = 1 in mod_talentbutton.conf.'
            ;;
        accountwide)
            printf '%s\n' \
                'Syncs achievements, currencies, gold, mounts, and pets across' \
                'all characters on an account. Each system is independently' \
                'toggle-able in config. A mandatory helper script' \
                '(00_AccountWideUtils.lua) must always be present alongside' \
                'other scripts from this repo. Best installed on a fresh server.'
            ;;
        levelupreward)
            printf '%s\n' \
                'Awards a random class-appropriate equippable item on every' \
                'level-up. Quality is rolled: 10% Epic, 25% Rare, 65% Uncommon,' \
                'with a fallback chain down to Common if the DB has no match.' \
                'Armor type scales with level (e.g. Warrior/Paladin switch' \
                'Mail → Plate at 40). First level-up also teaches all class' \
                'weapon proficiencies. Built natively for mod-ale.'
            ;;
        activechat)
            printf '%s\n' \
                'Fills world chat with ambient lore-grounded RP chatter from' \
                'a roster of recurring named residents — each with a faction,' \
                'role, and personality type. Time-of-day, seasonal, and' \
                'in-game event aware. 45+ lore placeholders keep lines fresh.' \
                'A major upgrade over the original ActiveChat. Requires mod-ale.'
            ;;
        battlepass)
            printf '%s\n' \
                'A complete Battle Pass progression system with XP earned from' \
                'kills, quests, PvP, and dungeons. Rewards include items, gold,' \
                'titles, and spells. Comes with an in-game client addon for' \
                'progress tracking and an NPC vendor fallback. Players use .bp' \
                'commands; GMs use .bpadmin. Full CSMH client-server sync included.'
            ;;
        paragon)
            printf '%s\n' \
                'Endless post-max-level stat progression for AzerothCore.' \
                'After reaching level 80, players earn Paragon XP that' \
                'converts into stat bonuses, keeping end-game progression' \
                'meaningful. Serverside is feature-complete; the clientside' \
                'addon UI is in beta. Full architecture documentation included.'
            ;;
        bmah)
            printf '%s\n' \
                'A faithful backport of the Mists of Pandaria Black Market' \
                'Auction House to AzerothCore 3.3.5 via Eluna Lua. Recreates' \
                'the authentic MoP BMAH UI and server-side auction logic.' \
                'Configure which NPC hosts the BMAH, the item pool, and bid' \
                'timers. Includes a matching client addon for the full MoP look.'
            ;;
        lootpet)
            printf '%s\n' \
                'Turns your vanity pet into a functional auto-looter. The pet' \
                'physically walks to nearby corpses to collect loot, adding' \
                'immersion. Party-aware with configurable loot distance and' \
                'group toggle. Default pet: Warbot (entry 34587, item 46767).' \
                'Summon your Warbot and start hunting.'
            ;;
        sod)
            printf '%s\n' \
                'Tiered Discoverer'"'"'s Delight XP bonus sourced from the' \
                'Dad'"'"'s MMO Lab ALE-Kegs collection. Applies +300% at 1-10,' \
                'stepping down through +250/200/150/100/50% to level 79.' \
                'Level 80 receives no buff. Auto-refreshes on level-up.' \
                'Requires server DBC files (custom spells 80865-80870) AND' \
                'client MPQ patches + icon — all installed automatically.'
            ;;
        sitmeanrest)
            printf '%s\n' \
                'Grants a regeneration buff (Graccu Fruitcake, spell 25990)' \
                'when players use the /sit emote. The buff is instantly removed' \
                'on any movement or entering combat, preventing exploit healing.' \
                'Duration and regen spell ID are configurable in the CONFIG' \
                'table at the top of SitMeansRest.lua.'
            ;;
        unlimitedammo)
            printf '%s\n' \
                'Automatically refills Hunter ammo (arrows or bullets) when' \
                'the stack drops below a configurable threshold (default: 52).' \
                'Keeps exactly one ammo type in bags -- no more running dry' \
                'mid-fight. Enable via the ENABLED flag in config, or use' \
                'the .ua GM command for runtime-only toggling.'
            ;;
        # ── SQL Mods ──────────────────────────────────────────
        portals-capitals)
            printf '%s\n' \
                'Capitalizes portal and interactable object labels in every' \
                'major city (Stormwind, Orgrimmar, etc.). Pure cosmetic SQL' \
                'change to gameobject_template display names -- no client mod' \
                'needed. GO_TEMPLATE and GO_SPAWN base IDs are configurable to' \
                'avoid conflicts with other SQL mods that add game objects.'
            ;;
        rare-drops)
            printf '%s\n' \
                'Adds hand-picked, level-appropriate loot to 450+ Classic rare' \
                'and rare-elite mobs that previously had no special drops. Each' \
                'item was thematically chosen for its specific mob across 100+' \
                'hours of curation. Note: no down.sql exists -- removing requires' \
                'restoring from the automatic backup taken at install time.'
            ;;
        lvl1-mounts)
            printf '%s\n' \
                'Modifies mount skill requirements so characters can ride at' \
                'level 1 instead of the default 20. A minimal SQL change to' \
                'skill line abilities. Revert SQL (level-twenty-mounts.sql)' \
                'is included to restore the original level-20 requirement.'
            ;;
        all-stackables)
            printf '%s\n' \
                'Updates all stackable items in item_template to a configurable' \
                'max stack size (default 200). Dramatically reduces bag clutter' \
                'for consumables, reagents, and materials. Stack size can be set' \
                'in Config before installing. Down.sql reverts to original sizes.'
            ;;
        mod-custom-login)
            printf '%s\n' \
                'Grants configurable starter items, bags, heirlooms, skills,' \
                'and faction reputation to characters on their very first login.' \
                'Toggle each category in mod_customlogin.conf. Ideal for fresh' \
                'realms or starter-pack servers. NOTE: C++ module -- requires' \
                'a worldserver rebuild after install to activate.'
            ;;
        npc-teleporter)
            printf '%s\n' \
                'Spawns a Portal Master NPC with a gossip menu teleporting' \
                'players to all major zones, dungeons, raids, and battlegrounds.' \
                'Two NPC variants: capital city hub and starting zone hub.' \
                'Onyxia Level (60 or 80) is configurable. Pure SQL -- no' \
                'client modification required. Originally by Rochet2.'
            ;;
        hearthstone-cd)
            printf '%s\n' \
                'Changes the Hearthstone cooldown from 30 minutes to your' \
                'preferred duration: 30 min, 15 min, 5 min, 1 min, or 1 sec.' \
                'Updates spell_dbc via a simple UPDATE statement. Select the' \
                'variant in Config before installing. Remove resets to the' \
                'WotLK default of 30 minutes automatically.'
            ;;
        buff-mobs)
            printf '%s\n' \
                'Multiplies all creature HP (×2), damage (×1.5), armor (×1.5),' \
                'and attack speed (×0.8 interval = faster). Makes open-world' \
                'mobs noticeably harder without touching dungeon scripting.' \
                'Multipliers are configurable. Remove applies the exact inverse' \
                'to restore original values (floating-point accuracy may vary).'
            ;;
        xbuff-mobs)
            printf '%s\n' \
                'Extreme buff: creature HP×4, damage×2, armor×2, attack speed' \
                '×0.5 (double attack rate). Designed for hardcore or challenge' \
                'servers where trash mobs are genuinely dangerous. Multipliers' \
                'are configurable. Remove applies the inverse multipliers.'
            ;;
        nerf-mobs)
            printf '%s\n' \
                'Reduces creature HP (×0.5), damage (×0.75), armor (×0.75),' \
                'and slows attacks (×1.2 interval). Makes open-world content' \
                'more accessible for casual players or fast leveling servers.' \
                'Multipliers are configurable. Remove applies the inverse.'
            ;;
        baby-mobs)
            printf '%s\n' \
                'Sets creatures to baby difficulty: HP×0.25, damage×0.25,' \
                'armor×0.25, very slow attacks (×1.5 interval). Ideal for' \
                'families, new players, or near-trivial open-world combat.' \
                'Multipliers are configurable. Remove applies the exact inverse.'
            ;;
        xp-rates)
            printf '%s\n' \
                'Edits Rate.XP.Kill, Rate.XP.Quest, and Rate.XP.Explore in' \
                'worldserver.conf to custom multiplier values (e.g. 2 = double' \
                'XP). Changes take effect after .reload config in-game or a' \
                'world server restart. Remove resets all three rates to 1.0' \
                '(the AzerothCore default). Install and Config do the same thing.'
            ;;
        *)
            printf '%s\n' 'No description available. See the GitHub link below.'
            ;;
    esac
}

show_about() {
    local key="$1" name="$2" url="$3"
    print_header
    printf "  ${GOLD}── About: %s ──${RST}\n\n" "$name"
    _get_about_text "$key" | sed 's/^/  /'
    [ -n "$url" ] && printf "\n  ${DIM}Source: %s${RST}\n" "$url"
    printf "\n  ${DIM}Press ENTER to return...${RST}\n"
    read -r _
}

# ── ALE Scripts submenu ───────────────────────────────────────
menu_ale_scripts() {
    local page_start=0
    _setup_screen
    while true; do
        if [ "$_RESIZE_NEEDED" = true ]; then
            _RESIZE_NEEDED=false
            _setup_screen
        fi
        local tlines; tlines=$_TERM_LINES

        # Clear menu area
        print_header

        if ! module_is_installed "mod-ale"; then
            printf "  ${RED}✗ mod-ale (ALE Lua Engine) is not installed.${RST}\n"
            printf "  ${WHITE}Install via main menu option 1, then configure via option 5.${RST}\n"
            printf "\n  ${DIM}Press ENTER to return...${RST}\n"
            read -r _
            return
        fi

        # Build full list with status markers
        local -a available_entries=()
        local -a markers=()
        local entry key name url cloned deployed marker

        for entry in "${ALE_SCRIPT_REGISTRY[@]}"; do
            IFS='|' read -r key name url branch <<< "$entry"
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
            IFS='|' read -r key name url branch <<< "${available_entries[$idx]}"
            printf "  ${WHITE}%2d)${RST} %-38s %b\n" "$(( idx + 1 ))" "$name" "${markers[$idx]}"
        done

        printf "  ${GOLD}──────────────────────────────────────────────────${RST}\n"
        if [ "$total_pages" -gt 1 ]; then
            local nav="  ${DIM}Page $current_page/$total_pages${RST}"
            [ "$current_page" -gt 1 ]              && nav+="   ${WHITE}< prev${RST}"
            [ "$current_page" -lt "$total_pages" ]  && nav+="   ${WHITE}> next${RST}"
            printf "%b\n" "$nav"
        fi
        local page_hint=""
        [ "$total_pages" -gt 1 ] && page_hint="   ${WHITE}< >${RST} Page"
        printf "  ${WHITE}i<num>${RST} Install   ${WHITE}r<num>${RST} Remove   ${WHITE}c<num>${RST} Config   ${WHITE}?<num>${RST} About${page_hint}   ${WHITE}ENTER${RST} Back\n"

        if ! _read_menu_input "$(( tlines - 1 ))"; then
            local _read_rc=$?
            [ "$_read_rc" -eq 2 ] && continue
            return
        fi
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
                if ! _parse_single_index "$nums" "$total"; then
                    print_warning "Invalid script number — e.g. i3"
                    press_enter; continue
                fi
                local inum="$_PARSED_INDEX"
                IFS='|' read -r key name url branch <<< "${available_entries[$((inum - 1))]}"
                ale_script_install "$key" "$name" "$url" "$branch" || true
                press_enter
                ;;
            r)
                if ! _parse_single_index "$nums" "$total"; then
                    print_warning "Invalid script number — e.g. r2"
                    press_enter; continue
                fi
                local rnum="$_PARSED_INDEX"
                IFS='|' read -r key name url branch <<< "${available_entries[$((rnum - 1))]}"
                ale_script_remove "$key" "$name"
                press_enter
                ;;
            c)
                if ! _parse_single_index "$nums" "$total"; then
                    print_warning "Invalid script number — e.g. c5"
                    press_enter; continue
                fi
                local cnum="$_PARSED_INDEX"
                IFS='|' read -r key name url branch <<< "${available_entries[$((cnum - 1))]}"
                case "$key" in
                    accountwide) configure_ale_accountwide ;;
                    activechat)  configure_ale_activechat ;;
                    battlepass) configure_ale_battlepass ;;
                    paragon)    configure_ale_paragon ;;
                    bmah)       configure_ale_bmah ;;
                    sitmeanrest)   configure_ale_sitmeanrest ;;
                    unlimitedammo) configure_ale_unlimitedammo ;;
                    *) print_info "No dedicated reconfigure for $name." ;;
                esac
                press_enter
                ;;
            [?])
                local anum; anum="${nums//[[:space:]]/}"
                if ! [[ "$anum" =~ ^[0-9]+$ ]] || \
                    [ "$anum" -lt 1 ] || [ "$anum" -gt "$total" ]; then
                    print_warning "Invalid script number -- e.g. ?5"
                    press_enter; continue
                fi
                IFS='|' read -r key name url branch <<< "${available_entries[$((anum - 1))]}"
                show_about "$key" "$name" "$url"
                ;;
            *)
                print_warning "Unknown command. Use i<num>, r<num>, c<num>, ?<num>, or ENTER."
                press_enter
                ;;
        esac
    done
}

# ─────────────────────────────────────────────────────────────
# MAIN MENUS
# ─────────────────────────────────────────────────────────────

_module_post_install_hook() {
    local key="$1"
    case "$key" in
        mod-ah-bot)
            echo ""
            print_info "AH Bot installed — configure a bot character?"
            if ask_yes_no "Configure AH Bot now?"; then configure_ahbot; fi
            ;;
        mod-ale)
            echo ""
            print_info "ALE requires post-install setup (lua_scripts dir + conf)."
            if ask_yes_no "Configure ALE now?"; then configure_ale; fi
            ;;
        mod-challenge-modes)
            echo ""
            # Patch: newer AzerothCore changed the OnPlayerResurrect hook signature.
            # The nl-saw fork has the old signature (bool instead of bool&, named params).
            # Both occurrences must be commented-out to match the current AC API.
            local _cm_dir="$SERVER_DIR/modules/mod-challenge-modes"
            local _cm_src="$_cm_dir/src/ChallengeModes.cpp"
            if [ -f "$_cm_src" ] && grep -q "float restore_percent, bool applySickness" "$_cm_src"; then
                print_step "Patching OnPlayerResurrect signature in ChallengeModes.cpp..."
                sed -i 's/float restore_percent, bool applySickness/float \/*restore_percent*\/, bool\& \/*applySickness*\//g' "$_cm_src"
                local _patch_count
                _patch_count=$(grep -c "bool& /\*applySickness\*/" "$_cm_src" 2>/dev/null || echo "0")
                if [ "$_patch_count" -ge 1 ]; then
                    print_success "Patched $_patch_count occurrence(s) — module now matches current AC API."
                else
                    print_warning "Patch may not have applied cleanly — verify ChallengeModes.cpp manually."
                    print_info "Lines 448 and 641: change 'float restore_percent, bool applySickness'"
                    print_info "  to: 'float /*restore_percent*/, bool& /*applySickness*/'"
                fi
            fi
            echo ""
            print_info "Challenge Modes has a conf file and requires EnablePlayerSettings = 1."
            print_info "Note: rebuild the worldserver first if the conf.dist is not yet present."
            if ask_yes_no "Configure Challenge Modes now?"; then configure_module_challenge_modes; fi
            ;;
        mod-player-bot-level-brackets)
            echo ""
            print_info "Bot Level Brackets requires the Playerbots module to function."
            print_info "Note: rebuild the worldserver first if the conf.dist is not yet present."
            if ask_yes_no "Configure Bot Level Brackets now?"; then configure_module_bot_level_brackets; fi
            ;;
        mod-npc-beastmaster)
            echo ""
            print_info "NPC Beastmaster has a conf file and SQL in db-world and db-characters."
            print_info "Note: rebuild the worldserver first if the conf.dist is not yet present."
            if ask_yes_no "Configure NPC Beastmaster now?"; then configure_module_npc_beastmaster; fi
            echo ""
            print_info "Players can summon the Beastmaster NPC anywhere via .beastmaster."
            print_info "You can also permanently place it in capital cities."
            _offer_npc_in_capitals 601026 "White Fang (Beastmaster NPC)" \
                "Run these commands after rebuilding and starting the worldserver."
            ;;
        mod-quest-loot-party)
            echo ""
            print_info "Module SQL (module_string entries) will be auto-applied on next server start."
            print_info "Note: rebuild the worldserver first if the conf.dist is not yet present."
            if ask_yes_no "Configure Quest Loot Party now?"; then configure_module_quest_loot_party; fi
            ;;
        mod-transmog)
            echo ""
            print_info "Transmogrification adds NPC entry 190010 — it must be manually placed in the world."
            _offer_npc_in_capitals 190010 "Transmogrifier NPC" \
                "Run these commands after rebuilding and starting the worldserver."
            ;;
        mod-1v1-arena)
            echo ""
            print_info "1v1 Arena adds a Battlemaster NPC (entry 999991) — it must be manually placed in the world."
            _offer_npc_in_capitals 999991 "Arena Battlemaster 1v1" \
                "Run these commands after rebuilding and starting the worldserver."
            ;;
        mod-arac)
            echo ""
            print_info "Run Configure (c) to apply SQL, copy server DBC files, and install Patch-A.MPQ."
            if ask_yes_no "Configure ARAC now?"; then
                configure_mod_arac
            fi
            ;;
        mod-dungeon-master)
            echo ""
            print_info "Dungeon Master SQL will be auto-applied on next server start."
            print_info "Note: rebuild the worldserver first if the conf.dist is not yet present."
            if ask_yes_no "Configure Dungeon Master now?"; then configure_module_dungeon_master; fi
            echo ""
            print_info "Dungeon Master NPC (entry 500000) spawns automatically in all major cities."
            print_info "You can also place one manually anywhere with: .npc add 500000"
            ;;
        mod-talentbutton)
            echo ""
            print_warning "Talent Button requires an UNPATCHED WoW 3.3.5a client (RCEPatcher blocks script injection)."
            print_info "Note: rebuild the worldserver first if the conf.dist is not yet present."
            if ask_yes_no "Configure Talent Button now?"; then configure_module_talentbutton; fi
            ;;
        mod-learn-spells)
            echo ""
            print_info "Learn Spells requires a conf file — create/activate it now to avoid config spam on every bot login."
            if ask_yes_no "Configure Learn Spells (create conf with defaults) now?"; then configure_module_learn_spells; fi
            ;;
    esac
}

# ── Unified module browser ────────────────────────────────────
# i <nums>  Install one or more (space-separated)
# r <num>   Remove one
# ENTER     Return to main menu
menu_modules() {
    local page_start=0
    _setup_screen
    while true; do
        if [ "$_RESIZE_NEEDED" = true ]; then
            _RESIZE_NEEDED=false
            _setup_screen
        fi
        local tlines; tlines=$_TERM_LINES

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
        print_header
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
        local page_hint=""
        [ "$total_pages" -gt 1 ] && page_hint="   ${WHITE}< >${RST} Page"
        printf "  ${WHITE}i<num>${RST} Install   ${WHITE}r<num>${RST} Remove   ${WHITE}c<num>${RST} Config   ${WHITE}?<num>${RST} About${page_hint}   ${WHITE}ENTER${RST} Back\n"

        if ! _read_menu_input "$(( tlines - 1 ))"; then
            local _read_rc=$?
            [ "$_read_rc" -eq 2 ] && continue
            return
        fi
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
                if ! _parse_single_index "$nums" "$total"; then
                    print_warning "Invalid module number — e.g. i3"
                    press_enter; continue
                fi
                local inum="$_PARSED_INDEX"
                IFS='|' read -r key name url sql_dirs <<< "${available_entries[$((inum - 1))]}"

                if [ "$SERVER_TYPE" != "playerbots" ]; then
                    print_header
                    print_warning "Module installs on $SERVER_NAME are experimental."
                    print_info "Modules will be cloned but rebuilding is not supported on this install type."
                    print_info "Recommended: reinstall as Playerbots for full module support."
                    echo ""
                    if ! ask_yes_no "Continue anyway?"; then continue; fi
                fi

                print_header
                module_install "$key" "$name" "$url" "$sql_dirs" || true
                upsert_mod_commands "$key"
                if [ "$key" = "mod-arac" ]; then
                    print_info "mod-arac cloned. This is a data-only mod — NO REBUILD REQUIRED."
                    print_info "Run Configure (c) to apply SQL, copy server DBC files, and install the client MPQ."
                else
                    print_info "Module cloned. SQL files will be applied automatically on next server start."
                    print_info "All C++ modules require a worldserver rebuild before they can load."
                    print_info "Conf files can be set up via 'Configure' — run configure after a rebuild if"
                    print_info "the conf.dist files are not yet present."
                fi

                if [ "$key" != "mod-arac" ]; then
                    if [ "$SERVER_TYPE" = "playerbots" ]; then
                        print_info "Rebuild the worldserver to compile the new module in."
                        echo ""
                        if ask_yes_no "Rebuild the worldserver now?"; then
                            rebuild_worldserver
                        fi
                    else
                        print_info "(Skipping rebuild — not supported on this install type.)"
                    fi
                fi

                _module_post_install_hook "$key"
                press_enter
                ;;
            r)
                if ! _parse_single_index "$nums" "$total"; then
                    print_warning "Invalid module number — e.g. r2"
                    press_enter; continue
                fi
                local rnum="$_PARSED_INDEX"
                IFS='|' read -r key name _ _ <<< "${available_entries[$((rnum - 1))]}"
                if ! module_is_installed "$key"; then
                    print_warning "$name is not installed."
                    press_enter; continue
                fi
                print_header
                module_remove "$key" "$name"
                if ! module_is_installed "$key"; then
                    remove_mod_commands "$key"
                fi
                if [ "$SERVER_TYPE" = "playerbots" ]; then
                    echo ""
                    print_info "Rebuild needed for module removal to take effect."
                    if ask_yes_no "Rebuild the worldserver now?"; then
                        rebuild_worldserver
                    fi
                fi
                press_enter
                ;;
            c)
                local cnum; cnum="${nums//[[:space:]]/}"
                if ! [[ "$cnum" =~ ^[0-9]+$ ]] || \
                    [ "$cnum" -lt 1 ] || [ "$cnum" -gt "$total" ]; then
                    print_warning "Invalid module number — e.g. c3"
                    press_enter; continue
                fi
                IFS='|' read -r key name _ _ <<< "${available_entries[$((cnum - 1))]}"
                print_header
                case "$key" in
                    mod-ah-bot)                  configure_ahbot ;;
                    mod-ale)                     configure_ale ;;
                    mod-arac)                    configure_mod_arac ;;
                    mod-challenge-modes)         configure_module_challenge_modes ;;
                    mod-dungeon-master)          configure_module_dungeon_master ;;
                    mod-player-bot-level-brackets) configure_module_bot_level_brackets ;;
                    mod-npc-beastmaster)         configure_module_npc_beastmaster ;;
                    mod-quest-loot-party)        configure_module_quest_loot_party ;;
                    mod-talentbutton)            configure_module_talentbutton ;;
                    mod-learn-spells)            configure_module_learn_spells ;;
                    *)
                        print_info "$name has no dedicated configure option."
                        print_info "Edit its .conf file in $SERVER_DIR/env/dist/etc/modules/ directly."
                        ;;
                esac
                press_enter
                ;;
            [?])
                local anum; anum="${nums//[[:space:]]/}"
                if ! [[ "$anum" =~ ^[0-9]+$ ]] || \
                    [ "$anum" -lt 1 ] || [ "$anum" -gt "$total" ]; then
                    print_warning "Invalid module number -- e.g. ?3"
                    press_enter; continue
                fi
                IFS='|' read -r key name url _sql_dirs <<< "${available_entries[$((anum - 1))]}"
                show_about "$key" "$name" "$url"
                ;;
            *)
                print_warning "Unknown command. Use i<num>, r<num>, c<num>, ?<num>, or ENTER."
                press_enter
                ;;
        esac
    done
}

# ── Module Management (conf files) ────────────────────────────
_module_conf_name() {
    case "$1" in
        mod-1v1-arena)                  echo "1v1arena.conf" ;;
        mod-aoe-loot)                   echo "mod_aoe_loot.conf" ;;
        mod-ah-bot)                     echo "mod_ahbot.conf" ;;
        mod-autobalance)                echo "AutoBalance.conf" ;;
        mod-arac)                       echo "" ;;
        mod-dungeon-master)             echo "mod_dungeon_master.conf" ;;
        mod-talentbutton)               echo "mod_talentbutton.conf" ;;
        mod-ale)                        echo "mod_ale.conf" ;;
        mod-player-bot-level-brackets)  echo "mod_player_bot_level_brackets.conf" ;;
        mod-challenge-modes)            echo "challenge_modes.conf" ;;
        mod-individual-progression)     echo "individualProgression.conf" ;;
        mod-junk-to-gold)               echo "" ;;
        mod-learn-spells)               echo "mod_learnspells.conf" ;;
        mod-npc-beastmaster)            echo "mod_npc_beastmaster.conf" ;;
        mod-quest-loot-party)           echo "mod-quest-loot-party.conf" ;;
        mod-solocraft)                  echo "Solocraft.conf" ;;
        mod-transmog)                   echo "transmog.conf" ;;
        *)                              echo "" ;;
    esac
}

_module_conf_active_path() {
    local conf_name; conf_name=$(_module_conf_name "$1")
    [ -z "$conf_name" ] && return 1
    echo "$SERVER_DIR/env/dist/etc/modules/$conf_name"
}

_module_conf_dist_path() {
    local key="$1"
    local conf_name; conf_name=$(_module_conf_name "$key")
    [ -z "$conf_name" ] && return 1

    local expected="$SERVER_DIR/modules/$key/conf/${conf_name}.dist"
    [ -f "$expected" ] && { echo "$expected"; return 0; }

    local found
    found=$(find "$SERVER_DIR/modules/$key" -maxdepth 4 -type f -name "${conf_name}.dist" 2>/dev/null | head -1)
    [ -n "$found" ] && { echo "$found"; return 0; }
    return 1
}

_module_conf_sync_legacy_if_needed() {
    local key="$1"
    [ "$key" != "mod-ah-bot" ] && return 0
    local active legacy
    active=$(_module_conf_active_path "$key") || return 0
    legacy="$SERVER_DIR/conf/modules/mod_ahbot.conf"
    if [ ! -f "$active" ] && [ -f "$legacy" ]; then
        mkdir -p "$(dirname "$active")"
        cp "$legacy" "$active"
    fi
}

_module_conf_status() {
    local key="$1"
    local conf_name; conf_name=$(_module_conf_name "$key")
    if [ -z "$conf_name" ]; then
        echo "${DIM}No conf${RST}"
        return 0
    fi

    _module_conf_sync_legacy_if_needed "$key"
    local active
    active=$(_module_conf_active_path "$key") || true

    if ! module_is_installed "$key"; then
        if [ -n "$active" ] && [ -f "$active" ]; then
            echo "${YELLOW}⚠ Conf exists, module missing${RST}"
        else
            echo "${DIM}○ Not installed${RST}"
        fi
        return 0
    fi

    if [ -n "$active" ] && [ -f "$active" ]; then
        echo "${GREEN}✓ Active${RST}"
        return 0
    fi

    if _module_conf_dist_path "$key" >/dev/null 2>&1; then
        echo "${CYAN}◑ Ready to activate${RST}"
    else
        echo "${YELLOW}⚠ Rebuild needed${RST}"
    fi
}

_module_conf_hints() {
    case "$1" in
        mod-1v1-arena)
            printf '%s\n' \
                'Common options:' \
                '  - Arena1v1.Enable' \
                '  - Arena1v1.MinLevel' \
                '  - Arena1v1.Costs' \
                '  - Arena1v1.Announcer' \
                '  - Arena1v1.PreventHealingTalents'
            ;;
        mod-aoe-loot)
            printf '%s\n' \
                'Common options:' \
                '  - AOELoot.Enable' \
                '  - AOELoot.Message' \
                '  - AOELoot.Range' \
                '  - AOELoot.Group'
            ;;
        mod-ah-bot)
            printf '%s\n' \
                'Common options:' \
                '  - AuctionHouseBot.EnableSeller' \
                '  - AuctionHouseBot.EnableBuyer' \
                '  - AuctionHouseBot.Account / GUID / GUIDs' \
                '  - AuctionHouseBot.Trace* debugging flags' \
                'Tip: use top-level option 4 for guided AH Bot setup.'
            ;;
        mod-autobalance)
            printf '%s\n' \
                'Common options:' \
                '  - AutoBalance.Enable.* toggles' \
                '  - Scaling controls by map size/content' \
                '  - Dungeon/raid coverage toggles'
            ;;
        mod-ale)
            printf '%s\n' \
                'Common options:' \
                '  - ALE.ScriptPath' \
                '  - ALE.EnableLuaEngine' \
                'Tip: use top-level option 5 for guided ALE setup.'
            ;;
        mod-player-bot-level-brackets)
            printf '%s\n' \
                'Common options:' \
                '  - BotLevelBrackets.Enabled' \
                '  - CheckFrequency / CheckFlaggedFrequency' \
                '  - IgnoreGuildBotsWithRealPlayers' \
                '  - IgnoreArenaTeamBots' \
                'Requires Playerbots module.'
            ;;
        mod-challenge-modes)
            printf '%s\n' \
                'Common options:' \
                '  - ChallengeModes.Enable' \
                '  - Hardcore.Enable' \
                '  - Hardcore.XPMultiplier' \
                '  - Hardcore.TalentRewards / ItemRewards' \
                'Also requires EnablePlayerSettings = 1 in worldserver.conf.'
            ;;
        mod-individual-progression)
            printf '%s\n' \
                'Common options:' \
                '  - IndividualProgression.Enable' \
                '  - EnforceGroupRules' \
                '  - VanillaPowerAdjustment / VanillaHealingAdjustment' \
                '  - QuestXPFix'
            ;;
        mod-junk-to-gold)
            printf '%s\n' \
                'This module has no standard .conf.dist file.' \
                'Behavior is module-driven with no exposed runtime conf here.'
            ;;
        mod-learn-spells)
            printf '%s\n' \
                'Common options:' \
                '  - LearnSpells.Enable' \
                '  - LearnSpells.Announce' \
                '  - LearnSpells.OnFirstLogin' \
                '  - LearnSpells.MaxLevel'
            ;;
        mod-npc-beastmaster)
            printf '%s\n' \
                'Common options:' \
                '  - BeastMaster.Enable' \
                '  - BeastMaster.HunterOnly' \
                '  - BeastMaster.AllowedClasses' \
                '  - BeastMaster.MinLevel' \
                'Tip: add 601026 to Creatures.CustomIDs in worldserver.conf.'
            ;;
        mod-quest-loot-party)
            printf '%s\n' \
                'Settings:' \
                '  - QuestParty.Enable  (true/false — master on/off)' \
                '  - QuestParty.Message (true/false — show login announcement)'
            ;;
        mod-solocraft)
            printf '%s\n' \
                'Common options:' \
                '  - Solocraft.Enable / Solocraft.Announce' \
                '  - SoloCraft.Debuff.Enable' \
                '  - SoloCraft.Spellpower.Mult / SoloCraft.Stats.Mult'
            ;;
        mod-transmog)
            printf '%s\n' \
                'Common options:' \
                '  - Transmogrification.Enable' \
                '  - ShowSetDisclaimer' \
                '  - UseCollectionSystem / UseVendorInterface' \
                '  - AllowHiddenTransmog'
            ;;
    esac
}
# Show unified module list with conf status and actions for managing conf files
menu_module_management() {
    local page_start=0
    _setup_screen
    while true; do
        if [ "$_RESIZE_NEEDED" = true ]; then
            _RESIZE_NEEDED=false
            _setup_screen
        fi
        local tlines; tlines=$_TERM_LINES
        print_header

        local -a available_entries=()
        local entry
        for entry in "${MODULE_REGISTRY[@]}"; do available_entries+=("$entry"); done

        local total=${#available_entries[@]}
        local avail=$(( tlines - MENU_START_ROW - 1 ))
        local page_size=$(( avail - 10 ))
        [ "$page_size" -lt 3 ] && page_size=3
        local max_start=$(( total - page_size ))
        [ "$max_start" -lt 0 ] && max_start=0
        [ "$page_start" -gt "$max_start" ] && page_start=$max_start
        [ "$page_start" -lt 0 ] && page_start=0
        local page_end=$(( page_start + page_size ))
        [ "$page_end" -gt "$total" ] && page_end=$total
        local total_pages=$(( (total + page_size - 1) / page_size ))
        local current_page=$(( page_start / page_size + 1 ))

        printf "  ${GOLD}── Module Management ─────────────────────────────${RST}\n"
        printf "  ${DIM}Conf files are activated by copying: .conf.dist -> .conf${RST}\n"
        printf "  ${DIM}Path: $SERVER_DIR/env/dist/etc/modules/${RST}\n"
        printf "  ${YELLOW}⚠  After installing modules, Rebuild worldserver, if you did not do it during installation.${RST}\n"
        printf "  ${DIM}%-4s %-34s %s${RST}\n" "Num" "Module" "Conf Status"
        printf "  ${GOLD}──────────────────────────────────────────────────${RST}\n"

        local idx key name url sql_dirs
        for (( idx=page_start; idx<page_end; idx++ )); do
            IFS='|' read -r key name url sql_dirs <<< "${available_entries[$idx]}"
            printf "  ${WHITE}%2d)${RST} %-34s %b\n" "$(( idx + 1 ))" "$name" "$(_module_conf_status "$key")"
        done

        printf "  ${GOLD}──────────────────────────────────────────────────${RST}\n"
        if [ "$total_pages" -gt 1 ]; then
            local nav="  ${DIM}Page $current_page/$total_pages${RST}"
            [ "$current_page" -gt 1 ] && nav+="   ${WHITE}< prev${RST}"
            [ "$current_page" -lt "$total_pages" ] && nav+="   ${WHITE}> next${RST}"
            printf "%b\n" "$nav"
        fi
        local page_hint=""
        [ "$total_pages" -gt 1 ] && page_hint="   ${WHITE}< >${RST} Page"
        printf "  ${WHITE}a<num>${RST} Activate conf   ${WHITE}e<num>${RST} Edit conf   ${WHITE}r<num>${RST} Reset defaults   ${WHITE}?<num>${RST} Help${page_hint}   ${WHITE}ENTER${RST} Back\n"

        if ! _read_menu_input "$(( tlines - 1 ))"; then
            local _read_rc=$?
            [ "$_read_rc" -eq 2 ] && continue
            return
        fi
        local raw_choice="$_MENU_INPUT"
        [ -z "$raw_choice" ] && return

        local action nums inum
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
            a|e|r|?)
                if ! _parse_single_index "$nums" "$total"; then
                    print_warning "Invalid module number."
                    press_enter
                    continue
                fi
                inum="$_PARSED_INDEX"
                IFS='|' read -r key name url sql_dirs <<< "${available_entries[$((inum - 1))]}"

                if [ "${action,,}" = "?" ]; then
                    print_header
                    printf "  ${GOLD}── Module Config Help: %s ──${RST}\n\n" "$name"
                    local conf_name conf_dist conf_active
                    conf_name=$(_module_conf_name "$key")
                    if [ -z "$conf_name" ]; then
                        printf "  ${DIM}No standard .conf.dist for this module.${RST}\n"
                    else
                        conf_dist=$(_module_conf_dist_path "$key" 2>/dev/null || true)
                        conf_active=$(_module_conf_active_path "$key" 2>/dev/null || true)
                        printf "  ${CYAN}Expected active file:${RST} %s\n" "$conf_active"
                        if [ -n "$conf_dist" ]; then
                            printf "  ${CYAN}Template (.dist):${RST} %s\n" "$conf_dist"
                        else
                            printf "  ${YELLOW}Template (.dist):${RST} not found yet (install + rebuild worldserver first)\n"
                        fi
                    fi
                    echo ""
                    _module_conf_hints "$key" | sed 's/^/  /'
                    echo ""
                    printf "  ${DIM}After conf changes: restart worldserver to apply.${RST}\n"
                    printf "  ${DIM}Press ENTER to return...${RST}\n"
                    read -r _
                    continue
                fi

                local conf_name conf_dist conf_active
                conf_name=$(_module_conf_name "$key")
                if [ -z "$conf_name" ]; then
                    print_warning "$name has no standard .conf.dist configuration file."
                    press_enter
                    continue
                fi
                if ! module_is_installed "$key"; then
                    print_warning "$name is not installed."
                    print_info "Install it first via main menu option 1 (Manage AzerothCore Modules), then rebuild via option 7 (Rebuild worldserver)."
                    press_enter
                    continue
                fi

                _module_conf_sync_legacy_if_needed "$key"
                conf_dist=$(_module_conf_dist_path "$key" 2>/dev/null || true)
                conf_active=$(_module_conf_active_path "$key" 2>/dev/null || true)
                mkdir -p "$SERVER_DIR/env/dist/etc/modules"

                if [ "${action,,}" = "a" ]; then
                    if [ -z "$conf_dist" ] || [ ! -f "$conf_dist" ]; then
                        print_warning "Template .dist not found for $name."
                        print_info "Run top-level option Rebuild worldserver, then try again."
                        press_enter
                        continue
                    fi
                    if [ -f "$conf_active" ] && ! ask_yes_no "Active conf already exists. Overwrite it?"; then
                        print_info "Kept existing conf."
                        press_enter
                        continue
                    fi
                    cp "$conf_dist" "$conf_active"
                    print_success "Activated conf: $conf_active"
                    print_info "Restart worldserver to apply."
                    press_enter
                    continue
                fi

                if [ "${action,,}" = "e" ]; then
                    if [ ! -f "$conf_active" ]; then
                        if [ -n "$conf_dist" ] && [ -f "$conf_dist" ] && ask_yes_no "No active conf yet. Create it from .dist now?"; then
                            cp "$conf_dist" "$conf_active"
                            print_success "Created $conf_active"
                        else
                            print_warning "No active conf file to edit."
                            print_info "Use a<num> to activate first."
                            press_enter
                            continue
                        fi
                    fi
                    _open_text_file "$conf_active"
                    print_info "Restart worldserver to apply."
                    press_enter
                    continue
                fi

                if [ "${action,,}" = "r" ]; then
                    if [ -z "$conf_dist" ] || [ ! -f "$conf_dist" ]; then
                        print_warning "Template .dist not found for $name."
                        print_info "Run top-level option Rebuild worldserver, then try again."
                        press_enter
                        continue
                    fi
                    if ! ask_yes_no "Reset $name conf to defaults? This overwrites current changes."; then
                        print_info "Canceled."
                        press_enter
                        continue
                    fi
                    cp "$conf_dist" "$conf_active"
                    print_success "Reset to defaults: $conf_active"
                    print_info "Restart worldserver to apply."
                    press_enter
                    continue
                fi
                ;;
            *)
                print_warning "Unknown command. Use a<num>, e<num>, r<num>, ?<num>, or ENTER."
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
        print_header
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

    echo -e "${WHITE}${BOLD}Three ways to modify your server:${RST}"
    echo ""
    echo -e "${GOLD}  1) AzerothCore Modules${RST}"
    echo -e "${WHITE}     C++ plugins that rebuild into the worldserver binary. Add new${RST}"
    echo -e "${WHITE}     features, mechanics, and systems. Require a ${BOLD}worldserver rebuild${RST}"
    echo -e "${WHITE}     (30–90 min on Steam Deck) before they take effect.${RST}"
    echo ""
    echo -e "${GOLD}  2) ALE Lua Mods${RST}"
    echo -e "${WHITE}     Lightweight Lua scripts that run inside the server at runtime —${RST}"
    echo -e "${WHITE}     no rebuild needed after the first time. ${BOLD}Requires the AzerothCore${RST}"
    echo -e "${WHITE}     ${BOLD}Lua Engine (ALE) module to be installed and configured first${RST}${WHITE}.${RST}"
    echo -e "${WHITE}     Install ALE via option 1, then configure it via option 5.${RST}"
    echo ""
    echo -e "${GOLD}  3) SQL Mods${RST}"
    echo -e "${WHITE}     Direct database tweaks: buff/nerf mobs, custom login messages,${RST}"
    echo -e "${WHITE}     teleporters, rare drops, and more. Apply instantly — no rebuild.${RST}"
    echo -e "${WHITE}     Databases are backed up automatically before each install.${RST}"
    echo ""

    echo -e "${WHITE}${BOLD}A few things to know:${RST}"
    echo ""
    echo -e "${GREEN}  ✓${RST} ${WHITE}Nothing changes until you explicitly choose an action.${RST}"
    echo -e "${WHITE}    Options 8 (Server status) and 12 (View logs) are read-only.${RST}"
    echo ""
    echo -e "${GREEN}  ✓${RST} ${WHITE}You'll be asked before anything destructive.${RST}"
    echo -e "${WHITE}    Installs, removes, rebuilds, and database operations all ask first.${RST}"
    echo ""
    echo -e "${GREEN}  ✓${RST} ${WHITE}Option 14 → Server Maintenance has backup, restore, and repair tools.${RST}"
    echo -e "${WHITE}    Repair only clears SQL update-tracking rows — it never drops tables.${RST}"
    echo ""

    if [ "$user_module_count" -eq 0 ]; then
        echo -e "${WHITE}${BOLD}Suggested first steps for a fresh install:${RST}"
        echo -e "${WHITE}  1. Option ${CYAN}8${WHITE} (Server status) — confirm your containers are running${RST}"
        echo -e "${WHITE}  2. Option ${CYAN}3${WHITE} (SQL Mods) — safe first tweaks, no rebuild needed${RST}"
        echo -e "${WHITE}  3. Option ${CYAN}1${WHITE} (Modules) — browse and install C++ modules${RST}"
        echo -e "${WHITE}  4. Option ${CYAN}5${WHITE} (Configure ALE) — if you installed the ALE module,${RST}"
        echo -e "${WHITE}     configure it here, then use option ${CYAN}2${WHITE} to add Lua mods${RST}"
    else
        echo -e "${WHITE}${BOLD}Useful options for an existing install:${RST}"
        echo -e "${WHITE}  • Option ${CYAN}1${WHITE} (Modules) — browse installed and available C++ modules${RST}"
        echo -e "${WHITE}  • Option ${CYAN}2${WHITE} (ALE Lua Mods) — manage Lua scripts (needs ALE installed)${RST}"
        echo -e "${WHITE}  • Option ${CYAN}3${WHITE} (SQL Mods) — database tweaks, no rebuild required${RST}"
        echo -e "${WHITE}  • Option ${CYAN}8${WHITE} (Server status) — check container state${RST}"
        echo -e "${WHITE}  • Option ${CYAN}14${WHITE} (Server Maintenance) — backup, restore, repair${RST}"
    fi
    echo ""
    echo -e "${DIM}This welcome shows once per install. The marker file at${RST}"
    echo -e "${DIM}$marker tracks this.${RST}"
    echo ""
    # Offer client folder detection on first run
    echo -e "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"
    echo -e "${WHITE}${BOLD} WoW Client Folder${RST}"
    echo -e "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"
    echo -e "${WHITE}Some mods include WoW client addons. Detecting your client${RST}"
    echo -e "${WHITE}folder now lets the manager auto-install them for you.${RST}"
    echo ""
    if ask_yes_no "Detect WoW client folder now? (can skip and do later via option 16)"; then
        detect_wow_client || true
    else
        print_info "You can set this anytime from the main menu → option 16."
    fi
    echo ""
    press_enter

    # Drop the marker — silent failure is OK, the welcome just shows again next time
    touch "$marker" 2>/dev/null || true
    # Restore static logo now that the welcome screen is done
    _setup_screen
    print_header
}

# ── SQL Mods submenu ──────────────────────────────────────────
menu_sql_mods() {
    sqlmod_init
    local page_start=0
    _setup_screen
    while true; do
        if [ "$_RESIZE_NEEDED" = true ]; then
            _RESIZE_NEEDED=false
            _setup_screen
        fi
        local tlines; tlines=$_TERM_LINES
        print_header

        local -a available_entries=()
        local -a markers=()
        local entry key name url type

        for entry in "${SQL_MOD_REGISTRY[@]}"; do
            IFS='|' read -r key name url type <<< "$entry"
            if sqlmod_is_installed "$key"; then
                markers+=("${GREEN}✓ Installed${RST}")
            else
                markers+=("${DIM}○ Not installed${RST}")
            fi
            available_entries+=("$entry")
        done

        local total=${#available_entries[@]}
        local avail=$(( tlines - MENU_START_ROW - 1 ))
        local page_size=$(( avail - 7 ))
        [ "$page_size" -lt 3 ] && page_size=3

        local max_start=$(( total - page_size ))
        [ "$max_start" -lt 0 ] && max_start=0
        [ "$page_start" -gt "$max_start" ] && page_start=$max_start
        [ "$page_start" -lt 0 ] && page_start=0

        local page_end=$(( page_start + page_size ))
        [ "$page_end" -gt "$total" ] && page_end=$total
        local total_pages=$(( (total + page_size - 1) / page_size ))
        local current_page=$(( page_start / page_size + 1 ))

        printf "  ${GOLD}── SQL Mods ──────────────────────────────────────${RST}\n"
        printf "  ${YELLOW}⚠  Edits the world DB — auto-backup runs before each install (see \"sql_scripts\" directory)${RST}\n"
        printf "  ${DIM}%-4s %-40s %s${RST}\n" "Num" "Mod" "Status"
        printf "  ${GOLD}──────────────────────────────────────────────────${RST}\n"

        local idx
        for (( idx=page_start; idx<page_end; idx++ )); do
            IFS='|' read -r key name url type <<< "${available_entries[$idx]}"
            printf "  ${WHITE}%2d)${RST} %-40s %b\n" "$(( idx + 1 ))" "$name" "${markers[$idx]}"
        done

        printf "  ${GOLD}──────────────────────────────────────────────────${RST}\n"
        if [ "$total_pages" -gt 1 ]; then
            local nav="  ${DIM}Page $current_page/$total_pages${RST}"
            [ "$current_page" -gt 1 ]              && nav+="   ${WHITE}< prev${RST}"
            [ "$current_page" -lt "$total_pages" ]  && nav+="   ${WHITE}> next${RST}"
            printf "%b\n" "$nav"
        fi
        local page_hint=""
        [ "$total_pages" -gt 1 ] && page_hint="   ${WHITE}< >${RST} Page"
        printf "  ${WHITE}i<num>${RST} Install   ${WHITE}r<num>${RST} Remove   ${WHITE}c<num>${RST} Config   ${WHITE}?<num>${RST} About${page_hint}   ${WHITE}ENTER${RST} Back\n"

        if ! _read_menu_input "$(( tlines - 1 ))"; then
            local _read_rc=$?
            [ "$_read_rc" -eq 2 ] && continue
            return
        fi
        local raw_choice="$_MENU_INPUT"
        [ -z "$raw_choice" ] && return

        local action nums
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
                if ! _parse_single_index "$nums" "$total"; then
                    print_warning "Invalid mod number — e.g. i3"
                    press_enter; continue
                fi
                local inum="$_PARSED_INDEX"
                IFS='|' read -r key name url type <<< "${available_entries[$((inum - 1))]}"
                print_header
                sqlmod_install "$key" "$name" "$url" "$type" || true
                press_enter
                ;;
            r)
                if ! _parse_single_index "$nums" "$total"; then
                    print_warning "Invalid mod number — e.g. r3"
                    press_enter; continue
                fi
                local rnum="$_PARSED_INDEX"
                IFS='|' read -r key name url type <<< "${available_entries[$((rnum - 1))]}"
                print_header
                sqlmod_remove "$key" "$name" "$url" "$type" || true
                press_enter
                ;;
            c)
                if ! _parse_single_index "$nums" "$total"; then
                    print_warning "Invalid mod number — e.g. c5"
                    press_enter; continue
                fi
                local cnum="$_PARSED_INDEX"
                IFS='|' read -r key name url type <<< "${available_entries[$((cnum - 1))]}"
                print_header
                sqlmod_configure "$key" "$name"
                ;;
            [?])
                if ! _parse_single_index "$nums" "$total"; then
                    print_warning "Invalid mod number -- e.g. ?3"
                    press_enter; continue
                fi
                local anum="$_PARSED_INDEX"
                IFS='|' read -r key name url type <<< "${available_entries[$((anum - 1))]}"
                show_about "$key" "$name" "$url"
                ;;
            *)
                print_warning "Unknown command. Use i<num>, r<num>, c<num>, ?<num>, or ENTER."
                press_enter
                ;;
        esac
    done
}

# ── Server Maintenance submenu ───────────────────────────────
# Scan installed module SQL files and mark any untracked ones as applied,
# fixing ac-db-import "Table X already exists" failures without dropping data.
fix_dbimport_table_exists() {
    print_step "Fix: ac-db-import Table Already Exists"
    echo ""
    echo -e "${WHITE}This scans every installed module's SQL files. For any file${RST}"
    echo -e "${WHITE}not tracked in the ${CYAN}updates${RST}${WHITE} table, it computes the file hash${RST}"
    echo -e "${WHITE}and inserts a tracking row so ac-db-import will skip it.${RST}"
    echo ""
    echo -e "${WHITE}Use this when:${RST}"
    echo -e "  ${CYAN}• ac-db-import fails with 'Table X already exists'${RST}"
    echo -e "  ${CYAN}• The module SQL does NOT use CREATE TABLE IF NOT EXISTS${RST}"
    echo ""
    refresh_container_names
    sqlmod_init
    if ! container_running "$DB_CONTAINER"; then
        print_error "Database container is not running — start the server first."
        return 1
    fi
    local fixed=0 skipped=0
    local entry key db_full files db_short
    for entry in "${MODULE_UPDATE_FILES[@]}"; do
        IFS='|' read -r key db_full files <<< "$entry"
        [ -z "$files" ] && continue
        ! module_is_installed "$key" && continue
        db_short="${db_full#acore_}"
        local f
        for f in $files; do
            # Check if this file already has a tracking row
            local rows
            rows=$(docker exec "$DB_CONTAINER" mysql -uroot -p"$DB_ROOT_PASSWORD" -N \
                "$db_full" \
                -e "SELECT COUNT(*) FROM updates WHERE name='$f';" 2>/dev/null \
                | tr -d '[:space:]')
            if [ "$rows" != "0" ] && [ -n "$rows" ]; then
                echo -e "  ${DIM}○ Already tracked:${RST} $f"
                skipped=$((skipped + 1))
                continue
            fi
            # Find the file in the modules directory
            local sql_file
            sql_file=$(find "$SERVER_DIR/modules/$key" -name "$f" 2>/dev/null | head -1)
            if [ -z "$sql_file" ]; then
                echo -e "  ${YELLOW}? File not found:${RST} $f"
                continue
            fi
            local hash; hash=$(compute_sql_hash "$sql_file")
            if [ -z "$hash" ]; then
                echo -e "  ${RED}✗ Hash failed:${RST} $f"
                continue
            fi
            docker exec "$DB_CONTAINER" mysql -uroot -p"$DB_ROOT_PASSWORD" \
                "$db_full" \
                -e "INSERT INTO updates (name,hash,state,timestamp,speed)
                    VALUES ('$f','$hash','RELEASED',NOW(),0)
                    ON DUPLICATE KEY UPDATE hash='$hash',state='RELEASED';" \
                2>/dev/null
            echo -e "  ${GREEN}✓ Marked as applied:${RST} $f  ${DIM}(${hash:0:7})${RST}"
            fixed=$((fixed + 1))
        done
    done
    echo ""
    if [ "$fixed" -gt 0 ]; then
        print_success "$fixed file(s) marked as applied."
        print_info "Restart the server — ac-db-import should now pass."
    else
        print_info "No untracked files found (all already marked or no known files)."
        if [ "$skipped" -gt 0 ]; then
            print_info "If ac-db-import still fails, try option 1 (Repair install state) to"
            print_info "clear stale hashes and force a re-apply with mode C (Clear tracking)."
        fi
    fi
}

fix_battlepass_npc() {
    print_step "Fix: BattlePass NPC (entry 90100) missing from database"
    echo ""
    echo -e "${WHITE}Creates creature_template entry 90100 (Battle Pass Vendor) in acore_world.${RST}"
    echo -e "${WHITE}Required before .npc add 90100 will work in-game.${RST}"
    echo ""
    refresh_container_names
    sqlmod_init
    if ! container_running "$DB_CONTAINER"; then
        print_error "Database container is not running — start the server first."
        return 1
    fi
    local _mdb="docker exec $DB_CONTAINER mysql -uroot -p$DB_ROOT_PASSWORD"
    # Step 1: show current state
    local _pre
    _pre=$(docker exec "$DB_CONTAINER" mysql -uroot -p"$DB_ROOT_PASSWORD" -N -e \
        "SELECT IFNULL(CONCAT('EXISTS: entry=',entry,' name=',name),'NOT FOUND') FROM acore_world.creature_template WHERE entry=90100 UNION ALL SELECT 'NOT FOUND' LIMIT 1;" 2>/dev/null | head -1)
    print_info "Current DB state: ${_pre:-unknown}"
    # Step 2: DELETE (separate call — FK checks off)
    local _del_out
    _del_out=$(docker exec "$DB_CONTAINER" mysql -uroot -p"$DB_ROOT_PASSWORD" \
        -e "SET foreign_key_checks=0; DELETE FROM acore_world.creature_template WHERE entry=90100; SET foreign_key_checks=1;" 2>&1)
    local _del_rc=$?
    local _del_errs; _del_errs=$(echo "$_del_out" | grep -v "^\(mysql:\|$\)")
    if [ $_del_rc -ne 0 ] || echo "$_del_errs" | grep -qi "^ERROR"; then
        print_error "DELETE failed (rc=$_del_rc): $_del_errs"
        return 1
    fi
    # Step 3: INSERT (separate call — explicit schema, FK off, sql_mode cleared)
    local _ins_out
    _ins_out=$(docker exec "$DB_CONTAINER" mysql -uroot -p"$DB_ROOT_PASSWORD" \
        -e "SET foreign_key_checks=0; SET sql_mode=''; INSERT INTO acore_world.creature_template (\`entry\`,\`name\`,\`subname\`,\`gossip_menu_id\`,\`minlevel\`,\`maxlevel\`,\`exp\`,\`faction\`,\`npcflag\`,\`speed_walk\`,\`speed_run\`,\`rank\`,\`dmgschool\`,\`DamageModifier\`,\`BaseAttackTime\`,\`RangeAttackTime\`,\`BaseVariance\`,\`RangeVariance\`,\`unit_class\`,\`unit_flags\`,\`unit_flags2\`,\`dynamicflags\`,\`type\`,\`AIName\`,\`MovementType\`,\`HoverHeight\`,\`HealthModifier\`,\`ManaModifier\`,\`ArmorModifier\`,\`RegenHealth\`,\`flags_extra\`,\`VerifiedBuild\`) VALUES (90100,'Battle Pass Vendor','Seasonal Rewards',0,80,80,0,35,1,1.0,1.14286,0,0,1.0,2000,2000,1.0,1.0,1,33536,2048,0,7,'',0,1.0,1.0,1.0,1.0,1,2,0); SET foreign_key_checks=1;" 2>&1)
    local _ins_rc=$?
    local _ins_errs; _ins_errs=$(echo "$_ins_out" | grep -v "^\(mysql:\|$\)")
    if [ $_ins_rc -ne 0 ] || echo "$_ins_errs" | grep -qi "^ERROR"; then
        print_error "INSERT failed (rc=$_ins_rc): $_ins_errs"
        print_info "This usually means a column mismatch. Try running the Server Maintenance → 5 again after a worldserver restart."
        return 1
    fi
    # Step 4: schema-adaptive model/scale (non-fatal)
    docker exec "$DB_CONTAINER" mysql -uroot -p"$DB_ROOT_PASSWORD" -e \
        "SET @h=(SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='acore_world' AND TABLE_NAME='creature_template' AND COLUMN_NAME='scale'); SET @s=IF(@h>0,'UPDATE acore_world.creature_template SET scale=1.0 WHERE entry=90100','SELECT 1'); PREPARE _p FROM @s; EXECUTE _p; DEALLOCATE PREPARE _p;" 2>/dev/null
    docker exec "$DB_CONTAINER" mysql -uroot -p"$DB_ROOT_PASSWORD" -e \
        "SET @h=(SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='acore_world' AND TABLE_NAME='creature_template_model'); SET @s=IF(@h>0,'DELETE FROM acore_world.creature_template_model WHERE CreatureID=90100','SELECT 1'); PREPARE _p FROM @s; EXECUTE _p; DEALLOCATE PREPARE _p;" 2>/dev/null
    docker exec "$DB_CONTAINER" mysql -uroot -p"$DB_ROOT_PASSWORD" -e \
        "SET @h=(SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='acore_world' AND TABLE_NAME='creature_template_model'); SET @s=IF(@h>0,'INSERT INTO acore_world.creature_template_model (CreatureID,Idx,CreatureDisplayID,DisplayScale,Probability,VerifiedBuild) VALUES (90100,0,25478,1.0,1.0,0)','SELECT 1'); PREPARE _p FROM @s; EXECUTE _p; DEALLOCATE PREPARE _p;" 2>/dev/null
    docker exec "$DB_CONTAINER" mysql -uroot -p"$DB_ROOT_PASSWORD" -e \
        "SET @h=(SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='acore_world' AND TABLE_NAME='creature_template' AND COLUMN_NAME='modelid1'); SET @s=IF(@h>0,'UPDATE acore_world.creature_template SET modelid1=25478 WHERE entry=90100','SELECT 1'); PREPARE _p FROM @s; EXECUTE _p; DEALLOCATE PREPARE _p;" 2>/dev/null
    # Step 5: verify
    local _bp_verify
    _bp_verify=$(docker exec "$DB_CONTAINER" mysql -uroot -p"$DB_ROOT_PASSWORD" -N -e \
        "SELECT CONCAT('entry=',entry,' name=',name,' npcflag=',npcflag) FROM acore_world.creature_template WHERE entry=90100;" 2>/dev/null)
    if echo "$_bp_verify" | grep -q "entry=90100"; then
        print_success "Verified: entry 90100 exists in DB → $_bp_verify"
    else
        print_error "Verification failed — entry 90100 not found after SQL."
        print_info "Run this diagnostic manually on the Steam Deck:"
        print_info "  docker exec \$DB_CONTAINER mysql -uroot -ppassword -e \"SHOW COLUMNS FROM acore_world.creature_template LIKE 'entry';\" 2>&1"
        return 1
    fi
    echo ""
    if ask_yes_no "Restart the worldserver now to load the new creature_template?"; then
        if [ -z "$WORLD_CONTAINER" ] || ! container_running "$WORLD_CONTAINER"; then
            print_error "Worldserver container not running — start the server first, then restart manually."
        elif docker restart "$WORLD_CONTAINER"; then
            print_success "Worldserver restarted — use .npc add 90100 in-game to spawn the NPC."
        else
            print_error "Restart failed. Container: $WORLD_CONTAINER"
        fi
    else
        print_info "Remember to restart the worldserver before spawning."
        print_info "  Main menu → Restart Server  or:  ${CYAN}docker restart $WORLD_CONTAINER${RST}"
        print_info "  then in-game: ${CYAN}.npc add 90100${RST}"
    fi
}

fix_battlepass_csmh_crash() {
    print_step "Fix: BattlePass CSMH double-load crash"
    echo ""
    echo -e "${WHITE}Root cause: CSMH_SMH.ext is a plain Lua file that ALE auto-loads${RST}"
    echo -e "${WHITE}BEFORE any .lua scripts. When 05_BP_Communication.lua then calls${RST}"
    echo -e "${WHITE}require(\"lib.CSMH.CSMH_SMH\"), CSMH is loaded a SECOND time — which${RST}"
    echo -e "${WHITE}registers duplicate event handlers and corrupts internal state.${RST}"
    echo -e "${WHITE}Fix: remove the redundant require line. Full CSMH client sync kept.${RST}"
    echo ""
    local lua_dir
    lua_dir=$(ale_lua_scripts_dir)
    local comm_file="$lua_dir/battlepass/05_BP_Communication.lua"
    if [ ! -d "$lua_dir/battlepass" ]; then
        print_error "BattlePass not deployed at: $lua_dir/battlepass"
        print_info "Deploy it first via: ALE Scripts → Install → battlepass"
        return 1
    fi
    if [ ! -f "$comm_file" ]; then
        print_error "File not found: $comm_file"
        return 1
    fi
    # Check if already patched
    if grep -q "require removed" "$comm_file" 2>/dev/null; then
        print_info "Already patched — require line already removed."
    elif grep -q 'require("lib\.CSMH\.CSMH_SMH")' "$comm_file" 2>/dev/null; then
        sed -i 's|^require("lib\.CSMH\.CSMH_SMH")|-- require removed: CSMH_SMH.ext is auto-loaded by ALE before .lua scripts run|' "$comm_file"
        print_success "Patched: removed duplicate require in $comm_file"
    else
        print_info "require line not found — file may already be clean or have a different format."
        grep -n "CSMH\|require" "$comm_file" | head -5
    fi
    echo ""
    print_info "Next steps:"
    print_info "  1. In-game GM command:  ${CYAN}.reload ale${RST}"
    print_info "  2. OR restart the worldserver from the main menu."
    print_info "  Full CSMH client sync is preserved — all BattlePass features work."
}

# ─────────────────────────────────────────────────────────────
# cleanup_docker
#   Prunes Docker build cache and optionally build volumes.
#   Safe by design: always keeps the database volume running.
#   Use after removing modules or when a rebuild isn't picking
#   up changes due to stale cache layers.
# ─────────────────────────────────────────────────────────────
cleanup_docker() {
    print_step "Docker Cleanup"
    echo ""
    echo -e "${WHITE}Frees disk space and forces a clean rebuild on next start.${RST}"
    echo -e "${WHITE}Useful after removing modules or when cached layers are stale.${RST}"
    echo ""
    echo -e "${WHITE}${BOLD}Choose cleanup level:${RST}"
    echo ""
    printf "  ${WHITE}1)${RST} Build cache only  ${DIM}(safe — frees space, next rebuild is full recompile)${RST}\n"
    printf "  ${WHITE}2)${RST} Build cache + build volume  ${DIM}(deeper clean — also wipes CMake artifacts)${RST}\n"
    printf "  ${WHITE}3)${RST} Full clean  ${DIM}(cache + build volume + old images — maximum disk recovery)${RST}\n"
    printf "  ${DIM}  [ENTER] Cancel${RST}\n"
    echo ""
    printf "${WHITE}Choice: ${RST}"
    read -r choice
    [ -z "$choice" ] && return 0

    case "$choice" in
        1|2|3) ;;
        *) print_warning "Invalid choice."; return 1 ;;
    esac

    # Ensure DB is running before we touch anything, so its volume stays attached
    refresh_container_names
    local db_was_running=false
    if container_running "$DB_CONTAINER"; then
        db_was_running=true
    else
        print_info "Starting database container to protect its volume..."
        (cd "$SERVER_DIR" && docker compose up -d ac-database 2>/dev/null) || true
        sleep 3
        refresh_container_names
    fi

    # Stop worldserver (not DB) before cleanup
    print_info "Stopping worldserver..."
    (cd "$SERVER_DIR" && docker compose stop ac-worldserver 2>/dev/null) || true

    echo ""
    print_info "Pruning Docker build cache..."
    if docker builder prune -af 2>&1 | grep -E "Total reclaimed|freed|error" ; then
        print_success "Build cache cleared."
    else
        print_warning "builder prune had non-zero exit — may already be empty."
    fi

    if [ "$choice" -ge 2 ]; then
        echo ""
        print_info "Identifying build volume..."
        local project; project=$(basename "$SERVER_DIR" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9-')
        # AzerothCore source-build volume is typically <project>_ac-build or <project>_build
        local build_vol
        build_vol=$(docker volume ls --format '{{.Name}}' 2>/dev/null \
            | grep -E "^${project}.*(ac.build|build)" | head -1)
        if [ -n "$build_vol" ]; then
            print_info "Removing build volume: $build_vol"
            if docker volume rm "$build_vol" 2>/dev/null; then
                print_success "Build volume removed — CMake cache cleared."
            else
                print_warning "Could not remove $build_vol (may still be in use)."
                print_info "Run: docker volume rm $build_vol  after fully stopping the server."
            fi
        else
            print_info "No build volume found matching '${project}*build' — nothing to remove."
            print_info "  (Run 'docker volume ls' to inspect manually if needed.)"
        fi
    fi

    if [ "$choice" -ge 3 ]; then
        echo ""
        print_info "Removing unused Docker images..."
        docker image prune -af 2>&1 | grep -E "Total reclaimed|deleted|error" || true
        print_success "Old images removed."
    fi

    echo ""
    print_success "Cleanup complete."
    echo ""
    echo -e "${WHITE}Next step: rebuild the worldserver${RST}"
    echo -e "  ${CYAN}Configuration → Rebuild Worldserver${RST}"
    echo -e "${DIM}  The first rebuild after this will take 30–90 min (full recompile).${RST}"
}

menu_server_maintenance() {
    _setup_screen
    while true; do
        if [ "$_RESIZE_NEEDED" = true ]; then
            _RESIZE_NEEDED=false
            _setup_screen
        fi
        print_header
        printf "  ${GOLD}${BOLD}Server Maintenance${RST}\n"
        printf "  ${GOLD}──────────────────────────────────────────────────${RST}\n"
        printf "  ${WHITE}1)${RST} Repair install state\n"
        printf "  ${WHITE}2)${RST} Backup databases\n"
        printf "  ${WHITE}3)${RST} Restore / import a backup\n"
        printf "  ${WHITE}4)${RST} Fix: ac-db-import 'Table already exists' errors\n"
        printf "  ${WHITE}5)${RST} Fix: BattlePass NPC missing (entry 90100)\n"
        printf "  ${WHITE}6)${RST} Fix: BattlePass crash (remove duplicate CSMH require)\n"
        printf "  ${WHITE}7)${RST} Clean Docker cache / build artifacts\n"
        printf "  ${GOLD}──────────────────────────────────────────────────${RST}\n"
        printf "  ${DIM}  [ENTER] Back${RST}\n"

        local _tlines; _tlines=$_TERM_LINES
        if ! _read_menu_input "$(( _tlines - 1 ))"; then
            local _read_rc=$?
            [ "$_read_rc" -eq 2 ] && continue
            return
        fi
        local choice="${_MENU_INPUT,,}"

        case "$choice" in
            1) repair_install_state; press_enter ;;
            2) _maintenance_backup_all; press_enter ;;
            3) _maintenance_import ;;
            4) fix_dbimport_table_exists; press_enter ;;
            5) fix_battlepass_npc; press_enter ;;
            6) fix_battlepass_csmh_crash; press_enter ;;
            7) cleanup_docker; press_enter ;;
            "") return ;;
            *) print_warning "Enter 1–7 or ENTER to go back."; press_enter ;;
        esac
    done
}

_maintenance_backup_all() {
    sqlmod_init
    if ! container_running "$DB_CONTAINER"; then
        print_error "Database container is not running."
        return 1
    fi
    print_header
    printf "  ${GOLD}── Database Backup ──${RST}\n\n"
    local ts; ts=$(date +%Y%m%d_%H%M%S)
    local failed=0
    for db in acore_world acore_characters acore_auth; do
        local bfile="$SQLMOD_BACKUP_DIR/${ts}_${db}.sql.gz"
        print_info "Backing up ${db} → $(basename "$bfile") ..."
        if ( set -o pipefail
            docker exec "$DB_CONTAINER" mysqldump -uroot -p"$DB_ROOT_PASSWORD" "$db" \
                2>/dev/null | gzip > "$bfile"
        ); then
            print_success "  ✓ $db"
            _prune_backup_files "$SQLMOD_BACKUP_DIR" "*_${db}.sql.gz" 2
        else
            rm -f "$bfile"
            print_error "  ✗ $db backup failed"
            failed=1
        fi
    done
    printf "\n"
    if [ "$failed" -eq 0 ]; then
        print_success "All databases backed up to: $SQLMOD_BACKUP_DIR"
    else
        print_warning "Some backups failed. Check that the DB container is healthy."
    fi
    printf "\n  ${DIM}Backups are .sql.gz files — restore via option 3 in this menu.${RST}\n"
}

_maintenance_import() {
    sqlmod_init
    if ! container_running "$DB_CONTAINER"; then
        print_error "Database container is not running."
        press_enter; return
    fi

    print_header
    printf "  ${GOLD}── Restore / Import Backup ──${RST}\n\n"

    # List available backups sorted newest-first
    local -a files=()
    while IFS= read -r f; do files+=("$f"); done < <(
        ls -t "$SQLMOD_BACKUP_DIR"/*.sql.gz 2>/dev/null
    )

    if [ "${#files[@]}" -eq 0 ]; then
        print_warning "No backups found in $SQLMOD_BACKUP_DIR"
        print_info "Run option 2 first to create a backup."
        press_enter; return
    fi

    printf "  ${DIM}Available backups (newest first):${RST}\n\n"
    local i=1
    for f in "${files[@]}"; do
        local sz; sz=$(du -h "$f" 2>/dev/null | awk '{print $1}')
        printf "  ${WHITE}%2d)${RST} %s  ${DIM}(%s)${RST}\n" "$i" "$(basename "$f")" "$sz"
        (( i++ ))
    done
    printf "\n  ${DIM}Or enter a full path to a .sql or .sql.gz file.${RST}\n"
    printf "\n  ${WHITE}Select [1-%d] or path (B to cancel): ${RST}" "${#files[@]}"
    local sel; read -r sel
    [ "${sel,,}" = "b" ] || [ -z "$sel" ] && return

    local chosen_file
    if [[ "$sel" =~ ^[0-9]+$ ]] && [ "$sel" -ge 1 ] && [ "$sel" -le "${#files[@]}" ]; then
        chosen_file="${files[$((sel - 1))]}"
    elif [ -f "$sel" ]; then
        chosen_file="$sel"
    else
        print_error "Invalid selection."; press_enter; return
    fi

    # Determine target database from filename
    local target_db=""
    local fname; fname="$(basename "$chosen_file")"
    if [[ "$fname" == *acore_world* ]];      then target_db="acore_world"
    elif [[ "$fname" == *acore_characters* ]]; then target_db="acore_characters"
    elif [[ "$fname" == *acore_auth* ]];       then target_db="acore_auth"
    fi

    if [ -z "$target_db" ]; then
        printf "\n  ${WHITE}Target database (acore_world / acore_characters / acore_auth): ${RST}"
        read -r target_db
        if [[ ! "$target_db" =~ ^acore_(world|characters|auth)$ ]]; then
            print_error "Invalid database name."; press_enter; return
        fi
    fi

    printf "\n"
    print_warning "This will OVERWRITE ${target_db} with data from: $(basename "$chosen_file")"
    print_warning "Make sure you have a fresh backup before restoring!"
    if ! ask_yes_no "Restore ${target_db} from this backup?"; then return; fi

    # Take a safety backup before overwriting
    print_info "Taking safety backup of current ${target_db} before restore..."
    local ts; ts=$(date +%Y%m%d_%H%M%S)
    local safefile="$SQLMOD_BACKUP_DIR/${ts}_pre_restore_${target_db}.sql.gz"
    if ( set -o pipefail
        docker exec "$DB_CONTAINER" mysqldump -uroot -p"$DB_ROOT_PASSWORD" "$target_db" \
            2>/dev/null | gzip > "$safefile"
    ); then
        print_success "Safety backup: $(basename "$safefile")"
    else
        rm -f "$safefile"
        print_warning "Safety backup failed — proceeding anyway (you accepted the risk)."
    fi

    print_info "Restoring ${target_db} from $(basename "$chosen_file")..."
    if [[ "$chosen_file" == *.gz ]]; then
        if ( set -o pipefail
             gzip -dc "$chosen_file" | docker exec -i "$DB_CONTAINER" \
                 mysql -uroot -p"$DB_ROOT_PASSWORD" "$target_db" 2>&1
        ); then
            print_success "Restore complete!"
        else
            print_error "Restore failed. Check the file and try again."
        fi
    else
        if docker exec -i "$DB_CONTAINER" \
            mysql -uroot -p"$DB_ROOT_PASSWORD" "$target_db" < "$chosen_file" 2>&1; then
            print_success "Restore complete!"
        else
            print_error "Restore failed. Check the file and try again."
        fi
    fi
    press_enter
}

menu_configuration() {
    while true; do
        if [ "$_RESIZE_NEEDED" = true ]; then
            _RESIZE_NEEDED=false
            _setup_screen
        fi
        print_header
        print_install_info
        printf "\n\n  ${GOLD}${BOLD}Configuration${RST}\n"
        printf "  ${GOLD}──────────────────────────────────────────────────${RST}\n"
        printf "  ${WHITE}C)${RST} Set WoW Client Folder\n"
        printf "  ${WHITE}1)${RST} Configure AH Bot\n"
        printf "  ${WHITE}2)${RST} Configure ALE\n"
        printf "  ${WHITE}3)${RST} Configure Modules\n"
        printf "  ${WHITE}4)${RST} View In-Game Commands\n"
        printf "  ${WHITE}5)${RST} Rebuild Worldserver\n"
        printf "  ${GOLD}──────────────────────────────────────────────────${RST}\n"
        printf "  ${DIM}  [ENTER] Back${RST}\n"
        local _tlines; _tlines=$_TERM_LINES
        if ! _read_menu_input "$(( _tlines - 1 ))"; then
            local _read_rc=$?
            [ "$_read_rc" -eq 2 ] && continue
            return
        fi
        local choice="${_MENU_INPUT,,}"
        case "$choice" in
            c)  configure_wow_client; press_enter ;;
            1)  configure_ahbot; press_enter ;;
            2)  configure_ale; press_enter ;;
            3)  menu_module_management ;;
            4)  show_ingame_commands ;;
            5)  rebuild_worldserver; press_enter ;;
            "")  return ;;
            *)  print_warning "Enter C, 1–5 or ENTER to go back."; press_enter ;;
        esac
    done
}
menu_server_modifications() {
    while true; do
        if [ "$_RESIZE_NEEDED" = true ]; then
            _RESIZE_NEEDED=false
            _setup_screen
        fi
        print_header
        print_install_info
        printf "\n\n  ${GOLD}${BOLD}Server Modifications${RST}\n"
        printf "  ${GOLD}──────────────────────────────────────────────────${RST}\n"
        printf "  ${WHITE}1)${RST} Manage AzerothCore Modules\n"
        printf "  ${WHITE}2)${RST} Manage ALE Lua Mods\n"
        printf "  ${WHITE}3)${RST} Manage SQL Mods\n"
        printf "  ${GOLD}──────────────────────────────────────────────────${RST}\n"
        printf "  ${DIM}  [ENTER] Back${RST}\n"
        local _tlines; _tlines=$_TERM_LINES
        if ! _read_menu_input "$(( _tlines - 1 ))"; then
            local _read_rc=$?
            [ "$_read_rc" -eq 2 ] && continue
            return
        fi
        local choice="${_MENU_INPUT,,}"
        case "$choice" in
            1)  menu_modules ;;
            2)  menu_ale_scripts ;;
            3)  menu_sql_mods ;;
            "")  return ;;
            *)  print_warning "Enter 1–3 or ENTER to go back."; press_enter ;;
        esac
    done
}
menu_server_controls() {
    while true; do
        if [ "$_RESIZE_NEEDED" = true ]; then
            _RESIZE_NEEDED=false
            _setup_screen
        fi
        print_header
        print_install_info
        printf "\n\n  ${GOLD}${BOLD}Server Controls${RST}\n"
        printf "  ${GOLD}──────────────────────────────────────────────────${RST}\n"
        printf "  ${WHITE}1)${RST} Server status\n"
        printf "  ${WHITE}2)${RST} Start server\n"
        printf "  ${WHITE}3)${RST} Stop server\n"
        printf "  ${WHITE}4)${RST} Restart server\n"
        printf "  ${WHITE}5)${RST} View logs\n"
        printf "  ${WHITE}6)${RST} Attach to console\n"
        printf "  ${WHITE}7)${RST} Server maintenance\n"
        printf "  ${GOLD}──────────────────────────────────────────────────${RST}\n"
        printf "  ${DIM}  [ENTER] Back${RST}\n"
        local _tlines; _tlines=$_TERM_LINES
        if ! _read_menu_input "$(( _tlines - 1 ))"; then
            local _read_rc=$?
            [ "$_read_rc" -eq 2 ] && continue
            return
        fi
        local choice="${_MENU_INPUT,,}"
        case "$choice" in
            1)  server_status; press_enter ;;
            2)  server_start; press_enter ;;
            3)  server_stop; press_enter ;;
            4)  server_restart; press_enter ;;
            5)  with_full_screen server_logs ;;
            6)  with_full_screen server_attach ;;
            7)  menu_server_maintenance ;;
            "")  return ;;
            *)  print_warning "Enter 1–7 or ENTER to go back."; press_enter ;;
        esac
    done
}
main_menu() {
    _IN_MENU=true
    _setup_screen
    print_header
    while true; do
        _RESIZE_NEEDED=false
        _setup_screen
        print_header
        print_install_info
        printf "\n\n  ${GOLD}${BOLD}Sub-Menus${RST}\n"
        printf "  ${GOLD}──────────────────────────────────────────────────${RST}\n"
        printf "  ${WHITE}1)${RST} Configurations\n"
        printf "  ${WHITE}2)${RST} Server Modifications\n"
        printf "  ${WHITE}3)${RST} Server Controls\n"
        printf "  ${GOLD}──────────────────────────────────────────────────${RST}\n"
        printf "  ${GOLD} Q)${RST} Quit\n"
        local _tlines; _tlines=$_TERM_LINES
        local _irow=$(( _tlines - 1 ))
        if ! _read_menu_input "$_irow"; then
            local _read_rc=$?
            [ "$_read_rc" -eq 2 ] && continue
            return
        fi
        local choice="${_MENU_INPUT,,}"
        case "$choice" in
            1)  menu_configuration ;;
            2)  menu_server_modifications ;;
            3)  menu_server_controls ;;
            q)  echo ""; print_info "Goodbye!"; exit 0 ;;
        esac
    done
}

# Scan all three mod registries and silently populate ingame-commands.txt
# for any mods already installed. Handles upgrades from older manager versions
# that pre-date the commands file system.
sync_ingame_commands_for_installed() {
    [ -z "$INGAME_COMMANDS_FILE" ] && return 0
    local key name entry count=0

    for entry in "${MODULE_REGISTRY[@]}"; do
        IFS='|' read -r key name _ _ <<< "$entry"
        if module_is_installed "$key"; then
            upsert_mod_commands "$key" --quiet
            count=$((count + 1))
        fi
    done

    for entry in "${ALE_SCRIPT_REGISTRY[@]}"; do
        IFS='|' read -r key name _ <<< "$entry"
        if ale_script_is_installed "$key"; then
            upsert_mod_commands "$key" --quiet
            count=$((count + 1))
        fi
    done

    sqlmod_init
    for entry in "${SQL_MOD_REGISTRY[@]}"; do
        IFS='|' read -r key name _ _ <<< "$entry"
        if sqlmod_is_installed "$key"; then
            upsert_mod_commands "$key" --quiet
            count=$((count + 1))
        fi
    done

    [ "$count" -gt 0 ] && \
        print_info "📋 In-game commands reference synced for $count installed mod(s): $INGAME_COMMANDS_FILE"
}

# ─────────────────────────────────────────────────────────────
# ENTRYPOINT
# ─────────────────────────────────────────────────────────────

start_logo_animation
detect_install
sync_ingame_commands_for_installed
show_first_run_welcome
main_menu
