"""T64: "Update the server to latest…" — the route, its refusals and its restores.

The engine half. The button, its three-way dialog and the backup it chains are
in `test_controller_view.py`, beside the rebuild press they copy.

**What every test here is arranged around.** The one thing this route can do
that nothing else in the app can is leave a source tree and a running image
disagreeing: the folder a hundred commits ahead of the binary answering on the
port, with nothing on screen saying so and every later reading — the Modules
tab, a version line, a bug report — telling the truth about the wrong thing.
So the assertions are not "the route raised": they are what `heads` holds
afterwards, which is the fact a user cannot check and the one that quietly
breaks everything downstream. A test that only asserted the exception would
pass against a route that fetched, failed and walked away.
"""

from __future__ import annotations

import json
import subprocess
import threading
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

from tests.conftest import HANG_BOUND
from tests.support_native import ENTRY, VMAP_FIXTURE, Recorder, engine, install, lay_patch_sources
from tests.support_stop import compile_until_stopped, stop_when

# The CMaNGOS half of this file drives a REAL TBC install, and the machinery
# that lays the SQL a CMaNGOS plan names -- and the import gate it needs --
# lives in that family's own test module. Imported rather than re-spelled: a
# second copy of `lay_sql` would be a second fixture to keep in step with the
# catalog, and this file is about the update route rather than about what a
# CMaNGOS install needs on disk.
from tests.test_families_cmangos import ENTRY as TBC
from tests.test_families_cmangos import engine as tbc_engine
from tests.test_families_cmangos import install as tbc_install
from yulon import docker, git, resources, rmtree, runner
from yulon.apply import CLONE_DIRS
from yulon.catalog import native
from yulon.catalog.catalog import CatalogEntry, EmulatorSource, load_catalog
from yulon.catalog.families.cmangos import CmangosInstaller
from yulon.catalog.installer import (
    InstallerError,
    InstallOptions,
    RollbackNotDone,
    WorldStoppedAfterReadyError,
)
from yulon.controller import Controller, StartRefused
from yulon.docker import AttachedRun
from yulon.ui.widgets.log_panel import STOPPED_THEN_FAILED, LogPanel

PINNED = ENTRY.emulator.sources[0].rev or ""
"""The commit `catalog.json` pins the WotLK core to — the one every gate ran on."""

OLD = "a" * 40
NEW = "b" * 40


def _installed(rec: Recorder, tmp_path: Path) -> Path:
    """A finished WotLK install, with every source sitting on `OLD`.

    Driven through the real `run()` rather than assembled by hand, and that is
    not ceremony: this route's first act is `_refuse_unless_rebuildable()`,
    which reads the state file and the compose ownership, so a folder a test
    made itself would be testing the refusal rather than the route.
    """
    server_dir = tmp_path / "server"
    install(rec, server_dir)
    for source in ENTRY.emulator.sources:
        rec.heads[server_dir / source.dest] = OLD
    return server_dir


def _ready(tmp_path: Path) -> tuple[Recorder, Path]:
    """A recorder and an installed folder whose every source has somewhere to move to."""
    rec = Recorder()
    server_dir = _installed(rec, tmp_path)
    for source in ENTRY.emulator.sources:
        rec.upstream[server_dir / source.dest] = NEW
    # The install's own clones and calls go, so `clones == []` in a refusal test
    # means "this press fetched nothing" rather than "the install did not".
    rec.clones.clear()
    rec.calls.clear()
    return rec, server_dir


def _press(rec: Recorder, server_dir: Path, **kwargs: object) -> list[str]:
    return list(
        engine(rec).update_to_latest(InstallOptions(server_dir=server_dir), **kwargs)  # type: ignore[arg-type]
    )


def _heads(rec: Recorder, server_dir: Path) -> dict[str, str]:
    return {source.repo: rec.heads[server_dir / source.dest] for source in ENTRY.emulator.sources}


@pytest.fixture(autouse=True)
def _gated(monkeypatch: pytest.MonkeyPatch) -> None:
    """`test_families_cmangos.gated`, which `tbc_engine()` depends on.

    Copied rather than imported because an autouse fixture belongs to the
    module it is declared in: `tbc_engine()` attaches a `_test_gate` and the
    real `_gate()` would otherwise point a `MarkerGate` at a database that is
    not there. `raising=True` for its reason too -- renaming the method errors
    here instead of quietly adding an unused attribute.
    """

    def gate(self: CmangosInstaller, ctx: native.StageContext) -> native.ImportGate:
        attached = getattr(self, "_test_gate", None)
        assert attached is not None, "build CMaNGOS engines with tbc_engine(rec)"
        return attached

    monkeypatch.setattr(CmangosInstaller, "_gate", gate, raising=True)


def _tbc(tmp_path: Path) -> tuple[Recorder, Path, CmangosInstaller]:
    """A finished TBC install, every source on `OLD` with `NEW` waiting upstream.

    The CMaNGOS half of this file, and the family that carries both of the
    facts the AzerothCore half cannot show: a `*-db` source that must not move,
    and a patch this app wrote into the checkout.
    """
    rec = Recorder()
    server_dir = tmp_path / "tbc"
    tbc_install(rec, server_dir, tmp_path / "client")
    for source in TBC.emulator.sources:
        rec.heads[server_dir / source.dest] = OLD
        rec.upstream[server_dir / source.dest] = NEW
    rec.clones.clear()
    rec.calls.clear()
    made = tbc_engine(rec)
    # The hook that lays a patch's pre-image belongs to the INSTALL: it stands
    # in for what a real clone leaves behind. An update fetches into a tree that
    # is already there, so leaving it armed would quietly re-lay the pre-image
    # over whatever the test just arranged.
    rec.on_clone = None
    return rec, server_dir, made


# -- which sources move -----------------------------------------------------


def test_every_shipped_source_is_classified_and_only_the_db_repos_stay() -> None:
    """The `*-db` rule, enumerated against the catalog rather than asserted about one entry.

    `sources_that_move()` decides on a repository's NAME, which is a cheap rule
    with one expensive failure mode: a source it reads wrongly is either a world
    database dragged to upstream's tip under a server that was gated against a
    different one, or a core that silently never updates. Both are invisible
    from the outside, so the derivation is pinned here for every shipped source
    at once and a new entry lands on this test before it lands on a user.
    """
    held = {
        source.repo
        for entry in load_catalog().games
        for source in entry.emulator.sources
        if native.held_at_its_pin(source)
    }
    assert held == {"cmangos/tbc-db", "cmangos/classic-db"}, held
    # The two figures the `commits_since()` docstring quotes, DERIVED here so
    # the words cannot rot: round 2 of the cold review found them reading
    # "nine sources, seven shallow" when the catalog held ten and nine. A number
    # in prose that nothing recomputes is a number that was true once.
    every = [source for entry in load_catalog().games for source in entry.emulator.sources]
    assert len(every) == 11, [s.repo for s in every]
    assert sum(1 for s in every if s.depth is not None) == 10
    moving = {
        entry.id: tuple(s.repo for s in entry.emulator.sources if not native.held_at_its_pin(s))
        for entry in load_catalog().games
    }
    assert moving == {
        "wow-wotlk": ("mod-playerbots/azerothcore-wotlk", "mod-playerbots/mod-playerbots"),
        "wow-tbc": ("cmangos/mangos-tbc", "cmangos/playerbots"),
        "wow-vanilla": ("cmangos/mangos-classic", "cmangos/playerbots"),
        "wow-tortoise": ("tortoise-wow/tortoise-wow", "Sagiroth/TortoiseBots"),
        "wow-centurion": ("thomasjteachey/TrinityCore112",),
    }, moving


def test_the_server_update_dests_are_its_moving_sources_and_never_a_db_repo() -> None:
    """T146: where the button moves a checkout, read off the same predicate it moves by.

    The Modules tab offers "Update the server to latest…" on a folder the
    SERVER install cloned, so which folders those are must be the route's own
    answer. The `*-db` half is asserted on a synthetic source because no
    shipped entry puts a database repository in a clone folder -- and a rule no
    shipped data exercises is a rule nothing would notice breaking.

    Mutation: build the set from every source, not just the moving ones, and
    `modules/foo-db` is offered an update the route would never make.
    """
    entry = load_catalog().get("wow-wotlk")
    assert native.server_update_dests(entry) == {
        PurePosixPath("."),
        PurePosixPath("modules/mod-playerbots"),
    }
    held = EmulatorSource(repo="acme/foo-db", branch="master", dest="modules/foo-db")
    widened = entry.model_copy(
        update={
            "emulator": entry.emulator.model_copy(
                update={"sources": (*entry.emulator.sources, held)}
            )
        }
    )
    assert native.held_at_its_pin(held)
    assert PurePosixPath("modules/foo-db") not in native.server_update_dests(widened)
    assert PurePosixPath("modules/mod-playerbots") in native.server_update_dests(widened)


def test_only_wotlk_has_a_server_source_inside_a_folder_the_modules_tab_lists() -> None:
    """T146's survey, as a test so it cannot quietly stop being true.

    The Modules tab lists folders directly inside `apply.CLONE_DIRS`. Of every
    source an update moves, only WotLK's `modules/mod-playerbots` lands there:
    the CMaNGOS bots and Tortoise's TortoiseBots are nested under `src/`, which
    no clone folder is. A new entry that puts a source in one lands here first.
    """
    folders = {PurePosixPath(folder) for folder in CLONE_DIRS.values()}
    listed = {
        (entry.id, str(dest))
        for entry in load_catalog().games
        for dest in native.server_update_dests(entry)
        if dest.parent in folders
    }
    assert listed == {("wow-wotlk", "modules/mod-playerbots")}, listed


def test_the_route_is_offered_for_every_shipped_entry_and_by_the_flag_not_the_id() -> None:
    """The owner overrode the design's one-family recommendation: all four at once.

    Read off `install.native.update_to_latest` rather than off an id, and
    asserted BOTH ways — that the four are true, and that the wiring answers
    `None` the moment the flag is false. A test of only the first half would
    pass against a wiring that ignored the flag entirely, which is the shape
    this whole control's safety rests on: the default is false so a new entry
    gets the button by somebody deciding it does.
    """
    from yulon.install_wiring import update_to_latest_for_app

    assert {entry.id: entry.install.native.update_to_latest for entry in load_catalog().games} == {
        "wow-wotlk": True,
        "wow-tbc": True,
        "wow-vanilla": True,
        "wow-tortoise": True,
        "wow-centurion": True,
    }
    assert update_to_latest_for_app(ENTRY, Path("/srv/x")) is not None
    unflagged = ENTRY.install.native.model_copy(update={"update_to_latest": False})
    off = ENTRY.model_copy(
        update={"install": ENTRY.install.model_copy(update={"native": unflagged})}
    )
    assert update_to_latest_for_app(off, Path("/srv/x")) is None
    # A server inside a WSL distro is offered it too since T125: the route runs
    # there, on the distro's own Docker (`tests/test_wsl_update_route.py`).
    assert update_to_latest_for_app(ENTRY, Path("/srv/x"), wsl_distro="Ubuntu") is not None


def test_a_db_source_is_never_fetched_even_though_the_others_are(tmp_path: Path) -> None:
    """TBC's world database stays on its pin while the core and the bots move.

    The negative half of the rule, driven rather than read: `clones` is every
    `CloneSpec` the engine handed the seam, so a route that quietly moved
    `tbc-db` too would show up here even though every other assertion in this
    file would stay green.
    """
    rec, server_dir, tbc = _tbc(tmp_path)
    list(tbc.update_to_latest(InstallOptions(server_dir=server_dir)))

    fetched = [spec.url for spec in rec.clones]
    assert not any("tbc-db" in url for url in fetched), fetched
    assert rec.heads[server_dir / "src/tbc-db"] == OLD, "the world database was dragged forward"
    assert rec.heads[server_dir / "src/mangos-tbc"] == NEW


# -- the three refusals, and the three "could not ask" halves ---------------


def test_a_checkout_of_somebody_elses_repository_is_refused_and_nothing_is_fetched(
    tmp_path: Path,
) -> None:
    """`dml wow update`'s hard REMOTE_MISMATCH, not wow-manage's "pull anyway?".

    The prior art asked the user; this refuses. The question "pull from this
    remote anyway?" is one nobody has the information to answer at the moment
    it is asked, and the answer that breaks the server is the polite one.
    """
    rec, server_dir = _ready(tmp_path)
    rec.remotes[server_dir] = "https://github.com/azerothcore/azerothcore-wotlk.git"

    with pytest.raises(InstallerError) as raised:
        _press(rec, server_dir)

    assert "azerothcore/azerothcore-wotlk" in str(raised.value)
    assert "nothing was fetched" in str(raised.value)
    assert rec.clones == [], "a refused press fetched something"
    assert _heads(rec, server_dir) == {s.repo: OLD for s in ENTRY.emulator.sources}


def test_a_folder_git_will_not_answer_about_is_refused_rather_than_fetched_into(
    tmp_path: Path,
) -> None:
    """`remote_url()` answering None is "could not ask", and this route fails closed on it.

    The same direction every `None` in `git.py` is documented under. A route
    that read None as "no remote yet, so clone" would fetch into whatever is
    sitting there, which is the incident `read_claim()` records.
    """
    rec, server_dir = _ready(tmp_path)
    del rec.remotes[server_dir]

    with pytest.raises(InstallerError, match="not a checkout"):
        _press(rec, server_dir)
    assert rec.clones == []


