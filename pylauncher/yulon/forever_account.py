"""The account Play makes on a WoW Forever realm, and the password Yu'lon keeps for it.

A WoW Forever client logs in with a Battle.net-style account: an e-mail-shaped name and a
password, checked with SRP6 v2 (PBKDF2-SHA512) by bnetserver. Yu'lon makes one account
per install, `<account_name>@<account_domain>`, through the world server's own console
(`bnetaccount create`, typed by /opt/forever/yulon/console.sh in the server image, which
feeds the console's pipe). The answer is read back from the auth database, not from the
console's words.

The password is the catalog's fixed one where it names one (a personal realm on
127.0.0.1: the owner asked for mmodad / pass), else one made here. It is kept the way
FFXI's is (`loader_account`: owner-only, under the config dir), keyed by this install, and
kept BEFORE the account is made, so an account is never made with a password Yu'lon failed
to write down. An account that is there with another password is set to this one.
"""

from __future__ import annotations

import secrets
import string
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from yulon import docker, loader_account
from yulon.catalog import composegen
from yulon.catalog.catalog import ForeverLogin, SingleEntry
from yulon.log import get_logger

logger = get_logger(__name__)

PASSWORD_LENGTH = 12
"""Letters and digits; bnetserver takes at most 16 characters."""

WORLD_SERVICE = "world"
DATABASE_SERVICE = "database"
"""The services of a ForeverCore stack Play talks to (catalog/installers/wow-forever/stack)."""

CONSOLE = "/opt/forever/yulon/console.sh"
"""The server image's script that types one line into worldserver's console."""

CREATE_WAIT_SECONDS = 20.0
"""How long the auth database is read for the account after the console was told to make it."""


class AccountError(RuntimeError):
    """The account could not be made; the message says what to do."""


DEFAULT_DOMAIN = "local"
"""bnetserver's `LoginREST.DefaultAccountDomain` (0.1.36): added to a name typed without an `@`."""


def account_email(login: ForeverLogin) -> str:
    """The account's full name on the realm (bnetserver keeps it upper-cased)."""
    return f"{login.account_name}@{login.account_domain}"


def typed_name(login: ForeverLogin) -> str:
    """What the player types at login: the short name, where bnetserver adds the domain itself."""
    if login.account_domain == DEFAULT_DOMAIN:
        return login.account_name
    return account_email(login)


def make_password(*, choice: Callable[[str], str] = secrets.choice) -> str:
    """A password a player can read and type: lowercase letters and digits, no look-alikes."""
    alphabet = "".join(c for c in string.ascii_lowercase + string.digits if c not in "l1o0")
    return "".join(choice(alphabet) for _ in range(PASSWORD_LENGTH))


def container_of(entry: SingleEntry, service: str) -> str:
    """The container of one of the stack's services."""
    stack = entry.install.stack
    if stack is not None:
        for item in stack.services:
            if item.service == service:
                return item.container
    raise AccountError(f"{entry.name} has no {service} service to make an account with.")


@dataclass(frozen=True)
class AccountSeams:
    """Everything this module reaches outside itself through. Real by default."""

    exec_output: Callable[..., object] = docker.exec_output
    install_id: Callable[[Path], str] = composegen.install_id
    recall: Callable[..., str | None] = loader_account.recall
    remember: Callable[..., None] = loader_account.remember
    forget: Callable[..., None] = loader_account.forget
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic


def ensure(
    entry: SingleEntry, server_dir: Path, *, seams: AccountSeams | None = None
) -> tuple[str, str]:
    """This install's account, made if it is not there yet: (name, password)."""
    s = seams if seams is not None else AccountSeams()
    play = entry.play
    login = play.forever if play is not None else None
    if login is None:
        raise AccountError(f"{entry.name} does not log in with a WoW Forever account.")
    email = account_email(login)
    server = f"{entry.id}:{s.install_id(server_dir)}"
    world = container_of(entry, WORLD_SERVICE)
    database = container_of(entry, DATABASE_SERVICE)
    kept = s.recall(entry.id, server, email)
    there = _exists(s, database, email)
    # The catalog's fixed password wins; else the kept one (a realm made again since, a
    # reinstall, gets the account back with it); else a new one.
    password = login.account_password or kept or make_password()
    if there and kept == password:
        return email, password
    try:
        if password != kept:
            s.remember(entry.id, server, email, password)
    except OSError as exc:
        raise AccountError(
            f"The account's password could not be kept ({exc.strerror or exc}), so no account "
            "was made. Check that Yu'lon's settings folder can be written, then press Play again."
        ) from exc
    command = (
        f"bnetaccount set password {email} {password} {password}"
        if there
        else f"bnetaccount create {email} {password}"
    )
    proc = s.exec_output(world, [CONSOLE, command], timeout=30.0)
    if getattr(proc, "returncode", 1) != 0:
        s.forget(entry.id, server, email)
        said = (getattr(proc, "stderr", "") or getattr(proc, "stdout", "") or "").strip()
        raise AccountError(
            "The world server's console did not take the account command"
            + (f" ({said})" if said else "")
            + ". Is the server running? Start it, wait until it is ready, then press Play again."
        )
    if there:
        logger.info(f"set a new kept password for {email} on {entry.id}")
        return email, password
    deadline = s.clock() + CREATE_WAIT_SECONDS
    while not _exists(s, database, email):
        if s.clock() >= deadline:
            s.forget(entry.id, server, email)
            raise AccountError(
                f"The world server did not make the account {email}. Its log on the server's "
                "tab says why; press Play again once the server is ready."
            )
        s.sleep(1.0)
    logger.info(f"made {email} on {entry.id}")
    return email, password


def _exists(s: AccountSeams, database: str, email: str) -> bool:
    """Whether the auth database has `email` (bnetserver keeps it upper-cased)."""
    query = (
        f"SELECT COUNT(*) FROM auth.battlenet_accounts WHERE email = UPPER('{_sql_text(email)}')"
    )
    proc = s.exec_output(
        database,
        ["sh", "-c", f'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -B -e "{query}" 2>/dev/null'],
        timeout=30.0,
    )
    out = (getattr(proc, "stdout", "") or "").strip()
    return getattr(proc, "returncode", 1) == 0 and out.isdigit() and int(out) > 0


def _sql_text(value: str) -> str:
    """`value` for a single-quoted SQL literal; the names here are letters, digits, @ and dots."""
    if any(c not in string.ascii_letters + string.digits + "@.-_" for c in value):
        raise AccountError(f"{value!r} is not an account name Yu'lon makes.")
    return value
