"""Uninstall and purge, the code half (8.9a).

Every seam `yulon.purge` reaches the world through is injected here, so nothing
in this file needs Docker, a folder full of AzerothCore, or a GUI. What is
asserted is the part that cannot be recovered from if it is wrong: the
refusals, the ORDER, the scope, and which volume survives a ticked box.

The refusals come first in this file for the same reason they come first in the
module: this is the one action whose bug deletes somebody's server.
"""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Callable
from pathlib import Path

import pytest

from yulon import dbsecret, docker, logsnap, platform, purge
from yulon.catalog import composegen, native
from yulon.catalog.catalog import load_catalog
from yulon.catalog.families.cmangos import CmangosInstaller
from yulon.controller_wow_wotlk import docker_ctl
from yulon.ownership import Ownership

SPEC = docker_ctl.SPEC
GAME = "wow-wotlk"

UNASKED = object()
"""What a seam answers when Docker could not be asked at all.

A distinct sentinel and not `None`: `None` is the ANSWER those two seams give
for exactly that case, so a default of `None` in this harness would make
"Docker would not say" unexpressible — which is the case two of the tests below
are entirely about.
"""


def _images(server_dir: Path) -> tuple[str, ...]:
    """The four per-install refs `composegen.built_image_refs()` computes, spelled out."""
    tag = composegen.image_tag(server_dir)
    return tuple(
        f"yulon.local/ac-wotlk-{name}:{tag}"
        for name in ("worldserver", "authserver", "db-import", "client-data")
    )


class Recorder:
    """Every seam of one Uninstaller, recording what was called in what order.

    A single object rather than a bag of lambdas because the ORDER assertions
    are the point of most of this file, and an order is a property of the whole
    set of calls rather than of any one of them.
    """

    def __init__(
        self,
        server_dir: Path,
        *,
        project: str | None = "yulon-wow-wotlk-deadbeef",
        claim: Ownership = Ownership.OWNED,
        running: docker.Running | None = None,
        containers: object = UNASKED,
        volumes: object = UNASKED,
        snapshot: logsnap.Snapshot | None = None,
        remove_folder: Callable[[Path], None] | None = None,
        image_warning: str = "",
    ) -> None:
        self.server_dir = server_dir
        self.order: list[str] = []
        self.removed_volumes: list[str] = []
        self.removed_images: list[str] = []
        self.forgotten = 0
        self._project = project
        self._claim = claim
        self._running = running if running is not None else docker.Running()
        self._containers = [SPEC.world, SPEC.db] if containers is UNASKED else containers
        self._volumes = (
            [f"{project}_db-data", f"{project}_client-data"] if volumes is UNASKED else volumes
        )
        self._snapshot = snapshot or logsnap.Snapshot(path=Path("/logs/snap.log"))
        self._remove_folder = remove_folder
        self._image_warning = image_warning

    # -- read seams -------------------------------------------------------
    def claim(self, path: Path) -> Ownership:
        self.order.append("claim")
        return self._claim

    def project_of(self) -> str | None:
        self.order.append("project")
        return self._project

    def census(self, project: str) -> docker.Running:
        self.order.append(f"census:{project}")
        return self._running

    def containers_of(self, project: str) -> list[str] | None:
        self.order.append(f"containers:{project}")
        return None if self._containers is None else list(self._containers)

    def volumes_of(self, project: str) -> list[str] | None:
        self.order.append(f"volumes:{project}")
        return None if self._volumes is None else list(self._volumes)

    def folder_size(self, path: Path) -> int:
        self.order.append("size")
        return 2_300_000_000

    # -- write seams ------------------------------------------------------
    def snapshot(self) -> logsnap.Snapshot:
        self.order.append("snapshot")
        return self._snapshot

    def remove_containers(self) -> bool:
        self.order.append("remove_containers")
        return True

    def remove_volume(self, name: str) -> None:
        self.order.append(f"remove_volume:{name}")
        self.removed_volumes.append(name)

    def remove_image(self, ref: str) -> str:
        self.order.append(f"remove_image:{ref}")
        self.removed_images.append(ref)
        return self._image_warning

    def remove_folder(self, path: Path) -> None:
        self.order.append(f"remove_folder:{path}")
        if self._remove_folder is not None:
            self._remove_folder(path)

    def forget(self) -> None:
        self.order.append("forget")
        self.forgotten += 1

    # -- the object under test --------------------------------------------
    def uninstaller(self, **overrides: object) -> purge.Uninstaller:
        seams = dict(
            game=GAME,
            server_dir=self.server_dir,
            spec=SPEC,
            image_refs=_images(self.server_dir),
            claim=self.claim,
            project_of=self.project_of,
            census=self.census,
            containers_of=self.containers_of,
            volumes_of=self.volumes_of,
            folder_size=self.folder_size,
            snapshot=self.snapshot,
            remove_containers=self.remove_containers,
            remove_volume=self.remove_volume,
            remove_image=self.remove_image,
            remove_folder=self.remove_folder,
            forget=self.forget,
        )
        seams.update(overrides)
        return purge.Uninstaller(**seams)  # type: ignore[arg-type]


