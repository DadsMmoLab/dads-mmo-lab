#!/bin/bash
# ============================================================
#  Wrath Unbound Add-On Uninstaller
#  Dad's MMO Lab — github.com/DadsMmoLab
#
#  Removes all Wrath Unbound infrastructure from a
#  Dad's MMO Lab WotLK Playerbots install:
#    • Drops the three Unbound DB tables
#    • Clears all Unbound rows from shared tables
#    • Removes module files and the Mentor's Lua script
#    • Leaves env/dist/etc/modules/mod_ale.conf and modules/mod-ale/
#      (Eluna/ALE Lua engine) in place if present — these are shared
#      with any other ALE/Eluna Lua mods (e.g. via wow-manage.sh) and
#      are harmless without env/dist/etc/modules/lua_scripts/unbound_mentor.lua
#    • Reverts the core-engine cross-class access patch (v1.2.0+)
#    • Reverts any legacy docker-compose.override.yml entries (pre-1.2.2)
#    • Reverts worldserver.conf (ValidateSkillLearnedBySpells = 1)
#    • Rebuilds the worldserver without the C++ module
#
#  ValidateSkillLearnedBySpells = 1 (the AzerothCore default)
#  means cross-class spells in character_spell are stripped
#  automatically on each character's next login — no manual
#  character-data surgery needed.
#
#  Run from Desktop Mode Konsole:
#    chmod +x ~/Downloads/uninstall-wrath-unbound-addon.sh
#    ~/Downloads/uninstall-wrath-unbound-addon.sh
# ============================================================

set -euo pipefail

# ── Colors ──────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
BOLD='\033[1m'
NC='\033[0m'

IS_WSL2=false
grep -qi "microsoft\|wsl" /proc/version 2>/dev/null && IS_WSL2=true

# ── Helpers ─────────────────────────────────────────────────
print_header() {
    echo ""
    echo -e "${RED}${BOLD}╔══════════════════════════════════════════════════╗${NC}"
    echo -e "${RED}${BOLD}║      WRATH UNBOUND — UNINSTALLER                 ║${NC}"
    echo -e "${RED}${BOLD}║      Dad's MMO Lab                               ║${NC}"
    echo -e "${RED}${BOLD}╚══════════════════════════════════════════════════╝${NC}"
    echo ""
}

print_step()    { echo -e "${BLUE}▶ $1${NC}"; }
print_success() { echo -e "  ${GREEN}✅ $1${NC}"; }
print_warning() { echo -e "  ${YELLOW}⚠  $1${NC}"; }
print_error()   { echo -e "  ${RED}✗  $1${NC}"; }

ask_yes_no() {
    local prompt="$1"
    while true; do
        echo -ne "  ${WHITE}$prompt [y/n]: ${NC}"
        read -r answer
        case "$answer" in
            [Yy]*) return 0 ;;
            [Nn]*) return 1 ;;
            *) echo "  Please answer y or n." ;;
        esac
    done
}

# ── Globals ─────────────────────────────────────────────────
SERVER_DIR=""
BACKUP_DIR=""

