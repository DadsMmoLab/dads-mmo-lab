"""Tests for the promoted git seam (`yulon.git`).

All subprocess calls are mocked at the `yulon.runner.run` boundary, so nothing
here clones anything. What is worth asserting is not "git was called" but the
three decisions this module exists to hold: line endings are pinned, depth is
the caller's choice, and probing for git must never open a GUI.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from yulon import git, runner


def _completed(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Record every argv `yulon.runner.run` is asked for; answer success."""
    calls: list[list[str]] = []

    def fake_run(argv: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return _completed()

    monkeypatch.setattr(runner, "run", fake_run)
    return calls


# -- line endings -----------------------------------------------------------


def test_clone_pins_line_endings_so_a_windows_checkout_is_not_crlf(
    seen: list[list[str]], tmp_path: Path
) -> None:
    """Git for Windows defaults `core.autocrlf=true`, which breaks the build at RUNTIME.

    A CRLF-mangled entrypoint passes the clone, the configure and the compile,
    then fails as `/bin/sh^M: bad interpreter` — after three hours. Pinning it
    on the command line costs nothing and cannot be forgotten per caller.
    """
    git.RunnerGit().clone(git.CloneSpec(url="https://example/repo.git", dest=tmp_path / "core"))
    argv = seen[0]
    assert argv[:5] == ["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf"]
    assert "clone" in argv


# -- depth ------------------------------------------------------------------


def test_clone_is_shallow_by_default(seen: list[list[str]], tmp_path: Path) -> None:
    """Most sources are content-only, so one commit is all anyone needs."""
    git.RunnerGit().clone(git.CloneSpec(url="https://example/mod.git", dest=tmp_path / "mod"))
    assert "--depth" in seen[0]
    assert seen[0][seen[0].index("--depth") + 1] == "1"


def test_depth_none_asks_for_a_full_clone(seen: list[list[str]], tmp_path: Path) -> None:
    """AzerothCore's CMake reads its revision from git metadata; shallow lies to it."""
    git.RunnerGit().clone(
        git.CloneSpec(url="https://example/core.git", dest=tmp_path / "core", depth=None)
    )
    assert "--depth" not in seen[0]


def test_update_of_an_existing_clone_fetches_and_resets(
    seen: list[list[str]], tmp_path: Path
) -> None:
    """A dest that is already a clone is updated in place, not re-cloned."""
    dest = tmp_path / "mod"
    (dest / ".git").mkdir(parents=True)
    git.RunnerGit().clone(git.CloneSpec(url="https://example/mod.git", dest=dest, branch="master"))
    assert seen == [
        ["git", "fetch", "--depth=1", "origin", "master"],
        ["git", "reset", "--hard", "FETCH_HEAD"],
    ]


# -- failures ---------------------------------------------------------------


def test_a_failed_git_carries_gits_own_last_words(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The error a user sees must be git's, not a generic 'clone failed'."""
    monkeypatch.setattr(
        runner,
        "run",
        lambda argv, cwd=None: _completed(returncode=128, stderr="fatal: repository not found"),
    )
    with pytest.raises(git.GitError, match="repository not found"):
        git.RunnerGit().clone(git.CloneSpec(url="https://example/nope.git", dest=tmp_path / "x"))


# -- probing ----------------------------------------------------------------


def test_git_available_is_false_when_there_is_no_git(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(git.shutil, "which", lambda _name: None)
    assert git.git_available() is False


def test_git_available_refuses_the_macos_command_line_tools_stub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a bare Mac `/usr/bin/git` exists but only opens a modal installer.

    Running it from a launcher is a hang, not an error, so the probe asks
    `xcode-select -p` — which answers the same question and opens no window.
    """
    monkeypatch.setattr(git.shutil, "which", lambda _name: "/usr/bin/git")
    monkeypatch.setattr(git.sys, "platform", "darwin")
    asked: list[list[str]] = []

    def fake_run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        asked.append(argv)
        return _completed(returncode=2, stderr="error: unable to get active developer directory")

    assert git.git_available(run=fake_run) is False
    assert asked == [["xcode-select", "-p"]], "must not invoke git itself on a bare Mac"


def test_git_available_accepts_a_mac_with_the_tools_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(git.shutil, "which", lambda _name: "/usr/bin/git")
    monkeypatch.setattr(git.sys, "platform", "darwin")
    assert git.git_available(run=lambda _argv: _completed()) is True


# -- containerized git ------------------------------------------------------


def test_container_git_mounts_the_destination_and_clones_into_it(
    seen: list[list[str]], tmp_path: Path
) -> None:
    """macOS/Windows already require Docker, so git need not be a second prerequisite."""
    dest = tmp_path / "core"
    git.ContainerGit().clone(git.CloneSpec(url="https://example/core.git", dest=dest, depth=None))
    argv = seen[0]
    assert argv[:4] == ["docker", "run", "--rm", "-v"]
    assert argv[4] == f"{dest}:/git"
    assert "core.autocrlf=false" in argv, "the CRLF trap applies inside the container too"
    assert argv[-2:] == ["https://example/core.git", "."]
    assert "--depth" not in argv


def test_container_git_reports_a_failure_as_a_git_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        runner, "run", lambda argv, cwd=None: _completed(returncode=1, stderr="could not resolve")
    )
    with pytest.raises(git.GitError, match="could not resolve"):
        git.ContainerGit().clone(
            git.CloneSpec(url="https://example/core.git", dest=tmp_path / "core")
        )