def test_a_checkout_with_the_users_own_edits_is_refused_and_the_files_are_named(
    tmp_path: Path,
) -> None:
    """Both prior launchers stashed and popped; this refuses, and says which files.

    `_update()` is `reset --hard`, not `pull --ff-only`, so a stash popped onto
    the result lands on a tree that may share no history with the one the edit
    was made against — wow-manage's own comment admits what happens then ("the
    updated file wins"). A refusal that names the files is a worse evening and
    a better outcome.

    The names matter as much as the refusal: a user told "you have changes
    here" with no list has to go looking with no idea what for.
    """
    rec, server_dir = _ready(tmp_path)
    rec.edits[server_dir] = ("src/server/game/World.cpp",)

    with pytest.raises(InstallerError) as raised:
        _press(rec, server_dir)

    assert "src/server/game/World.cpp" in str(raised.value)
    assert rec.clones == []


def test_a_checkout_git_will_not_describe_is_refused_rather_than_assumed_clean(
    tmp_path: Path,
) -> None:
    """`local_edits()` answering None refuses. "Could not check" is not "nothing to lose"."""
    rec, server_dir = _ready(tmp_path)
    rec.git_reads = False

    with pytest.raises(InstallerError) as raised:
        _press(rec, server_dir)

    # BY THE WHOLE SENTENCE, because "could not ask git" alone is in the NEXT
    # refusal too. A mutation that deleted this guard survived a `match=` on
    # that fragment: with `local_edits()` ignored, the press fell through to the
    # local-commits read, which also answers None on this machine, and raised a
    # different refusal with the same words in it. A substring that two
    # branches share proves neither of them ran.
    said = str(raised.value)
    assert said.startswith("Yu'lon could not ask git whether"), said
    assert "has changes of your own in it" in said, said
    assert rec.clones == []


def test_a_checkout_with_local_commits_is_refused_though_its_tree_is_clean(
    tmp_path: Path,
) -> None:
    """The refusal NEITHER prior launcher makes, and the one that loses the most.

    A checkout somebody committed their work into is perfectly clean by `git
    status` — `git.HistoryReader` says so — so the dirty-tree guard above lets
    it straight through. `reset --hard FETCH_HEAD` then moves HEAD off those
    commits and they are reachable only through the reflog.

    Driven with NO edits on purpose: a route that happened to refuse because
    the tree was also dirty would pass a test that set both.
    """
    rec, server_dir = _ready(tmp_path)
    assert rec.edits == {}
    rec.diverged.add(server_dir)

    with pytest.raises(InstallerError) as raised:
        _press(rec, server_dir)

    assert "commits that upstream does not" in str(raised.value)
    assert "reflog" in str(raised.value)
    assert rec.clones == []


def test_the_refusal_says_the_remote_was_unreachable_and_never_that_you_have_commits(
    tmp_path: Path,
) -> None:
    """`no_local_commits()` fetches, so `None` is usually "the network is down".

    Its own docstring makes the point: an offline user must be told the remote
    could not be reached, and never that they have commits of their own,
    because that answer establishes nothing about that. The two refusals are
    one line apart in the source and the wrong one is a lie about somebody's
    work.
    """
    rec, server_dir = _ready(tmp_path)

    def cannot_reach(dest: Path, branch: str | None) -> bool | None:
        return None

    with pytest.raises(InstallerError) as raised:
        list(
            engine(rec, no_local_commits=cannot_reach).update_to_latest(
                InstallOptions(server_dir=server_dir)
            )
        )

    said = str(raised.value)
    assert "did not answer" in said
    assert "commits of its own" in said and "carries commits that upstream" not in said


def test_the_second_source_being_refused_leaves_the_first_one_unfetched(
    tmp_path: Path,
) -> None:
    """Every source is checked before ANY source is fetched, and this is why.

    A route that checked-and-moved one source at a time would, on this input,
    fetch the core and then refuse the module — leaving a tree one version
    ahead of the image with no rollback started and nothing on screen about it.
    The check loop and the move loop are separate for exactly this, and nothing
    but this test can see that they are.
    """
    rec, server_dir = _ready(tmp_path)
    rec.edits[server_dir / "modules/mod-playerbots"] = ("src/Bot.cpp",)

    with pytest.raises(InstallerError, match="src/Bot.cpp"):
        _press(rec, server_dir)

    assert rec.clones == []
    assert _heads(rec, server_dir) == {s.repo: OLD for s in ENTRY.emulator.sources}


# -- the move itself --------------------------------------------------------


def test_the_move_asks_for_the_tip_and_the_return_asks_for_the_pin(tmp_path: Path) -> None:
    """`rev=None` is the whole difference between the two directions.

    `git.RunnerGit.clone()` on an existing `.git` runs `_update()` — fetch, then
    `reset --hard FETCH_HEAD` — and then `_pin()`, which returns immediately for
    `rev=None` and detaches onto `rev` otherwise. So the same seam is both
    directions and the spec is the only thing that says which, which makes the
    spec the thing to assert.
    """
    rec, server_dir = _ready(tmp_path)
    _press(rec, server_dir)
    assert [spec.rev for spec in rec.clones] == [None, None]
    assert _heads(rec, server_dir) == {s.repo: NEW for s in ENTRY.emulator.sources}

    rec.clones.clear()
    _press(rec, server_dir, to_pin=True)
    assert [spec.rev for spec in rec.clones] == [source.rev for source in ENTRY.emulator.sources]
    assert rec.heads[server_dir] == PINNED


def test_the_move_carries_the_sources_own_depth_and_branch(tmp_path: Path) -> None:
    """The spec is built from the catalog source, not from defaults.

    `CloneSpec.depth` defaults to 1 and AzerothCore's core repo must pass None:
    its CMake reads the revision out of git metadata and a shallow clone hands
    a three-hour build the wrong answer. A route that built a bare `CloneSpec`
    would truncate a full clone in place and the build would still finish.
    """
    rec, server_dir = _ready(tmp_path)
    _press(rec, server_dir)
    for spec, source in zip(rec.clones, engine(rec).sources_that_move(), strict=True):
        assert (spec.url, spec.branch, spec.depth) == (source.url, source.branch, source.depth)


def test_a_source_that_was_already_at_the_tip_says_so_rather_than_claiming_a_move(
    tmp_path: Path,
) -> None:
    """ "Already on abcdefg; nothing moved" and not a sha pair that is the same twice.

    The route reads the head again after the fetch instead of assuming it
    moved, which is what lets this line exist at all — and the line is what
    tells somebody who pressed the button and waited an hour that upstream had
    not published anything.
    """
    rec, server_dir = _ready(tmp_path)
    rec.upstream[server_dir] = OLD

    said = "\n".join(_press(rec, server_dir))
    assert f"already on {OLD[:7]}" in said
    assert f"{OLD[:7]} -> {NEW[:7]}" in said, "the module moved and was not reported"


def test_the_route_refuses_to_return_to_a_pin_an_entry_does_not_have(tmp_path: Path) -> None:
    """A source with no `rev` has no tested commit, and the way back cannot be invented.

    Every shipped entry pins every source, so this is a guard against a future
    one — and it refuses before anything is fetched rather than moving what it
    can and leaving the rest.
    """
    rec, server_dir = _ready(tmp_path)
    unpinned = ENTRY.model_copy(
        update={
            "emulator": ENTRY.emulator.model_copy(
                update={
                    "sources": tuple(
                        s.model_copy(update={"rev": None}) for s in ENTRY.emulator.sources
                    )
                }
            )
        }
    )
    made = engine(rec)
    made.entry = unpinned

    with pytest.raises(InstallerError, match="no tested commit to return to"):
        list(made.update_to_latest(InstallOptions(server_dir=server_dir), to_pin=True))
    assert rec.clones == []


# -- what the app owns inside a checkout ------------------------------------


def test_the_wotlk_compose_file_is_the_apps_and_not_read_as_the_users_work() -> None:
    """Found by a test, and it broke this feature in both directions at once.

    AzerothCore's core source has `dest: "."` -- the server directory IS the
    checkout -- and that repository tracks its own `docker-compose.yml`, which
    `stage_generate_compose()` overwrites with this app's marked one. So every
    healthy WotLK install has a modified tracked file in its checkout, and the
    first version of this route:

    * reported it through `local_edits()` and refused the update on every WotLK
      install there has ever been; and, with that silenced,
    * reset the checkout, which put upstream's compose back, after which
      `rebuild()`'s own guard refused with "these compose files were not
      written by Yu'lon" -- the press having broken the install it was updating.

    Both halves are the same fact, which is why one method answers it.
    """
    from yulon.catalog import composegen

    made = engine(Recorder())
    assert made.app_written_paths(Path("/srv/x")) == {".": composegen.COMPOSE_FILES}


def test_a_cmangos_entry_owns_its_patch_paths_and_no_compose_file(tmp_path: Path) -> None:
    """Its sources all live under `src/`, so a reset there cannot reach the server dir.

    Read out of the patch FILES rather than written down a second time in the
    catalog: the guard's exception list and the thing it is an exception for
    are then the same bytes, so a patch that grows a hunk in a new file cannot
    leave the guard behind.
    """
    _rec, server_dir, tbc = _tbc(tmp_path)
    owned = tbc.app_written_paths(server_dir)
    assert set(owned) == {"src/mangos-tbc"}, owned
    assert all(path.startswith("contrib/") for path in owned["src/mangos-tbc"]), owned


def test_the_apps_own_dirty_compose_does_not_refuse_the_update(tmp_path: Path) -> None:
    """The exemption DRIVEN, not just derived — a surviving mutation, round 2.

    `test_the_wotlk_compose_file_is_the_apps_and_not_read_as_the_users_work`
    asserts what `app_written_paths()` ANSWERS; nothing asserted that the answer
    reaches `local_edits()`. So `local_edits(dest, ours.get(...))` mutated to
    `local_edits(dest, ())` and all 38 tests stayed green — while the shipped
    app would have refused every WotLK update there has ever been, because that
    checkout's `docker-compose.yml` is modified on every healthy install.

    The dirty path here is the app's OWN, seeded exactly as an install leaves
    it. The press must go all the way through.
    """
    from yulon.catalog import composegen

    rec, server_dir = _ready(tmp_path)
    rec.edits[server_dir] = (composegen.BASE_FILE,)

    said = _press(rec, server_dir)

    assert _heads(rec, server_dir) == {s.repo: NEW for s in ENTRY.emulator.sources}
    assert any("is running on" in line for line in said), said


def test_a_cmangos_patch_path_left_dirty_by_the_install_does_not_refuse_the_update(
    tmp_path: Path,
) -> None:
    """The same exemption on the family it was written for, through the real patch files.

    `patch-sources` edits `contrib/vmap_extractor/...` inside the core checkout
    on every CMaNGOS install, so that tree is dirty at those exact paths for as
    long as the install exists. The paths are not written down here: they are
    read back out of `app_written_paths()`, which reads them out of the patch
    file — so a patch that grows a hunk in a new file cannot leave this test
    behind either.
    """
    rec, server_dir, tbc = _tbc(tmp_path)
    owned = tbc.app_written_paths(server_dir)["src/mangos-tbc"]
    assert owned, "the entry carries no patch paths, so this proves nothing"
    rec.edits[server_dir / "src/mangos-tbc"] = owned

    list(tbc.update_to_latest(InstallOptions(server_dir=server_dir)))

    assert rec.heads[server_dir / "src/mangos-tbc"] == NEW


def test_a_users_edit_beside_the_apps_own_still_refuses(tmp_path: Path) -> None:
    """The exemption is a subtraction, not an off switch.

    A guard that answered "this checkout has app-written paths in it, carry on"
    would pass the two tests above and throw away the user's work, which is the
    thing the guard exists for. The compose file is exempt; the file next to it
    is not, and only IT is named.
    """
    from yulon.catalog import composegen

    rec, server_dir = _ready(tmp_path)
    rec.edits[server_dir] = (composegen.BASE_FILE, "src/server/game/World.cpp")

    with pytest.raises(InstallerError) as raised:
        _press(rec, server_dir)

    said = str(raised.value)
    assert "src/server/game/World.cpp" in said
    assert composegen.BASE_FILE not in said, "the app's own file was blamed on the user"
    assert rec.clones == []


def test_the_update_writes_this_apps_compose_back_over_the_one_the_fetch_restored(
    tmp_path: Path,
) -> None:
    """The restore half, end to end: the press finishes and `rebuild()` does not refuse.

    Driven by making the clone do what a real `reset --hard` does to that
    file -- put upstream's unmarked copy back -- and then requiring the whole
    press to succeed. Asserting only that `generate-compose` was called would
    pass against a call that then refused; the marker in the file afterwards is
    the fact that matters, because it is what `rebuild()` reads.
    """
    from yulon.catalog import composegen

    rec, server_dir = _ready(tmp_path)
    base = server_dir / composegen.BASE_FILE
    assert composegen.GENERATED_MARKER in base.read_text(encoding="utf-8")

    upstream_reset = rec.on_clone

    def reset_the_tracked_compose(dest: Path) -> None:
        if dest == server_dir:
            base.write_text("services:\n  ac-database:\n    image: mysql:8.4\n", encoding="utf-8")
        if upstream_reset is not None:
            upstream_reset(dest)

    rec.on_clone = reset_the_tracked_compose
    _press(rec, server_dir)

    assert composegen.GENERATED_MARKER in base.read_text(
        encoding="utf-8"
    ), "the fetch put upstream's compose back and nothing wrote ours again"


# -- T173: a CMaNGOS update leaves the compose files alone --------------------

CMANGOS_GAMES = [load_catalog().get(game) for game in ("wow-tbc", "wow-vanilla", "wow-tortoise")]
"""Tortoise with the two games whose carried patch made the guard non-empty (T173)."""

PLAYERS_ZONE = "    environment:\n      TZ: Europe/Oslo\n"
"""What a player adds to the world service in the override by hand (T171's report)."""


