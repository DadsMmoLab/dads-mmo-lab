"""A game client's files as the disk spells them (T227).

Every name this app knows in a client is spelled the retail way --
`Data/lichking.MPQ`, `Data/enUS/locale-enUS.MPQ` -- and on Windows and macOS the
file system does not care how a name is cased. Linux and the Steam Deck do. A
client copied from Windows or unpacked by some tools arrives as
`Data/lichking.mpq`, and on the Centurion proof (yulon-ubuntu2, 2026-10-04) a
complete 3.3.5a client was refused as missing the very file it held.

So a client file is LOOKED UP case-insensitively, one path component at a time,
and the answer is the name on disk: what the player has is never renamed, and a
file written beside it lands on the name that is already there rather than on
a second name that differs only in case.

Exact spelling wins. On a case-sensitive disk two names that differ only in case
are two files, and the one asked for by its own spelling is the one meant.
"""

from __future__ import annotations

import os
from collections.abc import Collection
from pathlib import Path, PurePosixPath


def on_disk(root: Path, rel: str | PurePosixPath) -> PurePosixPath:
    """`rel` with every component that exists under `root` spelled as the disk spells it.

    A component the folder holds under the same spelling stays; otherwise the one
    entry equal to it under `casefold()` is taken (the first in name order, should
    a case-sensitive disk hold several). From the first component with no match,
    the rest keep the spelling given: there is nothing on disk to follow. Never
    raises: a folder that cannot be listed ends the matching the same way.
    """
    parts = PurePosixPath(rel).parts
    here = root
    spelled: list[str] = []
    for index, part in enumerate(parts):
        try:
            names = os.listdir(here)
        except OSError:
            return PurePosixPath(*spelled, *parts[index:])
        found = match(names, part)
        if found is None:
            return PurePosixPath(*spelled, *parts[index:])
        spelled.append(found)
        here = here / found
    return PurePosixPath(*spelled)


def match(names: Collection[str], name: str) -> str | None:
    """The one of `names` that is `name`: itself if there, else its first twin in another case.

    One folder's listing, asked the question `on_disk()` asks of each component,
    for a caller that lists a folder once and places many names in it.
    """
    if name in names:
        return name
    folded = name.casefold()
    return min((other for other in names if other.casefold() == folded), default=None)


def find(root: Path, rel: str | PurePosixPath) -> Path | None:
    """The file or folder at `rel` under `root`, matched case-insensitively, or None."""
    path = root.joinpath(*on_disk(root, rel).parts)
    return path if os.path.lexists(path) else None


# -- the names the map tools open (T260) ----------------------------------------------

DATA_FOLDER = "Data"
"""The client's archive folder, as every map tool spells it."""

RETAIL_LOCALES: tuple[str, ...] = (
    "enGB",
    "enUS",
    "deDE",
    "esES",
    "frFR",
    "koKR",
    "zhCN",
    "zhTW",
    "enCN",
    "enTW",
    "esMX",
    "ruRU",
)
"""The locale folders the CMaNGOS tools look for, in their spelling.

`langs[]` in `contrib/extractor/System.cpp` (mangos-tbc 75f9ae68, :95) and
`searchLocales` in `contrib/vmap_extractor/vmapextract/vmapexport.cpp` (:357-368),
the same twelve in both.
"""

_LOCALE_BY_FOLD = {name.casefold(): name for name in RETAIL_LOCALES}


def retail_locale(name: str) -> str | None:
    """`name` as the tools spell that locale folder (`enus` -> `enUS`), or None: not a locale."""
    return _LOCALE_BY_FOLD.get(name.casefold())


def retail_archive(name: str) -> str | None:
    """`name` as the tools open that archive (`PATCH-ENUS-2.mpq` -> `patch-enUS-2.MPQ`).

    None for a name that is not an `.MPQ`.

    Every archive name the CMaNGOS tools open -- read in their pinned sources
    (`contrib/extractor/System.cpp` and `contrib/vmap_extractor/vmapextract/
    vmapexport.cpp` at mangos-tbc 75f9ae68 and mangos-classic 8ec338a1,
    `tools/extractor/System.cpp` and `tools/vmap_extractor/vmapextract/
    vmapexport.cpp` at tortoise-wow 187af788) -- is a lower-case stem whose
    dash-separated locale part is spelled `enUS`, then `.MPQ`: `common.MPQ`,
    `patch-2.MPQ`, `locale-enUS.MPQ`, `expansion-locale-enUS.MPQ`,
    `patch-enUS-2.MPQ`, `dbc.MPQ`.
    """
    if not name.casefold().endswith(".mpq"):
        return None
    parts = name[: -len(".mpq")].lower().split("-")
    return "-".join(retail_locale(part) or part for part in parts) + ".MPQ"
