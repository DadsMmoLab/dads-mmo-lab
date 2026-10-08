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

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from yulon import docker
from yulon.catalog.catalog import CatalogEntry
from yulon.dbreads import SqlReader
from yulon.log import get_logger

logger = get_logger(__name__)

REALM_FLAG_OFFLINE = 2
"""The core's `REALM_FLAG_OFFLINE`, the same bit on every MaNGOS tree."""

DB_HEALTHY_TIMEOUT = 120.0
"""How long the database may take to say healthy before the realm is left as it was."""


def flags_statement(entry: CatalogEntry) -> str | None:
    """The SELECT that reads this entry's realm flags (on the auth schema); None if it has none."""
    realmlist = entry.realmlist
    column = realmlist.offline_flag_column
    if column is None:
        return None
    return f"SELECT {column} FROM {realmlist.table} WHERE id={realmlist.realm_id};"


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
    unless_world_up: bool = False,
    quiet: bool = False,
) -> bool:
    """Set the realm's offline bit; True when the statement ran, False when not run or failed.

    `start_database` is True before a start (the database is about to be needed anyway, and
    compose would wait for it) and False before a stop, which must not start a database to
    tell it the realm is closing.

    `unless_world_up` is for a start: a world container that is already running will not
    clear the bit again (only a world that starts does), so marking it would leave a healthy
    realm offline. That is reachable, since Start stays live while only the authserver is
    down, and `compose up` then starts nothing but the authserver. Not asked of a Stop or a
    replace, which mark a world that is up on purpose.

    `quiet` logs at debug level only, for the dashboard tick (T581), which says once per
    spell what it did rather than once per attempt.

    Never raises: a database that cannot be reached, a password
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
        skip_if_world_up=unless_world_up,
        quiet=quiet,
    )


def clear_offline_if_world_up(
    entry: CatalogEntry,
    spec: docker.ContainerSpec,
    server_dir: Path,
    *,
    wsl_distro: str | None = None,
    quiet: bool = False,
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
        skip_if_world_up=False,
        quiet=quiet,
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
    skip_if_world_up: bool,
    quiet: bool = False,
) -> bool:
    if statement is None:
        return False
    say = logger.debug if quiet else logger.info
    warn = logger.debug if quiet else logger.warning
    password = entry.install.db_password(server_dir)
    if password is None:
        warn(f"{entry.id}: the database password could not be read; realm row not set")
        return False
    native = entry.install.native
    client = native.db.client if native is not None else "mysql"
    try:
        if skip_if_world_up and spec.world in set(docker.status(wsl_distro=wsl_distro)):
            say(f"{entry.id}: the world is already running; realm row left as it is")
            return False
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
        warn(f"{entry.id}: the realm row was not set: {exc}")
        return False
    say(f"{entry.id}: realm row set in {entry.databases.auth}: {statement}")
    return True


# -- T581: the dashboard tick keeps the row honest while this app is open ------------------

_HOLD_LOCK = threading.Lock()
"""Taken by a deliberate hold's start and end, and by the tick's check-and-clear (T581).

So a Stop that begins while the tick is clearing waits for that clear and marks after it,
and a clear never lands between a Stop's mark and its end."""

_holds: dict[str, int] = {}
_epochs: dict[str, int] = {}


@contextmanager
def deliberately_offline(spec: docker.ContainerSpec) -> Iterator[None]:
    """Hold this install's realm offline on purpose for the length of the block (T581).

    Yu'lon's own Stop and a rebuild's replace mark the realm offline over a world that is
    still up, on purpose, and the world can take a long time to save on the way down. The
    dashboard tick clears a bit it finds set over a world that has been ready for a while
    (`Keeper`); inside this block it does not. Keyed by the database container, which is
    the row's home. In-process only: a Stop run by another process is not seen.
    """
    key = spec.db
    with _HOLD_LOCK:
        _holds[key] = _holds.get(key, 0) + 1
        _epochs[key] = _epochs.get(key, 0) + 1
    try:
        yield
    finally:
        with _HOLD_LOCK:
            _holds[key] -= 1
            if not _holds[key]:
                del _holds[key]
            _epochs[key] = _epochs.get(key, 0) + 1


def held(spec: docker.ContainerSpec) -> bool:
    """Whether a deliberate hold is in force on this install's realm right now."""
    with _HOLD_LOCK:
        return bool(_holds.get(spec.db))


