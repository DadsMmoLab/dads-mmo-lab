#!/bin/bash
DML_LIB_VERSION="1.3.0"   # read by DadsMmoLab.sh, which refreshes this file when it carries a newer one
# Dad's MMO Lab — shared launcher library v1.3.0
#   v1.3.0  a client that turns up AFTER the 5-minute window is still tracked:
#           the idle hold polls for it instead of sleeping, so a slow first
#           Proton launch no longer leaves the server running for 3 hours.
#   v1.2.0  the client watch takes a LIST of process names (launcher→game
#           handoffs), plus a 20s grace before believing the client is gone.
#
# THE GOLD STANDARD SERVER-LAUNCHER LIFECYCLE (agreed 2026-07-29).
# Modelled on ~/mhf-launcher.sh and ~/wow-playerbots-launcher.sh, which had the
# nicest shape in the fleet. Every server launcher from here on should follow it:
#
#   1. Start the server.
#   2. Wait for a REAL readiness signal, showing progress dots.
#   3. Say the world is ready, and tell the player to launch the game.
#   4. Watch for the client process. When it appears, say have fun.
#   5. Stay alive while they play.
#   6. When the client exits, shut the server down automatically.
#   7. Confirm the server is safely down.
#
# The launcher is the SERVER's app. The client is its own Steam shortcut, which
# is what lets step 4 work at all. Two shortcuts per game:
#
#   "<Game> Server"      -> konsole --hold -e bash ~/<game>-launcher.sh
#   "<Game>"             -> ~/<game>-client.sh   (or the .exe directly)
#
# Source this, then call the pieces. A launcher supplies only what is
# game-specific: how to start the server, how to prove it is ready, the client's
# exact process name, and how to stop the server.
#
# Usage sketch:
#   source /home/deck/dml-launcher-lib.sh
#   dml_init "Eden Eternal" "$LOGFILE"
#   dml_header
#   ... start the server, calling dml_ok / dml_die as you go ...
#   dml_phase "Waiting for the world to wake up..."
#   dml_wait_until 40 5 my_ready_check || dml_die "..." "..."
#   dml_ready "EDEN ETERNAL IS READY!"
#   dml_await_and_track "game_.exe" "Eden Eternal" my_stop_function
#   dml_footer "Server stopped! Safe to close."

# ── Steam overlay scrub (applied automatically when this file is sourced) ─────
#
# Steam starts every shortcut with its overlay shim in LD_PRELOAD, naming BOTH
# the 32- and 64-bit gameoverlayrenderer.so. That variable is inherited by
# everything the launcher spawns, and it produces two kinds of noise that bury
# the launcher's own output:
#
#   ERROR: ld.so: object '.../ubuntu12_32/gameoverlayrenderer.so' from LD_PRELOAD
#          cannot be preloaded (wrong ELF class: ELFCLASS32): ignored.
#       — printed by every 64-bit helper we run (pgrep, docker, timeout, nmcli,
#         tr...), i.e. a couple of lines every 5s out of each wait loop.
#
#   pid 13788 != 13785, skipping destruction (fork without exec?)
#       — the 64-bit shim's destructor running in a forked-but-not-exec'd child.
#         `( ... )`, `$( ... )` and background subshells are exactly that, so the
#         shutdown path fires these off in bursts.
#
# Neither is an error we can act on and neither means anything for a shell
# script, so the overlay is dropped. The original is kept in
# DML_STEAM_LD_PRELOAD so a client script can hand it back to the game itself
# (dml_steam_env_restore) instead of quietly disabling the Steam overlay.
#
# UNSETTING THE VARIABLE IS ONLY HALF THE FIX, and this is the part that is easy
# to get wrong: by the time the script runs, the shim is already MAPPED into
# this shell — Steam exec'd us with it. Clearing the variable stops the next
# exec from loading it, but it cannot unload what is already there, so every
# forked-but-not-exec'd subshell keeps running its destructor and printing the
# "skipping destruction" line. Measured: unset alone silences the ELFCLASS32
# flood and none of the other. So the shell re-execs itself once, now that the
# variable is gone, into a process that never loaded the shim at all.
#
# The re-exec needs no guard flag: LD_PRELOAD is unset in the child, so the
# second pass falls straight through. Everything above the `source` line in the
# calling script therefore runs twice — keep the source at the very top.
#
# Scripts that do NOT source this library (stop scripts, client scripts) carry
# the same lines inline — see ~/DML-LAUNCHER-STANDARD.md.
dml_steam_env_scrub() {
    DML_STEAM_LD_PRELOAD="${DML_STEAM_LD_PRELOAD-${LD_PRELOAD-}}"
    export DML_STEAM_LD_PRELOAD
    # Written as if-blocks, not `test && cmd`: a failing && list is the last
    # command here, and under `set -e` that would take the caller down with it.
    if [ -n "${LD_PRELOAD-}" ]; then
        unset LD_PRELOAD
        # $0 is not always a file (bash -c, stdin); only re-exec when it is.
        if [ -f "$0" ]; then
            exec "${BASH:-/bin/bash}" "$0" "$@"
        fi
    fi
    return 0
}

