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


def _kept_route(recorded: list[str], sentence: str = "kept gone") -> native.KeptBuildRoute:
    """A kept-build route whose `remove()` records into the shared `recorded` list."""

    def remove() -> str:
        recorded.append("kept-remove")
        return sentence

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
        "1 still held by a stopped container was left.",
        "kept gone",
        "Docker's build cache: removed, 28.0 GB freed.",
    )
    assert "prune" in calls and "-f" not in " ".join(calls)


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
    """REBUILD and INSTALL_PRESS both refuse; naming the press, ending as specified."""
    touched: list[str] = []
    monkeypatch.setattr(
        docker, "dangling_image_ids", lambda label, **_: touched.append("list") or ()
    )
    monkeypatch.setattr(
        docker, "remove_unused_image", lambda image_id, **_: touched.append("rm") or ""
    )
    monkeypatch.setattr(docker, "prune_build_cache", lambda **_: touched.append("prune") or "")
    for press in (server_build_presses.REBUILD, native.INSTALL_PRESS):
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
