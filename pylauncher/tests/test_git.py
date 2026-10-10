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

import ast
import dataclasses
import re
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import HANG_BOUND
from tests.support_fake_docker import calls as fake_calls
from tests.support_fake_docker import containers as fake_containers
from tests.support_fake_docker import end_fake_containers, finish_late_create, lay_fake_docker
from tests.support_fake_docker import running as fake_running
from yulon import container_end, git, runner
from yulon.catalog import native
from yulon.ui import lines


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


@pytest.mark.parametrize(
    "impl", [git.RunnerGit(), git.ContainerGit()], ids=["host", "containerized"]
)
def test_update_never_changes_the_depth_of_an_existing_clone(
    seen: list[list[str]], tmp_path: Path, impl: git.RunnerGit | git.ContainerGit
) -> None:
    """`git fetch --depth=1` TRUNCATES a full clone; the update path must not do that.

    Measured: a repository with five commits, fetched once with `--depth=1` and
    reset, becomes shallow with one — history destroyed in place. The reverse is
    just as bad: a shallow clone never becomes full without `--unshallow`, which
    was never issued. Either way the spec's `depth` would be decided by whatever
    the last update happened to do, and for AzerothCore a shallow clone makes
    CMake bake the wrong revision into a three-hour build.

    Both bodies (T149). `ContainerGit` passed the clone's depth on this fetch
    until then, and on a shallow clone that is not harmless either: it puts the
    fetched tip in `.git/shallow` before the reset, so a reset that then fails
    leaves a graft above HEAD that the next update's own guard reads as the
    user's commits (the real-git tests below `_updated_by_host_git()`).
    """
    for depth in (1, None, 50):
        seen.clear()
        dest = tmp_path / f"clone{depth}"
        (dest / ".git").mkdir(parents=True)
        impl.clone(git.CloneSpec(url="https://example/m.git", dest=dest, depth=depth))
        assert any("fetch" in argv for argv in seen), "no update fetch was run at all"
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

        $ docker run --rm -v /home/user/labtest:/git ... -c "touch /git/x"
        touch: /git/x: Permission denied
        $ docker run --rm -v /home/user/labtest:/git:z ... -c "touch /git/y"
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
        # T179: the update route's two diff questions are reads like these.
        (tmp_path / "read-diff", lambda impl, path: impl.changed_files(path, "a", "b", ["sql"])),
        (tmp_path / "read-lines", lambda impl, path: impl.changed_lines(path, "a", "b", "x.sql")),
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

        $ docker run --rm -v /home/user/ownco:/git ... remote get-url origin
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


def test_a_bare_container_git_asks_the_selinux_seams_the_module_holds_at_call_time(
    monkeypatch: pytest.MonkeyPatch, seen: list[list[str]], tmp_path: Path
) -> None:
    """A test that patches `platform.selinux_enforcing` has to be SEEN in here.

    Every test above hands the answer in through `selinux_enforcing=`, so not
    one of them could tell that the dataclass defaults were BOUND AT IMPORT —
    the same shape `docker.bind_mount_ok()` had until 2026-09-04, and the one
    its three production callers meet: `native.py` constructs `ContainerGit()`
    bare in `_git_file_unmodified()`, in the `Seams.clone` `default_factory`,
    and in `_git_remote_url()` — named, not numbered, because the numbers rot:
    an earlier draft of this docstring said "593, 616 and 2364" while the file
    already read 593, 616 and 2370 (`grep -n 'ContainerGit(' native.py`,
    m910q, 2026-09-05). Asked of the interpreter on m910q against the
    unchanged file (2026-09-04):

        {f.name: f.default is getattr(platform, f.name)
         for f in fields(git.ContainerGit) if f.name != "image"}
        -> {'selinux_enforcing': True, 'filesystem_type': True}

    **What is asserted is that the patched seams are CALLED and that their
    answers ARRIVE in the argv** — the write path's `:z` and the read path's
    `label:disable` — not that the fields exist. Counting the calls is what
    makes this fail on every host rather than only a non-enforcing one: with
    the defaults bound at import the REAL host is asked, and a Fedora runner
    would have produced the same argv by luck.
    """
    asked: list[str] = []

    def enforcing() -> bool | None:
        asked.append("selinux")
        return True

    def labelling(path: Path) -> str | None:
        asked.append(f"fs:{path}")
        return "ext2/ext3"

    monkeypatch.setattr(git.platform, "selinux_enforcing", enforcing)
    monkeypatch.setattr(git.platform, "filesystem_type", labelling)
    monkeypatch.setattr(git.platform, "docker_program", lambda: "docker")

    # The production shape: no seam handed in.
    dest = tmp_path / "core"
    git.ContainerGit().clone(git.CloneSpec(url="https://example/core.git", dest=dest, depth=None))
    assert asked == ["selinux", f"fs:{dest}"]
    assert seen[-1][seen[-1].index("-v") + 1] == f"{dest}:/git:z"

    asked.clear()
    (dest / ".git").mkdir(parents=True)
    git.ContainerGit().remote_url(dest)
    assert asked == ["selinux"], "a read consults SELinux and never the filesystem"
    assert "label:disable" in seen[-1]


def test_the_production_container_gits_are_bare_and_there_are_no_others(
    monkeypatch: pytest.MonkeyPatch, seen: list[list[str]], tmp_path: Path
) -> None:
    """The late lookup above is only reached by a `ContainerGit()` that carries no seam.

    The test above proves a BARE `ContainerGit` resolves `platform` at call
    time. That is evidence about production only if production really builds
    them bare, which the class comment used to assert as a dated
    `grep -n 'ContainerGit(' native.py` count — narrative nothing checked, and
    that same comment block had already gone stale once (it named lines "593,
    616 and 2364" while the file read 593, 616 and 2370). This test owns the
    claim instead, in the two halves a written number cannot have:

    * **Driven, not read.** Each of the three routes is called with
      `git.ContainerGit` replaced by a recorder, so what is asserted is that
      the construction really happened on that path and really carried no
      keyword — a route that started passing `selinux_enforcing=` would fail
      here even though the grep count would not move.
    * **Re-derived, so it fails on what it cannot see.** Driving three routes
      can never notice a FOURTH, so the set is also taken from `native.py`'s
      syntax tree. A new `git.ContainerGit(...)` anywhere in that module fails
      this test rather than silently joining an unaudited majority.

    An AST walk and not a text search: `audit by argv, not by string` — the
    same call spelled over two lines, or with a comment inside the parentheses,
    is one `ast.Call` and is two different greps.
    """
    real = git.ContainerGit
    made: list[dict[str, object]] = []

    def record(**kwargs: object) -> git.ContainerGit:
        made.append(kwargs)
        return real(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(git, "ContainerGit", record)
    monkeypatch.setattr(git.platform, "docker_program", lambda: "docker")
    monkeypatch.setattr(git.platform, "selinux_enforcing", lambda: False)
    monkeypatch.setattr(git.platform, "filesystem_type", lambda _p: "ext4")

    dest = tmp_path / "core"
    (dest / ".git").mkdir(parents=True)
    # `.git` exists on purpose: both read methods answer `None` early without
    # it, and an early return would still have constructed the object — so the
    # recorder alone could not tell a route that runs from one that gives up.
    assert native._git_file_unmodified(dest, "docker-compose.yml") is not None
    ran = len(seen)
    native._git_remote_url(dest)
    assert len(seen) == ran + 1, "the remote-url route reached a container, not an early return"
    # T64's six, driven for the same reason as the two above rather than
    # counted: each is a `Seams` default, so each is a production construction
    # site, and the `.git` created above is what stops them returning early.
    for route in (
        native._git_head_sha,
        native._git_head_version,
        native._git_local_edits,
    ):
        ran = len(seen)
        route(dest)  # type: ignore[operator]
        assert len(seen) == ran + 1, f"{route.__name__} reached a container, not an early return"
    ran = len(seen)
    native._git_no_local_commits(dest, "main")
    assert len(seen) == ran + 2, "the local-commits route fetches and then counts"
    ran = len(seen)
    native._git_commits_since(dest, "f82e7d6")
    assert len(seen) == ran + 1, "the commits-since route reached a container"
    ran = len(seen)
    native._git_restore_rev(dest, "f82e7d6")
    assert len(seen) == ran + 1, "the restore route reached a container"
    # T179's two: what changed between two commits, and how one file changed.
    ran = len(seen)
    native._git_changed_files(dest, "a" * 40, "b" * 40, ["centurion/sql"])
    assert len(seen) == ran + 1, "the changed-files route reached a container"
    ran = len(seen)
    native._git_changed_lines(dest, "a" * 40, "b" * 40, "centurion/sql/auth/auth_data.sql")
    assert len(seen) == ran + 1, "the changed-lines route reached a container"
    # T630's: the files the commit a Return moved to tracks.
    ran = len(seen)
    native._git_tree_files(dest, "b" * 40, ["data/sql"])
    assert len(seen) == ran + 1, "the tree-files route reached a container"
    ran = len(seen)
    native._git_file_lines(dest, "b" * 40, ["data/sql/x.sql"])
    assert len(seen) == ran + 1, "the file-lines route reached a container"
    # T632: the migration listing reads bytes, so it goes through `runner.run_bytes`.
    monkeypatch.setattr(
        git.runner,
        "run_bytes",
        lambda argv, **_kw: seen.append(argv) or subprocess.CompletedProcess(argv, 0, b"", b""),
    )
    ran = len(seen)
    native._git_tree_bytes(dest, "a" * 40, "sql/database_updates/world")
    assert len(seen) == ran + 1, "the tree-bytes route reached a container"
    ran = len(seen)
    native._git_is_ancestor(dest, "a" * 40, "b" * 40)
    assert len(seen) == ran + 1, "the is-ancestor route reached a container"
    native.Seams()

    assert made == [{}] * 15, "a production ContainerGit that carries a seam is not bare"

    tree = ast.parse(Path(native.__file__).read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "ContainerGit"
    ]
    # `Seams.in_wsl()` (T125) is the one site that is not bare, by design and
    # with nothing but the distro: a WSL ContainerGit answers SELinux itself
    # (False -- the WSL kernel runs none), so the late lookup this test is about
    # is never reached through it.
    distro_sites = [
        c for c in calls if not c.args and [k.arg for k in c.keywords] == ["wsl_distro"]
    ]
    assert len(distro_sites) == 1, "Seams.in_wsl() should build exactly one distro ContainerGit"
    bare = [c for c in calls if c not in distro_sites]
    assert len(bare) == len(made), "native.py grew a ContainerGit site this test does not drive"
    assert all(not c.args and not c.keywords for c in bare)


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
    assert seen_argv[-1][-6:] == [
        "status",
        "--ignore-submodules=all",
        "--porcelain",
        "--",
        "docker-compose.yml",
        # T66: the app's own clone marker is never a change to the tree. Asked
        # about the marker BY NAME the exclusion is dropped — asserted against
        # real git in `test_is_unmodified_ignores_the_apps_own_marker_and_only_that`.
        ":(exclude,top).yulon-clone.json",
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
    "impl",
    [
        git.RunnerGit(),
        # Both seams pinned. Bare, `ContainerGit()` asks the machine at call time,
        # and on an SELinux-Enforcing box the `stat -f -c %T` filesystem probe
        # goes through `runner.run` -- the one this test fakes -- and eats the
        # first canned answer. Measured 2026-09-05 on `yulon-fedora` (Enforcing,
        # Python 3.13): `IndexError: pop from empty list` at the rev-list call,
        # while m910q (no SELinux) and CI's Ubuntu runners passed. A fixture that
        # answers differently on the second box is what this pins against.
        git.ContainerGit(selinux_enforcing=lambda: False, filesystem_type=lambda _path: "ext4"),
    ],
    ids=["host", "containerized"],
)
def test_no_local_commits_counts_what_head_has_that_the_update_would_not(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, impl: git.HistoryReader
) -> None:
    """An empty list, or nothing but grafts, means the update's reset discards no history.

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
    answers += [_completed(), _completed(stdout="")]
    assert impl.no_local_commits(dest, "wotlk") is True
    # The manifest's branch is fetched — the same ref `_update()` names — and
    # the count is taken against what that fetch actually landed.
    assert seen_argv[-2][-3:] == ["fetch", "origin", "wotlk"]
    assert seen_argv[-1][-2:] == ["rev-list", "FETCH_HEAD..HEAD"]
    # No depth on that fetch: `--depth=1` truncates a full clone in place, and
    # the shape a clone was made with is not this check's to change.
    assert not [arg for arg in seen_argv[-2] if arg.startswith("--depth")]

    # Three shas rather than a count: T66 made the answer the LIST, because
    # in a shallow checkout the commits themselves have to be looked at. No
    # `.git/shallow` here, so none of them is a graft and all three count.
    answers += [_completed(), _completed(stdout="a1\nb2\nc3\n")]
    assert impl.no_local_commits(dest, "wotlk") is False

    # No branch on the manifest — every module in the wow-wotlk catalog — is the
    # literal `HEAD`, exactly as both update paths spell it.
    answers += [_completed(), _completed(stdout="")]
    assert impl.no_local_commits(dest, None) is True
    assert seen_argv[-2][-3:] == ["fetch", "origin", "HEAD"]
    assert seen_argv[-1][-2:] == ["rev-list", "FETCH_HEAD..HEAD"]

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
    fetches with. The mount it brings is safe only because
    `_adoption_refusal()` has already established that this app created the
    server directory.

    The count that follows needs no network and stays a read, so the hardened
    container is not given up for the whole question.
    """
    dest = tmp_path / "core"
    (dest / ".git").mkdir(parents=True)
    answers = [_completed(), _completed(stdout="")]
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
    assert count[-2:] == ["rev-list", "FETCH_HEAD..HEAD"]
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


@pytest.mark.skipif(not git.git_available(), reason="needs a host git to make a real checkout")
def test_a_shallow_pin_behind_the_tip_is_not_a_commit_of_the_users(tmp_path: Path) -> None:
    """T66's second layer, and no mock can settle it: it is a fact about grafts.

    `_pin()` clones at depth 1 and then fetches the pinned revision at depth 1.
    When the pin was ALREADY behind the tip, the result holds two commits that
    are both shallow ROOTS with no edge between them, so `rev-list
    FETCH_HEAD..HEAD` lists the pin — and `no_local_commits()` used to read
    that as work the user had committed and refuse to update.

    Measured here rather than asserted: the same fixture is checked against
    `merge-base --is-ancestor`, which ALSO answers no, so the obvious ancestry
    fix would not have helped.

    The second half is what keeps the fix honest. One commit of the user's own
    on top of that same grafted pin is not a graft, so the answer goes back to
    False and the refusal the guard exists for still fires. Neither half can
    pass because of the other: the only difference between the two checkouts is
    that one commit.

    Local repositories only — `git init` and a `file://` clone of it.
    """
    author = ["-c", "user.email=t@example", "-c", "user.name=t"]
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", "."], cwd=upstream, check=True)

    def upstream_commit(name: str) -> str:
        (upstream / name).write_text(f"{name}\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=upstream, check=True)
        subprocess.run([*["git", *author], "commit", "-qm", name], cwd=upstream, check=True)
        done = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=upstream, check=True, capture_output=True, text=True
        )
        return done.stdout.strip()

    old = upstream_commit("a.txt")
    upstream_commit("b.txt")
    impl = git.RunnerGit()
    dest = tmp_path / "mod-example"
    impl.clone(git.CloneSpec(url=upstream.as_uri(), dest=dest, rev=old))

    roots = (dest / ".git" / "shallow").read_text(encoding="utf-8").split()
    assert sorted(roots) == sorted({old, *roots}) and len(roots) == 2, "not the grafted shape"
    upstream_commit("c.txt")
    subprocess.run(["git", "fetch", "-q", "origin", "HEAD"], cwd=dest, check=True)
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD"], cwd=dest, check=False
    )
    assert ancestor.returncode != 0, "if git ever connects these, this fix can be simpler"

    assert impl.no_local_commits(dest, None) is True

    (dest / "mine.txt").write_text("three evenings\n", encoding="utf-8")
    subprocess.run(["git", "add", "mine.txt"], cwd=dest, check=True)
    subprocess.run([*["git", *author], "commit", "-qm", "mine"], cwd=dest, check=True)

    assert impl.no_local_commits(dest, None) is False


