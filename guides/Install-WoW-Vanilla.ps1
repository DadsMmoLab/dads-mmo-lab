#Requires -Version 5.1
<#
.SYNOPSIS
    Dad's MMO Lab -- Vanilla WoW (1.12.1) Server Installer for Windows
.DESCRIPTION
    Installs a full offline Vanilla WoW server -- CMaNGOS Classic + Playerbots
    + AHBot, compiled from source -- inside the dml-arch WSL2 distro.
    Requires Install-DML.ps1 to have been run first.

    Version: 1.0.0-vanilla-win

    Powered by:
      - cmangos/mangos-classic -- github.com/cmangos/mangos-classic
      - cmangos/playerbots     -- github.com/cmangos/playerbots
      - cmangos/classic-db     -- github.com/cmangos/classic-db

    You MUST own a WoW Vanilla 1.12.1 client (build 5875).
    The installer orchestrates only -- no game assets are downloaded.

    Changelog:
      1.0.0-vanilla-win (2026-07-07): Windows port of install-wow-vanilla.sh
        v1.1.2 (Steam Deck), following the Install-Tortoise-WoW.ps1 house
        pattern. Windows deltas:
        - Runs inside dml-arch (WSL2); Docker/pacman/SteamOS logic dropped --
          Assert-Prerequisites checks the DML substrate instead
        - Server lives in /home/dml/games/wow-vanilla-server (GAMES_DIR) so
          the dml CLI and DML tray see it
        - Extraction reads the client's Data folder READ-ONLY through /mnt
          and writes outputs natively inside dml-arch (the .sh wrote temp
          folders into the user's client folder and moved them out; the
          read-only nested mount means the client is never written to and
          interrupted runs leave no litter in it)
        - Extraction/mmap containers run as the invoking user -- no root-owned
          outputs, no sudo chown step
        - make -j$(nproc) in the Dockerfile (the Deck pinned -j2 for its
          16GB RAM); MoveMapGen threads = nproc capped at 8
        - restart: "no" on all services (no ghost boots at dockerd start; a
          crashed mangosd stays visibly 'exited' for the ready-wait check --
          replaces the .sh's RestartCount loop detection)
        - The db service publishes no ports (unchanged from .sh -- kept that
          way deliberately; never expose MariaDB root to the LAN)
        - Default account auto-created via the mangosd console AND verified
          through a realmd.account row check -- the console pipe's exit code
          is not trusted (docker attach + timeout reports failure even when
          the command worked; the F4 lesson from the tortoise port)
        - Import completion tracked with an explicit marker table
          (realmd.dml_import_complete) created only after content + bots SQL
          import AND verification pass -- table counts alone can't detect an
          interrupted import
        - Skip-detection state machine: image / extracted data / mmaps /
          volume / import-complete / existing password, so re-runs skip the
          multi-hour steps; mmaps can be regenerated without the client
        - Realm row pinned to 127.0.0.1:8085 (WSL2 localhost forwarding);
          name set on fresh install only, so a renamed realm isn't clobbered
        - No Gaming Mode launcher / realmlist chmod tricks -- the DML tray
          owns start/stop; completion screen gives realmlist.wtf contents
        (.sh v1.1.2 carried: spell_template 6-column fix, Playerbots SQL
         import, classic-db Full_DB + Updates + ACID, core sql/updates,
         aiplayerbot 1600-2000 bots / 400 accounts, high-volume AHBot,
         'Avg Diff:' ready signal, MYSQL_PWD env instead of -p -- all kept.)
#>

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# =============================================================================
# Config
# =============================================================================
$WizardVersion       = '1.0.0-vanilla-win'
$DmlDistro           = 'dml-arch'
$DmlUser             = 'dml'
$ServerDir           = '/home/dml/games/wow-vanilla-server'
$LegacyServerDir     = '/home/dml/wow-vanilla-server'
$ServerImage         = 'dml/cmangos-vanilla-server:local'
$ClientBuild         = '5875'
$DbVolume            = 'wow-vanilla-server_db-data'
$LogFile             = "$env:TEMP\dml-vanilla-install.log"
$Script:FailReported = $false
$Script:ClientMount  = $null
$Script:ClientPath   = $null
$Script:DbPassword   = $null

# =============================================================================
# Output helpers
# =============================================================================
function Write-Header {
    Clear-Host
    Write-Host ''
    Write-Host '  +==================================================+' -ForegroundColor Yellow
    Write-Host '  |  DAD''S MMO LAB                                   |' -ForegroundColor White
    Write-Host '  |  Vanilla WoW Server Installer (Windows)          |' -ForegroundColor White
    Write-Host '  |  CMaNGOS Classic + Playerbots + AHBot            |' -ForegroundColor Blue
    Write-Host ("  |" + ("  Version $WizardVersion").PadRight(50) + "|") -ForegroundColor Yellow
    Write-Host '  +==================================================+' -ForegroundColor Yellow
    Write-Host ''
}

function Write-Step([string]$msg) {
    Write-Host ''
    Write-Host '  --------------------------------------------------' -ForegroundColor Yellow
    Write-Host "   $msg" -ForegroundColor White
    Write-Host '  --------------------------------------------------' -ForegroundColor Yellow
}

function Write-Ok([string]$msg) {
    $line = "[ok]   $msg"
    Write-Host $line -ForegroundColor Green
    Add-Content -Path $LogFile -Value "$(Get-Date -Format 'HH:mm:ss') $line" -ErrorAction SilentlyContinue
}
function Write-Warn([string]$msg) {
    $line = "[WARN] $msg"
    Write-Host $line -ForegroundColor Yellow
    Add-Content -Path $LogFile -Value "$(Get-Date -Format 'HH:mm:ss') $line" -ErrorAction SilentlyContinue
}
function Write-Info([string]$msg) {
    $line = "[info] $msg"
    Write-Host $line -ForegroundColor Cyan
    Add-Content -Path $LogFile -Value "$(Get-Date -Format 'HH:mm:ss') $line" -ErrorAction SilentlyContinue
}
function Write-Fail([string]$msg) {
    $line = "[FAIL] $msg"
    Write-Host $line -ForegroundColor Red
    Add-Content -Path $LogFile -Value "$(Get-Date -Format 'HH:mm:ss') $line" -ErrorAction SilentlyContinue
    $Script:FailReported = $true
    throw $msg
}
function Write-Diag([string]$msg) {
    Add-Content -Path $LogFile -Value "$(Get-Date -Format 'HH:mm:ss') [diag] $msg" -ErrorAction SilentlyContinue
}

# =============================================================================
# WSL bash execution -- encoding-safe, EAP-safe (house pattern from
# Install-WoW-WotLK.ps1). Writes the script as raw UTF-8 bytes via \\wsl$\ to
# bypass PowerShell's pipe encoding layer; lowers EAP for the wsl call so
# PS 5.1 does not abort on normal docker/git stderr output. The whole step
# runs in ONE long-lived wsl.exe session -- Windows tears the WSL VM down
# ~13s after the last session exits, which kills MariaDB mid-operation if you
# poll with short calls instead.
# =============================================================================
function Invoke-DmlBash {
    param(
        [string]$Script,
        [string]$Label = 'bash',
        [switch]$AsRoot
    )
    $user = if ($AsRoot) { 'root' } else { $DmlUser }
    Write-Diag "[$Label] running in $DmlDistro as $user"

    # Warm up the distro -- surfaces startup failures with a clear error
    $prevEap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    wsl -d $DmlDistro -u $user -- true | Out-Null
    $warmOk = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prevEap
    if (-not $warmOk) { throw "[$Label] $DmlDistro failed to start (exit $LASTEXITCODE)" }

    # Wait for \\wsl$\ share (up to 9 s)
    $wslTmp = "\\wsl`$\$DmlDistro\tmp"
    $shareReady = $false
    for ($i = 0; $i -lt 3; $i++) {
        if (Test-Path $wslTmp) { $shareReady = $true; break }
        Write-Diag "[$Label] waiting for WSL share (attempt $($i+1)/3)..."
        Start-Sleep -Seconds 3
    }
    if (-not $shareReady) { throw "[$Label] WSL filesystem not accessible at $wslTmp" }

    # Write script as raw UTF-8 bytes -- no PowerShell encoding pipeline involved
    $tmpWin   = "$wslTmp\dml-vanilla-step.sh"
    $tmpLinux = '/tmp/dml-vanilla-step.sh'
    [System.IO.File]::WriteAllBytes($tmpWin, [System.Text.UTF8Encoding]::new($false).GetBytes($Script.Replace("`r`n", "`n")))

    # Run -- lower EAP so PS 5.1 doesn't abort on native command stderr
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        wsl -d $DmlDistro -u $user -- bash $tmpLinux | Out-Host
        $exit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEap
    }

    $prevEap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    wsl -d $DmlDistro -u $user -- rm -f $tmpLinux | Out-Null
    $ErrorActionPreference = $prevEap

    Write-Diag "[$Label] exit code: $exit"
    return $exit
}

# =============================================================================
# Helpers
# =============================================================================
function ConvertTo-WslWinPath([string]$linuxPath) {
    "\\wsl`$\$DmlDistro" + ($linuxPath -replace '/', '\')
}

function Invoke-YesNo([string]$prompt) {
    while ($true) {
        Write-Host "  $prompt (y/n): " -NoNewline -ForegroundColor White
        $ans = Read-Host
        if ($ans -match '^[Yy]') { return $true }
        if ($ans -match '^[Nn]') { return $false }
        Write-Host '  Please answer y or n.' -ForegroundColor Yellow
    }
}

# Run a short capture command in dml-arch; returns trimmed string output.
# Only for simple checks -- not for long-running streaming operations.
function Invoke-DmlCapture {
    param(
        [string]$BashOneLiner,
        [switch]$AsRoot
    )
    $user = if ($AsRoot) { 'root' } else { $DmlUser }
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $raw = (wsl -d $DmlDistro -u $user -- bash -c $BashOneLiner 2>&1)
    } finally {
        $ErrorActionPreference = $prevEap
    }
    # PS 5.1 wraps native stderr in ErrorRecord objects when EAP=Continue.
    # Discard them -- callers only need stdout.
    $strings = $raw | Where-Object { $_ -is [string] }
    return (($strings -replace "`0", "") -join "`n").Trim()
}

# =============================================================================
# Installer-held WSL keepalive -- Windows tears the WSL VM down ~13s after the
# last wsl.exe session exits. Once the server is running, the installer sits
# at Read-Host prompts with NO session alive, which kills MariaDB mid-first-
# boot (the pre-v1.2.5 failure class; the tray's poller usually masks it, but
# the tray may not be running during an install). Mirror the tray's fix: hold
# a hidden 'wsl sleep' from server start until the installer exits.
# =============================================================================
$Script:KeepaliveProc = $null
function Start-InstallKeepalive {
    if ($Script:KeepaliveProc -and -not $Script:KeepaliveProc.HasExited) { return }
    try {
        $Script:KeepaliveProc = Start-Process -FilePath 'wsl' `
            -ArgumentList '-d', $DmlDistro, '--exec', '/usr/bin/sleep', '7200' `
            -WindowStyle Hidden -PassThru
        Write-Diag "keepalive started (pid $($Script:KeepaliveProc.Id))"
    } catch {
        Write-Diag "keepalive failed to start: $($_.Exception.Message)"
    }
}
function Stop-InstallKeepalive {
    if ($Script:KeepaliveProc -and -not $Script:KeepaliveProc.HasExited) {
        try { $Script:KeepaliveProc.Kill(); Write-Diag 'keepalive stopped' } catch {}
    }
    $Script:KeepaliveProc = $null
}

