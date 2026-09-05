#!/usr/bin/env bash
# usage: ccache-probe.sh "<label>"   -- appends one ccache -s reading to ~/gate72-ccache-stats.txt
{ echo "=== ccache -s at $(date -Is) : $1"; docker build --no-cache --progress plain ~/ccache-probe 2>&1 | grep -E "^#[0-9]+ [0-9.]+ |^#[0-9]+ DONE" | grep -E "ccache|Hits|Misses|Cacheable|Cache size|Local storage|du|[0-9]+M|[0-9]+K|DONE" | tail -14; echo; } >> ~/gate72-ccache-stats.txt
