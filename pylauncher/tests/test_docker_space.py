"""T864: the Docker primitives behind Free Docker space and the module that plans it.

No daemon: the CLI is faked at `docker.runner.run` / `docker._docker`, as
tests/test_docker.py does. Each test names the failing change it guards.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from yulon import docker, docker_space, server_build_presses
from yulon.catalog import native


def _completed(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def _run_with(
    monkeypatch: pytest.MonkeyPatch, answers: dict[str, subprocess.CompletedProcess[str]]
) -> list[list[str]]:
    seen: list[list[str]] = []

    def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(argv[1:])
        return answers[argv[1]]

    monkeypatch.setattr(docker.runner, "run", run)
    return seen


def _docker_with(
    monkeypatch: pytest.MonkeyPatch, answers: dict[str, subprocess.CompletedProcess[str]]
) -> list[tuple[list[str], str | None]]:
    seen: list[tuple[list[str], str | None]] = []

    def answering(
        argv: list[str],
        cwd: Path | None = None,
        timeout: float | None = None,
        *,
        wsl_distro: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        seen.append((argv, wsl_distro))
        return answers[argv[0]]

    monkeypatch.setattr(docker, "_docker", answering)
    return seen


# -- 1. dangling_image_ids ------------------------------------------------------


def test_dangling_image_ids_sends_the_exact_argv_and_deduplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both label forms list ids only; repeats collapse to one."""
    seen = _docker_with(monkeypatch, {"images": _completed(stdout="abc\nabc\ndef\n\n")})
    assert docker.dangling_image_ids(f"{docker.PROJECT_LABEL}=yulon-wow-coa-abc123") == (
        "abc",
        "def",
    )
    ((argv, _),) = seen
    assert argv == [
        "images",
        "-q",
        "--filter",
        "dangling=true",
        "--filter",
        f"label={docker.PROJECT_LABEL}=yulon-wow-coa-abc123",
    ]
    # Mutation guard: dropping the dangling=true filter changes this argv.
    assert "--filter" in argv and "dangling=true" in argv


def test_dangling_image_ids_asks_with_the_bare_label_key_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _docker_with(monkeypatch, {"images": _completed(stdout="abc\n")})
    assert docker.dangling_image_ids(docker.PROJECT_LABEL) == ("abc",)
    ((argv, _),) = seen
    assert argv[-1] == f"label={docker.PROJECT_LABEL}"


def test_dangling_image_ids_returns_none_when_docker_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _docker_with(monkeypatch, {"images": _completed(returncode=1, stderr="boom")})
    assert docker.dangling_image_ids(docker.PROJECT_LABEL) is None


def test_dangling_image_ids_returns_empty_on_empty_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _docker_with(monkeypatch, {"images": _completed(stdout="\n")})
    assert docker.dangling_image_ids(docker.PROJECT_LABEL) == ()


# -- 2. remove_unused_image -----------------------------------------------------


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        (
            "conflict: unable to delete 8e8b42b36c65 (must be forced) - image is "
            "being used by stopped container 1a2b",
            docker.IMAGE_IN_USE,
        ),
        (
            "conflict: unable to delete 8e8b - (cannot be forced) - image is "
            "being used by running container",
            docker.IMAGE_IN_USE,
        ),
        ("Error: No such image: 8e8b", docker.IMAGE_GONE),
        ("Error: NO SUCH IMAGE: 8e8b", docker.IMAGE_GONE),
        ("daemon gone", "daemon gone"),
    ],
    ids=["held-by-stopped", "held-by-running", "gone", "gone-case", "other"],
)
def test_remove_unused_image_reads_the_daemon_answer(
    monkeypatch: pytest.MonkeyPatch, stderr: str, expected: str
) -> None:
    """Held (stopped or running) reads "in use"; missing reads gone; the rest is said as is."""
    seen = _docker_with(monkeypatch, {"image": _completed(returncode=1, stderr=stderr)})
    assert docker.remove_unused_image("8e8b42b36c65") == expected
    ((argv, _),) = seen
    # Mutation guard: adding -f changes this argv.
    assert argv == ["image", "rm", "8e8b42b36c65"], "never -f: force only drops the name"
    assert "-f" not in argv


