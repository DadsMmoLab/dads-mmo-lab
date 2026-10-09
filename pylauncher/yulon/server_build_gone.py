"""A server whose build is gone from Docker, or whose sources Yu'lon no longer builds (T627, T628).

Two facts about a folder, said in plain words wherever a press meets them:

* **The build is gone.** Yu'lon makes a server's images on this machine and names
  them `yulon.local/...`. When Docker has lost those names (a prune), `compose up`
  tries to PULL the image from a registry called `yulon.local` and dies on a
  name-resolution error. The player is told what is missing and the press that
  makes it again, "Rebuild the server...".
* **Rebuild cannot make it.** A Tortoise install made from the retired playerbots
  fork keeps its bots in `modules/mod-playerbots` and has no `modules/TortoiseBots`;
  today's recipe compiles TortoiseBots in and fails about 25 minutes into the
  compile on the fork's own sources (T628, live on yulon-arch 2026-10-09). A
  Rebuild on that folder is refused before it compiles, and a Start that finds the
  build gone there does not name a press that cannot work.

The check is made BEFORE `compose up`, so a Start that cannot run leaves the
database down and the realm row alone (T627). `docker image inspect` is one call per
image, asked only of the games whose one image is the whole build (CMaNGOS);
every other game meets a gone build through `names_a_gone_build()`, on compose's
own failure text.

This module imports no view and no engine, so the controller, the engine and the
Server tab can all say the same sentence.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from pathlib import Path

from yulon import server_build_presses
from yulon.catalog import composegen
from yulon.catalog.catalog import CatalogEntry

RETIRED_BOTS_MODULE = "mod-playerbots"
"""The retired fork's bots, in `<core>/modules/`; today's stack has `TortoiseBots` there."""

_GONE_WORDS = re.compile(
    r"No such image|Unable to find image|pull access denied|repository does not exist"
    r"|failed to resolve reference",
    re.IGNORECASE,
)

NEW_FOLDER_ADVICE = (
    "To run the current server, install it again into a new folder from the Catalog. "
    "This folder's characters and settings stay where they are."
)


def retired_fork_tree(entry: CatalogEntry, server_dir: Path) -> bool:
    """Whether `server_dir` holds the retired fork's sources, which today's recipe cannot build.

    CMaNGOS entries only (Tortoise is the one with a module source). True when a
    module the catalog clones into the core's `modules/` is not there and the
    fork's `mod-playerbots` is. A folder with nothing in it, or with every module
    present, is not this.
    """
    native = entry.install.native
    if native is None or native.cmangos is None:
        return False
    sources = entry.emulator.sources
    if len(sources) < 2:
        return False
    modules = server_dir / sources[0].dest / "modules"
    wanted = [server_dir / source.dest for source in sources[1:]]
    under = [path for path in wanted if path.parent == modules]
    if not under or all(path.is_dir() for path in under):
        return False
    return (modules / RETIRED_BOTS_MODULE).is_dir()


def rebuild_refusal(entry: CatalogEntry, server_dir: Path) -> str | None:
    """The sentence for a Rebuild that would compile the new recipe over the old fork (T628)."""
    if not retired_fork_tree(entry, server_dir):
        return None
    return (
        "This server was made from the retired playerbots fork, and its folder does not hold the "
        "bots module the current build needs. Rebuilding it would compile for about half an hour "
        "and then fail, so nothing was changed. "
        f"{NEW_FOLDER_ADVICE}"
    )


def gone_sentence(entry: CatalogEntry, server_dir: Path) -> str:
    """What the player reads when the server's build is gone from Docker, by what moves it on."""
    if retired_fork_tree(entry, server_dir):
        return (
            "The server's build is gone from Docker, and this server was made from the retired "
            "playerbots fork, which Yu'lon can no longer compile again. Nothing was started. "
            f"{NEW_FOLDER_ADVICE}"
        )
    press = server_build_presses.under_server_build(server_build_presses.REBUILD)
    return (
        "The server's build is gone from Docker, so it cannot start. Nothing was started. "
        f"Press {press} to compile it again."
    )


def refusal_before_start(
    entry: CatalogEntry | None,
    server_dir: Path,
    images_built: Callable[[Sequence[str]], bool | None],
) -> str | None:
    """The sentence for a Start whose one built image is not in Docker, else None (T627).

    `images_built` answers None when Docker would not say, which is not "gone": the
    Start goes on. Only a CMaNGOS entry is asked (its one `server` image is the whole
    build); a name this folder cannot work out is not asked either.
    """
    if entry is None or entry.install.native is None or entry.install.native.cmangos is None:
        return None
    try:
        refs = composegen.built_image_refs(entry, server_dir)
    except (composegen.ComposeGenError, OSError):
        return None
    if images_built(refs) is not False:
        return None
    return gone_sentence(entry, server_dir)


def names_a_gone_build(entry: CatalogEntry | None, text: str) -> bool:
    """Whether Docker's failure text says a `yulon.local` image of this game is not there (T627).

    Compose's pull attempt reads "failed to resolve reference ... lookup yulon.local" beside
    "No such image: <ref>"; either, with this game's image prefix beside it, is the gone build.
    """
    if entry is None or entry.install.native is None:
        return False
    return entry.install.native.image_prefix in text and _GONE_WORDS.search(text) is not None
