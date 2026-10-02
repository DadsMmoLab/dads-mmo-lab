"""Movement maps in the background, after the server is up (T179 Task 4).

Owner decision 4 of the T179 spec: a TrinityCore server is installed and played
first, and its movement maps -- the pathfinding data `mmaps_generator` makes from
the extracted maps and vmaps, which takes hours (README.md:193-194, facts §3) --
are made afterwards, while it runs. The world server starts without them
(`mmap.enablePathFinding = 0`, the conf table's value) and is told to use them
only once a COMPLETE run has finished, at its next start (spec §1 step 10).

**The job.** One detached container per server folder, named
`<container prefix>mmaps-<install id>` (`container_name()`), running the entry's
`mmaps.argv` in the server image with `data/` mounted READ-ONLY and only
`data/mmaps` writable: the generator reads `dbc/`, `maps/` and `vmaps/` and
writes nothing else (PathGenerator.cpp:44-77, MapBuilder.cpp:649,963), so a
mistake in it cannot reach the map data the running server reads. No `--rm`
(`docker.ContainerRun.to_detached_argv()`): its exit code and its log are how a
finished run is told from a dead one, by this process or a later one.

**The record.** `.yulon-mmaps.json` in the server folder, written whole or not
at all: `state` (`queued` before the container is asked for, `running`, `done`,
`failed`), when it started and finished, the progress last read, the container's
name and id, why it failed, and when pathfinding was switched on. No record is
"not started".

**Reconciling.** Every `mmaps_status()` call reads the record and asks Docker
what the container is really doing (`_reconcile()`), so a status after the app
was closed and opened again is the same answer the first process would have
given. Docker answering that the container is gone while the record says it
runs is a failed run: its partial output is removed and it can be started
again. Docker not answering at all changes nothing.

**Pathfinding on only after a complete run** (Review Focus 3). A run is
complete when its container exited with one of the plan's `success_codes`
AND `data/mmaps` holds at least `min_files` files (`extract.counts()`, the rule
the CMaNGOS `mmaps` stage records on). Then, and only then, worldserver.conf
gets `mmap.enablePathFinding = 1` (`conf.set_keys()`, the conf stage's own
writer), once; a failed, stopped or short run leaves it as it was and removes
its partial output, because the generator SKIPS every tile it finds a file for
(MapBuilder.cpp:1133-1150) and a set left by a dead run would look finished.

**Never during a rebuild.** Rebuild, Update to latest, Return to the tested pin
and Uninstall stop a job first (`stop_for_route()`, `remove_for_uninstall()`),
through hooks in the install spine and in `purge`; the job is started again
once a rebuild's server is ready.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, cast

from yulon import docker, platform, rmtree
from yulon.catalog import composegen
from yulon.catalog.catalog import CatalogEntry, TrinityCoreData
from yulon.catalog.families import conf, extract
from yulon.catalog.installer import InstallerError
from yulon.log import get_logger

logger = get_logger(__name__)

RECORD_FILE = ".yulon-mmaps.json"
"""In the server folder: the background job's record (see the module docstring)."""

PATHFINDING_KEY = "mmap.enablePathFinding"
"""The world server's switch for the movement maps (worldserver.conf.dist:394, facts §4)."""

DATA_DIR = "data"
"""The server folder's `data/`, the world server's DataDir and the generator's working folder."""

MMAPS_DIR = extract.MMAPS_DIR
"""What the generator writes under `data/`, and the only folder this module ever removes."""

WORK_MOUNT = "/out"
"""Where `data/` is mounted read-only, and the generator's working directory."""

LOG_TAIL_LINES = 200
"""How much of the generator's log a status reads: bounded, the progress is in its last line."""

_PROGRESS = re.compile(r"^\s*(\d{1,3})% \[Map (\d+)\] Building tile", re.MULTILINE)
"""MapBuilder.cpp:547 at CENTURION faac5fc9:
`printf("%u%% [Map %03i] Building tile [%02u,%02u]\\n", currentPercentageDone(), ...)`;
the percentage is of every tile of every map (`m_totalTilesProcessed / m_totalTiles`)."""

JobState = Literal["queued", "running", "done", "failed"]
State = Literal["not-started", "queued", "running", "done", "failed"]

