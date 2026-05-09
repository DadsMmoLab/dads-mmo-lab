#!/bin/bash
# ============================================================
#  Dad's MMO Lab — Playerbots Gaming Mode Launcher v1.2.0
#  https://github.com/DadsMmoLab/dads-mmo-lab
#
#  FLOW:
#  1. Launch this from Steam Gaming Mode
#  2. Wait for "AZEROTH IS READY"
#  3. Press Steam button → launch WoW
#  4. Play!
#  5. Close WoW → server auto-shuts down
# ============================================================

LAUNCHER_VERSION="1.2.0"

# Clean environment
export PATH="/usr/bin:/usr/local/bin:/bin:$PATH"
unset LD_PRELOAD
unset LD_LIBRARY_PATH

# All stderr to log
LOGFILE="/tmp/wow-playerbots-launch.log"
exec 2>"$LOGFILE"

# ─────────────────────────────────────────
# CHECKS
# ─────────────────────────────────────────
clear
echo ""
echo "  ⚔️  DAD'S MMO LAB — Playerbots Gaming Mode Launcher v${LAUNCHER_VERSION}"
echo "  ══════════════════════════════════════"
echo "  WoW + Playerbots Server"
echo "  ══════════════════════════════════════"
echo ""

if [ ! -d "$HOME/wow-server-playerbots" ]; then
    echo "  ERR: Server not found!"
    echo "  Run install.sh first."
    sleep 5
    exit 1
fi

if ! command -v docker &>/dev/null; then
    echo "  ERR: Docker not found."
    echo "  Reboot and try again."
    sleep 5
    exit 1
fi

# ─────────────────────────────────────────
# START SERVER
# ─────────────────────────────────────────
echo "  Starting server..."
echo ""

# Stop any other running WoW servers first
# Prevents database conflicts between server versions
WOW_CONTAINERS=$(docker ps --format '{{.Names}}' 2>/dev/null | \
    grep -iE "worldserver|authserver|ac-database|ac-eluna|ac-client|ac-db-import" || true)

if [ -n "$WOW_CONTAINERS" ]; then
    echo "  Stopping any running servers first..."
    echo "$WOW_CONTAINERS" | xargs docker stop >> "$LOGFILE" 2>&1 || true
    sleep 5
    echo "  All clear!"
    echo ""
fi

cd "$HOME/wow-server-playerbots" || exit 1

if docker compose up -d --scale phpmyadmin=0 >> "$LOGFILE" 2>&1; then
    echo "  Containers started!"
elif docker compose up -d >> "$LOGFILE" 2>&1; then
    echo "  Containers started (phpmyadmin fallback used)"
else
    echo "  ERR: Failed to start server."
    echo "  Check: $LOGFILE"
    sleep 10
    exit 1
fi

echo ""

# ─────────────────────────────────────────
# WAIT FOR WORLD SERVER
# ─────────────────────────────────────────
echo "  Waiting for world to initialize..."
echo "  First launch:       5-15 minutes"
echo "  After first launch: ~30 seconds"
echo ""

TIMEOUT=900
ELAPSED=0
READY=0
WORLD_CONTAINER=""

while [ $ELAPSED -lt $TIMEOUT ]; do
    WORLD_CONTAINER=$(docker ps --format '{{.Names}}' \
        2>/dev/null | grep -i "worldserver" | head -1)

    if [ -n "$WORLD_CONTAINER" ]; then
        if docker logs "$WORLD_CONTAINER" \
            2>/dev/null | grep -q "ready\.\.\."; then
            READY=1
            break
        fi
    fi

    printf "  ."
    sleep 5
    ELAPSED=$((ELAPSED + 5))
done

echo ""
echo ""

# ─────────────────────────────────────────
# READY SCREEN
# ─────────────────────────────────────────
if [ $READY -eq 1 ]; then
    echo "  ══════════════════════════════════════"
    echo "  ✅ AZEROTH IS READY!"
    echo "  ══════════════════════════════════════"
else
    echo "  ══════════════════════════════════════"
    echo "  ⏳ Still initializing..."
    echo "  ══════════════════════════════════════"
fi

echo ""
echo "  Press STEAM button and launch WoW"
echo "  from your Steam library now."
echo ""
echo "  Server will AUTO-SHUTDOWN when"
echo "  you close WoW. Enjoy! ⚔️"
echo ""
echo "  ══════════════════════════════════════"
echo ""

# ─────────────────────────────────────────
# WAIT FOR WOW TO START
# ─────────────────────────────────────────
echo "  Waiting for WoW to launch..."
echo ""

WOW_STARTED=0
for i in $(seq 1 60); do
    if pgrep -f "Wow\.exe" > /dev/null 2>&1; then
        WOW_STARTED=1
        break
    fi
    sleep 5
done

# ─────────────────────────────────────────
# WATCH FOR WOW TO CLOSE
# ─────────────────────────────────────────
if [ $WOW_STARTED -eq 1 ]; then
    echo "  WoW detected! Have fun! ⚔️"
    echo "  Server shuts down when WoW closes."
    echo ""

    while pgrep -f "Wow\.exe" > /dev/null 2>&1; do
        sleep 3
    done

    sleep 5
    echo ""
    echo "  WoW closed — shutting down server..."

else
    echo "  WoW not detected after 5 minutes."
    echo "  Shutting down server."
fi

# ─────────────────────────────────────────
# AUTO SHUTDOWN
# ─────────────────────────────────────────
echo ""

cd "$HOME/wow-server-playerbots"
docker compose down >> "$LOGFILE" 2>&1

echo "  ══════════════════════════════════════"
echo "  ✅ Server stopped! Safe to close."
echo "  ══════════════════════════════════════"
echo ""
echo "  Thanks for playing!"
echo "  youtube.com/@DadsMmoLab"
echo ""

sleep 5
