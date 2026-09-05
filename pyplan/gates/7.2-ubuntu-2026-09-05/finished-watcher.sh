#!/usr/bin/env bash
# Cycle 2, press B (the resume on the throwaway folder): let the build stage run to its end,
# then SIGKILL the unit the moment the engine prints "The build finished." -- the next stages
# would create ac-database / ac-authserver / ac-worldserver for a THROWAWAY install, and those
# container names belong to the real one at ~/wowserver. Then the T2 reading of the ccache mount.
UNIT=${UNIT:?}
LOG=${LOG:?}
OUT=${OUT:?}
LABEL=${PROBE_LABEL:-cycle2 T2: after press B finished the build}
while ! grep -q "^The build finished" "$LOG" 2>/dev/null; do sleep 2; done
{
  echo "=== press B: build finished; SIGKILL before the next stage, $(date -Is)"
  echo "--- last stage lines"
  grep -n "^--- \|^Step \|^The build finished" "$LOG" | tail -6
  echo "--- kill: systemctl --user kill --signal=SIGKILL $UNIT at $(date -Is)"
  systemctl --user kill --signal=SIGKILL "$UNIT"; echo "exit $?"
  sleep 3
  systemctl --user show "$UNIT" -p ActiveState -p Result -p ExecMainStatus
  echo "--- survivors"
  pgrep -af '[y]ulon.install_wiring' || echo 'no install_wiring process'
  pgrep -af '[s]ystemd-inhibit' || echo 'no systemd-inhibit left behind'
  echo "--- containers (the throwaway must not have started any)"
  docker ps -a --format '{{.Names}} {{.Image}} {{.Status}}'
  echo "--- images (the throwaway's four carry a different install id)"
  docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}' | grep native
} > "$OUT" 2>&1
"$HOME/ccache-probe.sh" "$LABEL"
echo "$(date +%H:%M:%S)  gate-71-72: cycle 2 press B build finished and unit killed before start-db; T2 taken" >> "$HOME/claude-activity.log"
