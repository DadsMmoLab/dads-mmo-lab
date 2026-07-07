# Dad's MMO Lab — Windows Installer: How-To Guide

**Installer:** Dad's MMO Lab Windows Substrate (Install-DML.ps1)
**Platform:** Windows 10 (version 2004 or newer) and Windows 11 (22H2 or newer), any modern desktop or laptop

---

## What This Installs

A clean Arch Linux + Docker environment running inside WSL2 on your Windows PC — the same environment the DML scripts run on for Steam Deck. Once it's set up, you can install any Dad's MMO Lab title with a single command or through the tray app.

This installer sets up:

- **WSL2** — Windows Subsystem for Linux (version 2), Microsoft's built-in Linux layer
- **Arch Linux** (registered as `dml-arch`) — isolated from any other Linux you might have installed
- **Docker Engine** — inside Arch Linux, rootful, same setup as the Deck
- **`dml` CLI** — a simple command for installing, checking, and managing DML titles
- **DML Launcher** — a Windows system tray app for starting, stopping, and installing titles without touching a terminal

**This installer does not install any game.** Games are layered on top afterward. This is by design — once the substrate is set up, you can install any Lab title without running this installer again.

---

## Requirements

| Requirement | Details |
|---|---|
| Windows version | **Windows 10, version 2004** (build 19041) or **Windows 11, version 22H2** (build 22621) or newer |
| Disk space | **30 GB free** on your C: drive |
| CPU virtualization | Must be enabled in BIOS/UEFI — most modern PCs have this on by default |
| Internet | Required throughout the install |
| Time | **~20 minutes** total (mostly automatic) |

> **How to check your Windows version:** Press **Windows key + R**, type `winver`, press Enter. You need build **19041** or higher on Windows 10, or **22621** or higher on Windows 11. If you're below that, run Windows Update first.

---

## Step 1 — Run the Installer

### Open PowerShell as Administrator

1. Press the **Windows key**, type `PowerShell`
2. Right-click **Windows PowerShell** → **Run as administrator**
3. Click **Yes** on the UAC prompt

### Paste and run this command

Replace the path with wherever you saved `Install-DML.ps1`:

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force
& "$env:USERPROFILE\Downloads\Install-DML.ps1"
```

> **If you saved it somewhere else** (like your Desktop), change `Downloads` to match. For example: `& "$env:USERPROFILE\Desktop\Install-DML.ps1"`

---

## What Happens During Install

### Phase 1: Preflight Checks (~2 min)

The installer runs these checks before touching anything:

| Check | What it's looking for |
|---|---|
| Windows version | Build 19041+ (Win 10 2004) or 22621+ (Win 11 22H2) |
| CPU virtualization | Intel VT-x or AMD-V enabled |
| Disk space | 30 GB free on C: |
| Internet | Can reach microsoft.com |

If a check fails, the installer stops and tells you exactly what to fix. Nothing gets installed until every check passes.

### The Reboot (first-time only)

The installer checks whether the Windows Hypervisor Platform is fully active — not just whether WSL is installed. This matters because WSL can be installed from the Microsoft Store without the underlying Virtual Machine Platform feature being enabled, which causes failures later.

If either the WSL feature or the Virtual Machine Platform feature isn't active, the installer enables them and requests one reboot. You'll see:

```
Alright, we need to reboot once -- totally normal.
I'll pick up right where we left off.
Restart now? (y/n)
```

Type **y** and press Enter. Your PC restarts, you log back in, and Phase 2 starts **automatically** — you don't need to re-run anything.

> **If WSL2 is already fully active** on your PC (the hypervisor is running), the installer detects this and skips the reboot entirely.

### Phase 2: Arch Linux + Docker Setup (~15–20 min, automatic)

After you log back in, a PowerShell window opens and runs Phase 2. You can watch the progress or walk away:

| Step | What's happening | Approx. time |
|---|---|---|
| WSL update | Updates WSL2 to the latest version | 1–2 min |
| `.wslconfig` | Sets RAM/CPU/swap limits for the Linux environment | < 1 min |
| Arch Linux install | Downloads the official Arch Linux image, imports it as `dml-arch` | 3–5 min |
| Keyring + updates | Initializes pacman's keyring, runs full system update | 2–3 min |
| User setup | Creates the `dml` Linux user with sudo access | < 1 min |
| Docker Engine | Installs Docker, enables it with systemd | 3–5 min |
| Docker test | Runs `hello-world` to confirm Docker works | < 1 min |

### Phase 3: dml CLI + DML Launcher (~2 min, automatic)

Immediately after Docker is verified, Phase 3 runs:

- Installs `base-devel`, `git`, `curl`, and `jq` inside the Arch environment
- Installs the `dml` command-line tool
- Moves any servers found at legacy locations into `/home/dml/games/` (older installs)
- Compiles and installs **DML Launcher** — a system tray app for managing your titles
- Creates a **Desktop shortcut** and adds the launcher to **Windows startup**
- Sets up the Windows firewall and port-proxy rules that make **LAN play** possible

When it's done you'll see:

```
============================================================
  Your DML environment is ready!
