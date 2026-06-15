#!/bin/bash
# ============================================================
#  Dad's MMO Lab — Wrath Unbound Add-On Installer
#  Layers the multi-class Wrath Unbound mod onto an EXISTING
#  Dad's MMO Lab WotLK Playerbots server (AzerothCore + Docker)
#
#  https://github.com/DadsMmoLab/dads-mmo-lab
#
#  Version: 1.2.2 (2026-06-14 — reworks ALE/Lua staging to match
#  wow-manage.sh's convention: unbound_mentor.lua now deploys to
#  $SERVER_DIR/env/dist/etc/modules/lua_scripts/ (the shared ALE-Kegs
#  directory, covered by AzerothCore's stock env/dist/etc bind mount) and
#  mod_ale.conf is configured in place with an absolute ALE.ScriptPath —
#  docker-compose.override.yml is no longer touched and no custom
#  AC_ALE_SCRIPT_PATH env var is added. Fixes a path-mismatch bug where
#  running wow-manage.sh before this installer left unbound_mentor.lua in
#  a directory ALE never scanned (Mentor said "Greetings" only, Mentor
#  Stone just ate). Uninstaller updated to match: removes
#  unbound_mentor.lua from the new and legacy locations, and no longer
#  deletes mod_ale.conf, which other ALE-Kegs Lua mods may share.
#
#  v1.2.1 (2026-06-14) fixed a dbimport duplicate-key bug in v1.2.0's
#  catalog-fix migrations 12-14 that could surface on reinstall/update
#  ("Duplicate entry '2-34769' for key 'unbound_class_catalog.PRIMARY'");
#  same feature set as 1.2.0:
#
#  v1.2.0 (2026-06-13) adds full cross-class access: Mentor-unlocked
#  classes can now train abilities directly from class trainers, equip
#  cross-class gear, and accept that class's quests, via a small AzerothCore
#  core-engine patch (Player::m_unboundClassMask). Mentor UI overhaul: spells
#  now buy instantly with one click, plus a "Buy ALL available abilities"
#  button. Catalog audit: corrected req_levels against real trainers, added
#  missing Mage teleports/portals + Paladin Summon Warhorse, and fixed
#  Paladin Judgement / Paladin+Warlock mount / Druid Flight Form purchases
#  that previously took gold and granted nothing.)
#
#  UPDATING AN EXISTING INSTALL: just re-run this installer. It re-stages
#  every file, re-applies all SQL migrations, applies the new core-engine
#  patch, and rebuilds — safe and idempotent.
#
#  NOT to be confused with install-wow-unbound.sh (The Unbound Era —
#  a separate, from-scratch Vanilla CMaNGOS project).
#
#  What this does:
#    1. Verifies this is a compatible Dad's MMO Lab WotLK install
#    2. Backs up your world/characters databases before touching anything
#    3. Drops in the mod-unbound C++ module + Lua script + SQL migrations
#    4. Stages the Eluna/ALE Lua engine module if your server doesn't have it
#    5. Applies a small core-engine patch enabling cross-class trainer/quest/item access
#    6. Patches worldserver.conf (ValidateSkillLearnedBySpells = 0)
#    7. Rebuilds the worldserver (forced — new compiled module + core patch)
#    8. Walks you through the one manual step (.npc add 900001)
#
#  Prerequisite: a running Dad's MMO Lab WotLK Playerbots server
#  (built via install-wow-wotlk.sh / install-wow.sh). This installer
#  does NOT build a server from scratch.
# ============================================================

WIZARD_VERSION="1.2.2"

# Default server location (install-wow-wotlk.sh / install-wow.sh standard).
# detect_server_dir() in MAIN will auto-detect if this path doesn't exist.
SERVER_DIR="$HOME/wow-server-playerbots"

set -o pipefail

# ─────────────────────────────────────────
# COLORS  (matching install-wow-wotlk.sh conventions)
# ─────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m'
BOLD='\033[1m'

IS_WSL2=false
grep -qi "microsoft\|wsl" /proc/version 2>/dev/null && IS_WSL2=true

print_header() {
    echo -e "${BOLD}${MAGENTA:-$CYAN}=============================================================${NC}"
    echo -e "${BOLD}  Dad's MMO Lab — Wrath Unbound Add-On Installer (v${WIZARD_VERSION})${NC}"
    echo -e "${BOLD}${MAGENTA:-$CYAN}=============================================================${NC}"
    echo ""
}

print_step()    { echo -e "${BLUE}▶ $1${NC}"; }
print_success() { echo -e "${GREEN}✅ $1${NC}"; }
print_warning() { echo -e "${YELLOW}⚠️  $1${NC}"; }

ask_yes_no() {
    local prompt="$1"
    local answer
    while true; do
        read -r -p "$(echo -e "${WHITE}${prompt} [y/n]: ${NC}")" answer
        case "$answer" in
            [Yy]*) return 0 ;;
            [Nn]*) return 1 ;;
            *) echo "Please answer yes or no." ;;
        esac
    done
}

# ============================================================
# Returns 0 (true) if the given path is on the Windows filesystem under WSL2.
# Covers standard WSL2 mounts (/mnt/c/, /mnt/d/, …) and custom root=/ mounts
# where drive letters sit directly under / (/c/, /d/, …).
# Standard Linux root dirs (/home/, /usr/, /bin/, …) are multi-character and
# never match the single-letter /X/ pattern used by Windows drives.
is_windows_fs_path() {
    $IS_WSL2 || return 1
    [[ "$1" =~ ^/mnt/[a-zA-Z]/ ]] && return 0   # /mnt/c/…, /mnt/d/…
    [[ "$1" =~ ^/[a-zA-Z]/ ]]     && return 0   # /c/…, /d/… (custom root=/)
    return 1
}

