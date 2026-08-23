"""Entry point for the Yu'lon launcher (PySide6).

Wires the pieces together and nothing more: logging into `config_dir()`,
the catalog tab (CatalogView over a shared LogPanel) and one ControllerView
tab per remembered install (`state.json`). New installs reported by the
catalog view are remembered and get a tab. Everything else lives in `yulon/`.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from yulon import platform
from yulon.log import configure, get_logger

logger = get_logger(__name__)


def build_window() -> object:
    """Create the main window (imports Qt lazily so `--help`-style tooling stays cheap)."""
    from PySide6.QtCore import QObject, QThread, Signal, Slot
    from PySide6.QtWidgets import QLabel, QMainWindow, QSplitter, QTabWidget, QVBoxLayout, QWidget

    from yulon import __version__
    from yulon.catalog.catalog import load_catalog
    from yulon.catalog.installer import Installer
    from yulon.state import KnownInstall, load_state, save_state
    from yulon.ui.catalog_view import CatalogView
    from yulon.ui.controller_view import ControllerServices, ControllerView
    from yulon.ui.widgets.log_panel import LogPanel
    from yulon.update import UpdateCheck, check_for_update

    catalog = load_catalog()
    state = load_state()
    window = QMainWindow()
    window.setWindowTitle(f"Yu'lon — Dad's MMO Lab launcher {__version__}")
    tabs = QTabWidget(window)
    central = QWidget(window)
    column = QVBoxLayout(central)
    banner = QLabel(central)
    banner.setOpenExternalLinks(True)
    banner.setVisible(False)
    column.addWidget(banner)
    column.addWidget(tabs, 1)
    window.setCentralWidget(central)

    log_panel = LogPanel()
    panels: list[LogPanel] = [log_panel]
    catalog_view = CatalogView(catalog, lambda entry: Installer(entry), log_panel)
    splitter = QSplitter()
    splitter.addWidget(catalog_view)
    splitter.addWidget(log_panel)
    tabs.addTab(splitter, "Catalog")

    controllers: dict[tuple[str, Path], QWidget] = {}
    controller_views: list[QWidget] = []

    def add_controller(game: str, server_dir: Path, client_dir: Path | None) -> None:
        """One tab per (game, server dir); a repeat (e.g. "Use existing…" twice) just focuses it."""
        key = (game, server_dir)
        if key in controllers:
            tabs.setCurrentWidget(controllers[key])
            return
        entry = catalog.get(game)
        services = ControllerServices.for_wotlk(entry, server_dir, client_dir)
        view = ControllerView(entry, services)
        # Every failure this view reports also lands in the app log. Each one is
        # already shown on its own tab, but the log is what a user pastes into a
        # bug report, and until now none of them reached it (review, 2026-08-22).
        view.action_failed.connect(
            lambda message, name=entry.name: logger.warning(f"{name}: {message}")
        )
        controllers[key] = view
        controller_views.append(view)
        panels.append(view.console_log)
        tabs.addTab(view, f"{entry.name} — {server_dir.name}")
        tabs.setCurrentWidget(view)

    for install in state.installs:
        try:
            add_controller(install.game, install.server_dir, install.client_dir)
        except KeyError:
            logger.warning(f"state.json names unknown game {install.game!r}; skipping")

    def on_installed(game: str, server_dir: object, client_dir: object) -> None:
        sd = Path(str(server_dir))
        cd = Path(str(client_dir)) if client_dir is not None else None
        state.remember(KnownInstall(game=game, server_dir=sd, client_dir=cd))
        save_state(state)
        add_controller(game, sd, cd)

    catalog_view.installed.connect(on_installed)

    # README §10: non-blocking update check on a background thread; banner only if newer.
    class _UpdateWorker(QObject):
        done = Signal(object)

        def run(self) -> None:
            self.done.emit(check_for_update())

    class _BannerHost(QObject):
        """Owns the banner slot ON THE GUI THREAD so the worker's signal is queued.

        A plain function has no thread affinity: connected to a worker-thread
        signal it runs on the WORKER (verified on PySide6 6.11.2 — an explicit
        QueuedConnection does not change that), touching `banner` off the GUI
        thread. Only a QObject-bound slot is delivered on this thread
        (review finding, 2026-08-21).
        """

        @Slot(object)
        def show_update(self, result: object) -> None:
            if isinstance(result, UpdateCheck) and result.available:
                banner.setText(
                    f"A newer Yu'lon ({result.latest}) is available — "
                    f'<a href="{result.url}">download it</a> (you have {result.current}).'
                )
                banner.setVisible(True)

    banner_host = _BannerHost(window)

    update_thread = QThread(window)
    update_worker = _UpdateWorker()
    update_worker.moveToThread(update_thread)
    update_thread.started.connect(update_worker.run)
    update_worker.done.connect(banner_host.show_update)
    update_worker.done.connect(update_thread.quit)
    window.setProperty("update_thread", update_thread)  # keep references alive with the window
    window.setProperty("update_worker", update_worker)
    update_thread.start()
    window.resize(1100, 750)
    window.setProperty("tabs", tabs)
    window.setProperty("log_panels", panels)
    window.setProperty("controllers", controller_views)
    assert isinstance(window, QWidget)
    return window


PROVISION_READY = 0
PROVISION_MANUAL = 2
PROVISION_REBOOT = 3


def provision_headless() -> int:
    """Run the Docker provisioning chain with no GUI and report machine-readably.

    Two audiences, and neither of them can drive a window.

    Support: "run Yu'lon with --provision and send me the JSON" answers, in one
    step, every question about why Docker will not come up on a user's machine —
    which of the steps ran, which were skipped and why, and what is left that
    only they can do.

    The clean-box harness (checklist 6.3's "proven on a clean box"): on Windows
    every shipped catalog entry is `platforms: ["linux"]`, so `Installer.preflight()`
    refuses before `ensure_docker()` is ever reached and the provisioning chain
    cannot be exercised through the app at all. That chain is nonetheless where
    the four Cross-cutting Windows defects live — download over a verified
    connection, silent install, find the executable that was just installed,
    start it, poll for ready, then build an argv from a CLI this process's own
    PATH never contained. This entry point is how those get exercised on a real
    clean box before 6.2/6.3 make them reachable the ordinary way.

    Exit codes are the harness's control flow, not decoration:
      0  ready — a daemon answers and nothing is outstanding
      3  a reboot is required first (`wsl --install` forces one on a box with no
         WSL), so nothing after it can be judged yet; reboot and run again
      2  not ready, and what remains needs a human
    """
    # The same defect's other half: the human-readable lines below put that same
    # step text through `logging`, and a cp1252 stream cannot encode it either.
    # `errors="replace"` rather than letting it raise, because a diagnostic that
    # kills the thing it is diagnosing is worse than one with a "?" in it.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):  # a stream that cannot be re-wrapped
                pass
    logger.info("Yu'lon provisioning (headless)")
    report = platform.ensure_docker()
    payload = {
        "platform": str(report.platform),
        "done": list(report.done),
        "skipped": list(report.skipped),
        "manual_steps": list(report.manual_steps),
        "reboot_required": report.reboot_required,
        "docker_ready": report.docker_ready,
        "ok": report.ok,
        # Which docker CLI this process resolved, or null. On a clean Windows box
        # this is the single most useful line in the report: it is the difference
        # between "the installer ran" and "the process that ran it can now use
        # what it installed", which is Cross-cutting defect 3 and is invisible
        # from anywhere else.
        "docker_cli": platform.docker_program(),
    }
    # Written to stdout as one line so a harness can parse it without caring
    # about the human-readable logging that shares this stream.
    #
    # `ensure_ascii` is left at its default, and that is the whole point: this
    # ran as `yulon.exe --provision > log 2>&1` on a clean Windows 11 box and
    # died here with UnicodeEncodeError, because a redirected Windows stdout is
    # cp1252 and platform's own step text contains an arrow ("downloaded the
    # installer -> C:\..."). The run had already spent a 659 MB download by
    # then. JSON escapes non-ASCII as \uXXXX, so an ASCII-safe line is not a
    # lossy one -- json.loads returns the identical object (clean-box run,
    # 2026-08-23).
    print("YULON_PROVISION_JSON " + json.dumps(payload))
    for step in report.done:
        logger.info("did: %s", step)
    for step in report.skipped:
        logger.warning("skipped: %s", step)
    for step in report.manual_steps:
        logger.warning("you must: %s", step)
    if report.reboot_required:
        return PROVISION_REBOOT
    return PROVISION_READY if report.ok else PROVISION_MANUAL


def main() -> int:
    """Start the launcher."""
    configure(config_dir=platform.config_dir())
    if "--provision" in sys.argv[1:] or os.environ.get("YULON_PROVISION"):
        return provision_headless()
    logger.info("Yu'lon launcher starting")
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox

    app = QApplication(sys.argv)
    window = build_window()
    assert isinstance(window, QMainWindow)
    try:
        if os.environ.get("YULON_SMOKE_TEST"):
            # CI / packaging check: prove the frozen app can build its window, then leave.
            logger.info("YULON_SMOKE_TEST set: window built, exiting 0")
            return 0

        # Defined here rather than at module scope because this module imports
        # PySide6 lazily — the class body names QObject, so a module-level
        # definition would need Qt at import time.
        class _RefuseCloseWhileBusy(QObject):
            """Decline to close the window while something is running that cannot be stopped.

            There is exactly one such thing today: the database import, which runs for
            10-30 minutes. Closing during one used to freeze the window for
            `STOP_GRACE_SECONDS + 30` seconds — `ControllerView.shutdown()` joins its
            worker, `_JobWorker.run()` calls its work synchronously so `thread.quit()`
            cannot preempt a blocking `subprocess.run` — and then abort the process
            exactly as `_stop_background_threads()` below describes, because the join
            expires while the thread is still running.

            Refusing is the honest answer rather than a restriction. The import cannot
            be stopped part-way without leaving the databases half-written, so the only
            choice that ever existed was between waiting and a crash; this makes that
            choice visible and takes the crash off the table (review, 2026-08-23).
            """

            def eventFilter(self, watched: QObject, event: QEvent) -> bool:
                if event.type() is not QEvent.Type.Close:
                    return False
                reasons = [
                    reason
                    for view in (watched.property("controllers") or [])
                    if (reason := getattr(view, "busy_reason", lambda: None)())
                ]
                if not reasons:
                    return False
                logger.info(f"close refused: {reasons[0]}")
                QMessageBox.information(None, "Yu'lon is still working", reasons[0])
                event.ignore()
                return True

        guard = _RefuseCloseWhileBusy(window)
        window.installEventFilter(guard)
        window.show()
        return int(app.exec())
    finally:
        _stop_background_threads(window)


def _stop_background_threads(window: object) -> None:
    """Stop every live worker before the interpreter tears Qt down.

    A `QThread` destroyed while running does not warn — it ABORTS the process
    (0xC0000409, verified): closing the window mid-install or while following
    a log must first stop and join those panels (review finding, 2026-08-21).

    This runs from `main()`'s `finally`, AFTER `app.exec()` has returned, so
    nothing is pumping the main thread's event queue while `panel.wait()`
    blocks in it. That is not incidental — it is why the join has to be able to
    complete without one, and for a while it could not: a panel's worker
    reached its thread only through `worker.finished -> thread.quit`, a queued
    connection into this very thread. Measured: `wait(3000)` returned False
    with the worker long finished, and Qt was then torn down with the QThread
    still running — the abort above, arriving through the function meant to
    prevent it. `LogPanel`'s worker now ends its own thread's loop directly
    (see `_StreamWorker.run()`), and `ThreadedJobRunner.wait()` quits each
    thread before waiting on it. Nothing here may go back to relying on a
    queued quit (review, 2026-08-23).

    What a close mid-install does NOT do, and never did, is register the
    install: `_on_finished` is queued into this same blocked thread, so
    `run_finished` never fires, `CatalogView._on_run_finished()` never runs and
    nothing is written to `state.json` on this path.
    """
    from PySide6.QtCore import QThread

    prop = getattr(window, "property", lambda _name: None)
    for view in prop("controllers") or []:
        view.shutdown()
    for panel in prop("log_panels") or []:
        panel.stop()
        panel.wait(5000)
    thread = prop("update_thread")
    if isinstance(thread, QThread) and thread.isRunning():
        thread.quit()
        thread.wait(8000)


if __name__ == "__main__":
    raise SystemExit(main())
