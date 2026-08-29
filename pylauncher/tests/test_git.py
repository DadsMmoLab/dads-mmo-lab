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

    def fake_run(
        argv: list[str], cwd: Path | None = None, env: object = None
    ) -> subprocess.CompletedProcess[str]:
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
    # The wrapper form covers this invocation ...
    assert argv[:5] == ["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf"]
    # ... and `clone --config` is what WRITES it into the new repository, which
    # is the half that survives to the next fetch. Measured: after a clone with
    # only `git -c`, the new .git/config carries no core.* keys at all.
    assert "--config" in argv
    assert argv[argv.index("--config") + 1] == "core.autocrlf=false"
    assert "core.eol=lf" in argv
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
        # `fetch` talks to the network, so it carries the HTTP/1.1 insurance;
        # `reset` is local and does not.
        [
            "git",
            "-c",
            "core.autocrlf=false",
            "-c",
            "core.eol=lf",
            "-c",
            "http.version=HTTP/1.1",
            "fetch",
            "origin",
            "master",
        ],
        ["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", "reset", "--hard", "FETCH_HEAD"],
    ]


def test_update_never_changes_the_depth_of_an_existing_clone(
    seen: list[list[str]], tmp_path: Path
) -> None:
    """`git fetch --depth=1` TRUNCATES a full clone; the update path must not do that.

    Measured: a repository with five commits, fetched once with `--depth=1` and
    reset, becomes shallow with one — history destroyed in place. The reverse is
    just as bad: a shallow clone never becomes full without `--unshallow`, which
    was never issued. Either way the spec's `depth` would be decided by whatever
    the last update happened to do, and for AzerothCore a shallow clone makes
    CMake bake the wrong revision into a three-hour build.
    """
    for depth in (1, None, 50):
        seen.clear()
        dest = tmp_path / f"clone{depth}"
        (dest / ".git").mkdir(parents=True)
        git.RunnerGit().clone(git.CloneSpec(url="https://example/m.git", dest=dest, depth=depth))
        assert not any("--depth" in arg for argv in seen for arg in argv), depth
        assert not any("--unshallow" in arg for argv in seen for arg in argv), depth


# -- failures ---------------------------------------------------------------