def _any_client(tmp_path: Path) -> Path:
    """A client folder every CMaNGOS entry's `ClientSpec` accepts (TBC's, plus Vanilla's file)."""
    client = tmp_path / "client"
    (client / "Data" / "enUS").mkdir(parents=True)
    for name in ("common", "expansion", "patch", "patch-2", "patch-3", "misc", "dbc"):
        (client / "Data" / f"{name}.MPQ").write_bytes(b"MPQ\x1a")
    (client / "Data" / "enUS" / "locale-enUS.MPQ").write_bytes(b"MPQ\x1a")
    return client


def _mmaps_as(rec: Recorder, entry: CatalogEntry) -> Callable[..., AttachedRun]:
    """`rec.run_container`, with the map generator finishing on the entry's own success code.

    Tortoise's MoveMapGen exits 1 when it is done (`mmaps.success_codes`), and
    one `run_result` answers every container run, so the generator is answered
    on its own here and every other tool as before.
    """
    native_block = entry.install.native
    assert native_block is not None and native_block.cmangos is not None, entry.id
    mmaps = native_block.cmangos.mmaps
    code = mmaps.success_codes[0]

    def run(spec: Any, *args: Any, **kwargs: Any) -> AttachedRun:
        if spec.argv[0] != mmaps.argv[0] or code == 0:
            return rec.run_container(spec, *args, **kwargs)
        saved = rec.run_result, rec.success_returncodes
        rec.run_result, rec.success_returncodes = AttachedRun(code, ("done",)), (code,)
        try:
            return rec.run_container(spec, *args, **kwargs)
        finally:
            rec.run_result, rec.success_returncodes = saved

    return run


def _etc_of(rec: Recorder, entry: CatalogEntry) -> Callable[[str, str, Path], None]:
    """`rec.copy_from_image`, laying every conf template `entry`'s table names, nested ones too.

    The double lays TBC's four `.conf.dist` names; Tortoise's image holds
    `aiplayerbot.conf` with no `.dist` and `modules/tortoise_bots.conf.dist`
    (`conf.template_of()`), and the conf stage refuses an image without them.
    """
    from yulon.catalog.families import conf

    native_block = entry.install.native
    assert native_block is not None and native_block.cmangos is not None, entry.id
    templates = [
        conf.template_of(name, patch) for name, patch in native_block.cmangos.conf.files.items()
    ]

    def copy(image: str, src: str, dest: Path) -> None:
        rec.copy_from_image(image, src, dest)
        if src.endswith(".conf.dist"):
            return
        for template in templates:
            path = dest / template
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(rec.conf_dist.get(Path(template).name, ""), encoding="utf-8")

    return copy


def _counts_realms(rec: Recorder) -> Callable[..., str]:
    """`rec.sql_query`, answering a COUNT of the realm table as a count (Tortoise's import check).

    The double answers every `realmlist` statement with the realm row, which is
    what the realm guard reads; Tortoise's plan also counts that table.
    """

    def query(
        container: str,
        client: str,
        password: str,
        schema: str | None,
        statement: str,
        **kwargs: Any,
    ) -> str:
        answer = rec.sql_query(container, client, password, schema, statement, **kwargs)
        return rec.query_answer if "COUNT(*) FROM realmlist" in statement else answer

    return query


def _cmangos(tmp_path: Path, entry: CatalogEntry) -> tuple[Recorder, Path, CmangosInstaller]:
    """`_tbc()` for any CMaNGOS entry: finished through the real `run()`, `NEW` upstream."""
    rec = Recorder()
    server_dir = tmp_path / entry.id
    tbc_install(
        rec,
        server_dir,
        _any_client(tmp_path),
        entry=entry,
        run_container=_mmaps_as(rec, entry),
        copy_from_image=_etc_of(rec, entry),
        sql_query=_counts_realms(rec),
    )
    for source in entry.emulator.sources:
        rec.heads[server_dir / source.dest] = OLD
        rec.upstream[server_dir / source.dest] = NEW
        if source.follow == "releases":
            # T126: the newest release, and GitHub placing it ahead of `OLD`.
            rec.releases[source.repo] = ("v-new", NEW)
            rec.github[source.repo] = 1
    rec.clones.clear()
    rec.calls.clear()
    rec.on_clone = None
    return rec, server_dir, tbc_engine(rec, entry=entry)


def _compose_on_disk(server_dir: Path) -> dict[str, tuple[bytes, int, int]]:
    """Each compose file and `.env` as bytes, mtime and inode: a rewrite moves one of the three."""
    from yulon.catalog import composegen

    found: dict[str, tuple[bytes, int, int]] = {}
    for name in (*composegen.COMPOSE_FILES, ".env"):
        path = server_dir / name
        if path.exists():
            info = path.stat()
            found[name] = (path.read_bytes(), info.st_mtime_ns, info.st_ino)
    return found


