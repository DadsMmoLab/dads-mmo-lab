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
from typing import Protocol, runtime_checkable

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


# Docker flags for the two questions `_capture(writes=False)` asks. They exist
# because those questions are asked about a folder this app has NOT decided is
# its own — `remote_url()`'s whole purpose is to find out whose a checkout is —
# and because on an enforcing SELinux box that same container also runs
# `--security-opt label:disable`, which drops it from `container_t` to the
# invoking user's full authority. `label:disable`'s safety argument was written
# for `docker.bind_mount_ok()`'s probe: pinned digest, `:ro` mount, entrypoint
# `ls`. The git read satisfied only the first clause, because the mount string
# was `f"{dest}:/git"` with an EMPTY label — read-write, on a stranger's
# repository (adversarial review, 2026-08-31). These flags are the other two
# clauses, and then some.
#
# Measured against the pinned digest below, on this repo shape (2026-08-31);
# `remote get-url origin` and `status --porcelain -- docker-compose.yml` both
# answer correctly under every line of it, and a modified file still reports
# ` M`:
#
#     -v <repo>:/git      sh -c 'touch /git/PROOF'  -> WROTE (the file appeared)
#     -v <repo>:/git:ro   sh -c 'touch /git/PROOF'  -> touch: Read-only file system
#
# `--read-only` is the container's OWN root filesystem, not the mount, and it is
# here because it was measured rather than assumed: both questions answer with
# it. `--network none` costs nothing — neither question touches a network — and
# it is what makes "the repository chose the program" (see below) unable to
# reach anything. `--cap-drop ALL` and `no-new-privileges` are the two that
# `container_t` was providing for free until `label:disable` turned it off.
_READ_ONLY_CONTAINER_ARGS = [
    "--network",
    "none",
    "--cap-drop",
    "ALL",
    "--security-opt",
    "no-new-privileges",
    "--read-only",
]

# The empty tree. Git resolves this object id in any repository, including one
# with no commits at all (measured: `git init` with nothing committed answers
# both questions with `--attr-source` pointed at it).
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

# Git honours REPOSITORY configuration, and two of its keys name a program git
# then EXECUTES. The repository being asked is by construction one this app did
# not make, and after `label:disable` the container asking is unconfined — so
# "is this file unchanged?" could run repository-selected code with the user's
# authority. Measured inside the pinned image (2026-08-31), on a repo carrying
# `core.fsmonitor=/git/fsm.sh`, `.gitattributes` of `* filter=evil` and
# `filter.evil.clean=/git/clean.sh`, with a plain
# `git status --porcelain -- docker-compose.yml`:
#
#     no flags                      -> fsmonitor RAN, clean filter RAN
#     -c core.fsmonitor=false       -> fsmonitor did not run, clean filter RAN
#     + --attr-source=<empty tree>  -> neither ran
#
# `--attr-source` is the one that reaches the filters: a clean/smudge driver is
# selected by a `filter` ATTRIBUTE, so cutting off the selection is the only
# mechanism there is — git offers no "run no filters" switch, and the driver
# NAMES come from the repository's own config, so there is nothing to enumerate
# and override. It is safe to require here because this argv only ever runs
# inside the pinned digest below (git 2.49.1) and `--attr-source` needs 2.40;
# the host-git `RunnerGit` is deliberately not given it.
#
# `--attr-source` covers the WORKING TREE's `.gitattributes` and NOTHING ELSE.
# Review measured the other two attribute sources on git 2.51.2, with a
# `filter.evil.clean` that logs when it runs:
#
#     .git/info/attributes      + --attr-source  -> the filter RAN
#     core.attributesFile (repo config) + same   -> the filter RAN
#     ... and + -c core.attributesFile=/dev/null -> it did not
#
# so `core.attributesFile` is in the list below, and `$GIT_DIR/info/attributes`
# is NOT closable by any flag. Both files are repository content in exactly the
# sense `core.fsmonitor` is, so this list does not make a hostile repository
# safe — it removes the easy routes and leaves one open.
#
# `--no-optional-locks` stops `status` refreshing `.git/index`, which is both a
# write the `:ro` mount would refuse and the trigger for the `post-index-change`
# hook; `core.hooksPath=/dev/null` is the belt to that braces, since a directory
# of hooks is also repository content. `--ignore-submodules=all` does the same
# for a nested repository — a SECOND set of repository-chosen programs — but it
# is a `status` option rather than a top-level one, so it lives at
# `is_unmodified()`'s call site instead of in this list.
#
# What this does NOT cover, said plainly rather than left to be discovered: git
# has no switch that disables filter DRIVERS themselves, only the attributes
# that select them — and it cannot override `$GIT_DIR/info/attributes` at all,
# so a repository that puts its `filter` attribute THERE still selects a driver
# despite every flag in this list. A future caller that needs real attributes
# gets no protection either. Whatever does run is contained by
# `_READ_ONLY_CONTAINER_ARGS` above and by nothing else, which is why that list
# is not optional.
_UNTRUSTED_REPO_ARGS = [
    "--no-optional-locks",
    f"--attr-source={_EMPTY_TREE}",
    "-c",
    "core.attributesFile=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.hooksPath=/dev/null",
]


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


