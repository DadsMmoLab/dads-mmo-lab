"""Where the app's bundled files live, from source and from a PyInstaller build.

From a checkout, `manifests/` and `catalog/installers/` sit next to `yulon/`
under `pylauncher/`. In a frozen build PyInstaller puts everything the spec
lists as data under its runtime root (`sys._MEIPASS`) with the SAME relative
names, so both layouts are answered by the functions here and nobody else has
to know about `_MEIPASS` (style-guide §4: one source of truth for a path).

`catalog/installers/` holds what the app reads at install time: the families'
compose templates and, since 7.2, the Steam Deck gaming-mode script. It was
split out of `archive/guides/<game>/` in roadmap 6.0 because that directory is
for humans (guides, HOWTOs) and this one is data.
"""

from __future__ import annotations

import sys
from pathlib import Path


def frozen() -> bool:
    """True inside a PyInstaller build."""
    return bool(getattr(sys, "frozen", False)) and hasattr(sys, "_MEIPASS")


def bundle_root() -> Path:
    """The directory holding `manifests/` and `catalog/installers/`."""
    if frozen():
        return Path(getattr(sys, "_MEIPASS"))  # noqa: B009 - attribute only exists when frozen
    return Path(__file__).resolve().parents[1]  # pylauncher/


def manifests_dir() -> Path:
    """`<bundle_root>/manifests`."""
    return bundle_root() / "manifests"


def installers_dir() -> Path:
    """`<bundle_root>/catalog/installers` — where `install.native.templates` resolves from."""
    return bundle_root() / "catalog" / "installers"
