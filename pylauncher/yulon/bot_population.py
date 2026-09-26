"""How many random bots a server runs, read and changed where its install wrote it (T99).

Asked for on #yulon ("is there a way to lower the bot count?"). Every install
pins the population to 500, Min = Max (owner decision 2026-08-28), and each game
keeps that number in its install's own place:

* **TBC, Vanilla, Tortoise** (CMaNGOS family): `AiPlayerbot.MinRandomBots` and
  `MaxRandomBots` in `etc/aiplayerbot.conf`, from the install's conf table
  (`catalog.json`). `./etc` is bound into the containers, so a restart applies it.
* **WotLK** (AzerothCore): `AC_AI_PLAYERBOT_MIN/MAX_RANDOM_BOTS` in the compose
  override's environment, from `azerothcore.world_env`. The environment WINS
  over `playerbots.conf` (`Config.cpp:540-552`), and a container keeps the
  environment it was created with, so a recreate applies it. Only those two
  lines of the world service's `environment:` are changed, every other byte
  kept (T99 fix wave: re-rendering the whole file dropped hand lines and, on a
  flaky SELinux probe, the `:z` binds). Rebuilding the file is Reset to
  default's job, not this box's.

`where()` reads that off the catalog, never off a game id. The Bots tab's box
sets Min = Max = N, as the install does; the Tuning tab shows the CMaNGOS file's
own keys as rows (`read().rows`).

**The range is read off the conf, never invented.** The conf states no upper
limit. On TBC and Vanilla the bot-character pool does: cmangos playerbots makes
at most 9 characters per bot account (`RandomPlayerbotFactory.cpp:755-760` and
`:827-853`, read on m910q 2026-09-24; 10 only under `MANGOSBOT_TWO`, which is
WotLK and not a cmangos entry here) for `AiPlayerbot.RandomBotAccountCount`
accounts, so a number above accounts x 9 is a number the server cannot reach.
Tortoise's module has no account-count key (T30 Half 1) and mod-playerbots sizes
its accounts from `MaxRandomBots` (`playerbots.conf.dist:93-97`), so there the
only limit is the whole number the core reads (`NO_CEILING`).

Nothing here imports Qt, and every function here touches the disk: the view runs
them on its job runner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from yulon import reset_defaults, tuning
from yulon.catalog import bot_count, composegen
from yulon.catalog.catalog import CatalogEntry, ConfPatch
from yulon.catalog.families import conf
from yulon.catalog.families.cmangos import ETC_DIR
from yulon.catalog.installer import InstallerError
from yulon.log import get_logger
from yulon.manifest import ConfKey

logger = get_logger(__name__)

MIN_KEY = bot_count.MIN_KEY
MAX_KEY = bot_count.MAX_KEY
ACCOUNT_KEY = "AiPlayerbot.RandomBotAccountCount"
MIN_ENV = bot_count.MIN_ENV
MAX_ENV = bot_count.MAX_ENV
CONF_NAME = bot_count.CONF_NAME
CONF_FILE = f"{ETC_DIR}/{CONF_NAME}"

CHARACTERS_PER_ACCOUNT = 9
"""cmangos playerbots' own cap outside `MANGOSBOT_TWO` (`RandomPlayerbotFactory.cpp:755-760`)."""

NO_CEILING = bot_count.LARGEST
"""The largest whole number the cores read these keys as (`GetIntDefault`, `GetOption<int32>`).

Not a recommendation: where the conf states no limit, this is the honest one.
"""

CARD = ("core", "aiplayerbot")
"""The Tuning tab's card key, `(family, module_id)`, for the CMaNGOS file's own rows."""

CARD_NAME = "Playerbots (server settings)"

Route = Literal["conf", "env"]