============================================================
```

Press **Enter** to close the window. The DML Launcher icon will appear in your system tray.

---

## Step 2 — Verify Your Environment

### Check the tray app

Look for the **DML Launcher** icon in your Windows system tray (bottom-right corner). Right-clicking it should show a menu. If you don't see it, launch it manually from your Desktop shortcut or `C:\DML\DML-Launcher.exe`.

### Check the CLI

Open a PowerShell or Windows Terminal window and run:

```powershell
wsl -d dml-arch
```

You're now inside your Arch Linux environment. Your prompt will look something like `[dml@...] $`.

Run the health check:

```bash
dml doctor
```

A healthy result looks like this:

```
[dml] Checking DML environment...
[ok]  systemd is running
[ok]  Docker Engine is running
[ok]  Disk space: 28 GB free on ext4
[ok]  Internet connection
[ok]  Environment healthy. Run 'dml run <url>' to install a title.
```

If you see `[WARN]` lines, see the Troubleshooting section below.

Type `exit` to leave the Linux environment and return to Windows.

---

## Step 3 — Install a Game Title

### Option A: Using DML Launcher (recommended)

1. Right-click the **DML Launcher** tray icon
2. Click **Install New Title...**
3. Click **Browse...** and select the title's `.sh` installer file from wherever you saved it on your PC
4. Click **Install** — a terminal window opens and runs the installer

> You can also paste a GitHub URL directly into the text box if you prefer to pull from the internet.

### Option B: Using the CLI

Open the `dml-arch` shell and use `dml run`:

```bash
wsl -d dml-arch
dml run https://github.com/DadsMmoLab/<title>
```

The `dml run` command clones the title's repo into `/home/dml/games/<title>/` and runs its `install.sh`. Follow the prompts from there — each title has its own setup steps.

> **Some titles have prerequisites.** For example, WoW Unbound requires WotLK to be installed first. Check the Dad's MMO Lab channel and the title's own HOWTO for the correct install order.

---

## Step 4 — Installing from a Downloaded Folder

Use this if you have a title's files extracted as a folder (from a ZIP) rather than a single `.sh` file.

**On Windows:** Extract the ZIP to somewhere easy to find — for example:

```
C:\Users\YourName\Downloads\wow-wotlk\
```

**Inside `dml-arch`:**

```bash
wsl -d dml-arch

# If you don't know your Windows username, list the Users folder:
ls /mnt/c/Users/

# Copy the folder into the Linux filesystem:
cp -r /mnt/c/Users/YourWindowsUsername/Downloads/wow-wotlk /home/dml/games/

# Run the installer:
cd /home/dml/games/wow-wotlk
bash install.sh
```

> **Why copy instead of running directly from `/mnt/c/`?** The `/mnt/c/` bridge runs over a slower protocol (9p). MMO server databases and Docker volumes must live on ext4 — that's `/home/dml/games/`. If you run `install.sh` from `/mnt/c/`, the script may work but any game data it creates there will be slow or broken.

---

## Managing Installed Titles

### Using DML Launcher (easiest)

Right-click the tray icon to see all installed titles. Each title shows its current status and its own submenu:

| Menu item | What it does |
|---|---|
| Title → Start | Starts the server (`docker compose up -d`) |
| Title → Stop | Stops the server (`docker compose down`) |
| Title → Attach to Console | Opens the live server console (exit safely with **Ctrl+P then Ctrl+Q** — Ctrl+C stops the server!) |
| Title → LAN Play | Lets other PCs on your home network join — see the title's own HOWTO for the full walkthrough |
| Install New Title... | Opens the install dialog — browse to a `.sh` file or paste a URL |
| Open DML Shell | Opens a terminal inside `dml-arch` |
| Run dml doctor | Runs a health check on your environment |
| Exit | Closes the tray app — **running servers shut down seconds later** (the launcher warns you first) |

> ⚠️ **Keep the DML Launcher running while you play.** Windows tears down the WSL2 environment — and every server inside it — shortly after the last connection to it closes. The launcher holds the environment open while a server is running, and blocks Windows from sleeping mid-session. Exiting the launcher with a server up means that server stops within seconds; nothing is damaged, but you'll have to start it again.

### Using the CLI

Run these from **inside the `dml-arch` environment** (after `wsl -d dml-arch`):

```bash
# List all installed titles and their status
dml list

# Check status of all titles
dml status

# Check status of a specific title
dml status <title>

