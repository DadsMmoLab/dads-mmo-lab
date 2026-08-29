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

from yulon import platform, runner
from yulon.log import get_logger

logger = get_logger(__name__)

RunCmd = Callable[[list[str]], subprocess.CompletedProcess[str]]

# Applied to a running git process. `git -c k=v` is the *wrapper* form: it
# affects that invocation and writes nothing into the repository, so it must be
# repeated on every later command against the same clone.
_LINE_ENDING_ARGS = ["-c", "core.autocrlf=false", "-c", "core.eol=lf"]

# Written INTO the new repository, so later fetch/reset/checkout inherit it even
# when nobody remembers to pass the flags. `git clone --config` is the form that
# persists; `git -c` is not. Measured: after `git -c core.autocrlf=false clone`,
# the new .git/config contains no core.* keys at all, and the next
# `git reset --hard` re-checks-out the files it rewrites with CRLF on Windows —
# reintroducing the `/bin/sh^M: bad interpreter` failure this module exists to
# prevent, on the update path rather than the clone path.
_LINE_ENDING_CONFIG = [
    "--config",
    "core.autocrlf=false",
    "--config",
    "core.eol=lf",
]

# HTTP/1.1 for the transport, in both forms, for the same reason the line-ending
# settings are in both: the wrapper form covers this invocation, the persisted
# form covers every later fetch against the clone.
#
# Measured, not inherited: `git clone` of `azerothcore-wotlk` (224k objects) on
# real Windows died with `fetch-pack: invalid index-pack output` /
# `unexpected disconnect while reading sideband packet`, and the same clone over
# HTTP/1.1 succeeded (2026-08-22, `pyplan/checklist.md`). The Rust launcher hit
# the same wall from the other side — a 1.3 GB clone over HTTP/2 dying with
# `curl 92 CANCEL (err 8)`, presenting as `early EOF`, which killed a real
# install at 9% (`rust-prior-art.md` §4).
#
# It is applied to the containerized git too, even though the measurement was
# Git for Windows: the failure is in the HTTP/2 conversation and the container's
# curl speaks it as readily. One flag of insurance on a step that costs 2.4 GB
# to retry.
#
# **`http.postBuffer=524288000` is deliberately NOT here**, though it was in the
# measured fix. The two were changed together, so nothing separates which one
# worked, and a half-gigabyte buffer is a widely-copied setting with a real cost
# and no mechanism connecting it to this failure. If HTTP/1.1 alone proves
# insufficient at a gate, that is the moment to add it — with that evidence.
_HTTP_VERSION_ARGS = ["-c", "http.version=HTTP/1.1"]
_HTTP_VERSION_CONFIG = ["--config", "http.version=HTTP/1.1"]


# Pinned by digest, not by tag. This image is handed a writable bind mount of
# the destination directory, so "whatever :latest resolves to today" is a
# third party with write access to a user's install. The tag is kept alongside
# for readability; the digest is what docker actually resolves.
# Resolved 2026-08-22 by pulling alpine/git:2.49.1 (git version 2.49.1) and
# reading back its RepoDigest; re-resolve the same way when bumping.
CONTAINER_GIT_IMAGE = (
    "alpine/git@sha256:c0280cf9572316299b08544065d3bf35db65043d5e3963982ec50647d2746e26"
)
"""Public because preflight's bind-mount probe has to run THIS reference.

A tag and a digest are two different image references to Docker. A probe that
asked for `alpine/git` pulled a second, unpinned image and bind-mounted the
user's chosen directory into whatever `:latest` resolved to that day, while the
clone stage that followed pulled the digest below (review, 2026-08-23).
Exporting the pinned value is what makes preflight's "the probe costs one pull
that was going to happen anyway" true rather than merely written.
"""

_CONTAINER_GIT_IMAGE = CONTAINER_GIT_IMAGE

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


# `remote_url()` is deliberately NOT on that Protocol. `apply.py` — the only
# other user of this seam — never asks the question, and widening a Protocol
# breaks every fake that implements it for a capability the fake's caller does
# not use. The install engine asks for the concrete implementation's method
# through its own seam instead (roadmap 6.2).


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


def _no_prompt_env() -> dict[str, str]:
    """The environment git runs in: never interactive, whatever the host thinks.

    A repository that answers 401 — renamed, deleted, or made private — makes
    git ask for a username. On Windows that request goes to Git Credential
    Manager, which opens a *graphical* dialog; from a launcher with no console
    that is an invisible modal and an install that hangs forever with no output.
    `GIT_TERMINAL_PROMPT=0` and an empty `GIT_ASKPASS`/`SSH_ASKPASS` turn it into
    an immediate, readable failure instead.
    """
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = ""
    env["SSH_ASKPASS"] = ""
    env["GCM_INTERACTIVE"] = "never"
    return env


