#!/usr/bin/env bash
# Press 2 (cold ccache, SIGKILLed) against press 3 (the resume): the BuildKit stage clock
# (`#NN <seconds>`) at the same ninja edges, read off the two transcripts. The 09-04 record
# (pyplan/gates/7.1-ubuntu-2026-09-04/ccache-recovery.txt) is the shape this reproduces;
# there the resume replayed 881 edges in ~16 s. Whatever the numbers say here is the verdict.
P2=${1:-$HOME/gate72-press2.log}
P3=${2:-$HOME/gate72-press3.log}
total=$(grep -oE '\[[0-9]+/[0-9]+\]' "$P2" | tail -1 | cut -d/ -f2 | tr -d ']')
echo "=== edge rate, press 2 vs press 3, $(date -Is); edges total ${total}"
clock() { grep -oE "^#[0-9]+ [0-9.]+ \[$2/${total}\]" "$1" | tail -1 | awk '{print $2}'; }
echo "--- BuildKit stage clock (s) at the same edge"
printf "%-8s %-14s %-14s\n" edge "press2(cold)" "press3(resume)"
for e in 1 100 200 300 400 500 600 700 800 900 1000 1100 1200 1226 1300 1400 1500 1600 1700 1800 ${total}; do
  printf "%-8s %-14s %-14s\n" "$e" "$(clock "$P2" "$e")" "$(clock "$P3" "$e")"
done
echo "--- the edge press 2 died at, and its clock"
grep -oE "^#[0-9]+ [0-9.]+ \[[0-9]+/${total}\]" "$P2" | tail -1
echo "--- press 3: first edge and last edge, with clocks"
grep -oE "^#[0-9]+ [0-9.]+ \[[0-9]+/${total}\]" "$P3" | head -1
grep -oE "^#[0-9]+ [0-9.]+ \[[0-9]+/${total}\]" "$P3" | tail -1
echo "--- press 3: Building edges reported (a replay from ccache still reports every edge)"
grep -cE "\[[0-9]+/${total}\] Building" "$P3"
echo "--- press 3: the engine's own line, if it skipped the build (a re-press of a COMPLETE install says this; a resume after a kill does not)"
grep -n "already built" "$P3" || echo "(not said: the build step was re-entered)"
echo "--- wall clock of the build step on each press, from the harness's own INFO stamps"
for f in "$P2" "$P3"; do
  echo "$f"
  grep -E "INFO \[yulon.docker\] build_staged\(\)" "$f" | cut -c1-19
  grep -nE "^The build finished|DONE [0-9.]+s$" "$f" | tail -1 | cut -c1-80
  grep -E "^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9:]{8} INFO" "$f" | grep -A1 "build_staged" | tail -1 | cut -c1-120
done
