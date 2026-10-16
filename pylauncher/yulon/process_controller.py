"""The lifecycle of one `process` single server: status, start, stop and its log.

The `single_controller.SingleServer` of the catalog's process runtime
(`catalog.SingleProcess`, DFO). The container controller asks Docker about a
container the compose project owns; a server of this runtime is a program on the
host, so this controller owns the one it starts and nothing else:

* **Start** writes the files the server reads (`process.files`, rendered with the
  player's client folder and the account), clears a lock a killed server left
  behind once its holder is gone, and runs `serve.argv` detached so it outlives
  Yu'lon, with its output in `serve.log` (emptied first, so the log is this run's).
  The pid and the program are recorded in `.yulon-process.json`.
* **Status** is that record checked against the kernel (the pid is alive AND is
  still that program, so a reused pid is not mistaken for the server) and the ready
  lines in this run's log.
* **Stop** ends that process tree (`taskkill /T /F`) and no other. Upstream's own
  `dfolauncher stop` kills every process named `probe.exe` or `wireprobe*.exe` on
  the machine, which is not a thing to do to a player's computer from a button.

The server takes players on this computer only (`entry.advertise` is None), so
there is no address to advertise.

**On Linux (a Steam Deck), the server runs in a private network.** Upstream's Windows
launcher adds a WFP rule so the client may talk to loopback only; the Linux equivalent is
a network namespace holding nothing but `lo`, made with unprivileged user namespaces, so
no root is needed. Start makes it (a `sleep infinity` holder keeps it alive) and runs the
server inside it; the game joins it through the client script (`gaming_mode.py`). The
records are the Deck's own layout, `run/holder.pid`, `run/server.pid` and
`run/server.logpath`, so a server the Gaming Mode launcher started and one this tab
started are the same thing to both. Stop, in the order measured on Deck #2 (2026-10-16):
the game's Wine once the game is gone, SIGINT to the server's process group, wait for the
server AND its gateway (which holds the save database's lock), the lock rule, then the
holder. Every child gets `runner.child_env()` and a working folder outside an AppImage's
mount: the Deck found that the bundle's `LD_LIBRARY_PATH` kills SteamOS's own `/bin/sh`.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yulon import platform, resources, runner, single_play
from yulon.catalog import composegen
from yulon.catalog.catalog import SingleEntry, SingleProcess
from yulon.catalog.single_install import missing_ready_lines
from yulon.log import get_logger
from yulon.play_launch import CREATE_NEW_PROCESS_GROUP, DETACHED_PROCESS
from yulon.selfupdate.layout import pid_is_this_app, pid_liveness
from yulon.single_controller import RUNNING, STARTING, STOPPED, UNKNOWN, SingleServerError
from yulon.ui import lines

logger = get_logger(__name__)

RUN_RECORD = ".yulon-process.json"
"""The running server's record in its folder: pid, program, account, when it started."""

STOP_DEADLINE_SECONDS = 20.0
"""How long Stop waits for the ended tree to be gone before it says it could not stop it."""

FOLLOW_POLL_SECONDS = 0.5
"""How often the console reads the log for new lines."""

LOOPBACK = "127.0.0.1"


@dataclass(frozen=True)
class RunRecord:
    """What Start recorded about the program it ran."""

    pid: int
    program: str
    account: str
    started: str


_spawn: Callable[..., Any] = subprocess.Popen
"""The real spawn behind `spawn_detached()`, looked up per call (as `play_launch._spawn`
is), so the suite can refuse it and no test starts a real server."""


def spawn_detached(argv: list[str], *, cwd: Path, env: dict[str, str], log: Path) -> int:
    """Start `argv` so it OUTLIVES Yu'lon, its stdout and stderr into `log`; its pid.

    `play_launch.launch()`'s flags on Windows (no console of ours, not in our Ctrl-C
    group) and a new session elsewhere. The log is opened here and handed over, so
    the child keeps writing to it after Yu'lon has gone.
    """
    detached: dict[str, Any]
    if os.name == "nt":
        detached = {"creationflags": DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP}
    else:
        detached = {"start_new_session": True}
    with log.open("wb") as out:
        child = _spawn(
            argv,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            **detached,
        )
    return int(child.pid)


