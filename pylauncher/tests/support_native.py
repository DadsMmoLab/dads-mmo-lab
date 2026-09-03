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

import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

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
    relabelled: list[Path] = field(default_factory=list)
    """Paths handed to `chcon -Rt container_file_t`, in order.

    A list and not a flag, because the negative is the interesting half: off
    SELinux, and on a filesystem that cannot hold a label, this must stay
    EMPTY. A relabel firing on Ubuntu is the same bug the shell lineage had,
    only pointing the other way.
    """

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
    reset_error: Exception | None = None
    """What `reset()` RAISES instead of answering, or None to answer.

    The seam behind it is `controller_wow_wotlk.repair.reset_unfinished()`,
    whose own `Raises:` names three: `MaintenanceError` (the schemas could not
    be listed, or one survived its `DROP`), `ApplyError` (the server refused a
    `DROP DATABASE`) and a bare `RuntimeError` (there is player data, so
    nothing was dropped). None of the three is an `InstallerError`, and a
    double that could only ever answer could not produce the refusal the
    engine has to translate — which is this module's own rule.
    """

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

    container_runs: list[docker.ContainerRun] = field(default_factory=list)
    """Every `docker run` the engine asked for, as the typed spec — asserted by field."""

    copied: list[tuple[str, str, Path]] = field(default_factory=list)
    sql_calls: list[str] = field(default_factory=list)
    """The first line of every stream fed to `exec_stdin`, plus every `sql_query` statement.

    A first line names a dump and is the wrong thing to reach for when the
    question is what a multi-line script said; `sql_scripts` below holds those
    whole.
    """

    distros: list[str | None] = field(default_factory=list)
    """The `wsl_distro` of every `exec_stdin`/`sql_query` call, in order — recorded, not dropped.

    `sqlplan.apply()` passes `wsl_distro=` on EVERY call, so a double without
    the keyword is a `TypeError` on the first statement rather than a wrong
    answer. Recording it is what makes the seam's daemon choice assertable:
    both Protocols (`sqlplan.ExecStdin`, `sqlplan.SqlQuery`) declare the
    keyword because a container name means nothing to a daemon that does not
    hold it, and a double that accepted and discarded it would let a call go to
    the wrong daemon with nothing in a test to show it.
    """

    sql_scripts: list[str] = field(default_factory=list)
    """The WHOLE text of every script fed to `exec_stdin`, in order.

    `sql_calls` keeps the FIRST line of each, which names a dump by its
    `-- <path>` header and was all this double kept until 2026-09-02. That is
    not enough to see what the database was told: `create_schemas()` writes its
    `CREATE DATABASE` on line 1 and its `CREATE USER`, `ALTER USER` and `GRANT`
    lines below it, so an assertion with only the first line in reach cannot
    tell a grant made for the emulator's user from one made for somebody
    else's — and a review found exactly that hole under a test that looked
    like it covered the user. Kept ALONGSIDE the first lines, never instead of
    them, because every existing assertion reads `sql_calls`.
    """

    sql_secrets: list[str] = field(default_factory=list)
    """The connection secret every SQL call carried — one entry per `sql_calls` entry.

    `env["MYSQL_PWD"]` at `exec_stdin`, the `password` argument at `sql_query`:
    the two seams this app reaches a database through, recorded as one list
    because "which password did this install spend" is one question about
    both. `""` is a call that carried no `MYSQL_PWD` at all, which is an answer
    no caller in `sqlplan` produces and a mutation of one that does.
    """

    volumes: set[str] = field(default_factory=set)
    """Named volumes that EXIST on this machine (`docker volume inspect` answers)."""

    run_result: docker.AttachedRun = docker.AttachedRun(0, ("extracted",))
    success_returncodes: tuple[int, ...] = (0,)
    """Which statuses this double treats as "the tool did its work and wrote output".

    A field rather than a literal `== 0`, because this double used to encode the
    exact assumption the code under test stopped making. `MmapPlan.success_codes`
    exists because MoveMapGen's convention differs by upstream tree -- the
    Tortoise fork returns 1 when it finishes -- and a double that writes files
    only on 0 cannot represent that tool at all: a test driving a Tortoise
    generator through the real stage got an empty output folder and an error
    about it, which is the double disagreeing with reality rather than the code
    being wrong (2026-09-03).
    """
    produce: dict[str, int] = field(
        default_factory=lambda: {
            "dbc": 100,
            "maps": 100,
            "Buildings": 100,
            "vmaps": 100,
            "mmaps": 500,
        }
    )
    """What a successful container run leaves under the `/out` mount — the real tools' shape.

    The names and the counts are `wow-tbc`'s own `extract.tools[*].produces`
    plus `mmaps.min_files`, and `test_families_cmangos.py` asserts that against
    the catalog: a folder no tool produces would be a fixture the code never
    looks at, and the shortfall it is supposed to exercise could never fire.
    """

    conf_dist: dict[str, str] = field(
        default_factory=lambda: {
            "mangosd.conf.dist": 'LoginDatabaseInfo = "old"\nDataDir = "."\nOther = 1\n',
            "realmd.conf.dist": 'LoginDatabaseInfo = "old"\n',
            "aiplayerbot.conf.dist": "AiPlayerbot.MinRandomBots = 50\n",
            "ahbot.conf.dist": "AuctionHouseBot.Chance.Sell = 0\n",
        }
    )
    failing_sql: str = ""
    """A substring; any stream containing it exits 1 with a mariadb-shaped stderr."""

    query_answer: str = "20000\n"
    realm_row: str = "127.0.0.1\t127.0.0.1\n"
    """What the realm query answers. A FRESH install holds loopback.

    Separate from `query_answer`, which is a row count for the import probe.
    One canned string for every question let the realm guard read "20000" as a
    perfectly good address and skip its write, which made a correct guard look
    broken (review, 2026-09-03)."""
    """What `sql_query` answers, VERBATIM — trailing newline and all.

    `docker.sql_query()` returns the client's stdout untouched, and under
    `--batch --skip-column-names` that distinction carries information no
    caller can get back once it is gone: one row holding the empty string
    prints `"\\n"`, no rows print `""`. A default of `"20000"` — no newline —
    is a fixture more convenient than reality, and a double that can never
    produce a trailing newline cannot exercise the branch it will be pointed
    at. Set it to `""` or to `"\\n"` to drive those two apart.
    """

    column_answer: str | None = None
    """What an `information_schema.columns` question answers; None falls through to `query_answer`.

    Kept apart from `query_answer` for the same reason `realm_row` is: one canned
    string for every question is a fixture answering itself. `check_update_levels()`
    asks whether a schema carries the column its last applied update leaves behind,
    and with a single answer of `"20000"` every schema is always at every level, so
    the branch that refuses one that is not could never be reached. Set this to
    `"0\n"` to drive a schema that stopped part-way through the chain.
    """

    on_clone: Callable[[Path], None] | None = None
    """Called with the dest after each clone — the CMaNGOS tests lay SQL fixtures with it."""

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
        if self.reset_error is not None:
            raise self.reset_error
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

    def relabel(self, path: Path) -> bool:
        """`platform.relabel_for_containers()` on a box where it worked."""
        self.relabelled.append(path)
        return True

    def start_db(self, spec: docker.ContainerSpec, server_dir: Path) -> None:
        self.calls.append("start-db")
        if self.db_start_error:
            raise docker.DockerCommandError(self.db_start_error)
        self.db_started = True

    def run_container(
        self,
        spec: docker.ContainerRun,
        *,
        sink: docker.OutputSink,
        cancel: threading.Event | None = None,
    ) -> docker.AttachedRun:
        """One `docker run --rm`: its output goes to the sink, its files land on the `/out` bind.

        Nothing is produced when the run failed. A tool that segfaults leaves
        the folder it was going to fill empty, and that emptiness is exactly
        what `extract.shortfall()` reads — a double that filled `/out` anyway
        would make every "the tool failed" test pass for the wrong reason.
        """
        self.calls.append(f"run:{spec.argv[0]}")
        self.container_runs.append(spec)
        sink(f"{spec.argv[0]} ran")
        out = next((m.host for m in spec.mounts if m.guest == "/out"), None)
        if out is not None and self.run_result.returncode in self.success_returncodes:
            for name, count in self.produce.items():
                folder = out / name
                folder.mkdir(parents=True, exist_ok=True)
                for index in range(count):
                    (folder / f"{index:05d}.bin").write_bytes(b"x")
        return self.run_result

    def copy_from_image(self, image: str, src: str, dest: Path) -> None:
        """`docker create`+`cp`+`rm`: a `.conf.dist` file, or the whole etc directory."""
        self.calls.append(f"copy:{src}")
        self.copied.append((image, src, dest))
        if src.endswith(".conf.dist"):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(self.conf_dist[Path(src).name], encoding="utf-8")
            return
        dest.mkdir(parents=True, exist_ok=True)
        for name, text in self.conf_dist.items():
            (dest / name).write_text(text, encoding="utf-8")

    def exec_stdin(
        self,
        container: str,
        argv: Sequence[str],
        source: BinaryIO,
        *,
        env: Mapping[str, str],
        wsl_distro: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        text = source.read().decode("utf-8", errors="replace")
        first = text.strip().splitlines()[0] if text.strip() else ""
        self.calls.append("sql")
        self.sql_calls.append(first)
        self.sql_scripts.append(text)
        self.sql_secrets.append(env.get("MYSQL_PWD", ""))
        self.distros.append(wsl_distro)
        if self.failing_sql and self.failing_sql in text:
            return subprocess.CompletedProcess(
                list(argv), 1, "", "ERROR 1064 (42000) at line 1: You have an error in your SQL"
            )
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    def sql_query(
        self,
        container: str,
        client: str,
        password: str,
        schema: str | None,
        statement: str,
        *,
        wsl_distro: str | None = None,
    ) -> str:
        self.calls.append("query")
        self.sql_calls.append(statement)
        self.sql_secrets.append(password)
        self.distros.append(wsl_distro)
        # The realm row is answered separately; see `realm_row`.
        if "realmlist" in statement:
            return self.realm_row
        if self.column_answer is not None and "information_schema.columns" in statement:
            return self.column_answer
        return self.query_answer

    def volume_exists(self, name: str) -> bool:
        return name in self.volumes

    def seams(self, **overrides: object) -> native.Seams:
        def clone(spec: git.CloneSpec) -> None:
            self.calls.append(f"clone:{spec.url}")
            self.clones.append(spec)
            (spec.dest / ".git").mkdir(parents=True, exist_ok=True)
            self.remotes[spec.dest] = spec.url
            if self.on_clone is not None:
                self.on_clone(spec.dest)
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
            # STATED, not detected. A successful install now advertises the realm at
            # the end, and the default seam is the real `platform.detect_lan_ip`, so
            # without this the recorded call lists below depend on whatever network
            # the test box is on -- present on the VM, absent on a machine with no LAN.
            lan_ip=lambda: "192.168.1.25",
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
            # An INERT SELinux by default: not enforcing, on a filesystem that
            # could hold a label if it were. That is Ubuntu/Arch/macOS, which
            # is what every other test in both files is about, and it keeps
            # `:z` out of their rendered compose files. A test that wants the
            # Fedora shape overrides these two by name.
            relabel=self.relabel,
            selinux_enforcing=lambda: False,
            fs_type=lambda path: "ext4",
            keep_awake=lambda: nullcontext(),
            run_container=self.run_container,
            copy_from_image=self.copy_from_image,
            exec_stdin=self.exec_stdin,
            sql_query=self.sql_query,
            volume_exists=self.volume_exists,
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
