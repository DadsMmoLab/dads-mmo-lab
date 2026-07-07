#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Dad's MMO Lab -- Windows Substrate Installer
.DESCRIPTION
    Bootstraps a clean Arch Linux (dml-arch) + Docker Engine environment in
    WSL2 -- the same substrate the DML scripts already run on for Steam Deck.
    No game logic lives here. Games are layered on top with 'dml run'.

    Phase 1 (PowerShell, elevated): preflights, enables WSL2 features, reboots once.
    Phase 2 (PowerShell, elevated): installs Arch Linux as 'dml-arch', Docker Engine.
    Phase 3 (bash, inside dml-arch): core deps + the 'dml' CLI.

    Supports Windows 10 (2004 / build 19041+) and Windows 11 (22H2+).
    Install location is chosen at first run; defaults to C:\DML.

    Run as Administrator. One click, one reboot, then run any DML script.
#>
[CmdletBinding()]
param(
    [switch]$ResumePhase2
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# =============================================================================
# Paths
# =============================================================================
$StateDir   = "$env:LOCALAPPDATA\DadsMMOLab"
$StateFile  = "$StateDir\install-state.json"
$LogFile    = "$StateDir\install.log"
$WslDir     = "$StateDir\wsl"
$ScriptPath = $MyInvocation.MyCommand.Path
if (-not $ScriptPath) { $ScriptPath = $PSCommandPath }

# =============================================================================
# Constants
# =============================================================================
$DiskNeededBytes = 30GB
$DmlDistroName   = 'dml-arch'
$DmlLinuxUser    = 'dml'
$DmlCliVersion   = '2.1.0'   # bundled dml CLI + launcher tooltip (keep in sync)
$TaskName        = 'DadsMmoLab-Phase2'

$Script:FailReported = $false

# =============================================================================
# Logging
# =============================================================================
function Write-Step([string]$msg) {
    $line = "[step] $msg"
    Write-Host $line -ForegroundColor Cyan
    Add-Content -Path $LogFile -Value "$(Get-Date -Format 'HH:mm:ss') $line"
}

function Write-Diag([string]$msg) {
    $line = "[diag] $msg"
    Write-Host $line -ForegroundColor DarkGray
    Add-Content -Path $LogFile -Value "$(Get-Date -Format 'HH:mm:ss') $line"
}

function Write-Warn([string]$msg) {
    $line = "[WARN] $msg"
    Write-Host $line -ForegroundColor Yellow
    Add-Content -Path $LogFile -Value "$(Get-Date -Format 'HH:mm:ss') $line"
}

function Write-Ok([string]$msg) {
    $line = "[ok]   $msg"
    Write-Host $line -ForegroundColor Green
    Add-Content -Path $LogFile -Value "$(Get-Date -Format 'HH:mm:ss') $line"
}

function Write-Fail([string]$msg) {
    $line = "[FAIL] $msg"
    Write-Host $line -ForegroundColor Red
    Add-Content -Path $LogFile -Value "$(Get-Date -Format 'HH:mm:ss') $line"
    $Script:FailReported = $true
    throw $msg
}

# =============================================================================
# State
# =============================================================================
function Save-State([string]$InstallRoot) {
    try {
        @{
            Phase1Complete = $true
            InstallRoot    = $InstallRoot
            Timestamp      = (Get-Date -Format 'o')
        } | ConvertTo-Json | Set-Content -Path $StateFile -Encoding UTF8
    } catch {
        Write-Fail "Could not save installer state to $StateFile -- check disk space and permissions.`n($($_.Exception.Message))"
    }
    Write-Diag "State saved: $StateFile"
}

# =============================================================================
# Step-completion markers (re-run safety for slow Phase 2/3 steps)
# =============================================================================
function Test-StepDone([string]$step) {
    Test-Path "$StateDir\done-$step"
}
function Mark-StepDone([string]$step) {
    New-Item -ItemType File -Force -Path "$StateDir\done-$step" | Out-Null
    Write-Diag "Step '$step' marked complete"
}
function Clear-DistroStepMarkers {
    # Called when we do a fresh distro import so stale inside-distro markers
    # from a previous install don't cause those steps to be skipped.
    foreach ($step in @('dml-user', 'wsl-conf', 'arch-keyring', 'docker-install', 'phase3-bootstrap')) {
        Remove-Item "$StateDir\done-$step" -Force -ErrorAction SilentlyContinue
    }
    Write-Diag "Cleared inside-distro step markers for fresh import"
}

# =============================================================================
# Preflight checks
# =============================================================================
function Assert-WindowsBuild {
    Write-Step "Checking Windows version..."
    $build      = [System.Environment]::OSVersion.Version.Build
    $Win11Build = 22621  # Win11 22H2
    $Win10Build = 19041  # Win10 2004
    Write-Diag "Build: $build  (Win10 min: $Win10Build  Win11 min: $Win11Build)"
    if ($build -ge $Win11Build) {
        Write-Ok "Windows 11 (build $build) -- OK"
    } elseif ($build -ge $Win10Build) {
        Write-Ok "Windows 10 (build $build) -- OK"
    } else {
        Write-Fail "Windows 10 version 2004 (build 19041) or Windows 11 22H2 (build 22621) or later is required.`nPlease run Windows Update, then try again."
    }
}

function Assert-VirtualizationFirmware {
    Write-Step "Checking CPU virtualization (required for WSL2)..."

    # Tri-state: $true = confirmed on, $null = inconclusive.
    # Never hard-fail on inconclusive -- the real test is whether WSL2 starts.
    $virtState = $null

    # Check 1 (authoritative): is a hypervisor already running?
    # Covers Hyper-V, Core Isolation / Memory Integrity, and any prior WSL2 run.
    # When true, virtualization is working -- do not parse systeminfo.
    try {
        $hv = (Get-CimInstance Win32_ComputerSystem -ErrorAction Stop).HypervisorPresent
        Write-Diag "Win32_ComputerSystem.HypervisorPresent: $hv"
        if ($hv -eq $true) { $virtState = $true }
    } catch {
        Write-Diag "HypervisorPresent query failed: $($_.Exception.Message)"
    }

    # Check 2: firmware flag -- positive only.
    # A $false here is ignored; it's false-negative-prone when Hyper-V is active.
    if ($virtState -ne $true) {
        try {
            $vals = @(Get-CimInstance Win32_Processor -ErrorAction Stop |
                      Select-Object -ExpandProperty VirtualizationFirmwareEnabled)
            Write-Diag "Win32_Processor.VirtualizationFirmwareEnabled: $($vals -join ', ')"
            if ($vals -contains $true) { $virtState = $true }
        } catch {
            Write-Diag "Win32_Processor query failed: $($_.Exception.Message)"
        }
    }

    if ($virtState -eq $true) {
        Write-Ok "CPU virtualization enabled"
        return
    }

    Write-Warn "Couldn't positively confirm CPU virtualization, but found no blocker -- continuing."
    Write-Warn "If WSL2 fails to start later, enable Intel VT-x or AMD-V (SVM) in your BIOS/UEFI."
}

function Assert-DiskSpace([string]$InstallRoot) {
    $driveLetter = $InstallRoot.Substring(0, 1).ToUpper()
    Write-Step "Checking disk space on ${driveLetter}:..."
    try {
        $drive = Get-PSDrive -Name $driveLetter -ErrorAction Stop
        if ($null -ne $drive.Free) {
            $freeGB = [math]::Round($drive.Free / 1GB, 1)
            Write-Diag "${driveLetter}: free: $freeGB GB  (need: 30 GB)"
            if ($drive.Free -lt $DiskNeededBytes) {
                Write-Fail "Not enough disk space on ${driveLetter}:. Need 30 GB free, found $freeGB GB.`nFree up space or choose a different drive."
            }
            Write-Ok "${driveLetter}: $freeGB GB free -- OK"
        } else {
            Write-Warn "Could not read free space on ${driveLetter}: -- continuing"
        }
    } catch {
        Write-Warn "Could not check disk space on ${driveLetter}: ($($_.Exception.Message)) -- continuing"
    }
}

function Assert-Internet {
    Write-Step "Checking internet connection..."
    try {
        $null = Invoke-WebRequest -Uri 'https://www.microsoft.com' -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
        Write-Ok "Internet connection OK"
    } catch {
        Write-Fail "No internet connection detected.`nThe installer needs to download WSL2 components and the Arch Linux image.`nConnect to the internet and try again."
    }
}

# =============================================================================
# WSL enable
# =============================================================================
function Enable-Wsl2Features {
    Write-Step "Enabling WSL2 features..."

    # Three cases to handle:
    #   (A) Fully working: wsl --version exit 0 AND hypervisor loaded  -> skip, no reboot
    #   (B) Features enabled but never rebooted: HypervisorPresent=False, features Enabled -> reboot
    #   (C) Features missing: enable them (wsl --install or Enable-WindowsOptionalFeature) -> reboot
    #
    # Cannot rely on wsl --version alone: Store-installed WSL makes exit 0 even when
    # VirtualMachinePlatform is inactive. Cannot rely on Get-WindowsOptionalFeature
    # alone: Store-installed WSL reports features as 'Disabled' even when fully working.
    # Both signals together cover all known machine states.

    $hvPresent = $false
    try { $hvPresent = (Get-CimInstance Win32_ComputerSystem -ErrorAction Stop).HypervisorPresent } catch {}
    Write-Diag "HypervisorPresent: $hvPresent"

    Write-Diag "Running: wsl --version (idempotency check)"
    wsl --version | Out-Null
    $wslVersionExit = $LASTEXITCODE
    Write-Diag "wsl --version exit code: $wslVersionExit"

    # Case A: everything is working
    if ($wslVersionExit -eq 0 -and $hvPresent) {
        Write-Ok "WSL2 already installed and working -- no reboot needed"
        return $false
    }

    if ($wslVersionExit -eq 0 -and -not $hvPresent) {
        Write-Diag "WSL executable present but hypervisor not loaded -- checking Windows feature state"
    }

    $wslFeat = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -ErrorAction SilentlyContinue
    $vmFeat  = Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform            -ErrorAction SilentlyContinue
    Write-Diag "WSL feature state:         $($wslFeat.State)"
    Write-Diag "VM Platform feature state: $($vmFeat.State)"

    # Case B: features already on, just needs a reboot to load the hypervisor
    if ($wslFeat.State -eq 'Enabled' -and $vmFeat.State -eq 'Enabled') {
        if (-not $hvPresent) {
            Write-Ok "WSL2 features are enabled -- a reboot is needed to activate the hypervisor"
            return $true
        }
        Write-Ok "WSL2 features already enabled"
        return $false
    }

    # Case C: one or both features are missing -- enable them
    Write-Diag "Running: wsl --install --no-distribution"
    wsl --install --no-distribution
    $wslExit = $LASTEXITCODE
    Write-Diag "wsl --install exit code: $wslExit"

    if ($wslExit -ne 0) {
        Write-Warn "wsl --install returned $wslExit -- falling back to Enable-WindowsOptionalFeature"

        try {
            Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -NoRestart -All | Out-Null
        } catch {
            Write-Fail "Failed to enable the Windows Subsystem for Linux feature.`nTry running Windows Update, restarting, and running this installer again.`n($($_.Exception.Message))"
        }

        try {
            Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -NoRestart -All | Out-Null
        } catch {
            Write-Fail "Failed to enable the Virtual Machine Platform feature.`nTry running Windows Update, restarting, and running this installer again.`n($($_.Exception.Message))"
        }
    }

    Write-Ok "WSL2 features enabled -- reboot required"
    return $true
}

# =============================================================================
# Scheduled task registration
# =============================================================================
function Register-Phase2Task {
    Write-Step "Registering Phase 2 to auto-run after you log back in..."

    if (-not $ScriptPath) {
        Write-Fail "Cannot determine installer script path -- save the script to a fixed location and run it from there."
    }

    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

    $action    = New-ScheduledTaskAction -Execute 'powershell.exe' `
                     -Argument "-ExecutionPolicy Bypass -WindowStyle Normal -File `"$ScriptPath`" -ResumePhase2"
    $trigger   = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings  = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 6) `
                     -RunOnlyIfNetworkAvailable:$false
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest

    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal -Force | Out-Null

    Write-Diag "Scheduled task '$TaskName' registered for user '$($env:USERNAME)'"
    Write-Ok "Phase 2 will start automatically after you log in"
}

# =============================================================================
# Phase 1
# =============================================================================
function Invoke-Phase1 {
    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null

    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "  Dad's MMO Lab -- Environment Setup  [Phase 1 of 2]" -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Setting up a clean Arch Linux + Docker environment in WSL2." -ForegroundColor White
    Write-Host "  It runs the same scripts as the Steam Deck." -ForegroundColor White
    Write-Host "  Games are installed separately with 'dml run' once this is done." -ForegroundColor White
    Write-Host "  Phase 1 runs some checks and may need one reboot." -ForegroundColor White
    Write-Host ""

    # Choose install location
    Write-Host "  Where do you want to install DML?" -ForegroundColor White
    Write-Host "  This is where the Linux environment (WSL VHD) and launcher will be stored." -ForegroundColor White
    Write-Host "  The folder will be created if it doesn't exist." -ForegroundColor White
    Write-Host "  Examples: D:\DML   E:\Games\DML" -ForegroundColor DarkGray
    Write-Host ""
    $defaultRoot = 'C:\DML'
    $rawInput    = Read-Host "  Install location (press Enter for $defaultRoot)"
    $InstallRoot = if ([string]::IsNullOrWhiteSpace($rawInput)) { $defaultRoot } else { $rawInput.Trim().TrimEnd('\') }
    if ($InstallRoot -notmatch '^[A-Za-z]:') {
        Write-Fail "Install path must start with a drive letter (e.g., D:\DML)`nNetwork paths and relative paths are not supported by WSL."
    }
    Write-Diag "Install root: $InstallRoot"
    Write-Ok "Installing to: $InstallRoot"
    Write-Host ""

    Assert-WindowsBuild
    Assert-VirtualizationFirmware
    Assert-DiskSpace -InstallRoot $InstallRoot
    Assert-Internet

    Write-Step "Saving installer state..."
    Save-State -InstallRoot $InstallRoot

    $rebootNeeded = Enable-Wsl2Features

    if ($rebootNeeded) {
        Register-Phase2Task

        Write-Host ""
        Write-Host "============================================================" -ForegroundColor Green
        Write-Host "  Phase 1 complete -- one reboot needed" -ForegroundColor Green
        Write-Host "============================================================" -ForegroundColor Green
        Write-Host ""
        Write-Host "  Alright, we need to reboot once -- totally normal." -ForegroundColor White
        Write-Host "  I'll pick up right where we left off." -ForegroundColor White
        Write-Host "  Phase 2 installs Arch Linux and Docker (10-20 min, runs automatically)." -ForegroundColor White
        Write-Host ""

        $go = Read-Host "  Restart now? (y/n)"
        if ($go -match '^[Yy]') {
            Write-Diag "Initiating restart..."
            Restart-Computer -Force
        } else {
            Write-Host ""
            Write-Host "  No problem -- restart whenever you're ready." -ForegroundColor Yellow
            Write-Host "  Phase 2 kicks off automatically when you log back in." -ForegroundColor Yellow
        }
    } else {
        Write-Host ""
        Write-Host "  WSL2 was already enabled -- no reboot needed." -ForegroundColor Green
        Write-Host "  Moving straight to Phase 2..." -ForegroundColor White
        Write-Host ""
        Invoke-Phase2
    }
}

