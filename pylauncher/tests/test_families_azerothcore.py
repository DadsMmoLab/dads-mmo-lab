"""Tests for the AzerothCore family (`yulon.catalog.families.azerothcore`, roadmap 7.1).

The tests of `test_native.py` (roadmap 6.2), re-homed unchanged in intent when
the engine split into a game-free spine and a family: every one of them is
about WHAT the WotLK install does — its stages, its refusals, what a resume
repeats — and that is the family's contract. Spine-only behaviour is in
`test_spine.py`; the machine double is `tests/support_native.py`.

Every external effect is a seam, so a whole install runs here in milliseconds
with no daemon, no network and no four-hour build. Nothing below is evidence
that the engine installs a server — it is evidence about the engine's control
flow, its refusals, and what a resume does and does not repeat.

The platform is always injected through the `platform_id` seam. Faking
`sys.platform` instead mutates the real module for the whole process, which is
how this suite once went red on every Python 3.12+ Linux box while CI stayed
green (checklist, "CI was green while the suite was red").
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import fields, replace
from pathlib import Path

import pytest

from tests.support_native import (
    ENTRY,
    IMPORTED,
    PARTIAL,
    POPULATED_HALF,
    TBC,
    UNREADABLE,
    UPSTREAM_COMPOSE,
    Recorder,
    engine,
    install,
)
from yulon import docker, git, platform, resources, runner
from yulon.catalog import composegen, native, preflight
from yulon.catalog import families as family_registry
from yulon.catalog import installer as installer_module
from yulon.catalog.catalog import load_catalog
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.catalog.installer import (
    DockerNeedsReLoginError,
    DockerUnavailableError,
    InstallerError,
    InstallOptions,
    UnsupportedPlatformError,
    installer_for,
)

STAGE_NAMES = AzerothCoreInstaller.STAGE_NAMES


def without_cmangos(monkeypatch: pytest.MonkeyPatch) -> None:
    """The registry as it stood before K.8: `azerothcore` and nothing else.

    The test below is about what `installer_for()` does when a shipped entry
    names a family THIS BUILD has no engine for. That was the tree's real state
    for the four groups between G.4 (which landed the three CMaNGOS `native`
    blocks) and K.8 (which registered the class that reads them), and `wow-tbc`
    was the live example. K.8 registered it, and the example went away; the
    state did not, because the next lineage's catalog data will arrive ahead of
    its engine the same way. What F.3 changed is the ANSWER: it used to be a
    fallback to the entry's bash script, and it is now a refusal.

    Narrowing the REGISTRY rather than editing an entry is what keeps the
    fixture honest about which rule it violates: `NativeInstall.family` is a
    `Literal["azerothcore", "cmangos"]`, so an entry naming a third family is a
    state no `catalog.json` can reach, and a test built on one would be
    proving the branch against data the app cannot receive.
    """
    monkeypatch.setattr(family_registry, "FAMILIES", {"azerothcore": AzerothCoreInstaller})


def test_the_family_decides_which_engine_installs_and_linux_no_longer_keeps_the_script() -> None:
    """One place decides, from `catalog.json` data — now on `install.native` (7.1, A1).

    WotLK is native on every platform, Linux included: the flip is one JSON key.

    This also asserted that the three CMaNGOS entries took the SCRIPT path,
    which was true until K.8 registered `CmangosInstaller` and made it false in
    the same commit. Where those three go now is asserted in
    `test_families_cmangos.py`, beside the class that receives them, and the
    catalog-wide relationship — every native entry reaching the class its family
    id names — is `test_spine.py`'s, since it is true of every family. What is
    left here is the AzerothCore half, which is this file's subject.
    """
    assert isinstance(installer_for(ENTRY, platform_id=lambda: "linux"), AzerothCoreInstaller)
    assert isinstance(installer_for(ENTRY, platform_id=lambda: "macos"), AzerothCoreInstaller)
    assert isinstance(installer_for(ENTRY, platform_id=lambda: "windows"), AzerothCoreInstaller)


def test_no_engine_and_no_script_left_is_the_app_bug_family_for_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A family this build has no engine for is the app bug, not a fallback (F.3).

    Until F.3 such an entry was handed back to its bash script, so reaching the
    refusal took a `model_copy` that also emptied `Install.script`. F.3 deleted
    that engine, which leaves one answer for the state and it is the sentence
    `family_for()` already had — so the sentence is not written a second time
    here. The entry used below is the shipped one, unmodified. Until F.4 that
    mattered for a second reason — it still carried the `script` field the
    fallback used to read, so asserting the field's presence said the fallback
    was GONE rather than merely starved. F.4 deleted the field, and what makes
    the fixture honest now is that the REGISTRY, not the entry, is what
    `without_cmangos` narrows: the entry is real catalog data throughout.
    """
    without_cmangos(monkeypatch)
    tbc = load_catalog().get("wow-tbc")
    assert tbc.install.native is not None and tbc.install.native.family == "cmangos"
    with pytest.raises(InstallerError, match="install family this app does not have"):
        installer_for(tbc, platform_id=lambda: "linux")


def test_the_wotlk_entry_names_the_family_of_the_engine_that_installs_it() -> None:
    """The class attribute and the catalog datum agree, for this file's own family.

    Everything else the deleted `..._still_reads_as_scripted` test held was
    about the script path (`scripted_platforms`/`uses_script`/`is_native` on
    TBC), which F.4 removed. The catalog-wide version of the agreement below
    — every native entry reaching the class its family id names — is
    `test_spine.py`'s, because it is true of every family; this is the
    AzerothCore half, which is this file's subject.
    """
    assert ENTRY.install.native is not None
    assert ENTRY.install.native.family == "azerothcore"
    assert AzerothCoreInstaller.family == ENTRY.install.native.family


