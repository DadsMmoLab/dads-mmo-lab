"""The one rule for a game account's name and password (T673).

Every place that makes an account or checks one reads it here: the shared writer
(`controller_wow_wotlk.accounts.create_account()`, which every family's Create and
the command channel go through), the Accounts tab's Create, and the account commands
Delete, Set password and Set GM level (`commands.valid_account_name()` /
`valid_account_password()`). The launcher's Create an account… (T696, not built yet)
is to call `create_problem()`, the same pre-check the Accounts tab runs.
Before T673 Create took any 1-17 character name without a space and any 1-16
character password, and the commands refused most of them, so an account could be
made that the same tab then could not manage.

The characters are the commands' allow-list, because the commands go to the server
as one line of words: a space, a quote or a semicolon is not a character a name or
a password can have (`commands.py`). The shortest name (3) and password (4) are the
same allow-list's. The longest are each core's own `MAX_ACCOUNT_STR` and password
ceiling, read at the catalog's pinned trees on 2026-10-11:

- `azerothcore` (AzerothCore): name 17, password 16. azerothcore-wotlk 2a2211cd and
  8a4e76ea `src/server/game/Accounts/AccountMgr.h:34-35`.
- `mangos_srp6` (CMaNGOS): name 16, password 16. mangos-tbc 15b6ddb4 and mangos-classic
  8ec338a1 `src/game/Accounts/AccountMgr.h:34`; `ChangePassword` holds the password to
  the same 16.
- `mangos_sha` (tortoise-wow, the MaNGOS-Zero line): name 16, password 16. tortoise-wow
  6131a26f and 10393426 `src/game/AccountMgr.h:50`, `AccountMgr.cpp:146`.
- `trinitycore` (TrinityCore112, Centurion): name 16, password 16. TrinityCore112
  25d3e6ef `src/server/game/Accounts/AccountMgr.h:41-42`.

A core's own `account create` refuses a longer one (`AOR_NAME_TOO_LONG`), so a row
written past it is one the server's own commands cannot name.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from yulon.catalog.catalog import CatalogEntry

NAME_MIN = 3
PASSWORD_MIN = 4
PASSWORD_MAX = 16

NAME_MAX: dict[str, int] = {
    "azerothcore": 17,
    "mangos_srp6": 16,
    "mangos_sha": 16,
    "trinitycore": 16,
}
"""Each core's `MAX_ACCOUNT_STR`, by the catalog's `accounts.scheme` (table above)."""

NAME_MAX_ANY = max(NAME_MAX.values())
"""The longest name any core accepts: the ceiling where the core is not known."""

RESERVED_PREFIX = "YULON_"
"""The command channel's accounts (`channel_setup.ACCOUNT_PREFIX`): hidden from the
Accounts tab and the launcher, and refused by every press as the app's own."""

_NAME_CHARS = re.compile(r"[A-Za-z0-9_]+")
_PASSWORD_CHARS = re.compile(r"[A-Za-z0-9_@#%+=!-]+")
PASSWORD_CHARACTERS = "letters A-Z, digits and _ @ # % + = ! -"


def name_max(scheme: str | None) -> int:
    """The longest name `scheme`'s core accepts; `NAME_MAX_ANY` for an unknown one."""
    return NAME_MAX.get(scheme or "", NAME_MAX_ANY)


def name_problem(name: str, *, scheme: str | None = None) -> str:
    """Why `name` cannot be an account on `scheme`'s core, or "" when it can.

    `fullmatch`, so a final line break is not let through (T301's cold review).
    """
    longest = name_max(scheme)
    if not NAME_MIN <= len(name) <= longest:
        return (
            f"An account name is {NAME_MIN} to {longest} characters long on this server; "
            f"{name!r} has {len(name)}."
        )
    if not _NAME_CHARS.fullmatch(name):
        return (
            f"An account name can have only letters A-Z, digits and _ in it, and {name!r} "
            "has other characters. Choose a name without them."
        )
    return ""