# ============================================================
#  detect_server_dir()
#
#  Resolves SERVER_DIR at runtime so users who renamed their server
#  folder (e.g. wow-unbound instead of the default wow-server-playerbots)
#  are not hard-blocked. Resolution order:
#
#  1. Default location  ~/wow-server-playerbots  (install-wow-wotlk.sh standard)
#  2. Common rename     ~/wow-unbound
#  3. Shallow scan      $HOME/* directories that look like AzerothCore installs
#     (have both docker-compose.yml and env/dist/ — the AC-specific layout)
#  4. Manual prompt     if nothing is found or the user wants to override
#
#  Windows-filesystem paths (/mnt/c/…, /c/…) are skipped in steps 1-3 and
#  hard-warned in step 4 — running the server from the Windows FS causes severe
#  Docker performance degradation and is not a supported configuration.
#
#  Sets SERVER_DIR on success; exits 1 on failure.
# ============================================================
detect_server_dir() {
    # ── Derive true Linux home ───────────────────────────────────────────────
    # On some WSL2 setups $HOME is set to the Windows user profile
    # (e.g. /c/Users/nolim or /mnt/c/Users/nolim) instead of /home/<user>.
    # When that happens, auto-detection and tilde expansion both produce wrong
    # paths.  Use getent passwd to get the real Linux home directory.
    local LINUX_HOME="$HOME"
    if $IS_WSL2 && [[ "$HOME" != /home/* ]]; then
        local pw_home
        pw_home="$(getent passwd "$(whoami)" 2>/dev/null | cut -d: -f6)"
        if [[ "$pw_home" == /home/* ]]; then
            LINUX_HOME="$pw_home"
        else
            LINUX_HOME="/home/$(whoami)"
        fi
        print_warning "\$HOME is set to '$HOME' (a Windows path on this WSL2 system)."
        echo "Using your Linux home directory instead: $LINUX_HOME"
        echo ""
    fi

    # ── 1 & 2: known locations ───────────────────────────────────────────────
    local windows_found=()   # Windows-FS paths found but skipped
    for candidate in "$LINUX_HOME/wow-server-playerbots" "$LINUX_HOME/wow-unbound"; do
        if [ -d "$candidate" ] && [ -f "$candidate/docker-compose.yml" ]; then
            if is_windows_fs_path "$candidate"; then
                windows_found+=("$candidate")
            else
                SERVER_DIR="$candidate"
                return
            fi
        fi
    done

    # ── 3: shallow scan for AzerothCore installs ────────────────────────────
    # On WSL2, $HOME may be wrong (Windows path) — always include /home/ in the
    # scan so the server is found regardless of $HOME.  Also scan all of /home/
    # when running as root (sudo) since $HOME=/root in that case.
    local scan_roots=("$LINUX_HOME")
    if ($IS_WSL2 || [ "$EUID" -eq 0 ]) && [ -d /home ]; then
        while IFS= read -r -d '' udir; do
            [[ "$udir" == "$LINUX_HOME" ]] || scan_roots+=("$udir")
        done < <(find /home -mindepth 1 -maxdepth 1 -type d -print0 2>/dev/null)
    fi

    local candidates=()
    for scan_root in "${scan_roots[@]}"; do
        while IFS= read -r -d '' dir; do
            if [ -f "$dir/docker-compose.yml" ] && [ -d "$dir/env/dist" ]; then
                if is_windows_fs_path "$dir"; then
                    windows_found+=("$dir")
                else
                    candidates+=("$dir")
                fi
            fi
        done < <(find "$scan_root" -mindepth 1 -maxdepth 1 -type d -not -name ".*" -print0 2>/dev/null)
    done

    if [ "${#candidates[@]}" -eq 1 ]; then
        print_warning "Server not found at the default location (~/wow-server-playerbots)."
        echo -e "  Found a likely AzerothCore install at: ${CYAN}${candidates[0]}${NC}"
        echo ""
        if ask_yes_no "Use this as your server folder?"; then
            SERVER_DIR="${candidates[0]}"
            return
        fi
    elif [ "${#candidates[@]}" -gt 1 ]; then
        print_warning "Server not found at the default location. Multiple candidates found:"
        echo ""
        for i in "${!candidates[@]}"; do
            echo -e "  ${CYAN}$((i+1)).${NC} ${candidates[$i]}"
        done
        echo ""
        local choice
        while true; do
            read -r -p "$(echo -e "${WHITE}Enter the number of your WotLK Playerbots server: ${NC}")" choice
            if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le "${#candidates[@]}" ]; then
                SERVER_DIR="${candidates[$((choice-1))]}"
                return
            fi
            echo "Please enter a number between 1 and ${#candidates[@]}."
        done
    fi

    # ── Windows-FS explanation (shown when only Windows paths were found) ────
    if [ "${#windows_found[@]}" -gt 0 ]; then
        echo ""
        print_warning "Found server folder(s) on your Windows filesystem — cannot use them:"
        for wf in "${windows_found[@]}"; do
            echo -e "  ${YELLOW}$wf${NC}"
        done
        echo ""
        echo "Running a WoW server from the Windows filesystem (/mnt/c/ or /c/) causes"
        echo "severe Docker I/O performance issues and is not a supported configuration."
        echo ""
        echo "Your server needs to be on the Linux filesystem. To fix this:"
        echo -e "  1. Copy the installer to your Linux home:"
        echo -e "     ${CYAN}cp /mnt/c/Users/\$(cmd.exe /c 'echo %USERNAME%' 2>/dev/null | tr -d '\r')/Downloads/install-wow-wotlk.sh ~/Downloads/${NC}"
        echo -e "     (or download it fresh from github.com/DadsMmoLab)"
        echo -e "  2. Run it from this terminal:"
        echo -e "     ${CYAN}bash ~/Downloads/install-wow-wotlk.sh${NC}"
        echo ""
    fi

    # ── 4: manual prompt ─────────────────────────────────────────────────────
    echo ""
    print_warning "Could not find a Dad's MMO Lab WotLK Playerbots install automatically."
    echo "Wrath Unbound adds onto an EXISTING server built with install-wow-wotlk.sh."
    echo ""
    # Running with sudo changes \$HOME to /root, which is why auto-detection
    # misses a server installed under the real user's home directory.
    if [ "$EUID" -eq 0 ]; then
        print_warning "You appear to be running as root (sudo)."
        echo "This changes \$HOME to /root, so auto-detection can't find your server."
        echo "Re-run without sudo: bash install-wrath-unbound-addon.sh"
        echo "Or enter the absolute path below (e.g. /home/username/wow-server-playerbots)."
        echo ""
    fi

    # WSL2: server must be on the Linux filesystem, not the Windows C: drive.
    if $IS_WSL2; then
        echo -e "${YELLOW}WSL2 detected.${NC} Your server must be on the Linux filesystem."
        echo "Do NOT use a /mnt/c/ path — that's your Windows drive."
        echo -e "Your server is most likely at: ${CYAN}/home/$(whoami)/wow-server-playerbots${NC}"
        echo ""
    fi

    local input expanded attempts=0
    while true; do
        attempts=$((attempts + 1))
        echo -e "${WHITE}Enter the full path to your server folder:${NC}"
        echo -e "${CYAN}Use an absolute path: /home/username/wow-server-playerbots${NC}"
        echo -e "${CYAN}  or tilde shorthand: ~/wow-server-playerbots${NC}"
        read -r input

        # Expand leading ~/ → LINUX_HOME/  (only when followed by / or end of
        # string).  Use LINUX_HOME (not $HOME) so tilde expansion is correct
        # even on WSL2 where $HOME may point to the Windows user profile.
        if [[ "$input" == "~/"* ]]; then
            expanded="${LINUX_HOME}/${input#~/}"
        elif [[ "$input" == "~" ]]; then
            expanded="$LINUX_HOME"
        else
            expanded="$input"
        fi

        # Hard-warn if the user types a Windows-filesystem path.
        if is_windows_fs_path "$expanded"; then
            echo ""
            print_warning "That path is on your Windows filesystem — not a supported location."
            echo "Running the server from /mnt/c/ or /c/ causes severe Docker performance"
            echo "degradation. Your server needs to be on the Linux filesystem:"
            echo -e "  ${CYAN}/home/$(whoami)/wow-server-playerbots${NC}"
            echo ""
            echo "Copy install-wow-wotlk.sh to your Linux home and re-run it from this"
            echo "terminal to install the base server in the right place:"
            echo -e "  ${CYAN}bash ~/Downloads/install-wow-wotlk.sh${NC}"
            echo ""
            if ask_yes_no "Proceed with this Windows-filesystem path anyway? (not recommended)"; then
                print_warning "Proceeding — expect performance issues and potential failures."
                echo ""
            else
                echo ""
                attempts=$((attempts - 1))   # don't count a Windows-path attempt
                continue
            fi
        fi

        if [ -d "$expanded" ] && [ -f "$expanded/docker-compose.yml" ]; then
            SERVER_DIR="$expanded"
            return
        fi

        echo ""
        print_warning "No server found at: $expanded"
        if [[ "$input" == "~"* ]] && [[ "$input" != "~/"* ]]; then
            echo "It looks like you typed '~home/...' or '~username/...' — that's not"
            echo "standard tilde syntax. Use a full absolute path instead:"
            echo -e "  ${CYAN}/home/$(whoami)/wow-server-playerbots${NC}"
        elif [ ! -d "$expanded" ]; then
            echo "That directory doesn't exist."
            local parent
            parent="$(dirname "$expanded")"
            if [ -d "$parent" ]; then
                echo -e "Folders found in ${CYAN}$parent${NC}:"
                ls -1 "$parent" 2>/dev/null | while read -r name; do
                    echo "  $name"
                done
            fi
        else
            echo "That directory exists but has no docker-compose.yml — it may not be"
            echo "a Dad's MMO Lab WotLK Playerbots install."
            echo -e "Folders found in ${CYAN}$(dirname "$expanded")${NC}:"
            ls -1 "$(dirname "$expanded")" 2>/dev/null | while read -r name; do
                echo "  $name"
            done
        fi
        echo ""

        if [ "$attempts" -ge 3 ]; then
            print_warning "Could not find a valid server folder after 3 attempts."
            echo "Make sure install-wow-wotlk.sh was run first, then try again."
            exit 1
        fi
    done
}

# ============================================================
#  check_compatibility()
#
#  Wrath Unbound's spell catalog (unbound_class_catalog) is built by
#  reading a specific set of SYNTHETIC npc_trainer rows (IDs 200002,
#  200004, 200006, 200008, 200010, 200012, 200014, 200016, 200018 —
#  one per class) that the mod-playerbots fork generates internally
#  for bot AI to query trainer spell lists. These rows have NO
#  associated creature_template entry — they are not real NPCs, just
#  incidental seed data from one specific build
#  (core_revision e98e7a97e3f2+, Playerbot branch, ACDB 335.16-dev).
#
#  This is NOT a documented, stable schema — a different Playerbots
#  build could produce different IDs or a different ID→class mapping,
#  silently breaking catalog population (empty catalog, or worse,
#  spells mapped to the wrong class). Wrath Unbound is therefore
#  scoped EXCLUSIVELY to Dad's MMO Lab WotLK Playerbots installs —
#  this function is the gate that enforces that.
#
#  Live-verified thresholds (2026-06-08, against the dev server):
#  actual = 9 distinct IDs / 1858 spell rows. Gate set well below
#  that (9 / 100) to tolerate normal build-to-build seed variance
#  while still rejecting servers with none of this data (0 / 0).
# ============================================================
check_compatibility() {
    print_step "Checking that this is a compatible Dad's MMO Lab Playerbots server..."

    local DB_RUNNING
    DB_RUNNING=$(docker compose ps -q ac-database 2>/dev/null)
    if [ -z "$DB_RUNNING" ]; then
        print_warning "Could not find a running ac-database container."
        echo "Wrath Unbound adds onto an EXISTING running Dad's MMO Lab WotLK server —"
        echo "start your server first (docker compose up -d), then run this installer."
        exit 1
    fi

    # ── Check 1: build fingerprint (informational — warn, don't block) ──
    local VERSION_INFO CORE_REV DB_VER
    VERSION_INFO=$(docker exec ac-database mysql -u root -ppassword acore_world -N \
        -e "SELECT core_revision, db_version FROM version LIMIT 1;" 2>/dev/null)
    CORE_REV=$(echo "$VERSION_INFO" | awk -F'\t' '{print $1}')
    DB_VER=$(echo "$VERSION_INFO"   | awk -F'\t' '{print $2}')

    echo -e "${CYAN}   Detected build: ${CORE_REV:-unknown} / ${DB_VER:-unknown}${NC}"

    if [[ "$CORE_REV" != e98e7a97e3f2* ]]; then
        print_warning "This server's core revision differs from the build Wrath Unbound"
        print_warning "was developed and tested against (e98e7a97e3f2+, Playerbot branch,"
        print_warning "ACDB 335.16-dev, 2026-05-29)."
        echo ""
        echo "It MAY still work if this is a Dad's MMO Lab WotLK install from a nearby"
        echo "build — the check below is what actually determines compatibility."
        echo ""
    else
        print_success "Core build matches the known-compatible baseline."
    fi

    # ── Check 2: the REAL dependency — synthetic Playerbots trainer seed data ──
    # This is a hard gate. If these rows are missing, wrong, or differently
    # mapped, the catalog migration will silently produce an empty or
    # incorrectly-mapped catalog — there is no safe way to proceed.
    local DISTINCT_IDS TOTAL_ROWS
    read -r DISTINCT_IDS TOTAL_ROWS <<< "$(docker exec ac-database mysql -u root -ppassword acore_world -N -e "
        SELECT COUNT(DISTINCT ID), COUNT(*)
        FROM npc_trainer
        WHERE ID IN (200002,200004,200006,200008,200010,200012,200014,200016,200018)
          AND SpellID > 0;" 2>/dev/null)"

    DISTINCT_IDS=${DISTINCT_IDS:-0}
    TOTAL_ROWS=${TOTAL_ROWS:-0}

    echo -e "${CYAN}   Found Playerbots trainer seed data for ${DISTINCT_IDS}/9 classes"
    echo -e "${CYAN}   (${TOTAL_ROWS} spell entries total).${NC}"

    if [ "$DISTINCT_IDS" -lt 9 ] || [ "$TOTAL_ROWS" -lt 100 ]; then
        echo ""
        print_warning "This server is missing the Playerbots trainer seed data that"
        print_warning "Wrath Unbound's spell catalog depends on."
        echo ""
        echo -e "${RED}Wrath Unbound is built specifically for Dad's MMO Lab WotLK Playerbots${NC}"
        echo -e "${RED}servers. This install doesn't match — installing here would produce${NC}"
        echo -e "${RED}an empty or broken spell catalog (or worse, map spells to the wrong${NC}"
        echo -e "${RED}classes). No changes have been made.${NC}"
        echo ""
        echo "If you believe this IS a Dad's MMO Lab install and are seeing this in"
        echo "error, please report it — this check may need adjusting for your build."
        exit 1
    fi

    print_success "Compatible Playerbots trainer data found — this looks like a"
    print_success "Dad's MMO Lab WotLK install. Proceeding."
    echo ""
}

# ============================================================
#  check_existing_install()
#
#  Two things to surface before touching the database, folded into one
#  gate + one confirmation:
#
#  1. Canary check — does this server already have Wrath Unbound on it?
#     (unbound_milestones is created by migration 01 and only exists on
#     servers that have already run this installer or had the mod
#     applied by hand.) Re-running is largely safe — the SQL audit
#     found most migrations idempotent.
#
#  2. Destructive-step warning (folded in from a separate
#     warn_destructive_steps() during scoping) — migrations 03 and 05
#     DELETE-then-INSERT rows in playercreateinfo_spell_custom for the
#     classes Wrath Unbound manages. That's the same table any
#     non-Wrath-Unbound custom creation-gift edits for those classes
#     would live in, so this ALWAYS needs surfacing — on a fresh
#     install just as much as a re-run — not only when an existing
#     Wrath Unbound install is detected.
# ============================================================
EXISTING_INSTALL=false

check_existing_install() {
    print_step "Checking for an existing Wrath Unbound install..."

    local CANARY
    CANARY=$(docker exec ac-database mysql -u root -ppassword acore_world -N \
        -e "SELECT 1 FROM unbound_milestones LIMIT 1;" 2>/dev/null)

    if [ "$CANARY" = "1" ]; then
        EXISTING_INSTALL=true
        print_warning "Wrath Unbound already appears to be installed on this server"
        print_warning "(found existing unbound_milestones data)."
        echo ""
        echo "Re-running this installer will re-apply its SQL migrations, restage"
        echo "the module files, and rebuild the worldserver. This only touches"
        echo "Wrath Unbound's own data and files — nothing else on your server."
    else
        print_success "No existing Wrath Unbound install detected — proceeding with a fresh install."
    fi
    echo ""

    print_warning "One thing worth knowing before you continue:"
    echo ""
    echo "Two of Wrath Unbound's SQL migrations (03 and 05) manage rows in"
    echo "'playercreateinfo_spell_custom' — the table that controls which spells"
    echo "characters receive at creation — for Warrior, Paladin, Hunter, Rogue,"
    echo "Priest, Shaman, Mage, Warlock, and Druid. They delete and re-insert"
    echo "those classes' rows every time they run."
    echo ""
    echo "If you've made your OWN custom creation-gift edits for those classes"
    echo "(outside of Wrath Unbound), this will overwrite them with Wrath"
    echo "Unbound's gift list. Nothing else on your server is touched, and your"
    echo "databases get backed up before any of this runs."
    echo ""

    if ! ask_yes_no "Continue?"; then
        echo "No changes made. Exiting."
        exit 0
    fi
    echo ""
}

# ============================================================
#  backup_database()
#
#  mysqldumps acore_world and acore_characters to a timestamped folder
#  before any migrations run. Cheap insurance — the SQL audit flagged
#  destructive DELETE-then-INSERT blocks in migrations 03/05, and a
#  rebuild + restart is involved, so a one-command restore path matters.
# ============================================================
BACKUP_DIR=""

backup_database() {
    print_step "Backing up your world and characters databases before making changes..."

    local TIMESTAMP
    TIMESTAMP=$(date +%Y%m%d-%H%M%S)
    BACKUP_DIR="$HOME/wrath-unbound-backups/$TIMESTAMP"
    mkdir -p "$BACKUP_DIR"

    local DB
    for DB in acore_world acore_characters; do
        echo -e "${CYAN}   Dumping ${DB}...${NC}"
        if ! docker exec ac-database mysqldump -u root -ppassword "$DB" > "$BACKUP_DIR/${DB}.sql" 2>/dev/null; then
            print_warning "Failed to back up ${DB} — aborting before making any changes."
            rm -rf "$BACKUP_DIR"
            exit 1
        fi
    done

    print_success "Backup saved to: $BACKUP_DIR"
    echo "If anything goes wrong, you can restore with:"
    echo -e "${CYAN}   docker exec -i ac-database mysql -u root -ppassword acore_world < $BACKUP_DIR/acore_world.sql${NC}"
    echo -e "${CYAN}   docker exec -i ac-database mysql -u root -ppassword acore_characters < $BACKUP_DIR/acore_characters.sql${NC}"
    echo ""
}

# ============================================================
#  stage_module_files()
#
#  Writes the Wrath Unbound payload (mod-unbound C++ module,
#  Lua script, SQL migrations, NPC setup) into the target server
#  tree. Payload is embedded inline below (heredocs) so this
#  installer is a single self-contained file — matches the
#  one-click feel of the rest of the Dad's MMO Lab suite.
#
#  NOTE FOR MAINTAINERS: the embedded blocks below are generated,
#  not hand-written. To refresh them after editing the live
#  source in ~/wow-server-playerbots/, regenerate this function
#  from the source tree rather than hand-editing the heredocs.
# ============================================================
stage_module_files() {
    print_step "Staging Wrath Unbound module files into your server..."

    local MODULE_DIR="$SERVER_DIR/modules/mod-unbound"
    mkdir -p "$MODULE_DIR/src"
    mkdir -p "$MODULE_DIR/data/sql/db-world"
    mkdir -p "$MODULE_DIR/data/sql/db-characters"
    mkdir -p "$SERVER_DIR/env/dist/etc/modules/lua_scripts"

    cat > "$MODULE_DIR/src/UnboundSystem.cpp" <<'WU_PAYLOAD_EOF_1'
#include "Player.h"
#include "ScriptMgr.h"
#include "ScriptDefines/PlayerScript.h"
#include "DatabaseEnv.h"
#include "Entities/Item/ItemTemplate.h"

// Unbound Wrath Edition — power chassis + weapon/armor proficiency hooks.
//
// OnPlayerHasActivePowerType:
//   AzerothCore gates ALL rage/energy generation through HasActivePowerType.
//   We intercept so any non-native power type the Lua system granted via
//   SetMaxPower > 0 actually generates in combat.
//
// OnPlayerLogin:
//   learnSkillRewardedSpells() (called when weapon skills are set) filters
//   proficiency spells by ClassMask.  A Paladin who unlocks Warrior will have
//   Swords/Axes/etc. proficiency (Paladin's ClassMask matches those entries)
//   but NOT Staves/Daggers/Wands/Bows (ClassMask excludes Paladin).
//   The client therefore shows those weapons as red/unequippable.
//   Fix: if the player is Unbound (has any entry in unbound_character_unlocks),
//   grant full weapon + armor proficiency and send SMSG_SET_PROFICIENCY so the
//   client updates immediately.  This fires after the player is in-world.
//
//   Also builds player->m_unboundClassMask (bitmask of EXTRA classes unlocked
//   via the Mentor, NOT including the native class; 0 = not Unbound) from
//   unbound_character_unlocks. CanUseItem, IsSpellFitByClassAndRace, and
//   SatisfyQuestClass (Player/PlayerStorage/PlayerQuest .cpp) consult this mask
//   so item, trainer-spell, and class-quest restrictions are relaxed only for this
//   character — item_template/SkillLineAbility/quest_template stay untouched, so
//   Playerbots' own class-appropriateness heuristics (which read those tables
//   directly) are unaffected for the random bot population.
//
// Everything else lives in env/dist/etc/modules/lua_scripts/unbound_mentor.lua.

class UnboundPlayerScript : public PlayerScript
{
public:
    UnboundPlayerScript() : PlayerScript("UnboundPlayerScript",
    {
        PLAYERHOOK_ON_PLAYER_HAS_ACTIVE_POWER_TYPE,
        PLAYERHOOK_ON_LOGIN,
        PLAYERHOOK_ON_AFTER_UPDATE_MAX_POWER
    }) {}

    // Prevent AzerothCore's UpdateMaxPower from wiping a Lua-set mana pool.
    // For non-caster classes (warriors, rogues, etc.) GetCreatePowers(POWER_MANA)
    // returns 0, so the recalculation always produces 0 — silently erasing whatever
    // SetMaxPower set.  We intercept here (before SetMaxPower is called) and restore
    // the previously stored value if it was non-zero.
    void OnPlayerAfterUpdateMaxPower(Player* player, Powers& power, float& value) override
    {
        if (power != POWER_MANA)
            return;
        if (player->getPowerType() == POWER_MANA)
            return;  // native caster — let normal calculation stand
        if (value > 0.0f)
            return;  // calculated a real value — don't interfere
        uint32 current = player->GetMaxPower(POWER_MANA);
        if (current > 0)
            value = static_cast<float>(current);
    }

    bool OnPlayerHasActivePowerType(Player const* player, Powers power) override
    {
        if (player->getPowerType() == power)
            return false;

        return player->GetMaxPower(power) > 0;
    }

    void OnPlayerLogin(Player* player) override
    {
        // Skip bots — they don't need cross-class weapon proficiency or
        // the Unbound class mask (Playerbots' own heuristics read
        // item_template/SkillLineAbility/quest_template directly and must
        // see the bot's native class only).
        if (player->GetSession()->IsBot())
            return;

        // Build the Unbound class mask: bitmask of EXTRA classes unlocked
        // via the Mentor, NOT including the native class (0 = not Unbound).
        // CanUseItem (PlayerStorage.cpp) checks GetUnboundClassMask() != 0
        // to bypass AllowableClass entirely; IsSpellFitByClassAndRace
        // (Player.cpp) and SatisfyQuestClass (PlayerQuest.cpp) instead OR
        // this onto getClassMask() to widen the effective class set.
        uint32 unboundClassMask = 0;

        QueryResult result = CharacterDatabase.Query(
            "SELECT class_id FROM unbound_character_unlocks WHERE char_guid = {}",
            player->GetGUID().GetCounter());

        if (result)
        {
            do
            {
                Field* fields = result->Fetch();
                uint8 classId = fields[0].Get<uint8>();
                unboundClassMask |= (1u << (classId - 1));
            } while (result->NextRow());
        }

        player->SetUnboundClassMask(unboundClassMask);

        // Not Unbound — nothing else to do.
        if (unboundClassMask == 0)
            return;

        // Grant full weapon and armor proficiency so the client shows all
        // weapon/armor types as equippable (not red).
        // The server-side equip check (GetSkillValue > 0) is handled by the
        // Lua layer which calls SetSkill for all weapon/armor skill IDs.
        uint32 allWeapons = (1u << MAX_ITEM_SUBCLASS_WEAPON) - 1u;
        uint32 allArmor   = (1u << MAX_ITEM_SUBCLASS_ARMOR)  - 1u;

        player->AddWeaponProficiency(allWeapons);
        player->AddArmorProficiency(allArmor);
        player->SendProficiency(ITEM_CLASS_WEAPON, player->GetWeaponProficiency());
        player->SendProficiency(ITEM_CLASS_ARMOR,  player->GetArmorProficiency());
    }
};

void AddUnboundScripts()
{
    new UnboundPlayerScript();
}
// cache-bust: 1781408710
WU_PAYLOAD_EOF_1

    cat > "$MODULE_DIR/src/UnboundSystem_loader.cpp" <<'WU_PAYLOAD_EOF_2'
// AzerothCore module loader — registers AddUnboundScripts() with the engine.
// The top-level modules/CMakeLists.txt calls Addmod_unboundScripts(),
// which this file defines by forwarding to our actual registration function.

void AddUnboundScripts();

void Addmod_unboundScripts()
{
    AddUnboundScripts();
}
WU_PAYLOAD_EOF_2

    cat > "$MODULE_DIR/npc_setup.sql" <<'WU_PAYLOAD_EOF_3'
-- Unbound Wrath Edition — Mentor NPC setup
-- Run once against acore_world AFTER the server has fully initialized.
-- Safe to re-run: INSERT IGNORE skips if entry already exists.
--
-- AzerothCore dropped `scale`, `mechanic_immune_mask`, and
-- `spell_school_immune_mask` from creature_template in migration
-- 2026_03_22_03.  This file uses the post-migration schema.
--
-- Apply:
--   docker exec -i <db-container> mysql -u root -p<pass> acore_world < npc_setup.sql
--
-- Then spawn in-game:
--   .npc add 900001

INSERT IGNORE INTO `creature_template`
    (`entry`, `name`, `subname`, `gossip_menu_id`,
     `minlevel`, `maxlevel`, `exp`, `faction`, `npcflag`,
     `speed_walk`, `speed_run`, `speed_swim`, `speed_flight`,
     `detection_range`, `rank`, `dmgschool`,
     `DamageModifier`, `BaseAttackTime`, `RangeAttackTime`,
     `BaseVariance`, `RangeVariance`,
     `unit_class`, `unit_flags`, `unit_flags2`, `dynamicflags`,
     `family`, `type`, `type_flags`,
     `lootid`, `pickpocketloot`, `skinloot`,
     `PetSpellDataId`, `VehicleId`, `mingold`, `maxgold`,
     `AIName`, `MovementType`, `HoverHeight`,
     `HealthModifier`, `ManaModifier`, `ArmorModifier`, `ExperienceModifier`,
     `RacialLeader`, `movementId`, `RegenHealth`,
     `flags_extra`, `ScriptName`, `VerifiedBuild`)
VALUES
    (900001, 'The Mentor', 'Unbound Class Trainer', 0,
     80, 80, 0, 35, 1,
     1.0, 1.14286, 1.0, 1.0,
     18, 0, 0,
     1.0, 1500, 2000,
     1.0, 1.0,
     1, 768, 2048, 0,
     0, 7, 0,
     0, 0, 0,
     0, 0, 0, 0,
     '', 0, 1.0,
     1.0, 1.0, 1.0, 1.0,
     0, 0, 1,
     2, '', 12340);

-- DisplayID 19097 = Ethereal Thief — final model, locked in by Joshua + Caitlin.
INSERT IGNORE INTO `creature_template_model`
    (`CreatureID`, `Idx`, `CreatureDisplayID`, `DisplayScale`, `Probability`, `VerifiedBuild`)
VALUES
    (900001, 0, 19097, 1.0, 1.0, 12340);
WU_PAYLOAD_EOF_3

    cat > "$MODULE_DIR/data/sql/db-world/01_unbound_world.sql" <<'WU_PAYLOAD_EOF_4'
-- Unbound Wrath Edition — world DB schema
-- Run against: acore_world
-- Safe to re-run: all tables use CREATE TABLE IF NOT EXISTS; INSERTs use IGNORE / ON DUPLICATE KEY.

-- ============================================================
-- Milestone ladder (how many gold each class unlock costs)
-- ============================================================
CREATE TABLE IF NOT EXISTS `unbound_milestones` (
    `milestone_index`    TINYINT UNSIGNED NOT NULL,
    `required_level`     TINYINT UNSIGNED NOT NULL,
    `unlock_cost_copper` INT UNSIGNED     NOT NULL,
    PRIMARY KEY (`milestone_index`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO `unbound_milestones` (`milestone_index`, `required_level`, `unlock_cost_copper`) VALUES
(1,  5,        0),       -- 1st class: free at level 5
(2,  25,   30000),       -- 2nd class: 3g at level 25
(3,  50,  800000),       -- 3rd class: 80g at level 50
(4,  70, 3000000),       -- 4th class: 300g at level 70
(5,  80,15000000);       -- 5th+ class: 1500g each at level 80 (index 5 is reused for all subsequent unlocks)

-- ============================================================
-- Purchasable spell catalog, populated from Playerbots trainer
-- data (npc_trainer IDs 200002–200018).
--
-- class_id follows WoW class constants:
--   1=Warrior  2=Paladin  3=Hunter   4=Rogue   5=Priest
--   7=Shaman   8=Mage     9=Warlock  11=Druid
-- ============================================================
CREATE TABLE IF NOT EXISTS `unbound_class_catalog` (
    `class_id`         TINYINT UNSIGNED NOT NULL,
    `spell_id`         INT UNSIGNED     NOT NULL,
    `gold_cost_copper` INT UNSIGNED     NOT NULL DEFAULT 0,
    `req_level`        TINYINT UNSIGNED NOT NULL DEFAULT 1,
    PRIMARY KEY (`class_id`, `spell_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Populate from Playerbots synthetic trainer templates.
-- These are the same spells WotLK class trainers teach;
-- Playerbots loaded them into npc_trainer so bots can learn them.
-- Trainer template → class ID mapping (verified against creature subnames):
--   200002 = Warrior  200004 = Paladin  200006 = Druid
--   200008 = Mage     200010 = Warlock  200012 = Priest
--   200014 = Hunter   200016 = Rogue    200018 = Shaman
INSERT INTO `unbound_class_catalog` (`class_id`, `spell_id`, `gold_cost_copper`, `req_level`)
SELECT
    CASE `nt`.`ID`
        WHEN 200002 THEN 1
        WHEN 200004 THEN 2
        WHEN 200006 THEN 11
        WHEN 200008 THEN 8
        WHEN 200010 THEN 9
        WHEN 200012 THEN 5
        WHEN 200014 THEN 3
        WHEN 200016 THEN 4
        WHEN 200018 THEN 7
    END                         AS `class_id`,
    `nt`.`SpellID`              AS `spell_id`,
    `nt`.`MoneyCost`            AS `gold_cost_copper`,
    `nt`.`ReqLevel`             AS `req_level`
FROM `npc_trainer` `nt`
WHERE `nt`.`ID` IN (200002, 200004, 200006, 200008, 200010, 200012, 200014, 200016, 200018)
  AND `nt`.`SpellID` > 0
ON DUPLICATE KEY UPDATE
    `gold_cost_copper` = VALUES(`gold_cost_copper`),
    `req_level`        = VALUES(`req_level`);

-- ============================================================
-- Mentor NPC creature_template + model:
-- NOT applied here to avoid touching vanilla tables in the
-- auto-update path.  Run npc_setup.sql manually once, or use:
--   .npc add 900001   (after running npc_setup.sql)
-- ============================================================
WU_PAYLOAD_EOF_4

    cat > "$MODULE_DIR/data/sql/db-world/02_fix_catalog_req_level.sql" <<'WU_PAYLOAD_EOF_5'
-- Fix: lower tier-1 spell req_level from 8 → 1 so newly unlocked classes
-- have buyable abilities immediately (first milestone unlocks at level 5).
-- All classes had min req_level=8 from Playerbots trainer data, causing a
-- level 5-7 player to see "no abilities available" after unlocking a class.
UPDATE `unbound_class_catalog` SET `req_level` = 1 WHERE `req_level` <= 8;
WU_PAYLOAD_EOF_5

    cat > "$MODULE_DIR/data/sql/db-world/03_creation_gift_spells.sql" <<'WU_PAYLOAD_EOF_6'
-- Unbound Wrath Edition — per-class character-creation gift spells
-- Applied to: acore_world.playercreateinfo_spell_custom
--
-- These are granted for FREE when a player unlocks a class via the Mentor NPC,
-- matching exactly what a freshly-created level-1 character of that class receives.
-- "Aelric opens the door; trainers fill the rooms."
--
-- classmask = 2^(classId-1):
--   Warrior=1  Paladin=2  Hunter=4  Rogue=8  Priest=16
--   Shaman=64  Mage=128   Warlock=256  Druid=1024
-- racemask = 0 means all races.
--
-- Apply: docker exec ac-database mysql -u root -ppassword acore_world < this_file.sql

-- Clear any previous entries so this file is safe to re-run
DELETE FROM playercreateinfo_spell_custom WHERE racemask = 0 AND classmask IN (1,2,4,8,16,64,128,256,1024);

-- ── Warrior (classmask=1) ────────────────────────────────────────────────────
-- All 3 stances + starting combat abilities
INSERT INTO playercreateinfo_spell_custom (racemask, classmask, Spell, Note) VALUES
(0, 1, 2457, 'Warrior - Battle Stance'),
(0, 1, 71,   'Warrior - Defensive Stance'),
(0, 1, 2458, 'Warrior - Berserker Stance'),
(0, 1, 78,   'Warrior - Heroic Strike r1'),
(0, 1, 6673, 'Warrior - Battle Shout r1'),
(0, 1, 100,  'Warrior - Charge r1');

-- ── Paladin (classmask=2) ────────────────────────────────────────────────────
-- Judgement is the core rotation ability — without it, an Unbound Paladin's
-- Seal is permanently inert. The trainer-taught ID (10321) has a
-- SPELL_EFFECT_LEARN_SPELL effect, which Mentor-driven grants silently fail
-- (see 14_judgement_fix.sql). This row is inserted as 10321 and corrected to
-- 20271 ("Judgement of Light" — the real castable Judgement button, confirmed
-- working via Testpal) by 14_judgement_fix.sql, which must run after this file.
INSERT INTO playercreateinfo_spell_custom (racemask, classmask, Spell, Note) VALUES
(0, 2, 635,   'Paladin - Holy Light r1'),
(0, 2, 20154, 'Paladin - Seal of Righteousness r1'),
(0, 2, 465,   'Paladin - Devotion Aura r1'),
(0, 2, 10321, 'Paladin - Judgement');

-- ── Hunter (classmask=4) ────────────────────────────────────────────────────
INSERT INTO playercreateinfo_spell_custom (racemask, classmask, Spell, Note) VALUES
(0, 4, 75,    'Hunter - Auto Shot'),
(0, 4, 2973,  'Hunter - Raptor Strike r1'),
(0, 4, 13165, 'Hunter - Aspect of the Hawk r1');

-- ── Rogue (classmask=8) ─────────────────────────────────────────────────────
INSERT INTO playercreateinfo_spell_custom (racemask, classmask, Spell, Note) VALUES
(0, 8, 1784, 'Rogue - Stealth r1'),
(0, 8, 1752, 'Rogue - Sinister Strike r1'),
(0, 8, 2098, 'Rogue - Eviscerate r1');

-- ── Priest (classmask=16) ────────────────────────────────────────────────────
INSERT INTO playercreateinfo_spell_custom (racemask, classmask, Spell, Note) VALUES
(0, 16, 585,  'Priest - Smite r1'),
(0, 16, 2050, 'Priest - Lesser Heal r1');

-- ── Shaman (classmask=64) ────────────────────────────────────────────────────
-- All 4 starter totems are gifted so Shaman spells that require totems work
-- immediately. 2484=Earthbind Totem; totem items (5175-5178) are given by
-- GrantClassGiftItems in the Lua (CLASS_GIFT_ITEMS[7]).
INSERT INTO playercreateinfo_spell_custom (racemask, classmask, Spell, Note) VALUES
(0, 64, 403,  'Shaman - Lightning Bolt r1'),
(0, 64, 331,  'Shaman - Healing Wave r1'),
(0, 64, 8071, 'Shaman - Stoneskin Totem r1 (Earth)'),
(0, 64, 8042, 'Shaman - Searing Totem r1 (Fire)'),
(0, 64, 5394, 'Shaman - Healing Stream Totem r1 (Water)'),
(0, 64, 8512, 'Shaman - Windfury Totem r1 (Air)'),
(0, 64, 2484, 'Shaman - Earthbind Totem');

-- ── Mage (classmask=128) ────────────────────────────────────────────────────
-- Arcane Intellect (1459) is a key Mage utility spell taught by trainer at level 1
-- but not included in Playerbots creation data — must be explicitly gifted.
INSERT INTO playercreateinfo_spell_custom (racemask, classmask, Spell, Note) VALUES
(0, 128, 133,  'Mage - Fireball r1'),
(0, 128, 168,  'Mage - Frost Armor r1'),
(0, 128, 1459, 'Mage - Arcane Intellect r1');

-- ── Warlock (classmask=256) ─────────────────────────────────────────────────
INSERT INTO playercreateinfo_spell_custom (racemask, classmask, Spell, Note) VALUES
(0, 256, 686, 'Warlock - Shadow Bolt r1'),
(0, 256, 687, 'Warlock - Demon Skin'),
(0, 256, 688, 'Warlock - Summon Imp');

-- ── Druid (classmask=1024) ───────────────────────────────────────────────────
-- Bear Form and Aquatic Form are sold via the Mentor catalog, not gifted free.
-- Bear Form: 5 silver (500 copper) — see 04_catalog_druid_forms.sql
-- Aquatic Form: already in catalog at 900 copper from Playerbots trainer data.
INSERT INTO playercreateinfo_spell_custom (racemask, classmask, Spell, Note) VALUES
(0, 1024, 5176, 'Druid - Wrath r1'),
(0, 1024, 5185, 'Druid - Healing Touch r1');
WU_PAYLOAD_EOF_6

    cat > "$MODULE_DIR/data/sql/db-world/04_catalog_druid_forms.sql" <<'WU_PAYLOAD_EOF_7'
-- Unbound Wrath Edition — add missing druid shapeshift forms to catalog
-- Bear Form was a class quest in vanilla, so the Playerbots trainer
-- template (200006) never included it.  Add it manually at a custom price.
-- All other forms are already present from the Playerbots trainer data.

INSERT INTO unbound_class_catalog (class_id, spell_id, gold_cost_copper, req_level)
VALUES (11, 5487, 500, 10)
ON DUPLICATE KEY UPDATE gold_cost_copper = 500, req_level = 10;
WU_PAYLOAD_EOF_7

    cat > "$MODULE_DIR/data/sql/db-world/05_individual_purchase_prereqs.sql" <<'WU_PAYLOAD_EOF_8'
-- Unbound Wrath Edition — individual spell purchase with rank prerequisites
-- Applied to: acore_world
--
-- 1. Add prereq_spell column to unbound_class_catalog
--    Populated from npc_trainer.ReqSpell (the prerequisite rank).
-- 2. Update shaman creation gifts: add missing starter totems.

-- ── 1. prereq_spell column ────────────────────────────────────────────────
-- MySQL 8 on this server doesn't support ADD COLUMN IF NOT EXISTS; use stored proc pattern
SET @col_exists = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = 'acore_world'
      AND TABLE_NAME   = 'unbound_class_catalog'
      AND COLUMN_NAME  = 'prereq_spell');
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE unbound_class_catalog ADD COLUMN prereq_spell INT UNSIGNED NOT NULL DEFAULT 0',
    'SELECT ''prereq_spell column already exists''');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- Populate prereq_spell from npc_trainer.ReqSpell for each catalog entry.
-- Trainer ID → class mapping:
--   200002=Warrior 200004=Paladin 200006=Druid  200008=Mage    200010=Warlock
--   200012=Priest  200014=Hunter  200016=Rogue   200018=Shaman
UPDATE unbound_class_catalog uc
INNER JOIN npc_trainer nt
    ON nt.SpellID = uc.spell_id
    AND nt.ID IN (200002,200004,200006,200008,200010,200012,200014,200016,200018)
SET uc.prereq_spell = nt.ReqSpell
WHERE nt.ReqSpell > 0 AND uc.prereq_spell = 0;

-- ── 2. Shaman starter totems (missing from Playerbots trainer template) ───
-- Each element's basic rank-1 totem, gifted free at class unlock.
-- Already in playercreateinfo_spell_custom for classmask=64.
DELETE FROM playercreateinfo_spell_custom WHERE classmask = 64 AND racemask = 0;
INSERT INTO playercreateinfo_spell_custom (racemask, classmask, Spell, Note) VALUES
(0, 64, 403,  'Shaman - Lightning Bolt r1'),
(0, 64, 331,  'Shaman - Healing Wave r1'),
(0, 64, 8071, 'Shaman - Stoneskin Totem r1 (Earth)'),
(0, 64, 8042, 'Shaman - Searing Totem r1 (Fire)'),
(0, 64, 5394, 'Shaman - Healing Stream Totem r1 (Water)'),
(0, 64, 8512, 'Shaman - Windfury Totem r1 (Air)'),
(0, 64, 2484, 'Shaman - Earthbind Totem');
WU_PAYLOAD_EOF_8

    cat > "$MODULE_DIR/data/sql/db-world/06_universal_skill_access.sql" <<'WU_PAYLOAD_EOF_9'
-- Unbound Wrath Edition — universal skill access for Unbound characters
-- Applied to: acore_world.skillraceclassinfo_dbc
--
-- Problem: AzerothCore's _LoadSkills validates every skill against
-- GetSkillRaceClassInfo(skill, race, class). If no entry exists for that
-- skill+race+class combo, the skill is stripped from memory on every login.
-- This prevents Unbound characters from keeping cross-class skills (Staves
-- for a Paladin, Daggers for a Warrior, etc.).
--
-- Fix: insert rows with ClassMask=0, RaceMask=0 for every skill we need.
-- ClassMask=0 → all classes. RaceMask=0 → all races. This makes the check
-- always return a valid entry, allowing any character to keep any listed skill.
--
-- These rows are loaded at server start via storage.LoadFromDB("skillraceclassinfo_dbc")
-- called inside LoadDBC() in DBCStores.cpp.  Restart required after applying.
--
-- Safe to re-run: DELETE + re-INSERT on our ID range.

DELETE FROM skillraceclassinfo_dbc WHERE ID >= 10000;

-- Auto-generate one row per skill.
-- Fields: ID, SkillID, RaceMask, ClassMask, Flags, MinLevel, SkillTierID, SkillCostIndex
-- ClassMask=0 = all classes, RaceMask=0 = all races, SkillTierID=0 = level-scaled.

INSERT INTO skillraceclassinfo_dbc
  (ID, SkillID, RaceMask, ClassMask, Flags, MinLevel, SkillTierID, SkillCostIndex)
VALUES
-- ── Weapon skills ────────────────────────────────────────────────────────────
(10001,  43, 0, 0, 0, 0, 0, 0),   -- Swords
(10002,  44, 0, 0, 0, 0, 0, 0),   -- Axes
(10003,  45, 0, 0, 0, 0, 0, 0),   -- Bows
(10004,  46, 0, 0, 0, 0, 0, 0),   -- Guns
(10005,  54, 0, 0, 0, 0, 0, 0),   -- Maces
(10006,  55, 0, 0, 0, 0, 0, 0),   -- Two-Handed Swords
(10007, 118, 0, 0, 0, 0, 0, 0),   -- Dual Wield
(10008, 136, 0, 0, 0, 0, 0, 0),   -- Staves
(10009, 160, 0, 0, 0, 0, 0, 0),   -- Two-Handed Maces
(10010, 162, 0, 0, 0, 0, 0, 0),   -- Unarmed
(10011, 172, 0, 0, 0, 0, 0, 0),   -- Two-Handed Axes
(10012, 173, 0, 0, 0, 0, 0, 0),   -- Daggers
(10013, 176, 0, 0, 0, 0, 0, 0),   -- Thrown
(10014, 226, 0, 0, 0, 0, 0, 0),   -- Crossbows
(10015, 228, 0, 0, 0, 0, 0, 0),   -- Wands
(10016, 229, 0, 0, 0, 0, 0, 0),   -- Polearms
(10017, 433, 0, 0, 0, 0, 0, 0),   -- Shield
(10018, 473, 0, 0, 0, 0, 0, 0),   -- Fist Weapons
-- ── Armor skills ─────────────────────────────────────────────────────────────
(10019, 293, 0, 0, 0, 0, 0, 0),   -- Plate Mail
(10020, 413, 0, 0, 0, 0, 0, 0),   -- Mail
(10021, 414, 0, 0, 0, 0, 0, 0),   -- Leather
(10022, 415, 0, 0, 0, 0, 0, 0),   -- Cloth
-- ── Class spellbook tab skills (from playercreateinfo_skills classMask!=0) ───
-- These allow Unbound characters to keep spellbook tabs from unlocked classes.
(10030,   6, 0, 0, 0, 0, 0, 0),
(10031,   8, 0, 0, 0, 0, 0, 0),
(10032,  26, 0, 0, 0, 0, 0, 0),
(10033,  38, 0, 0, 0, 0, 0, 0),
(10034,  39, 0, 0, 0, 0, 0, 0),
(10035,  50, 0, 0, 0, 0, 0, 0),
(10036,  51, 0, 0, 0, 0, 0, 0),
(10037,  56, 0, 0, 0, 0, 0, 0),
(10038,  78, 0, 0, 0, 0, 0, 0),
(10039, 129, 0, 0, 0, 0, 0, 0),
(10040, 134, 0, 0, 0, 0, 0, 0),
(10041, 163, 0, 0, 0, 0, 0, 0),
(10042, 184, 0, 0, 0, 0, 0, 0),
(10043, 237, 0, 0, 0, 0, 0, 0),
(10044, 253, 0, 0, 0, 0, 0, 0),
(10045, 256, 0, 0, 0, 0, 0, 0),
(10046, 257, 0, 0, 0, 0, 0, 0),
(10047, 267, 0, 0, 0, 0, 0, 0),
(10048, 354, 0, 0, 0, 0, 0, 0),
(10049, 355, 0, 0, 0, 0, 0, 0),
(10050, 373, 0, 0, 0, 0, 0, 0),
(10051, 374, 0, 0, 0, 0, 0, 0),
(10052, 375, 0, 0, 0, 0, 0, 0),
(10053, 573, 0, 0, 0, 0, 0, 0),
(10054, 574, 0, 0, 0, 0, 0, 0),
(10055, 593, 0, 0, 0, 0, 0, 0),
(10056, 594, 0, 0, 0, 0, 0, 0),
(10057, 613, 0, 0, 0, 0, 0, 0),
(10058, 762, 0, 0, 0, 0, 0, 0),
(10059, 770, 0, 0, 0, 0, 0, 0),
(10060, 771, 0, 0, 0, 0, 0, 0),
(10061, 772, 0, 0, 0, 0, 0, 0);
WU_PAYLOAD_EOF_9

    cat > "$MODULE_DIR/data/sql/db-world/07_mentor_stone.sql" <<'WU_PAYLOAD_EOF_10'
-- Unbound Wrath Edition — Unbounding Mentor Stone
-- Applied to: acore_world
--
-- Creates a permanent use-item (entry 900100) given to every character at login.
-- Right-clicking summons the Mentor NPC (entry 900001) for 3 minutes.
--
-- Safe to re-run: uses INSERT IGNORE / ON DUPLICATE KEY UPDATE.
--
-- IMPORTANT — root cause writeup (see ~/wow-server-playerbots/CLAUDE.md "RESOLVED BUGS"):
-- The item's spellid_1 MUST point at a real, client-known spell ID (Blizzard IDs
-- top out around ~71000). A custom server-only ID like 900200 is invisible to the
-- client's binary Spell.dbc — the client silently refuses to recognize the item as
-- usable and never even sends CMSG_USE_ITEM. spellid_1 = 433 ("Food") was chosen as
-- a harmless defense-in-depth fallback: its only effect is a heal-over-time that
-- fizzles unless seated, so even if the Lua cancellation in unbound_mentor.lua were
-- ever bypassed, nothing disruptive happens (unlike the Hearthstone teleport that
-- was tried first during diagnosis). The Lua ITEM_EVENT_ON_USE handler unconditionally
-- returns true to cancel the real cast — the Lua-side STONE_LAST_USE 180s cooldown
-- fully replaces spellcooldown_1 as the gameplay cooldown.
--
-- displayid = 6418 (INV_Misc_Rune_01) — a Vanilla-era rune-stone icon guaranteed
-- present in any 3.3.5a client; newer WotLK icons (e.g. 58413) can render as "?"
-- on clients whose MPQ data is missing those textures.

-- ── Item 900100: Unbounding Mentor Stone ─────────────────────────────────────
-- class=15 (Miscellaneous), InventoryType=0 (non-equippable bag item).
-- maxcount=1 ensures only one copy can be held at a time.
-- spellid_1=433 (Food — real client-known spell, cancelled by Lua) +
-- spellcooldown_1=180000 ms (3 min, superseded by the Lua-side cooldown guard).
INSERT INTO item_template
    (entry, class, subclass, SoundOverrideSubclass, name,
     displayid, Quality, Flags, FlagsExtra,
     BuyCount, BuyPrice, SellPrice,
     InventoryType, AllowableClass, AllowableRace,
     ItemLevel, RequiredLevel,
     maxcount, stackable,
     spellid_1, spelltrigger_1, spellcharges_1, spellppmRate_1,
     spellcooldown_1, spellcategory_1, spellcategorycooldown_1,
     description, ScriptName)
VALUES
    (900100, 15, 0, -1, 'Unbounding Mentor Stone',
     6418, 3, 0, 0,
     1, 0, 0,
     0, -1, -1,
     1, 0,
     1, 1,
     433, 0, 0, 0,
     180000, 0, -1,
     'Summons your Unbounding Mentor for 3 minutes. (3 min cooldown)', '')
ON DUPLICATE KEY UPDATE
    name              = VALUES(name),
    displayid         = VALUES(displayid),
    Quality           = VALUES(Quality),
    spellid_1         = VALUES(spellid_1),
    spelltrigger_1    = VALUES(spelltrigger_1),
    spellcooldown_1   = VALUES(spellcooldown_1),
    description       = VALUES(description);

-- ── Give stone to all new characters at creation ─────────────────────────────
-- race=0 means any race; class entries cover all WotLK playable classes.
-- The Lua login hook in unbound_mentor.lua also gives it to existing characters.
INSERT IGNORE INTO playercreateinfo_item (race, class, itemid, amount) VALUES
(0,  1, 900100, 1),   -- Warrior
(0,  2, 900100, 1),   -- Paladin
(0,  3, 900100, 1),   -- Hunter
(0,  4, 900100, 1),   -- Rogue
(0,  5, 900100, 1),   -- Priest
(0,  6, 900100, 1),   -- Death Knight
(0,  7, 900100, 1),   -- Shaman
(0,  8, 900100, 1),   -- Mage
(0,  9, 900100, 1),   -- Warlock
(0, 11, 900100, 1);   -- Druid
WU_PAYLOAD_EOF_10

    cat > "$MODULE_DIR/data/sql/db-world/08_catalog_additions.sql" <<'WU_PAYLOAD_EOF_11'
-- Unbound Wrath Edition — catalog gap fill
-- Applied to: acore_world.unbound_class_catalog
--
-- These spells appear in WotLK class trainers (trainer_spell IDs 1-34) but were
-- absent from the catalog, which was originally populated from Playerbots
-- synthetic trainer data (npc_trainer IDs 200002–200018). The Playerbots
-- templates omit some low-level rank-1 spells, particularly those that native
-- characters receive at creation.
--
-- Spells already in creation gifts (playercreateinfo_spell_custom) are excluded
-- because Unbound players receive them for free at unlock.
-- Prices match WotLK trainer MoneyCost values.
--
-- Safe to re-run: uses INSERT IGNORE.
-- prereq_spell defaults to 0; PREREQ_MAP (built from catalog req_level order at
-- script load) will infer rank chains automatically.

-- prereq_spell is omitted; it defaults to 0 (added by 05_individual_purchase_prereqs.sql).
-- PREREQ_MAP in the Lua infers rank chains from req_level ordering at script load.
INSERT IGNORE INTO unbound_class_catalog (class_id, spell_id, gold_cost_copper, req_level) VALUES

-- ── Warrior (class_id=1) ──────────────────────────────────────────────────────
-- Rend r1 (772), Parry (3127), Thunder Clap r1 (6343), Victory Rush (34428)
(1,   772,   100, 4),
(1,  3127,   100, 6),
(1,  6343,   100, 6),
(1, 34428,   100, 6),

-- ── Paladin (class_id=2) ──────────────────────────────────────────────────────
-- Judgement (10321), Blessing of Might r1 (19740),
-- Divine Protection r1 (498), Holy Light r2 (639),
-- Seal of Vengeance (31801 — high-level Retribution seal)
(2, 10321,   100, 4),
(2, 19740,   100, 4),
(2,   498,   100, 6),
(2,   639,   100, 6),
(2, 31801, 67000,64),

-- ── Hunter (class_id=3) ───────────────────────────────────────────────────────
-- Track Beasts (1494), Serpent Sting r1 (1978),
-- Hunter's Mark r1 (1130), Arcane Shot r1 (3044)
(3, 1494,    10, 2),
(3, 1978,   100, 4),
(3, 1130,   100, 6),
(3, 3044,   100, 6),

-- ── Rogue (class_id=4) ────────────────────────────────────────────────────────
-- Backstab r1 (53), Pickpocket (921),
-- Gouge r1 (1757), Ambush r1 (1776)
(4,   53,   100, 4),
(4,  921,   100, 4),
(4, 1757,   100, 6),
(4, 1776,   100, 6),

-- ── Priest (class_id=5) ───────────────────────────────────────────────────────
-- Power Word: Fortitude r1 (1243), Shadow Word: Pain r1 (589),
-- Lesser Heal r2 (2052 — rank 2 of creation gift 2050), Power Word: Shield r1 (17),
-- Smite r2 (591 — rank 2 of creation gift 585)
(5, 1243,    10, 1),
(5,  589,   100, 4),
(5, 2052,   100, 4),
(5,   17,   100, 6),
(5,  591,   100, 6),

-- ── Shaman (class_id=7) ───────────────────────────────────────────────────────
-- Rockbiter Weapon r1 (8017), Earth Shock r1 (8042 in gifts — skip),
-- Healing Wave r2 (332 — rank 2 of creation gift 331), Earthbind Totem (2484 in gifts — skip)
(7, 8017,    10, 1),
(7,  332,   100, 6),

-- ── Mage (class_id=8) ─────────────────────────────────────────────────────────
-- Arcane Intellect r1 (1459 — also in creation gifts; added here so higher ranks'
-- prereq chain resolves correctly and re-purchase is possible if lost)
-- Frostbolt r1 (116), Conjure Food r1 (587→5504),
-- Conjure Water r1 (143), Conjure Food r1 (587),
-- Fire Blast r1 (2136), Detect Magic (2855)
(8, 1459,    10, 1),
(8,  116,   100, 4),
(8, 5504,   100, 4),
(8,  143,   100, 6),
(8,  587,   100, 6),
(8, 2136,   100, 6),
(8, 2855,  2000,16),

-- ── Warlock (class_id=9) ──────────────────────────────────────────────────────
-- Immolate r1 (348), Corruption r1 (172), Curse of Weakness r1 (702),
-- Shadow Bolt r2 (695 — rank 2 of creation gift 686), Life Tap r1 (1454)
(9,  348,    10, 3),
(9,  172,   100, 4),
(9,  702,   100, 4),
(9,  695,   100, 6),
(9, 1454,   100, 6),

-- ── Druid (class_id=11) ───────────────────────────────────────────────────────
-- Mark of the Wild r1 (1126), Rejuvenation r1 (774), Moonfire r1 (8921),
-- Thorns r1 (467), Wrath r2 (5177 — rank 2 of creation gift 5176)
(11, 1126,    10, 1),
(11,  774,   100, 4),
(11, 8921,   100, 4),
(11,  467,   100, 6),
(11, 5177,   100,  6);
WU_PAYLOAD_EOF_11


    cat > "$MODULE_DIR/data/sql/db-world/10_catalog_audit_fixes.sql" <<'WU_PAYLOAD_EOF_14'
-- Unbound Wrath Edition — catalog req_level self-heal vs real WotLK trainers
-- Applied to: acore_world.unbound_class_catalog
--
-- 02_fix_catalog_req_level.sql blanket-lowered every entry with req_level <= 8
-- to req_level = 1 so a level-5 class unlock always had something buyable.
-- That also dragged down ~30 legitimate rank-2/utility spells (Heroic Strike
-- r2, Hammer of Justice, Aspect of the Hawk r2, etc.) that real trainers gate
-- at level 8, plus a separate batch of level 60/70 spells that were a tier
-- below their real 61/71 requirement.
--
-- Fix: pull req_level straight from the real class trainers (trainer +
-- trainer_spell, Type=0, Requirement=class_id) and apply it wherever the
-- catalog disagrees. Verified live (2026-06-13): this only ever RAISES
-- req_level — level-5 unlocks stay buyable because 08_catalog_additions.sql
-- already seeds req_level 1/2/4/6 entries per class.
--
-- Requires a worldserver restart afterward so PREREQ_MAP (built once at Eluna
-- load from catalog req_level order) re-sorts rank chains with the corrected
-- levels.
--
-- Safe to re-run: WHERE clause only touches rows that still disagree.

UPDATE unbound_class_catalog c
JOIN (
    SELECT t.Requirement AS class_id, ts.SpellID AS spell_id, MIN(ts.ReqLevel) AS req_level
    FROM trainer t
    JOIN trainer_spell ts ON ts.TrainerId = t.Id
    WHERE t.Type = 0 AND t.Requirement IN (1,2,3,4,5,7,8,9,11)
    GROUP BY t.Requirement, ts.SpellID
) rts ON rts.class_id = c.class_id AND rts.spell_id = c.spell_id
SET c.req_level = rts.req_level
WHERE c.req_level <> rts.req_level;

-- ============================================================
-- Hunter (class_id=3) gap fill: Aspect of the Monkey
-- ============================================================
-- 13163 = Aspect of the Monkey, a real Hunter trainer spell (req_level 4,
-- 100c) missing from the catalog. It was stuck in limbo because
-- 03_creation_gift_spells.sql's Hunter gift used to point at 13163 by mistake
-- (intending Aspect of the Hawk r1 = 13165, now corrected) — so 13163 was
-- neither gifted nor purchasable. prereq_spell defaults to 0: Aspect of the
-- Monkey has no rank chain.
INSERT IGNORE INTO unbound_class_catalog (class_id, spell_id, gold_cost_copper, req_level) VALUES
(3, 13163, 100, 4);
WU_PAYLOAD_EOF_14

    cat > "$MODULE_DIR/data/sql/db-world/11_catalog_gap_additions.sql" <<'WU_PAYLOAD_EOF_15'
-- Unbound Wrath Edition — catalog gap fill: Mage teleports/portals + Paladin mount
-- Applied to: acore_world.unbound_class_catalog
--
-- Remaining real-trainer spells identified by the level 1-80 catalog audit
-- (2026-06-13) that were missing entirely. req_level/cost taken directly from
-- trainer_spell (Type=0, Requirement=class_id).
--
-- Deliberately NOT added (see audit notes):
--   - Paladin Summon Charger (34767, req40/3500c): trainer_spell gates it on
--     ReqAbility1=33391 ("Journeyman Riding", itself a 1000g Riding-trainer
--     spell at req60) and ReqAbility2=34769 (a second, untaught "Summon
--     Warhorse" companion spell). That prereq chain reaches into the Riding
--     skill system, which Unbound doesn't model — locked until a proper
--     prereq/talent system exists, per Joshua's call on Seal of Corruption.
--   - Paladin Seal of Corruption (53736): per Wowhead, this is the
--     Horde-faction name for the same "Holy Vengeance" seal as Seal of
--     Vengeance (31801, already in the catalog via 08_catalog_additions.sql)
--     — Alliance/Horde naming variants of one ability, not a talent rank or
--     an upgrade. Adding it would just duplicate 31801 under another name.
--
-- prereq_spell defaults to 0; PREREQ_MAP (built from catalog req_level order
-- at script load) infers same-named rank chains automatically.
-- Safe to re-run: uses INSERT IGNORE.

INSERT IGNORE INTO unbound_class_catalog (class_id, spell_id, gold_cost_copper, req_level) VALUES

-- ── Paladin (class_id=2) ──────────────────────────────────────────────────────
-- Summon Warhorse (34768) — basic Paladin mount, no prereqs.
(2, 34768, 3500, 20),

-- ── Mage (class_id=8) — Teleport/Portal lines ─────────────────────────────────
-- req 20, 2000c: Teleport: Stormwind/Ironforge/Undercity/Orgrimmar/Exodar/Silvermoon/Stonard/Theramore
(8,  3561, 2000, 20),
(8,  3562, 2000, 20),
(8,  3563, 2000, 20),
(8,  3567, 2000, 20),
(8, 32271, 2000, 20),
(8, 32272, 2000, 20),
(8, 49358, 2000, 20),
(8, 49359, 2000, 20),

-- req 30, 8000c: Teleport: Darnassus/Thunder Bluff
(8,  3565, 8000, 30),
(8,  3566, 8000, 30),

-- req 35, 15000c: Portal: Theramore/Stonard
(8, 49360, 15000, 35),
(8, 49361, 15000, 35),

-- req 40, 15000c: Portal: Stormwind/Ironforge/Orgrimmar/Undercity/Exodar/Silvermoon
(8, 10059, 15000, 40),
(8, 11416, 15000, 40),
(8, 11417, 15000, 40),
(8, 11418, 15000, 40),
(8, 32266, 15000, 40),
(8, 32267, 15000, 40),

-- req 50, 32000c: Portal: Darnassus/Thunder Bluff
(8, 11419, 32000, 50),
(8, 11420, 32000, 50),

-- req 60, 20000c: Teleport: Shattrath (Aldor/Scryer faction-name variants)
(8, 33690, 20000, 60),
(8, 35715, 20000, 60),

-- req 65, 150000c: Portal: Shattrath (Aldor/Scryer faction-name variants)
(8, 33691, 150000, 65),
(8, 35717, 150000, 65),

-- req 71/74: Teleport/Portal: Dalaran
(8, 53140, 100000, 71),
(8, 53142, 100000, 74);
WU_PAYLOAD_EOF_15

    cat > "$MODULE_DIR/data/sql/db-world/12_mount_spell_fix.sql" <<'WU_PAYLOAD_EOF_16'
-- Unbound Wrath Edition — Paladin/Warlock mount purchase fix
-- Applied to: acore_world.unbound_class_catalog
--
-- Reported by Joshua: Summon Warhorse (Paladin) and Summon Felsteed (Warlock)
-- can be "bought" from the Mentor — gold is deducted and a success message is
-- shown — but the spell never appears in the spellbook, isn't selectable as a
-- mount, and the entry reappears in Browse as if never purchased.
--
-- Root cause (confirmed against AzerothCore source + Spell.dbc, 2026-06-13):
-- 34768 ("Summon Warhorse") and 1710 ("Summon Felsteed") are trainer TEACH
-- spells — their Effect array contains SPELL_EFFECT_LEARN_SPELL (36) twice,
-- meant to recursively grant the real mount spell + Apprentice Riding via the
-- temporary-learn trainer path. Player::_addSpell() (Player.cpp ~3192)
-- explicitly refuses any spell with SPELL_EFFECT_LEARN_SPELL when called via
-- the non-temporary player:LearnSpell() the Mentor uses — it adds the spell to
-- m_spells, immediately erases it, and returns false. The Lua never checks
-- that return value, so gold is taken and "Learned!" fires for a purchase that
-- silently did nothing.
--
-- Fix: point the catalog at the REAL castable mount spell each teach-spell was
-- meant to grant (same display name, same cost/req_level). Neither real mount
-- has a LEARN_SPELL effect, so player:LearnSpell() works normally — same code
-- path as Dreadsteed (23161), which already works correctly:
--   34768 "Summon Warhorse" (teach) -> 34769 "Summon Warhorse" (real mount)
--   1710  "Summon Felsteed" (teach) -> 5784  "Felsteed"        (real mount)
--
-- Note: both real mounts also require Apprentice Riding (skill 762 >= 75) to
-- be summonable once learned. Not modeled by the catalog, but
-- 06_universal_skill_access.sql already makes Riding (762) valid for every
-- class/race, and any character who trained a faction mount in the normal
-- 20-40 leveling range will already have it (confirmed live: Testmage has
-- Riding 150/150). Left out here to avoid scope creep into a riding-skill
-- purchase system — flag to Joshua if a player reports the mount is in their
-- spellbook but won't summon.
--
-- No worldserver restart required: the catalog is read live on every
-- Browse/Buy, and PREREQ_MAP doesn't reference these IDs (mounts have no rank
-- chain). Safe to re-run: each pair is a DELETE of the old spell_id followed
-- by INSERT IGNORE of the new one, so re-running never collides on the
-- (class_id, spell_id) primary key — even if an earlier INSERT IGNORE
-- migration re-creates the old row after this fix already ran once (e.g.
-- after an uninstall/reinstall where AzerothCore's update-tracking and the
-- catalog data fall out of sync). A plain UPDATE...SET spell_id=<new> would
-- collide with the primary key in that case since <new> already exists.

DELETE FROM unbound_class_catalog WHERE class_id = 2 AND spell_id = 34768;
INSERT IGNORE INTO unbound_class_catalog (class_id, spell_id, gold_cost_copper, req_level) VALUES (2, 34769, 3500, 20);

DELETE FROM unbound_class_catalog WHERE class_id = 9 AND spell_id = 1710;
INSERT IGNORE INTO unbound_class_catalog (class_id, spell_id, gold_cost_copper, req_level) VALUES (9, 5784, 10000, 20);
WU_PAYLOAD_EOF_16

    cat > "$MODULE_DIR/data/sql/db-world/13_flight_form_fix.sql" <<'WU_PAYLOAD_EOF_17'
-- Unbound Wrath Edition — Druid Flight Form purchase fix
-- Applied to: acore_world.unbound_class_catalog
--
-- Same bug class as 12_mount_spell_fix.sql, found by auditing every catalog
-- spell_id against Spell.dbc for SPELL_EFFECT_LEARN_SPELL (36).
--
-- 33950 "Flight Form" (Druid, req68/34000c) is a trainer TEACH spell —
-- Effects=[36,36,44], TriggerSpells=[33943 "Flight Form", 34090 "Expert
-- Riding"]. Player::_addSpell() erases any spell with SPELL_EFFECT_LEARN_SPELL
-- when learned via the non-temporary player:LearnSpell() the Mentor uses, so
-- buying 33950 took gold and granted nothing — identical symptom to the
-- mount bug (reappears in Browse, not in spellbook, not usable).
--
-- Fix: point the catalog at 33943, the real castable "Flight Form" shapeshift
-- spell (same name, same cost/req_level, Effects=[6,6,6] — no LEARN_SPELL,
-- learns normally).
--
-- Note: 34090 "Expert Riding" (skill 762 -> 225, needed to actually fly) is
-- not granted by this fix, same rationale as 12_mount_spell_fix.sql — Riding
-- skill is already universally accessible (06_universal_skill_access.sql) and
-- most level-68+ characters will already have at least Artisan Riding (300)
-- from normal flying-mount training, which exceeds the 225 Expert requirement.
--
-- No worldserver restart required. Safe to re-run: DELETE the old spell_id
-- then INSERT IGNORE the new one, so re-running never collides on the
-- (class_id, spell_id) primary key (see 12_mount_spell_fix.sql for why a
-- plain UPDATE isn't safe here).

DELETE FROM unbound_class_catalog WHERE class_id = 11 AND spell_id = 33950;
INSERT IGNORE INTO unbound_class_catalog (class_id, spell_id, gold_cost_copper, req_level) VALUES (11, 33943, 34000, 68);
WU_PAYLOAD_EOF_17

    cat > "$MODULE_DIR/data/sql/db-world/14_judgement_fix.sql" <<'WU_PAYLOAD_EOF_18'
-- Unbound Wrath Edition — Paladin Judgement purchase/gift fix
-- Applied to: acore_world.unbound_class_catalog, acore_world.playercreateinfo_spell_custom
--
-- Same bug class as 12_mount_spell_fix.sql / 13_flight_form_fix.sql.
--
-- Confirmed live (2026-06-13) with Testpal (Rogue, second class Paladin via
-- Mentor at level 5): the Paladin-unlock creation gifts 635 (Holy Light r1),
-- 20154 (Seal of Righteousness r1) and 465 (Devotion Aura r1) were granted
-- correctly, but 10321 ("Judgement") was not — and buying "Judgement" from
-- the Mentor (req4/100c) takes gold, grants nothing, and the entry never
-- disappears from Browse ("keeps buying over and over").
--
-- 10321 "Judgement" is a trainer TEACH spell: Effects=[36,36,0],
-- TriggerSpells=[20271 "Judgement of Light", 21084 "Seal of Righteousness"].
-- Both player:LearnSpell() (Mentor purchase) and the Mentor's class-unlock
-- gift-granting code call learnSpell() non-temporary, which Player::_addSpell
-- erases-and-rejects for any SPELL_EFFECT_LEARN_SPELL spell. A *native*
-- character creation grants 10321 via AzerothCore's temporary=true path
-- (which DOES honor LEARN_SPELL), so freshly-rolled Paladins are unaffected —
-- only Mentor-driven unlocks and Mentor purchases hit the broken path.
--
-- Fix part 1 (catalog, live immediately, no restart): point the catalog entry
-- at 20271 "Judgement of Light" — the actual SCRIPT_EFFECT spell WotLK
-- Paladins use as their "Judgement" button (it judges using whichever Seal is
-- currently active, regardless of the "of Light" name). Same cost/req_level.
-- This is also the remediation path for Testpal and anyone else already
-- missing Judgement from a Mentor unlock.
--
-- Fix part 2 (creation-gift table, requires worldserver restart):
-- playercreateinfo_spell_custom is loaded into PlayerInfo at startup, so this
-- only affects FUTURE Mentor class-unlocks until restarted.
--
-- Not touched: 21084 "Seal of Righteousness" (10321's other trigger). Testpal
-- already has 20154 "Seal of Righteousness r1" as a creation gift and both
-- DBC entries share the same name with no rank text to distinguish them —
-- granting 21084 too risks an unverified duplicate/rank conflict. Flag for a
-- follow-up if Seal of Righteousness turns out not to rank up correctly.
--
-- Safe to re-run: DELETE the old spell_id/Spell row then INSERT IGNORE the
-- new one in each table, so re-running never collides on the primary key
-- (see 12_mount_spell_fix.sql for why a plain UPDATE isn't safe here).

DELETE FROM unbound_class_catalog WHERE class_id = 2 AND spell_id = 10321;
INSERT IGNORE INTO unbound_class_catalog (class_id, spell_id, gold_cost_copper, req_level) VALUES (2, 20271, 100, 4);

DELETE FROM playercreateinfo_spell_custom WHERE racemask = 0 AND classmask = 2 AND Spell = 10321;
INSERT IGNORE INTO playercreateinfo_spell_custom (racemask, classmask, Spell, Note) VALUES (0, 2, 20271, 'Paladin - Judgement of Light');
WU_PAYLOAD_EOF_18
    cat > "$MODULE_DIR/data/sql/db-characters/01_unbound_characters.sql" <<'WU_PAYLOAD_EOF_12'
-- Unbound Wrath Edition — characters DB schema
-- Run against: acore_characters
-- Safe to re-run: CREATE TABLE IF NOT EXISTS.

-- ============================================================
-- Per-character class unlock records.
-- One row per (player, class) pair. Never deleted — additive only.
-- ============================================================
CREATE TABLE IF NOT EXISTS `unbound_character_unlocks` (
    `char_guid`        INT UNSIGNED     NOT NULL,
    `class_id`         TINYINT UNSIGNED NOT NULL,
    `unlocked_at_level` TINYINT UNSIGNED NOT NULL,
    `unlocked_ts`      TIMESTAMP        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`char_guid`, `class_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
WU_PAYLOAD_EOF_12

    cat > "$SERVER_DIR/env/dist/etc/modules/lua_scripts/unbound_mentor.lua" <<'WU_PAYLOAD_EOF_13'
--[[ ============================================================
  unbound_mentor.lua — Unbound Wrath Edition
  --------------------------------------------------------------
  Handles:
    • Mentor NPC gossip (class unlocks + individual spell purchase)
    • Multi-resource pool initialization on unlock and login
    • Weapon/armor proficiency (all skills) for every Unbound character
    • Persistent unlock state in unbound_character_unlocks (acore_characters)

  C++ side (mod-unbound/UnboundSystem.cpp) hooks
  Player::HasActivePowerType so rage/energy generation fires
  for any power type whose GetMaxPower() > 0.

  NPC entry 900001 = The Mentor (model = Ethereal Thief, displayid 19097).
  Spawn in-game via: .npc add 900001
============================================================ --]]

local MENTOR_ENTRY      = 900001
local MENTOR_STONE_ENTRY = 900100
local PAGE_SIZE          = 10   -- spells shown per gossip page

-- Per-player timestamp (os.time seconds) of last Mentor Stone use.
-- Used to enforce the 3-minute cooldown on the Lua side as a guard.
local STONE_LAST_USE = {}
local STONE_COOLDOWN_SEC = 180

-- AzerothCore power type constants
local POWER_MANA   = 0
local POWER_RAGE   = 1
local POWER_ENERGY = 3

local RAGE_MAX    = 1000
local ENERGY_MAX  = 100

local RAGE_NATIVE   = { [1]=true, [11]=true }
local ENERGY_NATIVE = { [4]=true, [11]=true }
local MANA_NATIVE   = { [2]=true, [3]=true, [5]=true, [7]=true, [8]=true, [9]=true, [11]=true }

local CLASS_NAMES = {
    [1]="Warrior", [2]="Paladin", [3]="Hunter",  [4]="Rogue",
    [5]="Priest",  [7]="Shaman",  [8]="Mage",    [9]="Warlock", [11]="Druid"
}

-- ── Weapon and armor skill IDs (from SharedDefines.h) ─────────────────────
-- These skills govern item equip eligibility (CanEquipItem checks GetSkillValue).
-- Granting all of them makes every Unbound character weapon/armor-agnostic.
local WEAPON_SKILLS = {
    43,   -- Swords
    44,   -- Axes
    45,   -- Bows
    46,   -- Guns
    54,   -- Maces
    55,   -- Two-Handed Swords
    118,  -- Dual Wield
    136,  -- Staves
    160,  -- Two-Handed Maces
    162,  -- Unarmed
    172,  -- Two-Handed Axes
    173,  -- Daggers
    176,  -- Thrown
    226,  -- Crossbows
    228,  -- Wands
    229,  -- Polearms
    433,  -- Shield
    473,  -- Fist Weapons
}
local ARMOR_SKILLS = {
    293,  -- Plate Mail
    413,  -- Mail
    414,  -- Leather
    415,  -- Cloth
}

-- ── Rank prerequisite map ─────────────────────────────────────────────────
-- PREREQ_MAP[classId][spellId] = prereqSpellId (0 if none)
-- Built at script-load from GetSpellInfo data; avoids any DB dependency.
local PREREQ_MAP = {}

-- GetSpellInfo returns a SpellInfo object.  Its name method is :GetName(locale)
-- which returns a plain string.  Rank info isn't on SpellInfo, so we order
-- same-named spells by catalog req_level to determine the rank chain.
local function BuildPrereqMap()
    for classId = 1, 11 do
        if CLASS_NAMES[classId] then
            PREREQ_MAP[classId] = {}
            -- Include req_level so we can sort chains without rank text
            local Q = WorldDBQuery(string.format(
                "SELECT spell_id, req_level FROM unbound_class_catalog " ..
                "WHERE class_id = %d ORDER BY req_level, spell_id", classId))
            if Q then
                local byName = {}  -- spellName → [{reqLevel, id}]
                repeat
                    local spellId   = Q:GetUInt32(0)
                    local reqLevel  = Q:GetUInt32(1)
                    local info      = GetSpellInfo(spellId)
                    if info then
                        local ok, name = pcall(function() return info:GetName(0) end)
                        if ok and name and name ~= "" then
                            if not byName[name] then byName[name] = {} end
                            table.insert(byName[name], { lv=reqLevel, id=spellId })
                        end
                    end
                until not Q:NextRow()
                for _, group in pairs(byName) do
                    if #group > 1 then
                        table.sort(group, function(a, b)
                            return a.lv < b.lv or (a.lv == b.lv and a.id < b.id)
                        end)
                        for i = 2, #group do
                            PREREQ_MAP[classId][group[i].id] = group[i-1].id
                        end
                    end
                end
            end
        end
    end
    print("[UNBOUND] Prereq map built.")
end

pcall(BuildPrereqMap)

-- ============================================================
-- Helpers
-- ============================================================

local function FormatCopper(copper)
    if copper == 0 then return "Free" end
    local g = math.floor(copper / 10000)
    local s = math.floor((copper % 10000) / 100)
    local c = copper % 100
    if g > 0 then
        return s > 0 and string.format("%dg %ds", g, s) or string.format("%dg", g)
    end
    if s > 0 then return string.format("%ds", s) end
    return string.format("%dc", c)
end

local function GetSpellDisplayName(spellId)
    local info = GetSpellInfo(spellId)
    if not info then return "Spell #" .. spellId end
    local ok, name = pcall(function() return info:GetName(0) end)
    if ok and name and name ~= "" then return name end
    return "Spell #" .. spellId
end

-- A handful of spell IDs (e.g. Parry=3127, Plate Mail=750, Mail=8737) appear
-- in unbound_class_catalog under MULTIPLE class_ids. Gossip intid only carries
-- one number, so the browse/buy path encodes both class and spell into it —
-- otherwise a bare "WHERE spell_id = ... LIMIT 1" recovery would resolve to
-- whichever class_id happens to sort first, not the class the player actually
-- browsed from, and reject the purchase with "You have not unlocked that class."
-- Max spell_id in the catalog is well under 1,000,000 and class_id <= 11, so
-- this never collides and stays inside Lua's 32-bit intid range.
local SPELL_ID_MULT = 1000000
local function EncodeClassSpell(classId, spellId)
    return classId * SPELL_ID_MULT + spellId
end
local function DecodeClassSpell(intid)
    return math.floor(intid / SPELL_ID_MULT), intid % SPELL_ID_MULT
end

local function GetUnlockedClasses(player)
    local unlocked = {}
    local Q = CharDBQuery(string.format(
        "SELECT class_id FROM unbound_character_unlocks WHERE char_guid = %d",
        player:GetGUIDLow()))
    if Q then
        repeat unlocked[Q:GetUInt32(0)] = true until not Q:NextRow()
    end
    return unlocked
end

local function GetUnlockedCount(player)
    local Q = CharDBQuery(string.format(
        "SELECT COUNT(*) FROM unbound_character_unlocks WHERE char_guid = %d",
        player:GetGUIDLow()))
    return Q and Q:GetUInt32(0) or 0
end

local function GetMilestone(index)
    local capped = math.min(5, index)
    local Q = WorldDBQuery(string.format(
        "SELECT required_level, unlock_cost_copper FROM unbound_milestones WHERE milestone_index = %d",
        capped))
    if not Q then return nil end
    return { level = Q:GetUInt32(0), cost = Q:GetUInt32(1) }
end

-- ============================================================
-- Power pools
-- ============================================================

local function ApplyUnboundPools(player)
    local native  = player:GetClass()
    local level   = player:GetLevel()
    local unlocked = GetUnlockedClasses(player)

    local needRage, needEnergy, needMana = false, false, false
    for classId in pairs(unlocked) do
        if classId == 1  then needRage   = true end
        if classId == 4  then needEnergy = true end
        if classId ~= 1 and classId ~= 4 then needMana = true end
    end
    if RAGE_NATIVE[native]   then needRage   = false end
    if ENERGY_NATIVE[native] then needEnergy = false end
    if MANA_NATIVE[native]   then needMana   = false end

    if needRage and player:GetMaxPower(POWER_RAGE) == 0 then
        player:SetMaxPower(POWER_RAGE, RAGE_MAX)
    end
    if needEnergy and player:GetMaxPower(POWER_ENERGY) == 0 then
        player:SetMaxPower(POWER_ENERGY, ENERGY_MAX)
        player:SetPower(ENERGY_MAX, POWER_ENERGY)
    end
    if needMana then
        -- 80% of mage base mana at the player's level.
        -- Warriors (and other non-mana classes) have basemana=0 in player_class_stats,
        -- so we look up the mage value explicitly rather than trusting GetMaxPower.
        -- The == 0 guard is intentionally removed: server-side UpdateMaxPower for
        -- non-mana classes always recalculates to 0, which would silently undo the set.
        local mQ = WorldDBQuery(string.format(
            "SELECT basemana FROM player_class_stats WHERE class = 8 AND level = %d", level))
        local pool = mQ and math.floor(mQ:GetUInt32(0) * 0.8) or (level * 30)
        if pool < 100 then pool = 100 end
        player:SetMaxPower(POWER_MANA, pool)
        player:SetPower(pool, POWER_MANA)

        -- AzerothCore computes percentage-based spell costs (ManaCostPercentage —
        -- e.g. Arcane Intellect, Frost Armor) as a % of GetCreateMana(), which reads
        -- UNIT_FIELD_BASE_MANA — the player's NATIVE class's base mana (0 for
        -- Rogue/Warrior/Hunter/etc). That makes those spells cost 0 mana for
        -- cross-class casters, so mana never visibly depletes. SetCreateMana() isn't
        -- Lua-exposed, so set the raw field directly: UNIT_FIELD_BASE_MANA =
        -- OBJECT_END(6) + 0x72 = 120. SetCreateMana() is only called from the
        -- level-change paths GiveLevel/InitStatsForLevel, both of which complete
        -- before ApplyUnboundPools runs (1s after login, 200ms after level-up), so
        -- this sticks until the next level change re-applies it.
        player:SetUInt32Value(120, pool)
    end
end

-- ============================================================
-- Skill grants
-- ============================================================

-- Class-specific skill tabs (Arms/Fury/Protection for warrior, etc.)
-- so the WotLK spellbook renders the correct ability tabs.
local function ApplyUnboundSkills(player, classId)
    local mask = math.floor(2 ^ (classId - 1))
    local Q = WorldDBQuery(string.format(
        "SELECT skill FROM playercreateinfo_skills WHERE classMask = %d", mask))
    if not Q then return end
    local maxSkill = math.max(1, player:GetLevel() * 5)
    repeat
        local skillId = Q:GetUInt32(0)
        if player:GetSkillValue(skillId) == 0 then
            player:SetSkill(skillId, 1, 1, maxSkill)
        end
    until not Q:NextRow()
end

local function ApplyAllUnboundSkills(player)
    for classId in pairs(GetUnlockedClasses(player)) do
        ApplyUnboundSkills(player, classId)
    end
end

-- Universal weapon + armor proficiency for all Unbound characters.
-- SetSkill grants the skill entry so the server-side equip check passes
-- (CanEquipItem checks GetSkillValue(itemSkill) > 0). The client-side
-- "weapon shows as equippable" half is handled separately by the C++
-- OnPlayerLogin hook in UnboundSystem.cpp (AddWeaponProficiency + SendProficiency),
-- which bypasses learnSkillRewardedSpells()'s ClassMask filter directly —
-- no custom proficiency spells needed.
--
-- Pure SetSkill approach — no CharDBExecute.
-- With the skillraceclassinfo_dbc fix (06_universal_skill_access.sql), _LoadSkills
-- no longer strips cross-class skills (ClassMask=0 entries pass validation).
-- SetSkill marks skill SKILL_NEW → _SaveSkills INSERTs on logout → persists.
-- Using CharDBExecute alongside SetSkill caused duplicate-key aborts that
-- reverted the entire character save transaction.
local function ApplyUnboundWeaponArmorSkills(player)
    local maxSkill = math.max(1, player:GetLevel() * 5)
    for _, skillId in ipairs(WEAPON_SKILLS) do
        if player:GetSkillValue(skillId) == 0 then
            player:SetSkill(skillId, 1, 1, maxSkill)
        end
    end
    for _, skillId in ipairs(ARMOR_SKILLS) do
        if player:GetSkillValue(skillId) == 0 then
            player:SetSkill(skillId, 1, 1, maxSkill)
        end
    end
    -- Dual Wield off-hand spell
    if not player:HasSpell(674) then
        player:LearnSpell(674)
    end
end

-- ============================================================
-- Creation gift spells
-- ============================================================
-- Sourced from playercreateinfo_spell_custom (same table AzerothCore
-- reads for LearnCustomSpells at character creation).

local function GrantClassGiftSpells(player, classId)
    local mask = math.floor(2 ^ (classId - 1))
    local Q = WorldDBQuery(string.format(
        "SELECT Spell FROM playercreateinfo_spell_custom WHERE classmask = %d AND racemask = 0",
        mask))
    if not Q then return end
    repeat
        local spellId = Q:GetUInt32(0)
        if not player:HasSpell(spellId) then player:LearnSpell(spellId) end
    until not Q:NextRow()
end

local function GrantAllClassGiftSpells(player)
    for classId in pairs(GetUnlockedClasses(player)) do
        GrantClassGiftSpells(player, classId)
    end
end

-- ============================================================
-- Creation gift items
-- ============================================================
-- Physical reagent items required to cast certain abilities.
-- Shaman needs the four totem items in inventory to cast totem spells.
-- item 5175=Earth Totem  5176=Fire Totem  5177=Water Totem  5178=Air Totem
local CLASS_GIFT_ITEMS = {
    [7] = { 5175, 5176, 5177, 5178 },
}

local function GrantClassGiftItems(player, classId)
    local items = CLASS_GIFT_ITEMS[classId]
    if not items then return end
    for _, itemId in ipairs(items) do
        if player:GetItemCount(itemId) == 0 then
            player:AddItem(itemId, 1)
        end
    end
end

local function GrantAllClassGiftItems(player)
    for classId in pairs(GetUnlockedClasses(player)) do
        GrantClassGiftItems(player, classId)
    end
end

-- ============================================================
-- Individual spell purchase helpers
-- ============================================================

-- Returns an ordered list of {id, cost} for spells the player can buy
-- right now: level met, prereq met (or none), not already known.
local function GetBuyableSpells(player, classId)
    local level = player:GetLevel()
    local Q = WorldDBQuery(string.format(
        "SELECT spell_id, gold_cost_copper FROM unbound_class_catalog " ..
        "WHERE class_id = %d AND req_level <= %d ORDER BY req_level, spell_id",
        classId, level))
    if not Q then return {} end

    local classPrereqs = PREREQ_MAP[classId] or {}
    local list = {}
    repeat
        local spellId = Q:GetUInt32(0)
        local cost    = Q:GetUInt32(1)
        if not player:HasSpell(spellId) then
            local prereq = classPrereqs[spellId] or 0
            if prereq == 0 or player:HasSpell(prereq) then
                table.insert(list, { id=spellId, cost=cost })
            end
        end
    until not Q:NextRow()
    return list
end

-- Build (or rebuild) the browse gossip page for a class.
local function ShowBrowsePage(player, creature, classId, page)
    player:GossipClearMenu()

    local list = GetBuyableSpells(player, classId)
    local total = #list
    local startIdx = page * PAGE_SIZE + 1
    local endIdx   = math.min(startIdx + PAGE_SIZE - 1, total)

    if total == 0 then
        local nextQ = WorldDBQuery(string.format(
            "SELECT MIN(req_level) FROM unbound_class_catalog " ..
            "WHERE class_id = %d AND req_level > %d", classId, player:GetLevel()))
        local nextLvl = nextQ and nextQ:GetUInt32(0) or 0
        if nextLvl > 0 then
            player:GossipMenuAddItem(0, string.format(
                "No %s abilities available yet — come back at level %d.",
                CLASS_NAMES[classId], nextLvl), 0, 99, false)
        else
            player:GossipMenuAddItem(0,
                "You already know all available " .. CLASS_NAMES[classId] .. " abilities.",
                0, 99, false)
        end
        player:GossipMenuAddItem(0, "← Back", 99, 0, false)
        player:GossipSendMenu(100, creature)
        return
    end

    -- Page header (non-clickable)
    player:GossipMenuAddItem(0, string.format(
        "── %s abilities (page %d/%d) ──",
        CLASS_NAMES[classId], page+1, math.ceil(total/PAGE_SIZE)),
        0, 99, false)

    -- Buy-all shortcut: sender=27, intid=classId
    if page == 0 then
        local totalCost = 0
        for _, sp in ipairs(list) do totalCost = totalCost + sp.cost end
        player:GossipMenuAddItem(0, string.format(
            "|cffffd700* Buy ALL available abilities (%s) *|r", FormatCopper(totalCost)),
            27, classId, false)
    end

    -- Individual spell items: sender=25, intid=EncodeClassSpell(classId, spellId)
    for i = startIdx, endIdx do
        local sp = list[i]
        local label = string.format("%s  [%s]",
            GetSpellDisplayName(sp.id), FormatCopper(sp.cost))
        player:GossipMenuAddItem(0, label, 25, EncodeClassSpell(classId, sp.id), false)
    end

    -- Pagination: sender=24, intid encodes (classId*1000 + page)
    if page > 0 then
        player:GossipMenuAddItem(0, "← Prev page", 24, classId*1000+(page-1), false)
    end
    if endIdx < total then
        player:GossipMenuAddItem(0, "Next page →", 24, classId*1000+(page+1), false)
    end

    player:GossipMenuAddItem(0, "← Back to menu", 99, 0, false)
    player:GossipSendMenu(100, creature)
end

-- ============================================================
-- Gossip: Hello
-- ============================================================

local function OnGossipHello(event, player, creature)
    player:GossipClearMenu()

    local unlockedCnt = GetUnlockedCount(player)
    local unlocked    = GetUnlockedClasses(player)
    local native      = player:GetClass()

    if unlockedCnt == 0 then
        local ms = GetMilestone(1)
        if ms and player:GetLevel() >= ms.level then
            player:GossipMenuAddItem(0, "I wish to walk the Unbound path.", 1, 0, false)
        else
            local lvl = ms and ms.level or 5
            player:GossipMenuAddItem(0, string.format(
                "(Reach level %d to begin the Unbound path.)", lvl), 0, 99, false)
        end
        player:GossipMenuAddItem(0, "Farewell.", 99, 0, false)
        player:GossipSendMenu(100, creature)
        return
    end

    local nextMs = GetMilestone(unlockedCnt + 1)
    if nextMs then
        if player:GetLevel() >= nextMs.level then
            player:GossipMenuAddItem(0, string.format(
                "Unlock another class  [%s | Requires level %d]",
                FormatCopper(nextMs.cost), nextMs.level), 1, 0, false)
        else
            player:GossipMenuAddItem(0, string.format(
                "(Next unlock: level %d, %s)", nextMs.level, FormatCopper(nextMs.cost)),
                0, 99, false)
        end
    end

    for classId in pairs(unlocked) do
        player:GossipMenuAddItem(0,
            "Browse " .. CLASS_NAMES[classId] .. " abilities", 2, classId, false)
    end

    player:GossipMenuAddItem(0, "Farewell.", 99, 0, false)
    player:GossipSendMenu(100, creature)
end

-- ============================================================
-- Gossip: Select
-- sender=1   → show class picker for next unlock
-- sender=10  → execute class unlock (classId = intid)
-- sender=2   → browse spells for class (classId = intid, page 0)
-- sender=24  → paginate (intid = classId*1000 + page)
-- sender=25  → buy individual spell directly, then refresh Browse (intid = encoded classId+spellId)
-- sender=27  → buy every currently-available spell for the class (intid = classId)
-- sender=99  → close
-- ============================================================

local function OnGossipSelect(event, player, creature, sender, intid, code, menuId)
    player:GossipClearMenu()

    if sender == 99 or intid == 99 then
        player:GossipComplete()
        return
    end

    local native      = player:GetClass()
    local unlockedCnt = GetUnlockedCount(player)
    local unlocked    = GetUnlockedClasses(player)

    -- ---- sender=1: show class picker for unlock ----
    if sender == 1 then
        local nextMs = GetMilestone(unlockedCnt + 1)
        if not nextMs then
            player:SendBroadcastMessage("Error: milestone data missing.")
            player:GossipComplete()
            return
        end
        if player:GetLevel() < nextMs.level then
            player:SendBroadcastMessage(string.format(
                "You must reach level %d before unlocking another class.", nextMs.level))
            player:GossipComplete()
            return
        end
        local costStr = FormatCopper(nextMs.cost)
        for classId, name in pairs(CLASS_NAMES) do
            if classId ~= native and not unlocked[classId] then
                player:GossipMenuAddItem(0,
                    string.format("%s  [%s]", name, costStr), 10, classId, false)
            end
        end
        player:GossipMenuAddItem(0, "Never mind.", 99, 0, false)
        player:GossipSendMenu(100, creature)
        return
    end

    -- ---- sender=10: execute class unlock ----
    if sender == 10 then
        local classId = intid
        local nextMs  = GetMilestone(unlockedCnt + 1)
        if not nextMs then
            player:SendBroadcastMessage("Error: milestone data missing.")
            player:GossipComplete()
            return
        end
        if player:GetLevel() < nextMs.level then
            player:SendBroadcastMessage(string.format(
                "You must be level %d to unlock another class.", nextMs.level))
            player:GossipComplete()
            return
        end
        if unlocked[classId] then
            player:SendBroadcastMessage("You have already unlocked that class.")
            player:GossipComplete()
            return
        end
        if not CLASS_NAMES[classId] then
            player:SendBroadcastMessage("Unknown class.")
            player:GossipComplete()
            return
        end
        if nextMs.cost > 0 and player:GetCoinage() < nextMs.cost then
            player:SendBroadcastMessage(string.format(
                "You need %s to unlock this class.", FormatCopper(nextMs.cost)))
            player:GossipComplete()
            return
        end

        if nextMs.cost > 0 then player:ModifyMoney(-nextMs.cost) end

        CharDBExecute(string.format(
            "INSERT IGNORE INTO unbound_character_unlocks (char_guid, class_id, unlocked_at_level) " ..
            "VALUES (%d, %d, %d)",
            player:GetGUIDLow(), classId, player:GetLevel()))

        ApplyUnboundPools(player)
        ApplyUnboundSkills(player, classId)
        ApplyUnboundWeaponArmorSkills(player)
        GrantClassGiftSpells(player, classId)
        GrantClassGiftItems(player, classId)

        player:SendBroadcastMessage(string.format(
            "|cff00ff00The path of the %s is now open to you!|r " ..
            "Relog once to see the ability tabs in your spellbook.",
            CLASS_NAMES[classId]))
        player:GossipComplete()
        return
    end

    -- ---- sender=2: open browse for class (page 0) ----
    if sender == 2 then
        local classId = intid
        if not unlocked[classId] then
            player:GossipComplete()
            return
        end
        ShowBrowsePage(player, creature, classId, 0)
        return
    end

    -- ---- sender=24: paginate ----
    if sender == 24 then
        local classId = math.floor(intid / 1000)
        local page    = intid % 1000
        if not unlocked[classId] then
            player:GossipComplete()
            return
        end
        ShowBrowsePage(player, creature, classId, page)
        return
    end

    -- ---- sender=25: buy individual spell, then refresh the Browse page ----
    if sender == 25 then
        local classId, spellId = DecodeClassSpell(intid)
        local Q = WorldDBQuery(string.format(
            "SELECT gold_cost_copper FROM unbound_class_catalog WHERE class_id = %d AND spell_id = %d",
            classId, spellId))
        if not Q then
            player:SendBroadcastMessage("Spell not found in catalog.")
            player:GossipComplete()
            return
        end
        local cost = Q:GetUInt32(0)

        if not unlocked[classId] then
            player:SendBroadcastMessage("You have not unlocked that class.")
            player:GossipComplete()
            return
        end
        if player:HasSpell(spellId) then
            player:SendBroadcastMessage("You already know that ability.")
            ShowBrowsePage(player, creature, classId, 0)
            return
        end
        local prereq = PREREQ_MAP[classId] and PREREQ_MAP[classId][spellId] or 0
        if prereq > 0 and not player:HasSpell(prereq) then
            player:SendBroadcastMessage(string.format(
                "You must learn %s first.", GetSpellDisplayName(prereq)))
            ShowBrowsePage(player, creature, classId, 0)
            return
        end
        if player:GetCoinage() < cost then
            player:SendBroadcastMessage(string.format(
                "You need %s to buy that ability.", FormatCopper(cost)))
            ShowBrowsePage(player, creature, classId, 0)
            return
        end

        player:ModifyMoney(-cost)
        player:LearnSpell(spellId)
        player:SendBroadcastMessage(string.format(
            "|cff00ff00Learned %s!|r", GetSpellDisplayName(spellId)))
        ShowBrowsePage(player, creature, classId, 0)
        return
    end

    -- ---- sender=27: buy every available spell for the class ----
    if sender == 27 then
        local classId = intid
        if not unlocked[classId] then
            player:GossipComplete()
            return
        end

        if #GetBuyableSpells(player, classId) == 0 then
            player:SendBroadcastMessage("You already know everything currently available.")
            ShowBrowsePage(player, creature, classId, 0)
            return
        end

        -- Re-query each pass: buying a spell can satisfy another's prereq,
        -- which only shows up once GetBuyableSpells re-checks HasSpell().
        local learned = 0
        while true do
            local list = GetBuyableSpells(player, classId)
            if #list == 0 then break end
            local boughtAny = false
            for _, sp in ipairs(list) do
                if player:GetCoinage() >= sp.cost then
                    player:ModifyMoney(-sp.cost)
                    player:LearnSpell(sp.id)
                    learned = learned + 1
                    boughtAny = true
                end
            end
            if not boughtAny then break end
        end

        if learned == 0 then
            player:SendBroadcastMessage("You can't afford any available abilities right now.")
        else
            player:SendBroadcastMessage(string.format(
                "|cff00ff00Learned %d %s abilities!|r", learned, CLASS_NAMES[classId]))
        end
        ShowBrowsePage(player, creature, classId, 0)
        return
    end

    player:GossipComplete()
end

-- ============================================================
-- Mentor Stone item use handler
-- ============================================================
-- Fires when a player right-clicks the Unbounding Mentor Stone (entry 900100).
-- Summons the Mentor NPC 3 yards in front of the player for 3 minutes.
--
-- Uses ITEM_EVENT_ON_USE (event=2), which fires before the item spell is cast.
-- Returning false lets AzerothCore proceed with CastItemUseSpell, which applies
-- the 3-minute cooldown from item_template.spellcooldown_1.
-- Returning true (or nil) tells Eluna "handled it" — skips the spell cast.
-- The Lua-side STONE_LAST_USE table guards against the server-restart case
-- where spellcooldown_1 state is lost but the Lua table has been reset too.
RegisterItemEvent(MENTOR_STONE_ENTRY, 2, function(event, player, item, target)
    local guid = player:GetGUIDLow()
    local now  = os.time()
    local last = STONE_LAST_USE[guid] or 0

    if (now - last) < STONE_COOLDOWN_SEC then
        local remaining = STONE_COOLDOWN_SEC - (now - last)
        player:SendBroadcastMessage(string.format(
            "|cffff4444Unbounding Mentor Stone is on cooldown (%ds remaining).|r", remaining))
        return true  -- Eluna "handled" this use; skip the spell cast
    end

    STONE_LAST_USE[guid] = now

    -- Summon 3 yards ahead of the player, facing back toward the player.
    local angle = player:GetO()
    local x = player:GetX() + math.cos(angle) * 3
    local y = player:GetY() + math.sin(angle) * 3
    local z = player:GetZ()
    local face = angle + math.pi  -- face the player

    -- TEMPSUMMON_TIMED_DESPAWN (3): despawn after 180 000 ms regardless of state.
    local mentor = player:SpawnCreature(MENTOR_ENTRY, x, y, z, face, 3, 180000)
    if not mentor then
        player:SendBroadcastMessage(
            "|cffff4444Could not summon the Mentor here. Try again in the open world.|r")
        STONE_LAST_USE[guid] = 0  -- refund cooldown on failure
        return true
    end

    player:SendBroadcastMessage(
        "|cff00ff00Your Unbounding Mentor has arrived. (3 min)|r")
    -- Return true: cancel the item's spell cast. spellid_1 only exists so the
    -- 3.3.5a client recognizes this as a usable item and sends CMSG_USE_ITEM —
    -- a custom server-only spell ID (900200, absent from the client's Spell.dbc)
    -- left the client unable to resolve the item, so right-click did nothing.
    -- The Lua-side STONE_LAST_USE cooldown (180s, matching spellcooldown_1)
    -- fully replaces the need for the real spell cast to apply a cooldown.
    return true
end)

-- ============================================================
-- Event registration
-- ============================================================

RegisterCreatureGossipEvent(MENTOR_ENTRY, 1, OnGossipHello)
RegisterCreatureGossipEvent(MENTOR_ENTRY, 2, OnGossipSelect)

-- On level-up: re-apply pools so the mana pool grows with the player's level.
-- The C++ OnAfterUpdateMaxPower hook (UnboundSystem.cpp) preserves the pool across
-- stat recalculations, but it locks in the OLD value — so we must re-calculate
-- after each level change.  Short 200 ms delay lets GiveLevel() finish its own
-- UpdateAllStats() pass before we write the new value.
RegisterPlayerEvent(13, function(event, player, oldLevel)
    local Q = CharDBQuery(string.format(
        "SELECT 1 FROM unbound_character_unlocks WHERE char_guid = %d LIMIT 1",
        player:GetGUIDLow()))
    if Q then
        player:RegisterEvent(function()
            pcall(function() ApplyUnboundPools(player) end)
        end, 200, 1)
    end
end)

-- On login: restore pools, skills, weapon/armor proficiency, gift spells,
-- and ensure the player has their Mentor Stone.
-- Delayed 1s: calling SetMaxPower during PLAYER_EVENT_ON_LOGIN crashes
-- AzerothCore before the character is fully in-world.
RegisterPlayerEvent(3, function(event, player)
    -- Give the Mentor Stone to any character that doesn't have it.
    -- Runs unconditionally so existing characters and anyone who deleted
    -- theirs get it back automatically.
    if player:GetItemCount(MENTOR_STONE_ENTRY) == 0 then
        player:AddItem(MENTOR_STONE_ENTRY, 1)
    end

    local Q = CharDBQuery(string.format(
        "SELECT 1 FROM unbound_character_unlocks WHERE char_guid = %d LIMIT 1",
        player:GetGUIDLow()))
    if Q then
        -- The `player` userdata captured directly in this closure goes stale by the
        -- time the 1s timer fires (Eluna invalidates it during the login->world
        -- transition: "pointer to nonexisting (invalidated) object"). Capture the
        -- GUID instead and re-fetch a live Player reference inside the callback.
        local guid = player:GetGUID()
        local guidLow = player:GetGUIDLow()
        player:RegisterEvent(function()
            local livePlayer = GetPlayerByGUID(guid)
            if not livePlayer or not livePlayer:IsInWorld() then
                return
            end
            local ok, err = pcall(function()
                ApplyUnboundPools(livePlayer)
                ApplyAllUnboundSkills(livePlayer)
                ApplyUnboundWeaponArmorSkills(livePlayer)
                GrantAllClassGiftSpells(livePlayer)
                GrantAllClassGiftItems(livePlayer)
            end)
            if not ok then
                print(string.format("[UNBOUND] OnLogin post-login setup ERROR for guidLow=%d: %s",
                    guidLow, tostring(err)))
            end
        end, 1000, 1)
    end
end)
WU_PAYLOAD_EOF_13

    cat > "$MODULE_DIR/unbound-core-access.patch" <<'WU_PAYLOAD_EOF_19'
diff --git a/src/server/game/Conditions/ConditionMgr.cpp b/src/server/game/Conditions/ConditionMgr.cpp
index 90319545d..9a5ba20ed 100644
--- a/src/server/game/Conditions/ConditionMgr.cpp
+++ b/src/server/game/Conditions/ConditionMgr.cpp
@@ -132,7 +132,15 @@ bool Condition::Meets(ConditionSourceInfo& sourceInfo)
     case CONDITION_CLASS:
     {
         if (Unit* unit = object->ToUnit())
+        {
             condMeets = unit->getClassMask() & ConditionValue1;
+
+            // Unbound Wrath Edition — also meets the condition if any class
+            // this character has unlocked via the Mentor matches.
+            if (!condMeets)
+                if (Player* player = unit->ToPlayer())
+                    condMeets = player->GetUnboundClassMask() & ConditionValue1;
+        }
         break;
     }
     case CONDITION_RACE:
diff --git a/src/server/game/Entities/Creature/Trainer.cpp b/src/server/game/Entities/Creature/Trainer.cpp
index 58b61aabb..819a6e5a6 100644
--- a/src/server/game/Entities/Creature/Trainer.cpp
+++ b/src/server/game/Entities/Creature/Trainer.cpp
@@ -216,7 +216,12 @@ namespace Trainer
             case Type::Class:
             case Type::Pet:
                 // check class for class trainers
-                return player->getClass() == GetTrainerRequirement();
+                if (player->getClass() == GetTrainerRequirement())
+                    return true;
+
+                // Unbound Wrath Edition — also valid for any class this
+                // character has unlocked via the Mentor.
+                return (player->GetUnboundClassMask() & (1u << (GetTrainerRequirement() - 1))) != 0;
             case Type::Mount:
                 // check race for mount trainers
                 return player->getRace() == GetTrainerRequirement();
diff --git a/src/server/game/Entities/Player/Player.cpp b/src/server/game/Entities/Player/Player.cpp
index ee531531a..335b9b2cd 100644
--- a/src/server/game/Entities/Player/Player.cpp
+++ b/src/server/game/Entities/Player/Player.cpp
@@ -12367,7 +12367,9 @@ float Player::GetReputationPriceDiscount(FactionTemplateEntry const* factionTemp
 bool Player::IsSpellFitByClassAndRace(uint32 spell_id) const
 {
     uint32 racemask  = getRaceMask();
-    uint32 classmask = getClassMask();
+    // Unbound Wrath Edition — also fit spells belonging to any class this
+    // character has unlocked via the Mentor (trainer spell visibility).
+    uint32 classmask = getClassMask() | GetUnboundClassMask();
 
     SkillLineAbilityMapBounds bounds = sSpellMgr->GetSkillLineAbilityMapBounds(spell_id);
     if (bounds.first == bounds.second)
diff --git a/src/server/game/Entities/Player/Player.h b/src/server/game/Entities/Player/Player.h
index 4f38d8012..190374724 100644
--- a/src/server/game/Entities/Player/Player.h
+++ b/src/server/game/Entities/Player/Player.h
@@ -2134,6 +2134,12 @@ public:
     void SetFactionForRace(uint8 race);
     void setTeamId(TeamId teamid) { m_team = teamid; };
 
+    // Unbound Wrath Edition — bitmask of classes this character has access to
+    // via the Mentor (native class | every class unlocked in unbound_character_unlocks).
+    // 0 means this character is not Unbound. Populated on login by UnboundSystem.cpp.
+    [[nodiscard]] uint32 GetUnboundClassMask() const { return m_unboundClassMask; }
+    void SetUnboundClassMask(uint32 mask) { m_unboundClassMask = mask; }
+
     void InitDisplayIds();
 
     bool IsAtGroupRewardDistance(WorldObject const* pRewardSource) const;
@@ -2829,6 +2835,7 @@ protected:
     ObjectGuid m_lootGuid;
 
     TeamId m_team;
+    uint32 m_unboundClassMask = 0; // Unbound Wrath Edition — see GetUnboundClassMask()
     uint32 m_nextSave; // pussywizard
     uint16 m_additionalSaveTimer; // pussywizard
     uint8 m_additionalSaveMask; // pussywizard
diff --git a/src/server/game/Entities/Player/PlayerQuest.cpp b/src/server/game/Entities/Player/PlayerQuest.cpp
index 94ac419b4..b92ab0925 100644
--- a/src/server/game/Entities/Player/PlayerQuest.cpp
+++ b/src/server/game/Entities/Player/PlayerQuest.cpp
@@ -1091,7 +1091,9 @@ bool Player::SatisfyQuestClass(Quest const* qInfo, bool msg) const
     if (reqClass == 0)
         return true;
 
-    if ((reqClass & getClassMask()) == 0)
+    // Unbound Wrath Edition — also satisfy class quests for any class this
+    // character has unlocked via the Mentor.
+    if ((reqClass & (getClassMask() | GetUnboundClassMask())) == 0)
     {
         if (msg)
             SendCanTakeQuestResponse(INVALIDREASON_DONT_HAVE_REQ);
diff --git a/src/server/game/Entities/Player/PlayerStorage.cpp b/src/server/game/Entities/Player/PlayerStorage.cpp
index eb7f10aab..1e37b5b9b 100644
--- a/src/server/game/Entities/Player/PlayerStorage.cpp
+++ b/src/server/game/Entities/Player/PlayerStorage.cpp
@@ -2389,7 +2389,9 @@ InventoryResult Player::CanUseItem(ItemTemplate const* proto) const
         return EQUIP_ERR_YOU_CAN_NEVER_USE_THAT_ITEM;
     }
 
-    if ((proto->AllowableClass & getClassMask()) == 0 || (proto->AllowableRace & getRaceMask()) == 0)
+    // Unbound Wrath Edition — characters who've unlocked extra classes via the
+    // Mentor can ignore an item's AllowableClass restriction; AllowableRace is untouched.
+    if ((GetUnboundClassMask() == 0 && (proto->AllowableClass & getClassMask()) == 0) || (proto->AllowableRace & getRaceMask()) == 0)
     {
         return EQUIP_ERR_YOU_CAN_NEVER_USE_THAT_ITEM;
     }
WU_PAYLOAD_EOF_19

    print_success "Module files staged: mod-unbound/ (C++ + SQL) and env/dist/etc/modules/lua_scripts/unbound_mentor.lua"
    echo ""
}

# ============================================================
#  stage_mod_ale()
#
#  mod-unbound's entire player-facing system (Mentor, spell catalog,
#  Mentor Stone) is driven by env/dist/etc/modules/lua_scripts/unbound_mentor.lua,
#  which requires Eluna/ALE — AzerothCore's Lua scripting engine — to be
#  compiled into the worldserver. install-wow-wotlk.sh does NOT include
#  mod-ale by default. A server built purely from install-wow-wotlk.sh has
#  no Eluna engine at all — ALE.Enabled = 1 and unbound_mentor.lua just sit
#  there inert, the Mentor Stone casts its raw bound spell (Food) and does
#  nothing else, and "[UNBOUND] Prereq map built." never appears no matter
#  how long you wait.
#
#  Source: official azerothcore/mod-ale, pinned to the commit confirmed
#  working alongside mod-unbound on the dev server — not floating
#  "master", to avoid introducing a second moving part while debugging.
#
#  Idempotent: if modules/mod-ale already has a CMakeLists.txt (pre-existing
#  on this server, or staged by a prior run), this is a no-op.
# ============================================================
MOD_ALE_COMMIT="1cb86c9600260c3731c96dc3c98d25b4fc3f2153"

stage_mod_ale() {
    print_step "Checking for the Eluna/ALE Lua engine module (mod-ale)..."

    local MOD_ALE_DIR="$SERVER_DIR/modules/mod-ale"

    # CMakeLists.txt at the module root is what makes this a real, buildable
    # AzerothCore module (every entry in modules/ has one — see mod-unbound,
    # mod-playerbots, etc.). Checking for it (not just "directory exists and
    # is non-empty") matters because git populates .git with objects BEFORE
    # checking out the working tree — an interrupted clone (dropped Wi-Fi,
    # etc.) can leave a non-empty modules/mod-ale/ with a .git folder but no
    # actual module files. A bare non-empty check would call that "already
    # staged" forever, print success, and leave Eluna uncompiled with no
    # further warning.
    if [ -f "$MOD_ALE_DIR/CMakeLists.txt" ]; then
        print_success "mod-ale already present in modules/ — skipping."
        echo ""
        return
    fi

    if [ -d "$MOD_ALE_DIR" ]; then
        print_warning "Found an incomplete modules/mod-ale/ (no CMakeLists.txt) —"
        print_warning "likely an interrupted clone from a previous run. Removing it"
        print_warning "and cloning fresh."
        rm -rf "$MOD_ALE_DIR"
    fi

    if ! command -v git >/dev/null 2>&1; then
        print_warning "git is not available — cannot stage mod-ale automatically."
        echo "Without it, Eluna never compiles into the worldserver and"
        echo "unbound_mentor.lua will never load. Install git, then run:"
        echo -e "${CYAN}   git clone https://github.com/azerothcore/mod-ale.git $MOD_ALE_DIR${NC}"
        echo -e "${CYAN}   cd $MOD_ALE_DIR && git checkout $MOD_ALE_COMMIT${NC}"
        echo "Then re-run this installer."
        exit 1
    fi

    echo "This server's worldserver doesn't have Eluna (the Lua engine mod-unbound"
    echo "depends on) compiled in yet. Staging the official azerothcore/mod-ale"
    echo "module now so it gets built in during the rebuild below."
    echo ""

    if ! git clone https://github.com/azerothcore/mod-ale.git "$MOD_ALE_DIR"; then
        print_warning "Failed to clone mod-ale (network issue, or modules/ isn't writable)."
        echo "Clone it manually and re-run this installer:"
        echo -e "${CYAN}   git clone https://github.com/azerothcore/mod-ale.git $MOD_ALE_DIR${NC}"
        echo -e "${CYAN}   cd $MOD_ALE_DIR && git checkout $MOD_ALE_COMMIT${NC}"
        exit 1
    fi

    if ! (cd "$MOD_ALE_DIR" && git checkout --quiet "$MOD_ALE_COMMIT"); then
        print_warning "Cloned mod-ale but couldn't check out the pinned commit ($MOD_ALE_COMMIT)."
        echo "Continuing with whatever was checked out by default — this may or"
        echo "may not match the version Wrath Unbound was tested against."
    fi

    # Belt and suspenders: confirm the working tree actually has the module,
    # not just that git exited 0 (e.g. disk-full mid-checkout).
    if [ ! -f "$MOD_ALE_DIR/CMakeLists.txt" ]; then
        print_warning "mod-ale was cloned but CMakeLists.txt is missing — the checkout"
        print_warning "looks incomplete (disk space?). Re-run this installer, or fix"
        print_warning "it manually:"
        echo -e "${CYAN}   rm -rf $MOD_ALE_DIR${NC}"
        echo -e "${CYAN}   git clone https://github.com/azerothcore/mod-ale.git $MOD_ALE_DIR${NC}"
        echo -e "${CYAN}   cd $MOD_ALE_DIR && git checkout $MOD_ALE_COMMIT${NC}"
        exit 1
    fi

    print_success "Staged mod-ale (Eluna/ALE) — will be compiled in during the rebuild below."
    echo ""
}

# ============================================================
#  apply_sql_migrations()
#
#  Runs the 14 SQL migrations against the right database, in order.
#  AzerothCore's built-in DB updater does NOT auto-apply files from
#  modules/<mod>/data/sql/** on this build (verified live — only
#  01_unbound_world.sql ended up tracked in the `updates` table, the
#  rest were applied by hand during dev), so we pipe each file into
#  mysql directly. All 14 are confirmed safe to re-run (idempotent —
#  INSERT IGNORE / ON DUPLICATE KEY UPDATE / CREATE TABLE IF NOT EXISTS
#  / information_schema guards), so this is safe on upgrade runs too.
# ============================================================
apply_sql_migrations() {
    print_step "Applying Wrath Unbound SQL migrations..."

    local MODULE_SQL="$SERVER_DIR/modules/mod-unbound/data/sql"
    local FAILED=0

    local DB_WORLD_FILES=(
        "01_unbound_world.sql"
        "02_fix_catalog_req_level.sql"
        "03_creation_gift_spells.sql"
        "04_catalog_druid_forms.sql"
        "05_individual_purchase_prereqs.sql"
        "06_universal_skill_access.sql"
        "07_mentor_stone.sql"
        "08_catalog_additions.sql"
        "10_catalog_audit_fixes.sql"
        "11_catalog_gap_additions.sql"
        "12_mount_spell_fix.sql"
        "13_flight_form_fix.sql"
        "14_judgement_fix.sql"
    )

    local FILE ERRMSG
    for FILE in "${DB_WORLD_FILES[@]}"; do
        echo -e "${CYAN}   Applying db-world/${FILE}...${NC}"
        ERRMSG=$(docker exec -i ac-database mysql -u root -ppassword acore_world \
                < "$MODULE_SQL/db-world/$FILE" 2>&1 >/dev/null)
        if [ $? -ne 0 ]; then
            print_warning "Failed to apply db-world/${FILE}"
            [ -n "$ERRMSG" ] && echo "   MySQL error: $ERRMSG"
            FAILED=1
        fi
    done

    echo -e "${CYAN}   Applying db-characters/01_unbound_characters.sql...${NC}"
    ERRMSG=$(docker exec -i ac-database mysql -u root -ppassword acore_characters \
            < "$MODULE_SQL/db-characters/01_unbound_characters.sql" 2>&1 >/dev/null)
    if [ $? -ne 0 ]; then
        print_warning "Failed to apply db-characters/01_unbound_characters.sql"
        [ -n "$ERRMSG" ] && echo "   MySQL error: $ERRMSG"
        FAILED=1
    fi

    # Apply npc_setup.sql HERE — before the worldserver rebuild — so the
    # creature_template entry for the Mentor (900001) exists when the server
    # starts.  RegisterCreatureGossipEvent(900001, ...) in the Lua crashes at
    # load time if the template is missing, preventing [UNBOUND] Prereq map
    # built. from ever appearing and causing wait_for_server() to time out.
    echo -e "${CYAN}   Applying npc_setup.sql (Mentor NPC template)...${NC}"
    local MODULE_DIR="$SERVER_DIR/modules/mod-unbound"
    ERRMSG=$(docker exec -i ac-database mysql -u root -ppassword acore_world \
            < "$MODULE_DIR/npc_setup.sql" 2>&1 >/dev/null) || true
    if [ -n "$ERRMSG" ] && echo "$ERRMSG" | grep -iv "warning\|insecure" | grep -q .; then
        print_warning "npc_setup.sql reported an error (Mentor template may be missing)"
        echo "   MySQL error: $ERRMSG"
        FAILED=1
    else
        print_success "Mentor NPC template (entry 900001) staged in world database."
    fi

    if [ "$FAILED" -ne 0 ]; then
        echo ""
        print_warning "One or more migrations failed to apply."
        echo "Your databases were backed up before any changes:"
        echo -e "${CYAN}   $BACKUP_DIR${NC}"
        echo "Restore from there if you need to roll back, then check the output"
        echo "above for the specific error before re-running this installer."
        exit 1
    fi

    print_success "All SQL migrations applied."
    echo ""
}

# ============================================================
#  apply_core_patches()
#
#  Wrath Unbound's cross-class access (Mentor-unlocked classes can train
#  abilities from class trainers, equip that class's gear, and accept that
#  class's quests) requires a small AzerothCore core-engine change: a new
#  Player::m_unboundClassMask field plus the five call sites that consult
#  it (Trainer.cpp, Player.cpp, PlayerQuest.cpp, PlayerStorage.cpp,
#  ConditionMgr.cpp). mod-unbound's OnPlayerLogin hook (UnboundSystem.cpp)
#  populates this field via SetUnboundClassMask() — the worldserver will
#  not COMPILE without this patch, since that method doesn't exist in
#  stock AzerothCore.
#
#  Applied with `git apply` against $SERVER_DIR (a git checkout — git is
#  already a hard dependency, used by stage_mod_ale() above).
#
#  Idempotent: if Player.h already declares GetUnboundClassMask(), the
#  patch was applied by a previous run and this is a no-op.
# ============================================================
apply_core_patches() {
    print_step "Applying Wrath Unbound core-engine patch (cross-class access)..."

    local PLAYER_H="$SERVER_DIR/src/server/game/Entities/Player/Player.h"
    local MODULE_DIR="$SERVER_DIR/modules/mod-unbound"
    local PATCH_FILE="$MODULE_DIR/unbound-core-access.patch"

    if [ ! -f "$PLAYER_H" ]; then
        print_warning "Could not find Player.h at:"
        print_warning "  $PLAYER_H"
        echo "Cross-class trainer/quest/item access requires this core-engine"
        echo "patch — without it the worldserver won't compile. Check that"
        echo "SERVER_DIR points at a real AzerothCore source checkout."
        exit 1
    fi

    if grep -q "GetUnboundClassMask" "$PLAYER_H"; then
        print_success "Core-engine cross-class patch already applied — nothing to do."
        echo ""
        return
    fi

    if [ ! -f "$PATCH_FILE" ]; then
        print_warning "Patch file missing at $PATCH_FILE — was stage_module_files run?"
        exit 1
    fi

    cd "$SERVER_DIR" || exit 1

    if ! git apply --check "$PATCH_FILE" 2>/dev/null; then
        print_warning "Core-engine patch did not apply cleanly to your AzerothCore source."
        echo "This usually means your src/ tree has diverged from the version"
        echo "Wrath Unbound was built against."
        echo ""
        echo "The patch is saved at:"
        echo -e "${CYAN}   $PATCH_FILE${NC}"
        echo "A maintainer can review and apply this 6-file diff by hand. Cross-class"
        echo "trainer/quest/item access won't work until this is resolved — everything"
        echo "else Wrath Unbound provides (Mentor, catalog, power pools, skills) is"
        echo "unaffected."
        exit 1
    fi

    git apply "$PATCH_FILE"
    print_success "Core-engine cross-class access patch applied (6 files)."
    echo "(Player::m_unboundClassMask + trainer/quest/item/condition checks)"
    echo ""
}

# ============================================================
#  configure_ale()
#
#  Wrath Unbound's Mentor (and Mentor Stone) is entirely driven by
#  env/dist/etc/modules/lua_scripts/unbound_mentor.lua via Eluna/ALE.
#  That directory is bind-mounted into the container for free as part
#  of AzerothCore's stock env/dist/etc mount — no custom volume mount
#  or AC_ALE_SCRIPT_PATH env var is needed, and docker-compose.override.yml
#  is never touched.
#
#  This matches the convention used by wow-manage.sh (the dads-mmo-lab
#  CLI) for all ALE-Kegs Lua mods, so unbound_mentor.lua sits alongside
#  any other ALE mods a player has installed via that tool, sharing the
#  same lua_scripts/ directory and mod_ale.conf.
#
#  This function only ensures mod_ale.conf (shared by all ALE mods) has
#  ALE.Enabled = 1 (integer — "true" is silently ignored) and ALE.ScriptPath
#  pointing at that shared directory. If wow-manage.sh (or a prior run)
#  already created mod_ale.conf, we correct it in place rather than
#  overwrite it — other ALE mods may depend on settings already in there.
#
#  Idempotent: re-running is a no-op if already correct.
# ============================================================
configure_ale() {
    print_step "Configuring mod_ale.conf for Lua script support..."

    local ALE_SCRIPT_PATH="/azerothcore/env/dist/etc/modules/lua_scripts"
    local MODULES_CONF_DIR="$SERVER_DIR/env/dist/etc/modules"
    local ALE_CONF="$MODULES_CONF_DIR/mod_ale.conf"

    if ! mkdir -p "$MODULES_CONF_DIR" 2>/dev/null; then
        print_warning "Could not create $MODULES_CONF_DIR"
        echo "The directory may be owned by root. Try:"
        echo -e "  ${CYAN}sudo mkdir -p $MODULES_CONF_DIR${NC}"
        echo -e "  ${CYAN}sudo chown deck:deck $MODULES_CONF_DIR${NC}"
        echo "Then re-run the installer."
        exit 1
    fi

    if [ ! -f "$ALE_CONF" ]; then
        if cat > "$ALE_CONF" << ALE_CONF_EOF
ALE.Enabled = 1
ALE.TraceBack = false
ALE.ScriptPath = "$ALE_SCRIPT_PATH"
ALE.PlayerAnnounceReload = false
ALE.RequirePaths = ""
ALE.RequireCPaths = ""
ALE.AutoReload = false
ALE.AutoReloadInterval = 1
ALE.BytecodeCache = true
ALE_CONF_EOF
        then
            print_success "Created mod_ale.conf (ALE.Enabled = 1, ALE.ScriptPath = \"$ALE_SCRIPT_PATH\")."
        else
            print_warning "Failed to write mod_ale.conf to $ALE_CONF"
            echo "The directory may be owned by root. Try:"
            echo -e "  ${CYAN}sudo chown deck:deck $ALE_CONF${NC}"
            echo "Then re-run the installer."
            exit 1
        fi
    else
        # Ensure existing conf has the correct integer format for ALE.Enabled
        if grep -q "ALE.Enabled = true" "$ALE_CONF" 2>/dev/null; then
            sed -i 's/ALE\.Enabled = true/ALE.Enabled = 1/' "$ALE_CONF"
            print_success "mod_ale.conf: corrected ALE.Enabled to integer format (1)."
        elif grep -q "^ALE.Enabled = 1" "$ALE_CONF" 2>/dev/null; then
            print_success "mod_ale.conf: ALE.Enabled = 1 already set."
        else
            echo "ALE.Enabled = 1" >> "$ALE_CONF"
            print_success "mod_ale.conf: added ALE.Enabled = 1."
        fi

        # Ensure ALE.ScriptPath points at the shared lua_scripts directory —
        # may already be set correctly by wow-manage.sh, or may be a stale
        # relative path / wrong directory from an older convention.
        if grep -qF "ALE.ScriptPath = \"$ALE_SCRIPT_PATH\"" "$ALE_CONF" 2>/dev/null; then
            print_success "mod_ale.conf: ALE.ScriptPath already correct."
        elif grep -q "^ALE.ScriptPath" "$ALE_CONF" 2>/dev/null; then
            sed -i "s|^ALE\.ScriptPath[[:space:]]*=.*\$|ALE.ScriptPath = \"$ALE_SCRIPT_PATH\"|" "$ALE_CONF"
            print_success "mod_ale.conf: corrected ALE.ScriptPath to \"$ALE_SCRIPT_PATH\"."
        else
            echo "ALE.ScriptPath = \"$ALE_SCRIPT_PATH\"" >> "$ALE_CONF"
            print_success "mod_ale.conf: added ALE.ScriptPath = \"$ALE_SCRIPT_PATH\"."
        fi
    fi

    echo ""
}

# ============================================================
#  verify_ale_config()
#  Confirms unbound_mentor.lua is staged where ALE actually scans
#  (env/dist/etc/modules/lua_scripts/), and that mod_ale.conf has
#  ALE enabled and pointed at that same directory. This is the
#  reality check that catches the "Mentor says Greetings only /
#  Mentor Stone just eats" failure mode before the user logs in.
# ============================================================
verify_ale_config() {
    print_step "Verifying ALE/Lua script configuration..."

    local ALE_SCRIPT_PATH="/azerothcore/env/dist/etc/modules/lua_scripts"
    local LUA_SCRIPT="$SERVER_DIR/env/dist/etc/modules/lua_scripts/unbound_mentor.lua"
    local ALE_CONF="$SERVER_DIR/env/dist/etc/modules/mod_ale.conf"
    local errors=0

    if [ -f "$LUA_SCRIPT" ]; then
        print_success "unbound_mentor.lua staged at env/dist/etc/modules/lua_scripts/"
    else
        print_warning "unbound_mentor.lua NOT found at $LUA_SCRIPT"
        errors=$((errors + 1))
    fi

    if [ -f "$ALE_CONF" ]; then
        if grep -q "^ALE.Enabled = 1" "$ALE_CONF"; then
            print_success "mod_ale.conf: ALE.Enabled = 1"
        else
            local found_val
            found_val=$(grep "ALE.Enabled" "$ALE_CONF" 2>/dev/null || echo "(ALE.Enabled line not found)")
            print_warning "mod_ale.conf: unexpected value — $found_val"
            errors=$((errors + 1))
        fi

        if grep -qF "ALE.ScriptPath = \"$ALE_SCRIPT_PATH\"" "$ALE_CONF"; then
            print_success "mod_ale.conf: ALE.ScriptPath = \"$ALE_SCRIPT_PATH\""
        else
            local found_path
            found_path=$(grep "ALE.ScriptPath" "$ALE_CONF" 2>/dev/null || echo "(ALE.ScriptPath line not found)")
            print_warning "mod_ale.conf: ALE.ScriptPath not pointing at lua_scripts/ — $found_path"
            errors=$((errors + 1))
        fi
    else
        print_warning "mod_ale.conf: not found at $ALE_CONF"
        errors=$((errors + 1))
    fi

    echo ""
    if [ "$errors" -gt 0 ]; then
        print_warning "$errors verification check(s) failed."
        echo "The Mentor's Lua script will NOT load until these are resolved."
        echo "Review the warnings above, correct the file manually if needed, then re-run."
        echo ""
        if ! ask_yes_no "Continue anyway?"; then
            exit 1
        fi
    else
        print_success "ALE config looks correct — the Mentor's Lua script should load on restart."
    fi

    echo ""
}

# ============================================================
#  patch_worldserver_conf()
#
#  Wrath Unbound REQUIRES ValidateSkillLearnedBySpells = 0 — if left at
#  the AzerothCore default of 1, the server strips every cross-class
#  spell from a character's record on each login, silently wiping
#  anything purchased through the Mentor. The setting is always present
#  and uncommented in worldserver.conf (`ValidateSkillLearnedBySpells = N`)
#  so the sed pattern is reliable.
# ============================================================
patch_worldserver_conf() {
    print_step "Checking worldserver.conf for the setting Wrath Unbound requires..."

    local CONF="$SERVER_DIR/env/dist/etc/worldserver.conf"

    if [ ! -f "$CONF" ]; then
        print_warning "Could not find worldserver.conf at:"
        print_warning "  $CONF"
        echo "You'll need to set this yourself before Wrath Unbound will work"
        echo "correctly:"
        echo -e "${CYAN}   ValidateSkillLearnedBySpells = 0${NC}"
        echo "Without it, AzerothCore strips cross-class spells from every"
        echo "character on login — wiping anything purchased through the Mentor."
        echo ""
        return
    fi

    local CURRENT
    CURRENT=$(grep -m1 "^ValidateSkillLearnedBySpells" "$CONF" | sed 's/.*=\s*//' | tr -d '[:space:]')

    if [ "$CURRENT" = "0" ]; then
        print_success "ValidateSkillLearnedBySpells is already set to 0 — nothing to change."
    else
        sed -i "s|^ValidateSkillLearnedBySpells.*|ValidateSkillLearnedBySpells = 0|" "$CONF"
        print_success "Set ValidateSkillLearnedBySpells = 0 in worldserver.conf"
        echo "(Required: without this, AzerothCore strips cross-class spells from"
        echo "every character on login — wiping anything bought through the Mentor.)"
    fi
    echo ""
}

# ============================================================
#  rebuild_server()
#
#  mod-unbound is a C++ module — it must be compiled into the
#  worldserver binary before any of its hooks (mana-pool preservation,
#  weapon/armor proficiency) take effect. This is an INCREMENTAL
#  rebuild — Docker reuses the existing compiled layers and only
#  compiles the new module in — NOT the original 2-4 hour from-scratch
#  Playerbots build, but still a real wait on Steam Deck hardware
#  (~30-90 min, per the dev rebuild history in CLAUDE.md).
#
#  Commands match the exact sequence used (and confirmed working)
#  during development: `docker compose build ac-worldserver` then
#  `docker compose up -d --force-recreate ac-worldserver`.
# ============================================================
rebuild_server() {
    print_step "Rebuilding the worldserver with the Wrath Unbound module..."
    echo ""
    echo "mod-unbound is a compiled C++ module — your worldserver needs an"
    echo "incremental rebuild to pick it up. Docker reuses your existing"
    echo "compiled layers and only builds the new module in, so this is much"
    echo "faster than the original multi-hour Playerbots compile, but it'll"
    echo "still take roughly 30-90 minutes on a Steam Deck."
    echo ""
    print_warning "Keep your Steam Deck plugged in and awake during this step."
    echo ""

    if ! ask_yes_no "Ready to rebuild the worldserver now?"; then
        echo ""
        echo "No problem — your module files and SQL migrations are already in"
        echo "place. When you're ready, rebuild manually with:"
        echo -e "${CYAN}   cd $SERVER_DIR${NC}"
        echo -e "${CYAN}   docker compose build ac-worldserver${NC}"
        echo -e "${CYAN}   docker compose up -d --force-recreate ac-worldserver${NC}"
        exit 0
    fi

    local LOGFILE="$HOME/wrath-unbound-rebuild.log"
    echo -e "${CYAN}   Progress saved to: $LOGFILE${NC}"
    echo -e "${CYAN}   Go grab a coffee — this will take a while.${NC}"
    echo ""

    cd "$SERVER_DIR" || exit 1

    docker compose build ac-worldserver 2>&1 | tee "$LOGFILE"
    if [ "${PIPESTATUS[0]}" -ne 0 ]; then
        print_warning "Build failed — check $LOGFILE for details."
        echo "Your databases were backed up before any changes were made:"
        echo -e "${CYAN}   $BACKUP_DIR${NC}"
        exit 1
    fi

    docker compose up -d --force-recreate ac-worldserver 2>&1 | tee -a "$LOGFILE"
    if [ "${PIPESTATUS[0]}" -ne 0 ]; then
        print_warning "Restart failed — check $LOGFILE for details."
        exit 1
    fi

    print_success "Worldserver rebuilt and restarted with Wrath Unbound."
    echo ""
}

# ============================================================
#  wait_for_server()
#
#  Confirms Wrath Unbound's Lua script loaded cleanly after the
#  rebuild + restart by polling for "[UNBOUND] Prereq map built." —
#  the exact line printed when the Lua catalog/PREREQ_MAP builds without
#  errors. --force-recreate in rebuild_server() creates a brand-new
#  container with an empty log buffer, so there is no stale line to
#  false-positive on; no --since timestamp needed.
#
#  Live-checked timing: warm restart ~12 seconds after container start.
#  Cold start after a fresh rebuild can take longer (new binary, DBC/map
#  loads), so the timeout is generous: poll every 5 seconds for up to
#  10 minutes.
# ============================================================
wait_for_server() {
    print_step "Waiting for the worldserver to come back up with Wrath Unbound loaded..."

    local MARKER="[UNBOUND] Prereq map built."
    local MAX_ATTEMPTS=120
    local ATTEMPT=0

    while [ "$ATTEMPT" -lt "$MAX_ATTEMPTS" ]; do
        if docker logs ac-worldserver 2>&1 | grep -F "$MARKER" > /dev/null; then
            print_success "Wrath Unbound loaded cleanly — saw \"$MARKER\" in the worldserver log."
            echo ""
            return
        fi
        ATTEMPT=$((ATTEMPT + 1))
        sleep 5
    done

    print_warning "Didn't see \"$MARKER\" in the worldserver log within 10 minutes."
    echo "The server may still be starting up, or Wrath Unbound's Lua script may"
    echo "have hit an error on load. Check the live log to see what's happening:"
    echo -e "${CYAN}   docker logs -f ac-worldserver${NC}"
    echo ""
    echo "If the log shows nothing Lua/Eluna-related at all (not even an error),"
    echo "confirm Eluna actually compiled into this binary:"
    echo -e "${CYAN}   docker exec ac-worldserver strings /azerothcore/env/dist/bin/worldserver | grep -i ALE.Enabled${NC}"
    echo "If that returns nothing, mod-ale wasn't built in — re-run this installer"
    echo "(it will stage mod-ale and rebuild again)."
    echo ""
}

# ============================================================
#  guide_manual_steps()
# ============================================================
guide_manual_steps() {
    echo ""
    if [ "$EXISTING_INSTALL" = true ]; then
        print_step "One last check — the Mentor (entry 900001):"
        echo ""
        echo -e "  If your Mentor from a previous install is still standing, you're"
        echo -e "  all set — no need to spawn another. If it's gone, log in and run:"
        echo -e "  ${GREEN}.npc add 900001${NC}"
    else
        print_step "One last step — spawn the Mentor in-game:"
        echo ""
        echo -e "  Log in, walk to where you want The Mentor to stand, and run:"
        echo -e "  ${GREEN}.npc add 900001${NC}"
        echo ""
        echo -e "  The spawn is permanent — you only need to do this once."
    fi
    echo ""
    echo -e "${WHITE}Press ENTER when you're done and ready to see the summary...${NC}"
    read -r
}

# ============================================================
#  show_completion()
# ============================================================
show_completion() {
    print_header
    if [ "$EXISTING_INSTALL" = true ]; then
        echo -e "${GREEN}${BOLD}Wrath Unbound has been updated!${NC}"
        echo ""
        echo -e "${WHITE}Your existing Wrath Unbound install was refreshed — module files,${NC}"
        echo -e "${WHITE}SQL migrations, the core-engine patch, and the worldserver binary${NC}"
        echo -e "${WHITE}are all up to date. Players will pick up the new abilities the next${NC}"
        echo -e "${WHITE}time they log in — no character action needed.${NC}"
        echo ""
        echo -e "${WHITE}${BOLD}New in this update:${NC}"
        echo -e "   ${CYAN}•${NC} Unlocked classes can now train abilities directly from class"
        echo -e "     trainers, equip that class's gear, and accept that class's quests"
        echo -e "   ${CYAN}•${NC} Mentor: individual spells buy instantly with one click, plus a"
        echo -e "     \"Buy ALL available abilities\" button"
        echo -e "   ${CYAN}•${NC} Catalog fixes: corrected req_levels against real trainers, added"
        echo -e "     missing Mage teleports/portals + Paladin Summon Warhorse, and fixed"
        echo -e "     Paladin Judgement / Paladin+Warlock mounts / Druid Flight Form"
        echo -e "     purchases that previously took gold and granted nothing"
    else
        echo -e "${GREEN}${BOLD}Wrath Unbound is installed!${NC}"
        echo ""
        echo -e "${WHITE}Your WotLK Playerbots server now has the multi-class Wrath Unbound${NC}"
        echo -e "${WHITE}mod layered on top. Here's what changed:${NC}"
    fi
    echo ""
    echo -e "   ${CYAN}•${NC} The Mentor (entry 900001) is spawned and ready for players"
    echo -e "   ${CYAN}•${NC} Players unlock additional classes through the Mentor starting at level 5"
    echo -e "   ${CYAN}•${NC} Cross-class spells are purchased individually (instant-buy, or"
    echo -e "     \"Buy ALL\"), with rank prerequisites enforced"
    echo -e "   ${CYAN}•${NC} Unlocked classes can train from, equip, and quest as that class too"
    echo ""
    echo -e "${WHITE}${BOLD}A database backup was saved before any changes were made:${NC}"
    echo -e "   ${CYAN}$BACKUP_DIR${NC}"
    echo -e "${WHITE}Keep it somewhere safe in case you ever need to roll back.${NC}"
    echo ""
    echo -e "${WHITE}${BOLD}Worth knowing:${NC}"
    echo -e "   ${CYAN}•${NC} Cross-class spells currently land in the General tab of the"
    echo -e "     spellbook (and weapon/armor skills show oddly in the Skills panel)"
    echo -e "     until a future client-side fix ships — purely cosmetic, everything works"
    echo -e "   ${CYAN}•${NC} To update later, just re-run this installer — it detects the"
    echo -e "     existing install, refreshes everything, and won't duplicate anything"
    echo ""
    print_success "Have fun, and let your players loose on the Mentor!"
    echo ""
}

# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
print_header
echo -e "${WHITE}This adds the Wrath Unbound multi-class mod to your existing${NC}"
echo -e "${WHITE}Dad's MMO Lab WotLK Playerbots server.${NC}"
echo ""

if ! ask_yes_no "Ready to check compatibility?"; then
    echo "No problem — run this script when you're ready!"
    exit 0
fi

# Resolve SERVER_DIR — auto-detects renamed folders (e.g. wow-unbound).
# `docker compose` resolves its project from CWD, so we must cd into
# SERVER_DIR before any compose call.
detect_server_dir
cd "$SERVER_DIR" || exit 1

check_compatibility
check_existing_install
backup_database
stage_module_files
stage_mod_ale
apply_sql_migrations
apply_core_patches
patch_worldserver_conf
configure_ale
verify_ale_config
rebuild_server
wait_for_server
guide_manual_steps
show_completion
