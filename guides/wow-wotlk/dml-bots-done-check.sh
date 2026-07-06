#!/usr/bin/env bash
# Returns 0 when all playerbots have logged in (or stats line seen).
set -euo pipefail
WORLD_CONTAINER="${1:-ac-worldserver}"
_clean() { sed 's/\x1b\[[0-9;]*[a-zA-Z]//g' | tr -d '\r'; }
logs=$(docker logs --tail 500 "$WORLD_CONTAINER" 2>&1 | _clean)
echo "$logs" | grep -q 'Random Bots Stats:' && exit 0
line=$(echo "$logs" | grep -E '[0-9]+/[0-9]+ Bot .+ logged in' | tail -1 || true)
[[ "$line" =~ ([0-9]+)/([0-9]+)[[:space:]]Bot ]] || exit 1
[[ "${BASH_REMATCH[1]}" == "${BASH_REMATCH[2]}" && "${BASH_REMATCH[2]}" -gt 0 ]]