def _run_git(argv: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    proc = runner.run(argv, cwd=cwd, env=_no_prompt_env())
    if proc.returncode != 0:
        raise GitError(f"{' '.join(argv)} exited {proc.returncode}: {proc.stderr.strip()}")
    return proc


class RunnerGit:
    """`Git` over the host's `git` CLI, through `yulon.runner`."""

    def remote_url(self, dest: Path) -> str | None:
        """What `origin` points at in the checkout at `dest`, or None if it cannot be read.

        The disk evidence behind the install engine's clone stages: a state
        file claiming the clone is done is a hint, and this is the thing that
        can contradict it. `None` means "not a checkout, or git would not say"
        — never "no remote", because the caller's next move on a `None` is to
        clone, and doing that over somebody else's checkout is what the check
        exists to prevent.
        """
        try:
            proc = _run_git(["git", "remote", "get-url", "origin"], cwd=dest)
        except GitError as exc:
            logger.debug(f"could not read origin in {dest}: {exc}")
            return None
        return proc.stdout.strip() or None

    def is_unmodified(self, dest: Path, relative_path: str) -> bool | None:
        """Is `relative_path` exactly what this checkout's HEAD committed? None = cannot ask."""
        if not (dest / ".git").is_dir():
            return None
        try:
            proc = _run_git(["git", "status", "--porcelain", "--", relative_path], cwd=dest)
        except GitError as exc:
            logger.debug(f"could not ask git about {relative_path} in {dest}: {exc}")
            return None
        return not proc.stdout.strip()

    def clone(self, spec: CloneSpec) -> None:
        if (spec.dest / ".git").is_dir():
            self._update(spec)
            return
        if spec.dest.exists():
            shutil.rmtree(spec.dest)  # a non-git leftover; wow-manage.sh does the same
        spec.dest.parent.mkdir(parents=True, exist_ok=True)
        if spec.sparse_path is None:
            argv = [
                "git",
                *_LINE_ENDING_ARGS,
                *_HTTP_VERSION_ARGS,
                "clone",
                *_LINE_ENDING_CONFIG,
                *_HTTP_VERSION_CONFIG,
                *_depth_args(spec.depth),
            ]
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
        # The transport policy, persisted here for the same reason the two
        # above are: this path builds its repository by hand, so it inherits
        # nothing from `clone --config`. It was missed when the HTTP/1.1 flag
        # landed — this function persisted the line-ending policy and not the
        # transport one, so a sparse clone kept exactly the HTTP/2 failure the
        # flag exists to prevent (adversarial review, 2026-08-24).
        _run_git(["git", "config", "http.version", "HTTP/1.1"], cwd=dest)
        (dest / ".git" / "info").mkdir(parents=True, exist_ok=True)
        (dest / ".git" / "info" / "sparse-checkout").write_text(
            spec.sparse_path.rstrip("/") + "/\n", encoding="utf-8", newline="\n"
        )
        pull = [
            "git",
            *_HTTP_VERSION_ARGS,
            "pull",
            *_pull_depth_args(spec.depth),
            "origin",
            spec.branch or "HEAD",
        ]
        _run_git(pull, cwd=dest)

    def _update(self, spec: CloneSpec) -> None:
        """Fetch and reset an existing clone, without changing its depth.

        Depth is deliberately NOT passed here. `git fetch --depth=1` against a
        full clone *truncates* it in place — measured: a repository with five
        commits becomes shallow with one — and a shallow clone fetched without
        `--unshallow` stays shallow forever. Either way the depth the caller
        asked for on the spec would be silently overridden by whatever the last
        update happened to do, and for AzerothCore that means CMake reading the
        wrong revision into a three-hour build. Leaving depth alone keeps each
        clone the shape it was created with.

        The line-ending flags are repeated because `git -c` did not persist into
        this repository if it was cloned by an older build of this launcher.
        """
        ref = spec.branch or "HEAD"
        _run_git(
            ["git", *_LINE_ENDING_ARGS, *_HTTP_VERSION_ARGS, "fetch", "origin", ref],
            cwd=spec.dest,
        )
        _run_git(["git", *_LINE_ENDING_ARGS, "reset", "--hard", "FETCH_HEAD"], cwd=spec.dest)


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
    maps ownership to the logged-in user, so no `--user` is passed and one must
    not be — it overrides the mapping this relies on.

    That second half read "and `os.getuid` does not exist, which is the same
    condition" until 2026-08-27, and `_user_args()` implemented it that way.
    `os.getuid` does not exist on Windows; it exists on macOS. So every Mac got
    a `--user` the rule excludes, the container saw the bind mount as
    `root:root`, and git could not create `.git`.
    """

    image: str = _CONTAINER_GIT_IMAGE

    def remote_url(self, dest: Path) -> str | None:
        """`git remote get-url origin` in the checkout at `dest`; see `RunnerGit.remote_url()`.

        Containerized like every other git call here, for the same reason: the
        machine this class exists for may have no git at all, and a question
        that needs one would put the second prerequisite straight back.
        """
        if not (dest / ".git").is_dir():
            return None
        try:
            proc = self._capture(dest, ["remote", "get-url", "origin"])
        except GitError as exc:
            logger.debug(f"could not read origin in {dest}: {exc}")
            return None
        return proc.stdout.strip() or None

    def is_unmodified(self, dest: Path, relative_path: str) -> bool | None:
        """Is `relative_path` exactly what this checkout's HEAD committed? None = cannot ask.

        One question, `git status --porcelain -- <path>`, and the three answers
        it distinguishes are the three that matter: no output means the path is
        tracked and matches the index and working tree; `?? path` means it is
        untracked; ` M path` (or any other code) means it was changed. So an
        empty answer — and only an empty answer — proves that replacing the file
        destroys nothing, because `git checkout -- <path>` restores it byte for
        byte.

        `None` when git could not be asked at all, which callers must fail
        closed on: "we could not check" is not "it is safe to overwrite".

        Deliberately NOT on the `Git` Protocol, for the same reason
        `remote_url()` is not — see the comment there.
        """
        if not (dest / ".git").is_dir():
            return None
        try:
            proc = self._capture(dest, ["status", "--porcelain", "--", relative_path])
        except GitError as exc:
            logger.debug(f"could not ask git about {relative_path} in {dest}: {exc}")
            return None
        return not proc.stdout.strip()

    def clone(self, spec: CloneSpec) -> None:
        if (spec.dest / ".git").is_dir():
            try:
                self._run(
                    spec, ["fetch", *_pull_depth_args(spec.depth), "origin", spec.branch or "HEAD"]
                )
                self._run(spec, ["reset", "--hard", "FETCH_HEAD"])
                return
            except GitError as exc:
                if platform.DOCKER_CLI_MISSING_HELP not in str(exc) and git_available():
                    logger.warning(
                        f"containerized git update failed in {spec.dest} ({exc}); "
                        "falling back to host git"
                    )
                    RunnerGit().clone(spec)
                    return
                raise
        if spec.dest.exists():
            shutil.rmtree(spec.dest)
        spec.dest.mkdir(parents=True, exist_ok=True)
        argv = [
            "clone",
            *_LINE_ENDING_CONFIG,
            *_HTTP_VERSION_CONFIG,
            *_depth_args(spec.depth),
        ]
        if spec.branch:
            argv += ["--branch", spec.branch]
        if spec.sparse_path is not None:
            argv += ["--filter=blob:none", "--sparse"]
        # The clone target is `.` because the mount point *is* the destination.
        try:
            self._run(spec, [*argv, spec.url, "."])
        except GitError as exc:
            if platform.DOCKER_CLI_MISSING_HELP not in str(exc) and git_available():
                logger.warning(
                    f"containerized git clone failed in {spec.dest} ({exc}); "
                    "falling back to host git"
                )
                RunnerGit().clone(spec)
                return
            raise
        if spec.sparse_path is not None:
            # --no-cone, or this checks out a DIFFERENT tree than RunnerGit.
            # `clone --sparse` turns cone mode on, and cone mode materializes
            # every file at the repo root and directly inside each parent
            # directory of the requested path. Measured on a repo with
            # ROOT.md, entrypoint.sh, guides/GUIDE.md and guides/x/a.txt,
            # sparse_path="guides/x": RunnerGit yields exactly guides/x/a.txt,
            # cone mode yields all four. Two implementations of one Protocol
            # must not disagree about what they produce.
            self._run(spec, ["sparse-checkout", "set", "--no-cone", spec.sparse_path.rstrip("/")])

    def _run(self, spec: CloneSpec, git_args: list[str]) -> None:
        """One containerized `git` invocation against this spec's destination."""
        self._capture(spec.dest, git_args)

    def _capture(self, dest: Path, git_args: list[str]) -> subprocess.CompletedProcess[str]:
        """One containerized `git` invocation, or `GitError` if it fails.

        argv[0] comes from `platform.docker_program()` for the reason spelled
        out there: this class exists *because* Windows and macOS already have
        Docker Desktop, so it is by definition the git that runs on the machine
        whose PATH does not yet mention docker — the first clone of a first
        install, minutes after `ensure_docker()` put it there.

        Both ways of having no docker end at the same sentence. `None` is "it
        was never found"; the `OSError` is the case the resolution cache cannot
        follow — a hit is remembered for the life of the process, so Docker
        uninstalled or self-updated while the launcher is open leaves that
        pinned path aimed at a file that is gone. Only the first was guarded
        when this moved off the literal `docker`, so the second still reached
        the user as `[WinError 2] The system cannot find the file specified`
        (review, 2026-08-23) — the exact failure the change was made to end.

        `_LINE_ENDING_ARGS` and `_HTTP_VERSION_ARGS` are applied to EVERY
        invocation this method makes, including the two that touch no network
        and no working tree — `remote get-url origin` and `is_unmodified()`'s
        `status --porcelain`. That is deliberate: one argv shape means there is
        no second spelling for a future command to be added to and forget, and
        the HTTP pin is simply inert without a network call.

        The line-ending half is not inert, and a review seat was right to say
        so. Forcing `core.autocrlf=false core.eol=lf` at `status` time is
        correct for a checkout THIS code cloned, because those are the same
        flags it was cloned under. Against a FOREIGN checkout — one the user
        already had, cloned with `autocrlf=true` so its files sit on disk with
        CRLF — the same flags make git compare unconverted bytes and report
        every such file as modified. `is_unmodified()` then answers False and
        `generate-compose` REFUSES, which is the safe direction (too strict,
        never overwriting), and it is unreachable today because every checkout
        the app asks about is one it made. It stops being unreachable the day
        an existing install can be attached, and that is the day to give the
        local calls their own argv.
        """
        program = platform.docker_program()
        if program is None:
            raise GitError(platform.DOCKER_CLI_MISSING_HELP)
        argv = [
            program,
            "run",
            "--rm",
            "-v",
            f"{dest}:/git",
            # State the working directory rather than inheriting the image's.
            # `image` is a public field, so an override would otherwise clone
            # into the wrong place — silently, since `.` would resolve
            # somewhere inside the container instead of the bind mount.
            "-w",
            "/git",
            *self._user_args(),
            self.image,
            *_LINE_ENDING_ARGS,
            *_HTTP_VERSION_ARGS,
            *git_args,
        ]
        # At INFO, and the mount is the point. A Mac tester's clone failed with
        # `/git/.git: No such file or directory` (2026-08-26) and the one fact
        # needed to diagnose it — which host directory was mounted at `/git` —
        # was in neither the error nor the log: `git_args` name the destination
        # `.`, and `runner.run()` logs the argv at DEBUG while the app runs at
        # INFO. Three rounds of asking over Discord went into recovering a
        # string this process already held. Same shape as
        # `docker.build_staged()`, and safe to print: these URLs come from the
        # manifest allow-list and carry no credentials.
        logger.info(f"containerized git: `{' '.join(argv[1:])}` into {dest}")
        try:
            proc = runner.run(argv, env=_no_prompt_env(), stdin=subprocess.DEVNULL)
        except OSError as exc:
            # Logged with the real errno first, the way `docker._docker()` does, so a
            # docker.exe blocked by an ACL or by AV leaves evidence instead of being
            # reported to the user as "install Docker Desktop" with nothing in the log
            # to contradict it (review finding, 2026-08-23).
            logger.warning(f"{argv[0]} could not be started: {exc}")
            raise GitError(platform.DOCKER_CLI_MISSING_HELP) from exc
        if proc.returncode != 0:
            raise GitError(
                # The exit code, which `_run_git()` has always reported and this
                # path never did. The Mac clone (2026-08-27) died in under a
                # second with git's stderr ending at `Cloning into '.'...` and
                # nothing after it — and a process that was killed looks exactly
                # like one that failed when the only evidence is the words it
                # got out first. 137 and 128 are different investigations.
                f"containerized git {' '.join(git_args)} in {dest} exited "
                f"{proc.returncode}: {proc.stderr.strip()}"
            )
        return proc

    @staticmethod
    def _user_args() -> list[str]:
        """`--user <uid>:<gid>` on Linux only. Docker Desktop must not get one.

        The rule is the class docstring's, and this is the line that did not
        obey it. `hasattr(os, "getuid")` is a test for WINDOWS wearing the name
        of a test for Docker Desktop: macOS has `os.getuid`, so every Mac was
        handed a `--user` — and a `--user` overrides the very file-sharing
        mapping the docstring relies on to make the flag unnecessary there.

        What that cost: the tester's container sees the bind mount as
        `root:root` (2026-08-27), so git running as 501 could not create `.git`
        and every macOS install ended in `/git/.git: No such file or directory`.

        Asking `platform.detect()` says what is meant. The old spelling was
        right about Windows by accident and wrong about macOS for the same
        reason — one question that happened to answer a different one.
        """
        if platform.detect() != "linux":
            return []
        getuid = getattr(os, "getuid", None)
        getgid = getattr(os, "getgid", None)
        if getuid is None or getgid is None:
            return []
        return ["--user", f"{getuid()}:{getgid()}"]