# =============================================================================
# Phase 2 helper
# =============================================================================
function Invoke-WslBash {
    param(
        [string]$Distro,
        [string]$User,
        [string]$Script,
        [string]$Label
    )
    Write-Diag "[$Label] running in $Distro as $User"
    # PowerShell @'...'@ heredocs use CRLF; bash rejects lines ending with \r.
    # bash -s reads stdin; piping to bare "bash" can hang waiting for a TTY.
    $Script.Replace("`r`n", "`n") | wsl -d $Distro -u $User -- bash -s | Out-Host
    $exit = $LASTEXITCODE
    Write-Diag "[$Label] exit code: $exit"
    return $exit
}

function Wait-DockerSocket {
    param(
        [string]$Distro,
        [int]$TimeoutSec = 30
    )
    Write-Diag "[$Distro] Waiting for Docker socket (max ${TimeoutSec}s)..."
    wsl -d $Distro -u root -- systemctl start docker 2>$null | Out-Null
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        wsl -d $Distro -u root -- test -S /var/run/docker.sock 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Diag "[$Distro] Docker socket ready"
            return 0
        }
        Start-Sleep -Seconds 1
    }
    Write-Diag "[$Distro] Docker socket not ready within ${TimeoutSec}s"
    return 1
}

# =============================================================================
# Phase 2
# =============================================================================
function Invoke-Phase2 {
    param([switch]$AfterReboot)
    New-Item -ItemType Directory -Force -Path $StateDir -ErrorAction SilentlyContinue | Out-Null

    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "  Dad's MMO Lab -- Environment Setup  [Phase 2 of 2]" -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host ""
    if ($AfterReboot) {
        Write-Step "Resuming after reboot..."
    } else {
        Write-Step "Starting Phase 2 (WSL2 already enabled)..."
    }

    # Remove the scheduled task immediately -- must not fire again on next login
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Diag "Scheduled task removed"

    if (-not (Test-Path $StateFile)) {
        Write-Fail "Install state not found at $StateFile.`nPlease re-run Phase 1 (run Install-DML.ps1 without -ResumePhase2)."
    }

    # Load install root saved by Phase 1; fall back to C:\DML for legacy state files
    $stateJson   = $null
    $InstallRoot = 'C:\DML'
    try {
        $stateJson = (Get-Content $StateFile -Raw | ConvertFrom-Json)
        if ($stateJson -and ($stateJson.PSObject.Properties.Name -contains 'InstallRoot')) {
            $InstallRoot = [string]$stateJson.InstallRoot
        }
    } catch {}
    $WslDir      = "$InstallRoot\wsl"
    Write-Diag "Install root: $InstallRoot"
    Write-Diag "WSL dir:      $WslDir"

    # -------------------------------------------------------------------------
    # Step 5: Update WSL + set default version to 2
    # -------------------------------------------------------------------------
    Write-Step "Updating WSL to latest version..."
    wsl --update
    $wslUpdateExit = $LASTEXITCODE
    Write-Diag "wsl --update exit code: $wslUpdateExit"
    if ($wslUpdateExit -ne 0) {
        Write-Warn "wsl --update returned $wslUpdateExit -- continuing with current version"
    } else {
        Write-Ok "WSL updated"
    }

    Write-Step "Setting WSL2 as default distro version..."
    wsl --set-default-version 2
    $wslDefaultExit = $LASTEXITCODE
    Write-Diag "wsl --set-default-version 2 exit code: $wslDefaultExit"
    if ($wslDefaultExit -ne 0) {
        Write-Fail "Failed to set WSL default version to 2 (exit $wslDefaultExit)."
    }
    Write-Ok "WSL2 set as default version"

    # Guard against null: Select-String returns $null when wsl --version output
    # encoding doesn't match (e.g. Store WSL on Windows 11 outputs UTF-16).
    $wslVerMatch = wsl --version | Select-String 'WSL version' | Select-Object -First 1
    $wslVerStr   = if ($wslVerMatch) { $wslVerMatch.Line -replace '.*WSL version:\s*', '' } else { '' }
    Write-Diag "WSL version: '$wslVerStr'"
    if ($wslVerStr) {
        try {
            $wslVer = [version]($wslVerStr.Trim().Split('.')[0..2] -join '.')
            if ($wslVer -lt [version]'2.4.4') {
                Write-Warn "WSL $wslVerStr is below the recommended minimum 2.4.4."
                Write-Warn "Run 'wsl --update' from an admin terminal, then re-run this installer."
            } else {
                Write-Ok "WSL version $wslVerStr -- OK"
            }
        } catch {
            Write-Diag "Could not parse WSL version string -- skipping version floor check"
        }
    }

    # -------------------------------------------------------------------------
    # Step 6: Write .wslconfig (RAM + CPU + swap caps, localhost forwarding)
    # -------------------------------------------------------------------------
    Write-Step "Configuring WSL2 resource limits (.wslconfig)..."
    $cs          = Get-CimInstance Win32_ComputerSystem
    $totalRamGB  = [math]::Round($cs.TotalPhysicalMemory / 1GB)
    $hostCores   = $cs.NumberOfLogicalProcessors
    $wslRamGB    = [math]::Max(2, [math]::Round($totalRamGB * 0.60))
    $wslSwapGB   = [math]::Max(2, [math]::Round($wslRamGB / 2))
    $wslCores    = [math]::Min(4, $hostCores)
    Write-Diag "Host: ${totalRamGB} GB RAM, $hostCores logical cores"
    Write-Diag "WSL2 cap: ${wslRamGB} GB RAM, $wslCores cores, ${wslSwapGB} GB swap"

    $wslConfigPath = "$env:USERPROFILE\.wslconfig"
    @"
[general]
instanceIdleTimeout=-1

[wsl2]
memory=${wslRamGB}GB
processors=$wslCores
swap=${wslSwapGB}GB
localhostForwarding=true
vmIdleTimeout=-1

[experimental]
autoMemoryReclaim=gradual
"@ | Set-Content -Path $wslConfigPath -Encoding UTF8
    Write-Ok ".wslconfig written: instanceIdleTimeout=-1, vmIdleTimeout=-1, autoMemoryReclaim=gradual"
    Write-Diag "Applying .wslconfig (requires brief WSL shutdown)..."
    wsl --shutdown 2>$null | Out-Null
    Start-Sleep -Seconds 3
    Write-Ok "WSL restarted — vmIdleTimeout=-1 is now active"

    # -------------------------------------------------------------------------
    # Step 7: Install Arch Linux as isolated 'dml-arch' distro
    #
    # wsl --install has no --name flag, so we:
    #   1. Install as 'archlinux' (official Store image)
    #   2. Export to a temp tar
    #   3. Import as 'dml-arch' into $WslDir (isolated VHD, no collision)
    #   4. Unregister 'archlinux' only if we were the ones who installed it
    # -------------------------------------------------------------------------
    Write-Step "Installing Arch Linux as '$DmlDistroName' (isolated distro)..."

    # wsl -l --quiet outputs UTF-16 LE on Windows 11; strip null bytes before matching
    $distroRaw      = (wsl -l --quiet) -replace "`0", ""
    Write-Diag "Registered distros: $($distroRaw -join ' | ')"
    $dmlArchPresent = [bool]($distroRaw | Where-Object { $_ -match '^dml-arch$' })

    if ($dmlArchPresent) {
        Write-Ok "'$DmlDistroName' already registered -- skipping install"
    } else {
        $archlinuxPreExisted = [bool]($distroRaw | Where-Object { $_ -match '^archlinux$' })
        Write-Diag "archlinux pre-existed: $archlinuxPreExisted"

        if (-not $archlinuxPreExisted) {
            Write-Step "Downloading official Arch Linux WSL image (source for import)..."
            wsl --install -d archlinux --no-launch
            $archInstallExit = $LASTEXITCODE
            Write-Diag "wsl --install archlinux exit code: $archInstallExit"
            if ($archInstallExit -ne 0) {
                Write-Fail "Failed to download Arch Linux from the Microsoft Store (exit $archInstallExit)`nCheck your internet connection and try again."
            }
        } else {
            Write-Diag "Using pre-existing 'archlinux' as import source"
        }

        # Brief init run to ensure the filesystem is fully unpacked before export
        Write-Diag "Initializing Arch Linux filesystem..."
        wsl -d archlinux -u root -- true
        $initExit = $LASTEXITCODE
        Write-Diag "Init run exit code: $initExit"
        if ($initExit -ne 0) {
            Write-Warn "Arch Linux init run returned $initExit -- proceeding with export (export will fail if the image is corrupt)."
        }

        # bsdtar (used by wsl --export) cannot archive Unix socket files and hard-fails.
        # Socket files are created by gpg-agent and other daemons during init and persist in
        # the VHD after forced termination. Remove them while the distro is still live.
        Write-Diag "Removing socket files before export (bsdtar cannot archive sockets)..."
        wsl -d archlinux -u root -- find / -xdev -type s -delete
        Write-Diag "Socket cleanup exit code: $LASTEXITCODE"

        wsl --terminate archlinux
        Write-Diag "wsl --terminate archlinux exit code: $LASTEXITCODE"

        # Export archlinux → import as dml-arch
        New-Item -ItemType Directory -Force -Path $WslDir | Out-Null
        $TmpTar = "$env:TEMP\dml-arch-rootfs.tar"

        # Remove any leftover tar from a prior interrupted run -- wsl --export will not overwrite.
        Remove-Item $TmpTar -Force -ErrorAction SilentlyContinue

        Write-Step "Exporting Arch Linux filesystem to temp tar..."
        wsl --export archlinux $TmpTar
        $exportExit = $LASTEXITCODE
        Write-Diag "wsl --export exit code: $exportExit"
        if ($exportExit -ne 0) {
            Remove-Item $TmpTar -Force -ErrorAction SilentlyContinue
            Write-Fail "Failed to export Arch Linux filesystem (exit $exportExit)."
        }

        Write-Step "Importing as '$DmlDistroName' to $WslDir..."
        wsl --import $DmlDistroName $WslDir $TmpTar
        $importExit = $LASTEXITCODE
        Remove-Item $TmpTar -Force -ErrorAction SilentlyContinue
        Write-Diag "wsl --import exit code: $importExit"
        if ($importExit -ne 0) {
            Write-Fail "Failed to import Arch Linux as '$DmlDistroName' (exit $importExit)."
        }

        if (-not $archlinuxPreExisted) {
            Write-Diag "Removing temporary 'archlinux' registration..."
            wsl --unregister archlinux
            $unregExit = $LASTEXITCODE
            Write-Diag "wsl --unregister exit code: $unregExit"
            if ($unregExit -ne 0) {
                Write-Warn "Could not remove temporary 'archlinux' distro (exit $unregExit)`nRemove it manually when convenient: wsl --unregister archlinux"
            }
        }

        # Clear any stale inside-distro step markers from a prior install attempt
        Clear-DistroStepMarkers
        Write-Ok "Arch Linux installed as '$DmlDistroName'"
    }

    wsl --set-default $DmlDistroName
    $setDefaultExit = $LASTEXITCODE
    Write-Diag "wsl --set-default exit code: $setDefaultExit"
    if ($setDefaultExit -ne 0) {
        Write-Warn "Could not set '$DmlDistroName' as default WSL distro (exit $setDefaultExit)`nFix manually: wsl --set-default dml-arch"
    }

    # -------------------------------------------------------------------------
    # Step 8a: Initialize pacman keyring + full system update
    # Fresh Arch-WSL ships with an empty keyring -- pacman fails until this runs.
    # -------------------------------------------------------------------------
    if (Test-StepDone 'arch-keyring') {
        Write-Ok "Arch Linux keyring + system update already done -- skipping"
    } else {
        Write-Step "Initializing Arch Linux (keyring + system update -- a few minutes)..."

        $exit8a = Invoke-WslBash -Distro $DmlDistroName -User root -Label 'keyring' -Script @'
set -euo pipefail
echo "[arch] Initializing pacman keyring..."
pacman-key --init
pacman-key --populate archlinux
echo "[arch] Keyring ready"
echo "[arch] Running full system update..."
pacman -Syu --noconfirm
echo "[arch] System up to date"
'@
        if ($exit8a -ne 0) {
            Write-Fail "Arch Linux keyring / system update failed (exit $exit8a)`nCheck your internet connection and the log: $LogFile"
        }
        Write-Ok "Arch Linux packages up to date"
        Mark-StepDone 'arch-keyring'
    }

    # -------------------------------------------------------------------------
    # Step 8b: Create the dml Linux user with sudo access
    # Docker group does not exist yet -- added in Step 9 after docker install.
    # -------------------------------------------------------------------------
    if (Test-StepDone 'dml-user') {
        Write-Ok "Linux user '$DmlLinuxUser' already configured -- skipping"
    } else {
        Write-Step "Creating Linux user '$DmlLinuxUser'..."

        $exit8b = Invoke-WslBash -Distro $DmlDistroName -User root -Label 'useradd' -Script @'
set -euo pipefail
pacman -S --noconfirm --needed sudo
if id dml &>/dev/null; then
    echo "[arch] User 'dml' already exists"
else
    useradd -m -G wheel dml
    echo "[arch] User 'dml' created"
fi
mkdir -p /etc/sudoers.d
echo '%wheel ALL=(ALL:ALL) NOPASSWD: ALL' > /etc/sudoers.d/wheel
chmod 0440 /etc/sudoers.d/wheel
echo "[arch] sudo configured"
'@
        if ($exit8b -ne 0) {
            Write-Fail "Failed to create Linux user '$DmlLinuxUser' (exit $exit8b)."
        }
        Write-Ok "Linux user '$DmlLinuxUser' ready with sudo access"
        Mark-StepDone 'dml-user'
    }

    # -------------------------------------------------------------------------
    # Step 8c: Write /etc/wsl.conf and restart distro to activate systemd
    # -------------------------------------------------------------------------
    if (Test-StepDone 'wsl-conf') {
        Write-Ok "systemd already configured in wsl.conf -- skipping"
    } else {
        Write-Step "Enabling systemd in Arch Linux..."

        $exit8c = Invoke-WslBash -Distro $DmlDistroName -User root -Label 'wsl.conf' -Script @'
set -euo pipefail
printf '[boot]\nsystemd=true\n\n[user]\ndefault=dml\n' > /etc/wsl.conf
echo "[arch] /etc/wsl.conf written"
'@
        if ($exit8c -ne 0) {
            Write-Fail "Failed to write /etc/wsl.conf (exit $exit8c)."
        }

        Write-Step "Restarting distro to activate systemd (wsl --terminate)..."
        wsl --terminate $DmlDistroName
        Write-Diag "wsl --terminate exit code: $LASTEXITCODE"
        Start-Sleep -Seconds 3
        Write-Ok "Systemd active on next distro start"
        Mark-StepDone 'wsl-conf'
    }

    # -------------------------------------------------------------------------
    # Step 9: Install Docker Engine inside Arch (now with systemd running)
    # usermod -aG docker dml runs after the docker package creates the docker group.
    # -------------------------------------------------------------------------
    if (Test-StepDone 'docker-install') {
        Write-Ok "Docker Engine already installed -- skipping"
    } else {
        Write-Step "Installing Docker Engine (downloading packages -- several minutes)..."

        $exit9 = Invoke-WslBash -Distro $DmlDistroName -User root -Label 'docker-install' -Script @'
set -euo pipefail
echo "[docker] Waiting for systemd to initialize..."
timeout 60 bash -c 'until systemctl is-system-running 2>/dev/null | grep -qE "running|degraded"; do sleep 2; done'
echo "[docker] systemd ready"
echo "[docker] Installing packages..."
pacman -S --noconfirm --needed docker docker-compose docker-buildx
echo "[docker] Enabling docker service..."
systemctl enable --now docker
echo "[docker] Adding dml to docker group..."
usermod -aG docker dml
echo "[docker] Waiting for Docker socket..."
timeout 30 bash -c 'until [ -S /var/run/docker.sock ]; do sleep 1; done'
echo "[docker] Done"
'@
        if ($exit9 -ne 0) {
            Write-Fail "Docker Engine installation failed (exit $exit9)`nCheck the log: $LogFile"
        }
        Write-Ok "Docker Engine installed and enabled"
        Mark-StepDone 'docker-install'
    }

    Write-Step "Verifying Docker..."
    # Wait for the Docker socket before running hello-world. Uses direct wsl test
    # calls instead of a piped bash script (which can hang after wsl --shutdown).
    $socketWait = Wait-DockerSocket -Distro $DmlDistroName -TimeoutSec 30
    if ($socketWait -ne 0) {
        Write-Warn "Docker socket not available within 30s (exit $socketWait) -- hello-world test may fail."
    }
    wsl -d $DmlDistroName -u root -- docker run --rm hello-world
    $dockerTestExit = $LASTEXITCODE
    Write-Diag "Docker hello-world exit code: $dockerTestExit"
    if ($dockerTestExit -ne 0) {
        Write-Warn "Docker hello-world test failed (exit $dockerTestExit). Check the log: $LogFile"
    } else {
        Write-Ok "Docker is working"
    }

    # -------------------------------------------------------------------------
    # CLI version check (always runs -- fast if already current)
    # If the installed version doesn't match the bundled version, clear the
    # phase3-bootstrap marker so Step 10 re-installs the CLI automatically.
    # -------------------------------------------------------------------------
    $ExpectedCliVersion = "dml v$DmlCliVersion"
    $installedCliRaw = ''
    try {
        $installedCliRaw = (wsl -d $DmlDistroName -- dml version 2>$null)
        if ($installedCliRaw) { $installedCliRaw = ($installedCliRaw -replace "`0","").Trim() }
    } catch { }
    Write-Diag "dml CLI: installed='$installedCliRaw'  expected='$ExpectedCliVersion'"
    if ($installedCliRaw -ne $ExpectedCliVersion) {
        Write-Diag "CLI version mismatch -- clearing phase3-bootstrap marker to force re-install"
        Remove-Item "$StateDir\done-phase3-bootstrap" -Force -ErrorAction SilentlyContinue
    }

    # -------------------------------------------------------------------------
    # Step 10: Phase 3 bootstrap -- core deps + dml CLI
    #
    # The dml CLI is base64-encoded here and decoded inside the distro.
    # This avoids nested-heredoc quoting issues and CRLF contamination.
    # -------------------------------------------------------------------------
    if (Test-StepDone 'phase3-bootstrap') {
        Write-Ok "Phase 3 bootstrap already done -- skipping"
    } else {
        Write-Step "Installing core dependencies and dml CLI (Phase 3)..."

        $DmlCli = @'
#!/usr/bin/env bash
set -euo pipefail

VERSION="__DML_CLI_VERSION__"
GAMES_DIR="$HOME"

_require_docker() {
    if ! docker info &>/dev/null; then
        echo "[dml] Docker is not running. Try: sudo systemctl start docker" >&2
        exit 1
    fi
}

_has_compose() {
    local dir="$1" name
    for name in docker-compose.yml docker-compose.yaml compose.yml compose.yaml; do
        [[ -f "$dir/$name" ]] && return 0
    done
    return 1
}

_compose_running() {
    local dir="$1" name compose_file=""
    for name in docker-compose.yml docker-compose.yaml compose.yml compose.yaml; do
        if [[ -f "$dir/$name" ]]; then compose_file="$dir/$name"; break; fi
    done
    [[ -z "$compose_file" ]] && echo 0 && return
    { docker compose -f "$compose_file" ps --status running -q 2>/dev/null || true; } | wc -l | tr -d '[:space:]'
}

# Windows-side helpers (paths written at install time)
DML_WIN_ROOT='__DML_INSTALL_ROOT__'

_win_path() { echo "${DML_WIN_ROOT//\\//}/$1"; }

_ensure_keepalive() {
    command -v powershell.exe &>/dev/null || return 0
    local ps1
    ps1=$(_win_path "DML-Ensure-Keepalive.ps1")
    powershell.exe -NoProfile -WindowStyle Hidden -File "$ps1" 2>/dev/null || true
}

_release_wsl() {
    # Must run detached on Windows AFTER this WSL session exits — cannot self-terminate.
    command -v powershell.exe &>/dev/null || return 0
    local ps1 win_ps1
    ps1=$(_win_path "DML-Release-WSL.ps1")
    win_ps1="${ps1//\//\\}"
    powershell.exe -NoProfile -WindowStyle Hidden -Command \
        "Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-WindowStyle','Hidden','-File','${win_ps1}') -WindowStyle Hidden" \
        2>/dev/null || true
}

_update_titles_cache() {
    # Keep C:\DML\dml-titles.cache fresh for the tray when WSL is off (no hardcoding).
    command -v powershell.exe &>/dev/null || return 0
    local cache titles=() dir title
    cache=$(_win_path "dml-titles.cache")
    [[ ! -d "$GAMES_DIR" ]] && return 0
    for dir in "$GAMES_DIR"/*/; do
        [[ -d "$dir" ]] || continue
        title=$(basename "$dir")
        if _has_compose "$dir"; then
            titles+=("$title")
        else
            for subdir in "$dir"*/; do
                [[ -d "$subdir" ]] && _has_compose "$subdir" && titles+=("$title") && break
            done
        fi
    done
    [[ ${#titles[@]} -eq 0 ]] && return 0
    printf '%s\n' "${titles[@]}" | powershell.exe -NoProfile -Command \
        "\$p='$cache'; \$i=\$input | Out-String; Set-Content -Path \$p -Value \$i.TrimEnd() -Encoding UTF8" \
        2>/dev/null || true
}

_win_to_mnt() {
    local p="${1//\\//}"
    echo "/mnt/c${p#C:}"
}

_wslconfig_path() {
    local p
    p=$(powershell.exe -NoProfile -Command '$env:USERPROFILE + "\.wslconfig"' 2>/dev/null | tr -d '\r\n')
    [[ -n "$p" ]] && _win_to_mnt "$p"
}

_doctor_section() { echo ""; echo "== $1 =="; }

_check_port_conflicts() {
    local in_use
    in_use=$(ss -tlnp 2>/dev/null)

    # DB port: remap silently -- safe to move because clients never connect to it directly
    if echo "$in_use" | grep -q ':3306[[:space:]]'; then
        if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'ac-database'; then
            if ! grep -q 'DOCKER_DB_EXTERNAL_PORT' .env 2>/dev/null; then
                printf 'DOCKER_DB_EXTERNAL_PORT=13306\n' >> .env
                echo "[dml] Port 3306 in use — remapped DB host port to 13306"
            fi
        fi
    fi

    # Game server ports: warn only -- clients connect to fixed ports, cannot silently remap
    local _ports=(
        "3724:WoW auth/login server (TrinityCore, AzerothCore, MaNGOS)"
        "8085:WoW world server (TrinityCore, AzerothCore)"
        "8086:WoW SOAP API (TrinityCore, AzerothCore)"
        "4000:EverQuest zone server (EQEmu)"
        "5998:EverQuest login server (EQEmu)"
        "5999:EverQuest login server (EQEmu)"
        "9000:EverQuest world/zone server (EQEmu)"
        "2593:Ultima Online game server (ServUO / RunUO)"
        "7171:Tibia game server (OpenTibia / OTServBR)"
        "6112:Blizzard legacy port (Warcraft III / Diablo II)"
        "43594:RuneScape private server (RSPS)"
        "2106:Lineage II login server (L2J)"
        "7777:Lineage II game server (L2J)"
        "54230:Final Fantasy XI auth server (Darkstar)"
        "54231:Final Fantasy XI game server (Darkstar)"
        "44453:Star Wars Galaxies login server"
        "44462:Star Wars Galaxies connection server"
    )
    local entry port desc
    for entry in "${_ports[@]}"; do
        port="${entry%%:*}"
        desc="${entry#*:}"
        if echo "$in_use" | grep -q ":${port}[[:space:]]"; then
            echo "[WARN] Port $port is already in use -- $desc."
            echo "[WARN]   Stop whatever is using port $port before starting this server."
        fi
    done
}

_start_title() {
    local title="$1"
    local mode="${2:-start}"
    local dir="$GAMES_DIR/$title"
    if [[ ! -d "$dir" ]]; then echo "[dml] ERROR: Title not found: $title" >&2; exit 1; fi
    local compose_dir="$dir"
    if ! _has_compose "$dir"; then
        for subdir in "$dir"*/; do
            if [[ -d "$subdir" ]] && _has_compose "$subdir"; then
                compose_dir="$subdir"; break
            fi
        done
    fi
    if ! _has_compose "$compose_dir"; then
        echo "[dml] ERROR: No compose file found in $title or its subdirectories." >&2; exit 1
    fi
    _require_docker
    _ensure_keepalive
    cd "$compose_dir"

    if [[ -x "$dir/dml-start.sh" ]]; then
        echo "[dml] ${mode^}ing $title (staged)..."
        bash "$dir/dml-start.sh" "$mode"
        return
    fi

    _check_port_conflicts
    echo "[dml] ${mode^}ing $title..."
    if [[ "$mode" == "restart" ]]; then
        docker compose down
    fi
    docker compose up -d
    echo "[dml] $title started"
}

cmd="${1:-help}"
shift || true

case "$cmd" in
  doctor)
    echo "[dml] DML Doctor v$VERSION"
    errors=0
    warns=0

    _doctor_section "Linux environment"
    if systemctl is-system-running 2>/dev/null | grep -qE "running|degraded"; then
        echo "[ok]  systemd is running"
    else
        echo "[WARN] systemd is not running -- from Windows: wsl --shutdown, then reopen"
        errors=$((errors + 1))
    fi

    if docker info &>/dev/null; then
        echo "[ok]  Docker Engine is running"
    else
        echo "[WARN] Docker is not responding -- try: sudo systemctl start docker"
        errors=$((errors + 1))
    fi

    if systemctl is-active dml-keepalive.service &>/dev/null; then
        echo "[ok]  dml-keepalive.service is active (WSL idle backup)"
    else
        echo "[WARN] dml-keepalive.service is not active -- WSL may idle-shutdown while playing"
        warns=$((warns + 1))
    fi

    free_kb=$(df /home --output=avail 2>/dev/null | tail -1 | tr -d ' ')
    if [[ "$free_kb" =~ ^[0-9]+$ ]]; then
        free_gb=$(( free_kb / 1024 / 1024 ))
        if (( free_gb >= 20 )); then
            echo "[ok]  Disk space: ${free_gb} GB free on ext4"
        else
            echo "[WARN] Low disk space: ${free_gb} GB free under /home (need 20+ GB)"
            errors=$((errors + 1))
        fi
    else
        echo "[WARN] Could not read disk space for /home"
        errors=$((errors + 1))
    fi

    if curl -fsS --max-time 5 https://www.google.com > /dev/null 2>&1; then
        echo "[ok]  Internet connection"
    else
        echo "[WARN] No internet connection detected"
        warns=$((warns + 1))
    fi

    _doctor_section "WSL stability (disconnects while playing)"
    wslconf=$(_wslconfig_path || true)
    if [[ -n "$wslconf" && -f "$wslconf" ]]; then
        echo "[ok]  .wslconfig found: $wslconf"
        if grep -qE 'instanceIdleTimeout=-1' "$wslconf" 2>/dev/null; then
            echo "[ok]  instanceIdleTimeout=-1 (distro idle disabled)"
        else
            echo "[WARN] instanceIdleTimeout not -1 -- WSL may shut down the distro after ~10 min idle"
            warns=$((warns + 1))
        fi
        if grep -qE 'vmIdleTimeout=-1' "$wslconf" 2>/dev/null; then
            echo "[ok]  vmIdleTimeout=-1 (VM idle disabled)"
        else
            echo "[WARN] vmIdleTimeout not -1 -- WSL VM may shut down after ~60s idle"
            warns=$((warns + 1))
        fi
        if grep -qE 'autoMemoryReclaim=gradual' "$wslconf" 2>/dev/null; then
            echo "[ok]  autoMemoryReclaim=gradual"
        else
            echo "[WARN] autoMemoryReclaim=gradual not set -- WSL may hold RAM when idle"
            warns=$((warns + 1))
        fi
    else
        echo "[WARN] .wslconfig not found -- re-run Install-DML.ps1 as Administrator"
        warns=$((warns + 1))
    fi

    if command -v journalctl &>/dev/null; then
        po_count=$(journalctl -b --no-pager 2>/dev/null | grep -c 'poweroff requested' || true)
        if (( po_count > 0 )); then
            echo "[WARN] $po_count unexpected WSL poweroff event(s) this boot -- check .wslconfig + keepalive"
            warns=$((warns + 1))
        else
            echo "[ok]  No unexpected WSL poweroff events this boot"
        fi
    fi

    if command -v powershell.exe &>/dev/null; then
        _doctor_section "Windows host + WSL"
        # wsl.exe stdout is UTF-16 when piped; strip NULs before text tools
        wsl_ver=$(wsl.exe --version 2>&1 | tr -d '\0' | grep -i 'WSL version' | head -1 | sed 's/.*:[[:space:]]*//' | tr -d '\r' || true)
        if [[ -n "$wsl_ver" ]]; then
            echo "[ok]  WSL version: $wsl_ver"
        else
            echo "[WARN] Could not read WSL version -- run: wsl --update"
            warns=$((warns + 1))
        fi
        # wsl -l -v emits UTF-16 when piped; strip NULs before grep
        wsl_line=$(wsl.exe -l -v 2>/dev/null | tr -d '\0' | grep -i 'dml-arch' | head -1 || true)
        if [[ "$wsl_line" == *Running* ]]; then
            echo "[ok]  WSL distro dml-arch: Running (Windows view)"
        elif [[ "$wsl_line" == *Stopped* ]]; then
            echo "[ok]  WSL distro dml-arch: Stopped (RAM released)"
            if docker ps -q 2>/dev/null | grep -q .; then
                echo "[WARN] dml-arch Stopped on Windows but Docker has containers -- stale state?"
                warns=$((warns + 1))
            fi
        else
            echo "[WARN] dml-arch not found in 'wsl -l -v' -- re-run Install-DML.ps1"
            warns=$((warns + 1))
        fi
        vmmem_mb=$(powershell.exe -NoProfile -Command \
            "\$p=Get-Process vmmem,VmmemWSL -EA SilentlyContinue | Select-Object -First 1; if(\$p){[math]::Round(\$p.WorkingSet64/1MB)}else{'0'}" \
            2>/dev/null | tr -d '\r\n')
        if [[ "$vmmem_mb" =~ ^[0-9]+$ ]]; then
            if (( vmmem_mb == 0 )); then
                echo "[ok]  Vmmem not active (WSL VM fully released)"
            elif [[ "$wsl_line" == *Stopped* ]] && (( vmmem_mb > 150 )); then
                echo "[WARN] Vmmem ${vmmem_mb} MB but dml-arch Stopped -- WSL VM still loaded; run: wsl --shutdown"
                warns=$((warns + 1))
            elif docker ps -q 2>/dev/null | grep -q .; then
                echo "[ok]  Vmmem RAM: ${vmmem_mb} MB (servers running)"
            elif (( vmmem_mb > 500 )); then
                echo "[WARN] Vmmem ${vmmem_mb} MB with no containers -- Stop from tray to release RAM"
                warns=$((warns + 1))
            else
                echo "[ok]  Vmmem RAM: ${vmmem_mb} MB"
            fi
        fi

        _doctor_section "Windows DML install ($DML_WIN_ROOT)"
        win_root_mnt=$(_win_to_mnt "$DML_WIN_ROOT")
        for script in WSL-Keepalive.ps1 DML-Ensure-Keepalive.ps1 DML-Release-WSL.ps1 DML-Launcher.exe dml-titles.cache; do
            if [[ -f "$win_root_mnt/$script" ]]; then
                echo "[ok]  $script present"
            else
                echo "[WARN] Missing $DML_WIN_ROOT\\$script -- re-run Install-DML.ps1"
                warns=$((warns + 1))
            fi
        done
        launcher_ver=$(grep -oE 'VERSION\s*=\s*"[0-9.]+"' "$win_root_mnt/DML-Launcher.cs" 2>/dev/null | head -1 | grep -oE '[0-9.]+' || true)
        if [[ -n "$launcher_ver" ]]; then
            echo "[ok]  DML Launcher source version: v$launcher_ver"
        fi
        if powershell.exe -NoProfile -Command 'Get-Process DML-Launcher -EA SilentlyContinue' &>/dev/null; then
            echo "[ok]  DML Launcher tray app is running"
        else
            echo "[WARN] DML Launcher is not running -- start $DML_WIN_ROOT\\DML-Launcher.exe"
            warns=$((warns + 1))
        fi
        legacy_vbs=$(powershell.exe -NoProfile -Command \
            "Test-Path (Join-Path \$env:APPDATA 'Microsoft\\Windows\\Start Menu\\Programs\\Startup\\DML-WSL-Keepalive.vbs')" \
            2>/dev/null | tr -d '\r\n')
        if [[ "$legacy_vbs" == "True" ]]; then
            echo "[WARN] Legacy always-on keepalive VBS in Startup -- remove or re-run Install-DML.ps1"
            warns=$((warns + 1))
        else
            echo "[ok]  No legacy always-on keepalive in Startup"
        fi
        if schtasks /Query /TN 'DML-WSL-Keepalive' &>/dev/null; then
            echo "[WARN] Legacy scheduled task DML-WSL-Keepalive exists -- re-run Install-DML.ps1 to remove"
            warns=$((warns + 1))
        fi

        _doctor_section "Windows keepalive + tray state"
        ka_count=$(powershell.exe -NoProfile -Command \
            "(Get-CimInstance Win32_Process -EA SilentlyContinue | Where-Object { \$_.CommandLine -match 'WSL-Keepalive|sleep.+infinity' }).Count" \
            2>/dev/null | tr -d '\r\n')
        if [[ "$ka_count" =~ ^[0-9]+$ ]] && (( ka_count > 0 )); then
            echo "[ok]  Windows WSL keepalive running ($ka_count process(es))"
        elif docker ps -q 2>/dev/null | grep -q .; then
            echo "[WARN] Game containers running but Windows keepalive is off -- WSL may idle-shutdown"
            warns=$((warns + 1))
        else
            echo "[ok]  Windows keepalive off (no game servers running)"
        fi
        stopped_marker="$win_root_mnt/.dml-servers-stopped"
        if [[ -f "$stopped_marker" ]] && docker ps -q 2>/dev/null | grep -q .; then
            echo "[WARN] .dml-servers-stopped marker exists but containers are running -- tray may show Stopped"
            warns=$((warns + 1))
        elif [[ -f "$stopped_marker" ]]; then
            echo "[ok]  .dml-servers-stopped marker set (intentional stop / RAM released)"
        fi
        if [[ -f "$win_root_mnt/dml-titles.cache" ]]; then
            cache_lines=$(wc -l < "$win_root_mnt/dml-titles.cache" | tr -d ' ')
            echo "[ok]  dml-titles.cache: $cache_lines title(s) (tray uses when WSL is Stopped)"
        else
            echo "[WARN] dml-titles.cache missing -- tray may be empty after reboot; run 'dml list'"
            warns=$((warns + 1))
        fi
        win_ports=$(powershell.exe -NoProfile -Command \
            "(Get-NetTCPConnection -State Listen -EA SilentlyContinue | Where-Object { \$_.LocalPort -in 3724,8085 } | ForEach-Object { \$_.LocalPort }) -join ','" \
            2>/dev/null | tr -d '\r\n')
        if docker ps -q 2>/dev/null | grep -q .; then
            if [[ "$win_ports" == *3724* ]]; then
                echo "[ok]  Windows forwarding: port 3724 listening"
            else
                echo "[WARN] Port 3724 not listening on Windows -- WoW client may not connect"
                warns=$((warns + 1))
            fi
            if [[ "$win_ports" == *8085* ]]; then
                echo "[ok]  Windows forwarding: port 8085 listening"
            else
                echo "[WARN] Port 8085 not listening on Windows"
                warns=$((warns + 1))
            fi
        fi
    else
        _doctor_section "Windows host"
        echo "[INFO] powershell.exe unavailable -- skipping Windows checks (not WSL-on-Windows?)"
    fi

    _doctor_section "Installed titles"
    title_count=0
    running_count=0
    if [[ -d "$GAMES_DIR" ]]; then
        for dir in "$GAMES_DIR"/*/; do
            [[ -d "$dir" ]] || continue
            title=$(basename "$dir")
            compose_dir="$dir"
            if ! _has_compose "$dir"; then
                for subdir in "$dir"*/; do
                    [[ -d "$subdir" ]] && _has_compose "$subdir" && compose_dir="$subdir" && break
                done
            fi
            [[ -d "$compose_dir" ]] && _has_compose "$compose_dir" || continue
            title_count=$((title_count + 1))
            rc=$(_compose_running "$compose_dir")
            if (( rc > 0 )); then
                running_count=$((running_count + 1))
                echo "[ok]  $title: running ($rc container(s))"
            else
                echo "[ok]  $title: stopped"
            fi
            if [[ -x "$dir/dml-start.sh" ]]; then
                echo "[ok]    dml-start.sh present (safe restart)"
            elif [[ -f "$dir/dml-start.sh" ]]; then
                echo "[WARN]  $title: dml-start.sh not executable"
                warns=$((warns + 1))
            else
                echo "[WARN]  $title: no dml-start.sh -- 'dml restart' may re-import DB (WoW/AzerothCore)"
                warns=$((warns + 1))
            fi
        done
    fi
    if (( title_count == 0 )); then
        echo "[INFO] No installed titles with compose files"
    fi

    if (( running_count > 0 )); then
        if ss -tln 2>/dev/null | grep -q ':3724 '; then
            echo "[ok]  WoW auth port 3724 listening"
        else
            echo "[WARN] Containers running but port 3724 not listening yet"
            warns=$((warns + 1))
        fi
        if ss -tln 2>/dev/null | grep -q ':8085 '; then
            echo "[ok]  WoW world port 8085 listening"
        else
            echo "[WARN] Containers running but port 8085 not listening yet"
            warns=$((warns + 1))
        fi
    fi

    echo ""
    if (( errors == 0 && warns == 0 )); then
        echo "[ok]  All checks passed."
    elif (( errors == 0 )); then
        echo "[dml] $warns warning(s), 0 critical issue(s). Share this output if you need help."
    else
        echo "[dml] $errors critical issue(s), $warns warning(s)."
    fi
    ;;

  list)
    if [[ ! -d "$GAMES_DIR" ]]; then
        echo "[dml] No titles installed yet. Run 'dml run <url>' to install one."
        exit 0
    fi
    found=0
    declare -A _list_seen
    for dir in "$GAMES_DIR"/*/; do
        [[ -d "$dir" ]] || continue
        title=$(basename "$dir")
        if _has_compose "$dir" || [[ -f "$dir/install.sh" ]]; then
            echo "$title"
            found=$((found + 1))
            _list_seen["$title"]=1
        else
            for subdir in "$dir"*/; do
                [[ -d "$subdir" ]] || continue
                [[ -n "${_list_seen[$title]:-}" ]] && continue
                if _has_compose "$subdir" || [[ -f "$subdir/install.sh" ]]; then
                    echo "$title"
                    found=$((found + 1))
                    _list_seen["$title"]=1
                    break
                fi
            done
        fi
    done
    if [[ $found -eq 0 ]]; then
        echo "[dml] No titles found in $GAMES_DIR"
    else
        _update_titles_cache
    fi
    ;;

  status)
    target="${1:-}"
    if [[ -n "$target" ]]; then
        dir="$GAMES_DIR/$target"
        if [[ ! -d "$dir" ]]; then echo "not-found"; exit 1; fi
        compose_dir="$dir"
        if ! _has_compose "$dir"; then
            for subdir in "$dir"*/; do
                if [[ -d "$subdir" ]] && _has_compose "$subdir"; then
                    compose_dir="$subdir"; break
                fi
            done
        fi
        if _has_compose "$compose_dir"; then
            count=$(_compose_running "$compose_dir")
            if [[ "$count" -gt 0 ]]; then echo "running"; else echo "stopped"; fi
        else
            echo "stopped"
        fi
    else
        [[ ! -d "$GAMES_DIR" ]] && exit 0
        declare -A _seen
        for dir in "$GAMES_DIR"/*/; do
            [[ -d "$dir" ]] || continue
            title=$(basename "$dir")
            if _has_compose "$dir"; then
                count=$(_compose_running "$dir")
                if [[ "$count" -gt 0 ]]; then echo "$title:running"; else echo "$title:stopped"; fi
                _seen["$title"]=1
            else
                # One level deeper -- catches repos with compose file in a subdirectory
                for subdir in "$dir"*/; do
                    [[ -d "$subdir" ]] || continue
                    _has_compose "$subdir" || continue
                    [[ -n "${_seen[$title]:-}" ]] && continue
                    count=$(_compose_running "$subdir")
                    if [[ "$count" -gt 0 ]]; then echo "$title:running"; else echo "$title:stopped"; fi
                    _seen["$title"]=1
                    break
                done
            fi
        done
        # Fallback: catch running Compose projects not found by directory scan
        while IFS= read -r project; do
            [[ -z "$project" ]] && continue
            [[ -n "${_seen[$project]:-}" ]] && continue
            echo "$project:running"
        done < <(docker ps --format '{{index .Labels "com.docker.compose.project"}}' 2>/dev/null | sort -u | grep -v '^$')
        _update_titles_cache
    fi
    ;;

  start)
    title="${1:?Usage: dml start <title>}"
    _start_title "$title" start
    ;;

  restart)
    title="${1:?Usage: dml restart <title>}"
    _start_title "$title" restart
    ;;

  stop)
    title="${1:?Usage: dml stop <title>}"
    dir="$GAMES_DIR/$title"
    if [[ ! -d "$dir" ]]; then echo "[dml] ERROR: Title not found: $title" >&2; exit 1; fi
    compose_dir="$dir"
    if ! _has_compose "$dir"; then
        for subdir in "$dir"*/; do
            if [[ -d "$subdir" ]] && _has_compose "$subdir"; then
                compose_dir="$subdir"; break
            fi
        done
    fi
    if ! _has_compose "$compose_dir"; then
        echo "[dml] ERROR: No compose file found in $title or its subdirectories." >&2; exit 1
    fi
    _require_docker
    cd "$compose_dir"
    echo "[dml] Stopping $title..."
    docker compose down
    echo "[dml] $title stopped"
    running=$(docker ps -q 2>/dev/null | wc -l | tr -d '[:space:]')
    if [[ "$running" -eq 0 ]]; then
      echo "[dml] No servers running — releasing WSL memory to Windows..."
      _release_wsl
    fi
    ;;

  scan)
    _require_docker
    echo "[dml] Scanning for all running containers in dml-arch..."
    echo ""

    total=$(docker ps -q 2>/dev/null | wc -l | tr -d '[:space:]')
    if [[ "$total" -eq 0 ]]; then
        echo "[dml] No running containers found."
        exit 0
    fi

    declare -A _known_ports
    _known_ports["3306"]="MySQL/MariaDB"
    _known_ports["3724"]="WoW auth/login"
    _known_ports["8085"]="WoW world server"
    _known_ports["8086"]="WoW SOAP API"
    _known_ports["4000"]="EQ zone (EQEmu)"
    _known_ports["5998"]="EQ login (EQEmu)"
    _known_ports["5999"]="EQ login (EQEmu)"
    _known_ports["9000"]="EQ world (EQEmu)"
    _known_ports["2593"]="Ultima Online"
    _known_ports["7171"]="Tibia"
    _known_ports["6112"]="Blizzard legacy"
    _known_ports["43594"]="RuneScape (RSPS)"
    _known_ports["2106"]="Lineage II login"
    _known_ports["7777"]="Lineage II game"
    _known_ports["54230"]="FFXI auth"
    _known_ports["54231"]="FFXI game"
    _known_ports["44453"]="SWG login"
    _known_ports["44462"]="SWG connection"

    prev_project="__unset__"
    while IFS='|' read -r cid cname project; do
        if [[ "$project" != "$prev_project" ]]; then
            [[ "$prev_project" != "__unset__" ]] && echo ""
            if [[ -z "$project" ]]; then
                echo "[ standalone containers -- no compose project ]"
            else
                echo "[ project: $project ]"
            fi
            prev_project="$project"
        fi
        printf "  %-40s  %s\n" "$cname" "$cid"
        while IFS= read -r pline; do
            [[ -z "$pline" ]] && continue
            hostport=$(echo "$pline" | grep -oE ':[0-9]+$' | tr -d ':')
            note="${_known_ports[$hostport]:-}"
            if [[ -n "$note" ]]; then
                printf "    %-36s  [%s]\n" "$pline" "$note"
            else
                printf "    %s\n" "$pline"
            fi
        done < <(docker port "$cid" 2>/dev/null)
    done < <(docker ps --format '{{.ID}}|{{.Names}}|{{index .Labels "com.docker.compose.project"}}' \
             2>/dev/null | sort -t'|' -k3)

    echo ""
    echo "[dml] To stop a project: dml kill <project-name>  or  dml kill --all"
    ;;

  kill)
    _require_docker
    target="${1:-}"
    if [[ -z "$target" ]]; then
        echo "[dml] Usage: dml kill <project-name> | --all" >&2
        exit 1
    fi

    if [[ "$target" == "--all" ]]; then
        running=$(docker ps -q 2>/dev/null)
        if [[ -z "$running" ]]; then
            echo "[dml] No running containers to stop."
            exit 0
        fi
        count=$(echo "$running" | wc -l | tr -d '[:space:]')
        echo "[dml] Stopping $count running container(s)..."
        echo "$running" | xargs docker stop 2>/dev/null || true
        echo "$running" | xargs docker rm -f 2>/dev/null || true
        docker network prune -f 2>/dev/null || true
        echo "[ok]  All containers stopped, removed, and orphaned networks pruned."
    else
        # Find containers by project label -- works with any compose version, no directory needed
        containers=$(docker ps -q --filter "label=com.docker.compose.project=$target" 2>/dev/null)
        if [[ -z "$containers" ]]; then
            echo "[dml] ERROR: No running containers found for project '$target'." >&2
            echo "[dml]   Run 'dml scan' to see what is currently running." >&2
            exit 1
        fi
        count=$(echo "$containers" | wc -l | tr -d '[:space:]')
        echo "[dml] Stopping $count container(s) for project '$target'..."
        echo "$containers" | xargs docker stop 2>/dev/null || true
        # Compose down cleans up networks and volumes; fall back to direct rm if unavailable
        if ! docker compose -p "$target" down 2>/dev/null; then
            echo "$containers" | xargs docker rm -f 2>/dev/null || true
        fi
        echo "[ok]  '$target' stopped."
    fi
    ;;

  clean)
    _require_docker
    yes_flag="${1:-}"
    _confirm() {
        local prompt="$1" ans
        if [[ "$yes_flag" == "--yes" ]]; then return 0; fi
        read -rp "    $prompt [y/N] " ans
        [[ "$ans" =~ ^[Yy] ]]
    }

    echo "[dml] Running DML cleanup..."
    echo ""

    # 1. Stop DML-managed containers (compose-project containers only; standalone containers not touched)
    running=$(docker ps -q --filter "label=com.docker.compose.project" 2>/dev/null)
    if [[ -n "$running" ]]; then
        count=$(echo "$running" | wc -l | tr -d '[:space:]')
        echo "[dml] $count Docker Compose container(s) found:"
        docker ps --filter "label=com.docker.compose.project" \
            --format '  {{.Names}}  (project: {{index .Labels "com.docker.compose.project"}})' 2>/dev/null
        echo ""
        echo "  Note: standalone containers not part of a compose project are not affected."
        echo ""
        if _confirm "Stop these containers?"; then
            echo "$running" | xargs docker stop 2>/dev/null || true
            echo "$running" | xargs docker rm -f 2>/dev/null || true
            echo "[ok]  Containers stopped."
        fi
    else
        echo "[ok]  No running Docker Compose containers found."
    fi
    echo ""

    # 2. Identify and optionally remove incomplete install directories
    if [[ -d "$GAMES_DIR" ]]; then
        echo "[dml] Checking $GAMES_DIR for incomplete installs..."
        declare -a incomplete
        for dir in "$GAMES_DIR"/*/; do
            [[ -d "$dir" ]] || continue
            if ! _has_compose "$dir" && [[ ! -f "$dir/install.sh" ]]; then
                found_nested=0
                for subdir in "$dir"*/; do
                    if [[ -d "$subdir" ]] && ( _has_compose "$subdir" || [[ -f "$subdir/install.sh" ]] ); then
                        found_nested=1; break
                    fi
                done
                [[ $found_nested -eq 0 ]] && incomplete+=("$dir")
            fi
        done

        if [[ ${#incomplete[@]} -gt 0 ]]; then
            echo "[dml] Incomplete directories (no compose file or install.sh found):"
            for d in "${incomplete[@]}"; do echo "    $(basename "$d")  ($d)"; done
            echo ""
            if _confirm "Remove these directories?"; then
                for d in "${incomplete[@]}"; do
                    [[ -z "$d" ]] && continue
                    rm -rf "$d" && echo "[ok]  Removed: $(basename "$d")"
                done
            fi
        else
            echo "[ok]  No incomplete install directories found."
        fi
    fi
    echo ""

    # 3. Docker prune
    dangling=$(docker images -f dangling=true -q 2>/dev/null | wc -l | tr -d '[:space:]')
    stopped_ct=$(docker ps -a -q --filter status=exited 2>/dev/null | wc -l | tr -d '[:space:]')
    echo "[dml] Docker: $dangling dangling image(s), $stopped_ct exited container(s)."
    if [[ "$dangling" -gt 0 || "$stopped_ct" -gt 0 ]]; then
        if _confirm "Run docker system prune? Warning: removes ALL unused Docker resources system-wide, not just DML ones."; then
            docker system prune -f
            echo "[ok]  Docker pruned."
        fi
    else
        echo "[ok]  Docker is already clean."
    fi
    echo ""
    echo "[ok]  Cleanup complete."
    ;;

  shell)
    exec bash --login
    ;;

  run)
    target="${1:?Usage: dml run <git-url|local-path>}"
    _require_docker
    mkdir -p "$GAMES_DIR"

    if [[ "$target" == /* ]]; then
        if [[ ! -d "$target" ]]; then
            echo "[dml] ERROR: Local path not found: $target" >&2; exit 1
        fi
        repo_name=$(basename "$target")
        clone_dir="$GAMES_DIR/$repo_name"
        if [[ -d "$clone_dir" ]]; then
            echo "[dml] $repo_name already exists in games dir -- skipping copy"
        else
            echo "[dml] Copying $target -> $clone_dir ..."
            cp -r "$target" "$clone_dir"
        fi
    else
        repo_name=$(basename "$target" .git)
        clone_dir="$GAMES_DIR/$repo_name"
        if [[ -d "$clone_dir/.git" ]]; then
            echo "[dml] $repo_name already cloned -- pulling latest"
            git -C "$clone_dir" pull
        else
            echo "[dml] Cloning $target ..."
            git clone "$target" "$clone_dir"
        fi
    fi

    entrypoint="install.sh"
    if [[ -f "$clone_dir/dml.manifest" ]]; then
        declared=$(jq -r '.entrypoint // empty' "$clone_dir/dml.manifest" 2>/dev/null || true)
        [[ -n "$declared" ]] && entrypoint="$declared"
    fi

    if [[ ! -f "$clone_dir/$entrypoint" ]]; then
        echo "[dml] ERROR: $entrypoint not found in $repo_name" >&2
        echo "[dml] This repo may not follow the DML convention (install.sh at root)." >&2
        exit 1
    fi

    echo "[dml] Starting $entrypoint from $repo_name ..."
    cd "$clone_dir"
    exec bash "$entrypoint"
    ;;

  version)
    echo "dml v$VERSION"
    ;;

  help|--help|-h)
    echo "dml -- Dad's MMO Lab CLI v$VERSION"
    echo ""
    echo "Commands:"
    echo "  doctor                diagnose Linux + Windows/WSL issues (idle shutdown, tray, RAM)"
    echo "  list                  list installed titles"
    echo "  status [<title>]      show running/stopped status (all titles if no arg)"
    echo "  start <title>         start a title's Docker server"
    echo "  restart <title>       restart a title (uses dml-start.sh if present)"
    echo "  stop <title>          stop a title; releases WSL RAM if no servers left"
    echo "  scan                  show all running containers and which game ports they hold"
    echo "  kill <name|--all>     force-stop by project name (no directory needed)"
    echo "  clean [--yes]         stop stuck containers, remove incomplete installs, prune Docker"
    echo "  shell                 open an interactive shell"
    echo "  run <url|path>        install a title from GitHub URL or local folder"
    echo "  version               print version"
    echo ""
    echo "Game data lives in /home/dml/ (ext4), never /mnt/c."
    ;;

  *)
    echo "[dml] Unknown command: $cmd" >&2
    echo "Run 'dml help' for usage." >&2
    exit 1
    ;;
esac
'@

        # Convert CRLF -> LF before encoding so the installed script has Unix line endings
        $DmlCliWinRoot = ($InstallRoot -replace '\\', '/')
        $DmlCliResolved = $DmlCli.Replace('__DML_INSTALL_ROOT__', $DmlCliWinRoot).Replace('__DML_CLI_VERSION__', $DmlCliVersion).Replace("`r`n", "`n")
        $DmlCliBytes = [System.Text.Encoding]::UTF8.GetBytes($DmlCliResolved)
        $DmlCliB64   = [Convert]::ToBase64String($DmlCliBytes)

        $exit10 = Invoke-WslBash -Distro $DmlDistroName -User root -Label 'phase3' -Script @"
set -euo pipefail
echo "[phase3] Installing core dependencies..."
pacman -S --noconfirm --needed base-devel git curl jq

echo "[phase3] Installing dml CLI..."
printf '%s' '$DmlCliB64' | base64 -d > /usr/local/bin/dml
chmod 0755 /usr/local/bin/dml

echo "[phase3] Verifying dml CLI..."
dml version
dml status >/dev/null 2>&1 || true

echo "[phase3] Installing Linux keepalive service (backup alongside vmIdleTimeout=-1)..."
cat > /usr/local/bin/dml-keepalive.sh << 'KEEPEOF'
#!/usr/bin/env bash
set -euo pipefail
while true; do sleep 3600; done
KEEPEOF
chmod 0755 /usr/local/bin/dml-keepalive.sh
cat > /etc/systemd/system/dml-keepalive.service << 'KEEPEOF'
[Unit]
Description=DML WSL keepalive (prevents vm idle poweroff)
After=network.target docker.service
Wants=docker.service

[Service]
Type=simple
ExecStart=/usr/local/bin/dml-keepalive.sh
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
KEEPEOF
systemctl daemon-reload
systemctl enable --now dml-keepalive.service

echo "[phase3] Done"
"@
        if ($exit10 -ne 0) {
            Write-Fail "Phase 3 bootstrap failed (exit $exit10)`nCheck the log: $LogFile"
        }
        Write-Ok "Core dependencies and dml CLI installed"
        Mark-StepDone 'phase3-bootstrap'
    }

    # -------------------------------------------------------------------------
    # Step 11: Compile DML Launcher (Windows system tray app)
    # Uses csc.exe from .NET Framework 4.8 -- pre-installed on Windows 10/11.
    # Output: $InstallRoot\DML-Launcher.exe + Desktop shortcut + startup shortcut.
    # -------------------------------------------------------------------------
    # Always write the icon so re-runs and upgrades pick it up
    $LauncherDir = $InstallRoot
    New-Item -ItemType Directory -Force -Path $LauncherDir | Out-Null
    # Write bundled icon (base64-encoded dml.ico)
            $IcoPath = "$LauncherDir\dml.ico"
            $IcoB64  = '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'
            [System.IO.File]::WriteAllBytes($IcoPath, [System.Convert]::FromBase64String($IcoB64))
            Write-Diag "Icon written: $IcoPath"

    Write-Step "Compiling DML Launcher (system tray app)..."

    $CscPath = Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
    if (-not (Test-Path $CscPath)) {
        Write-Warn "csc.exe not found at $CscPath -- skipping launcher (DML environment still fully works)."
    } else {
            $LauncherCs  = "$LauncherDir\DML-Launcher.cs"
            $LauncherExe = "$LauncherDir\DML-Launcher.exe"

            $LauncherSource = @'
// DML-Launcher.cs -- Dad's MMO Lab system tray launcher
// Compiled at install time: csc.exe /target:winexe /r:System.Windows.Forms.dll /r:System.Drawing.dll

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Forms;

class DmlLauncherEntry
{
    [STAThread]
    static void Main()
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new TrayApp());
    }
}

class TrayApp : ApplicationContext
{
    const string DISTRO   = "dml-arch";
    const string VERSION  = "__DML_CLI_VERSION__";

    string TrayTooltip(bool serverActive)
    {
        return serverActive
            ? "DML Launcher v" + VERSION + " — Server Active"
            : "DML Launcher v" + VERSION;
    }

    string TitlesCachePath {
        get {
            return System.IO.Path.Combine(
                System.IO.Path.GetDirectoryName(System.Reflection.Assembly.GetExecutingAssembly().Location),
                "dml-titles.cache");
        }
    }

    // True only when dml-arch is Running — wsl -l -v does NOT boot the distro.
    static bool IsDistroRunning()
    {
        try
        {
            var psi = new ProcessStartInfo();
            psi.FileName               = "wsl.exe";
            psi.Arguments              = "-l -v";
            psi.UseShellExecute        = false;
            psi.RedirectStandardOutput = true;
            psi.CreateNoWindow         = true;
            // wsl -l -v emits UTF-16 LE; UTF-8 decoding breaks "Running" matching
            psi.StandardOutputEncoding = Encoding.Unicode;
            using (var p = Process.Start(psi))
            {
                string output = p.StandardOutput.ReadToEnd();
                p.WaitForExit(5000);
                foreach (var line in output.Split(new char[] { '\n', '\r' }, StringSplitOptions.RemoveEmptyEntries))
                {
                    string trimmed = line.Trim();
                    if (trimmed.StartsWith(DISTRO, StringComparison.OrdinalIgnoreCase)
                        || trimmed.StartsWith("* " + DISTRO, StringComparison.OrdinalIgnoreCase))
                        return trimmed.IndexOf("Running", StringComparison.OrdinalIgnoreCase) >= 0;
                }
            }
        }
        catch { }
        return false;
    }

    void SaveTitleCache(System.Collections.Generic.IEnumerable<string> titles)
    {
        try
        {
            System.IO.File.WriteAllLines(TitlesCachePath, titles);
        }
        catch { }
    }

    string[] LoadTitleCache()
    {
        try
        {
            if (System.IO.File.Exists(TitlesCachePath))
                return System.IO.File.ReadAllLines(TitlesCachePath);
        }
        catch { }
        return new string[0];
    }

    string BuildStoppedStatusOutput()
    {
        var titles = LoadTitleCache();
        if (titles.Length == 0) return "";
        var lines = new System.Collections.Generic.List<string>();
        foreach (var t in titles)
        {
            string title = (t ?? "").Trim();
            if (title.Length > 0) lines.Add(title + ":stopped");
        }
        return string.Join("\n", lines);
    }

    // Prevents Windows from sleeping while a server is running.
    // ES_CONTINUOUS makes the state persist until explicitly released.
    // ES_SYSTEM_REQUIRED blocks sleep without requiring the display to stay on.
    [DllImport("kernel32.dll")] static extern uint SetThreadExecutionState(uint esFlags);
    const uint ES_CONTINUOUS      = 0x80000000;
    const uint ES_SYSTEM_REQUIRED = 0x00000001;

    NotifyIcon _tray;

    public TrayApp()
    {
        _tray = new NotifyIcon();
        string icoPath = System.IO.Path.Combine(
            System.IO.Path.GetDirectoryName(System.Reflection.Assembly.GetExecutingAssembly().Location),
            "dml.ico");
        _tray.Icon = System.IO.File.Exists(icoPath) ? new Icon(icoPath) : SystemIcons.Application;
        _tray.Text    = TrayTooltip(false);
        _tray.Visible = true;

        var menu = new ContextMenuStrip();
        menu.Opening += OnMenuOpening;
        _tray.ContextMenuStrip = menu;

        // Check server state at startup so sleep is blocked immediately
        // if a server is already running when the tray loads.
        var startupTimer = new System.Windows.Forms.Timer { Interval = 3000 };
        startupTimer.Tick += delegate {
            startupTimer.Stop(); startupTimer.Dispose();
            string[] r = { null };
            var pollTimer = new System.Windows.Forms.Timer { Interval = 150 };
            pollTimer.Tick += delegate {
                if (r[0] == null) return;
                pollTimer.Stop(); pollTimer.Dispose();
                UpdateSleepLock(CountRunning(r[0]));
            };
            pollTimer.Start();
            System.Threading.ThreadPool.QueueUserWorkItem(_ => {
                try {
                    r[0] = IsDistroRunning() ? WslRun("dml status") : BuildStoppedStatusOutput();
                } catch { r[0] = BuildStoppedStatusOutput(); }
            });
        };
        startupTimer.Start();
    }

    // Blocks or releases Windows sleep based on how many servers are running.
    void UpdateSleepLock(int runningCount)
    {
        if (runningCount > 0)
        {
            SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED);
            _tray.Text = TrayTooltip(true);
        }
        else
        {
            SetThreadExecutionState(ES_CONTINUOUS);  // release
            _tray.Text = TrayTooltip(false);
        }
    }

    static int CountRunning(string statusOut)
    {
        int count = 0;
        foreach (var line in statusOut.Split(new char[] { '\n', '\r' }, StringSplitOptions.RemoveEmptyEntries))
        {
            int colon = line.Trim().IndexOf(':');
            if (colon > 0 && line.Trim().Substring(colon + 1).Trim()
                    .Equals("running", StringComparison.OrdinalIgnoreCase))
                count++;
        }
        return count;
    }

    void OnMenuOpening(object sender, System.ComponentModel.CancelEventArgs e)
    {
        var menu = (ContextMenuStrip)sender;
        menu.Items.Clear();

        var header = new ToolStripMenuItem("DML Launcher v" + VERSION);
        header.Enabled = false;
        header.Font = new Font(SystemFonts.MenuFont, FontStyle.Bold);
        menu.Items.Add(header);
        menu.Items.Add(new ToolStripSeparator());

        // Placeholder shown immediately — menu pops up with no delay
        var placeholder = new ToolStripMenuItem("Checking servers...");
        placeholder.Enabled = false;
        placeholder.Tag = "placeholder";
        menu.Items.Add(placeholder);

        menu.Items.Add(new ToolStripSeparator());
        AddStaticItems(menu);

        // Shared slot for background result
        string[] result = new string[1];

        // Timer fires on the UI thread — no Invoke/handle needed
        var timer = new System.Windows.Forms.Timer();
        timer.Interval = 150;
        timer.Tick += delegate
        {
            if (result[0] == null) return;   // not ready yet
            timer.Stop();
            timer.Dispose();
            if (menu.IsDisposed) return;

            int idx = -1;
            for (int i = 0; i < menu.Items.Count; i++)
                if ("placeholder".Equals(menu.Items[i].Tag as string)) { idx = i; break; }
            if (idx < 0) return;

            menu.Items.RemoveAt(idx);
            var items = BuildTitleItems(result[0]);
            for (int i = items.Count - 1; i >= 0; i--)
                menu.Items.Insert(idx, items[i]);
        };

        // Clean up timer if the menu closes before the result arrives
        menu.Closed += delegate { timer.Stop(); timer.Dispose(); };

        timer.Start();

        // Background thread — do NOT boot WSL just to check status (that auto-starts Docker).
        System.Threading.ThreadPool.QueueUserWorkItem(delegate
        {
            try {
                result[0] = IsDistroRunning() ? WslRun("dml status") : BuildStoppedStatusOutput();
            } catch {
                result[0] = BuildStoppedStatusOutput();
            }
        });
    }

    System.Collections.Generic.List<ToolStripItem> BuildTitleItems(string statusOut)
    {
        var items     = new System.Collections.Generic.List<ToolStripItem>();
        var statusMap = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        int runningCount = 0;

        foreach (var line in statusOut.Split(new char[] { '\n', '\r' }, StringSplitOptions.RemoveEmptyEntries))
        {
            string trimmed = line.Trim();
            int colon = trimmed.IndexOf(':');
            if (colon > 0)
                statusMap[trimmed.Substring(0, colon)] = trimmed.Substring(colon + 1);
        }

        if (statusMap.Count == 0)
        {
            var empty = new ToolStripMenuItem("No titles installed");
            empty.Enabled = false;
            items.Add(empty);
            UpdateSleepLock(0);
            return items;
        }

        foreach (var kv in statusMap)
        {
            string title   = kv.Key;
            bool   running = string.Equals(kv.Value, "running", StringComparison.OrdinalIgnoreCase);
            if (running) runningCount++;

            var gameMenu  = new ToolStripMenuItem(title);
            var statusLbl = new ToolStripMenuItem(running ? "● Running" : "○ Stopped");
            statusLbl.Enabled = false;
            gameMenu.DropDownItems.Add(statusLbl);
            gameMenu.DropDownItems.Add(new ToolStripSeparator());

            var startItem   = new ToolStripMenuItem("Start");
            var restartItem = new ToolStripMenuItem("Restart");
            var stopItem    = new ToolStripMenuItem("Stop");
            startItem.Enabled   = !running;
            restartItem.Enabled =  running;
            stopItem.Enabled    =  running;

            string captured = title;
            startItem.Click   += delegate { RunAndReport("start",   captured); };
            restartItem.Click += delegate { RunAndReport("restart", captured); };
            stopItem.Click    += delegate { RunAndReport("stop",    captured); };

            gameMenu.DropDownItems.Add(startItem);
            gameMenu.DropDownItems.Add(restartItem);
            gameMenu.DropDownItems.Add(stopItem);
            items.Add(gameMenu);
        }

        SaveTitleCache(statusMap.Keys);
        UpdateSleepLock(runningCount);
        return items;
    }

    void AddStaticItems(ContextMenuStrip menu)
    {
        var installItem = new ToolStripMenuItem("Install New Title...");
        installItem.Click += delegate { ShowInstallDialog(); };
        menu.Items.Add(installItem);

        var shellItem = new ToolStripMenuItem("Open DML Shell");
        shellItem.Click += delegate { OpenTerminal("-d " + DISTRO); };
        menu.Items.Add(shellItem);

        var doctorItem = new ToolStripMenuItem("Run dml doctor");
        doctorItem.Click += delegate
        {
            string result = WslRun("dml doctor");
            MessageBoxIcon icon = result.Contains("[WARN]") ? MessageBoxIcon.Warning : MessageBoxIcon.Information;
            MessageBox.Show(result, "DML Doctor", MessageBoxButtons.OK, icon);
        };
        menu.Items.Add(doctorItem);

        menu.Items.Add(new ToolStripSeparator());

        var exitItem = new ToolStripMenuItem("Exit");
        exitItem.Click += delegate {
            SetThreadExecutionState(ES_CONTINUOUS);  // always release before exit
            _tray.Visible = false;
            _tray.Dispose();
            Application.Exit();
        };
        menu.Items.Add(exitItem);
    }

    void TriggerReleaseWsl()
    {
        try
        {
            string ps1 = System.IO.Path.Combine(
                System.IO.Path.GetDirectoryName(System.Reflection.Assembly.GetExecutingAssembly().Location),
                "DML-Release-WSL.ps1");
            if (!System.IO.File.Exists(ps1)) return;
            var psi = new ProcessStartInfo();
            psi.FileName = "powershell.exe";
            psi.Arguments = "-NoProfile -WindowStyle Hidden -File \"" + ps1 + "\"";
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.WindowStyle = ProcessWindowStyle.Hidden;
            Process.Start(psi);
        }
        catch { }
    }

    void RunAndReport(string cmd, string title)
    {
        string result  = WslRun("dml " + cmd + " " + title);
        string caption = (cmd == "start" ? "Start " : cmd == "restart" ? "Restart " : "Stop ") + title;
        MessageBoxIcon icon = (result.Contains("[error]") || result.ToLower().Contains("error"))
            ? MessageBoxIcon.Warning : MessageBoxIcon.Information;
        MessageBox.Show(result, caption, MessageBoxButtons.OK, icon);

        if (cmd == "stop" && result.IndexOf("releasing WSL", StringComparison.OrdinalIgnoreCase) >= 0)
            TriggerReleaseWsl();

        // Re-check server state after start/stop so the sleep lock updates
        // without requiring the user to reopen the menu.
        string[] r = { null };
        var pollTimer = new System.Windows.Forms.Timer { Interval = 150 };
        pollTimer.Tick += delegate {
            if (r[0] == null) return;
            pollTimer.Stop(); pollTimer.Dispose();
            UpdateSleepLock(CountRunning(r[0]));
        };
        pollTimer.Start();
        System.Threading.ThreadPool.QueueUserWorkItem(_ => {
            try {
                r[0] = IsDistroRunning() ? WslRun("dml status") : BuildStoppedStatusOutput();
            } catch { r[0] = BuildStoppedStatusOutput(); }
        });
    }

    string WslRun(string wslCmd)
    {
        try
        {
            var psi = new ProcessStartInfo();
            psi.FileName               = "wsl.exe";
            psi.Arguments              = "-d " + DISTRO + " -- " + wslCmd;
            psi.UseShellExecute        = false;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError  = true;
            psi.CreateNoWindow         = true;
            psi.StandardOutputEncoding = Encoding.UTF8;
            using (var p = Process.Start(psi))
            {
                string output = p.StandardOutput.ReadToEnd();
                p.WaitForExit(15000);
                return output.Trim();
            }
        }
        catch (Exception ex)
        {
            return "[error] Could not run WSL: " + ex.Message;
        }
    }

    void OpenTerminal(string wslArgs)
    {
        try
        {
            var psi = new ProcessStartInfo("wt.exe", "wsl " + wslArgs);
            psi.UseShellExecute = true;
            Process.Start(psi);
        }
        catch
        {
            try
            {
                var psi = new ProcessStartInfo("powershell.exe",
                    "-NoExit -Command \"wsl " + wslArgs + "\"");
                psi.UseShellExecute = true;
                Process.Start(psi);
            }
            catch { }
        }
    }

    void ShowInstallDialog()
    {
        using (var form = new Form())
        {
            form.Text            = "Install New DML Title";
            form.Size            = new Size(520, 210);
            form.StartPosition   = FormStartPosition.CenterScreen;
            form.FormBorderStyle = FormBorderStyle.FixedDialog;
            form.MaximizeBox     = false;
            form.MinimizeBox     = false;

            var lbl = new Label();
            lbl.Text   = "GitHub URL, local .sh installer, or folder:";
            lbl.Left   = 10; lbl.Top = 15; lbl.Width = 490; lbl.Height = 20;

            var box = new TextBox();
            box.Left = 10; box.Top = 42; box.Width = 380;

            var btnBrowse = new Button();
            btnBrowse.Text  = "Browse...";
            btnBrowse.Left  = 398; btnBrowse.Top = 40;
            btnBrowse.Width = 100;
            btnBrowse.Click += delegate {
                using (var ofd = new OpenFileDialog())
                {
                    ofd.Title       = "Select a DML installer script";
                    ofd.Filter      = "Shell scripts (*.sh)|*.sh|All files (*.*)|*.*";
                    ofd.FilterIndex = 1;
                    if (ofd.ShowDialog() == DialogResult.OK)
                        box.Text = ofd.FileName;
                }
            };

            var btnOk = new Button();
            btnOk.Text   = "Install"; btnOk.Left = 320; btnOk.Top = 100;
            btnOk.Width  = 85; btnOk.DialogResult = DialogResult.OK;

            var btnCancel = new Button();
            btnCancel.Text   = "Cancel"; btnCancel.Left = 415; btnCancel.Top = 100;
            btnCancel.Width  = 85; btnCancel.DialogResult = DialogResult.Cancel;

            form.Controls.Add(lbl);
            form.Controls.Add(box);
            form.Controls.Add(btnBrowse);
            form.Controls.Add(btnOk);
            form.Controls.Add(btnCancel);
            form.AcceptButton = btnOk;
            form.CancelButton = btnCancel;

            if (form.ShowDialog() == DialogResult.OK)
            {
                string input = box.Text.Trim();
                if (string.IsNullOrEmpty(input)) return;
                string wslPath = ToWslPath(input);
                // .sh file -> run directly with bash; directory or URL -> dml run
                string wslArgs = wslPath.EndsWith(".sh")
                    ? "-d " + DISTRO + " -- bash \"" + wslPath + "\""
                    : "-d " + DISTRO + " -- dml run \"" + wslPath + "\"";
                OpenTerminal(wslArgs);
            }
        }
    }

    static string ToWslPath(string input)
    {
        // Convert C:\path\to\folder -> /mnt/c/path/to/folder
        if (input.Length >= 3 && input[1] == ':' && (input[2] == '\\' || input[2] == '/'))
            return "/mnt/" + input.Substring(0, 1).ToLower() + "/" + input.Substring(3).Replace('\\', '/');
        return input;
    }
}

'@
            $LauncherSource = $LauncherSource.Replace('__DML_CLI_VERSION__', $DmlCliVersion)
            [System.IO.File]::WriteAllText($LauncherCs, $LauncherSource, [System.Text.Encoding]::UTF8)

            Write-Diag "Compiling DML-Launcher.cs -> DML-Launcher.exe..."
            $IcoPath = Join-Path $LauncherDir "dml.ico"
            $iconFlag = if (Test-Path $IcoPath) { "/win32icon:`"$IcoPath`"" } else { "" }
            & $CscPath /target:winexe /optimize+ /nologo `
                /r:System.Windows.Forms.dll /r:System.Drawing.dll `
                $iconFlag /out:"$LauncherExe" "$LauncherCs"
            $cscExit = $LASTEXITCODE
            Write-Diag "csc.exe exit code: $cscExit"

            if ($cscExit -ne 0) {
                Write-Warn "Launcher compilation failed (exit $cscExit) -- DML environment still works without it."
                Write-Warn "Try running Windows Update to repair .NET Framework, then re-run this installer."
            } else {
                Write-Ok "DML-Launcher.exe compiled to $LauncherDir"

                try {
                    $wshShell = New-Object -ComObject WScript.Shell

                    $desktopLnk = $wshShell.CreateShortcut("$env:USERPROFILE\Desktop\DML Launcher.lnk")
                    $desktopLnk.TargetPath = $LauncherExe
                    $desktopLnk.Save()
                    Write-Ok "Desktop shortcut created"

                    $startupDir = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup"
                    $startupLnk = $wshShell.CreateShortcut("$startupDir\DML Launcher.lnk")
                    $startupLnk.TargetPath = $LauncherExe
                    $startupLnk.Save()
                    Write-Ok "Added DML Launcher to Windows startup"
                } catch {
                    Write-Warn "Shortcut creation failed: $($_.Exception.Message)"
                }

        Mark-StepDone 'launcher-v2-icons'
        }
    }

    # -------------------------------------------------------------------------
    # Step 12: WSL resource scripts (keepalive while playing, release RAM on stop)
    # -------------------------------------------------------------------------
    Write-Step "Installing WSL resource management scripts..."

    $KeepalivePs1 = @'
# Holds dml-arch open while a game server is running (started by dml start).
$Distro = 'dml-arch'
while ($true) {
    try {
        $p = Start-Process -FilePath 'wsl.exe' -ArgumentList @('-d', $Distro, '-e', 'sleep', 'infinity') -WindowStyle Hidden -PassThru
        $p.WaitForExit()
    } catch {}
    Start-Sleep -Seconds 3
}
'@
    Set-Content -Path "$InstallRoot\WSL-Keepalive.ps1" -Value $KeepalivePs1 -Encoding UTF8

    $EnsureKeepalivePs1 = @'
$InstallRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $InstallRoot 'WSL-Keepalive.ps1'
if (-not (Test-Path $script)) { exit 0 }
$running = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*$script*" }
if (-not $running) {
    Start-Process pwsh -ArgumentList '-NoProfile', '-WindowStyle', 'Hidden', '-File', $script -WindowStyle Hidden
}
Remove-Item (Join-Path $InstallRoot '.dml-servers-stopped') -Force -ErrorAction SilentlyContinue
'@
    Set-Content -Path "$InstallRoot\DML-Ensure-Keepalive.ps1" -Value $EnsureKeepalivePs1 -Encoding UTF8

    $ReleaseWslPs1 = @'
# Returns WSL RAM to Windows when all game servers are stopped.
# Spawned detached from Windows (or via async Start-Process from dml stop).
param([int]$DelaySeconds = 3)
$Distro = 'dml-arch'
$InstallRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

Start-Sleep -Seconds $DelaySeconds

# Kill Windows-side keepalive loops
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'WSL-Keepalive\.ps1' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# Stop Linux services that pin RAM (auto-start again on next dml start / WSL boot)
wsl.exe -d $Distro -u root -- systemctl stop dml-keepalive.service 2>$null | Out-Null
wsl.exe -d $Distro -u root -- systemctl stop docker 2>$null | Out-Null

Set-Content -Path "$InstallRoot\.dml-servers-stopped" -Value (Get-Date -Format o) -Encoding UTF8

wsl.exe --terminate $Distro 2>$null | Out-Null
Start-Sleep -Milliseconds 500

# --terminate stops the distro; --shutdown kills the WSL VM (VmmemWSL).
# Without --shutdown, Vmmem keeps ~1 GB even when all distros show Stopped.
wsl.exe --shutdown 2>$null | Out-Null

Write-Host "[dml] WSL released - memory returned to Windows"
'@
    Set-Content -Path "$InstallRoot\DML-Release-WSL.ps1" -Value $ReleaseWslPs1 -Encoding UTF8

    # Remove legacy always-on keepalive (now started only by dml start)
    Remove-Item "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\DML-WSL-Keepalive.vbs" -Force -ErrorAction SilentlyContinue
    schtasks /Delete /TN 'DML-WSL-Keepalive' /F 2>$null | Out-Null

    Write-Ok "WSL scripts installed (keepalive on start, release RAM on stop)"

    # -------------------------------------------------------------------------
    # Done
    # -------------------------------------------------------------------------
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "  Your DML environment is ready!" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Installed inside WSL2 (dml-arch):" -ForegroundColor White
    Write-Host "    Arch Linux  +  systemd  +  Docker Engine  +  dml CLI v$DmlCliVersion" -ForegroundColor Green
    Write-Host "  Install location: $InstallRoot" -ForegroundColor DarkGray
    Write-Host ""
    if (Test-Path "$env:USERPROFILE\Desktop\DML Launcher.lnk") {
        Write-Host "  DML Launcher is on your Desktop and starts with Windows." -ForegroundColor White
        Write-Host "  Right-click the tray icon to start/stop titles and install new ones." -ForegroundColor White
    }
    Write-Host ""
    Write-Host "  To run a DML title from the command line:" -ForegroundColor White
    Write-Host "    wsl -d dml-arch" -ForegroundColor Cyan
    Write-Host "    dml run https://github.com/DadsMmoLab/<title>" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Check the Dad's MMO Lab channel for which title to install" -ForegroundColor White
    Write-Host "  first -- some titles have prerequisites." -ForegroundColor White
    Write-Host ""
    Write-Host "  To check your environment at any time:" -ForegroundColor White
    Write-Host "    dml doctor" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Full install log: $LogFile" -ForegroundColor DarkGray
    Write-Host ""
    $null = Read-Host "  Press Enter to close"
}

# =============================================================================
# Entry
# =============================================================================
try {
    if ($ResumePhase2) {
        Invoke-Phase2 -AfterReboot
    } else {
        Invoke-Phase1
    }
} catch {
    if (-not $Script:FailReported) {
        Write-Host ""
        Write-Host "[FAIL] $($_.Exception.Message)" -ForegroundColor Red
    }
    Write-Host ""
    Write-Host "  Installation stopped. Share this log if you need help:" -ForegroundColor Yellow
    Write-Host "  $LogFile" -ForegroundColor Yellow
    exit 1
}
