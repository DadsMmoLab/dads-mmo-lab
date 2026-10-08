#include "ScriptMgr.h"
#include "Player.h"
#include "Pet.h"
#include "ObjectAccessor.h"
#include "ObjectMgr.h"
#include "SpellScript.h"
#include "SpellInfo.h"
#include "SpellMgr.h"
#include "CharmInfo.h"
#include "DBCStores.h"
#include "Map.h"
#include "TemporarySummon.h"
#include "WorldSession.h"
#include <algorithm>
#include <map>
#include <numbers>
#include <unordered_map>
#include <vector>

// mod-multiclass-summons — module-managed multi-summon system (pure guardian model).
//
// Every target summon (warlock demons, mage Water Elemental, DK ghoul) is created as a
// controllable GUARDIAN that the module fully owns — never a real Pet, so nothing is
// written to character_pet and the core's single-class pet checks / mount stash logic
// are bypassed entirely (this avoids the real-pet mount/dismount crashes).
//
//   Primary   (cast while the pet slot is free): Category PET guardian -> claims the pet
//             slot, so the client shows the pet action bar/frame and the player controls it.
//   Secondary (slot already held): Category ALLY guardian -> side minion, no bar, follows,
//             joins combat, auto-casts its attack.
//
// Guardians normally only know their creature-template spells, so we inject each summon's
// real pet ability set (correct spell IDs + level-appropriate ranks, sourced from the
// same PetLevelupSpell/PetDefaultSpells data the pet system uses) and default them to
// REACT_DEFENSIVE. Summons are session-only. Playerbots are skipped (stock single pet).

namespace
{
    // Summon spells this module intercepts. Keep in sync with
    // data/sql/db-world/base/multiclass_summons.sql.
    bool IsMulticlassSummonSpell(uint32 spellId)
    {
        switch (spellId)
        {
            case 688:   // Summon Imp
            case 697:   // Summon Voidwalker
            case 712:   // Summon Succubus
            case 691:   // Summon Felhunter
            case 30146: // Summon Felguard
            case 70907: // Summon Water Elemental (Temp)
            case 70908: // Summon Water Elemental (Perm)
            case 46584: // Raise Dead (Temp Ghoul)
            case 52150: // Raise Dead (Perm Ghoul)
                return true;
            default:
                return false;
        }
    }

    template <typename T>
    bool IsPlayerBotHelper(T const* player)
    {
        auto* session = player->GetSession();
        if (!session)
            return false;

        if constexpr (requires { session->IsBot(); })
        {
            return session->IsBot();
        }
        else
        {
            return false;
        }
    }

    bool IsPlayerBot(Player const* player)
    {
        return IsPlayerBotHelper(player);
    }

    SummonPropertiesEntry MakeProps(uint32 category, uint32 type)
    {
        SummonPropertiesEntry props{};
        props.Id = 67;
        props.Category = category;
        props.Faction = 0;
        props.Type = type;
        props.Slot = 0;
        props.Flags = 0;
        return props;
    }

    // A follow angle (relative to the owner's facing) that spreads summons evenly around
    // the owner instead of stacking them all on the default PET_FOLLOW_ANGLE.
    float FollowAngleForIndex(std::size_t index)
    {
        constexpr float step = 2.0f * std::numbers::pi_v<float> / 8.0f;
        return PET_FOLLOW_ANGLE + step * float(index % 8);
    }

