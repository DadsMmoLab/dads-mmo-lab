"""Refuse to apply City Bots' SQL while a real account sits on one of its bot ids (T779).

mod-city-bots owns the account ids 12001-12400, named `CITYBOT<id>`
(pjerra/mod-city-bots: `data/sql/db-auth/updates/`, which seeds them with INSERT IGNORE
since PR #7, so it never overwrites a real account on such an id). Its
`data/sql/db-characters/updates/2026_08_22_00_stage_cast_characters.sql` writes
`characters.account = 12001 + (guid - 9000001)` without reading `acore_auth`, so a real
account on one of those ids is handed a bot's character, and a bot session on that id
kicks the player (`WorldSessionMgr.cpp:315-323`). The module cannot tell; Yu'lon can, and
this is the check, run before the importer one-shot that streams the module's SQL.

The range below is the module's own and exists once here. Change it only with the module.
"""

from __future__ import annotations

from pathlib import Path

from yulon import docker
from yulon.apply import ApplyError, DockerSql
from yulon.catalog.catalog import CatalogEntry, DbPasswordUnknown
from yulon.dbreads import SqlReader
from yulon.log import get_logger
from yulon.platform import wsl_location

logger = get_logger(__name__)

MODULE = "mod-city-bots"
"""The module folder (and manifest id) whose SQL this guards."""

FIRST_ACCOUNT_ID = 12001
LAST_ACCOUNT_ID = 12400
"""The account ids mod-city-bots owns. Mirrors the module's own SQL; see the module docstring."""

NAME_PREFIX = "CITYBOT"
"""The module's account names are `CITYBOT<id>`, compared in upper case."""

_SHOWN = 10
"""How many accounts a refusal names; the rest are counted."""

_QUERY = (
    "SELECT id, username FROM account "
    f"WHERE (id BETWEEN {FIRST_ACCOUNT_ID} AND {LAST_ACCOUNT_ID} "
    f"AND UPPER(username) <> CONCAT('{NAME_PREFIX}', id)) "
    f"OR (id NOT BETWEEN {FIRST_ACCOUNT_ID} AND {LAST_ACCOUNT_ID} "
    f"AND UPPER(username) REGEXP '^{NAME_PREFIX}[0-9]+$') "
    "ORDER BY id"
)
"""Read-only. An account in the range that is not the bot of that id, or a bot name outside it."""


class CityBotsAccountRefusal(RuntimeError):
    """The message is the sentence for the player. Nothing was applied."""


def city_bots_on(server_dir: Path, allowed: str) -> bool:
    """Would the importer read City Bots' SQL from this folder, given `allowed`?

    The importer finds a module's SQL in the install's `modules/` folder, so a module
    that is not there (or a folder that cannot be listed) has none to apply. `allowed` is
    what `AC_UPDATES_ALLOWED_MODULES` will be: `docker.ALL_MODULES` or a comma list.
    """
    names = docker.module_dir_names(server_dir)
    if names is None or MODULE not in names:
        return False
    return allowed == docker.ALL_MODULES or MODULE in allowed.split(",")


def _reader(entry: CatalogEntry, server_dir: Path) -> SqlReader:
    """The server's database through `docker exec ... mysql`, as root, password in the env.

    `known_db_password()`, never the published default (T748): an unknowable password is a
    refusal with that text.
    """
    try:
        password = entry.install.known_db_password(server_dir)
    except DbPasswordUnknown as exc:
        raise CityBotsAccountRefusal(str(exc)) from exc
    native = entry.install.native
    located = wsl_location(server_dir)
    return DockerSql(
        entry.container_spec().db,
        password,
        schemas=entry.schema_map(),
        wsl_distro=located[0] if located is not None else None,
        client=native.db.client if native is not None else None,
    )


def refuse_if_accounts_in_the_way(entry: CatalogEntry, server_dir: Path, allowed: str) -> None:
    """Raise `CityBotsAccountRefusal` if City Bots' SQL is about to be applied over real accounts.

    Asks nothing of the database unless City Bots is on (`city_bots_on()`), then reads
    `acore_auth.account` once. A database that cannot be asked is a refusal too: the SQL
    that follows cannot be undone by this app.

    Raises:
        CityBotsAccountRefusal: an account is in the way, the password is unknown, or the
            database could not be read.
    """
    if not city_bots_on(server_dir, allowed):
        return
    reader = _reader(entry, server_dir)
    try:
        rows = reader.query("auth", _QUERY)
    except ApplyError as exc:
        raise CityBotsAccountRefusal(
            "Yu'lon could not check whether real accounts sit on City Bots' account numbers "
            f"({FIRST_ACCOUNT_ID}-{LAST_ACCOUNT_ID}), so nothing was applied. {exc}"
        ) from exc
    clashing = _parse(rows)
    if not clashing:
        return
    shown = ", ".join(f'account {number} "{name}"' for number, name in clashing[:_SHOWN])
    more = len(clashing) - _SHOWN
    if more > 0:
        shown += f" and {more} more"
    logger.warning(f"City Bots SQL refused, accounts in the way: {shown}")
    raise CityBotsAccountRefusal(
        f"City Bots keeps its 400 bots on account numbers {FIRST_ACCOUNT_ID}-{LAST_ACCOUNT_ID}, "
        f"named {NAME_PREFIX}{FIRST_ACCOUNT_ID} to {NAME_PREFIX}{LAST_ACCOUNT_ID}, and "
        f"this server has accounts in the way: {shown}. Applying its SQL would put a bot's "
        "character on a real player's list, and a bot logging in would kick that player. "
        "Nothing was applied. Move those players to other accounts, or remove those "
        "accounts, then try again."
    )


def _parse(rows: str) -> list[tuple[int, str]]:
    """`(id, username)` per tab-separated line of the query's output."""
    found: list[tuple[int, str]] = []
    for line in rows.splitlines():
        if not line.strip():
            continue
        number, _, name = line.partition("\t")
        try:
            found.append((int(number), name.strip()))
        except ValueError as exc:
            raise CityBotsAccountRefusal(
                f"Yu'lon could not read the account check's answer ({line!r}), so nothing "
                "was applied."
            ) from exc
    return found
