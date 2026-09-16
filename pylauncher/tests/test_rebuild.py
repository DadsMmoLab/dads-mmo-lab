"""The rebuild control: the action `_format_report` has always named and never offered.

The Modules tab prints "worldserver REBUILD required before this takes effect"
after every module install whose manifest sets `build.rebuild` — 20 of the 41
shipped manifests, all of them `module`. Until this file there was no button
anywhere in `yulon/ui/` that started one, so those twenty entries could not work
at all on a server that was already built, and the sentence named an action the
app did not have.

Re-pressing Install was not it, and that is very probably what the user who
reported this meant by "i used the this thing to rebuild the server but nothing
changes when i log back in" (2026-09-07): `stage_build()` skips the compile when
the state file records a build AND the daemon holds every image, and the image
tag is derived from the FOLDER, so adding a module never changes it. Measured on
yulon-ubuntu through the app's own predicates: `state.has("build")` True,
`built_images()` True, `build_would_be_skipped()` True.

So the rebuild is not "run the install again". It is the build stage with the
skip rule switched off, the containers recreated from the image it produces, and
the readiness wait the install ends with — and the tests below are about the
force, the refusals, the cancel, and the fact that the progress a user watches is
the installer's own, not a second reporting scheme that can drift from it.

What is unit-tested only is stated in the report and in
`test_the_recreate_argv_does_not_rely_on_compose_noticing_a_new_image`: no
rebuild was run for this change.
"""

from __future__ import annotations

import re
import threading
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from tests.support_native import ENTRY, Recorder, engine, install
from tests.test_families_cmangos import ENTRY as CM_ENTRY
from tests.test_families_cmangos import client_folder
from tests.test_families_cmangos import engine as cm_engine
from tests.test_families_cmangos import install as cm_install
from yulon import docker
from yulon.apply import CLONE_DIRS, Applier
from yulon.catalog import composegen, native
from yulon.catalog.catalog import load_catalog
from yulon.catalog.families import dockerfile
from yulon.catalog.families.cmangos import CmangosInstaller
from yulon.catalog.installer import (
    InstallerError,
    InstallOptions,
    installer_for,
    rebuild_confirmation,
)

PYPLAN = Path(__file__).resolve().parents[2] / "pyplan"
RENDERED = Path(__file__).resolve().parent / "data" / "wotlk-rendered"


def a_finished_install(rec: Recorder, tmp_path: Path) -> Path:
    """A server dir in the state a real install leaves it in, made by the real install.

    Not a hand-built fixture: the whole question this feature turns on is what
    a FINISHED install looks like to the build stage's skip rule, and a fixture
    that laid its own state file and its own compose files would be free to lay
    the state that makes the test pass. `install()` runs all nine stages
    through the machine double, so the state file, the three compose files and
    their markers are the engine's own output.
    """
    server_dir = tmp_path / "wow"
    install(rec, server_dir)
    rec.calls.clear()
    return server_dir


# -- the force ---------------------------------------------------------------


def test_a_rebuild_compiles_a_server_whose_images_are_all_already_there(
    tmp_path: Path,
) -> None:
    """THE test for requirement 2. If the force is lost, this is what goes red.

    `images=True` plus a recorded `build` is exactly the state
    `build_would_be_skipped()` answers True for, and it is the state every
    already-installed server is in. A rebuild that honoured the skip would print
    "The server is already built; skipping the compile." and reproduce the
    defect it exists to fix, so both halves are asserted: the compile RAN, and
    the skip sentence was not said.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    said = list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert "build" in rec.calls, f"the rebuild skipped the compile: {rec.calls}"
    assert not any("skipping the compile" in line for line in said), said


@pytest.mark.parametrize("images", [True, False, None], ids=("present", "gone", "unknown"))
@pytest.mark.parametrize("recorded", [True, False], ids=("recorded", "fresh"))
def test_a_forced_context_is_never_a_skip_in_any_of_the_six_states(
    tmp_path: Path, recorded: bool, images: bool | None
) -> None:
    """The predicate and the stage, driven together over every state, forced.

    `test_build_would_be_skipped_answers_exactly_what_stage_build_then_does`
    (test_spine.py) does this for the unforced context and is the reason that
    predicate can be trusted by `CmangosInstaller._patch_sources()`. The force
    adds a seventh input to the same rule, and a force honoured by the stage but
    not by the predicate would make that refusal fire on a press that is about
    to compile — so the two are driven together here as well.
    """
    rec = Recorder(images=images)
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    installer = engine(rec)
    ctx = native.StageContext(
        server_dir=server_dir,
        client_dir=None,
        state=native.InstallState(
            ENTRY.id, "abc", "azerothcore", completed=("build",) if recorded else ()
        ),
        cancel=None,
        secrets=native.Secrets(db_password="pw"),
        force_build=True,
    )
    assert installer.build_would_be_skipped(ctx) is False
    said = list(installer.stage_build(ctx))
    assert "build" in rec.calls
    assert not any("skipping the compile" in line for line in said), said


def test_the_forced_compile_says_why_it_is_compiling_something_already_built(
    tmp_path: Path,
) -> None:
    """A build that looks unnecessary must say it was asked for, not just start.

    Without this the panel shows an hour of compiler output with nothing above
    it explaining why a finished server is being compiled, which is the shape
    of a bug rather than of a deliberate action.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    said = list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert any("asked for" in line and "already built" in line for line in said), said
    # And the build's own opening line stops saying "on a first install", which
    # is the wrong half of the truth for a press that is deliberately rebuilding
    # a finished server.
    building = [line for line in said if line.startswith("Building the server.")]
    assert building and "first install" not in building[0], building


# -- modules cloned since the last build --------------------------------------


def test_a_module_cloned_after_the_build_is_inside_what_the_build_overlay_compiles() -> None:
    """Requirement 3, asserted against the real overlay rather than assumed.

    A module is cloned to `<server_dir>/modules/<id>` (`apply.CLONE_DIRS`), and
    AzerothCore compiles modules INTO the worldserver binary. So "the rebuild
    picks up a module cloned since the last build" is true only if the build
    context the overlay declares CONTAINS that folder. The overlay in
    `tests/data/wotlk-rendered/` is the file the engine renders, compared field
    by field against a real install's `docker compose config` by
    `test_compose_fixture.py`, so it is the right thing to read this off.

    Every service is checked, not the worldserver alone: a context of `.` on
    three targets and something else on the fourth would still leave one image
    built from a tree without the module in it.
    """
    overlay = yaml.safe_load((RENDERED / composegen.BUILD_FILE).read_text(encoding="utf-8"))
    services = overlay["services"]
    assert services, "the build overlay declares no services at all"
    for name, service in services.items():
        context = service["build"]["context"]
        assert context == ".", f"{name} builds from {context!r}, not from the server dir"
    # The other half of the sentence: what `.` resolves to is the directory the
    # clone lands in. `build_staged()` runs compose with `cwd=server_dir`.
    assert CLONE_DIRS["module"] == "modules"
    assert Applier(Path("/srv"), git=None).clone_dir(  # type: ignore[arg-type]
        _a_module_manifest()
    ) == Path("/srv/modules/mod-ah-bot")


def _a_module_manifest() -> object:
    from yulon.controller_wow_wotlk import modules as wotlk_modules

    return wotlk_modules.store().load("module", "mod-ah-bot")


def test_the_rebuild_builds_with_all_three_compose_files(tmp_path: Path) -> None:
    """The `-f` set, off the call the engine makes, not off `build_staged`'s docstring.

    A bare `docker compose build` in a generated install's directory builds
    NOTHING and exits 0 — the `build:` blocks live in the overlay compose never
    auto-loads — and naming any `-f` disables auto-loading, so all three have to
    be listed. A rebuild that lost the overlay would print a successful build
    and change nothing, which is the user's complaint word for word.
    """
    rec = Recorder(images=True)
    seen: list[object] = []

    def build(server_dir: Path, files: object, **kwargs: object) -> docker.AttachedRun:
        rec.calls.append("build")
        seen.append(tuple(files))  # type: ignore[arg-type]
        return docker.AttachedRun(0, ("built",))

    server_dir = a_finished_install(rec, tmp_path)
    list(engine(rec, build=build).rebuild(InstallOptions(server_dir=server_dir)))
    assert seen == [composegen.COMPOSE_FILES]


