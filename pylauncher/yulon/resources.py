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


def lua_dir() -> Path:
    """`<bundle_root>/lua` — the My Party bridge scripts, one directory per family.

    Data rather than code, on the same reasoning as `installers_dir()`: these are
    files the app copies into somebody's server folder, not modules it imports.
    The layout (a family directory per group of scripts, flattened at deploy)
    is the bash launcher's `cli/lua/<family>/*.lua`, kept so the two trees'
    scripts stay comparable file for file.
    """
    return bundle_root() / "lua"


def theme_images_dir() -> Path:
    """`<bundle_root>/yulon/ui/theme_images` -- the SVGs the theme's sheet names (T193).

    The checkbox tick, the radio dot, the combo arrow and the spin box's − and +.
    Qt's style sheets draw a sub-control's glyph only from an `image:` URL, so
    these are files on disk rather than painted code, and they ship as a data
    tree in `build/pylauncher.spec` like `lua/`.
    """
    return bundle_root() / "yulon" / "ui" / "theme_images"