# =============================================================================
# Step 0 -- Prerequisites
# =============================================================================
function Assert-Prerequisites {
    Write-Step 'Checking Prerequisites'

    # WSL2 is available
    $prevEap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $null = wsl --status 2>&1
    $wslOk = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prevEap
    if (-not $wslOk) {
        Write-Fail 'WSL2 is not available. Please run Install-DML.ps1 first to set up the DML substrate.'
    }

    # dml-arch is registered
    $prevEap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $distroList = (wsl -l --quiet 2>&1) -replace "`0", ""
    $ErrorActionPreference = $prevEap
    if (($distroList -join '') -notmatch 'dml-arch') {
        Write-Fail "'dml-arch' is not installed. Please run Install-DML.ps1 first."
    }
    Write-Ok 'dml-arch found'

    # Docker is running inside dml-arch
    $prevEap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $null = wsl -d $DmlDistro -u $DmlUser -- docker ps 2>&1
    $dockerOk = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prevEap

    if (-not $dockerOk) {
        Write-Warn 'Docker is not running in dml-arch. Attempting to start...'
        $startExit = Invoke-DmlBash -Label 'docker-start' -AsRoot -Script @'
set -euo pipefail
systemctl start docker
timeout 20 bash -c 'until docker ps &>/dev/null; do sleep 2; done'
echo "[ok] Docker started"
'@
        if ($startExit -ne 0) {
            Write-Fail "Docker failed to start in dml-arch.`nTry: wsl --shutdown, then re-run this installer."
        }
    }
    Write-Ok 'Docker running in dml-arch'

    # Disk space -- builder image layers + runtime image + extracted client
    # data + database need ~25 GB inside dml-arch (Docker and /home share
    # the same ext4 VHD).
    $freeStr = Invoke-DmlCapture 'df -BG /home 2>/dev/null | tail -1 | awk ''{print $4}'' | tr -d G'
    if ($freeStr -match '^\d+$') {
        $freeGB = [int]$freeStr
        if ($freeGB -lt 25) {
            Write-Fail "Not enough space in dml-arch: ${freeGB}GB free, need at least 25GB (compile layers + map data + database)."
        }
        Write-Ok "Disk space OK (${freeGB}GB free in dml-arch)"
    } else {
        Write-Warn 'Could not read disk space -- continuing.'
    }

    # Internet
    try {
        $null = Invoke-WebRequest -Uri 'https://github.com' -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
        Write-Ok 'Internet connection OK'
    } catch {
        Write-Fail 'No internet connection detected. Connect and try again.'
    }
}

# =============================================================================
# Legacy layout migration -- normalize any pre-games-era layout to a real
# directory inside games/ so the dml CLI directory scan sees it. Fresh
# installs and already-migrated installs fall straight through. Running
# containers are stopped before any move (bind mounts).
# =============================================================================
function Resolve-LegacyLayout {
    Write-Step 'Checking Install Location'
    $exit = Invoke-DmlBash -Label 'path-migrate' -Script @"
set -euo pipefail
LEGACY='$LegacyServerDir'
TARGET='$ServerDir'
mkdir -p /home/dml/games

_stop_stack() {
    if docker ps --format '{{.Names}}' 2>/dev/null | grep '^vanilla-' >/dev/null; then
        echo "[vanilla] Stopping running vanilla containers before moving files..."
        ( cd "`$1" && docker compose down ) || true
    fi
}

if [ -L "`$TARGET" ]; then
    real=`$(readlink -f "`$TARGET")
    if [ -d "`$real" ] && [ "`$real" != "`$TARGET" ]; then
        _stop_stack "`$real"
        rm "`$TARGET"
        mv "`$real" "`$TARGET"
        echo "[vanilla] Replaced games/ symlink: moved `$real -> `$TARGET"
    else
        rm "`$TARGET"
        echo "[vanilla] Removed dangling games/ symlink"
    fi
elif [ ! -e "`$TARGET" ] && [ -d "`$LEGACY" ]; then
    _stop_stack "`$LEGACY"
    mv "`$LEGACY" "`$TARGET"
    echo "[vanilla] Moved legacy install into games/"
elif [ -d "`$TARGET" ] && [ -d "`$LEGACY" ]; then
    echo "[vanilla] NOTE: separate directories exist at BOTH `$LEGACY and `$TARGET."
    echo "[vanilla]       Using `$TARGET and leaving the other alone -- check it manually."
fi
echo "[vanilla] Install path ready: `$TARGET"
"@
    if ($exit -ne 0) { Write-Fail "Install path migration failed (exit $exit). Check that no vanilla files are open." }
    Write-Ok 'Install location OK'
}

