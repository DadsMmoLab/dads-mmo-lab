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
runs is a failed run, which can be started again. Docker not answering at all
changes nothing.

**Pathfinding on only after a complete run** (Review Focus 3). A run is
complete when its container exited with one of the plan's `success_codes`
AND `data/mmaps` holds at least `min_files` files (`extract.counts()`, the rule
the CMaNGOS `mmaps` stage records on). Then, and only then, worldserver.conf
gets `mmap.enablePathFinding = 1` (`conf.set_keys()`, the conf stage's own
writer), once; a failed, stopped or short run leaves it off.

**A run that stops part-way keeps its finished tiles (T209).** The generator
SKIPS every tile whose file has a header it can read (MapBuilder.cpp:1133-1152 at
faac5fc9) and never checks the length, while each tile is its own file written
header first (:977-988). So when a run fails, Docker loses its container, the
player presses Stop, or a Rebuild stops it, `_keep_finished()` removes only the
tiles that are not whole (the entry's `tile_header`: the magic, and a length of
header plus `size`) and the record says `resumable` with the count kept. The
next start continues from them -- but only while the map data is the one the run
began with (`evidence`, taken at its start: a hash of `data/.yulon-extract.json`
and of the size and modification time of every file in `dbc/`, `maps/`, `vmaps/`);
otherwise, and for a record that cannot be read or a folder of tiles with no
record, it empties `data/mmaps` first as before. A run that ends well is also
checked against that hash: map data changed while it ran makes it a failure
with every tile removed, never a set switched on. Update to latest, Return to the
tested pin (`stop_for_route(clear=True)`: the generator's code may change while
`MMAP_VERSION` does not) and Re-extract map data (`discard()`) still throw every
tile away. Every run uses the entry's `threads`, a retry after a crash included:
the owner's stopgap of one thread after a crash was dropped when one thread crashed
at 16-17 % live too (2026-10-05), so it only made the retry hours slower.

**A complete set from an older generator goes too (T244).** A `done` record is a complete set
and the routes leave it alone -- unless the entry's `mmaps.generation` is higher than the one
the record says it was made under (absent: 1). Centurion's generator crashed on every four-digit
map id and, before CENTURION 4948d1a9, looked a map's tiles up under the wrong names, so a set
made by it has the wrong tiles for maps 0, 1 and 30: Update to latest and Return to the tested
pin (`clear`) switch pathfinding off, remove the tiles and forget the record
(`_drop_outdated()`), and the next start makes the set again.

**Never during a rebuild.** Rebuild, Update to latest, Return to the tested pin
and Uninstall stop a job first (`stop_for_route()`, `remove_for_uninstall()`),
through hooks in the install spine and in `purge`; the job is started again
once a rebuild's server is ready.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol, cast

from yulon import docker, platform, rmtree, server_build_presses
from yulon.catalog import composegen, world_data
from yulon.catalog.catalog import CatalogEntry, ConfPatchTable, MmapTileHeader, TrinityCoreData
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

TILE_SUFFIX = ".mmtile"
"""`mmaps/MMMYYXX.mmtile`, one finished navmesh tile (MapBuilder.cpp:963 at faac5fc9); the
per-map `MMM.mmap` is rewritten whenever a map starts (:649-651) and is never checked."""

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


STATUS_TIMEOUT = 30.0
"""Seconds a READ may take (inspect, log tail, start time): a status poll's budget.

Every Docker call here runs under `_LOCK`, so a daemon that hangs would hold every
later poll -- and a rebuild's hook -- behind it for ever. A read that times out is
an unanswered one (`docker._docker()` turns a timeout into a non-zero result)."""

CHANGE_TIMEOUT = 120.0
"""Seconds a CHANGE may take (`run -d`, `rm -f`, the daemon's CPU count before a run).
One that times out raises, and a route that needed it does not start."""

SKEW = timedelta(seconds=60)
"""How far this host's clock (the switch's stamp) and the daemon's (the world server's
`StartedAt`, in Docker Desktop's VM) may disagree before a start reads as "before"."""


class Runner(Protocol):
    """Everything the job asks Docker; faked by the tests, `DockerRunner` for real.

    Every call carries its own timeout (`STATUS_TIMEOUT`, `CHANGE_TIMEOUT`).
    """

    def run_detached(self, spec: docker.ContainerRun, name: str, *, timeout: float) -> str: ...

    def inspect(self, name: str, *, timeout: float) -> docker.ContainerExit: ...

    def log_tail(self, name: str, lines: int, *, timeout: float) -> str | None: ...

    def remove(self, name: str, *, timeout: float) -> None: ...

    def started_at(self, container: str, *, timeout: float) -> str: ...

    def cpus(self, *, timeout: float) -> int | None: ...