def test_the_unsupported_platform_refusal_still_comes_first(tmp_path: Path) -> None:
    rec = Recorder()
    installer = AzerothCoreInstaller(TBC, seams=rec.seams(platform_id=lambda: "macos"))
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
    assert real.wait_ready is docker.wait_ready_for
    # The three SELinux seams. Every SELinux test in `test_spine.py` fakes all
    # three, so they are only evidence about Fedora if these are what a real
    # install reaches for — and `selinux_enforcing` in particular must be the
    # tri-state probe, not something that answers `False` for "could not ask".
    assert real.relabel is platform.relabel_for_containers
    assert real.selinux_enforcing is platform.selinux_enforcing
    assert real.fs_type is platform.filesystem_type
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
        # multi-hour build — see
        # `test_the_database_is_started_before_the_import_is_asked_anything`,
        # which owns that claim. (The name cited here until 2026-09-02,
        # `test_the_import_cannot_be_asked_anything...`, has never existed;
        # an elided citation cannot be checked by eye, so it is spelled out.)
        "start-db",
        "probe",
        "one-shot:ac-db-import",
        "verify",
        "start",
        "query",
        "sql",
    ]
    assert "compiling" in lines  # the build's output is streamed, not buffered
    state = native.read_state(server_dir, valid=STAGE_NAMES)
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
    down, AND the stage tuple refuses to record them at all. Only the second
    half survives someone rearranging `run()` — the first would keep passing if
    a stage stopped being reached for an unrelated reason. `preflight` and
    `guard` are not stages any more: the spine owns them, so a family can
    neither forget them nor record them.
    """
    rec = Recorder(images=False)
    server_dir = tmp_path / "wow"
    install(rec, server_dir)
    state = native.read_state(server_dir, valid=STAGE_NAMES)
    assert state is not None
    assert not (set(state.completed) & {"start-db", "up", "ready"})
    assert "preflight" not in STAGE_NAMES and "guard" not in STAGE_NAMES
    by_name = {stage.name: stage for stage in engine(Recorder()).stages()}
    for name in ("start-db", "up", "ready"):
        assert by_name[name].recorded is False, f"{name} would be written down"
    assert by_name["build"].recorded is True


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
    # Recorded AND on disk, so the clone is a genuine no-op: no fetch, no reset,
    # nothing moved. It used to update here, which is the whole of D5.
    assert not any(call.startswith("clone:") for call in again.calls), again.calls


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


# -- what a resume does to the source it already cloned (D5) ----------------


def test_a_resume_runs_no_git_command_at_all_against_a_clone_it_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asserted on the ARGV git is handed, because the seam only proves intent.

    `reset --hard` is spelled `["reset", "--hard", "FETCH_HEAD"]` here, so a
    test that greps a log for the shell spelling of the command would watch the
    wrong thing entirely (`audit by argv, not by string`). The REAL
    `ContainerGit` is wired into the clone seam and every argv it would hand
    `runner.run` is collected instead.
    """
    server_dir = tmp_path / "wow"
    install(Recorder(images=False), server_dir)

    argvs: list[list[str]] = []

    def record(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        argvs.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(runner, "run", record)
    monkeypatch.setattr(platform, "docker_program", lambda: "docker")
    again = resumed(server_dir, images=True, probe_answers=[IMPORTED])
    install(again, server_dir, clone=git.ContainerGit().clone)
    tokens = [token for argv in argvs for token in argv]
    assert "fetch" not in tokens, argvs
    assert "reset" not in tokens, argvs
    assert argvs == [], argvs


def test_an_edit_to_the_cloned_source_survives_a_resume(tmp_path: Path) -> None:
    """`reset --hard` is not recoverable, so a resume must not be able to reach one.

    The double models what the update path really does — the working tree
    becomes the remote's — so a resume that runs it destroys the edit exactly
    as the live gate's would have.
    """
    server_dir = tmp_path / "wow"
    install(Recorder(images=False), server_dir)
    edited = server_dir / "src" / "server" / "worldserver.cpp"
    edited.parent.mkdir(parents=True)
    edited.write_text("// my patch\n", encoding="utf-8")

    def hard_reset(spec: git.CloneSpec) -> None:
        """What `git fetch && git reset --hard FETCH_HEAD` does to a tracked file."""
        for path in spec.dest.rglob("*.cpp"):
            path.write_text("// upstream\n", encoding="utf-8")

    again = resumed(server_dir, images=True, probe_answers=[IMPORTED])
    install(again, server_dir, clone=hard_reset)
    assert edited.read_text(encoding="utf-8") == "// my patch\n"


def test_a_recorded_clone_that_is_gone_from_disk_is_cloned_again(tmp_path: Path) -> None:
    """Recorded is half the predicate; the disk has to agree, or the state file rules alone.

    `InstallState.has()` is "never a reason to skip on its own" for a reason
    this project paid for once already, so the repair path has to still be
    reachable: the module directory is deleted between the two runs and the
    resume must clone it, while the core — recorded and still on disk — is left
    alone.
    """
    server_dir = tmp_path / "wow"
    install(Recorder(images=False), server_dir)
    module = server_dir / ENTRY.emulator.sources[1].dest
    shutil.rmtree(module)
    again = resumed(server_dir, images=True, probe_answers=[IMPORTED])
    del again.remotes[module]  # a directory that is not there answers nothing
    install(again, server_dir)
    assert [spec.dest for spec in again.clones] == [module]


def test_a_first_install_into_the_users_own_checkout_is_refused_and_left_alone(
    tmp_path: Path,
) -> None:
    """The path D5's fix did not close, and it needs no resume to reach it.

    `_guard()` exempts a directory that is a git checkout from the not-empty
    refusal so the clone stage can name whose it is — and the clone stage only
    ever named a checkout of a DIFFERENT repository. A user who points a first
    install at their own checkout of the same repo has no record on disk, so
    `already_cloned()` is False and the seam's update path runs: `git fetch` +
    `git reset --hard FETCH_HEAD` over their work.
    """
    server_dir = tmp_path / "wow"
    (server_dir / ".git").mkdir(parents=True)
    edited = server_dir / "src" / "server" / "worldserver.cpp"
    edited.parent.mkdir(parents=True)
    edited.write_text("// my patch\n", encoding="utf-8")
    rec = Recorder(images=False)
    rec.remotes[server_dir] = ENTRY.emulator.sources[0].url
    reset: list[Path] = []

    def hard_reset(spec: git.CloneSpec) -> None:
        """What the seam does to a destination that already has a `.git`."""
        reset.append(spec.dest)
        for path in spec.dest.rglob("*.cpp"):
            path.write_text("// upstream\n", encoding="utf-8")

    lines: list[str] = []
    with pytest.raises(InstallerError, match="no record here of an install this app made"):
        for line in engine(rec, clone=hard_reset).run(InstallOptions(server_dir=server_dir)):
            lines.append(line)
    assert reset == [], reset
    assert edited.read_text(encoding="utf-8") == "// my patch\n"
    # The sentence that used to sit two lines above the `reset --hard`.
    assert not any("part-way through" in line for line in lines), lines


def test_a_state_file_nobody_can_read_never_authorises_a_reset_of_a_users_checkout(
    tmp_path: Path,
) -> None:
    """The FOURTH round's defect, at the level where the harm happens.

    Two functions disagreed about what owning a folder means, and a corrupt file
    landed between them. `read_state()` answered `None` for a state file that
    would not parse, so `_guard()` treated the folder as FRESH and skipped every
    refusal it has; `claimed_this_folder()` answered
    `(server_dir / STATE_FILE).is_file()` — presence — so
    `refuse_unowned_checkout()` called the folder OURS and stood down; and
    `git fetch` + `git reset --hard FETCH_HEAD` ran over a user's own checkout.

    The repro is the adversarial reviewer's, and it needs no SELinux and no
    resume: clone the repo to `~/mywork`, edit a file, truncate
    `.yulon-install.json` to zero bytes, install into `~/mywork`. Commit
    `60d53374` hid this on enforcing boxes — the container could not read `.git`
    at all, so an earlier guard raised first — and `5c6c655c`, which made those
    reads work again, uncovered it.

    Asserted at the harm rather than at a flag: the reset seam is never reached
    and the edit is still there. `test_a_first_install_into_the_users_own_
    checkout_is_refused_and_left_alone` above is the missing-file half of the
    same claim, and a corrupt file must never be worth more than a missing one.
    """
    server_dir = tmp_path / "mywork"
    (server_dir / ".git").mkdir(parents=True)
    edited = server_dir / "src" / "server" / "worldserver.cpp"
    edited.parent.mkdir(parents=True)
    edited.write_text("// my patch\n", encoding="utf-8")
    (server_dir / native.STATE_FILE).write_text("", encoding="utf-8")
    rec = Recorder(images=False)
    rec.remotes[server_dir] = ENTRY.emulator.sources[0].url
    reset: list[Path] = []

    def hard_reset(spec: git.CloneSpec) -> None:
        reset.append(spec.dest)
        for path in spec.dest.rglob("*.cpp"):
            path.write_text("// upstream\n", encoding="utf-8")

    with pytest.raises(InstallerError, match="cannot read"):
        list(engine(rec, clone=hard_reset).run(InstallOptions(server_dir=server_dir)))
    assert reset == [], reset
    assert rec.clones == [], rec.clones
    # Nothing ran at all - no machine check, no clone, no build, no container.
    # A flag set to the right value would not be evidence of that; an empty call
    # log is. It read `["gather"]` until 2026-09-02, which recorded the defect as
    # if it were the contract: the machine was measured, and Docker provisioned
    # first, before this folder was judged - and the refusal then says `Nothing
    # was written` about a machine that had just had packages installed on it.
    assert rec.calls == [], rec.calls
    assert edited.read_text(encoding="utf-8") == "// my patch\n"
    assert not (server_dir / "docker-compose.yml").exists()


def test_the_part_way_through_sentence_is_only_said_where_it_is_true(tmp_path: Path) -> None:
    """It is said over a fetch+reset, so it must never be said about somebody else's work.

    True exactly when this install already owns the folder: a record is on disk,
    `_guard()` matched it on `install_id`, `game_id` and `family`, and the
    checkout inside it was therefore made by a run of this install that did not
    get as far as writing the stage down.
    """
    server_dir = tmp_path / "wow"
    install(Recorder(images=False), server_dir)
    state = native.read_state(server_dir, valid=STAGE_NAMES)
    assert state is not None
    native.write_state(server_dir, replace(state, completed=("generate-compose", "build")))
    again = resumed(server_dir, images=True, probe_answers=[IMPORTED])
    lines = install(again, server_dir)
    assert any("A previous run of this install left it part-way through" in x for x in lines), lines


def test_a_clone_on_disk_that_was_never_recorded_is_still_updated(tmp_path: Path) -> None:
    """The other half: a run interrupted DURING a clone recorded nothing, so it repairs.

    That is where `fetch` + `reset --hard` belongs and it must stay reachable —
    a half-checked-out tree is not something to build against.
    """
    server_dir = tmp_path / "wow"
    install(Recorder(images=False), server_dir)
    state = native.read_state(server_dir, valid=STAGE_NAMES)
    assert state is not None
    native.write_state(server_dir, replace(state, completed=("generate-compose", "build")))
    again = resumed(server_dir, images=True, probe_answers=[IMPORTED])
    install(again, server_dir)
    assert [spec.dest for spec in again.clones] == [
        server_dir / source.dest for source in ENTRY.emulator.sources
    ]


def test_a_resume_says_it_left_the_clone_alone_rather_than_claiming_to_update_it(
    tmp_path: Path,
) -> None:
    """The log is the only place a user can see which of the two happened."""
    server_dir = tmp_path / "wow"
    install(Recorder(images=False), server_dir)
    again = resumed(server_dir, images=True, probe_answers=[IMPORTED])
    lines = install(again, server_dir)
    assert any("leaving it exactly as it is" in line for line in lines), lines
    assert not any("updating it instead" in line for line in lines), lines


def test_a_resume_puts_the_compose_file_back_and_the_note_does_not_deny_it(
    tmp_path: Path,
) -> None:
    """`docker-compose.yml` is a TRACKED file of the emulator checkout — it is source.

    The repository ships it at its root, which is why `generate-compose` has a
    carve-out for it at all; after the first install it carries this engine's
    marker, so `composegen.write_plan()` rewrites it whenever its content
    differs. A comment appended to it does not survive a resume (driven,
    2026-08-31), so a banner promising the source is left as it is on disk was
    false about a file this engine rewrites every time.
    """
    server_dir = tmp_path / "wow"
    install(Recorder(images=False), server_dir)
    base = server_dir / composegen.BASE_FILE
    base.write_text(base.read_text(encoding="utf-8") + "# my comment\n", encoding="utf-8")
    again = resumed(server_dir, images=True, probe_answers=[IMPORTED])
    install(again, server_dir)
    assert "# my comment" not in base.read_text(encoding="utf-8")
    assert "left exactly as it is on disk" not in native.OPENING_NOTE
    assert "the compose files this app writes" in native.OPENING_NOTE


def test_the_note_does_not_say_nothing_is_written_outside_the_folder(tmp_path: Path) -> None:
    """The last clause of that sentence that named no stage responsible for keeping it.

    An install writes gigabytes into Docker's own storage — the images it
    builds and the two named volumes the database and the server data live in —
    which is the whole reason preflight has a check about Docker's disk rather
    than this one. And on Linux the lines immediately UNDER the note are
    `apt-get`, `systemctl` and `usermod`. A reassurance printed three lines
    above the actions contradicting it is the same shape as the sentence
    removed from the clone stage.
    """
    report = platform.ProvisionReport(
        "linux",
        done=(
            "apt-get update",
            "apt-get install -y docker.io",
            "systemctl enable --now docker",
            "usermod -aG docker pk",
        ),
        docker_ready=True,
        docker_group="granted",
    )
    answers = iter([False, True])
    server_dir = tmp_path / "wow"
    lines = list(
        engine(
            Recorder(images=False),
            docker_ready=lambda: next(answers, True),
            ensure_docker=lambda **_kwargs: report,
        ).run(InstallOptions(server_dir=server_dir))
    )
    note_at = lines.index(native.OPENING_NOTE)
    outside = [
        line
        for line in lines
        if any(word in line for word in ("apt-get", "systemctl enable", "usermod"))
    ]
    assert outside and lines.index(outside[0]) > note_at, lines
    assert any("free space on Docker's disk" in line for line in lines), lines
    # And the data really does live outside the folder: two NAMED volumes.
    rendered = (server_dir / composegen.BASE_FILE).read_text(encoding="utf-8")
    assert "\nvolumes:\n" in rendered and "db-data:" in rendered and "client-data:" in rendered

    assert "Nothing is written outside the folder" not in native.OPENING_NOTE
    for named in ("Docker's own storage", "Docker itself", "config folder"):
        assert named in native.OPENING_NOTE, native.OPENING_NOTE


def test_the_note_does_not_call_the_repeated_steps_the_last_ones(tmp_path: Path) -> None:
    """`start-db` sits mid-list and `client-data` consults no state at all.

    Both really run on a resume of a finished install, so "only the last steps
    — starting the server and waiting for it — run on every attempt" named two
    of the four. The note has now moved three times; this is what pins it.
    """
    server_dir = tmp_path / "wow"
    install(Recorder(images=False), server_dir)
    again = resumed(server_dir, images=True, probe_answers=[IMPORTED])
    install(again, server_dir)
    assert "start-db" in again.calls, again.calls
    assert f"one-shot:{ENTRY.containers.client_data}" in again.calls, again.calls
    assert "Only the last steps" not in native.OPENING_NOTE
    for said in ("the server-data download", "the database and server are started"):
        assert said in native.OPENING_NOTE, native.OPENING_NOTE


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


def test_an_enforcing_box_still_recognises_a_users_own_checkout_of_this_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The refusal that names the repository must survive SELinux, and it did not.

    This is the level the harm happens at, so the argv is not stated here — the
    real `ContainerGit.remote_url()` runs, against a `runner.run` that behaves
    the way Fedora 44 Enforcing was measured to behave (2026-08-30): a bind
    mount of an unlabelled folder is invisible to a confined container, and the
    SAME command with `--security-opt label:disable` answers. Only the machine
    is stated, through the two seams `ContainerGit` has for exactly that; the
    seam wired in is `native.Seams.remote_url`'s own default body,
    `ContainerGit().remote_url`.

    What the denial looks like is why three review seats read past it. The
    container cannot see `.git` at all, so git says "fatal: not a git repository"
    rather than anything about permissions; `remote_url()` catches the `GitError`
    and returns `None`; and `None` is what a folder with no checkout in it
    answers. So on every enforcing box a user's own checkout stopped being a
    checkout of a NAMED repository and became an unreadable one — the refusal
    that fired said git would not say what this is and told the user to pick an
    empty folder, about a machine that could have answered perfectly well, to a
    user whose remedy is now to delete their work.

    Take `--security-opt label:disable` out of `git._capture()` and this goes
    red on the message: the "would not say what it is a checkout of" refusal
    fires instead of the one that names the repository and the missing record.
    """
    core_url = ENTRY.emulator.sources[0].url
    server_dir = tmp_path / "wow"
    (server_dir / ".git").mkdir(parents=True)
    (server_dir / "my-own-patches.cpp").write_text("mine", encoding="utf-8")

    def enforcing_selinux(
        argv: list[str], cwd: Path | None = None, env: object = None, **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        unconfined = "--security-opt" in argv and argv[argv.index("--security-opt") + 1] == (
            "label:disable"
        )
        if unconfined:
            return subprocess.CompletedProcess(argv, 0, f"{core_url}\n", "")
        # Not "permission denied": with the mount invisible, git reports that
        # the directory is not a repository at all.
        return subprocess.CompletedProcess(
            argv, 128, "", "fatal: not a git repository (or any parent up to mount point /)"
        )

    monkeypatch.setattr(runner, "run", enforcing_selinux)
    monkeypatch.setattr(git.platform, "docker_program", lambda: "docker")
    fedora = git.ContainerGit(
        selinux_enforcing=lambda: True, filesystem_type=lambda _path: "ext2/ext3"
    )

    rec = Recorder()
    with pytest.raises(InstallerError) as refusal:
        install(rec, server_dir, remote_url=fedora.remote_url)
    said = str(refusal.value)
    assert "there is no record here of an install this app made" in said, said
    assert core_url in said, said
    assert "would not say what it is a checkout of" not in said, said
    assert not rec.clones
    assert (server_dir / "my-own-patches.cpp").read_text(encoding="utf-8") == "mine"


def test_the_same_repository_spelled_differently_is_not_a_conflict(tmp_path: Path) -> None:
    """`...y.git`, `...y` and `git@github.com:x/y` are one repository.

    Asserted on a resume rather than a first press, because a checkout in a
    folder with no record of an install this app made is now refused whatever
    its origin says (`refuse_unowned_checkout()`). `_same_repo()` is compared
    BEFORE the record is consulted, so this still exercises the spelling rule:
    a refusal about punctuation would fire here first.
    """
    server_dir = tmp_path / "wow"
    install(Recorder(images=False), server_dir)
    again = resumed(server_dir, images=True, probe_answers=[IMPORTED])
    again.remotes[server_dir] = ENTRY.emulator.sources[0].url.removesuffix(".git")
    lines = install(again, server_dir)
    assert any("leaving it exactly as it is" in line for line in lines), lines


def _already_cloned(rec: Recorder, server_dir: Path) -> None:
    """Put the core checkout on disk the way `clone-core` really leaves it.

    `.git`, an origin git will answer for, the `docker-compose.yml` the
    repository ships — a module test that skipped that one got refused three
    stages earlier for a reason that had nothing to do with modules — AND the
    state file recording the stage. The record is not decoration: a checkout in
    a folder holding no record of an install this app made is refused by name
    now (`refuse_unowned_checkout()`), so a helper that left it out would be
    modelling a user's own checkout rather than this engine's own work.
    """
    (server_dir / ".git").mkdir(parents=True, exist_ok=True)
    rec.remotes[server_dir] = ENTRY.emulator.sources[0].url
    path = server_dir / composegen.BASE_FILE
    path.write_text(UPSTREAM_COMPOSE, encoding="utf-8")
    rec.tracked[path] = UPSTREAM_COMPOSE
    native.write_state(
        server_dir,
        native.InstallState(
            game_id=ENTRY.id,
            install_id=composegen.install_id(server_dir, platform_id=lambda: "macos"),
            family="azerothcore",
            completed=("clone-core",),
        ),
    )


def test_a_module_directory_holding_another_repository_is_refused_too(tmp_path: Path) -> None:
    """The same rule one level down: `modules/mod-playerbots` may be somebody's fork."""
    server_dir = tmp_path / "wow"
    module = server_dir / "modules" / "mod-playerbots"
    (module / ".git").mkdir(parents=True)
    rec = Recorder(images=False)
    _already_cloned(rec, server_dir)
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
    state = native.read_state(server_dir, valid=STAGE_NAMES)
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
    """`CallableGate.reset()`'s own refusal, and it must arrive whole.

    Asserted by EQUALITY, not by a phrase. `stage_import()` translates anything
    else `reset()` raises into a sentence of its own (2026-09-02), and an
    `InstallerError` caught by that translation would come back wrapped inside
    it - two refusals in one line, the second explaining the first. `match=`
    alone could not see that, because the wrapper interpolates the message it
    wrapped and the phrase survives inside it.
    """
    rec = Recorder(images=False, probe_answers=[PARTIAL])
    installer = AzerothCoreInstaller(
        ENTRY,
        installers_root=resources.installers_dir(),
        import_probe=rec.probe,
        reset_unfinished=None,
        seams=rec.seams(),
    )
    with pytest.raises(InstallerError) as refused:
        list(installer.run(InstallOptions(server_dir=tmp_path / "wow")))
    assert str(refused.value) == (
        "This install's databases were left half-written and this installer has no way "
        "to clear them, so nothing was run."
    )


def test_an_engine_with_no_probe_at_all_refuses_before_anything_runs(tmp_path: Path) -> None:
    """An installer that cannot ask what the databases hold must not write to them."""
    rec = Recorder()
    installer = AzerothCoreInstaller(ENTRY, import_probe=None, seams=rec.seams())
    with pytest.raises(InstallerError, match="built without a way to check"):
        list(installer.run(InstallOptions(server_dir=tmp_path / "wow")))
    assert rec.calls == []


# -- failure, cancel and the state file -------------------------------------


def test_a_failed_stage_records_nothing_so_it_runs_again(tmp_path: Path) -> None:
    rec = Recorder(images=False, build_result=docker.AttachedRun(1, ("no space left on device",)))
    server_dir = tmp_path / "wow"
    with pytest.raises(InstallerError, match="no space left"):
        install(rec, server_dir)
    state = native.read_state(server_dir, valid=STAGE_NAMES)
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
    assert native.read_state(server_dir, valid=STAGE_NAMES) is None


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
    state = native.read_state(server_dir, valid=STAGE_NAMES)
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


def _provisioned(**overrides: object) -> platform.ProvisionReport:
    """The live gate's press 1: the engine installed, the group joined, no daemon yet.

    `docker.io` 29.1.3 active and `pk` added to group 124 (Ubuntu gate,
    2026-08-30) — and `docker_ready` still False, because `usermod` cannot
    change the supplementary groups of a process that is already running.
    """
    fields_: dict[str, object] = {
        "done": ("apt-get install -y docker.io", "usermod -aG docker pk"),
        "manual_steps": (platform.DOCKER_GROUP_RELOGIN_STEP.format(user="pk"),),
        "docker_ready": False,
        "docker_group": "granted",
    }
    fields_.update(overrides)
    return platform.ProvisionReport("linux", **fields_)  # type: ignore[arg-type]


def test_a_provision_that_worked_and_only_needs_a_re_login_is_not_a_failure(
    tmp_path: Path,
) -> None:
    """D1: the gate's first press did everything right and was reported as "could not".

    Two different states with two different next actions — "Docker could not be
    set up" and "Docker is set up and your account needs a new login" — and the
    user reads the first sentence, not the remedy under it.
    """
    installer = engine(
        Recorder(images=False),
        docker_ready=lambda: False,
        ensure_docker=lambda **_kwargs: _provisioned(),
    )
    with pytest.raises(DockerNeedsReLoginError) as caught:
        installer.preflight(InstallOptions(server_dir=tmp_path / "srv"))
    message = str(caught.value)
    assert "could not be set up" not in message, message
    assert "log out and back in" in message.lower(), message


def test_the_press_after_the_group_join_is_not_told_docker_could_not_be_set_up(
    tmp_path: Path,
) -> None:
    """Press 2 on the gate box, and the state a real Linux user is in most often.

    `_docker_group_member()` asks `id -nG <user>`, which reads the group
    DATABASE - so the moment the join lands the user is a member by that
    question, while the running session still is not by the kernel's. Press 2
    therefore reports `already-member` with the daemon still unreachable, which
    `platform.DockerGroupOutcome`'s own docstring already put on the same side
    as `granted`: "only `granted` and `already-member` ... may print the
    log-out-and-back-in line". `docker_unavailable()` read only `granted`, so
    press 2 got D1's sentence back, with no manual steps to soften it.
    """
    installer = engine(
        Recorder(images=False),
        docker_ready=lambda: False,
        ensure_docker=lambda **_kwargs: platform.ProvisionReport(
            "linux",
            done=("apt-get install -y docker.io",),
            docker_group="already-member",
        ),
    )
    with pytest.raises(DockerNeedsReLoginError) as caught:
        installer.preflight(InstallOptions(server_dir=tmp_path / "srv"))
    message = str(caught.value)
    assert "could not be set up" not in message, message
    assert "Install Docker, start it, and try again." not in message, message
    assert "log out and back in" in message.lower(), message


def test_the_two_re_login_outcomes_do_not_read_as_one_sentence(tmp_path: Path) -> None:
    """Joined just now and already a member need different next actions.

    `granted` knows why this session cannot see the group. `already-member`
    does not: either the user has not logged out since, or the Docker service
    is down - and the message has to name both, because nothing on the report
    tells them apart.
    """
    seen = {
        outcome: str(
            installer_module.docker_unavailable(
                platform.ProvisionReport("linux", docker_group=outcome)
            )
        )
        for outcome in ("granted", "already-member")
    }
    assert len(set(seen.values())) == 2, seen
    assert "service is not running" in seen["already-member"]
    assert "service is not running" not in seen["granted"]


def test_a_provision_that_really_failed_still_says_so(tmp_path: Path) -> None:
    """The other half of the distinction: a join that did not run is not a success."""
    installer = engine(
        Recorder(images=False),
        docker_ready=lambda: False,
        ensure_docker=lambda **_kwargs: _provisioned(
            done=(),
            manual_steps=(platform.DOCKER_GROUP_JOIN_FAILED_STEP.format(user="pk"),),
            docker_group="join-failed",
        ),
    )
    with pytest.raises(DockerUnavailableError) as caught:
        installer.preflight(InstallOptions(server_dir=tmp_path / "srv"))
    assert not isinstance(caught.value, DockerNeedsReLoginError)
    assert "could not be set up automatically" in str(caught.value)


def test_a_successful_provision_still_says_what_it_did_and_what_is_left(tmp_path: Path) -> None:
    """The same root cause seen from the success side: the report was read only on failure.

    A user who has just granted the docker-group join, on a box where the
    daemon answers anyway, was never shown the log-out step at all — the one
    thing standing between them and a working install.
    """
    answers = iter([False, True])
    lines = list(
        engine(
            Recorder(images=False),
            docker_ready=lambda: next(answers, True),
            ensure_docker=lambda **_kwargs: _provisioned(docker_ready=True),
        ).run(InstallOptions(server_dir=tmp_path / "wow"))
    )
    assert any("usermod -aG docker pk" in line for line in lines), lines
    assert any("log out and back in" in line.lower() for line in lines), lines


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


def test_the_prompter_reaches_provisioning_and_nothing_else(tmp_path: Path) -> None:
    """The 7.1 flip of the 6.2 pin: `ask` is forwarded to exactly one consent seam.

    Two questions pass THROUGH the engine, both inside `ensure_docker()` and
    before stage 1 — the docker-group consent and, on Linux, the sudo password.
    The engine itself still asks nothing: the prompter below raises if any
    stage calls it, and a whole install runs with it attached.

    Until this flip the 6.2 pin asserted `ask is None` at this seam, which made
    the whole consent path unreachable from the Install button: `ensure_docker`
    declines the docker-group join whenever there is nobody to ask, so a native
    Linux install could never join the group however the user answered.
    """
    seen: list[dict[str, object]] = []

    def provision(**kwargs: object) -> platform.ProvisionReport:
        seen.append(dict(kwargs))
        return platform.ProvisionReport("linux", docker_ready=True)

    def prompter(_question: str) -> str:
        raise AssertionError("the engine asked the user something on its own behalf")

    rec = Recorder(images=False)
    installer = engine(rec, docker_ready=lambda: False, ensure_docker=provision)
    installer.preflight(InstallOptions(server_dir=tmp_path / "srv"), ask=prompter)
    assert seen, "ensure_docker was never reached"
    assert seen[0].get("ask") is prompter

    seen.clear()
    lines = list(installer.run(InstallOptions(server_dir=tmp_path / "srv"), ask=prompter))
    assert seen[0].get("ask") is prompter
    assert "start" in rec.calls  # the whole install ran; the prompter was never called
    assert lines[-1].endswith(str(tmp_path / "srv"))


def test_provisioning_is_the_only_seam_the_prompter_is_handed_to(tmp_path: Path) -> None:
    """The negative half of the rule, asserted over every seam rather than one.

    The test above proves the wire exists and that no stage CALLS the prompter.
    Neither catches the failure this rule is really about: a second seam being
    handed `ask` and asking later, off this call stack — `gather()` growing a
    "may I probe your ports with sudo?" question, say. So every callable on
    `Seams` is wrapped, a whole install runs, and the prompter is required to
    appear in exactly one seam's arguments.

    Written as an argv-shaped check rather than a grep: the same forwarding
    spelled `ask=ask`, `**kwargs` or a partial is one object arriving at one
    seam, and only the seam can see all three.

    The wrapper below assumes every non-`None` `Seams` field is CALLABLE, and
    that assumption is now asserted rather than relied on. `Seams` grows a field
    per merge; a data one — a Path, a timeout, a flag — would be silently
    replaced by a closure here and break its consumer somewhere else entirely,
    with this test still green. It has to fail at the field, by name.
    """
    handed: list[str] = []

    def watched(name: str, seam: Callable[..., object]) -> Callable[..., object]:
        def spy(*args: object, **kwargs: object) -> object:
            if any(arg is prompter for arg in (*args, *kwargs.values())):
                handed.append(name)
            return seam(*args, **kwargs)

        return spy

    def provision(**_kwargs: object) -> platform.ProvisionReport:
        return platform.ProvisionReport("linux", docker_ready=True)

    def prompter(_question: str) -> str:
        raise AssertionError("the engine asked the user something on its own behalf")

    rec = Recorder(images=False)
    installer = engine(rec, docker_ready=lambda: False, ensure_docker=provision)
    for seam_field in fields(native.Seams):
        original = getattr(installer._seams, seam_field.name)
        if original is not None:
            assert callable(original), (
                f"Seams.{seam_field.name} is not callable, so wrapping it here would hand the "
                "engine a closure where it expects data — and this test would stay green while "
                "something far away broke. Teach the wrapper about it instead."
            )
            setattr(installer._seams, seam_field.name, watched(seam_field.name, original))

    list(installer.run(InstallOptions(server_dir=tmp_path / "srv"), ask=prompter))
    assert handed == ["ensure_docker"], f"the prompter reached {handed}"


def test_the_reason_provisioning_gave_survives_the_trip_back_up(tmp_path: Path) -> None:
    """Six ways of not joining the docker group, six sentences — still six up here.

    `ensure_docker()` keeps its outcomes apart on purpose (`DockerGroupOutcome`
    is six values, and each one that leaves the user with something to do says
    so in its own `manual_steps` line). Forwarding `ask` is only worth anything
    if that distinction survives `_preflight_lines`: a spine that answered every
    unready daemon with one house sentence would throw the reasons away exactly
    when the user needs to read them.
    """
    messages: list[str] = []
    for step in (
        "You said no to the docker group, so run docker with sudo.",
        "Nobody was there to ask about the docker group.",
        "You said yes but the group join did not run.",
    ):

        def provision(step: str = step, **_kwargs: object) -> platform.ProvisionReport:
            return platform.ProvisionReport("linux", manual_steps=(step,))

        rec = Recorder(images=False)
        installer = engine(rec, docker_ready=lambda: False, ensure_docker=provision)
        with pytest.raises(DockerUnavailableError) as caught:
            installer.preflight(InstallOptions(server_dir=tmp_path / "srv"))
        assert step in str(caught.value)
        messages.append(str(caught.value))

    assert len(set(messages)) == 3, "three provisioning outcomes read as one message"


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
        "query",
        "sql",
    ]
    assert any("The server is up." in line for line in lines)