# Put the overlay back, for the one command that should have it: the game.
#   dml_steam_env_restore
#   exec "$PROTON" run "$GAME_EXE"
dml_steam_env_restore() {
    if [ -n "${DML_STEAM_LD_PRELOAD-}" ]; then
        export LD_PRELOAD="$DML_STEAM_LD_PRELOAD"
    fi
    return 0
}

# Applied on source. Inside a sourced file "$@" is the calling script's own
# positional parameters (bash keeps them when `source` is given no extra args),
# so the re-exec preserves whatever arguments the launcher was called with.
dml_steam_env_scrub "$@"

# ── Appearance ────────────────────────────────────────────────────────────────
DML_GOLD=$'\033[38;5;220m'; DML_DIM=$'\033[2m'; DML_BOLD=$'\033[1;37m\033[1m'
DML_GREEN=$'\033[0;32m'; DML_RED=$'\033[0;31m'; DML_YELLOW=$'\033[1;33m'
DML_OFF=$'\033[0m'
DML_BAR=" ══════════════════════════════════════════════════════════════════════════════════"
DML_RULE="━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

DML_GAME="game"
DML_LOG="/tmp/dml-launcher.log"

dml_init() {
    DML_GAME="$1"
    DML_LOG="${2:-/tmp/dml-launcher.log}"
    mkdir -p "$(dirname "$DML_LOG")" 2>/dev/null
    printf '=== %s launcher %s ===\n' "$DML_GAME" "$(date)" >>"$DML_LOG"
}

dml_log()  { printf '%s  %s\n' "$(date +%H:%M:%S)" "$*" >>"$DML_LOG"; }
dml_ok()   { echo -e "  ${DML_GREEN}✅ $*${DML_OFF}"; dml_log "OK: $*"; }
dml_warn() { echo -e "  ${DML_YELLOW}⚠️  $*${DML_OFF}"; dml_log "WARN: $*"; }
dml_note() { echo -e "  ${DML_DIM}$*${DML_OFF}"; }
dml_dot()  { printf "  ${DML_GOLD}.${DML_OFF}"; }

dml_header() {
    clear
    echo ""
    printf "%s%s%s\n" "$DML_GOLD" "$DML_BAR" "$DML_OFF"
    printf "   ${DML_DIM}Dad's MMO Lab${DML_OFF}  ✦  ${DML_DIM}%s${DML_OFF}\n" "$DML_GAME"
    printf "%s%s%s\n" "$DML_GOLD" "$DML_BAR" "$DML_OFF"
    echo ""
    echo -e "  ${DML_BOLD}Starting server...${DML_OFF}"
    echo ""
}

dml_phase() {
    echo ""
    printf "%s%s%s\n" "$DML_GOLD" "$DML_RULE" "$DML_OFF"
    echo -e "${DML_BOLD} $*${DML_OFF}"
    printf "%s%s%s\n" "$DML_GOLD" "$DML_RULE" "$DML_OFF"
    echo ""
}

dml_ready() {
    echo ""
    printf "%s%s%s\n" "$DML_GOLD" "$DML_RULE" "$DML_OFF"
    echo -e "${DML_GREEN}${DML_BOLD}  ✅ $*${DML_OFF}"
    printf "%s%s%s\n" "$DML_GOLD" "$DML_RULE" "$DML_OFF"
    echo ""
}

# There is deliberately NO dml_login_box helper here. Every pack ships THIS FILE to
# ~/dml-launcher-lib.sh, so restoring any game replaces it -- and a launcher for a
# different game, patched to call the helper and not itself re-restored, would then
# call a function that no longer exists. Login boxes are plain `echo` lines inside
# each launcher for that reason. See DML-LAUNCHER-STANDARD.md step 3b.

dml_footer() {
    echo ""
    printf "%s%s%s\n" "$DML_GOLD" "$DML_RULE" "$DML_OFF"
    echo -e "${DML_GREEN}${DML_BOLD}  ✅ $*${DML_OFF}"
    echo -e "  ${DML_DIM}Thanks for playing! youtube.com/@DadsMmoLab${DML_OFF}"
    printf "%s%s%s\n" "$DML_GOLD" "$DML_RULE" "$DML_OFF"
    echo ""
    sleep 5
}

# Fatal. In a terminal the printed message is enough; without a tty (someone
# pointed a Steam shortcut straight at the script, no konsole) fall back to
# zenity so it explains itself instead of silently bouncing to the library.
dml_die() {
    echo ""
    echo -e "  ${DML_RED}❌ $1${DML_OFF}"
    [ -n "${2:-}" ] && dml_note "$2"
    dml_note "Log: $DML_LOG"
    dml_log "FAIL: $1 ${2:-}"
    if [ ! -t 1 ] && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v zenity >/dev/null; then
        zenity --error --title="$DML_GAME" --width=420 --timeout=20 \
               --text="$1

${2:-}" >/dev/null 2>&1 &
        wait $! 2>/dev/null
    fi
    sleep 10
    exit 1
}