_LOCK = threading.RLock()
"""One record per server folder, read and rewritten by the UI's polls and by the routes'
hooks: serialised, so two of them never start two containers or lose each other's write."""


class MmapsError(InstallerError):
    """A refusal or failure of the background job, in the sentence a player reads."""


# -- the docker seam ----------------------------------------------------------------


class Runner(Protocol):
    """Everything the job asks Docker; faked by the tests, `DockerRunner` for real."""

    def run_detached(self, spec: docker.ContainerRun, name: str) -> str: ...

    def inspect(self, name: str) -> docker.ContainerExit: ...

    def log_tail(self, name: str, lines: int) -> str | None: ...

    def remove(self, name: str) -> None: ...

    def started_at(self, container: str) -> str: ...


class DockerRunner:
    """The local daemon, through `docker`'s own functions."""

    def run_detached(self, spec: docker.ContainerRun, name: str) -> str:
        return docker.run_detached(spec, name)

    def inspect(self, name: str) -> docker.ContainerExit:
        return docker.container_exit(name)

    def log_tail(self, name: str, lines: int) -> str | None:
        return docker.log_tail(name, lines)

    def remove(self, name: str) -> None:
        docker.remove_container(name)

    def started_at(self, container: str) -> str:
        return docker.started_at(container)


Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


# -- the record ---------------------------------------------------------------------


@dataclass(frozen=True)
class Record:
    """`.yulon-mmaps.json`, as read and as written."""

    state: JobState
    container: str
    container_id: str = ""
    started: str = ""
    finished: str = ""
    percent: int | None = None
    map: int | None = None
    error: str = ""
    pathfinding_on_at: str = ""
    """When `mmap.enablePathFinding = 1` was written; empty until it was."""


def read_record(server_dir: Path) -> Record | None:
    """The record; None when there is none. One that cannot be read is a failed job's.

    A record nobody can read must not be taken for "not started": its container
    may still be running. It is read as `failed` with no container named, so a
    status says so and a start (which first removes this folder's container by
    its derived name) begins clean.
    """
    path = server_dir / RECORD_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        return Record("failed", "", error=f"its record {path} could not be read ({exc}).")
    try:
        raw = json.loads(text)
        state = raw["state"]
        if state not in ("queued", "running", "done", "failed"):
            raise ValueError(state)
        return Record(
            state=state,
            container=str(raw.get("container", "")),
            container_id=str(raw.get("container_id", "")),
            started=str(raw.get("started", "")),
            finished=str(raw.get("finished", "")),
            percent=_int_or_none(raw.get("percent")),
            map=_int_or_none(raw.get("map")),
            error=str(raw.get("error", "")),
            pathfinding_on_at=str(raw.get("pathfinding_on_at", "")),
        )
    except (ValueError, KeyError, TypeError) as exc:
        return Record("failed", "", error=f"its record {path} is not one Yu'lon wrote ({exc}).")


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _write_record(server_dir: Path, record: Record) -> None:
    """The record, through a temporary name renamed into place: whole or not at all.

    Raises:
        MmapsError: it could not be written; the record on disk is the one before.
    """
    path = server_dir / RECORD_FILE
    staged = path.with_name(path.name + ".yulon-new")
    try:
        staged.write_text(json.dumps({"version": 1, **asdict(record)}, indent=2) + "\n", "utf-8")
        os.replace(staged, path)
    except OSError as exc:
        staged.unlink(missing_ok=True)
        raise MmapsError(
            f"{path} could not be written ({exc}); check that the server folder can be written."
        ) from exc


def _forget_record(server_dir: Path) -> None:
    (server_dir / RECORD_FILE).unlink(missing_ok=True)


# -- what a status says -------------------------------------------------------------


RUNS_WITHOUT_IT = "the server already runs without it"