def password_problem(password: str) -> str:
    """Why `password` cannot be an account's password, or "" when it can. Never quotes it."""
    if not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        return f"A password is {PASSWORD_MIN} to {PASSWORD_MAX} characters long."
    if not _PASSWORD_CHARS.fullmatch(password):
        return (
            f"A password can have only {PASSWORD_CHARACTERS} in it, so that Set password can "
            "set it again later."
        )
    return ""


def reserved_problem(name: str) -> str:
    """Why `name` is not a player's to create, or "": the command channel's prefix.

    `AHBOT` stays creatable: mod-ah-bot's own question tells the player to create
    that account first, and only Delete refuses it (`useraccounts`).
    """
    if name.strip().upper().startswith(RESERVED_PREFIX):
        return (
            f"Names that start with {RESERVED_PREFIX} are kept for Yu'lon's own command "
            "channel accounts. Choose another name."
        )
    return ""


def bot_prefix(entry: CatalogEntry, server_dir: Path | None = None) -> str | None:
    """The random bots' account prefix on this server, or None for a game with no bots marker.

    With `server_dir`, the one the server really runs with (`dbreads.resolve_marker()`: the
    install's playerbots conf, e.g. `AiPlayerbot.RandomBotAccountPrefix = bot`). Without it,
    or when that conf cannot say (unreadable, set twice, blank), the catalog's default
    (`observability.bots.account_prefix`; Centurion's fixed `PLAYERBOT` rows), which is what
    the module uses when nothing overrides it.
    """
    if entry.observability is None:
        return None
    if server_dir is not None:
        from yulon import dbreads  # dbreads reads the catalog; this module must stay light

        answer = dbreads.resolve_marker(entry, server_dir)
        if answer.marker is not None:
            return answer.marker.prefix
    return entry.observability.bots.account_prefix


def bot_problem(name: str, bot_prefix: str | None) -> str:
    """Why `name` is the random bots' to have, or "": it starts with their account prefix (T749).

    `bot_prefix` is `bot_prefix()`'s answer. Yu'lon's population count reads every
    account whose name starts with it as a bot (`dbreads`: `UPPER(username) LIKE 'PREFIX%'`),
    so a player's account named so would be counted as one.
    """
    if bot_prefix and name.strip().upper().startswith(bot_prefix.upper()):
        return (
            f"Names that start with {bot_prefix.upper()} are the random bots' accounts on this "
            "server. Choose another name."
        )
    return ""


def digits_problem(name: str, *, digits_are_ids: bool) -> str:
    """Why an all-digit `name` cannot be made on a tree that reads digits as an id, or ""."""
    if digits_are_ids and name.strip().isdigit():
        return (
            f"{name.strip()} is made only of digits, and this server's account commands read "
            "digits as an account number, so the account could never be changed or deleted by "
            "its name. Choose a name with at least one letter in it."
        )
    return ""


def create_problem(
    entry: CatalogEntry, name: str, password: str, *, server_dir: Path | None = None
) -> str:
    """Everything a player's Create checks before it writes, or "" (T673; T696 calls it).

    `server_dir` lets the bots' prefix be the one this install runs with (`bot_prefix()`);
    without it the catalog's default is refused.

    The rule the writer applies (name, password, digits), plus the reserved prefix,
    which only a player's account is refused for: the command channel creates its
    own `YULON_` account through the same writer. And the random bots' prefix (T749).
    A name that already exists is the writer's refusal (`accounts.AccountExists`): only
    the database can say.
    """
    from yulon import commands  # the tree's digit rule; commands imports this module

    clean = name.strip()
    scheme = entry.accounts.scheme
    return (
        reserved_problem(clean)
        or bot_problem(clean, bot_prefix(entry, server_dir))
        or name_problem(clean, scheme=scheme)
        or digits_problem(clean, digits_are_ids=entry.id not in commands.NAME_LOOKUP_TREES)
        or password_problem(password)
    )
