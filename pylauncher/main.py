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
from typing import TYPE_CHECKING, Any

from yulon import platform
from yulon.log import configure, file_log_problem, get_logger, use_utf8_streams

if TYPE_CHECKING:  # `yulon.state` pulls in pydantic; `--provision` must not pay for it.
    from yulon.state import AppState

logger = get_logger(__name__)

DEFAULT_WINDOW_SIZE = (1100, 750)
"""The size the window opens at, and the width every tab has to fit into.

A named constant rather than a literal at the `resize()` call because it is the
budget the catalog tiles are measured against: `test_catalog_view.py` asserts
that every Install button is inside the viewport at exactly this width, and a
test that carried its own copy of the number would keep passing if someone
shrank the window.
"""


_CATALOG_MIN_WIDTH = 420
"""Narrowest the catalog pane may become, in pixels.

Sized to the widest game tile plus its scrollbar, measured 2026-09-02: below
this the tile text clips mid-word and the Install button leaves the viewport.
It is a floor for the splitter, not a preference -- the pane is free to be
wider, and the user is free to drag it.
"""


def _warn_about_the_log_file(parent: Any) -> None:
    """Say once, on screen, that the log did not go where it was meant to.

    The frozen build is `console=False` (`build/pylauncher.spec`), so a warning
    on stderr reaches nobody at all: a dialog is the only channel that survives
    the packaging, and it carries the path because finding the log is the only
    reason anyone reads this. Silent when there is nothing to say, which is
    every normal start.
    """
    problem = file_log_problem()
    if problem is None:
        return
    from PySide6.QtWidgets import QMessageBox

    QMessageBox.warning(parent, "Yu'lon could not write its log", problem)


def _warn_unless_remembered(app_state: AppState, parent: Any) -> bool:
    """Write `state.json`, and say so out loud when it cannot be written.

    Same unwritable config dir as the log file, arriving in a Qt slot rather
    than at startup - so it degrades a running app instead of preventing one,
    and the uncaught `OSError` came out of a slot, where Qt turns it into a
    traceback nobody sees. Swallowing it is no better: the new tab stays on
    screen and the install is simply forgotten, which is indistinguishable from
    a save that worked until the next launch comes up without it. Returning the
    outcome keeps a caller from reporting one as the other.
    """
    from yulon.state import save_state

    try:
        save_state(app_state)
        return True
    except OSError as exc:
        logger.error(f"state.json could not be written: {exc}")
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.warning(
            parent,
            "Yu'lon cannot remember this server",
            "This server is set up and its tab works, but state.json could not be "
            f"written ({exc}), so Yu'lon will not reopen it after a restart.",
        )
        return False