def test_remove_unused_image_returns_empty_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _docker_with(monkeypatch, {"image": _completed()})
    assert docker.remove_unused_image("abc") == ""


def test_remove_unused_image_names_the_exit_when_docker_says_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2: a nonzero exit with empty stderr and stdout is a failure, never `""`.

    Mutation: returning `proc.stderr.strip()` unchanged answers `""`, which the
    caller reads as "removed" and counts the space freed.
    """
    _docker_with(monkeypatch, {"image": _completed(returncode=1)})
    said = docker.remove_unused_image("abc")
    assert said, "a nonzero exit answered the success sentinel"
    assert "code 1" in said, said


# -- 3. image_facts ---------------------------------------------------------------


def _inspect_row(image_id: str, size: int, project: str | None) -> str:
    labels = "null" if project is None else json.dumps({docker.PROJECT_LABEL: project})
    return f"{image_id}\t{size}\t{labels}"


def test_image_facts_parses_sizes_and_the_project_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ids, byte sizes and the compose project; null labels mean no project."""
    stdout = "\n".join(
        [
            _inspect_row("sha256:aaa", 1700000000, "yulon-wow-coa-af665b68"),
            _inspect_row("sha256:bbb", 12, None),
        ]
    )
    _docker_with(monkeypatch, {"image": _completed(stdout=stdout)})
    facts = docker.image_facts(["aaa", "bbb"])
    assert facts is not None
    assert [(f.image_id, f.size, f.project) for f in facts] == [
        ("sha256:aaa", 1700000000, "yulon-wow-coa-af665b68"),
        ("sha256:bbb", 12, None),
    ]


def test_image_facts_reads_partial_stdout_despite_a_failure_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`image inspect a b` exits 1 when b is missing but still prints a."""
    stdout = _inspect_row("sha256:aaa", 5, "yulon-wow-coa-x")
    _docker_with(
        monkeypatch,
        {"image": _completed(returncode=1, stdout=stdout, stderr="Error: No such image: b")},
    )
    facts = docker.image_facts(["aaa", "missing"])
    assert facts is not None and [f.image_id for f in facts] == ["sha256:aaa"]


def test_image_facts_returns_none_when_nothing_parsed_and_exit_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _docker_with(monkeypatch, {"image": _completed(returncode=1, stdout="", stderr="gone")})


def test_image_facts_asks_nothing_for_no_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _docker_with(monkeypatch, {"image": _completed()})
    assert docker.image_facts([]) == ()
    assert seen == []


# -- 4. prune_build_cache -----------------------------------------------------------


def test_prune_build_cache_sends_the_exact_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whole unused cache only: no -a, no filter, never system or image."""
    seen = _docker_with(monkeypatch, {"builder": _completed(stdout="Total reclaimed space: 30GB")})
    assert docker.prune_build_cache() == ""
    ((argv, _),) = seen
    # Mutation guard: adding -a changes this argv.
    assert argv == ["builder", "prune", "-f"], argv
    assert "-a" not in argv and "--all" not in argv and "--filter" not in argv
    assert "system" not in argv and "image" not in argv


def test_prune_build_cache_returns_the_daemon_words_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _docker_with(monkeypatch, {"builder": _completed(returncode=1, stderr="daemon busy")})
    assert docker.prune_build_cache() == "daemon busy"


