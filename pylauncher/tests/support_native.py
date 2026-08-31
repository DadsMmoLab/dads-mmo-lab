"""The machine double every native-engine test file drives (roadmap 7.1).

A plain module, not a conftest: `test_spine.py` and
`test_families_azerothcore.py` import what they use by name, so a reader of
either file can see where `Recorder` comes from.

**Every double here must be able to give the answers the real function gives,
including the ones that make the engine refuse.** Four blockers survived 677
green tests and a 41-mutation run on the first version of `test_native.py`,
and all four survived for the same reason: the doubles could not produce the
real answer. `container_project` returned `None` for a container that does not
exist, where the real one returns `UNREADABLE`; the import probe returned
`absent` with no database running, which the real probe cannot do; the clone
double made a bare `.git` directory, where a real clone of that repository also
lays down its own `docker-compose.yml`; and there was no case at all for the
port scan listing our own containers. So `Recorder` models a machine — what
containers exist on it, what git has, what the database can answer and when —
rather than answering each question the way the code under test would like.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path

from yulon import docker, git, platform, resources
from yulon.catalog import composegen, native, preflight
from yulon.catalog.catalog import load_catalog
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.catalog.installer import InstallOptions

ENTRY = load_catalog().get("wow-wotlk")
TBC = load_catalog().get("wow-tbc")

IMPORTED = docker.ImportState("imported", "every schema is full", complete=True)
ABSENT = docker.ImportState("absent", "no schemas at all")
PARTIAL = docker.ImportState("partial", "acore_world has 3 tables but no import record")
UNREADABLE = docker.ImportState("unreadable", "the database would not answer")
POPULATED_HALF = docker.ImportState("populated", "400 rows, but acore_world is empty")


UPSTREAM_COMPOSE = "services:\n  ac-database:\n    image: mysql:8.4\n"
"""Stand-in for the `docker-compose.yml` the emulator repository ships at its root.

