"""Shared subprocess streaming wrapper.

Provides line-by-line stdout streaming so the UI can display install/build/
container output without buffering the whole run. Stderr is drained
concurrently on a background thread — this exists purely to avoid pipe-buffer
deadlock (a chatty stderr filling its OS pipe buffer while nobody reads it),
not to interleave stderr into the stream in real time: stderr lines are
yielded only after the process exits and stdout is exhausted — unless the
caller asks for `merge_stderr`, which puts both on one pipe for commands whose
real output IS stderr (BuildKit). See `stream()`'s docstring for the exact
ordering.
"""

from __future__ import annotations

import atexit
import errno
import importlib
import ntpath
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import weakref
from collections.abc import Callable, Generator, Iterator, Mapping
from pathlib import Path
from typing import TypeVar

from yulon import ansi, winjob
from yulon.after_stop import StopTookEffect
from yulon.log import get_logger

logger = get_logger(__name__)

# How long to wait for a terminated/killed child and its stderr-reader thread
# to actually finish, when a `stream()` generator is abandoned early.
_SHUTDOWN_TIMEOUT_SECONDS = 5.0


_AnyPopen = subprocess.Popen[str] | subprocess.Popen[bytes]
"""A child of either shape, because the two streaming entry points differ there.

`stream()` reads text pipes; `stream_progress()` reads BINARY ones, so that
universal-newline translation cannot rewrite git's carriage returns before the
split sees them. Everything the registry and the teardown do to a child —
`poll()`, `terminate()`, `wait()`, `kill()` — is the same for both, so they
share one registry rather than each keeping half of it.
"""


class _Child:
    """What the exit hook needs from a `stream()` it cannot close: the process it started.

    `_stream_lines()` fills `proc` in once `Popen` has returned, so it is None
    for a generator nobody has started — which has no child to end.

    `started_on` is the ident of the thread that got the generator as far as
    `Popen`, and it is filled in beside `proc` for the same reason: those two
    facts are what `end_streams_started_on()` needs and they become true at the
    same instant. It is the STARTING thread and not the creating one, because
    `stream()` is lazy — a generator built on the GUI thread and iterated on a
    worker's is the app's normal shape (`LogPanel`), and the child belongs to
    whoever is blocked reading it.
    """

    __slots__ = (
        "proc",
        "started_on",
        "ended",
        "job",
        "group",
        "stopping_since",
        "readers",
        "settled",
        "cut_short",
        "answered",
        "lock",
    )

    def __init__(self) -> None:
        self.proc: _AnyPopen | None = None
        self.job: winjob.Job | None = None
        """The Windows Job object `proc` was started in, if it joined one (T299)."""
        self.group: int | None = None
        self.readers: tuple[threading.Thread, ...] = ()
        """`stream_progress()`'s pipe readers: while one runs, a pipe is still held (T546)."""
        self.stopping_since: float | None = None
        """When a Stop's ending of this child began, under `lock`; None when none is under way.

        A claim stops a watcher's polls from re-sending SIGTERM while an ending
        runs (T541): `docker compose up` reads a second SIGTERM as "force". It
        is let go when the ending fails, and it lapses after
        `_RECLAIM_AFTER_SECONDS`, so a Stop that did not finish can be tried again
        (Codex's adversarial review of T541).
        """
        """The process group `proc` leads, off Windows, when it was started as one (T529)."""
        self.settled = False
        """Set under `lock` when `_finish` lets `job` go; a Stop then skips it."""
        self.cut_short = False
        """Set under `lock` when a Stop claims a stream that has a job: it reads as a Stop.

        The Stop ends the job, root or no root, so a root that exits 0 on its
        own a moment later has still had its descendants' work cut off.
        """
        self.answered = False
        """Set under `lock` when the stream reads its exit and gives its answer (`_answer`).

        From then on the answer stands: a Stop's choice skips the stream, so a
        command reported as a success never has its job ended by a late Stop
        (scoped review of d3252e19).
        """
        self.lock = threading.RLock()
        """Guards `ended` and `settled` between a Stop and `_finish` (T299).

        Its own lock and not `_LIVE_STREAMS_LOCK`, and RE-ENTRANT, because
        `_finish` runs in a generator's `finally`, and a generator is finalised
        on whatever thread drops its last reference: inside a registry walk, or
        in a GC pass. A thread holding a plain lock there froze for good (cold
        review). Re-entered, `_finish` simply settles first, and the Stop
        that was choosing finds the stream settled.
        """
        self.started_on: int | None = None
        self.ended = False
        """Set by `end_streams_started_on()` BEFORE it ends `proc` (T240): the exit is a Stop's."""


class StreamEnded(subprocess.CalledProcessError, StopTookEffect):
    """A stream's child exited non-zero because `end_streams_started_on()` ended it (T240).

    A `CalledProcessError`, so every caller that already treats a non-zero exit
    as a failure still does. Its own type for the caller that must not answer a
    Stop as if the command had failed on its own: `git.py`'s containerized clone
    fell back to host git when the Stop killed its docker CLI, and cloned the
    whole repository again after the player had pressed Stop (yulon-win11,
    2026-10-04).

    `StopTookEffect` (T250): the log panel reads it, and what is raised `from`
    it, as the Stop and not as a failure.
    """


def _exit_failure(
    child: _Child, returncode: int, command: list[str]
) -> subprocess.CalledProcessError:
    """The error for a child that exited `returncode`: `StreamEnded` if a Stop ended it."""
    if child.ended:
        return StreamEnded(returncode, command)
    return subprocess.CalledProcessError(returncode, command)


# Every `stream()` generator handed out and not yet collected, with the child
# it started. Weak in the generator, so holding one here can never be the
# reason a caller's generator stays alive, and so an abandoned generator that
# the cycle collector reaches during the program's normal life drops out on
# its own.
_LIVE_STREAMS: weakref.WeakKeyDictionary[Generator[str, None, None], _Child] = (
    weakref.WeakKeyDictionary()
)
_LIVE_STREAMS_LOCK = threading.Lock()

_STOPS_SENT: dict[int, int] = {}
"""How many Stops `end_streams_started_on()` was sent per thread ident, under the lock (T321)."""


def stops_sent_to(ident: int) -> int:
    """How many times a Stop was sent to thread `ident`, live stream or not (T321).

    `end_streams_started_on()` ends only what is live, and a Stop that lands while
    the thread is in a plain `run()` -- `docker create`, which no Stop may cut short
    (`container_end`) -- finds nothing to end. A caller that must not lose that
    Stop reads this before such a call and again after it: a different count is a
    Stop sent in between. A count, not a flag, so nothing has to be reset.
    """
    with _LIVE_STREAMS_LOCK:
        return _STOPS_SENT.get(ident, 0)


def _register(generator: Generator[str, None, None], child: _Child) -> None:
    """Put `generator` under the exit hook's eye, with the holder its body reports `proc` in."""
    with _LIVE_STREAMS_LOCK:
        _LIVE_STREAMS[generator] = child


