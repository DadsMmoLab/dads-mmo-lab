"""Game accounts for Centurion: the shared writer's `trinitycore` scheme, this entry's seam.

No crypto here, for the TBC package's reason: `controller_wow_wotlk/accounts.py`
holds every account shape this app writes, and TrinityCore's is AzerothCore's
SRP6 pair with its own GM-level table (`TRINITYCORE_ACCESS` there). What this
module supplies is what that module takes as arguments:

* the scheme, read from the entry's `accounts.scheme` -- the catalog refuses any
  value but `trinitycore` or null on a TrinityCore entry, and null is refused
  here with the console command rather than guessed;
* the seam: `DockerSql` over this entry's database container, with its schema
  names (`centurion_auth`, not `acore_auth`) and its client (`mysql`);
* the level ceiling: `accounts.level.max_level` (3, `SEC_ADMINISTRATOR`).

**Bot accounts.** Centurion's dump carries four bot accounts, ids 76-79
(`auth_bots.sql`), and has no `AUTO_INCREMENT=` in its schema, so the first
account written here gets id 80 (README.md:235). The install imports before any
account is made, which is what keeps this app's own account off those ids.
"""

from __future__ import annotations

from pathlib import Path

from yulon.apply import DockerSql
from yulon.catalog.catalog import CatalogEntry
from yulon.controller_wow_wotlk import accounts as writer
from yulon.controller_wow_wotlk.accounts import MAX_GM_LEVEL as MAX_GM_LEVEL
from yulon.controller_wow_wotlk.accounts import NO_GM as NO_GM
from yulon.controller_wow_wotlk.accounts import AccountError as AccountError
from yulon.controller_wow_wotlk.accounts import AccountResult as AccountResult
from yulon.controller_wow_wotlk.accounts import SqlSeam as SqlSeam


def sql_for(
    entry: CatalogEntry, db_root_password: str, *, wsl_distro: str | None = None
) -> DockerSql:
    """A `SqlSeam` over this install's database container, with its own schema names."""
    block = entry.install.native
    return DockerSql(
        entry.container_spec().db,
        db_root_password,
        schemas=entry.schema_map(),
        wsl_distro=wsl_distro,
        client=block.db.client if block is not None else None,
    )


def sql_for_install(
    entry: CatalogEntry, server_dir: Path, *, wsl_distro: str | None = None
) -> DockerSql:
    """`sql_for()` with the password the install generated into its file.

    Raises:
        AccountError: the entry names a password file and it cannot be read. Never
            a default: the install generates one per server, and a guess fails at
            the database as "access denied", naming nothing.
    """
    password = entry.install.db_password(server_dir)
    if password is None:
        raise AccountError(
            f"this install's database password is not knowable: "
            f"{entry.install.password.file} could not be read in {server_dir}. "
            "Nothing was asked of the database."
        )
    return sql_for(entry, password, wsl_distro=wsl_distro)


def _scheme(entry: CatalogEntry) -> writer.Scheme:
    scheme = entry.accounts.scheme
    if scheme is None:
        raise AccountError(
            f"{entry.name} does not say how its core stores an account, so this app will not "
            f"write one. Create it at the worldserver console instead: "
            f"{entry.accounts.console_command}"
        )
    return scheme


def create_account(
    entry: CatalogEntry,
    sql: SqlSeam,
    username: str,
    password: str,
    *,
    gm_level: int = NO_GM,
) -> AccountResult:
    """Create one Centurion account, or bring an existing one up to `gm_level`.

    The shared `create_account()` with this entry's scheme and level ceiling bound;
    every rule it documents holds (an existing account keeps its password, the
    level is a floor and never a demotion, a repeated call finishes a half one).
    """
    level = entry.accounts.level
    return writer.create_account(
        sql,
        username,
        password,
        gm_level=gm_level,
        scheme=_scheme(entry),
        max_gm_level=level.max_level if level is not None else MAX_GM_LEVEL,
    )


def reset_own_password(entry: CatalogEntry, sql: SqlSeam, name: str, password: str) -> None:
    """Give THIS APP'S OWN account a new password, in this core's `salt`/`verifier`."""
    writer.reset_own_password(sql, name, password, scheme=_scheme(entry))
