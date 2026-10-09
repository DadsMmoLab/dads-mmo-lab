"""T621: add-on and module updates are counted in the background, once a day, never hanging.

T612 made Play do no network work, so the counts came only from the Modules tab's Check for
updates. This is the half that does it by itself: a bounded, cancellable fetch (`runner.
run_cancellable`, `git.RefreshGit`), a day's cache that a failure does not empty
(`apply.cached_module_updates`), and the two per-game entries the tab runs on a worker.

Real processes and real checkouts where the claim is about a process or a checkout; the fakes
below only stand in for the network and the clock.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from yulon import apply as apply_module
from yulon import git, runner
from yulon.catalog import upstream
from yulon.controller_wow_tortoise import modules as tortoise_modules
from yulon.controller_wow_wotlk import modules as wotlk_modules
from yulon.git import Behind, BehindCount, Counted, RefreshGit, RunnerGit, git_available

DAY = upstream.MAX_AGE_SECONDS

# ----------------------------------------------------------------------- the bounded process


def _sleeper() -> list[str]:
    return [sys.executable, "-c", "import time; time.sleep(15)"]


def test_a_process_that_outlives_its_timeout_is_ended_and_says_so() -> None:
    started = time.monotonic()
    proc = runner.run_cancellable(_sleeper(), timeout=0.5, cancel=threading.Event())
    assert time.monotonic() - started < 10, "the timeout did not end the child"
    assert runner.timed_out(proc)


def test_a_process_is_ended_when_cancel_is_set_long_before_its_timeout() -> None:
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    started = time.monotonic()
    proc = runner.run_cancellable(_sleeper(), timeout=120, cancel=cancel)
    assert time.monotonic() - started < 10, "cancel did not end the child"
    assert runner.cancelled(proc)
    assert not runner.timed_out(proc)


def test_a_process_that_finishes_in_time_is_answered_as_run_would() -> None:
    code = "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"
    proc = runner.run_cancellable(
        [sys.executable, "-c", code], timeout=30, cancel=threading.Event()
    )
    assert (proc.returncode, proc.stdout.strip(), proc.stderr.strip()) == (3, "out", "err")
    assert not runner.timed_out(proc) and not runner.cancelled(proc)


# --------------------------------------------------------------------------- the bounded fetch


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> object:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_the_refresh_fetch_carries_low_speed_limits_and_a_process_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dest = tmp_path / "mod-x"
    (dest / ".git").mkdir(parents=True)
    cancel = threading.Event()
    seen: list[tuple[list[str], dict[str, object]]] = []

    def fake(argv: list[str], **kwargs: object) -> object:
        seen.append((argv, kwargs))
        return _completed(1, stderr="stop here")

    monkeypatch.setattr(runner, "run_cancellable", fake)
    assert RefreshGit(cancel).counted_behind(dest, None) == Counted(None)
    (argv, kwargs), *rest = seen
    assert not rest, "a failed fetch must not be followed by a count"
    assert argv[argv.index(f"http.lowSpeedLimit={git.FETCH_LOW_SPEED_LIMIT}") - 1] == "-c"
    assert argv[argv.index(f"http.lowSpeedTime={git.FETCH_LOW_SPEED_SECONDS}") - 1] == "-c"
    assert "fetch" in argv and argv[-2] == "origin"
    assert kwargs["timeout"] == git.FETCH_TIMEOUT_SECONDS
    assert kwargs["cancel"] is cancel
    assert 0 < git.FETCH_TIMEOUT_SECONDS <= 300, "a refresh may not be left waiting for minutes"


def test_a_fetch_that_timed_out_is_could_not_ask(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dest = tmp_path / "mod-x"
    (dest / ".git").mkdir(parents=True)
    monkeypatch.setattr(
        runner,
        "run_cancellable",
        lambda argv, **k: _completed(runner.TIMED_OUT_RETURNCODE, stderr="timed out after 90s"),
    )
    assert RefreshGit(threading.Event()).commits_behind(dest, None) is None


def _g(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


@pytest.mark.skipif(not git_available(), reason="needs a host git to make a real checkout")
def test_the_refresh_counts_a_real_checkout_like_the_check_press_does(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()
    _g(origin, "init", "-q", "-b", "main", ".")
    for n in range(3):
        (origin / "a.txt").write_text(f"{n}\n")
        _g(origin, "add", ".")
        _g(origin, "commit", "-q", "-m", f"c{n}")
    clone = tmp_path / "clone"
    git.RunnerGit().clone(git.CloneSpec(url=origin.as_uri(), dest=clone))
    (origin / "a.txt").write_text("new\n")
    _g(origin, "commit", "-q", "-am", "newer")
    refreshed = RefreshGit(threading.Event()).commits_behind(clone, None)
    assert refreshed == RunnerGit().commits_behind(clone, None) == 1


@pytest.mark.skipif(not git_available(), reason="needs a host git")
def test_a_cancelled_refresh_counts_nothing_and_does_not_fetch(tmp_path: Path) -> None:
    dest = tmp_path / "c"
    dest.mkdir()
    _g(dest, "init", "-q", ".")
    cancel = threading.Event()
    cancel.set()
    assert RefreshGit(cancel).counted_behind(dest, None) == Counted(None)


# ------------------------------------------------------------------------- the day's cache


class _Git:
    """A reader that answers from a script, and says what it was asked."""

    def __init__(self, answers: list[BehindCount], head: str = "h1") -> None:
        self.answers = answers
        self.head = head
        self.fetches = 0
        self.on_fetch: list[object] = []

    def commits_behind(
        self, dest: Path, branch: str | None, *, release: bool = False
    ) -> BehindCount:
        self.fetches += 1
        for hook in self.on_fetch:
            hook()  # type: ignore[operator]
        return self.answers.pop(0)

    def head_sha(self, dest: Path) -> str | None:
        return self.head


def _server(tmp_path: Path, *names: str, kind: str = "module") -> Path:
    server = tmp_path / "server"
    for name in names:
        (server / apply_module.CLONE_DIRS[kind] / name / ".git").mkdir(parents=True)  # type: ignore[index]
    return server


def _refresh(
    server: Path, reader: _Git, now: int, *, cancel: threading.Event | None = None, kind="module"
):
    return apply_module.refresh_module_updates(
        server, kind=kind, cancel=cancel or threading.Event(), git=reader, now=now
    )


def test_a_clone_is_asked_once_a_day_and_again_when_the_day_is_over(tmp_path: Path) -> None:
    server = _server(tmp_path, "mod-a")
    reader = _Git([3, 4])
    (first,) = _refresh(server, reader, 1_000)
    (again,) = _refresh(server, reader, 1_000 + DAY - 1)
    assert reader.fetches == 1, "a second refresh inside the day went to the network"
    assert (first.behind, again.behind) == (3, 3)
    (later,) = _refresh(server, reader, 1_000 + DAY + 1)
    assert reader.fetches == 2
    assert later.behind == 4


def test_a_failed_fetch_keeps_the_count_the_day_before_had(tmp_path: Path) -> None:
    server = _server(tmp_path, "mod-a")
    reader = _Git([5, None])
    _refresh(server, reader, 1_000)
    (after,) = _refresh(server, reader, 1_000 + DAY + 10)
    assert reader.fetches == 2
    assert after.behind == 5, "a refresh that could not ask threw away the last count"
    # ... and it is what the next reader of the cache gets, too.
    (cached,) = apply_module.cached_module_updates(
        server, kind="module", git=_Git([]), now=1_000 + DAY + 20
    )
    assert cached.behind == 5


def test_a_failed_fetch_is_tried_again_in_an_hour_not_at_every_start(tmp_path: Path) -> None:
    server = _server(tmp_path, "mod-a")
    reader = _Git([5, None, 6])
    _refresh(server, reader, 1_000)
    _refresh(server, reader, 1_000 + DAY + 10)  # fails, count kept
    _refresh(server, reader, 1_000 + DAY + 10 + upstream.RETRY_SECONDS - 5)
    assert reader.fetches == 2, "a failure was asked again inside the retry wait"
    (later,) = _refresh(server, reader, 1_000 + DAY + 10 + upstream.RETRY_SECONDS + 5)
    assert reader.fetches == 3 and later.behind == 6


def test_a_failure_with_no_count_before_it_is_could_not_ask(tmp_path: Path) -> None:
    server = _server(tmp_path, "mod-a")
    (row,) = _refresh(server, _Git([None]), 1_000)
    assert row.behind is None


def test_an_old_count_is_not_kept_for_a_clone_that_has_since_moved(tmp_path: Path) -> None:
    server = _server(tmp_path, "mod-a")
    _refresh(server, _Git([5], head="h1"), 1_000)
    (after,) = _refresh(server, _Git([None], head="h2"), 1_000 + DAY + 10)
    assert after.behind is None, "a count taken at another commit is not this clone's count"


def test_the_check_press_still_shows_a_failure_as_a_failure(tmp_path: Path) -> None:
    server = _server(tmp_path, "mod-a")
    apply_module.cached_module_updates(server, kind="module", git=_Git([5]), now=1_000)
    (row,) = apply_module.cached_module_updates(
        server, kind="module", git=_Git([None]), now=1_000 + DAY + 10
    )
    assert row.behind is None


def test_a_cancel_before_the_first_clone_asks_nothing(tmp_path: Path) -> None:
    server = _server(tmp_path, "mod-a", "mod-b")
    cancel = threading.Event()
    cancel.set()
    reader = _Git([1, 2])
    assert _refresh(server, reader, 1_000, cancel=cancel) == ()
    assert reader.fetches == 0


def test_a_cancel_during_a_fetch_stops_before_the_next_clone_and_keeps_the_half_answer_out(
    tmp_path: Path,
) -> None:
    server = _server(tmp_path, "mod-a", "mod-b", "mod-c")
    cancel = threading.Event()
    reader = _Git([1, 2, 3])
    reader.on_fetch.append(cancel.set)
    assert _refresh(server, reader, 1_000, cancel=cancel) == ()
    assert reader.fetches == 1, "the refresh went on to the next clone after a cancel"
    # The cancelled clone was never cached as an answer (as a count or a failure).
    follow = _Git([9, 9, 9])
    rows = apply_module.cached_module_updates(server, kind="module", git=follow, now=1_001)
    assert follow.fetches == 3 and [r.behind for r in rows] == [9, 9, 9]


def test_a_cancelled_refresh_hands_back_nothing_even_for_the_clones_it_did_count(
    tmp_path: Path,
) -> None:
    """The tab drops a cancelled run's answer; this is the seam's half: nothing to drop."""
    server = _server(tmp_path, "mod-a", "mod-b")
    cancel = threading.Event()
    reader = _Git([1, 2])
    reader.on_fetch.append(lambda: cancel.set() if reader.fetches == 2 else None)
    assert _refresh(server, reader, 1_000, cancel=cancel) == ()
    assert reader.fetches == 2
    # What it did finish is kept for the next reader.
    follow = _Git([7])
    rows = apply_module.cached_module_updates(server, kind="module", git=follow, now=1_001)
    assert follow.fetches == 1 and [r.behind for r in rows] == [1, 7]


def test_with_no_host_git_the_refresh_asks_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _server(tmp_path, "mod-a")
    monkeypatch.setattr(apply_module, "git_available", lambda *a, **k: False)
    assert (
        apply_module.refresh_module_updates(server, kind="module", cancel=threading.Event()) == ()
    )


# ------------------------------------------------------------------- the two per-game entries


def test_tortoises_refresh_counts_the_addon_clones_into_the_shared_cache(tmp_path: Path) -> None:
    server = _server(tmp_path, "tortoise-gm-manager", kind="mod")
    reader = _Git([2])
    rows = tortoise_modules.refresh_module_updates(server, threading.Event(), git=reader, now=1_000)
    assert [(r.family, r.key, r.behind) for r in rows] == [("mod", "tortoise-gm-manager", 2)]
    # The Check press reads the very row the refresh made, and asks nobody.
    pressed = tortoise_modules.module_updates(server, git=_Git([]), now=1_100)
    assert [r.behind for r in pressed] == [2]


def test_wotlks_refresh_counts_the_module_clones_into_the_same_cache_file(
    tmp_path: Path,
) -> None:
    server = _server(tmp_path, "mod-aoe-loot")
    reader = _Git([4])
    rows = wotlk_modules.refresh_module_updates(server, threading.Event(), git=reader, now=1_000)
    assert [(r.family, r.key, r.behind) for r in rows] == [("module", "mod-aoe-loot", 4)]
    (server / apply_module.MODULE_UPDATES_FILE).stat()
    again = wotlk_modules.refresh_module_updates(server, threading.Event(), git=_Git([]), now=1_100)
    assert [r.behind for r in again] == [4]


def test_a_refresh_is_not_a_behind_count_the_player_asked_for(tmp_path: Path) -> None:
    """The row type is the Check press's own, so the tab needs no second shape."""
    server = _server(tmp_path, "mod-a")
    (row,) = _refresh(server, _Git([Behind.UNCOUNTED]), 1_000)
    assert isinstance(row, apply_module.ModuleUpdate) and row.behind is Behind.UNCOUNTED


