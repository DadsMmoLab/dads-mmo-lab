# 🪟 How to Install on Windows — WSL2 Guide

> Run your offline WoW server on any Windows 10 or 11 PC.
> No Steam Deck required!
>
> **Estimated time:** 45-60 minutes (Base WoW)
> **Difficulty:** Beginner friendly — just copy and paste!

---

## 📋 What You Need

- ✅ Windows 10 (version 2004 or later) or Windows 11
- ✅ At least **15GB** free storage (30GB+ for Playerbots)
- ✅ A WoW 3.3.5a client already installed on your Windows PC
- ✅ Internet connection for the initial download
- ✅ A PC that was made in the last 8 years (virtualization support required)

> **Not sure which Windows version you have?**
> Press **Windows key + R**, type `winver`, press Enter.
> You need version 2004 (build 19041) or higher.

---

## 🧠 What is WSL2 and Why Do We Need It?

The WoW server runs on Linux. Your PC runs Windows. WSL2
(Windows Subsystem for Linux 2) is a feature built into
Windows that lets you run a full Linux environment right
inside Windows — no rebooting, no dual boot, no virtual
machine headaches.

Think of it like a Linux terminal that lives inside Windows.
The server runs there. Your WoW client stays on Windows
and connects to it just like on the Steam Deck.

---

## 🚀 PART 1 — Enable WSL2

### Step 1 — Open PowerShell as Administrator

Press the **Windows key**, type `PowerShell`, right-click
**Windows PowerShell** and click **Run as administrator**.

Click **Yes** when Windows asks for permission.

---

### Step 2 — Install WSL2

In the PowerShell window, paste this and press Enter:

```powershell
wsl --install
```

Windows will install WSL2 and Ubuntu automatically.
This takes about 5 minutes.

> If you see an error saying WSL is already installed,
> skip to Step 3.

---

### Step 3 — Restart Your PC

When the install finishes, **restart your PC**.

After restarting, Ubuntu will finish setting up automatically
and a terminal window will open.

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
- Remember this password — you will need it when the
  installer asks for `sudo` permission

---

### Step 5 — Verify WSL2 is Working

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

## 🐳 PART 2 — Install Docker Inside WSL2

Everything from here runs inside the **Ubuntu terminal**,
not PowerShell. Open Ubuntu from the Start menu if it
is not already open.

---

### Step 6 — Update Ubuntu

```bash
sudo apt-get update && sudo apt-get upgrade -y
```

Enter your password when asked. This takes 1-2 minutes.

---

### Step 7 — Install Docker Dependencies

```bash
sudo apt-get install -y \
    ca-certificates \
    curl \
    gnupg \
    lsb-release
```

---

### Step 8 — Add Docker's Official Repository

```bash
sudo mkdir -p /etc/apt/keyrings

curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
    sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
```

---

### Step 9 — Install Docker Engine

```bash
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
```

This takes 2-3 minutes.

---

### Step 10 — Start Docker and Set It Up

```bash
sudo service docker start
```

Then add yourself to the docker group so you do not need
`sudo` for every docker command:

```bash
sudo usermod -aG docker $USER
```

Then apply the group change immediately:

```bash
newgrp docker
```

---

### Step 11 — Verify Docker is Working

```bash
docker ps
```

You should see an empty table with headers — no errors.
That means Docker is running correctly!

> If you see `Cannot connect to the Docker daemon` run:
> ```bash
> sudo service docker start
> ```
> then try `docker ps` again.

---

### Step 12 — Make Docker Start Automatically

WSL2 does not have systemctl like a full Linux system,
so we need to tell Docker to start when Ubuntu opens.

```bash
echo 'sudo service docker start > /dev/null 2>&1' >> ~/.bashrc
```

Docker will now start automatically every time you open
the Ubuntu terminal.

---

## ⚔️ PART 3 — Install Your WoW Server

### Step 13 — Download the Installer

```bash
cd ~ && curl -O https://raw.githubusercontent.com/DadsMmoLab/dads-mmo-lab/main/guides/wow-wotlk/install-wow.sh
chmod +x install-wow.sh
```