def _epoch(spec: docker.ContainerSpec) -> int:
    with _HOLD_LOCK:
        return _epochs.get(spec.db, 0)


MARK_RETRY = timedelta(seconds=30)
"""How long the tick waits before it tries a failed mark on the same run again."""

FLAGS_READ_EVERY = timedelta(seconds=60)
"""How often the tick reads the realm's flags over a world that is up."""

UNCONFIRMED_RETRY = timedelta(minutes=10)
"""How long a run whose log did not show the ready marker waits before it is searched again."""

CLEAR_AFTER = timedelta(seconds=60)
"""How long a run must have been seen ready before a bit still set on it is called stuck.

The core clears the bit a few statements after it prints its ready marker (`World.cpp:2399`
inside `SetInitialWorldSettings`, called at `Master.cpp:194`; the clear at `:228`), so a bit
still set a minute later is not the core's to clear any more. The install's own watch after
the ready marker is the same length (`native.READY_GRACE_SECONDS`)."""


class Keeper:
    """Keeps the realm row's offline bit in step with the world while the app is open (T581).

    Fed by the dashboard tick (`dashboard.Dashboard.tick`, every five seconds on a worker
    thread, never two at once) with what it already read: the world container's status,
    its run, and whether that run said ready.

    - The world `restarting`, or `running` on a run that has not said ready: mark the realm
      offline, once per run, only with the database already up. That is Docker's restart
      policy reviving a crashed world without this app, which the core never marks.
    - The world `running` on a run seen ready at least `CLEAR_AFTER` ago, its log showing the
      ready marker (`said_ready`; uptime alone is no proof), and the bit still set (read once
      a minute): take it off, unless a deliberate hold
      (`deliberately_offline`) is in force or began or ended since the tick started. That is
      a cancelled Stop whose own put-back failed. A mark of a running world is followed by
      the same check, so a mark that landed just after the core cleared the bit comes off.
    - Anything else (stopped, gone, unread): nothing. Yu'lon's own Stop and Start mark it.

    Says once per spell what it did, never per tick, and never raises.
    """

    def __init__(
        self,
        entry: CatalogEntry,
        spec: docker.ContainerSpec,
        server_dir: Path,
        sql: SqlReader,
        *,
        wsl_distro: str | None = None,
        now: Callable[[], datetime] | None = None,
        mark: Callable[..., bool] | None = None,
        clear: Callable[..., bool] | None = None,
    ) -> None:
        self.entry = entry
        self.spec = spec
        self.server_dir = server_dir
        self.sql = sql
        self.wsl_distro = wsl_distro
        self._now = now or (lambda: datetime.now(UTC))
        self._mark = mark or mark_offline
        self._clear = clear or clear_offline_if_world_up
        self._marked_run: str | None = None
        self._tried: tuple[str, datetime] | None = None
        self._spell_said: set[str] = set()
        self._ready_run: str | None = None
        self._ready_at: datetime | None = None
        self._read_at: datetime | None = None
        self._unconfirmed: tuple[str, datetime] | None = None
        self._unconfirmed_said: str | None = None
        self.said_ready: Callable[[str], bool] | None = None
        """Whether the world is still running run `run` and its log shows the ready marker.

        Set by the dashboard that feeds this keeper. The only evidence a clear is written on:
        the tab's own "ready" also counts ten minutes of uptime, which is no proof that a
        world listens (Codex adversarial review). None clears nothing."""

    def begin(self) -> int:
        """Called before the tick reads the container: the hold epoch the clear must match."""
        return _epoch(self.spec)

    def after_tick(self, status: str, run: str, ready: bool, begun: int) -> None:
        """Act on one tick's reading; never raises."""
        try:
            self._after_tick(status, run, ready, begun)
        except Exception as exc:  # noqa: BLE001 - the realm row is never a reason the tick fails
            logger.debug(f"{self.entry.id}: the realm row was not kept: {exc}")

    def _after_tick(self, status: str, run: str, ready: bool, begun: int) -> None:
        if status == "restarting" or (status == "running" and not ready):
            self._ready_run = None
            self._make_offline(status, run, begun)
        elif status == "running":
            self._spell_said.clear()
            self._unstick(run, begun)
        elif status:
            # Stopped or gone: Yu'lon's own Stop marked it, and the next start will.
            self._spell_said.clear()
            self._ready_run = None

    def _make_offline(self, status: str, run: str, begun: int) -> None:
        if self._marked_run == run:
            return
        now = self._now()
        if self._tried is not None and self._tried[0] == run and now - self._tried[1] < MARK_RETRY:
            return
        self._tried = (run, now)
        ok = self._mark(
            self.entry,
            self.spec,
            self.server_dir,
            wsl_distro=self.wsl_distro,
            start_database=False,
            quiet=True,
        )
        what = "restarting" if status == "restarting" else "loading"
        if ok and status == "running" and self.said_ready is not None and self.said_ready(run):
            # The world printed its ready marker and cleared the bit between this tick's read
            # and the mark (the marker comes first, `World.cpp:2399` before `Master.cpp:228`),
            # so the mark is the stale one: take it off again now (Codex review).
            self._clear_unless_held(begun)
            self._marked_run = run
            return
        if ok:
            self._marked_run = run
            if "marked" not in self._spell_said:
                self._spell_said.add("marked")
                logger.info(
                    f"{self.entry.id}: the world is {what}; its realm is listed Offline "
                    "until the world server says it is up"
                )
        elif "failed" not in self._spell_said:
            self._spell_said.add("failed")
            logger.info(
                f"{self.entry.id}: the world is {what}, and its realm could not be listed "
                "Offline (its database is down or did not answer); trying again"
            )

    def _unstick(self, run: str, begun: int) -> None:
        now = self._now()
        if self._ready_run != run or self._ready_at is None:
            self._ready_run, self._ready_at, self._read_at = run, now, None
            return
        if now - self._ready_at < CLEAR_AFTER:
            return
        if self._read_at is not None and now - self._read_at < FLAGS_READ_EVERY:
            return
        statement = flags_statement(self.entry)
        if statement is None:
            return
        self._read_at = now
        try:
            raw = self.sql.query("auth", statement)
            flags = int(raw.split()[0])
        except Exception as exc:  # noqa: BLE001 - an unread row is left as it is
            logger.debug(f"{self.entry.id}: the realm flags could not be read: {exc}")
            return
        if not flags & REALM_FLAG_OFFLINE:
            return
        if not self._confirmed(run):
            return
        if self._clear_unless_held(begun):
            logger.info(
                f"{self.entry.id}: the realm was still listed Offline over a world that has "
                "been up for a while; listed Online again"
            )

    def _confirmed(self, run: str) -> bool:
        """Whether run `run` said ready; a no is asked again only `UNCONFIRMED_RETRY` later.

        Each ask reads the run's log, so not every minute; but a load longer than the tab's
        ten-minute fallback says ready late, and is not written off for its whole run (Codex).
        """
        if self.said_ready is None:
            return False
        now = self._now()
        last = self._unconfirmed
        if last is not None and last[0] == run and now - last[1] < UNCONFIRMED_RETRY:
            return False
        if self.said_ready(run):
            self._unconfirmed = None
            return True
        self._unconfirmed = (run, now)
        if self._unconfirmed_said != run:
            self._unconfirmed_said = run
            logger.info(
                f"{self.entry.id}: the realm is listed Offline over a running world whose log "
                "does not show it ready yet; left as it is"
            )
        return False

    def _clear_unless_held(self, begun: int) -> bool:
        """Take the bit off, unless a deliberate hold is in force or moved since `begun`.

        Checked under the lock a Stop's hold takes, right before the write, so a Stop that
        begins now waits for this clear and marks after it.
        """
        with _HOLD_LOCK:
            if _holds.get(self.spec.db) or _epochs.get(self.spec.db, 0) != begun:
                return False
            return self._clear(
                self.entry, self.spec, self.server_dir, wsl_distro=self.wsl_distro, quiet=True
            )


def keeper_for(
    entry: CatalogEntry,
    spec: docker.ContainerSpec,
    server_dir: Path,
    sql: SqlReader,
    *,
    wsl_distro: str | None = None,
) -> Keeper | None:
    """A `Keeper` for an entry whose catalog names the offline flag column; None otherwise."""
    if entry.realmlist.offline_flag_column is None:
        return None
    return Keeper(entry, spec, server_dir, sql, wsl_distro=wsl_distro)
