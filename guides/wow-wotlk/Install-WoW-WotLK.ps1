#Requires -Version 5.1
<#
.SYNOPSIS
    Dad's MMO Lab — WoW Playerbots Installer for Windows
.DESCRIPTION
    Installs AzerothCore WotLK + Playerbots inside the dml-arch WSL2 distro.
    Requires Install-DML.ps1 to have been run first.

    Version: 1.0.0

    What this does:
      1. Verifies dml-arch and Docker are ready
      2. Shows a summary before building
      3. Compiles AzerothCore + Playerbots (~2-4 hours, inside WSL2)
      4. Waits for the world server to initialize
      5. Guides you through account creation
      After installation, use the DML Launcher tray icon to start/stop the server.

    Changelog:
      1.0.0 - Initial Windows release
        - Mirrors install-wow-wotlk.sh for Windows / dml-arch
        - Encoding-safe WSL execution (\\wsl$\ file write, no pipe BOM)
        - EAP lowered for native command stderr (PS 5.1 compatibility)
        - Integrates with DML Launcher tray app (installed by Install-DML.ps1)
#>

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# =============================================================================
# Config
# =============================================================================
$WizardVersion       = '1.3.0'
$DmlDistro           = 'dml-arch'
$DmlUser             = 'dml'
$ServerDir           = '/home/dml/games/wow-server-playerbots'
$LogFile             = "$env:TEMP\dml-wow-install.log"
$Script:FailReported = $false

# =============================================================================
# Output helpers
# =============================================================================
function Write-Header {
    Clear-Host
    Write-Host ''
    Write-Host '  +==================================================+' -ForegroundColor Cyan
    Write-Host '  |  DAD''S MMO LAB                                   |' -ForegroundColor White
    Write-Host '  |  WoW Playerbots Installer (Windows)              |' -ForegroundColor White
    Write-Host '  |  github.com/DadsMmoLab/dads-mmo-lab              |' -ForegroundColor Blue
    Write-Host "  |  Version $WizardVersion                                     |" -ForegroundColor Yellow
    Write-Host '  +==================================================+' -ForegroundColor Cyan
    Write-Host ''
}

function Write-Step([string]$msg) {
    Write-Host ''
    Write-Host '  --------------------------------------------------' -ForegroundColor Cyan
    Write-Host "   $msg" -ForegroundColor White
    Write-Host '  --------------------------------------------------' -ForegroundColor Cyan
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
# WSL bash execution — encoding-safe, EAP-safe
# Writes script as raw UTF-8 bytes via \\wsl$\ to bypass PowerShell's pipe
# encoding layer (which injects BOMs on some Windows locale configurations).
# Lowers $ErrorActionPreference to Continue for the wsl call so PS 5.1 does
# not abort on normal gpg/pacman/docker stderr output.
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
    $tmpWin   = "$wslTmp\dml-wow-step.sh"
    $tmpLinux = '/tmp/dml-wow-step.sh'
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
function Invoke-DmlCapture([string]$BashOneLiner) {
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $raw = (wsl -d $DmlDistro -u $DmlUser -- bash -c $BashOneLiner 2>&1)
    } finally {
        $ErrorActionPreference = $prevEap
    }
    # PS 5.1 wraps native stderr in ErrorRecord objects when EAP=Continue.
    # Discard them — callers only need stdout.
    $strings = $raw | Where-Object { $_ -is [string] }
    return (($strings -replace "`0", "") -join "`n").Trim()
}