# ------------------------------------------- a cancel is not held up by GitHub (T621 polish)


def _release_follower(tmp_path: Path) -> Path:
    return _server(tmp_path, "mod-a")


def test_a_quit_during_a_slow_release_lookup_returns_promptly(tmp_path: Path) -> None:
    server = _release_follower(tmp_path)
    cancel = threading.Event()
    held = threading.Event()

    def slow(slug: str) -> upstream.Release | None:
        held.wait(30)  # GitHub taking its ten seconds, and then some
        return None

    threading.Timer(0.3, cancel.set).start()
    started = time.monotonic()
    rows = apply_module.refresh_module_updates(
        server,
        kind="module",
        cancel=cancel,
        git=_Git([1]),
        releases={"mod-a": "o/mod-a"},
        newest_release=slow,
        now=1_000,
    )
    took = time.monotonic() - started
    held.set()
    assert rows == ()
    assert took < 3, f"the refresh waited {took:.1f}s on GitHub after a cancel"


def test_a_cancelled_refresh_asks_github_for_nothing_more(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _server(tmp_path, "mod-a", "mod-b")
    settled: list[object] = []
    monkeypatch.setattr(
        apply_module._GitHubCounts, "settle", lambda self, rows: settled.append(rows) or rows
    )
    cancel = threading.Event()
    reader = _Git([Behind.UNCOUNTED, 3])
    reader.on_fetch.append(lambda: cancel.set() if reader.fetches == 2 else None)
    assert _refresh(server, reader, 1_000, cancel=cancel) == ()
    assert settled == [], "GitHub's compare was asked after the refresh was cancelled"
    # mod-a was counted but still owes GitHub its question, so it is not cached as answered.
    follow = _Git([4, 5])
    rows = apply_module.cached_module_updates(server, kind="module", git=follow, now=1_001)
    assert follow.fetches == 2 and [r.behind for r in rows] == [4, 5]


def test_an_uncancelled_refresh_still_settles_with_github(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _server(tmp_path, "mod-a")
    settled: list[object] = []
    monkeypatch.setattr(
        apply_module._GitHubCounts, "settle", lambda self, rows: settled.append(rows) or rows
    )
    _refresh(server, _Git([2]), 1_000)
    assert len(settled) == 1


# ------------------------------------------------------- the child's children go too


def test_cancel_ends_a_grandchild_and_not_only_the_child() -> None:
    import os

    code = (
        "import subprocess, sys, time\n"
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(g.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    cancel = threading.Event()
    seen: list[int] = []

    def watch() -> None:
        # The grandchild's pid arrives on the child's stdout, which run_cancellable holds;
        # find it from the process table instead, by its parent.
        time.sleep(1.5)
        cancel.set()

    threading.Thread(target=watch, daemon=True).start()
    before = set(_children_of_pytest())
    proc = runner.run_cancellable([sys.executable, "-c", code], timeout=60, cancel=cancel)
    assert runner.cancelled(proc)
    seen = [int(x) for x in proc.stdout.split()] if proc.stdout.strip() else []
    assert seen, "the child never printed its grandchild's pid"
    time.sleep(0.5)
    for pid in seen:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            continue
        # A zombie still answers kill 0 until reaped by init; look at its state.
        state = Path(f"/proc/{pid}/stat").read_text().split(") ")[-1].split()[0]
        assert state in ("Z", "X"), f"the grandchild {pid} outlived the cancel (state {state})"
    assert set(_children_of_pytest()) <= before | set(seen)


def _children_of_pytest() -> list[int]:
    import os

    out = subprocess.run(
        ["pgrep", "-P", str(os.getpid())], capture_output=True, text=True, check=False
    ).stdout
    return [int(x) for x in out.split()]


class _ShallowGit(_Git):
    """A reader whose one clone is a shallow checkout on GitHub: its count is GitHub's to give."""

    def commits_behind(
        self, dest: Path, branch: str | None, *, release: bool = False
    ) -> BehindCount:
        return Behind.UNCOUNTED

    def counted_behind(self, dest: Path, branch: str | None, *, release: bool = False) -> Counted:
        self.fetches += 1
        return Counted(Behind.UNCOUNTED, "a" * 40, "b" * 40)

    def remote_url(self, dest: Path) -> str | None:
        return "https://github.com/o/mod-a"


def test_a_quit_during_a_slow_github_compare_returns_promptly(tmp_path: Path) -> None:
    server = _server(tmp_path, "mod-a")
    cancel = threading.Event()
    held = threading.Event()
    asked: list[str] = []

    def slow(slug: str, head: str, ref: str) -> None:
        asked.append(slug)
        held.wait(30)

    threading.Timer(0.4, cancel.set).start()
    started = time.monotonic()
    rows = apply_module.refresh_module_updates(
        server, kind="module", cancel=cancel, git=_ShallowGit([]), compare_commits=slow, now=1_000
    )
    took = time.monotonic() - started
    held.set()
    assert asked == ["o/mod-a"], "the compare was never reached, so this proved nothing"
    assert rows == ()
    assert took < 3, f"the refresh waited {took:.1f}s on GitHub's compare after a cancel"