_AUTHOR = ["-c", "user.email=t@example", "-c", "user.name=t"]


def _upstream(tmp_path: Path) -> tuple[Path, Callable[[str], str]]:
    """A real `git init` upstream with one commit, and a function that adds one more."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", "."], cwd=upstream, check=True)

    def commit(name: str) -> str:
        (upstream / name).write_text(f"{name}\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=upstream, check=True)
        subprocess.run(["git", *_AUTHOR, "commit", "-qm", name], cwd=upstream, check=True)
        return _rev(upstream, "HEAD")

    commit("a.txt")
    return upstream, commit


def _rev(repo: Path, rev: str) -> str:
    done = subprocess.run(
        ["git", "rev-parse", rev], cwd=repo, check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


def _updated_by_host_git(tmp_path: Path) -> tuple[git.CloneSpec, Callable[[str], str]]:
    """A depth-1 module clone that host git has updated once: HEAD is NOT a graft (T149).

    Host git's `_update()` fetches without a depth, so the new tip arrives with
    the edge down to the commit the clone was made at, and HEAD walks to it.
    That is the half of the stranded shape an ordinary update makes; the other
    half is a graft ABOVE this HEAD. Asserted rather than assumed, because a
    fixture that started on a grafted HEAD would pass every test below for a
    reason that has nothing to do with them.
    """
    upstream, commit = _upstream(tmp_path)
    spec = git.CloneSpec(url=upstream.as_uri(), dest=tmp_path / "mod-example")
    assert spec.depth == 1
    host = git.RunnerGit()
    host.clone(spec)
    commit("b.txt")
    host.clone(spec)
    head = _rev(spec.dest, "HEAD")
    assert git._shallow_roots(spec.dest), "not a shallow clone"
    assert head not in git._shallow_roots(spec.dest), "not the shape: HEAD is a graft"
    return spec, commit


def _container_git_over_host_git(monkeypatch: pytest.MonkeyPatch) -> git.ContainerGit:
    """`ContainerGit`, with only its transport swapped: each argv it picks runs as host git.

    The container adds a mount and a user and changes nothing about what git
    does with the argv, and the argv is what T149 is about -- which fetch the
    update runs before its reset. Everything above the transport is the real
    `clone()` / `clone_lines()`, fallback included.
    """

    def capture(
        self: git.ContainerGit, dest: Path, git_args: list[str], *, writes: bool
    ) -> subprocess.CompletedProcess[str]:
        return git._run_git(["git", *git_args], cwd=dest)

    def streamed(
        self: git.ContainerGit, dest: Path, git_args: list[str], *, stage: str
    ) -> Iterator[str]:
        yield from git._streamed_git(["git", *git_args], stage=stage, cwd=dest)

    monkeypatch.setattr(git.ContainerGit, "_capture", capture)
    monkeypatch.setattr(git.ContainerGit, "_streamed_capture", streamed)
    return git.ContainerGit()


_UPDATES: dict[str, Callable[[git.ContainerGit, git.CloneSpec], object]] = {
    "clone": lambda impl, spec: impl.clone(spec),
    "clone_lines": lambda impl, spec: list(impl.clone_lines(spec)),
}


@pytest.mark.skipif(not git.git_available(), reason="needs a host git to make a real checkout")
def test_a_graft_above_a_head_that_is_not_one_still_reads_as_the_users(tmp_path: Path) -> None:
    """T149's shape, made by hand: the guard cannot see through it, and fails CLOSED.

    HEAD is not a graft, and a depth-1 fetch has put the new tip in
    `.git/shallow` without moving HEAD. Walking down from that tip stops at
    once, so HEAD and every commit under it down to the old graft are "not on
    the tip", and none of them is a graft for `_only_grafts()` to excuse.
    Nothing local tells that apart from commits of the user's own without
    deepening -- which is why the fix is that no update makes this shape, not a
    guard that guesses. This pins the direction the guard fails in if something
    else ever makes it: a refusal, never a reset over somebody's work.
    """
    spec, commit = _updated_by_host_git(tmp_path)
    commit("c.txt")
    dest = spec.dest
    subprocess.run(["git", "fetch", "-q", "--depth=1", "origin", "HEAD"], cwd=dest, check=True)
    assert _rev(dest, "FETCH_HEAD") in git._shallow_roots(dest), "the depth-1 fetch grafted no tip"

    assert git.RunnerGit().no_local_commits(dest, None) is False


@pytest.mark.skipif(not git.git_available(), reason="needs a host git to make a real checkout")
@pytest.mark.parametrize("update", _UPDATES.values(), ids=_UPDATES.keys())
def test_a_failed_containerised_update_leaves_nothing_the_next_update_refuses(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    update: Callable[[git.ContainerGit, git.CloneSpec], object],
) -> None:
    """The fetch succeeded, the reset did not, and host git is not there to retry (T149).

    Until T149 `ContainerGit`'s update fetched at the clone's depth, which
    puts the fetched tip in `.git/shallow` BEFORE the reset. When the reset
    then failed, on a HEAD host git had once updated, that left the shape
    above, and every later Update refused the module with "carries commits the
    upstream does not" though nobody had committed anything. Host git's
    `_update()` has never passed a depth there, for its own reason; with both
    fetching the same way, a failed reset leaves the graft file exactly as it
    was.

    The reset is failed by a real `index.lock`, which is what a git that
    crashed mid-command leaves behind, and removed once the update has failed,
    as a person would. `git_available` is False so the host-git fallback cannot
    run the reset a second time.

    The last lines are what keeps this honest: one commit of the user's own on
    that same HEAD is still refused.
    """
    spec, commit = _updated_by_host_git(tmp_path)
    dest = spec.dest
    head = _rev(dest, "HEAD")
    grafts = git._shallow_roots(dest)
    tip = commit("c.txt")
    monkeypatch.setattr(git, "git_available", lambda: False)
    impl = _container_git_over_host_git(monkeypatch)
    lock = dest / ".git" / "index.lock"
    lock.touch()
    with pytest.raises(git.GitError, match="index.lock"):
        update(impl, spec)
    lock.unlink()
    assert _rev(dest, "FETCH_HEAD") == tip, "the fetch has to have succeeded for this to be T149"
    assert _rev(dest, "HEAD") == head, "the reset failed, so HEAD cannot have moved"
    assert git._shallow_roots(dest) == grafts, "the failed update left a graft behind"

    assert impl.no_local_commits(dest, None) is True

    (dest / "mine.txt").write_text("three evenings\n", encoding="utf-8")
    subprocess.run(["git", "add", "mine.txt"], cwd=dest, check=True)
    subprocess.run(["git", *_AUTHOR, "commit", "-qm", "mine"], cwd=dest, check=True)
    assert impl.no_local_commits(dest, None) is False


@pytest.mark.skipif(not git.git_available(), reason="needs a host git to make a real checkout")
@pytest.mark.parametrize("update", _UPDATES.values(), ids=_UPDATES.keys())
def test_an_ordinary_containerised_update_still_lands_and_stays_shallow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    update: Callable[[git.ContainerGit, git.CloneSpec], object],
) -> None:
    """T149's fetch without a depth still updates, and does not deepen the clone.

    `_update()`'s rule, now on both bodies: a shallow clone fetched without
    `--depth` stays shallow, so the update keeps the clone the shape it was
    made. The clone's first graft is still there afterwards and HEAD is the
    tip.
    """
    spec, commit = _updated_by_host_git(tmp_path)
    dest = spec.dest
    grafts = git._shallow_roots(dest)
    tip = commit("c.txt")
    impl = _container_git_over_host_git(monkeypatch)

    update(impl, spec)

    assert _rev(dest, "HEAD") == tip
    assert git._shallow_roots(dest) == grafts, "the update changed the clone's shape"
    assert impl.no_local_commits(dest, None) is True


def _checkout_fails_once(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """The next `git checkout` meets a real `index.lock`; every other command runs as it would.

    Only the TIMING is arranged: the lock appears for that one command and is
    gone after it, so the fetches before it and the update after it run
    against a clean `.git`. The failure itself is git's own. Both bodies reach
    git through `_run_git` here -- `RunnerGit` directly, the container through
    `_container_git_over_host_git()` -- so one wrapper times both.
    """
    real = git._run_git
    failed: list[list[str]] = []

    def run(argv: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        if "checkout" in argv and not failed and cwd is not None:
            failed.append(argv)
            lock = cwd / ".git" / "index.lock"
            lock.touch()
            try:
                return real(argv, cwd=cwd)
            finally:
                lock.unlink()
        return real(argv, cwd=cwd)

    monkeypatch.setattr(git, "_run_git", run)
    return failed


def _impl(name: str, monkeypatch: pytest.MonkeyPatch) -> git.RunnerGit | git.ContainerGit:
    return git.RunnerGit() if name == "host" else _container_git_over_host_git(monkeypatch)


@pytest.mark.skipif(not git.git_available(), reason="needs a host git to make a real checkout")
@pytest.mark.parametrize("update", _UPDATES.values(), ids=_UPDATES.keys())
@pytest.mark.parametrize("name", ["host", "container"])
def test_a_release_update_whose_checkout_fails_stays_on_the_release_it_had(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    name: str,
    update: Callable[[Any, git.CloneSpec], object],
) -> None:
    """T166: a failed pin used to leave the clone on the BRANCH TIP, past both releases.

    A module that follows its releases is updated by `clone()` with `rev` set
    to the new release. Until T149 round 2 that was two moves: `reset --hard`
    onto the branch tip, then `checkout --detach` onto the release. When the
    checkout failed the clone was left on the tip, AHEAD of the release, and
    T150's guard -- rightly, for a clone somebody moved there -- refused every
    later Update as "ahead of the newest release". Measured before the fix,
    both bodies: HEAD on the tip, `commits_behind(release=True)` answering
    `AHEAD_OF_RELEASE`.

    Now a pinned update moves HEAD once, onto the pin, so a failed checkout
    leaves it on the release it was on; the pin's fetch carries no depth, so
    no graft is left either. What the next Update's guard reads there is
    T150's ordinary shallow release update, `UNPLACED` (GitHub is asked), and
    the Update after the failure lands.
    """
    upstream, commit = _upstream(tmp_path)
    old = commit("r1.txt")
    commit("past-r1.txt")
    spec = git.CloneSpec(url=upstream.as_uri(), dest=tmp_path / "mod-example", rev=old)
    git.RunnerGit().clone(spec)  # the install: tip at depth 1, then the release
    dest = spec.dest
    assert _rev(dest, "HEAD") == old
    grafts = git._shallow_roots(dest)
    commit("c4.txt")
    newer = commit("r2.txt")
    commit("past-r2.txt")
    moving = dataclasses.replace(spec, rev=newer)
    impl = _impl(name, monkeypatch)
    failed = _checkout_fails_once(monkeypatch)

    with pytest.raises(git.GitError, match="index.lock"):
        update(impl, moving)
    assert failed, "no checkout was attempted, so nothing here was tested"
    assert _rev(dest, "HEAD") == old, "a failed pin left the clone somewhere else"
    assert git._shallow_roots(dest) == grafts, "the failed pin left a graft behind"
    assert impl.no_local_commits(dest, None) is True
    placed = impl.commits_behind(dest, newer, release=True)
    assert placed is git.Behind.UNPLACED, f"the next Update's guard would read {placed}"

    update(impl, moving)
    assert _rev(dest, "HEAD") == newer


def _recording_git(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], list[str]]]:
    """Every git argv both bodies run, each with the lines it streamed (none for a plain run)."""
    ran: list[tuple[list[str], list[str]]] = []
    plain, streamed = git._run_git, git._streamed_git

    def run(argv: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        ran.append((argv, []))
        return plain(argv, cwd=cwd)

    def stream(argv: list[str], *, stage: str, cwd: Path | None = None) -> Iterator[str]:
        said: list[str] = []
        ran.append((argv, said))
        for line in streamed(argv, stage=stage, cwd=cwd):
            said.append(line)
            yield line

    monkeypatch.setattr(git, "_run_git", run)
    monkeypatch.setattr(git, "_streamed_git", stream)
    return ran


@pytest.mark.skipif(not git.git_available(), reason="needs a host git to make a real checkout")
@pytest.mark.parametrize("update", _UPDATES.values(), ids=_UPDATES.keys())
@pytest.mark.parametrize("name", ["host", "container"])
def test_return_to_the_pin_after_an_update_fetches_nothing_for_the_pin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    name: str,
    update: Callable[[Any, git.CloneSpec], object],
) -> None:
    """The common way back is free: the pin is the commit the install grafted (T149 round 3).

    Installed at a pin behind the tip, updated to the branch, then sent back
    to the pin: the pin's object has been in the store since the install, so
    `_has_commit()` says so and nothing names it on a fetch. Measured the same
    way before this was written (the pin present locally, straight after
    Update to latest).
    """
    upstream, commit = _upstream(tmp_path)
    pin = commit("pin.txt")
    commit("past-the-pin.txt")
    spec = git.CloneSpec(url=upstream.as_uri(), dest=tmp_path / "server-source", rev=pin)
    git.RunnerGit().clone(spec)
    commit("newer.txt")
    impl = _impl(name, monkeypatch)
    update(impl, dataclasses.replace(spec, rev=None))  # Update to latest
    assert _rev(spec.dest, "HEAD") != pin
    ran = _recording_git(monkeypatch)

    update(impl, spec)  # Return to the tested pin

    assert _rev(spec.dest, "HEAD") == pin
    assert not [argv for argv, _ in ran if "fetch" in argv and pin in argv], "the pin was fetched"


@pytest.mark.skipif(not git.git_available(), reason="needs a host git to make a real checkout")
@pytest.mark.parametrize("name", ["host", "container"])
def test_a_pin_older_than_the_clone_is_fetched_where_the_panel_can_see_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str
) -> None:
    """The one pin that costs a download streams its progress, and lands (T149 round 3).

    A pin OLDER than the commit the clone was made at -- a catalog pin moved
    backwards -- is not in the store, and its fetch without a depth brings
    its history down to the root (`_pin_args()`: 150 of 633 objects on the
    measured fixture). Run silently after a streamed branch fetch, that
    looked like a hang; through `clone_lines()` it is git's own progress, in
    the lines the panel shows.
    """
    upstream, commit = _upstream(tmp_path)
    old = commit("old.txt")
    for n in range(5):
        commit(f"later-{n}.txt")
    spec = git.CloneSpec(url=upstream.as_uri(), dest=tmp_path / "server-source")
    git.RunnerGit().clone(spec)
    assert old not in git._shallow_roots(spec.dest)
    impl = _impl(name, monkeypatch)
    ran = _recording_git(monkeypatch)

    said = list(impl.clone_lines(dataclasses.replace(spec, rev=old)))

    assert _rev(spec.dest, "HEAD") == old
    pinned = [lines_ for argv, lines_ in ran if "fetch" in argv and old in argv]
    assert len(pinned) == 1, "the pin was not fetched once"
    assert pinned[0], "the pin's fetch was run where nothing it said could be seen"
    assert all(line in said for line in pinned[0])


@pytest.mark.skipif(not git.git_available(), reason="needs a host git to make a real checkout")
@pytest.mark.parametrize("update", _UPDATES.values(), ids=_UPDATES.keys())
@pytest.mark.parametrize("name", ["host", "container"])
def test_a_pinned_update_still_lands_over_a_file_this_app_changed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    name: str,
    update: Callable[[Any, git.CloneSpec], object],
) -> None:
    """The pin's checkout discards what the `reset --hard` it replaced discarded (T166).

    This app writes into its own checkouts -- a carried patch, a compose
    file -- and a file changed in the working tree that ALSO changed between
    the two commits makes a plain `git checkout` refuse ("Your local changes
    would be overwritten"). The reset used to throw that change away first;
    with the reset gone for a pin, `--force` is what does, and the update
    guards have already refused anything that was not this app's own.
    """
    upstream, commit = _upstream(tmp_path)
    old = commit("r1.txt")
    spec = git.CloneSpec(url=upstream.as_uri(), dest=tmp_path / "mod-example", rev=old)
    git.RunnerGit().clone(spec)
    dest = spec.dest
    (dest / "a.txt").write_text("patched by the app\n", encoding="utf-8")
    (upstream / "a.txt").write_text("upstream moved\n", encoding="utf-8")
    subprocess.run(["git", *_AUTHOR, "commit", "-qam", "a moves"], cwd=upstream, check=True)
    newer = _rev(upstream, "HEAD")

    update(_impl(name, monkeypatch), dataclasses.replace(spec, rev=newer))

    assert _rev(dest, "HEAD") == newer
    assert (dest / "a.txt").read_text(encoding="utf-8") == "upstream moved\n"


@pytest.mark.skipif(not git.git_available(), reason="needs a host git to make a real checkout")
@pytest.mark.parametrize("orphaned", [False, True], ids=["on-a-release-branch", "on-no-branch"])
@pytest.mark.parametrize("update", _UPDATES.values(), ids=_UPDATES.keys())
@pytest.mark.parametrize("name", ["host", "container"])
def test_a_failed_pin_above_the_tip_leaves_no_graft_the_branch_can_walk_into(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    name: str,
    update: Callable[[Any, git.CloneSpec], object],
    orphaned: bool,
) -> None:
    """The review's case: a pin OFF the branch, newer than HEAD, whose checkout fails.

    A release tagged on a release branch forked from the tip -- or a pin left
    on no branch at all, as the TortoiseBots pin was on 2026-09-25 -- is not
    under the branch tip, so it can lie ABOVE a HEAD that is not a graft. Its
    depth-1 fetch then put it in `.git/shallow` without moving HEAD. That is
    harmless until the branch moves THROUGH it (measured: a fast-forward of
    the release into the branch), and then it is T149's shape and
    `no_local_commits()` answers False for good. Measured before the fix, both
    bodies: True straight after the failure, False after the fast-forward.
    """
    spec, commit = _updated_by_host_git(tmp_path)
    upstream = Path(spec.url.removeprefix("file://"))
    dest = spec.dest
    head = _rev(dest, "HEAD")
    grafts = git._shallow_roots(dest)
    # GitHub serves a commit by its id wherever it lives; git's own server
    # only does when told to.
    subprocess.run(["git", "config", "uploadpack.allowAnySHA1InWant", "true"], cwd=upstream)
    subprocess.run(["git", "checkout", "-q", "-b", "release"], cwd=upstream, check=True)
    commit("rc.txt")
    above = commit("release.txt")
    subprocess.run(["git", "checkout", "-q", "main"], cwd=upstream, check=True)
    if orphaned:
        subprocess.run(["git", "branch", "-q", "-D", "release"], cwd=upstream, check=True)
    impl = _impl(name, monkeypatch)
    failed = _checkout_fails_once(monkeypatch)

    with pytest.raises(git.GitError, match="index.lock"):
        update(impl, dataclasses.replace(spec, rev=above))
    assert failed, "no checkout was attempted, so nothing here was tested"
    assert _rev(dest, "HEAD") == head
    assert git._shallow_roots(dest) == grafts, "the failed pin left a graft behind"

    subprocess.run(["git", "merge", "-q", "--ff-only", above], cwd=upstream, check=True)
    commit("next.txt")
    assert impl.no_local_commits(dest, None) is True


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

OFF_PATH_EXE = r"C:\Users\user\AppData\Local\Programs\DockerDesktop\resources\bin\docker.EXE"


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


# -- commit pins -------------------------------------------------------------

PIN = "0123456789abcdef0123456789abcdef01234567"
BAD_PIN = "cafebabe" * 5  # 40 hex characters, and no such commit


def test_a_pinned_source_is_fetched_by_hash_and_checked_out_detached(
    seen: list[list[str]], tmp_path: Path
) -> None:
    """`rev` is honoured AFTER the clone, by hash, and never as a branch.

    A `--depth 1` clone holds only the tip, so `git checkout <sha>` on it answers
    "reference is not a tree" for every commit but one; the object has to be
    fetched first, and the fetch keeps the clone's own depth. `--detach`
    because a pin is not a branch: the next update must not drag it to the tip.
    """
    git.RunnerGit().clone(
        git.CloneSpec(url="https://example/repo.git", dest=tmp_path / "core", rev=PIN)
    )
    fetch = next(argv for argv in seen if "fetch" in argv)
    assert fetch[fetch.index("fetch") :] == ["fetch", "--depth=1", "origin", PIN]
    checkout = next(argv for argv in seen if "checkout" in argv)
    assert checkout[-3:] == ["checkout", "--detach", PIN]
    assert seen.index(fetch) > 0, "the clone comes first; the pin is applied to it"
    assert seen.index(fetch) < seen.index(checkout)


def test_an_unpinned_source_never_checks_anything_out(
    seen: list[list[str]], tmp_path: Path
) -> None:
    git.RunnerGit().clone(git.CloneSpec(url="https://example/repo.git", dest=tmp_path / "core"))
    assert not any("checkout" in argv for argv in seen)


@pytest.mark.parametrize("held", [False, True], ids=["pin-not-held", "pin-held"])
@pytest.mark.parametrize(
    "impl", [git.RunnerGit(), git.ContainerGit()], ids=["host", "containerized"]
)
def test_updating_a_pinned_clone_moves_it_once_onto_the_pin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    impl: git.RunnerGit | git.ContainerGit,
    held: bool,
) -> None:
    """A pinned update is ONE move, onto the pin, and its fetch writes no graft (T149, T166).

    It used to reset onto the branch tip and then check the pin out on top, so
    a failed checkout left the clone on the tip -- past the release, where
    T150's guard refuses every later Update. And the pin was fetched at the
    clone's depth, which grafts a pin above a HEAD that is not one. A pin the
    store already holds is not fetched at all (T149 round 3). The real shapes
    are the real-git tests above; this pins the argv on both bodies, for every
    depth a source can carry.
    """
    seen: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        asks = "cat-file" in argv
        return _completed(returncode=1 if asks and not held else 0)

    monkeypatch.setattr(runner, "run", fake_run)
    for depth in (1, None):
        seen.clear()
        dest = tmp_path / f"core{depth}"
        (dest / ".git").mkdir(parents=True)
        impl.clone(git.CloneSpec(url="https://example/repo.git", dest=dest, rev=PIN, depth=depth))
        assert not any("reset" in argv for argv in seen), "a stop on the branch tip first"
        assert not any("--depth" in arg for argv in seen for arg in argv), depth
        asked = next(index for index, argv in enumerate(seen) if "cat-file" in argv)
        assert seen[asked][-3:] == ["cat-file", "-e", f"{PIN}^{{commit}}"]
        fetches = [index for index, argv in enumerate(seen) if argv[-1] == PIN and "fetch" in argv]
        checkout = next(index for index, argv in enumerate(seen) if "checkout" in argv)
        assert seen[checkout][-4:] == ["checkout", "--detach", "--force", PIN]
        if held:
            assert fetches == [], "a pin the store holds was fetched anyway"
        else:
            assert [seen[index][-3:] for index in fetches] == [["fetch", "origin", PIN]]
            assert asked < fetches[0] < checkout


def test_an_unpinned_update_still_resets_onto_the_tip(
    seen: list[list[str]], tmp_path: Path
) -> None:
    """The reset is left out for a PIN only: a branch update is the reset (T166)."""
    for impl in (git.RunnerGit(), git.ContainerGit()):
        seen.clear()
        dest = tmp_path / type(impl).__name__
        (dest / ".git").mkdir(parents=True)
        impl.clone(git.CloneSpec(url="https://example/repo.git", dest=dest))
        assert seen[-1][-3:] == ["reset", "--hard", "FETCH_HEAD"]
        assert not any("checkout" in argv for argv in seen)


def test_container_git_pins_the_same_way(seen: list[list[str]], tmp_path: Path) -> None:
    """Two implementations of one Protocol must not disagree about what they produce."""
    dest = tmp_path / "core"
    git.ContainerGit().clone(
        git.CloneSpec(url="https://example/core.git", dest=dest, depth=None, rev=PIN)
    )
    fetch = next(argv for argv in seen if "fetch" in argv)
    assert fetch[fetch.index("fetch") :] == ["fetch", "origin", PIN], "depth=None → no --depth"
    checkout = next(argv for argv in seen if "checkout" in argv)
    assert checkout[:3] == ["docker", "run", "--rm"], "still containerised"
    assert checkout[-3:] == ["checkout", "--detach", PIN]


def test_a_bad_pin_on_an_existing_clone_is_not_blamed_on_the_container(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """A pin that does not exist is a catalog fault, and must read like one.

    `ContainerGit.clone()`'s update path wraps its fetch/reset in the fallback
    that exists for "containerised git cannot work on this host" — and the pin
    used to sit inside it. A bad SHA therefore reached the user as
    `containerized git update failed in <dest> (...); falling back to host git`,
    the whole fetch ran a second time through host git, and only then did
    `upload-pack: not our ref` appear underneath an explanation that was not
    true. The fresh-clone path never did that: it pins outside the try, and a
    `GitError` from the pin propagates. Both halves of one method now agree.
    """
    dest = tmp_path / "core"
    (dest / ".git").mkdir(parents=True)
    seen_argv: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        seen_argv.append(argv)
        if BAD_PIN in argv:
            return _completed(
                returncode=128,
                stderr=f"fatal: remote error: upload-pack: not our ref {BAD_PIN}",
            )
        return _completed()

    monkeypatch.setattr(runner, "run", fake_run)
    # The fallback would fire if the pin were still inside the try: this host
    # has a git, and the failure says nothing about Docker.
    monkeypatch.setattr(git, "git_available", lambda: True)
    caplog.set_level("WARNING")
    with pytest.raises(git.GitError) as raised:
        git.ContainerGit().clone(
            git.CloneSpec(url="https://example/core.git", dest=dest, depth=None, rev=BAD_PIN)
        )
    assert "not our ref" in str(raised.value), "the real failure, not a container story"
    logged = "\n".join(record.message for record in caplog.records)
    assert "falling back to host git" not in logged, "a bad pin is not an environment problem"
    assert not any(argv[0] == "git" for argv in seen_argv), "host git must not be tried"
    assert sum(1 for argv in seen_argv if BAD_PIN in argv) == 1, "the pin is attempted once"


def test_a_source_pinned_on_a_named_branch_clones_the_branch_and_still_pins_by_hash(
    seen: list[list[str]], tmp_path: Path
) -> None:
    """`branch` AND `rev` on one source — the combination `wow-tortoise` ships.

    Every other `rev` test here pins a source with no `branch`, so this pairing
    was asserted and never exercised (review, 2026-09-03). It is not obviously
    safe: `git clone --branch X` implies `--single-branch`, which narrows the
    remote's fetch refspec to `refs/heads/X`, and the pin then asks for a commit
    BY HASH rather than through that refspec.

    Driven against the real repository before this test was written, because a
    unit test over a fake runner can only prove the argv, never that git accepts
    it. On yulon-ubuntu, in the same containerised git the installer uses:

        clone --depth 1 --branch playerbots-integration-gh   -> tip 7266affc
        fetch --depth=1 origin 7c0fb278…                     -> FETCH_HEAD, exit 0
        checkout --detach 7c0fb278…                          -> HEAD is 7c0fb278…

    The tip had already moved off the pinned commit by then, which is the whole
    reason the entry carries a `rev`: an unpinned install would now build a tree
    other than the one Tortoise's six catalog facts were measured against.

    What this test adds is the ORDER and the shape: the branch reaches the
    clone, and the pin still happens afterwards, by hash, detached.
    """
    git.RunnerGit().clone(
        git.CloneSpec(
            url="https://example/repo.git",
            dest=tmp_path / "core",
            branch="playerbots-integration-gh",
            rev=PIN,
        )
    )
    clone = next(argv for argv in seen if "clone" in argv)
    assert "--branch" in clone and clone[clone.index("--branch") + 1] == "playerbots-integration-gh"

    fetch = next(argv for argv in seen if "fetch" in argv)
    assert fetch[fetch.index("fetch") :] == [
        "fetch",
        "--depth=1",
        "origin",
        PIN,
    ], "the pin must be fetched by hash; a --single-branch clone cannot reach it any other way"
    checkout = next(argv for argv in seen if "checkout" in argv)
    assert checkout[-3:] == ["checkout", "--detach", PIN]
    assert seen.index(clone) < seen.index(fetch) < seen.index(checkout)


# --------------------------------------------------------------------------
# commits-behind (checklist 8.7a: "how far behind each installed module is")
# --------------------------------------------------------------------------


HEAD_SHA = "d" * 40
FETCHED_SHA = "5" * 40


@pytest.mark.parametrize(
    "impl",
    [
        git.RunnerGit(),
        git.ContainerGit(selinux_enforcing=lambda: False, filesystem_type=lambda _path: "ext4"),
    ],
    ids=["host", "containerized"],
)
def test_commits_behind_counts_what_the_update_would_bring_in(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, impl: git.BehindReader
) -> None:
    """The OTHER direction of the range, and it is not the same question.

    `no_local_commits()` counts `FETCH_HEAD..HEAD` — what this checkout has that
    the update has not, a guard's input. This counts `HEAD..FETCH_HEAD` — what
    the update would bring in, a NUMBER shown to a user. Reversing the range is
    the whole difference between them and the easiest thing in this file to get
    backwards, so both ends are pinned here.

    The target is `FETCH_HEAD` after this method's own fetch, for
    `no_local_commits()`'s measured reason: `fetch origin HEAD` refreshes no
    remote-tracking ref, so `origin/<branch>` — which is what the Rust launcher
    counted against (`crates/dml-wow/src/maint.rs:443`, `HEAD..origin/{branch}`,
    after a refspec-less `git fetch origin` that DOES refresh it) — is stale for
    every module in the wow-wotlk catalog, all 21 of which name no branch.
    """
    dest = tmp_path / "mod-aoe-loot"
    (dest / ".git").mkdir(parents=True)
    answers: list[subprocess.CompletedProcess[str]] = []
    seen_argv: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        seen_argv.append(argv)
        return answers.pop(0)

    monkeypatch.setattr(runner, "run", fake_run)

    full = _completed(stdout=f"false\n{HEAD_SHA}\n{FETCHED_SHA}\n")
    answers += [_completed(), full, _completed(stdout="7\n")]
    assert impl.commits_behind(dest, "wotlk") == 7
    assert seen_argv[-3][-3:] == ["fetch", "origin", "wotlk"]
    # T147: which commit HEAD is and whether the checkout is shallow, in one
    # question, asked AFTER the fetch -- a full checkout is counted as before.
    # T148: the same question says which commit the fetch brought, and the
    # range names THAT commit, so a later fetch cannot change what was counted.
    assert seen_argv[-2][-4:] == ["rev-parse", "--is-shallow-repository", "HEAD", "FETCH_HEAD"]
    assert seen_argv[-1][-3:] == ["rev-list", "--count", f"HEAD..{FETCHED_SHA}"]
    answers += [_completed(), full, _completed(stdout="7\n")]
    assert impl.counted_behind(dest, "wotlk") == git.Counted(7, HEAD_SHA, FETCHED_SHA)
    assert not [arg for arg in seen_argv[-3] if arg.startswith("--depth")]

    # Up to date is 0, and 0 is a real answer — never None, which means "could
    # not ask" and is what a caller has to print differently.
    answers += [_completed(), full, _completed(stdout="0\n")]
    assert impl.commits_behind(dest, None) == 0
    assert seen_argv[-3][-3:] == ["fetch", "origin", "HEAD"]

    # A SHALLOW checkout is walked instead, and both back-ends read the walk
    # the same way: straight on top of HEAD is counted; a walk that stopped
    # anywhere else is behind by a number nobody can prove.
    shallow = _completed(stdout=f"true\n{HEAD_SHA}\n{FETCHED_SHA}\n")
    straight = f"{'b' * 40} {'a' * 40}\n{'a' * 40} {HEAD_SHA}\n-{HEAD_SHA}\n"
    answers += [_completed(), shallow, _completed(stdout=straight)]
    assert impl.commits_behind(dest, None) == 2
    assert seen_argv[-1][-4:] == ["rev-list", "--parents", "--boundary", f"HEAD..{FETCHED_SHA}"]
    elsewhere = f"{'a' * 40} {'c' * 40}\n-{'c' * 40}\n"
    answers += [_completed(), shallow, _completed(stdout=elsewhere)]
    assert impl.commits_behind(dest, None) is git.Behind.UNCOUNTED
    parentless = f"{'a' * 40}\n"
    answers += [_completed(), shallow, _completed(stdout=parentless)]
    assert impl.commits_behind(dest, None) is git.Behind.UNCOUNTED
    answers += [_completed(), shallow, _completed(stdout="")]
    assert impl.commits_behind(dest, None) == 0

    # A fetch that cannot reach the remote asks nothing further.
    answers.append(_completed(returncode=128, stderr="Could not resolve host"))
    before = len(seen_argv)
    assert impl.commits_behind(dest, None) is None
    assert len(seen_argv) == before + 1, "a failed fetch must not be followed by a count"

    # A count that will not parse is None too: a figure a user checks against
    # `git rev-list` by hand must never be invented. So is a shape question
    # that did not answer as asked, and a git that refused either one.
    answers += [_completed(), full, _completed(stdout="not a number\n")]
    assert impl.commits_behind(dest, None) is None
    answers += [_completed(), _completed(stdout="true\n")]
    assert impl.commits_behind(dest, None) is None
    answers += [_completed(), _completed(stdout=f"true\n{HEAD_SHA}\nFETCH_HEAD\n")]
    assert impl.counted_behind(dest, None) == git.Counted(None)
    answers += [_completed(), _completed(returncode=128, stderr="broken")]
    assert impl.commits_behind(dest, None) is None
    answers += [_completed(), shallow, _completed(returncode=128, stderr="broken")]
    assert impl.commits_behind(dest, None) is None
    assert impl.commits_behind(tmp_path / "not-a-checkout", None) is None
    answers.append(_completed(returncode=128, stderr="Could not resolve host"))
    assert impl.counted_behind(dest, None) == git.Counted(None)


@pytest.mark.parametrize(
    "impl",
    [
        git.RunnerGit(),
        git.ContainerGit(selinux_enforcing=lambda: False, filesystem_type=lambda _path: "ext4"),
    ],
    ids=["host", "containerized"],
)
def test_commits_behind_asked_as_a_release_places_head_before_counting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, impl: git.BehindReader
) -> None:
    """T150: both back-ends read the release's two sides, and the walk, the same way.

    The real-repository tests below prove what git answers for each shape;
    this pins that the containerized body -- which a host with no git runs --
    reads those answers identically, and that an answer that does not parse
    is "could not ask", never a guess in either direction.
    """
    dest = tmp_path / "mod-example"
    (dest / ".git").mkdir(parents=True)
    answers: list[subprocess.CompletedProcess[str]] = []
    seen_argv: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        seen_argv.append(argv)
        return answers.pop(0)

    monkeypatch.setattr(runner, "run", fake_run)
    rel = "e" * 40
    full = _completed(stdout=f"false\n{HEAD_SHA}\n{rel}\n")
    shallow = _completed(stdout=f"true\n{HEAD_SHA}\n{rel}\n")

    def sides(mine: int, theirs: int) -> subprocess.CompletedProcess[str]:
        return _completed(stdout=f"{mine}\t{theirs}\n")

    # Full: under the release is its count, read off the same answer.
    answers += [_completed(), full, sides(0, 4)]
    assert impl.commits_behind(dest, rel, release=True) == 4
    assert seen_argv[-3][-3:] == ["fetch", "origin", rel]
    assert seen_argv[-1][-4:] == ["rev-list", "--left-right", "--count", f"HEAD...{rel}"]
    answers += [_completed(), full, sides(0, 0)]
    assert impl.commits_behind(dest, rel, release=True) == 0
    # Past it, on either shape, and off its line on a full one.
    answers += [_completed(), full, sides(3, 0)]
    assert impl.commits_behind(dest, rel, release=True) is git.Behind.AHEAD_OF_RELEASE
    answers += [_completed(), shallow, sides(1, 0)]
    assert impl.commits_behind(dest, rel, release=True) is git.Behind.AHEAD_OF_RELEASE
    answers += [_completed(), full, sides(3, 2)]
    assert impl.commits_behind(dest, rel, release=True) is git.Behind.OFF_RELEASE

    # Shallow and under it: T147's walk, unchanged.
    straight = f"{'b' * 40} {HEAD_SHA}\n-{HEAD_SHA}\n"
    answers += [_completed(), shallow, sides(0, 1), _completed(stdout=straight)]
    assert impl.commits_behind(dest, rel, release=True) == 1
    assert seen_argv[-1][-4:] == ["rev-list", "--parents", "--boundary", f"HEAD..{rel}"]

    # Shallow and not under it: a parentless commit decides, by its own object.
    walk = f"{rel} {'a' * 40}\n{'a' * 40}\n"
    root = _completed(stdout=f"tree {'f' * 40}\nauthor t\n\nroot\n")
    answers += [_completed(), shallow, sides(1, 2), _completed(stdout=walk), root]
    assert impl.commits_behind(dest, rel, release=True) is git.Behind.NOT_IN_RELEASE
    assert seen_argv[-1][-3:] == ["cat-file", "commit", "a" * 40]
    graft = _completed(stdout=f"tree {'f' * 40}\nparent {'9' * 40}\nauthor t\n\ncut\n")
    answers += [_completed(), shallow, sides(1, 2), _completed(stdout=walk), graft]
    assert impl.commits_behind(dest, rel, release=True) is git.Behind.UNPLACED
    # A message that merely MENTIONS a parent is not one.
    quoted = _completed(stdout=f"tree {'f' * 40}\nauthor t\n\nparent {'9' * 40}\n")
    answers += [_completed(), shallow, sides(1, 2), _completed(stdout=walk), quoted]
    assert impl.commits_behind(dest, rel, release=True) is git.Behind.NOT_IN_RELEASE
    # Every walked commit has its parents and the walk stopped only on HEAD's side.
    beside = f"{rel} {'c' * 40}\n-{'c' * 40}\n"
    answers += [_completed(), shallow, sides(1, 1), _completed(stdout=beside)]
    assert impl.commits_behind(dest, rel, release=True) is git.Behind.NOT_IN_RELEASE

    # Could not ask stays could not ask.
    answers += [_completed(), full, _completed(stdout="7\n")]
    assert impl.commits_behind(dest, rel, release=True) is None
    answers += [_completed(), full, _completed(stdout="x\ty\n")]
    assert impl.commits_behind(dest, rel, release=True) is None
    answers += [_completed(), full, _completed(returncode=128, stderr="broken")]
    assert impl.commits_behind(dest, rel, release=True) is None
    answers += [
        _completed(),
        shallow,
        sides(1, 2),
        _completed(stdout=walk),
        _completed(returncode=128, stderr="broken"),
    ]
    assert impl.commits_behind(dest, rel, release=True) is None


@pytest.mark.skipif(not git.git_available(), reason="needs a host git to make a real checkout")
def test_the_behind_figure_equals_the_same_range_run_by_hand(tmp_path: Path) -> None:
    """8.7a's definition of done, against real git rather than a mock.

    A mock can only prove the argv. What has to be true is that the number the
    app shows is the number `git rev-list --count HEAD..FETCH_HEAD` prints for
    the same checkout — the comparison the live gate makes on the box, made here
    against a `file://` remote so it runs in CI too.
    """
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    author = ["-c", "user.email=t@example", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", "-b", "main", "."], cwd=upstream, check=True)
    (upstream / "a.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=upstream, check=True)
    subprocess.run([*["git", *author], "commit", "-qm", "one"], cwd=upstream, check=True)

    dest = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(upstream), str(dest)], check=True)
    impl = git.RunnerGit()
    assert impl.commits_behind(dest, "main") == 0

    for name in ("two", "three"):
        (upstream / f"{name}.txt").write_text(f"{name}\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=upstream, check=True)
        subprocess.run([*["git", *author], "commit", "-qm", name], cwd=upstream, check=True)

    assert impl.commits_behind(dest, "main") == 2
    by_hand = subprocess.run(
        ["git", "rev-list", "--count", "HEAD..FETCH_HEAD"],
        cwd=dest,
        capture_output=True,
        text=True,
        check=True,
    )
    assert by_hand.stdout.strip() == "2"
    # And the guard's question still answers its own: nothing of the user's is
    # in the way of an update that is two commits ahead of this checkout.
    assert impl.no_local_commits(dest, "main") is True


# -- T147: the figure on a SHALLOW checkout -----------------------------------
#
# Every module clone is depth 1 (`CloneSpec.depth` defaults to it), and on a
# depth-1 checkout `HEAD..FETCH_HEAD` is not the distance: measured 2775 for a
# real 50 on mod-playerbots. These run the app's own `RunnerGit` against real
# repositories -- a work tree standing in for upstream, published as a bare
# repo and cloned over `file://`, because `--depth` over a plain path is
# ignored and would quietly test a FULL clone. The true distance is always read
# off the upstream work tree, which has the whole history.


class _Upstream:
    """A repository with history, published as a bare repo a clone can reach by `file://`."""

    def __init__(self, root: Path) -> None:
        self.work = root / "work"
        self.bare = root / "upstream.git"
        self.work.mkdir()
        self.git("init", "-q", "-b", "main", ".")

    def git(self, *argv: str, cwd: Path | None = None) -> str:
        done = subprocess.run(
            ["git", "-c", "user.email=t@example", "-c", "user.name=t", *argv],
            cwd=cwd or self.work,
            check=True,
            capture_output=True,
            text=True,
        )
        return done.stdout.strip()

    def commit(self, name: str) -> str:
        (self.work / name).write_text(f"{name}\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", name)
        return self.git("rev-parse", "HEAD")

    def commits(self, prefix: str, count: int) -> list[str]:
        return [self.commit(f"{prefix}{i}") for i in range(count)]

    def merge_branch_from(self, base: str, name: str, count: int) -> None:
        """A branch of `count` commits forked at `base`, merged into main with a merge commit."""
        self.git("checkout", "-q", "-b", name, base)
        self.commits(f"{name}-", count)
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff", name, "-m", f"merge {name}")

    def publish(self) -> str:
        """Push main to the bare repo a clone fetches from; return main's commit."""
        if not self.bare.exists():
            self.git("init", "-q", "--bare", "-b", "main", str(self.bare))
        self.git("push", "-q", "--force", str(self.bare), "main")
        return self.git("rev-parse", "HEAD")

    @property
    def url(self) -> str:
        return self.bare.as_uri()

    def distance(self, old: str, new: str) -> int:
        """The TRUE `old..new`, from the work tree that has every commit."""
        return int(self.git("rev-list", "--count", f"{old}..{new}"))


def _by_hand(dest: Path) -> int:
    """`git rev-list --count HEAD..FETCH_HEAD` as a person would type it in the checkout."""
    done = subprocess.run(
        ["git", "rev-list", "--count", "HEAD..FETCH_HEAD"],
        cwd=dest,
        check=True,
        capture_output=True,
        text=True,
    )
    return int(done.stdout)


def _counted_have_parents(dest: Path) -> bool:
    """Does every commit `HEAD..FETCH_HEAD` walks still have a parent in this checkout?"""
    done = subprocess.run(
        ["git", "rev-list", "--parents", "HEAD..FETCH_HEAD"],
        cwd=dest,
        check=True,
        capture_output=True,
        text=True,
    )
    return all(len(line.split()) > 1 for line in done.stdout.splitlines())


@pytest.mark.skipif(not git.git_available(), reason="needs a host git to make a real checkout")
def test_a_shallow_checkout_behind_a_merge_is_not_counted_as_the_whole_history(
    tmp_path: Path,
) -> None:
    """The measured 2775, in miniature: a branch merged since, forked before HEAD.

    The fetch hands that branch's history down to the root, and the commits
    under HEAD's graft -- the ones that would exclude it -- never arrive. So
    the plain range counts upstream's whole past as new. The only honest answer
    is "behind, how far this checkout cannot say".

    The fixture breaks one rule of the proof and only one: a counted commit
    with no parent (the root). Every other commit the walk stops at is HEAD.
    """
    up = _Upstream(tmp_path)
    history = up.commits("c", 20)
    installed = up.publish()
    spec = git.CloneSpec(url=up.url, dest=tmp_path / "mod-example")
    assert spec.depth == 1
    impl = git.RunnerGit()
    impl.clone(spec)
    grafts = (spec.dest / ".git" / "shallow").read_text(encoding="utf-8")

    up.merge_branch_from(history[10], "side", 3)
    up.commit("after")
    tip = up.publish()

    said = impl.commits_behind(spec.dest, None)
    # The shape is the one measured: the plain range says far more than the truth.
    assert up.distance(installed, tip) == 5
    assert _by_hand(spec.dest) == 16
    assert not _counted_have_parents(spec.dest)
    assert said is git.Behind.UNCOUNTED, said
    # And the question did not deepen the clone to answer itself.
    assert (spec.dest / ".git" / "shallow").read_text(encoding="utf-8") == grafts


@pytest.mark.skipif(not git.git_available(), reason="needs a host git to make a real checkout")
def test_a_shallow_checkout_whose_tip_is_already_a_graft_is_not_one_commit_behind(
    tmp_path: Path,
) -> None:
    """The other wrong answer, T146's live evidence: "1" for any distance.

    A pinned source clones the tip at depth 1 and then fetches the pin at
    depth 1 (`_pin()`), so the tip the count fetches is already here -- as a
    graft. The walk from it stops at once and counts the tip alone.
    """
    up = _Upstream(tmp_path)
    history = up.commits("c", 20)
    pin = history[-6]
    tip = up.publish()
    dest = tmp_path / "mod-example"
    impl = git.RunnerGit()
    impl.clone(git.CloneSpec(url=up.url, dest=dest, rev=pin))

    said = impl.commits_behind(dest, None)
    assert up.distance(pin, tip) == 5
    assert _by_hand(dest) == 1
    assert said is git.Behind.UNCOUNTED, said


@pytest.mark.skipif(not git.git_available(), reason="needs a host git to make a real checkout")
def test_a_merge_forked_under_the_graft_is_not_counted_after_an_update(tmp_path: Path) -> None:
    """Every counted commit has its parents, and the count is still wrong.

    After one host-git update (fetch without depth, reset) HEAD is no longer a
    graft, and an earlier merge brought history in beside the graft. A branch
    forked from a commit UNDER the graft then lands as five "new" commits whose
    parents are all here, so a no-parentless-commits rule alone passes it.
    What gives it away is where the walk stopped: at a commit that is not HEAD.
    This fixture breaks that rule and only that one.
    """
    up = _Upstream(tmp_path)
    history = up.commits("c", 20)
    up.publish()
    spec = git.CloneSpec(url=up.url, dest=tmp_path / "mod-example")
    impl = git.RunnerGit()
    impl.clone(spec)
    up.merge_branch_from(history[10], "side", 3)
    up.commit("after")
    up.publish()
    impl.clone(spec)  # the existing clone, so `_update()`: fetch + reset --hard FETCH_HEAD
    updated = impl.head_sha(spec.dest)
    assert updated is not None

    up.commits("more", 2)
    up.merge_branch_from(history[15], "late", 2)
    tip = up.publish()

    said = impl.commits_behind(spec.dest, None)
    assert up.distance(updated, tip) == 5
    assert _by_hand(spec.dest) == 10
    assert _counted_have_parents(spec.dest), "not the shape this test is about"
    assert said is git.Behind.UNCOUNTED, said


@pytest.mark.skipif(not git.git_available(), reason="needs a host git to make a real checkout")
def test_counted_behind_names_the_two_real_commits_it_counted_between(tmp_path: Path) -> None:
    """T148: HEAD and the upstream tip the count's own fetch brought, beside its figure."""
    up = _Upstream(tmp_path)
    up.commits("c", 3)
    installed = up.publish()
    spec = git.CloneSpec(url=up.url, dest=tmp_path / "mod-example")
    impl = git.RunnerGit()
    impl.clone(spec)
    up.commits("new", 2)
    tip = up.publish()

    assert impl.counted_behind(spec.dest, None) == git.Counted(2, installed, tip)
    assert impl.counted_behind(spec.dest, "no-such-branch") == git.Counted(None)


@pytest.mark.skipif(not git.git_available(), reason="needs a host git to make a real checkout")
def test_a_shallow_checkout_on_straight_history_keeps_its_exact_count(tmp_path: Path) -> None:
    """What the uncounted answer must not swallow: a count the checkout CAN prove.

    Straight history on top of HEAD, the shape of a module that squash-merges
    or rebases: every commit the walk counts has its parent, and the walk stops
    at HEAD and nowhere else, so the figure is exact on a depth-1 checkout too.
    Up to date is still 0, never uncounted.
    """
    up = _Upstream(tmp_path)
    up.commits("c", 20)
    installed = up.publish()
    spec = git.CloneSpec(url=up.url, dest=tmp_path / "mod-example")
    impl = git.RunnerGit()
    impl.clone(spec)
    assert impl.commits_behind(spec.dest, None) == 0

    up.commits("new", 5)
    tip = up.publish()
    assert impl.commits_behind(spec.dest, None) == up.distance(installed, tip) == 5

    # Once updated the checkout is no longer a graft at HEAD, and it still counts.
    impl.clone(spec)
    updated = impl.head_sha(spec.dest)
    assert updated is not None
    up.commits("next", 3)
    tip = up.publish()
    assert impl.commits_behind(spec.dest, None) == up.distance(updated, tip) == 3
    assert (spec.dest / ".git" / "shallow").is_file(), "the clone must still be shallow"


@pytest.mark.skipif(not git.git_available(), reason="needs a host git to make a real checkout")
def test_a_full_checkout_counts_through_merges_exactly(tmp_path: Path) -> None:
    """The shallow rule is for shallow checkouts: a full clone's range is the truth as it is.

    The same merged-in branch the shallow tests refuse to count stops this
    walk at a commit that is not HEAD too -- which in a full clone proves
    nothing is wrong. Applying the shallow rule here would hide an exact number.
    """
    up = _Upstream(tmp_path)
    history = up.commits("c", 20)
    installed = up.publish()
    spec = git.CloneSpec(url=up.url, dest=tmp_path / "mod-example", depth=None)
    impl = git.RunnerGit()
    impl.clone(spec)
    assert not (spec.dest / ".git" / "shallow").exists()

    up.merge_branch_from(history[10], "side", 3)
    up.commit("after")
    tip = up.publish()
    assert impl.commits_behind(spec.dest, None) == up.distance(installed, tip) == 5


# -- T150: a checkout that is not UNDER its release --------------------------
#
# A module that follows its releases (T126) is counted against the newest
# release's commit, and its update resets the clone to that commit. Where HEAD
# is newer than the release, or on another line, that reset is a downgrade --
# so the question asked as a release has three more answers than T147's, and
# none of them is behind. Each fixture below puts HEAD in ONE place relative
# to the release and changes nothing else.


def _release_off_main(up: _Upstream, base: str, name: str, count: int) -> str:
    """`count` commits on a branch forked at `base`, pushed but NOT merged: a release off main."""
    up.git("checkout", "-q", "-b", name, base)
    tip = up.commits(f"{name}-", count)[-1]
    up.git("checkout", "-q", "main")
    up.git("push", "-q", "--force", str(up.bare), name)
    return tip


@pytest.mark.skipif(not git.git_available(), reason="needs a host git to make a real checkout")
def test_a_full_checkout_newer_than_its_release_is_ahead_of_it(tmp_path: Path) -> None:
    """HEAD descends from the release: nothing to update to, and the row says why.

    Before T150 this was 0, "on the newest release" -- not true, and the row
    could not say what it was instead. Asked as a BRANCH the same commit is
    still 0: branch rows keep T147's figure exactly.
    """
    up = _Upstream(tmp_path)
    history = up.commits("c", 20)
    up.publish()
    spec = git.CloneSpec(url=up.url, dest=tmp_path / "mod-example", depth=None)
    impl = git.RunnerGit()
    impl.clone(spec)
    release = history[15]

    assert impl.commits_behind(spec.dest, release, release=True) is git.Behind.AHEAD_OF_RELEASE
    assert impl.commits_behind(spec.dest, release) == 0


@pytest.mark.skipif(not git.git_available(), reason="needs a host git to make a real checkout")
def test_a_full_checkout_off_its_release_line_is_off_it(tmp_path: Path) -> None:
    """HEAD and the release each carry commits the other lacks: not behind.

    Before T150 this counted the release's own two commits and offered them as
    "new release", and the update then dropped the four HEAD has that the
    release does not.
    """
    up = _Upstream(tmp_path)
    history = up.commits("c", 20)
    up.publish()
    release = _release_off_main(up, history[15], "rel", 2)
    spec = git.CloneSpec(url=up.url, dest=tmp_path / "mod-example", depth=None)
    impl = git.RunnerGit()
    impl.clone(spec)

    assert _by_hand_after(impl, spec.dest, release) == 2
    assert impl.commits_behind(spec.dest, release, release=True) is git.Behind.OFF_RELEASE


def _by_hand_after(impl: git.RunnerGit, dest: Path, ref: str) -> int:
    """Fetch `ref` as the count does, then `HEAD..FETCH_HEAD` by hand: what T147 would print."""
    impl.commits_behind(dest, ref)
    return _by_hand(dest)


@pytest.mark.skipif(not git.git_available(), reason="needs a host git to make a real checkout")
def test_a_full_checkout_under_its_release_keeps_its_exact_count(tmp_path: Path) -> None:
    """What the new answers must not swallow: a release that IS newer, and one HEAD is on."""
    up = _Upstream(tmp_path)
    history = up.commits("c", 20)
    up.publish()
    spec = git.CloneSpec(url=up.url, dest=tmp_path / "mod-example", depth=None)
    impl = git.RunnerGit()
    impl.clone(spec)
    up.git("checkout", "-q", "--detach", history[12], cwd=spec.dest)

    assert impl.commits_behind(spec.dest, history[19], release=True) == 7
    assert impl.commits_behind(spec.dest, history[12], release=True) == 0


@pytest.mark.skipif(not git.git_available(), reason="needs a host git to make a real checkout")
def test_a_depth_1_checkout_newer_than_its_release_is_not_offered_it(tmp_path: Path) -> None:
    """The ticket's shape: installed from the branch at depth 1, then told to follow releases.

    The fetch of the older release hands its whole history down to the root,
    and HEAD -- a graft -- is not in it. Nothing under HEAD's graft is here, so
    whether HEAD is NEWER than the release or on another line this checkout
    cannot show; what it can show is that the release does not contain HEAD,
    because the release's history arrived whole. Before T150: uncounted, the
    "Update available" chip, and a reset back to the release.
    """
    up = _Upstream(tmp_path)
    history = up.commits("c", 20)
    up.publish()
    spec = git.CloneSpec(url=up.url, dest=tmp_path / "mod-example")
    assert spec.depth == 1
    impl = git.RunnerGit()
    impl.clone(spec)
    grafts = (spec.dest / ".git" / "shallow").read_text(encoding="utf-8")

    said = impl.commits_behind(spec.dest, history[15], release=True)
    assert impl.commits_behind(spec.dest, history[15]) is git.Behind.UNCOUNTED, "not the shape"
    assert said is git.Behind.NOT_IN_RELEASE, said
    assert (spec.dest / ".git" / "shallow").read_text(encoding="utf-8") == grafts


@pytest.mark.skipif(not git.git_available(), reason="needs a host git to make a real checkout")
def test_a_depth_1_checkout_off_its_release_line_is_not_offered_it(tmp_path: Path) -> None:
    """A release published from a branch forked below HEAD: the same answer, for the same proof."""
    up = _Upstream(tmp_path)
    history = up.commits("c", 20)
    up.publish()
    spec = git.CloneSpec(url=up.url, dest=tmp_path / "mod-example")
    impl = git.RunnerGit()
    impl.clone(spec)
    release = _release_off_main(up, history[15], "rel", 2)

    assert impl.commits_behind(spec.dest, release, release=True) is git.Behind.NOT_IN_RELEASE


@pytest.mark.skipif(not git.git_available(), reason="needs a host git to make a real checkout")
def test_a_shallow_checkout_that_can_see_its_release_underneath_is_ahead_of_it(
    tmp_path: Path,
) -> None:
    """Shallow, and still proved: the release sits between the graft and HEAD.

    Installed from the branch at depth 1, then updated once (a fetch with no
    depth, then the reset), so everything from the old graft up to HEAD is
    here with its parents -- and the release is one of those commits.
    """
    up = _Upstream(tmp_path)
    up.commits("c", 20)
    up.publish()
    spec = git.CloneSpec(url=up.url, dest=tmp_path / "mod-example")
    impl = git.RunnerGit()
    impl.clone(spec)
    newer = up.commits("new", 5)
    up.publish()
    impl.clone(spec)  # the existing clone, so `_update()`: fetch + reset --hard FETCH_HEAD
    assert (spec.dest / ".git" / "shallow").is_file(), "the clone must still be shallow"

    said = impl.commits_behind(spec.dest, newer[1], release=True)
    assert said is git.Behind.AHEAD_OF_RELEASE, said


@pytest.mark.skipif(not git.git_available(), reason="needs a host git to make a real checkout")
def test_a_commit_of_the_users_on_top_of_the_release_is_ahead_of_it(tmp_path: Path) -> None:
    """This app's release install, then a commit of the user's own: past the release, not on it."""
    up = _Upstream(tmp_path)
    history = up.commits("c", 20)
    up.publish()
    dest = tmp_path / "mod-example"
    impl = git.RunnerGit()
    impl.clone(git.CloneSpec(url=up.url, dest=dest, rev=history[15]))
    (dest / "mine.txt").write_text("mine\n", encoding="utf-8")
    up.git("add", "-A", cwd=dest)
    up.git("commit", "-qm", "mine", cwd=dest)

    said = impl.commits_behind(dest, history[15], release=True)
    assert said is git.Behind.AHEAD_OF_RELEASE, said


@pytest.mark.skipif(not git.git_available(), reason="needs a host git to make a real checkout")
def test_a_release_install_behind_a_newer_release_is_still_offered_it(tmp_path: Path) -> None:
    """The ordinary T126 update must keep its chip, and it is the shape the proof cannot finish.

    This app installs a release by cloning the branch tip at depth 1 and then
    fetching the release at depth 1 (`_pin()`), so the old tip stays behind as
    a second graft. A newer release built on that tip is walked down to the
    tip's graft, which hides whether HEAD is under it -- exactly what hides it
    for a checkout that is AHEAD with a graft in the way. So it is `UNPLACED`:
    still offered, and not `UNCOUNTED`, which is proved under the release --
    `Applier.update()` asks GitHub before it resets from here. The one fixture
    that differs only in the release being straight on top of HEAD keeps its
    exact figure.
    """
    up = _Upstream(tmp_path)
    history = up.commits("c", 20)
    up.publish()
    dest = tmp_path / "mod-example"
    impl = git.RunnerGit()
    impl.clone(git.CloneSpec(url=up.url, dest=dest, rev=history[15]))
    assert impl.commits_behind(dest, history[15], release=True) == 0
    newer = up.commits("new", 3)
    up.publish()

    said = impl.commits_behind(dest, newer[-1], release=True)
    assert said is git.Behind.UNPLACED, said
    assert git.is_behind(said)

    straight = tmp_path / "mod-straight"
    impl.clone(git.CloneSpec(url=up.url, dest=straight, rev=newer[-1]))
    more = up.commits("more", 2)
    up.publish()
    assert impl.commits_behind(straight, more[-1], release=True) == 2


# ---------------------------------------------------------------------------
# T35: the clone says what it is doing while it does it.

GIT_PROGRESS = Path(__file__).parent / "fixtures" / "git-clone-progress.stderr"
"""The recording `test_runner.py` documents: a real `git clone --progress` stderr."""


@pytest.fixture
def streamed(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Serve the recorded clone through `runner.stream_progress`; record every argv."""
    calls: list[list[str]] = []
    fragments = [
        piece for piece in re.split(r"[\r\n]", GIT_PROGRESS.read_text(encoding="utf-8")) if piece
    ]

    def fake(argv: list[str], cwd: Path | None = None, env: object = None) -> Iterator[str]:
        # A GENERATOR, not `iter(list)`: the real `stream_progress()` returns
        # one and `_streamed_git()` closes it, which is how a clone abandoned
        # mid-stream ends its child. A list iterator has no `close()` and would
        # let that requirement pass unasserted.
        calls.append(argv)
        yield from fragments

    monkeypatch.setattr(runner, "stream_progress", fake)
    return calls


def _parsed(said: list[str]) -> list[lines.Parsed]:
    return [lines.parse(line) for line in said]


def test_clone_lines_turns_gits_progress_into_progress_lines(
    streamed: list[list[str]], seen: list[list[str]], tmp_path: Path
) -> None:
    """Every reading git printed reaches the panel as a `PROGRESS` line with its percent.

    The four phases named are the four git reports a percentage for. Everything
    else it says — `Cloning into 'repo'...`, `remote: Enumerating objects` —
    comes through as `TOOL`, because it is a subprocess talking and the panel
    dims it rather than dropping it.

    Mutation: yield the fragments as `TOOL` without asking for a percent, and
    `progress` below is empty while `tool` holds all 216 fragments — a strip
    that never moves and a panel back to a torrent.
    """
    dest = tmp_path / "core"
    said = list(git.RunnerGit().clone_lines(git.CloneSpec(url="https://x/y.git", dest=dest)))

    kinds = _parsed(said)
    progress = [one for one in kinds if one.kind == "progress"]
    receiving = [one.percent for one in progress if one.text.startswith("Receiving objects")]
    assert receiving[0] == 0 and receiving[-1] == 100
    assert len(receiving) > 50
    assert {one.stage for one in progress} == {"clone"}
    tool = [one for one in kinds if one.kind == "tool"]
    assert "Cloning into 'repo'..." in [one.text for one in tool]
    assert len(progress) + len(tool) == len(said), "a fragment came through as neither"


def test_clone_lines_asks_git_for_progress_it_would_not_print_to_a_pipe(
    streamed: list[list[str]], seen: list[list[str]], tmp_path: Path
) -> None:
    """`--progress`, or there is nothing to stream.

    Git suppresses its progress output when stderr is not a terminal, and a
    pipe never is. Measured while T35's fixture was recorded: the same clone
    without the flag wrote `Cloning into 'repo'...` and nothing else.

    Mutation: drop `--progress` from the argv and this fails while every other
    test here still passes, because the fixture is served regardless.
    """
    dest = tmp_path / "core"
    list(git.RunnerGit().clone_lines(git.CloneSpec(url="https://x/y.git", dest=dest)))
    assert streamed, "the clone did not go through stream_progress at all"
    assert "--progress" in streamed[0]
    assert "clone" in streamed[0]


def test_clone_lines_streams_the_update_of_a_checkout_that_is_already_there(
    streamed: list[list[str]], seen: list[list[str]], tmp_path: Path
) -> None:
    """A resumed install fetches, and a fetch has the same progress a clone does.

    Mutation: leave `_update()` on `runner.run` and a resume goes silent again
    for the whole fetch.
    """
    dest = tmp_path / "core"
    (dest / ".git").mkdir(parents=True)
    said = list(git.RunnerGit().clone_lines(git.CloneSpec(url="https://x/y.git", dest=dest)))
    assert streamed and "fetch" in streamed[0] and "--progress" in streamed[0]
    assert any(lines.parse(line).kind == "progress" for line in said)


def test_clone_lines_leaves_the_sparse_path_alone(
    streamed: list[list[str]], seen: list[list[str]], tmp_path: Path
) -> None:
    """The sparse clone builds its repository by hand and is not one command to stream.

    `_sparse_clone()` runs eight `git` calls of which one is a `pull`, and the
    checkout it produces is compared byte for byte against `ContainerGit`'s by
    `test_a_sparse_clone_checks_out_the_same_tree_either_way`. T35 does not
    touch it: the guide and keg repos are small and the silence is seconds.

    Mutation: route the sparse path through `stream_progress()` too and
    `streamed` is no longer empty, which this refuses.
    """
    dest = tmp_path / "guide"
    said = list(
        git.RunnerGit().clone_lines(
            git.CloneSpec(url="https://x/y.git", dest=dest, sparse_path="guides/x")
        )
    )
    assert streamed == [], "the sparse path was streamed"
    assert said == []
    assert any("sparse-checkout" in " ".join(argv) or "pull" in argv for argv in seen)


def test_a_seam_that_cannot_stream_still_clones_and_says_nothing(tmp_path: Path) -> None:
    """The fallback that kept every existing `Git` fake valid without an edit.

    Dozens of tests hand the install engine a clone seam that is one function
    of a `CloneSpec`. `git.clone_lines()` is what the engine calls, and against
    such a seam it does exactly what the engine used to do — clone, and yield
    nothing.

    Mutation: drop the fallback and every one of those tests fails with
    `AttributeError: 'function' object has no attribute 'clone_lines'`.
    """
    cloned: list[git.CloneSpec] = []
    spec = git.CloneSpec(url="https://x/y.git", dest=tmp_path / "core")

    assert list(git.clone_lines(cloned.append, spec)) == []
    assert cloned == [spec]


def test_a_seam_that_can_stream_is_streamed(tmp_path: Path) -> None:
    """And the same call against a real `Git` yields its progress.

    Mutation: always take the fallback and the clone stage is silent in
    production while every test still passes.
    """
    spec = git.CloneSpec(url="https://x/y.git", dest=tmp_path / "core")

    class _Streaming:
        def clone(self, spec: git.CloneSpec) -> None:
            raise AssertionError("the streaming path must not fall back")

        def clone_lines(self, spec: git.CloneSpec, *, stage: str = "clone") -> Iterator[str]:
            yield lines.PROGRESS + f"{stage} 42 Receiving objects"

    got = list(git.clone_lines(_Streaming().clone, spec, stage="clone-core"))
    assert got == [lines.PROGRESS + "clone-core 42 Receiving objects"]


def test_the_containerized_clone_streams_the_same_container_the_buffered_one_runs(
    streamed: list[list[str]],
    seen: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """`docker start -a` (T321; `docker run` before it) hands the container's stderr to the
    client's, so the same split works.

    What is asserted is that the STREAMED path runs the argv `_capture()`
    builds — same image, same mount, same working directory, same untrusted
    flags — with `--progress` added, and that git's readings come back as
    `PROGRESS` lines. The container is the one two hundred lines of
    `_capture()` justify, and a streamed clone through a different one would be
    a second security posture nobody reviewed.

    Mutation: build the argv here instead of calling `_argv()` and the mount
    assertion below fails; drop `--progress` and the percents go with it.
    """
    monkeypatch.setattr(git.platform, "docker_program", lambda: "docker")
    monkeypatch.setattr(git.platform, "selinux_enforcing", lambda: False)
    monkeypatch.setattr(git.platform, "filesystem_type", lambda _p: "ext4")
    dest = tmp_path / "core"
    said = list(git.ContainerGit().clone_lines(git.CloneSpec(url="https://x/y.git", dest=dest)))

    assert streamed, "the containerized clone did not reach stream_progress"
    # T321: the container is created with `_argv()`'s options, then streamed by its name.
    (argv,) = [call for call in seen if call[1:2] == ["create"]]
    assert argv[:3] == ["docker", "create", "--rm"]
    name = argv[argv.index("--name") + 1]
    assert streamed[0] == ["docker", "start", "-a", name]
    assert f"{dest}:/git" in argv and "-w" in argv and "/git" in argv
    assert git._CONTAINER_GIT_IMAGE in argv
    assert "--progress" in argv
    receiving = [
        parsed.percent
        for parsed in _parsed(said)
        if parsed.kind == "progress" and parsed.text.startswith("Receiving objects")
    ]
    assert receiving[0] == 0 and receiving[-1] == 100


def test_no_streamed_git_call_can_run_without_the_no_prompt_environment(
    monkeypatch: pytest.MonkeyPatch, seen: list[list[str]], tmp_path: Path
) -> None:
    """A credential prompt against a pipe is a clone that never ends.

    `_run_git()` has passed `_no_prompt_env()` since the beginning for this
    reason; the streamed path went without it until the 2026-09-12 review, and
    the streamed path is the one that runs for minutes. A headless harness has
    no terminal to type into and no Stop button, so a prompt there is a wait
    with no end at all.

    Driven through all three streamed routes rather than asserted of
    `_streamed_git()` alone — the host clone, the host update, and the
    containerized clone — because what has to hold is that no ROUTE reaches a
    child without the guard.

    Mutation: drop `env=_no_prompt_env()` from `_streamed_git()` and all three
    fail; pass the variables to `child_env()`'s caller wrongly (e.g. `env={}`)
    and they fail naming the missing variable.
    """
    monkeypatch.setattr(git.platform, "docker_program", lambda: "docker")
    monkeypatch.setattr(git.platform, "selinux_enforcing", lambda: False)
    monkeypatch.setattr(git.platform, "filesystem_type", lambda _p: "ext4")
    envs: list[dict[str, str] | None] = []

    def fake(argv: list[str], cwd: Path | None = None, env: object = None) -> Iterator[str]:
        envs.append(env)  # type: ignore[arg-type]
        yield "Receiving objects:  50% (1/2)"

    monkeypatch.setattr(runner, "stream_progress", fake)

    fresh = tmp_path / "fresh"
    existing = tmp_path / "existing"
    (existing / ".git").mkdir(parents=True)
    containerized = tmp_path / "containerized"
    list(git.RunnerGit().clone_lines(git.CloneSpec(url="https://x/y.git", dest=fresh)))
    list(git.RunnerGit().clone_lines(git.CloneSpec(url="https://x/y.git", dest=existing)))
    list(git.ContainerGit().clone_lines(git.CloneSpec(url="https://x/y.git", dest=containerized)))

    assert len(envs) == 3, "a streamed route did not reach a child"
    for env in envs:
        assert env is not None, "a streamed git inherited this process's environment"
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["GIT_ASKPASS"] == "" and env["SSH_ASKPASS"] == ""
        assert env["GCM_INTERACTIVE"] == "never"


# --------------------------------------------------------------------------
# head_version (T44 item 1: the sha and date a row shows)
# --------------------------------------------------------------------------


def test_head_version_reads_the_clone_and_never_fetches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One local `git log -1`, and nothing that touches the network (T44 item 1).

    The whole reason the version line is affordable at all is that it is a
    LOCAL read: `commits_behind()` costs a round trip per module and lives
    behind a button for that reason, and a version line that fetched would put
    that cost back on every reload.

    Mutation: add `--fetch`-shaped argv, or count against a remote ref, and the
    `not [a for a in argv if "fetch" in a]` assertion fails.
    """
    dest = tmp_path / "mod-aoe-loot"
    (dest / ".git").mkdir(parents=True)
    seen: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return _completed(stdout="7c02b1d 2026-09-01\n")

    monkeypatch.setattr(runner, "run", fake_run)

    assert git.RunnerGit().head_version(dest) == "7c02b1d · 2026-09-01"
    assert len(seen) == 1, "one command, not a fetch and a read"
    assert seen[0][-3:] == ["log", "-1", "--format=%h %cs"]
    assert not [arg for arg in seen[0] if "fetch" in arg]


def test_head_version_answers_nothing_rather_than_guessing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Four ways it cannot answer, and all four are silence (T44 item 1).

    A sha is the one thing on this row a user may paste into an issue, so an
    invented one is worse than a blank. And none of the four may turn a reload
    into a failure, which is why `OSError` -- a `git` that is not on PATH at
    all -- is caught here rather than at the caller.

    Mutation: return `raw.strip()` unconditionally and a git that printed a
    warning to stdout becomes a version; drop the `OSError` arm and a machine
    with no git raises out of `reload_modules()`.
    """
    dest = tmp_path / "mod-aoe-loot"
    (dest / ".git").mkdir(parents=True)
    impl = git.RunnerGit()

    assert impl.head_version(tmp_path / "not-a-checkout") is None, "no .git"

    answers: list[object] = []

    def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        assert isinstance(answer, subprocess.CompletedProcess)
        return answer

    monkeypatch.setattr(runner, "run", fake_run)

    answers.append(_completed(returncode=128, stderr="not a git repository"))
    assert impl.head_version(dest) is None, "git refused"

    answers.append(OSError("No such file or directory: 'git'"))
    assert impl.head_version(dest) is None, "no git on PATH"

    answers.append(_completed(stdout="\n"))
    assert impl.head_version(dest) is None, "git said nothing"

    answers.append(_completed(stdout="7c02b1d\n"))
    assert impl.head_version(dest) is None, "a sha with no date is not the line"


@pytest.mark.skipif(not git.git_available(), reason="needs a host git to make a real checkout")
def test_the_version_line_equals_what_git_log_prints_by_hand(tmp_path: Path) -> None:
    """The figure against real git, not against a mock (8.7a's own rule, applied here).

    A mock proves the argv; this proves the STRING a user reads is the sha and
    the date `git log -1` prints for the same checkout.

    Mutation: use `%H` instead of `%h` and the line stops matching the short
    sha this asserts against; use `%cd` instead of `%cs` and the date arrives
    in git's long default format.
    """
    dest = tmp_path / "clone"
    dest.mkdir()
    for argv in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@example.invalid"],
        ["git", "config", "user.name", "T"],
        ["git", "commit", "-q", "--allow-empty", "-m", "one"],
    ):
        subprocess.run(argv, cwd=dest, check=True, capture_output=True, text=True)
    by_hand = subprocess.run(
        ["git", "log", "-1", "--format=%h %cs"],
        cwd=dest,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()

    assert git.RunnerGit().head_version(dest) == f"{by_hand[0]} · {by_hand[1]}"


# -- T179: a checkout that leaves folders out (`CloneSpec.sparse_exclude`) ---------------

_EXCLUDED = ("playerbot reference", "centurion/launcher")


def _centurion_like_upstream(tmp_path: Path) -> tuple[str, str, str]:
    """A bare `file://` upstream on branch CENTURION with two commits; (url, first, second).

    `uploadpack.allowFilter` because GitHub serves `--filter=blob:none` and a local
    upstream does not unless told to -- without it the clone would quietly be a full
    one and the "never downloaded" half of the test would prove nothing.
    """
    work = tmp_path / "work"
    work.mkdir()

    def git_(*argv: str, cwd: Path = work) -> str:
        done = subprocess.run(
            ["git", *_AUTHOR, *argv], cwd=cwd, check=True, capture_output=True, text=True
        )
        return done.stdout.strip()

    git_("init", "-q", "-b", "CENTURION", ".")
    for rel in (
        "playerbot reference/PlayerbotAI.cpp",
        "centurion/launcher/main.js",
        "centurion/dbc/Spell.dbc",
        "centurion/sql/import.sh",
        "src/server/worldserver/Main.cpp",
        "README.md",
    ):
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_text(f"{rel}\n", encoding="utf-8")
    git_("add", "-A")
    git_("commit", "-qm", "one")
    first = git_("rev-parse", "HEAD")
    (work / "src" / "later.cpp").write_text("later\n", encoding="utf-8")
    git_("add", "-A")
    git_("commit", "-qm", "two")
    second = git_("rev-parse", "HEAD")
    bare = tmp_path / "up.git"
    git_("init", "-q", "--bare", "-b", "CENTURION", str(bare))
    git_("push", "-q", str(bare), "CENTURION")
    git_("config", "uploadpack.allowFilter", "true", cwd=bare)
    git_("config", "uploadpack.allowAnySHA1InWant", "true", cwd=bare)
    return bare.as_uri(), first, second


def _tree(dest: Path) -> list[str]:
    return sorted(
        path.relative_to(dest).as_posix()
        for path in dest.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(dest).parts
    )


def _missing_blobs(dest: Path) -> set[str]:
    """Paths whose blob the clone does not hold, asked without fetching them (`--missing=print`)."""
    listed = subprocess.run(
        ["git", "rev-list", "--objects", "--missing=print", "HEAD"],
        cwd=dest,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    missing = {line[1:] for line in listed if line.startswith("?")}
    tree = subprocess.run(
        ["git", "ls-tree", "-r", "HEAD"], cwd=dest, check=True, capture_output=True, text=True
    ).stdout.splitlines()
    return {line.split("\t", 1)[1] for line in tree if line.split()[2] in missing}


_KEPT_AT_FIRST = [
    "README.md",
    "centurion/dbc/Spell.dbc",
    "centurion/sql/import.sh",
    "src/server/worldserver/Main.cpp",
]


@pytest.mark.skipif(not git.git_available(), reason="needs a host git to make a real checkout")
@pytest.mark.parametrize("streamed", [False, True], ids=["clone", "clone_lines"])
@pytest.mark.parametrize("name", ["host", "container"])
@pytest.mark.parametrize("pinned", [False, True], ids=["tip", "pin"])
def test_a_sparse_exclude_clone_leaves_the_folders_out_and_never_downloads_them(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str, streamed: bool, pinned: bool
) -> None:
    url, first, second = _centurion_like_upstream(tmp_path)
    spec = git.CloneSpec(
        url=url,
        dest=tmp_path / "srv" / "src" / "centurion",
        branch="CENTURION",
        rev=first if pinned else None,
        sparse_exclude=_EXCLUDED,
    )
    impl = _impl(name, monkeypatch)
    if streamed:
        list(impl.clone_lines(spec))
    else:
        impl.clone(spec)
    expected = _KEPT_AT_FIRST if pinned else sorted([*_KEPT_AT_FIRST, "src/later.cpp"])
    assert _tree(spec.dest) == expected
    assert _rev(spec.dest, "HEAD") == (first if pinned else second)
    assert _missing_blobs(spec.dest) == {
        "playerbot reference/PlayerbotAI.cpp",
        "centurion/launcher/main.js",
    }, "the left-out files were downloaded"


@pytest.mark.skipif(not git.git_available(), reason="needs a host git to make a real checkout")
def test_an_update_of_a_sparse_exclude_checkout_keeps_leaving_them_out(tmp_path: Path) -> None:
    url, first, second = _centurion_like_upstream(tmp_path)
    spec = git.CloneSpec(
        url=url, dest=tmp_path / "core", branch="CENTURION", rev=first, sparse_exclude=_EXCLUDED
    )
    git.RunnerGit().clone(spec)
    git.RunnerGit().clone(dataclasses.replace(spec, rev=second))
    assert _tree(spec.dest) == sorted([*_KEPT_AT_FIRST, "src/later.cpp"])


def test_the_patterns_keep_everything_and_drop_each_folder_anchored() -> None:
    assert git.sparse_exclude_patterns(("playerbot reference", "centurion/launcher/")) == [
        "/*",
        "!/playerbot reference/",
        "!/centurion/launcher/",
    ]


def test_a_spec_cannot_keep_one_folder_and_leave_others_out(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not both"):
        git.CloneSpec(
            url="https://x/y.git", dest=tmp_path, sparse_path="guides", sparse_exclude=("a",)
        )


def test_the_container_clone_sets_the_patterns_non_cone_before_any_checkout(
    seen: list[list[str]], tmp_path: Path
) -> None:
    """Cone mode keeps folders and cannot say "all but"; the checkout must follow the patterns."""
    spec = git.CloneSpec(
        url="https://example/c.git",
        dest=tmp_path / "core",
        branch="CENTURION",
        sparse_exclude=_EXCLUDED,
    )
    git.ContainerGit(selinux_enforcing=lambda: False, filesystem_type=lambda path: "ext4").clone(
        spec
    )
    clone = next(argv for argv in seen if "clone" in argv)
    assert "--filter=blob:none" in clone and "--no-checkout" in clone
    assert clone[clone.index("--branch") + 1] == "CENTURION"
    sparse = next(i for i, argv in enumerate(seen) if "sparse-checkout" in argv)
    assert seen[sparse][-5:] == [
        "set",
        "--no-cone",
        "/*",
        "!/playerbot reference/",
        "!/centurion/launcher/",
    ]
    checkout = next(i for i, argv in enumerate(seen) if argv[-1] == "checkout")
    assert sparse < checkout


# -- T179: the diff questions' argv and parse ------------------------------------------


def test_the_diff_questions_ask_git_for_no_renames_and_no_repository_programs(
    seen: list[list[str]], tmp_path: Path
) -> None:
    dest = tmp_path / "core"
    (dest / ".git").mkdir(parents=True)
    reader = git.ContainerGit(selinux_enforcing=lambda: False, filesystem_type=lambda _p: "ext4")
    reader.changed_files(dest, "a" * 40, "b" * 40, ["centurion/sql", "centurion/dbc"])
    tail = seen[-1][seen[-1].index("diff") :]
    assert tail == [
        "diff",
        "--no-renames",
        "--no-ext-diff",
        "--no-textconv",
        "--no-color",
        "--name-status",
        "-z",
        "a" * 40,
        "b" * 40,
        "--",
        "centurion/sql",
        "centurion/dbc",
    ]


def test_the_name_status_parse_reads_pairs_and_refuses_what_it_cannot_read() -> None:
    assert git.parse_changed_files("M\0a b.sql\0A\0c.sql\0D\0d.sql\0") == (
        ("M", "a b.sql"),
        ("A", "c.sql"),
        ("D", "d.sql"),
    )
    assert git.parse_changed_files("") == ()
    assert git.parse_changed_files("M\0a.sql\0A\0") is None, "an odd count is not an answer"


def test_the_changed_lines_parse_keeps_hunk_lines_only() -> None:
    raw = (
        "diff --git a/x.sql b/x.sql\n"
        "index 1..2 100644\n"
        "--- a/x.sql\n"
        "+++ b/x.sql\n"
        "@@ -1 +1 @@\n"
        "--- a removed comment\n"
        "+INSERT INTO `realmlist` VALUES (2);\n"
        "\\ No newline at end of file\n"
    )
    assert git.parse_changed_lines(raw) == (
        "--- a removed comment",
        "+INSERT INTO `realmlist` VALUES (2);",
    )
    assert git.parse_changed_lines("Binary files a/x and b/x differ\n") == ()


# -- T240: a Stop mid-clone ends the clone's container and tries nothing else --------


@pytest.fixture
def fake_docker(tmp_path: Path) -> Iterator[tuple[Path, Path]]:
    """`support_fake_docker`'s CLI and state folder; every container left is ended after."""
    cli, state = lay_fake_docker(tmp_path)
    yield cli, state
    end_fake_containers(state)


def _container_git(monkeypatch: pytest.MonkeyPatch, cli: Path) -> git.ContainerGit:
    """The real `ContainerGit` on the fake CLI, with host git there to fall back to."""
    monkeypatch.setattr(git.platform, "docker_program", lambda: str(cli))
    monkeypatch.setattr(git, "git_available", lambda: True)
    return git.ContainerGit(selinux_enforcing=lambda: False, filesystem_type=lambda _p: "ext4")


CLONE_SHAPES = {
    "fresh": lambda dest: git.CloneSpec(url="https://x/y.git", dest=dest),
    "update": lambda dest: git.CloneSpec(url="https://x/y.git", dest=dest),
    "sparse-exclude": lambda dest: git.CloneSpec(
        url="https://x/y.git", dest=dest, sparse_exclude=("docs",)
    ),
}
"""The three containerized clones that fall back to host git when they fail."""


@pytest.mark.parametrize("shape", sorted(CLONE_SHAPES))
def test_a_stopped_containerized_clone_ends_its_container_and_never_falls_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_docker: tuple[Path, Path], shape: str
) -> None:
    """T240, at each of `clone_lines()`'s three fallbacks: a Stop is not a failed clone.

    The Stop is the one the log panel sends, `runner.end_streams_started_on()`
    for the thread reading the clone, and it lands while the container clones.
    """
    cli, state = fake_docker
    container_git = _container_git(monkeypatch, cli)
    host: list[git.CloneSpec] = []

    def host_clone(
        self: git.RunnerGit, spec: git.CloneSpec, *, stage: str = "clone"
    ) -> Iterator[str]:
        host.append(spec)
        yield "host git cloned it"

    monkeypatch.setattr(git.RunnerGit, "clone_lines", host_clone)
    settle = _Settle()
    monkeypatch.setattr(container_end, "time", settle)
    dest = tmp_path / "core"
    if shape == "update":
        (dest / ".git").mkdir(parents=True)
    spec = CLONE_SHAPES[shape](dest)
    outcome: list[BaseException | None] = []

    def clone() -> None:
        try:
            list(container_git.clone_lines(spec))
        except BaseException as exc:  # noqa: BLE001 - the outcome is what is asserted
            outcome.append(exc)
        else:
            outcome.append(None)

    worker = threading.Thread(target=clone)
    worker.start()
    deadline = time.monotonic() + HANG_BOUND
    while not fake_running(state) and time.monotonic() < deadline:
        time.sleep(0.01)
    (started,) = fake_containers(state)
    assert worker.ident is not None
    assert runner.end_streams_started_on(worker.ident) == 1
    worker.join(HANG_BOUND)

    assert not worker.is_alive(), "the stopped clone did not end"
    assert len(outcome) == 1 and isinstance(outcome[0], git.GitStopped), outcome
    assert fake_containers(state) == [], "the clone's container is still running"
    assert [call for call in fake_calls(state) if call.startswith("rm ")] == [f"rm -f {started}"]
    assert host == [], "a stopped clone was cloned again with host git"
    # T321: created under its name, then attached to; so "gone" needs no second look.
    docker_verbs = [call.split()[0] for call in fake_calls(state)]
    assert docker_verbs == ["create", "start", "rm"], docker_verbs
    assert settle.slept == [], "a created container was asked about again"


def test_a_stop_during_the_clones_create_ends_it_before_anything_starts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_docker: tuple[Path, Path]
) -> None:
    """T321: the panel's Stop lands while `docker create` runs, when no stream is live to end.

    In an install the panel drains rather than closing the job, and nothing in the clone
    reads the install's cancel, so a Stop nobody heard would let the whole clone run.
    The clone asks whether its thread was sent a Stop once the create returns, removes the
    container it made, and starts nothing; a Stop is never answered with host git.
    """
    cli, state = fake_docker
    (state / "slow-create").write_text("", encoding="utf-8")
    settle = _Settle()
    monkeypatch.setattr(container_end, "time", settle)
    container_git = _container_git(monkeypatch, cli)
    host: list[git.CloneSpec] = []

    def host_clone(
        self: git.RunnerGit, spec: git.CloneSpec, *, stage: str = "clone"
    ) -> Iterator[str]:
        host.append(spec)
        yield "host git cloned it"

    monkeypatch.setattr(git.RunnerGit, "clone_lines", host_clone)
    outcome: list[BaseException | None] = []

    def clone() -> None:
        try:
            list(
                container_git.clone_lines(git.CloneSpec(url="https://x/y.git", dest=tmp_path / "c"))
            )
        except BaseException as exc:  # noqa: BLE001 - the outcome is what is asserted
            outcome.append(exc)
        else:
            outcome.append(None)

    worker = threading.Thread(target=clone)
    worker.start()
    deadline = time.monotonic() + HANG_BOUND
    while not (state / "create-asked").exists():
        assert time.monotonic() < deadline, "the create was never asked"
        time.sleep(0.01)
    assert worker.ident is not None
    assert runner.end_streams_started_on(worker.ident) == 0, "the ground: no stream is live"
    (state / "slow-create").unlink()
    worker.join(HANG_BOUND)

    assert not worker.is_alive(), "the stopped clone did not end"
    assert len(outcome) == 1 and isinstance(outcome[0], git.GitStopped), outcome
    assert host == [], "a stopped clone was cloned again with host git"
    verbs = [call.split()[0] for call in fake_calls(state)]
    assert verbs == ["create", "rm"], verbs
    assert fake_containers(state) == [], "the created container is still there"
    assert settle.slept == []


def test_a_stop_during_a_clone_create_that_then_fails_is_a_stop_not_a_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_docker: tuple[Path, Path]
) -> None:
    """T321 cold review: the create fails (or times out) after the Stop was sent. A failed
    containerized clone falls back to host git, which in an install would run to its end
    with nobody hearing the Stop; a stopped one must not."""
    cli, state = fake_docker
    (state / "slow-create").write_text("", encoding="utf-8")
    (state / "create-refused").write_text("", encoding="utf-8")
    container_git = _container_git(monkeypatch, cli)
    host: list[git.CloneSpec] = []

    def host_clone(
        self: git.RunnerGit, spec: git.CloneSpec, *, stage: str = "clone"
    ) -> Iterator[str]:
        host.append(spec)
        yield "host git cloned it"

    monkeypatch.setattr(git.RunnerGit, "clone_lines", host_clone)
    outcome: list[BaseException | None] = []

    def clone() -> None:
        try:
            list(
                container_git.clone_lines(git.CloneSpec(url="https://x/y.git", dest=tmp_path / "c"))
            )
        except BaseException as exc:  # noqa: BLE001 - the outcome is what is asserted
            outcome.append(exc)
        else:
            outcome.append(None)

    worker = threading.Thread(target=clone)
    worker.start()
    deadline = time.monotonic() + HANG_BOUND
    while not (state / "create-asked").exists():
        assert time.monotonic() < deadline, "the create was never asked"
        time.sleep(0.01)
    assert worker.ident is not None
    runner.end_streams_started_on(worker.ident)
    (state / "slow-create").unlink()
    worker.join(HANG_BOUND)

    assert not worker.is_alive(), "the stopped clone did not end"
    assert len(outcome) == 1 and isinstance(outcome[0], git.GitStopped), outcome
    assert host == [], "a stopped clone was cloned again with host git"


