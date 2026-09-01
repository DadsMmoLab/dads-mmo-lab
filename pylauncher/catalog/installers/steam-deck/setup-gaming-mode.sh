#!/usr/bin/env bash
# Yu'lon - Steam Deck gaming-mode launcher
#
# The one bash file that survived Phase 7.2 (pyplan/phase7-decisions.md, owner
# answer 4), because starting a stack from a Steam library entry is a shell job
# the app does not do. Written on 2026-09-01 from install-wow-wotlk.sh's
# setup_gaming_mode (v1.2.10, lines 1442-1620 at commit 1a098cc5) and
# deliberately smaller than what it replaces:
#
#   * it takes what it needs as ARGUMENTS - the server dir, the game id and the
#     worldserver's ready regex, all catalog data the app already has - instead
#     of baking copies of them into a generated ~/wow-playerbots-launcher.sh;
#   * it starts the stack with `docker compose up -d`, waits for the regex, then
#     only PRINTS the Steam instructions; it launches nothing itself;
#   * it stops with `docker compose stop`, never with the sibling subcommand
#     that removes the containers: for the AzerothCore stack the next `up` would
#     then re-run ac-db-import, the failure dml-start.sh recorded as "was
#     killing the database";
#   * it never reaches for another game's containers. Two games share
#     3724/8085/3306 by design and the Server tab is where a user stops one; the
#     original swept every container whose name looked like WoW and stopped it.
#
# Nothing in the app runs this file - checked 2026-09-01, there is no reference
# to it in yulon/, in catalog.json or in the tests. It ships inside the bundle
# for a person to point Steam at by hand, the way the HOWTOs describe.
#
# Usage:
#   setup-gaming-mode.sh <server_dir> <game_id> <ready_regex>
#   e.g. setup-gaming-mode.sh ~/wow-server-playerbots wow-wotlk 'ready[.][.][.]'
#
# Add it to Steam (Games -> Add a Non-Steam Game -> Browse):
#   Target:  /usr/bin/konsole
#   Options: --hold -e bash <path to this file> <server_dir> <game_id> <ready_regex>
#   Proton:  OFF (this needs no Proton; the client keeps its own setting)
set -u

if [ "$#" -ne 3 ]; then
    echo "usage: $0 <server_dir> <game_id> <ready_regex>" >&2
    exit 2
fi

SERVER_DIR="$1"
GAME_ID="$2"
READY_REGEX="$3"

READY_TIMEOUT_SECONDS=900
CLIENT_WAIT_SECONDS=300
# A WoW client under Proton, seen from the host: the wine process carries the
# exe name. Matched case-insensitively by pgrep -fi below.
CLIENT_PATTERN='Wow[.]exe|wine.*[Ww]o[Ww]'
LOGFILE="${TMPDIR:-/tmp}/yulon-${GAME_ID}-gaming-mode.log"
# How long a closing message stays on screen. Gaming mode runs this inside a
# konsole window that vanishes on exit, so a bare exit would blink the message
# away. The override lets a terminal that stays open - and the tests that pin
# these exit codes - skip the wait.
PAUSE_SECONDS="${YULON_GAMING_MODE_PAUSE:-10}"

# Gaming mode strips the environment Docker's CLI expects, and Steam exports
# LD_PRELOAD for its overlay, which the docker binary does not survive.
export PATH="/usr/bin:/usr/local/bin:/bin:$PATH"
unset LD_PRELOAD
unset LD_LIBRARY_PATH

say() { printf '  %s\n' "$*"; }

if [ ! -f "$SERVER_DIR/docker-compose.yml" ]; then
    say "No docker-compose.yml in $SERVER_DIR - is that the folder Yu'lon installed $GAME_ID into?"
    sleep "$PAUSE_SECONDS"
    exit 1
fi

cd "$SERVER_DIR" || exit 1

clear 2>/dev/null || true
echo ""
say "Yu'lon - $GAME_ID"
say "Starting the server... (log: $LOGFILE)"
echo ""

# Taken BEFORE `up`: a worldserver whose previous run's ready line is still in
# its log would otherwise pass at once - the same false positive
# docker.wait_ready() guards against with the container's StartedAt.
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

if ! docker compose up -d >>"$LOGFILE" 2>&1; then
    say "Failed to start the server. Check: $LOGFILE"
    sleep "$PAUSE_SECONDS"
    exit 1
fi
say "Containers started."
say "First start after an install takes minutes; later starts about 30 seconds."
echo ""

elapsed=0
ready=0
while [ "$elapsed" -lt "$READY_TIMEOUT_SECONDS" ]; do
    # Captured first, then matched. Piping logs straight into `grep -q` under
    # pipefail SIGPIPEs the producer into a false negative - the Tortoise
    # installer recorded exactly that at its line 713.
    recent="$(docker compose logs --no-color --since "$STARTED_AT" 2>/dev/null)"
    if printf '%s\n' "$recent" | grep -Eq -- "$READY_REGEX"; then
        ready=1
        break
    fi
    printf '.'
    sleep 5
    elapsed=$((elapsed + 5))
done
echo ""

if [ "$ready" -eq 1 ]; then
    say "The server is READY."
else
    say "Still starting after ${READY_TIMEOUT_SECONDS}s - launch the game soon, and check"
    say "$LOGFILE if it never comes up."
fi
echo ""
say "Press the STEAM button and launch your game client."
say "The server stops on its own when the client closes,"
say "or press ENTER here to stop it now."
echo ""

manual=0
client_seen=0
waited=0
while [ "$waited" -lt "$CLIENT_WAIT_SECONDS" ]; do
    if pgrep -fi -- "$CLIENT_PATTERN" >/dev/null 2>&1; then
        client_seen=1
        break
    fi
    if read -r -t 5; then
        manual=1
        break
    fi
    waited=$((waited + 5))
done

if [ "$manual" -eq 0 ] && [ "$client_seen" -eq 1 ]; then
    say "Client detected - enjoy."
    while pgrep -fi -- "$CLIENT_PATTERN" >/dev/null 2>&1; do
        if read -r -t 3; then
            manual=1
            break
        fi
    done
    if [ "$manual" -eq 0 ]; then
        sleep 5
        say "Client closed - stopping the server..."
    fi
elif [ "$manual" -eq 0 ]; then
    say "No client detected in ${CLIENT_WAIT_SECONDS}s - press ENTER to stop the server."
    read -r
fi

if [ "$manual" -eq 1 ]; then
    say "Stopping the server..."
fi

# `stop` keeps the containers, so the next `up` is a restart, not a re-import.
docker compose stop >>"$LOGFILE" 2>&1

echo ""
say "Server stopped. Safe to close this window."
sleep "$PAUSE_SECONDS"
exit 0
