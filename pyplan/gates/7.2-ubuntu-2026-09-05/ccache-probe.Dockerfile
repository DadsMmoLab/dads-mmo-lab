# Reads the ccache counters out of the SAME BuildKit cache mount the AzerothCore build uses
# (apps/docker/Dockerfile: --mount=type=cache,target=/ccache). BuildKit keys a cache mount
# by its id, which defaults to the target path, so any Dockerfile naming target=/ccache on
# this builder sees the same directory. --no-cache makes the last RUN execute every time.
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends ccache && rm -rf /var/lib/apt/lists/*
RUN --mount=type=cache,target=/ccache,sharing=locked CCACHE_DIR=/ccache ccache -s && echo "--- du" && du -sh /ccache