def test_a_stop_just_before_the_clones_stream_starts_is_not_lost(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_docker: tuple[Path, Path]
) -> None:
    """T321: the Stop lands after the create returned, before `start -a` has a process.

    `end_streams_started_on()` ends only a child that exists, so this Stop ends nothing,
    and in an install the panel drains rather than closing the clone. The clone reads
    its thread's Stop count on the stream's lines and stops on the first.
    """
    cli, state = fake_docker
    container_git = _container_git(monkeypatch, cli)
    real = runner.stream_progress

    def stop_first(argv: list[str], **kwargs: Any) -> Iterator[str]:
        # The panel's Stop, sent while no child of this thread is running.
        assert runner.end_streams_started_on(threading.get_ident()) == 0
        return real(argv, **kwargs)

    monkeypatch.setattr(runner, "stream_progress", stop_first)
    clone = container_git.clone_lines(git.CloneSpec(url="https://x/y.git", dest=tmp_path / "c"))
    with pytest.raises(git.GitStopped):
        list(clone)

    (create,) = [c.split() for c in fake_calls(state) if c.startswith("create ")]
    name = create[create.index("--name") + 1]
    assert fake_containers(state) == [], "the clone's container is still there"
    assert [c for c in fake_calls(state) if c.startswith("rm ")] == [f"rm -f {name}"]


