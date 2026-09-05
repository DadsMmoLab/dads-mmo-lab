# Reads the ccache counters out of the SAME BuildKit cache mount the AzerothCore build uses
# (apps/docker/Dockerfile:83: --mount=type=cache,target=/ccache,sharing=locked). BuildKit keys a
# cache mount by its id, which defaults to the target path, so any Dockerfile naming target=/ccache
# on this builder sees the same directory.
# v2, 2026-09-05 01:2x: NO --no-cache. Measured in cachemount-diag3.txt: a --no-cache build that names
# a cache mount RESETS it, so v1 of this probe emptied the engine ccache it was meant to read. The
# NONCE build-arg is what makes the last RUN execute instead of coming from the layer cache.
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends ccache && rm -rf /var/lib/apt/lists/*
ARG NONCE
RUN --mount=type=cache,target=/ccache,sharing=locked sh -c "echo probe-$NONCE && CCACHE_DIR=/ccache ccache -s && echo --- du && du -sh /ccache"
