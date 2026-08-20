"""Where the app's bundled files live, from source and from a PyInstaller build.

From a checkout, `manifests/` sits next to `yulon/` under `pylauncher/` and
the install scripts live in the repo's `archive/guides/`. In a frozen build
PyInstaller puts everything the spec lists as data under its runtime root
(`sys._MEIPASS`), and `build/pylauncher.spec` copies `manifests/` and the
install scripts there under the same relative names — so both layouts are
answered by the two functions here and nobody else has to know about
`_MEIPASS` (style-guide §4: one source of truth for a path).
"""

from __future__ import annotations

import sys
from pathlib import Path


def frozen() -> bool:
    """True inside a PyInstaller build."""
    return bool(getattr(sys, "frozen", False)) and hasattr(sys, "_MEIPASS")


def bundle_root() -> Path:
    """The directory holding `manifests/` (and, when frozen, `archive/guides/`)."""
    if frozen():
        return Path(getattr(sys, "_MEIPASS"))  # noqa: B009 - attribute only exists when frozen
    return Path(__file__).resolve().parents[1]  # pylauncher/


def manifests_dir() -> Path:
    """`<bundle_root>/manifests`."""
    return bundle_root() / "manifests"


def repo_root() -> Path:
    """Where `archive/guides/...` install scripts resolve from.

    Frozen: the bundle root (the spec copies the scripts there). Source: the
    repository root, one level above `pylauncher/`.
    """
    if frozen():
        return bundle_root()
    return bundle_root().parent