Its exact content does not matter; that it is THERE after a clone does. The
server directory is the checkout, this repo's own `tests/fixture.md` calls that
file "the `docker-compose.yml` shipped in that repo", and the Linux installer's
whole mechanism (write only an override, then `compose up -d --build`) only
works because it is. A clone double that made only `.git` hid a blocker that
refused every install.
"""


@dataclass
class Recorder:
    """A whole machine's worth of doubles, and a record of what the engine did to it."""

    calls: list[str] = field(default_factory=list)
    clones: list[git.CloneSpec] = field(default_factory=list)
    remotes: dict[Path, str] = field(default_factory=dict)
    tracked: dict[Path, str] = field(default_factory=dict)
    """Files git has, and their committed content — what `git status` compares against."""

    git_answers: bool = True
    """False when git cannot be asked at all, which is `is_unmodified()`'s `None`."""

    images: bool | None = True
    build_result: docker.AttachedRun = docker.AttachedRun(0, ("built",))
    one_shot_result: docker.AttachedRun = docker.AttachedRun(0, ("ran",))
    probe_answers: list[docker.ImportState] = field(default_factory=lambda: [ABSENT, IMPORTED])
    reset_answer: tuple[str, ...] = ("acore_world",)
    containers: dict[str, str | None] = field(default_factory=dict)
    """Containers that EXIST on this machine, and the compose project owning each.

    `None` is a container carrying no compose label. A name that is not a key
    here does not exist — and `container_project()` answers `UNREADABLE` for
    those, because `docker inspect <missing>` exits 1. That is the answer the
    old `projects.get(name)` double could never give, and it refused every
    fresh install.
    """

    daemon_lists_containers: bool = True
    """False when `docker ps -a` fails, which the real `container_exists()` RAISES on."""

    db_started: bool = False
    db_start_error: str = ""
    db_healthy: bool = True
    ready: bool = True

    def probe(self) -> docker.ImportState:
        """What the databases read as — and `unreadable` until one is running.

        The real probe is `controller_wow_wotlk.repair.import_state()`, which
        asks `DockerMysql.databases()`, i.e. `docker exec ac-database mysql …`.
        With no database container that raises and the state is `unreadable`.
        `absent` is not an answer it can give, so this double cannot give it
        either until `start_db` has run.
        """
        self.calls.append("probe")
        if not self.db_started:
            return UNREADABLE
        return self.probe_answers.pop(0) if len(self.probe_answers) > 1 else self.probe_answers[0]

    def reset(self) -> tuple[str, ...]:
        self.calls.append("reset")
        return self.reset_answer

    def container_exists(self, name: str) -> bool:
        if not self.daemon_lists_containers:
            raise docker.DockerCommandError("docker ps -a exited 1: is the daemon running?")
        return name in self.containers

    def container_project(self, name: str) -> str | None:
        return self.containers[name] if name in self.containers else docker.UNREADABLE

    def file_unmodified(self, dest: Path, relative_path: str) -> bool | None:
        """`git status --porcelain -- <path>`: empty only for tracked and unchanged.

        Three answers, because the real command distinguishes three states and
        the engine treats them differently: untracked (`?? path`) and modified
        (` M path`) are both False, and a git that cannot be asked is None.
        """
        if not self.git_answers or not (dest / ".git").is_dir():
            return None
        path = dest / relative_path
        if path not in self.tracked:
            return False
        return path.is_file() and path.read_text(encoding="utf-8") == self.tracked[path]

    def start_db(self, spec: docker.ContainerSpec, server_dir: Path) -> None:
        self.calls.append("start-db")
        if self.db_start_error:
            raise docker.DockerCommandError(self.db_start_error)
        self.db_started = True

    def seams(self, **overrides: object) -> native.Seams:
        def clone(spec: git.CloneSpec) -> None:
            self.calls.append(f"clone:{spec.url}")
            self.clones.append(spec)
            (spec.dest / ".git").mkdir(parents=True, exist_ok=True)
            self.remotes[spec.dest] = spec.url
            if spec.url == ENTRY.emulator.sources[0].url:
                # What the real repository leaves behind, not just `.git`.
                path = spec.dest / composegen.BASE_FILE
                path.write_text(UPSTREAM_COMPOSE, encoding="utf-8")
                self.tracked[path] = UPSTREAM_COMPOSE

        def build(
            server_dir: Path, files: object, *, sink: object = None, cancel: object = None
        ) -> docker.AttachedRun:
            self.calls.append("build")
            if callable(sink):
                sink("compiling")
            return self.build_result

        def one_shot(
            service: str, server_dir: Path, *, sink: object = None, cancel: object = None
        ) -> docker.AttachedRun:
            self.calls.append(f"one-shot:{service}")
            if callable(sink):
                sink(f"{service} said something")
            return self.one_shot_result

        def verify(
            probe: object, service: str, server_dir: Path, run: object
        ) -> docker.ImportState:
            self.calls.append("verify")
            return IMPORTED

        seams = native.Seams(
            platform_id=lambda: "macos",
            docker_ready=lambda: True,
            ensure_docker=_never_provisions,
            gather=self.gather,
            clone=clone,
            remote_url=lambda dest: self.remotes.get(dest),
            file_unmodified=self.file_unmodified,
            images_built=lambda refs: self.images,
            build=build,
            one_shot=one_shot,
            verify_import=verify,
            container_exists=self.container_exists,
            container_project=self.container_project,
            start_db=self.start_db,
            start=self.start,
            wait_db_healthy=lambda spec: self.db_healthy,
            wait_ready=lambda spec, ready: self.ready,
            keep_awake=lambda: nullcontext(),
        )
        for key, value in overrides.items():
            setattr(seams, key, value)
        return seams

    def gather(self, entry: object, server_dir: Path, **_kwargs: object) -> preflight.Facts:
        self.calls.append("gather")
        return preflight.Facts(
            platform_id="macos",
            docker_ready=True,
            vm=platform.VmResources(16 * preflight.GIB, 4),
            data_root=Path("/var/lib/docker"),
            data_root_free=200 * preflight.GIB,
            server_dir_free=200 * preflight.GIB,
            same_volume=False,
            bind_mount=True,
        )

    def start(self, spec: docker.ContainerSpec, server_dir: Path) -> bool:
        self.calls.append("start")
        return True


def _never_provisions(**_kwargs: object) -> platform.ProvisionReport:
    raise AssertionError("the engine asked to provision Docker when Docker was already ready")


def engine(rec: Recorder, **overrides: object) -> AzerothCoreInstaller:
    return AzerothCoreInstaller(
        ENTRY,
        installers_root=resources.installers_dir(),
        import_probe=rec.probe,
        reset_unfinished=rec.reset,
        seams=rec.seams(**overrides),
    )


def install(rec: Recorder, server_dir: Path, **overrides: object) -> list[str]:
    return list(engine(rec, **overrides).run(InstallOptions(server_dir=server_dir)))