class DockerRunner:
    """The daemon that runs the server, through `docker`'s own functions.

    `wsl_distro` (T179 final round): a server inside a WSL distro is asked about,
    stopped and removed through THAT distro's Docker, as every other seam of an
    engine built for it (`install_wiring.installer_for_app`, `Seams.in_wsl`) -- this
    host's daemon would answer that the job's container does not exist. Starting a
    job there is refused: `docker.run_detached` mounts paths on this machine, which
    the distro's daemon cannot see; the route that would start it says so as a
    warning and the server runs without the maps.
    """

    def __init__(self, wsl_distro: str | None = None) -> None:
        self.wsl_distro = wsl_distro

    def run_detached(self, spec: docker.ContainerRun, name: str, *, timeout: float) -> str:
        if self.wsl_distro is not None:
            raise docker.DockerRefusal(
                f"the pathfinding job cannot be started from here for a server inside the WSL "
                f"distro {self.wsl_distro}; open Yu'lon inside that distro and start it on the "
                "server's Server tab there"
            )
        return docker.run_detached(spec, name, timeout=timeout)

    def inspect(self, name: str, *, timeout: float) -> docker.ContainerExit:
        return docker.container_exit(name, timeout=timeout, wsl_distro=self.wsl_distro)

    def log_tail(self, name: str, lines: int, *, timeout: float) -> str | None:
        return docker.log_tail(name, lines, timeout=timeout, wsl_distro=self.wsl_distro)

    def remove(self, name: str, *, timeout: float) -> None:
        docker.remove_container(name, timeout=timeout, wsl_distro=self.wsl_distro)

    def started_at(self, container: str, *, timeout: float) -> str:
        return docker.container_state(
            container, timeout=timeout, wsl_distro=self.wsl_distro
        ).started_at

    def cpus(self, *, timeout: float) -> int | None:
        return docker.daemon_cpus(timeout=timeout, wsl_distro=self.wsl_distro)


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
    evidence: str = ""
    """T209: what the map data was when this run started (`_evidence()`); empty when it
    could not be told. A run is continued only while the hash is the same."""
    resumable: bool = False
    """T209: failed or stopped with `kept` finished tiles left for the next run."""
    kept: int = 0
    """T209: how many whole tiles it left (`_keep_finished()`), or a resume started from."""
    generation: int = 1
    """T244: the entry's `mmaps.generation` when the run began (absent in older records: 1)."""
    unreadable: bool = False
    """Read off a file that could not be read or parsed: says nothing about the container.
    Never written."""


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
        return Record(
            "failed", "", error=f"its record {path} could not be read ({exc}).", unreadable=True
        )
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
            evidence=str(raw.get("evidence", "")),
            resumable=raw.get("resumable") is True,
            kept=_int_or_none(raw.get("kept")) or 0,
            generation=_int_or_none(raw.get("generation")) or 1,
        )
    except (ValueError, KeyError, TypeError) as exc:
        return Record(
            "failed",
            "",
            error=f"its record {path} is not one Yu'lon wrote ({exc}).",
            unreadable=True,
        )


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
        fields = {key: value for key, value in asdict(record).items() if key != "unreadable"}
        staged.write_text(json.dumps({"version": 1, **fields}, indent=2) + "\n", "utf-8")
        os.replace(staged, path)
    except OSError as exc:
        staged.unlink(missing_ok=True)
        raise MmapsError(
            f"{path} could not be written ({exc}); check that the server folder can be written."
        ) from exc


def _forget_record(server_dir: Path) -> None:
    """The record removed: not started. Raises `MmapsError`, never a bare `OSError`.

    Every caller is a status, a stop or a route hook, and each of them already
    answers a `MmapsError` in words; a bare `OSError` escaped the status path as a
    crash of the reading thread (Task 4's review, carried to Task 6).
    """
    path = server_dir / RECORD_FILE
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise MmapsError(f"its record {path} could not be removed ({exc}).") from exc


# -- what a status says -------------------------------------------------------------


RUNS_WITHOUT_IT = "the server already runs without it"

START_PRESS = "Make the pathfinding data"
"""The Server tab's press that starts a run, spelled once (T245).

A run that ended says what this press does next -- continue from the kept tiles, or
begin again -- so the line names it, and the view labels its button with this."""


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
    kept: int = 0
    """T209: a failed or stopped run's finished tiles, which the next run continues from."""
    begins_again_because: str = ""
    """T245: why the next start throws the `kept` tiles away (`_why_it_begins_again()`);
    empty when it continues from them."""

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
        # T245: an ended run says how far it got and what the press offered beside it
        # does next, so the press's own sentence ("Making…") is never the last word.
        # "had reached", never "stopped at": the percentage is the generator's last
        # progress line when its log could be read at the end, and otherwise the last
        # one a poll saw (a container Docker lost, a Stop) -- reached either way.
        reached = f" (it had reached {self.percent} %)" if self.percent is not None else ""
        if self.state == "failed" and self.kept and self.begins_again_because:
            return (
                f"Pathfinding data stopped part-way{reached}: {self.error} Its {self.kept} "
                f"finished tiles cannot be continued from, because {self.begins_again_because}, "
                f"so \u201c{START_PRESS}\u201d starts it again from the beginning."
            )
        if self.state == "failed" and self.kept:
            return (
                f"Pathfinding data stopped part-way{reached}: {self.error} Its {self.kept} "
                f"finished tiles are kept, and \u201c{START_PRESS}\u201d continues from there."
            )
        if self.state == "failed":
            return (
                f"Pathfinding data could not be made{reached}: {self.error} "
                f"\u201c{START_PRESS}\u201d starts it again from the beginning."
            )
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


