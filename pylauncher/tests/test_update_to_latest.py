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
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from tests.support_native import ENTRY, VMAP_FIXTURE, Recorder, engine, install, lay_patch_sources

# The CMaNGOS half of this file drives a REAL TBC install, and the machinery
# that lays the SQL a CMaNGOS plan names -- and the import gate it needs --
# lives in that family's own test module. Imported rather than re-spelled: a
# second copy of `lay_sql` would be a second fixture to keep in step with the
# catalog, and this file is about the update route rather than about what a
# CMaNGOS install needs on disk.
from tests.test_families_cmangos import ENTRY as TBC
from tests.test_families_cmangos import engine as tbc_engine
from tests.test_families_cmangos import install as tbc_install
from yulon import git, resources, rmtree, runner
from yulon.catalog import native
from yulon.catalog.catalog import load_catalog
from yulon.catalog.families.cmangos import CmangosInstaller
from yulon.catalog.installer import InstallerError, InstallOptions
from yulon.docker import AttachedRun

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
    assert len(every) == 10, [s.repo for s in every]
    assert sum(1 for s in every if s.depth is not None) == 9
    moving = {
        entry.id: tuple(s.repo for s in entry.emulator.sources if not native.held_at_its_pin(s))
        for entry in load_catalog().games
    }
    assert moving == {
        "wow-wotlk": ("mod-playerbots/azerothcore-wotlk", "mod-playerbots/mod-playerbots"),
        "wow-tbc": ("cmangos/mangos-tbc", "cmangos/playerbots"),
        "wow-vanilla": ("cmangos/mangos-classic", "cmangos/playerbots"),
        "wow-tortoise": ("tortoise-wow/tortoise-wow", "Sagiroth/TortoiseBots"),
    }, moving


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
    """Nine of the ten shipped sources are `depth: 1`, and the count lies on every one.

    `Source.depth` defaults to 1 and only AzerothCore's core overrides it, and
    `ContainerGit.clone()` — the production seam — passes that depth on its
    update fetch. HEAD's parents are then cut at the graft while the old pin's
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