def end_tree(pid: int) -> None:
    """End `pid` and every process it started (`taskkill /T /F`; a process group elsewhere)."""
    if os.name == "nt":
        system32 = os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "System32")
        taskkill = os.path.join(system32, "taskkill.exe")
        runner.run([taskkill, "/T", "/F", "/PID", str(pid)], timeout=30)
        return
    # `getattr`: the name exists only on POSIX, and naming it directly is an error under
    # `mypy --platform win32`, which CI runs (the convention `selfupdate/layout.py` uses).
    killpg = getattr(os, "killpg")  # noqa: B009 - POSIX-only attribute
    try:
        killpg(pid, 9)
    except OSError:
        pass


def port_open(port: int) -> bool:
    """Whether anything on this computer accepts a connection on 127.0.0.1:`port`."""
    try:
        with socket.create_connection((LOOPBACK, port), timeout=0.5):
            return True
    except OSError:
        return False


# -- Linux: the private network -------------------------------------------------------------

RUN_DIR = "run"
"""The private network's records, in the layout of the Deck's own DFO scripts."""
HOLDER_PID = "holder.pid"
SERVER_PID = "server.pid"
SERVER_LOGPATH = "server.logpath"
HOLDER_LOG = "holder.log"
HOLDER_PROGRAM = "sleep"

HOLDER_SCRIPT = 'ip link set lo up && echo $$ > "$1" && exec sleep infinity'
"""The holder: the outer namespace maps us to root, which is what lets `ip` bring `lo` up."""
SERVER_WRAP = 'echo $$ > "$1"; shift; exec "$@"'
"""The server's last hop: its pid on record, then the server itself in the same process."""

GAME_PROGRAMS = ("DFO.exe", "CrBrowserMain")
"""The game's names under Proton: its embedded Chromium renames it CrBrowserMain in seconds."""

HOLDER_WAIT_SECONDS = 10.0
STOP_GRACE_SECONDS = 15.0
"""How long the server and its gateway get after SIGINT before they are sent SIGTERM."""

COMM_LENGTH = 15
"""The kernel cuts a process's name to 15 characters: `wireprobe-pvf.exe` reads
`wireprobe-pvf.e`."""


def process_alive(pid: int) -> bool | None:
    """`pid_liveness()`, with a Linux zombie counted as gone and our own exited child reaped.

    On Linux the server is Yu'lon's own child: once it exits it stays a zombie until it is
    reaped, and `kill(pid, 0)` answers a zombie as alive, so Stop waited out its whole
    deadline for a server already gone (measured in the Linux end-to-end, 2026-10-16).
    Windows has no zombies, and is `pid_liveness()` unchanged.
    """
    if os.name == "posix" and pid > 0:
        try:
            getattr(os, "waitpid")(pid, getattr(os, "WNOHANG"))  # noqa: B009 - POSIX only
        except OSError:
            pass  # not our child (ECHILD): someone else reaps it
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
            if stat.rsplit(")", 1)[1].split()[0] in ("Z", "X"):
                return False
        except (OSError, IndexError):
            pass
    return pid_liveness(pid)


def read_comm(pid: int) -> str | None:
    """A process's name as the kernel keeps it, or None when it cannot be read."""
    try:
        return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def read_netns(pid: int | str) -> str | None:
    """Which network namespace a process is in (`self` for this one), or None."""
    try:
        return os.readlink(f"/proc/{pid}/ns/net")
    except OSError:
        return None


def all_pids() -> list[int]:
    """Every process on this computer the kernel shows; empty where there is no `/proc`."""
    try:
        return [int(entry.name) for entry in Path("/proc").iterdir() if entry.name.isdigit()]
    except OSError:
        return []


def signal_group(pgid: int, sig: int) -> None:
    """Send `sig` to a process group (POSIX only); a group already gone is not an error."""
    killpg = getattr(os, "killpg")  # noqa: B009 - POSIX-only attribute
    try:
        killpg(pgid, sig)
    except OSError:
        pass


def signal_pid(pid: int, sig: int) -> None:
    """Send `sig` to one process; one already gone is not an error."""
    try:
        os.kill(pid, sig)
    except OSError:
        pass


def process_group(pid: int) -> int | None:
    """A process's group (POSIX only), or None when it is gone or cannot be asked."""
    getpgid = getattr(os, "getpgid", None)
    if getpgid is None:
        return None
    try:
        return int(getpgid(pid))
    except OSError:
        return None


def user_ids() -> tuple[int, int]:
    """This user's uid and gid (POSIX only), for the nested namespace that maps back to them."""
    return getattr(os, "getuid")(), getattr(os, "getgid")()  # noqa: B009 - POSIX-only


