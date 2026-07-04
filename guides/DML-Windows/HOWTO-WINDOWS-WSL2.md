# 🪟 How to Install on Windows — WSL2 Guide

> Run offline MMO servers on any Windows 10 or 11 PC.
> No Steam Deck required!
>
> **Estimated time:** 30–45 minutes setup, then your game's install time
> **Difficulty:** Beginner friendly — just copy and paste!

---

## 📋 What You Need

- ✅ Windows 10 (version 2004 or later) or Windows 11
- ✅ At least **15GB** free storage per game (30GB+ for WoW with Playerbots)
- ✅ Your game client already installed on Windows (WoW 3.3.5a, etc.)
- ✅ Internet connection for the initial download
- ✅ A PC made in the last 8 years (virtualization support required)

> **Not sure which Windows version you have?**
> Press **Windows key + R**, type `winver`, press Enter.
> You need version 2004 (build 19041) or higher.

---

## 🎮 What Games Can I Install?

All of the following work on Windows via WSL2:

| Game | Installer | Install time |
|------|-----------|-------------|
| ⚔️ WoW Vanilla 1.12 | `install-wow-vanilla.sh` | ~30 min |
| ⚔️ WoW The Burning Crusade 2.4.3 | `install-wow-tbc.sh` | ~30 min |
| ⚔️ WoW Wrath of the Lich King 3.3.5a | `install-wow-wotlk.sh` | ~30 min |
| 🏃 RuneScape 2009 | `install-runescape.sh` | ~5 min |

---

## 🧠 What is WSL2 and Why Do We Need It?

The game servers run on Linux. Your PC runs Windows. WSL2 (Windows Subsystem for Linux 2) is a feature built into Windows that lets you run a full Linux environment right inside Windows — no rebooting, no dual boot, no virtual machine headaches.

Think of it like a Linux terminal that lives inside Windows. The servers run there. Your game client stays on Windows and connects to it.

---

## 🚀 PART 1 — Enable WSL2

### Step 1 — Open PowerShell as Administrator

Press the **Windows key**, type `PowerShell`, right-click **Windows PowerShell** and click **Run as administrator**.

Click **Yes** when Windows asks for permission.

---

### Step 2 — Install WSL2

In the PowerShell window, paste this and press Enter:

```powershell
wsl --install
```

Windows will install WSL2 and Ubuntu automatically. This takes about 5 minutes.

> If you see a message saying WSL is already installed, skip to Step 3.

---

### Step 3 — Restart Your PC

When the install finishes, **restart your PC**.

After restarting, Ubuntu will finish setting up automatically and a terminal window will open.

---

### Step 4 — Create Your Linux Username and Password

Ubuntu will ask you to create a username and password.

```
Enter new UNIX username: yourname
Enter new UNIX password:
```

**Important:**
- Use a simple lowercase username with no spaces (example: `deck` or `dad`)
- The password won't show as you type — that is normal!
- Remember this password — the installer will ask for it when it needs admin permission

---

### Step 5 — Verify WSL2 is Running

In PowerShell (not the Ubuntu window), run:

```powershell
wsl --list --verbose
```

You should see Ubuntu listed with **VERSION 2**:

```
  NAME      STATE           VERSION
* Ubuntu    Running         2
```

If it says VERSION 1, run this to upgrade:

```powershell
wsl --set-version Ubuntu 2
```

---

### Step 6 — (Optional but Recommended) Configure WSL2 Memory

By default WSL2 can consume a lot of RAM. Create a config file to keep it reasonable.

Open Notepad and save this as `C:\Users\YourName\.wslconfig` (replace YourName with your Windows username):

```ini
[wsl2]
memory=6GB
processors=4
```

Adjust `memory` to half your system RAM (e.g. `4GB` on an 8GB machine, `8GB` on a 16GB machine). Restart WSL2 after saving:

```powershell
wsl --shutdown
```

Then re-open Ubuntu from the Start menu.

---

## 🐳 PART 2 — Install Docker Inside WSL2

Everything from here runs inside the **Ubuntu terminal**, not PowerShell. Open Ubuntu from the Start menu if it is not already open.

---

### Step 7 — Update Ubuntu

```bash
sudo apt-get update && sudo apt-get upgrade -y
```

Enter your password when asked. This takes 1–2 minutes.

---

### Step 8 — Install Docker

Run these commands one at a time:

```bash
sudo apt-get install -y ca-certificates curl gnupg lsb-release
```

```bash
sudo mkdir -p /etc/apt/keyrings

curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
    sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
```

```bash
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
```

```bash
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
```

---

### Step 9 — Start Docker and Configure Your User

```bash
sudo service docker start
sudo usermod -aG docker $USER
newgrp docker
```

---

### Step 10 — Verify Docker is Working

```bash
docker ps
```