# `remote_url()` is still deliberately NOT on that Protocol: widening `Git`
# breaks every fake that implements it for a capability the fake's caller does
# not use. `apply.py` DID start asking the question — a clone into
# `modules/<id>` has to know whose repository is already sitting there — so the
# question got its own one-method Protocol below rather than a wider `Git`.


@runtime_checkable
class RemoteReader(Protocol):
    """ "What is this checkout a checkout of?" — the seam the ownership guards ask.

    Both concrete implementations here satisfy it, so a caller handed a real
    `Git` can narrow to it with `isinstance()` and get the SAME transport its
    clones use (host git, or the containerized one on a machine with no git).
    A fake that only clones does not satisfy it, which is the point: the caller
    then falls back to a default it names itself instead of crashing.
    """

    def remote_url(self, dest: Path) -> str | None: ...


@runtime_checkable
class TreeReader(Protocol):
    """ "Is this path exactly what HEAD committed?" — the second read-only question.

    Its own Protocol rather than a method on `RemoteReader`, for the reason that
    one is not on `Git`: a fake satisfies a Protocol by having the methods, so
    adding this to `RemoteReader` would silently stop every existing fake from
    narrowing and send the question to the host CLI instead. Both concrete
    implementations here have both methods, so a real `Git` narrows to both.
    """

    def is_unmodified(self, dest: Path, relative_path: str) -> bool | None: ...


def same_repo(existing: str, wanted: str) -> bool:
    """Do two clone URLs name the same repository?

    Compared loosely on purpose: `https://github.com/x/y.git`,
    `https://github.com/x/y` and `git@github.com:x/y.git` are one repository,
    and refusing an install because git wrote the URL back with a `.git` on it
    would be a refusal about punctuation.

    Lives here rather than in either engine because both of them refuse on the
    answer: `catalog/native.py` for a server source, `apply.py` for a module
    clone (roadmap 2.3).
    """
    return _repo_key(existing) == _repo_key(wanted)