def test_macos_native_installer_handles_db_healthy_timeout(tmp_path: Path) -> None:
    """If the database never reaches healthy during ready stage, fail with log advice."""
    rec = Recorder(images=False, db_healthy=False)
    with pytest.raises(InstallerError, match="The database never reported healthy"):
        install(rec, tmp_path / "wow-macos-db-unhealthy")


# -- the stage names, pinned -------------------------------------------------


def test_wotlk_stage_names_are_the_historical_tuple() -> None:
    """A state file written by the 6.3 Windows partial install (2026-08-25) exists and must read.

    Renaming a stage reinterprets every `.yulon-install.json` in the wild —
    a resume would redo, or worse skip, work under a name it no longer knows.
    The CMaNGOS family (7.3), which has no state files anywhere, is free to
    pick its own names; this tuple is not.
    """
    assert AzerothCoreInstaller.STAGE_NAMES == (
        "clone-core",
        "clone-modules",
        "generate-compose",
        "build",
        "client-data",
        "start-db",
        "import",
        "up",
        "ready",
    )
    assert engine(Recorder()).stage_names() == AzerothCoreInstaller.STAGE_NAMES
    assert len(set(AzerothCoreInstaller.STAGE_NAMES)) == len(AzerothCoreInstaller.STAGE_NAMES)


