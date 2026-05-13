# HOWTO — WoW Module Manager

Dad's MMO Lab — beginner-friendly guide

---

## What is this thing?

`manage-wow-modules.sh` is a separate tool that helps you **add, remove, and manage modules** on an AzerothCore WoW install **after** you've already done the main install with `install-wow.sh`.

Modules are little add-ons that change how the server works. They're optional. Some are wildly fun (Transmog, AoE Loot), some are serious quality-of-life upgrades (Solocraft, AutoBalance), and one is a true sidekick experience (the AH Bot, which actually puts items up for auction so the in-game economy isn't empty).

You don't need this tool to *play* WoW. You only need it when you want to **change what modules your server is running**.

---

## What you need before running this

1. ✅ You already used `install-wow.sh` to install one of these:
   - **AzerothCore Base** (clean Wrath of the Lich King)
   - **NPCBots** (bot NPCs you can hire)
   - **Playerbots** (full AI players that act like real players)
2. ✅ Your server is installed in one of these standard locations:
   - `~/wow-server`
   - `~/wow-npcbots-server`
   - `~/wow-playerbots-server`
3. ✅ You have Docker installed (`install-wow.sh` does this for you)

That's it. The manager will detect what kind of install you have automatically.

---

## ⭐ Step 1 — Get the file onto your Steam Deck

Download `manage-wow-modules.sh` from wherever Dad's MMO Lab is hosted (GitHub, the YouTube description, wherever you got it). On Steam Deck, the file will land in your `~/Downloads` folder by default.

---

## ⭐ Step 2 — Open Konsole

Konsole is the terminal app. On Steam Deck in Desktop Mode:

1. Tap the **🦅 Steam Deck logo** in the bottom-left corner
2. Type `Konsole` in the search box
3. Tap **Konsole** when it shows up

A black window with text appears. That's your terminal.

---

## ⭐ Step 3 — Make the file runnable (chmod)

You have to tell Linux that this file is allowed to run. We do that with a command called `chmod`.

**Pick the option that matches where your file is:**

### Option A — File is in your Downloads folder (most common)

Tap into Konsole and paste this exact command (Ctrl+Shift+V to paste in Konsole):

```
cd ~/Downloads && chmod +x manage-wow-modules.sh
```

Press Enter. If nothing prints back, **that means it worked.** Linux is quiet when it succeeds.

### Option B — File is in your home folder (`~`)

```
chmod +x ~/manage-wow-modules.sh
```

### Option C — File is on the Desktop

```
chmod +x ~/Desktop/manage-wow-modules.sh
```

### Option D — You moved the file somewhere weird and can't remember

Type this to find it:

```
find ~ -name "manage-wow-modules.sh" 2>/dev/null
```

It'll print the path. Use that path with chmod. Example:

```
chmod +x /home/deck/SomeFolder/manage-wow-modules.sh
```

---

## ⭐ Step 4 — Run the manager

### If you ran the chmod from Option A (Downloads):

```
./manage-wow-modules.sh
```

You're already in the Downloads folder because of the `cd ~/Downloads` in Step 3, so this works.

### If you ran the chmod from Option B (home folder):

```
~/manage-wow-modules.sh
```

### If you ran the chmod from Option C (Desktop):

```
~/Desktop/manage-wow-modules.sh
```

### If the manager can't find your server install

You'll see a message like "No AzerothCore install found." This means the manager looked in the standard places and came up empty.

**Don't panic.** Either:
- Your `install-wow.sh` run didn't finish (re-run it)
- You installed WoW in a non-standard folder

If the second one, contact me on YouTube and I'll add support for your folder layout in a future update.

---

## ⭐ Step 5 — The first-run welcome

The very first time you run the manager on a given install, you'll see a friendly welcome screen. **Read it.** It explains:

- Nothing changes until you explicitly pick an action
- Menu options 3, 6, and 10 are read-only — totally safe to explore
- Adding modules will rebuild the worldserver (30-90 minutes on Steam Deck)
- The repair function is non-destructive (never drops database tables)

Press Enter to dismiss. This screen only appears once per install, so don't worry about it cluttering things up.

---

## 🎮 The menu — what does each option do?

When the manager runs, you'll see a menu like this:

```
── Modules ──
    1) Add modules
    2) Remove modules
    3) List installed modules
    4) Configure / reconfigure AH Bot
    5) Rebuild worldserver

── Server Controls ──
    6) Server status
    7) Start server
    8) Stop server
    9) Restart server
   10) View worldserver logs
   11) Attach to worldserver console

── Troubleshooting ──
   12) Repair install state (clear stuck SQL update tracking)

    Q) Quit
```

Here's what each one does in plain English:

### Modules section

**Option 1 — Add modules**
Shows you a list of available modules. Pick the ones you want, the manager downloads them, then triggers a worldserver rebuild. This takes 30-90 minutes. **Plug your Steam Deck in.** Put it on a flat surface so the fan can breathe.

Available modules:
- **AH Bot** — fills the Auction House with items so the economy feels alive
- **Solocraft** — scales dungeons and raids down for solo play
- **AoE Loot** — loots all corpses around you in one tap
- **Learn Spells on Levelup** — auto-trains spells when you level
- **Individual Progression** — play through Vanilla → TBC → WotLK content in order
- **Auto Balance** — dynamically scales mob difficulty to your group size
- **Transmogrification** — change how your gear looks without changing stats
- **1v1 Arena** — solo PvP arena queues

**Option 2 — Remove modules**
Shows you what's installed and lets you uninstall any. Also triggers a worldserver rebuild.

**Option 3 — List installed modules**
Just shows you what's currently installed. **Read-only. Totally safe.** Use this to check your current loadout.