def _recorder(tmp_path: Path, **kwargs: object) -> Recorder:
    server_dir = tmp_path / "wowserver"
    server_dir.mkdir(exist_ok=True)
    return Recorder(server_dir, **kwargs)  # type: ignore[arg-type]


# -- 1. the ownership refusal ---------------------------------------------


def test_an_install_whose_record_cannot_be_read_is_refused_and_not_removed(
    tmp_path: Path,
) -> None:
    """`UNKNOWN` is the case the app knows LEAST about; it must act least freely."""
    rec = _recorder(tmp_path, claim=Ownership.UNKNOWN)
    with pytest.raises(purge.PurgeError) as caught:
        rec.uninstaller().run(keep_characters=False)
    assert "cannot read" in str(caught.value)
    assert "Nothing was removed" in str(caught.value)
    assert rec.order == ["claim"], rec.order


def test_a_folder_nothing_claims_is_refused_too_and_says_something_different(
    tmp_path: Path,
) -> None:
    """UNCLAIMED is not permission.

    `state.json` records whatever folder "Use existing…" was pointed at, so a
    user who adopted their own hand-built AzerothCore tree has a record naming
    a folder Yu'lon never created. Written as `if claim is UNKNOWN: refuse`,
    that user loses their server. Only OWNED authorises a delete — and the two
    refusals must not read the same, because the advice differs: one says a
    record here is unreadable, the other says there is no record at all.
    """
    rec = _recorder(tmp_path, claim=Ownership.UNCLAIMED)
    with pytest.raises(purge.PurgeError) as caught:
        rec.uninstaller().run(keep_characters=False)
    message = str(caught.value)
    assert "Nothing here says Yu'lon installed it" in message
    assert "cannot read" not in message
    assert rec.order == ["claim"], rec.order


def test_a_refusal_over_a_folder_that_is_entirely_gone_points_at_forget(tmp_path: Path) -> None:
    """T34: a deleted folder's UNCLAIMED refusal names the one way out it cannot offer itself.

    `server_dir` never being written also answers UNCLAIMED - reading a claim
    file out of a folder that is not there is the same `not is_file()` as
    reading one out of a folder that never had one - so this clause is the only
    thing telling the two apart in the sentence a user reads.
    """
    gone = tmp_path / "gone-for-good"
    message = purge.refusal_for(Ownership.UNCLAIMED, gone)
    assert "Nothing here says Yu'lon installed it" in message
    assert 'If the folder is gone for good, "Forget this install…" drops this tab.' in message


def test_an_unclaimed_folder_that_still_exists_gets_no_such_clause(tmp_path: Path) -> None:
    """The offer to forget is wrong advice for a folder that is still there to look at."""
    message = purge.refusal_for(Ownership.UNCLAIMED, tmp_path)
    assert "Forget this install" not in message


def test_the_two_refusals_do_not_share_their_wording(tmp_path: Path) -> None:
    """Three values, never two: the messages are how a user tells them apart."""
    unknown = purge.refusal_for(Ownership.UNKNOWN, tmp_path)
    unclaimed = purge.refusal_for(Ownership.UNCLAIMED, tmp_path)
    assert unknown and unclaimed and unknown != unclaimed
    assert purge.refusal_for(Ownership.OWNED, tmp_path) == ""


def test_a_plan_of_an_unprovable_install_carries_the_refusal_and_no_targets(
    tmp_path: Path,
) -> None:
    """The dialog must be able to show the refusal without the run being reached."""
    rec = _recorder(tmp_path, claim=Ownership.UNKNOWN)
    plan = rec.uninstaller().plan()
    assert plan.refusal
    assert plan.containers == () and plan.volumes == () and plan.images == ()


def test_an_install_whose_compose_project_cannot_be_resolved_is_refused(
    tmp_path: Path,
) -> None:
    """A `None` project is the refusal path, never a fallback: every command is scoped to it."""
    rec = _recorder(tmp_path, project=None)
    with pytest.raises(purge.PurgeError) as caught:
        rec.uninstaller().run(keep_characters=False)
    assert "which Docker project" in str(caught.value)
    assert "snapshot" not in rec.order


def test_docker_refusing_to_list_this_projects_containers_is_not_nothing_is_there(
    tmp_path: Path,
) -> None:
    """`project_containers()` answers None when Docker could not be asked."""
    rec = _recorder(tmp_path, containers=None)
    with pytest.raises(purge.PurgeError) as caught:
        rec.uninstaller().run(keep_characters=False)
    assert "could not ask Docker" in str(caught.value)
    assert "snapshot" not in rec.order


def test_containers_docker_will_not_name_an_owner_for_refuse_the_whole_purge(
    tmp_path: Path,
) -> None:
    """`unreadable` is not proof of anything, and this action cannot proceed on none."""
    rec = _recorder(tmp_path, running=docker.Running(unreadable=(SPEC.world,)))
    with pytest.raises(purge.PurgeError) as caught:
        rec.uninstaller().run(keep_characters=False)
    assert "would not say which project owns" in str(caught.value)


