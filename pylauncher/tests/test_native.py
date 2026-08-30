"""Tests for the native install engine (`yulon.catalog.native`, roadmap 6.2).

Every external effect is a seam, so a whole install runs here in milliseconds
with no daemon, no network and no four-hour build. That is the only kind of
test anyone on this project can run for this file: there is no Mac, so nothing
below is evidence that the engine installs a server — it is evidence about the
engine's control flow, its refusals, and what a resume does and does not
repeat.

The platform is always injected through the `platform_id` seam. Faking
`sys.platform` instead mutates the real module for the whole process, which is
how this suite once went red on every Python 3.12+ Linux box while CI stayed
green (checklist, "CI was green while the suite was red").

**Every double below must be able to give the answers the real function gives,
including the ones that make the engine refuse.** Four blockers survived 677
green tests and a 41-mutation run on the first version of this file, and all
four survived for the same reason: the doubles could not produce the real
answer. `container_project` returned `None` for a container that does not
exist, where the real one returns `UNREADABLE`; the import probe returned
`absent` with no database running, which the real probe cannot do; the clone
double made a bare `.git` directory, where a real clone of that repository also
lays down its own `docker-compose.yml`; and there was no case at all for the
port scan listing our own containers. So `Recorder` models a machine — what
containers exist on it, what git has, what the database can answer and when —
rather than answering each question the way the code under test would like.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from yulon import docker, git, platform, resources
from yulon.catalog import composegen, native, preflight
from yulon.catalog.catalog import load_catalog
from yulon.catalog.installer import (
    DockerUnavailableError,
    Installer,
    InstallerError,
    InstallOptions,
    UnsupportedPlatformError,
    installer_for,
)

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


def engine(rec: Recorder, **overrides: object) -> native.NativeInstaller:
    return native.NativeInstaller(
        ENTRY,
        installers_root=resources.installers_dir(),
        import_probe=rec.probe,
        reset_unfinished=rec.reset,
        seams=rec.seams(**overrides),
    )


def install(rec: Recorder, server_dir: Path, **overrides: object) -> list[str]:
    return list(engine(rec, **overrides).run(InstallOptions(server_dir=server_dir)))


# -- dispatch ---------------------------------------------------------------


def test_the_platform_decides_which_engine_installs_and_linux_keeps_the_script() -> None:
    """One place decides, from `catalog.json` data (roadmap 6.2/6.3)."""
    assert isinstance(installer_for(ENTRY, platform_id=lambda: "linux"), Installer)
    assert isinstance(installer_for(ENTRY, platform_id=lambda: "macos"), native.NativeInstaller)
    assert isinstance(installer_for(ENTRY, platform_id=lambda: "windows"), native.NativeInstaller)
    # An entry with no macOS/Windows path at all still gets the 6.1 refusal, from
    # the script installer, which is the one place that words it.
    assert isinstance(installer_for(TBC, platform_id=lambda: "macos"), Installer)
    assert isinstance(installer_for(TBC, platform_id=lambda: "windows"), Installer)


def test_script_platforms_defaults_to_platforms_so_old_entries_mean_what_they_said() -> None:
    assert TBC.install.script_platforms is None
    assert TBC.install.scripted_platforms() == TBC.install.platforms
    assert TBC.install.uses_script("linux") is True
    assert TBC.install.is_native("linux") is False
    # And a platform the entry does not support is never "native" — that is the
    # honest 6.1 refusal, not an engine that starts and then fails.
    assert TBC.install.is_native("macos") is False
    assert TBC.install.is_native("windows") is False
    assert ENTRY.install.is_native("macos") is True
    assert ENTRY.install.is_native("windows") is True
    assert ENTRY.install.uses_script("linux") is True
    assert ENTRY.install.uses_script("windows") is False


def test_the_unsupported_platform_refusal_still_comes_first(tmp_path: Path) -> None:
    rec = Recorder()
    installer = native.NativeInstaller(TBC, seams=rec.seams(platform_id=lambda: "macos"))
    with pytest.raises(UnsupportedPlatformError, match="cannot be installed on macOS"):
        list(installer.run(InstallOptions(server_dir=tmp_path)))
    assert rec.calls == []


def test_every_seam_defaults_to_the_real_function_it_stands_in_for() -> None:
    """The doubles are only evidence if the engine really calls these when nobody fakes them.

    `start_db` is named explicitly: it is `docker.start_database()`, the same
    function `repair_import()` calls, so the install and the repair cannot drift
    into starting the database two different ways.
    """
    real = native.Seams()
    assert real.platform_id is platform.detect
    assert real.start_db is docker.start_database
    assert real.container_exists is docker.container_exists
    assert real.container_project is docker.container_project
    assert real.images_built is docker.images_built
    assert real.one_shot is docker.run_one_shot
    assert real.gather is preflight.gather
    # `ensure_docker` is the one seam whose REAL default escalates on Linux, so
    # the engine not calling it (or calling a fake) is what the macOS path's
    # "no sudo" claim ultimately rests on. Pin that the default really is the
    # provisioning function, so a future refactor silently swapping it out
    # cannot look like a no-op.
    assert real.ensure_docker is platform.ensure_docker
    assert real.file_unmodified(Path("/nowhere-at-all"), "docker-compose.yml") is None


# -- the happy path ---------------------------------------------------------


def test_a_fresh_install_runs_every_stage_in_order(tmp_path: Path) -> None:
    rec = Recorder(images=False)
    server_dir = tmp_path / "wow"
    lines = install(rec, server_dir)
    assert rec.calls == [
        "gather",
        f"clone:{ENTRY.emulator.sources[0].url}",
        f"clone:{ENTRY.emulator.sources[1].url}",
        "build",
        "one-shot:ac-client-data-init",
        # The database is up BEFORE the probe is asked anything. Without this
        # the probe cannot answer, and the install refused itself after the
        # multi-hour build — see `test_the_import_cannot_be_asked_anything...`.
        "start-db",
        "probe",
        "one-shot:ac-db-import",
        "verify",
        "start",
    ]
    assert "compiling" in lines  # the build's output is streamed, not buffered
    state = native.read_state(server_dir, valid=native.STAGE_ORDER)
    assert state is not None
    assert state.completed == (
        "clone-core",
        "clone-modules",
        "generate-compose",
        "build",
        "client-data",
        "import",
    )
    # Every compose file is on disk and carries our marker.
    for name in composegen.COMPOSE_FILES:
        assert (
            (server_dir / name).read_text(encoding="utf-8").startswith(composegen.GENERATED_MARKER)
        )


def test_preflight_and_guard_and_up_and_ready_are_never_recorded(tmp_path: Path) -> None:
    """A guard a resume skips is not a guard, and a resume must really start the server.

    The assertion that matters is the engine-level one: a finished install has
    none of them written down. `with_stage` is asserted below only for what it
    still owns — ordering and the stage names it will accept at all.
    """
    rec = Recorder(images=False)
    server_dir = tmp_path / "wow"
    install(rec, server_dir)
    state = native.read_state(server_dir, valid=native.STAGE_ORDER)
    assert state is not None
    assert not (set(state.completed) & native.NEVER_RECORDED)
    fresh = native.InstallState(game_id=ENTRY.id, install_id="abcd1234")
    assert fresh.with_stage("build", native.STAGE_ORDER).completed == ("build",)
    # `with_stage` no longer knows NEVER_RECORDED; `_run_stage` enforces it (the
    # engine-level assertion above) and A.3 moves the rule to `Stage.recorded`.
    assert fresh.with_stage("no-such-stage", native.STAGE_ORDER).completed == ()


def resumed(server_dir: Path, **overrides: object) -> Recorder:
    """A recorder for a SECOND run against a directory a first run left behind.

    It knows the clone's origin, because a machine does: `git remote get-url`
    answers for a checkout that is on disk, whoever put it there.
    """
    rec = Recorder(**overrides)  # type: ignore[arg-type]
    rec.remotes[server_dir] = ENTRY.emulator.sources[0].url
    rec.remotes[server_dir / "modules" / "mod-playerbots"] = ENTRY.emulator.sources[1].url
    return rec


def test_a_resume_starts_and_verifies_the_server_again_but_does_not_rebuild(
    tmp_path: Path,
) -> None:
    """The point of recording stages: the four-hour one is not repeated."""
    server_dir = tmp_path / "wow"
    install(Recorder(images=False), server_dir)
    again = resumed(server_dir, images=True, probe_answers=[IMPORTED])
    install(again, server_dir)
    assert "build" not in again.calls
    assert "start" in again.calls  # `up` is never recorded, so it always runs
    assert f"clone:{ENTRY.emulator.sources[0].url}" in again.calls  # updates, not skipped


def test_a_state_file_claiming_a_build_that_docker_cannot_confirm_rebuilds(
    tmp_path: Path,
) -> None:
    """Disk evidence beats the state file. `None` is not "no images"."""
    server_dir = tmp_path / "wow"
    install(Recorder(images=False), server_dir)
    gone = resumed(server_dir, images=False, probe_answers=[IMPORTED])
    install(gone, server_dir)
    assert "build" in gone.calls
    unknown = resumed(server_dir, images=None, probe_answers=[IMPORTED])
    lines = install(unknown, server_dir)
    assert "build" in unknown.calls
    assert any("would not say whether" in line for line in lines)


# -- the guard --------------------------------------------------------------


def test_a_copied_install_folder_is_refused(tmp_path: Path) -> None:
    """The state file's `install_id` is a hash of the folder it belongs to.

    Copying a server folder does not copy its containers or its database
    volume, so a resume in the copy would adopt the original's stack.
    """
    original = tmp_path / "wow"
    install(Recorder(images=False), original)
    copy = tmp_path / "wow-copy"
    copy.mkdir()
    (copy / native.STATE_FILE).write_text(
        (original / native.STATE_FILE).read_text(encoding="utf-8"), encoding="utf-8"
    )
    with pytest.raises(InstallerError, match="copy of another install"):
        install(Recorder(), copy)


def test_a_directory_with_somebody_elses_files_is_refused_and_untouched(tmp_path: Path) -> None:
    server_dir = tmp_path / "not-empty"
    server_dir.mkdir()
    (server_dir / "holiday-photos.zip").write_text("mine", encoding="utf-8")
    rec = Recorder()
    with pytest.raises(InstallerError, match="not empty"):
        install(rec, server_dir)
    assert (server_dir / "holiday-photos.zip").read_text(encoding="utf-8") == "mine"
    assert "clone" not in " ".join(rec.calls)


def test_a_directory_holding_only_our_state_file_counts_as_empty(tmp_path: Path) -> None:
    """Or the record of a failed attempt would block the retry it exists to help."""
    server_dir = tmp_path / "wow"
    server_dir.mkdir()
    native.write_state(
        server_dir,
        native.InstallState(
            game_id=ENTRY.id,
            install_id=composegen.install_id(server_dir, platform_id=lambda: "macos"),
            last_error="the build was stopped",
        ),
    )
    install(Recorder(images=False), server_dir)


def test_a_container_owned_by_another_project_is_refused_by_name(tmp_path: Path) -> None:
    rec = Recorder(containers={"ac-worldserver": "somebody-elses-project"})
    with pytest.raises(InstallerError, match="belongs to another install"):
        install(rec, tmp_path / "wow")


def test_an_unreadable_container_owner_refuses_rather_than_assuming(tmp_path: Path) -> None:
    rec = Recorder(containers={"ac-database": docker.UNREADABLE})
    with pytest.raises(InstallerError, match="would not say which install"):
        install(rec, tmp_path / "wow")


def test_a_machine_that_has_never_run_this_server_is_not_a_conflict(tmp_path: Path) -> None:
    """The blocker that refused every fresh install, and the answer that hid it.

    `docker inspect <missing container>` exits 1, so `container_project()`
    answers `UNREADABLE` for a container that is not there — not `None`. The
    guard asked it about all three names unconditionally and refused every
    machine that had never had this server, naming a container the user could
    then go and fail to find. The shipped double answered `None`, which the real
    function never gives for an absent container (review, 2026-08-23).
    """
    rec = Recorder(images=False)
    assert rec.containers == {}  # a clean machine: nothing by those names exists
    assert rec.container_project("ac-database") == docker.UNREADABLE
    install(rec, tmp_path / "wow")
    assert "start" in rec.calls


def test_a_container_wearing_our_name_with_no_compose_label_is_not_a_conflict(
    tmp_path: Path,
) -> None:
    """`None` means it exists and was started outside compose — that is not another install."""
    rec = Recorder(images=False, containers={"ac-database": None})
    install(rec, tmp_path / "wow")
    assert "start" in rec.calls


def test_a_daemon_that_will_not_list_containers_refuses_before_writing_anything(
    tmp_path: Path,
) -> None:
    """`container_exists()` goes through `docker._run()`, which raises rather than degrades."""
    rec = Recorder(daemon_lists_containers=False)
    server_dir = tmp_path / "wow"
    with pytest.raises(InstallerError, match="would not say what containers"):
        install(rec, server_dir)
    assert not rec.clones
    assert not server_dir.exists()


# -- the clone stages -------------------------------------------------------


def test_a_checkout_of_the_wrong_repository_is_refused_and_never_deleted(
    tmp_path: Path,
) -> None:
    """Refused BY NAME: a directory holding somebody's fork is not ours to remove."""
    server_dir = tmp_path / "wow"
    (server_dir / ".git").mkdir(parents=True)
    (server_dir / "keep-me.txt").write_text("mine", encoding="utf-8")
    rec = Recorder()
    rec.remotes[server_dir] = "https://github.com/someone/else.git"
    with pytest.raises(InstallerError, match="already a git checkout"):
        install(rec, server_dir)
    assert (server_dir / "keep-me.txt").exists()
    assert not rec.clones