# ============================================================
#  detect_server_dir()
#  Same auto-detect logic as the installer.
# ============================================================
detect_server_dir() {
    print_step "Locating your WotLK Playerbots server..."

    local DEFAULT_DIR="$HOME/wow-server-playerbots"
    local ALT_DIR="$HOME/wow-unbound"

    # Step 1: check default path
    if [ -f "$DEFAULT_DIR/docker-compose.yml" ] && [ -d "$DEFAULT_DIR/env/dist" ]; then
        SERVER_DIR="$DEFAULT_DIR"
        print_success "Server found at $SERVER_DIR"
        echo ""
        return
    fi

    # Step 2: check common alt name
    if [ -f "$ALT_DIR/docker-compose.yml" ] && [ -d "$ALT_DIR/env/dist" ]; then
        SERVER_DIR="$ALT_DIR"
        print_success "Server found at $SERVER_DIR"
        echo ""
        return
    fi

    # Step 3: shallow scan of $HOME (and /home/* on WSL2 root)
    local scan_roots=("$HOME")
    if [ "$EUID" -eq 0 ] && $IS_WSL2 && [ -d /home ]; then
        while IFS= read -r -d '' udir; do
            scan_roots+=("$udir")
        done < <(find /home -mindepth 1 -maxdepth 1 -type d -print0 2>/dev/null)
    fi

    local candidates=()
    for scan_root in "${scan_roots[@]}"; do
        while IFS= read -r -d '' dir; do
            if [ -f "$dir/docker-compose.yml" ] && [ -d "$dir/env/dist" ]; then
                candidates+=("$dir")
            fi
        done < <(find "$scan_root" -mindepth 1 -maxdepth 1 -type d -not -name ".*" -print0 2>/dev/null)
    done

    if [ "${#candidates[@]}" -eq 1 ]; then
        SERVER_DIR="${candidates[0]}"
        print_success "Server found at $SERVER_DIR"
        echo ""
        return
    elif [ "${#candidates[@]}" -gt 1 ]; then
        echo "  Multiple candidate server directories found:"
        for c in "${candidates[@]}"; do echo "    $c"; done
        echo ""
    fi

    # Step 4: ask the user
    echo "  Could not auto-detect your server folder."
    if $IS_WSL2; then
        echo -e "${YELLOW}WSL2 detected.${NC} Your server must be on the Linux filesystem."
        echo "Do NOT use a /mnt/c/ path — that's your Windows drive."
        echo -e "Your server is most likely at: ${CYAN}/home/$(whoami)/wow-server-playerbots${NC}"
        echo ""
    fi
    echo "  Enter the full path to your WotLK Playerbots server directory:"

    while true; do
        echo -ne "  ${WHITE}Server path: ${NC}"
        read -r input
        local expanded="${input/#\~/$HOME}"
        if [ -f "$expanded/docker-compose.yml" ] && [ -d "$expanded/env/dist" ]; then
            SERVER_DIR="$expanded"
            print_success "Server confirmed at $SERVER_DIR"
            echo ""
            return
        elif [ ! -d "$expanded" ]; then
            echo "  That directory doesn't exist."
            local parent
            parent="$(dirname "$expanded")"
            if [ -d "$parent" ]; then
                echo -e "  Folders found in ${CYAN}$parent${NC}:"
                ls -1 "$parent" 2>/dev/null | while read -r name; do echo "    $name"; done
            fi
        else
            echo "  That directory exists but has no docker-compose.yml — it may not be"
            echo "  a Dad's MMO Lab WotLK Playerbots install."
            echo -e "  Folders found in ${CYAN}$(dirname "$expanded")${NC}:"
            ls -1 "$(dirname "$expanded")" 2>/dev/null | while read -r name; do echo "    $name"; done
        fi
    done
}

# ============================================================
#  check_wrath_unbound_installed()
# ============================================================
check_wrath_unbound_installed() {
    print_step "Checking whether Wrath Unbound is installed..."

    local MODULE_DIR="$SERVER_DIR/modules/mod-unbound"
    local LUA_SCRIPT="$SERVER_DIR/env/dist/etc/modules/lua_scripts/unbound_mentor.lua"
    local LUA_SCRIPT_LEGACY="$SERVER_DIR/lua_scripts/unbound_mentor.lua"

    local found=false
    [ -d "$MODULE_DIR" ] && found=true
    [ -f "$LUA_SCRIPT" ] && found=true
    [ -f "$LUA_SCRIPT_LEGACY" ] && found=true

    if ! $found; then
        print_warning "Wrath Unbound does not appear to be installed on this server."
        echo "  Neither $MODULE_DIR nor unbound_mentor.lua (in env/dist/etc/modules/lua_scripts/"
        echo "  or the legacy lua_scripts/ location) was found."
        echo ""
        if ! ask_yes_no "Continue anyway? (useful if you need to clean up a partial install)"; then
            echo ""
            echo "  Nothing changed. Exiting."
            exit 0
        fi
    else
        print_success "Wrath Unbound files detected — ready to remove."
    fi
    echo ""
}