def _renders(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Every folder `stage_generate_compose` is run on from here on, in order."""
    ran: list[Path] = []
    real = CmangosInstaller.stage_generate_compose

    def spy(self: CmangosInstaller, ctx: native.StageContext) -> Iterator[str]:
        ran.append(ctx.server_dir)
        yield from real(self, ctx)

    monkeypatch.setattr(CmangosInstaller, "stage_generate_compose", spy, raising=True)
    return ran


@pytest.mark.parametrize("to_pin", [False, True], ids=["to-latest", "to-pin"])
@pytest.mark.parametrize("entry", CMANGOS_GAMES, ids=lambda entry: entry.id)
def test_a_cmangos_update_never_rewrites_the_compose_files_the_players_edit_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: CatalogEntry, to_pin: bool
) -> None:
    """No CMaNGOS source is cloned into the server folder, so no fetch can touch its compose.

    `_rewrite_what_we_own()` asked `app_written_paths()` whether to render, and
    on TBC and Vanilla that mapping carries the vmap-extractor patch's paths, so
    both directions re-rendered all three compose files (T173): a player's own
    line in the override was thrown away, and a pre-T169 install gained its
    `./logs` bind without the `LogsDir` its Repair sets with it (the next test).
    Tortoise carries no patch and was never touched -- measured live on the
    T168 update, mtimes unchanged -- which is why it is here: the same answer
    for all three.

    The press must still finish, the sources must move, and the files must be
    the same bytes, the same mtime and the same inode.
    """
    from yulon.catalog import composegen

    rec, server_dir, made = _cmangos(tmp_path, entry)
    override = server_dir / composegen.OVERRIDE_FILE
    before = override.read_text(encoding="utf-8")
    edited = before.replace("    ports:\n", PLAYERS_ZONE + "    ports:\n", 1)
    assert edited != before, "the override has no ports block to add the player's line above"
    override.write_text(edited, encoding="utf-8")
    kept = _compose_on_disk(server_dir)
    ran = _renders(monkeypatch)

    said = list(made.update_to_latest(InstallOptions(server_dir=server_dir), to_pin=to_pin))

    assert any("is running on" in line for line in said), said
    moving = made.sources_that_move()
    assert {s.repo: rec.heads[server_dir / s.dest] for s in moving} == {
        s.repo: (s.rev if to_pin else NEW) for s in moving
    }
    assert PLAYERS_ZONE in override.read_text(encoding="utf-8"), "the player's own line is gone"
    assert _compose_on_disk(server_dir) == kept
    assert ran == [], f"{entry.id}: the update rendered compose files it does not own a copy of"
    assert not any(line.startswith("Wrote docker-compose") for line in said), said


@pytest.mark.parametrize("entry", CMANGOS_GAMES[:2], ids=lambda entry: entry.id)
def test_an_update_leaves_a_pre_t169_install_offered_the_repair_that_sets_logs_dir(
    tmp_path: Path, entry: CatalogEntry
) -> None:
    """T169's Repair adds the `./logs` bind AND `LogsDir` together, and only it may.

    A TBC or Vanilla install made before T169 has neither. The Update re-rendered
    the compose file (T173) and so added the bind alone: the conf still said
    `""`, the server kept writing into `bin/` inside the container, the folder
    stayed empty -- and the Repair that would have set the conf read the file as
    `current` and was never offered again, so nothing ever would.
    """
    from tests.test_tbc_vanilla_logs import before_t169, write_confs
    from yulon.catalog import composegen

    rec, server_dir, made = _cmangos(tmp_path, entry)
    before_t169(server_dir)
    confs = write_confs(server_dir)
    old_confs = {name: path.read_bytes() for name, path in confs.items()}
    base = server_dir / composegen.BASE_FILE
    old_base = base.read_bytes()
    assert made.base_compose_check(InstallOptions(server_dir=server_dir)).state == "stale"

    list(made.update_to_latest(InstallOptions(server_dir=server_dir)))

    assert base.read_bytes() == old_base, "the update added the bind without the setting"
    assert not (server_dir / "logs").exists()
    assert {name: path.read_bytes() for name, path in confs.items()} == old_confs
    check = made.base_compose_check(InstallOptions(server_dir=server_dir))
    assert check.state == "stale", check
    assert any("LogsDir" in line for line in check.settings), check


# -- the restores -----------------------------------------------------------


def test_a_patch_that_no_longer_applies_stops_the_press_and_puts_every_source_back(
    tmp_path: Path,
) -> None:
    """The design's hard rule: never build without a patch this project carries.

    The patch is written against the pinned commit. Upstream moving under it is
    the ordinary case, not an edge, and the two outcomes then available are
    "compile the unpatched source" — which is the defect the patch exists to
    stop, arriving by a new route — and "put everything back". This proves the
    second: the refusal is the patch's own sentence, and BOTH sources are back
    on the commit they were on.
    """
    rec, server_dir, tbc = _tbc(tmp_path)
    # Upstream has moved under the patch: the file it edits no longer holds the
    # lines it expects. Written through the same `on_clone` path the install
    # used, so what changes is the TREE and not the patch.
    core = server_dir / "src/mangos-tbc"
    edited = next(iter(rec.edits), None)
    assert edited is None
    for target in core.rglob("*.cpp"):
        target.write_text("something else entirely\n", encoding="utf-8")
    rec.on_clone = None

    with pytest.raises(InstallerError) as raised:
        list(tbc.update_to_latest(InstallOptions(server_dir=server_dir)))

    said = str(raised.value)
    assert "does not apply" in said or "no such file" in said, said
    assert native.SOURCES_PUT_BACK_NOTE in said
    assert rec.heads[core] == OLD, "the core was left on upstream's tip under an old image"
    assert rec.heads[server_dir / "src/mangos-tbc/src/modules/Bots"] == OLD
    assert "build" not in rec.calls[-1], "it compiled after the patch refused"


def test_the_carried_patch_is_written_into_the_moved_source_before_anything_compiles(
    tmp_path: Path,
) -> None:
    """The half the dry run does not do, and without which the compile is of unpatched code.

    `rebuild_stages()` excludes `patch-sources` -- a rebuild does not re-clone,
    so there was never anything to re-patch -- and this route DOES re-clone, by
    `reset --hard` onto upstream's tip, which discards the patch the install
    wrote. A mutation that ran the dry run and then skipped the write survived
    every other test in this file: the press succeeded, said nothing wrong, and
    compiled the defect the patch exists to stop.

    Asserted on the BYTES in the checkout, not on a line in the log: the log
    says what the code believes and the file is what the compiler reads.
    """
    rec, server_dir, tbc = _tbc(tmp_path)
    core = server_dir / "src/mangos-tbc"
    patched = sorted(core.rglob("*.cpp"))
    assert patched, "the fixture laid no file for the patch to edit"
    # The fetch's own effect on a patched tree: `reset --hard` puts upstream's
    # UNPATCHED bytes back. `lay_patch_sources` is what lays that pre-image, so
    # re-arming it as the clone hook is the fetch, modelled.
    patched_now = {path: path.read_bytes() for path in patched}
    rec.on_clone = lay_patch_sources(TBC)

    list(tbc.update_to_latest(InstallOptions(server_dir=server_dir)))

    after = {path: path.read_bytes() for path in patched}
    assert after == patched_now, "the fetch reset the tree and nothing wrote the patch again"
    pre_image = {path: (VMAP_FIXTURE / path.name).read_bytes() for path in patched}
    assert after != pre_image, "the fixture and the patched file are the same bytes"


def test_the_patches_are_resolved_dry_before_a_single_one_is_written(tmp_path: Path) -> None:
    """The order the design turns on, and the only thing that makes the restore free.

    A press that wrote each patch as it resolved it would, with two carried
    patches and the second no longer applying, have edited the first checkout
    before it found out -- and `_put_sources_back()` moves HEAD, which would
    then throw that edit away along with everything else. Only one patch ships
    today, so the guarantee is asserted on the ORDER of what the route says:
    every patch is checked, and only then is any applied.
    """
    rec, server_dir, tbc = _tbc(tmp_path)
    said = list(tbc.update_to_latest(InstallOptions(server_dir=server_dir)))

    checked = next(i for i, line in enumerate(said) if line.startswith("Checking "))
    settled = next(i for i, line in enumerate(said) if line.startswith("Every source patch"))
    applied = next(i for i, line in enumerate(said) if line.startswith("Applying "))
    assert checked < settled < applied, said
    assert not any(line.startswith("Applying ") for line in said[:settled]), said


def test_a_write_that_the_disk_refuses_still_puts_the_sources_back(tmp_path: Path) -> None:
    """The handler caught `InstallerError` only, and a bare `OSError` skipped the restore.

    Everything between the first fetch and the compile WRITES —
    `_rewrite_what_we_own()` renders three compose files through
    `composegen.write_plan()`, whose `path.write_text(...)` carries no `try`, and
    `apply_carried_patches()` writes into the checkout. A full disk, a read-only
    mount or a file somebody chmod'ed comes out as a plain `OSError` that no
    `InstallerError` wraps, and on it the sources stayed where the fetch put
    them (cold review round 2, 2026-09-16).

    Driven with a real read-only file rather than a raise injected at the seam:
    the point is that the exception escapes the write path untranslated, which
    only the real write can show.
    """
    from yulon.catalog import composegen

    rec, server_dir = _ready(tmp_path)
    base = server_dir / composegen.BASE_FILE

    def reset_then_lock(dest: Path) -> None:
        if dest != server_dir:
            return
        # What the fetch really does to a file the repo tracks and this app
        # overwrote: upstream's copy comes back. The content now DIFFERS from
        # what `write_plan()` wants, so it will try to write -- and cannot.
        base.write_text("services:\n  ac-database:\n    image: mysql:8.4\n", encoding="utf-8")
        base.chmod(0o444)

    rec.on_clone = reset_then_lock
    try:
        with pytest.raises(InstallerError) as raised:
            _press(rec, server_dir)
    finally:
        base.chmod(0o644)

    assert native.SOURCES_PUT_BACK_NOTE in str(raised.value)
    assert _heads(rec, server_dir) == {s.repo: OLD for s in ENTRY.emulator.sources}
    assert any(call.startswith("restore:") for call in rec.calls)


def test_a_rebuild_that_fails_puts_the_sources_back_so_folder_and_image_agree(
    tmp_path: Path,
) -> None:
    """The failure this route exists to survive, and the one nothing else covers.

    `rebuild()` already puts the IMAGE back — its rollback tags are unchanged
    and this route does not touch them. What it cannot do is put the SOURCE
    back, because it never moved one. So a compile that fails after the fetch
    leaves a checkout at upstream's tip under a binary built from the old
    commit, and every later reading of that folder is a true statement about
    the wrong thing.

    Driven through a build that fails rather than through a raised
    `InstallerError` of our own: `rebuild()` has four ways to fail and this is
    the one a user meets — a module that no longer compiles.
    """
    rec, server_dir = _ready(tmp_path)
    rec.build_result = AttachedRun(2, ("error: no",))

    with pytest.raises(InstallerError) as raised:
        _press(rec, server_dir)

    assert native.SOURCES_PUT_BACK_NOTE in str(raised.value)
    assert _heads(rec, server_dir) == {s.repo: OLD for s in ENTRY.emulator.sources}
    assert any(call.startswith("restore:") for call in rec.calls)


def test_the_restore_writes_this_apps_own_compose_back_into_the_checkout_it_reverted(
    tmp_path: Path,
) -> None:
    """The half a `checkout --force` undoes, and without which the recovery breaks the install.

    `restore_rev()` puts the checkout on the old commit and, with it, puts
    UPSTREAM's copy of every tracked file back -- including the ones this app
    overwrote. On AzerothCore that is `docker-compose.yml`. A restore that
    stopped at the sha would leave the folder on the right commit with a compose
    file this app does not recognise, and the NEXT press of anything, Rebuild
    included, would refuse it with "these compose files were not written by
    Yu'lon": the press would have fixed the sha and broken the install.

    Modelled by making the restore do what a real `checkout --force` does to
    that file. The assertion is on the marker, because the marker is exactly
    what the next press reads.
    """
    from yulon.catalog import composegen

    rec, server_dir = _ready(tmp_path)
    base = server_dir / composegen.BASE_FILE
    upstream = "services:\n  ac-database:\n    image: mysql:8.4\n"

    def restore_like_git(dest: Path, rev: str) -> None:
        rec.heads[dest] = rev
        if dest == server_dir:
            base.write_text(upstream, encoding="utf-8")

    rec.build_result = AttachedRun(2, ("error: no",))
    with pytest.raises(InstallerError):
        list(
            engine(rec, restore_rev=restore_like_git).update_to_latest(
                InstallOptions(server_dir=server_dir)
            )
        )

    assert rec.heads[server_dir] == OLD
    assert composegen.GENERATED_MARKER in base.read_text(
        encoding="utf-8"
    ), "the restore reverted the compose file and left the install unrebuildable"


def test_a_source_that_will_not_go_back_is_said_out_loud_with_the_command_to_fix_it(
    tmp_path: Path,
) -> None:
    """The one state this route can end in where the folder and the image disagree.

    A restore that fails must not replace the sentence that says what went
    wrong, and it must not be swallowed either — `_put_sources_back()` is on an
    already-failing path, so it says so per source and carries on. What the
    user gets is the contradiction in front of them plus two words of git,
    rather than a comfortable closing line that is false.
    """
    rec, server_dir = _ready(tmp_path)
    rec.build_result = AttachedRun(2, ("error: no",))
    rec.restore_error = git.GitError("index.lock exists")
    # Collected from the generator rather than from `_press`, because the lines
    # are what this test is about and `list()` loses them when the generator
    # raises at the end.
    said: list[str] = []

    with pytest.raises(InstallerError):
        for line in engine(rec).update_to_latest(InstallOptions(server_dir=server_dir)):
            said.append(line)

    text = "\n".join(said)
    assert "could NOT be put back" in text, text
    # `--force`, the same flag `restore_rev()` uses, because the command a user
    # is handed has to be the one that works: without it git refuses whenever a
    # tracked file differs in the working tree and between the commits, which is
    # the state this folder is in.
    assert f"checkout --detach --force {OLD}" in text
    assert rec.heads[server_dir] == NEW, "the head moved back though the restore refused"


def test_a_cancel_between_the_sources_puts_back_the_one_that_moved(tmp_path: Path) -> None:
    """Stop is a failure like any other here, and the half-moved tree is the reason.

    `_check_cancel()` raises an `InstallerError`, so it lands in the same
    handler — which is the point: a Stop pressed after the core has been reset
    and before the module has is exactly the disagreement this route is
    arranged to prevent, and it is the easiest one for a user to cause.
    """
    import threading

    rec, server_dir = _ready(tmp_path)
    stop = threading.Event()
    made = engine(rec)
    lines: list[str] = []

    with pytest.raises(InstallerError):
        for line in made.update_to_latest(InstallOptions(server_dir=server_dir), cancel=stop):
            lines.append(line)
            if line.startswith("mod-playerbots/azerothcore-wotlk:"):
                stop.set()

    assert rec.heads[server_dir] == OLD, "the core stayed on upstream's tip after a Stop"
    assert native.SOURCES_PUT_BACK_NOTE in "\n".join(lines) or any(
        "put back" in line for line in lines
    )


# -- the record and the version line ---------------------------------------


def test_what_the_server_was_built_from_survives_a_write_and_a_read(tmp_path: Path) -> None:
    """The state key round trip, through the real writer and the real parser.

    Asserted through the FILE and not through the dataclass, because the file
    is what an older build reads: `source_revs` is additive so `version` stays
    1, and a shape that only round-tripped in memory would be a key nothing on
    disk could hold.
    """
    rec, server_dir = _ready(tmp_path)
    _press(rec, server_dir)

    payload = json.loads((server_dir / native.STATE_FILE).read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["source_revs"]["mod-playerbots/azerothcore-wotlk"] == {
        "built": f"{NEW[:7]} · 2026-09-16",
        "pin": PINNED,
        "ahead": 12,
    }
    read = native.read_state(server_dir, valid=())
    assert read is not None
    assert read.rev_for("mod-playerbots/azerothcore-wotlk") == native.SourceRev(
        repo="mod-playerbots/azerothcore-wotlk",
        built=f"{NEW[:7]} · 2026-09-16",
        pin=PINNED,
        ahead=12,
    )


def test_a_press_that_succeeds_does_not_put_back_the_error_the_last_one_recorded(
    tmp_path: Path,
) -> None:
    """A working, updated server whose record says the update failed.

    `rebuild()` clears `last_error` on success (`_clear_error()`, written after
    the m910q reading of 2026-09-02), and the state this method was handed was
    read BEFORE that — so writing `replace(state, ...)` put the cleared sentence
    straight back. The input is two presses: one that fails, then one that
    works. Found by the cold review of 2026-09-16, not by a test.
    """
    rec, server_dir = _ready(tmp_path)
    rec.build_result = AttachedRun(2, ("error: no",))
    with pytest.raises(InstallerError):
        _press(rec, server_dir)
    assert json.loads((server_dir / native.STATE_FILE).read_text(encoding="utf-8"))["last_error"]

    rec.build_result = AttachedRun(0, ("built",))
    _press(rec, server_dir)

    payload = json.loads((server_dir / native.STATE_FILE).read_text(encoding="utf-8"))
    assert payload["last_error"] == "", payload["last_error"]
    assert payload["source_revs"], "the record this press exists to write was lost with it"


def test_an_install_that_was_never_updated_writes_no_source_revs_at_all(tmp_path: Path) -> None:
    """An ordinary install's state file is byte for byte what it was before T64.

    The key is written only when there is one, so the file every install in
    existence carries does not gain an empty mapping for a feature most of them
    will never press — and the version line reads "nothing to say" off the
    absence rather than off a sentinel.
    """
    rec = Recorder()
    server_dir = _installed(rec, tmp_path)
    payload = json.loads((server_dir / native.STATE_FILE).read_text(encoding="utf-8"))
    assert "source_revs" not in payload
    assert native.source_revs_line(native.read_state(server_dir, valid=())) == ""


def test_a_record_that_could_not_count_the_distance_never_prints_as_being_on_the_pin() -> None:
    """`None` and `0` are different sentences, and collapsing them is a lie in both directions.

    Zero is "you are on the tested pin", which is what a return press produces.
    `None` is "nobody could count", which a truncated history genuinely
    produces. A line that printed the second as the first would tell somebody
    on untested code that they are on the gated commit.
    """
    built = f"{NEW[:7]} · 2026-09-16"
    on_pin = native.SourceRev("x/y", built, pin=PINNED, ahead=0)
    unknown = native.SourceRev("x/y", built, pin=PINNED, ahead=None)
    assert native.commits_past_pin(on_pin) == f"built from {NEW[:7]} (2026-09-16), the tested pin"
    assert native.commits_past_pin(unknown) != native.commits_past_pin(on_pin)
    assert "the tested pin is" in native.commits_past_pin(unknown)


def test_the_version_line_is_the_designs_sentence_for_one_source_and_names_the_repo_for_two() -> (
    None
):
    """One line beginning "Built from" is a statement about this server; three are not.

    The design specifies the single-source sentence exactly, and that is what a
    one-source family gets. WotLK moves two, so each row is prefixed with the
    repository it is about — without which the tab would carry three
    statements about an unknown subject.
    """
    built = f"{NEW[:7]} · 2026-09-16"
    one = native.InstallState(
        game_id="wow-wotlk",
        install_id="x",
        source_revs=(native.SourceRev("a/b", built, pin=PINNED, ahead=12),),
    )
    assert native.source_revs_line(one) == (
        f"Built from {NEW[:7]} (2026-09-16), 12 commits past the tested pin {PINNED[:7]}"
    )
    two = native.InstallState(
        game_id="wow-wotlk",
        install_id="x",
        source_revs=(
            native.SourceRev("a/b", built, pin=PINNED, ahead=12),
            native.SourceRev("c/d", built, pin=PINNED, ahead=1),
        ),
    )
    said = native.source_revs_line(two).splitlines()
    assert said[0].startswith("a/b: built from")
    assert said[1].endswith(f"1 commit past the tested pin {PINNED[:7]}"), said[1]


def test_a_damaged_source_revs_costs_the_version_line_and_never_the_install(
    tmp_path: Path,
) -> None:
    """The whole file is a hint, and a damaged hint about a decoration must not refuse a resume.

    What is NOT tolerated is a plausible-looking wrong value: an `ahead` that
    is not a number becomes "could not say" rather than zero, because zero is a
    sentence on somebody's screen saying they are on the tested commit.
    """
    rec = Recorder()
    server_dir = _installed(rec, tmp_path)
    path = server_dir / native.STATE_FILE
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["source_revs"] = {
        "a/b": {"built": "abc · 2026-09-16", "pin": PINNED, "ahead": "twelve"},
        "c/d": "not a record",
        "e/f": {"pin": PINNED},
    }
    path.write_text(json.dumps(payload), encoding="utf-8", newline="\n")

    state = native.read_state(server_dir, valid=())
    assert state is not None
    assert [rev.repo for rev in state.source_revs] == ["a/b"]
    assert state.source_revs[0].ahead is None


def test_a_true_ahead_is_read_as_could_not_say_rather_than_as_one_commit() -> None:
    """`bool` is an `int` in Python, and `True` would arrive as "1 commit past the pin".

    A number on somebody's screen with nothing behind it. Its own test because
    the `isinstance(x, int)` that would have let it through is the obvious
    spelling and reads as correct.
    """
    got = native._parse_source_revs({"a/b": {"built": "x · y", "ahead": True}}, Path("s"))
    assert got[0].ahead is None


def test_a_press_that_fails_leaves_the_record_that_was_already_there(tmp_path: Path) -> None:
    """The record is written LAST, after the compile, and describes a build that happened.

    A record written before the build would describe one that may never have
    finished — and since the sources went back, the record already on disk is
    still true. So a failed press must change nothing here at all.
    """
    rec, server_dir = _ready(tmp_path)
    _press(rec, server_dir)
    before = (server_dir / native.STATE_FILE).read_text(encoding="utf-8")

    rec.upstream[server_dir] = "c" * 40
    rec.build_result = AttachedRun(2, ("no",))
    with pytest.raises(InstallerError):
        _press(rec, server_dir)

    payload = json.loads((server_dir / native.STATE_FILE).read_text(encoding="utf-8"))
    assert payload["source_revs"] == json.loads(before)["source_revs"]


# -- against real git -------------------------------------------------------


def _git(argv: list[str], cwd: Path) -> None:
    subprocess.run(["git", *argv], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    """A real repository with two commits, served over `file://`.

    The one test in this file that is not about the engine's control flow. Every
    other assertion here runs against a `Recorder`, which models what `git.
    RunnerGit.clone()` DOES — and a model of a thing is evidence about the model
    until somebody runs the thing. This runs it.
    """
    repo = tmp_path / "origin"
    repo.mkdir()
    _git(["init", "-q", "-b", "main"], repo)
    _git(["config", "user.email", "t@example.invalid"], repo)
    _git(["config", "user.name", "T"], repo)
    (repo / "README").write_text("one\n", encoding="utf-8", newline="\n")
    _git(["add", "-A"], repo)
    _git(["commit", "-qm", "one"], repo)
    return repo


def test_a_real_clone_with_rev_none_fetches_and_resets_onto_the_new_tip(
    origin: Path, tmp_path: Path
) -> None:
    """The seam this whole route turns on, run against real git.

    `rev=None` on an existing `.git` is `_update()` — `fetch origin <branch>`
    then `reset --hard FETCH_HEAD` — and `_pin()` returning immediately. What is
    asserted is the three facts the engine depends on and cannot check for
    itself: the head really moves to upstream's new commit, `head_sha()` reads
    it, and `restore_rev()` puts it back WITHOUT a fetch.
    """
    if not git.git_available():
        pytest.skip("no host git")
    dest = tmp_path / "work"
    real = git.RunnerGit()
    spec = git.CloneSpec(url=f"file://{origin}", dest=dest, branch="main", depth=None)
    real.clone(spec)
    first = real.head_sha(dest)
    assert first is not None and len(first) == 40

    (origin / "README").write_text("two\n", encoding="utf-8", newline="\n")
    _git(["add", "-A"], origin)
    _git(["commit", "-qm", "two"], origin)
    moved = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=origin, capture_output=True, text=True, check=True
    ).stdout.strip()

    real.clone(spec)
    assert real.head_sha(dest) == moved, "rev=None did not move the checkout to the new tip"
    assert real.commits_since(dest, first) == 1
    assert (dest / "README").read_text(encoding="utf-8") == "two\n"

    real.restore_rev(dest, first)
    assert real.head_sha(dest) == first
    assert (dest / "README").read_text(encoding="utf-8") == "one\n"


def test_real_local_edits_reports_the_users_file_and_skips_the_one_we_patch(
    origin: Path, tmp_path: Path
) -> None:
    """The guard's exception list, against real `git status`.

    The carried patch makes a CMaNGOS core checkout dirty on every healthy
    install, so a guard that could not subtract those paths would refuse the
    update on exactly the installs the feature is for. Both halves are asserted
    in one run: the user's file is reported and the app's own is not, from the
    same `git status`.
    """
    if not git.git_available():
        pytest.skip("no host git")
    dest = tmp_path / "work"
    real = git.RunnerGit()
    (origin / "patched.cpp").write_text("ours\n", encoding="utf-8", newline="\n")
    _git(["add", "-A"], origin)
    _git(["commit", "-qm", "two"], origin)
    real.clone(git.CloneSpec(url=f"file://{origin}", dest=dest, branch="main", depth=None))

    assert real.local_edits(dest) == ()
    (dest / "patched.cpp").write_text("ours, patched\n", encoding="utf-8", newline="\n")
    (dest / "README").write_text("theirs\n", encoding="utf-8", newline="\n")
    (dest / "untracked.txt").write_text("not at risk\n", encoding="utf-8", newline="\n")

    assert set(real.local_edits(dest) or ()) == {"patched.cpp", "README"}
    assert real.local_edits(dest, ignoring=("patched.cpp",)) == ("README",)
    assert "untracked.txt" not in (
        real.local_edits(dest) or ()
    ), "an untracked file reset --hard never touches was counted as an edit"


def test_restoring_a_source_asks_the_remote_for_nothing(tmp_path: Path) -> None:
    """The one difference from `_pin()`, and a mutation that added a fetch survived without it.

    `restore_rev()` runs on a path that is ALREADY failing, and one of the
    things that path fails over is the network. `rev` is a commit the checkout
    was sitting on a moment ago, so the object is in the store; asking the
    remote for it again would make the restore depend on the very thing that
    may be the reason we are restoring — and the real-git test above cannot see
    that, because its origin is a `file://` path that is always reachable.

    So this is asserted on the argv. `_pin()` fetches because ITS `rev` may be
    one the clone has never had; this one may not.
    """
    seen: list[list[str]] = []

    def fake_run(argv: list[str], cwd: Path | None = None, **_k: object) -> object:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    dest = tmp_path / "work"
    (dest / ".git").mkdir(parents=True)
    saved = runner.run
    try:
        runner.run = fake_run  # type: ignore[assignment]
        git.RunnerGit().restore_rev(dest, OLD)
    finally:
        runner.run = saved  # type: ignore[assignment]

    assert len(seen) == 1, seen
    assert "fetch" not in seen[0], seen[0]
    # `--force` is not a flourish: without it `checkout --detach` refuses
    # whenever a tracked file differs in the working tree AND between the two
    # commits, which on AzerothCore is always true of `docker-compose.yml` --
    # the one family where a failed restore matters most. What it may discard is
    # bounded by the guard that ran before any of this.
    assert seen[0][-4:] == ["checkout", "--detach", "--force", OLD]


def test_a_shallow_clone_answers_could_not_count_rather_than_one_commit(
    origin: Path, tmp_path: Path
) -> None:
    """Ten of the eleven shipped sources are `depth: 1`, and the count lies on every one.

    `Source.depth` defaults to 1 and only AzerothCore's core overrides it, and
    `_pin()` fetches at that depth (as `ContainerGit.clone()`'s update fetch
    did until T149). HEAD's parents are then cut at the graft while the old pin's
    object is still in the store (the checkout was on it a moment ago), so
    `rev-list --count <pin>..HEAD` walks HEAD, finds no parent and answers **1**
    whatever the real distance is. "1 commit past the tested pin" after a year
    of upstream history is a figure with nothing behind it.

    Driven against real git at depth 1, with the same repository cloned in full
    beside it as the control: the shallow one must answer `None` and the full
    one must answer the true number, or this test would pass against a function
    that had simply stopped counting.
    """
    if not git.git_available():
        pytest.skip("no host git")
    first = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=origin, capture_output=True, text=True, check=True
    ).stdout.strip()
    for n in (2, 3, 4):
        (origin / "README").write_text(f"{n}\n", encoding="utf-8", newline="\n")
        _git(["add", "-A"], origin)
        _git(["commit", "-qm", f"c{n}"], origin)

    real = git.RunnerGit()
    full = tmp_path / "full"
    real.clone(git.CloneSpec(url=f"file://{origin}", dest=full, branch="main", depth=None))
    assert real.commits_since(full, first) == 3, "the control could not count a full history"

    shallow = tmp_path / "shallow"
    real.clone(git.CloneSpec(url=f"file://{origin}", dest=shallow, branch="main", depth=1))
    # The pin's object is put in the store by hand, which is what the update
    # route's own fetch leaves behind: the checkout was sitting on it.
    _git(["fetch", "--depth=1", "origin", first], shallow)
    assert (
        real.commits_since(shallow, first) is None
    ), "a shallow clone answered a distance it cannot know"


def _sha(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _grafts(dest: Path) -> list[str]:
    """`.git/shallow` — the commits git handed this checkout with their parents cut."""
    return (dest / ".git" / "shallow").read_text(encoding="utf-8").split()


def _real_git_tbc(
    rec: Recorder, server_dir: Path, origin: Path, pin: str
) -> tuple[CmangosInstaller, native.CatalogEntry]:
    """A TBC engine whose git is REAL git over `origin`, pinned at `pin`.

    Two seams are doubled and the rest of git is not, and which two matters.
    `clone` is handed the route's own `CloneSpec` with nothing changed but the
    URL, because a `file://` path cannot be spelled in a manifest (`Source.repo`
    takes `owner/name` or an https host), and `remote_url` answers the catalog's
    URL for the same reason — `same_repo()` would otherwise refuse a checkout of
    the very repository this test cloned. Everything the guard under test reads
    (`local_edits`, `no_local_commits`, `head_sha`, `commits_since`) is
    `git.RunnerGit`'s own, run against the checkout on disk, and the depth,
    branch and rev on every spec are the catalog's.

    The rest of the machine stays the Recorder's, as in every other route test
    here: what follows the guard is a compile, and this test is about what git
    says to the guard.
    """
    moving = {"cmangos/mangos-tbc", "cmangos/playerbots"}
    entry = TBC.model_copy(
        update={
            "emulator": TBC.emulator.model_copy(
                update={
                    "sources": tuple(
                        source.model_copy(update={"rev": pin}) if source.repo in moving else source
                        for source in TBC.emulator.sources
                    )
                }
            )
        }
    )
    real = git.RunnerGit()
    urls = {server_dir / source.dest: source.url for source in entry.emulator.sources}

    def clone(spec: git.CloneSpec) -> None:
        rec.clones.append(spec)
        real.clone(replace(spec, url=origin.as_uri()))

    made = CmangosInstaller(
        entry,
        installers_root=resources.installers_dir(),
        seams=rec.seams(
            platform_id=lambda: "linux",
            clone=clone,
            remote_url=urls.get,
            local_edits=real.local_edits,
            no_local_commits=real.no_local_commits,
            head_sha=real.head_sha,
            head_version=real.head_version,
            commits_since=real.commits_since,
            restore_rev=real.restore_rev,
        ),
    )
    made._test_gate = native.CallableGate(rec.probe, rec.reset)  # type: ignore[attr-defined]
    return made, entry


def test_a_source_that_was_updated_and_returned_is_still_updatable_over_two_grafts(
    tmp_path: Path,
) -> None:
    """T82: the live refusal, at the route, on a shape only real git makes.

    Three ordinary presses on a `depth: 1` source — clone at the pin, "Update to
    latest", "Return to the tested pin" — leave `.git/shallow` holding TWO
    grafted commits with no edge between them: the tip the update fetched and
    the pin the return fetched back. `rev-list --count FETCH_HEAD..HEAD` then
    answers 1 and `merge-base --is-ancestor` answers no, and on the update route
    alone (`_refuse_unless_updatable()`) that read as "carries commits that
    upstream does not" and refused BOTH buttons, permanently, on a checkout
    nobody had committed anything into. Measured live on a real playerbots
    clone, 2026-09-16; `git.no_local_commits()`'s `_only_grafts()` is what lets
    it through, and this is the route-layer test that pins the pair together.

    The shape is BUILT BY THE ROUTE, not by writing `.git/shallow`: a
    hand-written graft file would prove this route survives a file a test wrote,
    and the whole finding is that the app's own two presses are what make it.
    `_grafts()` is asserted before the third press for exactly that reason — if
    a future `_pin()` stopped leaving two boundaries, this test would go on
    passing while testing nothing, and the assertion is what makes it fail
    instead.

    Both directions are pressed afterwards, because the live refusal was both:
    the guard is the same code on the way out and the way back, so a fix that
    only freed the update would strand a user one press further along.
    """
    if not git.git_available():
        pytest.skip("no host git")
    # The origin is named for the dest the carried patch applies to, so
    # `lay_patch_sources` lays the pre-image of the files this family's patch
    # edits INTO THE COMMIT — a real clone then carries them and the route's
    # patch stage has something to apply to.
    origin = tmp_path / "origin" / "src" / "mangos-tbc"
    origin.mkdir(parents=True)
    _git(["init", "-q", "-b", "main", "."], origin)
    _git(["config", "user.email", "t@example.invalid"], origin)
    _git(["config", "user.name", "T"], origin)
    lay_patch_sources(TBC)(origin)
    (origin / "README").write_text("the tested pin\n", encoding="utf-8", newline="\n")
    _git(["add", "-A"], origin)
    _git(["commit", "-qm", "pinned"], origin)
    pin = _sha(origin)
    # Upstream is AHEAD of the pin when the install clones, which is every
    # shipped source's ordinary state: the pin is a commit somebody gated weeks
    # ago. That is what makes `_pin()`'s `fetch --depth 1 <rev>` graft a second
    # time instead of walking a parent it already has.
    (origin / "README").write_text("upstream moved on\n", encoding="utf-8", newline="\n")
    _git(["add", "-A"], origin)
    _git(["commit", "-qm", "past the pin"], origin)

    rec, server_dir, _ = _tbc(tmp_path)
    tbc, entry = _real_git_tbc(rec, server_dir, origin, pin)
    moving = [source for source in entry.emulator.sources if not native.held_at_its_pin(source)]
    dests = [server_dir / source.dest for source in moving]
    # The install above ran on the Recorder's clone double, which leaves a bare
    # `.git`. These are the same sources cloned FOR REAL at the pin, which is
    # the state a finished install is in and the first of the three presses.
    for dest in dests:
        # `exists()` because playerbots is cloned INSIDE the core checkout, so
        # the core's removal takes it with it.
        if dest.exists():
            rmtree.remove_tree(dest)
    for source, dest in zip(moving, dests, strict=True):
        tbc._seams.clone(
            git.CloneSpec(
                url=source.url,
                dest=dest,
                branch=source.branch,
                sparse_path=source.sparse_path,
                depth=source.depth,
                rev=source.rev,
            )
        )
        assert pin in _grafts(dest), f"{dest} did not start on a grafted pin"
    rec.clones.clear()

    (origin / "README").write_text("upstream has moved\n", encoding="utf-8", newline="\n")
    _git(["add", "-A"], origin)
    _git(["commit", "-qm", "the tip"], origin)
    tip = _sha(origin)
    real = git.RunnerGit()

    list(tbc.update_to_latest(InstallOptions(server_dir=server_dir)))
    assert [real.head_sha(dest) for dest in dests] == [tip, tip]

    list(tbc.update_to_latest(InstallOptions(server_dir=server_dir), to_pin=True))
    assert [real.head_sha(dest) for dest in dests] == [pin, pin]
    for dest in dests:
        grafts = _grafts(dest)
        assert len(grafts) == 2 and pin in grafts, f"not the twice-grafted shape: {grafts}"
        # And the two questions the guard's own fetch then asks, measured here:
        # they must be LYING, or the press below would proceed for a reason that
        # has nothing to do with `_only_grafts()`. Both figures are the live
        # gate's (`.notes` T82): one commit "ahead", and no ancestry either way.
        _git(["fetch", "-q", "origin", "HEAD"], dest)
        ahead = subprocess.run(
            ["git", "rev-list", "--count", "FETCH_HEAD..HEAD"],
            cwd=dest,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert ahead == "1", f"{dest} is not the shape that counts one commit ahead"
        connected = subprocess.run(
            ["git", "merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD"], cwd=dest, check=False
        )
        assert connected.returncode != 0, "if git ever connects these, this fix can be simpler"

    # The third press: the one that was refused live, on the checkout the two
    # above left behind and with nothing else done to it.
    list(tbc.update_to_latest(InstallOptions(server_dir=server_dir)))
    assert [real.head_sha(dest) for dest in dests] == [tip, tip], "the update was refused"

    list(tbc.update_to_latest(InstallOptions(server_dir=server_dir), to_pin=True))
    assert [real.head_sha(dest) for dest in dests] == [pin, pin], "the way back was refused"


def test_the_two_transports_parse_one_status_the_same_way() -> None:
    """A rename is `R  old -> new`, and a guard that meant different things per machine is a bug.

    Both `local_edits()` bodies share `parse_status()` for this: a caller never
    learns which transport it got, and the refusal it produces is read by a
    person.
    """
    raw = " M src/a.cpp\n?? junk\nR  old/name.h -> new/name.h\nA  added.cpp\n"
    assert git.parse_status(raw) == ("src/a.cpp", "junk", "new/name.h", "added.cpp")


def test_a_git_that_is_not_there_answers_none_rather_than_raising(tmp_path: Path) -> None:
    """`OSError` is one of these questions' ordinary answers on a machine with no git.

    `_run_git()` raises `GitError` for a git that ran and refused and `runner.run()`
    raises `OSError` for one that could not be started, and every read here has
    to catch both: an exception out of a guard is a crash where a refusal
    belongs.
    """
    dest = tmp_path / "work"
    (dest / ".git").mkdir(parents=True)
    real = git.RunnerGit()

    def missing(*_a: object, **_k: object) -> Iterator[str]:
        raise OSError("no such file: git")

    saved = runner.run
    try:
        runner.run = missing  # type: ignore[assignment]
        assert real.head_sha(dest) is None
        assert real.local_edits(dest) is None
        assert real.commits_since(dest, OLD) is None
    finally:
        runner.run = saved  # type: ignore[assignment]


# -- T77: what is on the pin, and what each direction says about it ----------


def _rev(sha: str, *, ahead: int | None = None, repo: str = "x/y") -> native.SourceRev:
    return native.SourceRev(repo, f"{sha[:7]} · 2026-09-02", pin=PINNED, ahead=ahead)


def test_a_shallow_clone_back_on_its_pin_reads_as_being_on_it(tmp_path: Path) -> None:
    """The case the whole fix is for, and the one an `ahead == 0` rule would miss.

    Seven of the nine shipped sources are `depth: 1`, where `commits_since()`
    cannot count and records `None` -- so on the install this button exists for,
    "how far past the pin" has no answer and the SHA is the only evidence there
    is. The live gate's record after a return is exactly this shape:
    `{"built": "993f180 · 2026-09-02", "pin": "993f1809…", "ahead": null}`.
    """
    assert native.on_its_pin(_rev(PINNED)) is True
    assert native.past_the_tested_pin(_state(_rev(PINNED))) is False


def test_a_counted_distance_refutes_the_sha_rather_than_being_ignored(tmp_path: Path) -> None:
    """`ahead` can only say no, and a record that says both is not to be trusted.

    A non-zero count is a measurement of the same two commits, so a record whose
    sha matches the pin and whose count says twelve is damaged. Answering "not
    on the pin" there keeps the way back on screen: the failure that costs an
    hour of compiling is better than the one that strands somebody on untested
    code with no control that returns them.
    """
    assert native.on_its_pin(_rev(PINNED, ahead=12)) is False
    assert native.on_its_pin(_rev(PINNED, ahead=0)) is True
    # And a `built` nothing can read against a pin is not on it either.
    assert native.on_its_pin(native.SourceRev("x/y", "unknown", pin=PINNED)) is False
    # Nor is one with no pin at all -- an entry that does not pin its sources
    # has no tested commit to be on.
    assert native.on_its_pin(native.SourceRev("x/y", f"{PINNED[:7]} · 2026-09-02")) is False


def _state(*revs: native.SourceRev) -> native.InstallState:
    return native.InstallState(game_id="wow-wotlk", install_id="x", source_revs=revs)


def test_the_version_line_has_three_shapes_and_only_one_of_them_offers_the_way_back() -> None:
    """Moved, on the pin, and mixed -- the sentence and the button in one table.

    The middle shape is T77's third item. After a return the line read *"built
    from 993f180 (2026-09-02); the tested pin is 993f180"* -- the same sha
    twice, and the reader had to compare them character by character to learn
    the one thing the line existed to say (live gate, 2026-09-16).

    The mixed row is why the rule is `any` and not `all`: one source home and
    one not is not a returned install, and the press that finishes the job has
    to stay offered.
    """
    moved = _state(_rev(NEW))
    assert native.source_revs_line(moved) == (
        f"Built from {NEW[:7]} (2026-09-02); the tested pin is {PINNED[:7]}"
    )
    assert native.past_the_tested_pin(moved) is True

    home = _state(_rev(PINNED))
    assert native.source_revs_line(home) == f"On the tested pin {PINNED[:7]} (2026-09-02)"
    assert native.past_the_tested_pin(home) is False

    mixed = _state(_rev(PINNED, repo="a/b"), _rev(NEW, repo="c/d"))
    assert native.source_revs_line(mixed).splitlines() == [
        f"a/b: on the tested pin {PINNED[:7]} (2026-09-02)",
        f"c/d: built from {NEW[:7]} (2026-09-02); the tested pin is {PINNED[:7]}",
    ]
    assert native.past_the_tested_pin(mixed) is True


def test_an_install_with_no_record_says_nothing_and_offers_no_way_back() -> None:
    """Every install that has never pressed the button, and the `None` a bad read gives."""
    assert native.source_version(None) == native.SourceVersion(line="", past_the_pin=False)
    assert native.source_version(_state()) == native.SourceVersion(line="", past_the_pin=False)


def test_each_direction_opens_with_the_fact_that_is_true_of_it(tmp_path: Path) -> None:
    """The route's first note is the opposite claim in the two directions.

    The way out is *"code nobody has tested with this app"*; the way back is
    the commit the gates ran on. Sharing one sentence told a user returning to
    the tested commit that it was untested (live gate, 2026-09-16, press 6).

    Driven through the real route in both directions, not asserted against the
    constants: a route that yielded the right constant from the wrong branch is
    the defect, and only the press can tell them apart. The last two assertions
    are the ones that fail if the two ever become the same string again.
    """
    rec, server_dir = _ready(tmp_path)
    out = _press(rec, server_dir)
    rec.clones.clear()
    back = _press(rec, server_dir, to_pin=True)

    assert native.UPDATE_TO_LATEST_OPENING_NOTE in out
    assert native.RETURN_TO_PIN_OPENING_NOTE in back
    assert native.UPDATE_TO_LATEST_OPENING_NOTE not in back
    assert native.RETURN_TO_PIN_OPENING_NOTE not in out

    forwards = native.UPDATE_TO_LATEST_OPENING_NOTE.split(".")[0]
    backwards = native.RETURN_TO_PIN_OPENING_NOTE.split(".")[0]
    assert forwards != backwards
    assert forwards == "This is code nobody has tested with this app"
    assert backwards.startswith("This is the commit this app was tested against")
    # True of the way back and the reason somebody might press it by mistake:
    # it is not a quick undo. The dialog says so too; this is the note they see
    # after pressing, which is where the wait becomes real.
    assert "still a full build" in native.RETURN_TO_PIN_OPENING_NOTE
    # Both keep the promise the restore actually makes.
    for note in (native.UPDATE_TO_LATEST_OPENING_NOTE, native.RETURN_TO_PIN_OPENING_NOTE):
        assert "the build you have keeps running" in note


def test_the_two_confirmations_open_differently_and_neither_claims_an_undo() -> None:
    """The dialogs, which are the sentence BEFORE the press, on the same rule as the notes."""
    entry = ENTRY
    forwards = native.update_to_latest_confirmation(entry, Path("/srv"), "x/y")
    backwards = native.return_to_pin_confirmation(entry, Path("/srv"), "x/y")

    assert forwards.split("\n\n")[0] != backwards.split("\n\n")[0]
    assert "newest x/y code" in forwards.split("\n\n")[0]
    assert "commit this app was tested against" in backwards.split("\n\n")[0]
    # Neither offers the return as a fix for what the newer server wrote.
    assert "is not put back" in forwards
    assert "does NOT undo" in backwards


def test_the_rewritten_history_refusal_says_where_the_press_is() -> None:
    """T89 moved "Update the server to latest…" into the "Server build ▾" menu.

    This refusal tells the player to press it again, so it names the menu as
    well: the entry is not on the Modules toolbar itself any more.
    """
    said = str(native.RewrittenHistory("cmangos/x", "The release is on rewritten history."))
    assert "“Update the server to latest…”" in said
    assert "“Server build ▾”" in said, said


# -- T179: what changed between two commits (the TrinityCore route's question) ---------------


def test_the_other_families_never_ask_git_what_changed(tmp_path: Path) -> None:
    """The spine's hooks are no-ops: WotLK and TBC updates ask the same questions as before.

    And their rebuild replaces the containers in the one `recreate` call it always made:
    no family work between a stop and a start (T179 Task 6, fix round 1).
    """
    rec, server_dir = _ready(tmp_path / "wotlk")
    _press(rec, server_dir)
    assert not [call for call in rec.calls if call.startswith("changed-")]
    assert "recreate" in rec.calls and "stop_servers" not in rec.calls
    rec, server_dir, tbc = _tbc(tmp_path)
    list(tbc.update_to_latest(InstallOptions(server_dir=server_dir)))
    assert not [call for call in rec.calls if call.startswith("changed-")]
    assert "recreate" in rec.calls and "stop_servers" not in rec.calls


ABORTED_AFTER_READY = native.WorldOutput(
    text="ready...\nAvg Diff: 15ms\nWorld server is up and running\n>> ABORTED",
    restarts=0,
    status="exited",
)
"""A world that said ready, then stopped (T71): the rebuild keeps the new build."""


def test_a_kept_build_keeps_the_sources_it_was_made_from_and_records_them(
    tmp_path: Path,
) -> None:
    """T179 Task 6 fix round 2: the build is kept (T71), so its sources are too.

    Putting the old commits back under a kept new build would leave the folder and
    the running binary disagreeing -- the invariant this route exists for -- and
    the sentence would say they agree again.
    """
    rec, server_dir = _ready(tmp_path)
    with pytest.raises(WorldStoppedAfterReadyError) as raised:
        list(
            engine(rec, world_output=lambda spec: ABORTED_AFTER_READY).update_to_latest(
                InstallOptions(server_dir=server_dir)
            )
        )
    said = str(raised.value)
    assert set(_heads(rec, server_dir).values()) == {NEW}
    assert not [call for call in rec.calls if call.startswith("restore:")], rec.calls
    assert said.endswith(native.SOURCES_KEPT_NOTE)
    assert native.SOURCES_PUT_BACK_NOTE not in said and "put back" not in said
    state = native.read_state(server_dir, valid=())
    assert state is not None and state.source_revs, "what the kept build was made from"
    assert raised.value.sources_kept is True, "the outcome the tab reads, typed"


def test_a_kept_build_whose_after_work_fails_still_says_the_build_was_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix round 3: the after-work's failure is added to the sentence, never in its place."""
    rec, server_dir = _ready(tmp_path)
    made = engine(rec, world_output=lambda spec: ABORTED_AFTER_READY)

    def breaks(*_args: object, **_kwargs: object) -> Iterator[str]:
        raise InstallerError("the after-work broke")
        yield ""  # pragma: no cover - makes this a generator

    monkeypatch.setattr(made, "after_update", breaks)
    with pytest.raises(WorldStoppedAfterReadyError) as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))
    said = str(raised.value)
    assert "came up and then stopped" in said
    assert "the after-work broke" in said
    assert native.SOURCES_KEPT_NOTE in said
    assert raised.value.sources_kept is True


