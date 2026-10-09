"""The modules a server core already holds, and a player's module that takes one's name (T611).

A core's own `modules/` folder is part of its checkout, and a recipe that lays the server's
`modules/` over it (`COPY modules/ /src/modules/`, Tortoise's) puts a player's module of the
same name on top of the core's, so the player's files overwrite the core module's, with no
warning. So the name is refused wherever it could come to pass: at Install and Update of the
module, and at Rebuild, because the core can move onto a module of that name between the two.

Pure reads of a folder and one sentence. Names are compared without regard to case, but not
because a disk would mix them: the build runs on a Linux file system that tells `mod-x` and
`Mod-X` apart, so they stay two folders and both would be compiled in, and the module
loader's name for each (`Add<folder>Scripts`) would then be defined twice or built twice. The
sentence says which of the two problems it is.
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


def sentence(name: str, core_name: str, core_folder: str, *, installed: bool = True) -> str:
    """Why `name` cannot be built into this server, naming both modules and where the core's is.

    Two different problems share the check. The same spelling is one folder name in two
    parents, and the build lays the player's over the core's, so the player's files overwrite
    the core module's. A name that differs only in case stays two folders in the build (its
    file system tells cases apart), and both would be built in: two copies of one module.
    `installed` is whether there is anything of the player's module to remove: a refused
    first Install leaves nothing, so it is not told to remove it.
    """
    where = f"{core_folder}/{core_name}"
    if name == core_name:
        what = (
            f"The module {name} has the same name as a module the server's own source already "
            f"holds ({where}). The build lays a module's folder over the server's own, so "
            f"{name}'s files would overwrite that module's."
        )
    else:
        what = (
            f"The module {name} and the server's own {core_name} ({where}) would both be built "
            "into the server: two copies of one module side by side."
        )
    if installed:
        return f"{what} Remove {name}, or ask its author to give it another name."
    return f"{what} Nothing was installed. Ask its author to rename it, or pick another module."
