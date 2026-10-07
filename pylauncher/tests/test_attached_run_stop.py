"""T526: a Stop reaches an attached docker run whose output has gone quiet.

The install engine runs its build, import and download one-shots through
`native._pump()`, which calls `docker.run_attached()` on its own worker thread
(`yulon-install-output`). The panel's Stop ends only what ITS thread started, so
it reaches that run through the cancel token alone, and `run_attached()` used to
read the token only when a line arrived. Measured 2026-10-07
(`.notes/gates/probe-t529-t526-2026-10-07/laptop-t526/`): a fake build step silent
for 60 s, Stop at 3 s, the run ended 57.01 s later -- the rest of the silence.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

from tests.conftest import HANG_BOUND
from yulon import docker, runner

# A docker CLI stand-in: one line, then nothing for ten minutes.
_ONE_LINE_THEN_SILENCE = (
    "import time; print('#12 RUN cmake --build .', flush=True); time.sleep(600)"
)


def _silent_build(
    monkeypatch: pytest.MonkeyPatch, cancel: threading.Event, tmp_path: Path
) -> tuple[threading.Thread, list[docker.AttachedRun]]:
    """Start `run_attached()` on a worker thread against a quiet child; wait for its line."""
    monkeypatch.setattr(
        docker.platform,
        "docker_prefix",
        lambda *a, **k: (sys.executable, "-c", _ONE_LINE_THEN_SILENCE),
    )
    said = threading.Event()
    result: list[docker.AttachedRun] = []

    def work() -> None:
        result.append(
            docker.run_attached(
                [], tmp_path, sink=lambda line: said.set(), cancel=cancel, merge_stderr=True
            )
        )

    worker = threading.Thread(target=work, daemon=True, name="test-t526-install-output")
    worker.start()
    assert said.wait(HANG_BOUND), "the child never printed its line"
    return worker, result


def test_a_stop_ends_a_silent_attached_run_and_reads_as_the_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The cancel ends the docker CLI at once, and the run comes back as CANCELLED_RETURNCODE.

    The return code is half the test: callers decide on `== CANCELLED_RETURNCODE`
    (the build's "may have tagged", the import, botdash, extraction), so a Stop
    that ended the CLI must not come back as the CLI's own exit (-15 or 143).

    Mutations this catches: no watcher on the cancel (the worker is still
    blocked at `HANG_BOUND`, the child sleeps 600 s), and the watcher without the
    mapping (`returncode` is -15).
    """
    cancel = threading.Event()
    worker, result = _silent_build(monkeypatch, cancel, tmp_path)
    try:
        cancel.set()
        worker.join(timeout=HANG_BOUND)
        assert not worker.is_alive(), "the Stop waited for the silent child's next line"
        assert result[0].returncode == docker.CANCELLED_RETURNCODE, result
        assert result[0].tail == ("#12 RUN cmake --build .",)
    finally:
        if worker.is_alive():
            runner.end_streams_started_on(worker.ident or 0)
        worker.join(timeout=HANG_BOUND)


def test_without_a_cancel_a_run_ended_from_its_own_thread_keeps_its_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The mapping is for a Stop the cancel carried, and only that.

    A child some other route ended, with no cancel set, keeps its exit status:
    `run_attached()` returns it and its caller judges it, as before T526.
    """
    cancel = threading.Event()
    worker, result = _silent_build(monkeypatch, cancel, tmp_path)
    try:
        assert runner.end_streams_started_on(worker.ident or 0) == 1
        worker.join(timeout=HANG_BOUND)
        assert not worker.is_alive()
        assert result[0].returncode not in (0, docker.CANCELLED_RETURNCODE), result
    finally:
        worker.join(timeout=HANG_BOUND)