You should see an empty table with headers — no errors. That means Docker is running correctly!

> If you see `Cannot connect to the Docker daemon`, run:
> ```bash
> sudo service docker start
> ```
> then try `docker ps` again.

---

### Step 11 — Make Docker Start Automatically

WSL2 does not use systemd like a full Linux system, so we need to tell Docker to start when Ubuntu opens:

```bash
echo 'sudo service docker start > /dev/null 2>&1' >> ~/.bashrc
```

Docker will now start automatically every time you open the Ubuntu terminal.

---

## ⚔️ PART 3 — Install Your Game Server

### Step 12 — Download Your Installer

Pick your game and run the matching download command in the Ubuntu terminal:

**WoW Wrath of the Lich King 3.3.5a:**
```bash
cd ~ && curl -O https://raw.githubusercontent.com/DadsMmoLab/dads-mmo-lab/main/guides/wow-wotlk/install-wow-wotlk.sh
chmod +x install-wow-wotlk.sh
```

**WoW The Burning Crusade 2.4.3:**
```bash
cd ~ && curl -O https://raw.githubusercontent.com/DadsMmoLab/dads-mmo-lab/main/guides/wow-tbc/install-wow-tbc.sh
chmod +x install-wow-tbc.sh
```

**WoW Vanilla 1.12:**
```bash
cd ~ && curl -O https://raw.githubusercontent.com/DadsMmoLab/dads-mmo-lab/main/guides/wow-vanilla/install-wow-vanilla.sh
chmod +x install-wow-vanilla.sh
```

