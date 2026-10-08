#include "Player.h"
#include "ScriptMgr.h"
#include "ScriptDefines/PlayerScript.h"
#include "DatabaseEnv.h"
#include "Entities/Item/ItemTemplate.h"
#include "Log.h"
#include "ScriptDefines/WorldScript.h"

#include <array>
#include <string>
#include <vector>

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
// OnPlayerDeleteFromDB + UnboundOrphanSweepWorldScript (character-delete cleanup):
//   Unbound's per-character rows are keyed by the character's GUID counter, and
//   AzerothCore's own character delete does not know them. After a restart the
//   core hands the next new character MAX(characters.guid)+1, which is the
//   deleted character's GUID when it was the newest, so before this cleanup a
//   new level-1 character inherited the deleted one's unlocked classes.
//
// Everything else lives in env/dist/etc/modules/lua_scripts/unbound_mentor.lua.

namespace
{
struct GuidKeyedTable
{
    char const* table;
    char const* guidColumn;
};

// Every characters-database table that Unbound, or a script shipped beside it,
// keys by character GUID. Each is checked for before it is touched:
//   unbound_character_unlocks  data/sql/db-characters/01_unbound_characters.sql
//   unbound_character_talents  made by v1.4.0's Lua talent bridge, which this
//                              branch no longer ships; a server that ran it
//                              still has the table
//   dml_autobuff_kv            data/sql/db-characters/02_dml_autobuff_kv.sql
//                              (#buffs, lua_scripts/dml_autobuff.lua)
constexpr std::array<GuidKeyedTable, 3> UnboundGuidKeyedTables = {{
    { "unbound_character_unlocks", "char_guid" },
    { "unbound_character_talents", "char_guid" },
    { "dml_autobuff_kv",           "guid"      },
}};

// True when the characters database has this table with this column.
// Every statement below is guarded by it: AzerothCore stops the worldserver
// on a query that names a missing table or column (ER_NO_SUCH_TABLE and
// ER_BAD_FIELD_ERROR abort in MySQLConnection::_HandleMySQLErrno), and inside
// the character-delete transaction one failed statement would also roll back
// the whole delete. information_schema answers "no row" instead of failing.
bool CharacterTableHasColumn(char const* table, char const* column)
{
    return CharacterDatabase.Query(
        "SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME = '{}' AND COLUMN_NAME = '{}'", table, column) != nullptr;
}

// The tables above that this server has, probed once per worldserver process
// (thread-safe static initialisation) on first use, which may be the first
// character delete: Player::DeleteOldCharacters runs at start-up before the
// sweep, and a mass bot reset deletes many characters in a row. A table made
// while the server runs is picked up at the next start, whose sweep removes
// any rows the delete hook missed in between.
std::vector<GuidKeyedTable> const& PresentGuidKeyedTables()
{
    static std::vector<GuidKeyedTable> const present = []
    {
        std::vector<GuidKeyedTable> found;
        for (GuidKeyedTable const& keyed : UnboundGuidKeyedTables)
            if (CharacterTableHasColumn(keyed.table, keyed.guidColumn))
                found.push_back(keyed);

        std::string names;
        for (GuidKeyedTable const& keyed : found)
            names += (names.empty() ? "" : ", ") + std::string(keyed.table);
        LOG_INFO("module", "[UNBOUND] Character cleanup covers: {}.", names.empty() ? "no tables" : names);
        return found;
    }();
    return present;
}
}

class UnboundPlayerScript : public PlayerScript
{
public:
    UnboundPlayerScript() : PlayerScript("UnboundPlayerScript",
    {
        PLAYERHOOK_ON_PLAYER_HAS_ACTIVE_POWER_TYPE,
        PLAYERHOOK_ON_LOGIN,
        PLAYERHOOK_ON_AFTER_UPDATE_MAX_POWER,
        PLAYERHOOK_ON_DELETE_FROM_DB,
        PLAYERHOOK_ON_PLAYER_IS_CLASS
    }) {}

