"""Preflight for the native install engine: refuse, don't warn (roadmap 6.2).

Two halves, deliberately separated. `gather()` collects facts about THIS
machine through seams (`platform.py` for the OS, `docker.py` for the daemon);
`evaluate()` is a pure function from those facts plus the entry's floors to a
typed report. The split is what makes every threshold testable without a
daemon, and it keeps the rule the layers already have: `platform.py` knows no
games, `catalog.json` holds the numbers, and neither knows the other's half.

**The tri-state discipline is the point.** Every check answers refuse, warn,
*unchecked* or pass, and `unchecked` is never rounded to either neighbour. A
stopped Docker Desktop prints well-formed zeroes, so a check that reads a
number without confirming the engine spoke refuses a perfectly good machine
with "0 GB of RAM" (`pyplan/rust-prior-art.md` §3). An unreadable measurement
is reported as "unchecked — that is not a pass".

**Every numeric floor is inherited, none is measured by this project.** They
come from the earlier Rust launcher's incidents, are carried as data in
`catalog.json` (`install.native`), and the first live gates exist to replace
them with measurements — see `catalog.NativeInstall`.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from yulon import docker, git, platform
from yulon.catalog import composegen
from yulon.catalog.catalog import CatalogEntry
from yulon.log import get_logger

logger = get_logger(__name__)

Verdict = Literal["pass", "warn", "unchecked", "refuse"]

GIB = 1024**3

PROBE_IMAGE = git.CONTAINER_GIT_IMAGE
"""What the bind-mount probe runs: the exact reference the clone stages pull.