def test_a_checkout_git_will_not_identify_is_refused_rather_than_cloned_over(
    tmp_path: Path,
) -> None:
    """The clone seam DELETES a destination it does not recognise, so "unknown" must refuse."""
    server_dir = tmp_path / "wow"
    (server_dir / ".git").mkdir(parents=True)
    (server_dir / "somebody-elses-source").write_text("mine", encoding="utf-8")
    rec = Recorder()  # knows no remote for this directory
    with pytest.raises(InstallerError, match="would not say what it is a checkout of"):
        install(rec, server_dir)
    assert not rec.clones
    assert (server_dir / "somebody-elses-source").exists()


def test_the_same_repository_spelled_differently_is_not_a_conflict(tmp_path: Path) -> None:
    """`...y.git`, `...y` and `git@github.com:x/y` are one repository."""
    server_dir = tmp_path / "wow"
    (server_dir / ".git").mkdir(parents=True)
    rec = Recorder(images=False)
    rec.remotes[server_dir] = ENTRY.emulator.sources[0].url.removesuffix(".git")
    install(rec, server_dir)
    assert rec.clones  # it updated through the seam rather than refusing


def _already_cloned(rec: Recorder, server_dir: Path) -> None:
    """Put the core checkout on disk the way `clone-core` leaves it.

    `.git`, an origin git will answer for, and the `docker-compose.yml` the
    repository ships — a module test that skipped the last one got refused three
    stages earlier for a reason that had nothing to do with modules.
    """
    (server_dir / ".git").mkdir(parents=True, exist_ok=True)
    rec.remotes[server_dir] = ENTRY.emulator.sources[0].url
    path = server_dir / composegen.BASE_FILE
    path.write_text(UPSTREAM_COMPOSE, encoding="utf-8")
    rec.tracked[path] = UPSTREAM_COMPOSE