def test_a_stopped_clone_whose_container_will_not_go_says_so_in_the_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_docker: tuple[Path, Path]
) -> None:
    """T240, Codex adversarial review: a refused `rm -f` must not pass for a clean Stop.

    The container may still be cloning into the folder, so the run says which
    container it is and how to remove it, in a line of its own, before the Stop
    ends the run as a Stop.
    """
    cli, state = fake_docker
    (state / "refuse-rm").write_text("", encoding="utf-8")
    container_git = _container_git(monkeypatch, cli)
    dest = tmp_path / "core"
    said: list[str] = []
    outcome: list[BaseException] = []

    def clone() -> None:
        try:
            for line in container_git.clone_lines(git.CloneSpec(url="https://x/y.git", dest=dest)):
                said.append(line)
        except BaseException as exc:  # noqa: BLE001 - the outcome is what is asserted
            outcome.append(exc)

    worker = threading.Thread(target=clone)
    worker.start()
    deadline = time.monotonic() + HANG_BOUND
    while not fake_running(state) and time.monotonic() < deadline:
        time.sleep(0.01)
    (started,) = fake_containers(state)
    assert worker.ident is not None
    runner.end_streams_started_on(worker.ident)
    worker.join(HANG_BOUND)

    assert len(outcome) == 1 and isinstance(outcome[0], git.GitStopped), outcome
    assert fake_containers(state) == [started], "the ground: the daemon refused the removal"
    assert said[-1] == git.container_left_line(
        started, dest, "Error response from daemon: the daemon is shutting down"
    ), said[-1]
    assert f"docker rm -f {started}" in said[-1]