@dataclass(frozen=True)
class MmapsStatus:
    """What the Server tab's line reads (T179 Task 5), reconciled with Docker."""

    state: State
    percent: int | None = None
    map: int | None = None
    started: str = ""
    finished: str = ""
    error: str = ""
    pathfinding_on: bool = False
    """worldserver.conf says `mmap.enablePathFinding = 1` right now."""
    restart_needed: bool = False
    """Finished and switched on, and the world server's current run began before that:
    it reads the switch at start, so it uses the maps from its next start."""
    docker_unanswered: bool = False
    """Docker could not be asked this time; the state is the record's last word."""

    @property
    def can_start(self) -> bool:
        """A start would begin a run: nothing is running and no complete set exists."""
        return self.state in ("not-started", "failed")

    @property
    def can_stop(self) -> bool:
        return self.state in ("queued", "running")

    def line(self) -> str:
        """The one sentence the Server tab shows (spec §1 step 10)."""
        if self.state == "not-started":
            return f"Pathfinding data has not been made yet; {RUNS_WITHOUT_IT}."
        if self.state == "queued":
            return f"Pathfinding data: starting — {RUNS_WITHOUT_IT}."
        if self.state == "running":
            done = f"{self.percent} %" if self.percent is not None else "being made (takes hours)"
            unasked = " (Docker did not answer just now)" if self.docker_unanswered else ""
            return f"Pathfinding data: {done}{unasked} — {RUNS_WITHOUT_IT}."
        if self.state == "failed":
            return f"Pathfinding data could not be made: {self.error} It can be started again."
        if self.error:
            return f"Pathfinding data is ready, but {self.error}"
        if not self.pathfinding_on:
            return (
                "Pathfinding data is ready; pathfinding is switched off in the world server's conf."
            )
        if self.restart_needed:
            return "Pathfinding data is ready. Restart the server to use it."
        return "Pathfinding data is ready and in use."


# -- the job's facts ----------------------------------------------------------------


def background_block(entry: CatalogEntry) -> TrinityCoreData | None:
    """The entry's TrinityCore block when its movement maps are a background job; else None.

    Only a `trinitycore` block can say so (`TrinityCoreMmaps.background`). A
    CMaNGOS entry generates them in its install's own `mmaps` stage, and an
    AzerothCore entry's image does: neither has a job here.
    """
    native = entry.install.native
    block = native.trinitycore if native is not None else None
    if block is None or not block.mmaps.background:
        return None
    return block


@dataclass(frozen=True)
class Job:
    """One server's job: where it reads and writes, what it runs, how it is named."""

    server_dir: Path
    block: TrinityCoreData
    container: str
    world_container: str

    @property
    def data_dir(self) -> Path:
        return self.server_dir / DATA_DIR

    @property
    def world_conf(self) -> Path:
        return self.server_dir / composegen.SERVER_CONF_DIR / self.block.conf.world_conf


def container_name(entry: CatalogEntry, install_id: str) -> str:
    """`<prefix>mmaps-<install id>`: the compose services' prefix, and one per server folder.

    The install id is in it because the compose containers are named per GAME
    (`centurion-worldserver`) while two folders may each hold one: a start in
    one must never find, and remove, the other's job by name.
    """
    return f"{composegen.container_prefix(entry)}mmaps-{install_id}"


def job_for(server_dir: Path, entry: CatalogEntry, install_id: str) -> Job:
    """The job for `server_dir`; refused for an entry with no background movement maps."""
    block = background_block(entry)
    if block is None:
        raise MmapsError(
            f"{entry.name} makes its movement maps during the install, not in the background."
        )
    return Job(
        server_dir=server_dir,
        block=block,
        container=container_name(entry, install_id),
        world_container=entry.containers.world,
    )


def _install_id(server_dir: Path, platform_id: Callable[[], str] | None) -> str:
    return composegen.install_id(server_dir, platform_id=platform_id or platform.detect)


def image_ref(
    entry: CatalogEntry, server_dir: Path, *, platform_id: Callable[[], str], install_id: str
) -> str:
    """The built server image the extractors ran in, which holds `mmaps_generator` too."""
    block = background_block(entry)
    native = entry.install.native
    if block is None or native is None:
        raise MmapsError(f"{entry.name} has no background movement maps to make.")
    refs = composegen.built_image_refs(
        entry, server_dir, platform_id=platform_id, install_id=install_id
    )
    wanted = f"{native.image_prefix}{block.extract.image}:"
    for ref in refs:
        if ref.startswith(wanted):
            return ref
    raise MmapsError(f"{block.extract.image} is not one of the images {entry.name} builds.")


