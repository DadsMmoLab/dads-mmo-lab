"""Shared Docker lifecycle helpers (game-agnostic).

Implements the Docker-CLI operations every per-game controller needs — start/stop
via `docker compose`, status/health/polling via `docker ps`/`docker inspect`/
`docker logs` — by shelling out through `yulon.runner`, which owns all subprocess
concerns (style-guide §3). This module is deliberately game-agnostic: the
per-game specifics (container names, published ports) are grouped in
`ContainerSpec` for readability/typing, and the `*_for(spec, ...)` convenience
wrappers accept a spec directly; the lower-level functions still take
individual container names/ports for callers that don't have a full spec.
Nothing in this module knows about any particular server.

The polling helpers mirror `dml-start.sh`'s `_wait_db_healthy` / `_wait_ready`
logic, generalized and given explicit, overridable timeouts.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import inspect
import io
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Generator, Iterator, Mapping, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import IO, Any, BinaryIO, Literal, NamedTuple, ParamSpec, TypeVar

from yulon import ansi, container_end, platform, runner, server_build_presses, update_failure, wsl
from yulon.after_stop import StopTookEffect, TrueAfterStop
from yulon.log import get_logger
from yulon.said import SaidByYulon, details_below
from yulon.ui import lines

logger = get_logger(__name__)

_DB_HEALTHY_TIMEOUT_SECONDS = 180.0
_READY_TIMEOUT_SECONDS = 480.0
_POLL_INTERVAL_SECONDS = 2.0
# `docker compose config` on the GUI thread; see compose_project_name().
_COMPOSE_CONFIG_TIMEOUT_SECONDS = 10.0


class DockerCommandError(RuntimeError):
    """Raised when a `docker` CLI command exits with a non-zero status."""


class DockerRefusal(DockerCommandError, SaidByYulon):
    """A `DockerCommandError` whose message is Yu'lon's own sentence, not Docker's (T214).

    Shown on the line as written. A plain `DockerCommandError` carries what
    Docker or compose said, and goes under Details. `detail` is what a
    container printed that explains the sentence, shown under Details beside it.
    """

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail


def logs_command(service: str, server_dir: Path) -> str:
    """How to read everything `service` printed, for Details -- never for the line (T248).

    The line says in words that the container's own log has the reason; the
    command is what a player who wants it copies from Details.
    """
    return (
        f"To read everything it printed, run this in {server_dir}:\ndocker compose logs {service}"
    )


class DockerCliMissingError(DockerCommandError, SaidByYulon):
    """Raised when there is no docker CLI to run at all — nothing was asked of Docker.

    A subclass, so every `except DockerCommandError` already written keeps
    catching it and no caller has to learn a second type to stay correct. What
    the subclass buys is the callers that must NOT treat it as "Docker was asked
    and would not answer": that answer is transient and worth retrying or
    degrading past, and this one is neither.

    Added 2026-08-23. The commit that routed every argv through
    `platform.docker_program()` said "failure stays honest" and it was not quite
    true: `_status_safe()` degraded a missing CLI to `None` exactly as it
    degrades a daemon hiccup, so `stop_staged()` told the user their install had
    no `COMPOSE_PROJECT_NAME` pinned — blaming the install for the absence of
    Docker — and `wait_ready()` polled out its full 480s without a word above
    DEBUG.
    """


class DockerUnansweredError(DockerRefusal):
    """Raised when Docker did not answer the `docker ps` that says what is running.

    A subclass, so every `except DockerCommandError` keeps catching it. What it
    buys is the Server tab telling this apart from Docker refusing the stop
    itself: its words were shown raw under the presses ("could not ask Docker
    what is running, so the stop cannot be confirmed") on the Linux live test of
    PR 291, and stayed there after Docker came back.

    A `DockerRefusal`: every message it is raised with is Yu'lon's own sentence,
    not Docker's output (T214).
    """


class DockerTimedOutError(DockerCommandError):
    """Raised when a `docker` command was given up on at its deadline: Docker did not answer.

    A subclass, so every `except DockerCommandError` keeps catching it, and its
    words are the generic failure's ("... exited 124: timed out after 30.0s").
    What the type buys is the Server tab's banner telling it apart from an exit
    status 124 a container gave: with Docker Desktop's engine VM killed, the CLI
    hung past the realm poll's 30 s and the banner called that "an error Yu'lon
    doesn't recognise" (PR 291's Windows live test, 2026-10-04).

    Not `SaidByYulon` (T214): the message is the docker command line and its
    exit status, not a sentence Yu'lon wrote, so a press it ends says in words
    that it broke and puts these words under Details.
    """


class SourceUnreadableError(RuntimeError):
    """Raised when the stream `exec_stdin()` was pumping stopped being readable.

    Deliberately NOT a `DockerCommandError`: docker did as it was told, the
    container is fine, and a caller that reads the two apart would otherwise
    tell a user with a half-downloaded file that their container refused the
    import. What broke is the file on this side.

    It exists because the alternative is no type at all. `source` is a
    `BinaryIO`, so what it raises belongs to whoever opened it: a truncated
    `.sql.gz` — a download over a flaky connection, the commonest corruption
    there is — raises `EOFError`, mangled deflate bytes raise `zlib.error`, a
    file that is not gzip at all raises `gzip.BadGzipFile`, and only the last
    of those three is an `OSError`. Letting each one through as it came would
    hand every caller the same open-ended set to catch that this module just
    stopped trying to enumerate; the apply stages catch
    `(RuntimeError, OSError)`, and being a `RuntimeError` is what makes that
    clause enough. The original is kept as `__cause__`, so nothing about which
    corruption it was is lost — it just stops being the caller's problem to
    predict.

    `BaseException` is not wrapped. A `KeyboardInterrupt` mid-pump is the user
    stopping the import, not a broken dump, and it travels on unchanged.
    """


@dataclass(frozen=True)
class SaveFirst:
    """How a world whose own close loses saves is asked to save everyone first (T410, T411).

    Built by `CatalogEntry.container_spec()` from the entry's `save_before_stop` and
    its console's prompt; `save_then_stop_the_world()` types it before the signal.
    """

    command: str
    queue_command: str = ""
    queue_pattern: str = ""
    first: tuple[str, ...] = ()
    """Typed before `command` so nothing new joins the save queue (T498): Centurion's bots out."""
    if_given_up: tuple[str, ...] = ()
    """Typed when the stop is given up before its signal, to undo `first` (T498)."""
    prompt: str = "AC>"
    prompt_precedes_answer: bool = True

    def queue_length(self, text: str) -> int | None:
        """The character save queue's length in a `queue_command` answer, or None."""
        if not self.queue_pattern:
            return None
        found = re.findall(self.queue_pattern, text)
        return int(found[-1]) if found and str(found[-1]).isdigit() else None


@dataclass(frozen=True)
class ContainerSpec:
    """How one server install is addressed: its containers, services and ports.

    Two different names for the same three things, because Docker uses two:
    `docker ps`/`docker inspect` answer to **container** names, while
    `docker compose` addresses **services**. For every AzerothCore-derived game
    they happen to be identical (`ac-database`, `ac-authserver`,
    `ac-worldserver`), which is why `services` may be left empty and defaults to
    the container names — but the distinction is real, and a game whose compose
    file names its services differently must say so rather than get silently
    wrong behaviour.
    """

    db: str
    auth: str
    world: str
    ports: tuple[int, ...]
    services: tuple[str, ...] = ()

    import_service: str = ""
    """The one-shot compose service that populates the databases, if the game has one.

    Named here rather than deduced, and empty for a game that has not said.
    `compose_services()` exists to keep this service *out* of every ordinary
    start; `repair_import()` is the only thing allowed to select it, and it
    refuses outright when this is empty rather than guessing at a service name
    and running whatever happens to answer to it.
    """

    stop_waits_for_load: bool = False
    """True for a world server that cannot hear a stop while it loads (T158).

    Set by `CatalogEntry.container_spec()` from the entry's
    `stop_waits_for_world_load`: the three CMaNGOS games, whose mangosd is the
    container's PID 1 and installs its SIGTERM handler only once the world has
    loaded. Every stop of such a world first goes through
    `wait_for_the_world_to_load()`.
    """

    save_first: SaveFirst | None = None
    """How this world is asked to save everyone before its signal, or None (T410, T411).

    Set by `CatalogEntry.container_spec()` from the entry's `save_before_stop`:
    Centurion and Tortoise, whose own close loses saves. See
    `save_then_stop_the_world()`.
    """

    def compose_services(self) -> tuple[str, ...]:
        """The long-running compose services, in dependency order (db first).

        Deliberately excludes one-shot services such as `ac-db-import`: naming
        the services explicitly is what keeps `compose up` from ever selecting
        the import job. See `start_staged()`.
        """
        return self.services or (self.db, self.auth, self.world)

    def service_for(self, container: str) -> str:
        """The compose service that owns `container`, or `container` unchanged.

        Every message that tells a user to run `docker compose logs X` needs
        this: the code knows which CONTAINER is missing, and compose only
        answers to SERVICE names. Unchanged for a name this spec does not know,
        because a possibly-right command beats a confidently wrong one — and
        unchanged for every AzerothCore game, where the two names are equal.
        """
        containers = (self.db, self.auth, self.world)
        services = self.compose_services()
        for known, service in zip(containers, services, strict=False):
            if known == container:
                return service
        return container


CANCELLED_RETURNCODE = -1
"""What `run_attached()` reports when the caller's cancel token stopped the read.

Negative on purpose: a real process exit status is 0-255, and Python already
spells "killed by signal N" as `-N`, so no docker command can produce this by
exiting. A caller must not read it as a failure of the thing being run — the
build step or the one-shot is still finishing inside the daemon.
"""

_CLI_MISSING_RETURNCODE = 127
"""What a shell reports for "command not found", and what `_docker()` returns.

Borrowed rather than invented so the value means something to anyone reading a
log: no docker command can exit 127 itself, and every caller here already
branches on `returncode != 0`.
"""

_MISSING_CWD_RETURNCODE = 66
"""What `_docker()` and `run_attached()` report when `cwd` does not exist.

Borrowed from BSD sysexits' `EX_NOINPUT` ("cannot open input"), which is what
happened: the input this call needed — the server folder — could not be
opened. Distinct from `_CLI_MISSING_RETURNCODE` on purpose, so `_cli_missing()`
never mistakes a deleted server folder for a missing docker CLI; a WinError
267 from a `cwd` that does not exist used to be caught by the `except OSError`
below and reported as exactly that.
"""


def _cwd_is_missing(cwd: Path) -> bool:
    """`platform.folder_is_gone()`, named and patchable on its own for a test.

    Not `Path.is_dir()` — that also reads "gone" for a permission refusal or a
    stalled network share, and this call must ask Docker anyway when it
    genuinely cannot tell (T34 round 2). A seam of its own rather than calling
    `platform.folder_is_gone()` directly everywhere in this module, so a test
    can fake a deleted folder without also blinding the bind-mount probe and
    the SELinux checks, which walk real directories for reasons of their own.
    """
    return platform.folder_is_gone(cwd)


def _missing_cwd_result(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        command,
        _MISSING_CWD_RETURNCODE,
        "",
        f"The server folder {cwd} no longer exists, so Docker was not asked.",
    )


def _docker(
    argv: list[str],
    cwd: Path | None = None,
    timeout: float | None = None,
    *,
    wsl_distro: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run `docker <argv...>` under whatever name this host can actually start it.

    The single place this module names the CLI. `platform.docker_program()`
    exists because a Windows process cannot see the PATH entry Docker Desktop's
    installer just wrote, so the run that provisions Docker is exactly the run
    that must not spell the command `docker` and hope (see there).

    A host with no docker CLI at all comes back as a non-zero
    `CompletedProcess` carrying `DOCKER_CLI_MISSING_HELP` in `stderr`, not as an
    exception — the same shape `runner.run()` gives a timeout, and for the same
    reason: `health()` answers `"unknown"` and `container_state()` answers
    empty, and both of those degraded answers are right for "docker is missing"
    too. Raising instead would take a polling loop off the GUI thread's rails to
    say something its caller already knows how to report.

    It stays *recognisable* while it degrades, though — see `_cli_missing()`.
    The callers that must not confuse it with "Docker was asked and would not
    answer" are the ones that go on to explain themselves to the user.

    `OSError` is caught for the one case resolution cannot cover: Docker
    uninstalled while the launcher is open, leaving `docker_program()`'s pinned
    path aimed at a file that is gone. The user hears "Docker could not be
    found", which is true.

    Before the `cwd.is_dir()` guard above, a deleted server folder fell into
    this same `except OSError` and was misreported the same way: on
    `yulon-win11`, `subprocess` raised `[WinError 267] The directory name is
    invalid` for a `cwd` that no longer existed, and the owner was told to
    install a Docker Desktop that was already running (T34). The guard answers
    that case before `subprocess` is asked at all, so `OSError` here is once
    again only ever "the CLI itself is gone".

    The two branches log at different levels on purpose. "No CLI at all" is
    already in the `stderr` this returns, and every caller here logs that — a
    warning would print the same sentence twice for every command, and
    `wait_ready()` issues five per poll. The `OSError` carries what the sentence
    does not: which path was tried, and the errno.
    """
    # A Windows process cannot cd into a distro, so for a WSL install the
    # location rides in the argv instead - `wsl --cd`, positioned by
    # `docker_prefix()` because only it knows the flag belongs before the `--`
    # separator. `--cd` rather than compose's `--project-directory`, because
    # this function runs every docker subcommand and only compose understands
    # the latter.
    try:
        inside = (
            platform.wsl_linux_path_in(cwd, wsl_distro)
            if (wsl_distro is not None and cwd is not None)
            else None
        )
    except platform.WslDistroMismatch as exc:
        # The existing "the command failed" shape, so every caller already has
        # a sentence for it (wsl-resident-servers §6); nothing was started.
        return subprocess.CompletedProcess(["docker", *argv], 1, "", str(exc))
    prefix = platform.docker_prefix(wsl_distro, inside=inside)
    if prefix is not None:
        command = [*prefix, *argv]
        run_cwd: Path | None = None if wsl_distro is not None else cwd
        if run_cwd is not None and _cwd_is_missing(run_cwd):
            logger.warning(f"server folder gone: {run_cwd}")
            return _missing_cwd_result(command, run_cwd)
        try:
            return runner.run(command, cwd=run_cwd, timeout=timeout)
        except OSError as exc:
            logger.warning(f"{prefix[0]} could not be started: {exc}")
    else:
        where = f" in {wsl_distro}" if wsl_distro else ""
        logger.debug(f"no docker CLI on this host{where}; not running: docker {' '.join(argv)}")
    return subprocess.CompletedProcess(
        ["docker", *argv], _CLI_MISSING_RETURNCODE, "", platform.DOCKER_CLI_MISSING_HELP
    )


def _cli_missing(proc: subprocess.CompletedProcess[str]) -> bool:
    """True if this result is `_docker()`'s "there is no docker CLI" sentinel.

    Both halves are checked on purpose. The exit code alone would be a guess:
    `docker run` and `docker exec` return the *container's* status, so a real
    docker command can genuinely exit 127 (a command not found inside the
    image). Nothing in this module runs either — but `_CLI_MISSING_RETURNCODE`'s
    "no docker command can exit 127 itself" is only true of the commands here
    today, and pairing it with the exact help text means adding one costs
    nothing.
    """
    return (
        proc.returncode == _CLI_MISSING_RETURNCODE
        and proc.stderr == platform.DOCKER_CLI_MISSING_HELP
    )


def _run(
    argv: list[str],
    cwd: Path | None = None,
    *,
    timeout: float | None = None,
    wsl_distro: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run `docker <argv...>`; raise `DockerCommandError` on non-zero exit.

    Raises `DockerCliMissingError` — a subclass — when there was no CLI to run,
    carrying `DOCKER_CLI_MISSING_HELP` and nothing else. The argv is dropped
    from the message deliberately: `docker ps --format {{.Names}} exited 127:`
    in front of the sentence is noise to the user reading it in a dialog, and
    `_docker()` has already put the command in the log at DEBUG.
    """
    proc = _docker(argv, cwd=cwd, timeout=timeout, wsl_distro=wsl_distro)
    if _cli_missing(proc):
        raise DockerCliMissingError(platform.DOCKER_CLI_MISSING_HELP)
    if proc.returncode != 0:
        # Asked before the generic message is built, because on this one failure
        # the generic message is empty: a distro that no longer exists makes
        # wsl.exe complain on STDOUT and exit 0xFFFFFFFF, so the user was shown
        # `docker ps exited 4294967295: ` with nothing after the colon. The
        # knowledge of wsl.exe's codes and its UTF-16 output stays in `wsl.py`;
        # this seam only asks (see `wsl.missing_distro_problem`).
        problem = wsl.missing_distro_problem(wsl_distro, proc.returncode, proc.stdout)
        if problem is not None:
            raise DockerRefusal(problem)
        refusal = _port_refusal(proc.stderr)
        if refusal is not None:
            # The sentence is Yu'lon's; the daemon's own text rides beside it as the detail,
            # because the channel's rollback reads WHICH port from it (`blames_the_host_port`).
            raise DockerRefusal(refusal, detail=proc.stderr.strip())
        headline = _daemon_lines(proc.stderr) if argv[:1] == ["compose"] else ""
        if headline:
            # Compose's pull and create progress is dropped from the sentence, not
            # from the record: whatever else it printed stays in the log.
            logger.info(f"docker {' '.join(argv)} exited {proc.returncode}: {proc.stderr.strip()}")
        said = (
            f"docker {' '.join(argv)} exited {proc.returncode}: {headline or proc.stderr.strip()}"
        )
        if timeout is not None and runner.timed_out(proc):
            raise DockerTimedOutError(said)
        raise DockerCommandError(said)
    return proc


_DAEMON_LINE = re.compile(r"^[ \t]*(Error response from daemon:.*?)[ \t]*$", re.MULTILINE)
_PORT_NOT_AVAILABLE = re.compile(
    r"ports are not available: exposing port TCP \S*?:(\d+) -> \S+: listen tcp\d*"
    r" \S+: bind: (?P<why>[^\r\n]*)"
)


def _daemon_lines(stderr: str) -> str:
    """The daemon's own `Error response from daemon:` lines in compose's output, else "" (T574).

    `docker compose up` prints its pull and create progress first and the reason
    it failed last, so the head of stderr names an image layer, not the
    failure. Several lines are joined; the same line twice is kept once. Only
    a compose command's headline is cut down to these lines (see `_run()`).
    """
    found: list[str] = []
    for match in _DAEMON_LINE.finditer(stderr):
        if match.group(1) not in found:
            found.append(match.group(1))
    return " / ".join(found)


def _port_refusal(stderr: str) -> str | None:
    """The plain sentence for the daemon's `ports are not available` error, or None (T574).

    The preflight bind check covers a port blocked when the install starts; this
    covers one that became blocked afterwards (a range Windows reserved at the
    next boot). Only the daemon's own wording is read, so no host is asked:
    Windows' "forbidden by its access permissions" (WSAEACCES) and a Linux
    "permission denied" are `reserved`, everything else the bind said is `in_use`.
    A message without the port number in the shape Docker prints it is left alone.
    """
    for line in _daemon_lines(stderr).split(" / "):
        match = _PORT_NOT_AVAILABLE.search(line)
        if match is None:
            continue
        why = match.group("why").lower()
        windows = "forbidden by its access permissions" in why
        kind: Literal["reserved", "in_use"] = (
            "reserved" if windows or "permission denied" in why else "in_use"
        )
        return blocked_port_sentence(int(match.group(1)), kind, windows=windows)
    return None


def daemon_ready(*, wsl_distro: str | None = None, timeout: float = 30.0) -> bool:
    """True if `docker info` answers from the daemon this install lives on.

    `platform.docker_ready()` is the LOCAL question, and it is what an install
    asks. A server inside a WSL distro lives on that distro's Docker, which the
    local CLI cannot see and which may be the only Docker there is (T125): a
    rebuild asking the local question would refuse on a machine whose server is
    running fine, or answer yes from a Docker Desktop that has never heard of it.
    """
    proc = _docker(
        ["info", "--format", "{{.ServerVersion}}"], timeout=timeout, wsl_distro=wsl_distro
    )
    return proc.returncode == 0 and bool(proc.stdout.strip())


class ServerHeldError(DockerCommandError, SaidByYulon):
    """A start, stop or recreate refused because a maintenance job holds this server (T216).

    The message is the holder's own sentence (`hold_the_server()`'s `reason`),
    so the press that was refused says which job to wait for. Nothing was run.
    """


SERVER_IN_MOTION = (
    "This server is being started, stopped or recreated right now. Wait for that to "
    "finish, then try again."
)
"""Why a hold could not be taken: a lifecycle command for the same server is running."""

_HOLD_LOCK = threading.Lock()
_HELD: dict[str, list[str]] = {}
_IN_FLIGHT: dict[str, int] = {}

_P = ParamSpec("_P")
_R = TypeVar("_R")


def _server_key(server_dir: Path | str) -> str:
    """One spelling per server folder. String arithmetic only: no filesystem is touched.

    Not `Path.resolve()`, which reads the disk -- and on Windows a read under
    `\\\\wsl.localhost\\<distro>` starts a stopped distro (T133).
    """
    return os.path.normcase(os.path.abspath(os.fspath(server_dir)))


@contextmanager
def hold_the_server(server_dir: Path | str, reason: str) -> Iterator[None]:
    """Keep every start, stop and recreate of this server away while the block runs (T216).

    For the Maintenance tab's restore, and for a backup that started the
    database alone. Both write or read the databases with the game servers
    stopped and, when they started the database, stop it again at the end. A
    Start pressed in between brought the world up on databases being written
    -- the hazard the restore plan refuses for -- and the cleanup then stopped
    the database under that running world; a Stop or a Restart took the
    database from under the load. Start stays enabled throughout (only the
    database runs, and the view is not busy), so the press itself cannot be
    trusted to be absent.

    One guard at this layer, not one per button: every press that starts,
    stops or recreates this install -- Start, Stop, Restart and the recreate on
    the Tuning tab, "Start and play", "Stop the other server and start this
    one", a rebuild's or an update's recreate, the Tortoise bot rebuild's
    restart, a removal's teardown -- reaches `docker.compose` through one of
    the functions marked `@_a_lifecycle_command`, and each refuses here with
    `reason` before it runs anything. The database-only start and stop the
    jobs use themselves (`start_database()`, `stop_containers()`) are not
    lifecycle commands and are not held.

    "Stop the other server and start this one" pressed on ANOTHER install is
    not held here either, and does not need to be: its `stop_conflicting()`
    stops only an install whose containers publish that install's auth or world
    port (`ContainerSpec.ports`, never the database's), and a held install has
    neither running -- the restore plan refuses while they are up, and this
    hold refuses their start. Both Codex reviews of 2026-10-04 read the
    sentence above as covering that cross-server press; it covers the press
    made on the held install itself.

    Taken only when no lifecycle command for this server is running: a Start
    waiting for the database to report healthy has a world container that
    exists and is not running yet, which passes the restore's name census.

    Raises:
        ServerHeldError: a start, stop or recreate of this server is running
            (`SERVER_IN_MOTION`).
    """
    key = _server_key(server_dir)
    with _HOLD_LOCK:
        if _IN_FLIGHT.get(key):
            raise ServerHeldError(SERVER_IN_MOTION)
        _HELD.setdefault(key, []).append(reason)
    try:
        yield
    finally:
        with _HOLD_LOCK:
            reasons = _HELD[key]
            reasons.remove(reason)
            if not reasons:
                del _HELD[key]


@contextmanager
def _in_flight(server_dir: Path | str) -> Iterator[None]:
    """Mark a lifecycle command for this server as running, unless the server is held."""
    key = _server_key(server_dir)
    with _HOLD_LOCK:
        held = _HELD.get(key)
        if held:
            logger.warning(f"refused a start/stop/recreate of {server_dir}: {held[-1]}")
            raise ServerHeldError(held[-1])
        _IN_FLIGHT[key] = _IN_FLIGHT.get(key, 0) + 1
    try:
        yield
    finally:
        with _HOLD_LOCK:
            _IN_FLIGHT[key] -= 1
            if not _IN_FLIGHT[key]:
                del _IN_FLIGHT[key]


def lifecycle(server_dir: Path | str) -> contextlib.AbstractContextManager[None]:
    """One lifecycle command made of several: a restart, a recreate, a bot restart (T216).

    `@_a_lifecycle_command` marks each primitive while it runs, so between a
    restart's stop and its start nothing is marked, and a restore could take
    its hold there -- refusing the start and leaving the server stopped, or
    after a recreate removed (review round 3). Wrapped around every step of
    the composite, the mark lasts from its first step to its last; the marks
    nest, so the primitives inside run as before.

    Raises:
        ServerHeldError: the server is held, so not even the first step runs.
    """
    return _in_flight(server_dir)


class MaintenanceLeaseTaken(RuntimeError, SaidByYulon):
    """A Backup or Restore refused because another one of this server is running (T216).

    The message is the holder's own sentence (`maintenance_lease()`'s
    `reason`); the refused job adds what it did not do.
    """


_LEASED: dict[str, str] = {}


@contextmanager
def maintenance_lease(server_dir: Path | str, reason: str) -> Iterator[None]:
    """One Backup or Restore of this server at a time, for the whole of it (T216 round 3).

    Separate from `hold_the_server()`, which is about the containers: a hot
    backup of a running server takes no hold, so Start stays free during it,
    and still takes this. What it stops is two jobs on one set of databases:
    a mysqldump running beside a restore's load reported success for a copy
    of half-old, half-new data, and a Restore pressed while a stopped server's
    Backup had started the database loaded into it -- and the backup's own
    cleanup then stopped the database under the half-loaded restore.

    In this module and not on the view because one server can have more than
    one view (T187 gives each server its own launcher window), and a flag on
    one of them would not be seen by the other.

    Raises:
        MaintenanceLeaseTaken: a Backup or Restore of this server holds it.
    """
    key = _server_key(server_dir)
    with _HOLD_LOCK:
        holder = _LEASED.get(key)
        if holder is not None:
            raise MaintenanceLeaseTaken(holder)
        _LEASED[key] = reason
    try:
        yield
    finally:
        with _HOLD_LOCK:
            del _LEASED[key]


def _a_lifecycle_command(command: Callable[_P, _R]) -> Callable[_P, _R]:
    """Run `command` only while its server is not held, and mark it running meanwhile (T216).

    The server is read off the call's own `server_dir` argument, so every
    caller -- and every alias a game's `docker_ctl` binds -- goes through it.
    """
    signature = inspect.signature(command)

    @functools.wraps(command)
    def run(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        server_dir = signature.bind_partial(*args, **kwargs).arguments["server_dir"]
        with _in_flight(server_dir):
            return command(*args, **kwargs)

    return run


@_a_lifecycle_command
def start(server_dir: Path, *, wsl_distro: str | None = None) -> None:
    """Bring the compose project in `server_dir` up in the background.

    Creates whatever does not exist yet, which on an installed server also
    re-runs the one-shot containers. Prefer `start_staged()` for a server that
    has already been installed — see the warning there.
    """
    logger.debug(f"start() called: server_dir={server_dir}")
    _run(["compose", "up", "-d"], cwd=server_dir, wsl_distro=wsl_distro)


PROJECT_NAME_VAR = "COMPOSE_PROJECT_NAME"


def compose_project_name(server_dir: Path, *, wsl_distro: str | None = None) -> str | None:
    """What compose currently calls this project, or None if it cannot say.

    Asked rather than computed. Compose derives the name from the directory
    basename by lowercasing it, dropping every character outside `[a-z0-9_-]`
    and then trimming leading punctuation — measured: `WoW_Server 2` becomes
    `wow_server2`, `_leading` becomes `leading`, `Ünïcode` becomes `ncode`.
    Reimplementing that here would be a second copy of somebody else's rule,
    free to drift, and a wrong guess is worse than no guess: pinning the wrong
    value *renames* the project and orphans the containers it was meant to keep.
    """
    # Bounded, because two callers run on the GUI thread — pinning after an
    # install, and the ownership lookup behind a Stop. Measured floor is about
    # 0.6s even with the daemon down, and it is unbounded on a picked folder
    # that lives on a sleeping NAS or behind a stalled docker CLI. Failing to
    # name the project is already a handled outcome; freezing the window is not
    # (review, 2026-08-22).
    proc = _docker(
        ["compose", "config", "--format", "json"],
        cwd=server_dir,
        timeout=_COMPOSE_CONFIG_TIMEOUT_SECONDS,
        wsl_distro=wsl_distro,
    )
    if proc.returncode != 0:
        logger.debug(f"compose config failed in {server_dir}: {proc.stderr.strip()}")
        return None
    try:
        parsed = json.loads(proc.stdout)
    except ValueError:
        logger.debug("compose config did not return JSON")
        return None
    name = parsed.get("name") if isinstance(parsed, dict) else None
    return name if isinstance(name, str) and name else None


def pin_project_name(server_dir: Path, *, wsl_distro: str | None = None) -> str | None:
    """Freeze this install's compose project name into its own `.env`.

    Compose identifies a project by its directory basename unless told
    otherwise, but AzerothCore pins its container names, which are global. Move
    or rename the install folder and the two identities come apart: `compose`
    commands in the new directory address a project that owns nothing, so
    `compose stop` stops nothing and `compose up` collides with the containers
    that are still there under the old project. Writing the name down once, in
    the install itself, is what makes the folder movable.

    `wow-manage.sh` does the same thing on its own move command, which is where
    this behaviour comes from; writing it down whenever the name is provably
    right means the folder can then be moved by any means — a file manager, a
    backup restore — and still work.

    Only two callers may use this, and both are moments where the directory
    basename provably IS what compose named the containers: straight after this
    app's own installer finished, and after a stop that confirmed our own
    containers by label. Attaching an existing install is NOT such a moment —
    see `install_project()`.

    Returns the pinned name, or None if nothing was written (already pinned, or
    compose could not be asked).
    """
    env_path = server_dir / ".env"
    if pinned_project_name(server_dir) is not None:
        logger.debug(f"{PROJECT_NAME_VAR} already pinned in {env_path}")
        return None
    name = compose_project_name(server_dir, wsl_distro=wsl_distro)
    if name is None:
        logger.info(f"could not ask compose for the project name in {server_dir}; not pinning")
        return None
    # Byte-preserving and atomic, because this file holds the database root
    # password. `write_text` truncates before it writes, so a crash or a full
    # disk in between would leave the user with an empty `.env` and a database
    # they can no longer reach. Reading as BYTES also means a non-UTF-8 value
    # survives untouched instead of being silently rewritten through
    # `errors="replace"` (adversarial review finding, 2026-08-22).
    try:
        existing = env_path.read_bytes() if env_path.is_file() else b""
    except OSError as exc:
        logger.warning(f"could not read {env_path}; not pinning: {exc}")
        return None
    if existing and not existing.endswith(b"\n"):
        existing += b"\n"
    addition = (
        "# Pinned by Yu'lon so this install keeps working if the folder is moved.\n"
        f"{PROJECT_NAME_VAR}={name}\n"
    ).encode()
    try:
        # T127: never wider than 0600 -- `.env` holds the root password.
        platform.write_private_atomically(env_path, existing + addition)
    except OSError as exc:
        logger.warning(f"could not write {env_path}; not pinning: {exc}")
        return None
    logger.info(f"pinned {PROJECT_NAME_VAR}={name} in {env_path}")
    return name


def pinned_project_name(server_dir: Path) -> str | None:
    """The project name this install pinned into its own `.env`, if any.

    Read directly, without asking compose. `compose_project_name()` needs to
    parse the compose files, which is exactly what is unavailable in the case
    the by-name stop path exists for — so ownership would be unprovable at the
    one moment it is needed most, and a running server would be left up while
    the user is told it stopped.

    Every rule here was measured against the real `docker compose` rather than
    assumed, because a disagreement is not cosmetic: this value decides which
    containers the app believes are its own. Checked cases (review, 2026-08-22):

    * the LAST assignment wins, not the first — appending is exactly how a user
      follows the app's own "set COMPOSE_PROJECT_NAME=X in .env" advice;
    * `export ` is accepted;
    * an inline `# comment` is not part of the value — but a `#` inside quotes
      is, so it can only be stripped outside them;
    * surrounding single or double quotes are removed, and only a matched pair;
    * an empty assignment UNSETS it (compose falls back to the basename), so it
      cannot leave an earlier value standing;
    * a **UTF-8 byte-order mark** in front of the first line is not part of the
      first variable's name — see `utf-8-sig` below.

    `utf-8-sig`, not `utf-8`, and this one was found on Windows rather than
    reasoned about. `_stranger_message()` tells the user in as many words to add
    `COMPOSE_PROJECT_NAME=<x>` to this file, and on Windows the tools they have
    to hand put a BOM in front of it: PowerShell 5.1's `Set-Content -Encoding
    utf8` writes `EF BB BF`, and Notepad's "UTF-8 with BOM" does the same.
    Decoded as plain `utf-8` that becomes a leading `\\ufeff` on the first line,
    so `startswith("COMPOSE_PROJECT_NAME=")` is False and the pin is invisible.

    What makes it a defect rather than a quirk is that **compose reads the same
    file and does not agree**. Measured on Windows 11 / Docker 29.7.2
    (2026-08-23): with a BOM'd `.env`, `docker compose config` reports the
    project as `bomtest` while this function reported `None`. The two then
    disagree about which project this install is, which is the exact condition
    `install_project()` exists to prevent — and the fallback that hides it,
    asking compose, is unavailable in the one case this function exists for (an
    install whose compose files cannot be read). It also made `pin_project_name()`
    believe nothing was pinned and append a SECOND assignment, where compose's
    last-one-wins then silently overrides whatever the user had set.

    `utf-8-sig` strips a BOM if there is one and is byte-for-byte `utf-8` if
    there is not, so nothing else changes. A UTF-16 `.env` — what PowerShell's
    bare `>` and `Out-File` write — is deliberately NOT handled: compose cannot
    read one either, so that file is broken for both of us and guessing at an
    encoding here would be the app inventing an agreement that does not exist.
    """
    env_path = server_dir / ".env"
    if not env_path.is_file():
        return None
    try:
        text = env_path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None
    found: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("export"):
            stripped = stripped[len("export") :].lstrip()
        if stripped.startswith(f"{PROJECT_NAME_VAR}="):
            value = _env_value(stripped.split("=", 1)[1])
            if "${" in value or "$" in value:
                # Needs interpolation; see `_env_value`. Report no pin rather
                # than a literal no container carries.
                logger.info(f"{PROJECT_NAME_VAR} in {env_path} needs expanding; asking compose")
                value = ""
            found = value or None
    return found


def _env_value(raw: str) -> str:
    """One `.env` right-hand side, read the way compose reads it.

    With one deliberate exception: compose expands `${VAR}` in unquoted and
    double-quoted values, and this does not. Reimplementing interpolation here
    would be a second copy of somebody else's rule — the thing
    `compose_project_name()`'s docstring refuses to do — so a value that needs
    expanding is reported as *no pin at all*, and ownership falls through to
    asking compose. That fails closed: the install stays stoppable while its
    compose files are readable, and refuses honestly when they are not, instead
    of silently believing in a project literally named `ac-${REALM}` that no
    container carries (review, 2026-08-23).
    """
    raw = raw.strip()
    for quote in ('"', "'"):
        if len(raw) >= 2 and raw.startswith(quote):
            end = raw.find(quote, 1)
            if end > 0:
                return raw[1:end]  # anything after the closing quote is a comment
            return raw[1:].strip()  # unterminated: take the rest, minus the quote
    return raw.split("#", 1)[0].strip()


PROJECT_LABEL = "com.docker.compose.project"
WORKING_DIR_LABEL = "com.docker.compose.project.working_dir"
SERVICE_LABEL = "com.docker.compose.service"
"""The compose service a container was made for: an `up` one and a `run` one alike (T539)."""


UNREADABLE = "\x00unreadable"
"""Docker would not say who owns a container — not the same as "nobody owns it".

Two failures used to collapse into `None` here: `docker inspect` erroring out,
and a container that genuinely carries no compose label. They need different
answers — the first is "ask again later", the second is "this is not ours" — so
the unreadable case gets a value no project name can collide with (review,
2026-08-22).
"""


def container_project(container: str, *, wsl_distro: str | None = None) -> str | None:
    """Which compose project owns this container.

    Returns the project name, `None` for a container carrying no compose label
    (something started outside compose), or `UNREADABLE` when Docker could not
    be asked at all.
    """
    fmt = '{{index .Config.Labels "' + PROJECT_LABEL + '"}}'
    proc = _docker(["inspect", container, "--format", fmt], wsl_distro=wsl_distro)
    if proc.returncode != 0:
        logger.warning(f"could not read the compose project of {container}: {proc.stderr.strip()}")
        return UNREADABLE
    return proc.stdout.strip() or None


def container_working_dir(container: str, *, wsl_distro: str | None = None) -> str | None:
    """Which directory the compose project owning this container was brought up from.

    `container_project()` answers WHO owns it; this answers WHERE, which is what
    a person actually needs when they are told to go and stop something. Compose
    bakes the label in at container creation, so a moved install reports the path
    it was created at rather than where it lives now - worth knowing when the
    answer looks wrong, and still better than naming nothing at all.

    Returns `None` for a container carrying no such label, and `UNREADABLE` when
    Docker could not be asked, on the same reasoning as `container_project()`.
    """
    fmt = '{{index .Config.Labels "' + WORKING_DIR_LABEL + '"}}'
    proc = _docker(["inspect", container, "--format", fmt], wsl_distro=wsl_distro)
    if proc.returncode != 0:
        logger.warning(f"could not read the working dir of {container}: {proc.stderr.strip()}")
        return UNREADABLE
    return proc.stdout.strip() or None


def folder_projects(folder: Path, *, wsl_distro: str | None = None) -> tuple[str, ...] | None:
    """The compose project of every container brought up from `folder`, running or not (T170).

    Asked of the containers' own labels -- `docker ps -a` filtered on
    `WORKING_DIR_LABEL`, printing `PROJECT_LABEL` -- and not of their names,
    because the question is about a folder whose compose file is not Yu'lon's:
    the repository's own may name its containers anything. One entry per
    container, so a project appears as often as it has containers; a container
    whose project label reads empty is left out.

    Compose writes the working dir as the absolute folder it was run in, so the
    filter is asked with the folder as given -- measured on a test box with a
    compose stand-in (2026-09-28): the filter answers with the project for the
    folder spelled as it was brought up from. `None` when Docker could not be
    asked: a caller that would write on "no containers" must not read it that way.
    """
    fmt = '{{.Label "' + PROJECT_LABEL + '"}}'
    argv = ["ps", "-a", "--filter", f"label={WORKING_DIR_LABEL}={folder}", "--format", fmt]
    proc = _docker(argv, wsl_distro=wsl_distro)
    if proc.returncode != 0:
        logger.warning(f"could not list the containers brought up from {folder}: {proc.stderr}")
        return None
    return tuple(line.strip() for line in proc.stdout.splitlines() if line.strip())


def project_container_images(
    project: str, *, wsl_distro: str | None = None
) -> tuple[tuple[str, str, str], ...] | None:
    """`(container, image it was created from, image id)` for every container of `project` (T170).

    What a rebuild keeps as its rollback when this install's image NAMES are
    gone (moved aside by a retag, removed with `-f`) and its containers still
    exist: a container holds its image by id whatever happened to the name, and
    `.Config.Image` says which of the install's refs it was made from (measured
    on a test box, 2026-09-28: both still read after `docker rmi` of the tag). Two
    calls: `docker ps -a` filtered on `PROJECT_LABEL` for the names, then one
    `docker inspect` of them all. `None` when either could not be asked, or its
    answer could not be read.
    """
    listed = _docker(
        ["ps", "-a", "--filter", f"label={PROJECT_LABEL}={project}", "--format", "{{.Names}}"],
        wsl_distro=wsl_distro,
    )
    if listed.returncode != 0:
        logger.warning(f"could not list the containers of {project}: {listed.stderr}")
        return None
    names = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
    if not names:
        return ()
    fmt = "{{.Name}}\t{{.Config.Image}}\t{{.Image}}"
    proc = _docker(["inspect", "--format", fmt, *names], wsl_distro=wsl_distro)
    if proc.returncode != 0:
        logger.warning(f"could not read the images of {project}'s containers: {proc.stderr}")
        return None
    found: list[tuple[str, str, str]] = []
    for line in proc.stdout.splitlines():
        parts = line.strip().split("\t")
        if len(parts) != 3 or not all(parts):
            logger.warning(f"could not read a container of {project} from {line!r}")
            return None
        found.append((parts[0].lstrip("/"), parts[1], parts[2]))
    return tuple(found)


def install_project(
    spec: ContainerSpec, server_dir: Path, *, wsl_distro: str | None = None
) -> str | None:
    """This install's compose project, asked of the containers themselves first.

    The directory is the WRONG source of truth here. Compose derives a project
    from the directory basename, so a folder that has been moved reports a name
    no existing container carries — and an install created before pinning
    existed (which is every install in the wild) has no `.env` value to correct
    it. Comparing the directory's answer against the containers' labels then
    never matches, `_running()` finds nothing of ours, and a stop that stopped
    nothing reports success while players are still connected.

    It is tempting to read the identity off a container's own
    `com.docker.compose.project` label instead — that is what compose actually
    stamped, and it would fix the moved install outright. It also breaks the
    case this whole ownership check exists for: two installs of one game share
    container NAMES, so a stopped install would adopt the *running* install's
    project as its own and then stop it. Without a pin the two situations are
    genuinely indistinguishable from here, so the unresolved case is reported
    rather than guessed — see `stop_staged()`.

    The pin is therefore written at the one moment it is provably right: after
    this app's own installer finished, when the directory IS what compose just
    named the containers after. Attaching an existing install does not pin, an
    already-moved folder being exactly what that path exists to adopt.
    """
    return pinned_project_name(server_dir) or compose_project_name(
        server_dir, wsl_distro=wsl_distro
    )


@dataclass(frozen=True)
class Running:
    """Containers wearing this install's names, split by who actually owns them.

    A container name proves existence, never ownership. Two installs of one game
    carry identical container names — AzerothCore pins them globally — so a
    check that goes by name alone reports the *other* install's running server
    as this one's, and then acts on it.

    Every list is in stop order: world, then auth, then the database.
    """

    ours: tuple[str, ...] = ()
    """Running and carrying our project's label. Safe to act on."""

    strangers: tuple[tuple[str, str | None], ...] = ()
    """(container, the project that owns it) — `None` for "no compose label"."""

    unreadable: tuple[str, ...] = ()
    """Running, but Docker would not say who owns them. Not proof of anything."""


def _running(spec: ContainerSpec, project: str, *, wsl_distro: str | None = None) -> Running:
    """Classify what is running under this install's names by compose project label.

    Compose stamps every container it creates with the project it belongs to,
    and that label is the only ownership proof available. Anything that is not
    provably ours ends up in `strangers` or `unreadable`, never in `ours` — the
    caller must fail closed on those and say so rather than fall back to a name.

    Raises:
        DockerCliMissingError: There is no docker CLI here — allowed through
            from `_status_safe()` so the user hears that rather than "the stop
            cannot be confirmed", which describes a Docker that answered badly
            and says nothing about one that is not installed.
        DockerCommandError: Docker could not be asked what is running at all,
            so no claim about this install can be made.
    """
    listed = _status_safe(wsl_distro=wsl_distro)
    if listed is None:
        raise DockerUnansweredError(
            "could not ask Docker what is running, so the stop cannot be confirmed"
        )
    running = {line.strip() for line in listed}
    ours: list[str] = []
    strangers: list[tuple[str, str | None]] = []
    unreadable: list[str] = []
    for name in (spec.world, spec.auth, spec.db):
        if name not in running:
            continue
        owner = container_project(name, wsl_distro=wsl_distro)
        if owner == UNREADABLE:
            unreadable.append(name)
        elif owner == project:
            ours.append(name)
        else:
            strangers.append((name, owner))
    return Running(tuple(ours), tuple(strangers), tuple(unreadable))


def exec_output(
    container: str,
    argv: list[str],
    *,
    timeout: float | None = 60.0,
    wsl_distro: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run `docker exec <container> <argv...>` and return the result UNTOUCHED.

    The read-only sibling of `exec_stdin()`, for asking a running container a
    question whose answer is small: what files are in a directory the image owns
    and nothing outside it can see.

    Not raised on a non-zero exit, deliberately and for the same reason
    `exec_stdin()` is not: `docker exec` returns the CHILD's status, so exit 1
    can mean "the container said no", "there is no such container" or "the
    daemon never answered", and only the caller knows which of those it is
    allowed to treat as an answer. `_run()` would flatten all three into one
    exception. A host with no docker CLI comes back as `_docker()`'s recognisable
    sentinel rather than an exception, for the same reason again.

    The timeout is bounded by default because every caller so far is on a path a
    user is waiting on, and a wedged daemon must not turn a directory listing
    into an indefinite wait.
    """
    return _docker(["exec", container, *argv], timeout=timeout, wsl_distro=wsl_distro)


def container_exists(container: str, *, wsl_distro: str | None = None) -> bool:
    """True if a container by that name exists at all, running or exited."""
    proc = _run(["ps", "-a", "--format", "{{.Names}}"], wsl_distro=wsl_distro)
    return any(line.strip() == container for line in proc.stdout.splitlines())


def volume_exists(name: str, *, wsl_distro: str | None = None) -> bool:
    """Does a named volume exist on this daemon? Raises when Docker would not say.

    Asked by the CMaNGOS family's `db-password` stage before it writes a NEW
    generated password: a `db-data` volume that already exists was initialised
    with the password `.db_password` used to hold, and a fresh secret would lock
    the install out of its own database. So "no such volume" is a real answer
    (write the file), "it exists" is a refusal, and a daemon that cannot be
    asked is neither — the caller must refuse rather than guess, the same rule
    `_refuse_foreign_containers()` applies to container names.

    `_docker()` rather than `_run()` on purpose: `_run()` raises on any non-zero
    exit, and the absent answer IS a non-zero exit, so the branch this function
    exists for would be unreachable through it.

    The distro is not optional decoration. A WSL-resident install's volume lives
    on that distro's daemon; asked of Docker Desktop instead, `docker volume
    inspect` answers "no such volume" — the one answer that tells the caller it
    is safe to overwrite the password of a database that is sitting right there.
    The wrong-daemon bug and the destructive branch are the same branch.

    The substring is what tells the two failures apart, and they are otherwise
    identical: on a live daemon a missing volume is `exit 1, Error response from
    daemon: get <name>: no such volume`, while a CLI that cannot reach a daemon
    is *also* exit 1. Matching on the exit code would call the second one
    "absent" — so only the wording decides, and anything unrecognised refuses.

    Three unreachable wordings, all observed rather than remembered:

    - Windows, a `DOCKER_HOST` nothing is listening on:
      `error during connect: ... connectex: No connection could be made ...`
    - Linux, the daemon stopped — the commonest of the three:
      `failed to connect to the docker API at unix://...: connect: no such file
      or directory`
    - a `DOCKER_HOST` whose name does not resolve:
      `failed to connect to the docker API at tcp://...: lookup ...: no such host`

    Older Docker said `Cannot connect to the Docker daemon at unix://...` for
    the second; current Docker opens tcp and unix failures identically, so the
    prefix names neither the platform nor the transport.

    **Which is why the match is `no such volume` and not `no such`.** Two of the
    three above carry the shorter phrase, and shortening it — the obvious "be
    robust to wording changes" edit — turns a stopped Linux daemon into "safe to
    write a new password over a database you can still see".
    """
    proc = _docker(["volume", "inspect", "--format", "{{.Name}}", name], wsl_distro=wsl_distro)
    if proc.returncode == 0:
        return True
    if _cli_missing(proc):
        raise DockerCliMissingError(platform.DOCKER_CLI_MISSING_HELP)
    said = proc.stderr.strip()
    # `.lower()` is tolerance, not a captured requirement: every wording seen has
    # been lowercase. It is kept because dropping it can only turn an answer into
    # a refusal, and left untested because the only input that would exercise it
    # is one no daemon has printed (see `test_volume_exists_refuses_a_no_such...`).
    if "no such volume" in said.lower():
        return False
    # Same question `_run()` asks one line before its own raise, and for the same
    # reason: a deleted distro makes wsl.exe complain on STDOUT and exit
    # 0xFFFFFFFF, so this message was `docker volume inspect x_db-data exited
    # 4294967295: ` — nothing after the colon. `volume_exists` reads its own exit
    # codes rather than going through `_run()`, so it inherited none of that.
    problem = wsl.missing_distro_problem(wsl_distro, proc.returncode, proc.stdout)
    if problem is not None:
        raise DockerRefusal(problem)
    raise DockerCommandError(f"docker volume inspect {name} exited {proc.returncode}: {said}")


DATABASE_FILES = "/var/lib/mysql"
"""Where a MySQL or MariaDB image keeps its data files: both images, every family (T377)."""


def database_volume(
    spec: ContainerSpec, server_dir: Path, *, wsl_distro: str | None = None
) -> str | None:
    """The named volume compose mounts at the database service's `DATABASE_FILES`, or None.

    Asked of `compose config`, never built from a folder name (`project_volumes()`'s
    reason): compose prints each volume's full name under `volumes.<key>.name`, so
    an adopted install whose compose file names its volume `ac-database` is read
    as rightly as one this app wrote. None when compose could not be asked, the
    database service is not there, or its files are not in a named volume (a bind
    mount, say): the caller then knows nothing, and must say nothing.
    """
    proc = _docker(
        ["compose", "config", "--format", "json"],
        cwd=server_dir,
        timeout=_COMPOSE_CONFIG_TIMEOUT_SECONDS,
        wsl_distro=wsl_distro,
    )
    if proc.returncode != 0:
        logger.debug(f"compose config failed in {server_dir}: {proc.stderr.strip()}")
        return None
    try:
        parsed = json.loads(proc.stdout)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    services = parsed.get("services")
    service = services.get(spec.compose_services()[0]) if isinstance(services, dict) else None
    mounts = service.get("volumes") if isinstance(service, dict) else None
    volumes = parsed.get("volumes")
    if not isinstance(mounts, list) or not isinstance(volumes, dict):
        return None
    for mount in mounts:
        if not isinstance(mount, dict) or mount.get("type") != "volume":
            continue
        if str(mount.get("target", "")).rstrip("/") != DATABASE_FILES:
            continue
        key = mount.get("source")
        declared = volumes.get(key) if isinstance(key, str) else None
        name = declared.get("name") if isinstance(declared, dict) else None
        return name if isinstance(name, str) and name else None
    return None


def staged_up_argv(spec: ContainerSpec, *, force_recreate: bool = False) -> list[str]:
    """The argv of the one `compose up` this app runs, with and without the rebuild's force.

    One builder for both spellings, so the service selection and `--no-deps`
    cannot be respelled a second, wrong way — the discipline
    `build_staged()`'s docstring holds for the `-f` set, applied to the other
    command that must never select the one-shot import.

    `force_recreate` is the rebuild's, and only the rebuild's. Without it
    compose decides for itself whether a container needs replacing, and what it
    was measured to decide on (`start_staged()` below, Docker 29.1.3) is
    "a service whose CONFIGURATION changed". A rebuild changes neither the
    compose files nor the image TAG — `composegen.image_tag()` is derived from
    the folder — only the image the tag points at, and nothing in this
    repository records what compose does with that. So the rebuild asks for the
    replacement outright rather than hoping: the failure mode of hoping is an
    hour of correct compiler output followed by the old binary still running,
    which is the exact report this control exists to answer.
    """
    argv = ["compose", "up", "-d"]
    if force_recreate:
        argv.append("--force-recreate")
    return [*argv, "--no-deps", *spec.compose_services()]


def recreate_argv(spec: ContainerSpec) -> list[str]:
    """`staged_up_argv()` as the rebuild spells it — the name a test can assert on."""
    return staged_up_argv(spec, force_recreate=True)


def recreate_stop_argv(spec: ContainerSpec) -> list[str]:
    """What the rebuild stops before its recreate: the servers, not the database (T98).

    `compose up --force-recreate` replaces the database FIRST (dependency
    order) while the world is still running on it. Measured on yulon-ubuntu's
    WotLK install with 500 bots, 2026-09-24: before T98 the database went in
    under 4 s and the old worldserver logged `[2013] Lost connection` and
    `Waiting for 0 queries` -- its saves gone -- in a 19.6 s replace; with
    T98's database, which holds SIGTERM while a server is connected, the same
    command sat out the whole 240 s hold and then took the database from
    under the world anyway, 270.2 s. Stopping the servers first, with the
    grace every other stop here uses, lets the world drain onto a live
    database, and then the database has nobody to wait for.
    """
    database = spec.service_for(spec.db)
    servers = [service for service in spec.compose_services() if service != database]
    return ["compose", "stop", "-t", str(STOP_GRACE_SECONDS), *reversed(servers)]


@_a_lifecycle_command
def stop_servers_staged(
    spec: ContainerSpec,
    server_dir: Path,
    *,
    wsl_distro: str | None = None,
    control: StopControl | None = None,
    before_signal: Callable[[], None] | None = None,
) -> None:
    """Stop this install's servers and leave its database up: `recreate_stop_argv()`, waited for.

    The first half of `recreate_staged()`, on its own for the rebuild's
    rollback, which must stop the FAILED build's containers before it moves a
    single tag back (T158, round 3): a tag moved while a container still runs
    the image it named is a server whose tags and binary disagree.

    A world still loading is waited for before the stop
    (`wait_for_the_world_to_load()`); `before_signal` is called after that
    wait, after any save the world is asked for first (T410), and immediately
    before the world's own signal (`save_then_stop_the_world()`), so a caller
    can tell "nothing was signalled" from "something may have been". Compose's stop of services that
    are not running is a no-op, and so is the wait of a world that is down.
    """
    heard = wait_for_the_world_to_load(spec, control, wsl_distro=wsl_distro)
    # T384: the world saves before the grace below can reach it. `before_signal` is
    # called inside, right before the world's own signal (after any save it is asked
    # for first, which a give-up can still end with nothing sent).
    save_then_stop_the_world(
        spec, control, wsl_distro=wsl_distro, before_signal=before_signal, heard=heard
    )
    _run(recreate_stop_argv(spec), cwd=server_dir, wsl_distro=wsl_distro)


@_a_lifecycle_command
def recreate_staged(
    spec: ContainerSpec,
    server_dir: Path,
    *,
    wsl_distro: str | None = None,
    control: StopControl | None = None,
    before_signal: Callable[[], None] | None = None,
) -> bool:
    """Replace this install's long-running containers from the images on disk now.

    `start_staged()` with the force, under a name that says what the caller
    wants: after a rebuild the point is not "make sure it is up" — it very
    probably already is — but "stop running the binary that was there before".
    Everything else is that function's, including the check that the named
    services really are running afterwards.

    The named volumes are untouched: this replaces containers, not data. That
    is the same guarantee `start_staged()` already relies on every time compose
    recreates a service whose configuration changed.

    The servers are stopped first (`stop_servers_staged()`, whose argv is
    `recreate_stop_argv()`), for the reason given there -- and with its wait
    for a loading world, so the rebuild's replace is the check right before
    the signal, cancellable through `control` (T158). `before_signal` is its.
    """
    stop_servers_staged(
        spec, server_dir, wsl_distro=wsl_distro, control=control, before_signal=before_signal
    )
    return start_staged(spec, server_dir, wsl_distro=wsl_distro, force_recreate=True)


@_a_lifecycle_command
def start_staged(
    spec: ContainerSpec,
    server_dir: Path,
    *,
    wsl_distro: str | None = None,
    force_recreate: bool = False,
) -> bool:
    """Start this install's long-running services, and only those.

    `docker compose up -d` with no arguments starts every service that has no
    running container — including AzerothCore's one-shot `ac-db-import` and
    `ac-client-data-init`, which have already exited successfully. Re-running
    the import is what `dml-start.sh` warns about in as many words:

        # Use docker start (not compose up) so we do NOT re-trigger ac-db-import
        # or ac-client-data-init on every restart — that was killing the database.

    Naming the three services explicitly is the whole fix: compose cannot select
    a service that was not asked for, and `--no-deps` stops it pulling the
    import back in as a dependency of the servers. Measured against Docker
    29.1.3, this single command:

    - never runs the one-shot import — not on a plain restart, not when a
      compose file changed, and not when a container is missing;
    - recreates a service whose configuration changed, so an edited port or
      `AC_*` value actually takes effect;
    - recreates a container that no longer exists, without treating "missing
      container" as "never installed";
    - waits for `ac-database` to report healthy before starting the servers,
      because upstream's compose declares `condition: service_healthy` — and
      **fails closed** if it never does, rather than starting a worldserver
      against a dead database.

    That last point is why there is no health-polling here any more. `compose`
    owns the dependency graph; restating it in Python was how the ordering came
    to be documented but not delivered.

    An earlier version of this function tried to be clever: it checked whether
    the containers existed, compared compose config hashes, and started
    containers by name with `docker start`, falling back to a bare
    `compose up -d` whenever it was unsure. Every one of those fallbacks ran the
    destructive command, so the feature defeated itself exactly when it mattered
    — on a missing container or a changed setting. It also looked up containers
    by *global* name, so with two installs of the same game it could start the
    other one, silently. Addressing the project by its directory means the
    command never names a container, though `port_conflicts()` still does.

    What `--no-deps` costs, stated plainly: it prunes every `depends_on` edge
    whose target is outside the selected set, which drops `ac-db-import` (the
    point) and ALSO `ac-client-data-init`'s
    `condition: service_completed_successfully`. So an install interrupted
    part-way through the multi-GB client-data download leaves an incomplete
    volume that upstream would have finished and this will not. That is a
    narrow case with a loud symptom (the worldserver complains about missing
    DBC/map data), and the alternative — putting the init back in the service
    list — is only safe if its entrypoint is idempotent, which is unverified
    (review, 2026-08-22).

    Returns:
        True once every named service is actually running. `compose up` exiting
        0 is not on its own evidence of that: it is happy to report success for
        a container that started and died, and the caller would then sit out
        `wait_ready()`'s full timeout before hearing about it.

    Raises:
        DockerCommandError: compose failed, or a named service is not running
            once it returned.
    """
    argv = staged_up_argv(spec, force_recreate=force_recreate)
    logger.info(f"start_staged(): `{' '.join(argv)}` in {server_dir}")
    _run(argv, cwd=server_dir, wsl_distro=wsl_distro)
    listed = _status_safe(wsl_distro=wsl_distro)
    if listed is None:
        logger.warning("start_staged(): could not confirm what is running; taking compose's word")
        return True
    running = {line.strip() for line in listed}
    missing = [name for name in (spec.db, spec.auth, spec.world) if name not in running]
    if missing:
        raise DockerRefusal(
            f"Docker reported the start as done, but {', '.join(missing)} "
            f"{'is' if len(missing) == 1 else 'are'} not running. "
            f"{missing[0]}'s own log says why.",
            detail=logs_command(spec.service_for(missing[0]), server_dir),
        )
    return True


def _project_containers(project: str, *, wsl_distro: str | None = None) -> list[str] | None:
    """Every container compose stamped with `project`, running or exited.

    `container_exists()` cannot answer this: AzerothCore pins container names
    GLOBALLY (`ac-worldserver`, not `<project>-worldserver`), so a name search
    finds a second install's container and calls it ours. Filtering on the
    project label is the only way to ask about this install, and `-a` is what
    makes it about existence rather than about running.

    Returns None when Docker could not be asked, which callers must not read as
    "nothing is there".
    """
    proc = _docker(
        ["ps", "-a", "--filter", f"label={PROJECT_LABEL}={project}", "--format", "{{.Names}}"],
        wsl_distro=wsl_distro,
    )
    if proc.returncode != 0:
        logger.warning(f"could not list containers for project {project}: {proc.stderr.strip()}")
        return None
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def project_containers(project: str, *, wsl_distro: str | None = None) -> list[str] | None:
    """Which containers belong to a compose project. `None` if Docker could not be asked.

    The public form of `_project_containers()`, for the one caller outside this
    module that needs it: stopping a server that is holding our ports has to stop
    the whole project, not only the containers that publish those ports.
    """
    return _project_containers(project, wsl_distro=wsl_distro)


def running_census(spec: ContainerSpec, project: str, *, wsl_distro: str | None = None) -> Running:
    """Who owns the containers wearing this install's names. The public `_running()`.

    Made public for the same reason `project_containers()` was: one caller
    outside this module needs the answer this module already computes, and a
    second copy of it would be a second thing to keep in step. `purge.py` asks
    it twice — once to refuse an uninstall of a RUNNING server (`ours` non-empty,
    before any command is issued), and once for the ownership refusal the
    teardown path shares (`strangers`, `unreadable`).

    Raises the same way `_running()` does, and a caller must fail closed on
    both: `unreadable` is not proof of anything, and `strangers` means these
    containers are somebody else's.
    """
    return _running(spec, project, wsl_distro=wsl_distro)


def project_volumes(project: str, *, wsl_distro: str | None = None) -> list[str] | None:
    """Every named volume compose stamped with `project`. `None` if Docker would not say.

    Discovered, never computed. A volume name built out of a folder basename is
    the bug `guides/uninstall.sh:136` shipped — it hardcodes
    `wow-server-playerbots_ac-database`, so on a folder named anything else it
    removes nothing, and on the wrong machine it could remove somebody's
    database. Compose labels the volumes it creates exactly as it labels the
    containers, so the same filter answers both questions.

    `None` and not `[]` when the daemon could not be asked, on the same rule as
    `_project_containers()`: a caller must not read "Docker did not answer" as
    "there is nothing there", because the caller here goes on to delete a folder
    and forget the record.
    """
    proc = _docker(
        [
            "volume",
            "ls",
            "--filter",
            f"label={PROJECT_LABEL}={project}",
            "--format",
            "{{.Name}}",
        ],
        wsl_distro=wsl_distro,
    )
    if proc.returncode != 0:
        logger.warning(f"could not list volumes for project {project}: {proc.stderr.strip()}")
        return None
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def remove_volume(name: str, *, wsl_distro: str | None = None) -> None:
    """Delete one named volume, and confirm it is gone. The uninstall's own entry point.

    Deliberately NOT a flag on `remove_staged()`. That function's contract is
    that it never touches a volume and a test asserts its argv never grows a
    `-v`; it is offered on a RUNNING server under copy promising the characters
    are unaffected, so putting the data-destroying option one argv away from it
    would leave the safest action in the Controller and the most destructive one
    indistinguishable at a glance (`phase8-decisions.md`:135-143).

    `compose down -v` is the other shape this must not be. It removes BOTH
    volumes of an AzerothCore project — the 3.2 GB of client data and the
    characters — which makes "Keep my characters" unimplementable. The bash
    prior art does exactly that on all four of its WoW arms.

    The name must come from `project_volumes()`. This function does not check
    that, because it cannot: a volume name carries no reference to the folder it
    belongs to. What it does check is the ONE thing it can — that the volume is
    actually gone afterwards, rather than trusting an exit code, the same way
    `remove_staged()` re-censuses.

    Raises:
        DockerCommandError: The volume is still in use, or the removal could not
            be confirmed. "In use" is a failure and not a warning: an exited
            container still counts as a reference (the live `client-data` volume
            shows LINKS 2), so warning past it is how six gigabytes leak.
    """
    proc = _docker(["volume", "rm", name], wsl_distro=wsl_distro)
    if proc.returncode != 0:
        if _cli_missing(proc):
            raise DockerCliMissingError(platform.DOCKER_CLI_MISSING_HELP)
        said = proc.stderr.strip()
        if "no such volume" in said.lower():
            logger.info(f"volume {name} was already gone")
            return
        raise DockerCommandError(f"{name} could not be removed: {said}")
    if volume_exists(name, wsl_distro=wsl_distro):
        raise DockerRefusal(f"{name} is still there after being removed")
    logger.info(f"removed volume {name}")


def remove_image(ref: str, *, force: bool = False, wsl_distro: str | None = None) -> str:
    """Delete one image by its exact reference. Returns why it could not be, or `""`.

    One ref at a time, and never a compose flag. Measured on yulon-ubuntu
    2026-09-08: an AzerothCore project's images are the four
    `yulon.local/ac-wotlk-*:native-<install id>` builds plus `mysql:8.4`, which
    a second WotLK install also uses and TBC and Vanilla share `mariadb:11` the
    same way. `--rmi all` takes the shared one with it. `--rmi local` removes
    only images with no custom tag, and every one of ours is custom-tagged, so
    it removes none of them. That closes open item 1 of `phase8-decisions.md`,
    and the answer is neither of its two guesses: enumerate
    `composegen.built_image_refs()` and remove those.

    A refusal is a WARNING and not an error, which is the one place this module
    is deliberately soft: "in use by another container" means a second install
    or a running title is holding a layer, and that is not this uninstall's
    business. The Rust prior art reached the same conclusion from the other end
    (`destructive.rs:574-602`).

    `force` is `docker image rm -f`, and it exists for T79. A name whose image
    no OTHER name points at cannot be removed while any container references
    that image, *including a container that has exited*: the daemon answers
    `conflict: unable to delete <id> (must be forced) - container <id> is using
    its referenced image` (measured live 2026-09-09). The rebuild's
    recreate deliberately never selects the one-shot services
    (`staged_up_argv()`'s `--no-deps` over `spec.compose_services()`), so their
    EXITED containers still hold the pre-rebuild `db-import` and `client-data`
    images when the rollback names are let go -- and a refusal there leaves a
    `-rollback` tag on the daemon for ever, which is exactly what the round-3
    gate found. Forcing removes the NAME; the image it last pointed at becomes
    dangling and the exited container keeps running on the layers it already
    has until compose next recreates it from the live tag. `(cannot be forced)`
    -- a RUNNING container -- is a different answer, and this function passes
    both back for the caller to tell apart rather than retrying blind.
    """
    proc = _docker(["image", "rm", *(["-f"] if force else []), ref], wsl_distro=wsl_distro)
    if proc.returncode == 0:
        logger.info(f"removed image {ref}")
        return ""
    said = proc.stderr.strip()
    if "no such image" in said.lower():
        return ""
    logger.warning(f"could not remove image {ref}: {said}")
    return said


def tag_image(src: str, dst: str, *, wsl_distro: str | None = None) -> str:
    """Give the image at `src` the second name `dst`. Returns why it could not, or `""`.

    The rebuild's rollback (owner answer 2, 2026-09-08). A tag is a name and
    not a copy: it costs nothing until `docker compose build` writes a new
    image over `src`, at which point the old image lives on under `dst` and
    starts costing its own disk -- the price the owner accepted, for as long as
    the rebuild runs. `remove_image(dst)` is how it is let go.

    Docker's own words come back rather than a verdict, because what an
    untaggable image MEANS is the caller's question: for the rebuild, "no such
    image" on a tag the install claims to have built is a reason to refuse
    before compiling over it, not a warning.
    """
    proc = _docker(["image", "tag", src, dst], wsl_distro=wsl_distro)
    if proc.returncode == 0:
        logger.info(f"tagged {src} as {dst}")
        return ""
    said = proc.stderr.strip()
    logger.warning(f"could not tag {src} as {dst}: {said}")
    return said


LOG_TAIL_LINES = 2000
"""How many log ENTRIES of a container's log a snapshot keeps.

Entries, not lines, and the difference is measured rather than assumed: on
m910q on 2026-09-06 a `--tail 2000` of a CMaNGOS worldserver produced a file of
**3114 newline-terminated lines** and no carriage returns. The json-file driver
stores one entry per write, and a server that writes a multi-line message in one
call — a banner, an assertion block — spends one entry on several lines. The
byte cap below is what actually bounds the file; this bounds the read.

A bound rather than everything, because `docker logs` prints every line the
container has written across every restart, and a crash-looping worldserver
writes its whole startup again on each pass — the case a snapshot is most likely
to be taken on is exactly the case where the unbounded read is largest. Two
thousand lines is past an AzerothCore world server's startup banner and into the
run that preceded the stop, which is the part a person reads.
"""

_LOG_TAIL_TIMEOUT = 30.0
"""A snapshot's whole budget for one `docker logs`, because it runs before a stop.

`docker logs` against a wedged log driver does not return. Without a bound, the
evidence step would hold up the stop the user asked for — the one thing it must
never do.
"""


COMPOSE_PS_TIMEOUT = 30.0
"""`compose_container_id()`'s bound. Named so the support bundle (T93) can tell a
`None` that took the whole bound -- a daemon that did not answer -- from one that
came back at once."""


def compose_container_id(
    service: str, server_dir: Path, *, wsl_distro: str | None = None
) -> str | None:
    """The container id compose has for `service` in THIS project, or `None`.

    By project, never by name. Every AzerothCore-derived game pins its container
    names globally (`ac-worldserver`), so on a machine with two installs of one
    game a name lookup answers for whichever container happens to wear the name
    — the same class of bug `_project_containers()` exists to avoid, and the
    reason `start_staged()` addresses the project by its directory.

    `-a` because a stopped container still has a log, and the callers that want
    one are the stop, the remove and the uninstall.

    `None` rather than an exception for every failure: the caller is evidence
    collection, and evidence that cannot be collected must not stop the action
    it was collected for.
    """
    return compose_container_lookup(service, server_dir, wsl_distro=wsl_distro)[0]


def compose_container_lookup(
    service: str, server_dir: Path, *, wsl_distro: str | None = None
) -> tuple[str | None, str]:
    """`compose_container_id()`, with what Docker said when it did not answer (T211).

    `(id, "")` found; `(None, "")` compose knows no such container here; `(None,
    said)` the lookup itself failed, a timeout among them, and says nothing about
    whether the container is there. The log snapshot told the last two apart by
    neither, and said "could not find" a world server that was there while
    Docker was too busy stopping it to answer within the bound.
    """
    proc = _docker(
        ["compose", "ps", "-a", "-q", service],
        cwd=server_dir,
        wsl_distro=wsl_distro,
        timeout=COMPOSE_PS_TIMEOUT,
    )
    if proc.returncode != 0:
        said = proc.stderr.strip() or f"exit code {proc.returncode}"
        logger.warning(f"could not resolve {service} in {server_dir}: {said}")
        return None, said
    ids = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not ids:
        logger.warning(f"compose knows no container for {service} in {server_dir}")
        return None, ""
    return ids[0], ""


def log_tail(
    container: str,
    lines: int = LOG_TAIL_LINES,
    *,
    wsl_distro: str | None = None,
    timeout: float = _LOG_TAIL_TIMEOUT,
) -> str | None:
    """The last `lines` of a container's log, or `None` if it could not be read.

    The bounded counterpart to `_logs()`, which reads everything on purpose
    because readiness needs a marker printed once. Nothing that keeps a file
    wants that read: see `LOG_TAIL_LINES`.

    `None` and not `""`, because the two are different answers and the only
    caller keeps a file. This used to return `""` on a non-zero exit -- which
    is also the shape `_docker()` gives a TIMEOUT -- and `logsnap.capture()`
    wrote that empty string to disk and reported it as a saved snapshot: a
    zero-byte file, presented as evidence, on every stop whose log driver was
    wedged (retrospective audit, 2026-09-08). A log that is genuinely empty is
    `""`; a log that could not be read is nothing at all.

    `timeout` is the snapshot's 30 s unless a caller has a tighter budget: the
    support bundle reads three containers per install and gives each 20 s (T93).
    """
    proc = _docker(
        ["logs", "--tail", str(lines), container],
        wsl_distro=wsl_distro,
        timeout=timeout,
    )
    if proc.returncode != 0:
        logger.warning(f"could not read the logs of {container}: {proc.stderr.strip()}")
        return None
    return proc.stdout


def last_lines(
    container: str,
    lines: int,
    *,
    wsl_distro: str | None = None,
    timeout: float = 20.0,
    max_bytes: int | None = None,
) -> str | None:
    """`log_tail()`, keeping what the container wrote to stderr as well (T249).

    `docker logs` hands a container's stderr back on its OWN stderr, and
    `log_tail()` keeps stdout alone. A world server runs with a tty, so all it
    prints is on stdout; a container without one -- Tortoise's realmd, every
    database -- writes its errors to stderr, which is where a failure's reason
    is. The two come back as two pipes, so their interleaving is lost: the
    stderr lines follow stdout's, `lines` of each at most.

    `max_bytes` caps the answer, and each stream keeps its OWN end: cut
    together, a long stderr would push the end of stdout -- where a world
    server's crash reason is -- out of the cap (Codex T249 review). Every cut
    starts on a whole line.

    `None` when docker would not read it (no such container, a timeout, no
    CLI), never the daemon's refusal passed off as the container's own words.
    """
    proc = _docker(
        ["logs", "--tail", str(lines), container],
        wsl_distro=wsl_distro,
        timeout=timeout,
    )
    if proc.returncode != 0:
        logger.warning(f"could not read the logs of {container}: {proc.stderr.strip()}")
        return None
    out, err = proc.stdout, proc.stderr
    if max_bytes is not None and len((out + err).encode("utf-8")) > max_bytes:
        half = max_bytes // 2
        # Whatever one stream leaves of its half, the other may have.
        out_room = max(half, max_bytes - len(err.encode("utf-8")))
        out = _end_of(out, out_room)
        joint = 0 if not out or out.endswith("\n") else 1
        err = _end_of(err, max_bytes - len(out.encode("utf-8")) - joint)
    if not err:
        return out
    return (out if not out or out.endswith("\n") else out + "\n") + err


def _end_of(text: str, limit: int) -> str:
    """`text`'s last `limit` bytes (at least none), starting on a whole line.

    Unless the last line alone is longer than `limit`: then its end, so one huge
    line -- the newest, and perhaps the reason -- is cut rather than lost.
    """
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    tail = data[len(data) - max(limit, 0) :].decode("utf-8", errors="ignore")
    _, newline, whole = tail.partition("\n")
    return whole if newline and whole else tail


def server_version(*, wsl_distro: str | None = None, timeout: float = 20.0) -> str | None:
    """The Docker daemon's version, or `None` when it does not answer (T93's system-info.txt).

    A read, bounded, never raising: `_docker` turns a missing CLI and a timeout
    into a non-zero result, and both are "not reachable" to the one caller.
    """
    proc = _docker(
        ["version", "--format", "{{.Server.Version}}"], wsl_distro=wsl_distro, timeout=timeout
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


@_a_lifecycle_command
def remove_staged(
    spec: ContainerSpec,
    server_dir: Path,
    *,
    wsl_distro: str | None = None,
    control: StopControl | None = None,
) -> bool:
    """Stop this install and REMOVE its containers. Volumes are never touched.

    The deliberate teardown, for a project that needs recreating rather than
    restarting: a container wedged in a state `compose up` will not fix, a
    compose change that needs a fresh container, or clearing the way before an
    install is deleted by hand.

    WHAT THIS DOES NOT DO, because it is the whole reason the action is safe:
    it never removes a volume. The database lives in a named volume
    (`db-data:/var/lib/mysql` in an install this engine generated) and the
    client data in another, so `compose down` WITHOUT `-v` leaves every
    character exactly where it was. `-v` is the flag that would turn this into data loss, and a test
    asserts the argv never grows one.

    It also no longer costs the next start its database. An earlier version of
    this module warned that removing containers "forces the next start back onto
    `compose up -d`, and with it the one-shot database import" — that stopped
    being true when `start_staged()` began naming its three services explicitly.
    It selects `db auth world` and `--no-deps`, so compose cannot reach
    `ac-db-import` whether the containers are missing or not, and it recreates
    what is gone. The warning outlived the danger (2026-08-23).

    Ownership is proved the same way `stop_staged()` proves it, and refuses the
    same way, so the two actions cannot disagree about whose containers these
    are. `compose down` is project-scoped and would not touch a stranger anyway,
    but a census that cannot establish ownership means something is wrong with
    this install, and acting confidently on it is how the wrong server gets torn
    down.

    A loading world is waited for before `compose down` signals it, exactly as
    `stop_staged()` does and for its reason (`wait_for_the_world_to_load()`,
    T158); `control` is how the caller hears and steers it.

    Returns:
        True if this install had containers and they are now gone; False if
        there was nothing of it to remove. Not `compose down`'s exit code, which
        is 0 for a project that never existed.

    Raises:
        DockerCommandError: Ownership could not be established, or containers of
            this install are still there afterwards.
    """
    logger.debug(f"remove_staged() called: server_dir={server_dir}")
    project = install_project(spec, server_dir, wsl_distro=wsl_distro)
    if project is None:
        _refuse_without_an_identity(spec, server_dir, "Nothing was removed.", wsl_distro=wsl_distro)
        return False

    # The same look-before-touching census as the stop path, for the same
    # reason: a refusal has to happen before the command, not after it.
    running = _running(spec, project, wsl_distro=wsl_distro)
    if running.unreadable:
        raise DockerRefusal(
            f"Docker would not say which project owns {', '.join(running.unreadable)}, so this "
            f"install in {server_dir} cannot prove those containers are its own. Nothing was "
            "removed."
        )
    if running.strangers:
        raise _stranger_refusal(running.strangers, project, server_dir)

    before = _project_containers(project, wsl_distro=wsl_distro)
    if before is None:
        raise DockerRefusal(
            "could not ask Docker which containers this install has, so nothing was removed"
        )
    if not before:
        logger.info("remove_staged(): this install has no containers")
        return False

    # `down` and not `rm`: it walks the project's own depends_on graph in
    # reverse, so the servers close their database connections before the
    # database goes. `--remove-orphans` clears services dropped from the compose
    # file, which are exactly the leftovers that make a project need recreating.
    # No `-v`, ever.
    # The grace matters here as much as it does on the stop path, and it was
    # missed the first time round: this button is offered on a RUNNING server,
    # under copy that says the characters are not affected. At Docker's 10s
    # default that copy is false for a populated realm — the worldserver is
    # SIGKILLed mid-drain and the save queue is what is lost, not the containers
    # (review, 2026-08-23; the measurement is under `STOP_GRACE_SECONDS`).
    # And no grace helps a CMaNGOS world that is still loading: it drops the
    # SIGTERM, so it is waited for first (T158). It is always this install's
    # container here -- the census above refused anything else.
    if spec.world in before:
        heard = wait_for_the_world_to_load(spec, control, wsl_distro=wsl_distro)
        # T384: and saves, watched, before `down` and its fixed grace reach it.
        save_then_stop_the_world(spec, control, wsl_distro=wsl_distro, heard=heard)
    proc = _docker(
        ["compose", "down", "-t", str(STOP_GRACE_SECONDS), "--remove-orphans"],
        cwd=server_dir,
        wsl_distro=wsl_distro,
    )
    if proc.returncode != 0:
        logger.warning(f"compose down failed ({proc.stderr.strip()}); removing by name")

    after = _project_containers(project, wsl_distro=wsl_distro)
    if after is None:
        raise DockerRefusal(
            "the containers were asked to go, but Docker will no longer say what this install "
            "has, so the removal cannot be confirmed. Once Docker answers again, its list of "
            "containers shows whether any are left.",
            detail="Docker's list of every container, running or not: docker ps -a",
        )
    if after:
        # Only names the census already proved carry this project's label.
        logger.warning(f"compose down left {after}; removing by name")
        for name in after:
            # Stopped with the full grace BEFORE it is removed. `rm -f` is a
            # SIGKILL with no grace at all, so leaving it as the only fallback
            # left a hard-kill path reachable from the same button whose copy
            # promises the characters survive (review, 2026-08-23).
            _run_docker_stop(name, wsl_distro=wsl_distro)
            _docker(["rm", "-f", name], wsl_distro=wsl_distro)
        after = _project_containers(project, wsl_distro=wsl_distro) or []
    if after:
        raise DockerCommandError(f"still present after remove: {', '.join(after)}")

    logger.info(f"remove_staged(): removed {len(before)} container(s); volumes untouched")
    return True


# How long `status()` waits for `docker ps`. It is a local call, so a healthy
# daemon answers in milliseconds and a cold Docker Desktop in a few seconds -
# but an unhealthy one has no upper bound, and without a deadline the GUI's
# five-second poll wedges PERMANENTLY rather than slowly: `refresh_status()` sets
# `_status_pending` and clears it only from a callback, so a call that never
# returns makes every later poll return early at the guard, and the Server tab
# reads "status: unknown" until the app is restarted. Measured on yulon-win11
# 2026-08-28: `docker ps` hung 8+ minutes under memory pressure, and 125 seconds
# on an earlier run there. 30s is six polls - far above a slow honest answer and
# far below either of those. A timeout arrives as a non-zero CompletedProcess
# (see `_docker`), so it becomes DockerCommandError, so `_status_failed()` clears
# the flag and names the reason, and the next poll simply tries again.
STATUS_TIMEOUT_SECONDS = 30.0


STOP_GRACE_SECONDS = 300
"""Seconds a container gets between SIGTERM and the SIGKILL that follows it.

Since T384 the world server is no longer stopped by this number on any path that knows its
spec: `save_then_stop_the_world()` signals it first and waits for it while it is still
saving, and this grace reaches it only when that could not happen (a signal Docker refused, a
world that restarted). 300 s was not enough on 2026-10-05: a save of 2294 queued characters on
a slow disk was SIGKILLed at its end (exit 137). It still stops everything else.

Docker's own default is 10, and 10 was measured killing a live save: a plain
`docker compose stop` against the populated worldserver on yulon-ubuntu came
back with exit code 137 (2026-08-23).

Measured on that box the same day — AzerothCore + playerbots, 1980 characters
online — two shutdowns run under a grace so long it could not bind
(`docker stop --timeout 900`) took **90.7s** and **73.4s**, both exit 0. A
third, run through `stop_staged()` itself with this constant in force, stopped
the whole project in **58.3s** with all three containers exit 0 and none of
them removed. So the same server, at the same population, varies by more than
half a minute between runs; the value has to cover the bad run.

Almost all of it is one phase. After `Halting process...` and `Logging out all
bots...` the worldserver logs `Closing down DatabasePool 'acore_characters'.
Waiting for 7662 queries to finish...` and then spends 86s, 69s and 52s in the
three samples draining that character save queue — 7400-7700 queued saves at
90-145 a second. The other two containers are nowhere near the constraint:
ac-authserver stopped in 0.22s and ac-database in 1.4s.

300 is therefore about 3.3x the worst thing seen. The margin is deliberately
generous and deliberately asymmetric. The queue depth scales with population
and drains at whatever the disk gives, so a larger realm or a slower disk moves
the number — while the only cost of an over-long grace is that a genuinely hung
server takes longer to give up, and the cost of being 30s short is a player's
characters rolled back to their last save. It happens to agree with the
`stop_grace_period: 5m` the earlier Rust launcher wrote into its generated
compose file (`pyplan/rust-prior-art.md` §2); that is now a confirmed number
rather than an inherited one.

Every number above is AzerothCore's. The CMaNGOS worlds were measured on
2026-09-27 (Vanilla, 500 bots online, a Linux gate box, T158): a loaded
mangosd stopped on SIGTERM in **22.2s** (exit 0) and on SIGINT in 21.6s, every
bot logged out, so 300 covers them many times over. What no grace covers is a
CMaNGOS world signalled while it is still LOADING: it has no SIGTERM handler
yet and, as PID 1, drops the signal, so it loads, runs, and is SIGKILLed when
this grace ends (280.5s, exit 137). That is not fixed here but before the
signal is sent, by `wait_for_the_world_to_load()` (T158).

This is the CLI grace on the stop path. The compose `stop_grace_period` key is
the same 300 seconds, and the install engine now writes it: see
`catalog/installers/wow-wotlk/native/base.yml.tmpl`, whose comment points back
at this constant. The two are meant to agree, and nothing enforces that they do
— they are read by different tools in different processes, so the only thing
holding them together is that both cite the same three measured shutdowns.
"""

STOP_PROCESS_DEADLINE_SECONDS = STOP_GRACE_SECONDS + 60
"""How long the corrections press's `docker stop` may take before it is given up on (T159).

The grace and then a minute: docker sends SIGKILL when the grace ends, and a
killed container is gone in seconds, so a stop still running a minute later is
a daemon that is not answering rather than a server still saving. Longer than
the grace by construction, so it can never cut a save short that the grace
would have allowed. A command past it is reported as a stop whose outcome is
unknown, never as one that failed or one that worked.
"""


DatabaseImport = Literal["absent", "partial", "imported", "populated", "unreadable"]
"""What a probe found in the databases the one-shot import is supposed to fill.

* `absent` — nothing of the schema set is there. Never imported.
* `partial` — some of it is there and some is not. An import that stopped.
* `imported` — all of it is there, and nobody has played on it yet.
* `populated` — there are accounts or characters. Possibly somebody's server.
* `unreadable` — the database could not be asked, so nothing is established.

The line that matters is between `populated` and everything else: it is drawn
on player data, not on completeness, because player data is the thing whose loss
cannot be undone. A half-imported database that somehow holds characters is
`populated` and is refused, even though the import really did stop half-way.

`populated` does NOT mean "somebody played here", and calling it that was
wrong until a live import said so (yulon-ubuntu, 2026-08-23). The one-shot
applies every module's `data/sql/db-auth` and `db-characters` updates as well
as AzerothCore's own, so a module is free to seed rows: a first-ever import of
an install carrying mod-city-bots came out of the box with 400 accounts and 400
characters, none of them a person's. The state is still refused, because
nothing here can tell a seeded row from a made one and the fail-closed answer
is the only safe one — but a caller must read it as "there is data that a
re-import would destroy", not as "this server has been played on".
"""


@dataclass(frozen=True)
class ImportState:
    """One probe's answer, with the sentence that produced it.

    `detail` is carried rather than recomputed by the caller because the probe
    is the only thing that knows *why* — which schema was missing, how many
    accounts were found — and a refusal that cannot say why is a refusal the
    user has no way to act on.
    """

    state: DatabaseImport
    detail: str = ""
    complete: bool = False
    """Whether every schema the import is supposed to fill actually has tables.

    Separate from `state` because the two answer different questions and only
    one of them is about danger. `populated` short-circuits the moment a single
    row exists, precisely so a database with player data in it is refused before
    anything is written — which means it says nothing at all about whether the
    schemas are finished. That is fine for the refusal and useless for the
    post-check, which needs to know whether the one-shot did its job.

    A review found the hole this closes: an import that applies a module's
    `db-auth` updates (mod-city-bots seeds 400 accounts) and then dies on the
    world schema leaves `populated` with `acore_world` empty. Reading only the
    state, the action reported that as a finished repair, hid its own button,
    and left the user with a broken server and a success message.

    Defaults False so a probe that does not compute it cannot claim completeness
    by omission.
    """

    @property
    def repairable(self) -> bool:
        """True only where re-running the import can put something right.

        `partial` is here only because `repair_import()` can be handed a
        `reset` that drops the half-written schemas before the one-shot runs.
        Without one it must not be, and a live gate is why. Re-running
        `ac-db-import` over a schema that already exists does NOT finish it:
        AzerothCore skips the base dump for a database that is already there,
        creates its `updates` bookkeeping, and seeds it with every known SQL
        file marked as applied. Measured on yulon-ubuntu, 2026-08-23 — an import
        killed 19 seconds in left `acore_world` with 3 tables of 316, and
        re-running the one-shot took it to 5 tables and **2671 rows in
        `acore_world.updates`**, after which no run would ever apply those files
        again. The action did not merely fail to repair the state it was built
        for, it destroyed the only route out of it. `repair_import()` still
        refuses `partial` outright when no `reset` is supplied, which is what
        makes this property safe to widen.

        Deliberately false for `unreadable` too. An unanswerable database is not
        an empty one — the same fail-closed rule `_refuse_without_an_identity()`
        applies to ownership — and offering a destructive button on the strength
        of a question nobody answered is how it gets pressed by accident.
        """
        return self.state in ("absent", "partial")


ResetUnfinished = Callable[[], tuple[str, ...]]
"""Drops the schemas an interrupted import left half-written; returns their names.

A second seam rather than a wider `ImportProbe`, for the same reason the probe
is read-only: this module must not know that a schema is called `acore_world`
(style-guide §3), and the one thing on this path that destroys data should be
the one thing whose type says it writes. The per-game module owns both.

Optional. Given none, `repair_import()` refuses a `partial` database rather than
making it worse — see `ImportState.repairable`.
"""

ImportProbe = Callable[[], ImportState]
"""Asks the install's databases what state they are in.

A seam, because this module must not know what a schema is called or which
table holds an account (style-guide §3) — those are per-game facts, and the
per-game module owns them. See `controller_wow_wotlk/repair.py`.
"""


OutputSink = Callable[[str], None]
"""Where a long-running docker command's output goes, one line at a time.

Called on whatever thread is running the command — which for every caller in
this app is a worker thread, never the GUI thread. A sink that writes into a
widget directly is therefore the bug this seam exists to make avoidable, not
the use it exists for: the UI hands in something that can cross a thread
boundary (`ui/widgets/job.py`'s `LineRelay`), and `run_attached()` never learns
what happens at the far end.
"""


def repair_import(
    spec: ContainerSpec,
    server_dir: Path,
    probe: ImportProbe,
    *,
    reset: ResetUnfinished | None = None,
    output: OutputSink | None = None,
    db_timeout: float = _DB_HEALTHY_TIMEOUT_SECONDS,
    wsl_distro: str | None = None,
) -> bool:
    """Re-run this install's one-shot database import. For a BROKEN install only.

    The installer is the only thing that runs the import on a healthy path, and
    `start_staged()` exists precisely so an ordinary Start can never reach it.
    This is the repair for the one state that leaves behind: an install
    interrupted *after* its containers were created but *before* the import
    finished, whose Start now brings a worldserver up against empty schemas and
    fails in a way that explains nothing.

    It refuses far more often than it acts, and the refusals are the feature:

    * no `spec.import_service` — this game never said which service imports, and
      guessing a service name is guessing which container gets run;
    * ownership cannot be established, or containers wearing our names belong to
      another compose project. Identical to `remove_staged()`/`stop_staged()`, so
      the three cannot disagree about whose install this is;
    * an authserver or worldserver of this install is running. A live worldserver
      holds character state in memory, and the import writes underneath it;
    * the probe says the database holds player data. **This is the refusal that
      matters.** Re-importing over a populated database destroys characters, and
      it is not offered a second time for a user who asks twice — the way back
      from a bad database is Restore, which exists, and which was live-gated
      against a real 386 MB backup. On an install whose modules seed accounts
      this is also the refusal a *finished* repair leaves behind — pressing
      again lands here rather than on "already imported", which is a worse
      sentence for that case and still the right answer, since nothing can
      prove the rows are not a person's (see `DatabaseImport`);
    * the probe says the import already completed, or could not be asked at all.

    Two commands, both naming their service explicitly:

    1. `compose up -d --no-deps <db>`, only when the database is not already
       running. The import has to have somewhere to write, and the probe has to
       have something to ask — and a user cannot reach "database up, servers
       down" with the buttons this app has, since Start starts all three and
       Stop stops all three. Naming the database by hand is narrower than
       dropping `--no-deps` and letting compose's dependency graph decide what
       else comes up, which this repo has never measured.
    2. `compose up --no-deps <import_service>`, attached. `--no-deps` is what
       makes attached mode terminate: the one-shot is then the only container
       compose brings up, so `up` returns when it exits. Without it, `up` would
       also attach to the database and wait for a container that never stops.

    The exit code of that second command is not the answer, for the same reason
    `remove_staged()` does not believe `compose down`'s: the probe is run again
    afterwards, and only a database that is no longer repairable counts.

    **Live-gated on yulon-ubuntu, 2026-08-23**, against a copy of a real
    AzerothCore + playerbots + city-bots install on a brand-new empty volume,
    Docker 29.1.3. Three things this docstring had asserted without a daemon:

    * *Attached `up` terminates.* It does, and so does the detached database
      start. One whole call took 209.0s against a one-shot container that ran
      208.0s, so `up` came back within about a second of the import exiting.
      The database half: 23s from `compose up -d --no-deps <db>` to healthy on
      a brand-new volume, and 7.1s start-to-refusal on an existing container.
      "A full import takes several minutes", which is what the button says, is
      the right order of magnitude — 215s and 208s for two full imports.
    * *`compose up` re-runs a one-shot whose container already exists and has
      exited.* It does, with no `--force-recreate`. The same `rt-ac-db-import`
      container went `StartedAt 16:59:10 → 17:05:06`, `FinishedAt 17:02:45 →
      17:08:34`, exit 0 both times, and the second run refilled three schemas
      that had been `DROP DATABASE`d in between.
    * *A finished import leaves `acore_auth.account` empty.* **False**, and
      this is what the post-check below had to change for — see the comment
      there.

    `output`, when given, receives each line of the import's STDOUT as it prints
    it. Not every line: `runner.stream()` withholds stderr until the child has
    exited and then yields it in one block, so anything the one-shot writes
    there — compose's own `Container ... Created/Started` progress among it —
    arrives at the end rather than live. That is the same trade `follow_logs()`
    makes and it is what keeps a full pipe from deadlocking the child, but a
    caller putting these lines on screen should not promise the user more than
    it delivers (review, 2026-08-23). What is guaranteed is:
    see `run_attached()`, which is what turned that second command from a
    10-30 minute silence into something a caller can show. It is called on
    whatever thread is running this, so a UI sink has to be one that can cross
    threads. Passing nothing changes nothing except that the lines are dropped
    instead of forwarded; they are retained and reported either way.

    **This cannot be cancelled, deliberately.** There is no cancel token here
    and the UI offers no Stop, because the only way to abandon a running
    `compose up` is to terminate it — which stops `ac-db-import` part-way
    through writing schemas. That is a recoverable state (the probe reads it as
    `partial` and this action is offered again), but it is not one to hand a
    user a button for while the alternative is waiting.

    Returns:
        True once the probe says the import finished — every schema filled,
        whether or not the run also seeded rows of its own.

    Raises:
        DockerCommandError: any of the refusals above, the database never became
            healthy, or the import ran and the databases still read as `absent`,
            `partial` or `unreadable`.
    """
    logger.debug(f"repair_import() called: server_dir={server_dir}")
    service = spec.import_service
    if not service:
        raise DockerRefusal(
            "this game does not say which compose service imports its databases, so there is "
            "nothing to re-run. Nothing was changed."
        )

    project = install_project(spec, server_dir, wsl_distro=wsl_distro)
    if project is None:
        _refuse_without_an_identity(
            spec, server_dir, "The import was not re-run.", wsl_distro=wsl_distro
        )
        raise DockerRefusal(
            f"the install in {server_dir} cannot say which compose project it is — its compose "
            f"files are unreadable and no {PROJECT_NAME_VAR} is pinned — so the import was not "
            "re-run. Running it against the wrong project would overwrite the wrong database."
        )

    running = _running(spec, project, wsl_distro=wsl_distro)
    if running.unreadable:
        raise DockerRefusal(
            f"Docker would not say which project owns {', '.join(running.unreadable)}, so this "
            f"install in {server_dir} cannot prove those containers are its own. The import was "
            "not re-run."
        )
    if running.strangers:
        raise _stranger_refusal(running.strangers, project, server_dir)
    servers = [name for name in (spec.world, spec.auth) if name in running.ours]
    if servers:
        verb = "is" if len(servers) == 1 else "are"
        raise DockerRefusal(
            f"{', '.join(servers)} {verb} running. The import rewrites the databases underneath "
            "them, and a running worldserver holds characters in memory and saves them back over "
            "whatever it finds. Press Stop first, then try again."
        )

    # T539 (re-review of 7312223b): Repair is offered in exactly the half-written state
    # an orphaned importer leaves -- Yu'lon closed mid-import, and Docker Desktop keeps
    # the container -- so that importer is ended before anything else: before the
    # database it writes to is started (Codex review), and before it is read.
    left = end_one_shot(service, server_dir, wsl_distro=wsl_distro)
    if left is not None:
        raise DockerRefusal(importer_left_sentence(left, "the import was not re-run"))
    start_database(
        spec, server_dir, timeout=db_timeout, because="nothing was imported", wsl_distro=wsl_distro
    )

    before = probe()
    logger.info(f"repair_import(): the databases read as {before.state} — {before.detail}")
    if before.state == "populated":
        raise DockerRefusal(
            f"this install's databases hold player data ({before.detail}). Re-running the import "
            "would overwrite it, so it was not run. If the database is damaged, restore the last "
            "backup from the Maintenance tab instead — that is the path that keeps characters."
        )
    if before.state == "imported":
        raise DockerRefusal(
            f"the import has already completed ({before.detail}), so there is nothing to repair. "
            "If the server still will not start, its logs are where the reason is."
        )
    if before.state == "partial":
        # Re-running the one-shot over a half-written schema is not a no-op, it
        # is destructive — see `ImportState.repairable` for the measurement. The
        # only thing that makes it work is handing the importer an EMPTY schema,
        # which is what `reset` does; without one this refuses rather than
        # leaving the install permanently unimportable.
        if reset is None:
            raise DockerRefusal(
                f"this install's import stopped part-way ({before.detail}), and re-running it "
                "cannot finish the job: AzerothCore skips the base data for a database that "
                "already exists, so the import would record every remaining file as applied and "
                "leave the schema permanently unfinished. Nothing was run. Remove this install's "
                "containers and database volume, then install again."
            )
        logger.warning(f"repair_import(): clearing the half-written schemas ({before.detail})")
        try:
            dropped = reset()
        except Exception as exc:
            # Deliberately broad, and re-raised as this module's own error. The
            # seam belongs to the per-game package and may raise its own types;
            # what a caller here has contracted for is `DockerCommandError`, and
            # a failure to clear must not reach the user as a failed import.
            raise DockerCommandError(
                "the half-written databases could not be cleared, so the import was not re-run "
                f"and nothing else was changed: {exc}"
            ) from exc
        if not dropped:
            raise DockerRefusal(
                f"the databases read as unfinished ({before.detail}), but nothing was found to "
                "clear, so the import was not re-run. Nothing was changed."
            )
        logger.warning(f"repair_import(): dropped {', '.join(dropped)}; re-running the import")
    if not before.repairable:
        raise DockerRefusal(
            f"the databases could not be asked what state they are in ({before.detail}), so the "
            "import was not re-run. Nothing can be established about them either way."
        )

    logger.warning(f"repair_import(): `compose up --no-deps {service}` in {server_dir}")
    run = run_one_shot(service, server_dir, wsl_distro=wsl_distro, sink=output)
    verify_import(probe, service, server_dir, run)
    return True


def start_database(
    spec: ContainerSpec,
    server_dir: Path,
    *,
    timeout: float = _DB_HEALTHY_TIMEOUT_SECONDS,
    because: str = "nothing was run",
    wsl_distro: str | None = None,
) -> bool:
    """Start this install's database alone and wait for it to report healthy.

    Shared by `repair_import()` and by the native install engine's `start-db`
    stage (roadmap 6.2) so the two cannot drift, and byte-identical to the argv
    live-gated on yulon-ubuntu 2026-08-23 (23s from this call to healthy on a
    brand-new volume; 7.1s when the container already existed).

    Both callers need it for the same reason and it is not optional for either:
    the import one-shot runs with `--no-deps`, so compose will NOT bring up the
    database the `depends_on: service_healthy` edge names, and the import PROBE
    is a `docker exec ac-database mysql …` that answers `unreadable` when there
    is no such container. An install that skipped this refused itself at the
    import stage after a multi-hour build (review, 2026-08-23).

    Does nothing when the database container is already running, which is the
    ordinary case on a resume.

    `because` completes the sentence a timeout raises with, so a repair and an
    install each say what was not done.

    Returns:
        True if it had to start the container, False if it was already up.
        Added for `apply.Applier`'s `start_database` seam (T7): that caller puts
        a line in the user's report, and a report that claims a start on every
        ordinary install is worse than one that says nothing. The two older
        callers ignore it.

    Raises:
        DockerCommandError: compose would not start it, or it never became
            healthy inside `timeout`.
    """
    if spec.db in set(status(wsl_distro=wsl_distro)):
        return False
    # Started rather than demanded, because Stop takes the database down with
    # everything else — a user who followed the repair refusals would otherwise
    # have no way back to a state that action accepts.
    logger.info(f"start_database(): starting {spec.db} alone")
    # `compose_services()[0]`, not `spec.db`: compose takes SERVICE names and
    # `spec.db` is a CONTAINER name. They are equal for AzerothCore and
    # `ContainerSpec` exists precisely so a game whose compose file disagrees
    # can say so — reaching past it here would have made that promise false
    # for the first such game (review, 2026-08-23).
    _run(
        ["compose", "up", "-d", "--no-deps", spec.compose_services()[0]],
        cwd=server_dir,
        wsl_distro=wsl_distro,
    )
    if not wait_db_healthy(spec.db, timeout=timeout, wsl_distro=wsl_distro):
        raise DockerRefusal(
            f"{spec.db} did not report healthy within {timeout:.0f}s, so {because}. "
            "The database's own log says why.",
            detail=logs_command(spec.service_for(spec.db), server_dir),
        )
    return True


ALLOWED_MODULES_VAR = "AC_UPDATES_ALLOWED_MODULES"
"""The environment variable AzerothCore reads `Updates.AllowedModules` from.

Its config layer takes `AC_` + the option name upper-cased with dots as
underscores, and it says so on the way past: running the real `ac-db-import`
image with this set printed `Configuration field Updates.AllowedModules was
overridden with environment variable` (yulon-ubuntu, 2026-09-07)."""

ALL_MODULES = "all"
"""Upstream's own default for `Updates.AllowedModules`, and NOT "the modules on disk".

`src/tools/dbimport/Main.cpp:114-118` reads three different meanings out of
this one option and only the middle one is obvious:

* `""` — build a `DatabaseLoader` with **no module list at all**. Measured on
  the real image 2026-09-07 (bogus database hosts, so it printed its decision
  and then failed to connect): `Loading modules: none`. An empty value is a
  third meaning, not an absence, which is why `allowed_modules()` never returns
  one.
* `"all"` — pass `AC_MODULES_LIST`, the macro `modules/CMakeLists.txt:371`
  bakes from a glob **at CMake time**. On the yulon-ubuntu stack that macro
  holds exactly `mod-playerbots`, whatever is in `modules/` today.
* anything else — pass that comma-separated list through to `UpdateFetcher`,
  which joins each name onto `<source>/modules/<name>/data/sql/` and skips the
  ones that are not directories (`UpdateFetcher.cpp:159-186`). A name that is
  not on disk therefore costs nothing."""


MODULES_DIR_NAME = "modules"
"""The folder under an install that holds its modules, one per directory.

Named once because two things here have to agree about it: `allowed_modules()`
reads it off the host, and `importer_sees_modules()` asks whether the container
that applies SQL has it mounted. A check that looked for a different folder than
the one that was listed would answer confidently about nothing."""


def importer_sees_modules(
    service: str, server_dir: Path, *, wsl_distro: str | None = None
) -> bool | None:
    """Is the install's `modules/` folder mounted into the service that imports SQL?

    `None` means the question could not be asked (no compose config, no such
    service) — not `False`, because "cannot tell" and "proven absent" lead
    somewhere different and only one of them is worth refusing on.

    **This is the FACT 6 question, and on the install the real user has the
    answer is no.** `tests/data/wotlk-compose-config-script.json` is `docker
    compose config` read off a server the DML bash installer built (Fedora,
    2026-08-31, project `wow-server-playerbots`): its `ac-db-import` mounts
    `./env/dist/etc` and `./env/dist/logs` and nothing else, while `./modules`
    is bound into `ac-worldserver` alone. The importer on that install therefore
    resolves every allowed module name against the modules baked into the
    IMAGE — `UpdateFetcher.cpp:159-186` joins `<source>/modules/<name>/data/sql`
    and skips what is not a directory — so a module cloned onto the host
    afterwards is invisible to it no matter what `AC_UPDATES_ALLOWED_MODULES`
    says. The compose file this app generates does mount it (see
    `catalog/installers/wow-wotlk/native/base.yml.tmpl`), and nothing in this
    app rewrites a compose file it did not write: `composegen.write_plan()`
    refuses an unmarked file, and `catalog_view.attach_existing()` only
    remembers the folder. So this cannot be fixed from here — it can only be
    said, which is what the caller does with the answer.

    Asked of `compose config` rather than of the file, so an override that adds
    the mount counts and a file this app cannot parse does not have to be.
    """
    proc = _docker(
        ["compose", "config", "--format", "json"],
        cwd=server_dir,
        timeout=_COMPOSE_CONFIG_TIMEOUT_SECONDS,
        wsl_distro=wsl_distro,
    )
    if proc.returncode != 0:
        logger.debug(f"compose config failed in {server_dir}: {proc.stderr.strip()}")
        return None
    try:
        parsed = json.loads(proc.stdout)
    except ValueError:
        logger.debug("compose config did not return JSON")
        return None
    services = parsed.get("services") if isinstance(parsed, dict) else None
    entry = services.get(service) if isinstance(services, dict) else None
    if not isinstance(entry, dict):
        return None
    mounts = entry.get("volumes")
    if not isinstance(mounts, list):
        return None
    for mount in mounts:
        source = mount.get("source") if isinstance(mount, dict) else None
        # By name, not by target: the container-side path is a per-game fact
        # this module must not know (style-guide §3), while the host-side folder
        # is the one `allowed_modules()` just listed.
        if isinstance(source, str) and PurePosixPath(source.replace("\\", "/")).name == (
            MODULES_DIR_NAME
        ):
            return True
    return False


def allowed_modules(server_dir: Path) -> str:
    """Which modules this install's importer should be allowed to update, from DISK.

    The root cause this exists for, measured twice on yulon-ubuntu 2026-09-07
    against the same files and the same database: `compose up --no-deps
    ac-db-import` logged `Loading modules: all`, applied nothing and left
    `acore_world.updates` at 2967; the same container plus
    `-e AC_UPDATES_ALLOWED_MODULES=mod-aoe-loot` logged `>> Applying update
    aoe_loot_module_string.sql`, `>> Applied 1 query`, and moved it to 2968.
    The difference is not the files, which were identical — it is that `"all"`
    means the COMPILED list, so every module installed after the image was
    built is invisible to the importer and its SQL is never applied. Nothing in
    Yu'lon set this option anywhere before 8.x.

    Directories only, sorted, comma-joined. The live install's `modules/` also
    holds `CMakeLists.txt`, `create_module.sh`, `how_to_make_a_module.md`,
    `ModulesLoader.cpp.in.cmake`, `ModulesPCH.h` and `ModulesScriptLoader.h`
    beside `mod-playerbots` (read off yulon-ubuntu, 2026-09-07), and a
    dot-directory is `.git`, never a module.

    **A folder with nothing in it answers `"all"`, never `""`** — see
    `ALL_MODULES` for what those two mean to upstream, which is not the same
    thing. `"all"` is what the install would have done with no variable set at
    all, so a modules folder that is empty, missing or unreadable leaves the
    behaviour exactly as it was found; `""` would switch module updates off for
    an install that was getting them. The two are equivalent in effect for a
    genuinely empty folder — the compiled names have no directory, so
    `UpdateFetcher` skips them — and only one of them is safe when the folder
    could not be read.
    """
    names = module_dir_names(server_dir)
    if names is None:
        return ALL_MODULES
    return ",".join(names) if names else ALL_MODULES


def module_dir_names(server_dir: Path) -> list[str] | None:
    """The module folder names in this install, sorted. `None` means unreadable.

    The listing `allowed_modules()` and `apply.module_sql_plan()` share. They do
    NOT share the empty case: an unreadable folder and an empty one are the same
    answer to the list on screen (nothing to mark) and two different answers to
    the importer, where `ALL_MODULES` is upstream's default and `""` would switch
    module updates off. Hence `None` rather than `[]` here, so each caller
    decides for itself.

    Public since T78, and the name is the whole of what changed: the second
    caller is in `apply.py`, which has to tell "could not read the folder" from
    "there is nothing in it" before it decides which modules the core updater may
    be given, and a private helper reached across a module boundary is a worse
    answer than a listing with two readers.
    """
    modules = server_dir / MODULES_DIR_NAME
    try:
        return sorted(
            entry.name
            for entry in modules.iterdir()
            if entry.is_dir() and not entry.name.startswith(".")
        )
    except OSError as exc:
        logger.warning(f"could not list {modules}: {exc}")
        return None


def clone_names(folder: Path) -> frozenset[str]:
    """The directory names directly inside `folder`, as a set. Unreadable is empty (T41).

    What the Modules tab marks its rows with, one family's clone directory at a
    time. Cheap on purpose — directory names, no git, no daemon — because it
    runs on every `reload_modules()`, where `module_updates()` costs a `git
    fetch` per checkout and is a button.

    An unreadable or missing folder answers the empty set: the list then marks
    nothing, which is what it did before this existed, rather than claiming
    every module is missing. That is the opposite of `allowed_modules()`, which
    must answer `all` for the same folder — see `module_dir_names()`.

    Takes the folder rather than the server directory because the four manifest
    families do NOT share one: `apply.CLONE_DIRS` puts a module in `modules/`,
    an ale and a keg in `ale_scripts/` and a mod in `sql_scripts/clones/`.
    Reading `modules/` for all four marked an ale installed because a module of
    the same id was, and never marked a real ale at all (review, 2026-09-12).
    """
    try:
        return frozenset(
            entry.name
            for entry in folder.iterdir()
            if entry.is_dir() and not entry.name.startswith(".")
        )
    except OSError as exc:
        logger.info(f"could not list {folder}, so nothing there is marked installed: {exc}")
        return frozenset()


def run_one_shot(
    service: str,
    server_dir: Path,
    *,
    allowed_modules: str | None = None,
    wsl_distro: str | None = None,
    sink: OutputSink | None = None,
    cancel: threading.Event | None = None,
) -> AttachedRun:
    """Run one compose one-shot service attached, and return what it left behind.

    Byte-identical argv to the version live-gated against a real AzerothCore
    import on yulon-ubuntu (2026-08-23) — `--no-deps` is what makes an attached
    `up` terminate, because the one-shot is then the only container compose
    brings up. It is shared by the repair button and by the native install
    engine's `import`/`client-data` stages (roadmap 6.2) so the two can never
    drift into running different commands for the same job.

    `allowed_modules`, when given, switches the argv from `compose up` to
    `compose run --rm --no-deps -e AC_UPDATES_ALLOWED_MODULES=<list>`, and the
    reason it is a different command is that **`compose up` takes no `-e`**. The
    generated compose file interpolates the variable
    (`${AC_UPDATES_ALLOWED_MODULES:-all}`), so for an install this engine wrote
    a value in `.env` would be enough — but an install adopted from the DML bash
    launcher has a compose file we never wrote and no such key to interpolate,
    and that is the install the real user is running. Putting the value in argv
    is the only route that reaches both.

    Measured against the real `ac-db-import` image on yulon-ubuntu 2026-09-07,
    with bogus database hosts so nothing was written: the container logged
    `Configuration field Updates.AllowedModules was overridden with environment
    variable` and `Loading modules: mod-lane-c-probe,mod-second`, the pinned
    `container_name` did not stop `run` from starting it, and `--rm` left
    nothing behind (`docker ps -a` named no `*-run-*` container afterwards).

    What that mode costs, stated because a caller has to say it to a user:
    `--rm` deletes the container, so `docker compose logs <service>` afterwards
    shows nothing. The run's output is what there is — it arrives at `sink` and
    a bounded tail comes back on the result.

    The exit status is returned rather than raised for the reason
    `repair_import()` records: a one-shot that failed part-way and one that
    failed having done nothing exit alike, and only a probe of the result can
    tell them apart.
    """
    argv = (
        ["compose", "up", "--no-deps", service]
        if allowed_modules is None
        else [
            "compose",
            "run",
            "--rm",
            "--no-deps",
            "-e",
            f"{ALLOWED_MODULES_VAR}={allowed_modules}",
            service,
        ]
    )
    run = run_attached(
        argv,
        server_dir,
        wsl_distro=wsl_distro,
        sink=sink,
        cancel=cancel,
        # T539: the container goes with a Stop at once, `up` and `run` alike. See
        # `end_one_shot()`; the caller asks it again before it reports the Stop.
        on_cancel=lambda: end_one_shot(service, server_dir, wsl_distro=wsl_distro),
    )
    if run.returncode != 0:
        # Not raised here. See above — the probe is the only thing that can
        # tell the two failures apart, so this only makes sure the reason is in
        # the log.
        words = " / ".join(importer_last_words(run.tail))
        logger.warning(f"{service} exited {run.returncode}: {words}")
    return run


IMPORT_REST_UNDER_DETAILS = (
    "Its last lines, and how to read everything it printed, are under Details."
)
"""Where an import that did not finish points the player (T248): never at a command.

Both callers show Details: Repair under its press, and the install engine's
failure through `said.with_details()`.
"""


def _import_details(service: str, server_dir: Path, run: AttachedRun) -> str:
    """The importer's last lines, then how to read all of them (T248)."""
    return "\n\n".join(
        part for part in (last_words(run.tail), logs_command(service, server_dir)) if part
    )


def verify_import(
    probe: ImportProbe, service: str, server_dir: Path, run: AttachedRun
) -> ImportState:
    """Ask the databases whether the one-shot actually finished; raise if not.

    The post-check both the repair button and the install engine use, so
    "finished" means one thing in this codebase. Its two accepting answers are
    `imported`, and `populated` *with* `complete` — the second because an
    AzerothCore import applies every module's own `db-auth`/`db-characters`
    updates and a module is free to seed rows (measured: a first-ever import
    carrying mod-city-bots came out with 400 accounts and 400 characters,
    yulon-ubuntu 2026-08-23), and the `complete` half because `populated` alone
    reports an import that seeded rows and then died on the world schema as a
    success (review, 2026-08-23).

    Safe only because of the order the callers keep: `populated` is refused
    *before* the one-shot runs, so a database that is populated afterwards was
    populated by the run that just happened.

    Raises:
        DockerCommandError: the databases do not read as finished.
    """
    after = probe()
    # `populated` counts as success, and finding that out took a live import.
    # An AzerothCore import is not only AzerothCore's SQL: every module in the
    # tree gets its own `data/sql/db-auth` and `db-characters` updates applied
    # by the same one-shot, and a module is free to seed rows. Measured on
    # yulon-ubuntu, 2026-08-23: a first-ever import of an install carrying
    # mod-city-bots finished exit 0 with all three schemas full AND 400 rows in
    # `acore_auth.account` plus 400 in `acore_characters.characters`, every one
    # of them written by that module's own update files. Against `!= "imported"`
    # this raised "Nothing is imported that was not imported before" over an
    # import that had just done everything — the action reporting failure for
    # its own success, on the servers this project actually ships.
    #
    # But `populated` on its own is not enough, and a review caught that after
    # the gate had run. The probe answers `populated` the instant one row
    # exists, before it has looked at whether the schemas are finished — which
    # is right for the refusal and useless here. An import that applies the
    # module's `db-auth` updates and then dies on the world schema is
    # `populated` with `acore_world` empty, and this reported it as a finished
    # repair. So the post-check reads `complete` too, which is the question it
    # was actually asking all along.
    if after.state == "imported" or (after.state == "populated" and after.complete):
        pass
    elif after.state == "populated":
        # Rows but not every schema: the one-shot got far enough to apply a
        # module's `db-auth` updates and then died. Reading the state alone
        # called this a finished repair, hid the button, and left the user a
        # broken server with a success message (review, 2026-08-23).
        raise DockerRefusal(
            f"The database import ran and wrote some rows, but did not finish: {after.detail}. "
            f"The databases are in a half-imported state. {IMPORT_REST_UNDER_DETAILS}",
            detail=_import_details(service, server_dir, run),
        )
    else:
        raise DockerRefusal(
            f"The database import ran, but the databases still read as {after.state} "
            f"({after.detail}). Nothing is imported that was not imported before. "
            f"{IMPORT_REST_UNDER_DETAILS}",
            detail=_import_details(service, server_dir, run),
        )
    logger.info(f"{service} finished; the databases now read as {after.state}")
    return after


def apply_module_sql(
    spec: ContainerSpec,
    server_dir: Path,
    *,
    output: OutputSink | None = None,
    db_timeout: float = _DB_HEALTHY_TIMEOUT_SECONDS,
    modules: str | None = None,
    wsl_distro: str | None = None,
) -> AttachedRun:
    """Run the one-shot importer for the modules on disk. The route nothing else takes.

    A module installed by `apply.py` clones its files and activates its conf,
    and its `data/sql/db-world/*.sql` is then left "to `ac-db-import` on next
    start". There is no next start that runs it. `start_staged()` names the
    three long-running services precisely so an ordinary Start can never reach
    the importer, and `repair_import()` refuses every install whose databases
    are `imported` or `populated` — which is every working server. So a module's
    SQL was never applied by any button in this app, and the applier reported it
    as done regardless.

    This is that missing route, and it is deliberately not the repair. The
    repair exists for a BROKEN install and is about the base data; this one runs
    the same one-shot over a database that is expected to be complete, so that
    upstream's own updater applies the pending module files and ledgers them in
    `updates`. Running the importer over a finished install is what upstream's
    own `docker compose up` does on every start, so it is not a new risk — the
    measured danger (`ImportState.repairable`) is re-running it over a schema
    that was left HALF-written, which this cannot reach without going past the
    same refusals.

    The refusals are the same three `repair_import()` opens with, and they are
    the same on purpose: ownership must be provable, containers wearing our
    names must be ours, and **this install's authserver or worldserver must not
    be running**. That last one is checklist 8.7a's rule — a live worldserver
    holds state in memory and writes it back over whatever the import leaves —
    and it is the reason the honest affordance here is "Stop, then apply", not
    a button that works while people are playing.

    What is deliberately NOT here is a post-check. `verify_import()` asks
    whether the base import finished, which for this call is already true before
    it starts and stays true whether or not a single module file was applied.
    What actually happened is in the importer's own output — `>> Applying update
    <file>.sql` per file, `>> Applied N quer(y|ies)` — so the run comes back to
    the caller rather than a bool, and a caller that wants to tell the user what
    was applied reads the lines through `output` (the tail on the result is
    bounded; see `KEEP_OUTPUT_LINES`).

    `modules` is what `AC_UPDATES_ALLOWED_MODULES` is set to, and `None` --
    every caller until T78 -- means `allowed_modules(server_dir)`: every folder
    on disk. A caller that knows some of those folders hold SQL THIS app already
    applied with its own client passes the rest, because the updater refuses a
    file it has no ledger row for and exits 1 over it
    (`apply.module_sql_plan()` is that caller, and its docstring is the
    measurement). The three meanings of the value, `""` among them, are on
    `ALL_MODULES`; nothing is validated here, because upstream's own reading is
    the only one that counts.

    Raises:
        DockerCommandError: any of the refusals above, the database never became
            healthy, or the importer exited non-zero.
    """
    logger.debug(f"apply_module_sql() called: server_dir={server_dir}")
    service = spec.import_service
    if not service:
        raise DockerRefusal(
            "this game does not say which compose service imports its databases, so there is "
            "nothing to run its modules' SQL with. Nothing was changed."
        )

    project = install_project(spec, server_dir, wsl_distro=wsl_distro)
    if project is None:
        _refuse_without_an_identity(
            spec, server_dir, "No module SQL was applied.", wsl_distro=wsl_distro
        )
        raise DockerRefusal(
            f"the install in {server_dir} cannot say which compose project it is — its compose "
            f"files are unreadable and no {PROJECT_NAME_VAR} is pinned — so no module SQL was "
            "applied. Running the importer against the wrong project would write to the wrong "
            "database."
        )

    running = _running(spec, project, wsl_distro=wsl_distro)
    if running.unreadable:
        raise DockerRefusal(
            f"Docker would not say which project owns {', '.join(running.unreadable)}, so this "
            f"install in {server_dir} cannot prove those containers are its own. No module SQL "
            "was applied."
        )
    if running.strangers:
        raise _stranger_refusal(running.strangers, project, server_dir)
    servers = [name for name in (spec.world, spec.auth) if name in running.ours]
    if servers:
        verb = "is" if len(servers) == 1 else "are"
        raise DockerRefusal(
            f"{', '.join(servers)} {verb} running. The importer writes to the databases "
            "underneath them, and a running worldserver holds characters in memory and saves "
            "them back over whatever it finds. Press Stop first, then try again."
        )

    # Asked BEFORE the database is started, because it is the one refusal here
    # that is about the install's shape rather than its state: nothing this
    # function could do first would change the answer, and starting a container
    # to reach a refusal is a cost for nothing.
    sees = importer_sees_modules(service, server_dir, wsl_distro=wsl_distro)
    if sees is False:
        raise DockerRefusal(
            f"{service} in {server_dir} has no {MODULES_DIR_NAME} folder mounted into it, so it "
            "cannot see the modules that are installed here and nothing it applied would be "
            "theirs. This is what a server built by the DML bash installer looks like — its "
            f"compose file binds {MODULES_DIR_NAME} into the worldserver only. Nothing was run. "
            "Add the mount to this install's compose file (Yu'lon does not rewrite a compose "
            "file it did not write), then try again."
        )
    if sees is None:
        # Not a refusal. "Could not read the compose config" is the state a
        # pinned project name exists for, and the cost of being wrong here is a
        # run that applies nothing — loud in its own output — rather than a
        # write to anything.
        logger.warning(
            f"could not tell whether {service} can see this install's {MODULES_DIR_NAME} folder; "
            "running it anyway, and its output is the evidence"
        )

    start_database(
        spec,
        server_dir,
        timeout=db_timeout,
        because="no module SQL was applied",
        wsl_distro=wsl_distro,
    )

    allowed = allowed_modules(server_dir) if modules is None else modules
    logger.warning(f"apply_module_sql(): running {service} for modules: {allowed!r}")
    run = run_one_shot(
        service, server_dir, allowed_modules=allowed, wsl_distro=wsl_distro, sink=output
    )
    if run.returncode != 0:
        # In words (T214, PR 305's live check): the sentence used to name `--rm`
        # and `docker compose logs`. The importer's container is removed when it
        # stops, so what it printed is all there is; Details holds its error.
        raise DockerRefusal(
            "the database importer stopped with an error, so the modules' SQL may be only "
            "partly applied. What it said is under Details, and the importer keeps nothing "
            "else once it stops.",
            detail="\n".join(importer_last_words(run.tail)),
        )
    return run


ONE_SHOT_END_SECONDS = 30.0
"""How long `end_one_shot()` waits for a killed one-shot to be seen gone (T539).

`docker kill` sends SIGKILL, which no importer can trap, so the wait is for the
daemon to report the exit: seconds. A container still running at the end of it is
one the daemon would not end, and the caller says so rather than going on."""

_ONE_SHOT_POLL_SECONDS = 0.5
"""How often `end_one_shot()` asks again whether a killed one-shot is gone. Not a deadline."""

_ONE_SHOT_ASK_TIMEOUT = 30.0
"""The bound on each `docker ps`/`docker kill` it sends: a wedged daemon is an answer too."""


@dataclass(frozen=True)
class OneShotLeft:
    """What `end_one_shot()` could not end: the containers by name (empty if Docker would not
    say which), and why, in words for the sentence that names them."""

    names: tuple[str, ...]
    reason: str


def _spelled(folder: str) -> str:
    """A folder as `end_one_shot()` compares it: separators and case as this OS reads them."""
    return os.path.normcase(os.path.normpath(folder.strip()))


def _folder_spellings(server_dir: Path, *, wsl_distro: str | None = None) -> set[str]:
    """Every spelling of `server_dir` compose may have stamped as a container's folder.

    As given and resolved (compose may have followed a link), and for a server inside
    a WSL distro the distro's own path, which is what compose saw there.
    """
    found = {_spelled(str(server_dir))}
    try:
        found.add(_spelled(str(server_dir.resolve())))
    except OSError:
        pass
    if wsl_distro is not None:
        inside = platform.wsl_linux_path(server_dir)
        if inside is not None:
            found.add(_spelled(inside))
    return found


def importer_left_sentence(left: OneShotLeft, nothing_done: str) -> str:
    """A database importer from an earlier run that could not be ended, and how to end it (T539).

    `nothing_done` says what was therefore not done ("the import was not re-run").
    """
    names = ", ".join(left.names)
    who = f"A database import ({names})" if names else "A database import"
    said = (
        f"{who} from an earlier run may still be running, and it could not be ended: "
        f"{left.reason}. The databases were not touched, and {nothing_done}."
    )
    if not left.names:
        return said
    return f"{said} To end it, run this in a terminal:\ndocker rm -f {' '.join(left.names)}"


def end_one_shot(
    service: str, server_dir: Path, *, wsl_distro: str | None = None
) -> OneShotLeft | None:
    """End every running container of this install's one-shot `service`; None once none runs.

    T539. The install's import is `compose up --no-deps <importer>`, attached, and a
    Stop ends the CLI. Whether the importer goes with it is not this app's to
    choose: on Linux compose stops it when its CLI is signalled (yulon-ubuntu,
    2026-10-07), on Windows the Stop ends compose itself with docker.exe
    (T246/T299) and Docker Desktop keeps a container whose CLI is gone (T303), and
    a `compose run --rm` one outlives its CLI everywhere (measured the same day).
    An importer left running is under the next Install's `partial` branch, which
    DROPs the half-written schemas it is still writing. So the Stop ends it here,
    and the retry asks here before it clears anything.

    Found by its labels, not a name: this install's compose project (the pin, or
    the folder's own, as `install_project()` reads it) and the service, which
    covers an `up` container and any `run` one. Killed, then asked again until
    none runs or `ONE_SHOT_END_SECONDS` pass. Never raises.
    """
    project = pinned_project_name(server_dir) or compose_project_name(
        server_dir, wsl_distro=wsl_distro
    )
    if project is None:
        return OneShotLeft((), "this install's compose project could not be read")
    ask = [
        "ps",
        "--filter",
        f"label={PROJECT_LABEL}={project}",
        "--filter",
        f"label={SERVICE_LABEL}={service}",
        "--format",
        '{{.Names}}\t{{.Label "' + WORKING_DIR_LABEL + '"}}',
    ]
    ours = _folder_spellings(server_dir, wsl_distro=wsl_distro)
    deadline = time.monotonic() + ONE_SHOT_END_SECONDS
    killed: tuple[str, ...] = ()
    settled = False
    while True:
        listed = _docker(ask, timeout=_ONE_SHOT_ASK_TIMEOUT, wsl_distro=wsl_distro)
        if listed.returncode != 0:
            said = " ".join((listed.stderr or listed.stdout or "no answer").split())
            return OneShotLeft(killed, f"Docker would not say whether it is still running ({said})")
        running: list[str] = []
        for row in listed.stdout.splitlines():
            name, _, folder = row.partition("\t")
            if not name.strip():
                continue
            if not folder.strip():
                # Compose stamps every container with its folder; one without is not
                # provably this install's, and is not killed (adversarial review).
                return OneShotLeft(
                    (name.strip(),), "Docker did not say which folder it was started from"
                )
            if _spelled(folder) in ours:
                running.append(name.strip())
            else:
                # Same project and service, another folder: another install's. Not ours.
                logger.info(f"end_one_shot(): {name.strip()} belongs to {folder.strip()}; left")
        if not running:
            if settled:
                if killed:
                    logger.info(f"end_one_shot(): {', '.join(killed)} ended")
                return None
            # One more look a settle later, after a kill too: a create the daemon
            # received before a Stop can finish after this look (T321's bound,
            # Codex's adversarial reviews).
            settled = True
            time.sleep(container_end.LATE_CREATE_SETTLE)
            continue
        settled = False
        fresh = [name for name in running if name not in killed]
        if fresh:
            # Each container is killed once; one that appears later (a create the
            # daemon finished late) is killed when it is first seen.
            logger.warning(f"end_one_shot(): {', '.join(fresh)} still running; killing")
            killed = (*killed, *fresh)
            _docker(["kill", *fresh], timeout=_ONE_SHOT_ASK_TIMEOUT, wsl_distro=wsl_distro)
        if time.monotonic() >= deadline:
            return OneShotLeft(
                tuple(running),
                f"it was still running {ONE_SHOT_END_SECONDS:g} s after it was told to stop",
            )
        time.sleep(_ONE_SHOT_POLL_SECONDS)


_MYSQL_ERROR = re.compile(r"^ERROR \d+ \([0-9A-Z]+\)")
"""The mysql client's own error line: `ERROR 1064 (42000) at line 2: You have an error ...`."""
_IMPORTER_APPLYING = re.compile(r"Applying update\b")
_IMPORTER_CONTEXT_AFTER = 3
"""Lines kept after the error line: the importer's "Applying of file ... failed!" and its end."""


def importer_last_words(tail: Sequence[str]) -> list[str]:
    """The lines of an importer's output that say why it stopped, whole (T214).

    PR 305's live check read Details beginning "…_world' failed!" -- the last
    400 characters of `last_words()`, cut mid-line -- with the mysql error it
    stopped on, `ERROR 1064 (42000) ... near 'THIS IS NOT SQL AT ALL'`, not in
    it. So the last mysql `ERROR` line is looked for and always kept, with the
    `Applying update` line before it that names the file and the few lines after
    it that say what the importer made of it. Without one, the last five lines.
    Every line is kept whole; colour codes are already gone (`run_attached()`).
    """
    said = [line.strip() for line in tail if line.strip()]
    errors = [i for i, line in enumerate(said) if _MYSQL_ERROR.match(line)]
    if not errors:
        return said[-_LAST_WORDS_LINES:] or ["it printed nothing at all"]
    at = errors[-1]
    applying = [line for line in said[:at] if _IMPORTER_APPLYING.search(line)]
    return [*applying[-1:], *said[at : at + 1 + _IMPORTER_CONTEXT_AFTER]]


_NO_SUCH_CONTAINER = re.compile(r"\bno such (?:object|container)\b", re.IGNORECASE)
"""The two answers docker gives for a name it does not know (T95, wordings measured).
No unreachable-daemon wording contains either; "no such file or directory" is not one.
`container_state()` reads it as `missing`."""

_UNREAD_SAID: dict[tuple[str, str | None], str] = {}
"""What Docker last said when a container's state could not be read, by (name, distro).

`_note_unread()` / `_note_read()` keep it, under `_UNREAD_LOCK`: the realm poll
and a stop's waits read from worker threads.
"""
_UNREAD_LOCK = threading.Lock()


def _note_unread(container: str, wsl_distro: str | None, said: str) -> None:
    """Log a failed state read once per change of what Docker said.

    The Server tab reads the world's state every five seconds. With the daemon
    stopped, each read logged Docker's words again: 39 identical lines in three
    minutes on the Linux live test of PR 291 (2026-10-04).
    """
    with _UNREAD_LOCK:
        if _UNREAD_SAID.get((container, wsl_distro)) == said:
            return
        _UNREAD_SAID[(container, wsl_distro)] = said
    logger.warning(f"could not read the state of {container}: {said}")


def _note_read(container: str, wsl_distro: str | None) -> None:
    """A read that answered after one that did not: say so once, and forget the failure."""
    with _UNREAD_LOCK:
        if _UNREAD_SAID.pop((container, wsl_distro), None) is None:
            return
    logger.info(f"Docker answers about {container} again")


_STOP_SAYS_GONE = "No such container"
"""What `docker stop` says for a container that is already gone; `_run_docker_stop()`
takes it as done. The stricter, older reading of `_NO_SUCH_CONTAINER`'s answer, kept
exactly as it was; a test pins that the regex matches it, so the two cannot drift (T95)."""


def _run_docker_stop(
    container: str, *, wsl_distro: str | None = None, deadline: float | None = None
) -> None:
    """`docker stop <container>`, blocking until that one container has exited.

    One call per container on purpose. `docker stop a b c` looks ordered and is
    not: the CLI fans a multi-name stop out one goroutine per name, so all three
    receive SIGTERM in the same instant and the order in argv means nothing —
    measured at 6.19s total for three containers where the *first* one traps
    SIGTERM for 6s. A single-container stop blocks until that container is gone,
    which is what makes "world before the database" real rather than decorative.

    The grace is `STOP_GRACE_SECONDS`, not Docker's 10-second default, for the
    reason recorded there: at 10s the populated worldserver is SIGKILLed while
    it is still writing character saves. This path is the fallback, so it is the
    one that runs for an install whose compose files cannot be read — exactly
    the install least able to survive losing a save queue.

    A container that has vanished since it was listed is not an error: the goal
    state is "not running", and it is already there.

    **`deadline` bounds the command itself**, for the one caller that asks
    (T159's corrections press, `STOP_PROCESS_DEADLINE_SECONDS`): without it a
    daemon that stops answering holds that press, and the panel showing it,
    for ever (Codex, T159). Past it the stop's outcome is not known -- docker
    may yet finish it -- and the error says exactly that. None, the default,
    is every other stop's: `test_the_stop_paths_impose_no_subprocess_deadline_of_their_own`
    holds why `stop_staged()`'s fallback takes none.
    """
    # `-t` and not `--timeout`. Docker renamed the long form: through 27.x the
    # flag is spelled `-t, --time`, and `--timeout` only became valid in CLI
    # 28.0.0 (docker/cli#5485). The short form has meant the same thing across
    # every version this project can meet, so it is the only spelling that is
    # safe here — `--timeout` would exit 125 with `unknown flag` on any older
    # daemon, turning a working by-name stop into a hard failure (review).
    proc = _docker(
        ["stop", "-t", str(STOP_GRACE_SECONDS), container],
        timeout=deadline,
        wsl_distro=wsl_distro,
    )
    if proc.returncode == 0:
        return
    if deadline is not None and proc.returncode == _TIMEOUT_RETURNCODE:
        raise DockerRefusal(
            f"Docker did not finish stopping {container} within {deadline:.0f} seconds, so "
            f"whether {container} stopped is not known"
        )
    if _STOP_SAYS_GONE in proc.stderr:
        logger.debug(f"docker stop {container}: already gone")
        return
    raise DockerCommandError(f"docker stop {container} failed: {proc.stderr.strip()}")


def _refuse_without_an_identity(
    spec: ContainerSpec,
    server_dir: Path,
    nothing_was: str = "Nothing was stopped.",
    *,
    wsl_distro: str | None = None,
) -> None:
    """Raise if anything is running under our names while we cannot name our project.

    Returns quietly when nothing of the sort is up: there is simply nothing to
    stop, which is not an error.

    Raises:
        DockerCliMissingError: There is no docker CLI here. Deliberately allowed
            straight through from `_status_safe()` rather than folded into the
            message below. When Docker is absent, the project name is not
            *also* a problem — it is not a problem at all, since compose would
            have nothing to say it to — and naming it sends the user to edit
            their install's `.env` over a machine that has no Docker on it
            (review, 2026-08-23).
        DockerUnansweredError: Docker was asked and would not answer.
    """
    listed = _status_safe(wsl_distro=wsl_distro)
    if listed is None:
        # `or []` used to live here, which turned "Docker would not answer" into
        # "nothing is running" and returned False — the caller then told the user
        # the server had stopped while it was still serving. Socket permissions,
        # a wrong DOCKER_HOST and an API timeout under load all land here
        # (review, 2026-08-22).
        raise DockerUnansweredError(
            f"could not ask Docker what is running, and the install in {server_dir} has no "
            f"{PROJECT_NAME_VAR} pinned either, so nothing about it can be established. "
            f"{nothing_was}"
        )
    named = {line.strip() for line in listed}
    up = [name for name in (spec.world, spec.auth, spec.db) if name in named]
    if not up:
        logger.info("stop_staged(): no project name, and nothing running under our names")
        return
    raise DockerRefusal(
        f"cannot tell which containers belong to the install in {server_dir}: its compose files "
        "are unreadable and no COMPOSE_PROJECT_NAME is pinned, while "
        f"{', '.join(up)} are running. {nothing_was}"
    )


STRANGER_DETAIL = (
    "Each running compose project and the compose file it came from: docker compose ls"
)
"""What the stranger refusal's "Docker can list each running project" means, for Details (T248)."""


def _stranger_refusal(
    strangers: tuple[tuple[str, str | None], ...], project: str, server_dir: Path
) -> DockerRefusal:
    """`_stranger_message()` as the refusal, with the listing command under Details (T248)."""
    owned = any(owner for _name, owner in strangers)
    return DockerRefusal(
        _stranger_message(strangers, project, server_dir),
        detail=STRANGER_DETAIL if owned else "",
    )


def _stranger_message(
    strangers: tuple[tuple[str, str | None], ...], project: str, server_dir: Path
) -> str:
    """Explain a name/label mismatch, and put the diagnosis before any remedy.

    Two rewrites, both from review. It first ended with "re-attach this
    install", which is worse than useless: attaching no longer pins at all, and
    the version that did would have written the *current* basename — the very
    value that produces this mismatch — after which `.env` outranks the
    directory forever and a recoverable state becomes permanent.

    It then led with "set COMPOSE_PROJECT_NAME=<their project>", which was
    measured doing real damage: a user who followed it literally on a genuine
    second install had Yu'lon adopt and stop the OTHER server, on Yu'lon's own
    instructions. So the first thing offered is now the command that tells the
    two cases apart, and the pin is offered only after it, only when there is
    exactly one candidate, and only as the branch that begins "if it is".
    """
    owners = sorted({owner for _name, owner in strangers if owner})
    unlabelled = [name for name, owner in strangers if not owner]
    labelled = [name for name, owner in strangers if owner]
    names = ", ".join(name for name, _owner in strangers)
    lines = [
        f"{names} are running, but they do not belong to the install in {server_dir}, which is "
        f"compose project '{project}'. Nothing was stopped: this could equally be another "
        "install of the same game, or this one after its folder was moved, and stopping the "
        "wrong one would take down a server somebody is playing on."
    ]
    if unlabelled:
        lines.append(
            f"{', '.join(unlabelled)} carry no compose project at all, so they were started "
            "outside compose — Yu'lon will not touch them."
        )
    if owners:
        which = " and ".join(f"'{owner}'" for owner in owners)
        lines.append(
            f"{', '.join(labelled)} belong to compose project {which}. Find out which folder "
            "that is before changing anything: Docker can list each running project with its "
            "own compose file. If it is a different install, stop that server from its own "
            "folder and leave this one alone."
        )
    if len(owners) == 1 and not unlabelled:
        # Both conditions matter. With a second, unlabelled stranger in the set,
        # adopting the one project still leaves that container foreign — the
        # next Stop refuses again, `owners` is then empty, and the message
        # offers no remedy at all. A permanent write that bought nothing.
        pinned = pinned_project_name(server_dir)
        if pinned is not None:
            # A folder move CANNOT produce this state once a pin exists — the
            # pin outranks the directory. So the causes are a genuinely
            # different install, or a `.env` copied along with the folder, and
            # saying "the folder was moved" here sent the user to change a file
            # in the copy case, which is how the copy stops the original's
            # server (review, 2026-08-22).
            lines.append(
                f"This install claims project '{pinned}' from {server_dir / '.env'}. If that "
                f"line was copied here from another install, delete it. If this install really "
                f"is '{owners[0]}', change it to that."
            )
        else:
            # The copy warning is not optional here. This is now the common
            # branch — almost nothing pins any more — and a user in the copy
            # case who follows the remedy literally reproduces the exact
            # failure that got the Stop-time pin deleted: the copy adopts the
            # original's project and the next Stop takes down the original's
            # server (review, 2026-08-23).
            lines.append(
                f"Only if this folder IS the install those containers came from — moved or "
                f"renamed, not copied — add {PROJECT_NAME_VAR}={owners[0]} to "
                f"{server_dir / '.env'}, or rename this folder to {owners[0]}. If it is a copy "
                f"of that install, do neither: adopting the name would make Stop here take "
                f"down the other server."
            )
    elif len(owners) > 1:
        lines.append(
            "More than one project is involved, so no single name can reconcile them: sort the "
            "installs out from their own folders first."
        )
    return " ".join(lines)


WORLD_STILL_LOADING = (
    "The world is finishing its load before it can stop. A world server that is still loading "
    "ignores a stop, so Yu'lon waits until it can hear one and then stops it cleanly. That is "
    "usually under a minute; a first start can take much longer."
)
"""Said when a stop finds the world still loading and waits (T158), and again after a spell
of `WORLD_LOAD_UNCHECKED`.

The owner's words are its first sentence. There is no time limit to state:
the wait has none (see `wait_for_the_world_to_load()`), and the way out is
the caller's "Stop now anyway".
"""

WORLD_LOAD_UNCHECKED = (
    "Yu'lon can't check whether the world has finished loading: Docker is not answering the "
    "question. It keeps checking and will not stop the world until it can hear the stop -- "
    'press "Stop now anyway" to stop it regardless.'
)
"""Said when a look at a running world could not be read (T158, round 3: fail closed).

A running world whose mask cannot be read is NOT taken for one that can hear
the stop, and not for one that is gone: the wait goes on, says so, and says
the loading sentence again once a look reads.
"""

WORLD_FINISHED_LOADING = "The world has finished loading; stopping it now."
"""Said once the world can hear the stop, if a wait was announced first (T158)."""

WORLD_STUCK_AT_UPDATE_TAIL = (
    " It is not loading and cannot hear a stop, so Yu'lon is stopping it now instead of waiting."
)
"""Follows `update_failure.explain()`'s sentence when a stop finds a world stuck at a failed
update (T600)."""

_UPDATE_LOG_TAIL = 20
"""How many lines of a run's log a stop reads to see whether it ends on a failed update."""

WORLD_STOPPED_ANYWAY = (
    "Stopping the world now, as asked, although it had not finished loading. It may be "
    "force-stopped: a world still loading ignores the stop and is killed when the "
    f"{STOP_GRACE_SECONDS // 60}-minute grace ends, and anything since its last save is lost."
)
"""Said when "Stop now anyway" ends the wait of a world not seen able to hear the stop (T158)."""

WORLD_RESTARTED_STOPPING = (
    "The world server restarted while this waited for it to load, so it is crash-looping: "
    "stopping it now rather than waiting for a load that does not finish."
)
"""Said when `StopControl.restart_ends_the_wait` ends a wait on a new run (T159)."""

STOP_WAITS_FOR_THE_LOAD = (
    "Stop was pressed: Yu'lon lets the world server finish loading (stopping it mid-load can "
    "damage the database), then puts the build from before back."
)
"""Said once when Stop lands in a rebuild's ready wait (T247, the lead's words, 2026-10-05).

T158's rule reaches the ready wait: a new world still loading may be in the
middle of its database update, so the Stop is heard at once and acted on once
the load has ended -- ready, crashed or out of time. A member of
`LOAD_WAIT_LINES`, so the panel offers "Stop now anyway" beside it.
"""

LOAD_WAIT_LINES = frozenset({WORLD_STILL_LOADING, WORLD_LOAD_UNCHECKED, STOP_WAITS_FOR_THE_LOAD})
"""The sentences said while a stop is WAITING: the ones "Stop now anyway" is offered beside."""

WORLD_SAVE_STALL_SECONDS = 300
"""How long a world that was asked to stop may write NOTHING before it is killed (T384).

Silence, not total time. Measured on yulon-ubuntu (WotLK, 500 bots): after a SIGTERM
AzerothCore logs out every bot and then drains its character save queue
(`DatabaseWorkerPool::Close()` -> `_queue->Shutdown()`; the worker finishes the queue before
the join). The same ~2200 queued saves took 9-165 s across ten clean stops in the world's own
log, 150 s on a `server shutdown 1` on 2026-10-05, 34 s on a plain SIGTERM and 37 s on a
`compose stop` the same night -- and more than 300 s on the T301 gate, where `compose stop -t
300` SIGKILLed it mid-save (exit 137, the box's disk at its slowest: InnoDB took 70 s to start
that run against 1.7 s on a good one). During the slowest live drain watched, the world still
sent its database 2-3 KB every two seconds. So the clock restarts at every byte, and a world
is given the whole of the old fixed grace in silence before it is taken for hung -- never
less than the stop it replaces allowed.
"""

WORLD_SAVE_CEILING_SECONDS = 1800
"""The longest a stop waits for a world that keeps writing (T384): the bound on the bound.

Six times the old grace and the worst save seen (more than 300 s). At the slowest rate ever
seen (under 8 saves a second, the killed run) it covers some 14000 queued saves, nearly twice
the deepest queue measured (7400-7700, 1980 characters online, `STOP_GRACE_SECONDS`). It
exists for a world that sends something now and then and never finishes -- and for a world
whose traffic cannot be read at all, which is never taken for silence.
"""

_SAVE_POLL_SECONDS = 2.0
"""How long a stop pauses between two looks at a world that is saving (T384)."""

WORLD_SAVING = (
    "Saving characters: the world server is writing every character to its database before it "
    "closes. That usually takes under a minute and can take several on a busy disk. Yu'lon "
    "waits for it."
)
"""Said once the world was asked to stop and is still running at the first look (T384)."""

WORLD_SAVED = "The world server saved every character and closed."
"""Said when a world that `WORLD_SAVING` was said of has exited by itself (T384)."""

WORLD_CLOSED = "The world server closed without an error."
"""`WORLD_SAVED`'s place when the save asked for first did not happen or did not finish (T496).

Live, yulon-win11-gate, 2026-10-06: one Stop said `SAVE_FIRST_NOT_ASKED` and then
`WORLD_SAVED`, which contradict each other. The warning before it says what may be missing; this
says only what was seen, a clean exit."""

_WORLD_SAVE_FAILED_START = "The world server stopped with an error"


def world_save_failed(code: int) -> str:
    """Said when a world that was asked to stop exited with a code other than 0 (T414).

    Live, 2026-10-06 (Tortoise): "Cant begin transaction." and exit 139 after "Halting
    process...", while the tab said `WORLD_SAVED`. The world's last lines go under Details
    (`said.details_below()`), never into this sentence. Outlives the stop.
    """
    return (
        f"{_WORLD_SAVE_FAILED_START} (exit code {code}) while it was saving characters, so some "
        "saves may not have been written. Check your characters before you play; the world "
        "server's last lines are under Details."
    )


WORLD_SAVE_UNREAD = (
    "The world server closed, but Yu'lon could not read whether it finished saving cleanly. "
    "Check your characters before you play."
)
"""Said when a world that was asked to stop is gone and its exit code cannot be read (T414)."""

_EXIT_LINES = 20
"""How many of the world's last log lines go under Details when it exits with an error (T414)."""


def outlives_the_stop(text: str) -> bool:
    """Is this stop sentence one the tab keeps after the stop (`FORCE_STOP_WARNINGS`, T414)?

    Read on the sentence alone: a crash's line carries its exit code, and Details under it.
    """
    sentence = text.split("\n\nDetails:\n", 1)[0]
    return (
        sentence in FORCE_STOP_WARNINGS
        or sentence == WORLD_SAVE_UNREAD
        or sentence.startswith(_WORLD_SAVE_FAILED_START)
    )


def _how_the_world_ended(world: str, wsl_distro: str | None) -> tuple[str, bool]:
    """The sentence for a world that exited after its signal, and whether it is a warning (T414)."""
    proc = _docker(
        ["inspect", world, "--format", "{{.State.ExitCode}}"],
        timeout=_LOAD_LOOK_TIMEOUT,
        wsl_distro=wsl_distro,
    )
    code = proc.stdout.strip()
    if proc.returncode != 0 or not code.lstrip("-").isdigit():
        logger.warning(f"could not read how {world} exited: {proc.stderr.strip()}")
        return WORLD_SAVE_UNREAD, True
    if int(code) == 0:
        return WORLD_SAVED, False
    tail = log_tail(world, _EXIT_LINES, wsl_distro=wsl_distro) or ""
    return details_below(world_save_failed(int(code)), ansi.strip(tail).strip()), True


WORLD_SAVE_STALLED = (
    f"The world server wrote nothing to its database for {WORLD_SAVE_STALL_SECONDS // 60} minutes "
    "while it was saving, so Yu'lon force-stopped it. Characters may be missing what happened "
    "since their last save."
)
"""Said when a saving world was killed for its silence (T384). Outlives the stop."""

WORLD_SAVE_TOO_LONG = (
    f"The world server was still saving after {WORLD_SAVE_CEILING_SECONDS // 60} minutes, so "
    "Yu'lon force-stopped it. Characters may be missing what happened since their last save."
)
"""Said when a saving world was killed at `WORLD_SAVE_CEILING_SECONDS` (T384). Outlives the stop."""

WORLD_SAVE_ABANDONED = (
    "Yu'lon stopped watching while the world server was saving characters. The world goes on "
    "saving and closes by itself; the rest of the server was left running so the save can finish."
)
"""The `SaveAbandoned` of a give-up during the save (T384): nothing is killed."""

SAVE_FIRST_ASKING = (
    "Saving characters: Yu'lon is asking the world server to save every character before it "
    "is stopped. This can take a minute."
)
"""Said as the save a world whose own close loses saves is asked for BEGINS (T410, T411, T415).

Before the console is typed at, not after: `saveall` and the queue look took 10-15 s live, and
a line said at their end was replaced in the same second. It stands until the next step's line
(the queue's count, the save written, or `SAVE_FIRST_NOT_ASKED` when the console did not
answer)."""

SAVE_FIRST_QUEUED = (
    "The world server has {count} saves to write to its database before it can be stopped "
    "safely. Yu'lon waits for them; on a busy disk that can take several minutes."
)
"""Said once, with the queue's length, when saves are still queued at the first look (T410)."""

SAVE_FIRST_WRITTEN = "Every character's save is written. Stopping the world server."
"""Said when the save queue is seen empty after the save (T410)."""

SAVE_FIRST_UNFINISHED = (
    f"The world server's saves were not all written: its save queue did not get shorter for "
    f"{WORLD_SAVE_STALL_SECONDS // 60} minutes, or the wait reached "
    f"{WORLD_SAVE_CEILING_SECONDS // 60} minutes. Yu'lon stopped it anyway, so characters may "
    "be missing what happened since their last save."
)
"""Said when the wait for the save queue ran out and the stop went on (T410). Outlives the stop."""

SAVE_FIRST_NOT_ASKED = (
    "Yu'lon could not ask the world server to save every character before stopping it (its "
    "console did not answer), so characters may be missing what happened since their last "
    "automatic save."
)
"""Said when the console the save is typed at could not be reached (T410, T411); outlives it."""

SAVE_FIRST_NOT_ASKED_HERE = (
    "Yu'lon could not ask the world server to save every character before stopping it. On this "
    "computer it asks through the command channel, which is not turned on or did not answer, so "
    "characters may be missing what happened since their last automatic save."
)
"""`SAVE_FIRST_NOT_ASKED` where the console needs a terminal this host cannot open (T496).

There the save is asked through the install's command channel (`save_through_channel()`), so
"its console did not answer" would name a thing that was never tried. Outlives the stop."""

_SAVE_COMMAND_WINDOW_SECONDS = 10.0
"""How long the console is listened to after `saveall` (T410, T411).

The command saves every player on the world thread before it answers; the answer
itself is not needed, only that it was typed. A window too short for it reads as
an unprompted reply and changes nothing: the queue looks that follow see the saves."""

_SAVE_COMMAND_STALLED_WINDOW_SECONDS = 30.0
"""The ceiling for `saveall` on a console that says when it has answered (T561).

Measured on Tortoise with 500 bots (2026-10-08): `saveall` answers in 0.07-0.33 s, but in the first
minutes after a start the world thread stalls: answers took up to 18.3 s through Yu'lon's console
(p95 10.95 s, max 14.14 s in the first probe) and a world-thread stall of 21.8 s was measured
elsewhere, so 10 s missed the answer and the stop warned that characters might be missing. 30 s
leaves room above the longest of those. Only for a console whose prompt follows its answer
(`SaveFirst.prompt_precedes_answer` False): the wait then ends at the answer line and prompt, so
this costs a normal stop nothing. A readline console (Centurion) sleeps its whole window, so it
keeps the 10 s."""

_SAVE_ANSWER = "All players saved."
"""What `saveall` prints when it is done (`ObjectAccessor::SaveAllPlayers()`, read from a Tortoise
world's log, 2026-10-08). A console that prompts after its answer ends the wait for `saveall` at
this line followed by its prompt (T561); without the line the whole window is listened to."""

_QUEUE_LOOK_WINDOW_SECONDS = 4.0
"""How long the console is listened to for one `server debug` answer (T410).

The console's 3 s default plus a second: the command sums the size of the map
folders before it prints the queue lines, which are its last."""

FORCE_STOP_WARNINGS = frozenset(
    {
        WORLD_STOPPED_ANYWAY,
        WORLD_SAVE_STALLED,
        WORLD_SAVE_TOO_LONG,
        SAVE_FIRST_UNFINISHED,
        SAVE_FIRST_NOT_ASKED,
        SAVE_FIRST_NOT_ASKED_HERE,
    }
)
"""The sentences that must outlive the stop they were said in: it may have been forced."""

_SIGTERM = 15
"""SIGTERM's number on Linux, where the containers run, spelled out rather than read off
`signal`: the app also runs on Windows, and what is parsed is a Linux process's mask."""

_LOAD_POLL_SECONDS = 2.0
"""How long a stop pauses between two looks at a loading world."""

_LOAD_LOOK_TIMEOUT = 30.0
"""The bound on each of a look's two docker commands (`inspect`, `exec`). A wedged daemon
answers neither; a command that runs out is a look that could not be read, and the wait goes
on. So a look takes at most a minute, and the events are read between looks and during the
pause, which wakes for them within a quarter of a second."""


class StopAbandoned(DockerCommandError, SaidByYulon, StopTookEffect):
    """The stop was given up while it waited for a loading world; NOTHING was sent (T158).

    Raised when the caller's `abandon` event is set -- the app closing, a
    rebuild cancelled before it replaced anything -- so the world is left
    running rather than signalled mid-load. Its own type so a caller that owns
    the abandon can tell it from a stop that failed, and `StopTookEffect`
    (T250) so the log panel reads it as the job's Stop.
    """


class SaveFirstAbandoned(StopAbandoned):
    """A `StopAbandoned` from the save a world is asked for BEFORE its signal (T410, T411).

    Nothing was sent and the world is still running, so every `StopAbandoned`
    handler is right about that; its own type so a handler that names the load
    ("while the world was still loading") can say "saving" instead.
    """


class SaveAbandoned(DockerCommandError, SaidByYulon, TrueAfterStop):
    """The stop was given up while the world SAVED: it was signalled and is closing (T384).

    Not a `StopAbandoned`, on purpose: every handler of that one says the world was left
    running, and after the signal it is not -- it goes on saving and closes by itself. Nothing
    was killed, and the rest of the server was left up so the save can finish.

    `TrueAfterStop` (T228): after a job's Stop, that sentence says what the press
    left -- a world closing on its own and a database still up -- so the log panel
    shows it rather than folding it into "cancelled".
    """


class CancelWithForce(threading.Event):
    """A job's Cancel that also carries a "Stop now anyway" (T158).

    Is-a `threading.Event`, so every engine and panel that takes a cancel
    takes this one unchanged; `anyway` rides along for the one place that
    needs it -- a rebuild waiting to stop a loading world -- without a second
    argument threaded through every route that can reach a rebuild.
    """

    def __init__(self) -> None:
        super().__init__()
        self.anyway = threading.Event()


@dataclass(frozen=True)
class StopControl:
    """How the caller of a stop hears a load wait and steers it (T158).

    `say` receives the wait's sentences on the stop's own (worker) thread; see
    `OutputSink` for what that obliges a UI caller to hand in. `anyway` is the
    person's "Stop now anyway": if the world still cannot hear the stop, the
    wait ends and the stop is sent regardless. `also_anyway` are further
    events that mean the same -- a rebuild's rollback counts its Cancel as one.
    `abandon` is the app's: the wait ends and NOTHING is sent (`StopAbandoned`).
    All are read at every look and interrupt the pause between looks.

    The wait never clears them. A press left over from an earlier stop is the
    owner of the event's business: `Controller` and `purge.Uninstaller` clear
    `anyway` as each stop of theirs begins.
    """

    say: OutputSink | None = None
    anyway: threading.Event = field(default_factory=threading.Event)
    abandon: threading.Event = field(default_factory=threading.Event)
    also_anyway: tuple[threading.Event, ...] = ()
    restart_ends_the_wait: bool = False
    """A world seen to RESTART during the wait is stopped at once, not waited on again (T159).

    Off for every stop T158 wrote: there, a world that died mid-load and was
    brought back is loading again and is waited for in its turn. On for the
    corrections press, which exists to end a crash loop: measured on a Linux
    gate box 2026-09-28 (Tortoise, missing `character_inventory_copy`), each
    cycle ran ~20 s -- `running`, deaf, the whole time -- and read `restarting`
    for about one second, so a wait that stops only on `restarting` can miss
    it look after look. A new run is the loop showing itself; the stop sent
    then is not a mid-load kill of a world that would have loaded, because
    this one dies at the same statement every time (measured: `docker stop`
    on it returned in 11.9-20.2 s, at the cycle's own crash, never at SIGKILL).
    """

    def forced(self) -> bool:
        """Has anyone asked for the stop regardless?"""
        return self.anyway.is_set() or any(event.is_set() for event in self.also_anyway)


def _sigterm_caught(status: str) -> bool | None:
    """Does this `/proc/<pid>/status` say the process catches SIGTERM? None: no SigCgt line."""
    for line in status.splitlines():
        name, _, value = line.partition(":")
        if name.strip() == "SigCgt":
            try:
                return bool(int(value.strip(), 16) >> (_SIGTERM - 1) & 1)
            except ValueError:
                return None
    return None


_save_clock = time.monotonic
"""The clock a save wait measures silence and its ceiling by; a seam for the tests."""


def _bytes_moved(net_dev: str) -> int | None:
    """Bytes received plus sent on every interface but loopback, from a `/proc/net/dev`."""
    total = 0
    seen = False
    for line in net_dev.splitlines():
        name, colon, counters = line.partition(":")
        fields = counters.split()
        if not colon or name.strip() == "lo" or len(fields) < 9:
            continue
        if not (fields[0].isdigit() and fields[8].isdigit()):
            continue
        total += int(fields[0]) + int(fields[8])
        seen = True
    return total if seen else None


def _console_send(command: str, **kwargs: Any) -> Any:
    """Type one line at a world's console: the shared transport (T410, T411); a seam for tests.

    Imported here rather than at the top because the console package imports this
    module. Raises whatever the transport raises when it cannot type (`ConsoleError`).
    """
    from yulon.controller_wow_wotlk import console

    return console.send_command(command, **kwargs)


def _console_reaches(wsl_distro: str | None) -> bool:
    """Can this host type at a world's console at all? The shared transport's own answer (T496).

    False on Windows with Docker Desktop: `docker attach` to a tty container needs a terminal,
    and this process has none to give it. A WSL-resident server borrows the distro's.
    """
    from yulon.controller_wow_wotlk import console

    return console.can_send(wsl_distro)


_save_channels: dict[str, dict[str, Callable[[], object | None]]] = {}
"""Each install's command channel, for a save asked where its console cannot be typed at (T496).

World container name -> that install's folder -> its `live_channel`. A world's name is the
game's, the same for every install of it, so the folder says whose channel it is: the one whose
folder the running world was brought up from (`WORKING_DIR_LABEL`, cold review). Filled by the
tab's wiring (`save_through_channel()`), read by every stop route through `_type_at_the_world()`.
"""


def save_through_channel(
    world: str, server_dir: Path, channel_of: Callable[[], object | None]
) -> None:
    """Where this host cannot type at `world`'s console, ask its save through this channel (T496).

    `channel_of` is the install's `InstallChannel.live_channel`: asked at the moment of the save,
    it hands back a channel on the saved credential (an object whose `send(command)` returns a
    `channel.Answer`), or None when the channel is not set up. On a host that can type at the
    console this is never read, so Linux stops are #326's.
    """
    _save_channels.setdefault(world, {})[_folder_key(str(server_dir))] = channel_of


def _folder_key(folder: str) -> str:
    """One spelling per folder, for matching an install to a compose working-dir label."""
    return os.path.normcase(os.path.normpath(folder))


def _channel_of_the_running_world(
    spec: ContainerSpec, wsl_distro: str | None
) -> Callable[[], object | None] | None:
    """The registered `live_channel` of the install whose world is running, or None (T496)."""
    installs = _save_channels.get(spec.world, {})
    if len(installs) == 1:
        return next(iter(installs.values()))
    if not installs:
        return None
    folder = container_working_dir(spec.world, wsl_distro=wsl_distro)
    if folder is None or folder == UNREADABLE:
        logger.warning(f"could not tell which install {spec.world} belongs to")
        return None
    return installs.get(_folder_key(folder))


@dataclass(frozen=True)
class _ChannelReply:
    """A channel's `yes`, in the shape `_save_everyone_first()` reads a `ConsoleReply` in."""

    lines: tuple[str, ...]
    prompted: bool = True


def _ask_the_channel(
    spec: ContainerSpec, command: str, wsl_distro: str | None
) -> _ChannelReply | None:
    """One command through the world's command channel; None when it was not asked or not run.

    Only a `yes` is an answer: `no` is a refusal, and `unknown` is no answer about it (a timeout
    may have run it, which is still not a save anyone saw).
    """
    channel_of = _channel_of_the_running_world(spec, wsl_distro)
    if channel_of is None:
        logger.warning(
            f"could not ask {spec.world} for {command!r}: its console needs a terminal on this "
            "computer and no command channel is known for it"
        )
        return None
    try:
        channel = channel_of()
        if channel is None:
            logger.warning(
                f"could not ask {spec.world} for {command!r}: its console needs a terminal on "
                "this computer and its command channel is not set up"
            )
            return None
        answer = channel.send(command)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 - every channel failure means "not asked" here
        logger.warning(f"could not ask {spec.world} for {command!r} through its channel: {exc}")
        return None
    if getattr(answer, "outcome", "") != "yes":
        why = getattr(answer, "reason", "") or getattr(answer, "text", "") or "no answer"
        logger.warning(f"the command channel did not run {command!r} on {spec.world}: {why}")
        return None
    lines = tuple(str(getattr(answer, "text", "")).splitlines())
    logger.info(
        f"asked {spec.world} for {command!r} through its command channel: "
        f"{lines[0] if lines else 'answered'}"
    )
    return _ChannelReply(lines)


def _type_at_the_world(
    spec: ContainerSpec,
    command: str,
    window: float,
    wsl_distro: str | None,
    answer_marker: str | None = None,
) -> Any | None:
    """One console line to this world; its `ConsoleReply`, or None when it could not be typed.

    Where this host cannot type at the console at all (T496), the line goes through the
    install's command channel instead (`_ask_the_channel()`).
    """
    save = spec.save_first
    assert save is not None
    if not _console_reaches(wsl_distro):
        return _ask_the_channel(spec, command, wsl_distro)
    try:
        return _console_send(
            command,
            container=spec.world,
            wsl_distro=wsl_distro,
            window=window,
            prompt=save.prompt,
            prompt_precedes_answer=save.prompt_precedes_answer,
            answer_marker=answer_marker,
        )
    except Exception as exc:  # noqa: BLE001 - every console failure means "not typed" here
        logger.warning(f"could not type {command!r} at {spec.world}'s console: {exc}")
        return None


def _queue_length(spec: ContainerSpec, wsl_distro: str | None) -> int | None:
    """The world's character save queue as its `queue_command` reports it, or None (T410)."""
    save = spec.save_first
    assert save is not None
    reply = _type_at_the_world(spec, save.queue_command, _QUEUE_LOOK_WINDOW_SECONDS, wsl_distro)
    if reply is None:
        return None
    return save.queue_length("\n".join(getattr(reply, "lines", ())))


def _save_everyone_first(
    spec: ContainerSpec, control: StopControl, say: Callable[..., None], wsl_distro: str | None
) -> bool:
    """Have a running world whose own close loses saves save everyone, and wait for it (T410, T411).

    Before the signal, while the world still runs and its database is up. Typed at
    the console (`SaveFirst.command`, `saveall`), which needs no credential. For a
    core that drops what is still queued when it closes (Centurion), the save
    queue's length is then read every few seconds until it is EMPTY: one worker
    thread writes that queue in order, so a queue seen empty after the command
    holds nothing the command queued (an operation the worker is in the middle of
    is finished before it stops). Not "back to its level from before": with older
    items queued, that level is reached while the command's own saves are all
    still behind them (T384 cold review).

    The console's answer is not trusted on its own: a reply with no prompt in it is
    also what an attach that never reached the console looks like. For a queue
    core a readable queue length is the proof the console answered; without one,
    and for a core with no queue to read, an unprompted reply counts as not asked.

    The stop goes on -- the caller signals the world next -- when:

    * the console did not answer (`SAVE_FIRST_NOT_ASKED`): the close then loses
      what it always lost;
    * no look has found the queue shorter than the look before it (or none could be
      read) for `WORLD_SAVE_STALL_SECONDS`, or the wait reaches
      `WORLD_SAVE_CEILING_SECONDS` (`SAVE_FIRST_UNFINISHED`). Unlike the wait after
      the signal, an unreadable look is not given the benefit of the doubt: the world
      is RUNNING here, and a wait that cannot see anything would hold a live server
      for half an hour.

    Before the save, `SaveFirst.first` is typed so nothing new joins the queue (T498):
    live, 150 Centurion bots autosaving every 90 s kept it at ~1950 for 5 minutes while
    it drained, and "never below its lowest" was read as stuck. `server debug` prints
    no count of what was written (`cs_server.cpp:255-257` at the pin), so "still
    written" is read as a queue shorter than at the look before: a worker that writes
    nothing never makes it shorter, however much is still added. A `first` line that
    cannot be typed is logged and the save goes on.

    `control.abandon` raises `StopAbandoned`: nothing has been sent, the world is
    still running, which is what every handler of that type says -- after
    `SaveFirst.if_given_up` is typed, so what `first` closed is open again.

    Returns whether the save was asked for and, for a queue core, seen through: False after
    `SAVE_FIRST_NOT_ASKED` (`SAVE_FIRST_NOT_ASKED_HERE` where the console needs a terminal this
    host cannot open, T496) or `SAVE_FIRST_UNFINISHED`, so the close is not then said to have
    saved every character.
    """
    save = spec.save_first
    assert save is not None
    not_asked = SAVE_FIRST_NOT_ASKED if _console_reaches(wsl_distro) else SAVE_FIRST_NOT_ASKED_HERE
    say(SAVE_FIRST_ASKING)
    for line in save.first:
        if _type_at_the_world(spec, line, _QUEUE_LOOK_WINDOW_SECONDS, wsl_distro) is None:
            logger.warning(f"{spec.world} was not told {line!r} before its save; saving anyway")
    window = (
        _SAVE_COMMAND_WINDOW_SECONDS
        if save.prompt_precedes_answer
        else _SAVE_COMMAND_STALLED_WINDOW_SECONDS
    )
    reply = _type_at_the_world(
        spec,
        save.command,
        window,
        wsl_distro,
        answer_marker=None if save.prompt_precedes_answer else _SAVE_ANSWER,
    )
    answered = reply is not None and bool(getattr(reply, "prompted", False))
    if not save.queue_command:
        if not answered:
            say(not_asked, warn=True)
        return answered
    started = _save_clock()
    last_shrunk = started
    before: int | None = None
    looked = False
    while True:
        if control.abandon.is_set():
            for line in save.if_given_up:
                if _type_at_the_world(spec, line, _QUEUE_LOOK_WINDOW_SECONDS, wsl_distro) is None:
                    logger.warning(f"{spec.world} was left running without {line!r}")
            raise SaveFirstAbandoned(
                f"The stop was not sent: it was given up while {spec.world} was writing its "
                "saves, so the world was left running."
            )
        length = _queue_length(spec, wsl_distro) if reply is not None else None
        now = _save_clock()
        if not looked:
            looked = True
            if length is None:
                # No queue to read, so nothing proves the console answered or what to
                # wait for: said by the reply alone, and the stop goes on.
                if answered:
                    logger.info(f"{spec.world}'s save queue could not be read; not waited on")
                else:
                    say(not_asked, warn=True)
                return answered
            if length > 0:
                say(SAVE_FIRST_QUEUED.format(count=length))
        if length == 0:
            say(SAVE_FIRST_WRITTEN)
            return True
        if length is not None:
            if before is not None and length < before:
                last_shrunk = now
            before = length
        if (
            now - started >= WORLD_SAVE_CEILING_SECONDS
            or now - last_shrunk >= WORLD_SAVE_STALL_SECONDS
        ):
            say(SAVE_FIRST_UNFINISHED, warn=True)
            return False
        control.abandon.wait(_SAVE_POLL_SECONDS)


def save_then_stop_the_world(
    spec: ContainerSpec,
    control: StopControl | None = None,
    *,
    wsl_distro: str | None = None,
    before_signal: Callable[[], None] | None = None,
    heard: bool = True,
) -> None:
    """Ask the world server alone to stop, and wait while it saves; kill it only if it hangs (T384).

    Called by every stop path right before the command that stops the rest of the server, and
    after `wait_for_the_world_to_load()`. The world gets a SIGTERM of its own (`docker kill -s
    TERM`): AzerothCore, CMaNGOS and Tortoise all save every player and bot on it and drain
    their save queue before they exit (`WORLD_SAVE_STALL_SECONDS` has the measurements). No
    Docker timer runs on that signal, so how long the save may take is decided here, by
    looking, rather than by a number chosen in advance -- the fixed 300 s grace is what
    SIGKILLed a world mid-save on the T301 gate. Measured on the box, a `docker kill -s TERM`
    counts as a stop for `restart: unless-stopped` (Docker 29.1.3: a busybox stand-in and the
    real worldserver both stayed down, where a signal sent from inside restarted it), and a
    `server shutdown` through the command channel does NOT: the world exited 0 and Docker
    started it again.

    `heard` is what `wait_for_the_world_to_load()` returned: False after "Stop now
    anyway" on a world that may still be deaf, which is left to the caller's stop
    as before (no save asked for, no signal, no watch). `before_signal` is called
    exactly once, right before the world's own signal -- after the save it may be
    asked for first, whose give-up sends nothing.

    A world whose own close loses saves (`spec.save_first`: Centurion drops what is still
    queued, Tortoise fails the owned bots' last save) is first told to save everyone at its
    console while it still runs, and for Centurion waited for until that save queue is
    written (`_save_everyone_first()`, T410/T411). Only then is it signalled.

    Each look is a bounded `docker inspect`, then -- while it runs -- a bounded read of its
    `/proc/net/dev`. Once the players are gone the world's only traffic is its database, so
    bytes still moving mean it is still saving. It ends on the first of:

    * the world is no longer running: it exited by itself (`WORLD_SAVED`, if `WORLD_SAVING`
      was said; `WORLD_CLOSED` when the save asked for first did not happen or finish, T496),
      or it restarted (a new `StartedAt`), which is left to the ordinary stop that
      follows -- as is a signal Docker refused;
    * nothing moved for `WORLD_SAVE_STALL_SECONDS` -- killed, `WORLD_SAVE_STALLED`;
    * `WORLD_SAVE_CEILING_SECONDS` since the signal -- killed, `WORLD_SAVE_TOO_LONG`;
    * `control.abandon` -- raises `SaveAbandoned` and kills NOTHING: the world goes on saving
      by itself, and the database it writes to is left up because the caller's stop of the rest
      never runs.

    A look that cannot be read is not silence (fail closed for the saves): only the ceiling
    bounds a world whose traffic Docker will not show. "Stop now anyway" is NOT read: it was
    pressed for a world that could not hear the stop, and is no leave to kill one that heard it.
    """
    control = control or StopControl()
    world = spec.world

    def say(text: str, *, warn: bool = False) -> None:
        (logger.warning if warn else logger.info)(text)
        if control.say is not None:
            control.say(text)

    if heard is False:
        # "Stop now anyway" ended a load wait on a world that may still be deaf (T158):
        # it can neither save nor hear the signal, and its loading traffic is not a
        # save. The caller's stop does what it always did, with its grace.
        logger.info(f"{world} was stopped anyway while it loaded; left to the ordinary stop")
        if before_signal is not None:
            before_signal()
        return
    saved_first = True
    if spec.save_first is not None:
        state = container_state(world, timeout=_LOAD_LOOK_TIMEOUT, wsl_distro=wsl_distro)
        if state.settled:
            saved_first = _save_everyone_first(spec, control, say, wsl_distro)
    if before_signal is not None:
        before_signal()
    proc = _docker(["kill", "-s", "TERM", world], timeout=_LOAD_LOOK_TIMEOUT, wsl_distro=wsl_distro)
    if proc.returncode != 0:
        # Not running, gone, or Docker would not: the caller's stop does what it always did.
        logger.info(f"{world} was not sent a stop of its own ({proc.stderr.strip()})")
        return
    started = _save_clock()
    last_moved = started
    moved: int | None = None
    run = ""
    saying = False

    while True:
        if control.abandon.is_set():
            logger.warning(f"gave up watching {world} while it saved; it was not killed")
            raise SaveAbandoned(WORLD_SAVE_ABANDONED)
        state = container_state(world, timeout=_LOAD_LOOK_TIMEOUT, wsl_distro=wsl_distro)
        if state.missing or (state.status and not state.settled):
            # T414: "saved" only for a clean exit; a crash on the way out is said, whether or
            # not the save was said to have begun.
            ended, warn = _how_the_world_ended(world, wsl_distro)
            if ended == WORLD_SAVED and not saved_first:
                # T496: the save asked for first did not happen or did not finish, and the
                # warning said so; "saved every character" here would take that back.
                ended = WORLD_CLOSED
            if saying or (warn and ended != WORLD_SAVE_UNREAD):
                # Before the first look nothing was claimed, so an exit that cannot be
                # read claims nothing either; a known crash is said either way.
                say(ended, warn=warn)
            return
        now = _save_clock()
        if not state.settled:
            if not run:
                # Never seen running since the signal, and Docker will not say: the caller's
                # stop goes on as it always did, with its grace.
                logger.info(f"could not see whether {world} is saving; leaving it to the stop")
                return
            # Docker did not answer this time: that is not silence either.
            last_moved = now
        else:
            if run and state.started_at != run:
                logger.warning(f"{world} restarted while it saved; leaving it to the stop")
                return
            run = state.started_at
            if not saying:
                saying = True
                say(WORLD_SAVING)
            look = exec_output(
                world, ["cat", "/proc/net/dev"], timeout=_LOAD_LOOK_TIMEOUT, wsl_distro=wsl_distro
            )
            count = _bytes_moved(look.stdout) if look.returncode == 0 else None
            if count is None or count != moved:
                # Moved, or could not be read: neither is silence.
                last_moved = now
                moved = count if count is not None else moved
        verdict = ""
        if now - started >= WORLD_SAVE_CEILING_SECONDS:
            verdict = WORLD_SAVE_TOO_LONG
        elif now - last_moved >= WORLD_SAVE_STALL_SECONDS:
            verdict = WORLD_SAVE_STALLED
        if verdict:
            say(verdict, warn=True)
            try:
                kill_container(world, wsl_distro=wsl_distro)
            except DockerCommandError as exc:
                # Gone in the meantime, or Docker would not: the caller's stop follows.
                logger.warning(f"could not kill {world}: {exc}")
            return
        control.abandon.wait(_SAVE_POLL_SECONDS)


def _pause(control: StopControl, seconds: float) -> None:
    """Sleep between looks, but wake within a quarter of a second for the caller's events."""
    deadline = time.monotonic() + seconds
    while not (control.forced() or control.abandon.is_set()):
        left = deadline - time.monotonic()
        if left <= 0:
            return
        control.abandon.wait(min(left, 0.25))


def wait_for_the_world_to_load(
    spec: ContainerSpec,
    control: StopControl | None = None,
    *,
    wsl_distro: str | None = None,
) -> bool:
    """Before a stop: wait until the world server can hear it (T158).

    The ONE wait every stop path goes through -- `stop_staged()`,
    `remove_staged()`, `stop_servers_staged()`, `recreate_staged()` and
    `stop_containers()` call it immediately before the command that signals
    the world, so no Stop, Restart, Recreate, Remove, Uninstall, rebuild,
    rollback or stop-the-other-server can reach a loading world without it.
    It does nothing unless `spec.stop_waits_for_load`, which only the CMaNGOS
    games' specs are.

    Why: measured on a Linux gate box, 2026-09-27 (Vanilla, 500 bots), mangosd
    is the container's PID 1 and its SigCgt was `0000000100000000` (no SIGTERM,
    no SIGINT) at +3, +8, +15 and +25 s of its load, `0000000100004002` once
    loaded. A namespace's init gets no default action for a signal it has no
    handler for, so the SIGTERM a `compose stop` sent 15 s in was dropped: the
    world loaded, ran, and was SIGKILLed when the grace ended -- 280.5 s, exit
    137, nothing saved on the way out. Loaded, it stopped cleanly in 22.2 s.
    The owner's decision the same day: wait for the load, then stop; never
    kill a world mid-load, which is when it runs its start-up migrations.

    **What is waited for is that fact itself**: bit 15 of PID 1's SigCgt, read
    with `docker exec <world> cat /proc/1/status`. Not a log line. A log line
    stands in for the handler only if the core prints it after `_HookSignals()`,
    and Tortoise's ready banner ("World server is up and running!") is the
    last line of `SetInitialWorldSettings()`, printed BEFORE the hook. The mask
    cannot be early, and reading it costs no log.

    Each look is a `docker inspect` and then a `docker exec`, each bounded by
    `_LOAD_LOOK_TIMEOUT`. It ends on the first of:

    * `control.abandon` -- raises `StopAbandoned` and sends nothing. Read
      before every look.
    * the container is not running (`docker inspect` says so: exited,
      restarting, dead, or no such container) -- there is no process to
      ignore anything, and `docker stop` cancels a pending restart, so the
      stop goes at once.
    * SIGTERM is caught -- the stop goes now. Said only after a wait was said.
    * the world cannot hear the stop and its log ENDS on a failed world update (T600): the
      core waits in a read on its console there, so nothing is loading and the signal would
      be ignored for the whole grace. The world is killed and the stop goes on, saying why.
    * the world was looked at and was NOT seen able to hear the stop, and
      `control.forced()` -- "Stop now anyway". Said as a warning, because the
      stop that follows may be the forced one. A press never makes it skip the
      look: a world that can hear the stop is stopped with no warning, however
      the stop was asked for.

    **Nothing else ends it** (round 3: fail closed). A look that cannot be read
    while the world is running -- the `exec` failing or timing out, no SigCgt
    line, an `inspect` that does not answer -- is neither "able to hear" nor
    "gone"; the wait says `WORLD_LOAD_UNCHECKED` and goes on looking. A NEW run
    (`StartedAt` changed: `restart: unless-stopped` brought a dead one back) is
    loading again, deaf again, and is waited for in its turn -- unless
    `control.restart_ends_the_wait` (T159), where a new run is a crash loop and
    the stop goes at once.

    **There is no time limit.** A cap would send, at a moment chosen by a
    number, the very signal this exists to hold back -- and a measured valid
    first boot (TBC, 46 minutes on a 9p share) outlasts any cap short enough
    to matter. Only the person decides to force it.

    Returns False when it ended on `WORLD_STOPPED_ANYWAY`: the world may still be
    deaf to the stop, so `save_then_stop_the_world()` neither signals it nor reads
    its traffic as saving -- the load's own database traffic would hold a forced
    stop for half an hour (T384 cold review). True otherwise.
    """
    heard = True
    for text in world_load_steps(spec, control, wsl_distro=wsl_distro):
        if text == WORLD_STOPPED_ANYWAY:
            heard = False
        if control is not None and control.say is not None:
            control.say(text)
    return heard


def world_load_steps(
    spec: ContainerSpec,
    control: StopControl | None = None,
    *,
    wsl_distro: str | None = None,
) -> Iterator[str]:
    """`wait_for_the_world_to_load()` as a generator of its sentences, one loop in two shapes.

    `wait_for_the_world_to_load()` hands each sentence to `control.say`; a
    caller that yields its lines can iterate this instead. Every sentence is
    logged here. Raises `StopAbandoned` when `control.abandon` is set.
    """
    if not spec.stop_waits_for_load:
        return
    control = control or StopControl()
    world = spec.world
    said = ""
    run = ""

    def say(text: str, *, warn: bool = False) -> Iterator[str]:
        nonlocal said
        if text != said:
            said = text
            (logger.warning if warn else logger.info)(text)
            yield text

    while True:
        if control.abandon.is_set():
            raise StopAbandoned(
                f"The stop was not sent: it was given up while {world} was still loading, so "
                "the world was left running."
            )
        state = container_state(world, timeout=_LOAD_LOOK_TIMEOUT, wsl_distro=wsl_distro)
        if state.missing or (state.status and not state.settled):
            if said:
                logger.info(f"{world} is no longer running ({state.status}); stopping at once")
            return
        caught: bool | None = None
        if state.settled:
            if run and state.started_at != run:
                if control.restart_ends_the_wait:
                    logger.info(f"{world} restarted while it was waited on; stopping it at once")
                    yield WORLD_RESTARTED_STOPPING
                    return
                logger.info(f"{world} restarted while it was waited on; waiting on its new run")
            run = state.started_at
            proc = exec_output(
                world, ["cat", "/proc/1/status"], timeout=_LOAD_LOOK_TIMEOUT, wsl_distro=wsl_distro
            )
            if proc.returncode == 0:
                caught = _sigterm_caught(proc.stdout)
        if caught:
            if said:
                logger.info(WORLD_FINISHED_LOADING)
                yield WORLD_FINISHED_LOADING
            return
        if control.forced():
            logger.warning(WORLD_STOPPED_ANYWAY)
            yield WORLD_STOPPED_ANYWAY
            return
        if caught is False and state.settled:
            stuck = _stuck_at_a_failed_update(world, run, wsl_distro)
            if stuck:
                # Deaf and not loading: tortoise-wow waits in a read on its console after a
                # failed update (T600), so the stop's SIGTERM is ignored for the whole grace.
                # Nothing is loading and nothing was saved yet, so the kill loses nothing --
                # but only for THIS run: a replacement the restart policy started since the
                # look is loading and must be waited for, so the run is checked again first.
                again = container_state(world, timeout=_LOAD_LOOK_TIMEOUT, wsl_distro=wsl_distro)
                if again.settled and again.started_at == run:
                    logger.warning(
                        f"{world} is stuck at a failed update; killing it, then stopping"
                    )
                    try:
                        kill_container(world, wsl_distro=wsl_distro)
                    except DockerCommandError as exc:
                        logger.warning(f"could not kill {world}: {exc}")
                    yield stuck + WORLD_STUCK_AT_UPDATE_TAIL
                    return
                logger.info(f"{world} changed run while it was looked at; looking again")
        if caught is None:
            logger.warning(f"could not read whether {world} can hear a stop; asking again")
            yield from say(WORLD_LOAD_UNCHECKED, warn=True)
        else:
            yield from say(WORLD_STILL_LOADING)
        _pause(control, _LOAD_POLL_SECONDS)


def _stuck_at_a_failed_update(world: str, run: str, wsl_distro: str | None) -> str:
    """`update_failure.explain()`'s sentence when run `run`'s log ENDS on a failed update, or `""`.

    Only the last non-empty line counts: the core goes quiet after `failed to apply.` (it waits in
    a read), while a world that went on printing is a world that is going on (T600).
    """
    tail = _logs(world, this_run_only=True, since=run, tail=_UPDATE_LOG_TAIL, wsl_distro=wsl_distro)
    lines = [line for line in tail.splitlines() if line.strip()]
    if not lines:
        return ""
    last = lines[-1]
    if not (update_failure.FAILED.search(last) or update_failure.CLOSED.search(last)):
        return ""
    return update_failure.explain(tail)


def stop_containers(
    containers: list[str],
    *,
    wsl_distro: str | None = None,
    known: Sequence[ContainerSpec] = (),
    control: StopControl | None = None,
    deadline: float | None = None,
) -> None:
    """Stop these containers, the worldserver-ish ones first.

    The public form of `_run_docker_stop()`, for the case where the containers
    are NOT ours: another install is holding the ports and the user has asked
    for it to be stopped so this one can start.

    Order matters and cannot be read off a name with certainty, so this is a
    best effort rather than a promise: anything whose name mentions the world is
    stopped before anything that mentions the database, because a worldserver
    losing its database mid-save is how character saves are lost. For a container
    this project did not create, the name is the only hint there is.

    `known` is the specs the caller can put names to — the catalogue's, for the
    port-conflict offer. A container that is the `world` of one of them gets
    that game's `wait_for_the_world_to_load()` before its stop, because the
    server holding the ports may be one that was started a minute ago and is
    still loading (T158). A name nobody knows is stopped as before.

    `deadline` bounds each `docker stop` command (`_run_docker_stop()`), never
    the load wait before it, which T158 leaves to the person.
    """

    def rank(name: str) -> int:
        lowered = name.lower()
        if any(hint in lowered for hint in ("world", "mangosd")):
            return 0
        if any(hint in lowered for hint in ("auth", "realmd", "logon")):
            return 1
        return 2

    for name in sorted(containers, key=rank):
        for spec in known:
            if spec.world == name:
                heard = wait_for_the_world_to_load(spec, control, wsl_distro=wsl_distro)
                save_then_stop_the_world(spec, control, wsl_distro=wsl_distro, heard=heard)
                break
        _run_docker_stop(name, wsl_distro=wsl_distro, deadline=deadline)


def kill_container(container: str, *, wsl_distro: str | None = None) -> None:
    """`docker kill <container>`: the last resort after a stop that failed (T162).

    Raises `DockerCommandError` on a non-zero exit, a container that is not
    running included: the caller reads the container's state afterwards
    rather than trusting any exit code, so the words of Docker's refusal need
    not be matched here.
    """
    _run(["kill", container], wsl_distro=wsl_distro)


@_a_lifecycle_command
def stop_staged(
    spec: ContainerSpec,
    server_dir: Path,
    *,
    wsl_distro: str | None = None,
    control: StopControl | None = None,
) -> bool:
    """Stop this install without destroying its containers.

    The counterpart to `start_staged()`. `docker compose down` *removes* the
    containers, which is why it is not used here: a stop that removes leaves the
    next start with nothing to reuse. `compose stop` keeps them and walks the
    project's own `depends_on` graph, so the servers close their connections
    before the database goes away — upstream AzerothCore declares that graph and
    it is not ours to restate.

    The fallback stops this install's containers by name, one call at a time and
    in reverse order, for a project whose compose files cannot be read. Losing
    the ability to stop a running server because a file is missing would be a
    worse failure than losing the ordering guarantee. One call per container is
    not decorative: `docker stop a b c` signals all three at once (measured at
    6.19s for three containers where the *first* traps SIGTERM for 6s), so a
    multi-name call cannot express "world before the database".

    Both paths ask for `STOP_GRACE_SECONDS` instead of taking Docker's
    10-second default, which was measured SIGKILLing a populated worldserver
    while it was still flushing its character save queue. A Stop can therefore
    take minutes rather than seconds, and every caller of this runs it off the
    GUI thread already.

    A world that cannot hear a stop while it loads is waited for first
    (`wait_for_the_world_to_load()`, T158), so a Stop pressed during a
    CMaNGOS load takes the rest of the load plus the ~22 s shutdown rather
    than the full grace and a SIGKILL. `control` is how the caller hears that
    wait and steers it (`StopControl`).

    Then the world is stopped on its own and waited for while it saves
    (`save_then_stop_the_world()`, T384) -- the grace SIGKILLed one mid-save --
    and only then does `compose stop` take the rest down. A give-up during that
    save raises `SaveAbandoned` and leaves the database up.

    Either way the result is verified rather than assumed. A zero exit from
    `compose stop` only means compose had nothing to complain about — it says
    so even for a project where nothing was running, and even where the
    containers holding these names belong to a different install. So the census
    is taken from `docker ps` plus the compose project label, once before the
    stop and again after it, and the exit code is never the answer.

    Returns:
        True if something of this install was running and is now down; False if
        there was nothing of it to stop. The old version returned `compose
        stop`'s exit code, which is 0 for an empty project — it said "stopped"
        having stopped nothing (review, 2026-08-22).

    Raises:
        DockerCommandError: Ownership could not be established, or something of
            this install is still running after the stop. Reporting success in
            either case would be the worst outcome: the user is told the server
            is down while players are still connected.
    """
    logger.debug(f"stop_staged() called: server_dir={server_dir}")
    project = install_project(spec, server_dir, wsl_distro=wsl_distro)
    if project is None:
        _refuse_without_an_identity(spec, server_dir, wsl_distro=wsl_distro)
        return False

    # Look before touching anything. Taking the census first is what lets the
    # refusals below happen before a `compose stop`, and what makes the return
    # value mean "there was something of ours and it is now down" rather than
    # "compose had nothing to complain about" (review, 2026-08-22).
    before = _running(spec, project, wsl_distro=wsl_distro)
    if before.unreadable:
        raise DockerRefusal(
            f"Docker would not say which project owns {', '.join(before.unreadable)}, so this "
            f"install in {server_dir} cannot prove those containers are its own. Nothing was "
            "stopped. This is usually Docker being unwell rather than a second install — try "
            "again in a moment."
        )
    if before.strangers:
        raise _stranger_refusal(before.strangers, project, server_dir)

    # No pin is written here, though the census has just proved the basename and
    # the labels agree. Writing it down was tried and reverted the same day: a
    # pin lives in `.env`, `.env` travels with the folder, and `install_project()`
    # prefers it over the directory — so copying an install (a second realm, a
    # restored backup) hands the copy the original's identity, and pressing Stop
    # in the copy stops the ORIGINAL's running server. Measured end to end.
    #
    # Not writing one does NOT make the copy safe, and an earlier version of
    # this comment claimed it did. An unpinned copy resolves to its own
    # basename, which catches `~/wow` copied to `~/wow2` and misses
    # `~/wow-server` copied to `/mnt/backup/wow-server` — same basename, same
    # project, same outcome. And the install-time pin in
    # `catalog_view._on_run_finished()` is inherited by any copy of a folder it
    # wrote, so that path carries the hazard too. This only declines to ADD a
    # third way in (review, 2026-08-23). The candidate real fix — reading the
    # containers' `com.docker.compose.project.working_dir` label and checking
    # whether that directory still exists, which separates a move from a copy —
    # is recorded in `pyplan/phase6-decisions.md`, not implemented here.
    #
    # The cost of not pinning is that an attached install whose compose files
    # later become unreadable cannot prove ownership, and
    # `_refuse_without_an_identity()` says so rather than guessing. That is the
    # right way round: refusing to stop a server is recoverable, stopping
    # somebody else's is not.

    # Run this even with nothing of ours in `docker ps`. The project holds more
    # than the three named containers — `ac-db-import` and `ac-client-data-init`
    # are services too — and an install interrupted during the multi-GB client
    # data download leaves one of those running. Skipping the command told the
    # user "nothing was running" while the download carried on (review).
    # `--timeout`, because compose's default is Docker's 10s and that was
    # measured SIGKILLing a populated worldserver mid-save (see
    # `STOP_GRACE_SECONDS`). It is per container, not for the whole project, and
    # only the worldserver ever comes close to using it.
    # `-t` for the same reason as `_run_docker_stop()`; compose has always
    # accepted the short form, and spelling the two call sites alike means a
    # future reader does not have to know which CLI they are looking at.
    #
    # After the census, so a refusal is never kept waiting for a load; before
    # the command, because a CMaNGOS world signalled mid-load drops the SIGTERM
    # and sits out the whole grace to a SIGKILL (T158).
    if spec.world in before.ours:
        heard = wait_for_the_world_to_load(spec, control, wsl_distro=wsl_distro)
        # T384: the world alone first, waited for while it saves. The fixed grace below
        # SIGKILLed a world mid-save on a slow disk; it now reaches only the rest.
        save_then_stop_the_world(spec, control, wsl_distro=wsl_distro, heard=heard)
    proc = _docker(
        ["compose", "stop", "-t", str(STOP_GRACE_SECONDS)], cwd=server_dir, wsl_distro=wsl_distro
    )
    if proc.returncode != 0:
        logger.warning(f"compose stop failed ({proc.stderr.strip()}); stopping containers by name")
    if not before.ours:
        # False means "none of the three SERVERS were up". It cannot mean
        # "nothing was stopped": the `compose stop` above may well have stopped
        # a one-shot service, and compose does not say what it touched. The
        # caller's wording has to match that (review, 2026-08-22).
        logger.info("stop_staged(): none of this install's servers were running")
        return False

    after = _running(spec, project, wsl_distro=wsl_distro)
    if after.ours:
        # Either `compose stop` failed, or it succeeded having matched nothing —
        # the moved-folder case, where compose names the project after the
        # directory. Finish the job by name rather than believing the exit code.
        logger.warning(f"compose stop left {list(after.ours)} running; stopping by name")
        for name in after.ours:
            _run_docker_stop(name, wsl_distro=wsl_distro)
        after = _running(spec, project, wsl_distro=wsl_distro)

    if after.ours:
        raise DockerCommandError(f"still running after stop: {', '.join(after.ours)}")
    if after.unreadable:
        # Reading only `.ours` here let a container that is plainly still up be
        # reported as stopped, because `docker inspect` had started failing and
        # dropped it into `unreadable` — the same condition that is a hard
        # refusal three lines above, silently discarded (review, 2026-08-22).
        raise DockerRefusal(
            f"{', '.join(after.unreadable)} are still running and Docker will no longer say "
            "which project owns them, so this stop cannot be confirmed. Do not take the server "
            "as down until Docker's list of running containers leaves them out.",
            detail="Docker's list of running containers: docker ps",
        )
    logger.info("stop_staged(): stopped; containers kept for a fast restart")
    return True


def status(*, wsl_distro: str | None = None) -> list[str]:
    """Return the names of all currently-running containers (`docker ps`).

    Raises:
        DockerCommandError: If the `docker` CLI itself fails (e.g. the daemon
            is unreachable). Callers polling in a loop (see `wait_ready()`)
            must not call this directly without handling that — use
            `_status_safe()`/the polling helpers below instead.
    """
    logger.debug("status() called")
    proc = _run(
        ["ps", "--format", "{{.Names}}"], timeout=STATUS_TIMEOUT_SECONDS, wsl_distro=wsl_distro
    )
    return [name for name in proc.stdout.splitlines() if name.strip()]


def _status_safe(*, wsl_distro: str | None = None) -> list[str] | None:
    """Like `status()`, but returns `None` instead of raising on a *transient* failure.

    Used by polling loops (`wait_ready()`) where a single `docker ps` failure
    (daemon restart, brief overload, etc.) must be treated as "not ready this
    iteration, try again," not as a reason to abort the whole wait.

    `DockerCliMissingError` is not one of those and is re-raised. Degrading it
    here was measured doing real harm (2026-08-23): the two stop-path callers
    turn `None` into their own message, so a Windows box with no Docker was told
    its install had no `COMPOSE_PROJECT_NAME` pinned, and `wait_ready()` treated
    "there is no Docker on this machine" as a hiccup worth retrying 240 times.
    Every caller that wants the degradation still gets it for the failure it was
    written for.
    """
    try:
        return status(wsl_distro=wsl_distro)
    except DockerCliMissingError:
        raise
    except DockerCommandError as exc:
        logger.debug(f"status() failed during poll, will retry: {exc}")
        return None


def health(container: str, *, wsl_distro: str | None = None) -> str:
    """Return a container's health status, or `"unknown"` if it can't be read.

    `"unknown"` is deliberately overloaded and covers several distinct cases
    that cannot be told apart from this return value alone: the container
    doesn't exist yet, the daemon is unreachable, *and* the container exists
    and is running fine but simply has no `HEALTHCHECK` defined (a common,
    valid configuration — `docker inspect`'s health-status template fails in
    that case too). Callers relying on a `"healthy"` result to mean "the
    container is up" should be aware an unhealthchecked container will never
    report `"healthy"` and will look identical to "container missing."
    Mirrors `dml-start.sh`'s `... || echo unknown`.
    """
    return _health(container, wsl_distro=wsl_distro)[0]


def _health(container: str, *, wsl_distro: str | None = None) -> tuple[str, bool]:
    """`health()`'s answer, plus whether there was a docker CLI to ask.

    Split out so `wait_db_healthy()` can tell "no Docker on this machine" from
    every other reason the status is `"unknown"` without spending a second
    command per poll to find out. `health()`'s own answer still collapses the
    two, deliberately — see there — so this is additive, not a contract change.
    """
    logger.debug(f"health() called: container={container}")
    proc = _docker(
        ["inspect", container, "--format", "{{.State.Health.Status}}"], wsl_distro=wsl_distro
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return "unknown", _cli_missing(proc)
    return proc.stdout.strip(), False


@dataclass(frozen=True)
class ContainerState:
    """A container's status, the start time of its current run, and its restart count."""

    status: str = ""
    started_at: str = ""
    restart_count: int = 0
    missing: bool = False
    """Docker ANSWERED, and said there is no container by this name (T95).

    `status` stays `""` either way, because every other reader takes `""` to
    mean "could not be read" and must keep doing so. Only the dashboard tells
    the two apart. Asked of the wrong daemon (a WSL install asked of the
    host), a live container also reads as missing (`native.py:2170-2175`),
    so every caller that means it must pass `wsl_distro`.
    """

    @property
    def settled(self) -> bool:
        """True if it is running right now, rather than between restarts.

        Every compose service here carries `restart: unless-stopped`, and a
        container in restart backoff still appears in a plain `docker ps` while
        `.State.StartedAt` holds the previous run's timestamp. A crash-looping
        worldserver therefore satisfies both of `wait_ready()`'s other checks
        and is reported ready — the same false pass the `--since` scoping was
        added to remove, arriving by a different route (review, 2026-08-22).
        """
        return self.status == "running"


def container_state(
    container: str, *, timeout: float | None = None, wsl_distro: str | None = None
) -> ContainerState:
    """Status, current-run start time and restart count in ONE `docker inspect`.

    One call, not two, because `wait_ready()` asks for all of it every two
    seconds for up to eight minutes and a `docker inspect` costs about 0.3s of
    CLI startup on its own — two of them per container per poll took a healthy
    poll from five docker invocations to seven, enough to overrun the interval
    it names (review, 2026-08-22). `RestartCount` rides along for the same
    price: it is how `wait_ready()` tells a crash loop from a slow start.

    A short answer still parses. Only this module writes the format string, but
    an unreadable count (an older docker, a fake in a test) must not turn a
    running container into an empty `ContainerState` — the missing fields are
    read as `""`/`0`, which is what a container that has never restarted says.

    `timeout` bounds the `docker inspect` (None: unbounded, as it always was).
    A run that times out is a non-zero result, so it reads as unreadable. The
    stop's load wait passes one (T158): a wedged daemon must not hold a stop
    on a question it will never answer.
    """
    fmt = "{{.State.Status}}\t{{.State.StartedAt}}\t{{.RestartCount}}"
    proc = _docker(["inspect", container, "--format", fmt], timeout=timeout, wsl_distro=wsl_distro)
    if proc.returncode != 0:
        _note_unread(container, wsl_distro, proc.stderr.strip())
        missing = not _cli_missing(proc) and bool(_NO_SUCH_CONTAINER.search(proc.stderr))
        return ContainerState(missing=missing)
    _note_read(container, wsl_distro)
    fields = [part.strip() for part in proc.stdout.strip().split("\t")]
    status, started, count = (fields + ["", "", ""])[:3]
    return ContainerState(status, started, int(count) if count.isdigit() else 0)


def daemon_identity(*, wsl_distro: str | None = None) -> str:
    """Which start of the Docker daemon is answering: its default bridge network's ID (T306).

    Measured on yulon-ubuntu, Docker 29.1.3: the daemon deletes and recreates
    the default `bridge` network every time it starts (with live-restore off,
    the default here and on Docker Desktop), so its `Id` changes and its
    `Created` is the daemon's start time; `docker info`'s `ID` does not change.
    `""` when it cannot be read (no daemon, no `bridge` network as with Windows
    containers): the caller must then not assume either way.
    """
    proc = _docker(["network", "inspect", "bridge", "--format", "{{.Id}}"], wsl_distro=wsl_distro)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def world_running(container: str, *, wsl_distro: str | None = None) -> bool | None:
    """Is this install's worldserver up? THREE-valued, and `None` is not "no".

    The seam `apply.Applier`'s running-world guard asks (`apply.py`,
    `_refuse_direct_sql_into_a_running_world`), written once here rather than
    four times in four games' factories, because the mapping below is the whole
    of it and four copies would drift.

    Why not `container_state(...).settled`, which the tree already had at
    `controller_view.py`'s My Party group: `container_state()` returns an EMPTY
    `ContainerState` when Docker will not answer, and `.settled` turns that into
    `False`. Through this guard `False` is fail-OPEN — "not running" is the one
    answer that lets SQL into a live world's tables — so an unreadable inspect
    is `None` here, which the guard refuses on. T2's press wrote this mapping by
    hand for exactly that reason and left it as a note for whoever wired the
    seam (`8.7a-direct-sql-yulon-ubuntu2-2026-09-09/README.md`, last section).

    `restarting` counts as running, for the reason
    `controller_wow_tortoise.autoupdate`'s own guard counts it: a container in
    restart backoff is on its way back up, and its next start is the one that
    would read these tables.

    ONLY the terminal statuses read as down: `exited`, `dead`, `created` -- a
    container with nothing running has nothing resident to hold these tables.
    Everything else is `True`, `paused` included (Codex, T20, on `a6e2aff6`):
    a paused worldserver still keeps its whole database-backed state resident
    in memory and can be unpaused to write it straight back over whatever
    direct SQL just changed underneath it, which is the gap `paused: False`
    left open. Named the other way around -- listing every word that counts as
    up -- the next status Docker adds (`removing` already exists; there will be
    others) would fall through to `False` and reopen the same gap until
    someone noticed and added it by hand. Inverted, an unrecognised status
    reads as running and the guard is asked, which is what its own `None`
    branch above already prefers to a silent downgrade.
    """
    status_text = container_state(container, wsl_distro=wsl_distro).status
    if not status_text:
        return None
    return status_text not in ("exited", "dead", "created")


def started_at(container: str, *, wsl_distro: str | None = None) -> str:
    """When the container's CURRENT run began, or `""` if it cannot be read."""
    return container_state(container, wsl_distro=wsl_distro).started_at


def _logs(
    container: str,
    *,
    this_run_only: bool = False,
    since: str = "",
    until: str = "",
    tail: int | None = None,
    wsl_distro: str | None = None,
) -> str:
    """Return a container's logs, or `""` if they can't be read.

    `docker logs` prints everything the container has ever written, across every
    restart. That is the right default for showing a user what happened, and
    exactly wrong for deciding whether a server is ready *now*: a restarted
    worldserver still has the previous run's `ready...` sitting in its log, so a
    readiness check reads it and says yes while the new run is still loading.

    Measured on a real AzerothCore server (2026-08-22): after a stop/start
    cycle, `wait_ready()` returned True immediately, and the container's own
    last words were still `>> Loaded 13567 Quest Offer Reward Locale Strings` —
    it was mid-startup, and the stop that followed killed it there (exit 137).

    `this_run_only` scopes the read to the current run by asking when that run
    started; `until` ends it there (`docker logs --until`). `--tail` is not an
    alternative: the marker is printed once, so a tail window either misses it or
    slides past it. `tail` is for the opposite question, what a run's log ENDS on
    (T600: a world stuck at a failed update), which a window answers exactly.
    """
    argv = ["logs"]
    if this_run_only:
        # The caller may already have the start time from the state read it
        # had to do anyway; asking again is a second `docker inspect`.
        since = since or started_at(container, wsl_distro=wsl_distro)
        if since:
            argv += ["--since", since]
    if until:
        # T581: a bounded read, for a marker printed early in a run that may be days long.
        argv += ["--until", until]
    if tail is not None:
        argv += ["--tail", str(tail)]
    proc = _docker([*argv, container], wsl_distro=wsl_distro)
    if proc.returncode != 0:
        # Silently returning "" turned a rejected --since, a container removed
        # mid-wait, or an unreadable log driver into eight minutes of "starting"
        # followed by a generic failure with nothing naming the cause
        # (review, 2026-08-22). `_status_safe()` already logs its equivalent.
        logger.warning(f"could not read the logs of {container}: {proc.stderr.strip()}")
        return ""
    return proc.stdout


@dataclass(frozen=True)
class RunLog:
    """A container's log, and whether it is only its CURRENT run's (T144).

    `this_run_only` is False when the run's start time could not be read, and
    then `text` is every run the container has had: a reader deciding what
    THIS run did must not take an older run's line for it.
    """

    text: str
    this_run_only: bool


def current_run_log(container: str, *, wsl_distro: str | None = None) -> RunLog:
    """The current run's log (`docker logs --since <started_at>`), said as such, or all of it.

    `_logs(this_run_only=True)` falls back to the whole history, silently,
    when `started_at()` answers `""`; this says which one it was, so the
    caller can fail closed. `""` text when the log could not be read at all.
    """
    since = started_at(container, wsl_distro=wsl_distro)
    text = _logs(container, this_run_only=bool(since), since=since, wsl_distro=wsl_distro)
    return RunLog(text, this_run_only=bool(since))


_CLI_MISSING_GRACE_SECONDS = 30.0
"""How long a poll keeps going with no docker CLI before it gives up.

Neither zero nor the full timeout, and both extremes were the wrong answer.

Zero — stop the first time the CLI does not resolve — would fight the cache
design in `platform.docker_program()`, which refuses to remember a miss
precisely so that Docker arriving mid-run is picked up. It would also turn a
Docker Desktop self-update, which replaces the executable the pinned path names,
into a failed start.

The full timeout is what this constant exists to end: 480s of `wait_ready()`
polling for a container that cannot appear, because there is nothing to ask.

30s is `_ensure_docker_linux()`'s own `min(wait_seconds, 30.0)` — the window the
provisioner already believes is enough for a Docker it just installed to become
answerable. Borrowing it keeps one number instead of inventing a second, and a
CLI that has not appeared inside the window the installer is given is not going
to appear because we waited another 450 seconds in silence.
"""


def _cli_missing_run(since: float | None, what: str) -> tuple[float, bool]:
    """Track a poll's run of consecutive missing-CLI iterations; True once it should stop.

    The stopping rule for both polling loops, in one place (style-guide §4).
    Pass the previous return's timestamp (or `None` on the first miss) and get
    back the timestamp to carry forward plus whether to give up.

    The user learns the cause once. WARNING on the first miss and on the
    decision to stop, DEBUG for everything between: `wait_ready()` issues five
    docker commands per poll for up to 480s, and 1200 copies of the same
    sentence is how a log stops being read.
    """
    now = time.monotonic()
    if since is None:
        logger.warning(f"{what}: {platform.DOCKER_CLI_MISSING_HELP}")
        return now, False
    waited = now - since
    if waited < _CLI_MISSING_GRACE_SECONDS:
        logger.debug(f"{what}: still no docker CLI after {waited:.1f}s; waiting")
        return since, False
    logger.warning(
        f"{what}: no docker CLI for {waited:.0f}s, so nothing can be watched or started here. "
        "Giving up rather than polling out the rest of the timeout in silence."
    )
    return since, True


def wait_db_healthy(
    db_container: str,
    timeout: float = _DB_HEALTHY_TIMEOUT_SECONDS,
    interval: float = _POLL_INTERVAL_SECONDS,
    *,
    wsl_distro: str | None = None,
) -> bool:
    """Poll `health()` until the DB container reports `healthy` or time out.

    Mirrors `dml-start.sh`'s `_wait_db_healthy` (~90 checks at 2s), generalized
    with explicit timeouts so callers/tests aren't forced to sleep. Never
    raises `DockerCommandError` — `health()` already swallows CLI failures
    into `"unknown"`, which this treats as "not yet healthy."

    The one failure it does not treat that way is "there is no docker CLI on
    this machine": `"unknown"` is then not a container that is still starting,
    it is a question nobody was asked, and waiting 180s for the answer to change
    without saying why is not a diagnosis. See `_cli_missing_run()`.

    Note: worst-case wall-clock time before returning `False` is
    `timeout + interval` (the loop always sleeps once after its final check),
    not exactly `timeout`.

    Args:
        interval: Must be positive. A zero/negative interval would busy-loop
            the `docker` CLI with no delay; this is asserted, not silently
            allowed.
    """
    if interval <= 0:
        raise ValueError(f"interval must be positive, got {interval!r}")
    logger.debug(f"wait_db_healthy() called: db_container={db_container} timeout={timeout}")
    deadline = time.monotonic() + timeout
    cli_missing_since: float | None = None
    while time.monotonic() < deadline:
        status, cli_missing = _health(db_container, wsl_distro=wsl_distro)
        if cli_missing:
            cli_missing_since, give_up = _cli_missing_run(cli_missing_since, "wait_db_healthy()")
            if give_up:
                return False
        else:
            cli_missing_since = None
            if status == "healthy":
                return True
        time.sleep(interval)
    return False


@dataclass(frozen=True)
class ReadySpec:
    """What "the server is up" means for one game, as data (phase7-decisions, Ready).

    Every string is a regex applied with `re.search` to the CURRENT run's log
    only. `auth=None` means the game has no separate auth container worth
    waiting on; `fatal` names a line that means the server will never become
    ready, so the wait ends at once instead of polling out its timeout;
    `restart_loop` is how much `RestartCount` may grow before a crash loop is
    declared. The AzerothCore values come from `azerothcore_ready()`, every
    other family's from `catalog.json`'s `ready` block (the spine `re.escape`s
    those unless the block says `regex: true` — contract amendment A5).

    On the two timeouts: this default is `_READY_TIMEOUT_SECONDS` (480s), which
    is what `dml-start.sh`'s `_wait_ready` has always waited — 240 polls at 2s —
    while `catalog.ReadyMarkers.timeout_s` defaults to 600. They do not need to
    agree and neither was adopted over the other. A spec built from a catalogue
    `ready` block passes `timeout=ready.timeout_s` explicitly, so the data wins
    whenever there is data; this default covers only a `ReadySpec` written in
    code — `azerothcore_ready()` and the tests — and keeping it at 480 leaves
    the legacy AzerothCore wait exactly as long as it has always been.
    """

    world: str
    auth: str | None = None
    fatal: str | None = None
    timeout: float = _READY_TIMEOUT_SECONDS
    interval: float = _POLL_INTERVAL_SECONDS
    restart_loop: int = 4
    cancel: threading.Event | None = field(default=None, compare=False, repr=False)
    """The job's Stop (T247): once set, `wait_ready()` ends at its next look, answering False.

    Not part of what "ready" means, so it takes no part in comparing two specs
    or in the one-line form the wait logs. Carried here rather than as an
    argument because every ready-wait seam already takes `(spec, ready)`, and
    the one that must hear it is the real `wait_ready()` underneath them. Until
    T247 a Stop pressed during the ready wait was acted on only when the wait
    ended by itself: 83 s on m910q (2026-10-05), on a crash loop's verdict.
    """


AZEROTHCORE_READY_WORLD = "ready..."
"""What an AzerothCore worldserver prints once the world is loaded; the legacy marker.

Verified against the three shipped installers (`wait_for_server` greps
`ready\\.\\.\\.` in the worldserver log), `dml-start.sh`'s `_wait_ready`, and
`catalog.json`'s `install.native.ready.world` — the same literal in all four.

A LITERAL, which is why `azerothcore_ready()` `re.escape`s it before putting it
in a `ReadySpec`: the four greps that verify it escape the dots too, and
unescaped it matches `already up-to-date` in a log that has no ready banner.
"""


def azerothcore_ready(realm_host: str, realm_port: int, **kwargs: float) -> ReadySpec:
    """The AzerothCore `ReadySpec`: auth serving `<host>:<port>`, world saying `ready...`.

    The `**kwargs: float` shape is kept for `controller.wait_ready()` and
    `docker_ctl.wait_server_ready()`, whose callers forward `timeout`/`interval`
    exactly as they always did. Only those two are accepted: `restart_loop` is
    an int and anything else is a typo, so both are refused rather than dropped.

    BOTH markers are escaped here, always. The host is the obvious one — it is
    a literal, not a regex. The world marker is the one that bit: `ready...`
    fed to `re.search()` is `ready` plus any three characters, and a still
    loading worldserver prints `>> Database is already up-to-date!`, in which
    `alREADY UP-to-date` matches. The wait then reported a loading server as
    up, the ready stage passed, and the install was recorded complete. Every
    one of the four shipped bash scripts greps `ready\\.\\.\\.` with the dots
    escaped; this is the port catching up with its own lineage. Amendment A5
    has the spine escape catalogue markers the same way unless the `ready`
    block says `regex: true`, so nothing here is the odd one out.
    """
    unknown = set(kwargs) - {"timeout", "interval"}
    if unknown:
        raise TypeError(f"azerothcore_ready() accepts timeout/interval only, not {sorted(unknown)}")
    return ReadySpec(
        world=re.escape(AZEROTHCORE_READY_WORLD),
        auth=re.escape(f"{realm_host}:{realm_port}"),
        timeout=kwargs.get("timeout", _READY_TIMEOUT_SECONDS),
        interval=kwargs.get("interval", _POLL_INTERVAL_SECONDS),
    )


def wait_ready(
    auth_container: str,
    world_container: str,
    spec: ReadySpec,
    *,
    wsl_distro: str | None = None,
) -> bool:
    """Poll until the server is up, or until it is clear it never will be.

    Ready means: every container the spec waits on is listed by `docker ps`
    AND running (not in restart backoff), and this run's log matches
    `spec.auth` (when set) / `spec.world`. Not-ready-ever means one of three
    things, each returning False at once: `spec.fatal` matched in the world
    log; `RestartCount` grew by `spec.restart_loop` since the first look (a
    crash loop under `restart: unless-stopped`, which `docker ps` never shows
    — review, 2026-08-22); or the timeout ran out.

    Logs are read into Python and matched here with `re.search`, never through
    a shell pipeline: the Tortoise script recorded `pipefail` + `grep -q`
    SIGPIPEing `docker logs` into false negatives. It is a regex search and not
    a substring one for a second reason too — `azerothcore_ready()` escapes the
    realm address it looks for, so `127\\.0\\.0\\.1:8085` is the pattern and
    `127.0.0.1:8085` is the log; a literal `in` can never match the two, and a
    readiness check that cannot succeed reports a dead server as an install.

    Both markers are looked for in the CURRENT run's logs only (see `_logs()`):
    Docker keeps output across restarts, and reading the whole log returned True
    on every restart while the server was in fact still loading.

    A transient `docker ps`/CLI failure is "not ready this iteration" and
    retried, never raised — see `_status_safe()`. A missing docker CLI is not
    transient: still `False`, said out loud at WARNING, and it stops within
    `_CLI_MISSING_GRACE_SECONDS` (fixed 2026-08-23).

    Worst-case wall-clock before `False` is `timeout + interval`, as for
    `wait_db_healthy()`. `spec.interval` must be positive (same reason).

    `spec.cancel` set (T247, the job's Stop) is also `False`: the pause between
    looks is a wait on it rather than a sleep, so the wait ends inside the pause
    the press lands in, and no look is taken after it. The caller asks the
    event to tell that answer from a server that is not up.
    """
    if spec.interval <= 0:
        raise ValueError(f"interval must be positive, got {spec.interval!r}")
    logger.debug(
        f"wait_ready() called: auth_container={auth_container} "
        f"world_container={world_container} spec={spec}"
    )
    wanted = [world_container] if spec.auth is None else [auth_container, world_container]
    deadline = time.monotonic() + spec.timeout
    cli_missing_since: float | None = None
    first_restarts: int | None = None
    while time.monotonic() < deadline:
        if spec.cancel is not None and spec.cancel.is_set():
            logger.info(f"wait_ready(): Stop was pressed; ending the wait for {world_container}")
            return False
        try:
            running = _status_safe(wsl_distro=wsl_distro)
        except DockerCliMissingError:
            cli_missing_since, give_up = _cli_missing_run(cli_missing_since, "wait_ready()")
            if give_up:
                return False
            _ready_pause(spec)
            continue
        cli_missing_since = None
        if running is not None and all(name in running for name in wanted):
            # `docker ps` lists a container in restart backoff, so being listed
            # is not the same as being up. One inspect per container answers
            # that, "when did THIS run start" and "how often has it died".
            world = container_state(world_container, wsl_distro=wsl_distro)
            if first_restarts is None and world.status:
                first_restarts = world.restart_count
            if (
                first_restarts is not None
                and world.restart_count - first_restarts >= spec.restart_loop
            ):
                logger.warning(
                    f"{world_container} restarted {world.restart_count - first_restarts} times "
                    "while being waited on; that is a crash loop, not a slow start"
                )
                return False
            world_log = _logs(
                world_container,
                this_run_only=True,
                since=world.started_at,
                wsl_distro=wsl_distro,
            )
            fatal = re.search(spec.fatal, world_log) if spec.fatal is not None else None
            if fatal is not None:
                # The LINE, not the pattern. A catalogue `fatal` is an
                # alternation, and showing the user `Could not connect|FATAL:`
                # tells them nothing about what their server actually said.
                logger.warning(
                    f"{world_container} printed a line that means it will never be ready: "
                    f"{fatal.group(0)!r} (matched {spec.fatal!r}); giving up"
                )
                return False
            if (
                world.settled
                and re.search(spec.world, world_log)
                and _auth_ready(auth_container, spec, wsl_distro=wsl_distro)
            ):
                return True
        _ready_pause(spec)
    return False


def _ready_pause(spec: ReadySpec) -> None:
    """`wait_ready()`'s pause between looks: a sleep, or a wait its Stop can end (T247)."""
    if spec.cancel is None:
        time.sleep(spec.interval)
    else:
        spec.cancel.wait(spec.interval)


def _auth_ready(auth_container: str, spec: ReadySpec, *, wsl_distro: str | None = None) -> bool:
    """The auth half of `wait_ready()`: trivially true when the spec does not wait on auth."""
    if spec.auth is None:
        return True
    auth = container_state(auth_container, wsl_distro=wsl_distro)
    if not auth.settled:
        return False
    auth_log = _logs(
        auth_container, this_run_only=True, since=auth.started_at, wsl_distro=wsl_distro
    )
    return re.search(spec.auth, auth_log) is not None


def port_conflicts(ports: tuple[int, ...], *, wsl_distro: str | None = None) -> list[str]:
    """Return the names of *any* running containers currently binding `ports`.

    This is the single-instance guard from README §12: all v1 servers share
    the same ports, so a second install cannot be started while the first
    still binds them. This is a **global** port scan — it has no concept of
    "which install" a container belongs to, so it will also flag the same
    install's own containers (e.g. on a restart) or an unrelated non-Yu'lon
    container that happens to publish the same port; it does not distinguish
    those cases from a genuine second-install conflict. Parses host-side
    publishes (`:PORT->`) from `docker ps`.
    """
    logger.debug(f"port_conflicts() called: ports={ports}")
    if not ports:
        return []
    proc = _run(["ps", "--format", "{{.Names}}\t{{.Ports}}"], wsl_distro=wsl_distro)
    conflicts: list[str] = []
    for line in proc.stdout.splitlines():
        if "\t" not in line:
            continue
        name, ports_field = line.split("\t", 1)
        for port in ports:
            if f":{port}->" in ports_field:
                conflicts.append(name)
                break
    return conflicts


def wait_db_healthy_for(
    spec: ContainerSpec, *, wsl_distro: str | None = None, **kwargs: float
) -> bool:
    """`wait_db_healthy()` for `spec.db`. `kwargs` forwards `timeout`/`interval`."""
    return wait_db_healthy(spec.db, **kwargs, wsl_distro=wsl_distro)


def wait_ready_for(spec: ContainerSpec, ready: ReadySpec, *, wsl_distro: str | None = None) -> bool:
    """`wait_ready()` for `spec.auth`/`spec.world` with the game's `ReadySpec`."""
    return wait_ready(spec.auth, spec.world, ready, wsl_distro=wsl_distro)


def port_conflicts_for(spec: ContainerSpec, *, wsl_distro: str | None = None) -> list[str]:
    """`port_conflicts()` for `spec.ports` — the convenience form callers want."""
    return port_conflicts(spec.ports, wsl_distro=wsl_distro)


def foreign_port_conflicts(
    spec: ContainerSpec, project: str, *, wsl_distro: str | None = None
) -> list[str]:
    """`port_conflicts_for()` minus the containers belonging to `project`.

    The global scan cannot answer "is this MY server?", and a caller that
    refuses on its raw answer refuses its own install. That is not theoretical:
    the native engine's preflight re-runs on every resume, so once `up` had
    started the three containers — which carry `restart: unless-stopped` — the
    next attempt was told "ac-authserver, ac-worldserver already publish the
    ports this server needs. Stop that server first (or remove its containers)",
    naming the containers of the install being finished (review, 2026-08-23).

    Ownership is decided the way `_running()` decides it: the compose project
    label, which is the only proof there is. A container Docker will not answer
    for is NOT filtered out — an unreadable owner is not proof of ownership, and
    the caller refusing is the fail-closed direction here.
    """
    conflicts = port_conflicts(spec.ports, wsl_distro=wsl_distro)
    if not conflicts:
        return []
    return [name for name in conflicts if container_project(name, wsl_distro=wsl_distro) != project]


NETSH_RESERVED_RANGES = "netsh interface ipv4 show excludedportrange protocol=tcp"
"""What shows the port ranges Windows has set aside (Hyper-V, WinNAT, Docker Desktop)."""


def blocked_port_sentence(
    port: int, kind: Literal["reserved", "in_use"], *, windows: bool, what: str = ""
) -> str:
    """Plain words for a host port that cannot be bound: the reason, then what to do (T574).

    One sentence builder for the two places that learn it: the preflight bind
    probe, before the build, and the daemon's own `ports are not available`
    error from `up`, which covers a range reserved between the two (a reboot).
    `kind` is a definite fact in both; a refused or timed-out connect is not.
    """
    named = f"port {port} ({what})" if what else f"port {port}"
    if kind == "in_use":
        return (
            f"Another program on this computer already uses {named}, so the server cannot "
            "start. Close that program or stop its service, then try again."
        )
    if windows:
        return (
            f"Windows has reserved {named}, so no program can use it and the server cannot "
            f"start. To see the reserved ranges, run this in a Command Prompt: "
            f"{NETSH_RESERVED_RANGES}. To free the port, open a Command Prompt as "
            "administrator, run net stop winnat and then net start winnat, and try again. "
            "A Windows service that holds the port for itself gives the same message; "
            "stop that service if there is one."
        )
    return (
        f"This computer will not let a program use {named} (permission denied), so the "
        "server cannot start. Free the port or change what reserves it, then try again."
    )


@dataclass(frozen=True)
class PortHolders:
    """Who publishes the host ports asked about, split by ownership (T574).

    `ours`: ports published by a container of the asked-for compose project.
    `foreign`: port -> the other containers publishing it (an unreadable owner
    counts as foreign, for `foreign_port_conflicts()`'s reason).
    """

    ours: frozenset[int] = frozenset()
    foreign: dict[int, tuple[str, ...]] = field(default_factory=dict)


def port_holders(
    ports: Sequence[int], project: str, *, wsl_distro: str | None = None
) -> PortHolders:
    """Which running containers publish any of `ports`, and which of them are `project`'s.

    The bind probe's partner: a socket bind cannot tell a port our own database
    holds (a resume, with `restart: unless-stopped` containers up) from a port a
    stranger holds, so preflight asks this first and binds only the rest. Same
    ownership proof as `foreign_port_conflicts()`: the compose project label.
    Containers publishing none of `ports` are not asked about at all.
    """
    wanted = set(ports)
    if not wanted:
        return PortHolders()
    proc = _run(["ps", "--format", "{{.Names}}\t{{.Ports}}"], wsl_distro=wsl_distro)
    ours: set[int] = set()
    foreign: dict[int, list[str]] = {}
    for line in proc.stdout.splitlines():
        if "\t" not in line:
            continue
        name, ports_field = line.split("\t", 1)
        published: set[int] = set()
        for part in ports_field.split(","):
            if "->" not in part:
                continue
            _, _, port_text = part.split("->", 1)[0].rpartition(":")
            if port_text.strip().isdigit() and int(port_text) in wanted:
                published.add(int(port_text))
        if not published:
            continue
        if container_project(name, wsl_distro=wsl_distro) == project:
            ours |= published
        else:
            for port in sorted(published):
                foreign.setdefault(port, []).append(name)
    return PortHolders(frozenset(ours), {port: tuple(names) for port, names in foreign.items()})


def published_bindings(*, wsl_distro: str | None = None) -> dict[int, str]:
    """Host address each published port is bound to, parsed from `docker ps` (`{{.Ports}}`).

    The guide's LAN check: `0.0.0.0:3724->3724/tcp` reaches the network,
    `127.0.0.1:3724->3724/tcp` does not (and on WSL2 needs a portproxy). IPv6
    publishes (`[::]:3724->`) are ignored; the first IPv4 binding per port wins.
    """
    logger.debug("published_bindings() called")
    proc = _run(["ps", "--format", "{{.Ports}}"], wsl_distro=wsl_distro)
    bindings: dict[int, str] = {}
    for line in proc.stdout.splitlines():
        for part in line.split(","):
            part = part.strip()
            if "->" not in part or part.startswith("["):
                continue
            host_side = part.split("->", 1)[0]
            if ":" not in host_side:
                continue
            address, _, port_text = host_side.rpartition(":")
            if port_text.isdigit():
                bindings.setdefault(int(port_text), address)
    return bindings


def follow_logs(container: str, tail: int = 200, *, wsl_distro: str | None = None) -> Iterator[str]:
    """Stream `docker logs -f` for one container (the Console tab's log source).

    Lives here so no `ui/` module ever builds a docker argv itself
    (style-guide §3; review finding, 2026-08-21).

    The one site in this module that raises rather than returning a failed
    `CompletedProcess`: `runner.stream()` yields lines, so there is no exit
    status to hand back. `LogPanel`'s worker catches everything the source
    raises and shows it as `"<type>: <message>"`, so a missing CLI reads as
    "DockerCliMissingError: Docker could not be found on this machine..." in the
    panel — where the unresolved name used to surface a bare WinError 2.
    """
    logger.debug(f"follow_logs() called: container={container} tail={tail}")
    prefix = platform.docker_prefix(wsl_distro)
    if prefix is None:
        raise DockerCliMissingError(platform.DOCKER_CLI_MISSING_HELP)
    # Kept because wsl.exe's complaint arrives as OUTPUT here, not as an
    # exception message: `stream()` yields whatever the child wrote and only
    # then raises a `CalledProcessError` that carries a number and an argv. A
    # missing distro is recognised from that text, so the last of it has to
    # survive long enough to be asked about. Bounded for the same reason
    # `KEEP_OUTPUT_LINES` is - this can follow a log for days.
    recent: deque[str] = deque(maxlen=20)
    try:
        for line in runner.stream([*prefix, "logs", "-f", "--tail", str(tail), container]):
            recent.append(line)
            # MARKED AS TOOL OUTPUT (T35), because that is exactly what it is:
            # the worldserver's own log, relayed. The Console tab is the same
            # `LogPanel` the install uses, and the panel strips the marker out
            # of `text()` — so what changes is only the weight it is drawn at,
            # and the tab stops looking like the app is saying these things.
            # `recent` keeps the UNMARKED line, because it is read by
            # `wsl.missing_distro_problem()` and not by a reader.
            yield from lines.relayed(line, stage="console")
    except subprocess.CalledProcessError as exc:
        # The streaming half of what `_run()` does for buffered calls. Without
        # it the Console tab showed wsl.exe's UTF-16 complaint as NUL-riddled
        # gibberish and then "CalledProcessError: ... exit status 4294967295",
        # which names neither the distro nor anything to do about it.
        problem = wsl.missing_distro_problem(wsl_distro, exc.returncode, "\n".join(recent))
        if problem is not None:
            raise DockerRefusal(problem) from exc
        raise
    except OSError as exc:
        # The same uninstalled-mid-run case `_docker()` handles, arriving from
        # `Popen` on the first line instead of from `subprocess.run`. Both roads
        # have to end at the same sentence, or the panel shows a WinError for
        # one kind of missing docker and an explanation for the other.
        raise DockerCliMissingError(platform.DOCKER_CLI_MISSING_HELP) from exc


KEEP_OUTPUT_LINES = 200
"""How many of a streamed command's output lines are kept for the failure text.

A bound rather than a buffer, and the number is chosen for what has to survive
rather than for what looks generous. `runner.stream()` yields all of stdout as
it arrives and then every line of stderr in one block at the end, so the LAST
lines are the ones that explain a failure — which is the whole reason anything
is retained at all.

The alternative, keeping everything, is what this replaces: a full AzerothCore
import runs 10-30 minutes and prints a line per SQL file, and the previous
version held all of it in one string inside a GUI process that may then stay
open for days. Unbounded growth driven by how long somebody's import took is a
defect of its own, and one that gets worse on exactly the slow machine whose
import is worth watching.

200 lines is roughly 20 KB. What it costs: the middle of a failed import is
gone from this process. It is not gone from Docker — `docker compose logs
<service>` still has all of it, and the failure message says so.
"""

_LAST_WORDS_LINES = 5
_LAST_WORDS_CHARS = 400
"""How much of the retained tail goes into a message a user reads.

The retained tail is for the log; a `QLabel` on the Server tab is for one
paragraph. Five lines capped at 400 characters is enough to carry a mysql error
and not enough to push the rest of the tab off screen.
"""


_BUILDKIT_FENCE = "------"
"""The rule BuildKit draws around the failing step's own output.

Exactly six hyphens, and distinct from the twenty it draws around the
Dockerfile context below it — which is part of what makes the block findable.
"""

_BUILDKIT_STEP_HEADER = re.compile(r"^> \[[^\]]+\]")
"""BuildKit's ` > [3/3] RUN …:` header, stripped of its leading space.

The bracketed step number is required, not just a leading `>`: `last_words()`
is shared with imports, clones and extractors, and any of them may print a
line starting with `>`. Two rules and a `>` alone was enough to make an
unrelated tool's output be read as a build (review, 2026-09-12).
"""

_BUILD_FAILED = re.compile(r"^(?:ERROR:|#\d+ ERROR:)|failed to solve:")
"""The line that says a build failed, in every spelling a front end prints it.

Three shapes, measured rather than guessed:

* `ERROR: failed to build: failed to solve: …` — the classic `docker build`
  epilogue, all T38 was measured on (an Ubuntu test box, Docker 29.7.2, 2026-09-12);
* `#17 ERROR: process "/bin/sh -c …" …` — BuildKit's step-numbered line, which
  `docker compose build` prints and which `startswith("ERROR:")` rejects;
* `failed to solve: …`, and through this app's panel `target ac-worldserver:
  failed to solve: …` — how `docker compose build` ENDS, with no `ERROR:`
  anywhere on the line.

The third is why T38 shipped and changed nothing for the user who reported it:
this app builds with `docker compose build` (`build_staged()`), so the only
shape T38 could match was the one shape this app never produces (T70, measured
live 2026-09-16, Compose 2.40.3 / Engine 29.1.3).

`failed to solve:` is matched anywhere on the line because the prefix is the
front end's to choose; the two `ERROR:` spellings are anchored because `ERROR:`
alone, unanchored, is a word an import or a map extractor can print in passing.
"""

_BUILDKIT_PREFIX = re.compile(r"^(?:#\d+ )?(?:\d+\.\d+ )?")
"""BuildKit's `#25 7.331 ` stamp on a line of a step's own output.

Two independent parts, both optional. `--progress plain` prefixes every line
with the step number so concurrent steps can be told apart, and the container's
own output keeps the elapsed-seconds stamp that the fenced replay also carries.
Neither is the compiler's, and a diagnostic has to LEAD with its file and line
or the 400-character cap spends itself on bookkeeping.
"""

_COMPILER_DIAGNOSTIC = re.compile(
    r"^(?:"
    r"[^\s\"'`]+:\d+:\d+: (?:fatal error|error): \S"
    r"|[\w.+-]+: (?:fatal error|error): \S"
    r"|[^\s\"'`]+: .*undefined reference to"
    r"|CMake Error\b"
    r")"
)
"""A toolchain naming what went wrong, at the start of a line.

Four shapes, and the last three are here because they are the ones that land
ABOVE BuildKit's ten-line replay just as readily as the first:

* `path:line:column: error: …` / `fatal error:` — clang and gcc on a source
  file, the live capture of 2026-09-16;
* `<tool>: error: …` — the driver and the linker, which have no source
  position to give. `c++: fatal error: Killed signal terminated program
  cc1plus` and `clang++: error: unable to execute command: Killed` are the
  out-of-memory kill this project warns about before the build even starts:
  `-j $(nproc)+1` on a 16 GB VM, the same event the `exited with code: 137`
  path was added for. Also `ld.lld: error: undefined symbol: …` and
  `collect2: error: ld returned 1 exit status`;
* GNU ld's ``Main.cpp:(.text+0x28): undefined reference to `Foo::bar()'``, which
  carries a section offset where the others carry a line and column;
* `CMake Error at CMakeLists.txt:5 (message):` — a configure that never
  reached a compiler at all, and the shape a module with a broken
  `CMakeLists.txt` produces.

Anchored after `_BUILDKIT_PREFIX` is stripped, and that anchoring is the whole
rule rather than a tidiness: the same text appears inside BuildKit's step
header (` > [3/3] RUN sh -c "echo "…Transmog.cpp:212:9: error:…"`), inside the
Dockerfile context (`   3 | >>> RUN …`) and inside the final `ERROR:` line
whenever the failing `RUN` is a shell that echoes a diagnostic — T38's own
fixture has it in all three. Only the toolchain puts it at column zero.

The `^` is the anchor's only home, and it is searched rather than matched for
exactly that reason: `_COMPILER_DIAGNOSTIC.match()` would anchor a second time
on its own, and a rule written down twice is a rule no single mutation can
remove — measured while mutating this fix, where dropping the `^` AND swapping
`match` for `search` each left the suite green because the other still held.
Every pattern here carries its own anchor and every call site searches.

Anything else is still the fence's: a `RUN` that exits non-zero with no
diagnostic of any kind falls through to the replay exactly as before.
"""

_NINJA_FAILED = re.compile(r"^FAILED: ")
"""ninja's own line, printed BEFORE the command and the diagnostic it belongs to."""

_NINJA_PROGRESS = re.compile(r"^\[\d+/\d+\] ")
"""ninja's `[151/1838] Building CXX object …`.

The line the whole ticket is about: `cmake --build -j` lets the jobs already
in flight finish after one fails, so fifteen of these separate the diagnostic
from ninja giving up — and ten of them are all BuildKit's fenced replay holds.
"""

_DIAGNOSTIC_CONTEXT = 3
"""How many lines after a diagnostic are quoted with it.

Clang prints the offending source line, a caret column marker and `N errors
generated.` — three, measured on the live capture of 2026-09-16.
"""

_DIAGNOSTIC_KEEP = 2
"""How many diagnostics are quoted when several jobs failed at once.

`cmake --build -j $(nproc)+1` runs dozens of compiles in parallel and ninja
lets the in-flight ones finish, so more than one can fail in a single step.
Two fills the cap; the rest are counted rather than dropped silently.
"""


def _diagnostic_stops(line: str) -> bool:
    """Is this line the end of a diagnostic's context rather than part of it?"""
    return (
        not line
        or line == _BUILDKIT_FENCE
        or line.startswith("ninja: ")
        or bool(_NINJA_FAILED.search(line))
        or bool(_NINJA_PROGRESS.search(line))
        or bool(_COMPILER_DIAGNOSTIC.search(line))
        or bool(_BUILD_FAILED.search(line))
    )


def _inline_diagnostics(said: list[str], skip: range) -> list[str]:
    """Every compiler diagnostic in the WHOLE stream, first `_DIAGNOSTIC_KEEP` quoted.

    T70 round 2, and the reason a perfect fence parser was not enough. BuildKit
    replays only the LAST TEN lines of the failed step between its `------`
    rules. On the live capture of 2026-09-16 the compiler's `fatal error:` sat
    fifteen ninja jobs above that window — ninja lets the in-flight parallel
    compiles finish before it stops — so the fenced block held ten lines of
    `Building CXX object …` progress and no diagnostic at all.

    The diagnostic is therefore taken from the whole retained tail, whatever
    `#<n>` step printed it. Which step that is cannot be relied on either: three
    compose services (`ac-worldserver`, `ac-authserver`, `ac-db-import`) build
    the SAME image concurrently, all three fail, and which one's epilogue gets
    replayed is a race — the same capture's fence header read `[ac-db-import
    build 8/8]`, its final line `target ac-authserver: failed to solve:`, and
    the panel above both said `ac-worldserver`.

    `skip` is the fenced block's own indices. Excluding them is what keeps
    T38's classic-builder shape on the fence path it was measured on, where the
    only diagnostic IS the replay, and it stops one error being quoted twice.

    Identical diagnostics are collapsed: three services failing on one bad
    header print one error, not "+2 more".

    Context lines are left-stripped. Clang indents the offending source line
    and its caret marker to align the `^` under the column it names, and that
    alignment is already gone by the time anybody reads this: the lines are
    joined with ` / ` into one paragraph of a proportional-font `QLabel`, where
    no two columns line up anyway. So the indentation buys nothing and costs
    characters against the 400-character cap — `/     6 |` becomes `/ 6 |`.
    """
    blocks: list[list[str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(said):
        if index in skip:
            continue
        line = _BUILDKIT_PREFIX.sub("", raw)
        if not _COMPILER_DIAGNOSTIC.search(line) or line in seen:
            continue
        seen.add(line)
        block = [line]
        for after in said[index + 1 : index + 1 + _DIAGNOSTIC_CONTEXT]:
            context = _BUILDKIT_PREFIX.sub("", after).lstrip()
            if _diagnostic_stops(context):
                break
            block.append(context)
        blocks.append(block)
    if not blocks:
        return []
    return _fitted(blocks)


def _fitted(blocks: list[list[str]]) -> list[str]:
    """`blocks` cut to `_LAST_WORDS_LINES`, every dropped diagnostic counted.

    The count is derived from the diagnostics that actually SURVIVE the cut,
    never from `_DIAGNOSTIC_KEEP`, and that is the whole reason this is a
    function of its own. Counting `found - _DIAGNOSTIC_KEEP` is right only when
    the quoted diagnostics carry no context: three real clang diagnostics with
    three context lines each are eleven lines competing for five, the second
    diagnostic is cut for space, and the sentence then says "and 1 more" while
    two broken files have gone unnamed. A user told one file is broken fixes
    one file and runs the build again (review, 2026-09-16).

    So the heads are placed first, the spare lines are handed out as context
    round-robin — each diagnostic gets its source line before any gets its
    caret — and whatever is left over is counted. What the sentence claims and
    what it shows cannot drift, because the claim is computed from the showing.
    """
    total = len(blocks)
    shown = min(total, _DIAGNOSTIC_KEEP)
    # A count line costs one of the five, so its room is taken before the heads
    # are placed rather than after — otherwise a head is quoted and then evicted
    # by the very line that was meant to account for it.
    shown = min(shown, _LAST_WORDS_LINES - 1 if total > shown else _LAST_WORDS_LINES)
    spare = _LAST_WORDS_LINES - shown - (1 if total > shown else 0)
    context = [0] * shown
    for rank in range(_DIAGNOSTIC_CONTEXT):
        for which in range(shown):
            if spare <= 0:
                break
            if len(blocks[which]) > rank + 1:
                context[which] += 1
                spare -= 1
    lines: list[str] = []
    for which in range(shown):
        lines.append(blocks[which][0])
        lines.extend(blocks[which][1 : 1 + context[which]])
    missing = total - shown
    if missing:
        lines.append(f"(and {missing} more compiler error{'s' if missing > 1 else ''})")
    return lines


_ERROR_KEEP = 70
"""How much of each end of BuildKit's `ERROR:` line survives the elision.

The middle of that line is the whole `RUN` command; the ends are "ERROR: failed
to solve: process" and the exit code, which is the half that says something.
"""


def _elided(line: str) -> str:
    """A long `ERROR:` line with its embedded command removed, both ends kept."""
    if len(line) <= 2 * _ERROR_KEEP:
        return line
    return f"{line[:_ERROR_KEEP]}…{line[-_ERROR_KEEP:]}"


def _buildkit_failure(said: list[str]) -> tuple[list[str], str]:
    """A failed `docker build` split into (the step's own output, its `ERROR:` line).

    A failed build ends in a fixed shape (captured on m910q, Docker 29.7.2,
    `.notes/gates/t38-build-failure-reports-the-command-2026-09-12/`)::

        ------
         > [3/3] RUN sh -c "…":
        0.213 mod_transmog/src/Transmog.cpp:212:9: error: no member named GetGUID
        0.213 1 error generated.
        ------
        Dockerfile:3
        --------------------
           3 | >>> RUN sh -c "…"
        --------------------
        ERROR: failed to build: failed to solve: process "/bin/sh -c …" did not complete…

    The last five lines of that are the Dockerfile context and the `ERROR:`
    line, and that line embeds the entire `RUN` command — so it alone overruns
    the character cap and arrives truncated from the left. A user adding a
    module was shown the middle of a cmake invocation and nothing else (T38,
    Lac, 2026-09-12). The compiler's words are between the fences, and were
    never missing from the buffer: `KEEP_OUTPUT_LINES` keeps 200.

    **Both halves are returned, because either alone can be the whole story.**
    The step block carries a compiler diagnostic; the `ERROR:` line carries the
    exit code, and for a build the kernel killed — `exited with code: 137`, an
    out-of-memory linker on a machine whose Docker VM is too small, which this
    project warns about before it ever starts — the step block's last line is a
    cheerful `[95%] Linking CXX executable worldserver` and the 137 is the only
    sign anything went wrong. Returning the block alone hid it (review,
    2026-09-12).

    Fences are paired structurally rather than by taking the last two, and
    independently of where the marker sits — see `_step_fences()`. The marker
    is then the last `_BUILD_FAILED` line OUTSIDE the block, which is both
    halves of T70: this app builds with `docker compose build`, whose epilogue
    says `failed to solve:` with no `ERROR:` prefix and whose only `ERROR:`
    line is step-numbered and printed ABOVE the opening fence.

    Requiring the marker to sit outside the block is not tidiness. A compiler
    may print a line of its own beginning `ERROR:`, and counting that would let
    a step block from a build that SUCCEEDED be reported as the failure of
    whatever ran after it — the case the no-marker fallback already pins.

    An empty block with a non-empty `ERROR:` line, or both empty, tells the
    caller to fall back — a silent failing step must not come out worse than
    it did before.
    """
    opened, closed = _step_fences(said)
    # The block is located FIRST and the marker is then read from outside it.
    # Searching back from the marker, which is what T38 did, cannot work on the
    # compose route at all: BuildKit's `#17 ERROR:` line is printed ABOVE the
    # opening fence there, so a marker widened to match it would still find no
    # block behind it (T70).
    inside = range(opened, closed + 1) if opened is not None and closed is not None else range(0)
    error_at: int | None = None
    for index in range(len(said) - 1, -1, -1):
        if index not in inside and _BUILD_FAILED.search(said[index]):
            error_at = index
            break
    if error_at is None:
        # No BuildKit failure marker: not a failed build. An import, a clone or
        # a map extractor reaches here too, and none of them is parsed.
        return [], ""
    error_line = said[error_at]
    # The compiler's own words FIRST, from anywhere in the stream. BuildKit's
    # fenced replay is only the last ten lines of the failed step, and a
    # diagnostic fifteen parallel ninja jobs above it is outside that window —
    # T70 round 2, measured live 2026-09-16. The fence is the fallback, not the
    # source, and it is still the whole answer for the shapes that have no
    # inline diagnostic: T38's classic builder, and any `RUN` that simply exits
    # non-zero.
    inline = _inline_diagnostics(said, inside)
    if inline:
        return inline, error_line
    if opened is None or closed is None:
        return [], error_line
    # `opened + 2` drops the header: naming the command is what this ticket
    # exists to stop doing.
    return said[opened + 2 : closed], error_line


def _step_fences(said: list[str]) -> tuple[int | None, int | None]:
    """The last `------`/step-header pair and the first rule after it, or `(None, None)`.

    Paired structurally rather than by taking the last two rules in the output:
    the opening one is the last that carries a `_BUILDKIT_STEP_HEADER`, and the
    closing one is the first fence after it. An odd count, a stray rule in some
    tool's output, or a `------` inside the step's own lines then costs at most
    a short block instead of silently choosing two unrelated rules (review,
    2026-09-12).

    Searched over the WHOLE tail rather than up to the failure marker, which is
    T70's half of the fix — see `_buildkit_failure()`.
    """
    for index in range(len(said) - 1, 0, -1):
        if said[index - 1] == _BUILDKIT_FENCE and _BUILDKIT_STEP_HEADER.match(said[index]):
            for after in range(index + 1, len(said)):
                if said[after] == _BUILDKIT_FENCE:
                    return index - 1, after
            return index - 1, None
    return None, None


_BUILD_DETAILS = re.compile(r"View build details:\s*(\S+)")
"""Docker Desktop's stand-in for the build output it did not print.

Measured from a user's own failed build, 2026-09-13 (T50): Docker Desktop for
Windows sends the step output to its Build view and leaves this line in the
stream. There is no `------` fence and no step header, so `_buildkit_failure()`
answers nothing and has nothing to answer with -- the compiler's words are not
in the buffer at all, which is the premise T38 inherited from measuring Docker
CE on Linux and stated as a guarantee.
"""

_EXIT_CODE = re.compile(r"exit(?:ed with)? code:? (\d+)")

_BUILDER_GONE = re.compile(
    r"rpc error: code = Unavailable\b"
    r"|failed to solve: Unavailable:"
    r"|failed to (?:solve|receive status): error reading from server: EOF\b"
)
"""The build's client losing BuildKit: gRPC's `Unavailable`, the transport gone (T202).

Measured twice on one Windows test VM (Docker Desktop 29.7.2, 2026-10-03, the
T179 live check): `failed to receive status: rpc error: code = Unavailable
desc = error reading from server: EOF`, once after 53 silent minutes that
followed a finished compile, once mid-compile at 99% on a VM whose memory was
then raised and whose next build passed. The `desc` after the code is the
transport's to word (`error reading from server: EOF`, `closing transport due
to: ...`), so the code is what is matched. No build step prints this, so a
compiler's line cannot be read as it.

BuildKit words the same event a second way. The m910q live test of PR 294
(Docker 29.7.2, 2026-10-04) restarted Docker under a build at [459/1848], and
the build ended on `target ac-db-import: failed to solve: Unavailable: error
reading from server: EOF` -- the code spelled as `Unavailable:` after `failed
to solve:`, with no `rpc error`. The third form is the transport's own EOF with
no code in front of it. All three are Docker's own lines, never a step's: a
step's output arrives as `#<n> <seconds> ...` and is not read at all
(`builder_connection_lost()`), and a registry's `503 Service Unavailable`,
`429 Too Many Requests`, `not found` or a pull's `unexpected EOF` matches none.
"""

_STEP_OUTPUT = re.compile(r"^\s*#\d+\s")
"""A line BuildKit's plain progress prints for a step: `#25 213.5 ...`, `#9 ERROR: ...`."""


def builder_connection_lost(tail: Sequence[str]) -> bool:
    """Did this build end because its client lost Docker's builder? (T202)

    The whole tail is read: the line is the last one Docker prints, but blank
    lines may follow it. A step's own output lines are skipped: what a RUN step
    prints is that program's, not Docker's word on its builder.
    """
    return any(_BUILDER_GONE.search(line) and not _STEP_OUTPUT.match(line) for line in tail)


def _build_log_elsewhere(said: list[str]) -> str:
    """The URL Docker Desktop printed instead of the build's output, or `""`.

    Searched from the END: a build that failed twice in one stream leaves two,
    and the one that matters is the last.
    """
    for line in reversed(said):
        found = _BUILD_DETAILS.search(line)
        if found:
            return found.group(1)
    return ""


def _exit_code(said: Sequence[str]) -> str:
    """`"exit code: 137 — "` from the LAST line that carries one, else `""`.

    The whole tail is searched, not the `ERROR:` line and not the final line.
    Round 1 looked in exactly those two and the reporter's own stream has it in
    neither: Docker Desktop prints the clause on a line with no `ERROR:` prefix,
    and the last line is the `View build details:` URL.

    Kept even though the caller already has the process's own return code,
    because this is the code of the step INSIDE the build rather than of
    `docker build` itself, and 137 is an out-of-memory kill that nothing else
    in a Docker Desktop stream says out loud.
    """
    for line in reversed(said):
        found = _EXIT_CODE.search(line)
        if found:
            return f"exit code: {found.group(1)} — "
    return ""


_METADATA_STEP = re.compile(r"^#(\d+) \[[^\]]*\] load metadata for (\S+)$")
"""BuildKit's header for the step that looks a `FROM` image up in its registry.

`#3 [ac-worldserver internal] load metadata for docker.io/library/ubuntu:24.04` --
the first thing a build does, before any layer is built.
"""

_RESOLVE_FAILED = re.compile(r"failed to resolve source metadata for (\S+): ")
"""The same failure in the build's closing `failed to solve:` line."""

_REGISTRY_UNREACHABLE = re.compile(
    r"TLS handshake timeout|i/o timeout|no such host|connection reset by peer"
    r"|connection refused|connection timed out|no route to host|network is unreachable"
    r"|context deadline exceeded|server misbehaving|Temporary failure in name resolution"
    r"|(?::|\bunexpected) EOF$"
)
"""The network's words, as Go's HTTP client and BuildKit's resolver print them.

`EOF` only at the end of the line, after Go's `: ` or as `unexpected EOF` -- a
connection the registry's side closed mid-answer -- so the three letters inside
an image name or a URL are not read as one. Every word here counts only on the
metadata step's lines (`base_image_unreachable()`), which is what keeps a `RUN`
step's own failure out.

Transport errors only. "failed to fetch anonymous token" is NOT one: it is
BuildKit's wrapper around whatever the token request met, which was a TLS
handshake timeout on yulon-win11 (2026-10-04) but is an HTTP status when Docker
Hub answers and refuses -- 401, 403, or a 429 rate limit -- and telling that
user to check their internet connection is false (Codex review). Nor is an
image name that does not exist (`...: not found`): asking again cannot fix it.
"""


def base_image_unreachable(tail: Sequence[str]) -> str:
    """The base image a failed build could not look up because the registry did not answer.

    `""` for every other failure (T223). Only the metadata step counts -- its
    own `#N ERROR:` line, or the closing line's `failed to resolve source
    metadata for` -- because that step runs before anything is built, so a
    failure there means nothing was compiled and another try costs seconds. The
    same network words in a `RUN` step's output are that step's command failing,
    perhaps after an hour of compiling, and are not this.

    The name comes back without `docker.io/library/`, as the Dockerfile spells it.
    """
    said = [line.strip() for line in tail]
    looked_up: dict[str, str] = {}
    for line in said:
        header = _METADATA_STEP.match(line)
        if header:
            looked_up[header.group(1)] = header.group(2)
    image = ""
    for line in said:
        if not _REGISTRY_UNREACHABLE.search(line):
            continue
        step = re.match(r"^#(\d+) ERROR: ", line)
        resolved = _RESOLVE_FAILED.search(line)
        if step and step.group(1) in looked_up:
            image = looked_up[step.group(1)]
        elif resolved:
            image = resolved.group(1)
    return image.removeprefix("docker.io/").removeprefix("library/")


def last_words(tail: tuple[str, ...], *, from_build: bool = False) -> str:
    """The end of a command's output, short enough to put inside a sentence.

    Blank lines are dropped before the count, because a shell script's spacing
    is exactly what a five-line window cannot afford to spend itself on.

    For a failed `docker build` the last lines are Docker's own epilogue rather
    than anything that went wrong, so the failing step's output is preferred
    and the `ERROR:` line is kept beside it with its command elided — see
    `_buildkit_failure()`.
    """
    said = [line.strip() for line in tail if line.strip()]
    if not said:
        return "it printed nothing at all"
    block, error_line = _buildkit_failure(said)
    if block:
        text = " / ".join(block[-_LAST_WORDS_LINES:])
        if len(text) > _LAST_WORDS_CHARS:
            # From the HEAD here, the opposite of the fallback below: a compiler
            # puts `file:line:column: error:` at the front of its diagnostic,
            # and keeping the tail of a long one throws away the only part that
            # says where to look (review, 2026-09-12).
            text = text[:_LAST_WORDS_CHARS] + "…"
        return f"{text} / {_elided(error_line)}" if error_line else text
    kept = _build_log_elsewhere(said) if from_build else ""
    if kept:
        # T50. The step output is not in the buffer to be selected from, so the
        # honest answer is where it IS -- not the left-truncated command echo
        # below, which is the one thing already known to tell a reader nothing.
        # Correlated, not searched independently: the exit-code clause must come
        # at or before the URL, because Docker prints the URL after the failure.
        # Two reverse searches over the whole tail could take a code from a
        # later, unrelated line and attribute it to this build -- 137 in
        # particular sends recovery in the wrong direction (review, 2026-09-13).
        where = max(i for i, line in enumerate(said) if _BUILD_DETAILS.search(line))
        code = _exit_code(said[: where + 1])
        # The plain tail is kept BESIDE the link, not replaced by it: a
        # `docker-desktop://` URL resolves only where Docker Desktop is
        # installed, and this app runs headless and under WSL too.
        nearby = [ln for ln in said[:where] if not _BUILD_DETAILS.search(ln)]
        said_too = f" / {_elided(nearby[-1])}" if nearby else ""
        return (
            f"{code}Docker Desktop kept this build's log instead of printing it: {kept}{said_too}"
        )
    text = " / ".join(said[-_LAST_WORDS_LINES:])
    return text if len(text) <= _LAST_WORDS_CHARS else "…" + text[-_LAST_WORDS_CHARS:]


@dataclass(frozen=True)
class AttachedRun:
    """What a streamed docker command left behind: its exit code and the end of its output."""

    returncode: int
    tail: tuple[str, ...] = ()
    container_left: str = ""
    """The tool container a Stop could not remove, by name, or "" (T303, Codex adversarial
    review): it may still be writing into its mounts, so the caller must not treat the
    run as a clean Stop -- a stopped Re-extract leaves the old map data aside for it."""
    stop_seen: bool = False
    """The job's Cancel was already set when the command returned (T250 review).

    Set by the engine's bridge (`native.StagedInstaller._pump()`), never here.
    A bare exit 1 is how a docker CLI ended by Stop exits on Windows, and how
    many real failures exit everywhere; only one that came after the Stop is
    the Stop's.
    """


def cli_missing_run(run: AttachedRun) -> bool:
    """True if this result is "there was no docker CLI to run", not something a command said.

    `_cli_missing()`'s rule for the streamed shape, and public because the
    callers that need it are stages in other modules: a tool that never started
    and a tool that started and failed are different sentences to a user, and
    only this module knows which returncode and which text mean the first one.

    Both halves are checked, for `_cli_missing()`'s reason and one more of its
    own. `docker run` and `docker exec` return the CONTAINER's status, so 127 —
    "command not found" — is a status a real container genuinely produces when
    the image does not hold the binary it was asked for. That is the extraction
    stage's most likely 127 by some distance, and reading it as "Docker is not
    installed" would send the user to reinstall a Docker that is working.
    """
    return run.returncode == _CLI_MISSING_RETURNCODE and run.tail == (
        platform.DOCKER_CLI_MISSING_HELP,
    )


def run_attached(
    argv: list[str],
    cwd: Path,
    *,
    wsl_distro: str | None = None,
    sink: OutputSink | None = None,
    keep: int = KEEP_OUTPUT_LINES,
    cancel: threading.Event | None = None,
    merge_stderr: bool = False,
    env: Mapping[str, str] | None = None,
    on_cancel: Callable[[], object] | None = None,
) -> AttachedRun:
    """Run `docker <argv...>` attached, handing stdout lines to `sink` as they arrive.

    The streaming counterpart to `_docker()`, and the second site in this module
    that goes through `runner.stream()` rather than `runner.run()`. It lives
    beside `follow_logs()` for the reason recorded there: no `ui/` module builds
    a docker argv (style-guide §3), so a view that wants to show a command's
    output asks for a sink rather than for a subprocess.

    Why it exists: `_docker()` buffers a whole run into one string and returns
    it at the end, which for the database import means 10-30 minutes in which
    the caller has nothing to say and the user cannot tell a long job from a
    hung one. The argv is unchanged by this — only the reading of it is.

    Three deliberate differences from `runner.stream()`'s own contract:

    * **The exit status is returned, never raised.** `stream()` raises
      `CalledProcessError` once an exhausted generator finds a non-zero exit;
      that would take the decision away from callers who must go on and check
      something else (see `repair_import()`, where a one-shot that failed
      part-way and one that failed having done nothing exit alike).
    * **Only the last `keep` lines are retained** — see `KEEP_OUTPUT_LINES`.
      The sink sees every line, though not all of them live — stdout arrives as
      it is written and stderr in one block after the child exits. This process
      remembers a bounded end of them.
    * **A sink that raises is dropped, not propagated.** Letting it out would
      abandon the generator mid-iteration, and `stream()` terminates the child
      when that happens — so a widget deleted while an import was running would
      kill the import. The first failure is logged and nothing is sent onward
      after it.

    A host with no docker CLI comes back as `_CLI_MISSING_RETURNCODE` carrying
    `DOCKER_CLI_MISSING_HELP`, the same shape `_docker()` gives it, rather than
    as an exception — the callers here already have to handle a failed run.

    `cancel`, when set mid-run, ends the docker CLI at once and the result
    comes back as `CANCELLED_RETURNCODE`, whether the CLI was ended by the
    watcher (`_ended_on_cancel`) or abandoned when its next line arrived. The
    watcher is T526: the install engine runs this on its own worker thread
    (`native._pump()`), out of reach of the panel's Stop, and a build step that
    prints nothing used to keep the Stop waiting -- measured 2026-10-07, a step
    silent for 60 s ended 57 s after Stop. The code is the Stop's own, never the
    CLI's -15/143 or Windows' 1, because callers decide on
    `== CANCELLED_RETURNCODE` (the build's "may have tagged", the import).

    What a Stop does to work already handed to the daemon differs by platform.
    A one-shot container keeps running to completion everywhere, unless its
    caller ends it by name, as
    `run_container()` does for the extraction tools (T303). A build ends with
    its client: on Linux only the docker CLI is ended, and the T298 probes of
    2026-10-06 saw compose and its `buildx bake` end within 2 s and the build
    never tag; on Windows the client's whole process tree is ended (T246), and
    the yulon-win11 probe of 2026-10-05 saw BuildKit's build end at once as
    `Error`. macOS was not probed. Either way the layer cache keeps
    every finished step and a resumed install re-probes the databases, and it
    is the caller's job to say what a Stop costs, per stage — see
    `native.build_cancel_note()` and its neighbours. `repair_import()`
    deliberately passes no cancel at all; see there.

    `on_cancel`, when given, is called once, on the watcher's thread, right after
    the cancel has ended the CLI: what else of the run must end with it (T539:
    `run_one_shot()` ends its container, because a `compose up` sent one SIGTERM
    stops it gracefully and slowly, and an orphaned compose held the importer
    and the pipe for 46 s live).

    `merge_stderr` is for the build, whose entire progress output is stderr;
    see `runner.stream()`. `env` is the child's whole environment, None to
    inherit; for a WSL install it must already carry its `WSLENV`
    (`platform.wsl_env()`), and `build_staged()` is its one caller (T376).
    """
    logger.debug(f"run_attached() called: argv={argv} cwd={cwd}")
    tail: deque[str] = deque(maxlen=keep)
    # The same two-seam rule as `_docker()`: for a WSL install the location
    # rides in the argv as `wsl --cd`, because a Windows process cannot cd into
    # a distro and `runner.stream()` would be handed a cwd that does not exist.
    try:
        inside = (
            platform.wsl_linux_path_in(cwd, wsl_distro)
            if (wsl_distro is not None and cwd is not None)
            else None
        )
    except platform.WslDistroMismatch as exc:
        return AttachedRun(1, (str(exc),))
    prefix = platform.docker_prefix(wsl_distro, inside=inside)
    if prefix is None:
        logger.debug(f"no docker CLI on this host; not running: docker {' '.join(argv)}")
        return AttachedRun(_CLI_MISSING_RETURNCODE, (platform.DOCKER_CLI_MISSING_HELP,))
    stream_cwd = None if wsl_distro is not None else cwd
    if stream_cwd is not None and _cwd_is_missing(stream_cwd):
        logger.warning(f"server folder gone: {stream_cwd}")
        missing = _missing_cwd_result([*prefix, *argv], stream_cwd)
        return AttachedRun(missing.returncode, (missing.stderr,))
    live = sink
    ended_by_cancel = threading.Event()  # set when the watcher ended this run's CLI
    after_cancel = _AfterCancel(on_cancel)
    try:
        # `closing`, not a bare `for`: leaving the loop early has to CLOSE the
        # generator for `stream()`'s finally to terminate the child, and
        # relying on the loop variable falling out of scope makes that depend
        # on refcounting rather than on the code saying so.
        stream = runner.stream([*prefix, *argv], cwd=stream_cwd, merge_stderr=merge_stderr, env=env)
        with (
            closing(stream) as lines,
            _ended_on_cancel(cancel, lines, ended_by_cancel, after_cancel),
        ):
            for line in lines:
                if cancel is not None and cancel.is_set():
                    logger.warning(f"docker {' '.join(argv)} was cancelled; abandoning the client")
                    # A line came, so the CLI runs and has made what it makes: end that
                    # with it, as the watcher would have (T539).
                    after_cancel.once()
                    return AttachedRun(CANCELLED_RETURNCODE, tuple(tail))
                # Colour codes off at the source (T214): this line goes to the
                # screen, to Details through `tail` and to the log, and none of
                # them draws `ESC[36m`. AzerothCore's importer colours every line.
                line = ansi.strip(line)
                tail.append(line)
                if live is not None:
                    try:
                        live(line)
                    except Exception as exc:  # noqa: BLE001 - a dead sink keeps the child
                        logger.warning(f"the output sink stopped accepting lines: {exc}")
                        live = None
    except subprocess.CalledProcessError as exc:
        if ended_by_cancel.is_set() or (
            isinstance(exc, runner.StreamEnded) and cancel is not None and cancel.is_set()
        ):
            # The Stop ended the CLI (T526): said as the Stop, as the line check above says it.
            logger.warning(f"docker {' '.join(argv)} was cancelled; its client was ended")
            return AttachedRun(CANCELLED_RETURNCODE, tuple(tail))
        # Replaced rather than appended: on a missing distro every line in
        # `tail` IS the complaint, arriving as UTF-16 with a NUL after each
        # character, and showing that above the explanation would bury it in
        # the noise it exists to translate. This one keeps the contract above -
        # the status comes back, nothing is raised.
        problem = wsl.missing_distro_problem(wsl_distro, exc.returncode, "\n".join(tail))
        if problem is not None:
            return AttachedRun(exc.returncode, (problem,))
        return AttachedRun(exc.returncode, tuple(tail))
    except OSError as exc:
        # Docker uninstalled while the launcher is open, arriving from `Popen`;
        # `follow_logs()` handles the same case one line above.
        logger.warning(f"{prefix[0]} could not be started: {exc}")
        return AttachedRun(_CLI_MISSING_RETURNCODE, (platform.DOCKER_CLI_MISSING_HELP,))
    finally:
        if cancel is not None and cancel.is_set():
            # Once more, now that the CLI is gone (Codex review of 5b67822c): a CLI that
            # had started could still create its container after the first ending, and a
            # cancel set after the last line was seen by neither the read nor the watcher.
            after_cancel.last()
    if ended_by_cancel.is_set():
        # Ended by the cancel and exited 0 all the same: `compose up` traps SIGTERM,
        # stops what it started and exits cleanly. Still the Stop (cold review of T526).
        logger.warning(f"docker {' '.join(argv)} was cancelled; its client stopped cleanly")
        return AttachedRun(CANCELLED_RETURNCODE, tuple(tail))
    return AttachedRun(0, tuple(tail))


# ------------------------------------------------------------- the build

_TIMEOUT_RETURNCODE = 124
"""What `runner.run()` reports for a command that did not answer in time.

Its own choice, borrowed from `timeout(1)`; named here so `bind_mount_ok()`
can tell "Docker said no" from "Docker said nothing".
"""

BIND_PROBE_TIMEOUT_SECONDS = 30.0
"""How long the bind-mount probe gets, first image pull included.

Reconciles two numbers. `pyplan/rust-prior-art.md` §3 bounds probes at 30s
because a wedged dockerd accepts the socket and never answers; the 5-second
figure in roadmap 6.2 assumed the image was already pulled. The probe uses the
same `alpine/git` image the clone stages need anyway, so on a first install the
bound has to cover pulling it — a bound too short to pull under would report
"your folder is not shared with Docker" for what is really a slow network.
"""


def build_staged(
    server_dir: Path,
    compose_files: Sequence[str],
    *,
    wsl_distro: str | None = None,
    sink: OutputSink | None = None,
    cancel: threading.Event | None = None,
) -> AttachedRun:
    """Build this install's images from the THREE compose files, streamed.

    The only builder. `compose_files` must be the base, the override and the
    build overlay, in that order, because of a trap that costs a whole install:
    a bare `docker compose build` in a generated install's directory builds
    NOTHING and exits 0 — the `build:` blocks live in a file compose never
    auto-loads — and naming any `-f` at all disables auto-loading, so the base
    and the override have to be listed too or the build loses the image tags
    and runtime env it is meant to produce (`rust-prior-art.md` §2).
    Centralising the argv here is what keeps a caller from spelling that
    discipline a second, wrong way (style-guide §4).

    `--progress plain` is deliberate: the default renders an ANSI progress
    display for a terminal that is not there, and a log panel would show the
    escape sequences instead of the build. Output is read with `merge_stderr`,
    because BuildKit writes ALL of its progress to stderr and `runner.stream()`
    otherwise withholds it until the child exits — which for a two-to-four-hour
    compile is a blank panel for the entire build.

    **More than one built service is built one call per service (T376)**, each
    on its own buildx config folder (`_buildx_config()`). BuildKit keeps a
    cached copy of a build context under a key made of the context folder's
    NAME and the buildx node, and the node is the `.buildNodeID` in the buildx
    config folder; which files a target reads is not part of the key. WotLK's
    four services are four targets of one Dockerfile on one folder, built
    together by compose, so they took each other's copy and re-sent the
    difference: on yulon-win11-gate a Rebuild with nothing changed sent 2.36 GB
    in 912 s, and this shape sent 1.1 MB in 21 s (`.notes/plans/2026-10-05-t376-
    compose-context-resend.md`). Splitting the calls without the folders fixes
    nothing: the one key then flips between targets call by call. A game with
    one built service keeps the single call and the user's own buildx config,
    because it has no race to lose and a new key costs one cold send.

    **Services that read the same files can share one folder (T525).** A service
    whose own `x-yulon-buildx-group` key (`BUILDX_GROUP_KEY`) names the same
    group as an earlier one builds on that earlier service's folder, so the
    group keeps ONE cached copy of the context instead of one each. It is only
    safe for targets that read exactly the same paths, because a shared copy is
    synced to whatever the current target reads; the overlay's author says
    which ones do. On the first member's folder rather than a new one, so the
    copy that member already holds stays warm when an install gains the key.
    The calls stay one per service and in the overlay's order, and a service
    without the key keeps its own folder as before.

    **The builder a plain build would use is asked first (T413)**, because a
    builder picked with `docker buildx use` is recorded in the user's buildx
    config, which the per-service folders are not. A per-service folder holds
    no `current`, so buildx falls back to the current docker context's own
    builder, a `docker`-driver builder named after the context. The split calls
    are made only when that IS the builder in use (`_current_context()`), and
    they name no builder at all (T527): compose refuses a `BUILDX_BUILDER` that
    names a docker context other than `default`, the current one included, so
    on a stock Docker Desktop (context and builder both `desktop-linux`) naming
    it failed every WotLK build within a second. Any other builder, a
    `docker`-driver one of another context or one of another driver
    (docker-container, remote, kubernetes), lives only in the user's own
    config, and an answer that cannot be read is not a builder; all of them get
    the single call on the user's own config, which is the build before T376:
    their own builder, without the per-service cache. `BUILDX_BUILDER` is taken
    out of the split calls' environment, so one the user set reaches no call
    that would refuse it.

    The calls run in the overlay's order and the first that does not exit 0
    ends the build: its run is returned as it is, so its status and its own
    lines are what `_check_run()` and `base_image_unreachable()` read. A Stop
    between two calls starts no further one.

    Unbounded on purpose (rust-prior-art §1: probes are bounded, builds are
    not). Returns the run rather than raising, so the caller can tell a
    cancellation from a failure.
    """
    argv = ["compose"]
    for name in compose_files:
        argv += ["-f", name]
    argv += ["build", "--progress", "plain"]
    built = _built_services(server_dir, compose_files)
    services = tuple(service.name for service in built)
    builder = _current_builder(wsl_distro) if len(services) >= 2 else None
    context = (
        _current_context(wsl_distro) if builder is not None and builder[1] == "docker" else None
    )
    if builder is not None and builder[1] != "docker":
        logger.info(
            f"build_staged(): the builder in use is {builder[0]} ({builder[1]} driver), "
            "which only the user's own buildx config can name; one build call"
        )
    elif builder is not None and context != builder[0]:
        logger.info(
            f"build_staged(): the builder in use is {builder[0]}, and the current docker "
            f"context is {context or 'not known'}; only the user's own buildx config reaches "
            "that builder, so one build call"
        )
    if builder is None or builder[1] != "docker" or context != builder[0]:
        logger.info(f"build_staged(): `docker {' '.join(argv)}` in {server_dir}")
        return run_attached(
            argv, server_dir, wsl_distro=wsl_distro, sink=sink, cancel=cancel, merge_stderr=True
        )
    tail: deque[str] = deque(maxlen=KEEP_OUTPUT_LINES)
    first_of_group: dict[str, str] = {}
    for service, group in built:
        if cancel is not None and cancel.is_set():
            logger.warning(f"build_staged(): stopped before building {service}")
            return AttachedRun(CANCELLED_RETURNCODE, tuple(tail))
        folder = service if group is None else first_of_group.setdefault(group, service)
        config = _buildx_config(server_dir, folder)
        extra = {"BUILDX_CONFIG": str(config)}
        env = (
            platform.wsl_env(extra, paths=("BUILDX_CONFIG",))
            if wsl_distro is not None
            else {**os.environ, **extra}
        )
        env.pop("BUILDX_BUILDER", None)
        logger.info(
            f"build_staged(): `docker {' '.join([*argv, service])}` in {server_dir}, "
            f"BUILDX_CONFIG={config}, on the {context} context's own builder"
        )
        run = run_attached(
            [*argv, service],
            server_dir,
            wsl_distro=wsl_distro,
            sink=sink,
            cancel=cancel,
            merge_stderr=True,
            env=env,
        )
        if run.returncode != 0:
            return run
        tail.extend(run.tail)
    return AttachedRun(0, tuple(tail))


BUILDER_PROBE_TIMEOUT = 30.0
"""How long `_current_builder()` waits for `docker buildx inspect`: a bounded probe."""


def _current_builder(wsl_distro: str | None = None) -> tuple[str, str] | None:
    """(name, driver) of the builder a plain build would use; None if buildx will not say (T413).

    Asked with the user's own environment and buildx config, so `docker buildx
    use`, `BUILDX_BUILDER` and the docker context all count as they would for
    a `docker compose build` started by hand. A refusal, a missing CLI or a
    timeout is None: `build_staged()` then builds as it did before T376
    rather than guess the builder.
    """
    proc = _docker(["buildx", "inspect"], timeout=BUILDER_PROBE_TIMEOUT, wsl_distro=wsl_distro)
    if proc.returncode != 0:
        logger.warning(
            f"docker buildx inspect exited {proc.returncode}: {proc.stderr.strip()}; "
            "the builder in use is not known"
        )
        return None
    builder = _builder_in(proc.stdout)
    if builder is None:
        logger.warning(f"docker buildx inspect named no builder: {proc.stdout.strip()!r}")
    return builder


def _current_context(wsl_distro: str | None = None) -> str | None:
    """The docker context a plain command would use; None if docker will not say (T527).

    `docker context show` reads `DOCKER_CONTEXT`, `DOCKER_HOST` and the user's
    `docker context use` exactly as `docker compose` does. Bounded like
    `_current_builder()`, and None on any refusal: `build_staged()` then makes
    the single call rather than guess which builder a split call would reach.
    """
    proc = _docker(["context", "show"], timeout=BUILDER_PROBE_TIMEOUT, wsl_distro=wsl_distro)
    name = proc.stdout.strip() if proc.returncode == 0 else ""
    if not name or "\n" in name:
        logger.warning(
            f"docker context show exited {proc.returncode}: {proc.stderr.strip()!r} "
            f"{proc.stdout.strip()!r}; the current context is not known"
        )
        return None
    return name


def _builder_in(text: str) -> tuple[str, str] | None:
    """(name, driver) from `docker buildx inspect`'s text, or None when it names neither (T413).

    The builder's own `Name:` and `Driver:` come before the `Nodes:` heading;
    each node below it has a `Name:` of its own (`<builder>0`), which is not the
    builder's, so nothing from the node block is read.
    """
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if line.strip() == "Nodes:":
            break
        key, sep, value = line.partition(":")
        if sep and key.strip() in ("Name", "Driver"):
            fields[key.strip()] = value.strip()
    name, driver = fields.get("Name", ""), fields.get("Driver", "")
    return (name, driver) if name and driver else None


BUILDX_GROUP_KEY = "x-yulon-buildx-group"
"""A service's own key in the build overlay naming the buildx group it builds in (T525).

Services of one group build on one buildx config folder and so share one cached
copy of the context; see `build_staged()`. Compose ignores any `x-` key.
"""

_BUILDX_GROUP_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
"""What a group may be called: a plain name, never a path, a variable or a list."""


class BuiltService(NamedTuple):
    """One service the build overlay builds, and the buildx group it named, if any (T525)."""

    name: str
    group: str | None


def _built_services(server_dir: Path, compose_files: Sequence[str]) -> tuple[BuiltService, ...]:
    """The services with a `build:` block in the build overlay, in its order (T376).

    The overlay is the LAST of `compose_files` and is a file Yu'lon writes
    (`composegen.BUILD_FILE`), so this reads its block shape rather than YAML
    at large: a service is a key one level under `services:`, and it is built
    when one of its own keys is `build`. An overlay it cannot read, or one in
    another shape, answers no services, and the build is then the single
    call it was before T376.

    A built service's own `BUILDX_GROUP_KEY`, at the same depth as its `build`,
    is its group (T525); one deeper (a label, say) is not. A value that is not
    a plain name (`_buildx_group()`) is logged and read as no group.
    """
    if not compose_files:
        return ()
    overlay = server_dir / compose_files[-1]
    try:
        text = overlay.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning(f"the build overlay {overlay} could not be read ({exc}); one build call")
        return ()
    found: list[str] = []
    groups: dict[str, str | None] = {}
    in_services = False
    service_indent: int | None = None
    key_indent: int | None = None
    service: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            in_services = line == "services:"
            service_indent = key_indent = None
            service = None
            continue
        if not in_services:
            continue
        if service_indent is None:
            service_indent = indent
        if indent == service_indent:
            service = line.strip()[:-1] if line.endswith(":") else None
            key_indent = None
            continue
        if service is None or indent < service_indent:
            continue
        if key_indent is None:
            key_indent = indent
        key, _, value = line.strip().partition(":")
        if indent != key_indent:
            continue
        if key == "build" and service not in found:
            found.append(service)
        elif key == BUILDX_GROUP_KEY:
            groups[service] = _buildx_group(service, value)
    return tuple(BuiltService(name, groups.get(name)) for name in found)


def _buildx_group(service: str, value: str) -> str | None:
    """The group a `BUILDX_GROUP_KEY` value names, or None for one that is not a plain name (T525).

    Read the way Yu'lon writes it: a bare or quoted word, perhaps followed by a
    `# comment`. Anything else joins no group, so the service keeps its own
    folder, which is never wrong, only bigger.
    """
    text = value.split(" #", 1)[0].strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    if _BUILDX_GROUP_NAME.fullmatch(text):
        return text
    logger.warning(
        f"{service}'s {BUILDX_GROUP_KEY} {value.strip()!r} is not a plain name; "
        "it builds on its own buildx config"
    )
    return None


def _buildx_config(server_dir: Path, service: str) -> Path:
    """The buildx config folder one service of one install builds on (T376).

    A buildx group (T525) builds on its first member's folder, so `service` is
    always the name of a built service.

    Under the app's own folder and never under `server_dir`, because the T224
    fingerprint reads every file in the server folder and buildx writes here.
    The same install and service get the same folder every time, so its node,
    and with it BuildKit's cached copy of the context, survives from one build
    to the next; a folder named per build would make every build a cold send.
    The install part is a hash of the folder's absolute path, so two installs
    in folders of the same name do not share one.

    Created here rather than left to buildx, so that WSL has a folder to
    translate when it crosses as `BUILDX_CONFIG/p`; a folder that cannot be
    made is logged and left to buildx to try.
    """
    where = os.path.normcase(os.path.abspath(server_dir))
    install = hashlib.sha256(where.encode("utf-8")).hexdigest()[:12]
    config = platform.config_dir() / "buildx" / install / service
    try:
        config.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning(f"could not make the buildx config folder {config}: {exc}")
    return config


def build_image(
    context: Path,
    tag: str,
    *,
    wsl_distro: str | None = None,
    sink: OutputSink | None = None,
    cancel: threading.Event | None = None,
) -> AttachedRun:
    """`docker build -t <tag> .` in `context`, streamed; the run is returned, never raised (T127).

    For an image that is NOT one of the install's compose builds: the bots
    module's own dashboard, built from the Dockerfile it ships
    (`tools/observability`). The install's images keep `build_staged()`, which
    is the only builder allowed to name the compose files.

    No `--progress`: BuildKit picks plain output by itself when stdout is not a
    terminal, and the flag is refused by a daemon still on the classic builder.
    """
    argv = ["build", "-t", tag, "."]
    logger.info(f"build_image(): `docker {' '.join(argv)}` in {context}")
    return run_attached(
        argv, context, wsl_distro=wsl_distro, sink=sink, cancel=cancel, merge_stderr=True
    )


def compose_up_service(
    server_dir: Path,
    service: str,
    *,
    force_recreate: bool = False,
    timeout: float | None = None,
    wsl_distro: str | None = None,
) -> None:
    """Start ONE service of this install's project, and nothing it depends on (T127).

    `--no-deps` for `staged_up_argv()`'s reason: the caller decides what else
    runs. Raises `DockerCommandError` with compose's own words, which for a
    taken port name the port. `timeout` bounds the wait (a timed-out run is a
    non-zero result from `runner.run()`, so it raises here like any failure).
    """
    argv = ["compose", "up", "-d", "--no-deps"]
    if force_recreate:
        argv.append("--force-recreate")
    _run([*argv, service], cwd=server_dir, timeout=timeout, wsl_distro=wsl_distro)


def compose_remove_service(
    server_dir: Path, service: str, *, wsl_distro: str | None = None
) -> None:
    """Stop and remove ONE service's container (`compose rm --stop --force`). No volumes (T127).

    Meant to be idempotent: compose v2 treats a service that has no container as
    nothing to remove and exits 0, which the bot dashboard's switch-off relies on
    when a second press repeats this step. Expected from compose, not measured in
    this repository; a non-zero exit still raises, and then the switch stays On.
    """
    _run(["compose", "rm", "--stop", "--force", service], cwd=server_dir, wsl_distro=wsl_distro)


def container_ip(container: str, *, wsl_distro: str | None = None) -> str | None:
    """The container's address on its compose network, or None if Docker would not say (T127).

    Asked because a peer that resolved this container's NAME once keeps the
    address it got: recreating the container can hand it a new one.
    """
    proc = _docker(
        ["inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}", container],
        wsl_distro=wsl_distro,
    )
    if proc.returncode != 0:
        return None
    found = proc.stdout.split()
    return found[0] if found else None


def images_built(refs: Sequence[str], *, wsl_distro: str | None = None) -> bool | None:
    """Do all of `refs` exist on this daemon? None = could not ask.

    Disk evidence for the install engine's `build` stage (rust-prior-art §1),
    and it asks the daemon about image references rather than asking compose
    about a project — because compose cannot answer this question.

    **Measured on yulon-ubuntu, Docker 29.1.3 / Compose 2.40.3 (2026-08-24),
    which is why this no longer runs `compose images -q`.** After a successful
    `compose -f base -f override -f build build`, `compose images -q` returned
    NOTHING, both bare and with the same `-f` set; it began answering only once
    containers existed (`compose create` sufficed, `up` was not needed). Compose
    enumerates the images of a project's CREATED CONTAINERS, not of its service
    definitions. The window it answered wrongly in — built, no containers yet —
    is precisely the window a resume asks in, so every resume re-ran the
    compile. The old docstring predicted this and asked the first gate to
    record it; it did.

    ALL of them, not any: a build that produced three of four images is not a
    finished build, and skipping it would start a server missing a binary.

    `None` is not `False`. A resume that cannot ask must not conclude "nothing
    is built" and spend hours proving itself wrong, and must not conclude
    "built" either; the caller decides. `refs` is passed in rather than derived
    here for the reason every game-specific value is — `docker.py` knows no
    game (`composegen.built_image_refs()` is where they come from).
    """
    if not refs:
        # NOT vacuous truth. "All zero of them exist" is formally True and would
        # be the worst possible answer here: an empty tuple means the CALLER
        # could not work out what this install builds, and reporting "built" for
        # that skips the build stage entirely and starts a server with no
        # binaries. `None` sends it back to the caller as the unanswerable
        # question it is. Held by a review seat that wanted the reasoning next
        # to the branch rather than only in a transcript (2026-08-24).
        return None
    for ref in refs:
        proc = _docker(["image", "inspect", "--format", "{{.Id}}", ref], wsl_distro=wsl_distro)
        if proc.returncode == 0 and proc.stdout.strip():
            continue
        # `docker image inspect` exits non-zero for "no such image", which is an
        # answer, and for a daemon that will not talk, which is not. They are
        # told apart by what the daemon said, because guessing either way is the
        # expensive mistake this function exists to avoid.
        said = proc.stderr.strip().lower()
        if "no such image" in said or "not found" in said:
            return False
        logger.warning(f"could not ask whether {ref} exists: {proc.stderr.strip()}")
        return None
    # Every reference answered, or the loop returned. The counter this used to
    # compare against `len(refs)` could only ever equal it here, which read as
    # if a partial count could reach this line (review, 2026-08-24).
    return True


BUILD_CACHE_TIMEOUT_SECONDS = 60.0
"""How long each build-cache question gets. `system df` sizes every image and volume."""

_DECIMAL_SIZE = re.compile(r"^(\d+(?:\.\d+)?)\s*([kMGTP]?)B$")
_DECIMAL_UNITS = {"": 1, "k": 10**3, "M": 10**6, "G": 10**9, "T": 10**12, "P": 10**15}
_BUILDX_TOTAL = re.compile(r"^Total:\s+(\S+)\s*$")


def _decimal_bytes(size: str) -> int | None:
    """Docker's own size spelling (`12.91GB`, `441.6kB`, `0B`, decimal units) as bytes."""
    found = _DECIMAL_SIZE.match(size.strip())
    if found is None:
        return None
    return round(float(found.group(1)) * _DECIMAL_UNITS[found.group(2)])


def build_cache_bytes(*, wsl_distro: str | None = None) -> int | None:
    """How much build cache Docker holds, in bytes; `None` when it would not say (T203).

    `docker buildx du` first: it sizes the cache and nothing else, and it asks
    the builder a build would use. Its summary ends `Total:\t\t4.79GB`, which on
    a Linux test box (Docker 29.7.2, buildx v0.36.1, 2026-10-04) was the same
    figure `docker system df` gave for `Build Cache` that minute, in 0.1 s
    against 0.34 s. A Docker without the buildx plugin answers `'buildx' is not
    a docker command` and a non-zero exit; that, or a summary with no readable
    `Total:`, falls back to the `Build Cache` row's `Size` in `docker system df
    --format '{{json .}}'`, which every Docker since 1.13 has.

    `None` is not 0, in either direction: a daemon that would not answer, a
    missing row or a size this cannot read credits nothing, and a real 0 is the
    answer "nothing to reuse".
    """
    proc = _docker(["buildx", "du"], timeout=BUILD_CACHE_TIMEOUT_SECONDS, wsl_distro=wsl_distro)
    if proc.returncode == 0:
        for line in reversed(proc.stdout.splitlines()):
            found = _BUILDX_TOTAL.match(line.strip())
            if found is not None:
                total = _decimal_bytes(found.group(1))
                if total is not None:
                    return total
                break
    logger.info("`docker buildx du` gave no total; asking `docker system df`")
    proc = _docker(
        ["system", "df", "--format", "{{json .}}"],
        timeout=BUILD_CACHE_TIMEOUT_SECONDS,
        wsl_distro=wsl_distro,
    )
    if proc.returncode != 0:
        logger.info(f"could not ask Docker how much build cache it holds: {proc.stderr.strip()}")
        return None
    for line in proc.stdout.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("Type") != "Build Cache":
            continue
        size = _decimal_bytes(str(row.get("Size", "")))
        if size is None:
            logger.info(f"could not read Docker's build cache size: {row.get('Size')!r}")
        return size
    logger.info("`docker system df` printed no Build Cache row")
    return None


def image_id(ref: str, *, wsl_distro: str | None = None) -> str | None:
    """The id of the image `ref` names, or None when there is none to read (T224, T225).

    Asked the way `images_built()` asks, one ref at a time. None covers both
    "no such image" and a daemon that did not answer, and the caller decides
    what that means: a rebuild's Stop counts an unanswered id as a tag that
    moved (putting back a tag that never moved costs nothing, and trusting the
    silence would lose the old build), and a kept build is never used on one.
    """
    proc = _docker(["image", "inspect", "--format", "{{.Id}}", ref], wsl_distro=wsl_distro)
    found = proc.stdout.strip() if proc.returncode == 0 else ""
    if not found:
        logger.info(f"no image id for {ref}: {proc.stderr.strip() or 'empty answer'}")
        return None
    return found


def _probe_selinux_argv(selinux_enforcing: Callable[[], bool | None]) -> list[str]:
    """`--security-opt label:disable` for the bind probe, and only when enforcing.

    Measured on a clean Fedora 44 box with SELinux Enforcing (2026-08-30). The
    probe mounts `$HOME` (see `bind_mount_ok()`: it walks up to the nearest
    POPULATED ancestor) and the container is denied it, because `$HOME` is
    `user_home_dir_t` and a confined container may only read `container_file_t`:

        $ docker run --rm --entrypoint ls -v /home/user:/probe:ro <digest> -A /probe
        ls: can't open '/probe': Permission denied
        $ docker run --rm --security-opt label:disable ... -A /probe
        .bash_logout
        .bash_profile
        ...

    An empty listing plus a non-zero exit is exactly the shape `bind_mount_ok()`
    reads as "Docker cannot see that folder", so preflight REFUSED every install
    on every enforcing box — on a host with no Docker Desktop to configure, one
    line after it had printed `[pass] SELinux`. The engine's own SELinux support
    (`{{BIND_LABEL}}` → `:z` on every generated bind, and
    `platform.relabel_for_containers()`) is real and correct, and no install
    could ever reach it: both run at `generate-compose`, which is after this.

    **`:z` is not the fix and must not be used here.** The mount is an ANCESTOR
    of the chosen folder, routinely the user's home directory, and `:z` tells
    the daemon to RECURSIVELY relabel the mount source to `container_file_t` —
    so a read-only listing probe would rewrite the SELinux label of every file
    under `$HOME`. That breaks the desktop session it just relabelled, and
    nothing undoes it. The install's own binds are a different case: they name
    the server folder the app created, so `composegen` labels those and this
    does not.

    What `label:disable` gives up, stated plainly: this one container runs
    unconfined by SELinux for the length of an `ls`. It runs a pinned image
    DIGEST, the mount is `:ro`, the entrypoint is `ls`, and everything but the
    listing is discarded — there is no write path to give up and no code of ours
    or the user's inside. The alternative was refusing the install outright.

    Three answers, not two. `selinux_enforcing()` returns `None` for "could not
    ask", and `None` is neither True nor False here: on `None` the flag is NOT
    added, so a box that cannot answer gets the same probe an unlabelled box
    gets, and a genuine denial still reaches the user as a refusal it can
    explain. Folding "could not ask" into either answer is the mistake
    `platform.selinux_enforcing()`'s own docstring was written against.

    The rule itself lives in `platform.label_disable_args()`, beside
    `bind_label()`, because `git.ContainerGit` needs the same flag for the same
    reason — its read-only `remote get-url origin` was denied on this exact box
    — and two spellings of one security decision are one edit away from being
    two different security decisions. What stays here is the seam adapter: this
    function takes the CALLABLE, because `bind_mount_ok()` takes the SELinux
    question as a parameter so a test can state the machine's answer.
    """
    return platform.label_disable_args(enforcing=selinux_enforcing())


def bind_mount_ok(
    server_dir: Path,
    image: str,
    *,
    timeout: float = BIND_PROBE_TIMEOUT_SECONDS,
    wsl_distro: str | None = None,
    selinux_enforcing: Callable[[], bool | None] | None = None,
) -> bool | None:
    """Can a container actually see the chosen folder? None = could not ask.

    Docker Desktop shares only the directories its file-sharing settings list.
    A folder outside them mounts as an EMPTY directory rather than failing the
    run, so the clone appears to succeed, the build gets an empty context, and
    the first thing that reports the problem is a compile error hours later.

    **The exit code of an `ls` cannot detect that**, and an earlier version of
    this function claimed it could. `ls` on an empty directory exits 0, and the
    server directory is empty or absent at preflight time by construction — so
    against the silently-empty mount this exists for, the probe answered True
    and preflight printed `[pass] sharing the folder with Docker` (review,
    2026-08-23). What it can do is compare CONTENTS: mount a directory the host
    can see files in, and check the container sees files too.

    So the probe runs against the deepest ANCESTOR of `server_dir` that exists
    and has entries in it, not against `server_dir`. Two reasons, both
    load-bearing. Docker Desktop shares whole trees, so an ancestor's answer is
    the chosen folder's answer; and `-v <server_dir>:/probe` on a folder that
    does not exist yet makes Docker CREATE it, which put a directory on disk
    before `guard` had claimed it.

    `None` means the question could not be answered: no docker CLI, the daemon
    did not reply inside `timeout`, or no ancestor with anything in it could be
    found to compare against. That is preflight's *unchecked* tri-state, never a
    pass and never a refusal — a caller that read it as False would refuse an
    install that would have worked.

    Still unverified on macOS in the way that matters: that Docker Desktop
    mounts an unshared folder as empty rather than failing is inherited from
    the Rust launcher, and what a Mac actually does with an unshared path is
    exactly what nobody here can run. What a Mac HAS now produced (2026-08-26)
    is the other half — this function refusing a folder that was shared —
    which is what the second question below exists for.

    On an enforcing SELinux host the probe container is DENIED the mount unless
    it is told not to be confined — see `_probe_selinux_argv()`, which is the
    whole of that story. Measured on a clean Fedora 44 box (2026-08-30): every
    install was refused here, one line after `[pass] SELinux`, and told to
    check a Docker Desktop setting that does not exist on Docker Engine.

    `selinux_enforcing` is resolved at CALL time rather than bound as a
    default, which is `extract.run_plan()`'s shape and is copied from it. The
    default here USED to be `platform.selinux_enforcing` itself, so a test
    that patched the module attribute was not seen — the trap
    `platform.container_user_args()` documents against itself, and this
    function was the real instance of it. Asked of the interpreter on m910q
    before the change (2026-09-04):

        signature(bind_mount_ok).parameters["selinux_enforcing"].default
            is platform.selinux_enforcing        -> True
        the same question of extract.run_plan    -> None

    It was latent rather than live, and by luck rather than by wiring: the
    production caller `preflight._default_bind_probe` passes no seam, so the
    bound default ran and asked the real host, which was the right answer by
    accident; under test it never ran at all, because `test_preflight.py`
    fakes one level up. The rejected alternative was to leave this default
    alone and thread a seam down from `preflight` instead — that fixes the one
    caller there is and leaves the same default waiting for the next one, and
    it would not have made this function's own tests state the machine's
    answer. Nothing about a real install changes either way: `None` here asks
    the same `platform.selinux_enforcing` the bound default was.
    """
    mount = _first_populated_ancestor(server_dir)
    if mount is None:
        logger.info(
            f"nothing on the way up from {server_dir} had files in it, so a container's view of "
            "it cannot be compared against the host's"
        )
        return None
    # `:ro`, because the mount is now an ANCESTOR of the chosen folder and that
    # is routinely the user's home directory. The probe only lists; the clone
    # stage is the thing that needs write access, and it gets its own mount.
    # `--entrypoint ls`, and it is not optional. The probe image is
    # `git.CONTAINER_GIT_IMAGE` — deliberately, so this pulls the exact digest
    # the clone stages pull rather than a second image — and `alpine/git`'s
    # ENTRYPOINT is `git`. Passing `ls -A /probe` after the image name therefore
    # ran `git ls -A /probe`, which exits 1 with "'ls' is not a git command",
    # which this function read as "Docker cannot see that folder" and preflight
    # turned into a refusal. **Every native install, on every platform, was
    # refused** (found by the Windows file-sharing gate 2026-08-24, then
    # reproduced on Linux — it was never Windows-specific).
    ask = selinux_enforcing if selinux_enforcing is not None else platform.selinux_enforcing
    proc = _docker(
        ["run", "--rm", *_probe_selinux_argv(ask), "--entrypoint", "ls"]
        + ["-v", f"{mount}:/probe:ro", image, "-A", "/probe"],
        timeout=timeout,
        wsl_distro=wsl_distro,
    )
    if _cli_missing(proc):
        return None
    # **stdout first, and the exit code second.** The question is whether a
    # container sees what the host sees, and the answer to that is the listing;
    # `ls`'s opinion of the parts it could not reach is not the answer.
    #
    # Asking the other way round refused EVERY macOS install whose chosen folder
    # was new — which is every first install. The chosen folder is empty or
    # absent at preflight time, so the probe walks up to the nearest populated
    # ancestor, routinely the user's home directory, and on a Mac `ls -A` of a
    # home directory prints a full listing AND exits non-zero: Docker Desktop
    # cannot stat the TCC-protected entries in it, busybox `ls` returns failure
    # when it could not stat something. The tester's own run (2026-08-26) shows
    # both halves at once — two `No such file or directory` lines for `.Trash`
    # and `Documents`, then fifteen entries including the folder he had picked.
    # Nothing he could do would make that pass: he re-added the folder to file
    # sharing, added its parent, tried other folders and read a file back out of
    # a container against that exact path, and none of it makes `.Trash`
    # stat-able.
    if any(line.strip() for line in proc.stdout.splitlines()):
        if proc.returncode != 0:
            logger.info(
                f"a container listed {mount} and `ls` still exited {proc.returncode}; the "
                f"listing is the answer. What it could not reach: {proc.stderr.strip()}"
            )
        return True
    # From here the listing was EMPTY, which is the silently-empty mount this
    # check exists for — and the case where the exit code is worth reading.
    #
    # A non-zero exit is the daemon answering "no", which IS an answer — unless
    # it never answered at all. `runner.run()` reports a timeout as 124 with the
    # reason in stderr rather than raising, so that case has to be separated out
    # or a wedged dockerd reaches the user as "your folder is not shared with
    # Docker Desktop", which is a different fix entirely.
    if proc.returncode == _TIMEOUT_RETURNCODE:
        logger.warning(f"the bind-mount probe of {mount} did not answer within {timeout:.0f}s")
        return None
    if proc.returncode != 0:
        # A non-zero exit is Docker answering "no" only if Docker got as far as
        # the mount. It also fails BEFORE that — and on the first install of a
        # first run, the way it fails there is the probe's own image pull. A Mac
        # (2026-08-26) ran the CLI out of Docker Desktop's bundle with launchd's
        # PATH, so `docker` could not exec `docker-credential-desktop`, so every
        # pull died at authentication; this line read that as an unshared folder
        # and preflight refused an install whose folder WAS shared, on a machine
        # where the same command worked from Terminal.
        #
        # The exit code cannot separate them: a denied mount and a failed pull
        # are both non-zero, and matching on error wording would be a list of
        # every message Docker has ever printed. So the daemon is asked a second
        # question instead, and one it answers unambiguously. `docker run` pulls
        # before it mounts, so an image that is not here proves the mount was
        # never reached, which is `None` — preflight's *unchecked*, never a
        # refusal. An image in hand means the failure really was the mount.
        logger.warning(f"the bind-mount probe of {mount} failed: {proc.stderr.strip()}")
        if images_built((image,)) is not True:
            logger.warning(
                f"{image} could not be confirmed on this daemon, so that failure was the "
                "probe's own pull rather than an answer about the folder"
            )
            return None
        return False
    logger.warning(
        f"a container saw {mount} as empty although the host sees files in it — Docker is not "
        "sharing that folder"
    )
    return False


def _first_populated_ancestor(path: Path) -> Path | None:
    """The nearest directory at or above `path` that exists and is not empty.

    The probe needs a host directory whose contents it can hold the container's
    answer up against; an empty one proves nothing either way.
    """
    probe = path
    while True:
        try:
            if probe.is_dir() and any(probe.iterdir()):
                return probe
        except OSError as exc:
            logger.info(f"could not look inside {probe}: {exc}")
            return None
        if probe == probe.parent:
            return None
        probe = probe.parent


# ------------------------------------------- containers, copies and exec (7.3)


@dataclass(frozen=True)
class Mount:
    """One host directory a container gets to see, and whether it may write there.

    `read_only` is a field rather than a caller-supplied `:ro` suffix because it
    is the extraction stage's whole safety argument (phase 7): the user's client
    is mounted read-only, so an interrupted run can leave nothing behind in it.
    A suffix is a string convention; a field is something a test asserts by name.

    **No SELinux label is emitted, and that is a decision the CALLER still owns.**
    Everywhere else in this app a bind mount carries one: `composegen` fills
    `{{BIND_LABEL}}` with `platform.bind_label()`'s answer on every generated
    host bind, and `git.ContainerGit` asks the same function for its writable
    clone mount. Neither answer belongs here. `to_argv()` is pure — it asks the
    machine nothing — and the SELinux question has three answers (enforcing, not
    enforcing, could not ask) that `platform.bind_label()` already keeps three;
    re-asking it from a dataclass would be a second spelling of one security
    decision, which is the mistake `_probe_selinux_argv()` was written against.

    It is also not one label for the whole run. `:z` RECURSIVELY relabels the
    mount source, so it is right for a directory this app created (the server's
    `data/`) and wrong for the user's game client, exactly as it is wrong for
    `$HOME` in `bind_mount_ok()`'s probe. Whoever builds these mounts has to
    answer per mount, and until a caller needs to, no field is invented for it.
    """

    host: Path
    guest: str
    read_only: bool = False


@dataclass(frozen=True)
class ContainerRun:
    """One `docker run --rm`, described rather than spelled.

    `to_argv()` derives the argv from typed fields so a test can audit it by
    field — which mount is `:ro`, which user it runs as, where its cwd is —
    instead of matching a string that a reordered flag would silently break.

    `user_args` is passed in rather than computed here because the uid:gid
    policy has exactly one home, `platform.container_user_args()`, and this
    module knows nothing about a platform's rules (style-guide §3).

    `env` is for values that are not secrets. `to_argv()` is pure, so every
    variable named here lands in the command line as `-e NAME=value`, and a
    command line is world-readable. A secret goes through `exec_stdin()`, which
    forwards it from this process's environment and never spells it.

    There is deliberately no field NAMING `git.py`'s `_READ_ONLY_CONTAINER_ARGS`
    (`--network none --cap-drop ALL --security-opt no-new-privileges
    --read-only`). Those exist there because that container is handed a
    STRANGER'S repository — content this app did not make, which gets to choose
    what git executes. What this class describes is an image this app built from
    its own Dockerfile, running a tool this app named, and needing to write its
    output to a bind mount; `--read-only` and a dropped capability set are not
    free there, and none of it has been measured against the extraction tools.
    A caller that wants hardening states it, and states why, at its own call
    site — which is what `security_args` is for.
    """

    image: str
    argv: tuple[str, ...]
    mounts: tuple[Mount, ...] = ()
    workdir: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    user_args: tuple[str, ...] = ()
    ulimits: tuple[str, ...] = ()
    security_args: tuple[str, ...] = ()
    """The caller's container-level security decision, verbatim, or nothing.

    `user_args`'s twin, and passed the same way and for the same reason: the
    policy has one home in `platform` — `container_user_args()` there,
    `label_disable_args()` here — and this module knows nothing about a
    platform's rules (style-guide §3). Both answers arrive already spelled as
    docker options so that neither decision is respelled at the seam.

    **This is where the SELinux answer for a `docker run` lives, and `Mount` is
    where it does not.** Measured on `yulon-fedora-gate` (Fedora 44, Enforcing,
    Docker 29.7.2, 2026-09-01): a confined container cannot read the user's game
    client at all, because the client is the user's own directory outside the
    server folder, so no `chcon` of ours ever reaches it. The mount-suffix fix
    is not available — `:z` and `:Z` RECURSIVELY relabel their source, and the
    source here is somebody's game install — while `--security-opt
    label:disable` read the client and left its context byte-identical. It is a
    property of the RUN, not of one mount: the extraction container holds both
    the client it must not touch and the `data/` it must write, and one
    container-wide flag serves both.

    Empty by default and emitted verbatim, so a run that states nothing gets
    nothing: turning a container's confinement off is a decision, and a default
    would be one nobody took.
    """

    def to_argv(self, name: str | None = None) -> list[str]:
        """The `docker` argv (without the program name), fields in a fixed order.

        `name` is `--name <name>`, right after `--rm`: `run_container()` names
        every tool's container so a Stop can end it (T303).

        `--rm` always. Every run this describes is a tool that runs to
        completion and leaves its result on a bind mount; a container left
        behind per extraction tool is a leak nobody sees until `docker ps -a`.

        Every option precedes the image on purpose: docker stops reading its
        own options at the image name, so anything after it belongs to the
        tool — which is why `argv` is copied verbatim at the end. Same shape as
        the two argvs this module and `git.py` already spell by hand —
        `bind_mount_ok()`'s probe, and the one built inline in
        `ContainerGit._capture()` — which is `run`, `--rm`, options, `-v`, `-w`,
        image, then theirs.

        The user args, and the security args behind them, sit right after `--rm`
        here rather than just before the image as `git.py` places them. Docker
        reads its own options in any order up to the image name, so the position
        carries no meaning; the test audits by flag rather than by index for the
        same reason. Both go in verbatim: they arrive from `platform` already
        spelled as options, and re-spelling either here would be a second
        spelling of a decision that has one home.

        Raises:
            ValueError: a mount's host path is not absolute. Docker refuses a
                relative bind source with a daemon-side error that names
                neither the field nor the caller; refusing here does both.
        """
        name_args = ["--name", name] if name is not None else []
        return ["run", "--rm", *name_args, *self._options_and_command()]

    def to_create_argv(self, name: str) -> list[str]:
        """`to_argv(name)` as `docker create`: the same container, made and not started (T321).

        `run_container()` creates the container under its name and then attaches to
        it with `docker start -a <name>`, so the name exists before anything can be
        stopped (`container_end`'s module docstring has why).
        """
        labels = ["--label", f"{OWNER_LABEL}={owner_id()}"]
        writes = [folder_id(mount.host) for mount in self.mounts if not mount.read_only]
        named = [ident for ident in writes if ident is not None]
        # T536: a folder with no id to name it by says nothing of where this writes,
        # so every Yu'lon's guard counts the container -- the safe side.
        if writes and len(named) == len(writes):
            labels += ["--label", f"{WRITES_LABEL}={','.join(named)}"]
        return ["create", *labels, *self.to_argv(name=name)[1:]]

    def to_detached_argv(self, name: str) -> list[str]:
        """`to_argv()` for a job that outlives this process: `-d --name <name>`, and NO `--rm`.

        The one run this describes that is not a tool run to completion while
        someone watches: T179's movement-map generation runs for hours in the
        background after the server is up. `--rm` is left off on purpose, and it
        is the whole reason this is a second method rather than a flag: a
        container that removes itself on exit takes its exit code and its log with
        it, and those are how the app -- possibly restarted since -- tells a
        FINISHED generation from one that died. The caller removes the container
        once it has read them (`families/mmaps.py`).

        Raises:
            ValueError: an empty name, or a mount's host path is not absolute
                (see `to_argv()`).
        """
        if not name:
            raise ValueError("a detached container needs a name to be found again")
        return ["run", "-d", "--name", name, *self._options_and_command()]

    def _options_and_command(self) -> list[str]:
        """Every option, then the image and the tool's argv: what both argv forms share."""
        argv = [*self.user_args, *self.security_args]
        for limit in self.ulimits:
            argv += ["--ulimit", limit]
        for mount in self.mounts:
            if not mount.host.is_absolute():
                raise ValueError(f"bind mount source must be absolute, got {mount.host!r}")
            suffix = ":ro" if mount.read_only else ""
            argv += ["-v", f"{mount.host}:{mount.guest}{suffix}"]
        if self.workdir is not None:
            argv += ["-w", self.workdir]
        for name, value in self.env.items():
            argv += ["-e", f"{name}={value}"]
        argv.append(self.image)
        argv.extend(self.argv)
        return argv


def run_container(
    spec: ContainerRun, *, sink: OutputSink, cancel: threading.Event | None = None
) -> AttachedRun:
    """Run one throwaway container attached, streaming its output to `sink`.

    `run_attached()` does the work, and its bounded tail. **A Stop ends the
    container too (T303)**, where for a build only the client is abandoned: a
    tool's container writes into the server's `data/`, and on Docker Desktop
    one whose CLI was ended went on extracting until it was removed by hand
    (yulon-win11, 2026-10-05). So each container is named
    (`TOOL_CONTAINER_PREFIX`), a set `cancel` ends the CLI within
    `_STOP_POLL_SECONDS` even while the tool is silent, and the container is
    then killed and removed by its name (`container_end.end_container()`, the
    second look for a late create included) before this returns -- so whatever
    the caller does next, putting old data back included, does not race it. A
    run abandoned by an exception ends its container the same way. A `cancel`
    already set when this is called starts nothing.

    **Created, then started (T321).** `docker create --rm --name <name>` runs to
    its end first (`container_end.create()`; no Stop ends it), then a streamed
    `docker start -a <name>` attaches, and its exit code is the container's, as
    `docker run`'s was. A Stop during the create is seen once it returns, and the
    container it made is removed without having started; one during the start
    ends a container known to exist, so "gone" needs no second look. A create
    Docker refused (an image it does not have: 125, as `docker run` said) comes
    back as the run's failure in Docker's words, with nothing started.

    Output is read merged because the tools this exists for — the map, vmap and
    mmap extractors — print their progress to stderr, which
    `runner.stream()` otherwise withholds until the tool has exited.

    Four different things can come back, and they stay four, because a stage
    that cannot tell them apart says the wrong sentence to the user:

    * `0` — the tool ran and was happy.
    * `CANCELLED_RETURNCODE` — the user pressed Stop. Not an exit status the
      tool produced, and not a reason to say the extraction failed.
    * `_CLI_MISSING_RETURNCODE` with `DOCKER_CLI_MISSING_HELP` as the whole
      tail — nothing was spawned at all. "Could not ask", not "it answered no".
    * anything else — what the container exited with, its last lines kept.
      `docker run` uses 125 for an image it could not find or pull, so the
      caller reads the words rather than inventing a meaning for the number.

    A bad spec is none of those. `to_argv()` raises `ValueError` for a relative
    mount source and that is left to propagate: it says this code built the
    wrong command, where an `AttachedRun` would say the user's machine could
    not run the tool, and the install engine treats those very differently.

    **This addresses the local daemon and takes no `wsl_distro`** — the one
    function in this module that reaches the spawn seam without being able to
    name a distro, which is why it is listed in the completeness test's
    `_DAEMON_AGNOSTIC` rather than growing the parameter. A bind mount is the
    reason: `Mount.host` is a path on THIS machine, `to_argv()` is pure and
    translates nothing, and handing a Windows drive path to a docker living
    inside a distro mounts a directory that does not exist there.
    Making this WSL-capable is a question about where the mounts are built, not
    one this function can answer alone — the same shape as the SELinux label
    `Mount` deliberately leaves to its caller. `git.ContainerGit._capture()`,
    the app's other `docker run` over a host bind, resolves `docker_program()`
    directly for exactly this reason.

    The cwd handed to the docker client is this process's own and nothing
    depends on it: `to_argv()` refuses a relative mount source, so no path in
    the argv is resolved against it. The container's working directory is
    `spec.workdir`, which is the one that matters.

    `sink` is required, not optional as in `run_attached()`: every caller of
    this is a stage that has a log panel to fill, and a run whose only trace
    is a 200-line tail after an hour is the silence phase 6 measured against.
    """
    if cancel is not None and cancel.is_set():
        # Nothing is started after a Stop (T303): the token was set while the
        # caller was getting here -- laying the client packs, or a tool before.
        logger.info(f"run_container(): stopped before it began; not running {spec.argv[0]}")
        return AttachedRun(CANCELLED_RETURNCODE)
    name = f"{TOOL_CONTAINER_PREFIX}{uuid.uuid4().hex[:12]}"
    argv = spec.to_create_argv(name=name)
    launcher = platform.docker_prefix(None)
    if launcher is None:
        logger.debug(f"no docker CLI on this host; not running: docker {' '.join(argv)}")
        return AttachedRun(_CLI_MISSING_RETURNCODE, (platform.DOCKER_CLI_MISSING_HELP,))
    logger.info(f"run_container(): `docker {' '.join(argv)}`, then `docker start -a {name}`")
    writes = tuple(mount.host for mount in spec.mounts if not mount.read_only)
    with _UNENDED_LOCK:
        _UNENDED[name] = writes
    try:
        made, refused = container_end.create(launcher, argv, name, what=_TOOL)
    except OSError as exc:
        # Docker uninstalled while the launcher is open, as `run_attached()` meets it.
        _ended(name)
        logger.warning(f"{launcher[0]} could not be started: {exc}")
        return AttachedRun(_CLI_MISSING_RETURNCODE, (platform.DOCKER_CLI_MISSING_HELP,))
    except BaseException:
        # Taken away mid-create: the CLI was ended, so the daemon may still make it.
        if container_end.end_container(launcher, name, what=_TOOL) is None:
            _ended(name)
        raise
    if made.returncode != 0:
        if refused is None:
            _ended(name)
        said = tuple(
            line for line in ansi.strip(f"{made.stdout}\n{made.stderr}").splitlines() if line
        ) or (f"docker create exited {made.returncode}",)
        if runner.timed_out(made):
            said = (
                *said,
                f"docker create did not answer within {container_end.CREATE_TIMEOUT:g} s.",
            )
        for line in said:
            try:
                sink(line)
            except Exception as exc:  # noqa: BLE001 - `run_attached()`'s rule for a dead sink
                logger.warning(f"the output sink stopped accepting lines: {exc}")
                break
        # A Stop pressed while the create was failing is still the Stop (cold review).
        code = CANCELLED_RETURNCODE if cancel is not None and cancel.is_set() else made.returncode
        return AttachedRun(code, said, container_left=name if refused else "")
    try:
        if cancel is not None and cancel.is_set():
            # Stopped while Docker was creating it: it exists and has not started.
            run = AttachedRun(CANCELLED_RETURNCODE)
        else:
            # `run_attached()` ends the CLI on the cancel itself (T526); the container
            # is ended by its name below.
            run = run_attached(
                ["start", "-a", name], Path.cwd(), sink=sink, cancel=cancel, merge_stderr=True
            )
    except BaseException:
        # Abandoned: whatever took the run away mid-tool left its container running.
        # A refusal cannot travel in a result here, so it stays in `_UNENDED`.
        if container_end.end_container(launcher, name, what=_TOOL, created=True) is None:
            _ended(name)
        raise
    if cancel is None or not cancel.is_set():
        if run.returncode == 0:
            _ended(name)  # it ran to its end, and `--rm` removed it
            return run
        # Codex review round 3: `--rm` acts on a container that ran. One the daemon
        # would not start (or a CLI that lost the daemon mid-run) may still be there,
        # created or running, so it is removed here; one that ran and failed is gone.
        refused = container_end.end_container(launcher, name, what=_TOOL, created=True)
        if refused is None:
            _ended(name)
            return run
        try:
            sink(tool_container_left_line(name, refused, stopped=False))
        except Exception as exc:  # noqa: BLE001 - `run_attached()`'s rule for a dead sink
            logger.warning(f"the output sink stopped accepting lines: {exc}")
        return AttachedRun(run.returncode, run.tail, container_left=name)
    refused = container_end.end_container(launcher, name, what=_TOOL, created=True)
    if refused is None:
        _ended(name)
    else:
        try:
            sink(tool_container_left_line(name, refused))
        except Exception as exc:  # noqa: BLE001 - `run_attached()`'s rule for a dead sink
            logger.warning(f"the output sink stopped accepting lines: {exc}")
    return AttachedRun(CANCELLED_RETURNCODE, run.tail, container_left=name if refused else "")


_UNENDED: dict[str, tuple[Path, ...]] = {}
"""Tool containers this process started and has not seen end, with the folders they write.

Codex adversarial review, round 2: a run abandoned by an exception (a closed stream,
the app quitting) re-raises what abandoned it, so a refused removal cannot reach its
caller in a result -- and a worker still ending its container when the abandoner
stopped waiting has no result yet. Both stay here until they are seen gone, for
`tool_containers_writing_into()`."""

_UNENDED_LOCK = threading.Lock()


def _ended(name: str) -> None:
    with _UNENDED_LOCK:
        _UNENDED.pop(name, None)


@dataclass(frozen=True)
class StillWriting:
    """The tool containers that may still be writing into a folder, by whose they are (T544).

    `ours`: this process's, one an earlier run of this Yu'lon left, or one with no
    owner label (made before the label was) -- the player may remove these. `others`:
    another Yu'lon's on the same daemon (a second user, or a WSL one beside Windows),
    its live run, which only it ends. True when either holds a name.
    """

    ours: tuple[str, ...] = ()
    others: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.ours or self.others)

    @property
    def names(self) -> tuple[str, ...]:
        """Every name, sorted."""
        return tuple(sorted(self.ours + self.others))


def tool_containers_writing_into(folder: Path) -> StillWriting:
    """The tool containers that may still be writing into `folder` (T303), by name and whose.

    Started by `run_container()` with `folder` as a writable mount and not yet seen
    to end: still running, or stopped and refused removal. A Re-extract asks before
    it starts, and a stopped one before it puts old map data back into that folder.

    **Docker is asked again about each one first (cold review).** The player may
    have removed it by hand since, and a name kept on memory alone would block every
    later press until the app restarts. A container Docker says is gone, or has
    exited, is dropped; one it does not answer about is kept, which is the safe side.
    """
    wanted = os.path.normcase(os.path.abspath(folder))
    with _UNENDED_LOCK:
        named = sorted(
            name
            for name, writes in _UNENDED.items()
            if any(os.path.normcase(os.path.abspath(path)) == wanted for path in writes)
        )
    still: list[str] = []
    for name in named:
        facts = container_exit(name, timeout=_ASK_AGAIN_TIMEOUT)
        if facts.missing or facts.status in ("exited", "dead"):
            logger.info(f"the extraction tool container {name} is gone now")
            _ended(name)
        else:
            still.append(name)
    left = tool_containers_left_running(folder)
    return StillWriting(ours=tuple(sorted({*still, *left.ours})), others=left.others)


def tool_containers_left_running(folder: Path) -> StillWriting:
    """Running extraction tool containers this process did not start that may write `folder`.

    Codex review of the stop-paths branch: `_UNENDED` is this process's memory and
    goes with it. A container whose removal Docker refused, followed by the player
    closing Yu'lon, would be forgotten, and the next press would put old map data
    back under a tool still writing into `data/` -- the very thing the guard is for.
    So Docker is asked for every running `yulon-extract-*` labelled as this Yu'lon's
    (`OWNER_LABEL`, `owner_id()`) or not labelled at all (made before the label was);
    one this process does not know was started by an earlier one. Its mounts are not
    read: a bind's source is spelled the daemon's way (a Docker Desktop VM path on
    Windows), so a tool nobody here started is counted as writing into any server's
    `data/`, the safe side. Another Yu'lon's -- a second user, or a WSL one beside
    Windows, on the same daemon -- is its own live run and is left out (cold review),
    unless its `WRITES_LABEL` names `folder` or names nothing: two Yu'lons may share a
    server folder, and that one may be writing into it (Codex review of that fix).
    "Names `folder`" is by `folder_id()`, the folder's own id file, so a Windows and a
    WSL Yu'lon, or a symlink alias, reaching one folder under two spellings name it
    once (T536). An older Yu'lon's label (`yulon.writes`, a path hash) is not read:
    a hash cannot tell an alias from another folder, so its container counts, and a
    folder here with no id to compare leaves no other Yu'lon's container out.

    Another Yu'lon's that counts comes back in `StillWriting.others` (T544): it is that
    Yu'lon's live run, and the press says so rather than offering to remove it.

    A Docker that does not answer adds none. The guard's question is "could a tool
    still be writing?", and a daemon that is down runs nothing; the press asks Docker
    for the tool's own container next and says so if it cannot.
    """
    with _UNENDED_LOCK:
        known = set(_UNENDED)
    proc = _docker(
        [
            "ps",
            "--filter",
            f"name={TOOL_CONTAINER_PREFIX}",
            "--format",
            f'{{{{.Names}}}}\t{{{{.Label "{OWNER_LABEL}"}}}}\t{{{{.Label "{WRITES_LABEL}"}}}}',
        ],
        timeout=_ASK_AGAIN_TIMEOUT,
    )
    if proc.returncode != 0:
        logger.info(f"could not list the running extraction tool containers: {proc.stderr.strip()}")
        return StillWriting()
    # Before AND after the question (cold review): a run of this app that ended, or
    # started, while Docker answered is this app's own and not one left behind.
    with _UNENDED_LOCK:
        known |= set(_UNENDED)
    mine, here = owner_id(), folder_id(folder)
    left: list[str] = []
    theirs: list[str] = []
    for row in proc.stdout.splitlines():
        name, owner, writes = ([*row.strip().split("\t"), "", ""])[:3]
        if not name.startswith(TOOL_CONTAINER_PREFIX) or name in known:
            continue
        # Another Yu'lon's live run is not ours to count, unless it says it writes
        # here or says nothing of where. One with no label was made before the label
        # was, so it still counts: one user is no less guarded. Only an id can say
        # "elsewhere" (T536): none here, or none on the container, counts it.
        others = owner.strip() and owner.strip() != mine
        named = {part for part in writes.strip().split(",") if part}
        if others and here is not None and named and here not in named:
            continue
        (theirs if others else left).append(name)
    left.sort()
    theirs.sort()
    for name in left:
        logger.warning(f"the extraction tool container {name} was left running by an earlier run")
    for name in theirs:
        logger.info(
            f"another Yu'lon's extraction tool container {name} may be writing into {folder}"
        )
    return StillWriting(ours=tuple(left), others=tuple(theirs))


WRITES_LABEL = "yulon.writes-ids"
"""The label `docker create` puts on an extraction tool container: the folders it writes,
each as `folder_id()`, comma-separated (Codex review of the owner label; T536).

A new key, not the `yulon.writes` of before T536, which named a folder by a hash of
its path (Codex adversarial review of T536). An older Yu'lon reads only that key, so
it finds nothing on a container made here and counts it, the safe side, rather than
matching a path hash that an alias spells differently."""


FOLDER_ID_FILE = ".yulon-folder-id"
"""In a folder an extraction tool writes (`data/`): a random id naming that folder (T536)."""

_FOLDER_ID = re.compile(r"[0-9a-f]{32}")


def folder_id(folder: Path) -> str | None:
    """The id in `folder`'s `FOLDER_ID_FILE`, made on first use; None when there is none to be had.

    T536: two Yu'lons on one daemon can reach one folder under two spellings -- a
    Windows one by `C:\\...`, a WSL one by `/mnt/c/...`, or either through a symlink
    -- and a path hash names it twice. A file in the folder is read the same way
    under every spelling. Made whole in a temporary sibling and published with
    `os.link()`, which refuses a name that is taken, so a reader sees no file or the
    whole one and two Yu'lons making it at once end with the one that won.

    A filesystem with no hard links (vfat, exFAT) refuses the link; there the file is
    made by an exclusive create instead (T543), written in place, so a reader at that
    instant may find it empty and answer None for that one ask.

    None, and the caller takes the safe side, when it can be neither read nor made:
    no folder, one that refuses the write, or a file that does not hold an id. Not
    remembered, so the next ask tries again.

    A folder copied by hand carries its id: a guard on the copy then counts the
    original's tool as writing into it, a refusal and never a mix. Device and inode
    cannot tell a copy apart here, because Windows and WSL read different ones for
    the same NTFS folder.
    """
    target = folder / FOLDER_ID_FILE
    found = _read_folder_id(target)
    if found is not None or os.path.lexists(target):
        return found
    try:
        fd, name = tempfile.mkstemp(prefix=f"{FOLDER_ID_FILE}.", suffix=".yulon-new", dir=folder)
    except OSError as exc:
        logger.info(f"could not make an id for {folder}: {exc}")
        return None
    tmp = Path(name)
    text = f"{uuid.uuid4().hex}\n"
    try:
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            # `mkstemp` makes it owner-only; another user's Yu'lon reads it. Refused, the
            # id is still this user's to use (cold review).
            os.chmod(tmp, 0o644)
        except OSError as exc:
            logger.info(f"could not make {tmp} readable to other users: {exc}")
        try:
            os.link(tmp, target)
        except FileExistsError:
            pass  # another Yu'lon made it meanwhile: theirs is the id
        except OSError as exc:
            logger.info(f"no hard link for {target} ({exc}); creating it exclusively instead")
            _create_folder_id(target, text)
    except OSError as exc:
        logger.info(f"could not make an id for {folder}: {exc}")
        return None
    finally:
        _drop_temp(tmp)
    return _read_folder_id(target)


def _create_folder_id(target: Path, text: str) -> None:
    """`folder_id()`'s publish where hard links are refused: create-if-absent, written in place.

    A name another Yu'lon took meanwhile is theirs. A write that fails part-way removes
    the file it made, so a half id is not left to read as none for ever.

    Raises:
        OSError: it could not be made.
    """
    try:
        fd = os.open(
            target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o644
        )
    except FileExistsError:
        return
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode("ascii"))
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def _drop_temp(tmp: Path) -> None:
    """Remove `folder_id()`'s temp file; a refusal is logged, never raised (cold review).

    On Windows another process reading it (a sharing violation) refuses the removal,
    and an exception here would leave `docker create` or the press with no answer at
    all instead of the id, or None and the safe side.
    """
    try:
        tmp.unlink(missing_ok=True)
    except OSError as exc:
        logger.info(f"could not remove the temporary file {tmp}: {exc}")


def _read_folder_id(target: Path) -> str | None:
    """`FOLDER_ID_FILE`'s id; None when it is not there, not readable, or not an id."""
    try:
        text = target.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return text if _FOLDER_ID.fullmatch(text) else None


OWNER_LABEL = "yulon.owner"
"""The label `docker create` puts on every extraction tool container: whose Yu'lon made it."""


def owner_id() -> str:
    """This Yu'lon's owner label value: a hash of its config folder (cold review).

    One daemon can serve more than one Yu'lon -- two Linux users, or a Windows and
    a WSL one on Docker Desktop -- each with its own config folder. So a running
    tool labelled with another value is that Yu'lon's live run, not one ours left.
    """
    folder = os.path.normcase(os.path.abspath(platform.config_dir()))
    return hashlib.sha256(folder.encode("utf-8")).hexdigest()[:16]


_ASK_AGAIN_TIMEOUT = 20.0
"""How long Docker gets to say whether a remembered tool container is still there."""


# ------------------------------------- T543: one press per folder, decided by the daemon

CLAIM_PREFIX = "yulon-claim-"
"""A folder claim's container is `yulon-claim-<folder id>` (T543): one name per folder."""

CLAIM_LABEL = "yulon.claim"
"""A claim's label: a nonce of the press that made it, so it knows the claim it sees is its own."""

_CLAIM_UP_TIMEOUT = 60.0
"""How long a claim gets to be running before the press goes on without one."""

_CLAIM_RELEASE_TIMEOUT = 15.0
"""How long a released claim's CLI gets to end on its own before it is killed."""

_CLAIM_POLL_SECONDS = 0.1

_CLAIM_ASK_TIMEOUT = 5.0
"""How long any one Docker question about a claim may take (Codex review of the folds):
a hung daemon must not hold a press, or a Stop, for `_ASK_AGAIN_TIMEOUT`."""

_CLAIM_LOOK_TIMEOUT = 1.0
"""How long one look at a coming-up claim may take, so a Stop is seen between looks."""

_CLAIM_SWEEP_SECONDS = _CLAIM_UP_TIMEOUT + 30.0
"""How long a claim given up, or not removed, is looked for in the background to remove it:
past the whole wait for one to come up, with margin (Codex review of the folds)."""

_CLAIM_SWEEP_POLL = 0.5

_CLAIM_ABANDON_WAIT = 0.5
"""How long the CLI of a claim given up while it came up gets before it is killed."""

_NAME_IN_USE = re.compile(r"Conflict\. The container name .* is already in use", re.IGNORECASE)
"""How the daemon refuses a second container of one name (m910q Engine and Docker Desktop,
measured 2026-10-07, `.notes/gates/live-t543-2026-10-07`)."""

_CLAIMS_HELD: set[str] = set()
"""The claims this process holds, by name."""

_CLAIMS_LOCK = threading.Lock()


class FolderClaimed(Exception):
    """Another press holds the folder's claim (T543), the container `name`.

    `ours`: its owner label is this Yu'lon's. `here`: this process holds it -- a press
    still running; ours and not here is one an earlier run of this Yu'lon left.
    `known` False: Docker refused the name and then would not say whose it is.
    """

    def __init__(self, name: str, *, ours: bool, here: bool = False, known: bool = True) -> None:
        super().__init__(name)
        self.name = name
        self.ours = ours
        self.here = here
        self.known = known


class ClaimUnavailable(Exception):
    """The folder's claim could not be made, for a reason other than another press (T543)."""


class ClaimStopped(ClaimUnavailable, StopTookEffect):
    """Stop was pressed while the claim came up (cold review of T543): nothing was touched."""


_IMAGE_GONE = re.compile(
    r"No such image|Unable to find image|pull access denied|repository does not exist",
    re.IGNORECASE,
)
_DAEMON_DOWN = re.compile(
    r"Cannot connect to the Docker daemon|error during connect|daemon is not running"
    r"|failed to connect to the docker API",
    re.IGNORECASE,
)


@dataclass
class _Claim:
    name: str
    container: str
    proc: subprocess.Popen[bytes]
    nonce: str


@dataclass(frozen=True)
class ClaimHeld:
    """What `folder_claim()` yields: the claim's container name, and whether it was lost (T549).

    `lost` is set when the claim's CLI ends before its press lets it go: Docker Desktop
    restarted, or the container was removed. From then on a second Yu'lon can claim
    the folder, so a press that holds this must stop.
    """

    name: str
    lost: threading.Event
    confirm: Callable[[], bool] = field(default=lambda: True, compare=False)
    """Asks Docker NOW whether the claim still runs as this press's own (by its nonce)."""

    def held(self) -> bool:
        """Is the claim still this press's, asked now (Codex reviews of T549)? False sets `lost`.

        The watcher's `lost` comes a moment after the CLI ends, from another thread, so
        a press about to change the folder asks this instead: Docker itself, before each
        change. An unanswered question is not a claim held.
        """
        if self.lost.is_set():
            return False
        if self.confirm():
            return True
        self.lost.set()
        return False


@contextmanager
def folder_claim(
    folder: Path, image: str, cancel: threading.Event | None = None
) -> Iterator[ClaimHeld]:
    """Hold `folder`'s claim while inside (T543); a press goes ahead only inside one.

    Two Yu'lons on one daemon -- a second user, or a Windows and a WSL one -- can press
    Re-extract on one server folder at once, and both find no tool running yet. The
    claim is a container named by the folder's id (`folder_id()`), so every spelling
    of the folder names one claim and the daemon, which refuses a second container of
    one name, decides which press goes ahead.

    It is `docker run --rm -i ... cat` with its stdin a pipe this process holds, so it
    lives exactly as long as this process: closing the pipe, or this process dying,
    ends `cat`, and `--rm` removes it (measured on Docker Engine and Docker Desktop,
    0.7-1.7 s). A claim found in place is never removed here, whoever's it is (Codex
    adversarial review): the caller says what it is and, for this Yu'lon's own, how.

    Yields a `ClaimHeld`, whose `lost` a watcher sets if the claim's CLI ends before
    the press lets it go (T549). Without a claim nothing is yielded: the press stops
    there (Codex adversarial review, twice), since the tools need the same docker and
    image and nothing is lost by stopping before the old map data moves.

    `cancel` set while the claim comes up ends the wait at once (cold review): the
    claim's CLI goes, and so does a claim it had made.

    Raises:
        FolderClaimed: another press holds it.
        ClaimStopped: `cancel` was set while it came up.
        ClaimUnavailable: no claim could be made (no folder id, no docker, no image, a
            daemon that refused or did not answer); its text says what to do.
    """
    ident = folder_id(folder)
    if ident is None:
        # Not by its path (Codex adversarial review): a path names one folder twice
        # under two spellings, the very race the claim is for.
        raise ClaimUnavailable(
            f"The folder would not give or take Yu'lon's id file, {FOLDER_ID_FILE}: if that "
            "file is there, delete it; if the folder is read-only, make it writable."
        )
    held = _take_claim(CLAIM_PREFIX + ident, image, cancel, again=True)
    lost = threading.Event()
    letting_go = threading.Event()
    threading.Thread(
        target=_watch_claim, args=(held, lost, letting_go), name="yulon-claim-watch", daemon=True
    ).start()
    try:
        yield ClaimHeld(held.name, lost, lambda: _claim_still_ours(held))
    finally:
        letting_go.set()  # before the release ends the CLI: that end is not a loss
        _release_claim(held)


_CLAIM_STILL_OURS_ASKS = 2
"""How many answers it takes to call a claim lost (cold review of T549): one slow or failed
`docker inspect` is not a lost claim, and a loss, once said, stays said."""

_CLAIM_ASK_GAP = 1.5
"""Seconds between two such answers (cold review 2 of T549). Back to back, a transient
fault -- the daemon answering "no such container" for a moment, as it can while it
restarts its network -- fails both in milliseconds. The most a loss costs is two bounded
asks plus this one wait."""


def _claim_still_ours(held: _Claim) -> bool:
    """Does Docker say the claim `held` still runs, carrying this press's nonce? (T549)

    Asked with `_CLAIM_ASK_TIMEOUT`, and once more, `_CLAIM_ASK_GAP` later, before the
    answer is no.
    """
    for attempt in range(_CLAIM_STILL_OURS_ASKS):
        if attempt:
            time.sleep(_CLAIM_ASK_GAP)
        facts = _claim_facts(held.name, timeout=_CLAIM_ASK_TIMEOUT)
        if facts is not None and facts.nonce == held.nonce and facts.status == "running":
            return True
    return False


def _watch_claim(held: _Claim, lost: threading.Event, letting_go: threading.Event) -> None:
    """Set `lost` if the claim's CLI ends before its press lets it go (T549). Never raises.

    The CLI is `docker run -i` attached to the claim: it ends when the container does
    (removed, or Docker restarted) as well as when the press closes its stdin.
    """
    try:
        held.proc.wait()
    except Exception as exc:  # noqa: BLE001 - a watcher must not take the press down
        logger.warning(f"the claim {held.name} could not be watched: {exc!r}")
        return
    if not letting_go.is_set():
        logger.warning(
            f"the claim {held.name} ended before its press did (exit {held.proc.returncode})"
        )
        lost.set()


def _take_claim(name: str, image: str, cancel: threading.Event | None, *, again: bool) -> _Claim:
    """Start the claim `name` and see it running as this press's own.

    Raises:
        FolderClaimed: the daemon refused the name: another press holds it.
        ClaimUnavailable: any other reason it is not running as ours.
    """
    if cancel is not None and cancel.is_set():
        raise ClaimStopped("Stop was pressed before the folder was reserved.")
    program = platform.docker_program()
    if program is None:
        raise ClaimUnavailable("Docker's command-line tool was not found; start or install Docker.")
    nonce = uuid.uuid4().hex
    argv = [
        program,
        "run",
        "--rm",
        "-i",
        "--name",
        name,
        "--label",
        f"{OWNER_LABEL}={owner_id()}",
        "--label",
        f"{CLAIM_LABEL}={nonce}",
        "--network",
        "none",
        "--entrypoint",
        "sh",
        image,
        "-c",
        "cat >/dev/null",
    ]
    logger.info(f"claiming the folder: `docker {' '.join(argv[1:])}`")
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=runner.child_env(),
            creationflags=runner.creationflags(),
        )
    except OSError as exc:
        raise ClaimUnavailable(f"Docker could not be started ({exc}); is Docker running?") from exc
    try:
        return _claim_coming_up(name, image, proc, nonce, cancel, again=again)
    except BaseException:
        # Not ours after all, or abandoned while it came up: its CLI goes -- at once,
        # not after the release's wait, for a Stop's sake -- and a claim this press
        # made goes with it (by its nonce, never another's).
        cut_short = proc.poll() is None
        _end_claim_cli(proc, wait=_CLAIM_ABANDON_WAIT)
        facts = _claim_facts(name, timeout=_CLAIM_LOOK_TIMEOUT)
        removed = False
        if facts is not None and facts.nonce == nonce:
            removed = _remove_claim(facts.container, timeout=_CLAIM_LOOK_TIMEOUT)
        if cut_short and not removed:
            # Ending the CLI does not cancel a `run` the daemon already has (Codex review
            # of the folds): the claim may appear later and would block every press.
            _start_sweep(name, nonce)
        raise


def _start_sweep(name: str, nonce: str) -> None:
    """`_sweep_late_claim()` on a background thread, so nothing waits for it."""
    threading.Thread(
        target=_sweep_late_claim, args=(name, nonce), name="yulon-claim-sweep", daemon=True
    ).start()


def _sweep_late_claim(name: str, nonce: str) -> None:
    """Remove the claim `name` carrying `nonce` if Docker makes it after its press gave up.

    Looked for in the background for `_CLAIM_SWEEP_SECONDS`, so a Stop is not held up.
    Another press's claim on the name meanwhile does not end the look (Codex review): it
    may go before this one's request reaches the daemon. Only `nonce`'s is removed.
    """
    deadline = time.monotonic() + _CLAIM_SWEEP_SECONDS
    try:
        while time.monotonic() < deadline:
            facts = _claim_facts(name)
            if facts is not None and facts.nonce == nonce:
                logger.info(f"removing the claim {name}, made after its press gave it up")
                if _remove_claim(facts.container):
                    return
            time.sleep(_CLAIM_SWEEP_POLL)
    except Exception as exc:  # noqa: BLE001 - a background thread has no caller to tell
        logger.warning(f"stopped looking for the claim {name}: {exc}")


def _claim_coming_up(
    name: str,
    image: str,
    proc: subprocess.Popen[bytes],
    nonce: str,
    cancel: threading.Event | None,
    *,
    again: bool,
) -> _Claim:
    """Wait for `proc`'s claim to run as this press's own (`nonce`), or say why it did not."""
    deadline = time.monotonic() + _CLAIM_UP_TIMEOUT
    while True:
        # Before each look, and each look short (Codex review of the cold-review folds):
        # a Docker that does not answer must not hold a Stop for a whole question.
        if cancel is not None and cancel.is_set():
            raise ClaimStopped("Stop was pressed before the folder was reserved.")
        facts = _claim_facts(name, timeout=_CLAIM_LOOK_TIMEOUT)
        # Running, not only there (Codex adversarial review, round 3): a claim whose
        # command failed is seen created, with this nonce, before `--rm` takes it.
        if facts is not None and facts.nonce == nonce and facts.status == "running":
            if cancel is not None and cancel.is_set():  # pressed while Docker answered
                raise ClaimStopped("Stop was pressed before the folder was reserved.")
            with _CLAIMS_LOCK:
                _CLAIMS_HELD.add(name)
            return _Claim(name, facts.container, proc, nonce)
        if proc.poll() is not None:
            assert proc.stderr is not None
            said = proc.stderr.read().decode("utf-8", "replace").strip()
            proc.stderr.close()
            if _NAME_IN_USE.search(said):
                return _claim_in_use(name, image, cancel, again=again)
            raise ClaimUnavailable(_claim_refused(image, said or f"it exited {proc.returncode}"))
        if time.monotonic() > deadline:
            raise ClaimUnavailable(
                f"Docker had not started its reservation after {_CLAIM_UP_TIMEOUT:.0f} s; "
                "wait until Docker has started."
            )
        if cancel is not None and cancel.wait(_CLAIM_POLL_SECONDS):
            raise ClaimStopped("Stop was pressed before the folder was reserved.")
        elif cancel is None:
            time.sleep(_CLAIM_POLL_SECONDS)


def _claim_refused(image: str, said: str) -> str:
    """What to do about a claim Docker refused, by Docker's own words (cold review of T543)."""
    if _IMAGE_GONE.search(said):
        return (
            f"The server's extraction image, {image}, is no longer in Docker (Docker said: "
            f"{said}). Press \u201c{server_build_presses.REBUILD}\u201d on the Server tab to make "
            "it again."
        )
    if _DAEMON_DOWN.search(said):
        return f"Docker is not running (Docker said: {said}); start Docker."
    return f"Docker refused it (Docker said: {said})."


def _claim_in_use(name: str, image: str, cancel: threading.Event | None, *, again: bool) -> _Claim:
    """The daemon said `name` is taken: say whose; never remove it (T543)."""
    facts = _claim_facts(name, timeout=_CLAIM_LOOK_TIMEOUT)
    if cancel is not None and cancel.is_set():
        raise ClaimStopped("Stop was pressed before the folder was reserved.")
    if facts is None:
        if again:  # it ended between the refusal and the question (a Stop is seen there)
            return _take_claim(name, image, cancel, again=False)
        # Refused twice, and twice no answer about whose (cold review): not "another's".
        raise FolderClaimed(name, ours=False, known=False)
    with _CLAIMS_LOCK:
        here = name in _CLAIMS_HELD
    raise FolderClaimed(name, ours=facts.owner == owner_id(), here=here)


class _ClaimFacts(NamedTuple):
    container: str
    status: str
    nonce: str
    owner: str


def _claim_facts(name: str, timeout: float = _CLAIM_ASK_TIMEOUT) -> _ClaimFacts | None:
    """The claim `name`'s container id, state, nonce and owner; None when none (or no answer)."""
    fmt = (
        f'{{{{.Id}}}}\t{{{{.State.Status}}}}\t{{{{index .Config.Labels "{CLAIM_LABEL}"}}}}'
        f'\t{{{{index .Config.Labels "{OWNER_LABEL}"}}}}'
    )
    proc = _docker(["inspect", name, "--format", fmt], timeout=timeout)
    if proc.returncode != 0:
        return None
    facts = _ClaimFacts(*([*proc.stdout.strip().split("\t"), "", "", ""])[:4])
    return facts if facts.container else None


def _release_claim(held: _Claim) -> None:
    """End a claim this process holds: its stdin closed, its CLI waited for, its container gone."""
    with _CLAIMS_LOCK:
        _CLAIMS_HELD.discard(held.name)
    _end_claim_cli(held.proc)
    if not _remove_claim(held.container):
        # A daemon slow to answer (Codex review of the folds): left to the sweep, by nonce.
        _start_sweep(held.name, held.nonce)


def _end_claim_cli(proc: subprocess.Popen[bytes], *, wait: float = _CLAIM_RELEASE_TIMEOUT) -> None:
    """Close the claim's stdin, wait for its CLI, kill it if it will not end; never raises."""
    try:
        if proc.stdin is not None:
            proc.stdin.close()
    except OSError as exc:
        logger.info(f"the claim's stdin would not close: {exc}")
    try:
        proc.wait(timeout=wait)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    if proc.stderr is not None and not proc.stderr.closed:
        proc.stderr.close()


def _remove_claim(container: str, timeout: float = _CLAIM_ASK_TIMEOUT) -> bool:
    """`docker rm -f` a claim by its container id; True once it is gone (already gone counts)."""
    proc = _docker(["rm", "-f", container], timeout=timeout)
    if proc.returncode != 0 and not _NO_SUCH_CONTAINER.search(proc.stderr):
        logger.warning(f"the claim {container} could not be removed: {proc.stderr.strip()}")
        return False
    return True


TOOL_CONTAINER_PREFIX = "yulon-extract-"
"""Every extraction tool's container is `yulon-extract-<12 hex>` (T303), as clones are
`yulon-git-<12 hex>` (T240): a name is what a Stop ends a container by."""

_TOOL = "extraction tool"

_STOP_POLL_SECONDS = 0.1
"""How often a running tool looks at its cancel token: a Stop's cost before the CLI ends."""


def tool_container_left_line(name: str, reason: str, *, stopped: bool = True) -> str:
    """The log line for a tool whose container could not be removed (T303).

    `stopped` False for a tool that failed on its own: nobody pressed Stop (cold review).
    """
    after = " after Stop" if stopped else ""
    return (
        f"The extraction tool's container {name} could not be removed{after} ({reason}), "
        f"so it may still be writing into the server's data folder. "
        f"{container_end.remove_it()}\ndocker rm -f {name}"
    )


class _AfterCancel:
    """A cancelled run's `on_cancel`, run once, by whichever of its two paths sees the cancel
    first: the watcher, once it has claimed the CLI, or the read, when a line arrives (T539)."""

    def __init__(self, on_cancel: Callable[[], object] | None) -> None:
        self._on_cancel = on_cancel
        self._lock = threading.Lock()
        self._done = on_cancel is None

    def once(self) -> None:
        with self._lock:
            if self._done:
                return
            self._done = True
        self._call()

    def last(self) -> None:
        """After the run's CLI is gone: what it created after `once()` is ended too."""
        if self._on_cancel is not None:
            self._call()

    def _call(self) -> None:
        try:
            self._on_cancel()  # type: ignore[misc]
        except Exception as exc:  # noqa: BLE001 - the Stop goes on regardless
            logger.warning(f"ending what a cancelled run left failed: {exc!r}")


@contextmanager
def _ended_on_cancel(
    cancel: threading.Event | None,
    stream: Generator[str, None, None],
    ended: threading.Event,
    after_cancel: _AfterCancel | None = None,
) -> Iterator[None]:
    """While inside, a set `cancel` ends the docker CLI of `stream`, and nothing else (T303, T526).

    `run_attached()` reads the token only when a line arrives, and a tool can be
    silent for minutes (`vmap4assembler`), as can a build step, so a watcher ends
    the CLI through `runner.end_stream()` -- the panel's own Stop, for this one
    stream -- and the read returns. One stream and not the thread's (Codex's
    adversarial review): a thread can hold an outer job's stream while it runs
    this one, and a run's cancel is about the run. It claims the CLI once (T541),
    and polls until it has: a claim made while the lazy stream is still starting
    its CLI is held for it and ended as it starts (T546). `after_cancel` runs once
    the claimed CLI exists, never before what it makes can exist.
    Ending the CLI does not end the container on Docker Desktop;
    `run_container()` does that next, by its name. `ended` is set once it has
    ended the CLI, so `run_attached()` reads the run as the Stop even when the
    CLI then exits 0 (cold review of T526).
    """
    if cancel is None:
        yield
        return
    done = threading.Event()

    def watch() -> None:
        while not done.is_set():
            if cancel.wait(_STOP_POLL_SECONDS) and not done.is_set():
                # Claimed first, so the run reads as the Stop however its CLI exits.
                if runner.end_stream(stream):
                    ended.set()
                # Only once the CLI has been claimed AND started: before it starts, what
                # it is about to make does not exist yet (Codex review; a held claim, T546).
                if after_cancel is not None and ended.is_set() and runner.stream_started(stream):
                    after_cancel.once()
                done.wait(_STOP_POLL_SECONDS)

    watcher = threading.Thread(target=watch, name="yulon-tool-stop", daemon=True)
    watcher.start()
    try:
        yield
    finally:
        done.set()
        watcher.join()


def run_detached(spec: ContainerRun, name: str, *, timeout: float | None = None) -> str:
    """Start `spec` in the background as the container `name`; its id (T179's mmaps job).

    `ContainerRun.to_detached_argv()`: `-d --name`, and no `--rm`, so its exit code
    and its log are still there to read after it ends, whoever reads them.

    Local daemon only, for `run_container()`'s reason: the mounts are paths on this
    machine (`_DAEMON_AGNOSTIC` in the completeness test says so).

    Raises:
        DockerCliMissingError: there is no docker CLI.
        DockerCommandError: docker refused (an image it does not have, a name in use).
    """
    argv = spec.to_detached_argv(name)
    logger.info(f"run_detached(): `docker {' '.join(argv)}`")
    return _run(argv, timeout=timeout).stdout.strip()


@dataclass(frozen=True)
class ContainerExit:
    """A background container's state, and how it ended once it has (T179's mmaps job).

    `status` is `""` when Docker could not be asked or did not answer, and
    `missing` is True only when it ANSWERED that there is no such container --
    the two are kept apart for `ContainerState.missing`'s reason: a job whose
    container is gone has failed, and one Docker could not be asked about has not.
    """

    status: str = ""
    exit_code: int | None = None
    finished_at: str = ""
    container_id: str = ""
    missing: bool = False


def container_exit(
    container: str, *, timeout: float | None = None, wsl_distro: str | None = None
) -> ContainerExit:
    """`ContainerExit` in one `docker inspect`; never raises. A timeout reads as unanswered."""
    fmt = "{{.Id}}\t{{.State.Status}}\t{{.State.ExitCode}}\t{{.State.FinishedAt}}"
    proc = _docker(["inspect", container, "--format", fmt], timeout=timeout, wsl_distro=wsl_distro)
    if proc.returncode != 0:
        _note_unread(container, wsl_distro, proc.stderr.strip())
        missing = not _cli_missing(proc) and bool(_NO_SUCH_CONTAINER.search(proc.stderr))
        return ContainerExit(missing=missing)
    _note_read(container, wsl_distro)
    fields = [part.strip() for part in proc.stdout.strip().split("\t")]
    cid, status, code, finished = (fields + ["", "", "", ""])[:4]
    exit_code = int(code) if code.lstrip("-").isdigit() else None
    return ContainerExit(status, exit_code, finished, cid)


def daemon_cpus(*, timeout: float | None = None, wsl_distro: str | None = None) -> int | None:
    """How many CPUs the Docker DAEMON has (`docker info --format {{.NCPU}}`); None if unknown.

    The daemon's, not this process's host: on Docker Desktop the containers run in
    a VM given a share of the machine, and a job sized from `os.cpu_count()` would
    ask for cores that VM does not have (T179's movement-map threads).
    """
    proc = _docker(["info", "--format", "{{.NCPU}}"], timeout=timeout, wsl_distro=wsl_distro)
    text = proc.stdout.strip()
    if proc.returncode != 0 or not text.isdigit() or int(text) < 1:
        logger.warning(f"could not read the daemon's CPU count: {proc.stderr.strip()}")
        return None
    return int(text)


def remove_container(
    container: str, *, timeout: float | None = None, wsl_distro: str | None = None
) -> None:
    """`docker rm -f <container>`; a container that is already gone is not a failure.

    Raises:
        DockerCliMissingError: there is no docker CLI.
        DockerCommandError: docker refused for any other reason, or did not answer in time.
    """
    proc = _docker(["rm", "-f", container], timeout=timeout, wsl_distro=wsl_distro)
    if proc.returncode == 0:
        return
    if _cli_missing(proc):
        raise DockerCliMissingError(platform.DOCKER_CLI_MISSING_HELP)
    if _NO_SUCH_CONTAINER.search(proc.stderr):
        return
    raise DockerCommandError(f"docker rm -f {container} exited {proc.returncode}: {proc.stderr}")


_MISSING_IN_IMAGE = re.compile(r"No such container:path|Could not find the file", re.IGNORECASE)
"""How `docker cp` says the source path was not in there, across CLI generations.

Both spellings, because they are the same fact told by two different halves of
docker - the CLI when it stats the path itself, the daemon when it is the one
asked - and which of them answers is not something this code can predict.
Measured on Engine 29.6.2, a missing source in a real image comes back as
`Could not find the file <path> in container <id>`; `No such container:path`
is the other half's wording. An earlier version of this comment attributed each
spelling to a CLI generation, newest-first, and had them the wrong way round.
Matching only one would send a whole class of "this image does not ship that
file" through as an unexplained copy failure, and the two are not worth keeping
apart: what differs is the wording, not the situation.
"""


def copy_from_image(image: str, src: str, dest: Path) -> None:
    """Copy `src` out of `image` into `dest` on the host, without running anything.

    `docker create` + `docker cp` + `docker rm`, which is how the conf stage
    gets `*.conf.dist` out of a built image on every platform: no shell in the
    image is needed, no bind mount has to be shareable with Docker Desktop, and
    the files arrive owned by this user rather than by root — a bind mount
    written from inside the container is what needed the `sudo chown` the
    scripts did.

    `--entrypoint true`, because `docker create` refuses an image that declares
    neither an ENTRYPOINT nor a CMD ("Error response from daemon: no command
    specified", measured against docker 29.6.2), and an image built around a
    server binary may well be one. Note that inheriting one counts: a Dockerfile
    that says nothing about either still gets its base image's, so only an
    image that clears them — or a `FROM scratch` — actually hits this. Nothing
    is ever started, so the command named here never runs.

    `docker cp`'s own rules apply to `dest`: a directory `src` copied to a
    `dest` that already exists lands INSIDE it, and to one that does not exist
    becomes it. The caller chooses which by whether it made `dest` first.

    **This addresses the local daemon and takes no `wsl_distro`**, which is why
    it is listed in the completeness test's `_DAEMON_AGNOSTIC`; the reason
    recorded there is that only an install reaches this, and an install is local
    by construction (`install_wiring.installer_for_app()` says so and passes no
    distro), while `dest` is a host path that `docker cp` resolves on whichever
    side of the WSL boundary the CLI runs.

    The created container is removed whether or not the copy worked. A failed
    `cp` on every resume would otherwise leave one more stopped container each
    time, and `docker ps -a` is not somewhere a user looks. `finally` rather
    than `except`, so a cancel — which arrives here as `KeyboardInterrupt`,
    since `_run()` has no cancel event to watch — takes the container with it
    too.

    Three different failures reach the caller and they stay three, because the
    stage above says a different sentence to each:

    * `DockerCliMissingError` — nothing was asked of Docker at all. Re-raised
      untouched: it carries `DOCKER_CLI_MISSING_HELP` and nothing else on
      purpose, and wrapping it in a sentence about the image would bury the one
      instruction the user can act on.
    * the image does not ship `src` — a catalog bug, which no amount of retrying
      fixes. Docker's own wording for it blames a *container* (`No such
      container:path: 0123456789ab:/opt/etc`), and read at face value that says
      the throwaway container vanished, which is a machine problem and reads as
      worth a retry. So the message leads with the image and the path.
    * anything else the copy hit — a full disk, a permission, a daemon that went
      away mid-stream. Docker's stderr is kept whole; only the lead changes.

    Raises:
        DockerCliMissingError: there was no docker CLI to run.
        DockerCommandError: the create failed, the create printed no id, or the
            copy failed. The removal's own failure is logged, not raised: the
            copy's error is the one that explains anything.
    """
    created = _run(["create", "--entrypoint", "true", image])
    # The id is the LAST stdout line: a first-time pull prints its progress on
    # stderr, but some CLI versions have put a line on stdout before the id.
    lines = [line.strip() for line in created.stdout.splitlines() if line.strip()]
    container = lines[-1] if lines else ""
    if not container:
        raise DockerCommandError(f"docker create {image} printed no container id")
    # Load-bearing past the debugging. The container is created anonymously, so
    # this id is the only thing that distinguishes it from any other container
    # made from the same image — including the ones belonging to whatever else
    # shares this daemon. The live leak gate reads it for exactly that reason;
    # it used to count the daemon's containers for the image instead, and went
    # red 4 runs in 6 when anything else on the box touched one. Move or rename
    # this line and that gate fails loudly rather than passing on an empty set.
    logger.debug(f"copy_from_image(): {image}:{src} -> {dest} via {container[:12]}")
    try:
        _run(["cp", f"{container}:{src}", str(dest)])
    except DockerCliMissingError:
        # Ahead of the DockerCommandError clause, which is its base class and
        # would otherwise swallow it into a sentence about the image.
        raise
    except DockerCommandError as exc:
        if _MISSING_IN_IMAGE.search(str(exc)):
            raise DockerCommandError(f"{image} has no {src} to copy out: {exc}") from exc
        raise DockerCommandError(f"could not copy {src} out of {image}: {exc}") from exc
    finally:
        removal = _docker(["rm", "-f", container])
        if removal.returncode != 0:
            if _cli_missing(removal):
                # `removal.stderr` here is the whole install-Docker help text,
                # and it has already reached the user through the error being
                # raised past this. What the log needs is the container that is
                # now genuinely stranded, by name.
                logger.warning(
                    f"no docker CLI left to remove {container[:12]} with; it is still "
                    f"there, and `docker rm -f {container[:12]}` clears it once docker is back"
                )
            else:
                logger.warning(
                    f"could not remove {container[:12]} after the copy: {removal.stderr.strip()}"
                )


_STDIN_CHUNK_BYTES = 1 << 20
"""How much of `source` is read per write. A dump is measured in gigabytes."""


def exec_stdin(
    container: str,
    argv: Sequence[str],
    source: BinaryIO,
    *,
    env: Mapping[str, str],
    wsl_distro: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """`docker exec -i <container> <argv…>` with `source` streamed to its stdin.

    The SQL transport (phase 7): each dump file is streamed from the host
    checkout into the database client inside the container, so no helper
    container, no shared compose network and no shell pipeline is involved.

    Every name in `env` is forwarded as a bare `-e NAME`. The value is placed
    in THIS process's environment for the child, and `docker exec` copies it
    from there into the container. That is the rule `apply.mysql_env()` and
    `maintenance.DockerMysql._exec()` already keep for `MYSQL_PWD`, and the
    reason is unchanged: an argv is world-readable (`ps`,
    `/proc/<pid>/cmdline`, Task Manager); an environment is not. Tests assert
    the value is in no argv element, that the `args` of the returned result
    carry none either, and that no line this function logs contains it.

    `wsl_distro` is the second half of that rule and not decoration. A
    container name means nothing to the daemon that does not hold it: asked of
    Docker Desktop, a server running inside a distro answers `No such
    container`. And a variable set here does NOT reach a process inside a
    distro unless `WSLENV` names it — measured, it arrives EMPTY, and mysql
    then reports an authentication failure against a perfectly healthy
    database. `platform.wsl_env()` is what carries it across, so the crossing
    is got right here rather than remembered at each call site. Both existing
    `docker exec -e MYSQL_PWD` call sites (`apply.DockerSql._argv()`,
    `maintenance.DockerMysql._exec()`) take a distro; this one is the third,
    and the completeness test in `tests/test_docker.py` is why it must.

    `source` is PUMPED through a pipe rather than handed to the child as its
    file descriptor, and that is not an optimisation. `subprocess` turns a file
    object into `stdin` by calling `fileno()`, and a `gzip.open()` handle
    answers with the descriptor of the COMPRESSED file underneath it — the
    client would receive gzip bytes, and the "decompressed by Python on the way
    in" this exists for would silently not happen. Copying from `source`
    ourselves is what makes any `BinaryIO` — a gzip stream, a `BytesIO`, a
    plain file — mean what it says. The copy runs on this thread while two
    readers drain the child's stdout and stderr, so a client that prints a lot
    cannot stall on a full pipe while we are still writing to it.

    `subprocess.run` is not used, for the reason `maintenance.DockerMysql`'s
    docstring records: `runner.run()` has no stdin, and `communicate()` closes
    the stdin it is handed, which is the pipe the pump is writing to.

    The two ends of the pump fail for opposite reasons and are NOT one
    `except OSError`:

    * writing raises `BrokenPipeError` (EINVAL on Windows) when the client has
      already exited — a syntax error early in a dump, a wrong password. Its
      exit status and stderr say why, so the broken pipe is not the error and
      replacing `ERROR 1045: Access denied` with `[Errno 32] Broken pipe`
      would name nothing the user can fix.
    * reading raises when the SOURCE is unreadable. Swallowed the same way, a
      corrupt or truncated dump feeds the client half a file — still valid
      SQL, so it exits 0 and every check downstream agrees the import worked.
      That one is raised, as `SourceUnreadableError`.

    Which is a matter of one `finally`, not of a longer `except`. The first
    version of this caught `OSError` on the read side because `gzip.BadGzipFile`
    is one — and a TRUNCATED `.sql.gz`, the failure a flaky download actually
    produces, raises `EOFError` instead, while mangled deflate bytes raise
    `zlib.error`. Neither is an `OSError`; both left this function before the
    child was waited for and before the readers were joined, leaking a live
    `docker exec` and two threads per failed import. Widening the clause to
    those two would have been wrong again at the third, because `source` is a
    `BinaryIO` and what it raises is the caller's choice, not this module's —
    a `KeyboardInterrupt` mid-pump is not even an `Exception`. So the child is
    reaped and the readers joined on EVERY way out, which needs to know
    nothing about types, and the read failure is normalised on its way past
    (see `SourceUnreadableError`) so a caller does not inherit the same
    open-ended set to catch.

    Returns:
        A text `CompletedProcess`. NOT raised on non-zero exit: `sqlplan.apply()`
        decides per phase whether a failing file stops the stage or is reported
        and passed over, and only it knows which. The three non-zero states a
        caller has to tell apart — the SQL was wrong, the container is not
        running, the daemon never answered — stay apart, because docker's own
        stderr is returned untouched.

    Raises:
        DockerCliMissingError: there is no docker CLI to run (nor `wsl.exe` for
            a distro), or the one resolved earlier has since been uninstalled
            (the `OSError` road). "Could not ask", never "it answered no".
        SourceUnreadableError: `source` could not be read — truncated, corrupt,
            or anything else it chose to raise, kept as `__cause__`.

    Guaranteed on the way out whatever was raised, this included and a
    `KeyboardInterrupt` too: the child's stdin is closed, the child has been
    waited for, and both reader threads have been joined. Nothing is left
    running behind a failure. This promise is about the exit, not about a type
    — the earlier wording named `OSError`, and the exceptions it did not name
    were exactly the ones that leaked.
    """
    prefix = platform.docker_prefix(wsl_distro)
    if prefix is None:
        raise DockerCliMissingError(platform.DOCKER_CLI_MISSING_HELP)
    forwarded: list[str] = []
    for name in env:
        forwarded += ["-e", name]
    command = [*prefix, "exec", "-i", *forwarded, container, *argv]
    # The NAMES of the forwarded variables, never their values: this is the one
    # function in the module that is handed a secret, and a log file is
    # something users attach to bug reports.
    logger.debug(f"exec_stdin(): {' '.join(command)}")
    child = platform.wsl_env(dict(env)) if wsl_distro is not None else {**os.environ, **env}
    return _pumped(command, source, child_env=child, label=container)


def compose_run_stdin(
    server_dir: Path,
    service: str,
    entrypoint: str,
    argv: Sequence[str],
    source: BinaryIO,
    *,
    wsl_distro: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """`docker compose run --rm --no-deps -T --entrypoint <e> <service> <argv…>`, fed `source`.

    The route into a named volume that the running server mounts read-only (T62).
    A compose service is addressed rather than a volume name or a container name,
    and each of those alternatives is a guess this one does not have to make:

    * the VOLUME is `<project>_client-data` on an install this engine wrote and
      `<project>_ac-client-data` on one the bash installer built
      (`tests/data/wotlk-compose-config*.json`), and the project is whatever
      compose calls it. Compose resolves the mount from the service definition,
      so the copy lands in THIS install's volume whichever it is.
    * a CONTAINER name is global to the daemon (`ac-client-data-init` belongs to
      whichever install ran last), and `run` makes a fresh one inside this
      project instead (`run_one_shot()` records `container_name` not stopping it).

    `source` goes in on stdin and never as a bind mount, which is what makes the
    same argv work on all three daemons: a Windows path, a
    `\\\\wsl.localhost\\...` path Docker Desktop refuses to mount, and a SELinux
    host all read a pipe the same way. Through `wsl.exe` the bytes cross exactly
    as `DockerSql.run_file()`'s do.

    `-T` because there is no terminal and the stream is binary; `--no-deps`
    because nothing else in the project may start for a file copy. The exit
    status is returned rather than raised, like `exec_stdin()`: the caller owns
    the sentence.

    Raises:
        DockerCliMissingError: no docker CLI here (nor `wsl.exe` for a distro).
        DockerCommandError: `server_dir` is gone, or a distro install's folder
            has no Linux spelling to run compose in.
        SourceUnreadableError: `source` could not be read.
    """
    inside: str | None = None
    if wsl_distro is not None:
        try:
            inside = platform.wsl_linux_path_in(server_dir, wsl_distro)
        except platform.WslDistroMismatch as exc:
            raise DockerCommandError(str(exc)) from exc
        if inside is None:
            # `_docker()` would run compose in the distro's home directory here,
            # which is some OTHER project or none. A copy into a volume must not
            # guess which install it is writing to.
            raise DockerRefusal(
                f"{server_dir} is not a path inside the {wsl_distro} distro, so there is no "
                "folder there to run this install's compose project in. Nothing was copied."
            )
    elif _cwd_is_missing(server_dir):
        raise DockerRefusal(
            f"The server folder {server_dir} no longer exists, so Docker was not asked."
        )
    prefix = platform.docker_prefix(wsl_distro, inside=inside)
    if prefix is None:
        raise DockerCliMissingError(platform.DOCKER_CLI_MISSING_HELP)
    command = [
        *prefix,
        "compose",
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "--entrypoint",
        entrypoint,
        service,
        *argv,
    ]
    logger.debug(f"compose_run_stdin(): {' '.join(command)} in {server_dir}")
    child = platform.wsl_env() if wsl_distro is not None else dict(os.environ)
    return _pumped(
        command,
        source,
        child_env=child,
        label=service,
        cwd=None if wsl_distro is not None else server_dir,
    )


def _pumped(
    command: list[str],
    source: BinaryIO,
    *,
    child_env: dict[str, str],
    label: str,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Start `command`, pump `source` into its stdin, and reap it on every way out.

    The body `exec_stdin()` documents, shared with `compose_run_stdin()` so the
    reaping guarantee is written once. `cwd` is passed to `Popen` only when
    there is one: a WSL route carries its directory in the argv (`wsl --cd`).
    """
    extra: dict[str, Any] = {} if cwd is None else {"cwd": cwd}
    try:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=runner.child_env(child_env),
            creationflags=runner.creationflags(),
            **extra,
        )
    except OSError as exc:
        logger.warning(f"{command[0]} could not be started: {exc}")
        raise DockerCliMissingError(platform.DOCKER_CLI_MISSING_HELP) from exc
    # All three are pipes because all three were asked for as pipes; the
    # asserts are type narrowing, not a check.
    assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
    captured: dict[str, bytes] = {}

    def _drain(name: str, pipe: IO[bytes]) -> None:
        captured[name] = pipe.read()

    readers = (
        threading.Thread(target=_drain, args=("stdout", proc.stdout), daemon=True),
        threading.Thread(target=_drain, args=("stderr", proc.stderr), daemon=True),
    )
    for reader in readers:
        reader.start()
    try:
        _pump(source, proc.stdin, label)
    finally:
        # Unconditional, and a `finally` rather than a list of clauses,
        # because the pump can end in a way this module does not get to
        # choose: `source` is the caller's object and a Ctrl+C is nobody's.
        # `_pump` has closed the child's stdin by now on every one of those
        # roads, so the client sees EOF and this wait is not the one that
        # hangs.
        returncode = proc.wait()
        for reader in readers:
            reader.join()
    return subprocess.CompletedProcess(
        list(command),
        returncode,
        captured.get("stdout", b"").decode("utf-8", errors="replace"),
        captured.get("stderr", b"").decode("utf-8", errors="replace"),
    )


def _pump(source: BinaryIO, sink: IO[bytes], container: str) -> None:
    """Copy `source` into `sink` and close it, telling the two failures apart.

    The `except` sits around the WRITE alone, which is the only side whose
    failures this function can name. Its own docstring used to promise the
    read side was an `OSError`; it is whatever `source` decides, so nothing is
    caught around the read but the wrap that gives it one type — see
    `SourceUnreadableError`. `BaseException` passes through untouched.

    `sink` is closed on every way out, including that one. Without the EOF a
    client that has read everything it was sent waits forever, and the
    `proc.wait()` that follows would wait with it.

    Raises:
        SourceUnreadableError: `source` could not be read, whatever it raised.
    """
    try:
        while True:
            try:
                chunk = source.read(_STDIN_CHUNK_BYTES)
            except Exception as exc:
                raise SourceUnreadableError(
                    f"the dump being streamed into {container} could not be read: {exc}"
                ) from exc
            if not chunk:
                break
            try:
                sink.write(chunk)
            except OSError as exc:
                logger.debug(f"stdin of docker exec {container} closed early: {exc}")
                break
    finally:
        try:
            sink.close()  # without EOF a client that read everything waits forever
        except OSError as exc:
            logger.debug(f"stdin of docker exec {container} would not close: {exc}")


def sql_query(
    container: str,
    client: str,
    password: str,
    schema: str | None,
    statement: str,
    *,
    wsl_distro: str | None = None,
) -> str:
    """One statement as root, batch mode, through `exec_stdin()`; the client's stdout.

    `--batch --skip-column-names` gives tab-separated rows and nothing else, so
    a caller counting rows or reading one value never parses a table border.
    Both `mysql` and `mariadb` accept the long spellings, which is the point of
    taking the client's name as data (`DbFacts.client`): this module does not
    know which one the image ships.

    The statement travels on stdin exactly as a dump file does, and the password
    in `MYSQL_PWD` through the environment - neither is ever in argv. The error
    raised carries the client's stderr, which names the user and the reason and
    never the password.

    Root, because the import streams as root and the probe reads the same
    schemas it wrote (phase 7 "one secret"); the app user in `DbFacts.user` is
    what the emulator connects as, not what the installer asks with.

    `wsl_distro` is forwarded, not defaulted away. A container living inside a
    distro is `No such container` to Docker Desktop, and this is a PROBE: its
    answer decides whether an install runs. Asked of the wrong daemon it reads
    as "nothing is imported" for a database that is fully populated, and the
    stage would re-import over a working server. `exec_stdin()` also carries
    the secret across the boundary (`WSLENV`), which is the other half of the
    same rule, so all this has to do is hand the distro over.

    The three answers a caller has to tell apart stay apart, and the types are
    how:

    * **no rows** - exit 0 with nothing on stdout, returned as `""`. That is a
      verdict, not a failure: the database was asked and said no.
    * **the query failed** - `DockerCommandError` carrying the client's own
      words (`ERROR 1064 ... syntax`, `ERROR 1045 ... Access denied`) or
      docker's (`container ... is not running`), untouched.
    * **the database could not be asked** - `DockerCliMissingError` from
      `exec_stdin()`, passed through. It is a subclass of the above, so a
      caller wanting them apart must catch it FIRST.

    Stdout is returned VERBATIM, and the trailing newline matters. Under
    `--skip-column-names` a single row holding the empty string prints one
    empty line and no rows print nothing; stripping the result would flatten
    both to `""` and a caller counting `splitlines()` would see zero rows where
    there is one. Trimming is the caller's decision because only the caller
    knows whether it asked for a value or for a count.

    Raises:
        DockerCommandError: the client exited non-zero - no such schema, access
            denied, a syntax error, or the container is not running.
        DockerCliMissingError: there was no docker CLI to ask with. Nothing
            reached a database, so this is never a verdict about one.
    """
    argv = [client, "-u", "root", "--batch", "--skip-column-names"]
    if schema is not None:
        argv.append(schema)
    with io.BytesIO(statement.encode("utf-8")) as source:
        proc = exec_stdin(
            container, argv, source, env={"MYSQL_PWD": password}, wsl_distro=wsl_distro
        )
    if proc.returncode != 0:
        # `stderr` is where both the client and the daemon put their reason, in
        # every failure measured against mariadb:11 (2026-09-01): the syntax
        # error, the unknown database, the access denial, `No such container`
        # and `container ... is not running`. The fallbacks are for the one
        # shape that says nothing there - a client killed by a signal exits 137
        # with BOTH pipes empty, and "exited 137: " trailing off into a colon
        # names nothing the reader can act on.
        said = proc.stderr.strip() or proc.stdout.strip() or "no output"
        raise DockerCommandError(f"{client} in {container} exited {proc.returncode}: {said}")
    return proc.stdout
