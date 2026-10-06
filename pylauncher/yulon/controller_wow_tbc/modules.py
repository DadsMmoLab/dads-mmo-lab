"""Module / mod management for TBC, driven by JSON manifests (roadmap 8.7b).

The per-game binding only — the WotLK sibling's shape, with this game's facts:
the `wow-tbc` game id, where its bundled manifests live, the GitHub location
they are refreshed from. Loading and validating (`yulon.manifest_store`) and
applying (`yulon.apply`) are shared and game-agnostic (style-guide §4).

**What a "module" is here, and why nothing new had to be invented.** An
AzerothCore module is a git repository compiled into the worldserver: clone it
into `modules/`, rebuild, restart. CMaNGOS has no such mechanism at all — there
is no module directory, no CMake collection step, nothing to link in. So on
this game an item is one of the two shapes the manifest schema already had for
AzerothCore's *non*-module families:

* a **configuration activation** — `conf[].keys` written into a file the
  install already owns, which for CMaNGOS means `etc/mangosd.conf`. The WotLK
  `mods` family does exactly this to `env/dist/etc/worldserver.conf`.
* a **SQL mod** — `sql[].statement` run against this game's world schema.

The one thing the schema could not say was the consequence: `ApplyReport.
restart_recommended` was derived from NPCs, direct SQL and server DBCs, none of
which a conf-only item has, so it answered "nothing further needed" over a value
mangosd reads once, at startup. `Build.restart` (added by 8.7b) is that fact
declared by the item.

Three things this binding does NOT do, each because CMaNGOS cannot:

* no `applier()` of its own that builds a `DockerSql`. The WotLK one can,
  because that game's database password is a fixed catalog value; TBC's is
  generated per install and lives in `<server_dir>/.db_password`. The caller
  that has already read it — `ui.controller_view._for_tbc()` — passes the
  runner in, so the password is read in exactly one place.
* no `modules`/`ale`/`kegs` content. Those indexes exist and are empty:
  "there are none" is a fact worth writing down, and without the files the
  Modules tab prints `!! could not load modules: manifest file missing` for
  each of the three.
* no `rebuild`. Every manifest here says `build.rebuild: false`, and
  `test_tbc_modules.py` requires it — the FIELD's default is True, so a mod
  that merely declared `{"restart": true}` would ask a user to spend an hour
  recompiling a worldserver that would come back byte-identical.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from yulon import resources
from yulon.apply import Applier, ApplyReport, SqlRunner
from yulon.git import Git
from yulon.manifest import Manifest, ManifestType
from yulon.manifest_store import (
    HttpGet,
    ManifestFetcher,
    ManifestStore,
    RefreshResult,
    load_manifest,
    urllib_get,
)

GAME = "wow-tbc"

# The bundled manifest tree that ships with the app (source tree or PyInstaller bundle).
BUNDLED_MANIFESTS_DIR = resources.manifests_dir()

# Raw-GitHub prefix that `<game>/<family>.json` is joined onto when refreshing.
MANIFEST_BASE_URL = (
    "https://raw.githubusercontent.com/DadsMmoLab/dads-mmo-lab/main/pylauncher/manifests"
)


def load_module(manifest_path: Path) -> Manifest:
    """Load and validate a single manifest file into a typed `Manifest`.

    Raises `yulon.manifest_store.ManifestError` for a missing/unreadable file and
    `pydantic.ValidationError` for anything the schema rejects — including a
    `source.repo` outside the allow-list (README §3a).
    """
    return load_manifest(manifest_path)


def store(root: Path = BUNDLED_MANIFESTS_DIR) -> ManifestStore:
    """The TBC manifest store over `root` (bundled by default, or a refreshed cache)."""
    return ManifestStore(root, GAME)


def fetcher(cache_root: Path, http: HttpGet = urllib_get) -> ManifestFetcher:
    """A fetcher that mirrors the TBC manifests from GitHub into `cache_root`."""
    return ManifestFetcher(MANIFEST_BASE_URL, cache_root, http)


def refresh(cache_root: Path, kind: ManifestType, http: HttpGet = urllib_get) -> RefreshResult:
    """Refresh one family of TBC manifests into `cache_root` (ETag-revalidated)."""
    return fetcher(cache_root, http).refresh(GAME, kind)


def applier(
    server_dir: Path,
    *,
    sql: SqlRunner | None,
    world_running: Callable[[], bool | None],
    start_database: Callable[[], bool] | None = None,
    git: Git | None = None,
    client_dir: Path | None = None,
) -> Applier:
    """An `Applier` for the TBC install at `server_dir`.

    `sql` is REQUIRED rather than defaulted, and that is the difference from
    `controller_wow_wotlk.modules.applier()` worth reading twice. That one can
    build its own `DockerSql`, because AzerothCore's database password is a
    fixed value in `catalog.json`. This game's is generated at install time into
    `<server_dir>/.db_password`, and its schemas are `mangos`/`realmd`/
    `characters` rather than `acore_*`. A default built here would have to
    re-derive both, and a second derivation of a password is how a runner ends
    up authenticating with the wrong one — the closed bug `controller_view.
    _db_password()` describes. So the caller that already holds the correct
    `DockerSql` (built from the entry, with the entry's schema map and MariaDB
    client) hands it over, and passing `sql=None` explicitly means "no
    database", which reports every SQL step as skipped rather than running it
    somewhere else.

    No `dbc=`: `server_dbc` copies DBC files out of a clone, and nothing in
    `manifests/wow-tbc/` clones anything.

    `world_running` is REQUIRED for the reason `sql` is, and it is the same kind
    of fact: something only the caller knows about THIS install, which a default
    here would have to re-derive. It is checklist 8.7a's guard (`apply.py`,
    `_refuse_direct_sql_into_a_running_world`), and this family is squarely on
    its path — `all-stackables` alone ships three direct `world` steps on
    install and two on remove here. `start_database` is optional and absent
    means the behaviour this route had before T7: on a stopped stack the
    database is down too, and a direct step dies on `container ... is not
    running`, which is `bug-checklist §46` and was CMaNGOS's entry before it was
    measured on AzerothCore.
    """
    return Applier(
        server_dir,
        git=git,
        sql=sql,
        client_dir=client_dir,
        world_running=world_running,
        start_database=start_database,
    )


def apply_module(
    manifest: Manifest,
    server_dir: Path,
    values: Mapping[str, str] | None = None,
    *,
    sql: SqlRunner | None,
    world_running: Callable[[], bool | None],
    start_database: Callable[[], bool] | None = None,
    client_dir: Path | None = None,
) -> ApplyReport:
    """Install `manifest` into the TBC server at `server_dir`.

    Returns the `ApplyReport`; the caller decides about the restart it names
    (call down / signal up — this function never touches Docker's lifecycle
    itself). For a TBC item that restart is the whole point: every manifest
    here declares `build.restart`, because mangosd reads `etc/*.conf` and loads
    the world database once, at startup.

    `world_running` is required for `applier()`'s reason and passed straight
    through, rather than answered here on the caller's behalf (T7).
    """
    return applier(
        server_dir,
        sql=sql,
        world_running=world_running,
        start_database=start_database,
        client_dir=client_dir,
    ).install(manifest, values)