def test_a_stranger_wearing_our_container_names_refuses(tmp_path: Path) -> None:
    """Two installs of one game share container names; the label is the only proof."""
    rec = _recorder(tmp_path, running=docker.Running(strangers=((SPEC.world, "someone-else"),)))
    with pytest.raises(purge.PurgeError) as caught:
        rec.uninstaller().run(keep_characters=False)
    assert "someone-else" in str(caught.value)


# -- 2. the running refusal ------------------------------------------------


def test_a_purge_of_a_running_server_refuses_and_says_to_stop_it_first(
    tmp_path: Path,
) -> None:
    """Asked of the census that knows, not of a flag a caller passed in.

    `_running().ours` is what compose actually stamped, taken before any
    command is issued. Deleting the database volume out from under a live
    mysqld loses exactly the characters a ticked box promised to keep.
    """
    rec = _recorder(tmp_path, running=docker.Running(ours=(SPEC.world, SPEC.db)))
    with pytest.raises(purge.PurgeError) as caught:
        rec.uninstaller().run(keep_characters=True)
    message = str(caught.value)
    assert SPEC.world in message
    assert "Stop the server first" in message
    assert "snapshot" not in rec.order, "a refusal must not have written anything"


def test_the_running_refusal_is_asked_before_any_removal_command(tmp_path: Path) -> None:
    rec = _recorder(tmp_path, running=docker.Running(ours=(SPEC.world,)))
    with pytest.raises(purge.PurgeError):
        rec.uninstaller().run(keep_characters=False)
    assert not any(step.startswith("remove_") for step in rec.order), rec.order


def test_the_plan_of_a_running_install_says_so_rather_than_offering_a_purge(
    tmp_path: Path,
) -> None:
    rec = _recorder(tmp_path, running=docker.Running(ours=(SPEC.world,)))
    plan = rec.uninstaller().plan()
    assert "Stop the server first" in plan.refusal


# -- 3. the order ----------------------------------------------------------


def test_the_snapshot_is_first_and_the_record_is_forgotten_last(tmp_path: Path) -> None:
    """The whole order, asserted as a sequence rather than as a set of facts.

    A test that merely checks both happened passes with the two swapped. This
    one names the sequence, so a snapshot taken after `compose down` (the
    container's log goes with the container) or a record forgotten before the
    folder is gone (an install nobody owns, which is the one state the ownership
    refusal cannot recover from) fails here.
    """
    rec = _recorder(tmp_path)
    project = "yulon-wow-wotlk-deadbeef"
    rec.uninstaller().run(keep_characters=False)
    destructive = [step for step in rec.order if step.startswith(("snapshot", "remove_", "forget"))]
    assert destructive == [
        "snapshot",
        "remove_containers",
        f"remove_volume:{project}_db-data",
        f"remove_volume:{project}_client-data",
        *[f"remove_image:{ref}" for ref in _images(rec.server_dir)],
        f"remove_folder:{rec.server_dir}",
        "forget",
    ], rec.order


def test_the_snapshot_runs_while_the_containers_are_still_there(tmp_path: Path) -> None:
    """Not "both happened" — the snapshot seam refuses if the containers are gone.

    `logsnap.capture()` resolves the container through `compose ps` in the
    server dir, so it can only answer while both the containers and the compose
    files exist. Swap the two and this raises out of the snapshot.
    """
    rec = _recorder(tmp_path)

    def snapshot() -> logsnap.Snapshot:
        assert "remove_containers" not in rec.order, "the log was taken after the container went"
        assert rec.server_dir.exists(), "the log was taken after the folder went"
        rec.order.append("snapshot")
        return logsnap.Snapshot(path=Path("/logs/snap.log"))

    rec.uninstaller(snapshot=snapshot).run(keep_characters=False)
    assert rec.order.index("snapshot") < rec.order.index("remove_containers")


def test_a_folder_that_will_not_delete_leaves_the_record_pointing_at_it(
    tmp_path: Path,
) -> None:
    """The recoverable failure. Forget the record first and no surface can reach it again."""

    def explode(path: Path) -> None:
        raise purge.PurgeError(f"{path} could not be deleted")

    rec = _recorder(tmp_path, remove_folder=explode)
    with pytest.raises(purge.PurgeError):
        rec.uninstaller().run(keep_characters=False)
    assert rec.forgotten == 0, "the record was forgotten for a server that is still there"


def test_a_snapshot_that_fails_does_not_prevent_the_uninstall(tmp_path: Path) -> None:
    """`logsnap` never raises and never blocks the action it runs in front of."""
    rec = _recorder(tmp_path, snapshot=logsnap.Snapshot(problem="no such container"))
    report = rec.uninstaller().run(keep_characters=False)
    assert report.snapshot.problem == "no such container"
    assert rec.forgotten == 1


def test_the_record_is_still_forgotten_when_the_state_file_cannot_be_written(
    tmp_path: Path,
) -> None:
    """Everything else is gone, so this is a warning on a finished job, not a failure."""

    def forget() -> None:
        rec.order.append("forget")
        raise OSError("read-only file system")

    rec = _recorder(tmp_path)
    report = rec.uninstaller(forget=forget).run(keep_characters=False)
    assert report.record_forgotten is False
    assert any("state.json" in warning for warning in report.warnings), report.warnings


