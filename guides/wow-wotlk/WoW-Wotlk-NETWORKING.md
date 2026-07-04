# AzerothCore Network Setup Guide
### Connecting Others to Your Private WoW Server (Steam Deck, Linux & Windows WSL2)

## What This Guide Does

This guide helps you let other players connect to your AzerothCore WoW private server running in Docker on your Steam Deck, Linux PC, or Windows 10/11 machine with WSL2.
Please join the discord to discuss issues related to this or if you are having issues doing it. Do *NOT* submit a github issue.

**LAN only** — Same Wi-Fi as you: No router changes needed
**Internet play** — Anyone anywhere: Requires router access

> Not sure which? 
  Same home or same Wi-Fi → **LAN**.
  Friends at their own home → **Internet play**.

> 📦 **New to this? Setting up the server for the first time?**
> - **Steam Deck or Linux:** Start with the [WoW WotLK HOWTO guide](./WoW-WotLK-HOWTO.md), then come back here.
> - **Windows:** Start with the [DML Windows HOWTO](../DML-Windows/DML-Windows-HOWTO.md), then come back here.

---

## 🪟 Windows + WSL2 — Know Your Terminals

If you are on Windows, you will use two different windows throughout this guide. Read this first.

**WSL2 terminal (Arch Linux shell)**
Use for: `docker`, `sudo`, `cd`, `curl`
Open: Start → search **Windows Terminal** → type `wsl` → Enter
You should see a prompt like `user@archlinux ~ $`

**PowerShell or Command Prompt**
Use for: `ipconfig` only
Open: Start → search **PowerShell** or **cmd**

**PowerShell as Administrator**
Use for: `netsh` firewall commands only
Open: Right-click **Start** → **Windows PowerShell (Admin)** → click **Yes** on the popup

> 🪟 Think of WSL2 as a small Linux PC living **inside** your Windows PC. Its internal address (usually `172.x.x.x`) is invisible to your home network. For all setup steps, always use the **Windows IPv4 address** from `ipconfig` (usually `192.168.x.x`). Never share the `172.x.x.x` address with friends or use it in the server database.

> 🪟 **Not sure you have Arch Linux?** Open WSL2 and run: `cat /etc/os-release`
> If it says `Arch Linux` you're good. If it says Ubuntu or Debian, use the Ubuntu/Debian commands where shown instead.

---

## Before You Begin — Prerequisites

**Steam Deck / Linux:**
- AzerothCore Docker server installed and runs locally
- You know the folder name where it is installed
- You have set a sudo password (run `passwd` in a terminal if not)
- *(Internet play only)* You can log in to your router admin page
- You can edit `realmlist.wtf` on any WoW client that needs to connect

**Windows / WSL2:**
- Docker Desktop is installed and running — open Docker Desktop and wait until the bottom bar says **"Engine running"**
- WSL2 is installed and your Arch Linux distro opens successfully
- In your WSL2 terminal, `docker ps` runs without error
- You know the folder where the server is installed inside WSL2
- *(Internet play only)* You can log in to your router admin page
- You can edit `realmlist.wtf` on any WoW client that needs to connect

> 🪟 **sudo on Arch WSL2:** The `sudo` command may not be pre-configured in all Arch WSL2 setups. If `sudo` gives "command not found", you need to install it as root first:
> 1. Switch to root: `su -` (enter the root password when prompted)
> 2. Install sudo: `pacman -S sudo`
> 3. Configure your user: run `visudo` and add this line at the bottom:  
>    `yourusername ALL=(ALL) ALL`  
>    *(replace `yourusername` with your actual username — use arrow keys to navigate, type the line, then press **Ctrl+X → Y → Enter** to save)*
> 4. Type `exit` to leave root, then test: `sudo whoami` — it should print `root`
> 
> Alternatively, prefix admin commands with `su -c "command"` and enter the root password when prompted.

## Part 1 — Open a Terminal

