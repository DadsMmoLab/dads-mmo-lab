# Integration Test Fixture — AzerothCore WotLK

Pins the exact AzerothCore compose project used by the Phase 1.5 integration
suite (roadmap Phase 0.4) so "a real running AzerothCore compose project" is a
reproducible fixture, not an ambient assumption.

## Source

The installers clone the Playerbots fork:

```text
repo:    https://github.com/mod-playerbots/azerothcore-wotlk.git
branch:  Playerbot
```

The `docker-compose.yml` shipped in that repo is the canonical compose file the
integration suite exercises. It defines the three containers the controller
manages (names mirror `dml-start.sh`):

| Container | Compose service | Purpose |
|---|---|---|
| `ac-database` | database | MySQL 8 (AzerothCore schema: auth/chars/world) |
| `ac-authserver` | authserver | Realm/auth (port 3724) |
| `ac-worldserver` | worldserver | World (port 8085) |

## Reproducing the fixture

A contributor or CI runner brings up an equivalent environment with:

```bash
git clone --depth 1 --branch Playerbot \
  https://github.com/mod-playerbots/azerothcore-wotlk.git
cd azerothcore-wotlk
cp conf/dist/env ../ # or the repo's documented env step
docker compose up -d
```

The worldserver is built from source and takes significant time on first boot;
integration tests must poll `docker inspect --format '{{.State.Health.Status}}'`
(not a fixed sleep) — the same logic `docker_ctl.py` ports from `dml-start.sh`.

## Not pinned (intentionally)

- **Exact commit SHA.** The `Playerbot` branch moves, so pinning a SHA here
  would drift faster than the installers. This doc pins the *source + branch*;
  if a breaking change ever lands, pin a SHA and record it in
  `pyplan/checklist.md`.
- **DB credentials.** Default to `password` (matching `dml-start.sh`'s
  `DOCKER_DB_ROOT_PASSWORD` default); override via env in the test when needed.

## When this changes

Any change to the fixture (repo, branch, container names, credentials) must be
updated here **and** logged in `pyplan/checklist.md` Phase 0 Notes.