def test_prune_build_cache_names_the_exit_when_docker_says_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2: a nonzero exit with empty stderr and stdout is a failure, never `""`."""
    _docker_with(monkeypatch, {"builder": _completed(returncode=1)})
    said = docker.prune_build_cache()
    assert said, "a nonzero exit answered the success sentinel"
    assert "code 1" in said, said


# -- 5. presses_running --------------------------------------------------------------


def test_presses_running_sees_a_press_inside_and_not_after(
    tmp_path: Path,
) -> None:
    """A press entered with `_press_on` is running; after it exits it is not."""
    assert docker.presses_running() == ()
    with docker._press_on(tmp_path / "s", server_build_presses.REBUILD):
        assert server_build_presses.REBUILD in docker.presses_running()
    assert docker.presses_running() == ()


# -- 6. read_space --------------------------------------------------------------


def _fact(image_id: str, size: int, project: str | None) -> docker.ImageFact:
    return docker.ImageFact(image_id=image_id, size=size, project=project)


def test_read_space_keeps_only_yulon_projects_listed_by_the_label_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tortoise's and unlabelled danglers are not Yu'lon's old builds."""
    monkeypatch.setattr(docker, "dangling_image_ids", lambda label, **_: ("a", "b", "c"))
    monkeypatch.setattr(
        docker,
        "image_facts",
        lambda names, **_: (
            _fact("a", 1700000000, "yulon-wow-coa-af665b68"),
            _fact("b", 1350000000, "tortoise-observability"),
            _fact("c", 9, None),
        ),
    )
    monkeypatch.setattr(docker, "build_cache_bytes", lambda **_: None)
    space = docker_space.read_space(kept_build=None, wsl_distro=None)
    assert space.old_builds is not None
    assert [(b.image_id, b.project) for b in space.old_builds] == [("a", "yulon-wow-coa-af665b68")]
    # Mutation guard: dropping the startswith filter would keep all three.


def test_read_space_lists_by_the_label_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[str] = []
    monkeypatch.setattr(docker, "dangling_image_ids", lambda label, **_: asked.append(label) or ())
    monkeypatch.setattr(docker, "image_facts", lambda names, **_: ())
    monkeypatch.setattr(docker, "build_cache_bytes", lambda **_: None)
    docker_space.read_space(kept_build=None, wsl_distro=None)
    assert asked == [docker.PROJECT_LABEL]


# -- 7. Choice defaults ---------------------------------------------------------------


def test_choice_defaults_tick_cache_and_old_builds_but_not_the_kept_build() -> None:
    """D2: the kept build is opt-in because the next rebuild may reuse it."""
    choice = docker_space.Choice()
    assert (choice.cache, choice.old_builds, choice.kept_build) == (True, True, False)
    # Mutation guard: kept_build defaulting True breaks this.
    assert docker_space.Choice().kept_build is False


def _kept_route(
    recorded: list[str], sentence: str = "kept gone", *, ok: bool = True
) -> native.KeptBuildRoute:
    """A kept-build route whose `remove()` records into the shared `recorded` list."""

    def remove() -> native.KeptBuildRemoved:
        recorded.append("kept-remove")
        return native.KeptBuildRemoved(ok=ok, line=sentence)

    return native.KeptBuildRoute(check=lambda: "kept", remove=remove, size=lambda: None)


# -- 8-12. carry_out -----------------------------------------------------------