def _end_child(
    proc: _AnyPopen,
    job: winjob.Job | None = None,
    *,
    bounded: bool = False,
    group: int | None = None,
) -> bool:
    """End `proc` if it is still running: on Windows its tree first, then terminate, then kill.

    True if it found `proc` running and ended it; False if `proc` had already
    exited, which a caller with a group to end then has to see to (`_finish`).

    **The tree is ended by `proc`'s Job object when it has one (T299)**, and by
    taskkill only when it has none or the job could not be ended. The job holds
    every process the child started, however late and whatever became of its
    parent; taskkill reaches what is still linked by parent pid at that moment.
    See `yulon.winjob` and `_end_tree`.

    **On Windows `terminate()` ends one process, and a Stop has to end a tree
    (T246).** Measured on yulon-win11 2026-10-05: a rebuild's child was
    docker.exe, which had started docker-compose.exe, which had started
    `docker-buildx.exe bake`. Stop ended docker.exe alone; the other two ran on
    as orphans for 12.5 minutes, BuildKit finished the build, and the live image
    tag moved 10 min 38 s after Stop. `taskkill /T /F` on docker.exe ended all
    three, the build ended in the engine as `Error`, and no tag moved. So the
    tree goes first, while docker.exe is still alive to anchor it: taskkill
    finds descendants by their parent's pid, and once docker.exe has been
    terminated its children's parent is gone.

    `terminate()`/`kill()` stay after it as the fallback, and are what a
    taskkill that failed, timed out or could not start leaves in charge.

    Off Windows the docker CLI alone is signalled, and on Linux that was
    measured to be enough (T298, 2026-10-06, yulon-ubuntu and m910q): SIGTERM,
    and SIGKILL too, left no compose or `buildx bake` process 2 s later, and the
    stopped build never tagged its image. The CLI closes its plugin's socket and
    compose ends its bake on that; both plugins ran in the CLI's own process
    group, so a group signal would also have reached them, had it been needed.
    macOS was not probed.

    **A host tool's child is another matter, and `group` is for it (T529).**
    Measured on m910q 2026-10-07: SIGTERM to `git clone` during `Resolving
    deltas` left `index-pack` running 13.8 s with PPID 1, and a blob-less
    `git checkout`'s own `git fetch` downloaded on for 36 s; each held the
    stream's pipe, so the Stop did not end the read until it exited.
    `stream_progress()` starts its child as the leader of a session of its own,
    and for such a child `group` is its pid: the whole group gets SIGTERM, and
    whatever of it is still there `_SHUTDOWN_TIMEOUT_SECONDS` later gets SIGKILL
    (`_end_group`). `stream()` is not given one: off Windows it only runs the
    docker CLI, measured above.

    `bounded` is for a caller that must not block for long (T365: a Stop on the
    thread that pressed it): the `kill()` is not waited for. Nothing is lost by
    that, because the stream's own `finally` reaps the child.
    """
    if proc.poll() is None:
        if sys.platform == "win32":
            if job is not None and job.end():
                # TerminateJobObject ended docker.exe with the rest: reap it
                # rather than terminate it a second time (Codex's third
                # adversarial review). A root still there is ended as before.
                try:
                    proc.wait(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
                    return True
                except subprocess.TimeoutExpired:
                    pass
            else:
                _end_tree(proc)
        elif group is not None and _end_group(proc, group, bounded=bounded):
            return True
        proc.terminate()
        try:
            proc.wait(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            if not bounded:
                proc.wait()
        return True
    return False


def _end_group(proc: _AnyPopen, group: int, *, bounded: bool) -> bool:
    """SIGTERM to `proc`'s whole group, and SIGKILL to what is left of it after the timeout (T529).

    The group is `proc`'s own: `_group_led_by()` gave it only for a child this
    module started as a session leader, so it holds `proc` and what `proc`
    started, and nothing else. POSIX does not hand out a pid while it is still
    some group's id, so the id stays this group's while any member lives; the
    SIGKILL is sent only while a member is still seen. False, having done
    nothing, for a group that cannot be signalled: `_end_child` then ends
    `proc` alone, as before T529.
    """
    deadline = time.monotonic() + _SHUTDOWN_TIMEOUT_SECONDS
    if not _group_still_ours(proc, group) or not _signal_group(group, signal.SIGTERM):
        return False
    try:
        proc.wait(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    while _group_still_ours(proc, group) and time.monotonic() < deadline:
        time.sleep(_GROUP_POLL_SECONDS)
    if _group_still_ours(proc, group):
        _signal_group(group, getattr(signal, "SIGKILL"))  # noqa: B009 - POSIX-only attribute
    if proc.poll() is None:
        proc.kill()
        if not bounded:
            proc.wait()
    return True


_GROUP_POLL_SECONDS = 0.05


def _group_still_ours(proc: _AnyPopen, group: int) -> bool:
    """Is process group `group` still the one `proc` led, with a member in it? (T546)

    While `proc` runs, or has exited unreaped, the id is pinned to it. Once it is
    reaped, POSIX keeps the id from reuse only while a member of the group lives, so
    a group left with members and no leader is still ours. A live process whose pid
    IS the id is not: ours was reaped, so that pid is someone else's, and so is any
    group it leads (Codex's adversarial review: a held pipe alone did not prove it).
    """
    if sys.platform == "win32":
        return False  # no process groups there; a Stop ends a job (T299)
    if proc.poll() is None:
        return True
    try:
        os.getpgid(group)
    except ProcessLookupError:
        return _signal_group(group, 0)  # no leader: members of ours, if any are left
    except OSError:
        return False
    return False


"""How often `_end_group` asks whether anything of a group it signalled is still there."""


def _signal_group(group: int, sig: int) -> bool:
    """Send `sig` to process group `group`; False when none of it is left or it cannot be asked."""
    killpg = getattr(os, "killpg")  # noqa: B009 - POSIX-only attribute; Windows has no groups
    try:
        killpg(group, sig)
    except ProcessLookupError:
        return False
    except OSError as exc:
        logger.warning(f"could not signal process group {group}: {exc!r}")
        return False
    return True


_REAL_POPEN = subprocess.Popen
"""The class a real child is, held before anything can stand in for it (`_group_led_by`)."""


def _group_led_by(proc: _AnyPopen) -> int | None:
    """`proc`'s pid if it is a real child that leads its own process group, else None (T529).

    Asked of the process rather than assumed from the spawn's arguments, so a
    group signal can never go to a pid that is not a child this module started
    as a session leader: a stand-in object, or a child whose session was not
    made, gets None and is ended as before.
    """
    if sys.platform == "win32" or not isinstance(proc, _REAL_POPEN):
        return None
    try:
        return proc.pid if os.getpgid(proc.pid) == proc.pid else None
    except OSError:
        return None


def _abandon_unread(proc: _AnyPopen, job: winjob.Job | None, group: int | None = None) -> None:
    """End a child whose reader could not start, before its stream's `finally` exists.

    From both Codex reviews of T299: the reader starts before the `try` whose
    `finally` ends the child, so a `RuntimeError: can't start new thread` left
    the tree running in a job nobody would close.
    """
    _end_child(proc, job, group=group)
    if job is not None:
        job.close()


def _finish(
    proc: _AnyPopen,
    job: winjob.Job | None,
    *,
    stopped: bool = False,
    child: _Child | None = None,
    drained: bool = True,
) -> None:
    """A stream's last word on its child: end it if it is running, then let its job go.

    A child that ran out by itself has its job RELEASED, so whatever it left
    running goes on as before jobs (Codex adversarial review of T299). One
    that was still running was abandoned: its tree is ended and the job
    closed, which also ends anything `_end_child`'s fallback could not reach.
    The job is closed only after it was asked to end the tree, because a
    closed job's handle can no longer end anything.

    **Ran out by itself means the root exited AND its output was read to the
    end (T495).** `drained` is False for a stream its consumer closed before
    EOF. Its root may have exited, but something still held the pipe, and that
    is the command's own work: docker.exe ended by hand while compose and
    `buildx bake` went on compiling into the pipe (yulon-win11-gate
    2026-10-06). The Stop reached that stream only as a cancel that closed it
    (the stream had been started on `native._pump()`'s worker, which the
    panel's `end_streams_started_on()` does not match), and a release here let
    the build compile on for 11 minutes. Such a job is closed. `interact()`
    passes nothing: it stops reading when its child exits, by design.

    A Stop overrides `poll()`: if docker.exe exits before the Stop's thread
    runs, a release here would leave the rest of the tree running (Codex's
    second adversarial review). For a registered stream that Stop is
    `child.ended`, read under `child.lock` at the moment of deciding,
    and `child.settled` is set in the same breath, so `end_streams_started_on()`
    either marks the child first (closed here) or finds it settled and leaves
    it (Codex's fourth). `stopped` is the same for `interact()`, which is not
    registered and whose cancel is its Stop.
    """
    # Read BEFORE `_end_child`: a root it has just ended through its group is
    # not one that had exited, and must not be sent the group's ending twice.
    exited = proc.poll() is not None
    ran_out = drained and exited
    group = child.group if child is not None else None
    try:
        ended_root = False if exited else _end_child(proc, job, group=group)
        if (
            child is not None
            and group is not None
            and not drained
            and not ended_root
            and _pipe_held(child)
        ):
            # T495's rule off Windows (T529): closed before EOF with its root gone --
            # gone before this looked, or as `_end_child` looked (Codex review) --
            # something of its group still held the pipe: the command's own work.
            _end_group(proc, group, bounded=False)
    finally:
        if child is not None and child.group is not None:
            # From here a Stop leaves this stream's group alone (`_group_unsettled`).
            with child.lock:
                child.settled = True
        # Whatever `_end_child` did, the job is let go (Codex's third review).
        if job is not None:
            if child is not None:
                with child.lock:
                    stopped = stopped or child.ended
                    child.settled = True
            if ran_out and not stopped:
                job.release()
            else:
                job.close()


def _taskkill() -> str:
    """The system's own taskkill.exe, by its full path rather than whatever PATH finds first."""
    return ntpath.join(os.environ.get("SystemRoot") or r"C:\Windows", "System32", "taskkill.exe")


def _end_tree(proc: _AnyPopen) -> None:
    """`taskkill /T /F` the tree under `proc`, bounded, never raising (T246; see `_end_child`).

    **Since T299 this is the fallback**: a child started in a Job object is
    ended by `TerminateJobObject` instead, and this runs only for a child that
    has no job (none could be made, or it could not join) or whose job could
    not be ended. `yulon.winjob` says how the job closes the gaps below: the
    child is created suspended and joins before it runs, so the race moves
    nowhere.

    What taskkill cannot reach, and why each is narrow (challenged by Codex's
    adversarial review, 2026-10-05):

    * a descendant whose parent had already exited. docker.exe does not exit
      before its plugin does: docker/cli's `tryPluginRun` blocks in
      `plugincmd.Run()` until compose returns. So a live compose under a dead
      docker.exe needs something else to have killed docker.exe, and this
      function only runs while `proc.poll()` says docker.exe is alive.
    * a process started between taskkill's snapshot and its kill. compose
      starts `buildx bake` once, at the start of a build, so the window is a
      Stop landing in the first moments of that one command.

    Popen holds docker.exe's handle until it is reaped, so `proc.pid` cannot be
    another process's pid while this runs.
    """
    try:
        command = [_taskkill(), "/T", "/F", "/PID", str(proc.pid)]
        done = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            env=child_env(),
            timeout=_SHUTDOWN_TIMEOUT_SECONDS,
            creationflags=creationflags(),
            check=False,
        )
    except Exception as exc:  # noqa: BLE001 - a Stop must reach terminate() whatever went wrong
        # Not only `OSError` and `TimeoutExpired`, the two `subprocess.run`
        # documents: an error of any kind escaping here would skip the
        # `terminate()`/`kill()` after it and lose the Stop (cold review N2).
        logger.warning(f"could not end the process tree of pid {proc.pid}: {exc!r}")
        return
    said = " ".join((done.stdout + done.stderr).split())
    if done.returncode:
        logger.warning(
            f"taskkill could not end the process tree of pid {proc.pid} "
            f"(exit {done.returncode}): {said}"
        )
    else:
        logger.debug(f"ended the process tree of pid {proc.pid}: {said}")


def _close_abandoned_streams() -> None:
    """Close every still-open `stream()` generator at `atexit` — or end its child, if it cannot.

    Measured on m910q, 2026-09-04, CPython 3.11.15: a driver that took five
    lines off `docker.follow_logs()` and `break`d, without closing the
    iterator, exited **134 (SIGABRT)**:

        Fatal Python error: _enter_buffered_busy: could not acquire lock for
        <_io.BufferedReader name=5> at interpreter shutdown, possibly due to
        daemon threads
        Python runtime state: finalizing
          Garbage-collecting
          File ".../yulon/runner.py", line 236 in stream

    The abandoned generator survived to interpreter shutdown and was finalised
    by the shutdown garbage collection. By then the `_drain_stderr` daemon
    thread was blocked in `read()` holding the `BufferedReader`'s lock and
    could no longer be resumed — a daemon thread that asks for the GIL after
    finalisation has begun is exited on the spot, lock still held. `join()`
    therefore timed out and `proc.stderr.close()` hit a lock nobody would ever
    release; CPython treats that as a fatal error rather than a hang.

    `atexit` callbacks run BEFORE that finalisation: the interpreter is intact
    and daemon threads still get the GIL. Two shapes were measured on the same
    box and day. A generator held SUSPENDED at a `yield` — the driver above —
    was closed here, its `finally` ran the terminate-and-join, and the driver
    exited 0. A generator whose frame another thread was RUNNING — the app's
    shape: `native._pump()`'s worker inside `docker.run_attached()`, blocked
    in `readline()` inside this generator — refused the close:

        ValueError: generator already executing

    That was logged at debug and nothing else happened: the `finally` never
    ran, and the child was still alive with PPID 1 after the driver had gone
    (`test_runner.py`, both shapes). A frame that is executing cannot be
    entered from here, so its CHILD is ended directly instead; the thread
    that owns the frame then leaves its read on EOF and runs the `finally`
    itself, on a process that has already exited.

    Rejected: **not closing `proc.stderr` when the reader is still alive.** It
    moves the abort rather than removing it — the `BufferedReader`'s
    deallocation calls `close()` itself, that call takes the same lock and
    reaches the same `_enter_buffered_busy`. Measured on the same box and the
    same reproducer: still 134, still `_enter_buffered_busy`, now reported at
    the end of the `finally` instead of at the `close()` line. Also rejected:
    making the reader a non-daemon thread, so `threading._shutdown()` would
    join it before finalisation. Nothing has terminated the child at that
    point, so the join lasts as long as the child does — a launcher that will
    not close is worse than one that aborts on close.
    """
    with _LIVE_STREAMS_LOCK:
        pending = list(_LIVE_STREAMS.items())
    for generator, child in pending:
        try:
            generator.close()
        except ValueError as exc:
            # Refused, not failed: another thread is inside this frame. The
            # child is ended whatever `gi_running` says NOW — the frame may
            # have reached its `yield` since the refusal, and a suspended
            # generator whose child has exited finishes on its own next
            # `next()`; one whose child is still running does not.
            logger.debug(f"an abandoned stream() is being run by another thread at exit: {exc}")
            if child.proc is not None:
                _end_child(child.proc, child.job, group=child.group)
        except BaseException as exc:  # noqa: BLE001 - exiting; nothing may escape
            # The generator's own `finally` failed. Logged rather than swallowed
            # silently, but never re-raised: an exception here would be reported
            # as an error during shutdown for a generator that is not ours to
            # police, and the generators after it in `pending` still need
            # closing.
            logger.debug(f"could not close an abandoned stream() at exit: {exc}")


atexit.register(_close_abandoned_streams)


def _still_running(proc: _AnyPopen | None) -> bool:
    """True for a child that has been started and has not exited.

    **A THREAD IDENT IS REUSED, and this is what stops that mattering.** The
    registry outlives the threads in it: an entry stays while its generator does,
    and a generator whose child has long exited is still referenced by whatever
    holds it. The OS then hands the same ident to the next thread, so
    `started_on == ident` can be true of a stream some earlier, unrelated thread
    started. Measured on `yulon-fedora` 2026-09-09 in the checks gate, where the
    suite runs the whole file in one process:
    `test_end_streams_started_on_ends_the_child_a_worker_thread_is_blocked_reading`
    read `assert 2 == 1` — a second, already-finished stream from an earlier test
    in this file had been attributed to the new worker.

    Asking the child settles it without needing to know which thread is alive: a
    child that has exited is nothing to end, and one that is running is the job
    the caller means. `poll()` is a non-blocking `waitpid`, safe to call while
    another thread is inside `readline()` on the same child.
    """
    return proc is not None and proc.poll() is None


def end_streams_started_on(ident: int) -> int:
    """End the child of every live `stream()` that thread `ident` started. Returns how many.

    **THE ONLY THING THAT CAN STOP A QUIET STREAM.** A thread inside
    `for line in proc.stdout` is in a C-level `readline()`: no flag it owns is
    read again until a line arrives, and a `docker logs -f` on a world that has
    gone quiet never sends one. Measured on `yulon-ubuntu2` 2026-09-08 through
    the panel's real Stop button
    (`.notes/gates/7.10-rerun-ubuntu2-2026-09-08/log-panel-stop-probe.txt`): 120
    seconds of `running=True cancelled=True worker._stop=True lines=210`, then
    `panel.wait(10000) -> False`, then `QThread: Destroyed while thread '' is
    still running` and exit 134. The same probe's synthetic source — one line
    every 50 ms — stopped 0.02 s after the click, which is how the torrent was
    ruled out and the blocked read ruled in. Ending the child closes the pipe,
    the read returns EOF, and the thread runs the generator's own `finally`.

    **Keyed on the thread, because a cancel token cannot get there.** `stream()`
    is reached through wrappers — `docker.follow_logs()`, `run_attached()` — and
    a token would have to be threaded through every one of them by every caller;
    the Console tab's call site passes none today, which is the other half of
    what the gate measured. A thread ident is something the caller of a job
    already has, and it says exactly what a Stop button means: end what MY job
    started. `docker.repair_import()`, whose docstring records that it "cannot
    be cancelled, deliberately", runs on its own worker and so stays out of
    reach — the filter is what keeps that promise, not the absence of a
    parameter.

    **It ends only what is live NOW.** A stream the same thread starts a moment
    later is not affected, so a cancelled install's cleanup and rollback steps
    still get to run. That is the difference between this and an ambient
    per-thread cancel, which was the alternative and would have killed them.

    **It does not wait, and that is the prior art's lesson, not ours.**
    `origin/rust-main:crates/dml-core/src/proc.rs`'s `abandon()` — read on the
    branch, because `pyplan/rust-prior-art.md` is silent on that file — says:
    "`kill()` can fail, and the `wait()` that followed it was then INFINITE
    against a process whose whole problem is that it outlives the deadline.
    Measured 2026-08-03: a 600ms-bounded call returned after 605 SECONDS."
    `_end_child()` ends in an unbounded `proc.wait()` and the caller here is the
    thread that painted the button, so each ending goes to a thread nobody
    joins — exactly `abandon()`'s shape. A thread that cannot start (`can't
    start new thread`, T365) does not lose the Stop: that child is ended here
    by `_stop_child_here()`, whose every wait is bounded, and the rest go on.

    **The status is still raised.** A terminated child exits non-zero (143 on
    the live box) and `stream()` goes on raising `CalledProcessError` for it:
    `docker.run_attached()` turns a raised status into a failed build and a
    swallowed one into `AttachedRun(0, ...)`, so suppressing it here would
    report a killed compile as a compile that worked. Whether a non-zero exit
    was a refusal or a Stop is known only where the Stop happened, and that is
    where it is said (`LogPanel._on_finished`).
    """
    with _LIVE_STREAMS_LOCK:
        _STOPS_SENT[ident] = _STOPS_SENT.get(ident, 0) + 1  # `stops_sent_to()` (T321)
        # Only copied out under the lock, and nothing else done there: the
        # dictionary is weak and every other reader takes the same lock, and a
        # generator can be finalised on this thread inside this very walk (its
        # last reference dropped elsewhere, or a GC pass), running its `finally`
        # here. Nothing that `finally` needs may be held (cold review of T299).
        mine = [child for child in _LIVE_STREAMS.values() if child.started_on == ident]
    ended = _end_chosen(mine)
    if ended:
        logger.debug(f"ending {ended} stream child(ren) started on thread {ident}")
    return ended


def end_stream(generator: Generator[str, None, None]) -> bool:
    """End the child of the ONE live stream `generator`, as a Stop would (T526). True if it did.

    `end_streams_started_on()` for a caller that holds its own stream and must
    not reach any other: `docker.run_attached()`'s cancel watcher. A thread can
    hold more than one live stream -- an outer job's, suspended at a line while
    it runs a command -- and a run's cancel is about that run alone (Codex's
    adversarial review). Not a Stop of the thread, so `stops_sent_to()` does not
    count it.
    """
    with _LIVE_STREAMS_LOCK:
        child = _LIVE_STREAMS.get(generator)
    return child is not None and _end_chosen([child]) == 1


def _end_chosen(candidates: list[_Child]) -> int:
    """Claim each of `candidates` that is a Stop's to end, and end it off this thread. How many."""
    children = []
    held = 0
    for child in candidates:
        # Chosen and marked under the child's own lock, so `_finish` cannot
        # decide between release and close in between (Codex's fourth
        # adversarial review of T299). Marked before the end is even asked for,
        # so the reading thread can never see this child's exit before it can
        # see why (T240).
        # `cut_short` in the same block, not on the Stop's thread: the reader
        # reads it under this lock in `_answer`, so either the Stop claims the
        # stream first and the reader reports a Stop, or the reader answers
        # first and the Stop leaves the stream alone (scoped review of d3252e19).
        # Only with a job or a group (T529): without one nothing ends a root
        # that has exited, and its exit status is the whole story, as before.
        with child.lock:
            if child.answered or _ending_under_way(child):
                # A Stop already claimed it and its ending is under way (T541).
                # Claimed again it got a new SIGTERM on every poll of a watcher, and
                # `docker compose up` reads a second SIGTERM as "force".
                continue
            if child.proc is None and child.started_on is not None:
                # Being started this instant (T546): the claim is held, and the
                # stream ends its child as soon as it has one (`_started`).
                if not child.ended:
                    child.ended = True
                    held += 1
                continue
            if _still_running(child.proc) or _job_unsettled(child) or _group_unsettled(child):
                child.ended = True
                child.stopping_since = time.monotonic()
                child.cut_short = child.job is not None or child.group is not None
                children.append(child)
    for child in children:
        _start_stopper(child)
    return len(children) + held


def _start_stopper(child: _Child) -> None:
    """End a claimed child on a thread of its own, or here, bounded, if none will start."""
    proc = child.proc
    assert proc is not None  # a claimed child has started; narrows for mypy
    try:
        threading.Thread(
            target=_stop_child_claimed,
            args=(child,),
            daemon=True,
            name=f"yulon-end-stream-{proc.pid}",
        ).start()
    except Exception as exc:  # noqa: BLE001 - any refusal must not lose this Stop or the next
        # T365: `RuntimeError: can't start new thread` in a process that has
        # run out of them. The child is already marked as ended, so it is
        # ended here instead, with every wait bounded.
        logger.warning(f"could not start a thread to end pid {proc.pid} ({exc!r}); ending it here")
        _stop_child_here(child)


def _started(
    child: _Child, proc: _AnyPopen, job: winjob.Job | None, *, group: int | None = None
) -> None:
    """Write down what a stream started; a Stop held while it started ends it now (T546).

    Under `child.lock`, the lock a Stop's claim is made under, so a Stop either finds
    the child here or held its claim before it existed -- never neither.
    """
    with child.lock:
        child.job = job
        child.group = group
        child.proc = proc
        held = child.ended and not child.answered
        if held:
            child.stopping_since = time.monotonic()
            child.cut_short = job is not None or group is not None
    if held:
        _start_stopper(child)


_RECLAIM_AFTER_SECONDS = 2 * _SHUTDOWN_TIMEOUT_SECONDS
"""How long a Stop's claim on a child holds before another Stop may try again (T541).

Twice `_end_child`'s own escalation (SIGTERM, then SIGKILL after the timeout), so a
graceful ending gets its whole window before anything is sent a second time."""


def _ending_under_way(child: _Child) -> bool:
    """Is a Stop's ending of `child` running now? Read under `child.lock` (T541)."""
    since = child.stopping_since
    return since is not None and time.monotonic() - since < _RECLAIM_AFTER_SECONDS


def _stop_child_claimed(child: _Child) -> None:
    """`_stop_child` on its own thread; a failure lets the claim go, so the next Stop retries."""
    try:
        _stop_child(child)
    except Exception as exc:  # noqa: BLE001 - logged; the next Stop tries again
        proc = child.proc
        logger.warning(f"ending the stream child pid {proc.pid if proc else None} failed: {exc!r}")
        with child.lock:
            child.stopping_since = None


def _job_unsettled(child: _Child) -> bool:
    """A child whose root may have exited but whose job its stream has not let go yet (T299).

    The root's descendants inherit its output pipe, so a stream reads on after
    docker.exe exits for as long as they write. That stream is still the job a
    Stop means, and its job can end the tree without the root. Once `_finish`
    has settled the job — released or closed — the stream is history.
    """
    return child.proc is not None and child.job is not None and not child.settled


def _group_unsettled(child: _Child) -> bool:
    """`_job_unsettled` for a POSIX group (T529, Codex's review): its root may have exited.

    A member that holds the output pipe keeps the stream reading after its leader
    is gone, so it is still the job a Stop means. Settled by `_finish`, and asked
    of the group itself: one with no member left is nothing to end.
    """
    return (
        child.proc is not None
        and child.group is not None
        and not child.settled
        and _pipe_held(child)
        and _group_still_ours(child.proc, child.group)
    )


def _pipe_held(child: _Child) -> bool:
    """Does something still hold this stream's pipes? Only then is its group id still its own.

    T546. A group id is free again once its last member is gone, and any new group
    leader can be given it. A stream whose readers have reached EOF has nothing left
    holding its pipes, so its group may be long gone -- a stream that ran out and was
    never closed stays registered and unsettled for as long as something holds it --
    and nothing may be sent to that id. While a reader runs, a writer is alive, and a
    member that holds the pipe keeps the id this group's. (A descendant that left the
    session and kept the pipe would not; git's detached helpers close theirs.)
    """
    # A reader not started yet counts as holding: its pipe is open and unread.
    return any(reader.is_alive() or reader.ident is None for reader in child.readers)


def _stop_child(child: _Child, *, bounded: bool = False) -> None:
    """A Stop's ending: `_end_child`, or the job (T299) or group (T529) alone after the root exited.

    The choice in `end_streams_started_on()` already set `cut_short` for a
    child with a job or a group, so whatever this ends, the stream reports a Stop.
    `bounded` is passed on to `_end_child`.
    """
    proc, job = child.proc, child.job
    assert proc is not None  # chosen streams have started
    if proc.poll() is None:
        _end_child(proc, job, bounded=bounded, group=child.group)
    elif job is not None:
        job.end()
    elif child.group is not None and _pipe_held(child):
        # The leader has exited and something of its group may still read on (T529),
        # which a held pipe proves; one nobody holds may be another's id (T546).
        _end_group(proc, child.group, bounded=bounded)


def _stop_child_here(child: _Child) -> None:
    """`_stop_child` on the Stop's own thread, for when no thread would start for it (T365).

    Bounded, because that thread is the one that painted the button: each wait
    in `_end_child` has `_SHUTDOWN_TIMEOUT_SECONDS`, and the kill's reap is left
    to the stream's `finally`. It never raises, so one child that cannot be
    ended does not keep the Stop from the next.

    What it cannot do (cold review of T365): on Windows a child with no job is
    ended through taskkill, and `_end_tree` reads taskkill's output with
    threads; with none to start, it falls through to `terminate()`, which ends
    docker.exe alone, the T246 case. That needs no job and no threads at once.
    """
    try:
        _stop_child(child, bounded=True)
    except Exception as exc:  # noqa: BLE001 - the Stop goes on to the other streams
        proc = child.proc
        pid = proc.pid if proc is not None else None
        logger.warning(f"could not end the stream child pid {pid}: {exc!r}")
        with child.lock:
            child.stopping_since = None  # the next Stop tries again (T541)


def _answer(child: _Child) -> bool:
    """Close the stream's answer to a Stop; True if a Stop claimed it first (`cut_short`).

    Read and marked under `child.lock`, the lock the Stop's choice holds, so
    the answer and the choice cannot interleave: see `_Child.answered`.
    """
    with child.lock:
        child.answered = True
        return child.cut_short


def _cwd_arg(cwd: Path | None) -> str | None:
    """Convert an optional Path working dir to the str `subprocess` expects."""
    return str(cwd) if cwd is not None else None


def creationflags() -> int:
    """`CREATE_NO_WINDOW` on native Windows, 0 elsewhere (roadmap 6.3).

    Every subprocess this module spawns is a console-less utility (git, docker
    compose, mysql, curl), and native Windows gives each a console window by
    default — a window that flashes over the launcher UI on every one of the
    dozen short-lived commands a single server action runs, and that a user can
    close while the build it belongs to is still running (`rust-prior-art.md`
    §4, "spawn with CREATE_NO_WINDOW or consoles flash over the UI").

    Public (not `_`-prefixed) because the three spawn sites that do not go through
    this module's `run()`/`stream()`/`interact()` — `apply.py`'s SQL runner,
    `maintenance.py`'s `docker exec`, and `console.py`'s `docker attach` client
    (`popen=subprocess.Popen`) — must apply the same flag, and a flag applied to
    some spawn sites but not others is a window that flashes anyway. `console.py`'s
    attach is POSIX-only by `pty_supported()`, so the flag is inert there today;
    it is carried anyway so the one place that *can* add a Windows console does
    not silently become the exception. The same reason `git.CONTAINER_GIT_IMAGE`
    is public: a value that must be shared exactly rather than re-derived.

    Fetched off `subprocess` at call time rather than imported at module scope,
    because `CREATE_NO_WINDOW` exists only on Windows and this module is
    type-checked for POSIX too — the same reason `pty_supported()` does not name
    `openpty` directly. `sys.platform` is checked, not `hasattr`, so a future
    stdlib flag rename cannot silently turn a no-window child into a windowed
    one without this branch noticing.
    """
    if sys.platform != "win32":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW"))  # noqa: B009 - Windows-only attribute


_FROZEN_LIBRARY_VARS = (
    "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",
    "DYLD_FRAMEWORK_PATH",
    "LIBPATH",
)
"""Loader search paths PyInstaller rewrites, and children must not inherit.

One per platform family - Linux/BSD, macOS (two of them), AIX - listed together
because the bug is one bug and a fix applied to some of them is a fix on some
platforms only.
"""


def child_env(env: Mapping[str, str] | None = None) -> dict[str, str] | None:
    """The environment a spawned child should actually get.

    THE PACKAGED LAUNCHER BREAKS THE TOOLS IT SHELLS OUT TO, and this is where
    that stops. PyInstaller points `LD_LIBRARY_PATH` at the bundle's own
    `_internal` directory so the frozen interpreter finds its libraries. Every
    child process inherits it, and a system binary that links anything the
    bundle also ships then loads the BUNDLE's copy. Measured inside the v0.6.5
    AppImage on Arch, 2026-08-24:

        bash  -> symbol lookup error: undefined symbol: rl_print_keybinding
        curl  -> libssl.so.3: version `OPENSSL_3.2.0' not found
                 (required by /usr/lib/libcurl.so.4)
        git   -> libpcre2-8.so.0: no version information available

    bash died outright, so `installer.bash_available()` - which ran
    `bash -c "exit 0"` and was the FIRST subprocess an install made - answered
    False, and the user was told "this machine has no working bash" about a
    machine whose bash was fine. That probe went with the bash engine in 7.2;
    the leak is unchanged and still reaches every child. curl loses HTTPS
    entirely, which is what "refuses to download files" looks like from the
    outside.

    PyInstaller saves the pre-launch value as `<VAR>_ORIG` for exactly this
    purpose. Restoring it (or removing the variable when there was nothing to
    restore) hands the child the environment it would have had if the user had
    typed the command themselves.

    Returns None when not frozen, so a source checkout keeps inheriting this
    process's environment exactly as before - the bug does not exist there, and
    neither should the fix. That asymmetry is also why the whole test suite and
    the CLI harness stayed green while the shipped artifact could not run
    `bash`: they never run frozen.
    """
    if not getattr(sys, "frozen", False):
        return dict(env) if env is not None else None
    base = dict(env) if env is not None else dict(os.environ)
    for var in _FROZEN_LIBRARY_VARS:
        original = base.pop(f"{var}_ORIG", None)
        if original:
            base[var] = original
        else:
            # No _ORIG, or an empty one: the variable was unset before the
            # bundle set it, so unset is what the child should see. Leaving an
            # empty string behind is not the same thing - an empty
            # LD_LIBRARY_PATH means "the current directory" to some loaders.
            base.pop(var, None)
    return base


_Popen = TypeVar("_Popen", subprocess.Popen[str], subprocess.Popen[bytes])


def _spawn(start: Callable[[int], _Popen]) -> tuple[_Popen, winjob.Job | None]:
    """Start a child through `start(creationflags)`; on Windows, inside a Job object (T299).

    The child is created suspended, joins the job, and only then runs, so
    nothing it starts can be outside the job (see `yulon.winjob`). No job — none
    could be made, or the child could not join — leaves it as before, ended by
    taskkill. A child the job cannot resume would never run: it is killed, and
    the `OSError` goes to the caller as a command that could not start. Off
    Windows this is `start(creationflags())`.
    """
    job = winjob.create() if sys.platform == "win32" else None
    flags = creationflags() | (winjob.CREATE_SUSPENDED if job is not None else 0)
    try:
        proc = start(flags)
    except BaseException:
        if job is not None:
            job.close()
        raise
    if job is None:
        return proc, None
    try:
        joined = job.start(proc.pid)
    except BaseException:
        proc.kill()
        proc.wait()
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            if pipe is not None:
                pipe.close()
        job.close()
        raise
    if not joined:
        job.close()
        return proc, None
    return proc, job


def stream(
    command: list[str],
    cwd: Path | None = None,
    *,
    merge_stderr: bool = False,
    env: Mapping[str, str] | None = None,
) -> Generator[str, None, None]:
    """Run a command, yielding stdout lines live and stderr lines at the end.

    Stdout lines are yielded one at a time as they arrive. Stderr is **not**
    interleaved in real time — it is drained on a background thread purely to
    prevent a full pipe buffer from deadlocking the child, then yielded in one
    block after stdout is exhausted and the process has exited. Each yielded
    line has its trailing newline removed.

    If the caller abandons the generator before it's exhausted (e.g. `break`s
    out of a `for line in stream(...):` loop, or the generator is garbage
    collected), the child process is terminated (escalating to `kill()` after
    a timeout) and the stderr-reader thread is joined before the generator
    finishes unwinding — the child and thread are never silently orphaned.
    That is why the return type is a `Generator` and not an `Iterator`: the
    `close()` is part of the contract, and a caller that stops early should say
    so (`contextlib.closing`) rather than leave it to refcounting.

    A caller that does NOT say so is still owed an exit that is not a crash.
    Every generator handed out is registered weakly in `_LIVE_STREAMS`, and
    `_close_abandoned_streams()` closes whatever is left at `atexit` — the last
    moment at which the cleanup above can still run — or, for a frame that
    another thread is executing right then, ends the child it started. Its
    docstring carries the measured abort, the measured refusal, and the two
    alternatives that were rejected. This function is a plain function
    returning a generator, not a generator function, purely so the
    registration happens at the call; nothing else about it changed — the body
    is still lazy, so no process starts until the first `next()`. Both halves
    of that sentence are owned by
    `test_runner.py::test_stream_registers_at_the_call_and_starts_no_process_until_the_first_next`,
    added 2026-09-05 when the meta review found the laziness half claimed by a
    comment and asserted by nothing.

    Args:
        command: The argv list to execute (no shell interpolation).
        cwd: Optional working directory for the child process.
        merge_stderr: Send the child's stderr into the SAME pipe as its stdout,
            so both are yielded live and in the order the child wrote them.
            Added for the native install engine's build stage (roadmap 6.2):
            BuildKit writes all of its progress to stderr, and the default
            ordering above turns a two-to-four-hour compile into a blank log
            panel that only fills in once the build has already finished.
            Interleaving costs the ability to tell the two streams apart, which
            is why it is opt-in: every existing caller reads a command whose
            stderr is an error report rather than its output.
        env: The child's WHOLE environment, or None to inherit this process's.
            Either way it goes through `child_env()`. Added for T376, whose
            build hands each compose call its own `BUILDX_CONFIG`.

    Yields:
        Each output line (all of stdout, in order, then any stderr) as a
        string without a trailing newline. With `merge_stderr`, one interleaved
        stream in the child's own order.

    Raises:
        subprocess.CalledProcessError: If the command exits non-zero (only
            raised if the generator is fully exhausted normally).
            `StreamEnded`, a subclass, when `end_streams_started_on()` ended it.
        OSError: If `command`'s executable cannot be found/started (propagates
            directly from `subprocess.Popen`).
    """
    child = _Child()
    generator = _stream_lines(command, cwd, merge_stderr=merge_stderr, env=env, child=child)
    _register(generator, child)
    return generator


def _stream_lines(
    command: list[str],
    cwd: Path | None = None,
    *,
    merge_stderr: bool = False,
    env: Mapping[str, str] | None = None,
    child: _Child,
) -> Generator[str, None, None]:
    """`stream()`'s body. Private so that no caller can skip the registration."""
    logger.debug(f"stream() called: command={command} cwd={cwd} merge_stderr={merge_stderr}")
    # Before the spawn (T546): the child runs the moment `Popen` returns, and a Stop
    # in between has to find this stream to hold its claim for it (`_started`).
    child.started_on = threading.get_ident()
    proc, job = _spawn(
        lambda flags: subprocess.Popen(
            command,
            cwd=_cwd_arg(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_env(env),
            creationflags=flags,
        )
    )
    _started(child, proc, job)
    stderr_lines: list[str] = []

    def _drain_stderr() -> None:
        assert proc.stderr is not None
        stderr_lines.extend(line.rstrip("\n") for line in proc.stderr)

    # With the streams merged there is no second pipe to drain, and starting a
    # reader on `proc.stderr` (which is then None) would raise inside the
    # thread rather than at the call site.
    reader: threading.Thread | None = None
    if not merge_stderr:
        reader = threading.Thread(target=_drain_stderr, daemon=True)
        try:
            reader.start()
        except BaseException:
            _abandon_unread(proc, job)
            raise

    drained = False  # set at EOF; a stream closed before it did not run out (T495)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            yield line.rstrip("\n")
        drained = True

        if reader is not None:
            reader.join()
        proc.wait()
        yield from stderr_lines

        # `_answer` first and always: it is what closes the stream to a Stop.
        if _answer(child) or proc.returncode:
            # 1, the code a terminated child leaves on Windows, for a root that
            # exited 0 although a Stop had claimed its tree (`_answer`).
            raise _exit_failure(child, proc.returncode or 1, command)
    finally:
        # Runs on normal completion (all no-ops below, since the process has
        # already exited and the reader thread has already finished) AND on
        # early abandonment via GeneratorExit — where it does the real work of
        # not leaking a running child process or a stuck reader thread.
        _finish(proc, job, child=child, drained=drained)
        if reader is not None:
            reader.join(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()


_FRAGMENT = re.compile(rb"[\r\n]")
"""What ends one fragment of a child's output. BOTH separators, and `\r` is the point.

Measured from a real `git clone --progress` of `github.com/psf/requests`,
recorded on 2026-09-12 into `tests/fixtures/git-clone-progress.stderr`: 9958
bytes carrying 209 carriage returns and 7 newlines. Git ended every progress
update with `\r` so that a terminal would overwrite the reading in place, and
wrote `\n` only when a phase was done — so `Receiving objects` from 0% to 100%
was 103 readings inside ONE newline-terminated line. Splitting on newlines
alone yields that whole phase as a single fragment whose only visible
percentage is the last, which is a progress bar that jumps from nothing to
finished.
"""

_READ_SIZE = 1
"""How much of a pipe is read at a time, and why it is one byte.

A fragment has to be yielded when it ARRIVES, and git's progress readings are
seconds apart. `read(n)` on a pipe blocks until it has n bytes or the child
exits, so any larger number holds the last reading back until the next one
pushes it out; `read1()` would return early but does not exist on the
`io.StringIO`/`io.BytesIO` doubles the tests serve recordings through. A clone's
stderr is tens of kilobytes, so the cost of asking per byte is a few tens of
thousands of Python calls spread over minutes.
"""


def stream_progress(
    command: list[str], cwd: Path | None = None, env: Mapping[str, str] | None = None
) -> Generator[str, None, None]:
    """Run a command, yielding BOTH pipes live, split on carriage returns as well as newlines.

    `stream()` for the one command whose real output is stderr with no newlines
    in it. It exists rather than a flag on `stream()` because the two differ in
    every respect that matters: this one reads both pipes on their own threads
    and merges them through a queue, and it cuts a fragment at `\r` — which
    `stream()` must never do, since a `\r` inside a line of a build log is part
    of that line.

    **It does not promise the child's own ordering across the two pipes**, and
    could not: two independent reader threads race, so a stdout fragment and a
    stderr fragment written a microsecond apart can be queued either way round.
    What holds is the order WITHIN each pipe. That is enough for git, whose
    progress is all stderr and whose stdout is silent, and it is the claim this
    docstring made too strongly until the 2026-09-12 review.

    Git is what needs it, and `_FRAGMENT` carries the recording that says why.
    `stream(merge_stderr=True)` was tried first and is not enough: merging puts
    both pipes in the child's own order, but the fragments still arrive as one
    newline-terminated line per phase, so the clone stage went from silence to
    "done" with nothing in between.

    `env` is the complete environment for the child, through `child_env()` and
    with `run()`'s meaning: it REPLACES this process's rather than adding to it,
    so a caller that only wants a variable added copies `os.environ` and extends
    it. `git.py` passes `_no_prompt_env()`, which is that copy plus the four
    variables that stop a credential helper opening a prompt — the guard
    `_run_git()` has always had, and the one this function went without until
    the 2026-09-12 review.

    Yields:
        Each fragment of either stream, with its own separator removed, decoded
        UTF-8 and undecodable bytes replaced. **Order holds WITHIN each pipe and
        not across the two**: the two pipes are read by independent threads, so
        a stdout fragment and a stderr fragment written a microsecond apart can
        arrive either way round.

    Raises:
        subprocess.CalledProcessError: if the command exits non-zero, AFTER
            everything it wrote has been yielded — the tail of git's stderr is
            what says why a clone failed.
            `StreamEnded`, a subclass, when `end_streams_started_on()` ended it.
        OSError: if `command` cannot be started (propagates from `Popen`).

    Registered in `_LIVE_STREAMS` exactly as `stream()` is, so
    `end_streams_started_on()` can end the clone a `LogPanel` Stop was pressed
    on, and `_close_abandoned_streams()` can end one nobody closed. The
    registration is why this is a plain function returning a generator.
    """
    child = _Child()
    generator = _progress_lines(command, cwd, env, child=child)
    _register(generator, child)
    return generator


def _progress_lines(
    command: list[str],
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    *,
    child: _Child,
) -> Generator[str, None, None]:
    """`stream_progress()`'s body. Private so that no caller can skip the registration."""
    logger.debug(f"stream_progress() called: command={command} cwd={cwd}")
    child.started_on = threading.get_ident()  # before the spawn; see `_stream_lines` (T546)
    # Binary pipes, deliberately. `text=True` puts both through universal-newline
    # translation, which rewrites every `\r` as `\n` before this function can
    # see it — and then the carriage returns this exists for are gone, silently,
    # with the split still looking right.
    # T529: off Windows the child leads a session of its own, so a Stop can end
    # what it starts (`_end_child`'s `group`); with no terminal, and stdin
    # closed, a git that asks for input fails instead of waiting.
    own_session = sys.platform != "win32"
    proc, job = _spawn(
        lambda flags: subprocess.Popen(
            command,
            cwd=_cwd_arg(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=child_env(env),
            creationflags=flags,
            stdin=subprocess.DEVNULL if own_session else None,
            start_new_session=own_session,
        )
    )
    fragments: queue.Queue[str | None] = queue.Queue()

    def read(pipe: object) -> None:
        held = b""
        try:
            while True:
                byte = pipe.read(_READ_SIZE)  # type: ignore[attr-defined]
                if not byte:
                    break
                if _FRAGMENT.match(byte):
                    if held:
                        fragments.put(held.decode("utf-8", errors="replace"))
                    held = b""
                else:
                    held += byte
        except (OSError, ValueError) as exc:
            # A pipe closed under the reader: the generator's `finally` ended the
            # child and closed it. Not a failure of the command.
            logger.debug(f"a stream_progress() pipe stopped reading: {exc}")
        if held:
            fragments.put(held.decode("utf-8", errors="replace"))
        fragments.put(None)

    readers = [
        threading.Thread(target=read, args=(pipe,), daemon=True)
        for pipe in (proc.stdout, proc.stderr)
    ]
    child.readers = tuple(readers)
    # Written down only now, readers and all (Codex review): a Stop held while the
    # child started is acted on here, and its stopper reads `_pipe_held()`.
    _started(child, proc, job, group=_group_led_by(proc))
    try:
        for reader in readers:
            reader.start()
    except BaseException:
        _abandon_unread(proc, job, child.group)
        raise

    drained = False  # set once both pipes reached EOF; see `stream()` (T495)
    try:
        done = 0
        while done < len(readers):
            item = fragments.get()
            if item is None:
                done += 1
                continue
            yield item
        drained = True
        for reader in readers:
            reader.join()
        proc.wait()
        # `_answer` first and always: it is what closes the stream to a Stop.
        if _answer(child) or proc.returncode:
            # 1, the code a terminated child leaves on Windows, for a root that
            # exited 0 although a Stop had claimed its tree (`_answer`).
            raise _exit_failure(child, proc.returncode or 1, command)
    finally:
        # `stream()`'s teardown, for `stream()`'s reasons: a caller that
        # abandoned this generator must not leave a clone running or a reader
        # thread stuck on a pipe.
        _finish(proc, job, child=child, drained=drained)
        for reader in readers:
            reader.join(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None:
                pipe.close()


def run(
    command: list[str],
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    *,
    stdin: int | None = subprocess.DEVNULL,
) -> subprocess.CompletedProcess[str]:
    """Run a command to completion and return the completed process.

    Captures output (text mode, UTF-8) rather than streaming it. Use `stream()`
    when line-by-line output is needed; use this for fire-and-collect calls.

    Args:
        command: The argv list to execute (no shell interpolation).
        cwd: Optional working directory for the child process.
        env: Complete environment for the child, or None to inherit this
            process's. Callers that only want to *add* a variable should copy
            `os.environ` and extend it — this replaces the environment wholesale,
            exactly like `subprocess.run`. It exists so a secret can be handed
            over the environment instead of argv (`/proc/<pid>/cmdline` is
            world-readable on Linux; `environ` is not), and so a child can be
            told not to prompt.
        timeout: Seconds to wait before giving up, or None for no bound. A
            timeout is reported as a non-zero `returncode` with the reason in
            `stderr`, not raised, so every existing caller keeps its shape.
            There is nothing sound to default this to — a `compose up` may take
            minutes — so it is per-call, and the callers that need one are the
            ones running on the GUI thread.
        stdin: Optional standard input stream or descriptor (defaults to DEVNULL).

    Returns:
        The completed process (stdout/stderr available as strings). Does not
        raise on non-zero exit — inspect `returncode`.
    """
    logger.debug(f"run() called: command={command} cwd={cwd}")
    try:
        proc = subprocess.run(
            command,
            cwd=_cwd_arg(cwd),
            env=child_env(env),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
            creationflags=creationflags(),
            stdin=stdin,
        )
    except subprocess.TimeoutExpired as exc:
        _note_unanswered(command, timeout)
        return subprocess.CompletedProcess(
            command, TIMED_OUT_RETURNCODE, _as_text(exc.stdout), f"{_TIMED_OUT} {timeout}s"
        )
    _note_answered(command)
    return proc


TIMED_OUT_RETURNCODE = 124
"""The `returncode` `run()` reports when its `timeout` passed (`timeout(1)`'s own)."""

_TIMED_OUT = "timed out after"

_UNANSWERED: set[tuple[str, ...]] = set()
"""The command lines whose last `run()` timed out, under `_UNANSWERED_LOCK`.

Worker threads run commands (the realm poll among them), so it is locked."""
_UNANSWERED_LOCK = threading.Lock()


def timed_out(proc: subprocess.CompletedProcess[str]) -> bool:
    """Whether `proc` is `run()` giving up at its `timeout`, not a command that exited 124."""
    return proc.returncode == TIMED_OUT_RETURNCODE and proc.stderr.startswith(f"{_TIMED_OUT} ")


def _note_unanswered(command: list[str], timeout: float | None) -> None:
    """Log a timeout once per change: the same command line timing out again is not news.

    With Docker Desktop's engine away the realm poll timed out every 35 s, and
    each said "docker did not answer within 30.0s; giving up" again: nine
    identical lines in five minutes on PR 291's Windows live test (2026-10-04).
    """
    key = tuple(command)
    with _UNANSWERED_LOCK:
        repeat = key in _UNANSWERED
        _UNANSWERED.add(key)
    if repeat:
        logger.debug(f"{command[0]} still did not answer within {timeout}s")
        return
    logger.warning(f"{command[0]} did not answer within {timeout}s; giving up")


def _note_answered(command: list[str]) -> None:
    """A command line that timed out has answered: say so once, and forget the timeout."""
    with _UNANSWERED_LOCK:
        if not _UNANSWERED:
            return
        try:
            _UNANSWERED.remove(tuple(command))
        except KeyError:
            return
    logger.info(f"{command[0]} answers again")


def _as_text(captured: object) -> str:
    """`TimeoutExpired.stdout` is bytes even in text mode, and may be None."""
    if isinstance(captured, bytes):
        return captured.decode("utf-8", errors="replace")
    return captured if isinstance(captured, str) else ""


# A prompt-answering callback for `interact()`: gets each output line (and each
# quiet partial line, i.e. a prompt with no trailing newline) with ANSI colour
# codes stripped, returns the text to send to stdin (without newline) or None.
Responder = Callable[[str], str | None]

# Asked when the child prints the exact marker the CALLER chose, and never
# otherwise. Returning None means "I cannot answer this either", and the caller
# is left to cancel.
#
# There used to be a heuristic here: a partial line ending in one of : ? > ]
# after a moment's quiet was taken for a prompt. Measured against real build
# output it fires on "[ 43%]", "Get:12 ... [345 kB]", "note:", "#12 sha256:abc
# [2/5]" and every gcc diagnostic — and `interact()` reads in 4096-byte chunks,
# so a chunk boundary landing on one of those during a 2-4 hour compile is
# routine rather than exotic. The result was an application-modal dialog that
# blocked the Stop button, quoted a fragment of compiler output, and wrote
# whatever was typed into the build's stdin.
#
# So the guess is gone. The one prompt that actually needed answering is sudo's,
# and sudo lets the caller choose its wording through SUDO_PROMPT — an exact,
# unguessable string no compiler will ever print (review, 2026-08-22).
Prompter = Callable[[str], str | None]


# How long a partial line must sit unchanged before it is looked at.
_PROMPT_QUIET_SECONDS = 0.3

# Yielded by `interact()` when its reader's end-of-stream sentinel was never
# consumed, so a cut-off log says it is cut off instead of just stopping.
_OUTPUT_MAY_BE_CUT_OFF = "[Yu'lon] The rest of this output may be cut off: it was still arriving."


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences (the install scripts colour everything); see `yulon.ansi`."""
    return ansi.strip(text)


def pty_supported() -> bool:
    """True where a pseudo-terminal can be opened (POSIX). False on Windows.

    Only `openpty` is required. It briefly also demanded `os.login_tty`, which
    is Python 3.11+, but `console.py` shares this predicate and needs no such
    thing — a 3.10 interpreter would have been told "this platform has no
    pseudo-terminal", which is false (review, 2026-08-22).
    """
    return hasattr(os, "openpty")


def open_pty() -> tuple[int, int]:
    """`os.openpty()`, fetched dynamically because it does not exist on Windows.

    Callers must check `pty_supported()` first. Lives here rather than in a
    per-game package because two unrelated features need a terminal: the
    worldserver console (docker refuses to attach to a non-TTY) and the
    installer (sudo reads its password from /dev/tty, never from stdin) —
    style-guide §4.
    """
    open_it = getattr(os, "openpty")  # noqa: B009 - POSIX-only attribute
    master, slave = open_it()
    return int(master), int(slave)


# Claim the pty as the controlling terminal WITHOUT running Python after fork.
#
# `/dev/tty` is the whole point: sudo deliberately does not read its password
# from stdin, so a child holding a pty on fd 0 but with no CONTROLLING terminal
# still fails with "a terminal is required to read the password". Inheriting a
# pty is not enough — the session has to be claimed.
#
# The obvious way to claim it is `preexec_fn=lambda: os.login_tty(slave)`. That
# is a Python callback executed between fork and exec, in a process whose Qt GUI
# thread and job threads are still live: closure read, global lookup, frame
# creation, `getattr`, an args tuple — every step can allocate, and an allocator
# lock held by another thread at fork time wedges the child there while the
# parent blocks inside Popen, before `interact()`'s cancel loop is ever entered.
# CPython's own comment at that call site reads "This is where the user has
# asked us to deadlock their program" (review, 2026-08-22).
#
# So the claim is delegated to `sh`, after exec, where no Python is involved:
# `start_new_session=True` calls setsid() in C, leaving a session leader with no
# controlling terminal, and re-`open()`ing the slave BY NAME without O_NOCTTY is
# what makes it one. The shell then execs the real command, so the pid, signals
# and exit status are the command's own.
_CLAIM_THE_TERMINAL = 'exec <"$1" >"$1" 2>&1; shift; exec "$@"'


def _terminal_argv(slave: int, command: list[str]) -> list[str]:
    """`command`, wrapped so it starts owning `slave` as its controlling terminal."""
    ttyname = getattr(os, "ttyname")  # noqa: B009 - POSIX-only attribute
    return ["sh", "-c", _CLAIM_THE_TERMINAL, "sh", str(ttyname(slave)), *command]


def _silence_terminal_echo(slave: int) -> None:
    """Stop the line discipline echoing back everything we type into the child.

    A fresh pty has ECHO on, so every answer `respond()` writes would be echoed
    onto the master, land in the output buffer, and be yielded straight into the
    log panel. sudo turns echo off around its own password read, so the measured
    "the password never appeared in the output" was sudo's doing, not ours —
    which is not a property to rely on for anything else the app types
    (review, 2026-08-22). Best effort: a pty that will not take the setting is
    not a reason to refuse to install.
    """
    try:
        # Fetched dynamically for the same reason `open_pty` is: the module does
        # not exist on Windows, and mypy checks this file for that platform too.
        termios = importlib.import_module("termios")
        attrs = termios.tcgetattr(slave)
        attrs[3] &= ~(termios.ECHO | termios.ECHONL)  # index 3 is lflag
        termios.tcsetattr(slave, termios.TCSANOW, attrs)
    except Exception as exc:  # noqa: BLE001 - never fail an install over echo
        logger.debug(f"could not turn off terminal echo: {exc}")


def interact(
    command: list[str],
    cwd: Path | None = None,
    *,
    respond: Responder,
    ask: Prompter | None = None,
    ask_marker: str | None = None,
    env: Mapping[str, str] | None = None,
    terminal: bool = False,
    quiet_seconds: float = _PROMPT_QUIET_SECONDS,
    cancel: threading.Event | None = None,
) -> Iterator[str]:
    """Run an interactive command, yielding its output live and answering its prompts.

    `cancel` is checked every loop turn: setting it interrupts even a child
    sitting on a prompt no rule answers, which otherwise blocks forever
    (review finding, 2026-08-21) — the child is then terminated as usual.

    Stdout and stderr are merged and read in chunks, so a prompt that does not
    end in a newline (`read -p`, `echo -n ...; read`) still surfaces: once a
    partial line has been quiet for `quiet_seconds`, `respond()` is asked about
    it. `respond()` also sees every complete line. Whenever it returns a
    string, that string plus a newline is written to the child's input. Lines
    are yielded raw (with colour codes); `respond()` receives them stripped.

    `ask` is the escape hatch for the one prompt no rule can answer: sudo's
    password. It is consulted ONLY when the pending text contains `ask_marker`,
    an exact string the caller arranged for the child to print. Without a
    marker `ask` is never called at all — a deliberate dead default, because the
    previous version guessed from the shape of a line and could not tell a
    password prompt from `[ 43%]`.

    `terminal=True` runs the child on a pseudo-terminal and makes that terminal
    its controlling tty. This is what the sudo case needs and a pipe cannot
    give: sudo reads from /dev/tty precisely so that a piped stdin cannot feed
    it a password. Measured — a child reading stdin answers through a pipe, the
    same child reading /dev/tty does not, and real sudo says "a terminal is
    required to read the password". POSIX only; ignored where `pty_supported()`
    is False, which is every Windows box and no installer host (the catalog's
    install scripts are Linux-only).

    Raises `subprocess.CalledProcessError` on non-zero exit, like `stream()`.
    If the generator is abandoned early the child is terminated (then killed)
    and its reader thread joined, exactly like `stream()`.
    """
    logger.debug(f"interact() called: command={command} cwd={cwd} terminal={terminal}")
    on_pty = terminal and pty_supported()
    master = slave = -1
    job: winjob.Job | None = None
    if on_pty:
        master, slave = open_pty()
    try:
        if on_pty:
            _silence_terminal_echo(slave)
            proc = subprocess.Popen(
                _terminal_argv(slave, command),
                cwd=_cwd_arg(cwd),
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=child_env(env),
                bufsize=0,
                start_new_session=True,  # see _CLAIM_THE_TERMINAL
            )
        else:
            proc, job = _spawn(
                lambda flags: subprocess.Popen(
                    command,
                    cwd=_cwd_arg(cwd),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=child_env(env),
                    bufsize=0,
                    creationflags=flags,
                )
            )
    except BaseException:
        for fd in (master, slave):
            if fd >= 0:
                os.close(fd)
        raise
    if on_pty:
        # The parent's copy would otherwise hold the pty open forever, so the
        # read below would never see EOF after the child exits.
        os.close(slave)
        slave = -1
        out_fd = master
    else:
        assert proc.stdout is not None
        out_fd = proc.stdout.fileno()
    chunks: queue.Queue[bytes | None] = queue.Queue()

    def _write(payload: bytes) -> bool:
        """Send bytes to the child, whichever transport it is on."""
        try:
            if on_pty:
                os.write(master, payload)
            else:
                assert proc.stdin is not None
                proc.stdin.write(payload)
                proc.stdin.flush()
        except (BrokenPipeError, OSError):
            return False
        return True

    def _pump() -> None:
        try:
            while True:
                data = os.read(out_fd, 4096)
                if not data:
                    break
                chunks.put(data)
        except OSError as exc:
            # On a pty the master raises EIO rather than returning b"" when the
            # last slave closes. That is this transport's EOF, not a failure —
            # and so is EBADF, which is what the `finally` closing `master` under
            # a live read looks like from here. Anything else means output was
            # lost, and a truncated log on a failed two-hour build is exactly
            # when the log matters, so say so (review, 2026-08-22).
            if exc.errno not in (errno.EIO, errno.EBADF):
                logger.warning(f"reading the child's output stopped early: {exc}")
        finally:
            chunks.put(None)

    reader = threading.Thread(target=_pump, daemon=True)
    try:
        reader.start()
    except BaseException:
        # The child is already running and `master` is open, and the try/finally
        # below has not been entered yet. `RuntimeError: can't start new thread`
        # is not far-fetched in a long-lived GUI process (review, 2026-08-22).
        proc.kill()
        proc.wait()
        if job is not None:
            job.close()  # kill-on-close: the root's descendants too (T299)
        if master >= 0:
            os.close(master)
        raise
    buffer = ""
    answered_partial = False

    def _the_prompt(text: str) -> str | None:
        """The marker and everything after it, or None if the marker is not there.

        Only the exact marker the caller arranged for — no guessing. The slice
        matters as much as the match: `ask()` used to receive the whole pending
        buffer, which on a terminal is routinely non-empty (progress output ends
        in `\\r`, and only `\\n` splits a line here). So the question put to the
        user was whatever the script last printed with the prompt stuck on the
        end — measured: `Checking directory /opt/azerothcore ... [marker]
        password:`, which `is_secret()` then classified as NOT a secret because
        it contains the word "directory", and the root password was typed into
        an unmasked field and written to the log (review, 2026-08-22).
        """
        if not ask_marker:
            return None
        clean = strip_ansi(text)
        at = clean.find(ask_marker)
        return None if at < 0 else clean[at:]

    def _answer(text: str, *, blocked: bool = False) -> bool:
        prompt = _the_prompt(text) if blocked else None
        if prompt is not None:
            # The marker proves the child is blocked on the ONE prompt we know
            # about. Everything printed before it is unrelated output that
            # merely shares the buffer, and a `respond()` built from unanchored
            # `search`es — as the bash engine's rule table was until 7.2 — got
            # the whole buffer, so its bare `(y/n)` catch-all answered sudo's
            # password read with "y". Measured: a child printing
            # `Reset the keyring? (y/n) \r` and
            # then the marker got `GOT:y` and `ask()` was never called, which is
            # the pre-6.1.5 symptom arriving by a new route. Slicing for `ask`
            # alone was half a fix (review, 2026-08-23).
            reply = respond(prompt)
            if reply is None and ask is not None:
                reply = ask(prompt)
        else:
            reply = respond(strip_ansi(text))
        if reply is None:
            return False
        return _write((reply + "\n").encode("utf-8"))

    def _complete_lines() -> Iterator[str]:
        """Yield every complete line in `buffer`, and let `respond()` see each one."""
        nonlocal buffer
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.rstrip("\r")
            yield line
            _answer(line)

    cancelled = False
    try:
        eof = False
        while not eof or buffer:
            if cancel is not None and cancel.is_set():
                cancelled = True
                break
            try:
                data = chunks.get(timeout=quiet_seconds)
            except queue.Empty:
                if proc.poll() is not None:
                    # The child is gone but the reader has not seen EOF. On a
                    # pty that is normal: the install scripts leave a
                    # `sudo -n true; sleep 60` keepalive whose orphaned `sleep`
                    # still holds the slave, so `os.read(master)` returns
                    # neither b"" nor EIO for up to a minute after the install
                    # finished — a minute of "installing" with no output and no
                    # way out (review, 2026-08-22).
                    #
                    # This used to require an EMPTY buffer, so a script whose
                    # last line had no trailing newline never took it — and on
                    # cancel those bytes were dropped, which was exactly the
                    # text the bash engine's `run()` built its failure message
                    # from. They are yielded below, after whatever the reader
                    # still had (review, 2026-08-23; T108).
                    logger.debug("child exited; ending the read rather than waiting for EOF")
                    break
                # A partial line that has gone quiet is the child waiting for
                # input — or just a slow build. Ask about it once.
                if not buffer or answered_partial:
                    continue
                if _answer(buffer, blocked=True) or _the_prompt(buffer) is not None:
                    # Shown either way. A prompt the user declined still has to
                    # reach the log, or the install appears to freeze with
                    # nothing on screen explaining why (review, 2026-08-22).
                    yield buffer
                    buffer = ""
                    answered_partial = False
                else:
                    answered_partial = True  # asked once; do not spam the same line
                continue
            if data is None:
                eof = True
                if buffer:
                    yield buffer
                    buffer = ""
                break
            buffer += data.decode("utf-8", errors="replace")
            answered_partial = False
            yield from _complete_lines()
        if not cancelled:
            # Bounded: an orphan holding the slave keeps the reader in os.read
            # long after the child is gone, and this join used to be unbounded.
            reader.join(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
            # The loop above can stop on "the child is gone" while the reader
            # still holds the child's last output: already read but not yet
            # queued, or still in the kernel buffer waiting for the reader
            # thread to get a CPU. The join has now let the reader queue it,
            # so read the queue once more. Until T108 nothing did, and on a
            # busy machine the tail of the output was lost whenever a quiet
            # tick fell between the child's exit and the reader's last put().
            # Measured: 1 run in 100 at loadavg ~5 lost `GOT:hunter2` in
            # test_prompt's /dev/tty test. The reader is given the join's bound
            # to deliver, not one quiet tick.
            #
            # The drain takes only what is queued when it starts (`qsize()`,
            # read once), or up to the end-of-stream sentinel if that comes
            # first. An orphan that keeps writing keeps the reader queueing,
            # and an unbounded drain would follow it forever.
            for _ in range(chunks.qsize()):
                try:
                    data = chunks.get_nowait()
                except queue.Empty:
                    break
                if data is None:
                    eof = True
                    break
                buffer += data.decode("utf-8", errors="replace")
            if not eof and not reader.is_alive():
                # The reader finished after the snapshot: its tail and its
                # sentinel may be queued now. A finished reader's queue is
                # finite, so it is drained to the sentinel without a bound.
                # The `qsize()` bound above is only for a reader still alive,
                # which an orphan can keep feeding forever.
                while True:
                    try:
                        data = chunks.get_nowait()
                    except queue.Empty:
                        break
                    if data is None:
                        eof = True
                        break
                    buffer += data.decode("utf-8", errors="replace")
            yield from _complete_lines()
            if buffer:
                yield buffer
                buffer = ""
            if not eof:
                # The end-of-stream sentinel was never consumed, by the loop
                # or by either drain. Only the sentinel proves the reader
                # handed over everything. The reader was alive at the check
                # above: an orphan holds the terminal, or it was starved for
                # the whole join. Whatever it queues from here is never read.
                # Closing that gap would take an unbounded wait, so it is said
                # instead, in the log and in the output (T108 review).
                logger.warning(
                    f"the reader of {command[0]} had not reached end of stream "
                    f"{_SHUTDOWN_TIMEOUT_SECONDS}s after the child exited; "
                    "its last output may be missing"
                )
                yield _OUTPUT_MAY_BE_CUT_OFF
            proc.wait()
            if proc.returncode:
                raise subprocess.CalledProcessError(proc.returncode, command)
    finally:
        # `stream()`'s ending, so a cancelled child's whole tree ends on Windows
        # too (T246); until then this was a copy of it that ended the root alone.
        # A cancel is this generator's Stop (Codex's third review; see `_finish`).
        _finish(proc, job, stopped=cancelled)
        reader.join(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
        for handle in (proc.stdin, proc.stdout):
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass
        for fd in (master, slave):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
