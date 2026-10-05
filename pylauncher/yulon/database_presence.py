"""Is this server's database there at all? Asked before a Start or a Rebuild runs on it (T377).

Seen live on yulon-win11, 2026-10-05: an older WotLK folder whose database
volume had been pruned. Start and Rebuild both ran `compose up`, compose made a
new, EMPTY volume in its place, and the server came up on it -- `Unknown
database acore_auth`, the login server restarting -- until the import was run by
hand. Nothing in Yu'lon had asked whether there was a database to start on.

Two questions, both of Docker and neither of a guess:

* does the volume compose mounts at the database's files exist
  (`docker.database_volume()`, then `docker.volume_exists()`); if not, `missing`
  -- and nothing has been started, so compose has not made an empty one yet;
* if it does, does the login database have its `account` table, asked of a
  database that has said it is healthy. No table is `empty`.

Everything else is `unknown`, and `unknown` never refuses anything: a database
that is slow to come up, a password that cannot be read, a compose file that
keeps its data in a bind mount, a Docker that did not answer -- each of those
starts the server exactly as before. Only a reading that Docker gave refuses.

A database this module started in order to ask is stopped again when the answer
is `missing` or `empty` (nothing may run on it), and also on every answer when
the caller did not ask to keep it (a Rebuild, which has an hour of compiling
before it starts anything). One that was already up is left up.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from yulon import docker
from yulon.catalog.catalog import CatalogEntry
from yulon.log import get_logger

logger = get_logger(__name__)

Presence = Literal["missing", "empty", "present", "unknown"]

DB_HEALTHY_TIMEOUT = 120.0
"""How long the database may take to say healthy before the answer is `unknown`.

`Controller`'s own wait for a restart of containers that already exist; a
first start on a new, empty volume measured 23 s on yulon-ubuntu (2026-08-23).
Past it nothing is concluded, and the Start goes on as before."""

MISSING = (
    "The server's database is missing (Docker's copy was removed). Repair rebuilds it from "
    "the server files; your characters cannot come back unless you have a backup."
)
"""The sentence a refused Start or Rebuild says, for `missing` and `empty` alike."""


@dataclass(frozen=True)
class Reading:
    """What the database was found to be, and why, for the log."""

    presence: Presence
    why: str = ""

    @property
    def refuses(self) -> bool:
        """True for the two answers nothing may start on."""
        return self.presence in ("missing", "empty")


def _client(entry: CatalogEntry) -> str:
    native = entry.install.native
    return native.db.client if native is not None else "mysql"


def _account_table_query(auth: str) -> str:
    # The schema name is the catalog's, never typed in by a person.
    return (
        "SELECT COUNT(*) FROM information_schema.tables "
        f"WHERE table_schema = '{auth}' AND table_name = 'account';"
    )


def read(
    entry: CatalogEntry,
    server_dir: Path,
    wsl_distro: str | None = None,
    *,
    keep_running: bool = False,
) -> Reading:
    """Ask Docker whether `entry`'s database at `server_dir` is missing, empty or present.

    Never raises: every failure to ask is `unknown`. `keep_running` leaves a
    database this started running when the answer lets a start go on, because
    the start that follows would only start it again.
    """
    spec = entry.container_spec()
    try:
        volume = docker.database_volume(spec, server_dir, wsl_distro=wsl_distro)
        if volume is None:
            return Reading("unknown", "compose names no volume for the database's files")
        if not docker.volume_exists(volume, wsl_distro=wsl_distro):
            return Reading("missing", f"Docker has no volume {volume}")
    except docker.DockerCommandError as exc:
        return Reading("unknown", f"Docker could not say whether the volume is there: {exc}")
    password = entry.install.db_password(server_dir)
    if password is None:
        return Reading("unknown", "the database password could not be read")
    auth = entry.schema_map().get("auth")
    if not auth:
        return Reading("unknown", f"{entry.name} names no login database")
    try:
        was_up = spec.db in set(docker.status(wsl_distro=wsl_distro))
    except docker.DockerCommandError as exc:
        return Reading("unknown", f"Docker could not say what is running: {exc}")
    reading = Reading("unknown")
    try:
        docker.start_database(
            spec,
            server_dir,
            timeout=DB_HEALTHY_TIMEOUT,
            because="whether it holds anything was not asked",
            wsl_distro=wsl_distro,
        )
        answer = docker.sql_query(
            spec.db,
            _client(entry),
            password,
            None,
            _account_table_query(auth),
            wsl_distro=wsl_distro,
        ).strip()
    except docker.DockerCommandError as exc:
        reading = Reading("unknown", f"the database could not be asked: {exc}")
    else:
        if answer == "0":
            reading = Reading("empty", f"{auth} has no account table")
        elif answer == "1":
            reading = Reading("present", f"{auth}.account is there")
        else:
            reading = Reading("unknown", f"the database answered {answer!r}")
    if not was_up and (reading.refuses or not keep_running):
        _put_it_back_down(spec, wsl_distro)
    logger.info(f"{entry.id} database at {server_dir}: {reading.presence} ({reading.why})")
    return reading


def _put_it_back_down(spec: docker.ContainerSpec, wsl_distro: str | None) -> None:
    """Stop the database this module started; a failure is logged, never raised."""
    try:
        docker.stop_containers([spec.db], wsl_distro=wsl_distro)
    except docker.DockerCommandError as exc:
        logger.warning(f"{spec.db} was started to ask about it and could not be stopped: {exc}")