def test_an_abandoned_containerized_clone_ends_its_container_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_docker: tuple[Path, Path]
) -> None:
    """T240: a reader that drops the clone part-way (`close()`) ends the container as well.

    Closing ends the CLI (`runner.stream_progress()`'s own teardown), and the
    container would otherwise go on exactly as after a Stop.
    """
    cli, state = fake_docker
    settle = _Settle()
    monkeypatch.setattr(container_end, "time", settle)
    container_git = _container_git(monkeypatch, cli)
    clone = container_git.clone_lines(git.CloneSpec(url="https://x/y.git", dest=tmp_path / "core"))
    assert "Receiving objects" in next(line for line in clone if "Receiving" in line)
    (started,) = fake_containers(state)
    clone.close()  # type: ignore[attr-defined]

    assert fake_containers(state) == []
    assert [call for call in fake_calls(state) if call.startswith("rm ")] == [f"rm -f {started}"]
    assert settle.slept == [], "T321: a created container is not asked about again"


def test_a_clone_that_fails_on_its_own_still_falls_back_and_ends_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The other half of T240: a containerized clone that FAILED is still answered with host git.

    The fallback exists for a host whose containerized git cannot work, and a
    Stop is the one failure it must not answer. One `rm -f` before it (Codex
    review round 3): `--rm` removed a container that ran, but one the daemon
    would not start stays created, and the failure cannot tell which it was.
    """
    container_git = _container_git(monkeypatch, Path("docker"))
    ran: list[list[str]] = []

    def fails(argv: list[str], cwd: Path | None = None, env: object = None) -> Iterator[str]:
        ran.append(argv)
        yield "fatal: unable to access 'https://x/y.git/': Could not resolve host: x"
        raise subprocess.CalledProcessError(128, argv)

    monkeypatch.setattr(runner, "stream_progress", fails)
    created: list[list[str]] = []

    def create(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        created.append(argv)
        return subprocess.CompletedProcess(argv, 0, "0123456789ab\n", "")

    monkeypatch.setattr(runner, "run", create)
    host: list[git.CloneSpec] = []

    def host_clone(
        self: git.RunnerGit, spec: git.CloneSpec, *, stage: str = "clone"
    ) -> Iterator[str]:
        host.append(spec)
        yield "host git cloned it"

    monkeypatch.setattr(git.RunnerGit, "clone_lines", host_clone)
    said = list(
        container_git.clone_lines(git.CloneSpec(url="https://x/y.git", dest=tmp_path / "core"))
    )
    assert len(host) == 1 and said[-1] == "host git cloned it"
    # T321: the streamed clone is created under its name, then attached to by that name.
    assert created[0][1:2] == ["create"] and "--name" in created[0]
    name = created[0][created[0].index("--name") + 1]
    assert ran == [["docker", "start", "-a", name]], ran
    assert created[1:] == [["docker", "rm", "-f", name]], created


def _stop_mid_clone(
    container_git: git.ContainerGit,
    spec: git.CloneSpec,
    state: Path,
    *,
    container_first: bool = False,
) -> tuple[list[str], list[BaseException]]:
    """Clone on a worker thread and send the panel's Stop once the docker CLI is running.

    `container_first` holds the Stop until the fake container exists. Without
    it the Stop can land before the CLI has made its container, and a test of
    what `rm -f` answers about a container then asks about none (T305).
    """
    said: list[str] = []
    outcome: list[BaseException] = []

    def clone() -> None:
        try:
            said.extend(container_git.clone_lines(spec))
        except BaseException as exc:  # noqa: BLE001 - the outcome is what is asserted
            outcome.append(exc)

    worker = threading.Thread(target=clone)
    worker.start()
    deadline = time.monotonic() + HANG_BOUND
    while not any(call.startswith("start -a ") for call in fake_calls(state)):
        assert time.monotonic() < deadline, "the docker CLI never started"
        time.sleep(0.01)
    while container_first and not fake_running(state):
        assert time.monotonic() < deadline, "the clone's container never started"
        time.sleep(0.01)
    assert worker.ident is not None
    while runner.end_streams_started_on(worker.ident) == 0:
        assert time.monotonic() < deadline, "the clone's stream never went live"
        time.sleep(0.01)
    worker.join(HANG_BOUND)
    assert not worker.is_alive(), "the stopped clone did not end"
    return said, outcome


class _Settle:
    """`container_end.time` for a stopped clone: the wait before the second `rm -f`, unslept.

    `container_end.end_container()` sleeps `LATE_CREATE_SETTLE` between its two
    looks. A real second against a fake daemon that had to act inside it is what
    flaked under xdist (T305), so the wait is recorded instead, and `during` is
    what happens while it lasts. Any other `time` attribute is the real one.
    """

    def __init__(self, during: Callable[[], None] = lambda: None) -> None:
        self.slept: list[float] = []
        self._during = during

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self._during()

    def __getattr__(self, name: str) -> Any:
        return getattr(time, name)


def test_a_clone_create_that_timed_out_is_looked_for_twice_and_its_late_container_removed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_docker: tuple[Path, Path],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """T240 cold review's second look, kept by T321 for the one late create left.

    A `docker create` that does not answer in time is ended by its timeout, and the
    daemon may still make the container after the first `rm -f` found nothing. So a
    "gone" is asked again once, after a settle, and the log says what happened.
    Nothing was started, so the clone falls back to host git as any failed one does.
    """
    cli, state = fake_docker
    (state / "late-create").write_text("", encoding="utf-8")
    monkeypatch.setattr(container_end, "CREATE_TIMEOUT", 0.5)
    late: list[str] = []

    def the_daemon_finishes_the_create() -> None:
        # The ground, at the moment it is laid: the first look found nothing.
        assert len([call for call in fake_calls(state) if call.startswith("rm -f ")]) == 1
        assert fake_containers(state) == []
        late.append(finish_late_create(state))

    settle = _Settle(during=the_daemon_finishes_the_create)
    monkeypatch.setattr(container_end, "time", settle)
    container_git = _container_git(monkeypatch, cli)
    host: list[git.CloneSpec] = []

    def host_clone(
        self: git.RunnerGit, spec: git.CloneSpec, *, stage: str = "clone"
    ) -> Iterator[str]:
        host.append(spec)
        yield "host git cloned it"

    monkeypatch.setattr(git.RunnerGit, "clone_lines", host_clone)
    caplog.set_level("INFO", logger="yulon.container_end")
    said = list(
        container_git.clone_lines(git.CloneSpec(url="https://x/y.git", dest=tmp_path / "core"))
    )

    assert said[-1] == "host git cloned it" and len(host) == 1, said
    assert settle.slept == [container_end.LATE_CREATE_SETTLE], settle.slept
    removals = [call for call in fake_calls(state) if call.startswith("rm -f ")]
    assert removals == [f"rm -f {late[0]}"] * 2, removals
    assert not [call for call in fake_calls(state) if call.startswith("start ")], "started"
    assert fake_containers(state) == [], "the late container is still there"
    logged = [r.getMessage() for r in caplog.records if "clone container" in r.getMessage()]
    assert any("created after the Stop" in message for message in logged), logged


def test_a_clone_create_docker_refused_falls_back_and_starts_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_docker: tuple[Path, Path]
) -> None:
    """T321: a create Docker refused is a failed containerized clone, in Docker's words."""
    cli, state = fake_docker
    (state / "create-refused").write_text("", encoding="utf-8")
    container_git = _container_git(monkeypatch, cli)
    failed: list[str] = []

    def host_clone(
        self: git.RunnerGit, spec: git.CloneSpec, *, stage: str = "clone"
    ) -> Iterator[str]:
        yield "host git cloned it"

    monkeypatch.setattr(git.RunnerGit, "clone_lines", host_clone)
    monkeypatch.setattr(git.logger, "warning", lambda message: failed.append(message))
    said = list(
        container_git.clone_lines(git.CloneSpec(url="https://x/y.git", dest=tmp_path / "core"))
    )

    assert said == ["host git cloned it"]
    assert [call.split()[0] for call in fake_calls(state)] == ["create"]
    assert any("pull access denied" in message for message in failed), failed


