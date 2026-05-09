# 🗑️ How to Uninstall — WoW Server Suite

> The uninstaller is safe, smart, and asks before deleting anything.
> It detects which servers you have installed and lets you choose.

---

## 🚀 Quick Start

Open Konsole in Desktop Mode and run:

```bash
chmod +x uninstall.sh && ./uninstall.sh
```

---

## 🗺️ What the Uninstaller Does

The uninstaller automatically detects which servers you have installed and builds a menu:

```
Which server do you want to uninstall?

1) Base WoW Server      (~/wow-server)
2) NPCBots WoW Server   (~/wow-server-npcbots)
3) Playerbots WoW       (~/wow-server-playerbots)
4) ALL servers
```

Only the servers you actually have installed appear in the menu.

---

## 💾 Backup First!

Before uninstalling the script asks if you want to back up your character data:

```
Do you want to back up your character data first? (y/n)
```

Always say **y** unless you are absolutely sure you do not want your characters.

The backup saves to:
```
~/wow-server-backup-YYYYMMDD_HHMMSS/full_server_backup.sql
```

Keep this file somewhere safe. You can restore it after reinstalling.

> ⚠️  The database must be running to create a backup.
> If it is not running the script will warn you and ask
> whether you want to continue without a backup.

---

## 🔒 Safety Confirmations

The uninstaller has multiple safety steps before deleting anything:

```
Are you absolutely sure you want to uninstall? (y/n)
```

Then:

```
Last chance — type DELETE to confirm:
```

You must type `DELETE` exactly to proceed. This prevents accidents.

---

## 💿 Keep Client Data Option

The uninstaller also asks whether to keep your client data volumes:

```
Keep client data volumes to speed up future reinstalls? (y/n)
```

**Say yes** unless you need a completely clean slate. Client data
is the map and DBC files downloaded during install — it never
changes between reinstalls and takes 30+ minutes to re-download.
Keeping it makes future reinstalls much faster.

---

## 🗑️ What Gets Removed

For each selected server:

- All Docker containers for that server
- All Docker images downloaded for that server
- The server folder and all its contents
- The database volume with all character data
- The Gaming Mode launcher script

**What is NOT touched:**
- Your WoW 3.3.5a client files
- Docker itself
- Any other server versions you did not select
- Any other projects on your system
- Client data volumes (if you chose to keep them)

---

## 🔄 Reinstalling After Uninstall

After uninstalling you can reinstall fresh at any time:

```bash
chmod +x install-wow.sh && ./install-wow.sh
```

The wizard will walk you through everything again from scratch.

---

## 💡 Restoring a Backup

If you backed up before uninstalling and want to restore your characters after reinstalling:

1. Reinstall the server using install-wow.sh
2. Wait for the server to fully start
3. Run this command with your backup file:

```bash
DB=$(docker ps --format '{{.Names}}' | grep -iE "database" | head -1)
docker exec -i $DB mysql -uroot -ppassword < ~/wow-server-backup-YYYYMMDD_HHMMSS/full_server_backup.sql
```

Replace the date in the filename with your actual backup date.

---

## What is Next?

- Want to reinstall? See HOWTO-INSTALL.md
- Want to manage your server? See HOWTO-DESKTOP-CONTROLS-1.md

---

*Part of the Dad's MMO Lab project — offline MMO servers on Steam Deck, free forever.*

**youtube.com/@DadsMmoLab**
**github.com/DadsMmoLab/dads-mmo-lab**
