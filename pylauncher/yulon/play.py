"""The reads the Characters tab is built on (8.4a).

Reads live here and writes go to the server through `yulon.commands`, which is
owner answer 7 rather than a layering choice: this app reads rows and the server
changes them.

Three of these guard a failure that does not raise, which is why each has a test
naming what it would look like:

* a character name matched in the wrong case answers "no such character" for a
  character standing in Stormwind -- this tree's `characters.name` is
  case-sensitive, and it was the prior art's own bug;
* a search pattern with a `%` in it matches every item in the game, because
  `%` and `_` are LIKE wildcards and a person typing "50% off" did not mean
  that;
* a gear-set query built for the wrong tree reads a column that means something
  else. `character_inventory.item` is the item INSTANCE guid on all four of
  these trees, and a query that selected it would answer a list of numbers that
  look exactly like item ids and are not (see `equipped`, which records what
  each of the shapes actually does on each tree, measured rather than assumed).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from yulon import commands
from yulon.actions import Outcome, send
from yulon.catalog.catalog import CatalogEntry
from yulon.dbreads import SqlReader
from yulon.log import get_logger

logger = get_logger(__name__)


class Ambiguous(LookupError):
    """More than one character on this server answers to that name.

    Measured on the live Vanilla install, 2026-09-07 (8.4c): `characters.name`
    is `idx_name`, a NON-unique index, and this server holds two characters
    called Joleta and two called Dalnaal -- the bot generator collided and
    nothing stopped it. The read that found it did not answer the wrong set; it
    died with *"Subquery returns more than 1 row"*, and the tab turned that into
    "Joleta is wearing nothing" with the button greyed out, for a character
    wearing a full set.

    It is a named refusal rather than a pick, because there is nothing here to
    pick BY: every command this feature sends addresses a character by name, so
    a set read off one of the two would be mailed to whichever the server's own
    lookup chose. The two are not distinguishable at this layer and saying so is
    the only honest answer.

    It carries the sentence in two lengths because its reader is a BUTTON:
    `summary` is what fits on one, and the whole of it is what a person gets
    when they ask why. The first version put all of it on the label and the
    label ran off the end of the window (`4-two-of-one-name.png` in 8.4c's gate
    folder is the photograph of that).
    """

    def __init__(self, summary: str, detail: str) -> None:
        super().__init__(f"{summary} {detail}")
        self.summary = summary


class NotMeasured(LookupError):
    """This tree has no Play block, so there is nothing here to read it with.

    Raised rather than guessed, and the reason is narrower than the one this
    class was first given. What the block carries are COLUMN NAMES that go into
    a statement unread and a NUMBER that goes onto a button as a promise;
    nothing here can check either against a server nobody has asked. The column
    name that is silently wrong is `item` -- the instance guid, on every one of
    these trees -- and it is the one a person porting a sibling's block half way
    would land on. See `equipped` for what each shape does where.
    """


_ESCAPE = "!"
"""The LIKE escape character, NAMED IN THE STATEMENT.

MySQL's default is the backslash, and `NO_BACKSLASH_ESCAPES` turns that off --
so a pattern escaped with backslashes silently stops being escaped on a server
whose sql_mode somebody tightened, and every search matches everything. `!` is
not special anywhere in this path, and `ESCAPE '!'` says so out loud.
"""

EQUIPPED_SLOTS = 19
"""Bag 0, slots 0 to 18: head to tabard, what a character is wearing.

