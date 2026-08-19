# MU Online — Dad's MMO Lab HOWTO

**Server:** OpenMU (Season 6 Episode 3)
**Installer version:** 2.0.0
**Platform:** Steam Deck (SteamOS) — Desktop Mode required for install

---

## What You're Getting

A fully offline **MU Online Season 6 Episode 3** server on your Steam Deck, powered by [OpenMU](https://github.com/MUnique/OpenMU) — a from-scratch, open-source MU server emulator. All maps, monsters, bosses, and a web admin panel.

The installer sets up the server, a one-tap Gaming Mode launcher, and — crucially — the network plumbing that makes the Windows client talk to the local server through Proton (see "How the connection works" below; it's the part everyone else gets stuck on).

---

## Before You Start

The **server** is automatic. You supply the **client** (Webzen game assets — not ours to distribute). From the OpenMU community (GitHub releases + the OpenMU Discord `#downloads`):

| File | What it is |
|------|-----------|
| MU **Season 6 Episode 3** client | The base game files |
| OpenMU patched **`main.exe`** | GameGuard removed; works with OpenMU |
| Patched **`Data/Local/Eng/ItemTooltip_eng.bmd`** | Fixes a tooltip data crash |
| OpenMU **ClientLauncher** (optional) | github.com/MUnique/OpenMU/releases |

Assemble them into one folder, e.g. `~/Games/MU Client 1.04d - Season 6E3/`, dropping the patched `main.exe` and `ItemTooltip_eng.bmd` over the base files.

**Disk:** ~3 GB client + a few GB Docker. **Internet:** required for install **and** to play (the client must be on a network — see below).

---

## Running the Installer

```bash
chmod +x install-muonline.sh
./install-muonline.sh
```

It will: install Docker, start the OpenMU server, pin everything to your Deck's LAN IP, install the launcher, and print the client steps. You'll be asked for your `sudo` password (for Docker + the network setup).

---

## How the Connection Works (read this once)

This is the part that makes MU different from the other Dad's MMO Lab installers:

- **Proton can't reach `127.0.0.1`.** The Steam Deck's Proton sandbox has an *isolated loopback* — `127.x` inside the sandbox is not your Deck's localhost. But it **can** reach your Deck's **LAN IP** (e.g. `192.168.0.21`).
- So the server is told to advertise the **LAN IP**, and the client's hardcoded connect hostname (`connect.muonline.webzen.com` — which the patched `main.exe` always uses) is redirected in `/etc/hosts` to that **LAN IP**.
- The launcher **auto-re-pins** both every time it runs, so a changed IP (new router / DHCP) fixes itself. It uses a tiny root-owned helper (`/etc/dml-mu-hosts.sh`) + a one-line passwordless-sudo rule the installer sets up.

You don't have to manage any of this — just launch via the server shortcut. It's only explained here so the behavior isn't mysterious.

---

## Client Setup (Steam)

1. **Add `main.exe` to Steam** as a Non-Steam game. Name it **`Mu Client 2`**.
2. **Properties → Compatibility** → force **GE-Proton** (install it via ProtonUp-Qt if needed — standard Proton won't do).
3. **Properties → General → Launch Options:**
   ```
   gamescope -w 1024 -h 768 -W 1280 -H 800 -f -- %command%
   ```
   This scales MU to fill the Deck screen **and** lets the launcher detect when you quit (so it can auto-stop the server). Don't add anything else — `connect /u… /p…` args are ignored by this client.

---

## Gaming Mode Launcher

The installer creates `~/muonline-launcher.sh`. Add it to Steam:

1. Add **`/usr/bin/konsole`** as a Non-Steam game → rename it **`MU Online Server`**.
2. **Launch Options:**
   ```
   --hold -e bash ~/muonline-launcher.sh
   ```
3. **Do NOT** enable Proton on this shortcut (it's a bash script).

**To play:** launch **`MU Online Server`** first → wait for **"✅ MU ONLINE IS READY!"** → press Steam button → launch **`Mu Client 2`**. When you close the client, the server shuts down automatically.

---

## Logging In

MU Online does **not** auto-register. Use the built-in demo accounts — **the password is the same as the username:**

| Username | Password | Notes |
|----------|----------|-------|
| `test0` | `test0` | normal account |
| `test1`…`test9` | same as username | more normal accounts |
| `testgm` | `testgm` | **GM / admin powers** |

Pick any server in the list (they're all your local server) → log in → create a character.

---

## Admin Panel

Open a browser to **`http://127.0.0.1`** (port 80). No login for local use. Manage accounts, servers, and configuration there.

---

## Troubleshooting

**"Wrong username or password"**
- The password **is** the username. Try `test0` / `test0`.

**Client connects to a server list but disconnects when you pick a server, OR shows a server you didn't set up ("Helheim")**
- You launched the wrong exe or the redirect isn't pinned. Make sure you're launching **`Mu Client 2`** (the patched client), and start the **`MU Online Server`** launcher first (it pins the redirect).

**MU stopped connecting after it worked before (new router / changed Wi-Fi)**
- Your Deck's LAN IP changed. Just relaunch **`MU Online Server`** — it auto-re-pins to the new IP.

**MU stopped connecting after a SteamOS system update**
- Updates can wipe `/etc`. Re-run `install-muonline.sh` to restore the redirect + helper. (Or run the one-line fix the launcher prints.)

**"No LAN IP found" / can't connect at all**
- The Deck must be on a network (Wi-Fi or dock ethernet). Proton can't reach a localhost-only server. Connect to a network and relaunch.

**Server won't start / "ready" never appears**
- Check `docker logs -f openmu-startup`. First run pulls the image and initializes the DB — give it a few minutes.

---

## Useful Commands

```bash
# Start / stop the server manually
cd ~/muonline-server && docker compose up -d
cd ~/muonline-server && docker compose down

# Live server log
docker logs -f openmu-startup

# What IP are the game servers advertising?
docker logs openmu-startup | grep "registered with endpoint"

# Re-pin the client redirect to the current LAN IP by hand
sudo /etc/dml-mu-hosts.sh "$(ip route get 1.1.1.1 | grep -oP 'src \K[0-9.]+')"
```

Full reference: `~/muonline-server/MY_SERVER.txt`

---

## Links

- OpenMU server: https://github.com/MUnique/OpenMU
- Dad's MMO Lab YouTube: https://youtube.com/@DadsMmoLab
- Dad's MMO Lab GitHub: https://github.com/DadsMmoLab/dads-mmo-lab
- Support the channel: https://ko-fi.com/dadsmmolab