# ── Waiting ───────────────────────────────────────────────────────────────────
# dml_wait_until <tries> <sleep_secs> <command...>
# Runs the command until it succeeds, printing a dot per attempt. Returns 0/1.
dml_wait_until() {
    local tries="$1" gap="$2"; shift 2
    local i
    for (( i = 0; i < tries; i++ )); do
        if "$@"; then
            [ "$i" -gt 0 ] && echo ""
            return 0
        fi
        dml_dot
        sleep "$gap"
    done
    echo ""
    return 1
}

# ── The client watch ──────────────────────────────────────────────────────────
# dml_client_running <process_name> [more_names...]
# True while ANY of the named processes is running.
#
# Deliberately `pgrep -x` (exact process name), never a `-f` pattern. A -f
# pattern matches the launcher's own command line — the self-match trap that has
# bitten the FFXI and Tortoise launchers, and that killed an SSH session during
# the Eden Eternal port. Note that `-x` matches comm, which the kernel truncates
# to 15 characters: a longer .exe name can never match, so name the real process,
# not the file on disk.
#
# Several names matter for clients that HAND OFF. RF Online is the case that
# forced this: Amazing-CCR.exe is only the login/patch launcher, and it exits the
# moment it starts the game (RF.exe). Watching the launcher alone reads that
# handoff as "the player quit" and stops the server the instant the game
# actually starts. Track the whole chain, launcher and game together.
dml_client_running() {
    local p
    for p in "$@"; do
        pgrep -x "$p" >/dev/null 2>&1 && return 0
    done
    return 1
}

# dml_await_and_track <client_process_names> <friendly_name> <stop_function>
#                     [wait_minutes] [idle_hours]
#
# Steps 4-7 of the lifecycle. Waits for the client to appear, stays alive while
# it runs, then calls <stop_function> to bring the server down.
#
# <client_process_names> is one name, or several space-separated ones for a
# client that hands off from a launcher to the game itself (see
# dml_client_running). The watch holds while ANY of them is alive. Names may not
# contain spaces — the list is split on whitespace.
#
# If the client never appears the server is NOT torn down immediately — the
# player may be doing something else, and a launcher that killed the server after
# five quiet minutes would be worse than useless. It holds for <idle_hours> and
# then tidies up, matching MHF's behaviour.
dml_await_and_track() {
    local procs="$1" friendly="$2" stop_fn="$3"
    local wait_minutes="${4:-5}" idle_hours="${5:-3}"

    echo -e "  ${DML_BOLD}Press STEAM and launch ${friendly}${DML_OFF}"
    dml_note "This window keeps the server alive — leave it open."
    dml_note "The server shuts down automatically when you close the game."
    echo ""

    # $procs is deliberately unquoted everywhere below: it is a name LIST.
    local found=0 i
    for (( i = 0; i < wait_minutes * 12; i++ )); do
        # shellcheck disable=SC2086
        if dml_client_running $procs; then found=1; break; fi
        dml_dot
        sleep 5
    done
    echo ""

    if [ "$found" = 0 ]; then
        # Not a teardown, and not a blind 3-hour sleep either. A first client
        # launch can spend minutes in Proton prefix setup before its process
        # exists, and a friend has to get from this window to the Steam tile;
        # Deck #2 (2026-09-04) saw Wow.exe appear after the window closed and
        # the server sat running for the full idle hold. Keep looking.
        dml_warn "No client detected yet"
        dml_note "Still watching for ${friendly} for up to ${idle_hours}h — launch it whenever you like."
        dml_note "Close this window any time to leave the server running."
        local waited=0
        while [ "$waited" -lt $(( idle_hours * 3600 )) ]; do
            sleep 15; waited=$(( waited + 15 ))
            # shellcheck disable=SC2086
            if dml_client_running $procs; then found=1; break; fi
        done
    fi

    if [ "$found" = 1 ]; then
        dml_log "client detected ($procs)"
        echo -e "  ${DML_GREEN}⚔️  ${friendly} detected — have fun!${DML_OFF}"
        echo ""
        # A client that is quitting can briefly vanish and respawn: a launcher
        # handoff (RF Online's Amazing-CCR.exe exits as it starts RF.exe) and a
        # relaunch-into-fullscreen both look exactly like a quit for a moment.
        # Only believe it once the whole set has stayed gone for the full grace
        # period, and go back to watching if anything comes back.
        local gone
        while :; do
            # shellcheck disable=SC2086
            while dml_client_running $procs; do sleep 3; done
            for (( gone = 0; gone < 4; gone++ )); do
                sleep 5
                # shellcheck disable=SC2086
                dml_client_running $procs && break
            done
            # shellcheck disable=SC2086
            dml_client_running $procs || break
            dml_log "client reappeared; resuming watch"
        done
        echo -e "  ${DML_YELLOW}${friendly} closed — shutting the server down...${DML_OFF}"
        dml_log "client exited; stopping server"
    else
        echo -e "  ${DML_YELLOW}Idle timeout — shutting the server down...${DML_OFF}"
        dml_log "idle timeout; stopping server"
    fi

    "$stop_fn"
}
