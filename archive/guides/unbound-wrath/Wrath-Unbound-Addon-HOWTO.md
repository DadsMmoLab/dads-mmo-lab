# ⚔️ Wrath Unbound — Setup & Player Guide
**Dad's MMO Lab** · youtube.com/@DadsMmoLab · github.com/DadsMmoLab/dads-mmo-lab

*For: `install-wrath-unbound-addon.sh` v1.2.2*

---

## What This Guide Covers

- What Wrath Unbound adds to your server
- Requirements (what you need before running this)
- Running the installer (and what each phase does)
- Spawning the Mentor
- Unlocking classes — the milestone ladder
- Buying abilities — instant-buy and "Buy ALL"
- Cross-class access: training, equipping, and questing as your unlocked classes
- The Unbounding Mentor Stone
- Updating to a newer version
- Uninstalling
- Known limitations
- Troubleshooting

---

## What Is Wrath Unbound?

Wrath Unbound is a multi-class mod for Dad's MMO Lab's WotLK 3.3.5a Playerbots server. Instead of being locked to the class you rolled at character creation, you can unlock additional classes through an NPC called **The Mentor** and gradually buy that class's abilities with gold.

A single character can end up with, say, a Warrior's stances and Charge, a Mage's Fireball and teleports, and a Priest's heals — all on one character, all usable at the same time.

Covers 9 of WotLK's 10 classes (everything except Death Knight): **Warrior, Paladin, Hunter, Rogue, Priest, Shaman, Mage, Warlock, Druid.**

---

## Requirements

This is an **add-on** — it layers onto a server you already have. You need:

- A working Dad's MMO Lab WotLK Playerbots server (from `install-wow-wotlk.sh` / `install-wow.sh`), with `docker compose up -d` working
- The `ac-database`, `ac-authserver`, and `ac-worldserver` containers able to start
- About **30-90 minutes** free for a forced worldserver rebuild — the installer compiles a small C++ module and applies a core-engine patch, so a rebuild is unavoidable
- Your Steam Deck plugged in for that rebuild

You do **not** need a fresh server — this installs on top of your existing world, characters, and bot accounts.

---

## Step 1 — Run the Installer

From Desktop Mode, open Konsole:

```bash
cd ~/Downloads
chmod +x install-wrath-unbound-addon.sh
./install-wrath-unbound-addon.sh
```

The installer will:

1. **Check compatibility** — confirms your server is a Dad's MMO Lab WotLK Playerbots install with the catalog data Wrath Unbound depends on
2. **Back up your databases** — `acore_world` and `acore_characters` are dumped to `~/wrath-unbound-backups/<timestamp>/` before anything changes
3. **Stage the mod** — drops in the `mod-unbound` C++ module, the Mentor's Lua script, and all SQL migrations
4. **Stage the Eluna/ALE Lua engine** if your server doesn't already have it (this is what lets the Mentor's Lua script run at all)
5. **Apply a small core-engine patch** — six files in AzerothCore's source get a small addition (`Player::m_unboundClassMask`) that's what makes cross-class trainer/quest/item access work (see below)
6. **Patch `worldserver.conf`** — sets `ValidateSkillLearnedBySpells = 0`, which AzerothCore needs so it doesn't strip your cross-class spells every time you log in
7. **Rebuild the worldserver** — this is the 30-90 minute step
8. **Walk you through the one manual step** — spawning the Mentor

If you ever need to back out, your pre-install database dumps are sitting in `~/wrath-unbound-backups/`.

---

## Step 2 — Meet the Mentor

Once the rebuild finishes and the worldserver is back up, the installer asks you to spawn the Mentor.

In-game, walk to wherever you want the Mentor to stand and run:

```
.npc add 900001
```

