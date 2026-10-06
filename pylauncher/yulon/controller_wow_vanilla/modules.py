"""Module / mod management for Vanilla, driven by JSON manifests (roadmap 8.7c).

The per-game binding only — the TBC sibling's shape (8.7b), with this game's
facts: the `wow-vanilla` game id, where its bundled manifests live, and the
GitHub location they are refreshed from. Loading and validating
(`yulon.manifest_store`) and applying (`yulon.apply`) are shared and
game-agnostic (style-guide §4).

**What a "module" is here.** The same two shapes 8.7b settled on, and for the
same reason: CMaNGOS has no module directory, no CMake collection step and
nothing to link in, so an item is either a **configuration activation** —
`conf[].keys` written into `etc/mangosd.conf`, a file this install already owns
— or a **SQL mod**, `sql[].statement` run against `mangos`, this game's world
schema. That decision is not re-argued here; `controller_wow_tbc.modules`
carries it.

**What IS this box's own, and was measured rather than inherited.** The
manifests under `manifests/wow-vanilla/` are not copies of `manifests/wow-tbc/`,
and three differences came out of reading `~/vanilla-75b` on m910q on
2026-09-08:

* `AllowTwoSide.Interaction.Trade` exists on this fork
  (`src/game/World/World.cpp:525`, `etc/mangosd.conf:928`) and does not exist on
  mangos-tbc, so `cross-faction` is ten key/patch pairs here and nine there;
* there are no `Rate.XP.Kill.Vanilla` / `.BC` twins — `World.cpp:422` sets
  `Rate.XP.Kill` and nothing else of that family;
* `Rate.Pet.XP.Kill` ships two lines above `Rate.XP.Kill`, which makes the
  anchored remove patches load-bearing on this tree in a way they are not on
  the other.

The `.server motd` proof is this tree's own too: the command table row is
`src/game/Chat/Chat.cpp:797` with `allowConsole` true, answering from
`Level0.cpp:280-282` — a different file and a different line from the TBC row
the sibling box cites.

Three things this binding does NOT do, each for the reason 8.7b gives:

* no `applier()` that builds its own `DockerSql`. This family's database
  password is generated per install into `<server_dir>/.db_password`, so the
  caller that has already read it — `ui.controller_view._for_vanilla()` —
  passes the runner in, and the password is read in exactly one place.
* no `modules`/`ale`/`kegs` content. Those indexes exist and are empty: "there
  are none" is a fact worth writing down, and without the files the Modules tab
  prints `!! could not load modules: manifest file missing` for each.
* no `rebuild`. Every manifest here says `build.rebuild: false`, and
  `test_vanilla_modules.py` requires it — the FIELD's default is True, so a mod
  that merely declared `{"restart": true}` would ask a user to spend an hour
  recompiling a mangosd that would come back byte-identical.
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

GAME = "wow-vanilla"

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
    """The Vanilla manifest store over `root` (bundled by default, or a refreshed cache)."""
    return ManifestStore(root, GAME)


def fetcher(cache_root: Path, http: HttpGet = urllib_get) -> ManifestFetcher:
    """A fetcher that mirrors the Vanilla manifests from GitHub into `cache_root`."""
    return ManifestFetcher(MANIFEST_BASE_URL, cache_root, http)


def refresh(cache_root: Path, kind: ManifestType, http: HttpGet = urllib_get) -> RefreshResult:
    """Refresh one family of Vanilla manifests into `cache_root` (ETag-revalidated)."""
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
    """An `Applier` for the Vanilla install at `server_dir`.

    `sql` is REQUIRED rather than defaulted, exactly as in
    `controller_wow_tbc.modules.applier()` and for the same reason: this game's
    database password is generated at install time into
    `<server_dir>/.db_password`, and its schemas are `mangos`/`realmd`/
    `characters` rather than `acore_*`. A default built here would have to
    re-derive both, and a second derivation of a password is how a runner ends
    up authenticating with the wrong one — the closed bug
    `controller_view._db_password()` describes. Passing `sql=None` explicitly
    means "no database", which reports every SQL step as skipped rather than
    running it somewhere else.

    No `dbc=`: `server_dbc` copies DBC files out of a clone, and nothing in
    `manifests/wow-vanilla/` clones anything.

    `world_running` is REQUIRED for the same reason `sql` is, and it is
    checklist 8.7a's guard (`apply.py`,
    `_refuse_direct_sql_into_a_running_world`): a fact about THIS install that
    only the caller holds, which a default here would have to re-derive. This
    family is on that guard's path — `all-stackables` ships three direct
    `world` steps on install here and two on remove. `start_database` is
    optional; absent means the behaviour this route had before T7, which on a
    stopped stack is `container ... is not running` (`bug-checklist §46`).
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
    """Install `manifest` into the Vanilla server at `server_dir`.

    Returns the `ApplyReport`; the caller decides about the restart it names
    (call down / signal up — this function never touches Docker's lifecycle
    itself). Every manifest here declares `build.restart`, because mangosd reads
    `etc/*.conf` and loads the world database once, at startup.

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
