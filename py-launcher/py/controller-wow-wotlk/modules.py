"""Module / ALE / mod management driven by JSON manifests.

Loads and validates manifests from manifests/wow-wotlk/ (and, later, fetched
from GitHub). See pyplan/README.md §6 for the schema.
"""

from __future__ import annotations


def load_module(manifest_path: str) -> dict:
    """Load and validate a single module/manifest JSON file."""
    raise NotImplementedError


def apply_module(manifest: dict) -> None:
    """Install/configure a module from its manifest. Placeholder — Phase 2."""
    raise NotImplementedError