@dataclass(frozen=True)
class ProcessControlSeams:
    """Everything the controller reaches outside itself through. Real by default."""

    spawn: Callable[..., int] = spawn_detached
    end_tree: Callable[[int], None] = end_tree
    alive: Callable[[int], bool | None] = process_alive
    is_program: Callable[[int, Path], bool | None] = pid_is_this_app
    port_open: Callable[[int], bool] = port_open
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    platform_id: Callable[[], str] = platform.detect
    comm: Callable[[int], str | None] = read_comm
    netns: Callable[[int | str], str | None] = read_netns
    pids: Callable[[], list[int]] = all_pids
    signal_group: Callable[[int, int], None] = signal_group
    signal: Callable[[int, int], None] = signal_pid
    ids: Callable[[], tuple[int, int]] = user_ids
    pgid: Callable[[int], int | None] = process_group


def posix(path: Path) -> str:
    """An absolute path spelled with `/`, the form upstream's JSON files ask for."""
    return path.resolve().as_posix()


class _Tail:
    """One file read as it grows: whole lines as they land, the unfinished one at the end."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = 0
        self.pending = b""

    def lines(self) -> list[str]:
        """The lines completed since the last call (a file not there yet has none)."""
        try:
            with self.path.open("rb") as fh:
                fh.seek(self.offset)
                chunk = fh.read()
        except OSError:
            return []
        self.offset += len(chunk)
        *complete, self.pending = (self.pending + chunk).split(b"\n")
        return [raw.decode("utf-8", errors="replace").rstrip("\r") for raw in complete]

    def rest(self) -> list[str]:
        """The last line, when the file ended without a newline."""
        text, self.pending = self.pending.decode("utf-8", errors="replace"), b""
        return [text] if text else []


class ProcessController:
    """Start, stop, read the status of, and follow the log of one `process` install."""

    follows_with_cancel = True
    """The console reads a file, so it ends on the panel's `cancel` (`SingleServer`)."""

    def __init__(
        self,
        entry: SingleEntry,
        server_dir: Path,
        *,
        installers_root: Path | None = None,
        seams: ProcessControlSeams | None = None,
    ) -> None:
        process = entry.install.process
        if process is None:
            raise ValueError(f"{entry.id} is not run as host programs")
        self.entry = entry
        self.server_dir = server_dir
        self.process: SingleProcess = process
        self.installers_root = (
            installers_root if installers_root is not None else resources.installers_dir()
        )
        self._seams = seams if seams is not None else ProcessControlSeams()

    # -- paths and tokens -----------------------------------------------------------

    @property
    def log_path(self) -> Path:
        """This run's output: the server program's stdout and stderr."""
        return self.server_dir / self.process.serve.log

    def tokens(self, *, account: str, client_dir: Path) -> dict[str, str]:
        """Every `{{TOKEN}}` the serve argv, its env and the file templates may spend."""
        return {
            "SERVER_DIR": posix(self.server_dir),
            "CHECKOUT": posix(self.server_dir / self.process.checkout),
            "MODULE": posix(self.server_dir / self.process.module),
            "CLIENT_DIR": posix(client_dir),
            "ACCOUNT": account,
            **self.entry.port_tokens(),
        }

    # -- the record ------------------------------------------------------------------

    def _record(self) -> RunRecord | None:
        try:
            raw = json.loads((self.server_dir / RUN_RECORD).read_text(encoding="utf-8"))
            return RunRecord(
                pid=int(raw["pid"]),
                program=str(raw["program"]),
                account=str(raw["account"]),
                started=str(raw["started"]),
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _write_record(self, record: RunRecord) -> None:
        path = self.server_dir / RUN_RECORD
        temp = path.with_name(path.name + ".yulon-new")
        temp.write_text(json.dumps(record.__dict__, indent=2) + "\n", encoding="utf-8")
        temp.replace(path)

    def _forget_record(self) -> None:
        (self.server_dir / RUN_RECORD).unlink(missing_ok=True)

    def _running(self, record: RunRecord) -> bool | None:
        """Whether the recorded program still runs as that program; None when it cannot tell."""
        alive = self._seams.alive(record.pid)
        if alive is not True:
            return alive
        if self._private():
            # In its nested user namespace the server's /proc/<pid>/exe may not be ours to
            # read; its name, cut as the kernel cuts it, is what the Deck's scripts check.
            name = self._seams.comm(record.pid)
            return None if name is None else name == Path(record.program).name[:COMM_LENGTH]
        return self._seams.is_program(record.pid, Path(record.program))

    # -- Linux: the private network ------------------------------------------------------

    def _private(self) -> bool:
        """Whether this server runs in its own private network (Linux)."""
        return self._seams.platform_id() == "linux"

    @property
    def run_dir(self) -> Path:
        return self.server_dir / RUN_DIR

    def _pid_in(self, name: str) -> int | None:
        try:
            digits = "".join(
                c for c in (self.run_dir / name).read_text(encoding="utf-8") if c.isdigit()
            )
        except OSError:
            return None
        return int(digits) if digits else None

    def holder_pid(self) -> int | None:
        """The private network's holder: alive, still `sleep`, and in another network."""
        pid = self._pid_in(HOLDER_PID)
        if pid is None or self._seams.comm(pid) != HOLDER_PROGRAM:
            return None
        theirs = self._seams.netns(pid)
        if theirs is None or theirs == self._seams.netns("self"):
            return None
        return pid

    def _network_members(self) -> list[int]:
        """Every process in the private network except its holder."""
        holder = self.holder_pid()
        if holder is None:
            return []
        network = self._seams.netns(holder)
        return [
            pid for pid in self._seams.pids() if pid != holder and self._seams.netns(pid) == network
        ]

    def _game_running(self) -> bool:
        """The game in the private network, under either of its names."""
        play = self.entry.play
        names = {
            GAME_PROGRAMS[1],
            (play.exe if play is not None else GAME_PROGRAMS[0])[:COMM_LENGTH],
        }
        return any(self._seams.comm(pid) in names for pid in self._network_members())

    def _start_private(self, argv: list[str], env: dict[str, str]) -> int:
        """Make the private network, then start the server inside it; the server's pid.

        nsenter and unshare exec in place, and the last `sh` execs the server, so the pid
        spawned is the server's own, the leader of its own process group.
        """
        run = self.run_dir
        run.mkdir(parents=True, exist_ok=True)
        if self._network_members():
            # A server is up in the network with no record of ours (its launcher could not
            # write one; `start()` swept what a recorded, crashed one left): ending the
            # holder would leave it running where nothing sees it.
            raise SingleServerError(
                f"A {self.entry.name} server is already running here, started outside "
                "Yu'lon. Stop it from its own window (or the Server Stop tile), then Start."
            )
        self._end_network()  # a holder a server that is gone left behind
        (run / SERVER_LOGPATH).write_text(f"{self.log_path}\n", encoding="utf-8")
        self._seams.spawn(
            ["unshare", "-rn", "sh", "-c", HOLDER_SCRIPT, "sh", str(run / HOLDER_PID)],
            cwd=Path("/"),
            env=env,
            log=run / HOLDER_LOG,
        )
        deadline = self._seams.clock() + HOLDER_WAIT_SECONDS
        while (holder := self.holder_pid()) is None:
            if self._seams.clock() >= deadline:
                raise SingleServerError(
                    f"{self.entry.name}'s private network could not be made, so the server was "
                    "not started. Unprivileged user namespaces must be allowed (they are on "
                    f"SteamOS). What it said is in {run / HOLDER_LOG}."
                )
            self._seams.sleep(0.2)
        uid, gid = self._seams.ids()
        wrapped = [
            "nsenter",
            "-t",
            str(holder),
            "-U",
            "-n",
            "--preserve-credentials",
            "--",
            "unshare",
            "--user",
            f"--map-user={uid}",
            f"--map-group={gid}",
            "--",
            "sh",
            "-c",
            SERVER_WRAP,
            "sh",
            str(run / SERVER_PID),
            *argv,
        ]
        pid = self._seams.spawn(
            wrapped, cwd=self.server_dir / self.process.checkout, env=env, log=self.log_path
        )
        # The server is in the network once its own `sh` has put its pid on record; an
        # `nsenter` that was refused never gets there, and Start says so, not "started".
        deadline = self._seams.clock() + HOLDER_WAIT_SECONDS
        while self._pid_in(SERVER_PID) != pid:
            if self._seams.alive(pid) is False or self._seams.clock() >= deadline:
                self._seams.signal_group(pid, signal.SIGTERM)
                self._end_network()
                raise SingleServerError(
                    f"{self.entry.name} could not be started inside its private network. "
                    f"What it said is in {self.log_path}."
                )
            self._seams.sleep(0.1)
        return pid

    def _stop_private(self, record: RunRecord) -> None:
        """SIGINT the server's group, wait for it and its gateway, then SIGTERM what is left.

        The gateway holds the save database's lock (measured on Deck #2), so Stop waits
        for the whole group before the lock rule runs. The server has no shutdown handler
        of its own (upstream ends it with `taskkill /F`); its saves are committed as the
        game plays, and the database's WAL keeps every committed write.
        """
        if self._game_running():
            raise SingleServerError(
                f"{self.entry.name} is still being played. Quit the game first, then Stop the "
                "server."
            )
        group = record.pid
        self._seams.signal_group(group, signal.SIGINT)
        deadline = self._seams.clock() + STOP_GRACE_SECONDS
        while self._seams.alive(group) is not False or self._in_group(group):
            if self._seams.clock() >= deadline:
                self._seams.signal_group(group, signal.SIGTERM)
                break
            self._seams.sleep(0.25)
        # Whatever is left in the network is the server's (the gateway need not share its
        # group) or the game's Wine helpers, which outlive the game: the game itself is
        # gone, checked above. Nothing outside the network is touched.
        for pid in self._network_members():
            self._seams.signal(pid, signal.SIGTERM)
        deadline = self._seams.clock() + STOP_DEADLINE_SECONDS
        while self._seams.alive(group) is not False or self._network_members():
            if self._seams.clock() >= deadline:
                raise SingleServerError(
                    f"{self.entry.name} (process {group}) was told to stop and is still "
                    "running. It may be finishing a save; try Stop again in a moment."
                )
            self._seams.sleep(0.25)

    def _in_group(self, group: int) -> bool:
        """Whether anything of the server's process group is still in the private network."""
        return any(self._seams.pgid(pid) == group for pid in self._network_members())

    def _sweep_network(self) -> None:
        """SIGTERM what is left in the private network, and wait for it to go.

        Only for a network whose server is gone and whose game is not running, so all
        that can be left is the server's own (its gateway) or the game's Wine helpers.
        """
        for pid in self._network_members():
            self._seams.signal(pid, signal.SIGTERM)
        deadline = self._seams.clock() + STOP_DEADLINE_SECONDS
        while self._network_members():
            if self._seams.clock() >= deadline:
                raise SingleServerError(
                    f"What is left of {self.entry.name}'s last run was told to stop and is "
                    "still running. Try again in a moment."
                )
            self._seams.sleep(0.25)

    def _end_network(self) -> None:
        """End the private network's holder, and forget the run records.

        The holder Start made is Yu'lon's own child, so the wait below is also what reaps
        it (`process_alive()`), rather than leaving a zombie `sleep` behind.
        """
        holder = self.holder_pid()
        if holder is not None:
            self._seams.signal(holder, signal.SIGTERM)
            deadline = self._seams.clock() + 2.0
            while self._seams.alive(holder) is not False and self._seams.clock() < deadline:
                self._seams.sleep(0.05)
        for name in (HOLDER_PID, SERVER_PID):
            (self.run_dir / name).unlink(missing_ok=True)

    # -- status ------------------------------------------------------------------------

    def status(self) -> str:
        """`running` once every ready line is in this run's log; `starting` before it.

        `stopped` when no program of ours is running; `unknown` when the kernel would
        not say, which is never read as stopped.
        """
        record = self._record()
        if record is None:
            return STOPPED
        running = self._running(record)
        if running is None:
            return UNKNOWN
        if not running:
            return STOPPED
        if not missing_ready_lines(self.entry, self._log_text()):
            return RUNNING
        return STARTING

    def session_account(self) -> str | None:
        """The account the running server was started for, or None when none runs."""
        record = self._record()
        if record is None or self._running(record) is not True:
            return None
        return record.account

    def _log_text(self) -> str:
        try:
            return self.log_path.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            return ""

    # -- Start and Stop ------------------------------------------------------------------

    def start(self, *, account: str = "", client_dir: Path | None = None) -> None:
        """Start the server for `account`, reading the client in `client_dir`.

        Refuses, with nothing started, without a usable account name or a client
        folder holding the client's program, while something on this computer already
        answers on one of the server's ports, and while a live process holds the
        save database's lock.
        """
        status = self.status()
        if status in (RUNNING, STARTING):
            return
        problem = single_play.check_account(account)
        if problem is not None:
            raise SingleServerError(problem)
        play = self.entry.play
        if client_dir is None:
            what = play.client if play is not None else "your client folder"
            raise SingleServerError(
                f"Choose {what} first: the server reads its game data from your own client."
            )
        if play is not None and not (client_dir / play.exe).is_file():
            raise SingleServerError(
                f"There is no {play.exe} in {client_dir}, so the server was not started. "
                f"Choose {play.client}."
            )
        # On Linux the server's ports are inside its own network, so the host's are no clash.
        held = (
            []
            if self._private()
            else [port for port in self.entry.host_ports() if self._seams.port_open(port)]
        )
        if held:
            raise SingleServerError(
                f"Something on this computer already answers on port "
                f"{', '.join(str(p) for p in held)}, which {self.entry.name} needs. If it is "
                f"another {self.entry.name} server, stop that one first."
            )
        if self._private() and self._record() is not None and self._network_members():
            # Our record, its server gone (status said so): what is left in the network is
            # that server's (a gateway that outlived a crash), and it holds the save lock.
            self._sweep_network()
        self._clear_stale_lease()
        tokens = self.tokens(account=account, client_dir=client_dir)
        self._write_files(tokens)
        argv = [composegen.fill(arg, tokens) for arg in self.process.serve.argv]
        # `runner.child_env()`: a frozen build's own LD_LIBRARY_PATH must not reach the server,
        # nor `sh`, `unshare` and `nsenter` on the way (it kills SteamOS's /bin/sh).
        env = runner.child_env(dict(os.environ)) or dict(os.environ)
        env.update({k: composegen.fill(v, tokens) for k, v in self.process.serve.env.items()})
        try:
            if self._private():
                pid = self._start_private(argv, env)
            else:
                pid = self._seams.spawn(
                    argv,
                    cwd=self.server_dir / self.process.checkout,
                    env=env,
                    log=self.log_path,
                )
        except OSError as exc:
            raise SingleServerError(
                f"{self.entry.name} could not be started: {exc.strerror or exc}"
            ) from exc
        try:
            self._write_record(
                RunRecord(
                    pid=pid,
                    program=str(Path(argv[0])),
                    account=account,
                    started=datetime.now(UTC).isoformat(timespec="seconds"),
                )
            )
        except OSError as exc:
            # Without its record the server could be neither seen nor stopped from
            # here, so the program just started is ended rather than left behind.
            self._seams.end_tree(pid)
            raise SingleServerError(
                f"{self.entry.name} was not started: {RUN_RECORD} could not be written in "
                f"{self.server_dir} ({exc.strerror or exc})."
            ) from exc
        logger.info(f"{self.entry.id} started from {self.server_dir} as pid {pid} for {account}")

    def stop(self) -> bool:
        """Stop the server this folder started; False when there was nothing to stop."""
        record = self._record()
        if record is None:
            return False
        running = self._running(record)
        if running is None:
            raise SingleServerError(
                f"This computer would not say whether {self.entry.name} (process {record.pid}) is "
                "still running, so it was left alone. Try again in a moment."
            )
        if not running:
            if self._private():
                # The server is gone; a gateway that outlived it is not, and holds the lock.
                if self._game_running():
                    raise SingleServerError(
                        f"{self.entry.name} is still being played. Quit the game first, then "
                        "Stop the server."
                    )
                self._sweep_network()
                self._forget_record()
                with contextlib.suppress(SingleServerError):
                    self._clear_stale_lease()
                self._end_network()
                return False
            self._forget_record()
            return False
        if self._private():
            self._stop_private(record)
        else:
            self._seams.end_tree(record.pid)
            deadline = self._seams.clock() + STOP_DEADLINE_SECONDS
            while self._seams.alive(record.pid) is not False:
                if self._seams.clock() >= deadline:
                    raise SingleServerError(
                        f"{self.entry.name} (process {record.pid}) was told to stop and is still "
                        "running. It may be finishing a save; try Stop again in a moment."
                    )
                self._seams.sleep(0.25)
        self._forget_record()
        try:
            self._clear_stale_lease()
        except SingleServerError as exc:
            # The server is stopped; a lock some other process holds is the next Start's
            # to refuse, by name, not a reason to call this Stop a failure.
            logger.info(f"{self.entry.id}: lease left in place after stop: {exc}")
        if self._private():
            self._end_network()
        logger.info(f"{self.entry.id} stopped in {self.server_dir}")
        return True

    # -- what Start writes and clears ------------------------------------------------------

    def _write_files(self, tokens: dict[str, str]) -> None:
        """Render every `process.files` template and write the ones whose text changed.

        A template's first line is Yu'lon's banner, as every shipped template's is
        (`test_every_shipped_templates_first_line_is_ours`); these are JSON files, which
        have no comment to carry it in, so it is dropped from what is written. They are
        this app's to rewrite whole: upstream keeps both out of its repository as local
        settings, and the values in them are the ones this tab chose.
        """
        for spec in self.process.files:
            template = self.installers_root / self.process.templates / spec.template
            target = self.server_dir / spec.dest
            try:
                body = template.read_text(encoding="utf-8")
                if composegen.is_marker_line(body):
                    body = body.split("\n", 1)[1] if "\n" in body else ""
                text = composegen.fill(body, tokens)
                if target.is_file() and target.read_text(encoding="utf-8") == text:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                temp = target.with_name(target.name + ".yulon-new")
                temp.write_text(text, encoding="utf-8", newline="\n")
                temp.replace(target)
            except (OSError, composegen.ComposeGenError) as exc:
                raise SingleServerError(f"{spec.dest} could not be written: {exc}") from exc

    def _clear_stale_lease(self) -> None:
        """Remove the lock a killed server left, once the pid in it is gone; never before.

        Upstream's rule (`Clear-AdminGuard`): a lease whose holder still runs may be a
        GM tool writing the save, so it is refused with the holder named; one holding
        no pid is left for its own 60 s expiry.
        """
        if self.process.lease is None:
            return
        lease = self.server_dir / self.process.lease
        try:
            holder = lease.read_text(encoding="utf-8", errors="replace").strip()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise SingleServerError(f"{lease} could not be read: {exc}") from exc
        if not holder.isdigit():
            return
        if self._seams.alive(int(holder)) is not False:
            raise SingleServerError(
                f"Process {holder} holds the save database's lock ({lease.name}), so "
                f"{self.entry.name} was not started. Close whatever is writing to the saves, "
                "then try again."
            )
        lease.unlink(missing_ok=True)
        logger.info(f"{self.entry.id}: cleared {lease.name} left by process {holder}")

    # -- the console ----------------------------------------------------------------------

    def follow_logs(self, cancel: threading.Event | None = None) -> Iterator[str]:
        """This run's log, live: what is there, then each line as it lands, until it stops.

        With a `serve.session_log`, the file the program names in its own output is
        followed too, from its first line, once that line has been read: for DFO that
        is the gateway's own log, where the server says what it loads and what each
        player does. A named folder outside the server dir is not followed.

        Ends when `cancel` is set (the panel's Stop) or once the server is gone and
        the last of its output has been read.
        """
        spec = self.process.serve.session_log
        tails = [_Tail(self.log_path)]
        while True:
            for tail in list(tails):
                for text in tail.lines():
                    yield from lines.relayed(text, stage="console")
                    if spec is not None and len(tails) == 1 and text.startswith(spec.marker):
                        named = self._session_file(text[len(spec.marker) :].strip(), spec.file)
                        if named is not None:
                            tails.append(_Tail(named))
            if cancel is not None and cancel.is_set():
                return
            if self.status() == STOPPED:
                for tail in tails:
                    for text in (*tail.lines(), *tail.rest()):
                        yield from lines.relayed(text, stage="console")
                return
            if cancel is not None:
                cancel.wait(FOLLOW_POLL_SECONDS)
            else:
                self._seams.sleep(FOLLOW_POLL_SECONDS)

    def _session_file(self, folder: str, name: str) -> Path | None:
        """`folder/name` when the folder is inside this server dir, else None."""
        try:
            path = Path(folder).resolve()
            if not path.is_relative_to(self.server_dir.resolve()):
                return None
        except (OSError, ValueError):
            return None
        return path / name

    # -- who can connect -------------------------------------------------------------------

    def advertised(self) -> str | None:
        """None: this server takes players on this computer only."""
        return None

    def advertise(self, address: str) -> bool:
        """Refused: there is no address to tell anyone; the server answers on loopback."""
        raise SingleServerError(
            f"{self.entry.name} takes players on this computer only, so there is no address "
            "to change."
        )
