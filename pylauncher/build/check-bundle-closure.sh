#!/usr/bin/env bash
# Does the Linux bundle carry every shared library it needs, or does it expect
# the user's machine to supply one?
#
# Twice a library shipped missing and was found by a user, not by us:
# libxcb-cursor0 (#96) and libxkbcommon-x11 (v0.6.51, which aborted on launch on
# Arch). Both had the same cause - PyInstaller bundles what the BUILD HOST
# happens to have - and both would have SURVIVED a smoke test on the GitHub
# runner, because the runner has those libraries. Testing an artifact on the
# machine that built it is blind to this whole class, which is why this resolves
# the bundle inside a container that has nothing.
#
# Static: no X server, no display, the app is never launched. Seconds.
#
#   usage: check-bundle-closure.sh <dir containing yulon/ and yulon/_internal>
#
# EVERY failure path here exits non-zero on purpose. A review of the first
# version measured two ways it could report a clean bundle without having looked
# at one - an unpullable image, and globs that matched nothing - and a gate that
# passes when it did not run is worse than no gate, because it is believed.
set -uo pipefail

BUNDLE=${1:?usage: check-bundle-closure.sh <dir containing yulon/ and yulon/_internal>}
IMAGE=${BUNDLE_CHECK_IMAGE:-debian:bookworm-slim}

# Libraries the artifact is SUPPOSED to take from the host.
#
# The graphics stack is tied to the user's GPU driver: shipping our own libGL
# against their kernel driver is a documented way to break machines, and every
# AppImage guide says to exclude it. libxcb and the core X client libraries are
# on any machine that has a display at all - which is the only kind that can run
# a GUI.
#
# Nothing else belongs here. A soname added is a PROMISE that every supported
# distro ships it, and libxkbcommon-x11 is what that promise looks like when it
# is wrong: assumed universal, absent on Arch, and the app aborted on launch.
HOST_PROVIDED='
libGL.so.1
libEGL.so.1
libGLX.so.0
libGLdispatch.so.0
libOpenGL.so.0
libdrm.so.2
libgbm.so.1
libwayland-client.so.0
libwayland-cursor.so.0
libwayland-egl.so.1
libwayland-server.so.0
libxcb.so.1
'

# Qt's GTK platform-theme plugin, and only that one, is skipped.
#
# Qt DEGRADES PAST it: without libqgtk3.so you lose the GTK look of the native
# file dialog, not the application. This gate exists to fail a release when the
# artifact cannot RUN, so an optional plugin must not be able to block one.
#
# Passed through the environment and used UNQUOTED, which is the whole point.
# The first version wrote a regex ('platformthemes/libqgtk3\.so$') and spliced
# it into a `case`, which takes globs - it matched only because the trailing
# `$*` expanded to empty in that exact invocation, so a later edit that gave
# `sh -c` two more tokens would have silently switched it off. A gate that
# quietly stops checking is the defect this file's header describes twice.
#
# Deleting it outright was tried first and was wrong for a subtler reason.
# Measured on the real ubuntu-22.04 artifact, PyInstaller does bundle the whole
# GTK stack, so scanning the plugin passes today at 254 objects instead of 253.
# But NOTHING in release.yml installs GTK - that stack is on the runner only as
# an ambient side effect of its preinstalled browsers. Resting a permanent rule
# on one image's incidental package set is the same mistake this file was
# written to catch, and it would come due exactly when the ubuntu-22.04 pin
# expires (see release.yml) and the artifact moves to an image that may trim it.
EXCLUDE_GLOB=${BUNDLE_CHECK_EXCLUDE_GLOB:-'*/platformthemes/libqgtk3.so'}

if [ ! -d "$BUNDLE" ]; then
    echo "no such directory: $BUNDLE" >&2
    exit 2
fi

echo "=== resolving the bundle against a bare $IMAGE (no desktop libraries)"

