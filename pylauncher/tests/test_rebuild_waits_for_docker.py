"""T223: a Rebuild whose recreate step cannot reach Docker, and one that cannot reach Docker Hub.

Seen on yulon-win11, 2026-10-04: an 85-minute compile finished, the recreate
step asked `docker info` ONCE, the answer took longer than its 10-second bound
on a box still busy exporting the image, and the restore that followed deleted
the new build -- while the panel said "Nothing was touched". Docker was slow,
not down: the restore's own `docker tag` calls, seconds later, all worked. The
next press then died in seconds on a Docker Hub TLS timeout while BuildKit
looked up `ubuntu:24.04`, and said only "the build failed (exit 1)".

The owner's answers (2026-10-04):

* D1 -- wait for Docker, then say plainly that the new build was removed and
  the server is on the build it had; keeping the build for later is T224;
* D2 -- wait up to 3 minutes, asking every 5 seconds, and honour Stop;
* D3 -- Docker Hub unreachable before anything compiled: say so, and try the
  build once more after 30 seconds.

Every test drives `rebuild()` itself against `test_rebuild._Daemon`, the fake
that holds names, images and the containers made from them, so "the new build
was removed" is read off the daemon and not off the sentence that claims it.

The engine's `sleep` is a no-op in every test (`support_native`), while its
`monotonic` is the real clock unless a test binds `_Clock` -- which is why the
wait is bounded by its number of asks as well as by the clock, and why one test
below freezes the clock to prove the count alone ends it.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from tests.support_native import Recorder, engine
from tests.test_rebuild import (
    _answers,
    _Daemon,
    _daemon_for,
    _refs,
    _seams_of,
    a_finished_install,
)
from yulon import docker, server_build_presses
from yulon.catalog import native
from yulon.catalog.installer import InstallerError, InstallOptions, RollbackNotDone

WAITING = "up to 3 minutes for Docker"
SILENT_FOR_THE_WAIT = (False,) * 36
"""Every ask of the recreate's 3-minute wait unanswered (at 0, 5, ... 175 s); see below."""
REMOVED = "The build that had just finished was removed"
HUB = "Docker Hub could not be reached"