---

### Step 14 — Run the Wizard

```bash
./install-wow.sh
```

The wizard is identical to the Steam Deck experience.
Follow the prompts to choose your server type and modules.

**Recommended for first time:** Choose **Base WoW** — it
is the fastest install and easiest to get running.

> The wizard will detect Linux automatically.
> Ignore any references to SteamOS — those steps
> are skipped automatically on Ubuntu.

---

### Step 15 — Wait for Installation

**Base WoW:** About 30 minutes

**NPCBots (pre-built):** About 10-15 minutes

**NPCBots or Playerbots (compile):** 2-4 hours.
Keep your PC awake and plugged in!

Watch progress in the terminal. When you see:

```
✅ Server is READY! ⚔️
```

You are ready for the next step!

---

## 👤 PART 4 — Create Your Account

### Step 16 — Open the GM Console

In the Ubuntu terminal, run:

```bash
docker attach $(docker ps --format '{{.Names}}' | grep worldserver | head -1)
```

You will see server output. That means it worked!

---

### Step 17 — Create Your Account

Type these commands — replace USERNAME and PASSWORD
with whatever you want:

```
account create USERNAME PASSWORD PASSWORD
account set gmlevel USERNAME 3 -1
```

**Example:**
```
account create dad mypassword mypassword
account set gmlevel dad 3 -1
```

---

### Step 18 — Exit the Console Safely

Press **Ctrl+P** then immediately **Ctrl+Q**

> ⚠️  Never press Ctrl+C — that stops the server!

---

## 🎮 PART 5 — Connect WoW to Your Server

This is the key difference from the Steam Deck guide.
Your WoW client is on Windows, your server is in WSL2.

### Step 19 — Find Your WSL2 IP Address

In the Ubuntu terminal run:

```bash
hostname -I | awk '{print $1}'
```

This will print an IP address like `172.24.144.1`.
**Write this down** — you need it in the next step.

> ⚠️  This IP address can change every time you restart
> WSL2. You will need to check it each session if it
> stops working. See the FAQ below for how to fix this.

---

### Step 20 — Update Your WoW Realmlist

On your **Windows PC** (not in Ubuntu), find your
WoW 3.3.5a client folder and open `realmlist.wtf`
in Notepad.

Change it to use your WSL2 IP address:

```
set realmlist 172.24.144.1
```

Replace `172.24.144.1` with the IP you got in Step 19.

Save and close the file.

---

### Step 21 — Launch WoW and Log In

Launch WoW from your Windows desktop as normal.

Log in with the username and password you created
in Step 17.

**You should be in Azeroth! ⚔️**

---

## 🖥️ PART 6 — Starting and Stopping Your Server

Unlike the Steam Deck, there is no Gaming Mode launcher
on Windows. Here is how to manage your server manually.

### Starting Your Server

Open the Ubuntu terminal and run:

```bash
# Base WoW
cd ~/wow-server && docker compose up -d

# NPCBots
cd ~/wow-server-npcbots && docker compose up -d

# Playerbots
cd ~/wow-server-playerbots && docker compose up -d
```

Wait for the server to be ready — watch with:

```bash
docker logs -f $(docker ps --format '{{.Names}}' | grep worldserver | head -1)
```

When you see `ready...` in the logs, launch WoW.
Press Ctrl+C to stop watching the logs — the server
keeps running.

---

### Stopping Your Server

Always stop the server properly before closing Ubuntu
or shutting down your PC:

```bash
# Base WoW
cd ~/wow-server && docker compose down

# NPCBots
cd ~/wow-server-npcbots && docker compose down

# Playerbots
cd ~/wow-server-playerbots && docker compose down
```

> ⚠️  Never just close the Ubuntu window while the
> server is running. Always run docker compose down first
> to save your character data properly.

---

### Check if Server is Running

```bash
docker ps
```

If you see containers listed — server is running.
If the table is empty — server is stopped.

---

## ❓ Frequently Asked Questions

---

**The WSL2 IP address keeps changing. How do I fix this?**