**Option 4 — Configure / reconfigure AH Bot**
Tweaks how aggressive the AH Bot is. How many items it puts up at a time, item levels to use, that kind of thing. Only available if AH Bot is installed.

**Option 5 — Rebuild worldserver**
Manually triggers a rebuild. You shouldn't normally need this — adding/removing modules triggers it automatically. Use this if something weird happened and your modules aren't loading correctly.

### Server Controls section

**Option 6 — Server status**
Shows which containers (worldserver, authserver, database, etc) are running. **Read-only.** Use this when you're not sure if your server is on.

**Option 7 — Start server**
Starts your WoW server. Use this if you stopped it and want to play again.

**Option 8 — Stop server**
Stops your WoW server. Use this if you want to free up memory for something else.

**Option 9 — Restart server**
Stops and starts. Use this if something's gone wonky and you want a fresh boot.

**Option 10 — View worldserver logs**
Watches the worldserver's output live. **Read-only.** Press Ctrl+C to stop watching. Useful if you want to see what's happening behind the scenes (or if something's broken and you want to see error messages).

**Option 11 — Attach to worldserver console**
Drops you INTO the worldserver's command prompt where you can type GM commands like `account create` or `.server info`.

**🚨 IMPORTANT:** To exit the attached console, press **Ctrl+P then Ctrl+Q**. **Do NOT press Ctrl+C** — that stops the entire server!

### Troubleshooting section

**Option 12 — Repair install state**
This fixes a specific failure: when `ac-db-import` keeps crashing because it thinks SQL updates were already applied but they're actually broken. It clears the tracking rows so the next start re-applies them.

**Important:** This is **non-destructive.** It never drops your database tables. It only clears the "I already did this update" tracker so AzerothCore re-checks everything.

Only use this if Option 6 shows `ac-db-import` failing repeatedly.

---

## 🛠️ Common things you might want to do

### "I want bots that auction stuff so my game has a real economy"

1. Run the manager
2. Pick option **1** (Add modules)
3. Select **mod-ah-bot**
4. Wait 30-90 minutes for the rebuild
5. After it's done, pick option **4** (Configure AH Bot) to tune it

### "I just want fancy gear appearances"

1. Run the manager
2. Pick option **1** (Add modules)
3. Select **mod-transmog**
4. Wait for rebuild

### "I want to solo dungeons that normally need 5 people"

1. Run the manager
2. Pick option **1** (Add modules)
3. Select **mod-solocraft** and probably **mod-autobalance** too
4. Wait for rebuild

### "My server isn't starting and I see ac-db-import failing"

1. Run the manager
2. Pick option **12** (Repair install state)
3. Confirm
4. Pick option **9** (Restart server)
5. Check option **6** (Server status) after a minute or two

### "I want to see what's currently installed before doing anything"

1. Run the manager
2. Pick option **3** (List installed modules)
3. Pick option **6** (Server status) for good measure

Both are read-only. Nothing changes.

---

## ❓ Things that go wrong (and how to fix them)

### "Permission denied" when running the manager

You forgot Step 3. Run the chmod command again. Pick the option that matches where the file is.

### "No such file or directory"

You're running the manager from the wrong folder, OR the file isn't where you think it is. Use the `find` command from Step 3, Option D to locate it.

### Manager says "No AzerothCore install found"

The manager looks for these folders:
- `~/wow-server`
- `~/wow-npcbots-server`
- `~/wow-playerbots-server`

If your WoW server is in one of these and the manager still can't find it, something might be off with your install. Re-run `install-wow.sh`.

### The rebuild is taking forever

That's normal. **Plug your Steam Deck in.** Put it on a flat surface. Compiling AzerothCore from source on Steam Deck takes **30-90 minutes** because the Steam Deck CPU isn't a workstation. This is one-time per module change.

### Steam Deck is getting hot during rebuild

Also normal. Put it on a flat hard surface so the fan can breathe. Avoid blankets, beds, and laps. If you're worried, run option 12 and step away — let it finish.

### I added a module but I don't see its effects in game

After the rebuild finishes, the server has to fully restart. Try option 6 (Server status) — wait until everything shows `Running`. Some modules also need configuration through `worldserver.conf` files. The manager will tell you when this is needed.

### I attached to the worldserver console and now I can't get out

**Press Ctrl+P, then immediately press Ctrl+Q.**

Do NOT press Ctrl+C. Ctrl+C stops the server.

---

## 🆘 If everything is broken

In order from gentlest to most aggressive:

1. **Option 12** (Repair install state) — fixes most SQL tracking issues
2. **Option 9** (Restart server) — handles transient weirdness
3. **Stop the server (option 8), wait 30 seconds, start it again (option 7)** — sometimes Docker just needs a beat
4. **Reboot the Steam Deck** — if all else fails

If nothing works, drop a comment on the YouTube video with:
- Which install type (Base, NPCBots, Playerbots)
- What you tried
- What option 6 (Server status) shows

I'll get back to you.

---

## 📌 Quick reference — the commands you actually need

Copy-paste these in Konsole. **Always pick the one that matches where your file is.**

**Make file runnable (Downloads folder):**
```
cd ~/Downloads && chmod +x manage-wow-modules.sh
```

**Run the manager (after running the above):**
```
./manage-wow-modules.sh
```

**Make file runnable (home folder):**
```
chmod +x ~/manage-wow-modules.sh
```

**Run the manager (from home folder):**
```
~/manage-wow-modules.sh
```

**Make file runnable (Desktop):**
```
chmod +x ~/Desktop/manage-wow-modules.sh
```

**Run the manager (from Desktop):**
```
~/Desktop/manage-wow-modules.sh
```

**Find the file if you lost it:**
```
find ~ -name "manage-wow-modules.sh" 2>/dev/null
```

---

That's everything. Have fun, dad. ⚔️
