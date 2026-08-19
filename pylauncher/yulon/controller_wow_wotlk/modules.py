"""Module / ALE / mod management driven by JSON manifests.

Loads and validates manifests from manifests/wow-wotlk/ (and, later, fetched
from GitHub). See pyplan/README.md §6 for the schema.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def load_module(manifest_path: Path) -> dict[str, Any]:
    """Load and validate a single module/manifest JSON file.

    Returns a raw dict at this JSON-parse boundary; Phase 2 replaces this with
    a typed dataclass/Pydantic model per style-guide.md §2.
    """
    raise NotImplementedError


def apply_module(manifest: dict[str, Any]) -> None:
    """Install/configure a module from its manifest. Placeholder — Phase 2."""
    raise NotImplementedError
