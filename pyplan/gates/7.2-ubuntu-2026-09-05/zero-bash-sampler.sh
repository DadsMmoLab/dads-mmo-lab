#!/usr/bin/env bash
# Every 15 s: any process whose argv names a bash-lineage script, any process running a .sh file at all,
# and whether the bash installer's own log file exists. Patterns are bracketed so this sampler never matches itself.
while true; do
  ts=$(date -Is)
  lineage=$(pgrep -af "[i]nstall-[a-z-]*\.sh|[d]ml-start\.sh|[w]ow-manage\.sh" || true)
  anysh=$(ps -eo pid,args | grep -E "[/ ][A-Za-z0-9_.-]+\.sh( |$)" | grep -v "zero-bash-sampler" || true)
  logs=$(ls ~/dads-mmo-lab-install-*.log 2>/dev/null | wc -l)
  echo "$ts lineage=[${lineage:-none}] any-sh=[${anysh:-none}] install-logs=$logs"
  sleep 15
done