def test_a_module_directory_holding_another_repository_is_refused_too(tmp_path: Path) -> None:
    """The same rule one level down: `modules/mod-playerbots` may be somebody's fork."""
    server_dir = tmp_path / "wow"
    module = server_dir / "modules" / "mod-playerbots"
    (server_dir / ".git").mkdir(parents=True)
    (module / ".git").mkdir(parents=True)
    rec = Recorder(images=False)
    rec.remotes[server_dir] = ENTRY.emulator.sources[0].url
    rec.remotes[module] = "https://github.com/someone/mod-playerbots.git"
    with pytest.raises(InstallerError, match="not of"):
        install(rec, server_dir)
    assert all(spec.dest != module for spec in rec.clones)


def test_a_module_directory_the_user_put_there_by_hand_is_never_deleted(tmp_path: Path) -> None:
    """The clone seam `rmtree`s a destination it does not recognise, one level down too.

    `_remote_of()` answers `None` for a directory with no `.git`, so a
    `modules/mod-playerbots` unpacked from a tarball or copied in by hand fell
    through the only check this loop had and was silently deleted — in the one
    engine that refuses to touch a directory it does not own everywhere else
    (review, 2026-08-23).
    """
    server_dir = tmp_path / "wow"
    module = server_dir / "modules" / "mod-playerbots"
    module.mkdir(parents=True)
    (module / "my-own-patches.cpp").write_text("mine", encoding="utf-8")
    rec = Recorder(images=False)
    _already_cloned(rec, server_dir)
    with pytest.raises(InstallerError, match="has files in it but is not a checkout"):
        install(rec, server_dir)
    assert (module / "my-own-patches.cpp").read_text(encoding="utf-8") == "mine"
    assert all(spec.dest != module for spec in rec.clones)