def test_a_failed_git_carries_gits_own_last_words(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The error a user sees must be git's, not a generic 'clone failed'."""
    monkeypatch.setattr(
        runner,
        "run",
        lambda argv, cwd=None, env=None: _completed(
            returncode=128, stderr="fatal: repository not found"
        ),
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
    assert argv[5:7] == ["-w", "/git"], "the workdir must be stated, not inherited from the image"
    assert "core.autocrlf=false" in argv, "the CRLF trap applies inside the container too"
    assert argv[-2:] == ["https://example/core.git", "."]
    assert "--depth" not in argv
    assert "@sha256:" in " ".join(argv), "the image must be pinned by digest, not by a moving tag"


def test_docker_desktop_never_gets_a_user_flag(
    monkeypatch: pytest.MonkeyPatch, seen: list[list[str]], tmp_path: Path
) -> None:
    """The class docstring's own rule, applied to the platform it was wrong about.

    It reads: "On Linux the container's root would own every cloned file, so
    the current uid/gid is passed through; on Docker Desktop the file-sharing
    layer already maps ownership to the logged-in user and `os.getuid` does not
    exist, which is the same condition."

    `os.getuid` does not exist on WINDOWS. It exists on macOS, so every Mac got
    `--user <uid>:<gid>` that the design says Docker Desktop must not get — and
    the condition the sentence relies on, the file-sharing layer doing the
    mapping, is exactly what a `--user` overrides. The tester's container sees
    the bind mount as `root:root` (2026-08-27):

        $ docker run --rm --entrypoint ls -v /Users/js/wow3:/git ... -la /git
        drwxr-xr-x    2 root     root            64 ...

    A container running as 501 cannot create `.git` in that, and failing to
    create `.git` is how every macOS install has ended.

    Pinned per platform rather than on `hasattr(os, "getuid")`, because that is
    the test that read "Windows" and answered "not macOS".
    """
    dest = tmp_path / "core"
    spec = git.CloneSpec(url="https://example/core.git", dest=dest, depth=None)
    # Present for every case, so the PLATFORM is the only thing deciding. Without
    # this the test passes on a Windows dev box for the wrong reason — no
    # `os.getuid` there, so no branch is exercised and macOS looks fixed while
    # it is not. That is the same blind spot the code had.
    monkeypatch.setattr(git.os, "getuid", lambda: 501, raising=False)
    monkeypatch.setattr(git.os, "getgid", lambda: 20, raising=False)

    monkeypatch.setattr(git.platform, "detect", lambda: "macos")
    git.ContainerGit().clone(spec)
    assert "--user" not in seen[-1], "Docker Desktop maps ownership; a --user overrides it"

    monkeypatch.setattr(git.platform, "detect", lambda: "windows")
    git.ContainerGit().clone(spec)
    assert "--user" not in seen[-1]

    monkeypatch.setattr(git.platform, "detect", lambda: "linux")
    git.ContainerGit().clone(spec)
    argv = seen[-1]
    assert argv[argv.index("--user") + 1] == "501:20", "Linux still needs it, or root owns all"


def test_a_failed_clone_names_the_directory_it_mounted(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """A failure nobody can reconstruct the command for costs a day of round trips.

    A Mac tester (2026-08-26) reported

        containerized git clone --config core.autocrlf=false … . failed:
        Cloning into '.'...
        /git/.git: No such file or directory

    and the one fact needed to diagnose it — WHICH host directory was mounted
    at `/git` — was in neither the message nor the log. `git_args` alone name
    `.`, the mount is the only place the destination appears, and
    `runner.run()` logs the argv at DEBUG while the app runs at INFO. Three
    rounds of asking over Discord went into recovering a string the process
    already had.
    """
    dest = tmp_path / "core"
    dest.mkdir()

    def fail(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(returncode=128, stderr="/git/.git: No such file or directory")

    monkeypatch.setattr(runner, "run", fail)
    caplog.set_level("INFO")
    with pytest.raises(git.GitError) as raised:
        git.ContainerGit().clone(
            git.CloneSpec(url="https://example/core.git", dest=dest, depth=None)
        )
    assert str(dest) in str(raised.value), "the message must say where it was cloning to"
    assert "/git/.git: No such file or directory" in str(raised.value)
    # And the exit code, which `RunnerGit` has always reported and this path
    # never did. The Mac clone died in under a second with git's stderr cut off
    # after `Cloning into '.'...` and nothing after it — a killed process and a
    # failed one look identical without the number, and 137 means something
    # very different from 128 here.
    assert "128" in str(raised.value), "a failure with no exit code cannot be told apart"
    logged = "\n".join(r.message for r in caplog.records)
    assert f"{dest}:/git" in logged, "the mount belongs in the log, at the level the app runs at"


def test_a_containerized_failure_is_reported_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One failure, one sentence. The Mac report (2026-08-29) carried two.

    Adding the exit code (#117) left the sentence it replaced concatenated to
    it — three adjacent f-strings with no comma between them — so every
    containerized git failure reached the user as its own message printed
    twice, run together with no separator:

        ... exited 1: Cloning into '.'...
        /git/.git: No such file or directorycontainerized git clone ... failed:
        Cloning into '.'...
        /git/.git: No such file or directory

    The substring assertions above all pass against that, which is why it
    shipped. Counting is what catches it.
    """
    dest = tmp_path / "core"
    dest.mkdir()

    def fail(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(returncode=1, stderr="/git/.git: No such file or directory")

    monkeypatch.setattr(runner, "run", fail)
    with pytest.raises(git.GitError) as raised:
        git.ContainerGit().clone(
            git.CloneSpec(url="https://example/core.git", dest=dest, depth=None)
        )
    message = str(raised.value)
    assert message.count("containerized git") == 1, f"the failure is reported twice: {message}"
    assert message.count("/git/.git: No such file or directory") == 1


def test_is_unmodified_tells_upstreams_own_file_from_one_somebody_edited(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One question with three answers, and the install engine treats each differently.

    `git status --porcelain -- <path>` prints nothing for a tracked file that
    matches HEAD, `?? path` for an untracked one and ` M path` for a changed
    one — so an empty answer, and only an empty answer, proves `git checkout`
    can put the file back. That is what lets `generate-compose` replace the
    `docker-compose.yml` the clone brought with it without ever touching one a
    user wrote.
    """
    dest = tmp_path / "core"
    (dest / ".git").mkdir(parents=True)
    answers: list[subprocess.CompletedProcess[str]] = []
    seen_argv: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        seen_argv.append(argv)
        return answers.pop(0)

    monkeypatch.setattr(runner, "run", fake_run)
    answers.append(_completed(stdout=""))
    assert git.ContainerGit().is_unmodified(dest, "docker-compose.yml") is True
    assert seen_argv[-1][-4:] == ["status", "--porcelain", "--", "docker-compose.yml"]
    answers.append(_completed(stdout=" M docker-compose.yml\n"))
    assert git.ContainerGit().is_unmodified(dest, "docker-compose.yml") is False
    answers.append(_completed(stdout="?? docker-compose.yml\n"))
    assert git.ContainerGit().is_unmodified(dest, "docker-compose.yml") is False
    # A git that cannot be asked answers None, which callers must fail closed on.
    answers.append(_completed(returncode=128, stderr="not a git repository"))
    assert git.ContainerGit().is_unmodified(dest, "docker-compose.yml") is None
    assert git.ContainerGit().is_unmodified(tmp_path / "not-a-checkout", "x") is None


def test_both_git_implementations_check_out_the_same_sparse_tree(
    seen: list[list[str]], tmp_path: Path
) -> None:
    """One Protocol, two implementations — they must not disagree about the result.

    `git clone --sparse` turns cone mode ON, and cone mode materializes every
    file at the repo root and in each parent directory of the requested path.
    Measured on a repo with ROOT.md, entrypoint.sh, guides/GUIDE.md and
    guides/x/a.txt with sparse_path="guides/x": RunnerGit yields exactly
    guides/x/a.txt, cone mode yields all four. Downstream `clone.glob(...)` in
    apply.py would then match different files depending on which back-end ran —
    a bug that reproduces on one OS only.
    """
    spec = git.CloneSpec(url="https://example/r.git", dest=tmp_path / "keg", sparse_path="guides/x")
    git.ContainerGit().clone(spec)
    sparse = [argv for argv in seen if "sparse-checkout" in argv]
    assert sparse, "expected a sparse-checkout call"
    assert "--no-cone" in sparse[0]


def test_git_is_never_left_waiting_on_an_invisible_password_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A private or renamed repo answers 401, and git then asks for a username.

    On Windows that request reaches Git Credential Manager, which opens a
    graphical dialog — from a launcher with no console that is an invisible
    modal and an install that hangs forever with no output.
    """
    envs: list[dict[str, str] | None] = []

    def fake_run(argv: list[str], cwd: Path | None = None, env=None):
        envs.append(env)
        return _completed()

    monkeypatch.setattr(runner, "run", fake_run)
    git.RunnerGit().clone(git.CloneSpec(url="https://example/private.git", dest=tmp_path / "p"))
    assert envs and envs[0] is not None
    assert envs[0]["GIT_TERMINAL_PROMPT"] == "0"
    assert envs[0]["GIT_ASKPASS"] == ""


def test_container_git_reports_a_failure_as_a_git_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        runner,
        "run",
        lambda argv, cwd=None, env=None: _completed(returncode=1, stderr="could not resolve"),
    )
    with pytest.raises(git.GitError, match="could not resolve"):
        git.ContainerGit().clone(
            git.CloneSpec(url="https://example/core.git", dest=tmp_path / "core")
        )


# --------------------------------------------------------- naming the docker CLI
# `ContainerGit` exists precisely because Windows and macOS already have Docker
# Desktop, which makes it the git that runs on the machine whose PATH does not
# yet mention docker: the first clone of a first install, minutes after
# `ensure_docker()` put Docker there. Hardcoding `docker` here made that clone
# the very next thing to fail after provisioning was fixed.

OFF_PATH_EXE = r"C:\Users\pk\AppData\Local\Programs\DockerDesktop\resources\bin\docker.EXE"


def test_container_git_runs_the_docker_this_host_can_start(
    seen: list[list[str]], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(git.platform, "_resolved_docker_cli", OFF_PATH_EXE)
    git.ContainerGit().clone(git.CloneSpec(url="https://example/core.git", dest=tmp_path / "core"))
    assert seen, "nothing ran"
    assert seen[0][0] == OFF_PATH_EXE
    assert seen[0][1:3] == ["run", "--rm"], "only argv[0] moved"


def test_container_git_without_any_docker_explains_itself(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A `GitError` naming Docker, not a `FileNotFoundError` from `subprocess`."""
    monkeypatch.setattr(git.platform, "_resolved_docker_cli", None)
    monkeypatch.setattr(git.platform, "docker_programs", lambda: ("docker",))
    monkeypatch.setattr(git.platform, "_which", lambda name, path=None: None)
    with pytest.raises(git.GitError, match="Docker could not be found"):
        git.ContainerGit().clone(
            git.CloneSpec(url="https://example/core.git", dest=tmp_path / "core")
        )


def test_container_git_says_the_same_thing_when_a_resolved_docker_has_gone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The other way to have no Docker, which only `yulon.docker` guarded.

    `docker_program()` remembers a hit for the life of the process, so a Docker
    Desktop uninstall or self-update while the launcher is open leaves that
    pinned path aimed at a file that is gone. That arrives as `OSError` from
    `subprocess`, not as `None` from the resolver, and it used to come out of
    here as `FileNotFoundError: [Errno 2]` while `docker.start()` on the same
    run said "Docker could not be found on this machine" (review, 2026-08-23).
    """
    monkeypatch.setattr(git.platform, "_resolved_docker_cli", OFF_PATH_EXE)

    def gone(argv: list[str], **kwargs: object):
        raise FileNotFoundError(2, "The system cannot find the file specified", OFF_PATH_EXE)

    monkeypatch.setattr(git.runner, "run", gone)
    with pytest.raises(git.GitError, match="Docker could not be found"):
        git.ContainerGit().clone(
            git.CloneSpec(url="https://example/core.git", dest=tmp_path / "core")
        )


def test_a_large_clone_is_pinned_to_http_1_1_on_the_wire_and_in_the_repo(
    seen: list[list[str]], tmp_path: Path
) -> None:
    """The measured 224k-object failure, and the reason it must persist.

    A clone of AzerothCore over HTTP/2 died on real Windows with
    `unexpected disconnect while reading sideband packet`, and the Rust
    launcher lost a 1.3 GB clone at 9% to the same conversation. The flag has
    to be in BOTH forms for the same reason `core.autocrlf` is: `git -c` covers
    only the invocation it is on, so without `--config` the next `fetch` on the
    update path negotiates HTTP/2 again and the failure returns — on a clone
    that already cost 2.4 GB.
    """
    git.RunnerGit().clone(
        git.CloneSpec(url="https://example/core.git", dest=tmp_path / "core", depth=None)
    )
    argv = seen[0]
    assert "-c" in argv and "http.version=HTTP/1.1" in argv
    assert argv[argv.index("--config") :].count("http.version=HTTP/1.1") == 1
    # The wrapper form comes before the subcommand, the persisted form after.
    assert argv.index("clone") < argv.index("--config")


def test_the_sparse_clone_path_carries_the_http_policy_too(
    seen: list[list[str]], tmp_path: Path
) -> None:
    """Every network git operation gets HTTP/1.1, including the one built by hand.

    `_sparse_clone()` does not run `git clone`; it inits a repository, writes
    its config line by line and pulls. So it inherits nothing from
    `clone --config`, and when the HTTP/1.1 flag landed it persisted the
    line-ending policy and not the transport one — leaving the sparse path with
    exactly the HTTP/2 failure the flag exists to prevent. Found by adversarial
    review, not by this suite, which had only ever checked the two clone paths.
    """
    git.RunnerGit().clone(
        git.CloneSpec(
            url="https://example/guides.git",
            dest=tmp_path / "guides",
            sparse_path="guides/wow-wotlk",
        )
    )
    assert ["git", "config", "http.version", "HTTP/1.1"] in seen
    pull = next(argv for argv in seen if "pull" in argv)
    assert "http.version=HTTP/1.1" in pull
    assert pull.index("-c") < pull.index("pull")