def overlay(table: ConfPatchTable, entry: CatalogEntry, server_dir: Path) -> ConfPatchTable:
    """The install's conf table with pathfinding ON while a finished set is there (T179).

    The table says `mmap.enablePathFinding = 0` -- right for a fresh install, whose
    maps are made afterwards -- and the switch to 1 is written ONCE, when a run
    completes (`_done_status`). Anything that writes the table again (the conf
    stage on a resume, Reset to default) would put the 0 back for good: the record
    already says it was switched on, so nothing switches it again. So while the
    record says `done`, the table carries 1, the way `bot_dashboard.overlay` lays
    the dashboard's keys over it. A set emptied since is caught by the next status
    (`_done_status` counts it again and turns the switch off with it).
    """
    block = background_block(entry)
    if block is None:
        return table
    record = read_record(server_dir)
    world = block.conf.world_conf
    if record is None or record.state != "done" or world not in table.files:
        return table
    patch = table.files[world]
    keys = {**patch.keys, PATHFINDING_KEY: "1"}
    files = {**table.files, world: patch.model_copy(update={"keys": keys})}
    return table.model_copy(update={"files": files})


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
    one is left alone. A container left under this folder's name is removed. A
    failed or stopped run that kept finished tiles is CONTINUED from them while
    the map data is the one it began with (T209, `_resume_or_clear()`); any other
    start empties `data/mmaps` first, because the generator skips every tile it
    finds a file for.

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
        before = read_record(server_dir)
        evidence = _evidence(job)
        # T219: on a Windows install the world server's copy of `data/mmaps` must stay
        # empty while this job writes it, should Docker restart the world meanwhile;
        # the fingerprint says `-` until the run is done. A failure is logged there.
        try:
            world_data.refresh(entry, server_dir)
        except world_data.FingerprintNotRecorded:
            pass  # logged there; the job is not a world start, and the world's start refuses
        argv = _filled_argv(job, run)
        _remove_container(run, job.container)
        kept = _resume_or_clear(job, before, evidence)
        started = _stamp(now())
        queued = Record(
            "queued",
            job.container,
            started=started,
            evidence=evidence,
            kept=kept,
            generation=job.block.mmaps.generation,
        )
        _write_record(server_dir, queued)
        spec = docker.ContainerRun(
            image=ref,
            argv=argv,
            mounts=(
                docker.Mount(data_dir, WORK_MOUNT, read_only=True),
                docker.Mount(data_dir / MMAPS_DIR, f"{WORK_MOUNT}/{MMAPS_DIR}"),
            ),
            workdir=WORK_MOUNT,
            user_args=args,
            security_args=extract.EXTRACT_HARDENING,
        )
        try:
            cid = run.run_detached(spec, job.container, timeout=CHANGE_TIMEOUT)
        except docker.DockerCommandError as exc:
            # A `run -d` that timed out may still have made, and started, the
            # container: removed here, so a `failed` record never sits beside a
            # live generator (fix round 2).
            try:
                run.remove(job.container, timeout=CHANGE_TIMEOUT)
            except docker.DockerCommandError as again:
                logger.warning(f"could not remove {job.container} after its start failed: {again}")
            _write_record(
                server_dir,
                replace(
                    queued,
                    state="failed",
                    finished=_stamp(now()),
                    error=f"Docker could not start it ({exc}).",
                    resumable=kept > 0,
                ),
            )
            raise MmapsError(
                f"Pathfinding data could not be started: Docker refused ({exc}). The server "
                "runs without it."
            ) from exc
        _write_record(server_dir, replace(queued, state="running", container_id=cid))
        logger.info(f"started {job.container} ({cid}) in {server_dir} ({kept} tiles kept)")
        if kept:
            return (
                f"Continuing the pathfinding data in the background from its {kept} finished "
                "tiles; the server already runs without it. When it is finished, restart the "
                "server to use it."
            )
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
    """Stop a queued, running or failed job: its container gone, its finished tiles kept.

    T209: the tiles a run finished stay for the next run (`_stop()` with a reason), and
    the record says so; a run that finished none leaves no record, as before. A
    finished one is left exactly as it is: its maps are complete.

    Raises:
        MmapsError: Docker could not remove the container (the record is kept),
            or a cut-off tile or the output could not be removed.
    """
    job = job_for(server_dir, entry, install_id or _install_id(server_dir, platform_id))
    with _LOCK:
        run = runner or DockerRunner()
        state = _reconcile(job, run, _utc_now).state
        if state == "not-started":
            return "Pathfinding data is not being made; there was nothing to stop."
        if state == "done":
            return "Pathfinding data is already made; there was nothing to stop."
        kept = _stop(job, run, keep="you stopped it.")
        stopped = read_record(server_dir) if kept else None
        why = _why_it_begins_again(job, stopped) if stopped is not None else ""
        if why:
            return (
                f"Stopped making the pathfinding data. Its {kept} finished tiles are kept, but "
                f"{why}, so the next run starts again from the beginning. Pathfinding stays off "
                "until a run finishes."
            )
        if kept:
            return (
                f"Stopped making the pathfinding data. Its {kept} finished tiles are kept, and "
                "the next run continues from there. Pathfinding stays off until a run finishes."
            )
        return (
            "Stopped making the pathfinding data and removed what it had made so far. "
            "Pathfinding stays off until a run finishes."
        )


def stop_for_route(
    server_dir: Path,
    entry: CatalogEntry,
    route: str,
    *,
    clear: bool,
    press: str = server_build_presses.REBUILD,
    runner: Runner | None = None,
    platform_id: Callable[[], str] | None = None,
    install_id: str | None = None,
    kept_note: str = "It continues from there once the server has been rebuilt, or from the "
    "Server tab.",
) -> str | None:
    """Stop a job that may run before `route` changes the server; None when none can.

    The routes' hook (Rebuild, Update to latest, Return to the tested pin, and the
    map data's re-extraction): asked after their refusals and before their first
    change. Reads the record first, so a server with no record asks Docker
    nothing, and one whose record is a readable `done` is left alone: its set is
    complete. A readable `failed` one has its DERIVED container removed again, in
    case a start that timed out made it after all (fix round 2) -- a container
    already gone is no failure. Anything else -- queued, running, or a record that
    cannot be read, which says nothing about the container -- is stopped by that
    derived name. A removal or stop that fails raises, ending in what to do and
    `press`, the entry to press again; the route does not start, because the job
    must never run while the server is rebuilt.

    `clear` (T209, the owner's word of 2026-10-04): False for a Rebuild, which
    keeps the server's code, so a stopped run keeps its finished tiles and the
    run started once the server is ready continues from them; True for Update to
    latest, Return to the tested pin and Re-extract map data, whose new code or
    map data the tiles were not made with -- then a failed run's kept tiles go too
    and its record is forgotten. Re-extract map data passes False since T241: it
    keeps the old map data until the new is in and puts it back unchanged when the
    extraction does not finish, so the tiles made from it are still good; it
    throws them away itself (`discard()`) once the new data is in. `kept_note` is
    what the sentence about kept tiles says happens to them next.
    """
    again = f"Check that Docker is running, then press \u201c{press}\u201d again."
    if background_block(entry) is None:
        return None
    with _LOCK:
        record = read_record(server_dir)
        if record is None:
            return None
        job = job_for(server_dir, entry, install_id or _install_id(server_dir, platform_id))
        if not record.unreadable and record.state == "done":
            return _drop_outdated(job, record, route) if clear else None
        run = runner or DockerRunner()
        if not record.unreadable and record.state == "failed":
            # Its container should be gone already; removed again by the derived
            # name in case a start that timed out made it after all. Fail closed.
            try:
                run.remove(job.container, timeout=CHANGE_TIMEOUT)
            except docker.DockerCommandError as exc:
                raise MmapsError(
                    f"A container left by a pathfinding run that failed ({job.container}) could "
                    f"not be removed ({exc}), so {route} was not started: it must not run while "
                    f"the server is rebuilt. Nothing was changed. {again}"
                ) from exc
            return _drop_kept(job, record, route) if clear else None
        # Reconciled first: a run that FINISHED since the last status is a complete
        # set, switched on here, and never thrown away by the stop below. An
        # unreadable record has nothing to reconcile.
        if not record.unreadable and _reconcile(job, run, _utc_now).state not in (
            "queued",
            "running",
        ):
            # It ended since the last poll. A failure there kept its finished
            # tiles (`_fail`), which a route with `clear` must still remove.
            after = read_record(server_dir)
            if clear and after is not None and not after.unreadable:
                if after.state == "failed":
                    return _drop_kept(job, after, route)
                if after.state == "done":
                    return _drop_outdated(job, after, route)
            return None
        try:
            kept = _stop(job, run, keep=None if clear else f"it was stopped for {route}.")
        except MmapsError as exc:
            raise MmapsError(
                f"{exc} The pathfinding data is still being made, so {route} was not started: "
                f"it must not run while the server is rebuilt. Nothing was changed. {again}"
            ) from exc
        if kept:
            return (
                f"Stopped making the pathfinding data before {route}; its {kept} finished tiles "
                f"are kept and pathfinding stays off. {kept_note}"
            )
        return (
            f"Stopped making the pathfinding data before {route}; what it had made so far was "
            "removed and pathfinding stays off. It starts again from the beginning once the "
            "server has been rebuilt, or from the Server tab."
        )