def test_a_folder_this_app_filled_and_then_failed_in_is_still_its_own_on_the_retry(
    tmp_path: Path,
) -> None:
    """The retry after a crash during stage one, which used to be refused outright.

    `_run_one()` writes the state file only after a stage FINISHES, so anything
    that ends the process mid-stage-one — a crash, a power cut, a killed
    terminal — left a folder holding `src/` and no record. `_guard()` then
    refused it with "is not empty and was not created by this app", which was
    false: this app had written every byte in it, and the only route out was to
    delete a part-finished multi-gigabyte clone by hand.

    Driven, not reasoned: the TBC-on-Windows gate was killed mid-clone on
    `yulon-win11` (2026-09-03) and refused its own 162 MB checkout on the next
    attempt.

    Two halves, and the second is what makes the fix narrow rather than
    convenient. `_claim_if_ours()` records the state only when the guard
    accepted the folder EMPTY, because that is the one moment "did we fill
    this" can be answered — and the neighbouring tests require an install into
    the user's own checkout to leave that checkout untouched.

    **What the fixture leaves behind is the whole test, and the first version
    got it wrong.** It wrote a plain `src/half-a-checkout` and then asserted
    only that one sentence was ABSENT from the retry's error. A review pointed
    out that any other refusal satisfies that, and this is the case: `git clone`
    creates `.git` in its destination in its first moments, so a killed clone
    leaves a PARTIAL CHECKOUT, and that is what the retry has to get past. The
    fixture below leaves one, and what is asserted is positive — the clone was
    attempted a second time.
    """
    server_dir = tmp_path / "wow"
    rec = Recorder()
    attempts: list[int] = []
    url = "https://github.com/mod-playerbots/azerothcore-wotlk.git"

    # Stage one gets as far as a partial checkout and then the process dies —
    # what a killed `git clone` really leaves, `.git` and all.
    def clone_then_die(*_args: object, **_kwargs: object) -> object:
        attempts.append(1)
        (server_dir / ".git").mkdir(parents=True, exist_ok=True)
        (server_dir / "half-a-checkout").write_bytes(b"x")
        raise InstallerError("killed mid-clone")

    with pytest.raises(InstallerError):
        install(rec, server_dir, clone=clone_then_die, remote_url=lambda _dest: url)

    assert (server_dir / "half-a-checkout").is_file(), "the fixture wrote nothing"
    assert (server_dir / native.STATE_FILE).is_file(), (
        "the folder this app filled was left with no record of the install, so the retry is "
        "refused as somebody else's directory"
    )
    assert attempts == [1], "the first run did not reach the clone at all"

    # The record names THIS folder. A claim written under any other install id
    # is a claim the guard refuses on the very next run ("looks like a copy of
    # another install"), which is the same dead end wearing a different
    # sentence. Asserted as a VALUE rather than left to the retry to reveal:
    # `_record_error()` runs one line after `_claim_if_ours()` and rewrites the
    # file from the same `state` object, so a wrong id written inside the claim
    # is erased before anything can observe it — the mask is real, and it is
    # not a reason for this file to have no opinion about what was claimed.
    claimed = native.read_state(server_dir, valid=("clone-core",))
    assert claimed is not None, "the claim did not parse"
    assert claimed.install_id == composegen.install_id(
        server_dir, platform_id=lambda: "macos"
    ), "the folder was claimed for a different install id"

    # And the retry REACHES THE CLONE. Asserted positively, because the absence
    # of one sentence is not the presence of progress: a review claimed the
    # folder under a stranger's install id, which produces a DIFFERENT refusal
    # ("copy of another install"), leaves the user in exactly the dead end this
    # test is named for, and passed the old assertion. What the fix has to buy
    # is a second attempt at the work, so that is what is asserted; the wording
    # check stays as the specific regression it was.
    with pytest.raises(InstallerError) as again:
        install(rec, server_dir, clone=clone_then_die, remote_url=lambda _dest: url)
    assert attempts == [
        1,
        1,
    ], "the retry never got as far as the clone; something ahead of it refused the folder: " + str(
        again.value
    )
    assert "killed mid-clone" in str(
        again.value
    ), "the second refusal is not the clone's own: " + str(again.value)
    assert "was not created by this app" not in str(again.value), str(again.value)


