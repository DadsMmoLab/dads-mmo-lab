#!/usr/bin/env bash
# 7.10 cross-server regression sweep on yulon-ubuntu.
# Drives the 2026-08-28 sweep drivers, adjusted only in their two path constants,
# against the WotLK server the 7.1 lane left running at ~/wowserver.
set -u
OUT=/home/pk/gate710
mkdir -p "$OUT"
PY=/home/pk/gate0904/venv/bin/python
DRV=/home/pk/gate710/drivers

say() { command -v claude-say >/dev/null 2>&1 && claude-say "$1" >/dev/null 2>&1; printf '%s %s\n' "$(date -Is)" "$1" >> /home/pk/claude-activity.log; }

probe() {  # probe <file-tag>
  local f="$OUT/state-$1.txt"
  {
    echo "=== state-$1 ==="
    echo "taken (local): $(date -Is)"
    echo "--- docker ps ---"
    docker ps --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'
    echo "--- listening 3724/8085/3306 ---"
    ss -ltnp 2>/dev/null | grep -E ':(3724|8085|3306)\b' || echo "(none)"
    echo "--- schema table counts (auth/characters/world/playerbots) ---"
    docker exec ac-database mysql -uroot -ppassword -N -B -e \
      "SELECT table_schema, COUNT(*) FROM information_schema.tables WHERE table_schema LIKE 'acore%' GROUP BY table_schema ORDER BY table_schema;" 2>/dev/null || echo "(query failed)"
    echo "--- gate account row ---"
    docker exec ac-database mysql -uroot -ppassword -N -B -e \
      "SELECT id, username FROM acore_auth.account;" 2>/dev/null || echo "(query failed)"
    echo "--- realmlist ---"
    docker exec ac-database mysql -uroot -ppassword -N -B -e \
      "SELECT id, name, address, localAddress, port FROM acore_auth.realmlist;" 2>/dev/null || echo "(query failed)"
    echo "--- ufw ---"
    sudo ufw status numbered 2>&1
    echo "--- disk ---"
    df -h /home/pk | tail -1
    echo "--- install state ---"
    cat /home/pk/wowserver/.yulon-install.json 2>/dev/null || echo "(no state file)"
  } > "$f" 2>&1
  echo "wrote $f"
}

echo "=== 7.10 sweep started $(date -Is) ===" > "$OUT/run.log"
{
  echo "code under test : /home/pk/gate0904/checkout at $(git -C /home/pk/gate0904/checkout rev-parse HEAD)"
  echo "interpreter     : $PY  ($($PY --version 2>&1))"
  echo "server dir      : /home/pk/wowserver"
  echo "drivers         : $DRV (2026-08-28 originals, two path constants changed - see drivers.diff)"
} >> "$OUT/run.log"

say "lane 7.10: capturing server state before the sweep"
probe before

for n in "" 2 3 4; do
  d="$DRV/sweep_driver${n}.py"
  log="$OUT/sweep${n:-1}.log"
  say "lane 7.10: running $(basename "$d") against the live WotLK server"
  {
    echo "=== $(basename "$d") ==="
    echo "started (local): $(date -Is)"
    echo "command: $PY $d"
    echo "---"
  } > "$log"
  t0=$(date +%s)
  "$PY" "$d" >> "$log" 2>&1
  rc=$?
  t1=$(date +%s)
  echo "--- exit code: $rc   elapsed: $((t1-t0))s   finished: $(date -Is)" >> "$log"
  echo "$(basename "$d") exit $rc  $((t1-t0))s" >> "$OUT/run.log"
  if [ "$n" = "3" ]; then
    say "lane 7.10: restore driver finished (exit $rc); waiting 180s for the stack to come back"
    sleep 180
    probe after-restore
  fi
done

say "lane 7.10: capturing server state after the sweep"
probe after
echo "=== 7.10 sweep finished $(date -Is) ===" >> "$OUT/run.log"