# ============================================================
#  warn_and_confirm()
# ============================================================
warn_and_confirm() {
    echo -e "${RED}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${RED}${BOLD}  THIS WILL PERMANENTLY REMOVE WRATH UNBOUND${NC}"
    echo -e "${RED}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "  The following will happen:"
    echo ""
    echo -e "  ${CYAN}Database (acore_world):${NC}"
    echo "    • Wrath Unbound tables dropped (milestones, spell catalog)"
    echo "    • Mentor NPC removed from world"
    echo "    • Mentor Stone item removed from creation table"
    echo "    • Universal skill entries removed"
    echo "    • Creation gift spells removed"
    echo ""
    echo -e "  ${CYAN}Database (acore_characters):${NC}"
    echo "    • unbound_character_unlocks table dropped"
    echo ""
    echo -e "  ${CYAN}Files:${NC}"
    echo "    • modules/mod-unbound/ removed"
    echo "    • env/dist/etc/modules/lua_scripts/unbound_mentor.lua removed"
    echo "    • env/dist/etc/modules/mod_ale.conf left in place if present —"
    echo "      shared with any other ALE/Eluna Lua mods (e.g. via wow-manage.sh)"
    echo "    • modules/mod-ale/ (Eluna engine) left in place if present —"
    echo "      not part of Wrath Unbound, may be used by other Lua mods"
    echo ""
    echo -e "  ${CYAN}Configuration:${NC}"
    echo "    • Any legacy lua_scripts volume mount / AC_ALE_SCRIPT_PATH env var"
    echo "      from older Wrath Unbound versions removed, if present"
    echo "    • ValidateSkillLearnedBySpells = 1 (AzerothCore default)"
    echo ""
    echo -e "  ${CYAN}Worldserver:${NC}"
    echo "    • Core-engine cross-class access patch reverted, if present (6 files)"
    echo "    • Rebuilt without the mod-unbound C++ module (~30–90 min)"
    echo ""
    echo -e "  ${YELLOW}Note on character data:${NC}"
    echo "  Cross-class spells already on characters stay in the database"
    echo "  but will be stripped automatically on each character's next login"
    echo "  (that's what ValidateSkillLearnedBySpells = 1 does). Any Mentor"
    echo "  Stones still in player bags become inert items — remove them"
    echo "  manually in-game with a GM command if desired."
    echo ""
    echo -e "  A database backup is taken ${BOLD}before${NC} any changes are made."
    echo ""

    if ! ask_yes_no "Are you sure you want to completely remove Wrath Unbound?"; then
        echo ""
        echo "  Uninstall cancelled. Nothing changed."
        exit 0
    fi
    echo ""
}

# ============================================================
#  backup_database()
# ============================================================
backup_database() {
    print_step "Backing up databases before making changes..."

    local TIMESTAMP
    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    BACKUP_DIR="$HOME/wrath-unbound-backups/uninstall_$TIMESTAMP"
    mkdir -p "$BACKUP_DIR"

    echo -e "  ${CYAN}Saving to: $BACKUP_DIR${NC}"
    echo ""

    echo -e "  ${CYAN}Backing up acore_world...${NC}"
    if ! docker exec ac-database mysqldump -u root -ppassword acore_world \
            > "$BACKUP_DIR/acore_world.sql" 2>/dev/null; then
        print_error "Could not back up acore_world. Is the ac-database container running?"
        echo "  Start your server first:"
        echo -e "  ${CYAN}  cd $SERVER_DIR && docker compose up -d${NC}"
        exit 1
    fi
    print_success "acore_world backed up."

    echo -e "  ${CYAN}Backing up acore_characters...${NC}"
    if ! docker exec ac-database mysqldump -u root -ppassword acore_characters \
            > "$BACKUP_DIR/acore_characters.sql" 2>/dev/null; then
        print_error "Could not back up acore_characters."
        exit 1
    fi
    print_success "acore_characters backed up."

    echo ""
    echo -e "  ${WHITE}Restore commands (if you ever need to roll back):${NC}"
    echo -e "  ${CYAN}  docker exec -i ac-database mysql -u root -ppassword acore_world < $BACKUP_DIR/acore_world.sql${NC}"
    echo -e "  ${CYAN}  docker exec -i ac-database mysql -u root -ppassword acore_characters < $BACKUP_DIR/acore_characters.sql${NC}"
    echo ""
}

