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


@dataclass
class Recorder:
    """A whole machine's worth of doubles, and a record of what the engine did to it."""

    calls: list[str] = field(default_factory=list)
    clones: list[git.CloneSpec] = field(default_factory=list)
    remotes: dict[Path, str] = field(default_factory=dict)
    images: bool | None = True
    build_result: docker.AttachedRun = docker.AttachedRun(0, ("built",))
    one_shot_result: docker.AttachedRun = docker.AttachedRun(0, ("ran",))
    probe_answers: list[docker.ImportState] = field(default_factory=lambda: [ABSENT, IMPORTED])
    reset_answer: tuple[str, ...] = ("acore_world",)
    projects: dict[str, str | None] = field(default_factory=dict)
    db_healthy: bool = True
    ready: bool = True

    def probe(self) -> docker.ImportState:
        self.calls.append("probe")
        return self.probe_answers.pop(0) if len(self.probe_answers) > 1 else self.probe_answers[0]

    def reset(self) -> tuple[str, ...]:
        self.calls.append("reset")
        return self.reset_answer

    def seams(self, **overrides: object) -> native.Seams:
        def clone(spec: git.CloneSpec) -> None:
            self.calls.append(f"clone:{spec.url}")
            self.clones.append(spec)
            (spec.dest / ".git").mkdir(parents=True, exist_ok=True)
            self.remotes[spec.dest] = spec.url

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
            images_built=lambda server_dir, files: self.images,
            build=build,
            one_shot=one_shot,
            verify_import=verify,
            container_project=lambda name: self.projects.get(name),
            start=self.start,
            wait_db_healthy=lambda spec: self.db_healthy,
            wait_ready=lambda spec, host, port: self.ready,
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
    """One place decides, from `catalog.json` data (roadmap 6.2)."""
    assert isinstance(installer_for(ENTRY, platform_id=lambda: "linux"), Installer)
    assert isinstance(installer_for(ENTRY, platform_id=lambda: "macos"), native.NativeInstaller)
    # An entry with no macOS path at all still gets the 6.1 refusal, from the
    # script installer, which is the one place that words it.
    assert isinstance(installer_for(TBC, platform_id=lambda: "macos"), Installer)


def test_script_platforms_defaults_to_platforms_so_old_entries_mean_what_they_said() -> None:
    assert TBC.install.script_platforms is None
    assert TBC.install.scripted_platforms() == TBC.install.platforms
    assert TBC.install.uses_script("linux") is True
    assert TBC.install.is_native("linux") is False
    # And a platform the entry does not support is never "native" — that is the
    # honest 6.1 refusal, not an engine that starts and then fails.
    assert TBC.install.is_native("macos") is False
    assert ENTRY.install.is_native("macos") is True
    assert ENTRY.install.uses_script("linux") is True


def test_the_unsupported_platform_refusal_still_comes_first(tmp_path: Path) -> None:
    rec = Recorder()
    installer = native.NativeInstaller(TBC, seams=rec.seams(platform_id=lambda: "macos"))
    with pytest.raises(UnsupportedPlatformError, match="cannot be installed on macOS"):
        list(installer.run(InstallOptions(server_dir=tmp_path)))
    assert rec.calls == []


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
        "probe",
        "one-shot:ac-db-import",
        "verify",
        "start",
    ]
    assert "compiling" in lines  # the build's output is streamed, not buffered
    state = native.read_state(server_dir)
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

    Asserted twice on purpose: a finished install has none of them written
    down, AND the state object refuses to record them at all. Only the second
    half survives someone rearranging `run()` — the first would keep passing if
    a stage stopped being reached for an unrelated reason.
    """
    rec = Recorder(images=False)
    server_dir = tmp_path / "wow"
    install(rec, server_dir)
    state = native.read_state(server_dir)
    assert state is not None
    assert not (set(state.completed) & native.NEVER_RECORDED)
    fresh = native.InstallState(game_id=ENTRY.id, install_id="abcd1234")
    for stage in ("preflight", "guard", "up", "ready"):
        assert fresh.with_stage(stage).completed == (), f"{stage} was written down"
    assert fresh.with_stage("build").completed == ("build",)


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
    rec = Recorder(projects={"ac-worldserver": "somebody-elses-project"})
    with pytest.raises(InstallerError, match="belongs to another install"):
        install(rec, tmp_path / "wow")


def test_an_unreadable_container_owner_refuses_rather_than_assuming(tmp_path: Path) -> None:
    rec = Recorder(projects={"ac-database": docker.UNREADABLE})
    with pytest.raises(InstallerError, match="would not say which install"):
        install(rec, tmp_path / "wow")


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


def test_the_core_is_cloned_at_full_depth_and_the_module_shallow(tmp_path: Path) -> None:
    """Data, not a constant: `genrev.cmake` reads the revision out of git history."""
    rec = Recorder(images=False)
    install(rec, tmp_path / "wow")
    core, module = rec.clones[0], rec.clones[1]
    assert core.depth is None
    assert module.depth == 1
    assert module.dest.name == "mod-playerbots"
    assert module.dest.parent.name == "modules"


# -- the import stage -------------------------------------------------------


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
    state = native.read_state(server_dir)
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


def test_the_cancel_note_is_said_before_the_build_not_after_it(tmp_path: Path) -> None:
    rec = Recorder(images=False)
    lines = install(rec, tmp_path / "wow")
    assert native.CANCEL_NOTE in lines
    assert lines.index(native.CANCEL_NOTE) < next(
        index for index, line in enumerate(lines) if line == "--- build"
    )


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
    assert native.read_state(server_dir) is None


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
    state = native.read_state(server_dir)
    assert state is not None
    assert state.completed == ("build",)


# -- provisioning and readiness --------------------------------------------


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
