"""Where a family id becomes a class — and nothing else (roadmap 7.1).

`installer.installer_for()` reads `install.native.family` off the entry and
asks here. Adding an emulator lineage is one class in this package and one
line in `FAMILIES`; a game of an existing lineage is catalog data only.

Catalog data that names a family this build has no engine for is a DEFECT and
not a supported window: F.3 deleted the bash `Installer` such an entry used to
fall back to, so the only answer left is `family_for()`'s refusal, and the
`is_registered()` predicate that had described the window — unreferenced since
that branch went — was deleted in G.7 rather than left contradicting
`test_spine.py::test_every_shipped_native_entry_reaches_the_class_its_family_id_names`,
which fails on exactly that state.
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