# -- the public calls ---------------------------------------------------------------


def mmaps_status(
    server_dir: Path,
    entry: CatalogEntry,
    *,
    runner: Runner | None = None,
    clock: Clock | None = None,
    platform_id: Callable[[], str] | None = None,
    install_id: str | None = None,
) -> MmapsStatus:
    """The job's state for the Server tab, reconciled with Docker; never raises for Docker.

    Asks Docker (an inspect, a bounded log tail, the world server's start time),
    so a caller on the GUI thread should poll it from a worker.

    Raises:
        MmapsError: the entry has no background movement maps, or the record
            could not be written.
    """
    job = job_for(server_dir, entry, install_id or _install_id(server_dir, platform_id))
    with _LOCK:
        return _reconcile(job, runner or DockerRunner(), clock or _utc_now)


def start_mmaps(
    server_dir: Path,
    entry: CatalogEntry,
    *,
    runner: Runner | None = None,
    clock: Clock | None = None,
    platform_id: Callable[[], str] | None = None,
    install_id: str | None = None,
    user_args: Sequence[str] | None = None,
) -> str:
    """Start the job unless it is running or finished; the sentence that says what happened.

    Never a second job: the record is reconciled first, and a queued or running
    one is left alone. A failed or never-started one begins clean -- a container
    left under this folder's name is removed and `data/mmaps` is emptied first,
    because the generator skips every tile it finds a file for.

    Raises:
        MmapsError: the map data is not there yet, `data/` is not inside the
            server folder, or Docker refused (the record then says `failed`).
    """
    ask = platform_id or platform.detect
    ident = install_id or _install_id(server_dir, ask)
    job = job_for(server_dir, entry, ident)
    run = runner or DockerRunner()
    now = clock or _utc_now
    with _LOCK:
        status = _reconcile(job, run, now)
        if status.state in ("queued", "running"):
            return "Pathfinding data is already being made in the background."
        if status.state == "done":
            return "Pathfinding data is already made."
        data_dir = _checked_data_dir(job)
        missing = extract.missing_map_data(data_dir, job.block.required_maps)
        if missing:
            raise MmapsError(
                f"Pathfinding data is made from the server's map data, and that is not all "
                f"there ({'; '.join(missing)}). Nothing was started."
            )
        ref = image_ref(entry, server_dir, platform_id=ask, install_id=ident)
        args = (
            tuple(user_args)
            if user_args is not None
            else tuple(platform.container_user_args(platform_id=_platform_id(ask)))
        )
        _remove_container(run, job.container)
        _clear_output(job)
        started = _stamp(now())
        _write_record(server_dir, Record("queued", job.container, started=started))
        spec = docker.ContainerRun(
            image=ref,
            argv=tuple(job.block.mmaps.argv),
            mounts=(
                docker.Mount(data_dir, WORK_MOUNT, read_only=True),
                docker.Mount(data_dir / MMAPS_DIR, f"{WORK_MOUNT}/{MMAPS_DIR}"),
            ),
            workdir=WORK_MOUNT,
            user_args=args,
            security_args=extract.EXTRACT_HARDENING,
        )
        try:
            cid = run.run_detached(spec, job.container)
        except docker.DockerCommandError as exc:
            _write_record(
                server_dir,
                Record(
                    "failed",
                    job.container,
                    started=started,
                    finished=_stamp(now()),
                    error=f"Docker could not start it ({exc}).",
                ),
            )
            raise MmapsError(
                f"Pathfinding data could not be started: Docker refused ({exc}). The server "
                "runs without it."
            ) from exc
        _write_record(
            server_dir, Record("running", job.container, container_id=cid, started=started)
        )
        logger.info(f"started {job.container} ({cid}) in {server_dir}")
        return (
            "Making the pathfinding data in the background; this takes hours, and the "
            "server already runs without it. When it is finished, restart the server to use it."
        )