Imported rather than spelled, because a tag and a digest are two different
image references. Writing `alpine/git` here made the probe pull a SECOND,
unpinned image and hand it a bind mount of the user's chosen directory, while
`git.ContainerGit` pulled the pinned one — two pulls, and the 30 s
`BIND_PROBE_TIMEOUT_SECONDS` budget had been reasoned about as covering the one
the clone needed anyway (review, 2026-08-23).
"""


@dataclass(frozen=True)
class Check:
    """One question, its answer, and what the user can do about it."""

    name: str
    verdict: Verdict
    detail: str
    remedy: str = ""

    def line(self) -> str:
        """One line for the log panel: the verdict, the question, the answer."""
        said = f"[{self.verdict}] {self.name}: {self.detail}"
        return f"{said} {self.remedy}".rstrip()


@dataclass(frozen=True)
class Facts:
    """What could be established about this machine. `None` means "not established".

    Every field is optional for the same reason: the absence of a fact is a
    fact of its own, and it must not arrive here as a zero.
    """

    platform_id: str
    docker_ready: bool
    vm: platform.VmResources | None = None
    data_root: Path | None = None
    data_root_free: int | None = None
    server_dir_free: int | None = None
    same_volume: bool | None = None
    dir_problem: str | None = None
    bind_mount: bool | None = None
    port_conflicts: tuple[str, ...] = ()
    ports_in_use: tuple[int, ...] = ()


@dataclass(frozen=True)
class Report:
    """Every check, and the shortcuts a caller needs to act on them."""

    checks: tuple[Check, ...] = field(default_factory=tuple)

    def refusals(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if check.verdict == "refuse")

    def warnings(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if check.verdict == "warn")

    def unchecked(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if check.verdict == "unchecked")

    def ok(self) -> bool:
        """True when nothing refused. Warnings and unchecked items do not block."""
        return not self.refusals()

    def message(self) -> str:
        """Why the install was refused, as the paragraph a user reads.

        Only the refusals: a dialog that also recites the warnings buries the
        one sentence that says what to change.
        """
        return "\n".join(
            f"{check.name}: {check.detail} {check.remedy}".rstrip() for check in self.refusals()
        )


def gather(
    entry: CatalogEntry,
    server_dir: Path,
    *,
    client_dir: Path | None = None,
    platform_id: Callable[[], str] = platform.detect,
    docker_ready: Callable[[], bool] = platform.docker_ready,
    vm_resources: Callable[[], platform.VmResources | None] = platform.vm_resources,
    data_root: Callable[[], Path | None] = platform.docker_desktop_data_root,
    disk_free: Callable[[Path], int | None] | None = None,
    dir_problem: Callable[[Path], str | None] = platform.server_dir_problem,
    bind_mount_ok: Callable[[Path], bool | None] | None = None,
    port_conflicts: Callable[[], list[str]] | None = None,
    probe_port: Callable[[str, int], platform.PortProbe] = platform.probe_tcp,
) -> Facts:
    """Ask the machine everything `evaluate()` needs, refusing to invent an answer.

    Nothing here decides anything. Facts that cannot be established come back
    `None`, and the daemon is asked exactly once: with no Docker there is no VM
    size, no data root and no bind-mount probe, and asking anyway would produce
    the fabricated zeroes this module exists to keep out.

    `platform_id` is threaded into every question that has a per-OS answer,
    including the volume comparison. Reading the real platform inside one of
    them is how this suite once went red on every Python 3.12+ Linux box while
    CI stayed green.

    `client_dir` is carried from 7.1 and unused until 7.3 adds the client
    checks (A9). The spine passes it on every call, so the seam that will do
    the checking is already wired when the checks arrive; accepting it now is
    what keeps that from being a second change to every caller.
    """
    del client_dir  # 7.3 reads it; named here so the keyword is part of the contract
    here = platform_id()
    ready = docker_ready()
    free = disk_free if disk_free is not None else _free_bytes
    facts_vm = vm_resources() if ready else None
    root = data_root() if ready else None
    root_free = free(root) if root is not None else None
    dir_free = free(server_dir)
    probe = bind_mount_ok if bind_mount_ok is not None else _default_bind_probe
    conflicts = (
        port_conflicts
        if port_conflicts is not None
        else _default_conflicts(entry, server_dir, platform_id)
    )
    listening: list[int] = []
    for port in (entry.ports.auth, entry.ports.world):
        # Only a completed connection counts. `probe_tcp()` reports a refusal,
        # a timeout and a permission error alike as `unknown`, and treating any
        # of those as "in use" is precisely the hard refusal of a server that
        # would have started that `rust-prior-art.md` §4 warns about.
        if probe_port("127.0.0.1", port).status == "open":
            listening.append(port)
    return Facts(
        platform_id=here,
        docker_ready=ready,
        vm=facts_vm,
        data_root=root,
        data_root_free=root_free,
        server_dir_free=dir_free,
        same_volume=_same_volume(root, server_dir, here),
        dir_problem=dir_problem(server_dir),
        bind_mount=probe(server_dir) if ready else None,
        port_conflicts=tuple(conflicts()) if ready else (),
        ports_in_use=tuple(listening),
    )


def _default_bind_probe(server_dir: Path) -> bool | None:
    return docker.bind_mount_ok(server_dir, PROBE_IMAGE)


def _default_conflicts(
    entry: CatalogEntry, server_dir: Path, platform_id: Callable[[], str]
) -> Callable[[], list[str]]:
    """Publishers of this entry's ports that are NOT this install's own containers.

    The unfiltered scan is a global one and refuses the install it belongs to:
    preflight re-runs on every resume, so once `up` had started the three
    containers — which carry `restart: unless-stopped` — the next attempt was
    refused and told to remove the containers of the install it was trying to
    finish (review, 2026-08-23). The compose project is the same ownership
    proof the install guard uses, so the two cannot disagree about whose
    containers these are.
    """
    spec = entry.container_spec()
    project = composegen.project_name(entry.id, server_dir, platform_id=platform_id)
    return lambda: docker.foreign_port_conflicts(spec, project)


def _free_bytes(path: Path) -> int | None:
    """Free space on the volume holding `path`, or None if it cannot be asked.

    Walks up to the first directory that exists, because the folder being
    installed into is routinely one the user has not created yet — asking about
    a path that is not there answers "unchecked" for a machine with 900 GB free.
    """
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError as exc:
        logger.info(f"could not measure the free space at {probe}: {exc}")
        return None


def _same_volume(data_root: Path | None, server_dir: Path, platform_id: str) -> bool | None:
    """Do the images and the checkout share one pool of free space? None = unknown.

    It matters because the floors then ADD rather than each being met on its
    own: both grow, at the same time, out of the same free space.

    `platform_id` is passed in rather than detected here so the Windows branch
    below is reachable from a test that injects a platform — the module's whole
    claim is that every threshold is testable without a daemon, a disk or a Mac.
    """
    if data_root is None:
        return None
    try:
        return _volume_of(data_root, platform_id) == _volume_of(server_dir, platform_id)
    except OSError as exc:
        logger.info(f"could not tell whether {data_root} and {server_dir} share a volume: {exc}")
        return None


def _volume_of(path: Path, platform_id: str) -> object:
    """A value equal for two paths on the same volume, on Windows and POSIX alike."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if platform_id == "windows":
        # `st_dev` is not a volume id on Windows before the file is opened;
        # the drive letter is what actually answers the question there.
        return str(probe.resolve().drive).lower()
    return probe.stat().st_dev