# -- refusals ----------------------------------------------------------------


def test_a_rebuild_refuses_a_folder_this_app_has_no_record_of_building(tmp_path: Path) -> None:
    """An install this app did not build. Requirement 4's first half."""
    rec = Recorder()
    server_dir = tmp_path / "somebody-elses"
    server_dir.mkdir()
    (server_dir / composegen.BASE_FILE).write_text("services: {}\n", encoding="utf-8")
    with pytest.raises(InstallerError) as raised:
        list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    message = str(raised.value)
    assert native.STATE_FILE in message
    assert str(server_dir) in message
    assert "Nothing was started" in message
    assert "build" not in rec.calls


def test_a_rebuild_refuses_an_adopted_install_whose_compose_file_is_not_ours(
    tmp_path: Path,
) -> None:
    """The DML-built install adopted through "Use existing…", said honestly.

    `composegen.write_plan()` never overwrites a compose file this engine did
    not write — it raises rather than orphan somebody's character volumes — so
    the app cannot put a build overlay into such a folder, and without an
    overlay `docker compose build` builds nothing. The refusal has to say that,
    including the half that is about what the button CANNOT do here, because a
    user whose server is one of those is exactly the user this feature is for.

    The state file is present and complete: this is not the "no record" case
    above, it is the one where the record is there and the compose files are
    somebody else's.
    """
    rec = Recorder()
    server_dir = a_finished_install(rec, tmp_path)
    (server_dir / composegen.BUILD_FILE).write_text(
        "# hand-written by the DML launcher\nservices: {}\n", encoding="utf-8"
    )
    with pytest.raises(InstallerError) as raised:
        list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    message = str(raised.value)
    assert composegen.BUILD_FILE in message
    assert "did not write" in message
    assert "Nothing was started" in message
    assert "build" not in rec.calls


def test_a_rebuild_refuses_when_the_build_overlay_is_missing_altogether(tmp_path: Path) -> None:
    """A deleted overlay is the same dead end as a foreign one and gets the same refusal.

    `composegen.is_ours()` answers True for a file that is not there — the
    right answer for "may I write this?" and the wrong one for "can I build
    with this?". A rebuild that read only the marker would sail past a missing
    overlay into a `docker compose build` that exits 0 having built nothing.
    """
    rec = Recorder()
    server_dir = a_finished_install(rec, tmp_path)
    (server_dir / composegen.BUILD_FILE).unlink()
    with pytest.raises(InstallerError) as raised:
        list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert composegen.BUILD_FILE in str(raised.value)
    assert "build" not in rec.calls


# -- the cancel --------------------------------------------------------------


def test_a_cancel_set_before_the_first_stage_compiles_nothing(tmp_path: Path) -> None:
    """Stop is a real stop, and the engine's own cancel is what carries it.

    The confirmation dialog is the user's first chance to say no
    (`test_declining_the_rebuild_confirmation_starts_nothing`); this is the
    second, and it is the one that has to hold once the panel is streaming.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(InstallerError):
        list(engine(rec).rebuild(InstallOptions(server_dir=server_dir), cancel=cancel))
    assert "build" not in rec.calls


# -- progress ----------------------------------------------------------------


def test_the_rebuild_reports_through_the_installers_own_staged_reporting(
    tmp_path: Path,
) -> None:
    """Requirement 5. The same `Step n of m` / `--- <name>` lines, from the same loop.

    Not a similar-looking pair of lines: `^--- <stage>` is what gate scripts,
    log captures and the interrupted-import watchers match on, and a second
    reporting scheme would be a second place for that format to drift. The
    cancel note is asserted too, because the spine — not the stage body — is
    what says it, so its presence is evidence the shared loop ran.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    said = list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    stages = [line for line in said if line.startswith("--- ")]
    assert stages == ["--- build", "--- recreate", "--- ready"], said
    steps = [line for line in said if line.startswith("Step ")]
    assert steps[0].startswith(f"Step 1 of {len(stages)}"), steps
    assert len(steps) == len(stages)
    assert native.BUILD_CANCEL_NOTE in said


def test_the_rebuild_ends_by_saying_what_it_did_and_what_it_did_not(tmp_path: Path) -> None:
    """The closing line is the answer to "nothing changes when I log back in".

    It has to name the server as running on the new build, and it must not
    imply the database was touched: this press compiles and recreates, and the
    module SQL an AzerothCore module carries is applied by the emulator's own
    updater for modules in `AC_MODULES_LIST`, which nothing here runs.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    said = list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert said[-1] == f"{ENTRY.name} was rebuilt and is running in {server_dir}"
    assert native.REBUILD_CLOSING_NOTE in said
    # The claim it must NOT make. "Applied", "installed" or any past-tense verb
    # about the database would be this feature's own bug repeated one layer up:
    # telling a user something took effect when nobody checked.
    assert "ran no SQL" in native.REBUILD_CLOSING_NOTE
    for promise in ("SQL was applied", "fully installed", "module is now installed"):
        assert promise not in native.REBUILD_CLOSING_NOTE
    # And nothing anywhere in the run went near a database.
    assert "sql" not in rec.calls and "query" not in rec.calls, rec.calls


def test_the_recreate_argv_does_not_rely_on_compose_noticing_a_new_image() -> None:
    """UNIT-TESTED ONLY, and the reason the flag is there rather than assumed.

    `start_staged()`'s docstring records what was measured against Docker
    29.1.3: it "recreates a service whose configuration changed". Nothing in
    this repository records what compose does when only the IMAGE behind an
    unchanged tag has changed — and the image tag is derived from the folder,
    so a rebuild never changes it. A rebuild that left the old container
    running would produce a perfect log and no change at all, which is the
    defect this whole feature is about, so the recreate is asked for
    explicitly instead of hoped for.

    No rebuild was run to write this. What is asserted is the argv.
    """
    argv = docker.recreate_argv(ENTRY.container_spec())
    assert argv[:4] == ["compose", "up", "-d", "--force-recreate"]
    assert "--no-deps" in argv
    spec = ENTRY.container_spec()
    assert spec.import_service is not None
    assert spec.import_service not in argv, "a recreate must never re-run the one-shot import"


# -- the cost the confirmation states -----------------------------------------


def test_every_build_time_the_confirmation_quotes_is_still_recorded_in_pyplan() -> None:
    """Requirement 6: the numbers are this project's own measurements, not invented.

    Each one is pinned to the page it was measured on, so a confirmation that
    grew a friendlier number would go red here rather than in front of a user
    who then plans their evening around it. If a citation ever stops matching,
    the fix is to re-read the gate and change the sentence — not to relax the
    pin.
    """
    from yulon.catalog.installer import rebuild_confirmation

    text = rebuild_confirmation(ENTRY, Path("/srv"))
    checklist = (PYPLAN / "checklist.md").read_text(encoding="utf-8")
    # "35-72 minutes" is the hunt rounds' range; those rounds are kept in the
    # maintainers' local notes, so only its presence in the sentence is pinned here.
    assert "35-72 minutes" in text, "the confirmation no longer quotes 35-72 minutes"
    for quoted in ("68 minutes", "15 minutes"):
        assert quoted in text, f"the confirmation no longer quotes {quoted}"
        assert quoted in checklist, f"{quoted} is no longer recorded in pyplan/checklist.md"


def test_the_confirmation_says_what_it_costs_before_it_says_yes(tmp_path: Path) -> None:
    """Everything a person needs to answer the question, in the question.

    The three facts a "Rebuild?" box that omitted them would be lying by
    omission about: it takes as long as an install's compile, the server goes
    down and comes back, and saying no changes nothing.
    """
    from yulon.catalog.installer import rebuild_confirmation

    text = rebuild_confirmation(ENTRY, tmp_path)
    assert str(tmp_path) in text, "the question does not say which install it is about"
    lowered = text.lower()
    assert "minutes" in lowered, "the question does not say how long"
    assert "stopped" in lowered, "the question does not say the server goes down"
    assert "say no and nothing happens" in lowered, "the question does not say what no costs"


# -- the rollback: owner answer 2, 2026-09-08 -----------------------------------
#
# "Always keep a rollback, and restore it automatically if the world does not
# come up. Tag the working image before building; if the worldserver does not
# reach ready within its window, retag, bring the old one back, and show what
# the log said." It is the recipe that saved m910q on the night of 2026-09-08,
# done by hand after a Tortoise rebuild crash-looped on 173 migrations; these
# tests make it the app's. `pyplan/phase8-owner-answers-2026-09-08.md` §2.


def _refs(server_dir: Path) -> tuple[str, ...]:
    return composegen.built_image_refs(ENTRY, server_dir, platform_id=lambda: "macos")


def _rollback_refs(server_dir: Path) -> tuple[str, ...]:
    return tuple(ref + native.ROLLBACK_TAG_SUFFIX for ref in _refs(server_dir))


def _answers(*values: bool):
    """A `wait_ready` seam that answers `values` in order, then the last one for ever."""
    queue = list(values)

    def wait_ready(spec: object, ready: object) -> bool:
        if len(queue) > 1:
            return queue.pop(0)
        return queue[0]

    return wait_ready


def test_a_rebuild_keeps_the_running_build_under_a_rollback_tag_before_compiling(
    tmp_path: Path,
) -> None:
    """Every image the build will overwrite gets a second name FIRST.

    `docker compose build` writes the new image over the same tag the running
    containers were created from, so without this the old build is gone the
    moment the compile finishes -- which is why the m910q rollback had to be
    prepared by hand before the rebuild rather than after it failed.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    tags = [c for c in rec.calls if c.startswith("tag:")]
    assert tags == [f"tag:{r}->{r}{native.ROLLBACK_TAG_SUFFIX}" for r in _refs(server_dir)]
    assert rec.calls.index(tags[-1]) < rec.calls.index("build"), rec.calls


