#!/bin/bash
# Dad's MMO Lab — @TITLE@: stop the server Yu'lon installed in @SERVER_DIR@
# (the "Server Stop" tile). Stop on the server's tab in Yu'lon does the same: both run
# Yu'lon's own Stop, which keeps the containers, so the next Start is quick.
export DML_STEAM_LD_PRELOAD="${DML_STEAM_LD_PRELOAD-${LD_PRELOAD-}}"
if [ -n "${LD_PRELOAD-}" ]; then
    unset LD_PRELOAD
    [ -f "$0" ] && exec "${BASH:-/bin/bash}" "$0" "$@"
fi

YULON=(@YULON@)
GAME="@GAME@"
SERVER_DIR="@SERVER_DIR@"
TITLE="@TITLE@"

say() {   # visible both in a terminal and from a Gaming Mode tile
    if [ ! -t 1 ] && command -v zenity >/dev/null && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
        zenity --info --no-wrap --timeout=20 --title="$TITLE" --text="$*" 2>/dev/null
    else
        echo "$*"
    fi
}

out=$("${YULON[@]}" --server stop "$GAME" "$SERVER_DIR" 2>&1)
rc=$?
say "$(printf '%s\n' "$out" | tail -3)"
exit "$rc"
