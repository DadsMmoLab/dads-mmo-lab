"""T864 D1: a successful build removes its own old images; failures never do.

The Seams doubles live in `tests/support_native.Recorder` next to
`build_cache_bytes` (so every pinned call list in the suite stays as it is):
`dangling_asked` records what project label was asked, `unused_removed`
records removals, `unused_answers` answers per id.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support_native import ENTRY, Recorder, engine, install
from yulon import docker
from yulon.catalog import composegen
from yulon.catalog.installer import InstallerError, InstallOptions  # noqa: E402


def _project(server_dir: Path, rec: Recorder) -> str:
    eng = engine(rec)
    return composegen.project_name(
        ENTRY.id,
        server_dir,
        platform_id="macos",
        install_id=eng._install_id(server_dir),
    )


def _install(rec: Recorder, tmp_path: Path) -> Path:
    server_dir = tmp_path / "wow"
    install(rec, server_dir)
    rec.calls.clear()
    rec.dangling_asked.clear()
    rec.unused_removed.clear()
    rec.holders_asked.clear()
    rec.containers_removed.clear()
    return server_dir


def test_a_successful_rebuild_asks_once_with_this_projects_label_and_removes_each_id(
    tmp_path: Path,
) -> None:
    """The listing is this install's project label; every id is removed; the closing stays last."""
    rec = Recorder()
    server_dir = _install(rec, tmp_path)
    project = _project(server_dir, rec)
    rec.dangling_ids = ("aaa", "bbb")
    said = list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert rec.dangling_asked == [f"{docker.PROJECT_LABEL}={project}"]
    # Mutation guard: passing the bare label key breaks the asked assertion above.
    assert rec.dangling_asked != [docker.PROJECT_LABEL]
    assert rec.unused_removed == ["aaa", "bbb"]
    assert said[-1] == f"{ENTRY.name} was rebuilt and is running in {server_dir}"


def test_an_in_use_image_yields_no_line_but_is_still_listed_next_time(
    tmp_path: Path,
) -> None:
    rec = Recorder()
    server_dir = _install(rec, tmp_path)
    rec.dangling_ids = ("aaa",)
    rec.unused_answers = {"aaa": docker.IMAGE_IN_USE}
    said = list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert said[-1] == f"{ENTRY.name} was rebuilt and is running in {server_dir}"
    assert all("old images" not in line for line in said)
    rec.dangling_asked.clear()
    rec.unused_removed.clear()
    said = list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert rec.dangling_asked == [f"{docker.PROJECT_LABEL}={_project(server_dir, rec)}"]
    assert rec.unused_removed == ["aaa"]
    assert said[-1] == f"{ENTRY.name} was rebuilt and is running in {server_dir}"


def test_a_failed_rebuild_never_lists(tmp_path: Path) -> None:
    """A build that failed never reaches the pass (it raises before the success tail)."""
    rec = Recorder()
    server_dir = _install(rec, tmp_path)
    rec.build_result = docker.AttachedRun(1, ("cc1plus: error",))
    with pytest.raises(InstallerError):
        list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    # Mutation guard: moving the call into a finally (or before the raise paths) lists here.
    assert rec.dangling_asked == []


def test_a_successful_install_runs_it_too_and_keeps_the_closing_last(
    tmp_path: Path,
) -> None:
    rec = Recorder()
    server_dir = tmp_path / "wow"
    said = list(install(rec, server_dir))
    project = _project(server_dir, rec)
    assert rec.dangling_asked == [f"{docker.PROJECT_LABEL}={project}"]
    assert rec.unused_removed == []
    assert said[-1] == f"{ENTRY.name} is installed and running in {server_dir}"


def test_cache_pointer_above_threshold_and_silent_at_it(tmp_path: Path) -> None:
    """21 GiB appends exactly the pointer line; 20 GiB appends nothing (strictly greater)."""
    from yulon import docker_space

    for gib, expect_pointer in ((21, True), (20, False)):
        rec = Recorder()
        rec.build_cache = gib * docker_space.GIB
        server_dir = tmp_path / f"w{gib}" / "wow"
        install(rec, server_dir)
        rec.calls.clear()
        rec.dangling_asked.clear()
        rec.unused_removed.clear()
        said = list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
        pointer = docker_space.cache_pointer(gib * docker_space.GIB)
        if expect_pointer:
            # Mutation guard: >= would also fire at exactly 20 GiB.
            assert pointer in said, said
        else:
            assert pointer not in said, said
        assert said[-1] == f"{ENTRY.name} was rebuilt and is running in {server_dir}"