# ============================================================
#  run_sql_world()   — helper to run a SQL string against acore_world
#  run_sql_chars()   — helper to run a SQL string against acore_characters
# ============================================================
run_sql_world() {
    local SQL="$1"
    local LABEL="$2"
    local ERRMSG exit_code=0
    # || exit_code=$? prevents set -e from aborting on SQL failure so we
    # can print the warning and keep cleaning up the remaining items.
    ERRMSG=$(docker exec ac-database mysql -u root -ppassword acore_world \
             -e "$SQL" 2>&1 >/dev/null) || exit_code=$?
    if [ "$exit_code" -ne 0 ]; then
        print_warning "SQL failed ($LABEL)"
        [ -n "$ERRMSG" ] && echo "   MySQL error: $ERRMSG"
    else
        print_success "$LABEL"
    fi
}

run_sql_chars() {
    local SQL="$1"
    local LABEL="$2"
    local ERRMSG exit_code=0
    ERRMSG=$(docker exec ac-database mysql -u root -ppassword acore_characters \
             -e "$SQL" 2>&1 >/dev/null) || exit_code=$?
    if [ "$exit_code" -ne 0 ]; then
        print_warning "SQL failed ($LABEL)"
        [ -n "$ERRMSG" ] && echo "   MySQL error: $ERRMSG"
    else
        print_success "$LABEL"
    fi
}

# ============================================================
#  revert_database()
#
#  Note: migrations 10-14 (catalog audit fixes, gap-fills, and the
#  mount/flight-form/Judgement "teach-spell" fixes) only ever touch
#  unbound_class_catalog and playercreateinfo_spell_custom — both already
#  fully wiped below (DROP TABLE / classmask-based DELETE), regardless of
#  which spell_id ended up in those rows. No extra cleanup needed for them.
# ============================================================
revert_database() {
    print_step "Reverting database changes..."
    echo ""

    # ── Spawned Mentor creatures (live world) ───────────────
    # creature_addon first (references creature.guid), then creature.
    run_sql_world \
        "DELETE FROM creature_addon WHERE guid IN (SELECT guid FROM creature WHERE id1 = 900001);" \
        "Removed creature_addon entries for Mentor (entry 900001)"
    run_sql_world \
        "DELETE FROM creature WHERE id1 = 900001;" \
        "Despawned all Mentor NPC instances"

    # ── Mentor NPC template ─────────────────────────────────
    run_sql_world \
        "DELETE FROM creature_template_model WHERE CreatureID = 900001;" \
        "Removed Mentor model (creature_template_model)"
    run_sql_world \
        "DELETE FROM creature_template WHERE entry = 900001;" \
        "Removed Mentor template (creature_template)"

    # ── Mentor Stone item ───────────────────────────────────
    run_sql_world \
        "DELETE FROM playercreateinfo_item WHERE itemid = 900100;" \
        "Removed Mentor Stone from character creation (playercreateinfo_item)"
    run_sql_world \
        "DELETE FROM item_template WHERE entry = 900100;" \
        "Removed Mentor Stone item template"

    # ── Creation gift spells ────────────────────────────────
    run_sql_world \
        "DELETE FROM playercreateinfo_spell_custom WHERE racemask = 0 AND classmask IN (1,2,4,8,16,64,128,256,1024);" \
        "Removed creation gift spells (playercreateinfo_spell_custom)"

    # ── Universal skill entries ─────────────────────────────
    run_sql_world \
        "DELETE FROM skillraceclassinfo_dbc WHERE ID >= 10000;" \
        "Removed universal skill access entries (skillraceclassinfo_dbc ID >= 10000)"

    # ── Unbound-specific tables ─────────────────────────────
    run_sql_world \
        "DROP TABLE IF EXISTS unbound_class_catalog;" \
        "Dropped unbound_class_catalog"
    run_sql_world \
        "DROP TABLE IF EXISTS unbound_milestones;" \
        "Dropped unbound_milestones"

    # ── Characters DB ───────────────────────────────────────
    run_sql_chars \
        "DROP TABLE IF EXISTS unbound_character_unlocks;" \
        "Dropped unbound_character_unlocks (acore_characters)"

    echo ""
}

