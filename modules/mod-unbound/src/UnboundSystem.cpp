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