def test_a_rebuild_whose_server_never_comes_up_puts_the_old_build_back(
    tmp_path: Path,
) -> None:
    """The new build never reports ready: the rollback tags go back, the containers are
    recreated on them, and the failure the user reads says both what happened and that
    the old build is running again."""
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    with pytest.raises(InstallerError) as raised:
        list(
            engine(rec, wait_ready=_answers(False, True)).rebuild(
                InstallOptions(server_dir=server_dir)
            )
        )
    refs, backs = _refs(server_dir), _rollback_refs(server_dir)
    restores = [f"tag:{b}->{r}" for r, b in zip(refs, backs, strict=True)]
    for restore in restores:
        assert restore in rec.calls, rec.calls
    recreates = [i for i, c in enumerate(rec.calls) if c == "recreate"]
    assert len(recreates) == 2, rec.calls
    assert rec.calls.index(restores[-1]) < recreates[1], rec.calls
    said = str(raised.value)
    assert "never reported ready" in said
    assert "put back" in said and "running again" in said, said


def test_a_rebuild_that_comes_up_lets_the_rollback_go(tmp_path: Path) -> None:
    """Success removes the second name, so the old build stops costing disk.

    Owner answer 2 names the price as "one image's worth of disk WHILE a
    rebuild runs" -- not for ever. Removed after `ready`, never before it.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    said = list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    rmis = [c for c in rec.calls if c.startswith("rmi:")]
    assert rmis == [f"rmi:{b}" for b in _rollback_refs(server_dir)], rec.calls
    assert rec.calls.index(rmis[0]) > rec.calls.index("recreate"), rec.calls
    assert said[-1] == f"{ENTRY.name} was rebuilt and is running in {server_dir}"


def test_a_failed_compile_leaves_the_running_server_alone(tmp_path: Path) -> None:
    """A build that fails leaves the old tag in place, so there is nothing to restore
    and no reason to touch a server that is still running the build it had."""
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    rec.build_result = docker.AttachedRun(1, ("cc1plus: error",))
    with pytest.raises(InstallerError) as raised:
        list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert "recreate" not in rec.calls, rec.calls
    assert not [
        c
        for c in rec.calls
        if c.startswith("tag:") and "->" in c and c.endswith(tuple(_refs(server_dir)))
    ], "a failed build was 'restored' over a tag it never changed"
    # The duplicate name is let go: the old image is still the live tag.
    assert [c for c in rec.calls if c.startswith("rmi:")] == [
        f"rmi:{b}" for b in _rollback_refs(server_dir)
    ], rec.calls
    assert "put back" not in str(raised.value)


def test_the_failure_names_it_when_the_old_build_does_not_come_up_either(
    tmp_path: Path,
) -> None:
    """Both builds failing to report ready is the one outcome a rollback cannot fix,
    and the sentence must not claim the server is running again."""
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    rec.ready = False
    with pytest.raises(InstallerError) as raised:
        list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    said = str(raised.value)
    assert "put back" in said, said
    assert "running again" not in said, said
    assert "did not report ready either" in said, said


def test_a_rebuild_with_images_missing_refuses_rather_than_compiling_without_a_rollback(
    tmp_path: Path,
) -> None:
    """Nothing to keep is a refusal, not a warning. Owner answer 2 says ALWAYS.

    Until the adversarial review of 2026-09-08 this pressed on with a sentence
    saying no rollback was kept -- which contradicted the confirmation the
    user had just agreed to, and made the one press with no safety net the one
    that looked most like the others. A finished install whose images are
    gone is Install's to repair (its resume rebuilds them), not this button's.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    rec.images = False
    with pytest.raises(InstallerError) as raised:
        list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert "build" not in rec.calls, rec.calls
    assert not [c for c in rec.calls if c.startswith("tag:")], rec.calls
    assert "Nothing was started" in str(raised.value)
    assert "Install" in str(raised.value), str(raised.value)


def test_a_daemon_that_will_not_say_whether_the_images_exist_is_a_refusal_too(
    tmp_path: Path,
) -> None:
    """`None` is "could not ask", and destructive work on an unanswered question fails closed."""
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    rec.images = None
    with pytest.raises(InstallerError) as raised:
        list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert "build" not in rec.calls, rec.calls
    assert "would not say" in str(raised.value), str(raised.value)


def test_a_rollback_that_cannot_be_kept_refuses_before_the_compile(tmp_path: Path) -> None:
    """The images exist and docker will not tag them: nothing is compiled.

    Building anyway would overwrite the only copy of the running build with
    the rollback the owner asked for unkept -- the one state answer 2 rules
    out. Refused with docker's own words and before the confirmation's cost.
    """
    rec = Recorder(images=True, tag_problem="Error response from daemon: read-only layer store")
    server_dir = a_finished_install(rec, tmp_path)
    with pytest.raises(InstallerError) as raised:
        list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert "build" not in rec.calls, rec.calls
    assert "read-only layer store" in str(raised.value)
    assert "Nothing was started" in str(raised.value)


def test_the_rollback_is_in_the_confirmation_the_user_agrees_to(tmp_path: Path) -> None:
    """The confirmation is where the price and the safety net are both read."""
    text = rebuild_confirmation(ENTRY, tmp_path / "wow")
    assert "rollback" in text.lower() or "put back" in text.lower(), text


