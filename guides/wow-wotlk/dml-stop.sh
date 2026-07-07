#!/usr/bin/env bash
# Dad's MMO Lab — friendly stop for AzerothCore + Playerbots
# Called by: dml stop wow-server-playerbots
set -euo pipefail

SERVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SERVER_DIR"

COMPOSE_PROJECT="$(basename "$SERVER_DIR")"
DB_VOLUME="${COMPOSE_PROJECT}_ac-database"
DML_QUIET_COMPOSE="docker-compose.dml-quiet.yml"
DML_WIN_ROOT="${DML_WIN_ROOT:-/mnt/c/DML}"
DML_BUSY_MARKER=".dml-lifecycle-busy"

_log() { echo "[dml] $*"; }

_log_ok() {
  if [[ -t 1 ]]; then
    echo -e "\033[32m[dml] $*\033[0m"
  else
    _log "$*"
  fi
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

_release_wsl_windows() {
  local delay="${1:-3}"
  command -v powershell.exe &>/dev/null || return 0
  local ps1="${DML_WIN_ROOT}/DML-Release-WSL.ps1"
  [[ -f "$ps1" ]] || return 0
  # Must be a real Windows path (C:\...) for -File to resolve; a naive slash
  # swap on the /mnt/c/... form yields \mnt\c\... which PowerShell can't find.
  local win_ps1
  win_ps1="$(wslpath -w "$ps1" 2>/dev/null)" || win_ps1="${ps1//\//\\}"
  powershell.exe -NoProfile -WindowStyle Hidden -Command \
    "Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-WindowStyle','Hidden','-File','${win_ps1}','-DelaySeconds','${delay}') -WindowStyle Hidden" \
    2>/dev/null || true
}

_close_prompt() {
  local ans
  echo ""
  if [[ -t 0 ]]; then
    read -r -p "[dml] Close this window? [Y/N]: " ans || ans=""
    case "${ans,,}" in
      y|yes)
        _log "Closing — your server data is saved. Use Start when you want to play again."
        exit 0
        ;;
    esac
  fi

  _log "Keeping window open — server is stopped; data is on disk. Type 'exit' when done."
  exec bash -l </dev/tty >/dev/tty 2>/dev/null || sleep infinity
}

touch "$DML_BUSY_MARKER"
trap 'rm -f "$DML_BUSY_MARKER"' EXIT

_log "Stopping wow-server-playerbots..."
_log "This stops running containers only — your database, characters, and bot progress stay saved on disk."

if _has_persisted_data; then
  _log "Found existing data volume (${DB_VOLUME}) — nothing is being deleted."
else
  _log "No data volume yet (fresh install)."
fi

_log "Shutting down containers quietly..."
if ! _dml_compose down >"$SERVER_DIR/.dml-stop.log" 2>&1; then
  echo "[dml] ERROR: Stop failed — see $SERVER_DIR/.dml-stop.log" >&2
  tail -20 "$SERVER_DIR/.dml-stop.log" >&2 || true
  exit 1
fi

echo ""
_log_ok "wow-server-playerbots stopped"
_log_ok "All progress preserved — database and client data remain in Docker volumes"
_log_ok "Next Start will bring everything back (no re-import needed)"

if [[ "$(docker ps -q 2>/dev/null | wc -l | tr -d '[:space:]')" -eq 0 ]]; then
  echo ""
  _log "No servers running — releasing WSL memory to Windows..."
  _log "WSL is shutting down to free that RAM -- this window will close on its own in a few seconds."
  _log "You may see \"process exited\" and a prompt to restart -- there's nothing to restart, just close the window."
  _release_wsl_windows 8
  rm -f "$DML_BUSY_MARKER"
  exit 0
fi

rm -f "$DML_BUSY_MARKER"
_close_prompt
exit 0