def test_a_claimed_folder_whose_leftovers_are_not_a_checkout_is_still_refused(
    tmp_path: Path,
) -> None:
    """The half the ownership record does NOT buy back, measured rather than assumed.

    `_claim_if_ours()` teaches `_guard()` that the folder is this install's. It
    teaches `stage_clone_sources()` nothing: that stage asks a different
    question — is there a `.git` here — and a destination with files and no
    `.git` is refused, because the clone seam `shutil.rmtree`s a destination it
    does not recognise and a tree somebody unpacked by hand must not fall
    through (review, 2026-08-23).

    So a first stage that wrote non-git files and died leaves the user with a
    second, narrower refusal instead of the first one. It is honest — it names
    the directory and it does not claim the app did not write it — and it is
    still a manual delete. Written down here because the fix's own test now
    passes, and without this the fix would read as complete.

    The two guards disagreeing about who owns the folder is bug §38's open
    design question, and the answer is the owner's: ownership recorded before
    the first mutating stage would let the clone stage consult it, which is a
    change across the spine and the families rather than a patch.
    """
    server_dir = tmp_path / "wow"
    rec = Recorder()

    def write_then_die(*_args: object, **_kwargs: object) -> object:
        (server_dir / "src").mkdir(parents=True, exist_ok=True)
        (server_dir / "src" / "not-a-checkout").write_bytes(b"x")
        raise InstallerError("killed before git made .git")

    with pytest.raises(InstallerError):
        install(rec, server_dir, clone=write_then_die)
    assert (server_dir / native.STATE_FILE).is_file(), "the folder was not claimed"

    with pytest.raises(InstallerError) as again:
        install(rec, server_dir, clone=write_then_die)
    message = str(again.value)
    assert "has files in it but is not a checkout" in message, message
    assert "was not created by this app" not in message, (
        "the ownership record is doing its job; the remaining refusal must not be the one "
        "that asserts the app did not write these bytes: " + message
    )