    // Give a guardian the ability set the matching pet would have at the owner's level,
    // by writing the spell ids into the creature's spell slots (read by
    // CharmInfo::InitCharmCreateSpells, which runs later in Guardian::InitStats to build
    // the action bar + autocast). Active abilities go first so they take the bar's
    // castable slots; passives follow and are cast on init. Sourced from the same
    // PetLevelupSpell (per family) + PetDefaultSpells data the real pet system uses, so
    // ids and ranks are correct without hardcoding. No-op if no pet data exists for the
    // creature (leaves its template spells intact).
    void ApplyPetAbilities(Creature* guardian, uint8 level)
    {
        CreatureTemplate const* cinfo = guardian->GetCreatureTemplate();
        if (!cinfo)
            return;

        // first-rank-in-chain -> { highest required level <= owner level, that rank's id }
        std::map<uint32, std::pair<uint32, uint32>> best;

        auto consider = [&](uint32 spellId, uint32 reqLevel)
        {
            if (!spellId || reqLevel > level || !sSpellMgr->GetSpellInfo(spellId))
                return;

            uint32 const first = sSpellMgr->GetFirstSpellInChain(spellId);
            auto itr = best.find(first);
            if (itr == best.end() || reqLevel >= itr->second.first)
                best[first] = { reqLevel, spellId };
        };

        // 1) Keep the creature's own template spells (e.g. Water Elemental's Waterbolt +
        //    Freeze, Ghoul's Claw/Gnaw/Leap/Huddle). reqLevel 0 = always eligible; a
        //    proper pet-data rank below overrides them for shared spell chains.
        for (uint8 i = 0; i < MAX_CREATURE_SPELLS; ++i)
            consider(guardian->m_spells[i], 0);

        // 2) Add the matching pet's family level-up spells (correct rank for this level)
        //    — this is what fills in the warlock-demon kits the template doesn't carry.
        if (cinfo->family)
            if (PetLevelupSpellSet const* levelup = sSpellMgr->GetPetLevelupSpellList(cinfo->family))
                for (auto const& entry : *levelup)
                    consider(entry.second, entry.first);

        int32 const petSpellsId = cinfo->PetSpellDataId ? -(int32)cinfo->PetSpellDataId : (int32)guardian->GetEntry();
        if (PetDefaultSpellsEntry const* def = sSpellMgr->GetPetDefaultSpellsEntry(petSpellsId))
            for (uint32 spellId : def->spellid)
                if (SpellInfo const* info = sSpellMgr->GetSpellInfo(spellId))
                    consider(spellId, info->SpellLevel);

        if (best.empty())
            return;

        uint8 slot = 0;
        for (auto const& kv : best) // active abilities first
        {
            if (slot >= MAX_SPELL_CHARM)
                break;
            SpellInfo const* info = sSpellMgr->GetSpellInfo(kv.second.second);
            if (info && !info->IsPassive())
                guardian->m_spells[slot++] = kv.second.second;
        }
        for (auto const& kv : best) // then passives
        {
            if (slot >= MAX_SPELL_CHARM)
                break;
            SpellInfo const* info = sSpellMgr->GetSpellInfo(kv.second.second);
            if (info && info->IsPassive())
                guardian->m_spells[slot++] = kv.second.second;
        }
        for (; slot < MAX_SPELL_CHARM; ++slot)
            guardian->m_spells[slot] = 0;
    }

    struct ActiveSummon
    {
        ObjectGuid guid;
        uint32 entry;
        uint32 spellId;
        int32 duration;
        bool primary;
    };

    struct PlayerSummons
    {
        std::vector<ActiveSummon> list;
        uint32 reconcileTimer = 0;
    };

    // Owns every module summon for every (non-bot) player. World-thread only, so a plain
    // map needs no locking.
    class SummonManager
    {
    public:
        static SummonManager& Instance()
        {
            static SummonManager instance;
            return instance;
        }

        // A target summon spell was cast: create it as primary (pet slot free) or as a
        // side guardian, one active summon per creature entry.
        void HandleCast(Player* owner, uint32 spellId, uint32 entry, int32 duration)
        {
            // Re-casting the entry that already holds the pet slot: leave it in place
            // (no duplicate). Covers recasting your current primary.
            if (Creature* prim = ObjectAccessor::GetCreatureOrPetOrVehicle(*owner, owner->GetPetGUID()))
                if (prim->GetEntry() == entry)
                    return;

            PlayerSummons& ps = _players[owner->GetGUID()];
            RemoveEntry(owner, ps, entry);

            bool const primary = owner->GetPetGUID().IsEmpty();
            float const followAngle = FollowAngleForIndex(ps.list.size());

            if (TempSummon* summon = CreateGuardian(owner, entry, spellId, duration, primary, followAngle))
            {
                ps.list.push_back({ summon->GetGUID(), entry, spellId, duration, primary });
                LOG_INFO("module.multiclass_pet_fix", "Summon: {} entry {} (spell {}) as {} for {}",
                    summon->GetGUID().ToString(), entry, spellId, primary ? "PRIMARY" : "guardian", owner->GetName());
            }
            else if (ps.list.empty())
                _players.erase(owner->GetGUID());
        }

