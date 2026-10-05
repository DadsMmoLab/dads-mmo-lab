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
        if part not in names:
            folded = part.casefold()
            matches = sorted(name for name in names if name.casefold() == folded)
            if not matches:
                return PurePosixPath(*spelled, *parts[index:])
            part = matches[0]
        spelled.append(part)
        here = here / part
    return PurePosixPath(*spelled)


def find(root: Path, rel: str | PurePosixPath) -> Path | None:
    """The file or folder at `rel` under `root`, matched case-insensitively, or None."""
    path = root.joinpath(*on_disk(root, rel).parts)
    return path if os.path.lexists(path) else None
