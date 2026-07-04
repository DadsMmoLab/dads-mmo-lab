# 👤 How to Create Accounts

> Creating accounts takes about 60 seconds.
> Copy paste the commands below — that's all!
>
> 💡 **To paste in your terminal:** right-click → Paste, or press **Ctrl+Shift+V**

---

## Before You Start

**Open a terminal for your platform:**

| Platform | How to open a terminal |
|---|---|
| **Steam Deck (SteamOS)** | Press Steam → Power → **Switch to Desktop** → open **Konsole** from the taskbar |
| **Ubuntu / Debian / Linux Mint** | Press `Ctrl+Alt+T` or search **Terminal** in your app menu |
| **Fedora** | Press `Ctrl+Alt+T` or open **Activities** → search **Terminal** |
| **Windows 10/11 (WSL2)** | Start → search **Windows Terminal** → open it → type `wsl` → Enter |

Then make sure your server is running. Check with:

```bash
docker ps
```

If you see `ac-worldserver` and `ac-authserver` listed — you're ready!

> 💡 **Steam Deck Gaming Mode:** You can also check for **AZEROTH IS READY!** in your server launcher window.

---

## Step 1 — Open the GM Console

Copy and paste this into your terminal:

```bash
docker attach $(docker ps --format '{{.Names}}' | grep worldserver | head -1)
```

If it worked, your prompt changes to `AC>`. That means you're now talking directly to the server console.

> If nothing appears after attaching, press **Enter** once to get a fresh `AC>` prompt.

---

## Step 2 — Create Your Account

Type this exactly — replace `USERNAME` and `PASSWORD` with whatever you want:

```
account create USERNAME PASSWORD
```

Wait 2 seconds then type:

```
account set gmlevel USERNAME 3 -1
```

> `account set gmlevel ... 3 -1` gives your account administrator ("GM") powers in-game — teleport, summon items, and more. This only affects your local server.

**Example** — creating an account called "dad":
```
account create dad mypassword
account set gmlevel dad 3 -1
```

---

## Step 3 — Exit the Console Safely

Press **Ctrl+P** then immediately **Ctrl+Q**

> ⚠️ **Do NOT press Ctrl+C** — that stops the entire server!

---

## Step 4 — Done!

Log into WoW with your new username and password.

Make sure your `realmlist.wtf` is set to `127.0.0.1` if you haven't already:
- **Steam Deck users:** see [WoW-WotLK-HOWTO.md — Step 3](./WoW-WotLK-HOWTO.md#step-3--set-your-realmlist)
- **Linux users (local play):** open your WoW client folder → `Data/<locale>/realmlist.wtf` → set it to `set realmlist 127.0.0.1`
- **Windows / LAN / internet play:** see [WoW-Wotlk-NETWORKING.md](./WoW-Wotlk-NETWORKING.md)

---

## Creating More Accounts

Just repeat Steps 1–3 for each account. You can
create as many as you need — one per family member,
one for testing, whatever you like.

---

## Quick Reference — Copy Paste Ready

**Open console:**
```bash
docker attach $(docker ps --format '{{.Names}}' | grep worldserver | head -1)
```

**Create account** (replace USERNAME and PASSWORD):
```
account create USERNAME PASSWORD
account set gmlevel USERNAME 3 -1
```

**Exit console safely:**
Ctrl+P then Ctrl+Q &nbsp;&nbsp;*(never Ctrl+C)*

---

## Troubleshooting

**Console shows nothing after attaching?**
Press Enter once to get a fresh `AC>` prompt, then type.

**"Account already exists" error?**
Good news — the account is already there! Just
try logging in with that username and password.

**Login says information not valid?**
Make sure your username and password match exactly what you typed when creating the account. Try creating the account again with a simpler password (no special characters).

To reset a password, attach to the GM console and run:
```
account set password USERNAME NEWPASSWORD NEWPASSWORD
```

> Type the new password **twice**. You do not need the old password when you have GM console access.

**Server not found?**
Start your server first:
```bash
# Base WoW
cd ~/wow-server && docker compose up -d

# NPCBots
cd ~/wow-server-npcbots && docker compose up -d

# Playerbots
cd ~/wow-server-playerbots && docker compose up -d
```

---

## Related Guides

- [Server Controls Part 1](./WoW-WotLK-CONTROLS-1.md) — start/stop server, account management
- [Server Controls Part 2](./WoW-WotLK-CONTROLS-2.md) — GM console commands, troubleshooting
- [Networking Guide](./WoW-Wotlk-NETWORKING.md) — let friends connect (LAN & internet)
- [Full Install Guide — Steam Deck](./WoW-WotLK-HOWTO.md) — Steam Deck installer walkthrough

---

*Part of the Dad's MMO Lab project — free forever.*

**youtube.com/@DadsMmoLab**
**github.com/DadsMmoLab/dads-mmo-lab**