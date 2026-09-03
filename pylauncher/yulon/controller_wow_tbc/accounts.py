"""Game accounts for WoW TBC: the CMaNGOS binding of the shared SRP6 writer.

No crypto is written here. `controller_wow_wotlk/accounts.py` already carries
all three account shapes this app knows — AzerothCore's binary `salt`/`verifier`
with the level in `account_access`, tortoise's `sha_pass_hash` with the level in
`account.rank`, and CMaNGOS's `mangos_srp6`: the same modulus, generator and
`H(s || H(U:P))` as AzerothCore, stored as uppercase big-endian hex in `v`/`s`
with the level in `account.gmlevel`. That third shape was solved from rows the
servers themselves shipped — the seeded ADMINISTRATOR and PLAYER accounts read
off a live TBC and a live Vanilla server on 2026-08-26, byte-identical on both
— and `tests/test_accounts.py` pins them. Duplicating any of it here would be a
second copy of an algorithm whose whole value is that it matches a measurement.

What this module supplies is the two facts that module takes as arguments:

* the scheme, read from `accounts.scheme` in the entry rather than spelled here
  — a wrong scheme does not fail, it writes a row that looks perfect and can
  never log in;
* the seam: `DockerSql` over `tbc-db` with THIS core's schema names, so a
  statement goes to `realmd` and not to `acore_auth` (`Unknown database
  'acore_auth'` on every CMaNGOS install was a real report, 2026-08-26).

`expansion` is deliberately absent from the insert, and that is right for this
core rather than an omission: the installer's SQL plan ends with an "expansion
unlock" phase that sets `realmd.account.expansion` to a column default of 1 and
updates every existing row (`catalog.json`), so an account created afterwards
gets TBC without this app naming a number the schema already carries.

UNVERIFIED, and the reason it is named here rather than papered over: the
shared `create_account()` follows its insert with AzerothCore's
`realmcharacters` seeding statement on every scheme. Whether CMaNGOS's `realmd`
has that table has NOT been checked against a TBC schema by this project. If it
does not, account creation on this game raises `AccountError` naming the failed
statement — a loud failure, not a silent one — and the fix belongs in the shared
module, not in a fork of it here.
"""

from __future__ import annotations

from pathlib import Path

from yulon.apply import DockerSql
from yulon.controller_wow_tbc import docker_ctl

# The `as` spelling is what mypy's --no-implicit-reexport asks for: these names
# are this module's public surface too, so a caller need not know which package
# the shared implementation happens to live in.
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
    create_account as _create_account,
)

SCHEME = docker_ctl.ENTRY.accounts.scheme
"""How this core stores an account, from the entry: `mangos_srp6` for TBC.

`None` there means "this app does not write accounts for this core, use the
worldserver console instead", which is why `create_account()` below checks it
rather than assuming a string arrived.
"""

CONSOLE_COMMAND = docker_ctl.ENTRY.accounts.console_command
"""What to type at the `mangos>` prompt when the SQL path is not available."""


def sql_for(root_password: str, *, wsl_distro: str | None = None) -> DockerSql:
    """A `SqlSeam` over this install's database container, with its own schema names.

    `schemas=` is the load-bearing argument. `DockerSql` defaults to
    `apply.DB_NAMES`, which is AzerothCore's `acore_*`, and it puts the schema
    in argv — so without this every account statement would run
    `mariadb -uroot acore_auth` against a server whose auth schema is `realmd`.
    """
    return DockerSql(
        docker_ctl.SPEC.db,
        root_password,
        schemas=docker_ctl.ENTRY.schema_map(),
        wsl_distro=wsl_distro,
    )


def sql_for_install(server_dir: Path, *, wsl_distro: str | None = None) -> DockerSql:
    """`sql_for()` with the password read from the install at `server_dir`.

    TBC was the one CMaNGOS package without this, and the gap was not cosmetic.
    This entry's password plan is `generated` -- the installer mints a password
    and writes it to `.db_password` -- so there is no fixed value any caller can
    carry, and without this function a caller wanting a TBC account had no way
    to obtain the one thing it needs. Vanilla and Tortoise both had it; TBC's
    plan is identical to theirs, so the asymmetry was an omission rather than a
    difference between the games (review, 2026-09-03).

    Raises:
        AccountError: the entry names a password file and it cannot be read.
            Refused rather than defaulted -- falling back to a shared literal is
            how every SQL-backed control came to authenticate as root with the
            password "password" (2026-08-26), and a guess here fails later, at
            the database, in a sentence about access denial that names nothing.
    """
    password = docker_ctl.ENTRY.install.db_password(server_dir)
    if password is None:
        raise AccountError(
            f"this install's database password is not knowable: "
            f"{docker_ctl.ENTRY.install.password.file} could not be read in {server_dir}. "
            "Nothing was asked of the database."
        )
    return sql_for(password, wsl_distro=wsl_distro)


def create_account(
    sql: SqlSeam, username: str, password: str, *, gm_level: int = NO_GM
) -> AccountResult:
    """Create one TBC game account, or bring an existing one up to `gm_level`.

    The shared `create_account()` with this game's scheme bound. Every rule it
    documents still holds and is not restated here: an existing account keeps
    its password, `gm_level` is a floor and never a demotion, and a repeated
    call finishes one that died half way.

    Raises:
        AccountError: everything the shared function raises, plus the case
            where the entry declares no scheme at all — that is a core this app
            must not write rows for, and the honest answer is a refusal naming
            the console command rather than a guessed row shape.
    """
    # Read into a local before it is checked, so what is refused and what is
    # passed on are provably the same value.
    scheme = SCHEME
    if scheme is None:
        raise AccountError(
            f"{docker_ctl.GAME} does not say how its core stores an account, so this app will "
            f"not write one. Create it at the worldserver console instead: {CONSOLE_COMMAND}"
        )
    return _create_account(sql, username, password, gm_level=gm_level, scheme=scheme)