def test_a_restore_that_fails_halfway_puts_every_tag_back_on_the_new_build(
    tmp_path: Path,
) -> None:
    """Half the refs on the old build and half on the new is a server nobody has tested.

    Adversarial review, 2026-09-08: the restore retagged each ref on its own
    and, on a failure, reported "the tags still name the new build" -- false
    for the refs already put back. Now the new build is given its own name
    before any ref moves, and a failure part-way retags the moved ones back
    onto it, so the sentence "still on the new build" is true of all of them.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    refs, backs = _refs(server_dir), _rollback_refs(server_dir)
    rec.ready = False
    restores_seen = 0

    def tag_image(src: str, dst: str) -> str:
        nonlocal restores_seen
        rec.calls.append(f"tag:{src}->{dst}")
        if src in backs:
            restores_seen += 1
            if restores_seen == 2:
                return "Error response from daemon: layer store is read-only"
        return ""

    with pytest.raises(InstallerError) as raised:
        list(engine(rec, tag_image=tag_image).rebuild(InstallOptions(server_dir=server_dir)))
    said = str(raised.value)
    # The one ref that moved was moved back onto the new build's own name.
    failed_name = refs[0] + native.FAILED_TAG_SUFFIX
    assert f"tag:{failed_name}->{refs[0]}" in rec.calls, rec.calls
    assert "still name the new build" in said, said
    assert "put back and is running" not in said, said
    # And no recreate happened on a mixed set.
    assert rec.calls.count("recreate") == 1, rec.calls


def test_the_new_build_gets_its_own_name_before_any_tag_is_moved(tmp_path: Path) -> None:
    """The compensating retag above needs the new build addressable; that name is made first."""
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    rec.ready = False
    with pytest.raises(InstallerError):
        list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    refs, backs = _refs(server_dir), _rollback_refs(server_dir)
    named = [f"tag:{r}->{r}{native.FAILED_TAG_SUFFIX}" for r in refs]
    restores = [f"tag:{b}->{r}" for r, b in zip(refs, backs, strict=True)]
    for n in named:
        assert n in rec.calls, rec.calls
    assert max(rec.calls.index(n) for n in named) < min(rec.calls.index(s) for s in restores)


def test_the_rollback_sentence_says_the_database_is_not_part_of_what_was_put_back(
    tmp_path: Path,
) -> None:
    """An image rollback is not a system rollback, and the sentence must not read as one.

    The Tortoise incident this feature answers (2026-09-08) was the new
    binary's own updater migrating the world database at startup and then
    cancelling the server. Putting the old binary back does not put those
    rows back. The owner chose the image rollback knowing that (answer 2);
    what the adversarial review added is that the user must be told it in
    the same sentence that says the old build is running again.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    with pytest.raises(InstallerError) as raised:
        list(
            engine(rec, wait_ready=_answers(False, True)).rebuild(
                InstallOptions(server_dir=server_dir)
            )
        )
    said = str(raised.value)
    assert "running again" in said, said
    assert "database" in said and "NOT put back" in said, said


def test_the_failed_name_is_let_go_once_the_containers_holding_it_are_replaced(
    tmp_path: Path,
) -> None:
    """A `-failed` name docker refused is let go once the restore's own recreate frees it.

    **Measured on `yulon-ubuntu2`, 2026-09-09, on the first live press of the
    restore arm** (`.notes/gates/rollback-restore-yulon-ubuntu2-2026-09-09/`).
    `_let_go(named)` ran while the containers made from the new build were
    still there, so the daemon refused two of the four removals by name:

        could not remove image ...ac-wotlk-worldserver:native-243c46e3-failed:
        conflict: unable to delete ... (must be forced) - container
        31769acad1c4 is using its referenced image 7c1ecf94b44e

    and nothing ever asked again. The press ended with 840 MB of a build known
    to be broken sitting on the daemon under a name `FAILED_TAG_SUFFIX`
    documents as transient -- and it is not the unlucky half: it is exactly the
    long-running services, whose containers are what the restore is about to
    replace, so on this shape it happens every time.

    The double could not see it, which is why the unit side passed while the
    daemon refused: `Recorder.remove_image` is "always allowed here". The seam
    here is a `remove_image` that refuses the way this daemon does, and the
    assertion is on the LAST attempt rather than on any attempt -- one made
    while the blocker is still in place proves nothing about the name being
    gone.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    failed = tuple(ref + native.FAILED_TAG_SUFFIX for ref in _refs(server_dir))

    def remove_image(ref: str) -> str:
        rec.calls.append(f"rmi:{ref}")
        # The broken containers exist until the restore's own recreate replaces
        # them, which is the second `recreate` of the run.
        if ref in failed and rec.calls.count("recreate") < 2:
            return (
                f"Error response from daemon: conflict: unable to delete {ref} (must be "
                f"forced) - container 31769acad1c4 is using its referenced image"
            )
        return ""

    with pytest.raises(InstallerError) as raised:
        list(
            engine(rec, remove_image=remove_image, wait_ready=_answers(False, True)).rebuild(
                InstallOptions(server_dir=server_dir)
            )
        )
    assert "put back and is running again" in str(raised.value), raised.value
    last_recreate = max(i for i, c in enumerate(rec.calls) if c == "recreate")
    for name in failed:
        tries = [i for i, c in enumerate(rec.calls) if c == f"rmi:{name}"]
        assert tries, f"{name} was never let go at all: {rec.calls}"
        assert max(tries) > last_recreate, (
            f"{name} was only ever let go while the container holding it was still there, "
            f"so the refusal stands and the name is left on the daemon for ever: {rec.calls}"
        )


def test_the_confirmation_says_what_the_rollback_does_not_cover(tmp_path: Path) -> None:
    text = rebuild_confirmation(ENTRY, tmp_path / "wow")
    assert "database" in text.lower(), text
    low = text.lower()
    assert low.index("rollback") < low.index("database", low.index("rollback")), text


# -- the address the ready wait asserts --------------------------------------


def test_a_rebuild_waits_for_a_realm_line_whatever_address_it_advertises(
    tmp_path: Path,
) -> None:
    """The defect the first live press of this control found, on yulon-ubuntu2 2026-09-09.

    `rebuild_stages()` reuses the install's `ready` stage, whose auth marker is
    `{{REALM_HOST}}:{{WORLD_PORT}}` filled with `INSTALL_REALM_HOST` -- the
    loopback a FRESH install advertises. `_advertise_realm()` is the install's
    LAST act and replaces that row with the machine's LAN address, so on every
    install this button can be pressed on, the authserver's own line says the
    LAN address while the marker says `127.0.0.1`. Measured live: the compile
    finished, the containers were replaced, the new worldserver came up with
    the module compiled in and answered a command over its own channel, and the
    press sat in "Waiting for the world server" because `_auth_ready()` could
    never match. Left alone it burns `READY_CEILING_SECONDS` -- six hours --
    and then rolls a GOOD build back: the complaint this button exists to fix,
    with six hours added to it.

    What readiness needs from the auth server is that it advertised THIS
    install's realm on THIS install's world port. Which address it advertises
    is `_advertise_realm()`'s question and is asked there, against the row.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    rec.ready_specs.clear()
    list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert rec.ready_specs, "the rebuild never reached the ready wait"
    auth = rec.ready_specs[-1].auth
    assert auth is not None
    advertised = f'Added realm "Whatever" at 192.168.1.25:{ENTRY.ports.world}.'
    assert re.search(auth, advertised), (auth, advertised)
    loopback = f'Added realm "Whatever" at 127.0.0.1:{ENTRY.ports.world}.'
    assert re.search(auth, loopback), (auth, loopback)


