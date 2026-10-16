#!/bin/bash
source @HOME@/dml-launcher-lib.sh      # must be first: it re-execs to drop Steam's overlay shim
# Dad's MMO Lab — @TITLE@ server launcher, for the server Yu'lon installed in
# @SERVER_DIR@. Written by "Add to Steam Deck (Gaming Mode)…" on that server's tab.
#
# The Lab's lifecycle (dml-launcher-lib.sh): start the server, wait for a real ready
# signal, say so, watch for the game, stop the server when it closes. The server work is
# Yu'lon's own: `yulon --server start` runs the Start the tab's button runs (the
# reservation against another open Yu'lon, the checks, the realm port, the staged start
# that never re-runs the database import) and waits for the world's own ready line. A
# Yu'lon WoW server is never started with a bare `docker compose up`.
#
# One game at a time: a server holding this one's ports is stopped first, and Yu'lon
# says which.

YULON=(@YULON@)
GAME="@GAME@"
SERVER_DIR="@SERVER_DIR@"
TITLE="@TITLE@"
LOG="@SCRIPTS_DIR@/launcher.log"

dml_init "$TITLE" "$LOG"
dml_header

if "${YULON[@]}" --server status "$GAME" "$SERVER_DIR" >/dev/null 2>&1; then
    dml_ok "Server already running"
else
    dml_phase "Starting $TITLE..."
    dml_note "The first start after an install or an update can take several minutes."
    "${YULON[@]}" --server start "$GAME" "$SERVER_DIR" 2>&1 | tee -a "$LOG"
    rc=${PIPESTATUS[0]}
    case "$rc" in
        0) dml_ok "Server is up" ;;
        3) dml_die "The server started, but its world did not say it is ready." \
               "Open Yu'lon and look at the $TITLE tab. Log: $LOG" ;;
        *) dml_die "The server did not start." "The reason is above. Log: $LOG" ;;
    esac
fi

dml_ready "$TITLE IS READY!"
echo "  ----------------------------------------------"
echo "  How to log in"
echo ""
echo "     Use an account you made for this server on Yu'lon's Accounts tab."
echo "     The realm is this Deck (127.0.0.1)."
echo ""

wow_stop() {
    "${YULON[@]}" --server stop "$GAME" "$SERVER_DIR" 2>&1 | tee -a "$LOG"
    [ "${PIPESTATUS[0]}" = 0 ] && dml_footer "Server stopped! Safe to close."
}

# Wow.exe for 2.4.3 and 3.3.5a, WoW.exe for 1.12.1 and Turtle: `pgrep -x` is case-sensitive,
# so the watch takes both names.
dml_await_and_track "Wow.exe WoW.exe" "$TITLE" wow_stop
