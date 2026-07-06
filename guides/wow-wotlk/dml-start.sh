#!/usr/bin/env bash
# Dad's MMO Lab — staged start/restart for AzerothCore + Playerbots
# Called by: dml start|restart wow-server-playerbots
set -euo pipefail

MODE="${1:-start}"
SERVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SERVER_DIR"

REALM_ADDRESS="${DML_REALM_ADDRESS:-127.0.0.1}"
DB_CONTAINER="${DML_DB_CONTAINER:-ac-database}"
AUTH_CONTAINER="${DML_AUTH_CONTAINER:-ac-authserver}"
WORLD_CONTAINER="${DML_WORLD_CONTAINER:-ac-worldserver}"
DB_PASSWORD="${DOCKER_DB_ROOT_PASSWORD:-password}"
COMPOSE_PROJECT="$(basename "$SERVER_DIR")"
DB_VOLUME="${COMPOSE_PROJECT}_ac-database"
DML_QUIET_COMPOSE="docker-compose.dml-quiet.yml"
DML_ENTRYPOINT="dml-docker-entrypoint.sh"
DML_QUIET_MARKER=".dml-quiet-applied"
DML_BUSY_MARKER=".dml-lifecycle-busy"

if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  source .env 2>/dev/null || true
  DB_PASSWORD="${DOCKER_DB_ROOT_PASSWORD:-$DB_PASSWORD}"
fi

_log() { echo "[dml] $*"; }

_log_ok() {
  if [[ -t 1 ]]; then
    echo -e "\033[32m[dml] $*\033[0m"
  else
    _log "$*"
  fi
}

_log_tail_pid=""

_stop_log_tail() {
  if [[ -n "${_log_tail_pid:-}" ]] && kill -0 "$_log_tail_pid" 2>/dev/null; then
    kill "$_log_tail_pid" 2>/dev/null || true
    wait "$_log_tail_pid" 2>/dev/null || true
  fi
  _log_tail_pid=""
}

_dml_compose() {
  local args=(-f docker-compose.yml)
  [[ -f docker-compose.override.yml ]] && args+=(-f docker-compose.override.yml)
  [[ -f "$DML_QUIET_COMPOSE" ]] && args+=(-f "$DML_QUIET_COMPOSE")
  docker compose "${args[@]}" "$@"
}

_has_persisted_data() {
  docker volume inspect "$DB_VOLUME" &>/dev/null
}

_ensure_quiet_startup() {
  local bundled ep="$SERVER_DIR/$DML_ENTRYPOINT" qc="$SERVER_DIR/$DML_QUIET_COMPOSE"
  bundled="$(dirname "${BASH_SOURCE[0]}")/$DML_ENTRYPOINT"

  if [[ -f "$bundled" ]]; then
    if [[ "$bundled" -ef "$ep" ]]; then
      chmod +x "$ep"
    else
      cp "$bundled" "$ep"
      chmod +x "$ep"
    fi
  fi

  if [[ ! -f "$qc" ]]; then
    cat > "$qc" << 'EOF'
# DML overlay — quiet startup (no cp -n warnings). Merged by dml-start.sh.
services:
  ac-worldserver:
    entrypoint: ["/bin/bash", "/azerothcore/dml-docker-entrypoint.sh"]
    command: ["worldserver"]
    volumes:
      - ./dml-docker-entrypoint.sh:/azerothcore/dml-docker-entrypoint.sh:ro
    environment:
      AC_PROCESS_PRIORITY: "0"
  ac-authserver:
    entrypoint: ["/bin/bash", "/azerothcore/dml-docker-entrypoint.sh"]
    command: ["authserver"]
    volumes:
      - ./dml-docker-entrypoint.sh:/azerothcore/dml-docker-entrypoint.sh:ro
    environment:
      AC_PROCESS_PRIORITY: "0"
EOF
  fi

  local ws_conf="env/dist/etc/worldserver.conf"
  if [[ -f "$ws_conf" ]] && grep -q '^ProcessPriority = 1' "$ws_conf"; then
    sed -i 's/^ProcessPriority = 1/ProcessPriority = 0/' "$ws_conf"
  fi

  if [[ ! -f "$DML_QUIET_MARKER" && -f "$qc" && -f "$ep" ]]; then
    touch "$DML_QUIET_MARKER"
    export DML_QUIET_MIGRATE=1
  fi
}