# -- 4. the scope ----------------------------------------------------------


def test_nothing_outside_this_projects_own_objects_is_ever_named(tmp_path: Path) -> None:
    """The neighbour is a second WotLK install: same container names, another project.

    Its volumes carry ITS project prefix and its images ITS install id. Nothing
    this purge issues may name any of them, and the container half goes through
    the project label rather than through the globally-pinned names.
    """
    neighbour = "yulon-wow-wotlk-cafebabe"
    rec = _recorder(tmp_path)
    rec.uninstaller().run(keep_characters=False)
    said = " ".join(rec.order)
    assert neighbour not in said
    assert f"{neighbour}_db-data" not in rec.removed_volumes
    for step in rec.order:
        if step.startswith(("census:", "containers:", "volumes:")):
            assert step.endswith("yulon-wow-wotlk-deadbeef"), step


def test_every_volume_removed_was_one_docker_listed_for_this_project(tmp_path: Path) -> None:
    """Discovered by label, never computed from a folder basename.

    The bash prior art hardcoded `wow-server-playerbots_ac-database` out of an
    assumed folder name, which either misses or hits somebody else's data.
    """
    rec = _recorder(tmp_path, volumes=["yulon-wow-wotlk-deadbeef_db-data"])
    rec.uninstaller().run(keep_characters=False)
    assert rec.removed_volumes == ["yulon-wow-wotlk-deadbeef_db-data"]


def test_only_this_installs_own_four_image_refs_are_removed(tmp_path: Path) -> None:
    """`--rmi all` would take `mysql:8.4` with it; `--rmi local` would take nothing."""
    rec = _recorder(tmp_path)
    rec.uninstaller().run(keep_characters=False)
    assert rec.removed_images == list(_images(rec.server_dir))
    assert not any("mysql" in ref for ref in rec.removed_images)
    assert not any("alpine" in ref for ref in rec.removed_images)


def test_every_tab_that_offers_an_uninstall_is_handed_this_installs_own_built_images(
    tmp_path: Path,
) -> None:
    """The JOIN, which the test above cannot reach: what the app actually hands one.

    `test_only_this_installs_own_four_image_refs_are_removed` asks a `Recorder`
    whose `image_refs` this file typed itself (`_images()`), so it proves the
    ENGINE removes what it is given and says nothing about what the app gives
    it. `reviews check functions, not call sites` is the standing lesson and
    this is that shape exactly: measured at `dcc64543` on 2026-09-08, replacing
    `image_refs=composegen.built_image_refs(entry, server_dir)` with
    `image_refs=()` at `yulon/ui/controller_view.py:778` left the whole suite
    byte-identical -- 3597 passed and the same five Windows line-ending failures
    -- while every WotLK install's four images leaked on every uninstall for
    ever. The same mutation at `:1128` was caught at once, by
    `test_uninstall_second_family.py::test_the_vanilla_uninstall_removes_this_installs_images_and_not_the_shared_one`,
    which is why this is enumerated over the catalog rather than written twice:
    a per-family assertion is a per-family hole, and 8.9c and 8.9d will each
    add a third and a fourth wiring of the identical five lines.

    The `_FakeUninstall` in `test_controller_view.py`'s 8.9a section is the
    other half of the reason nothing saw it: that whole section assigns
    `services.uninstall = fake`, so the real `for_entry()` is never asked what
    it wired.

    **What the refs must not be, and it is not hypothetical.** The bash prior
    art derives its removal list from the compose FILE
    (`_compose_server_images`, `cli/src/90-main.sh:80`), which includes the
    PULLED database image, and its whole defence is that the daemon refuses a
    removal while somebody holds it -- `docker image rm` failing becomes a
    warn. On m910q that defence is absent: `wow-vanilla` and `wow-tbc` both
    pull `mariadb:11`, the TBC install has no containers at all, so the daemon
    would not refuse, and a neighbour's next start would re-pull 458 MB over
    that box's known-flaky link. `built_image_refs()` never names a pulled
    image, so the safety is structural rather than a race the daemon usually
    wins.
    """
    from yulon.ui.controller_view import ControllerServices

    offered: dict[str, tuple[str, ...]] = {}
    for entry in load_catalog().games:
        server_dir = tmp_path / entry.id
        server_dir.mkdir()
        # `wow-vanilla`'s password plan is `generated`, and its Uninstaller
        # reads the file out of the folder it is about to delete before it will
        # build. Written for every entry rather than for the one that needs it:
        # which trees generate is a per-tree fact, and a fixture that knows
        # which is a fixture that goes quietly wrong when 8.9c adds one.
        (server_dir / ".db_password").write_text("hunter2\n", encoding="utf-8")
        services = ControllerServices.for_entry(entry, server_dir)
        if services.uninstall is None:
            continue
        offered[entry.id] = services.uninstall.image_refs
        built = composegen.built_image_refs(entry, server_dir)
        assert services.uninstall.image_refs == built, entry.id
        assert built, f"{entry.id}: an install with no built ref leaves its images behind for ever"
        for ref in built:
            assert ref.startswith("yulon.local/"), (entry.id, ref)
        db_image = entry.install.native.db.image if entry.install.native else None
        assert db_image not in built, (entry.id, db_image)

    # The subject, pinned before the rule is believed. An empty `offered` would
    # make every assertion above pass, and this box's own history is that a
    # tab's uninstall arrives one family at a time -- so the day 8.9c wires TBC
    # this goes red, and the fix is to add the id here rather than to widen a
    # rule that has stopped covering anything.
    assert sorted(offered) == ["wow-vanilla", "wow-wotlk"], sorted(offered)