    // Rogue and hunter abilities for a character that added the class.
    // The core asks Player::IsClass(CLASS_ROGUE, CLASS_CONTEXT_ABILITY) before
    // it hands out pickpocket loot (LootHandler.cpp, three sites: "You do not
    // have permission to loot that corpse" otherwise), and
    // IsClass(CLASS_HUNTER, CLASS_CONTEXT_PET) before Tame Beast tames
    // (Spell::EffectTameCreature returns silently otherwise) and before a
    // stable master offers the stable (PlayerGossip.cpp).
    // Keyed on the spell, not on the Unbound class mask: the mask is built at
    // login, so it is stale after an unlock until the next login, while the
    // spell is there the moment it is learned. Only "true" is ever answered;
    // every other question falls through to the native class.
    // mod-multiclass-summons answers the hunter PET question for Call Pet (883)
    // the same way; this adds Tame Beast (1515) for a character without it.
    Optional<bool> OnPlayerIsClass(Player const* player, Classes playerClass, ClassContext context) override
    {
        constexpr uint32 SPELL_PICK_POCKET = 921;
        constexpr uint32 SPELL_TAME_BEAST  = 1515;

        if (playerClass == CLASS_ROGUE && context == CLASS_CONTEXT_ABILITY && player->HasSpell(SPELL_PICK_POCKET))
            return true;
        if (playerClass == CLASS_HUNTER && context == CLASS_CONTEXT_PET && player->HasSpell(SPELL_TAME_BEAST))
            return true;
        return std::nullopt;
    }

    // Called only when a character is removed for good (Player::DeleteFromDB,
    // CHAR_DELETE_REMOVE, after the core's own deletes and before the commit).
    // A soft delete (CharDeleteMethod = 1) keeps the GUID and does not get
    // here, so a restorable character keeps its unlocks; its final removal
    // later (DeleteOldCharacters) does. Bots come through here too, harmlessly.
    void OnPlayerDeleteFromDB(CharacterDatabaseTransaction trans, uint32 guid) override
    {
        for (GuidKeyedTable const& keyed : PresentGuidKeyedTables())
            trans->Append("DELETE FROM `{}` WHERE `{}` = {}", keyed.table, keyed.guidColumn, guid);
    }

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

// Once per worldserver start, before the world opens to players: remove the
// rows of characters that no longer exist. It covers every delete the hook
// above never saw: deletes made before this cleanup existed, and characters
// removed while this module was not loaded or outside the worldserver.
//
// It deletes nothing unless the characters table is there, answers and has at
// least one character (an empty table more likely means one being reloaded
// than a realm whose every character was deleted, and the sweep would then
// take every Unbound row), and each DELETE joins against that same table, so a row whose GUID belongs to a
// character that exists (a bot, or a soft-deleted character that can still be
// restored) is never touched.
class UnboundOrphanSweepWorldScript : public WorldScript
{
public:
    UnboundOrphanSweepWorldScript() : WorldScript("UnboundOrphanSweepWorldScript",
    {
        WORLDHOOK_ON_BEFORE_WORLD_INITIALIZED
    }) {}

    void OnBeforeWorldInitialized() override
    {
        if (!CharacterTableHasColumn("characters", "guid"))
        {
            LOG_WARN("module", "[UNBOUND] Orphan sweep skipped: the characters database has no characters.guid here.");
            return;
        }

        QueryResult characters = CharacterDatabase.Query("SELECT COUNT(*) FROM characters");
        if (!characters)
        {
            LOG_WARN("module", "[UNBOUND] Orphan sweep skipped: the characters table could not be read.");
            return;
        }

        uint64 const characterCount = characters->Fetch()[0].Get<uint64>();
        if (!characterCount)
        {
            LOG_WARN("module", "[UNBOUND] Orphan sweep skipped: the characters table is empty, so every Unbound row "
                "would count as an orphan. Nothing was removed.");
            return;
        }

        uint64 removedTotal = 0;
        for (GuidKeyedTable const& keyed : PresentGuidKeyedTables())
        {
            QueryResult orphans = CharacterDatabase.Query(
                "SELECT COUNT(*) FROM `{0}` k LEFT JOIN characters c ON c.guid = k.`{1}` WHERE c.guid IS NULL",
                keyed.table, keyed.guidColumn);
            if (!orphans)
            {
                LOG_WARN("module", "[UNBOUND] Orphan sweep skipped {}: it could not be read.", keyed.table);
                continue;
            }

            uint64 const count = orphans->Fetch()[0].Get<uint64>();
            if (!count)
                continue;

            CharacterDatabase.DirectExecute(
                "DELETE k FROM `{0}` k LEFT JOIN characters c ON c.guid = k.`{1}` WHERE c.guid IS NULL",
                keyed.table, keyed.guidColumn);
            LOG_INFO("module", "[UNBOUND] Orphan sweep: removed {} row(s) of deleted characters from {}.", count, keyed.table);
            removedTotal += count;
        }

        LOG_INFO("module", "[UNBOUND] Orphan sweep done: {} row(s) removed, {} character(s) on this realm.",
            removedTotal, characterCount);
    }
};

void AddUnboundScripts()
{
    new UnboundPlayerScript();
    new UnboundOrphanSweepWorldScript();
}
// cache-bust: 1781408710
