"""Cloning and updating git sources (game-agnostic).

Promoted out of `yulon.apply`, which owned the only clone seam the project had.
The native install engine (roadmap 6.2/6.3) needs the same operation for much
bigger sources — AzerothCore itself, its module tree, the client-data repo — on
machines that may not have `git` at all, so the seam becomes a module with two
implementations:

- `RunnerGit` shells out to the host's `git`, and is what Linux uses today.
- `ContainerGit` runs git *inside a container*, so macOS and Windows do not
  need a host git before they can install anything. Docker is already a hard
  requirement on those platforms; a second one would not be.

Both are `Git`, so the engine never learns which it got.

Two traps are baked in here rather than left for each caller to remember:

- **`core.autocrlf`.** Git for Windows defaults it to `true`, which rewrites
  AzerothCore's entrypoint `.sh` files to CRLF on checkout. That does not fail
  the clone, or the configure, or the build — it fails at *runtime*, as
  `/bin/sh^M: bad interpreter`, after a three-hour compile. Every clone here
  pins `core.autocrlf=false` and `core.eol=lf`.
- **Depth.** A shallow clone is much faster, but AzerothCore's CMake derives
  its revision string from git metadata, so the core wants a full one. Depth is
  therefore a field on `CloneSpec` and not a constant: the caller that knows
  which source it is asks for what that source needs.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from yulon import runner
from yulon.log import get_logger

logger = get_logger(__name__)

RunCmd = Callable[[list[str]], subprocess.CompletedProcess[str]]

# Pinned on every clone/update; see the module docstring.
_LINE_ENDING_ARGS = ["-c", "core.autocrlf=false", "-c", "core.eol=lf"]

_CONTAINER_GIT_IMAGE = "alpine/git:latest"

MISSING_GIT_HELP = {
    "linux": "Install git with your package manager (e.g. `sudo apt install git`) and try again.",
    "macos": (
        "Install Apple's Command Line Tools by running `xcode-select --install` in Terminal, "
        "then try again."
    ),
    "windows": "Install Git for Windows from https://git-scm.com/download/win and try again.",
}


class GitError(RuntimeError):
    """A git operation failed. The message carries git's own last words."""


@dataclass(frozen=True)
class CloneSpec:
    """One source to materialize at `dest`.

    Attributes:
        url: The repository to clone.
        dest: Where the working tree should end up.
        branch: Branch or tag to check out; None means the remote's default.
        sparse_path: Check out only this subdirectory (used for the guide/keg
            repos, where one directory out of a large tree is wanted).
        depth: Shallow-clone depth, or None for a full clone. Defaults to 1
            because most sources are content-only; AzerothCore's core repo must
            pass None, since its CMake reads the revision out of git metadata
            and a shallow clone gives it the wrong answer.
    """

    url: str
    dest: Path
    branch: str | None = None
    sparse_path: str | None = None
    depth: int | None = 1


class Git(Protocol):
    """Clone/update seam. Implementations raise `GitError` on failure."""

    def clone(self, spec: CloneSpec) -> None: ...


def _depth_args(depth: int | None) -> list[str]:
    return [] if depth is None else ["--depth", str(depth)]


def git_available(run: RunCmd | None = None) -> bool:
    """True if the host has a `git` that can actually run, without prompting.

    The probe is deliberately not "is git on PATH". On a Mac with no Command
    Line Tools, `/usr/bin/git` exists as a stub whose only behaviour is to pop a
    modal GUI installer and block until someone clicks it — from a launcher that
    is a hang, not an error. `xcode-select -p` answers the same question by
    exiting non-zero, and never opens a window.
    """
    do = run if run is not None else runner.run
    if shutil.which("git") is None:
        return False
    if sys.platform == "darwin":
        try:
            if do(["xcode-select", "-p"]).returncode != 0:
                logger.info("git_available(): git is the Command Line Tools stub, not a real git")
                return False
        except OSError:
            return False
    try:
        return do(["git", "--version"]).returncode == 0
    except OSError:
        return False