# ============================================================
#  remove_files()
# ============================================================
remove_files() {
    print_step "Removing Wrath Unbound files..."
    echo ""

    local MODULE_DIR="$SERVER_DIR/modules/mod-unbound"
    local LUA_SCRIPT="$SERVER_DIR/env/dist/etc/modules/lua_scripts/unbound_mentor.lua"
    local LUA_SCRIPT_LEGACY="$SERVER_DIR/lua_scripts/unbound_mentor.lua"
    local ALE_CONF="$SERVER_DIR/env/dist/etc/modules/mod_ale.conf"

    if [ -d "$MODULE_DIR" ]; then
        rm -rf "$MODULE_DIR"
        print_success "Removed modules/mod-unbound/"
    else
        echo "  (modules/mod-unbound/ not found — already removed)"
    fi

    if [ -f "$LUA_SCRIPT" ]; then
        rm -f "$LUA_SCRIPT"
        print_success "Removed env/dist/etc/modules/lua_scripts/unbound_mentor.lua"
    else
        echo "  (env/dist/etc/modules/lua_scripts/unbound_mentor.lua not found — already removed)"
    fi

    if [ -f "$LUA_SCRIPT_LEGACY" ]; then
        rm -f "$LUA_SCRIPT_LEGACY"
        print_success "Removed legacy lua_scripts/unbound_mentor.lua (pre-1.2.2 location)"
    fi

    if [ -f "$ALE_CONF" ]; then
        echo "  (env/dist/etc/modules/mod_ale.conf left in place — shared with any"
        echo "   other ALE/Eluna Lua mods installed via wow-manage.sh or similar)"
    fi

    local MOD_ALE_DIR="$SERVER_DIR/modules/mod-ale"
    if [ -d "$MOD_ALE_DIR" ]; then
        echo "  (modules/mod-ale/ left in place — it's the Eluna/ALE Lua"
        echo "   engine, not part of Wrath Unbound itself. Harmless without"
        echo "   unbound_mentor.lua, and may be reused by other"
        echo "   Lua-based add-ons later.)"
    fi

    echo ""
}

# ============================================================
#  revert_core_patches()
#
#  v1.2.0 of the installer applies a small AzerothCore core-engine patch
#  (Player::m_unboundClassMask + the trainer/quest/item/condition checks
#  that consult it — 6 files) so Mentor-unlocked classes can train, equip,
#  and quest as that class. This reverts it via `git apply -R` so
#  $SERVER_DIR/src/ goes back to stock AzerothCore.
#
#  Skips cleanly if the patch was never applied (installs from before
#  v1.2.0, or a partial/manual install) — checked via GetUnboundClassMask
#  in Player.h.
# ============================================================
revert_core_patches() {
    print_step "Reverting Wrath Unbound core-engine patch (cross-class access)..."
    echo ""

    local PLAYER_H="$SERVER_DIR/src/server/game/Entities/Player/Player.h"

    if [ ! -f "$PLAYER_H" ] || ! grep -q "GetUnboundClassMask" "$PLAYER_H"; then
        echo "  (core-engine patch not present — nothing to revert)"
        echo ""
        return
    fi

    local PATCH_FILE
    PATCH_FILE=$(mktemp)
    cat > "$PATCH_FILE" <<'WU_UNINSTALL_CORE_PATCH'
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
WU_UNINSTALL_CORE_PATCH

    cd "$SERVER_DIR" || exit 1

    if ! git apply -R --check "$PATCH_FILE" 2>/dev/null; then
        print_warning "Could not cleanly revert the core-engine patch — your src/ tree"
        print_warning "has diverged from what Wrath Unbound applied. Leaving it in place;"
        print_warning "it's inert without mod-unbound (Player::m_unboundClassMask just"
        print_warning "stays 0) but won't be removed automatically."
        rm -f "$PATCH_FILE"
        echo ""
        return
    fi

    git apply -R "$PATCH_FILE"
    rm -f "$PATCH_FILE"
    print_success "Core-engine cross-class access patch reverted (6 files)."
    echo ""
}

