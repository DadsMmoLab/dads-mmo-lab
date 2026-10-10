"""T632: does a move go forward, in a SHALLOW checkout? (re-review of df3a9f49)

Every source but AzerothCore's core is a depth-1 clone: the tip is fetched at depth 1, then
the pin at depth 1, so both are grafts in `.git/shallow`, and later fetches connect new
commits back only to what is held. `merge-base --is-ancestor` answers 1 for a REAL forward
move through a graft; the parent ids in the commit objects survive grafting.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from yulon import git

COMMITS = 8


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "-c",
            "protocol.file.allow=always",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _upstream(tmp_path: Path) -> tuple[Path, list[str]]:
    up = tmp_path / "up"
    _git(tmp_path, "init", "-q", "up")
    shas = []
    for n in range(COMMITS):
        (up / "f").write_text(str(n))
        _git(up, "add", "f")
        _git(up, "commit", "-q", "-m", f"c{n}")
        shas.append(_git(up, "rev-parse", "HEAD"))
    return up, shas


def _clone(tmp_path: Path, up: Path, tip: str, *then: str) -> Path:
    """What a fresh install does: the tip at depth 1, then each later rev at depth 1."""
    cl = tmp_path / "cl"
    _git(tmp_path, "init", "-q", "cl")
    _git(cl, "remote", "add", "origin", f"file://{up}")
    _git(cl, "fetch", "-q", "--depth", "1", "origin", tip)
    _git(cl, "checkout", "-q", "FETCH_HEAD")
    for rev in then:
        _git(cl, "fetch", "-q", "--depth", "1", "origin", rev)
    return cl


def test_a_forward_move_through_a_graft_is_forward(tmp_path: Path) -> None:
    up, c = _upstream(tmp_path)
    cl = _clone(tmp_path, up, c[5], c[4], c[6])  # tip c5, pin c4 (c5's parent), then c6
    assert (cl / ".git" / "shallow").is_file()
    said = subprocess.run(["git", "merge-base", "--is-ancestor", c[4], c[6]], cwd=cl)
    assert said.returncode == 1, "the fixture must reproduce git's wrong answer"

    # c6's object names c5, and c5's names c4: the pin is found though c5 is a graft.
    assert git.RunnerGit().is_ancestor(cl, c[4], c[6]) is True


def test_a_move_several_commits_ahead_is_undecided_not_false(tmp_path: Path) -> None:
    up, c = _upstream(tmp_path)
    cl = _clone(tmp_path, up, c[5], c[4], c[7])  # c7's parent c6 is not held

    assert git.RunnerGit().is_ancestor(cl, c[4], c[7]) is None


def test_a_pin_the_objects_do_not_name_is_undecided_not_false(tmp_path: Path) -> None:
    up, c = _upstream(tmp_path)
    cl = _clone(tmp_path, up, c[5], c[2], c[7])  # c2 is three below the tip: no object names it

    assert git.RunnerGit().is_ancestor(cl, c[2], c[7]) is None


def test_a_backward_move_in_a_shallow_checkout_is_undecided(tmp_path: Path) -> None:
    up, c = _upstream(tmp_path)
    cl = _clone(tmp_path, up, c[5], c[4], c[6])

    assert git.RunnerGit().is_ancestor(cl, c[6], c[4]) is None


def test_a_full_clone_is_decided_both_ways(tmp_path: Path) -> None:
    up, c = _upstream(tmp_path)
    cl = tmp_path / "full"
    _git(tmp_path, "clone", "-q", f"file://{up}", "full")

    impl = git.RunnerGit()
    assert impl.is_ancestor(cl, c[2], c[7]) is True
    assert impl.is_ancestor(cl, c[7], c[2]) is False


def test_the_check_never_deepens_the_checkout(tmp_path: Path) -> None:
    up, c = _upstream(tmp_path)
    cl = _clone(tmp_path, up, c[5], c[4], c[6])
    before = (cl / ".git" / "shallow").read_text()

    git.RunnerGit().is_ancestor(cl, c[4], c[6])

    assert (cl / ".git" / "shallow").read_text() == before


def test_a_directory_that_is_no_checkout_is_undecided(tmp_path: Path) -> None:
    assert git.RunnerGit().is_ancestor(tmp_path, "a" * 40, "b" * 40) is None


def test_the_containerised_seam_walks_with_the_grafts_switched_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only the transport is swapped: the argv each question picks runs as host git."""
    up, c = _upstream(tmp_path)
    cl = _clone(tmp_path, up, c[5], c[4], c[6])
    seen: list[tuple[list[str], tuple[str, ...]]] = []

    def capture(
        self: git.ContainerGit,
        dest: Path,
        git_args: list[str],
        *,
        writes: bool,
        container_env: Sequence[str] = (),
    ) -> subprocess.CompletedProcess[str]:
        seen.append((git_args, tuple(container_env)))
        assert writes is False
        env = {**os.environ, **dict(one.split("=", 1) for one in container_env)}
        proc = subprocess.run(["git", *git_args], cwd=dest, env=env, capture_output=True, text=True)
        if proc.returncode != 0:
            raise git.GitError(f"containerized git x in {dest} exited {proc.returncode}: ")
        return proc

    monkeypatch.setattr(git.ContainerGit, "_capture", capture)

    assert git.ContainerGit().is_ancestor(cl, c[4], c[6]) is True
    assert seen[-1][1] == ("GIT_SHALLOW_FILE=/dev/null",), seen
    assert git.ContainerGit().is_ancestor(cl, c[2], c[7]) is None


