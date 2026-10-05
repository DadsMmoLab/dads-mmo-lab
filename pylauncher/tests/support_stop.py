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
from yulon import docker
from yulon.docker import AttachedRun
from yulon.ui.widgets.log_panel import LogPanel


def stop_when(
    source: Callable[[threading.Event], Iterator[str]],
    reached: threading.Event,
    what: str,
    *,
    cancel: threading.Event | None = None,
) -> tuple[LogPanel, list[tuple[bool, str]]]:
    """Run `source(cancel)` in a panel, press Stop once `reached` is set, wait for the end.

    `cancel` is the job's Cancel; a rebuild-panel job's is a
    `docker.CancelWithForce`, carrying "Stop now anyway".
    """
    cancel = cancel if cancel is not None else threading.Event()
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
    """A `build` seam that compiles until the press's cancel is set, then ends as cancelled.

    `reached` is set once it is compiling. It answers `CANCELLED_RETURNCODE`,
    which is what the real `docker.run_attached()` hands back once it reads the
    cancel at the compiler's next line. It answered 143, a killed child's exit,
    until T250: that read as the Stop only while the panel decided by timing,
    and the build runs on the engine's own worker thread, where no Stop kills
    a child.
    """

    def build(
        server_dir: Path, files: object, *, sink: object = None, cancel: object = None
    ) -> AttachedRun:
        if callable(sink):
            sink("compiling")
        reached.set()
        assert isinstance(cancel, threading.Event), "the build was handed no cancel"
        cancel.wait(HANG_BOUND)
        return AttachedRun(docker.CANCELLED_RETURNCODE, ("compiling",))

    return build


def ready_wait_until_stop(reached: threading.Event) -> Callable[..., bool]:
    """A ready-wait seam that holds until the cancel IT IS HANDED is set, then says "not yet".

    `reached` is set once it is waiting. The cancel is `ReadySpec.cancel` (T247),
    so a press that does not hand the job's Stop to the wait fails at the
    `assert` here rather than hanging.
    """

    def wait(spec: object, ready: object) -> bool:
        cancel = getattr(ready, "cancel", None)
        assert cancel is not None, "the ready wait was handed no cancel, so Stop cannot end it"
        reached.set()
        assert cancel.wait(HANG_BOUND), "Stop never reached the ready wait"
        return False

    return wait