# -- T197: a rollback that stops early leaves the new build, so its sources stay -----

REFUSED = "Error response from daemon: read-only layer store"

SPINE_GAMES = [ENTRY, *CMANGOS_GAMES]
"""Every family on the spine's rollback: AzerothCore, and CMaNGOS's three (Tortoise among them)."""


def _spine(
    tmp_path: Path, entry: CatalogEntry
) -> tuple[Recorder, Path, Callable[..., native.StagedInstaller]]:
    """An installed `entry` with `NEW` upstream, and a maker of its engine with seams overridden."""
    if entry.id == ENTRY.id:
        rec, server_dir = _ready(tmp_path)
        return rec, server_dir, lambda **overrides: engine(rec, **overrides)
    rec, server_dir, _made = _cmangos(tmp_path, entry)
    return rec, server_dir, lambda **overrides: tbc_engine(rec, entry=entry, **overrides)


def _tags(rec: Recorder, refuse: Callable[[str, str, int], bool]) -> Callable[[str, str], str]:
    """`docker tag`, recorded, refused where `refuse(src, dst, nth retag back)` says so."""
    back = 0

    def tag(src: str, dst: str) -> str:
        nonlocal back
        rec.calls.append(f"tag:{src}->{dst}")
        if src.endswith(native.ROLLBACK_TAG_SUFFIX):
            back += 1
        return REFUSED if refuse(src, dst, back) else ""

    return tag


