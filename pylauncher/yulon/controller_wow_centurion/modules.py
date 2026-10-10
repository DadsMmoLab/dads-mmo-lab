"""Centurion's manifest store: the user layer alone (T613 PR-2).

Centurion ships no manifests (`catalog.json` says `has_manifests: false`): its
TrinityCore core takes none of Yu'lon's modules. What a player brings is a
client add-on (`yulon.client_addons`), recorded in the same user layer WotLK
and Tortoise keep (`<config_dir>/manifests/user/wow-centurion/`), so this store
reads that layer and nothing bundled.
"""

from __future__ import annotations

from pathlib import Path

from yulon import resources
from yulon.controller_wow_wotlk.modules import user_manifests_dir
from yulon.manifest_store import ManifestStore

GAME = "wow-centurion"


def store(user_root: Path | None = None) -> ManifestStore:
    """The Centurion store: no bundled tree (every index empty), the user layer over it."""
    return ManifestStore(
        resources.manifests_dir(),
        GAME,
        user_root if user_root is not None else user_manifests_dir(),
        shipped=False,
    )
