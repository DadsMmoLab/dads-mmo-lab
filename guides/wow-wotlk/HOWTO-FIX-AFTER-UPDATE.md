# 🔧 How to Fix Docker After a SteamOS Update

> SteamOS updates can wipe Docker and break the pacman keyring.
> This fix takes about 2 minutes and gets everything working again.

---

## The Problem

SteamOS uses a read-only image-based filesystem. When Steam updates the OS it restores the system partition to a clean state which removes any packages installed via pacman — including Docker.

This means after a SteamOS update you may see errors like:

```
docker: command not found
```

or

```
Cannot connect to the Docker daemon
```

or your WoW server just refuses to start with no explanation.

---

## 🚀 The Fix — One Command

Open Konsole in Desktop Mode and run:

```bash
chmod +x fix-after-update.sh && ./fix-after-update.sh
```

That is all. The script handles everything automatically.

---

## What the Fix Does

1. Disables the SteamOS read-only filesystem temporarily
2. Wipes and rebuilds the pacman keyring from scratch
3. Reinstalls Docker and Docker Compose via pacman
4. Re-enables the Docker service
5. Verifies Docker is working before finishing

The whole process takes about 2 minutes.

---

## After Running the Fix

Your WoW server files and character data are completely safe. The fix only reinstalls Docker — it does not touch your server folders, databases or any game files.

After the fix just start your server as normal:

```bash
cd ~/wow-server && docker compose up -d
```

Or launch from Gaming Mode as usual.

---

## How to Prevent This

You cannot prevent SteamOS from updating but you can recover quickly every time by keeping `fix-after-update.sh` somewhere easy to find.

Good places to keep it:
- Your home folder: `~/fix-after-update.sh`
- Your Downloads folder

After any SteamOS update if your server does not start just run the fix script and you are back up in 2 minutes.

---

## Still Not Working?

If the fix script runs but Docker still does not work try rebooting your Steam Deck and running the fix script one more time.

If it still fails after two attempts open an issue on GitHub and we will help:

**github.com/DadsMmoLab/dads-mmo-lab**

---

## What is Next?

- Need to start your server? See HOWTO-INSTALL.md
- Need to manage your server? See HOWTO-DESKTOP-CONTROLS-1.md

---

*Part of the Dad's MMO Lab project — offline MMO servers on Steam Deck, free forever.*

**youtube.com/@DadsMmoLab**
**github.com/DadsMmoLab/dads-mmo-lab**
