#!/usr/bin/env bash
# Returns 0 when all playerbots have logged in for the CURRENT worldserver run.
set -euo pipefail
WORLD_CONTAINER="${1:-ac-worldserver}"
_clean() { sed 's/\x1b\[[0-9;]*[a-zA-Z]//g' | tr -d '\r'; }

docker ps --format '{{.Names}}' | grep -qx "$WORLD_CONTAINER" || exit 1

started_at=$(docker inspect "$WORLD_CONTAINER" --format '{{.State.StartedAt}}' 2>/dev/null || true)
[[ -n "$started_at" && "$started_at" != "0001-01-01T00:00:00Z" ]] || exit 1

# Only this container instance — ignore 564/564 lines left over from before a restart
logs=$(docker logs --since "$started_at" "$WORLD_CONTAINER" 2>&1 | _clean)
echo "$logs" | grep -q 'Random Bots Stats:' && exit 0
line=$(echo "$logs" | grep -E '[0-9]+/[0-9]+ Bot .+ logged in' | tail -1 || true)
[[ "$line" =~ ([0-9]+)/([0-9]+)[[:space:]]Bot ]] || exit 1
[[ "${BASH_REMATCH[1]}" == "${BASH_REMATCH[2]}" && "${BASH_REMATCH[2]}" -gt 0 ]]