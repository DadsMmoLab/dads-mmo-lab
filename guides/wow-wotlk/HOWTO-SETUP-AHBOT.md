# 💰 How to Set Up the Auction House Bot

> The AH Bot fills all three Auction Houses with items automatically.
> It makes your server feel like a real MMO economy — buy and sell
> just like you would on a live server.

---

## 📋 Before You Start

Make sure:
- ✅ Your WoW server is installed and running
- ✅ You selected **AH Bot** during installation
- ✅ Your WoW client realmlist is set to `127.0.0.1`

---

## 🚀 Quick Start — One Command

Open Konsole in Desktop Mode and run:

```bash
chmod +x setup-ahbot.sh && ./setup-ahbot.sh
```

---

## 🗺️ What the Script Does

The AH Bot needs its own dedicated character to run the
Auction House on your behalf. The script sets this up
automatically in 4 steps.

---

### Step 1 — Creates the AH Bot Account

The script automatically creates a dedicated account:

```
Username: ahbot
Password: ahbot
```

You do not need to do anything for this step.

---

### Step 2 — You Create the Character In-Game

This is the only step that requires you to do something!

The script will show you clear instructions:

```
1. Launch WoW from Steam
2. Login with:
      Username: ahbot
      Password: ahbot
3. Create a NEW character
      Suggested name: Auctioneer
      Any race and class is fine!
4. Enter the game world
      The character must actually load in
5. Log out back to character select
```

> The script watches the database automatically.
> You do NOT need to come back to Konsole
> until after you have logged out of WoW.

---

### Step 3 — Auto-Detection

The script watches the database in the background.
The moment your new character appears it detects it
automatically — no input needed from you.

You will see:

```
Character detected: Auctioneer (GUID: 3)
```

---

### Step 4 — Auto-Configuration

The script:
- Writes the AH Bot config with your character's details
- Enables the seller bot (lists items for sale)
- Enables the buyer bot (buys items from the AH)
- Restarts the worldserver to activate everything

When it finishes you will see:

```
AUCTION HOUSE BOT IS ACTIVE!
```

---

## 💡 Tips

**Give it a few minutes after setup.** The AH Bot does not
flood the Auction House instantly — it populates gradually
over a few minutes. Check back after 5-10 minutes and you
will see items appearing.

**All three Auction Houses get populated.** Alliance, Horde
and Neutral AHs all receive items from the bot.

**Never play on the Auctioneer character.** Keep it
dedicated to the AH Bot. If you move it or delete it
the bot will stop working and you will need to run
setup-ahbot.sh again with a new character.

**The bot account does not need GM level.** It is a regular
player account — the bot handles everything automatically
in the background.

---

## ❓ Frequently Asked Questions

**The AH is still empty after setup. Is something wrong?**

Give it 5-10 minutes. The bot populates gradually. If it
is still empty after 15 minutes check that the worldserver
restarted successfully:

```bash
docker logs $(docker ps --format '{{.Names}}' | grep worldserver | head -1) | tail -20
```

Look for `ready...` near the bottom — that confirms it
restarted cleanly.

---

**I accidentally deleted the Auctioneer character. Now what?**

Just run setup-ahbot.sh again and create a new character
on the ahbot account. The script will detect the new
character and update the config automatically.

---

**Can I name the character something other than Auctioneer?**

Yes! Any name works. The script detects any character
created on the ahbot account regardless of name.

---

**Do I need to run this again after reinstalling the server?**

Yes. Each fresh install needs the AH Bot set up again
since the character data is wiped with the server.

---

## What is Next?

- Need to install a server? See HOWTO-INSTALL.md
- Need to manage your server? See HOWTO-DESKTOP-CONTROLS-1.md
- Need to create player accounts? See HOWTO-CREATE-ACCOUNTS.md

---

*Part of the Dad's MMO Lab project — offline MMO servers
on Steam Deck, free forever.*

**youtube.com/@DadsMmoLab**
**github.com/DadsMmoLab/dads-mmo-lab**
**ko-fi.com/dadsmmolab**
