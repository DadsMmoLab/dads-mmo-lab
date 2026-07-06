#!/usr/bin/env bash
# DML quiet entrypoint — same as AzerothCore entrypoint.sh but without cp -n portability warnings.
set -euo pipefail

CONF_DIR="${CONF_DIR:-/azerothcore/env/dist/etc}"
LOGS_DIR="${LOGS_DIR:-/azerothcore/env/dist/logs}"

if ! touch "$CONF_DIR/.write-test" 2>/dev/null || ! touch "$LOGS_DIR/.write-test" 2>/dev/null; then
  cat <<EOF
===== WARNING =====
The current user doesn't have write permissions for
the configuration dir ($CONF_DIR) or logs dir ($LOGS_DIR).
It's likely that services will fail due to this.
====================
EOF
fi

[[ -f "$CONF_DIR/.write-test" ]] && rm -f "$CONF_DIR/.write-test"
[[ -f "$LOGS_DIR/.write-test" ]] && rm -f "$LOGS_DIR/.write-test"

if compgen -G "/azerothcore/env/ref/etc/*" >/dev/null; then
  cp -ru --update=none /azerothcore/env/ref/etc/* "$CONF_DIR" 2>/dev/null \
    || cp -ru /azerothcore/env/ref/etc/* "$CONF_DIR" 2>/dev/null \
    || true
fi

CONF="$CONF_DIR/$ACORE_COMPONENT.conf"
CONF_DIST="$CONF_DIR/$ACORE_COMPONENT.conf.dist"

if [[ -f "$CONF_DIST" ]]; then
  cp --update=none "$CONF_DIST" "$CONF" 2>/dev/null \
    || cp -n "$CONF_DIST" "$CONF" 2>/dev/null \
    || true
else
  touch "$CONF"
fi

echo "Starting $ACORE_COMPONENT..."

exec "$@"