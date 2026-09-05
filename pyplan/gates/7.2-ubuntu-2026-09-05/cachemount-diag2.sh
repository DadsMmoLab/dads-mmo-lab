#!/usr/bin/env bash
# cachemount-diag.sh's CONTROL failed: a RUN that completed normally wrote a marker into a
# cache mount and the very next build, same id, could not see it. Every build in that script
# ran with `--no-cache`. So the question is not "did the kill lose the mount" but "does
# `--no-cache` give each build its own cache mount on this daemon" -- in which case the T0/T1/T2
# probes in ccache-stats.txt could never have seen the engine's mount, and the only evidence
# about the resume is edge-rate.txt. Same busybox shapes, `--no-cache` removed; a build-arg
# nonce makes the probe's RUN execute instead of being served from the layer cache.
set -u
D=$HOME/cachemount-diag
mkdir -p "$D"
OUT=$HOME/gate72-cachemount-diag2.txt
N=$(date +%s)
{
echo "=== cache-mount diagnostic 2 (no --no-cache anywhere), lane gate-71-72, $(date -Is)"

echo
echo "=== A3. a RUN completes; the next build reads the same id (control, without --no-cache)"
cat > "$D/Dockerfile.a3" <<'EOF'
FROM busybox:1.36
ARG NONCE
RUN --mount=type=cache,target=/diagcache,sharing=locked,id=diag-a3 sh -c "echo written-$NONCE > /diagcache/marker && ls -la /diagcache"
EOF
cat > "$D/Dockerfile.a3-probe" <<'EOF'
FROM busybox:1.36
ARG NONCE
RUN --mount=type=cache,target=/diagcache,sharing=locked,id=diag-a3 sh -c "echo probe-$NONCE && ls -la /diagcache && cat /diagcache/marker"
EOF
docker build --progress plain --build-arg NONCE=$N -f "$D/Dockerfile.a3" "$D" 2>&1 | grep -E "^#[0-9]+ [0-9.]+ " | tail -3
echo "--- probe"
docker build --progress plain --build-arg NONCE=$N -f "$D/Dockerfile.a3-probe" "$D" 2>&1 | grep -E "^#[0-9]+ [0-9.]+ |ERROR" | tail -5
echo "--- records for diag-a3"
docker buildx du --verbose 2>/dev/null | awk -v RS="" '/diag-a3/' | grep -E "^Size|^Description|^Usage" | sed 's/ from exec .* with id/ ... with id/'

echo
echo "=== A4. a RUN writes, its client is SIGKILLed 15 s in; the next build reads the same id (no --no-cache)"
cat > "$D/Dockerfile.a4" <<'EOF'
FROM busybox:1.36
ARG NONCE
RUN --mount=type=cache,target=/diagcache,sharing=locked,id=diag-a4 sh -c "echo written-before-kill-$NONCE > /diagcache/marker && dd if=/dev/zero of=/diagcache/blob bs=1M count=50 2>/dev/null && sync && ls -la /diagcache && sleep 120"
EOF
cat > "$D/Dockerfile.a4-probe" <<'EOF'
FROM busybox:1.36
ARG NONCE
RUN --mount=type=cache,target=/diagcache,sharing=locked,id=diag-a4 sh -c "echo probe-$NONCE && ls -la /diagcache && cat /diagcache/marker"
EOF
docker build --progress plain --build-arg NONCE=$N -f "$D/Dockerfile.a4" "$D" > "$D/a4-writer.log" 2>&1 &
WPID=$!
sleep 15
grep -E "^#[0-9]+ [0-9.]+ " "$D/a4-writer.log" | tail -3
echo "--- SIGKILL the client (pid $WPID) at $(date -Is); sleep-120 in container before: $(pgrep -c -f '[s]leep 120')"
kill -9 "$WPID"; sleep 5
echo "--- after: sleep-120 processes: $(pgrep -c -f '[s]leep 120')"
echo "--- probe"
docker build --progress plain --build-arg NONCE=$N -f "$D/Dockerfile.a4-probe" "$D" 2>&1 | grep -E "^#[0-9]+ [0-9.]+ |ERROR" | tail -6
echo "--- records for diag-a4"
docker buildx du --verbose 2>/dev/null | awk -v RS="" '/diag-a4/' | grep -E "^Size|^Description|^Usage" | sed 's/ from exec .* with id/ ... with id/'

echo
echo "=== T2'. the engine's own /ccache mount, read WITHOUT --no-cache (nonce forces the RUN)"
cat > "$D/Dockerfile.ccache" <<'EOF'
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends ccache && rm -rf /var/lib/apt/lists/*
ARG NONCE
RUN --mount=type=cache,target=/ccache,sharing=locked sh -c "echo probe-$NONCE && CCACHE_DIR=/ccache ccache -s && echo '--- du' && du -sh /ccache && ls /ccache | head"
EOF
docker build --progress plain --build-arg NONCE=$N -f "$D/Dockerfile.ccache" "$D" 2>&1 | grep -E "^#[0-9]+ [0-9.]+ " | grep -vE "apt|Get:|Unpack|Setting|Selecting|Preparing|Reading|Building dep|Need to|After this|debconf|update-alternatives|Updating" | tail -16
echo "--- every exec.cachemount record now"
docker buildx du --verbose 2>/dev/null | awk -v RS="" '/exec.cachemount/' | grep -E "^ID|^Size|^Description|^Usage|^Last used" | sed 's/ from exec .* with id/ ... with id/'
} > "$OUT" 2>&1
cat "$OUT"
