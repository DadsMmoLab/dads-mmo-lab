"""Tests for the promoted git seam (`yulon.git`).

Most subprocess calls are mocked at the `yulon.runner.run` boundary, because
what is worth asserting about an argv decision is the argv: line endings are
pinned, depth is the caller's choice, and probing for git must never open a GUI.

The exceptions are the two `no_local_commits()` tests marked
`skipif(not git.git_available())`, and they are not decoration. That method's
answer depends on what `git fetch` WRITES, which no mock can establish — the
first version of it was wrong about exactly that, and every test covering the
case it was wrong about was a mock. Both run against local repositories only:
`git init` and a `file://` clone of it, no network and no container.
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
        argv: list[str], cwd: Path | None = None, env: object = None, **kwargs: object
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
    # The SELinux answer is stated rather than inherited from the box the suite
    # runs on: on a Fedora runner the real seam answers "enforcing" and the
    # mount is `…:/git:z`, which the labelling tests below assert on purpose.
    git.ContainerGit(selinux_enforcing=lambda: False).clone(
        git.CloneSpec(url="https://example/core.git", dest=dest, depth=None)
    )
    argv = seen[0]
    assert argv[:4] == ["docker", "run", "--rm", "-v"]
    assert argv[4] == f"{dest}:/git"
    assert argv[5:7] == ["-w", "/git"], "the workdir must be stated, not inherited from the image"
    assert "core.autocrlf=false" in argv, "the CRLF trap applies inside the container too"
    assert argv[-2:] == ["https://example/core.git", "."]
    assert "--depth" not in argv
    assert "@sha256:" in " ".join(argv), "the image must be pinned by digest, not by a moving tag"


def _clone_mount(
    seen: list[list[str]], dest: Path, *, enforcing: bool | None, fs_type: str | None = "ext2/ext3"
) -> str:
    """The `-v` argument of the one containerized clone this states the machine for."""
    git.ContainerGit(
        selinux_enforcing=lambda: enforcing, filesystem_type=lambda _path: fs_type
    ).clone(git.CloneSpec(url="https://example/core.git", dest=dest, depth=None))
    argv = seen[-1]
    return argv[argv.index("-v") + 1]


def test_the_clone_mount_is_labelled_when_selinux_is_enforcing(
    seen: list[list[str]], tmp_path: Path
) -> None:
    """Measured on a clean Fedora 44 box with SELinux Enforcing (2026-08-30).

    The destination is a folder the app has just created under the user's home,
    so it is `user_home_t`, and a confined container may only write
    `container_file_t`:

        $ docker run --rm -v /home/pk/labtest:/git ... -c "touch /git/x"
        touch: /git/x: Permission denied
        $ docker run --rm -v /home/pk/labtest:/git:z ... -c "touch /git/y"
        (succeeded)

    So with the preflight probe fixed, every Fedora install stopped one stage
    later, at `clone-core`.
    """
    dest = tmp_path / "core"
    assert _clone_mount(seen, dest, enforcing=True) == f"{dest}:/git:z"


def test_the_clone_mount_is_not_labelled_where_selinux_is_not_enforcing(
    seen: list[list[str]], tmp_path: Path
) -> None:
    """Relabelling is not free and not universal: no SELinux, no `:z`.

    Same rule the generated compose binds follow, because it is literally the
    same function deciding — `platform.bind_label()`.
    """
    dest = tmp_path / "core"
    assert _clone_mount(seen, dest, enforcing=False) == f"{dest}:/git"


def test_a_selinux_answer_nobody_could_read_does_not_label_the_clone_mount(
    seen: list[list[str]], tmp_path: Path
) -> None:
    """THREE answers, not two. `None` is "could not ask", and it is not a quiet yes.

    A `:z` on a machine that never claimed to be enforcing asks the daemon to
    rewrite the labels of a folder for no evidence, and on an engine that does
    not support the option it fails an install that otherwise works.
    """
    dest = tmp_path / "core"
    assert _clone_mount(seen, dest, enforcing=None) == f"{dest}:/git"


def test_the_clone_mount_is_not_labelled_on_a_filesystem_that_cannot_hold_labels(
    seen: list[list[str]], tmp_path: Path
) -> None:
    """Enforcing is not enough: exFAT/NTFS/CIFS carry no labels and `:z` on them is refused.

    Not re-implemented here — `platform.bind_label()` already consults
    `selinux_labels_supported()`, and reusing that seam is what brings the rule
    along. This asserts that the reuse is real.
    """
    dest = tmp_path / "core"
    assert _clone_mount(seen, dest, enforcing=True, fs_type="ntfs") == f"{dest}:/git"


def test_a_read_only_git_question_does_not_relabel_the_folder_it_asks_about(
    seen: list[list[str]], tmp_path: Path
) -> None:
    """`:z` is a recursive RELABEL of the mount source, so a question must not carry one.

    `_capture()` is shared with `remote_url()` and `is_unmodified()`, which write
    nothing. The cost of labelling them anyway is not untidiness: the first press
    against a user's OWN checkout of the same repository is refused by
    `native.refuse_unowned_checkout()`, and the evidence that refusal rests on is
    `git remote get-url origin` — `_remote_of()` -> `_git_remote_url()` ->
    `ContainerGit.remote_url()` -> here. So on an enforcing box the engine would
    have rewritten the labels of a stranger's git checkout as a side effect of
    deciding not to touch it, under a message that says "nothing was touched".

    Same machine, same two answers, for both halves — so this cannot pass by the
    seams quietly answering "not enforcing".
    """
    fedora = git.ContainerGit(
        selinux_enforcing=lambda: True, filesystem_type=lambda _path: "ext2/ext3"
    )
    dest = tmp_path / "someone-elses-checkout"
    (dest / ".git").mkdir(parents=True)

    fedora.remote_url(dest)
    fedora.is_unmodified(dest, "docker-compose.yml")
    assert len(seen) == 2, "both questions ran; neither was short-circuited away"
    for argv in seen:
        mount = argv[argv.index("-v") + 1]
        assert mount == f"{dest}:/git:ro", "a read must not relabel its subject"
        assert not mount.endswith((":z", ":Z"))

    # The anchor: the SAME machine puts `:z` on the mount that WRITES, so the
    # negative above is the distinction being made and not SELinux being absent.
    writing = tmp_path / "ours"
    fedora.clone(git.CloneSpec(url="https://example/core.git", dest=writing, depth=None))
    assert seen[-1][seen[-1].index("-v") + 1] == f"{writing}:/git:z"


def _labelling_fs(_path: Path) -> str:
    """A filesystem that holds SELinux labels, so `bind_label()` is not the thing under test."""
    return "ext2/ext3"


def _read_argv(seen: list[list[str]], dest: Path, *, enforcing: bool | None) -> list[str]:
    """The argv of one read-only containerized git question, with the machine stated."""
    (dest / ".git").mkdir(parents=True, exist_ok=True)
    git.ContainerGit(
        selinux_enforcing=lambda: enforcing, filesystem_type=lambda _path: "ext2/ext3"
    ).remote_url(dest)
    assert seen, "the question never reached a container"
    return seen[-1]


def test_a_read_only_git_question_mounts_read_only_and_keeps_nothing_it_does_not_need(
    seen: list[list[str]], tmp_path: Path
) -> None:
    """The mount is the grant, and the read path was granting a WRITABLE one.

    `label:disable`'s price was paid on the probe's argument — pinned digest,
    `:ro` mount, `--entrypoint ls` — and the git read met only the first of the
    three: `_capture()` built `f"{dest}:/git"` with an empty label whenever
    `writes=False`. So an unconfined container, holding the invoking user's full
    authority, had a read-write mount of the folder this app had just decided it
    does not own (adversarial review, 2026-08-31).

    Measured against the pinned digest (2026-08-31), same image, same shape as
    production:

        -v <repo>:/git     sh -c 'touch /git/PROOF' -> wrote; PROOF appeared
        -v <repo>:/git:ro  sh -c 'touch /git/PROOF' -> Read-only file system

    and `remote get-url origin` plus `status --porcelain` answer correctly under
    `:ro` and under every other flag asserted here, with a modified file still
    reported as ` M`.

    **What an argv test cannot cover.** That the daemon honours `:ro`,
    `--read-only`, `--cap-drop` and `no-new-privileges` at all; that they behave
    the same under an enforcing policy as they do here; and that a future git
    subcommand added to the read path still answers with the container's own
    root filesystem read-only. The measurements above are the evidence for the
    first two, on a real daemon, and they are not re-run by this suite.
    """
    for dest, ask in (
        (tmp_path / "read-remote", lambda impl, path: impl.remote_url(path)),
        (tmp_path / "read-status", lambda impl, path: impl.is_unmodified(path, "x")),
    ):
        (dest / ".git").mkdir(parents=True)
        ask(git.ContainerGit(selinux_enforcing=lambda: True, filesystem_type=_labelling_fs), dest)
        argv = seen[-1]
        assert argv[argv.index("-v") + 1] == f"{dest}:/git:ro"
        assert argv[argv.index("--network") + 1] == "none"
        assert argv[argv.index("--cap-drop") + 1] == "ALL"
        assert "--read-only" in argv
        assert "no-new-privileges" in argv

    # The anchor: a WRITE gets none of it. A clone that could not write into its
    # own destination, or reach the network it clones from, is not a clone.
    writing = tmp_path / "ours"
    git.ContainerGit(selinux_enforcing=lambda: True, filesystem_type=_labelling_fs).clone(
        git.CloneSpec(url="https://example/core.git", dest=writing, depth=None)
    )
    argv = seen[-1]
    assert argv[argv.index("-v") + 1] == f"{writing}:/git:z"
    assert "--read-only" not in argv
    assert "--network" not in argv
    assert "--cap-drop" not in argv
    assert "no-new-privileges" not in argv


def test_a_read_only_git_question_denies_the_repository_the_choice_of_what_runs(
    seen: list[list[str]], tmp_path: Path
) -> None:
    """`git status` runs programs the REPOSITORY names, and the repository is not ours.

    `core.fsmonitor` names a program git executes; a clean/smudge filter is
    consulted while deciding whether a file is modified. Both come from the
    repository's own config, and `remote_url()`/`is_unmodified()` are asked about
    checkouts this app did not make — inside the container whose SELinux
    confinement this branch turned off.

    Measured inside the pinned image (2026-08-31) on a repository carrying
    `core.fsmonitor=/git/fsm.sh`, `.gitattributes` of `* filter=evil` and
    `filter.evil.clean=/git/clean.sh`, running `status --porcelain -- <path>`:

        no flags                     -> fsmonitor RAN, clean filter RAN
        -c core.fsmonitor=false      -> fsmonitor did not run, clean filter RAN
        + --attr-source=<empty tree> -> neither ran

    `--attr-source` is the only lever that reaches the filters: git has no
    switch that disables filter DRIVERS, and their names come from the
    repository, so cutting off the ATTRIBUTE that selects one is the whole
    mechanism. Stated plainly because it is a limit, not a win.

    **What an argv test cannot cover.** Whether git 2.49.1 really consults no
    driver under `--attr-source`, and whether a later git changes that; both are
    the measurement above, against the digest this argv pins. Nor can it cover
    the mechanisms nobody disabled — see `git._UNTRUSTED_REPO_ARGS`.
    """
    dest = tmp_path / "someone-elses-checkout"
    (dest / ".git").mkdir(parents=True)
    impl = git.ContainerGit(selinux_enforcing=lambda: True, filesystem_type=_labelling_fs)
    impl.remote_url(dest)
    impl.is_unmodified(dest, "docker-compose.yml")
    assert len(seen) == 2
    for argv in seen:
        assert "--no-optional-locks" in argv
        assert f"--attr-source={git._EMPTY_TREE}" in argv
        assert "core.fsmonitor=false" in argv
        assert "core.hooksPath=/dev/null" in argv
        # Before the subcommand, or git rejects them outright.
        subcommand = min(argv.index(word) for word in ("remote", "status") if word in argv)
        for flag in ("--no-optional-locks", "core.fsmonitor=false"):
            assert argv.index(flag) < subcommand
    assert "--ignore-submodules=all" in seen[-1], "a nested repo is a second config"

    # The anchor: a WRITE keeps none of them. `--attr-source` on a clone would
    # blind the checkout to the repository's own `.gitattributes`, which is the
    # repository this app CHOSE and whose line endings it depends on.
    impl.clone(git.CloneSpec(url="https://example/core.git", dest=tmp_path / "ours", depth=None))
    for flag in ("--no-optional-locks", "core.fsmonitor=false", "core.hooksPath=/dev/null"):
        assert flag not in seen[-1]
    assert not [item for item in seen[-1] if item.startswith("--attr-source")]


def test_a_read_only_git_question_runs_unconfined_so_it_can_see_an_unlabelled_folder(
    seen: list[list[str]], tmp_path: Path
) -> None:
    """Dropping `:z` from the reads was half an answer; without the other half they go blind.

    Measured on Fedora 44, Enforcing (2026-08-30), against a checkout the user
    made themselves, so `unconfined_u:object_r:user_home_t:s0`:

        $ docker run --rm -v /home/pk/ownco:/git ... remote get-url origin
        fatal: not a git repository (or any parent up to mount point /)
        $ docker run --rm --security-opt label:disable -v ... remote get-url origin
        https://github.com/mod-playerbots/azerothcore-wotlk.git

    and `ls -Zd` says the label is untouched afterwards, which is the whole
    reason this is the right flag and `:z` is not.

    What the denial looks like is why an argv test alone was not enough to catch
    it: the container cannot see `.git`, so git reports "not a git repository"
    rather than a permission error, `remote_url()` catches the `GitError` and
    answers `None`, and `None` is exactly what a directory holding no checkout
    answers. See the refusal-level test in `test_families_azerothcore.py`.
    """
    argv = _read_argv(seen, tmp_path / "someone-elses-checkout", enforcing=True)
    assert "--security-opt" in argv
    assert argv[argv.index("--security-opt") + 1] == "label:disable"
    # And NOT the other half: `label:disable` lets the container read the folder,
    # `:z` would rewrite it. A read that carried both would still be a read that
    # relabels its subject.
    assert not [item for item in argv if item.endswith((":z", ":Z"))]

    # The anchor, on the SAME machine: a WRITE is the mirror image. `:z` is
    # right there — the folder is this app's own and relabelling it is the point
    # — and confinement is not turned off for it.
    writing = tmp_path / "ours"
    git.ContainerGit(
        selinux_enforcing=lambda: True, filesystem_type=lambda _path: "ext2/ext3"
    ).clone(git.CloneSpec(url="https://example/core.git", dest=writing, depth=None))
    assert seen[-1][seen[-1].index("-v") + 1] == f"{writing}:/git:z"
    assert "label:disable" not in seen[-1]


def test_a_read_only_git_question_stays_confined_where_selinux_is_not_enforcing(
    seen: list[list[str]], tmp_path: Path
) -> None:
    """No SELinux, nothing to disable. Ubuntu, Arch, macOS and Windows read confined."""
    argv = _read_argv(seen, tmp_path / "checkout", enforcing=False)
    assert "label:disable" not in argv
    assert not [item for item in argv if item.endswith((":z", ":Z"))]


def test_a_selinux_answer_nobody_could_read_neither_labels_nor_unconfines_a_read(
    seen: list[list[str]], tmp_path: Path
) -> None:
    """THREE answers, and `None` gets neither half.

    `None` is "could not ask" — no `getenforce`, an unreadable
    `/sys/fs/selinux/enforce`, a tool that said something new. Turning a
    container's confinement off is a security decision, and taking one on no
    evidence is the mistake `platform.selinux_enforcing()`'s docstring exists to
    prevent; a `:z` on the same evidence would relabel a stranger's folder. So
    the question runs exactly as it does on a box with no SELinux at all, and a
    genuine denial reaches the caller as the `None` it already fails closed on.
    """
    argv = _read_argv(seen, tmp_path / "checkout", enforcing=None)
    assert "label:disable" not in argv
    assert not [item for item in argv if item.endswith((":z", ":Z"))]


def test_the_filesystem_is_not_stated_unless_selinux_says_enforcing(
    seen: list[list[str]], tmp_path: Path
) -> None:
    """`platform.filesystem_type()` shells out `stat`, and off SELinux it cannot matter.

    `bind_label()` is `"" ` for `False` and for `None` whatever the filesystem
    answers, so asking was a subprocess per containerized git call on every
    Ubuntu and Arch box — and, because the real one runs through `runner.run`,
    it also put a `stat` argv in front of the docker argv every test that reads
    `seen[0]` was written against.
    """
    asked: list[Path] = []

    def record(path: Path) -> str | None:
        asked.append(path)
        return "ext2/ext3"

    for answer in (False, None):
        quiet = git.ContainerGit(
            selinux_enforcing=lambda said=answer: said,  # type: ignore[misc]
            filesystem_type=record,
        )
        quiet.clone(git.CloneSpec(url="https://example/core.git", dest=tmp_path / f"core-{answer}"))
    assert asked == [], "nothing that cannot change the label is worth a subprocess"

    # And it IS asked when the answer decides something — otherwise a seam that
    # was never called would pass this test by doing nothing at all.
    enforcing_dest = tmp_path / "core-enforcing"
    git.ContainerGit(selinux_enforcing=lambda: True, filesystem_type=record).clone(
        git.CloneSpec(url="https://example/core.git", dest=enforcing_dest)
    )
    assert asked == [enforcing_dest]


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
    assert str(raised.value).count("containerized git") == 1, "no duplicate error prefix"
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
    assert seen_argv[-1][-5:] == [
        "status",
        "--ignore-submodules=all",
        "--porcelain",
        "--",
        "docker-compose.yml",
    ]
    answers.append(_completed(stdout=" M docker-compose.yml\n"))
    assert git.ContainerGit().is_unmodified(dest, "docker-compose.yml") is False
    answers.append(_completed(stdout="?? docker-compose.yml\n"))
    assert git.ContainerGit().is_unmodified(dest, "docker-compose.yml") is False
    # A git that cannot be asked answers None, which callers must fail closed on.
    answers.append(_completed(returncode=128, stderr="not a git repository"))
    assert git.ContainerGit().is_unmodified(dest, "docker-compose.yml") is None
    assert git.ContainerGit().is_unmodified(tmp_path / "not-a-checkout", "x") is None


# -- what HEAD carries ------------------------------------------------------
#
# A separate question from the one above, and the reason it exists: `status`
# answers about the working tree and the index, so it is silent about commits.


@pytest.mark.parametrize(
    "impl", [git.RunnerGit(), git.ContainerGit()], ids=["host", "containerized"]
)
def test_no_local_commits_counts_what_head_has_that_the_update_would_not(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, impl: git.HistoryReader
) -> None:
    """Zero and only zero means the update's reset discards no history.

    Both implementations fetch the same ref and count against the same target,
    because a caller narrowing to `HistoryReader` never learns which one it got
    — and this is a guard's input, so a disagreement between them would be a
    guard that means different things on Windows than on Linux.

    The target is `FETCH_HEAD`, not `refs/remotes/origin/...`: a branchless
    `fetch origin HEAD` refreshes no remote-tracking ref at all, which is what
    `test_this_apps_own_update_does_not_make_a_branchless_clone_look_like_the_users`
    proves against real git. That is a fact about git and no mock can establish
    it; what this test holds is that both back-ends spell the question the same.
    """
    dest = tmp_path / "core"
    (dest / ".git").mkdir(parents=True)
    answers: list[subprocess.CompletedProcess[str]] = []
    seen_argv: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        seen_argv.append(argv)
        return answers.pop(0)

    monkeypatch.setattr(runner, "run", fake_run)
    answers += [_completed(), _completed(stdout="0\n")]
    assert impl.no_local_commits(dest, "wotlk") is True
    # The manifest's branch is fetched — the same ref `_update()` names — and
    # the count is taken against what that fetch actually landed.
    assert seen_argv[-2][-3:] == ["fetch", "origin", "wotlk"]
    assert seen_argv[-1][-3:] == ["rev-list", "--count", "FETCH_HEAD..HEAD"]
    # No depth on that fetch: `--depth=1` truncates a full clone in place, and
    # the shape a clone was made with is not this check's to change.
    assert not [arg for arg in seen_argv[-2] if arg.startswith("--depth")]

    answers += [_completed(), _completed(stdout="3\n")]
    assert impl.no_local_commits(dest, "wotlk") is False

    # No branch on the manifest — every module in the wow-wotlk catalog — is the
    # literal `HEAD`, exactly as both update paths spell it.
    answers += [_completed(), _completed(stdout="0\n")]
    assert impl.no_local_commits(dest, None) is True
    assert seen_argv[-2][-3:] == ["fetch", "origin", "HEAD"]
    assert seen_argv[-1][-3:] == ["rev-list", "--count", "FETCH_HEAD..HEAD"]

    # A fetch that cannot reach the remote — an offline machine, a repository
    # that has gone private — is None, not True, and asks nothing further:
    # there is no answer to count against. Every caller fails closed on None.
    answers.append(_completed(returncode=128, stderr="Could not resolve host"))
    before = len(seen_argv)
    assert impl.no_local_commits(dest, None) is None
    assert len(seen_argv) == before + 1, "a failed fetch must not be followed by a count"
    # And so is a git that would not answer the count.
    answers += [_completed(), _completed(returncode=128, stderr="broken")]
    assert impl.no_local_commits(dest, "wotlk") is None
    assert impl.no_local_commits(tmp_path / "not-a-checkout", "wotlk") is None


def test_the_containerized_history_question_fetches_in_a_container_that_has_a_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`no_local_commits()` fetches, and a reader container cannot reach a remote.

    `_capture(writes=False)` adds `_READ_ONLY_CONTAINER_ARGS`, which begins
    `--network none` — so a fetch asked as a "read" fails on every machine, on
    every module, with a docker flag as its cause and nothing in the message
    saying so. It must go through the write container, the same one `_update()`
    fetches with. The mount it brings is safe only because `_may_adopt()` has
    already established that this app created the server directory.

    The count that follows needs no network and stays a read, so the hardened
    container is not given up for the whole question.
    """
    dest = tmp_path / "core"
    (dest / ".git").mkdir(parents=True)
    answers = [_completed(), _completed(stdout="0\n")]
    seen_argv: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        seen_argv.append(argv)
        return answers.pop(0)

    monkeypatch.setattr(runner, "run", fake_run)
    impl = git.ContainerGit(selinux_enforcing=lambda: False, filesystem_type=lambda _p: "ext4")
    assert impl.no_local_commits(dest, None) is True

    fetch, count = seen_argv
    assert fetch[-3:] == ["fetch", "origin", "HEAD"]
    assert "none" not in fetch, "the fetch container must be able to reach the remote"
    assert f"{dest}:/git" in fetch, "and must mount the clone read-write"
    assert count[-3:] == ["rev-list", "--count", "FETCH_HEAD..HEAD"]
    assert count[count.index("--network") + 1] == "none"
    assert f"{dest}:/git:ro" in count


