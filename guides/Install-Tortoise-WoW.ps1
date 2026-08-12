#Requires -Version 5.1
<#
.SYNOPSIS
    Dad's MMO Lab — Tortoise WoW Server Installer for Windows  (PERSONAL)
.DESCRIPTION
    Installs the Tortoise WoW server (MaNGOS Zero / Turtle-WoW solo fork,
    compiled from source) inside the dml-arch WSL2 distro.
    Requires Install-DML.ps1 to have been run first.

    Upstream: https://github.com/Penqle/tortoise-wow   (AGPL-3.0)

    Version: 0.3.2-tortoise-win   (DEV — personal use only)

    !! PERSONAL PROJECT. Upstream README: "not to be used for profit."
    !! Do NOT ship this to Ready To Ship or the channel. No paired HOWTO.

    You MUST own the matching client: patch 1.18.1, build 7272
    (the custom Turtle-WoW client). Any other build will not work.

    Changelog:
      0.3.2-tortoise-win (2026-07-07): ready-signal fix (fresh-install test
        finding #2): Turtle source cloned after ~2026-07-01 prints
        "World server is up and running!" at world init -- none of the old
        signatures (World initialized / started up successfully / Ready to
        login) appear, so a perfectly booted server (66s) hit the 30-min
        timeout prompt. New phrase added to the grep; old ones kept for
        older builds. The .sh upstream has the same stale signature.
      0.3.1-tortoise-win (2026-07-07): MoveMapGen exit-code fix (fresh-install
        test finding): Turtle's MoveMapGen returns exit code 1 on SUCCESS when
        --silent is passed (generator.cpp: `return silent ? 1 : finish(...,1)`;
        real error paths return 255/254/253). The bare command under `set -e`
        aborted the extraction container after a perfect mmaps build, showing
        "Extraction reported errors" + a continue-y/n on every successful run.
        Now accepts exit 1 as success; real failures still abort.
      0.3.0-tortoise-win (2026-07-07): Windows port of install-tortoise-wow.sh
        v0.2.0, plus every change made live on the validated Windows install:
        - db service no longer publishes 3306 (the .sh exposed MariaDB root to
          the LAN; containers reach it over the compose network — and the old
          "shared port 3306" collision is gone; only 3724 still collides with
          the WoW Playerbots stack)
        - tw_char.character_inventory_copy created at DB init and ensured on
          every start: Turtle's date-triggered honor maintenance TRUNCATEs it,
          upstream schema never creates it -> assertion abort, mangosd exit
          139 crash loop ("Your database structure is not up to date" +
          HonorMaintenancer in the backtrace)
        - Realm address pinned to 127.0.0.1 (WSL2 localhost forwarding); no
          LAN-IP detection, no Gaming Mode launcher — DML tray runs the server
        - Server lives in /home/dml/games/ (GAMES_DIR) so dml/tray see it;
          migrates the legacy /home/dml/tortoise-wow-server + games/ symlink
          layout from the pre-games era
        - Re-run safe: reuses the DB password from etc/mangosd.conf; skips
          compile / extraction / DB import when their outputs already exist;
          detects a partial DB import (interrupted run) and offers a wipe
        - Configs generated with sed directly in dml-arch (Arch has sed; the
          .sh used a container because SteamOS might lack tools)
        - make -j$(nproc) instead of the Deck's -j4
      (.sh 0.2.0 carried: sql mount not read-only, ALLOW_TURTLE_ADDONS=ON,
       ForcePinAccountRank=10, GameType=0, GM.LoginState=0 + GM.StartLevel=1,
       AutoHonorRestart=0 + AutoRestart.MaxServerUptime=0 — all kept here.)

      R1+R2 audit fixes (2026-07-07, pre-release, same version):
        - DB wipe now removes pinned containers first and VERIFIES the volume
          is gone (a swallowed "in use" sent fresh imports at the old DB)
        - Skip checks hardened against interrupted runs: extraction needs
          calibrated counts (maps>=1000, dbc>=50, vmaps>=100); import needs
          table count AND the character_inventory_copy completion marker
        - Installer holds a hidden 'wsl sleep' keepalive from server start to
          exit (WSL teardown fuse vs. Read-Host prompts, pre-v1.2.5 class)
        - Root-password preflight after db healthy (conf/volume mismatch now
          fails with a diagnosis instead of cryptic import errors)
        - Trailing newlines on generated files; keepalive note on the
          completion screen; soft WoW.exe check at client prompt
#>

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# =============================================================================
# Config
# =============================================================================
$WizardVersion       = '0.3.2-tortoise-win'
$DmlDistro           = 'dml-arch'
$DmlUser             = 'dml'
$ServerDir           = '/home/dml/games/tortoise-wow-server'
$LegacyServerDir     = '/home/dml/tortoise-wow-server'
$Image               = 'dml/tortoise-wow:local'
$SourceRepo          = 'https://github.com/Penqle/tortoise-wow.git'
$ClientBuild         = '7272'
$DbVolume            = 'tortoise-wow-server_dbdata'
$LogFile             = "$env:TEMP\dml-tortoise-install.log"
$Script:FailReported = $false
$Script:ClientMount  = $null
$Script:DoMmaps      = $false
$Script:DbPassword   = $null

# =============================================================================
# Output helpers
# =============================================================================
function Write-Header {
    Clear-Host
    Write-Host ''
    Write-Host '  +==================================================+' -ForegroundColor Green
    Write-Host '  |  DAD''S MMO LAB                                   |' -ForegroundColor White
    Write-Host '  |  Tortoise WoW Installer (Windows) -- PERSONAL    |' -ForegroundColor White
    Write-Host '  |  MaNGOS Zero / Turtle-WoW solo fork              |' -ForegroundColor Blue
    Write-Host "  |  Version $WizardVersion                      |" -ForegroundColor Yellow
    Write-Host '  +==================================================+' -ForegroundColor Green
    Write-Host ''
}

function Write-Step([string]$msg) {
    Write-Host ''
    Write-Host '  --------------------------------------------------' -ForegroundColor Green
    Write-Host "   $msg" -ForegroundColor White
    Write-Host '  --------------------------------------------------' -ForegroundColor Green
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
# WSL bash execution — encoding-safe, EAP-safe (house pattern from
# Install-WoW-WotLK.ps1). Writes the script as raw UTF-8 bytes via \\wsl$\ to
# bypass PowerShell's pipe encoding layer; lowers EAP for the wsl call so
# PS 5.1 does not abort on normal docker/git stderr output. The whole step
# runs in ONE long-lived wsl.exe session — Windows tears the WSL VM down
# ~13s after the last session exits, which kills MySQL mid-operation if you
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

    # Warm up the distro — surfaces startup failures with a clear error
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

    # Write script as raw UTF-8 bytes — no PowerShell encoding pipeline involved
    $tmpWin   = "$wslTmp\dml-tortoise-step.sh"
    $tmpLinux = '/tmp/dml-tortoise-step.sh'
    [System.IO.File]::WriteAllBytes($tmpWin, [System.Text.UTF8Encoding]::new($false).GetBytes($Script.Replace("`r`n", "`n")))

    # Run — lower EAP so PS 5.1 doesn't abort on native command stderr
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
# Only for simple checks — not for long-running streaming operations.
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
    # Discard them — callers only need stdout.
    $strings = $raw | Where-Object { $_ -is [string] }
    return (($strings -replace "`0", "") -join "`n").Trim()
}

# =============================================================================
# Installer-held WSL keepalive — Windows tears the WSL VM down ~13s after the
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
# Step 0 — Prerequisites
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

    # Disk space — source + build + client data need ~15 GB inside dml-arch
    $freeStr = Invoke-DmlCapture 'df -BG /home 2>/dev/null | tail -1 | awk ''{print $4}'' | tr -d G'
    if ($freeStr -match '^\d+$') {
        $freeGB = [int]$freeStr
        if ($freeGB -lt 15) {
            Write-Fail "Not enough space in dml-arch: ${freeGB}GB free, need at least 15GB (source + build + map data)."
        }
        Write-Ok "Disk space OK (${freeGB}GB free in dml-arch)"
    } else {
        Write-Warn 'Could not read disk space — continuing.'
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
# Legacy layout migration — the first (pre-games) Windows install lives at
# /home/dml/tortoise-wow-server with a SYMLINK at games/tortoise-wow-server.
# Normalize to a real directory inside games/ so the dml CLI directory scan
# and Install-DML.ps1's Step 10.5 both-exist guard stop tripping over it.
# Idempotent: fresh installs and already-migrated installs fall straight
# through. Running containers are stopped before any move (bind mounts).
# =============================================================================
function Resolve-LegacyLayout {
    Write-Step 'Checking Install Location'
    $exit = Invoke-DmlBash -Label 'path-migrate' -Script @"
set -euo pipefail
LEGACY='$LegacyServerDir'
TARGET='$ServerDir'
mkdir -p /home/dml/games

_stop_stack() {
    if docker ps --format '{{.Names}}' 2>/dev/null | grep '^tortoise-' >/dev/null; then
        echo "[tortoise] Stopping running tortoise containers before moving files..."
        ( cd "`$1" && docker compose down ) || true
    fi
}

if [ -L "`$TARGET" ]; then
    real=`$(readlink -f "`$TARGET")
    if [ -d "`$real" ] && [ "`$real" != "`$TARGET" ]; then
        _stop_stack "`$real"
        rm "`$TARGET"
        mv "`$real" "`$TARGET"
        echo "[tortoise] Replaced games/ symlink: moved `$real -> `$TARGET"
    else
        rm "`$TARGET"
        echo "[tortoise] Removed dangling games/ symlink"
    fi
elif [ ! -e "`$TARGET" ] && [ -d "`$LEGACY" ]; then
    _stop_stack "`$LEGACY"
    mv "`$LEGACY" "`$TARGET"
    echo "[tortoise] Moved legacy install into games/"
elif [ -d "`$TARGET" ] && [ -d "`$LEGACY" ]; then
    echo "[tortoise] NOTE: separate directories exist at BOTH `$LEGACY and `$TARGET."
    echo "[tortoise]       Using `$TARGET and leaving the other alone -- check it manually."
fi
echo "[tortoise] Install path ready: `$TARGET"
"@
    if ($exit -ne 0) { Write-Fail "Install path migration failed (exit $exit). Check that no tortoise files are open." }
    Write-Ok 'Install location OK'
}

# =============================================================================
# Install state — what already exists, so re-runs skip the long steps.
# =============================================================================
function Get-InstallState {
    Write-Step 'Detecting Existing Install'

    $srcOk  = (Invoke-DmlCapture ("[ -f '{0}/src/CMakeLists.txt' ] && echo yes || echo no" -f $ServerDir)) -eq 'yes'
    $imgOk  = (Invoke-DmlCapture ('docker images --format ''{{.Repository}}:{{.Tag}}'' 2>/dev/null | grep -qx ''' + $Image + ''' && echo yes || echo no')) -eq 'yes'
    $binOk  = (Invoke-DmlCapture ("[ -x '{0}/install/bin/mangosd' ] && [ -x '{0}/install/bin/realmd' ] && [ -f '{0}/install/etc/mangosd.conf.dist' ] && echo yes || echo no" -f $ServerDir)) -eq 'yes'
    # Thresholds calibrated against the validated live install (2805 maps,
    # 159 dbc, 6921 vmaps): an extraction interrupted partway must NOT pass,
    # or it gets skipped forever and mangosd dies with "Correct *.map files
    # not found". Set well below live values, far above partial output.
    $nMaps  = Invoke-DmlCapture (('find __SD__/data/maps -name ''*.map'' 2>/dev/null | wc -l').Replace('__SD__', $ServerDir))
    $nDbc   = Invoke-DmlCapture (('find __SD__/data/dbc -iname ''*.dbc'' 2>/dev/null | wc -l').Replace('__SD__', $ServerDir))
    $nVmaps = Invoke-DmlCapture (('ls __SD__/data/vmaps 2>/dev/null | wc -l').Replace('__SD__', $ServerDir))
    $dataOk = ($nMaps -match '^\d+$' -and [int]$nMaps -ge 1000) -and
              ($nDbc -match '^\d+$' -and [int]$nDbc -ge 50) -and
              ($nVmaps -match '^\d+$' -and [int]$nVmaps -ge 100)
    Write-Diag "data counts: maps=$nMaps dbc=$nDbc vmaps=$nVmaps"
    $volOk  = (Invoke-DmlCapture ('docker volume ls --format ''{{.Name}}'' 2>/dev/null | grep -qx ''' + $DbVolume + ''' && echo yes || echo no')) -eq 'yes'

    # Completed-import detection needs TWO signals. create_databases.sql
    # creates all ~280 tw_world tables BEFORE the 186 base-content files
    # import, so a table count alone cannot distinguish an interrupted import
    # from a finished one. character_inventory_copy is created by our fix-ups
    # only AFTER a successful import -- and it already exists on the
    # pre-installer live box (the 2026-07-06 crash fix), so existing installs
    # are grandfathered in.
    $importOk = $false
    if ($volOk) {
        $frmCount = Invoke-DmlCapture ('ls /var/lib/docker/volumes/' + $DbVolume + '/_data/tw_world 2>/dev/null | grep -c frm') -AsRoot
        $marker   = Invoke-DmlCapture ('test -f /var/lib/docker/volumes/' + $DbVolume + '/_data/tw_char/character_inventory_copy.frm && echo yes || echo no') -AsRoot
        if ($frmCount -match '^\d+$' -and [int]$frmCount -ge 200 -and $marker -eq 'yes') { $importOk = $true }
        Write-Diag "tw_world frm count: $frmCount; completion marker: $marker"
    }

    # Reuse the DB password from an existing config — the root password is
    # baked into the data volume on first boot; regenerating it would lock
    # the installer out of its own database.
    $existingPw = ''
    if ($volOk) {
        $existingPw = Invoke-DmlCapture (('grep -m1 LoginDatabase.Info __SD__/etc/mangosd.conf 2>/dev/null | cut -d'';'' -f4').Replace('__SD__', $ServerDir))
        if ($existingPw -notmatch '^[A-Za-z0-9]+$') { $existingPw = '' }
    }

    $state = @{
        SrcOk    = $srcOk
        ImageOk  = $imgOk
        BinOk    = $binOk
        DataOk   = $dataOk
        VolOk    = $volOk
        ImportOk = $importOk
        DbPw     = $existingPw
    }

    if ($srcOk)    { Write-Ok 'Source tree present' }        else { Write-Info 'Source tree: will clone' }
    if ($imgOk)    { Write-Ok "Docker image present ($Image)" } else { Write-Info 'Docker image: will build (~3-5 min)' }
    if ($binOk)    { Write-Ok 'Compiled binaries present -- skipping compile' } else { Write-Info 'Binaries: will compile (~20-60 min)' }
    if ($dataOk)   { Write-Ok 'Extracted map data present -- skipping extraction (no client needed)' } else { Write-Info 'Map data: will extract from your client' }
    if ($volOk -and $importOk)      { Write-Ok 'Database volume present with completed import' }
    elseif ($volOk -and -not $importOk) { Write-Warn 'Database volume present but the import looks INCOMPLETE (interrupted run?)' }
    else                            { Write-Info 'Database: will import fresh (4 DBs + 186 world files)' }

    return $state
}

# =============================================================================
# Client location — only needed when map extraction will run. The client
# lives on the Windows side; docker inside dml-arch reads it through /mnt/.
# (Reading MPQs over /mnt is slow — extraction takes noticeably longer than
# it would from native storage. It works; it's how the 6/30 install ran.)
# =============================================================================
function Get-ClientSetup {
    Write-Header
    Write-Step "Locating Your Turtle WoW Client (build $ClientBuild)"
    Write-Host ''
    Write-Host "  I need the path to your Turtle-WoW 1.18.1 (build $ClientBuild) folder" -ForegroundColor White
    Write-Host '  on Windows. It must contain WoW.exe and a Data folder of .MPQ files.' -ForegroundColor White
    Write-Host ''
    Write-Host '  Example:  C:\Games\TurtleWoW' -ForegroundColor Cyan
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
            Write-Warn 'Please use a full Windows path like C:\Games\TurtleWoW.'
            continue
        }
        if (-not (Test-Path -LiteralPath $p -PathType Container)) {
            Write-Warn "Folder doesn't exist: $p"
            continue
        }
        if (-not (Test-Path -LiteralPath (Join-Path $p 'WoW.exe') -PathType Leaf)) {
            Write-Warn 'No WoW.exe in this folder. Extraction only reads Data, so this can'
            Write-Warn 'still work -- but double-check this is really your client folder.'
        }
        $dataDir = Join-Path $p 'Data'
        if (-not (Test-Path -LiteralPath $dataDir -PathType Container)) {
            Write-Warn "No Data folder inside $p"
            if (-not (Invoke-YesNo 'Use this folder anyway?')) { continue }
        } else {
            $mpqCount = @(Get-ChildItem -LiteralPath $dataDir -Recurse -Depth 1 -Filter '*.mpq' -File -ErrorAction SilentlyContinue).Count
            if ($mpqCount -lt 5) {
                Write-Warn "Only $mpqCount .MPQ files found in Data -- vanilla-era clients have more."
                if (-not (Invoke-YesNo 'Continue anyway?')) { continue }
            } else {
                Write-Ok "Client found: $p ($mpqCount .MPQ files)"
            }
        }

        # Windows path -> WSL /mnt path (C:\Games\TurtleWoW -> /mnt/c/Games/TurtleWoW)
        $drive = $p.Substring(0, 1).ToLower()
        $rest  = $p.Substring(2).Replace('\', '/').TrimEnd('/')
        $mount = "/mnt/$drive$rest"

        $seen = Invoke-DmlCapture ("[ -d '{0}' ] && echo yes || echo no" -f $mount)
        if ($seen -ne 'yes') {
            Write-Warn "dml-arch cannot see $mount -- is this drive available to WSL?"
            continue
        }
        $Script:ClientMount = $mount
        Write-Ok "Client reachable from dml-arch at $mount"
        break
    }

    Write-Host ''
    Write-Info 'Pathfinding meshes (mmaps) make creatures path properly, but the full'
    Write-Info 'build can take 1-3 hours. The server runs fine without them (creatures'
    Write-Info 'fall back to straight-line movement).'
    $Script:DoMmaps = Invoke-YesNo 'Build mmaps now? (n = skip, extract again later if you want them)'
    if (-not $Script:DoMmaps) { Write-Info 'Skipping mmaps.' }
}

# =============================================================================
# Summary
# =============================================================================
function Show-Summary([hashtable]$state) {
    Write-Header
    Write-Step 'Pre-Build Summary'
    Write-Host ''
    Write-Host '  Server:    Tortoise WoW (MaNGOS Zero / Turtle-WoW solo fork)' -ForegroundColor Cyan
    Write-Host "  Location:  dml-arch -> $ServerDir" -ForegroundColor Cyan
    Write-Host "  Client:    build $ClientBuild required to play" -ForegroundColor Cyan
    Write-Host '  Realm:     127.0.0.1 (this PC; ports 3724 login / 8090 world)' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '  Planned work:' -ForegroundColor White
    $planClone   = if ($state.SrcOk)  { 'skip (already present)' } else { 'clone from GitHub' }
    $planImage   = if ($state.ImageOk) { 'skip (already built)' } else { 'build (~3-5 min)' }
    $planCompile = if ($state.BinOk)  { 'skip (already compiled)' } else { 'compile (~20-60 min)' }
    $planExtract = if ($state.DataOk) { 'skip (already extracted)' } else {
        if ($Script:DoMmaps) { 'extract from client + mmaps (extraction + 1-3 hrs)' } else { 'extract from client (mmaps skipped)' }
    }
    $planDb      = if ($state.VolOk -and $state.ImportOk) { 'skip (already imported)' } else { 'import 4 DBs + 186 world files' }
    Write-Host "    Source:      $planClone" -ForegroundColor Green
    Write-Host "    Build image: $planImage" -ForegroundColor Green
    Write-Host "    Compile:     $planCompile" -ForegroundColor Green
    Write-Host "    Map data:    $planExtract" -ForegroundColor Green
    Write-Host "    Database:    $planDb" -ForegroundColor Green
    Write-Host ''
    Write-Host '  NOTE: only one WoW server can run at a time -- Tortoise and the' -ForegroundColor Yellow
    Write-Host '  Playerbots server both use login port 3724. Stop one before' -ForegroundColor Yellow
    Write-Host '  starting the other (DML tray icon).' -ForegroundColor Yellow
    Write-Host ''
    Write-Host '  PERSONAL BUILD: upstream fork is marked not-for-profit.' -ForegroundColor Yellow
    Write-Host '  Not for the channel, not for Ready To Ship.' -ForegroundColor Yellow
    Write-Host ''
    if (-not (Invoke-YesNo 'Start the install?')) {
        Write-Host ''
        Write-Host "  No problem -- run this script again when you're ready." -ForegroundColor White
        exit 0
    }
}

# =============================================================================
# Source clone
# =============================================================================
function Install-Source([hashtable]$state) {
    Write-Step 'Fetching Source'
    if ($state.SrcOk) {
        Write-Ok 'Source already present -- skipping clone.'
        return
    }
    $exit = Invoke-DmlBash -Label 'clone' -Script @"
set -euo pipefail
mkdir -p '$ServerDir'
cd '$ServerDir'
if [ -d src ] && [ ! -f src/CMakeLists.txt ]; then
    echo "[tortoise] Incomplete source tree from a previous run -- removing..."
    rm -rf src
fi
if [ ! -d src ]; then
    echo "[tortoise] Cloning $SourceRepo ..."
    git clone --depth 1 '$SourceRepo' src
fi
mkdir -p data etc logs
echo "[tortoise] Source ready."
"@
    if ($exit -ne 0) { Write-Fail "Clone failed (exit $exit). Check your internet connection." }
    Write-Ok 'Source cloned'
}

# =============================================================================
# Build/runtime image — one image: build deps double as runtime libs (mangosd
# links against Ubuntu 22.04's libACE/libmysqlclient/libssl, so the server
# runs in this container too).
# =============================================================================
function Install-BuildImage([hashtable]$state) {
    Write-Step 'Build/Runtime Image'
    if ($state.ImageOk) {
        Write-Ok "Image already built ($Image) -- skipping."
        return
    }
    $exit = Invoke-DmlBash -Label 'image-build' -Script @"
set -euo pipefail
cd '$ServerDir'
cat > Dockerfile << 'DOCKERFILE'
FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive TZ=UTC
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake git pkg-config ca-certificates \
    libace-dev default-libmysqlclient-dev libmysqlclient-dev \
    libssl-dev zlib1g-dev libbz2-dev \
    mariadb-client gosu \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/tortoise
DOCKERFILE
echo "[tortoise] Building compile/runtime image (~3-5 min)..."
docker build -t '$Image' . 2>&1 | tail -5
echo "[tortoise] Image ready: $Image"
"@
    if ($exit -ne 0) { Write-Fail "Image build failed (exit $exit)." }
    Write-Ok "Image built: $Image"
}

# =============================================================================
# Compile — mangosd + realmd + extractors from source (~20-60 min).
# Builds as the invoking user so outputs aren't root-owned.
# DEBUG_SYMBOLS=OFF slims mangosd; USE_ANTICHEAT=OFF (pointless solo/offline);
# ALLOW_TURTLE_ADDONS=ON (else client "interface corrupt" crash).
# =============================================================================
function Invoke-Compile([hashtable]$state) {
    Write-Step 'Compiling Server (mangosd + realmd + extractors)'
    if ($state.BinOk) {
        Write-Ok 'Compiled binaries already present -- skipping compile.'
        Write-Info 'To force a recompile, delete the build outputs inside dml-arch:'
        Write-Info "  wsl -d dml-arch -u dml -- rm -rf $ServerDir/install $ServerDir/src/_build"
        return
    }
    Write-Info 'This takes roughly 20-60 minutes. Progress prints as it goes;'
    Write-Info 'a heartbeat line appears every 5 minutes.'
    $exit = Invoke-DmlBash -Label 'compile' -Script @"
set -o pipefail
cd '$ServerDir' || exit 1
mkdir -p src/_build install

( ELAPSED=0; while sleep 300; do ELAPSED=`$((ELAPSED+5)); echo "[tortoise] Still compiling... `${ELAPSED} min elapsed."; done ) &
HB=`$!
trap 'kill `$HB 2>/dev/null || true' EXIT

docker run --rm \
    -u "`$(id -u):`$(id -g)" \
    -v '$ServerDir/src':/src \
    -v '$ServerDir/install':/install \
    -w /src/_build '$Image' bash -c '
        set -e
        cmake .. \
          -DCMAKE_INSTALL_PREFIX=/install \
          -DUSE_EXTRACTORS=ON -DUSE_SCRIPTS=ON -DUSE_STD_MALLOC=ON \
          -DDEBUG_SYMBOLS=OFF -DUSE_ANTICHEAT=OFF -DALLOW_TURTLE_ADDONS=ON
        make -j`$(nproc)
        make install
    ' 2>&1 | tee ~/tortoise-build.log
rc=`$?
if [ `$rc -ne 0 ]; then
    echo "[FAIL] Compile failed -- last 30 lines:"
    tail -30 ~/tortoise-build.log
    exit 1
fi
if [ ! -x install/bin/mangosd ] || [ ! -x install/bin/realmd ]; then
    echo "[FAIL] Binaries missing after build."
    exit 1
fi
echo "[tortoise] Compiled: mangosd, realmd, extractors."
"@
    if ($exit -ne 0) {
        Write-Fail "Compilation failed (exit $exit).`nFull log: wsl -d dml-arch -u dml -- tail -50 ~/tortoise-build.log"
    }
    Write-Ok 'Server compiled'
}

# =============================================================================
# Extract client data — maps/dbc/vmaps (+ optional mmaps) from the client on
# the Windows side, read through /mnt/. Extractors were built FROM this repo
# for build 7272, so DBC formats match. "Can't find area flag..." and
# missing-.m2 warnings are NORMAL for Turtle custom content.
# =============================================================================
function Invoke-Extract([hashtable]$state) {
    Write-Step 'Extracting Map Data From Your Client'
    if ($state.DataOk) {
        Write-Ok 'Map data already extracted -- skipping.'
        Write-Info 'To force re-extraction, delete the data folder inside dml-arch:'
        Write-Info "  wsl -d dml-arch -u dml -- rm -rf $ServerDir/data"
        Write-Info 'Then re-run this installer.'
        return
    }
    if (-not $Script:ClientMount) { Write-Fail 'Internal error: extraction needed but no client path was collected.' }

    $mmapCmd = ':'
    if ($Script:DoMmaps) {
        $mmapCmd = '/install/bin/MoveMapGen --silent --doNotFilterDeepWater --offMeshInput /src/tools/mmap/offmesh.txt --settingsInput /src/tools/mmap/mmapSettings.txt'
    }

    Write-Info "Reading client from $($Script:ClientMount) (through /mnt -- this is slow, be patient)."
    Write-Info '"Can''t find area flag" and missing-.m2 warnings are normal for Turtle content.'
    $exit = Invoke-DmlBash -Label 'extract' -Script @"
set -o pipefail
cd '$ServerDir' || exit 1
mkdir -p data

docker run --rm \
    -u "`$(id -u):`$(id -g)" \
    -v '$($Script:ClientMount)':/client:ro \
    -v '$ServerDir/data':/out \
    -v '$ServerDir/src':/src \
    -v '$ServerDir/install':/install \
    -w /out '$Image' bash -c "
        set -e
        echo '=== mapextractor (maps + dbc) ==='
        /install/bin/mapextractor -i /client -o /out -e 3
        echo '=== vmapextractor (raw building models) ==='
        /install/bin/vmapextractor -d /client/Data/
        echo '=== vmap_assembler (assemble vmaps) ==='
        mkdir -p /out/vmaps
        /install/bin/vmap_assembler /out/Buildings /out/vmaps
        echo '=== mmaps ==='
        $mmapCmd || [ \`$? -eq 1 ]
        echo '=== outputs ==='
        ls -la /out
    " 2>&1 | tee ~/tortoise-extract.log
rc=`$?
if [ `$rc -ne 0 ]; then
    echo "[WARN] Extraction reported errors -- see ~/tortoise-extract.log"
    exit 2
fi
echo "[tortoise] Extraction finished."
"@
    if ($exit -eq 1) { Write-Fail 'Extraction failed hard -- see: wsl -d dml-arch -u dml -- tail -50 ~/tortoise-extract.log' }
    if ($exit -eq 2) {
        Write-Warn 'Extraction reported errors.'
        if (-not (Invoke-YesNo 'Continue anyway? (the server may not boot without maps)')) {
            Write-Fail 'Stopped after extraction errors.'
        }
    }

    # Sanity: maps + dbc are mandatory. Empty output almost always means the
    # client is not build 7272.
    $nMaps = Invoke-DmlCapture (('find __SD__/data/maps -name ''*.map'' 2>/dev/null | wc -l').Replace('__SD__', $ServerDir))
    $nDbc  = Invoke-DmlCapture (('find __SD__/data/dbc -iname ''*.dbc'' 2>/dev/null | wc -l').Replace('__SD__', $ServerDir))
    if (($nMaps -notmatch '^\d+$') -or ([int]$nMaps -lt 1) -or ($nDbc -notmatch '^\d+$') -or ([int]$nDbc -lt 1)) {
        Write-Warn "No maps ($nMaps) or dbc ($nDbc) produced -- extraction did not work."
        Write-Info "This almost always means the client isn't build $ClientBuild."
        if (-not (Invoke-YesNo 'Continue and try to start anyway?')) { Write-Fail 'Stopped: no usable map data.' }
    } else {
        Write-Ok "Extracted: $nMaps maps, $nDbc dbc files"
    }
}

# =============================================================================
# DB password — reuse the one baked into the existing volume, or generate.
# Handles the two broken states: volume without a readable password (can't
# administer it) and volume with a half-finished import (interrupted run).
# Both offer a wipe; both destroy characters, so they ask first.
# =============================================================================
function Resolve-DbPassword([hashtable]$state) {
    Write-Step 'Database Password'

    $needWipe = $false
    if ($state.VolOk -and -not $state.DbPw) {
        Write-Warn 'A tortoise database volume exists, but no config with its password was found.'
        Write-Warn 'Without the password, the existing database cannot be used or repaired.'
        if (Invoke-YesNo 'Wipe the database volume and rebuild it fresh? (DESTROYS characters!)') {
            $needWipe = $true
        } else {
            Write-Fail 'Cannot proceed without the database password. (It normally lives in etc/mangosd.conf.)'
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
docker rm -f tortoise-db tortoise-realmd tortoise-mangosd 2>/dev/null || true
docker volume rm '$DbVolume' 2>/dev/null || true
# Verify -- a swallowed "volume is in use" here would send the fresh import
# against the OLD database with a NEW password (auth failures mid-install).
if docker volume ls --format '{{.Name}}' 2>/dev/null | grep -qx '$DbVolume'; then
    echo "[FAIL] Database volume is still present -- something is holding it."
    exit 1
fi
echo "[tortoise] Database volume wiped."
"@
        if ($exit -ne 0) { Write-Fail 'Failed to wipe the database volume. Stop the server from the DML tray, then re-run this installer.' }
        $state.VolOk = $false
        $state.ImportOk = $false
        $state.DbPw = ''
        Write-Ok 'Database volume wiped -- a fresh import will run.'
    }

    if ($state.DbPw) {
        $Script:DbPassword = $state.DbPw
        Write-Ok 'Reusing existing database password from etc/mangosd.conf'
    } else {
        $Script:DbPassword = 'tortoise' + (Get-Random -Minimum 10000 -Maximum 99999)
        Write-Ok 'Generated a new database password'
    }
}

# =============================================================================
# Compose + configs + MY_SERVER.txt — written fresh on every run (they are
# deterministic given the password). Differences vs the .sh, both from the
# validated live install:
#   - db publishes NO ports (containers use the compose network; publishing
#     3306 exposed MariaDB root to the LAN and collided with other stacks)
#   - restart: "no" everywhere (validated live; also means no ghost boots
#     when dockerd starts, and a crashed mangosd stays visibly 'exited')
# =============================================================================
function Write-ComposeAndConfigs {
    Write-Step 'Writing Compose + Configs'

    # Ensure target dirs exist before writing through \\wsl$\
    $null = Invoke-DmlCapture ("mkdir -p '{0}/etc' '{0}/logs' '{0}/data' && echo ok" -f $ServerDir)

    $compose = @"
services:
  db:
    image: mariadb:10.6
    container_name: tortoise-db
    restart: "no"
    environment:
      MARIADB_ROOT_PASSWORD: $($Script:DbPassword)
      MARIADB_USER: mangos
      MARIADB_PASSWORD: $($Script:DbPassword)
    volumes:
      - dbdata:/var/lib/mysql
    networks: [tortoise-net]
    healthcheck:
      test: ["CMD", "healthcheck.sh", "--connect", "--innodb_initialized"]
      interval: 10s
      timeout: 5s
      retries: 40
      start_period: 60s

  realmd:
    image: $Image
    container_name: tortoise-realmd
    restart: "no"
    depends_on:
      db: { condition: service_healthy }
    ports:
      - "3724:3724"
    volumes:
      - ./install:/opt/tortoise
      - ./etc/realmd.conf:/opt/tortoise/bin/realmd.conf:ro
      - ./logs:/opt/tortoise/logs
    working_dir: /opt/tortoise/bin
    command: ["./realmd", "-c", "/opt/tortoise/bin/realmd.conf"]
    networks: [tortoise-net]

  mangosd:
    image: $Image
    container_name: tortoise-mangosd
    restart: "no"
    depends_on:
      db: { condition: service_healthy }
    ports:
      - "8090:8090"
    volumes:
      - ./install:/opt/tortoise
      - ./etc/mangosd.conf:/opt/tortoise/bin/mangosd.conf:ro
      - ./data:/opt/tortoise/data
      # NOT :ro -- the DB auto-updater creates sql/unused/ on every boot; a
      # read-only mount crashes mangosd ("cannot create directory ... Read-only").
      - ./src/sql:/opt/tortoise/sql
      - ./logs:/opt/tortoise/logs
    working_dir: /opt/tortoise/bin
    command: ["./mangosd", "-c", "/opt/tortoise/bin/mangosd.conf"]
    stdin_open: true
    tty: true
    networks: [tortoise-net]

volumes:
  dbdata:

networks:
  tortoise-net:
    driver: bridge
"@
    $composeWin = ConvertTo-WslWinPath "$ServerDir/docker-compose.yml"
    [System.IO.File]::WriteAllBytes($composeWin, [System.Text.UTF8Encoding]::new($false).GetBytes($compose.Replace("`r`n", "`n") + "`n"))
    Write-Ok 'docker-compose.yml written (no published 3306; localhost play via 3724/8090)'

    # Configs from the compiled .dist templates. sed runs directly in dml-arch
    # (Arch has sed; the .sh used a container because SteamOS might not).
    # NOTE: the shipped default points CharacterDatabase.Info at "tw_chars"
    # (plural) but the DB created is "tw_char" (singular) -- fixed here.
    $pw = $Script:DbPassword
    $exit = Invoke-DmlBash -Label 'configs' -Script @"
set -euo pipefail
cd '$ServerDir'
if [ ! -f install/etc/mangosd.conf.dist ] || [ ! -f install/etc/realmd.conf.dist ]; then
    echo "[FAIL] .dist config templates missing -- compile step incomplete?"
    exit 1
fi
sed -E \
  -e 's#^LoginDatabase\.Info.*#LoginDatabase.Info = "db;3306;mangos;$pw;tw_logon"#' \
  -e 's#^WorldDatabase\.Info.*#WorldDatabase.Info = "db;3306;mangos;$pw;tw_world"#' \
  -e 's#^CharacterDatabase\.Info.*#CharacterDatabase.Info = "db;3306;mangos;$pw;tw_char"#' \
  -e 's#^LogsDatabase\.Info.*#LogsDatabase.Info = "db;3306;mangos;$pw;tw_logs"#' \
  -e 's#^Database\.AutoUpdate\.Path.*#Database.AutoUpdate.Path = "/opt/tortoise/sql/"#' \
  -e 's#^DataDir.*#DataDir = "/opt/tortoise/data"#' \
  -e 's#^AutoHonorRestart.*#AutoHonorRestart = 0#' \
  -e 's#^AutoRestart\.MaxServerUptime.*#AutoRestart.MaxServerUptime = 0#' \
  -e 's#^GameType.*#GameType = 0#' \
  -e 's#^GM\.LoginState.*#GM.LoginState = 0#' \
  -e 's#^GM\.StartLevel.*#GM.StartLevel = 1#' \
  install/etc/mangosd.conf.dist > etc/mangosd.conf
sed -E \
  -e 's#^LoginDatabaseInfo.*#LoginDatabaseInfo = "db;3306;mangos;$pw;tw_logon"#' \
  -e 's#^ForcePinAccountRank.*#ForcePinAccountRank = 10#' \
  install/etc/realmd.conf.dist > etc/realmd.conf
echo "[tortoise] Configs written (DB names + Turtle quirks fixed)."
"@
    if ($exit -ne 0) { Write-Fail 'Config generation failed.' }
    Write-Ok 'mangosd.conf + realmd.conf written'

    $info = @"
====================================
  Dad's MMO Lab -- Tortoise WoW  (personal)
  MaNGOS Zero / Turtle-WoW solo fork
====================================

SERVER:
  Folder:    $ServerDir
  Realm IP:  127.0.0.1   (Windows localhost via WSL2 forwarding)
  World:     127.0.0.1:8090
  Login:     127.0.0.1:3724
  Account:   player / player   (GM level 3)
  Client:    build $ClientBuild

CLIENT (realmlist.wtf in your WoW client folder on Windows):
  set realmlist 127.0.0.1

START / STOP:
  Right-click the DML Launcher tray icon -> tortoise-wow-server -> Start / Stop
  (Or from inside dml-arch: dml start tortoise-wow-server)

MANUAL COMMANDS (from inside dml-arch):
  Start:   cd $ServerDir && docker compose up -d
  Stop:    cd $ServerDir && docker compose down
  Logs:    docker logs -f tortoise-mangosd
  Console: docker attach tortoise-mangosd   (exit: Ctrl+P then Ctrl+Q)

NOTE: only one WoW server runs at a time (shared login port 3724).
      Stop the Playerbots server before starting Tortoise WoW.
"@
    $infoWin = ConvertTo-WslWinPath "$ServerDir/MY_SERVER.txt"
    [System.IO.File]::WriteAllBytes($infoWin, [System.Text.UTF8Encoding]::new($false).GetBytes($info.Replace("`r`n", "`n") + "`n"))
    Write-Ok 'MY_SERVER.txt written'
}

# =============================================================================
# Database init + server start — one long-lived WSL session for the whole
# sequence (db up -> health poll -> import -> fix-ups -> full start -> ready
# wait), so WSL idle teardown can't kill MariaDB between steps.
# =============================================================================
function Start-ServerAndDatabase([hashtable]$state) {
    Write-Step 'Starting Database + Server'

    # From here on containers are running -- keep WSL alive across the PS-side
    # gaps and prompts for the rest of the install (released in finally).
    Start-InstallKeepalive

    $freshFlag = if ($state.VolOk -and $state.ImportOk) { 'no' } else { 'yes' }
    if ($freshFlag -eq 'yes') {
        Write-Info 'Fresh database: schema + 186 base world files will be imported.'
    } else {
        Write-Info 'Existing database found -- import skipped; fix-ups still applied.'
    }
    $pw = $Script:DbPassword

    $exit = Invoke-DmlBash -Label 'db-start' -Script @"
set -o pipefail
cd '$ServerDir' || exit 1

echo "[tortoise] Starting database..."
if ! docker compose up -d db; then
    echo "[FAIL] Could not start the database container."
    exit 1
fi

_t0=`$(date +%s)
while true; do
    _h=`$(docker inspect -f '{{.State.Health.Status}}' tortoise-db 2>/dev/null || echo unknown)
    _el=`$(( `$(date +%s) - _t0 ))
    if [ "`$_h" = "healthy" ]; then
        echo "[tortoise] Database is up (`${_el}s)."
        break
    fi
    if [ "`$_el" -ge 3600 ]; then
        echo "[FAIL] Database did not become healthy within 60 minutes."
        exit 1
    fi
    if [ `$(( _el % 30 )) -lt 5 ]; then
        echo "[tortoise] Database still starting... (`${_el}s elapsed, health: `$_h)"
    fi
    sleep 5
done

# Auth preflight -- if etc/mangosd.conf and the volume ever fall out of sync
# (hand-edit, restored file), fail HERE with a diagnosis instead of letting
# the import/fix-ups die with cryptic access-denied errors.
if ! docker exec tortoise-db mariadb -uroot -p'$pw' -e "SELECT 1;" >/dev/null 2>&1; then
    echo "[FAIL] Database is up but rejected the password from etc/mangosd.conf."
    echo "       The config and the database volume are out of sync. Restore the"
    echo "       matching etc/mangosd.conf, or re-run and choose to wipe the volume."
    exit 1
fi

if [ "$freshFlag" = "yes" ]; then
    echo "[tortoise] Importing schema (4 databases, 415 tables)..."
    if ! docker exec -i tortoise-db mariadb -uroot -p'$pw' < src/sql/create_databases.sql; then
        echo "[FAIL] Schema import failed."
        exit 1
    fi
    for d in tw_world tw_char tw_logon tw_logs; do
        if ! docker exec tortoise-db mariadb -uroot -p'$pw' -e "GRANT ALL ON `$d.* TO 'mangos'@'%'; FLUSH PRIVILEGES;"; then
            echo "[FAIL] Grant failed for `$d."
            exit 1
        fi
    done
    echo "[tortoise] Importing 186 base world files (several minutes)..."
    n=0
    for f in src/sql/base/*.sql; do
        if ! docker exec -i tortoise-db mariadb -uroot -p'$pw' tw_world < "`$f"; then
            echo "[FAIL] Import failed on `$f"
            exit 1
        fi
        n=`$((n+1))
        if [ `$(( n % 25 )) -eq 0 ]; then
            echo "[tortoise]   `$n/186 imported..."
        fi
    done
    echo "[tortoise] Base import complete (`$n files). mangosd applies updates on first boot."
fi

# --- Idempotent fix-ups, applied on EVERY start ---
# 1) Turtle's date-triggered honor maintenance TRUNCATEs character_inventory_copy;
#    upstream schema never creates it -> assertion abort (mangosd exit 139 loop).
if ! docker exec tortoise-db mariadb -uroot -p'$pw' tw_char -e "CREATE TABLE IF NOT EXISTS character_inventory_copy LIKE character_inventory;"; then
    echo "[FAIL] Could not ensure tw_char.character_inventory_copy."
    exit 1
fi
echo "[tortoise] Honor-maintenance guard table ensured (character_inventory_copy)."

# 2) Realm row pinned to Windows localhost (WSL2 forwards 127.0.0.1 into the distro).
if [ "$freshFlag" = "yes" ]; then
    docker exec tortoise-db mariadb -uroot -p'$pw' tw_logon -e "REPLACE INTO realmlist (id,name,address,port,icon,realmflags,timezone,allowedSecurityLevel,population,realmbuilds) VALUES (1,'Tortoise WoW','127.0.0.1',8090,0,0,0,0,0,'$ClientBuild');" || { echo "[FAIL] Realm row seed failed."; exit 1; }
else
    docker exec tortoise-db mariadb -uroot -p'$pw' tw_logon -e "UPDATE realmlist SET address='127.0.0.1', port=8090 WHERE id=1;" || { echo "[FAIL] Realm row update failed."; exit 1; }
fi
echo "[tortoise] Realm row set -> 127.0.0.1:8090 (build $ClientBuild)."

echo "[tortoise] Starting realmd + mangosd (first boot applies database updates)..."
if ! docker compose up -d; then
    echo "[FAIL] docker compose up failed."
    exit 1
fi

TIMEOUT=1800
ELAPSED=0
while [ `$ELAPSED -lt `$TIMEOUT ]; do
    # grep WITHOUT -q: pipefail + grep -q SIGPIPEs docker logs -> false negatives
    if docker logs tortoise-mangosd 2>&1 | grep -E 'World server is up and running|World initialized|started up successfully|Ready to login' >/dev/null; then
        echo "[tortoise] World server is online."
        exit 0
    fi
    _st=`$(docker inspect -f '{{.State.Status}}' tortoise-mangosd 2>/dev/null || echo missing)
    if [ "`$_st" = "exited" ]; then
        echo "[FAIL] mangosd exited during startup -- last 20 log lines:"
        docker logs --tail 20 tortoise-mangosd 2>&1
        exit 1
    fi
    if docker logs tortoise-mangosd 2>&1 | grep -E 'Correct \*.map files not found|Could not open|Database .* not found' >/dev/null; then
        echo "[FAIL] mangosd failed to start cleanly -- last 15 log lines:"
        docker logs --tail 15 tortoise-mangosd 2>&1
        exit 1
    fi
    if [ `$(( ELAPSED % 60 )) -eq 0 ] && [ `$ELAPSED -gt 0 ]; then
        echo "[tortoise] Still starting... (`$(( ELAPSED / 60 )) min -- first boot applies DB updates, be patient)"
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
        Write-Info 'Watch it: wsl -d dml-arch -u dml -- docker logs -f tortoise-mangosd'
        if (-not (Invoke-YesNo 'Continue with the remaining steps anyway?')) {
            Write-Fail 'Stopped while waiting for the server. Re-run this installer to try again -- no recompile needed.'
        }
        return
    }
    Write-Info 'Check the logs:'
    Write-Info '  wsl -d dml-arch -u dml -- docker logs --tail 50 tortoise-mangosd'
    Write-Info '  wsl -d dml-arch -u dml -- docker logs --tail 50 tortoise-db'
    Write-Fail 'Server startup failed. Re-run this installer to try again -- no recompile needed.'
}

# =============================================================================
# Default account — player/player, GM level 3 (solo-GM convenience).
# Only attempted on a fresh database; existing DBs already have it.
# =============================================================================
function New-DefaultAccount([hashtable]$state) {
    if ($state.VolOk -and $state.ImportOk) {
        Write-Info 'Existing database -- skipping default account creation (player/player already exists).'
        return
    }
    Write-Step 'Creating Default Account (player / player)'
    $exit = Invoke-DmlBash -Label 'account' -Script @"
sleep 8
if printf 'account create player player\naccount set gmlevel player 3 -1\n' | timeout 20 docker attach tortoise-mangosd >/dev/null 2>&1; then
    echo "[tortoise] Account player/player created (GM level 3)."
    exit 0
fi
exit 3
"@
    if ($exit -eq 0) {
        Write-Ok 'Account player/player created (GM level 3)'
    } else {
        Write-Warn 'Auto account-create did not confirm. Create it manually:'
        Write-Info '  wsl -d dml-arch -u dml'
        Write-Info '  docker attach tortoise-mangosd'
        Write-Info '  account create player player'
        Write-Info '  account set gmlevel player 3 -1'
        Write-Info '  (exit safely: Ctrl+P then Ctrl+Q -- NEVER Ctrl+C, that stops the server)'
    }
}

# =============================================================================
# Completion
# =============================================================================
function Show-Completion {
    Write-Host ''
    Write-Host '  +==================================================+' -ForegroundColor Green
    Write-Host '  |   TORTOISE WOW INSTALLED!                        |' -ForegroundColor Green
    Write-Host '  +==================================================+' -ForegroundColor Green
    Write-Host ''
    Write-Host '  Server:   tortoise-wow-server (MaNGOS Zero / Turtle solo fork)' -ForegroundColor Cyan
    Write-Host "  Location: dml-arch -> $ServerDir" -ForegroundColor Cyan
    Write-Host '  Account:  player / player  (GM level 3)' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '  --------------------------------------------------' -ForegroundColor Cyan
    Write-Host '  STEP A -- Point Your Client At the Server' -ForegroundColor White
    Write-Host '  --------------------------------------------------' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '  1. Open your Turtle WoW client folder (build 7272)' -ForegroundColor White
    Write-Host '  2. Find and open: realmlist.wtf' -ForegroundColor White
    Write-Host '  3. Make sure it says: set realmlist 127.0.0.1' -ForegroundColor Green
    Write-Host '  4. Save the file' -ForegroundColor White
    Write-Host ''
    Write-Host '  --------------------------------------------------' -ForegroundColor Cyan
    Write-Host '  STEP B -- Start / Stop Your Server' -ForegroundColor White
    Write-Host '  --------------------------------------------------' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '  Use the DML Launcher in your system tray:' -ForegroundColor White
    Write-Host '  Right-click the DML icon -> tortoise-wow-server -> Start' -ForegroundColor Green
    Write-Host ''
    Write-Host '  IMPORTANT: keep the DML tray running while you play -- it is' -ForegroundColor Yellow
    Write-Host '  what holds WSL awake. Without it, Windows shuts the server' -ForegroundColor Yellow
    Write-Host '  down seconds after the last window into it closes.' -ForegroundColor Yellow
    Write-Host ''
    Write-Host '  REMINDER: Tortoise and the Playerbots server share login' -ForegroundColor Yellow
    Write-Host '  port 3724 -- run one at a time.' -ForegroundColor Yellow
    Write-Host ''
    Write-Host "  Full info: $ServerDir/MY_SERVER.txt" -ForegroundColor Blue
    Write-Host '  (Personal build -- not for the channel.)' -ForegroundColor Yellow
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

Write-Host '  Welcome to the Tortoise WoW installer for Windows!' -ForegroundColor White
Write-Host '  A personal, offline Turtle-WoW solo-fork server' -ForegroundColor White
Write-Host '  (autoscale dungeons/raids, leech system, extra talents),' -ForegroundColor White
Write-Host '  compiled from source and run fully in Docker inside dml-arch.' -ForegroundColor White
Write-Host ''
Write-Host "  Client required: the custom Turtle-WoW client, build $ClientBuild" -ForegroundColor Blue
Write-Host '  Default account: player / player' -ForegroundColor Blue
Write-Host ''
Write-Host '  PERSONAL BUILD: the upstream fork is marked not-for-profit.' -ForegroundColor Yellow
Write-Host '  This installer stays off the channel and out of Ready To Ship.' -ForegroundColor Yellow
Write-Host ''

if (-not (Invoke-YesNo 'Ready to build your tortoise?')) {
    Write-Host "  No problem -- run this script again when you're ready."
    exit 0
}

try {
    Assert-Prerequisites
    Resolve-LegacyLayout
    $state = Get-InstallState
    if (-not $state.DataOk) { Get-ClientSetup }
    Show-Summary $state
    Install-Source $state
    Install-BuildImage $state
    Invoke-Compile $state
    Invoke-Extract $state
    Resolve-DbPassword $state
    Write-ComposeAndConfigs
    Start-ServerAndDatabase $state
    New-DefaultAccount $state
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