        // Throttled per-player reconcile: prune dead summons and promote a new primary if
        // the slot frees up.
        void Update(Player* owner, uint32 diff)
        {
            auto it = _players.find(owner->GetGUID());
            if (it == _players.end())
                return;

            PlayerSummons& ps = it->second;
            ps.reconcileTimer += diff;
            if (ps.reconcileTimer < RECONCILE_INTERVAL)
                return;
            ps.reconcileTimer = 0;

            Reconcile(owner, ps);

            if (ps.list.empty())
                _players.erase(it);
        }

        // A REAL pet (hunter Call Pet, stable retrieval, dismount re-summon) is about to
        // claim the pet slot: if one of OUR guardians holds it, demote it to a side
        // guardian first. Without this the core's SetMinion conflict path just
        // UnSummon()s the guardian — the demon is lost instead of stepping aside. Runs
        // synchronously inside Pet::LoadPetFromDB (hook fires before SetMinion), so the
        // 1s Reconcile tick can never re-promote in the gap.
        void DemotePrimary(Player* owner)
        {
            ObjectGuid const petGuid = owner->GetPetGUID();
            if (petGuid.IsEmpty() || petGuid.IsPet())
                return; // slot empty, or held by a real Pet — nothing of ours to move

            auto it = _players.find(owner->GetGUID());
            if (it == _players.end())
                return;

            PlayerSummons& ps = it->second;
            for (auto itr = ps.list.begin(); itr != ps.list.end(); ++itr)
            {
                if (itr->guid != petGuid)
                    continue;

                ActiveSummon const demote = *itr;
                Unsummon(owner, demote.guid);
                ps.list.erase(itr);

                float const followAngle = FollowAngleForIndex(ps.list.size());
                if (TempSummon* summon = CreateGuardian(owner, demote.entry, demote.spellId, demote.duration,
                    false, followAngle))
                {
                    ps.list.push_back({ summon->GetGUID(), demote.entry, demote.spellId, demote.duration, false });
                    LOG_INFO("module.multiclass_pet_fix", "Demoted entry {} (spell {}) to guardian for {} (real pet incoming)",
                        demote.entry, demote.spellId, owner->GetName());
                }
                return;
            }
        }

        // Session-only: drop the registry (and despawn the summons) when the player leaves.
        void Clear(Player* owner)
        {
            auto it = _players.find(owner->GetGUID());
            if (it == _players.end())
                return;

            for (ActiveSummon const& summon : it->second.list)
                Unsummon(owner, summon.guid);

            _players.erase(it);
        }

    private:
        static constexpr uint32 RECONCILE_INTERVAL = 1000;

        std::unordered_map<ObjectGuid, PlayerSummons> _players;

        TempSummon* CreateGuardian(Player* owner, uint32 entry, uint32 spellId, int32 duration, bool primary,
            float followAngle)
        {
            static SummonPropertiesEntry const primaryProps = MakeProps(SUMMON_CATEGORY_PET, SUMMON_TYPE_PET);
            static SummonPropertiesEntry const secondaryProps = MakeProps(SUMMON_CATEGORY_ALLY, SUMMON_TYPE_GUARDIAN);

            // Spawn at the summon's follow position (out at pet range, at its own angle)
            // so they appear spread out rather than on top of each other.
            float x, y, z;
            owner->GetClosePoint(x, y, z, owner->GetObjectSize(), PET_FOLLOW_DIST, followAngle);

            TempSummon* summon = owner->GetMap()->SummonCreature(entry,
                Position(x, y, z, owner->GetOrientation()),
                primary ? &primaryProps : &secondaryProps,
                duration, owner, spellId);
            if (!summon)
                return nullptr;

            // Keep the summon following at its own angle around the owner.
            static_cast<Minion*>(summon)->SetFollowAngle(followAngle);

            if (std::string name = sObjectMgr->GeneratePetName(entry); !name.empty())
                summon->SetName(name);

            // Default to defensive: engage when the owner is attacked / attacks, rather
            // than pulling on sight (Guardian::InitStats forces aggressive). Guardian
            // also sent the pet bar (in InitSummon) with the old state, so for the
            // primary re-send it now that the react state is defensive.
            summon->SetReactState(REACT_DEFENSIVE);
            if (primary)
                owner->CharmSpellInitialize();

            return summon;
        }

