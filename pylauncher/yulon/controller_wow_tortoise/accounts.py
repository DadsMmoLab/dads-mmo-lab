"""Creating a game account on Tortoise: the shared SRP6/SHA writer, bound to this core's shape.

The algorithms are NOT reimplemented here. `controller_wow_wotlk/accounts.py`
already carries all three account shapes — AzerothCore's binary salt/verifier,
CMaNGOS's hex `v`/`s`, and this core's single `sha_pass_hash` column — with the
vectors each was reproduced from, including the pair of hashes a live Tortoise
server logged when it created accounts itself (2026-08-26). Copying 600 lines of
crypto to change one `Literal` would fork the one file in this app where a
silent mistake produces an account that looks perfect and can never log in.

So this module is the binding, and the binding is the whole job:

* the scheme comes from `accounts.scheme` in the entry (`mangos_sha`), so the
  insert names `sha_pass_hash` and the GM grant writes `account.rank` — a
  reserved word, which is why the shared code quotes it. AzerothCore's
  `account_access` table does not exist on this core.
* `sql_for()` carries this game's schema map, so the manifest key `auth`
  resolves to `tw_logon`. Without it every statement addresses `acore_auth`
  and dies with `ERROR 1049 Unknown database`.

That the WotLK package is where a game-agnostic account writer lives is an
accident of it having been first; the honest home is a top-level `yulon/`
module. Moving it is a change to files this package does not own, so it is
named here rather than done.

UNVERIFIED, by the agent that wrote this file, and worth knowing before the
first live account is made on a Tortoise server:

* `create_account()` runs AzerothCore's `realmcharacters` seeding statement on
  every path, whatever the scheme. MaNGOS-lineage realmd schemas do carry a
  `realmcharacters` table, and this app already routes Tortoise through that
  same function today (`ui/controller_view.py` passes `entry.accounts.scheme`),
  so this file changes nothing about it — but nobody has watched that statement
  run against this core. If the table is absent the call raises `AccountError`
  naming the seeding step, after the account row has already been committed.
* the shared writer refuses a username over 17 characters and a password over
  16, which are AzerothCore's `MAX_ACCOUNT_STR`/`MAX_PASS_STR`. Whether this
  core accepts longer was not checked. The failure direction is a refusal, not
  a broken row.
"""

from __future__ import annotations

from pathlib import Path

from yulon.apply import DockerSql
from yulon.controller_wow_tortoise import docker_ctl, game

# Re-exported so a caller of this package never has to name another game's
# package to catch what this one raises or to type what it returns.
from yulon.controller_wow_wotlk.accounts import (
    MAX_GM_LEVEL as MAX_GM_LEVEL,
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
from yulon.controller_wow_wotlk.accounts import Scheme, SqlSeam
from yulon.controller_wow_wotlk.accounts import (
    create_account as _create_account,
)


def scheme() -> Scheme:
    """How this core stores an account, from the entry — never a default.

    Raises:
        NotImplementedError: the entry declares no scheme. That is a core whose
            account row has not been measured, and there is no safe fallback:
            writing AzerothCore's salt/verifier into a table that has neither
            column fails loudly, but writing this core's `sha_pass_hash` into a
            core that expects SRP6 succeeds and produces an account that can
            never authenticate. The caller must send the user to the
            worldserver console (`accounts.console_command` in the entry)
            instead of guessing.
    """
    declared = game.entry().accounts.scheme
    if declared is None:
        raise NotImplementedError(
            f"{game.GAME} declares no account scheme, so this app does not know which columns "
            "its `account` table has. Nothing was written. Create the account at the "
            "worldserver console instead."
        )
    return declared


# No module-level `SCHEME` constant. Resolving it at import would make an entry
# with no declared scheme an ImportError for the whole package, which is a worse
# failure than the refusal `scheme()` raises at the one call that needs it.


def sql_for(
    db_root_password: str, *, wsl_distro: str | None = None, container: str | None = None
) -> DockerSql:
    """A SQL seam over this install's database container, carrying its schema names.

    `container` defaults to `docker_ctl.SPEC.db` and exists for the caller that
    holds a catalog entry of its own: the Server tab builds its seam from the
    entry it was opened with, so that a second install cannot be reached through
    this package's module-level spec.
    """
    return DockerSql(
        container or docker_ctl.SPEC.db,
        db_root_password,
        schemas=game.schemas(),
        wsl_distro=wsl_distro,
        client=game.db().client,
    )


def create_account(
    sql: SqlSeam, username: str, password: str, *, gm_level: int = NO_GM
) -> AccountResult:
    """Create one account on this core, or bring an existing one up to `gm_level`.

    Every rule the shared writer documents holds unchanged: an existing account
    keeps its password, `gm_level` is a floor and never a demotion, a repeated
    call finishes one that died half way, and no password reaches argv, a log
    line or the returned object. What this adds is the scheme, from data.

    Raises:
        AccountError: the name or password is unusable, `gm_level` is out of
            range, or a statement failed.
        NotImplementedError: this entry declares no account scheme (see
            `scheme()`); nothing was written.
    """
    return _create_account(sql, username, password, gm_level=gm_level, scheme=scheme())


def sql_for_install(server_dir: Path, *, wsl_distro: str | None = None) -> DockerSql:
    """`sql_for()` with the password read from the install at `server_dir`.

    This entry generates its database password at install time and writes it to
    a file under the server dir, so there is no constant to fall back on. A
    caller that cannot read that file has an install whose password is not
    knowable from here, and being told so is better than authenticating as root
    with a guess: TBC and Vanilla did exactly that until the file was read, and
    the failure surfaced six clicks later as "access denied".

    Raises:
        AccountError: the entry names a password file and it could not be read.
            Spelled in this module's own vocabulary because every other way this
            path fails already arrives as `AccountError`.
    """
    password = game.entry().install.db_password(server_dir)
    if password is None:
        plan = game.entry().install.password
        raise AccountError(
            f"this install's database password is not knowable: {plan.file} could not be read "
            f"in {server_dir}. Nothing was asked of the database."
        )
    return sql_for(password, wsl_distro=wsl_distro)
