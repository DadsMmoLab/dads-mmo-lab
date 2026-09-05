#!/usr/bin/env bash
# usage: ccache-probe.sh "<label>"   -- appends one ccache -s reading to ~/gate72-ccache-stats.txt
# v2: no --no-cache (see the Dockerfile header); a nonce forces the reading RUN to execute.
{ echo "=== ccache -s at $(date -Is) : $1"; docker build --progress plain --build-arg NONCE=$(date +%s%N) ~/ccache-probe 2>&1 | grep -E "^#[0-9]+ [0-9.]+ |^#[0-9]+ DONE" | grep -E "probe-|Hits|Misses|Cacheable|Cache size|Local storage|du|[0-9]+[MK]\s|DONE" | tail -14; echo; } >> ~/gate72-ccache-stats.txt
