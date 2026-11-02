"""Module / mod management for CoA, driven by JSON manifests (T855).

The per-game binding only — the TBC sibling's shape, with this game's facts:
the `wow-coa` game id, where its bundled manifests live, the GitHub location
they are refreshed from. Loading and validating (`yulon.manifest_store`) and
applying (`yulon.apply`) are shared and game-agnostic (style-guide §4).

What the tree holds, and why it is its own: ONE `mod`, `consoleport` — the
ConsolePortLK Enhanced controller add-on, eight `client` steps into the
player's own CoA client (`Interface/AddOns`). The Modules tab used to read
WotLK's tree here (`manifests_from: wow-wotlk` until T855), which offered
twenty-one C++ modules and eleven SQL mods the CoA server never builds and the
refresh never reads. `modules`, `ale` and `kegs` indexes exist and are empty:
"there are none" is a fact, and a missing file is an error message in the
user's Modules tab.
"""

from __future__ import annotations

from pathlib import Path

from yulon import resources
from yulon.manifest import Manifest, ManifestType
from yulon.manifest_store import (
    HttpGet,
    ManifestFetcher,
    ManifestStore,
    RefreshResult,
    load_manifest,
    urllib_get,
)

GAME = "wow-coa"

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


def store(
    root: Path = BUNDLED_MANIFESTS_DIR, user_root: Path | None = None, *, user_game: str = GAME
) -> ManifestStore:
    """The CoA manifest store over `root`, with the user layer over it (T855).

    `root` is the bundled tree by default (or a refreshed cache). The second layer
    is where a client add-on the player brought is recorded (`yulon.client_addons`),
    so it is listed on the next start, as on every other game. The shipped ids are
    this tree's own: nothing of WotLK's is offered on a CoA server. `user_game`
    names whose user layer is read (T554's rule, shared with WotLK's store).
    """
    from yulon.controller_wow_wotlk.modules import user_manifests_dir

    return ManifestStore(
        root,
        GAME,
        user_root if user_root is not None else user_manifests_dir(),
        user_game=user_game,
    )


def fetcher(cache_root: Path, http: HttpGet = urllib_get) -> ManifestFetcher:
    """A fetcher that mirrors the CoA manifests from GitHub into `cache_root`."""
    return ManifestFetcher(MANIFEST_BASE_URL, cache_root, http)


def refresh(cache_root: Path, kind: ManifestType, http: HttpGet = urllib_get) -> RefreshResult:
    """Refresh one family of CoA manifests into `cache_root` (ETag-revalidated)."""
    return fetcher(cache_root, http).refresh(GAME, kind)
