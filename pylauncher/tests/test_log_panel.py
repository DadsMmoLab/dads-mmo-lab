"""Tests for `LogPanel` (roadmap 4.1): a job streams in without blocking, and finishes cleanly."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

from tests.conftest import process_events
from yulon.ui.widgets.log_panel import LogPanel


def _wait_for(panel: LogPanel, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while panel.running and time.monotonic() < deadline:
        process_events(20)
    panel.wait(1000)
    process_events(50)


def test_lines_stream_in_on_a_background_thread_and_finish(qapp: object) -> None:
    panel = LogPanel()
    ui_thread = threading.get_ident()
    seen_threads: set[int] = set()
    finished: list[tuple[bool, str]] = []
    panel.run_finished.connect(lambda ok, msg: finished.append((ok, msg)))

    def source() -> Iterator[str]:
        seen_threads.add(threading.get_ident())
        for i in range(3):
            yield f"line {i}"
            time.sleep(0.01)

    assert panel.run(source, title="job") is True
    assert panel.run(source) is False  # busy
    _wait_for(panel)
    assert panel.text().splitlines() == ["line 0", "line 1", "line 2"]
    assert seen_threads and ui_thread not in seen_threads
    assert finished == [(True, "done")]
    assert panel.running is False


def test_job_exception_becomes_a_failed_status_not_a_crash(qapp: object) -> None:
    panel = LogPanel()
    finished: list[tuple[bool, str]] = []
    panel.run_finished.connect(lambda ok, msg: finished.append((ok, msg)))

    def source() -> Iterator[str]:
        yield "before"
        raise RuntimeError("boom")

    panel.run(source)
    _wait_for(panel)
    assert panel.text().splitlines() == ["before"]
    assert finished == [(False, "RuntimeError: boom")]


def test_stop_ends_an_endless_job(qapp: object) -> None:
    panel = LogPanel()
    finished: list[tuple[bool, str]] = []
    panel.run_finished.connect(lambda ok, msg: finished.append((ok, msg)))

    def endless() -> Iterator[str]:
        n = 0
        while True:
            yield f"tick {n}"
            n += 1
            time.sleep(0.005)

    panel.run(endless)
    process_events(100)
    panel.stop()
    _wait_for(panel)
    assert finished and finished[0] == (True, "stopped")
    assert "tick 0" in panel.text()


def _cancellable(release: threading.Event) -> Iterator[str]:
    """A source shaped like `runner.interact()` under cancel: it RETURNS, never raises."""
    yield "cloning"
    release.wait(5.0)


def test_a_stopped_job_says_cancelled_and_not_finished(qapp: object) -> None:
    """A cancelled source reports success, so only the panel can tell the difference.

    `runner.interact()` returns when its cancel event is set rather than
    raising, so the worker ends its loop normally and reports `ok=True` with
    the same "done" any completed job produces. Measured through the Catalog's
    Install button against the real install script: Stop during the source
    clone left the header reading "finished: stopped" (install gate,
    2026-08-23).
    """
    panel = LogPanel()
    finished: list[tuple[bool, str]] = []
    panel.run_finished.connect(lambda ok, msg: finished.append((ok, msg)))
    release = threading.Event()

    assert panel.cancelled is False
    panel.run(lambda: _cancellable(release), title="Installing")
    process_events(50)
    panel.stop()
    release.set()
    _wait_for(panel)

    assert finished and finished[0][0] is True  # the job itself claims success
    assert panel.cancelled is True
    assert panel.status_text() == "cancelled"


def test_the_next_job_is_not_still_cancelled_from_the_last_one(qapp: object) -> None:
    """`run()` resets the flag, or every job after a Stop would be called cancelled."""
    panel = LogPanel()
    release = threading.Event()
    panel.run(lambda: _cancellable(release))
    process_events(50)
    panel.stop()
    release.set()
    _wait_for(panel)
    assert panel.cancelled is True

    panel.run(lambda: iter(["second job"]))
    _wait_for(panel)
    assert panel.cancelled is False
    assert panel.status_text() == "finished: done"