_filter_world_logs() {
  grep --line-buffered -v -E \
    '^cp: warning: behavior of -n is non-portable' \
    | grep --line-buffered -v -E \
    "Can't set process priority class|MoveSplineInitArgs::Validate|WaypointMovementGenerator::DoInitialize"
}

_ensure_playerbots_conf() {
  local conf="env/dist/etc/modules/playerbots.conf"
  local dist="env/dist/etc/modules/playerbots.conf.dist"
  if [[ ! -f "$conf" && -f "$dist" ]]; then
    cp "$dist" "$conf"
    _log "Created missing playerbots.conf from dist"
  fi
}

_pin_realm_local() {
  if docker ps --format '{{.Names}}' | grep -qx "$DB_CONTAINER"; then
    docker exec "$DB_CONTAINER" mysql -uroot -p"$DB_PASSWORD" -e \
      "UPDATE acore_auth.realmlist SET address='${REALM_ADDRESS}', localAddress='${REALM_ADDRESS}' WHERE id=1;" \
      2>/dev/null || true
  fi
}

_wait_db_healthy() {
  local i status
  for i in $(seq 1 90); do
    status=$(docker inspect "$DB_CONTAINER" --format '{{.State.Health.Status}}' 2>/dev/null || echo unknown)
    if [[ "$status" == "healthy" ]]; then
      return 0
    fi
    sleep 2
  done
  return 1
}

_is_ready() {
  local ok=1
  docker ps --format '{{.Names}}' | grep -qx "$AUTH_CONTAINER" \
    && docker ps --format '{{.Names}}' | grep -qx "$WORLD_CONTAINER" || return 1
  set +o pipefail
  docker logs "$AUTH_CONTAINER" 2>&1 | grep -m1 -q "${REALM_ADDRESS}:8085" \
    && docker logs "$WORLD_CONTAINER" 2>&1 | grep -m1 -q 'ready\.\.\.' && ok=0
  set -o pipefail
  [[ "$ok" -eq 0 ]]
}

_start_world_log_tail() {
  local i
  for i in $(seq 1 30); do
    if docker ps -a --format '{{.Names}}' | grep -qx "$WORLD_CONTAINER"; then
      docker logs -f "$WORLD_CONTAINER" 2>&1 | _filter_world_logs &
      _log_tail_pid=$!
      return 0
    fi
    sleep 1
  done
  return 1
}

_bots_are_done() {
  local line cur total
  set +o pipefail
  if docker logs --tail 50 "$WORLD_CONTAINER" 2>&1 | grep -q 'Random Bots Stats:'; then
    set -o pipefail
    return 0
  fi
  line=$(docker logs --tail 20 "$WORLD_CONTAINER" 2>&1 \
    | grep -E '[0-9]+/[0-9]+ Bot .+ logged in' | tail -1 || true)
  set -o pipefail
  if [[ "$line" =~ ^([0-9]+)/([0-9]+)\ Bot ]]; then
    cur="${BASH_REMATCH[1]}"
    total="${BASH_REMATCH[2]}"
    [[ "$cur" == "$total" && "$total" -gt 0 ]]
    return
  fi
  return 1
}

_wait_bots_populated() {
  local i
  _log "AzerothCore is ready — streaming playerbot logins until complete..."
  for i in $(seq 1 300); do
    if _bots_are_done; then
      return 0
    fi
    sleep 2
  done
  _log "WARN: Timed out waiting for all bots — server is still playable"
  return 0
}