def _drop_kept(job: Job, record: Record, route: str) -> str | None:
    """A failed run's kept tiles removed and its record forgotten before `route` (`clear`).

    Forgotten even when it kept nothing: `route` begins a new set, and a failed
    record would read as that set's. The sentence only when there were tiles to remove.
    """
    _clear_output(job)
    _forget_record(job.server_dir)
    if not record.resumable:
        return None
    return (
        f"The {record.kept} pathfinding tiles kept from an earlier run were removed "
        f"before {route}, which can change how they are made. It starts again from the "
        "beginning once the server has been rebuilt, or from the Server tab."
    )


def _drop_outdated(job: Job, record: Record, route: str) -> str | None:
    """A complete set made under an older generator removed before `route` (T244); None if current.

    A `done` record is a complete set and every route leaves it alone -- unless the catalog has
    since raised the entry's `mmaps.generation`: the generator that made this set had a fault
    the pin on the way in no longer has (Centurion: tiles under the wrong map's name, and a
    crash on a four-digit map id). Then pathfinding is switched off first, the tiles go, and
    the record is forgotten, so the next start makes the set again.
    """
    if record.generation >= job.block.mmaps.generation:
        return None
    _clear_output(job)
    _forget_record(job.server_dir)
    return (
        f"The pathfinding data made earlier was removed before {route}: it was made by an older "
        "version of the tool, which got some of it wrong. Pathfinding stays off and the data is "
        "made again once the server has been rebuilt, or from the Server tab."
    )


def discard(
    server_dir: Path,
    entry: CatalogEntry,
    *,
    platform_id: Callable[[], str] | None = None,
    install_id: str | None = None,
    stop: Callable[[], bool] | None = None,
) -> None:
    """Throw the movement maps away because the map data they were made from is replaced.

    T179 Task 6's re-extraction, after `stop_for_route()` has stopped any job:
    `_clear_output()` -- pathfinding switched off FIRST, then `data/mmaps`
    emptied -- and the record forgotten, so the job reads as not started and the
    next start makes a whole new set. A complete set goes too: it describes maps
    that are about to change.

    `stop` (T549): asked per tile; when it says stop, the record is forgotten anyway and
    `rmtree.StoppedPartWay` raised, so a part-deleted set reads as not made, never done.

    Raises:
        MmapsError: the switch, the folder or the record could not be changed.
        rmtree.StoppedPartWay: `stop` ended the deletion between two tiles.
    """
    job = job_for(server_dir, entry, install_id or _install_id(server_dir, platform_id))
    with _LOCK:
        try:
            _clear_output(job, stop)
        except rmtree.StoppedPartWay:
            _forget_record(server_dir)
            raise
        _forget_record(server_dir)