# ============================================================
#  revert_compose_override()
# ============================================================
# Legacy cleanup only: installer versions before 1.2.2 added a custom
# lua_scripts bind mount + AC_ALE_SCRIPT_PATH env var to
# docker-compose.override.yml. 1.2.2+ installs never write these (ALE is
# configured via env/dist/etc/modules/mod_ale.conf instead), so on a
# fresh-convention install both greps below simply find nothing.
revert_compose_override() {
    print_step "Reverting docker-compose.override.yml..."
    echo ""

    local OVERRIDE="$SERVER_DIR/docker-compose.override.yml"

    if [ ! -f "$OVERRIDE" ]; then
        echo "  (docker-compose.override.yml not found — nothing to revert)"
        echo ""
        return
    fi

    # Remove lua_scripts bind mount line (pre-1.2.2 convention)
    if grep -q '\./lua_scripts:/azerothcore/env/dist/bin/lua_scripts' "$OVERRIDE"; then
        sed -i '/\.\/lua_scripts:\/azerothcore\/env\/dist\/bin\/lua_scripts/d' "$OVERRIDE"
        print_success "Removed lua_scripts volume mount"
    else
        echo "  (lua_scripts volume mount not found — already removed)"
    fi

    # Remove AC_ALE_SCRIPT_PATH env var line
    if grep -q "AC_ALE_SCRIPT_PATH" "$OVERRIDE"; then
        sed -i '/AC_ALE_SCRIPT_PATH/d' "$OVERRIDE"
        print_success "Removed AC_ALE_SCRIPT_PATH env var"
    else
        echo "  (AC_ALE_SCRIPT_PATH not found — already removed)"
    fi

    echo ""
}

# ============================================================
#  revert_worldserver_conf()
# ============================================================
revert_worldserver_conf() {
    print_step "Reverting worldserver.conf..."
    echo ""

    local CONF="$SERVER_DIR/env/dist/etc/worldserver.conf"

    if [ ! -f "$CONF" ]; then
        print_warning "worldserver.conf not found at $CONF — skipping."
        echo ""
        return
    fi

    if grep -q "^ValidateSkillLearnedBySpells" "$CONF"; then
        sed -i "s/^ValidateSkillLearnedBySpells *= *[0-9]*/ValidateSkillLearnedBySpells = 1/" "$CONF"
        print_success "ValidateSkillLearnedBySpells = 1 (AzerothCore default restored)"
    else
        echo "ValidateSkillLearnedBySpells = 1" >> "$CONF"
        print_success "ValidateSkillLearnedBySpells = 1 appended to worldserver.conf"
    fi

    echo ""
}

# ============================================================
#  rebuild_server()
# ============================================================
rebuild_server() {
    print_step "Rebuilding the worldserver without Wrath Unbound..."
    echo ""
    echo "  Docker reuses your existing compiled layers and only removes"
    echo "  the mod-unbound module — much faster than the initial build,"
    echo "  but still expect 30–90 minutes on a Steam Deck."
    echo ""
    print_warning "Keep your Steam Deck plugged in and awake during this step."
    echo ""

    if ! ask_yes_no "Ready to rebuild the worldserver now?"; then
        echo ""
        echo "  Database and files have been cleaned up. When you're ready"
        echo "  to rebuild the binary, run manually:"
        echo -e "  ${CYAN}  cd $SERVER_DIR${NC}"
        echo -e "  ${CYAN}  docker compose build ac-worldserver${NC}"
        echo -e "  ${CYAN}  docker compose up -d --force-recreate ac-worldserver${NC}"
        exit 0
    fi

    local LOGFILE="$HOME/wrath-unbound-uninstall-rebuild.log"
    echo -e "  ${CYAN}Progress saved to: $LOGFILE${NC}"
    echo -e "  ${CYAN}Go grab a coffee — this will take a while.${NC}"
    echo ""

    cd "$SERVER_DIR" || exit 1

    docker compose build ac-worldserver 2>&1 | tee "$LOGFILE"
    if [ "${PIPESTATUS[0]}" -ne 0 ]; then
        print_warning "Rebuild failed — check $LOGFILE for details."
        echo "  Your database backup is at: $BACKUP_DIR"
        exit 1
    fi

    docker compose up -d --force-recreate ac-worldserver 2>&1 | tee -a "$LOGFILE"
    if [ "${PIPESTATUS[0]}" -ne 0 ]; then
        print_warning "Container restart failed — check $LOGFILE for details."
        exit 1
    fi

    print_success "Worldserver rebuilt and restarted without Wrath Unbound."
    echo ""
}