def test_a_module_checkout_git_cannot_identify_is_refused_rather_than_reset(
    tmp_path: Path,
) -> None:
    """Otherwise it gets `fetch` + `reset --hard FETCH_HEAD` against whatever `origin` is."""
    server_dir = tmp_path / "wow"
    module = server_dir / "modules" / "mod-playerbots"
    (module / ".git").mkdir(parents=True)
    rec = Recorder(images=False)  # knows no remote for that directory
    _already_cloned(rec, server_dir)
    with pytest.raises(InstallerError, match="would not say what it is a checkout of"):
        install(rec, server_dir)
    assert all(spec.dest != module for spec in rec.clones)


def test_the_core_is_cloned_at_full_depth_and_the_module_shallow(tmp_path: Path) -> None:
    """Data, not a constant: `genrev.cmake` reads the revision out of git history."""
    rec = Recorder(images=False)
    install(rec, tmp_path / "wow")
    core, module = rec.clones[0], rec.clones[1]
    assert core.depth is None
    assert module.depth == 1
    assert module.dest.name == "mod-playerbots"
    assert module.dest.parent.name == "modules"


# -- generating the compose files -------------------------------------------


def test_the_compose_file_the_clone_brings_with_it_does_not_refuse_the_install(
    tmp_path: Path,
) -> None:
    """The blocker that refused every install, told to a user who DID pick an empty folder.

    The server directory is the emulator checkout, and that repository ships its
    own `docker-compose.yml` at the root — the Linux installer only writes an
    override precisely because it is there. So the clone stage always lays an
    unmarked base file down, and `write_plan()`'s marker rule then said "point
    the install at an empty folder, or move that file aside", after a 2.4 GB
    clone (review, 2026-08-23).
    """
    rec = Recorder(images=False)
    server_dir = tmp_path / "wow"
    lines = install(rec, server_dir)
    assert (
        (server_dir / composegen.BASE_FILE)
        .read_text(encoding="utf-8")
        .startswith(composegen.GENERATED_MARKER)
    )
    assert any("came with the repository" in line for line in lines)