def test_at_most_one_failure_line_and_the_build_still_ends(tmp_path: Path) -> None:
    """None-listing, two daemon refusals, and a raising listing each yield exactly one line."""
    from yulon import docker_space

    rec = Recorder()
    server_dir = _install(rec, tmp_path)
    rec.dangling_ids = None  # Docker would not list
    said = list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert said.count(docker_space.OLD_IMAGES_NOT_REMOVED) == 1
    assert said[-1] == f"{ENTRY.name} was rebuilt and is running in {server_dir}"

    rec2 = Recorder()
    server_dir2 = tmp_path / "w2" / "wow"
    install(rec2, server_dir2)
    rec2.calls.clear()
    rec2.dangling_asked.clear()
    rec2.unused_removed.clear()
    rec2.dangling_ids = ("a", "b")
    rec2.unused_answers = {"a": "daemon gone", "b": "daemon gone"}
    said2 = list(engine(rec2).rebuild(InstallOptions(server_dir=server_dir2)))
    assert said2.count(docker_space.OLD_IMAGES_NOT_REMOVED) == 1, said2

    rec3 = Recorder()
    server_dir3 = tmp_path / "w3" / "wow"
    install(rec3, server_dir3)
    rec3.dangling_asked.clear()
    rec3.unused_removed.clear()
    before = list(rec3.dangling_asked)
    rec3.dangling_asked.append("x")
    rec3.dangling_ids = ("y",)
    raising_calls: list[str] = []

    def raising(label: str) -> tuple[str, ...] | None:
        raising_calls.append(label)
        raise RuntimeError("daemon hung up")

    # Mutation guard: dropping the except lets this raise instead of yielding one line.
    said3 = list(
        engine(rec3, dangling_image_ids=raising).rebuild(InstallOptions(server_dir=server_dir3))
    )
    assert said3.count(docker_space.OLD_IMAGES_NOT_REMOVED) == 1
    assert said3[-1] == f"{ENTRY.name} was rebuilt and is running in {server_dir3}"
    assert before == [] and raising_calls != []


def _holder(
    name: str,
    service: str,
    *,
    state: str = "exited",
    project: str | None,
) -> docker.ContainerRef:
    """One container as `containers_on()` lists it: an id, its state and both labels."""
    return docker.ContainerRef(
        id=f"id-{name}", name=name, state=state, project=project, service=service
    )


def test_an_image_held_only_by_this_projects_finished_one_shot_is_removed(
    tmp_path: Path,
) -> None:
    """T864-E: the recreate never selects ac-db-import, so its container kept the image.

    Mutation: drop the project-label check in `remove_finished_holders_on()` and
    the other-project test below fails; skip the retry and this one does.
    """
    rec = Recorder()
    server_dir = _install(rec, tmp_path)
    project = _project(server_dir, rec)
    rec.dangling_ids = ("aaa",)
    rec.in_use_once = {"aaa"}
    rec.holders = {"aaa": (_holder("ac-db-import", "ac-db-import", project=project),)}
    said = list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert rec.holders_asked == ["aaa"]
    assert rec.containers_removed == ["id-ac-db-import"]
    # Asked twice: refused while the container held it, removed after the container went.
    assert rec.unused_removed == ["aaa", "aaa"]
    assert all("old images" not in line for line in said), said
    assert said[-1] == f"{ENTRY.name} was rebuilt and is running in {server_dir}"


def test_an_image_a_running_container_holds_is_left_alone(tmp_path: Path) -> None:
    """T864-E: a RUNNING holder is listed, and every rule says leave it."""
    rec = Recorder()
    server_dir = _install(rec, tmp_path)
    project = _project(server_dir, rec)
    rec.dangling_ids = ("aaa",)
    rec.in_use_once = {"aaa"}
    rec.holders = {
        "aaa": (_holder("ac-worldserver", "ac-worldserver", state="running", project=project),)
    }
    said = list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert rec.containers_removed == []
    assert rec.unused_removed == ["aaa"]
    assert all("old images" not in line for line in said), said
    assert said[-1] == f"{ENTRY.name} was rebuilt and is running in {server_dir}"


def test_an_image_whose_finished_holder_belongs_to_another_project_is_left_alone(
    tmp_path: Path,
) -> None:
    """T864-E: an image id is not proof of ownership; another project's container stays."""
    rec = Recorder()
    server_dir = _install(rec, tmp_path)
    rec.dangling_ids = ("aaa",)
    rec.in_use_once = {"aaa"}
    rec.holders = {"aaa": (_holder("other-import", "ac-db-import", project="yulon-other"),)}
    said = list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert rec.containers_removed == []
    assert rec.unused_removed == ["aaa"]
    assert said[-1] == f"{ENTRY.name} was rebuilt and is running in {server_dir}"


def test_a_listing_that_failed_leaves_the_image_and_the_build_ends(tmp_path: Path) -> None:
    """T864-E: "the daemon did not answer" removes no container and fails no build."""
    from yulon import docker_space

    rec = Recorder()
    server_dir = _install(rec, tmp_path)
    rec.dangling_ids = ("aaa",)
    rec.in_use_once = {"aaa"}
    rec.holders = {"aaa": None}
    said = list(engine(rec).rebuild(InstallOptions(server_dir=server_dir)))
    assert rec.holders_asked == ["aaa"]
    assert rec.containers_removed == []
    assert rec.unused_removed == ["aaa"]
    assert said.count(docker_space.OLD_IMAGES_NOT_REMOVED) == 0, said
    assert said[-1] == f"{ENTRY.name} was rebuilt and is running in {server_dir}"


def test_held_rebuilds_return_before_the_success_tail_so_they_never_list() -> None:
    """T739's held press returns before it; pinned here so a move of the call
    site into a finally (or ahead of the held return) breaks this, not review."""
    from yulon.catalog import native

    held_path = native.StagedInstaller.rebuild
    assert held_path is not None
    import inspect

    source = inspect.getsource(held_path)
    assert "held_rebuild_ending" in source and "_free_own_old_images" in source
    assert source.index("held_rebuild_ending") < source.index("_free_own_old_images")
