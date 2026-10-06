"""The text a command becomes, and the arguments it refuses (Phase 8.2a).

Pure: this module sends nothing, holds no lock and knows no container. It turns
a template and some arguments into one line, and refuses arguments the server
would refuse — so nothing reaches the wire that cannot succeed.

## The leading dot lives here, once

The console and SOAP parser strips a leading `.` or `!` before it looks anything
up (`azerothcore.md:161-176`, `if (str[0] == '.' || str[0] == '!')`), while the
in-game handler *requires* one (`Chat.cpp:255-256`). So on this route the dot is
a matter of taste — and the prior art shipped both tastes: its `gm_level_cmd`
emits `.character level {player} {level}` with a dot and its `gm_at_login_cmd`
emits `character {flag} {player}` without, both pinned by their own tests, with
no comment explaining the asymmetry (`hypeer.md:1369-1374`).

Yu'lon's templates therefore carry no dot, and this module is the one place that
decides. A template that grows one anyway still produces the same line, because
`line()` strips it.

## The validators are the server's, not this app's

The charsets and lengths come from the bash oracle by way of the Rust port
(`soap_cmds.rs:24-40`, from `cli/src/90-main.sh:116-138`). They are allow-lists,
which is what stops an argument smuggling a second command into a line: a space
or a semicolon is simply not in the set.
"""

from __future__ import annotations

import re

_ACCOUNT_NAME = re.compile(r"^[A-Za-z0-9_]{3,20}$")
_ACCOUNT_PASSWORD = re.compile(r"^[A-Za-z0-9_@#%+=!-]{4,16}$")
_CHARACTER_NAME = re.compile(r"^[A-Za-z0-9_]{1,12}$")


class CommandError(ValueError):
    """An argument the server would refuse, refused here instead."""


def line(text: str) -> str:
    """One command, normalised: no leading `.`/`!`, no surrounding space.

    Inner spacing is left exactly as given — `server set motd` takes a message,
    and collapsing its spaces would change what players read.
    """
    stripped = text.strip()
    if stripped[:1] in (".", "!"):
        stripped = stripped[1:].strip()
    if not stripped:
        raise CommandError("an empty command was asked for; nothing was sent")
    if "\n" in stripped or "\r" in stripped:
        raise CommandError("a command is one line, and this one carries a line break")
    return stripped


def valid_account_name(name: str) -> bool:
    """`^[A-Za-z0-9_]{3,20}$` — what account creation accepts."""
    return bool(_ACCOUNT_NAME.match(name))


def valid_account_password(password: str) -> bool:
    """`^[A-Za-z0-9_@#%+=!-]{4,16}$` — and the 16 is a ceiling, not a preference."""
    return bool(_ACCOUNT_PASSWORD.match(password))


def valid_character_name(name: str) -> bool:
    """`^[A-Za-z0-9_]{1,12}$` — a character name as this core stores it."""
    return bool(_CHARACTER_NAME.match(name))


def account_create(account: str, password: str) -> str:
    """`account create <user> <pass>`, with both parts checked first."""
    _require(valid_account_name(account), f"{account!r} is not a name this server would accept")
    _require(valid_account_password(password), "that password is not one this server would accept")
    return line(f"account create {account} {password}")