def test_a_compose_file_the_user_edited_is_still_refused(tmp_path: Path) -> None:
    """The exception is "git says this is exactly what the clone wrote", nothing wider.

    A modified file answers ` M path` to `git status --porcelain`, an untracked
    one answers `?? path`, and a git that cannot be asked answers nothing at
    all. All three keep the refusal, because only an empty answer proves `git
    checkout` can put the file back.
    """
    rec = Recorder(images=False)
    server_dir = tmp_path / "wow"

    def clone_then_edit(spec: git.CloneSpec) -> None:
        rec.calls.append(f"clone:{spec.url}")
        rec.clones.append(spec)
        (spec.dest / ".git").mkdir(parents=True, exist_ok=True)
        rec.remotes[spec.dest] = spec.url
        if spec.url == ENTRY.emulator.sources[0].url:
            path = spec.dest / composegen.BASE_FILE
            rec.tracked[path] = UPSTREAM_COMPOSE
            path.write_text(UPSTREAM_COMPOSE + "    ports: ['3307:3306']\n", encoding="utf-8")

    with pytest.raises(InstallerError, match="not written by Yu'lon"):
        install(rec, server_dir, clone=clone_then_edit)


def test_a_git_that_will_not_answer_keeps_the_refusal_rather_than_overwriting(
    tmp_path: Path,
) -> None:
    """Fail closed: "we could not check" is not "it is safe to replace"."""
    rec = Recorder(images=False, git_answers=False)
    with pytest.raises(InstallerError, match="not written by Yu'lon"):
        install(rec, tmp_path / "wow")


def test_the_override_and_build_files_get_no_such_exception(tmp_path: Path) -> None:
    """Upstream ships neither, so an unmarked one there is somebody's own settings."""
    rec = Recorder(images=False)
    server_dir = tmp_path / "wow"

    def clone_with_an_override(spec: git.CloneSpec) -> None:
        rec.calls.append(f"clone:{spec.url}")
        rec.clones.append(spec)
        (spec.dest / ".git").mkdir(parents=True, exist_ok=True)
        rec.remotes[spec.dest] = spec.url
        if spec.url == ENTRY.emulator.sources[0].url:
            for name in (composegen.BASE_FILE, composegen.OVERRIDE_FILE):
                path = spec.dest / name
                path.write_text(UPSTREAM_COMPOSE, encoding="utf-8")
                rec.tracked[path] = UPSTREAM_COMPOSE

    with pytest.raises(InstallerError, match="not written by Yu'lon"):
        install(rec, server_dir, clone=clone_with_an_override)
    assert (server_dir / composegen.OVERRIDE_FILE).read_text(encoding="utf-8") == UPSTREAM_COMPOSE


# -- the import stage -------------------------------------------------------


def test_the_database_is_started_before_the_import_is_asked_anything(tmp_path: Path) -> None:
    """The blocker that killed every install AFTER the multi-hour build.

    The import probe is a `docker exec ac-database mysql …`; with no database
    container it raises and reads `unreadable`, which `_import()` turns into a
    hard refusal. And running the one-shot anyway would not have helped:
    `run_one_shot()` passes `--no-deps`, which prunes the `depends_on:
    ac-database: service_healthy` edge the generated base file declares.

    Asserted as an ORDER rather than a call count, because a `start-db` that
    happened after the probe would satisfy every other assertion here.
    """
    rec = Recorder(images=False)
    install(rec, tmp_path / "wow")
    assert rec.calls.index("start-db") < rec.calls.index("probe")
    assert rec.calls.index("start-db") < rec.calls.index("one-shot:ac-db-import")


def test_an_install_whose_database_never_starts_says_so_instead_of_blaming_the_import(
    tmp_path: Path,
) -> None:
    rec = Recorder(images=False, db_start_error="ac-database did not report healthy within 180s")
    with pytest.raises(InstallerError, match="database could not be started"):
        install(rec, tmp_path / "wow")
    assert "one-shot:ac-db-import" not in rec.calls


def test_starting_the_database_is_never_recorded_so_a_resume_does_it_again(
    tmp_path: Path,
) -> None:
    """A resume probes too, so it needs the database up just as much as a first install."""
    server_dir = tmp_path / "wow"
    install(Recorder(images=False), server_dir)
    state = native.read_state(server_dir, valid=native.STAGE_ORDER)
    assert state is not None
    assert "start-db" not in state.completed
    again = resumed(server_dir, images=True, probe_answers=[IMPORTED])
    install(again, server_dir)
    assert again.calls.index("start-db") < again.calls.index("probe")


def test_a_half_written_import_is_cleared_before_it_is_re_run(tmp_path: Path) -> None:
    """Measured on yulon-ubuntu, 2026-08-23: re-running over a live schema is destructive.

    AzerothCore skips the base data for a database that already exists, records
    every remaining file as applied, and leaves the schema permanently
    unfinished — a "success" in 28 seconds that destroys the only way out.
    """
    rec = Recorder(images=False, probe_answers=[PARTIAL, IMPORTED])
    install(rec, tmp_path / "wow")
    assert rec.calls.index("reset") < rec.calls.index("one-shot:ac-db-import")