def test_the_ready_wait_still_refuses_a_realm_line_on_another_port(tmp_path: Path) -> None:
    """The control for the test above, and the reason the marker is not just `\S+`.

    Opening the ADDRESS is the change; opening the port would make the marker
    match a realm this install is not, which is what the port is in it for. A
    pattern that matched everything would pass the test above for the wrong
    reason, so the same pattern is shown refusing something.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    rec.ready_specs.clear()
    list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    auth = rec.ready_specs[-1].auth
    assert auth is not None
    other = f'Added realm "Whatever" at 192.168.1.25:{ENTRY.ports.world + 1}.'
    assert not re.search(auth, other), (auth, other)
    assert not re.search(auth, "the auth server said nothing about a realm"), auth


def test_a_fresh_install_waits_on_the_same_marker_the_rebuild_does(tmp_path: Path) -> None:
    """One marker, both paths -- the install is not left on a rule of its own.

    A fix that only touched `rebuild_stages()` would leave two ideas of what
    ready means in one class, and the install's would still be the one that
    goes stale the moment `_advertise_realm()` runs. This holds them equal.
    """
    rec = Recorder(images=True)
    install(rec, tmp_path / "wow")
    assert rec.ready_specs, "the install never reached the ready wait"
    installed = rec.ready_specs[-1].auth
    rec2 = Recorder(images=True)
    server_dir = a_finished_install(rec2, tmp_path / "second")
    rec2.ready_specs.clear()
    list(engine(rec2).rebuild(InstallOptions(server_dir=server_dir)))
    assert installed == rec2.ready_specs[-1].auth
    assert installed is not None
    assert re.search(installed, f'Added realm "W" at 127.0.0.1:{ENTRY.ports.world}.')


# -- the build recipe the compile is handed ------------------------------------
#
# T8, filed from T4's live Tortoise upgrade on m910q (2026-09-09,
# `.notes/gates/tortoise-upgrade-m910q-2026-09-09/`). The install's Dockerfile is
# rendered ONCE, when the server is installed, and `rebuild_stages()` used to be
# `(build, recreate, ready)` -- so `docker compose build` compiled whatever that
# render left behind, and a fix shipped in this app's template could never reach
# a server that was already installed. Measured rather than reasoned: that
# install's Dockerfile was rendered 2026-09-07 (`FROM ubuntu:22.04`, zero
# `INSERT IGNORE`), the template had been fixed 2026-09-08 (`3a1ed6ee`), and the
# upgrade only worked because the hand ran the family's own `write-dockerfile`
# stage first (`dockerfile-render.log`: "Dockerfile changed: True", both `FROM`
# lines 22.04 -> 24.04, and the `INSERT IGNORE` rewrite arriving with them).
#
# A family whose checkout ships its OWN Dockerfile (AzerothCore: `dockerfile_dir`
# is None) has no template of ours to be behind, which is why the stage is
# selected by presence rather than prepended to every rebuild.


@pytest.fixture
def cmangos_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """`CmangosInstaller._gate` is the double `cm_engine(rec)` attached, as in its own file.

    `test_families_cmangos.py`'s `gated` fixture is autouse THERE and reaches
    nothing here, so a cmangos install driven from this file would take the real
    `MarkerGate` to a database that does not exist. Same four lines, same
    `raising=True`: renaming `_gate` errors here rather than quietly adding an
    attribute nobody reads.
    """

    def gate(self: CmangosInstaller, ctx: native.StageContext) -> native.ImportGate:
        attached = getattr(self, "_test_gate", None)
        assert attached is not None, "build cmangos engines with cm_engine(rec) in this file"
        return attached

    monkeypatch.setattr(CmangosInstaller, "_gate", gate, raising=True)


def a_finished_cmangos_install(rec: Recorder, tmp_path: Path) -> Path:
    """`a_finished_install()` for the family that renders its own Dockerfile.

    The same rule: the real install through the machine double, so the state
    file, the compose files, the `.db_password` the re-render reads back and the
    Dockerfile itself are the engine's own output rather than a fixture's.
    """
    server_dir = tmp_path / "tbc"
    cm_install(rec, server_dir, client_folder(tmp_path))
    rec.calls.clear()
    return server_dir


def _stale(server_dir: Path) -> dict[str, bytes]:
    """Put this install a template-fix behind, in BOTH recipe files, the way a real one gets there.

    An install made before the base image moved: its rendered Dockerfile names
    the OLD base, on both `FROM` lines, which is the shape the Tortoise
    template's own comment says they always move in — and its `.dockerignore`
    carries a line the template has since dropped, because the stage renders
    that file too and a test that only staled the Dockerfile could not tell a
    one-file restore from a two-file one. The marker stays on both: these are
    files Yu'lon wrote, not somebody's own, so the only thing standing between
    the stale text and the compiler is whether a rebuild renders again.

    Bytes throughout, never `read_text`/`write_text`: `dockerfile.write()` writes
    with `newline="\\n"` and compares untranslated, so a round trip through
    Python's universal newlines would hand this test a CRLF "ground" that the
    engine can never reproduce and the restore would look broken on Windows.

    Returns:
        Each file's bytes as the templates render them TODAY — what the compile
        must be handed, and what a stopped press must put back.
    """
    edits = {
        dockerfile.DOCKERFILE: lambda text: text.replace("ubuntu:22.04", "ubuntu:20.04"),
        dockerfile.DOCKERIGNORE: lambda text: text + "# a line the template has since dropped\n",
    }
    fresh: dict[str, bytes] = {}
    for name, edit in edits.items():
        path = server_dir / name
        fresh[name] = path.read_bytes()
        stale = edit(fresh[name].decode("utf-8"))
        assert stale.encode("utf-8") != fresh[name], f"{name}: the template no longer renders this"
        path.write_bytes(stale.encode("utf-8"))
    return fresh


def test_a_rebuild_hands_the_compiler_the_current_template_not_the_render_on_disk(
    cmangos_gate: None, tmp_path: Path
) -> None:
    """THE test for T8. Read at the moment the build seam is called, not afterwards.

    Asserting the file on disk once the run has finished would pass for a
    rebuild that rendered AFTER compiling, which is the one ordering that
    changes nothing at all. So the Dockerfile is read INSIDE the build seam --
    the last moment before the daemon is handed the context -- and what it has
    to hold is the text the app's own renderer produces today.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_cmangos_install(rec, tmp_path)
    fresh = _stale(server_dir)
    handed: list[dict[str, bytes]] = []

    def build(
        build_dir: Path, files: object, *, sink: object = None, cancel: object = None
    ) -> docker.AttachedRun:
        rec.calls.append("build")
        handed.append({name: (build_dir / name).read_bytes() for name in fresh})
        return rec.build_result

    said = list(cm_engine(rec, build=build).rebuild(InstallOptions(server_dir=server_dir)))
    assert handed, "the rebuild never reached the compile"
    assert handed[0] == fresh, (
        "the compile was handed the build recipe rendered when this server was installed, "
        "so a fix shipped in the app's templates cannot reach an existing install"
    )
    stages = [line for line in said if line.startswith("--- ")]
    assert stages == ["--- write-dockerfile", "--- build", "--- recreate", "--- ready"], said
    assert {name: (server_dir / name).read_bytes() for name in fresh} == fresh