This requires GM level 3 on your account (the same level the base installer's account-creation step grants you). The spawn is **permanent** — you only need to do this once, ever, even across server restarts.

The Mentor appears as an **Ethereal Thief**. Talk to it to open the Wrath Unbound menu.

---

## Step 3 — Unlocking Classes (the Milestone Ladder)

Talking to the Mentor at the right level offers "Unlock another class." Each unlock has a level requirement and a gold cost:

| Unlock | Level Required | Cost |
|--------|----------------|------|
| 1st extra class | 5 | **Free** |
| 2nd extra class | 25 | 3 gold |
| 3rd extra class | 50 | 80 gold |
| 4th extra class | 70 | 300 gold |
| 5th+ extra class | 80 | 1,500 gold each |

Pick which of the other 8 classes you want to unlock from a list (you can't unlock your own native class — you already have it). On unlock you immediately receive:

- That class's starting abilities (stances, Stealth, Fireball, Lightning Bolt, totems, etc. — whatever a freshly-rolled character of that class gets at level 1)
- Any starting items it needs (e.g. a Shaman's totems)
- The weapon/armor skills and resource pool (rage/mana/energy) that class uses

**Log out and back in once** after unlocking a class — this lets the spellbook tabs and resource pools register correctly.

---

## Step 4 — Buying Abilities

Once a class is unlocked, talk to the Mentor and choose **"Browse [Class] abilities."** This shows every ability for that class you're currently eligible for — level requirement met, and (for higher ranks) the previous rank already learned.

**Instant-buy:** click any ability in the list and it's bought immediately — gold is deducted, the spell is learned, and the menu refreshes so you can keep buying without re-navigating.

**Buy ALL:** at the top of the first page, a gold-highlighted **"Buy ALL available abilities"** option shows the total cost of everything currently buyable for that class. One click buys everything you can currently afford — and because buying a rank-1 ability often unlocks rank 2 as buyable, it keeps going in passes until nothing new becomes available or you run out of gold.

As you level up and learn more, return to the Mentor — new abilities unlock into the Browse list automatically.

---

## Cross-Class Access (New in v1.2.0)

Earlier versions let you *buy* cross-class abilities from the Mentor, but using them elsewhere — training at a normal class trainer, wearing that class's armor, picking up that class's quests — didn't work. **v1.2.0 fixes all three:**

- **Trainers:** visit any class trainer (e.g. a Priest trainer) and, if you've unlocked Priest via the Mentor, that trainer's full spell list is now available to you — same as a native Priest would see.
- **Equipment:** gear restricted to a class you've unlocked can now be equipped (e.g. a Mage who's unlocked Warrior can wear plate). Race restrictions on items are unchanged.
- **Quests:** class-restricted quest lines for any class you've unlocked are now available to pick up and complete.

This works for **every class you've unlocked via the Mentor**, automatically, with no extra setup — it's driven by a small addition to the server's core code that checks "is this one of my unlocked classes?" alongside the usual "is this my native class?" check.

---

## The Unbounding Mentor Stone

Every character automatically receives an **Unbounding Mentor Stone** at creation. Right-click it to summon the Mentor to your location for 3 minutes — handy if you're out leveling and don't want to travel back.

- 3-minute cooldown between uses
- The green "Use:" tooltip text is a leftover from the placeholder spell it's bound to and can be ignored — the item's name and description (which explain what it actually does) are accurate

---

## What's New in v1.2.0 — Catalog Fixes

A full audit of the Mentor's ability catalog found a handful of entries that took your gold, said "Learned!", but didn't actually grant a usable spell (a quirk of how AzerothCore handles certain "teach" spells). These are now fixed:

- **Paladin — Judgement** now grants the real, castable Judgement
- **Paladin — Summon Warhorse** and **Warlock — Summon Felsteed** now grant working mounts
- **Druid — Flight Form** now grants a working flight form
- **Mage** gained its missing teleport and portal spells across the leveling range
- **Paladin** gained Summon Warhorse as a catalog entry (previously missing)
- ~30 catalog entries had their level requirements corrected against real trainer data

If you bought one of these abilities on an older version and it never worked, talk to the Mentor again after updating — the catalog entry now points at the correct spell and is buyable normally.

> **Note on mounts/Flight Form:** these spells additionally require Riding skill (Apprentice 75 for ground mounts, Expert 225 for Flight Form). Wrath Unbound grants universal access to the Riding skill line, but you still need to actually train it up to the right rank.

---

## Updating to a Newer Version

Just **re-run the installer**:

```bash
cd ~/Downloads
./install-wrath-unbound-addon.sh
```

It detects your existing Wrath Unbound install, re-stages every file, re-applies all SQL migrations (safe to re-run — nothing gets duplicated), applies any new core-engine patch, and rebuilds. Your Mentor stays spawned, your players' unlocked classes and purchased spells are untouched, and everyone picks up the new behavior on their next login. No character action needed.

---

## Uninstalling

A matching uninstaller is included:

```bash
cd ~/Downloads
chmod +x uninstall-wrath-unbound-addon.sh
./uninstall-wrath-unbound-addon.sh
```

This backs up your databases first, then:

- Removes the Mentor NPC, the Mentor Stone item, and all Wrath Unbound database tables
- Reverts the core-engine patch (if present) and the `docker-compose.override.yml` / `worldserver.conf` changes
- Sets `ValidateSkillLearnedBySpells = 1`, which makes AzerothCore automatically strip any cross-class spells from characters on their next login — no manual character editing needed
- Rebuilds the worldserver back to a stock Dad's MMO Lab WotLK Playerbots server

Your characters, levels, gold, and native-class abilities are untouched.

---

## Known Limitations

- **Spellbook tab placement / Skills panel:** cross-class abilities currently land in the General tab of your spellbook instead of their proper class tab, and weapon/armor skills can look odd in the Skills panel. This is purely cosmetic — every ability works exactly as it should when cast. Fixing this requires a client-side data file patch that's planned for a future version.

---

## Troubleshooting

**Talking to the Mentor only shows "Greetings" with no options**
The Mentor's Lua script isn't loaded. Check:
```bash
docker logs ac-worldserver | grep UNBOUND
```
You should see `[UNBOUND] Prereq map built.` near the end of startup. If it's missing, re-run the installer — it will re-stage the Lua engine and rebuild.

If you've also used `wow-manage.sh`'s ALE-Kegs menu to install other community Lua mods (e.g. Black Market Auction House, Season of Discovery), v1.2.2+ shares the same `env/dist/etc/modules/lua_scripts/` directory and `mod_ale.conf` with those mods — re-running this installer won't disturb them. If you're updating from an installer older than v1.2.2, re-run it once to move `unbound_mentor.lua` to the shared location.

**Right-clicking the Mentor Stone just casts "Food" and does nothing else**
Same root cause as above — the item is recognized by the client, but the Mentor's Lua handler isn't registered. Check the worldserver log as above and re-run the installer if needed.

**A cross-class ability I bought doesn't appear / isn't usable**
Log out and back in once after buying — the spellbook needs a relog to refresh. If it's a v1.1.x-era purchase of Judgement, Summon Warhorse, Summon Felsteed, or Flight Form, see "What's New in v1.2.0" above — re-buy it from the Mentor after updating.

**My purchased spells vanished after a server restart**
This means `ValidateSkillLearnedBySpells` got reset to `1` in `worldserver.conf` (AzerothCore's default), which strips cross-class spells on login. Re-run the installer — it checks and corrects this setting.

**Installer says it can't find a compatible Dad's MMO Lab install**
The installer looks for `~/wow-server-playerbots` first, then `~/wow-unbound`, then scans your home folder for an AzerothCore-shaped directory. If your server folder has an unusual name, it'll prompt you for the path — point it at the folder containing `docker-compose.yml` and `env/`.

**The rebuild is taking forever / failed**
Check the rebuild log:
```bash
cat ~/wrath-unbound-rebuild.log
```
A first-time rebuild with a new C++ module commonly takes the full 30-90 minutes on Steam Deck hardware. If it genuinely failed, your pre-install database backup is in `~/wrath-unbound-backups/` and the log will show the compile error.

---

## Useful Commands

| What | Command |
|------|---------|
| Re-run installer (update) | `cd ~/Downloads && ./install-wrath-unbound-addon.sh` |
| Uninstall | `cd ~/Downloads && ./uninstall-wrath-unbound-addon.sh` |
| Spawn the Mentor (once) | `.npc add 900001` (in-game, GM level 3) |
| Check Wrath Unbound loaded | `docker logs ac-worldserver \| grep UNBOUND` |
| Server logs | `docker logs -f ac-worldserver` |

---

*Dad's MMO Lab · youtube.com/@DadsMmoLab · ko-fi.com/dadsmmolab*
