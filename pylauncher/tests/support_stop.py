"""Press Stop on a real `LogPanel` once a job has reached a chosen point (T228).

Test support. A job that ends after Stop is judged by the panel, and only by
the panel: whether it says "cancelled" or "Stopped. FAILED: ..." is decided in
`_StreamWorker.run()` and `LogPanel._on_finished()`. So the tests that check
what a stopped press shows run the press's real generator in a real panel and
press the panel's own Stop, never a hand-set flag.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from pathlib import Path

from tests.conftest import HANG_BOUND, process_events, pump_until, wait_for_panel
from yulon.docker import AttachedRun
from yulon.ui.widgets.log_panel import LogPanel


def stop_when(
    source: Callable[[threading.Event], Iterator[str]],
    reached: threading.Event,
    what: str,
) -> tuple[LogPanel, list[tuple[bool, str]]]:
    """Run `source(cancel)` in a panel, press Stop once `reached` is set, wait for the end."""
    cancel = threading.Event()
    panel = LogPanel()
    finished: list[tuple[bool, str]] = []
    panel.run_finished.connect(lambda ok, message: finished.append((ok, message)))
    panel.run(lambda: source(cancel), title="Stopping", cancel=cancel)
    pump_until(reached.is_set, what)
    panel.stop()
    wait_for_panel(panel)
    process_events()
    return panel, finished


def compile_until_stopped(reached: threading.Event) -> Callable[..., AttachedRun]:
    """A `build` seam that compiles until the press's cancel is set, then exits as killed.

    `reached` is set once it is compiling. 143 is what the live box's killed
    compile exited with (the 7.10 probe's own log).
    """

    def build(
        server_dir: Path, files: object, *, sink: object = None, cancel: object = None
    ) -> AttachedRun:
        if callable(sink):
            sink("compiling")
        reached.set()
        assert isinstance(cancel, threading.Event), "the build was handed no cancel"
        cancel.wait(HANG_BOUND)
        return AttachedRun(143, ("compiling", "terminated"))

    return build