def stop_mmaps(
    server_dir: Path,
    entry: CatalogEntry,
    *,
    runner: Runner | None = None,
    platform_id: Callable[[], str] | None = None,
    install_id: str | None = None,
) -> str:
    """Stop a queued, running or failed job: its container and partial output gone, no record.

    A finished one is left exactly as it is: its maps are complete.

    Raises:
        MmapsError: Docker could not remove the container (the record is kept),
            or the partial output could not be removed.
    """
    job = job_for(server_dir, entry, install_id or _install_id(server_dir, platform_id))
    with _LOCK:
        run = runner or DockerRunner()
        state = _reconcile(job, run, _utc_now).state
        if state == "not-started":
            return "Pathfinding data is not being made; there was nothing to stop."
        if state == "done":
            return "Pathfinding data is already made; there was nothing to stop."
        _stop(job, run)
        return (
            "Stopped making the pathfinding data and removed what it had made so far. "
            "Pathfinding stays off until a run finishes."
        )


def stop_for_route(
    server_dir: Path,
    entry: CatalogEntry,
    route: str,
    *,
    runner: Runner | None = None,
    platform_id: Callable[[], str] | None = None,
    install_id: str | None = None,
) -> str | None:
    """Stop a queued or running job before `route` changes the server; None when none runs.

    The routes' hook (Rebuild, Update to latest, Return to the tested pin): asked
    after their refusals and before their first change. Reads the record only, so
    a server with no job asks Docker nothing. A stop that fails raises, and the
    route does not start: the job must never run while the server is rebuilt.
    """
    if background_block(entry) is None:
        return None
    with _LOCK:
        record = read_record(server_dir)
        if record is None or record.state not in ("queued", "running"):
            return None
        job = job_for(server_dir, entry, install_id or _install_id(server_dir, platform_id))
        run = runner or DockerRunner()
        # Reconciled first: a run that FINISHED since the last status is a complete
        # set, switched on here, and never thrown away by the stop below.
        if _reconcile(job, run, _utc_now).state not in ("queued", "running"):
            return None
        try:
            _stop(job, run)
        except MmapsError as exc:
            raise MmapsError(
                f"{exc} The pathfinding data is still being made, so {route} was not started: "
                "it must not run while the server is rebuilt. Nothing was changed."
            ) from exc
        return (
            f"Stopped making the pathfinding data before {route}; what it had made so far was "
            "removed and pathfinding stays off. It starts again from the beginning once the "
            "server has been rebuilt, or from the Server tab."
        )


def remove_for_uninstall(
    server_dir: Path,
    *,
    runner: Runner | None = None,
    platform_id: Callable[[], str] | None = None,
) -> None:
    """Uninstall's call: remove this folder's job container, if its record names one.

    Before the containers and images go: a job left running would hold the
    server image and write into a folder being deleted. Its output goes with the
    folder. The record's container is removed only when its name ends with THIS
    folder's install id, so a record edited by hand cannot remove another
    server's container.

    Raises:
        MmapsError: Docker could not remove it.
    """
    with _LOCK:
        record = read_record(server_dir)
        if record is None or not record.container:
            return
        suffix = f"mmaps-{_install_id(server_dir, platform_id)}"
        if not record.container.endswith(suffix):
            logger.warning(
                f"{server_dir / RECORD_FILE} names {record.container}, not this folder's job; "
                "left alone"
            )
            return
        try:
            (runner or DockerRunner()).remove(record.container)
        except docker.DockerCommandError as exc:
            raise MmapsError(
                f"The background job making this server's pathfinding data "
                f"({record.container}) could not be stopped ({exc}), so nothing was removed. "
                "Check that Docker is running, then press Uninstall again."
            ) from exc


# -- the moving parts ---------------------------------------------------------------


def _reconcile(job: Job, run: Runner, now: Clock) -> MmapsStatus:
    """The record brought up to what Docker says; the status that follows from it."""
    record = read_record(job.server_dir)
    if record is None:
        return MmapsStatus("not-started", pathfinding_on=_pathfinding_on(job))
    if record.state in ("queued", "running"):
        return _reconcile_live(job, record, run, now)
    if record.state == "done":
        return _done_status(job, record, run, now)
    return _status_of(record, pathfinding_on=_pathfinding_on(job))