def evaluate(entry: CatalogEntry, server_dir: Path, facts: Facts) -> Report:
    """Turn machine facts plus this entry's floors into refusals, warnings and unknowns.

    Pure: every threshold in the table below is testable without a daemon, a
    disk or a Mac. The order is the order a user should read them in — the
    cheapest fix first, and the daemon check first of all because every number
    below it is fabricated without one.
    """
    native = entry.install.native
    if native is None:
        return Report(
            (
                Check(
                    "native install",
                    "refuse",
                    f"{entry.name} has no native install data in catalog.json",
                    "This is a bug in the app, not something to fix on this machine.",
                ),
            )
        )
    checks: list[Check] = [_docker_check(facts)]
    checks.append(_ram_check(facts, native.min_ram_gb, native.warn_ram_gb))
    checks.append(_cpu_check(facts))
    refuse_root, warn_root = native.min_data_root_gb, native.warn_data_root_gb
    refuse_dir, warn_dir = native.min_server_dir_gb, native.warn_server_dir_gb
    if facts.same_volume:
        # One pool, so each floor is not enough on its own — they add.
        refuse_root, warn_root = native.floors_gb(same_volume=True)
        refuse_dir, warn_dir = refuse_root, warn_root
    checks.append(
        _space_check("Docker's disk", facts.data_root_free, refuse_root, warn_root, facts)
    )
    checks.append(
        _space_check("the server folder", facts.server_dir_free, refuse_dir, warn_dir, facts)
    )
    checks.append(_folder_check(facts, server_dir))
    checks.append(_bind_check(facts, server_dir))
    checks.append(_port_check(facts))
    return Report(tuple(checks))


def _docker_check(facts: Facts) -> Check:
    if facts.docker_ready:
        return Check("Docker", "pass", "the daemon answered")
    return Check(
        "Docker",
        "refuse",
        "no Docker daemon answered, and it could not be started automatically",
        "Open Docker Desktop, wait for the whale icon to stop animating, then try again.",
    )


def _ram_check(facts: Facts, refuse_gb: float, warn_gb: float) -> Check:
    """RAM the container ENGINE reports — the VM's, not the host's.

    A hard refusal below the floor, and that was the open question this design
    closed: yes. Below it the OOM killer SIGKILLs a compiler and the symptom is
    "dies at the same low percentage every retry" with a bare `Killed`, which
    is three hours to learn. A false refusal costs one settings change.
    """
    if facts.vm is None:
        return Check(
            "memory",
            "unchecked",
            "Docker would not say how much memory its VM has — that is not a pass",
            f"Give Docker at least {warn_gb:.0f} GB in its settings before a long build.",
        )
    gigabytes = facts.vm.memory_bytes / GIB
    if gigabytes < refuse_gb:
        return Check(
            "memory",
            "refuse",
            f"Docker's VM has {gigabytes:.1f} GB, and the build needs {refuse_gb:.0f} GB",
            "Raise the memory limit in Docker Desktop's Resources settings and try again. "
            "Below this the compiler is killed part-way through, every time.",
        )
    if gigabytes < warn_gb:
        return Check(
            "memory",
            "warn",
            f"Docker's VM has {gigabytes:.1f} GB, which is enough but not comfortable",
            f"{warn_gb:.0f} GB makes the build markedly less likely to be killed.",
        )
    return Check("memory", "pass", f"Docker's VM has {gigabytes:.1f} GB")