def test_a_removal_already_in_progress_is_a_container_going_not_a_refusal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_docker: tuple[Path, Path],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """T240 cold review: Moby's answer when `--rm` is already removing the container.

    It is the container going, so nothing is said to the player about one left behind.
    """
    cli, state = fake_docker
    (state / "rm-in-progress").write_text("", encoding="utf-8")
    settle = _Settle()
    monkeypatch.setattr(container_end, "time", settle)
    container_git = _container_git(monkeypatch, cli)
    caplog.set_level("INFO", logger="yulon.git")
    said, outcome = _stop_mid_clone(
        container_git,
        git.CloneSpec(url="https://x/y.git", dest=tmp_path / "core"),
        state,
        container_first=True,
    )

    assert len(outcome) == 1 and isinstance(outcome[0], git.GitStopped), outcome
    assert fake_containers(state) == []
    # T321: it was created before it started, so "going" is the end of it.
    assert settle.slept == [], "a created container's removal in progress was asked again"
    assert not [line for line in said if "could not be removed" in line], said
    logged = [r.getMessage() for r in caplog.records if "clone container" in r.getMessage()]
    assert any("was already gone" in message for message in logged), logged


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "answer"),
    [
        (0, "yulon-git-0\n", "", "removed"),
        (0, "", "", "gone"),
        (1, "", "Error response from daemon: No such container: yulon-git-0", "gone"),
        (
            1,
            "",
            "Error response from daemon: removal of container yulon-git-0 is already in progress",
            "gone",
        ),
        (1, "", "Error response from daemon: the daemon is shutting down", "refused"),
    ],
)
def test_each_answer_of_docker_rm_is_read_for_what_it_says_about_the_container(
    monkeypatch: pytest.MonkeyPatch, returncode: int, stdout: str, stderr: str, answer: str
) -> None:
    """T240: removed, gone (three spellings), or refused. An exit 0 that names nothing is gone."""

    def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    monkeypatch.setattr(runner, "run", run)
    said = container_end.remove_once(["fake-docker"], "yulon-git-0")
    expected = {"removed": container_end.REMOVED, "gone": container_end.GONE}.get(answer, stderr)
    assert said == expected