def _stop_refused(rec: Recorder) -> dict[str, object]:
    """The rollback's stop of the failed build fails (`docker.DockerCommandError`)."""

    def refuse(control: object) -> None:
        raise docker.DockerCommandError("the daemon did not answer the stop")

    rec.on_stop_servers = refuse
    return {}


def _name_refused(rec: Recorder) -> dict[str, object]:
    """The new build cannot be given its `-failed` name to undo onto."""
    return {"tag_image": _tags(rec, lambda src, dst, n: dst.endswith(native.FAILED_TAG_SUFFIX))}


def _retag_refused(rec: Recorder) -> dict[str, object]:
    """The first tag moved back to the old build is refused: no tag moved, none to undo."""
    return {
        "tag_image": _tags(
            rec, lambda src, dst, n: src.endswith(native.ROLLBACK_TAG_SUFFIX) and n == 1
        )
    }


def _mixed(rec: Recorder) -> dict[str, object]:
    """The SECOND tag moved back is refused, and undoing the first too: the tags are mixed.

    AzerothCore only: a CMaNGOS server builds one image, so it has no second tag.
    """
    return {
        "tag_image": _tags(
            rec,
            lambda src, dst, n: (src.endswith(native.ROLLBACK_TAG_SUFFIX) and n == 2)
            or src.endswith(native.FAILED_TAG_SUFFIX),
        )
    }