def test_a_finished_import_is_left_alone_by_a_resume(tmp_path: Path) -> None:
    rec = Recorder(images=False, probe_answers=[IMPORTED])
    install(rec, tmp_path / "wow")
    assert "one-shot:ac-db-import" not in rec.calls
    assert "verify" not in rec.calls


def test_an_unreadable_database_refuses_rather_than_importing_over_it(tmp_path: Path) -> None:
    rec = Recorder(images=False, probe_answers=[UNREADABLE])
    with pytest.raises(InstallerError, match="could not be asked"):
        install(rec, tmp_path / "wow")
    assert "one-shot:ac-db-import" not in rec.calls


def test_a_populated_but_unfinished_database_is_refused(tmp_path: Path) -> None:
    """Rows are somebody's until proven otherwise; the import would overwrite them."""
    rec = Recorder(images=False, probe_answers=[POPULATED_HALF])
    with pytest.raises(InstallerError, match="already hold data"):
        install(rec, tmp_path / "wow")


def test_an_engine_without_the_reset_seam_refuses_a_partial_database(tmp_path: Path) -> None:
    rec = Recorder(images=False, probe_answers=[PARTIAL])
    installer = native.NativeInstaller(
        ENTRY,
        installers_root=resources.installers_dir(),
        import_probe=rec.probe,
        reset_unfinished=None,
        seams=rec.seams(),
    )
    with pytest.raises(InstallerError, match="no way to clear"):
        list(installer.run(InstallOptions(server_dir=tmp_path / "wow")))


def test_an_engine_with_no_probe_at_all_refuses_before_anything_runs(tmp_path: Path) -> None:
    """An installer that cannot ask what the databases hold must not write to them."""
    rec = Recorder()
    installer = native.NativeInstaller(ENTRY, import_probe=None, seams=rec.seams())
    with pytest.raises(InstallerError, match="built without a way to check"):
        list(installer.run(InstallOptions(server_dir=tmp_path / "wow")))
    assert rec.calls == []


# -- failure, cancel and the state file -------------------------------------


def test_a_failed_stage_records_nothing_so_it_runs_again(tmp_path: Path) -> None:
    rec = Recorder(images=False, build_result=docker.AttachedRun(1, ("no space left on device",)))
    server_dir = tmp_path / "wow"
    with pytest.raises(InstallerError, match="no space left"):
        install(rec, server_dir)
    state = native.read_state(server_dir, valid=native.STAGE_ORDER)
    assert state is not None
    assert "build" not in state.completed
    assert state.last_error  # …but the reason is kept for the next run's log


def test_a_failure_before_anything_was_written_leaves_no_state_file(tmp_path: Path) -> None:
    """Or the record of the failure becomes the non-empty directory that blocks the retry."""
    server_dir = tmp_path / "wow"
    rec = Recorder()
    rec.remotes[server_dir] = "https://github.com/someone/else.git"
    (server_dir / ".git").mkdir(parents=True)
    with pytest.raises(InstallerError):
        install(rec, server_dir)
    assert not (server_dir / native.STATE_FILE).exists()


def test_cancel_between_stages_stops_and_says_what_the_daemon_is_still_doing(
    tmp_path: Path,
) -> None:
    """The honest cancel copy: BuildKit finishes its step and the work is kept."""
    cancel = threading.Event()
    rec = Recorder(images=False)

    def build_then_cancel(
        server_dir: Path,
        files: object,
        *,
        sink: object = None,
        cancel_: object = None,
        **kw: object,
    ) -> docker.AttachedRun:
        rec.calls.append("build")
        cancel.set()
        return docker.AttachedRun(docker.CANCELLED_RETURNCODE, ("stopped",))

    installer = engine(rec, build=build_then_cancel)
    with pytest.raises(InstallerError, match="already on"):
        list(installer.run(InstallOptions(server_dir=tmp_path / "wow"), cancel=cancel))
    assert "one-shot:ac-db-import" not in rec.calls


def test_the_build_cancel_note_is_said_at_the_build_and_not_before_every_stage(
    tmp_path: Path,
) -> None:
    """It is copy about the BUILD, and it was being said as the second line of every install.

    A user who stopped 20 minutes into the 2.4 GB clone, or during the
    client-data download, was told "Docker is finishing the build step it is
    already on" and that the work was kept (review, 2026-08-23). The opening
    line now says only what is true of every stage; the build's own sentence
    belongs to the build.
    """
    rec = Recorder(images=False)
    lines = install(rec, tmp_path / "wow")
    assert native.OPENING_NOTE in lines
    assert lines.index(native.OPENING_NOTE) == 1
    build_at = next(index for index, line in enumerate(lines) if line == "--- build")
    assert native.BUILD_CANCEL_NOTE in lines
    assert lines.index(native.BUILD_CANCEL_NOTE) > build_at
    assert native.BUILD_CANCEL_NOTE not in lines[:build_at]