Bags and the bank are also in `character_inventory`, and a set that swept them
up would mail somebody their whole life rather than their gear.
"""

_ITEM_SEARCH_LIMIT = 50
"""46096 items on the WotLK install. A tab that asked for all of them would
freeze on the answer rather than on the query."""


@dataclass(frozen=True)
class Character:
    """One row of the character list."""

    guid: int
    name: str
    level: int
    online: bool
    account: str


@dataclass(frozen=True)
class Item:
    """One row of an item search."""

    item_id: int
    name: str
    quality: int


def literal(text: str) -> str:
    """One string, as a hex blob, so nothing a person types is ever parsed.

    The same shape `yulon.useraccounts` uses. A name typed as
    `guglu' OR 1=1 --` reaches the server as forty hex digits.
    """
    return "_utf8mb4 X'" + text.encode("utf-8").hex().upper() + "'"


def like_literal(text: str) -> str:
    """A LIKE pattern that searches for what was typed, wildcards and all.

    `%` and `_` are escaped with `_ESCAPE`, and the escape character itself is
    escaped first -- otherwise a person typing `!` would break the next one.
    """
    escaped = text.replace(_ESCAPE, _ESCAPE * 2).replace("%", f"{_ESCAPE}%")
    escaped = escaped.replace("_", f"{_ESCAPE}_")
    return literal(f"%{escaped}%")


@dataclass(frozen=True)
class Stored:
    """What the database says about a typed name, at the moment it was asked.

    Two fields rather than one, and read in one statement, because 8.4d's review
    found the destructive action deciding from the character LIST -- a snapshot
    that is already stale by the time anybody presses anything, on a tree whose
    bot manager logs its bots in and out on a timer. `online` here is a reading
    taken by the press itself.
    """

    name: str
    online: bool


def canonical_character(sql: SqlReader, entry: CatalogEntry, typed: str) -> Stored | None:
    """The stored spelling of a character's name and whether it is logged in,
    or `None` if there is no such character.

    This tree's name column is case-sensitive and so is the server's own lookup,
    so `guglu` is not `Guglu` to either of them -- the prior art answered "not
    online" for an online character on exactly that. Every command this app
    builds uses the answer from here rather than what somebody typed.

    The `online` column comes back with it because the caller that needs it
    needs it FRESH, and asking twice would be two answers about two moments.
    A row that answers no second field is read as not logged in: the direction
    that refuses a destructive command is the direction to be wrong in.
    """
    characters = entry.schema_map()["characters"]
    found = sql.query(
        "characters",
        f"SELECT name, online FROM {characters}.characters "
        f"WHERE UPPER(name) = UPPER({literal(typed)}) LIMIT 1;",
    ).strip()
    if not found:
        return None
    fields = found.splitlines()[0].split("\t")
    return Stored(fields[0].strip(), len(fields) > 1 and fields[1].strip() == "1")


def characters(sql: SqlReader, entry: CatalogEntry) -> tuple[Character, ...]:
    """Every character on this server, with the account each belongs to.

    Ordered by name AND guid: `ORDER BY name` alone is not a total order, and
    8.5a's review found that equal keys can put one row on two pages and another
    on none.
    """
    schemas = entry.schema_map()
    rows = sql.query(
        "characters",
        f"SELECT c.guid, c.name, c.level, c.online, a.username "
        f"FROM {schemas['characters']}.characters c "
        f"JOIN {schemas['auth']}.account a ON a.id = c.account "
        "ORDER BY c.name, c.guid;",
    )
    listed = []
    for line in rows.splitlines():
        fields = line.split("\t")
        if len(fields) != 5:
            continue
        guid, name, level, online, account = fields
        listed.append(Character(int(guid), name, int(level), online == "1", account))
    return tuple(listed)


def find_items(sql: SqlReader, entry: CatalogEntry, text: str) -> tuple[Item, ...]:
    """Items whose name contains what was typed, bounded and escaped."""
    world = entry.schema_map()["world"]
    rows = sql.query(
        "world",
        f"SELECT entry, name, Quality FROM {world}.item_template "
        f"WHERE name LIKE {like_literal(text)} ESCAPE '{_ESCAPE}' "
        f"ORDER BY name, entry LIMIT {_ITEM_SEARCH_LIMIT};",
    )
    found = []
    for line in rows.splitlines():
        fields = line.split("\t")
        if len(fields) != 3:
            continue
        item_id, name, quality = fields
        found.append(Item(int(item_id), name, int(quality)))
    return tuple(found)


def equipped(sql: SqlReader, entry: CatalogEntry, character: str) -> tuple[int, ...]:
    """The item ids this character is wearing, in slot order.

    Ids and not id/count pairs: equipped gear does not stack, and a caller that
    mails this set sends each piece as one.

    The shape of the query is the catalog's, because the trees disagree about
    where the item id lives. AzerothCore's inventory row carries the item
    INSTANCE guid alone and the template id is a join away in
    `item_instance.itemEntry`; the CMaNGOS rows carry BOTH -- `item` (the
    instance guid, and the primary key) and `item_template`.

    **What each shape does where, corrected in 8.4c.** The note here said a
    query built for the wrong tree "does not fail -- it answers a list of
    guids", it was repeated in five places, and it is true of neither of the two
    shapes this catalog offers:

    * the flat shape on AzerothCore names a column that install has not got
      (`character_inventory` there is `guid, bag, slot, item` --
      `.notes/phase8-reads/azerothcore.md:432-441`), so it is an `Unknown
      column` error and loud;
    * the joined shape on a CMaNGOS tree answers the SAME ids the flat one
      does, because `character_inventory.item` is `item_instance.guid` and
      `Player.cpp:3832` writes both from one Item -- a redundant hop, and one
      that can only drop a piece whose `item_instance` row is missing.

    The shape that answers guids is neither of those: it is `template_column`
    set to `item` with no join, which is what porting AzerothCore's column name
    onto CMaNGOS's flat shape produces. That is the mistake the catalog block
    exists to make deliberate, and the flat shape is chosen on these trees
    because it is one hop and cannot lose a row to a missing join.

    (Vanilla's half of that was read on m910q, 2026-09-07:
    `~/vanilla-75b/src/mangos-classic/sql/base/characters.sql:339-347` and
    `src/game/Entities/Player.cpp:3832`. That the two shapes agree ROW FOR ROW
    on a loaded database is a source reading, not a live one -- 8.4c's gate
    counts the difference on the running server.)

    **The name is matched by a JOIN and the owner comes back with the row**,
    which is 8.4c's other correction. It used to be
    `ci.guid = (SELECT guid FROM characters WHERE name = ...)`, a scalar
    subquery on a column no core makes unique -- so on a server with two
    characters of one name the read did not answer the wrong set, it died with
    *"Subquery returns more than 1 row"*, and the tab drew "is wearing nothing"
    over the wreck. Selecting the owner beside the item means the ambiguity is
    visible in the answer rather than in the database engine's error, and it
    costs no extra round trip. See `Ambiguous`.
    """
    schemas = entry.schema_map()
    if entry.play is None:
        raise NotMeasured(
            f"{entry.name} has not measured where it keeps a character's equipped items, "
            "so this app cannot read them"
        )
    block = entry.play.equipped
    inventory = f"{schemas['characters']}.character_inventory"
    owner = (
        f"JOIN {schemas['characters']}.characters ch ON ch.guid = ci.guid "
        f"WHERE ch.name = {literal(character)} "
        f"AND ci.bag = 0 AND ci.slot < {EQUIPPED_SLOTS}"
    )
    if block.instance_table is None:
        statement = (
            f"SELECT ci.{block.template_column}, ch.guid FROM {inventory} ci "
            f"{owner} ORDER BY ch.guid, ci.slot;"
        )
    else:
        instances = f"{schemas['characters']}.{block.instance_table}"
        statement = (
            f"SELECT ii.{block.template_column}, ch.guid FROM {inventory} ci "
            f"JOIN {instances} ii ON ii.guid = ci.{block.inventory_column} "
            f"{owner} ORDER BY ch.guid, ci.slot;"
        )
    rows = sql.query("characters", statement)
    worn: dict[str, list[int]] = {}
    for line in rows.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        worn.setdefault(fields[1] if len(fields) > 1 else "", []).append(int(fields[0]))
    if len(worn) > 1:
        owners = ", ".join(sorted(worn))
        raise Ambiguous(
            f"{len(worn)} characters here are called {character}",
            f"(guids {owners}), and every command this app sends names a character rather "
            "than a guid -- so it cannot tell you which one's gear this is, nor send it to "
            "the right one.",
        )
    return tuple(next(iter(worn.values()))) if worn else ()


# -- what the tab is handed --------------------------------------------------


class InstallPlay:
    """One install's characters, as the Characters tab presses them.

    Reads go to the database and writes go to the server, which is owner
    answer 7. Two things are true of every action here and are done in one place
    rather than in eight:

    * **the name is the server's, not the typist's.** `characters.name` is
      case-sensitive on these trees and so is the server's own lookup, so every
      action canonicalises first and sends the stored spelling. The prior art
      answered "not online" for a character standing in Stormwind on exactly
      this.
    * **a name nobody has never reaches the server.** Its refusal would name a
      character that does not exist, which reads like the server disagreeing
      about a character rather than a typo.

    **What canonicalising does NOT give is identity.** It fixes case; it is not
    a lock. Between the lookup and the command a character can be renamed or
    deleted and its name taken by another, and the command would then act on
    the replacement (8.4a's adversarial review). These cores address these
    commands by name and offer nothing else to address them by -- there is no
    guid form of `character level` -- so the honest position is that this is a
    real gap with no fix at this layer, narrow (it needs a rename or a delete in
    the same second, on a server whose administrator is the person pressing the
    button) and worth knowing about rather than papering over.
    """

    def __init__(
        self,
        entry: CatalogEntry,
        server_dir: Path,
        *,
        sql: SqlReader,
        channel_for_saved: Callable[[], object | None],
    ) -> None:
        self.entry = entry
        self.server_dir = server_dir
        self._sql = sql
        self._channel_for_saved = channel_for_saved

    @staticmethod
    def for_entry_is_possible(entry: CatalogEntry) -> bool:
        """Whether this tree has measured what the tab would need.

        The tab draws what the tree has: a button on a tree whose block is
        absent would send a command nobody has run against it.
        """
        return entry.play is not None

    @property
    def mail_item_cap(self) -> int:
        """How many attachments one mail carries HERE, from this entry's block.

        No number in this file is right for every tree: `MAX_MAIL_ITEMS` is 12
        on the WotLK and TBC installs and 1 on the Vanilla one -- the same line
        of the same header in two CMaNGOS checkouts on m910q, read 2026-09-07.

        The refusal used to be a returned 1, which read as a refusal only while
        no tree carried a cap of one. Vanilla does, so from 8.4c that answer was
        indistinguishable from a measurement, and a gear button on an unmeasured
        tree would have promised a mail per piece with nothing behind it.
        """
        if self.entry.play is None:
            raise NotMeasured(
                f"{self.entry.name} has not measured how many items one of its mails carries, "
                "so this app cannot say how many mails a gear set would take"
            )
        return self.entry.play.mail_item_cap

    # -- reads ---------------------------------------------------------------

    def listing(self) -> tuple[Character, ...]:
        return characters(self._sql, self.entry)

    def find_items(self, text: str) -> tuple[Item, ...]:
        return find_items(self._sql, self.entry, text)

    def gear_set_size(self, character: str) -> tuple[int, int]:
        """How many pieces this character is wearing, and how many mails that is.

        Asked BEFORE the press, so the button can say "send 19 pieces in 2
        mails" rather than leaving somebody to discover the second one.
        """
        name = self._stored_name(character)
        if name is None:
            return (0, 0)
        pieces = equipped(self._sql, self.entry, name)
        return (len(pieces), _mails_needed(len(pieces), self.mail_item_cap))

    # -- writes --------------------------------------------------------------

    def teleport(self, character: str, location: str) -> Outcome:
        verb = self.entry.play.teleport_command if self.entry.play is not None else ""
        return self._one(character, lambda name: commands.teleport_to(name, location, verb=verb))

    def set_level(self, character: str, level: int) -> Outcome:
        """Refused outright on a tree whose console has no route to a level.

        The tab does not draw the control there (8.4d), so this is the second
        of the two places -- and it is the one that matters, because the first
        is a decision about drawing and this is the decision about SENDING. A
        press that arrived anyway must not fall through to a sibling's
        `character level`: on the tortoise fork that is an unknown subcommand
        today, and on a fork that later grows one it would be a real command
        nobody has run.
        """
        block = self.entry.play
        if block is None or block.set_level_command is None:
            return Outcome(False, problem=_no_set_level(self.entry))
        verb = block.set_level_command
        return self._one(
            character, lambda name: commands.set_character_level(name, level, verb=verb)
        )

    def rename(self, character: str) -> Outcome:
        """Not sent at all where this tree measured the offline arm destroying
        the name.

        The belt-and-braces argument `set_level`'s docstring makes belongs
        here far more than it belongs there: `set_level`'s worst outcome on
        the fork that has no such command is `There is no such subcommand`,
        while `rename`'s is
        `UPDATE characters SET name = guid, at_login = at_login | '1'`
        (this fork's `src/game/Commands/Commands.cpp:12624-12635`) -- the name
        replaced by the numeric guid, behind a button that says "Rename at next
        login". The view greys the button, but it greys it from a character
        list read minutes ago; this reads the row the press is about.
        """
        block = self.entry.play
        verb = block.rename_command if block is not None else ""
        return self._one(
            character,
            lambda name: commands.rename_at_login(name, verb=verb),
            refuse_offline=block.rename_offline_refusal if block is not None else None,
        )

    def revive(self, character: str) -> Outcome:
        return self._one(character, commands.revive)

    def mail_gold(self, character: str, *, gold: int, subject: str, body: str) -> Outcome:
        """Gold in, copper out, multiplied once and in one place."""
        return self._one(
            character,
            lambda name: commands.mail_money(
                name, subject=subject, body=body, copper=gold * _COPPER_PER_GOLD
            ),
        )

    def mail_items(
        self, character: str, *, items: tuple[tuple[int, int], ...], subject: str, body: str
    ) -> Outcome:
        return self._one(
            character,
            lambda name: commands.mail_items(
                name, subject=subject, body=body, items=items, cap=self.mail_item_cap
            ),
        )

    def send_gear_set(self, character: str, *, to: str, subject: str, body: str) -> Outcome:
        """Everything a character is wearing, in as many mails as it takes.

        A set of nineteen pieces does not fit in one mail on any of these trees,
        so this sends the mails the cap requires and, if one of them fails,
        reports WHICH and what pressing again would do.

        **It is not resumable, and the sentence says so rather than implying
        otherwise** (8.4a's adversarial review). An earlier version advised
        "send only what is missing", which is an operation nobody has: there is
        one button and it sends everything worn. Somebody following that advice
        presses it again and the recipient receives the first twelve items
        twice. Making it resumable means persisting the batches and their
        states, which is a feature rather than a sentence, and it is not this
        box's.
        """
        wearer = self._stored_name(character)
        if wearer is None:
            return Outcome(False, problem=_no_such(character))
        recipient = self._stored_name(to)
        if recipient is None:
            return Outcome(False, problem=_no_such(to))
        channel = self._channel_for_saved()
        if channel is None:
            return Outcome(False, problem=_NO_CHANNEL)
        try:
            pieces = equipped(self._sql, self.entry, wearer)
        except Ambiguous as exc:
            # A refusal and not a raise: the press came from a button, and a
            # button that throws is a crash report where a sentence belongs.
            return Outcome(False, problem=str(exc))
        if not pieces:
            return Outcome(
                False, problem=f"{wearer} is wearing nothing, so there are no items to send"
            )
        cap = self.mail_item_cap
        batches = [pieces[at : at + cap] for at in range(0, len(pieces), cap)]
        for number, batch in enumerate(batches, start=1):
            try:
                line = commands.mail_items(
                    recipient,
                    subject=subject,
                    body=body,
                    items=tuple((item, 1) for item in batch),
                    cap=cap,
                )
            except commands.CommandError as exc:
                return Outcome(False, problem=str(exc))
            outcome = send(channel, line)
            if not outcome.done:
                return Outcome(
                    False,
                    indeterminate=outcome.indeterminate,
                    problem=(
                        f"mail {number} of {len(batches)} did not go: {outcome.problem} "
                        f"{number - 1} of the {len(batches)} mails already arrived. Pressing "
                        "this again sends the whole set from the start, so whatever arrived "
                        "will arrive a second time."
                    ),
                )
        return Outcome(
            True,
            text=f"{len(pieces)} items sent to {recipient} in {len(batches)} mails.",
        )

    # -- the shape every write shares ----------------------------------------

    def _one(
        self, character: str, build: Callable[[str], str], *, refuse_offline: str | None = None
    ) -> Outcome:
        """One write, with the name the server stores.

        `refuse_offline` is the sentence to answer with instead of sending, on
        a tree that has measured this command doing something WORSE than
        nothing to a character who is not logged in. It is checked here, off
        the row this press just read, rather than in the view off the character
        list -- 8.4d's review: the list is a snapshot and the bot manager
        invalidates it on a timer. Passed per action rather than applied to all
        of them, because every other action on that same fork works offline.
        """
        stored = self._stored(character)
        if stored is None:
            return Outcome(False, problem=_no_such(character))
        if refuse_offline and not stored.online:
            return Outcome(False, problem=f"{stored.name} {refuse_offline}")
        channel = self._channel_for_saved()
        if channel is None:
            return Outcome(False, problem=_NO_CHANNEL)
        try:
            line = build(stored.name)
        except commands.CommandError as exc:
            return Outcome(False, problem=str(exc))
        return send(channel, line)

    def _stored(self, character: str) -> Stored | None:
        return canonical_character(self._sql, self.entry, character)

    def _stored_name(self, character: str) -> str | None:
        stored = self._stored(character)
        return None if stored is None else stored.name


def _mails_needed(pieces: int, cap: int) -> int:
    return (pieces + cap - 1) // cap if pieces else 0


def _no_set_level(entry: CatalogEntry) -> str:
    """Why the press did nothing, in the tree's own measured words.

    The entry's sentence and not a phrase from here, so the refusal a person
    reads at the seam is the same one the tab draws where the control would be
    -- one measurement with one voice, rather than two English sentences that
    can drift apart.
    """
    reason = entry.play.set_level_absent_reason if entry.play is not None else None
    return reason or (
        f"{entry.name} has not measured whether its console can set a character's level, "
        "so this app does not send one"
    )


def _no_such(typed: str) -> str:
    """Named with what was TYPED, because that is what a person can correct."""
    return f"there is no character called {typed} on this server"


_COPPER_PER_GOLD = 10_000

_NO_CHANNEL = (
    "the command channel is not set up for this install yet, and these changes are made by "
    "the server rather than by writing rows"
)
