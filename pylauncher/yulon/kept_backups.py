"""Where an uninstall keeps a server's backups, and how the Maintenance tab finds them again (T677).

An uninstall deletes the server folder, and the backups live inside it
(`sql_scripts/backups`). So it first keeps them in a folder BESIDE the server folder,
`<server folder name> - kept backups`, or `... (2)`, `... (3)` when an earlier set is there.
A leaf module on purpose (`os`, `re`, `pathlib` and `links`): `purge` writes the folder and
`backup_shelf` reads it, and neither can import the other.

The name is a function of the disk, not of the clock, so the confirmation can name the exact
folder before the press. It is a sibling of the server folder because removing that folder
already needs write access to the parent, and because a rename inside one disk copies nothing.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from yulon import links

SUFFIX = " - kept backups"
"""A server folder `wowserver` keeps its backups in `wowserver - kept backups`."""

PARTIAL = ".partial"
"""A copy still being made lives at `<kept folder>.partial` and takes its name only when whole."""


def kept_backups_path(server_dir: Path) -> Path:
    """The first free `<name> - kept backups`, then ` (2)`, ` (3)`...: never onto an earlier set.

    A name whose `.partial` exists counts as taken, so a copy that was cut short can never be
    mistaken for, or merged into, a new one.
    """
    base = f"{server_dir.name}{SUFFIX}"
    candidate = server_dir.parent / base
    number = 2
    while os.path.lexists(candidate) or os.path.lexists(str(candidate) + PARTIAL):
        candidate = server_dir.parent / f"{base} ({number})"
        number += 1
    return candidate


def earlier_sets(server_dir: Path) -> list[Path]:
    """The folders earlier uninstalls of a server in THIS folder name left beside it, oldest first.

    Real folders only: a link, a file, or a `.partial` copy is not a kept set. Another server's
    (a different folder name) is never listed. An unreadable parent is an empty answer, since the
    Backups list must still open.
    """
    pattern = re.compile(re.escape(f"{server_dir.name}{SUFFIX}") + r"(?: \((\d+)\))?")
    found: list[tuple[int, Path]] = []
    try:
        entries = list(os.scandir(server_dir.parent))
    except OSError:
        return []
    for entry in entries:
        match = pattern.fullmatch(entry.name)
        if match is None:
            continue
        try:
            if not entry.is_dir(follow_symlinks=False) or links.is_link(entry.path):
                continue
        except OSError:
            continue
        found.append((int(match.group(1) or 1), Path(entry.path)))
    return [path for _number, path in sorted(found)]