def _run_git(argv: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    proc = runner.run(argv, cwd=cwd)
    if proc.returncode != 0:
        raise GitError(f"{' '.join(argv)} exited {proc.returncode}: {proc.stderr.strip()}")
    return proc


class RunnerGit:
    """`Git` over the host's `git` CLI, through `yulon.runner`."""

    def clone(self, spec: CloneSpec) -> None:
        if (spec.dest / ".git").is_dir():
            self._update(spec)
            return
        if spec.dest.exists():
            shutil.rmtree(spec.dest)  # a non-git leftover; wow-manage.sh does the same
        spec.dest.parent.mkdir(parents=True, exist_ok=True)
        if spec.sparse_path is None:
            argv = ["git", *_LINE_ENDING_ARGS, "clone", *_depth_args(spec.depth)]
            if spec.branch:
                argv += ["--branch", spec.branch]
            _run_git([*argv, spec.url, str(spec.dest)])
            return
        self._sparse_clone(spec)

    def _sparse_clone(self, spec: CloneSpec) -> None:
        assert spec.sparse_path is not None
        dest = spec.dest
        dest.mkdir(parents=True, exist_ok=True)
        _run_git(["git", "init", "-q"], cwd=dest)
        _run_git(["git", "remote", "add", "origin", spec.url], cwd=dest)
        _run_git(["git", "config", "core.sparseCheckout", "true"], cwd=dest)
        _run_git(["git", "config", "core.autocrlf", "false"], cwd=dest)
        _run_git(["git", "config", "core.eol", "lf"], cwd=dest)
        (dest / ".git" / "info").mkdir(parents=True, exist_ok=True)
        (dest / ".git" / "info" / "sparse-checkout").write_text(
            spec.sparse_path.rstrip("/") + "/\n", encoding="utf-8", newline="\n"
        )
        pull = ["git", "pull", *_pull_depth_args(spec.depth), "origin", spec.branch or "HEAD"]
        _run_git(pull, cwd=dest)

    def _update(self, spec: CloneSpec) -> None:
        fetch = ["git", "fetch", *_pull_depth_args(spec.depth), "origin", spec.branch or "HEAD"]
        _run_git(fetch, cwd=spec.dest)
        _run_git(["git", "reset", "--hard", "FETCH_HEAD"], cwd=spec.dest)


def _pull_depth_args(depth: int | None) -> list[str]:
    """`git pull`/`git fetch` spell depth as one token, unlike `git clone`."""
    return [] if depth is None else [f"--depth={depth}"]


@dataclass(frozen=True)
class ContainerGit:
    """`Git` that runs git inside a container, for hosts without one.

    macOS and Windows both require Docker Desktop already, so cloning through a
    container removes the *second* prerequisite instead of adding one — no
    "install Git for Windows first, then come back". The destination directory
    is bind-mounted, so the working tree lands on the host exactly as a native
    clone would leave it.

    On Linux the container's root would own every cloned file, so the current
    uid/gid is passed through; on Docker Desktop the file-sharing layer already
    maps ownership to the logged-in user and `os.getuid` does not exist, which
    is the same condition.
    """

    image: str = _CONTAINER_GIT_IMAGE

    def clone(self, spec: CloneSpec) -> None:
        if (spec.dest / ".git").is_dir():
            self._run(
                spec, ["fetch", *_pull_depth_args(spec.depth), "origin", spec.branch or "HEAD"]
            )
            self._run(spec, ["reset", "--hard", "FETCH_HEAD"])
            return
        if spec.dest.exists():
            shutil.rmtree(spec.dest)
        spec.dest.mkdir(parents=True, exist_ok=True)
        argv = ["clone", *_depth_args(spec.depth)]
        if spec.branch:
            argv += ["--branch", spec.branch]
        if spec.sparse_path is not None:
            argv += ["--filter=blob:none", "--sparse"]
        # The clone target is `.` because the mount point *is* the destination.
        self._run(spec, [*argv, spec.url, "."])
        if spec.sparse_path is not None:
            self._run(spec, ["sparse-checkout", "set", spec.sparse_path.rstrip("/")])

    def _run(self, spec: CloneSpec, git_args: list[str]) -> None:
        argv = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{spec.dest}:/git",
            *self._user_args(),
            self.image,
            *_LINE_ENDING_ARGS,
            *git_args,
        ]
        proc = runner.run(argv)
        if proc.returncode != 0:
            raise GitError(f"containerized git {' '.join(git_args)} failed: {proc.stderr.strip()}")

    @staticmethod
    def _user_args() -> list[str]:
        getuid = getattr(os, "getuid", None)
        getgid = getattr(os, "getgid", None)
        if getuid is None or getgid is None:
            return []
        return ["--user", f"{getuid()}:{getgid()}"]