**Steam Deck:**
1. Connect to power
2. Hold power button → **Switch to Desktop**
3. Open app launcher → click **Konsole**

**Fedora / Debian / Ubuntu Linux:**
Press `Ctrl + Alt + T` or search **Terminal** in your app menu

**Windows 10 / 11 (WSL2):**
1. Start → search **Windows Terminal** → open it
2. Type `wsl` and press Enter
3. You should see `user@archlinux ~ $` — you are now in Linux
4. If `wsl` says "not found", WSL2 is not installed — stop and [set it up first](https://learn.microsoft.com/en-us/windows/wsl/install)

> 🪟 All `docker` and `sudo` commands must be run in the **WSL2 terminal** — not in regular PowerShell — unless the step specifically says otherwise.

---

## Part 2 — Start Your Server

**Step 1.** Navigate to your server folder. Common folder names are `wow-server`, `wow-server-npcbots`, or `wow-server-playerbots`:
```
cd ~/wow-server-playerbots
```
> Not sure of your folder name? Run `ls ~` to see what's in your home folder.

> 🪟 **Windows:** If your server is on the C: drive, the WSL2 path is `/mnt/c/Users/YourName/your-server-folder`. Storing the server inside the Linux filesystem (`~/`) gives better Docker performance.

**Step 2.** Start the server:
```
docker compose up -d
```
> `-d` means Docker runs in the background and doesn't take over your terminal.

**Step 3.** Verify all containers started (wait 30–60 seconds first):
```
docker compose ps
```
Look for all three containers to show **Up** or **healthy**:
```
NAME              STATUS
ac-authserver     Up
ac-worldserver    Up
ac-database       Up
```
> **Tip:** First-time database setup can take **10–15 minutes**. Re-run `docker compose ps` until all show `Up`.

> 🪟 **Windows:** Check that the STATUS column shows ports like `0.0.0.0:3724->3724/tcp`. If it shows `127.0.0.1:3724->3724/tcp` instead, LAN access may not work — check your Docker Compose port bindings.

## Part 3 — Find Your IP Addresses

You need two numbers. **Write them both down** — you will use them shortly.

**Your Local (LAN) IP Address**

**Steam Deck / Linux** — run in your terminal:
```
ip -4 route get 1.1.1.1 | awk '{for (i=1;i<=NF;i++) if ($i=="src") {print $(i+1); exit}}'
```
Prints something like `192.168.1.25` — this is your **local IP**.

**Windows** — run in PowerShell or Command Prompt (not WSL2):
```
ipconfig
```
Find the **IPv4 Address** under your active **Wi-Fi** or **Ethernet** adapter. That is your **local IP**.

> 🪟 The WSL2 terminal also has an IP (usually `172.x.x.x`) — that is NOT your Windows LAN address. Always use the `192.168.x.x` from `ipconfig` for all setup and to share with friends on LAN.

**Your External (Public) IP Address** *(internet play only — skip if LAN only)*

**Steam Deck / Linux / WSL2 terminal:**
```
curl -4 https://icanhazip.com
```

**Windows PowerShell:**
```
curl.exe -4 https://icanhazip.com
```
> Note the `.exe` — regular `curl` in PowerShell is a different command that won't work here.

Prints something like `98.24.105.7` — this is your **public IP**.

> ⚠️ **CGNAT Warning:** Some providers (especially mobile/LTE home internet) use CGNAT, which blocks port forwarding entirely. If everything is set up correctly but internet friends still can't connect, ask your ISP whether your plan supports hosting/port forwarding.

## Part 4 — Open Firewall Ports

Your machine's firewall blocks incoming connections by default. Open ports **3724** and **8085**.

> ⚠️ **Required for BOTH LAN and internet play. Follow only the block for your OS — skip the others.**

**Steam Deck (SteamOS only)** — paste this entire block:
```bash
sudo steamos-readonly disable
sudo pacman -Sy ufw
sudo ufw allow 3724/tcp
sudo ufw allow 8085/tcp
sudo ufw enable
sudo systemctl enable ufw
sudo steamos-readonly enable
```
> SteamOS locks system files for stability. This temporarily unlocks it to install UFW (the firewall tool), then locks it again. It is safe and only modifies the firewall — nothing else. After running, verify with:
> ```bash
> sudo ufw status
> ```
> You should see `3724` and `8085` listed as `ALLOW`.

---

**Fedora only:**
```bash
sudo systemctl enable --now firewalld
sudo firewall-cmd --permanent --add-port=3724/tcp
sudo firewall-cmd --permanent --add-port=8085/tcp
sudo firewall-cmd --reload
```

---

**Debian / Ubuntu / Linux Mint only:**
```bash
sudo apt update
sudo apt install ufw
sudo ufw allow 3724/tcp
sudo ufw allow 8085/tcp
sudo ufw enable
```

---

**Windows 10 / 11 with WSL2 only:**

Open **PowerShell as Administrator**: right-click **Start** → **Windows PowerShell (Admin)** → click **Yes** on the popup. The window title bar should say **Administrator**.

Run these two commands:
```
netsh advfirewall firewall add rule name="AzerothCore Auth" protocol=TCP dir=in localport=3724 action=allow
netsh advfirewall firewall add rule name="AzerothCore World" protocol=TCP dir=in localport=8085 action=allow
```
> 🪟 Run `netsh` in the Windows Admin PowerShell — not inside your WSL2 terminal. Linux firewall tools inside WSL2 do not control the Windows host. Docker Desktop forwards container ports to your Windows host automatically.

> 🪟 Also check your network is set to **Private**: Settings → Network & Internet → click your connection → set to **Private**. Public networks block many incoming connections.

**WSL2 port proxy (Windows only):**

Because WSL2 runs in a virtual machine, if your server ports are bound to `127.0.0.1` instead of `0.0.0.0`, Windows needs to forward traffic from your real network card into the WSL2 environment. Run `docker compose ps` — if ports show `127.0.0.1:3724->3724/tcp`, you need portproxy. If they show `0.0.0.0:3724->3724/tcp`, skip this section.

Replace `YOUR_LOCAL_IP` with your Windows IPv4 from `ipconfig` (e.g. `192.168.1.25`). Run in **PowerShell as Administrator**:

**Required — game ports (3724 and 8085):**
```
netsh interface portproxy add v4tov4 listenaddress=YOUR_LOCAL_IP listenport=3724 connectaddress=127.0.0.1 connectport=3724
netsh interface portproxy add v4tov4 listenaddress=YOUR_LOCAL_IP listenport=8085 connectaddress=127.0.0.1 connectport=8085
```

**Optional — database port (3306, only needed if you use HeidiSQL or another database tool):**
```
netsh advfirewall firewall add rule name="AzerothCore DB" protocol=TCP dir=in localport=3306 action=allow
netsh interface portproxy add v4tov4 listenaddress=YOUR_LOCAL_IP listenport=3306 connectaddress=127.0.0.1 connectport=3306
```

> ⚠️ If your Windows IP changes, re-run these commands with the new IP. To verify rules are active: `netsh interface portproxy show all`. To remove old rules before re-adding: `netsh interface portproxy delete v4tov4 listenaddress=OLD_IP listenport=3724`

---

## Part 5 — Choose Your Path

👉 **LAN only (same Wi-Fi)?** Continue to **Section 6A** below.
👉 **Internet play (friends at other homes)?** Skip to **Section 6B** below.

---

## 6A — LAN-Only Setup

> Only for people on the **same Wi-Fi**. No router changes needed.

**Step 1 — Update the Server Database**

Replace `YOUR_LOCAL_IP` with the local IP from Part 3:
```
docker compose exec ac-database mysql -uroot -ppassword -e "UPDATE acore_auth.realmlist SET address='YOUR_LOCAL_IP', localAddress='YOUR_LOCAL_IP' WHERE id=1;"
```
> This tells the server what address players should connect to.
> Default database password is `password`. If you changed it, replace `password` with yours.
> 🪟 **Windows:** Use Windows IPv4 from `ipconfig` — not the WSL2 `172.x.x.x` address.
> If the command returns to a blank prompt with no error, it worked.

**Step 2 — Restart the Server**
```
docker compose down
docker compose up -d
```

**Step 3 — Edit realmlist.wtf on Each Game Client**

`realmlist.wtf` is a file inside every player's WoW folder that tells the game which server to connect to.

Where to find it:
- **Windows client:** WoW folder → `Data` → locale folder (e.g. `enUS`) → `realmlist.wtf`
- **Steam Deck (Proton):** Usually at `~/.steam/steam/steamapps/compatdata/[AppID]/pfx/drive_c/Program Files/World of Warcraft/Data/enUS/realmlist.wtf` — or wherever you placed your WoW folder
  > 💡 **Not sure what `[AppID]` is?** Open **Dolphin** file manager, press **F5** to show hidden files, then use the search bar and search for `realmlist.wtf` — it'll find the right file wherever it lives.
- **Linux client (Wine/Proton):** Inside the Wine prefix for your WoW install. A Wine prefix is a folder that acts like a Windows C: drive. It typically lives at `~/.wine/drive_c/...` or wherever your WoW folder is stored.

Open it in a text editor and change the `set realmlist` line to:
```
set realmlist YOUR_LOCAL_IP
```
Example: `set realmlist 192.168.1.25`

> 🪟 On Windows: right-click the file → **Open with** → **Notepad**. Save after editing.
> Each player must edit their own `realmlist.wtf` on their own computer.

✅ **LAN Setup Complete!** Players on the same Wi-Fi can now log in.

---

## 6B — Internet Play Setup

> For players **outside your home network**. Requires router access.

**Step 1 — Reserve a Static Local IP for Your Machine**

Your router may give your machine a new local IP after a reconnect, which would break port forwarding. A DHCP reservation tells your router to always give your PC the same home-network address.

1. Log in to your router admin page (see Step 2 for how)
2. Find **DHCP Reservations**, **Static DHCP**, or **Address Reservation** (search your router model + "DHCP reservation" if unsure)
3. Find your Steam Deck or Windows PC in the connected devices list
4. Assign it the same local IP from Part 3
5. Save and apply

**Step 2 — Set Up Port Forwarding on Your Router**

Log in to your router's admin page in a browser:
- **Netgear:** `https://www.routerlogin.net`
- **Most routers:** `http://192.168.1.1` or `http://192.168.0.1`
- Check the label on your router if neither works
- Some ISPs use a phone app instead

Find the **Port Forwarding**, **Virtual Server**, or **NAT** section. Create two rules:

__Rule 1 — Auth Server__
Name: AzerothCore Auth | Protocol: TCP
External Port: 3724 | Internal Port: 3724
Internal IP: your local IP

__Rule 2 — World Server__
Name: AzerothCore World | Protocol: TCP
External Port: 8085 | Internal Port: 8085
Internal IP: your local IP

> ⚠️ **TCP only.** Do not choose UDP or Both.

---

**Step 3 — Update the Server Database**

Replace `YOUR_PUBLIC_IP` with your public IP and `YOUR_LOCAL_IP` with your local IP from Part 3:
```
docker compose exec ac-database mysql -uroot -ppassword -e "UPDATE acore_auth.realmlist SET address='YOUR_PUBLIC_IP', localAddress='YOUR_LOCAL_IP' WHERE id=1;"
```
> 🪟 **Windows:** Use Windows IPv4 from `ipconfig` as `YOUR_LOCAL_IP` — not the WSL2 `172.x.x.x`.
> If the command returns to a blank prompt with no error, it worked.

**Step 4 — Restart the Server**
```
docker compose down
docker compose up -d
```

**Step 5 — Edit realmlist.wtf on Each Game Client**

For players **connecting over the internet:**
```
set realmlist YOUR_PUBLIC_IP
```

For players **on the same local Wi-Fi** as the server:
```
set realmlist YOUR_LOCAL_IP
```
> LAN players should use the local IP — most home routers don't support connecting to your own public IP from inside your own network (called hairpin NAT).

✅ **Internet Play Setup Complete!** Remote friends can now connect using your public IP.

## ⚠️ Dynamic IP Warning

Your public IP can change — especially with Comcast/Xfinity, Cox, and others — after a modem restart or over time.

If internet play suddenly stops working:
1. Check your current IP: `curl -4 https://icanhazip.com` (Linux/WSL2) or `curl.exe -4 https://icanhazip.com` (PowerShell)
2. If it changed, re-run the database command from 6B Step 3 with the new IP and restart the server

**Free fix:** [DuckDNS](https://www.duckdns.org/) gives you a permanent address like `mygameserver.duckdns.org` that follows your changing IP automatically. Worth setting up if you host regularly.

---

## Troubleshooting

**"It worked yesterday but not today"**
Your local IP probably changed. Check it:
- Linux/WSL2 terminal: `ip -4 route get 1.1.1.1 | awk '{for (i=1;i<=NF;i++) if ($i=="src") {print $(i+1); exit}}'`
- Windows PowerShell: `ipconfig`

Re-run the database command with the new IP and restart. Set a DHCP reservation (6B Step 1) to prevent this.

**"Server not showing up / containers stopped"**
```
docker compose ps
```
All three (auth, world, database) should say `Up`. If not:
```
docker compose up -d
```

**"Friends on LAN can't connect — Windows/WSL2 server"**
1. Use **Windows IPv4** from `ipconfig` — not the WSL2 `172.x.x.x`
2. Confirm Windows Firewall rules were added (Part 4 Windows section)
3. Set Windows network to **Private** (Settings → Network & Internet)
4. Make sure Docker Desktop is running
5. Run `docker compose ps` and confirm ports show `0.0.0.0:3724` not `127.0.0.1:3724`

**"LAN works but internet players can't connect"**
1. Check port forwarding — local IP must match your current machine IP
2. Both ports forwarded as **TCP** only
3. Confirm firewall allows ports (Part 4)
4. Check public IP hasn't changed
5. Ask your ISP if CGNAT is blocking port forwarding

**"Command not found" errors**
- `docker` → Docker Desktop or Docker Engine not installed
- `curl` not found:
  - SteamOS or WSL2 Arch: `sudo pacman -S curl`
  - Debian/Ubuntu: `sudo apt install curl`
  - Fedora: `sudo dnf install curl`
  - Windows PowerShell: use `curl.exe` (already built in)

**"Access denied" on database command**
Default password is `password`. If you changed it, check the `.env` file in your server folder — open `~/wow-server-playerbots/.env` (or `~/wow-server/.env` etc.) in Kate or any text editor and look for the `DOCKER_DB_ROOT_PASSWORD` line.

**"steamdeck is not a valid host"**
Hostname resolution is unreliable on most home networks. Use the **local IP address directly**.

---

## AzerothCore Port Reference

- **3724 TCP** — Authentication server (login)
- **8085 TCP** — World server (gameplay)
- **3306 TCP** — Database (optional — only needed for database tools or backup)

---

## Useful Links

- [AzerothCore Docker Install Guide](https://www.azerothcore.org/wiki/install-with-docker)
- [AzerothCore Networking Docs](https://www.azerothcore.org/wiki/networking)
- [AzerothCore Client Setup](https://www.azerothcore.org/wiki/client-setup)
- [Port Forwarding Help — portforward.com](https://portforward.com/)
- [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/)
- [WSL2 Install Guide (Microsoft)](https://learn.microsoft.com/en-us/windows/wsl/install)
- [DuckDNS — free dynamic DNS](https://www.duckdns.org/)

---

*Part of the Dad's MMO Lab project — free forever.*

**youtube.com/@DadsMmoLab**
**github.com/DadsMmoLab/dads-mmo-lab**
