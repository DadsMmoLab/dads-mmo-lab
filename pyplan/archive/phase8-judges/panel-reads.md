# The six reads taken after the panel reported, with their lines

> The decisions page lists six facts as "settled by reading". The third review found that none
> of the six was committed as a read — they entered the decision through no reader — and that
> one of the six was wrong in exactly the way an unreviewed read fails. This file is the
> missing artefact: each fact, the command, and the lines as they came back on 2026-09-06.
> The trees are the ones the catalog pins; the fetched revisions are in `phase8-delta.md`.

## 1. Bots added to a party are logged out with their master

```
450:    void OnPlayerbotLogout(Player* player) override
451:    {
452:        if (PlayerbotMgr* playerbotMgr = GET_PLAYERBOT_MGR(player))
453:        {
454:            PlayerbotAI* botAI = PlayerbotsMgr::instance().GetPlayerbotAI(player);
455:
456:            if (botAI == nullptr || IsSelfBot(player))
457:                playerbotMgr->LogoutAllBots();
458:        }
459:
460:        sRandomPlayerbotMgr.OnPlayerLogout(player);
461:    }
```

The condition has two arms, and the decisions page's paraphrase named only the first. It fires
when the logging-out player has no bot AI of its own — a human — **or** when it is a self-bot.
The fact the design needed holds: a human's added bots do not outlive their master's session.

## 2. Tortoise does not cache passwords; only the rank is cached
```
bool AccountMgr::CheckPassword(uint32 accid, std::string passwd, std::string username)
{
    if (username.empty())
    {
        if (!GetName(accid, username))
            return false;
    }
    else
        normalizeString(username);

    normalizeString(passwd);

    QueryResult *result = LoginDatabase.PQuery("SELECT 1 FROM account WHERE id='%u' AND sha_pass_hash='%s'", accid, CalculateShaPassHash(username, passwd).c_str());
```

## 3. The Lua engine's command hook hands the script the chat handler
```
    }

    START_HOOK_WITH_RETVAL(PLAYER_EVENT_ON_COMMAND, true);
    Push(player);
    Push(text);
    Push(&handler);
    return CallAllFunctionsBool(PlayerEventBindings, key, true);
```
And the compiled default disagrees with the shipped comment:
```
/c/Users/perzi/AppData/Local/Temp/claude/C--Users-perzi-dads-mmo-lab/e6db1900-f1cd-4099-995e-e1ef6bec1004/scratchpad/ac-trees/mod-ale/src/LuaEngine/ALEConfig.cpp:20:    SetConfigValue<bool>(ALEConfigValues::ENABLED,                    "ALE.Enabled",            "false");
/c/Users/perzi/AppData/Local/Temp/claude/C--Users-perzi-dads-mmo-lab/e6db1900-f1cd-4099-995e-e1ef6bec1004/scratchpad/ac-trees/mod-ale/conf/mod_ale.conf.dist:6:#   ALE.Enabled
/c/Users/perzi/AppData/Local/Temp/claude/C--Users-perzi-dads-mmo-lab/e6db1900-f1cd-4099-995e-e1ef6bec1004/scratchpad/ac-trees/mod-ale/conf/mod_ale.conf.dist:63:ALE.Enabled = true
```

## 4. The CMaNGOS equipped-inventory table, which the delta records as unread
```
416:CREATE TABLE `character_inventory` (
417-  `guid` int(11) unsigned NOT NULL DEFAULT '0' COMMENT 'Global Unique Identifier',
418-  `bag` int(11) unsigned NOT NULL DEFAULT '0',
419-  `slot` tinyint(3) unsigned NOT NULL DEFAULT '0',
420-  `item` int(11) unsigned NOT NULL DEFAULT '0' COMMENT 'Item Global Unique Identifier',
421-  `item_template` int(11) unsigned NOT NULL DEFAULT '0' COMMENT 'Item Identifier',
422-  PRIMARY KEY (`item`),
```
Read 2026-09-06 by the third review, not by the panel. **Vanilla and Tortoise were read the same
day** and carry the same five columns: `mangos-classic/sql/base/characters.sql:339-346` and
`tortoise-wow/sql/create_databases.sql:573-581`. So all four trees carry the item's template id on
the inventory row, which makes the equipped-set join *simpler* on the three CMaNGOS-lineage trees
than on AzerothCore, where it needs a second hop through the item instance. The prerequisite this
note was written to justify is therefore closed, and gear sets stay on all four families.

## 5. The AzerothCore worldserver image installs neither iproute2 nor curl

This is the one that was published wrong. The stage graph:
```
5:FROM ubuntu:$UBUNTU_VERSION AS skeleton
44:FROM skeleton AS build
112:FROM skeleton AS runtime
154:FROM runtime AS authserver
175:FROM runtime AS worldserver
198:FROM runtime AS db-import
222:FROM skeleton AS client-data
256:FROM runtime AS tools
```
The worldserver is built `FROM runtime`, whose only apt install is:
```
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      libmysqlclient21 libreadline8 libicu74 libncurses5-dev \
      gettext-base default-mysql-client \
      adduser \
    && rm -rf /var/lib/apt/lists/*
```
Every `curl` in the file, and neither is in that stage:
```
68:        git lsb-base curl unzip default-mysql-client openssl                  \
229:    apt-get install -y curl unzip adduser && \
```
`iproute2` and `net-tools` appear nowhere:
```
$ grep -c 'iproute2\|net-tools' Dockerfile
0
```

## 6. An app account named YULON already exists on the CMaNGOS gate box

Not a tree read — a read of this repository's own record, `pyplan/checklist.md:1452-1461`,
where the Phase 7.9 gate wrote that account at GM level 3 and logged a client in through it.
It is why the app's account is named per install rather than by a fixed string.