EARLY_RETURNS: dict[str, Callable[[Recorder], dict[str, object]]] = {
    "stop-refused": _stop_refused,
    "name-refused": _name_refused,
    "retag-refused": _retag_refused,
    "mixed": _mixed,
}
"""`_restore_rollback()`'s four early returns: each leaves the tags on the new build (or mixed)."""

EARLY_CASES = [
    pytest.param(entry, how, id=f"{entry.id}-{how}")
    for entry in SPINE_GAMES
    for how in EARLY_RETURNS
    if how != "mixed"
]
"""The early returns that leave ONE build on the tags: the new one. Mixed is its own case."""

EARLY_SENTENCES = {
    "stop-refused": "servers could not be stopped",
    "name-refused": "could not be given a name to undo onto",
    "retag-refused": "the 0 tag(s) already moved were moved back, so the tags still name",
    "mixed": "the tags are MIXED",
}
"""What each early return says, so a test knows it reached the one it arranged."""


def _moving_heads(rec: Recorder, server_dir: Path, made: native.StagedInstaller) -> set[str]:
    return {rec.heads[server_dir / source.dest] for source in made.sources_that_move()}


def _recorded_builds(server_dir: Path) -> set[str]:
    state = native.read_state(server_dir, valid=())
    assert state is not None
    return {rev.built[:7] for rev in state.source_revs}


@pytest.mark.parametrize(("entry", "how"), EARLY_CASES)
def test_a_rollback_that_stops_early_leaves_the_sources_with_the_new_build(
    tmp_path: Path, entry: CatalogEntry, how: str
) -> None:
    """T197: the old build was not put back, so the old commits must not be either.

    The new build never reported ready, its containers were replaced, and the
    rollback stopped before the old build was back on its tags. Putting the old
    commits back here left the folder disagreeing with the build every start
    runs, under a sentence saying they agree again. The sources stay on the new
    commits and are recorded as what the build was made from (T179's kept-build
    shape), and the sentence says so.
    """
    rec, server_dir, make = _spine(tmp_path, entry)
    rec.ready = False
    made = make(**EARLY_RETURNS[how](rec))
    with pytest.raises(RollbackNotDone) as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))
    said = str(raised.value)
    assert "recreate" in rec.calls, "the ground: the new build's containers were replaced"
    assert EARLY_SENTENCES[how] in said, said
    assert _moving_heads(rec, server_dir, made) == {NEW}
    assert not [call for call in rec.calls if call.startswith("restore:")], rec.calls
    assert _recorded_builds(server_dir) == {NEW[:7]}, "what the new build was made from"
    assert said.endswith(native.SOURCES_LEFT_NOTE)
    assert native.SOURCES_PUT_BACK_NOTE not in said and "agree again" not in said
    assert raised.value.sources_kept is True, "the outcome the tab reads, typed"
    assert raised.value.touched is True and raised.value.mixed is False
    assert native.owed_start_refusal(server_dir) is None, "one build on the tags: Start may run"


@pytest.mark.parametrize("entry", SPINE_GAMES, ids=lambda entry: entry.id)
def test_a_rollback_that_put_the_old_build_back_still_puts_the_sources_back(
    tmp_path: Path, entry: CatalogEntry
) -> None:
    """The regression half of T197: a rollback that did restore keeps today's behaviour.

    The new build never comes up and the old one does: the ready wait answers
    differently the second time, so the restore really ran.
    """
    rec, server_dir, make = _spine(tmp_path, entry)
    answers = [False, True]

    def wait_ready(spec: object, ready: object) -> bool:
        return answers.pop(0) if len(answers) > 1 else answers[0]

    made = make(wait_ready=wait_ready)
    with pytest.raises(InstallerError) as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))
    said = str(raised.value)
    assert not isinstance(raised.value, RollbackNotDone)
    assert "put back and is running again" in said
    assert _moving_heads(rec, server_dir, made) == {OLD}
    assert said.endswith(native.SOURCES_PUT_BACK_NOTE)
    assert native.SOURCES_LEFT_NOTE not in said
    state = native.read_state(server_dir, valid=())
    assert state is not None and not state.source_revs, "a press that went back records nothing"


def _recreate_given_up(rec: Recorder) -> None:
    """The recreate is given up in the load wait, before its signal: no container replaced."""

    def give_up(control: object) -> None:
        raise docker.StopAbandoned("the world was still loading")

    rec.on_recreate = give_up


def test_a_rollback_that_stops_early_before_any_container_moved_leaves_the_new_sources_too(
    tmp_path: Path,
) -> None:
    """T197: the containers are the old build, but the tags -- what a start runs -- are new.

    Start is `compose up -d`, which replaces a container whose tag names another
    image, so the build the server runs next is the one the tags name, and the
    sources stay with it.
    """
    rec, server_dir = _ready(tmp_path)
    _recreate_given_up(rec)
    made = engine(rec, **_name_refused(rec))
    with pytest.raises(RollbackNotDone) as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))
    assert "recreate" not in rec.calls, "the ground: no container was replaced"
    assert str(raised.value) == (
        f"{GIVEN_UP} Putting the build from before this rebuild back was not attempted, "
        f"because the new build could not be given a name to undo onto ({REFUSED}); the tags "
        "still name the new build, all of them. The old images are on the daemon under their "
        "-rollback tags. The source folders were left on the new commits, because the image "
        "tags name the new build made from them. None of your server's containers was "
        "replaced, so it is still running the build from before this update if it is up, and "
        "its next Start runs the new build."
    )
    assert set(_heads(rec, server_dir).values()) == {NEW}
    assert _recorded_builds(server_dir) == {NEW[:7]}
    assert raised.value.touched is False and raised.value.sources_kept is True


GIVEN_UP = (
    "The rebuild was cancelled while the world was still loading, so its containers were not "
    "replaced -- the server you have is still the one that was running before this rebuild. "
    "Nothing was touched."
)
"""`stage_recreate()`'s sentence for a recreate given up before its signal: nothing replaced."""

BEFORE = native.SourceRev(repo="earlier/press", built="1234567 · 2026-09-01", pin="", ahead=None)
"""A row an earlier press recorded: a mixed rollback must leave the record exactly as it was."""


def _recorded_before(server_dir: Path) -> tuple[native.SourceRev, ...]:
    state = native.read_state(server_dir, valid=())
    assert state is not None
    native.write_state(server_dir, replace(state, source_revs=(BEFORE,)))
    return (BEFORE,)


def test_a_rollback_that_leaves_the_tags_mixed_says_a_rebuild_is_needed_and_records_nothing(
    tmp_path: Path,
) -> None:
    """T197 fix round 1: mixed tags are no one build, so no new build is recorded or kept.

    Yu'lon has no record that refuses a Start until a Rebuild, so the record is left as
    it was and the sources go back to the commits it names; the sentence says the tags
    are mixed and that the server needs a Rebuild before it can start.
    """
    rec, server_dir = _ready(tmp_path)
    before = _recorded_before(server_dir)
    _recreate_given_up(rec)
    made = engine(rec, **_mixed(rec))
    refs = made.image_refs_at(server_dir)
    with pytest.raises(RollbackNotDone) as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))
    assert str(raised.value) == (
        f"{GIVEN_UP} Putting the build from before this rebuild back failed part-way "
        f"({REFUSED}) and undoing it failed too, so the tags are MIXED: {refs[0]} name the old "
        "build and the rest name the new one. Do not start this server until they agree; the "
        "old images are under their -rollback tags. The source folders were put back on the "
        "commits they were on before this update. None of your server's containers was "
        "replaced, so it is still running the build from before this update if it is up. Its "
        "image tags are mixed, so it must be rebuilt before it can start, and Start is refused "
        "until it is: press “Rebuild the "
        "server…” under “Server build ▾” on the Modules tab, which compiles every image from "
        "those commits."
    )
    state = native.read_state(server_dir, valid=())
    assert state is not None and state.source_revs == before, "the record is as it was"
    assert set(_heads(rec, server_dir).values()) == {OLD}
    assert raised.value.mixed is True and raised.value.sources_kept is False
    _start_is_refused_for_a_rebuild(server_dir)


def test_a_rollback_that_leaves_the_tags_mixed_after_the_containers_moved_records_nothing(
    tmp_path: Path,
) -> None:
    """The same with the new build's containers replaced: still no one build to keep."""
    rec, server_dir = _ready(tmp_path)
    before = _recorded_before(server_dir)
    rec.ready = False
    made = engine(rec, **_mixed(rec))
    with pytest.raises(RollbackNotDone) as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))
    said = str(raised.value)
    assert "recreate" in rec.calls, "the ground: the new build's containers were replaced"
    assert said.endswith(native.SOURCES_MIXED_NOTE)
    assert native.SOURCES_LEFT_NOTE not in said and "agree again" not in said
    state = native.read_state(server_dir, valid=())
    assert state is not None and state.source_revs == before
    assert set(_heads(rec, server_dir).values()) == {OLD}
    assert raised.value.mixed is True and raised.value.touched is True
    _start_is_refused_for_a_rebuild(server_dir)


MIXED_REFUSAL = (
    "This server's image tags are mixed: an update or rebuild could not put the build from "
    "before it back, and left some images on one build and the rest on the other. It must be "
    "rebuilt before it can start: press “Rebuild the server…” under “Server build ▾” on the "
    "Modules tab."
)


def _start_is_refused_for_a_rebuild(server_dir: Path) -> None:
    """A NEW controller over the folder -- the app restarted -- refuses, and so does the engine."""
    with pytest.raises(StartRefused) as refused:
        Controller(ENTRY.container_spec(), server_dir).refuse_start()
    assert str(refused.value) == MIXED_REFUSAL
    assert (server_dir / native.START_REFUSED_FILE).is_file()


def test_a_plain_rebuild_that_leaves_the_tags_mixed_refuses_every_start_too(
    tmp_path: Path,
) -> None:
    """Fix round 2: the geometry is the Rebuild press's as much as the update's."""
    rec, server_dir = _ready(tmp_path)
    rec.ready = False
    with pytest.raises(RollbackNotDone) as raised:
        list(engine(rec, **_mixed(rec)).rebuild(InstallOptions(server_dir=server_dir)))
    assert raised.value.mixed is True
    _start_is_refused_for_a_rebuild(server_dir)


def test_a_rebuild_that_succeeds_clears_the_refusal_and_one_that_fails_keeps_it(
    tmp_path: Path,
) -> None:
    """Fix round 2: Rebuild is the repair, so it is not refused, and only its success clears."""
    rec, server_dir = _ready(tmp_path)
    assert native.owe_start(server_dir) == ""
    rec.build_result = AttachedRun(2, ("error: no",))
    with pytest.raises(InstallerError):
        list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert "build" in rec.calls, "the ground: the Rebuild was not refused, it failed"
    assert native.owed_start_refusal(server_dir) == MIXED_REFUSAL, "a failed Rebuild keeps it"

    rec.build_result = AttachedRun(0, ("built",))
    list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert native.owed_start_refusal(server_dir) is None
    assert not (server_dir / native.START_REFUSED_FILE).exists()
    Controller(ENTRY.container_spec(), server_dir).refuse_start()


