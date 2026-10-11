"""CMaNGOS's four seeded accounts locked before a TBC or Vanilla server starts (T668).

mangos-tbc's and mangos-classic's `realmd.sql` ship `ADMINISTRATOR` (GM 3), `GAMEMASTER` (2),
`MODERATOR` (1) and `PLAYER` (0), each with password = username, and the auth port is open on
every interface. Anyone who could reach 3724 could log in as ADMINISTRATOR.

**What is done.** Each of the four rows that is STILL seeded -- its stored `v` is the verifier
of `(name, name)` under the row's own salt `s` -- gets a fresh random password nobody is told:
a new `(s, v)` from `mangos_srp6_credentials()`. Chosen over a delete because nothing is lost
and nothing dangling is left (`realmcharacters`, `characters.account` and `account_access`
rows keep pointing at a row that exists), and over a ban row because a ban is lifted by one
GM command and shows as a punishment; a password nobody knows is just an account nobody uses.
The player can always give the row a password from the Accounts tab or `.account password`.

**Only while seeded.** The SELECT names the four usernames and nothing else; a row is touched
only when its `v` equals the verifier of its own name as password, and the UPDATE repeats that
`v` and `s` in its WHERE, so a password a player sets between the read and the write wins.
A player who already gave ADMINISTRATOR a password of their own keeps it. The check is made
with the row's own salt rather than a pinned pair so it holds whatever salt a future upstream
seed carries. Every value in a statement is a hex literal.

**Idempotent.** The second run reads the random `v`, sees it is not the seeded one, and writes
nothing.

**Also without a press of Start (`start_database=False`).** A server Docker restarts after a
reboot (`restart: unless-stopped`) serves the seeded rows until the player presses something. The
Server tab asks `settle()` once each time its poll finds the database running (it asks nothing
while it is down), and the Accounts tab asks before it reads its list, both off the GUI thread and
only once WSL says the distro runs. It starts nothing and is idempotent.

**When.** Right before every start of the world, beside the T657 key rename: `Controller.start()`
(Start, Restart, recreate), the install's `up` (so a new install is locked before its auth
server has ever listened) and a rebuild's recreate. Every existing TBC/Vanilla install reaches
one of them without the player doing anything. Cores whose accounts are not `mangos_srp6` are
not asked anything (Tortoise seeds no such accounts; AzerothCore and TrinityCore seed none).

Best effort: a database that cannot be asked, or a password that cannot be read, never stops a
start, but the line returned says the accounts could not be locked and are still open. A name
is said as locked only when its UPDATE changed a row.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

from yulon import docker
from yulon.log import get_logger

if TYPE_CHECKING:
    from yulon.catalog.catalog import CatalogEntry

logger = get_logger(__name__)

NAMES = ("ADMINISTRATOR", "GAMEMASTER", "MODERATOR", "PLAYER")
"""The usernames CMaNGOS's `realmd.sql` seeds; the catalog's `exclude_usernames` has them too."""

SCHEME = "mangos_srp6"
"""The account scheme of the cores that seed them (TBC and Vanilla)."""

DB_HEALTHY_TIMEOUT = 120.0
"""How long the database may take to say healthy before the accounts are left as they were."""

LOCKED = (
    "The server comes with built-in accounts ({names}) whose password was the same as the name, "
    "so anyone who could reach this server could log in with it. Yu'lon gave them random "
    "passwords nobody knows. Make your own account in the Accounts tab; accounts you made are "
    "untouched."
)
ADMINISTRATOR_AGAIN = " To use ADMINISTRATOR again, set its password in the Accounts tab."
"""Added when ADMINISTRATOR was among the locked: a player who kept their own is not told this."""
NOT_LOCKED = (
    "Yu'lon could not lock the server's built-in accounts ({why}). Until it can, ADMINISTRATOR, "
    "GAMEMASTER, MODERATOR and PLAYER still log in with their own name as the password, and "
    "anyone who can reach this server can use them."
)


def _writer() -> ModuleType:
    """The shared SRP6 writer (`controller_wow_wotlk.accounts`), imported when first needed.

    Not at module level: `catalog.native` imports this module for a `Seams` default, and the
    writer's own import chain reaches `catalog.native` again, so a top-level import here made
    `import yulon.seeded_accounts` and `import yulon.ui.controller_view` fail cold (T668).
    This module stays a leaf of the import graph: only `docker` and `log` at the top.
    """
    from yulon.controller_wow_wotlk import accounts

    return accounts


def _is_seeded(name: str, s: str, v: str) -> bool:
    """Whether `v` is the verifier of `name` with password `name` under the salt `s`."""
    try:
        raw = bytes.fromhex(s)[::-1]
        if len(raw) != 32:
            return False
        writer = _writer()
        verifier: str = writer.mangos_srp6_credentials(
            writer.fold(name), writer.fold(name), salt=raw
        )[1]
        return v.upper() == verifier
    except ValueError:
        return False


def lock(ask: Callable[[str], str], schema: str) -> list[str]:
    """Give every still-seeded account a random password; the names changed, in `NAMES` order.

    `ask` runs the statements it is given in ONE client session and returns the rows of the
    last SELECT, tab-separated; `schema` is the auth database's name. A name is returned only
    when its guarded UPDATE changed a row (`ROW_COUNT()`, asked in the same session since the
    count is per connection): a password set between the read and the write is not named.
    Raises whatever `ask` raises.
    """
    writer = _writer()
    _text_literal, fold = writer._text_literal, writer.fold  # noqa: N806, SLF001
    names = ", ".join(_text_literal(name) for name in NAMES)
    rows = ask(f"SELECT username, s, v FROM {schema}.account WHERE username IN ({names});")
    found: dict[str, tuple[str, str]] = {}
    for line in rows.splitlines():
        parts = line.rstrip("\r").split("\t")
        if len(parts) == 3 and fold(parts[0]) in NAMES:
            found[fold(parts[0])] = (parts[1], parts[2])
    changed: list[str] = []
    for name in NAMES:
        if name not in found or not _is_seeded(name, *found[name]):
            continue
        old_s, old_v = found[name]
        new_s, new_v = writer.mangos_srp6_credentials(name, secrets.token_hex(16))
        counted = ask(
            f"UPDATE {schema}.account SET v = {_text_literal(new_v)}, s = {_text_literal(new_s)}"
            f" WHERE username = {_text_literal(name)}"
            f" AND v = {_text_literal(old_v)} AND s = {_text_literal(old_s)};"
            " SELECT ROW_COUNT();"
        )
        if counted.strip() == "1":
            changed.append(name)
    return changed


def settle(
    entry: CatalogEntry,
    spec: docker.ContainerSpec,
    server_dir: Path,
    *,
    wsl_distro: str | None = None,
    start_database: bool = True,
) -> str | None:
    """Lock this server's seeded accounts before a start; the line to say, or None.

    `start_database=False` is for the app opening on a server Docker brought back by itself
    (`restart: unless-stopped`): it starts nothing, so a stopped server stays stopped. The
    CALLER has seen the database running (`Controller.lock_seeded_accounts_if_up()` asks its
    own `status()`, the seam the status poll reads), so this asks `docker` nothing about it.

    None when the core does not seed them or nothing was still seeded. A password that cannot
    be read is `NOT_LOCKED`, like a database that cannot be asked. Never raises: see the
    module docstring.
    """
    if entry.accounts.scheme != SCHEME:
        return None
    password = entry.install.db_password(server_dir)
    if password is None:
        logger.warning(f"{entry.id}: the database password could not be read; accounts not locked")
        return NOT_LOCKED.format(why="the database password could not be read")
    native = entry.install.native
    client = native.db.client if native is not None else "mysql"

    def ask(statement: str) -> str:
        return docker.sql_query(spec.db, client, password, None, statement, wsl_distro=wsl_distro)

    try:
        if start_database:
            docker.start_database(
                spec,
                server_dir,
                timeout=DB_HEALTHY_TIMEOUT,
                because="the built-in accounts were not locked",
                wsl_distro=wsl_distro,
            )
        changed = lock(ask, entry.databases.auth)
    except docker.DockerCommandError as exc:
        logger.warning(f"{entry.id}: the seeded accounts were not locked: {exc}")
        return NOT_LOCKED.format(why=_why(exc))
    if not changed:
        return None
    logger.info(f"{entry.id}: gave the seeded accounts {', '.join(changed)} random passwords")
    line = LOCKED.format(names=", ".join(changed))
    return line + ADMINISTRATOR_AGAIN if "ADMINISTRATOR" in changed else line


def _why(exc: Exception) -> str:
    text = str(exc).strip().splitlines()
    return text[0] if text else "the database did not answer"
