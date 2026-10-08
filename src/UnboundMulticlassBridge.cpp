#include "Chat.h"
#include "DatabaseEnv.h"
#include "DBCStores.h"
#include "Log.h"
#include "Player.h"
#include "ScriptMgr.h"

#include <charconv>
#include <sstream>
#include <string>
#include <string_view>

namespace
{
constexpr std::string_view AddonPrefix = "MCUB";

bool IsUnboundClass(uint32 classId)
{
    switch (classId)
    {
        case 1:  // Warrior
        case 2:  // Paladin
        case 3:  // Hunter
        case 4:  // Rogue
        case 5:  // Priest
        case 7:  // Shaman
        case 8:  // Mage
        case 9:  // Warlock
        case 11: // Druid
            return true;
        default:
            return false;
    }
}

uint32 RefreshUnboundClassMask(Player* player)
{
    uint32 classMask = 0;
    QueryResult result = CharacterDatabase.Query(
        "SELECT class_id FROM unbound_character_unlocks WHERE char_guid = {} ORDER BY class_id",
        player->GetGUID().GetCounter());

    if (result)
    {
        do
        {
            uint8 const classId = result->Fetch()[0].Get<uint8>();
            if (IsUnboundClass(classId))
                classMask |= 1u << (classId - 1);
        } while (result->NextRow());
    }

    player->SetUnboundClassMask(classMask);
    return classMask;
}

void SendAddon(Player* player, std::string const& payload)
{
    std::string const wire = std::string(AddonPrefix) + "\t" + payload;
    WorldPacket packet;
    ChatHandler::BuildChatPacket(packet, CHAT_MSG_WHISPER, LANG_ADDON, player, player, wire.c_str());
    player->SendDirectMessage(&packet);
}

bool IsClassUnlocked(Player* player, uint32 classId)
{
    if (classId == player->getClass())
        return true;

    return (RefreshUnboundClassMask(player) & (1u << (classId - 1))) != 0;
}

bool ParseUint32(std::string_view value, uint32& result)
{
    auto const [end, error] = std::from_chars(value.data(), value.data() + value.size(), result);
    return error == std::errc{} && end == value.data() + value.size();
}

void SendClassSync(Player* player)
{
    uint32 const classMask = RefreshUnboundClassMask(player);

    std::ostringstream classes;
    bool first = true;
    for (uint8 classId = 1; classId <= 11; ++classId)
    {
        if (classMask & (1u << (classId - 1)))
        {
            if (!first)
                classes << ',';
            classes << classId;
            first = false;
        }
    }

    LOG_INFO("server.loading", "MCUB sync for {} (guid {}) returned class mask {}.",
        player->GetName(), player->GetGUID().GetCounter(), classMask);
    SendAddon(player, "CLASSES:" + classes.str());
}

void Deny(Player* player, std::string_view reason, uint32 classId, uint32 spellId)
{
    SendAddon(player, "DENY:" + std::string(reason) + ':' + std::to_string(classId) + ':' + std::to_string(spellId));
}

void LearnTalent(Player* player, uint32 classId, uint32 spellId)
{
    if (!IsUnboundClass(classId) || !IsClassUnlocked(player, classId))
    {
        Deny(player, "LOCKED", classId, spellId);
        return;
    }

    TalentSpellPos const* position = GetTalentSpellPos(spellId);
    if (!position)
    {
        Deny(player, "INVALID", classId, spellId);
        return;
    }

    TalentEntry const* talent = sTalentStore.LookupEntry(position->talent_id);
    TalentTabEntry const* talentTab = talent ? sTalentTabStore.LookupEntry(talent->TalentTab) : nullptr;
    uint32 const classMask = 1u << (classId - 1);
    if (!talent || !talentTab || !(talentTab->ClassMask & classMask) || talent->RankID[position->rank] != spellId)
    {
        Deny(player, "INVALID", classId, spellId);
        return;
    }

    // The submitted spellId identifies the TALENT, not necessarily the exact
    // next rank: client-side talent data tables do not always order ranks the
    // way the server DBC's RankID[] does, which made legitimate picks on
    // multi-rank talents deny with RANK. Normalize server-side: find the
    // player's current rank in this talent and learn the one above it. The
    // core's LearnTalent still enforces prerequisites, tier points and class
    // mask; the client is told the spellId it submitted so its pending-pick
    // bookkeeping still matches.
    uint8 const spec = player->GetActiveSpec();
    int32 currentRank = -1;
    for (int32 rank = MAX_TALENT_RANK - 1; rank >= 0; --rank)
    {
        if (talent->RankID[rank] && player->HasTalent(talent->RankID[rank], spec))
        {
            currentRank = rank;
            break;
        }
    }

    uint32 const nextRank = static_cast<uint32>(currentRank + 1);
    if (nextRank >= MAX_TALENT_RANK || !talent->RankID[nextRank])
    {
        Deny(player, "RANK", classId, spellId);   // talent already at max rank
        return;
    }

    if (player->GetFreeTalentPoints() == 0)
    {
        Deny(player, "NOPOINTS", classId, spellId);
        return;
    }

    player->LearnTalent(position->talent_id, nextRank);
    player->SendTalentsInfoData(false);
    if (player->HasTalent(talent->RankID[nextRank], spec))
        // The trailing field is the AUTHORITATIVE rank count now known by the
        // server. The client keys its pending pick by the submitted spellId
        // (identical across a multi-rank batch), so an unranked echo collapsed
        // five confirmations into one local +1 -- the client then trailed the
        // server forever and every local rank/prereq check lied.
        SendAddon(player, "LEARNED:" + std::to_string(classId) + ':' + std::to_string(spellId) +
                          ':' + std::to_string(nextRank + 1));
    else
        Deny(player, "TIER", classId, spellId);
}
}

class UnboundMulticlassBridgeScript : public PlayerScript
{
public:
    UnboundMulticlassBridgeScript() : PlayerScript("UnboundMulticlassBridgeScript", { PLAYERHOOK_CAN_PLAYER_USE_PRIVATE_CHAT }) { }

    bool OnPlayerCanUseChat(Player* player, uint32 /*type*/, uint32 language, std::string& message, Player* receiver) override
    {
        if (language != LANG_ADDON || !receiver || receiver != player)
            return true;

        std::string const prefix = std::string(AddonPrefix) + '\t';
        if (message.rfind(prefix, 0) != 0)
            return true;

        std::string_view const payload(message.c_str() + prefix.size(), message.size() - prefix.size());
        if (payload == "SYNC")
            SendClassSync(player);
        else if (payload == "RESET")
        {
            player->resetTalents(true);
            // resetTalents changes the server field but does not itself send
            // the talent window update that GetUnspentTalentPoints() uses.
            player->SendTalentsInfoData(false);
            SendAddon(player, "RESET:" + std::to_string(player->GetFreeTalentPoints()));
        }
        else if (payload.rfind("LEARN:", 0) == 0)
        {
            std::string_view const args = payload.substr(6);
            size_t const separator = args.find(':');
            uint32 classId = 0;
            uint32 spellId = 0;
            if (separator == std::string_view::npos || !ParseUint32(args.substr(0, separator), classId) || !ParseUint32(args.substr(separator + 1), spellId))
                Deny(player, "INVALID", classId, spellId);
            else
                LearnTalent(player, classId, spellId);
        }

        return false;
    }
};

void AddUnboundMulticlassBridge()
{
    new UnboundMulticlassBridgeScript();
}
