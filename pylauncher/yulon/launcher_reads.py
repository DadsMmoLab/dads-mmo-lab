"""The reads the client launcher window shows (T187).

* **Log in as** -- the server's game accounts, by name.
* **Realm address** -- the address the server's realm row announces.
* **N bots and M players online** -- the banner's count.
* **Addons in this client** -- the ready-to-play client's `Interface/AddOns`.

Plain functions over the existing seams. The window calls them on a worker
thread, with the server possibly stopped and Docker possibly down, so none of
them raises: a failed account read offers no accounts (the window still has
"Ask in the game"), a failed count is `None` (the banner leaves the count out
rather than showing a wrong one), and an unreadable addon folder lists nothing.

Nothing here decides which rows are bots or which account is the app's own.
`useraccounts.accounts()` and `dbreads.population()` already do, with the one
identity clause `dbreads.bot_clause()` that the bot list reads as well, so the
launcher cannot come to a different answer from the tabs.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import AbstractContextManager as ContextManager
from pathlib import Path

from yulon import dbreads, networking, useraccounts
from yulon.catalog.catalog import CatalogEntry
from yulon.dbreads import Marker, SqlReader
from yulon.log import get_logger

logger = get_logger(__name__)

BLIZZARD_PREFIX = "blizzard_"
"""Blizzard's own interface addons (`Blizzard_AuctionUI`, ...), compared casefolded.

They ship with every client and the player cannot do without them, so a list of
"the addons in this client" that named them would bury the player's own."""


def login_accounts(
    sql: SqlReader, entry: CatalogEntry, marker: Marker, *, app_account: str
) -> tuple[str, ...]:
    """The account names a person may log in as, sorted; empty when they cannot be read.

    `account_names()` with "could not be read" as no names, for a caller that
    only lists them.
    """
    return account_names(sql, entry, marker, app_account=app_account) or ()


def account_names(
    sql: SqlReader, entry: CatalogEntry, marker: Marker, *, app_account: str
) -> tuple[str, ...] | None:
    """The account names a person may log in as, sorted; `None` when they could not be read.

    `useraccounts.accounts()` is the read, so the bot accounts, every `YULON_`
    command-channel account and the auction-house bot are already left out in
    its WHERE. The app's own account is left out once more here by name,
    casefolded, because a dropdown that offered it would hand a person the
    credential every server feature rides on, whatever its name looks like;
    and so is every name `useraccounts` reserves for a command channel
    (`APP_PREFIX`, the rule its writes refuse by), for a row that comes back
    with a space or another case the SQL comparison did not see.

    `None` and not an empty tuple on a failure, because the launcher answers
    the two differently (T187 Review Focus 2): an account saved earlier that
    is not among the names READ is gone and falls back to "Ask in the game",
    while one that could not be looked for is kept.

    A blank marker is refused before any SQL: `bot_clause()` would make it
    `LIKE '%'`, call every account a bot and list none, which would read as
    "this server has no accounts" -- and as every saved account gone.
    """
    if not marker.prefix.strip():
        logger.info(
            "Log in as offers no accounts: this install's bot marker is blank, so the "
            "bot accounts cannot be told from a person's"
        )
        return None
    try:
        listing = useraccounts.accounts(sql, entry, marker, app_account=app_account)
    except Exception as exc:  # noqa: BLE001 - on a worker, a raise is a lost window
        logger.warning(f"could not read {entry.id}'s accounts for Log in as: {exc}")
        return None
    if listing.problem:
        logger.info(f"Log in as offers no accounts: {listing.problem}")
        return None
    own = app_account.strip().casefold()
    names = {
        a.username
        for a in listing.accounts
        if a.username.strip().casefold() != own
        and not a.username.strip().upper().startswith(useraccounts.APP_PREFIX)
    }
    return tuple(sorted(names, key=lambda name: (name.casefold(), name)))


def announced_address(sql: SqlReader, entry: CatalogEntry) -> str | None:
    """The address this server's realm row hands a client after login, or `None`.

    `networking.realmlist_address_query()`, the SELECT the installer's own
    realm step compares with, read for its first column (`address`). `None`
    when the read fails or does not come back as exactly one row with an
    address in it: the launcher's "you will be sent to ..." hint is left out
    rather than shown about an address nobody read.
    """
    try:
        raw = sql.query("auth", networking.realmlist_address_query(entry))
    except Exception as exc:  # noqa: BLE001 - on a worker, a raise is a lost window
        logger.info(f"could not read {entry.id}'s realm address: {exc}")
        return None
    rows = [line for line in raw.splitlines() if line.strip()]
    if len(rows) != 1:
        return None
    address = rows[0].split("\t")[0].strip()
    return address or None