def account_set_gm_level(account: str, level: int, *, realms: bool, highest: int) -> str:
    """`account set gmlevel <user> <n>`, with `-1` for every realm where there are realms.

    `realms` is required and not defaulted, because the two cores disagree and
    neither disagreement fails loudly.

    AzerothCore keeps the level in `account_access`, which has a `RealmID`
    column, so its command takes a realm and `-1` means every one of them. SOAP
    reads that table with no realm filter, so a row pinned to a single realm id
    would not be seen on this path; the prior art passes `-1` for the same
    reason (`soap_cmds.rs:95`).

    The CMaNGOS handler takes no such argument. It is
    `account set gmlevel <account> <level>`, and it writes
    `UPDATE account SET gmlevel = '%i' WHERE id = '%u'` against the account row
    (`Level3.cpp:1080-1128`, read on the TBC install 2026-09-07). There is no
    realm to name because there is no table with realms in it.

    The caller knows which it is from a fact its own box already measured:
    `entry.accounts.level.table` is null exactly where the level is a column.

    `highest` is required for the same reason and comes from the same block.
    Three of these trees stop at 3 and the tortoise fork accepts 4, because its
    check grants at the caller's own level rather than strictly below it
    (measured live, 2026-09-07: `4` was accepted and `5` answered "Incorrect
    values."). A number in this file would refuse a level the server accepts,
    in this app's own voice, as though the SERVER had said no.
    """
    _require(valid_account_name(account), f"{account!r} is not a name this server would accept")
    _require(0 <= level <= highest, f"{level} is not a GM level this server has")
    every_realm = " -1" if realms else ""
    return line(f"account set gmlevel {account} {level}{every_realm}")


def account_set_password(account: str, password: str) -> str:
    """`account set password <user> <pass> <pass>` — the server wants it twice."""
    _require(valid_account_name(account), f"{account!r} is not a name this server would accept")
    _require(valid_account_password(password), "that password is not one this server would accept")
    return line(f"account set password {account} {password} {password}")


# -- the Play verbs (8.4a) ---------------------------------------------------
#
# Every shape below was read off a live AzerothCore server on 2026-09-07 by
# asking it for its own help, and one of them by asking it for its BEHAVIOUR
# because the help is wrong: `.revive` documents no argument at all, and
# `revive NOSUCHCHARACTER` answered "Character 'Nosuchcharacter' does not
# exist." A feature built from that help would have drawn no revive button.

_TELEPORT_LOCATION = re.compile(r"^[A-Za-z0-9_$-]{1,64}$")
"""One token of the server's own `game_tele` alphabet.

1989 rows on this install, spelled `7thLegionFront`, `AbandonedArmory`, `AB`.
The `$` is there for `$home`, which that command's help names as a location. A
space in this argument is a SECOND argument to the server, not a longer name.
"""

_MONEY_CAP = 2_147_483_647
"""Copper in a signed 32-bit field, which is what the server counts money in."""


def _character(name: str) -> str:
    """The one place a character name is checked, for every verb below.

    A check that lives on six code paths is a check that is missing from one of
    them, so the verbs all come through here.
    """
    _require(
        valid_character_name(name), f"{name!r} is not a character name this server would accept"
    )
    return name


_TELEPORT_VERB = re.compile(r"^[a-z]+ name$")
"""What a teleport verb looks like, so a catalog value cannot become two commands.

`teleport name` on AzerothCore and `tele name` on the CMaNGOS trees -- measured
on both, 2026-09-07, where `teleport` on the second answered "There is no such
command". Anything else is a tree nobody has measured, and sending it would put
a character name where a verb belongs.
"""


def teleport_to(character: str, location: str, *, verb: str) -> str:
    """`<verb> <character> <location>` -- and it works on an OFFLINE character.

    The verb is `teleport name` on AzerothCore and `tele name` on the CMaNGOS
    trees; the bare `teleport`/`tele` moves whoever is SELECTED, and over a
    command channel nobody is selected. Both trees' own help says "Character
    can be offline", which is what makes this a button that works on a list
    rather than only on somebody playing.

    Passed rather than defaulted, for the reason `realms` and `highest` are:
    the trees disagree and the disagreement is silent -- `teleport name` on TBC
    is refused by a server that closes the connection without a word.
    """
    _character(character)
    _require(
        bool(_TELEPORT_VERB.match(verb.strip())),
        f"{verb!r} is not a teleport command this app has measured on a server",
    )
    _require(
        bool(_TELEPORT_LOCATION.match(location)),
        f"{location!r} is not one of this server's teleport names",
    )
    return line(f"{verb.strip()} {character} {location}")


