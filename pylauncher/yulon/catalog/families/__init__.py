"""Where a family id becomes a class — and nothing else (roadmap 7.1).

`installer.installer_for()` reads `install.native.family` off the entry and
asks here. Adding an emulator lineage is one class in this package and one
line in `FAMILIES`; a game of an existing lineage is catalog data only.
"""

from __future__ import annotations

from collections.abc import Mapping

from yulon.catalog.catalog import CatalogEntry
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.catalog.families.cmangos import CmangosInstaller
from yulon.catalog.installer import InstallerError
from yulon.catalog.native import StagedInstaller

FAMILIES: Mapping[str, type[StagedInstaller]] = {
    "azerothcore": AzerothCoreInstaller,
    "cmangos": CmangosInstaller,
}


def is_registered(family: str) -> bool:
    """Does THIS build have an engine for `family`? The question, not the mapping.

    Catalog data outruns the engines that read it. It did in 7.3: the `cmangos`
    blocks landed in group G (task G.4) and the class that consumes them was
    registered four groups later in K.8, and for the length of that gap three
    shipped entries named a family that had no engine above. K.8 closed that
    gap, so as of this commit no shipped entry is in that state — the predicate
    is kept because the NEXT family will reopen it, not because one is open.
    `installer.installer_for()` has to be able to tell that state apart from a
    typo, and it cannot read `FAMILIES` to do it —
    importing this package at its module scope is the cycle its own in-function
    import exists to avoid, and an import moved inside a branch is the same
    coupling with the name filed off. A predicate lives here, where the mapping
    does, and the registry stays this package's business.
    """
    return family in FAMILIES


def family_for(entry: CatalogEntry) -> type[StagedInstaller]:
    """The engine class for `entry`'s `install.native.family`.

    A missing `native` block or an unknown family is an `InstallerError` with
    the sentence a user reads, never a `KeyError` from inside a button.
    """
    native = entry.install.native
    if native is None:
        raise InstallerError(
            f"{entry.name} is not set up for a native install — its catalog entry has no "
            "`install.native` section. Nothing was started."
        )
    try:
        return FAMILIES[native.family]
    except KeyError:
        raise InstallerError(
            f"{entry.name} names an install family this app does not have "
            f"({native.family!r}). That is a bug in the app, not something to fix on this "
            "machine."
        ) from None