# =============================================================================
# Install state -- what already exists, so re-runs skip the long steps.
# =============================================================================
function Get-InstallState {
    Write-Step 'Detecting Existing Install'

    $imgOk  = (Invoke-DmlCapture ('docker images --format ''{{.Repository}}:{{.Tag}}'' 2>/dev/null | grep -qx ''' + $ServerImage + ''' && echo yes || echo no')) -eq 'yes'

    # Extraction thresholds: interrupted extraction must NOT pass, or it gets
    # skipped forever and mangosd dies with "map files not found". The .sh
    # expects ~2000-4000 maps, ~150 dbc, ~3000+ vmaps, ~2000 mmaps on a real
    # 1.12.1 client. Set well below expected, far above partial output.
    # TODO: calibrate against the first validated live Windows install.
    $nMaps  = Invoke-DmlCapture (('find __SD__/data/maps -type f 2>/dev/null | wc -l').Replace('__SD__', $ServerDir))
    $nDbc   = Invoke-DmlCapture (('find __SD__/data/dbc -type f -iname ''*.dbc'' 2>/dev/null | wc -l').Replace('__SD__', $ServerDir))
    $nVmaps = Invoke-DmlCapture (('ls __SD__/data/vmaps 2>/dev/null | wc -l').Replace('__SD__', $ServerDir))
    $nMmaps = Invoke-DmlCapture (('ls __SD__/data/mmaps 2>/dev/null | wc -l').Replace('__SD__', $ServerDir))
    $dataOk = ($nMaps -match '^\d+$' -and [int]$nMaps -ge 1000) -and
              ($nDbc -match '^\d+$' -and [int]$nDbc -ge 100) -and
              ($nVmaps -match '^\d+$' -and [int]$nVmaps -ge 1000)
    # Mmaps tracked separately: they are REQUIRED (Playerbots iterates all
    # maps for travelnode generation and crashes on missing files -- the .sh
    # learned mmap.enabled=0 does not help), but they can be regenerated from
    # maps+vmaps WITHOUT the client, so an interrupted mmap build should not
    # force a full re-extraction.
    $mmapsOk = ($nMmaps -match '^\d+$' -and [int]$nMmaps -ge 500)
    Write-Diag "data counts: maps=$nMaps dbc=$nDbc vmaps=$nVmaps mmaps=$nMmaps"

    $volOk  = (Invoke-DmlCapture ('docker volume ls --format ''{{.Name}}'' 2>/dev/null | grep -qx ''' + $DbVolume + ''' && echo yes || echo no')) -eq 'yes'

    # Completed-import detection needs an explicit marker: the classic-db
    # Full_DB import creates world tables early, so a table count alone
    # cannot distinguish an interrupted import from a finished one. The
    # marker table realmd.dml_import_complete is created only AFTER content
    # + playerbots SQL import and verification pass. The mangos frm count is
    # a belt-and-braces sanity signal on top.
    $importOk = $false
    if ($volOk) {
        $frmCount = Invoke-DmlCapture ('ls /var/lib/docker/volumes/' + $DbVolume + '/_data/mangos 2>/dev/null | grep -c frm') -AsRoot
        $marker   = Invoke-DmlCapture ('test -f /var/lib/docker/volumes/' + $DbVolume + '/_data/realmd/dml_import_complete.frm && echo yes || echo no') -AsRoot
        if ($frmCount -match '^\d+$' -and [int]$frmCount -ge 100 -and $marker -eq 'yes') { $importOk = $true }
        Write-Diag "mangos frm count: $frmCount; completion marker: $marker"
    }

    # Reuse the DB password from a previous run -- the root password is baked
    # into the data volume on first boot; regenerating it would lock the
    # installer out of its own database. Primary source is the .db_password
    # file (same mechanism as the .sh); fallback is the generated config.
    $existingPw = ''
    if ($volOk) {
        $existingPw = Invoke-DmlCapture ("cat '{0}/.db_password' 2>/dev/null" -f $ServerDir)
        if ($existingPw -notmatch '^[A-Za-z0-9]+$') {
            $existingPw = Invoke-DmlCapture (('grep -m1 ''^LoginDatabaseInfo'' __SD__/etc/mangosd.conf 2>/dev/null | cut -d'';'' -f4').Replace('__SD__', $ServerDir))
            if ($existingPw -notmatch '^[A-Za-z0-9]+$') { $existingPw = '' }
        }
    }

    $state = @{
        ImageOk  = $imgOk
        DataOk   = $dataOk
        MmapsOk  = $mmapsOk
        VolOk    = $volOk
        ImportOk = $importOk
        DbPw     = $existingPw
    }

    if ($imgOk)    { Write-Ok "Compiled server image present ($ServerImage) -- skipping the long compile" } else { Write-Info 'Server image: will compile from source (1-4 hours)' }
    if ($dataOk)   { Write-Ok 'Extracted map data present -- skipping extraction (no client needed)' } else { Write-Info 'Map data: will extract from your client (~30-60 min)' }
    if ($dataOk -and -not $mmapsOk) { Write-Warn 'Pathfinding meshes (mmaps) missing or incomplete -- will regenerate (30-90 min, no client needed)' }
    elseif ($mmapsOk) { Write-Ok 'Pathfinding meshes (mmaps) present' }
    else { Write-Info 'Pathfinding meshes: will generate after extraction (30-90 min)' }
    if ($volOk -and $importOk)      { Write-Ok 'Database volume present with completed import' }
    elseif ($volOk -and -not $importOk) { Write-Warn 'Database volume present but the import looks INCOMPLETE (interrupted run?)' }
    else                            { Write-Info 'Database: will import fresh (4 DBs + world content + bots SQL)' }

    return $state
}

# =============================================================================
# Client location -- only needed when map extraction will run. The client
# lives on the Windows side; docker inside dml-arch reads its Data folder
# read-only through /mnt/. (Reading MPQs over /mnt is slow -- extraction
# takes noticeably longer than from native storage. It works.)
# =============================================================================
function Get-ClientSetup {
    Write-Header
    Write-Step "Locating Your Vanilla WoW Client (1.12.1, build $ClientBuild)"
    Write-Host ''
    Write-Host "  I need the path to your WoW 1.12.1 (build $ClientBuild) client folder" -ForegroundColor White
    Write-Host '  on Windows. It must contain:' -ForegroundColor White
    Write-Host '    - WoW.exe' -ForegroundColor Cyan
    Write-Host '    - a Data folder with .MPQ files inside' -ForegroundColor Cyan
    Write-Host '    - Data\dbc.MPQ specifically (required for extraction)' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '  Example:  C:\Games\VanillaWoW' -ForegroundColor Cyan
    Write-Host ''

    while ($true) {
        Write-Host '  Enter the path to your client folder: ' -NoNewline -ForegroundColor White
        $raw = Read-Host
        $p = $raw.Trim().Trim('"')
        if (-not $p) { continue }
        if ($p -match "'") {
            Write-Warn "Paths containing an apostrophe are not supported -- copy the client to a simpler path."
            continue
        }
        if ($p -notmatch '^[A-Za-z]:\\') {
            Write-Warn 'Please use a full Windows path like C:\Games\VanillaWoW.'
            continue
        }
        if (-not (Test-Path -LiteralPath $p -PathType Container)) {
            Write-Warn "Folder doesn't exist: $p"
            continue
        }

        # WoW.exe -- soft check, plus a build-number peek if it's there
        $wowExe = Join-Path $p 'WoW.exe'
        if (Test-Path -LiteralPath $wowExe -PathType Leaf) {
            $ver = (Get-Item -LiteralPath $wowExe).VersionInfo.FileVersion
            if ($ver -and $ver -notmatch '^1[,.]\s*12') {
                Write-Warn "WoW.exe reports version '$ver' -- expected 1.12.x (build $ClientBuild)."
                Write-Warn 'A wrong client version means extraction fails AFTER the multi-hour compile.'
                if (-not (Invoke-YesNo 'Use this client anyway? (NOT recommended)')) { continue }
            } elseif ($ver) {
                Write-Ok "WoW.exe version: $ver"
            }
        } else {
            Write-Warn 'No WoW.exe in this folder. Extraction only reads Data, so this can'
            Write-Warn 'still work -- but double-check this is really your client folder.'
        }

        # Data folder + MPQ sanity
        $dataDir = Join-Path $p 'Data'
        if (-not (Test-Path -LiteralPath $dataDir -PathType Container)) {
            Write-Warn "No Data folder inside $p -- this doesn't look like a WoW client."
            continue
        }
        $mpqCount = @(Get-ChildItem -LiteralPath $dataDir -Filter '*.mpq' -File -ErrorAction SilentlyContinue).Count
        if ($mpqCount -lt 5) {
            Write-Warn "Only $mpqCount .MPQ files in Data\ -- Vanilla 1.12.1 typically has 10-14."
            Write-Warn 'Fewer usually means a wrong WoW version, an incomplete download,'
            Write-Warn 'or a heavily stripped repack.'
            if (-not (Invoke-YesNo 'Continue anyway? (NOT recommended -- extraction will likely fail)')) { continue }
        }

        # dbc.MPQ -- THE killer check. Without it the extractors cannot produce
        # dbc/maps, and the failure surfaces only AFTER the multi-hour compile.
        # (Windows filesystem is case-insensitive, so one Test-Path covers
        # dbc.MPQ / dbc.mpq / DBC.MPQ.)
        if (-not (Test-Path -LiteralPath (Join-Path $dataDir 'dbc.MPQ') -PathType Leaf)) {
            Write-Warn 'Data\dbc.MPQ is MISSING. This file is required for extraction.'
            Write-Warn 'Likely causes: a heavily stripped repack, a wrong WoW version,'
            Write-Warn 'or an incomplete download. Find a complete 1.12.1 client (~5GB).'
            if (-not (Invoke-YesNo 'Continue anyway? (NOT recommended)')) { continue }
        }

        # Repack signature check (informational only)
        $repackSignals = 0
        if (Test-Path -LiteralPath (Join-Path $p 'realmlist.wtf') -PathType Leaf) { $repackSignals++ }
        if (-not (Test-Path -LiteralPath (Join-Path $dataDir 'enUS')) -and
            -not (Test-Path -LiteralPath (Join-Path $dataDir 'enGB'))) { $repackSignals++ }
        if ($repackSignals -ge 2) {
            Write-Info 'This client looks like a community repack (realmlist at top level, no'
            Write-Info 'locale folder). That is USUALLY fine -- extraction works with stripped'
            Write-Info 'repacks as long as Data\dbc.MPQ exists.'
        }

        Write-Ok "Client found: $p ($mpqCount .MPQ files)"

        # Windows path -> WSL /mnt path (C:\Games\VanillaWoW -> /mnt/c/Games/VanillaWoW)
        $drive = $p.Substring(0, 1).ToLower()
        $rest  = $p.Substring(2).Replace('\', '/').TrimEnd('/')
        $mount = "/mnt/$drive$rest"

        $seen = Invoke-DmlCapture ("[ -d '{0}/Data' ] && echo yes || echo no" -f $mount)
        if ($seen -ne 'yes') {
            Write-Warn "dml-arch cannot see $mount/Data -- is this drive available to WSL?"
            continue
        }
        $Script:ClientMount = $mount
        $Script:ClientPath  = $p
        Write-Ok "Client reachable from dml-arch at $mount"
        break
    }
}

# =============================================================================
# Summary
# =============================================================================
function Show-Summary([hashtable]$state) {
    Write-Header
    Write-Step 'Pre-Build Summary'
    Write-Host ''
    Write-Host '  Server:    Vanilla WoW 1.12.1 (CMaNGOS Classic + Playerbots + AHBot)' -ForegroundColor Cyan
    Write-Host "  Location:  dml-arch -> $ServerDir" -ForegroundColor Cyan
    Write-Host "  Client:    1.12.1 build $ClientBuild required to play" -ForegroundColor Cyan
    Write-Host '  Realm:     127.0.0.1 (this PC; ports 3724 login / 8085 world)' -ForegroundColor Cyan
    Write-Host '  Bots:      Playerbots (AI players) + AHBot (auction house)' -ForegroundColor Cyan
    Write-Host '  Account:   player / player (created automatically)' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '  Planned work:' -ForegroundColor White
    $planCompile = if ($state.ImageOk) { 'skip (already compiled)' } else { 'compile from source (1-4 HOURS)' }
    $planExtract = if ($state.DataOk)  { 'skip (already extracted)' } else { 'extract from client (~30-60 min)' }
    $planMmaps   = if ($state.MmapsOk) { 'skip (already generated)' } else { 'generate pathfinding meshes (30-90 min)' }
    $planDb      = if ($state.VolOk -and $state.ImportOk) { 'skip (already imported)' } else { 'import 4 DBs + world content + bots SQL (~5-10 min)' }
    Write-Host "    Compile:     $planCompile" -ForegroundColor Green
    Write-Host "    Map data:    $planExtract" -ForegroundColor Green
    Write-Host "    Mmaps:       $planMmaps" -ForegroundColor Green
    Write-Host "    Database:    $planDb" -ForegroundColor Green
    Write-Host ''
    if (-not $state.ImageOk) {
        Write-Host '  HONEST TIME COMMITMENT: the compile step alone can take 1-4 hours' -ForegroundColor Yellow
        Write-Host '  depending on your CPU. It is fully hands-off -- a heartbeat line' -ForegroundColor Yellow
        Write-Host '  prints every 5 minutes so you know it is alive. Walk away!' -ForegroundColor Yellow
        Write-Host ''
    }
    Write-Host '  TIP: do not click inside this window while it works -- selecting text' -ForegroundColor Yellow
    Write-Host '  freezes the output until you press a key (Windows QuickEdit).' -ForegroundColor Yellow
    Write-Host ''
    Write-Host '  NOTE: only one WoW server can run at a time -- every DML WoW title' -ForegroundColor Yellow
    Write-Host '  shares login port 3724 (and Vanilla/TBC/WotLK-Playerbots also share' -ForegroundColor Yellow
    Write-Host '  world port 8085). Stop other WoW servers before starting this one.' -ForegroundColor Yellow
    Write-Host ''
    if (-not (Invoke-YesNo 'Start the install?')) {
        Write-Host ''
        Write-Host "  No problem -- run this script again when you're ready." -ForegroundColor White
        exit 0
    }
}

# =============================================================================
# Compile -- a two-stage Dockerfile clones CMaNGOS Classic + Playerbots +
# classic-db and compiles everything inside the builder stage; the runtime
# stage keeps only binaries + bundled SQL (~small image). Docker layer
# caching means an interrupted build resumes past the clone layers.
# =============================================================================
function Invoke-BuildImage([hashtable]$state) {
    Write-Step 'Compiling CMaNGOS Classic + Playerbots (the long step)'
    if ($state.ImageOk) {
        Write-Ok 'Compiled server image already present -- skipping compile.'
        Write-Info 'To force a recompile, remove the image inside dml-arch:'
        Write-Info "  wsl -d dml-arch -u dml -- docker rmi $ServerImage"
        return
    }

    $null = Invoke-DmlCapture ("mkdir -p '{0}' && echo ok" -f $ServerDir)

    # Dockerfile ported as-is from install-wow-vanilla.sh v1.1.2 (two-stage,
    # gcc-12 for the TileAssembler.cpp ICE, SQL preservation into the runtime
    # stage). One delta: make -j$(nproc) -- the Deck pinned -j2 for its RAM.
    $dockerfile = @'
# --------------------------------------------------------------
# Stage 1: Build CMaNGOS Classic + Playerbots from source
# --------------------------------------------------------------
FROM ubuntu:22.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

# Install build dependencies
# Per cmangos/issues/wiki/Installation-Instructions-for-Linux.
# ca-certificates is required because --no-install-recommends strips it.
# gcc-12 is required because gcc-11.4 has an internal compiler error
# on TileAssembler.cpp (CMaNGOS warns about this in its CMake config).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    cmake \
    g++-12 \
    gcc-12 \
    git \
    libssl-dev \
    default-libmysqlclient-dev \
    libace-dev \
    libtbb-dev \
    libboost-all-dev \
    libreadline-dev \
    zlib1g-dev \
    openssl \
    p7zip-full \
    && rm -rf /var/lib/apt/lists/*

ENV CC=/usr/bin/gcc-12
ENV CXX=/usr/bin/g++-12

WORKDIR /src

# Clone CMaNGOS Classic core
RUN git clone --depth 1 https://github.com/cmangos/mangos-classic.git /src/mangos-classic

# Clone Playerbots into modules folder where CMake expects it
# Per cmangos/playerbots README, bots live at: src/modules/Bots
RUN git clone --depth 1 https://github.com/cmangos/playerbots.git \
    /src/mangos-classic/src/modules/Bots

# Clone classic-db (world content database)
RUN git clone --depth 1 https://github.com/cmangos/classic-db.git /src/classic-db

# Clone playerbots repo SEPARATELY at /src/playerbots-fresh to get its
# own sql/ tree (the modules/Bots clone is just C++ source)
RUN git clone --depth 1 https://github.com/cmangos/playerbots.git /src/playerbots-fresh

# Configure with BUILD_PLAYERBOTS=1
WORKDIR /src/mangos-classic
RUN mkdir -p build && cd build && \
    cmake .. \
        -DCMAKE_INSTALL_PREFIX=/opt/mangos \
        -DBUILD_PLAYERBOTS=1 \
        -DBUILD_AHBOT=1 \
        -DBUILD_EXTRACTORS=1 \
        -DBUILD_GAME_SERVER=1 \
        -DBUILD_LOGIN_SERVER=1 \
        -DBUILD_TOOLS=1 \
        -DPCH=1

# Compile (the long step)
RUN cd build && make -j$(nproc) && make install

# -- PRESERVATION: copy SQL files into /opt/mangos/ before stage 2 --
# Without this, the multi-stage Dockerfile would strip them. The
# runtime image needs these for first-boot DB initialization.
RUN mkdir -p /opt/mangos/sql && \
    cp -r /src/mangos-classic/sql/* /opt/mangos/sql/ && \
    mkdir -p /opt/mangos/classic-db && \
    cp -r /src/classic-db/* /opt/mangos/classic-db/ && \
    mkdir -p /opt/mangos/playerbots-sql && \
    cp -r /src/playerbots-fresh/sql/* /opt/mangos/playerbots-sql/

# --------------------------------------------------------------
# Stage 2: Runtime -- minimal image with binaries + SQL bundled
# --------------------------------------------------------------
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

# Install runtime deps + mariadb-client so we can run DB init from
# inside this image without depending on host tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    libssl3 \
    libmysqlclient21 \
    libace-dev \
    libtbb12 \
    libboost-system1.74.0 \
    libboost-filesystem1.74.0 \
    libboost-program-options1.74.0 \
    libreadline8 \
    netcat-openbsd \
    mariadb-client \
    gzip \
    && rm -rf /var/lib/apt/lists/*

# Copy compiled binaries + bundled SQL from builder
COPY --from=builder /opt/mangos /opt/mangos

WORKDIR /opt/mangos/bin

# Default command -- overridden by docker compose for realmd
CMD ["./mangosd"]
'@
    $dfWin = ConvertTo-WslWinPath "$ServerDir/Dockerfile"
    [System.IO.File]::WriteAllBytes($dfWin, [System.Text.UTF8Encoding]::new($false).GetBytes($dockerfile.Replace("`r`n", "`n")))
    Write-Ok 'Dockerfile written (with sql/ preservation baked in)'

    Write-Info 'Starting compile. This is THE long step: 1-4 hours depending on CPU.'
    Write-Info 'Full log: wsl -d dml-arch -u dml -- tail -f ~/vanilla-build.log'
    Write-Info 'A heartbeat line prints every 5 minutes.'

    $exit = Invoke-DmlBash -Label 'compile' -Script @"
set -o pipefail
cd '$ServerDir' || exit 1

( ELAPSED=0; while sleep 300; do ELAPSED=`$((ELAPSED+5)); echo "[vanilla] Still compiling... `${ELAPSED} min elapsed."; done ) &
HB=`$!
trap 'kill `$HB 2>/dev/null || true' EXIT

docker build -t '$ServerImage' . 2>&1 | tee ~/vanilla-build.log
rc=`$?
if [ `$rc -ne 0 ]; then
    echo "[FAIL] Compile failed -- last 30 lines:"
    tail -30 ~/vanilla-build.log
    exit 1
fi
echo "[vanilla] CMaNGOS Classic compiled with Playerbots."
"@
    if ($exit -ne 0) {
        Write-Fail "Compilation failed (exit $exit).`nFull log: wsl -d dml-arch -u dml -- tail -50 ~/vanilla-build.log`nCommon causes: out of disk space, network drop during clone (re-run), out of RAM (close other apps)."
    }
    $state.ImageOk = $true
    Write-Ok "Compiled: $ServerImage"
}

# =============================================================================
# Extract client data -- dbc/maps/vmaps via ExtractResources.sh, then mmaps
# via MoveMapGen. The client's Data folder is mounted READ-ONLY inside the
# work dir, so all outputs land natively in dml-arch and the client is never
# written to (the .sh wrote temp folders into the client and moved them out).
# Exit codes of the extractors are NOT trusted -- output file counts are the
# real success signal (the .sh learned exit 0 with empty folders is a thing).
# =============================================================================
function Invoke-Extract([hashtable]$state) {
    Write-Step 'Extracting Map Data From Your Client'
    if ($state.DataOk) {
        Write-Ok 'Map data already extracted -- skipping.'
        Write-Info 'To force re-extraction, delete the data folder inside dml-arch:'
        Write-Info "  wsl -d dml-arch -u dml -- rm -rf $ServerDir/data"
        Write-Info 'Then re-run this installer.'
    } else {
        if (-not $Script:ClientMount) { Write-Fail 'Internal error: extraction needed but no client path was collected.' }

        Write-Info "Reading client from $($Script:ClientMount)/Data (read-only, through /mnt -- slow, be patient)."
        Write-Info 'This extracts dbc + maps + vmaps (~30-60 min). Mmaps come after.'
        $exit = Invoke-DmlBash -Label 'extract' -Script @"
set -o pipefail
cd '$ServerDir' || exit 1
mkdir -p data

docker run --rm \
    -u "`$(id -u):`$(id -g)" \
    -v '$ServerDir/data':/work \
    -v '$($Script:ClientMount)/Data':/work/Data:ro \
    -w /work --entrypoint /bin/bash '$ServerImage' -c '
        echo "=== Extraction starting at `$(date) ==="
        # "a" = extract all, non-interactive (verified from CMaNGOS source
        # by the Deck installer). CWD must contain Data/; the script locates
        # its sibling binaries via dirname `$0. Outputs (dbc/ maps/ vmaps/
        # Buildings/) land in CWD, which is our native data dir.
        /opt/mangos/bin/tools/ExtractResources.sh a || true
        echo "=== Extraction pass done at `$(date) ==="
    ' 2>&1 | tee ~/vanilla-extract.log
echo "[vanilla] Extraction container finished."
"@
        if ($exit -ne 0) { Write-Fail 'Extraction could not run (docker error). See: wsl -d dml-arch -u dml -- tail -50 ~/vanilla-extract.log' }

        # Validate by counting output files -- the only reliable signal.
        $nMaps  = Invoke-DmlCapture (('find __SD__/data/maps -type f 2>/dev/null | wc -l').Replace('__SD__', $ServerDir))
        $nDbc   = Invoke-DmlCapture (('find __SD__/data/dbc -type f -iname ''*.dbc'' 2>/dev/null | wc -l').Replace('__SD__', $ServerDir))
        $nVmaps = Invoke-DmlCapture (('ls __SD__/data/vmaps 2>/dev/null | wc -l').Replace('__SD__', $ServerDir))
        Write-Info "Extraction outputs: maps=$nMaps (expect ~2000-4000), dbc=$nDbc (expect ~150), vmaps=$nVmaps (expect ~3000+)"
        $countsOk = ($nMaps -match '^\d+$' -and [int]$nMaps -ge 1000) -and
                    ($nDbc -match '^\d+$' -and [int]$nDbc -ge 100) -and
                    ($nVmaps -match '^\d+$' -and [int]$nVmaps -ge 1000)
        if (-not $countsOk) {
            Write-Warn 'Extraction did not produce enough output files!'
            Write-Info 'Likely causes: client missing Data\dbc.MPQ, wrong WoW version, disk full.'
            Write-Info 'Full log: wsl -d dml-arch -u dml -- tail -80 ~/vanilla-extract.log'
            if (-not (Invoke-YesNo 'Continue anyway? (server WILL fail to load maps)')) {
                Write-Fail 'Stopped: no usable map data.'
            }
        } else {
            Write-Ok "Extraction validated: $nMaps maps, $nDbc dbc, $nVmaps vmaps"
            $state.DataOk = $true
            # R2-1: extraction just ran, so any lingering mmaps were built
            # from the PREVIOUS maps/vmaps (possibly a different client).
            # Regenerate so pathfinding matches the new data.
            if ($state.MmapsOk) {
                Write-Info 'Existing mmaps were built from previous map data -- regenerating to match.'
                $state.MmapsOk = $false
            }
        }
    }

    # -- Mmaps -- REQUIRED, not optional. Playerbots iterates all maps for
    # travelnode generation and crashes on missing mmap files; mmap.enabled=0
    # does not prevent that (hard-won .sh lesson). Regenerable from maps +
    # vmaps without the client, so an interrupted run resumes here directly.
    if ($state.MmapsOk) {
        Write-Ok 'Pathfinding meshes already generated -- skipping.'
        return
    }
    Write-Step 'Generating Pathfinding Meshes (30-90 min -- the last big wait)'
    Write-Info 'Progress streams below. Walk away if you want.'
    $exit = Invoke-DmlBash -Label 'mmaps' -Script @"
set -o pipefail
cd '$ServerDir' || exit 1
# Clean any leftover partial mmaps so counts below mean THIS run's output
rm -rf data/mmaps
mkdir -p data/mmaps
T=`$(nproc)
[ "`$T" -gt 8 ] && T=8
echo "[vanilla] MoveMapGen with `$T threads..."
docker run --rm \
    -u "`$(id -u):`$(id -g)" \
    -v '$ServerDir/data':/work \
    -w /work --entrypoint /bin/bash '$ServerImage' -c "
        /opt/mangos/bin/tools/MoveMapGen --silent --threads `$T
        echo ''
        echo '=== Generation done ==='
    " 2>&1 | tee ~/vanilla-mmap.log
echo "[vanilla] Mmap container finished."
"@
    if ($exit -ne 0) { Write-Fail 'Mmap generation could not run (docker error). See: wsl -d dml-arch -u dml -- tail -50 ~/vanilla-mmap.log' }

    $nMmaps = Invoke-DmlCapture (('ls __SD__/data/mmaps 2>/dev/null | wc -l').Replace('__SD__', $ServerDir))
    Write-Info "Mmap files generated: $nMmaps (expect ~2000)"
    if ($nMmaps -match '^\d+$' -and [int]$nMmaps -ge 500) {
        Write-Ok "Pathfinding meshes generated ($nMmaps files)"
        $state.MmapsOk = $true
    } else {
        Write-Warn 'Mmap count is lower than expected. With Playerbots compiled in, the'
        Write-Warn 'server may CRASH on boot when bots hit a missing mmap file.'
        Write-Info 'Full log: wsl -d dml-arch -u dml -- tail -50 ~/vanilla-mmap.log'
        if (-not (Invoke-YesNo 'Continue anyway?')) {
            Write-Fail 'Stopped after incomplete mmap generation. Re-run this installer to retry -- no recompile or re-extraction needed.'
        }
    }
}

# =============================================================================
# DB password -- reuse the one baked into the existing volume, or generate.
# Handles the two broken states: volume without a readable password (can't
# administer it) and volume with a half-finished import (interrupted run).
# Both offer a wipe; both destroy characters, so they ask first.
# =============================================================================
function Resolve-DbPassword([hashtable]$state) {
    Write-Step 'Database Password'

    $needWipe = $false
    if ($state.VolOk -and -not $state.DbPw) {
        Write-Warn 'A vanilla database volume exists, but no saved password was found.'
        Write-Warn 'Without the password, the existing database cannot be used or repaired.'
        if (Invoke-YesNo 'Wipe the database volume and rebuild it fresh? (DESTROYS characters!)') {
            $needWipe = $true
        } else {
            Write-Fail 'Cannot proceed without the database password. (It normally lives in .db_password inside the server folder.)'
        }
    } elseif ($state.VolOk -and -not $state.ImportOk) {
        Write-Warn 'The existing database import looks incomplete (interrupted previous run).'
        if (Invoke-YesNo 'Wipe the database volume and re-import fresh? (DESTROYS characters, if any)') {
            $needWipe = $true
        } else {
            Write-Fail 'Cannot start a server on a half-imported database.'
        }
    }

    if ($needWipe) {
        $exit = Invoke-DmlBash -Label 'db-wipe' -Script @"
set -o pipefail
cd '$ServerDir' 2>/dev/null && docker compose down -v 2>/dev/null
# Container names are pinned -- remove them even if the compose file is gone,
# otherwise a running/stopped container keeps the volume "in use" and the rm
# below fails silently.
docker rm -f vanilla-db vanilla-realmd vanilla-mangosd 2>/dev/null || true
docker volume rm '$DbVolume' 2>/dev/null || true
# Verify -- a swallowed "volume is in use" here would send the fresh import
# against the OLD database with a NEW password (auth failures mid-install).
if docker volume ls --format '{{.Name}}' 2>/dev/null | grep -qx '$DbVolume'; then
    echo "[FAIL] Database volume is still present -- something is holding it."
    exit 1
fi
echo "[vanilla] Database volume wiped."
"@
        if ($exit -ne 0) { Write-Fail 'Failed to wipe the database volume. Stop the server from the DML tray, then re-run this installer.' }
        $state.VolOk = $false
        $state.ImportOk = $false
        $state.DbPw = ''
        Write-Ok 'Database volume wiped -- a fresh import will run.'
    }

    if ($state.DbPw) {
        $Script:DbPassword = $state.DbPw
        Write-Ok 'Reusing existing database password'
    } else {
        # Same shape as the .sh: vanilla + 16 hex chars
        $hex = -join (1..16 | ForEach-Object { '{0:x}' -f (Get-Random -Maximum 16) })
        $Script:DbPassword = "vanilla$hex"
        Write-Ok 'Generated a new database password'
    }
}

# =============================================================================
# Compose + configs + MY_SERVER.txt -- written fresh on every run (they are
# deterministic given the password). Deltas vs the .sh:
#   - restart: "no" everywhere (no ghost boots when dockerd starts; a crashed
#     mangosd stays visibly 'exited' for the ready-wait check)
#   - healthcheck via the image's healthcheck.sh (no password in the
#     container inspect output, unlike the .sh's mariadb -e "SELECT 1")
#   - db publishes NO ports (same as .sh -- kept deliberately)
# =============================================================================
function Write-ComposeAndConfigs {
    Write-Step 'Writing Compose + Configs'

    # Ensure target dirs exist before writing through \\wsl$\
    $null = Invoke-DmlCapture ("mkdir -p '{0}/etc' '{0}/data' && echo ok" -f $ServerDir)

    $compose = @"
services:
  db:
    image: mariadb:11
    container_name: vanilla-db
    restart: "no"
    environment:
      MARIADB_ROOT_PASSWORD: $($Script:DbPassword)
    volumes:
      - db-data:/var/lib/mysql
    networks: [vanilla-net]
    healthcheck:
      test: ["CMD", "healthcheck.sh", "--connect", "--innodb_initialized"]
      interval: 10s
      timeout: 5s
      retries: 40
      start_period: 60s

  realmd:
    image: $ServerImage
    container_name: vanilla-realmd
    restart: "no"
    depends_on:
      db: { condition: service_healthy }
    ports:
      - "3724:3724"
    volumes:
      - ./etc:/opt/mangos/etc
      - ./data:/opt/mangos/data
    networks: [vanilla-net]
    command: ["./realmd"]

  mangosd:
    image: $ServerImage
    container_name: vanilla-mangosd
    restart: "no"
    depends_on:
      db: { condition: service_healthy }
    ports:
      - "8085:8085"
    volumes:
      - ./etc:/opt/mangos/etc
      - ./data:/opt/mangos/data
    networks: [vanilla-net]
    stdin_open: true
    tty: true
    command: ["./mangosd"]

volumes:
  db-data:

networks:
  vanilla-net:
    driver: bridge
"@
    $composeWin = ConvertTo-WslWinPath "$ServerDir/docker-compose.yml"
    [System.IO.File]::WriteAllBytes($composeWin, [System.Text.UTF8Encoding]::new($false).GetBytes($compose.Replace("`r`n", "`n") + "`n"))
    Write-Ok 'docker-compose.yml written (no published 3306; localhost play via 3724/8085)'

    # Persist the password for re-runs (same mechanism as the .sh)
    $pwWin = ConvertTo-WslWinPath "$ServerDir/.db_password"
    [System.IO.File]::WriteAllBytes($pwWin, [System.Text.UTF8Encoding]::new($false).GetBytes($Script:DbPassword + "`n"))
    $null = Invoke-DmlCapture ("chmod 600 '{0}/.db_password' && echo ok" -f $ServerDir)
    Write-Ok 'Database password saved (.db_password)'

    # Pull config templates out of the compiled image, then patch them with
    # sed directly in dml-arch (Arch has sed; the .sh used the container).
    $pw = $Script:DbPassword
    $exit = Invoke-DmlBash -Label 'configs' -Script @"
set -o pipefail
cd '$ServerDir' || exit 1

echo "[vanilla] Extracting config templates from the compiled image..."
docker run --rm \
    -u "`$(id -u):`$(id -g)" \
    -v '$ServerDir/etc':/out \
    --entrypoint /bin/bash '$ServerImage' -c '
        cp /opt/mangos/etc/mangosd.conf.dist /out/mangosd.conf 2>/dev/null || true
        cp /opt/mangos/etc/realmd.conf.dist /out/realmd.conf 2>/dev/null || true
        cp /opt/mangos/etc/ahbot.conf.dist /out/ahbot.conf 2>/dev/null || true
        cp /opt/mangos/etc/*aiplayerbot*.dist /out/ 2>/dev/null || true
        cd /out
        for f in *.dist; do
            [ -f "`$f" ] && mv "`$f" "`${f%.dist}"
        done
        exit 0
    '
if [ ! -f etc/mangosd.conf ] || [ ! -f etc/realmd.conf ]; then
    echo "[FAIL] Config templates missing from the image -- compile incomplete?"
    exit 1
fi

# -- mangosd.conf: 5 patches; missing any silently breaks boot --
sed -i 's|^LoginDatabaseInfo.*|LoginDatabaseInfo = "db;3306;mangos;$pw;realmd"|' etc/mangosd.conf
sed -i 's|^WorldDatabaseInfo.*|WorldDatabaseInfo = "db;3306;mangos;$pw;mangos"|' etc/mangosd.conf
sed -i 's|^CharacterDatabaseInfo.*|CharacterDatabaseInfo = "db;3306;mangos;$pw;characters"|' etc/mangosd.conf
sed -i 's|^LogsDatabaseInfo.*|LogsDatabaseInfo = "db;3306;mangos;$pw;logs"|' etc/mangosd.conf
sed -i 's|^DataDir[[:space:]]*=.*|DataDir = "/opt/mangos/data"|' etc/mangosd.conf

ok=0
grep -q '^LoginDatabaseInfo = "db;3306;mangos;$pw;realmd"' etc/mangosd.conf && ok=`$((ok+1))
grep -q '^WorldDatabaseInfo = "db;3306;mangos;$pw;mangos"' etc/mangosd.conf && ok=`$((ok+1))
grep -q '^CharacterDatabaseInfo = "db;3306;mangos;$pw;characters"' etc/mangosd.conf && ok=`$((ok+1))
grep -q '^LogsDatabaseInfo = "db;3306;mangos;$pw;logs"' etc/mangosd.conf && ok=`$((ok+1))
grep -q '^DataDir = "/opt/mangos/data"' etc/mangosd.conf && ok=`$((ok+1))
if [ "`$ok" -ne 5 ]; then
    echo "[FAIL] mangosd.conf patching incomplete -- only `$ok/5 patches verified."
    exit 1
fi
echo "[vanilla] mangosd.conf patched (5/5 verified)."

# -- realmd.conf --
sed -i 's|^LoginDatabaseInfo.*|LoginDatabaseInfo = "db;3306;mangos;$pw;realmd"|' etc/realmd.conf
grep -q '^LoginDatabaseInfo = "db;3306;mangos;$pw;realmd"' etc/realmd.conf || { echo "[FAIL] realmd.conf patch failed."; exit 1; }
echo "[vanilla] realmd.conf patched."

# -- Playerbots: 1600-2000 random bots (values shipped by the .sh) --
if [ -f etc/aiplayerbot.conf ]; then
    sed -i 's|^AiPlayerbot\.MinRandomBots .*|AiPlayerbot.MinRandomBots = 1600|' etc/aiplayerbot.conf
    sed -i 's|^AiPlayerbot\.MaxRandomBots .*|AiPlayerbot.MaxRandomBots = 2000|' etc/aiplayerbot.conf
    sed -i 's|^AiPlayerbot\.RandomBotAccountCount .*|AiPlayerbot.RandomBotAccountCount = 400|' etc/aiplayerbot.conf
    # R2-3: WARN (not fail) if the patch didn't land -- upstream conf-format
    # drift would otherwise silently leave default bot counts.
    if grep -q '^AiPlayerbot.MinRandomBots = 1600' etc/aiplayerbot.conf; then
        echo "[vanilla] aiplayerbot.conf patched (1600-2000 bots, 400 accounts)."
    else
        echo "[WARN] aiplayerbot.conf patch did not land (upstream format changed?) -- bots will use DEFAULT counts."
    fi
else
    echo "[WARN] aiplayerbot.conf not found in the image -- bots will use defaults."
fi

# -- AHBot: high-volume auction house (values shipped by the .sh) --
if [ -f etc/ahbot.conf ]; then
    sed -i 's|^AuctionHouseBot\.Chance\.Sell .*|AuctionHouseBot.Chance.Sell = 75|' etc/ahbot.conf
    sed -i 's|^AuctionHouseBot\.Loot\.Creature\.Normal .*|AuctionHouseBot.Loot.Creature.Normal    =   90, 100, 30, 40|' etc/ahbot.conf
    sed -i 's|^AuctionHouseBot\.Loot\.Creature\.Rare .*|AuctionHouseBot.Loot.Creature.Rare      =    0,  30,  1,  2|' etc/ahbot.conf
    sed -i 's|^AuctionHouseBot\.Loot\.Creature\.Elite .*|AuctionHouseBot.Loot.Creature.Elite     =   80,  90,  4,  6|' etc/ahbot.conf
    sed -i 's|^AuctionHouseBot\.Loot\.Creature\.RareElite .*|AuctionHouseBot.Loot.Creature.RareElite =    0,  15,  1,  2|' etc/ahbot.conf
    sed -i 's|^AuctionHouseBot\.Loot\.Creature\.WorldBoss .*|AuctionHouseBot.Loot.Creature.WorldBoss =  -10,   2,  1,  1|' etc/ahbot.conf
    sed -i 's|^AuctionHouseBot\.Loot\.Disenchant .*|AuctionHouseBot.Loot.Disenchant =   40,  50,  2,  3|' etc/ahbot.conf
    sed -i 's|^AuctionHouseBot\.Loot\.Fishing .*|AuctionHouseBot.Loot.Fishing    =   12,  18,100,150|' etc/ahbot.conf
    sed -i 's|^AuctionHouseBot\.Loot\.Gameobject .*|AuctionHouseBot.Loot.Gameobject =   50,  60, 30, 45|' etc/ahbot.conf
    sed -i 's|^AuctionHouseBot\.Loot\.Skinning .*|AuctionHouseBot.Loot.Skinning   =   12,  18,200,250|' etc/ahbot.conf
    sed -i 's|^AuctionHouseBot\.Items\.Profession .*|AuctionHouseBot.Items.Profession = 250, 300, 0, 50|' etc/ahbot.conf
    if grep -q '^AuctionHouseBot.Chance.Sell = 75' etc/ahbot.conf; then
        echo "[vanilla] ahbot.conf patched (Chance.Sell=75, high-volume loot)."
    else
        echo "[WARN] ahbot.conf patch did not land (upstream format changed?) -- AHBot will use DEFAULT settings."
    fi
else
    echo "[WARN] ahbot.conf not found in the image -- AHBot will use defaults."
fi
echo "[vanilla] Configs written."
"@
    if ($exit -ne 0) { Write-Fail 'Config generation failed.' }
    Write-Ok 'Configs extracted and patched'

    $info = @"
====================================
  Dad's MMO Lab -- Vanilla WoW
  CMaNGOS Classic + Playerbots + AHBot
  (source compile)
====================================

SERVER:
  Folder:    $ServerDir  (inside dml-arch)
  Realm IP:  127.0.0.1   (Windows localhost via WSL2 forwarding)
  World:     127.0.0.1:8085
  Login:     127.0.0.1:3724
  Account:   player / player   (GM level 3)
  Client:    WoW 1.12.1 (build $ClientBuild)

CLIENT (realmlist.wtf in your WoW client folder on Windows):
  set realmlist 127.0.0.1

START / STOP:
  Right-click the DML Launcher tray icon -> wow-vanilla-server -> Start / Stop
  (Or from inside dml-arch: dml start wow-vanilla-server)

MANUAL COMMANDS (from inside dml-arch):
  Start:   cd $ServerDir && docker compose up -d
  Stop:    cd $ServerDir && docker compose down
  Logs:    docker logs -f vanilla-mangosd
  Console: docker attach vanilla-mangosd   (exit: Ctrl+P then Ctrl+Q -- NEVER Ctrl+C)

CREATE MORE ACCOUNTS (in the console above):
  account create USERNAME PASSWORD
  account set gmlevel USERNAME 3 -1   (optional: makes account GM)

BOT SETTINGS:
  Bots:     $ServerDir/etc/aiplayerbot.conf  (MinRandomBots / MaxRandomBots)
  Auctions: $ServerDir/etc/ahbot.conf
  Restart the server after changes.

PORTS -- ONE WOW SERVER AT A TIME:
  Every DML WoW title shares login port 3724. Vanilla, TBC and the WotLK
  Playerbots server also share world port 8085. Stop one before starting
  another (DML tray icon).
"@
    $infoWin = ConvertTo-WslWinPath "$ServerDir/MY_SERVER.txt"
    [System.IO.File]::WriteAllBytes($infoWin, [System.Text.UTF8Encoding]::new($false).GetBytes($info.Replace("`r`n", "`n") + "`n"))
    Write-Ok 'MY_SERVER.txt written'
}

# =============================================================================
# Database init -- one long-lived WSL session: db up -> health poll -> auth
# preflight -> (fresh only) 8 import phases -> verify -> completion marker ->
# realm row. Exit codes: 0 ok, 1 fail, 3 = imported but verification found
# low counts (PS asks whether to continue).
# =============================================================================
function Initialize-Database([hashtable]$state) {
    Write-Step 'Setting Up the Database'

    # Containers exist from here on -- keep WSL alive across the PS-side gaps
    # and prompts for the rest of the install (released in main finally).
    Start-InstallKeepalive

    $freshFlag = if ($state.VolOk -and $state.ImportOk) { 'no' } else { 'yes' }
    if ($freshFlag -eq 'yes') {
        Write-Info 'Fresh database: 4 schemas + world content + updates + ACID + bots SQL.'
    } else {
        Write-Info 'Existing database found -- import skipped; fix-ups still applied.'
    }
    $pw = $Script:DbPassword

    $exit = Invoke-DmlBash -Label 'db-init' -Script @"
set -o pipefail
cd '$ServerDir' || exit 1

# R1-A: a RUNNING server gets a graceful stop first (compose down = SIGTERM,
# clean world save) -- docker rm -f alone is SIGKILL and would lose the last
# world save if the user re-runs the installer mid-play. The rm -f after it
# sweeps stale/exited leftovers that block 'compose up' with "name already
# in use" (containers only; the volume is untouched).
if docker ps --format '{{.Names}}' 2>/dev/null | grep '^vanilla-' >/dev/null; then
    echo "[vanilla] A vanilla server is running -- stopping it cleanly first..."
    docker compose down 2>/dev/null || true
fi
docker rm -f vanilla-db vanilla-realmd vanilla-mangosd 2>/dev/null || true

echo "[vanilla] Pulling MariaDB image (instant if cached)..."
docker pull mariadb:11 2>&1 | tail -2 || true

echo "[vanilla] Starting database..."
if ! docker compose up -d db; then
    echo "[FAIL] Could not start the database container."
    exit 1
fi

_t0=`$(date +%s)
while true; do
    _h=`$(docker inspect -f '{{.State.Health.Status}}' vanilla-db 2>/dev/null || echo unknown)
    _el=`$(( `$(date +%s) - _t0 ))
    if [ "`$_h" = "healthy" ]; then
        echo "[vanilla] Database is up (`${_el}s)."
        break
    fi
    if [ "`$_el" -ge 3600 ]; then
        echo "[FAIL] Database did not become healthy within 60 minutes."
        exit 1
    fi
    if [ `$(( _el % 30 )) -lt 5 ]; then
        echo "[vanilla] Database still starting... (`${_el}s elapsed, health: `$_h)"
    fi
    sleep 5
done

# Auth preflight -- if the saved password and the volume ever fall out of
# sync, fail HERE with a diagnosis instead of letting the import die with
# cryptic access-denied errors.
if ! docker exec -e MYSQL_PWD='$pw' vanilla-db mariadb -u root -e "SELECT 1;" >/dev/null 2>&1; then
    echo "[FAIL] Database is up but rejected the saved password."
    echo "       .db_password and the database volume are out of sync."
    echo "       Re-run this installer and choose to wipe the volume."
    exit 1
fi

# Compose network name is detected, not guessed (fragile-convention lesson)
NET=`$(docker network ls --format '{{.Name}}' 2>/dev/null | grep 'vanilla-net' | head -1)
if [ -z "`$NET" ]; then
    echo "[FAIL] Couldn't find the vanilla-net Docker network."
    exit 1
fi

if [ "$freshFlag" = "yes" ]; then
    # -- Phase 1: 4 databases + dedicated mangos user with grants --
    echo "[vanilla] Creating databases + mangos user..."
    if ! docker exec -i -e MYSQL_PWD='$pw' vanilla-db mariadb -u root <<SQL
CREATE DATABASE IF NOT EXISTS mangos CHARACTER SET utf8mb4;
CREATE DATABASE IF NOT EXISTS realmd CHARACTER SET utf8mb4;
CREATE DATABASE IF NOT EXISTS characters CHARACTER SET utf8mb4;
CREATE DATABASE IF NOT EXISTS logs CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS 'mangos'@'%' IDENTIFIED BY '$pw';
GRANT ALL PRIVILEGES ON mangos.* TO 'mangos'@'%';
GRANT ALL PRIVILEGES ON realmd.* TO 'mangos'@'%';
GRANT ALL PRIVILEGES ON characters.* TO 'mangos'@'%';
GRANT ALL PRIVILEGES ON logs.* TO 'mangos'@'%';
FLUSH PRIVILEGES;
SQL
    then
        echo "[FAIL] Failed to create databases/user."
        exit 1
    fi

    # -- Phase 2: base schemas (mangos comes via Full_DB, which DROPs and
    # recreates every world table -- importing mangos.sql first is wasted) --
    echo "[vanilla] Importing base schemas (realmd, characters, logs)..."
    for schema in realmd characters logs; do
        if ! docker run --rm --network "`$NET" -e MYSQL_PWD='$pw' '$ServerImage' \
            sh -c "mariadb -h db -u root `$schema < /opt/mangos/sql/base/`$schema.sql" 2>&1 | tail -2; then
            echo "[FAIL] Schema import failed for `$schema."
            exit 1
        fi
        echo "[vanilla]   `$schema schema imported."
    done

    # -- Phase 3: classic-db Full World Database (the big one) --
    echo "[vanilla] Importing world content (creatures/items/quests)..."
    if ! docker run --rm --network "`$NET" -e MYSQL_PWD='$pw' '$ServerImage' \
        sh -c 'gunzip -c /opt/mangos/classic-db/Full_DB/ClassicDB_*.sql.gz | mariadb -h db -u root mangos' 2>&1 | tail -2; then
        echo "[FAIL] Full DB import failed."
        exit 1
    fi
    echo "[vanilla] World content imported."

    # -- Phase 4: classic-db content updates (per-file errors tolerated --
    # some assume a different baseline; that is expected and safe) --
    echo "[vanilla] Applying classic-db content updates..."
    docker run --rm --network "`$NET" -e MYSQL_PWD='$pw' '$ServerImage' sh -c '
        cd /opt/mangos/classic-db/Updates || { echo "[WARN] classic-db Updates directory not found (upstream layout changed?) -- content updates SKIPPED"; exit 0; }
        for sql in `$(ls -v *.sql 2>/dev/null); do
            mariadb -h db -u root mangos < "`$sql" 2>/dev/null
        done
        echo "  content updates done"
    ' 2>&1 | tail -1

    # -- Phase 5: ACID creature AI scripts --
    echo "[vanilla] Importing ACID creature AI scripts..."
    docker run --rm --network "`$NET" -e MYSQL_PWD='$pw' '$ServerImage' \
        sh -c 'mariadb -h db -u root mangos < /opt/mangos/classic-db/ACID/acid_classic.sql' 2>&1 | tail -2 \
        || echo "[WARN] ACID had errors (may be OK)"

    # -- Phase 6: core schema updates (per-file errors tolerated -- most fail
    # harmlessly on db_version tracking drift; the ALTERs that matter land) --
    echo "[vanilla] Applying core schema updates (mangos, realmd, characters, logs)..."
    docker run --rm --network "`$NET" -e MYSQL_PWD='$pw' '$ServerImage' sh -c '
        for db in mangos realmd characters logs; do
            cd /opt/mangos/sql/updates/`$db 2>/dev/null || { echo "  (no updates dir for `$db -- skipped)"; continue; }
            for sql in `$(ls -v *.sql 2>/dev/null); do
                mariadb -h db -u root "`$db" < "`$sql" 2>/dev/null
            done
        done
        echo "  core updates done"
    ' 2>&1 | tail -1
fi

# -- Phase 7 (every run, idempotent): spell_template 6-column fix.
# The z2817/z2818 update files fail via stdin pipe (their first CHANGE
# COLUMN statement errors and aborts the batch), so the ALTER runs directly.
# Without these columns, mangosd refuses to load spell_template. --
echo "[vanilla] Ensuring spell_template column extension (6 columns)..."
docker exec -i -e MYSQL_PWD='$pw' vanilla-db mariadb -u root mangos <<'SQL' 2>&1 | tail -2 || true
ALTER TABLE spell_template
    ADD COLUMN IF NOT EXISTS EffectBonusCoefficient1 FLOAT NOT NULL DEFAULT '0' AFTER RequiredAuraVision,
    ADD COLUMN IF NOT EXISTS EffectBonusCoefficient2 FLOAT NOT NULL DEFAULT '0' AFTER EffectBonusCoefficient1,
    ADD COLUMN IF NOT EXISTS EffectBonusCoefficient3 FLOAT NOT NULL DEFAULT '0' AFTER EffectBonusCoefficient2,
    ADD COLUMN IF NOT EXISTS EffectBonusCoefficientFromAP1 FLOAT NOT NULL DEFAULT '0' AFTER EffectBonusCoefficient3,
    ADD COLUMN IF NOT EXISTS EffectBonusCoefficientFromAP2 FLOAT NOT NULL DEFAULT '0' AFTER EffectBonusCoefficientFromAP1,
    ADD COLUMN IF NOT EXISTS EffectBonusCoefficientFromAP3 FLOAT NOT NULL DEFAULT '0' AFTER EffectBonusCoefficientFromAP2;
SQL
n_cols=`$(docker exec -e MYSQL_PWD='$pw' vanilla-db mariadb -u root mangos -sN -e \
    "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='mangos' AND table_name='spell_template' AND column_name LIKE 'EffectBonusCoefficient%'" 2>/dev/null)
if [ "`$n_cols" != "6" ]; then
    echo "[FAIL] spell_template extension incomplete (`$n_cols/6 columns) -- mangosd will refuse to boot."
    exit 1
fi
echo "[vanilla] spell_template verified (6/6 columns)."

if [ "$freshFlag" = "yes" ]; then
    # -- Phase 8: Playerbots SQL (without these, mangosd dies on boot with
    # "Table 'mangos.ai_playerbot_help_texts' doesn't exist") --
    echo "[vanilla] Importing Playerbots SQL..."
    docker run --rm --network "`$NET" -e MYSQL_PWD='$pw' '$ServerImage' sh -c '
        for sql in /opt/mangos/playerbots-sql/characters/*.sql; do
            mariadb -h db -u root characters < "`$sql" 2>/dev/null
        done
        for sql in /opt/mangos/playerbots-sql/world/*.sql; do
            [ -f "`$sql" ] && mariadb -h db -u root mangos < "`$sql" 2>/dev/null
        done
        for sql in /opt/mangos/playerbots-sql/world/classic/*.sql; do
            mariadb -h db -u root mangos < "`$sql" 2>/dev/null
        done
        echo "  playerbots sql done"
    ' 2>&1 | tail -1

    # -- Phase 9: verify, then set the completion marker --
    echo "[vanilla] Verifying database setup..."
    items=`$(docker exec -e MYSQL_PWD='$pw' vanilla-db mariadb -u root mangos -sN -e "SELECT COUNT(*) FROM item_template" 2>/dev/null)
    bots=`$(docker exec -e MYSQL_PWD='$pw' vanilla-db mariadb -u root mangos -sN -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='mangos' AND table_name LIKE 'ai_playerbot%'" 2>/dev/null)
    echo "[vanilla]   item_template rows: `${items:-unknown} (expect ~17,000+)"
    echo "[vanilla]   ai_playerbot_* tables: `${bots:-unknown} (expect ~10+)"
    if [ "`${items:-0}" -ge 17000 ] 2>/dev/null && [ "`${bots:-0}" -ge 10 ] 2>/dev/null; then
        # Marker created ONLY after everything imported and verified -- this
        # is what ImportOk checks on re-runs.
        docker exec -e MYSQL_PWD='$pw' vanilla-db mariadb -u root realmd -e \
            "CREATE TABLE IF NOT EXISTS dml_import_complete (id INT PRIMARY KEY);" \
            || { echo "[FAIL] Could not write the import-complete marker."; exit 1; }
        echo "[vanilla] Database setup verified -- completion marker set."
    else
        echo "[WARN] Database verification found low counts -- import may be incomplete."
        exit 3
    fi
fi

# -- Realm row (every run): pin address to Windows localhost. The base
# realmd.sql seeds one realm (id=1) at 127.0.0.1 already; the UPDATE heals
# drift and re-pins after any hand-edit. Name set on fresh install only, so
# a renamed realm isn't clobbered on re-runs. --
realms=`$(docker exec -e MYSQL_PWD='$pw' vanilla-db mariadb -u root realmd -sN -e "SELECT COUNT(*) FROM realmlist" 2>/dev/null)
if [ "`$realms" = "0" ]; then
    echo "[WARN] realmlist is EMPTY (base schema normally seeds one realm)."
    echo "       Trying a minimal insert..."
    docker exec -e MYSQL_PWD='$pw' vanilla-db mariadb -u root realmd -e \
        "INSERT INTO realmlist (name, address, port) VALUES ('Vanilla WoW', '127.0.0.1', 8085);" \
        || echo "[WARN] Insert failed -- you may need to add a realmlist row manually."
elif [ "$freshFlag" = "yes" ]; then
    docker exec -e MYSQL_PWD='$pw' vanilla-db mariadb -u root realmd -e \
        "UPDATE realmlist SET name='Vanilla WoW', address='127.0.0.1', port=8085 WHERE id=1;" \
        || { echo "[FAIL] Realm row update failed."; exit 1; }
else
    docker exec -e MYSQL_PWD='$pw' vanilla-db mariadb -u root realmd -e \
        "UPDATE realmlist SET address='127.0.0.1', port=8085 WHERE id=1;" \
        || { echo "[FAIL] Realm row update failed."; exit 1; }
fi
echo "[vanilla] Realm row set -> 127.0.0.1:8085."
echo "[vanilla] Database ready."
"@
    if ($exit -eq 0) {
        Write-Ok 'Database ready'
        return
    }
    if ($exit -eq 3) {
        Write-Warn 'Database imported but verification found low counts.'
        Write-Info 'The server may fail to boot (missing content or bot tables).'
        if (-not (Invoke-YesNo 'Continue and try to start anyway?')) {
            Write-Fail 'Stopped after incomplete database verification. Re-run this installer and choose to wipe the volume for a clean import.'
        }
        return
    }
    Write-Info 'Check the logs:'
    Write-Info '  wsl -d dml-arch -u dml -- docker logs --tail 50 vanilla-db'
    Write-Fail 'Database setup failed. Re-run this installer to try again -- no recompile needed.'
}

# =============================================================================
# First start + ready wait. CMaNGOS prints "Avg Diff:" once mangosd enters
# its main update loop -- that is the real ready signal ("World initialized"
# never appears). With restart: "no", a crashed mangosd stays 'exited', which
# the wait loop checks directly (replaces the .sh's RestartCount detection).
# =============================================================================
function Start-Server {
    Write-Step 'Starting World + Login Servers'
    Write-Info 'First boot creates ~400 bot accounts and warms Playerbots up --'
    Write-Info 'this can take several minutes. Progress prints below.'

    $exit = Invoke-DmlBash -Label 'server-start' -Script @"
set -o pipefail
cd '$ServerDir' || exit 1

# R2-2: another WoW stack holding 3724/8085 would make compose up die with a
# raw bind error at the END of a long install. Name it and stop it cleanly
# (docker stop = SIGTERM) -- one WoW server at a time, as the summary warned.
BLOCKERS=`$(docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null | grep -vE '^vanilla-' | grep -E ':(3724|8085)->' | awk '{print `$1}')
if [ -n "`$BLOCKERS" ]; then
    echo "[vanilla] Another WoW server is holding the login/world ports:"
    echo "`$BLOCKERS" | sed 's/^/             /'
    echo "[vanilla] Stopping it gracefully (only one WoW server runs at a time)..."
    echo "`$BLOCKERS" | xargs -r docker stop
fi

if ! docker compose up -d; then
    echo "[FAIL] docker compose up failed."
    exit 1
fi

TIMEOUT=1800
ELAPSED=0
while [ `$ELAPSED -lt `$TIMEOUT ]; do
    # grep WITHOUT -q: pipefail + grep -q SIGPIPEs docker logs -> false negatives
    if docker logs vanilla-mangosd --since 30m 2>&1 | grep 'Avg Diff:' >/dev/null; then
        echo "[vanilla] World server is online."
        exit 0
    fi
    _st=`$(docker inspect -f '{{.State.Status}}' vanilla-mangosd 2>/dev/null || echo missing)
    if [ "`$_st" = "exited" ]; then
        echo "[FAIL] mangosd exited during startup -- last 25 log lines:"
        docker logs --tail 25 vanilla-mangosd 2>&1
        echo ""
        echo "  Most common causes:"
        echo "    - spell_template columns missing (re-run installer)"
        echo "    - Playerbots SQL not imported (re-run installer, wipe DB)"
        echo "    - mmap files missing (bots crash without them)"
        exit 1
    fi
    if docker logs vanilla-mangosd 2>&1 | grep -E 'map files not found|VMap file.*does not exist' >/dev/null; then
        echo "[FAIL] mangosd cannot find its map data -- last 15 log lines:"
        docker logs --tail 15 vanilla-mangosd 2>&1
        exit 1
    fi
    if [ `$(( ELAPSED % 60 )) -eq 0 ] && [ `$ELAPSED -gt 0 ]; then
        echo "[vanilla] Still starting... (`$(( ELAPSED / 60 )) min -- first boot builds bot accounts, be patient)"
    fi
    sleep 10
    ELAPSED=`$((ELAPSED+10))
done
echo "[WARN] Server did not report ready within 30 minutes."
exit 2
"@
    if ($exit -eq 0) {
        Write-Ok 'World server is ONLINE!'
        return
    }
    if ($exit -eq 2) {
        Write-Warn 'Server is taking longer than expected -- it may still come up.'
        Write-Info 'Watch it: wsl -d dml-arch -u dml -- docker logs -f vanilla-mangosd'
        Write-Info "Look for 'Avg Diff:' to confirm it's running."
        if (-not (Invoke-YesNo 'Continue with the remaining steps anyway?')) {
            Write-Fail 'Stopped while waiting for the server. Re-run this installer to try again -- no recompile needed.'
        }
        return
    }
    Write-Info 'Check the logs:'
    Write-Info '  wsl -d dml-arch -u dml -- docker logs --tail 50 vanilla-mangosd'
    Write-Info '  wsl -d dml-arch -u dml -- docker logs --tail 50 vanilla-db'
    Write-Fail 'Server startup failed. Re-run this installer to try again -- no recompile needed.'
}

# =============================================================================
# Default account -- player/player, GM level 3. Created through the mangosd
# console (SRP6 hashing -- NEVER insert the account row via SQL), but success
# is judged by the realmd.account ROW, not the console pipe's exit code:
# 'timeout N docker attach' can kill the pipe and report failure even though
# the command worked (the F4 false-negative from the tortoise port).
# =============================================================================
function New-DefaultAccount {
    Write-Step 'Creating Default Account (player / player)'
    $pw = $Script:DbPassword

    $exit = Invoke-DmlBash -Label 'account' -Script @"
set -o pipefail

_console() {
    # Exit code deliberately ignored -- docker attach + timeout lies.
    printf 'account create player player\naccount set gmlevel player 3 -1\n' | \
        timeout 20 docker attach vanilla-mangosd >/dev/null 2>&1 || true
}
_account_row() {
    docker exec -e MYSQL_PWD='$pw' vanilla-db mariadb -u root realmd -sN -e \
        "SELECT COUNT(*) FROM account WHERE UPPER(username)='PLAYER'" 2>/dev/null
}
_gm_level() {
    docker exec -e MYSQL_PWD='$pw' vanilla-db mariadb -u root realmd -sN -e \
        "SELECT gmlevel FROM account WHERE UPPER(username)='PLAYER'" 2>/dev/null
}

# Already there (re-run on an existing database)? Verify, don't re-create.
if [ "`$(_account_row)" = "1" ]; then
    gm=`$(_gm_level)
    echo "[vanilla] Account player/player already exists (GM level: `${gm:-unknown})."
    exit 5
fi

sleep 8
_console
ok=no
for i in 1 2 3; do
    sleep 5
    if [ "`$(_account_row)" = "1" ]; then ok=yes; break; fi
done
if [ "`$ok" = "no" ]; then
    echo "[vanilla] First attempt did not land -- retrying console commands..."
    _console
    sleep 10
    [ "`$(_account_row)" = "1" ] && ok=yes
fi
if [ "`$ok" = "no" ]; then
    exit 3
fi

gm=`$(_gm_level)
if [ "`$gm" != "3" ]; then
    echo "[vanilla] Account exists but GM level is '`$gm' -- retrying gmlevel command..."
    printf 'account set gmlevel player 3 -1\n' | timeout 15 docker attach vanilla-mangosd >/dev/null 2>&1 || true
    sleep 5
    gm=`$(_gm_level)
fi
if [ "`$gm" = "3" ]; then
    echo "[vanilla] Account player/player created and verified (GM level 3)."
    exit 0
fi
echo "[vanilla] Account player/player created (GM level: `${gm:-unknown})."
exit 4
"@
    if ($exit -eq 0) {
        Write-Ok 'Account player/player created and VERIFIED in the database (GM level 3)'
        return
    }
    if ($exit -eq 5) {
        Write-Ok 'Account player/player already present -- nothing to do.'
        return
    }
    if ($exit -eq 4) {
        Write-Warn 'Account created, but the GM level could not be verified as 3.'
        Write-Info 'You can log in. To grant GM later, in the server console:'
        Write-Info '  wsl -d dml-arch -u dml -- docker attach vanilla-mangosd'
        Write-Info '  account set gmlevel player 3 -1'
        Write-Info '  (exit safely: Ctrl+P then Ctrl+Q -- NEVER Ctrl+C, that stops the server)'
        return
    }
    Write-Warn 'Auto account-create did not land. Create it manually (30 seconds):'
    Write-Info '  wsl -d dml-arch -u dml'
    Write-Info '  docker attach vanilla-mangosd'
    Write-Info '  account create player player'
    Write-Info '  account set gmlevel player 3 -1'
    Write-Info '  (exit safely: Ctrl+P then Ctrl+Q -- NEVER Ctrl+C, that stops the server)'
}

# =============================================================================
# Completion
# =============================================================================
function Show-Completion {
    Write-Host ''
    Write-Host '  +==================================================+' -ForegroundColor Green
    Write-Host '  |   VANILLA WOW INSTALLED!                         |' -ForegroundColor Green
    Write-Host '  +==================================================+' -ForegroundColor Green
    Write-Host ''
    Write-Host '  You compiled CMaNGOS Classic from source on your own PC.' -ForegroundColor White
    Write-Host "  That's real engineering work. Welcome to the club." -ForegroundColor White
    Write-Host ''
    Write-Host '  Server:   wow-vanilla-server (CMaNGOS Classic + Playerbots + AHBot)' -ForegroundColor Cyan
    Write-Host "  Location: dml-arch -> $ServerDir" -ForegroundColor Cyan
    Write-Host '  Account:  player / player  (GM level 3)' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '  --------------------------------------------------' -ForegroundColor Cyan
    Write-Host '  STEP A -- Point Your Client At the Server' -ForegroundColor White
    Write-Host '  --------------------------------------------------' -ForegroundColor Cyan
    Write-Host ''

    # Point at the exact realmlist.wtf when we know the client folder.
    # Vanilla keeps it at top level; some installs use Data\<locale>\.
    $rlHint = 'your WoW client folder'
    if ($Script:ClientPath) {
        $rlPath = Join-Path $Script:ClientPath 'realmlist.wtf'
        foreach ($locale in 'enUS', 'enGB', 'deDE', 'frFR', 'esES', 'esMX', 'ruRU') {
            $cand = Join-Path $Script:ClientPath "Data\$locale\realmlist.wtf"
            if (Test-Path -LiteralPath $cand -PathType Leaf) { $rlPath = $cand; break }
        }
        $rlHint = $rlPath
    }
    Write-Host "  1. Open: $rlHint" -ForegroundColor White
    Write-Host '  2. Make sure it says exactly: set realmlist 127.0.0.1' -ForegroundColor Green
    Write-Host '  3. Save the file' -ForegroundColor White
    Write-Host ''
    Write-Host '  --------------------------------------------------' -ForegroundColor Cyan
    Write-Host '  STEP B -- Start / Stop Your Server' -ForegroundColor White
    Write-Host '  --------------------------------------------------' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '  Use the DML Launcher in your system tray:' -ForegroundColor White
    Write-Host '  Right-click the DML icon -> wow-vanilla-server -> Start' -ForegroundColor Green
    Write-Host ''
    Write-Host '  IMPORTANT: keep the DML tray running while you play -- it is' -ForegroundColor Yellow
    Write-Host '  what holds WSL awake. Without it, Windows shuts the server' -ForegroundColor Yellow
    Write-Host '  down seconds after the last window into it closes.' -ForegroundColor Yellow
    Write-Host ''
    Write-Host '  REMINDER: every DML WoW title shares login port 3724 (and this' -ForegroundColor Yellow
    Write-Host '  server shares world port 8085 with TBC and WotLK-Playerbots).' -ForegroundColor Yellow
    Write-Host '  Run ONE WoW server at a time.' -ForegroundColor Yellow
    Write-Host ''
    Write-Host '  Bots populate the world over the first 5-10 minutes after each' -ForegroundColor Cyan
    Write-Host '  start -- be patient, Azeroth fills up.' -ForegroundColor Cyan
    Write-Host ''
    Write-Host "  Full info: $ServerDir/MY_SERVER.txt" -ForegroundColor Blue
    Write-Host ''
    Write-Host '  Your server is still running right now!' -ForegroundColor Yellow
    Write-Host ''

    if (Invoke-YesNo 'Stop the server now?') {
        Write-Info 'Stopping server...'
        $prevEap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
        wsl -d $DmlDistro -u $DmlUser -- bash -c "cd '$ServerDir' && docker compose down" | Out-Host
        $stopOk = ($LASTEXITCODE -eq 0)
        $ErrorActionPreference = $prevEap
        if ($stopOk) {
            Write-Ok 'Server stopped. Use the DML Launcher tray icon to start it next time.'
        } else {
            Write-Warn 'Stop command returned an error -- check the tray launcher or run: wsl -d dml-arch -u dml'
        }
    } else {
        Write-Info 'Server left running -- enjoy!'
    }
    Write-Host ''
}

# =============================================================================
# Main
# =============================================================================
Write-Header

Write-Host '  Welcome to the Vanilla WoW installer for Windows!' -ForegroundColor White
Write-Host ''
Write-Host '  This installs a full offline WoW 1.12.1 server -- CMaNGOS Classic' -ForegroundColor White
Write-Host '  compiled from source with Playerbots and AHBot enabled -- running' -ForegroundColor White
Write-Host '  fully in Docker inside dml-arch.' -ForegroundColor White
Write-Host ''
Write-Host '  AI players roam Azeroth, form parties with you, run dungeons,' -ForegroundColor Green
Write-Host "  and populate the auction house. You're never alone in Vanilla." -ForegroundColor Green
Write-Host ''
Write-Host '  HONEST TIME COMMITMENT (first run only):' -ForegroundColor Yellow
Write-Host '    - Compile: 1-4 hours (hands-off, heartbeat every 5 min)' -ForegroundColor Yellow
Write-Host '    - Map extraction: ~30-60 minutes' -ForegroundColor Yellow
Write-Host '    - Pathfinding meshes: 30-90 minutes' -ForegroundColor Yellow
Write-Host '    - Database + first boot: ~15 minutes' -ForegroundColor Yellow
Write-Host '  Future starts take seconds. Re-runs skip completed steps.' -ForegroundColor Yellow
Write-Host ''
Write-Host "  Client required: WoW 1.12.1 (build $ClientBuild) -- you supply it" -ForegroundColor Blue
Write-Host '  Default account: player / player' -ForegroundColor Blue
Write-Host ''

if (-not (Invoke-YesNo 'Ready to build Azeroth?')) {
    Write-Host "  No problem -- run this script again when you're ready."
    exit 0
}

try {
    Assert-Prerequisites
    Resolve-LegacyLayout
    $state = Get-InstallState
    if (-not $state.DataOk) { Get-ClientSetup }
    Show-Summary $state
    Invoke-BuildImage $state
    Invoke-Extract $state
    Resolve-DbPassword $state
    Write-ComposeAndConfigs
    Initialize-Database $state
    Start-Server
    New-DefaultAccount
    Show-Completion
} catch {
    if (-not $Script:FailReported) {
        Write-Host ''
        Write-Host "[FAIL] $($_.Exception.Message)" -ForegroundColor Red
    }
    Write-Host ''
    Write-Host "  Full log: $LogFile" -ForegroundColor Yellow
    exit 1
} finally {
    # Hand WSL-awake duty back to the DML tray (its poller arms its own
    # keepalive within ~10s of seeing a running server).
    Stop-InstallKeepalive
}