def test_an_image_still_in_use_is_a_warning_and_not_a_failed_uninstall(
    tmp_path: Path,
) -> None:
    """A shared base layer another title holds is not this uninstall's problem."""
    rec = _recorder(tmp_path, image_warning="in use by another container")
    report = rec.uninstaller().run(keep_characters=False)
    assert report.warnings
    assert rec.forgotten == 1


def test_the_purge_never_prunes_anything(tmp_path: Path) -> None:
    """`image prune`, `builder prune` and `system prune` reach the whole daemon."""
    source = (Path(__file__).resolve().parents[1] / "yulon" / "purge.py").read_text(
        encoding="utf-8"
    )
    for word in ("prune", "--rmi", "system"):
        assert f'"{word}"' not in source, word


# -- 5. keep my characters -------------------------------------------------


def test_ticked_keeps_the_database_volume_and_the_client_data_volume(
    tmp_path: Path,
) -> None:
    """Two objects survive a ticked purge, and the second is the owner's call.

    Until 2026-09-08 this test pinned the opposite: ticked kept `_db-data` alone
    and removed `_client-data`, and 8.9a's own gate log shows it doing exactly
    that (`stage1.log:102`, `client-data volume gone: True`, on the TICKED
    press). The design of record never decided the 3.2 GB client-data volume
    either way; the owner did, the same morning (owner answer 3,
    `pyplan/phase8-owner-answers-2026-09-08.md`): an UNTICKED purge removes it,
    because "remove everything" that leaves 3.2 GB behind is the surprise
    nobody wants, and a TICKED purge keeps it, because a reinstall that keeps
    the characters should not re-download 3.2 GB of maps. Re-pointed, with the
    old expectation named here so the change is visible rather than silent.
    """
    rec = _recorder(tmp_path)
    report = rec.uninstaller().run(keep_characters=True)
    assert set(report.kept_volumes) == {
        "yulon-wow-wotlk-deadbeef_db-data",
        "yulon-wow-wotlk-deadbeef_client-data",
    }
    assert rec.removed_volumes == [], "a ticked purge removed a volume"


def test_the_plan_names_the_client_data_volume_so_the_dialog_can_say_it_is_kept(
    tmp_path: Path,
) -> None:
    """The dialog must name what a ticked press keeps BEFORE the press, from the plan."""
    plan = _recorder(tmp_path).uninstaller().plan()
    assert plan.client_volume == "yulon-wow-wotlk-deadbeef_client-data"
    assert plan.character_volume == "yulon-wow-wotlk-deadbeef_db-data"


def test_ticked_still_removes_the_folder_and_the_record(tmp_path: Path) -> None:
    """ "Keep my characters" is about one volume, not about keeping the install.

    The definition of done is that a reinstall TO THE SAME FOLDER finds the
    characters, which needs the folder gone so the installer has somewhere to
    install.
    """
    rec = _recorder(tmp_path)
    rec.uninstaller().run(keep_characters=True)
    assert f"remove_folder:{rec.server_dir}" in rec.order
    assert rec.forgotten == 1


def test_the_kept_volume_is_the_name_a_reinstall_to_this_folder_recomputes(
    tmp_path: Path,
) -> None:
    """The whole chain the checkbox rests on, asserted without Docker.

    The volume is `<project>_db-data`; the project is
    `yulon-<game>-<install_id>`; `install_id` is a hash of the folder's
    absolute path. So a reinstall to the SAME folder computes the same name and
    compose binds the pre-existing volume rather than creating an empty one.
    The decisions doc marks this "unverified — owed a test"; this is its code
    half, and the live gate is the other.
    """
    server_dir = tmp_path / "wowserver"
    server_dir.mkdir(exist_ok=True)
    project = composegen.project_name(GAME, server_dir)
    rec = Recorder(server_dir, project=project)
    report = rec.uninstaller().run(keep_characters=True)
    reinstalled = composegen.project_name(GAME, server_dir)
    assert f"{reinstalled}_db-data" in report.kept_volumes
    assert (
        f"{reinstalled}_client-data" in report.kept_volumes
    ), "the reinstall would re-download 3.2 GB of maps (owner answer 3, 2026-09-08)"


def test_unticked_leaves_no_container_volume_image_folder_or_record(
    tmp_path: Path,
) -> None:
    """The other direction of the checkbox, asserted as a whole."""
    rec = _recorder(tmp_path)
    report = rec.uninstaller().run(keep_characters=False)
    assert report.removed_containers is True
    assert report.kept_volumes == ()
    assert sorted(report.removed_volumes) == [
        "yulon-wow-wotlk-deadbeef_client-data",
        "yulon-wow-wotlk-deadbeef_db-data",
    ]
    assert report.removed_images == _images(rec.server_dir)
    assert report.folder_removed is True
    assert report.record_forgotten is True


