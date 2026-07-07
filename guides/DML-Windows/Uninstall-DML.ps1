#Requires -RunAsAdministrator
param(
    [switch]$RemoveWSL,
    [switch]$Force
)

# Uninstall-DML.ps1 -- Dad's MMO Lab Uninstaller
#
# Standard uninstall (keeps WSL for other distros):
#   & "Uninstall-DML.ps1"
#
# Full wipe including WSL features (clean YouTube demo slate, requires reboot):
#   & "Uninstall-DML.ps1" -RemoveWSL
#
# Silent / no prompts:
#   & "Uninstall-DML.ps1" -RemoveWSL -Force

Set-StrictMode -Off
$ErrorActionPreference = 'Continue'

function Write-Header {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "  Dad's MMO Lab -- Uninstaller" -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host ""
}
function Write-Step ($msg) { Write-Host "  --> $msg" -ForegroundColor Cyan }
function Write-Ok   ($msg) { Write-Host "  [ok]   $msg" -ForegroundColor Green }
function Write-Warn ($msg) { Write-Host "  [warn] $msg" -ForegroundColor Yellow }
function Write-Info ($msg) { Write-Host "  [info] $msg" -ForegroundColor Gray }

$DistroName  = 'dml-arch'
$TaskName    = 'DadsMmoLab-Phase2'
$LauncherDir = 'C:\DML'
$StateDir    = "$env:LOCALAPPDATA\DadsMMOLab"
$WslConfig   = "$env:USERPROFILE\.wslconfig"
$DesktopLnk  = "$env:USERPROFILE\Desktop\DML Launcher.lnk"
$StartupLnk  = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\DML Launcher.lnk"

Write-Header

Write-Host "  This will permanently remove:" -ForegroundColor White
Write-Host "    - DML Launcher (C:\DML\)" -ForegroundColor White
Write-Host "    - dml-arch WSL distro + ALL game data inside it" -ForegroundColor White
Write-Host "    - DML state files and VHD ($StateDir)" -ForegroundColor White
Write-Host "    - Desktop and startup shortcuts" -ForegroundColor White
Write-Host "    - DadsMmoLab-Phase2 scheduled task" -ForegroundColor White
Write-Host "    - LAN play firewall and port proxy rules" -ForegroundColor White
if ($RemoveWSL) {
    Write-Host "    - WSL Windows features (requires reboot)" -ForegroundColor Yellow
}
Write-Host ""

if (-not $Force) {
    $confirm = Read-Host "  Type YES to continue"
    if ($confirm -ne 'YES') {
        Write-Host "  Cancelled." -ForegroundColor Yellow
        exit 0
    }
    Write-Host ""
}

# Step 1 -- Kill DML Launcher
Write-Step "Stopping DML Launcher..."
$proc = Get-Process -Name 'DML-Launcher' -ErrorAction SilentlyContinue
if ($proc) {
    $proc | Stop-Process -Force
    Start-Sleep -Milliseconds 800
    Write-Ok "DML Launcher stopped"
} else {
    Write-Info "DML Launcher was not running"
}

# Step 2 -- Remove scheduled task
Write-Step "Removing scheduled task '$TaskName'..."
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Write-Ok "Scheduled task removed (or was not present)"

# Step 3 -- Remove shortcuts
Write-Step "Removing shortcuts..."
foreach ($lnk in @($DesktopLnk, $StartupLnk)) {
    if (Test-Path $lnk) {
        Remove-Item $lnk -Force
        Write-Ok "Removed: $lnk"
    } else {
        Write-Info "Not found: $lnk"
    }
}

# Step 4 -- Remove C:\DML
Write-Step "Removing $LauncherDir..."
if (Test-Path $LauncherDir) {
    Remove-Item $LauncherDir -Recurse -Force
    Write-Ok "$LauncherDir removed"
} else {
    Write-Info "$LauncherDir not found"
}

# Step 5 -- Unregister WSL distros
Write-Step "Removing WSL distros..."
$rawList = wsl -l --quiet 2>$null
$distros  = @()
if ($rawList) {
    $distros = ($rawList -replace "`0","") -split "`n" | Where-Object { $_.Trim() -ne "" }
}

if ($distros -match $DistroName) {
    wsl --unregister $DistroName 2>$null
    Write-Ok "dml-arch distro removed"
} else {
    Write-Info "dml-arch not found -- already removed or never installed"
}