def test_a_cancel_says_what_is_true_of_the_stage_that_was_cancelled(tmp_path: Path) -> None:
    """Three stages, three different things a Stop costs, and one of them is nothing."""
    cancel = threading.Event()
    stopped = docker.AttachedRun(docker.CANCELLED_RETURNCODE, ("stopped",))

    rec = Recorder(images=False, one_shot_result=stopped)
    with pytest.raises(InstallerError) as caught:
        install(rec, tmp_path / "download")
    # The client-data fetch resumes; nothing about a build step is true here.
    assert native.DOWNLOAD_CANCEL_NOTE in str(caught.value)
    assert native.BUILD_CANCEL_NOTE not in str(caught.value)

    later = Recorder(images=False)
    installer = engine(later)
    generator = installer.run(InstallOptions(server_dir=tmp_path / "between"), cancel=cancel)
    next(generator)
    cancel.set()
    with pytest.raises(InstallerError) as between:
        list(generator)
    # Stopped between stages: nothing is half-done, so there is nothing to add.
    assert str(between.value) == "the install was stopped."


def test_a_state_file_from_another_game_is_refused(tmp_path: Path) -> None:
    server_dir = tmp_path / "wow"
    server_dir.mkdir()
    native.write_state(
        server_dir,
        native.InstallState(
            game_id="wow-tbc",
            install_id=composegen.install_id(server_dir, platform_id=lambda: "macos"),
        ),
    )
    with pytest.raises(InstallerError, match="already holds an install of wow-tbc"):
        install(Recorder(), server_dir)


def test_an_unreadable_state_file_is_a_missing_hint_not_a_crash(tmp_path: Path) -> None:
    server_dir = tmp_path / "wow"
    server_dir.mkdir()
    (server_dir / native.STATE_FILE).write_text("{not json", encoding="utf-8")
    assert native.read_state(server_dir, valid=native.STAGE_ORDER) is None


def test_the_state_file_only_records_stages_this_engine_knows(tmp_path: Path) -> None:
    """A file naming a stage that no longer exists must not become a skip."""
    server_dir = tmp_path / "wow"
    server_dir.mkdir()
    (server_dir / native.STATE_FILE).write_text(
        json.dumps(
            {
                "version": 1,
                "game_id": ENTRY.id,
                "install_id": "abc",
                "completed": ["build", "invent-a-stage"],
            }
        ),
        encoding="utf-8",
    )
    state = native.read_state(server_dir, valid=native.STAGE_ORDER)
    assert state is not None
    assert state.completed == ("build",)


# -- provisioning and readiness --------------------------------------------


def test_the_panel_says_docker_is_being_set_up_before_the_silent_wait(tmp_path: Path) -> None:
    """Provisioning can be a .dmg download plus a 180-second poll with no output of its own.

    The first macOS tester saw two lines and then nothing for minutes, and
    called the install silently dead (macOS gate, 2026-08-25). The engine must
    say what it is waiting on BEFORE it starts waiting, and say how long that
    can take.
    """
    rec = Recorder()
    report = platform.ProvisionReport("macos", (), ("open Docker Desktop yourself",), ())
    installer = engine(
        rec,
        docker_ready=lambda: False,
        ensure_docker=lambda **_kwargs: report,
    )
    lines: list[str] = []
    with pytest.raises(DockerUnavailableError):
        for line in installer.run(InstallOptions(server_dir=tmp_path / "wow")):
            lines.append(line)
    docker_lines = [line for line in lines if "Docker" in line]
    assert docker_lines, lines
    assert any("minutes" in line for line in docker_lines), docker_lines


def test_docker_that_cannot_be_provisioned_is_a_clean_refusal(tmp_path: Path) -> None:
    rec = Recorder()
    report = platform.ProvisionReport("macos", (), ("open Docker Desktop yourself",), ())
    installer = engine(
        rec,
        docker_ready=lambda: False,
        ensure_docker=lambda **_kwargs: report,
    )
    with pytest.raises(DockerUnavailableError):
        list(installer.run(InstallOptions(server_dir=tmp_path / "wow")))


def test_the_engines_own_platform_reaches_preflight_instead_of_the_real_host(
    tmp_path: Path,
) -> None:
    """Or an engine that dispatches as macOS gathers facts about the box it is really on."""
    seen: dict[str, object] = {}

    def gather(entry: object, server_dir: Path, **kwargs: object) -> preflight.Facts:
        seen.update(kwargs)
        return Recorder().gather(entry, server_dir)

    rec = Recorder(images=False)
    install(rec, tmp_path / "wow", gather=gather)
    assert callable(seen["platform_id"])
    assert seen["platform_id"]() == "macos"  # type: ignore[operator]
    assert callable(seen["docker_ready"])


def test_a_docker_that_stops_answering_during_preflight_is_a_sentence_not_a_traceback(
    tmp_path: Path,
) -> None:
    """`gather()`'s port scan goes through `docker._run()`, which RAISES.

    `run()`'s contract is that its message is the sentence a user reads in the
    failure dialog. Every other outward call in this engine is wrapped; this one
    was not, so a `docker ps` that failed after `docker_ready()` said yes escaped
    as a raw `DockerCommandError` (review, 2026-08-23).
    """

    def gather(entry: object, server_dir: Path, **_kwargs: object) -> preflight.Facts:
        raise docker.DockerCommandError("docker ps --format {{.Names}} exited 1: no daemon")

    rec = Recorder()
    with pytest.raises(InstallerError, match="would not answer again"):
        install(rec, tmp_path / "wow", gather=gather)