_COUNT_KEYS = (MIN_KEY, MAX_KEY, ACCOUNT_KEY)
_LABELS = {
    MIN_KEY: "Random bots (lowest)",
    MAX_KEY: "Random bots (highest)",
    ACCOUNT_KEY: "Bot accounts",
}
_EXPLAIN = {
    MIN_KEY: (
        "The server keeps between the lowest and the highest number of random bots online. "
        "The Random bots box on the Bots tab sets both to one number."
    ),
    MAX_KEY: (
        "The server keeps between the lowest and the highest number of random bots online. "
        "The Random bots box on the Bots tab sets both to one number."
    ),
    ACCOUNT_KEY: (
        f"How many accounts the bots are made on. Each holds at most "
        f"{CHARACTERS_PER_ACCOUNT} bot characters, so this caps how many random bots can exist."
    ),
}

MISSING = (
    "{file} is not on disk, so there is no bot count to read or change. Repair the server to "
    "write it again."
)
FOREIGN = (
    "this server's compose files were not made by Yu'lon, so Yu'lon does not rewrite its "
    "{file}. Change the bot count in that file yourself."
)
UNREADABLE = "{file} could not be read ({exc})"
NOT_UTF8 = "{file} is not UTF-8 text, so Yu'lon will not read or rewrite it ({exc})"
NO_ROUTE = "Yu'lon does not know where {game} keeps its bot count"
NOT_A_NUMBER = "{value!r} is not a whole number of bots"
OUT_OF_RANGE = "{n} is outside what this server allows: 0 to {ceiling} ({why})"
WRITE_FAILED = "{file} could not be written ({exc}); it was left as it was"
CEILING_ACCOUNTS = (
    "{accounts} bot accounts ({key}) x {per} characters each; raise {key} on the Tuning tab "
    "for more"
)
CEILING_NONE = "the server's own settings set no upper limit"
CEILING_UNKNOWN = (
    "{key} is {value!r}, which is not a number of bot accounts, so the server's own settings "
    "give no upper limit Yu'lon can read"
)
ENV_MISSING = (
    "{name} is not in the {service} environment of {file}, so Yu'lon does not know which line "
    "the server reads. Reset to default on the Tuning tab writes the file again as installed."
)
ENV_TWICE = (
    "{name} is in the {service} environment of {file} more than once, so Yu'lon does not know "
    "which line the server reads. Remove the extra line, or use Reset to default on the Tuning "
    "tab."
)


class BotCountError(RuntimeError):
    """A refusal a player reads, raised before anything is written."""


@dataclass(frozen=True)
class Reading:
    """What one install's bot population is now, and what may be asked of it."""

    file: str
    route: Route
    min: int | None = None
    max: int | None = None
    ceiling: int = NO_CEILING
    ceiling_why: str = CEILING_NONE
    problem: str | None = None
    """Why the count cannot be read or changed here; `None` when it can."""
    rows: tuple[tuning.TuningRow, ...] = field(default_factory=tuple)
    """The Tuning tab's rows for the CMaNGOS file's own keys. Empty for WotLK."""


@dataclass(frozen=True)
class Written:
    """What one press did: the file, its backup, and the job that makes it count."""

    file: str
    rule: tuning.ApplyRule
    backup: Path | None
    """`None` when the number was already N and nothing was written."""
    before: int | None
    after: int

    @property
    def changed(self) -> bool:
        return self.backup is not None


def where(entry: CatalogEntry) -> tuple[str, Route] | None:
    """The file this game's install writes its bot count into, and how, or `None`."""
    native_block = entry.install.native
    if native_block is None:
        return None
    if native_block.family == "azerothcore":
        env = composegen.world_env(entry)
        if MIN_ENV in env and MAX_ENV in env:
            return (composegen.OVERRIDE_FILE, "env")
        return None
    if native_block.family == "cmangos" and native_block.cmangos is not None:
        patch = native_block.cmangos.conf.files.get(CONF_NAME)
        if patch is not None and MIN_KEY in patch.keys and MAX_KEY in patch.keys:
            return (CONF_FILE, "conf")
    return None


