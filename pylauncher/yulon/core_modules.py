"""The modules a server core already holds, and a player's module that takes one's name (T611).

A core's own `modules/` folder is part of its checkout, and a recipe that lays the server's
`modules/` over it (`COPY modules/ /src/modules/`, Tortoise's) mixes a player's module of the
same name into the core's, file by file, with no warning. So the name is refused wherever it
could come to pass: at Install and Update of the module, and at Rebuild, because the core can
move onto a module of that name between the two.

Pure reads of a folder and one sentence. Names are compared without regard to case, because
the disk a Windows or macOS player has tells no two cases apart and the sentence names both
spellings.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


def names_in(folder: Path) -> tuple[str, ...]:
    """The module folders directly inside `folder`: its directories (its README is a file).

    Empty when the folder is not there or cannot be listed: a core checkout that is not
    there holds no module, and a refusal needs a name to give.
    """
    try:
        return tuple(sorted(child.name for child in folder.iterdir() if child.is_dir()))
    except OSError:
        return ()


def same_name_in(name: str, core_names: Iterable[str]) -> str | None:
    """The core's module that `name` collides with, as the core spells it, or None."""
    wanted = name.casefold()
    return next((core for core in core_names if core.casefold() == wanted), None)


def sentence(name: str, core_name: str, core_folder: str) -> str:
    """Why `name` cannot be built into this server, naming both modules and where the core's is."""
    return (
        f"The module {name} has the same name as {core_name}, a module the server's own "
        f"source already holds ({core_folder}/{core_name}). The build lays a module's "
        f"folder over the server's own one file at a time, so the two would be mixed into "
        f"one module. Remove {name}, or ask its author to give it another name."
    )
