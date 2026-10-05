"""The accounts a person may act on, and what they may do to them (Phase 8.3a).

A read, and what it leaves OUT is the point. A WotLK install with the owner's
settings carries 500 bot accounts and one account this app made for itself. A
list of all 502 is a list nobody can use, and one of those rows is the
launcher's own credential — handing a user a Set-password button for it would
break the channel every other feature in Phase 8 rides on.

Both exclusions are in the WHERE rather than applied to the answer. Filtering
afterwards leaves the rows on the wire and in any log of the statement, and puts
the burden on every future caller to remember what this one knew.

**Where the GM level lives is a per-tree fact.** AzerothCore keeps it in
`account_access` keyed `id`; the CMaNGOS trees keep it on the account row, under
two different names. Reading the wrong one does not fail — it shows every
account as level 0, which is a lie shaped like an answer — so an entry whose
level store has not been measured refuses rather than guessing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from yulon import commands, passwordcheck
from yulon.actions import Outcome, outcome_of
from yulon.actions import send as _send
from yulon.catalog.catalog import CatalogEntry
from yulon.dbreads import Marker, SqlReader, bot_clause, resolve_marker
from yulon.log import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class Account:
    """One account a person may act on."""

    id: int
    username: str
    gm_level: int


@dataclass(frozen=True)
class Listing:
    """The accounts, or why there are none to show.

    `problem` is not decoration: an empty list and an unreadable database look
    identical on screen, and one of them means "this server has no accounts"
    while the other means "do not trust what you are looking at".
    """

    accounts: list[Account] = field(default_factory=list)
    problem: str = ""


APP_PREFIX = "YULON_"
"""The prefix every account this app makes for itself carries.

The guard reads THIS and not one install's own name, because two installs can
share an auth database: `YULON_AAAAAAAA` and `YULON_BBBBBBBB` are two channel
accounts, and a guard that knows only its own would list the neighbour's and
offer to change its password -- ending that install's command channel while
saying nothing about it (adversarial review, 2026-09-07).

The underscore is load-bearing. `YULONGATE` is a person's account on the gate
box and is theirs to change; `LEFT(username, 6)` tells them apart, where a LIKE
would not because `_` is a wildcard.
"""


AHBOT_ACCOUNT = "AHBOT"
"""The account the auction-house module runs as, which is not a person's.

