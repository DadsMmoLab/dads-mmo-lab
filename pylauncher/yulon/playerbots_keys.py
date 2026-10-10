"""Which names mod-playerbots reads its settings under, asked of each server's own checkout (T657).

mod-playerbots ed54b459 (#2854, 2026-10-06) renamed every config key from
`AiPlayerbot.<Name>` to `Playerbots.<Name>`, and its `PlayerbotAIConfig.cpp` reads
only the new names; its PR text: "A server that keeps its old conf gets defaults".
AzerothCore's environment override follows the key (`Config.cpp` IniKeyToEnvVarKey:
`"AC_"` + upper snake case), so `AC_AI_PLAYERBOT_MIN_RANDOM_BOTS` became
`AC_PLAYERBOTS_MIN_RANDOM_BOTS` with it.

**Only the prefix moved.** `conf/playerbots.conf.dist` at the pin 037c0141 and the tip
79bd4281 (887 and 888 assignment keys, commented ones included), with `AiPlayerbot.`
read as `Playerbots.`, differ in three keys and none is a rename: `FleeingEnabled`
went (2bd0f91b), `ReactStrategies` and `RandomBotReactStrategies` came (7fa0623c).
The `GetOption` literals in `src/` agree (395 and 396). So a key's own name is kept
and its prefix is swapped, in both directions.

**Three kinds of name the swap never touches**, read in both trees:

* `Playerbots.Updates.EnableDatabases` had that name before the rename too
  (`src/Script/Playerbots.cpp:52` at 037c0141 and at 79bd4281), so it is no evidence
  of either side and is never renamed;
* `PlayerbotsDatabaseInfo` and `PlayerbotsDatabase.WorkerThreads/SynchThreads` have no
  dot after `Playerbots` and never changed;
* their environment names, which DO start `AC_PLAYERBOTS_` on both sides
  (`NEVER_RENAMED_ENV`): renaming those back would switch the bots' database off.

**Read, never guessed by date or commit.** `module_prefix()` reads the server folder's
`modules/mod-playerbots`: the keys of its `conf/playerbots.conf.dist`, and when that
says nothing, the `GetOption` literals of its `src/PlayerbotAIConfig.cpp`. Only one
side present is an answer; neither, both, or no checkout is `None`, and `None` changes
nothing anywhere (every name stays as the catalog and this code spell it, the old one).

The catalog and this code keep spelling the old names (`AiPlayerbot.MinRandomBots`,
`AC_AI_PLAYERBOT_COMMAND_SERVER_PORT`): `key()` and `env()` give the name a server
reads. Nothing here reads anything but the two module files, and nothing here writes:
`playerbots_rename` does the renaming on disk.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path

from yulon.log import get_logger

logger = get_logger(__name__)

OLD = "AiPlayerbot."
NEW = "Playerbots."
OLD_ENV = "AC_AI_PLAYERBOT_"
NEW_ENV = "AC_PLAYERBOTS_"
"""`env_name_for()` of each prefix: `.` resets the case rule, so a key's tail spells the
same after either prefix and only these heads differ."""

MODULE = "modules/mod-playerbots"
DIST = f"{MODULE}/conf/playerbots.conf.dist"
SOURCE = f"{MODULE}/src/PlayerbotAIConfig.cpp"

CONF = "env/dist/etc/modules/playerbots.conf"
"""The conf the world reads (the catalog's `confs_from_dist` and `prefix_conf_file`)."""

NEVER_RENAMED = frozenset({"Playerbots.Updates.EnableDatabases"})
NEVER_RENAMED_ENV = frozenset(
    {
        "AC_PLAYERBOTS_UPDATES_ENABLE_DATABASES",
        "AC_PLAYERBOTS_DATABASE_INFO",
        "AC_PLAYERBOTS_DATABASE_WORKER_THREADS",
        "AC_PLAYERBOTS_DATABASE_SYNCH_THREADS",
    }
)

_DIST_KEY = re.compile(r"^[ \t]*#?[ \t]*((?:AiPlayerbot|Playerbots)\.[A-Za-z0-9_.]+)[ \t]*=", re.M)
_SOURCE_KEY = re.compile(r"\"((?:AiPlayerbot|Playerbots)\.[A-Za-z0-9_.%]*)")


def prefix_in(keys: Iterable[str]) -> str | None:
    """`OLD` or `NEW` when the keys use one prefix only (`NEVER_RENAMED` aside), else None."""
    old = new = False
    for key in keys:
        if key.startswith(OLD):
            old = True
        elif key.startswith(NEW) and key not in NEVER_RENAMED:
            new = True
    if old == new:
        return None
    return OLD if old else NEW


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def module_prefix(server_dir: Path) -> str | None:
    """The prefix this server's mod-playerbots checkout reads, or None when it cannot be told."""
    dist = _read(server_dir / DIST)
    if dist is not None:
        found = prefix_in(_DIST_KEY.findall(dist))
        if found is not None:
            return found
    source = _read(server_dir / SOURCE)
    if source is not None:
        return prefix_in(_SOURCE_KEY.findall(source))
    return None


def other(prefix: str) -> str:
    return NEW if prefix == OLD else OLD


def key(name: str, prefix: str | None) -> str:
    """`name` (either prefix) as a module reading `prefix` spells it; `None` keeps it."""
    if prefix is None or name in NEVER_RENAMED:
        return name
    for head in (OLD, NEW):
        if name.startswith(head):
            return prefix + name[len(head) :]
    return name


def env(name: str, prefix: str | None) -> str:
    """An environment name (either prefix) for a module reading `prefix`; `None` keeps it."""
    if prefix is None or name in NEVER_RENAMED_ENV:
        return name
    want = NEW_ENV if prefix == NEW else OLD_ENV
    for head in (OLD_ENV, NEW_ENV):
        if name.startswith(head):
            return want + name[len(head) :]
    return name


def env_map(values: Mapping[str, str], prefix: str | None) -> dict[str, str]:
    """`values` with every name as `env()` spells it; a later name wins a clash, as in a merge."""
    return {env(name, prefix): value for name, value in values.items()}


def reading_order(name: str, prefix: str | None, *, of_env: bool = False) -> tuple[str, ...]:
    """The spellings of `name` a reader looks for, the server's own first, then the other.

    For a file a press has not renamed yet: what is on disk is what the next press
    renames, so it is read rather than missed. `None` reads the name as given first.
    """
    spell = env if of_env else key
    first = spell(name, prefix) if prefix is not None else name
    second = spell(first, other(prefix if prefix is not None else _prefix_of(first, of_env)))
    return tuple(dict.fromkeys((first, second)))


def _prefix_of(name: str, of_env: bool) -> str:
    if of_env:
        return NEW if name.startswith(NEW_ENV) else OLD
    return NEW if name.startswith(NEW) else OLD