        void Unsummon(Player* owner, ObjectGuid guid)
        {
            if (Creature* creature = ObjectAccessor::GetCreature(*owner, guid))
                if (TempSummon* summon = creature->ToTempSummon())
                    summon->UnSummon();
        }

        void RemoveEntry(Player* owner, PlayerSummons& ps, uint32 entry)
        {
            for (ActiveSummon const& summon : ps.list)
                if (summon.entry == entry)
                    Unsummon(owner, summon.guid);

            ps.list.erase(std::remove_if(ps.list.begin(), ps.list.end(),
                [entry](ActiveSummon const& summon) { return summon.entry == entry; }),
                ps.list.end());
        }

        void Reconcile(Player* owner, PlayerSummons& ps)
        {
            // Drop summons that have died or despawned.
            ps.list.erase(std::remove_if(ps.list.begin(), ps.list.end(),
                [owner](ActiveSummon const& summon)
                {
                    Creature* creature = ObjectAccessor::GetCreature(*owner, summon.guid);
                    return !creature || !creature->IsAlive();
                }),
                ps.list.end());

            if (ps.list.empty())
                return;

            // While mounted / in flight the pet slot can be transiently empty (the core
            // stashes a real pet, or a guardian primary persists separately). Never
            // promote in that window.
            if (owner->IsMounted() || owner->GetTemporaryUnsummonedPetNumber())
                return;

            // If the pet slot is occupied there is nothing to promote.
            if (!owner->GetPetGUID().IsEmpty())
                return;

            // Promote the oldest remaining summon to primary by re-spawning it as a
            // pet-slot guardian.
            ActiveSummon const promote = ps.list.front();
            Unsummon(owner, promote.guid);
            ps.list.erase(ps.list.begin());

            float const followAngle = FollowAngleForIndex(ps.list.size());

            if (TempSummon* summon = CreateGuardian(owner, promote.entry, promote.spellId, promote.duration,
                true, followAngle))
            {
                ps.list.push_back({ summon->GetGUID(), promote.entry, promote.spellId, promote.duration, true });
                LOG_INFO("module.multiclass_pet_fix", "Promoted entry {} (spell {}) to primary for {}",
                    promote.entry, promote.spellId, owner->GetName());
            }
        }
    };
}

class MulticlassPetFixPlayerScript : public PlayerScript
{
public:
    MulticlassPetFixPlayerScript() : PlayerScript("MulticlassPetFixPlayerScript",
    {
        PLAYERHOOK_ON_BEFORE_LOAD_PET_FROM_DB,
        PLAYERHOOK_ON_BEFORE_GUARDIAN_INIT_STATS_FOR_LEVEL,
        PLAYERHOOK_ON_BEFORE_TEMP_SUMMON_INIT_STATS,
        PLAYERHOOK_ON_PLAYER_IS_CLASS,
        PLAYERHOOK_ON_UPDATE,
        PLAYERHOOK_ON_LOGOUT
    }) { }