# ============================================================
#  show_completion()
# ============================================================
show_completion() {
    print_header
    echo -e "${GREEN}${BOLD}Wrath Unbound has been removed.${NC}"
    echo ""
    echo -e "${WHITE}What was cleaned up:${NC}"
    echo -e "   ${CYAN}•${NC} Unbound DB tables dropped (milestones, spell catalog, character unlocks)"
    echo -e "   ${CYAN}•${NC} Mentor NPC removed from world and template tables"
    echo -e "   ${CYAN}•${NC} Mentor Stone removed from item table and character creation"
    echo -e "   ${CYAN}•${NC} Universal skill entries removed"
    echo -e "   ${CYAN}•${NC} Creation gift spells removed"
    echo -e "   ${CYAN}•${NC} Module files and the Mentor's Lua script deleted"
    echo -e "   ${CYAN}•${NC} Core-engine cross-class access patch reverted (if it was present)"
    if [ -f "$SERVER_DIR/env/dist/etc/modules/mod_ale.conf" ]; then
        echo -e "   ${CYAN}•${NC} mod_ale.conf left in place — shared with other ALE/Eluna Lua mods"
    fi
    if [ -d "$SERVER_DIR/modules/mod-ale" ]; then
        echo -e "   ${CYAN}•${NC} modules/mod-ale/ (Eluna Lua engine) left in place — harmless, reusable later"
    fi
    echo -e "   ${CYAN}•${NC} Any legacy docker-compose.override.yml entries reverted (pre-1.2.2)"
    echo -e "   ${CYAN}•${NC} ValidateSkillLearnedBySpells = 1 (AzerothCore default)"
    echo -e "   ${CYAN}•${NC} Worldserver rebuilt without the C++ module"
    echo ""
    echo -e "${WHITE}Character data note:${NC}"
    echo "  Any cross-class spells on existing characters will be stripped"
    echo "  automatically the next time each character logs in."
    echo ""
    echo "  Mentor Stones still in player bags have no item template now."
    echo "  Players can destroy them normally (right-click → Destroy)."
    echo "  To clean them up in bulk via SQL before anyone logs in:"
    echo -e "  ${CYAN}  docker exec ac-database mysql -u root -ppassword acore_characters -e \\${NC}"
    echo -e "  ${CYAN}    \"DELETE ci FROM character_inventory ci JOIN item_instance ii ON ci.item=ii.guid WHERE ii.itemEntry=900100; DELETE FROM item_instance WHERE itemEntry=900100;\"${NC}"
    echo ""
    echo -e "${WHITE}Your pre-uninstall backup:${NC}"
    echo -e "   ${CYAN}$BACKUP_DIR${NC}"
    echo ""
    echo -e "${BLUE}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${WHITE}  Re-install any time: ~/Downloads/install-wrath-unbound-addon.sh${NC}"
    echo -e "${BLUE}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
}

# ============================================================
#  MAIN
# ============================================================
print_header

detect_server_dir
check_wrath_unbound_installed
warn_and_confirm
backup_database
revert_database
remove_files
revert_core_patches
revert_compose_override
revert_worldserver_conf
rebuild_server
show_completion