`manifests/wow-wotlk/modules/mod-ah-bot.json` tells the user to create it by
this name and hand its character's GUID to the module. Listing it beside real
players offers to change its password -- which would break the auction house
without a word, since the module logs in as it. The Rust launcher excluded it
(`origin/rust-main:crates/dml-wow/src/pages.rs:302`) and this port dropped the
exclusion for want of reading that first (retrospective audit, 2026-09-08).
A literal name rather than a pattern: it is the name the manifest asks for.
"""


def accounts(sql: SqlReader, entry: CatalogEntry, marker: Marker, *, app_account: str) -> Listing:
    """This install's accounts, without the bots and without the app's own."""
    level = entry.accounts.level
    if level is None:
        return Listing(
            problem=(
                f"{entry.name} keeps its GM levels somewhere this app has not measured yet, "
                "so it will not guess at them"
            )
        )
    schemas = entry.schema_map()
    auth = schemas["auth"]
    bots = _bot_accounts_clause(entry, marker)
    if level.table is None:
        # The level is a column on the account row itself, so there is nothing
        # to join and no realm to consider.
        selected = f"COALESCE(a.{level.level_column}, 0)"
        join = ""
    else:
        selected = f"COALESCE(MAX(x.{level.level_column}), 0)"
        join = f" LEFT JOIN {auth}.{level.table} x ON x.{level.account_column} = a.id"
    group = "" if level.table is None else " GROUP BY a.id, a.username"
    statement = (
        f"SELECT a.id, a.username, {selected} FROM {auth}.account a{join} "
        f"WHERE NOT ({bots}) AND LEFT(a.username, {len(APP_PREFIX)}) <> '{APP_PREFIX}' "
        f"AND a.username <> '{AHBOT_ACCOUNT}'"
        f"{group} ORDER BY a.username;"
    )
    try:
        raw = sql.query("auth", statement)
    except Exception as exc:  # noqa: BLE001 - every seam failure is one answer here
        logger.warning(f"could not read {entry.id}'s accounts: {exc}")
        return Listing(problem=f"could not read this server's accounts: {exc}")
    return _rows(raw)


def _bot_accounts_clause(entry: CatalogEntry, marker: Marker) -> str:
    """`bot_clause()`, but true of a row in `account` rather than in `characters`.

    The same two arms and the same reasons — the registry first where there is
    one, the name prefix always — expressed against the account's own `id` and
    `username` instead of a character's `account` column.
    """
    ops = entry.observability
    arms = [f"UPPER(a.username) LIKE '{marker.prefix.upper()}%'"]
    if ops is not None:
        registry = ops.bots.registry
        schemas = entry.schema_map()
        if registry is not None and registry.database in schemas:
            types = ", ".join(str(t) for t in registry.types)
            arms.insert(
                0,
                f"a.id IN (SELECT {registry.account_column} FROM "
                f"{schemas[registry.database]}.{registry.table} "
                f"WHERE {registry.type_column} IN ({types}))",
            )
    _ = bot_clause  # the characters-table sibling; kept named so the pair is findable
    return " OR ".join(arms)


def _rows(raw: str) -> Listing:
    """Three tab-separated fields per line, and nothing halfway.

    A line that will not parse fails the whole listing rather than being
    skipped: a list quietly missing one account is worse than no list, because
    nothing on screen says an account is missing.
    """
    out: list[Account] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != 3 or not fields[0].strip().isdigit():
            return Listing(problem=f"an account row came back as {line.strip()!r}")
        level = fields[2].strip()
        if not level.lstrip("-").isdigit():
            return Listing(problem=f"an account row came back as {line.strip()!r}")
        out.append(Account(id=int(fields[0]), username=fields[1].strip(), gm_level=int(level)))
    return Listing(accounts=out)


# -- what a person may do to one, and to which ------------------------------


def set_password(
    channel: object,
    *,
    account: str,
    password: str,
    app_account: str,
    password_is_in_force: Callable[[str, str], bool | None] | None = None,
) -> Outcome:
    """`account set password <user> <pass> <pass>`, through the server itself.

    Owner answer 7: the server performs its own character-database writes. A
    password written here as a row is a row this app would have to get exactly
    right on every core, forever, and getting it wrong inserts something that
    looks correct and can never log in.

    **The reply is a hint and the row is the answer** (8.3b, measured on m910q
    2026-09-07). CMaNGOS's handler sends its success message and then
    `SetSentErrorMessage(true); return false;` -- deliberately, "to avoid normal
    report for hide passwords" (`Level3.cpp:1178-1183` on TBC, and the same
    lines on Vanilla) -- and SOAP turns a handler that returned false into a
    fault. So on those cores a SUCCESSFUL change comes back as a failure, every
    time.

    Telling somebody their password did not change when it did is the worst of
    the available wrongs: they retype the old one, for an account that no longer
    has it. 8.3a's review raised that hazard for timeouts; here it is guaranteed.

    But **"the row changed" is not the answer either**, which is what 8.3b's own
    adversarial review found. Anything else that writes that row -- a second
    window, an administrator at a console, another install sharing the auth
    database -- would make a refused command look like a success, and the person
    would then be locked out by the reassurance rather than by the failure.

    So `password_is_in_force` asks the one question that belongs to this
    command: is the password we were asked to set the one this account now has?
    `yulon.passwordcheck` answers it from the account's own row, which is true
    whoever wrote that row and whatever the server said.

    **It is asked even when the server says yes** (8.3d). The tortoise fork
    answers "The password was changed" and then, for an account that has logged
    in before, stores a hash with an EMPTY account name in it:

        stored                        A78031B82173E3D5AB216AB0835A165DB5D56020
        SHA1(":PR0BE-P@SS55")         matches
        SHA1("GATE83E:PR0BE-P@SS55")  does not

    That account can never log in again -- measured with a real client -- and
    the server has just reported success. So a yes the row contradicts is not a
    yes, and the price of knowing is one SELECT per password change.

    `None` is not `False`: a tree whose scheme nobody has measured, or a
    database that did not answer, leaves the server's own reply exactly as it
    was, which is the behaviour every caller had before this.
    """
    refusal = _not_our_own(account, app_account, "have its password changed")
    if refusal is not None:
        return refusal
    try:
        line = commands.account_set_password(account, password)
    except commands.CommandError as exc:
        return Outcome(False, problem=str(exc))
    outcome = _send(channel, line)
    if password_is_in_force is None:
        return outcome
    in_force = _in_force(password_is_in_force, account, password)
    if in_force is None:
        # Nothing to add: no measured recipe here, or the database did not
        # answer. The server's own reply is all there is, as it always was.
        return outcome
    if in_force:
        logger.info(f"{account}'s stored credential is the one this password makes")
        return Outcome(
            True,
            text=(
                "The password was changed. Some servers report a password change as a failure "
                "even when it works, so the account's own row was read to be sure."
            ),
        )
    if outcome.done:
        logger.warning(f"{account}: the server reported a password change its own row denies")
        return Outcome(
            False,
            problem=(
                f"The server said {account}'s password was changed, but the account's stored "
                "credential is not that password, so it will not log in with it. Nothing here "
                "can put it right: the change has to be made on the server itself, and the old "
                "password may or may not still work. Try logging in before relying on either."
            ),
        )
    return outcome


def _in_force(
    reader: Callable[[str, str], bool | None], account: str, password: str
) -> bool | None:
    """Whether the row says this password is in force -- or `None` for cannot say.

    Three answers and not two. A database that is down, or a tree whose scheme
    nobody has measured, is a question this app could not ask; turning that into
    "no" would report a password change as failed for every tree this module has
    never measured.
    """
    try:
        return reader(account, password)
    except Exception as exc:  # noqa: BLE001 - a read that failed is an absence
        logger.info(f"could not read {account}'s credential row: {exc}")
        return None


def set_gm_level(
    channel: object, *, account: str, level: int, app_account: str, realms: bool, highest: int
) -> Outcome:
    """`account set gmlevel <user> <n>`, with the realm argument where there are realms.

    `realms` and `highest` both come from the entry -- a tree whose level is a
    column on the account row has no realm to name, and the trees do not agree
    on how high the levels go -- and both are passed rather than defaulted, for
    the reason `commands.account_set_gm_level()` gives.
    """
    refusal = _not_our_own(account, app_account, "have its GM level changed")
    if refusal is not None:
        return refusal
    try:
        line = commands.account_set_gm_level(account, level, realms=realms, highest=highest)
    except commands.CommandError as exc:
        return Outcome(False, problem=str(exc))
    return _send(channel, line)


def _not_our_own(account: str, app_account: str, what: str) -> Outcome | None:
    """The app's own account is not the user's to change.

    Here rather than in the tab, where it would be one forgotten `if` away from
    being gone. Changing this account's password breaks the command channel
    every other Phase 8 feature rides on; dropping its level below administrator
    breaks it just as thoroughly, and neither failure says what it was.
    """
    name = account.strip().upper()
    if not name.startswith(APP_PREFIX) and name != app_account.strip().upper():
        return None
    mine = name == app_account.strip().upper()
    whose = "its own command channel" if mine else "another install's command channel"
    # "reserves", not "made": the name is derived from the install id and is
    # refused whether or not an account by it exists. On the tortoise fork this
    # app never makes one -- the console IS the channel there, so there is no
    # credential and no account (8.2e) -- and a sentence claiming otherwise
    # teaches a person the wrong thing about their own server, in the tab.
    return Outcome(
        False,
        problem=(
            f"{account} is the name this app reserves for {whose}, and it cannot {what} from "
            "here — where that account exists, changing it stops the channel working"
        ),
    )


# -- deleting one (T301) ------------------------------------------------------


@dataclass(frozen=True)
class DeletePlan:
    """What deleting one account would remove, or why it will not be deleted.

    `characters` is what the person is asked about, read from the characters
    database: the server deletes them with the account
    (`AccountMgr::DeleteAccount` on every tree), so a question that named only
    the account would be asking about less than the press does.
    """

    account: str
    characters: tuple[str, ...] = ()
    problem: str = ""
    account_id: int = 0
    """The row the person was asked about. The name and the characters are not
    enough to know it is still that account: one deleted elsewhere and made
    again under the same name can have the same characters, or none (Codex,
    T301's two reviews)."""
    character_ids: tuple[int, ...] = ()
    """The characters' own rows (`guid`), beside `characters`, for the same
    reason: a character deleted and made again under its old name is not the
    one the person was asked about (Codex, T301's second normal review)."""


def deletion_plan(
    sql: SqlReader, entry: CatalogEntry, marker: Marker, *, account: str, app_account: str
) -> DeletePlan:
    """The account's characters, or the rule that keeps it.

    The rules, in the order they are asked: a name the server would refuse;
    this app's own command-channel accounts; the auction-house account; an
    account that is not there; the bot module's accounts (by registry where
    there is one, and by prefix); and an account with a character in the game.
    The last is a refusal rather than a log-out (lead decision, T301): taking
    somebody out of the game is a second thing the press would do without
    saying so.

    "In the game" is the characters table's `online` column: a character in
    the world. Somebody logged in and sitting at the character list has no
    character online and is not refused. The server's delete kicks only a
    player in the world (AzerothCore `AccountMgr.cpp:131-137`); a session at
    the character list stays where it is, with its characters gone, and the
    next login fails.

    An all-digit name is refused on the trees that read digits as an account
    id (`commands.digits_read_as_an_id()`), before anything is read.
    """
    refusal = _not_ours_to_delete(account, app_account, digits_are_ids=digits_are_ids(entry))
    if refusal:
        return DeletePlan(account, problem=refusal)
    ops = entry.observability
    if ops is None:
        return DeletePlan(
            account,
            problem=(
                f"Yu'lon cannot yet tell whose characters are whose on {entry.name}, so it "
                "does not delete accounts there"
            ),
        )
    schemas = entry.schema_map()
    bots = _bot_accounts_clause(entry, marker)
    try:
        raw = sql.query(
            "auth",
            f"SELECT a.id, CASE WHEN ({bots}) THEN 1 ELSE 0 END FROM {schemas['auth']}.account a "
            f"WHERE a.username = {_text_literal(account)};",
        )
    except Exception as exc:  # noqa: BLE001 - every seam failure is one answer here
        logger.warning(f"could not read the account {account}: {exc}")
        return DeletePlan(account, problem=f"Could not read the account {account}: {exc}")
    rows = [line.split("\t") for line in raw.splitlines() if line.strip()]
    if not rows:
        return DeletePlan(
            account,
            problem=(
                f"There is no account named {account} on this server now. "
                "Press Refresh the list."
            ),
        )
    if len(rows) != 1 or len(rows[0]) != 2 or not rows[0][0].strip().isdigit():
        return DeletePlan(account, problem=f"the account {account} came back as {raw.strip()!r}")
    if rows[0][1].strip() != "0":
        return DeletePlan(
            account,
            problem=(
                f"{account} belongs to this server's bots: the bot module made it and runs it, "
                "so Yu'lon does not delete it."
            ),
        )
    account_id = int(rows[0][0])
    table = ops.characters
    try:
        raw = sql.query(
            "characters",
            f"SELECT guid, name, {table.online} FROM {schemas['characters']}.{table.table} "
            f"WHERE {table.account} = {account_id} ORDER BY name, guid;",
        )
    except Exception as exc:  # noqa: BLE001 - every seam failure is one answer here
        logger.warning(f"could not read {account}'s characters: {exc}")
        return DeletePlan(
            account,
            problem=(
                f"Could not read {account}'s characters, so Yu'lon cannot say what deleting "
                f"it would remove: {exc}"
            ),
        )
    ids: list[int] = []
    names: list[str] = []
    online: list[str] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != 3 or not fields[0].strip().isdigit() or not fields[2].strip().isdigit():
            return DeletePlan(
                account, problem=f"one of {account}'s characters came back as {line.strip()!r}"
            )
        ids.append(int(fields[0]))
        names.append(fields[1].strip())
        if fields[2].strip() != "0":
            online.append(fields[1].strip())
    if online:
        who = _names(online)
        verb = "is" if len(online) == 1 else "are"
        whom = online[0] if len(online) == 1 else "them"
        return DeletePlan(
            account,
            characters=tuple(names),
            account_id=account_id,
            problem=(
                f"{who} {verb} in the game right now on {account}. Log {whom} out first, then "
                "delete the account."
            ),
        )
    return DeletePlan(
        account, characters=tuple(names), account_id=account_id, character_ids=tuple(ids)
    )


def delete_account(
    channel: object,
    *,
    account: str,
    app_account: str,
    characters: tuple[str, ...],
    digits_are_ids: bool,
) -> Outcome:
    """`account delete <user>` through the server, and its answer read truthfully.

    A result -- even an empty one, which is a command that ran and printed
    nothing (T226) -- is done. A fault is the server refusing, said in its own
    words. Anything else is `outcome_of()`'s, which keeps "could not ask" and
    "may have run" apart from both.

    The own-account and auction-house rules are asked here too, where the line
    is built; the rules that need a read are `InstallAccounts.delete_account`'s,
    which asks them again right before it calls this.
    """
    refusal = _not_ours_to_delete(account, app_account, digits_are_ids=digits_are_ids)
    if refusal:
        return Outcome(False, problem=refusal)
    try:
        line = commands.account_delete(account, digits_are_ids=digits_are_ids)
    except commands.CommandError as exc:
        return Outcome(False, problem=str(exc))
    answer = channel.send(line)  # type: ignore[attr-defined]
    outcome = getattr(answer, "outcome", "")
    if outcome == "yes":
        logger.info(f"the server deleted the account {account}")
        if not characters:
            return Outcome(True, text=f"Deleted the account {account}. It had no characters.")
        if len(characters) == 1:
            return Outcome(
                True, text=f"Deleted the account {account} and its character {characters[0]}."
            )
        return Outcome(
            True,
            text=(
                f"Deleted the account {account} and its {len(characters)} characters, "
                f"{_names(characters)}."
            ),
        )
    if outcome == "no":
        said = str(getattr(answer, "text", "")).strip()
        if not said:
            return Outcome(
                False, problem=f"The server did not delete {account}, and did not say why."
            )
        return Outcome(False, problem=f"The server did not delete {account}: {said}")
    return outcome_of(answer)


def digits_are_ids(entry: CatalogEntry) -> bool:
    """Does this tree read an all-digit account argument as an account id?

    True unless the tree is one measured to look names up by name alone, so a
    tree added later starts on the safe side (T301's cold review).
    """
    return entry.id not in _NAMES_ARE_NAMES


_NAMES_ARE_NAMES = frozenset({"wow-wotlk", "wow-centurion"})
"""`AccountMgr::GetId(accountName)` and nothing else: AzerothCore `cs_account.cpp:347`
(7f12e89e), TrinityCore112 `cs_account.cpp:303` (faac5fc9). The CMaNGOS trees try
the digits as an id first: `commands.digits_read_as_an_id()`."""


def _not_ours_to_delete(account: str, app_account: str, *, digits_are_ids: bool) -> str:
    """The rules that need no read: the name, the channel accounts, the auction house.

    The auction-house account is refused by that exact name, `AHBOT`, the one
    its manifest asks for; one made under another name is an ordinary account.
    """
    if not commands.valid_account_name(account):
        return f"{account!r} is not a name this server would accept"
    if digits_are_ids:
        refusal = commands.digits_read_as_an_id(account)
        if refusal:
            return refusal
    own = _not_our_own(account, app_account, "be deleted")
    if own is not None:
        return own.problem
    if account.strip().upper() == AHBOT_ACCOUNT:
        return (
            f"{AHBOT_ACCOUNT} is the account the auction house runs as. Deleting it stops the "
            "auction house, so Yu'lon does not delete it."
        )
    return ""


def _names(names: list[str] | tuple[str, ...]) -> str:
    """`A`, `A and B`, `A, B and C`."""
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _text_literal(text: str) -> str:
    """A hex blob, as every other statement in this project writes a string."""
    return "_utf8mb4 X'" + text.encode("utf-8").hex().upper() + "'"


# -- what the tab is handed --------------------------------------------------


class InstallAccounts:
    """One install's accounts, as the Accounts tab sees them (8.3a).

    Reads go to the database and writes go to the server, and that split is
    owner answer 7 rather than an implementation detail: this app reads rows
    and the server changes them.

    Both halves need the app's own account name, for opposite reasons — the
    read leaves it out of the list, the writes refuse it — so it is held here
    once instead of being passed in at every call site.
    """

    def __init__(
        self,
        entry: CatalogEntry,
        server_dir: Path,
        *,
        sql: SqlReader,
        channel_for_saved: Callable[[], object | None],
        app_account: str,
    ) -> None:
        self.entry = entry
        self.server_dir = server_dir
        self._sql = sql
        self._channel_for_saved = channel_for_saved
        self.app_account = app_account

    @property
    def sql(self) -> SqlReader:
        """The reader the listing uses; the client launcher reads with the same one (T187)."""
        return self._sql

    def listing(self) -> Listing:
        """The accounts, with this install's live bot marker."""
        answer = resolve_marker(self.entry, self.server_dir)
        if answer.marker is None:
            return Listing(problem=answer.problem or "this install's bot marker could not be read")
        return accounts(self._sql, self.entry, answer.marker, app_account=self.app_account)

    def set_password(self, account: str, password: str) -> Outcome:
        channel = self._channel()
        if channel is None:
            return Outcome(False, problem=_NO_CHANNEL)
        return set_password(
            channel,
            account=account,
            password=password,
            app_account=self.app_account,
            password_is_in_force=self._password_is_in_force,
        )

    def _password_is_in_force(self, account: str, password: str) -> bool | None:
        """Does this account's stored credential belong to this password?

        The narrow question, and the only one worth asking: a row that merely
        CHANGED could have been changed by a second window, an administrator at
        a console, or another install sharing this auth database, and reporting
        that as this command's success would lock somebody out with a
        reassurance (8.3b's adversarial review).

        The columns are this tree's own, and `yulon.passwordcheck` holds both
        them and the recipe that reads them so the two cannot drift apart: the
        CMaNGOS trees keep a salt and a verifier in `s`/`v`, and the Tortoise
        fork keeps ONE unsalted hash in `sha_pass_hash` -- and carries `v`/`s`
        columns as well, so a check that reached for the wrong pair there would
        find something to read and answer no forever.

        The salt and the verifier never leave this method. They are not a
        password, and nothing logs them.
        """
        scheme = self.entry.accounts.scheme or ""
        columns = passwordcheck.COLUMNS.get(scheme)
        if columns is None:
            logger.info(f"{self.entry.name} has no measured credential recipe; the reply stands")
            return None
        auth = self.entry.schema_map()["auth"]
        row = self._sql.query(
            "auth",
            f"SELECT {', '.join(columns)} FROM {auth}.account "
            f"WHERE username = {_text_literal(account)};",
        )
        fields = row.split()
        if not fields:
            # No row at all is not "the wrong password" -- it is no answer.
            logger.info(f"{account} has no credential row to read")
            return None
        answered = passwordcheck.matches(scheme, account, password, fields)
        if answered is None:
            logger.info(f"{account}'s credential is not in a shape this app can read")
        return answered

    def set_gm_level(self, account: str, level: int) -> Outcome:
        channel = self._channel()
        if channel is None:
            return Outcome(False, problem=_NO_CHANNEL)
        # From the entry, not from a default: a tree whose level lives on the
        # account row has no realm to name (8.3b), and the trees do not agree on
        # how high the levels go (8.3d -- the tortoise fork accepts 4).
        level_block = self.entry.accounts.level
        return set_gm_level(
            channel,
            account=account,
            level=level,
            app_account=self.app_account,
            realms=level_block is not None and level_block.table is not None,
            highest=level_block.max_level if level_block is not None else 3,
        )

    def delete_plan(self, account: str) -> DeletePlan:
        """What deleting `account` would remove, read now, or why it will not be deleted."""
        refusal = _not_ours_to_delete(
            account, self.app_account, digits_are_ids=digits_are_ids(self.entry)
        )
        if refusal:
            return DeletePlan(account, problem=refusal)
        if self._channel() is None:
            # Said before the question rather than after it: a person who has
            # just said yes should not then be told nothing could be asked.
            return DeletePlan(account, problem=_NO_CHANNEL)
        answer = resolve_marker(self.entry, self.server_dir)
        if answer.marker is None:
            return DeletePlan(
                account, problem=answer.problem or "this install's bot marker could not be read"
            )
        return deletion_plan(
            self._sql, self.entry, answer.marker, account=account, app_account=self.app_account
        )

    def delete_account(self, confirmed: DeletePlan) -> Outcome:
        """Delete the account the person confirmed, if it is still that account.

        Every rule is asked again here, right before the line is sent: the
        question may have been open for a minute, and somebody can log in, or
        make a character, in that minute. `confirmed` is the plan they were
        shown; if the row is another one now, or has other characters, nothing
        is deleted.

        What is left is the moment between this read and the server running
        the command, and nothing on any of the five trees closes it: `account
        delete` takes a name, checks nothing of ours, and kicks whoever is on
        the account (`AccountMgr::DeleteAccount`). So a login inside that
        moment is kicked and its characters -- the ones the person was asked
        about -- are deleted with the account.
        """
        account = confirmed.account
        plan = self.delete_plan(account)
        if plan.problem:
            return Outcome(False, problem=plan.problem)
        channel = self._channel()
        if channel is None:
            return Outcome(False, problem=_NO_CHANNEL)
        if plan.account_id != confirmed.account_id:
            return Outcome(
                False,
                problem=(
                    f"{account} is not the account you were asked about: it was deleted and made "
                    "again while you were being asked. Nothing was deleted."
                ),
            )
        if (plan.characters, plan.character_ids) != (
            tuple(confirmed.characters),
            tuple(confirmed.character_ids),
        ):
            now = _names(plan.characters) if plan.characters else "none"
            return Outcome(
                False,
                problem=(
                    f"{account}'s characters changed while you were being asked; it now has "
                    f"{now}. Nothing was deleted. Press delete again to be asked about these."
                ),
            )
        return delete_account(
            channel,
            account=account,
            app_account=self.app_account,
            characters=plan.characters,
            digits_are_ids=digits_are_ids(self.entry),
        )

    def _channel(self) -> object | None:
        return self._channel_for_saved()


_NO_CHANNEL = (
    "the command channel is not set up for this install yet, and these changes are made by "
    "the server rather than by writing rows. Turn it on from the Server tab."
)
