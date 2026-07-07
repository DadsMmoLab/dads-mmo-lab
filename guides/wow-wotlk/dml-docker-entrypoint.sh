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

# Materialize any *.conf.dist that doesn't have a matching *.conf yet -- this
# covers the main component conf below AND every module conf under modules/.
# Without this, a module added after the volume already existed (its
# .conf.dist lands via the ref/etc copy above, but nothing turns it into a
# real .conf) silently falls back to hardcoded defaults and spams "Missing
# property" warnings for every option it checks.
while IFS= read -r -d '' dist; do
  conf="${dist%.dist}"
  [[ -f "$conf" ]] && continue
  cp --update=none "$dist" "$conf" 2>/dev/null \
    || cp -n "$dist" "$conf" 2>/dev/null \
    || true
done < <(find "$CONF_DIR" -name '*.conf.dist' -print0 2>/dev/null)

CONF="$CONF_DIR/$ACORE_COMPONENT.conf"
[[ -f "$CONF" ]] || touch "$CONF"

echo "Starting $ACORE_COMPONENT..."

exec "$@"