# NOTE: Port proxy / firewall plumbing for LAN play used to live here
# (Setup-PortProxy). It moved to Install-DML.ps1 (Step 12), which owns the
# Windows-side network setup for ALL titles — and it no longer exposes 3306
# (the database) to the LAN. LAN play itself is toggled per title from the
# DML Launcher tray ('dml lan').

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

    # Disk space — compilation needs ~30 GB inside dml-arch
    $freeStr = Invoke-DmlCapture 'df -BG /home 2>/dev/null | tail -1 | awk ''{print $4}'' | tr -d G'
    if ($freeStr -match '^\d+$') {
        $freeGB = [int]$freeStr
        if ($freeGB -lt 30) {
            Write-Fail "Not enough space in dml-arch: ${freeGB}GB free, need at least 30GB for compilation."
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
# Step 1 — Summary
# =============================================================================
function Show-Summary {
    Write-Header
    Write-Step 'STEP 1/3 -- What We''re Building'
    Write-Host ''
    Write-Host "  Server:    WoW Playerbots (AzerothCore WotLK + Playerbots)" -ForegroundColor Cyan
    Write-Host "  Location:  dml-arch -> $ServerDir" -ForegroundColor Cyan
    Write-Host "  Install:   Compile from source (2-4 hours inside WSL2)" -ForegroundColor Yellow
    Write-Host ''
    Write-Host '  What you get:' -ForegroundColor White
    Write-Host '    + Hundreds of AI players roaming the world' -ForegroundColor Green
    Write-Host '    + Bots quest, dungeon, raid alongside you' -ForegroundColor Green
    Write-Host '    + Azeroth feels truly alive -- solo or co-op' -ForegroundColor Green
    Write-Host ''
    Write-Host '  COMPILATION WARNING:' -ForegroundColor Yellow
    Write-Host '  This will take 2-4 hours inside WSL2.' -ForegroundColor White
    Write-Host '  Keep your PC running and plugged in.' -ForegroundColor White
    Write-Host "  Progress is logged to: $LogFile" -ForegroundColor White
    Write-Host ''
    if (-not (Invoke-YesNo 'Ready to build your Playerbots server?')) {
        Write-Host ''
        Write-Host "  No problem -- run this script again when you're ready." -ForegroundColor White
        exit 0
    }
}

# =============================================================================
# Startup failure handler — called when docker compose up -d fails after all
# retries. Detects whether ac-db-import failed (partial volumes from a prior
# crashed run) and auto-resets without prompting — this is an installer, so
# there is no player data to protect yet. Either throws (via Write-Fail) or
# returns normally so the caller can continue the install flow.
# =============================================================================
function Handle-StartupFailure {
    $dbImportStatus = Invoke-DmlCapture "cd '$ServerDir' 2>/dev/null && docker compose ps --all 2>/dev/null | grep -i 'db.import' || echo ''"
    $isDbImportFailure = ($dbImportStatus -match 'exit') -and ($dbImportStatus -notmatch '(?i)exited \(0\)')

    if ($isDbImportFailure) {
        Write-Warn 'Database import failed -- partial data detected in volumes from a previous run.'
        Write-Info 'Auto-resetting database volumes and retrying...'
        $resetExit = Invoke-DmlBash -Label 'db-reset' -Script @"
set -euo pipefail
cd "$ServerDir"
echo "[wow] Removing containers and volumes..."
docker compose down -v 2>/dev/null || true
sleep 10
echo "[wow] Starting fresh..."
docker compose up -d
"@
        if ($resetExit -ne 0) {
            Write-Info 'Check the import logs for details:'
            Write-Info "  wsl -d dml-arch -u dml -- docker compose -f $ServerDir/docker-compose.yml logs ac-db-import --tail 50"
            Write-Fail 'Server startup failed even after database reset.'
        }
        # Returned normally — reset succeeded, caller continues
    } else {
        Write-Warn 'Server failed to start after two attempts.'
        Write-Info 'Check the logs for details:'
        Write-Info "  wsl -d dml-arch -u dml -- docker compose -f $ServerDir/docker-compose.yml logs ac-db-import --tail 30"
        Write-Info "  wsl -d dml-arch -u dml -- docker compose -f $ServerDir/docker-compose.yml logs ac-database --tail 30"
        Write-Fail 'Server startup failed. Re-run this installer to try again -- no recompile needed.'
    }
}

# =============================================================================
# Step 2 — Install server
# =============================================================================
function Install-Server {
    Write-Header
    Write-Step 'STEP 2/3 -- Building Playerbots Server (2-4 hours)'

    # Write docker-compose.override.yml first — needed by both paths.
    # The health check override replaces the upstream mysqladmin ping (which can
    # fail on MySQL 8.4 in some WSL2 environments) with a plain TCP port check.
    # restart: on-failure replaces upstream's unless-stopped so servers still
    # auto-recover from crashes during play, but do NOT auto-boot with dockerd —
    # otherwise any touch of the distro (tray menu, terminal) resurrects
    # whichever servers were running at the last WSL teardown.
    Write-Info 'Writing docker-compose.override.yml...'
    $overrideContentSkip = @'
services:
  ac-worldserver:
    restart: on-failure
    environment:
      AC_PLAYERBOTS_UPDATES_ENABLE_DATABASES: "1"
      AC_AI_PLAYERBOT_RANDOM_BOT_AUTOLOGIN: "1"
      AC_AI_PLAYERBOT_MIN_RANDOM_BOTS: "200"
      AC_AI_PLAYERBOT_MAX_RANDOM_BOTS: "250"
      AC_PLAYERBOTS_DATABASE_WORKER_THREADS: "1"
      AC_PLAYERBOTS_DATABASE_SYNCH_THREADS: "1"
    volumes:
      - ./modules/mod-playerbots:/azerothcore/modules/mod-playerbots:ro
  ac-authserver:
    restart: on-failure
  ac-database:
    restart: on-failure
    healthcheck:
      test: ["CMD-SHELL", "bash -c 'echo > /dev/tcp/127.0.0.1/3306' 2>/dev/null && echo ok || exit 1"]
      interval: 5s
      timeout: 5s
      retries: 180
      start_period: 60s
'@

    # Migrate from old install path if needed (pre-v1.1 installs used /home/dml/wow-server-playerbots)
    $OldServerDir = '/home/dml/wow-server-playerbots'
    $migrateCheck = Invoke-DmlCapture "[[ -d '$OldServerDir' && ! -d '$ServerDir' ]] && echo migrate || echo skip"
    if ($migrateCheck -eq 'migrate') {
        Write-Info 'Found existing install at old path -- migrating to correct location...'
        $migrateExit = Invoke-DmlBash -Label 'migrate-path' -Script @"
set -euo pipefail
cd '$OldServerDir'
echo "[wow] Stopping containers and wiping volumes (database will repopulate on next start)..."
docker compose down -v 2>/dev/null || true
echo "[wow] Moving to correct location..."
mkdir -p /home/dml/games
mv '$OldServerDir' '$ServerDir'
echo "[wow] Migration complete."
"@
        if ($migrateExit -eq 0) {
            Write-Ok 'Migrated to correct install path.'
        } else {
            Write-Warn 'Path migration failed -- a fresh install will be attempted.'
        }
    }

    # Skip compile if worldserver images already exist
    # NOTE: must query the image store directly ('docker images'), NOT 'docker
    # compose images' — the latter only lists images of EXISTING containers, so
    # after 'docker compose down -v' it reports nothing and a working install
    # would be misrouted into the fresh-compile path (which deletes ServerDir).
    $imageCheck = Invoke-DmlCapture "test -f '$ServerDir/docker-compose.yml' && docker images --format '{{.Repository}}' 2>/dev/null | grep -qi 'worldserver' && echo found || echo not-found"
    if ($imageCheck -eq 'found') {
        Write-Ok 'Compiled images already found -- skipping compile.'
        Write-Info 'To force a fresh compile, delete the server folder inside dml-arch:'
        Write-Info "  wsl -d dml-arch -u dml -- rm -rf $ServerDir"
        Write-Info 'Then re-run this installer.'

        $overrideWin = ConvertTo-WslWinPath "$ServerDir/docker-compose.override.yml"
        [System.IO.File]::WriteAllBytes($overrideWin, [System.Text.UTF8Encoding]::new($false).GetBytes($overrideContentSkip))
        Write-Ok 'docker-compose.override.yml updated'

        Write-Info 'Starting server...'
        $startExit = Invoke-DmlBash -Label 'server-start' -Script @"
set -euo pipefail
cd "$ServerDir"

# Pre-flight: clean up any bad state left by a previous run.
# Check for unhealthy containers OR a previously failed db-import.
_needs_down=false
_needs_wipe=false
if docker ps -a --format '{{.Status}}' 2>/dev/null | grep -qi 'unhealthy'; then
    echo "[wow] Found unhealthy containers from a previous run -- cleaning up..."
    _needs_down=true
fi
if docker ps -a --format '{{.Names}}:{{.Status}}' 2>/dev/null | grep -i 'db.import' | grep -qi 'exited'; then
    if ! docker ps -a --format '{{.Names}}:{{.Status}}' 2>/dev/null | grep -i 'db.import' | grep -qi 'exited (0)'; then
        echo "[wow] Previous database import failed -- wiping volumes for a clean import..."
        _needs_down=true
        _needs_wipe=true
    fi
fi
if `$_needs_wipe; then
    docker compose down -v 2>/dev/null || true
    sleep 15
elif `$_needs_down; then
    docker compose down 2>/dev/null || true
    sleep 10
fi

# Phase 1: start the database and poll its health ourselves. We must NOT let
# 'docker compose up' enforce the health deadline: compose aborts permanently
# the moment Docker marks the container unhealthy, but MySQL's first-run
# initialization on WSL2 can outlast any fixed healthcheck budget (observed:
# 27 minutes right after a VHD compaction). Docker flips the status back to
# healthy as soon as probes pass, so patient polling succeeds where compose's
# dependency wait gives up.
_start_db_and_wait() {
    if ! docker compose up -d --no-deps ac-database ac-client-data-init; then
        echo "[wow] docker compose failed to start the database container."
        return 1
    fi
    _t0=`$(date +%s)
    while true; do
        _h=`$(docker inspect ac-database --format '{{.State.Health.Status}}' 2>/dev/null || echo unknown)
        _el=`$(( `$(date +%s) - _t0 ))
        if [ "`$_h" = "healthy" ]; then
            echo "[wow] Database is up (took `${_el}s)."
            return 0
        fi
        if [ "`$_el" -ge 3600 ]; then
            echo "[wow] Database did not come up within 60 minutes -- giving up."
            return 1
        fi
        if [ `$(( _el % 30 )) -lt 5 ]; then
            echo "[wow] Database still starting... (`${_el}s elapsed, health: `$_h)"
        fi
        sleep 5
    done
}

# Only run the import if db-import hasn't already completed from a prior run.
# grep -c prints the count (including 0) itself; '|| true' only guards set -e
# on no-match exit status. '|| echo 0' here would emit a second 0 and break -eq.
_db_done=`$(docker ps -a --format '{{.Names}}:{{.Status}}' 2>/dev/null | grep -c 'db-import.*Exited (0)' || true)
if [ "`$_db_done" -eq 0 ]; then
    echo "[wow] Starting database (first-run initialization can take a long time on slow disks)..."
    _start_db_and_wait || exit 1
    echo "[wow] Importing world databases (5-10 minutes)..."
    docker compose up -d ac-db-import
    if ! docker compose wait ac-db-import; then
        echo "[wow] Database import failed -- wiping volumes and retrying once..."
        docker compose down -v 2>/dev/null || true
        sleep 15
        _start_db_and_wait || exit 1
        docker compose up -d ac-db-import
        if ! docker compose wait ac-db-import; then
            echo "[wow] Database import failed twice -- giving up."
            exit 1
        fi
    fi
    echo "[wow] Database import complete."
fi

# Phase 2: initialize acore_playerbots base schema before worldserver starts.
# Without this step the worldserver sends MySQL SHUTDOWN when it encounters a
# missing table (update applied before base), causing an infinite crash loop.
_pb_base="$ServerDir/modules/mod-playerbots/data/sql/playerbots/base"
_pb_tables=`$(docker exec ac-database mysql -uroot -ppassword -sN \
    -e "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='acore_playerbots';" 2>/dev/null || echo 0)
if [ "`$_pb_tables" -lt 29 ]; then
    echo "[wow] Initializing acore_playerbots (`$_pb_tables/29 base tables present)..."
    docker exec ac-database mysql -uroot -ppassword \
        -e "DROP DATABASE IF EXISTS acore_playerbots; CREATE DATABASE acore_playerbots DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_unicode_ci;" 2>/dev/null
    for _f in "`$_pb_base"/*.sql; do
        docker exec -i ac-database mysql -uroot -ppassword acore_playerbots 2>/dev/null < "`$_f"
    done
    echo "[wow] acore_playerbots initialized."
else
    echo "[wow] acore_playerbots already has `$_pb_tables tables -- skipping init."
fi

# Phase 3: start worldserver and authserver (db-import already completed above).
if ! docker compose up -d --no-deps ac-worldserver ac-authserver; then
    echo "[wow] Worldserver start failed -- retrying..."
    sleep 10
    docker compose up -d --no-deps ac-worldserver ac-authserver
fi
"@
        if ($startExit -ne 0) {
            Handle-StartupFailure
        }
        Write-Ok 'Playerbots server started!'
        return
    }

    # Clone
    Write-Info 'Cloning AzerothCore WotLK + Playerbots (official mod-playerbots fork)...'
    $cloneExit = Invoke-DmlBash -Label 'clone' -Script @"
set -euo pipefail

if [ -d "$ServerDir" ]; then
    # Safety net: a complete install (compose file + compiled images) must never
    # reach this path — deleting it would destroy a working server. If we get
    # here anyway, path detection has a bug; refuse rather than destroy.
    if [ -f "$ServerDir/docker-compose.yml" ] && docker images --format '{{.Repository}}' 2>/dev/null | grep -qi 'worldserver'; then
        echo "[err] Existing install with compiled images found at $ServerDir -- refusing to delete it." >&2
        echo "[err] This should not happen; please report this as an installer bug." >&2
        exit 2
    fi
    echo "[wow] Removing incomplete previous install..."
    docker compose -f "$ServerDir/docker-compose.yml" down -v 2>/dev/null || true
    rm -rf "$ServerDir"
fi

git clone https://github.com/mod-playerbots/azerothcore-wotlk.git \
    --branch=Playerbot \
    "$ServerDir"

if [ ! -d "$ServerDir" ]; then
    echo "[err] Clone failed -- check your internet connection." >&2
    exit 1
fi

mkdir -p "$ServerDir/modules"

echo "[wow] Cloning mod-playerbots module..."
git clone --depth 1 \
    https://github.com/mod-playerbots/mod-playerbots.git \
    --branch=master \
    "$ServerDir/modules/mod-playerbots"
if [ ! -d "$ServerDir/modules/mod-playerbots/data" ]; then
    echo "[err] mod-playerbots clone failed or incomplete -- worldserver cannot start without it." >&2
    exit 1
fi
"@
    if ($cloneExit -ne 0) { Write-Fail "Clone failed (exit $cloneExit). Check your internet connection." }
    Write-Ok 'Source cloned'

    # Write docker-compose.override.yml via \\wsl$\ (encoding-safe)
    # (skip-compile path already wrote this above; fresh-compile path writes the
    # full version including build targets so docker compose build works correctly)
    $overrideContent = @'
services:
  ac-worldserver:
    restart: on-failure
    build:
      context: .
      target: worldserver
    volumes:
      - ./modules:/azerothcore/modules
    environment:
      AC_PLAYERBOTS_UPDATES_ENABLE_DATABASES: "1"
      AC_AI_PLAYERBOT_RANDOM_BOT_AUTOLOGIN: "1"
      AC_AI_PLAYERBOT_MIN_RANDOM_BOTS: "200"
      AC_AI_PLAYERBOT_MAX_RANDOM_BOTS: "250"
      AC_PLAYERBOTS_DATABASE_WORKER_THREADS: "1"
      AC_PLAYERBOTS_DATABASE_SYNCH_THREADS: "1"
  ac-authserver:
    restart: on-failure
    build:
      context: .
      target: authserver
  ac-db-import:
    build:
      context: .
      target: db-import
  ac-client-data-init:
    build:
      context: .
      target: client-data
  ac-database:
    restart: on-failure
    healthcheck:
      test: ["CMD-SHELL", "bash -c 'echo > /dev/tcp/127.0.0.1/3306' 2>/dev/null && echo ok || exit 1"]
      interval: 5s
      timeout: 5s
      retries: 180
      start_period: 60s
'@
    $overrideWin = ConvertTo-WslWinPath "$ServerDir/docker-compose.override.yml"
    [System.IO.File]::WriteAllBytes($overrideWin, [System.Text.UTF8Encoding]::new($false).GetBytes($overrideContent))
    Write-Ok 'docker-compose.override.yml written'

    # Compile — build images first, then start containers separately.
    # Splitting these lets us give an accurate error message:
    # a build failure means check the log; a startup failure is usually
    # the database health check timing out on first run (auto-retried once).
    Write-Info 'Compiling Playerbots server (2-4 hours)...'
    Write-Info 'Keep your PC running and plugged in.'
    Write-Info 'Go make a coffee -- this will take a while!'
    $buildExit = Invoke-DmlBash -Label 'build' -Script @"
set -euo pipefail
cd "$ServerDir"

echo "[wow] Building images..."
docker compose build 2>&1 | tee ~/playerbots-build.log

echo "[wow] Starting containers..."
_needs_down=false
_needs_wipe=false
if docker ps -a --format '{{.Status}}' 2>/dev/null | grep -qi 'unhealthy'; then
    echo "[wow] Found unhealthy containers -- cleaning up before start..."
    _needs_down=true
fi
if docker ps -a --format '{{.Names}}:{{.Status}}' 2>/dev/null | grep -i 'db.import' | grep -qi 'exited'; then
    if ! docker ps -a --format '{{.Names}}:{{.Status}}' 2>/dev/null | grep -i 'db.import' | grep -qi 'exited (0)'; then
        echo "[wow] Previous database import failed -- wiping volumes for a clean import..."
        _needs_down=true
        _needs_wipe=true
    fi
fi
if `$_needs_wipe; then
    docker compose down -v 2>/dev/null || true
    sleep 15
elif `$_needs_down; then
    docker compose down 2>/dev/null || true
    sleep 10
fi
# Phase 1: start the database and poll its health ourselves (see skip-compile
# path for rationale: compose's dependency wait aborts permanently on the first
# unhealthy status, but slow first-run MySQL init recovers to healthy later).
_start_db_and_wait() {
    if ! docker compose up -d --no-deps ac-database ac-client-data-init; then
        echo "[wow] docker compose failed to start the database container."
        return 1
    fi
    _t0=`$(date +%s)
    while true; do
        _h=`$(docker inspect ac-database --format '{{.State.Health.Status}}' 2>/dev/null || echo unknown)
        _el=`$(( `$(date +%s) - _t0 ))
        if [ "`$_h" = "healthy" ]; then
            echo "[wow] Database is up (took `${_el}s)."
            return 0
        fi
        if [ "`$_el" -ge 3600 ]; then
            echo "[wow] Database did not come up within 60 minutes -- giving up."
            return 1
        fi
        if [ `$(( _el % 30 )) -lt 5 ]; then
            echo "[wow] Database still starting... (`${_el}s elapsed, health: `$_h)"
        fi
        sleep 5
    done
}

echo "[wow] Starting database (first-run initialization can take a long time on slow disks)..."
_start_db_and_wait || exit 1
echo "[wow] Importing world databases (5-10 minutes)..."
docker compose up -d ac-db-import
if ! docker compose wait ac-db-import; then
    echo "[wow] Database import failed -- wiping volumes and retrying once..."
    docker compose down -v 2>/dev/null || true
    sleep 15
    _start_db_and_wait || exit 1
    docker compose up -d ac-db-import
    if ! docker compose wait ac-db-import; then
        echo "[wow] Database import failed twice -- giving up."
        exit 1
    fi
fi
echo "[wow] Database import complete."

# Phase 2: initialize acore_playerbots base schema before worldserver starts.
# Without this step the worldserver sends MySQL SHUTDOWN when it encounters a
# missing table (update applied before base), causing an infinite crash loop.
_pb_base="$ServerDir/modules/mod-playerbots/data/sql/playerbots/base"
_pb_tables=`$(docker exec ac-database mysql -uroot -ppassword -sN \
    -e "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='acore_playerbots';" 2>/dev/null || echo 0)
if [ "`$_pb_tables" -lt 29 ]; then
    echo "[wow] Initializing acore_playerbots (`$_pb_tables/29 base tables present)..."
    docker exec ac-database mysql -uroot -ppassword \
        -e "DROP DATABASE IF EXISTS acore_playerbots; CREATE DATABASE acore_playerbots DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_unicode_ci;" 2>/dev/null
    for _f in "`$_pb_base"/*.sql; do
        docker exec -i ac-database mysql -uroot -ppassword acore_playerbots 2>/dev/null < "`$_f"
    done
    echo "[wow] acore_playerbots initialized."
else
    echo "[wow] acore_playerbots already has `$_pb_tables tables -- skipping init."
fi

# Phase 3: start worldserver and authserver (db-import already completed above).
if ! docker compose up -d --no-deps ac-worldserver ac-authserver; then
    echo "[wow] Worldserver start failed -- retrying..."
    sleep 10
    docker compose up -d --no-deps ac-worldserver ac-authserver
fi
"@
    if ($buildExit -ne 0) {
        $imagesExist = Invoke-DmlCapture "docker images --format '{{.Repository}}' 2>/dev/null | grep -qi 'worldserver' && echo yes || echo no"
        if ($imagesExist -eq 'yes') {
            Handle-StartupFailure
        } else {
            Write-Fail "Compilation failed (exit $buildExit).`nCheck: wsl -d dml-arch -u dml -- cat ~/playerbots-build.log"
        }
    }
    Write-Ok 'Playerbots server compiled and started!'
}

# =============================================================================
# Wait for server ready — polls docker logs for "ready..."
# =============================================================================
function Wait-ForServer {
    param([string]$Message = 'First launch after compilation: 30-60 minutes. Subsequent starts: ~30 seconds.')

    Write-Info 'Waiting for world server to initialize...'
    Write-Info $Message
    Write-Host ''

    # The whole wait runs inside ONE long-lived WSL session. Windows tears the
    # WSL VM down within seconds of the last wsl.exe session exiting — killing
    # MySQL while the worldserver is still applying first-boot updates. Polling
    # from PowerShell with short wsl.exe calls left exactly such gaps between
    # polls, which is how installs kept dying at this stage.
    $waitExit = Invoke-DmlBash -Label 'wait-ready' -Script @"
end=`$((SECONDS+3600))
while [ `$SECONDS -lt `$end ]; do
    c=`$(docker ps --format '{{.Names}}' 2>/dev/null | grep worldserver | head -1)
    if [ -n "`$c" ] && docker logs --tail 50 "`$c" 2>&1 | grep -q 'ready\.\.\.'; then
        echo "[wow] World server is ready."
        exit 0
    fi
    if [ `$(( SECONDS % 60 )) -lt 15 ]; then
        echo "[wow] Server still starting... (`$(( SECONDS / 60 )) min elapsed -- first boot applies database updates, be patient)"
    fi
    sleep 15
done
echo "[wow] Server did not report ready within 60 minutes."
exit 1
"@

    Write-Host ''

    if ($waitExit -eq 0) {
        Write-Ok 'Server is READY!'
        return $true
    } else {
        Write-Warn 'Server is taking longer than expected.'
        Write-Info 'Check: wsl -d dml-arch -u dml -- docker logs -f ac-worldserver'
        return $false
    }
}

# =============================================================================
# Playerbot config — copy .dist to .conf, restart worldserver
# =============================================================================
function Setup-PlayerbotConfig {
    Write-Step 'Configuring Playerbots'

    $etcLinux  = "$ServerDir/env/dist/etc"
    $distLinux = "$etcLinux/playerbot.conf.dist"
    $confLinux = "$etcLinux/playerbot.conf"
    $distWin   = ConvertTo-WslWinPath $distLinux
    $confWin   = ConvertTo-WslWinPath $confLinux

    # playerbot.conf.dist is written by db-import just before worldserver signals ready.
    # On slow machines the file may not be visible yet — retry before falling back to search.
    $distFound = $false
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        if (Test-Path $distWin) { $distFound = $true; break }
        if ($attempt -lt 3) {
            Write-Info "playerbot.conf.dist not yet visible (attempt $attempt/3) -- waiting 10s..."
            Start-Sleep -Seconds 10
        }
    }

    if (-not $distFound) {
        $found = Invoke-DmlCapture "find '$ServerDir' -name 'playerbot.conf.dist' 2>/dev/null | head -1"
        if ($found) {
            $distLinux = $found
            $confLinux = $found -replace '\.dist$', ''
            $distWin   = ConvertTo-WslWinPath $distLinux
            $confWin   = ConvertTo-WslWinPath $confLinux
        } else {
            Write-Warn 'playerbot.conf.dist not found -- bots may use worldserver.conf defaults.'
            Write-Info "Expected: $etcLinux/playerbot.conf.dist"
            return
        }
    }

    if (Test-Path $confWin) {
        Write-Ok 'playerbot.conf already present -- skipping'
        return
    }

    Write-Info 'Copying playerbot.conf.dist -> playerbot.conf...'
    try {
        Copy-Item $distWin $confWin -ErrorAction Stop
        Write-Ok 'playerbot.conf created'
    } catch {
        Write-Warn "Failed to copy playerbot.conf: $($_.Exception.Message)"
        Write-Info "Fix manually: wsl -d dml-arch -u dml -- cp $distLinux $confLinux"
        return
    }

    # Restart worldserver so it loads the new config
    $prevEap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $names = (wsl -d $DmlDistro -u $DmlUser -- docker ps --format '{{.Names}}' 2>&1) -replace "`0", ""
    $container = ($names | Where-Object { $_ -match 'worldserver' } | Select-Object -First 1)
    $ErrorActionPreference = $prevEap

    if ($container) {
        Write-Info 'Restarting worldserver to load playerbot.conf...'
        $prevEap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
        wsl -d $DmlDistro -u $DmlUser -- docker restart $container | Out-Null
        $restartOk = ($LASTEXITCODE -eq 0)
        $ErrorActionPreference = $prevEap

        if (-not $restartOk) {
            Write-Warn "Worldserver restart failed -- playerbot.conf is on disk but won't load until the server restarts."
            Write-Info "Restart manually: wsl -d dml-arch -u dml -- docker restart $container"
            return
        }
        $null = Wait-ForServer 'Restarting to load playerbot.conf -- usually ready in ~30 seconds.'
    }
}

# =============================================================================
# Step 3 — Account creation guidance
# =============================================================================
function Show-AccountCreation {
    Write-Header
    Write-Step 'STEP 3/3 -- Create Your Accounts'
    Write-Host ''
    Write-Host '  Your server is running!' -ForegroundColor Green
    Write-Host ''
    Write-Host '  Open a new PowerShell or Command Prompt window' -ForegroundColor White
    Write-Host '  and follow these steps:' -ForegroundColor White
    Write-Host ''
    Write-Host '  --------------------------------------------------' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '  1. Open the GM Console:' -ForegroundColor White
    Write-Host '     wsl -d dml-arch -u dml' -ForegroundColor Cyan
    Write-Host '     docker attach $(docker ps --format ''{{.Names}}'' | grep worldserver | head -1)' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '  2. Create your account (replace USERNAME and PASSWORD):' -ForegroundColor White
    Write-Host '     account create USERNAME PASSWORD' -ForegroundColor Green
    Write-Host '     account set gmlevel USERNAME 3 -1' -ForegroundColor Green
    Write-Host ''
    Write-Host '  3. Exit the console safely:' -ForegroundColor White
    Write-Host '     Ctrl+P then Ctrl+Q' -ForegroundColor Yellow
    Write-Host '     Never press Ctrl+C -- that stops the server!' -ForegroundColor Red
    Write-Host ''
    Write-Host '  --------------------------------------------------' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '  Press ENTER when done creating accounts...' -ForegroundColor White
    $null = Read-Host
}


# =============================================================================
# Completion
# =============================================================================
function Show-Completion {
    Write-Host ''
    Write-Host '  +==================================================+' -ForegroundColor Green
    Write-Host '  |   YOUR PLAYERBOTS SERVER IS READY!               |' -ForegroundColor Green
    Write-Host '  +==================================================+' -ForegroundColor Green
    Write-Host ''
    Write-Host "  Server:   wow-server-playerbots (AzerothCore WotLK)" -ForegroundColor Cyan
    Write-Host "  Location: dml-arch -> $ServerDir" -ForegroundColor Cyan
    Write-Host ''
    Write-Host '  --------------------------------------------------' -ForegroundColor Cyan
    Write-Host '  STEP A -- Set Your WoW Realmlist' -ForegroundColor White
    Write-Host '  --------------------------------------------------' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '  1. Open your WoW client folder' -ForegroundColor White
    Write-Host '  2. Find and open: realmlist.wtf' -ForegroundColor White
    Write-Host '  3. Make sure it says: set realmlist 127.0.0.1' -ForegroundColor Green
    Write-Host '  4. Save the file' -ForegroundColor White
    Write-Host ''
    Write-Host '  --------------------------------------------------' -ForegroundColor Cyan
    Write-Host '  STEP B -- Start Your Server' -ForegroundColor White
    Write-Host '  --------------------------------------------------' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '  Use the DML Launcher in your system tray:' -ForegroundColor White
    Write-Host '  Right-click the DML icon -> wow-server-playerbots -> Start' -ForegroundColor Green
    Write-Host ''
    Write-Host '  The tray shows all your installed servers.' -ForegroundColor White
    Write-Host '  Start and Stop any server from there.' -ForegroundColor White
    Write-Host ''
    Write-Host '  --------------------------------------------------' -ForegroundColor Yellow
    Write-Host '  youtube.com/@DadsMmoLab' -ForegroundColor White
    Write-Host '  github.com/DadsMmoLab/dads-mmo-lab' -ForegroundColor White
    Write-Host '  ko-fi.com/dadsmmolab' -ForegroundColor White
    Write-Host '  --------------------------------------------------' -ForegroundColor Yellow
    Write-Host ''
    Write-Host "  Welcome to Azeroth. It's yours now. Forever." -ForegroundColor Green
    Write-Host ''
    Write-Host '  Your server is still running right now!' -ForegroundColor Yellow
    Write-Host '  Stop it from the tray, or answer yes below.' -ForegroundColor Yellow
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
        Write-Info 'Server left running -- enjoy Azeroth!'
    }
    Write-Host ''
}

# =============================================================================
# Main
# =============================================================================
Write-Header

Write-Host '  Welcome to the WoW Playerbots installer for Windows!' -ForegroundColor White
Write-Host '  Hundreds of AI players will roam your Azeroth,' -ForegroundColor White
Write-Host '  quest, run dungeons, and make the world feel alive.' -ForegroundColor White
Write-Host ''
Write-Host '  This takes about 5 minutes to set up, then' -ForegroundColor Blue
Write-Host '  compiles itself over 2-4 hours. Walk away and let it run.' -ForegroundColor Blue
Write-Host ''

if (-not (Invoke-YesNo 'Ready to begin?')) {
    Write-Host "  No problem -- run this script again when you're ready."
    exit 0
}

try {
    Assert-Prerequisites
    Show-Summary
    Install-Server
    $null = Wait-ForServer
    Setup-PlayerbotConfig
    Show-AccountCreation
    Show-Completion
} catch {
    if (-not $Script:FailReported) {
        Write-Host ''
        Write-Host "[FAIL] $($_.Exception.Message)" -ForegroundColor Red
    }
    Write-Host ''
    Write-Host "  Full log: $LogFile" -ForegroundColor Yellow
    exit 1
}