# Start a title
dml start <title>

# Stop a title
dml stop <title>

# Check environment health
dml doctor

# Install a title from GitHub
dml run https://github.com/DadsMmoLab/<title>

# LAN play: point the realm at your PC's LAN address / back to this PC only / check
dml lan <title> on <lan-ip>
dml lan <title> off
dml lan <title> status

# Show every running container and which game ports it holds
dml scan

# Force-stop a stuck project by name (no directory needed)
dml kill <project-name>

# Clean up stuck containers, incomplete installs, and Docker leftovers
dml clean

# Check dml version
dml version
```

---

## Useful Windows Commands

Run these from **PowerShell or Windows Terminal**:

```powershell
# Open the DML environment
wsl -d dml-arch

# Shut down the DML environment (stops all running servers)
wsl --terminate dml-arch

# Full shutdown of all WSL distros
wsl --shutdown

# Check what WSL distros are installed
wsl -l -v

# Re-run the installer (safe -- picks up where it left off, always recompiles launcher)
Set-ExecutionPolicy Bypass -Scope Process -Force
& "$env:USERPROFILE\Downloads\Install-DML.ps1"
```

---

## Files and Paths

| Path | What it is |
|---|---|
| `C:\DML\DML-Launcher.exe` | The Windows tray app |
| `C:\DML\dml.ico` | Launcher icon (must stay alongside the exe) |
| `%USERPROFILE%\Desktop\DML Launcher.lnk` | Desktop shortcut |
| `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\DML Launcher.lnk` | Startup shortcut |
| `%LOCALAPPDATA%\DadsMMOLab\install.log` | Full install log (share this if something goes wrong) |
| `%LOCALAPPDATA%\DadsMMOLab\install-state.json` | Installer phase marker (used for reboot resume) |
| `%LOCALAPPDATA%\DadsMMOLab\wsl\` | The `dml-arch` Linux filesystem (VHD) |
| `%USERPROFILE%\.wslconfig` | WSL2 resource limits (RAM, CPU, swap) |
| `/home/dml/games/` | Inside WSL: where installed titles live |
| `/usr/local/bin/dml` | Inside WSL: the dml CLI |

> **To find `%LOCALAPPDATA%` in Explorer:** Press **Windows key + R**, type `%LOCALAPPDATA%\DadsMMOLab`, press Enter.

---

## Uninstalling DML

Run `Uninstall-DML.ps1` as Administrator to remove DML from a PC. The uninstaller removes:

- The DML Launcher process and exe (`C:\DML\`)
- The `dml-arch` WSL distro and all game data inside it
- Desktop and startup shortcuts
- The DML state directory (`%LOCALAPPDATA%\DadsMMOLab\`)
- The scheduled Phase 2 task
- The LAN play firewall and port proxy rules

```powershell
# Standard uninstall (keeps WSL itself and any other distros):
Set-ExecutionPolicy Bypass -Scope Process -Force
& "$env:USERPROFILE\Downloads\Uninstall-DML.ps1"

# Also remove WSL Windows features entirely (requires reboot):
& "$env:USERPROFILE\Downloads\Uninstall-DML.ps1" -RemoveWSL

# No prompts (for scripted resets):
& "$env:USERPROFILE\Downloads\Uninstall-DML.ps1" -Force
```

### Prompts during uninstall

The uninstaller will ask about two optional items:

- **.wslconfig** — only remove this if DML was your only WSL use. If you have other distros, keep it.
- **archlinux distro** — the installer uses the official `archlinux` WSL distro as a template when building `dml-arch`. If it finds `archlinux` still registered, it asks whether to remove it. Say **y** if DML was the reason it's there; say **n** if you use Arch Linux separately.

> **Warning:** Uninstalling removes all game servers and data inside `dml-arch`. Back up anything you want to keep before running the uninstaller.

---

## Re-running the Installer

The installer is **safe to re-run** at any time. Slow steps (keyring, Docker, dml CLI) are skipped if already complete. The **DML Launcher is always recompiled** on every run — this is intentional so that updates to the launcher are picked up automatically without any manual steps.

To force a completely fresh install, run the uninstaller first, then re-run the installer:

```powershell
# 1. Uninstall DML:
Set-ExecutionPolicy Bypass -Scope Process -Force
& "$env:USERPROFILE\Downloads\Uninstall-DML.ps1"

# 2. Re-run the installer:
& "$env:USERPROFILE\Downloads\Install-DML.ps1"
```

> **Warning:** The uninstaller permanently deletes everything inside `dml-arch`, including any game servers you installed. Back up anything you want to keep first.

---

## Troubleshooting

### "Running scripts is disabled on this system"

PowerShell's execution policy is blocking the script. Run this first in your Administrator PowerShell window, then re-run the installer:

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force
```

