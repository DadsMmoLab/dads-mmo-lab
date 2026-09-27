"""One Yu'lon per user: a second launch brings the first one's window forward (T152).

Two copies open at once raced on the same server's state, and nothing stopped a
second one: both set up the command channel with different generated passwords
and the later save could store the one the server had refused (T138), both
could take a tuning backup and revert over each other (T145), both write
install records and caches. The owner's decision (2026-09-27) is the ordinary
desktop answer: opening Yu'lon again brings the running window to the front.

Two Qt pieces, one job each:

- a `QLockFile` in `config_dir()` says WHO is first. It is per user because the
  config dir is, and it is held for the whole life of the process: taken before
  the window is built and given back only after the exit join, so a relaunch
  cannot start beside a copy that is still stopping its jobs.
- a `QLocalServer` the first copy listens on is HOW a later launch asks it to
  come forward. The name is derived from the lock's path, so it is as per-user
  as the lock, and a test's scratch config dir gets a name of its own.

Only `main()` claims it. `build_window()`, `--version`, `--provision`, the
install harness and every in-process test never do, so nothing that is not a
user opening the app can be refused by it.
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QDeadlineTimer, QDir, QLockFile, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from yulon.log import get_logger

logger = get_logger(__name__)

LOCK_NAME = "yulon.lock"
"""The lock file's name inside `config_dir()`."""

CLAIM_WAIT_MS = 15000
"""How long a launch keeps trying while the lock is held and nobody answers.

That is the first copy's exit: it stops answering as soon as its window has
closed and gives the lock back only after it has joined its jobs, which is
seconds (`main.PANEL_JOIN_MS`, `UPDATE_JOIN_MS`, `EXIT_JOIN_MS`). A player who
closes Yu'lon and opens it again at once should get a window, not a refusal, so
this outlasts an ordinary exit. It is also how long a first copy that holds the
lock but never listened is waited on before the box says so.
"""

CONNECT_MS = 1000
"""How long one attempt to reach the first copy's socket may take."""

REPLY_MS = 5000
"""How long a connected first copy has to answer before it is called stuck.

A healthy one answers from its event loop in milliseconds. One that accepted
the connection (the OS does that, not the app) and then said nothing has a
window that is not processing events, and waiting on it longer only leaves the
player looking at nothing.
"""

RETRY_MS = 250
"""The pause between tries while the lock is held and nobody is listening."""

MAX_LINE = 4096
"""The longest message the first copy reads. A raise is one short line."""

ASFW_ANY = 0xFFFFFFFF
"""`AllowSetForegroundWindow`'s "any process": the first copy's pid is not known here."""

UNANSWERED_TITLE = "Yu'lon is already open"
UNANSWERED_TEXT = (
    "Yu'lon is already open, but it did not answer when asked to come to the front. "
    "It may still be closing, or it may be stuck.\n\n"
    "Wait a moment and open Yu'lon again. If this keeps happening, close the other "
    "Yu'lon (from Task Manager on Windows, or your system monitor), then open it again."
)

Claim = Literal["first", "raised", "unanswered", "unguarded"]
"""What `InstanceGuard.claim()` found.

- `first`: this process holds the lock and is listening; carry on.
- `raised`: another copy holds it and has come forward; leave, exit 0.
- `unanswered`: another copy holds it and would not answer; say so and leave.
- `unguarded`: no lock could be written at all (an unwritable config dir). That
  is not somebody else's lock, so the app runs, without the guard, as it did
  before T152.
"""

_active: InstanceGuard | None = None
"""The guard this process holds, for `handed_over()`: the one caller without a handle."""


SOCKET_PATH_MAX = 100
"""The longest socket path used as given; `sun_path` holds 108 bytes on Linux, 104 on macOS."""


