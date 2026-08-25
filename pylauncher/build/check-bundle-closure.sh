#!/usr/bin/env bash
# Does the Linux bundle carry every shared library it needs, or does it expect
# the user's machine to supply one?
#
# Twice a library has been missing from the shipped artifact and been found by a
# user rather than by CI: libxcb-cursor0 (#96) and libxkbcommon-x11 (v0.6.51,
# which aborted on launch on Arch). Both had the same cause - PyInstaller
# bundles what the BUILD HOST happens to have - and both would have SURVIVED a
# smoke test on the GitHub runner, because the runner has those libraries
# installed. Testing the artifact on the machine that built it cannot find this
# class of defect at all, which is why this runs in a container that has
# nothing.
#
# Static: no X server, no display, the app is never launched. Seconds.
#
#   usage: check-bundle-closure.sh <dir containing yulon/ and yulon/_internal>
#
# The Qt platform plugins are checked as well as the top-level binary, because
# that is where both real defects lived: libqxcb.so is dlopened at runtime, so
# nothing in the executable's own DT_NEEDED mentions it, and a missing
# dependency of it does not surface until a user starts the app.
set -uo pipefail

BUNDLE=${1:?usage: check-bundle-closure.sh <dir containing yulon/ and yulon/_internal>}
IMAGE=${BUNDLE_CHECK_IMAGE:-debian:bookworm-slim}

# Libraries the artifact is SUPPOSED to take from the host.
#
# The graphics stack is tied to the user's GPU driver: shipping our own libGL
# against their kernel driver is a documented way to break machines, and every
# AppImage guide says to exclude it. libxcb and the core X client libraries are
# present on any machine that has a display at all - which is the only kind of
# machine that can run a GUI.
#
# Nothing else belongs here. A soname added to this list is a promise that every
# supported distro ships it, and the Arch failure is what that promise looks
# like when it is wrong: libxkbcommon-x11 was assumed universal and is not.
HOST_PROVIDED='
libGL.so.1
libEGL.so.1
libGLX.so.0
libGLdispatch.so.0
libdrm.so.2
libgbm.so.1
libwayland-client.so.0
libwayland-cursor.so.0
libwayland-egl.so.1
libwayland-server.so.0
libxcb.so.1
'

if [ ! -d "$BUNDLE" ]; then
    echo "no such directory: $BUNDLE" >&2
    exit 2
fi

echo "=== resolving the bundle against a bare $IMAGE (no desktop libraries)"

# `ldd` on the runner would resolve against the RUNNER's libraries and report
# everything satisfied - which is exactly how both shipped defects passed. The
# container is the whole point.
#
# Only "=> not found" lines are a finding. `ldd` also prints its own error lines
# for an object it cannot load at all, and matching those produced nonsense the
# first time this was written.
raw=$(docker run --rm \
    -v "$(cd "$BUNDLE" && pwd)":/bundle:ro \
    -e LD_LIBRARY_PATH=/bundle/yulon/_internal \
    "$IMAGE" \
    sh -c '
        set -u
        for so in /bundle/yulon/_internal/PySide6/Qt/plugins/platforms/*.so \
                  /bundle/yulon/_internal/lib*.so* \
                  /bundle/yulon/yulon; do
            [ -e "$so" ] || continue
            ldd "$so" 2>/dev/null | awk "/=> not found/ {print \$1}"
        done
    ' 2>/dev/null | sort -u)

missing=""
for soname in $raw; do
    case "$HOST_PROVIDED" in
        *"
$soname
"*) continue ;;
    esac
    missing="$missing$soname
"
done

if [ -n "$missing" ]; then
    echo
    echo "MISSING from the bundle, and not on the host-provided list:"
    printf '%s' "$missing" | sed 's/^/  /'
    echo
    echo "Each is a library the artifact expects the USER's machine to have."
    echo "It resolved on the builder, which is why nothing caught it there."
    echo "Either add the providing package to the workflow's apt step so the"
    echo "bundle carries it, or - if every supported distro really does ship it -"
    echo "add the soname to HOST_PROVIDED in this script, with a reason."
    exit 1
fi

echo "  every needed library is either bundled or on the host-provided list"