def build_window() -> object:
    """Create the main window (imports Qt lazily so `--help`-style tooling stays cheap)."""
    from PySide6.QtCore import QObject, QThread, Signal, Slot
    from PySide6.QtWidgets import (
        QLabel,
        QMainWindow,
        QMessageBox,
        QSplitter,
        QTabWidget,
        QVBoxLayout,
        QWidget,
    )

    from yulon import __version__
    from yulon.catalog.catalog import load_catalog
    from yulon.install_wiring import installer_for_app
    from yulon.state import KnownInstall, load_state
    from yulon.ui.catalog_view import CatalogView
    from yulon.ui.controller_view import ControllerServices, ControllerView
    from yulon.ui.tab_titles import retitle_controller_tabs
    from yulon.ui.widgets.log_panel import LogPanel
    from yulon.update import UpdateCheck, check_for_update

    class _Window(QMainWindow):
        """The main window, which also carries the two registries the exit path walks.

        They are attributes and NOT `setProperty()` values, and that is
        load-bearing: Qt stores a property as a QVariant, and PySide converts a
        Python list into one by COPYING it. Measured on 6.11.2 - appending to
        the list after `setProperty()` leaves `property()` still answering with
        the snapshot taken at that call. Every tab opened after startup, which
        is every install and every adopt, was therefore invisible to
        `_stop_background_threads()`: a console left following the worldserver
        log on such a tab was never joined, and Qt was then torn down with that
        QThread still running - the 0xC0000409 abort that function exists to
        prevent. A QObject (`tabs`, the update thread) is stored by pointer and
        is unaffected, so those stay properties.
        """

        yulon_controllers: list[QWidget]
        yulon_log_panels: list[LogPanel]

    catalog = load_catalog()
    state = load_state()
    window = _Window()
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

    # The engine and its per-game import gate come from one place (7.1):
    # `install_wiring` is what the Server tab and the CLI harness use too. The
    # copy that stood here hand-wrote the same probe pair and the same
    # fixed-password fallback, and `catalog/` may not import a controller
    # package to do it itself (style-guide §3).
    catalog_view = CatalogView(catalog, installer_for_app, log_panel)
    splitter = QSplitter()
    splitter.addWidget(catalog_view)
    splitter.addWidget(log_panel)
    # The catalog is the thing the window is for; it may shrink, never vanish.
    # A bare QSplitter honours whatever minimum its children ask for, so one
    # widget with a wide size hint can squeeze the other to nothing -- which is
    # exactly what an unwrapped status label did on 2026-09-02, leaving the
    # tiles clipped mid-word and their buttons unreachable. That label now
    # wraps, which is the fix; this is the floor, so the next widget with a wide
    # hint cannot do it again. Stretch goes to the log because it is the pane
    # whose content grows.
    splitter.setCollapsible(0, False)
    splitter.setStretchFactor(0, 0)
    splitter.setStretchFactor(1, 1)
    catalog_view.setMinimumWidth(_CATALOG_MIN_WIDTH)
    tabs.addTab(splitter, "Catalog")

    # Typed as the concrete view, not QWidget: `drop_controller()` and the
    # distro comparison both reach into `services` and `console_log`.
    controllers: dict[tuple[str, Path], ControllerView] = {}
    controller_views: list[QWidget] = []

    def drop_controller(key: tuple[str, Path]) -> None:
        """Tear one live tab down completely, mirroring `_stop_background_threads()`.

        Same reason as that function: a `QThread` destroyed while running ABORTS
        the process (0xC0000409, verified), so the view's own `shutdown()` and
        its console panel's stop+join have to happen BEFORE the widget leaves
        the tab bar. The three registries are cleaned out with it, because a
        stale entry in any of them is what `_stop_background_threads()` would
        later call `shutdown()`/`wait()` on at exit.
        """
        view = controllers.pop(key)
        view.shutdown()
        panel = view.console_log
        panel.stop()
        panel.wait(5000)
        if view in controller_views:
            controller_views.remove(view)
        if panel in panels:
            panels.remove(panel)
        index = tabs.indexOf(view)
        if index != -1:
            tabs.removeTab(index)
        # `removeTab()` only unparents the page, it does not delete it. Without
        # this the discarded view stays alive for the life of the process, and
        # it is a whole ControllerView (six sub-tabs, a LogPanel, a QTimer).
        view.deleteLater()

    def add_controller(
        game: str,
        server_dir: Path,
        client_dir: Path | None,
        wsl_distro: str | None = None,
    ) -> None:
        """One tab per (game, server dir); a repeat (e.g. "Use existing…" twice) just focuses it.

        Unless the distro changed. Adopting a WSL-resident server that already
        had a tab used to write the distro to `state.json` and stop there, so
        every button on the open tab kept talking to the LOCAL docker daemon
        until the app was restarted - Start reported nothing up, Stop stopped
        nothing, and none of it said anything was wrong.

        Rebuilt rather than patched in place: `ControllerServices.for_wotlk()`
        bakes the distro into `DockerSql`, `DockerMysql` AND the `Controller`,
        and captures it again in the `logs_source` lambda. Setting
        `controller.wsl_distro` would fix one of those four and leave the
        others pointed at the wrong daemon - the exact half-updated shape this
        branch has already produced four times.
        """
        key = (game, server_dir)
        live = controllers.get(key)
        if live is not None:
            # `None` here means "this caller was not told a distro", NOT "this
            # server is local". `on_installed` never passes one, and "Use
            # existing…" accepts a `\\wsl.localhost\...` folder - the same
            # spelling an adopted server is stored under - so the keys collide.
            # Treating that as a change demoted a working WSL tab to the local
            # daemon: this fix's own failure mode, running backwards
            # (review, 2026-08-26).
            same = wsl_distro is None or live.services.controller.wsl_distro == wsl_distro
            if same:
                tabs.setCurrentWidget(live)
                return
            if (reason := live.busy_reason()) is not None:
                # A rebuild is a teardown, and `busy_reason()` exists because one
                # kind of work cannot survive being torn down: the import runs
                # 10-30 minutes inside a blocking `subprocess.run`, so
                # `shutdown()`'s join times out and the deferred delete then
                # destroys a still-running QThread - 0xC0000409, in a LIVE app
                # rather than at exit. The close guard already refuses for this
                # reason; so does this. The tab keeps addressing the old daemon
                # until it is reopened, which is stated rather than silent.
                QMessageBox.warning(
                    window,
                    "Cannot switch this server over yet",
                    f"{reason}\n\nThe WSL distro has been saved, and this tab will use it "
                    "the next time Yu'lon starts.",
                )
                tabs.setCurrentWidget(live)
                return
            drop_controller(key)
        entry = catalog.get(game)
        services = ControllerServices.for_wotlk(entry, server_dir, client_dir, wsl_distro)
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
        tabs.addTab(view, entry.name)
        # The leaf folder alone was the title, and it is the one part of the
        # path that repeats: the installer suggests the same name every time,
        # so two installs under different parents both read "WoW WotLK —
        # DadsMmoLab". The whole strip is re-titled and not just this tab,
        # because what tells them apart is the shortest tail they do NOT share
        # - a fact about the set, so opening the second tab is what makes the
        # first one's title wrong.
        retitle_controller_tabs(tabs, controllers.values())
        tabs.setCurrentWidget(view)

    for install in state.installs:
        try:
            add_controller(install.game, install.server_dir, install.client_dir, install.wsl_distro)
        except KeyError:
            logger.warning(f"state.json names unknown game {install.game!r}; skipping")

    def on_installed(game: str, server_dir: object, client_dir: object) -> None:
        sd = Path(str(server_dir))
        cd = Path(str(client_dir)) if client_dir is not None else None
        # Carried over, not defaulted away: `remember()` REPLACES the entry with
        # the same game + dir, and this signal carries no distro - so pointing
        # "Use existing…" at a `\\wsl.localhost\...` folder already known as a
        # WSL server erased the distro from `state.json`, and the next launch
        # built its tab against the local daemon. An install genuinely has no
        # distro to lose, so keeping the known one costs nothing
        # (review, 2026-08-26).
        known = state.find(game, sd)
        state.remember(
            KnownInstall(
                game=game,
                server_dir=sd,
                client_dir=cd,
                wsl_distro=known.wsl_distro if known else None,
            )
        )
        _warn_unless_remembered(state, window)
        add_controller(game, sd, cd, known.wsl_distro if known else None)

    def on_adopted(game: str, server_dir: object, client_dir: object, wsl_distro: object) -> None:
        """A server adopted from a WSL distro, which is remembered with it.

        The distro is recorded rather than re-derived on every start: a server
        inside a distro answers only to that distro's docker, and working it out
        again later would mean guessing from a path. See
        `pyplan/wsl-resident-servers.md`.
        """
        sd = Path(str(server_dir))
        cd = Path(str(client_dir)) if client_dir is not None else None
        distro = str(wsl_distro) if wsl_distro else None
        state.remember(KnownInstall(game=game, server_dir=sd, client_dir=cd, wsl_distro=distro))
        _warn_unless_remembered(state, window)
        add_controller(game, sd, cd, distro)

    catalog_view.installed.connect(on_installed)
    catalog_view.adopted.connect(on_adopted)

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
    # `setProperty` does NOT keep a Python object alive - a Qt property holds a
    # QObject*, not a reference - so `update_worker` used to die the moment this
    # function returned, and the thread started three lines down then called
    # `run` on freed memory whenever the OS got round to scheduling it. Native
    # backtrace on yulon-ubuntu (2026-08-28): `QThread::started` ->
    # `SignalManager::qt_metacall` -> SIGBUS in `QMetaMethod::name()`, at the
    # end of test_main.py where a window is dropped. `in_flight()` owns the pair
    # until the thread has finished; the properties stay for
    # `_stop_background_threads`, which joins by them.
    # Imported here, not at module scope: `main.py` keeps every Qt import inside
    # the functions that build a window, so `--provision` runs on a box with no
    # Qt at all. A module-level import of this contradicted the file's own
    # lazy-import comments (review, 2026-08-28).
    from yulon.ui.widgets.job import in_flight

    in_flight().hold(update_thread, update_worker)
    window.setProperty("update_thread", update_thread)
    window.setProperty("update_worker", update_worker)
    update_thread.start()
    window.resize(*DEFAULT_WINDOW_SIZE)
    window.setProperty("tabs", tabs)
    # The live lists themselves, not a copy of either - see `_Window`.
    window.yulon_log_panels = panels
    window.yulon_controllers = controller_views
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
    every shipped catalog entry is `platforms: ["linux"]`, so the engine's
    `preflight()` refuses before `ensure_docker()` is ever reached and the chain
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

    On Linux, 2 is the *expected* outcome rather than a fault: there is nobody
    here to ask about joining the docker group, and the app never makes a
    root-equivalent change with nobody asked, so the engine is installed and
    the one step only the user may take is printed for them to run.
    """
    # The same defect's other half: the human-readable lines below put that same
    # step text through `logging`, and a cp1252 stream cannot encode it either.
    # One home for the rule since 2026-09-03 -- `install_wiring` needed it too
    # and did not have it, which is what stopped the first Windows gate.
    use_utf8_streams()
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
        # What happened to the docker-group question. Headless has nobody to
        # ask, so on Linux this is always "not-asked" and the exact command is
        # in `manual_steps` — which makes exit 2 the expected outcome of a
        # clean Linux provision, not a failure. Support reads this line to tell
        # "the user declined root-equivalent access" apart from "it broke".
        "docker_group": str(report.docker_group),
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


def _regain_docker_group() -> None:
    """Restart under `sg docker` when that is all that stands between us and Docker.

    The SILENT half of the fix, and deliberately silent: it runs before the
    window exists and before any Docker question is asked, so the user never
    sees the process it replaces. There is nothing to click and nothing to
    explain -- the app simply opens able to use Docker.

    It covers the user who was joined to the group and then closed the launcher.
    The other half is `CatalogView._offer_a_restart_instead()`, which covers the
    user still sitting in the session the join happened in; that one has to ask,
    because it throws away a running application.

    Not one `os.getgroups()` call. Off Linux `docker_group_reexec()` returns
    None on a `sys.platform` check and spends nothing else. On Linux, before it
    can return None, it spends an env read, a `geteuid()`, a
    `shutil.which("sg")` PATH scan, `os.getgroups()`, and one `grp.getgrgid()`
    per gid -- and for a user who is NOT in the docker group, which is every
    Linux user who has not provisioned Docker, also a `pwd.getpwuid()` and a
    SUBPROCESS (`id -nG <user>`, 5s timeout). All of it runs as the first
    statement of `main()`, before QApplication exists, so a host where that
    subprocess is slow delays the window with nothing on screen yet to say why.
    """
    platform.restart_under_docker_group()


def main() -> int:
    """Start the launcher."""
    configure(config_dir=platform.config_dir())
    _regain_docker_group()
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
                    for view in getattr(watched, "yulon_controllers", [])
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
        # After show(), so the app is visibly UP before it admits to anything:
        # the log file failing is not a reason to hold the window back.
        _warn_about_the_log_file(window)
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
    # Read off the window as attributes: `build_window()`'s `_Window` records
    # why - a list put through `setProperty()` comes back as a copy frozen at
    # that call, and the tabs that matter here are the ones opened after it.
    for view in getattr(window, "yulon_controllers", []):
        view.shutdown()
    for panel in getattr(window, "yulon_log_panels", []):
        panel.stop()
        panel.wait(5000)
    thread = prop("update_thread")
    if isinstance(thread, QThread) and thread.isRunning():
        thread.quit()
        thread.wait(8000)
    # Whatever the panels and runners above did not own any more: `job.InFlight`
    # keeps every started pair alive until its thread has finished, so this is
    # the join for a worker whose panel is already gone.
    from yulon.ui.widgets.job import in_flight

    in_flight().wait_all(8000)


if __name__ == "__main__":
    raise SystemExit(main())
