#!/usr/bin/env bash
# Watches press 2's transcript for the first ninja edge >= TARGET, then SIGKILLs the whole
# gate72-press2 unit (driver, harness, compose client) and writes down what that left behind.
#
# Every pgrep pattern is bracketed ([y]ulon) so it cannot match the shell running it: the
# 09-04 kill record (pyplan/gates/7.1-ubuntu-2026-09-04/kill-record.txt) matched its own
# recorder, killed it, and lost the tail of its own evidence. This file is a script on disk
# rather than a `bash -c` string for the same reason: the patterns are not in any argv.
TARGET=${1:-900}
LOG=$HOME/gate72-press2.log
OUT=$HOME/gate72-kill-record.txt

while true; do
  edge=$(grep -oE '\[[0-9]+/1829\]' "$LOG" 2>/dev/null | tail -1 | tr -dc '0-9/' | cut -d/ -f1)
  if [ -n "$edge" ] && [ "$edge" -ge "$TARGET" ]; then break; fi
  sleep 2
done

{
  echo "=== mid-build SIGKILL of press 2, lane gate-71-72, $(date -Is)"
  echo "--- trigger: first edge >= $TARGET seen in $LOG (polled every 2 s)"
  echo "--- the last edge line in the log at the moment of the kill"
  grep -E '\[[0-9]+/1829\]' "$LOG" | tail -1
  echo "--- Building edges reported so far"
  grep -cE '\[[0-9]+/1829\] Building' "$LOG"
  echo "--- the unit's own process tree before the kill"
  systemctl --user status gate72-press2 --no-pager 2>&1 | sed -n '1,30p'
  echo "--- compiler processes on the host (BuildKit runs them in a container, but ps sees them)"
  echo "cc1plus/ccache/ninja: $(pgrep -c -f '[c]c1plus|[c]cache|[n]inja')"
  echo "--- kill: systemctl --user kill --signal=SIGKILL gate72-press2 at $(date -Is)"
  systemctl --user kill --signal=SIGKILL gate72-press2; echo "exit $?"
  sleep 3
  echo "--- unit state after"
  systemctl --user show gate72-press2 -p ActiveState -p SubState -p Result -p ExecMainStatus -p ExecMainCode
  echo "--- survivors (bracketed patterns, so this recorder cannot match itself)"
  pgrep -af '[y]ulon.install_wiring' || echo 'no install_wiring process'
  pgrep -af '[p]ress-driver' || echo 'no press-driver process'
  pgrep -af '[b]uildx' || echo 'no buildx process'
  pgrep -af '[d]ocker compose' || echo 'no compose client process'
  pgrep -af '[s]ystemd-inhibit' || echo 'no systemd-inhibit left behind'
  echo "--- does the daemon keep compiling after its client is gone? (sampled every 5 s for 60 s)"
  for i in $(seq 1 12); do
    echo "$(date -Is) containers=$(docker ps -q | wc -l) compiler-processes=$(pgrep -c -f '[c]c1plus|[c]cache|[n]inja')"
    sleep 5
  done
  echo "--- docker ps -a"
  docker ps -a
  echo "--- last line of the log"
  tail -1 "$LOG"
  echo "--- install state file after the kill"
  cat "$HOME/wowserver/.yulon-install.json"
  echo "--- df -h ~"
  df -h ~
} > "$OUT" 2>&1

"$HOME/ccache-probe.sh" "T1: after the mid-build SIGKILL of press 2 (edge >= $TARGET)"
echo "$(date +%H:%M:%S)  gate-71-72: press 2 SIGKILLed mid-build (edge >= $TARGET); ~/gate72-kill-record.txt; ccache T1 taken" >> "$HOME/claude-activity.log"
