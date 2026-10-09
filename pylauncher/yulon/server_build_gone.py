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
from yulon.catalog import composegen, native
from yulon.catalog.catalog import CatalogEntry
from yulon.catalog.installer import InstallerError
from yulon.log import get_logger

logger = get_logger(__name__)

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


def _one_image_build(entry: CatalogEntry) -> bool:
    """An entry whose build is ONE image, so that image is all of it (the CMaNGOS ones)."""
    native = entry.install.native
    return native is not None and len(native.images) == 1


def retired_fork_tree(entry: CatalogEntry, server_dir: Path) -> bool:
    """Whether `server_dir` holds the retired fork's sources, which today's recipe cannot build.

    One-image builds only, and not an entry whose own sources carry `mod-playerbots` (Tortoise is
    the one with a module source). True when a
    module the catalog clones into the core's `modules/` is not there and the
    fork's `mod-playerbots` is. A folder with nothing in it, or with every module
    present, is not this.
    """
    if not _one_image_build(entry):
        return False
    sources = entry.emulator.sources
    if len(sources) < 2 or any(source.dest.endswith(RETIRED_BOTS_MODULE) for source in sources):
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


def gone_sentence(entry: CatalogEntry, server_dir: Path, *, after_an_attempt: bool = False) -> str:
    """What the player reads when the server's build is gone from Docker, by what moves it on.

    `after_an_attempt`: said after compose already ran, so the database may be up and the
    realm row already written; "Nothing was started" is true only of the refusal before.
    """
    said = (
        "Part of the server may have started first." if after_an_attempt else "Nothing was started."
    )
    if retired_fork_tree(entry, server_dir):
        return (
            "The server's build is gone from Docker, and this server was made from the retired "
            f"playerbots fork, which Yu'lon can no longer compile again. {said} "
            f"{NEW_FOLDER_ADVICE}"
        )
    press = server_build_presses.under_server_build(server_build_presses.REBUILD)
    return (
        f"The server's build is gone from Docker, so it cannot start. {said} "
        f"Press {press} to compile it again."
    )


def refusal_before_start(
    entry: CatalogEntry | None,
    server_dir: Path,
    images_built: Callable[[Sequence[str]], bool | None],
    *,
    wsl_distro: str | None = None,
    services: Sequence[str] = (),
    compose_images: Callable[[], dict[str, str] | None] | None = None,
) -> str | None:
    """The sentence for a Start whose built image is not in Docker, else None (T627).

    The images asked about are the ones the folder's own compose files name for the
    `services` Start brings up and this app builds (`image_prefix`): that is what compose
    will start, whatever the folder's path is now (a moved folder, a server made inside a
    distro). Only where there is no compose file to read are the names worked out: the id
    the install's record carries, else the folder's hash. A compose file that cannot be
    read or parsed, a distro folder with no usable record, a name Docker will not answer
    for, or a folder with no such image named is no refusal: the Start goes on as it did.
    """
    native_block = entry.install.native if entry is not None else None
    if entry is None or native_block is None or not _one_image_build(entry):
        return None
    refs: tuple[str, ...]
    try:
        if (server_dir / composegen.BASE_FILE).is_file():
            found = compose_images() if compose_images is not None else None
            if found is None:
                logger.warning(
                    f"{server_dir}: compose would not say which images this server runs (it "
                    "failed, timed out or answered nothing readable), so Start did not check "
                    "that its build is in Docker"
                )
                return None
            refs = tuple(
                sorted(
                    {
                        image
                        for service in services
                        if (image := found.get(service, "")).startswith(native_block.image_prefix)
                    }
                )
            )
            if not refs:
                return None
        else:
            try:
                recorded: str | None = native.recorded_install_id(server_dir)
            except InstallerError:
                if wsl_distro is not None:
                    return None  # its Windows spelling names nothing there; a guess would refuse
                recorded = None
            refs = composegen.built_image_refs(entry, server_dir, install_id=recorded)
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