if ($distros -match 'archlinux') {
    $removeArch = $Force
    if (-not $Force) {
        Write-Host ""
        Write-Info "The 'archlinux' WSL distro is still registered."
        Write-Info "DML uses it as a template during install. If you did not have"
        Write-Info "Arch Linux installed before running DML, it is safe to remove."
        $ans = Read-Host "  Remove archlinux distro? (y/n)"
        $removeArch = ($ans -eq 'y')
    }
    if ($removeArch) {
        wsl --unregister archlinux 2>$null
        Write-Ok "archlinux distro removed"
    } else {
        Write-Info "archlinux kept"
    }
}

# Step 6 -- Remove state directory (contains VHD and logs)
Write-Step "Removing DML state directory ($StateDir)..."
if (Test-Path $StateDir) {
    Remove-Item $StateDir -Recurse -Force
    Write-Ok "State directory removed"
} else {
    Write-Info "State directory not found"
}

# Step 7 -- Remove .wslconfig (ask unless -Force)
if (Test-Path $WslConfig) {
    $removeConfig = $Force
    if (-not $Force) {
        Write-Host ""
        $ans = Read-Host "  Remove .wslconfig? Only say yes if DML was your only WSL use (y/n)"
        $removeConfig = ($ans -eq 'y')
    }
    if ($removeConfig) {
        Remove-Item $WslConfig -Force
        Write-Ok ".wslconfig removed"
    } else {
        Write-Info ".wslconfig kept"
    }
}

# Step 8 -- Remove LAN play network rules (firewall + port proxy)
# Same match logic as the installer's Step 12: only port proxy rules that
# point at 127.0.0.1 on DML's ports are touched (3306 covers rules from
# pre-1.3.0 WoW installers), so unrelated rules survive.
Write-Step "Removing LAN play network rules..."
$fwRules = Get-NetFirewallRule -DisplayName 'DML LAN Play*' -ErrorAction SilentlyContinue
if ($fwRules) {
    $fwRules | Remove-NetFirewallRule -ErrorAction SilentlyContinue
    Write-Ok "$(@($fwRules).Count) firewall rule(s) removed"
} else {
    Write-Info "No DML firewall rules found"
}

$dmlProxyPorts = @('3306', '3724', '8085')
$proxyRules = netsh interface portproxy show v4tov4 2>$null
$swept = 0
foreach ($line in @($proxyRules)) {
    if ("$line" -match '^\s*(\S+)\s+(\d+)\s+(\S+)\s+(\d+)\s*$') {
        $lAddr = $Matches[1]; $lPort = $Matches[2]
        $cAddr = $Matches[3]; $cPort = $Matches[4]
        if ($cAddr -eq '127.0.0.1' -and $dmlProxyPorts -contains $cPort) {
            netsh interface portproxy delete v4tov4 listenaddress=$lAddr listenport=$lPort 2>$null | Out-Null
            $swept++
        }
    }
}
if ($swept -gt 0) {
    Write-Ok "$swept port proxy rule(s) removed"
} else {
    Write-Info "No DML port proxy rules found"
}

# Step 9 -- Disable WSL Windows features (-RemoveWSL switch)
if ($RemoveWSL) {
    Write-Host ""
    Write-Step "Disabling WSL Windows features..."
    Write-Info "Note: this removes WSL for ALL distros, not just dml-arch."

    dism /online /disable-feature /featurename:Microsoft-Windows-Subsystem-Linux /norestart 2>$null
    Write-Ok "WSL feature disabled"

    dism /online /disable-feature /featurename:VirtualMachinePlatform /norestart 2>$null
    Write-Ok "Virtual Machine Platform disabled"
}

# Done
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  Dad's MMO Lab has been uninstalled." -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""

if ($RemoveWSL) {
    Write-Host "  WSL features disabled. A reboot is required to complete removal." -ForegroundColor Yellow
    Write-Host ""
    if (-not $Force) {
        $reboot = Read-Host "  Reboot now? (y/n)"
        if ($reboot -eq 'y') { Restart-Computer -Force }
    }
} else {
    Write-Host "  WSL itself was left installed (run with -RemoveWSL to also remove it)." -ForegroundColor Gray
    Write-Host "  No reboot needed." -ForegroundColor Gray
}

Write-Host ""