    // Real-pet support (hunter pets on multiclass characters): bypass the Death Knight
    // pet exception for non-undead pets loaded from character_pet. Module summons are
    // guardians and never travel this path.
    void OnPlayerBeforeLoadPetFromDB(Player* player, uint32& /*petentry*/, uint32& petnumber, bool& current, bool& forceLoadFromDB) override
    {
        PetStable* petStable = player->GetPetStable();
        if (!petStable)
            return;

        PetStable::PetInfo const* petInfo = nullptr;
        if (petnumber)
        {
            if (petStable->CurrentPet && petStable->CurrentPet->PetNumber == petnumber)
                petInfo = &petStable->CurrentPet.value();
            else
            {
                for (auto const& info : petStable->UnslottedPets)
                {
                    if (info.PetNumber == petnumber)
                    {
                        petInfo = &info;
                        break;
                    }
                }
            }
        }
        else if (current)
        {
            if (petStable->CurrentPet)
                petInfo = &petStable->CurrentPet.value();
        }

        if (petInfo)
        {
            // A real pet is about to load into the pet slot: demote any module guardian
            // holding it to a side summon so the load doesn't destroy it.
            SummonManager::Instance().DemotePrimary(player);

            CreatureTemplate const* creatureInfo = sObjectMgr->GetCreatureTemplate(petInfo->CreatureId);
            if (creatureInfo && creatureInfo->type != CREATURE_TYPE_UNDEAD)
            {
                // Force load from DB to bypass the DK pet exception check for all non-DK pets.
                forceLoadFromDB = true;
            }
        }
    }

    void OnPlayerBeforeGuardianInitStatsForLevel(Player* /*player*/, Guardian* guardian, CreatureTemplate const* /*cinfo*/, PetType& petType) override
    {
        if (guardian->IsPet())
        {
            if (petType == MAX_PET_TYPE)
            {
                petType = guardian->ToPet()->getPetType();
            }
        }
    }

    // Pet-context class identity for multiclass characters: if a character has learned
    // another class's pet-summon spell, treat them as that class for PET-ONLY checks.
    // Strictly gated on HasSpell + CLASS_CONTEXT_PET, so it never fires for a character
    // that lacks the spell and defers to the real class everywhere else.
    Optional<bool> OnPlayerIsClass(Player const* player, Classes playerClass, ClassContext context) override
    {
        if (context != CLASS_CONTEXT_PET)
            return std::nullopt;

        switch (playerClass)
        {
            case CLASS_WARLOCK:
                if (player->HasSpell(688) || player->HasSpell(697) || player->HasSpell(712) ||
                    player->HasSpell(691) || player->HasSpell(30146))
                    return true;
                break;
            case CLASS_MAGE:
                if (player->HasSpell(31687))
                    return true;
                break;
            case CLASS_DEATH_KNIGHT:
                if (player->HasSpell(46584))
                    return true;
                break;
            case CLASS_HUNTER:
                if (player->HasSpell(883))
                    return true;
                break;
            default:
                break;
        }

        return std::nullopt;
    }

    // Flag module summon guardians controllable and inject their pet ability set during
    // InitStats (before AddToWorld -> AIM_Initialize and before Guardian::InitStats builds
    // the action bar), so PetAI is selected and the abilities land on the bar + autocast.
    void OnPlayerBeforeTempSummonInitStats(Player* player, TempSummon* tempSummon, uint32& /*duration*/) override
    {
        if (IsPlayerBot(player))
            return;

        if (!tempSummon->IsGuardian())
            return;

        Guardian* guardian = static_cast<Guardian*>(tempSummon);
        if (!IsMulticlassSummonSpell(guardian->GetUInt32Value(UNIT_CREATED_BY_SPELL)))
            return;

        // NOTE: Do NOT call AIM_Initialize() here — AddToWorld() does it, and the mask
        // below ensures PetAI is picked at that point.
        guardian->AddUnitTypeMask(UNIT_MASK_CONTROLLABLE_GUARDIAN);
        guardian->InitCharmInfo();
        ApplyPetAbilities(guardian, player->GetLevel());
    }

    void OnPlayerUpdate(Player* player, uint32 diff) override
    {
        if (IsPlayerBot(player))
            return;

        SummonManager::Instance().Update(player, diff);
    }

    void OnPlayerLogout(Player* player) override
    {
        SummonManager::Instance().Clear(player);
    }
};

class SpellSummonPetOverrideScript : public SpellScript
{
    PrepareSpellScript(SpellSummonPetOverrideScript);

