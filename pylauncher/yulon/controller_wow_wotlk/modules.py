"""Module / ALE / mod / keg management for WotLK, driven by JSON manifests (roadmap 2.3).

This is the per-game binding only: the WotLK game id, where its bundled
manifests live, the GitHub location they are refreshed from, and the
AzerothCore database container the SQL steps run in. The behavior —
loading/validating (`yulon.manifest_store`), applying (`yulon.apply`) — is
shared and game-agnostic (style-guide §4). Nothing here knows what any
particular module does; that is all in `manifests/wow-wotlk/` (§3).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from yulon import resources
from yulon.apply import Applier, ApplyReport, DbcCopier, DockerSql, SqlRunner
from yulon.controller_wow_wotlk import docker_ctl
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

GAME = "wow-wotlk"

# The bundled manifest tree that ships with the app (source tree or PyInstaller bundle).
BUNDLED_MANIFESTS_DIR = resources.manifests_dir()

# Raw-GitHub prefix that `<game>/<family>.json` is joined onto when refreshing.
MANIFEST_BASE_URL = (
    "https://raw.githubusercontent.com/DadsMmoLab/dads-mmo-lab/main/pylauncher/manifests"
)

# acore-docker default; matches dml-start.sh's DOCKER_DB_ROOT_PASSWORD default.
DEFAULT_DB_ROOT_PASSWORD = "password"


def load_module(manifest_path: Path) -> Manifest:
    """Load and validate a single manifest file into a typed `Manifest`.

    Raises `yulon.manifest_store.ManifestError` for a missing/unreadable file and
    `pydantic.ValidationError` for anything the schema rejects — including a
    `source.repo` outside the allow-list (README §3a).
    """
    return load_manifest(manifest_path)


def store(root: Path = BUNDLED_MANIFESTS_DIR) -> ManifestStore:
    """The WotLK manifest store over `root` (bundled by default, or a refreshed cache)."""
    return ManifestStore(root, GAME)


def fetcher(cache_root: Path, http: HttpGet = urllib_get) -> ManifestFetcher:
    """A fetcher that mirrors the WotLK manifests from GitHub into `cache_root`."""
    return ManifestFetcher(MANIFEST_BASE_URL, cache_root, http)


def refresh(cache_root: Path, kind: ManifestType, http: HttpGet = urllib_get) -> RefreshResult:
    """Refresh one family of WotLK manifests into `cache_root` (ETag-revalidated)."""
    return fetcher(cache_root, http).refresh(GAME, kind)


def applier(
    server_dir: Path,
    *,
    git: Git | None = None,
    sql: SqlRunner | None = None,
    client_dir: Path | None = None,
    dbc: DbcCopier | None = None,
    db_root_password: str = DEFAULT_DB_ROOT_PASSWORD,
) -> Applier:
    """An `Applier` for the WotLK install at `server_dir`.

    Defaults to running direct SQL through the WotLK DB container
    (`docker_ctl.SPEC.db`); pass `sql=None` explicitly via a caller that has no
    database to get every SQL step reported as skipped instead.
    """
    runner: SqlRunner | None = sql
    if runner is None:
        runner = DockerSql(docker_ctl.SPEC.db, db_root_password)
    return Applier(server_dir, git=git, sql=runner, client_dir=client_dir, dbc=dbc)


def apply_module(
    manifest: Manifest,
    server_dir: Path,
    values: Mapping[str, str] | None = None,
    *,
    client_dir: Path | None = None,
) -> ApplyReport:
    """Install `manifest` into the WotLK server at `server_dir` (the roadmap 2.3 entry point).

    Returns the `ApplyReport`; the caller decides about the rebuild/restart it
    names (call down / signal up — this function never touches Docker's
    lifecycle itself).
    """
    return applier(server_dir, client_dir=client_dir).install(manifest, values)