**RuneScape 2009:** See [Part 7 — RuneScape on WSL2](#-part-7--runescape-2009-on-wsl2) — it has its own section below.

---

### Step 13 — Run the Installer

Run your installer. Replace the filename with whichever you downloaded:

```bash
./install-wow-wotlk.sh
```

The wizard is identical to the Steam Deck experience. Follow the prompts.

> The wizard detects Linux automatically. Ignore any references to SteamOS — those steps are skipped on Ubuntu.

---

### Step 14 — Wait for Installation

| Game | Time |
|------|------|
| WoW Vanilla | ~30 minutes |
| WoW TBC | ~30 minutes |
| WoW WotLK | ~30 minutes |

Keep your PC awake and connected during install.

When you see:

```
✅ Installation complete!
```

You are ready for the next step!

---

## 👤 PART 4 — Your Account (WoW)

The installer automatically creates an **admin / admin** account with GM Level 3. You can use that to log in immediately — no extra setup required.

To create additional accounts, open the GM console:

```bash
docker attach $(docker ps --format '{{.Names}}' | grep -i "worldserver\|mangosd" | head -1)
```

You will see server output scrolling. Type your commands:

```
account create USERNAME PASSWORD PASSWORD
account set gmlevel USERNAME 3 -1
```

**Example:**
```
account create dad mypassword mypassword
account set gmlevel dad 3 -1
```

Exit the console with **Ctrl+P** then **Ctrl+Q**.

> ⚠️ Never press Ctrl+C in the console — that stops the server!

---

## 🌐 PART 5 — Connect Your WoW Client to the Server

This is the most important difference from the Steam Deck guide. Your WoW client is on Windows; your server is in WSL2. They are not on the same `localhost`.

### Which method works for you?

**Check your WSL2 version:**
```powershell
wsl --version
```

| Your setup | What to use |
|------------|-------------|
| Windows 11 22H2 or later | Use the [Mirrored Networking](#option-c--mirrored-networking-windows-11-22h2-recommended) method — `127.0.0.1` works |
| Windows 10 or Windows 11 older | Use the [WSL2 IP method](#option-a--wsl2-ip-address-always-works) |

---

### Option A — WSL2 IP Address (always works)

In the Ubuntu terminal, run:

```bash
hostname -I | awk '{print $1}'
```

This prints an IP like `172.24.144.1`. Use that IP in your `realmlist.wtf`.

On your **Windows PC**, find your WoW client folder and open `realmlist.wtf` in Notepad. Change it to:

```
set realmlist 172.24.144.1
```

> ⚠️ This IP can change every time WSL2 restarts. Check it each session if the connection stops working. See the FAQ for a workaround.

---

### Option B — localhost forwarding (recent WSL2, easy check)

If your WSL2 was installed or updated recently (2023 or later), localhost forwarding may already be enabled by default. Try this first:

Set your `realmlist.wtf` to:

```
set realmlist 127.0.0.1
```

Launch WoW and try to connect. If it works — you're done, use this going forward. If not, use Option A or C.

---

### Option C — Mirrored Networking (Windows 11 22H2+, recommended)

This makes WSL2 share the same network as Windows, so `127.0.0.1` always works and never changes.

On your **Windows PC** (not in Ubuntu), open Notepad and save this as `C:\Users\YourName\.wslconfig`:

```ini
[wsl2]
memory=6GB
networkingMode=mirrored
```

Then restart WSL2:
```powershell
wsl --shutdown
```

Reopen Ubuntu, start your server, and set your `realmlist.wtf` to:

```
set realmlist 127.0.0.1
```

This is now permanent — the realmlist never needs updating again.

---

### Step 15 — Launch WoW and Log In

Launch WoW from your Windows desktop as normal.

Log in with `admin` / `admin` (or the account you created in Part 4).

**You should be in Azeroth! ⚔️**

---

## 🖥️ PART 6 — Starting and Stopping Your Server

Unlike the Steam Deck, there is no Gaming Mode launcher on Windows. Here is how to manage your server.

### Starting Your Server

Open the Ubuntu terminal and run the command for your game:

```bash
# WoW WotLK
cd ~/wow-server-playerbots && docker compose up -d

# WoW TBC
cd ~/wow-tbc-server && docker compose up -d

# WoW Vanilla
cd ~/wow-vanilla-server && docker compose up -d
```

Watch the server start:

```bash
docker logs -f $(docker ps --format '{{.Names}}' | grep -i "worldserver\|mangosd" | head -1)
```

When you see `ready...` in the logs, launch WoW. Press **Ctrl+C** to stop watching — the server keeps running.

---

### Stopping Your Server

Always stop the server before closing Ubuntu or shutting down your PC:

```bash
# WoW WotLK
cd ~/wow-server-playerbots && docker compose down

# WoW TBC
cd ~/wow-tbc-server && docker compose down

# WoW Vanilla
cd ~/wow-vanilla-server && docker compose down
```

> ⚠️ Never just close the Ubuntu window while the server is running. Always run `docker compose down` first to save character data properly.

---

### Check Server Status

```bash
docker ps
```

Containers listed = server is running. Empty table = server is stopped.

---

## 🏃 PART 7 — RuneScape 2009 on WSL2

RuneScape uses Java instead of Docker, which makes things slightly different on WSL2.

### Install the Server

Download and run the installer:

```bash
cd ~ && curl -O https://raw.githubusercontent.com/DadsMmoLab/dads-mmo-lab/main/guides/runescape/install-runescape.sh
chmod +x install-runescape.sh
./install-runescape.sh
```

The installer handles Java 11, the bundled MySQL, and the server setup automatically.

---

### Connect Your Client

RuneScape's server listens on port **43594**. Your client (on Windows) needs to point at the WSL2 IP or `127.0.0.1`.

The 2009scape project has a **Windows client** — use that instead of the Linux `client.jar`:

1. Download the 2009scape client for Windows from [2009scape.org](https://2009scape.org)
2. In the client's settings, set the server IP to your WSL2 IP (from `hostname -I | awk '{print $1}'`)
3. Or use `127.0.0.1` if you've set up mirrored networking (Option C above)

---

### Starting and Stopping RuneScape

RuneScape does not use Docker — it runs as a native Java process in WSL2.

**Start:**
```bash
cd ~/runescape-server
export LD_LIBRARY_PATH="$HOME/runescape-server/database/lib"
# Start the bundled MySQL
database/bin/mysqld --console --skip-grant-tables \
    --lc-messages-dir="./share/" --datadir="./data" &
sleep 5
# Start management server and game server
/usr/lib/jvm/java-11-openjdk/bin/java -jar ms.jar &
/usr/lib/jvm/java-11-openjdk/bin/java -jar server.jar &
```

**Stop:**
```bash
pkill -f "runescape-server/server.jar"
pkill -f "runescape-server/ms.jar"
pkill -f "runescape-server/database/bin/mysqld"
```

> The Steam Deck launchers (`runescape-launcher.sh`) can also be run directly in WSL2 — they handle all of this automatically.

---

### Using the Linux Client on Windows 11 (WSLg)

Windows 11 includes **WSLg** — a built-in display server that lets Linux GUI apps open as Windows. If you are on Windows 11 (build 22000 or later), you can run the Linux `client.jar` directly:

```bash
/usr/lib/jvm/java-11-openjdk/bin/java -jar ~/runescape-server/client.jar
```

A Java window will open on your Windows desktop — no additional setup required.

> On Windows 10, WSLg is not available. Use the Windows native 2009scape client instead.

---

## ❓ Frequently Asked Questions

---

**The WSL2 IP address keeps changing. How do I fix this?**

Two options:

**Option 1 — Mirrored networking** (Windows 11 22H2+ only): Set `networkingMode=mirrored` in `.wslconfig`. After that, use `127.0.0.1` forever — it never changes. See Part 5.

**Option 2 — Hosts file trick** (Windows 10 or older Windows 11): Add this to `C:\Windows\System32\drivers\etc\hosts`:
```
172.24.144.1    wowserver.local
```
Set your realmlist to `set realmlist wowserver.local`. When the IP changes, update only the hosts file — your realmlist stays the same.

---

**Can I use 127.0.0.1 directly?**

**Windows 11 22H2+ with mirrored networking:** Yes, always.

**Recent WSL2 on Windows 10 or older Windows 11:** Sometimes — try it and see. If it works, use it. If not, use the WSL2 IP.

---

**Docker stops working after I restart my PC**

WSL2 suspends when Windows restarts. Open Ubuntu and run:

```bash
sudo service docker start
```

If you completed Step 11, this should happen automatically when you open Ubuntu. If not, re-run:

```bash
echo 'sudo service docker start > /dev/null 2>&1' >> ~/.bashrc
```

---

**The server starts but WoW says "unable to connect"**

Check three things in order:

1. **Is the server ready?**
```bash
docker logs $(docker ps --format '{{.Names}}' | grep -i "worldserver\|mangosd" | head -1) | tail -20
```
Look for `ready...` near the bottom.

2. **Is your IP right?**
```bash
hostname -I | awk '{print $1}'
```
Compare this to what is in your `realmlist.wtf`. They must match exactly.

3. **Is Docker even running?**
```bash
docker ps
```
If this fails, run `sudo service docker start` and try again.

---

**Windows Firewall is blocking the connection**

If the IP and realmlist are correct but WoW still can't connect, Windows Firewall may be blocking the ports.

Open **Windows Defender Firewall** → **Allow an app through firewall** → check if Ubuntu or WSL is listed and allowed. If not, add it manually.

Alternatively, temporarily turn off the Windows Firewall to test — if WoW connects, firewall is the issue.

---

**WSL2 is using too much RAM / disk**

Add a `.wslconfig` file at `C:\Users\YourName\.wslconfig` to cap RAM:

```ini
[wsl2]
memory=4GB
processors=2
```

Restart WSL2 with `wsl --shutdown` in PowerShell for it to take effect.

---

**Can I run this on Windows 10?**

Yes. You need Windows 10 version 2004 (build 19041) or later. Press **Win+R**, type `winver` to check. If you are on an older version, run Windows Update first.

Note: Windows 10 does not support mirrored networking or WSLg (GUI apps). Use the WSL2 IP method for the realmlist, and use Windows native clients for games.

---

**Does this work on a laptop?**

Yes! Any Windows laptop made in the last 8 years works fine. Keep your laptop plugged in during any compilation step — the Playerbots installer compiles C++ source and takes 2–4 hours on older machines.

---

**Can I have multiple game servers installed?**

Yes! Each installs to its own folder and they never conflict. Just run one at a time.

```
~/wow-server-playerbots    WoW WotLK
~/wow-tbc-server           WoW TBC
~/wow-vanilla-server       WoW Vanilla
~/runescape-server         RuneScape 2009
```

---

**Do I need to re-run the installer after a Windows Update?**

No — your Ubuntu environment and game server files survive Windows updates. WSL2 itself may update, but your data inside it is not affected.

---

## 📋 Quick Reference Card

**Open Ubuntu:** Start menu → Ubuntu

**Start Docker (if needed):**
```bash
sudo service docker start
```

**Get your WSL2 IP:**
```bash
hostname -I | awk '{print $1}'
```

**Start WoW server:**
```bash
# WotLK
cd ~/wow-server-playerbots && docker compose up -d
# TBC
cd ~/wow-tbc-server && docker compose up -d
# Vanilla
cd ~/wow-vanilla-server && docker compose up -d
```

**Watch server start:**
```bash
docker logs -f $(docker ps --format '{{.Names}}' | grep -i "worldserver\|mangosd" | head -1)
```

**Stop WoW server:**
```bash
cd ~/wow-server-playerbots && docker compose down
```

**Check server status:**
```bash
docker ps
```

**Open GM console:**
```bash
docker attach $(docker ps --format '{{.Names}}' | grep -i "worldserver\|mangosd" | head -1)
```
Exit with **Ctrl+P** then **Ctrl+Q**

**Restart WSL2 (from PowerShell):**
```powershell
wsl --shutdown
```

---

## 🗺️ What's Next?

- **Need to create more accounts?** See `HOWTO-CREATE-ACCOUNTS.md` in the guides folder
- **Want the full game list?** Everything is at [github.com/DadsMmoLab/dads-mmo-lab](https://github.com/DadsMmoLab/dads-mmo-lab)
- **Video walkthroughs:** [youtube.com/@DadsMmoLab](https://youtube.com/@DadsMmoLab)

---

*Part of the Dad's MMO Lab project — offline MMO servers, free forever. No Steam Deck required.*

**youtube.com/@DadsMmoLab**
**github.com/DadsMmoLab/dads-mmo-lab**
**ko-fi.com/dadsmmolab**