def online_counts(sql: SqlReader, entry: CatalogEntry, marker: Marker) -> tuple[int, int] | None:
    """(bots online, players online), or `None` when the banner should leave the count out.

    `characters.online` split by `dbreads.bot_clause()`, through
    `dbreads.population()`: the identity clause `botlist` uses, the registry
    and the account prefix on WotLK and the prefix alone on the CMaNGOS and
    Tortoise trees. A player is every online character that clause does not
    claim.

    `None` on every failure, and on three answers that would be wrong rather
    than missing:

    * a blank marker, which `bot_clause()` would turn into `LIKE '%'` and call
      every account a bot (the `botlist.page()` refusal, asked first);
    * a tree with no measured bot marker;
    * a marker that matched no character while characters exist. Bots are
      always there on a server this app made, so "0 bots and 900 players" would
      be every bot counted as a person; `population()` flags it as a warning,
      and here the count is hidden instead of shown wrong.
    """
    if not marker.prefix.strip():
        logger.info("online count hidden: this install's bot marker is blank")
        return None
    try:
        counted = dbreads.population(sql, entry, marker)
    except Exception as exc:  # noqa: BLE001 - on a worker, a raise is a lost window
        logger.warning(f"could not count who is online on {entry.id}: {exc}")
        return None
    if counted.problem or counted.warning:
        logger.info(f"online count hidden: {counted.problem or counted.warning}")
        return None
    if counted.bots is None or counted.players is None:
        return None
    return counted.bots, counted.players


def addon_folders(play_dir: Path) -> tuple[str, ...]:
    """The addon folders in a ready-to-play client's `Interface/AddOns`, sorted.

    Blizzard's own (`Blizzard_*`) are left out. `Interface` and `AddOns` are
    matched casefolded, because a client copied from Windows keeps whatever
    case its folders had there and WoW does not care; where a case-sensitive
    disk holds two spellings, both are read and the names merged.

    **Links.** Only NAMES are read -- three single-folder listings, the client,
    its `Interface` and its `AddOns` -- and no addon folder is ever entered,
    linked or not, so nothing behind a link to the player's own client is read.
    `play_client._is_link` is not needed for that and is not used: it exists to
    stop a WALK, and this does not walk. The two ways a link shows up:

    * an addon folder that is a symlink or a junction is listed by name when
      it leads to a folder, since the game loads it; one that leads nowhere is
      not an addon the game can load and is left out;
    * an `Interface` or `AddOns` that is itself a link is listed THROUGH, one
      listing of its target, because that is the folder the game reads its
      addons from. `play_client.plan()` never copies a linked folder, so such
      a link in a ready-to-play client is one the player made there.

    An unreadable folder lists nothing rather than raising: the window shows an
    empty list and the **Open AddOns folder** button still works.
    """
    names: set[str] = set()
    for interface in _children_named(play_dir, "interface"):
        for addons in _children_named(interface, "addons"):
            for entry in _entries(addons):
                if entry.name.casefold().startswith(BLIZZARD_PREFIX):
                    continue
                if _leads_to_folder(entry):
                    names.add(entry.name)
    return tuple(sorted(names, key=lambda name: (name.casefold(), name)))


def _scandir(folder: Path) -> ContextManager[Iterator[os.DirEntry[str]]]:
    """The one folder listing in this module; a seam, so tests can see which folders it reads."""
    return os.scandir(folder)


def addons_folder(play_dir: Path) -> Path:
    """The folder **Open AddOns folder** opens: `Interface/AddOns`, matched as `addon_folders` does.

    Casefolded per part, so a client copied from Windows as `interface/addons` or
    `INTERFACE/AddOns` opens there too (T187 final review). Without an `AddOns`, the
    `Interface` folder; without that, the client itself.
    """
    interfaces = list(_children_named(play_dir, "interface"))
    for interface in interfaces:
        for addons in _children_named(interface, "addons"):
            return addons
    return interfaces[0] if interfaces else play_dir


def _entries(folder: Path) -> list[os.DirEntry[str]]:
    """One folder's entries, or none when it cannot be listed."""
    try:
        with _scandir(folder) as found:
            return list(found)
    except OSError as exc:
        logger.info(f"could not list {folder}: {exc}")
        return []


def _children_named(folder: Path, casefolded: str) -> Iterator[Path]:
    """The sub-folders of `folder` whose name casefolds to `casefolded`, in name order."""
    hits = [e for e in _entries(folder) if e.name.casefold() == casefolded and _leads_to_folder(e)]
    for entry in sorted(hits, key=lambda e: e.name):
        yield folder / entry.name


def _leads_to_folder(entry: os.DirEntry[str]) -> bool:
    """A folder, or a link to one. A link to nothing, or a look that fails, is not."""
    try:
        return entry.is_dir(follow_symlinks=True)
    except OSError:
        return False