def test_a_rebuild_that_is_already_current_leaves_the_dockerfile_alone(
    cmangos_gate: None, tmp_path: Path
) -> None:
    """Idempotent, and the stage says so rather than going quiet.

    `dockerfile.write()` leaves unchanged text alone so the mtime does not move
    -- which is the whole reason this stage can go in front of every rebuild
    instead of behind a drift check. A re-render that rewrote the byte-identical
    file would move that mtime, and the `COPY` of the build context would then
    miss the layer cache on every rebuild anybody ever presses.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_cmangos_install(rec, tmp_path)
    before = (server_dir / dockerfile.DOCKERFILE).stat().st_mtime_ns
    said = list(cm_engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert f"{dockerfile.DOCKERFILE} is already exactly what this install needs." in said, said
    assert f"Wrote {dockerfile.DOCKERFILE}" not in said, said
    assert (server_dir / dockerfile.DOCKERFILE).stat().st_mtime_ns == before


def test_a_checkout_that_ships_its_own_dockerfile_rebuilds_the_same_three_stages(
    tmp_path: Path,
) -> None:
    """AzerothCore has no template of ours to be behind, and must not gain a stage.

    `dockerfile_dir` is None for that entry and the field's own description says
    what that means: "the checkout ships its own Dockerfile". Prepending the
    stage to every family would ask `stage_named()` for one AzerothCore does not
    have and refuse every WotLK rebuild by name -- so the selection is by
    presence, and this is the half that says so.
    """
    rec = Recorder(images=True)
    assert ENTRY.install.native is not None
    assert ENTRY.install.native.dockerfile_dir is None
    names = [stage.name for stage in engine(rec).rebuild_stages()]
    assert names == ["build", "recreate", "ready"], names
    cm_names = [stage.name for stage in installer_for(CM_ENTRY).rebuild_stages()]
    assert cm_names == ["write-dockerfile", "build", "recreate", "ready"], cm_names


def test_the_confirmation_promises_the_re_render_for_exactly_the_games_that_get_it() -> None:
    """The sentence and the stage tuple, read from the same catalog, entry by entry.

    Two derivations of one fact: `rebuild_confirmation()` holds only the entry,
    so it asks whether `install.native.dockerfile_dir` names a template, while
    `rebuild_stages()` asks its own family for a `write-dockerfile` stage. A
    guard that only checked the sentence was present somewhere would pass while
    the app promised WotLK users a re-render nothing does -- which is the shape
    of promise this whole ticket is about. So the two are bound across every
    shipped entry rather than sampled.
    """
    entries = [e for e in load_catalog().games if e.install.native is not None]
    assert len(entries) >= 4, "the catalog lost its native entries"
    for entry in entries:
        native_block = entry.install.native
        assert native_block is not None
        renders = native_block.dockerfile_dir is not None
        staged = "write-dockerfile" in [
            stage.name for stage in installer_for(entry).rebuild_stages()
        ]
        assert renders is staged, (
            f"{entry.id}: the confirmation reads `dockerfile_dir` and the rebuild reads its "
            f"stage tuple, and they disagree ({renders} vs {staged})"
        )
        text = rebuild_confirmation(entry, Path("/srv"))
        assert (dockerfile.DOCKERFILE in text) is renders, (entry.id, text)
        # BOTH files, because the stage writes both through one
        # `dockerfile.write()`. The clause named only the first and added
        # "nothing else in the folder is rewritten", which was false of a
        # `.dockerignore` behind its template (Fable, round 1).
        assert (dockerfile.DOCKERIGNORE in text) is renders, (entry.id, text)
        assert "Nothing else in the folder" not in text, (entry.id, text)
        # And it may not claim that editing one of them stops the press. What
        # stops it is the FIRST LINE going missing: `_look()` answers `OURS` on
        # the marker alone, so a file edited under it is replaced whole (cold
        # review, round 2). The behaviour half is
        # `test_a_rebuild_hands_the_compiler_the_current_template_not_the_render_on_disk`,
        # which edits both files below the marker and requires them replaced.
        assert "if you have edited either" not in text.lower(), (entry.id, text)
        if renders:
            assert "templates" in text, (entry.id, text)
            assert "put back" in text, (entry.id, text)
            assert "line it writes at the top" in text, (entry.id, text)


def test_a_dockerfile_the_user_owns_stops_the_rebuild_before_it_compiles(
    cmangos_gate: None, tmp_path: Path
) -> None:
    """The new failure surface this stage brings, and the sentence the confirmation promises.

    A rebuild can now fail BEFORE the compile, which nothing could do before:
    `dockerfile.write()` refuses a file that carries no marker, because a
    Dockerfile this engine did not write is somebody's own build. What makes it
    "somebody's own" is the first line and only the first line -- `_look()`
    answers `OURS` on `composegen.GENERATED_MARKER` -- so the file planted here
    drops that line, which is the one edit the check can actually see, and
    `rebuild_confirmation()` says so in those terms since round 2.

    The tempting wrong fix is a `try`/`except` around the stage so a Dockerfile
    problem "does not block a rebuild". That would compile the user's own file
    and report success, which is this ticket's defect with a friendlier face, so
    what is asserted is that the press RAISED and never reached the compiler --
    and that the rollback names taken a moment earlier were let go rather than
    left on the daemon.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_cmangos_install(rec, tmp_path)
    theirs = "FROM scratch\n# mine, not the app's\n"
    (server_dir / dockerfile.DOCKERFILE).write_text(theirs, encoding="utf-8")
    said: list[str] = []
    with pytest.raises(InstallerError) as raised:
        for line in cm_engine(rec).rebuild(InstallOptions(server_dir=server_dir)):
            said.append(line)
    assert "was not written by Yu'lon" in str(raised.value), raised.value
    assert "build" not in rec.calls, rec.calls
    assert "recreate" not in rec.calls, rec.calls
    assert (server_dir / dockerfile.DOCKERFILE).read_text(encoding="utf-8") == theirs
    refs = composegen.built_image_refs(CM_ENTRY, server_dir, platform_id=lambda: "linux")
    backs = [ref + native.ROLLBACK_TAG_SUFFIX for ref in refs]
    assert [c for c in rec.calls if c.startswith("rmi:")] == [f"rmi:{b}" for b in backs], rec.calls
    # On the LINES, not on the exception: the restore's sentences are yielded,
    # so `"put back" not in str(raised.value)` could never have failed (cold
    # review, round 2). Nothing moved here -- the user's file is the ground --
    # so nothing may claim it was put back or left behind.
    # "put back" alone would match `_keep_rollback`'s own line about the images.
    restored = [
        line
        for line in said
        if "build recipe was put back" in line or "build recipe was left as it is now" in line
    ]
    assert not restored, said