def _table(entry: CatalogEntry) -> ConfPatch | None:
    native_block = entry.install.native
    if native_block is None or native_block.cmangos is None:
        return None
    return native_block.cmangos.conf.files.get(CONF_NAME)


def conf_keys(entry: CatalogEntry) -> dict[str, ConfKey]:
    """The install table's `aiplayerbot.conf` keys, as the Tuning tab declares them.

    The keys Yu'lon writes into that file and nothing else: they are the ones a
    player has a reason to look for, and each has a value the install chose.
    Only the three counts are typed (a whole number, not below 0 -- the cores
    read them unsigned); the rest are text boxes by `tuning`'s safety rule.
    """
    table = _table(entry)
    if table is None:
        return {}
    keys: dict[str, ConfKey] = {}
    for key, value in table.keys.items():
        counted = key in _COUNT_KEYS
        keys[key] = ConfKey(
            key=key,
            default=value,
            label=_LABELS.get(key),
            explain=_EXPLAIN.get(key),
            type="int" if counted else None,
            min=0 if counted else None,
        )
    return keys


def _rows(entry: CatalogEntry, text: str | None) -> tuple[tuning.TuningRow, ...]:
    return tuple(
        tuning.TuningRow(
            module_id=CARD[1],
            module_name=CARD_NAME,
            family=CARD[0],
            file=CONF_FILE,
            key=key,
            label=spec.label or key,
            explain=spec.explain,
            type=spec.type,
            min=spec.min,
            max=spec.max,
            default=spec.default,
            current=None if text is None else tuning.conf_value(text, key),
            installed=True,
            backend="conf",
            read_only_reason=None,
        )
        for key, spec in conf_keys(entry).items()
    )