def _cpu_check(facts: Facts) -> Check:
    """Warn when the CPU count outruns the memory, and name the number to set.

    Never a refusal: this is a machine that will build, slowly or not at all,
    depending on a setting the user can change. Upstream's Dockerfile hardcodes
    `-j $(nproc+1)` INSIDE the RUN, so no build argument can change the job
    count — the only lever is the CPU count Docker Desktop is given.
    """
    if facts.vm is None:
        return Check("CPU vs memory", "unchecked", "Docker would not say what its VM has")
    jobs = facts.vm.cpus + 1
    affordable = int(facts.vm.memory_bytes / GIB // 2)
    if jobs > affordable and affordable >= 1:
        return Check(
            "CPU vs memory",
            "warn",
            f"{facts.vm.cpus} CPUs means {jobs} parallel compilers at about 2 GB each, and "
            f"{facts.vm.memory_bytes / GIB:.1f} GB affords about {affordable}",
            f"Either raise the memory, or set Docker Desktop to {max(affordable - 1, 1)} CPUs "
            "— the job count comes from the CPU count and cannot be set any other way.",
        )
    return Check("CPU vs memory", "pass", f"{facts.vm.cpus} CPUs against {affordable} affordable")


def _space_check(
    what: str, free: int | None, refuse_gb: float, warn_gb: float, facts: Facts
) -> Check:
    # On macOS, "Docker's disk" is the HOST volume holding the sparse VM image,
    # and host free space is an upper bound on what the VM can still grow into —
    # the VM can fill at its own cap while the host has room to spare. So a low
    # reading is a safe refusal (the VM certainly has no more than the host
    # does), but an ample reading proves nothing about the cap and must never
    # become a pass. See `_space_check_macos_bounded()`.
    if what == "Docker's disk" and facts.platform_id == "macos":
        return _space_check_macos_bounded(free, refuse_gb, warn_gb, facts)
    if free is None:
        return Check(
            f"free space on {what}",
            "unchecked",
            f"the free space on {what} could not be measured — that is not a pass",
            f"Make sure there is at least {warn_gb:.0f} GB free before starting a long build.",
        )
    gigabytes = free / GIB
    if facts.same_volume:
        note = " (the server folder and Docker's disk share one drive, so both needs add up)"
    else:
        note = ""
    if gigabytes < refuse_gb:
        return Check(
            f"free space on {what}",
            "refuse",
            f"{gigabytes:.0f} GB free, and the install needs {refuse_gb:.0f} GB{note}",
            "Free some space, or install to a drive that has room, then try again.",
        )
    if gigabytes < warn_gb:
        return Check(
            f"free space on {what}",
            "warn",
            f"{gigabytes:.0f} GB free; {warn_gb:.0f} GB is the comfortable figure{note}",
        )
    return Check(f"free space on {what}", "pass", f"{gigabytes:.0f} GB free")


def _space_check_macos_bounded(
    free: int | None, refuse_gb: float, warn_gb: float, facts: Facts
) -> Check:
    """The macOS-host case of `_space_check`: refuse-when-low, but never a pass.

    What is measured here is free space on the volume holding Docker Desktop's
    sparse `Docker.raw`, not the room *inside* the Linux VM. The VM's own disk
    is capped near 64 GB by default and can fill up while the host has hundreds
    of gigabytes free, so an ample host reading is evidence of nothing. The
    tri-state discipline that governs this whole module forbids rounding that
    to a pass, and the alternative — inventing "the VM is full" from a host
    number — is the fabricated refusal it was written to prevent.

    So below the refuse floor is a real refusal, below the warn floor a real
    warning, and anything more comfortable is `unchecked`: the build may fit,
    and it may hit the VM's cap, and only a Mac gate measuring the actual
    virtual-disk state can say which.
    """
    if free is None:
        return Check(
            "free space on Docker's disk",
            "unchecked",
            "on macOS the free space of the volume holding Docker's disk image could not be "
            "measured — that is not a pass",
            f"Make sure the drive has at least {warn_gb:.0f} GB free, and that Docker's "
            "virtual disk is not near its size cap, before a long build.",
        )
    gigabytes = free / GIB
    if gigabytes < refuse_gb:
        return Check(
            "free space on Docker's disk",
            "refuse",
            f"{gigabytes:.0f} GB free on the drive, and the install needs {refuse_gb:.0f} GB",
            "Free some space, or install to a drive that has room, then try again.",
        )
    if gigabytes < warn_gb:
        return Check(
            "free space on Docker's disk",
            "warn",
            f"{gigabytes:.0f} GB free on the drive; {warn_gb:.0f} GB is the comfortable figure",
        )
    return Check(
        "free space on Docker's disk",
        "unchecked",
        f"{gigabytes:.0f} GB free on the HOST drive — plentiful, but Docker's Linux VM caps "
        "its own disk near 64 GB by default and can fill while the host has room to spare, "
        "so this is not a pass",
        "If the build runs out of space, raise Docker's virtual-disk size in Docker Desktop "
        "before restarting.",
    )


def _folder_check(facts: Facts, server_dir: Path) -> Check:
    if facts.dir_problem is None:
        return Check("the server folder", "pass", f"{server_dir} looks usable")
    return Check(
        "the server folder",
        "refuse",
        facts.dir_problem,
        "Pick a different folder and try again. Nothing was written.",
    )


def _bind_check(facts: Facts, server_dir: Path) -> Check:
    """Does a container actually see what the host sees in that folder?

    Docker Desktop mounts a folder outside its file-sharing list as an EMPTY
    directory instead of failing, so the clone "succeeds", the build context is
    empty and the first report of the problem is a compile error hours later.
    `_folder_check()` explains *why* a mount would fail; this reports whether it
    does — see `docker.bind_mount_ok()`, which compares the container's listing
    against the host's rather than trusting an exit code, since `ls` on an empty
    directory exits 0 and the chosen folder is empty at preflight time.
    """
    if facts.bind_mount is None:
        return Check(
            "sharing the folder with Docker",
            "unchecked",
            "the folder could not be tested inside a container — that is not a pass",
            "If the install fails immediately, check Docker Desktop's file sharing settings.",
        )
    if facts.bind_mount:
        return Check("sharing the folder with Docker", "pass", f"a container can read {server_dir}")
    return Check(
        "sharing the folder with Docker",
        "refuse",
        f"a container could not see {server_dir}, so the server files would be invisible to it",
        "Add this folder (or its parent) to Docker Desktop's Settings → Resources → File "
        "sharing, or pick a folder under your home directory, then try again.",
    )


def _port_check(facts: Facts) -> Check:
    """Refuse a port conflict BEFORE the build, not after it.

    Two sources, and only one of them refuses. A FOREIGN container already
    publishing the port is proof — `gather()` drops this install's own
    containers by compose project before the list gets here, or a resume would
    be refused because its own half-started server is still up. A raw socket
    probe is not proof: Hyper-V and WSL reserve ranges, and a permission error
    looks exactly like a listener — so the socket half only ever warns, because
    hard-refusing on it would refuse a server that would have started
    (`rust-prior-art.md` §4).
    """
    if facts.port_conflicts:
        return Check(
            "the server's ports",
            "refuse",
            f"{', '.join(facts.port_conflicts)} already publish the ports this server needs",
            "Stop that server first (or remove its containers), then try again.",
        )
    if facts.ports_in_use:
        ports = ", ".join(str(port) for port in facts.ports_in_use)
        return Check(
            "the server's ports",
            "warn",
            f"something on this machine is already listening on {ports}",
            "If the server cannot start, that is the first thing to look at.",
        )
    return Check("the server's ports", "pass", "nothing else is using them")


def lines(report: Report) -> Sequence[str]:
    """Every check as one line each, for the install log."""
    return [check.line() for check in report.checks]