@pytest.mark.parametrize(
    "suffix",
    ["-custom", " (modified)", " - my note", " — my note"],
    ids=["glued-suffix", "parenthetical", "hyphen-note", "em-dash-note"],
)
def test_a_mark_that_only_looks_like_the_marker_stops_the_rebuild_before_it_compiles(
    cmangos_gate: None, tmp_path: Path, suffix: str
) -> None:
    """The prefix bug's own examples (T25 round 1) and the separator hole it left (round 2),
    driven through a real rebuild.

    Before round 1 `_look()` answered `OURS` for a first line that merely STARTS with
    `composegen.GENERATED_MARKER` -- so a user's own `# generated by Yu'lon-custom` or
    `# generated by Yu'lon (modified)`, marking a file they had taken over, read back as
    ours and `write-dockerfile` replaced it whole before the compile ever ran. Round 1's
    separator rule then answered OURS for `... - my note` and `... — my note`, which reuse
    the exact punctuation this engine's own banners do. All four are somebody's own file:
    the press stops, the compiler is never reached, and the rollback names taken a moment
    earlier are let go rather than left on the daemon -- the same three things
    `..._the_user_owns_stops_the_rebuild...` proves for a marker dropped outright.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_cmangos_install(rec, tmp_path)
    theirs = f"{composegen.GENERATED_MARKER}{suffix}\nFROM scratch\n"
    (server_dir / dockerfile.DOCKERFILE).write_text(theirs, encoding="utf-8")
    with pytest.raises(InstallerError) as raised:
        list(cm_engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert "was not written by Yu'lon" in str(raised.value), raised.value
    assert "build" not in rec.calls, rec.calls
    assert "recreate" not in rec.calls, rec.calls
    assert (server_dir / dockerfile.DOCKERFILE).read_text(encoding="utf-8") == theirs
    refs = composegen.built_image_refs(CM_ENTRY, server_dir, platform_id=lambda: "linux")
    backs = [ref + native.ROLLBACK_TAG_SUFFIX for ref in refs]
    assert [c for c in rec.calls if c.startswith("rmi:")] == [f"rmi:{b}" for b in backs], rec.calls


# -- what a stopped press leaves on the disk ------------------------------------
#
# `rebuild_opening_note()` promises that stopping before the containers are
# replaced leaves the server "exactly as it is", and the re-render put two files
# in that window: it rewrites them before the compile and records its stage, so a
# cancel or a failed build used to leave a recipe the user never confirmed for
# the NEXT press to compile (Codex, round 1). The promise is kept by
# `_put_recipe_back()` rather than by narrowing the sentence.


def _state_without_the_recipe_record(server_dir: Path) -> native.InstallState:
    """The state file an install made BEFORE this stage existed carries, and its ground.

    Not an invented fixture: `write-dockerfile` is a recorded stage, so every
    install this app has ever made has it in `completed` — and that is exactly
    what makes the record half of the restore invisible on a fresh tree. The
    population it is for is the folders on disk today, whose state files were
    written by earlier versions, and `read_state`'s own `unknown` field exists
    because that population is real. Modelled by taking the record back out.
    """
    ground = native.read_state(server_dir, valid=CmangosInstaller.STAGE_NAMES)
    assert ground is not None and native.DOCKERFILE_STAGE in ground.completed
    ground = replace(
        ground, completed=tuple(s for s in ground.completed if s != native.DOCKERFILE_STAGE)
    )
    native.write_state(server_dir, ground)
    return ground


def test_a_build_that_fails_puts_both_recipe_files_back_as_they_were(
    cmangos_gate: None, tmp_path: Path
) -> None:
    """The disk after a failed compile is the ground's, byte for byte, and so is the record.

    The compile is what the user agreed to and it did not happen, so nothing
    this press did to the folder may outlive it. Both files are asserted because
    the stage writes both through one `dockerfile.write()`; the state record is
    asserted because leaving `write-dockerfile` in `completed` would tell the
    install's own resume that a stage ran whose output has just been undone.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_cmangos_install(rec, tmp_path)
    ground_state = _state_without_the_recipe_record(server_dir)
    fresh = _stale(server_dir)
    was = {name: (server_dir / name).read_bytes() for name in fresh}
    assert was != fresh, "the stale ground is the same as the template's own render"
    rec.build_result = docker.AttachedRun(1, ("cc1plus: error",))
    with pytest.raises(InstallerError):
        list(cm_engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert {name: (server_dir / name).read_bytes() for name in fresh} == was, (
        "a press that compiled nothing left a build recipe the user never confirmed, so the "
        "next build would produce a different image"
    )
    after = native.read_state(server_dir, valid=CmangosInstaller.STAGE_NAMES)
    assert after is not None
    assert after.completed == ground_state.completed, after.completed
    assert after.last_error, "the failure itself is still recorded"


def test_a_stop_between_the_compile_and_the_recreate_puts_the_recipe_back(
    cmangos_gate: None, tmp_path: Path
) -> None:
    """The exact window the promise is about: the compile finished, the containers did not move.

    `_restore_rollback()`'s own docstring names it -- "the running server IS the
    old build, only the tags name the new one" -- and puts the tags back without
    restarting anything. The recipe has to come back with them, or the machine
    is left running a build made from one recipe with a different one on disk.
    This arm is a different branch from the failed compile above (`built` is
    True, so the images are restored rather than let go), which is why both are
    driven rather than one.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_cmangos_install(rec, tmp_path)
    fresh = _stale(server_dir)
    was = {name: (server_dir / name).read_bytes() for name in fresh}
    stop = threading.Event()

    def build(
        build_dir: Path, files: object, *, sink: object = None, cancel: object = None
    ) -> docker.AttachedRun:
        rec.calls.append("build")
        # Stopped while the compiler was running: the spine checks before the
        # next stage, so this is a press abandoned after an hour of output and
        # before anything the user is running has been touched.
        stop.set()
        return rec.build_result

    with pytest.raises(InstallerError) as raised:
        list(
            cm_engine(rec, build=build).rebuild(InstallOptions(server_dir=server_dir), cancel=stop)
        )
    assert "recreate" not in rec.calls, rec.calls
    assert {name: (server_dir / name).read_bytes() for name in fresh} == was, raised.value


def test_a_recreate_that_cannot_even_start_puts_the_recipe_back_and_takes_no_second_call(
    cmangos_gate: None, tmp_path: Path
) -> None:
    """The window the stop test above cannot reach: `recreate()` is entered, and the daemon
    was never asked to replace anything.

    (T25 round 1) `touched` used to go True on ENTRY to the wrapper, before
    `stage_recreate()` ran at all, so a `recreate` that raised before any container moved
    still read as `touched`: the `except` in `rebuild()` skipped `_put_recipe_back()` (the
    just-rewritten recipe stayed on disk though nothing was replaced) and
    `_restore_rollback()` went on to call `stage_recreate()` a SECOND time for a server the
    first call never touched.

    (T25 round 2) Round 1's own fix -- `touched` after a clean return from
    `stage_recreate()` -- read a `DockerCommandError` from the seam itself the same wrong
    way in the opposite direction (see the sibling test below): that call is one `compose
    up --force-recreate` for every service, and a failure from it does not say whether it
    is `docker_ready()`'s "never asked" or a `compose` that changed two of three services
    before failing the third. `docker_ready()` is the one question `stage_recreate()` can
    answer BEFORE the destructive call, so THIS is the scenario modelled here -- the
    daemon plainly unreachable, refused before `self._seams.recreate()` is ever called --
    and both recipe files and the stage record must come back exactly as they were, with
    the daemon never asked to recreate anything at all.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_cmangos_install(rec, tmp_path)
    ground_state = _state_without_the_recipe_record(server_dir)
    fresh = _stale(server_dir)
    was = {name: (server_dir / name).read_bytes() for name in fresh}
    assert was != fresh, "the stale ground is the same as the template's own render"

    said: list[str] = []
    with pytest.raises(InstallerError) as raised:
        for line in cm_engine(rec, docker_ready=lambda: False).rebuild(
            InstallOptions(server_dir=server_dir)
        ):
            said.append(line)
    assert {name: (server_dir / name).read_bytes() for name in fresh} == was, raised.value
    after = native.read_state(server_dir, valid=CmangosInstaller.STAGE_NAMES)
    assert after is not None
    assert after.completed == ground_state.completed, after.completed
    assert "recreate" not in rec.calls, rec.calls
    assert [line for line in said if "build recipe was put back exactly as it was" in line], said
    assert "no container was replaced" in str(raised.value), raised.value
    assert "Docker is not answering" in str(raised.value), raised.value


def test_a_recreate_that_fails_after_the_daemon_may_have_changed_something_is_not_restored(
    cmangos_gate: None, tmp_path: Path
) -> None:
    """The other side of the boundary: past `docker_ready()`, a failure is never "untouched".

    (T25 round 2) Modelled with a `recreate` seam that records having been asked to
    replace the containers -- what the real `compose up --force-recreate` would have done
    to SOME of them before a later service failed, or before the running-container check
    failed -- and then raises. The daemon really was asked to replace something, so the
    just-rewritten build recipe must NOT be put back (a fresh `write-dockerfile` stayed
    the one on disk), the stage record keeps the recipe stage, and the failure path takes
    the restoring `stage_recreate()` call rather than skipping it.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_cmangos_install(rec, tmp_path)
    fresh = _stale(server_dir)
    was = {name: (server_dir / name).read_bytes() for name in fresh}
    assert was != fresh, "the stale ground is the same as the template's own render"

    attempts = {"n": 0}

    def recreate(spec: docker.ContainerSpec, dir_: Path) -> bool:
        attempts["n"] += 1
        rec.calls.append("recreate")
        if attempts["n"] == 1:
            # What a real `compose up --force-recreate` can do before failing: replace
            # some of the services it was asked to and then error on another one.
            rec.calls.append("recreate:some-services-replaced")
            raise docker.DockerCommandError("worldserver did not come up")
        return True

    said: list[str] = []
    with pytest.raises(InstallerError) as raised:
        for line in cm_engine(rec, recreate=recreate).rebuild(
            InstallOptions(server_dir=server_dir)
        ):
            said.append(line)
    assert {name: (server_dir / name).read_bytes() for name in fresh} != was, (
        "a compose command that may have already changed something was 'restored' as "
        "though the press never reached it"
    )
    after = native.read_state(server_dir, valid=CmangosInstaller.STAGE_NAMES)
    assert after is not None
    assert native.DOCKERFILE_STAGE in after.completed
    assert attempts["n"] == 2, rec.calls
    assert not [line for line in said if "build recipe was put back exactly as it was" in line]
    assert "was put back" in str(raised.value), raised.value


def test_a_failure_between_the_progress_line_and_the_compose_call_is_still_untouched(
    cmangos_gate: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The window round 2 left (its review): `touched` flipped at `stage_recreate()`'s first
    yield, but `container_spec()` and the compose call come AFTER that yield, and the
    daemon can go away between the readiness probe and the call. A failure there is still
    "nothing was touched": both recipe files come back byte for byte, the stage record is
    restored, and the daemon is never asked to recreate anything. Modelled with
    `container_spec()` refusing after the progress line has been yielded."""
    rec = Recorder(images=True)
    server_dir = a_finished_cmangos_install(rec, tmp_path)
    ground_state = _state_without_the_recipe_record(server_dir)
    fresh = _stale(server_dir)
    was = {name: (server_dir / name).read_bytes() for name in fresh}
    assert was != fresh, "the stale ground is the same as the template's own render"

    engine_ = cm_engine(rec)

    def no_spec(self: object) -> docker.ContainerSpec:
        raise InstallerError("the compose file could not be read, so no container was named")

    monkeypatch.setattr(type(engine_.entry), "container_spec", no_spec)
    said: list[str] = []
    with pytest.raises(InstallerError) as raised:
        for line in engine_.rebuild(InstallOptions(server_dir=server_dir)):
            said.append(line)
    assert [line for line in said if "Replacing the running containers" in line], said
    assert {name: (server_dir / name).read_bytes() for name in fresh} == was, raised.value
    after = native.read_state(server_dir, valid=CmangosInstaller.STAGE_NAMES)
    assert after is not None
    assert after.completed == ground_state.completed, after.completed
    assert "recreate" not in rec.calls, rec.calls
    assert [line for line in said if "build recipe was put back exactly as it was" in line], said
    assert "no container was named" in str(raised.value), raised.value


def test_the_restore_says_so_and_stays_quiet_when_nothing_moved(
    cmangos_gate: None, tmp_path: Path
) -> None:
    """A silent revert is its own defect; a line about a restore that restored nothing is noise.

    The common case is an install already current, where the stage writes
    nothing and there is nothing to put back — so the sentence has to be tied to
    a file actually moving rather than to the failure.
    """

    def failed_rebuild(rec: Recorder, server_dir: Path) -> list[str]:
        rec.build_result = docker.AttachedRun(1, ("cc1plus: error",))
        said: list[str] = []
        with pytest.raises(InstallerError):
            for line in cm_engine(rec).rebuild(InstallOptions(server_dir=server_dir)):
                said.append(line)
        return said

    rec = Recorder(images=True)
    current = a_finished_cmangos_install(rec, tmp_path)
    quiet = failed_rebuild(rec, current)
    assert not [line for line in quiet if "put back exactly as it was" in line], quiet

    rec2 = Recorder(images=True)
    behind = a_finished_cmangos_install(rec2, tmp_path / "again")
    _stale(behind)
    said = failed_rebuild(rec2, behind)
    assert [line for line in said if "put back exactly as it was" in line], said


def test_a_ground_file_this_process_cannot_read_is_left_alone_and_named(
    cmangos_gate: None, tmp_path: Path
) -> None:
    """ "Could not ask" is not "was not there", and collapsing the two deleted the file.

    Traced by the cold reviewer, round 2: `_recipe_ground()` mapped every
    `OSError` to `None`, `None` means "the re-render created this, remove it",
    and a `Dockerfile` this process cannot READ therefore ended the press by
    being unlinked under the sentence "put back exactly as it was". On Windows
    the unlink raises `PermissionError` instead -- not an `InstallerError` -- so
    it flies past `_let_go` and the `-rollback` tags stay on the daemon for ever.

    A DIRECTORY in the file's place rather than `chmod 000`, so the same test
    runs on both platforms and needs no skip: `read_bytes()` raises
    `IsADirectoryError` on Linux and `PermissionError` on Windows, neither of
    them `FileNotFoundError`, which is exactly the distinction under test. It is
    also what `_look()` sees, so the stage refuses it as unreadable and the
    press reaches the restore the way a real one would.

    Three things are asserted, and the third is the one the double can see that
    a reader cannot: the directory survives, no sentence claims the recipe was
    put back, and the rollback names were let go -- proof that nothing raised
    past `_let_go` on the way out.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_cmangos_install(rec, tmp_path)
    path = server_dir / dockerfile.DOCKERFILE
    path.unlink()
    path.mkdir()
    (path / "keep.txt").write_text("a file inside what used to be the Dockerfile\n")
    # The rule itself, before the press that depends on it: three answers, and
    # this is the third. Asserted here as well as end-to-end because the harm
    # the press can show on both platforms is the wrong SENTENCE -- `unlink()`
    # cannot remove a directory, so the deletion the collapse causes needs a
    # regular unreadable file, which Windows cannot make.
    ground = cm_engine(rec)._recipe_ground(server_dir)
    assert ground[dockerfile.DOCKERFILE] is native.UNREADABLE_RECIPE, ground
    assert isinstance(ground[dockerfile.DOCKERIGNORE], bytes), ground
    said: list[str] = []
    with pytest.raises(InstallerError) as raised:
        for line in cm_engine(rec).rebuild(InstallOptions(server_dir=server_dir)):
            said.append(line)
    assert path.is_dir(), "the unreadable ground was removed"
    assert (path / "keep.txt").exists(), "what was in it went with it"
    assert "build" not in rec.calls, rec.calls
    assert not [line for line in said if "put back exactly as it was" in line], said
    named = [line for line in said if str(path) in line and "could not be read" in line]
    assert named, said
    refs = composegen.built_image_refs(CM_ENTRY, server_dir, platform_id=lambda: "linux")
    backs = [ref + native.ROLLBACK_TAG_SUFFIX for ref in refs]
    assert [c for c in rec.calls if c.startswith("rmi:")] == [
        f"rmi:{b}" for b in backs
    ], f"something raised past _let_go and left the rollback tags: {rec.calls}; {raised.value}"


def test_a_restore_that_cannot_write_says_so_instead_of_taking_the_rollback_with_it(
    tmp_path: Path,
) -> None:
    """Nothing in the restore may raise: it runs ahead of `_let_go` and `_restore_rollback`.

    The window is real rather than theoretical -- the ground was read before the
    first stage, and anything can have happened to those two paths in the hour
    of compiling since -- but it cannot be produced through a press, because a
    path the re-render could not write is a path the stage refused first. So
    this drives the method: a ground that says "these bytes were here" against a
    path that is now a directory.

    Without the guard the `OSError` leaves `rebuild()` through its own `except`
    clause, so `_let_go(kept)` never runs and the `-rollback` names stay on the
    daemon under a suffix documented as transient -- the same shape as the
    `-failed` leak measured on yulon-ubuntu 2026-09-09, arriving through the
    method written to prevent a leak.
    """
    installer = engine(Recorder(images=True))
    server_dir = tmp_path / "srv"
    (server_dir / dockerfile.DOCKERFILE).mkdir(parents=True)
    ctx = native.StageContext(
        server_dir=server_dir,
        client_dir=None,
        state=native.InstallState(ENTRY.id, "abc", "azerothcore"),
        cancel=None,
        secrets=native.Secrets(db_password="pw"),
    )
    said = list(installer._put_recipe_back(ctx, {dockerfile.DOCKERFILE: b"the recipe\n"}))
    assert (server_dir / dockerfile.DOCKERFILE).is_dir()
    assert [line for line in said if "could not be put back" in line], said
    assert not [line for line in said if "put back exactly as it was" in line], said


def test_a_rebuild_tuple_missing_the_stage_the_rollback_watches_refuses_before_tagging(
    tmp_path: Path,
) -> None:
    """The `if wrappers:` arm, driven. Nothing else in the suite reaches it.

    `stage_named()` refuses a family with no `build` at all; this is the other
    half -- a tuple that carries a `build` under another name, or that stopped
    adding `recreate` -- and what it produces without the check is not a missing
    stage but a rollback that silently never fires: `touched` would stay False
    through a recreate that really happened, so a server that failed to come
    back up would be "restored" without its containers being replaced.

    Refused BEFORE `_keep_rollback()`, which is what makes "Nothing was started"
    true, so the absence of any `tag:` call is asserted rather than implied.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    installer = engine(rec)
    installer.rebuild_stages = lambda: tuple(  # type: ignore[method-assign]
        stage for stage in engine(rec).rebuild_stages() if stage.name != "recreate"
    )
    rec.calls.clear()
    with pytest.raises(InstallerError) as raised:
        list(installer.rebuild(InstallOptions(server_dir=server_dir)))
    said = str(raised.value)
    assert "`recreate` stage to watch" in said, said
    assert "could not be rolled back" in said and "Nothing was started" in said, said
    assert not [c for c in rec.calls if c.startswith("tag:")], rec.calls
    assert "build" not in rec.calls, rec.calls


# -- T70: what the press actually SAYS when the compile fails ----------------

COMPOSE_CAPTURE = Path(__file__).resolve().parent / "data" / "compose-failed-build-epilogue.txt"
"""The real epilogue of a failed `docker compose build` — see `test_docker.py`."""


def test_a_rebuild_whose_compile_fails_names_the_compiler_error_in_its_refusal(
    tmp_path: Path,
) -> None:
    """T70, at the call site rather than at the function.

    `docker.last_words()` is not what a user reads; this sentence is. T38 was
    unit-tested on a bare `docker build` epilogue and shipped, and the press
    that reaches it — Rebuild, through `stage_build()` and `_check_run(…,
    from_build=True)` — went on printing the cmake command line for another
    four days, because the builder on this route is `docker compose build` and
    its epilogue has no `ERROR:` line (measured live 2026-09-16). A test that
    only ever calls the extractor cannot tell those two apart, so this one
    drives the real press with the real capture as the build's tail.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    tail = COMPOSE_CAPTURE.read_text(encoding="utf-8").splitlines()[-docker.KEEP_OUTPUT_LINES :]
    rec.build_result = docker.AttachedRun(1, tuple(tail))

    with pytest.raises(InstallerError) as raised:
        list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))

    said = str(raised.value)
    assert said.startswith("the build failed (exit 1). Its last words were: "), said
    assert "CbDuelBotUtil.cpp:11:16: fatal error" in said, said
    assert "use of undeclared identifier" in said, said
    # Lac's message, which this press printed verbatim on the live gate.
    assert "-DBoost_USE_STATIC_LIBS" not in said, said
    assert "$(nproc)" not in said, said
    assert "build" in rec.calls, rec.calls