_close_prompt() {
  local ans
  echo ""
  if [[ -t 0 ]]; then
    read -r -p "[dml] Close this window? [y/N]: " ans
    case "${ans,,}" in
      y|yes)
        _log "Closing — server keeps running in the background."
        exit 0
        ;;
    esac
  fi
  _log "Keeping window open — type 'exit' when you are done."
  exec bash -l
}

_wait_ready() {
  local i core_ok=0

  if _has_persisted_data; then
    _log "Waiting for AzerothCore ready (following worldserver logs; data volumes preserved)..."
  else
    _log "Waiting for AzerothCore ready (first boot — following worldserver logs; can take several minutes)..."
  fi

  _start_world_log_tail || _log "WARN: worldserver container not found yet — polling without live logs"

  for i in $(seq 1 240); do
    if _is_ready; then
      core_ok=1
      break
    fi
    sleep 2
  done

  if [[ "$core_ok" -ne 1 ]]; then
    _stop_log_tail
    return 1
  fi

  _wait_bots_populated
  _stop_log_tail
  return 0
}

_start_auth_world() {
  if docker ps -a --format '{{.Names}}' | grep -qx "$AUTH_CONTAINER" \
     && docker ps -a --format '{{.Names}}' | grep -qx "$WORLD_CONTAINER"; then
    docker start "$DB_CONTAINER" 2>/dev/null || true
    _wait_db_healthy || { echo "[dml] ERROR: Database not healthy" >&2; exit 1; }
    _pin_realm_local
    if [[ -n "${DML_QUIET_MIGRATE:-}" ]]; then
      _log "Refreshing auth + world for quiet startup (one-time)..."
      _dml_compose up -d --force-recreate --no-deps "$AUTH_CONTAINER" "$WORLD_CONTAINER"
    else
      _log "Starting auth + world (direct)..."
      docker start "$AUTH_CONTAINER" "$WORLD_CONTAINER"
    fi
  else
    if _has_persisted_data; then
      _log "Recreating auth + world containers (compose up; database and client data preserved)..."
    else
      _log "First boot — running docker compose up..."
    fi
    _dml_compose up -d "$AUTH_CONTAINER" "$WORLD_CONTAINER"
  fi
}

_ensure_quiet_startup
_ensure_playerbots_conf

touch "$DML_BUSY_MARKER"
trap 'rm -f "$DML_BUSY_MARKER"' EXIT

if [[ "$MODE" == "restart" ]]; then
  _log "Restarting WoW server (staged)..."
  docker stop "$AUTH_CONTAINER" "$WORLD_CONTAINER" 2>/dev/null || true
else
  _log "Starting WoW server (staged)..."
fi

if ! docker ps --format '{{.Names}}' | grep -qx "$DB_CONTAINER"; then
  if docker ps -a --format '{{.Names}}' | grep -qx "$DB_CONTAINER"; then
    _log "Starting existing database container..."
    docker start "$DB_CONTAINER"
  else
    if _has_persisted_data; then
      _log "Recreating database container (existing data volume preserved)..."
    else
      _log "Bringing up database (first install)..."
    fi
    _dml_compose up -d "$DB_CONTAINER"
  fi
fi

_log "Waiting for database..."
if ! _wait_db_healthy; then
  echo "[dml] ERROR: Database did not become healthy in time" >&2
  exit 1
fi

_start_auth_world

if _wait_ready; then
  echo ""
  _log_ok "wow-server-playerbots is ready"
  _log_ok "Realm: AzerothCore at ${REALM_ADDRESS}:8085"
  _log_ok "Login: admin / admin"
  docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | grep -E 'NAMES|ac-'
  _close_prompt
fi

echo "[dml] WARN: Timed out waiting for ready — server may still be starting" >&2
docker ps --format 'table {{.Names}}\t{{.Status}}'
exit 1