class _Clock:
    """`monotonic` and `sleep` for the engine: time passes only when something spends it."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class _Probe:
    """`docker_ready`, answering `answers` in order and then `then` for ever.

    `cost` is what one ask takes on the clock: the real probe is `docker info`
    with a 10-second bound, so a daemon that never answers spends ten seconds a
    time. `limit` turns a loop that would never end into a failing test rather
    than a hung run.
    """

    def __init__(
        self,
        clock: _Clock | None,
        *answers: bool,
        then: bool = False,
        cost: float = 0.0,
        limit: int = 200,
        on_ask: dict[int, threading.Event] | None = None,
    ) -> None:
        self.clock = clock
        self.answers = list(answers)
        self.then = then
        self.cost = cost
        self.limit = limit
        self.on_ask = on_ask or {}
        self.asked = 0
        self.started: list[float] = []
        self.silent = False

    def __call__(self) -> bool:
        self.asked += 1
        if self.asked > self.limit:
            raise AssertionError(f"docker_ready was asked {self.asked} times: the wait never ends")
        if self.clock is not None:
            self.started.append(self.clock.now)
            self.clock.now += self.cost
        if self.asked in self.on_ask:
            self.on_ask[self.asked].set()
        answer = self.answers[self.asked - 1] if self.asked <= len(self.answers) else self.then
        self.silent = not answer
        return answer


def _silent_with(probe: _Probe, daemon: _Daemon) -> dict[str, object]:
    """The daemon's tag and rmi calls, failing while the last ask went unanswered.

    A Docker that does not answer `docker info` does not answer `docker tag`
    either; the fake daemon alone would move tags for a daemon that is not there
    (cold review of T223: the restore's tag calls ran against the silent Docker).
    Spelled as Docker Desktop on Windows spells it.
    """
    down = (
        'error during connect: Get "http://%2F%2F.%2Fpipe%2FdockerDesktopLinuxEngine/v1.47/'
        'images/json": open //./pipe/dockerDesktopLinuxEngine: The system cannot find the '
        "file specified."
    )

    def tag_image(src: str, dst: str) -> str:
        return down if probe.silent else daemon.tag_image(src, dst)

    def remove_image(ref: str, force: bool = False) -> str:
        return down if probe.silent else daemon.remove_image(ref, force)

    return {"tag_image": tag_image, "remove_image": remove_image}


def _new_build_live(daemon: _Daemon, server_dir: Path) -> bool:
    return all(daemon.names.get(ref) == "after" for ref in _refs(server_dir))


def _untouched(daemon: _Daemon, server_dir: Path) -> None:
    """The server as it was before the press: its names and containers on the old build."""
    assert all(daemon.names.get(ref) == "before" for ref in _refs(server_dir)), daemon.names
    assert set(daemon.containers.values()) == {"before"}, daemon.containers
    assert daemon.transient() == [], daemon.transient()


# -- D2: the recreate waits for a slow Docker ---------------------------------


def test_a_recreate_waits_for_a_docker_that_answers_late_and_keeps_the_new_build(
    tmp_path: Path,
) -> None:
    """The yulon-win11 night, with the wait: Docker answers on the third ask, the build is used."""
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    clock = _Clock()
    probe = _Probe(clock, False, False, True, then=True)
    said = list(
        engine(
            rec,
            **_seams_of(rec, daemon),
            docker_ready=probe,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        ).rebuild(InstallOptions(server_dir=server_dir))
    )
    assert probe.asked == 3, probe.asked
    assert _new_build_live(daemon, server_dir), daemon.names
    # The containers the recreate selects were made from the NEW build.
    recreated = {ref for ref in _refs(server_dir) if ref not in daemon.pinned}
    assert recreated and {daemon.containers[ref] for ref in recreated} == {"after"}
    assert daemon.transient() == [], daemon.transient()
    assert len([line for line in said if WAITING in line]) == 1, said
    assert said[-1].endswith(f"was rebuilt and is running in {server_dir}"), said


def test_a_docker_that_never_answers_is_asked_every_5_seconds_until_3_minutes_then_refused(
    tmp_path: Path,
) -> None:
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    clock = _Clock()
    # Silent for the whole wait, back for the restore that follows it.
    probe = _Probe(clock, *SILENT_FOR_THE_WAIT, then=True)
    with pytest.raises(InstallerError) as raised:
        list(
            engine(
                rec,
                **_seams_of(rec, daemon),
                docker_ready=probe,
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            ).rebuild(InstallOptions(server_dir=server_dir))
        )
    # Asked at 0, 5, ... 175 seconds: 36 asks. None is started at 180, when the
    # three minutes are already spent (adversarial review: one there could carry
    # the wait to 190 s on its own 10-second bound). The 37th is the restore's.
    assert probe.started[:36] == [5.0 * n for n in range(36)], probe.started
    assert probe.asked == 37, probe.asked
    assert clock.now == 180.0, clock.now
    message = str(raised.value)
    assert "Docker did not answer for 3 minutes" in message, message
    assert "Nothing was touched" not in message, message
    _untouched(daemon, server_dir)


def test_the_wait_ends_by_its_count_of_asks_when_the_clock_does_not_move(tmp_path: Path) -> None:
    """In tests `sleep` is a no-op and the clock is real: the wait must still end, and soon.

    The first ask and one per pause, 1 + 180 / 5: the count alone ends it.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    probe = _Probe(None, *(False,) * 37, then=True, limit=100)
    with pytest.raises(InstallerError):
        list(
            engine(
                rec,
                **_seams_of(rec, daemon),
                docker_ready=probe,
                monotonic=lambda: 0.0,
            ).rebuild(InstallOptions(server_dir=server_dir))
        )
    assert probe.asked == 38, "37 in the wait and one by the restore after it"
    _untouched(daemon, server_dir)


def test_the_wait_ends_by_the_clock_when_every_ask_is_slow(tmp_path: Path) -> None:
    """Each ask costs 8 s and each pause 5 s: the 3 minutes are spent after 14 asks, not 37.

    Asks end at 8, 21, ... 177 s; the pause after the last is cut to the 3 s left,
    so the wait ends at 180 s exactly rather than at 182.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    clock = _Clock()
    probe = _Probe(clock, *(False,) * 14, then=True, cost=8.0)
    with pytest.raises(InstallerError) as raised:
        list(
            engine(
                rec,
                **_seams_of(rec, daemon),
                docker_ready=probe,
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            ).rebuild(InstallOptions(server_dir=server_dir))
        )
    assert probe.asked == 15, "14 in the wait and one by the restore after it"
    assert probe.started[14] == 180.0, probe.started
    assert "Docker did not answer for 3 minutes" in str(raised.value), raised.value
    _untouched(daemon, server_dir)


def test_a_stop_during_the_docker_wait_ends_it_and_replaces_nothing(
    tmp_path: Path,
) -> None:
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    clock = _Clock()
    stop = threading.Event()
    probe = _Probe(clock, False, False, then=True, on_ask={2: stop})
    with pytest.raises(InstallerError) as raised:
        list(
            engine(
                rec,
                **_seams_of(rec, daemon),
                docker_ready=probe,
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            ).rebuild(InstallOptions(server_dir=server_dir), cancel=stop)
        )
    assert probe.asked == 3, "two in the wait, and the restore's own before it moves a tag"
    message = str(raised.value)
    assert "stopped" in message, message
    # Said once (cold review): the Stop's sentence and the restore's both said it.
    assert message.lower().count("replaced") == 1, message
    assert "Docker did not answer for" not in message, message
    # The Stop gives up the new build, and says so: the same arm as Docker's refusal.
    assert REMOVED in message, message
    _untouched(daemon, server_dir)
    assert "after" not in daemon.names.values(), daemon.names


def test_a_stop_during_a_pause_is_read_before_docker_is_asked_again(tmp_path: Path) -> None:
    """Codex review: an ask after the pause can take its own 10 seconds, so the Stop goes first."""
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    clock = _Clock()
    stop = threading.Event()
    probe = _Probe(clock, False, then=True)

    def sleep(seconds: float) -> None:
        clock.sleep(seconds)
        stop.set()

    with pytest.raises(InstallerError) as raised:
        list(
            engine(
                rec,
                **_seams_of(rec, daemon),
                docker_ready=probe,
                monotonic=clock.monotonic,
                sleep=sleep,
            ).rebuild(InstallOptions(server_dir=server_dir), cancel=stop)
        )
    assert probe.asked == 2, "one in the wait, and the restore's own"
    # The pause is taken a second at a time, so the Stop lands within one (cold review).
    assert probe.started[1] == 1.0, probe.started
    assert "stopped while it waited for Docker" in str(raised.value), raised.value
    _untouched(daemon, server_dir)


# -- D1: what the refusal says about the build that finished ------------------


def test_a_recreate_refused_after_a_finished_compile_says_the_new_build_was_removed(
    tmp_path: Path,
) -> None:
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    clock = _Clock()
    with pytest.raises(InstallerError) as raised:
        list(
            engine(
                rec,
                **_seams_of(rec, daemon),
                docker_ready=_Probe(clock, *SILENT_FOR_THE_WAIT, then=True),
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            ).rebuild(InstallOptions(server_dir=server_dir))
        )
    message = str(raised.value)
    assert REMOVED in message, message
    assert "the server is still on the build it had before this rebuild" in message, message
    # The press that was used, named, and not "the next rebuild" (cold review).
    rebuild = server_build_presses.under_server_build(server_build_presses.REBUILD)
    assert f"pressing {rebuild} again compiles it again" in message, message
    assert engine(rec).start_refusal(server_dir) is None
    # And it is true: no name on the daemon points at the new build any more.
    assert "after" not in daemon.names.values(), daemon.names
    _untouched(daemon, server_dir)


def test_a_docker_silent_through_the_restore_too_leaves_start_refused(tmp_path: Path) -> None:
    """Cold review, IMPORTANT: the tags cannot go back, so the next Start must not run them.

    Docker answers nothing for the recreate's three minutes and nothing for the
    restore's. The new build has never started, the live tags name it, and Start
    is `compose up -d`, which would run it -- the opposite of owner answer D1.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    clock = _Clock()
    probe = _Probe(clock)
    said: list[str] = []
    with pytest.raises(RollbackNotDone) as raised:
        for line in engine(
            rec,
            **{**_seams_of(rec, daemon), **_silent_with(probe, daemon)},
            docker_ready=probe,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        ).rebuild(InstallOptions(server_dir=server_dir)):
            said.append(line)
    assert probe.asked == 72, "36 asks in each of the two waits"
    message = str(raised.value)
    assert raised.value.touched is False
    assert engine(rec).start_refusal(server_dir) == native.UNTESTED_BUILD_REFUSAL
    rebuild = server_build_presses.under_server_build(server_build_presses.REBUILD)
    assert f"Start is refused until this server is rebuilt: press {rebuild}." in message, message
    assert "No container was replaced" in message, message
    # What is on the daemon is what the sentence says: the new build on the live
    # tags, the old one under its rollback names -- kept, being its only copy.
    assert _new_build_live(daemon, server_dir), daemon.names
    assert sorted(daemon.transient()) == sorted(
        ref + native.ROLLBACK_TAG_SUFFIX for ref in _refs(server_dir)
    )
    assert len([line for line in said if "before putting the build from before" in line]) == 1


@pytest.mark.parametrize("refused", ["the-new-build-cannot-be-named", "a-tag-will-not-move-back"])
def test_a_tag_docker_refuses_after_it_answered_leaves_start_refused_too(
    tmp_path: Path, refused: str
) -> None:
    """Lead decision (owner answer D1): T197's untouched exits refuse a Start as well.

    Docker answers the restore, but refuses a tag: either the new build's own
    `-failed` name, or the second tag moved back (the first is then undone, so
    every live tag names the new build again, not mixed). No container was
    replaced, the new build never started, and Start would run it.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    clock = _Clock()
    moved_back: list[str] = []

    def tag_image(src: str, dst: str) -> str:
        if refused == "the-new-build-cannot-be-named" and dst.endswith(native.FAILED_TAG_SUFFIX):
            return "Error response from daemon: read-only file system"
        if refused == "a-tag-will-not-move-back" and src.endswith(native.ROLLBACK_TAG_SUFFIX):
            moved_back.append(dst)
            if len(moved_back) == 2:
                return "Error response from daemon: read-only file system"
        return daemon.tag_image(src, dst)

    with pytest.raises(RollbackNotDone) as raised:
        list(
            engine(
                rec,
                **{**_seams_of(rec, daemon), "tag_image": tag_image},
                docker_ready=_Probe(clock, *SILENT_FOR_THE_WAIT, then=True),
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            ).rebuild(InstallOptions(server_dir=server_dir))
        )
    assert raised.value.touched is False and raised.value.mixed is False
    assert _new_build_live(daemon, server_dir), daemon.names
    assert engine(rec).start_refusal(server_dir) == native.UNTESTED_BUILD_REFUSAL
    rebuild = server_build_presses.under_server_build(server_build_presses.REBUILD)
    assert f"Start is refused until this server is rebuilt: press {rebuild}." in str(
        raised.value
    ), raised.value


def test_a_stop_during_the_wait_does_not_cut_the_restores_wait_short(tmp_path: Path) -> None:
    """The Stop that ended the recreate's wait is still set when the restore waits; it must
    not end that one too, or the tags stay on the new build with nothing refusing a Start."""
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    clock = _Clock()
    stop = threading.Event()
    probe = _Probe(clock, on_ask={2: stop})
    with pytest.raises(RollbackNotDone):
        list(
            engine(
                rec,
                **{**_seams_of(rec, daemon), **_silent_with(probe, daemon)},
                docker_ready=probe,
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            ).rebuild(InstallOptions(server_dir=server_dir), cancel=stop)
        )
    assert probe.asked == 2 + 36, "two in the stopped wait, all 36 in the restore's"
    assert engine(rec).start_refusal(server_dir) == native.UNTESTED_BUILD_REFUSAL


def test_a_rebuild_that_succeeds_lets_the_untested_build_refusal_go(tmp_path: Path) -> None:
    """The refusal's own advice works: the Rebuild it names runs, and clears it."""
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    assert native.owe_start(server_dir, why="untested") == ""
    assert engine(rec).start_refusal(server_dir) == native.UNTESTED_BUILD_REFUSAL
    daemon = _daemon_for(server_dir)
    list(engine(rec, **_seams_of(rec, daemon)).rebuild(InstallOptions(server_dir=server_dir)))
    assert engine(rec).start_refusal(server_dir) is None


# -- D2 in the restore: putting the old build back waits for Docker too -------


def test_putting_the_old_build_back_waits_for_a_slow_docker_too(tmp_path: Path) -> None:
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    clock = _Clock()
    # Ready for the replace; then slow for the restore's own recreate.
    probe = _Probe(clock, True, False, False, then=True)
    with pytest.raises(InstallerError) as raised:
        list(
            engine(
                rec,
                **_seams_of(rec, daemon),
                docker_ready=probe,
                wait_ready=_answers(False, True),
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            ).rebuild(InstallOptions(server_dir=server_dir))
        )
    assert probe.asked == 4, probe.asked
    assert "put back and is running again" in str(raised.value), raised.value


def test_a_restore_docker_never_answers_says_its_servers_are_left_stopped(tmp_path: Path) -> None:
    """Cold review: Stop does not end the restore's wait; the sentences say so, and what is left."""
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    clock = _Clock()
    probe = _Probe(clock, True)
    said: list[str] = []
    with pytest.raises(InstallerError) as raised:
        for line in engine(
            rec,
            **_seams_of(rec, daemon),
            docker_ready=probe,
            wait_ready=_answers(False, True),
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        ).rebuild(InstallOptions(server_dir=server_dir)):
            said.append(line)
    assert probe.asked == 37, "the replace's one, and 36 in the restore's wait"
    waiting = [line for line in said if WAITING in line]
    assert len(waiting) == 1 and "Stop does not end this wait" in waiting[0], said
    message = str(raised.value)
    assert "its servers are stopped" in message, message
    assert "press Start" in message, message
    # Left stopped is the existing exit for it, and nothing says the old build runs.
    assert isinstance(raised.value, native.ServersLeftStopped), type(raised.value)
    assert "is running" not in message and "did not report ready" not in message, message


def test_a_stop_that_started_the_restore_does_not_cut_the_restores_wait_short(
    tmp_path: Path,
) -> None:
    """The Stop that brought the restore here is already set; it must not abandon the old build.

    `_stop_control()`'s rule for a rollback: the Cancel FORCES the failed build's
    stop rather than giving up, because a restore given up half-way leaves
    nothing running. The wait for Docker in front of the restore's recreate is
    the same: a Stop read there would end it on the first ask.
    """
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    clock = _Clock()
    stop = docker.CancelWithForce()
    asked = {"n": 0}

    def stopped_while_loading(spec: object, ready: object) -> bool:
        asked["n"] += 1
        if asked["n"] == 1:
            stop.set()
            return False
        return True

    probe = _Probe(clock, True, False, then=True)
    with pytest.raises(InstallerError) as raised:
        list(
            engine(
                rec,
                **_seams_of(rec, daemon),
                docker_ready=probe,
                wait_ready=stopped_while_loading,
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            ).rebuild(InstallOptions(server_dir=server_dir), cancel=stop)
        )
    assert probe.asked == 3, probe.asked
    assert "put back and is running again" in str(raised.value), raised.value


# -- D3: Docker Hub unreachable before anything compiled ----------------------

_TOKEN_TIMEOUT = (
    'failed to authorize: failed to fetch anonymous token: Get "https://auth.docker.io/token?'
    'scope=repository%3Alibrary%2Fubuntu%3Apull&service=registry.docker.io": net/http: TLS '
    "handshake timeout"
)

HUB_UNREACHABLE = (
    # The `#3 ERROR:` line is yulon-win11's own (live.log l.195, 2026-10-04); the
    # fence, the Dockerfile context and the compose epilogue around it are
    # BuildKit's fixed failure shape (`docker._buildkit_failure()`), for a step
    # that printed nothing of its own.
    "#1 [internal] load local bake definitions",
    "#1 reading from stdin 1.02kB done",
    "#1 DONE 0.0s",
    "#2 [ac-worldserver internal] load build definition from Dockerfile",
    "#2 transferring dockerfile: 4.81kB done",
    "#2 DONE 0.0s",
    "#3 [ac-worldserver internal] load metadata for docker.io/library/ubuntu:24.04",
    f"#3 ERROR: {_TOKEN_TIMEOUT}",
    "------",
    " > [ac-worldserver internal] load metadata for docker.io/library/ubuntu:24.04:",
    "------",
    "Dockerfile:16",
    "--------------------",
    "  15 |     # The builder",
    "  16 | >>> FROM ubuntu:24.04 AS builder",
    "  17 |     ",
    "--------------------",
    "failed to solve: ubuntu:24.04: failed to resolve source metadata for "
    f"docker.io/library/ubuntu:24.04: {_TOKEN_TIMEOUT}",
)

TOKEN_REFUSED = (
    # Docker Hub ANSWERED, with a refusal: the same "failed to fetch anonymous
    # token" wrapper, around an HTTP status rather than a network error (Codex
    # review). A rate limit or a refusal is not "check your internet connection".
    "#3 [ac-worldserver internal] load metadata for docker.io/library/ubuntu:24.04",
    "#3 ERROR: failed to authorize: failed to fetch anonymous token: unexpected status from GET "
    "request to https://auth.docker.io/token?scope=repository%3Alibrary%2Fubuntu%3Apull&service="
    "registry.docker.io: 429 Too Many Requests",
    "------",
    " > [ac-worldserver internal] load metadata for docker.io/library/ubuntu:24.04:",
    "------",
    "failed to solve: ubuntu:24.04: failed to resolve source metadata for "
    "docker.io/library/ubuntu:24.04: failed to authorize: failed to fetch anonymous token: "
    "unexpected status from GET request to https://auth.docker.io/token?scope=repository%3A"
    "library%2Fubuntu%3Apull&service=registry.docker.io: 429 Too Many Requests",
)

TAG_NOT_FOUND = (
    # The same step failing for a reason another try cannot fix: the name.
    "#3 [ac-worldserver internal] load metadata for docker.io/library/ubuntu:24.04",
    "#3 ERROR: docker.io/library/ubuntu:24.04: not found",
    "------",
    " > [ac-worldserver internal] load metadata for docker.io/library/ubuntu:24.04:",
    "------",
    "failed to solve: ubuntu:24.04: failed to resolve source metadata for "
    "docker.io/library/ubuntu:24.04: docker.io/library/ubuntu:24.04: not found",
)

NETWORK_INSIDE_A_RUN_STEP = (
    # The base image was found; a later step's OWN command could not reach the
    # internet. The compile may already have run, and that is not Docker Hub.
    "#3 [ac-worldserver internal] load metadata for docker.io/library/ubuntu:24.04",
    "#3 DONE 0.9s",
    "#9 [ac-worldserver builder 4/9] RUN git clone --depth 1 https://github.com/a/b /src/b",
    "#9 0.512 Cloning into '/src/b'...",
    "#9 10.61 fatal: unable to access 'https://github.com/a/b/': dial tcp: lookup "
    "github.com: i/o timeout",
    '#9 ERROR: process "/bin/sh -c git clone --depth 1 https://github.com/a/b /src/b" did '
    "not complete successfully: exit code: 128",
    "------",
    " > [ac-worldserver builder 4/9] RUN git clone --depth 1 https://github.com/a/b /src/b:",
    "0.512 Cloning into '/src/b'...",
    "10.61 fatal: unable to access 'https://github.com/a/b/': dial tcp: lookup github.com: "
    "i/o timeout",
    "------",
    'failed to solve: process "/bin/sh -c git clone --depth 1 https://github.com/a/b /src/b" '
    "did not complete successfully: exit code: 128",
)


class _Builds:
    """The build seam: answers `results` in order, compiling (on the daemon) on a 0."""

    def __init__(self, rec: Recorder, daemon: _Daemon, clock: _Clock, *results: docker.AttachedRun):
        self.rec = rec
        self.daemon = daemon
        self.clock = clock
        self.results = list(results)
        self.at: list[float] = []

    def __call__(
        self, server_dir: Path, files: object, *, sink: object = None, cancel: object = None
    ) -> docker.AttachedRun:
        self.rec.calls.append("build")
        self.at.append(self.clock.now)
        result = self.results[min(len(self.at), len(self.results)) - 1]
        if result.returncode == 0:
            self.daemon.compiled()
        return result


def _rebuild_with(
    tmp_path: Path, *results: docker.AttachedRun, cancel: threading.Event | None = None
) -> tuple[_Builds, _Daemon, Path, _Clock, list[str], InstallerError | None]:
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    clock = _Clock()
    builds = _Builds(rec, daemon, clock, *results)
    said: list[str] = []
    failed: InstallerError | None = None
    try:
        for line in engine(
            rec,
            **{**_seams_of(rec, daemon), "build": builds},
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        ).rebuild(InstallOptions(server_dir=server_dir), cancel=cancel):
            said.append(line)
    except InstallerError as exc:
        failed = exc
    return builds, daemon, server_dir, clock, said, failed


def _lookup_failed(error: str) -> tuple[str, ...]:
    """The metadata step failing with `error`, in BuildKit's shape, nothing else in the tail."""
    head = 'failed to do request: Head "https://registry-1.docker.io/v2/library/ubuntu/manifests/24.04"'
    return (
        "#3 [ac-worldserver internal] load metadata for docker.io/library/ubuntu:24.04",
        f"#3 ERROR: {head}: {error}",
        "------",
        " > [ac-worldserver internal] load metadata for docker.io/library/ubuntu:24.04:",
        "------",
        "failed to solve: ubuntu:24.04: failed to resolve source metadata for "
        f"docker.io/library/ubuntu:24.04: {head}: {error}",
    )


@pytest.mark.parametrize(
    "tail",
    [
        pytest.param(HUB_UNREACHABLE, id="tls-handshake-timeout-yulon-win11"),
        pytest.param(
            _lookup_failed(
                "dial tcp: lookup registry-1.docker.io: Temporary failure in name resolution"
            ),
            id="temporary-failure-in-name-resolution",
        ),
        pytest.param(
            _lookup_failed("dial tcp 44.208.254.194:443: connect: connection timed out"),
            id="connection-timed-out",
        ),
        pytest.param(
            _lookup_failed("dial tcp 44.208.254.194:443: connect: no route to host"),
            id="no-route-to-host",
        ),
        pytest.param(
            _lookup_failed(
                "dial tcp: lookup registry-1.docker.io on 127.0.0.11:53: server misbehaving"
            ),
            id="server-misbehaving",
        ),
        pytest.param(_lookup_failed("EOF"), id="eof"),
        pytest.param(_lookup_failed("unexpected EOF"), id="unexpected-eof"),
        pytest.param(
            _lookup_failed("dial tcp 44.208.254.194:443: connect: connection refused"),
            id="connection-refused",
        ),
    ],
)
def test_a_build_that_cannot_reach_docker_hub_is_tried_again_after_30_seconds(
    tmp_path: Path, tail: tuple[str, ...]
) -> None:
    builds, daemon, server_dir, _clock, said, failed = _rebuild_with(
        tmp_path, docker.AttachedRun(1, tail), docker.AttachedRun(0, ("compiled",))
    )
    assert failed is None, failed
    assert len(builds.at) == 2, builds.at
    assert builds.at[1] - builds.at[0] >= 30.0, builds.at
    told = [line for line in said if HUB in line]
    assert len(told) == 1 and "ubuntu:24.04" in told[0] and "30 seconds" in told[0], said
    assert _new_build_live(daemon, server_dir), daemon.names


def test_a_build_that_cannot_reach_docker_hub_twice_says_so_and_that_nothing_was_compiled(
    tmp_path: Path,
) -> None:
    builds, daemon, server_dir, _clock, _said, failed = _rebuild_with(
        tmp_path, docker.AttachedRun(1, HUB_UNREACHABLE)
    )
    assert len(builds.at) == 2, builds.at
    assert failed is not None
    message = str(failed)
    assert HUB in message and "ubuntu:24.04" in message, message
    assert "nothing was compiled" in message, message
    assert "internet connection" in message, message
    assert "failed (exit" not in message, message
    _untouched(daemon, server_dir)


def test_a_stop_during_the_30_seconds_ends_the_rebuild_without_a_second_build(
    tmp_path: Path,
) -> None:
    stop = threading.Event()
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    clock = _Clock()
    builds = _Builds(
        rec, daemon, clock, docker.AttachedRun(1, HUB_UNREACHABLE), docker.AttachedRun(0, ())
    )

    def sleep(seconds: float) -> None:
        clock.sleep(seconds)
        stop.set()

    with pytest.raises(InstallerError) as raised:
        list(
            engine(
                rec,
                **{**_seams_of(rec, daemon), "build": builds},
                monotonic=clock.monotonic,
                sleep=sleep,
            ).rebuild(InstallOptions(server_dir=server_dir), cancel=stop)
        )
    assert len(builds.at) == 1, builds.at
    assert "stopped" in str(raised.value), raised.value
    assert clock.now < 30.0, clock.now
    _untouched(daemon, server_dir)


@pytest.mark.parametrize(
    "tail",
    [
        pytest.param(TAG_NOT_FOUND, id="the-base-image-name-was-not-found"),
        pytest.param(TOKEN_REFUSED, id="docker-hub-answered-with-a-refusal"),
        pytest.param(NETWORK_INSIDE_A_RUN_STEP, id="a-run-step-could-not-reach-the-internet"),
        pytest.param(("cc1plus: error: out of memory",), id="a-compile-error"),
    ],
)
def test_a_build_that_failed_for_another_reason_is_not_tried_again(
    tmp_path: Path, tail: tuple[str, ...]
) -> None:
    builds, daemon, server_dir, clock, said, failed = _rebuild_with(
        tmp_path, docker.AttachedRun(1, tail), docker.AttachedRun(0, ("compiled",))
    )
    assert len(builds.at) == 1, builds.at
    assert failed is not None
    assert HUB not in str(failed) and not [line for line in said if HUB in line], failed
    assert clock.now < 30.0, clock.now
    _untouched(daemon, server_dir)


# -- D3's other half: the opening note stops saying it fetches nothing --------


def test_the_opening_note_says_the_build_may_go_online_for_its_base_image(tmp_path: Path) -> None:
    rec = Recorder(images=True)
    server_dir = a_finished_install(rec, tmp_path)
    daemon = _daemon_for(server_dir)
    said = list(
        engine(rec, **_seams_of(rec, daemon)).rebuild(InstallOptions(server_dir=server_dir))
    )
    note = next(line for line in said if line.startswith("You can stop this at any time"))
    assert "does not fetch anything" not in note.lower(), note
    assert "Docker Hub" in note and "base image" in note, note