@pytest.mark.parametrize("stopped", [False, True], ids=("no-stop", "stopped-during-create"))
def test_a_timed_out_clone_create_whose_late_container_will_not_go_is_said_not_cloned_again(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_docker: tuple[Path, Path],
    stopped: bool,
) -> None:
    """Codex review of the stop-paths branch (both reviews, round 2): `create()` hands back
    why a timed-out create's late container could not be removed, and it was dropped. The
    container may still clone into the folder: its name and the command are said, and host
    git is never started under it -- with or without a Stop sent during the create."""
    cli, state = fake_docker
    (state / "late-create").write_text("", encoding="utf-8")
    monkeypatch.setattr(container_end, "CREATE_TIMEOUT", 0.5)
    late: list[str] = []

    def the_daemon_makes_it_and_will_not_remove_it() -> None:
        late.append(finish_late_create(state))
        (state / "refuse-rm").write_text("", encoding="utf-8")
        if stopped:
            # The panel's Stop, sent while the create had not answered.
            runner.end_streams_started_on(threading.get_ident())

    monkeypatch.setattr(
        container_end, "time", _Settle(during=the_daemon_makes_it_and_will_not_remove_it)
    )
    container_git = _container_git(monkeypatch, cli)
    host: list[git.CloneSpec] = []

    def host_clone(
        self: git.RunnerGit, spec: git.CloneSpec, *, stage: str = "clone"
    ) -> Iterator[str]:
        host.append(spec)
        yield "host git cloned it"

    monkeypatch.setattr(git.RunnerGit, "clone_lines", host_clone)
    dest = tmp_path / "core"
    said: list[str] = []

    with pytest.raises(git.GitError) as failed:
        for line in container_git.clone_lines(git.CloneSpec(url="https://x/y.git", dest=dest)):
            said.append(line)

    assert host == [], "host git was started under a container that may still be cloning"
    assert isinstance(failed.value, git.GitStopped) is stopped, failed.value
    assert fake_containers(state) == late, "the ground: the daemon kept it"
    assert said and said[-1].startswith(f"The clone's container {late[0]} could not be removed")
    assert f"docker rm -f {late[0]}" in said[-1], said[-1]
    assert ("after Stop" in said[-1]) is stopped, said[-1]
    if not stopped:
        # Cold review: a create that timed out is a daemon that hangs; `rm -f` hung too.
        assert "restart Docker" in str(failed.value), failed.value
    assert not [call for call in fake_calls(state) if call.startswith("start ")], "started"