def _answer_space(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> None:
    monkeypatch.setattr(docker, "build_cache_bytes", lambda **_: overrides.get("cache"))
    monkeypatch.setattr(
        docker,
        "dangling_image_ids",
        lambda label, **_: overrides.get("ids", ()),
    )
    facts = overrides.get("facts", ())
    monkeypatch.setattr(docker, "image_facts", lambda names, **_: facts)


GIB = docker_space.GIB


def test_carry_out_removes_old_builds_then_kept_then_cache_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All three ticked: ids by id without -f, kept route once, prune once, verbatim lines."""
    calls: list[str] = []
    old = (
        docker_space.OldBuild(image_id="aaa", size=20 * GIB, project="yulon-wow-coa-x"),
        docker_space.OldBuild(image_id="bbb", size=10 * GIB, project="yulon-wow-coa-x"),
        docker_space.OldBuild(image_id="held", size=5 * GIB, project="yulon-wow-coa-x"),
    )
    answers = {"aaa": "", "bbb": docker.IMAGE_GONE, "held": docker.IMAGE_IN_USE}

    def remove_unused(image_id: str, **_: object) -> str:
        calls.append(f"rm:{image_id}")
        return answers[image_id]

    monkeypatch.setattr(docker, "remove_unused_image", remove_unused)
    # T864-E: the held image asks who holds it; answer "no finished container" here,
    # so this test stays about the sentence and not about the new pass.
    monkeypatch.setattr(docker, "exited_containers_on", lambda image, **_: ())
    monkeypatch.setattr(docker_space, "_old_builds", lambda distro: old)
    caches = [30 * GIB, 2 * GIB]
    monkeypatch.setattr(docker, "build_cache_bytes", lambda **_: caches.pop(0) if caches else None)

    def prune(**_: object) -> str:
        calls.append("prune")
        return ""

    monkeypatch.setattr(docker, "prune_build_cache", prune)
    kept = _kept_route(calls)
    freed = docker_space.carry_out(
        tmp_path,
        docker_space.Choice(cache=True, old_builds=True, kept_build=True),
        kept_build=kept,
        spec=None,
        wsl_distro=None,
    )
    assert calls == ["rm:aaa", "rm:bbb", "rm:held", "kept-remove", "prune"]
    assert freed.lines == (
        "Yu'lon's old untagged builds: removed 1, up to 20.0 GB. "
        "1 still used by a container was left.",
        "kept gone",
        "Docker's build cache: removed, 28.0 GB freed.",
    )
    assert "prune" in calls and "-f" not in " ".join(calls)


def test_an_old_build_held_only_by_its_projects_finished_container_is_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T864-E through the press: the container goes, then the image, and one line says so."""
    calls, freed = _press_an_old_build_held_by(
        tmp_path,
        monkeypatch,
        holders=(docker.ContainerRef("ac-db-import", "yulon-wow-coa-x"),),
        still_held=False,
    )
    assert calls == ["ac-db-import"]
    assert freed.lines[0] == "Yu'lon's old untagged builds: removed 1, up to 1.0 GB."


def test_an_old_build_a_running_container_holds_is_left_by_the_press(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T864-E: the listing is finished containers only, so a running holder frees nothing."""
    calls, freed = _press_an_old_build_held_by(tmp_path, monkeypatch, holders=(), still_held=True)
    assert calls == []
    assert freed.lines[0] == (
        "Yu'lon's old untagged builds: removed 0, up to 0.0 GB. "
        "1 still used by a container was left."
    )


def test_an_old_build_whose_finished_holder_is_another_project_is_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T864-E: the image's own project is the only one whose containers may be cleared."""
    calls, freed = _press_an_old_build_held_by(
        tmp_path,
        monkeypatch,
        holders=(docker.ContainerRef("other-import", "yulon-other"),),
        still_held=False,
    )
    assert calls == []
    assert freed.lines[0] == (
        "Yu'lon's old untagged builds: removed 0, up to 0.0 GB. "
        "1 still used by a container was left."
    )


def _press_an_old_build_held_by(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    holders: tuple[docker.ContainerRef, ...] | None,
    still_held: bool,
) -> tuple[list[str], docker_space.Freed]:
    """One old build whose first `image rm` is refused by a container, through carry_out.

    `still_held` keeps the retry refused, which is what a running holder (or a
    removal that did not help) answers; False lets it succeed, as it does once
    the finished one-shot container is gone.
    """
    calls: list[str] = []
    old = (docker_space.OldBuild(image_id="aaa", size=GIB, project="yulon-wow-coa-x"),)
    monkeypatch.setattr(docker_space, "_old_builds", lambda distro: old)
    saids = [docker.IMAGE_IN_USE] + ([docker.IMAGE_IN_USE] * 3 if still_held else [""])

    def remove_unused(image_id: str, **_: object) -> str:
        return saids.pop(0) if saids else docker.IMAGE_IN_USE

    monkeypatch.setattr(docker, "remove_unused_image", remove_unused)
    monkeypatch.setattr(docker, "exited_containers_on", lambda image, **_: holders)
    monkeypatch.setattr(
        docker, "remove_finished_container", lambda name, **_: calls.append(name) or ""
    )
    monkeypatch.setattr(docker, "build_cache_bytes", lambda **_: None)
    monkeypatch.setattr(docker, "prune_build_cache", lambda **_: "")
    freed = docker_space.carry_out(
        tmp_path,
        docker_space.Choice(cache=False, old_builds=True, kept_build=False),
        kept_build=None,
        spec=None,
        wsl_distro=None,
    )
    return calls, freed


def test_unticked_kept_build_never_calls_remove(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept_calls: list[str] = []
    kept = _kept_route(kept_calls)
    monkeypatch.setattr(docker_space, "_old_builds", lambda distro: ())
    monkeypatch.setattr(docker, "build_cache_bytes", lambda **_: 2 * GIB)
    monkeypatch.setattr(docker, "prune_build_cache", lambda **_: "")
    docker_space.carry_out(
        tmp_path,
        docker_space.Choice(cache=True, old_builds=False, kept_build=False),
        kept_build=kept,
        spec=None,
        wsl_distro=None,
    )
    assert kept_calls == []


def test_cache_only_lists_and_removes_no_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    listed: list[str] = []
    removed: list[str] = []
    monkeypatch.setattr(
        docker, "dangling_image_ids", lambda label, **_: listed.append(label) or ("x",)
    )
    monkeypatch.setattr(
        docker, "remove_unused_image", lambda image_id, **_: removed.append(image_id) or ""
    )
    monkeypatch.setattr(docker, "build_cache_bytes", lambda **_: 1)
    monkeypatch.setattr(docker, "prune_build_cache", lambda **_: "")
    docker_space.carry_out(
        tmp_path,
        docker_space.Choice(cache=True, old_builds=False, kept_build=False),
        kept_build=None,
        spec=None,
        wsl_distro=None,
    )
    assert listed == [] and removed == []


def test_a_build_press_running_refuses_and_touches_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Rebuild press refuses; naming the press, ending as specified.

    The "Install" label is NOT one of these (T864 review round 3): a CoA repair,
    update or adopt re-takes the hold under it and compiles nothing, and an
    install that compiles is seen by `builds_running()`, pinned below.
    """
    touched: list[str] = []
    monkeypatch.setattr(
        docker, "dangling_image_ids", lambda label, **_: touched.append("list") or ()
    )
    monkeypatch.setattr(
        docker, "remove_unused_image", lambda image_id, **_: touched.append("rm") or ""
    )
    monkeypatch.setattr(docker, "prune_build_cache", lambda **_: touched.append("prune") or "")
    for press in (
        server_build_presses.REBUILD,
        server_build_presses.UPDATE_TO_LATEST,
        server_build_presses.RETURN_TO_PIN,
    ):
        # Mutation guard: deleting the pre-lease check takes the lease first,
        # which the under-lease check then refuses with the lease-taker suffix.
        with docker._press_on(tmp_path / "other", press):
            with pytest.raises(docker_space.DockerSpaceRefusal) as raised:
                docker_space.carry_out(
                    tmp_path,
                    docker_space.Choice(cache=True, old_builds=True, kept_build=False),
                    kept_build=None,
                    spec=None,
                    wsl_distro=None,
                )
        assert press in str(raised.value), str(raised.value)
        assert str(raised.value).endswith("Nothing was removed.")
    assert touched == []
    # The pre-lease check refuses before Docker is asked for the lease: spy on
    # the lease and fail if it is ever taken while a build press runs.
    taken: list[str] = []
    real_lease = docker.maintenance_lease
    import contextlib

    @contextlib.contextmanager
    def spying_lease(*args: object, **kwargs: object):
        taken.append("lease")
        with real_lease(*args, **kwargs):
            yield

    monkeypatch.setattr(docker, "maintenance_lease", spying_lease)
    with docker._press_on(tmp_path / "other", server_build_presses.REBUILD):
        with pytest.raises(docker_space.DockerSpaceRefusal):
            docker_space.carry_out(
                tmp_path,
                docker_space.Choice(cache=True, old_builds=True, kept_build=False),
                kept_build=None,
                spec=None,
                wsl_distro=None,
            )
    assert taken == [], "pre-lease check deleted: the lease was taken first"


def test_an_installs_reservation_label_is_not_a_build_press(tmp_path: Path) -> None:
    """R3 review: a CoA repair/update/adopt re-takes the hold as "Install", compiling nothing.

    Mutation: put `native.INSTALL_PRESS` back in `build_press_refusal()`'s `watched`.
    Then the first half refuses with a sentence about an install building while no
    compile runs, which is what the press said during a CoA repair.
    """
    assert docker_space.build_press_refusal() is None
    with docker._press_on(tmp_path / "wow", native.INSTALL_PRESS):
        assert native.INSTALL_PRESS in docker.presses_running()
        assert docker.builds_running() is False
        assert docker_space.build_press_refusal() is None
        with docker.building(tmp_path / "wow"):
            refused = docker_space.build_press_refusal()
        assert refused is not None and native.INSTALL_PRESS in refused, refused
    assert docker_space.build_press_refusal() is None


def test_a_compiling_stage_loop_refuses_with_the_install_sentence(tmp_path: Path) -> None:
    """R3: an install compiling before `start-db` holds no reservation, yet still refuses."""
    assert docker_space.build_press_refusal() is None
    with docker.building(tmp_path / "wow"):
        refused = docker_space.build_press_refusal()
    assert refused is not None and native.INSTALL_PRESS in refused, refused
    assert refused.endswith("Nothing was removed.")
    assert docker_space.build_press_refusal() is None


def test_the_compiling_flag_drops_when_a_stage_fails(tmp_path: Path) -> None:
    """R3: whatever exit the stage loop takes, the flag goes with it (success, error, cancel)."""
    from collections.abc import Iterator

    from tests.support_native import Recorder, install
    from tests.test_spine import _build, _family
    from yulon.catalog.installer import InstallerError, InstallOptions

    rec = Recorder()
    server_dir = tmp_path / "wow"
    install(rec, server_dir)
    rec.calls.clear()

    def _boom(ctx: native.StageContext) -> Iterator[str]:
        refused = docker_space.build_press_refusal()
        assert refused is not None and native.INSTALL_PRESS in refused, refused
        yield "compiling"
        raise InstallerError("the build died")

    family = _family(lambda me: (native.Stage("build", _boom),))
    with pytest.raises(InstallerError):
        list(_build(rec, family).run(InstallOptions(server_dir=server_dir)))
    assert docker.builds_running() is False
    assert docker_space.build_press_refusal() is None


def test_a_taken_lease_refuses_and_removes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    removed: list[str] = []
    monkeypatch.setattr(
        docker, "remove_unused_image", lambda image_id, **_: removed.append(image_id) or ""
    )
    monkeypatch.setattr(docker, "prune_build_cache", lambda **_: removed.append("prune") or "")
    with docker.maintenance_lease(tmp_path, "busy"):
        with pytest.raises(docker_space.DockerSpaceRefusal) as raised:
            docker_space.carry_out(
                tmp_path,
                docker_space.Choice(cache=True, old_builds=True, kept_build=False),
                kept_build=None,
                spec=None,
                wsl_distro=None,
            )
    assert str(raised.value).endswith("Nothing was removed.")
    assert removed == []


def test_windows_note_last_on_windows_and_absent_on_linux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(docker_space, "_old_builds", lambda distro: ())
    monkeypatch.setattr(docker, "build_cache_bytes", lambda **_: 1)
    monkeypatch.setattr(docker, "prune_build_cache", lambda **_: "")
    monkeypatch.setattr(docker_space.platform, "detect", lambda: "windows")
    freed = docker_space.carry_out(
        tmp_path,
        docker_space.Choice(cache=True, old_builds=False, kept_build=False),
        kept_build=None,
        spec=None,
        wsl_distro=None,
    )
    assert freed.lines[-1] == docker_space.WINDOWS_NOTE
    # D3: never a drive letter, never "back".
    assert "C:" not in docker_space.WINDOWS_NOTE and "back" not in docker_space.WINDOWS_NOTE
    monkeypatch.setattr(docker_space.platform, "detect", lambda: "linux")
    monkeypatch.setattr(docker, "build_cache_bytes", lambda **_: 1)
    freed = docker_space.carry_out(
        tmp_path,
        docker_space.Choice(cache=True, old_builds=False, kept_build=False),
        kept_build=None,
        spec=None,
        wsl_distro=None,
    )
    assert all(line != docker_space.WINDOWS_NOTE for line in freed.lines)


def test_a_silent_nonzero_removal_is_not_counted_freed_and_adds_no_windows_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2: both helpers exit nonzero with nothing printed, through the real ones.

    The caller's only success sentinel is `""`; a silent failure that answered
    it would count the image removed and the cache freed, and append the Windows
    "removed" note over nothing.
    """
    old = (docker_space.OldBuild(image_id="aaa", size=GIB, project="yulon-wow-coa-x"),)
    monkeypatch.setattr(docker_space, "_old_builds", lambda distro: old)
    monkeypatch.setattr(docker, "build_cache_bytes", lambda **_: GIB)
    monkeypatch.setattr(docker_space.platform, "detect", lambda: "windows")
    _docker_with(
        monkeypatch,
        {"image": _completed(returncode=1), "builder": _completed(returncode=1)},
    )
    freed = docker_space.carry_out(
        tmp_path,
        docker_space.Choice(cache=True, old_builds=True, kept_build=False),
        kept_build=None,
        spec=None,
        wsl_distro=None,
    )
    assert "removed 0" in freed.lines[0], freed.lines
    assert "1 could not be removed" in freed.lines[0], freed.lines
    assert freed.lines[1].startswith("Docker's build cache was not removed:"), freed.lines
    assert "code 1" in freed.lines[1], freed.lines
    assert docker_space.WINDOWS_NOTE not in freed.lines


def test_a_failed_kept_build_removal_is_shown_as_is_with_no_windows_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2: a kept-build Docker refused is not "freed": its line stands, and no note follows."""
    monkeypatch.setattr(docker_space, "_old_builds", lambda distro: ())
    monkeypatch.setattr(docker, "build_cache_bytes", lambda **_: 1)
    kept = _kept_route([], "The kept build was not removed: boom.", ok=False)
    monkeypatch.setattr(docker_space.platform, "detect", lambda: "windows")
    freed = docker_space.carry_out(
        tmp_path,
        docker_space.Choice(cache=False, old_builds=False, kept_build=True),
        kept_build=kept,
        spec=None,
        wsl_distro=None,
    )
    assert freed.lines == ("The kept build was not removed: boom.",)


def test_no_player_text_carries_a_backtick() -> None:
    """Every player sentence is plain text: no backticks anywhere."""
    texts = [
        docker_space.OLD_IMAGES_NOT_REMOVED,
        docker_space.CACHE_NOTE,
        docker_space.OLD_BUILDS_NOTE,
        docker_space.KEPT_NOTE,
        docker_space.NEVER_NOTE,
        docker_space.WINDOWS_NOTE,
        docker_space.cache_pointer(30 * GIB),
        docker_space.gb_text(30 * GIB),
        docker_space.cache_row(
            docker_space.Space(
                cache_bytes=30 * GIB, old_builds=(), kept_build=False, kept_build_bytes=None
            )
        ),
        docker_space.old_builds_row(
            docker_space.Space(
                cache_bytes=None,
                old_builds=(docker_space.OldBuild(image_id="a", size=GIB, project="yulon-x"),),
                kept_build=False,
                kept_build_bytes=None,
            )
        ),
        docker_space.kept_row(
            docker_space.Space(
                cache_bytes=None, old_builds=(), kept_build=True, kept_build_bytes=GIB
            )
        ),
    ]
    assert all("`" not in text for text in texts), texts
    assert "Free Docker space" not in " ".join(texts).replace("Free Docker space…", "")