def server_name_for(lock_path: Path) -> str:
    """The local socket name that goes with a lock file: per user because the path is.

    A hash rather than the path itself, because on Windows the name is a pipe
    name and on Linux and macOS it becomes a socket file in the temp dir, and a
    path is neither.

    **A long temp dir gets `/tmp`.** Qt puts a plain name's socket file under
    `QDir.tempPath()`, and a socket path past `sun_path` makes `listen()` fail
    with "Name error" (measured, PySide6 6.11.2): the first copy would hold the
    lock with nobody listening, and every later launch would wait out the claim
    and call it stuck. Every launch computes the same answer from the same
    environment, so both halves still meet.
    """
    digest = hashlib.sha256(str(lock_path.absolute()).encode("utf-8")).hexdigest()[:16]
    name = f"yulon-{digest}"
    if sys.platform != "win32":
        if len(os.fsencode(os.path.join(QDir.tempPath(), name))) > SOCKET_PATH_MAX:
            return f"/tmp/{name}"
    return name


class InstanceGuard(QObject):
    """The lock and the socket for one config dir. See the module docstring."""

    raise_requested = Signal(str)
    """A later launch asked this copy to come forward; the argument is its activation token."""

    def __init__(self, directory: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._directory = directory
        self._lock_path = directory / LOCK_NAME
        self._lock = QLockFile(str(self._lock_path))
        # **0, not Qt's default of 30 s.** A lock older than the stale time is
        # called stale even when its holder is alive, and only the holder's OS
        # lock then keeps it from being taken: `flock` on Linux, which a network
        # home can lack. A crashed copy is recognised by its pid instead - Qt
        # checks that the pid is running and is still the same program - which
        # is measured in `test_a_lock_left_by_a_killed_yulon_does_not_stop_the_next_launch`.
        self._lock.setStaleLockTime(0)
        self.server_name = server_name_for(self._lock_path)
        self._server: QLocalServer | None = None

    # ------------------------------------------------------------------ claim

    def claim(self, *, wait_ms: int = CLAIM_WAIT_MS) -> Claim:
        """Become the one Yu'lon, or ask the one there is to come forward."""
        deadline = time.monotonic() + wait_ms / 1000
        while True:
            try:
                self._directory.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logger.warning(f"single instance: no lock at {self._lock_path} ({exc}); unguarded")
                return "unguarded"
            if self._take():
                return "first"
            error = self._lock.error()
            if error is not QLockFile.LockError.LockFailedError:
                logger.warning(
                    f"single instance: could not write {self._lock_path} ({error.name}); unguarded"
                )
                return "unguarded"
            answer = self._ask_the_first()
            if answer == "raised":
                logger.info("single instance: Yu'lon is already open; asked it to come forward")
                return "raised"
            if answer == "silent":
                logger.warning("single instance: the open Yu'lon accepted but did not answer")
                return "unanswered"
            if time.monotonic() >= deadline:
                logger.warning(
                    "single instance: the lock is held and nobody answered within "
                    f"{wait_ms} ms; giving up"
                )
                return "unanswered"
            time.sleep(RETRY_MS / 1000)

    def _take(self) -> bool:
        """Take the lock if it is free or stale, and start listening. False if it is held."""
        global _active
        if not self._lock.tryLock(0):
            return False
        # What a crashed copy left behind on Linux and macOS: a socket FILE with
        # nobody listening, which makes a plain `listen()` fail with AddressInUse
        # (measured). With `UserAccessOption` Qt 6.11 happens to create the socket
        # elsewhere and rename it over the old one, so this is belt and braces -
        # but a documented one, and safe only because the lock is ours now: no
        # live copy owns that name. On Windows the pipe went with its process.
        QLocalServer.removeServer(self.server_name)
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if server.listen(self.server_name):
            server.newConnection.connect(self._take_connections)
            self._server = server
        else:
            # Still the one copy: the lock is what keeps a second one out. A
            # later launch just cannot bring this one forward, and says so.
            logger.warning(
                f"single instance: holding the lock, but cannot listen on {self.server_name}: "
                f"{server.errorString()}"
            )
            server.deleteLater()
        _active = self
        return True

    def _ask_the_first(self) -> Literal["raised", "absent", "silent"]:
        """Ask whoever holds the lock to come forward.

        `absent`: nobody is listening, or the listener went away mid-question -
        a copy that is starting or closing, so the caller tries again. `silent`:
        connected and never answered, which is a stuck window.
        """
        socket = QLocalSocket()
        socket.connectToServer(self.server_name)
        if not socket.waitForConnected(CONNECT_MS):
            return "absent"
        _let_the_first_take_the_foreground()
        # What a Wayland desktop hands the process IT launched. This one will
        # not show a window, so the token is only worth anything to the first.
        token = "".join(os.environ.get("XDG_ACTIVATION_TOKEN", "").split())[: MAX_LINE // 2]
        socket.write(f"raise {token}\n".encode())
        socket.flush()
        socket.waitForBytesWritten(REPLY_MS)
        deadline = QDeadlineTimer(REPLY_MS)
        connected = QLocalSocket.LocalSocketState.ConnectedState
        while not socket.canReadLine():
            # The reply is read before the state is looked at: the first copy
            # hangs up straight after answering, and its line is still buffered.
            if socket.state() is not connected:
                return "absent"
            if deadline.hasExpired() or not socket.waitForReadyRead(deadline.remainingTime()):
                if socket.canReadLine():
                    break
                if socket.state() is not connected:
                    return "absent"
                socket.abort()
                return "silent"
        reply = bytes(socket.readLine(MAX_LINE).data()).strip()
        socket.disconnectFromServer()
        return "raised" if reply == b"ok" else "silent"

    # ----------------------------------------------------------------- answer

    def _take_connections(self) -> None:
        server = self._server
        while server is not None and server.hasPendingConnections():
            socket = server.nextPendingConnection()
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(socket.deleteLater)
            # The line can arrive with the connection, before `readyRead` is connected.
            self._read(socket)

    def _read(self, socket: QLocalSocket) -> None:
        if not socket.canReadLine():
            if socket.bytesAvailable() > MAX_LINE:
                socket.abort()
            return
        line = bytes(socket.readLine(MAX_LINE).data()).decode("utf-8", "replace").strip()
        verb, _, token = line.partition(" ")
        if verb != "raise":
            logger.info(f"single instance: dropped a message that was not a raise ({verb[:20]!r})")
            socket.abort()
            return
        logger.info("single instance: another launch asked this Yu'lon to come forward")
        self.raise_requested.emit(token)
        socket.write(b"ok\n")
        socket.flush()
        socket.disconnectFromServer()

    # ---------------------------------------------------------------- release

    def stop_answering(self) -> None:
        """Stop listening, and keep the lock: the window has closed, the jobs have not.

        A launch that arrives now finds nobody to ask and keeps trying for the
        lock, which it gets once this copy has finished leaving.
        """
        server, self._server = self._server, None
        if server is not None:
            server.close()
            server.deleteLater()

    def release(self) -> None:
        """Give up the socket and the lock. Safe to call twice, and on a guard that never won."""
        global _active
        self.stop_answering()
        if self._lock.isLocked():
            self._lock.unlock()
        if _active is self:
            _active = None


@contextmanager
def handed_over() -> Iterator[None]:
    """Give the lock up around an `os.execv` that replaces this process with a new Yu'lon.

    The docker-group restart (`CatalogView._offer_a_restart_instead()`) keeps
    the PID and changes the program, so the lock file would name a process that
    is running - the new program itself - and the new program would find its
    own lock, ask a socket that went with the old one, and refuse to open.

    The far side of the `with` is only ever reached when the exec did NOT
    happen, and then this copy carries on and takes its lock back.
    """
    guard = _active
    if guard is None:
        yield
        return
    guard.release()
    try:
        yield
    finally:
        if not guard._take():
            logger.warning("single instance: the restart did not happen and the lock was taken")


def _let_the_first_take_the_foreground() -> None:
    """Windows gives the foreground to the process the user just started - this one.

    The window that should come forward belongs to the first copy, and Windows
    refuses `SetForegroundWindow` from a process that has not been given the
    right, flashing its taskbar button instead. This process has it and can
    pass it on.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            allow = ctypes.windll.user32.AllowSetForegroundWindow
            allow.argtypes = [wintypes.DWORD]
            allow(ASFW_ANY)
        except Exception as exc:  # noqa: BLE001 - the flash is the fallback, not a failure
            logger.info(f"single instance: AllowSetForegroundWindow failed: {exc}")
