#include "Config.h"
#include "Log.h"
#include "ScriptMgr.h"
#include "SpellInfo.h"
#include "SpellMgr.h"
#include <unordered_set>

// UnboundReagentFree — strip CASTING reagents server-wide (user-approved scope
// 2026-08-15): soul shards, candles, powders, symbols, seeds, runes, ankh,
// corpse dust, infernal/demonic stones. Profession crafting materials are NOT
// reagents on crafting spells (those are itemized via the trade-skill system's
// spell reagents too — which is exactly why this list is a curated ITEM set and
// not a blanket wipe: only spells whose reagent is one of these consumables
// are touched, so blacksmithing mats etc. stay required).
//
// Runs at world startup, after the spell store is loaded (same pattern as
// mod-multiclass-summons' attribute fix-up). This is what lets warlock demons
// chain-summon shard-free through mod-multiclass-summons.
//
// Both behaviours are server house rules, so each is behind its own switch in
// mod_unbound.conf and both are OFF unless the conf turns them on (a missing
// conf also means off):
//   Unbound.ReagentFree   = 0   free casting reagents
//   Unbound.InstantSummons = 0  instant, free, cooldown-less class summons
// The settings are read once, at startup.

namespace
{
    std::unordered_set<int32> const kCastingReagents = {
        6265,  // Soul Shard
        5565,  // Infernal Stone
        16583, // Demonic Figurine
        17020, // Arcane Powder
        17021, // Wild Berries
        17026, // Wild Thornroot
        22148, // Wild Quillvine
        17028, // Holy Candle
        17029, // Sacred Candle
        17030, // Ankh
        17031, // Rune of Teleportation
        17032, // Rune of Portals
        17033, // Symbol of Divinity
        21177, // Symbol of Kings
        17034, // Maple Seed
        17035, // Stranglethorn Seed
        17036, // Ashwood Seed
        17037, // Hornbeam Seed
        17038, // Ironwood Seed
        22147, // Flintweed Seed
        37201, // Corpse Dust
        17057, // Shiny Fish Scales
        17058, // Fish Oil
        44605, // Wild Spineleaf   (WotLK Gift of the Wild top rank)
        44614, // Starleaf Seed    (WotLK Rebirth top rank)
        44615, // Devout Candle    (WotLK Prayer of Fortitude top rank)
    };
}

class UnboundReagentFree : public WorldScript
{
public:
    UnboundReagentFree() : WorldScript("UnboundReagentFree") { }

    void OnStartup() override
    {
        if (sConfigMgr->GetOption<bool>("Unbound.ReagentFree", false))
            StripCastingReagents();
        else
            LOG_INFO("module", "[UNBOUND] free reagents: off");

        if (sConfigMgr->GetOption<bool>("Unbound.InstantSummons", false))
            MakeSummonsInstant();
        else
            LOG_INFO("module", "[UNBOUND] instant summons: off");
    }

private:
    static void StripCastingReagents()
    {
        uint32 changed = 0;
        for (uint32 id = 0; id < sSpellMgr->GetSpellInfoStoreSize(); ++id)
        {
            SpellInfo const* info = sSpellMgr->GetSpellInfo(id);
            if (!info)
                continue;

            bool touched = false;
            SpellInfo* mut = const_cast<SpellInfo*>(info);
            for (uint8 i = 0; i < MAX_SPELL_REAGENTS; ++i)
            {
                if (mut->Reagent[i] > 0 && kCastingReagents.count(mut->Reagent[i]))
                {
                    mut->Reagent[i] = 0;
                    mut->ReagentCount[i] = 0;
                    touched = true;
                }
            }

            if (touched)
                ++changed;
        }

        LOG_INFO("module", "[UNBOUND] free reagents: on (stripped casting reagents from {} spells)", changed);
    }

    static void MakeSummonsInstant()
    {
        // Pet/summon QoL (user request 2026-08-17): every class summon casts
        // instantly, costs nothing and has no cooldown, so one macro can chain
        // the whole menagerie. GCD is zeroed server-side too; the client may
        // still enforce its own GCD locally, in which case the macro is spammed
        // once per summon instead of firing in a single press.
        static constexpr uint32 kInstantSummons[] = {
            688,   // Summon Imp
            697,   // Summon Voidwalker
            712,   // Summon Succubus
            691,   // Summon Felhunter
            30146, // Summon Felguard
            1122,  // Inferno
            31687, // Summon Water Elemental
            70907, // Summon Water Elemental (Temp, module path)
            70908, // Summon Water Elemental (Perm, module path)
            46584, // Raise Dead
            52150, // Raise Dead (Perm Ghoul, module path)
            51533, // Feral Spirit
        };

        uint32 summons = 0;
        for (uint32 id : kInstantSummons)
        {
            if (SpellInfo const* info = sSpellMgr->GetSpellInfo(id))
            {
                SpellInfo* mut = const_cast<SpellInfo*>(info);
                mut->CastTimeEntry = nullptr; // CalcCastTime -> 0
                mut->ManaCost = 0;
                mut->ManaCostPercentage = 0;
                mut->ManaCostPerlevel = 0;
                mut->RecoveryTime = 0;
                mut->CategoryRecoveryTime = 0;
                mut->StartRecoveryTime = 0;
                mut->StartRecoveryCategory = 0;
                ++summons;
            }
        }

        LOG_INFO("module", "[UNBOUND] instant summons: on ({} summon spells now instant, free and cooldown-less)", summons);
    }
};

void AddUnboundReagentFree()
{
    new UnboundReagentFree();
}
