--[[ ============================================================
  dml_uninvite.lua — Dad's MMO Lab "kick a bot from my party" relay
  (AGPL-3.0, part of dads-mmo-lab; reimplemented, not copied.)

  Registers a console/SOAP-callable command:
      dml_uninvite <playerName> <botName>

  Calls bot:RemoveFromGroup() (AC Player::RemoveFromGroup) directly
  via Eluna — /uninvite is an opcode-layer slash command that
  Player:RunCommand cannot drive, and there is no SOAP kick command.
  The bot knows its own group; remaining members get the leave packet.

  T13 (from T5's round-3 review): <playerName> is new.
  party.py's InstallParty.remove_all re-reads the group table and
  refuses to act unless the fresh guid set is exactly what a person
  confirmed -- but the interval between that read and this command
  landing is real, and nothing further Python reads narrows it to
  zero: the bot manager moves bots on its own timers, so a bot can
  join a DIFFERENT master's party in that window. Before this
  ticket the one-argument form trusted whatever group the bot
  happened to occupy WHEN THIS RAN, so such a bot was kicked from a
  party nobody confirmed. This is the only place left that can
  still see the group at the moment it acts, so the check moves
  here: before RemoveFromGroup(), verify <botName>'s CURRENT group
  counts <playerName> as a MEMBER.

  Round 1 (cold review, rejected): an earlier version of this
  file tested LEADERSHIP (`g:GetLeaderGUID() == p:GetGUID()`), which
  is a different question from the one party.py's own
  `group_rows_sql` asks (`party.py:907-935`: every bot whose
  `group_member.guid` equals the MASTER'S OWN group id -- no
  leadership clause at all). A master grouped with another human, or
  any leader hand-off, is still that master's party by the feature's
  own definition, and the leadership test refused such a master's
  every bot with a false sentence. Fixed here by testing membership
  instead, with `Group:IsMember(guid)` -- confirmed against the
  documented Eluna API (azerothcore.org/eluna/Group/IsMember.html,
  read 2026-09-09: `Group:IsMember(guid: number) -> boolean`,
  "Returns 'true' if the Player is a member of this Group"), which
  also does the comparison in C++ rather than asking Lua to compare
  two GUID userdata with ~=/== -- the round-1 review's own note that
  Eluna's guid values are not guaranteed a Lua `__eq` metamethod, and
  a silent mismatch there would have refused every uninvite rather
  than only the wrong ones. Nothing pinned from this fork's own
  mod-playerbots was on this box to read against (server modules
  live on the test VMs, never this laptop -- see this script
  family's sibling `party.py` header for why); this fork's own
  master-resolution semantics (what playerbots itself counts as
  "grouped with") are still the live half's job to prove against a
  real party.

  Round 1, must-fix 2: every branch that does NOT remove the bot --
  <playerName> not found/offline, <botName> not found/offline,
  <botName> ungrouped, <botName> grouped with someone else -- used to
  answer with print() alone, which never reaches a SOAP caller (see
  dml_bridge_ping.lua's header): the hook returns false either way,
  so the core's error flag never trips and an empty <result> reads
  as outcome=="yes" on the Python side, exactly the false-success
  this ticket exists to delete. Every one of the four branches now
  answers over handler:SendSysMessage() too, with ONE marker shared
  by all four (chosen over one sentence per branch: party.py's
  dismiss() then needs to recognise only one shape, not enumerate
  the branches a second time on its side of the wire) plus that
  branch's own reason clause, in the words party.py's dismiss() reads
  back (`_uninvite_refusal_marker`):
      "<botName> is not in <playerName>'s party now (<reason>)"
============================================================ --]]

local function OnUninviteCommand(event, player, command, handler)
    if player ~= nil then return end

    local pname, bname = command:match("^dml_uninvite%s+(%S+)%s+(%S+)$")
    if not pname then return end

    local function refuse(reason)
        local msg = string.format("%s is not in %s's party now (%s)", bname, pname, reason)
        if handler ~= nil then
            handler:SendSysMessage(msg)
        end
        print("[dml_uninvite] " .. msg)
        return false
    end

    local p = GetPlayerByName(pname)
    if not p then
        return refuse(string.format("%s not found or offline", pname))
    end
    local b = GetPlayerByName(bname)
    if not b then
        return refuse(string.format("%s not found or offline", bname))
    end

    -- Round 2's review, note 4: `dml_uninvite <master> <master>` resolves
    -- p and b to the SAME player, passes IsMember (a player is a member of
    -- his own group) and removes the master from his own party. The app
    -- itself cannot ask for it -- `group_rows_sql`'s `bot_clause` never
    -- returns the master, so the panel can neither list nor confirm him --
    -- but this command is console/SOAP-callable by anything holding the
    -- admin account, and the refusal costs one comparison. Proved live
    -- rather than reasoned: the gate folder this guard ships with whispers
    -- it and captures the refusal.
    if b == p then
        return refuse(string.format("%s is the master, not a bot in the party", bname))
    end

    local g = b:GetGroup()
    if g == nil then
        return refuse(string.format("%s is not grouped with anyone", bname))
    end
    if not g:IsMember(p:GetGUID()) then
        return refuse(string.format("%s is grouped with someone else", bname))
    end

    b:RemoveFromGroup()
    print(string.format("[dml_uninvite] removed %s from group", bname))
    return false
end

RegisterPlayerEvent(42, OnUninviteCommand)
print("[dml_uninvite] loaded -- group-remove relay ready")