@pytest.mark.skipif(not git.git_available(), reason="needs a host git to make a real checkout")
def test_a_committed_change_is_invisible_to_status_and_visible_to_the_count(
    tmp_path: Path,
) -> None:
    """The measurement the fourth adoption fact rests on, against real git.

    Every other test in this file mocks `runner.run`, and that is right for
    argv decisions. This one is not an argv decision: it is the claim that
    `git status --porcelain` and `rev-list <ref>..HEAD` answer DIFFERENTLY about
    the same checkout, and a mock proves nothing about that. If they ever agreed
    there would be no reason for `no_local_commits()` to exist, and the guard
    that calls it could be "simplified" back into the bug it was written for.

    Local repositories only — `git init` and a clone over a filesystem path. No
    network, no container, nothing cloned from anywhere.
    """
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    author = ["-c", "user.email=t@example", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", "-b", "main", "."], cwd=upstream, check=True)
    (upstream / "a.txt").write_text("upstream\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=upstream, check=True)
    subprocess.run([*["git", *author], "commit", "-qm", "one"], cwd=upstream, check=True)

    dest = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(upstream), str(dest)], check=True)
    impl = git.RunnerGit()
    assert impl.is_unmodified(dest, ".") is True
    assert impl.no_local_commits(dest, "main") is True

    (dest / "mine.txt").write_text("three evenings\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=dest, check=True)
    subprocess.run([*["git", *author], "commit", "-qm", "mine"], cwd=dest, check=True)

    # The whole point, in two lines: the tree is spotless and the history is not.
    assert impl.is_unmodified(dest, ".") is True
    assert impl.no_local_commits(dest, "main") is False


@pytest.mark.skipif(not git.git_available(), reason="needs a host git to make a real checkout")
def test_this_apps_own_update_does_not_make_a_branchless_clone_look_like_the_users(
    tmp_path: Path,
) -> None:
    """One legitimate update must not turn fact 4 into a refusal.

    Every OTHER `branch is None` test here mocks `runner.run`, and a mock cannot
    settle this: the question is what `git fetch` WRITES. `fetch origin
    <named-branch>` moves `refs/remotes/origin/<branch>`; `fetch origin HEAD` —
    the literal command both update paths run when the manifest names no branch,
    which is all 21 modules in the wow-wotlk catalog — moves only `FETCH_HEAD`.
    `refs/remotes/origin/HEAD` is written once, at clone time, and never again.

    So a checkout that has taken one update is one commit "ahead" of that ref
    while carrying nothing of the user's, and the adoption this whole guard
    exists to permit — for a module installed by a build older than the claim
    file, which by definition has had time to be updated — is refused with
    "throws away anything you have changed there" told to somebody who changed
    nothing.

    Driven through the app's own `RunnerGit` and `CloneSpec` rather than raw
    git commands, because the defect lives in the agreement between two of this
    module's own methods. `depth` is left at its default 1: every real module
    clone is shallow, and `apply.py` never overrides it.

    Local repositories only — `git init` and a `file://` clone of it. No
    network, no container, nothing cloned from anywhere.
    """
    author = ["-c", "user.email=t@example", "-c", "user.name=t"]
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", "."], cwd=upstream, check=True)

    def upstream_commit(name: str) -> None:
        (upstream / name).write_text(f"{name}\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=upstream, check=True)
        subprocess.run([*["git", *author], "commit", "-qm", name], cwd=upstream, check=True)

    upstream_commit("a.txt")
    # `file://`, not a bare path: git clones a local path by hardlinking and
    # ignores --depth, so a plain path would quietly test a FULL clone.
    spec = git.CloneSpec(url=upstream.as_uri(), dest=tmp_path / "mod-example")
    assert spec.branch is None and spec.depth == 1
    impl = git.RunnerGit()
    impl.clone(spec)
    assert impl.no_local_commits(spec.dest, None) is True

    upstream_commit("b.txt")
    impl.clone(spec)  # the existing clone, so `_update()`: fetch + reset --hard FETCH_HEAD
    assert impl.is_unmodified(spec.dest, ".") is True
    assert (
        impl.no_local_commits(spec.dest, None) is True
    ), "one app-driven update must not read as the user's own commits"

    # And the fact still does its job: a real local commit is still refused.
    (spec.dest / "mine.txt").write_text("three evenings\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=spec.dest, check=True)
    subprocess.run([*["git", *author], "commit", "-qm", "mine"], cwd=spec.dest, check=True)
    assert impl.is_unmodified(spec.dest, ".") is True
    assert impl.no_local_commits(spec.dest, None) is False

    # Still shallow: neither the update nor the check may deepen the clone.
    assert (spec.dest / ".git" / "shallow").is_file()


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
        lambda argv, cwd=None, env=None, **kwargs: _completed(
            returncode=1, stderr="could not resolve"
        ),
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
    # `seen[0]` has to BE the docker call, so the filesystem seam is stated:
    # the real one shells out `stat` through this very fixture on Linux, and on
    # an enforcing runner it would be recorded first. See `_capture()`.
    git.ContainerGit(filesystem_type=lambda _path: None).clone(
        git.CloneSpec(url="https://example/core.git", dest=tmp_path / "core")
    )
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


def test_container_git_takes_its_user_args_from_platform(
    seen: list[list[str]], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`git.py` no longer decides the uid:gid policy; `platform.container_user_args()` does.

    The stand-in swallows keyword arguments because the call site hands the
    platform seam through explicitly — see `ContainerGit._user_args()` for why
    it has to. What is asserted is the wiring: whatever `platform` answers is
    what lands in the argv, and it lands before the image, where a `docker run`
    flag has to be.
    """

    def four_two(**kwargs: object) -> list[str]:
        return ["--user", "4242:4242"]

    monkeypatch.setattr(git.platform, "container_user_args", four_two)
    # The filesystem seam is stated for the reason given in
    # `test_container_git_runs_the_docker_this_host_can_start`: on an enforcing
    # Linux runner the real one would put a `stat` argv into `seen[0]`.
    unlabelled = git.ContainerGit(filesystem_type=lambda _path: None)
    unlabelled.clone(git.CloneSpec(url="https://example/core.git", dest=tmp_path / "core"))
    argv = seen[0]
    assert argv[argv.index("--user") + 1] == "4242:4242"
    assert argv.index("--user") < argv.index(git.CONTAINER_GIT_IMAGE)

    monkeypatch.setattr(git.platform, "container_user_args", lambda **kwargs: [])
    unlabelled.clone(git.CloneSpec(url="https://example/core.git", dest=tmp_path / "core2"))
    assert "--user" not in seen[1]