def test_a_server_that_never_reports_ready_fails_the_install(tmp_path: Path) -> None:
    rec = Recorder(images=False, ready=False)
    with pytest.raises(InstallerError, match="never reported ready"):
        install(rec, tmp_path / "wow")


def test_a_machine_that_cannot_be_held_awake_says_so_and_installs_anyway(
    tmp_path: Path,
) -> None:
    """A power API saying no is not a reason to refuse an install."""

    @contextmanager
    def refuses() -> Iterator[None]:
        raise RuntimeError("keep_awake() must run on the worker thread")
        yield  # pragma: no cover - unreachable, present so this is a generator

    rec = Recorder(images=False)
    lines = install(rec, tmp_path / "wow", keep_awake=refuses)
    assert any("go to sleep" in line for line in lines)
    assert "start" in rec.calls


def test_a_stage_failure_is_not_swallowed_by_the_keep_awake_wrapper(tmp_path: Path) -> None:
    """`InstallerError` is a `RuntimeError`, and the wrapper catches RuntimeError.

    Written as its own test because the obvious spelling of that context
    manager — a `try` around the `yield` — turns every failed build into a
    cheerful "this machine may go to sleep" and an install that reports success.
    """
    rec = Recorder(images=False, build_result=docker.AttachedRun(2, ("compiler died",)))
    with pytest.raises(InstallerError, match="compiler died"):
        install(rec, tmp_path / "wow")


def test_the_native_engine_never_hands_a_prompter_to_provisioning(tmp_path: Path) -> None:
    """The one thing keeping a native Linux entry from escalating silently.

    `native.py`'s module docstring argues that "nothing on this path may
    prompt" is structural rather than a fact about today's catalog: the engine
    calls `ensure_docker()` itself, and on Linux that call CAN join the docker
    group, so the safety comes from passing no prompter down — with nobody to
    ask, `ensure_docker()` declines.

    That invariant was enforced by nothing but a human reading the source. The
    script path got a pinning test for its half the same day; this side, which
    the docstring itself says is one `catalog.json` key away from mattering,
    did not (review, 2026-08-24).
    """
    seen: list[dict[str, object]] = []

    def provision(**kwargs: object) -> platform.ProvisionReport:
        seen.append(dict(kwargs))
        return platform.ProvisionReport("linux", docker_ready=True)

    def prompter(_question: str) -> str:
        raise AssertionError("the native path asked the user something")

    rec = Recorder()
    installer = engine(rec, docker_ready=lambda: False, ensure_docker=provision)
    installer.preflight(InstallOptions(server_dir=tmp_path / "srv"), ask=prompter)

    assert seen, "ensure_docker was never reached"
    assert seen[0].get("ask") is None


def test_macos_native_installer_full_run_and_compose_generation(tmp_path: Path) -> None:
    """Full WotLK native install workflow on macOS produces compose files and starts services."""
    rec = Recorder(images=False)
    server_dir = tmp_path / "wow-macos"
    lines = install(rec, server_dir)

    # All three compose files exist on disk
    base_file = server_dir / composegen.BASE_FILE
    override_file = server_dir / composegen.OVERRIDE_FILE
    build_file = server_dir / composegen.BUILD_FILE

    assert base_file.is_file()
    assert override_file.is_file()
    assert build_file.is_file()

    # macOS leaves user to image rather than root
    base_content = base_file.read_text(encoding="utf-8")
    assert 'user: "0:0"' not in base_content
    assert "# user: left to the image (acore)" in base_content

    # Images in base compose file match expected tags
    refs = composegen.built_image_refs(ENTRY, server_dir, platform_id=lambda: "macos")
    assert any(ref in base_content for ref in refs)

    # Build overlay specifies build contexts and Dockerfiles
    build_content = build_file.read_text(encoding="utf-8")
    assert "apps/docker/Dockerfile" in build_content

    # Full stage progression through import and start
    assert rec.calls == [
        "gather",
        f"clone:{ENTRY.emulator.sources[0].url}",
        f"clone:{ENTRY.emulator.sources[1].url}",
        "build",
        "one-shot:ac-client-data-init",
        "start-db",
        "probe",
        "one-shot:ac-db-import",
        "verify",
        "start",
    ]
    assert any("The server is up." in line for line in lines)


def test_macos_native_installer_handles_db_healthy_timeout(tmp_path: Path) -> None:
    """If the database never reaches healthy during ready stage, fail with log advice."""
    rec = Recorder(images=False, db_healthy=False)
    with pytest.raises(InstallerError, match="The database never reported healthy"):
        install(rec, tmp_path / "wow-macos-db-unhealthy")