def _number(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def _read_text(path: Path) -> str:
    """Exact text (`newline=""`), strictly decoded: a byte it cannot read it must not write."""
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def _world_service(entry: CatalogEntry) -> str:
    """The compose service that runs the world: the templates name it as its container."""
    return entry.container_spec().world


def _env_line(lines: list[str], entry: CatalogEntry, name: str, file: str) -> int:
    """The index of the ONE line of `name` in the world service's environment.

    Raises:
        BotCountError: the key is not there, or is there more than once.
    """
    service = _world_service(entry)
    found = bot_count.env_lines(lines, service).get(name, [])
    if not found:
        raise BotCountError(ENV_MISSING.format(name=name, service=service, file=file))
    if len(found) > 1:
        raise BotCountError(ENV_TWICE.format(name=name, service=service, file=file))
    return found[0]


def _set_env_value(line: str, value: str) -> str:
    """The same line with its value replaced: indent, key, separator, quotes, tail, CR kept."""
    ending = "\r" if line.endswith("\r") else ""
    match = bot_count.ENV_LINE.match(line[: len(line) - len(ending)])
    if match is None:  # `bot_count.env_lines` only hands over lines that match
        raise BotCountError(f"not a `KEY: value` line: {line!r}")
    old = match.group("value")
    quote = old[0] if old[:1] in ('"', "'") else ""
    return f"{match.group('head')}{quote}{value}{quote}{match.group('tail')}{ending}"


def patch_env(text: str, entry: CatalogEntry, file: str, values: dict[str, str]) -> str:
    """`text` with each named key's value replaced in the world's environment, nothing else.

    Split on LF alone, so a CRLF file's CR stays on every line.

    Raises:
        BotCountError: a key is missing from that environment, or is in it twice.
    """
    lines = text.split("\n")
    for name, value in values.items():
        index = _env_line(lines, entry, name, file)
        lines[index] = _set_env_value(lines[index], value)
    return "\n".join(lines)


def _foreign(server_dir: Path) -> bool:
    base = server_dir / composegen.BASE_FILE
    return not (base.is_file() and composegen.is_ours(base))


def read(entry: CatalogEntry, server_dir: Path) -> Reading:
    """This install's bot population as its files say it now. Never raises for a file."""
    spot = where(entry)
    if spot is None:
        return Reading("", "conf", problem=NO_ROUTE.format(game=entry.name))
    file, route = spot
    path = server_dir / file
    if route == "env" and _foreign(server_dir):
        return Reading(file, route, problem=FOREIGN.format(file=path.name))
    try:
        text = _read_text(path)
    except FileNotFoundError:
        return Reading(file, route, problem=MISSING.format(file=path.name))
    except UnicodeDecodeError as exc:
        return Reading(file, route, problem=NOT_UTF8.format(file=path.name, exc=exc))
    except OSError as exc:
        return Reading(file, route, problem=UNREADABLE.format(file=path.name, exc=exc))
    if route == "conf":
        low = _number(tuning.conf_value(text, MIN_KEY))
        high = _number(tuning.conf_value(text, MAX_KEY))
        ceiling, why = _ceiling(tuning.conf_value(text, ACCOUNT_KEY))
        return Reading(file, route, low, high, ceiling, why, rows=_rows(entry, text))
    lines = text.split("\n")
    try:
        low, high = (
            _number(bot_count.env_value(lines[_env_line(lines, entry, name, path.name)]))
            for name in (MIN_ENV, MAX_ENV)
        )
    except BotCountError as exc:
        return Reading(file, route, problem=str(exc))
    return Reading(file, route, low, high)


def _ceiling(accounts_text: str | None) -> tuple[int, str]:
    """The box's upper bound from `RandomBotAccountCount`, and the sentence that explains it.

    Accounts x 9 when the key is a positive number, never above `NO_CEILING`
    (Qt's spin box and the cores both stop at a 32-bit int). Absent: no limit.
    0, negative or not a number: no limit Yu'lon can read -- NOT a 0..0 box
    (Codex medium); the question's warning above the installed 500 stands.
    """
    if accounts_text is None:
        return NO_CEILING, CEILING_NONE
    accounts = _number(accounts_text)
    if accounts is None or accounts <= 0:
        return NO_CEILING, CEILING_UNKNOWN.format(key=ACCOUNT_KEY, value=accounts_text)
    why = CEILING_ACCOUNTS.format(accounts=accounts, key=ACCOUNT_KEY, per=CHARACTERS_PER_ACCOUNT)
    return min(accounts * CHARACTERS_PER_ACCOUNT, NO_CEILING), why


def write(entry: CatalogEntry, server_dir: Path, n: int) -> Written:
    """Set Min = Max = `n`, backing the file up first; nothing written if it already says `n`.

    Read fresh here, not handed in: the range and the file's case are the disk's
    answer at the moment of writing. CMaNGOS: the two keys through `conf.patch`,
    the install's own writer, so every active copy of each key moves and nothing
    else does. WotLK: the two env lines' values in the world's `environment:`,
    every other byte of the override kept (`patch_env`).

    Raises:
        BotCountError: a refusal a player reads; nothing was written.
    """
    if isinstance(n, bool) or not isinstance(n, int):
        raise BotCountError(NOT_A_NUMBER.format(value=n))
    reading = read(entry, server_dir)
    if reading.problem is not None:
        raise BotCountError(reading.problem)
    if n < 0 or n > reading.ceiling:
        raise BotCountError(
            OUT_OF_RANGE.format(n=n, ceiling=reading.ceiling, why=reading.ceiling_why)
        )
    file, route = reading.file, reading.route
    rule: tuning.ApplyRule = (
        tuning.file_rule(file) if route == "conf" else reset_defaults.apply_rule(file)
    )
    if reading.min == n and reading.max == n:
        return Written(file, rule, None, reading.max, n)
    path = server_dir / file
    try:
        if route == "conf":
            table = _table(entry)
            text = conf.patch(
                _read_text(path),
                ConfPatch(
                    keys={MIN_KEY: str(n), MAX_KEY: str(n)},
                    match_commented=table.match_commented if table is not None else False,
                ),
                {},
            )
        else:
            text = patch_env(_read_text(path), entry, path.name, {MIN_ENV: str(n), MAX_ENV: str(n)})
    except (OSError, UnicodeDecodeError, InstallerError) as exc:
        raise BotCountError(WRITE_FAILED.format(file=path.name, exc=exc)) from exc
    try:
        made = tuning.backup(path)
    except (OSError, tuning.TuningError) as exc:
        raise BotCountError(WRITE_FAILED.format(file=path.name, exc=exc)) from exc
    try:
        conf.replace_file(path, text)
    except InstallerError as exc:
        # The file is as it was (`replace_file` is atomic), so the backup of it
        # is a copy of what is still there: removed, so Revert does not offer it.
        made.unlink(missing_ok=True)
        raise BotCountError(WRITE_FAILED.format(file=path.name, exc=exc)) from exc
    logger.info(f"set {entry.id}'s random bots to {n} in {path}; backup {made.name}")
    return Written(file, rule, made, reading.max, n)


def installed_count(entry: CatalogEntry) -> int | None:
    """The number a fresh Yu'lon install writes (500 today), read off the catalog."""
    spot = where(entry)
    if spot is None:
        return None
    if spot[1] == "env":
        return _number(composegen.world_env(entry).get(MAX_ENV))
    table = _table(entry)
    return _number(table.keys.get(MAX_KEY)) if table is not None else None


def question(entry: CatalogEntry, reading: Reading, n: int) -> str:
    """The Yes/No the Bots tab asks before `write()`, in the words a player reads.

    It says where the number goes, that a backup is made, and exactly which job makes
    the server use it -- a restart for a conf, a recreate for container
    environment -- because the running server keeps what it started with.
    """
    name = Path(reading.file).name
    parts = [f"Set the random bots on this server to {n}?"]
    if reading.route == "conf":
        parts.append(
            f"{name} gets {n} as both its lowest and highest number of random bots. A backup "
            f"of it is made beside it first, and Revert on the {CARD_NAME} card of the Tuning "
            "tab puts it back."
        )
    else:
        parts.append(
            f"{name} gets {n} for both {MIN_ENV} and {MAX_ENV}, the lowest and highest number "
            "of random bots; only those two lines change. A backup of it is made beside it "
            "first."
        )
    installed = installed_count(entry)
    if installed is not None and n > installed:
        parts.append(
            f"That is more than the {installed} Yu'lon installs: more bots need more memory "
            "and processor time."
        )
    parts.append(when_it_counts(reading.route, n))
    return "\n\n".join(parts)


def when_it_counts(route: Route, n: int) -> str:
    """When a written number reaches the running server: the honest sentence, per route."""
    if route == "conf":
        return (
            "The running server keeps its current bots until it is restarted; Yu'lon offers the "
            f"restart when this is done. After the restart the bots log in again, up to {n}, "
            "which can take a few minutes."
        )
    return (
        "The running server keeps its current bots until its containers are RECREATED; Yu'lon "
        f"offers the recreate when this is done. After it the bots log in again, up to {n}, "
        "which can take a few minutes."
    )


@dataclass(frozen=True)
class BotPopulationRoute:
    """The Bots tab's two presses, bound to one install. Both touch the disk: run off-thread."""

    entry: CatalogEntry
    server_dir: Path

    def read(self) -> Reading:
        return read(self.entry, self.server_dir)

    def write(self, n: int) -> Written:
        return write(self.entry, self.server_dir, n)


def bot_count_route(entry: CatalogEntry, server_dir: Path) -> BotPopulationRoute | None:
    """This install's route, or `None` for a game whose install writes no bot count."""
    if where(entry) is None:
        return None
    return BotPopulationRoute(entry, server_dir)
