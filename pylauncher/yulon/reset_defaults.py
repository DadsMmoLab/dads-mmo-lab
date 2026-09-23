"""Put a server's own settings files back to how Yu'lon installed them (T94).

**What "default" means here**, because the obvious answer is wrong. For TBC,
Vanilla and Tortoise the install writes the database logins, the world port,
SOAP on (T87), `Ra.Enable = 0`, the 500-bot population, the auction-house bot
and Tortoise's `Database.AutoUpdate.*` INTO the conf files, from each entry's
conf table (`catalog.json`). A copy of the upstream `.dist` would lock the
server out of its own database, switch the command channel off and drop the
bots to upstream's count. So the default is what a fresh install writes: the
image's template, patched by that same table with that same install's tokens,
through the same `conf.patch()` the install stage uses.

WotLK is the other case. Its install writes no conf keys, so each conf's
`.dist` sibling IS the as-installed default; its real settings -- the bot
population, and SOAP once the channel is switched on -- are container
environment in `docker-compose.override.yml`, which wins over the conf. That
file's default is what the install's compose stage renders (owner decision 4,
2026-09-23: a player broke it editing the timezone and could not log in), plus
the channel's own keys while its press is live.

**Only the server's own files.** A game's set is exactly its install conf
table, or for WotLK `AZEROTHCORE_CORE_FILES` plus the override. A module's own
conf file is never touched (the owner's decision); `reset()` refuses a file
outside the set as a caller bug.

Nothing here imports Qt. The Tuning tab (`ui/controller_view.py`) draws the
button and runs `reset()` on its job runner.
"""

from __future__ import annotations

from yulon.catalog import composegen
from yulon.catalog.catalog import CatalogEntry
from yulon.catalog.families.cmangos import ETC_DIR
from yulon.log import get_logger

logger = get_logger(__name__)

AZEROTHCORE_ETC = "env/dist/etc"
"""Where an AzerothCore install's confs live, bound into its containers."""

AZEROTHCORE_CORE_FILES: tuple[str, ...] = (
    f"{AZEROTHCORE_ETC}/worldserver.conf",
    f"{AZEROTHCORE_ETC}/authserver.conf",
    f"{AZEROTHCORE_ETC}/modules/playerbots.conf",
)
"""WotLK's own CONF files. Was `controller_view.TUNING_CORE_FILES`; that name is now this tuple.

Here and not in the view so this module needs no Qt, and so the Tuning tab's
read-only list and the reset's conf set are one tuple. The override is NOT in
it: this tuple is also the raw editor's file list (`_tuning_files`), and the
override is not a file that editor shows. `core_files()` adds it.
"""


def core_files(entry: CatalogEntry) -> tuple[str, ...]:
    """This game's own settings files, relative to the server folder, in table order.

    A CMaNGOS game's are exactly its install conf table, under `etc/`; WotLK's
    are its three confs and the compose override. Any other family has none,
    and the tab draws no button.
    """
    native_block = entry.install.native
    if native_block is None:
        return ()
    if native_block.family == "azerothcore":
        return (*AZEROTHCORE_CORE_FILES, composegen.OVERRIDE_FILE)
    if native_block.family == "cmangos" and native_block.cmangos is not None:
        return tuple(f"{ETC_DIR}/{name}" for name in native_block.cmangos.conf.files)
    return ()


def label(file: str) -> str:
    """What the menu and the report call a file: its path under the game's etc folder."""
    for prefix in (f"{AZEROTHCORE_ETC}/", f"{ETC_DIR}/"):
        if file.startswith(prefix):
            return file[len(prefix) :]
    return file