def _reconcile_live(job: Job, record: Record, run: Runner, now: Clock) -> MmapsStatus:
    # Always the name THIS folder's job has (`container_name()`), never one read
    # off the record: a record edited by hand must not point a removal elsewhere.
    facts = run.inspect(job.container)
    if not facts.status and not facts.missing:
        return replace(_status_of(record), docker_unanswered=True)
    if facts.missing:
        why = (
            "its container is gone (Docker was restarted or it was removed) before it finished."
            if record.state == "running"
            else "it was interrupted while it was being started."
        )
        return _fail(job, record, why, run, now, remove=False)
    tail = run.log_tail(job.container, LOG_TAIL_LINES)
    percent, current = _progress(tail) if tail is not None else (None, None)
    if facts.status not in ("exited", "dead"):
        moved = replace(
            record,
            state="running",
            container_id=record.container_id or facts.container_id,
            percent=percent if percent is not None else record.percent,
            map=current if current is not None else record.map,
        )
        if moved != record:
            _write_record(job.server_dir, moved)
        return _status_of(moved)
    return _finished(job, record, facts, tail, run, now)


def _finished(
    job: Job,
    record: Record,
    facts: docker.ContainerExit,
    tail: str | None,
    run: Runner,
    now: Clock,
) -> MmapsStatus:
    """The container exited: complete, and pathfinding switched on, or failed and cleared."""
    plan = job.block.mmaps
    if facts.exit_code not in plan.success_codes:
        words = docker.last_words(tuple((tail or "").splitlines()[-20:]))
        return _fail(
            job, record, f"the generator stopped with exit {facts.exit_code}. {words}", run, now
        )
    have = extract.counts({MMAPS_DIR: plan.min_files}, job.data_dir)[MMAPS_DIR]
    if have < plan.min_files:
        return _fail(
            job,
            record,
            f"it ended with {have} files where at least {plan.min_files} were expected.",
            run,
            now,
        )
    done = replace(
        record, state="done", finished=_stamp(now()), percent=100, error="", map=record.map
    )
    _write_record(job.server_dir, done)
    try:
        run.remove(job.container)
    except docker.DockerCommandError as exc:
        logger.warning(f"could not remove the finished {job.container}: {exc}")
    logger.info(f"{job.container} finished: {have} files in {job.data_dir / MMAPS_DIR}")
    return _done_status(job, done, run, now)


def _done_status(job: Job, record: Record, run: Runner, now: Clock) -> MmapsStatus:
    """A complete set: switched on once (retried until it was), and whether a restart is due."""
    if not record.pathfinding_on_at:
        try:
            conf.set_keys(job.world_conf, {PATHFINDING_KEY: "1"})
        except InstallerError as exc:
            # Kept `done`: the maps ARE complete. The switch is tried again on the
            # next status, and the line says why it is not on yet.
            return replace(
                _status_of(record),
                error=f"Yu'lon could not switch it on in the world server's conf: {exc}",
            )
        record = replace(record, pathfinding_on_at=_stamp(now()))
        _write_record(job.server_dir, record)
    on = _pathfinding_on(job)
    started = _parse_stamp(run.started_at(job.world_container))
    switched = _parse_stamp(record.pathfinding_on_at)
    due = on and switched is not None and (started is None or started < switched)
    return replace(_status_of(record), pathfinding_on=on, restart_needed=due)


def _fail(
    job: Job, record: Record, why: str, run: Runner, now: Clock, *, remove: bool = True
) -> MmapsStatus:
    """Record a failed run: its container and partial output gone, pathfinding off."""
    if remove:
        try:
            run.remove(job.container)
        except docker.DockerCommandError as exc:
            logger.warning(f"could not remove the failed {job.container}: {exc}")
    try:
        _clear_output(job)
        cleared = ""
    except MmapsError as exc:
        cleared = f" {exc}"
    failed = replace(record, state="failed", finished=_stamp(now()), error=f"{why}{cleared}")
    _write_record(job.server_dir, failed)
    logger.warning(f"{job.container} failed: {failed.error}")
    return _status_of(failed, pathfinding_on=_pathfinding_on(job))


def _stop(job: Job, run: Runner) -> None:
    """Container removed, partial output removed, record gone: not started."""
    name = job.container
    try:
        run.remove(name)
    except docker.DockerCommandError as exc:
        raise MmapsError(
            f"The background job making the pathfinding data ({name}) could not be stopped "
            f"({exc})."
        ) from exc
    _clear_output(job)
    _forget_record(job.server_dir)
    logger.info(f"stopped {name} in {job.server_dir}")


