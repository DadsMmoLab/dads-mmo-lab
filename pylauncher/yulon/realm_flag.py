"""Mark a realm offline in the login database while its world server is not listening (T577).

Seen live on yulon-ubuntu, 2026-10-08: the Tortoise world server only ever CLEARS the
offline bit of its realm row (`Master.cpp:228`, right before it starts listening) and
never sets it, so the authserver listed the realm as online for the whole minute or more
the world spent loading, and a 1.x client sent to a world that was not listening went
back to the realm list with no error. This app sets the bit before it starts the world and
before it stops it; the core clears it when the world is up, and the authserver re-reads the
row every ten seconds, so the realm list says Offline instead.

Driven by the catalog (`Realmlist.offline_flag_column`), which names the column only for a
core measured to need it. Every call here is best effort: the realm list being wrong is
never a reason a server does not start or stop.
"""

from __future__ import annotations

from pathlib import Path

from yulon import docker
from yulon.catalog.catalog import CatalogEntry
from yulon.log import get_logger

logger = get_logger(__name__)

REALM_FLAG_OFFLINE = 2
"""The core's `REALM_FLAG_OFFLINE`, the same bit on every MaNGOS tree."""

DB_HEALTHY_TIMEOUT = 120.0
"""How long the database may take to say healthy before the realm is left as it was."""


def online_statement(entry: CatalogEntry) -> str | None:
    """The UPDATE that clears the offline bit again; None when the entry needs none."""
    realmlist = entry.realmlist
    column = realmlist.offline_flag_column
    if column is None:
        return None
    return (
        f"UPDATE {entry.databases.auth}.{realmlist.table} "
        f"SET {column} = {column} & ~{REALM_FLAG_OFFLINE} WHERE id={realmlist.realm_id};"
    )


def offline_statement(entry: CatalogEntry) -> str | None:
    """The UPDATE that sets the offline bit on this entry's realm row; None when it needs none."""
    realmlist = entry.realmlist
    column = realmlist.offline_flag_column
    if column is None:
        return None
    return (
        f"UPDATE {entry.databases.auth}.{realmlist.table} "
        f"SET {column} = {column} | {REALM_FLAG_OFFLINE} WHERE id={realmlist.realm_id};"
    )


def mark_offline(
    entry: CatalogEntry,
    spec: docker.ContainerSpec,
    server_dir: Path,
    *,
    wsl_distro: str | None = None,
    start_database: bool = True,
) -> bool:
    """Set the realm's offline bit; True when the statement ran, False when not run or failed.

    `start_database` is True before a start (the database is about to be needed anyway, and
    compose would wait for it) and False before a stop, which must not start a database to
    tell it the realm is closing. Never raises: a database that cannot be reached, a password
    that cannot be read or a statement that fails is logged and the caller carries on.
    """
    return _run(
        entry,
        spec,
        server_dir,
        offline_statement(entry),
        wsl_distro=wsl_distro,
        start_database=start_database,
        only_with_world_up=False,
    )


def clear_offline_if_world_up(
    entry: CatalogEntry,
    spec: docker.ContainerSpec,
    server_dir: Path,
    *,
    wsl_distro: str | None = None,
) -> bool:
    """Take the offline bit off again when a Stop or a replace gave up and the world still runs.

    The core only ever clears the bit when it starts, so a world left running behind a bit
    this app set before a cancelled Stop would show Offline until its next restart. Does
    nothing when the world container is not running (a start will mark it again) or when the
    database is down. Never raises.
    """
    return _run(
        entry,
        spec,
        server_dir,
        online_statement(entry),
        wsl_distro=wsl_distro,
        start_database=False,
        only_with_world_up=True,
    )


def _run(
    entry: CatalogEntry,
    spec: docker.ContainerSpec,
    server_dir: Path,
    statement: str | None,
    *,
    wsl_distro: str | None,
    start_database: bool,
    only_with_world_up: bool,
) -> bool:
    if statement is None:
        return False
    password = entry.install.db_password(server_dir)
    if password is None:
        logger.warning(f"{entry.id}: the database password could not be read; realm row not set")
        return False
    native = entry.install.native
    client = native.db.client if native is not None else "mysql"
    try:
        if start_database:
            docker.start_database(
                spec,
                server_dir,
                timeout=DB_HEALTHY_TIMEOUT,
                because="the realm was not marked offline",
                wsl_distro=wsl_distro,
            )
        else:
            up = set(docker.status(wsl_distro=wsl_distro))
            if spec.db not in up or (only_with_world_up and spec.world not in up):
                return False
        docker.sql_query(spec.db, client, password, None, statement, wsl_distro=wsl_distro)
    except docker.DockerCommandError as exc:
        logger.warning(f"{entry.id}: the realm row was not set: {exc}")
        return False
    logger.info(f"{entry.id}: realm row set in {entry.databases.auth}: {statement}")
    return True