def test_the_container_argv_carries_the_environment_before_the_image(tmp_path: Path) -> None:
    argv = git.ContainerGit(selinux_enforcing=lambda: False)._argv(
        "docker", tmp_path, ["rev-list"], writes=False, container_env=("A=b",)
    )

    assert argv.index("-e") < argv.index(git.ContainerGit().image)
    assert argv[argv.index("-e") + 1] == "A=b"


def test_a_backward_move_is_proved_by_the_reverse_ancestry_in_the_production_clone(
    tmp_path: Path,
) -> None:
    """Install: tip at depth 1, the pin at depth 1; the update then fetches with NO depth
    (`_update()`), which connects the new commits down to the pin. Going back from the new
    head to the pin: the pin is an ancestor of the head, so the move is backward, with no
    history read and no GitHub."""
    up, c = _upstream(tmp_path)
    cl = _clone(tmp_path, up, c[2], c[5])  # tip c2 (a graft), pin c5
    _git(cl, "fetch", "-q", "origin", c[7])  # the update's fetch, no --depth
    assert (cl / ".git" / "shallow").is_file()
    impl = git.RunnerGit()

    assert impl.is_ancestor(cl, c[7], c[5]) is False
    assert impl.is_ancestor(cl, c[5], c[7]) is True
    assert impl.is_ancestor(cl, c[5], c[5]) is True


class _Seams:
    """Just the two seams `moves_forward()` reads."""

    def __init__(self, local: bool | None, body: bytes | None) -> None:
        self.local, self.body, self.urls = local, body, []  # type: ignore[var-annotated]

    def is_ancestor(self, dest: Path, old: str, new: str) -> bool | None:
        return self.local

    def upstream_get(self, url: str, accept: str) -> bytes:
        self.urls.append(url)
        if self.body is None:
            raise OSError("rate limited")
        return self.body


def test_a_url_repo_is_asked_of_github_by_its_slug(tmp_path: Path) -> None:
    import json

    from yulon.catalog.families.direction import moves_forward

    seams = _Seams(None, json.dumps({"status": "ahead", "ahead_by": 3, "behind_by": 0}).encode())

    said = moves_forward(seams, "https://github.com/Sagiroth/TortoiseBots", tmp_path, "a", "b")  # type: ignore[arg-type]

    assert said == (True, "")
    assert seams.urls and "/repos/Sagiroth/TortoiseBots/compare/" in seams.urls[0], seams.urls


def test_a_repo_off_github_and_a_silent_github_are_each_said(tmp_path: Path) -> None:
    from yulon.catalog.families.direction import moves_forward

    off, why_off = moves_forward(_Seams(None, None), "https://gitlab.com/o/n", tmp_path, "a", "b")  # type: ignore[arg-type]
    silent, why = moves_forward(_Seams(None, None), "o/n", tmp_path, "a", "b")  # type: ignore[arg-type]

    assert off is None and "not on GitHub" in why_off
    assert silent is None and "try again later" in why and "walk" not in why


def test_the_container_environment_is_the_literal_null_device() -> None:
    source = Path(git.__file__).read_text(encoding="utf-8")
    assert "GIT_SHALLOW_FILE=/dev/null" in source and "os.devnull" not in source