_LEVEL_VERB = re.compile(r"^(?:[a-z]+ )?level$")
"""What a set-level verb looks like: `character level` on three of these trees.

The fourth has none at all, and that is why this is a passed argument rather
than the constant it used to be. On the tortoise fork the only command that
writes an arbitrary level is `.levelup`, registered with `AllowConsole` false
(`Chat.cpp:923`, read from that fork's source 2026-09-07), and `CliHandler::
isAvailable` refuses on that field before it looks at security -- so its entry's
`play.set_level_command` is null, and a default here is precisely how a null
turns back into a sibling's string on the way to the wire.
"""

_RENAME_VERB = re.compile(r"^(?:[a-z]+ )?rename$")
"""`character rename` on three trees, and the bare `rename` on the tortoise fork.

Registered there at the top level (`Chat.cpp:850`), with no rename row in its
`characterCommandTable` at all -- so the sibling spelling is an unknown
SUBcommand there and answers with the list of subcommands it does have, which
reads like this app being broken rather than like a command that is elsewhere.
"""


def set_character_level(character: str, level: int, *, verb: str) -> str:
    """`<verb> <character> <level>`.

    1 to 255 is the shape this command takes; whether THIS server allows level
    80 or 60 is its own configured business and it says so itself when asked.

    The verb is the CALLER'S, from `play.set_level_command`, and required for
    the reason `cap` and `realms` are: a tree that has no such command carries a
    null there, and a default here would quietly restore a string for it.
    """
    _character(character)
    _require(
        bool(_LEVEL_VERB.match(verb.strip())),
        f"{verb!r} is not a set-level command this app has measured on a server",
    )
    _require(1 <= level <= 255, f"{level} is not a level this command takes")
    return line(f"{verb.strip()} {character} {level}")


def rename_at_login(character: str, *, verb: str) -> str:
    """`<verb> <character>` -- marked for rename at the next login.

    The optional `reserveName` and `$newName` arguments are deliberately not
    sent: one reserves the old name server-wide and the other renames without
    asking the player, and neither is what a button called "Rename at next
    login" promises. On the tortoise fork a second argument is not optional
    trimming but a DIFFERENT command -- `rename <char> <newname>` renames on the
    spot (`Commands.cpp:12588-12609`) with no name-validity check of its own.
    """
    _character(character)
    _require(
        bool(_RENAME_VERB.match(verb.strip())),
        f"{verb!r} is not a rename command this app has measured on a server",
    )
    return line(f"{verb.strip()} {character}")


def revive(character: str) -> str:
    """`revive <character>` -- named, whatever the help says.

    See the note above: the help documents no argument and the server answers
    one anyway.
    """
    _character(character)
    return line(f"revive {character}")


def mail_items(
    character: str,
    *,
    subject: str,
    body: str,
    items: tuple[tuple[int, int], ...],
    cap: int,
) -> str:
    """`send items <to> "<subject>" "<text>" id:count ...`.

    `cap` is the CALLER'S, measured per tree and named at every call: this
    module is read by four trees and they do not agree, so no number here can be
    right for all of them. Each entry carries its own in `play.mail_item_cap`.

    It was a keyword with a default of twelve until 8.4c, and the default was
    the bug rather than a convenience: `MAX_MAIL_ITEMS` is 1 on the Vanilla
    install and 12 on the TBC one -- the same line of the same header in two
    checkouts on the same box (`src/game/Mails/Mail.h:49`, read 2026-09-07) --
    so a caller that omitted it built a two-attachment line for a server that
    accepts one, and that server's refusal is a `return false` that reaches SOAP
    as a closed connection with nothing in it to read.

    **What this counts is items, and what the server counts is STACKS.** Read
    on the Vanilla checkout, 2026-09-07: `src/game/Chat/Level3.cpp:6385-6397`
    splits one `id:count` into as many pairs as `GetMaxStackSize()` needs BEFORE
    it compares the list to `MAX_MAIL_ITEMS`, and the server's own message
    (mangos_string 53) says "item stacks". So `2589:40` is two stacks there and
    one item here. Nothing sends such a line today -- the only caller is
    `send_gear_set`, and worn gear is always count 1 -- and closing the gap
    honestly needs `item_template.stackable`, which is a read this module has
    not got. It is written down rather than guessed at.
    """
    _character(character)
    _require(bool(items), "a mail with no items in it is not a gift")
    _require(
        len(items) <= cap,
        f"this server carries at most {cap} item{'' if cap == 1 else 's'} in one mail",
    )
    parts = []
    for item, count in items:
        _require(item > 0, f"{item} is not an item id")
        _require(count > 0, f"{count} is not a number of items to send")
        parts.append(f"{item}:{count}")
    subject = _mail_text(subject, MAIL_SUBJECT_CAP)
    body = _mail_text(body, MAIL_BODY_CAP)
    return line(f'send items {character} "{subject}" "{body}" ' + " ".join(parts))