def test_the_repair_rebuild_after_mixed_tags_runs_to_the_end_and_starts_the_server(
    tmp_path: Path,
) -> None:
    """Fix round 8: the record a real mixed rollback wrote, then the repair, with no stage faked.

    The round-8 review read `stage_up()`'s refusal (asked without `rebuilding`) as on
    this path. It is not: `rebuild_stages()` ends in `recreate` and `ready`, never the
    install's `up`, so the press's own `rebuilding=True` question is the only one it asks
    before the start. Bound here so a stage tuple that grows an `up` fails this.
    """
    rec, server_dir = _ready(tmp_path)
    rec.ready = False
    with pytest.raises(RollbackNotDone):
        list(engine(rec, **_mixed(rec)).rebuild(InstallOptions(server_dir=server_dir)))
    _start_is_refused_for_a_rebuild(server_dir)
    rec.ready = True
    rec.calls.clear()
    said = list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert said[-1] == f"{ENTRY.name} was rebuilt and is running in {server_dir}"
    assert [c for c in rec.calls if c in ("build", "recreate")] == ["build", "recreate"]
    assert not (server_dir / native.START_REFUSED_FILE).exists()
    Controller(ENTRY.container_spec(), server_dir).refuse_start()


def test_a_repair_rebuild_whose_build_does_not_come_up_leaves_the_mixed_build_stopped(
    tmp_path: Path,
) -> None:
    """Fix round 8: its rollback puts back the MIXED tags, so it must not start them.

    Until round 8 only the update route's rollback asked `start_refusal()` before its
    restart, so a failed repair recreated the very images the record refuses to start.
    """
    rec, server_dir = _ready(tmp_path)
    assert native.owe_start(server_dir) == ""
    rec.ready = False
    with pytest.raises(native.ServersLeftStopped) as raised:
        list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert rec.calls.count("recreate") == 1, "the new build's only: the rollback started nothing"
    assert rec.calls.count("start") == 0
    assert "its servers were left STOPPED: " + MIXED_REFUSAL in str(raised.value)
    assert "is running again" not in str(raised.value)
    _start_is_refused_for_a_rebuild(server_dir)


def test_a_record_nobody_can_read_still_refuses_and_asks_for_a_rebuild(tmp_path: Path) -> None:
    server_dir = tmp_path
    (server_dir / native.START_REFUSED_FILE).write_text("not json", encoding="utf-8")
    assert native.owed_start_refusal(server_dir) == MIXED_REFUSAL
    assert native.owed_start_refusal(server_dir, rebuilding=True) is None


def test_a_rollback_that_put_the_tags_back_before_any_container_moved_puts_the_sources_back(
    tmp_path: Path,
) -> None:
    """The regression half: the tags went back to the build still running, so the sources do."""
    rec, server_dir = _ready(tmp_path)
    _recreate_given_up(rec)
    with pytest.raises(InstallerError) as raised:
        _press(rec, server_dir)
    said = str(raised.value)
    assert not isinstance(raised.value, RollbackNotDone)
    assert "The tags were put back to the build that is running" in said
    assert set(_heads(rec, server_dir).values()) == {OLD}
    assert said.endswith(native.SOURCES_PUT_BACK_NOTE)


def test_a_rollback_that_stops_early_on_a_plain_rebuild_adds_nothing_about_sources(
    tmp_path: Path,
) -> None:
    """Rebuild moves no source: its sentence is the rollback's own, and nothing is kept."""
    rec, server_dir = _ready(tmp_path)
    rec.ready = False
    with pytest.raises(RollbackNotDone) as raised:
        list(engine(rec, **_name_refused(rec)).rebuild(InstallOptions(server_dir=server_dir)))
    said = str(raised.value)
    assert said.endswith(f"under their {native.ROLLBACK_TAG_SUFFIX} tags.")
    assert raised.value.sources_kept is False
    assert set(_heads(rec, server_dir).values()) == {OLD}, "Rebuild never moved them"


def _stopped_in_the_ready_wait(
    rec: Recorder, server_dir: Path, **overrides: object
) -> tuple[LogPanel, list[tuple[bool, str]]]:
    """An update press run in a real `LogPanel`, with Stop pressed while it waits for ready (T228).

    The new build's ready wait blocks until the panel's Stop sets the press's
    cancel, then answers "not ready", which is the order the m910q check met
    (P9): build, recreate, ready wait, Stop. What the rollback then does is the
    test's `overrides`; `answers` are the ready waits' answers in turn.
    """
    waiting = threading.Event()
    gate: list[threading.Event] = []
    answers = list(overrides.pop("answers", [False]))  # type: ignore[call-overload]

    def wait_ready(spec: object, ready: object) -> bool:
        if not waiting.is_set():
            waiting.set()
            gate[0].wait(HANG_BOUND)
        return bool(answers.pop(0) if len(answers) > 1 else answers[0])

    rebuild = bool(overrides.pop("rebuild", False))
    made = engine(rec, wait_ready=wait_ready, **overrides)
    options = InstallOptions(server_dir=server_dir)

    def press(cancel: threading.Event) -> Iterator[str]:
        gate.append(cancel)
        if rebuild:
            return made.rebuild(options, cancel=cancel)
        return made.update_to_latest(options, cancel=cancel)

    return stop_when(press, waiting, "the press reached the new build's ready wait")


def test_a_stop_whose_rollback_stopped_early_shows_what_the_press_left(
    qapp: object, tmp_path: Path
) -> None:
    """T228: the sentence that says what state the server is in survives the Stop.

    The m910q check (P9): Stop in the ready wait, then the rollback's first retag
    failed. The press ended with "Putting ... back failed" and "The source
    folders were left on the new commits", and the panel said only "cancelled".
    """
    rec, server_dir = _ready(tmp_path)
    panel, finished = _stopped_in_the_ready_wait(rec, server_dir, **_retag_refused(rec))

    assert panel.cancelled is True
    header = panel.status_text()
    assert header.startswith(STOPPED_THEN_FAILED), header
    assert EARLY_SENTENCES["retag-refused"] in header, header
    assert header.endswith(native.SOURCES_LEFT_NOTE), header
    assert len(finished) == 1 and finished[0][0] is False, finished
    assert native.SOURCES_LEFT_NOTE in finished[0][1], "the tab's refusal gets the sentence too"
    assert set(_heads(rec, server_dir).values()) == {NEW}, "the ground: the sources stayed moved"


def test_a_stop_whose_new_build_was_kept_says_the_build_and_sources_were_kept(
    qapp: object, tmp_path: Path
) -> None:
    """T228: the build came up and then stopped, so it was KEPT, and so were its sources."""
    rec, server_dir = _ready(tmp_path)
    panel, finished = _stopped_in_the_ready_wait(
        rec, server_dir, answers=[True], world_output=lambda spec: ABORTED_AFTER_READY
    )

    header = panel.status_text()
    assert header.startswith(STOPPED_THEN_FAILED), header
    assert header.endswith(native.SOURCES_KEPT_NOTE), header
    assert finished and finished[0][0] is False, finished
    assert set(_heads(rec, server_dir).values()) == {NEW}


def test_a_stopped_rebuild_whose_rollback_left_the_servers_stopped_says_start_is_refused(
    qapp: object, tmp_path: Path
) -> None:
    """T228: a start refusal now in force is said after a Stop, with what to press."""
    rec, server_dir = _ready(tmp_path)
    assert native.owe_start(server_dir) == ""
    panel, finished = _stopped_in_the_ready_wait(rec, server_dir, rebuild=True)

    header = panel.status_text()
    assert header.startswith(STOPPED_THEN_FAILED), header
    assert "its servers were left STOPPED: " + MIXED_REFUSAL in header, header
    assert finished and finished[0][0] is False, finished
    assert rec.calls.count("start") == 0, "the ground: nothing was started"


def test_a_stop_whose_rollback_put_the_old_build_back_says_what_the_database_holds(
    qapp: object, tmp_path: Path
) -> None:
    """T228 cold review: "put back and is running" carries a note a Stop must not hide.

    The old build is back and running, but on the database as the new build left
    it, and the sources went back under it. Every word of that is true after a
    Stop, so it is shown under "Stopped".
    """
    rec, server_dir = _ready(tmp_path)
    panel, finished = _stopped_in_the_ready_wait(rec, server_dir, answers=[False, True])

    header = panel.status_text()
    assert header.startswith(STOPPED_THEN_FAILED), header
    assert "put back and is running again" in header, header
    assert "is NOT put back by this" in header, header
    assert header.endswith(native.SOURCES_PUT_BACK_NOTE), header
    assert finished and finished[0][0] is False, finished
    assert set(_heads(rec, server_dir).values()) == {OLD}, "the ground: the sources went back"


def test_a_stop_whose_old_build_did_not_come_up_either_says_so(
    qapp: object, tmp_path: Path
) -> None:
    """T228 cold review: the old build was put back and did not report ready either."""
    rec, server_dir = _ready(tmp_path)
    panel, finished = _stopped_in_the_ready_wait(rec, server_dir, answers=[False, False])

    header = panel.status_text()
    assert header.startswith(STOPPED_THEN_FAILED), header
    assert "did not report ready either" in header, header
    assert header.endswith(native.SOURCES_PUT_BACK_NOT_UP_NOTE), header
    assert native.SOURCES_PUT_BACK_NOTE not in header, "the server is not up to agree with"
    assert finished and finished[0][0] is False, finished


def test_a_stop_in_the_replace_before_any_container_moved_says_only_cancelled(
    qapp: object, tmp_path: Path
) -> None:
    """The rollback's untouched return: the tags went back, no container moved, sources back.

    The recreate waits on the world's load until Stop, then gives up before its
    signal. Nothing that runs was changed, so this is a clean cancel.
    """
    rec, server_dir = _ready(tmp_path)
    reached = threading.Event()
    gate: list[threading.Event] = []

    def give_up(control: object) -> None:
        reached.set()
        gate[0].wait(HANG_BOUND)
        raise docker.StopAbandoned("the world was still loading")

    rec.on_recreate = give_up
    made = engine(rec)
    options = InstallOptions(server_dir=server_dir)

    def press(cancel: threading.Event) -> Iterator[str]:
        gate.append(cancel)
        return made.update_to_latest(options, cancel=cancel)

    panel, finished = stop_when(press, reached, "the replace was waiting on the world")

    assert panel.status_text() == "cancelled"
    assert finished == [(True, "stopped")], finished
    assert "recreate" not in rec.calls, "the ground: no container was replaced"
    assert set(_heads(rec, server_dir).values()) == {OLD}


def test_a_stop_during_the_compile_puts_everything_back_and_says_only_cancelled(
    qapp: object, tmp_path: Path
) -> None:
    """The clean cancel T228 keeps: stopped before any container moved, nothing left behind.

    The compile is killed, the rollback names are released, the sources go back,
    and the server runs the build it had. The failure is a plain `InstallerError`
    as long as the others, so the panel tells them apart by type alone.
    """
    rec, server_dir = _ready(tmp_path)
    compiling = threading.Event()
    made = engine(rec, build=compile_until_stopped(compiling))
    options = InstallOptions(server_dir=server_dir)
    panel, finished = stop_when(
        lambda cancel: made.update_to_latest(options, cancel=cancel), compiling, "compiling"
    )

    assert panel.cancelled is True
    assert panel.status_text() == "cancelled"
    assert finished == [(True, "stopped")], finished
    assert "recreate" not in rec.calls, "the ground: no container was replaced"
    assert set(_heads(rec, server_dir).values()) == {OLD}, "the ground: the sources went back"


def test_a_shallow_checkout_says_which_files_changed_between_the_commit_it_left_and_its_new_one(
    origin: Path, tmp_path: Path
) -> None:
    """Real git, at depth 1, as the update route leaves a checkout: the old commit's tree is
    still in the store, so the two can be compared with no network and no history between.

    Added, modified and removed are three different answers to the route (import, import,
    leave the table), and a path outside the asked folders is not reported.
    """
    if not git.git_available():
        pytest.skip("no host git")
    (origin / "sql").mkdir()
    (origin / "sql" / "old.sql").write_text("DROP TABLE old;\n", encoding="utf-8", newline="\n")
    (origin / "sql" / "keep.sql").write_text(
        "-- a comment\nINSERT INTO `realmlist` VALUES (1);\nINSERT INTO `other` VALUES (1);\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(["add", "-A"], origin)
    _git(["commit", "-qm", "sql"], origin)
    real = git.RunnerGit()
    dest = tmp_path / "work"
    spec = git.CloneSpec(url=f"file://{origin}", dest=dest, branch="main", depth=1)
    real.clone(spec)
    first = real.head_sha(dest)
    assert first is not None

    (origin / "sql" / "old.sql").unlink()
    (origin / "sql" / "new.sql").write_text("DROP TABLE new;\n", encoding="utf-8", newline="\n")
    (origin / "sql" / "keep.sql").write_text(
        "-- another comment\n"
        "INSERT INTO `realmlist` VALUES (2);\n"
        "INSERT INTO `other` VALUES (1);\n",
        encoding="utf-8",
        newline="\n",
    )
    (origin / "README").write_text("moved\n", encoding="utf-8", newline="\n")
    _git(["add", "-A"], origin)
    _git(["commit", "-qm", "moved"], origin)
    real.clone(spec)
    moved = real.head_sha(dest)
    assert moved is not None and moved != first

    assert sorted(real.changed_files(dest, first, moved, ["sql"]) or ()) == [
        ("A", "sql/new.sql"),
        ("D", "sql/old.sql"),
        ("M", "sql/keep.sql"),
    ]
    assert real.changed_files(dest, moved, first, ["sql"]) is not None, "either direction"
    # A removed SQL comment prints as `--- a comment`, the shape of a diff header.
    assert real.changed_lines(dest, first, moved, "sql/keep.sql") == (
        "--- a comment",
        "-INSERT INTO `realmlist` VALUES (1);",
        "+-- another comment",
        "+INSERT INTO `realmlist` VALUES (2);",
    )
    assert real.changed_files(dest, first, "f" * 40, ["sql"]) is None, "an unknown commit"
    assert real.changed_files(tmp_path / "nowhere", first, moved, ["sql"]) is None