# `ldd` on the runner would resolve against the RUNNER's libraries and report
# everything satisfied - which is exactly how both shipped defects passed. The
# container is the whole point.
#
# LD_LIBRARY_PATH is scoped to the ldd invocation, NOT exported to the
# container: several of the bundle's libraries (libselinux, libstdc++, libssl)
# are newer than the image's, and exporting it makes the container's own
# coreutils die with a glibc version error - measured, `find` and `sed` both
# abort. That would empty the pipeline, which used to look like a pass.
#
# Two failure shapes are collected, because only the first was caught before and
# the second is the same defect wearing different words:
#   libfoo.so.1 => not found                       - a library nobody provides
#   ... version `GLIBC_2.38' not found (required by ...)  - one too new to use
# The second is live in this pipeline by construction: the runner is Ubuntu
# 24.04 (glibc 2.39) and this image is bookworm (2.36), so the artifact is
# always built against the newer of the two.
output=$(docker run --rm \
    -v "$(cd "$BUNDLE" && pwd)":/bundle:ro \
    -e EXCLUDE_GLOB="$EXCLUDE_GLOB" \
    "$IMAGE" \
    sh -c '
        set -u
        found=0
        for so in $(find /bundle -name "*.so" -o -name "*.so.*" -o -name "yulon" 2>/dev/null); do
            [ -f "$so" ] || continue
            # Unquoted on purpose: expanded, then matched AS a glob.
            case "$so" in $EXCLUDE_GLOB) continue ;; esac
            found=$((found + 1))
            LD_LIBRARY_PATH=/bundle/yulon/_internal ldd "$so" 2>&1 \
                | sed -n -e "s/^[[:space:]]*\([^ ]*\) => not found.*/MISSING \1/p" \
                         -e "s/.*version .\(GLIBC_[0-9.]*\). not found.*/TOONEW \1/p"
        done
        echo "INSPECTED $found"
    ' 2>&1)
rc=$?

if [ "$rc" -ne 0 ]; then
    echo
    echo "the probe itself failed (docker exit $rc) - this is NOT a pass:" >&2
    printf '%s\n' "$output" | sed 's/^/  /' >&2
    echo "Rate limiting, a daemon that is not up, or no docker at all look like" >&2
    echo "this. The bundle has not been checked." >&2
    exit 2
fi

inspected=$(printf '%s\n' "$output" | awk '/^INSPECTED /{print $2; exit}')
if [ -z "$inspected" ] || [ "$inspected" -eq 0 ]; then
    echo
    echo "the probe inspected NO objects - this is NOT a pass." >&2
    echo "The bundle layout has moved (a PyInstaller upgrade relocating _internal," >&2
    echo "or the executable being renamed) and this check is now blind. Fix the" >&2
    echo "paths rather than the symptom." >&2
    exit 2
fi
echo "  inspected $inspected objects"

missing=""
for line in $(printf '%s\n' "$output" | awk '/^MISSING /{print $2}' | sort -u); do
    case "$HOST_PROVIDED" in
        *"
$line
"*) continue ;;
    esac
    missing="$missing  $line
"
done

toonew=$(printf '%s\n' "$output" | awk '/^TOONEW /{print $2}' | sort -u)

if [ -n "$missing" ] || [ -n "$toonew" ]; then
    echo
    if [ -n "$missing" ]; then
        echo "MISSING from the bundle, and not on the host-provided list:"
        printf '%s' "$missing"
        echo
        echo "Each is a library the artifact expects the USER's machine to have."
        echo "It resolved on the builder, which is why nothing caught it there."
        echo "Either add the providing package to the workflow's apt step so the"
        echo "bundle carries it, or - if every supported distro really ships it -"
        echo "add the soname to HOST_PROVIDED here, with a reason."
    fi
    if [ -n "$toonew" ]; then
        echo
        echo "BUILT AGAINST A NEWER GLIBC than the bundle can run on:"
        printf '%s\n' "$toonew" | sed 's/^/  /'
        echo
        echo "The artifact will abort before drawing anything on any distro older"
        echo "than the builder. Build on an older runner image, or vendor the"
        echo "affected library."
    fi
    exit 1
fi

echo "  every needed library is either bundled or on the host-provided list"