def test_a_ticked_box_refuses_when_the_database_volume_cannot_be_identified(
    tmp_path: Path,
) -> None:
    """Fail closed: promising to keep characters and finding no volume to keep is a lie."""
    rec = _recorder(tmp_path, volumes=["yulon-wow-wotlk-deadbeef_client-data"])
    with pytest.raises(purge.PurgeError) as caught:
        rec.uninstaller().run(keep_characters=True)
    assert "database volume" in str(caught.value)
    assert rec.removed_volumes == []


def test_docker_refusing_to_list_volumes_refuses_the_purge(tmp_path: Path) -> None:
    """A volume list that could not be read is not an empty one."""
    rec = _recorder(tmp_path, volumes=None)
    with pytest.raises(purge.PurgeError) as caught:
        rec.uninstaller().run(keep_characters=False)
    assert "could not ask Docker" in str(caught.value)


# -- the plan --------------------------------------------------------------


def test_the_plan_names_the_folder_its_size_and_what_would_go(tmp_path: Path) -> None:
    """The dialog's numbers come from here, and `plan()` writes nothing."""
    rec = _recorder(tmp_path)
    plan = rec.uninstaller().plan()
    assert plan.refusal == ""
    assert plan.server_dir == rec.server_dir
    assert plan.folder_bytes == 2_300_000_000
    assert plan.containers == (SPEC.world, SPEC.db)
    assert plan.character_volume == "yulon-wow-wotlk-deadbeef_db-data"
    assert plan.images == _images(rec.server_dir)
    assert not any(step.startswith(("snapshot", "remove_", "forget")) for step in rec.order)


def test_the_plan_says_what_it_leaves_behind(tmp_path: Path) -> None:
    """Owner answer 1 is a list of exclusions, and a blast radius has to be readable.

    Backups especially: an uninstall that deleted them would make "Keep my
    characters" pointless in the one case it matters.
    """
    rec = _recorder(tmp_path)
    plan = rec.uninstaller().plan()
    left = " ".join(plan.left_behind).lower()
    assert "backup" in left
    assert "client" in left


# -- 7. the Windows read-only bit -----------------------------------------


def test_a_tree_whose_files_are_read_only_is_still_deleted(tmp_path: Path) -> None:
    """Git writes packs and loose objects read-only; a bare rmtree stops on them.

    Simulated here with a directory whose write bit is cleared, which is the
    POSIX shape of the same stop: `unlink` inside it is refused. The retry
    clears the bit and finishes. The Windows spelling of this
    (FILE_ATTRIBUTE_READONLY on the file itself) is unproven on this box — see
    the gate plan.
    """
    tree = tmp_path / "checkout"
    (tree / ".git" / "objects").mkdir(parents=True)
    victim = tree / ".git" / "objects" / "pack"
    victim.write_text("x", encoding="utf-8")
    victim.chmod(0o444)
    (tree / ".git" / "objects").chmod(0o555)

    purge.remove_tree(tree)
    assert not tree.exists()


@pytest.mark.skipif(
    os.name == "nt" or os.geteuid() == 0,  # type: ignore[attr-defined]
    reason=(
        "needs a directory whose write bit actually refuses an unlink: Windows ignores "
        "chmod on a directory, and root ignores the bit. The Windows spelling of this "
        "failure is its own press on the gate box."
    ),
)
def test_a_delete_that_cannot_finish_says_so_instead_of_reporting_success(
    tmp_path: Path,
) -> None:
    """The Rust prior art is `let _ = remove_dir_all(...)` and reports success having
    deleted nothing. This must fail loudly and name the path."""
    parent = tmp_path / "locked"
    tree = parent / "checkout"
    tree.mkdir(parents=True)
    (tree / "file").write_text("x", encoding="utf-8")
    parent.chmod(0o555)
    try:
        with pytest.raises(purge.PurgeError) as caught:
            purge.remove_tree(tree)
        assert str(tree) in str(caught.value)
        assert "read-only" in str(caught.value).lower()
    finally:
        parent.chmod(0o755)