def _repo_key(url: str) -> str:
    text = url.strip().rstrip("/")
    if text.endswith(".git"):
        text = text[: -len(".git")]
    for prefix in ("https://", "http://", "ssh://", "git@"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    return text.replace(":", "/").lower()


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


def _is_fresh_mount_race(message: str) -> bool:
    """True only for the exact "clone started, then ENOENT under /git" shape.

    Both substrings, not either alone: "No such file or directory" on its own
    also fires on a genuinely missing parent path, and "Cloning into" alone
    fires on ordinary successful output baked into a different error. Together
    they are git's own words for "I started, and the bind mount was not there
    yet" - see `ContainerGit._clone_with_mount_race_retry()`.
    """
    return "Cloning into" in message and "No such file or directory" in message


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
    a `--user` the rule excludes, and the container saw the bind mount as
    `root:root`.

    **That was a real defect and it was not the macOS clone failure**, though
    it was recorded here as its cause. Measured 2026-08-29 against this exact
    pinned image: a root-owned mount plus `--user <uid>:<gid>` makes git print
    `/git/.git: Permission denied`. The tester reported
    `/git/.git: No such file or directory` — EACCES against ENOENT, which is
    not the same failure and not the same investigation. ENOENT there means
    the container's `/git` had no directory behind it at `mkdir` time, and
    what a Mac's file-sharing layer does to a bind mount is the one thing
    nobody on this project can run. The macOS failure is still open.
    """

    image: str = _CONTAINER_GIT_IMAGE

    # The SELinux seams, in the shape `docker.bind_mount_ok()` already uses:
    # the real functions by default, overridable so a test can state the
    # machine's answer instead of inheriting the host the suite runs on. Both,
    # because `platform.bind_label()` needs both — see `_capture()`.
    selinux_enforcing: Callable[[], bool | None] = platform.selinux_enforcing
    filesystem_type: Callable[[Path], str | None] = platform.filesystem_type

    def remote_url(self, dest: Path) -> str | None:
        """`git remote get-url origin` in the checkout at `dest`; see `RunnerGit.remote_url()`.

        Containerized like every other git call here, for the same reason: the
        machine this class exists for may have no git at all, and a question
        that needs one would put the second prerequisite straight back.
        """
        if not (dest / ".git").is_dir():
            return None
        try:
            proc = self._capture(dest, ["remote", "get-url", "origin"], writes=False)
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

        `--ignore-submodules=all` is a `status` option rather than one of
        `_UNTRUSTED_REPO_ARGS`, so it lives here: without it `status` descends
        into a nested repository, and a nested repository is a SECOND set of
        repository-chosen programs. It cannot change this method's answer for a
        `relative_path` that is an ordinary file.

        Deliberately NOT on the `Git` Protocol, for the same reason
        `remote_url()` is not — see the comment there.
        """
        if not (dest / ".git").is_dir():
            return None
        try:
            proc = self._capture(
                dest,
                ["status", "--ignore-submodules=all", "--porcelain", "--", relative_path],
                writes=False,
            )
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
            self._clone_with_mount_race_retry(spec, [*argv, spec.url, "."])
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
        self._capture(spec.dest, git_args, writes=True)

    def _clone_with_mount_race_retry(self, spec: CloneSpec, git_args: list[str]) -> None:
        """The initial clone, retried once against the exact bind-mount race in the class docstring.

        Reproduced live on macOS/Docker Desktop 2026-08-29: git starts (`Cloning
        into '.'...`) against a directory that was `mkdir`'d immediately before
        the `docker run`, then fails `/git/.git: No such file or directory` —
        the container's view of a brand-new bind mount had not caught up with
        the host's. Twelve immediate repeats of the identical command, on the
        identical machine, all succeeded; the failure did not recur once. That
        is the signature of a mount-propagation race, not a real clone failure,
        so one immediate retry is tried before falling back to host git — a
        fallback this class exists specifically to let a Mac without Xcode's
        Command Line Tools avoid needing.

        Deliberately narrow: only the exact "started, then ENOENT under /git"
        shape retries. Anything else (auth, network, a bad branch) raises on
        the first attempt exactly as before.
        """
        try:
            self._capture(spec.dest, git_args, writes=True)
        except GitError as exc:
            if not _is_fresh_mount_race(str(exc)):
                raise
            logger.warning(
                f"containerized git clone hit the fresh-mount race in {spec.dest} ({exc}); "
                "retrying once before falling back to host git"
            )
            self._capture(spec.dest, git_args, writes=True)

    def _capture(
        self, dest: Path, git_args: list[str], *, writes: bool
    ) -> subprocess.CompletedProcess[str]:
        """One containerized `git` invocation, or `GitError` if it fails.

        `writes` says whether this invocation puts anything into `dest`, and it
        is keyword-only and mandatory so a new caller has to answer it rather
        than inherit an answer. It picks between two different containers. A
        writer gets a read-write mount, `:z` where SELinux is enforcing, and
        stays confined. A reader gets a `:ro` mount, `_READ_ONLY_CONTAINER_ARGS`,
        `_UNTRUSTED_REPO_ARGS`, and — where SELinux is enforcing, and only there
        — `--security-opt label:disable`. Never both labels, never neither; see
        the SELinux comment below.

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
        # `:z` on an enforcing SELinux box, and the SAME decision the generated
        # compose binds make: `platform.bind_label()` is the one place that
        # answers it, so the clone mount and the `{{BIND_LABEL}}` mounts can
        # never disagree about whether this machine labels. Reusing it also
        # brings the filesystem rule along for free — `bind_label()` consults
        # `selinux_labels_supported()`, so a server folder on exFAT, NTFS or a
        # network share gets no label rather than a mount the daemon refuses —
        # and it keeps the three answers three: `selinux_enforcing()` returns
        # `None` for "could not ask", and only `True` labels.
        #
        # Measured on a clean Fedora 44 box with SELinux Enforcing (2026-08-30),
        # with the preflight probe already fixed so the install could get this
        # far:
        #
        #     $ ls -Zd ~/labtest
        #     unconfined_u:object_r:user_home_t:s0 /home/pk/labtest
        #     $ docker run --rm -v /home/pk/labtest:/git ... -c "touch /git/x"
        #     touch: /git/x: Permission denied
        #     $ docker run --rm -v /home/pk/labtest:/git:z ... -c "touch /git/y"
        #     (succeeded, and the folder is now container_file_t)
        #
        # **`:z` for a write, `--security-opt label:disable` for a read, and the
        # difference is the mount source.** The preflight probe
        # deliberately mounts an ANCESTOR of the chosen folder — routinely the
        # user's whole home directory — so a `:z` there would recursively
        # relabel `$HOME` to `container_file_t` and break the desktop session.
        # This mount is the server directory itself, the folder the app just
        # created and owns; relabelling THAT is exactly what the install wants,
        # it is what `platform.relabel_for_containers()` does to it a few stages
        # later anyway, and every compose bind the engine generates for it
        # already carries the same `:z`.
        #
        # **Only for the calls that WRITE into `dest`.** `_capture()` is shared
        # with `remote_url()` and `is_unmodified()`, which ask a question and
        # change nothing — and a `:z` relabels the mount source recursively, so
        # asking would have rewritten the labels of the very folder the answer
        # is used to decide NOT to touch. That is not hypothetical: the first
        # press against a user's OWN checkout of the same repository is refused
        # by `native.refuse_unowned_checkout()`, and the evidence it refuses on
        # is `_remote_of()` -> `_git_remote_url()` -> `remote_url()` -> here.
        # The refusal's own words are "nothing was touched", and a relabel of
        # the user's git checkout would have made them false.
        #
        # **A read must still be able to SEE the folder, and unlabelled it
        # cannot.** Dropping the `:z` from the two questions was only half the
        # answer, and the half that was left out was measured the same day on
        # Fedora 44, Enforcing, against a user's own unlabelled checkout
        # (`unconfined_u:object_r:user_home_t:s0`):
        #
        #     $ docker run --rm -v /home/pk/ownco:/git ... remote get-url origin
        #     fatal: not a git repository (or any parent up to mount point /)
        #     $ docker run --rm --security-opt label:disable -v ... get-url origin
        #     https://github.com/mod-playerbots/azerothcore-wotlk.git
        #
        # and the folder's label is byte-identical afterwards, which is the
        # whole point. Note the SHAPE of the denial: the container cannot see
        # `.git` at all, so git does not report a permission error, it reports
        # that the directory is not a repository — so the failure arrives here
        # as an ordinary `GitError` and leaves as `None`, indistinguishable from
        # "there is no checkout here". Every enforcing box therefore answered
        # `None` for every foreign checkout it was asked about. That direction
        # is not free even where a guard catches it: `_clone_core()` refuses on
        # `has_git and existing is None` with "git would not say what it is a
        # checkout of ... Pick an empty folder", which is a true sentence about
        # a machine that could have answered perfectly well, told to a user
        # whose remedy is to delete a checkout.
        #
        # `platform.label_disable_args()` is the same function
        # `docker.bind_mount_ok()`'s probe asks — one decision about running a
        # container unconfined, in one place — and it keeps the three answers
        # three: `None` adds neither the label nor the flag.
        #
        # `filesystem_type()` is asked only when the answer can matter.
        # `bind_label()` is still the one place that decides — `enforcing is
        # True` is not re-implemented here, it is the precondition for the
        # `stat` being worth spawning at all. Off SELinux the label is `""`
        # whatever the filesystem says, so the subprocess was pure waste on
        # every Ubuntu and Arch box, on every containerized git call.
        #
        # **A read mounts `:ro` and runs with everything else it does not need
        # taken away** — `_READ_ONLY_CONTAINER_ARGS` — and asks git in a way that
        # denies the repository the choice of what runs —
        # `_UNTRUSTED_REPO_ARGS`. Both are above, with the measurements. The
        # short version: the mount string here used to be `f"{dest}:/git"` with
        # an empty label on the read path, so `label:disable` was granting an
        # unconfined container a READ-WRITE mount of a folder this app had just
        # decided was not its own, on a justification (`:ro`, entrypoint `ls`,
        # pinned digest) that belonged to `docker.bind_mount_ok()`'s probe.
        label = ""
        hardening: list[str] = []
        untrusted: list[str] = []
        if writes:
            enforcing = self.selinux_enforcing()
            label = platform.bind_label(
                enforcing=enforcing,
                fs_type=self.filesystem_type(dest) if enforcing is True else None,
            )
        else:
            label = ":ro"
            hardening = [
                *platform.label_disable_args(enforcing=self.selinux_enforcing()),
                *_READ_ONLY_CONTAINER_ARGS,
            ]
            untrusted = _UNTRUSTED_REPO_ARGS
        argv = [
            program,
            "run",
            "--rm",
            *hardening,
            "-v",
            f"{dest}:/git{label}",
            # State the working directory rather than inheriting the image's.
            # `image` is a public field, so an override would otherwise clone
            # into the wrong place — silently, since `.` would resolve
            # somewhere inside the container instead of the bind mount.
            "-w",
            "/git",
            *self._user_args(),
            self.image,
            *untrusted,
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
                # One sentence, and the comma-less concatenation of the two
                # spellings that shipped in v0.6.57 is what the extra assertion
                # in `test_a_containerized_failure_is_reported_once` guards:
                # every macOS failure reached the tester printed twice, run
                # together with no separator.
                f"containerized git {' '.join(git_args)} in {dest} exited "
                f"{proc.returncode}: {proc.stderr.strip()}"
            )
        return proc

    @staticmethod
    def _user_args() -> list[str]:
        """`platform.container_user_args()` — the policy lives there, not here.

        The rule is the class docstring's, and the line that used to be here
        did not obey it. `hasattr(os, "getuid")` is a test for WINDOWS wearing
        the name of a test for Docker Desktop: macOS has `os.getuid`, so every
        Mac was handed a `--user` — and a `--user` overrides the very
        file-sharing mapping the docstring relies on to make the flag
        unnecessary there. The tester's container saw the bind mount as
        `root:root` (2026-08-27), so git running as 501 could not create
        `.git`.

        `docker.run_container()` (7.3) needs the same three lines, so they now
        live once, in `platform`, and this asks rather than decides.

        `platform.detect` is handed over explicitly even though it is also the
        parameter's default: a default is bound when `platform` is imported, so
        the seam would be dead — a test replacing `platform.detect` would keep
        getting the real host's answer, and `test_docker_desktop_never_gets_a_user_flag`
        would silently stop exercising macOS on a Windows dev box. That is the
        same shape of blind spot the `hasattr` had.
        """
        return platform.container_user_args(platform_id=platform.detect)
