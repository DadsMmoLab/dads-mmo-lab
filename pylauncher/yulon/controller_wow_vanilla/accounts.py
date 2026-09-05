"""Create Vanilla game accounts: the shared SRP6 writer, bound to this core's shape.

The arithmetic and the write are not reimplemented here. `accounts.py` in the
WotLK package already carries all three account shapes as a `scheme`
parameter, and the one this game needs — `mangos_srp6` — was solved against
rows two live servers shipped rather than derived from a specification: the
seeded `ADMINISTRATOR` and `PLAYER` accounts of a live TBC and a live **Vanilla**
server, byte-identical on both, are reproduced exactly by
`mangos_srp6_credentials()` when handed their own salt (2026-08-26;
`tests/test_accounts.py` pins the pair). Re-deriving that here would be a
second copy of a modulus.

What `scheme="mangos_srp6"` changes, against AzerothCore's default
-----------------------------------------------------------------
Same `N`, same `g`, same `v = g ^ H(s || H(U:P)) mod N`. Only the storage and
the level differ:

* the credentials go into `account.v` and `account.s` as 64 uppercase hex
  characters, big-endian, where AzerothCore writes binary little-endian into
  `binary(32)` columns;
* the insert names four columns (`username, v, s, joindate`) — no `expansion`,
  no `reg_mail`/`email`, which this core's table does not have;
* there is no `account_access` table. The GM level is `account.gmlevel`, and
  the grant is an `UPDATE`.

The scheme is READ from `entry().accounts.scheme` rather than spelled here.
The catalog field's own description says why that matters: getting it wrong
does not fail loudly, it inserts a row that looks correct and can never log in.

Two things this package has NOT verified for this core
------------------------------------------------------
Neither is invented here — both are AzerothCore facts the shared writer
applies to every scheme — and both are named so the next person does not read
them as measured:

1. **`realmcharacters`.** `create_account()` reproduces AzerothCore's
   `LOGIN_INS_REALM_CHARACTERS_INIT` after the account row, against the
   `realmlist` and `realmcharacters` tables. That those tables exist in this
   core's `realmd` schema has not been checked against a running Vanilla
   server from here. If they do not, the statement fails and the call raises
   `AccountError` — after the account row has already been committed, so the
   account exists and its GM level was never applied. That is a hole that
   announces itself rather than a silently wrong row, which is why it is
   documented instead of guarded by a guess.
2. **The length ceilings.** `MAX_USERNAME` (17) and `MAX_PASSWORD` (16) are
   AzerothCore's `MAX_ACCOUNT_STR`/`MAX_PASS_STR`, applied here to a core
   whose own limits nobody in this repo has read. They are ceilings, so the
   direction of a wrong value is: this app refuses a name or password the
   server would have accepted. Never the reverse.
"""

from __future__ import annotations

from pathlib import Path

from yulon.apply import DockerSql
from yulon.controller_wow_vanilla import docker_ctl, entry

# The writer, imported rather than copied. Its module docstring is the record
# of where the crypto came from and what it was checked against.
from yulon.controller_wow_wotlk import accounts as writer
from yulon.controller_wow_wotlk.accounts import (
    MAX_GM_LEVEL as MAX_GM_LEVEL,
)
from yulon.controller_wow_wotlk.accounts import (
    MAX_PASSWORD as MAX_PASSWORD,
)
from yulon.controller_wow_wotlk.accounts import (
    MAX_USERNAME as MAX_USERNAME,
)
from yulon.controller_wow_wotlk.accounts import (
    NO_GM as NO_GM,
)
from yulon.controller_wow_wotlk.accounts import (
    AccountError as AccountError,
)
from yulon.controller_wow_wotlk.accounts import (
    AccountResult as AccountResult,
)
from yulon.controller_wow_wotlk.accounts import (
    SqlSeam as SqlSeam,
)
from yulon.controller_wow_wotlk.accounts import (
    fold as fold,
)
from yulon.controller_wow_wotlk.accounts import (
    mangos_srp6_credentials as mangos_srp6_credentials,
)


def scheme() -> writer.Scheme:
    """How this core stores an account, per the catalog entry.

    Raises:
        NotImplementedError: the entry declares no scheme. That is the
            catalog's way of saying nobody has measured how this core writes an
            account, and the one thing that must not happen then is a default:
            a wrong scheme writes a row that looks perfectly well-formed and
            can never authenticate. The Accounts surface points a user at
            `entry().accounts.console_command` instead.
    """
    declared = entry().accounts.scheme
    if declared is None:
        raise NotImplementedError(
            f"{entry().name} declares no account scheme, so this app does not know how it "
            f"stores a password. Create the account on the worldserver console instead: "
            f"{entry().accounts.console_command}"
        )
    return declared


def sql_for(db_root_password: str, *, wsl_distro: str | None = None) -> DockerSql:
    """A read+write SQL seam over THIS install's database container and schemas.

    `schemas=entry().schema_map()` is the part that is not decoration: without
    it `DockerSql` addresses AzerothCore's `acore_*` names, and every statement
    dies with `ERROR 1049 Unknown database` against a CMaNGOS install (Discord
    report, 2026-08-26). The manifest key `auth` reaches `realmd` here, which
    is the schema this core keeps `account` in.

    `wsl_distro` says which daemon holds the container: a server living inside
    a WSL distro is `No such container` to a Windows-side Docker Desktop.
    """
    return DockerSql(
        docker_ctl.SPEC.db,
        db_root_password,
        wsl_distro=wsl_distro,
        schemas=entry().schema_map(),
        client=docker_ctl.DB_CLIENT,
    )


def sql_for_install(server_dir: Path, *, wsl_distro: str | None = None) -> DockerSql:
    """`sql_for()` with the password read from the install at `server_dir`.

    This entry's password plan is `generated`: the installer minted one and
    wrote it to the file the plan names, so there is no fixed value a caller
    could carry. `Install.db_password()` knows both shapes.

    Raises:
        AccountError: the entry names a password file and it cannot be read.
            Refused rather than defaulted — falling back to a shared literal is
            how every SQL-backed control came to authenticate as root with the
            password "password" (2026-08-26), and a guess here fails later, at
            the database, in a sentence about access denial that names nothing.
    """
    password = entry().install.db_password(server_dir)
    if password is None:
        raise AccountError(
            f"this install's database password is not knowable: "
            f"{entry().install.password.file} could not be read in {server_dir}. "
            "Nothing was asked of the database."
        )
    return sql_for(password, wsl_distro=wsl_distro)


def create_account(
    sql: SqlSeam,
    username: str,
    password: str,
    *,
    gm_level: int = NO_GM,
) -> AccountResult:
    """Create one Vanilla game account, or bring an existing one up to `gm_level`.

    The shared writer with this game's scheme filled in. Everything it
    documents holds unchanged: an existing account keeps its credentials, the
    level is a floor and never a demotion, a repeated call finishes one that
    died half way, and no password reaches a statement, a log line or an
    exception message.

    `gm_level` defaults to 0 — an ordinary player. The bootstrap administrator
    passes 3 explicitly; defaulting to it would make every family member who
    creates a character able to delete the realm.

    Raises:
        AccountError: the username or password is unusable, `gm_level` is out
            of range, or a statement failed — including the `realmcharacters`
            statement this package has not verified against this core (see the
            module docstring).
        NotImplementedError: the catalog declares no scheme for this core.
    """
    return writer.create_account(sql, username, password, gm_level=gm_level, scheme=scheme())