def remove_for_uninstall(
    server_dir: Path,
    *,
    entry_of: Callable[[], CatalogEntry | None] | None = None,
    runner: Runner | None = None,
    platform_id: Callable[[], str] | None = None,
) -> None:
    """Uninstall's call: remove this folder's job container whenever a record says one existed.

    Before the containers and images go: a job left running would hold the
    server image and write into a folder being deleted. Its output goes with the
    folder. Nothing at all without a record (no Docker call, no catalog read).

    Which container: the record's, when it ends with THIS folder's install id;
    otherwise -- a record that cannot be read, or names something else -- the
    DERIVED name (`container_name()`, from `entry_of()`, the server's catalog
    entry), because such a record says nothing about whether the job still
    runs, and a name read off it must never reach another server's container.
    With no entry to derive from, nothing is removed and the log says so.

    Raises:
        MmapsError: Docker could not remove it.
    """
    with _LOCK:
        record = read_record(server_dir)
        if record is None:
            return
        ident = _install_id(server_dir, platform_id)
        name = record.container
        # An unreadable record has no name at all (`read_record()`), so it lands here too.
        if not name.endswith(f"mmaps-{ident}"):
            entry = entry_of() if entry_of is not None else None
            if entry is None or background_block(entry) is None:
                logger.warning(
                    f"{server_dir / RECORD_FILE} does not name this folder's job and its game's "
                    "entry is not known, so no movement-map container was removed"
                )
                return
            name = container_name(entry, ident)
        try:
            (runner or DockerRunner()).remove(name, timeout=CHANGE_TIMEOUT)
        except docker.DockerCommandError as exc:
            raise MmapsError(
                f"The background job making this server's pathfinding data "
                f"({name}) could not be stopped ({exc}), so nothing was removed. "
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
    return _failed_status(job, record)


def _reconcile_live(job: Job, record: Record, run: Runner, now: Clock) -> MmapsStatus:
    # Always the name THIS folder's job has (`container_name()`), never one read
    # off the record: a record edited by hand must not point a removal elsewhere.
    facts = run.inspect(job.container, timeout=STATUS_TIMEOUT)
    if not facts.status and not facts.missing:
        return replace(_status_of(record), docker_unanswered=True)
    if facts.missing:
        why = (
            "its container is gone (Docker was restarted or it was removed) before it finished."
            if record.state == "running"
            else "it was interrupted while it was being started."
        )
        return _fail(job, record, why, run, now, remove=False)
    tail = run.log_tail(job.container, LOG_TAIL_LINES, timeout=STATUS_TIMEOUT)
    percent, current = _progress(tail) if tail is not None else (None, None)
    # The log's last progress line, also for a run that ended since the last poll: a
    # failure then says how far it really got (T245), not where a poll last saw it.
    latest = replace(
        record,
        percent=percent if percent is not None else record.percent,
        map=current if current is not None else record.map,
    )
    if facts.status not in ("exited", "dead"):
        moved = replace(
            latest, state="running", container_id=record.container_id or facts.container_id
        )
        if moved != record:
            _write_record(job.server_dir, moved)
        return _status_of(moved)
    return _finished(job, latest, facts, tail, run, now)


def _quoted(tail: str) -> str:
    """The generator's last words for the pathfinding line, cut at whole words (T304).

    `docker.last_words()` keeps the end of the log, and two cuts can fall inside a
    word. A log that does not end in a newline ended in the middle of a line --
    the generator's output reaches the log a block at a time and a crash leaves
    the last block part-way (`Building t`, yulon-win11 2026-10-05) -- so that
    line's last word is dropped unless the cut fell on a space, and an ellipsis
    says the line went on. A quote kept from the end is started at its first
    whole word. A full stop ends it, so the sentence after it does not run on.
    """
    lines = tail.splitlines()[-20:]
    if not any(line.strip() for line in lines):
        return f"{docker.last_words(())}."  # nothing said is not a quote cut short
    cut = not tail.endswith(("\n", "\r"))
    if cut:
        last = lines[-1]
        whole = last.rstrip() if last[-1:].isspace() else last.rpartition(" ")[0].rstrip()
        if whole or any(line.strip() for line in lines[:-1]):
            lines[-1] = whole  # a log that is one cut word is quoted as it is
    words = docker.last_words(tuple(lines))
    if words.startswith("…") and not words[1:2].isspace():
        rest = words[1:].partition(" ")[2].lstrip(" /")
        words = f"…{rest}" if rest else words
    if cut and not words.endswith("…"):
        words = f"{words}…"
    return words if words.endswith((".", "!", "?")) else f"{words}."


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
        return _fail(
            job,
            record,
            f"the generator stopped with exit {facts.exit_code}. {_quoted(tail or '')}",
            run,
            now,
        )
    if record.evidence and _evidence(job) != record.evidence:
        # Codex adversarial review: tiles made before and after the change describe two
        # different map datas, which no later run can tell apart. All of them go.
        why = (
            "the map data changed while it was being made, so what it made was removed. "
            "It starts again from the beginning."
        )
        try:
            run.remove(job.container, timeout=CHANGE_TIMEOUT)
        except docker.DockerCommandError as exc:
            logger.warning(f"could not remove the finished {job.container}: {exc}")
        try:
            _clear_output(job)
        except MmapsError as exc:
            why = f"{why} {exc}"
        failed = replace(
            record, state="failed", finished=_stamp(now()), error=why, resumable=False, kept=0
        )
        _write_record(job.server_dir, failed)
        logger.warning(f"{job.container} failed: {why}")
        return _status_of(failed, pathfinding_on=_pathfinding_on(job))
    have = _set_size(job)
    if have < plan.min_files:
        return _fail(
            job,
            record,
            f"it ended with {have} files where at least {plan.min_files} were expected.",
            run,
            now,
        )
    done = replace(
        record,
        state="done",
        finished=_stamp(now()),
        percent=100,
        error="",
        map=record.map,
        resumable=False,
    )
    _write_record(job.server_dir, done)
    # T219: the fingerprint said `-` for mmaps while the run made it; now the next start
    # of a Windows world -- Yu'lon's or Docker's -- must copy the finished set.
    _refresh_world_data(job)
    try:
        run.remove(job.container, timeout=CHANGE_TIMEOUT)
    except docker.DockerCommandError as exc:
        logger.warning(f"could not remove the finished {job.container}: {exc}")
    logger.info(f"{job.container} finished: {have} files in {job.data_dir / MMAPS_DIR}")
    return _done_status(job, done, run, now)


def _refresh_world_data(job: Job) -> None:
    """T219's fingerprint from a status poll: never a refusal here, which starts nothing.

    A fingerprint that can be neither written nor removed is logged by `world_data`, and
    the world's next start from Yu'lon refuses on it.
    """
    try:
        world_data.refresh_for(job.block, job.server_dir)
    except world_data.FingerprintNotRecorded:
        pass


def _done_status(job: Job, record: Record, run: Runner, now: Clock) -> MmapsStatus:
    """A complete set: switched on once (retried until it was), and whether a restart is due.

    Counted again first: a set emptied since (a new extraction, Task 6, or a hand)
    is no set, so the switch goes back to 0 with it and the job is not started.
    """
    plan = job.block.mmaps
    if _set_size(job) < plan.min_files:
        # A poll never raises for this: a folder it cannot clear, or a switch it
        # cannot turn off, is a failed state with its reason (fix round 2).
        try:
            _clear_output(job)
            _forget_record(job.server_dir)
        except MmapsError as exc:
            return MmapsStatus(
                "failed",
                error=f"the finished pathfinding data is no longer all there, and {exc}",
                pathfinding_on=_pathfinding_on(job),
            )
        _refresh_world_data(job)  # T219: back to `-` with it
        logger.warning(f"{job.data_dir / MMAPS_DIR} no longer holds a whole set; not started")
        return MmapsStatus("not-started", pathfinding_on=_pathfinding_on(job))
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
    started = _parse_stamp(run.started_at(job.world_container, timeout=STATUS_TIMEOUT))
    switched = _parse_stamp(record.pathfinding_on_at)
    due = on and switched is not None and (started is None or started < switched - SKEW)
    return replace(_status_of(record), pathfinding_on=on, restart_needed=due)


def _fail(
    job: Job, record: Record, why: str, run: Runner, now: Clock, *, remove: bool = True
) -> MmapsStatus:
    """Record a failed run: its container gone, its finished tiles kept, pathfinding off.

    T209: `_keep_finished()` removes only the tiles that are not whole, and the
    record says how many are left for the next run to continue from.
    """
    if remove:
        try:
            run.remove(job.container, timeout=CHANGE_TIMEOUT)
        except docker.DockerCommandError as exc:
            logger.warning(f"could not remove the failed {job.container}: {exc}")
    try:
        kept = _keep_finished(job)
        cleared = ""
    except MmapsError as exc:
        kept, cleared = 0, f" {exc}"
    failed = replace(
        record,
        state="failed",
        finished=_stamp(now()),
        error=f"{why}{cleared}",
        resumable=kept > 0,
        kept=kept,
    )
    _write_record(job.server_dir, failed)
    logger.warning(f"{job.container} failed: {failed.error}")
    return _failed_status(job, failed)


def _stop(job: Job, run: Runner, *, keep: str | None) -> int:
    """Container removed, then the finished tiles kept (`keep`, the reason) or all removed.

    `keep` is what the record's error says (T209): the run's whole tiles stay and
    the record reads failed and resumable, so the next start continues from them.
    Kept only from a record this module can read (it holds the map data's hash);
    with `keep` None, no record, or no whole tile, `data/mmaps` is emptied and the
    record forgotten: not started. The number of tiles kept.
    """
    name = job.container
    try:
        run.remove(name, timeout=CHANGE_TIMEOUT)
    except docker.DockerCommandError as exc:
        raise MmapsError(
            f"The background job making the pathfinding data ({name}) could not be stopped "
            f"({exc})."
        ) from exc
    record = read_record(job.server_dir)
    kept = 0
    if keep is not None and record is not None and not record.unreadable:
        kept = _keep_finished(job)
    if kept and record is not None:
        stopped = replace(
            record,
            state="failed",
            finished=_stamp(_utc_now()),
            error=keep or "",
            resumable=True,
            kept=kept,
        )
        _write_record(job.server_dir, stopped)
        logger.info(f"stopped {name} in {job.server_dir}; {kept} finished tiles kept")
        return kept
    _clear_output(job)
    _forget_record(job.server_dir)
    logger.info(f"stopped {name} in {job.server_dir}")
    return 0


def _filled_argv(job: Job, run: Runner) -> tuple[str, ...]:
    """The plan's argv with `{{THREADS}}` filled (`TrinityCoreMmaps.threads`).

    `half` is half the DAEMON's CPUs, at least 1: on Docker Desktop that is the
    VM's share, not this host's `os.cpu_count()`. A daemon that does not say is 1.
    """
    plan = job.block.mmaps
    if isinstance(plan.threads, int):
        threads = plan.threads
    else:
        cpus = run.cpus(timeout=CHANGE_TIMEOUT)
        threads = max(1, (cpus or 1) // 2)
    try:
        return tuple(composegen.fill(arg, {"THREADS": str(threads)}) for arg in plan.argv)
    except composegen.ComposeGenError as exc:
        raise MmapsError(f"the generator's command could not be filled in: {exc}") from exc


def _remove_container(run: Runner, name: str) -> None:
    try:
        run.remove(name, timeout=CHANGE_TIMEOUT)
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


def _switch_off(job: Job) -> None:
    """`mmap.enablePathFinding = 0` before any tile is removed (see `_clear_output()`)."""
    if _pathfinding_on(job):
        try:
            conf.set_keys(job.world_conf, {PATHFINDING_KEY: "0"})
        except InstallerError as exc:
            raise MmapsError(
                f"pathfinding could not be switched off in the world server's conf ({exc}), so the "
                "movement maps it would read were left as they are."
            ) from exc


def _input_dirs(job: Job) -> tuple[str, ...]:
    """What the generator reads under `data/` (PathGenerator.cpp:44-77 at faac5fc9), and so
    what a kept tile was made from: the DBCs (the entry's `dbc_overlay_to`), maps and vmaps."""
    return (job.block.extract.dbc_overlay_to, extract.MAPS_DIR, extract.VMAPS_DIR)


def _evidence(job: Job) -> str:
    """What the map data is now: a hash of the extraction's evidence and every input file's facts.

    `data/.yulon-extract.json` (`extract.EVIDENCE_FILE`) is rewritten whenever the
    extraction makes the map data, and Re-extract map data moves it aside with the
    map data and puts it back, unchanged, when the extraction does not finish
    (T241); a run made from the old data then continues. That alone does not prove
    the files are the same (Codex adversarial review, T209): a
    map put back or changed by hand leaves it as it was. So the hash also covers
    each file under `_input_dirs()` -- its path, size and modification time, from one
    `os.scandir` walk (the directory listing carries them on Windows), never its
    bytes. Empty when the evidence file is missing or a folder cannot be read: a
    run whose map data cannot be told apart is never continued.
    """
    digest = hashlib.sha256()
    try:
        digest.update((job.data_dir / extract.EVIDENCE_FILE).read_bytes())
        for folder in _input_dirs(job):
            for line in sorted(_file_facts(job.data_dir / folder, folder)):
                digest.update(line.encode("utf-8", "surrogateescape"))
    except OSError:
        return ""
    return digest.hexdigest()


def _file_facts(folder: Path, prefix: str) -> list[str]:
    """`<path>\0<size>\0<mtime ns>\n` for every file under `folder`.

    A link raises `OSError`, so the hash is empty and nothing is continued: its own
    size and time say nothing about the file or folder it points at, which is what
    the generator reads (Codex adversarial review). An extraction makes no links.
    """
    facts: list[str] = []
    with os.scandir(folder) as entries:
        for entry in entries:
            name = f"{prefix}/{entry.name}"
            if entry.is_symlink():
                raise OSError(f"{entry.path} is a link")
            if entry.is_dir(follow_symlinks=False):
                facts.extend(_file_facts(Path(entry.path), name))
                continue
            info = entry.stat(follow_symlinks=False)
            facts.append(f"{name}\0{info.st_size}\0{info.st_mtime_ns}\n")
    return facts


def _resumable(job: Job, before: Record | None) -> bool:
    """A readable record of a failed run that kept tiles, for an entry that can tell a whole one."""
    return (
        before is not None
        and not before.unreadable
        and before.state == "failed"
        and before.resumable
        and job.block.mmaps.tile_header is not None
    )


def continues_from(
    server_dir: Path,
    entry: CatalogEntry,
    *,
    platform_id: Callable[[], str] | None = None,
    install_id: str | None = None,
) -> int:
    """How many finished tiles the next run would continue from; 0 when it would start over (T263).

    Asked of the record and the map data as they are now, by the same rule a
    start applies (`_resume_or_clear()`): a readable record of a failed run that
    kept tiles, made from the map data `data/` holds now (`_evidence()`), and of
    those tiles the ones that are whole now (`_whole_tiles()`). For a sentence
    that promises the continuation, so it is said only when a start would keep
    it. Never raises: a record or map data that cannot be read answers 0, which
    says nothing.
    """
    if background_block(entry) is None:
        return 0
    before = read_record(server_dir)
    if before is None:
        return 0
    job = job_for(server_dir, entry, install_id or _install_id(server_dir, platform_id))
    kept, begins_again_because = _continuation(job, before)
    return 0 if begins_again_because else kept


def _continuation(job: Job, record: Record, *, polled: bool = False) -> tuple[int, str]:
    """`(tiles, why)`: the whole tiles a start keeps now, and why it would not (T245, T263).

    The ONE rule the Server tab's line (`_failed_status()`) and a failed Re-extract's
    sentence (`continues_from()`) both read, so the two can never disagree: the
    record must be a readable, resumable one of a failed run on an entry that can
    tell a whole tile (`_resumable()`, as `_resume_or_clear()` demands), the count is
    of the tiles that are whole now (`_whole_tiles()`, not the record's `kept`, which
    a tile removed or cut off since makes stale), and `why` is
    `_why_it_begins_again()`'s map-data answer, empty when the start continues.
    `(0, "")` when nothing would be kept at all. `polled` (the Server tab's 5 s line)
    counts through `_whole_tiles_polled()`, which opens no tile while the folder is
    unchanged; Re-extract's one sentence counts fresh.
    """
    if not _resumable(job, record):
        return 0, ""
    tiles = _whole_tiles_polled(job) if polled else _whole_tiles(job)
    if not tiles:
        return 0, ""
    return tiles, _why_it_begins_again(job, record)


_TILE_COUNTS: dict[tuple[Path, str], tuple[tuple[tuple[str, int, int], ...], int]] = {}
"""`_whole_tiles_polled()`'s cache: per tiles folder and header, its fingerprint and count."""


def _tile_fingerprint(out: Path) -> tuple[tuple[str, int, int], ...] | None:
    """Every `.mmtile`'s name, size and modification time, from one `os.scandir`; no file opened.

    None when the folder cannot be listed or a tile cannot be stat'ed: no cache then.
    """
    try:
        with os.scandir(out) as entries:
            return tuple(
                sorted(
                    (entry.name, info.st_size, info.st_mtime_ns)
                    for entry in entries
                    if entry.name.endswith(TILE_SUFFIX)
                    for info in (entry.stat(follow_symlinks=False),)
                )
            )
    except OSError:
        return None


def _whole_tiles_polled(job: Job) -> int:
    """`_whole_tiles()` for the Server tab's poll: tiles opened only when the folder changed.

    A full Centurion set is thousands of tiles (about 2.7 GB), and the line is asked
    every 5 s while a failed run is shown. So the count is kept beside a fingerprint
    of the folder (`_tile_fingerprint()`: names, sizes and dates, from the listing
    alone), and the headers are read again only when that fingerprint differs. A
    tile rewritten to the same size within the same nanosecond would not be seen;
    the start (`_keep_finished()`) checks every header again regardless.
    """
    header = job.block.mmaps.tile_header
    out = job.data_dir / MMAPS_DIR
    if header is None or out.is_symlink():
        return 0
    fingerprint = _tile_fingerprint(out)
    if fingerprint is None:
        return _whole_tiles(job)
    key = (out, header.model_dump_json())  # a header the catalog changed is a recount
    cached = _TILE_COUNTS.get(key)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]
    try:
        tiles = [path for path in out.iterdir() if path.name.endswith(TILE_SUFFIX)]
    except OSError:
        return 0
    states = [_tile_state(path, header) for path in tiles]
    count = sum(1 for state in states if state is True)
    if None not in states:  # a tile that could not be opened is asked again next poll
        _TILE_COUNTS[key] = (fingerprint, count)
    else:
        _TILE_COUNTS.pop(key, None)
    return count


def _whole_tiles(job: Job) -> int:
    """How many `.mmtile` in `data/mmaps` are whole now (`_whole_tile()`); nothing removed.

    The count a start's `_keep_finished()` would keep, without its removals: the
    record's `kept` is the count at the failure, and a tile removed or cut off since
    is not one the next run continues from (Codex review of T263). 0 when the folder
    cannot be read or the entry cannot tell a whole tile.
    """
    header = job.block.mmaps.tile_header
    out = job.data_dir / MMAPS_DIR
    if header is None or out.is_symlink():
        return 0
    try:
        tiles = [path for path in out.iterdir() if path.name.endswith(TILE_SUFFIX)]
    except OSError:
        return 0
    return sum(1 for path in tiles if _whole_tile(path, header))


def _why_it_begins_again(job: Job, record: Record) -> str:
    """Why a start would throw away the tiles `record` kept, in words; "" when it continues.

    The rule `_resume_or_clear()` starts by: a run whose map data could not be told
    is never continued, nor one whose map data has changed since. Hashed at every
    reading, never cached: the line beside the press is a promise about that press
    (T245; a one-minute cache was refused by Codex's adversarial review, because a
    map file changed inside the minute left the promise false). One hash of ~18,000
    files took 55 ms on WSL (2026-10-05), once per poll and only while a failed run
    with kept tiles is shown.
    """
    if not record.evidence:
        return "Yu'lon could not tell which map data it was made from"
    if _evidence(job) == record.evidence:
        return ""
    return "the map data has changed since it began"


def _failed_status(job: Job, record: Record) -> MmapsStatus:
    """A failed record's status, saying whether the next start continues from its tiles."""
    status = _status_of(record, pathfinding_on=_pathfinding_on(job))
    if not status.kept:
        return status
    kept, why = _continuation(job, record, polled=True)  # Re-extract's rule too (T263)
    return replace(status, kept=kept, begins_again_because=why)


def _resume_or_clear(job: Job, before: Record | None, evidence: str) -> int:
    """Before a start: the whole tiles a resumable run kept, or `data/mmaps` emptied (0).

    Continued only from a readable record that says `resumable`, whose map data
    hash is not empty and is the one `data/` has now, and only when the entry
    says how to tell a whole tile (`tile_header`). The tiles are checked again
    here, because files can change between the failure and the start.
    """
    resumable = _resumable(job, before)
    if resumable and before is not None and (not before.evidence or before.evidence != evidence):
        logger.warning(
            f"the map data in {job.data_dir} changed since the pathfinding run that stopped "
            f"part-way began, so its {before.kept} finished tiles were removed and the run "
            "starts again from the beginning"
        )
        resumable = False
    if resumable:
        return _keep_finished(job)
    _clear_output(job)
    return 0


def _keep_finished(job: Job) -> int:
    """Pathfinding switched off, then every `.mmtile` that is not whole removed; the count kept.

    Whole is the entry's `tile_header` (`_whole_tile()`): the file starts with the
    magic and is the header plus the `size` it states long. Anything else under
    `*.mmtile` is a tile the generator was writing when it stopped, which it would
    otherwise skip as finished. `MMM.mmap` and every other file are left alone.
    With no `tile_header` the entry cannot tell, and the folder is emptied (0).
    """
    header = job.block.mmaps.tile_header
    if header is None:
        _clear_output(job)
        return 0
    _switch_off(job)
    data_dir = _checked_data_dir(job)
    out = data_dir / MMAPS_DIR
    if not out.is_dir():
        if data_dir.is_dir():
            _clear_output(job)
        return 0
    kept = 0
    try:
        tiles = [
            path
            for path in out.iterdir()
            if path.name.endswith(TILE_SUFFIX) and (path.is_symlink() or not path.is_dir())
        ]
    except OSError as exc:
        raise MmapsError(f"{out} could not be read ({exc}).") from exc
    for path in tiles:
        if _whole_tile(path, header):
            kept += 1
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise MmapsError(
                f"{path} was cut off when the generator stopped and could not be removed "
                f"({exc}); the generator would skip it as finished."
            ) from exc
    return kept


def _whole_tile(path: Path, header: MmapTileHeader) -> bool:
    """Does `path` start with the magic and run to exactly the header plus its `size`?"""
    return _tile_state(path, header) is True


def _tile_state(path: Path, header: MmapTileHeader) -> bool | None:
    """Is `path` whole (magic, and exactly the header plus its `size`); None: unreadable."""
    try:
        if not path.is_file() or path.is_symlink():
            return False
        length = path.stat().st_size
        with path.open("rb") as tile:
            head = tile.read(header.length)
    except OSError:
        return None  # could not be read: neither whole nor known cut off
    if len(head) < header.length:
        return False
    if int.from_bytes(head[:4], "little") != header.magic:
        return False
    size = int.from_bytes(head[header.size_offset : header.size_offset + 4], "little")
    return length == header.length + size


def _clear_output(job: Job, stop: Callable[[], bool] | None = None) -> None:
    """Pathfinding switched off, then `data/mmaps` emptied and present for the next bind.

    Off FIRST, whenever the maps are emptied, whatever switched it on: a record
    that was lost or edited may hide that an earlier complete run turned it on,
    and a world server told to path over an empty folder is worse than one told
    not to. So "pathfinding on" never outlives the set it was switched on for.
    """
    _switch_off(job)
    data_dir = _checked_data_dir(job)
    out = data_dir / MMAPS_DIR
    try:
        if out.exists():
            if stop is not None:
                rmtree.remove_tree_stoppably(out, stop)  # per tile (T549)
            else:
                rmtree.remove_tree(out)
        # Never `data/` itself: a server whose map data is gone gets no empty
        # folder in its place, only a start (which refuses without maps) needs it.
        if data_dir.is_dir():
            out.mkdir(exist_ok=True)
    except OSError as exc:
        raise MmapsError(
            f"{out} could not be emptied ({exc}); the generator skips every tile it finds, so "
            "it would leave a set that looks finished."
        ) from exc


_COUNTS: dict[Path, tuple[int, int]] = {}
"""`data/mmaps` -> (its modification time in ns, the files counted then).

A finished set is thousands of tiles, and each poll of a done job asks whether
they are still there; walking them every few seconds is real I/O on Windows. The
folder is flat, so adding or removing a tile moves its own mtime, and the count
is taken again only then."""


def _set_size(job: Job) -> int:
    """How many files `data/mmaps` holds, from `_COUNTS` while its mtime has not moved."""
    out = job.data_dir / MMAPS_DIR
    try:
        stamp = out.stat().st_mtime_ns
    except OSError:
        _COUNTS.pop(out, None)
        return 0
    cached = _COUNTS.get(out)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    count = extract.counts({MMAPS_DIR: 0}, job.data_dir)[MMAPS_DIR]
    _COUNTS[out] = (stamp, count)
    return count


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
        kept=record.kept if record.state == "failed" and record.resumable else 0,
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
