#!/usr/bin/env bash
# T44's mutation runner. One named patch, applied to a clean tree, gated, reverted.
#
# Written for round 2: the first report claimed 60 killed mutations in prose and
# the gate folder had no runner, no patches and no logs, so the claim could not
# be checked. `patches/` holds one `.patch` per mutation and `logs/` holds the
# gate output each produced.
#
#   ./run-mutation.sh <name>      one mutation, by the basename of its patch
#   ./run-mutation.sh --all       every patch in patches/, in name order
#
# A mutation PASSES the audit when the gate it runs goes RED. A patch whose gate
# stays green is a mutation nothing saw, and the report has to say so.
#
# `__pycache__` is purged on BOTH sides of every apply. A same-length edit
# reuses stale bytecode and the evidence is then worthless.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../../.." && pwd)"
PY="${YULON_PY:-$HOME/y313/bin/python}"
mkdir -p "$HERE/logs"

purge() { find "$REPO/pylauncher" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; true; }

one() {
    local name="$1" patch="$HERE/patches/$1.patch"
    [ -f "$patch" ] || { echo "$name: no such patch" >&2; return 2; }
    local scope
    scope="$(sed -n 's/^# *gate: *//p' "$patch" | head -1)"
    [ -n "$scope" ] || scope="-q"
    if ! git -C "$REPO" apply --check "$patch" 2>/dev/null; then
        echo "$name: DOES NOT APPLY to this tree" | tee "$HERE/logs/$name.log"
        return 2
    fi
    git -C "$REPO" apply "$patch"
    purge
    YULON_REPO="$REPO" yt "$scope" > "$HERE/logs/$name.log" 2>&1
    local rc=$?
    git -C "$REPO" apply -R "$patch"
    purge
    local last
    last="$(sed 's/\x1b\[[0-9;]*m//g' "$HERE/logs/$name.log" | grep -aoE '[0-9]+ (failed|passed)[^%]*' | tail -1)"
    if [ "$rc" -eq 0 ]; then
        echo "SURVIVED  $name  ($last)"
        return 1
    fi
    echo "killed    $name  ($last)"
    return 0
}

if [ "${1:-}" = "--all" ]; then
    rc=0
    for patch in "$HERE"/patches/*.patch; do
        one "$(basename "$patch" .patch)" || rc=1
    done
    exit $rc
fi
one "${1:?usage: run-mutation.sh <name>|--all}"