    void HandleSummon(SpellEffIndex effIndex)
    {
        Player* owner = GetCaster()->ToPlayer();
        if (!owner)
            return;

        // Leave playerbots on stock single-pet behaviour (their AI relies on GetPet()).
        if (IsPlayerBot(owner))
            return;

        uint32 const entry = GetSpellInfo()->Effects[effIndex].MiscValue;
        if (!entry)
            return;

        // The module owns every one of these summons — never run the default (real-pet)
        // effect, which would dismiss the active pet.
        PreventHitDefaultEffect(effIndex);

        int32 duration = GetSpellInfo()->GetDuration();
        if (Player* modOwner = owner->GetSpellModOwner())
            modOwner->ApplySpellMod(GetSpellInfo()->Id, SPELLMOD_DURATION, duration);

        SummonManager::Instance().HandleCast(owner, GetSpellInfo()->Id, entry, duration);
    }

    void Register() override
    {
        OnEffectHit += SpellEffectFn(SpellSummonPetOverrideScript::HandleSummon, EFFECT_0, SPELL_EFFECT_SUMMON_PET);
        OnEffectHit += SpellEffectFn(SpellSummonPetOverrideScript::HandleSummon, EFFECT_1, SPELL_EFFECT_SUMMON_PET);
        OnEffectHit += SpellEffectFn(SpellSummonPetOverrideScript::HandleSummon, EFFECT_2, SPELL_EFFECT_SUMMON_PET);

        OnEffectHit += SpellEffectFn(SpellSummonPetOverrideScript::HandleSummon, EFFECT_0, SPELL_EFFECT_SUMMON);
        OnEffectHit += SpellEffectFn(SpellSummonPetOverrideScript::HandleSummon, EFFECT_1, SPELL_EFFECT_SUMMON);
        OnEffectHit += SpellEffectFn(SpellSummonPetOverrideScript::HandleSummon, EFFECT_2, SPELL_EFFECT_SUMMON);
    }
};

class SpellSummonPetOverrideLoader : public SpellScriptLoader
{
public:
    SpellSummonPetOverrideLoader() : SpellScriptLoader("spell_summon_pet_override") { }

    SpellScript* GetSpellScript() const override
    {
        return new SpellSummonPetOverrideScript();
    }
};

class MulticlassSummonWorldScript : public WorldScript
{
public:
    MulticlassSummonWorldScript() : WorldScript("MulticlassSummonWorldScript") { }

    // Allow these summons to be cast while another pet is already active. Without
    // SPELL_ATTR1_DISMISS_PET_FIRST, Spell::CheckCast rejects a SUMMON_PET (or a
    // pet-category SUMMON, e.g. the temporary Water Elemental) with ALREADY_HAVE_SUMMON
    // when the caster has a pet, so the (often triggered) Water Elemental / permanent
    // ghoul summon silently fails. We intercept the effect and spawn a side guardian, so
    // the "dismiss first" semantics never actually run. Warlock demons already carry this
    // attribute; setting it again is a no-op. Runs after spells are loaded.
    void OnStartup() override
    {
        // 883 = hunter Call Pet: without the attribute, CheckCast answers
        // ALREADY_HAVE_SUMMON while a module guardian holds the pet slot, so a hunter
        // with demons out could never call their real pet. Safe to add: 883 HAS
        // SPELL_EFFECT_SUMMON_PET, so Spell::prepare's attr-driven RemovePet branch
        // skips it (and Player::GetPet() is null for a guardian anyway). The guardian
        // itself is demoted, not lost — see SummonManager::DemotePrimary, which the
        // BeforeLoadPetFromDB hook runs before the real pet claims the slot.
        static constexpr uint32 spells[] = { 688, 697, 712, 691, 30146, 70907, 70908, 46584, 52150, 883 };
        for (uint32 id : spells)
            if (SpellInfo const* info = sSpellMgr->GetSpellInfo(id))
                const_cast<SpellInfo*>(info)->AttributesEx |= SPELL_ATTR1_DISMISS_PET_FIRST;
    }
};

void AddMulticlassPetFixScripts()
{
    new MulticlassPetFixPlayerScript();
    new SpellSummonPetOverrideLoader();
    new MulticlassSummonWorldScript();

    // NOTE: spell_script_names registration is handled by
    // data/sql/db-world/base/multiclass_summons.sql, which the DBUpdater auto-applies
    // during database loading at startup, BEFORE LoadSpellScriptNames().
}
