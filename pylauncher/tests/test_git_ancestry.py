"""T632: does a move go forward, in a SHALLOW checkout? (re-review of df3a9f49)

Every source but AzerothCore's core is a depth-1 clone: the tip is fetched at depth 1, then
the pin at depth 1, so both are grafts in `.git/shallow`, and later fetches connect new
commits back only to what is held. `merge-base --is-ancestor` answers 1 for a REAL forward
move through a graft; the parent ids in the commit objects survive grafting.
"""

from __future__ import annotations

import subprocess
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


def _no_traversal(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Make any git that WALKS history fail, as git 2.34 does at a graft with the grafts off.

    The fixture tests then pass only for a reader that takes the commit objects as they are
    (`cat-file`), whatever version of git the host has.
    """
    seen: list[list[str]] = []
    real_run, real_bytes = git.runner.run, git.runner.run_bytes

    def refuse(argv: list[str]) -> bool:
        seen.append(list(argv))
        return any(word in argv for word in ("rev-list", "log"))

    def run(argv: list[str], *args: object, **kw: object) -> subprocess.CompletedProcess[str]:
        if refuse(argv):
            return subprocess.CompletedProcess(argv, 128, "", "fatal: Could not read parent")
        return real_run(argv, *args, **kw)  # type: ignore[arg-type]

    def run_bytes(
        argv: list[str], *args: object, **kw: object
    ) -> subprocess.CompletedProcess[bytes]:
        if refuse(argv):
            return subprocess.CompletedProcess(argv, 128, b"", b"fatal")
        return real_bytes(argv, *args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(git.runner, "run", run)
    monkeypatch.setattr(git.runner, "run_bytes", run_bytes)
    return seen


def test_the_history_is_read_from_the_commit_objects_not_by_a_traversal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    up, c = _upstream(tmp_path)
    cl = _clone(tmp_path, up, c[5], c[4], c[6])
    seen = _no_traversal(monkeypatch)

    assert git.RunnerGit().is_ancestor(cl, c[4], c[6]) is True
    assert any("cat-file" in argv for argv in seen), seen
    assert git.RunnerGit().is_ancestor(cl, c[2], c[7]) is None


def test_the_containerised_seam_reads_the_same_way(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only the transport is swapped: each argv the container would run runs as host git."""
    up, c = _upstream(tmp_path)
    cl = _clone(tmp_path, up, c[5], c[4], c[6])

    def argv(
        self: git.ContainerGit, program: object, dest: Path, git_args: list[str], **kw: object
    ) -> list[str]:
        assert kw.get("writes") is False
        return ["git", "-C", str(dest), *git_args]

    def capture(
        self: git.ContainerGit, dest: Path, git_args: list[str], *, writes: bool
    ) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(["git", *git_args], cwd=dest, capture_output=True, text=True)
        if proc.returncode != 0:
            raise git.GitError(f"containerized git x in {dest} exited {proc.returncode}: ")
        return proc

    monkeypatch.setattr(git.ContainerGit, "_argv", argv)
    monkeypatch.setattr(git.ContainerGit, "_capture", capture)
    monkeypatch.setattr(git.ContainerGit, "_launcher", lambda self: "docker")
    _no_traversal(monkeypatch)

    assert git.ContainerGit().is_ancestor(cl, c[4], c[6]) is True
    assert git.ContainerGit().is_ancestor(cl, c[2], c[7]) is None


def test_the_container_argv_keeps_stdin_open_only_when_asked(tmp_path: Path) -> None:
    impl = git.ContainerGit(selinux_enforcing=lambda: False)
    asked = impl._argv("docker", tmp_path, ["cat-file"], writes=False, interactive=True)
    plain = impl._argv("docker", tmp_path, ["cat-file"], writes=False)

    assert "-i" in asked[: asked.index(impl.image)]
    assert "-i" not in plain


SHA = [f"{n:040x}" for n in range(1, 9)]


def _batch(*objects: tuple[str, bytes | None]) -> bytes:
    """What `git cat-file --batch` prints: a header and the body, or `<id> missing`."""
    out = b""
    for sha, body in objects:
        if body is None:
            out += f"{sha} missing\n".encode()
        else:
            out += f"{sha} commit {len(body)}\n".encode() + body + b"\n"
    return out


def _commit(*parents: str, message: str = "m") -> bytes:
    lines = ["tree " + "0" * 40, *(f"parent {p}" for p in parents)]
    lines += ["author a <a@a> 1 +0000", "committer a <a@a> 1 +0000", "", message]
    return "\n".join(lines).encode()


def test_the_batch_output_gives_each_commits_parents() -> None:
    raw = _batch(
        (SHA[0], _commit(SHA[1], SHA[2], message="merge \u00e9 \u00fc")),
        (SHA[1], _commit()),
        (SHA[3], None),
    )

    assert git.parse_commit_parents(raw) == {SHA[0]: (SHA[1], SHA[2]), SHA[1]: ()}


def test_a_message_that_looks_like_a_header_does_not_confuse_the_batch_reader() -> None:
    fake = f"{SHA[5]} commit 3\nparent {SHA[6]}"
    raw = _batch((SHA[0], _commit(SHA[1], message=fake)), (SHA[1], _commit()))

    assert git.parse_commit_parents(raw) == {SHA[0]: (SHA[1],), SHA[1]: ()}


def test_a_batch_output_that_is_cut_short_reads_nothing() -> None:
    raw = _batch((SHA[0], _commit(SHA[1])))

    assert git.parse_commit_parents(raw[:-30]) is None


def test_the_walk_finds_an_id_named_only_as_a_parent() -> None:
    parents = {SHA[0]: (SHA[1],), SHA[1]: (SHA[2],)}

    assert git.reaches(parents, SHA[0], SHA[2]) is True
    assert git.reaches(parents, SHA[0], SHA[0]) is True
    assert git.reaches(parents, SHA[0], SHA[3]) is False
    assert git.reaches(parents, SHA[1], SHA[0]) is False


def test_a_cycle_does_not_loop_forever() -> None:
    assert git.reaches({SHA[0]: (SHA[1],), SHA[1]: (SHA[0],)}, SHA[0], SHA[5]) is False


def test_only_commits_are_listed_and_the_list_is_capped() -> None:
    listing = f"commit {SHA[0]}\nblob {SHA[1]}\ntree {SHA[2]}\ncommit {SHA[3]}\n"

    assert git.commit_ids(listing, limit=10) == [SHA[0], SHA[3]]
    assert git.commit_ids(listing, limit=1) == [SHA[0]]


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