@pytest.mark.skipif(
    os.name == "nt" or os.geteuid() == 0,  # type: ignore[attr-defined]
    reason=(
        "needs a directory the walk cannot enter. On Windows the specimen is a "
        "WSL-made symlink, which no Win32 call can create -- it is made by git "
        "inside the clone container and pressed live on the gate box. Root "
        "enters anything, so the POSIX specimen needs a normal user."
    ),
)
def test_a_directory_the_walk_cannot_enter_is_removed_rather_than_walked_into(
    tmp_path: Path,
) -> None:
    """`rmtree` recurses into it and stops; the whole uninstall then cannot finish.

    Measured on `yulon-win11` 2026-09-08, on a real WotLK install: AzerothCore's
    clone stage runs git inside a container with the server dir bind-mounted, so
    every symlink in the repository lands on NTFS as
    `IO_REPARSE_TAG_LX_SYMLINK` (0xa000001d). Python does not know that tag --
    `Path.is_symlink()` answers False and `entry.is_dir(follow_symlinks=False)`
    answers True -- so `shutil.rmtree` treats it as an ordinary directory,
    recurses, and dies with `[WinError 1920] The file cannot be accessed by the
    system`. Pressing Uninstall again did exactly the same thing: the folder was
    stuck at 12,405 entries with its containers, volumes and images already gone
    (`pyplan/gates/8.9a-wotlk-yulon-win11-2026-09-08/logs/retry.log`).

    The POSIX specimen is a directory whose mode refuses `scandir` -- the same
    shape, an entry the walk cannot enter -- and it is what makes the fix
    gateable off Windows. `_clear_read_only` does not rescue it: it adds the
    WRITE bit, and entering a directory needs execute.

    `os.rmdir` is what both specimens have in common: it removes a reparse point
    itself rather than its target, and an ordinary directory only when it is
    empty.
    """
    tree = tmp_path / "checkout"
    blocked = tree / ".claude" / "skills" / "generate-pr-description"
    blocked.mkdir(parents=True)
    (tree / "src").mkdir()
    (tree / "src" / "ordinary.txt").write_text("x", encoding="utf-8")
    blocked.chmod(0o000)

    try:
        purge.remove_tree(tree)
    finally:
        if blocked.exists():
            blocked.chmod(0o700)
    assert not tree.exists()


def test_a_wsl_resident_install_is_refused_rather_than_half_deleted(
    tmp_path: Path,
) -> None:
    """The folder is inside the distro and cannot be `rmtree`d from Windows.

    Refused cleanly rather than half-done: nothing here runs a command inside a
    distro yet, and a purge that removed the Docker objects and left the folder
    would leave an install the ownership refusal can no longer authorise.
    """
    rec = _recorder(tmp_path)
    with pytest.raises(purge.PurgeError) as caught:
        rec.uninstaller(wsl_distro="Ubuntu").run(keep_characters=False)
    assert "WSL" in str(caught.value)
    assert "snapshot" not in rec.order


# -- 6. the password the deleted folder was holding ------------------------
#
# The blocker the 8.9b lane found by reading this module's own code: a ticked
# purge keeps `<project>_db-data` and then deletes the folder — and on every
# `generated` entry the folder holds `.db_password`, the only copy of the
# password that volume was created with. "The characters are kept" was true in
# the letter and false in the substance: the reinstall to the same folder mints
# a NEW password and stops, because `CmangosInstaller._db_password` refuses to
# write one next to a volume that exists.


GENERATED_GAME = "wow-vanilla"
"""A shipped entry whose password is minted per install and kept in the folder.

`wow-wotlk` — the family 8.9a gates on — is `fixed`, so the catalog carries its
password and deleting the folder costs it nothing. That is exactly why the gate
could not have found this: the bug is invisible on the gated family.
"""

MADE_UP_SECRET = "vanilla-not-a-real-password"
"""Shaped like a generated value, and deliberately not one. Nothing depends on it."""


def test_a_ticked_purge_leaves_the_password_where_a_reinstall_will_look_for_it(
    tmp_path: Path,
) -> None:
    """The clause the checkbox promises, asked of the two halves that have to agree.

    Both ends run their PRODUCTION defaults: the purge's own way of deciding
    what is at risk and where to keep it, and the shipped installer's own way of
    resolving this install's secret. The folder is really deleted in between
    (`remove_folder=shutil.rmtree`), because the whole failure is that the
    secret lived inside it.

    What it asserts is not "a file was written somewhere" but the property that
    matters: after the purge, the thing a REINSTALL asks answers with the value
    that opens the kept volume.
    """
    server_dir = tmp_path / "wowserver"
    server_dir.mkdir()
    entry = load_catalog().get(GENERATED_GAME)
    plan = entry.install.password
    assert plan.mode == "generated" and plan.file, plan
    (server_dir / plan.file).write_text(MADE_UP_SECRET + "\n", encoding="utf-8")

    rec = Recorder(
        server_dir,
        project=composegen.project_name(GENERATED_GAME, server_dir),
        remove_folder=shutil.rmtree,
    )
    report = rec.uninstaller(game=GENERATED_GAME).run(keep_characters=True)

    assert report.kept_volumes and not server_dir.exists()
    engine = CmangosInstaller(entry, seams=native.Seams())
    assert engine.resolve_secrets(server_dir).db_password == MADE_UP_SECRET, (
        "the volume the tick kept was created with this password and the folder that "
        "held it is gone, so a reinstall that cannot find it keeps characters nothing "
        "can open"
    )


def test_a_ticked_purge_names_the_volume_the_copy_opens(tmp_path: Path) -> None:
    """The copy is filed against one volume, and the report says where it went.

    Both halves are what makes the copy usable rather than merely stored: the
    reinstall's stage checks the volume name before it trusts the value, and a
    user who wants to keep the secret elsewhere has to be told which file it is.
    """
    server_dir = tmp_path / "wowserver"
    server_dir.mkdir()
    entry = load_catalog().get(GENERATED_GAME)
    assert entry.install.password.file
    (server_dir / entry.install.password.file).write_text(MADE_UP_SECRET, encoding="utf-8")
    rec = Recorder(server_dir, project=composegen.project_name(GENERATED_GAME, server_dir))

    report = rec.uninstaller(game=GENERATED_GAME).run(keep_characters=True)

    assert report.secret_kept is not None
    kept = dbsecret.recall(GENERATED_GAME, composegen.install_id(server_dir))
    assert kept == dbsecret.Kept(password=MADE_UP_SECRET, volume=report.kept_volumes[0])


