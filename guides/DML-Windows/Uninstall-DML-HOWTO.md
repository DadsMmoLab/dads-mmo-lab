# Dad's MMO Lab — Uninstaller: How-To Guide

**Uninstaller:** Dad's MMO Lab Windows Uninstaller (Uninstall-DML.ps1)
**Platform:** Windows 10 and Windows 11

---

## What the Uninstaller Removes

| What | Details |
|---|---|
| DML Launcher | Stops the process and removes `C:\DML\` |
| `dml-arch` WSL distro | Unregisters the distro and deletes its VHD — **all game data inside is gone** |
| Desktop shortcut | `%USERPROFILE%\Desktop\DML Launcher.lnk` |
| Startup shortcut | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\DML Launcher.lnk` |
| DML state directory | `%LOCALAPPDATA%\DadsMMOLab\` — install log, state file, and the VHD folder |
| Phase 2 scheduled task | The `DadsMmoLab-Phase2` task registered during install |
| LAN play network rules | The `DML LAN Play` firewall rules and the port proxy entries for the game ports (only rules pointing at `127.0.0.1` on DML's ports — anything else you've configured is left alone) |

The following are **not removed automatically** — the uninstaller prompts you about each:

| What | Why it prompts |
|---|---|
| `archlinux` WSL distro | DML uses it as a template, but you may have had it installed before DML |
| `.wslconfig` | Controls RAM/CPU limits for all WSL distros, not just DML's |
| WSL Windows features | Removing WSL affects every distro on the PC, not just DML |

> **This is permanent.** Everything inside `dml-arch` — game servers, databases, configuration — is deleted. Back up anything you want to keep before running the uninstaller.

---

## Before You Start

### Back up game data (if needed)

Game data lives inside the `dml-arch` WSL distro at `/home/dml/games/`. To copy something out before uninstalling:

```powershell
# Open DML shell
wsl -d dml-arch

# Copy a title's folder to your Windows Downloads:
cp -r /home/dml/games/<title> /mnt/c/Users/$USER/Downloads/
```

---

## Step 1 — Run the Uninstaller

### Open PowerShell as Administrator

1. Press the **Windows key**, type `PowerShell`
2. Right-click **Windows PowerShell** → **Run as administrator**
3. Click **Yes** on the UAC prompt

### Paste and run this command

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force
& "$env:USERPROFILE\Downloads\Uninstall-DML.ps1"
```

The uninstaller opens, lists what it's about to remove, and asks you to confirm:

```
  This will permanently remove:
    - DML Launcher (C:\DML\)
    - dml-arch WSL distro + ALL game data inside it
    - DML state files and VHD (...)
    - Desktop and startup shortcuts
    - DadsMmoLab-Phase2 scheduled task
    - LAN play firewall and port proxy rules

  Type YES to continue:
```

Type **YES** (all caps) and press Enter to proceed.

---

## Step 2 — Answer the Prompts

### archlinux distro

If the `archlinux` WSL distro is still registered on your PC, the uninstaller asks:

```
  [info] The 'archlinux' WSL distro is still registered.
  [info] DML uses it as a template during install. If you did not have
  [info] Arch Linux installed before running DML, it is safe to remove.
  Remove archlinux distro? (y/n):
```

- Type **y** if DML was the reason `archlinux` is installed — it's safe to remove
- Type **n** if you use Arch Linux yourself for other things and want to keep it

### .wslconfig

If a `.wslconfig` file exists in your user profile, the uninstaller asks:

```
  Remove .wslconfig? Only say yes if DML was your only WSL use (y/n):
```

- Type **y** if DML was your only WSL use — this removes the RAM/CPU limits file
- Type **n** if you use other WSL distros and want to keep the settings

---

## What You'll See When It's Done

```
============================================================
  Dad's MMO Lab has been uninstalled.
============================================================

  WSL itself was left installed (run with -RemoveWSL to also remove it).
  No reboot needed.
```

No reboot is needed for a standard uninstall. The DML Launcher icon disappears from the tray immediately.

---

## Optional: Full WSL Removal (-RemoveWSL)

Use this if you want to remove WSL from the PC entirely — for example, to reset a demo machine to a completely clean state before filming.

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force
& "$env:USERPROFILE\Downloads\Uninstall-DML.ps1" -RemoveWSL
```

This adds one more step after the standard uninstall:

- Disables the **Windows Subsystem for Linux** Windows feature
- Disables the **Virtual Machine Platform** Windows feature

> **This affects every WSL distro on the PC**, not just DML's. Any other Linux environments you had running in WSL will also be gone.

After this step the uninstaller asks:

```
  WSL features disabled. A reboot is required to complete removal.
  Reboot now? (y/n):
```

Type **y** to reboot immediately, or **n** to reboot later. The features aren't fully removed until after the reboot.

---

## Silent Mode (-Force)

Skips all prompts and proceeds automatically. Useful for scripted resets.

```powershell
# Silent standard uninstall:
& "$env:USERPROFILE\Downloads\Uninstall-DML.ps1" -Force

# Silent full wipe including WSL features (auto-reboots):
& "$env:USERPROFILE\Downloads\Uninstall-DML.ps1" -RemoveWSL -Force
```

In `-Force` mode:
- The `YES` confirmation is skipped
- `archlinux` is removed without prompting
- `.wslconfig` is removed without prompting
- With `-RemoveWSL`, the PC reboots automatically without prompting

---

## After Uninstalling

To verify everything is gone:

```powershell
# Confirm dml-arch is no longer registered:
wsl -l -v

# Confirm C:\DML is gone:
Test-Path "C:\DML"    # should return False

# Confirm state directory is gone:
Test-Path "$env:LOCALAPPDATA\DadsMMOLab"    # should return False

# Confirm the LAN play port proxy rules are gone (list should be empty
# unless you created rules of your own for other software):
netsh interface portproxy show v4tov4
```

If you removed WSL features and rebooted, `wsl -l -v` will report that WSL has no installed distributions (or that WSL is not installed at all).

---

## Re-installing After Uninstalling

Just re-run the installer — it starts fresh:

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force
& "$env:USERPROFILE\Downloads\Install-DML.ps1"
```

See `DML-Windows-HOWTO.md` for the full install guide.

---

*Dad's MMO Lab — one-click offline MMO servers for Windows & Steam Deck.*
*youtube.com/@DadsMmoLab*