### Phase 2 didn't start automatically after the reboot

The scheduled task that kicks off Phase 2 runs at your next login. If the window never appeared, re-run the installer manually — it detects that WSL is already enabled and goes straight to Phase 2:

```powershell
# Administrator PowerShell:
Set-ExecutionPolicy Bypass -Scope Process -Force
& "$env:USERPROFILE\Downloads\Install-DML.ps1"
```

### "Windows 10 version 2004 ... or Windows 11 22H2 ... or later is required"

Your Windows version is too old. Run Windows Update until there are no more updates available, then try again.

### "CPU virtualization" warning during Phase 1

The installer couldn't positively confirm virtualization is enabled. It continues anyway — this check can be inconclusive on machines where Hyper-V is already running. If the install completes successfully, you can ignore this.

If WSL later fails to start with a virtualization error, you need to enable Intel VT-x or AMD-V (SVM) in your BIOS/UEFI:
1. Restart your PC and enter BIOS (usually **Del**, **F2**, **F10**, or **F12** during boot)
2. Look for **Virtualization Technology**, **Intel VT-x**, or **AMD SVM**
3. Enable it, save and exit

### Install fails with "HCS_E_SERVICE_NOT_AVAILABLE" or VM errors

This means the Windows Hypervisor Platform isn't loaded. This can happen on PCs where WSL was installed from the Microsoft Store but the Virtual Machine Platform Windows feature was never fully activated.

The installer detects this automatically and enables the feature, then prompts for a reboot. If you see this error in the install log, re-run the installer — it will catch the missing feature and fix it with a reboot.

If the installer itself fails before reaching that point, run these in an admin PowerShell, then reboot and re-run:

```powershell
Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -NoRestart
Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -NoRestart
Restart-Computer
```

### "Failed to download Arch Linux from the Microsoft Store"

This is an internet or Store connectivity issue. Check:

```powershell
# Test internet from PowerShell:
Test-NetConnection www.microsoft.com -Port 443
```

If that fails, check your network connection. If it succeeds but the Arch download still fails, Microsoft Store may be having a service issue — wait a few minutes and re-run the installer.

### "Arch Linux keyring / system update failed"

Most likely a temporary network hiccup during the pacman update. Re-run the installer — it skips all completed steps and retries only the failed one.

### "Docker Engine installation failed"

Check the install log for details:

```powershell
# Open the log in Notepad:
notepad "$env:LOCALAPPDATA\DadsMMOLab\install.log"
```

Common causes:
- **Network drop during pacman package download** — re-run the installer
- **systemd didn't initialize in time** — usually fixed by re-running; the installer waits 60 seconds for systemd on first boot

### DML Launcher tray icon not appearing

The launcher starts automatically at login via a startup shortcut. If it's not in the tray:

1. Check `C:\DML\DML-Launcher.exe` exists — if not, re-run the installer
2. Launch it manually: double-click the Desktop shortcut or run `C:\DML\DML-Launcher.exe`
3. Check the hidden tray icons (the `^` arrow in the taskbar corner)

### DML Launcher doesn't show an installed title

The launcher scans `/home/dml/games/` inside WSL for titles. Very early installers placed servers directly in `/home/dml/` instead — those won't appear.

**Fix:** stop the server if it's running, then re-run `Install-DML.ps1`. It finds servers at legacy locations and moves them into `games/` automatically — you'll see a `[migrate] Moved legacy server ...` line during the install. (Don't create symlinks by hand; they confuse the migration.)

### `dml doctor` shows Docker warnings after install

Docker may take a few seconds to finish starting. Wait 30 seconds and run `dml doctor` again. If it still fails:

```bash
# Inside dml-arch:
sudo systemctl status docker
sudo systemctl start docker
```

### `dml doctor` shows low disk space warning

The `dml-arch` VHD lives in `%LOCALAPPDATA%\DadsMMOLab\wsl\`. WSL2 VHDs can grow but don't automatically shrink. If your C: drive is getting full:

```powershell
# From Windows, check the VHD size:
(Get-Item "$env:LOCALAPPDATA\DadsMMOLab\wsl\ext4.vhdx").Length / 1GB
```

MMO servers with large databases need 20+ GB inside the Linux environment. Plan accordingly.

### "archlinux distro" still visible after uninstalling

The uninstaller prompts about the `archlinux` distro if it finds it registered. If you skipped that prompt (or used an older uninstaller), remove it manually:

```powershell
wsl --unregister archlinux
```

This is safe — the `archlinux` distro is only a template the installer uses to build `dml-arch`. Your game data lives in `dml-arch`, which the uninstaller already removed.

---

*Dad's MMO Lab — one-click offline MMO servers for Windows & Steam Deck.*
*youtube.com/@DadsMmoLab*
