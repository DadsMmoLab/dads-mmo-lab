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

if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  source .env 2>/dev/null || true
  DB_PASSWORD="${DOCKER_DB_ROOT_PASSWORD:-$DB_PASSWORD}"
fi

_log() { echo "[dml] $*"; }

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

_wait_ready() {
  local i
  for i in $(seq 1 240); do
    if docker ps --format '{{.Names}}' | grep -qx "$AUTH_CONTAINER" \
       && docker ps --format '{{.Names}}' | grep -qx "$WORLD_CONTAINER"; then
      if docker logs "$AUTH_CONTAINER" 2>&1 | grep -q "${REALM_ADDRESS}:8085"; then
        if docker logs "$WORLD_CONTAINER" 2>&1 | grep -q 'ready\.\.\.'; then
          return 0
        fi
      fi
    fi
    sleep 2
  done
  return 1
}

_start_auth_world() {
  # Use docker start (not compose up) so we do NOT re-trigger ac-db-import
  # or ac-client-data-init on every restart — that was killing the database.
  if docker ps -a --format '{{.Names}}' | grep -qx "$AUTH_CONTAINER" \
     && docker ps -a --format '{{.Names}}' | grep -qx "$WORLD_CONTAINER"; then
    docker start "$DB_CONTAINER" 2>/dev/null || true
    _wait_db_healthy || { echo "[dml] ERROR: Database not healthy" >&2; exit 1; }
    _pin_realm_local
    _log "Starting auth + world (direct)..."
    docker start "$AUTH_CONTAINER" "$WORLD_CONTAINER"
  else
    _log "First boot — running docker compose up..."
    docker compose up -d "$AUTH_CONTAINER" "$WORLD_CONTAINER"
  fi
}

_ensure_playerbots_conf

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
    _log "Bringing up database (first install)..."
    docker compose up -d "$DB_CONTAINER"
  fi
fi

_log "Waiting for database..."
if ! _wait_db_healthy; then
  echo "[dml] ERROR: Database did not become healthy in time" >&2
  exit 1
fi

_start_auth_world

_log "Waiting for AzerothCore (playerbots first boot can take several minutes)..."
if _wait_ready; then
  _log "wow-server-playerbots is ready"
  _log "Realm: AzerothCore at ${REALM_ADDRESS}:8085"
  _log "Login: admin / admin"
  docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | grep -E 'NAMES|ac-'
  exit 0
fi

echo "[dml] WARN: Timed out waiting for ready — server may still be starting" >&2
docker ps --format 'table {{.Names}}\t{{.Status}}'
exit 1