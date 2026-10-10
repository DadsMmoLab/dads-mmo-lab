"""The characters a module question can be answered with (T637).

`mod-ah-bot` asked for a character GUID and an account id typed as numbers, and
nothing in Yu'lon showed either. A prompt of `kind: "character"` is answered by
picking from this server's own characters instead; this module reads them.

Reads only, through the same SQL read seam the Characters tab uses, and run on a
worker thread by the dialog (`ManifestPromptDialog`), never on the GUI thread.

**Playerbots' characters are left out**, using the bot test the dashboard counts
with (`dbreads.bot_clause`): `mod-ah-bot-plus` warns that a bot character used as
the AH bot will likely crash the server, and a server with five hundred bots has
a list nobody could pick from. How many were left out is said under the list.

**A read that cannot be made is a problem sentence, not an empty server**: the
dialog then lets the numbers be typed as before.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from yulon import dbreads
from yulon.catalog.catalog import CatalogEntry
from yulon.log import get_logger

logger = get_logger(__name__)

RACES = {
    1: "Human",
    2: "Orc",
    3: "Dwarf",
    4: "Night Elf",
    5: "Undead",
    6: "Tauren",
    7: "Gnome",
    8: "Troll",
    10: "Blood Elf",
    11: "Draenei",
}
"""AzerothCore's `ChrRaces.dbc` ids, as the 3.3.5a client names them."""

CLASSES = {
    1: "Warrior",
    2: "Paladin",
    3: "Hunter",
    4: "Rogue",
    5: "Priest",
    6: "Death Knight",
    7: "Shaman",
    8: "Mage",
    9: "Warlock",
    11: "Druid",
}
"""AzerothCore's `ChrClasses.dbc` ids."""

NONE_YET = (
    "This server has no characters to pick yet. Create an account and ONE character for the "
    "AH bot (Accounts tab), log it out, then press Install again."
)
ONLINE_MARK = "online: log it out first"


@dataclass(frozen=True)
class Pickable:
    """One character a question can be answered with."""

    guid: int
    name: str
    account_id: int
    account: str
    level: int
    race: int
    klass: int
    online: bool
    bot: bool = False


@dataclass(frozen=True)
class Roster:
    """The pickable characters, or why there are none to show.

    `problem` set means the read could not be made (never "this server has none"):
    `characters` is then empty and the numbers can still be typed.
    """

    characters: tuple[Pickable, ...] = ()
    problem: str = ""
    bots_left_out: int = 0
    database_started: bool = False
    """This read had to start the database (it was down), and nothing here stopped it again."""


def describe(character: Pickable) -> str:
    """`Name — account ACCOUNT, level N, Race Class`, marked when online."""
    race = RACES.get(character.race, f"race {character.race}")
    klass = CLASSES.get(character.klass, f"class {character.klass}")
    text = (
        f"{character.name} — account {character.account}, level {character.level}, {race} {klass}"
    )
    return f"{text} — {ONLINE_MARK}" if character.online else text


def read_roster(
    sql: object | None,
    entry: CatalogEntry,
    server_dir: Path,
    *,
    start_database: Callable[[], bool] | None = None,
) -> Roster:
    """This server's characters, bots left out. Never raises."""
    if sql is None or not isinstance(sql, dbreads.SqlReader):
        return Roster(problem="this install has no way to read the server's database")
    started = False
    if start_database is not None:
        try:
            started = bool(start_database())
        except Exception as exc:  # noqa: BLE001 - any failure to start is one answer here
            logger.warning(f"could not start the database to list characters: {exc}")
            return Roster(problem=f"the database could not be started: {exc}")
        # `started` rides on every answer below, so a caller that cancels can say so.
    schemas = entry.schema_map()
    ops = entry.observability
    table = ops.characters.table if ops is not None else "characters"
    account = ops.characters.account if ops is not None else "account"
    online = ops.characters.online if ops is not None else "online"
    bot = "0"
    if ops is not None:
        marker = dbreads.resolve_marker(entry, server_dir)
        if marker.marker is None:
            return Roster(
                problem=f"could not tell which characters are bots: {marker.problem}",
                database_started=started,
            )
        bot = f"({dbreads.bot_clause(entry, marker.marker)})"
    statement = (
        f"SELECT c.guid, c.name, c.{account}, "
        f"(SELECT a.username FROM {schemas['auth']}.account a WHERE a.id = c.{account}), "
        f"c.level, c.race, c.class, c.{online}, {bot} "
        f"FROM {schemas['characters']}.{table} c ORDER BY c.name, c.guid;"
    )
    try:
        rows = sql.query("characters", statement)
    except Exception as exc:  # noqa: BLE001 - every seam failure is one answer here
        logger.warning(f"could not list this server's characters: {exc}")
        return Roster(
            problem=f"could not read this server's characters: {exc}", database_started=started
        )
    people: list[Pickable] = []
    bots = 0
    for line in rows.splitlines():
        fields = line.split("\t")
        if len(fields) != 9:
            continue
        guid, name, owner, owner_name, level, race, klass, on, is_bot = fields
        try:
            numbers = [int(v) for v in (guid, owner, level, race, klass)]
        except ValueError:
            continue
        if is_bot.strip() == "1":
            bots += 1
            continue
        people.append(
            Pickable(
                numbers[0],
                name,
                numbers[1],
                owner_name,
                numbers[2],
                numbers[3],
                numbers[4],
                on.strip() == "1",
            )
        )
    return Roster(characters=tuple(people), bots_left_out=bots, database_started=started)
