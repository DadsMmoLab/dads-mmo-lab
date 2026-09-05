#!/usr/bin/env bash
# Why did press 3 recompile everything? Two small questions put to the daemon with busybox-sized
# builds ("gate the tool, not the payload"), after the real install has reached ready:
#
#   A. Does a `--mount=type=cache` keep what a RUN wrote into it when that RUN's CLIENT is
#      SIGKILLed mid-way (the shape of the kill in kill-record.txt)?
#   B. Does a cache mount written by `docker compose build` (buildx bake, the engine's route)
#      have the same key as one read by `docker build` (the ccache-probe's route)?
#
# And C, the real thing: every exec.cachemount record the daemon holds right now, with its id
# string and size, after press 3's complete build.
set -u
D=$HOME/cachemount-diag
mkdir -p "$D"
OUT=$HOME/gate72-cachemount-diag.txt
{
echo "=== cache-mount diagnostic, lane gate-71-72, $(date -Is)"
echo "docker: $(docker --version); compose: $(docker compose version); buildx: $(docker buildx version)"

echo
echo "=== C. every exec.cachemount record the daemon holds now (after press 3's full build)"
docker buildx du --verbose 2>/dev/null | awk -v RS="" '/exec.cachemount/' | grep -E "^ID|^Size|^Description|^Usage|^Last used|^Type" | sed 's/ from exec .* with id/ ... with id/'

echo
echo "=== A. a RUN writes into a cache mount, its client is SIGKILLed 20 s in; does the write survive?"
cat > "$D/Dockerfile.a" <<'EOF'
FROM busybox:1.36
RUN --mount=type=cache,target=/diagcache,sharing=locked,id=diag-a sh -c 'echo written-before-kill > /diagcache/marker && dd if=/dev/zero of=/diagcache/blob bs=1M count=50 2>/dev/null && ls -la /diagcache && sync && sleep 120'
EOF
cat > "$D/Dockerfile.a-probe" <<'EOF'
FROM busybox:1.36
RUN --mount=type=cache,target=/diagcache,sharing=locked,id=diag-a ls -la /diagcache && cat /diagcache/marker
EOF
echo "--- start the writer (docker build, --no-cache, progress plain) in the background"
docker build --no-cache --progress plain -f "$D/Dockerfile.a" "$D" > "$D/a-writer.log" 2>&1 &
WPID=$!
sleep 20
echo "--- writer log so far"; grep -E "^#[0-9]+ [0-9.]+ " "$D/a-writer.log" | tail -6
echo "--- compiler-shaped processes: $(pgrep -c -f '[s]leep 120') sleep-120 in the container"
echo "--- SIGKILL the client (pid $WPID) at $(date -Is)"
kill -9 "$WPID"; sleep 4
echo "--- containers still running: $(docker ps -q | wc -l); sleep-120 processes: $(pgrep -c -f '[s]leep 120')"
sleep 4
echo "--- probe the same cache mount id from a fresh build"
docker build --no-cache --progress plain -f "$D/Dockerfile.a-probe" "$D" 2>&1 | grep -E "^#[0-9]+ [0-9.]+ |ERROR|error" | tail -8
echo "--- the record"
docker buildx du --verbose 2>/dev/null | awk -v RS="" '/diag-a/' | grep -E "^Size|^Description|^Usage" | sed 's/ from exec .* with id/ ... with id/'

echo
echo "=== A2. the same, but the RUN COMPLETES (control: a finished RUN's writes are kept)"
cat > "$D/Dockerfile.a2" <<'EOF'
FROM busybox:1.36
RUN --mount=type=cache,target=/diagcache,sharing=locked,id=diag-a2 sh -c 'echo written-and-finished > /diagcache/marker && ls -la /diagcache'
EOF
cat > "$D/Dockerfile.a2-probe" <<'EOF'
FROM busybox:1.36
RUN --mount=type=cache,target=/diagcache,sharing=locked,id=diag-a2 ls -la /diagcache && cat /diagcache/marker
EOF
docker build --no-cache --progress plain -f "$D/Dockerfile.a2" "$D" 2>&1 | grep -E "^#[0-9]+ [0-9.]+ " | tail -3
docker build --no-cache --progress plain -f "$D/Dockerfile.a2-probe" "$D" 2>&1 | grep -E "^#[0-9]+ [0-9.]+ |ERROR|error" | tail -5

echo
echo "=== B. compose build (bake) writes a cache mount; docker build reads the same target"
mkdir -p "$D/b"
cat > "$D/b/Dockerfile" <<'EOF'
FROM busybox:1.36
RUN --mount=type=cache,target=/diagcache2,sharing=locked sh -c 'echo written-by-compose-build > /diagcache2/marker && ls -la /diagcache2'
EOF
cat > "$D/b/compose.yml" <<'EOF'
services:
  diag:
    image: yulon.local/diag-b:latest
    build:
      context: .
EOF
cat > "$D/Dockerfile.b-probe" <<'EOF'
FROM busybox:1.36
RUN --mount=type=cache,target=/diagcache2,sharing=locked ls -la /diagcache2 && cat /diagcache2/marker
EOF
( cd "$D/b" && docker compose build --no-cache --progress plain 2>&1 | grep -E "^#[0-9]+ [0-9.]+ |cached mount" | tail -4 )
echo "--- docker build probe of target=/diagcache2 (no id given on either side)"
docker build --no-cache --progress plain -f "$D/Dockerfile.b-probe" "$D" 2>&1 | grep -E "^#[0-9]+ [0-9.]+ |ERROR|error" | tail -5
echo "--- the records for /diagcache2, with the id string each side used"
docker buildx du --verbose 2>/dev/null | awk -v RS="" '/diagcache2/' | grep -E "^Size|^Description|^Usage" | sed 's/ from exec .* with id/ ... with id/'

echo
echo "=== cleanup of the diagnostic's own images (cache-mount records are left for the reader)"
docker image rm -f yulon.local/diag-b:latest > /dev/null 2>&1; echo done
} > "$OUT" 2>&1
cat "$OUT"