def _remove_container(run: Runner, name: str) -> None:
    try:
        run.remove(name)
    except docker.DockerCommandError as exc:
        raise MmapsError(
            f"A container left by an earlier run ({name}) could not be removed ({exc}). "
            "Nothing was started."
        ) from exc


def _checked_data_dir(job: Job) -> Path:
    """`data/` itself, refused when it or `data/mmaps` is a link: this module deletes there."""
    data_dir = job.data_dir
    for path in (data_dir, data_dir / MMAPS_DIR):
        if path.is_symlink():
            raise MmapsError(
                f"{path} is a link to another folder, and Yu'lon removes what the pathfinding "
                "generator writes there, so it was left alone. Make it an ordinary folder."
            )
    try:
        inside = data_dir.resolve().parent == job.server_dir.resolve()
    except OSError:
        inside = False
    if not inside:
        raise MmapsError(f"{data_dir} is not inside the server folder {job.server_dir}.")
    return data_dir


def _clear_output(job: Job) -> None:
    """Pathfinding switched off, then `data/mmaps` emptied and present for the next bind.

    Off FIRST, whenever the maps are emptied, whatever switched it on: a record
    that was lost or edited may hide that an earlier complete run turned it on,
    and a world server told to path over an empty folder is worse than one told
    not to. So "pathfinding on" never outlives the set it was switched on for.
    """
    if _pathfinding_on(job):
        try:
            conf.set_keys(job.world_conf, {PATHFINDING_KEY: "0"})
        except InstallerError as exc:
            raise MmapsError(
                f"pathfinding could not be switched off in the world server's conf ({exc}), so the "
                "movement maps it would read were left as they are."
            ) from exc
    out = _checked_data_dir(job) / MMAPS_DIR
    try:
        if out.exists():
            rmtree.remove_tree(out)
        out.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise MmapsError(
            f"{out} could not be emptied ({exc}); the generator skips every tile it finds, so "
            "it would leave a set that looks finished."
        ) from exc


def _pathfinding_on(job: Job) -> bool:
    """Does worldserver.conf's last active `mmap.enablePathFinding` line say 1?"""
    try:
        text = job.world_conf.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    value = ""
    for line in text.splitlines():
        found = re.match(rf"^{re.escape(PATHFINDING_KEY)}\s*=\s*(\S+)", line)
        if found:
            value = found.group(1).strip('"')
    return value == "1"


def _progress(tail: str) -> tuple[int | None, int | None]:
    """The last `NN% [Map NNN] Building tile` line's percentage and map, or (None, None)."""
    found = list(_PROGRESS.finditer(tail.replace("\r", "\n")))
    if not found:
        return None, None
    last = found[-1]
    return min(int(last.group(1)), 100), int(last.group(2))


def _status_of(record: Record, *, pathfinding_on: bool = False) -> MmapsStatus:
    return MmapsStatus(
        state=record.state,
        percent=record.percent,
        map=record.map,
        started=record.started,
        finished=record.finished,
        error=record.error if record.state == "failed" else "",
        pathfinding_on=pathfinding_on,
    )


def _stamp(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_stamp(text: str) -> datetime | None:
    """An ISO time in UTC (ours, or Docker's with nanoseconds); None for empty or zero."""
    found = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?Z?$", text.strip())
    if not found or found.group(1).startswith("0001-"):
        return None
    fraction = (found.group(2) or "0")[:6].ljust(6, "0")
    return datetime.strptime(f"{found.group(1)}.{fraction}", "%Y-%m-%dT%H:%M:%S.%f").replace(
        tzinfo=UTC
    )


def _platform_id(ask: Callable[[], str]) -> Callable[[], platform.PlatformId]:
    """`ask` as the type `container_user_args()` declares (`CmangosInstaller._user_args()`'s cast).

    The function compares against `"linux"` and treats every other string as
    Docker Desktop, so a seam answering something else is safe.
    """
    return cast("Callable[[], platform.PlatformId]", ask)
