--[[ ============================================================
  dml_botadd.lua — Dad's MMO Lab "add a named character as a bot" relay
  (AGPL-3.0, part of dads-mmo-lab; reimplemented, not copied.)

  Registers a console/SOAP-callable command:
      dml_botadd <masterName> <characterName>

  Runs `.playerbots bot add <characterName>` AS <masterName>, through
  ALE's Player:RunCommand -- the same three lines as dml_login.lua:26-27,
  and for the same reason: the command is SEC_PLAYER, Console::No
  (mod-playerbots PlayerbotCommandScript.cpp:36 at b949b50b), so a
  console or SOAP caller cannot see it, and the master is always the
  CALLING SESSION's own player (PlayerbotMgr.cpp:860-874) -- no argument
  names another master, which is why the master's name is what this
  script needs and the command itself does not carry it.

  MEASURED on yulon-ubuntu2, 2026-09-10; the record is
  .notes/gates/8.6-altbot-measure-yulon-ubuntu2-2026-09-10/. Over the
  app's own SOAP seam `.playerbots bot add <name>` came back with the
  USAGE list of the three Console::Yes siblings and the world logged
  nothing at all (04-soap.log); `bot add` and `.bot add` come back
  "Command 'bot add <name>' does not exist" -- there is no `.bot add` on
  this fork. The relay is the only route.

  WHY THIS SCRIPT ANSWERS IN ITS OWN WORDS. The module reports what it
  did through the MASTER's session: PlayerbotMgr.cpp:889-892 loops its
  messages into handler->PSendSysMessage, which for a RunCommand caller
  is the master's game window and never the SOAP <result>. So not one of
  the module's own sentences -- "Failure: You are not allowed to control
  bot <Name>" (:115), "Failure: You have added too many bots (more than
  40)" (:134), "player already logged in" (:686) -- can reach this app.
  This script therefore says only what IT can see, and party.py's
  add_named() decides the outcome by polling the group table, never by
  this reply. dml_uninvite.lua solved the same shape first (T13).

  THE TWO THINGS IT CAN SEE, and nothing else is claimed:

  * <masterName> is not in the world. GetPlayerByName answers for online
    players only (dml_login.lua uses it the same way), and the module
    refuses a session-less caller anyway (PlayerbotMgr.cpp:864-866,
    "You may only add bots from an active session").
  * <characterName> is logged in. An online character can never be
    added: PlayerbotMgr.cpp:685-686 short-circuits before any permission
    rule with "player already logged in". A character this script cannot
    find is either offline -- which is the addable case -- or not on this
    server at all, and nothing in the Eluna API this bridge already uses
    tells those two apart. So the script does not guess: it issues the
    command and lets the poll report what arrived. The picker reads
    acore_characters itself and offers only names that are there
    (party.py's candidates_sql).

  The five permission rules (same account, guild mate, addclass pool,
  linked account, random bot -- PlayerbotMgr.cpp:101-116) are decided
  inside the module, out of this script's sight. party.py applies them
  BEFORE the press so a person is not sent at a refusal nothing can
  hear.
============================================================ --]]

-- PLAYER_EVENT_ON_COMMAND = 42 (https://www.azerothcore.org/eluna/Hooks.html)
local function OnBotAddCommand(event, player, command, handler)
    if player ~= nil then return end  -- console / SOAP origin only

    local pname, cname = command:match("^dml_botadd%s+(%S+)%s+(%S+)$")
    if not pname then return end  -- not our command

    -- One marker for every branch that does NOT issue the command, each
    -- carrying its own reason, exactly as dml_uninvite.lua answers: the
    -- Python side then recognises one shape rather than enumerating the
    -- branches a second time on its side of the wire.
    local function refuse(reason)
        local msg = string.format("%s was not added to %s's party (%s)", cname, pname, reason)
        if handler ~= nil then
            handler:SendSysMessage(msg)
        end
        print("[dml_botadd] " .. msg)
        return false
    end

    local p = GetPlayerByName(pname)
    if not p then
        return refuse(string.format("%s is not in the world", pname))
    end
    if GetPlayerByName(cname) then
        return refuse(string.format("%s is logged in", cname))
    end

    p:RunCommand(string.format("playerbots bot add %s", cname))
    -- Not "added": the command was ISSUED. Whether a bot arrived is the
    -- group table's answer and party.py polls it -- this line is only
    -- what the relay itself did.
    local msg = string.format("%s asked the server to add %s", pname, cname)
    if handler ~= nil then
        handler:SendSysMessage(msg)
    end
    print(string.format("[dml_botadd] %s ran: .playerbots bot add %s", pname, cname))
    return false
end

RegisterPlayerEvent(42, OnBotAddCommand)
print("[dml_botadd] loaded -- named-character add relay ready")