def test_a_ticked_purge_refuses_when_the_password_it_would_have_to_keep_is_gone(
    tmp_path: Path,
) -> None:
    """Fail closed, and fail EARLY: the folder may still hold a recoverable copy.

    Reported afterwards this is not a warning but an obituary - the volume is
    kept, its key is gone, and nothing can put either back. Refused here the
    user still has the folder, the file and the choice.
    """
    rec = _recorder(tmp_path)  # a folder with no `.db_password` in it
    with pytest.raises(purge.PurgeError) as caught:
        rec.uninstaller(game=GENERATED_GAME).run(keep_characters=True)
    message = str(caught.value)
    assert "Keep my characters" in message and "nothing was removed" in message
    assert rec.removed_volumes == [] and "snapshot" not in rec.order


def test_the_same_install_with_the_box_unticked_is_not_refused_and_keeps_nothing(
    tmp_path: Path,
) -> None:
    """The refusal is about a promise, not about a password.

    Same folder, same missing file, box unticked: the database is going with
    everything else, so there is nothing to keep and nothing to be unable to
    keep. A guard written as "generated entries need their password file"
    would have stopped this press too, for a reason that does not apply to it.
    """
    rec = _recorder(tmp_path)
    report = rec.uninstaller(game=GENERATED_GAME).run(keep_characters=False)
    assert report.secret_kept is None
    assert len(rec.removed_volumes) == 2
    assert not (platform.config_dir() / dbsecret.DIR_NAME).exists()


def test_a_fixed_password_install_keeps_no_copy_of_a_password_the_catalog_carries(
    tmp_path: Path,
) -> None:
    """The gated family, and the reason the gate could not have found this bug.

    `wow-wotlk` is `fixed`: the value is in `catalog.json`, so the folder never
    held the only copy and there is nothing for a purge to rescue. Copying it
    into the config directory anyway would spread a secret for no reason and
    make the ticked path look tested on a family where it is trivially true.
    """
    rec = _recorder(tmp_path)
    report = rec.uninstaller().run(keep_characters=True)
    assert report.kept_volumes and report.secret_kept is None
    assert not (platform.config_dir() / dbsecret.DIR_NAME).exists()


def test_a_copy_that_cannot_be_written_stops_the_purge_before_anything_is_removed(
    tmp_path: Path,
) -> None:
    """The write is a real reach at the disk, so it is a real way to lose a database.

    Placed above the line where the machine starts changing precisely so that
    this failure costs nothing: the folder, the volume and the record are all
    still there afterwards.
    """
    server_dir = tmp_path / "wowserver"
    server_dir.mkdir()
    entry = load_catalog().get(GENERATED_GAME)
    assert entry.install.password.file
    (server_dir / entry.install.password.file).write_text(MADE_UP_SECRET, encoding="utf-8")
    rec = Recorder(server_dir, project=composegen.project_name(GENERATED_GAME, server_dir))

    def refuse(password: str, volume: str) -> Path:
        raise OSError(28, "No space left on device")

    with pytest.raises(purge.PurgeError) as caught:
        rec.uninstaller(game=GENERATED_GAME, keep_secret=refuse).run(keep_characters=True)
    assert "No space left on device" in str(caught.value)
    assert "snapshot" not in rec.order, rec.order
    assert rec.removed_volumes == [] and rec.forgotten == 0
    assert server_dir.is_dir()


def test_the_kept_copy_is_created_owner_only_and_a_damaged_one_is_ignored(
    tmp_path: Path,
) -> None:
    """It is a password in a file, and a password in a file has two obligations.

    The mode is asserted on POSIX only, where it means something: on Windows
    `os.open`'s mode argument is ignored, and reading it back there would pass
    for the wrong reason (`channel_setup.CREDENTIAL_MODE` holds the same note).

    A hand-edited or truncated copy answers `None` rather than raising, because
    the caller is a reinstall: the honest outcome is that it mints a password
    and the stage that would lock a user out refuses on its own evidence.
    """
    path = dbsecret.remember(
        GENERATED_GAME, "deadbeef", password=MADE_UP_SECRET, volume="v_db-data"
    )
    assert dbsecret.recall(GENERATED_GAME, "deadbeef") == dbsecret.Kept(MADE_UP_SECRET, "v_db-data")
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == dbsecret.SECRET_MODE

    assert dbsecret.recall(GENERATED_GAME, "never-purged") is None
    path.write_text("{", encoding="utf-8")
    assert dbsecret.recall(GENERATED_GAME, "deadbeef") is None
    path.write_text('{"volume": "v_db-data", "password": ""}', encoding="utf-8")
    assert dbsecret.recall(GENERATED_GAME, "deadbeef") is None