This is a known WSL2 limitation. Every time WSL2 restarts
it may get a new IP address. There are two solutions:

**Option A — Check the IP each session (easiest):**
Run `hostname -I | awk '{print $1}'` in Ubuntu before
playing and update your realmlist.wtf if it changed.

**Option B — Use a fixed IP (advanced):**
Add this to your Windows hosts file
(`C:\Windows\System32\drivers\etc\hosts`):
```
172.24.144.1    wowserver.local
```
Then set your realmlist to `set realmlist wowserver.local`.
You still need to update the IP in the hosts file when
it changes, but at least your realmlist never changes.

---

**Can I use 127.0.0.1 like on the Steam Deck?**

Unfortunately not. On WSL2, `127.0.0.1` refers to
Windows itself, not the Linux environment. You must
use the WSL2 IP address from `hostname -I`.

---

**Docker stops working after I restart my PC**

WSL2 suspends when you restart. Open Ubuntu and run:

```bash
sudo service docker start
```

If you completed Step 12, this should happen automatically
when you open Ubuntu.

---

**The server starts but WoW says "unable to connect"**

Check two things:

1. Is the server actually ready?
```bash
docker logs $(docker ps --format '{{.Names}}' | grep worldserver | head -1) | tail -20
```
Look for `ready...` near the bottom.

2. Is your realmlist pointing to the right IP?
```bash
hostname -I | awk '{print $1}'
```
Compare this to what is in your `realmlist.wtf`. They must match.

---

**Windows Firewall is blocking the connection**

If WoW cannot connect and you have confirmed the IP
and realmlist are correct, Windows Firewall may be
blocking the ports.

Open **Windows Defender Firewall** → **Allow an app
through firewall** → check if Ubuntu or WSL is listed
and allowed. If not, add it.

---

**Can I run this on Windows 10?**

Yes! You need Windows 10 version 2004 (build 19041)
or later. Press **Win+R**, type `winver` to check.
If you are on an older version, run Windows Update first.

---

**Does this work on a laptop?**

Yes! Any Windows laptop made in the last 8 years should
work fine for Base WoW and NPCBots. Playerbots compilation
takes longer on older hardware but will still work.
Keep your laptop plugged in during compilation.

---

**Can I have multiple server versions installed?**

Yes! Same as the Steam Deck — each installs to its
own folder and they never conflict. Just run one at a time.

```
~/wow-server           Base WoW
~/wow-server-npcbots   NPCBots
~/wow-server-playerbots Playerbots
```

---

## 🔧 Keeping Docker Running Between Sessions

Every time you open Ubuntu you may need to start Docker:

```bash
sudo service docker start
```

If you completed Step 12 this is automatic. If you ever
need to redo it:

```bash
echo 'sudo service docker start > /dev/null 2>&1' >> ~/.bashrc
```

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

**Start server:**
```bash
cd ~/wow-server && docker compose up -d
```

**Watch server start:**
```bash
docker logs -f $(docker ps --format '{{.Names}}' | grep worldserver | head -1)
```

**Stop server:**
```bash
cd ~/wow-server && docker compose down
```

**Check server status:**
```bash
docker ps
```

**Open GM console:**
```bash
docker attach $(docker ps --format '{{.Names}}' | grep worldserver | head -1)
```
Exit with Ctrl+P then Ctrl+Q

---

## What is Next?

- Need to create more accounts? See HOWTO-CREATE-ACCOUNTS.md
- Need to manage your server? See HOWTO-DESKTOP-CONTROLS-1.md
- Want to set up the AH Bot? See HOWTO-SETUP-AHBOT.md

---

## Video Guide

Full video walkthroughs at:
**youtube.com/@DadsMmoLab**

## GitHub

Everything is free at:
**github.com/DadsMmoLab/dads-mmo-lab**

---

*Part of the Dad's MMO Lab project — offline MMO servers,
free forever. No Steam Deck required.*

**youtube.com/@DadsMmoLab**
**github.com/DadsMmoLab/dads-mmo-lab**
**ko-fi.com/dadsmmolab**