def mail_money(character: str, *, subject: str, body: str, copper: int) -> str:
    """`send money <to> "<subject>" "<text>" <copper>`.

    Copper, because that is what the server counts. A button that says gold
    multiplies before it gets here; this refuses to guess which unit it was
    handed.
    """
    _character(character)
    _require(0 < copper <= _MONEY_CAP, f"{copper} is not an amount of copper this server holds")
    subject = _mail_text(subject, MAIL_SUBJECT_CAP)
    body = _mail_text(body, MAIL_BODY_CAP)
    return line(f'send money {character} "{subject}" "{body}" {copper}')


MAIL_SUBJECT_CAP = 200
MAIL_BODY_CAP = 2000
"""How much of a subject and a body this app will send.

An unbounded argument is a command of unbounded length, and nobody has measured
what this core does with one -- so it is bounded here rather than found out by a
person whose mail vanished.
"""


def _mail_text(text: str, cap: int) -> str:
    """Subject or body, made safe to sit inside the quotes the server parses.

    An allow-list rather than a list of characters somebody thought of, which
    is 8.4a's adversarial review and the argument is the tokenizer: a quote
    closes the argument early and hands the rest of the sentence to the parser
    as item ids; a line break ends the command and starts another with whatever
    followed; a BACKSLASH may escape the closing quote in the core's own
    tokenizer; and tabs, nulls and escapes have no defined behaviour in a
    command line nobody has tested them against.

    So: printable text and spaces go through, everything else BECOMES a space
    -- replaced rather than deleted, so two words do not glue into one -- and
    the result is capped. A field that sanitises down to nothing is sent as a
    single space, because `""` would let the server read the next argument as
    this one.
    """
    kept = "".join("" if c in '"\\' else (c if c.isprintable() else " ") for c in text)[:cap]
    return kept if kept.strip() else " "


SERVER_INFO = "server info"
"""The round trip that proves a channel without changing anything.

It prints the core revision, `Players online: N. Max online: M.` and uptime
(`cmangos.md` records the same shape on that family), so a reply is both proof
the channel works and something a person can read in a capture.
"""


_ENDS_THE_WORLD = ("shutdown", "exit", "idleshutdown")


def ends_the_world(typed: str) -> bool:
    """Does this console line shut the world server down for good (T412)?

    `server shutdown`, `server exit` and `server idleshutdown`, with or without a
    leading dot, in any case, and ABBREVIATED as the consoles accept them
    (AzerothCore, TrinityCore and CMaNGOS all take a command by any prefix of each
    word, so `ser shut 1` is `server shutdown 1`) -- and not their `cancel`.
    Measured on yulon-ubuntu 2026-10-05 (WotLK): `server shutdown 1` saved and
    closed the world cleanly, exit 0, and Docker started it again at once,
    because every service here has `restart: unless-stopped` and an exit the
    container chose itself is not a stop. `server restart` is not one of these:
    Docker bringing it back is what it asks for.

    A prefix the console itself would find ambiguous (`server s`) is refused
    too: nothing a player needs is lost by that, and the console would not have
    run it either.
    """
    words = typed.strip().lstrip(".").lower().split()
    if len(words) < 2 or not "server".startswith(words[0]):
        return False
    if not any(verb.startswith(words[1]) for verb in _ENDS_THE_WORLD):
        return False
    return not (len(words) > 2 and "cancel".startswith(words[2]))


def _require(ok: bool, message: str) -> None:
    if not ok:
        raise CommandError(message)