def test_a_clone_docker_would_not_start_has_its_container_removed_before_host_git(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_docker: tuple[Path, Path]
) -> None:
    """Codex review round 3: a container the daemon would not start is never `--rm`-removed;
    the failed clone removes it, and only then answers with host git."""
    cli, state = fake_docker
    (state / "start-refused").write_text("", encoding="utf-8")
    container_git = _container_git(monkeypatch, cli)
    seen_at_host_git: list[list[str]] = []

    def host_clone(
        self: git.RunnerGit, spec: git.CloneSpec, *, stage: str = "clone"
    ) -> Iterator[str]:
        seen_at_host_git.append(fake_containers(state))
        yield "host git cloned it"

    monkeypatch.setattr(git.RunnerGit, "clone_lines", host_clone)
    said = list(
        container_git.clone_lines(git.CloneSpec(url="https://x/y.git", dest=tmp_path / "core"))
    )

    assert said[-1] == "host git cloned it"
    assert seen_at_host_git == [[]], "host git ran beside the created container"
    assert fake_containers(state) == []


def test_a_clone_docker_would_not_start_or_remove_is_said_and_not_cloned_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_docker: tuple[Path, Path]
) -> None:
    cli, state = fake_docker
    (state / "start-refused").write_text("", encoding="utf-8")
    (state / "refuse-rm").write_text("", encoding="utf-8")
    container_git = _container_git(monkeypatch, cli)
    host: list[git.CloneSpec] = []

    def host_clone(
        self: git.RunnerGit, spec: git.CloneSpec, *, stage: str = "clone"
    ) -> Iterator[str]:
        host.append(spec)
        yield "host git cloned it"

    monkeypatch.setattr(git.RunnerGit, "clone_lines", host_clone)
    said: list[str] = []

    with pytest.raises(git.GitContainerLeft):
        for line in container_git.clone_lines(
            git.CloneSpec(url="https://x/y.git", dest=tmp_path / "core")
        ):
            said.append(line)

    (name,) = fake_containers(state)
    assert host == []
    assert said[-1].startswith(f"The clone's container {name} could not be removed"), said
    assert "after Stop" not in said[-1], "nobody pressed Stop (cold review)"


def test_a_clone_whose_container_was_left_is_not_retried_for_the_mount_race(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The race retry reads the message, and a left container's message carries git's
    words: a second container must not start over one that could not be removed."""
    container_git = git.ContainerGit(
        selinux_enforcing=lambda: False, filesystem_type=lambda _p: "ext4"
    )
    calls: list[Path] = []

    def left(
        self: git.ContainerGit, dest: Path, git_args: list[str], *, stage: str
    ) -> Iterator[str]:
        calls.append(dest)
        raise git.GitContainerLeft(
            "Cloning into '.'... fatal: No such file or directory; its container "
            "yulon-git-0123456789ab could not be removed"
        )
        yield ""  # pragma: no cover - a generator, as the real one is

    monkeypatch.setattr(git.ContainerGit, "_streamed_capture", left)
    spec = git.CloneSpec(url="https://x/y.git", dest=tmp_path / "core")

    with pytest.raises(git.GitContainerLeft):
        list(container_git._streamed_clone_with_mount_race_retry(spec, ["clone"], stage="clone"))

    assert calls == [spec.dest], "retried over a container that could not be removed"
